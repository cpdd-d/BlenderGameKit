"""算子:添加节点、导出、预览、打开目录。"""

import json
import os
import subprocess
import sys

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from . import asset_export, codegen, exporter, gk_log, npr_preview, project
from . import properties as props
from .nodes import nodes as node_lib

# 记录上一次导出结果，供面板与"打开结果"使用
LAST_EXPORT = {}
# 最近用过的节点类型(bl_idname),最近的在最前面。
# 蓝图里加节点是高频操作,菜单里给个"最近使用"能省不少翻分类的时间。
RECENT_NODES = []
_RECENT_LIMIT = 12

# 存进 .blend 的键名。用场景的自定义属性存,这样关掉 Blender 再打开还在 ——
# 之前 LAST_EXPORT 只在内存里,重开之后"打开导出目录"就找不到地方了。
_PERSIST_KEY = "gk_last_export"


def remember_node(node_type):
    """把刚用过的节点记到最近列表最前面。"""
    if not node_type:
        return
    if node_type in RECENT_NODES:
        RECENT_NODES.remove(node_type)
    RECENT_NODES.insert(0, node_type)
    del RECENT_NODES[_RECENT_LIMIT:]


def remember_export(scene, result):
    """把导出结果同时记到内存和 .blend 里。

    .blend 是持久层:关掉再打开,面板上还能看到"上次导出: xxx.gd",
    『打开导出目录』也还能用。之前这些只存在内存里,重开就没了。
    """
    LAST_EXPORT.clear()
    LAST_EXPORT.update(result)
    if scene is None:
        return
    try:
        # 只存必要且可序列化的字段。场景里塞太多东西会让 .blend 变大。
        scene[_PERSIST_KEY] = json.dumps({
            "directory": result.get("directory", ""),
            "script_name": result.get("script_name", ""),
            "lines": int(result.get("lines", 0)),
            "warnings": list(result.get("warnings", []))[:8],
        }, ensure_ascii=False)
    except Exception:
        pass


def restore_export(scene):
    """从 .blend 里恢复上次导出信息(打开文件时调用)。"""
    if scene is None:
        return
    raw = scene.get(_PERSIST_KEY)
    if not raw:
        return
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return
    if not isinstance(data, dict):
        return
    LAST_EXPORT.clear()
    LAST_EXPORT.update(data)


def _report(operator, level, message):
    """统一上报。INFO 也会打印到控制台，方便排查。"""
    if level == "ERROR":
        gk_log.error(message)
    elif level == "WARNING":
        gk_log.warn(message)
    else:
        gk_log.info(message)
    operator.report({level}, message)


def _resolve_project(operator, context, create=False):
    """取当前 .blend 绑定的 Godot 项目目录。

    返回 (目录, 是否成功)。**失败时已经上报了错误**,调用方直接 return 即可。

    这里刻意不提供"兜底目录" —— 早期版本会静默写到家目录下的
    BlenderGameKit_Export,结果是用户根本不知道文件去哪了。
    现在推不出目录就明确报错,让用户先保存 .blend。

    create=True 时:缺项目骨架 / 缺子目录都会自动补齐。
    缺子目录是所有导出都会失败的原因(比如 .blend 放在别人的 Godot 项目里,
    而那个项目没有 scripts/),而补齐是纯增量操作,不会覆盖任何已有文件。
    """
    directory, reason = project.resolve(context.scene, adopt=create)
    if not directory:
        _report(operator, "ERROR", reason or "推不出项目目录")
        return None, False

    if not os.path.isdir(directory) or not project.is_project(directory):
        if not create:
            _report(operator, "ERROR",
                    f"Godot 项目还没建出来: {directory}\n"
                    f"请先在侧栏『项目』里点『创建 Godot 项目』")
            return None, False
        try:
            project.create_project(directory)
        except OSError as exc:
            _report(operator, "ERROR", f"创建项目失败: {exc}")
            return None, False

    return directory, True


def _tree_of(operator, context):
    tree = props.current_tree(context)
    if tree is not None:
        return tree

    # 退路:从搜索弹窗里调用时 context 没有 space_data,
    # current_tree() 拿不到东西,于是报"请先打开节点树" —— 但用户明明
    # 就在节点编辑器里,只是走了搜索弹窗那条路。所以这里退回"当前唯一的
    # 那棵游戏逻辑树"。
    #
    # 这个坑值得记:Blender 的 invoke_search_popup 会把 context 收窄成
    # 只有 window_manager,任何依赖 space_data 的代码在里面都会静默失效。
    candidates = [group for group in bpy.data.node_groups
                  if getattr(group, "bl_idname", "") == "GKGameLogicTree"]
    active = getattr(getattr(context, "space_data", None), "edit_tree", None)
    if active is not None and active in candidates:
        return active
    if len(candidates) == 1:
        return candidates[0]

    if not candidates:
        _report(operator, "ERROR", "请先在节点编辑器里新建一棵『游戏逻辑』节点树")
    else:
        _report(operator, "ERROR",
                f"有 {len(candidates)} 棵游戏逻辑节点树,无法确定用哪棵。"
                f"请在节点编辑器里打开其中一棵再操作")
    return None


def _open_in_text_editor(context, code, name):
    """把生成的代码放进 Blender 文本编辑器，方便立刻看。"""
    text = bpy.data.texts.get(name)
    if text is None:
        text = bpy.data.texts.new(name)
    text.clear()
    text.write(code)
    try:
        text.use_fake_user = True
    except Exception:
        pass
    return text


