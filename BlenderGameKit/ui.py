"""UI:侧栏面板、添加节点菜单、节点引用选择器。"""

import os

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       IntProperty, StringProperty)

from . import codegen, diagnostics, export_report, exporter, gk_log, project
from . import asset_export
from . import operator_io as ops
from . import properties as props
from .nodes import nodes as node_lib
from .nodes import node_base as NB
from .nodes.node_base import snake_case


# ==========================================================================
# 节点树类型
# ==========================================================================
class GKGameLogicTree(bpy.types.NodeTree):
    """游戏逻辑节点树:在节点编辑器里用中文节点写游戏逻辑，导出 GDScript。"""

    bl_idname = "GKGameLogicTree"
    bl_label = "游戏逻辑"
    bl_icon = "NODETREE"
    bl_description = "BlenderGameKit 的游戏逻辑节点树(导出为 Godot GDScript)"

    gk_enabled: BoolProperty(
        name="参与导出", default=True,
        description="关掉 = 这张图暂时不导出(节点和连线都留着,随时能开回来)")

    # ── 这张逻辑图对外的**端口**(用户第 21 轮要的"真接口")───────────
    # 声明过的端口 = 导出成脚本变量 `输出_<名字>`;『逻辑_输出结果』
    # 从下拉里挑,别的图用『变量_读取』读同名端口 —— 导出时会校验
    # "被读写的端口有没有声明过",打错名字不再是静默失败。
    gk_ports: CollectionProperty(type=props.GKLogicPort)
    gk_port_index: IntProperty(name="选中端口", default=0)

    # ── 这张逻辑图声明的变量(用户 2026-10-04)───────────────────────
    # 声明过 = 侧栏一处定好名字/类型/初值,节点里从下拉里挑;
    # 没声明过的变量仍然能用『变量_设置』隐式建出来(兼容老图)。
    gk_vars: CollectionProperty(type=props.GKLogicVariable)
    gk_var_index: IntProperty(name="选中变量", default=0)

    # ── 这张图声明的**函数**(用户 2026-10-05 第十七轮)─────────────────
    # 用户原话:"可查看函数,自定义函数,折叠函数等"。
    # 名字/返回类型/参数在这里一处写好,『函数_定义』『函数_调用』
    # 从下拉里挑 —— 调用点的参数引脚按这份声明自动长出来。
    gk_funcs: CollectionProperty(type=props.GKLogicFunction)
    gk_func_index: IntProperty(name="选中函数", default=0)

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
                                                    "GKAnimationTree",
                                                    "GKUiDesignTree",
                                                    "GKGameAiTree",
                                                    "GKDisplayTree",
                                                    "GKAssetTree"):
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
            if wanted == "GKUiDesignTree":
                # 顺手放个起始控件(见 _new_ui_design_tree)
                tree = _new_ui_design_tree()
            elif wanted == "GKGameAiTree":
                # 顺手搭一个"巡逻 -> 追击 -> 回到巡逻"的骨架(见 _new_ai_tree)
                tree = _new_ai_tree()
            elif wanted == "GKDisplayTree":
                # 顺手把四张设置卡摆好(见 _new_display_tree)
                tree = _new_display_tree()
            elif wanted == "GKAssetTree":
                # 顺便把 .blend 里已有的资产收录成卡(见 _fill_asset_cards)
                tree = _asset_tree_or_new()
                _fill_asset_cards(tree)
            elif wanted == "GKAnimationTree":
                # 顺手放个起始状态,免得面对空图不知道从哪下手
                tree = bpy.data.node_groups.new("动画", "GKAnimationTree")
                state = tree.nodes.new("GKNodeAnimState")
                state.gk_state_name = "idle"
                # ── 不要再设 gk_is_default(踩过的坑)────────────────────
                # 这个属性**从来没有定义过** —— 给节点赋一个未声明的属性
                # Blender 会直接 `AttributeError`(实测确认),于是
                # 「切到动画树」这条路径一走到新建分支就报错。
                # 起始状态现在由**拓扑**决定(『进入』没被任何『转到』
                # 指到的那个),不需要勾选框,见 codegen._anim_tree_entry。
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

    # ── 这张树自己的自定义变量(侧栏『变量』那一栏)────────────────
    # 照虚幻蓝图:变量属于这一"个"动画蓝图,用『动画_取变量』
    # /『动画_设变量』节点拉进图里连。
    # GKAnimVariable 在 properties.py 里注册(注册顺序早于这里的
    # NodeTree,见 __init__._iter_classes),所以这里引用它是安全的。
    gk_variables: CollectionProperty(type=props.GKAnimVariable)
    gk_var_index: IntProperty(name="选中变量", default=0)

    # ── 这张图**参不参与导出**(用户 2026-10-03 第五轮)──────────────
    # 用户原话:"可以在场景集合那里添加节点图显示,可隐藏,可失效的场景功能"。
    # Blender 的『场景集合』(大纲视图)里没法自己加条目,但把大纲视图的显示
    # 模式切成 **Blend File** 就能看到所有节点组 —— 在那边选中、这里勾掉
    # 『参与导出』,效果就是"这张图失效"(保留着但不导出、不报错)。
    gk_enabled: BoolProperty(
        name="参与导出", default=True,
        description="关掉 = 这张图暂时不导出(节点和连线都留着,随时能开回来)")

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
            # 不设 gk_is_default —— 那个属性没定义过,赋值会 AttributeError。
            # 起始状态由连线拓扑决定(见 codegen._anim_tree_entry)。
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

        states = []
        transitions = []
        if tree is not None:
            for node in tree.nodes:
                kind = getattr(node, "bl_idname", "")
                if kind == "GKNodeAnimState":
                    states.append(node)
                elif kind == "GKNodeAnimTransition":
                    transitions.append(node)

        # ---- 空图时教怎么开始 ----
        # 以前的说明写的是"加一个『动画_切状态』卡片"和"勾上『起始』" ——
        # 那都是**旧方案**的说法:『动画_切状态』这个节点已经不在了,
        # 而"起始"现在不看勾选框、看连线(见下面 entry 的说明)。
        # 说明和实际对不上,用户照着做只会更糊涂。
        if tree is not None and not states and not transitions:
            box = layout.box()
            box.label(text="这张动画树还是空的", icon="INFO")
            col = box.column(align=True)
            col.label(text="1. 加一个『动画_状态』当起点")
            col.label(text="2. 选它的『动画』(状态名 = 动画名)")
            col.label(text="3. 加第二个『动画_状态』")
            col.label(text="── 中间不放过转场 ──")
            col.label(text="4a. 直接把状态1的『转到』连到状态2的『进入』")
            col.label(text="    意思 = 状态1 播完就切到状态2")
            col.label(text="── 要按条件切 ──")
            col.label(text="4b. 中间放一个『动画_转场』:")
            col.label(text="     状态1『转到』-> 转场『上游』")
            col.label(text="     转场『转到』-> 状态2『进入』")
            col.label(text="     转场『条件』-> 判断节点的输出")
            col.label(text="5. 一个状态可以接**多条**转场,")
            col.label(text="   从上往下第一个成立的赢(和虚幻 5 一样)")
            col.label(text="没有任何转场指向的状态 = 起始状态,进游戏就播它",
                      icon="INFO")
            row = box.row(align=True)
            row.operator("gk.add_node_menu", text="加状态",
                         icon="ADD").node_type = "GKNodeAnimState"
            row.operator("gk.add_node_menu", text="加转场",
                         icon="ADD").node_type = "GKNodeAnimTransition"

        if tree is not None and (states or transitions):
            box = layout.box()
            # ── 状态机:直接把**导出的那张图**摊开 ─────────────────────
            # 以前这里自己一套判断(谁没被『转到』指到、边从哪来),
            # 和 codegen 各写一份 —— 一旦两边的规则有出入,面板就在骗人。
            # 现在统一走 `nodes.anim_tree_graph`:导出用什么图,面板画什么图。
            _draw_anim_tree_graph(box, tree, states, transitions)

        # ── 自定义变量(照虚幻蓝图的 My Blueprint)──────────────────────
        # 放在状态清单之后、动作分类之前:它是"写这张动画图"最常用的东西。
        _draw_anim_variable_box(layout, context)

        # 动作分类**不在这里画**(用户第 24 轮:"尤其是那个动作分类" ——
        # 逻辑图里它只显示"未分类 · 待机"这种没用的一行)。
        # 它只属于动画树面板。

        # ── 回游戏逻辑图的按钮 ──────────────────────────────────────
        # 用户原话:"从游戏动画返回也应该加一个返回按钮,也是由于回去
        # 还要多按一遍"。切过来有按钮、切回去没有,是不对称的。
        row = layout.row(align=True)
        back = row.operator("gk.switch_tree", text="← 回到游戏逻辑",
                            icon="LOOP_BACK")
        back.target = "GKGameLogicTree"

        # 「添加节点」按钮也删了(第 24 轮):Shift+A 和右键菜单都在,
        # 侧栏再放一个只是占地方。
        # ── 这里**不再放导出按钮**(用户 2026-10-03)──────────────────
        # 用户原话:"游戏动画的导出按钮也直接删了,它是游戏逻辑的一部分,
        # 游戏逻辑导出了它也包含在内,或者加个提示在游戏逻辑那导出"。
        # 他说得对:导出永远是**从游戏逻辑那张图**发起的(运行游戏的按钮
        # 也在那边),在这边放一个会让人以为"要分别导出两次"。
        # 但直接删掉又会有另一个误会("动画图是不是导不出去?")——
        # 所以换成一行**说明**,把"去哪导出"讲清楚。
        hint = layout.box()
        hint.label(text="动画图跟着游戏逻辑一起导出", icon="INFO")
        hint.label(text="切到『游戏逻辑』那张图 -> 导出为 Godot 脚本")


# ==========================================================================
# 界面设计图 —— 第三张节点图(第四十轮,用户说"方便对UI设计")
# ==========================================================================
# ── 和前两张的关系(用户第 40 轮选的"套用式")──────────────────────
#     玩法图(游戏逻辑)              界面设计图(游戏界面)
#     ┌──────────────────┐          ┌─────────────────────────────┐
#     │ 每当_准备         │          │ [界面_面板 暂停菜单]         │
#     │  界面_套用设计 ───┼─引用────>│ [界面_文本 标题]  父=暂停菜单│
#     │  界面_按钮点击    │          │ [界面_按钮 继续]  父=暂停菜单│
#     └──────────────────┘          └─────────────────────────────┘
#
# 设计图里只管"长什么样"(控件、坐标、父子、样式);玩法图里管
# "什么时候显示 / 点了做什么"。控件引用一律**按名字**
# (『界面_隐藏控件』『界面_设置』『界面_按钮点击』都填节点名)。
#
# 界面节点**两棵树都能放**(老图一字不变)—— 这不是强制迁移,是多一个地方摆。
class GKUiDesignTree(bpy.types.NodeTree):
    """游戏界面节点树:控件摆在这里,由玩法图的『界面_套用设计』建出来。"""

    bl_idname = "GKUiDesignTree"
    bl_label = "游戏界面"
    bl_icon = "WINDOW"
    bl_description = ("BlenderGameKit 的界面设计图 —— 控件(文本 / 按钮 / 面板 / "
                      "列表…)摆在这里,玩法图放一个『界面_套用设计』引用它")

    # ── 视口叠加预览(用户第 40 轮选的"清单 + 视口叠加")───────────────
    # 编辑这张图时,3D 视口里按屏幕比例把控件画成框 —— 不用导出就能看出
    # "东西大概摆在哪"。默认开,嫌挡就在侧栏里关掉。
    gk_preview: BoolProperty(
        name="视口预览", default=True,
        description="编辑这张设计图时,在 3D 视口上按屏幕比例叠画控件框")

    # 预览的"屏幕"多大 —— 就是游戏窗口的像素尺寸(和 Godot 工程一致)。
    # 控件里填的 X / Y / 宽 / 高 都是这个坐标系里的像素。
    gk_design_width: IntProperty(name="设计宽", default=1152, min=64, max=7680)
    gk_design_height: IntProperty(name="设计高", default=648, min=64, max=7680)

    @classmethod
    def poll(cls, context):
        return True

    @classmethod
    def draw_header(cls, layout, context):
        layout.label(text="控件摆这里,玩法图用『界面_套用设计』引用", icon="INFO")


def _new_ui_design_tree():
    """新建一张界面设计图,顺手放一个起始控件。

    照动画树"新建时放一个起始状态"的做法 —— 面对一张空图,
    十个有九个不知道第一个该放什么。
    """
    tree = bpy.data.node_groups.new("主界面", "GKUiDesignTree")
    title = tree.nodes.new("GKNodeUiText")
    title.name = "标题"
    title.location = (0.0, 0.0)
    title.gk_text = "标题"
    return tree


class GK_OT_OpenUiDesignTree(bpy.types.Operator):
    """打开界面设计图;没有就新建一张。

    和『打开/新建动画树』同一个理由:设计图是**另一张节点图**,
    新手不知道要去节点编辑器的类型下拉里切。
    """

    bl_idname = "gk.open_ui_design_tree"
    bl_label = "打开/新建界面设计图"
    bl_description = "切到『游戏界面』设计图(没有就新建一张并放一个起始控件)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        trees = [g for g in bpy.data.node_groups
                 if getattr(g, "bl_idname", "") == "GKUiDesignTree"]
        tree = trees[0] if trees else None
        if tree is None:
            tree = _new_ui_design_tree()
            self.report({"INFO"}, f"已新建界面设计图『{tree.name}』")

        space = _node_space(context)
        if space is None:
            self.report({"WARNING"},
                        "没找到节点编辑器 —— 请打开一个『节点编辑器』区域")
            return {"CANCELLED"}
        space.tree_type = "GKUiDesignTree"
        space.node_tree = tree
        return {"FINISHED"}


# ── 控件清单的顺序 / 图标(侧栏清单和视口预览共用)────────────────────
_UI_WIDGET_ICONS = {
    "GKNodeUiText": "FONT_DATA",
    "GKNodeUiBar": "COLOR",
    "GKNodeUiButton": "PLAY",
    "GKNodeUiImage": "IMAGE_DATA",
    "GKNodeUiPanel": "MOD_OPACITY",
    "GKNodeUiList": "LINENUMBERS_ON",
    "GKNodeUiNinePatch": "MESH_GRID",
    "GKNodeUiInput": "GREASEPENCIL",
}


def _ui_design_widget_rows(nodes):
    """控件按父子关系排成 `[(控件, 层级)]`:父母在前、子项缩进。

    ── 为什么不用 id()/is 认同一个节点(踩过的坑)──────────────────────
    Blender 里 `tree.nodes[i]` 和顺着属性拿到的可能是**两个 Python 包装
    对象**(见 nodes.node_key 的说明)。所以身份一律走 `node_lib.node_key`,
    和 codegen 那边同一套判断。
    """
    by_name = {}
    for node in nodes:
        by_name.setdefault(str(getattr(node, "name", "")), node)
    children = {}
    roots = []
    for node in nodes:
        parent = by_name.get(str(getattr(node, "gk_parent_widget", "") or "").strip())
        if parent is not None and node_lib.node_key(parent) != node_lib.node_key(node):
            children.setdefault(str(getattr(parent, "name", "")), []).append(node)
        else:
            roots.append(node)

    rows = []

    def walk(node, depth):
        rows.append((node, depth))
        for child in children.get(str(getattr(node, "name", "")), ()):
            walk(child, depth + 1)

    for node in roots:
        walk(node, 0)
    # 环形父子(甲挂乙、乙挂甲)会让两个都进不了 roots —— 兜底补在末尾,
    # 不能因为用户摆错就"清单里凭空少两个控件"。
    listed = {node_lib.node_key(node) for node, _depth in rows}
    for node in nodes:
        if node_lib.node_key(node) not in listed:
            rows.append((node, 0))
    return rows


def _ui_preview_size(node):
    """控件的**大致**尺寸(设计像素)—— 视口预览用。

    文字控件没有宽高(size 就是文字本身),按"字数 × 字号"估一个;
    图片的"0 = 用原图尺寸"这里拿不到原图,退回 128。
    """
    kind = getattr(node, "bl_idname", "")
    if kind == "GKNodeUiText":
        size = float(getattr(node, "gk_size", 24) or 24)
        text = str(getattr(node, "gk_text", "") or "")
        return (max(60.0, len(text) * size * 1.05), size * 1.4)
    width = float(getattr(node, "gk_width", 0.0) or 0.0)
    height = float(getattr(node, "gk_height", 0.0) or 0.0)
    return (width if width > 0.0 else 128.0,
            height if height > 0.0 else 128.0)


def _ui_preview_rects(tree):
    """算出每个控件在"设计像素"里的矩形:`[(名字, x, y, 宽, 高, 层级)]`。

    ── 这是**近似**预览(面板上也是这么写的)──────────────────────────
    * 对齐 = 左上:直接读 X / Y(和生成的代码一致);
    * 其它对齐:按 Godot 预设的语义在**父控件(或屏幕)矩形**里算 ——
      "屏幕右上角留 24"这类意图能看出来;
    * 挂在『界面_列表』里的子控件由容器排版,这里从容器左上角起流水排;
    * 文字宽度 / 图片原始尺寸拿不到就估,做不到像素级精确 —— 但
      "谁压住了谁、有没有摆到屏幕外"这些**真正需要看出来的问题**够用。
    """
    widgets = [n for n in (getattr(tree, "nodes", ()) or ())
               if getattr(n, "gk_ui_builder", False)]
    if not widgets:
        return []
    screen_w = float(getattr(tree, "gk_design_width", 1152) or 1152)
    screen_h = float(getattr(tree, "gk_design_height", 648) or 648)
    list_names = {}
    for node in widgets:
        if getattr(node, "bl_idname", "") == "GKNodeUiList":
            list_names[str(getattr(node, "name", ""))] = float(
                getattr(node, "gk_separation", 8) or 8.0)

    rects = {}
    out = []
    row_cursor = {}          # 列表容器 -> 下一个子控件排到哪了(竖排)
    for node, depth in _ui_design_widget_rows(widgets):
        name = str(getattr(node, "name", ""))
        width, height = _ui_preview_size(node)
        parent_name = str(getattr(node, "gk_parent_widget", "") or "").strip()
        parent = rects.get(parent_name) or (0.0, 0.0, screen_w, screen_h)
        px, py, pw, ph = parent
        align = str(getattr(node, "gk_align", "TOP_LEFT") or "TOP_LEFT")
        margin = float(getattr(node, "gk_margin", 24.0) or 0.0)

        if parent_name in list_names:
            # 容器排版:从容器左上角往下流水摆(间距照『间距』属性估)
            separation = list_names[parent_name]
            cursor = row_cursor.get(parent_name, (px, py))
            x, y = cursor
            row_cursor[parent_name] = (x, y + height + separation)
        elif align == "TOP_LEFT":
            x = px + float(getattr(node, "gk_x", 20.0) or 0.0)
            y = py + float(getattr(node, "gk_y", 20.0) or 0.0)
        elif align == "TOP_CENTER":
            x, y = px + (pw - width) * 0.5, py + margin
        elif align == "TOP_RIGHT":
            x, y = px + pw - width - margin, py + margin
        elif align == "CENTER_LEFT":
            x, y = px + margin, py + (ph - height) * 0.5
        elif align == "CENTER":
            x, y = px + (pw - width) * 0.5, py + (ph - height) * 0.5
        elif align == "CENTER_RIGHT":
            x, y = px + pw - width - margin, py + (ph - height) * 0.5
        elif align == "BOTTOM_LEFT":
            x, y = px + margin, py + ph - height - margin
        elif align == "BOTTOM_CENTER":
            x, y = px + (pw - width) * 0.5, py + ph - height - margin
        else:                                        # BOTTOM_RIGHT
            x, y = px + pw - width - margin, py + ph - height - margin

        rects[name] = (x, y, width, height)
        # 第七项把**节点本身**带出去(第四十六轮):预览要按控件自己的
        # 颜色 / 文字 / 数值画(WYSIWYG),光有矩形不够。
        # 前六项的顺序保持不动 —— smoke 里的断言按老下标读。
        out.append((name, x, y, width, height, depth, node))
    return out


# 预览里每一层的颜色(按层级轮换 —— 没设颜色的控件一眼看出"这是谁的子控件")
_UI_PREVIEW_COLORS = (
    (0.25, 0.58, 0.95, 0.32),
    (0.30, 0.80, 0.55, 0.32),
    (0.95, 0.72, 0.25, 0.32),
    (0.80, 0.45, 0.85, 0.32),
)


def _ui_preview_rect_color(node, depth):
    """预览里的填充色:**优先用设计图里的颜色**,没设过才按层级配色。

    第四十六轮(用户:"没有预览到实时显示修改UI"):以前不管控件配了什么色,
    预览一律画"按层级配色的灰框"—— 改了颜色 / 透明度在视口里完全看不出来。
    现在直接用控件自己的 `gk_color`(太透的给个 0.25 下限,不然看不见)。
    """
    raw = getattr(node, "gk_color", None)
    try:
        red, green, blue, alpha = (float(raw[0]), float(raw[1]),
                                   float(raw[2]), float(raw[3]))
        if alpha < 0.25:
            alpha = 0.25
        return (red, green, blue, alpha)
    except Exception:                                     # noqa: BLE001
        return _UI_PREVIEW_COLORS[depth % len(_UI_PREVIEW_COLORS)]


def _ui_preview_average_color(node):
    """图片控件的"代表色":采样它那张图(缩略图太贵,取平均色就够认)。

    采样失败(图丢了 / 太大)就给个中性灰 —— 预览不该因此罢工。
    """
    try:
        import bpy
        image = bpy.data.images.get(str(getattr(node, "gk_image", "") or ""))
        size = getattr(image, "size", None)
        if image is None or not size or int(size[0]) == 0:
            return (0.35, 0.35, 0.40, 0.85)
        pixels = list(getattr(image, "pixels", ())[:4 * 64])   # 头 16 像素
        if len(pixels) < 4:
            return (0.35, 0.35, 0.40, 0.85)
        count = len(pixels) // 4
        red = sum(pixels[0::4]) / count
        green = sum(pixels[1::4]) / count
        blue = sum(pixels[2::4]) / count
        return (red, green, blue, 0.85)
    except Exception:                                     # noqa: BLE001
        return (0.35, 0.35, 0.40, 0.85)

# 挂在 3D 视口上的绘制句柄(注册时加、注销时摘)
_UI_PREVIEW_HANDLE = []


def _ui_preview_tree():
    """找出正在编辑的界面设计图(没在编辑 / 关了预览就返回 None)。"""
    context = bpy.context
    screen = getattr(context, "screen", None)
    for area in getattr(screen, "areas", ()) or ():
        if getattr(area, "type", "") != "NODE_EDITOR":
            continue
        for space in getattr(area, "spaces", ()) or ():
            if getattr(space, "tree_type", "") != "GKUiDesignTree":
                continue
            tree = getattr(space, "edit_tree", None) or getattr(space, "node_tree", None)
            if tree is None or str(getattr(tree, "bl_idname", "")) != "GKUiDesignTree":
                continue
            if not getattr(tree, "gk_preview", True):
                continue
            return tree
    return None


