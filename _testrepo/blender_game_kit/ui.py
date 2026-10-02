"""UI:侧栏面板、添加节点菜单、节点引用选择器。"""

import os

import bpy
from bpy.props import IntProperty, StringProperty

from . import codegen, exporter, gk_log, project
from . import asset_export
from . import operator_io as ops
from . import properties as props
from .nodes import nodes as node_lib
from .nodes import node_base as NB


# ==========================================================================
# 节点树类型
# ==========================================================================
class GKGameLogicTree(bpy.types.NodeTree):
    """游戏逻辑节点树:在节点编辑器里用中文节点写游戏逻辑，导出 GDScript。"""

    bl_idname = "GKGameLogicTree"
    bl_label = "游戏逻辑"
    bl_icon = "NODETREE"
    bl_description = "BlenderGameKit 的游戏逻辑节点树(导出为 Godot GDScript)"

    @classmethod
    def poll(cls, context):
        return True

    @classmethod
    def draw_header(cls, layout, context):
        """编辑器顶部那条工具栏。

        这是"问你要不要打开 Godot"最合适的位置:切到游戏逻辑节点编辑器
        就能看到,不弹窗打断,但足够醒目。
        """
        directory, _reason = project.resolve(context.scene)

        if not bpy.data.filepath:
            layout.label(text="先保存 .blend", icon="ERROR")
            layout.operator("gk.create_project", text="建项目", icon="ADD")
            return

        if not directory:
            layout.label(text="未绑定项目", icon="ERROR")
            layout.operator("gk.create_project", text="建项目", icon="ADD")
            return

        if not project.is_project(directory):
            layout.label(text="项目未创建", icon="INFO")
            layout.operator("gk.create_project", text="创建 Godot 项目", icon="ADD")
            return

        # 项目就绪 —— 这里就是那个"问你要不要打开 Godot"的入口
        layout.label(text=os.path.basename(directory), icon="FILE_FOLDER")
        layout.operator("gk.open_godot", text="打开 Godot",
                        icon="WINDOW").editor = True
        layout.operator("gk.open_godot", text="运行",
                        icon="PLAY").editor = False
        layout.separator()
        layout.operator("gk.export_script", text="导出脚本", icon="FILE_TICK")


def _node_space(context):
    """找到当前可用的节点编辑器区域(拿不到就返回 None)。"""
    window = getattr(context, "window", None)
    screen = getattr(window, "screen", None) if window else None
    for area in getattr(screen, "areas", ()) or ():
        if getattr(area, "type", "") != "NODE_EDITOR":
            continue
        for space in getattr(area, "spaces", ()) or ():
            return space
    return None


# 每种树类型**上次用的是哪张** —— 供 GK_OT_SwitchTree 恢复。
_LAST_TREE = {}


class GK_OT_SwitchTree(bpy.types.Operator):
    """切到另一种节点图,并**恢复上次用的那张**。

    ── 为什么需要(用户实测的问题)──────────────────────────────
    用户原话:"切换视图后,它没有保持上一步的设计界面,变成未创建的状态,
    但点击新建旁边的下三角后就可以看到了,这每次切换重置都比较麻烦"

    根因:Blender 自己的类型下拉切换 `tree_type` 时会把
    `space.node_tree` 清掉 —— 它不知道"这一类树里你上次看的是哪张",
    因为 `node_tree` 只有**一个**槽位,切类型时它就不匹配了。

    Blender 没提供"切换后自动选回上次那张"的钩子,所以给一个自己的
    切换入口:记住每种类型上次用的树,切过去时恢复。
    """

    bl_idname = "gk.switch_tree"
    bl_label = "切换节点图"
    bl_description = "在游戏逻辑图和动画树之间切换(会恢复上次看的那张)"
    bl_options = {"REGISTER", "UNDO"}

    target: StringProperty(name="目标类型", default="GKGameLogicTree")

    def execute(self, context):
        space = _node_space(context)
        if space is None:
            self.report({"WARNING"}, "请先打开一个『节点编辑器』区域")
            return {"CANCELLED"}

        # 离开之前先记下当前这张,回来时才能恢复
        current_kind = getattr(space, "tree_type", "")
        current = (getattr(space, "edit_tree", None)
                   or getattr(space, "node_tree", None))
        if current is not None and current_kind in ("GKGameLogicTree",
                                                    "GKAnimationTree"):
            _LAST_TREE[current_kind] = current

        wanted = self.target
        tree = _LAST_TREE.get(wanted)
        # 记着的那张可能已经被删了 —— 校验一下还在不在
        if tree is not None and tree.name not in bpy.data.node_groups:
            tree = None
        if tree is None:
            for group in bpy.data.node_groups:
                if getattr(group, "bl_idname", "") == wanted:
                    tree = group
                    break
        if tree is None:
            if wanted == "GKAnimationTree":
                # 顺手放个起始状态,免得面对空图不知道从哪下手
                tree = bpy.data.node_groups.new("动画", "GKAnimationTree")
                state = tree.nodes.new("GKNodeAnimState")
                state.gk_state_name = "idle"
                state.gk_is_default = True
            else:
                tree = bpy.data.node_groups.new("游戏逻辑", "GKGameLogicTree")
            self.report({"INFO"}, f"已新建『{tree.name}』")

        space.tree_type = wanted
        space.node_tree = tree
        _LAST_TREE[wanted] = tree
        return {"FINISHED"}


# ==========================================================================
# 动画树 —— 独立的第二张节点图(照虚幻 5 的 Animation Blueprint)
# ==========================================================================
# 用户原话:"这个可以按照虚幻5蓝图的来做,或者建立一个动作系统,像虚幻5
# 那样,然后就像 upbge 的逻辑砖调用,而不是直接在这来进行,不然,
# 后面就密密麻麻的(这也是 upbge 的缺点,虚幻5的分开得还好)"
#
# 说得对。第一版把状态表塞进一个节点,那就是 UPBGE 的老毛病。
# 现在拆成两张图:
#
#     游戏逻辑树(玩法)              动画树(动作)
#     ┌───────────────┐             ┌──────────────────────┐
#     │ 每当_准备      │             │ [动画状态: idle] 起始  │
#     │  动画_状态机  ─┼── 驱动 ────>│ [动画状态: run]  条件─┤
#     └───────────────┘             │ [动画状态: jump] 条件─┤
#                                   └──────────────────────┘
#
# 玩法图里只留**一个**引用节点,动作规则全在动画树里。
class GKAnimationTree(bpy.types.NodeTree):
    """游戏动画节点树:状态和切换规则画在这里,由游戏逻辑里的
    『动画_状态机』节点驱动。"""

    bl_idname = "GKAnimationTree"
    bl_label = "游戏动画"
    bl_icon = "ARMATURE_DATA"
    bl_description = ("BlenderGameKit 的动画系统节点树 —— "
                      "动画状态和切换条件写在这里,别和玩法挤在一张图上")

    @classmethod
    def poll(cls, context):
        return True

    @classmethod
    def draw_header(cls, layout, context):
        layout.label(text="动画状态 + 切换条件", icon="INFO")


