"""Install, restart and exercise the generic ZIP in isolated Blender preferences."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
KURO = ROOT.parent / 'kuro2_export'
SKY = ROOT.parent / 'sky2_export'


def snapshot(objects):
    digest = hashlib.sha256()
    for obj in sorted(objects, key=lambda o:o.name):
        digest.update(repr((obj.name, tuple(tuple(row) for row in obj.matrix_world))).encode())
        if obj.type == 'MESH':
            for vertex in obj.data.vertices:
                digest.update(repr((tuple(vertex.co), [(g.group,g.weight) for g in vertex.groups])).encode())
            for uv in obj.data.uv_layers:
                for item in uv.data:
                    digest.update(repr(tuple(item.uv)).encode())
        if obj.type == 'ARMATURE':
            for bone in obj.data.bones:
                digest.update(repr((bone.name, tuple(tuple(row) for row in bone.matrix_local))).encode())
    return digest.hexdigest()


def fixture(root, ident):
    for directory in ('fbx','gltf','materials','textures','reports'):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / 'asset_profile.json').write_text(json.dumps({'id':ident,'name':ident}), encoding='utf-8')
    for rel in ('materials/chr0000.json','gltf/chr0000.gltf','reports/shader_slots.json'):
        shutil.copy2(KURO / rel, root / rel)
    document = json.loads((KURO / 'gltf/chr0000.gltf').read_text(encoding='utf-8'))
    for image in document['images']:
        source = (KURO / 'gltf' / image['uri']).resolve()
        if source.is_file():
            shutil.copy2(source, root / source.relative_to(KURO))


def inside(phase, run):
    import bpy
    import addon_utils
    for resource in ('CONFIG','SCRIPTS'):
        Path(bpy.utils.user_resource(resource)).resolve().relative_to(run.resolve())
    bpy.context.preferences.view.language = 'zh_HANS'
    bpy.context.preferences.view.use_translate_interface = True
    bpy.context.preferences.view.use_translate_new_dataname = True
    if phase == 'install':
        assert bpy.ops.preferences.addon_install(filepath=str(ROOT / 'FDK_Blender_Addon.zip'), overwrite=True) == {'FINISHED'}
        assert bpy.ops.preferences.addon_enable(module='kuro2_preview') == {'FINISHED'}
    import kuro2_preview as addon
    assert addon.bl_info['version'] == (1,3,0)
    assert addon.materials._is_shadow_helper({'material_name':'shadow','material_switches':[{'material_switch_name':'SWITCH_SHADOWONLY','int2':-1}]})
    assert not addon.materials._is_shadow_helper({'material_name':'shadow','material_switches':[{'material_switch_name':'SWITCH_SHADOWONLY','int2':0}]})
    assert not addon.materials._is_shadow_helper({'material_name':'shadow'})
    assert addon_utils.check('kuro2_preview') == (True,True)
    assert bpy.types.KURO2_PT_main.bl_category == 'FDK'
    settings = bpy.context.scene.kuro2_settings
    prefs = bpy.context.preferences.addons['kuro2_preview'].preferences
    if phase == 'sky':
        # This is an isolated test process; remove its factory cube/camera/light
        # so the delivered sample contains only the imported asset and its stage.
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        settings.library = 'sky2'
        assert Path(settings.asset_root) == SKY
        expected = ('chr0001', 'chr0001_c01', 'chr5002', 'mon5000')
        assert set(expected).issubset({item.name for item in settings.catalog})
        results = []
        for ident in expected:
            result, objects = addon.import_model(bpy.context, SKY, ident, create_stage=True)
            assert result['profile_id'] == 'sky2'
            assert not result['unmatched_materials'], result
            assert result['texture_nodes'] > 0
            assert result['bones'] > 0
            before = snapshot(objects)
            collection = addon.current_collection(bpy.context)
            assert collection['fdk_stage_id'] == 'sky2__' + ident
            for mode in ('UNLIT', 'PRINCIPLED'):
                settings.material_mode = mode
                assert bpy.ops.kuro2.rebuild_materials() == {'FINISHED'}
                assert snapshot(objects) == before
            if ident == 'chr5002':
                helper = [m for o in objects if o.type=='MESH' for m in o.data.materials if m and m.get('kuro2_source_material')=='shadow']
                assert helper and all(m.get('kuro2_hidden_helper') for m in helper)
                assert result['hidden_helper_materials'] >= 1
            if ident == 'chr0001':
                destination = SKY / 'previews'
                destination.mkdir(exist_ok=True)
                bpy.ops.wm.save_as_mainfile(filepath=str(destination / 'chr0001_preview.blend'))
                bpy.context.scene.render.resolution_percentage = 50
                bpy.context.scene.render.filepath = str(destination / 'chr0001_preview.png')
                bpy.ops.render.render(write_still=True)
            results.append({'id':ident,'bones':result['bones'],'meshes':result['meshes'],
                            'materials':result['material_count'],'texture_nodes':result['texture_nodes'],
                            'missing_textures':result['missing_textures'],'hidden_helpers':result['hidden_helper_materials'],
                            'geometry_uv_skin_unchanged':True})
        report = {'passed':True,'phase':phase,'language':'zh_HANS','models':results,
                  'render':str(SKY / 'previews/chr0001_preview.png'),
                  'blend':str(SKY / 'previews/chr0001_preview.blend')}
        (run / (phase + '.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print('FDK_ADDON_TEST_OK',phase)
        return
    if phase == 'restart':
        assert prefs.default_asset_root == str(KURO)
        assert Path(settings.asset_root) == KURO
        assert settings.library == 'kuro2'
        assert bpy.ops.kuro2.refresh_catalog() == {'FINISHED'}
        settings.model_id = 'chr0000'
        assert bpy.ops.kuro2.import_model() == {'FINISHED'}
        report = {'passed':True,'phase':phase,'version':addon.bl_info['version'],
                  'preferences_persisted':True,'import_after_restart':True,'language':bpy.context.preferences.view.language}
    else:
        prefs.library_config = str(ROOT / 'profiles.json')
        settings.library = 'kuro2'
        assert Path(settings.asset_root) == KURO
        assert len(settings.catalog) == 2057
        settings.search = 'chr0000'
        settings.model_id = 'chr0000'
        settings.library = 'sky2'
        assert Path(settings.asset_root) == SKY and settings.search == ''
        assert settings.model_id != 'chr0000'
        settings.library = 'kuro2'
        settings.model_id = 'chr0000'
        sentinel = bpy.data.objects.new('User sentinel',None)
        bpy.context.scene.collection.objects.link(sentinel)
        result, objects = addon.import_model(bpy.context,KURO,'chr0000',create_stage=True)
        assert result['bones'] == 197
        assert result['profile_id'] == 'kuro2'
        collection = addon.current_collection(bpy.context)
        assert collection['fdk_profile_id'] == 'kuro2'
        assert collection['fdk_stage_id'] == 'kuro2__chr0000'
        before = snapshot(objects)
        # Existing 1.1.x images must be corrected on cache hits as well.
        cached = {node.image for o in objects if o.type=='MESH' for m in o.data.materials
                  for node in m.node_tree.nodes if node.type=='TEX_IMAGE' and node.image}
        for image in cached:
            image.alpha_mode = 'STRAIGHT'
        for mode in ('PRINCIPLED', 'UNLIT'):
            settings.material_mode = mode
            assert bpy.ops.kuro2.rebuild_materials() == {'FINISHED'}
            assert all(image.alpha_mode == 'CHANNEL_PACKED' for image in cached)
            hair = next(m for o in objects if o.type=='MESH' for m in o.data.materials
                        if m.get('kuro2_source_material')=='hair_01')
            report = json.loads(hair['kuro2_material_report'])
            assert report['alpha_mode'] in ('MASK','BLEND')
            if mode == 'PRINCIPLED':
                shader = next(n for n in hair.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
                assert shader.inputs['Alpha'].is_linked
            else:
                shader = next(n for n in hair.node_tree.nodes if n.type=='MIX_SHADER')
                assert shader.inputs[0].is_linked
            assert any(link.from_socket.name == 'Alpha' and link.from_node.type=='TEX_IMAGE'
                       for link in hair.node_tree.links)
            assert snapshot(objects) == before
        before_materials = [m.as_pointer() for o in objects if o.type=='MESH' for m in o.data.materials]
        foreign = run / 'foreign_library'
        fixture(foreign, 'sky2')
        settings.asset_root = str(foreign)
        try:
            bpy.ops.kuro2.rebuild_materials()
        except RuntimeError as error:
            assert '请切回对应游戏资源库' in str(error)
        else:
            raise AssertionError('Cross-game rebinding was allowed')
        assert snapshot(objects) == before
        assert before_materials == [m.as_pointer() for o in objects if o.type=='MESH' for m in o.data.materials]
        relocated = run / 'relocated_kuro2'
        fixture(relocated, 'kuro2')
        settings.asset_root = str(relocated)
        settings.material_mode = 'UNLIT'
        assert bpy.ops.kuro2.rebuild_materials() == {'FINISHED'}
        assert snapshot(objects) == before
        assert Path(collection['asset_root']) == relocated
        for o in objects:
            if o.type == 'MESH':
                for mat in o.data.materials:
                    for node in mat.node_tree.nodes:
                        if node.type == 'TEX_IMAGE' and node.image:
                            Path(node.image.filepath).resolve().relative_to(relocated)
        # A leftover PNG must not override the current source-reference audit.
        document = json.loads((relocated / 'gltf/chr0000.gltf').read_text(encoding='utf-8'))
        stale = next(Path(image['uri']).stem for image in document['images']
                     if (relocated / 'gltf' / image['uri']).is_file())
        audit = relocated / 'reports/texture_references.json'
        audit.write_text(json.dumps([{'model':'chr0000','texture':stale,'source':''}]), encoding='utf-8')
        missing_report = addon.materials.build_materials(objects, relocated, 'chr0000')
        assert stale in missing_report['missing_textures']
        matching = [node for o in objects if o.type=='MESH' for mat in o.data.materials
                    for node in mat.node_tree.nodes if node.type=='TEX_IMAGE'
                    and node.get('kuro2_relative_path') == f'textures/{stale}.png']
        assert matching and all(node.image is None for node in matching)
        assert (relocated / 'textures' / (stale + '.png')).is_file()
        assert snapshot(objects) == before
        # Legacy identity migration remains safe, including inaccessible roots.
        del collection['fdk_profile_id']; del collection['fdk_profile_name']
        collection['asset_root'] = str(KURO)
        assert addon.validate_rebuild_library(collection, KURO)['id'] == 'kuro2'
        try:
            addon.validate_rebuild_library(collection, foreign)
        except ValueError:
            pass
        else:
            raise AssertionError('Legacy collection cross-game rebinding was allowed')
        collection['asset_root'] = str(run / 'unidentified_old_root')
        try:
            addon.validate_rebuild_library(collection, relocated)
        except ValueError:
            pass
        else:
            raise AssertionError('Unknown legacy provenance was guessed')
        collection['asset_root'] = str(KURO)
        settings.library = 'kuro2'
        settings.material_mode = 'PRINCIPLED'
        assert bpy.ops.kuro2.rebuild_materials() == {'FINISHED'}
        assert sentinel.name in bpy.context.scene.objects
        # A public ZIP must not invent local libraries when configuration is missing.
        prefs.library_config = str(run / 'missing_profiles.json')
        assert addon.library_records() == []  # Public packages carry no personal fallback libraries.
        prefs.library_config = str(ROOT / 'profiles.json')
        assert bpy.ops.kuro2.save_default_root() == {'FINISHED'}
        report = {'passed':True,'phase':phase,'version':addon.bl_info['version'],'language':'zh_HANS',
                  'library_switch_no_stale_selection':True,'cross_game_rebinding_rejected':True,
                  'same_profile_relocation':True,'legacy_identity_guard':True,'preset_fallback':True,
                  'missing_source_overrides_leftover_png':True,
                  'packed_alpha_cache_repaired':True,'explicit_mask_opacity_both_modes':True,
                  'character_bones':result['bones'],'geometry_uv_skin_unchanged':True,'user_objects_preserved':True}
    (run / (phase + '.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('FDK_ADDON_TEST_OK',phase)


def outer():
    run = ROOT / 'reports' / 'blender_preview' / ('installed_' + time.strftime('%Y%m%d_%H%M%S'))
    run.mkdir(parents=True)
    env = dict(os.environ,PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
    for key,folder in [('BLENDER_USER_CONFIG','config'),('BLENDER_USER_SCRIPTS','scripts'),('BLENDER_USER_DATAFILES','datafiles'),('BLENDER_USER_EXTENSIONS','extensions')]:
        path = run / folder; path.mkdir(); env[key] = str(path)
    env.pop('PYTHONPATH',None)
    blender = json.loads((ROOT / 'profiles.json').read_text(encoding='utf-8-sig'))['blender']
    phases = ('install','restart','sky') if '--sky' in sys.argv else ('install','restart')
    for phase in phases:
        command = [blender,'--background','--offline-mode','--threads','2','--python-exit-code','1']
        if phase == 'install': command += ['--factory-startup']
        command += ['--python',str(Path(__file__).resolve()),'--','--inside',phase,'--run',str(run)]
        with (run / (phase + '.log')).open('w',encoding='utf-8') as log:
            result = subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=180)
        if result.returncode:
            raise RuntimeError(f'{phase}: {run / (phase + ".log")}')
    report = {'passed':True,'isolated_directory':str(run),'checks':[json.loads((run / (p + '.json')).read_text(encoding='utf-8')) for p in phases]}
    target = ROOT / 'reports/blender_preview/addon_installation.json'
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(target)


if __name__ == '__main__':
    if '--inside' in sys.argv:
        parser = argparse.ArgumentParser(); parser.add_argument('--inside'); parser.add_argument('--run',type=Path)
        args = parser.parse_args(sys.argv[sys.argv.index('--')+1:]); inside(args.inside,args.run)
    else:
        outer()
