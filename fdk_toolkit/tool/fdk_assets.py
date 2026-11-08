"""Shared, read-only FDK asset pipeline. Input packaging and shader maps vary per game."""
from __future__ import annotations
import argparse
from collections import Counter
import concurrent.futures
import contextlib
import csv
from functools import lru_cache
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import struct
import sys
import traceback

WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE / 'tools/kuro_mdl_tool'))
import kuro_mdl_export_meshes as mdl
import kuro_mdl_to_basic_gltf as gltf
from PIL import Image
import lz4.frame
from archive_pac import PacArchive
from asset_config import decode_asset_config

PREFIXES = ('chr', 'mon', 'rob', 'equ', 'fis', 'etc', 'fc_chr', 'screen_')
SLOT_MAP = {}
_generate_materials = gltf.generate_materials


def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


@lru_cache(maxsize=48)
def archive(path, size, modified):
    return PacArchive(path)


def read_resource(game, source):
    game = Path(game).resolve()
    if '::' in source:
        packed, virtual = source.split('::', 1)
        path = (game / packed).resolve(); path.relative_to(game)
        stat = path.stat()
        return archive(str(path), stat.st_size, stat.st_mtime_ns).read(virtual)
    path = (game / source).resolve(); path.relative_to(game)
    return path.read_bytes()


def decode(data):
    for _ in range(8):
        if data.startswith(b'\x04\x22\x4d\x18'):
            data = lz4.frame.decompress(data)
        elif data[:4] in (b'C9BA', b'D9BA', b'F9BA'):
            data = mdl.decryptCLE(data)
        else:
            return data
    raise ValueError('资源包装嵌套层数异常。')


def index_resources(profile):
    game = Path(profile['game']); rows = []
    if profile['adapter'] == 'loose_cle':
        for path in sorted(game.rglob('*')):
            if path.is_file() and (path.suffix.lower() in ('.mdl', '.dds', '.mi') or path.name == 'asset_config.json'):
                relative = path.relative_to(game).as_posix()
                rows.append({'source': relative, 'path': relative, 'size': path.stat().st_size})
    elif profile['adapter'] == 'falcom_pac':
        paths = [path for path in sorted(game.rglob('*.pac')) if path.name in
                 ('asset_common_model.pac', 'asset_common_model_info.pac', 'misc.pac') or path.name.startswith('image')]
        if not paths:
            raise ValueError('找不到受支持的 FPAC 资源包。')
        for path in paths:
            stat = path.stat(); pack = archive(str(path.resolve()), stat.st_size, stat.st_mtime_ns)
            relative = path.relative_to(game).as_posix()
            for entry in pack.entries:
                rows.append({'source': relative + '::' + entry.path, 'path': entry.path, 'size': entry.size})
    else:
        raise ValueError('没有为此游戏实现输入适配器。')
    return rows


def priority(source, locale):
    # Declared offline selection policy, not a claim about runtime mount order.
    name = source.casefold()
    localized = name.startswith(locale + '/') or f'image_{locale}.pac::' in name
    common = name.startswith('c/') or 'image.pac::' in name
    return (0 if localized else 1 if common else 2 if name.startswith(('asset/', 'f/')) else 3, name)


def shader_map(profile, resources):
    candidates = [r for r in resources if PurePosixPath(r['path']).name == 'asset_config.json']
    if not candidates:
        raise ValueError('找不到本游戏的 asset_config.json，不能套用其他游戏的材质槽位。')
    source = sorted(candidates, key=lambda r: priority(r['source'], profile['locale']))[0]['source']
    raw = decode(read_resource(profile['game'], source))
    document = decode_asset_config(raw)
    slots = {}
    for shader in document['ShaderInfos']:
        slots[shader['name']] = {str(s['tex_slot']): s['name'].removeprefix('SWITCH_') or 'UNKNOWN'
                                 for s in shader.get('switches', []) if 'tex_slot' in s}
    if not slots:
        raise ValueError('游戏材质配置不包含可识别的 ShaderInfos。')
    output = Path(profile['output'])
    write_json(output / 'reports/shader_slots.json', slots)
    write_json(output / 'reports/shader_slots_source.json', {'source': source, 'sha256': hashlib.sha256(raw).hexdigest(),
               'profile_id': profile['id'], 'shader_count': len(slots)})
    return slots


