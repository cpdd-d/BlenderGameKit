"""算子:添加节点、导出、预览、打开目录。"""

import json
import os
import subprocess
import sys
import time

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       StringProperty)

from . import (advisor, advisor_tools, asset_export, codegen, curve_assets,
               export_report, exporter, gk_log, npr_preview, packaging,
               project)
from . import properties as props
from .nodes import nodes as node_lib

# 记录上一次导出结果，供面板与"打开结果"使用
LAST_EXPORT = {}
# 记录上一次**打包**出来的可执行文件路径,供面板显示 + 『打开打包目录』。
# 只在内存里:打包是"刚才那一下"的动作,重开 Blender 后路径多半已经没意义了。
LAST_RELEASE = {}
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
    # ── 顺带记一份**结构化报告**(用户 2026-10-05 第二十三轮)──────────
    # 面板上那块"上次导出"以前只列前 4 条、还把诊断压成一行(附在消息里的
    # "怎么修"全看不见)。报告在 export_report 里统一做:排序、截断、
    # 带 hint / node_name 的清单 + 可复制成文本。
    try:
        export_report.record(scene, result)
    except Exception as exc:                              # noqa: BLE001
        gk_log.warn(f"导出报告记录失败(不影响导出): {exc}")
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
    # ── 有**选中节点**的那棵树优先(用户 2026-10-05 第十七轮)─────────
    # 『折叠成函数』这类"对选中节点动手"的算子必须拿到**选中节点所在的**
    # 那棵树:有两棵树时,上一条"只有一棵才敢用"的规矩会让它直接报错 ——
    # 而用户明明就选中了节点(无头测试里也在跑同样的路径)。
    with_selection = [group for group in candidates
                      if any(getattr(node, "select", False)
                             for node in group.nodes)]
    if len(with_selection) == 1:
        return with_selection[0]
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
    # 对话历史也跟着文件走 —— 重开 .blend 还能接着上一轮聊(和上面
    # restore_export 同一个理由)。**只恢复历史**,不去碰文本块:
    # 文本块本身就在 .blend 里,Blender 自己会把它读回来。
    try:
        advisor.load_history(getattr(bpy.context, "scene", None))
    except Exception:
        pass
    # ── 引脚改名迁移必须在这里做(踩过的坑)──────────────────────────
    # Blender 加载 .blend 的顺序是"**先注册插件、再读文件**" ——
    # 注册时 `bpy.data.node_groups` 还是空的,那时候迁移什么也找不到。
    # (实测:注册时返回 0,改到 load_post 之后返回 1 并真的改掉了。)
    try:
        from .nodes import nodes as _node_lib
        moved = _node_lib.migrate_pin_names()
        if moved:
            print(f"[BlenderGameKit] 已更新 {moved} 个节点的引脚名"
                  f"(旧文件升级,保存一次即可固化)")
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


def node_tree_error(cls, tree):
    """这个节点能不能放进这棵树?不能就返回**一句人话**,能放返回 None。

    ── 为什么单独抽成函数(用户 2026-10-03 的报错就在这里)──────────
    用户截图里的那条:
        RuntimeError:『常量_数值』是游戏逻辑树里的节点,不能放进当前的图
    出处就是这里。原因是 `gk_tree_type` 允许是**元组**(常量/数学/比较
    这些"两张树都能用"的值节点),而老代码拿元组和字符串比
    (`wanted != actual`)**永远是真** —— 于是所有"两张树都能用"的节点
    全被挡在门外,而用户明明只是想给转场条件加一个数值。

    抽出来还有一个好处:它是**纯函数**,可以直接在测试里喂真实节点树
    (不用模拟 Blender 的 space_data 上下文)。
    """
    actual = getattr(tree, "bl_idname", "")
    if props.node_fits_tree(cls, actual):
        return None
    kinds = props.node_tree_kinds(cls)
    label_of = {
        "GKGameLogicTree": "游戏逻辑树",
        "GKAnimationTree": "动画树(『游戏动画』)",
        "GKUiDesignTree": "界面设计图(『游戏界面』)",
        "GKGameAiTree": "AI 图(『游戏AI』)",
        "GKDisplayTree": "显示设置图(『游戏显示』)",
        "GKAssetTree": "资产库(『游戏资产』)",
    }
    names = [label_of[kind] for kind in kinds if kind in label_of]
    where = " / ".join(names) if names else "游戏逻辑树"
    if "GKAnimationTree" in kinds:
        hint = "侧栏『游戏逻辑』面板 -> 『动画系统』-> 点『打开/新建动画树』"
    elif "GKUiDesignTree" in kinds:
        hint = ("侧栏『游戏逻辑』面板 -> 『界面设计』-> "
                "点『打开/新建界面设计图』")
    elif "GKGameAiTree" in kinds:
        hint = ("侧栏『游戏逻辑』面板 -> 『游戏AI』-> "
                "点『打开/新建 AI 图』")
    elif "GKDisplayTree" in kinds:
        hint = ("侧栏『游戏逻辑』面板 -> 『游戏显示设置』-> "
                "点『打开/新建显示设置图』")
    elif "GKAssetTree" in kinds:
        hint = ("侧栏『游戏逻辑』面板 -> 『游戏资产』-> "
                "点『打开/新建资产库』")
    else:
        hint = "请切到游戏逻辑图"
    return (f"『{getattr(cls, 'bl_label', cls)}』是{where}里的节点,"
            f"不能放进当前的图。{hint}")


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

        # ── 节点只能放进**自己那类树**(踩过的坑)────────────────────
        # 用户实测:在「游戏逻辑」图里加『动画状态』节点,Blender 只丢出
        #     RuntimeError: 无法将 'GKNodeAnimState' 类型的节点添加到
        #     节点树 '骨骼_动画_游戏逻辑' 中
        # —— 这是 `poll()` 挡下来了,但**用户看不懂该去哪**。
        #
        # 所以在这里先拦一道,给一句人话:告诉他该去哪种树。
        # 判断本身在 node_tree_error 里(那里解释了元组树类型的坑)。
        problem = node_tree_error(cls, tree)
        if problem:
            _report(self, "ERROR", problem)
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


# ==========================================================================
# 注释框(UE 蓝图的 Comment Box)
# ==========================================================================
# 用户要「节点内联注释」—— 蓝图里那个能**框住一组节点**、带标题和颜色、
# 拖动框会带着整组节点走的 Comment Box。Blender 里对应的就是原生
# `NodeFrame`:它天生支持认子节点(parent)、有 label、能自定义颜色,
# 拖框即拖整组。所以这里不重造轮子,而是给一个"一键建好"的入口:
# 选中节点 -> 点『注释框』-> 自动算包围盒、建 Frame、认子、上色。
#
# ── 为什么不直接调 Blender 自带的 `node.join_in_new_frame` ──────────
# 它依赖节点编辑器的 UI 上下文(area/space_data),在无头/测试环境里
# 调不动;而且它不带我们想要的默认标题与配色。自己算包围盒更可控,
# 也能把"算包围盒"这段单独拿出来测。
def _node_bounds(node):
    """返回节点的 (左上角 x, 左上角 y, 宽, 高)。

    Blender 的 node.location 是**左上角**,高度向下延伸(y 减小)。
    """
    wx, wy = node.location[0], node.location[1]
    width, height = getattr(node, "dimensions", (0.0, 0.0))
    if not width or width < 1.0:
        width = float(getattr(node, "width", 0.0) or 0.0) or 160.0
    if not height or height < 1.0:
        # 还没画过时 dimensions 是 (0,0)(无头/刚建的节点) —— 按引脚数估个高度
        count = (len(getattr(node, "inputs", ()) or ())
                 + len(getattr(node, "outputs", ()) or ()))
        height = 60.0 + 22.0 * count
    return wx, wy, float(width), float(height)


def comment_box_rect(nodes):
    """若干节点的包围盒 -> 注释框的 (location, width, height)。纯函数,可离线测。"""
    left = right = top = bottom = None
    for node in nodes:
        wx, wy, width, height = _node_bounds(node)
        n_left, n_right = wx, wx + width
        n_top, n_bottom = wy, wy - height
        left = n_left if left is None else min(left, n_left)
        right = n_right if right is None else max(right, n_right)
        top = n_top if top is None else max(top, n_top)
        bottom = n_bottom if bottom is None else min(bottom, n_bottom)
    pad, bar = 24.0, 30.0
    location = (left - pad, top + pad + bar)
    return location, (right - left) + 2.0 * pad, (top - bottom) + 2.0 * pad + bar


class GK_OT_AddCommentBox(bpy.types.Operator):
    """把选中的节点框进一个注释框(UE 蓝图的 Comment Box)。

    不生成任何代码 —— 它只是节点图上的排版/备注工具。
    """

    bl_idname = "gk.add_comment_box"
    bl_label = "注释框"
    bl_description = ("把选中的节点框起来:可写标题、改颜色,拖动框会带着节点一起走。"
                      "不生成任何代码")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree = _tree_of(self, context)
        if tree is None:
            _report(self, "ERROR", "请先打开『游戏逻辑』或『游戏动画』节点图")
            return {"CANCELLED"}
        picked = [node for node in tree.nodes
                  if getattr(node, "select", False)
                  and getattr(node, "bl_idname", "") != "NodeFrame"]
        if not picked:
            _report(self, "ERROR", "先选中要框住的节点,再点『注释框』")
            return {"CANCELLED"}

        location, width, height = comment_box_rect(picked)
        frame = tree.nodes.new("NodeFrame")
        # ── 顺序要紧:先定好框的位置,再认子 ──────────────────────────
        # Blender 在 `node.parent = frame` 时会把节点的 location
        # **自动换算成相对坐标**(实测:100,-50 的节点、框在 60,30,
        # 认子后自动变成 40,-80)。所以框的位置必须在认子**之前**定死,
        # 否则子节点会被算歪。
        frame.location = location
        frame.width = width
        frame.height = height
        frame.use_custom_color = True
        frame.color = (0.16, 0.30, 0.45)
        frame.label = "注释"
        frame.label_size = 16
        for node in picked:
            node.parent = frame

        for other in tree.nodes:
            other.select = False
        frame.select = True
        tree.nodes.active = frame
        _report(self, "INFO",
                f"已把 {len(picked)} 个节点框进注释框。按 F2 改标题,侧栏可改颜色")
        return {"FINISHED"}


class GK_OT_VarFindRefs(bpy.types.Operator):
    """选中图里所有引用某个变量的节点(蓝图 Find References)。"""

    bl_idname = "gk.var_find_refs"
    bl_label = "查找引用"
    bl_description = "选中这张图里所有读到/写到这个变量的节点"
    bl_options = {"REGISTER", "UNDO"}

    var_name: StringProperty(name="变量名", default="")

    def execute(self, context):
        tree = _tree_of(self, context)
        if tree is None:
            return {"CANCELLED"}
        hits = props.nodes_for_variable(tree, self.var_name)
        if not hits:
            _report(self, "INFO", f"图里没有节点引用『{self.var_name}』")
            return {"CANCELLED"}
        for node in tree.nodes:
            node.select = False
        for node in hits:
            node.select = True
        tree.nodes.active = hits[0]
        # 顺手把镜头带到选中的节点上,不然高亮了也不一定在视野里。
        # 没有合适的区域时(view_selected 找不到节点编辑器)就算了。
        try:
            bpy.ops.node.view_selected()
        except Exception:
            pass
        _report(self, "INFO", f"『{self.var_name}』被 {len(hits)} 个节点引用")
        return {"FINISHED"}


class GK_OT_VarMakeNode(bpy.types.Operator):
    """把变量拉到图里 —— 生成『变量_读取』或『变量_设置』节点。"""

    bl_idname = "gk.var_make_node"
    bl_label = "把变量拉进图"
    bl_description = "生成一个读/写这个变量的节点(放在节点编辑器的光标位置)"
    bl_options = {"REGISTER", "UNDO"}

    var_name: StringProperty(name="变量名", default="")
    make_set: BoolProperty(name="是设置", default=False)

    def execute(self, context):
        tree = _tree_of(self, context)
        if tree is None:
            return {"CANCELLED"}
        name = str(self.var_name or "").strip()
        # 『取』必须有个变量名;『设』留空则用节点自带的默认名("health"),
        # 相当于"新建一个变量"(面板空的时候给的那颗按钮)。
        if not name and not self.make_set:
            _report(self, "ERROR", "先给变量起个名字")
            return {"CANCELLED"}
        space = getattr(context, "space_data", None)
        location = getattr(space, "cursor_location", None) or (0.0, 0.0)
        node = tree.nodes.new("GKNodeSetVar" if self.make_set else "GKNodeGetVar")
        node.location = location
        if name:
            node.gk_var_name = name
        for other in tree.nodes:
            other.select = False
        node.select = True
        tree.nodes.active = node
        _report(self, "INFO",
                f"放了{'设置' if self.make_set else '读取'}变量"
                f"『{name or getattr(node, 'gk_var_name', '')}』的节点")
        return {"FINISHED"}


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


