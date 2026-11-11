"""Build a clean Windows x64 runtime bundle; never reads a game or local profile."""
from __future__ import annotations
import argparse
import ast
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import py_compile
import re
import shutil
import struct
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
PYTHON_VERSION = '3.11.9'
PYTHON_URL = f'https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip'
PYTHON_SHA256 = '009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b'
PARSER_COMMIT = 'e2e6dbe47d6a917fafa7ba3e0eb2d57cdbcb8090'
PARSER_FILES = ('kuro_mdl_export_meshes.py', 'kuro_mdl_to_basic_gltf.py', 'lib_fmtibvb.py')
BLENDER_SCRIPTS = ('blender_export.py', 'blender_rig_repair.py')
TOOLS = ('archive_pac.py', 'asset_config.py',
         'cache_sources.py', 'export_fbx_batch.py', 'fdk_assets.py', 'fdk_backend.py',
         'fdk_runtime.py', 'profiles.py', 'script_paths.py')


def run(*arguments, **kwargs):
    subprocess.run([str(a) for a in arguments], check=True, **kwargs)


def portable_environment():
    env = dict(os.environ)
    for key in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONUSERBASE'):
        env.pop(key, None)
    return env


def compile_source(source, destination, display_name):
    destination.parent.mkdir(parents=True, exist_ok=True)
    py_compile.compile(str(source), cfile=str(destination), dfile=display_name,
                       doraise=True, invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)


def copy_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def compile_blender_scripts(toolkit, blender52):
    """Only these two scripts run in Blender's Python; the backend stays 3.11."""
    for name in BLENDER_SCRIPTS:
        compile_source(ROOT / 'fdk_toolkit/tool' / name,
                       toolkit / 'tool/blender_py311' / Path(name).with_suffix('.pyc'),
                       'fdk_toolkit/tool/' + name)
    destination = toolkit / 'tool/blender_py313'
    destination.mkdir(parents=True)
    expression = (
        'import bpy,sys,py_compile; from pathlib import Path; '
        "assert bpy.app.version[:2]==(5,2) and sys.version_info[:2]==(3,13), 'Build requires official Blender 5.2 LTS'; "
        f'sources={[(str(ROOT / "fdk_toolkit/tool" / name), str(destination / Path(name).with_suffix(".pyc")), "fdk_toolkit/tool/" + name) for name in BLENDER_SCRIPTS]!r}; '
        '[py_compile.compile(source,cfile=target,dfile=display,doraise=True,'
        'invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH) for source,target,display in sources]')
    run(blender52, '--background', '--factory-startup', '--disable-autoexec',
        '--python-exit-code', '1', '--python-expr', expression, env=portable_environment())


def install_dependencies(folder, build_root):
    installed = build_root / 'wheel_install'
    run(sys.executable, '-m', 'pip', 'install', '--no-cache-dir', '--disable-pip-version-check',
        '--no-compile', '--target', installed, '-r', ROOT / 'requirements.txt')
    skip_parts = {'tests', 'test', '__pycache__', 'docs', 'doc', 'benchmarks', 'examples', '_examples', 'bin',
                  'f2py', '_pyinstaller', 'include'}
    skip_extensions = {'.pyc', '.pyi', '.h', '.c', '.cpp', '.pxd', '.pxi', '.f', '.f90', '.src',
                       '.lib', '.pyx', '.whl', '.build', '.typed'}
    for source in sorted(installed.rglob('*')):
        if not source.is_file():
            continue
        relative = source.relative_to(installed)
        if any(part.casefold() in skip_parts for part in relative.parts):
            continue
        if source.suffix.lower() in skip_extensions:
            continue
        # numpy.core._add_newdocs imports _multiarray_tests during normal import.
        if (source.suffix.lower() == '.pyd' and not source.name.startswith('_multiarray_tests.') and
                ('_tests' in source.name or source.name.startswith('_test'))):
            continue
        destination = folder / relative
        if source.suffix == '.py':
            compile_source(source, destination.with_suffix('.pyc'), 'site-packages/' + relative.as_posix())
        else:
            copy_file(source, destination)
    # Keep each wheel's original license/notice texts and package metadata both
    # next to the runtime and in a human-readable top-level license directory.
    license_root = build_root / 'license_copies'
    for metadata in sorted(installed.glob('*.dist-info')):
        for source in metadata.rglob('*'):
            if source.is_file() and (source.name == 'METADATA' or
                    any(word in source.name.casefold() for word in ('license', 'copying', 'notice', 'authors'))):
                copy_file(source, license_root / metadata.name / source.relative_to(metadata))
    return license_root