def sections(data):
    if data[:4] != b'MDL ' or len(data) < 12:
        raise ValueError('解包后的资源不是 Falcom MDL。')
    result, pos = [], 12
    while pos < len(data):
        if data[pos:] == b'\xff\xff\xff\xff': break
        if pos + 8 > len(data): raise ValueError('MDL 段头不完整。')
        kind, size = struct.unpack_from('<II', data, pos)
        if pos + 8 + size > len(data): raise ValueError('MDL 段超出文件范围。')
        count = struct.unpack_from('<I', data, pos+8)[0] if size >= 4 else 0
        result.append({'type': kind, 'size': size, 'offset': pos+8, 'count': count}); pos += size+8
    return result


def inspect_model(task):
    resource, profile = task
    ident = PurePosixPath(resource['path']).stem
    row = {'id': ident, 'source': resource['source']}
    try:
        raw = read_resource(profile['game'], resource['source']); data = decode(raw)
        secs = sections(data); version = mdl.get_kuro_ver(data)
        if version not in profile['mdl_versions']:
            raise ValueError(f'此游戏配置尚未验证 MDL v{version}。')
        mats = mdl.obtain_material_data(data) or []
        has_mesh = any(s['type'] == 1 and s['count'] > 0 for s in secs)
        family = next((p for p in PREFIXES if ident.startswith(p)), ident[:3])
        character = any(m['shader_name'].startswith(('chr_', 'monster')) for m in mats)
        row.update(version=version, wrapper=raw[:4].hex(), sections=secs, materials=mats,
                   has_mesh=has_mesh, selected=has_mesh and (family in PREFIXES or character), family=family,
                   selection_reason='prefix' if family in PREFIXES else 'character_shader' if character else 'excluded',
                   classification='mesh' if has_mesh else 'animation_or_metadata',
                   sha256=hashlib.sha256(raw).hexdigest(), status='ok')
    except Exception as exc:
        row.update(status='error', error=f'{type(exc).__name__}: {exc}')
    return row