def _ui_preview_draw():
    """3D 视口上的叠加预览(POST_PIXEL 绘制回调)。

    ── 三条纪律 ────────────────────────────────────────────────────
    1. **不许抛异常**:绘制回调里冒异常会刷满控制台、还会连累别的绘制,
       所以整段包在 try 里(宁可这一帧不画);
    2. 不用完的 GPU 状态要还原(blend 模式);
    3. 只在"正编辑设计图 + 勾了预览"时画 —— 别的场合一帧都不多花。
    """
    try:
        tree = _ui_preview_tree()
        if tree is None:
            return
        region = getattr(bpy.context, "region", None)
        if region is None or region.width < 80 or region.height < 80:
            return
        import blf
        import gpu
        from gpu_extras.batch import batch_for_shader

        rw, rh = float(region.width), float(region.height)
        design_w = float(getattr(tree, "gk_design_width", 1152) or 1152)
        design_h = float(getattr(tree, "gk_design_height", 648) or 648)
        scale = min((rw - 48.0) / design_w, (rh - 48.0) / design_h)
        if scale <= 0.0:
            return
        box_w, box_h = design_w * scale, design_h * scale
        x0 = (rw - box_w) * 0.5
        y0 = (rh - box_h) * 0.5

        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        gpu.state.blend_set("ALPHA")
        try:
            # 屏幕框:底色 + 亮边(这就是"游戏窗口"的范围)
            quad = [(x0, y0), (x0 + box_w, y0), (x0 + box_w, y0 + box_h),
                    (x0, y0), (x0 + box_w, y0 + box_h), (x0, y0 + box_h)]
            batch = batch_for_shader(shader, "TRIANGLES", {"pos": quad})
            shader.bind()
            shader.uniform_float("color", (0.04, 0.05, 0.08, 0.55))
            batch.draw(shader)
            frame = [(x0, y0), (x0 + box_w, y0), (x0 + box_w, y0), (x0, y0 + box_h),
                     (x0 + box_w, y0 + box_h), (x0, y0 + box_h),
                     (x0, y0 + box_h), (x0, y0)]
            batch = batch_for_shader(shader, "LINES", {"pos": frame})
            shader.bind()
            shader.uniform_float("color", (0.55, 0.75, 1.0, 0.9))
            batch.draw(shader)

            # ── 控件:所见即所得(第四十六轮)────────────────────────────────
            # 每个控件用**它自己的颜色**填底,再按类型补内容:
            #   文本 -> 真文字(字号按设计像素 × 屏幕缩放);按钮 -> 居中文字;
            #   进度条 -> 按『当前值/最大值』画比例;图片 -> 平均色打底;
            #   非文字控件补一个小名字(方便对着节点找)。
            # 全部**每帧重读节点值** —— 改设计图里的任何参数,视口这一帧就变。
            fills = []
            texts = []
            bars = []
            for name, rx, ry, width, height, depth, node in _ui_preview_rects(tree):
                left = x0 + rx * scale
                bottom = y0 + (design_h - ry - height) * scale
                right = left + width * scale
                top = bottom + height * scale
                blob = str(getattr(node, "bl_idname", ""))
                color = (_ui_preview_average_color(node)
                         if blob == "GKNodeUiImage"
                         else _ui_preview_rect_color(node, depth))
                fills.append((((left, bottom), (right, bottom), (right, top),
                               (left, bottom), (right, top), (left, top)), color))
                fills.append((((left, bottom), (right, bottom),
                               (right, bottom), (right, top),
                               (right, top), (left, top),
                               (left, top), (left, bottom)),
                              (0.85, 0.92, 1.0, 0.45)))
                if blob == "GKNodeUiBar":
                    try:
                        value = float(getattr(node, "gk_value", 0.0) or 0.0)
                        biggest = float(getattr(node, "gk_max", 1.0) or 1.0)
                        ratio = 0.0 if biggest <= 0.0 else max(
                            0.0, min(1.0, value / biggest))
                    except Exception:                     # noqa: BLE001
                        ratio = 0.0
                    bars.append((left, bottom, left + (right - left) * ratio, top))
                text = str(getattr(node, "gk_text", "") or "")
                raw_color = getattr(node, "gk_color", (1.0, 1.0, 1.0, 1.0))
                try:
                    text_color = (float(raw_color[0]), float(raw_color[1]),
                                  float(raw_color[2]), 1.0)
                except Exception:                         # noqa: BLE001
                    text_color = (1.0, 1.0, 1.0, 1.0)
                size = max(9.0, min(44.0,
                                    float(getattr(node, "gk_size", 22.0) or 22.0)
                                    * scale))
                if blob in ("GKNodeUiText", "GKNodeUiButton") and text:
                    if blob == "GKNodeUiButton":
                        try:
                            blf.size(0, size)
                            text_w, text_h = blf.dimensions(0, text)
                        except Exception:                 # noqa: BLE001
                            text_w, text_h = len(text) * size * 0.6, size
                        texts.append((text, (left + right - text_w) * 0.5,
                                      (bottom + top - text_h) * 0.5,
                                      size, text_color))
                    else:
                        texts.append((text, left + 3.0, top - size - 2.0,
                                      size, text_color))
                else:
                    texts.append((name, left + 3.0, top - 13.0, 11.0,
                                  (0.85, 0.9, 1.0, 0.75)))
            for verts, color in fills:
                batch = batch_for_shader(
                    shader, "LINES" if len(verts) == 8 else "TRIANGLES",
                    {"pos": verts})
                shader.bind()
                shader.uniform_float("color", color)
                batch.draw(shader)
            for left, bottom, right, top in bars:
                if right - left <= 0.5:
                    continue
                verts = ((left, bottom), (right, bottom), (right, top),
                         (left, bottom), (right, top), (left, top))
                batch = batch_for_shader(shader, "TRIANGLES", {"pos": verts})
                shader.bind()
                shader.uniform_float("color", (0.35, 0.85, 0.45, 0.85))
                batch.draw(shader)
            for text, tx, ty, size, color in texts:
                blf.size(0, size)
                blf.color(0, color[0], color[1], color[2], color[3])
                blf.position(0, tx, ty, 0.0)
                blf.draw(0, text)
        finally:
            gpu.state.blend_set("NONE")
    except Exception:                                     # noqa: BLE001
        # 绘制回调的异常没有任何去处 —— 静默跳过这一帧
        return


def _remove_ui_preview_handler():
    """摘掉视口绘制回调(注销 / 重载时各调一次,重复调没事)。"""
    import bpy as _bpy
    while _UI_PREVIEW_HANDLE:
        handle = _UI_PREVIEW_HANDLE.pop()
        try:
            _bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
        except Exception:                                 # noqa: BLE001
            pass


class GK_PT_UiDesign(bpy.types.Panel):
    """界面设计图专属面板:控件清单 + 预览开关 + 用法提示。"""

    bl_idname = "GK_PT_UiDesign"
    bl_label = "游戏界面 (BlenderGameKit)"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "游戏界面"

    @classmethod
    def poll(cls, context):
        space = getattr(context, "space_data", None)
        return space is not None and getattr(space, "tree_type", "") == "GKUiDesignTree"

    def draw(self, context):
        layout = self.layout
        tree = props.current_tree(context)
        if tree is None:
            layout.label(text="没有界面设计图", icon="INFO")
            return

        widgets = [n for n in tree.nodes if getattr(n, "gk_ui_builder", False)]

        # ---- 概览 ----
        box = layout.box()
        box.label(text=f"『{tree.name}』:{len(widgets)} 个控件", icon="NODETREE")
        if not widgets:
            box.label(text="还没摆控件 —— 先加一个『界面_文本』试试",
                      icon="INFO")
            row = box.row(align=True)
            row.operator("gk.add_node_menu", text="文本",
                         icon="ADD").node_type = "GKNodeUiText"
            row.operator("gk.add_node_menu", text="面板",
                         icon="ADD").node_type = "GKNodeUiPanel"
            row.operator("gk.add_node_menu", text="按钮",
                         icon="ADD").node_type = "GKNodeUiButton"

        # ---- 控件清单(父 -> 子缩进,游戏里也按这个顺序建)----
        if widgets:
            box = layout.box()
            box.label(text="控件清单(父 → 子)", icon="LINENUMBERS_ON")
            known = {str(getattr(n, "name", "")) for n in widgets}
            for widget, depth in _ui_design_widget_rows(widgets):
                name = str(getattr(widget, "name", ""))
                parent_name = str(getattr(widget, "gk_parent_widget", "") or "").strip()
                row = box.row(align=True)
                icon = _UI_WIDGET_ICONS.get(getattr(widget, "bl_idname", ""), "NODE")
                if parent_name and parent_name not in known:
                    # 父控件填了个这张图里没有的名字 -> 生成时会告警 + 挂到根下
                    icon = "ERROR"
                row.label(text="    " * depth + name, icon=icon)
                pick = row.operator("gk.select_node", text="", icon="VIEWZOOM")
                pick.node_name = name
            missing = [str(n.name) for n in widgets
                       if str(getattr(n, "gk_parent_widget", "") or "").strip()
                       and str(getattr(n, "gk_parent_widget", "") or "").strip() not in known]
            if missing:
                box.label(text=f"父控件名字对不上:{'、'.join(missing[:3])}",
                          icon="ERROR")

        # ---- 预览 ----
        box = layout.box()
        box.label(text="预览(叠画在 3D 视口上)", icon="VIEWZOOM")
        row = box.row(align=True)
        row.prop(tree, "gk_preview", text="", toggle=True)
        row.label(text="显示控件框" if getattr(tree, "gk_preview", True) else "已关闭")
        row = box.row(align=True)
        row.prop(tree, "gk_design_width", text="宽")
        row.prop(tree, "gk_design_height", text="高")
        box.label(text="按这块屏幕的比例画(近似预览)", icon="INFO")

        # ---- 用法 ----
        box = layout.box()
        box.label(text="玩法图放『界面_套用设计』引用本图", icon="INFO")
        box.label(text="点了做什么 -> 『界面_按钮点击』填控件名")
        row = box.row(align=True)
        back = row.operator("gk.switch_tree", text="← 回到游戏逻辑",
                            icon="LOOP_BACK")
        back.target = "GKGameLogicTree"


# ==========================================================================
# AI 图 —— 第四张节点图(第四十一轮,用户说"添加游戏AI逻辑编辑器")
# ==========================================================================
# 用户原话:"添加游戏AI逻辑编辑器,这样分类开不会后期混乱"。
# 交互完全照动画树(状态卡 + 转场卡 + 起始状态 = 谁都没指到的那个),
# 区别只在状态里干的事:动画状态播动画,AI 状态**做行为**。
#
#     玩法图(游戏逻辑)              AI 图(游戏AI)
#     ┌───────────────┐           ┌──────────────────────────┐
#     │ 每当_准备      │           │ [AI_状态: 巡逻] 行为=巡逻   │
#     │  AI_启用 ──────┼── 驱动 ──>│   ├─转场:看见玩家─> [追击]  │
#     └───────────────┘           │   └─转场:丢失玩家─> [巡逻]  │
#                                 └──────────────────────────┘
class GKGameAiTree(bpy.types.NodeTree):
    """游戏 AI 节点树:巡逻 / 追击 / 逃跑这些规则画在这里,
    由玩法图的『AI_启用』节点驱动。"""

    bl_idname = "GKGameAiTree"
    bl_label = "游戏AI"
    bl_icon = "TRACKER"
    bl_description = ("BlenderGameKit 的 AI 图 —— 『AI_状态』(巡逻 / 追击…)"
                      "和『AI_转场』(看见 / 丢失目标…)摆在这里,"
                      "玩法图放一个『AI_启用』引用它")

    # ── 视口叠加预览(和界面设计图同一套)─────────────────────────────
    # 编辑这张图时,3D 视口里画**巡逻路线**和**感知范围圈** ——
    # 不用导出就能看出"怪会走到哪、看得到多远"。默认开,嫌挡就在侧栏里关。
    gk_preview: BoolProperty(
        name="视口预览", default=True,
        description="编辑这张 AI 图时,在 3D 视口上画巡逻路线和感知范围圈")

    @classmethod
    def poll(cls, context):
        return True

    @classmethod
    def draw_header(cls, layout, context):
        layout.label(text="AI 规则画这里,玩法图用『AI_启用』引用", icon="INFO")


def _new_ai_tree():
    """新建一张 AI 图,顺手搭一个**能跑的最小 AI**:巡逻 → 看见追 → 追丢回。

    照界面设计图"新建时放一个起始控件"的做法 —— 面对一张空图,
    十个有九个不知道第一个该放什么。

    ── 为什么这里敢连成"环" ────────────────────────────────────────
    动画树那边的注释写着"Blender 不允许环,连线连成环会报『链接形式的
    循环不受支持』" —— 第四十一轮**实测**(Blender 4.5.5):AI 图里
    连成环没问题,保存 / 重新打开都正常,解析也认(见冒烟测试)。
    所以这里用连线搭出完整的环 —— 比只用下拉直观得多。
    『从』『到』下拉仍然支持(见 gk_from_state / gk_to_state):
    手建转场时忘了连线也能用,两个都填时**连线优先**。
    """
    tree = bpy.data.node_groups.new("敌人AI", "GKGameAiTree")
    # 没有用户引用它的节点树,存盘时会**被直接丢掉**(踩过的坑,见 presets):
    # 『AI_启用』按**名字**引用它,那不算 Blender 的数据块引用 —— 必须 fake user。
    tree.use_fake_user = True

    patrol = tree.nodes.new("GKNodeAiState")
    patrol.name = "巡逻"
    patrol.location = (-40.0, 0.0)
    patrol.gk_state_name = "巡逻"
    patrol.gk_behavior = "PATROL"

    chase = tree.nodes.new("GKNodeAiState")
    chase.name = "追击"
    chase.location = (660.0, 0.0)
    chase.gk_state_name = "追击"
    chase.gk_behavior = "CHASE"

    to_chase = tree.nodes.new("GKNodeAiTransition")
    to_chase.name = "看见玩家"
    to_chase.location = (310.0, 160.0)
    to_chase.gk_cond_source = "SEE"
    to_chase.gk_from_state = "巡逻"
    to_chase.gk_to_state = "追击"

    back = tree.nodes.new("GKNodeAiTransition")
    back.name = "丢失玩家"
    back.location = (310.0, -190.0)
    back.gk_cond_source = "LOST"
    back.gk_from_state = "追击"
    back.gk_to_state = "巡逻"

    tree.links.new(patrol.outputs["转到"], to_chase.inputs["上游"])
    tree.links.new(to_chase.outputs["转到"], chase.inputs["进入"])
    tree.links.new(chase.outputs["转到"], back.inputs["上游"])
    tree.links.new(back.outputs["转到"], patrol.inputs["进入"])
    return tree


class GK_OT_OpenAiTree(bpy.types.Operator):
    """打开 AI 图;没有就新建一张。

    和『打开/新建动画树』『打开/新建界面设计图』同一个理由:
    它是**另一张节点图**,新手不知道要去节点编辑器的类型下拉里切。
    """

    bl_idname = "gk.open_ai_tree"
    bl_label = "打开/新建 AI 图"
    bl_description = "切到『游戏AI』图(没有就新建一张,带巡逻/追击的起始骨架)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        trees = [g for g in bpy.data.node_groups
                 if getattr(g, "bl_idname", "") == "GKGameAiTree"]
        tree = trees[0] if trees else None
        if tree is None:
            tree = _new_ai_tree()
            self.report({"INFO"}, f"已新建 AI 图『{tree.name}』")

        space = _node_space(context)
        if space is None:
            self.report({"WARNING"},
                        "没找到节点编辑器 —— 请打开一个『节点编辑器』区域")
            return {"CANCELLED"}
        space.tree_type = "GKGameAiTree"
        space.node_tree = tree
        return {"FINISHED"}


# ==========================================================================
# 第五种节点树:『游戏显示』设置图(第四十二轮)
# ==========================================================================
# 用户原话:"添加游戏显示设置编辑器,整合如物理显示等的排列,不然不好找";
# 动工前在四个形态里点了**新节点树类型**(和游戏逻辑 / 动画 / 界面 / AI
# 同一个机制:注册 bpy.types.NodeTree 就自动出现在编辑器类型菜单里)。
#
# 这张图不是逻辑图,是一张**设置控制台**:四张卡各管一类显示设置
# (物理线框 / 调试面板 / 画面风格 / 视口预览)。卡上的开关**直接读写
# 当前场景的设置**(写透)—— 不存在"忘了点应用",老工程也不用迁移。
class GKDisplayTree(bpy.types.NodeTree):
    """游戏显示设置图:显示 / 调试 / 画面类开关的分类控制台。"""

    bl_idname = "GKDisplayTree"
    bl_label = "游戏显示"
    bl_icon = "HIDE_OFF"
    bl_description = ("BlenderGameKit 的显示设置图 —— 物理线框 / 调试面板 / "
                      "画面风格 / 视口预览 / 环境氛围 五张设置卡摆在这里,"
                      "卡上改的就是**当前场景**的设置,导出立刻生效")

    @classmethod
    def poll(cls, context):
        return True

    @classmethod
    def draw_header(cls, layout, context):
        layout.label(text="卡上的开关直接改当前场景的设置,不用点应用",
                     icon="INFO")


def _fill_display_cards(tree):
    """把缺的设置卡补上(已有的不动)。返回补了几张。"""
    added = 0
    for blob in node_lib.missing_display_cards(tree):
        node = tree.nodes.new(blob)
        node.location = (0.0, -node_lib.DISPLAY_CARD_GAP
                         * (len(tree.nodes) - 1))
        added += 1
    return added


def _new_display_tree():
    """新建一张显示设置图,顺手把设置卡摆成一列。

    照界面设计图"新建时放一个起始控件"的做法 —— 面对一张空图,
    十个有九个不知道第一个该放什么。
    """
    tree = bpy.data.node_groups.new("游戏显示", "GKDisplayTree")
    # 没有人"引用"它的数据块存盘时会被丢掉(和 AI 图同一个坑),必须 fake user
    tree.use_fake_user = True
    _fill_display_cards(tree)
    return tree


class GK_OT_OpenDisplayTree(bpy.types.Operator):
    """打开显示设置图;没有就新建一张(带五张设置卡)。"""

    bl_idname = "gk.open_display_tree"
    bl_label = "打开/新建显示设置图"
    bl_description = ("切到『游戏显示』图(没有就新建一张,"
                      "带物理线框 / 调试面板 / 画面风格 / 视口预览 / 环境氛围"
                      "五张设置卡)")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        trees = [g for g in bpy.data.node_groups
                 if getattr(g, "bl_idname", "") == "GKDisplayTree"]
        tree = trees[0] if trees else None
        if tree is None:
            tree = _new_display_tree()
            self.report({"INFO"}, f"已新建显示设置图『{tree.name}』")

        space = _node_space(context)
        if space is None:
            self.report({"WARNING"},
                        "没找到节点编辑器 —— 请打开一个『节点编辑器』区域")
            return {"CANCELLED"}
        space.tree_type = "GKDisplayTree"
        space.node_tree = tree
        return {"FINISHED"}


class GK_OT_DisplayFillCards(bpy.types.Operator):
    """把这张显示设置图里缺的设置卡补回来(已有的不动)。"""

    bl_idname = "gk.display_fill_cards"
    bl_label = "补齐缺失的设置卡"
    bl_description = "把这张图里还没有的设置卡补上(已有的卡片和位置都不动)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree = props.current_tree(context)
        if tree is None or str(getattr(tree, "bl_idname", "")) != "GKDisplayTree":
            self.report({"WARNING"}, "请先打开一张『游戏显示』设置图")
            return {"CANCELLED"}
        added = _fill_display_cards(tree)
        if not added:
            self.report({"INFO"}, "设置卡都在,没有缺的")
        else:
            self.report({"INFO"}, f"补了 {added} 张设置卡")
        return {"FINISHED"}


class GK_PT_DisplayTree(bpy.types.Panel):
    """『游戏显示』图的侧栏面板:当前设置摘要 + 补卡 + 回游戏逻辑。"""

    bl_idname = "GK_PT_DisplayTree"
    bl_label = "游戏显示 (BlenderGameKit)"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "游戏显示"

    @classmethod
    def poll(cls, context):
        space = getattr(context, "space_data", None)
        return (space is not None
                and getattr(space, "tree_type", "") == "GKDisplayTree")

    def draw(self, context):
        layout = self.layout
        tree = props.current_tree(context)
        settings = getattr(context.scene, "gk_settings", None)
        scene_name = getattr(getattr(context, "scene", None), "name", "?")

        box = layout.box()
        box.label(text=f"当前场景:{scene_name}", icon="SCENE_DATA")
        if tree is None:
            box.label(text="没有显示设置图", icon="INFO")
            return

        # ---- 当前设置摘要(卡上和这里显示的是同一批值)----
        rows = node_lib.display_settings_rows(settings)
        box = layout.box()
        if rows:
            box.label(text="本场景当前设置", icon="CHECKMARK")
            for title, text, on in rows:
                box.label(text=f"{title}: {text}",
                          icon="CHECKMARK" if on else "X")
        else:
            box.label(text="拿不到场景设置", icon="ERROR")

        # ---- 视口叠加预览:几张图开着 ----
        preview_trees = [g for g in bpy.data.node_groups
                         if getattr(g, "bl_idname", "") in ("GKUiDesignTree",
                                                            "GKGameAiTree")]
        if preview_trees:
            on = sum(1 for g in preview_trees
                     if bool(getattr(g, "gk_preview", True)))
            box = layout.box()
            box.label(text=f"视口叠加预览:{on}/{len(preview_trees)} 张开着",
                      icon="VIEWZOOM")
            box.label(text="逐张开关在『显示_视口预览』卡上", icon="INFO")

        # ---- 设置卡补齐 ----
        total = len(node_lib.DISPLAY_CARD_IDS)
        missing = node_lib.missing_display_cards(tree)
        box = layout.box()
        box.label(text=f"设置卡 {total - len(missing)}/{total} 张在图上",
                  icon="NODETREE" if not missing else "ERROR")
        if missing:
            box.operator("gk.display_fill_cards",
                         text=f"补齐 {len(missing)} 张", icon="ADD")
        box.label(text="卡上改 = 导出立刻按新值走,不用点应用", icon="INFO")
        back = box.operator("gk.switch_tree", text="← 回到游戏逻辑",
                            icon="LOOP_BACK")
        back.target = "GKGameLogicTree"


# ==========================================================================
# 第六种节点树:『游戏资产』资产库(第四十三轮)
# ==========================================================================
# 用户原话:"添加游戏资产管理编辑器,便于重复利用与和常规资产隔开";
# 三个形态里点了**新节点树类型**,并选了:收四类(3D 道具 / 图片 /
# 曲线 / 音频)、**跨场景共用**、资产实例自动收进『资产』集合。
#
# 这张图是**资产库**:每份资产一张卡,卡上只存**名字**
# (资产名 / 图片名 / 曲线名 / 音频文件路径)—— 真正的数据还在原地
# (物体属性 / bpy.data.images / 曲线物体 / 本地文件),不搬数据只做索引,
# 所以老工程一个字都不用改。
#
# ── 跨场景共用怎么做的(不改导出流程)──────────────────────────────
# 关卡 2 点『再摆一份』时,把关卡 1 的**本体复制**进关卡 2;目标场景
# 还没有本体时,这份复制品自动当本体(否则导出的场景里实例没有模型)。
# 于是每个场景都有自己的一份"本体 + 实例",导出语义和现在**完全一样**。
class GKAssetTree(bpy.types.NodeTree):
    """游戏资产图:可复用资产(道具 / 图片 / 曲线 / 音频)的资产库。"""

    bl_idname = "GKAssetTree"
    bl_label = "游戏资产"
    bl_icon = "ASSET_MANAGER"
    bl_description = ("BlenderGameKit 的资产库 —— 3D 道具 / 图片 / 曲线 / "
                      "音频 各一张资产卡,卡上写清楚几份、谁在用;"
                      "资产库是整个 .blend 共用的(关卡 1 的树能摆到关卡 2)")

    @classmethod
    def poll(cls, context):
        return True

    @classmethod
    def draw_header(cls, layout, context):
        layout.label(text="资产库跨场景共用:关卡 1 的资产也能摆到关卡 2",
                     icon="INFO")


# 卡片类型 -> 卡上存"名字"的那个属性
_ASSET_CARD_VALUE_PROP = {
    "GKNodeAssetModel": "gk_asset",
    "GKNodeAssetImage": "gk_image",
    "GKNodeAssetCurve": "gk_curve",
    "GKNodeAssetSound": "gk_res",
}


def _asset_card_icon(blob):
    return {"GKNodeAssetModel": "MESH_DATA", "GKNodeAssetImage": "IMAGE_DATA",
            "GKNodeAssetCurve": "CURVE_DATA",
            "GKNodeAssetSound": "SOUND"}.get(blob, "ASSET_MANAGER")


def _ensure_asset_card(tree, blob, value, extras=None):
    """保证图上有这么一张卡(同类型 + 同名的不重复加)。加过就返回它。

    extras 是"顺带写上的其它栏位"(比如声音卡的名字)—— 只在新建时写。
    """
    prop = _ASSET_CARD_VALUE_PROP.get(blob)
    if prop is None:
        return None
    value = str(value or "").strip()
    for node in getattr(tree, "nodes", ()) or ():
        if getattr(node, "bl_idname", "") != blob:
            continue
        if str(getattr(node, prop, "") or "").strip() == value:
            return node
    node = tree.nodes.new(blob)
    try:
        setattr(node, prop, value)
        for key, item in (extras or {}).items():
            setattr(node, key, item)
    except Exception:                                     # noqa: BLE001
        pass
    node.location = (0.0, -node_lib.ASSET_CARD_GAP
                     * max(0, len(tree.nodes) - 1))
    return node


def _asset_trees():
    return [group for group in bpy.data.node_groups
            if getattr(group, "bl_idname", "") == "GKAssetTree"]


