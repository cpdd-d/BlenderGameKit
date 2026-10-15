# 节点定义共享地基:辅助函数、常量、引脚类型与登记器(CLASSES / N)。
# 从原 nodes/nodes.py 头部原样搬出(GPT 审查)。领域模块 `from ._common import *`
# 取用这里的名字;门面 nodes/nodes.py 按顺序导入领域模块并合并命名空间。
"""GK 节点集合。

命名约定:
    gk_in_* / 引脚属性   未连线时引脚读哪个属性(见 gk_input_props)
    gk_cfg_*            纯配置，只画在节点体或侧栏

四大类，显示在"添加节点"菜单里:
    事件   程序入口(_ready / _process / _physics_process / _input / 自定义函数)
    动作   对节点和游戏状态做的事
    运算   纯取值表达式
    实用   流程与整理
"""

from . import node_base as B
from .node_base import finalize, gd_node_path, gd_string, snake_case, target_pin, TARGET_SELF
from .. import gk_sockets as S


# ==========================================================================
# 两张树里都能用的节点
# ==========================================================================
# 用户 2026-10-03 的反馈:"之前添加的节点使用不了" —— 他在**动画树**里点
# 『运算 ▸ 常量 ▸ 常量_数值』,弹出的是
#     RuntimeError:『常量_数值』是游戏逻辑树里的节点,不能放进当前的图
#
# 根因有两层:菜单没按树类型过滤(见 properties.node_fits_tree),以及
# **动画树里开放的节点太少了** —— 只有 6 个,连"血少于 30% 就切受伤状态"
# 这种最基本的转场条件都拼不出来(常量、比较、数学全在主图里)。
#
# 虚幻 5 的动画蓝图里,转场条件写的就是普通蓝图表达式。所以这里把
# **纯取值**(没有执行流、随时求值都安全)的节点整类开放给动画树。
BOTH_TREES = ("GKGameLogicTree", "GKAnimationTree")

# 界面控件构建节点只属于『游戏界面』设计图。游戏逻辑树通过
# 『界面_套用设计』创建它们，并通过运行时控件节点控制和响应。
UI_TREES = ("GKUiDesignTree",)


# ==========================================================================
# 动作(Action)归属识别 —— "动画名下拉"的地基
# ==========================================================================
# 背景:用户踩过这个坑 —— 节点里动画名是默认的 `idle`,
# 而模型里的动作叫 `骨骼动作` / `形态键动作`,于是播不了。
#
# ── 为什么不能简单地把 bpy.data.actions 全列出来 ──────────────────
# `bpy.data.actions` 是**全局**的,不区分属于哪个角色。
# 场景里有两个角色时,动作会混在一个列表里,选错是迟早的事。
#
# ── 实测出来的归属依据(用户的胡桃模型)────────────────────────────
#
#     动作             id_root     slots
#     骨骼动作         OBJECT      ['胡桃-海灯节_arm']   <- 槽名就是骨架名
#     形态键动作       KEY         ['Key']
#
# Blender 4.4+ 的 slotted action 会把**绑定的物体名**记在 slot 上,
# 这是最可靠的归属依据。拿不到 slot 时退回名字匹配 + 类型判断。
# ==========================================================================

# id_root -> 这类动作该挂在什么类型的物体上
_ACTION_ROOT_TYPES = {
    "OBJECT": ("ARMATURE", "OBJECT", "MESH"),
    "KEY": ("MESH",),
}

# 用户可读的类别名(画在下拉列表里,帮他分辨)
_ACTION_KIND_LABEL = {
    "OBJECT": "骨骼/物体",
    "KEY": "形态键",
    "MATERIAL": "材质",
    "NODETREE": "节点",
    "PARTICLE": "粒子",
}


# ── 动作分类标签(用户要的"分类动画:静止 / 移动 / 攻击…")───────────
# 存哪儿:直接塞进 Action 的**自定义属性** `action["gk_anim_category"]`。
#
# 为什么不用 PropertyGroup / 注册 bpy.types.Action 属性:
#   * 自定义属性随 .blend 一起保存,禁用插件也**不会丢**
#   * 不用在 register() 里往 bpy.types.Action 上挂属性 —— 插件一禁用
#     就留下一个"悬空"的属性定义,Blender 有时会抱怨
#   * 一个动作只需要一个分类,不需要列表结构
ANIM_CATEGORY_NAMES = ("静止", "移动", "攻击", "受击", "死亡", "其他")

# 存进 Action 自定义属性用的键名
ANIM_CATEGORY_KEY = "gk_anim_category"


def action_category(action):
    """取一个动作的分类标签(没打过就返回空串)。"""
    if action is None:
        return ""
    try:
        return str(action.get(ANIM_CATEGORY_KEY, "") or "")
    except (AttributeError, KeyError, TypeError, ReferenceError):
        # 只有这几种是**预期内**的:桩对象没有 .get / 自定义属性不存在 /
        # 动作刚被释放(ReferenceError)。其它异常不该在这里被吞掉,
        # 否则真正的 bug 会伪装成"这个动作没有分类"(GPT 审查)
        return ""


def set_action_category(action, category):
    """给动作打分类标签;category 为空就清掉。"""
    if action is None:
        return
    category = str(category or "")
    if category:
        action[ANIM_CATEGORY_KEY] = category
    elif ANIM_CATEGORY_KEY in action:
        del action[ANIM_CATEGORY_KEY]


def animation_tree_items():
    """给『选…』按钮用的动画树清单:(标识, 显示名, 说明)。

    ── 为什么必须有这个函数(踩过的坑)────────────────────────────
    `operator_io.GK_OT_PickAnimTree.draw()` 一直在调 `animation_tree_items()`,
    但这个函数**从来没被定义过** —— 用户点节点上那个『选…』放大镜时
    直接 `AttributeError`,弹窗根本打不开。
    (旁边还有个能手打的文本框,所以这个坑一直没被发现。)
    """
    auto = ("", "自动(用文件里第一张)", "留空就用这张")
    try:
        import bpy
    except Exception:
        return [auto]
    data = getattr(bpy, "data", None)
    groups = getattr(data, "node_groups", None) if data is not None else None
    if groups is None:
        return [auto]
    trees = []
    for group in groups:
        if getattr(group, "bl_idname", "") != "GKAnimationTree":
            continue
        count = len([n for n in (getattr(group, "nodes", ()) or ())
                     if getattr(n, "bl_idname", "") in ("GKNodeAnimState",
                                                        "GKNodeAnimTransition")])
        trees.append((str(group.name), f"{group.name}  ({count} 张卡片)", "这张树"))
    return [auto] + trees


def node_key(node):
    """节点的**稳定标识** —— 不能拿 `id()` 当身份(踩过的坑,很隐蔽)。

    ── 为什么(实测数据)────────────────────────────────────────────
    Blender 里 `tree.nodes[i]` 和 `link.to_node` 返回的可能是**两个不同的
    Python 包装对象**,同一个节点会拿到两个不同的 `id()`,连 `is` 都是 False:

        状态节点: 动画_状态.001  id= 765860832     <- 遍历 nodes 拿到的
        直连:     动画_状态 -> 动画_状态.001
                  id(dst)= 765864288              <- 顺着连线拿到的,同一个节点
                  dst in states: False            <- is 比较失败

    后果非常隐蔽:"谁指向谁"全判错 —— 入口状态选错(把终点当起点)、
    孤立状态误报、转移生成到不存在的段里。离线测试(纯 Python 桩)永远
    复现不出来,因为桩对象的 id 是稳定的。

    真正的稳定标识是底层 C 结构地址 `as_pointer()`。桩环境没有它就退回
    `id()`(桩里本来就是稳定的)。
    """
    pointer = getattr(node, "as_pointer", None)
    if callable(pointer):
        try:
            return int(pointer())
        except Exception:
            pass
    return id(node)


def animation_tree_names():
    """文件里所有动画树的名字(不含"自动"那一项)。

    给『动画_状态机』判断"要不要让用户选树名":只有一张时根本不用选,
    直接显示"会用哪一张"就行。
    """
    return [item[0] for item in animation_tree_items() if item[0]]


def find_animation_tree(name=""):
    """按名字找一张动画树;名字为空就用文件里第一张(找不到返回 None)。

    面板、节点上的预览、代码生成三处都要问"这个节点会用哪张树",
    判断必须只有一份 —— 否则会出现"面板说用 A、导出却用了 B"。
    """
    try:
        import bpy
    except Exception:
        return None
    data = getattr(bpy, "data", None)
    groups = getattr(data, "node_groups", None)
    if groups is None:
        return None
    wanted = str(name or "")
    first = None
    for group in groups:
        if getattr(group, "bl_idname", "") != "GKAnimationTree":
            continue
        if first is None:
            first = group
        if wanted and str(getattr(group, "name", "")) == wanted:
            return group
    return first if not wanted else None


