"""Compatibility retry for nested glTF armatures that Blender's FBX reader rejects.

Uses the same arguments and checks as blender_export.py. Original armature
object transforms become connecting bones before the rigs are joined.
"""
import hashlib
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import blender_export as exporter
REPAIR_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def world_vertices():
    result = {}
    for obj in bpy.context.scene.objects:
        if obj.type != 'MESH':
            continue
        vertices = np.empty(len(obj.data.vertices)*3, dtype=np.float32)
        obj.data.vertices.foreach_get('co', vertices)
        matrix = np.array(obj.matrix_world, dtype=np.float64)
        result[obj.name] = vertices.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
    return result


def merge_nested_armatures():
    changes = []
    while True:
        child = next((obj for obj in bpy.context.scene.objects
                      if obj.type == 'ARMATURE' and obj.parent and obj.parent.type == 'ARMATURE'), None)
        if child is None:
            return changes
        parent = child.parent
        parent_bone = child.parent_bone if child.parent_type == 'BONE' else ''
        child_name = child.name
        child_world = child.matrix_world.copy()
        parent_space = parent.matrix_world.inverted() @ child_world
        roots = [bone.name for bone in child.data.bones if bone.parent is None]
        if set(parent.data.bones.keys()) & set(child.data.bones.keys()):
            raise ValueError('Nested armatures contain overlapping bone names; cannot safely join')
        before = exporter.scene_stats()
        vertex_positions = world_vertices()
        mesh_world_matrices = {obj.name: obj.matrix_world.copy() for obj in bpy.context.scene.objects if obj.type == 'MESH'}
        modifiers_to_retarget = [modifier for obj in bpy.context.scene.objects
                                for modifier in obj.modifiers
                                if modifier.type == 'ARMATURE' and modifier.object == child]
        bone_matrices = {bone.name: obj.matrix_world @ bone.matrix_local
                         for obj in (parent, child) for bone in obj.data.bones}
        bpy.ops.object.select_all(action='DESELECT')
        child.select_set(True)
        parent.select_set(True)
        bpy.context.view_layer.objects.active = parent
        # Joining in object mode preserves existing world-space bone matrices.
        bpy.ops.object.join()
        for modifier in modifiers_to_retarget:
            modifier.object = parent
        bpy.ops.object.mode_set(mode='EDIT')
        try:
            if child_name in parent.data.edit_bones:
                raise ValueError(f'Connecting bone name already exists: {child_name}')
            connector = parent.data.edit_bones.new(child_name)
            connector.head = parent_space.translation
            connector.tail = parent_space @ Vector((0, 0.05, 0))
            connector.matrix = parent_space
            connector.length = 0.05
            if parent_bone:
                connector.parent = parent.data.edit_bones[parent_bone]
            for root_name in roots:
                parent.data.edit_bones[root_name].parent = connector
        finally:
            bpy.ops.object.mode_set(mode='OBJECT')
        # Blender joins bone data in world space, but reparents child meshes
        # without compensating their object transforms. Restore each original
        # world matrix so both mesh and bone bind spaces remain identical.
        for name, matrix in mesh_world_matrices.items():
            bpy.data.objects[name].matrix_world = matrix
        bpy.context.view_layer.update()
        after = exporter.scene_stats()
        after_positions = world_vertices()
        vertex_delta = max(float(np.max(np.abs(vertices-after_positions[name])))
                           for name, vertices in vertex_positions.items())
        if vertex_delta > 1e-4:
            raise ValueError(f'Joining armatures changed world-space vertices by {vertex_delta}')
        bone_delta = max(abs(a-b) for name, matrix in bone_matrices.items()
                         for row1, row2 in zip(matrix, parent.matrix_world @ parent.data.bones[name].matrix_local)
                         for a, b in zip(row1, row2))
        if bone_delta > 1e-4:
            raise ValueError(f'Joining armatures changed bone bind matrices by {bone_delta}')
        if before['vertices'] != after['vertices'] or before['normalized_bone_weight_digest'] != after['normalized_bone_weight_digest']:
            raise ValueError('Joining armatures changed mesh topology or vertex bone weights')
        delta = max(abs(a-b) for left, right in zip(before['bounds'], after['bounds']) for a, b in zip(left, right))
        if delta > 1e-4:
            raise ValueError(f'Joining armatures changed world-space bounds by {delta}')
        changes.append({'nested_armature': child_name, 'merged_into': parent.name,
                        'connecting_bone': child_name, 'parent_bone': parent_bone,
                        'world_bounds_max_delta': delta,
                        'world_vertex_max_delta': vertex_delta, 'bone_bind_matrix_max_delta': bone_delta,
                        'bones_before': before['bones'], 'bones_after': after['bones'],
                        'armatures_before': before['armatures'], 'armatures_after': after['armatures'],
                        'reason': 'Blender FBX importer KeyError on bone-parented nested armatures'})


original_materials = exporter.prepare_fbx_materials


def materials_and_rigs():
    changes = original_materials()
    changes.extend(merge_nested_armatures())
    return changes


exporter.prepare_fbx_materials = materials_and_rigs
original_save = exporter.save_report


def save_with_provenance(path, report):
    for job in report['jobs']:
        job['repair_exporter_sha256'] = REPAIR_HASH
        job['repair_exporter'] = str(Path(__file__).resolve())
    original_save(path, report)


exporter.save_report = save_with_provenance
exporter.main()
