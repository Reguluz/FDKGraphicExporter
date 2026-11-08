"""Restore preview materials for this export layout without changing model data.

Only the exported base-color and normal bindings have confirmed preview UV
assignments. All other FDK inputs are retained as labelled reference nodes.
They are deliberately not interpreted as PBR channels or engine toon shading.
"""
import json
from pathlib import Path
import re

import bpy

HIDDEN_HELPERS = {'chr_shadow', 'fdk_shadow'}
_REFERENCE_MISSING_CACHE = {}


def _missing_source_textures(root, model_id):
    path = root / 'reports' / 'texture_references.json'
    if not path.is_file():
        return frozenset()  # Older exported libraries may lack the optional audit.
    stat = path.stat()
    signature = (stat.st_size, stat.st_mtime_ns)
    cached = _REFERENCE_MISSING_CACHE.get(str(path))
    if cached is None or cached[0] != signature:
        missing = {}
        for row in _load_json(path):
            if not row.get('source') and row.get('model') and row.get('texture'):
                missing.setdefault(row['model'], set()).add(row['texture'])
        cached = (signature, missing)
        _REFERENCE_MISSING_CACHE[str(path)] = cached
    return cached[1].get(model_id, frozenset())


def _is_shadow_helper(source):
    # Sky 2 names this material simply "shadow". The explicit enabled switch,
    # observed alongside Switch_ShadowOnly=1, is the evidence, not that name.
    shadow_only = any(switch.get('material_switch_name') == 'SWITCH_SHADOWONLY'
                      and switch.get('int2') == -1
                      for switch in source.get('material_switches', []))
    known_legacy = source.get('original_material_name', source['material_name']).casefold() in HIDDEN_HELPERS
    return shadow_only or known_legacy


def _load_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _resolve_material(material, sources, model_id):
    if material is None:
        return None
    if material.get('kuro2_model_id') == model_id:
        index = material.get('kuro2_source_material_index')
        for source in sources:
            if source['_index'] == index:
                return source
    by_name = {s['material_name']: s for s in sources}
    name = material.name
    if material.get('kuro2_source_material') in by_name:
        return by_name[material['kuro2_source_material']]
    # Exact names first: a literal .001 may be a real, distinct source name.
    if name in by_name:
        return by_name[name]
    if name.startswith(model_id + '::'):
        name = name[len(model_id) + 2:]
    while True:
        if name in by_name:
            return by_name[name]
        shorter = re.sub(r'\.\d{3,}$', '', name)
        if shorter == name:
            break
        name = shorter
    aliases = [s for s in sources if s.get('original_material_name') == name]
    return aliases[0] if len(aliases) == 1 else None


def _image(path, colorspace):
    absolute = str(path.resolve())
    for existing in bpy.data.images:
        if (existing.get('kuro2_source_path') == absolute
                and existing.colorspace_settings.name == colorspace):
            # Game DDS channels can be independent data. Straight-alpha color
            # sampling multiplied a face mask into opaque skin RGB. Opacity is
            # wired explicitly below, so keep RGBA channels independent here.
            existing.alpha_mode = 'CHANNEL_PACKED'
            return existing
    result = bpy.data.images.load(absolute, check_existing=False)
    result.name = f'Kuro2::{path.stem}::{colorspace}'
    result.colorspace_settings.name = colorspace
    result.alpha_mode = 'CHANNEL_PACKED'
    result['kuro2_source_path'] = absolute
    return result


def _node(tree, kind, name, x, y):
    node = tree.nodes.new(kind)
    node.name = name
    node.label = name
    node.location = (x, y)
    return node