@bpy.app.handlers.persistent
def _on_load_post(_dummy):
    """打开 .blend 之后,把上次导出信息恢复出来。

    为什么需要:LAST_EXPORT 原来只在内存里,关掉 Blender 再打开就没了 ——
    面板上"上次导出"消失、「打开导出目录」也不知道该开哪。
    那些信息本来就该跟着文件走,所以现在是写进场景自定义属性的。

    @persistent 是必须的:不带它的话,加载新文件时 Blender 会把
    这个 handler 一并清掉,只能生效一次。
    """
    try:
        scene = getattr(bpy.context, "scene", None)
        restore_export(scene)
    except Exception:
        pass
    # 顺手把材质参数的中文显示名补齐。
    #
    # 必须**在这里**做,不能只在插件注册时做:注册发生在打开文件**之前**,
    # 而 open_mainfile 会把当时内存里的数据全换掉 —— 注册时补的名字等于白补。
    # (实测踩过:插件注册时回填了一次,打开文件后材质里 _RNA_UI 又是空的。)
    try:
        npr_module = sys.modules.get(f"{__package__}.npr_preview")
        if npr_module is not None:
            npr_module.backfill_display_names()
    except Exception:
        pass


def _register_handlers():
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def _unregister_handlers():
    try:
        bpy.app.handlers.load_post.remove(_on_load_post)
    except (ValueError, AttributeError):
        pass


class GK_OT_AddNode(bpy.types.Operator):
    """按分类添加一个 GK 节点。"""

    bl_idname = "gk.add_node"
    bl_label = "添加游戏逻辑节点"
    bl_description = "在鼠标位置添加一个节点"
    bl_options = {"REGISTER", "UNDO"}

    node_type: StringProperty(name="节点类型", default="")
    link_from: StringProperty(name="从哪个节点连线", default="")

    def execute(self, context):
        tree = _tree_of(self, context)
        if tree is None:
            return {"CANCELLED"}
        cls = node_lib.ALL_CLASSES and None
        for candidate in node_lib.ALL_CLASSES:
            if candidate.bl_idname == self.node_type:
                cls = candidate
                break
        if cls is None:
            _report(self, "ERROR", f"未知节点类型: {self.node_type}")
            return {"CANCELLED"}

        location = context.space_data.cursor_location if context.space_data else (0.0, 0.0)
        node = tree.nodes.new(cls.bl_idname)
        node.location = location
        node.select = True
        for other in tree.nodes:
            if other is not node:
                other.select = False
        tree.nodes.active = node

        if self.link_from:
            source = tree.nodes.get(self.link_from)
            if source is not None:
                _auto_link(tree, source, node)
        remember_node(cls.bl_idname)
        _report(self, "INFO", f"已添加节点: {node.bl_label}")
        return {"FINISHED"}

    def invoke(self, context, event):
        # 分类菜单会传 node_type;节点搜索菜单不传，走弹窗
        if self.node_type:
            return self.execute(context)
        return context.window_manager.invoke_search_popup(self)


class GK_OT_StarterGraph(bpy.types.Operator):
    """在当前节点树里搭一个可运行的最小示例。

    空节点树最难的是"从哪开始"。这个算子直接给出一条最短的完整链路
    (每当_准备 → 打印_输出),用户照着改就行。
    """

    bl_idname = "gk.starter_graph"
    bl_label = "生成示例节点图"
    bl_description = ("搭一条最小的可运行链路:每当_准备 → 打印_输出。"
                      "会先清空当前节点树")
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        tree = _tree_of(self, context)
        if tree is not None and len(tree.nodes) > 0:
            return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def draw(self, context):
        self.layout.label(text="当前节点树里已经有节点,会被清空。", icon="ERROR")

    def execute(self, context):
        tree = _tree_of(self, context)
        if tree is None:
            _report(self, "ERROR", "请先打开一棵『游戏逻辑』节点树")
            return {"CANCELLED"}
        count, message = exporter.build_starter_graph(tree)
        if count == 0:
            _report(self, "ERROR", message)
            return {"CANCELLED"}
        _report(self, "INFO", f"已生成示例链路:{message}。改成你要的就行")
        return {"FINISHED"}


def _regenerate_one_preview(obj):
    """只重建某个物体的碰撞线框(实时预览用)。

    找到已有的预览物体,就地改它的顶点 —— 不删了重建。
    为什么:重建会换掉对象指针,拖滑块时视口里的选择状态和绘制状态
    会不停闪烁。就地更新稳得多。
    """
    import bpy

    scene = bpy.context.scene
    if scene is None:
        return False

    physics = getattr(obj, "gk_physics", None)
    is_3d = str(getattr(scene.gk_settings, "dimension", "3")) != "2"

    existing = None
    for candidate in scene.objects:
        if candidate.get(PREVIEW_MARK) and candidate.parent is obj:
            existing = candidate
            break

    # 不需要预览的情况:删掉已有的
    if physics is not None and not getattr(physics, "enabled", True):
        if existing is not None:
            bpy.data.objects.remove(existing, do_unlink=True)
        return False

    preview = asset_export.collision_preview_mesh(obj, is_3d, physics)
    if preview is None:
        if existing is not None:
            bpy.data.objects.remove(existing, do_unlink=True)
        return False

    if existing is None:
        # 还没有预览物体(用户没点过"显示碰撞体")—— 不主动创建,
        # 免得改个参数就凭空冒出线框
        return False

    mesh = existing.data
    mesh.clear_geometry()
    mesh.from_pydata(list(preview["verts"]), list(preview["edges"]), [])
    mesh.update()
    return True


def _on_physics_changed(self, context):
    """物理参数变了 -> 立刻重建这个物体的碰撞线框。

    这就是"实时预览":拖碰撞形状/尺寸的滑块,视口里的线框跟着变,
    不用反复点『显示碰撞体』。UPBGE 也是这个体验。
    """
    obj = getattr(self, "id_data", None)
    if obj is None:
        return
    try:
        _regenerate_one_preview(obj)
    except Exception:
        pass
    # 让视口重画
    try:
        context.view_layer.update()
    except Exception:
        pass


