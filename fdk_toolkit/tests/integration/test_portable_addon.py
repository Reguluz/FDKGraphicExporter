"""No-assets check: blender --background --factory-startup --python <this file>."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

import bpy


ROOT = next(candidate for parent in Path(__file__).resolve().parents
            for candidate in (parent, parent / 'fdk_toolkit')
            if (candidate / 'blender_addon' / 'package_addon.py').is_file())


def check_packaging(folder):
    """Both build modes leave source presets unchanged; public ZIPs omit paths."""
    spec = importlib.util.spec_from_file_location('fdk_package', ROOT / 'blender_addon/package_addon.py')
    packager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packager)
    packager.ROOT = folder / 'toolkit'
    source = packager.ROOT / 'blender_addon/kuro2_preview'
    shutil.copytree(ROOT / 'blender_addon/kuro2_preview', source,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    before = {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()}
    expected = {'profiles_path': '', 'profiles': []}

    def presets(archive):
        with zipfile.ZipFile(archive) as bundle:
            return json.loads(bundle.read('kuro2_preview/library_presets.json'))

    assert not (packager.ROOT / 'profiles.json').exists()
    assert presets(packager.build(destination=folder / 'portable.zip')) == expected
    config = packager.ROOT / 'profiles.json'
    config.write_text(json.dumps({'profiles': [
        {'id': 'example', 'name': 'Example', 'output': '../exports/example'}
    ]}), encoding='utf-8')
    local = presets(packager.build(local_paths=True, destination=folder / 'local.zip'))
    assert local['profiles_path'] == str(config.resolve())
    assert local['profiles'][0]['output'] == str((packager.ROOT / '../exports/example').resolve())
    assert presets(packager.build(destination=folder / 'public_with_config.zip')) == expected
    assert {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()} == before


def check_plugin(folder):
    sys.path.insert(0, str(ROOT / 'blender_addon'))
    import kuro2_preview as addon
    assert addon.bl_info['version'] == (1, 2, 1)
    assert json.loads((ROOT / 'blender_addon/kuro2_preview/library_presets.json').read_text(
        encoding='utf-8')) == {'profiles_path': '', 'profiles': []}
    original_objects = {obj.as_pointer() for obj in bpy.data.objects}
    assert 'kuro2_preview' not in bpy.context.preferences.addons, 'Run with --factory-startup'
    addon.register()
    entry = bpy.context.preferences.addons.new()
    entry.module = 'kuro2_preview'
    try:
        assert hasattr(bpy.types.Scene, 'kuro2_settings')
        prefs = bpy.context.preferences.addons['kuro2_preview'].preferences
        settings = bpy.context.scene.kuro2_settings
        assert prefs.default_asset_root == prefs.library_config == ''
        assert addon.default_root() == settings.asset_root == ''
        assert settings.library == 'CUSTOM'
        assert addon.library_records() == []
        for action in (lambda: addon.asset_root(bpy.context),
                       lambda: addon.refresh_catalog(bpy.context)):
            try:
                action()
            except ValueError as error:
                assert '尚未选择导出目录' in str(error)
            else:
                raise AssertionError('An empty directory was interpreted as the working directory')

        template = json.loads((ROOT / 'profiles.example.json').read_text(encoding='utf-8-sig'))
        config = folder / 'config' / 'profiles.json'
        config.parent.mkdir()
        config.write_text(json.dumps(template), encoding='utf-8')
        prefs.library_config = str(config)
        records = addon.library_records()
        assert len(records) == len(template['profiles']) >= 2
        for row, record in zip(template['profiles'], records):
            output = Path(row['output'])
            expected = (output if output.is_absolute() else config.parent / output).resolve()
            assert record['output'] == str(expected)
            settings.search = 'stale selection'
            settings.library = row['id']
            assert Path(settings.asset_root) == expected
            assert settings.library == row['id']
            assert settings.search == '' and settings.model_id == ''
            assert len(settings.catalog) == 0

        prefs.library_config = str(folder / 'missing_profiles.json')
        settings.asset_root = ''
        assert addon.library_records() == [] and settings.library == 'CUSTOM'
        assert settings.asset_root == ''
        assert {obj.as_pointer() for obj in bpy.data.objects} == original_objects
    finally:
        bpy.context.preferences.addons.remove(entry)
        addon.unregister()
        sys.path.pop(0)
    assert not hasattr(bpy.types.Scene, 'kuro2_settings')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='fdk_portable_addon_') as temporary:
        folder = Path(temporary)
        check_packaging(folder)
        check_plugin(folder)
    print('FDK_PORTABLE_ADDON_TEST_OK: portable/local packaging, registration, empty defaults, profile switching')
