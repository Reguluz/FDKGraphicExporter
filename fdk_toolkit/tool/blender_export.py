"""Batch glTF -> FBX using Blender 4.5, with baked UV transforms and round-trip checks.

Run: blender --background --factory-startup --python blender_export.py --
     --manifest jobs.json --report result.json [--verify] [--preview sample.png]
The manifest is a list, or {"jobs": [{"id": ..., "gltf": ..., "fbx": ...}]}.
Input and output paths are absolute or relative to the manifest directory.
"""
import argparse
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import tempfile
import traceback
from urllib.parse import unquote

import bpy
from bpy_extras.node_shader_utils import PrincipledBSDFWrapper
from mathutils import Euler, Matrix, Vector


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


@contextlib.contextmanager
def import_source(path, result):
    """Remove redundant scene roots in an upstream converter's temporary copy."""
    if path.suffix.lower() != ".gltf":
        yield path
        return
    document = json.loads(path.read_text(encoding="utf-8"))
    nodes = document.get("nodes", [])
    removed = []
    for scene in document.get("scenes", []):
        roots = scene.get("nodes", [])
        descendants = set()
        for root in roots:
            pending = list(nodes[root].get("children", []))
            while pending:
                current = pending.pop()
                if current in descendants:
                    continue
                descendants.add(current)
                pending.extend(nodes[current].get("children", []))
        removed.extend(n for n in roots if n in descendants)
        scene["nodes"] = list(dict.fromkeys(n for n in roots if n not in descendants))
    result["redundant_scene_roots_removed"] = removed
    if not removed:
        yield path
        return
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".gltf", prefix=".fbx_import_", dir=path.parent, delete=False) as handle:
        json.dump(document, handle)
        temporary = Path(handle.name)
    try:
        yield temporary
    finally:
        temporary.unlink(missing_ok=True)


def save_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def source_uv(node, seen=None):
    """Trace imported image/mapping/wrapping nodes to their named UV set."""
    seen = set() if seen is None else seen
    if node in seen:
        return set()
    seen.add(node)
    if node.type == "UVMAP":
        return {node.uv_map or "UVMap"}
    result = set()
    for socket in node.inputs:
        for link in socket.links:
            result.update(source_uv(link.from_node, seen))
    return result


def bake_uv_transforms():
    """Bake each material's POINT Mapping into polygon-loop UVs, exactly once.

    Blender already translates the glTF V flip into its own coordinates. For
    the converter's scale [1,-1], its Mapping is (u, 2-v). Using the imported
    transform avoids guessing orientation and also handles offsets/rotation.
    """
    transforms = {}
    mappings = []
    for material in bpy.data.materials:
        if not material.use_nodes:
            continue
        by_uv = {}
        for node in material.node_tree.nodes:
            if node.type != "MAPPING":
                continue
            if node.vector_type != "POINT" or not node.inputs["Vector"].is_linked:
                raise ValueError(f"Unsupported UV mapping in {material.name}: {node.name}")
            uv_names = source_uv(node)
            if len(uv_names) != 1:
                raise ValueError(f"Ambiguous UV source in {material.name}: {uv_names}")
            uv_name = next(iter(uv_names))
            location = Vector(node.inputs["Location"].default_value)
            rotation = Euler(node.inputs["Rotation"].default_value).to_matrix().to_4x4()
            scale = Matrix.Diagonal(Vector((*node.inputs["Scale"].default_value, 1)))
            matrix = Matrix.Translation(location) @ rotation @ scale
            if uv_name in by_uv and any(abs(a-b) > 1e-6 for r, s in zip(matrix, by_uv[uv_name]) for a, b in zip(r, s)):
                raise ValueError(f"Conflicting texture transforms for {material.name}/{uv_name}")
            by_uv[uv_name] = matrix
            mappings.append((material, node))
        transforms[material.name] = by_uv

    affected_loops = 0
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        # The glTF importer can share mesh data. Make it local before baking.
        if obj.data.users > 1:
            obj.data = obj.data.copy()
        for poly in obj.data.polygons:
            material = obj.data.materials[poly.material_index] if obj.data.materials else None
            if material is None:
                continue
            for uv_name, matrix in transforms.get(material.name, {}).items():
                uv_layer = obj.data.uv_layers.get(uv_name)
                if uv_layer is None:
                    raise ValueError(f"Missing UV set {uv_name} on {obj.name}")
                for loop_index in poly.loop_indices:
                    uv = uv_layer.data[loop_index].uv
                    transformed = matrix @ Vector((uv.x, uv.y, 0, 1))
                    uv_layer.data[loop_index].uv = transformed.xy
                    affected_loops += 1
    for material, node in mappings:
        tree = material.node_tree
        source = node.inputs["Vector"].links[0].from_socket
        targets = [link.to_socket for link in node.outputs["Vector"].links]
        tree.nodes.remove(node)
        for target in targets:
            tree.links.new(source, target)
    return {"mapping_nodes_baked": len(mappings), "uv_loops_transformed": affected_loops}


