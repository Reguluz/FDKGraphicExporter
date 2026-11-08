"""Exact source fingerprints for shared FDK intermediate caches.

Planning is read-only. Commit records only successful work, after conversion.
The caller must serialize prepare/commit per output directory (the GUI does).
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re

SCHEMA_VERSION = 1


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _json_hash(value):
    return _hash(json.dumps(value, sort_keys=True, ensure_ascii=False,
                            separators=(',', ':')).encode('utf-8'))


def _manifest_path(profile):
    return Path(profile['output']).resolve() / 'reports' / 'prepared_sources.json'


def _read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _manifest(profile):
    path = _manifest_path(profile)
    if not path.is_file():
        return {'schema_version': SCHEMA_VERSION, 'models': {}, 'textures': {}}
    document = _read_json(path)
    if (document.get('schema_version', SCHEMA_VERSION) != SCHEMA_VERSION
            or not isinstance(document.get('models'), dict)
            or not isinstance(document.get('textures'), dict)):
        raise ValueError('prepared_sources.json 版本或结构无效，请先备份该记录再重建资源。')
    return document


def _complete_model(output, ident):
    try:
        target = output / 'gltf' / (ident + '.gltf')
        data = _read_json(target)
        if not data.get('buffers'):
            return False
        for buffer in data['buffers']:
            if buffer['uri'] != ident + '.bin' or target.with_suffix('.bin').stat().st_size < buffer['byteLength']:
                return False
        _read_json(target.with_suffix('.metadata'))
        _read_json(output / 'materials' / (ident + '.json'))
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _complete_texture(path):
    try:
        from PIL import Image
        with Image.open(path) as image:
            if image.format != 'PNG': return False
            image.verify()
        return True
    except (OSError, ValueError, SyntaxError):
        return False


def _legacy_evidence(profile):
    """Only the original verified loose Kuro II export can be adopted once."""
    if profile['id'] != 'kuro2' or profile.get('adapter') != 'loose_cle':
        return None
    output = Path(profile['output']).resolve()
    try:
        summary = _read_json(output / 'reports' / 'inventory_summary.json')
        # A scan made by the new tool must not certify an older untracked cache.
        if summary.get('profile_id') or Path(summary['game']).resolve() != Path(profile['game']).resolve():
            return None
        inventory = output / 'reports' / 'inventory.json'
        rows = {r['id']: r for r in _read_json(inventory) if r.get('status') == 'ok'}
        jobs = {r['id']: r for r in _read_json(output / 'reports' / 'batch_summary.json')['jobs']
                if r.get('status') == 'ok' and r.get('verification', {}).get('passed')}
        textures = {r['texture']: r for r in _read_json(output / 'reports' / 'all_textures.json')
                    if r.get('status') == 'ok'}
        return dict(rows=rows, jobs=jobs, textures=textures, inventory_mtime=inventory.stat().st_mtime_ns)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _legacy_model(evidence, output, row, source_sha):
    if not evidence: return False
    try:
        ident = row['id']; old = evidence['rows'][ident]; job = evidence['jobs'][ident]
        if old['sha256'] != source_sha or old['source'] != row['source']:
            return False
        if evidence['inventory_mtime'] > (output / 'gltf' / (ident + '.gltf')).stat().st_mtime_ns:
            return False
        signatures = {Path(path).resolve(): value for path, value in job['source_signature'].items()}
        for suffix in ('.gltf', '.bin', '.metadata'):
            path = (output / 'gltf' / (ident + suffix)).resolve()
            stat, signature = path.stat(), signatures[path]
            if stat.st_size != signature['bytes'] or stat.st_mtime_ns != signature['mtime_ns']:
                return False
        return True
    except (OSError, KeyError, TypeError):
        return False


def _legacy_texture(evidence, profile, name, source, png):
    if not evidence or '::' in source: return False
    try:
        old = evidence['textures'][name]
        if old['source'] != source or Path(old['png']).resolve() != png.resolve(): return False
        source_path = (Path(profile['game']) / source).resolve()
        source_path.relative_to(Path(profile['game']).resolve())
        return source_path.stat().st_mtime_ns <= png.stat().st_mtime_ns
    except (OSError, KeyError, ValueError):
        return False


def plan(profile, selected_rows, refs, read_resource_callable, shader_slots):
    """Hash only selected model bytes and their resolved image sources.

    read_resource_callable(game, source) returns original on-disk/archive bytes.
    Missing references stay empty in the model dependency fingerprint and never
    become image jobs, even if a same-named PNG exists in the output directory.
    """
    output = Path(profile['output']).resolve()
    previous = _manifest(profile); legacy = _legacy_evidence(profile)
    rows = {row['id']: row for row in selected_rows}
    if len(rows) != len(selected_rows): raise ValueError('模型选择包含重复 ID。')
    for ident in rows:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', ident): raise ValueError('模型 ID 包含非法路径。')
    selected_refs = []
    refs_by_model = {ident: [] for ident in rows}
    for ref in refs:
        if ref['model'] in rows:
            selected_refs.append(ref)
            refs_by_model[ref['model']].append(ref)
    texture_sources = {}
    folded_sources = {}
    for ref in selected_refs:
        name, source = ref['texture'], ref.get('source') or ''
        if not name or Path(name).name != name or any(c in name for c in '/\\:\0'):
            raise ValueError('贴图名称包含非法路径。')
        if not source: continue
        folded = name.casefold()
        if folded in folded_sources and folded_sources[folded] != source:
            raise ValueError(f'同名贴图引用不同来源，不能写入同一缓存：{name}')
        folded_sources[folded] = source
        texture_sources[name] = source
    all_sources = set(texture_sources.values()) | {row['source'] for row in rows.values()}
    workers = max(1, min(int(profile.get('workers', 4)), 16))
    def hash_source(source):
        return source, _hash(read_resource_callable(profile['game'], source))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        hashes = dict(pool.map(hash_source, sorted(all_sources)))
    identity = {'profile_id': profile['id'], 'game': str(Path(profile['game']).resolve())}
    result = {'schema_version': SCHEMA_VERSION, 'profile_id': profile['id'],
              'manifest': str(_manifest_path(profile)), 'models': {}, 'textures': {}}
    for name, source in sorted(texture_sources.items()):
        record = dict(identity, source=source, source_sha256=hashes[source])
        record['fingerprint'] = _json_hash(record)
        png = output / 'textures' / (name + '.png')
        complete = _complete_texture(png)
        prior = previous['textures'].get(name)
        adopt = (prior is None and complete and _legacy_texture(legacy, profile, name, source, png))
        same = prior is not None and prior.get('fingerprint') == record['fingerprint']
        rebuild = not complete or not (same or adopt)
        record.update(rebuild=rebuild, legacy_adopt=adopt,
                      reason='cache_incomplete' if not complete else 'legacy_adopt' if adopt else 'source_changed' if rebuild else 'unchanged')
        result['textures'][name] = record
    slots_hash = _json_hash(shader_slots)
    for ident, row in rows.items():
        source_sha = hashes[row['source']]
        if row.get('sha256') != source_sha:
            raise ValueError(f'{ident} 的源模型已在扫描后变化，请先重新扫描游戏资源。')
        dependencies = [{k: ref.get(k, '') for k in ('material', 'material_index', 'texture', 'source', 'slot', 'semantic')}
                        for ref in refs_by_model[ident]]
        dependencies.sort(key=lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False))
        record = dict(identity, source=row['source'], source_sha256=source_sha,
                      shader_slots_sha256=slots_hash, references_sha256=_json_hash(dependencies))
        record['fingerprint'] = _json_hash(record)
        complete = _complete_model(output, ident)
        prior = previous['models'].get(ident)
        adopt = (prior is None and complete and _legacy_model(legacy, output, row, source_sha))
        same = prior is not None and prior.get('fingerprint') == record['fingerprint']
        rebuild = not complete or not (same or adopt)
        record.update(rebuild=rebuild, legacy_adopt=adopt,
                      reason='cache_incomplete' if not complete else 'legacy_adopt' if adopt else 'source_changed' if rebuild else 'unchanged')
        result['models'][ident] = record
    return result


def commit(profile, planned, successful_model_ids, successful_texture_names):
    """Atomically merge successful entries, retaining unrelated cached entries."""
    if planned['profile_id'] != profile['id'] or Path(planned['manifest']).resolve() != _manifest_path(profile):
        raise ValueError('缓存计划与当前游戏输出目录不一致。')
    model_ids, texture_names = set(successful_model_ids), set(successful_texture_names)
    if model_ids - planned['models'].keys() or texture_names - planned['textures'].keys():
        raise ValueError('提交内容超出本次缓存计划。')
    document = _manifest(profile)
    document['schema_version'] = SCHEMA_VERSION
    for category, names in (('models', model_ids), ('textures', texture_names)):
        for name in names:
            document[category][name] = {k: v for k, v in planned[category][name].items()
                                        if k not in ('rebuild', 'reason')}
    path = _manifest_path(profile); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
    return path
