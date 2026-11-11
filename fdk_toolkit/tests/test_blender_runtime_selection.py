"""ABI selection and capability failures must be caught before exporting assets."""
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tool'))
from script_paths import blender_script_args, blender_script_path, validate_blender_probe


def record(version=(4, 5, 3), python=(3, 11, 11)):
    return {'version': list(version), 'blender_version': '.'.join(map(str, version)),
            'python_version': list(python), 'operators': {'gltf_import': True,
            'fbx_export': True, 'fbx_import_legacy': True, 'fbx_import_native': False}}


class BlenderRuntimeTests(unittest.TestCase):
    def test_only_verified_lts_branches_and_python_pairs_are_accepted(self):
        self.assertIsNotNone(validate_blender_probe(record()))
        self.assertIsNotNone(validate_blender_probe(record((5, 2, 4), (3, 13, 13))))
        for version, python in [((4, 4, 0), (3, 11, 0)), ((5, 1, 0), (3, 13, 0)),
                                ((5, 3, 0), (3, 13, 0)), ((5, 2, 4), (3, 11, 0))]:
            with self.assertRaises(RuntimeError):
                validate_blender_probe(record(version, python))

    def test_missing_legacy_operator_is_rejected_even_if_native_exists(self):
        data = record(); data['operators']['gltf_import'] = False
        with self.assertRaises(RuntimeError):
            validate_blender_probe(data)
        data = record(); data['operators']['fbx_import_legacy'] = False
        with self.assertRaises(RuntimeError):
            validate_blender_probe(data)
        data['operators']['fbx_import_native'] = True
        with self.assertRaises(RuntimeError):
            validate_blender_probe(data)

    def test_compiled_bootstrap_uses_own_abi_and_preserves_script_arguments(self):
        with tempfile.TemporaryDirectory(prefix='FDK Unicode 空格 ') as folder:
            root = Path(folder)
            source = root / 'fixture.py'
            source.write_text("import sys; assert sys.argv[-2:]==['--','example']; print('EXECUTED')", encoding='utf-8')
            pyc = root / 'blender_py311/fixture.pyc'; pyc.parent.mkdir()
            py_compile.compile(str(source), cfile=str(pyc), doraise=True)
            source.unlink()
            self.assertEqual(blender_script_path(source, [3, 11, 11]), pyc)
            self.assertEqual(blender_script_path(source, [3, 13, 13]), root / 'blender_py313/fixture.pyc')
            expression = blender_script_args(source)[-1]
            fake_bpy = "import sys,types; sys.modules['bpy']=types.SimpleNamespace(app=types.SimpleNamespace(version=(4,5,3))); "
            result = subprocess.run([sys.executable, '-c', fake_bpy + expression, '--', 'example'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('EXECUTED', result.stdout)
            # A wrongly packed interpreter version must fail without executing the payload.
            data = pyc.read_bytes(); pyc.write_bytes(b'BAD!' + data[4:])
            result = subprocess.run([sys.executable, '-c', fake_bpy + expression, '--', 'example'],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('bytecode version mismatch', result.stderr)
            self.assertNotIn('EXECUTED', result.stdout)


if __name__ == '__main__':
    unittest.main()
