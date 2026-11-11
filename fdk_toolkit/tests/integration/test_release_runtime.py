"""No-game-assets integration: --bundle <unpacked release> --blender <blender.exe>."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def create_scene(destination):
    import bpy
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.object.armature_add()
    armature = bpy.context.object
    armature.name = 'SyntheticRig'
    bpy.ops.object.mode_set(mode='EDIT')
    bone = armature.data.edit_bones[0]
    bone.name = 'root'
    bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
    child = armature.data.edit_bones.new('tip')
    child.head, child.tail, child.parent = (0, 0, 1), (0, 0, 2), bone
    bpy.ops.object.mode_set(mode='OBJECT')
    mesh = bpy.data.meshes.new('SyntheticTriangle')
    mesh.from_pydata([(-0.5, 0, 0), (0.5, 0, 0), (0, 0, 1.5)], [], [(0, 1, 2)])
    model = bpy.data.objects.new('SyntheticMesh', mesh)
    bpy.context.collection.objects.link(model)
    model.vertex_groups.new(name='root').add([0, 1], 1.0, 'REPLACE')
    model.vertex_groups.new(name='tip').add([2], 1.0, 'REPLACE')
    model.modifiers.new(name='Skin', type='ARMATURE').object = armature
    model.parent = armature
    for name, values in [('UVPrimary', ((0, 0), (1, 0), (0.5, 1))),
                         ('UVSecondary', ((0.2, 0.1), (0.8, 0.1), (0.4, 0.9)))]:
        layer = mesh.uv_layers.new(name=name)
        for loop, value in zip(layer.data, values):
            loop.uv = value
    material = bpy.data.materials.new('SyntheticMaterial')
    material.use_nodes = True
    model.data.materials.append(material)
    nodes, links = material.node_tree.nodes, material.node_tree.links
    principled = next(n for n in nodes if n.type == 'BSDF_PRINCIPLED')
    for role, color, uv_name in [('color', (0.3, 0.6, 0.9, 1), 'UVSecondary'),
                                 ('normal', (0.5, 0.5, 1, 1), 'UVPrimary')]:
        image = bpy.data.images.new('synthetic_' + role, width=4, height=4)
        image.generated_color = color
        image.filepath_raw = str(destination.parent / ('synthetic_' + role + '.png'))
        image.file_format = 'PNG'
        image.save()
        texture = nodes.new('ShaderNodeTexImage'); texture.image = image
        uv = nodes.new('ShaderNodeUVMap'); uv.uv_map = uv_name
        if role == 'color':
            mapping = nodes.new('ShaderNodeMapping')
            mapping.inputs['Location'].default_value = (0.1, 0.2, 0)
            mapping.inputs['Scale'].default_value = (0.5, 0.75, 1)
            links.new(uv.outputs['UV'], mapping.inputs['Vector'])
            links.new(mapping.outputs['Vector'], texture.inputs['Vector'])
            links.new(texture.outputs['Color'], principled.inputs['Base Color'])
        else:
            image.colorspace_settings.name = 'Non-Color'
            normal = nodes.new('ShaderNodeNormalMap'); normal.uv_map = uv_name
            links.new(uv.outputs['UV'], texture.inputs['Vector'])
            links.new(texture.outputs['Color'], normal.inputs['Color'])
            links.new(normal.outputs['Normal'], principled.inputs['Normal'])
    bpy.ops.export_scene.gltf(filepath=str(destination), export_format='GLTF_SEPARATE',
                              export_animations=False, export_skins=True, export_yup=True)


def check_fbx_uv_sets(path, expected):
    from io_scene_fbx import parse_fbx as parse
    root, _version = parse.parse(str(path))
    names = set()
    def walk(element):
        if element.id == b'P' and element.props and element.props[0] == b'UVSet':
            value = element.props[-1]
            names.add(value.decode('utf-8') if isinstance(value, bytes) else value)
        for child in element.elems:
            walk(child)
    walk(root)
    assert set(expected) <= names, (expected, names)
    print('Explicit FBX UVSet properties:', sorted(names))


def run(*args, **kwargs):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=180, **kwargs)
    if result.returncode:
        raise AssertionError(result.stdout + '\n' + result.stderr)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--blender', required=True, type=Path)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[3]
    tests = repository / 'dist/runtime_qa'
    tests.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='中文 路径 ', dir=tests) as temporary:
        work = Path(temporary)
        bundle = work / '工具 合集'
        shutil.copytree(args.bundle, bundle)
        python = bundle / 'tools/python/Scripts/python.exe'
        env = dict(os.environ)
        for key in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONUSERBASE'):
            env.pop(key, None)
        env['PATH'] = str(Path(os.environ['WINDIR']) / 'System32')
        output = work / '合成 模型'
        (output / 'reports').mkdir(parents=True)
        gltf = output / 'synthetic.gltf'
        expression = (f"import runpy; module=runpy.run_path({str(Path(__file__).resolve())!r}); "
                      f"module['create_scene'](module['Path']({str(gltf)!r}))")
        run(args.blender, '--background', '--factory-startup', '--python-exit-code', '1',
            '--python-expr', expression, cwd=work, env=env)
        manifest = output / 'reports/all_jobs.json'
        manifest.write_text(json.dumps({'jobs': [
            {'id': 'synthetic', 'gltf': str(gltf), 'fbx': str(output / 'fbx/synthetic.fbx')}
        ]}), encoding='utf-8')
        run(python, '-X', 'utf8', '-u', bundle / 'fdk_toolkit/tool/export_fbx_batch.pyc',
            '--blender', args.blender, '--output', output, '--workers', '1', '--manifest', manifest,
            '--tag', 'synthetic', cwd=work, env=env)
        summary = json.loads((output / 'reports/synthetic_summary.json').read_text(encoding='utf-8'))
        assert summary['completed'] == 1 and not summary['failed'] and not summary['unprocessed'], summary
        record = summary['jobs'][0]
        assert record['before']['bones'] == 2 and record['before']['triangles'] == 1, record
        assert record['verification']['passed'] and (output / 'fbx/synthetic.fbx').is_file(), record
        assert len(record['before']['texture_bindings']) == 2, record
        assert record['uv_bake']['mapping_nodes_baked'] >= 1, record
        expected = sorted({name for binding in record['before']['texture_bindings'] for name in binding['uv_sets']})
        # glTF's UV0 may use the unlinked/default texture coordinate. The
        # second UV set is explicitly named and must survive as FBX UVSet.
        assert expected and record['before']['uv_loop_count'] == 6, record
        expression = (f"import runpy; module=runpy.run_path({str(Path(__file__).resolve())!r}); "
                      f"module['check_fbx_uv_sets']({str(output / 'fbx/synthetic.fbx')!r},{expected!r})")
        print(run(args.blender, '--background', '--factory-startup', '--python-exit-code', '1',
                  '--python-expr', expression, cwd=work, env=env).stdout.strip())
        # Exercise Windows spawn/import and DDS-to-PNG through the actual worker.
        texture_script = (
            "from pathlib import Path; from PIL import Image; from concurrent.futures import ProcessPoolExecutor; "
            "import fdk_assets; "
            f"game=Path({str(work / 'synthetic_game')!r}); output=Path({str(output)!r}); "
            "game.mkdir(); (output/'textures').mkdir(); "
            "Image.new('RGBA',(4,4),(20,40,60,255)).save(game/'texture.dds'); "
            "profile={'game':str(game),'output':str(output)}; "
            "pool=ProcessPoolExecutor(max_workers=1); "
            "result=list(pool.map(fdk_assets.convert_texture,[('texture','texture.dds',profile,False)])); "
            "pool.shutdown(); assert result[0]['status']=='ok',result; "
            "assert Image.open(output/'textures/texture.png').getpixel((0,0))==(20,40,60,255); "
            "print('Portable ProcessPool DDS worker: OK')")
        worker = run(python, '-X', 'utf8', '-u', '-c', texture_script, cwd=work, env=env)
        print(worker.stdout.strip())
        # The rarely used repair entry point must also load the compiled exporter.
        empty = work / 'empty.json'; empty.write_text('{"jobs":[]}', encoding='utf-8')
        sys.path.insert(0, str(repository / 'fdk_toolkit/tool'))
        from script_paths import blender_script_args
        repair = bundle / 'fdk_toolkit/tool/blender_rig_repair.py'
        run(args.blender, '--background', '--factory-startup', *blender_script_args(repair),
            '--', '--manifest', empty, '--report', work / 'repair.json', '--verify', cwd=work, env=env)
        print('FDK_RELEASE_RUNTIME_OK: Unicode/space path, bytecode workers, 2-bone textured FBX, UV transforms/sets')


if __name__ == '__main__':
    main()
