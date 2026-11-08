"""FDK exported-asset browser; retain legacy module/property IDs for saved scenes."""
bl_info = {
    'name': 'FDK 模型与材质预览', 'author': 'Local asset tools',
    'version': (1, 2, 0), 'blender': (4, 5, 0),
    'location': '3D View > Sidebar > FDK',
    'description': 'Browse exported FBX assets, reconstruct material references and create a preview stage',
    'category': 'Import-Export',
}

import csv
from datetime import datetime
import json
from pathlib import Path
import re
import traceback

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from . import materials, stage

OWNER = 'kuro2_preview'
ADDON_ID = __package__
_library_enum_items = []  # Blender requires enum strings to remain alive.


def library_records(context=None):
    """Read live toolkit profiles; installed ZIP has an explicit fallback list."""
    preferences = addon_preferences(context)
    presets = json.loads(Path(__file__).with_name('library_presets.json').read_text(encoding='utf-8'))
    configured = (preferences.library_config if preferences else '') or presets.get('profiles_path', '')
    path = Path(bpy.path.abspath(configured)).resolve() if configured else None
    if path and path.is_file():
        document = json.loads(path.read_text(encoding='utf-8-sig'))
        rows = document['profiles']
        base = path.parent
    else:
        rows = presets['profiles']
        base = Path(__file__).parent
    result = []
    seen = set()
    for row in rows:
        ident = row['id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', ident) or ident in seen:
            raise ValueError('资源库配置包含重复或无效的游戏 ID')
        output = Path(row['output'])
        output = (output if output.is_absolute() else base / output).resolve()
        result.append({'id': ident, 'name': row.get('name', ident), 'output': str(output)})
        seen.add(ident)
    return result


def library_items(settings, context):
    global _library_enum_items
    try:
        rows = library_records(context)
        candidate = [('CUSTOM', '自定义目录', '使用下方资源目录', 0)] + [
            (row['id'], row['name'], row['output'], index + 1) for index, row in enumerate(rows)]
    except (OSError, ValueError, KeyError):
        candidate = [('CUSTOM', '自定义目录', '配置不可读取；可直接选择资源目录', 0)]
    if candidate != _library_enum_items:
        _library_enum_items = candidate
    return _library_enum_items


def get_library(settings):
    try:
        configured = get_asset_root(settings)
        if not configured.strip():
            return 0
        current = Path(bpy.path.abspath(configured)).resolve()
        for index, row in enumerate(library_records()):
            if current == Path(row['output']):
                return index + 1
    except (OSError, ValueError, KeyError):
        pass
    return 0


def set_library(settings, value):
    if value == 0:
        return
    rows = library_records()
    if 0 < value <= len(rows):
        set_asset_root(settings, rows[value - 1]['output'])
        refresh_catalog(bpy.context)


def library_identity(root):
    root = Path(root).resolve()
    metadata = root / 'asset_profile.json'
    if metadata.is_file():
        record = json.loads(metadata.read_text(encoding='utf-8-sig'))
        ident = record.get('id', record.get('profile_id'))
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', ident):
            raise ValueError('asset_profile.json 缺少有效的游戏 ID')
        return {'id': ident, 'name': record.get('name', ident)}
    for row in library_records():
        if root == Path(row['output']):
            return {'id': row['id'], 'name': row['name']}
    return {'id': 'path:' + str(root).casefold(), 'name': root.name}


def collection_identity(collection):
    if collection.get('fdk_profile_id'):
        return {'id': collection['fdk_profile_id'], 'name': collection.get('fdk_profile_name', collection['fdk_profile_id'])}
    # Migrate legacy collections only with their original root's evidence.
    return library_identity(Path(collection['asset_root']))


def validate_rebuild_library(collection, root):
    original = collection_identity(collection)
    chosen = library_identity(root)
    if original['id'] != chosen['id']:
        raise ValueError(f'当前模型属于「{original["name"]}」，所选资源库是「{chosen["name"]}」。请切回对应游戏资源库，避免同名模型贴图混用。')
    return chosen


