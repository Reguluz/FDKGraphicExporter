"""Installed add-on checks on an explicit Blender executable and local exported libraries."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def snapshot(objects):
    digest = hashlib.sha256()
    for obj in objects:
        digest.update(repr((obj.name, tuple(tuple(row) for row in obj.matrix_world))).encode())
        if obj.type == 'MESH':
            for vertex in obj.data.vertices:
                digest.update(repr((tuple(vertex.co), [(g.group, g.weight) for g in vertex.groups])).encode())
            for layer in obj.data.uv_layers:
                for item in layer.data:
                    digest.update(repr(tuple(item.uv)).encode())
        elif obj.type == 'ARMATURE':
            for bone in obj.data.bones:
                digest.update(repr((bone.name, tuple(tuple(row) for row in bone.matrix_local))).encode())
    return digest.hexdigest()


def check_opacity(hair, destination):
    """Render real alpha-zero/alpha-one texels, including packed RGBA handling."""
    import bpy
    import numpy as np
    texture = next(node for node in hair.node_tree.nodes if node.type == 'TEX_IMAGE'
                   and node.get('kuro2_semantic') == 'DIFFUSEMAP0')
    width, height = texture.image.size
    alpha = np.array(texture.image.pixels[:]).reshape(height, width, 4)[:, :, 3]
    coords = []
    for target in (0, 1):
        candidates = np.argwhere(alpha == target)
        y, x = next((int(y), int(x)) for y, x in candidates if 3 <= y < height - 3 and 3 <= x < width - 3
                    and np.all(alpha[y - 2:y + 3, x - 2:x + 3] == target))
        coords.append((x, y))
    scene = bpy.data.scenes.new('Isolated opacity test')
    for index, (x, y) in enumerate(coords):
        mesh = bpy.data.meshes.new('Opacity test plane')
        mesh.from_pydata([(index - .5, -.5, 0), (index + .5, -.5, 0),
                          (index + .5, .5, 0), (index - .5, .5, 0)], [], [(0, 1, 2, 3)])
        obj = bpy.data.objects.new('Opacity test plane', mesh)
        scene.collection.objects.link(obj)
        material = hair.copy()
        tree = material.node_tree
        texture = next(node for node in tree.nodes if node.type == 'TEX_IMAGE'
                       and node.get('kuro2_semantic') == 'DIFFUSEMAP0')
        uv = tree.nodes.new('ShaderNodeCombineXYZ')
        uv.inputs[0].default_value, uv.inputs[1].default_value = (x + .5) / width, (y + .5) / height
        tree.links.new(uv.outputs[0], texture.inputs['Vector'])
        mesh.materials.append(material)
    camera = bpy.data.objects.new('Opacity camera', bpy.data.cameras.new('Opacity camera'))
    scene.collection.objects.link(camera)
    camera.location = (.5, 0, 5)
    camera.data.type, camera.data.ortho_scale = 'ORTHO', 2
    scene.camera = camera
    scene.render.engine = 'BLENDER_EEVEE' if bpy.app.version >= (5, 0, 0) else 'BLENDER_EEVEE_NEXT'
    scene.render.film_transparent = True
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 64, 32, 100
    scene.render.image_settings.file_format, scene.render.image_settings.color_mode = 'PNG', 'RGBA'
    scene.render.filepath = str(destination / 'opacity.png')
    bpy.ops.render.render(write_still=True, scene=scene.name)
    image = bpy.data.images.load(scene.render.filepath, check_existing=False)
    values = [image.pixels[(16 * 64 + x) * 4 + 3] for x in (16, 48)]
    assert values[0] < .01 and values[1] > .99, values
    bpy.data.scenes.remove(scene)
    return values


def inside(args):
    import bpy
    for key in ('CONFIG', 'SCRIPTS'):
        Path(bpy.utils.user_resource(key)).resolve().relative_to(args.run.resolve())
    assert bpy.app.version[:2] in {(4, 5), (5, 2)}, bpy.app.version
    if args.phase == 'restart':
        import kuro2_preview as addon
        prefs = bpy.context.preferences.addons['kuro2_preview'].preferences
        assert prefs.default_asset_root == str(args.sky)
        bpy.ops.wm.open_mainfile(filepath=str(args.run / 'saved.blend'))
        settings = bpy.context.scene.kuro2_settings
        assert Path(settings.asset_root) == args.sky
        assert settings.library == 'sky2'
        assert bpy.ops.kuro2.rebuild_materials() == {'FINISHED'}
        (args.run / 'restart.json').write_text(json.dumps({'passed': True, 'preferences_and_scene_reloaded': True}), encoding='utf-8')
        return
    # Remove only this factory test scene's starter objects, before user-object preservation checks.
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    assert bpy.ops.preferences.addon_install(filepath=str(ROOT / 'FDK_Blender_Addon.zip'), overwrite=True) == {'FINISHED'}
    assert bpy.ops.preferences.addon_enable(module='kuro2_preview') == {'FINISHED'}
    import kuro2_preview as addon
    assert addon.bl_info['version'] == (1, 3, 0)
    settings = bpy.context.scene.kuro2_settings
    prefs = bpy.context.preferences.addons['kuro2_preview'].preferences
    bpy.context.preferences.view.language = 'zh_HANS'
    bpy.context.preferences.view.use_translate_interface = True
    bpy.context.preferences.view.use_translate_new_dataname = True
    config = args.run / 'profiles.json'
    config.write_text(json.dumps({'profiles': [
        {'id': 'kuro2', 'name': 'Kuro II', 'output': str(args.kuro)},
        {'id': 'sky2', 'name': 'Sky 2', 'output': str(args.sky)},
    ]}), encoding='utf-8')
    prefs.library_config = str(config)
    settings['asset_root'] = str(args.kuro)  # Pre-1.3 backing data remains readable.
    assert Path(settings.asset_root) == args.kuro
    settings.asset_root = str(args.sky)
    assert settings['_fdk_asset_root'] == str(args.sky)
    assert Path(settings.asset_root) == args.sky
    sentinel = bpy.data.objects.new('User object to preserve', None)
    bpy.context.scene.collection.objects.link(sentinel)
    checks = []
    samples = [('kuro2', args.kuro, 'chr0000'), ('sky2', args.sky, 'chr0001'), ('sky2', args.sky, 'chr5002')]
    for ident, root, model_id in samples:
        settings.library = ident
        assert Path(settings.asset_root) == root
        report, objects = addon.import_model(bpy.context, root, model_id, create_stage=True)
        assert report['bones'] > 0 and not report['unmatched_materials'], report
        if model_id == 'chr0000':
            assert report['bones'] == 197
        signature = snapshot(objects)
        for mode in ('UNLIT', 'PRINCIPLED'):
            settings.material_mode = mode
            assert bpy.ops.kuro2.rebuild_materials() == {'FINISHED'}
            assert snapshot(objects) == signature
            images = {node.image for obj in objects if obj.type == 'MESH' for mat in obj.data.materials if mat
                      for node in mat.node_tree.nodes if node.type == 'TEX_IMAGE' and node.image}
            assert images and all(image.alpha_mode == 'CHANNEL_PACKED' for image in images)
            if model_id == 'chr0000':
                hair = next(mat for obj in objects if obj.type == 'MESH' for mat in obj.data.materials
                            if mat and mat.get('kuro2_source_material') == 'hair_01')
                assert json.loads(hair['kuro2_material_report'])['alpha_mode'] in {'MASK', 'BLEND'}
                assert any(link.from_node.type == 'TEX_IMAGE' and link.from_socket.name == 'Alpha'
                           for link in hair.node_tree.links)
                if mode == 'UNLIT':
                    alpha_pixels = check_opacity(hair, args.run)
        assert bpy.ops.kuro2.setup_stage() == {'FINISHED'}
        assert snapshot(objects) == signature
        expected_engine = 'BLENDER_EEVEE' if bpy.app.version >= (5, 0, 0) else 'BLENDER_EEVEE_NEXT'
        assert bpy.context.scene.render.engine == expected_engine
        if model_id == 'chr5002':
            assert report['hidden_helper_materials'] >= 1
        if model_id == 'chr0001':
            scene = bpy.context.scene
            scene.render.resolution_percentage = 25
            scene.render.filepath = str(args.run / 'preview.png')
            bpy.ops.render.render(write_still=True)
            assert (args.run / 'preview.png').is_file()
        checks.append({'model': model_id, 'game': ident, 'bones': report['bones'], 'meshes': report['meshes'],
                       'texture_nodes': report['texture_nodes'], 'geometry_uv_skin_unchanged': True})
    assert sentinel.name in bpy.context.scene.objects
    collection = addon.current_collection(bpy.context)
    settings.library = 'kuro2'
    try:
        addon.validate_rebuild_library(collection, args.kuro)
    except ValueError:
        pass
    else:
        raise AssertionError('Cross-game rebinding allowed')
    settings.library = 'sky2'
    assert bpy.ops.kuro2.save_default_root() == {'FINISHED'}
    bpy.ops.wm.save_as_mainfile(filepath=str(args.run / 'saved.blend'))
    (args.run / 'main.json').write_text(json.dumps({'passed': True, 'blender': bpy.app.version_string,
        'python': sys.version.split()[0], 'addon': addon.bl_info['version'], 'checks': checks,
        'rendered_alpha': alpha_pixels,
        'legacy_directory_migration': True, 'cross_game_rebind_rejected': True,
        'chinese_nodes_and_stage_render': True}, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', type=Path)
    parser.add_argument('--kuro', type=Path, required=True)
    parser.add_argument('--sky', type=Path, required=True)
    parser.add_argument('--run', type=Path)
    parser.add_argument('--phase', choices=('main', 'restart'))
    values = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else sys.argv[1:]
    args = parser.parse_args(values)
    args.kuro, args.sky = args.kuro.resolve(), args.sky.resolve()
    if args.phase:
        inside(args)
        return
    if not args.blender:
        parser.error('--blender is required outside Blender')
    run = ROOT.parent / 'dist/qa_1_3/addon' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    run.mkdir(parents=True)
    env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    for key in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONUSERBASE'):
        env.pop(key, None)
    for key, name in [('BLENDER_USER_CONFIG', 'config'), ('BLENDER_USER_SCRIPTS', 'scripts'),
                      ('BLENDER_USER_DATAFILES', 'datafiles'), ('BLENDER_USER_EXTENSIONS', 'extensions')]:
        folder = run / name
        folder.mkdir()
        env[key] = str(folder)
    for phase in ('main', 'restart'):
        command = [str(args.blender), '--background', '--offline-mode', '--threads', '2', '--python-exit-code', '1']
        if phase == 'main':
            command += ['--factory-startup']
        command += ['--python', str(Path(__file__).resolve()), '--', '--phase', phase,
                    '--run', str(run), '--kuro', str(args.kuro), '--sky', str(args.sky)]
        with (run / (phase + '.log')).open('w', encoding='utf-8') as log:
            result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError(str(run / (phase + '.log')))
    print(json.dumps({'passed': True, 'report': str(run), 'blender': str(args.blender)}))


if __name__ == '__main__':
    main()