class GK_OT_CharacterPreset(bpy.types.Operator):
    """一键生成第三人称角色(预设)。

    ── 为什么要有它(用户 2026-10-04 第 6 条)────────────────────────
    用户原话:"把现有 输入 / 移动 / 动画 / 相机 节点封装成'一键生成第三人称
    角色'模板,不改底层节点"。所以这里**只建节点 + 连线 + 填参数** ——
    建出来的是一张普通节点图,之后想怎么改都行;连线和参数取自
    examples/demo_level 里那套已经跑通的手感(见 presets 模块)。

    相机物体**不建**(用户确认:"只连图,相机我自己摆")—— 名字对上就行,
    场景里没有同名相机时运行时本来就有兜底告警。
    """

    bl_idname = "gk.character_preset"
    bl_label = "生成第三人称角色(预设)"
    bl_description = ("新建两棵连好的图:角色逻辑(输入/移动/跳跃/相机)"
                      " + 角色动作(待机/跑步/跳跃),并顺手把角色的物理类型"
                      "与『脚本挂载目标』配好。相机物体自己摆,名字对上即可")
    bl_options = {"REGISTER", "UNDO"}

    character: StringProperty(
        name="角色物体", default="",
        description="留空 = 用当前选中的网格 / 已经挂了脚本的物体")
    camera_side: StringProperty(name="跟随机位名", default="侧视机位",
                                description="第三人称跟随机位(弹簧臂的主人)")
    camera_top: StringProperty(name="俯瞰机位名", default="俯瞰机位",
                               description="按 C 切过去的俯瞰机位")
    move_speed: FloatProperty(name="移动速度(米/秒)", default=6.0,
                              min=0.1, max=50.0)
    jump_force: FloatProperty(name="跳跃强度", default=7.0, min=0.1, max=50.0)
    sensitivity: FloatProperty(name="鼠标灵敏度(度/像素)", default=0.25,
                               min=0.01, max=2.0)
    arm_length: FloatProperty(name="弹簧臂长度", default=5.0, min=0.5, max=40.0)
    arm_height: FloatProperty(name="杆子高度", default=1.4, min=0.0, max=10.0)
    aim_pitch: FloatProperty(name="起始俯仰(度)", default=12.0,
                             min=-89.0, max=89.0)
    with_animation: BoolProperty(name="连动画状态机(待机/跑步/跳跃)",
                                 default=True)
    with_ports: BoolProperty(name="对外端口(位置 X/Y/Z + 在地面)",
                             default=True)
    with_god_camera: BoolProperty(name="打开场景的『上帝摄像机』开关",
                                  default=True)

    def invoke(self, context, event):
        # 参数都摆在一个小对话框里 —— 一次性动作,不占侧栏
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "character")
        box = layout.box()
        box.label(text="机位(自己摆好,名字对上就行)", icon="CAMERA_DATA")
        box.prop(self, "camera_side")
        box.prop(self, "camera_top")
        box = layout.box()
        box.label(text="手感", icon="DRIVER")
        box.prop(self, "move_speed")
        box.prop(self, "jump_force")
        box.prop(self, "sensitivity")
        box.prop(self, "arm_length")
        box.prop(self, "arm_height")
        box.prop(self, "aim_pitch")
        box = layout.box()
        box.label(text="带什么", icon="CHECKBOX_HLT")
        box.prop(self, "with_animation")
        box.prop(self, "with_ports")
        box.prop(self, "with_god_camera")

    def execute(self, context):
        from . import presets

        scene = context.scene
        character = presets.resolve_character(scene, self.character)
        try:
            logic_tree, _anim_tree, notes = presets.create_character_preset(
                scene, character,
                character=self.character,
                camera_side=self.camera_side, camera_top=self.camera_top,
                move_speed=self.move_speed, jump_force=self.jump_force,
                sensitivity=self.sensitivity, arm_length=self.arm_length,
                arm_height=self.arm_height, aim_pitch=self.aim_pitch,
                with_animation=self.with_animation,
                with_ports=self.with_ports,
                with_god_camera=self.with_god_camera)
        except Exception as exc:                          # noqa: BLE001
            _report(self, "ERROR", f"生成失败:{type(exc).__name__}: {exc}")
            return {"CANCELLED"}
        for note in notes:
            print(f"[BlenderGameKit] {note}")
        _report(self, "INFO",
                f"预设已生成『{logic_tree.name}』—— 相机名对不上时在 "
                f"『弹簧臂 / 相机_切换』节点里改一下即可")
        # 顺手切到新那张图,让用户立刻看到成果(没有节点编辑器就算了)
        try:
            bpy.ops.gk.focus_tree(tree_name=logic_tree.name)
        except Exception:                                 # noqa: BLE001
            pass
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

    # 碰撞块改了参数 -> 要刷新的是**宿主**那条线框(块的形状画在宿主身上)
    if asset_export.is_collision_piece(obj):
        obj = asset_export.collision_owner(obj) or obj
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
            # 子物体碰撞块不单独显示 —— 它们的形状跟着宿主一起画
            # (分开画会重叠两份线框,看起来像没对齐)
            objects = [obj for obj in context.scene.objects
                       if obj.type == "MESH"
                       and not asset_export.is_collision_piece(obj)]
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
            # 预览碰撞块 = 预览它的**宿主**(整组一起看,才知道头/身/腿拼得对不对)
            root = active
            if asset_export.is_collision_piece(active):
                root = asset_export.collision_owner(active) or active
            objects = [root]

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


class GK_OT_PreviewColliders(bpy.types.Operator):
    """导出前预览碰撞体:把**这次导出会生成**的全部碰撞体显示成线框。"""

    bl_idname = "gk.preview_colliders"
    bl_label = "导出前预览碰撞体"
    bl_description = ("把这次导出会生成的所有碰撞体显示成线框"
                      "(只显示、不会导出);看一眼有没有漏设 / 尺寸不对")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        if context.scene is None:
            return {"CANCELLED"}
        # 先清干净再重建,反复点不会叠出残留
        _remove_previews(context.scene)
        is_3d = str(getattr(context.scene.gk_settings, "dimension", "3")) != "2"

        made = 0
        shape_total = 0
        for obj in context.scene.objects:
            if getattr(obj, "type", "") != "MESH":
                continue
            # 碰撞块不单独画:它们的形状跟着宿主一起显示(和导出结构一致)
            if asset_export.is_collision_piece(obj):
                continue
            preview = asset_export.collision_preview_mesh(
                obj, is_3d, getattr(obj, "gk_physics", None))
            if preview is None:
                continue
            _make_preview_object(context, obj, preview, is_3d)
            made += 1
            # 数准实际会生成的形状(形状设成『无』的块不算)
            for piece in asset_export.collision_pieces(obj):
                if asset_export.build_collision(
                        piece, is_3d, getattr(piece, "gk_physics", None)):
                    shape_total += 1
        shape_total += made

        if not made:
            _report(self, "WARNING",
                    "没有可预览的碰撞体(碰撞形状是不是都设成了『无』?)")
            return {"FINISHED"}
        _report(self, "INFO",
                f"这次导出会生成 {shape_total} 个碰撞形状"
                f"(分布在 {made} 个物体上);线框只显示、不会导出。"
                f"看完点『隐藏碰撞体』清理")
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


class GK_OT_ReportCopy(bpy.types.Operator):
    """把『上次导出报告』整份复制成文本(剪贴板 + 文本编辑器 + 控制台)。

    用户 2026-10-05 第二十三轮:"导出报告可视化"。出问题时他要发出来,
    所以三处都给:剪贴板(直接粘)、文本编辑器(长的能慢慢看)、
    控制台(无头 / 剪贴板不可用时靠它)。
    """

    bl_idname = "gk.export_report_copy"
    bl_label = "复制导出报告"
    bl_description = ("把上次导出的报告(错误 / 警告 / 怎么修)复制成纯文本 —— "
                      "直接粘出来就能发")
    bl_options = {"REGISTER"}

    def execute(self, context):
        report = export_report.load(getattr(context, "scene", None))
        text = export_report.report_text(report)
        copied = False
        try:
            context.window_manager.clipboard = text
            copied = True
        except Exception:                                 # noqa: BLE001
            pass
        _open_in_text_editor(context, text, "导出报告")
        print(text)
        self.report({"INFO"}, "导出报告已复制到剪贴板(也放进文本编辑器)"
                    if copied else "导出报告已打印到控制台(剪贴板不可用)")
        return {"FINISHED"}


class GK_OT_ReportFocus(bpy.types.Operator):
    """跳到报告里那条问题说的节点(选中 + 视图定位)。"""

    bl_idname = "gk.export_report_focus"
    bl_label = "定位到出问题的节点"
    bl_description = "在节点编辑器里选中并聚焦报告里提到的那个节点"
    bl_options = {"REGISTER"}

    node_name: StringProperty(name="节点名", default="")

    def execute(self, context):
        wanted = str(self.node_name or "").strip()
        if not wanted:
            self.report({"WARNING"}, "报告里这条没说节点名")
            return {"CANCELLED"}
        # ── 在哪张图里找 ────────────────────────────────────────────────
        # 先看用户正开着的那张,找不到就在**所有**游戏逻辑图里找 ——
        # 报告可能是上一次会话导的,那时开着的图不一定是现在这张。
        # 比较用**名字**(Blender 的 ID 包装对象别拿 in / == 当可靠判断)。
        trees = []
        current = _tree_of(self, context)
        if current is not None:
            trees.append(current)
        seen = {tree.name for tree in trees}
        for group in bpy.data.node_groups:
            if group.name in seen:
                continue
            if getattr(group, "bl_idname", "") != "GKGameLogicTree":
                continue
            trees.append(group)
        for tree in trees:
            node = tree.nodes.get(wanted)
            if node is None:
                continue
            for other in tree.nodes:
                other.select = False
            node.select = True
            tree.nodes.active = node
            try:
                bpy.ops.node.view_selected()
            except Exception:                             # noqa: BLE001
                pass          # 无头 / 不在节点编辑器里:选中就够了
            self.report({"INFO"}, f"已定位到节点『{wanted}』")
            return {"FINISHED"}
        self.report({"WARNING"},
                    f"图里找不到节点『{wanted}』—— 可能改名或删掉了"
                    f"(报告是那次导出时记的)")
        return {"CANCELLED"}


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


class GK_OT_PickAnimName(bpy.types.Operator):
    """从列表里挑一个动画名填进节点。

    为什么不是 EnumProperty 下拉:Blender 不允许动态 items 的枚举配
    字符串默认值(`'default' can only be an integer when 'items' is a
    function`)。所以做成"文本框 + 选…按钮"。

    列表由 `nodes.action_ownership` 的动作归属分析生成:
    **属于当前物体**的动作带 ★ 排在最前面。
    """

    bl_idname = "gk.pick_anim_name"
    bl_label = "选择动画"
    bl_description = "从当前文件的所有动画里挑一个(带 ★ 的是这个物体自己的)"
    bl_options = {"REGISTER", "UNDO"}

    node_name: StringProperty(name="节点", default="")
    choice: StringProperty(name="动画名", default="")

    def invoke(self, context, event):
        if self.choice:
            return self.execute(context)
        context.window_manager.invoke_search_popup(self)
        # ── 必须显式返回一个集合(踩过的坑)──────────────────────────
        # 用户报:
        #     RuntimeError: class GK_OT_pick_anim_name, function invoke:
        #     incompatible return value, Function.result expected a set,
        #     not a NoneType
        # 因为 `invoke_search_popup()` 自己**返回 None** ——
        # 直接把它的返回值当结果返回,Blender 就不认了。
        # 搜索弹窗是**异步**的:这里只负责把它弹出来,用户点选之后
        # 会再走一次 execute()。所以这里返回 RUNNING_MODAL。
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.choice:
            # 同样不能返回 None —— 任何路径都必须给出集合
            return {"CANCELLED"}
        tree = props.current_tree(context)
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None or not hasattr(node, "gk_anim_name"):
            self.report({"WARNING"}, "没找到目标节点,动画名没写进去")
            return {"CANCELLED"}
        node.gk_anim_name = self.choice
        # ── 同时把**原生动作指针**也指上(踩过的坑)──────────────────
        # `_animation_name_of` 的优先级是"动作指针 > 文本框",用户先用这个
        # 列表选了名字、之后再动节点上那个原生选择器(或反过来),
        # 两边就会各说各话 —— 界面上是"没选动作",导出却用文本名。
        # 所以这里一并把对应的 Action 指上,让两个属性、界面三者一致。
        action = bpy.data.actions.get(self.choice)
        if action is not None and hasattr(node, "gk_anim_action"):
            node.gk_anim_action = action
        self.report({"INFO"}, f"动画名 -> {self.choice}")
        return {"FINISHED"}

    def draw(self, context):
        """搜索弹窗的每一行。

        沿用 `gk.find_node` 那套"分类 · 名字"的写法 ——
        动作多的时候只写名字分不清。
        """
        tree = props.current_tree(context)
        node = tree.nodes.get(self.node_name) if tree else None
        # 节点的『动画播放器』属性指向哪个物体,就拿它分析归属。
        # 拿不到就列全部(至少比手打强)。
        target = None
        player = getattr(node, "gk_player", "") if node is not None else ""
        if player:
            target = bpy.data.objects.get(player)
        for ident, label, _desc in node_lib.discover_actions(target):
            op = self.layout.operator("gk.pick_anim_name", text=label)
            op.node_name = self.node_name
            op.choice = ident