# 碰撞预览物体的标记。定义在 asset_export 里,因为导出时也要用它来
# 排除预览线框(hide_render 对 glTF 导出器无效,必须显式取消选中)。
PREVIEW_MARK = asset_export.PREVIEW_MARK


class GK_OT_ShowCollision(bpy.types.Operator):
    """在当前场景里生成碰撞体线框,方便调整大小让它贴合模型。"""

    bl_idname = "gk.show_collision"
    bl_label = "显示碰撞体"
    bl_description = ("为选中的物体生成碰撞线框。改了形状/尺寸后再点一次会重新生成")
    bl_options = {"REGISTER", "UNDO"}

    # 默认**只处理选中的物体**。
    #
    # 为什么不默认全场景:多个碰撞盒叠在一起时,你在视口里分不清哪个是
    # 哪个物体的,很容易改错尺寸 —— 用户明确反馈过这一点。
    # 全部显示留作"排查有没有漏设"的独立按钮。
    target_all: BoolProperty(
        name="包含整个场景",
        description="不勾 = 只处理选中的物体（推荐）。勾上会显示所有物体的碰撞盒",
        default=False,
    )

    def execute(self, context):
        if context.scene is None:
            return {"CANCELLED"}

        # 先清干净,再按需要重建 —— 这样反复点不会叠出一堆残留线框。
        _remove_previews(context.scene)
        is_3d = str(getattr(context.scene.gk_settings, "dimension", "3")) != "2"

        if self.target_all:
            objects = [obj for obj in context.scene.objects if obj.type == "MESH"]
        else:
            # ⚠ 只认**活动对象**(属性面板里正在看的那个),不做任何退让。
            #
            # 这里踩过两次,所以写死:
            #
            # 1) **不能用 context.selected_objects**。Blender 的"选中"=
            #    活动对象 + 所有高亮项,一次 Ctrl+A 之后它会一直是一大堆,
            #    于是"显示这一个"和"显示全部"看起来一模一样。
            #
            # 2) **不要"取不到活动对象就退回选中列表"**。用户反馈:
            #    "在什么都没有选择时点击显示一个,会显示全部" ——
            #    就是因为有这个退让:活动对象为空时它去拿高亮列表,
            #    而高亮列表可能是一大堆。宁可什么都不显示也不猜。
            #
            # 现在:拿不到活动网格就**清空并明确报错**,让用户知道要点一下模型。
            active = getattr(context, "active_object", None)
            if active is None or getattr(active, "type", "") != "MESH":
                name = getattr(active, "name", "")
                detail = f"当前活动对象是「{name}」,不是网格物体。" if name else \
                    "当前没有活动物体。"
                _report(self, "ERROR",
                        f"{detail}请先在视口里点一下你要看碰撞盒的那个模型,"
                        f"再点『显示这一个』"
                        f"（已顺手清掉旧的碰撞线框）")
                return {"CANCELLED"}
            objects = [active]

        made = []
        skipped = 0
        for obj in objects:
            physics = getattr(obj, "gk_physics", None)
            preview = asset_export.collision_preview_mesh(obj, is_3d, physics)
            if preview is None:
                skipped += 1
                continue
            _make_preview_object(context, obj, preview, is_3d)
            made.append(obj.name)

        if not made:
            _report(self, "WARNING",
                    f"没有生成碰撞线框（跳过 {skipped} 个:"
                    f"碰撞形状设成了『无』）")
            return {"FINISHED"}

        # 把物体名报出来 —— 用户能一眼确认"是不是只有我要看的那个"。
        # 只报个数量的话,"显示了 2 个"到底是哪两个完全看不出来。
        listing = "、".join(made[:4]) + ("…" if len(made) > 4 else "")
        _report(self, "INFO",
                f"已为 {len(made)} 个物体显示碰撞线框: {listing}"
                + (f"（跳过 {skipped} 个）" if skipped else "")
                + "。改尺寸会自动刷新;点『隐藏碰撞体』清理")
        return {"FINISHED"}


def _make_preview_object(context, source, preview, is_3d):
    """按线框数据建一个物体,挂到源物体下面。"""
    name = f"__GK碰撞预览__{source.name}"
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(list(preview["verts"]), list(preview["edges"]), [])
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    context.scene.collection.objects.link(obj)

    # 挂到源物体下并归零变换:这样源物体一动,线框跟着动
    obj.parent = source
    obj.matrix_parent_inverse = source.matrix_world.inverted()
    obj.location = (0.0, 0.0, 0.0)
    obj.rotation_euler = (0.0, 0.0, 0.0)
    obj.scale = (1.0, 1.0, 1.0)

    # 亮青色 + 线框显示。颜色醒目但不刺眼,方便对齐。
    material = bpy.data.materials.get("__GK碰撞预览材质__")
    if material is None:
        material = bpy.data.materials.new("__GK碰撞预览材质__")
        material.use_nodes = True
        for node in material.node_tree.nodes:
            if node.bl_idname == "ShaderNodeBsdfPrincipled":
                node.inputs["Base Color"].default_value = (0.1, 0.9, 1.0, 1.0)
                node.inputs["Emission Color"].default_value = (0.1, 0.9, 1.0, 1.0)
                try:
                    node.inputs["Emission Strength"].default_value = 0.6
                except KeyError:
                    pass
        material.diffuse_color = (0.1, 0.9, 1.0, 1.0)
    mesh.materials.append(material)

    obj.display_type = "WIRE"
    obj.show_in_front = True          # 穿透显示,不然被模型挡住看不见
    obj.hide_render = True            # **不能出现在导出里**
    obj[PREVIEW_MARK] = True
    try:
        obj.hide_select = True
    except Exception:
        pass
    return obj


def _remove_previews(scene):
    """删掉场景里所有碰撞预览物体。"""
    removed = 0
    for obj in list(scene.objects):
        if obj.get(PREVIEW_MARK):
            bpy.data.objects.remove(obj, do_unlink=True)
            removed += 1
    return removed


