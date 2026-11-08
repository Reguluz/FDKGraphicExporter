"""Explicit game adapters and isolated output libraries for the FDK asset tools."""
from pathlib import Path
import json
import re

TOOLKIT = Path(__file__).resolve().parents[1]
WORKSPACE = TOOLKIT.parent


def read_profile(request):
    registry = json.loads((TOOLKIT / 'profiles.json').read_text(encoding='utf-8-sig'))
    candidates = [p for p in registry['profiles'] if p['id'] == request.get('profile_id')]
    if len(candidates) != 1:
        raise ValueError('请选择已配置的游戏；未知 FDK 游戏需要先验证格式适配器。')
    profile = dict(candidates[0])
    for key in ('game', 'output'):
        path = Path(request.get(key) or profile[key]).expanduser()
        profile[key] = str((path if path.is_absolute() else TOOLKIT / path).resolve())
    blender = Path(request.get('blender') or registry['blender'])
    profile['blender'] = str((blender if blender.is_absolute() else TOOLKIT / blender).resolve())
    profile['workers'] = request.get('workers', registry.get('workers', 4))
    if type(profile['workers']) is not int or not 1 <= profile['workers'] <= 32:
        raise ValueError('并行任务数必须为 1 到 32。')
    for flag in ('rescan', 'rebuild_intermediate'):
        value = request.get(flag, False)
        if type(value) is not bool:
            raise ValueError(f'{flag} 必须为布尔值。')
        profile[flag] = value
    profile['action'] = request.get('action', 'export')
    if profile['action'] not in ('export', 'scan'):
        raise ValueError('未知操作。')
    ids = request.get('model_ids')
    if ids is not None:
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', i) for i in ids):
            raise ValueError('请勾选有效的模型，或选择全部模型。')
        ids = sorted(set(ids))
    profile['model_ids'] = ids
    game, output = Path(profile['game']), Path(profile['output'])
    if output == game or game in output.parents or output in game.parents:
        raise ValueError('输出目录必须与只读游戏安装目录分开。')
    if output == WORKSPACE or WORKSPACE not in output.parents:
        raise ValueError('资源输出必须位于当前工作区内的独立子目录。')
    validate_library(profile)
    return profile


def validate_library(profile):
    """Never adopt an existing library as another game, even during a rescan."""
    output, game = Path(profile['output']), Path(profile['game'])
    marker = output / 'asset_profile.json'
    identity = json.loads(marker.read_text(encoding='utf-8')) if marker.is_file() else None
    if identity is not None:
        if not isinstance(identity, dict) or not identity.get('id'):
            raise ValueError('现有资源库的身份标记无效，请先检查 asset_profile.json。')
        if identity.get('id') != profile['id'] or identity.get('adapter', profile['adapter']) != profile['adapter']:
            raise ValueError('此输出目录属于另一款游戏，请为每款游戏使用独立目录。')
    summary = output / 'reports/inventory_summary.json'
    if summary.is_file():
        inventory = json.loads(summary.read_text(encoding='utf-8'))
        old_game = inventory.get('game')
        old_id = inventory.get('profile_id')
        if old_id and old_id != profile['id']:
            raise ValueError('此输出目录的扫描记录属于另一款游戏，重新扫描不能更换资源库身份。')
        if not identity and old_id != profile['id'] and (not old_game or Path(old_game).resolve() != game):
            raise ValueError('现有资源库没有身份标记，且扫描记录与当前游戏不符；请使用新的独立输出目录。')
        if old_game and Path(old_game).resolve() != game and not profile['rescan'] and profile['action'] != 'scan':
            raise ValueError('游戏路径与扫描记录不同，请先扫描模型目录或勾选重新扫描。')
    elif not identity and output.exists():
        # The shared lock must exist before the backend can claim an empty
        # library. It is the only file allowed before the identity is written.
        allowed = output / 'reports/launcher/export.lock'
        if any(path.is_file() and path != allowed for path in output.rglob('*')):
            raise ValueError('此非空目录没有可确认的游戏身份；请为新游戏选择一个空目录。')


def write_identity(profile):
    from fdk_assets import write_json
    write_json(Path(profile['output']) / 'asset_profile.json', {
        'schema_version': 1, 'id': profile['id'], 'name': profile['name'],
        'source_game': profile['game'], 'adapter': profile['adapter'],
        'texture_locale': profile['locale'], 'format': 'FDK asset library',
    })
