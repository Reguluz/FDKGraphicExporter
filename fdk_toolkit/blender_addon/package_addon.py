"""Build a portable FDK plugin ZIP; local library paths are opt-in."""
import argparse
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(local_paths=False, destination=None):
    package = ROOT / 'blender_addon' / 'kuro2_preview'
    presets = {'profiles_path': '', 'profiles': []}
    if local_paths:
        config = ROOT / 'profiles.json'
        profiles = json.loads(config.read_text(encoding='utf-8-sig'))['profiles']
        presets = {'profiles_path': str(config.resolve()), 'profiles': [
            {'id': row['id'], 'name': row['name'], 'output': str((ROOT / row['output']).resolve())}
            for row in profiles]}
    destination = Path(destination) if destination is not None else ROOT / 'FDK_Blender_Addon.zip'
    sources = ('__init__.py', 'materials.py', 'stage.py')
    for name in sources:
        compile((package / name).read_text(encoding='utf-8'), name, 'exec')
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sources:
            archive.write(package / name, 'kuro2_preview/' + name)
        archive.writestr('kuro2_preview/library_presets.json',
                         json.dumps(presets, ensure_ascii=False, indent=2) + '\n')
        guide = ROOT / 'BLENDER_ADDON.md'
        if guide.is_file():
            archive.write(guide, 'kuro2_preview/README.md')
        license_file = ROOT.parent / 'LICENSE'
        if license_file.is_file():
            archive.write(license_file, 'kuro2_preview/LICENSE')
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-paths', action='store_true',
                        help='Include local profiles.json paths in the ZIP for personal use only')
    parser.add_argument('--output', type=Path, help='Destination ZIP path')
    args = parser.parse_args()
    print(build(local_paths=args.local_paths, destination=args.output))