# ==========================================================================
# 界面设计图(第四十轮)—— 和动画树同一套"独立画布 + 一个引用节点"的套路
# ==========================================================================
# 用户第 40 轮原话:"添加游戏界面设计编辑器,方便对UI设计"。
# 界面节点以前全挤在玩法图里,菜单一多(文本/按钮/面板/列表/…)玩法图就糊了。
# 现在照动画树的做法分开:界面"长什么样"画在**独立一张图**里,
# 玩法图只放一个『界面_套用设计』引用它(见 codegen.services)。
def ui_design_tree_items():
    """给『选…』按钮用的界面设计图清单:(标识, 显示名, 说明)。

    和 `animation_tree_items` 同一套:标识就是树名(空串 = 自动用第一张)。
    """
    auto = ("", "自动(用文件里第一张)", "留空就用这张")
    try:
        import bpy
    except Exception:                                     # noqa: BLE001
        return [auto]
    data = getattr(bpy, "data", None)
    groups = getattr(data, "node_groups", None) if data is not None else None
    if groups is None:
        return [auto]
    trees = []
    for group in groups:
        if getattr(group, "bl_idname", "") != "GKUiDesignTree":
            continue
        count = len([n for n in (getattr(group, "nodes", ()) or ())
                     if getattr(n, "gk_ui_builder", False)])
        trees.append((str(group.name), f"{group.name}  ({count} 个控件)", "这张设计图"))
    return [auto] + trees


def ui_design_tree_names():
    """文件里所有界面设计图的名字(不含"自动"那一项)。"""
    return [item[0] for item in ui_design_tree_items() if item[0]]


def find_ui_design_tree(name=""):
    """按名字找一张界面设计图;名字为空就用文件里第一张(找不到返回 None)。

    面板、节点上的预览、代码生成三处都要问"这个引用节点会用哪张图",
    判断必须只有一份 —— 和 `find_animation_tree` 同一个理由。
    """
    try:
        import bpy
    except Exception:                                     # noqa: BLE001
        return None
    data = getattr(bpy, "data", None)
    groups = getattr(data, "node_groups", None)
    if groups is None:
        return None
    wanted = str(name or "")
    first = None
    for group in groups:
        if getattr(group, "bl_idname", "") != "GKUiDesignTree":
            continue
        if first is None:
            first = group
        if wanted and str(getattr(group, "name", "")) == wanted:
            return group
    return first if not wanted else None


def main_ui_design_tree():
    """文件里勾了『主界面』的设计图(没有就 None;勾了多张取**第一张**)。

    ── 『主界面』是什么(第一百一十二轮)────────────────────────────
    跨场景常驻的那一份界面:导出时单独生成 `scripts/gk_main_ui.gd`
    并注册成 Godot autoload —— 开局建一次、**切关卡不消失**。
    所以它同时是"不用每关各画一份重复界面"的落点。

    勾多张时导出侧会告警并只用第一张 —— 两份"常驻主界面"叠在一起
    没有任何合理语义(取第一张 = 文件里的先后顺序,稳定可预期)。
    """
    try:
        import bpy
    except Exception:                                     # noqa: BLE001
        return None
    data = getattr(bpy, "data", None)
    groups = getattr(data, "node_groups", None)
    if groups is None:
        return None
    for group in groups:
        if getattr(group, "bl_idname", "") != "GKUiDesignTree":
            continue
        if bool(getattr(group, "gk_main_ui", False)):
            return group
    return None


def main_ui_design_trees():
    """勾了『主界面』的全部设计图(导出侧靠它出"勾了多张"的告警)。"""
    try:
        import bpy
    except Exception:                                     # noqa: BLE001
        return []
    data = getattr(bpy, "data", None)
    groups = getattr(data, "node_groups", None)
    if groups is None:
        return []
    return [group for group in groups
            if getattr(group, "bl_idname", "") == "GKUiDesignTree"
            and bool(getattr(group, "gk_main_ui", False))]


# ── 『主界面』的导出名字(第一百一十二轮,两处必须一致)──────────────
# 生成侧(codegen 里『界面_主界面』拼 `show_main(...)` 的查找路径)和
# 导出侧(asset_export 写脚本文件 + 注册 [autoload])都读这里的常量 ——
# 一边改名另一边忘了,表现是"节点点了没反应",很难查。
MAIN_UI_AUTOLOAD = "GkMainUi"          # Godot autoload 名 = /root/GkMainUi
MAIN_UI_SCRIPT = "gk_main_ui.gd"       # 落盘到 scripts/ 的文件名


# ==========================================================================
# 动画树 → "状态图" —— 面板显示和代码生成共用这一份解析
# ==========================================================================
# ── 这一节是为什么存在的(用户 2026-10-03 报"动画_状态机未能正常工作")──
# 拿他的真实工程(骨骼_动画_测试.blend)导出后实测,四个原因:
#
#   1. 『动画_转场』的『上游』没接线。他把状态的『转到』**直接连到了**
#      下一个状态的『进入』—— 老实现只认"状态→转场→状态"这条链,
#      于是导出结果里 _physics_process 只有一句 pass,状态机完全不动。
#      **现在直连也算数**,语义是"这个动画播完就切过去"(kind=finish)。
#   2. 一个状态只能有一条出线。走链的实现只取第一条,第二条静默丢掉;
#      而真实状态机最常见的形状恰恰是 idle→run / idle→jump 多个出口。
#      **现在一个状态能接任意多条**,按节点在画布上的上下位置定优先级。
#   3. 起始状态的动画从来没被播过 —— 只写了 `anim_state = "骨骼动作"`,
#      没有任何一处调用 play()。**现在入口状态真的会播**。
#   4. 出问题时用户看不见 —— 告警只出现在导出报告里。现在节点上、
#      面板里都会把整张图摊开(见 ui._draw_anim_tree_graph)。
ANIM_STATE_ID = "GKNodeAnimState"
ANIM_TRANSITION_ID = "GKNodeAnimTransition"
ANIM_ENTRY_ID = "GKNodeAnimEntry"
ANIM_EXIT_ID = "GKNodeAnimExit"

# 一条边(转场)的条件从哪来
ANIM_EDGE_PIN = "pin"        # 接在『条件』引脚上的表达式
ANIM_EDGE_FINISH = "finish"  # 上游状态那个动画播完了(状态直连用的就是它)
ANIM_EDGE_EXIT = "exit"      # 走到『动画_结束』了(整张图收工)
# 『条件来源』选了输入类 -> 对应的 Godot 判定函数。
# 键名同时也是边的 kind,所以这里一份表两用。
ANIM_ACTION_SOURCES = {
    "ACTION_PRESSED": "Input.is_action_pressed",
    "ACTION_JUST": "Input.is_action_just_pressed",
    "ACTION_RELEASED": "Input.is_action_just_released",
}


def anim_layout_key(node):
    """画布上的先后顺序:上 -> 下、左 -> 右。

    ── 为什么用 Y 排优先级 ────────────────────────────────────────
    一个状态有多条出线时,"先判哪条"必须有确定答案,否则导出结果会
    随节点内部顺序变化,同一个图导出两次可能不一样。用户能控制的只有
    摆位,所以**用画布上的上下顺序**当优先级 —— 和蓝图里"从上往下第一个
    成立的分支赢"是同一个直觉(Blender 的 Y 轴向上,所以取负号)。
    """
    x, y = getattr(node, "location", (0.0, 0.0))[:2]
    return (-round(float(y)), round(float(x)))


def anim_state_name(node):
    """一个状态叫什么 —— 优先用选中的**动作名**,否则用手填的状态名。"""
    if node is None:
        return ""
    resolver = getattr(node, "resolved_state_name", None)
    name = (resolver() if resolver is not None
            else getattr(node, "gk_state_name", ""))
    return str(name or "").strip()


def anim_state_nodes(tree):
    """树里所有『动画_状态』,按画布顺序(上到下、左到右)排。"""
    nodes = [n for n in (getattr(tree, "nodes", ()) or ())
             if getattr(n, "bl_idname", "") == ANIM_STATE_ID]
    nodes.sort(key=anim_layout_key)
    return nodes


def animation_tree_states(tree):
    """给『选…』按钮用的状态清单:(状态名, 显示名, 说明)。"""
    items = []
    for state in anim_state_nodes(tree):
        name = anim_state_name(state)
        if not name:
            items.append(("", f"{getattr(state, 'name', '?')}(还没设名字)",
                          "先在节点上选一个动作"))
            continue
        items.append((name, name, f"节点名 {getattr(state, 'name', '?')}"))
    return items


def _socket_links(node, socket_name, is_output):
    """某个引脚上的所有连线(桩与 Blender 两种实现都能用)。"""
    collection = getattr(node, "outputs" if is_output else "inputs", None)
    getter = getattr(collection, "get", None)
    socket = getter(socket_name) if callable(getter) else None
    if socket is None:
        return []
    out = []
    for link in getattr(socket, "links", ()) or ():
        other = getattr(link, "to_node" if is_output else "from_node", None)
        if other is not None:
            out.append(other)
    return out


