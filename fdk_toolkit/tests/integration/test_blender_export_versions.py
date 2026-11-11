"""Export a textured synthetic rig and optional local samples with each Blender.

Run using the repository Python, passing --blender repeatedly and optionally
--sample <exported_library> <model_id>. Inputs are copied before any processing.
All generated inputs, FBX files and reports remain in ignored dist/qa_1_3.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).with_name('test_release_runtime.py')


def run(command, environment):
    result = subprocess.run([str(a) for a in command], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', env=environment, timeout=300)
    if result.returncode:
        raise RuntimeError(result.stdout + '\n' + result.stderr)
    return result.stdout


def copy_sample(library, model, destination):
    source = library / 'gltf' / (model + '.gltf')
    document = json.loads(source.read_text(encoding='utf-8'))
    folder = destination / 'gltf'; folder.mkdir(parents=True)
    textures = destination / 'textures'; textures.mkdir()
    for index, buffer in enumerate(document.get('buffers', [])):
        original = source.parent / unquote(buffer['uri'])
        name = f'{model}_{index}.bin'
        shutil.copy2(original, folder / name)
        buffer['uri'] = name
    for index, image in enumerate(document.get('images', [])):
        uri = image.get('uri', '')
        if not uri or uri.startswith('data:'):
            continue
        original = source.parent / unquote(uri)
        name = f'{index}_{original.name}'
        if original.is_file():
            shutil.copy2(original, textures / name)
        image['uri'] = '../textures/' + name
    target = folder / source.name
    target.write_text(json.dumps(document), encoding='utf-8')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', required=True, type=Path, action='append')
    parser.add_argument('--sample', nargs=2, action='append', default=[], metavar=('LIBRARY', 'MODEL'))
    args = parser.parse_args()
    destination = ROOT / 'dist/qa_1_3' / ('export_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    destination.mkdir(parents=True)
    env = dict(os.environ)
    for key in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONUSERBASE'):
        env.pop(key, None)
    for kind in ('CONFIG', 'SCRIPTS', 'EXTENSIONS'):
        env['BLENDER_USER_' + kind] = str(destination / ('blender_' + kind.lower()))
    synthetic = destination / 'synthetic'; synthetic.mkdir()
    gltf = synthetic / 'synthetic.gltf'
    expression = (f"import runpy; m=runpy.run_path({str(FIXTURE)!r}); "
                  f"m['create_scene'](m['Path']({str(gltf)!r}))")
    run([args.blender[0], '--background', '--factory-startup', '--python-exit-code', '1',
         '--python-expr', expression], env)
    inputs = [('synthetic', gltf)]
    for index, (library, model) in enumerate(args.sample):
        if not re.fullmatch(r'[A-Za-z0-9_-]+', model):
            parser.error('Invalid model ID')
        key = f'sample{index}_{model}'
        inputs.append((key, copy_sample(Path(library).resolve(), model, destination / key)))
    records = []
    first_synthetic = None
    for index, blender in enumerate(args.blender):
        output = destination / ('blender_' + str(index)); output.mkdir()
        manifest = output / 'jobs.json'
        manifest.write_text(json.dumps({'jobs': [
            {'id': key, 'gltf': str(path), 'fbx': str(output / (key + '.fbx'))}
            for key, path in inputs]}), encoding='utf-8')
        report = output / 'report.json'
        log = run([blender, '--background', '--factory-startup', '--python-exit-code', '1',
                   '--python', ROOT / 'fdk_toolkit/tool/blender_export.py', '--',
                   '--manifest', manifest, '--report', report, '--verify'], env)
        (output / 'export.log').write_text(log, encoding='utf-8')
        result = json.loads(report.read_text(encoding='utf-8'))
        assert result['completed'] == len(inputs) and result['failed'] == 0, result
        synthetic_result = next(job for job in result['jobs'] if job['id'] == 'synthetic')
        if first_synthetic is None:
            first_synthetic = synthetic_result
        expected = sorted({uv for binding in synthetic_result['before']['texture_bindings']
                           for uv in binding['uv_sets']})
        assert expected and synthetic_result['uv_bake']['mapping_nodes_baked'] > 0
        assert synthetic_result['before']['uv_loop_count'] == 6
        expression = (f"import runpy; m=runpy.run_path({str(FIXTURE)!r}); "
                      f"m['check_fbx_uv_sets']({str(output / 'synthetic.fbx')!r},{expected!r})")
        run([blender, '--background', '--factory-startup', '--python-exit-code', '1',
             '--python-expr', expression], env)
        records.append({'blender': result['blender_version'], 'completed': result['completed'],
                        'report': str(report), 'uv_sets': expected})
    # A source-script hash is identical across Blender versions. Successful
    # results must be reused only in the version that actually verified them.
    resume_manifest = destination / 'resume_jobs.json'
    resume_manifest.write_text(json.dumps({'jobs': [
        {key: first_synthetic[key] for key in ('id', 'gltf', 'fbx')}
    ]}), encoding='utf-8')
    resume_report = destination / 'resume_report.json'
    resume_report.write_text(json.dumps({'jobs': [first_synthetic]}), encoding='utf-8')
    base = ['--background', '--factory-startup', '--python-exit-code', '1', '--python',
            ROOT / 'fdk_toolkit/tool/blender_export.py', '--', '--manifest', resume_manifest,
            '--report', resume_report, '--verify', '--resume']
    run([args.blender[0], *base], env)
    reused = json.loads(resume_report.read_text(encoding='utf-8'))
    assert reused['skipped_this_run'] == 1 and reused['processed_this_run'] == 0, reused
    if records[-1]['blender'] != records[0]['blender']:
        run([args.blender[-1], *base], env)
        changed = json.loads(resume_report.read_text(encoding='utf-8'))
        assert changed['processed_this_run'] == 1 and changed['skipped_this_run'] == 0, changed
        assert changed['jobs'][0]['blender_version'] == records[-1]['blender']
    (destination / 'summary.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
    print(json.dumps({'passed': True, 'runs': records}, indent=2))


if __name__ == '__main__':
    main()