def _wrapped_uv(tree, socket, image_node, wrap_s, wrap_t, x, y):
    # Follow Blender 4.5's glTF sampler representation: native extension for
    # equal axes, Separate/Math/Combine for mixed wrapping. CLAMP is EXTEND.
    known = {0: 'REPEAT', 1: 'MIRROR', 2: 'EXTEND'}
    if wrap_s == wrap_t and wrap_s in known:
        image_node.extension = known[wrap_s]
        tree.links.new(socket, image_node.inputs['Vector'])
        return
    image_node.extension = 'EXTEND'
    separate = _node(tree, 'ShaderNodeSeparateXYZ', 'Separate sampler UV', x, y)
    combine = _node(tree, 'ShaderNodeCombineXYZ', 'Combine sampler UV', x+360, y)
    tree.links.new(socket, separate.inputs[0])
    for axis, wrap in enumerate((wrap_s, wrap_t)):
        source_socket = separate.outputs[axis]
        if wrap in (0, 1):
            math = _node(tree, 'ShaderNodeMath', f'{"Repeat" if wrap == 0 else "Mirror"} {"UV"[axis]}', x+170, y-axis*130)
            math.operation = 'WRAP' if wrap == 0 else 'PINGPONG'
            if wrap == 0:
                math.inputs[1].default_value = 0
                math.inputs[2].default_value = 1
            else:
                math.inputs[1].default_value = 1
            tree.links.new(source_socket, math.inputs[0])
            source_socket = math.outputs[0]
        tree.links.new(source_socket, combine.inputs[axis])
    tree.links.new(combine.outputs[0], image_node.inputs['Vector'])


def _primary_bindings(document, material):
    bindings = {}
    entries = [('DIFFUSEMAP0', material.get('pbrMetallicRoughness', {}).get('baseColorTexture')),
               ('NORMALMAP0', material.get('normalTexture'))]
    for semantic, info in entries:
        if not info:
            continue
        texture = document['textures'][info['index']]
        image = document['images'][texture['source']]
        transform = info.get('extensions', {}).get('KHR_texture_transform', {})
        bindings[semantic] = {'name': Path(image['uri']).stem,
                              'uv_index': transform.get('texCoord', info.get('texCoord', 0)),
                              'transform': transform}
    return bindings


def _is_baked_flip(transform):
    return (transform.get('offset', [0, 0]) == [0, 0]
            and transform.get('scale', [1, 1]) == [1, -1]
            and transform.get('rotation', 0) == 0)