class GK_OT_HideCollision(bpy.types.Operator):
    """清除所有碰撞体线框。"""

    bl_idname = "gk.hide_collision"
    bl_label = "隐藏碰撞体"
    bl_description = "删除所有由『显示碰撞体』生成的线框"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if context.scene is None:
            return {"CANCELLED"}
        removed = _remove_previews(context.scene)
        _report(self, "INFO", f"已清除 {removed} 个碰撞线框")
        return {"FINISHED"}


class GK_OT_MatchGodotLook(bpy.types.Operator):
    """把 Blender 的预览画面调成贴近 Godot 的样子。"""

    bl_idname = "gk.match_godot_look"
    bl_label = "预览贴近 Godot"
    bl_description = ("把视图变换改成 Standard(对应 Godot 的 Linear 色调映射)、"
                      "背景换成 Godot 默认的平灰、"
                      "并去掉 Godot 没有的环境光和光线追踪")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from . import godot_look

        ok, message = godot_look.apply(context.scene)
        if not ok:
            _report(self, "ERROR", message)
            return {"CANCELLED"}
        _report(self, "INFO", message)
        return {"FINISHED"}


class GK_OT_RestoreLook(bpy.types.Operator):
    """把预览画面还原成 Blender 原来的样子。"""

    bl_idname = "gk.restore_look"
    bl_label = "还原"
    bl_description = "把视图变换、世界和 EEVEE 开关还原成开启『贴近 Godot』之前的样子"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from . import godot_look

        ok, message = godot_look.restore(context.scene)
        if not ok:
            _report(self, "WARNING", message)
            return {"CANCELLED"}
        _report(self, "INFO", message)
        return {"FINISHED"}


class GK_OT_OpenGlossary(bpy.types.Operator):
    """生成并打开「Godot 中文对照表」。

    为什么需要:Godot 界面上有一批英文**改不了** ——
    节点类型名是引擎类名(任何语言都不翻译),检查器里的分组标题
    (Mesh / Transform / Script…)Godot 的中文语言包也没覆盖。
    所以给一份对照表,看到不认识的词能查。
    """

    bl_idname = "gk.open_glossary"
    bl_label = "Godot 中文对照表"
    bl_description = ("把常用英文(节点类型、检查器分组、灯光相机属性)"
                      "的中文对照写成一份文档并打开")
    bl_options = {"REGISTER"}

    def execute(self, context):
        from . import godot_glossary

        # 放在 .blend 旁边 —— 不写进 Godot 项目,免得 res:// 里多一个
        # 跟游戏无关的文件(而且 Godot 会把它当资源列出来)。
        blend = bpy.data.filepath
        target = os.path.dirname(blend) if blend else ""
        if not target:
            target, _ok = _resolve_project(self, context, create=False)
        if not target:
            target = os.path.expanduser("~")

        path, ok, message = godot_glossary.write(target)
        if not ok:
            _report(self, "ERROR", message)
            return {"CANCELLED"}

        # ⚠ open_path 返回的是**结果字典**({"FINISHED"} / {"CANCELLED"}),
        # 不是布尔值 —— 而空字典以外的字典都是真值,直接 if 判断永远为真。
        # 踩过:写成 `if project.open_path(...)` 的话,打开失败也会走"成功"分支。
        opened = project.open_path(path, self)
        if "FINISHED" in opened:
            _report(self, "INFO", f"对照表已生成并打开: {path}")
            return {"FINISHED"}

        # 打开失败(.md 可能没有默认关联程序)—— 至少把文件夹打开,
        # 别让用户对着一句"打不开"发呆。
        project.open_folder(os.path.dirname(path))
        _report(self, "INFO",
                f"对照表已生成: {path}（系统没有 .md 的默认程序,"
                f"已打开所在文件夹,用记事本打开即可）")
        return {"FINISHED"}


class GK_OT_FixBaseClass(bpy.types.Operator):
    """把脚本基类改成『自动』,修掉和节点类型对不上的问题。"""

    bl_idname = "gk.fix_base_class"
    bl_label = "改成自动"
    bl_description = ("把基类设成『自动』,让脚本的 extends 跟着物体的"
                      "物理类型走 —— 这样绝不会对不上")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = getattr(context.scene, "gk_settings", None)
        if settings is None:
            return {"CANCELLED"}
        settings.base_class = props.BASE_CLASS_AUTO
        _report(self, "INFO",
                "基类已改成『自动』。重新导出一次，Godot 就能正常加载脚本了")
        return {"FINISHED"}


class GK_OT_AutoLink(bpy.types.Operator):
    """把两个选中节点自动连起来(按第一个匹配的引脚)。"""

    bl_idname = "gk.auto_link"
    bl_label = "自动连线"
    bl_description = "把选中的两个节点连起来:执行流对执行流，数据引脚按类型匹配"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree = _tree_of(self, context)
        if tree is None:
            return {"CANCELLED"}
        selected = [node for node in tree.nodes if node.select]
        if len(selected) != 2:
            _report(self, "ERROR", "请正好选中两个节点")
            return {"CANCELLED"}
        source, target = selected
        if source.location[0] > target.location[0]:
            source, target = target, source
        if _auto_link(tree, source, target):
            _report(self, "INFO", f"已连接 {source.bl_label} → {target.bl_label}")
            return {"FINISHED"}
        _report(self, "WARNING", "没有找到可连接的引脚")
        return {"CANCELLED"}