class GK_OT_OpenAnimTree(bpy.types.Operator):
    """打开动画树;没有就新建一张。

    为什么要一个按钮:动画树是**另一张节点图**,
    新手不知道要去节点编辑器的下拉框里切类型 ——
    用户原话就是"在游戏逻辑下边建立游戏动画系统节点"。
    """

    bl_idname = "gk.open_anim_tree"
    bl_label = "打开/新建动画树"
    bl_description = "切到动画树编辑器(没有就新建一张并放好起始状态)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        trees = [g for g in bpy.data.node_groups
                 if getattr(g, "bl_idname", "") == "GKAnimationTree"]
        tree = trees[0] if trees else None
        if tree is None:
            tree = bpy.data.node_groups.new("动画", "GKAnimationTree")
            # 新建时放一个起始状态,免得用户面对一张空图不知道从哪下手
            state = tree.nodes.new("GKNodeAnimState")
            state.gk_state_name = "idle"
            state.gk_is_default = True
            state.location = (0.0, 0.0)
            self.report({"INFO"}, f"已新建动画树『{tree.name}』")

        # 把节点编辑器的树切过去
        space = _node_space(context)
        if space is not None:
            space.tree_type = "GKAnimationTree"
            space.node_tree = tree
        else:
            self.report({"WARNING"},
                        "没找到节点编辑器 —— 请打开一个『节点编辑器』区域")
            return {"CANCELLED"}
        return {"FINISHED"}


class GK_PT_AnimTree(bpy.types.Panel):
    """动画树专属面板。

    ── 为什么必须有一个(踩过的坑)────────────────────────────────
    以前只有 `GK_PT_Main`,它的 poll 写死 `tree_type == "GKGameLogicTree"`
    —— 于是切到动画树时**整个侧栏是空的**(用户实测:
    "游戏动画那里什么都没有")。人会以为这功能坏了。
    """

    bl_idname = "GK_PT_AnimTree"
    bl_label = "游戏动画 (BlenderGameKit)"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "游戏动画"

    @classmethod
    def poll(cls, context):
        space = getattr(context, "space_data", None)
        return space is not None and getattr(space, "tree_type", "") == "GKAnimationTree"

    def draw(self, context):
        layout = self.layout
        tree = props.current_tree(context)

        if tree is not None and len(tree.nodes) == 0:
            box = layout.box()
            box.label(text="这张动画树还是空的", icon="INFO")
            col = box.column(align=True)
            col.label(text="1. 加一个『动画_切状态』卡片")
            col.label(text="2. 选『目标动画』(从文件里的动作挑)")
            col.label(text="3. 把一个判断接到它的『条件』引脚")
            col.label(text="     例:『动画判断_移动状态』的『在移动』")
            col.label(text="4. 勾上『起始』的那个就是进游戏时的状态")
            box.operator("gk.add_node_menu", text="添加节点",
                         icon="ADD").node_type = "GKNodeAnimTransition"

        if tree is not None:
            cards = [n for n in tree.nodes
                     if getattr(n, "bl_idname", "") in ("GKNodeAnimTransition",
                                                        "GKNodeAnimState")]
            box = layout.box()
            box.label(text=f"{tree.name} —— {len(cards)} 张卡片",
                      icon="NODETREE")
            default = [c for c in cards if getattr(c, "gk_is_default", False)]
            for card in sorted(cards,
                               key=lambda c: int(getattr(c, "gk_priority", 0) or 0)):
                row = box.row(align=True)
                mark = "★ " if card in default else "   "
                resolver = getattr(card, "resolved_state_name", None)
                name = (resolver() if resolver is not None
                        else getattr(card, "gk_state_name", "?")) or "(没设目标)"
                row.label(text=f"{mark}{name}", icon="FORWARD")
                row.label(text=f"优先 {getattr(card, 'gk_priority', 0)}")
            if not default and cards:
                box.label(text="还没勾『起始』,会用第一张", icon="ERROR")

        layout.operator("gk.add_node_menu", text="添加节点", icon="ADD")
        layout.operator("gk.export_script", text="导出脚本", icon="FILE_TICK")


# ==========================================================================
# 添加节点菜单
# ==========================================================================
def _menu_name(category):
    return "GK_MT_NODES_" + str(abs(hash(category)) % 100000)


class GK_OT_AddNodeMenu(bpy.types.Operator):
    """占位算子:菜单项点击后由 GK_OT_AddNode 真正执行。"""

    bl_idname = "gk.add_node_menu"
    bl_label = "游戏逻辑"
    bl_description = "添加游戏逻辑节点"

    node_type: StringProperty(default="")
    link_from: StringProperty(default="")

    def execute(self, context):
        return bpy.ops.gk.add_node(node_type=self.node_type, link_from=self.link_from)


class GK_MT_AddNodeCategories(bpy.types.Menu):
    """全部分类(Shift+A 里那个子菜单的内容)。

    ── 为什么这里只有分类 ────────────────────────────────────────────
    「搜索 / 常用 / 最近使用」都在**外层**菜单里(见 _draw_node_menu_contents)，
    因为外层有两个入口:Shift+A 和右键。放在这里的话右键菜单
    就看不到它们了。这里只负责"按分类列节点"。
    """

    bl_idname = "GK_MT_ADD_NODE"
    bl_label = "添加游戏逻辑节点"

    def draw(self, context):
        layout = self.layout
        tree = props.current_tree(context)
        link_from = ""
        if tree is not None and getattr(tree, "nodes", None) is not None:
            active = getattr(tree.nodes, "active", None)
            if active is not None:
                link_from = active.name
        if link_from:
            layout.label(text=f"新节点会自动接到: {link_from}", icon="LINKED")

        # 全部分类。大类自带二级子菜单(见 props.node_menu_tree)
        for category, subs in props.node_menu_tree(props.current_tree_type(context)):
            layout.menu(_menu_name(category), text=category)


