"""Non-destructive, size-aware preview lighting for exported Kuro II models.

Blender 4.5/5.2 API. This module only replaces objects carrying its own stage tag;
imported meshes, transforms, armatures and user-owned datablocks are not edited.
"""
from __future__ import annotations

import bpy
from mathutils import Vector

TAG = "kuro2_preview_stage"
OWNER = "kuro2_stage_model_id"


def _ensure_surface_shader(tree, shader_type, output_type):
    """Find owned-stage nodes by stable RNA type, never translated display name.

    A user can rename or remove nodes between stage rebuilds. Restore the
    required shader/output connection only in our own stage datablocks.
    """
    outputs = [node for node in tree.nodes if node.bl_idname == output_type]
    output = next((node for node in outputs if node.is_active_output), None)
    if output is None:
        output = outputs[0] if outputs else tree.nodes.new(output_type)
    surface = output.inputs['Surface']
    shader = next((link.from_node for link in surface.links
                   if link.from_node.bl_idname == shader_type), None)
    if shader is None:
        shader = next((node for node in tree.nodes if node.bl_idname == shader_type), None)
    if shader is None:
        shader = tree.nodes.new(shader_type)
    if not any(link.from_node == shader for link in surface.links):
        tree.links.new(shader.outputs[0], surface)
    output.is_active_output = True
    return shader


def _is_visible_surface(material):
    if material is None:
        return True
    if material.get("kuro2_hidden_helper"):
        return False
    name = material.name.lower()
    if "chr_shadow" in name:
        return False
    if material.use_nodes:
        shader = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if shader is not None:
            alpha = shader.inputs.get("Alpha")
            if alpha is not None and not alpha.is_linked and alpha.default_value <= 0.001:
                return False
    return True


def model_bounds(objects):
    """World-space bounds of visible model meshes, excluding shadow-only helpers."""
    candidates = [obj for obj in objects if obj.type == "MESH" and not obj.get(TAG)
                  and not obj.hide_render and len(obj.data.vertices)]
    meshes = [obj for obj in candidates if not obj.data.materials
              or any(_is_visible_surface(mat) for mat in obj.data.materials)]
    if not meshes:
        raise ValueError("The selected model has no visible mesh to frame.")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    corners = []
    for obj in meshes:
        evaluated = obj.evaluated_get(depsgraph)
        corners.extend(evaluated.matrix_world @ Vector(corner) for corner in evaluated.bound_box)
    low = Vector(tuple(min(v[axis] for v in corners) for axis in range(3)))
    high = Vector(tuple(max(v[axis] for v in corners) for axis in range(3)))
    return low, high, meshes, [obj.name for obj in candidates if obj not in meshes]


def _owned_object(collection, name, data, model_id):
    obj = bpy.data.objects.new(name, data)
    obj[TAG] = True
    obj[OWNER] = model_id
    collection.objects.link(obj)
    return obj


def _aim(obj, target):
    obj.rotation_euler = (target - obj.location).to_track_quat("-Z", "Y").to_euler()


def _stage_collection(scene, model_id):
    collection = next((c for c in scene.collection.children if c.get(TAG)
                       and c.get(OWNER) == model_id), None)
    if collection is None:
        collection = bpy.data.collections.new(f"Kuro Preview Stage · {model_id}")
        collection[TAG] = True
        collection[OWNER] = model_id
    if collection.name not in scene.collection.children:
        scene.collection.children.link(collection)
    for obj in list(collection.objects):
        if obj.get(TAG) and obj.get(OWNER) == model_id:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            # Remove only now-unused geometry/light/camera data created by this module.
            if data is not None and data.users == 0 and data.get(TAG):
                if isinstance(data, bpy.types.Mesh):
                    bpy.data.meshes.remove(data)
                elif isinstance(data, bpy.types.Camera):
                    bpy.data.cameras.remove(data)
                elif isinstance(data, bpy.types.Light):
                    bpy.data.lights.remove(data)
    for other in scene.collection.children:
        if other.get(TAG):
            inactive = other != collection
            other.hide_render = inactive
            other.hide_viewport = inactive
    collection.hide_render = False
    collection.hide_viewport = False
    return collection


def _world(scene, model_id):
    previous = scene.world.name if scene.world else None
    world = next((w for w in bpy.data.worlds if w.get(TAG) and w.get(OWNER) == model_id
                  and w.get("kuro2_stage_scene") == scene.name), None)
    if world is None:
        world = bpy.data.worlds.new(f"Kuro Preview World · {model_id}")
        world[TAG] = True
        world[OWNER] = model_id
        world["kuro2_stage_scene"] = scene.name
    world.use_nodes = True
    background = _ensure_surface_shader(world.node_tree, 'ShaderNodeBackground', 'ShaderNodeOutputWorld')
    background.inputs["Color"].default_value = (0.11, 0.13, 0.17, 1.0)
    background.inputs["Strength"].default_value = 0.35
    if scene.world and not scene.world.get(TAG):
        scene["kuro2_previous_world"] = scene.world.name
    scene.world = world
    return previous, world.name