def _asset_tree_or_new():
    trees = _asset_trees()
    if trees:
        return trees[0]
    tree = bpy.data.node_groups.new("游戏资产", "GKAssetTree")
    # 没有人"引用"它的数据块存盘时会丢(和 AI 图 / 显示设置图同一个坑)
    tree.use_fake_user = True
    return tree


def _fill_asset_cards(tree):
    """**收录**:把 .blend 里已有的东西登记成资产卡(不重复加)。

    扫三类来源:
      * 所有场景里填了『资产名』的物体 -> 模型卡(跨场景);
      * 所有节点图里在用的图片 -> 图片卡;
      * 在用的曲线 / 音频路径 -> 曲线卡 / 声音卡(声音只登记路径)。
    返回 {"model": n, "image": n, "curve": n, "sound": n}(补了几张)。
    """
    counts = {"model": 0, "image": 0, "curve": 0, "sound": 0}
    for name in node_lib.all_asset_names():
        before = len(tree.nodes)
        _ensure_asset_card(tree, "GKNodeAssetModel", name)
        counts["model"] += len(tree.nodes) - before
    used = node_lib.collect_used_assets(bpy.data.node_groups)
    for image_name in used["images"]:
        before = len(tree.nodes)
        _ensure_asset_card(tree, "GKNodeAssetImage", image_name)
        counts["image"] += len(tree.nodes) - before
    for blob, key, values in (("GKNodeAssetCurve", "curve", used["curves"]),
                              ("GKNodeAssetSound", "sound",
                               used["sounds"])):
        for value in values:
            before = len(tree.nodes)
            extras = {"gk_name": os.path.basename(value)} \
                if blob == "GKNodeAssetSound" else None
            _ensure_asset_card(tree, blob, value, extras)
            counts[key] += len(tree.nodes) - before
    return counts


# ── 『资产』集合(用户选的"场景里收进『资产』集合")────────────────────
# 每个场景**各有一个**同名集合(名字后面带 .001 的是第二个场景的)——
# 不能所有场景共用一个集合:同一个集合链进两个场景时,里面的物体会
# 在两个场景里**都出现**(Blender 的集合是场景之上的结构)。
# 用场景自定义属性记住"这个场景的资产集合叫什么"。
_ASSET_COLLECTION_KEY = "gk_asset_collection"


def asset_collection(scene, create=True):
    """这个场景的『资产』集合(没有就建)。"""
    if scene is None:
        return None
    stored = str(scene.get(_ASSET_COLLECTION_KEY) or "")
    if stored:
        collection = bpy.data.collections.get(stored)
        if collection is not None and stored in scene.collection.children:
            return collection
    # 退路:这个场景下已经有叫『资产』的集合(老文件 / 用户自己建的)
    for child in scene.collection.children:
        if child.name == "资产" or child.name.startswith("资产."):
            scene[_ASSET_COLLECTION_KEY] = child.name
            return child
    if not create:
        return None
    collection = bpy.data.collections.new("资产")
    scene.collection.children.link(collection)
    scene[_ASSET_COLLECTION_KEY] = collection.name
    return collection


def move_into_asset_collection(scene, objects):
    """把资产物体收进『资产』集合,并从**同场景**的其它集合里摘出来。

    只摘"属于这个场景"的集合(别的场景的集合不动)—— 用户要的是
    "大纲视图里和普通物体分开",不是"把它从所有地方摘出去"。
    返回真正挪动的个数。
    """
    collection = asset_collection(scene)
    if collection is None:
        return 0
    moved = 0
    for obj in objects or ():
        try:
            if obj.name not in collection.objects:
                collection.objects.link(obj)
        except Exception:                                 # noqa: BLE001
            continue
        for other in list(getattr(obj, "users_collection", ()) or ()):
            if other is collection:
                continue
            try:
                scenes = getattr(other, "users_scene", None)
                if scenes is not None and scene not in scenes:
                    continue
                other.objects.unlink(obj)
            except Exception:                             # noqa: BLE001
                pass
        moved += 1
    return moved


def _scene_asset_objects(scene):
    """这个场景里填了『资产名』的网格物体(『收进集合』用)。"""
    out = []
    for obj in getattr(scene, "objects", ()) or ():
        if getattr(obj, "type", "") != "MESH":
            continue
        physics = getattr(obj, "gk_physics", None)
        if physics is None or not getattr(physics, "enabled", True):
            continue
        if str(getattr(physics, "asset_name", "") or "").strip():
            out.append(obj)
    return out


def _asset_source(asset_name):
    """跨场景找本体:返回 (本体物体, 场景) —— 优先当前场景。"""
    from . import asset_export
    wanted = str(asset_name or "").strip()
    if not wanted:
        return None, None
    scenes = list(getattr(bpy.data, "scenes", ()) or ())
    current = getattr(bpy.context, "scene", None)
    if current is not None and current in scenes:
        scenes.remove(current)
        scenes.insert(0, current)
    for scene in scenes:
        try:
            entry = asset_export.collect_assets(scene).get(wanted)
        except Exception:                                 # noqa: BLE001
            continue
        if entry and entry.get("source") is not None:
            return entry["source"], scene
    return None, None


class GK_OT_OpenAssetTree(bpy.types.Operator):
    """打开资产图;没有就新建一张(顺手收录现有资产)。"""

    bl_idname = "gk.open_asset_tree"
    bl_label = "打开/新建资产库"
    bl_description = ("切到『游戏资产』图(没有就新建一张,"
                      "顺手把 .blend 里已有的资产收录成卡)")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        trees = _asset_trees()
        tree = trees[0] if trees else None
        if tree is None:
            tree = _asset_tree_or_new()
            counts = _fill_asset_cards(tree)
            total = sum(counts.values())
            self.report({"INFO"}, f"已新建资产库『{tree.name}』"
                                  f"(收录了 {total} 份资产)")

        space = _node_space(context)
        if space is None:
            self.report({"WARNING"},
                        "没找到节点编辑器 —— 请打开一个『节点编辑器』区域")
            return {"CANCELLED"}
        space.tree_type = "GKAssetTree"
        space.node_tree = tree
        return {"FINISHED"}


class GK_OT_AssetFillCards(bpy.types.Operator):
    """收录:把 .blend 里已有的资产登记成卡(不重复加)。"""

    bl_idname = "gk.asset_fill_cards"
    bl_label = "收录已有资产"
    bl_description = ("扫所有场景的静态资产 + 所有节点图在用的图片 / 曲线 / "
                      "音频,缺的卡补上(已有的不动)")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree = _asset_tree_or_new()
        counts = _fill_asset_cards(tree)
        total = sum(counts.values())
        if not total:
            self.report({"INFO"}, "能收的都收过了(或还没有资产)")
        else:
            self.report({"INFO"},
                        f"收录 {total} 张卡:模型 {counts['model']} / "
                        f"图片 {counts['image']} / 曲线 {counts['curve']} / "
                        f"声音 {counts['sound']}")
        return {"FINISHED"}


class GK_OT_AssetRegister(bpy.types.Operator):
    """把当前选中的物体登记为资产(填名 + 收录卡 + 收进『资产』集合)。"""

    bl_idname = "gk.asset_register"
    bl_label = "把选中物体登记为资产"
    bl_description = ("给选中物体填上『资产名』(没填的话用物体名),"
                      "在资产库建一张卡,并收进『资产』集合")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = getattr(context, "active_object", None) \
            or getattr(context, "object", None)
        if obj is None or getattr(obj, "type", "") != "MESH":
            self.report({"WARNING"}, "先在视口里选中一个网格物体")
            return {"CANCELLED"}
        physics = getattr(obj, "gk_physics", None)
        if physics is None:
            self.report({"WARNING"}, "这个物体没有『游戏物理』设置")
            return {"CANCELLED"}
        name = str(getattr(physics, "asset_name", "") or "").strip() or obj.name
        physics.asset_name = name
        tree = _asset_tree_or_new()
        _ensure_asset_card(tree, "GKNodeAssetModel", name)
        moved = move_into_asset_collection(context.scene, [obj])
        self.report({"INFO"},
                    f"已登记资产『{name}』(卡片已建"
                    + (f",已收进『资产』集合" if moved else "") + ")")
        return {"FINISHED"}


class GK_OT_AssetCollect(bpy.types.Operator):
    """把本场景的资产全部收进『资产』集合(批量,给老工程用)。"""

    bl_idname = "gk.asset_collect"
    bl_label = "把本场景资产收进『资产』集合"
    bl_description = ("把本场景所有填了『资产名』的物体挪进『资产』集合,"
                      "和普通物体分开(只动这个场景的集合)")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        objects = _scene_asset_objects(context.scene)
        if not objects:
            self.report({"INFO"}, "本场景还没有资产(先在物体上填『资产名』)")
            return {"CANCELLED"}
        moved = move_into_asset_collection(context.scene, objects)
        self.report({"INFO"}, f"已把 {moved} 个资产物体收进『资产』集合")
        return {"FINISHED"}


def place_asset_into_scene(scene, asset_name):
    """把一份资产摆进 scene(跨场景可用)。

    返回 (成功?, 说明, 新物体或 None) —— 抽成普通函数是为了能**直接测**
    (后台模式里造不出完整的 operator 上下文,而这段逻辑值得一条真测试):
    本体在别的场景时,这份复制品在目标场景里自动当**本体**。
    """
    from . import asset_export
    name = str(asset_name or "").strip()
    if not name:
        return False, "没指定资产", None
    source, source_scene = _asset_source(name)
    if source is None:
        return (False,
                f"资产『{name}』还没有本体 —— 先在某个场景里把一份的"
                f"『作为实例』取消勾选,让它当本体", None)
    try:
        entry = asset_export.collect_assets(scene).get(name)
    except Exception:                                     # noqa: BLE001
        entry = None
    members = list((entry or {}).get("members") or [])
    has_source = bool((entry or {}).get("source"))

    copy = source.copy()                       # 共享网格数据 = 实例
    copy.data = source.data
    copy.name = f"{name}_实例"
    scene.collection.objects.link(copy)
    physics = getattr(copy, "gk_physics", None)
    if physics is not None:
        physics.asset_name = name
        # 这一关没有本体 -> 这份当本体;有本体 -> 它是实例
        physics.asset_instance = has_source
        physics.script_target = False          # 实例不挂脚本
    # 摆在哪:这一关已经摆过 -> 挨着第一份;完全没摆过 -> 站本体原位
    if members:
        anchor = members[0]
        width = max(float(getattr(anchor.dimensions, "x", 1.0) or 1.0), 0.5)
        copy.location = (anchor.location.x + width * 1.2,
                         anchor.location.y, anchor.location.z)
    else:
        copy.location = source.location
    move_into_asset_collection(scene, [copy])
    role = "实例" if physics is not None and physics.asset_instance else "本体"
    origin = "" if source_scene is scene \
        else f"(本体来自『{getattr(source_scene, 'name', '?')}』)"
    return (True,
            f"已摆一份{role}: {copy.name}{origin}(资产『{name}』,"
            f"本场景现在共 {len(members) + 1} 份)", copy)


class GK_OT_AssetPlace(bpy.types.Operator):
    """再摆一份(跨场景):把本体复制进当前场景。

    目标场景还没有本体时,这份复制品自动当**本体**(否则导出时
    这一关的实例没有模型可用)。
    """

    bl_idname = "gk.asset_place"
    bl_label = "再摆一份资产"
    bl_description = ("把这份资产摆到当前场景(本体在别的关卡也能用);"
                      "这一关还没有本体时,摆的第一份自动当本体")
    bl_options = {"REGISTER", "UNDO"}

    asset_name: StringProperty(name="资产", default="")

    def execute(self, context):
        ok, message, copy = place_asset_into_scene(context.scene,
                                                   self.asset_name)
        if not ok:
            self.report({"WARNING"}, message)
            return {"CANCELLED"}
        scene = context.scene
        for other in scene.objects:
            other.select_set(False)
        if copy is not None:
            copy.select_set(True)
            context.view_layer.objects.active = copy
        self.report({"INFO"}, message)
        return {"FINISHED"}


class GK_OT_AssetFocus(bpy.types.Operator):
    """定位资产的本体(本体在别的场景时会切过去)。"""

    bl_idname = "gk.asset_focus"
    bl_label = "定位资产本体"
    bl_description = "切到本体所在的场景并选中它"
    bl_options = {"REGISTER", "UNDO"}

    asset_name: StringProperty(name="资产", default="")

    def execute(self, context):
        source, scene = _asset_source(self.asset_name)
        if source is None:
            self.report({"WARNING"}, f"找不到资产『{self.asset_name}』的本体")
            return {"CANCELLED"}
        window = getattr(context, "window", None)
        if scene is not None and context.scene is not scene:
            if window is None:
                self.report({"WARNING"}, "没有窗口可切场景(用命令行跑的?)")
                return {"CANCELLED"}
            window.scene = scene
        try:
            for other in scene.objects:
                other.select_set(False)
            source.select_set(True)
            scene.view_layers[0].objects.active = source
        except Exception:                                 # noqa: BLE001
            pass
        self.report({"INFO"}, f"已定位到本体: {source.name}(场景『{scene.name}』)")
        return {"FINISHED"}


class GK_OT_AssetFocusNode(bpy.types.Operator):
    """跳到"用着这份资产"的那个节点(卡上『谁在用』旁边的小放大镜)。"""

    bl_idname = "gk.asset_focus_node"
    bl_label = "跳到那个节点"
    bl_description = "切到那张图并选中那个节点"
    bl_options = {"REGISTER", "UNDO"}

    tree_name: StringProperty(name="节点图", default="")
    node_name: StringProperty(name="节点", default="")

    def execute(self, context):
        group = bpy.data.node_groups.get(self.tree_name)
        node = group.nodes.get(self.node_name) if group is not None else None
        space = _node_space(context)
        if group is None or node is None or space is None:
            self.report({"WARNING"}, "找不到那个节点(图或节点被删了?)")
            return {"CANCELLED"}
        try:
            space.tree_type = str(getattr(group, "bl_idname", ""))
            space.pin = True
            space.node_tree = group
            for other in group.nodes:
                other.select = other is node
            group.nodes.active = node
            space.cursor_location = node.location
        except Exception:                                 # noqa: BLE001
            self.report({"WARNING"}, "这个编辑器打不开那张图")
            return {"CANCELLED"}
        return {"FINISHED"}


class GK_PT_AssetTree(bpy.types.Panel):
    """『游戏资产』图的侧栏面板:资产统计 + 收录 / 登记 / 收集合。"""

    bl_idname = "GK_PT_AssetTree"
    bl_label = "游戏资产 (BlenderGameKit)"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "游戏资产"

    @classmethod
    def poll(cls, context):
        space = getattr(context, "space_data", None)
        return (space is not None
                and getattr(space, "tree_type", "") == "GKAssetTree")

    def draw(self, context):
        layout = self.layout
        tree = props.current_tree(context)
        scene_name = getattr(getattr(context, "scene", None), "name", "?")
        box = layout.box()
        box.label(text=f"当前场景:{scene_name}", icon="SCENE_DATA")
        if tree is None:
            box.label(text="没有资产库", icon="INFO")
            return

        # ---- 资产统计(按卡的类型数)----
        counts = {}
        for node in tree.nodes:
            blob = getattr(node, "bl_idname", "")
            if blob in _ASSET_CARD_VALUE_PROP:
                counts[blob] = counts.get(blob, 0) + 1
        box = layout.box()
        box.label(text=f"资产库共 {sum(counts.values())} 份资产",
                  icon="ASSET_MANAGER")
        for blob in node_lib.ASSET_CARD_IDS:
            label = {"GKNodeAssetModel": "3D 道具", "GKNodeAssetImage": "图片",
                     "GKNodeAssetCurve": "曲线",
                     "GKNodeAssetSound": "音频"}.get(blob, blob)
            box.label(text=f"{label}: {counts.get(blob, 0)}", icon=_asset_card_icon(blob))

        # ---- 本场景的资产 / 集合 ----
        local = _scene_asset_objects(context.scene)
        collection = asset_collection(context.scene, create=False)
        in_collection = len(getattr(collection, "objects", ()) or ()) \
            if collection is not None else 0
        box = layout.box()
        box.label(text=f"本场景 {len(local)} 个资产物体"
                       f"(集合里 {in_collection} 个)", icon="MESH_DATA")
        if local and in_collection < len(local):
            box.operator("gk.asset_collect",
                         text="把本场景资产收进『资产』集合", icon="GROUP")
        box.label(text="收进集合 = 大纲视图里和普通物体分开", icon="INFO")

        # ---- 收录 / 登记 ----
        box = layout.box()
        box.operator("gk.asset_fill_cards", text="收录已有资产", icon="ADD")
        box.operator("gk.asset_register", text="把选中物体登记为资产",
                     icon="ASSET_MANAGER")
        box.label(text="收录会扫所有场景(资产库跨场景共用)", icon="INFO")

        back = box.operator("gk.switch_tree", text="← 回到游戏逻辑",
                            icon="LOOP_BACK")
        back.target = "GKGameLogicTree"


# ---- AI 图的视口叠加预览(巡逻路线 + 感知范围圈)----------------------
# 挂在 3D 视口上的绘制句柄(注册时加、注销时摘)
_AI_PREVIEW_HANDLE = []


def _ai_preview_tree():
    """找出正在编辑的 AI 图(没在编辑 / 关了预览就返回 None)。"""
    context = bpy.context
    screen = getattr(context, "screen", None)
    for area in getattr(screen, "areas", ()) or ():
        if getattr(area, "type", "") != "NODE_EDITOR":
            continue
        for space in getattr(area, "spaces", ()) or ():
            if getattr(space, "tree_type", "") != "GKGameAiTree":
                continue
            tree = getattr(space, "edit_tree", None) or getattr(space, "node_tree", None)
            if tree is None or str(getattr(tree, "bl_idname", "")) != "GKGameAiTree":
                continue
            if not getattr(tree, "gk_preview", True):
                continue
            return tree
    return None


def _ai_preview_anchor(scene):
    """感知圈画在谁身上:场景里**挂了脚本**的第一个物体(一般就是敌人自己)。

    感知圈是"AI 看得到多远",必须有个中心 —— 而 AI 的"自己"在运行时
    由『AI_启用』的『作用在谁』决定(玩法图里,这张图上看不到)。
    常见情况是"敌人自己挂脚本",所以拿第一个挂脚本的物体当示意中心。
    """
    try:
        carriers = exporter.script_targets(scene)
    except Exception:                                     # noqa: BLE001
        carriers = []
    return carriers[0] if carriers else None


def _ai_preview_draw():
    """3D 视口上的 AI 预览(POST_PIXEL 绘制回调)。

    ── 和界面预览的区别 ────────────────────────────────────────────
    界面预览画在**屏幕空间**(控件框本来就是 2D 的);AI 预览要画
    **世界空间**的东西(巡逻点在哪、看得到多远),所以先把世界坐标
    用 `location_3d_to_region_2d` 投影到屏幕上再画。投影不到的点
    (在相机背后)整段跳过 —— 硬画会出现"线跑到屏幕另一边"的鬼影。

    纪律和界面预览一样:不许抛异常(整段 try)、GPU 状态用完还原。
    """
    try:
        tree = _ai_preview_tree()
        if tree is None:
            return
        context = bpy.context
        region = getattr(context, "region", None)
        rv3d = getattr(context, "region_data", None)
        scene = getattr(context, "scene", None)
        if region is None or rv3d is None or scene is None:
            return
        if region.width < 80 or region.height < 80:
            return
        import math

        import gpu
        from gpu_extras.batch import batch_for_shader
        from bpy_extras.view3d_utils import location_3d_to_region_2d

        def project(co):
            point = location_3d_to_region_2d(region, rv3d, co)
            if point is None:
                return None
            return (float(point.x), float(point.y))

        graph = node_lib.ai_tree_graph(tree)
        segments = []      # (a, b, color)
        dots = []          # (point, color)

        # ---- 1) 巡逻路线:A ←→ B(每个"巡逻"行为的状态各画一条)----
        for state in graph["states"]:
            if str(getattr(state, "gk_behavior", "") or "") != "PATROL":
                continue
            obj_a = bpy.data.objects.get(str(getattr(state, "gk_patrol_a", "") or ""))
            obj_b = bpy.data.objects.get(str(getattr(state, "gk_patrol_b", "") or ""))
            if obj_a is None or obj_b is None:
                continue
            pa = project(obj_a.matrix_world.translation)
            pb = project(obj_b.matrix_world.translation)
            if pa is not None and pb is not None:
                segments.append((pa, pb, (1.0, 0.85, 0.25, 0.9)))
            if pa is not None:
                dots.append((pa, (1.0, 0.85, 0.25, 1.0)))
            if pb is not None:
                dots.append((pb, (1.0, 0.85, 0.25, 1.0)))

        # ---- 2) 感知范围圈:中心 = 挂脚本的物体,半径 = 转场的『视野距离』----
        anchor = _ai_preview_anchor(scene)
        radii = []
        for edge in graph["edges"]:
            if edge.get("kind") in ("SEE", "LOST"):
                trans = edge.get("transition")
                sight = float(getattr(trans, "gk_sight", 12.0) or 12.0)
                if sight > 0.0:
                    radii.append(sight)
        if anchor is not None and radii:
            center = anchor.matrix_world.translation
            # 2D 项目的世界是 XY 平面,3D 是 XZ 平面 —— 圈要画在地上
            dimension = str(getattr(getattr(scene, "gk_settings", None),
                                    "dimension", "3") or "3")
            radius = radii[0]
            ring = []
            steps = 48
            for index in range(steps + 1):
                angle = math.tau * index / steps
                if dimension == "2":
                    co = (center.x + math.cos(angle) * radius,
                          center.y + math.sin(angle) * radius, center.z)
                else:
                    co = (center.x + math.cos(angle) * radius,
                          center.y,
                          center.z + math.sin(angle) * radius)
                ring.append(project(co))
            for index in range(steps):
                a, b = ring[index], ring[index + 1]
                if a is not None and b is not None:
                    segments.append((a, b, (0.35, 0.85, 1.0, 0.8)))
        if not segments and not dots:
            return

        shader = gpu.shader.from_builtin("UNIFORM_COLOR")
        gpu.state.blend_set("ALPHA")
        try:
            shader.bind()
            for a, b, color in segments:
                batch = batch_for_shader(shader, "LINES", {"pos": [a, b]})
                shader.uniform_float("color", color)
                batch.draw(shader)
            for point, color in dots:
                size = 5.0
                verts = [(point[0] - size, point[1]), (point[0] + size, point[1]),
                         (point[0], point[1] - size), (point[0], point[1] + size)]
                batch = batch_for_shader(shader, "LINES", {"pos": verts})
                shader.uniform_float("color", color)
                batch.draw(shader)
        finally:
            gpu.state.blend_set("NONE")
    except Exception:                                     # noqa: BLE001
        # 绘制回调的异常没有任何去处 —— 静默跳过这一帧
        return


def _remove_ai_preview_handler():
    """摘掉 AI 预览的绘制回调(注销 / 重载时各调一次,重复调没事)。"""
    import bpy as _bpy
    while _AI_PREVIEW_HANDLE:
        handle = _AI_PREVIEW_HANDLE.pop()
        try:
            _bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
        except Exception:                                 # noqa: BLE001
            pass