def _make_category_menu(category, classes, subcategories=None):
    """为一个分类动态生成菜单类。

    subcategories 不为 None 时，这个分类会先列出二级子菜单
    (比如『运算』-> 常量 / 数学 / 向量 / 逻辑 / 转换)。
    """

    def _add(menu_layout, cls, link_from):
        op = menu_layout.operator("gk.add_node_menu", text=cls.bl_label)
        op.node_type = cls.bl_idname
        op.link_from = link_from

    def draw(self, context):
        layout = self.layout
        link_from = ""
        tree = props.current_tree(context)
        if tree is not None:
            active = getattr(tree.nodes, "active", None)
            if active is not None:
                link_from = active.name
        if subcategories:
            for sub_name, sub_classes in subcategories:
                # 子菜单类在 CATEGORY_MENUS 里单独注册，这里只要挂上去
                layout.menu(_menu_name(f"{category}/{sub_name}"), text=sub_name)
        else:
            # ── 这里再过滤一次(兜底)────────────────────────────────
            # 外层菜单已经按树类型筛过分类了,但 `current_tree_type()`
            # 在某些上下文拿不到时会回退成"列全部分类" ——
            # 那时这个分类里可能全是别的树的节点,点下去只会报错。
            # 在 draw 里(此时一定有 context)按当前树类型再筛一遍。
            kind = props.current_tree_type(context)
            shown = 0
            for cls in classes:
                if kind is not None:
                    if getattr(cls, "gk_tree_type", "GKGameLogicTree") != kind:
                        continue
                _add(layout, cls, link_from)
                shown += 1
            if shown == 0:
                layout.label(text="(这个分类下没有适合当前节点图的节点)",
                             icon="INFO")

    name = f"GK_MT_CAT_{abs(hash(category)) % 100000}"
    return type(name, (bpy.types.Menu,), {
        "bl_idname": _menu_name(category),
        "bl_label": category,
        "draw": draw,
        "__doc__": f"添加『{category}』类游戏逻辑节点",
    })


def _make_subcategory_menu(category, sub_name, classes):
    """二级子菜单(比如 『运算 / 向量』)。"""

    def draw(self, context):
        layout = self.layout
        link_from = ""
        tree = props.current_tree(context)
        if tree is not None:
            active = getattr(tree.nodes, "active", None)
            if active is not None:
                link_from = active.name
        for cls in classes:
            op = layout.operator("gk.add_node_menu", text=cls.bl_label)
            op.node_type = cls.bl_idname
            op.link_from = link_from

    key = f"{category}/{sub_name}"
    name = f"GK_MT_SUB_{abs(hash(key)) % 100000}"
    return type(name, (bpy.types.Menu,), {
        "bl_idname": _menu_name(key),
        "bl_label": sub_name,
        "draw": draw,
        "__doc__": f"添加『{category} / {sub_name}』类节点",
    })


def _menu_has_entries(menu_name, context, classes_getter):
    """这个子菜单在当前树里会不会有内容?

    用来决定**画不画**它。动画树里「常用 / 最近使用」原来是两张空菜单
    ——它们的候选全是玩法节点,一张都不适用(实测:动画树的右键菜单里
    这两项点开是空的)。空菜单比没有菜单更让人困惑。
    """
    kind = props.current_tree_type(context)
    try:
        classes = classes_getter()
    except Exception:
        return False
    for cls in classes:
        if getattr(cls, "gk_hidden", False):
            continue
        if kind is None or getattr(cls, "gk_tree_type",
                                   "GKGameLogicTree") == kind:
            return True
    return False


def _make_common_menu():
    """「常用」子菜单:新手起步最需要的十几个节点。"""

    def draw(self, context):
        layout = self.layout
        link_from = ""
        tree = props.current_tree(context)
        if tree is not None:
            active = getattr(tree.nodes, "active", None)
            if active is not None:
                link_from = active.name
        for cls in props.common_node_classes():
            # 同 _make_category_menu:按当前树类型过滤,
            # 不然动画树里点开会是一串必然报错的玩法节点
            kind = props.current_tree_type(context)
            if kind is not None and getattr(
                    cls, "gk_tree_type", "GKGameLogicTree") != kind:
                continue
            if getattr(cls, "gk_hidden", False):
                continue
            op = layout.operator("gk.add_node_menu", text=cls.bl_label)
            op.node_type = cls.bl_idname
            op.link_from = link_from

    return type("GK_MT_Common", (bpy.types.Menu,), {
        "bl_idname": _menu_name("常用"),
        "bl_label": "常用",
        "draw": draw,
        "__doc__": "新手起步最常用的节点",
    })


CATEGORY_MENUS = []
# 注意:这里是**模块级**代码,没有 context —— 不能按当前树类型过滤。
# 菜单建成"全部节点",真正的过滤在各自的 `draw()` 里按
# `props.current_tree_type(context)` 做。见 GK_MT_AddNodeCategories。
for _category, _subs in props.node_menu_tree(None):
    if _subs is None:
        _classes = next(classes for cat, classes in props.node_menu_entries(None)
                        if cat == _category)
        CATEGORY_MENUS.append(_make_category_menu(_category, _classes))
    else:
        CATEGORY_MENUS.append(_make_category_menu(_category, None, subcategories=_subs))
        for _sub_name, _sub_classes in _subs:
            CATEGORY_MENUS.append(_make_subcategory_menu(_category, _sub_name, _sub_classes))


def _make_recent_menu():
    """「最近使用」子菜单。

    蓝图里高频操作是"反复加同一批节点"(事件、分支、设置变量),
    每次都翻分类太慢。这个菜单按最近使用排序,最多 8 个。
    """

    def draw(self, context):
        layout = self.layout
        link_from = ""
        tree = props.current_tree(context)
        if tree is not None:
            active = getattr(tree.nodes, "active", None)
            if active is not None:
                link_from = active.name

        recent = _recent_classes()
        if not recent:
            layout.label(text="(还没用过任何节点)", icon="INFO")
            return
        for cls in recent:
            op = layout.operator("gk.add_node_menu", text=cls.bl_label)
            op.node_type = cls.bl_idname
            op.link_from = link_from

    return type("GK_MT_Recent", (bpy.types.Menu,), {
        "bl_idname": _menu_name("最近使用"),
        "bl_label": "最近使用",
        "draw": draw,
        "__doc__": "最近用过的游戏逻辑节点",
    })