def patch_fbx_uv_sets():
    """Blender 4.5's FBX exporter leaves UVSet unset (an upstream TODO).

    Add this standard FBX texture property from the material's UVMap node.
    The patch is local to this Blender process, not the installed add-on.
    """
    from io_scene_fbx import export_fbx_bin as exporter
    original = exporter.fbx_data_texture_file_elements

    def with_uv_set(root, blender_tex_key, scene_data):
        original(root, blender_tex_key, scene_data)
        material, socket_name = blender_tex_key
        wrapped = PrincipledBSDFWrapper(material, is_readonly=True)
        texture = getattr(wrapped, socket_name)
        uv_names = source_uv(texture.node_image) if texture.node_image else set()
        if len(uv_names) == 1:
            texture_element = root.elems[-1]
            props = next(e for e in texture_element.elems if e.id == b"Properties70")
            exporter.elem_props_set(props, "p_string", b"UVSet", next(iter(uv_names)))

    exporter.fbx_data_texture_file_elements = with_uv_set


def prepare_fbx_materials():
    """Make base-color textures visible to Blender's limited FBX shader reader.

    glTF vertex-color multiplication becomes a Mix node, which the FBX reader
    cannot follow. Keep the color attributes but bind the image directly;
    shader-specific multiplication is recorded for reconstruction in a DCC.
    """
    changes = []
    for material in bpy.data.materials:
        if not material.use_nodes:
            continue
        tree = material.node_tree
        principled = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        image = next((n for n in tree.nodes if n.type == "TEX_IMAGE" and n.label == "BASE COLOR"), None)
        if principled is None or image is None:
            continue
        color = principled.inputs["Base Color"]
        if not color.is_linked or color.links[0].from_node != image:
            changes.append({"material": material.name,
                            "change": "Bound base-color image directly; vertex color attributes remain but their shader multiplication must be rebuilt in the target DCC."})
            tree.links.new(image.outputs["Color"], color)
        alpha = principled.inputs["Alpha"]
        if alpha.is_linked and alpha.links[0].from_node != image:
            tree.links.new(image.outputs["Alpha"], alpha)
    return changes


def merge_duplicate_skin_groups(source):
    """Repair repeated node IDs in source skin.joints, using slot identity.

    Blender creates one vertex group per joint slot even when several slots
    name the same bone. Its auto-suffixed groups have no corresponding bone
    and are consequently omitted by FBX. Merge by original joint IDs, never
    by guessing that a name suffix should be removed.
    """
    if source.suffix.lower() != ".gltf":
        return []
    gltf = json.loads(source.read_text(encoding="utf-8"))
    repairs = []
    for node in gltf.get("nodes", []):
        if "mesh" not in node or "skin" not in node:
            continue
        joints = gltf["skins"][node["skin"]]["joints"]
        if len(joints) == len(set(joints)):
            continue
        obj = bpy.data.objects.get(node.get("name", ""))
        if obj is None or obj.type != "MESH":
            raise ValueError(f"Cannot identify mesh for duplicate skin slots: {node.get('name')}")
        groups = list(obj.vertex_groups)
        if len(groups) != len(joints):
            raise ValueError(f"Joint-slot/vertex-group mismatch in {obj.name}")
        first_slot = {}
        replacements = {}
        for slot, joint_id in enumerate(joints):
            if joint_id in first_slot:
                replacements[slot] = first_slot[joint_id]
            else:
                first_slot[joint_id] = slot
        touched = 0
        for vertex in obj.data.vertices:
            contributions = [(g.group, g.weight) for g in vertex.groups if g.group in replacements and g.weight > 0]
            if contributions:
                touched += 1
                for slot, weight in contributions:
                    groups[replacements[slot]].add([vertex.index], weight, "ADD")
        mapping = [{"from": groups[slot].name, "to": groups[target].name}
                   for slot, target in replacements.items()]
        for slot in sorted(replacements, reverse=True):
            obj.vertex_groups.remove(groups[slot])
        repairs.append({"mesh": obj.name, "duplicate_slots_merged": len(replacements),
                        "vertices_affected": touched, "mapping": mapping})
    return repairs