def stage_identifier(collection):
    if collection.get('fdk_stage_id'):
        return collection['fdk_stage_id']
    # Preserve stages in existing 1.1.x scenes.
    return collection['model_id']

def default_root():
    return ''


def addon_preferences(context=None):
    context = context or bpy.context
    addon = context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon else None


def get_asset_root(settings):
    """Unsaved scenes inherit the installed add-on's persistent directory."""
    override = settings.get('asset_root', '')
    if override:
        return override
    preferences = addon_preferences()
    return preferences.default_asset_root if preferences else default_root()


def set_asset_root(settings, value):
    changed = value != get_asset_root(settings)
    settings['asset_root'] = value
    if changed:
        settings.catalog.clear()
        settings.active_index = 0
        settings.model_id = ''
        settings.search = ''
        settings.status = '资源库已切换，请刷新模型列表'
        settings.warning_summary = ''
        settings.report_text = ''


def selected_root(settings):
    configured = settings.asset_root
    if not configured.strip():
        raise ValueError('尚未选择导出目录，请先选择资源库或导出目录')
    return Path(bpy.path.abspath(configured)).resolve()


def asset_root(context):
    root = selected_root(context.scene.kuro2_settings)
    if not (root/'fbx').is_dir() or not (root/'materials').is_dir() or not (root/'textures').is_dir():
        raise ValueError('请选择同时包含 fbx、materials、textures 文件夹的导出目录')
    return root

def validate_id(model_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', model_id):
        raise ValueError('模型 ID 只允许字母、数字、下划线和连字符')
    return model_id

def catalog_selected(settings, context):
    if 0 <= settings.active_index < len(settings.catalog):
        settings.model_id = settings.catalog[settings.active_index].name

def current_collection(context):
    scene_collections = set(context.scene.collection.children_recursive)
    if context.active_object:
        for collection in context.active_object.users_collection:
            if collection in scene_collections and collection.get('kuro2_owner') == OWNER:
                return collection
    collection = bpy.data.collections.get(context.scene.kuro2_settings.last_collection)
    return collection if collection in scene_collections and collection.get('kuro2_owner') == OWNER else None


def apply_isolation(context, chosen=None):
    settings = context.scene.kuro2_settings
    chosen = chosen or current_collection(context)
    if settings.isolate_preview and chosen is None:
        return
    for collection in context.scene.collection.children_recursive:
        if collection.get('kuro2_owner') == OWNER:
            hidden = settings.isolate_preview and collection != chosen
            collection.hide_render = hidden
            collection.hide_viewport = hidden


def update_isolation(settings, context):
    if context and context.scene:
        apply_isolation(context)

def record_report(context, report, suffix='import'):
    settings = context.scene.kuro2_settings
    report.setdefault('limitations', [
        '预览材质不是原游戏 FDK 着色器的逐像素复现。',
        'FBX 中保留骨架和蒙皮；没有动画轨道，未启用游戏 IK 或动态骨骼行为。',
    ])
    namespace = re.sub(r'[^A-Za-z0-9_-]', '_', report.get('profile_id', 'legacy'))
    report_name = f'FDK_{namespace}_{report["model_id"]}_{suffix}.json'
    text = bpy.data.texts.get(report_name)
    if text is None:
        text = bpy.data.texts.new(report_name)
    text.clear()
    text.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    settings.report_text = text.name
    settings.status = f'{report["model_id"]} · {report.get("meshes", 0)} 网格 · {report.get("bones", 0)} 骨骼'
    settings.warning_summary = f'缺图 {len(report.get("missing_textures", []))} 项 · 未匹配材质 {len(report.get("unmatched_materials", []))} 项'
    return text.name

def report_error(operator, context, action, error):
    """Keep the full traceback available even when Blender only shows its last line."""
    trace = traceback.format_exc()
    print(trace)
    name = 'FDK_Error.txt'
    details = '\n'.join([
        f'Time: {datetime.now().astimezone().isoformat()}',
        f'Add-on: {bl_info["version"]}; Blender: {bpy.app.version_string}',
        f'Language: {context.preferences.view.language}',
        f'Action: {action}',
        f'Model: {context.scene.kuro2_settings.model_id}',
        trace,
    ])
    text = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    text.clear()
    text.write(details)
    settings = context.scene.kuro2_settings
    settings.report_text = name
    settings.status = '操作失败：' + action
    settings.warning_summary = str(error)
    try:
        root = Path(bpy.path.abspath(settings.asset_root)) if settings.asset_root.strip() else None
        if root is not None and (root / 'fbx').is_dir():
            folder = root / 'reports' / 'blender_preview'
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'last_error.txt').write_text(details, encoding='utf-8')
    except OSError:
        pass  # The Blender text record remains available if disk writing fails.
    operator.report({'ERROR'}, f'{error}（详情：{name}）')