class GK_OT_FindNode(bpy.types.Operator):
    """搜索并添加节点(蓝图那种"打字就出来"的入口)。"""

    bl_idname = "gk.find_node"
    bl_label = "搜索游戏逻辑节点"
    bl_description = ("输入关键字搜索全部节点。中文标签、分类名、英文类型名都能搜到，"
                      "比如输入『存储存』或『ray』")
    bl_options = {"REGISTER", "UNDO"}

    node_type: StringProperty(name="节点类型", default="")

    def execute(self, context):
        # 通过搜索弹窗选中后,Blender 会把 bl_label 回填到 node_type;
        # 这里再用它去查真正的 bl_idname。
        if self.node_type:
            for cls in node_lib.ALL_CLASSES:
                if self.node_type in (cls.bl_idname, cls.bl_label):
                    return bpy.ops.gk.add_node(node_type=cls.bl_idname)
        return context.window_manager.invoke_search_popup(self)

    def invoke(self, context, event):
        if self.node_type:
            return self.execute(context)
        return context.window_manager.invoke_search_popup(self)

    def draw(self, context):
        """搜索弹窗的每一行。

        ── 为什么写成「分类 · 节点名」而不是只写节点名 ──────────────────
        87 个节点里有不少同名前缀(存档_写入 / 字典_写入 / 变量_设置…),
        只显示节点名的话搜出来一排"写入"根本分不清。
        带上分类之后既能区分，又能靠"输入分类名"来过滤
        (比如只打『存档』就只剩存档那几个)。
        """
        for category, classes in props.node_menu_entries(props.current_tree_type(context)):
            for cls in classes:
                op = self.layout.operator("gk.find_node", text=f"{category} · {cls.bl_label}")
                op.node_type = cls.bl_idname


# ==========================================================================
# 节点引用选择器
# ==========================================================================
class GK_MT_TargetPicker(bpy.types.Menu):
    bl_idname = "GK_MT_TARGET_PICKER"
    bl_label = "选择节点引用"

    node_name: StringProperty(default="")
    prop_name: StringProperty(default="gk_target")

    def draw(self, context):
        layout = self.layout
        tree = props.current_tree(context)
        node = tree.nodes.get(self.node_name) if tree is not None else None
        if node is None:
            layout.label(text="节点已不存在", icon="ERROR")
            return
        for key, text, description, icon, index in NB.node_items_for(node):
            op = layout.operator("gk.set_target", text=text, icon=icon)
            op.node_name = self.node_name
            op.prop_name = self.prop_name
            op.value = key


class GK_OT_SetTarget(bpy.types.Operator):
    """设置某个节点的节点引用。"""

    bl_idname = "gk.set_target"
    bl_label = "设置节点引用"
    bl_options = {"REGISTER", "UNDO"}

    node_name: StringProperty(default="")
    prop_name: StringProperty(default="gk_target")
    value: StringProperty(default="")

    def execute(self, context):
        tree = props.current_tree(context)
        if tree is None:
            return {"CANCELLED"}
        node = tree.nodes.get(self.node_name)
        if node is None or not hasattr(node, self.prop_name):
            self.report({"ERROR"}, "节点或属性不存在")
            return {"CANCELLED"}
        setattr(node, self.prop_name, self.value)
        return {"FINISHED"}


class GK_OT_PickTarget(bpy.types.Operator):
    """打开节点引用下拉菜单(由节点的下拉按钮调用)。"""

    bl_idname = "gk.pick_target"
    bl_label = "选择节点引用"
    bl_options = {"REGISTER"}

    node_name: StringProperty(default="")
    prop_name: StringProperty(default="gk_target")

    def execute(self, context):
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.popup_menu(
            _make_target_picker_draw(self.node_name, self.prop_name),
            title="选择节点",
            icon="NODE_SEL",
        )
        return {"FINISHED"}


def _make_target_picker_draw(node_name, prop_name):
    def draw(self, context):
        layout = self.layout
        tree = props.current_tree(context)
        node = tree.nodes.get(node_name) if tree is not None else None
        if node is None:
            layout.label(text="节点已不存在", icon="ERROR")
            return
        for key, text, description, icon, index in NB.node_items_for(node):
            op = layout.operator("gk.set_target", text=text, icon=icon)
            op.node_name = node_name
            op.prop_name = prop_name
            op.value = key

    return draw


