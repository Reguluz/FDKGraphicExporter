"""Regression tests for exact source cache tracking and limited legacy adoption."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tool'))
import cache_sources as cache


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.tmp.name)
        self.game, self.output = self.root / 'game', self.root / 'output'
        self.game.mkdir(); self.output.mkdir()
        self.profile = {'id': 'sky2', 'adapter': 'falcom_pac', 'game': str(self.game),
                        'output': str(self.output), 'workers': 2}
        self.rows, self.refs = [], []
        for ident in ('m1', 'm2'):
            source = ident + '.mdl'; payload = ('raw-' + ident).encode()
            (self.game / source).write_bytes(payload)
            self.rows.append({'id': ident, 'source': source, 'sha256': hashlib.sha256(payload).hexdigest(), 'status': 'ok'})
            texture = 't' + ident[-1]
            (self.game / (texture + '.dds')).write_bytes(('raw-' + texture).encode())
            self.refs.append({'model': ident, 'texture': texture, 'source': texture + '.dds',
                              'material': 'body', 'material_index': 0, 'slot': 0, 'semantic': 'DIFFUSEMAP0'})
            write_json(self.output / 'gltf' / (ident + '.gltf'), {'buffers': [{'uri': ident + '.bin', 'byteLength': 4}]})
            (self.output / 'gltf' / (ident + '.bin')).write_bytes(b'abcd')
            write_json(self.output / 'gltf' / (ident + '.metadata'), {})
            write_json(self.output / 'materials' / (ident + '.json'), [])
            (self.output / 'textures').mkdir(exist_ok=True)
            Image.new('RGBA', (2, 2), (10, 20, 30, 255)).save(self.output / 'textures' / (texture + '.png'))
        self.slots = {'chr_cloth': {'0': 'DIFFUSEMAP0'}}
        self.reads = []

    def tearDown(self): self.tmp.cleanup()

    def read(self, game, source):
        self.reads.append(source)
        return (Path(game) / source).read_bytes()

    def plan(self, rows=None, refs=None, profile=None, slots=None):
        return cache.plan(profile or self.profile, self.rows if rows is None else rows,
                          self.refs if refs is None else refs, self.read, self.slots if slots is None else slots)

    def seed(self):
        planned = self.plan()
        cache.commit(self.profile, planned, ['m1', 'm2'], ['t1', 't2'])

    def test_new_unknown_cache_then_precise_reuse(self):
        first = self.plan()
        self.assertTrue(all(r['rebuild'] for r in first['models'].values()))
        self.assertTrue(all(r['rebuild'] for r in first['textures'].values()))
        self.seed()
        self.reads.clear()
        second = self.plan(rows=[self.rows[0]])
        self.assertFalse(second['models']['m1']['rebuild'])
        self.assertFalse(second['textures']['t1']['rebuild'])
        self.assertEqual(set(self.reads), {'m1.mdl', 't1.dds'})

    def test_texture_content_and_same_name_source_change(self):
        self.seed()
        old_mtime = (self.game / 't1.dds').stat().st_mtime_ns
        (self.game / 't1.dds').write_bytes(b'new-pixels')
        os.utime(self.game / 't1.dds', ns=(old_mtime, old_mtime))
        changed = self.plan()
        self.assertTrue(changed['textures']['t1']['rebuild'])
        self.assertFalse(changed['textures']['t2']['rebuild'])
        # Equal bytes from another source still produce a distinct dependency.
        (self.game / 'another.dds').write_bytes(b'raw-t2')
        refs = copy.deepcopy(self.refs); refs[1]['source'] = 'another.dds'
        changed = self.plan(refs=refs)
        self.assertTrue(changed['textures']['t2']['rebuild'])
        self.assertTrue(changed['models']['m2']['rebuild'])

    def test_model_changed_requires_scan_then_rebuild(self):
        self.seed()
        payload = b'changed-mdl'
        (self.game / 'm1.mdl').write_bytes(payload)
        with self.assertRaisesRegex(ValueError, '先重新扫描'): self.plan()
        self.rows[0]['sha256'] = hashlib.sha256(payload).hexdigest()
        planned = self.plan()
        self.assertTrue(planned['models']['m1']['rebuild'])
        self.assertFalse(planned['models']['m2']['rebuild'])

    def test_successful_partial_commit_preserves_others(self):
        self.seed()
        before = json.loads(cache._manifest_path(self.profile).read_text())
        payload = b'changed-mdl'; (self.game / 'm1.mdl').write_bytes(payload)
        self.rows[0]['sha256'] = hashlib.sha256(payload).hexdigest()
        planned = self.plan(rows=[self.rows[0]])
        cache.commit(self.profile, planned, ['m1'], [])
        after = json.loads(cache._manifest_path(self.profile).read_text())
        self.assertEqual(after['models']['m2'], before['models']['m2'])
        self.assertEqual(after['textures'], before['textures'])
        self.assertNotEqual(after['models']['m1']['fingerprint'], before['models']['m1']['fingerprint'])

    def test_missing_reference_and_shader_changes(self):
        self.seed()
        refs = copy.deepcopy(self.refs); refs[0]['source'] = ''
        planned = self.plan(refs=refs)
        self.assertTrue(planned['models']['m1']['rebuild'])
        self.assertNotIn('t1', planned['textures'])
        self.assertTrue((self.output / 'textures' / 't1.png').is_file())
        planned = self.plan(slots={'chr_cloth': {'0': 'UNKNOWN'}})
        self.assertTrue(all(r['rebuild'] for r in planned['models'].values()))

    def test_cross_game_and_incomplete_cache(self):
        self.seed()
        profile = dict(self.profile, id='different_game')
        planned = self.plan(profile=profile)
        self.assertTrue(all(r['rebuild'] for r in planned['models'].values()))
        self.assertTrue(all(r['rebuild'] for r in planned['textures'].values()))
        (self.output / 'gltf' / 'm1.metadata').unlink()
        (self.output / 'textures' / 't2.png').write_bytes(b'broken')
        planned = self.plan()
        self.assertTrue(planned['models']['m1']['rebuild'])
        self.assertTrue(planned['textures']['t2']['rebuild'])

    def legacy(self):
        self.profile.update(id='kuro2', adapter='loose_cle')
        write_json(self.output / 'reports' / 'inventory_summary.json', {'game': str(self.game)})
        write_json(self.output / 'reports' / 'inventory.json', self.rows)
        timestamp = min((self.output / 'gltf' / (r['id'] + '.gltf')).stat().st_mtime_ns for r in self.rows) - 1000000000
        os.utime(self.output / 'reports' / 'inventory.json', ns=(timestamp, timestamp))
        jobs = []
        for row in self.rows:
            signatures = {}
            for suffix in ('.gltf', '.bin', '.metadata'):
                path = self.output / 'gltf' / (row['id'] + suffix)
                signatures[str(path.resolve())] = {'bytes': path.stat().st_size, 'mtime_ns': path.stat().st_mtime_ns}
            jobs.append({'id': row['id'], 'status': 'ok', 'verification': {'passed': True}, 'source_signature': signatures})
        write_json(self.output / 'reports' / 'batch_summary.json', {'jobs': jobs})
        write_json(self.output / 'reports' / 'all_textures.json', [
            {'texture': r['texture'], 'source': r['source'], 'status': 'ok',
             'png': str(self.output / 'textures' / (r['texture'] + '.png'))} for r in self.refs])

    def test_verified_kuro_legacy_adopts_incrementally(self):
        self.legacy()
        first = self.plan(rows=[self.rows[0]])
        self.assertTrue(first['models']['m1']['legacy_adopt'])
        self.assertTrue(first['textures']['t1']['legacy_adopt'])
        self.assertFalse(first['models']['m1']['rebuild'])
        cache.commit(self.profile, first, ['m1'], ['t1'])
        second = self.plan(rows=[self.rows[1]])
        self.assertTrue(second['models']['m2']['legacy_adopt'])
        self.assertTrue(second['textures']['t2']['legacy_adopt'])

    def test_scan_only_cannot_adopt_stale_legacy(self):
        self.legacy()
        # New scan provenance prevents treating updated inventory as old evidence.
        write_json(self.output / 'reports' / 'inventory_summary.json', {'game': str(self.game), 'profile_id': 'kuro2'})
        planned = self.plan()
        self.assertTrue(all(r['rebuild'] for r in planned['models'].values()))
        self.assertFalse(any(r['legacy_adopt'] for r in planned['models'].values()))


if __name__ == '__main__': unittest.main(verbosity=2)