def link_targets(node, socket_name):
    """输出引脚连到的所有节点。"""
    return _socket_links(node, socket_name, True)


def link_sources(node, socket_name):
    """输入引脚的上游节点(FLOW 输入只可能有一条,这里仍然返回列表)。"""
    return _socket_links(node, socket_name, False)


def transition_is_used(trans):
    """这条转场用户到底动过没有?

    刚拖进来、什么都没接的『动画_转场』不该刷一堆告警 ——
    但**接了条件或选了目标**的就说明用户是想用它,那时缺什么必须说清。
    """
    for socket_name in ("上游", "条件"):
        if link_sources(trans, socket_name):
            return True
    if link_targets(trans, "转到"):
        return True
    if str(getattr(trans, "gk_from_state", "") or "").strip():
        return True
    if str(getattr(trans, "gk_to_state", "") or "").strip():
        return True
    if str(getattr(trans, "gk_cond_source", "PIN")) != "PIN":
        return True
    return False


def _edge_condition_key(edge):
    """两条边的条件"是不是同一件事" —— 确定不了就返回 None。

    只有两种条件能在**纯拓扑**这一层判定相同:
      * 播完(kind=finish)—— 条件永远是"上游那段播完了"
      * 选输入(kind=ACTION_*)—— 看动作名
    引脚上的表达式不去比:两条不同的判断切到同一个状态是合法的
    (第二条是 elif 的兜底),误报比漏报更烦人。
    """
    kind = edge.get("kind")
    if kind == ANIM_EDGE_FINISH:
        return ("finish",)
    if kind in ANIM_ACTION_SOURCES:
        return (kind, str(getattr(edge.get("transition"),
                                  "gk_cond_action", "") or ""))
    return None


def anim_tree_graph(tree):
    """把一张动画树解析成状态图。

    返回一个字典:
        states     [状态节点...]          按画布顺序
        edges      [边...]                见 _make_edge
        entry      入口状态 或 None
        orphans    [走不到的状态...]
        by_name    {状态名: 状态节点}
        problems   [人话问题...]          导出时逐条变成告警

    每条边是一个字典:
        from / to   状态节点(两端都在树里才成立)
        transition  『动画_转场』节点,直连时为 None
        kind        pin / finish / ACTION_*
        order       优先级排序键(画布上谁在上面谁先判)
        pin_used    『条件』引脚上到底有没有接线
        label       给告警用的短名
    """
    states = anim_state_nodes(tree)
    transitions = [n for n in (getattr(tree, "nodes", ()) or ())
                   if getattr(n, "bl_idname", "") == ANIM_TRANSITION_ID]
    problems = []

    # ---- 状态名:必须唯一、必须有 ----
    by_name = {}
    for state in states:
        name = anim_state_name(state)
        if not name:
            problems.append(
                f"状态『{getattr(state, 'name', '?')}』还没有名字 —— "
                f"在它上面选一个『动画』(状态名就是动画名),或者手填一个")
            continue
        if name in by_name:
            problems.append(
                f"有两个状态都叫『{name}』—— 状态机按名字判断该播谁,"
                f"重名会让其中一个永远进不去。给它们选不同的动作")
            continue
        by_name[name] = state

    # ---- 边(1):经过『动画_转场』的正规写法 ----
    edges = []
    for trans in transitions:
        label = getattr(trans, "name", "?")
        ups = [n for n in link_sources(trans, "上游")
               if getattr(n, "bl_idname", "") == ANIM_STATE_ID]
        dsts = [n for n in link_targets(trans, "转到")
                if getattr(n, "bl_idname", "") == ANIM_STATE_ID]
        other_targets = [n for n in link_targets(trans, "转到")
                         if getattr(n, "bl_idname", "") != ANIM_STATE_ID]
        # 备用下拉(用户确认要的:连线忘了也能用)
        fallback_from = str(getattr(trans, "gk_from_state", "") or "").strip()
        fallback_to = str(getattr(trans, "gk_to_state", "") or "").strip()
        source = ups[0] if ups else by_name.get(fallback_from)
        target = dsts[0] if dsts else by_name.get(fallback_to)

        if not transition_is_used(trans):
            continue          # 刚拖进来还没配,不刷告警

        if source is None:
            other_sources = [n for n in link_sources(trans, "上游")
                             if getattr(n, "bl_idname", "") != ANIM_STATE_ID]
            if other_sources:
                # 接了线,但接的不是状态 —— 说清楚接的是什么,
                # 不然用户会觉得"我明明接了线啊"
                problems.append(
                    f"转场『{label}』的『上游』接的是"
                    f"『{getattr(other_sources[0], 'bl_label', '?')}』,"
                    f"不是『动画_状态』—— 它应该接上一个状态的『转到』")
            else:
                hint = (f"『从状态』选的是『{fallback_from}』,这张树里没有这个状态"
                        if fallback_from else
                        "把上一个状态的『转到』接到它的『上游』,"
                        "或者在它上面把『从状态』选上")
                problems.append(
                    f"转场『{label}』不知道**从哪个状态切过来** —— {hint}")
        if target is None:
            if other_targets:
                problems.append(
                    f"转场『{label}』的『转到』接的是"
                    f"『{getattr(other_targets[0], 'bl_label', '?')}』,"
                    f"不是『动画_状态』—— 它应该接下一个状态的『进入』")
            else:
                hint = (f"『到状态』选的是『{fallback_to}』,这张树里没有这个状态"
                        if fallback_to else
                        "把它连到下一个状态的『进入』,或者在它上面把『到状态』选上")
                problems.append(f"转场『{label}』不知道**切到哪个状态** —— {hint}")

        source_kind = str(getattr(trans, "gk_cond_source", "PIN"))
        pin_used = bool(link_sources(trans, "条件"))
        if source_kind == "PIN" and not pin_used:
            problems.append(
                f"转场『{label}』没有条件:『条件来源』是『自己写』,但"
                f"『条件』引脚空着 —— 这条规则不会生效。"
                f"接一个判断节点上去,或者把『条件来源』改成"
                f"『上游动画播完』/『选输入』")
        if source_kind in ANIM_ACTION_SOURCES and pin_used:
            problems.append(
                f"转场『{label}』既接了『条件』引脚,又把『条件来源』选成了输入 —— "
                f"**以『条件来源』为准**,接的那根线被忽略了。"
                f"想用接线的判断就把『条件来源』改回『自己写』")
        if source_kind in ANIM_ACTION_SOURCES and not str(
                getattr(trans, "gk_cond_action", "") or "").strip():
            problems.append(f"转场『{label}』选了『选输入』但没填输入动作名")

        if source is None or target is None:
            continue
        edges.append({
            "from": source, "to": target, "transition": trans,
            "kind": (ANIM_EDGE_FINISH if source_kind == "FINISH"
                     else source_kind if source_kind in ANIM_ACTION_SOURCES
                     else ANIM_EDGE_PIN),
            "order": anim_layout_key(trans),
            "pin_used": pin_used,
            "label": label,
        })

    # ---- 边(2):状态的『转到』直接连到另一个状态 ----
    # 用户实际就是这么连的(见文件头那四条)。语义:"这个动画播完就切过去",
    # 也就是 Godot 自己的 AnimationNodeStateMachine 里的 auto-advance。
    exits = [n for n in (getattr(tree, "nodes", ()) or ())
             if getattr(n, "bl_idname", "") == ANIM_EXIT_ID]
    for state in states:
        for target in link_targets(state, "转到"):
            kind_id = getattr(target, "bl_idname", "")
            if kind_id == ANIM_TRANSITION_ID:
                continue                     # 正规写法,上面处理过了
            if kind_id == ANIM_EXIT_ID:
                # 转到『动画_结束』= 这段动画演完了(见下面的 finish 边)
                edges.append({
                    "from": state, "to": None, "transition": None,
                    "kind": ANIM_EDGE_EXIT, "order": anim_layout_key(target),
                    "pin_used": False,
                    "label": f"{anim_state_name(state)}→结束",
                    "exit": target,
                })
                continue
            if kind_id != ANIM_STATE_ID:
                problems.append(
                    f"状态『{anim_state_name(state) or state.name}』的『转到』接的是"
                    f"『{getattr(target, 'bl_label', '?')}』—— 它只能接"
                    f"『动画_转场』、另一个『动画_状态』,或者『动画_结束』。"
                    f"想在这个状态里**执行语句**(设变量、播音效…),"
                    f"请用它的『进入时』/『离开时』出口")
                continue
            edges.append({
                "from": state, "to": target, "transition": None,
                "kind": ANIM_EDGE_FINISH, "order": anim_layout_key(target),
                "pin_used": False,
                "label": f"{anim_state_name(state)}→{anim_state_name(target)}(直连)",
            })

    # ---- 开始口:显式的起点 + 自定义的开始条件(用户 2026-10-03)----
    # 有『动画_开始』节点时,**它连的那个状态才是起始状态** ——
    # 不用再靠"谁没被指到"去猜(那张图一大就猜不准)。
    entry_nodes = [n for n in (getattr(tree, "nodes", ()) or ())
                   if getattr(n, "bl_idname", "") == ANIM_ENTRY_ID]
    explicit_entry = None
    start_node = entry_nodes[0] if entry_nodes else None
    if start_node is not None:
        for target in link_targets(start_node, "开始"):
            if getattr(target, "bl_idname", "") == ANIM_STATE_ID:
                explicit_entry = target
                break
        if explicit_entry is None:
            problems.append(
                "『动画_开始』还没有连到任何状态 —— 它的『开始』要接到"
                "第一个状态的『进入』,否则这张图不知道从哪儿开始")

    # ---- 入口 ----
    # 1) 有『动画_开始』节点 -> **它连的那个状态就是起始状态**(显式,不用猜)
    # 2) 没放开始口 -> 退回老规矩:没有任何一条边指向它的状态(照 UE5 的 Entry)
    # 比较一律走 node_key() —— 不能用 id()/is,见 node_key 的说明。
    targeted = {node_key(edge["to"]) for edge in edges if edge["to"] is not None}
    named = [s for s in states if anim_state_name(s)]
    entries = [s for s in named if node_key(s) not in targeted]
    if explicit_entry is not None and anim_state_name(explicit_entry):
        entry = explicit_entry
    else:
        entry = entries[0] if entries else (named[0] if named else None)

    # ---- 孤立状态:从入口走不到的那些 ----
    orphans = []
    if entry is not None:
        reachable = {node_key(entry)}
        stack = [entry]
        while stack:
            current = stack.pop()
            current_key = node_key(current)
            for edge in edges:
                if node_key(edge["from"]) != current_key:
                    continue
                if edge["to"] is None:
                    continue          # 指向『动画_结束』:链到头了
                target_key = node_key(edge["to"])
                if target_key in reachable:
                    continue
                reachable.add(target_key)
                stack.append(edge["to"])
        orphans = [s for s in named if node_key(s) not in reachable]
    if orphans:
        names = "、".join(anim_state_name(s) for s in orphans[:4])
        problems.append(
            f"有 {len(orphans)} 个状态是**孤立**的(从起始状态走不到):({names})"
            f"—— 它们不会出现在导出的状态机里。"
            f"用『动画_转场』把它们连起来")

    # ---- 动画树里的『动画_设变量』:只有接在状态的『进入时』/『离开时』
    #      后面才会被执行 ----
    # 动画树本身只算条件(一张状态图 + 每条边的表达式),不会沿执行流走语句。
    # 所以『动画_设变量』必须**挂在状态的进入时/离开时上**才有意义 ——
    # 用户第一次用它时几乎一定会踩(蓝图里 Set 到处都能用):他把状态节点的
    # 『转到』直接连到了设变量上,而那条线是"去下一个状态"的意思。
    live = set()
    stack = []
    for state in states:
        for pin_name in ("进入时", "离开时"):
            stack.extend(link_targets(state, pin_name))
    while stack:
        current = stack.pop()
        key = node_key(current)
        if key in live:
            continue
        live.add(key)
        for socket in (getattr(current, "outputs", ()) or ()):
            for link in (getattr(socket, "links", ()) or ()):
                target = getattr(link, "to_node", None)
                if target is not None:
                    stack.append(target)
    setters = [n for n in (getattr(tree, "nodes", ()) or ())
               if getattr(n, "bl_idname", "") == "GKNodeAnimSetVar"]
    dead = [n for n in setters if node_key(n) not in live]
    if dead:
        names = "、".join(getattr(n, "name", "?") for n in dead[:3])
        problems.append(
            f"动画树里的『动画_设变量』({names})**不会被执行**:它没有接在"
            f"状态的『进入时』/『离开时』出口上。两条路可选:"
            f"(1) 把设变量接到状态节点的『进入时』/『离开时』后面;"
            f"(2) 在游戏逻辑图里用『变量_设置』(两张图编译成同一个脚本,"
            f"变量是同一个)")

    for state in named:
        outgoing = [e for e in edges
                    if node_key(e["from"]) == node_key(state)]
        if not outgoing:
            continue
        # 同一个状态里"条件完全一样"的两条转移:第二条永远不会被用到。
        # 只比**能确定相同**的条件(播完 / 同一个输入动作)—— 引脚上的
        # 表达式不好在纯拓扑这一层比较,而且两条不同的判断去同一个状态
        # 是合法的(第二条是 elif 的兜底),不能误报。
        seen = set()
        for edge in outgoing:
            key = _edge_condition_key(edge)
            if key is None:
                continue
            if key in seen:
                problems.append(
                    f"状态『{anim_state_name(state)}』有两条一模一样的转移"
                    f"(都去『{anim_state_name(edge['to'])}』,条件也一样)"
                    f"—— 后面那条永远不会被用到")
            seen.add(key)

    edges.sort(key=lambda e: (anim_layout_key(e["from"]), e["order"]))
    return {
        "states": states,
        "edges": edges,
        "entry": entry,
        "orphans": orphans,
        "by_name": by_name,
        "problems": problems,
        "start": start_node,          # 『动画_开始』节点(没有就是 None)
        "exit": exits[0] if exits else None,
        "explicit_entry": explicit_entry,
    }