def setup_preview(objects, model_id: str) -> dict:
    """Create/update an EEVEE stage around supplied models without moving them.

    Changes active scene camera, world assignment and preview render settings.
    Existing world and camera datablocks remain intact, and their names are
    recorded on the scene. The function does not render or save the user's file.
    """
    objects = list(objects)
    if not model_id:
        raise ValueError("A model id is required for the preview stage.")
    scene = bpy.context.scene
    low, high, meshes, excluded = model_bounds(objects)
    center = (low + high) * 0.5
    dimensions = high - low
    size = max(max(dimensions), 0.01)
    collection = _stage_collection(scene, model_id)

    # Models exported through this workflow face -Y in Blender's Z-up space.
    # A slight lateral offset reveals depth while keeping faces readable.
    camera_data = bpy.data.cameras.new(f"Kuro Preview Camera · {model_id}")
    camera_data[TAG] = True
    camera = _owned_object(collection, camera_data.name, camera_data, model_id)
    camera.location = center + Vector((size * 0.18, -size * 3.2, size * 0.10))
    _aim(camera, center)
    camera_data.type = "ORTHO"
    rotation = camera.rotation_euler.to_matrix().transposed()
    projected = [rotation @ (Vector((x, y, z)) - center)
                 for x in (low.x, high.x) for y in (low.y, high.y) for z in (low.z, high.z)]
    width = max(v.x for v in projected) - min(v.x for v in projected)
    height = max(v.y for v in projected) - min(v.y for v in projected)
    camera_data.ortho_scale = max(width, height, size * 0.25) * 1.20
    camera_data.clip_start = max(size / 10000, 0.0001)
    camera_data.clip_end = max(size * 30, 100)
    if scene.camera and not scene.camera.get(TAG):
        scene["kuro2_previous_camera"] = scene.camera.name
    scene.camera = camera

    floor_mesh = bpy.data.meshes.new(f"Kuro Preview Floor · {model_id}")
    floor_mesh[TAG] = True
    radius = size * 5
    floor_mesh.from_pydata([(-radius, -radius, 0), (radius, -radius, 0),
                           (radius, radius, 0), (-radius, radius, 0)], [], [(0, 1, 2, 3)])
    floor_mesh.update()
    floor = _owned_object(collection, floor_mesh.name, floor_mesh, model_id)
    floor.location = (center.x, center.y, low.z - size * 0.002)
    floor_material = next((m for m in bpy.data.materials if m.get(TAG)
                           and m.get(OWNER) == model_id and m.get("kuro2_role") == "floor"), None)
    if floor_material is None:
        floor_material = bpy.data.materials.new(f"Kuro Preview Floor Material · {model_id}")
        floor_material[TAG] = True
        floor_material[OWNER] = model_id
        floor_material["kuro2_role"] = "floor"
    floor_material.use_nodes = True
    shader = _ensure_surface_shader(floor_material.node_tree, 'ShaderNodeBsdfPrincipled', 'ShaderNodeOutputMaterial')
    shader.inputs["Base Color"].default_value = (0.055, 0.065, 0.085, 1.0)
    shader.inputs["Roughness"].default_value = 0.85
    floor.data.materials.append(floor_material)

    # Energy and emitter area scale with the model, so a tiny accessory and a
    # full-size character receive comparable illumination.
    light_parameters = [
        ("Key", (-1.0, -1.7, 1.8), 430, 1.25, (1.0, 0.92, 0.82)),
        ("Fill", (1.4, -0.6, 0.65), 220, 1.4, (0.77, 0.86, 1.0)),
        ("Rim", (0.6, 1.2, 1.65), 520, 1.0, (0.83, 0.91, 1.0)),
    ]
    lights = []
    for role, offset, energy, area_size, color in light_parameters:
        data = bpy.data.lights.new(f"Kuro Preview {role} · {model_id}", "AREA")
        data[TAG] = True
        light = _owned_object(collection, data.name, data, model_id)
        light.location = center + Vector(offset) * size
        data.energy = energy * size * size
        data.shape = "DISK"
        data.size = size * area_size
        data.color = color
        _aim(light, center)
        lights.append(light.name)

    previous_world, world = _world(scene, model_id)
    scene.render.engine = "BLENDER_EEVEE" if bpy.app.version >= (5, 0, 0) else "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = 1024
    scene.render.resolution_y = 1024
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = 1.0
    scene.render.pixel_aspect_y = 1.0
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    scene.view_settings.use_curve_mapping = False
    return {
        "model_id": model_id,
        "stage_collection": collection.name,
        "camera": camera.name,
        "lights": lights,
        "world": world,
        "previous_world": previous_world,
        "bounds": [list(low), list(high)],
        "framed_meshes": [obj.name for obj in meshes],
        "excluded_shadow_meshes": excluded,
        "resolution": [1024, 1024],
        "engine": scene.render.engine,
        "model_transforms_changed": False,
    }


def frame_viewport(objects) -> dict:
    """Frame the supplied model in available 3D views; harmless in background mode."""
    low, high, meshes, excluded = model_bounds(list(objects))
    center = (low + high) / 2
    size = max(max(high - low), 0.01)
    direction = Vector((0.18, -3.2, 0.10))
    rotation = (-direction).to_track_quat("-Z", "Y")
    count = 0
    manager = bpy.context.window_manager
    for window in manager.windows if manager else []:
        if window.scene != bpy.context.scene:
            continue
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            space = area.spaces.active
            if space.region_3d is None:
                continue
            region = space.region_3d
            region.view_location = center
            region.view_rotation = rotation
            region.view_distance = size * 1.45
            region.view_perspective = "ORTHO"
            space.clip_start = max(size / 10000, 0.0001)
            space.clip_end = max(size * 30, 100)
            space.shading.type = "MATERIAL"
            space.shading.use_scene_world = True
            space.shading.use_scene_lights = True
            area.tag_redraw()
            count += 1
    return {"views_framed": count, "framed_meshes": [obj.name for obj in meshes]}
