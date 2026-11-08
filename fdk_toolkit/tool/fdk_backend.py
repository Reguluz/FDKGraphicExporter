"""Isolated JSON-lines backend for verified FDK game profiles."""
from __future__ import annotations
import argparse
import csv
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import fdk_runtime as runtime
from profiles import read_profile, validate_library, write_identity, TOOLKIT, WORKSPACE
TOOL = TOOLKIT / 'tool'
PYTHON = WORKSPACE / 'tools/python/Scripts/python.exe'
OUTPUT = REPORTS = None
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

class Cancelled(Exception):
    pass

def emit(kind, message, **values):
    print(json.dumps(dict(type=kind, message=message, **values), ensure_ascii=False), flush=True)

def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)

class Runner:
    def __init__(self, log, cancel_file):
        self.log, self.cancel_file = log, cancel_file

    def check_cancel(self):
        if self.cancel_file is not None and self.cancel_file.exists():
            raise Cancelled('已停止本次任务；再次执行可继续。')

    def say(self, message):
        self.log.write(message + '\n'); self.log.flush()
        emit('log', message)

    def phase(self, current, message):
        self.check_cancel()
        emit('progress', message, current=current, total=5, stage=message)

    def command(self, arguments, phase_lines=False):
        """Drain stdout on a thread while the main loop remains cancellable."""
        self.check_cancel()
        self.log.write('COMMAND ' + json.dumps([str(a) for a in arguments], ensure_ascii=False) + '\n')
        self.log.flush()
        tree = runtime.ProcessTree()
        process = None
        lines = queue.Queue()
        captured = []
        try:
            process = subprocess.Popen([str(a) for a in arguments], cwd=TOOLKIT,
                env=runtime.environment(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace', creationflags=NO_WINDOW)
            try:
                tree.attach(process)
            except Exception:
                process.terminate(); process.wait(timeout=10); raise

            def reader():
                try:
                    for line in process.stdout:
                        lines.put(line.rstrip('\r\n'))
                finally:
                    lines.put(None)

            thread = threading.Thread(target=reader, daemon=True)
            thread.start()
            ended = False
            labels = ['扫描模型目录', '转换贴图与模型', '保存辅助配置', '导出并回读 FBX', '校验完整文件集']
            while not ended:
                self.check_cancel()
                try:
                    line = lines.get(timeout=0.15)
                except queue.Empty:
                    continue
                if line is None:
                    ended = True
                    continue
                captured.append(line)
                if phase_lines:
                    match = re.match(r'\[(\d)/5\]', line)
                    if match:
                        index = int(match.group(1)) - 1
                        self.phase(index, labels[index])
                self.say(line)
            code = process.wait()
            self.check_cancel()
            if code:
                detail = next((line for line in reversed(captured) if line.strip()), '')
                raise RuntimeError(f'当前阶段失败（退出码 {code}）：{detail}')
            return captured
        finally:
            tree.close()
            if process is not None:
                if process.poll() is None:
                    process.wait(timeout=20)
                if process.stdout:
                    process.stdout.close()

def selected_rows(ids):
    inventory = json.loads((REPORTS / 'inventory.json').read_text(encoding='utf-8'))
    rows = {row['id']: row for row in inventory if row.get('selected')}
    unknown = sorted(set(ids) - set(rows))
    if unknown:
        raise ValueError('下列 ID 不在可导出的模型列表中：' + '、'.join(unknown[:20]))
    return [rows[ident] for ident in ids]

def recover_selected_cache(ids, backup, runner):
    """Apply the launcher's cache validation rules only to selected assets."""
    from PIL import Image
    result = {'models_checked': 0, 'textures_checked': 0, 'quarantined': []}

    def quarantine(path, error):
        destination = backup / path.relative_to(OUTPUT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        path.rename(destination)
        result['quarantined'].append({'path': str(path), 'backup': str(destination), 'reason': str(error)})

    for ident in ids:
        runner.check_cancel()
        path = OUTPUT / 'gltf' / (ident + '.gltf')
        if not path.is_file():
            continue
        result['models_checked'] += 1
        try:
            doc = json.loads(path.read_text(encoding='utf-8'))
            for buffer in doc['buffers']:
                if buffer['uri'] != ident + '.bin':
                    raise ValueError('中间文件路径未整理完成')
                if path.with_suffix('.bin').stat().st_size < buffer['byteLength']:
                    raise ValueError('中间模型二进制文件不完整')
            if any(not item['uri'].startswith('../textures/') for item in doc.get('images', [])):
                raise ValueError('中间贴图路径未整理完成')
            json.loads(path.with_suffix('.metadata').read_text(encoding='utf-8'))
            json.loads((OUTPUT / 'materials' / (ident + '.json')).read_text(encoding='utf-8'))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            quarantine(path, exc)
    references = json.loads((REPORTS / 'texture_references.json').read_text(encoding='utf-8'))
    selected = set(ids)
    names = {r['texture'] for r in references if r['model'] in selected and r.get('source')}
    for name in sorted(names):
        runner.check_cancel()
        if Path(name).name != name or any(c in name for c in '/\\:'):
            raise ValueError('贴图名称包含非法路径。')
        path = OUTPUT / 'textures' / (name + '.png')
        if not path.is_file():
            continue
        result['textures_checked'] += 1
        try:
            with Image.open(path) as image:
                if image.format != 'PNG':
                    raise ValueError('贴图不是 PNG')
                image.verify()
        except (OSError, ValueError, SyntaxError) as exc:
            quarantine(path, exc)
    return result

def seed_resume_records(ids, workers, tag):
    """Repartition previously verified results to match this run's worker count."""
    records = {}
    # Compatibility/sample batches can seed a new library as well. Reports
    # without successful job entries are ignored; exporter checks the signature.
    sources = sorted(REPORTS.glob('*_summary.json'), key=lambda p:p.stat().st_mtime_ns)
    sources.append(REPORTS / 'gui_exports/verified_cache.json')
    incremental = list(REPORTS.glob('batch_*_fbx.json'))
    incremental += list((REPORTS / 'gui_exports').glob('batch_*_*_fbx.json'))
    sources += sorted(incremental, key=lambda p:p.stat().st_mtime_ns)
    for path in sources:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        for record in data.get('jobs', []):
            if record.get('status') == 'ok':
                records[record['id']] = record
    for index in range(workers):
        jobs = [records[ident] for ident in ids if ident in records and
                int(hashlib.sha256(ident.encode()).hexdigest()[:16], 16) % workers == index]
        save_json(REPORTS / f'{tag}_{index:02d}_fbx.json', {'jobs': jobs})
    return records

def merge_resume_cache(records, workers, tag):
    for index in range(workers):
        path = REPORTS / f'{tag}_{index:02d}_fbx.json'
        if path.exists():
            try:
                for row in json.loads(path.read_text(encoding='utf-8')).get('jobs', []):
                    if row.get('status') == 'ok':
                        records[row['id']] = row
            except (OSError, ValueError):
                pass
    save_json(REPORTS / 'gui_exports/verified_cache.json', {'jobs': list(records.values())})


def load_request(path):
    request = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(request, dict):
        raise ValueError('任务配置必须是 JSON 对象。')
    return read_profile(request)


def configure(profile):
    global OUTPUT, REPORTS
    OUTPUT = Path(profile['output'])
    REPORTS = OUTPUT / 'reports'


def preflight(profile):
    validate_library(profile)
    game = Path(profile['game'])
    required = [game / profile['executable'], PYTHON, TOOL / 'fdk_assets.py',
                WORKSPACE / 'tools/kuro_mdl_tool/kuro_mdl_export_meshes.py']
    if profile['adapter'] == 'loose_cle':
        required += [game / 'c/asset/common/model', game / 'c/asset/dx11/image']
    elif profile['adapter'] == 'falcom_pac':
        if not any(game.rglob('asset_common_model.pac')):
            raise FileNotFoundError('游戏目录下找不到 asset_common_model.pac。')
        if not any(game.rglob('misc.pac')):
            raise FileNotFoundError('游戏目录下找不到 misc.pac，无法确认本游戏材质槽位。')
    else:
        raise ValueError('此游戏的输入适配器尚未实现。')
    if profile['action'] != 'scan':
        required += [Path(profile['blender']), TOOL / 'blender_export.py',
                     TOOL / 'blender_rig_repair.py', TOOL / 'export_fbx_batch.py']
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError('找不到所需文件或目录：\n' + '\n'.join(missing))
    if Path(sys.executable).resolve() != PYTHON.resolve():
        raise ValueError('请从 FDK 图形工具启动，以使用工作区 Python 环境。')
    dependencies = subprocess.run([str(PYTHON), '-c',
        'import numpy,PIL,zstandard,blowfish,xxhash,pyquaternion,lz4.frame'],
        env=runtime.environment(), capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=30, creationflags=NO_WINDOW)
    if dependencies.returncode:
        raise RuntimeError('导出依赖不完整：' + dependencies.stderr.strip())
    blender_version = None
    if profile['action'] != 'scan':
        result = subprocess.run([profile['blender'], '--version'], capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=30, creationflags=NO_WINDOW)
        if result.returncode or not result.stdout.startswith('Blender 4.5'):
            raise RuntimeError('请指定已经验证的 Blender 4.5 可执行文件。')
        blender_version = result.stdout.splitlines()[0]
    return {'profile_id': profile['id'], 'game': profile['game'], 'output': profile['output'],
            'adapter': profile['adapter'], 'python': str(PYTHON), 'blender': blender_version,
            'workers': profile['workers'], 'action': profile['action']}


def asset_worker(runner, run_dir, profile, action, **values):
    request = run_dir / f'{action}_request.json'
    save_json(request, {'action': action, 'profile': profile, **values})
    return runner.command([PYTHON, TOOL / 'fdk_assets.py', '--job', request])


def validate_inventory():
    summary = json.loads((REPORTS / 'inventory_summary.json').read_text(encoding='utf-8'))
    if summary.get('parsed') != summary.get('mdl_count'):
        raise ValueError('扫描含未解析的模型，不能将当前列表声明为完整兼容；请查看 inventory.json。')
    rows = json.loads((REPORTS / 'inventory.json').read_text(encoding='utf-8'))
    selected = [row['id'] for row in rows if row.get('selected')]
    if not selected or len(selected) != len(set(selected)):
        raise ValueError('扫描没有可导出模型，或存在重复资源 ID。')
    if any(not re.fullmatch(r'[A-Za-z0-9_-]+', ident) for ident in selected):
        raise ValueError('扫描结果中存在不安全的模型 ID。')
    return sorted(selected)


def verify_result(profile, ids, prepare_tag, batch_tag, run_dir, runner, complete):
    batch = json.loads((REPORTS / f'{batch_tag}_summary.json').read_text(encoding='utf-8'))
    records = {row['id']: row for row in batch['jobs']}
    if set(records) != set(ids) or batch.get('failed') or batch.get('unprocessed'):
        raise ValueError('FBX 验证结果与本次模型集合不一致。')
    if complete:
        actual = {path.stem for path in (OUTPUT / 'fbx').glob('*.fbx')}
        extras = sorted(actual - set(ids))
        if extras:
            raise ValueError('FBX 目录包含旧模型：' + '、'.join(extras[:20]) +
                             '。请将它们备份到 fbx 文件夹外，再执行全部导出；工具不会自动删除。')
        if actual != set(ids):
            raise ValueError('FBX 目录缺少本次要求的模型。')
    catalog = []
    for ident in sorted(ids):
        runner.check_cancel()
        row = records[ident]
        path = OUTPUT / 'fbx' / f'{ident}.fbx'
        if row.get('status') != 'ok' or not row.get('verification', {}).get('passed'):
            raise ValueError(f'{ident} 未通过 FBX 回读验证。')
        if not path.is_file() or path.stat().st_size != row['bytes'] or row['bytes'] <= 0:
            raise ValueError(f'{ident} 文件大小与验证记录不符。')
        with path.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        catalog.append({'id': ident, 'file': f'fbx/{ident}.fbx', 'bytes': row['bytes'], 'sha256': digest,
                        **{k: row['before'][k] for k in ('meshes','vertices','triangles','bones')}, 'verified': True})
    textures = json.loads((REPORTS / f'{prepare_tag}_textures.json').read_text(encoding='utf-8'))
    if any(row.get('status') != 'ok' or not Path(row['png']).is_file() for row in textures):
        raise ValueError('引用贴图转换结果不完整。')
    missing = [r for r in json.loads((REPORTS / 'missing_textures.json').read_text(encoding='utf-8')) if r['model'] in set(ids)]
    inventory = json.loads((REPORTS / 'inventory_summary.json').read_text(encoding='utf-8'))
    summary = {'profile_id': profile['id'], 'game': profile['game'], 'scope': 'all' if complete else 'selected_only',
        'mdl_files_scanned': inventory['mdl_count'], 'fbx_models': len(catalog), 'fbx_verified': len(catalog),
        'fbx_bytes': sum(r['bytes'] for r in catalog), 'png_textures': len(textures),
        'missing_unique_source_textures': sorted({r['texture'] for r in missing}),
        'missing_reference_count': len(missing), 'families': inventory.get('families', {}),
        'validation': 'Requested FBX files passed recorded Blender round-trip checks; sizes and SHA-256 catalog verified.',
        'limitations': 'Offline validation only. FDK-specific rendering, animation, IK and physics are not reproduced.'}
    destination = REPORTS if complete else run_dir
    csv_path = destination / 'fbx_catalog.csv'
    temporary = csv_path.with_suffix('.csv.tmp')
    with temporary.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(catalog[0])); writer.writeheader(); writer.writerows(catalog)
    temporary.replace(csv_path)
    save_json(destination / 'delivery_summary.json', summary)
    save_json(run_dir / 'fbx_summary.json', batch)
    runner.say(f'已验证 {len(catalog)} 个模型、{len(textures)} 张贴图；报告：{destination / "delivery_summary.json"}')


def execute(profile, run_dir, runner):
    runner.phase(0, '检查资源库与扫描模型目录')
    index_names = ['inventory.json', 'inventory_summary.json', 'model_catalog.csv',
                   'texture_references.json', 'shader_slots.json', 'missing_textures.json']
    needs_scan = profile['action'] == 'scan' or profile['rescan'] or any(not (REPORTS / name).is_file() for name in index_names)
    if needs_scan:
        asset_worker(runner, run_dir, profile, 'scan')
    all_ids = validate_inventory()
    if profile['action'] == 'scan':
        runner.phase(5, f'扫描完成，发现 {len(all_ids)} 个可导出模型')
        return {'selected_count': len(all_ids), 'catalog': str(REPORTS / 'model_catalog.csv')}
    ids = profile['model_ids'] or all_ids
    if set(ids) - set(all_ids):
        raise ValueError('所选 ID 不在当前游戏的可导出模型目录中：' + '、'.join(sorted(set(ids) - set(all_ids))[:20]))
    complete = profile['model_ids'] is None
    save_json(run_dir / 'selection.json', ids)
    overwrite = profile['rebuild_intermediate'] or profile['rescan']
    if not overwrite:
        # Runs validation in a cancellable child, including a complete library.
        recovery_request = run_dir / 'recovery_request.json'
        save_json(recovery_request, {'profile': profile, 'ids': ids,
                                    'backup': str(run_dir / 'incomplete_cache'), 'report': str(run_dir / 'cache_recovery.json')})
        runner.command([PYTHON, TOOL / 'fdk_backend.py', '--recover-worker', recovery_request])
    prepare_tag = 'all' if complete else (run_dir.relative_to(REPORTS) / 'prepared').as_posix()
    runner.phase(1, f'转换 {len(ids)} 个模型与引用贴图')
    asset_worker(runner, run_dir, profile, 'prepare', ids=ids, tag=prepare_tag, overwrite=overwrite)
    runner.phase(2, '保存同名辅助配置')
    report = REPORTS / 'model_info_summary.json' if complete else run_dir / 'model_info_summary.json'
    asset_worker(runner, run_dir, profile, 'model_info', ids=ids, report=str(report))
    runner.phase(3, '导出并回读 FBX（复用已验证结果）')
    workers = min(profile['workers'], len(ids))
    batch_tag = 'batch' if complete else f'gui_exports/batch_{workers}'
    prior = seed_resume_records(ids, workers, batch_tag)
    try:
        runner.command([PYTHON, TOOL / 'export_fbx_batch.py', '--blender', profile['blender'],
                        '--output', OUTPUT, '--workers', workers, '--manifest',
                        REPORTS / f'{prepare_tag}_jobs.json', '--tag', batch_tag])
    finally:
        merge_resume_cache(prior, workers, batch_tag)
    runner.phase(4, '检查文件并生成校验目录')
    verify_result(profile, ids, prepare_tag, batch_tag, run_dir, runner, complete)
    runner.phase(5, '导出与验证完成')
    return {'selected_count': len(ids), 'scope': 'all' if complete else 'selected_only'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--cancel-file', type=Path)
    args = parser.parse_args()
    status_path = log_path = None
    status = {'started': datetime.now().astimezone().isoformat(), 'status': 'running', 'pid': os.getpid()}
    started = time.monotonic()
    code, cancelled, message, details = 1, False, '', {}
    try:
        if args.cancel_file and args.cancel_file.exists():
            raise Cancelled('任务已取消。')
        profile = load_request(args.request)
        configure(profile)
        emit('status', f'正在检查 {profile["name"]} 的路径与工具环境。')
        # Preflight itself is a child Job so Cancel also interrupts version checks.
        runner = Runner(io.StringIO(), args.cancel_file)
        lines = runner.command([PYTHON, TOOL / 'fdk_backend.py', '--preflight-worker', args.request.resolve()])
        check = json.loads(lines[-1])
        runner.check_cancel()
        if args.check_only:
            emit('done', '环境检查通过。', success=True, cancelled=False, status='ready',
                 exit_code=0, log='', preflight=check)
            return 0
        (REPORTS / 'launcher').mkdir(parents=True, exist_ok=True)
        with runtime.export_lock(REPORTS / 'launcher/export.lock'):
            validate_library(profile)
            write_identity(profile)
            run_dir = REPORTS / 'gui_exports' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            run_dir.mkdir(parents=True)
            log_path, status_path = run_dir / 'export.log', run_dir / 'status.json'
            status.update(profile=profile, preflight=check, log=str(log_path))
            save_json(status_path, status)
            emit('status', f'任务已开始，日志：{log_path}', log=str(log_path))
            with log_path.open('w', encoding='utf-8', buffering=1) as log:
                runner = Runner(log, args.cancel_file)
                details = execute(profile, run_dir, runner)
            code, message = 0, '扫描完成。' if profile['action'] == 'scan' else '导出与验证完成。'
    except (Cancelled, KeyboardInterrupt) as exc:
        code, cancelled, message = 130, True, str(exc) or '任务已取消，可再次执行继续。'
    except Exception as exc:
        message = str(exc)
        emit('error', message)
        if log_path:
            with log_path.open('a', encoding='utf-8') as log:
                import traceback
                traceback.print_exc(file=log)
    finally:
        if status_path:
            status.update(status='cancelled' if cancelled else 'complete' if code == 0 else 'failed',
                          exit_code=code, finished=datetime.now().astimezone().isoformat(),
                          seconds=round(time.monotonic() - started, 2), message=message, **details)
            save_json(status_path, status)
    emit('done', message, success=code == 0, cancelled=cancelled,
         status='cancelled' if cancelled else 'complete' if code == 0 else 'failed',
         exit_code=code, log=str(log_path) if log_path else '', **details)
    return code


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--preflight-worker':
        print(json.dumps(preflight(load_request(sys.argv[2])), ensure_ascii=False), flush=True)
    elif len(sys.argv) == 3 and sys.argv[1] == '--recover-worker':
        request = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
        configure(request['profile'])
        result = recover_selected_cache(request['ids'], Path(request['backup']), Runner(io.StringIO(), None))
        save_json(Path(request['report']), result)
    else:
        raise SystemExit(main())