# ==========================================================================
# 侧栏面板
# ==========================================================================
# ==========================================================================
# 物体属性面板:碰撞与物理(参考 UPBGE 的做法)
# ==========================================================================
# 为什么放在物体属性里而不是侧栏的导出面板:
#   碰撞形状、物理类型这些**属于物体本身**。选中谁就改谁,最直观;
#   放在导出面板里就得"先选中物体,再跳到另一个面板填参数",两处来回跑。
# 参考的就是 UPBGE —— 它在物体属性里加 Physics 面板,这个思路是对的。
class GK_PT_ObjectPhysics(bpy.types.Panel):
    bl_idname = "GK_PT_ObjectPhysics"
    bl_label = "游戏物理 (BlenderGameKit)"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"
    bl_category = "游戏物理"

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "object", None)
        return obj is not None and hasattr(obj, "gk_physics")

    def draw(self, context):
        layout = self.layout
        obj = context.object
        physics = obj.gk_physics
        settings = getattr(context.scene, "gk_settings", None)
        dimension = getattr(settings, "dimension", "3") if settings else "3"
        is_3d = str(dimension) != "2"

        # ---- 物体类型 ----
        box = layout.box()
        box.label(text=f"物体: {obj.name}", icon="OBJECT_DATAMODE")
        if obj.type != "MESH":
            box.label(text="只有网格物体会被导出", icon="INFO")

        col = box.column(align=True)
        _draw_body_type(col, physics, is_3d)

        # ---- 碰撞 ----
        box = layout.box()
        box.label(text="碰撞", icon="MESH_ICOSPHERE")
        col = box.column(align=True)
        col.prop(physics, "collision_shape", text="")

        shape = physics.collision_shape
        if shape == "auto":
            # 显示会自动算成什么,免得用户猜
            guess = auto_shape_for(obj, is_3d)
            col.label(text=f"按尺寸会选: {SHAPE_LABELS.get(guess, guess)}",
                      icon="CHECKMARK")
            # ── 手动尺寸会**覆盖**上面那个自动结果,必须说出来 ──────────
            # `shape_size` 只要是非 0,不管你选胶囊还是球,用的都是这组数字。
            # 用户实测踩过:选了「胶囊」却因为残留的手动尺寸,
            # 导出的碰撞体比模型又宽又高,角色走到别的模型跟前就穿模 ——
            # 而他完全看不出"自动"其实没生效。
            manual = tuple(round(float(v), 3) for v in physics.shape_size)
            if any(abs(v) > 1e-6 for v in manual):
                col.label(text=f"实际用手动尺寸 {manual}", icon="ERROR")
                col.label(text="清成 0 才会用上面的自动结果")
        elif shape not in ("none",):
            col.prop(physics, "shape_size")
            col.label(text="填了就用这组数字,不再自动贴合模型", icon="INFO")

        if shape != "none":
            row = col.row(align=True)
            row.prop(physics, "collision_layer", text="")
            row.prop(physics, "collision_mask", text="")
            col.prop(physics, "is_trigger")

        # ---- 物理属性 ----
        body = physics.body_type
        if "RigidBody" in body:
            box = layout.box()
            box.label(text="刚体", icon="PHYSICS")
            col = box.column(align=True)
            col.prop(physics, "mass")
            col.prop(physics, "gravity_scale")

        # ---- 脚本 ----
        box = layout.box()
        box.label(text="逻辑", icon="FILE_SCRIPT")
        col = box.column(align=True)
        col.prop(physics, "script_target")
        if physics.script_target:
            box.label(text="节点图导出的脚本会挂到这个物体上", icon="INFO")

        # ---- 视口预览 ----
        # 碰撞体在 Godot 里是运行时的东西,Blender 视口里本来看不见。
        box = layout.box()
        box.label(text="视口预览", icon="HIDE_OFF")

        col = box.column(align=True)
        # 只显示**活动物体**的碰撞盒 —— 多个叠在一起在视口里分不清谁是谁,
        # 很容易改错尺寸(用户反馈过)。
        col.label(text="碰撞体（只显示正在看的这个）:")
        row = col.row(align=True)
        row.operator("gk.show_collision", text="显示这一个",
                     icon="SHADING_WIRE")
        row.operator("gk.hide_collision", text="", icon="X")
        # 一次性显示全部是给"排查有没有漏设"用的
        col.operator("gk.show_collision", text="显示全部（排查用）"
                     ).target_all = True
        col.label(text="线框自动生成,不会导出", icon="INFO")


def _draw_body_type(layout, physics, is_3d):
    """物理类型下拉。按 2D/3D 过滤,免得给 3D 项目选到 2D 类型。

    ⚠ 这个函数曾经被我误删过 —— 插入新面板时连带删掉了它,
    结果 GK_PT_ObjectPhysics.draw 抛 NameError,
    整个『游戏物理』面板直接不显示(用户报告:碰撞体/描边的按钮找不到了)。
    Blender 对面板 draw 里抛的异常是**静默**的,界面只是少一块,
    所以这种错误必须靠测试兜住 —— 见 tests/blender_panels.py。
    """
    layout.prop(physics, "body_type", text="")


# 自动形状的显示名
SHAPE_LABELS = {
    "box": "盒子",
    "sphere": "球",
    "capsule": "胶囊",
}


def auto_shape_for(obj, is_3d=True):
    """按物体尺寸猜一个合适的碰撞形状。

    规则(和导出时用的是同一份逻辑,所以面板上显示的就是实际会生成的):
      * 三边接近相等        -> 球
      * 某一轴明显最长(人形) -> 胶囊
      * 其余(扁平的墙/地板) -> 盒子
    """
    return asset_export.guess_collision_shape(obj, is_3d)


class GK_PT_GodotLook(bpy.types.Panel):
    """让 3D 视口看起来像 Godot 渲染出来的样子。

    ── 为什么放在视口侧栏(N 面板)────────────────────────────────
    这是"看画面"的东西:调开关的时候眼睛正盯着模型,
    放在视口侧栏才不用来回切面板。
    也刻意**不放进主面板** —— 那边是导出流程,混在一起会更乱。
    """

    bl_idname = "GK_PT_GodotLook"
    bl_label = "Godot 观感 (BlenderGameKit)"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Godot"

    def draw(self, context):
        from . import godot_look

        layout = self.layout
        scene = getattr(context, "scene", None)
        if scene is None:
            layout.label(text="没有场景", icon="ERROR")
            return

        active = godot_look.is_active(scene)
        box = layout.box()
        if active:
            box.label(text="已开启:贴近 Godot", icon="CHECKMARK")
        else:
            box.label(text="当前是 Blender 默认观感", icon="INFO")

        row = box.row(align=True)
        row.scale_y = 1.5
        row.operator("gk.match_godot_look", text="预览贴近 Godot",
                     icon="WORLD")
        row.operator("gk.restore_look", text="", icon="LOOP_BACK")

        col = box.column(align=True)
        for line in godot_look.describe():
            col.label(text=line, icon="DOT" if line.startswith("·") else "NONE")

        # ---- Godot 界面英文的对照表 ----
        #
        # Godot 里有一批英文改不了:节点类型名是引擎类名(任何语言都不翻译),
        # 检查器的分组标题(Mesh / Transform / Script…)中文包也没覆盖。
        # 所以给一份能查的表,而不是让他去猜。
        box = layout.box()
        box.label(text="看不懂 Godot 里的英文?", icon="QUESTION")
        box.operator("gk.open_glossary", icon="TEXT")


