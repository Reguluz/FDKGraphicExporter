"""Checks for game/library isolation, cache seeding and cancellation."""
import ctypes
from ctypes import wintypes
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tool'))
import fdk_backend as backend
import profiles

TEST_ROOT = ROOT / 'reports/backend_tests'
TEST_ROOT.mkdir(parents=True, exist_ok=True)


def alive(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        kernel.CloseHandle(handle)


class BackendTests(unittest.TestCase):
    def fixture(self):
        return tempfile.TemporaryDirectory(dir=TEST_ROOT)

    def summary(self, folder, data):
        path = Path(folder) / 'reports/inventory_summary.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding='utf-8')

    def test_rescan_cannot_adopt_other_game_unmarked_library(self):
        known = profiles.read_profile({'profile_id': 'kuro2'})
        for action in ('scan', 'export'):
            with self.fixture() as folder:
                self.summary(folder, {'game': known['game']})
                with self.assertRaises(ValueError):
                    profiles.read_profile({'profile_id': 'sky2', 'output': folder, 'rescan': True, 'action': action})

    def test_marked_library_cannot_change_game(self):
        with self.fixture() as folder:
            (Path(folder) / 'asset_profile.json').write_text(json.dumps({'id': 'kuro2'}))
            with self.assertRaises(ValueError):
                profiles.read_profile({'profile_id': 'sky2', 'output': folder, 'rescan': True})

    def test_known_legacy_kuro_library_is_accepted(self):
        known = profiles.read_profile({'profile_id': 'kuro2'})
        with self.fixture() as folder:
            self.summary(folder, {'game': known['game']})
            result = profiles.read_profile({'profile_id': 'kuro2', 'output': folder})
            self.assertEqual(result['output'], str(Path(folder).resolve()))

    def test_same_profile_game_move_requires_scan(self):
        with self.fixture() as folder:
            (Path(folder) / 'asset_profile.json').write_text(json.dumps({'id': 'kuro2', 'adapter': 'loose_cle'}))
            self.summary(folder, {'game': 'F:/previous_install', 'profile_id': 'kuro2'})
            with self.assertRaises(ValueError):
                profiles.read_profile({'profile_id': 'kuro2', 'output': folder})
            result = profiles.read_profile({'profile_id': 'kuro2', 'output': folder, 'rescan': True})
            self.assertTrue(result['rescan'])

    def test_nonempty_unknown_and_external_output_rejected(self):
        with self.fixture() as folder:
            (Path(folder) / 'unknown.fbx').write_bytes(b'not an identified library')
            with self.assertRaises(ValueError):
                profiles.read_profile({'profile_id': 'kuro2', 'output': folder, 'rescan': True})
        with self.assertRaises(ValueError):
            profiles.read_profile({'profile_id': 'kuro2', 'output': 'C:/outside_workspace/fdk'})

    def test_empty_selection_and_invalid_id_rejected(self):
        for ids in ([], ['../chr0000'], ['chr0000', 5]):
            with self.assertRaises(ValueError):
                profiles.read_profile({'profile_id': 'kuro2', 'model_ids': ids})

    def test_seed_compatibility_report_does_not_leak_other_models(self):
        with self.fixture() as folder:
            backend.configure({'output': folder})
            backend.save_json(backend.REPORTS / 'compatibility_summary.json', {'jobs': [
                {'id': 'chr0001', 'status': 'ok'}, {'id': 'equ0000', 'status': 'ok'},
                {'id': 'old_removed', 'status': 'ok'}]})
            backend.seed_resume_records(['chr0001', 'equ0000'], 3, 'batch')
            records = []
            for i in range(3):
                records += json.loads((backend.REPORTS / f'batch_{i:02d}_fbx.json').read_text())['jobs']
            self.assertEqual(sorted(row['id'] for row in records), ['chr0001', 'equ0000'])

    def test_lock_excludes_concurrent_writer(self):
        with self.fixture() as folder:
            lock = Path(folder) / 'export.lock'
            with backend.runtime.export_lock(lock):
                with self.assertRaises(RuntimeError):
                    with backend.runtime.export_lock(lock):
                        self.fail('Concurrent writer accepted')
            with backend.runtime.export_lock(lock):
                pass

    def test_cancel_stops_child_and_grandchild(self):
        with self.fixture() as folder:
            folder = Path(folder)
            ready, cancel = folder / 'processes.json', folder / 'cancel'
            code = ('import subprocess,sys,time,json,os,pathlib; '
                    'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]); '
                    f'pathlib.Path({str(ready)!r}).write_text(json.dumps([os.getpid(),p.pid])); '
                    'print("ready",flush=True); time.sleep(60)')
            def trigger():
                deadline = time.monotonic() + 10
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                cancel.touch()
            thread = threading.Thread(target=trigger); thread.start()
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(backend.Cancelled):
                    backend.Runner(io.StringIO(), cancel).command([sys.executable, '-c', code])
            thread.join()
            pids = json.loads(ready.read_text())
            deadline = time.monotonic() + 3
            while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(all(not alive(pid) for pid in pids))

    def test_check_only_can_cancel_during_preflight(self):
        with self.fixture() as folder:
            folder = Path(folder)
            request = folder / 'request.json'; cancel = folder / 'cancel'
            request.write_text(json.dumps({'profile_id': 'kuro2'}))
            process = subprocess.Popen([sys.executable, ROOT / 'tool/fdk_backend.py', '--request', request,
                '--check-only', '--cancel-file', cancel], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', env=backend.runtime.environment(), creationflags=backend.NO_WINDOW)
            try:
                self.assertEqual(json.loads(process.stdout.readline())['type'], 'status')
                cancel.touch()
                output, error = process.communicate(timeout=10)
                events = [json.loads(line) for line in output.splitlines()]
                self.assertEqual(process.returncode, 130, error)
                self.assertTrue(events[-1]['cancelled'])
                self.assertFalse(events[-1]['success'])
            finally:
                if process.poll() is None:
                    process.terminate(); process.wait(timeout=10)

    @unittest.skipUnless(os.environ.get('FDK_RUN_GAME_TESTS') == '1',
                         'Set FDK_RUN_GAME_TESTS=1 with a configured local game, Blender and runtime')
    def test_preflight_accepts_nonexistent_output_without_creating_it(self):
        with self.fixture() as folder:
            folder = Path(folder)
            output = folder / 'new_library'
            request = folder / 'request.json'
            request.write_text(json.dumps({'profile_id': 'kuro2', 'output': str(output)}))
            result = subprocess.run([sys.executable, ROOT / 'tool/fdk_backend.py', '--request', request,
                '--check-only'], capture_output=True, text=True, encoding='utf-8',
                env=backend.runtime.environment(), creationflags=backend.NO_WINDOW, timeout=40)
            events = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(events[-1]['success'])
            self.assertEqual(events[-1]['status'], 'ready')
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