def scene_stats():
    meshes = sorted((obj for obj in bpy.context.scene.objects if obj.type == "MESH"), key=lambda obj: obj.name)
    armatures = [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]
    vertices = sum(len(obj.data.vertices) for obj in meshes)
    triangles = 0
    uv_values = []
    coords = []
    weighted_vertices = 0
    total_weight = 0.0
    skin_hash = hashlib.sha256()
    unbound_groups = []
    for obj in meshes:
        obj.data.calc_loop_triangles()
        triangles += len(obj.data.loop_triangles)
        coords.extend(obj.matrix_world @ vert.co for vert in obj.data.vertices)
        group_names = {g.index: g.name for g in obj.vertex_groups}
        armature = next((mod.object for mod in obj.modifiers if mod.type == "ARMATURE"), None)
        unbound = set()
        skin_hash.update(obj.name.encode("utf-8"))
        for vert in obj.data.vertices:
            weights = [group.weight for group in vert.groups if group.weight > 1e-8]
            weighted_vertices += bool(weights)
            total_weight += sum(weights)
            named_weights = sorted((group_names[g.group], g.weight) for g in vert.groups if g.weight > 1e-8)
            weight_sum = sum(w for _, w in named_weights)
            normalized = [(name, round(weight / weight_sum, 7)) for name, weight in named_weights] if weight_sum else []
            skin_hash.update(repr((vert.index, normalized)).encode("utf-8"))
            unbound.update(name for name, _ in named_weights if armature is None or name not in armature.data.bones)
        if unbound:
            unbound_groups.append({"mesh": obj.name, "groups": sorted(unbound)})
        for layer in obj.data.uv_layers:
            uv_values.extend((layer.name, round(item.uv.x, 5), round(item.uv.y, 5)) for item in layer.data)
    texture_bindings = []
    warnings = []
    for material in bpy.data.materials:
        if not material.use_nodes or not material.users:
            continue
        wrapped = PrincipledBSDFWrapper(material, is_readonly=True)
        for slot in ["base_color_texture", "normalmap_texture"]:
            texture = getattr(wrapped, slot)
            if texture and texture.image:
                path = str(Path(bpy.path.abspath(texture.image.filepath)).resolve())
                texture_bindings.append({"material": material.name, "slot": slot, "path": path,
                                         "exists": Path(path).is_file(),
                                         "uv_sets": sorted(source_uv(texture.node_image))})
        if any(n.type == "MATH" and n.operation == "PINGPONG" for n in material.node_tree.nodes):
            warnings.append(f"{material.name}: mirrored-repeat sampling is not represented by standard FBX exporter")
    return {
        "meshes": len(meshes), "vertices": vertices, "triangles": triangles,
        "armatures": len(armatures), "bones": sum(len(obj.data.bones) for obj in armatures),
        "bone_names": sorted(b.name for obj in armatures for b in obj.data.bones),
        "weighted_vertices": weighted_vertices, "total_skin_weight": round(total_weight, 4),
        "normalized_bone_weight_digest": skin_hash.hexdigest(), "unbound_weighted_groups": unbound_groups,
        "uv_loop_count": len(uv_values),
        "uv_digest": hashlib.sha256(repr(sorted(uv_values)).encode()).hexdigest(),
        "bounds": [[min(v[i] for v in coords) for i in range(3)],
                   [max(v[i] for v in coords) for i in range(3)]] if coords else None,
        "texture_bindings": texture_bindings, "warnings": warnings,
    }