class GK_PT_Main(bpy.types.Panel):
    bl_idname = "GK_PT_Main"
    bl_label = "游戏逻辑 (BlenderGameKit)"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "游戏逻辑"

    @classmethod
    def poll(cls, context):
        space = getattr(context, "space_data", None)
        return space is not None and getattr(space, "tree_type", "") == "GKGameLogicTree"

    def draw(self, context):
        layout = self.layout
        tree = props.current_tree(context)

        # ---- 空节点树时,先教怎么开始 ----
        # 47 个节点摆在那里但"该从哪开始"完全看不出来,这是新用户最大的坎。
        # 与其写一堆说明文字,不如直接给一条能跑的链路让他照着改。
        if tree is not None and len(tree.nodes) == 0:
            box = layout.box()
            box.label(text="这棵节点树还是空的", icon="INFO")
            col = box.column(align=True)
            col.label(text="怎么用:先放一个『事件』节点当入口,")
            col.label(text="再用金色『执行』线串起后面要做的事。")
            col.label(text="接完线后点『导出为 Godot 脚本』。")
            row = box.row(align=True)
            row.scale_y = 1.4
            row.operator("gk.starter_graph", text="生成示例节点图", icon="ADD")
            row.operator("gk.add_node_menu", text="", icon="NODETREE")
            layout.separator()

        # ---- 项目绑定(最上面,因为它是所有导出的落点)----
        _draw_project_box(layout, context)

        # ---- 导出脚本 ----
        box = layout.box()
        box.label(text="导出为 Godot 脚本", icon="EXPORT")
        row = box.row(align=True)
        row.scale_y = 1.4
        row.operator("gk.export_script", icon="FILE_TICK")
        row.operator("gk.preview_script", text="", icon="HIDE_OFF")

        settings = getattr(context.scene, "gk_settings", None)
        if settings is not None:
            col = box.column(align=True)
            row = col.row(align=True)
            row.prop(settings, "write_scene", toggle=True)
            row.operator("gk.open_export_folder", text="", icon="FILE_FOLDER")

        # ---- 模型导出:把 Blender 的网格 / 材质 / 贴图一起带进 Godot ----
        #
        # 材质跟着模型一起导出:没设材质就自动给默认白色。
        # 以前这里拆成"导出模型"和"导出材质"两个按钮,但用户实际
        # 永远是两个一起点 —— 拆开只是多一步操作,所以合并成一个。
        box = layout.box()
        box.label(text="导出模型和材质", icon="MESH_DATA")
        row = box.row(align=True)
        row.scale_y = 1.4
        row.operator("gk.export_assets", icon="EXPORT")
        box.label(text="选中网格 = 只导选中的；不选 = 导出全部", icon="INFO")
        box.label(text="没材质的物体会用默认白色材质", icon="INFO")
        box.label(text="导出会带上卡通材质；描边等额外效果请在 Godot 里加",
                  icon="INFO")

        last = ops.LAST_EXPORT
        if last:
            sub = box.column(align=True)
            sub.label(text=f"上次导出: {last.get('script_name', '')}"
                           f"（{last.get('lines', 0)} 行）", icon="CHECKMARK")
            for warning in last.get("warnings", [])[:4]:
                sub.label(text=warning, icon="ERROR")

        # ---- 节点树状态 ----
        box = layout.box()
        box.label(text="节点图", icon="NODETREE")
        if tree is None:
            box.label(text="当前没有游戏逻辑节点树", icon="INFO")
            box.operator("node.new_node_tree", text="新建节点树", icon="ADD").type = "GKGameLogicTree"
        else:
            events, statements = props.event_summary(tree)
            col = box.column(align=True)
            col.label(text=f"节点总数:{len(tree.nodes)}")
            col.label(text=f"事件:{events}    动作:{statements}")
            if events == 0:
                col.label(text="还没有事件节点，脚本不会执行任何逻辑", icon="ERROR")
            box.operator("gk.add_node_menu", text="添加节点", icon="ADD")

        # ---- 动画系统(独立那张树)----
        box = layout.box()
        box.label(text="动画系统(单独一张图)", icon="ARMATURE_DATA")
        anim_trees = [g for g in bpy.data.node_groups
                      if getattr(g, "bl_idname", "") == "GKAnimationTree"]
        if anim_trees:
            for tree in anim_trees:
                count = len([n for n in tree.nodes
                             if getattr(n, "bl_idname", "") == "GKNodeAnimState"])
                box.label(text=f"{tree.name}  ({count} 个状态)",
                          icon="NODETREE")
        else:
            box.label(text="还没有动画树", icon="INFO")
        box.operator("gk.open_anim_tree", text="打开/新建动画树",
                     icon="FILE_NEW")

        # ---- 导出设置 ----
        if settings is not None:
            box = layout.box()
            box.label(text="导出设置", icon="PREFERENCES")
            col = box.column(align=True)
            row = col.row(align=True)
            row.prop(settings, "dimension", expand=True)
            col.prop(settings, "base_class", text="")
            col.prop(settings, "class_name", text="类名")
            col.prop(settings, "node_name", text="节点名")
            row = col.row(align=True)
            row.prop(settings, "export_tunables", toggle=True)
            row.operator("gk.reset_settings", text="", icon="LOOP_BACK")

        # ---- 日志 ----
        if settings is not None and settings.show_log:
            box = layout.box()
            row = box.row()
            row.label(text="日志", icon="TEXT")
            row.prop(settings, "show_log", text="", icon="TRASH")
            records = gk_log.records()[-8:]
            if not records:
                box.label(text="(还没有日志)")
            for level, line in records:
                icon = {"ERROR": "ERROR", "WARN": "ERROR"}.get(level, "INFO")
                box.label(text=line[11:] if len(line) > 11 else line, icon=icon)