def anim_edge_label(edge):
    """一条边在面板上怎么念 —— 给用户看的短句。"""
    kind = edge.get("kind")
    if kind == ANIM_EDGE_EXIT:
        return "走到结束口"
    if kind == ANIM_EDGE_FINISH:
        return "播完就切"
    trans = edge.get("transition")
    if kind == ANIM_EDGE_PIN:
        return "条件:接引脚"
    if kind in ANIM_ACTION_SOURCES:
        action = str(getattr(trans, "gk_cond_action", "") or "")
        label = {"ACTION_PRESSED": "按住", "ACTION_JUST": "刚按下",
                 "ACTION_RELEASED": "松开"}.get(kind, kind)
        return f"条件:{label} {action or '(没填动作)'}"
    return "条件:?"


def short_problem(problem, limit=26):
    """把一条问题压成一行画得下的短句。

    `layout.label` 放不下就把话截断(用户看到的是半句话),所以这里
    取"破折号之前"的那一段 —— 我们写问题时都是
    "哪儿错了 —— 怎么修" 的结构,前半段正好是结论。

    limit 按用处给:节点体上很窄(26),侧栏宽一些(44)。
    """
    text = str(problem or "").strip()
    for separator in ("——", " -- ", "("):
        head, sep, _tail = text.partition(separator)
        if sep and head.strip():
            text = head.strip()
            break
    if limit and len(text) > limit:
        text = text[:limit - 1] + "…"
    return text


def _action_slot_names(action):
    """取一个 Action 的 slot 名字列表(拿不到就返回空)。"""
    slots = getattr(action, "slots", None)
    if not slots:
        return []
    out = []
    for slot in slots:
        name = getattr(slot, "name_display", None) or getattr(slot, "name", "")
        if name:
            out.append(str(name))
    return out


def action_belongs_to(action, obj):
    """这个动作是不是"可能属于"这个物体。用于给下拉列表排序/标注。

    判据按可靠性排序:
      1. slot 名字里含物体名(最强 —— Blender 自己就是这么记的)
      2. 动作的 id_root 和物体类型对得上
    """
    if action is None or obj is None:
        return False
    obj_name = str(getattr(obj, "name", ""))
    for slot_name in _action_slot_names(action):
        if obj_name and obj_name in slot_name:
            return True
    root = str(getattr(action, "id_root", "") or "")
    allowed = _ACTION_ROOT_TYPES.get(root, ())
    return str(getattr(obj, "type", "")) in allowed


def actions_on_object(obj):
    """**确凿**挂在这个物体上的动作(当前激活的 + NLA 里的 + 形态键的)。

    这是"一定对"的那一批,优先列给用户。
    """
    found = []
    if obj is None:
        return found

    def _add(action):
        if action is not None and action not in found:
            found.append(action)

    anim = getattr(obj, "animation_data", None)
    if anim is not None:
        _add(getattr(anim, "action", None))
        for track in getattr(anim, "nla_tracks", ()) or ():
            for strip in getattr(track, "strips", ()) or ():
                _add(getattr(strip, "action", None))

    # 形态键动画挂在 mesh data 的 shape_keys 上,不在物体自己的
    # animation_data 里 —— 漏掉这里用户的"形态键动作"就搜不到。
    data = getattr(obj, "data", None)
    shape_keys = getattr(data, "shape_keys", None)
    if shape_keys is not None:
        key_anim = getattr(shape_keys, "animation_data", None)
        if key_anim is not None:
            _add(getattr(key_anim, "action", None))
    return found