def _auto_link(tree, source, target):
    """先试执行流，再试类型匹配的数据引脚。"""
    from .nodes.node_base import target_pin  # noqa: F401  (仅用于文档一致性)

    # 1) 执行流
    for out_socket in source.outputs:
        if out_socket.bl_idname != "GKSocketFlow":
            continue
        for in_socket in target.inputs:
            if in_socket.bl_idname != "GKSocketFlow":
                continue
            if getattr(in_socket, "is_linked", False):
                continue
            tree.links.new(out_socket, in_socket)
            return True
    # 2) 数据引脚:同类型，或目标是"任意值"
    for in_socket in target.inputs:
        if in_socket.bl_idname == "GKSocketFlow" or getattr(in_socket, "is_linked", False):
            continue
        for out_socket in source.outputs:
            if out_socket.bl_idname == "GKSocketFlow":
                continue
            same_type = out_socket.bl_idname == in_socket.bl_idname
            compatible = in_socket.bl_idname == "GKSocketAny" or out_socket.bl_idname == "GKSocketAny"
            if same_type or compatible:
                tree.links.new(out_socket, in_socket)
                return True
    return False


class GK_OT_Export(bpy.types.Operator):
    """生成 GDScript 并写到磁盘。"""

    bl_idname = "gk.export_script"
    bl_label = "导出为 Godot 脚本"
    bl_description = "把当前节点树生成 GDScript 4 代码并保存到导出目录"
    bl_options = {"REGISTER"}

    open_after: BoolProperty(name="导出后打开结果", default=True)
    preview_only: BoolProperty(name="只预览不写文件", default=False)

    def execute(self, context):
        tree = _tree_of(self, context)
        if tree is None:
            return {"CANCELLED"}
        settings = exporter.build_settings(context.scene)
        # 导出目录一律走"当前 .blend 绑定的项目",不再有兜底目录。
        # create=True:允许导出时顺手把缺的项目骨架补上(用户已经在
        # 『项目』面板确认过要建了,或者这就是第一次导出)。
        directory, ok = _resolve_project(self, context, create=True)
        if not ok:
            return {"CANCELLED"}
        settings["output_dir"] = directory

        try:
            result = exporter.export_tree(tree, settings, write_scene=False)
        except codegen.CodegenAbort as exc:
            _report(self, "ERROR", str(exc))
            return {"CANCELLED"}
        except OSError as exc:
            _report(self, "ERROR", f"写入失败: {exc}")
            return {"CANCELLED"}

        # 同时写进 .blend,这样关掉再打开还能看到"上次导出"
        remember_export(context.scene, result)
        for warning in result["warnings"]:
            self.report({"WARNING"}, warning)

        _open_in_text_editor(context, result["code"], result["script_name"])
        if settings.get("write_scene"):
            try:
                scene_path = exporter.write_scene_file(
                    result["directory"],
                    settings.get("node_name", "Player"),
                    settings.get("base_class", "CharacterBody3D"),
                    result["script_name"],
                )
                result["written"].append(scene_path)
                project_path = exporter.write_project_file(result["directory"])
                if project_path:
                    result["written"].append(project_path)
            except OSError as exc:
                _report(self, "WARNING", f"示例场景写入失败: {exc}")

        message = (f"已导出 {result['script_name']}({result['lines']} 行)"
                   + (f"，{len(result['warnings'])} 条告警" if result["warnings"] else ""))
        _report(self, "INFO", message)
        return {"FINISHED"}


class GK_OT_Preview(bpy.types.Operator):
    """只生成代码并放进文本编辑器，不写文件。"""

    bl_idname = "gk.preview_script"
    bl_label = "预览生成的代码"
    bl_description = "在 Blender 文本编辑器里查看将要生成的 GDScript"
    bl_options = {"REGISTER"}

    def execute(self, context):
        tree = _tree_of(self, context)
        if tree is None:
            return {"CANCELLED"}
        settings = exporter.build_settings(context.scene)
        try:
            code, warnings, name, _actions = exporter.generate_code(tree, settings)
        except codegen.CodegenAbort as exc:
            _report(self, "ERROR", str(exc))
            return {"CANCELLED"}

        # 预览也算一次导出记录,但**不写进 .blend**:
        # 预览没落盘,记进去会让"打开导出目录"指向一个没有新文件的位置。
        LAST_EXPORT.clear()
        LAST_EXPORT.update({
            "ok": True, "code": code, "warnings": warnings, "script_name": name,
            "lines": len(code.splitlines()), "written": [],
            "directory": settings.get("output_dir", ""),
        })
        _open_in_text_editor(context, code, name)
        for warning in warnings:
            self.report({"WARNING"}, warning)
        _report(self, "INFO", f"已生成预览: {name}({len(code.splitlines())} 行)")
        return {"FINISHED"}


class GK_OT_ExportAssets(bpy.types.Operator):
    """把 Blender 的模型和材质一起导出到 Godot,并装配成"模型+碰撞体+脚本"的场景。

    材质跟着模型一起导出:没设材质的物体会用默认白色材质。
    以前这里拆成"导出模型"和"导出材质"两个按钮,但用户实际永远是
    两个一起点 —— 拆开只是多一步操作,所以合并成一个。
    """

    bl_idname = "gk.export_assets"
    bl_label = "导出模型和材质"
    bl_description = ("导出选中网格为 glTF(.glb),同时生成材质文件与引用它的 .tscn,"
                      "并把上一次导出的脚本挂到根节点上。没材质的用默认白色")
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        settings = exporter.build_settings(scene)
        directory, ok = _resolve_project(self, context, create=True)
        if not ok:
            return {"CANCELLED"}
        settings["output_dir"] = directory

        objects, how = asset_export.collect_objects(context)
        if not objects:
            _report(self, "ERROR", "没有可导出的网格:请选中至少一个网格物体")
            return {"CANCELLED"}

        # 整条流程都在 asset_export.export_assets_pipeline 里,
        # 这里只负责"取参数 + 报告结果"。
        # 抽出去的原因:"运行前自动导出"要用同一套流程,
        # 抄一份必然走偏(顺序、材质引用这些细节很容易漏)。
        try:
            outcome = asset_export.export_assets_pipeline(
                scene, directory,
                dimension=settings.get("dimension", "3"),
                script_name=LAST_EXPORT.get("script_name") or None,
            )
        except Exception as exc:
            _report(self, "ERROR", f"导出失败: {type(exc).__name__}: {exc}")
            return {"CANCELLED"}

        if outcome.get("error"):
            _report(self, "ERROR", outcome["error"])
            return {"CANCELLED"}

        for warning in outcome.get("warnings", []):
            _report(self, "WARNING", warning)

        written = outcome.get("written", [])
        asset_export.ensure_project_file(directory)

        if not LAST_EXPORT.get("script_name"):
            _report(self, "WARNING",
                    "还没有导出过脚本，先点『导出为 Godot 脚本』才能把逻辑挂到模型上")

        bounds = asset_export.object_bounds(objects)
        size = bounds["size"] if bounds else (0, 0, 0)
        _report(self, "INFO",
                f"已导出 {len(objects)} 个网格、{outcome['materials']} 个材质、"
                f"{outcome['bodies']} 个碰撞体、{outcome['cameras']} 个相机、"
                f"{outcome['lights']} 盏灯"
                f"（包围盒 {size[0]:.2f}×{size[1]:.2f}×{size[2]:.2f}）")
        for path in written:
            gk_log.info(f"写入 {os.path.basename(path)}")
        return {"FINISHED"}