def _draw_project_box(layout, context):
    """画"项目"面板:这个 .blend 绑定的 Godot 项目。

    设计意图:导出目录不该是插件设置里的一个路径 —— 那意味着每开一个
    新 .blend 都要重新填。改成跟着 .blend 走之后,打开哪个文件就知道
    该写哪,面板只需要把当前绑定显示清楚。

    这里最容易让人困惑的是"打开 Godot 的按钮去哪了" —— 答案是
    项目还没建出来时不显示它(没东西可打开)。所以未建状态下必须
    把下一步说清楚,不能只放一个孤零零的按钮。
    """
    box = layout.box()
    box.label(text="Godot 项目", icon="FILE_FOLDER")

    if not bpy.data.filepath:
        box.label(text="这个 .blend 还没保存", icon="ERROR")
        box.label(text="先 Ctrl+S 存一次,项目会建在它旁边", icon="INFO")
        row = box.row(align=True)
        row.scale_y = 1.3
        row.operator("gk.create_project", text="创建 Godot 项目", icon="ADD")
        return

    # adopt=True:如果 .blend 在别人的 Godot 项目里,顺手把缺的标准子目录补上。
    # 不补的话所有导出都会失败,而"补空目录"是纯增量操作,不会覆盖东西。
    directory, reason = project.resolve(context.scene, adopt=True)
    status, icon = project.project_summary(directory)

    box.label(text=f"对应文件: {os.path.basename(bpy.data.filepath)}",
              icon="FILE_BLEND")

    sub = box.column(align=True)
    sub.label(text=directory or "(未绑定)", icon=icon)
    sub.label(text=f"状态: {status}", icon="INFO")
    sub.label(text=f"规则: {reason}", icon="INFO")

    if not project.is_project(directory):
        # ---- 还没建项目:把"下一步"讲清楚,别让人找按钮 ----
        warn = box.box()
        warn.label(text="项目还没建出来", icon="ERROR")
        warn.label(text="建好之后这里才会出现『打开 Godot』")
        row = warn.row(align=True)
        row.scale_y = 1.4
        row.operator("gk.create_project", text="创建 Godot 项目", icon="ADD")
        row = box.row(align=True)
        row.operator("gk.relink_project", text="换个位置", icon="FILE_FOLDER")
        row.operator("gk.reset_settings", text="重置", icon="LOOP_BACK")
    else:
        # ---- 项目已就绪 ----
        # 两个按钮是**不同**操作,必须能用文字区分开。
        # 之前一个写"打开 Godot 测试"、另一个只有个图标,两个都长得像
        # "打开编辑器" —— 难怪会点两次、开出两个窗口。
        row = box.row(align=True)
        row.scale_y = 1.5
        # 两个按钮都**显式**写 editor,不依赖默认值 ——
        # 属性一旦没传对就会跑错动作,显式写出来排除了这种可能。
        row.operator("gk.open_godot", text="打开 Godot 编辑器",
                     icon="WINDOW").editor = True
        row.operator("gk.open_export_folder", text="", icon="FILE_FOLDER")
        row = box.row(align=True)
        row.scale_y = 1.3
        row.operator("gk.open_godot", text="直接运行游戏(F5)",
                     icon="PLAY").editor = False
        # 运行前自动导出:不勾的话点运行跑的还是上次导出的旧内容
        prefs = project.preferences(context)
        if prefs is None or getattr(prefs, "auto_export_on_run", True):
            box.label(text="运行前会自动保存并导出", icon="INFO")
        else:
            box.label(text="运行前不会自动导出(改完记得手动导)", icon="ERROR")
        row = box.row(align=True)
        row.operator("gk.relink_project", text="改位置", icon="FILE_FOLDER")
        row.operator("gk.reset_settings", text="重置", icon="LOOP_BACK")

    # 2D / 3D 决定项目里生成什么,放这里合适
    settings = getattr(context.scene, "gk_settings", None)
    if settings is not None:
        row = box.row(align=True)
        row.prop(settings, "dimension", expand=True)

        # ---- 基类对不上的警告 ----
        #
        # 脚本的 `extends` 和节点的 `type` 必须一致,否则 Godot 直接拒绝加载:
        #     Script inherits from native type 'StaticBody3D',
        #     so it can't be assigned to an object of type: 'Area3D'
        # 然后整个场景加载失败,游戏跑不起来 —— 用户被一堆英文报错拦住,
        # 根本想不到是 Blender 这边的一个下拉框引起的。
        #
        # 默认是"自动",不可能对不上;只有用户手动选了基类才可能冲突。
        dimension = getattr(settings, "dimension", "3")
        clashes = props.base_class_mismatches(context.scene, dimension)
        if clashes:
            warn = layout.box()
            warn.label(text="脚本基类和节点类型对不上", icon="ERROR")
            for name, chosen, actual in clashes[:4]:
                warn.label(text=f"{name}: 脚本是 {chosen},节点会是 {actual}")
            warn.label(text="Godot 会拒绝加载脚本,游戏跑不起来")
            row = warn.row(align=True)
            row.scale_y = 1.3
            row.operator("gk.fix_base_class", text="改成『自动』修好",
                         icon="CHECKMARK")


def _draw_node_menu_contents(layout, context, nested=False):
    """节点菜单的内容。Shift+A 和右键菜单共用这一份。

    做成公共函数是因为两个入口必须**完全一致** —— 分别写两份的话，
    加个新分类很容易只加到一个入口里。

    ── nested 两种形态 ───────────────────────────────────────────────
    flat(默认):直接铺开「搜索 / 常用 / 最近 / 全部分类 / 自动连线」。
      右键菜单用这个 —— 一按就到，不用再点一层。

    nested:把这些内容收进一个『添加游戏逻辑节点』子菜单里。
      Shift+A 用这个:Blender 自带的『添加』菜单也在同一个菜单里，
      不收起来的话一屏里会出现两套并列的"加节点"，看起来就像重复了。
    """
    tree = props.current_tree(context)
    link_from = ""
    if tree is not None and getattr(tree, "nodes", None) is not None:
        active = getattr(tree.nodes, "active", None)
        if active is not None:
            link_from = active.name

    if nested:
        layout.menu(GK_MT_AddNodeCategories.bl_idname,
                    text="添加游戏逻辑节点", icon="NODETREE")
        layout.operator("gk.auto_link", text="把选中的两个节点连起来", icon="LINKED")
        return

    # ── flat 形态 ──────────────────────────────────────────────────
    # 最上面放搜索 —— 蓝图里最常用的入口。89 个节点靠翻分类找太慢,
    # 直接打字搜最快。
    row = layout.row(align=True)
    row.scale_y = 1.3
    row.operator("gk.find_node", text="搜索节点…", icon="VIEWZOOM")

    # 「常用」放第二:新手第一次打开不用翻分类就能起步。
    # 但**当前树里一个常用节点都没有时不画** —— 动画树里它就是空的
    # (实测:点开是空白菜单,比没有这一项更让人困惑)。
    if _menu_has_entries("常用", context, props.common_node_classes):
        layout.menu(_menu_name("常用"), text="常用", icon="SOLO_ON")

    # 「最近使用」**始终显示**,哪怕还是空的。
    # 一开始写成"空就不画",结果新用户第一次按 Shift+A 时菜单里没有这一项,
    # 等他用过几个节点之后菜单**形状变了** —— 这会让肌肉记忆失效。
    # 菜单结构应该稳定,空的时候由它的 draw 里显示"(还没用过任何节点)"。
    layout.menu(_menu_name("最近使用"), text="最近使用", icon="FILE_REFRESH")
    layout.separator()

    if link_from:
        layout.label(text=f"新节点会自动接到: {link_from}", icon="LINKED")

    # 全部分类。大类自带二级子菜单(见 props.node_menu_tree)
    #
    # ── 兜底:一个分类都没有时列**全部**(踩过的坑)──────────────────
    # 菜单按节点树类型过滤。但 `current_tree_type()` 在某些上下文里
    # 会返回 None(拿不到 space_data),那时过滤结果就是**空菜单** ——
    # 用户实测"shift+A那里没有东西"。
    #
    # 宁可多列几个分类(点错了会有人话报错,见 gk.add_node 的检查),
    # 也不能给一个空菜单 —— 空菜单让人以为功能坏了。
    kind = props.current_tree_type(context)
    entries = props.node_menu_tree(kind)
    if not entries:
        entries = props.node_menu_tree(None)
    for category, subs in entries:
        layout.menu(_menu_name(category), text=category)

    layout.separator()
    layout.operator("gk.auto_link", text="把选中的两个节点连起来", icon="LINKED")