def import_model(context, root, model_id, create_stage=True):
    """Public entry point shared by the UI, bootstrap and background tests."""
    validate_id(model_id)
    root = Path(root).resolve()
    identity = library_identity(root)
    fbx = root/'fbx'/f'{model_id}.fbx'
    if not fbx.is_file() or not (root/'materials'/f'{model_id}.json').is_file():
        raise FileNotFoundError(f'找不到该模型的 FBX 或材质记录：{model_id}')
    if context.mode != 'OBJECT':
        raise ValueError('请先退出编辑/姿态模式，再导入另一个模型')
    previous_objects = set(bpy.data.objects)
    collection = None
    try:
        bpy.ops.import_scene.fbx(filepath=str(fbx), use_image_search=False, automatic_bone_orientation=False)
        objects = [obj for obj in bpy.data.objects if obj not in previous_objects]
        collection = bpy.data.collections.new(f'FDK · {identity["name"]} · {model_id}')
        collection['kuro2_owner'] = OWNER
        collection['model_id'] = model_id
        collection['asset_root'] = str(root)
        collection['fdk_profile_id'] = identity['id']
        collection['fdk_profile_name'] = identity['name']
        namespace = re.sub(r'[^A-Za-z0-9_-]', '_', identity['id'])
        collection['fdk_stage_id'] = f'{namespace}__{model_id}'
        context.scene.collection.children.link(collection)
        for obj in objects:
            for old in list(obj.users_collection):
                old.objects.unlink(obj)
            collection.objects.link(obj)
            obj['kuro2_model_id'] = model_id
        settings = context.scene.kuro2_settings
        report = materials.build_materials(objects, root, model_id, mode=settings.material_mode)
        report.update(model_id=model_id, source_fbx=str(fbx), profile_id=identity['id'], profile_name=identity['name'],
                      meshes=sum(obj.type=='MESH' for obj in objects),
                      bones=sum(len(obj.data.bones) for obj in objects if obj.type=='ARMATURE'))
        if create_stage:
            report['stage'] = stage.setup_preview(objects, stage_identifier(collection))
        settings.last_collection = collection.name
        settings.model_id = model_id
        apply_isolation(context, collection)
        record_report(context, report)
        bpy.ops.object.select_all(action='DESELECT')
        for obj in objects:
            obj.select_set(True)
            if obj.type == 'ARMATURE':
                obj.show_in_front = settings.rig_in_front
                obj.data.display_type = 'STICK'
        context.view_layer.objects.active = next((o for o in objects if o.type=='ARMATURE'), objects[0])
        if hasattr(stage, 'frame_viewport'):
            stage.frame_viewport(objects)
        return report, objects
    except Exception:
        # Roll back only objects introduced by this operation; never clear a user's scene.
        for obj in list(bpy.data.objects):
            if obj not in previous_objects:
                bpy.data.objects.remove(obj, do_unlink=True)
        if collection and collection.name in bpy.data.collections:
            bpy.data.collections.remove(collection)
        raise