class GK_OT_SyncFaceAngle(bpy.types.Operator):
    """从场景里的太阳光反推面部 SDF 的光照系数。

    面部 SDF 的阴影线位置由"光相对相机的水平夹角"决定。Godot 里那是逐片元
    算的;Blender 预览里直接给系数,这个算子负责从场景灯光把系数算出来,
    省得手动试。

    注意这是**近似**:Godot 用视图空间叉积,这里用的是光源与相机的水平夹角。
    固定光照方向时两者给出同样的阴影线位置 —— 也就是预览要的效果。
    """

    bl_idname = "gk.sync_face_angle"
    bl_label = "从场景灯光取角度"
    bl_description = ("按场景里太阳光与相机的夹角,设置材质上的面部光照系数")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from . import npr_preview

        angle = npr_preview.compute_face_sdf_angle(context.scene)
        if angle is None:
            _report(self, "ERROR", "需要场景里有相机和一盏太阳光(SUN)")
            return {"CANCELLED"}

        materials = []
        for obj in context.selected_objects:
            for slot in getattr(obj.data, "materials", ()) or ():
                if slot is not None and npr_preview.has_param(slot, "face_sdf_mix"):
                    materials.append(slot)
        if not materials:
            materials = [m for m in bpy.data.materials
                         if npr_preview.has_param(m, "face_sdf_mix")]
        if not materials:
            _report(self, "ERROR", "没找到带面部 SDF 参数的材质")
            return {"CANCELLED"}

        for material in materials:
            params = npr_preview.read_params(material)
            params["face_sdf_angle"] = angle
            npr_preview.update_material(material, params)

        _report(self, "INFO",
                f"已把 {len(materials)} 个材质的『面部光照系数』设为 {angle:+.3f}"
                f"（0=正前方，+1=画面右侧）")
        return {"FINISHED"}


# ==========================================================================
# 项目管理:一个 .blend 对应一个 Godot 项目
# ==========================================================================
class GK_OT_CreateProject(bpy.types.Operator):
    """为当前 .blend 创建配套的 Godot 项目。

    项目建在 .blend 旁边,文件夹名 = 文件名 + _godot。
    """

    bl_idname = "gk.create_project"
    bl_label = "创建 Godot 项目"
    bl_description = ("在 .blend 旁边建一个同名的 Godot 项目(含 project.godot "
                      "与 art/scripts/scenes/materials/shaders 目录)")
    bl_options = {"REGISTER", "UNDO"}

    # 让用户在弹窗里改名字/位置
    directory: StringProperty(
        name="项目位置",
        description="留空 = 建在 .blend 旁边,文件夹名与 .blend 同名 + _godot",
        default="",
        subtype="DIR_PATH",
    )
    open_after: BoolProperty(
        name="创建后用 Godot 打开",
        default=True,
    )

    def invoke(self, context, event):
        derived, reason = project.resolve(context.scene)
        if derived:
            self.directory = derived
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        layout = self.layout
        blend = bpy.data.filepath or "(未保存)"
        layout.label(text=f"当前 .blend: {os.path.basename(blend) or blend}",
                     icon="FILE_BLEND")
        if not bpy.data.filepath:
            layout.label(text="文件还没保存,请先 Ctrl+S", icon="ERROR")
        layout.separator()
        layout.prop(self, "directory", text="项目位置")
        layout.prop(self, "open_after")
        layout.separator()
        preview = self.directory or project.derive_project_dir()
        exists = os.path.isdir(preview) if preview else False
        if preview:
            layout.label(text="将创建/使用:", icon="FILE_FOLDER")
            layout.label(text=preview)
            if project.is_project(preview):
                layout.label(text="这个目录已经是一个 Godot 项目(不会覆盖内容)",
                             icon="INFO")
            elif exists:
                layout.label(text="目录已存在,只会补齐缺的部分", icon="INFO")

    def execute(self, context):
        if not bpy.data.filepath:
            _report(self, "ERROR", "请先保存 .blend 文件(Ctrl+S),"
                                   "项目要建在它旁边")
            return {"CANCELLED"}

        directory = (self.directory or "").strip()
        if not directory:
            directory, reason = project.resolve(context.scene)
            if not directory:
                _report(self, "ERROR", reason)
                return {"CANCELLED"}
        directory = os.path.abspath(bpy.path.abspath(directory))

        already = project.is_project(directory)
        try:
            created = project.create_project(
                directory,
                title=project.safe_project_name(project.blend_name() or "", "game"),
                blend_file_name=os.path.basename(bpy.data.filepath),
            )
        except OSError as exc:
            _report(self, "ERROR", f"创建失败: {exc}")
            return {"CANCELLED"}

        # 如果用户指定的位置和推导出来的不一样,记到场景属性上,
        # 这样下次打开这个 .blend 还知道项目在哪
        derived, _reason = project.resolve(context.scene)
        settings = getattr(context.scene, "gk_settings", None)
        if settings is not None and directory != derived:
            settings.project_dir = directory

        if already:
            _report(self, "INFO", f"项目已存在: {directory}")
        else:
            _report(self, "INFO",
                    f"已创建 Godot 项目: {directory}"
                    f"（{len(created)} 项）")

        if self.open_after:
            ok, message = project.open_in_godot(directory, context)
            _report(self, "INFO" if ok else "WARNING", message)
        return {"FINISHED"}