def _build_one(source, gltf_material, document, semantics, asset_root, model_id, uv_names, color_name, mode, missing_sources=frozenset()):
    material = bpy.data.materials.new(f'{model_id}::{source["material_name"]}')
    material.use_nodes = True
    material['kuro2_model_id'] = model_id
    material['kuro2_source_material'] = source['material_name']
    material['kuro2_source_material_index'] = source['_index']
    material['kuro2_shader'] = source['shader_name']
    material['kuro2_preview_mode'] = mode
    material['kuro2_original_material_json'] = json.dumps({k:v for k,v in source.items() if k != '_index'}, ensure_ascii=False)
    tree = material.node_tree
    tree.nodes.clear()
    output = _node(tree, 'ShaderNodeOutputMaterial', 'Preview output', 980, 100)
    record = {'source_name': source['material_name'], 'material_name': material.name,
              'source_index': source['_index'], 'shader': source['shader_name'],
              'slots': [], 'connected_semantics': [], 'missing_textures': [],
              'warnings': [], 'helper_hidden': False}
    if _is_shadow_helper(source):
        transparent = _node(tree, 'ShaderNodeBsdfTransparent', 'Runtime shadow helper: hidden in preview', 700, 100)
        tree.links.new(transparent.outputs[0], output.inputs['Surface'])
        material.diffuse_color = (0, 0, 0, 0)
        material['kuro2_hidden_helper'] = True
        material.surface_render_method = 'DITHERED'
        record['helper_hidden'] = True
        record['warnings'].append('Known runtime shadow material hidden; Blender preview uses scene lighting shadows.')
    shader = _node(tree, 'ShaderNodeBsdfPrincipled' if mode == 'PRINCIPLED' else 'ShaderNodeEmission',
                   'Standard material preview' if mode == 'PRINCIPLED' else 'Unlit color preview', 700, 100)
    if mode == 'PRINCIPLED':
        pbr = gltf_material.get('pbrMetallicRoughness', {})
        shader.inputs['Metallic'].default_value = pbr.get('metallicFactor', 0)
        shader.inputs['Roughness'].default_value = pbr.get('roughnessFactor', 1)
        shader.inputs['Base Color'].default_value = pbr.get('baseColorFactor', [0.6, 0.6, 0.6, 1])
    else:
        shader.inputs['Color'].default_value = (0.6, 0.6, 0.6, 1)
        shader.inputs['Strength'].default_value = 1
    if not record['helper_hidden']:
        tree.links.new(shader.outputs[0], output.inputs['Surface'])
    material.use_backface_culling = not gltf_material.get('doubleSided', False)
    bindings = _primary_bindings(document, gltf_material)
    baked_uvs = {b['uv_index'] for b in bindings.values() if _is_baked_flip(b['transform'])}
    record['baked_uv_indices'] = sorted(baked_uvs)
    unknown_transforms = [b for b in bindings.values() if b['transform'] and not _is_baked_flip(b['transform'])]
    if unknown_transforms:
        record['warnings'].append('Nonstandard exported UV transform: primary bindings use FBX coordinates; reference nodes require inspection.')
    uv_nodes = {}
    texture_nodes = {}
    for index, texture in enumerate(source['textures']):
        slot = texture['texture_slot']
        semantic = semantics.get(str(slot), 'UNKNOWN')
        name = texture['texture_image_name']
        primary = bindings.get(semantic)
        confirmed = bool(primary and primary['name'] == name)
        if confirmed:
            uv_index = primary['uv_index']
        else:
            # This is the upstream converter convention, not a complete FDK
            # shader disassembly. These inputs remain disconnected references.
            maps = source.get('uv_map_indices', [])
            map_index = slot % 3 if slot < 6 else 0
            uv_index = maps[map_index] if len(maps) > map_index else 0
        uv_name = uv_names[uv_index] if 0 <= uv_index < len(uv_names) else None
        is_data = semantic.startswith(('NORMAL', 'MASK', 'DUDV')) or semantic == 'UNKNOWN'
        color_space = 'Non-Color' if is_data else 'sRGB'
        path = asset_root/'textures'/f'{name}.png'
        y = -index*360
        tex = _node(tree, 'ShaderNodeTexImage', f'Slot {slot}: {semantic} | {name}', -130, y)
        tex.width = 310
        tex['kuro2_slot'] = slot
        tex['kuro2_semantic'] = semantic
        tex['kuro2_relative_path'] = f'textures/{name}.png'
        tex['kuro2_wrap_s'] = texture.get('wrapS', 0)
        tex['kuro2_wrap_t'] = texture.get('wrapT', 0)
        tex['kuro2_uv_index'] = uv_index
        tex['kuro2_reference_only'] = not confirmed
        source_missing = name in missing_sources
        exists = path.is_file() and not source_missing
        if exists:
            tex.image = _image(path, color_space)
        else:
            tex.label += ' [SOURCE MISSING]' if source_missing else ' [MISSING]'
            record['missing_textures'].append(name)
        slot_report = {'slot': slot, 'semantic': semantic, 'texture': name,
                       'path': str(path), 'exists': exists, 'on_disk': path.is_file(),
                       'source_missing': source_missing, 'color_space': color_space,
                       'image_alpha_mode': tex.image.alpha_mode if tex.image else None,
                       'uv_index': uv_index, 'uv_map': uv_name,
                       'uv_resolution': 'exported_gltf_binding' if confirmed else 'reference_only_exporter_convention',
                       'fbx_uv_already_baked': uv_index in baked_uvs,
                       'additional_v_flip': uv_index not in baked_uvs,
                       'wrapS': texture.get('wrapS', 0), 'wrapT': texture.get('wrapT', 0),
                       'connected': False}
        if uv_name:
            key = (uv_index, uv_index not in baked_uvs)
            if key not in uv_nodes:
                uv_node = _node(tree, 'ShaderNodeUVMap', f'{uv_name} (FBX UV)', -1100, y)
                uv_node.uv_map = uv_name
                uv_socket = uv_node.outputs['UV']
                if uv_index not in baked_uvs:
                    # glTF import converted v to 1-v, but only primary texture
                    # UVs were subsequently baked by the exporter. Restore its
                    # Blender-space (u, 2-v) transform for untouched UV layers.
                    mapping = _node(tree, 'ShaderNodeMapping', 'Reference UV: apply unbaked V transform', -920, y)
                    mapping.vector_type = 'POINT'
                    mapping.inputs['Location'].default_value[1] = 2
                    mapping.inputs['Scale'].default_value[1] = -1
                    tree.links.new(uv_socket, mapping.inputs['Vector'])
                    uv_socket = mapping.outputs['Vector']
                uv_nodes[key] = uv_socket
            _wrapped_uv(tree, uv_nodes[key], tex, texture.get('wrapS', 0), texture.get('wrapT', 0), -730, y)
        else:
            record['warnings'].append(f'{name}: UV index {uv_index} is absent; binding not activated.')
        if confirmed and tex.image and uv_name:
            texture_nodes[semantic] = tex
            slot_report['connected'] = semantic == 'DIFFUSEMAP0' or mode == 'PRINCIPLED'
        record['slots'].append(slot_report)

    base = texture_nodes.get('DIFFUSEMAP0')
    normal = texture_nodes.get('NORMALMAP0')
    if base:
        color_socket = base.outputs['Color']
        parameters = {p['shader_name']:p.get('data') for p in source.get('shaders', [])}
        if parameters.get('Switch_VertexColor', 0) and color_name:
            color = _node(tree, 'ShaderNodeVertexColor', 'Enabled source vertex color', 110, -180)
            color.layer_name = color_name
            multiply = _node(tree, 'ShaderNodeMixRGB', 'Base color x vertex color', 350, 100)
            multiply.blend_type = 'MULTIPLY'
            multiply.inputs[0].default_value = 1
            tree.links.new(color_socket, multiply.inputs[1])
            tree.links.new(color.outputs['Color'], multiply.inputs[2])
            color_socket = multiply.outputs[0]
            record['vertex_color_layer'] = color_name
        tree.links.new(color_socket, shader.inputs['Base Color' if mode == 'PRINCIPLED' else 'Color'])
        record['connected_semantics'].append('DIFFUSEMAP0')
        alpha_mode = gltf_material.get('alphaMode', 'OPAQUE')
        if alpha_mode in ('MASK', 'BLEND') and not record['helper_hidden']:
            material.surface_render_method = 'DITHERED'
            alpha_socket = base.outputs['Alpha']
            if alpha_mode == 'MASK':
                cutoff = _node(tree, 'ShaderNodeMath', 'Alpha cutoff', 340, -200)
                cutoff.operation = 'GREATER_THAN'
                cutoff.inputs[1].default_value = gltf_material.get('alphaCutoff', 0.5)
                tree.links.new(alpha_socket, cutoff.inputs[0])
                alpha_socket = cutoff.outputs[0]
            if mode == 'PRINCIPLED':
                tree.links.new(alpha_socket, shader.inputs['Alpha'])
            else:
                transparent = _node(tree, 'ShaderNodeBsdfTransparent', 'Transparent', 700, -180)
                mix = _node(tree, 'ShaderNodeMixShader', 'Unlit opacity', 950, 100)
                tree.links.new(alpha_socket, mix.inputs[0])
                tree.links.new(transparent.outputs[0], mix.inputs[1])
                tree.links.new(shader.outputs[0], mix.inputs[2])
                output.location.x = 1180
                tree.links.new(mix.outputs[0], output.inputs['Surface'])
    if normal and mode == 'PRINCIPLED':
        normal_map = _node(tree, 'ShaderNodeNormalMap', 'Tangent-space normal', 360, -420)
        normal_map.space = 'TANGENT'
        normal_map.uv_map = next(s['uv_map'] for s in record['slots'] if s['semantic'] == 'NORMALMAP0' and s['connected'])
        tree.links.new(normal.outputs['Color'], normal_map.inputs['Color'])
        tree.links.new(normal_map.outputs['Normal'], shader.inputs['Normal'])
        record['connected_semantics'].append('NORMALMAP0')
    record['alpha_mode'] = gltf_material.get('alphaMode', 'OPAQUE')
    record['unconnected_semantics'] = sorted({s['semantic'] for s in record['slots'] if not s['connected']})
    if source.get('misc_flags', {}).get('alpha_blend_mode') not in (None, 0, 1):
        record['warnings'].append('Special FDK blend mode retained in metadata; standard exported alpha used for preview.')
    material['kuro2_material_report'] = json.dumps(record, ensure_ascii=False)
    return material, record