def verify_roundtrip(before, after):
    errors = []
    for key in ["meshes", "vertices", "triangles", "armatures", "bones", "bone_names", "weighted_vertices", "normalized_bone_weight_digest", "uv_loop_count", "uv_digest"]:
        if before[key] != after[key]:
            errors.append(f"{key} differs after FBX re-import")
    if abs(before["total_skin_weight"] - after["total_skin_weight"]) > max(0.01, before["vertices"] * 1e-5):
        errors.append("Skin weights differ after FBX re-import")
    if before["bounds"] and after["bounds"]:
        delta = max(abs(a-b) for aa, bb in zip(before["bounds"], after["bounds"]) for a, b in zip(aa, bb))
        size = max(abs(v) for bound in before["bounds"] for v in bound)
        if delta > max(1e-4, size * 1e-5):
            errors.append(f"World-space bounds changed: max delta {delta}")
    def signature(stats):
        return sorted((binding["material"], binding["slot"], binding["path"].lower())
                      for binding in stats["texture_bindings"])
    if signature(before) != signature(after):
        errors.append("Base-color/normal texture references differ after FBX re-import")
    if any(not binding["exists"] for binding in after["texture_bindings"]):
        errors.append("An FBX texture path does not exist")
    return {"passed": not errors, "errors": errors, "after": after,
            "notes": ["Blender FBX importer does not restore per-texture UVSet; UV layers and exported UVSet properties are retained."]}