def _draw_add_menu(self, context):
    """挂到 NODE_MT_add(Shift+A)。

    ── 为什么只放一个入口 ──────────────────────────────────────────
    用 prepend 而不是 append:**append 会把我们的菜单压到最底下** ——
    实测在默认面板高度下正好被截断,表现就是"按了 Shift+A 什么都没有",
    非常容易误判成功能没做。prepend 让它排在最上面,一按就看得见。

    但 Shift+A 里**同时还有 Blender 自带的『添加』**,直接铺开就会出现
    两套并列的"加节点"(用户实测反馈:看起来重复了)。
    所以这里只放一个入口,内容收进子菜单。
    右键菜单那边是 flat 形态 —— 一按就到,不用点两层。
    """
    # ── 别写死只认逻辑树(踩过三次的坑)────────────────────────────
    # 这里以前是 `tree_type != "GKGameLogicTree": return` ——
    # 于是**动画树里按 Shift+A 整个菜单是空的**(用户实测:
    # "shift+A那里没有东西")。
    #
    # 同一个错误在三个地方犯过:`properties.current_tree()`、
    # 菜单分类过滤、还有这里。教训:**凡是判断"是不是我们的树",
    # 一律走 `props.current_tree_type(context)`,不要自己写字符串比较。**
    if props.current_tree_type(context) is None:
        return
    _draw_node_menu_contents(self.layout, context, nested=True)


def _draw_context_menu(self, context):
    """挂到 NODE_MT_context_menu(节点上右键)。

    蓝图里右键就是"加节点",这里给**完整平铺菜单** —— 不用再点进子菜单。
    放在最前面,这样常用的加节点操作不用去翻 Blender 自带的那一长串。
    """
    # 同 _draw_add_menu:两类树都要认(见那里的说明)
    if props.current_tree_type(context) is None:
        return
    self.layout.separator()
    _draw_node_menu_contents(self.layout, context, nested=False)


def on_register():
    """注册后的自检与菜单挂钩。"""
    # ---- 挂菜单 ----
    for target_name, func in (
        ("NODE_MT_add", _draw_add_menu),
        ("NODE_MT_context_menu", _draw_context_menu),
    ):
        try:
            target = getattr(bpy.types, target_name, None)
            if target is None:
                gk_log.warn(f"找不到 {target_name},跳过挂载")
                continue
            # 先摘掉再挂,避免重复注册时挂两次(菜单里出现两份)
            try:
                target.remove(func)
            except Exception:
                pass
            target.prepend(func)
        except Exception as exc:  # pragma: no cover
            gk_log.error(f"挂载 {target_name} 失败: {exc}")

    # ---- 自检:确认每个分类子菜单都真的注册上了 ----
    # 动态生成的菜单类很容易因为一个小笔误(非法图标名、bl_idname 重复)
    # 而**静默**注册失败，所以这里主动查一遍并报出来。
    failed = [cls.__name__ for cls in CATEGORY_MENUS if not hasattr(cls, "bl_rna")]
    if failed:
        gk_log.error(f"分类菜单注册失败({len(failed)} 个): {', '.join(failed)}")
    else:
        gk_log.info(
            f"BlenderGameKit 已加载:{len(node_lib.ALL_CLASSES)} 个节点、"
            f"{len(CATEGORY_MENUS)} 个分类菜单可用"
        )


# ==========================================================================
# 菜单内容(Shift+A 与右键共用)
# ==========================================================================

def _recent_classes(limit=8):
    """最近用过的节点,按最近在前。"""
    by_id = {cls.bl_idname: cls for cls in node_lib.ALL_CLASSES}
    out = []
    for idname in list(ops.RECENT_NODES):
        cls = by_id.get(idname)
        if cls is not None and cls not in out:
            out.append(cls)
        if len(out) >= limit:
            break
    return out


# "最近使用"菜单要在 _recent_classes 定义之后才能建(它的 draw 里要用),
# 所以放在这里而不是文件开头。
RECENT_MENU = _make_recent_menu()
COMMON_MENU = _make_common_menu()


# 注册清单。这个元组必须在所有类都定义完之后才求值,
# 所以放在文件末尾附近 —— 放中间的话后面定义的类会漏注册。
GK_CLASSES = (
    GKGameLogicTree,
    GKAnimationTree,
    GK_OT_OpenAnimTree,
    GK_OT_SwitchTree,
    GK_PT_AnimTree,
    GK_OT_AddNodeMenu,
    GK_OT_FindNode,
    GK_MT_AddNodeCategories,
    COMMON_MENU,
    RECENT_MENU,
    GK_MT_TargetPicker,
    GK_OT_SetTarget,
    GK_OT_PickTarget,
    GK_PT_Main,
    GK_PT_ObjectPhysics,
    GK_PT_GodotLook,
) + tuple(CATEGORY_MENUS)


def on_unregister():
    # 摘掉菜单回调。不摘的话禁用插件后按 Shift+A / 右键
    # 会去调用已经不存在的菜单类,报一串错。
    for target_name, func in (
        ("NODE_MT_add", _draw_add_menu),
        ("NODE_MT_context_menu", _draw_context_menu),
    ):
        target = getattr(bpy.types, target_name, None)
        if target is None:
            continue
        try:
            target.remove(func)
        except Exception:
            pass
    ops.LAST_EXPORT.clear()
    ops.RECENT_NODES.clear()
