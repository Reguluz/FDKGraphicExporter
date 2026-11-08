"""Repair owned image cache alpha mode without writing any source PNG."""
import hashlib
import json
from pathlib import Path
import sys
import bpy
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'blender_addon'))
from kuro2_preview.materials import _image
paths=[ROOT.parent/'sky2_export/textures/common_m01_face_c_q.png',
       ROOT.parent/'sky2_export/textures/chr0001_eyes_a.png',
       ROOT.parent/'kuro2_export/textures/chr0000_01_a.png']
checks=[]
for path in paths:
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    image=_image(path,'sRGB')
    image.alpha_mode='STRAIGHT'
    cached=_image(path,'sRGB')
    assert cached==image and cached.alpha_mode=='CHANNEL_PACKED'
    after=hashlib.sha256(path.read_bytes()).hexdigest()
    assert before==after
    checks.append({'path':str(path),'sha256':after,'unchanged':True,'cache_repaired':True})
target=ROOT/'reports/blender_preview/image_cache_validation.json'
target.write_text(json.dumps({'passed':True,'checks':checks},indent=2),encoding='utf-8')
print(target)