class GK_OT_PickAnimTree(bpy.types.Operator):
    """从列表里挑一张动画树填进节点。

    和 `gk.pick_anim_name` 同一套做法(见那里的说明)——
    手打树名打错了导出时只会说"找不到动画树",不如直接选。
    """

    bl_idname = "gk.pick_anim_tree"
    bl_label = "选择动画树"
    bl_description = "从文件里的动画树中挑一张(标签里带状态数量)"
    bl_options = {"REGISTER", "UNDO"}

    node_name: StringProperty(name="节点", default="")
    choice: StringProperty(name="树名", default="")

    def invoke(self, context, event):
        if self.choice:
            return self.execute(context)
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.choice:
            return {"CANCELLED"}
        tree = props.current_tree(context)
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None or not hasattr(node, "gk_tree_name"):
            self.report({"WARNING"}, "没找到目标节点")
            return {"CANCELLED"}
        # choice 用的就是树名(空 = 自动)
        node.gk_tree_name = self.choice
        self.report({"INFO"}, f"动画树 -> {self.choice or '自动'}")
        return {"FINISHED"}

    def draw(self, context):
        for ident, label, _desc in node_lib.animation_tree_items():
            op = self.layout.operator("gk.pick_anim_tree", text=label)
            op.node_name = self.node_name
            op.choice = ident


class GK_OT_PickUiDesign(bpy.types.Operator):
    """从列表里挑一张界面设计图填进『界面_套用设计』。

    和 `GK_OT_PickAnimTree` 一模一样的一套(见那里的说明)——
    手打树名打错了只会在导出报告里说"找不到设计图",不如直接选。
    """

    bl_idname = "gk.pick_ui_design"
    bl_label = "选择界面设计图"
    bl_description = "从文件里的界面设计图中挑一张(标签里带控件数量)"
    bl_options = {"REGISTER", "UNDO"}

    node_name: StringProperty(name="节点", default="")
    choice: StringProperty(name="设计图", default="")

    def invoke(self, context, event):
        if self.choice:
            return self.execute(context)
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.choice:
            return {"CANCELLED"}
        tree = props.current_tree(context)
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None or not hasattr(node, "gk_design"):
            self.report({"WARNING"}, "没找到目标节点")
            return {"CANCELLED"}
        # choice 用的就是树名(空 = 自动用第一张)
        node.gk_design = self.choice
        self.report({"INFO"}, f"界面设计图 -> {self.choice or '自动'}")
        return {"FINISHED"}

    def draw(self, context):
        for ident, label, _desc in node_lib.ui_design_tree_items():
            op = self.layout.operator("gk.pick_ui_design", text=label)
            op.node_name = self.node_name
            op.choice = ident


class GK_OT_PickAiTree(bpy.types.Operator):
    """从列表里挑一张 AI 图填进『AI_启用』。

    和 `GK_OT_PickAnimTree` / `GK_OT_PickUiDesign` 一模一样的一套 ——
    手打树名打错了只会在导出报告里说"找不到 AI 图",不如直接选。
    """

    bl_idname = "gk.pick_ai_tree"
    bl_label = "选择 AI 图"
    bl_description = "从文件里的 AI 图中挑一张(标签里带状态数量)"
    bl_options = {"REGISTER", "UNDO"}

    node_name: StringProperty(name="节点", default="")
    choice: StringProperty(name="AI 图", default="")

    def invoke(self, context, event):
        if self.choice:
            return self.execute(context)
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.choice:
            return {"CANCELLED"}
        tree = props.current_tree(context)
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None or getattr(node, "bl_idname", "") != "GKNodeAiEnable":
            self.report({"WARNING"}, "没找到『AI_启用』节点")
            return {"CANCELLED"}
        # choice 用的就是树名(空 = 自动用第一张)
        node.gk_tree_name = self.choice
        self.report({"INFO"}, f"AI 图 -> {self.choice or '自动'}")
        return {"FINISHED"}

    def draw(self, context):
        for ident, label, _desc in node_lib.ai_tree_items():
            op = self.layout.operator("gk.pick_ai_tree", text=label)
            op.node_name = self.node_name
            op.choice = ident


class GK_OT_PickAiState(bpy.types.Operator):
    """从**这张 AI 图的状态**里挑一个,填进转场的『从』/『到』。

    和 `GK_OT_PickAnimState` 同一套 —— 连线是首选,但手打状态名太容易错,
    错了同样会静默失效(状态机不动),所以给下拉兜底。
    """

    bl_idname = "gk.pick_ai_state"
    bl_label = "选择状态"
    bl_description = "从这张 AI 图的状态里挑一个(连线优先,这只是兜底)"
    bl_options = {"REGISTER", "UNDO"}

    node_name: StringProperty(name="节点", default="")
    prop_name: StringProperty(name="属性", default="gk_from_state")
    choice: StringProperty(name="状态名", default="")

    def invoke(self, context, event):
        if self.choice:
            return self.execute(context)
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.choice:
            return {"CANCELLED"}
        tree = props.current_tree(context)
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None or not hasattr(node, self.prop_name):
            self.report({"WARNING"}, "没找到目标节点,状态名没写进去")
            return {"CANCELLED"}
        setattr(node, self.prop_name, self.choice)
        self.report({"INFO"}, f"{self.prop_name} -> {self.choice}")
        return {"FINISHED"}

    def draw(self, context):
        tree = props.current_tree(context)
        node = tree.nodes.get(self.node_name) if tree else None
        # 状态在**当前这张树**里找(转场的『从』『到』只能是同一张图的状态)
        owner = getattr(node, "id_data", None) if node is not None else None
        for ident, label, description in node_lib.ai_state_items(owner):
            op = self.layout.operator("gk.pick_ai_state", text=label)
            op.node_name = self.node_name
            op.prop_name = self.prop_name
            op.choice = ident


def apply_captured_key(context, node_name, key, tree=None,
                       key_prop="gk_key", action_prop="gk_action"):
    """把"按一下键"捕获到的键写进节点。返回 (成功?, 提示文字)。

    ── 为什么单独抽成函数(不是为了好看)────────────────────────────
    Blender 的算子**不能直接 new**(`bpy_struct.__new__(struct): expected a
    single argument`),模态算子又只能在界面上按出来 —— 逻辑留在 modal 里面
    就永远测不到 (`context.space_data` 在没有界面时是 None,连树都找不到)。
    抽出来之后测试可以直接把树喂进来验证。

    key_prop / action_prop:「输入_方向」这种一个节点有**四组**键的,
    每组各点各的按钮 —— 按钮上带着"写到哪个属性"。
    """
    if tree is None:
        tree = props.current_tree(context)
    node = tree.nodes.get(node_name) if tree is not None else None
    if node is None or not hasattr(node, key_prop):
        return False, "没找到目标节点,按键没绑上"
    key = str(key or "").strip()
    if not key:
        return False, "没识别出按键"
    setattr(node, key_prop, key)
    # 动作名留空就按按键名生成 —— 用户不用想名字。
    # 已经自动生成过的(key_xxx)也跟着新键改名,否则"先绑 W 又改绑 Q"
    # 时动作名还停在 key_w,看着像没生效。
    current = str(getattr(node, action_prop, "") or "").strip()
    if not current or current.startswith("key_"):
        setattr(node, action_prop, "key_" + key.lower())
    return True, (f"{key_prop} -> {key}"
                  f"(动作名:{getattr(node, action_prop)})")


# 等按键期间会**不停刷进来**的事件 —— 必须忽略,否则鼠标一动就当按了键。
# (鼠标移动、计时器、窗口激活/失焦、数位板、3D 鼠标…)
_IGNORED_EVENT_TYPES = {
    "MOUSEMOVE", "INBETWEEN_MOUSEMOVE", "TIMER", "TIMER0", "TIMER1",
    "TIMER2", "TIMERJOBS", "TIMER_REPEAT", "WINDOW_DEACTIVATE",
    "WINDOW_ACTIVATE", "NDOF_MOTION", "HEADER", "TRACKPADPAN",
    "TRACKPADZOOM", "MOUSEROTATE", "NOTHING", "EVENT_NONE",
    # 鼠标键不走这里(鼠标有专门的节点;绑到"键盘"节点上只会让人困惑)
    "LEFTMOUSE", "MIDDLEMOUSE", "RIGHTMOUSE", "BUTTON4MOUSE",
    "BUTTON5MOUSE", "BUTTON6MOUSE", "BUTTON7MOUSE",
}

# 这些键名和 Blender 的语义对不上/不该绑,按了就当取消或忽略
_CANCEL_KEYS = {"ESC"}


def key_from_event(event):
    """从 Blender 事件里取出"用户按了哪个键"。

    返回 `(键名 或 None, 是否取消)` —— None 表示"这个事件不算按键,继续等"。

    ── 为什么不能判断 `event.type == "KEYDOWN"`(实测踩过的坑)────────
    Blender 里**根本没有** `KEYDOWN` 这个事件类型。按 W 时是:

        event.type   == "W"          # 键名就是类型名
        event.value  == "PRESS"      # 按下 / RELEASE 松开

    第一版写的是 `if event.type == "KEYDOWN"`,于是**按字母键毫无反应**:
    模态一直挂着(状态栏那句"按一下你要绑的键"不消失),
    用户 2026-10-03 截图报"输入按键部分未正常工作"——就是这个。
    正确做法:`event.value == "PRESS"` + 排除鼠标/移动/计时器。

    抽成独立函数是为了**能测**:Blender 的模态算子没法在无界面下按出来,
    但喂一个假事件进去判断"认不认"是可以测的。
    """
    kind = str(getattr(event, "type", "") or "")
    value = str(getattr(event, "value", "") or "")
    if kind in _CANCEL_KEYS:
        return None, True
    if not kind or kind in _IGNORED_EVENT_TYPES or kind.startswith("TIMER"):
        return None, False
    # 松开、拖拽、双击都跳过 —— 只在"按下"那一刻取
    if value not in ("PRESS", "CLICK"):
        return None, False
    return kind, False


def curve_object_items(_self=None, _context=None):
    """场景里所有**曲线**物体的名字(给『相机_路径』挑轨道用)。"""
    items = []
    for obj in getattr(bpy.data, "objects", ()) or ():
        if getattr(obj, "type", "") == "CURVE":
            items.append((obj.name, obj.name, "拿它当运镜轨道", "CURVE_DATA", 0))
    if not items:
        items.append(("", "(场景里没有曲线)", "先加一条曲线:添加 → 曲线 → 路径/贝塞尔",
                      "ERROR", 0))
    return items