class GK_PT_AiDesign(bpy.types.Panel):
    """AI 图专属面板:状态清单 + 问题 + 预览开关 + 用法提示。"""

    bl_idname = "GK_PT_AiDesign"
    bl_label = "游戏AI (BlenderGameKit)"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "游戏AI"

    @classmethod
    def poll(cls, context):
        space = getattr(context, "space_data", None)
        return space is not None and getattr(space, "tree_type", "") == "GKGameAiTree"

    def draw(self, context):
        layout = self.layout
        tree = props.current_tree(context)
        if tree is None:
            layout.label(text="没有 AI 图", icon="INFO")
            return

        graph = node_lib.ai_tree_graph(tree)
        states = [s for s in graph["states"] if node_lib.ai_state_name(s)]

        # ---- 概览 ----
        box = layout.box()
        box.label(text=f"『{tree.name}』:{len(states)} 个状态 / "
                       f"{len(graph['edges'])} 条转场", icon="TRACKER")
        if not states:
            box.label(text="还没放状态 —— 先加一个『AI_状态』试试", icon="INFO")
            row = box.row(align=True)
            row.operator("gk.add_node_menu", text="状态",
                         icon="ADD").node_type = "GKNodeAiState"
            row.operator("gk.add_node_menu", text="转场",
                         icon="ADD").node_type = "GKNodeAiTransition"

        # ---- 状态清单(起始状态标出来)----
        if states:
            box = layout.box()
            box.label(text="状态清单(起始 = 没被转场指到的那个)", icon="LINENUMBERS_ON")
            entry = graph["entry"]
            for state in states:
                row = box.row(align=True)
                name = node_lib.ai_state_name(state)
                icon = "PLAY" if state is entry else "FORWARD"
                row.label(text=f"{name} · {node_lib.ai_behavior_label(state)}",
                          icon=icon)
                pick = row.operator("gk.select_node", text="", icon="VIEWZOOM")
                pick.node_name = name

        # ---- 要处理的事(缺目标 / 缺巡逻点 / 转场没条件…)----
        if graph["problems"]:
            box = layout.box()
            box.label(text=f"{len(graph['problems'])} 条要处理的事", icon="ERROR")
            for problem in graph["problems"][:6]:
                box.label(text=node_lib.short_problem(problem, 44), icon="DOT")
            if len(graph["problems"]) > 6:
                box.label(text=f"…还有 {len(graph['problems']) - 6} 条"
                               f"(导出报告里也会说)", icon="INFO")

        # ---- 预览 ----
        box = layout.box()
        box.label(text="预览(叠画在 3D 视口上)", icon="VIEWZOOM")
        row = box.row(align=True)
        row.prop(tree, "gk_preview", text="", toggle=True)
        row.label(text="巡逻路线 + 感知范围"
                  if getattr(tree, "gk_preview", True) else "已关闭")
        box.label(text="感知圈画在挂了脚本的物体上(半径 = 转场的视野距离)",
                  icon="INFO")

        # ---- 用法 ----
        box = layout.box()
        box.label(text="玩法图放『AI_启用』引用本图", icon="INFO")
        row = box.row(align=True)
        back = row.operator("gk.switch_tree", text="← 回到游戏逻辑",
                            icon="LOOP_BACK")
        back.target = "GKGameLogicTree"


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


def _menu_link_from(context):
    """菜单项要自动接线的"上一个节点名"(没选中就空着)。"""
    tree = props.current_tree(context)
    if tree is None:
        return ""
    active = getattr(tree.nodes, "active", None)
    return active.name if active is not None else ""


def _menu_add(menu_layout, cls, link_from):
    op = menu_layout.operator("gk.add_node_menu", text=cls.bl_label)
    op.node_type = cls.bl_idname
    op.link_from = link_from


def _make_category_menu(category, classes, subcategories=None):
    """为一个分类动态生成菜单类。

    subcategories 不为 None 时，这个分类会先列出二级子菜单
    (比如『运算』-> 常量 / 数学 / 向量 / 逻辑 / 转换)。
    """

    def draw(self, context):
        layout = self.layout
        link_from = _menu_link_from(context)
        kind = props.current_tree_type(context)
        if subcategories:
            for sub_name, sub_classes in subcategories:
                # 子菜单类在 CATEGORY_MENUS 里单独注册，这里只要挂上去。
                #
                # ── 但这个子菜单在当前树里**有没有内容**,要先看(踩过的坑)──
                # 以前这里直接挂菜单,过滤留给子菜单自己做 —— 而子菜单
                # **根本没过滤**,于是动画树里点『运算 ▸ 常量 ▸ 常量_数值』
                # 会弹 `RuntimeError:『常量_数值』是游戏逻辑树里的节点`
                # (用户截图里的那条)。空菜单 / 必错的菜单项比没有更糟。
                if any(props.node_fits_tree(cls, kind) for cls in sub_classes):
                    layout.menu(_menu_name(f"{category}/{sub_name}"),
                                text=sub_name)
            return
        # ── 这里再过滤一次(兜底)────────────────────────────────
        # 外层菜单已经按树类型筛过分类了,但 `current_tree_type()`
        # 在某些上下文拿不到时会回退成"列全部分类" ——
        # 那时这个分类里可能全是别的树的节点,点下去只会报错。
        # 在 draw 里(此时一定有 context)按当前树类型再筛一遍。
        shown = 0
        for cls in classes:
            if not props.node_fits_tree(cls, kind):
                continue
            _menu_add(layout, cls, link_from)
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
        link_from = _menu_link_from(context)
        # 按当前树类型过滤 —— 和 _make_category_menu 同一套判断
        # (见 properties.node_fits_tree 的说明:元组树类型曾经在这里漏判)
        kind = props.current_tree_type(context)
        shown = 0
        for cls in classes:
            if not props.node_fits_tree(cls, kind):
                continue
            _menu_add(layout, cls, link_from)
            shown += 1
        if shown == 0:
            layout.label(text="(这里没有适合当前节点图的节点)", icon="INFO")

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
    except (AttributeError, ImportError, KeyError, TypeError):
        # 菜单绘制路径上拿不到节点类清单(模块还没就绪 / 属性缺失),
        # 这时**不画**这张子菜单即可。收窄成这几种是防止真正的 bug
        # 被当成"这张菜单是空的"悄悄吞掉(GPT 审查)。
        gk_log.exception("节点菜单取类清单失败(这张子菜单不画)")
        return False
    for cls in classes:
        if getattr(cls, "gk_hidden", False):
            continue
        if props.node_fits_tree(cls, kind):
            return True
    return False


def _make_common_menu():
    """「常用」子菜单:新手起步最需要的十几个节点。"""

    def draw(self, context):
        layout = self.layout
        link_from = _menu_link_from(context)
        kind = props.current_tree_type(context)
        for cls in props.common_node_classes():
            # 同 _make_category_menu:按当前树类型过滤,
            # 不然动画树里点开会是一串必然报错的玩法节点
            if not props.node_fits_tree(cls, kind):
                continue
            if getattr(cls, "gk_hidden", False):
                continue
            _menu_add(layout, cls, link_from)

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
        link_from = _menu_link_from(context)
        kind = props.current_tree_type(context)

        recent = _recent_classes()
        if not recent:
            layout.label(text="(还没用过任何节点)", icon="INFO")
            return
        shown = 0
        for cls in recent:
            # 和『常用』一样按当前树类型过滤 —— 最近用过玩法节点之后
            # 切到动画树,这个菜单里原来会列一串点了必然报错的节点。
            if not props.node_fits_tree(cls, kind):
                continue
            _menu_add(layout, cls, link_from)
            shown += 1
        if shown == 0:
            layout.label(text="(最近用过的节点都不适合当前节点图)", icon="INFO")

    return type("GK_MT_Recent", (bpy.types.Menu,), {
        "bl_idname": _menu_name("最近使用"),
        "bl_label": "最近使用",
        "draw": draw,
        "__doc__": "最近用过的游戏逻辑节点",
    })


def _make_top_menu():
    """右键菜单顶部的『搜索与常用』子菜单(用户 2026-10-04)。

    右键菜单原来把「搜索 / 常用 / 最近使用 / 自动接续提示」全平铺在最上面,
    四行起步;现在收成一项,菜单明显短了。
    """

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("gk.find_node", text="搜索节点…", icon="VIEWZOOM")
        # 与 _draw_node_menu_contents 一致:当前树没有可用常用节点时就不画
        if _menu_has_entries("常用", context, props.common_node_classes):
            layout.menu(_menu_name("常用"), text="常用", icon="SOLO_ON")
        layout.menu(_menu_name("最近使用"), text="最近使用", icon="FILE_REFRESH")
        link_from = _menu_link_from(context)
        if link_from:
            layout.separator()
            layout.label(text=f"新节点会自动接到: {link_from}", icon="LINKED")

    return type("GK_MT_NodeTop", (bpy.types.Menu,), {
        "bl_idname": _menu_name("搜索与常用"),
        "bl_label": "搜索与常用",
        "draw": draw,
        "__doc__": "搜索 / 常用 / 最近使用 节点",
    })