def discover_actions(obj=None):
    """给下拉列表用的动作清单:(标识, 显示名, 说明) 三元组序列。

    obj 给了的话,把"属于它"的动作排在前面并标上 ★。

    ── 标识为什么用 `名字` 而不是别的 ──────────────────────────────
    导出的 GDScript 里最终要的是**动画名**(Godot 的
    `AnimationPlayer.has_animation("骨骼动作")`)。所以这里直接用
    Action 的名字当枚举标识,节点上选什么、生成什么,一一对应,
    中间不做任何转换 —— 少一层映射就少一类 bug。
    """
    try:
        import bpy
    except Exception:
        return []

    # ── 测试桩里的 bpy 是个空壳(没有 .data)────────────────────────
    # 直接用 bpy.data.actions 会 AttributeError。离线测试跑的就是这条路,
    # 所以这里必须先确认 .data 真的存在 —— 否则"动作发现"会让整个
    # 离线测试台崩掉,而它本该只是拿不到数据而已。
    data = getattr(bpy, "data", None)
    if data is None:
        return []
    all_actions = getattr(data, "actions", None)
    if all_actions is None:
        return []

    owned = []
    if obj is not None:
        owned = [a.name for a in actions_on_object(obj)]

    items = []
    for action in all_actions:
        name = str(action.name)
        root = str(getattr(action, "id_root", "") or "")
        kind = _ACTION_KIND_LABEL.get(root, root or "?")
        slot_names = _action_slot_names(action)
        category = action_category(action)
        owned_mark = "★ " if name in owned else ""
        # 分类摆在最前面 —— 用户要的"分类动画"就是靠这一栏认出来的
        detail = f"{category} · {kind}" if category else kind
        if slot_names:
            detail += f" · {slot_names[0]}"
        items.append((name, f"{owned_mark}{name}  ({detail})", name, category))

    # 排序:属于当前物体的排最前 -> 再按分类聚一起 -> 最后按名字。
    # 没打分类标签的排在最后(它们在列表里是"待整理"的一批)。
    order = {name: index for index, name in enumerate(ANIM_CATEGORY_NAMES)}
    items.sort(key=lambda item: (0 if item[0] in owned else 1,
                                 order.get(item[3], len(order)), item[1]))
    return [(item[0], item[1], item[2]) for item in items]


def check_action_target(node, obj):
    """检查"这个动作挂在这个物体上对不对"。返回一条警告文本或 None。

    ── 回答用户的问题:"如果应用动画物体错误了会怎么样" ─────────────
    **以前是静默失败** —— 导出的脚本在 Godot 里跑,AnimationPlayer
    找不到那个动画名,只在控制台打一条警告,画面看不出问题,
    用户根本不知道哪里错了。

    现在导出时就能发现。判据:
      * `id_root` 是 `KEY`(形态键动画)-> 必须挂在网格上
      * `id_root` 是 `OBJECT` -> 挂在骨架或物体上
      * 动作的 slot 名字里如果有别的物体名 -> 大概率选错了

    返回 None 表示没问题。
    """
    action = getattr(node, "gk_anim_action", None)
    if action is None or obj is None:
        return None

    obj_type = str(getattr(obj, "type", ""))
    root = str(getattr(action, "id_root", "") or "")
    allowed = _ACTION_ROOT_TYPES.get(root)

    if allowed and obj_type not in allowed:
        want = "/".join(allowed)
        return (f"节点『{node.bl_label}』选的动画『{action.name}』是 "
                f"{_ACTION_KIND_LABEL.get(root, root)} 动画,"
                f"应该挂在 {want} 上,但现在挂的是 "
                f"{obj.name}({obj_type})—— 播放时会找不到动画")

    # slot 名字里出现了**别的**物体名 -> 多半是选串了
    own_name = str(getattr(obj, "name", ""))
    for slot_name in _action_slot_names(action):
        if own_name and own_name in slot_name:
            return None      # 明确属于这个物体,没问题
    others = [n for n in _action_slot_names(action) if n and n != "Key"]
    if others and own_name and own_name not in others[0]:
        return (f"节点『{node.bl_label}』选的动画『{action.name}』"
                f"看起来是属于『{others[0]}』的,不是『{own_name}』"
                f"—— 确认一下是不是选错了")
    return None


def action_enum_items(_self=None, _context=None):
    """动态枚举回调:列出场景里所有动作。

    没绑定到具体物体 —— Blender 的 EnumProperty 回调拿不到节点实例,
    所以这里列**全部**,靠标签上的 ★ 和"(类型 · 归属)"帮用户分辨。
    导出时还会再校验一次(见 gk_anim_name 的检查)。
    """
    entries = discover_actions(None)
    if not entries:
        return [("", "(这个文件里还没有任何动画)", "")]
    return entries
# ==========================================================================
# AI 图(『游戏AI』)—— 第四十一轮
# ==========================================================================
# 用户原话:"添加游戏AI逻辑编辑器,这样分类开不会后期混乱"。
# 交互完全照动画树(状态卡 + 转场卡 + 起始状态),区别只在状态里干的事:
# 动画状态播动画,AI 状态**做行为**(巡逻 / 追击 / 逃跑…)。
#
#     玩法图(游戏逻辑)              AI 图(游戏AI)
#     ┌───────────────┐           ┌──────────────────────────┐
#     │ 每当_准备      │           │ [AI_状态: 巡逻] 行为=巡逻   │
#     │  AI_启用 ──────┼── 驱动 ──>│   ├─转场:看见玩家─> [追击]  │
#     └───────────────┘           │   └─转场:丢失玩家─> [巡逻]  │
#                                 └──────────────────────────┘
AI_TREE_ID = "GKGameAiTree"
AI_STATE_ID = "GKNodeAiState"
AI_TRANSITION_ID = "GKNodeAiTransition"
# 第六十九轮的三件:显式入口 / 对外布尔输出 / 条件组合
AI_START_ID = "GKNodeAiStart"
AI_OUTPUT_ID = "GKNodeAiOutput"
AI_LOGIC_ID = "GKNodeAiLogic"


def ai_output_nodes(tree):
    """AI 图里所有『AI_输出』节点(按画布顺序)。

    纯读 `tree.nodes` 的 bl_idname —— 离线桩树也能喂进来测。
    """
    return [node for node in (getattr(tree, "nodes", ()) or ())
            if getattr(node, "bl_idname", "") == AI_OUTPUT_ID]


# 行为枚举:六个行为覆盖"小怪 / NPC"最常见的全部动作。
# 这些行为都生成 `velocity + move_and_slide()`(和『移动_朝向目标』同一套),
# 所以『AI_启用』的『作用在谁』必须是 CharacterBody(3D 角色 / 2D 角色)。
AI_BEHAVIORS = (
    ("STAND", "待机", "站着不动(站岗 / 等条件成立)"),
    ("PATROL", "巡逻", "在『巡逻点 A』和『巡逻点 B』之间来回走"),
    ("CHASE", "追击", "朝『目标』走过去(追人的敌人)"),
    ("FLEE", "逃跑", "目标近到『停在多远』以内就往反方向跑"),
    ("FOLLOW", "跟随", "跟着『目标』走,保持『停在多远』的距离(宠物)"),
    ("RETURN", "返回", "走回『巡逻点 A』(追丢了回岗位)"),
    # 第四十一轮的听觉配套:听到动静过去看看(恐怖游戏最常见的那个"追声")
    ("INVESTIGATE", "去查看", "朝**最近听到的噪音点**走过去(听声辨位)"),
    # 第七十九轮:攻击。插件**不建血量系统** —— "打一下"具体做什么由用户
    # 接在状态的『进入时』出口上(设变量 / 播音效 / 发信号),伤害那套在
    # 玩法图里拼。这样两种做法都行:轻微碰撞的推挤,或者回合制式扣血。
    ("ATTACK", "攻击", "走到『目标』身边(进『攻击距离』就停),"
                       "每『冷却』秒“打一下” —— “打”什么由状态的"
                       "『进入时』出口决定"),
)

# 朝向:走的时候头朝哪
AI_FACINGS = (
    ("GOAL", "朝目标", "看着目标(巡逻 / 返回时看下一个巡逻点)"),
    ("MOVE", "朝移动方向", "头跟着路转 —— 绕障碍时比朝目标自然"),
    ("KEEP", "不转", "自己另外用『移动_设置朝向』控"),
)