def build_materials(objects, asset_root, model_id, mode='PRINCIPLED'):
    """Rebuild materials on supplied FBX meshes; return a JSON-safe audit report.

    Never modifies the source FBX, mesh vertices, UV values, armatures, or skin
    weights. Material slots are made local when shared mesh data is encountered.
    Repeated calls match source indices, not mutable Blender display names.
    """
    if mode not in {'PRINCIPLED', 'UNLIT'}:
        raise ValueError('Supported preview modes: PRINCIPLED and UNLIT')
    root = Path(asset_root).expanduser().resolve()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', model_id) or model_id in {'.', '..'}:
        raise ValueError('Invalid model resource ID')
    sources = _load_json(root/'materials'/f'{model_id}.json')
    document = _load_json(root/'gltf'/f'{model_id}.gltf')
    slots = _load_json(root/'reports'/'shader_slots.json')
    missing_sources = _missing_source_textures(root, model_id)
    for index, source in enumerate(sources):
        source['_index'] = source.get('source_material_index', source.get('id_referenceonly', index))
    gltf_by_name = {m['name']:m for m in document.get('materials', [])}
    report = {'model_id': model_id, 'asset_root': str(root), 'mode': mode,
              'materials': [], 'assignments': [], 'missing_textures': [],
              'unmatched_materials': [], 'warnings': [], 'bones_untouched': True,
              'scope': 'Preview base color, normal, alpha and source-enabled vertex color. FDK-specific layers retained as disconnected reference nodes.'}
    cache = {}
    old_preview_materials = set()
    for obj in objects:
        if obj.type != 'MESH':
            continue
        if obj.data.users > 1:
            obj.data = obj.data.copy()
        uv_names = tuple(layer.name for layer in obj.data.uv_layers)
        color_name = obj.data.color_attributes[0].name if obj.data.color_attributes else None
        for slot_index, old in enumerate(obj.data.materials):
            source = _resolve_material(old, sources, model_id)
            if source is None:
                report['unmatched_materials'].append({'object': obj.name, 'slot': slot_index, 'material': old.name if old else None})
                continue
            key = (source['_index'], uv_names, color_name)
            if key not in cache:
                gltf_material = gltf_by_name.get(source['material_name'])
                if gltf_material is None:
                    raise ValueError(f'glTF material metadata absent: {source["material_name"]}')
                material, record = _build_one(source, gltf_material, document,
                    slots.get(source['shader_name'], {}), root, model_id, uv_names, color_name, mode, missing_sources)
                cache[key] = material
                report['materials'].append(record)
            obj.data.materials[slot_index] = cache[key]
            report['assignments'].append({'object': obj.name, 'slot': slot_index,
                                          'source_index': source['_index'], 'material': cache[key].name})
            if old and old.get('kuro2_model_id') == model_id:
                old_preview_materials.add(old)
    for old in old_preview_materials:
        if old.users == 0:
            bpy.data.materials.remove(old)
    report['missing_textures'] = sorted({name for m in report['materials'] for name in m['missing_textures']})
    report['warnings'] = list(dict.fromkeys(w for m in report['materials'] for w in m['warnings']))
    if report['unmatched_materials']:
        report['warnings'].append('Some imported material names could not be matched unambiguously; those slots were left unchanged.')
    report['material_count'] = len(report['materials'])
    report['texture_nodes'] = sum(len(m['slots']) for m in report['materials'])
    report['hidden_helper_materials'] = sum(m['helper_hidden'] for m in report['materials'])
    return report
