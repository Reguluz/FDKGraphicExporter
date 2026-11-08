"""Render real exported hair material at alpha-zero/alpha-one texels."""
import json
from pathlib import Path
import sys
import bpy
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
KURO=ROOT.parent/'kuro2_export'
DEST=ROOT/'reports/blender_preview/packed_alpha_opacity'
DEST.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(ROOT/'blender_addon'))
from kuro2_preview import materials
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=str(KURO/'fbx/chr0000.fbx'),use_image_search=False)
objects=list(bpy.context.scene.objects)
materials.build_materials(objects,KURO,'chr0000',mode='UNLIT')
hair=next(m for o in objects if o.type=='MESH' for m in o.data.materials if m.get('kuro2_source_material')=='hair_01')
base=next(n for n in hair.node_tree.nodes if n.type=='TEX_IMAGE' and n.get('kuro2_semantic')=='DIFFUSEMAP0')
assert base.image.alpha_mode=='CHANNEL_PACKED'
width,height=base.image.size
alpha=np.array(base.image.pixels[:]).reshape(height,width,4)[:,:,3]
coords=[]
for target in (0,1):
    candidates=np.argwhere((alpha==target))
    y,x=next((int(y),int(x)) for y,x in candidates if 3<=y<height-3 and 3<=x<width-3
             and np.all(alpha[y-2:y+3,x-2:x+3]==target))
    coords.append((x,y))
for obj in objects:obj.hide_render=True
for index,(x,y) in enumerate(coords):
    bpy.ops.mesh.primitive_plane_add(size=1,location=(index,0,0))
    material=hair.copy();tree=material.node_tree
    base=next(n for n in tree.nodes if n.type=='TEX_IMAGE' and n.get('kuro2_semantic')=='DIFFUSEMAP0')
    uv=tree.nodes.new('ShaderNodeCombineXYZ')
    uv.inputs[0].default_value=(x+.5)/width;uv.inputs[1].default_value=(y+.5)/height
    tree.links.new(uv.outputs[0],base.inputs['Vector'])
    bpy.context.object.data.materials.append(material)
bpy.ops.object.camera_add(location=(.5,0,5))
bpy.context.scene.camera=bpy.context.object;bpy.context.object.data.type='ORTHO';bpy.context.object.data.ortho_scale=2
scene=bpy.context.scene;scene.render.engine='BLENDER_EEVEE_NEXT';scene.render.film_transparent=True
scene.render.resolution_x=64;scene.render.resolution_y=32;scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGBA'
scene.render.filepath=str(DEST/'opacity.png');bpy.ops.render.render(write_still=True)
image=bpy.data.images.load(scene.render.filepath,check_existing=False)
values=[image.pixels[(16*64+x)*4+3] for x in (16,48)]
assert values[0]<.01 and values[1]>.99,values
(DEST/'result.json').write_text(json.dumps({'passed':True,'material':'chr0000/hair_01','alpha_mode':'CHANNEL_PACKED',
    'source_texels':coords,'rendered_alpha':values,'explicit_alpha_mask_preserved':True},indent=2),encoding='utf-8')
print(values)