def asset_name_items(_self=None, _context=None):
    """场景里所有**静态资产**的名字(给『资产_创建实例』挑资产用)。

    顺带把"摆了几份"写进列表 —— 用户 2026-10-05:"给实例添加显示多少在
    场景中这样的显示信息"。挑的时候一眼就知道这个资产现在场上有几份。
    """
    items = []
    try:
        from . import asset_export
        assets = asset_export.collect_assets(bpy.context.scene)
    except Exception:                                     # noqa: BLE001
        assets = {}
    for name in sorted(assets):
        entry = assets[name]
        count = len(entry.get("members") or [])
        note = f"场景里共 {count} 份"
        if entry.get("source") is None:
            note += "(⚠ 没有本体:先取消一份的『作为实例』)"
        items.append((name, f"{name}({count} 份)", note, "ASSET_MANAGER", 0))
    if not items:
        items.append(("", "(还没有资产)",
                      "选中模型 -> 『游戏物理』面板 -> 填『资产名』",
                      "ERROR", 0))
    return items


class GK_OT_PickAsset(bpy.types.Operator):
    """挑一个静态资产(名字写回节点上的『资产名』)。"""

    bl_idname = "gk.pick_asset"
    bl_label = "选资产"
    bl_description = "把场景里的一个静态资产填到这个节点上"
    bl_property = "asset_name"      # 弹出搜索框时默认聚焦这个

    node_name: StringProperty(name="节点", default="")
    asset_prop: StringProperty(name="属性名", default="gk_asset")
    asset_name: EnumProperty(name="资产", items=asset_name_items)

    def invoke(self, context, _event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        tree = props.current_tree(context)
        node = tree.nodes.get(self.node_name) if tree is not None else None
        prop_name = self.asset_prop or "gk_asset"
        if node is None or not hasattr(node, prop_name):
            self.report({"WARNING"}, "没找到目标节点(或它没有该资产栏位)")
            return {"CANCELLED"}
        if not self.asset_name:
            self.report({"WARNING"}, "场景里还没有静态资产 —— "
                                     "选中模型,在『游戏物理』面板填『资产名』")
            return {"CANCELLED"}
        setattr(node, prop_name, self.asset_name)
        self.report({"INFO"}, f"资产 -> {self.asset_name}")
        return {"FINISHED"}


class GK_OT_PickCurve(bpy.types.Operator):
    """挑一条曲线(照 UPBGE 那种原生选择器的体验)。

    ── 为什么带 `curve_prop`(用户 2026-10-04 第十二轮)──────────────
    一个节点上可能有**好几条**曲线栏位:相机_切换 的过渡曲线 / 运镜轨道、
    时间轴 的进度曲线 / 每条轨道的曲线 / 曲线轨道。按钮上写清楚写回哪个
    属性,就不用为每个栏位各写一个算子。
    """

    bl_idname = "gk.pick_curve"
    bl_label = "选曲线"
    bl_description = "把这条曲线设为当前栏位的曲线"
    bl_property = "curve_name"      # 弹出搜索框时默认聚焦这个

    node_name: StringProperty(name="节点", default="")
    curve_prop: StringProperty(name="属性名", default="gk_curve")
    curve_name: EnumProperty(name="曲线", items=curve_object_items)

    def invoke(self, context, _event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        tree = props.current_tree(context)
        node = tree.nodes.get(self.node_name) if tree is not None else None
        prop_name = self.curve_prop or "gk_curve"
        if node is None or not hasattr(node, prop_name):
            self.report({"WARNING"}, "没找到目标节点(或这个节点没有该曲线栏位)")
            return {"CANCELLED"}
        if not self.curve_name:
            self.report({"WARNING"}, "场景里还没有曲线物体")
            return {"CANCELLED"}
        setattr(node, prop_name, self.curve_name)
        self.report({"INFO"}, f"曲线 -> {self.curve_name}")
        return {"FINISHED"}


def image_items(_self=None, _context=None):
    """工程里所有**图片**的名字(给『界面_图片』/ 对话框立绘挑图用)。

    顺带把尺寸写进条目说明里 —— 挑了多大一张图一眼能看到
    (挑完才发现是 4096 的大图,缩放到 HUD 上糊成一团,那种事谁都不想干)。
    """
    items = []
    for image in getattr(bpy.data, "images", ()) or ():
        name = getattr(image, "name", "")
        # Render Result / Viewer Node 是渲染输出,不是素材,别列进来
        if not name or name in {"Render Result", "Viewer Node"}:
            continue
        size = getattr(image, "size", None)
        width = int(size[0]) if size else 0
        height = int(size[1]) if size else 0
        hint = f"{width}×{height}" if width else "没有像素数据(检查图片是否丢失)"
        items.append((name, name, hint, "IMAGE_DATA", 0))
    if not items:
        items.append(("", "(工程里还没有图片)",
                      "先在 Blender 里导入一张:文件 → 导入 → 图像", "ERROR", 0))
    return items


class GK_OT_PickImage(bpy.types.Operator):
    """挑一张图片(『界面_图片』/『界面_对话框』的立绘)。

    和『选曲线』一模一样的形式:弹出搜索框,挑中的名字写回节点的
    `gk_image`。导出时那张图会落盘成 `art/ui_xxx.png` 带进 Godot ——
    所以用户不用自己去拷文件、也不用手敲 res:// 路径。
    """

    bl_idname = "gk.pick_image"
    bl_label = "选图片"
    bl_description = "把这张图片设为当前栏位的图片(导出时会带进 Godot)"
    bl_property = "image_name"

    node_name: StringProperty(name="节点", default="")
    image_prop: StringProperty(name="属性名", default="gk_image")
    image_name: EnumProperty(name="图片", items=image_items)

    def invoke(self, context, _event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        tree = props.current_tree(context)
        node = tree.nodes.get(self.node_name) if tree is not None else None
        prop_name = self.image_prop or "gk_image"
        if node is None or not hasattr(node, prop_name):
            self.report({"WARNING"}, "没找到目标节点(或这个节点没有该图片栏位)")
            return {"CANCELLED"}
        if not self.image_name:
            self.report({"WARNING"}, "工程里还没有可选的图片")
            return {"CANCELLED"}
        setattr(node, prop_name, self.image_name)
        self.report({"INFO"}, f"图片 -> {self.image_name}")
        return {"FINISHED"}


# ── 音频资产(第四十三轮:『游戏资产』图的『资产_声音卡』)──────────────
def sound_asset_items(_self=None, _context=None):
    """资产库里所有声音卡,给音效节点卡上的『从资产库挑』用。

    条目值是 **res:// 路径** —— 直接写进节点的 `gk_path`,和导出时
    复制进 audio/ 用的那个路径**是同一个函数**算出来的,不会对不上。
    """
    try:
        from .nodes import nodes as node_lib
        entries = node_lib.sound_card_entries()
    except Exception:                                     # noqa: BLE001
        entries = []
    items = []
    for name, path, res in entries:
        if not res:
            continue
        label = name or os.path.basename(path) or res
        hint = ("导出时复制进 audio/" if path
                else "只登记路径(自己放进项目)")
        items.append((res, label, hint, "SOUND", 0))
    if not items:
        items.append(("", "(资产库里还没有声音卡)",
                      "侧栏『游戏逻辑』->『游戏资产』->『打开/新建资产库』"
                      "-> 加一张『资产_声音卡』", "ERROR", 0))
    return items


class GK_OT_PickSound(bpy.types.Operator):
    """从资产库挑一个音频,写进音效节点的『音频路径』。"""

    bl_idname = "gk.pick_sound"
    bl_label = "从资产库挑声音"
    bl_description = "把这份资产的声音路径写进节点的『音频路径』"
    bl_property = "sound_path"

    node_name: StringProperty(name="节点", default="")
    sound_prop: StringProperty(name="属性名", default="gk_path")
    sound_path: EnumProperty(name="声音", items=sound_asset_items)

    def invoke(self, context, _event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        tree = props.current_tree(context)
        node = tree.nodes.get(self.node_name) if tree is not None else None
        prop_name = self.sound_prop or "gk_path"
        if node is None or not hasattr(node, prop_name):
            _report(self, "WARNING", "没找到目标节点(或这个节点没有音频栏位)")
            return {"CANCELLED"}
        if not self.sound_path:
            _report(self, "WARNING", "资产库里还没有声音卡")
            return {"CANCELLED"}
        setattr(node, prop_name, self.sound_path)
        _report(self, "INFO", f"音频 -> {self.sound_path}")
        return {"FINISHED"}


class GK_OT_AssetPickSound(bpy.types.Operator):
    """『资产_声音卡』上挑一个本地音频文件(带进 Godot 的源头)。"""

    bl_idname = "gk.asset_pick_sound"
    bl_label = "挑音频文件"
    bl_description = ("挑一个本地音频(wav / ogg / mp3);"
                      "导出时复制进项目的 audio/ 目录")
    bl_options = {"REGISTER", "UNDO"}

    node_name: StringProperty(name="节点", default="")
    filepath: StringProperty(name="文件", subtype="FILE_PATH", default="")
    filter_glob: StringProperty(default="*.wav;*.ogg;*.mp3",
                                options={"HIDDEN"})

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        from . import sound_assets
        tree = props.current_tree(context)
        node = tree.nodes.get(self.node_name) if tree is not None else None
        path = str(self.filepath or "").strip()
        if node is None or not hasattr(node, "gk_file"):
            _report(self, "WARNING", "没找到那张声音卡")
            return {"CANCELLED"}
        if not path:
            return {"CANCELLED"}
        if not sound_assets.is_sound_file(path):
            _report(self, "ERROR", f"只支持 wav / ogg / mp3: {path}")
            return {"CANCELLED"}
        node.gk_file = path
        node.gk_res = sound_assets.sound_res_path(path)
        if not str(getattr(node, "gk_name", "") or "").strip():
            node.gk_name = os.path.splitext(os.path.basename(path))[0]
        _report(self, "INFO",
                f"音频资产 -> {node.gk_res}(导出会复制进 audio/)")
        return {"FINISHED"}


class GK_OT_PreviewCameraPath(bpy.types.Operator):
    """**真预览**:在视口里把相机沿曲线(或过渡)跑一遍。

    ── 为什么要有它(用户 2026-10-04 第十二轮)──────────────────────
    用户原话:"现在设计的这个预览不了 —— 想要做的曲线运动动画的话,
    还要在节点里连一大堆,那么这个怎么设计更好"。
    光在节点里填一个曲线名字,用户没法知道镜头到底怎么飞。所以这里
    **模态**地把相机跑一遍,所见即所得。

    三种情况都认:
      * 『相机_路径』:沿它选的那条曲线走一遍(勾了循环就一直走)
      * 『相机_切换』+『沿曲线运镜』:从当前机位沿轨道飞到目标机位
      * 『相机_切换』只填了『过渡』:走直线,但**按你选的曲线缓动**

    跑完 / 按 Esc 之后,相机的位置朝向、以及"当前活动相机"都会**还原**,
    不会改坏场景,也不会产生任何游戏代码。
    """

    bl_idname = "gk.preview_camera_path"
    bl_label = "预览运镜"
    bl_description = "在视口里把相机沿曲线跑一遍(Esc 取消;跑完自动还原)"
    bl_options = {"REGISTER", "UNDO"}

    node_name: StringProperty(name="节点", default="")

    # ---- 工具 ----
    @staticmethod
    def _ease(preset, t):
        """档位 -> 缓动比例(和 Godot 的 TRANS/EASE 用同一套公式)。"""
        import math
        t = max(0.0, min(1.0, t))
        if preset == "EASE_IN":
            return 1.0 - math.cos(t * math.pi / 2.0)
        if preset == "EASE_OUT":
            return math.sin(t * math.pi / 2.0)
        if preset == "EASE_IN_OUT":
            return -(math.cos(math.pi * t) - 1.0) / 2.0
        if preset == "BACK":
            c1, c3 = 1.70158, 2.70158
            s = t - 1.0
            return 1.0 + c3 * s * s * s + c1 * s * s
        if preset == "BOUNCE":
            n1, d1 = 7.5625, 2.75
            if t < 1.0 / d1:
                return n1 * t * t
            if t < 2.0 / d1:
                t -= 1.5 / d1
                return n1 * t * t + 0.75
            if t < 2.5 / d1:
                t -= 2.25 / d1
                return n1 * t * t + 0.9375
            t -= 2.625 / d1
            return n1 * t * t + 0.984375
        return t

    @staticmethod
    def _sample_ease(points, t):
        """一维曲线的 [0,1] 采样(点在采样时已经归一化过,见 curve_assets)。"""
        if not points:
            return t
        t = max(0.0, min(1.0, t))
        for index in range(len(points) - 1):
            t0, v0 = points[index]
            t1, v1 = points[index + 1]
            if t <= t1 or index == len(points) - 2:
                span = t1 - t0
                ratio = 0.0 if span <= 1e-9 else (t - t0) / span
                return v0 + (v1 - v0) * ratio
        return points[-1][1]

    @staticmethod
    def _sample_path(points, t):
        """三维轨道按**点数**等距插值(采样时已经等距,见 curve_assets)。"""
        if len(points) < 2:
            return points[0] if points else (0.0, 0.0, 0.0)
        t = max(0.0, min(1.0, t))
        scaled = t * (len(points) - 1)
        index = int(scaled)
        index = min(index, len(points) - 2)
        ratio = scaled - index
        a, b = points[index], points[index + 1]
        return tuple(a[i] + (b[i] - a[i]) * ratio for i in range(3))

    def _target_camera(self, scene, node):
        """节点上选的相机(留空 = 场景的当前活动相机)。"""
        raw = str(getattr(node, "gk_target", "") or "").strip()
        objects = getattr(bpy.data, "objects", None)
        if raw and raw != "__self__" and objects is not None:
            found = objects.get(raw) if hasattr(objects, "get") else None
            if found is not None and getattr(found, "type", "") == "CAMERA":
                return found
            if found is not None:
                self.report({"WARNING"}, f"节点上的『相机』是 {raw},它不是相机物体")
        return getattr(scene, "camera", None)

    def invoke(self, context, _event):
        tree = props.current_tree(context)
        node = tree.nodes.get(self.node_name) if tree is not None else None
        if node is None:
            self.report({"WARNING"}, "没找到节点 —— 先在节点编辑器里选中它再点预览")
            return {"CANCELLED"}
        scene = context.scene
        camera = self._target_camera(scene, node)
        if camera is None:
            self.report({"WARNING"}, "没有可预览的相机 —— 给节点选一台相机,"
                                     "或者先设一台当前相机(小键盘 0)")
            return {"CANCELLED"}

        # ---- 这次要跑什么 ----
        self._points = []
        self._ease_points = []
        self._preset = str(getattr(node, "gk_blend_trans", "LINEAR") or "LINEAR")
        self._duration = 2.5
        self._loop = False
        self._look_forward = False
        self._look_target = None
        curve_name = ""
        path_name = ""
        if node.bl_idname == "GKNodeCameraPath":
            curve_name = str(getattr(node, "gk_curve", "") or "").strip()
            self._loop = bool(getattr(node, "gk_loop", False))
            speed = float(getattr(node, "gk_speed", 0.0) or 0.0)
            self._duration = 3.0 if speed <= 0.0 else min(
                10.0, 100.0 / max(speed, 1.0))
            mode = str(getattr(node, "gk_look_mode", "FORWARD"))
            self._look_forward = mode == "FORWARD"
            if mode == "TARGET":
                raw = str(getattr(node, "gk_look_target", "") or "").strip()
                objects = getattr(bpy.data, "objects", None)
                if raw and objects is not None and hasattr(objects, "get"):
                    self._look_target = objects.get(raw)
        else:
            # 相机_切换:运镜轨道优先;没有轨道就按"过渡秒数"走直线
            if str(getattr(node, "gk_path_mode", "DIRECT")) == "CURVE":
                path_name = str(getattr(node, "gk_path_curve", "") or "").strip()
            blend = float(getattr(node, "gk_blend", 0.0) or 0.0)
            self._duration = max(0.5, min(10.0, blend if blend > 0 else 2.5))
            if self._preset == "CUSTOM":
                self._ease_points = curve_assets.sample_ease_curve(
                    str(getattr(node, "gk_blend_curve", "") or "").strip()) or []
            self._path_mode = True
        if path_name:
            self._points = curve_assets.sample_path_points(path_name)
            if len(self._points) < 2:
                self.report({"WARNING"}, f"曲线『{path_name}』采不到点 —— "
                                         f"确认它是曲线物体,而且有至少两个点")
                return {"CANCELLED"}
        elif curve_name:
            self._points = curve_assets.sample_path_points(curve_name)
            if len(self._points) < 2:
                self.report({"WARNING"}, f"曲线『{curve_name}』采不到点 —— "
                                         f"确认它是曲线物体,而且有至少两个点")
                return {"CANCELLED"}
        elif node.bl_idname == "GKNodeCameraPath":
            self.report({"WARNING"}, "这条『相机_路径』还没选曲线 —— "
                                     "点节点上的『选曲线』挑一条")
            return {"CANCELLED"}

        # ---- 起点:当前活动相机(和它不同的话) ----
        self._scene = scene
        self._camera = camera
        self._saved_camera = getattr(scene, "camera", None)
        self._saved_matrix = camera.matrix_world.copy()
        self._start_matrix = None
        self._start_quat = None
        if node.bl_idname != "GKNodeCameraPath" and self._saved_camera is not None \
                and self._saved_camera is not camera:
            self._start_matrix = self._saved_camera.matrix_world.copy()
        if self._start_matrix is not None:
            self._start_quat = self._start_matrix.to_quaternion()
        self._end_quat = camera.matrix_world.to_quaternion()
        # 让视口切到这台相机 —— 否则用户什么都看不到
        scene.camera = camera

        self._start_time = time.monotonic()
        self._hold_until = None
        self._timer = context.window_manager.event_timer_add(
            1.0 / 60.0, window=context.window)
        context.window_manager.modal_handler_add(self)
        if context.area is not None:
            context.area.header_text_set(
                "预览运镜中…(按 Esc 取消;跑完自动还原相机位置)")
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        import mathutils
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._restore(context)
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"RUNNING_MODAL"}
        elapsed = time.monotonic() - self._start_time
        raw = elapsed / max(self._duration, 0.001)
        if raw >= 1.0 and not self._loop:
            # 到点后**停一下**让用户看清最后一帧,再还原(直接还原的话
            # 最后一帧一闪而过,等于没看到终点机位)
            if self._hold_until is None:
                self._hold_until = time.monotonic() + 0.6
            if time.monotonic() >= self._hold_until:
                self._restore(context)
                self.report({"INFO"}, "预览完成(相机位置已还原)")
                return {"FINISHED"}
            raw = 1.0
        else:
            raw = raw % 1.0 if self._loop else min(raw, 1.0)
        if self._ease_points:
            t = self._sample_ease(self._ease_points, raw)
        else:
            t = self._ease(self._preset, raw)

        matrix = self._camera.matrix_world.copy()
        # 位置:有轨道就走轨道;没轨道但从另一台相机切过来 -> 直线插值
        if self._points:
            position = mathutils.Vector(self._sample_path(self._points, t))
        elif self._start_matrix is not None:
            position = self._start_matrix.translation.lerp(
                self._saved_matrix.translation, t)
        else:
            position = self._camera.matrix_world.translation
        # 朝向:能插值就插值(从旧机位转到目标机位),否则沿轨道看前方/看向目标
        look = None
        if self._start_quat is not None:
            look = self._start_quat.slerp(
                self._end_quat, max(0.0, min(t, 1.0))).to_matrix().to_4x4()
        elif self._look_forward and self._points:
            ahead = mathutils.Vector(
                self._sample_path(self._points, min(t + 0.03, 1.0)))
            delta = ahead - position
            if delta.length > 0.001:
                look = delta.to_track_quat("-Z", "Y").to_matrix().to_4x4()
        elif self._look_target is not None \
                and getattr(self._look_target, "location", None) is not None:
            delta = self._look_target.matrix_world.translation - position
            if delta.length > 0.001:
                look = delta.to_track_quat("-Z", "Y").to_matrix().to_4x4()
        if look is None:
            look = matrix   # 不改朝向(『不改朝向』档)
        look.translation = position
        self._camera.matrix_world = look
        if context.area is not None:
            context.area.tag_redraw()
        return {"RUNNING_MODAL"}

    def _restore(self, context):
        if context.area is not None:
            context.area.header_text_set(None)
        try:
            context.window_manager.event_timer_remove(self._timer)
        except Exception:
            pass
        try:
            self._camera.matrix_world = self._saved_matrix
            if self._scene is not None:
                self._scene.camera = self._saved_camera
        except Exception:
            gk_log.exception("预览运镜还原相机时出错")
        if context.area is not None:
            context.area.tag_redraw()


class GK_OT_CaptureKey(bpy.types.Operator):
    """**按一下键盘上任意键**就把它绑到节点上(照 UPBGE 的 Press & Choose)。

    ── 为什么要这个(用户 2026-10-03 的要求)──────────────────────────
    用户原话:"在输入按键时,按下键盘任意键即可设置哪个键盘控制输入等,
    如 upbge 的 keyboard key 设计逻辑"。

    以前只能**打字**填动作名(`jump` / `move_right`),而且得记住
    "jump 绑空格、move_right 绑 D"这套约定 —— 名字打错了不报错,
    只是游戏里按了没反应。现在按一下键就绑上,名字自动生成。

    ── 实现要点 ────────────────────────────────────────────────────
    * 模态算子(invoke_modal):等在键盘上按一下,而不是弹个对话框让你选
    * 认的是 `event.value == "PRESS"`(**不是** `KEYDOWN`,见 key_from_event)
    * `Esc` 取消 —— 否则用户想退出都退不出来
    * 存的是 `event.type`(Blender 的键名,如 `W` / `SPACE` / `LEFT_SHIFT`),
      导出时再翻成 Godot 的 keycode(见 project.KEY_TO_GODOT)
    """

    bl_idname = "gk.capture_key"
    bl_label = "按一下键"
    bl_description = "按一下你想用的键(比如 W);按 Esc 取消"
    bl_options = {"REGISTER", "UNDO"}

    node_name: StringProperty(name="节点", default="")
    # 「输入_方向」一个节点有四组键,按钮上带着"写到哪个属性"
    key_prop: StringProperty(name="按键属性", default="gk_key")
    action_prop: StringProperty(name="动作属性", default="gk_action")

    def invoke(self, context, event):
        if context.area is not None:
            context.area.header_text_set("按一下你要绑的键(Esc 取消)")
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        key, cancel = key_from_event(event)
        if cancel:
            self._finish(context)
            return {"CANCELLED"}
        if key:
            self._apply(context, key)
            return {"FINISHED"}
        return {"RUNNING_MODAL"}

    def _finish(self, context):
        if context.area is not None:
            context.area.header_text_set(None)

    def _apply(self, context, key):
        self._finish(context)
        ok, message = apply_captured_key(context, self.node_name, key,
                                         key_prop=self.key_prop,
                                         action_prop=self.action_prop)
        self.report({"INFO" if ok else "WARNING"}, message)
        if ok:
            # 记下来给"最近用过"菜单用
            try:
                remember_node("GKNodeKey")
            except Exception:
                pass
            # ── 让界面立刻显示新绑的键(踩过的坑)──────────────────────
            # 节点属性是在"没有界面"的情况下被改的,不主动刷新的话
            # 节点上那个『按键』框还是空的,用户以为没绑上。
            try:
                if context.area is not None:
                    context.area.tag_redraw()
                for window in getattr(context, "window_manager", None).windows \
                        if getattr(context, "window_manager", None) else ():
                    screen = getattr(window, "screen", None)
                    for area in getattr(screen, "areas", ()) or ():
                        if area.type == "NODE_EDITOR":
                            area.tag_redraw()
            except Exception:
                pass


class GK_OT_PickAnimState(bpy.types.Operator):
    """从**这张动画树的状态**里挑一个,填进转场的『从』/『到』。

    ── 为什么要它(用户 2026-10-03 踩的坑)────────────────────────────
    他把状态的『转到』直接连到下一个状态的『进入』,转场的『上游』空着,
    导出后整台状态机是死的(`_physics_process` 只有 pass)。连线是首选,
    但**手打状态名太容易错**,而错了同样会静默失效。
    所以给下拉兜底:忘了连线也能从列表里点一个。
    """

    bl_idname = "gk.pick_anim_state"
    bl_label = "选择状态"
    bl_description = "从这张动画树的状态里挑一个(连线优先,这只是兜底)"
    bl_options = {"REGISTER", "UNDO"}

    node_name: StringProperty(name="节点", default="")
    prop_name: StringProperty(name="属性", default="gk_from_state")
    choice: StringProperty(name="状态名", default="")

    def invoke(self, context, event):
        if self.choice:
            return self.execute(context)
        context.window_manager.invoke_search_popup(self)
        # 搜索弹窗是异步的,这里必须显式返回集合(见 gk.pick_anim_name)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.choice:
            return {"CANCELLED"}
        tree = props.current_tree(context)
        node = tree.nodes.get(self.node_name) if tree else None
        if node is None or not hasattr(node, self.prop_name):
            self.report({"WARNING"}, "没找到目标节点,状态名没写进去")
            return {"CANCELLED"}
        setattr(node, self.prop_name, self.choice)
        self.report({"INFO"}, f"{self.prop_name} -> {self.choice}")
        return {"FINISHED"}

    def draw(self, context):
        tree = props.current_tree(context)
        node = tree.nodes.get(self.node_name) if tree else None
        # 状态在**当前这张树**里找(转场的『从』『到』只能是同一张树的状态)
        owner = getattr(node, "id_data", None) if node is not None else None
        for ident, label, description in node_lib.animation_tree_states(owner):
            op = self.layout.operator("gk.pick_anim_state", text=label)
            op.node_name = self.node_name
            op.prop_name = self.prop_name
            op.choice = ident


class GK_OT_TagAnimCategory(bpy.types.Operator):
    """给一个动作打分类标签(静止/移动/攻击…)。

    ── 为什么要它 ──────────────────────────────────────────────────
    动作一多(MMD 模型动辄几十个),下拉列表里全是名字,分不清哪个是
    "待机"哪个是"攻击"。打上分类之后:
      * 『选…』列表里按分类聚在一起、分类摆在最前
      * 导出的 GDScript 里会生成一张按分类索引的表(见 codegen),
        Godot 侧可以直接按分类随机/顺序挑动作

    标签存进 Action 的**自定义属性**(见 nodes.ANIM_CATEGORY_KEY),
    随 .blend 保存,禁用插件也不丢。
    """

    bl_idname = "gk.tag_anim_category"
    bl_label = "动作分类"
    bl_description = "给这个动作打个分类(只有一个分类,再点其它分类会改过去)"
    bl_options = {"REGISTER", "UNDO"}

    action_name: StringProperty(name="动作", default="")
    # 用 EnumProperty 而不是字符串:面板上用 `operatormenu_enum` 把
    # 六个分类直接列成菜单,点一下就打上,不用弹对话框。
    category: EnumProperty(
        name="分类",
        items=[(name, name, f"标记为『{name}』")
               for name in node_lib.ANIM_CATEGORY_NAMES],
        default=node_lib.ANIM_CATEGORY_NAMES[0],
    )
    clear: BoolProperty(name="清除分类", default=False)

    def execute(self, context):
        action = bpy.data.actions.get(self.action_name)
        if action is None:
            self.report({"WARNING"},
                        f"找不到动作『{self.action_name}』(可能已被删除)")
            return {"CANCELLED"}
        if self.clear:
            node_lib.set_action_category(action, "")
            self.report({"INFO"}, f"已清除『{action.name}』的动作分类")
            return {"FINISHED"}
        if not self.category:
            self.report({"WARNING"}, "没指定分类")
            return {"CANCELLED"}
        node_lib.set_action_category(action, self.category)
        self.report({"INFO"}, f"『{action.name}』-> {self.category}")
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

    # ── 进度反馈(用户要求)──────────────────────────────────────────
    # 实测:49 MB / 1056 骨骼的 MMD 工程要导 **26 秒**,其中
    # 14.2s + 12s 全在 Blender 的 glTF 导出器里。
    # 用户原话:"导出时间有点...或许可以加一个进度条,要不然以为卡住了"。
    #
    # 诚实地说明能做到什么程度:导出内部是**同步阻塞**的
    # (bpy.ops.export_scene.gltf 一调就回不来),所以这期间界面不重绘,
    # 进度条**不会动**。真正有效的是:
    #   1. 状态栏文字 —— 明确告诉用户"正在导出,这个模型大概要 20~30 秒"
    #   2. 结束后把每一步的**实测耗时**打出来,下次心里有数
    # 想让它真的动起来,得把整条流水线改成生成器 + 定时器驱动,
    # 那是另一个量级的改动(已记进 docs/待办清单.md)。
    def _progress(self, context, text):
        try:
            context.workspace.status_text_set(f"BlenderGameKit:{text}")
        except Exception:
            pass

    def _finish_progress(self, context):
        try:
            context.workspace.status_text_set(None)
        except Exception:
            pass

    def execute(self, context):
        started = time.time()
        # 先按模型规模给个预估,免得用户以为卡死
        mesh_count = sum(1 for o in context.scene.objects
                         if getattr(o, "type", "") == "MESH")
        estimate = "预计 20~30 秒" if mesh_count > 200 else "预计几秒"
        self._progress(context, f"正在导出({estimate},请勿关闭 Blender)")

        scene = context.scene
        settings = exporter.build_settings(scene)
        directory, ok = _resolve_project(self, context, create=True)
        if not ok:
            self._finish_progress(context)
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
        #
        # ── 多场景(用户 2026-10-04)──────────────────────────────────
        # 一个 .blend 里可以有好几个 Blender 场景(关卡1 / 关卡2 / ...)。
        # export_all_scenes 会遍历它们,每个场景各导一张 .tscn ——
        # 只有一棵场景时行为和以前完全一样(仍是 World.tscn)。
        try:
            outcome = asset_export.export_all_scenes(
                directory,
                dimension=settings.get("dimension", "3"),
                script_name=LAST_EXPORT.get("script_name") or None,
            )
        except Exception as exc:
            self._finish_progress(context)
            _report(self, "ERROR", f"导出失败: {type(exc).__name__}: {exc}")
            return {"CANCELLED"}

        if outcome.get("error"):
            self._finish_progress(context)
            _report(self, "ERROR", outcome["error"])
            return {"CANCELLED"}

        self._finish_progress(context)

        for warning in outcome.get("warnings", []):
            _report(self, "WARNING", warning)

        written = outcome.get("written", [])
        asset_export.ensure_project_file(directory)

        if not outcome.get("single") and not LAST_EXPORT.get("script_name"):
            # 多场景时可能看到"还没导出过脚本"的告警 —— 那不算问题:
            # 每个场景的逻辑图是这次导出时**自动**导出的。
            pass
        elif not LAST_EXPORT.get("script_name"):
            _report(self, "WARNING",
                    "还没有导出过脚本，先点『导出为 Godot 脚本』才能把逻辑挂到模型上")

        bounds = asset_export.object_bounds(objects)
        size = bounds["size"] if bounds else (0, 0, 0)
        elapsed = time.time() - started
        if outcome.get("single"):
            _report(self, "INFO",
                    f"已导出 {len(objects)} 个网格、{outcome['materials']} 个材质、"
                    f"{outcome['bodies']} 个碰撞体、{outcome['cameras']} 个相机、"
                    f"{outcome['lights']} 盏灯"
                    f"（包围盒 {size[0]:.2f}×{size[1]:.2f}×{size[2]:.2f}，"
                    f"耗时 {elapsed:.1f} 秒）")
        else:
            done = [e for e in outcome.get("scenes", []) if e["ok"]]
            names = "、".join(f"{e['scene']}→{e['scene_name']}.tscn" for e in done)
            _report(self, "INFO",
                    f"已导出 {len(done)}/{len(outcome.get('scenes', []))} 个场景:"
                    f"{names}（主场景 {outcome.get('main_scene', '')}，"
                    f"耗时 {elapsed:.1f} 秒）")
        for path in written:
            # 防御:written 里理论上不该有 None,但一旦混进来,
            # `os.path.basename(None)` 会崩,而且崩在"报告结果"这一步 ——
            # 用户看到的是一堆 traceback 而不是真正的错误原因(实测踩过)。
            if path:
                gk_log.info(f"写入 {os.path.basename(str(path))}")
        # 旧文件清理(第十三轮)要说出来 —— 删了东西却不说,用户会以为
        # "我的文件怎么没了"。没删就不提。
        deleted = outcome.get("deleted", [])
        if deleted:
            _report(self, "INFO",
                    f"已清理 {len(deleted)} 个上一次导出留下的旧文件:"
                    + "、".join(deleted[:6])
                    + ("…(详见导出日志)" if len(deleted) > 6 else ""))
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
            _auto_prepare(self, context, directory)

        # ── 启动前先说一句"要等几秒"(用户 2026-10-03:"打开的有点慢")────
        # 导出本身只要几毫秒(日志里有),慢的是 **Godot 引擎自己启动**
        # (Vulkan 初始化 + 资源导入,普通机器 3~6 秒)。
        # 那段时间 Blender 界面上什么都不会发生,很容易被当成"卡住了"。
        # 所以先把话说在前面;顺便提示第一次导入会更久。
        if not self.editor:
            _report(self, "INFO",
                    "正在启动 Godot(引擎启动约 3~6 秒;首次打开项目"
                    "还要额外导入资源,会更久一点)")

        opened, message = project.open_in_godot(directory, context,
                                                editor=bool(self.editor))
        if not opened:
            _report(self, "ERROR", message)
            return {"CANCELLED"}
        _report(self, "INFO", message)
        return {"FINISHED"}


class GK_OT_ExportRelease(bpy.types.Operator):
    """一键打包:导出工程 + 调 Godot 打出可执行文件(Windows .exe)。

    ── 为什么要有这个按钮 ──────────────────────────────────────────────
    插件以前导出到 Godot **工程**为止。要拿到"能双击运行的游戏",用户还得
    开 Godot 编辑器、配导出预设、去装导出模板、再点一次导出 —— 四步。
    这就是整个 3D 全流程里**唯一断掉的一环**。这个算子把它接成一条龙。

    流程(和『直接运行游戏』尽量共用同一条路,减少"两处不一致"):
      1. 保存 .blend 并把脚本 / 模型重新导出到 Godot 工程;
      2. 检查导出模板(没装就直接给"去哪儿装"的人话,不再往下走);
      3. 缺 export_presets.cfg 就生成一份(已有的一律不覆盖);
      4. `godot --headless --path <项目> --export-release "<预设>" <产物>`。
    """

    bl_idname = "gk.export_release"
    bl_label = "打包成可执行文件"
    bl_description = ("导出工程并调用 Godot --export-release 打出可执行文件"
                      "(需要先装好 Godot 导出模板)")
    bl_options = {"REGISTER", "UNDO"}

    auto_export: BoolProperty(
        name="打包前自动导出",
        description=("打包之前自动保存 .blend 并导出脚本与模型,"
                     "这样就不用手动先点一次导出"),
        default=True,
    )

    def execute(self, context):
        directory, ok = _resolve_project(self, context, create=False)
        if not ok:
            return {"CANCELLED"}

        # 1) 先把最新内容导出到 Godot 工程(和"运行游戏"同一套)。
        prefs = project.preferences(context)
        pref_on = bool(getattr(prefs, "auto_export_on_run", True)) if prefs else True
        if self.auto_export and pref_on:
            _auto_prepare(self, context, directory)

        # 2) 找 Godot
        executable = project.find_godot_executable(context)
        if not executable:
            _report(self, "ERROR",
                    "找不到 Godot。请在插件偏好设置里指定 godot.exe 的路径"
                    "(侧栏『项目』->『自动查找 Godot』)")
            return {"CANCELLED"}

        # 3) 导出模板检查 —— 头号失败模式,提前拦下并给出装法
        version = project.godot_version(executable, context)
        if version:
            ready, _template_dir, note = packaging.templates_status(version)
            if not ready:
                _report(self, "ERROR", note)
                return {"CANCELLED"}
        else:
            _report(self, "WARNING",
                    "读不到 Godot 版本号,跳过导出模板预检;"
                    "如果下面打包失败,多半是模板没装")

        # 4) 预设:优先沿用用户已有的(绝不覆盖),没有才生成一份
        project_name = project.safe_project_name(
            os.path.basename(directory.rstrip("\\/")))
        preset_name = packaging.read_preset_name(directory)
        output_path = None
        if not preset_name:
            preset_name = packaging.WINDOWS_PRESET_NAME
            output_path = packaging.default_output_path(directory, project_name)
            preset_path, created = packaging.ensure_export_presets(
                directory, title=project_name, preset_name=preset_name,
                export_path=packaging.rel_output_path(directory, output_path))
            if created:
                _report(self, "INFO",
                        f"已生成导出预设(Windows Desktop): {preset_path}")
        else:
            # 用户预设里可能自带输出路径;有就用它,别把产物丢到他没预期的地方
            configured = packaging.read_export_path(directory)
            if configured:
                output_path = configured if os.path.isabs(configured) \
                    else os.path.join(directory, configured)
        if not output_path:
            output_path = packaging.default_output_path(directory, project_name)

        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
        except OSError as exc:
            _report(self, "ERROR", f"创建打包输出目录失败: {exc}")
            return {"CANCELLED"}

        # 5) 真打包。这一步要等 Godot 起来(几秒到十几秒),先说一声。
        _report(self, "INFO",
                f"正在打包(预设『{preset_name}』,Godot 启动约 3~6 秒)...")
        ok_run, log = packaging.run_release(executable, directory, preset_name,
                                            output_path)
        if not ok_run or not os.path.isfile(output_path):
            hint = packaging.failure_hint(log, version)
            _report(self, "ERROR", f"打包失败。\n{hint}")
            return {"CANCELLED"}

        LAST_RELEASE.clear()
        LAST_RELEASE.update({"output": output_path, "directory": directory,
                             "preset": preset_name})
        _report(self, "INFO", f"打包完成: {output_path}")
        return {"FINISHED"}


class GK_OT_OpenReleaseFolder(bpy.types.Operator):
    """在文件管理器里打开上一次打包产物的所在目录。"""

    bl_idname = "gk.open_release_folder"
    bl_label = "打开打包目录"
    bl_description = "用系统文件管理器打开上次打出来的可执行文件所在的目录"
    bl_options = {"REGISTER"}

    def execute(self, context):
        output = LAST_RELEASE.get("output")
        directory = os.path.dirname(output) if output else None
        if not directory or not os.path.isdir(directory):
            _report(self, "ERROR",
                    "还没有打包产物。先在侧栏『项目』里点『打包成可执行文件』")
            return {"CANCELLED"}
        ok, message = project.open_folder(directory)
        if not ok:
            _report(self, "ERROR", message)
            return {"CANCELLED"}
        return {"FINISHED"}


def _auto_prepare(operator, context, directory):
    """保存 .blend 并导出脚本 + 模型。失败只警告,不阻止后续动作。
    (参数名就叫 operator,避开了与 bpy.types.Operator 的混淆——这里收的
     是**算子实例**,用它上报消息。)

    注意:这一步刻意**不抛异常打断流程** —— 用户点的是"运行"/"打包",
    因为导出出了点问题就不让跑(不让打包)会很恼人。警告写进日志就够了。

    ── 为什么抽成模块级函数 ────────────────────────────────────────
    『直接运行游戏』和『打包成可执行文件』都要这一步,逻辑必须**完全一致**
    (以前只有前者用,后者接进来时把它提出来共用,免得两处各写一份、改一处
    漏一处)。
    """
    # 1) 保存 .blend —— 素材和节点图都在文件里,不保存的话导出的是旧内容
    if bpy.data.filepath and bpy.data.is_dirty:
        try:
            bpy.ops.wm.save_mainfile()
            _report(operator, "INFO", "已自动保存 .blend")
        except Exception as exc:
            _report(operator, "WARNING", f"自动保存失败(继续): {exc}")

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
                operator.report({"WARNING"}, warning)
        except Exception as exc:
            _report(operator, "WARNING", f"自动导出脚本失败(继续): {exc}")

    # 3) 导出场景 / 模型 / 材质(多场景时每个 Blender 场景各导一张 .tscn)
    try:
        outcome = asset_export.export_all_scenes(
            directory,
            dimension=exporter.build_settings(context.scene).get("dimension", "3"),
            script_name=script_name,
        )
        if outcome.get("error"):
            _report(operator, "WARNING", f"自动导出模型失败: {outcome['error']}")
        else:
            for warning in outcome.get("warnings", []):
                _report(operator, "WARNING", warning)
            _report(operator, "INFO",
                    f"已自动导出:{outcome['materials']} 个材质、"
                    f"{outcome['bodies']} 个碰撞体、"
                    f"{outcome['cameras']} 个相机、{outcome['lights']} 盏灯")
    except Exception as exc:
        _report(operator, "WARNING", f"自动导出模型失败(继续): {exc}")


# 项目顾问的界面状态(侧栏显示用)。**对话内容**在 advisor.HISTORY 里,
# 这里只放"现在处于什么状态"这种一会儿就过期的信息。
LAST_ADVISOR = {}
# 轮询后台请求的定时器间隔(秒)。0.3 是人感觉"立刻就跳"的粒度,
# 又不至于每秒空转太多次。
_ADVISOR_POLL_INTERVAL = 0.3


def _copy_to_clipboard(context, operator, text, what):
    """把一段文本塞进系统剪贴板。返回操作结果字典。"""
    if not text:
        _report(operator, "ERROR", f"还没有可复制的{what}")
        return {"CANCELLED"}
    try:
        context.window_manager.clipboard = text
    except Exception as exc:                              # noqa: BLE001
        _report(operator, "ERROR", f"写剪贴板失败: {exc}")
        return {"CANCELLED"}
    _report(operator, "INFO", f"已复制{what}({len(text)} 字)到剪贴板")
    return {"FINISHED"}


class GK_OT_AskAdvisor(bpy.types.Operator):
    """把"项目诊断 + 你的问题"发给 AI,让它分析并给下一步。

    ── 为什么改成异步(用户 2026-10-08 的反馈)──────────────────────
    第一版是**阻塞**的:点下去界面冻 60 秒 —— 只问一句还能忍,要"连续对话"
    就完全不行,而且以后让 AI 连着调几个工具会直接冻死。现在改成后台线程发
    请求 + 主线程定时器收结果:点完立刻返回,侧栏显示『正在问 AI…』,回复
    到了自动追加进文本块。
    """

    bl_idname = "gk.ask_advisor"
    bl_label = "问 AI"
    bl_description = ("把项目诊断快照和你的问题发给 AI(需先填 AI 地址 / "
                      "模型名 / API Key)。发出后界面不会卡,回复自动进对话记录")
    bl_options = {"REGISTER"}

    def execute(self, context):
        if advisor.agent_active():
            _report(self, "ERROR", "上一条还在等 AI 回,稍等一下再发")
            return {"CANCELLED"}

        settings = getattr(context.scene, "gk_settings", None)
        question = (getattr(settings, "advisor_question", "") or "").strip()
        if not question:
            _report(self, "ERROR", "先在『项目顾问』里写一句你卡在哪,再点『问 AI』")
            return {"CANCELLED"}

        prefs = project.preferences(context)
        base_url = (getattr(prefs, "advisor_base_url", "") or "") if prefs else ""
        if not base_url.strip():
            _report(self, "ERROR",
                    "还没填 AI 地址(插件偏好设置 -> 『项目顾问』)。\n"
                    "不打算配的话,点『复制诊断』把它粘到别处的 AI 里问也一样")
            return {"CANCELLED"}
        api_key = (getattr(prefs, "advisor_api_key", "") or "") if prefs else ""
        model = (getattr(prefs, "advisor_model", "") or "").strip() if prefs else ""
        include_code = bool(getattr(prefs, "advisor_include_code", True)) \
            if prefs else True

        _report(self, "INFO", "正在收集项目诊断...")
        try:
            context_text, _sizes = advisor.collect_context(
                context, include_code=include_code)
        except Exception as exc:                          # noqa: BLE001
            _report(self, "ERROR", f"收集项目诊断失败: {type(exc).__name__}: {exc}")
            return {"CANCELLED"}

        # 历史 + **刚取的一份**快照(快照进系统消息,不进历史 —— 见
        # advisor.build_messages 的说明:否则每轮带一份过期快照,越聊越错)
        messages = advisor.build_messages(question, context_text,
                                          advisor.HISTORY)
        # ── 走 Agent 循环(不是普通问答)────────────────────────────
        # 带工具之后,AI 可以自己去查节点图 / 导出报告,而不是只凭那份快照
        # 猜。它要工具时由主线程执行,再喂回去接着问 —— 见 poll_agent_step。
        if not advisor.start_agent(base_url, api_key, model, messages):
            _report(self, "ERROR", "上一条还在等 AI 回,稍等一下再发")
            return {"CANCELLED"}

        LAST_ADVISOR.clear()
        LAST_ADVISOR.update({"status": "正在问 AI…", "question": question,
                             "error": ""})
        _start_advisor_poll()
        _report(self, "INFO",
                f"已发出(项目诊断 {len(context_text)} 字,AI 可自己查项目)。"
                f"界面不会卡,回复到了会自动追加到文本块"
                f"『{advisor.DIALOG_TEXT_NAME}』")
        return {"FINISHED"}


class GK_OT_CopyAdvisorContext(bpy.types.Operator):
    """把"问题 + 项目诊断"整理成一段文本复制走(**不联网**)。

    这是没配 AI 时的兜底,而且往往比配 API 更省事:直接粘到 Trae / 网页版
    AI 里问。整理上下文才是这个功能里最值钱的部分,发不发出去是次要的。
    """

    bl_idname = "gk.copy_advisor_context"
    bl_label = "复制诊断"
    bl_description = "把问题 + 项目诊断快照整理成一段文本复制到剪贴板(不联网)"
    bl_options = {"REGISTER"}

    def execute(self, context):
        settings = getattr(context.scene, "gk_settings", None)
        question = (getattr(settings, "advisor_question", "") or "").strip()
        prefs = project.preferences(context)
        include_code = bool(getattr(prefs, "advisor_include_code", True)) \
            if prefs else True
        try:
            context_text, _sizes = advisor.collect_context(
                context, include_code=include_code)
        except Exception as exc:                          # noqa: BLE001
            _report(self, "ERROR", f"收集项目诊断失败: {type(exc).__name__}: {exc}")
            return {"CANCELLED"}
        LAST_ADVISOR["context"] = context_text
        return _copy_to_clipboard(context, self,
                                  advisor.compose_text(question, context_text),
                                  "诊断")


class GK_OT_OpenTextBlock(bpy.types.Operator):
    """在文本编辑器里打开一个文本块(能滚动 / 选择 / 复制 / 搜索)。

    ── 为什么长内容都往这里送(用户第五十四、五十五两轮的反馈)────────
    截图里 808 字的回答把侧栏撑满、**没有滚动条**、一行才十来个字;导出报告
    一出问题也能有十几条。侧栏的 label 结构上就干不了长文本(一行一个控件、
    不支持滚动),而 Blender 的文本编辑器天生能滚动/选择/复制/搜索。
    所以『GK 对话记录』『GK 导出报告』都写进文本块,用文本编辑器读 ——
    这是插件能提供的最好形态(自定义编辑器窗口 Python 注册不了)。
    """

    bl_idname = "gk.open_text_block"
    bl_label = "查看文本"
    bl_description = "在文本编辑器里打开指定的文本块"
    bl_options = {"REGISTER"}

    text_name: StringProperty(
        name="文本块",
        description="要打开的文本块名字",
        default="",
    )

    def execute(self, context):
        name = (self.text_name or "").strip() or advisor.DIALOG_TEXT_NAME
        block = bpy.data.texts.get(name)
        if block is None:
            _report(self, "ERROR",
                    f"还没有『{name}』,先做一次会生成它的操作(问 AI / 导出一次)")
            return {"CANCELLED"}

        # 1) 已经有文本编辑器 -> 直接把内容切过去(**不动用户布局**,最不打扰)
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != "TEXT_EDITOR":
                    continue
                for space in area.spaces:
                    if getattr(space, "type", "") == "TEXT_EDITOR":
                        space.text = block
                area.tag_redraw()
                return {"FINISHED"}

        # 2) 一块文本编辑器都没有 -> 开个新窗口,把它最大的一块改成文本编辑器
        try:
            bpy.ops.wm.window_new()
        except Exception as exc:                          # noqa: BLE001
            _report(self, "ERROR",
                    f"开新窗口失败: {exc}\n"
                    f"可以手动开一个文本编辑器,选『{advisor.DIALOG_TEXT_NAME}』")
            return {"CANCELLED"}
        window = bpy.context.window_manager.windows[-1]
        area = _biggest_area(window)
        if area is None:
            _report(self, "ERROR", "新窗口里找不到可用区域,请手动开文本编辑器")
            return {"CANCELLED"}
        try:
            area.type = "TEXT_EDITOR"
            for space in area.spaces:
                if getattr(space, "type", "") == "TEXT_EDITOR":
                    space.text = block
        except Exception as exc:                          # noqa: BLE001
            _report(self, "ERROR", f"切换成文本编辑器失败: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


def _biggest_area(window):
    """新窗口里挑面积最大的一块区域(拿它改成文本编辑器最不容易白屏)。"""
    best = None
    try:
        areas = list(window.screen.areas)
    except Exception:                                     # noqa: BLE001
        return None
    for area in areas:
        if best is None or (area.width * area.height) > (best.width * best.height):
            best = area
    return best


class GK_OT_ApplyAdvisorAction(bpy.types.Operator):
    """执行 AI 提议的那个动作 —— **只有用户点这里才真的改项目**。

    ── 这一步是整个设计的"最后一道关" ────────────────────────────
    AI 全程**没有**修改项目的能力:它只能 `propose_action` 生成一张卡片。
    真改发生在**用户按下这个按钮**的时候。这样"AI 能不能动我的工程"这个
    问题的答案,永远由用户那一刻的决定给出 —— 而不是由模型的自觉给出。

    执行结果会写进对话记录(用户回头看得到"我当时做了什么改动"),
    也会记进日志。
    """

    bl_idname = "gk.apply_advisor_action"
    bl_label = "执行 AI 提议的动作"
    bl_description = "按 AI 的建议修改项目(点之前不会有任何改动)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        card = advisor.agent_proposal()
        if not card:
            _report(self, "ERROR", "没有待确认的动作(可能已经执行或忽略过了)")
            return {"CANCELLED"}
        ok, message = advisor_tools.apply_proposal(context, card)
        advisor.clear_proposal()
        LAST_ADVISOR["status"] = ("已执行:" + message) if ok else ""
        if not ok:
            LAST_ADVISOR["error"] = f"执行失败:{message}"
        # 这一笔也要留痕 —— 用户回头看对话记录时知道"当时做了什么"
        try:
            advisor.sync_dialog_text(
                f"[执行动作] {card.get('label', '')}"
                + (f"({card.get('target', '')})" if card.get("target") else ""),
                f"{'成功' if ok else '失败'}:{message}")
        except Exception:                                 # noqa: BLE001
            pass
        _report(self, "INFO" if ok else "ERROR", message)
        return {"FINISHED"} if ok else {"CANCELLED"}


class GK_OT_DismissAdvisorAction(bpy.types.Operator):
    """忽略 AI 提议的动作(什么都不改)。"""

    bl_idname = "gk.dismiss_advisor_action"
    bl_label = "忽略"
    bl_description = "不采纳这个建议,项目不会有任何改动"
    bl_options = {"REGISTER"}

    def execute(self, context):
        advisor.clear_proposal()
        LAST_ADVISOR["status"] = "已忽略 AI 的建议(项目没有改动)"
        return {"FINISHED"}


class GK_OT_RunAdvisorAction(bpy.types.Operator):
    """手动挑一个动作执行(不经过 AI)。

    ── 为什么要有这个(而不是只让 AI 提议)────────────────────────
    提议卡片只能由 AI 发起 —— 用户可能没配 AI,也可能 AI 没想到那个动作。
    给一个手选入口,这份动作清单就不会白做,而且用户在 AI 抽风时也有路可走。
    """

    bl_idname = "gk.run_advisor_action"
    bl_label = "手动执行一个动作"
    bl_description = "从已登记的安全动作里挑一个执行(不经过 AI)"
    bl_options = {"REGISTER", "UNDO"}

    action: EnumProperty(
        name="动作",
        description="要执行的动作",
        items=lambda self, context: advisor_tools.action_menu_items(self,
                                                                    context),
    )
    target: StringProperty(
        name="对象",
        description="物体名 / 图名 / 属性名(看动作需不需要)",
        default="",
    )
    value: StringProperty(name="值", description="要设成的值", default="")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "action", text="动作")
        hint = advisor_tools.action_hint(self.action)
        if hint:
            for line in advisor.wrap(hint, 44)[:4]:
                layout.label(text=line, icon="INFO")
        needs = advisor_tools.ACTIONS.get(self.action, {}).get("needs", ())
        if "target" in needs:
            layout.prop(self, "target", text="对象")
        if "value" in needs:
            layout.prop(self, "value", text="值")

    def execute(self, context):
        card = {"action": self.action, "label": advisor_tools.action_label(
            self.action), "target": self.target, "value": self.value,
            "why": "(手动执行)", "hint": ""}
        ok, message = advisor_tools.apply_proposal(context, card)
        _report(self, "INFO" if ok else "ERROR", message)
        return {"FINISHED"} if ok else {"CANCELLED"}


class GK_OT_ClearAdvisor(bpy.types.Operator):
    """清空对话历史 + 对话记录文本块。"""

    bl_idname = "gk.clear_advisor"
    bl_label = "清空对话"
    bl_description = "清空对话历史与『GK 对话记录』文本块(下次问就不带旧上下文)"
    bl_options = {"REGISTER"}

    def execute(self, context):
        # 正在跑的先放弃等待,否则它回来还会往刚清空的记录里写一条
        advisor.cancel_agent()
        advisor.clear_history()
        advisor.save_history(context.scene)
        advisor.clear_dialog_text()
        LAST_ADVISOR.clear()
        _report(self, "INFO", "已清空对话历史与对话记录")
        return {"FINISHED"}


def _start_advisor_poll():
    """挂上轮询定时器(已经在挂就不重复挂)。"""
    try:
        if not bpy.app.timers.is_registered(_poll_advisor):
            bpy.app.timers.register(_poll_advisor,
                                    first_interval=_ADVISOR_POLL_INTERVAL)
    except Exception as exc:                              # noqa: BLE001
        gk_log.warn(f"挂 AI 轮询定时器失败(回复可能不会自动出现): {exc}")


def _stop_advisor_poll():
    try:
        if bpy.app.timers.is_registered(_poll_advisor):
            bpy.app.timers.unregister(_poll_advisor)
    except Exception:                                     # noqa: BLE001
        pass


def _poll_advisor():
    """主线程定时器:推进 AI 一轮。返回 None = 注销自己。

    ── 这个函数是"AI 能自己查项目"的关键 ──────────────────────────
    `poll_agent_step` 在**主线程**里执行 AI 要的工具(读节点图 / 导出报告…),
    然后把结果交回后台线程发下一轮。所以"网络在线程、工具在主线程"这个交替
    是在这里发生的 —— 后台线程碰 bpy 会崩,这一层不能省。

    只在**结果到了 / 推进了一步**才重画界面 —— 干等期间反复重画纯属浪费。
    """
    if not advisor.agent_active():
        return None
    state, result = advisor.poll_agent_step(*_advisor_args())
    if state != "done":
        if state == "continue":
            _redraw_all_areas()      # "它读了节点图"这类进度要立刻看得见
        return _ADVISOR_POLL_INTERVAL
    _apply_advisor_result(*result)
    _redraw_all_areas()
    return None


def _advisor_args():
    """拼 `poll_agent_step` 要的那串参数(地址 / Key / 模型 / 超时)。"""
    context = bpy.context
    prefs = project.preferences(context)
    base_url = (getattr(prefs, "advisor_base_url", "") or "") if prefs else ""
    api_key = (getattr(prefs, "advisor_api_key", "") or "") if prefs else ""
    model = (getattr(prefs, "advisor_model", "") or "").strip() if prefs else ""
    return context, base_url, api_key, model


def _apply_advisor_result(ok, text):
    """**主线程**里处理结果:写历史、写文本块、更新侧栏状态。

    这一步必须回主线程做 —— 后台线程碰 bpy 会出"偶尔崩、偶尔不刷新"的
    鬼影 bug(见 advisor 里那段说明)。
    """
    scene = getattr(bpy.context, "scene", None) or next(iter(bpy.data.scenes), None)
    question = LAST_ADVISOR.get("question", "")
    if ok:
        # 到这一步才记进历史:失败的请求不该污染下一轮的上下文
        advisor.remember("user", question)
        advisor.remember("assistant", text)
        advisor.save_history(scene)
        try:
            advisor.sync_dialog_text(question, text)
        except Exception as exc:                          # noqa: BLE001
            gk_log.warn(f"写对话记录失败(回答还在日志里): {exc}")
        # 问完了就把输入框腾空,方便接着问下一句
        settings = getattr(scene, "gk_settings", None) if scene else None
        if settings is not None:
            settings.advisor_question = ""
        steps = advisor.agent_steps()
        # 有提议时,那把回答从"文字"升级成"带一张待确认卡片的文字"
        if advisor.agent_proposal():
            status = "AI 提了一个动作 —— 在下面确认或忽略"
        else:
            status = (f"AI 已回复({len(text)} 字),"
                      f"已存入『{advisor.DIALOG_TEXT_NAME}』")
        LAST_ADVISOR.update({"status": status, "error": "", "steps": steps})
        gk_log.info(f"AI 回答了 {len(text)} 字,已追加到对话记录"
                    + (f";它做了:{'、'.join(steps)}" if steps else ""))
    else:
        LAST_ADVISOR.update({"status": "", "error": text})
        try:
            advisor.sync_dialog_text(question, "", error=text)
        except Exception:                                 # noqa: BLE001
            pass
        gk_log.error(text)


def _redraw_all_areas():
    """让侧栏重画(状态从『正在问 AI…』变成结果)。"""
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
    except Exception:                                     # noqa: BLE001
        pass


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
    GK_OT_AddCommentBox,
    # 变量集中面板:查找引用 + 放取/设节点
    GK_OT_VarFindRefs,
    GK_OT_VarMakeNode,
    GK_OT_StarterGraph,
    GK_OT_AutoLink,
    # 动画名选择器(动作归属分析,带 ★ 的排前面)
    GK_OT_PickAnimName,
    GK_OT_PickAnimTree,
    # 界面设计图选择器(『界面_套用设计』用,第四十轮)
    GK_OT_PickUiDesign,
    # AI 图选择器(『AI_启用』用)+ AI 转场的『从』/『到』状态选择器(第四十一轮)
    GK_OT_PickAiTree,
    GK_OT_PickAiState,
    # 音频资产:音效节点『从资产库挑』+ 声音卡上『挑本地文件』(第四十三轮)
    GK_OT_PickSound,
    GK_OT_AssetPickSound,
    # 转场的『从』/『到』状态选择器(忘了连线时的兜底)
    GK_OT_PickAnimState,
    # 按一下键盘就绑定按键(照 UPBGE 的 Press & Choose)
    GK_OT_CaptureKey,
    # 挑一条曲线当运镜轨道(『相机_路径』用)
    GK_OT_PickCurve,
    # 挑一个静态资产(『资产_创建实例』用;列表里带"共几份")
    GK_OT_PickAsset,
    # 挑一张图片当界面图片 / 对话框立绘(第三十七轮 UI 设计)
    GK_OT_PickImage,
    # 真预览:把相机沿曲线跑一遍(所见即所得,不产生游戏代码)
    GK_OT_PreviewCameraPath,
    # 动作分类标签(给动作打"静止/移动/攻击…",导出时按分类成表)
    GK_OT_TagAnimCategory,
    # 碰撞体可视化(像 UPBGE 那样能在视口里看到碰撞范围)
    GK_OT_ShowCollision,
    GK_OT_HideCollision,
    GK_OT_PreviewColliders,
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
    # 打包发布:一键把工程打成可执行文件(补全流程最后断掉的一环)
    GK_OT_ExportRelease,
    GK_OT_OpenReleaseFolder,
    # 项目顾问(AI):把项目诊断 + 你的问题交给模型分析(异步,对话记录进文本块)
    GK_OT_AskAdvisor,
    GK_OT_CopyAdvisorContext,
    GK_OT_ClearAdvisor,
    # AI 提议的动作:执行 / 忽略 / 手动挑一个(只有用户点『执行』才真改项目)
    GK_OT_ApplyAdvisorAction,
    GK_OT_DismissAdvisorAction,
    GK_OT_RunAdvisorAction,
    # 长内容统一从这里进文本编辑器(对话记录 / 导出报告)
    GK_OT_OpenTextBlock,
    GK_OT_RelinkProject,
    GK_OT_DetectGodot,
    GK_OT_Export,
    GK_OT_ExportAssets,
    GK_OT_SyncFaceAngle,
    GK_OT_Preview,
    # 导出报告(第二十三轮):复制成文本 / 定位到出问题的节点
    GK_OT_ReportCopy,
    GK_OT_ReportFocus,
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
    # 定时器必须摘掉:它闭包里握着本模块的函数对象,插件注销后还在跑会报错
    _stop_advisor_poll()
    advisor.cancel_agent()
    advisor.clear_history()
    LAST_EXPORT.clear()
    LAST_RELEASE.clear()
    LAST_ADVISOR.clear()
    export_report.clear()
    RECENT_NODES.clear()
