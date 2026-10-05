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
from ..gk_sockets import ANY, BOOL, COLOR, FLOAT, FLOW, INT, OBJECT, TEXT, VECTOR, VECTOR2

CLASSES = []


def N(cls):
    """登记节点类并物化它的声明式属性。"""
    CLASSES.append(finalize(cls))
    return cls