def render_preview(path, stats):
    if not stats["bounds"]:
        return
    low, high = map(Vector, stats["bounds"])
    center = (low + high) / 2
    size = max(high-low)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 16
    scene.cycles.use_denoising = True
    scene.world = bpy.data.worlds.new("PreviewWorld")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0.18, 0.18, 0.18, 1)
    scene.world.node_tree.nodes["Background"].inputs[1].default_value = 0.7
    bpy.ops.object.camera_add(location=center + Vector((size*0.5, -size*2.5, size*0.12)))
    camera = bpy.context.object
    camera.rotation_euler = (center-camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = size*1.23
    camera.data.clip_end = size*20
    scene.camera = camera
    for offset, energy in [((1, -1.5, 2), 450), ((-1.3, -0.5, 0.7), 220), ((0, 1.5, 1.4), 350)]:
        bpy.ops.object.light_add(type="AREA", location=center+Vector(offset)*size)
        light = bpy.context.object
        light.data.energy = energy*size*size
        light.data.shape = "DISK"
        light.data.size = size*1.3
        light.rotation_euler = (center-light.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.resolution_x = 640
    scene.render.resolution_y = 800
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.render.render(write_still=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--preview", type=Path)
    parser.add_argument("--resume", action="store_true",
                        help="Reuse successful entries from the report only while their FBX still exists and is nonempty")
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:])
    exporter_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    jobs = manifest["jobs"] if isinstance(manifest, dict) else manifest
    prior_jobs = []
    if args.resume and args.report.is_file():
        prior_jobs = json.loads(args.report.read_text(encoding="utf-8"))["jobs"]
    report = {"blender_version": bpy.app.version_string, "verify_requested": args.verify,
              "requested": len(jobs), "completed": 0, "failed": 0, "jobs": prior_jobs,
              "skipped_this_run": 0, "processed_this_run": 0}
    previous = {str(item["id"]): item for item in prior_jobs}
    def update_totals():
        report["completed"] = sum(item.get("status") == "ok" for item in report["jobs"])
        report["failed"] = sum(item.get("status") == "error" for item in report["jobs"])
    update_totals()
    save_report(args.report, report)
    patch_fbx_uv_sets()
    for index, job in enumerate(jobs):
        started = time.monotonic()
        result = {"id": job["id"]}
        try:
            source = (args.manifest.resolve().parent / job["gltf"]).resolve()
            target = (args.manifest.resolve().parent / job["fbx"]).resolve()
            result.update(gltf=str(source), fbx=str(target))
            result["source_signature"] = {
                str(path): {"bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
                for path in [source, source.with_suffix(".bin"), source.with_suffix(".metadata")]
                if path.is_file()
            }
            result["exporter_sha256"] = exporter_hash
            if source.suffix.lower() == ".gltf":
                image_uris = [image.get("uri", "") for image in json.loads(source.read_text(encoding="utf-8")).get("images", [])]
                result["texture_signature"] = {}
                for uri in sorted(set(image_uris)):
                    if not uri or uri.startswith("data:"):
                        continue
                    image_path = (source.parent / unquote(uri)).resolve()
                    result["texture_signature"][str(image_path)] = (
                        {"bytes": image_path.stat().st_size, "mtime_ns": image_path.stat().st_mtime_ns}
                        if image_path.is_file() else None)
            prior = previous.get(str(job["id"]))
            if (args.resume and prior and prior.get("status") == "ok"
                    and prior.get("fbx") == str(target) and target.is_file()
                    and target.stat().st_size > 0
                    and prior.get("source_signature") == result["source_signature"]
                    and prior.get("texture_signature") == result.get("texture_signature")
                    and prior.get("exporter_sha256") == exporter_hash
                    and (not args.verify or prior.get("verification", {}).get("passed"))):
                report["skipped_this_run"] += 1
                print(f"KURO_FBX {index+1}/{len(jobs)} {job['id']} resumed", flush=True)
                continue
            reset_scene()
            with import_source(source, result) as sanitized:
                bpy.ops.import_scene.gltf(filepath=str(sanitized), import_pack_images=False,
                                          bone_heuristic="BLENDER", disable_bone_shape=True,
                                          merge_vertices=False)
            result["uv_bake"] = bake_uv_transforms()
            result["material_simplifications"] = prepare_fbx_materials()
            result["skin_repairs"] = merge_duplicate_skin_groups(source)
            before = scene_stats()
            result["before"] = before
            if before["unbound_weighted_groups"]:
                raise ValueError("Weighted vertex groups lack bones: " + str(before["unbound_weighted_groups"]))
            if not before["meshes"] or not before["triangles"]:
                raise ValueError("Model has no renderable mesh")
            missing = [item["path"] for item in before["texture_bindings"] if not item["exists"]]
            if missing:
                raise ValueError("Missing required images: " + ", ".join(missing))
            if args.preview and index == 0:
                render_preview(args.preview.resolve(), before)
                result["preview"] = str(args.preview.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            bpy.ops.export_scene.fbx(filepath=str(target), use_selection=False,
                object_types={"MESH", "ARMATURE", "EMPTY"}, use_mesh_modifiers=False,
                mesh_smooth_type="FACE", add_leaf_bones=False, bake_anim=False,
                use_armature_deform_only=False, armature_nodetype="NULL",
                path_mode="RELATIVE", embed_textures=False, use_custom_props=True,
                axis_forward="-Z", axis_up="Y")
            result["bytes"] = target.stat().st_size
            if args.verify:
                reset_scene()
                bpy.ops.import_scene.fbx(filepath=str(target), use_image_search=False,
                                          automatic_bone_orientation=False)
                result["verification"] = verify_roundtrip(before, scene_stats())
                if not result["verification"]["passed"]:
                    raise ValueError("; ".join(result["verification"]["errors"]))
            result["status"] = "ok"
        except Exception as exc:
            result["status"] = "error"
            result["error"] = str(exc)
            result["traceback"] = traceback.format_exc()
        result["seconds"] = round(time.monotonic()-started, 3)
        report["jobs"] = [item for item in report["jobs"] if str(item["id"]) != str(job["id"])]
        report["jobs"].append(result)
        report["processed_this_run"] += 1
        previous[str(job["id"])] = result
        update_totals()
        save_report(args.report, report)
        print(f"KURO_FBX {index+1}/{len(jobs)} {job['id']} {result['status']} {result['seconds']}s", flush=True)
    save_report(args.report, report)
    print(f"KURO_FBX_DONE ok={report['completed']} failed={report['failed']}", flush=True)
    if report["failed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
