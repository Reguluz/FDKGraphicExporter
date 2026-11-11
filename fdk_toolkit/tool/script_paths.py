"""Resolve development sources and compiled portable-release entry points."""
from pathlib import Path
import json
import os
import subprocess

SUPPORTED_BLENDER = {(4, 5): (3, 11), (5, 2): (3, 13)}
PROBE_PREFIX = 'FDK_BLENDER_PROBE '


def script_path(path):
    path = Path(path)
    return path if path.is_file() else path.with_suffix('.pyc')


def blender_script_args(path):
    """Select bytecode inside Blender, never from the backend's Python version."""
    path = Path(path).resolve().with_suffix('.py')
    expression = (
        "import bpy,sys,runpy,importlib.util; from pathlib import Path; "
        f"supported={SUPPORTED_BLENDER!r}; "
        "version=tuple(bpy.app.version[:2]); "
        "assert version in supported, 'Supported Blender versions: 4.5 LTS / 5.2 LTS'; "
        "assert tuple(sys.version_info[:2])==supported[version], 'Unsupported Blender Python runtime'; "
        f"source=Path({str(path)!r}); "
        "compiled=source.parent/('blender_py%d%d'%sys.version_info[:2])/(source.stem+'.pyc'); "
        "selected=source if source.is_file() else compiled; "
        "assert selected.is_file(), 'Missing Blender script: '+str(selected); "
        "assert selected.suffix=='.py' or selected.read_bytes()[:4]==importlib.util.MAGIC_NUMBER, 'Blender bytecode version mismatch'; "
        "sys.path.insert(0,str(selected.parent)); "
        "runpy.run_path(str(selected),run_name='__main__')")
    return ['--python-exit-code', '1', '--python-expr', expression]


def blender_script_path(path, python_version):
    source = Path(path).with_suffix('.py')
    if source.is_file():
        return source
    major, minor = python_version[:2]
    return source.parent / f'blender_py{major}{minor}' / (source.stem + '.pyc')


def validate_blender_probe(record):
    version = tuple(record['version'][:2])
    if version not in SUPPORTED_BLENDER:
        raise RuntimeError('目前支持 Blender 4.5 LTS 和 5.2 LTS；所选版本为 ' + record['blender_version'])
    if tuple(record['python_version'][:2]) != SUPPORTED_BLENDER[version]:
        raise RuntimeError('此 Blender 内置 Python 与已验证的官方版本不一致。')
    operators = record['operators']
    # Round-trip checks intentionally keep the legacy importer so bone/UV
    # semantics stay identical across the two supported official LTS branches.
    required = ['gltf_import', 'fbx_export', 'fbx_import_legacy']
    missing = [name for name in required if not operators.get(name)]
    if missing:
        raise RuntimeError('Blender 缺少所需导入导出接口：' + ', '.join(missing))
    return record


def probe_blender(executable):
    expression = '''import bpy,sys,json,importlib.util
def available(group,name):
    try:
        getattr(getattr(bpy.ops,group),name).get_rna_type()
        return True
    except (AttributeError,RuntimeError):
        return False
print('FDK_BLENDER_PROBE '+json.dumps({
    'blender_version':bpy.app.version_string,'version':list(bpy.app.version),
    'python_version':list(sys.version_info[:3]),'bytecode_magic':importlib.util.MAGIC_NUMBER.hex(),
    'operators':{'gltf_import':available('import_scene','gltf'),
                 'fbx_export':available('export_scene','fbx'),
                 'fbx_import_legacy':available('import_scene','fbx'),
                 'fbx_import_native':available('wm','fbx_import')}}))'''
    environment = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    for key in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONUSERBASE'):
        environment.pop(key, None)
    result = subprocess.run([str(executable), '--background', '--factory-startup',
        '--disable-autoexec', '--python-exit-code', '1', '--python-expr', expression],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), env=environment)
    records = [line[len(PROBE_PREFIX):] for line in result.stdout.splitlines() if line.startswith(PROBE_PREFIX)]
    if result.returncode or len(records) != 1:
        raise RuntimeError('无法读取 Blender 运行环境，请选择官方 Blender 4.5 LTS 或 5.2 LTS。\n'
                           + (result.stderr or result.stdout)[-2000:])
    return validate_blender_probe(json.loads(records[0]))