def _make_tools_menu():
    """右键菜单底部的『节点工具』子菜单(用户 2026-10-04)。"""

    def draw(self, context):
        layout = self.layout
        layout.operator("gk.auto_link", text="把选中的两个节点连起来",
                        icon="LINKED")
        layout.operator("gk.add_comment_box", text="注释框(框住选中节点)",
                        icon="PIVOT_BOUNDBOX")
        layout.operator("gk.collapse_to_function",
                        text="折叠成函数(框住选中节点)", icon="FILE_SCRIPT")

    return type("GK_MT_NodeTools", (bpy.types.Menu,), {
        "bl_idname": _menu_name("节点工具"),
        "bl_label": "节点工具",
        "draw": draw,
        "__doc__": "自动连线 / 注释框 / 折叠成函数",
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
# 场景 -> 逻辑图 绑定(用户 2026-10-04:一个 .blend 多个 Blender 场景,
# 每个场景各出一张 .tscn,得让用户说清"这个场景用哪棵图")
# ==========================================================================
class GK_OT_SetLogicTree(bpy.types.Operator):
    """把"本场景用哪棵游戏逻辑图"写进场景设置。空串 = 自动。"""

    bl_idname = "gk.set_logic_tree"
    bl_label = "设置场景逻辑图"
    bl_options = {"REGISTER", "UNDO"}

    value: StringProperty(default="")

    def execute(self, context):
        settings = getattr(context.scene, "gk_settings", None)
        if settings is None:
            return {"CANCELLED"}
        settings.logic_tree = self.value
        self.report({"INFO"}, f"本场景逻辑图 = {self.value or '自动'}")
        return {"FINISHED"}


class GK_OT_PickLogicTree(bpy.types.Operator):
    """弹出下拉,选本场景用哪棵游戏逻辑图。"""

    bl_idname = "gk.pick_logic_tree"
    bl_label = "选择本场景的逻辑图"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        from . import exporter
        trees = exporter.game_logic_trees()
        settings = getattr(context.scene, "gk_settings", None)
        current = str(getattr(settings, "logic_tree", "") or "")

        def _draw(self, context):
            layout = self.layout
            op = layout.operator(
                "gk.set_logic_tree", text="自动(只有一棵图时就用它)",
                icon="CHECKMARK" if not current else "NODETREE")
            op.value = ""
            layout.separator()
            if not trees:
                layout.label(text="整个 .blend 里没有游戏逻辑图", icon="INFO")
                return
            for tree in trees:
                op = layout.operator(
                    "gk.set_logic_tree", text=tree.name,
                    icon="CHECKMARK" if tree.name == current else "NODETREE")
                op.value = tree.name

        context.window_manager.popup_menu(
            _draw, title="本场景用哪棵游戏逻辑图", icon="NODETREE")
        return {"FINISHED"}


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

        # ---- 灯光(第四十八轮:阶段①灯光扩展)─────────────────────────
        # 灯不参与物理 / 静态资产那几块,选中灯时就只画这一块。
        # 这里只报"会导成什么"和唯一的灯光专属开关(跟随相机 = 手电筒);
        # 颜色 / 亮度 / 阴影直接用 Blender 灯光自己的设置,不另做一套。
        if obj.type == "LIGHT":
            box = layout.box()
            box.label(text="灯光", icon="LIGHT")
            kind = str(getattr(obj.data, "type", ""))
            if kind == "SUN":
                box.label(text="太阳光 → 平行光(方向 = 灯的朝向)", icon="INFO")
            elif kind == "POINT":
                box.label(text="点光 → 灯泡(会随距离变暗)", icon="INFO")
            elif kind == "SPOT":
                try:
                    cone = round(float(obj.data.spot_size) * 57.29578, 1)
                except Exception:                             # noqa: BLE001
                    cone = 0.0
                box.label(text=f"聚光 → 手电筒 / 聚光灯(锥角 {cone}°)", icon="INFO")
            else:
                box.label(text=f"『{kind}』暂不导出:请换太阳 / 点光 / 聚光",
                          icon="ERROR")
            col = box.column(align=True)
            col.prop(physics, "light_follow_camera", toggle=True)
            if physics.light_follow_camera:
                box.label(text="运行时贴着当前相机走(手电筒)", icon="CHECKMARK")
            box.label(text="颜色 / 亮度 / 阴影:用 Blender 灯光自己的设置",
                      icon="INFO")
            if float(getattr(obj.data, "energy", 1.0) or 0.0) <= 0.0:
                box.label(text="能量是 0:这盏灯不会导出(当作关着的灯)",
                          icon="INFO")
            return

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
                col.label(text=f"手动尺寸 {manual} 在『自动』下不生效",
                          icon="INFO")
                col.label(text="想用它请选一个具体形状;清成 0 可去掉这行")
        elif shape == "convex":
            # 凸包(本轮):形状 = 网格真实外形,尺寸不用填
            col.label(text="按网格真实外形生成(凸包),尺寸不用填",
                      icon="CHECKMARK")
            col.label(text="适合石头 / 雕像这类不规则模型;只有 3D 支持",
                      icon="INFO")
        elif shape not in ("none",):
            col.prop(physics, "shape_size")
            # ── 一键贴合模型(用户第 21 轮要的)────────────────────────
            # 手填尺寸这几轮坑最多:填 0.5 出来 1.0、填了却忘了清、
            # 平面填成 0 厚度…… 与其让用户自己量,不如给个按钮直接量。
            row = col.row(align=True)
            row.operator("gk.fit_collision_size", text="贴合模型",
                         icon="FULLSCREEN_ENTER")
            row.operator("gk.clear_collision_size", text="清空(自动)",
                         icon="TRASH")
            manual = tuple(round(float(v), 3) for v in physics.shape_size)
            if any(abs(v) > 1e-6 for v in manual):
                col.label(text=f"用手动尺寸 {manual}", icon="INFO")
            else:
                col.label(text="全 0 = 按模型自动贴合(推荐)", icon="CHECKMARK")

        if shape != "none":
            row = col.row(align=True)
            row.prop(physics, "collision_layer", text="")
            row.prop(physics, "collision_mask", text="")
            col.prop(physics, "is_trigger")

        # ── 子物体碰撞块(本轮:"人形头/身/腿各摆一个")────────────────
        # 勾上 = 这个物体不出现在画面里,形状并入父物体的碰撞体。
        # 放在碰撞区最后:它是"高级用法",不打断常规设置的主线。
        col.separator()
        col.prop(physics, "collision_piece")
        if physics.collision_piece:
            owner = asset_export.collision_owner(obj)
            if owner is None:
                col.label(text="找不到父物体:碰撞块要挂在别的网格下面",
                          icon="ERROR")
            else:
                col.label(text=f"并入父物体『{owner.name}』的碰撞体,"
                               f"本物体不出现在画面里", icon="CHECKMARK")
        else:
            pieces = asset_export.collision_pieces(obj)
            if pieces:
                names = "、".join(piece.name for piece in pieces[:3])
                if len(pieces) > 3:
                    names += "…"
                col.label(text=f"含 {len(pieces)} 个碰撞块:{names}", icon="INFO")

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

        # ---- 静态资产(用户 2026-10-05:"游戏静态资产设计")----------
        # 一个模型只导一次、场景里摆很多份:同名的算同一个资产,
        # 没勾"作为实例"的那一个是**本体**(定义模型),其余拿它的模型。
        box = layout.box()
        box.label(text="静态资产", icon="ASSET_MANAGER")
        col = box.column(align=True)
        col.prop(physics, "asset_name", text="")
        col.prop(physics, "asset_instance")
        name = str(getattr(physics, "asset_name", "") or "").strip()
        if not name:
            col.label(text="留空 = 普通物体(自己导一份模型)", icon="INFO")
        else:
            # ── 『场景里共几份』(用户 2026-10-05)──────────────────────────
            # 实例摆多了很容易忘了总共摆了几份("我到底种了几棵树"),
            # 这里把总数和"这是第几份"直接报出来 —— 一个模型导一次、
            # 场景里摆多份,数得清才好管。
            total, index = _asset_count(context, name, obj)
            if physics.asset_instance:
                col.label(text=f"『{name}』第 {index}/{total} 份(实例)",
                          icon="DUPLICATE")
                col.label(text="复用本体的模型", icon="INFO")
            else:
                col.label(text=f"『{name}』的本体(模型由它定义)", icon="MESH_DATA")
                col.label(text=f"场景里共 {total} 份"
                               f"(含 {max(total - 1, 0)} 份实例)", icon="INFO")
        # 点一个按钮就能在场景里再摆一份(复制本体、勾上"作为实例")
        row = col.row(align=True)
        row.operator("gk.add_asset_instance", text="再加一份实例",
                     icon="DUPLICATE")

        # ---- 粒子特效(需求 5,2026-10-05 第二十一轮)------------------
        # Blender 的粒子系统导不进 glTF,所以这里做的是"标记 + 参数":
        # 导出时在 Godot 里生成 GPUParticles3D / CPUParticles3D,
        # 运行时用『特效_播放』『特效_停止』『特效_爆发』控制。
        fx = getattr(obj, "gk_fx", None)
        if fx is not None:
            box = layout.box()
            box.label(text="粒子特效", icon="MOD_PARTICLES")
            col = box.column(align=True)
            col.prop(fx, "enabled")
            if not fx.enabled:
                col.label(text="勾上 = 导出成 Godot 的粒子节点"
                               "(Blender 自己的粒子导不出去)", icon="INFO")
            else:
                col.prop(fx, "particle_type", text="")
                row = col.row(align=True)
                row.prop(fx, "amount")
                row.prop(fx, "lifetime")
                row = col.row(align=True)
                row.prop(fx, "one_shot")
                row.prop(fx, "auto_play")
                col.prop(fx, "explosiveness")
                if not fx.auto_play:
                    col.label(text="不自动播放:要靠『特效_播放』/"
                                   "『特效_爆发』启动", icon="INFO")
                col.separator()
                col.prop(fx, "direction")
                row = col.row(align=True)
                row.prop(fx, "speed")
                row.prop(fx, "spread")
                col.prop(fx, "gravity")
                row = col.row(align=True)
                row.prop(fx, "radius")
                row.prop(fx, "randomness")
                col.separator()
                col.prop(fx, "start_color")
                col.prop(fx, "end_color")
                row = col.row(align=True)
                row.prop(fx, "start_size")
                row.prop(fx, "end_size")
                col.prop(fx, "preprocess")
                row = col.row(align=True)
                row.prop(fx, "local_coords")
                row.prop(fx, "additive")
                col.separator()
                col.prop(fx, "hide_mesh")
                if fx.hide_mesh:
                    col.label(text="本体网格不导出:这个物体只当发射器标记",
                              icon="CHECKMARK")
                # 贴图来源(和导出用的是**同一个函数**,不会说法不一致)
                if asset_export.fx_texture_file(obj) \
                        == asset_export.FX_DEFAULT_TEXTURE:
                    col.label(text="贴图:内置圆形柔光(本物体没有材质贴图)",
                              icon="IMAGE_DATA")
                else:
                    col.label(text="贴图:用本物体材质的贴图", icon="IMAGE_DATA")
                if obj.type != "MESH":
                    col.label(text="空物体也能当发射器:只标位置和朝向",
                              icon="INFO")

        # ---- 『视口预览』盒第四十二轮搬走了 ----
        # 碰撞线框的"显示这一个 / 显示全部 / 隐藏"原来在这里,和侧栏导出盒里的
        # 『预览碰撞体』是两处入口 —— 用户嫌"不好找"。现在统一收进
        # 『游戏显示』设置图的『显示_碰撞线框』卡上(第四十二轮)。


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
    "cylinder": "圆柱",
    "convex": "凸包",
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


def _anim_state_label(state):
    """一个『动画_状态』显示什么名字(取不到就直说没设)。"""
    return node_lib.anim_state_name(state) or "(没设状态名)"


def _draw_anim_tree_graph(box, tree, states, transitions):
    """把动画树的**状态图**画出来 —— 和导出用的是同一份解析。

    ── 为什么要画全图(用户 2026-10-03 的坑)────────────────────────
    他的图:状态的『转到』直接连到下一个状态的『进入』,转场的『上游』
    空着。老面板只列"转场: (上游没接) → (没接下一个状态)",
    **完全没提**那条直连 —— 而导出的状态机恰恰是靠它才有的。
    面板和导出各说各话,是最难查的一类问题。

    所以这里画三样东西,和 `nodes.anim_tree_graph` 一一对应:
      * 状态清单(起始那个标出来)
      * **每一条边**(包括直连、下拉选的),以及它的条件
      * 图上的**问题**(导出时会变成告警的那些)
    """
    graph = node_lib.anim_tree_graph(tree)
    box.label(text=f"{tree.name} —— {len(states)} 状态 / "
                   f"{len(graph['edges'])} 条转场", icon="NODETREE")
    entry = graph["entry"]

    # ── 开始口 / 结束口(第三轮)────────────────────────────────────
    # 有这两个口时,这张图是"有始有终"的:先说清楚"从哪开始、什么条件开始",
    # 再说清楚"到哪算完"。面板上把它摆在状态清单**前面** ——
    # 用户看图第一眼要看到的就是这两件事。
    has_start = graph.get("start") is not None
    has_exit = graph.get("exit") is not None
    if has_start or has_exit:
        box.label(text="开始 / 结束", icon="DOT")
        if has_start:
            start = graph["start"]
            mode = str(getattr(start, "gk_start_mode", "NOW"))
            label = {"NOW": "立刻开始", "PIN": "自己写(接条件引脚)",
                     "VAR": "变量为真", "ACTION_JUST": "刚按下某键",
                     "ACTION_PRESSED": "按住某键",
                     "FINISH": "上一个动画播完"}.get(mode, mode)
            box.label(text=f"开始: {label}", icon="PLAY")
        else:
            box.label(text="开始: 没放『动画_开始』(按老规矩猜第一个状态)",
                      icon="INFO")
        box.label(text=f"结束: {'有结束口' if has_exit else '没有(不会收工)'}",
                  icon="QUIT" if has_exit else "INFO")

    if states:
        box.label(text="状态", icon="DOT")
        for state in graph["states"]:
            row = box.row(align=True)
            # 一律用 node_key 比 —— `is` 在真实 Blender 里不可靠
            # (同一个节点可以有多个 Python 包装对象,见 nodes.node_key)
            is_entry = (entry is not None
                        and node_lib.node_key(state) == node_lib.node_key(entry))
            row.label(text=f"{'起始 ' if is_entry else '     '}"
                           f"{_anim_state_label(state)}",
                      icon="PLAY" if is_entry else "FORWARD")
    else:
        box.label(text="只有转场、没有状态,导出时不生效", icon="ERROR")

    if graph["edges"]:
        box.label(text="转场(按判定顺序:上面的先判)", icon="DOT")
        for edge in graph["edges"]:
            row = box.row(align=True)
            # 走到结束口的边没有目标状态 —— 以前这里会直接取名字取到 None,
            # 面板上会显示成空的(或者更糟)。现在明确写成『结束』。
            target_name = (node_lib.anim_state_name(edge["to"])
                           if edge.get("to") is not None else "结束")
            row.label(text=f"{node_lib.anim_state_name(edge['from'])} → "
                           f"{target_name}",
                      icon="TRACKING_FORWARDS_SINGLE")
            row.label(text=node_lib.anim_edge_label(edge))
            if edge.get("transition") is None:
                # 直连:条件不是用户写的,而是"播完" —— 画出来免得他找条件
                row.label(text="直连", icon="INFO")
    elif states:
        box.label(text="还没有任何转场 —— 状态之间不会切换", icon="ERROR")

    if entry is None:
        box.label(text="找不到起始状态,导出时会报错", icon="ERROR")
    for problem in graph["problems"][:4]:
        # 侧栏比节点体宽,能多放几个字 —— 完整的话在导出报告里
        box.label(text=node_lib.short_problem(problem, limit=44), icon="ERROR")
    if len(graph["problems"]) > 4:
        box.label(text=f"…还有 {len(graph['problems']) - 4} 条(导出报告里全有)",
                  icon="INFO")


def _in_source(node, socket_name):
    """这个输入引脚是从哪个节点接过来的(没接返回 None)。

    转发到 `nodes.link_sources` —— 判断只留一份,免得面板和导出两套。
    """
    sources = node_lib.link_sources(node, socket_name)
    return sources[0] if sources else None


def _out_target(node, socket_name):
    """这个输出引脚连到了哪个节点(没接返回 None)。"""
    targets = node_lib.link_targets(node, socket_name)
    return targets[0] if targets else None


def _draw_anim_category_box(layout, context):
    """画"动作分类"盒子:给文件里的动作打分类标签。

    ── 为什么要一块独立的面板 ──────────────────────────────────────
    动作一多(MMD 模型动辄几十个),选动作时列表里全是名字,分不清哪个是
    "待机"哪个是"攻击"。在这里打个分类之后:
      * 节点上『选…』弹出的列表会按分类聚在一起、分类摆在最前
      * 导出时按分类生成一张表(见 codegen 的 `GK_ANIM_BY_CATEGORY`),
        Godot 侧可以按分类随机/顺序挑动作

    分类存进动作的自定义属性(见 nodes.ANIM_CATEGORY_KEY),
    随 .blend 保存,禁用插件也不丢。

    两处面板(游戏逻辑 / 游戏动画)共用这一个函数,免得写两遍。
    """
    actions = sorted(getattr(bpy.data, "actions", None) or (),
                     key=lambda a: str(a.name))
    box = layout.box()
    box.label(text=f"动作分类  ({len(actions)})", icon="ARMATURE_DATA")

    if not actions:
        box.label(text="这个文件里还没有动作", icon="INFO")
        box.label(text="导入带动作的模型后,这里会列出它的动作", icon="DOT")
        return

    # 场景里物体**正在用**的动作排前面 —— 那些才是真的会用到的,
    # 剩下的是文件里躺着没挂的动作。
    scene_names = []
    for obj in getattr(context.scene, "objects", ()) or ():
        for action in node_lib.actions_on_object(obj):
            if action.name not in scene_names:
                scene_names.append(action.name)

    order = {name: i for i, name in enumerate(node_lib.ANIM_CATEGORY_NAMES)}
    actions.sort(key=lambda a: (0 if a.name in scene_names else 1,
                                order.get(node_lib.action_category(a),
                                          len(order)),
                                str(a.name)))

    max_rows = 24
    for action in actions[:max_rows]:
        category = node_lib.action_category(action)
        row = box.row(align=True)
        row.label(text=f"{category or '未分类'} · {action.name}",
                  icon="CHECKMARK" if category else "DOT")
        picker = row.operator_menu_enum("gk.tag_anim_category", "category",
                                        text="", icon="COLLAPSEMENU")
        picker.action_name = action.name
        if category:
            clear = row.operator("gk.tag_anim_category", text="", icon="X")
            clear.action_name = action.name
            clear.clear = True
    if len(actions) > max_rows:
        box.label(text=f"还有 {len(actions) - max_rows} 个动作没列出"
                       f"(可在动作编辑器里查看)", icon="INFO")


# ==========================================================================
# 侧栏『变量』 —— 照虚幻蓝图 My Blueprint 那一栏
# ==========================================================================
# 用户要求:"在侧边栏可以添加变量，自定义变量引脚连线"。
#
# 存储:GKAnimationTree.gk_variables(见 properties.GKAnimVariable)。
# 这里只负责"画 + 三个小动作":加一个变量 / 删一个变量 /
# 把变量拉进图里(生成『动画_取变量』或『动画_设变量』节点 ——
# 这就是蓝图里"从 My Blueprint 拖一个变量进图"的那一步)。
def _anim_tree_of(context):
    """当前正在编辑的动画树(编辑的是玩法图就返回 None)。"""
    if props.current_tree_type(context) != "GKAnimationTree":
        return None
    return props.current_tree(context)


class GK_OT_AnimVarAdd(bpy.types.Operator):
    """在当前动画树里加一个自定义变量。"""

    bl_idname = "gk.anim_var_add"
    bl_label = "添加变量"
    bl_description = "在当前动画树里加一个自定义变量"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree = _anim_tree_of(context)
        if tree is None:
            self.report({"WARNING"}, "请先打开一张动画树")
            return {"CANCELLED"}
        used = {str(var.var_name) for var in tree.gk_variables}
        name, index = "新变量", 1
        while name in used:
            index += 1
            name = f"新变量{index}"
        var = tree.gk_variables.add()
        var.var_name = name
        tree.gk_var_index = len(tree.gk_variables) - 1
        self.report({"INFO"},
                    f"加了变量『{name}』—— 点它那行的『取』/『设』拉进图里")
        return {"FINISHED"}


class GK_OT_AnimVarRemove(bpy.types.Operator):
    """删掉一个自定义变量。"""

    bl_idname = "gk.anim_var_remove"
    bl_label = "删除变量"
    bl_description = "删掉这个变量(已经拉进图的取/设节点会变成『没选变量』)"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(name="第几个", default=0)

    def execute(self, context):
        tree = _anim_tree_of(context)
        if tree is None or not (0 <= self.index < len(tree.gk_variables)):
            return {"CANCELLED"}
        name = str(tree.gk_variables[self.index].var_name)
        tree.gk_variables.remove(self.index)
        tree.gk_var_index = max(0, min(int(tree.gk_var_index),
                                       len(tree.gk_variables) - 1))
        self.report({"INFO"}, f"删了变量『{name}』")
        return {"FINISHED"}


class GK_OT_AnimVarMakeNode(bpy.types.Operator):
    """把变量拉进图里 —— 生成『动画_取变量』或『动画_设变量』节点。"""

    bl_idname = "gk.anim_var_make_node"
    bl_label = "把变量拉进图"
    bl_description = "生成一个读/写这个变量的节点(放在节点编辑器的光标位置)"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(name="第几个", default=0)
    make_set: bpy.props.BoolProperty(name="是设置", default=False)

    def execute(self, context):
        tree = _anim_tree_of(context)
        if tree is None or not (0 <= self.index < len(tree.gk_variables)):
            return {"CANCELLED"}
        name = str(tree.gk_variables[self.index].var_name)
        space = getattr(context, "space_data", None)
        location = getattr(space, "cursor_location", None) or (0.0, 0.0)
        node = tree.nodes.new("GKNodeAnimSetVar" if self.make_set
                              else "GKNodeAnimGetVar")
        node.location = location
        node.gk_var_name = name
        tree.gk_var_index = self.index
        self.report({"INFO"}, f"{'设' if self.make_set else '取'}变量『{name}』")
        return {"FINISHED"}


class GK_OT_AnimVarPick(bpy.types.Operator):
    """在取/设变量节点上挑一个变量(和『选动画』同一套搜索弹窗)。"""

    bl_idname = "gk.anim_var_pick"
    bl_label = "选择变量"
    bl_description = "从这张动画树的变量里挑一个"
    bl_options = {"REGISTER", "UNDO"}

    node_name: StringProperty(name="节点", default="")
    choice: StringProperty(name="变量名", default="")

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
        if node is None or not hasattr(node, "gk_var_name"):
            self.report({"WARNING"}, "没找到目标节点,变量名没写进去")
            return {"CANCELLED"}
        node.gk_var_name = self.choice
        self.report({"INFO"}, f"变量 -> {self.choice}")
        return {"FINISHED"}

    def draw(self, context):
        tree = props.current_tree(context)
        for var in (getattr(tree, "gk_variables", ()) or ()):
            op = self.layout.operator("gk.anim_var_pick", text=str(var.var_name))
            op.node_name = self.node_name
            op.choice = str(var.var_name)


def _draw_anim_variable_box(layout, context):
    """侧栏『变量』盒子:加/删变量,并把变量拉进图里当引脚连。"""
    tree = _anim_tree_of(context)
    if tree is None:
        return

    box = layout.box()
    box.label(text=f"变量 ({len(tree.gk_variables)})", icon="PROPERTIES")
    if not len(tree.gk_variables):
        box.label(text="还没有变量。加一个,再点『取』/『设』拉进图", icon="INFO")

    for index, var in enumerate(tree.gk_variables):
        row = box.row(align=True)
        row.prop(var, "var_name", text="")
        row.prop(var, "var_type", text="")
        get = row.operator("gk.anim_var_make_node", text="取")
        get.index = index
        get.make_set = False
        put = row.operator("gk.anim_var_make_node", text="设")
        put.index = index
        put.make_set = True
        remove = row.operator("gk.anim_var_remove", text="", icon="X")
        remove.index = index
        detail = box.row(align=True)
        detail.prop(var, "default_value", text="初值")
        detail.prop(var, "comment", text="", icon="INFO")

    if tree.gk_variables:
        box.label(text="布尔变量可以直接接『动画_转场』的条件", icon="INFO")
    box.operator("gk.anim_var_add", text="添加变量", icon="ADD")



def _draw_logic_port_box(layout, context):
    """侧栏『对外端口』盒子:声明这张逻辑图的输出/输入。

    用户第 21 轮的原话:"一般来说,一个游戏逻辑最后应该输出布尔变量,
    以便与其他的游戏逻辑联动"。声明过的端口 = 脚本变量 `输出_<名字>`,
    别的图用『变量_读取』读同名端口;导出时校验有没有打错。
    """
    tree = props.current_tree(context)
    if tree is None or getattr(tree, "bl_idname", "") != "GKGameLogicTree":
        return
    ports = getattr(tree, "gk_ports", None)
    if ports is None:
        return

    box, opened = collapsible_box(layout, "logic_ports",
                                  f"对外端口 ({len(ports)})", "PLUGIN")
    if not opened:
        return
    if not len(ports):
        box.label(text="声明过的端口:别的图能读到、导出时会校验", icon="INFO")

    for index, port in enumerate(ports):
        row = box.row(align=True)
        row.prop(port, "port_name", text="")
        row.prop(port, "port_type", text="")
        row.prop(port, "direction", text="")
        remove = row.operator("gk.port_remove", text="", icon="X")
        remove.index = index
        detail = box.row(align=True)
        detail.prop(port, "default_value", text="初值")
        detail.prop(port, "comment", text="", icon="INFO")
        use = box.row(align=True)
        op = use.operator("gk.port_make_node", text="放一个『输出结果』节点")
        op.index = index

    box.operator("gk.port_add", text="添加端口", icon="ADD")
    if len(ports):
        box.label(text="别的图:『变量_读取』填 输出_<端口名>", icon="INFO")


def _logic_tree_of(context):
    """当前正在编辑的玩法图(编辑的是动画图就返回 None)。"""
    if props.current_tree_type(context) != "GKGameLogicTree":
        return None
    return props.current_tree(context)


class GK_OT_LogicVarAdd(bpy.types.Operator):
    """在当前逻辑图里声明一个变量。"""

    bl_idname = "gk.logic_var_add"
    bl_label = "声明一个变量"
    bl_description = "在当前逻辑图里声明一个变量(名字 / 类型 / 初值)"
    bl_options = {"REGISTER", "UNDO"}

    # 从**哪个分组**的『＋』按的(第三十二轮):新变量直接落进那个组
    group: StringProperty(name="分组", default="")

    def execute(self, context):
        tree = _logic_tree_of(context)
        if tree is None:
            self.report({"WARNING"}, "请先打开一张游戏逻辑图")
            return {"CANCELLED"}
        used = {str(var.var_name) for var in tree.gk_vars}
        name, index = "新变量", 1
        while name in used:
            index += 1
            name = f"新变量{index}"
        var = tree.gk_vars.add()
        var.var_name = name
        var.group = str(self.group or "")
        tree.gk_var_index = len(tree.gk_vars) - 1
        where = f"(分组『{var.group}』)" if var.group else ""
        self.report({"INFO"}, f"声明了变量『{name}』{where} —— 节点上点放大镜挑它")
        return {"FINISHED"}


class GK_OT_LogicVarRemove(bpy.types.Operator):
    """删掉一个声明的变量。"""

    bl_idname = "gk.logic_var_remove"
    bl_label = "删除变量"
    bl_description = "删掉这个声明(图里已经引用它的节点会退回隐式声明)"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(name="第几个", default=0)
    # 按名字删(第三十二轮):面板现在按分组重排过,列表下标不再等于
    # `gk_vars` 里的下标 —— 传名字更稳,顺带也修掉"删错一个"的隐患
    var_name: StringProperty(name="变量名", default="")

    def execute(self, context):
        tree = _logic_tree_of(context)
        if tree is None:
            return {"CANCELLED"}
        index = int(self.index)
        if self.var_name:
            for slot, var in enumerate(tree.gk_vars):
                if str(var.var_name) == self.var_name:
                    index = slot
                    break
        if not (0 <= index < len(tree.gk_vars)):
            return {"CANCELLED"}
        name = str(tree.gk_vars[index].var_name)
        tree.gk_vars.remove(index)
        tree.gk_var_index = max(0, min(int(tree.gk_var_index),
                                       len(tree.gk_vars) - 1))
        self.report({"INFO"}, f"删了变量声明『{name}』")
        return {"FINISHED"}


class GK_OT_PickVar(bpy.types.Operator):
    """在取/设变量节点上挑一个**已声明**的变量(和『选动画』同一套搜索弹窗)。"""

    bl_idname = "gk.pick_var"
    bl_label = "选择变量"
    bl_description = "从这张图声明的变量里挑一个填进节点"
    bl_options = {"REGISTER", "UNDO"}

    node_name: StringProperty(name="节点", default="")
    slot: IntProperty(name="第几个变量槽", default=1)
    choice: StringProperty(name="变量名", default="")

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
        if node is None:
            self.report({"WARNING"}, "没找到目标节点,变量名没写进去")
            return {"CANCELLED"}
        attr = "gk_var_name" if self.slot <= 1 else f"gk_var_name_{self.slot}"
        if not hasattr(node, attr):
            self.report({"WARNING"}, "这个节点没有对应的变量槽,没写进去")
            return {"CANCELLED"}
        setattr(node, attr, self.choice)
        self.report({"INFO"}, f"变量 -> {self.choice}")
        return {"FINISHED"}

    def draw(self, context):
        tree = props.current_tree(context)
        if tree is None:
            return
        # 按分组显示(第三十二轮):组名做小标题,变量排在它下面
        for group_name, vars_in_group in props.logic_var_groups(tree):
            self.layout.label(text=group_name or "未分组", icon="FILE_FOLDER")
            for var in vars_in_group:
                op = self.layout.operator("gk.pick_var",
                                          text="    " + str(var.var_name))
                op.node_name = self.node_name
                op.slot = self.slot
                op.choice = str(var.var_name)


def _draw_declared_var_rows(layout, tree, index, var):
    """一个**已声明变量**的全部行(名字 / 取设 / 初值 / 说明 / 监视 / 分组 / 实现)。

    抽成函数是为了**按分组**排(第三十二轮):每组一个可折叠盒子,盒子里
    还是这些行,一个控件都没变。『删除』按**名字**传 —— 面板重排过之后,
    列表下标不再等于 `gk_vars` 里的下标。
    """
    row = layout.row(align=True)
    row.prop(var, "var_name", text="")
    row.prop(var, "var_type", text="")
    get = row.operator("gk.var_make_node", text="取")
    get.var_name = str(var.var_name)
    get.make_set = False
    put = row.operator("gk.var_make_node", text="设")
    put.var_name = str(var.var_name)
    put.make_set = True
    remove = row.operator("gk.logic_var_remove", text="", icon="X")
    remove.var_name = str(var.var_name)
    detail = layout.row(align=True)
    detail.prop(var, "default_value", text="初值")
    detail.prop(var, "comment", text="", icon="INFO")
    # 监视开关(第二十二轮):勾上的变量进**游戏内调试面板**。
    # 一个都不勾 = 面板显示脚本里全部变量(见 GKLogicVariable.watch)。
    detail.prop(var, "watch", text="", toggle=True,
                icon="HIDE_OFF" if getattr(var, "watch", True)
                else "HIDE_ON")
    # ── 分组(第三十二轮)────────────────────────────────────────────
    # 填同一个名字就归到一组,留空 = 未分组(排在最后)。只影响显示。
    detail.prop(var, "group", text="", icon="FILE_FOLDER")
    # ── 变量的"实现"(用户 2026-10-05:照 UE5 的 getter/setter)────────
    # 读 / 写这个变量时改调的函数。绑了之后图里照样用『变量_读取』
    # 『变量_设置』,生成出来却是函数调用 —— "读的时候要算一下"
    # 就不用每个读取点各连一遍。
    impl = layout.row(align=True)
    getter = str(getattr(var, "getter", "") or "")
    setter = str(getattr(var, "setter", "") or "")
    pick = impl.operator("gk.var_bind_impl", text="读→" + (getter or "变量"),
                         icon="TRIA_RIGHT")
    pick.slot = index
    pick.which = "getter"
    pick = impl.operator("gk.var_bind_impl", text="写→" + (setter or "变量"),
                         icon="TRIA_LEFT")
    pick.slot = index
    pick.which = "setter"
    declared_funcs = props.tree_functions(tree)
    for label, bound in (("读取时调用", getter), ("写入时调用", setter)):
        if bound and bound not in declared_funcs:
            layout.label(text=f"⚠ {label}的『{bound}』没有声明过 —— "
                              f"导出时会改回直接用变量", icon="ERROR")
        elif bound and label == "写入时调用":
            params = declared_funcs.get(bound, {}).get("params") or []
            if len(params) != 1:
                layout.label(text=f"⚠ setter『{bound}』要正好 1 个参数"
                                  f"(现在 {len(params)} 个)", icon="ERROR")


def _draw_variable_box(layout, context):
    """侧栏『变量』盒子 —— 声明 + 集中视图(用户 2026-10-04)。

    上半是**声明区**:一处定好变量的名字 / 类型 / 初值,节点上从下拉里挑,
    导出时按这里的类型生成成员变量(打在脚本顶部的成员变量清单里)。
    下半是**引用区**:把整张图扫一遍,列出"谁在读、谁在写" ——
    没声明过的名字会标『未声明』,点放大镜能选中所有引用它的节点。
    """
    tree = _logic_tree_of(context)
    if tree is None:
        return
    declared = getattr(tree, "gk_vars", None) or ()
    declared_names = {str(var.var_name) for var in declared}
    usages = props.variable_references(tree)

    box, opened = collapsible_box(
        layout, "variables",
        f"变量 ({len(declared)} 声明 · {len(usages)} 引用)", "SORTALPHA")
    if not opened:
        return

    # ---- 声明区(第三十二轮:按『分组』折叠显示,像文件夹)────────────
    if not declared:
        box.label(text="声明一个变量,节点上就能从下拉里挑名字", icon="INFO")
    # 删 / 绑实现要的下标:按名字查(面板按分组重排过,不能拿遍历顺序当下标)
    index_of = {}
    for slot, var in enumerate(declared):
        index_of.setdefault(str(var.var_name), slot)
    for group_name, vars_in_group in props.logic_var_groups(tree):
        label = (f"{group_name} ({len(vars_in_group)})" if group_name
                 else f"未分组 ({len(vars_in_group)})")

        def _add_here(header, group_name=group_name):
            """组标题右边的『＋』:新变量直接落进这一组。"""
            op = header.operator("gk.logic_var_add", text="", icon="ADD")
            op.group = group_name

        sub, opened = collapsible_box(box, f"vargroup:{group_name}", label,
                                      "FILE_FOLDER", header_extra=_add_here)
        if not opened:
            continue
        for var in vars_in_group:
            _draw_declared_var_rows(sub, tree,
                                    index_of.get(str(var.var_name), 0), var)
    add = box.operator("gk.logic_var_add", text="声明一个变量", icon="ADD")
    add.group = ""            # 不选组直接加 = 未分组
    if declared:
        box.label(text="『分组』那一栏填同一个名字就归到一组(只影响显示)",
                  icon="FILE_FOLDER")
        box.label(text="『读→/写→』= 读写这个变量时改调的函数(UE5 的 getter/setter)",
                  icon="INFO")

    # ---- 引用区:图里谁读谁写;没声明的名字标出来(跟着同一套分组显示)----
    box.separator()
    if not usages:
        box.label(text="图里还没有节点用到变量", icon="INFO")
    else:
        # 分组排布(含『对外端口』那组)在 properties.reference_groups 里,
        # 这里只负责画 —— 面板按分组重排过之后,这里的顺序也不能自己另搞一套。
        for group_name, rows_in_group in props.reference_groups(tree):
            label = (f"{group_name} ({len(rows_in_group)})" if group_name
                     else f"未分组 ({len(rows_in_group)})")
            # 『对外端口』那组:端口就是脚本变量(输出_<名字>),用不同的图标,
            # 也不标"未声明" —— 图里引用端口名是正常接法(第三十三轮)。
            is_ports = group_name == props.PORT_REF_GROUP
            sub, opened = collapsible_box(box, f"varrefs:{group_name}", label,
                                          "PLUGIN" if is_ports else "SORTALPHA")
            if not opened:
                continue
            for name, sets, gets in rows_in_group:
                row = sub.row(align=True)
                if name in declared_names:
                    row.label(text=name, icon="SORTALPHA")
                elif is_ports:
                    row.label(text=name, icon="PLUGIN")
                else:
                    row.label(text=f"{name} · 未声明", icon="ERROR")
                row.label(text=f"设 {len(sets)} · 读 {len(gets)}")
                find = row.operator("gk.var_find_refs", text="", icon="VIEWZOOM")
                find.var_name = name
                get = row.operator("gk.var_make_node", text="取",
                                   icon="CHECKBOX_DEHLT")
                get.var_name = name
                get.make_set = False
                put = row.operator("gk.var_make_node", text="设",
                                   icon="CHECKBOX_HLT")
                put.var_name = name
                put.make_set = True
        box.label(text="点放大镜选中所有引用该变量的节点", icon="INFO")


class GK_OT_VarBindImpl(bpy.types.Operator):
    """给变量绑一个"实现"函数(用户 2026-10-05:照 UE5 的 getter/setter)。

    图里照样用『变量_读取』『变量_设置』,生成出来却是函数调用 ——
    这样"读的时候要算一下 / 写的时候顺带做别的事"只写一处。
    """

    bl_idname = "gk.var_bind_impl"
    bl_label = "绑定实现函数"
    bl_description = ("读 / 写这个变量时改调的函数(留空 = 直接用变量本身)。"
                      "从侧栏『函数』框里声明的函数里挑")
    bl_property = "choice"

    slot: IntProperty(name="第几个变量", default=0)
    which: StringProperty(name="getter 还是 setter", default="getter")
    choice: EnumProperty(name="函数", items=lambda self, context: [
        ("", "(不绑定 —— 直接用变量)", "取消绑定", "X", 0)] + [
        (name, name, spec.get("comment") or "这个函数", "FILE_SCRIPT", 0)
        for name, spec in sorted(
            props.tree_functions(props.current_tree(context) or object())
            .items())])

    def invoke(self, context, _event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        tree = _logic_tree_of(context)
        if tree is None or not (0 <= self.slot < len(tree.gk_vars)):
            return {"CANCELLED"}
        var = tree.gk_vars[self.slot]
        attr = "setter" if self.which == "setter" else "getter"
        setattr(var, attr, self.choice or "")
        label = "写入时调用" if attr == "setter" else "读取时调用"
        if self.choice:
            self.report({"INFO"}, f"『{var.var_name}』的{label} -> {self.choice}")
        else:
            self.report({"INFO"}, f"『{var.var_name}』取消绑定(直接用变量)")
        return {"FINISHED"}


class GK_OT_FuncAdd(bpy.types.Operator):
    """在当前逻辑图里声明一个函数(用户 2026-10-05 第十七轮)。"""

    bl_idname = "gk.func_add"
    bl_label = "声明一个函数"
    bl_description = "在当前逻辑图里声明一个函数(名字 / 返回 / 参数)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree = _logic_tree_of(context)
        if tree is None:
            self.report({"WARNING"}, "请先打开一张游戏逻辑图")
            return {"CANCELLED"}
        used = {str(item.func_name) for item in tree.gk_funcs}
        name, index = "新函数", 1
        while name in used:
            index += 1
            name = f"新函数{index}"
        func = tree.gk_funcs.add()
        func.func_name = name
        tree.gk_func_index = len(tree.gk_funcs) - 1
        self.report({"INFO"}, f"声明了函数『{name}』—— 用『函数_定义』写它、"
                              f"『函数_调用』用它")
        return {"FINISHED"}


class GK_OT_FuncRemove(bpy.types.Operator):
    """删掉一个声明的函数。"""

    bl_idname = "gk.func_remove"
    bl_label = "删除函数"
    bl_description = ("删掉这个函数声明。⚠ 图里引用它的『函数_定义』"
                      "『函数_调用』节点会变成没声明的状态(导出时告警)")
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(name="第几个", default=0)

    def execute(self, context):
        tree = _logic_tree_of(context)
        if tree is None or not (0 <= self.index < len(tree.gk_funcs)):
            return {"CANCELLED"}
        name = str(tree.gk_funcs[self.index].func_name)
        tree.gk_funcs.remove(self.index)
        tree.gk_func_index = max(0, min(int(tree.gk_func_index),
                                        len(tree.gk_funcs) - 1))
        self.report({"INFO"}, f"删了函数声明『{name}』")
        return {"FINISHED"}


class GK_OT_MakeFunctionNode(bpy.types.Operator):
    """一键拖出『函数_定义』或『函数_调用』节点(省得去菜单里翻)。"""

    bl_idname = "gk.func_make_node"
    bl_label = "函数节点"
    bl_description = "为这个函数造一个定义 / 调用节点"
    bl_options = {"REGISTER", "UNDO"}

    func_name: StringProperty(name="函数名", default="")
    make_call: BoolProperty(name="造调用节点", default=False)

    def execute(self, context):
        tree = _logic_tree_of(context)
        if tree is None or not self.func_name:
            return {"CANCELLED"}
        if self.func_name not in props.tree_functions(tree):
            self.report({"WARNING"}, f"没有声明过函数『{self.func_name}』")
            return {"CANCELLED"}
        node = tree.nodes.new("GKNodeFunctionCall" if self.make_call
                              else "GKNodeFunctionDefine")
        node.gk_func_name = self.func_name
        node.name = (f"调用_{self.func_name}" if self.make_call
                     else f"定义_{self.func_name}")
        # 摆在视口中间偏一点,免得和别的节点叠在一起看不见
        node.location = (0.0, 200.0 if self.make_call else 0.0)
        builder = getattr(node, "build_sockets", None)
        if callable(builder):
            builder()
        self.report({"INFO"}, f"造了{'调用' if self.make_call else '定义'}节点:"
                              f"{node.name}")
        return {"FINISHED"}


class GK_OT_PickFunction(bpy.types.Operator):
    """在函数节点上挑一个**已声明**的函数(和『选变量』同一套搜索弹窗)。"""

    bl_idname = "gk.pick_function"
    bl_label = "选择函数"
    bl_description = "从这张图声明的函数里挑一个填进节点"
    bl_property = "func_name"

    node_name: StringProperty(name="节点", default="")
    func_prop: StringProperty(name="属性名", default="gk_func_name")
    func_name: EnumProperty(name="函数", items=lambda self, context: [
        (name, f"{name}({len(spec['params'])} 参数)"
         + (f" -> {spec['return']}" if spec["return"] else ""),
         spec.get("comment") or "这个函数", "FILE_SCRIPT", 0)
        for name, spec in sorted(
            props.tree_functions(props.current_tree(context) or object())
            .items())] or [("", "(还没有函数)", "先在侧栏『函数』框声明", "ERROR", 0)])

    def invoke(self, context, _event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        if not self.func_name:
            return {"CANCELLED"}
        tree = props.current_tree(context)
        node = tree.nodes.get(self.node_name) if tree is not None else None
        if node is None or not hasattr(node, self.func_prop):
            self.report({"WARNING"}, "没找到目标节点(或它没有函数栏位)")
            return {"CANCELLED"}
        setattr(node, self.func_prop, self.func_name)
        # 换了函数 -> 参数引脚要跟着重长(不然还是上一个函数的参数框)
        builder = getattr(node, "build_sockets", None)
        if callable(builder):
            builder()
        self.report({"INFO"}, f"函数 -> {self.func_name}")
        return {"FINISHED"}


def _draw_function_box(layout, context):
    """侧栏『函数』盒子(用户 2026-10-05 第十七轮:"查看函数,自定义函数")。

    上半是**声明区**:一处写好名字 / 返回 / 参数,节点上从下拉里挑;
    每个函数旁边两个按钮直接造『函数_定义』『函数_调用』节点。
    下半是**清单**:图里哪些函数真的有定义 / 有被调用 ——
    "声明了但从来没定义"是最常见的忘接线,这里一眼能看出来。
    """
    tree = _logic_tree_of(context)
    if tree is None:
        return
    declared = getattr(tree, "gk_funcs", None) or ()
    specs = props.tree_functions(tree)
    # 扫图:哪些函数有『函数_定义』、哪些被调用过
    defined, called = set(), set()
    for node in getattr(tree, "nodes", ()) or ():
        name = str(getattr(node, "gk_func_name", "") or "").strip()
        idname = getattr(node, "bl_idname", "")
        if not name:
            continue
        if idname == "GKNodeFunctionDefine":
            defined.add(name)
        elif idname == "GKNodeFunctionCall":
            called.add(name)

    box, opened = collapsible_box(
        layout, "functions",
        f"函数 ({len(declared)} 声明 · {len(defined)} 有定义)", "FILE_SCRIPT")
    if not opened:
        return

    if not declared:
        box.label(text="声明一个函数,就能一处定义、多处调用", icon="INFO")
    for index, func in enumerate(declared):
        row = box.row(align=True)
        row.prop(func, "func_name", text="")
        row.prop(func, "return_type", text="")
        remove = row.operator("gk.func_remove", text="", icon="X")
        remove.index = index
        detail = box.row(align=True)
        detail.prop(func, "params", text="参数")
        row = box.row(align=True)
        op = row.operator("gk.func_make_node", text="造定义", icon="FILE_SCRIPT")
        op.func_name = str(func.func_name)
        op.make_call = False
        op = row.operator("gk.func_make_node", text="造调用", icon="PLAY")
        op.func_name = str(func.func_name)
        op.make_call = True
        box.prop(func, "comment", text="", icon="INFO")
        name = str(func.func_name)
        if name in specs:
            spec = specs[name]
            note = [text for text in (
                "有定义" if name in defined else "⚠ 没有『函数_定义』节点",
                f"被调用 {1 if name in called else 0} 次") if text]
            box.label(text=" · ".join(note), icon="INFO")
            if spec.get("bad_types"):
                box.label(text="⚠ 参数类型不认识(已按浮点): "
                               + "、".join(spec["bad_types"]), icon="ERROR")
    box.operator("gk.func_add", text="声明一个函数", icon="ADD")
    if declared:
        box.label(text="函数体 = 从『函数_定义』的执行口接出去的那串节点",
                  icon="INFO")
    # 『折叠 + 复用』的入口 —— 选中一串节点,一键收进函数(照蓝图
    # 的 Collapse to Function)。放在这里是因为"折叠"这事的结果就是
    # 多出一个函数,在这个框里最容易找到。
    collapse = box.row(align=True)
    collapse.operator("gk.collapse_to_function",
                      text="折叠选中的节点成函数", icon="FILE_SCRIPT")


class GK_OT_CollapseToFunction(bpy.types.Operator):
    """把选中的一串节点**折叠成一个函数**(用户 2026-10-05 第十七轮)。

    用户原话:"可查看函数,自定义函数,折叠函数等"(照虚幻蓝图 Collapse to
    Function)。

    ── 干什么 ──────────────────────────────────────────────────────
    选中一段执行流 → 一键做完三件事:
      ① 侧栏『函数』框里**声明**一个新函数(名字自动取,可改);
      ② 把选中的节点搬进函数体(前面接一个『函数_定义』);
      ③ 在原来的位置**留一个『函数_调用』**,原来的上游/下游照旧接上。

    所以折叠前后**行为完全一样**,只是那串节点被收进函数里、可以复用。

    ── 不能自动折叠的情况(明确报出来,而不是折叠坏)────────────────
      * 选中里有**多个入口**(两条上游都指进来)—— 函数只能有一个入口;
      * 有**数据线穿过边界**(函数里的值给外面用 / 外面的值给里面用)——
        那需要参数和返回值,得手动摆;
      * 出口超过一个(比如选中了分支节点的『是』『否』两边)。
    """

    bl_idname = "gk.collapse_to_function"
    bl_label = "折叠成函数"
    bl_description = ("把选中的节点收进一个新函数,原位置留一个调用节点。"
                      "⚠ 需要:一个入口、一个出口,且数据线不跨边界")
    bl_options = {"REGISTER", "UNDO"}

    func_name: StringProperty(name="函数名", default="")

    # ---- 小工具 ----
    @staticmethod
    def _exec_in(node):
        """节点上那个叫『执行』的输入引脚(没有就 None)。"""
        getter = getattr(getattr(node, "inputs", None), "get", None)
        return getter("执行") if callable(getter) else None

    @staticmethod
    def _exec_out(node):
        getter = getattr(getattr(node, "outputs", None), "get", None)
        if not callable(getter):
            return None
        for name in ("完成", "执行"):
            socket = getter(name)
            if socket is not None:
                return socket
        return None

    def _flow_links(self, tree, selected):
        """把连线分成三类:(从外进里的, 从里出去的, 跨边界的数据线)。"""
        incoming, outgoing, crossing = [], [], []
        for link in tree.links:
            from_node = getattr(link, "from_node", None)
            to_node = getattr(link, "to_node", None)
            inside_from = from_node in selected
            inside_to = to_node in selected
            if inside_from and inside_to:
                continue
            if inside_from and not inside_to:
                socket = getattr(link, "from_socket", None)
                # 执行流出口 vs 数据出口:靠引脚种类分(Flow 是 GK 自己的种类)
                if _is_flow_socket(socket):
                    outgoing.append(link)
                else:
                    crossing.append(link)
            elif inside_to and not inside_from:
                socket = getattr(link, "to_socket", None)
                if _is_flow_socket(socket):
                    incoming.append(link)
                else:
                    crossing.append(link)
        return incoming, outgoing, crossing

    @staticmethod
    def _kind_of(socket):
        """这根引脚跑的是什么类型 -> 声明里用的短名(bool/int/float/string/vector)。

        认不出来就给 float —— 和『参数』那一栏"类型不认识就按浮点"一个规矩。
        """
        idname = str(getattr(socket, "bl_idname", "")
                     or getattr(socket, "type", "") or "")
        return {
            "NodeSocketBool": "bool",
            "NodeSocketInt": "int",
            "NodeSocketFloat": "float",
            "NodeSocketString": "string",
            "NodeSocketVector": "vector",
            "NodeSocketVector2D": "vector",
        }.get(idname, "float")

    def execute(self, context):
        # ── 用 ops._tree_of 而不是 props.current_tree ────────────────
        # 无头 / 搜索弹窗那条路上 context 里没有 space_data,
        # 而"折叠"必须拿到**选中节点所在的**那棵树(_tree_of 有退路)。
        tree = ops._tree_of(self, context) or props.current_tree(context)
        if tree is None:
            return {"CANCELLED"}
        selected = [node for node in tree.nodes
                    if getattr(node, "select", False)
                    and not getattr(node, "gk_is_comment", False)]
        # 跨边界的数据线分析要用到"选中集合"和"当前树",挂成实例属性
        # (几个小工具函数都在用,逐个传参太啰嗦)
        self._selected = selected
        self._tree = tree
        if len(selected) < 1:
            self.report({"WARNING"}, "先选中要折叠的那串节点(至少一个)")
            return {"CANCELLED"}
        # 『函数_定义』『函数_调用』本身不能再被折叠
        for node in selected:
            if getattr(node, "bl_idname", "") in ("GKNodeFunctionDefine",
                                                  "GKNodeFunctionCall"):
                self.report({"WARNING"}, "选中的节点里有函数节点,不能再折叠它")
                return {"CANCELLED"}

        incoming, outgoing, crossing = self._flow_links(tree, selected)
        if len(incoming) > 1:
            self.report({"WARNING"}, "选中的节点有**多个入口** —— 函数只能有一个"
                                     "入口,请少选一段,分两次折叠")
            return {"CANCELLED"}
        if not incoming:
            self.report({"WARNING"}, "选中的这段**没有上游**(没人驱动它)—— "
                                     "函数得从别处被调用,请把它接在一条执行流后面")
            return {"CANCELLED"}
        if len(outgoing) > 1:
            self.report({"WARNING"}, "选中的这段有**多个出口**(比如选了分支的"
                                     "两边)—— 请只选一条路径")
            return {"CANCELLED"}
        # ── 出口必须是"往下一句走"的口(『完成』『执行』)─────────────────
        # 如果出口是分支的『是』/『否』,折叠后它会被挪到**调用之后**、
        # 变成无条件执行 —— 行为就变了(实测想清楚才发现,不是报错而是
        # "静默改语义",这种最坑)。
        if outgoing:
            exit_socket = getattr(outgoing[0], "from_socket", None)
            exit_name = str(getattr(exit_socket, "name", "") or "")
            if exit_name not in ("完成", "执行"):
                self.report(
                    {"WARNING"},
                    f"选中这段的出口是『{exit_name}』(分支/循环的出口)——"
                    f"折叠后它会变成**无条件执行**,行为会变。请只选一条"
                    f"直着往下的路径(出口是『完成』)")
                return {"CANCELLED"}
        if crossing:
            # 跨边界的数据线现在**自动**变成参数 / 返回值(见 _wire_boundary_data):
            # 外面给里面用 -> 参数;里面给外面用 -> 返回值(只能一个)。
            # 先算出"要几个参数、返回值什么类型",写进声明 —— 必须在建
            # 『函数_调用』节点**之前**算完:那个节点的参数引脚是按声明长出来的,
            # 建完之后再改声明就得重建引脚,而重建会把接好的线全冲掉。
            boundary = self._plan_boundary_data(crossing)
            if boundary["error"]:
                self.report({"WARNING"}, boundary["error"])
                return {"CANCELLED"}
        else:
            boundary = {"params": [], "return": None, "error": ""}

        # ---- ① 声明函数 ----
        used = {str(item.func_name) for item in tree.gk_funcs}
        raw = self.func_name.strip()
        if not raw:
            index = 1
            raw = "折叠函数"
            while raw in used:
                index += 1
                raw = f"折叠函数{index}"
        elif raw in used:
            self.report({"WARNING"}, f"已经有一个叫『{raw}』的函数了,换个名字")
            return {"CANCELLED"}
        func = tree.gk_funcs.add()
        func.func_name = raw
        func.comment = "由『折叠成函数』从选中的 %d 个节点生成" % len(selected)
        if boundary["params"]:
            func.params = ", ".join(f"{name}:{kind}"
                                    for name, kind, _src, _targets
                                    in boundary["params"])
        if boundary["return"]:
            func.return_type = boundary["return"]["kind"]

        # ---- ② 摆『函数_定义』,把它接在入口前面 ----
        xs = [node.location[0] for node in selected]
        ys = [node.location[1] for node in selected]
        define = tree.nodes.new("GKNodeFunctionDefine")
        define.gk_func_name = raw
        define.name = f"定义_{raw}"
        define.location = (min(xs) - 260.0, max(ys))
        builder = getattr(define, "build_sockets", None)
        if callable(builder):
            builder()
        entry_link = incoming[0]
        entry_socket = getattr(entry_link, "to_socket", None)
        from_socket = getattr(entry_link, "from_socket", None)
        # 先把旧线拆掉:上游 -> 入口 改成 上游 -> 调用
        tree.links.remove(entry_link)
        # 定义节点 -> 入口
        define_out = self._exec_out(define)
        if define_out is not None and entry_socket is not None:
            tree.links.new(define_out, entry_socket)

        # ---- ③ 在原来位置留一个『函数_调用』 ----
        # ⚠ 这一步之后**不能再重建引脚**(重建会把刚接的线冲掉),所以
        #   参数的声明必须在上面就写好 —— 参数框在这一步按声明长出来。
        call = tree.nodes.new("GKNodeFunctionCall")
        call.gk_func_name = raw
        call.name = f"调用_{raw}"
        call.location = (min(xs) - 260.0,
                         min(ys) - 120.0)
        builder = getattr(call, "build_sockets", None)
        if callable(builder):
            builder()
        call_in = self._exec_in(call)
        if call_in is not None and from_socket is not None:
            tree.links.new(from_socket, call_in)
        if outgoing:
            exit_link = outgoing[0]
            # ⚠ 必须**先读**引脚再删线:删掉之后 link 的 StructRNA 就失效了,
            # 再去 getattr(link, "to_socket") 会报
            #     ReferenceError: StructRNA of type NodeLink has been removed
            # (实测踩过)
            to_socket = getattr(exit_link, "to_socket", None)
            tree.links.remove(exit_link)
            call_out = self._exec_out(call)
            if call_out is not None and to_socket is not None:
                tree.links.new(call_out, to_socket)

        # ---- ④ 跨边界的数据线 -> 接成参数 / 返回值(用户 2026-10-05)----
        made = self._wire_boundary_data(tree, boundary, define, call, selected)

        for node in tree.nodes:
            if hasattr(node, "select"):
                node.select = False
        call.select = True
        extra = ""
        if made["params"]:
            extra += ",参数 " + "、".join(made["params"])
        if made["return_type"]:
            extra += f",返回 {made['return_type']}"
        self.report({"INFO"}, f"已折叠成函数『{raw}』({len(selected)} 个节点"
                              f"{extra});函数体 = 『定义_{raw}』后面那串")
        return {"FINISHED"}

    def _plan_boundary_data(self, crossing):
        """先算清楚"要几个参数、返回值什么类型"(不动图)。

        返回 {"params": [(名字, 类型, 外部源引脚, [(内部目标引脚, 线)])...],
              "return": {"kind", "socket", "links"} 或 None, "error": 文本}。
        """
        result = {"params": [], "return": None, "error": ""}
        selected = self._selected
        data_in, data_out = {}, {}
        for link in crossing:
            from_node = getattr(link, "from_node", None)
            to_node = getattr(link, "to_node", None)
            from_socket = getattr(link, "from_socket", None)
            to_socket = getattr(link, "to_socket", None)
            inside_from = from_node in selected
            inside_to = to_node in selected
            if inside_to and not inside_from:            # 外面的值 -> 里面用
                key = _socket_key(from_socket)
                entry = data_in.setdefault(key, {"socket": from_socket,
                                                 "node": from_node,
                                                 "targets": []})
                entry["targets"].append((to_socket, link))
            elif inside_from and not inside_to:          # 里面的值 -> 外面用
                key = _socket_key(from_socket)
                data_out.setdefault(key, {"socket": from_socket,
                                          "links": []})["links"].append(link)

        # ── 返回值只能有一个 ─────────────────────────────────────────
        # 多个不同来源要往外送,折叠后就得写多个返回 —— 那得打包成字典 /
        # 列表才行。这里明确拒绝,比"悄悄只返回一个"安全得多。
        if len(data_out) > 1:
            result["error"] = ("有**多个**值要往外送(折叠需要一个返回值)"
                               "—— 请用一个字典 / 列表把它们装起来再往外接,"
                               "或者分两次折叠")
            return result
        if data_out:
            entry = list(data_out.values())[0]
            entry["kind"] = self._kind_of(entry["socket"])
            result["return"] = entry

        # ── 参数:按外部源引脚下标去重 ───────────────────────────────
        # 同一个外部值喂给选中里两个节点时,只生成**一个**参数 ——
        # 生成两个的话调用点会多出两个框,用户还得各自接一遍。
        taken = {str(getattr(v, "var_name", "")) for v in self._tree_vars()}
        for index, entry in enumerate(data_in.values(), start=1):
            base = snake_case(str(getattr(entry["node"], "name", "") or ""), "")
            name = base or f"输入{index}"
            if name in taken:
                suffix = 2
                while f"{name}_{suffix}" in taken:
                    suffix += 1
                name = f"{name}_{suffix}"
            taken.add(name)
            result["params"].append((name, self._kind_of(entry["socket"]),
                                     entry["socket"], entry["targets"]))
        return result

    def _tree_vars(self):
        """当前树里已声明的变量(给参数起名时避重名用)。"""
        return list(getattr(self._tree, "gk_vars", ()) or ())

    def _wire_boundary_data(self, tree, boundary, define, call, selected):
        """按计划把跨边界的数据线接成参数与返回值(只接线,不再改声明)。

        ── 两条通路 ────────────────────────────────────────────────────
        外面 -> 里面(参数):
            外面那个输出 ──► 调用节点的参数框
            函数体里插一个『变量_读取(参数名)』──► 原来那个输入
        里面 -> 外面(返回值):
            里面的输出 ──► 定义节点的『返回』引脚
            调用节点的『结果』──► 原来接在外面的那几个输入
        """
        made = {"params": [], "return_type": ""}
        for name, kind, source, targets in boundary["params"]:
            for target_socket, link in targets:
                tree.links.remove(link)
                reader = tree.nodes.new("GKNodeGetVar")
                reader.name = f"读取参数_{name}"
                reader.gk_var_name = name
                reader.location = (min(n.location[0] for n in selected) - 120.0,
                                   max(n.location[1] for n in selected) + 60.0)
                builder = getattr(reader, "build_sockets", None)
                if callable(builder):
                    builder()
                value_out = reader.outputs.get("值")
                if value_out is not None and target_socket is not None:
                    tree.links.new(value_out, target_socket)
            call_pin = call.inputs.get(name)
            if call_pin is not None and source is not None:
                tree.links.new(source, call_pin)
            made["params"].append(f"{name}({kind})")

        entry = boundary.get("return")
        if entry:
            made["return_type"] = entry["kind"]
            return_pin = define.inputs.get("返回")
            if return_pin is not None and entry["socket"] is not None:
                tree.links.new(entry["socket"], return_pin)
            result_out = call.outputs.get("结果")
            for link in entry["links"]:
                to_socket = getattr(link, "to_socket", None)
                tree.links.remove(link)
                if result_out is not None and to_socket is not None:
                    tree.links.new(result_out, to_socket)
        return made


def _is_flow_socket(socket):
    """这根引脚是不是执行流(靠 GK 的种类判断,不靠名字)。"""
    if socket is None:
        return False
    try:
        from . import gk_sockets as S
        return S.is_flow(socket)
    except Exception:                                     # noqa: BLE001
        return False


def _socket_key(socket):
    """给引脚一个可比较的身份。

    同一个引脚每次取到的是**不同的 Python 包装对象**(Blender 的行为),
    用 `id()` 会把"同一根引脚"当成两根 —— 于是同一个外部值会被算成
    两个参数。`as_pointer()` 才是稳定身份(和 gk_sockets._socket_key
    同一个坑、同一个解法)。
    """
    if socket is None:
        return None
    pointer = getattr(socket, "as_pointer", None)
    if callable(pointer):
        try:
            return ("p", pointer())
        except Exception:                                 # noqa: BLE001
            pass
    name = getattr(socket, "name", None)
    owner = getattr(socket, "node", None)
    return ("n", getattr(owner, "name", ""), name)


class GK_OT_PortAdd(bpy.types.Operator):
    """给当前逻辑图加一个对外端口。"""

    bl_idname = "gk.port_add"
    bl_label = "添加端口"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        tree = props.current_tree(context)
        if tree is None or not hasattr(tree, "gk_ports"):
            return {"CANCELLED"}
        port = tree.gk_ports.add()
        port.port_name = f"端口{len(tree.gk_ports)}"
        tree.gk_port_index = len(tree.gk_ports) - 1
        return {"FINISHED"}


class GK_OT_PortRemove(bpy.types.Operator):
    """删掉一个端口。"""

    bl_idname = "gk.port_remove"
    bl_label = "删除端口"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(name="序号", default=0)

    def execute(self, context):
        tree = props.current_tree(context)
        if tree is None or not hasattr(tree, "gk_ports"):
            return {"CANCELLED"}
        if 0 <= self.index < len(tree.gk_ports):
            tree.gk_ports.remove(self.index)
        return {"FINISHED"}


class GK_OT_PortMakeNode(bpy.types.Operator):
    """放一个已经填好端口名的『逻辑_输出结果』节点(省得手打名字)。"""

    bl_idname = "gk.port_make_node"
    bl_label = "放到图里"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(name="序号", default=0)

    def execute(self, context):
        tree = props.current_tree(context)
        if tree is None or not hasattr(tree, "gk_ports"):
            return {"CANCELLED"}
        if not (0 <= self.index < len(tree.gk_ports)):
            return {"CANCELLED"}
        port = tree.gk_ports[self.index]
        node = tree.nodes.new("GKNodeLogicOutput")
        node.location = (context.space_data.cursor_location
                         if context.space_data else (0.0, 0.0))
        node.gk_output_name = port.port_name
        node.gk_value = (str(port.default_value).strip().lower()
                         in ("true", "1", "yes", "真"))
        return {"FINISHED"}

def _draw_tree_list_box(layout, context):
    """把所有节点图列出来:一行一张,可跳转、可切换"参与导出"。"""
    box, opened = collapsible_box(layout, "tree_list", "节点图", "NODETREE")
    if not opened:
        return
    groups = [g for g in bpy.data.node_groups
              if getattr(g, "bl_idname", "") in ("GKGameLogicTree",
                                                 "GKAnimationTree",
                                                 "GKUiDesignTree",
                                                 "GKGameAiTree",
                                                 "GKDisplayTree",
                                                 "GKAssetTree")]
    if not groups:
        box.label(text="还没有节点图", icon="INFO")
        return
    disabled = 0
    for group in groups:
        row = box.row(align=True)
        blob = getattr(group, "bl_idname", "")
        enabled = bool(getattr(group, "gk_enabled", True))
        if not enabled:
            disabled += 1
        icon = ("NODETREE" if blob == "GKGameLogicTree"
                else "ARMATURE_DATA" if blob == "GKAnimationTree"
                else "TRACKER" if blob == "GKGameAiTree"
                else "HIDE_OFF" if blob == "GKDisplayTree"
                else "ASSET_MANAGER" if blob == "GKAssetTree"
                else "WINDOW")
        # 勾选框:关掉 = 这张图不参与导出
        if blob in ("GKUiDesignTree", "GKGameAiTree", "GKDisplayTree",
                    "GKAssetTree"):
            # ── 设计图 / AI 图 / 显示设置图 / 资产库没有"参与导出"这一说
            # (第四十 / 四十一 / 四十二 / 四十三轮)──────────────────────
            # 前两张由玩法图按需引用(『界面_套用设计』『AI_启用』),
            # 后两张更是"索引 / 控制台" —— 摆一个勾选框不仅含义含糊,
            # `prop()` 还会去读它们**没有**的属性(画面板时炸)。
            row.label(text=" ")
        else:
            row.prop(group, "gk_enabled", text="")
        op = row.operator("gk.focus_tree", text=group.name, icon=icon)
        op.tree_name = group.name
        kind = {"GKGameLogicTree": "逻辑", "GKAnimationTree": "动画",
                "GKUiDesignTree": "界面", "GKGameAiTree": "AI",
                "GKDisplayTree": "显示", "GKAssetTree": "资产"}.get(blob, "图")
        row.label(text=f"{kind} · {len(group.nodes)} 节点")
    if disabled:
        box.label(text=f"有 {disabled} 张图已停用(不会导出)", icon="INFO")
    box.label(text="大纲视图切到 Blend File 也能看到这些图", icon="INFO")


def _draw_preset_box(layout, context):
    """预设:一键生成第三人称角色(用户 2026-10-04 第六条的中间那块)。

    按钮点开是个小对话框(参数都在里面),生成的是**两张普通节点图** ——
    之后想怎么改都行,不是黑盒。实现见 presets.py。
    """
    box, opened = collapsible_box(layout, "preset_box", "预设", "AUTO")
    if not opened:
        return
    box.label(text="一套能跑的第三人称角色:", icon="INFO")
    box.label(text="输入 · 移动 · 跳跃 · 相机 · 动画")
    box.operator("gk.character_preset", text="生成第三人称角色(预设)",
                 icon="PLAY")
    box.label(text="相机物体自己摆,名字对上就行", icon="CAMERA_DATA")


def _draw_asset_box(layout, context):
    """场景资产(用户 2026-10-05):一个模型导一次、场景里摆多份。

    ── 用户要解决的两件事(原话)────────────────────────────────────
      * "不可能最后场景一大堆模型在那" -> 资产列表 + 实例化引用;
      * "有时要定位到一个模型,拉过去的话,视图上会有拉不动,显示到头了等"
        -> 点一下**定位到它**(选中 + 视图聚焦),顺便检查**视图裁剪**
           (clip_end 太小的话大场景会被整片裁掉,看起来就是"拉不动")。
    """
    box, opened = collapsible_box(layout, "scene_assets", "场景资产",
                                 "ASSET_MANAGER")
    if not opened:
        return
    try:
        assets = asset_export.collect_assets(context.scene)
    except Exception:                                     # noqa: BLE001
        assets = {}
    if not assets:
        box.label(text="还没有资产(留空 = 普通物体)", icon="INFO")
        box.label(text="选中模型 -> 『游戏物理』-> 填『资产名』")
        box.label(text="复制一份、勾『作为实例』= 再摆一份", icon="DUPLICATE")
        _draw_view_clip_row(box, context)
        return

    total_instances = 0
    total_members = 0
    for name in sorted(assets):
        entry = assets[name]
        members = entry["members"]
        source = entry["source"]
        total_instances += max(0, len(members) - 1)
        total_members += len(members)
        row = box.row(align=True)
        icon = "ERROR" if source is None else "MESH_DATA"
        row.label(text=f"{name}({len(members)} 份)", icon=icon)
        # 定位:选中 + 视图聚焦(用户:"定位不到模型")
        op = row.operator("gk.focus_object", text="", icon="VIEWZOOM")
        op.object_name = (source or members[0]).name
        # 再加一份实例
        add = row.operator("gk.add_asset_instance", text="", icon="DUPLICATE")
        add.source_name = source.name if source is not None else ""
        if source is None:
            box.label(text=f"⚠ 『{name}』没有本体,实例会没有模型", icon="ERROR")
        elif entry["extra_sources"]:
            box.label(text=f"⚠ 『{name}』有多个本体(只用了 {source.name})",
                      icon="ERROR")
    # ── 场景里的总数统计(用户 2026-10-05:"场景里标出实例总数统计")────
    # 一行看清"这个场景一共摆了多少份资产/实例",不用一个个资产去加。
    box.label(text=f"{len(assets)} 个资产 · {total_instances} 份实例 · "
                   f"共 {total_members} 份物体", icon="CHECKMARK")
    box.label(text="一个模型导一次,实例各自带碰撞", icon="INFO")
    _draw_view_clip_row(box, context)


def _asset_count(context, name, obj=None):
    """这个资产在场景里一共摆了几份,以及**当前这个物体**是第几份。

    用户 2026-10-05:"给实例添加显示多少在场景中这样的显示信息" ——
    实例摆多了很容易忘了总数(到底种了几棵树),面板上直接报
    `第 2/3 份` / `场景里共 3 份`,数得清才好管。
    """
    members = []
    try:
        entry = asset_export.collect_assets(context.scene).get(name)
        members = list(entry.get("members") or []) if entry else []
    except Exception:                                     # noqa: BLE001
        members = []
    total = len(members) or 1
    for position, member in enumerate(members, start=1):
        if member == obj:
            return total, position
    return total, 1


def _draw_view_clip_row(box, context):
    """视图裁剪检查(用户:"拉过去视图上拉不动、显示到头了")。

    `frame_selected` 之后什么都看不见,最常见的原因**不是**模型丢了,
    而是 3D 视图的『裁剪终点(clip_end)』比场景尺寸小 —— 远景被裁掉,
    看起来像"拉不动 / 显示到头了"。这里直接把当前值报出来。
    """
    space = getattr(context, "space_data", None)
    clip = getattr(space, "clip_end", None)
    if clip is None or clip >= 1000.0:
        return
    row = box.row(align=True)
    row.label(text=f"视图裁剪 {clip:.0f}(远处会被裁掉)", icon="ERROR")
    row.operator("gk.fix_view_clip", text="改大", icon="FULLSCREEN_ENTER")


def _draw_script_carrier_box(layout, context):
    """显示**哪些物体挂了游戏逻辑脚本**(重复勾选会互相覆盖)。"""
    box, opened = collapsible_box(layout, "script_carrier",
                                  "谁挂了脚本", "OBJECT_DATA")
    if not opened:
        return
    try:
        carriers = exporter.script_targets(context.scene)
    except Exception:
        carriers = []
    if not carriers:
        box.label(text="还没有物体挂脚本", icon="ERROR")
        box.label(text="选中角色 -> 『游戏物理』-> 挂游戏逻辑脚本")
        return
    if len(carriers) > 1:
        box.label(text=f"⚠ {len(carriers)} 个物体都挂了脚本(会互相覆盖)",
                  icon="ERROR")
    else:
        box.label(text=f"脚本挂在这个物体上({len(carriers)} 个)", icon="CHECKMARK")
    for obj in carriers[:6]:
        row = box.row(align=True)
        row.label(text=obj.name, icon="OBJECT_DATA")
        op = row.operator("gk.focus_object", text="", icon="VIEWZOOM")
        op.object_name = obj.name
    if len(carriers) > 1:
        box.label(text="只留一个:选中其它的 -> 取消勾选", icon="INFO")


class GK_OT_FocusTree(bpy.types.Operator):
    """点一下跳到那张节点图(省得在下拉框里翻)。"""

    bl_idname = "gk.focus_tree"
    bl_label = "打开这张图"
    bl_description = "在节点编辑器里打开这张节点图"

    tree_name: StringProperty(name="节点图", default="")

    def execute(self, context):
        group = bpy.data.node_groups.get(self.tree_name)
        space = getattr(context, "space_data", None)
        if group is None or space is None:
            return {"CANCELLED"}
        try:
            # ── 先把编辑器类型切对再塞图(第四十轮)────────────────────
            # Blender 的节点编辑器只认**当前类型**的树:显示着『游戏逻辑』
            # 时直接塞一张界面设计图进去会被拒(打不开 / 悄悄弹回)。
            kind = str(getattr(group, "bl_idname", ""))
            if kind in ("GKGameLogicTree", "GKAnimationTree", "GKUiDesignTree",
                        "GKGameAiTree", "GKDisplayTree", "GKAssetTree"):
                space.tree_type = kind
            space.pin = True
            space.node_tree = group
        except Exception:
            self.report({"WARNING"}, "这个编辑器打不开这张图")
            return {"CANCELLED"}
        return {"FINISHED"}


class GK_OT_FocusObject(bpy.types.Operator):
    """在大纲 / 视口里选中那个物体。"""

    bl_idname = "gk.focus_object"
    bl_label = "选中这个物体"
    bl_description = "在场景里选中挂了脚本的那个物体"

    object_name: StringProperty(name="物体", default="")

    def execute(self, context):
        obj = bpy.data.objects.get(self.object_name)
        if obj is None:
            return {"CANCELLED"}
        for other in context.scene.objects:
            other.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {"FINISHED"}



class GK_OT_AddAssetInstance(bpy.types.Operator):
    """再加一份资产的实例(用户 2026-10-05:"游戏静态资产设计")。

    复制**本体**(共享网格数据 = Blender 侧就是实例),摆在它旁边,
    自动勾上『作为实例』—— 导出的场景里这一份复用本体的模型。
    """

    bl_idname = "gk.add_asset_instance"
    bl_label = "再加一份实例"
    bl_description = ("复制本体、在旁边摆一份新实例(自动勾『作为实例』)。"
                      "位置可以在视口里随便拖")
    bl_options = {"REGISTER", "UNDO"}

    source_name: StringProperty(name="本体", default="")

    def execute(self, context):
        source = bpy.data.objects.get(self.source_name) \
            if self.source_name else None
        if source is None:
            # 没指名本体:用当前选中的物体;它自己如果是实例,就找它的本体
            obj = getattr(context, "object", None)
            physics = getattr(obj, "gk_physics", None) if obj else None
            if obj is not None and physics is not None:
                name = str(getattr(physics, "asset_name", "") or "").strip()
                if name:
                    from . import asset_export
                    entry = asset_export.collect_assets(context.scene).get(name)
                    if entry is not None:
                        source = entry["source"]
        if source is None or getattr(source, "type", "") != "MESH":
            self.report({"WARNING"}, "没找到本体:先在『游戏物理』里填『资产名』,"
                                     "或在列表里点这个资产")
            return {"CANCELLED"}
        physics = getattr(source, "gk_physics", None)
        asset_name = str(getattr(physics, "asset_name", "") or "").strip() \
            if physics is not None else ""
        copy = source.copy()                    # 共享网格数据(天然实例)
        copy.data = source.data
        copy.name = f"{asset_name or source.name}_实例"
        for collection in source.users_collection:
            collection.objects.link(copy)
        # 摆在旁边(沿 X 挪一个身位),用户不满意可以直接拖
        width = max(float(getattr(source.dimensions, "x", 1.0) or 1.0), 0.5)
        copy.location = (source.location.x + width * 1.2,
                         source.location.y, source.location.z)
        copy_physics = getattr(copy, "gk_physics", None)
        if copy_physics is not None:
            copy_physics.asset_name = asset_name or source.name
            copy_physics.asset_instance = True
            copy_physics.script_target = False       # 实例不挂脚本
        for other in context.scene.objects:
            other.select_set(False)
        copy.select_set(True)
        context.view_layer.objects.active = copy
        # 报一下"现在场景里共几份" —— 连点几下也知道摆到第几份了
        from . import asset_export
        total = len((asset_export.collect_assets(context.scene)
                     .get(copy_physics.asset_name if copy_physics else "", {})
                     or {}).get("members") or [])
        self.report({"INFO"}, f"已加一份实例: {copy.name}"
                              f"(资产『{copy_physics.asset_name if copy_physics else '?'}』"
                              f",场景里现在共 {total or 1} 份)")
        return {"FINISHED"}


class GK_OT_FixViewClip(bpy.types.Operator):
    """把 3D 视图的『裁剪终点』改大(用户:"拉过去视图上拉不动、显示到头了")。

    默认 100 米对稍大一点的场景就太小了 —— 视图聚焦到远处时整片被裁掉,
    看起来像"模型不见了 / 拉不动",其实只是没画出来。
    """

    bl_idname = "gk.fix_view_clip"
    bl_label = "改大视图裁剪"
    bl_description = "把所有 3D 视图的『裁剪终点』提到 10000 米(大场景不再被裁掉)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        changed = 0
        windows = getattr(context.window_manager, "windows", ()) or ()
        for window in windows:
            screen = getattr(window, "screen", None)
            for area in getattr(screen, "areas", ()) or ():
                if getattr(area, "type", "") != "VIEW_3D":
                    continue
                for space in getattr(area, "spaces", ()) or ():
                    if getattr(space, "type", "") != "VIEW_3D":
                        continue
                    if float(getattr(space, "clip_end", 0.0) or 0.0) < 10000.0:
                        space.clip_end = 10000.0
                        changed += 1
        if not changed:
            self.report({"INFO"}, "视图裁剪已经够大了")
        else:
            self.report({"INFO"}, f"已把 {changed} 个 3D 视图的裁剪终点改到 10000")
        return {"FINISHED"}


class GK_OT_FitCollisionSize(bpy.types.Operator):
    """把碰撞尺寸填成"正好包住这个模型"。"""

    bl_idname = "gk.fit_collision_size"
    bl_label = "贴合模型"
    bl_description = ("按模型的实际尺寸填好碰撞体大小 —— "
                      "省得自己去量(量错就会陷进去 / 悬空)")
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = getattr(context, "object", None)
        physics = getattr(obj, "gk_physics", None) if obj is not None else None
        if obj is None or physics is None:
            return {"CANCELLED"}
        from . import asset_export
        bounds = asset_export.object_bounds([obj])
        if bounds is None:
            self.report({"WARNING"}, "量不出这个模型的尺寸")
            return {"CANCELLED"}
        size = bounds["size"]
        settings = getattr(context.scene, "gk_settings", None)
        is_3d = str(getattr(settings, "dimension", "3")) != "2"
        if is_3d:
            # 面板里的三个数字是 Godot 的轴序:Y 是"高"。
            # 模型是 Blender 的 Z-up,所以高度取 Z(和导出时的换算一致)
            physics.shape_size = (max(abs(size[0]), 0.01),
                                  max(abs(size[2]), 0.01),
                                  max(abs(size[1]), 0.01))
        else:
            physics.shape_size = (max(abs(size[0]), 0.01),
                                  max(abs(size[1]), 0.01), 0.01)
        self.report({"INFO"}, f"碰撞尺寸已贴合模型: {tuple(round(v, 2) for v in physics.shape_size)}")
        return {"FINISHED"}


class GK_OT_ClearCollisionSize(bpy.types.Operator):
    """清成 0 = 回到"按模型自动贴合"。"""

    bl_idname = "gk.clear_collision_size"
    bl_label = "清空"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = getattr(context, "object", None)
        physics = getattr(obj, "gk_physics", None) if obj is not None else None
        if physics is None:
            return {"CANCELLED"}
        physics.shape_size = (0.0, 0.0, 0.0)
        return {"FINISHED"}


# ==========================================================================
# 可折叠的侧栏盒子(用户 2026-10-04:"那几个也可以像导出设置那样折叠起来")
# ==========================================================================
# 侧栏原来是一长串盒子,全摊开着 —— 常用的一两块被一堆"看一眼就够"的
# 信息挤到看不见。『导出设置』早就能折叠(默认收起),现在把同一套交互
# 铺到每个盒子上:标题行可点,三角箭头朝下=展开 / 朝右=收起。
#
# 折叠状态存在模块字典里(跟着当前 Blender 会话走)。**不写进 .blend** ——
# 它是纯粹的"界面偏好",不该在文件里留下用户看不见的数据。
_OPEN_SECTIONS = {}


def collapsible_box(layout, key, label, icon="", default_open=True,
                    header_extra=None):
    """画一个可折叠盒子,返回 (盒子布局, 是否展开)。

    用法:
        box, opened = collapsible_box(layout, "tree_list", "节点图", "NODETREE")
        if opened:
            box.label(text="...")     # 展开时才画内容

    header_extra 是一个 `func(row)`,用来在标题行最右边再放点东西
    (比如日志盒子的"关闭日志"按钮)。
    """
    opened = _OPEN_SECTIONS.get(key, default_open)
    box = layout.box()
    header = box.row(align=True)
    if icon:
        header.label(text="", icon=icon)
    toggle = header.operator(
        "gk.toggle_section", text=label, emboss=False,
        icon="TRIA_DOWN" if opened else "TRIA_RIGHT")
    toggle.key = key
    toggle.default_open = default_open
    if header_extra is not None:
        header_extra(header)
    return box, opened


class GK_OT_ToggleSection(bpy.types.Operator):
    """折叠 / 展开侧栏里的一个盒子。"""

    bl_idname = "gk.toggle_section"
    bl_label = "折叠/展开分区"
    bl_description = "收起或展开这块面板"

    key: StringProperty(name="分区标识", default="")
    default_open: BoolProperty(name="默认展开", default=True)

    def execute(self, context):
        opened = _OPEN_SECTIONS.get(self.key, self.default_open)
        _OPEN_SECTIONS[self.key] = not opened
        # 强制重画侧栏,点了立刻见效(否则要等下一次界面刷新才变)
        area = getattr(context, "area", None)
        if area is not None:
            area.tag_redraw()
        return {"FINISHED"}


# ==========================================================================
# 导出报告(用户 2026-10-05 第二十三轮:"导出报告可视化")
# ==========================================================================
# 数据在 export_report 里(结构化:level / message / hint / node_name),
# 这里只负责画:摘要 + 按级别分组的清单 + 每条一个『定位』按钮。
# 报告本身能一键复制成文本 —— 出问题时用户要把它发出来。

def _report_title(report):
    """折叠盒子的标题(带徽标:错了几个 / 警了几个)。"""
    if not report:
        return "上次导出报告"
    errors, warnings, _infos = export_report.counts(report)
    if errors:
        return f"上次导出报告(❌ {errors} 错误 · {warnings} 警告)"
    if warnings:
        return f"上次导出报告(⚠ {warnings} 警告)"
    return "上次导出报告(✅ 没有告警)"


def _wrap_text(text, width=34):
    """按字数硬折行。

    Blender 的 `label()` **不会自动换行** —— 超长的句子直接被截断
    ("…"),而我们的诊断动辄两三句(还带"怎么修")。所以自己折。
    """
    text = str(text or "")
    lines = []
    while len(text) > width:
        lines.append(text[:width])
        text = text[width:]
    lines.append(text)
    return lines


def _draw_export_report(box, report):
    """画一份导出报告。"""
    if not report:
        box.label(text="还没有导出过 —— 点上面的『导出为 Godot 脚本』导一次",
                  icon="INFO")
        return
    errors, warnings, infos = export_report.counts(report)
    summary = "成功" if report.get("ok") else "失败"
    if report.get("script_name"):
        summary += f" · {report['script_name']}({report.get('lines', 0)} 行)"
    summary += f" · {report.get('written', 0)} 个文件"
    if report.get("elapsed_ms"):
        summary += f" · {report['elapsed_ms']:.0f} ms"
    box.label(text=summary,
              icon="CHECKMARK" if report.get("ok") else "ERROR")
    if report.get("error"):
        stage = f"[{report['stage']}] " if report.get("stage") else ""
        for index, line in enumerate(_wrap_text(stage + report["error"])):
            if index == 0:
                box.label(text=line, icon="ERROR")
            else:
                box.label(text="    " + line)
    if report.get("at"):
        box.label(text=f"{report['at']}", icon="TIME")
    if not (errors or warnings or infos):
        box.label(text="这次导出没有任何问题", icon="CHECKMARK")
    for level, label, icon in (("error", "错误", "ERROR"),
                               ("warning", "警告", "ERROR"),
                               ("info", "提示", "INFO")):
        items = export_report.items_of(report, level)
        if not items:
            continue
        box.separator()
        box.label(text=f"{label}({len(items)})", icon=icon)
        for item in items:
            _draw_report_item(box, item)
    if report.get("dropped"):
        box.label(text=f"(还有 {report['dropped']} 条没列出来)", icon="INFO")
    row = box.row(align=True)
    row.operator("gk.export_report_copy", icon="COPYDOWN")
    row.operator("gk.open_export_folder", text="", icon="FILE_FOLDER")
    box.label(text="出问题时点『复制导出报告』,粘出来就能发", icon="INFO")


def _draw_report_item(box, item):
    """报告里的一条:消息 + 怎么修 + (有节点名就给)定位按钮。"""
    lines = _wrap_text(item.get("message", ""))
    for index, line in enumerate(lines):
        if index > 0:
            box.label(text="    " + line)
        elif str(item.get("level")) == "error":
            box.label(text=line, icon="ERROR")
        else:
            box.label(text=line, icon="DOT")
    if item.get("hint"):
        for index, line in enumerate(_wrap_text("怎么修:" + item["hint"], 32)):
            box.label(text=("    " if index else "  ") + line, icon="INFO")
    if item.get("node_name"):
        row = box.row(align=True)
        focus = row.operator("gk.export_report_focus",
                             text=f"定位到『{item['node_name']}』",
                             icon="VIEWZOOM")
        focus.node_name = item["node_name"]


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

        # ---- 节点图总览 + 参与导出开关(用户 2026-10-03 第五轮)--------
        # 用户原话:"可以在场景集合那里添加节点图显示,可隐藏,可失效的场景功能"。
        # 大纲视图里加不了自定义条目,但**侧栏能列全**:所有逻辑图 / 动画图
        # 一行一个,带『参与导出』勾选 —— 关掉就是"这张图失效"(节点和连线
        # 都留着,随时能开回来)。点名字直接跳进那张图。
        _draw_tree_list_box(layout, context)

        # ---- 预设(用户 2026-10-04 第六条:一键生成第三人称角色)----
        _draw_preset_box(layout, context)

        # ---- 谁挂了脚本(用户第五轮:"不知道哪个搭载了脚本,容易重复")----
        _draw_script_carrier_box(layout, context)

        # ---- 场景资产(用户 2026-10-05:"游戏静态资产设计")----
        _draw_asset_box(layout, context)

        # ---- 对外端口(第 21 轮:"一个游戏逻辑最后应该输出布尔变量")----
        _draw_logic_port_box(layout, context)

        # ---- 变量集中视图(用户 2026-10-04:"图里有哪些变量?谁读谁写?"）----
        _draw_variable_box(layout, context)

        # ---- 函数(用户 2026-10-05 第十七轮:"查看函数,自定义函数")----
        _draw_function_box(layout, context)

        # ---- 导出脚本 ----
        settings = getattr(context.scene, "gk_settings", None)
        box, opened = collapsible_box(layout, "export_script",
                                      "导出为 Godot 脚本", "EXPORT")
        if opened:
            row = box.row(align=True)
            row.scale_y = 1.4
            row.operator("gk.export_script", icon="FILE_TICK")
            row.operator("gk.preview_script", text="", icon="HIDE_OFF")

            if settings is not None:
                col = box.column(align=True)
                row = col.row(align=True)
                row.prop(settings, "write_scene", toggle=True)
                row.operator("gk.open_export_folder", text="", icon="FILE_FOLDER")
                # ── 这里的四个开关第四十二轮搬走了 ──────────────────────
                # 预览碰撞体 / 运行日志 / 调试面板 / 卡通材质(含跟随光照)
                # 全部搬进『游戏显示』设置图(用户:"整合如物理显示等的排列,
                # 不然不好找")—— 侧栏只留导出流程本身的东西。
                # 上帝摄像机(用户 2026-10-04:把"主相机"那一行改成这个开关)。
                # 原来这里是"主相机"下拉 + 拾取按钮 —— 用户的原话是
                # "改成其他要用的或者删了";改成常驻一台上帝摄像机,
                # 运行时按 Tab 在玩家相机和它之间切。
                cam_row = box.row(align=True)
                cam_row.prop(settings, "god_camera", toggle=True,
                             icon="CAMERA_DATA")
                cameras = [o.name for o in context.scene.objects
                           if getattr(o, "type", "") == "CAMERA"]
                if getattr(settings, "god_camera", False):
                    box.label(text="运行时按 Tab 切换玩家相机 / "
                                   "上帝摄像机(WASD + 空格/Shift + 鼠标;"
                                   "上帝视角按 T 时停)",
                              icon="INFO")
                elif not cameras:
                    box.label(text="场景里没有相机 —— 运行时会自动造一台观察机",
                              icon="INFO")
                else:
                    box.label(text=f"自动:跟着视口当前相机({cameras[0]})",
                              icon="CHECKMARK")

                # ── 寻路 / 导航网格(第三十四轮;第三十六轮加 2D / 重烤)──
                # 打开 = 导出的场景里多一块运行时烤的导航网格,
                # 『移动_寻路』的角色就能绕开墙 / 石头走到目标。
                nav_row = box.row(align=True)
                nav_row.prop(settings, "nav_enabled", toggle=True,
                             icon="TRACKER")
                if getattr(settings, "nav_enabled", False):
                    if str(getattr(settings, "dimension", "3")) == "2":
                        # 2D:碰撞体按"障碍物"解析 —— 这是和 3D 最大的差异,
                        # 用户不看到这句就会把整块地板也烤成不可走区。
                        nums = box.row(align=True)
                        nums.prop(settings, "nav_agent_radius", text="半径")
                        box.label(text="2D 按障碍物烤:给墙 / 箱子加碰撞体,"
                                       "整块地板不要加", icon="INFO")
                        box.label(text="启动时烤;门挪了用『移动_重烤导航』重烤",
                                  icon="INFO")
                    else:
                        nums = box.row(align=True)
                        nums.prop(settings, "nav_agent_radius", text="半径")
                        nums.prop(settings, "nav_agent_height", text="高度")
                        nums.prop(settings, "nav_max_slope", text="爬坡°")
                        box.label(text="启动时按静态碰撞体烤网格;改完要重新导出",
                                  icon="INFO")
                        box.label(text="门 / 平台挪了用『移动_重烤导航』重烤",
                                  icon="INFO")
                else:
                    box.label(text="关着:『移动_寻路』退化成直线走", icon="INFO")

        # ---- 模型导出:把 Blender 的网格 / 材质 / 贴图一起带进 Godot ----
        #
        # 材质跟着模型一起导出:没设材质就自动给默认白色。
        # 以前这里拆成"导出模型"和"导出材质"两个按钮,但用户实际
        # 永远是两个一起点 —— 拆开只是多一步操作,所以合并成一个。
        box, opened = collapsible_box(layout, "export_assets",
                                      "导出模型和材质", "MESH_DATA")
        if opened:
            row = box.row(align=True)
            row.scale_y = 1.4
            row.operator("gk.export_assets", icon="EXPORT")
            box.label(text="选中网格 = 只导选中的；不选 = 导出全部", icon="INFO")
            box.label(text="没材质的物体会用默认白色材质", icon="INFO")
            box.label(text="导出会带上卡通材质；描边等额外效果请在 Godot 里加",
                      icon="INFO")

            # ── 上次导出报告(用户 2026-10-05 第二十三轮:导出报告可视化)──
            # 以前这里只列**前 4 条**警告、还把每条压成一行普通 label ——
            # 诊断里附的"怎么修"(hint)完全看不见,而且转头就找不到了。
            # 现在是独立的可折叠盒子:按级别分组、带修复建议、能定位到
            # 出问题的那个节点、能一键复制成文本发出来。
            report = export_report.load(context.scene)
            report_box, report_open = collapsible_box(
                layout, "export_report", _report_title(report), "TEXT",
                default_open=bool(report))
            if report_open:
                _draw_export_report(report_box, report)

        # ---- 节点树状态 ----
        box, opened = collapsible_box(layout, "tree_status", "节点图", "NODETREE")
        if opened:
            if tree is None:
                box.label(text="当前没有游戏逻辑节点树", icon="INFO")
                box.operator("node.new_node_tree", text="新建节点树",
                             icon="ADD").type = "GKGameLogicTree"
            else:
                events, statements = props.event_summary(tree)
                col = box.column(align=True)
                col.label(text=f"节点总数:{len(tree.nodes)}")
                col.label(text=f"事件:{events}    动作:{statements}")
                if events == 0:
                    col.label(text="还没有事件节点，脚本不会执行任何逻辑", icon="ERROR")
                # 「添加节点」按钮删掉(用户第 25 轮):Shift+A 和右键菜单都在,
                # 侧栏再放一个是重复的,而且它常年占着最显眼的位置。

        # ---- 动画系统(独立那张树)----
        box, opened = collapsible_box(layout, "anim_system",
                                      "动画系统(单独一张图)", "ARMATURE_DATA")
        if opened:
            anim_trees = [g for g in bpy.data.node_groups
                          if getattr(g, "bl_idname", "") == "GKAnimationTree"]
            if anim_trees:
                for anim_tree in anim_trees:
                    count = len([n for n in anim_tree.nodes
                                 if getattr(n, "bl_idname", "") in
                                 ("GKNodeAnimTransition", "GKNodeAnimState")])
                    box.label(text=f"{anim_tree.name}  ({count} 个状态)",
                              icon="NODETREE")
            else:
                box.label(text="还没有动画树", icon="INFO")
            box.operator("gk.open_anim_tree", text="打开/新建动画树",
                         icon="FILE_NEW")

        # ---- 界面设计(独立那张图,第四十轮)----
        box, opened = collapsible_box(layout, "ui_design",
                                      "界面设计(单独一张图)", "WINDOW")
        if opened:
            design_trees = [g for g in bpy.data.node_groups
                            if getattr(g, "bl_idname", "") == "GKUiDesignTree"]
            if design_trees:
                for design in design_trees:
                    count = len([n for n in design.nodes
                                 if getattr(n, "gk_ui_builder", False)])
                    box.label(text=f"{design.name}  ({count} 个控件)",
                              icon="NODETREE")
            else:
                box.label(text="还没有界面设计图", icon="INFO")
            box.operator("gk.open_ui_design_tree", text="打开/新建界面设计图",
                         icon="FILE_NEW")

        # ---- AI(独立那张图,第四十一轮)----
        box, opened = collapsible_box(layout, "ai_design",
                                      "游戏AI(单独一张图)", "TRACKER")
        if opened:
            ai_trees = [g for g in bpy.data.node_groups
                        if getattr(g, "bl_idname", "") == "GKGameAiTree"]
            if ai_trees:
                for ai_tree in ai_trees:
                    count = len([n for n in ai_tree.nodes
                                 if getattr(n, "bl_idname", "") == "GKNodeAiState"])
                    box.label(text=f"{ai_tree.name}  ({count} 个状态)",
                              icon="NODETREE")
            else:
                box.label(text="还没有 AI 图", icon="INFO")
            box.operator("gk.open_ai_tree", text="打开/新建 AI 图",
                         icon="FILE_NEW")
            box.label(text="玩法图放『AI_启用』引用它", icon="INFO")

        # ---- 游戏显示设置(独立那张图,第四十二轮)----
        box, opened = collapsible_box(layout, "display_settings",
                                      "游戏显示设置(单独一张图)", "HIDE_OFF")
        if opened:
            display_trees = [g for g in bpy.data.node_groups
                             if getattr(g, "bl_idname", "") == "GKDisplayTree"]
            if display_trees:
                for display_tree in display_trees:
                    box.label(text=f"{display_tree.name}"
                                   f"  ({len(display_tree.nodes)} 张设置卡)",
                              icon="HIDE_OFF")
            else:
                box.label(text="还没有显示设置图", icon="INFO")
            box.operator("gk.open_display_tree", text="打开/新建显示设置图",
                         icon="FILE_NEW")
            box.label(text="物理线框 / 调试面板 / 画面风格 / 视口预览 / 环境氛围"
                           " 都在这张图里", icon="INFO")

        # ---- 游戏资产(独立那张图,第四十三轮)----
        box, opened = collapsible_box(layout, "asset_library",
                                      "游戏资产(单独一张图)", "ASSET_MANAGER")
        if opened:
            asset_trees = _asset_trees()
            if asset_trees:
                for asset_tree in asset_trees:
                    count = len([n for n in asset_tree.nodes
                                 if getattr(n, "bl_idname", "")
                                 in _ASSET_CARD_VALUE_PROP])
                    box.label(text=f"{asset_tree.name}  ({count} 份资产)",
                              icon="ASSET_MANAGER")
            else:
                box.label(text="还没有资产库", icon="INFO")
            box.operator("gk.open_asset_tree", text="打开/新建资产库",
                         icon="FILE_NEW")
            box.label(text="3D 道具 / 图片 / 曲线 / 音频;跨场景共用",
                      icon="INFO")

        # 「动作分类」删掉(用户第 24、25 轮两次点名):
        # 它只是给动作打个标签,而标签现在没有任何地方用到 ——
        # 占了侧栏一大块,却只显示"未分类 · 待机"这种没用的一行。
        # 函数 `_draw_anim_category_box` 留着(以后要做"按分类筛选"再挂回来)。

        # ---- 导出设置(默认**收起**)----
        # 用户第 24 轮:"侧边栏这里的东西太多了,删掉一些没用的"。
        # 3D/2D、基类、类名这些**开一次就不用再动**,常驻只会挤掉
        # 真正常用的东西。现在折起来,要用再展开。
        if settings is not None:
            box = layout.box()
            row = box.row(align=True)
            row.prop(settings, "show_export_settings",
                     text="导出设置", icon="TRIA_DOWN"
                     if getattr(settings, "show_export_settings", False)
                     else "TRIA_RIGHT", emboss=False)
            if getattr(settings, "show_export_settings", False):
                col = box.column(align=True)
                row = col.row(align=True)
                row.prop(settings, "dimension", expand=True)
                col.prop(settings, "base_class", text="")
                col.prop(settings, "class_name", text="类名")
                col.prop(settings, "node_name", text="节点名")
                # 本场景用哪棵逻辑图(用户 2026-10-04:多场景各导一张 .tscn)。
                # 留空 = 自动(整个 .blend 只有一棵图时就用它)。
                row = col.row(align=True)
                row.operator("gk.pick_logic_tree", text="逻辑图:"
                             + (settings.logic_tree or "自动"), icon="NODETREE")
                row = col.row(align=True)
                row.prop(settings, "export_tunables", toggle=True)
                row.operator("gk.reset_settings", text="", icon="LOOP_BACK")

        # ---- 诊断(用户第 26 轮:"每次又错误时加入调试信息")----
        # 出问题时点一下:插件版本、每个物体的网格/碰撞尺寸、节点图内容、
        # 主相机解析结果、导出告警 —— 全写进一个 txt,直接发我。
        diag, opened = collapsible_box(layout, "diagnostics", "出问题了?", "QUESTION")
        if opened:
            diag.operator("gk.dump_diagnostics", text="导出诊断信息",
                          icon="TEXT")

        # ---- 日志 ----
        if settings is not None and settings.show_log:
            box, opened = collapsible_box(
                layout, "log", "日志", "TEXT",
                header_extra=lambda row: row.prop(
                    settings, "show_log", text="", icon="TRASH"))
            if opened:
                records = gk_log.records()[-8:]
                if not records:
                    box.label(text="(还没有日志)")
                for level, line in records:
                    icon = {"ERROR": "ERROR", "WARN": "ERROR"}.get(level, "INFO")
                    box.label(text=line[11:] if len(line) > 11 else line,
                              icon=icon)


def _draw_project_box(layout, context):
    """画"项目"面板:这个 .blend 绑定的 Godot 项目。

    设计意图:导出目录不该是插件设置里的一个路径 —— 那意味着每开一个
    新 .blend 都要重新填。改成跟着 .blend 走之后,打开哪个文件就知道
    该写哪,面板只需要把当前绑定显示清楚。

    这里最容易让人困惑的是"打开 Godot 的按钮去哪了" —— 答案是
    项目还没建出来时不显示它(没东西可打开)。所以未建状态下必须
    把下一步说清楚,不能只放一个孤零零的按钮。
    """
    box, opened = collapsible_box(layout, "project", "Godot 项目", "FILE_FOLDER")
    if not opened:
        return

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

        # ── 打包发布(补全流程最后一环:出可执行文件)──────────────────
        # 以前这里到"运行游戏"为止 —— 要 exe 还得自己开 Godot 配导出预设、
        # 装导出模板、再点一次导出。现在一个按钮走完:
        # 导出工程 -> 生成预设(不覆盖已有的)-> godot --export-release。
        row = box.row(align=True)
        row.scale_y = 1.4
        row.operator("gk.export_release", text="打包成可执行文件",
                     icon="EXPORT")
        released = ops.LAST_RELEASE.get("output", "")
        if released:
            row = box.row(align=True)
            row.label(text=f"上次打包: {os.path.basename(released)}", icon="CHECKMARK")
            row.operator("gk.open_release_folder", text="", icon="FILE_FOLDER")
        else:
            box.label(text="打出可直接运行的 exe(需装导出模板)", icon="INFO")

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
    flat(默认):「搜索与常用」子菜单 + 全部分类 + 「节点工具」子菜单。
      右键菜单用这个 —— 顶部/底部各收成一项,菜单更短(用户 2026-10-04)。

    nested:把这些内容收进一个『添加游戏逻辑节点』子菜单里。
      Shift+A 用这个:Blender 自带的『添加』菜单也在同一个菜单里，
      不收起来的话一屏里会出现两套并列的"加节点"，看起来就像重复了。
    """
    if nested:
        layout.menu(GK_MT_AddNodeCategories.bl_idname,
                    text="添加游戏逻辑节点", icon="NODETREE")
        layout.operator("gk.auto_link", text="把选中的两个节点连起来", icon="LINKED")
        layout.operator("gk.add_comment_box", text="注释框(框住选中节点)",
                        icon="PIVOT_BOUNDBOX")
        return

    # ── flat 形态 ──────────────────────────────────────────────────
    # 顶部四项(搜索 / 常用 / 最近使用 / 自动接续提示)收进一个子菜单
    # (用户 2026-10-04:"右键菜单的那几个也可以折叠起来")。
    layout.menu(_menu_name("搜索与常用"), text="搜索与常用", icon="VIEWZOOM")

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

    # 底部两项(自动连线 / 注释框)也收进一个子菜单(用户 2026-10-04)。
    layout.separator()
    layout.menu(_menu_name("节点工具"), text="节点工具", icon="TOOL_SETTINGS")


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


# ── 菜单重复:根因与修法(用户报了多次)─────────────────────────
# 现象:右键 / Shift+A 的整块内容出现两份、三份，而且越重载越多。
#
# 真根因(已在 Blender 4.5.5 上跑无头脚本实测复现):
#   1) 插件重载时 `importlib.reload()` 是**原地重新执行**模块 ——
#      模块对象(及其 `__dict__`)还是同一个，只是函数对象全换成了新的。
#   2) 旧的那份 draw 回调仍挂在 Blender 的菜单类上；上一版想靠
#      "比较模块对象判断过期" 让它闭嘴 —— 但模块对象根本没换，
#      这个判断永远是 False，于是旧回调照画不误。
#   3) `register()` 里又拿**新函数对象**去 `remove()`，与旧函数不是
#      同一个对象，摘不掉；接着 `prepend()` -> 每个版本留一份。
#
# 修法:给本插件挂上的回调打一个**函数属性标记**；重挂之前，遍历菜单类
#   上的 draw 回调列表，把带标记的**全部**摘掉(不管它是哪一版模块留下的)，
#   再挂当前这一份。
#
#   回调列表存在 `cls.draw._draw_funcs`(见 Blender 的
#   `bpy_types._GenericUI._dyn_ui_initialize` / `append` / `prepend` / `remove`)。
#   它挂在**菜单类对象**上，而菜单类是 Blender 自己的、跨插件重载不会丢 ——
#   所以历史遗留的旧函数都能被找到并 `remove` 掉(remove 本身是好用的，
#   之前"摘不掉"的判断是错的)。
_GK_MENU_HOOK = "_gk_menu_hook"


def _menu_draw_funcs(target):
    """取菜单类上的 draw 回调列表(必要时触发 Blender 的初始化)。"""
    funcs = getattr(target.draw, "_draw_funcs", None)
    if funcs is None:
        try:
            funcs = target._dyn_ui_initialize()
        except Exception:
            funcs = None
    return funcs


def _purge_gk_hooks(target):
    """摘掉**所有**本插件挂过的 draw 回调(含以前重载遗留的旧函数)。"""
    funcs = _menu_draw_funcs(target)
    if not funcs:
        return
    for old in [f for f in funcs if getattr(f, _GK_MENU_HOOK, False)]:
        try:
            target.remove(old)
        except Exception:
            pass


def _install_gk_hook(target, func):
    """先清掉历次遗留的同名回调，再挂当前这一份 —— 保证菜单只出现一次。"""
    _purge_gk_hooks(target)
    setattr(func, _GK_MENU_HOOK, True)
    target.prepend(func)


def on_register():
    """注册后的自检与菜单挂钩。"""
    # ---- 挂菜单:先按标记清掉历次遗留,再挂当前这一份(见上方说明)----
    for target_name, func in (
        ("NODE_MT_add", _draw_add_menu),
        ("NODE_MT_context_menu", _draw_context_menu),
    ):
        target = getattr(bpy.types, target_name, None)
        if target is None:
            gk_log.warn(f"找不到 {target_name},跳过挂载")
            continue
        try:
            _install_gk_hook(target, func)
        except Exception as exc:  # pragma: no cover
            gk_log.error(f"挂载 {target_name} 失败: {exc}")

    # ---- 界面设计图的视口叠加预览(第四十轮)----
    # 先摘旧句柄再挂:重载会留下上一版的回调(行为一样但函数对象已过期),
    # 不摘的话画两遍。绘制本身有 try 兜底,挂载失败也只是少个预览。
    _remove_ui_preview_handler()
    try:
        _UI_PREVIEW_HANDLE.append(
            bpy.types.SpaceView3D.draw_handler_add(
                _ui_preview_draw, (), "WINDOW", "POST_PIXEL"))
    except Exception as exc:                              # pragma: no cover
        gk_log.warn(f"界面预览绘制挂载失败: {exc}")

    # ---- AI 图的视口叠加预览(第四十一轮,同一套)----
    _remove_ai_preview_handler()
    try:
        _AI_PREVIEW_HANDLE.append(
            bpy.types.SpaceView3D.draw_handler_add(
                _ai_preview_draw, (), "WINDOW", "POST_PIXEL"))
    except Exception as exc:                              # pragma: no cover
        gk_log.warn(f"AI 预览绘制挂载失败: {exc}")

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
# 右键菜单收起来的两块(用户 2026-10-04):顶部『搜索与常用』/ 底部『节点工具』
TOP_MENU = _make_top_menu()
TOOLS_MENU = _make_tools_menu()


# ==========================================================================
# 快捷键:节点编辑器里按 F5 = 直接运行游戏
# ==========================================================================
# ── 为什么必须有它(用户 2026-10-03)──────────────────────────────────
# 用户原话:"突然发现按 F5 没法直接启动"。
# 按钮上写着"直接运行游戏(F5)",但**从来没有注册过这个快捷键** ——
# 按 F5 什么也不会发生(节点编辑器里 F5 本来就是空的)。
# 按钮文字许诺了快捷键,就得真的把它接上。
_addon_keymaps = []


def register_keymaps():
    """注册 F5(节点编辑器)。拿不到 keyconfig 就安静跳过(离线/无头环境)。"""
    try:
        import bpy
        wm = getattr(bpy.context, "window_manager", None)
        kc = getattr(getattr(wm, "keyconfigs", None), "addon", None)
        if kc is None:
            return 0
        # ── 必须用 find() 拿**已存在**的那个 keymap(踩过的坑)──────────
        # 用 `keymaps.new()` 会新建一个**同名的孤儿 keymap** ——
        # Blender 只认内置/已有的那个,新建的根本不参与匹配,
        # 于是"注册成功了但按 F5 没反应"(用户实测)。
        km = kc.keymaps.find(name="Node Editor", space_type="NODE_EDITOR")
        if km is None:
            km = kc.keymaps.new(name="Node Editor", space_type="NODE_EDITOR")
        item = km.keymap_items.new("gk.open_godot", "F5", "PRESS")
        # editor=False 才是"运行游戏";True 是"打开编辑器"
        item.properties.editor = False
        _addon_keymaps.append((km, item))
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[BlenderGameKit] F5 快捷键没注册上: {exc}")
        return 0


def unregister_keymaps():
    try:
        for km, item in _addon_keymaps:
            try:
                km.keymap_items.remove(item)
            except Exception:
                pass
        _addon_keymaps.clear()
    except Exception:
        pass


# 注册清单。这个元组必须在所有类都定义完之后才求值,
# 所以放在文件末尾附近 —— 放中间的话后面定义的类会漏注册。
GK_CLASSES = (
    GKGameLogicTree,
    GKAnimationTree,
    GKUiDesignTree,
    GKGameAiTree,
    GKDisplayTree,
    GKAssetTree,
    # 侧栏里"点一下就跳过去"的两个小算子(节点图总览 / 脚本载体)
    GK_OT_FocusTree,
    GK_OT_FocusObject,
    # 逻辑图的对外端口
    GK_OT_PortAdd,
    GK_OT_PortRemove,
    GK_OT_PortMakeNode,
    # 逻辑图的变量声明 + 节点下拉选(用户 2026-10-04)
    GK_OT_LogicVarAdd,
    GK_OT_LogicVarRemove,
    GK_OT_PickVar,
    # 变量的"实现"(UE5 的 getter/setter;用户 2026-10-05 第十七轮)
    GK_OT_VarBindImpl,
    # 逻辑图的函数声明 + 函数节点(用户 2026-10-05 第十七轮)
    GK_OT_FuncAdd,
    GK_OT_FuncRemove,
    GK_OT_MakeFunctionNode,
    GK_OT_PickFunction,
    GK_OT_CollapseToFunction,
    # 碰撞一键贴合
    GK_OT_FitCollisionSize,
    GK_OT_ClearCollisionSize,
    # 静态资产:再加一份实例 + 修视图裁剪(用户 2026-10-05)
    GK_OT_AddAssetInstance,
    GK_OT_FixViewClip,
    # 诊断信息(用户第 26 轮:出问题时把调试信息发我)
    diagnostics.GK_OT_DumpDiagnostics,
    GK_OT_OpenAnimTree,
    GK_OT_OpenUiDesignTree,
    GK_OT_OpenAiTree,
    GK_OT_OpenDisplayTree,
    GK_OT_DisplayFillCards,
    # 资产库(第四十三轮):打开 / 收录 / 登记 / 收集合 / 摆一份 / 定位
    GK_OT_OpenAssetTree,
    GK_OT_AssetFillCards,
    GK_OT_AssetRegister,
    GK_OT_AssetCollect,
    GK_OT_AssetPlace,
    GK_OT_AssetFocus,
    GK_OT_AssetFocusNode,
    GK_OT_SwitchTree,
    GK_OT_AnimVarAdd,
    GK_OT_AnimVarRemove,
    GK_OT_AnimVarMakeNode,
    GK_OT_AnimVarPick,
    GK_OT_AddNodeMenu,
    GK_OT_FindNode,
    GK_MT_AddNodeCategories,
    COMMON_MENU,
    RECENT_MENU,
    TOP_MENU,
    TOOLS_MENU,
    GK_MT_TargetPicker,
    GK_OT_SetTarget,
    GK_OT_PickTarget,
    # 场景 -> 逻辑图 绑定(用户 2026-10-04:多场景各导一张 .tscn)
    GK_OT_SetLogicTree,
    GK_OT_PickLogicTree,
    # 侧栏盒子的折叠开关(用户 2026-10-04)
    GK_OT_ToggleSection,
    GK_PT_Main,
    GK_PT_AnimTree,
    GK_PT_UiDesign,
    GK_PT_AiDesign,
    GK_PT_DisplayTree,
    GK_PT_AssetTree,
    GK_PT_ObjectPhysics,
    GK_PT_GodotLook,
) + tuple(CATEGORY_MENUS)


def on_unregister():
    # 摘掉界面预览和 AI 预览的视口绘制回调(留着的话禁用插件后它还在画)
    _remove_ui_preview_handler()
    _remove_ai_preview_handler()
    # 摘掉菜单回调。按标记摘 —— 这样连以前重载遗留的旧函数也一起清掉,
    # 禁用插件后不会留野回调。
    for target_name in ("NODE_MT_add", "NODE_MT_context_menu"):
        target = getattr(bpy.types, target_name, None)
        if target is None:
            continue
        _purge_gk_hooks(target)
    ops.LAST_EXPORT.clear()
    ops.RECENT_NODES.clear()