# 『AI_转场』的条件来源:自己写 / 看见目标 / 丢失目标 / 听见声音 / 安静下来
# ── 为什么把听觉也做成"内置条件"(和看见/丢失同一套)────────────────
# 恐怖游戏最常写的两条规则是"巡逻 -> 听见动静 -> 去查看"和"去查看 ->
# 没动静了 -> 回巡逻"。为了这两条去拉『AI_听见』+ 连线,状态一多整张图
# 全是线;内置成条件来源后,填个"听觉距离"就能用。
AI_COND_SOURCES = ("SEE", "LOST", "HEARD", "CALM")

# 需要**指定目标**的条件来源(听觉的两个不需要 —— 噪音不来自某个特定目标)
AI_GOAL_COND_SOURCES = ("SEE", "LOST")

# 需要**听觉参数**(听觉距离 / 记忆秒数)的条件来源
AI_HEAR_COND_SOURCES = ("HEARD", "CALM")


def ai_tree_items():
    """给『选…』按钮用的 AI 图清单:(标识, 显示名, 说明)。

    和 `animation_tree_items` / `ui_design_tree_items` 同一套:
    标识就是树名(空串 = 自动用第一张)。
    """
    auto = ("", "自动(用文件里第一张)", "留空就用这张")
    try:
        import bpy
    except Exception:                                     # noqa: BLE001
        return [auto]
    data = getattr(bpy, "data", None)
    groups = getattr(data, "node_groups", None) if data is not None else None
    if groups is None:
        return [auto]
    trees = []
    for group in groups:
        if getattr(group, "bl_idname", "") != AI_TREE_ID:
            continue
        count = len([n for n in (getattr(group, "nodes", ()) or ())
                     if getattr(n, "bl_idname", "") == AI_STATE_ID])
        trees.append((str(group.name), f"{group.name}  ({count} 个状态)", "这张 AI 图"))
    return [auto] + trees


def ai_tree_names():
    """文件里所有 AI 图的名字(不含"自动"那一项)。"""
    return [item[0] for item in ai_tree_items() if item[0]]


def find_ai_tree(name=""):
    """按名字找一张 AI 图;名字为空就用文件里第一张(找不到返回 None)。

    面板、节点上的预览、代码生成三处都要问"这个节点会用哪张图",
    判断必须只有一份 —— 和 `find_animation_tree` 同一个理由。
    """
    try:
        import bpy
    except Exception:                                     # noqa: BLE001
        return None
    data = getattr(bpy, "data", None)
    groups = getattr(data, "node_groups", None)
    if groups is None:
        return None
    wanted = str(name or "")
    first = None
    for group in groups:
        if getattr(group, "bl_idname", "") != AI_TREE_ID:
            continue
        if first is None:
            first = group
        if wanted and str(getattr(group, "name", "")) == wanted:
            return group
    return first if not wanted else None


def ai_state_name(node):
    """一个 AI 状态叫什么(手填的『状态名』)。"""
    if node is None:
        return ""
    return str(getattr(node, "gk_state_name", "") or "").strip()


def ai_behavior_label(node):
    """状态的行为显示名(『巡逻』『追击』…),给告警和面板用。"""
    behavior = str(getattr(node, "gk_behavior", "PATROL") or "PATROL")
    for ident, label, _desc in AI_BEHAVIORS:
        if ident == behavior:
            return label
    return behavior


def ai_state_nodes(tree):
    """树里所有『AI_状态』,按画布顺序(上到下、左到右)排。"""
    nodes = [n for n in (getattr(tree, "nodes", ()) or ())
             if getattr(n, "bl_idname", "") == AI_STATE_ID]
    nodes.sort(key=anim_layout_key)
    return nodes


def ai_state_items(tree):
    """给『选…』按钮用的状态清单:(状态名, 显示名, 说明)。

    和 `animation_tree_states` 同一套 —— 『AI_转场』的『从』/『到』
    下拉用它(忘了连线时的兜底)。
    """
    items = []
    for state in ai_state_nodes(tree):
        name = ai_state_name(state)
        if not name:
            items.append(("", f"{getattr(state, 'name', '?')}(还没设名字)",
                          "先在节点上填一个『状态名』"))
            continue
        items.append((name, f"{name} · {ai_behavior_label(state)}",
                      f"节点名 {getattr(state, 'name', '?')}"))
    return items


def ai_transition_is_used(trans):
    """这条转场用户到底动过没有?

    刚拖进来、什么都没接的『AI_转场』不该刷一堆告警 ——
    但接了线或选了条件的就说明用户是想用它,那时缺什么必须说清
    (照 `transition_is_used`)。
    """
    for socket_name in ("上游", "条件"):
        if link_sources(trans, socket_name):
            return True
    if link_targets(trans, "转到"):
        return True
    if str(getattr(trans, "gk_from_state", "") or "").strip():
        return True
    if str(getattr(trans, "gk_to_state", "") or "").strip():
        return True
    if str(getattr(trans, "gk_cond_source", "PIN")) != "PIN":
        return True
    return False


def ai_edge_label(edge):
    """一条边在面板上怎么念 —— 给用户看的短句。"""
    kind = edge.get("kind")
    if kind == "SEE":
        return "条件:看见目标"
    if kind == "LOST":
        return "条件:丢失目标"
    if kind == "HEARD":
        return "条件:听见动静"
    if kind == "CALM":
        return "条件:安静下来"
    if kind == "pin":
        return "条件:接引脚"
    return "条件:?"


