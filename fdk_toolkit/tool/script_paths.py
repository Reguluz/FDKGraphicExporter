"""Resolve development sources and compiled portable-release entry points."""
from pathlib import Path


def script_path(path):
    path = Path(path)
    return path if path.is_file() else path.with_suffix('.pyc')


def blender_script_args(path):
    path = script_path(path).resolve()
    # Blender's --python reads source text, while run_path also reads .pyc.
    if path.suffix == '.pyc':
        return ['--python-exit-code', '1', '--python-expr',
                f"import runpy; runpy.run_path({str(path)!r}, run_name='__main__')"]
    return ['--python-exit-code', '1', '--python', str(path)]