def write_zip(folder, destination):
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                info = zipfile.ZipInfo((Path(folder.name) / path.relative_to(folder)).as_posix(), (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, path.read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default='1.3.0')
    parser.add_argument('--blender52', required=True, type=Path,
                        help='Official Blender 5.2 LTS blender.exe, used only to compile Python 3.13 scripts')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 11) or struct.calcsize('P') != 8:
        parser.error('Build with Windows x64 Python 3.11 and the repository build environment.')
    if not re.fullmatch(r'\d+\.\d+\.\d+', args.version):
        parser.error('Version must contain three numeric components.')
    if not args.blender52.is_file():
        parser.error('--blender52 must point to Blender 5.2 LTS blender.exe.')
    addon = ROOT / 'fdk_toolkit/blender_addon/kuro2_preview/__init__.py'
    info = next(ast.literal_eval(node.value) for node in ast.parse(addon.read_text(encoding='utf-8')).body
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'bl_info' for t in node.targets))
    if tuple(map(int, args.version.split('.'))) != info['version']:
        raise ValueError('The add-on version must match the requested release.')
    commit = subprocess.check_output(['git', '-C', str(ROOT / 'tools/kuro_mdl_tool'), 'rev-parse', 'HEAD'], text=True).strip()
    if commit != PARSER_COMMIT:
        raise ValueError('The parser submodule does not match the validated commit.')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    downloads = out / 'downloads'; downloads.mkdir(exist_ok=True)
    embedded = downloads / PYTHON_URL.rsplit('/', 1)[-1]
    if not embedded.is_file():
        print('Downloading the official Python embeddable runtime...', flush=True)
        data = urllib.request.urlopen(PYTHON_URL, timeout=60).read()
        if hashlib.sha256(data).hexdigest() != PYTHON_SHA256:
            raise ValueError('Official Python download checksum mismatch.')
        embedded.write_bytes(data)
    if hashlib.sha256(embedded.read_bytes()).hexdigest() != PYTHON_SHA256:
        raise ValueError('Cached Python download checksum mismatch.')
    build = out / ('build_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    bundle = build / f'FDKGraphicExporter-v{args.version}'
    toolkit = bundle / 'fdk_toolkit'
    python_dir = bundle / 'tools/python/Scripts'
    python_dir.mkdir(parents=True)
    with zipfile.ZipFile(embedded) as archive:
        archive.extractall(python_dir)
    (python_dir / 'python311._pth').write_text(
        'python311.zip\n.\n../Lib/site-packages\n../../../fdk_toolkit/tool\nimport site\n', encoding='utf-8')
    licenses = install_dependencies(bundle / 'tools/python/Lib/site-packages', build)
    shutil.copytree(licenses, bundle / 'licenses/python_packages')
    copy_file(python_dir / 'LICENSE.txt', bundle / 'licenses/Python-LICENSE.txt')
    copy_file(ROOT / 'tools/kuro_mdl_tool/LICENSE', bundle / 'licenses/kuro_mdl_tool-LICENSE.txt')
    copy_file(ROOT / 'tools/kuro_mdl_tool/README.md', bundle / 'licenses/kuro_mdl_tool-README.md')
    run('powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ROOT / 'fdk_toolkit/tool/build_gui.ps1')
    copy_file(ROOT / 'fdk_toolkit/FDK_Asset_Tool.exe', toolkit / 'FDK_Asset_Tool.exe')
    for name in TOOLS:
        compile_source(ROOT / 'fdk_toolkit/tool' / name, toolkit / 'tool' / Path(name).with_suffix('.pyc'),
                       'fdk_toolkit/tool/' + name)
    compile_blender_scripts(toolkit, args.blender52.resolve())
    for name in PARSER_FILES:
        compile_source(ROOT / 'tools/kuro_mdl_tool' / name, bundle / 'tools/kuro_mdl_tool' / Path(name).with_suffix('.pyc'),
                       'tools/kuro_mdl_tool/' + name)
    for name in ('README.md', 'BLENDER_ADDON.md', 'THIRD_PARTY.md'):
        copy_file(ROOT / 'fdk_toolkit' / name, toolkit / name)
    copy_file(ROOT / 'fdk_toolkit/profiles.example.json', toolkit / 'profiles.json')
    copy_file(ROOT / 'fdk_toolkit/profiles.example.json', toolkit / 'profiles.example.json')
    copy_file(ROOT / 'README.md', bundle / 'README.md')
    copy_file(ROOT / 'LICENSE', bundle / 'LICENSE')
    copy_file(ROOT / 'requirements.txt', bundle / 'licenses/PYTHON_REQUIREMENTS.txt')
    for name in ('COMPATIBILITY.md', 'BUILDING.md', f'RELEASE_NOTES_v{args.version}.md'):
        if (ROOT / 'docs' / name).is_file():
            copy_file(ROOT / 'docs' / name, bundle / 'docs' / name)
    run(sys.executable, ROOT / 'fdk_toolkit/blender_addon/package_addon.py', '--output', toolkit / 'FDK_Blender_Addon.zip')
    (bundle / 'SOURCE.txt').write_text(
        f'FDKGraphicExporter v{args.version}\nGPL-3.0 source and build instructions:\n'
        f'https://github.com/Reguluz/FDKGraphicExporter/tree/v{args.version}\n'
        f'https://github.com/Reguluz/FDKGraphicExporter/archive/refs/tags/v{args.version}.zip\n\n'
        'Pinned upstream parser source (GPL-3.0):\n'
        f'https://github.com/eArmada8/kuro_mdl_tool/tree/{PARSER_COMMIT}\n'
        f'https://github.com/eArmada8/kuro_mdl_tool/archive/{PARSER_COMMIT}.zip\n\n'
        f'Official CPython {PYTHON_VERSION} runtime:\n{PYTHON_URL}\n'
        f'SHA256: {hashlib.sha256(embedded.read_bytes()).hexdigest()}\n'
        'Python source: https://www.python.org/ftp/python/3.11.9/Python-3.11.9.tar.xz\n'
        'Third-party wheel package licenses and metadata: licenses/python_packages/\n'
        'Backend runtime: CPython 3.11. Blender scripts: Python 3.11 / 3.13 bytecode from the same source.\n'
        'Supported Blender branches: 4.5 LTS / 5.2 LTS; Blender is installed separately.\n'
        'The Blender add-on ZIP uses the standard installable Python add-on format.\n', encoding='utf-8')
    # Verify imports using only the embedded runtime and its own relative paths.
    run(python_dir / 'python.exe', '-X', 'utf8', '-u', '-c',
        "import sys,numpy,PIL,zstandard,blowfish,xxhash,pyquaternion,lz4.frame,fdk_assets,fdk_backend; "
        "assert sys.flags.utf8_mode == 1; print('Portable dependency imports OK:',sys.version)", cwd=bundle, env=portable_environment())
    run(python_dir / 'python.exe', '-X', 'utf8', '-u', toolkit / 'tool/fdk_backend.pyc', '--help', cwd=bundle, env=portable_environment())
    # Explicit packaging checks: only builder-created files can enter the ZIP.
    forbidden_dirs = {'reports', 'previews', 'fbx', 'textures', 'gltf', 'materials', '.git', '__pycache__'}
    forbidden_suffixes = {'.py', '.cs', '.ps1', '.pac', '.mdl', '.dds', '.fbx', '.blend', '.png', '.log'}
    for path in bundle.rglob('*'):
        rel = path.relative_to(bundle)
        if any(part in forbidden_dirs for part in rel.parts) or path.suffix.lower() in forbidden_suffixes:
            raise ValueError(f'Unexpected source, asset or generated result in bundle: {rel}')
    package = out / f'FDKGraphicExporter-v{args.version}-windows-x64.zip'
    write_zip(bundle, package)
    addon_zip = out / f'FDK_Blender_Addon-v{args.version}.zip'
    copy_file(toolkit / 'FDK_Blender_Addon.zip', addon_zip)
    checksums = ''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in (package, addon_zip))
    (out / 'SHA256SUMS.txt').write_text(checksums, encoding='ascii')
    print(json.dumps({'bundle_directory': str(bundle), 'package': str(package), 'addon': str(addon_zip),
                      'checksums': str(out / 'SHA256SUMS.txt')}, indent=2))


if __name__ == '__main__':
    main()