def ai_tree_graph(tree):
    """把一张 AI 图解析成状态图(和 `anim_tree_graph` 同一套形状)。

    返回一个字典:
        states     [状态节点...]          按画布顺序
        edges      [边...]
        entry      起始状态 或 None(没有任何边指向它的那个)
        orphans    [走不到的状态...]
        by_name    {状态名: 状态节点}
        problems   [人话问题...]          导出时逐条变成告警

    每条边:
        from / to   状态节点
        transition  『AI_转场』节点
        kind        pin / SEE / LOST
        order       优先级排序键(画布上谁在上面谁先判)
        pin_used    『条件』引脚上到底有没有接线
        label       给告警用的短名

    ── 和动画树唯一的差别:直连不算数 ──────────────────────────────
    动画状态直连 = "动画播完就切",有内在时序;AI 状态直连**没有条件**,
    语义只能是"一到就切走" —— 那没有任何用处,所以这里把它报成问题
    (说清该用『AI_转场』),而不是生成一条废边。
    """
    states = ai_state_nodes(tree)
    transitions = [n for n in (getattr(tree, "nodes", ()) or ())
                   if getattr(n, "bl_idname", "") == AI_TRANSITION_ID]
    problems = []

    # ---- 状态名:必须唯一、必须有 ----
    by_name = {}
    for state in states:
        name = ai_state_name(state)
        if not name:
            problems.append(
                f"状态『{getattr(state, 'name', '?')}』还没有名字 —— "
                f"在它上面填一个『状态名』(比如 巡逻 / 追击)")
            continue
        if name in by_name:
            problems.append(
                f"有两个状态都叫『{name}』—— 状态机按名字判断在哪个状态,"
                f"重名会让其中一个永远进不去。给它们取不同的名字")
            continue
        by_name[name] = state

    # ---- 行为参数齐不齐(缺了先说,免得导出后"站着不动"查半天)----
    for state in states:
        name = ai_state_name(state) or getattr(state, "name", "?")
        behavior = str(getattr(state, "gk_behavior", "PATROL") or "PATROL")
        label = ai_behavior_label(state)
        if behavior in ("PATROL", "RETURN") and not str(
                getattr(state, "gk_patrol_a", "") or "").strip():
            problems.append(
                f"状态『{name}』的行为是『{label}』,但没填『巡逻点 A』—— "
                f"它哪儿也不去。在场景里摆两个空物体当巡逻点,再选上")
        # 填了 C / D 时 B 可以是空的(跳着填 = 走 A↔C,D 也同理)
        filled_extra = any(
            str(getattr(state, prop, "") or "").strip()
            for prop in ("gk_patrol_c", "gk_patrol_d"))
        if behavior == "PATROL" and not filled_extra and not str(
                getattr(state, "gk_patrol_b", "") or "").strip():
            problems.append(
                f"状态『{name}』没填『巡逻点 B』—— 只在 A 点待着不算巡逻,"
                f"把它选上(或者把行为改成『返回』/『待机』)")
        if behavior in ("CHASE", "FOLLOW", "FLEE") and not str(
                getattr(state, "gk_goal", "") or "").strip():
            problems.append(
                f"状态『{name}』的行为是『{label}』,但没选『目标』—— "
                f"它不知道追谁 / 逃谁。选上玩家(或要跟随的物体)")
        # 『攻击』要两样:目标(冲谁去)+『进入时』出口(打一下做什么)。
        # 目标缺了是真跑不动;『进入时』缺了则会生成一个"只会贴身站着"的
        # 状态 —— 不说一声的话,用户会以为攻击坏了。
        if behavior == "ATTACK":
            if not str(getattr(state, "gk_goal", "") or "").strip():
                problems.append(
                    f"状态『{name}』的行为是『{label}』,但没选『目标』—— "
                    f"它不知道冲谁去。选上玩家(或要攻击的物体)")
            elif not link_targets(state, "进入时"):
                problems.append(
                    f"状态『{name}』的行为是『攻击』,但『进入时』出口没接"
                    f"任何东西 —— 每『冷却』秒那“打一下”会是空的。"
                    f"在它右边接上『变量_设置』(扣血) / 『音效_播放』 / "
                    f"『信号_发出』")

    # ---- 边:经过『AI_转场』的正规写法 ----
    edges = []
    for trans in transitions:
        label = getattr(trans, "name", "?")
        ups = [n for n in link_sources(trans, "上游")
               if getattr(n, "bl_idname", "") == AI_STATE_ID]
        dsts = [n for n in link_targets(trans, "转到")
                if getattr(n, "bl_idname", "") == AI_STATE_ID]
        other_ups = [n for n in link_sources(trans, "上游")
                     if getattr(n, "bl_idname", "") != AI_STATE_ID]
        other_dsts = [n for n in link_targets(trans, "转到")
                      if getattr(n, "bl_idname", "") != AI_STATE_ID]
        fallback_from = str(getattr(trans, "gk_from_state", "") or "").strip()
        fallback_to = str(getattr(trans, "gk_to_state", "") or "").strip()
        source = ups[0] if ups else by_name.get(fallback_from)
        target = dsts[0] if dsts else by_name.get(fallback_to)

        if not ai_transition_is_used(trans):
            continue          # 刚拖进来还没配,不刷告警

        if source is None:
            if other_ups:
                problems.append(
                    f"转场『{label}』的『上游』接的是"
                    f"『{getattr(other_ups[0], 'bl_label', '?')}』,"
                    f"不是『AI_状态』—— 它应该接上一个状态的『转到』")
            else:
                hint = (f"『从状态』选的是『{fallback_from}』,这张图里没有这个状态"
                        if fallback_from else
                        "把上一个状态的『转到』接到它的『上游』,"
                        "或者在它上面把『从状态』选上")
                problems.append(
                    f"转场『{label}』不知道**从哪个状态切过来** —— {hint}")
        if target is None:
            if other_dsts:
                problems.append(
                    f"转场『{label}』的『转到』接的是"
                    f"『{getattr(other_dsts[0], 'bl_label', '?')}』,"
                    f"不是『AI_状态』—— 它应该接下一个状态的『进入』")
            else:
                hint = (f"『到状态』选的是『{fallback_to}』,这张图里没有这个状态"
                        if fallback_to else
                        "把它连到下一个状态的『进入』,或者在它上面把『到状态』选上")
                problems.append(f"转场『{label}』不知道**切到哪个状态** —— {hint}")

        source_kind = str(getattr(trans, "gk_cond_source", "PIN") or "PIN")
        pin_used = bool(link_sources(trans, "条件"))
        if source_kind == "PIN" and not pin_used:
            problems.append(
                f"转场『{label}』没有条件:『条件来源』是『自己写』,但"
                f"『条件』引脚空着 —— 这条规则不会生效。"
                f"接一个判断节点上去(比如『AI_看得见』/『AI_听见』),"
                f"或者把『条件来源』改成『看见目标』/『丢失目标』/"
                f"『听见动静』/『安静下来』")
        if source_kind in AI_COND_SOURCES and pin_used:
            problems.append(
                f"转场『{label}』既接了『条件』引脚,又把『条件来源』选成了"
                f"内置条件 —— **以『条件来源』为准**,接的那根线被忽略了")
        # ⚠ 只有"看见 / 丢失目标"需要选目标 —— 听觉那两个不需要
        # (噪音不来自某个特定目标)。以前这里对所有非 PIN 来源都要求目标,
        # 加了听觉之后会把合法的听觉转场误报成问题。
        if source_kind in AI_GOAL_COND_SOURCES and not str(
                getattr(trans, "gk_goal", "") or "").strip():
            problems.append(
                f"转场『{label}』选了『看见 / 丢失目标』但没选『目标』—— "
                f"看谁?把玩家选上(或者改用『自己写』接『AI_看得见』)")

        if source is None or target is None:
            continue
        edges.append({
            "from": source, "to": target, "transition": trans,
            "kind": source_kind if source_kind in AI_COND_SOURCES else "pin",
            "order": anim_layout_key(trans),
            "pin_used": pin_used,
            "label": label,
        })

    # ---- 直连:AI 里没有意义(见函数说明),报清怎么改 ----
    for state in states:
        for target in link_targets(state, "转到"):
            kind_id = getattr(target, "bl_idname", "")
            if kind_id == AI_TRANSITION_ID:
                continue
            if kind_id == AI_STATE_ID:
                problems.append(
                    f"状态『{ai_state_name(state) or state.name}』的『转到』"
                    f"**直连**到了另一个状态 —— AI 状态之间请隔一个"
                    f"『AI_转场』(它才有条件;直连没有条件,一到就会切走)")
            else:
                problems.append(
                    f"状态『{ai_state_name(state) or state.name}』的『转到』接的是"
                    f"『{getattr(target, 'bl_label', '?')}』—— "
                    f"它只能接『AI_转场』")

    # ---- 入口(第六十九轮:`AI_开始` 优先,否则还是"没人指向的那个")----
    # 老规矩(没有任何一条边指向它的状态当入口)在**循环图**上直接失效 ——
    # 每个状态都有入边时只能随便挑一个(以前是 named[0]),用户根本没法表达
    # "我要从『追』开始"。『AI_开始』把这件事变成显式的:一根『开始』线指过去。
    starts = [n for n in (getattr(tree, "nodes", ()) or ())
              if getattr(n, "bl_idname", "") == AI_START_ID]
    if len(starts) > 1:
        problems.append(
            f"这张图里有 {len(starts)} 个『AI_开始』—— 起始状态只能有一个,"
            f"留一个(其余的删掉)")
    start_entry = None
    if starts:
        picked = [n for n in link_targets(starts[0], "开始")
                  if getattr(n, "bl_idname", "") == AI_STATE_ID]
        if picked:
            start_entry = picked[0]
        else:
            problems.append(
                f"『AI_开始』的『开始』引脚还没接状态 —— 把它连到第一个状态的"
                f"『进入』上,不然起始状态还是靠猜")
    targeted = {node_key(edge["to"]) for edge in edges if edge["to"] is not None}
    named = [s for s in states if ai_state_name(s)]
    entries = [s for s in named if node_key(s) not in targeted]
    if start_entry is not None:
        entry = start_entry
    else:
        entry = entries[0] if entries else (named[0] if named else None)

    # ---- 孤立状态:从入口走不到的那些 ----
    orphans = []
    if entry is not None:
        reachable = {node_key(entry)}
        stack = [entry]
        while stack:
            current = stack.pop()
            current_key = node_key(current)
            for edge in edges:
                if node_key(edge["from"]) != current_key:
                    continue
                if edge["to"] is None:
                    continue
                target_key = node_key(edge["to"])
                if target_key in reachable:
                    continue
                reachable.add(target_key)
                stack.append(edge["to"])
        orphans = [s for s in named if node_key(s) not in reachable]
    if orphans:
        names = "、".join(ai_state_name(s) for s in orphans[:4])
        problems.append(
            f"有 {len(orphans)} 个状态是**孤立**的(从起始状态走不到):({names})"
            f"—— 它们不会出现在导出的状态机里。用『AI_转场』把它们连起来")

    # ---- 『AI_输出』(第六十九轮):变量名与状态名都得对得上 ----
    # 这两项错一个,玩法图那边就是"永远读到假 / 读不到东西" —— 而且不报错。
    seen_vars = {}
    for output in ai_output_nodes(tree):
        label = getattr(output, "name", "?")
        var = str(getattr(output, "gk_var", "") or "").strip()
        state_name = str(getattr(output, "gk_state_name", "") or "").strip()
        if not var:
            problems.append(
                f"『AI_输出』『{label}』还没填『变量名』—— 玩法图要靠这个名字"
                f"读它(用英文 / 数字 / 下划线,比如 is_chasing)")
        elif var in seen_vars:
            problems.append(
                f"有两个『AI_输出』都用『变量名 = {var}』—— 它们会互相覆盖,"
                f"给其中一个改个名字")
        else:
            seen_vars[var] = output
        if not state_name:
            problems.append(
                f"『AI_输出』『{label}』还没选『状态』—— 它输出的是"
                f"『在不在某个状态』,得先告诉它看哪个状态")
        elif state_name not in by_name:
            problems.append(
                f"『AI_输出』『{label}』选的状态『{state_name}』这张图里没有")

    edges.sort(key=lambda e: (anim_layout_key(e["from"]), e["order"]))
    return {
        "states": states,
        "edges": edges,
        "entry": entry,
        "starts": starts,
        "outputs": ai_output_nodes(tree),
        "orphans": orphans,
        "by_name": by_name,
        "problems": problems,
    }