class KURO2_Preferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID
    default_asset_root: StringProperty(name='默认导出目录', subtype='DIR_PATH', default=default_root(),
                                      description='选择包含 fbx、textures、materials、gltf、reports 的目录')
    library_config: StringProperty(name='游戏配置文件', subtype='FILE_PATH',
        default='',
        description='通用导出工具的 profiles.json；不可访问时使用随插件打包的资源库预设')

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'default_asset_root')
        layout.prop(self, 'library_config')
        layout.label(text='3D 视图 → N 侧栏 → FDK', icon='INFO')
        layout.label(text='保存偏好设置后，新工程自动使用此目录。')
        layout.label(text='模型和贴图从本地导出目录读取，无需网络或启动脚本。')


class KURO2_OT_DefaultRoot(bpy.types.Operator):
    bl_idname = 'kuro2.save_default_root'
    bl_label = '保存为默认目录'
    bl_description = '将当前导出目录保存到插件偏好设置，供下次启动和新工程使用'

    def execute(self, context):
        try:
            root = asset_root(context)
            preferences = addon_preferences(context)
            if preferences is None:
                raise ValueError('请先从偏好设置安装并启用 FDK 插件，再保存默认目录')
            preferences.default_asset_root = str(root)
            bpy.ops.wm.save_userpref()
            self.report({'INFO'}, '默认资源目录已保存')
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class KURO2_AssetItem(bpy.types.PropertyGroup):
    family: StringProperty()
    material_count: IntProperty()

class KURO2_Settings(bpy.types.PropertyGroup):
    library: EnumProperty(name='游戏资源库', items=library_items, get=get_library, set=set_library)
    asset_root: StringProperty(name='导出目录', subtype='DIR_PATH', get=get_asset_root, set=set_asset_root)
    model_id: StringProperty(name='模型 ID', default='chr0000')
    catalog: CollectionProperty(type=KURO2_AssetItem)
    active_index: IntProperty(default=0, update=catalog_selected)
    search: StringProperty(name='搜索', description='按资源 ID 或类别过滤，例如 chr0000、mon、equ')
    preview_stage: BoolProperty(name='同时创建预览灯光与相机', default=True)
    isolate_preview: BoolProperty(name='只显示当前工具导入的模型', description='勾选时隔离当前模型，取消时显示本场景全部工具模型，不影响用户其他集合', default=True, update=update_isolation)
    material_mode: EnumProperty(name='材质预览', items=[('PRINCIPLED','灯光与法线','基础颜色、法线和透明度的常规预览'),('UNLIT','纯色贴图','不受灯光影响，便于检查基色贴图和 UV')], default='PRINCIPLED')
    rig_in_front: BoolProperty(name='骨架透视显示', default=False)
    last_collection: StringProperty()
    status: StringProperty(default='选择导出目录，然后刷新模型列表')
    report_text: StringProperty()
    warning_summary: StringProperty()