def scan(profile):
    output = Path(profile['output']); resources = index_resources(profile)
    slots = shader_map(profile, resources)
    write_json(output / 'reports/resource_index.json', resources)
    grouped = {}
    for item in resources:
        if PurePosixPath(item['path']).suffix.lower() == '.mdl':
            grouped.setdefault(PurePosixPath(item['path']).stem, []).append(item)
    chosen = [sorted(items, key=lambda r: priority(r['source'], profile['locale']))[0] for _, items in sorted(grouped.items())]
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=profile['workers']) as pool:
        for i, row in enumerate(pool.map(inspect_model, ((item, profile) for item in chosen)), 1):
            rows.append(row)
            if i % 500 == 0: print(f'扫描模型 {i}/{len(chosen)}', flush=True)
    images = {}
    for item in resources:
        path = PurePosixPath(item['path'])
        if path.suffix.lower() == '.dds': images.setdefault(path.stem.casefold(), []).append(item['source'])
    refs = []
    for row in rows:
        for material in row.get('materials', []):
            for tex in material['textures']:
                candidates = sorted(images.get(tex['texture_image_name'].casefold(), []), key=lambda p: priority(p, profile['locale']))
                refs.append({'model': row['id'], 'selected': row.get('selected', False),
                    'material': material['material_name'], 'material_index': material['id_referenceonly'], 'shader': material['shader_name'],
                    'slot': tex['texture_slot'], 'semantic': slots.get(material['shader_name'], {}).get(str(tex['texture_slot']), 'UNKNOWN'),
                    'texture': tex['texture_image_name'], 'source': candidates[0] if candidates else '', 'candidates': candidates,
                    'status': 'resolved' if candidates else 'missing', 'wrapS': tex['wrapS'], 'wrapT': tex['wrapT'], 'uv_map_indices': material['uv_map_indices']})
    summary = {'game': profile['game'], 'profile_id': profile['id'], 'adapter': profile['adapter'], 'locale': profile['locale'],
        'mdl_count': len(rows), 'parsed': sum(r['status'] == 'ok' for r in rows),
        'selected_models': sum(r.get('selected', False) for r in rows),
        'mdl_versions': dict(Counter(str(r['version']) for r in rows if r['status'] == 'ok')),
        'families': {p: sum(r.get('selected', False) and r.get('family') == p for r in rows) for p in PREFIXES},
        'selected_texture_references': sum(r['selected'] for r in refs),
        'missing_selected_references': sum(r['selected'] and r['status'] == 'missing' for r in refs),
        'texture_resolution_policy': f'explicit {profile["locale"]} locale, common, remaining candidates; all candidates retained',
        'duplicate_model_sources': {i: [r['source'] for r in items] for i, items in grouped.items() if len(items) > 1}}
    for name, value in [('inventory', rows), ('inventory_summary', summary), ('texture_references', refs),
                        ('missing_textures', [r for r in refs if r['selected'] and r['status'] == 'missing'])]:
        write_json(output / f'reports/{name}.json', value)
    with (output / 'reports/model_catalog.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['id','family','source','material_count','texture_reference_count','unique_texture_count','selection_reason']); writer.writeheader()
        for row in rows:
            if row.get('selected'):
                names = [t['texture_image_name'] for m in row['materials'] for t in m['textures']]
                writer.writerow({**{k: row[k] for k in ('id','family','source','selection_reason')}, 'material_count': len(row['materials']),
                                 'texture_reference_count': len(names), 'unique_texture_count': len(set(names))})
    with (output / 'reports/texture_references.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        if refs:
            writer = csv.DictWriter(stream, fieldnames=list(refs[0])); writer.writeheader(); writer.writerows(refs)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if summary['parsed'] != len(rows): raise ValueError('存在未能解析的模型，请查看 inventory.json；未声明整游戏兼容。')
    if not summary['selected_models']: raise ValueError('没有发现符合角色范围的网格模型。')
    return summary


def generate_materials(document, materials):
    document = _generate_materials(document, materials)
    offset = 0
    for dest, source in zip(document['materials'], materials):
        dest.pop('occlusionTexture', None); dest.pop('normalTexture', None)
        pbr = dest.setdefault('pbrMetallicRoughness', {}); pbr.pop('baseColorTexture', None)
        semantics = SLOT_MAP.get(source['shader_name'], {})
        for i, texture in enumerate(source['textures']):
            slot = texture['texture_slot']; semantic = semantics.get(str(slot), 'UNKNOWN')
            if semantic in ('DIFFUSEMAP0', 'NORMALMAP0'):
                uvs = source['uv_map_indices']; uv = uvs[slot % 3 if slot < 6 else 0] if uvs else 0
                binding = {'index': offset+i, 'texCoord': uv, 'extensions': {'KHR_texture_transform': {'offset': [0,0], 'rotation': 0, 'scale': [1,-1]}}}
                if semantic == 'DIFFUSEMAP0': pbr['baseColorTexture'] = binding
                else: dest['normalTexture'] = binding
        dest['extras'] = {'fdk_shader': source['shader_name'], 'fdk_texture_slots': [
            {**t, 'semantic': semantics.get(str(t['texture_slot']), 'UNKNOWN')} for t in source['textures']]}
        offset += len(source['textures'])
    return document


gltf.generate_materials = generate_materials


def prepare_one(task):
    row, profile, overwrite, missing_names = task
    output = Path(profile['output']); ident = row['id']; target = output / 'gltf' / f'{ident}.gltf'
    result = {'id': ident, 'gltf': str(target), 'fbx': str(output / 'fbx' / f'{ident}.fbx')}
    try:
        if overwrite or not target.exists():
            global SLOT_MAP
            SLOT_MAP = json.loads((output / 'reports/shader_slots.json').read_text(encoding='utf-8'))
            data = decode(read_resource(profile['game'], row['source'])); mats = mdl.obtain_material_data(data)
            names = [m['material_name'] for m in mats]
            for index, material in enumerate(mats):
                material['source_material_index'] = index; material['original_material_name'] = material['material_name']
                if names.count(material['material_name']) > 1: material['material_name'] += f'__material_{index}'
            meshes = mdl.obtain_mesh_data(data, material_struct=mats, trim_for_gpu=False)
            skeleton = gltf.process_skeleton_data(mdl.obtain_skeleton_data(data))
            with contextlib.redirect_stdout(io.StringIO()):
                gltf.write_glTF(str(target.with_suffix('.mdl')), skeleton, meshes, mats, False, write_glb=False, calc_ibm=True)
            doc = json.loads(target.read_text(encoding='utf-8'))
            child_nodes = {n for node in doc.get('nodes', []) for n in node.get('children', [])}
            for scene in doc.get('scenes', []): scene['nodes'] = [n for n in scene['nodes'] if n not in child_nodes]
            for image in doc.get('images', []): image['uri'] = '../textures/' + PurePosixPath(image['uri']).name
            for buffer in doc.get('buffers', []): buffer['uri'] = PurePosixPath(buffer['uri']).name
            for material in doc.get('materials', []):
                missing = []
                for container, key in ((material, 'normalTexture'), (material.get('pbrMetallicRoughness', {}), 'baseColorTexture')):
                    if key in container:
                        image = doc['images'][doc['textures'][container[key]['index']]['source']]
                        if PurePosixPath(image['uri']).stem in missing_names or not (target.parent / image['uri']).exists():
                            missing.append(image['uri']); del container[key]
                if missing: material.setdefault('extras', {})['missing_source_textures'] = missing
            write_json(target, doc); write_json(output / 'materials' / f'{ident}.json', mats)
        result['status'] = 'ok'
    except Exception as exc:
        result.update(status='error', error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())
    return result


def convert_texture(task):
    name, source, profile, overwrite = task
    target = Path(profile['output']) / 'textures' / (name + '.png')
    result = {'texture': name, 'source': source, 'png': str(target)}
    try:
        if not name or PurePosixPath(name).name != name or any(c in name for c in '\\:\0'):
            raise ValueError('贴图名称包含非法路径。')
        if overwrite or not target.exists():
            data = decode(read_resource(profile['game'], source))
            if data[:4] != b'DDS ': raise ValueError(f'不支持的图片封装：{data[:8].hex()}')
            with Image.open(io.BytesIO(data)) as image:
                result.update(width=image.width, height=image.height, mode=image.mode); image.save(target)
        result['status'] = 'ok'
    except Exception as exc: result.update(status='error', error=f'{type(exc).__name__}: {exc}')
    return result


def prepare(profile, ids, tag, overwrite=False):
    output = Path(profile['output']); wanted = set(ids)
    rows = json.loads((output / 'reports/inventory.json').read_text(encoding='utf-8'))
    selected = [r for r in rows if r.get('selected') and r['id'] in wanted]
    if {r['id'] for r in selected} != wanted: raise ValueError('所选模型与当前资源目录不匹配。')
    refs = json.loads((output / 'reports/texture_references.json').read_text(encoding='utf-8'))
    missing = {}
    for ref in refs:
        if not ref['source']: missing.setdefault(ref['model'], set()).add(ref['texture'])
    for folder in ('gltf','fbx','textures','materials','model_info'): (output / folder).mkdir(parents=True, exist_ok=True)
    textures = {r['texture']: r['source'] for r in refs if r['model'] in wanted and r['source']}
    from cache_sources import plan, commit
    print('核对源文件内容与缓存指纹…', flush=True)
    planned = plan(profile, selected, refs, read_resource,
                   json.loads((output / 'reports/shader_slots.json').read_text(encoding='utf-8')))
    write_json(output / f'reports/{tag}_cache_plan.json', planned)
    print(f'模型需转换 {sum(overwrite or r["rebuild"] for r in planned["models"].values())}/{len(selected)}；'
          f'贴图需转换 {sum(overwrite or r["rebuild"] for r in planned["textures"].values())}/{len(textures)}', flush=True)
    texture_results, jobs = [], []
    with concurrent.futures.ProcessPoolExecutor(max_workers=profile['workers']) as pool:
        for i, result in enumerate(pool.map(convert_texture, ((n,s,profile,overwrite or planned['textures'][n]['rebuild']) for n,s in textures.items())), 1):
            texture_results.append(result)
            if i % 25 == 0: print(f'贴图 {i}/{len(textures)}', flush=True)
        if any(r['status'] != 'ok' for r in texture_results):
            write_json(output / f'reports/{tag}_textures.json', texture_results)
            commit(profile, planned, [], [r['texture'] for r in texture_results if r['status'] == 'ok'])
            raise ValueError('部分贴图转换失败，模型阶段未启动；请查看本次 textures 报告。')
        for i, result in enumerate(pool.map(prepare_one, ((r,profile,overwrite or planned['models'][r['id']]['rebuild'],missing.get(r['id'],set())) for r in selected)), 1):
            jobs.append(result); print(f'模型 {i}/{len(selected)} {result["id"]}: {result["status"]}', flush=True)
    for suffix, value in (('textures',texture_results),('prepare',jobs),('jobs',{'jobs':[r for r in jobs if r['status']=='ok']})):
        write_json(output / f'reports/{tag}_{suffix}.json', value)
    commit(profile, planned, [r['id'] for r in jobs if r['status']=='ok'], [r['texture'] for r in texture_results if r['status']=='ok'])
    if any(r['status'] != 'ok' for r in jobs+texture_results): raise ValueError('部分资源转换失败，请查看本次 prepare/textures 报告。')
    return jobs


def export_model_info(profile, ids, report_path):
    output = Path(profile['output']); index_path = output / 'reports/resource_index.json'
    if profile.get('model_info_root'):
        # Known loose-file layout: a selected export must not walk every game file.
        folder = (Path(profile['game']) / profile['model_info_root']).resolve()
        folder.relative_to(Path(profile['game']).resolve())
        resources = [{'source': (path.relative_to(Path(profile['game']))).as_posix(),
                      'path': path.name} for ident in ids
                     for path in [folder / (ident + '.mi')] if path.is_file()]
    elif index_path.is_file(): resources = json.loads(index_path.read_text(encoding='utf-8'))
    else: resources = index_resources(profile)
    by_name = {}
    for item in resources:
        path = PurePosixPath(item['path'])
        if path.suffix.lower() == '.mi': by_name.setdefault(path.stem, []).append(item['source'])
    rows = []
    (output / 'model_info').mkdir(parents=True, exist_ok=True)
    for ident in ids:
        candidates = sorted(by_name.get(ident, []), key=lambda s: priority(s, profile['locale']))
        if not candidates: rows.append({'id':ident,'status':'missing_exact_name'}); continue
        source = candidates[0]; data = decode(read_resource(profile['game'], source))
        try: json.loads(data); suffix = '.json'; kind = 'json'
        except (ValueError, UnicodeError): suffix = '.bin' if data.startswith(b'JSON') else '.txt'; kind = 'binary_json' if suffix=='.bin' else 'text_non_json'
        destination = output / 'model_info' / (ident + suffix)
        if not destination.is_file() or destination.read_bytes() != data: destination.write_bytes(data)
        record = {'id':ident,'source':source,'status':kind,'sha256':hashlib.sha256(data).hexdigest(),'file':str(destination)}
        if kind == 'binary_json':
            try:
                readable = destination.with_suffix('.json')
                write_json(readable, decode_asset_config(data))
                record['readable_json'] = str(readable)
            except (ValueError, UnicodeError) as exc:
                # Auxiliary behavior is not required for mesh export. Preserve
                # the exact binary even if a future JSON opcode is unknown.
                record['readable_json_error'] = str(exc)
        rows.append(record)
    write_json(report_path, {'game': profile['game'], 'profile_id': profile['id'], 'selected_models': len(ids),
        'exact_name_matches': sum(r['status'] != 'missing_exact_name' for r in rows),
        'json_exports': sum(r['status'] == 'json' for r in rows),
        'binary_json_exports': sum(r['status'] == 'binary_json' for r in rows),
        'text_exports': sum(r['status'] == 'text_non_json' for r in rows),
        'missing_exact_name_count': sum(r['status'] == 'missing_exact_name' for r in rows),
        'records':rows,'note':'Exact-name configuration only; bytes preserved, no guessed variant inheritance.'})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--job', type=Path, required=True); args = parser.parse_args()
    request = json.loads(args.job.read_text(encoding='utf-8-sig')); profile = request['profile']
    if request['action'] == 'scan': scan(profile)
    elif request['action'] == 'prepare': prepare(profile, request['ids'], request['tag'], request.get('overwrite',False))
    elif request['action'] == 'model_info': export_model_info(profile, request['ids'], Path(request['report']))
    else: raise ValueError('Unknown worker action')