class GK_OT_OpenGodot(bpy.types.Operator):
    """用 Godot 打开当前 .blend 绑定的项目。"""

    bl_idname = "gk.open_godot"
    bl_label = "用 Godot 打开"
    bl_description = "启动 Godot 编辑器并打开当前项目"
    # 加 UNDO:让 Blender 把算子属性当"每次都重新设置",
    # 而不是可能残留上一次的值。
    bl_options = {"REGISTER", "UNDO"}

    # 属性名刻意叫 editor 而不是 run_game:
    # 默认值 True = 打开编辑器,这是最常用也最安全的动作。
    # 之前叫 run_game 且默认 False,一旦属性传递出问题(比如从别的入口调),
    # 就会变成"点了打开编辑器却在运行游戏"。
    # 现在的写法:默认值本身就是"打开编辑器",即使属性丢了也不会跑错。
    editor: BoolProperty(
        name="打开编辑器",
        description="勾选 = 打开 Godot 编辑器;不勾 = 直接运行游戏",
        default=True,
    )
    auto_export: BoolProperty(
        name="运行前自动导出",
        description=("直接运行游戏之前,自动保存 .blend 并导出脚本与模型。"
                     "这样就不用手动点两下 —— 改完直接点运行就能看到新结果"),
        default=True,
    )

    def execute(self, context):
        directory, ok = _resolve_project(self, context, create=False)
        if not ok:
            return {"CANCELLED"}

        # 直接运行游戏时,先自动保存 + 导出。
        #
        # 为什么:用户的直觉是"我在 Blender 里改完,点运行就能看到新结果"。
        # 如果还要手动先保存、再点导出,点运行时跑的还是旧内容 ——
        # 既费事又容易误判成"改了没生效"。
        #
        # 偏好设置里的 auto_export_on_run 是总开关,算子属性是本次的开关,
        # 两者都为真才自动导出。
        prefs = project.preferences(context)
        pref_on = bool(getattr(prefs, "auto_export_on_run", True)) if prefs else True
        if not self.editor and self.auto_export and pref_on:
            self._auto_prepare(context, directory)

        opened, message = project.open_in_godot(directory, context,
                                                editor=bool(self.editor))
        if not opened:
            _report(self, "ERROR", message)
            return {"CANCELLED"}
        _report(self, "INFO", message)
        return {"FINISHED"}

    def _auto_prepare(self, context, directory):
        """保存 .blend 并导出脚本 + 模型。失败只警告,不阻止运行。

        注意:这一步刻意**不抛异常打断流程** —— 用户点的是"运行",
        因为导出出了点问题就不让跑会很恼人。警告写进日志就够了。
        """
        # 1) 保存 .blend —— 素材和节点图都在文件里,不保存的话导出的是旧内容
        if bpy.data.filepath and bpy.data.is_dirty:
            try:
                bpy.ops.wm.save_mainfile()
                _report(self, "INFO", "已自动保存 .blend")
            except Exception as exc:
                _report(self, "WARNING", f"自动保存失败(继续运行): {exc}")

        # 2) 导出脚本(节点图)
        tree = None
        for group in bpy.data.node_groups:
            if getattr(group, "bl_idname", "") == "GKGameLogicTree" and len(group.nodes):
                tree = group
                break
        script_name = None
        if tree is not None:
            try:
                settings = exporter.build_settings(context.scene)
                settings["output_dir"] = directory
                result = exporter.export_tree(tree, settings, write_scene=False)
                remember_export(context.scene, result)
                script_name = result["script_name"]
                for warning in result["warnings"]:
                    self.report({"WARNING"}, warning)
            except Exception as exc:
                _report(self, "WARNING", f"自动导出脚本失败(继续运行): {exc}")

        # 3) 导出场景 / 模型 / 材质
        try:
            outcome = asset_export.export_assets_pipeline(
                context.scene, directory,
                dimension=exporter.build_settings(context.scene).get("dimension", "3"),
                script_name=script_name,
            )
            if outcome.get("error"):
                _report(self, "WARNING", f"自动导出模型失败: {outcome['error']}")
            else:
                for warning in outcome.get("warnings", []):
                    _report(self, "WARNING", warning)
                _report(self, "INFO",
                        f"已自动导出:{outcome['materials']} 个材质、"
                        f"{outcome['bodies']} 个碰撞体、"
                        f"{outcome['cameras']} 个相机、{outcome['lights']} 盏灯")
        except Exception as exc:
            _report(self, "WARNING", f"自动导出模型失败(继续运行): {exc}")