class KURO2_UL_Assets(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row()
        row.label(text=item.name, icon='OUTLINER_OB_ARMATURE' if item.family in {'chr','mon','rob'} else 'MESH_DATA')
        row.label(text=item.family)
    def filter_items(self, context, data, propname):
        query = context.scene.kuro2_settings.search.casefold().split()
        flags = [self.bitflag_filter_item if all(q in (item.name+' '+item.family).casefold() for q in query) else 0
                 for item in getattr(data, propname)]
        return flags, []

def refresh_catalog(context):
    settings = context.scene.kuro2_settings
    settings.catalog.clear()
    root = selected_root(settings)
    if not (root / 'reports' / 'model_catalog.csv').exists() and not (root / 'fbx').is_dir():
        settings.model_id = ''
        settings.status = '此资源库尚无导出结果，请先在 FDK 导出工具中扫描并导出'
        return
    chosen = settings.model_id
    catalog = root / 'reports' / 'model_catalog.csv'
    if catalog.is_file():
        with catalog.open(encoding='utf-8-sig', newline='') as source:
            records = list(csv.DictReader(source))
    else:
        records = [{'id':p.stem, 'family':p.stem[:3], 'material_count':0} for p in sorted((root/'fbx').glob('*.fbx'))]
    indexed = len(records)
    records = [row for row in records if (root / 'fbx' / (row['id'] + '.fbx')).is_file()
               and (root / 'materials' / (row['id'] + '.json')).is_file()]
    for row in records:
        item = settings.catalog.add()
        item.name = row['id']; item.family = row.get('family','')
        item.material_count = int(row.get('material_count',0))
    settings.active_index = next((i for i,r in enumerate(records) if r['id']==chosen), 0)
    settings.model_id = records[settings.active_index]['id'] if records else ''
    settings.status = f'{library_identity(root)["name"]} · 可预览 {len(records)} / 索引 {indexed} 个模型'
    if not records:
        settings.warning_summary = '请先在 FDK 导出窗口选择模型并完成导出'


class KURO2_OT_Refresh(bpy.types.Operator):
    bl_idname = 'kuro2.refresh_catalog'
    bl_label = '刷新模型列表'
    def execute(self, context):
        try:
            refresh_catalog(context)
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'},str(exc)); return {'CANCELLED'}

class KURO2_OT_Import(bpy.types.Operator):
    bl_idname = 'kuro2.import_model'
    bl_label = '导入模型并恢复材质'
    bl_options = {'REGISTER','UNDO'}
    def execute(self, context):
        try:
            settings = context.scene.kuro2_settings
            report, _ = import_model(context,asset_root(context),settings.model_id,settings.preview_stage)
            self.report({'INFO'},settings.status)
            return {'FINISHED'}
        except Exception as exc:
            report_error(self, context, '导入模型并恢复材质', exc)
            return {'CANCELLED'}

class KURO2_OT_Rebuild(bpy.types.Operator):
    bl_idname = 'kuro2.rebuild_materials'
    bl_label = '重建当前模型材质'
    bl_options = {'REGISTER','UNDO'}
    def execute(self, context):
        collection = current_collection(context)
        if not collection:
            self.report({'ERROR'},'请先通过此工具导入一个模型'); return {'CANCELLED'}
        try:
            if context.mode != 'OBJECT':
                raise ValueError('请先切回物体模式，再重建材质')
            objects = list(collection.all_objects)
            root = asset_root(context)
            identity = validate_rebuild_library(collection, root)
            report = materials.build_materials(objects,root,collection['model_id'],mode=context.scene.kuro2_settings.material_mode)
            report.update(model_id=collection['model_id'],profile_id=identity['id'],profile_name=identity['name'],meshes=sum(o.type=='MESH' for o in objects),
                          bones=sum(len(o.data.bones) for o in objects if o.type=='ARMATURE'))
            collection['asset_root'] = str(root)
            collection['fdk_profile_id'] = identity['id']
            collection['fdk_profile_name'] = identity['name']
            record_report(context, report, 'materials')
            self.report({'INFO'},'材质已重建；完整记录保存在 Blender 文本数据中')
            return {'FINISHED'}
        except Exception as exc:
            report_error(self, context, '重建当前模型材质', exc)
            return {'CANCELLED'}

class KURO2_OT_Stage(bpy.types.Operator):
    bl_idname = 'kuro2.setup_stage'
    bl_label = '重建预览灯光与相机'
    bl_options = {'REGISTER','UNDO'}
    def execute(self, context):
        collection = current_collection(context)
        if not collection:
            self.report({'ERROR'},'请先导入一个模型');return {'CANCELLED'}
        try:
            stage.setup_preview(list(collection.all_objects),stage_identifier(collection))
            return {'FINISHED'}
        except Exception as exc:
            report_error(self, context, '重建预览灯光与相机', exc)
            return {'CANCELLED'}

class KURO2_OT_Rig(bpy.types.Operator):
    bl_idname = 'kuro2.show_rig'
    bl_label = '切换骨架透视显示'
    bl_options = {'REGISTER','UNDO'}
    def execute(self, context):
        collection = current_collection(context)
        if not collection:
            self.report({'ERROR'},'请先导入一个模型');return {'CANCELLED'}
        settings = context.scene.kuro2_settings
        settings.rig_in_front = not settings.rig_in_front
        count=0
        for obj in collection.all_objects:
            if obj.type=='ARMATURE':
                obj.show_in_front=settings.rig_in_front;obj.data.display_type='STICK';count+=1
        self.report({'INFO'},f'{count} 个骨架；骨骼透视'+('开启' if settings.rig_in_front else '关闭'))
        return {'FINISHED'}

class KURO2_OT_Save(bpy.types.Operator):
    bl_idname='kuro2.save_preview'
    bl_label='保存预览工程…'
    def execute(self,context):
        try:
            collection=current_collection(context)
            root=Path(collection['asset_root']) if collection else asset_root(context)
            folder=root/'previews';folder.mkdir(exist_ok=True)
            model_id=collection['model_id'] if collection else context.scene.kuro2_settings.model_id
            path=folder/f'{validate_id(model_id)}_preview.blend'
            bpy.ops.wm.save_as_mainfile('INVOKE_DEFAULT',filepath=str(path),check_existing=True)
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}