from ..gk_sockets import ANY, BOOL, COLOR, FLOAT, FLOW, INT, OBJECT, TEXT, VECTOR, VECTOR2

CLASSES = []


def N(cls):
    """登记节点类并物化它的声明式属性。"""
    CLASSES.append(finalize(cls))
    return cls

# ── 环境预设:内置七项(第一百一十三轮;第 123 轮补二次元三档)────────
# 值是 Godot 端语义(Color 三元组 / 能量 / 密度),和场景设置卡的
# env_* 一一对应。bg_mode: 1 = COLOR(纯色), 2 = SKY(程序化天空)。
#
# ── 为什么前三个是"卡通二次元"三档(用户实测反馈)──────────────────
# 用户原话:"现在的环境渲染成了『中世纪』的风格了,但我想要的是卡通二次元"。
# 病根是**亮部被压暗 + 阴影色调偏土**:暗部一旦掉到棕灰,画面立刻变成
# 写实奇幻(中世纪酒馆),而二次元要的是"亮部干净、暗部有色相、对比干脆"。
# 所以这三档的配方和恐怖系**明确分开**:
#   * `amb_e` 抬到 0.65~0.95(恐怖系只有 0.09~0.22)—— 暗部不许死黑;
#   * `shadow_tint` 一律**冷色相**(青蓝 / 蓝紫),不许用暖棕 ——
#     暖棕暗部就是"中世纪"的直接来源;
#   * `sat` 1.10~1.25、`ct` 1.05~1.12 —— 颜色更艳、明暗边界更硬;
#   * `br` 1.05 起 —— 高调(high-key),这是动画赛璐璐的曝光习惯;
#   * `rim` 给一点点(0.12~0.35)—— 轮廓光让角色从背景里"浮"出来,
#     这是日式动画最标志性的一笔。⚠ 别贪多:rim 在 shader 里是
#     **EMISSION 加法项**(见第一百一十七轮的标定注释),0.5 以上整关发灰。
# 恐怖系三项(恐怖夜 / 黄昏 / 浓雾夜)的数值**一个字没动** —— 老工程
# 与恐怖示例的观感不能因为这次调色而变。
ENV_PRESET_BUILTINS = {
    # ── ① 卡通二次元:日间(默认推荐)────────────────────────────────
    # ── 第 124 轮:改成"接近原神那套"的配方(用户:"可以渲染成原神那样的
    #    画面吗,现在的画质感觉上不是很好看(二次元视角下)")────────────
    # 上一版用 SKY 背景 + 环境光 0.90,渲出来**过曝发白**(用户截图里整屏
    # 米白、明暗层次糊掉)。原神那套的观感其实是三件事:
    #   * **背景是干净的淡蓝**(不是白)—— 用纯色背景,别用程序化天空
    #     (天空 + 高环境光会把画面糊成一片白);
    #   * **环境光适中(0.45~0.6)+ 冷色暗部** —— 亮部留得住、暗部有颜色,
    #     明暗才是"两段/三段"的赛璐璐,而不是渐变糊;
    #   * **饱和度 1.2 左右 + 对比 1.08** —— 颜色干净、边界干脆。
    # 另外"原神感"还有一半在**模型与贴图上**(硬边高光、面部专用着色、
    # 描边),那些不是这几个数字能给的;预设只能把"光与色"这一段做对。
    "卡通日间": {
        "bg_mode": 1, "bg": (0.34, 0.48, 0.72),
        "amb": (0.68, 0.78, 0.96), "amb_e": 0.50,
        "fog": False, "fog_c": (0.55, 0.66, 0.85), "fog_d": 0.003,
        "glow": True, "glow_i": 0.35, "glow_b": 0.03,
        "adjust": True, "br": 1.02, "ct": 1.08, "sat": 1.22,
        "shadow_step": 0.06, "shadow_tint": (0.58, 0.66, 0.92),
        "three_tone": 0.55, "rim": 0.20, "specular": 0.0,
    },
    # ── ② 卡通二次元:黄昏(暖光 + 冷影,动画里最常见的"放学后")────
    "卡通黄昏": {
        "bg_mode": 1, "bg": (0.62, 0.42, 0.42),
        "amb": (0.80, 0.62, 0.60), "amb_e": 0.45,
        "fog": True, "fog_c": (0.72, 0.52, 0.50), "fog_d": 0.008,
        "glow": True, "glow_i": 0.60, "glow_b": 0.06,
        "adjust": True, "br": 1.02, "ct": 1.09, "sat": 1.25,
        "shadow_step": 0.08, "shadow_tint": (0.44, 0.44, 0.82),
        "three_tone": 0.50, "rim": 0.25, "specular": 0.0,
    },
    # ── ③ 卡通二次元:夜晚 / 教室(亮部不脏、暗部偏蓝紫)────────────
    "卡通夜晚": {
        "bg_mode": 1, "bg": (0.10, 0.13, 0.26),
        "amb": (0.46, 0.54, 0.86), "amb_e": 0.42,
        "fog": True, "fog_c": (0.24, 0.28, 0.48), "fog_d": 0.008,
        "glow": True, "glow_i": 0.85, "glow_b": 0.07,
        "adjust": True, "br": 1.03, "ct": 1.08, "sat": 1.22,
        "shadow_step": 0.10, "shadow_tint": (0.40, 0.42, 0.78),
        "three_tone": 0.45, "rim": 0.28, "specular": 0.0,
    },
    # ── 以下三项是恐怖系(数值不动,老工程观感不变)─────────────────
    "恐怖夜": {
        "bg_mode": 1, "bg": (0.015, 0.02, 0.045),
        "amb": (0.16, 0.20, 0.34), "amb_e": 0.10,
        "fog": True, "fog_c": (0.09, 0.11, 0.17), "fog_d": 0.055,
        "glow": True, "glow_i": 0.9, "glow_b": 0.12,
        "adjust": True, "br": 0.95, "ct": 1.12, "sat": 0.82,
        "shadow_step": 0.18, "shadow_tint": (0.30, 0.26, 0.48),
        "three_tone": 0.0, "rim": 0.0, "specular": 0.0,
    },
    "明亮白天": {
        "bg_mode": 2, "bg": (0.55, 0.72, 0.95),
        "amb": (0.85, 0.88, 0.95), "amb_e": 0.8,
        "fog": False, "fog_c": (0.75, 0.80, 0.90), "fog_d": 0.005,
        "glow": False, "glow_i": 0.5, "glow_b": 0.05,
        "adjust": False, "br": 1.0, "ct": 1.0, "sat": 1.0,
        "shadow_step": 0.05, "shadow_tint": (0.66, 0.70, 0.90),
        "three_tone": 0.0, "rim": 0.0, "specular": 0.0,
    },
    "黄昏": {
        "bg_mode": 2, "bg": (0.90, 0.55, 0.30),
        "amb": (0.85, 0.60, 0.42), "amb_e": 0.45,
        "fog": True, "fog_c": (0.80, 0.55, 0.38), "fog_d": 0.02,
        "glow": True, "glow_i": 1.1, "glow_b": 0.15,
        "adjust": True, "br": 1.0, "ct": 1.05, "sat": 1.10,
        "shadow_step": 0.10, "shadow_tint": (0.52, 0.32, 0.28),
        "three_tone": 0.0, "rim": 0.0, "specular": 0.0,
    },
    "浓雾夜": {
        "bg_mode": 1, "bg": (0.04, 0.05, 0.07),
        "amb": (0.30, 0.33, 0.40), "amb_e": 0.18,
        "fog": True, "fog_c": (0.13, 0.15, 0.19), "fog_d": 0.12,
        "glow": False, "glow_i": 0.6, "glow_b": 0.08,
        "adjust": True, "br": 0.92, "ct": 1.05, "sat": 0.75,
        "shadow_step": 0.16, "shadow_tint": (0.26, 0.28, 0.34),
        "three_tone": 0.0, "rim": 0.0, "specular": 0.0,
    },
}

# 内置预设名(顺序即下拉顺序;identifier 按它的下标生成)。
# ⚠ **单一定义源**:properties 的下拉、『环境_应用预设』节点的默认值 /
#   提示语都从这里取 —— 以前这三处各写一份中文名单,加一档就要改三处,
#   漏一处就成了"下拉里有、节点说找不到"(第 123 轮收口)。
ENV_PRESET_NAMES = tuple(ENV_PRESET_BUILTINS.keys())
# 「卡通二次元」那三档(给 UI 分组显示用)
ENV_PRESET_ANIME = ("卡通日间", "卡通黄昏", "卡通夜晚")
ENV_PRESET_HORROR = ("恐怖夜", "明亮白天", "黄昏", "浓雾夜")
