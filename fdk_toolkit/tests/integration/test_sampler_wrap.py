"""Render constant Shader Math Wrap inputs to verify actual Blender behavior."""
from pathlib import Path
import json
import bpy

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / 'reports/blender_preview/sampler_wrap'
DEST.mkdir(parents=True, exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
cases = [(value, upper, lower) for value in (1.2, 0.2, -0.2)
         for upper, lower in ((0,1),(1,0))]
for index, (value, upper, lower) in enumerate(cases):
    bpy.ops.mesh.primitive_plane_add(size=1, location=(index,0,0))
    material = bpy.data.materials.new(str(index)); material.use_nodes = True
    tree=material.node_tree; tree.nodes.clear()
    output=tree.nodes.new('ShaderNodeOutputMaterial')
    emission=tree.nodes.new('ShaderNodeEmission')
    math=tree.nodes.new('ShaderNodeMath'); math.operation='WRAP'
    math.inputs[0].default_value=value; math.inputs[1].default_value=upper; math.inputs[2].default_value=lower
    tree.links.new(math.outputs[0],emission.inputs['Color'])
    tree.links.new(emission.outputs[0],output.inputs['Surface'])
    bpy.context.object.data.materials.append(material)
bpy.ops.object.camera_add(location=(2.5,0,5))
camera=bpy.context.object; camera.data.type='ORTHO'; camera.data.ortho_scale=6
bpy.context.scene.camera=camera
scene=bpy.context.scene; scene.render.engine='BLENDER_EEVEE_NEXT'
scene.render.resolution_x=192;scene.render.resolution_y=32;scene.render.resolution_percentage=100
scene.view_settings.view_transform='Raw'
scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGB'
scene.render.filepath=str(DEST/'wrap_values.png')
bpy.ops.render.render(write_still=True)
image=bpy.data.images.load(scene.render.filepath,check_existing=False)
values=[]
for index, (value, upper, lower) in enumerate(cases):
    offset=(16*image.size[0]+index*32+16)*4
    values.append({'value':value,'input1':upper,'input2':lower,'pixel':list(image.pixels[offset:offset+3])})
(DEST/'result.json').write_text(json.dumps(values,indent=2),encoding='utf-8')
print(json.dumps(values))