class GK_OT_RelinkProject(bpy.types.Operator):
    """把当前 .blend 重新绑到别的项目目录(或恢复成"跟着 .blend 走")。"""

    bl_idname = "gk.relink_project"
    bl_label = "重新指定项目位置"
    bl_description = "手动指定这个 .blend 对应的 Godot 项目目录"
    bl_options = {"REGISTER", "UNDO"}

    directory: StringProperty(
        name="项目位置",
        description="留空 = 恢复成『跟着 .blend 走』",
        default="",
        subtype="DIR_PATH",
    )

    def invoke(self, context, event):
        settings = getattr(context.scene, "gk_settings", None)
        if settings is not None:
            self.directory = settings.project_dir or ""
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "directory", text="项目位置")
        layout.separator()
        if self.directory:
            layout.label(text="会写进这个 .blend,下次打开还记得", icon="INFO")
        else:
            derived = project.derive_project_dir()
            layout.label(text="留空 = 跟着 .blend 走:", icon="INFO")
            layout.label(text=derived or "(需要先保存 .blend)")

    def execute(self, context):
        settings = getattr(context.scene, "gk_settings", None)
        if settings is None:
            _report(self, "ERROR", "场景属性不可用")
            return {"CANCELLED"}
        settings.project_dir = (self.directory or "").strip()
        if settings.project_dir:
            _report(self, "INFO", f"项目位置已指定: {settings.project_dir}")
        else:
            derived, _reason = project.resolve(context.scene)
            _report(self, "INFO", f"已恢复为『跟着 .blend 走』: {derived or '(待保存)'}")
        return {"FINISHED"}


class GK_OT_DetectGodot(bpy.types.Operator):
    """自动找一个 Godot 可执行文件并填进偏好设置。"""

    bl_idname = "gk.detect_godot"
    bl_label = "自动查找 Godot"
    bl_options = {"REGISTER"}

    def execute(self, context):
        prefs = project.preferences(context)
        if prefs is None:
            _report(self, "ERROR", "取不到插件偏好设置")
            return {"CANCELLED"}
        found = project.find_godot_executable(context)
        if not found:
            _report(self, "ERROR", "没找到 Godot,请手动指定路径")
            return {"CANCELLED"}
        prefs.godot_path = found
        _report(self, "INFO", f"已找到: {found}")
        return {"FINISHED"}


class GK_OT_OpenExportFolder(bpy.types.Operator):
    """在文件管理器里打开导出目录。"""

    bl_idname = "gk.open_export_folder"
    bl_label = "打开导出目录"
    bl_description = "用系统文件管理器打开 GDScript 的导出目录"
    bl_options = {"REGISTER"}

    def execute(self, context):
        # 优先用上次导出的实际目录;没有就问项目解析要
        directory = LAST_EXPORT.get("directory")
        if not directory:
            directory, _reason = project.resolve(context.scene)
        if not directory or not os.path.isdir(directory):
            _report(self, "ERROR",
                    f"项目目录还不存在: {directory or '(未绑定)'}\n"
                    f"请先在侧栏『项目』里创建项目")
            return {"CANCELLED"}
        ok, message = project.open_folder(directory)
        if not ok:
            _report(self, "ERROR", message)
            return {"CANCELLED"}
        return {"FINISHED"}


class GK_OT_SelectNode(bpy.types.Operator):
    """导出一处告警时，跳回出问题的节点。"""

    bl_idname = "gk.select_node"
    bl_label = "定位节点"
    bl_description = "选中并居中显示指定节点"
    bl_options = {"REGISTER", "UNDO"}

    node_name: StringProperty(name="节点名", default="")

    def execute(self, context):
        tree = _tree_of(self, context)
        if tree is None:
            return {"CANCELLED"}
        node = tree.nodes.get(self.node_name)
        if node is None:
            _report(self, "WARNING", f"找不到节点: {self.node_name}")
            return {"CANCELLED"}
        for other in tree.nodes:
            other.select = other is node
        tree.nodes.active = node
        if context.space_data is not None:
            context.space_data.cursor_location = node.location
        return {"FINISHED"}


class GK_OT_ResetSettings(bpy.types.Operator):
    """把导出设置恢复成默认值。"""

    bl_idname = "gk.reset_settings"
    bl_label = "重置导出设置"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        settings = getattr(context.scene, "gk_settings", None)
        if settings is None:
            return {"CANCELLED"}
        settings.dimension = "3"
        settings.base_class = "CharacterBody3D"
        settings.class_name = ""
        settings.node_name = "Player"
        settings.export_tunables = True
        settings.write_scene = True
        # 清空而不是填默认路径 —— 留空表示"跟着 .blend 走",这是推荐用法
        settings.output_dir = ""
        settings.project_dir = ""
        _report(self, "INFO", "导出设置已重置(项目目录恢复为『跟着 .blend』)")
        return {"FINISHED"}


GK_CLASSES = (
    GK_OT_AddNode,
    GK_OT_StarterGraph,
    GK_OT_AutoLink,
    # 碰撞体可视化(像 UPBGE 那样能在视口里看到碰撞范围)
    GK_OT_ShowCollision,
    GK_OT_HideCollision,
    # 让 Blender 预览贴近 Godot 的渲染(色调映射/背景/环境光)
    GK_OT_MatchGodotLook,
    GK_OT_RestoreLook,
    # Godot 界面英文的中文对照表
    GK_OT_OpenGlossary,
    # 修脚本基类和节点类型对不上的问题
    GK_OT_FixBaseClass,
    # 项目管理(一个 .blend 对应一个 Godot 项目)
    GK_OT_CreateProject,
    GK_OT_OpenGodot,
    GK_OT_RelinkProject,
    GK_OT_DetectGodot,
    GK_OT_Export,
    GK_OT_ExportAssets,
    GK_OT_SyncFaceAngle,
    GK_OT_Preview,
    GK_OT_OpenExportFolder,
    GK_OT_SelectNode,
    GK_OT_ResetSettings,
)


def on_register():
    _register_handlers()
    # 插件启用时若已经打开了 .blend,也恢复一次
    try:
        restore_export(getattr(bpy.context, "scene", None))
    except Exception:
        pass


def on_unregister():
    _unregister_handlers()
    LAST_EXPORT.clear()
    RECENT_NODES.clear()