class KURO2_PT_Main(bpy.types.Panel):
    bl_label='FDK 模型预览'
    bl_idname='KURO2_PT_main'
    bl_space_type='VIEW_3D'
    bl_region_type='UI'
    bl_category='FDK'
    def draw(self,context):
        layout=self.layout;settings=context.scene.kuro2_settings
        box=layout.box()
        box.label(text='资源目录',icon='FILE_FOLDER')
        box.prop(settings,'library')
        box.prop(settings,'asset_root',text='')
        box.operator('kuro2.save_default_root',icon='PREFERENCES')
        box.operator('kuro2.refresh_catalog',icon='FILE_REFRESH')
        layout.prop(settings,'search',icon='VIEWZOOM')
        layout.template_list('KURO2_UL_Assets','',settings,'catalog',settings,'active_index',rows=6)
        layout.prop(settings,'model_id')
        layout.prop(settings,'material_mode')
        layout.prop(settings,'preview_stage')
        layout.prop(settings,'isolate_preview')
        row=layout.row();row.scale_y=1.4;row.operator('kuro2.import_model',icon='IMPORT')
        layout.separator()
        collection=current_collection(context)
        label = collection.get('fdk_profile_name', '旧版资源') + ' / ' + collection['model_id'] if collection else '尚未导入'
        layout.label(text='当前模型：'+label)
        layout.operator('kuro2.rebuild_materials',icon='MATERIAL')
        layout.operator('kuro2.setup_stage',icon='LIGHT_AREA')
        layout.operator('kuro2.show_rig',icon='ARMATURE_DATA',depress=settings.rig_in_front)
        layout.operator('kuro2.save_preview',icon='FILE_BLEND')
        box=layout.box();box.label(text=settings.status,icon='INFO')
        box.label(text='材质为预览近似；动画 / IK 未启用')
        if settings.warning_summary:box.label(text=settings.warning_summary)
        if settings.report_text:box.label(text='详细记录：'+settings.report_text)

CLASSES=(KURO2_Preferences,KURO2_OT_DefaultRoot,KURO2_AssetItem,KURO2_Settings,KURO2_UL_Assets,KURO2_OT_Refresh,KURO2_OT_Import,
         KURO2_OT_Rebuild,KURO2_OT_Stage,KURO2_OT_Rig,KURO2_OT_Save,KURO2_PT_Main)

def register():
    for cls in CLASSES:bpy.utils.register_class(cls)
    bpy.types.Scene.kuro2_settings=PointerProperty(type=KURO2_Settings)

def unregister():
    if hasattr(bpy.types.Scene,'kuro2_settings'):del bpy.types.Scene.kuro2_settings
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)
