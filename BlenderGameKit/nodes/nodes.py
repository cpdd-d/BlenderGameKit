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
    except Exception:
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


# ==========================================================================
# 事件
# ==========================================================================
@N
class GKNodeOnReady(B.GKEventNode):
    bl_idname = "GKNodeOnReady"
    bl_label = "每当_准备"
    bl_icon = "PLAY"
    gk_category = "事件"
    gk_doc = "游戏开始时执行一次，对应 Godot 的 _ready()"
    gk_outputs = (S.pin(FLOW, "执行"),)



@N
class GKNodeOnProcess(B.GKEventNode):
    bl_idname = "GKNodeOnProcess"
    bl_label = "每当_每帧"
    bl_icon = "TIME"
    gk_category = "事件"
    gk_doc = "每一帧执行，对应 Godot 的 _process(delta)"
    gk_prop_defs = (("gk_order", "int", 0, "排序", "多个每帧事件时的执行顺序"),)
    gk_outputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "帧间隔", None),)


@N
class GKNodeOnPhysicsProcess(B.GKEventNode):
    bl_idname = "GKNodeOnPhysicsProcess"
    bl_label = "每当_物理帧"
    bl_icon = "PHYSICS"
    gk_category = "事件"
    gk_doc = "固定频率执行(默认每秒 60 次)，对应 _physics_process(delta)"
    gk_prop_defs = (("gk_order", "int", 0, "排序", "多个物理帧事件时的执行顺序"),)
    gk_outputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "帧间隔", None),)


@N
class GKNodeOnInput(B.GKEventNode):
    bl_idname = "GKNodeOnInput"
    bl_label = "每当_输入"
    bl_icon = "HAND"
    gk_category = "事件"
    gk_doc = "按键 / 鼠标事件，对应 Godot 的 _input(event)"
    gk_outputs = (S.pin(FLOW, "执行"), S.pin(ANY, "输入事件", None),)


@N
class GKNodeFunction(B.GKEventNode):
    bl_idname = "GKNodeFunction"
    bl_label = "自定义函数"
    bl_icon = "FILE_SCRIPT"
    gk_category = "事件"
    gk_doc = "定义一个可被其它节点调用、也能接信号的函数"
    gk_prop_defs = (("gk_func_name", "string", "my_function", "函数名", "生成的 func 名称"),)
    gk_outputs = (S.pin(FLOW, "执行"),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_func_name", text="")


@N
class GKNodeCallFunction(B.GKStatementNode):
    bl_idname = "GKNodeCallFunction"
    bl_label = "调用_函数"
    bl_icon = "FILE_REFRESH"
    gk_category = "事件"
    gk_doc = "调用函数(本脚本的自定义函数，或目标节点上的方法)"
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_func_name", "string", "my_function", "函数名", "要调用的方法名"),
        ("gk_target", "node", TARGET_SELF, "目标", "留空表示本节点"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_func_name", text="")
        row.prop(self, "gk_target", text="")


# ==========================================================================
# 流程控制
# ==========================================================================
@N
class GKNodeBranch(B.GKStatementNode):
    bl_idname = "GKNodeBranch"
    bl_label = "如果_否则"
    bl_icon = "ARROW_LEFTRIGHT"
    gk_category = "流程"
    gk_doc = "条件分支:条件为真走『是』出口，为假走『否』出口"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(BOOL, "条件", False),)
    gk_outputs = (S.pin(FLOW, "是"), S.pin(FLOW, "否"),)


@N
class GKNodeDoOnce(B.GKStatementNode):
    bl_idname = "GKNodeDoOnce"
    bl_label = "只执行一次"
    bl_icon = "LOCKED"
    gk_category = "流程"
    gk_doc = "第一次到达时执行，之后自动跳过"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(BOOL, "重置", False),)
    gk_outputs = (S.pin(FLOW, "首次"),)



@N
class GKNodeSequence(B.GKStatementNode):
    bl_idname = "GKNodeSequence"
    bl_label = "序列"
    bl_icon = "LINENUMBERS_ON"
    gk_category = "流程"
    gk_doc = "依次执行多个出口(出口数量可调)"
    gk_prop_defs = (("gk_step_count", "int", 3, "出口数量", "1~8 个出口"),)
    # 出口引脚数量由 gk_step_count 决定，在 build_sockets 里动态生成
    gk_inputs = (S.pin(FLOW, "执行"),)

    gk_outputs = (S.pin(FLOW, "1"), S.pin(FLOW, "2"), S.pin(FLOW, "3"),)

    def build_sockets(self):
        count = max(1, min(8, int(getattr(self, "gk_step_count", 3) or 3)))
        self.inputs.clear()
        for spec in self.gk_inputs:
            socket = self.inputs.new(spec["kind"][0], spec["name"])
            if spec["default"] is not None:
                socket.default_value = spec["default"]
        self.outputs.clear()
        for index in range(count):
            self.outputs.new(S.FLOW[0], str(index + 1))
        self.gk_socket_cache = (len(self.inputs), len(self.outputs))

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_step_count", text="出口")


@N
class GKNodeSwitch(B.GKStatementNode):
    bl_idname = "GKNodeSwitch"
    bl_label = "多路分支"
    bl_icon = "SORT_ASC"
    gk_category = "流程"
    gk_doc = ("按一个整数走不同出口。出口数量可调(1~12),"
              "改数字引脚会立刻长出来 —— 这就是蓝图那种'自定义分支数'的节点。"
              "值不匹配任何一条时走『默认』")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(INT, "值", 0),)
    # 出口在 build_sockets 里按 gk_case_count 动态生成,这里只是默认值
    gk_outputs = (S.pin(FLOW, "0"), S.pin(FLOW, "1"),
                  S.pin(FLOW, "默认"))
    gk_prop_defs = (
        # 属性名里带 "count" -> node_base 会自动给它挂上"重建引脚"的回调
        ("gk_case_count", "int", 2, "出口数量", "1~12 条分支",
         None, {"min": 1, "max": 12}),
    )

    def build_sockets(self):
        """按 gk_case_count 重建出口。

        出口名就是**要匹配的整数值**,最后一条固定叫『默认』。
        用名字当匹配值(而不是另开一堆属性)是为了:
          * 用户在节点上直接看到"这条出口对应哪个值"
          * 增减出口时不用同步维护另一个数组属性
        """
        count = max(1, min(12, int(getattr(self, "gk_case_count", 2) or 2)))
        self.inputs.clear()
        for spec in self.gk_inputs:
            socket = self.inputs.new(spec["kind"][0], spec["name"])
            if spec["default"] is not None:
                try:
                    socket.default_value = spec["default"]
                except Exception:
                    pass
        self.outputs.clear()
        for index in range(count):
            self.outputs.new(S.FLOW[0], str(index))
        self.outputs.new(S.FLOW[0], "默认")

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_case_count", text="分支数")


@N
class GKNodeForLoop(B.GKStatementNode):
    bl_idname = "GKNodeForLoop"
    bl_label = "循环_次数"
    bl_icon = "FILE_REFRESH"
    gk_category = "流程"
    gk_doc = "把循环体执行指定次数"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(INT, "次数", 3),)
    gk_outputs = (S.pin(FLOW, "循环体"), S.pin(FLOW, "结束后"),)
    gk_prop_defs = (("gk_index_name", "string", "i", "索引变量", "循环索引变量名"),)


@N
class GKNodeWait(B.GKStatementNode):
    bl_idname = "GKNodeWait"
    bl_label = "等待_秒"
    bl_icon = "MOD_TIME"
    gk_category = "流程"
    gk_doc = "延迟一段时间再继续(协程等待，不卡住游戏)"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "秒", 1.0),)
    gk_prop_defs = (("gk_use_process", "bool", False, "使用真实时间", "忽略引擎暂停"),)


@N
class GKNodePrint(B.GKStatementNode):
    bl_idname = "GKNodePrint"
    bl_label = "打印_输出"
    bl_icon = "CONSOLE"
    gk_category = "流程"
    gk_doc = "在 Godot 的输出面板打印一行文本"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(TEXT, "内容", "你好，世界"),)
    gk_input_props = {"内容": "gk_text"}
    gk_prop_defs = (("gk_text", "string", "你好，世界", "内容", "未连线时打印的字面量"),)


# ==========================================================================
# 移动
# ==========================================================================
@N
class GKNodeSetVelocity(B.GKStatementNode):
    bl_idname = "GKNodeSetVelocity"
    bl_label = "移动_设置速度"
    bl_icon = "FORWARD"
    gk_category = "移动"
    gk_doc = "设置速度向量(只改速度不做位移，通常后面接『移动_滑动』)"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(VECTOR, "速度", (0.0, 0.0, 0.0)),)
    gk_input_props = {"速度": "gk_velocity"}
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "目标", "要移动的节点"),
        ("gk_velocity", "float", 0.0, "速度(Y)", "未连线时写入的速度"),
    )

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)


@N
class GKNodeMoveAndSlide(B.GKStatementNode):
    bl_idname = "GKNodeMoveAndSlide"
    bl_label = "移动_滑动"
    bl_icon = "TRACKER"
    gk_category = "移动"
    gk_doc = "按当前速度移动并处理碰撞，对应 move_and_slide()"
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (("gk_target", "node", TARGET_SELF, "目标", "要移动的节点"),)

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)


@N
class GKNodeMoveDirection(B.GKStatementNode):
    bl_idname = "GKNodeMoveDirection"
    bl_label = "移动_按方向"
    bl_icon = "ORIENTATION_GLOBAL"
    gk_category = "移动"
    gk_doc = ("沿指定方向移动。**平台跳跃 / 有重力的游戏务必勾上『重力模式』**："
              "Godot 4 的 CharacterBody 不会自动施加重力，不勾角色会浮在原地不动。\n"
              "『前进轴』『横移轴』决定输入往哪个世界轴走 —— 按着不对就翻一下"
              "(和 UPBGE 里设移动轴是一回事)")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(VECTOR, "方向", (0.0, 0.0, 0.0)),
        S.pin(FLOAT, "速度", 300.0),
    )
    gk_input_props = {"速度": "gk_speed"}
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "目标", "要移动的节点"),
        ("gk_dir_x", "float", 0.0, "方向 X", ""),
        ("gk_dir_y", "float", 0.0, "方向 Y", ""),
        ("gk_speed", "float", 300.0, "速度", "未连线时的速度"),
        # ── 输入往哪个世界轴走(用户要求:像 UPBGE 那样能设)──────────
        # 默认 XZ 平面、前进走 -Z(Godot / glTF 的"前")。
        # 实测踩过:相机朝向不同时,"按前往后、按左往右"完全可能 ——
        # 这不该逼用户去改脚本,给个选项翻过来就行。
        # 2D 项目用不到(只有 X 轴),这两项会被忽略。
        ("gk_axis_h", "enum", "X", "横移轴",
         "左右输入作用在哪个世界轴上",
         (("X", "X (左右)", ""), ("Z", "Z (前后)", ""), ("Y", "Y (上下)", ""))),
        ("gk_axis_v", "enum", "Z", "前进轴",
         "前后输入作用在哪个世界轴上",
         (("Z", "Z (前后)", ""), ("X", "X (左右)", ""), ("Y", "Y (上下)", ""))),
        ("gk_forward_sign", "enum", "NEG", "前进方向",
         "按『前』时沿前进轴的正向还是负向走。默认负向 = Godot / glTF 的『前』",
         (("NEG", "负 (Godot 的『前』)", ""), ("POS", "正", ""))),
        # ── 为什么需要这个开关 ────────────────────────────────────────
        # Godot 4 的 CharacterBody2D/3D **不会自动施加重力**，它只按你设的
        # velocity 移动。所以平台跳跃游戏必须自己累加重力，否则角色
        # 会永远浮在原地(实测:velocity 恒为 (0,0)、is_on_floor 恒 false，
        # 看起来就像"脚本没生效")。
        #
        # 打开这个开关会同时做两件事:
        #   1) 每帧累加 get_gravity()
        #   2) 只改水平速度，纵向留给重力
        # 俯视角游戏(不需要重力)保持关闭即可。
        # 默认关:保持既有工程行为不变，避免"升级后手感突然变了"。
        ("gk_keep_y", "bool", False, "重力模式",
         "平台跳跃 / 有重力的游戏请勾上:自动施加重力，且只改水平速度"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        B.draw_target_picker(self, row)
        row.prop(self, "gk_speed", text="")
        layout.prop(self, "gk_keep_y", text="重力模式(平台游戏勾上)", toggle=True)

    def draw_buttons_ext(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_dir_x")
        row.prop(self, "gk_dir_y")


@N
class GKNodeJump(B.GKStatementNode):
    bl_idname = "GKNodeJump"
    bl_label = "移动_跳跃"
    bl_icon = "TRIA_UP"
    gk_category = "移动"
    gk_doc = "设置纵向速度实现跳跃"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "强度", 400.0),)
    gk_input_props = {"强度": "gk_jump_force"}
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "目标", "要跳跃的节点"),
        ("gk_jump_force", "float", 400.0, "强度", "未连线时的跳跃初速度"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        B.draw_target_picker(self, row)
        row.prop(self, "gk_jump_force", text="")


@N
class GKNodeAddImpulse(B.GKStatementNode):
    bl_idname = "GKNodeAddImpulse"
    bl_label = "移动_施加冲量"
    bl_icon = "FORCE_FORCE"
    gk_category = "移动"
    gk_doc = "对刚体施加冲量(对应 apply_impulse)"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(VECTOR, "冲量", (0.0, -200.0, 0.0)),)
    gk_prop_defs = (("gk_target", "node", TARGET_SELF, "目标", "刚体节点"),)

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)


@N
class GKNodeSetRotation(B.GKStatementNode):
    bl_idname = "GKNodeSetRotation"
    bl_label = "移动_设置朝向"
    bl_icon = "ORIENTATION_LOCAL"
    gk_category = "移动"
    gk_doc = "设置节点朝向(2D 用弧度转 rotation，3D 用 rotation_degrees.y)"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "角度", 0.0), S.pin(INT, "轴", 1),)
    gk_input_props = {"角度": "gk_angle", "轴": "gk_axis"}
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "目标", "要转向的节点"),
        ("gk_angle", "float", 0.0, "角度", "未连线时的角度"),
        ("gk_axis", "enum", "Y", "轴", "3D 里绕哪根轴旋转", (("X", "X", ""), ("Y", "Y", ""), ("Z", "Z", ""))),
        ("gk_degrees", "bool", True, "使用角度制", "关闭则为弧度制"),
    )

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)


@N
class GKNodeFlip(B.GKStatementNode):
    bl_idname = "GKNodeFlip"
    bl_label = "移动_水平翻转"
    bl_icon = "ARROW_LEFTRIGHT"
    gk_category = "移动"
    gk_doc = "按朝向翻转贴图，二次元横版角色常用"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(BOOL, "朝左", False),)
    gk_input_props = {"朝左": "gk_face_left"}
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "目标", "要做翻转的精灵节点"),
        ("gk_mode", "enum", "AUTO", "翻转方式", "默认按速度方向自动判断", (
            ("AUTO", "自动(按速度方向)", ""),
            ("LEFT", "始终朝左", ""),
            ("RIGHT", "始终朝右", ""),
        )),
        ("gk_face_left", "bool", False, "朝左", "『朝左』引脚未连线且方式为手动时使用"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        B.draw_target_picker(self, row)
        row.prop(self, "gk_mode", text="")


# ==========================================================================
# 动画
# ==========================================================================
@N
class GKNodePlayAnim(B.GKStatementNode):
    bl_idname = "GKNodePlayAnim"
    bl_label = "动画_播放"
    bl_icon = "PLAY"
    gk_category = "动画"
    gk_doc = ("播放动画片段(对应 AnimationPlayer.play)。"
              "『动画播放器』留空 = 自动找:glTF 导入时 AnimationPlayer 藏在"
              "模型实例内部,自动找会自动把它挖出来,不用你填路径。"
              "── 右侧三个事件出口 ──"
              "『开始时』= 动画刚开始那一刻;"
              "『每帧』= 播放期间每一帧(做持续判定用);"
              "『结束时』= 播完了那一刻(做连招、循环用)。"
              "只对连了线的出口生成代码,没连就没有开销")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "动画名", "idle"),
        target_pin("动画播放器"),
    )
    # ── 三个事件出口(照 UPBGE 的 Play Animation 设计)────────────────
    # UPBGE 那个节点最值钱的就是 Started / Running / On Finish ——
    # 有它们才能做"播完接下一招""循环期间持续判定"。
    # 我们以前只有"完成",接不了任何后续判断。
    #
    # 顺序:"完成"在前(沿用原来的默认出口,老图不受影响),
    # 然后依次是三个事件出口 —— 视觉上从上到下就是 UPBGE 那个排列。
    gk_outputs = (
        S.pin(FLOW, "完成"),
        S.pin(FLOW, "开始时"),
        S.pin(FLOW, "每帧"),
        S.pin(FLOW, "结束时"),
    )
    gk_input_props = {"动画名": "gk_anim_name", "动画播放器": "gk_player"}
    gk_prop_defs = (
        # 默认必须是**空**(=自动查找)。以前默认写的是字面量 "AnimationPlayer",
        # 于是生成 `$AnimationPlayer` —— 那是个相对路径,而 glTF 导入时
        # AnimationPlayer 在 Model/ 下面,路径必然找不到,脚本直接崩。
        ("gk_player", "node", "", "动画播放器",
         "留空 = 自动查找(推荐);也可以填场景里的节点名"),
        # ── 动画名:文本框 + 『选…』按钮,不是下拉 ─────────────────────
        # 想做成 EnumProperty 下拉,但 Blender 不允许:
        #     TypeError: EnumProperty(...): 'default' can only be an
        #                integer when 'items' is a function
        # 动态 items 的枚举**默认值只能是整数下标**,而我们这里要的是
        # 动作名字符串,两边对不上。
        #
        # 所以改成:文本框(名字能直接看到) + 『选…』按钮弹列表。
        # 按钮弹出的列表由动作归属分析生成,属于当前物体的排在前面并带 ★。
        #
        # 默认值留空 —— 以前默认 `idle` 是个**假名字**,用户不动它就会
        # 一直播不出来。空值会让导出的脚本报"动画不存在,可用的是…",
        # 比一个看起来合理但永远错的名字好。
        ("gk_anim_name", "string", "", "动画名",
         "不用管它 —— 上面选了『动作』就会自动同步成动作名"),
        # ── 原生动作选择器(用户要的"自动列出它的动作")────────────────
        # 这是**正解**:`PointerProperty(type=Action)` 拿到的是 Blender
        # 原生的数据块选择器(带放大镜、能搜、能浏览全部动作)。
        # 之前试的 EnumProperty 动态下拉被 Blender 挡了
        # (动态 items 的默认值只能是整数,而我们要字符串),
        # 『选…』按钮又需要用户手点一下。见 node_base.prop_action 的说明。
        #
        # 填了这个就**优先用它**,下面的文本框退化成"手填兜底"。
        ("gk_anim_action", "action", None, "动作",
         "从文件里所有动作中选一个(原生选择器,能搜索)"),
        ("gk_speed", "float", 1.0, "播放速度", "1.0 为原速"),
        # ── 照 UPBGE『Play Animation』补齐的参数 ────────────────────────
        # 用户要的那几项:起始/结束帧、Blend-In、混合模式、层。
        # 全部以 0 / -1 为"不生效",所以**老图的行为一点不变**
        # (起始=0、结束=0、Blend-In=-1、层=0、模式=替换)。
        #
        # 单位说明:Godot 的 seek/play_section 收的是**秒**,
        # 而 Blender 里用户看到的是**帧**,所以导出时按场景帧率换算。
        ("gk_start_frame", "float", 0.0, "起始帧",
         "从这个动作的第几帧开始播(0 = 从头)。导出的代码会换算成秒"),
        ("gk_end_frame", "float", 0.0, "结束帧",
         "播到第几帧就停(0 = 播到动作末尾)。相当于在播放器里裁一段"),
        ("gk_blend_in", "float", -1.0, "Blend-In(秒)",
         "和上一个动画之间的过渡时间(秒)。-1 或 0 = 立即切换"),
        ("gk_layer", "int", 0, "层",
         "0 = 主播放器(默认)。≥1 会**另起一个动画播放器**专放这层 —— "
         "上下半身分离动画用(上半身的动作挂 1 层,下半身留在 0 层)"),
        ("gk_blend_mode", "enum", "REPLACE", "混合模式",
         "Godot 的 AnimationPlayer 只能「替换播放」,没有 UPBGE 那种加法/组合。"
         "这里给的是实用区分:『续播』= 这个动画已经在播就不重新开始",
         (("REPLACE", "替换(重新开始)", "每次都从头播这个动画"),
          ("CONTINUE", "续播(同名不打断)", "已经在播同名动画就跳过,不鬼畜重播"))),
    )

    def draw_buttons(self, context, layout):
        # ── 主入口:原生动作选择器 ──────────────────────────────────
        # 用户反馈"再在下面的那个 idle 是不是多余了,这个又不能改什么,
        # 为啥不去掉" —— 对,那个文本框以前顶着个假默认值 `idle`,
        # 看着能改其实改了也没用(上面选了动作就以动作为准)。
        #
        # 现在:**选了动作就只显示动作名(只读),不显示文本框**。
        # 只有"没选动作"时才给你一个手填的框 —— 那时它才有意义。
        layout.prop(self, "gk_anim_action", text="")
        if self.gk_anim_action is not None:
            # 选了动作 -> 只**显示**名字,不再给一个能改但改了没用的框
            layout.label(text=f"动画名:{self.gk_anim_action.name}",
                         icon="CHECKMARK")
        else:
            # 没选动作 -> 给手填框 + 『选…』按钮。
            # 按钮那个列表会**按归属排序**(属于当前物体的带 ★ 排前面),
            # 比原生选择器更好找 —— 原生选择器只能列全部。
            row = layout.row(align=True)
            row.prop(self, "gk_anim_name", text="", icon="SORTALPHA")
            picker = row.operator("gk.pick_anim_name", text="", icon="VIEWZOOM")
            picker.node_name = self.name
        layout.prop(self, "gk_speed", text="播放速度")
        # ── 照 UPBGE 补的那几项(见 gk_prop_defs)──────────────────────
        # 分两行画,免得节点变太宽。
        row = layout.row(align=True)
        row.prop(self, "gk_start_frame", text="起始帧")
        row.prop(self, "gk_end_frame", text="结束帧")
        row = layout.row(align=True)
        row.prop(self, "gk_blend_in", text="Blend-In")
        row.prop(self, "gk_layer", text="层")
        layout.prop(self, "gk_blend_mode", text="")

    def draw_buttons_ext(self, context, layout):
        layout.label(text="动画播放器:留空 = 自动查找(推荐)")
        B.draw_target_picker(self, layout, "gk_player")


@N
class GKNodeAnimParam(B.GKStatementNode):
    bl_idname = "GKNodeAnimParam"
    bl_label = "动画_设混合参数"
    bl_icon = "DRIVER"
    gk_category = "动画"
    gk_doc = ("给整个模型的形态键统一设一个值。"
              "做口型/表情时有用:播放说话动画的同时,"
              "用这个节点把嘴形参数推到 0~1")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "值", 1.0),)
    gk_input_props = {"值": "gk_value"}
    gk_outputs = (S.pin(FLOW, "完成"),)

    gk_prop_defs = (
        ("gk_value", "float", 1.0, "值", "0~1"),
        ("gk_shape_index", "int", 0, "形态键序号", "第几个形态键(从 0 数)"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_value", text="")
        row.prop(self, "gk_shape_index", text="序号")


# ==========================================================================
# 动画系统节点 —— 属于**独立的动画树**(GKAnimationTree)
# ==========================================================================
# 用户原话:"这个可以按照虚幻5蓝图的来做,或者建立一个动作系统,像虚幻5
# 那样,然后就像 upbge 的逻辑砖调用,而不是直接在这来进行,不然,
# 后面就密密麻麻的(这也是 upbge 的缺点,虚幻5的分开得还好)"
#
# 他说得对。第一版把状态机整个塞进一个节点(状态表多行文本 + 一堆动态
# 引脚),那就是 UPBGE 的老毛病 —— 东西一多节点就糊成一团。
#
# 改成照虚幻 5 的 **Animation Blueprint** 那样:
#
#     游戏逻辑树(写玩法)          动画树(写动作,独立一张图)
#     ┌──────────────┐            ┌──────────────────────┐
#     │ 每当_准备     │            │ [状态: idle]          │
#     │   动画_状态机 ├──引用─────>│ [状态: run]  条件引脚… │
#     └──────────────┘            │ [状态: jump]          │
#                                 └──────────────────────┘
#
# 玩法那边只留**一个**引用节点,动作全在另一张图里,两边都清爽。
#
# ── 条件怎么表达 ────────────────────────────────────────────────
# 每个状态节点上有：
#   * 一个布尔**条件引脚** —— 接表达式节点,为真时切到它
#   * 一个『优先级』整数 —— 同时为真时谁赢(数字小的先判)
#   * 一个『目标』布尔 —— 勾上表示这是**起始状态**
# 转移图就是"哪条条件先为真去哪",不需要单独画线。
_ANIM_TREE_HINT = (
    "这个节点属于**动画树**(不是游戏逻辑树)。"
    "在节点编辑器左上角把类型切到『游戏动画』,或者点『游戏动画』面板里的"
    "『打开动画树』"
)


@N
class GKNodeAnimLocomotion(B.GKExpressionNode):
    bl_idname = "GKNodeAnimLocomotion"
    bl_label = "动画判断_移动状态"
    bl_icon = "ORIENTATION_GLOBAL"
    gk_category = "动画系统"
    gk_tree_type = "GKAnimationTree"
    gk_doc = (
        "把动画切换最常用的判断一次给你 —— 不用为每个条件单独拉节点。"
        "输出全是布尔,直接接到转场节点的『条件』引脚:"
        "『在地面』『在移动』『地面且移动』『已离地』『在上升』"
        "『在下落』『速度快』『速度慢』。"
        "『速度阈值』用来区分走和跑")
    gk_inputs = (
    )
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "角色", "要判断的节点"),
        ("gk_speed_threshold", "float", 0.1, "速度阈值",
         "『在移动』和『速度快』用的门槛"),
        ("gk_fast_threshold", "float", 3.0, "跑的速度阈值",
         "『速度快』用的门槛(比上面那个高)"),
    )
    gk_outputs = (
        S.pin(BOOL, "在地面", False),
        S.pin(BOOL, "在移动", False),
        S.pin(BOOL, "地面且移动", False),
        S.pin(BOOL, "已离地", False),
        S.pin(BOOL, "在上升", False),
        S.pin(BOOL, "在下落", False),
        S.pin(BOOL, "速度快", False),
        S.pin(BOOL, "速度慢", False),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_speed_threshold", text="移动")
        row.prop(self, "gk_fast_threshold", text="跑")


def anim_state_items(self, context):
    """『从 / 到』下拉的候选:**这张动画树里的所有状态名**。

    用户 2026-10-03:状态机天然是环,而 Blender 不允许连成环
    ("链接形式的循环不受支持")。所以给他一条不用连线的路 ——
    在转场上直接选"从哪个状态到哪个状态"。
    """
    items = [("", "不指定(看连线)", "想用连线就留空")]
    tree = getattr(self, "id_data", None)
    seen = set()
    for node in getattr(tree, "nodes", ()) or ():
        if getattr(node, "bl_idname", "") != "GKNodeAnimState":
            continue
        name = anim_state_name(node)
        if name and name not in seen:
            seen.add(name)
            items.append((name, name, f"『{name}』"))
    return items



@N
class GKNodeAnimState(B.GKStatementNode):
    bl_idname = "GKNodeAnimState"
    bl_label = "动画_状态"
    bl_icon = "POSE_HLT"
    gk_category = "动画系统"
    gk_tree_type = "GKAnimationTree"
    # ── 这里**曾经**是 `gk_hidden = True`(踩过的坑)────────────────
    # 上一轮(A 方案之前)这个节点是"旧版卡片",标了隐藏。
    # 这一轮把它**重新做成了 A 方案的状态节点**,却忘了把这一行删掉 ——
    # 于是它继续被菜单过滤掉,用户在"添加节点"里**根本看不到它**
    # (实测:菜单里只有『动画_转场』和『动画判断_移动状态』)。
    #
    # 教训:改造一个已有节点时,要把它身上所有"上一版留下的标记"
    # 一起检查一遍,别只改自己新加的部分。
    gk_doc = ("一个动画状态(照虚幻 5 的动画状态机)。"
              "『动画』选这个状态播什么;"
              "左边『进入』接上一个状态的『转到』;"
              "右边『转到』接一个『动画_转场』。"
              "『进入』没被任何『转到』指到的状态 = 起始状态")
    # ── A 方案:照虚幻 5 的动画状态机(用户确认)──────────────────
    # UE5 里状态之间**只能有一条出线**(出线接一个 Transition,
    # Transition 再接下一个状态的入口),所以不需要额外给"完成"引脚 ——
    # 『转到』一条就够了。
    #
    #   [动画_状态 idle] ● 进入   ● 转到 ──> [动画_转场] ● 转到 ──> [动画_状态 run] ● 进入
    #
    # 一条链就是一张状态机图,一眼看出从哪来、到哪去。
    gk_inputs = (
        S.pin(FLOW, "进入"),
    )
    gk_outputs = (
        S.pin(FLOW, "转到"),
        # ── 状态自己的执行流(照虚幻 5 的 State Entry / State Exit)──────
        # 用户 2026-10-03 想做的事:**"上一个动画播完了 -> 设一个布尔变量"**,
        # 他把状态的『转到』直接接到了『动画_设变量』上 —— 但『转到』是
        # "去下一个状态"的意思,那条线只能报错。
        #
        # 所以在状态上开两个**做事的**出口:
        #   『进入时』-> 进入这个状态之后要执行的语句(设变量、播音效…)
        #   『离开时』-> 切走之前要执行的语句(清标志、收尾…)
        # 有了它们,『动画_设变量』在动画树里就是**活**的了。
        S.pin(FLOW, "进入时"),
        S.pin(FLOW, "离开时"),
    )
    gk_prop_defs = (
        # 状态名 = 动画名,用**动作数据块**选,不用手打。
        # 手打 "run" 打成 "rnu" 不会有任何提示,要到 Godot 运行时才发现。
        ("gk_anim_action", "action", None, "动画",
         "这个状态播哪个动作(原生选择器,能搜索)"),
        ("gk_state_name", "string", "idle", "状态名(手填兜底)",
         "没选『动画』时才用它"),
        # ── 为什么要好几个『进入』(Blender 的硬限制)────────────────────
        # **一个输入引脚只允许一条连线**(踩过的坑,README 第 9 条)。
        # 于是"待机"被 走/跑/跳/受击/攻击 五条转场指着时,只有**最后接的
        # 那一条**留在引脚上,前面几条的线会被悄悄顶掉 —— 用户在画布上
        # 会看到自己的箭头跳走,导出时则报"不知道切到哪个状态"。
        #
        # 解法:入口引脚数量可调(和『序列』的出口一个做法)。多接几个
        # 转场就把它调大,每个引脚接一条。第一个仍然叫『进入』——
        # 老文件里已经接好的线不会断。
        ("gk_in_count", "int", 2, "入口数量",
         "可以接几条转场(Blender 一个输入引脚只允许一条线)", None,
         {"min": 1, "max": 8}),
        # ── 一个状态**同时**播好几段动画(用户 2026-10-03 要的)────────
        # 他的 MMD 模型就是这样:**骨骼动作**和**形态键动作**是分开的两段,
        # 想看完整动作必须同时播。Godot 的 AnimationPlayer 一次只能播一段
        # (`play()` 会顶掉上一段),所以这里按"层"复制出额外的播放器
        # (见 codegen.need_layer_player),两段各播各的,互不干扰。
    )

    def build_sockets(self):
        """按『入口数量』生成进入引脚:进入 / 进入 2 / 进入 3 …"""
        count = max(1, min(8, int(getattr(self, "gk_in_count", 2) or 2)))
        self.inputs.clear()
        self.inputs.new(S.FLOW[0], "进入")
        for index in range(1, count):
            self.inputs.new(S.FLOW[0], f"进入 {index + 1}")
        self.outputs.clear()
        for spec in self.gk_outputs:
            socket = self.outputs.new(spec["kind"][0], spec["name"])
            if spec["default"] is not None:
                try:
                    socket.default_value = spec["default"]
                except Exception:
                    pass

    def resolved_state_name(self):
        """这个状态叫什么 —— 优先用选中的**动作**,否则用手填的。

        两种来源都支持是为了两件事:
          * 选了动作 -> 名字一定对(动画不存在根本选不出来)
          * 没选     -> 还能手填(比如动画是运行时才加载的)
        """
        action = getattr(self, "gk_anim_action", None)
        if action is not None:
            name = str(getattr(action, "name", "") or "")
            if name:
                return name
        return str(getattr(self, "gk_state_name", "") or "")

    def extra_animation_names(self):
        """这个状态**同时播**的另外几段动画名(没填的空着)。"""
        names = []
        for prop in ("gk_anim_action_2", "gk_anim_action_3"):
            action = getattr(self, prop, None)
            name = str(getattr(action, "name", "") or "") if action else ""
            names.append(name)
        return [name for name in names if name]

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_anim_action", text="")
        if self.gk_anim_action is not None:
            layout.label(text=f"状态:{self.gk_anim_action.name}",
                         icon="CHECKMARK")
        else:
            layout.prop(self, "gk_state_name", text="", icon="SORTALPHA")
        # 同时播的另外几段:填了才画,免得每个状态都顶着两个空框
        extras = self.extra_animation_names()
        if extras or self.gk_anim_action_2 is not None:
            layout.label(text=f"同时播:{len(extras)} 段", icon="DUPLICATE")
        row = layout.row(align=True)
        row.prop(self, "gk_anim_action_2", text="")
        row.prop(self, "gk_anim_action_3", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_in_count", text="入口")
        full = all(
            bool(getattr(socket, "is_linked", False))
            for socket in (getattr(self, "inputs", ()) or ()))
        if full and int(getattr(self, "gk_in_count", 2) or 2) < 8:
            # 每个入口都被占了 —— 下一条线会**顶掉**现有的那一条,
            # 所以这里必须提醒(用户会看到自己的箭头"跳走")
            row.label(text="已满,调大再接", icon="ERROR")
        layout.label(text="转到 -> 接『动画_转场』的『上游』", icon="FORWARD")
        layout.label(text="进入时 / 离开时 -> 接要执行的语句", icon="PLAY")


@N
class GKNodeAnimEntry(B.GKStatementNode):
    bl_idname = "GKNodeAnimEntry"
    bl_label = "动画_开始"
    bl_icon = "PLAY"
    gk_category = "动画系统"
    gk_tree_type = "GKAnimationTree"
    gk_doc = ("**这张动画图从哪儿开始、什么时候开始。**"
              "把它的『开始』接到第一个状态的『进入』—— 那个状态就是起始状态"
              "(不用再靠『谁没被指到』去猜)。"
              "── 开始条件可自定义(用户要求)──"
              "『开始方式』可以选:立刻开始 / 变量为真 / 刚按下某键 / "
              "上一个动画播完 / 自己写(接条件引脚)。"
              "条件不成立时整张图**不动**,成立才开始播第一个状态;"
              "走到『动画_结束』之后,条件再次成立就会重新开始")
    gk_inputs = (
        S.pin(BOOL, "条件", False),
    )
    gk_outputs = (
        S.pin(FLOW, "开始"),
    )
    gk_prop_defs = (
        ("gk_start_mode", "enum", "NOW", "开始方式",
         "『变量 / 输入 / 播完』不用接线;『自己写』才用左边的条件引脚",
         (("NOW", "立刻开始", "游戏一开始(或结束之后马上)就跑这张图"),
          ("PIN", "自己写(接条件引脚)", "想怎么判就怎么判 —— 接判断节点"),
          ("VAR", "变量为真", "侧栏『变量』里的布尔变量为真时开始"),
          ("ACTION_JUST", "刚按下某键", "按下按键的那一刻开始"),
          ("ACTION_PRESSED", "按住某键", "按住期间都算满足"),
          ("FINISH", "上一个动画播完", "某段动画播完之后开始"))),
        ("gk_var_name", "string", "", "变量名", "开始方式 = 『变量为真』时用"),
        ("gk_cond_action", "string", "attack", "输入动作",
         "开始方式 = 『刚按下 / 按住』时用"),
        ("gk_anim_name", "string", "", "动画名",
         "开始方式 = 『上一个动画播完』时用(留空 = 任意动画播完)"),
    )

    def draw_buttons(self, context, layout):
        mode = str(getattr(self, "gk_start_mode", "NOW"))
        layout.prop(self, "gk_start_mode", text="")
        if mode == "VAR":
            layout.prop(self, "gk_var_name", text="", icon="SORTALPHA")
        elif mode in ("ACTION_JUST", "ACTION_PRESSED"):
            layout.prop(self, "gk_cond_action", text="", icon="MOUSE_MOVE")
        elif mode == "FINISH":
            layout.prop(self, "gk_anim_name", text="", icon="TIME")
        elif mode == "PIN":
            layout.label(text="条件 <- 接判断节点的输出", icon="LINKED")
        layout.label(text="开始 -> 接第一个状态的『进入』", icon="FORWARD")


@N
class GKNodeAnimExit(B.GKStatementNode):
    bl_idname = "GKNodeAnimExit"
    bl_label = "动画_结束"
    bl_icon = "QUIT"
    gk_category = "动画系统"
    gk_tree_type = "GKAnimationTree"
    gk_doc = ("**这张动画图到哪儿算结束。**"
              "状态的『转到』可以直接连到它的『进入』—— 走到这里 = 这段动画"
              "演完了,状态机会停下来(不再判转场),并把"
              "『已结束』置为真,玩法图里用『动画_运行状态』就能读到,"
              "接着做下一步逻辑(结算伤害、回到玩法状态、播下一段动画…)")
    gk_inputs = (
        S.pin(FLOW, "进入"),
    )
    gk_outputs = (
        S.pin(FLOW, "结束后"),
    )
    gk_prop_defs = (
        ("gk_stop_player", "bool", False, "结束时停住动画",
         "勾上 = 把动画播放器停下(回到静止姿势);不勾 = 保持最后一帧的姿势"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_stop_player", text="结束时停住动画")
        layout.label(text="进入 <- 接状态的『转到』", icon="LOOP_BACK")
        layout.label(text="结束后 -> 接要执行的语句(可选)", icon="FORWARD")


@N
class GKNodeAnimStatus(B.GKExpressionNode):
    bl_idname = "GKNodeAnimStatus"
    bl_label = "动画_运行状态"
    bl_icon = "INFO"
    gk_category = "动画"
    gk_tree_type = BOTH_TREES
    gk_doc = ("这张动画图现在什么情况 —— 给**玩法图**判断下一步用:"
              "『已结束』(走到结束口了)、『在跑』(正在演)、"
              "『当前状态』(状态名,做分支/调试用)。"
              "例:`如果(动画_运行状态.已结束) -> 扣血 / 回到待机`")
    gk_prop_defs = (
        ("gk_state_var", "string", "anim_state", "状态变量名",
         "和『动画_状态机』上填的一致(默认 anim_state)"),
    )
    gk_outputs = (
        S.pin(BOOL, "已结束", False),
        S.pin(BOOL, "在跑", True),
        S.pin(TEXT, "当前状态", ""),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_state_var", text="", icon="SORTALPHA")


@N
class GKNodeAnimTransition(B.GKStatementNode):
    bl_idname = "GKNodeAnimTransition"
    bl_label = "动画_转场"
    bl_icon = "TRACKING_FORWARDS_SINGLE"
    gk_category = "动画系统"
    gk_tree_type = "GKAnimationTree"
    gk_doc = (
        "一条转场规则(对应虚幻 5 的 Transition Rule):"
        "『条件』为真时,从左边连过来的状态切到右边连着的状态。"
        "── 三件事都要有 ──"
        "『上游』接上一个状态的『转到』(忘了接就用下面的『从』下拉选);"
        "『条件』接判断节点的输出,或者把『条件来源』改成"
        "『上游动画播完』/『选输入』直接挑一个按键;"
        "『转到』接下一个状态的『进入』(也能用『到』下拉选)。"
        "── 忘了接上游会怎样 ──"
        "以前这种情况导出后**什么都不生成**,状态机一动不动;"
        "现在导出时会明确告诉你缺哪根线"
    )
    # ── 为什么有两个输入引脚 ────────────────────────────────────────
    # 『进入』= **执行流**入口,接上游状态的『转到』。
    # 『条件』= 布尔判断,接『动画判断_移动状态』之类的输出,
    #          或者用『条件来源』在节点上直接选一个按键。
    #
    # 两者必须分开:一条转移既要知道"从哪来"(流),
    # 又要知道"什么条件下切"(布尔)。合成一个引脚会互相覆盖。
    gk_inputs = (
        # -- 引脚名要能一眼看出该接哪(用户实测踩过)----------------
        # 原来这个引脚叫『进入』,和状态节点的『进入』**同名** ——
        # 用户截图里就是把上游状态的『转到』接到了转场的
        # 『条件』上,而这个『进入』空着,于是链路是空的、
        # 整个状态机没有一条转移(实测诊断出来的)。
        #
        # 改叫『上游』,并说清接上一个状态的『转到』。
        S.pin(FLOW, "上游"),
        S.pin(BOOL, "条件", False),
    )
    gk_outputs = (
        S.pin(FLOW, "转到"),
    )
    gk_prop_defs = (
        # ── 备用下拉:连线忘了也能用(用户 2026-10-03 确认要)──────────
        # 起因:他的树里『上游』空着、『转到』却接了状态,导出后整台状态机
        # 是死的(_physics_process 只有 pass)。连线仍是首选(看得见箭头),
        # 但下拉是兜底 —— 少接一根线不该让状态机静默失效。
        # 两个都填时**以连线为准**(见 nodes.anim_tree_graph)。
        ("gk_from_state", "string", "", "从状态",
         "备用:『上游』没接线时用这个(下拉里是这张树的状态)"),
        ("gk_to_state", "string", "", "到状态",
         "**推荐**:点右边的下拉从这张树的状态里挑'切到哪个'。"
         "这样图里不用连回状态的线 —— Blender 不允许环,"
         "连线连成环会报『链接形式的循环不受支持』"),
        # ── 复合条件:不用额外拉表达式节点 ──────────────────────────
        # 蓝图里最常见的条件就是"某个键按住/松开"。为了这个去拉一个
        # 『输入_按住』节点再连线,状态一多整张图就全是线 —— 那正是
        # 用户抱怨的"密密麻麻"。
        ("gk_cond_source", "enum", "PIN", "条件来源",
         "『自己写』= 用左边的条件引脚;其它选项都在节点上直接挑",
         (("PIN", "自己写(接引脚)", "想多复杂都行:速度、距离、组合条件"),
          ("FINISH", "上游动画播完", "上游状态那段动画播完的那一刻切过去"
                                     "(动画要设成不循环)"),
          ("ACTION_PRESSED", "选输入:按住", "按键按住期间为真"),
          ("ACTION_JUST", "选输入:刚按下", "按键刚按下那一帧为真"),
          ("ACTION_RELEASED", "选输入:松开", "按键松开期间为真"))),
        ("gk_cond_action", "string", "move_right", "输入动作",
         "只有『条件来源』选了输入类才用得上"),
    )

    def condition_expr(self, ctx):
        """这条转场的条件表达式;没有条件就返回 None。

        只有『自己写』和『选输入』两种在这里处理 —— 『上游动画播完』
        要靠"上游状态叫什么名字"才能算,那是**拓扑**信息,所以放在
        codegen 的状态图遍历里(见 codegen._edge_condition)。
        """
        source = str(getattr(self, "gk_cond_source", "PIN"))
        if source == "PIN":
            socket = self.inputs.get("条件")
            if socket is None or not getattr(socket, "is_linked", False):
                return None
            return ctx.expr_of(socket)
        if source == "FINISH":
            return None
        action = str(getattr(self, "gk_cond_action", "move_right") or "")
        if not action:
            return None
        const = ctx.add_action(action)
        func = ANIM_ACTION_SOURCES.get(source)
        return f"{func}({const})" if func else None

    def draw_buttons(self, context, layout):
        source = str(getattr(self, "gk_cond_source", "PIN"))
        layout.prop(self, "gk_cond_source", text="")
        # ── 从 / 到:连线优先,下拉兜底 ────────────────────────────
        # 接好了线就只画一个 ✓(把"这台状态机到底认哪条边"一眼说清),
        # 没接才把下拉和放大镜摆出来 —— 节点本来就小,常驻两个空输入框
        # 只会让人以为"这里必须填"。
        wired_from = bool(link_sources(self, "上游"))
        wired_to = bool(link_targets(self, "转到"))
        if wired_from:
            layout.label(text="上游 <- 上一个状态的『转到』", icon="CHECKMARK")
        else:
            row = layout.row(align=True)
            row.label(text="上游空着 -> 用『从』兜底", icon="LOOP_BACK")
            pick = row.operator("gk.pick_anim_state", text="", icon="VIEWZOOM")
            pick.node_name = self.name
            pick.prop_name = "gk_from_state"
            layout.prop(self, "gk_from_state", text="从")
        if source == "PIN":
            layout.label(text="条件 <- 接判断节点的输出", icon="LINKED")
        elif source == "FINISH":
            layout.label(text="切过去 = 上游那段动画播完", icon="TIME")
        else:
            layout.prop(self, "gk_cond_action", text="")
        # ── "线被忽略了"必须画在节点上(用户 2026-10-03 的疑问)────────
        # 他的图:『在地面』接在『条件』上,而『条件来源』选的是别的 ——
        # 于是那根线**根本没参与判定**,可节点上完全看不出来,
        # 他只会觉得"这个切换条件不能自定义"。
        if source != "PIN" and link_sources(self, "条件"):
            layout.label(text="! 条件引脚接的线被忽略了", icon="ERROR")
            layout.label(text="   以『条件来源』为准")
        if wired_to:
            layout.label(text="转到 -> 下一个状态的『进入』", icon="CHECKMARK")
        else:
            row = layout.row(align=True)
            row.label(text="转到空着 -> 用『到』兜底", icon="FORWARD")
            pick = row.operator("gk.pick_anim_state", text="", icon="VIEWZOOM")
            pick.node_name = self.name
            pick.prop_name = "gk_to_state"
            layout.prop(self, "gk_to_state", text="到")


# ==========================================================================
# 动画树的自定义变量节点(照虚幻蓝图的 Get / Set)
# ==========================================================================
# 用户要求:"在侧边栏可以添加变量，自定义变量引脚连线"。
# 变量本身存在动画树上(见 properties.GKAnimVariable),这里只负责
# 把它**变成图里的引脚**:『取』拉出一个值,『设』写进去。
#
# 用 ANY 引脚而不是按类型分好几种节点:变量可以随时改类型,
# 分成"布尔取变量/浮点取变量…"的话改类型就得换节点、连线全断。
@N
class GKNodeAnimGetVar(B.GKExpressionNode):
    bl_idname = "GKNodeAnimGetVar"
    bl_label = "动画_取变量"
    bl_icon = "DRIVER"
    gk_category = "动画系统"
    gk_tree_type = "GKAnimationTree"
    gk_doc = ("读取这张动画树的自定义变量(在侧栏『变量』里加)。"
              "相当于蓝图里把变量拖进图的那个 Get 节点")
    gk_outputs = (S.pin(ANY, "值", None),)

    gk_prop_defs = (
        ("gk_var_name", "string", "", "变量名",
         "侧栏『变量』里声明的名字(点那行的『取』会自动填好)"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_var_name", text="", icon="SORTALPHA")
        picker = row.operator("gk.anim_var_pick", text="", icon="VIEWZOOM")
        picker.node_name = self.name
        layout.label(text="值 -> 接要用它的引脚", icon="FORWARD")


@N
class GKNodeAnimSetVar(B.GKStatementNode):
    bl_idname = "GKNodeAnimSetVar"
    bl_label = "动画_设变量"
    bl_icon = "CHECKBOX_HLT"
    gk_category = "动画系统"
    gk_tree_type = "GKAnimationTree"
    gk_doc = ("给这张动画树的自定义变量赋值 —— 蓝图里的 Set 节点。"
              "变量一改,引用它的转场条件下一帧就跟着变")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(ANY, "值", None),)
    gk_outputs = (S.pin(FLOW, "完成"),)

    gk_prop_defs = (
        ("gk_var_name", "string", "", "变量名",
         "侧栏『变量』里声明的名字(点那行的『设』会自动填好)"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_var_name", text="", icon="SORTALPHA")
        picker = row.operator("gk.anim_var_pick", text="", icon="VIEWZOOM")
        picker.node_name = self.name
        layout.label(text="执行 -> 完成", icon="FORWARD")


@N
class GKNodeAnimStateMachineRef(B.GKStatementNode):
    bl_idname = "GKNodeAnimStateMachineRef"
    bl_label = "动画_状态机"
    bl_icon = "AUTOMERGE_ON"
    gk_category = "动画"
    gk_doc = ("驱动一张**动画树**(在『游戏动画』面板里建)。"
              "动作的切换规则全写在动画树里,这里只负责启动它 —— "
              "玩法图和动画图分开放,两张图都不会糊成一团。"
              "『树名』留空 = 用文件里第一张动画树。"
              "── 作用在谁身上 ──"
              "**就是脚本挂着的这个物体**(角色的 AnimationPlayer 在"
              "模型内部,导出后自动往下找),所以不需要再选物体")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "树名", ""),
    )
    gk_input_props = {"树名": "gk_tree_name"}
    gk_outputs = (S.pin(FLOW, "完成"),)

    gk_prop_defs = (
        # ── 树名:文本框 + 『选…』按钮(和『动画_播放』同一套)──────────
        # 想做成下拉,但 Blender 不允许动态 items 的枚举配字符串默认值
        # (`'default' can only be an integer when 'items' is a function`)。
        # 所以沿用那个已经验证过的做法:文本框 + 按钮弹列表。
        #
        # 留空 = 用文件里第一张动画树。**只有一张树时根本不用管它** ——
        # 这就是用户问的"一般都是两个就可以了吧"的答案:
        # 是的,大多数情况留空就行。
        ("gk_tree_name", "string", "", "树名",
         "留空 = 用文件里第一张动画树(只有一张时不用管)"),
        # ── 『动画播放器』物体选择已删除(用户 2026-10-03 要求)─────────
        # 用户原话:"删除物体选择,原因:在此物体选择动画_状态机时自然是
        # 作用于本身"。对 —— 这个节点生成的是**脚本所在物体**的代码,
        # 选别的物体只会生成一条指向它的路径,既没必要又容易选错。
        #
        # 属性本身留着(不画、不用):老文件里存过值,删掉声明会让
        # Blender 读文件时报未知属性。见 codegen.animation_player。
        ("gk_player", "node", "", "(已废弃)动画播放器",
         "已经不画也不用了 —— 状态机作用在脚本所在物体上"),
        ("gk_state_var", "string", "anim_state", "状态变量名",
         "当前状态存在哪个成员变量里"),
        ("gk_check_mode", "enum", "EVERY_FRAME", "检查时机",
         "每个物理帧判一次 / 只在开始时判一次",
         (("EVERY_FRAME", "每帧检查", "动作连贯,适合走跑切换"),
          ("ON_ENTER", "只在开始时判", "省性能,适合一次性判定"))),
        # ── 调试开关(用户要的"打印节点")────────────────────────────
        # 与其再加一个『调试_打印状态』节点串在链上(会把链路图搞复杂),
        # 不如在这里加个勾选框 —— 状态切换本来就只有**一处**生成点,
        # 在这儿加打印最省事。
        ("gk_debug_print", "bool", False, "打印状态变化",
         "勾上后每次切换状态都往 Godot 输出面板打一行,"
         "用来确认状态机到底有没有在跑"),
    )

    def draw_buttons(self, context, layout):
        # ── 树名:只有一张树时不问,直接显示会用哪张(用户要求)─────────
        # 用户原话:"在指定物体后,就会自动查找…并不需要输入而自动列出来"。
        # 手输树名是这套流程里唯一还要打字的地方,而大多数文件只有一张树
        # —— 那种情况下这个输入框纯属多余,还容易打错。
        # ── 树名:永远给出可选项(用户 2026-10-03 的疑问)──────────────
        # 用户问"一个游戏逻辑里是否可以有多个游戏动画?"—— 能(见 codegen
        # 里状态变量的按节点隔离),但前提是**你得能选到第二张树**。
        # 老逻辑"只有一张树时不画选择器"让这个入口看起来根本不存在,
        # 所以现在**一直画**:一张时它显示名字,多张时点放大镜就能换。
        trees = animation_tree_names()
        row = layout.row(align=True)
        if len(trees) <= 1:
            row.label(text=f"动画树:{trees[0] if trees else '(还没有)'}",
                      icon="NODETREE")
        else:
            row.prop(self, "gk_tree_name", text="", icon="NODETREE")
        picker = row.operator("gk.pick_anim_tree", text="", icon="VIEWZOOM")
        picker.node_name = self.name
        if len(trees) > 1:
            row.label(text=f"(共 {len(trees)} 张)", icon="INFO")
        layout.prop(self, "gk_check_mode", text="")
        layout.prop(self, "gk_debug_print", text="打印状态变化")
        self._draw_tree_graph(layout)

    def _draw_tree_graph(self, layout):
        """把这张动画树的**状态图**摊在节点上。

        ── 为什么要画在节点上(而不是只放侧栏)────────────────────────
        用户 2026-10-03 报"动画_状态机未能正常工作",而真正的原因是
        他的转场少接了一根线 —— 节点上什么提示都没有,他只能反复导出
        去 Godot 里试。图和问题直接画在节点上,一眼就能对上。
        """
        tree = find_animation_tree(getattr(self, "gk_tree_name", ""))
        if tree is None:
            layout.label(text="还没有动画树 —— 点『打开/新建动画树』",
                         icon="ERROR")
            return
        graph = anim_tree_graph(tree)
        states = [s for s in graph["states"] if anim_state_name(s)]
        column = layout.column(align=True)
        entry = graph["entry"]
        column.label(text=f"{len(states)} 状态 / {len(graph['edges'])} 条转场",
                     icon="ARMATURE_DATA")
        for state in states[:8]:
            name = anim_state_name(state)
            here = "起始 " if state is entry else "     "
            icon = "PLAY" if state is entry else "FORWARD"
            column.label(text=f"{here}{name}", icon=icon)
        if len(states) > 8:
            column.label(text=f"     …还有 {len(states) - 8} 个", icon="DOT")
        for problem in graph["problems"][:3]:
            # 节点上的字要短 —— 完整的话留给侧栏和导出报告
            column.label(text=short_problem(problem), icon="ERROR")
        if len(graph["problems"]) > 3:
            column.label(text=f"…还有 {len(graph['problems']) - 3} 条问题"
                              f"(见侧栏『游戏动画』)", icon="INFO")

# 用户要求:"做动画状态机取代手拼分支网" —— 这也是 UPBGE 那套最大的缺陷
# (它一次只能播一个动画,没有状态机)。
#
# ── 为什么转移表用**多行文本**而不是可编辑列表 ────────────────────
# 理想的 UI 是像检查器那样的可增删列表,那需要 `CollectionProperty`
# + 一个 `PropertyGroup` 类 + 一个 UIList。试下来的两个硬障碍:
#
#   1. `PropertyGroup` 必须自己注册成 Blender 类,而节点类是由
#      `finalize()` 统一登记的 —— 两者注册时机和通道不一样,
#      混在一起很容易"节点注册成功但属性组没注册",报一堆难懂的错。
#   2. 在节点体里画可增删列表要用 `UILayout.template_list`,而它需要一个
#      **PointerProperty 指向自身**才能定位列表容器 —— 节点类型上挂
#      PointerProperty 在 Blender 里是受限的。
#
# 所以改用多行文本。一行一条规则,格式故意做得极简:
#
#     idle -> run        当『有输入』为真时,idle 切到 run
#     run -> idle        条件是节点上的布尔引脚
#
# 好处:**看得出全部规则、能复制粘贴、能整块注释掉、diff 干净**。
# 代价:没有下拉选择状态名 —— 但状态名本来就是用户自己的字符串,
# 手打反而更自由(而且打错了导出时会报"目标状态没有对应的动画")。
@N
class GKNodeAnimStateMachine(B.GKStatementNode):
    bl_idname = "GKNodeAnimStateMachine"
    # ── 标签必须和新的引用节点区分开(踩过的坑)────────────────────
    # 两个节点的标签都叫『动画_状态机』,菜单里**看起来一模一样**,
    # 用户截图问"这怎么有两个动画状态机"。
    # 这个是把状态表塞进单节点的旧版,用户已经明确否掉了
    # ("这也太简陋了吧...后面就密密麻麻的"),所以标上『旧』。
    bl_label = "动画_状态表(旧)"
    bl_icon = "AUTOMERGE_ON"
    # 挪到单独的『动画(旧)』分类,和新的动画树节点彻底分开,
    # 免得用户在『动画』里又选错。
    gk_category = "动画(旧)"
    gk_doc = ("【旧版,建议改用『动画_状态机』+ 独立动画树】"
              "把状态表直接写在节点里。东西一多节点就糊成一团,"
              "所以新方案改成了独立的动画树。留着是为了兼容已经用了它的图")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(TEXT, "起始状态", "idle"),)
    gk_outputs = (S.pin(FLOW, "完成"),)

    gk_prop_defs = (
        ("gk_states", "string", "idle\nrun", "状态列表",
         "这个角色有哪些状态,一行一个(只是给『选…』按钮和导出的常量用)"),
        ("gk_transitions", "string", "idle -> run\nrun -> idle", "状态表",
         "一行一条规则,格式:`起始状态 -> 目标状态`"),
        ("gk_player", "node", "", "动画播放器", "留空 = 自动查找"),
        ("gk_check_mode", "enum", "EVERY_FRAME", "检查时机",
         "每个物理帧判一次 / 只在切换后判一次",
         (("EVERY_FRAME", "每帧检查", "动作连贯,适合走跑切换"),
          ("ON_ENTER", "只在进入时判", "省性能,适合一次性判定"))),
        ("gk_state_var", "string", "anim_state", "状态变量名",
         "当前状态存在哪个成员变量里"),
    )

    def build_sockets(self):
        """按状态表动态生成条件引脚。

        每条规则一个布尔输入引脚,名字就是 `起始→目标`。
        """
        self.inputs.clear()
        for spec in self.gk_inputs:
            socket = self.inputs.new(spec["kind"][0], spec["name"])
            if spec["default"] is not None:
                try:
                    socket.default_value = spec["default"]
                except Exception:
                    pass
        self.outputs.clear()
        for spec in self.gk_outputs:
            socket = self.outputs.new(spec["kind"][0], spec["name"])
            if spec["default"] is not None:
                try:
                    socket.default_value = spec["default"]
                except Exception:
                    pass
        for rule in parse_transitions(getattr(self, "gk_transitions", "")):
            pin_name = transition_pin_name(rule[0], rule[1])
            if self.inputs.get(pin_name) is not None:
                continue      # 重复规则只建一个引脚
            socket = self.inputs.new(S.BOOL[0], pin_name)
            try:
                socket.default_value = False
            except Exception:
                pass

    def draw_buttons(self, context, layout):
        col = layout.column(align=True)
        col.label(text="状态表(一行:`起始 -> 目标`)")
        col.prop(self, "gk_transitions", text="")
        col.prop(self, "gk_check_mode", text="")
        rules = parse_transitions(getattr(self, "gk_transitions", ""))
        if rules:
            col.label(text=f"已生成 {len(rules)} 条转移", icon="INFO")
        else:
            col.label(text="还没有规则", icon="ERROR")


def parse_transitions(text):
    """把状态表文本解析成 [(起始, 目标), ...]。

    宽容处理:支持 `->` 和 `→`,忽略空行和 `#` 开头的注释行。
    解析不出来就跳过那一行,不抛异常 —— 用户正在打字时半截规则
    (只有 `idle ->`)不该让整个节点构建失败。
    """
    rules = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for arrow in ("->", "→"):
            if arrow in line:
                left, _sep, right = line.partition(arrow)
                start, end = left.strip(), right.strip()
                if start and end:
                    rules.append((start, end))
                break
    return rules


def transition_pin_name(start, end):
    """转移规则对应的引脚名。用箭头连接,用户一眼能对上。"""
    return f"{start}→{end}"


# ── 动画的其余常用操作 ──────────────────────────────────────────────
# 用户原话:"怎么测试骨骼和动画,这两个比较重要"。
# 实测(建了带骨架的角色,导出到 Godot 真跑)确认整条链路是通的:
#     Body.glb 里 animations=['RigAction'] skins=1
#     Godot 导入后 Hero/Model/AnimationPlayer,骨骼数=2,播放位置在推进,
#     骨骼旋转逐帧变化 —— 动画真的在驱动骨骼。
# 但当时节点图里只有『播放』和『切换状态』两个节点,做个游戏根本不够用,
# 所以把最常用的几个补齐。
@N
class GKNodeAnimPlaying(B.GKExpressionNode):
    bl_idname = "GKNodeAnimPlaying"
    bl_label = "动画_是否在播"
    bl_icon = "TIME"
    gk_category = "动画"
    gk_doc = ("某个动画片段是否正在播放(对应 AnimationPlayer.is_playing 组合"
              "current_animation)。留空动画名 = 只要有动画在播就算真")
    gk_prop_defs = (
        ("gk_player", "node", "", "动画播放器", "留空 = 自动查找"),
        ("gk_anim_name", "string", "", "动画名", "留空 = 任意动画"),
    )
    gk_outputs = (S.pin(BOOL, "在播", False),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_anim_name", text="")


@N
class GKNodeAnimPause(B.GKStatementNode):
    bl_idname = "GKNodeAnimPause"
    bl_label = "动画_暂停/继续"
    bl_icon = "PAUSE"
    gk_category = "动画"
    gk_doc = ("暂停或继续当前动画。暂停用 AnimationPlayer.pause()"
              "(停在当前位置,姿势保持不动);继续会从当前位置接着播。"
              "注:Godot 4.7 的 AnimationPlayer **没有 paused 属性**,"
              "所以这里用的是 pause() / play()")
    gk_inputs = (S.pin(FLOW, "执行"),)

    gk_prop_defs = (
        ("gk_player", "node", "", "动画播放器", "留空 = 自动查找"),
        ("gk_paused", "bool", True, "暂停", "勾上=暂停,取消=继续"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)



@N
class GKNodeAnimStop(B.GKStatementNode):
    bl_idname = "GKNodeAnimStop"
    bl_label = "动画_停止"
    bl_icon = "CANCEL"
    gk_category = "动画"
    gk_doc = "停止播放(对应 AnimationPlayer.stop),角色会回到静止姿势"
    gk_inputs = (S.pin(FLOW, "执行"),)

    gk_prop_defs = (("gk_player", "node", "", "动画播放器", "留空 = 自动查找"),)
    gk_outputs = (S.pin(FLOW, "完成"),)



@N
class GKNodeAnimSetSpeed(B.GKStatementNode):
    bl_idname = "GKNodeAnimSetSpeed"
    bl_label = "动画_设播放速度"
    bl_icon = "DRIVER"
    gk_category = "动画"
    gk_doc = ("改当前动画的播放速度(对应 AnimationPlayer.speed_scale)。"
              "0.5 = 慢动作,2.0 = 快放")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "速度", 1.0),)
    gk_input_props = {"速度": "gk_speed"}
    gk_prop_defs = (
        ("gk_player", "node", "", "动画播放器", "留空 = 自动查找"),
        ("gk_speed", "float", 1.0, "速度", "1.0 为原速"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_speed", text="")


@N
class GKNodeAnimLength(B.GKExpressionNode):
    bl_idname = "GKNodeAnimLength"
    bl_label = "动画_时长"
    bl_icon = "FULLSCREEN_ENTER"
    gk_category = "动画"
    gk_doc = ("某个动画片段有多长(秒)。做连招/计时的时候要先知道它多久。"
              "动画名不存在时返回 0")
    gk_prop_defs = (
        ("gk_player", "node", "", "动画播放器", "留空 = 自动查找"),
        ("gk_anim_name", "string", "idle", "动画名", ""),
    )
    gk_outputs = (S.pin(FLOAT, "秒", 0.0),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_anim_name", text="")


@N
class GKNodeAnimFinished(B.GKExpressionNode):
    bl_idname = "GKNodeAnimFinished"
    bl_label = "动画_播放完成"
    bl_icon = "CHECKMARK"
    gk_category = "动画"
    # 两张树里都能用:动画树里拼转场条件,玩法图里做"播完了就设变量/换状态"
    gk_tree_type = BOTH_TREES
    gk_doc = ("某段动画**播完了吗** —— 一次性动画播完那一刻起为真。"
              "『动画名』留空 = 只要有任意动画播完就算真。"
              "── 它能用在两处 ──"
              "动画树:和『逻辑_布尔运算』拼出「播完了**并且**在地面」这种复合转场条件;"
              "玩法图:`如果(动画_播放完成) -> 变量_设置`,做「播完就清标志/发奖励」。"
              "注:标志会一直保留到**下一段动画开始播**(进入新状态会自动清掉),"
              "所以不用担心『只在那一帧有效』而错过")
    gk_prop_defs = (
        ("gk_player", "node", "", "动画播放器", "留空 = 自动查找"),
        ("gk_anim_name", "string", "", "动画名", "留空 = 任意动画播完就算"),
        ("gk_anim_action", "action", None, "动作",
         "从文件里所有动作中选一个(原生选择器,能搜索)"),
    )
    gk_outputs = (S.pin(BOOL, "完成", False),)


    def draw_buttons(self, context, layout):
        # 和『动画_播放』同一套:选了动作就只显示名字,没选才给手填框
        layout.prop(self, "gk_anim_action", text="")
        if self.gk_anim_action is not None:
            layout.label(text=f"动画名:{self.gk_anim_action.name}",
                         icon="CHECKMARK")
        else:
            layout.prop(self, "gk_anim_name", text="", icon="SORTALPHA")


@N
class GKNodeSetState(B.GKStatementNode):
    bl_idname = "GKNodeSetState"
    bl_label = "动画_切换状态"
    bl_icon = "IMAGE_DATA"
    gk_category = "动画"
    gk_doc = "切换角色状态(idle / run / jump / attack / hurt …)"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(TEXT, "状态", "idle"),)
    gk_input_props = {"状态": "gk_state"}
    gk_prop_defs = (
        ("gk_state", "string", "idle", "状态名", "角色状态字符串"),
        ("gk_var", "string", "state", "状态变量名", "要写入的成员变量名"),
    )


# ==========================================================================
# 实例与场景
# ==========================================================================
@N
class GKNodeSpawn(B.GKStatementNode):
    bl_idname = "GKNodeSpawn"
    bl_label = "实例_生成"
    bl_icon = "ADD"
    gk_category = "实例"
    gk_doc = "实例化一个场景并放到指定位置(子弹 / 特效 / 敌人)"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(TEXT, "场景路径", "res://scenes/Bullet.tscn"),)
    gk_input_props = {"场景路径": "gk_scene_path"}
    gk_prop_defs = (
        ("gk_scene_path", "string", "res://scenes/Bullet.tscn", "场景路径", "要实例化的 .tscn"),
        ("gk_x", "float", 0.0, "X", "生成位置"),
        ("gk_y", "float", 0.0, "Y", "生成位置"),
        ("gk_z", "float", 0.0, "Z", "生成位置"),
        ("gk_parent", "node", "", "父节点", "留空表示挂到当前场景根节点"),
    )
    gk_outputs = (S.pin(FLOW, "完成"), S.pin(ANY, "实例", None),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_scene_path", text="")


@N
class GKNodeQueueFree(B.GKStatementNode):
    bl_idname = "GKNodeQueueFree"
    bl_label = "实例_销毁"
    bl_icon = "TRASH"
    gk_category = "实例"
    gk_doc = "销毁节点(延迟到帧末，对应 queue_free)"
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (("gk_target", "node", TARGET_SELF, "目标", "要销毁的节点"),)

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)


@N
class GKNodeQuitGame(B.GKStatementNode):
    bl_idname = "GKNodeQuitGame"
    bl_label = "流程_退出游戏"
    bl_icon = "QUIT"
    gk_category = "流程"
    gk_doc = ("关掉游戏窗口(桌面版)。用户 2026-10-03 提的:"
              "开着游戏按 Esc 应该能退出 —— 那需要一个『按 Esc → 退出』的接法,"
              "这个节点就是那个『退出』。"
              "注意:网页 / 手机上没有窗口可关,那样只会停住逻辑")
    gk_inputs = (S.pin(FLOW, "执行"),)

    gk_prop_defs = (
        ("gk_delay", "float", 0.0, "延迟(秒)",
         "> 0 时先等一会儿再退(让退出动画/音效播完)"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_delay", text="延迟")


@N
class GKNodeLogicOutput(B.GKStatementNode):
    bl_idname = "GKNodeLogicOutput"
    bl_label = "逻辑_输出结果"
    bl_icon = "CHECKMARK"
    gk_category = "变量"
    gk_doc = ("**给这张逻辑图一个对外的结果**(用户 2026-10-03 的计划:"
              "『一个游戏逻辑最后应该输出布尔变量,以便与其他的游戏逻辑联动』)。"
              "名字 + 真假值 → 生成一个脚本变量(带 `输出_` 前缀),"
              "别的逻辑图 / 动画树用『变量_读取』读同一个名字即可 —— "
              "所有图编译成**同一个脚本**,所以这是跨图联动的通用做法。"
              "导出报告里会把这些输出列出来,方便对照")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(BOOL, "值", True),
    )
    gk_prop_defs = (
        ("gk_output_name", "string", "完成", "输出名",
         "别的图用『变量_读取』填 `输出_名字` 就能读到"),
        ("gk_note", "string", "", "说明", "只是给自己看的备注,不影响导出"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_output_name", text="输出名")
        layout.prop(self, "gk_note", text="", icon="INFO")


@N
class GKNodeReloadScene(B.GKStatementNode):
    bl_idname = "GKNodeReloadScene"
    bl_label = "实例_重载场景"
    bl_icon = "FILE_REFRESH"
    gk_category = "实例"
    gk_doc = "重新加载当前场景(重开一局 / 复活后重置关卡)"
    gk_inputs = (S.pin(FLOW, "执行"),)

    gk_prop_defs = (
        ("gk_slot", "string", "1", "存档槽", "留空则重载当前场景;填了则读取该存档槽"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_slot", text="")


@N
class GKNodeGetNode(B.GKExpressionNode):
    bl_idname = "GKNodeGetNode"
    bl_label = "取值_获取节点"
    bl_icon = "NODE_SEL"
    gk_category = "实例"
    gk_doc = "按路径获取场景里的节点"
    gk_outputs = (S.pin(OBJECT, "节点", None),)

    gk_prop_defs = (("gk_path", "node", TARGET_SELF, "节点", "从当前脚本所在节点开始"),)

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout, "gk_path")


@N
class GKNodeIsOnFloor(B.GKExpressionNode):
    bl_idname = "GKNodeIsOnFloor"
    bl_label = "取值_是否在地面"
    bl_icon = "TRIA_DOWN"
    gk_category = "实例"
    gk_doc = "角色是否踩在地面(对应 is_on_floor)，做跳跃判定用"
    gk_outputs = (S.pin(BOOL, "在地面", True),)

    gk_prop_defs = (("gk_target", "node", TARGET_SELF, "目标", "角色节点"),)

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)


# ==========================================================================
# 变量与信号
# ==========================================================================
# 一张『变量_设置』卡片最多能写几个变量
SET_VAR_SLOTS = 6


@N
class GKNodeSetVar(B.GKStatementNode):
    bl_idname = "GKNodeSetVar"
    bl_label = "变量_设置"
    bl_icon = "CHECKBOX_DEHLT"
    gk_category = "变量"
    # ── 一张卡片设多个变量(用户 2026-10-03 的要求)──────────────────
    # 用户原话:"在设置变量属性时,卡片里可以设置多个并且引脚可连接不同节点,
    # 如虚幻蓝图的变量节点可重复连接"。
    #
    # 蓝图的 Set 节点展开后就是一串变量,每个有自己的值引脚 ——
    # 我们照这个做成『数量』可调:值 1 / 值 2 / 值 3 …,每个引脚
    # **可以接不同的上游节点**(一个输入引脚只接一条线,所以要多接就加数量)。
    # 第 1 个仍然叫『值』、属性仍然叫 `gk_var_name`/`gk_value` ——
    # 老文件里已经接好的线不会断。
    gk_doc = ("给脚本变量赋值(首次出现会自动声明成成员变量)。"
              "『数量』调大就能**一次设好几个变量**,每个『值』引脚可以接"
              "不同的节点 —— 和虚幻蓝图的 Set 节点一样。"
              "执行流出口可以接判断节点(『如果_否则』),也可以扇出到好几处")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(ANY, "值", 100),)
    gk_input_props = {"值": "gk_value"}
    gk_prop_defs = (
        ("gk_var_count", "int", 1, "数量",
         f"一次设几个变量(1~{SET_VAR_SLOTS})—— 引脚会长出 值 2 / 值 3 …",
         None, {"min": 1, "max": SET_VAR_SLOTS}),
        ("gk_var_name", "string", "health", "变量名", "GDScript 成员变量名"),
        ("gk_value", "float", 100.0, "值", "未连线时的值"),
    )
    # 第 2~N 个变量的属性(数量够了才画/才用)
    for _slot in range(2, SET_VAR_SLOTS + 1):
        gk_prop_defs += (
            (f"gk_var_name_{_slot}", "string", "", f"变量名 {_slot}", ""),
            (f"gk_value_{_slot}", "float", 0.0, f"值 {_slot}", ""),
        )

    def build_sockets(self):
        """按『数量』生成引脚:执行 / 值 / 值 2 / 值 3 …"""
        count = max(1, min(SET_VAR_SLOTS,
                           int(getattr(self, "gk_var_count", 1) or 1)))
        self.inputs.clear()
        self.inputs.new(S.FLOW[0], "执行")
        for index in range(count):
            name = "值" if index == 0 else f"值 {index + 1}"
            socket = self.inputs.new(S.ANY[0], name)
            # 引脚默认值 = 节点上那个数字框 —— 界面上看得见,
            # 生成器没连线时也能拿到正确的值(不给的话是 None -> `= null`)
            suffix = "" if index == 0 else f"_{index + 1}"
            try:
                socket.default_value = getattr(self, f"gk_value{suffix}", 0.0)
            except Exception:
                pass
        self.outputs.clear()
        for spec in self.gk_outputs:
            socket = self.outputs.new(spec["kind"][0], spec["name"])
            if spec["default"] is not None:
                try:
                    socket.default_value = spec["default"]
                except Exception:
                    pass

    def slots(self):
        """(变量名, 属性里的值) 列表,只取填了名字的那些。"""
        out = []
        for index in range(1, SET_VAR_SLOTS + 1):
            suffix = "" if index == 1 else f"_{index}"
            name = str(getattr(self, f"gk_var_name{suffix}", "") or "").strip()
            if not name:
                continue
            out.append((index, name, getattr(self, f"gk_value{suffix}", 0.0)))
        return out

    def draw_buttons(self, context, layout):
        count = max(1, min(SET_VAR_SLOTS,
                           int(getattr(self, "gk_var_count", 1) or 1)))
        row = layout.row(align=True)
        row.prop(self, "gk_var_count", text="变量")
        row.prop(self, "gk_var_name", text="", icon="SORTALPHA")
        row.prop(self, "gk_value", text="")
        for index in range(2, count + 1):
            row = layout.row(align=True)
            row.label(text=f"{index}.", icon="BLANK1")
            row.prop(self, f"gk_var_name_{index}", text="")
            row.prop(self, f"gk_value_{index}", text="")


@N
class GKNodeGetVar(B.GKExpressionNode):
    bl_idname = "GKNodeGetVar"
    bl_label = "变量_读取"
    bl_icon = "CHECKBOX_HLT"
    gk_category = "变量"
    gk_doc = "读取脚本变量"
    gk_outputs = (S.pin(ANY, "值", None),)

    gk_prop_defs = (("gk_var_name", "string", "health", "变量名", "GDScript 成员变量名"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_var_name", text="")


@N
class GKNodeEmitSignal(B.GKStatementNode):
    bl_idname = "GKNodeEmitSignal"
    bl_label = "信号_发出"
    bl_icon = "RADIOBUT_ON"
    gk_category = "信号"
    gk_doc = "发出信号通知其它节点"
    gk_inputs = (S.pin(FLOW, "执行"),)

    gk_prop_defs = (
        ("gk_signal_name", "string", "died", "信号名", "本脚本声明的信号"),
        ("gk_signal_declare", "bool", True, "自动声明", "在脚本顶部自动 signal 声明"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_signal_name", text="")


@N
class GKNodeConnect(B.GKStatementNode):
    bl_idname = "GKNodeConnect"
    bl_label = "信号_连接"
    bl_icon = "LINKED"
    gk_category = "信号"
    gk_doc = "把目标节点的信号连到本脚本的函数上"
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "目标", "发出信号的节点"),
        ("gk_signal_name", "string", "pressed", "信号名", "对方节点上的信号"),
        ("gk_handler", "string", "on_signal", "响应函数", "本脚本里要调用的函数名"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_signal_name", text="")
        row.prop(self, "gk_handler", text="")


# ==========================================================================
# 输入
# ==========================================================================
# ── 为什么改成"一个节点 + 触发方式"(用户 2026-10-03,对着 UPBGE 截图)──
# 用户原话:"在输入按键时,按下键盘任意键即可设置哪个键盘控制输入等,
# 如 upbge 的 keyboard key 设计逻辑"。
#
# UPBGE 的 Keyboard Key 就一个节点:一个 `Tap` 下拉(按下/按住/松开)+
# 一个 `Press & Choose` 按钮(按哪个键就是哪个)。我们原来是**两个**节点
# (『按键按住』『按键按下』)而且只能**打字**填动作名 ——
# 用户得先记住"jump 绑空格、move_right 绑 D"这套约定。
#
# 现在合成一个 『输入_按键』:
#   * 『触发方式』:按住 / 刚按下 / 松开(一个节点顶三个)
#   * 『按键』:按一下键盘上任意键就绑上(不用记名字)
#   * 『输入动作』:导出到 Godot 的映射名;留空 = 按按键名自动生成
@N
class GKNodeKey(B.GKExpressionNode):
    bl_idname = "GKNodeKey"
    bl_label = "输入_按键"
    bl_icon = "HAND"
    gk_category = "输入"
    gk_tree_type = BOTH_TREES
    gk_doc = ("按一下键盘就知道你按的是哪个键(**不用记动作名**)。"
              "『触发方式』一个节点顶三个:按住 / 刚按下(这一帧)/ 松开。"
              "── 两个填法,任选 ──"
              "(1) 点右边那个『按一下键』按钮,按哪个键就绑哪个;"
              "(2) 『输入动作』填 Godot 输入映射里的动作名 —— 用 move_*/jump "
              "这类名字导出时会自动绑好默认键。"
              "留空 = 按你捕获的那个键自动生成动作名。"
              "动画树的转场条件也能直接用(两张图都能放)")
    gk_prop_defs = (
        ("gk_key", "string", "", "按键",
         "捕获到的键(点右边的按钮,按一下你想用的键)"),
        ("gk_action", "string", "", "输入动作",
         "留空 = 按按键名自动生成(比如 W -> key_w)"),
        ("gk_trigger", "enum", "PRESSED", "触发方式",
         "和 UPBGE 的 Tap 一个意思",
         (("PRESSED", "按住", "按住期间一直为真"),
          ("JUST", "刚按下", "只在这一帧为真 —— 跳跃、攻击用这个"),
          ("RELEASED", "松开", "松手那一刻为真 —— 蓄力、拖拽用这个"))),
    )
    gk_outputs = (S.pin(BOOL, "结果", False),)


    def resolved_action(self):
        """导出用的输入动作名:填了就用填的,没填按按键名生成。"""
        raw = str(getattr(self, "gk_action", "") or "").strip()
        if raw:
            return raw
        key = str(getattr(self, "gk_key", "") or "").strip()
        if not key:
            return "jump"
        return "key_" + snake_case(key, "key").lower()

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_trigger", text="")
        # 捕获按钮:按一下键盘上任意键就绑上(和 UPBGE 的 Press & Choose 一样)
        capture = row.operator("gk.capture_key", text="", icon="KEYINGSET")
        capture.node_name = self.name
        row = layout.row(align=True)
        row.prop(self, "gk_key", text="按键", icon="SORTALPHA")
        row.prop(self, "gk_action", text="", icon="DOT")
        if not str(getattr(self, "gk_key", "") or "").strip():
            layout.label(text="点右边的按钮,按一下你要用的键", icon="INFO")


@N
class GKNodeActionPressed(B.GKExpressionNode):
    bl_idname = "GKNodeActionPressed"
    bl_label = "输入_按键按住(旧)"
    bl_icon = "HAND"
    gk_category = "输入"
    # 被『输入_按键』取代(那个能捕获按键 + 选触发方式)。
    # 隐藏起来但仍然能加载、能生成代码 —— 老图不会坏(见 node_menu_entries 的说明)。
    gk_hidden = True
    gk_doc = ("【旧版,建议改用『输入_按键』】动作按键是否按住。"
              "默认 move_right(导出时自动绑到 D)")
    gk_prop_defs = (("gk_action", "string", "move_right", "动作名",
                     "Godot 输入映射里的动作名。用 move_*/jump 这类名字"
                     "导出时会自动绑好按键"),)
    gk_outputs = (S.pin(BOOL, "按住", False),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_action", text="")


@N
class GKNodeActionJustPressed(B.GKExpressionNode):
    bl_idname = "GKNodeActionJustPressed"
    bl_label = "输入_按键按下(旧)"
    bl_icon = "MOUSE_LMB"
    gk_category = "输入"
    gk_hidden = True      # 同『输入_按键按住(旧)』
    gk_doc = ("【旧版,建议改用『输入_按键』】动作键是否刚按下(本帧)。"
              "默认 jump(导出时自动绑到空格)")
    # 默认 jump 而不是 Godot 内置的 ui_accept —— 后者绑的是**回车/空格**,
    # 但"跳跃用空格"更符合直觉,而且导出时会自动把空格绑上(见 DEFAULT_KEYS)
    gk_prop_defs = (("gk_action", "string", "jump", "动作名",
                     "Godot 输入映射里的动作名。用 move_*/jump 这类名字"
                     "导出时会自动绑好按键"),)
    gk_outputs = (S.pin(BOOL, "刚按下", False),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_action", text="")


# ==========================================================================
# 输入 / 鼠标(UPBGE 的『输入 ▸ 鼠标』那一栏,我们以前完全没有)
# ==========================================================================
@N
class GKNodeMouseButton(B.GKExpressionNode):
    bl_idname = "GKNodeMouseButton"
    bl_label = "鼠标_按键按住"
    bl_icon = "MOUSE_LMB"
    gk_category = "输入"
    gk_tree_type = BOTH_TREES
    gk_doc = "鼠标键是否按住(对应 Input.is_mouse_button_pressed)"
    gk_prop_defs = (
        ("gk_button", "enum", "LEFT", "哪个键", "",
         (("LEFT", "左键", ""), ("RIGHT", "右键", ""),
          ("MIDDLE", "中键", ""), ("MB4", "侧键 4", ""), ("MB5", "侧键 5", ""))),
    )
    gk_outputs = (S.pin(BOOL, "按住", False),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_button", text="")


@N
class GKNodeMouseButtonDown(B.GKExpressionNode):
    bl_idname = "GKNodeMouseButtonDown"
    bl_label = "鼠标_按键按下"
    bl_icon = "MOUSE_LMB"
    gk_category = "输入"
    gk_tree_type = BOTH_TREES
    gk_doc = ("鼠标键是否刚按下(这一帧)。"
              "Godot 的鼠标键**没有** just_pressed,所以这里靠记住上一帧实现")
    gk_prop_defs = (
        ("gk_button", "enum", "LEFT", "哪个键", "",
         (("LEFT", "左键", ""), ("RIGHT", "右键", ""),
          ("MIDDLE", "中键", ""), ("MB4", "侧键 4", ""), ("MB5", "侧键 5", ""))),
    )
    gk_outputs = (S.pin(BOOL, "刚按下", False),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_button", text="")


@N
class GKNodeMouseWheel(B.GKExpressionNode):
    bl_idname = "GKNodeMouseWheel"
    bl_label = "鼠标_滚轮"
    bl_icon = "MOUSE_MMB_SCROLL"
    gk_category = "输入"
    gk_doc = ("这一帧滚轮滚了多少(往上正、往下负)。"
              "做缩放 / 切武器用:接『逻辑_比较』判 > 0 或 < 0,"
              "或者直接加到相机 zoom 上")
    gk_outputs = (S.pin(FLOAT, "滚轮", 0.0),)



@N
class GKNodeMousePosition(B.GKExpressionNode):
    bl_idname = "GKNodeMousePosition"
    bl_label = "鼠标_位置"
    bl_icon = "CURSOR"
    gk_category = "输入"
    gk_doc = ("鼠标在屏幕上的位置(像素)。"
              "2D 游戏要换算到世界坐标的话接『实例_获取节点』拿到相机再算,"
              "或者直接用『屏幕』坐标做准星")
    gk_prop_defs = (
        ("gk_relative", "bool", False, "相对上一帧",
         "勾上 = 输出这一帧移动了多少(做第一人称视角用)"),
    )
    gk_outputs = (S.pin(VECTOR2, "位置", (0.0, 0.0)),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_relative", text="相对上一帧")


@N
class GKNodeMouseMotion(B.GKExpressionNode):
    bl_idname = "GKNodeMouseMotion"
    bl_label = "鼠标_移动量"
    bl_icon = "MOUSE_MOVE"
    gk_category = "输入"
    gk_doc = ("鼠标这一帧移动了多少(像素/帧)。"
              "第一人称/越肩视角转镜头用:`rotation.y -= 移动量.x * 灵敏度`。"
              "配合『鼠标_模式 = 锁定』才不会撞到屏幕边缘")
    gk_outputs = (S.pin(VECTOR2, "移动量", (0.0, 0.0)),)



@N
class GKNodeMouseMode(B.GKStatementNode):
    bl_idname = "GKNodeMouseMode"
    bl_label = "鼠标_模式"
    bl_icon = "MOUSE_MOVE"
    gk_category = "输入"
    gk_doc = ("设置鼠标模式。做 3D 视角用『锁定』(鼠标不会跑出窗口、也不显示),"
              "做菜单 / 2D 点选用『可见』。"
              "注意:锁定之后要用别的键才能退出来(比如 Esc)")
    gk_inputs = (S.pin(FLOW, "执行"),)

    gk_prop_defs = (
        ("gk_mode", "enum", "CAPTURED", "模式", "",
         (("VISIBLE", "可见", "正常显示,能点界面"),
          ("HIDDEN", "隐藏", "看不见但还能点(触摸屏/虚拟摇杆用)"),
          ("CAPTURED", "锁定", "藏起来并且锁在窗口里 —— 3D 转视角用"),
          ("CONFINED", "限制在窗口内", "看得见但不许跑出窗口"))),
        ("gk_visible_default", "bool", True, "可见", ""),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_mode", text="")


@N
class GKNodeInputDirection(B.GKExpressionNode):
    bl_idname = "GKNodeInputDirection"
    bl_label = "输入_方向"
    bl_icon = "ORIENTATION_GIMBAL"
    gk_category = "输入"
    gk_tree_type = BOTH_TREES
    gk_doc = ("**一个节点搞定移动方向** —— 上下左右四个键,直接给出"
              "『方向』(二维向量)/『左右』X /『前后』Y。"
              "接『移动_按方向』的『方向』引脚就能走,"
              "**不用再自己设变量拼向量**(虚幻蓝图 MoveForward / MoveRight "
              "那条路子,这里是内建的)。"
              "── 两种填法,混着用也行 ──"
              "(1) 每个键点『按一下键』直接捕获(不用记动作名);"
              "(2) 『动作』填 Godot 动作名 —— 手柄 / 虚拟摇杆走同一套动作,"
              "键盘手柄就自动互通了")
    gk_prop_defs = (
        ("gk_key_up", "string", "", "上键", "点『按一下键』捕获;留空用默认 W"),
        ("gk_action_up", "string", "", "上动作", "留空按按键自动生成"),
        ("gk_key_down", "string", "", "下键", ""),
        ("gk_action_down", "string", "", "下动作", ""),
        ("gk_key_left", "string", "", "左键", ""),
        ("gk_action_left", "string", "", "左动作", ""),
        ("gk_key_right", "string", "", "右键", ""),
        ("gk_action_right", "string", "", "右动作", ""),
        ("gk_deadzone", "float", 0.2, "死区",
         "摇杆回中不准时用;纯键盘可以填 0"),
        ("gk_normalize", "bool", True, "斜着走要归一化",
         "勾上 = 斜向速度不会变成 1.41 倍(和虚幻的 Normalize 一个意思)"),
    )
    gk_outputs = (
        S.pin(VECTOR2, "方向", (0.0, 0.0)),
        S.pin(FLOAT, "左右", 0.0),
        S.pin(FLOAT, "前后", 0.0),
        S.pin(BOOL, "有输入", False),
    )

    _DIRECTIONS = ("up", "down", "left", "right")

    def action_for(self, direction):
        """这个方向的输入动作名:填了用填的,没填按按键生成,再没填用默认。"""
        slot = {"up": ("gk_key_up", "gk_action_up", "move_up"),
                "down": ("gk_key_down", "gk_action_down", "move_down"),
                "left": ("gk_key_left", "gk_action_left", "move_left"),
                "right": ("gk_key_right", "gk_action_right", "move_right")}
        key_prop, action_prop, fallback = slot[direction]
        action = str(getattr(self, action_prop, "") or "").strip()
        if action:
            return action, str(getattr(self, key_prop, "") or "")
        key = str(getattr(self, key_prop, "") or "").strip()
        if key:
            return "key_" + key.lower(), key
        return fallback, ""

    def draw_buttons(self, context, layout):
        for label, direction in (("上", "up"), ("下", "down"),
                                 ("左", "left"), ("右", "right")):
            key_prop = f"gk_key_{direction}"
            action_prop = f"gk_action_{direction}"
            row = layout.row(align=True)
            row.label(text=f"{label}:")
            row.prop(self, key_prop, text="")
            capture = row.operator("gk.capture_key", text="", icon="KEYINGSET")
            capture.node_name = self.name
            capture.key_prop = key_prop
            capture.action_prop = action_prop
            row.prop(self, action_prop, text="", icon="DOT")
        row = layout.row(align=True)
        row.prop(self, "gk_deadzone", text="死区")
        row.prop(self, "gk_normalize", text="归一化")


@N
class GKNodeGetAxis(B.GKExpressionNode):
    bl_idname = "GKNodeGetAxis"
    bl_label = "输入_轴(旧)"
    gk_hidden = True     # 被『输入_方向』取代(那个一次给 x/y/向量)
    bl_icon = "ARROW_LEFTRIGHT"
    gk_category = "输入"
    gk_doc = ("取输入轴强度(-1 ~ 1)，用两个动作拼出一个轴。"
              "默认 move_left / move_right(导出时自动绑到 A / D)")
    gk_prop_defs = (
        ("gk_negative", "string", "move_left", "负方向动作",
         "负方向的动作名。默认 move_left(自动绑到 A)"),
        ("gk_positive", "string", "move_right", "正方向动作",
         "正方向的动作名。默认 move_right(自动绑到 D)"),
        ("gk_deadzone", "float", 0.2, "死区", "小于该值视为 0"),
    )
    gk_outputs = (S.pin(FLOAT, "强度", 0.0),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_negative", text="")
        row.prop(self, "gk_positive", text="")


@N
class GKNodeGetVector(B.GKExpressionNode):
    bl_idname = "GKNodeGetVector"
    bl_label = "输入_方向向量(旧)"
    gk_hidden = True     # 被『输入_方向』取代(那个能捕获按键 + 直接给 x/y)
    bl_icon = "ORIENTATION_GIMBAL"
    gk_category = "输入"
    gk_doc = ("取方向输入并合成向量(对应 Input.get_vector)。"
              "默认动作 move_up/down/left/right 导出时会自动绑到 W/S/A/D —— "
              "不用去 Godot 里手工配输入映射")
    # ── 默认值必须是 move_*,不能是 Godot 内置的 ui_*(踩过的坑)────────
    # `ui_left/right/up/down` 是 Godot 的**全局界面动作**,绑的是**方向键**,
    # 不是 WASD。用户按 WASD 完全没反应,只会得出"输入坏了"的结论
    # —— 而"WASD 走路"是所有人的第一直觉。
    # 用 move_* 的话,导出时会把这四个动作连**按键**一起写进 project.godot
    # (见 project.set_input_actions 的 DEFAULT_KEYS),开箱即用。
    # 想换键位就改这里的动作名,或者在 Godot 的输入映射里改。
    gk_prop_defs = (
        ("gk_left", "string", "move_left", "左", "动作名(导出时自动绑到 A)"),
        ("gk_right", "string", "move_right", "右", "动作名(导出时自动绑到 D)"),
        ("gk_up", "string", "move_up", "上", "动作名(导出时自动绑到 W)"),
        ("gk_down", "string", "move_down", "下", "动作名(导出时自动绑到 S)"),
        ("gk_deadzone", "float", 0.2, "死区", ""),
    )
    gk_outputs = (S.pin(VECTOR, "方向", (0.0, 0.0, 0.0)),)


    def draw_buttons(self, context, layout):
        grid = layout.grid_flow(row_major=True, columns=2, even_columns=True)
        grid.prop(self, "gk_left", text="")
        grid.prop(self, "gk_right", text="")
        grid.prop(self, "gk_up", text="")
        grid.prop(self, "gk_down", text="")


# ==========================================================================
# 运算(纯表达式，不需要执行流)
# ==========================================================================
@N
class GKNodeConstFloat(B.GKExpressionNode):
    bl_idname = "GKNodeConstFloat"
    bl_label = "常量_数值"
    bl_icon = "DOT"
    gk_category = "运算"
    gk_doc = "一个固定数值"
    gk_prop_defs = (("gk_value", "float", 0.0, "数值", ""),)
    gk_outputs = (S.pin(FLOAT, "值", 0.0),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_value", text="")


@N
class GKNodeConstText(B.GKExpressionNode):
    bl_idname = "GKNodeConstText"
    bl_label = "常量_文本"
    bl_icon = "FONT_DATA"
    gk_category = "运算"
    gk_doc = "一段固定文本"
    gk_prop_defs = (("gk_value", "string", "文本", "文本", ""),)
    gk_outputs = (S.pin(TEXT, "值", ""),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_value", text="")


@N
class GKNodeConstBool(B.GKExpressionNode):
    bl_idname = "GKNodeConstBool"
    bl_label = "常量_布尔"
    bl_icon = "CHECKBOX_HLT"
    gk_category = "运算"
    gk_doc = "真 / 假"
    gk_prop_defs = (("gk_value", "bool", True, "值", ""),)
    gk_outputs = (S.pin(BOOL, "值", False),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_value", text="")


@N
class GKNodeConstVector(B.GKExpressionNode):
    bl_idname = "GKNodeConstVector"
    bl_label = "常量_向量"
    bl_icon = "EMPTY_ARROWS"
    gk_category = "运算"
    gk_doc = "一个固定向量"
    gk_prop_defs = (
        ("gk_x", "float", 1.0, "X", ""),
        ("gk_y", "float", 0.0, "Y", ""),
        ("gk_z", "float", 0.0, "Z", ""),
        ("gk_dim", "enum", "V3", "维度", "", (("V2", "二维 Vector2", ""), ("V3", "三维 Vector3", ""))),
    )
    gk_outputs = (S.pin(VECTOR, "向量", (0.0, 0.0, 0.0)),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_x", text="X")
        row.prop(self, "gk_y", text="Y")
        row.prop(self, "gk_z", text="Z")


MATH_OPS = (
    ("ADD", "加 +", ""),
    ("SUB", "减 −", ""),
    ("MUL", "乘 ×", ""),
    ("DIV", "除 ÷", ""),
    ("MOD", "取余 %", ""),
    ("POW", "乘方 ^", ""),
    ("MIN", "取较小 min", ""),
    ("MAX", "取较大 max", ""),
    ("ABS", "绝对值 abs", ""),
    ("SQRT", "开方 sqrt", ""),
    ("SIN", "正弦 sin", ""),
    ("COS", "余弦 cos", ""),
    ("FLOOR", "向下取整 floor", ""),
    ("CEIL", "向上取整 ceil", ""),
    ("ROUND", "四舍五入 round", ""),
    ("CLAMP", "钳制 clamp", ""),
    ("LERP", "线性插值 lerp", ""),
    ("SIGN", "符号 sign", ""),
    ("RANDOM", "随机数 randf", ""),
    ("DEG2RAD", "角度转弧度 deg_to_rad", ""),
    ("RAD2DEG", "弧度转角度 rad_to_deg", ""),
)

MATH_TWO_ARG = {"ADD", "SUB", "MUL", "DIV", "MOD", "POW", "MIN", "MAX"}
MATH_THREE_ARG = {"CLAMP", "LERP"}
MATH_ONE_ARG = {"ABS", "SQRT", "SIN", "COS", "FLOOR", "CEIL", "ROUND", "SIGN", "DEG2RAD", "RAD2DEG"}


@N
class GKNodeMath(B.GKExpressionNode):
    bl_idname = "GKNodeMath"
    bl_label = "数学_运算"
    bl_icon = "DRIVER"
    gk_category = "运算"
    gk_doc = "基础数学运算:加减乘除、取整、钳制、插值、三角函数"
    gk_inputs = (
        S.pin(FLOAT, "A", 0.0),
        S.pin(FLOAT, "B", 0.0),
        S.pin(FLOAT, "C", 0.0),
    )
    gk_input_props = {"A": "gk_a", "B": "gk_b", "C": "gk_c"}
    gk_prop_defs = (
        ("gk_op", "enum", "ADD", "运算", "要执行的运算符", MATH_OPS),
        ("gk_a", "float", 0.0, "A", "未连线时使用"),
        ("gk_b", "float", 0.0, "B", "未连线时使用"),
        ("gk_c", "float", 0.0, "C", "未连线时使用(钳制 / 插值)"),
    )
    gk_outputs = (S.pin(FLOAT, "结果", 0.0),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_op", text="")

    def active_inputs(self):
        op = getattr(self, "gk_op", "ADD")
        if op in MATH_THREE_ARG:
            return ("A", "B", "C")
        if op in MATH_TWO_ARG:
            return ("A", "B")
        if op in MATH_ONE_ARG:
            return ("A",)
        return ("A",)


@N
class GKNodeCompare(B.GKExpressionNode):
    bl_idname = "GKNodeCompare"
    bl_label = "逻辑_比较"
    bl_icon = "ARROW_LEFTRIGHT"
    gk_category = "运算"
    # 和『逻辑_布尔运算』一样,动画树的转场条件也需要它(速度 > 阈值)
    gk_tree_type = ("GKGameLogicTree", "GKAnimationTree")
    gk_doc = "比较两个值，输出布尔。A / B 是动态类型，可接数值或文本"
    gk_inputs = (S.pin(ANY, "A", 0.0), S.pin(ANY, "B", 0.0),)
    gk_input_props = {"A": "gk_a", "B": "gk_b"}
    gk_prop_defs = (
        ("gk_op", "enum", "EQ", "比较方式", "", (
            ("EQ", "等于 ==", ""),
            ("NE", "不等于 !=", ""),
            ("GT", "大于 >", ""),
            ("GE", "大于等于 >=", ""),
            ("LT", "小于 <", ""),
            ("LE", "小于等于 <=", ""),
        )),
        ("gk_a", "string", "0", "A", "未连线时的字面量"),
        ("gk_b", "string", "0", "B", "未连线时的字面量"),
    )
    gk_outputs = (S.pin(BOOL, "结果", False),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_op", text="")


@N
class GKNodeLogic(B.GKExpressionNode):
    bl_idname = "GKNodeLogic"
    bl_label = "逻辑_布尔运算"
    bl_icon = "CHECKMARK"
    gk_category = "运算"
    # ── 两张树里都能用(用户要求的"逻辑是否、或者")──────────────────
    # 动画转场条件也常常是"A 与 B"(按住加速 && 在地面)。以前这个节点
    # 只在玩法图的菜单里,用户在动画树里根本拉不出来,只能回到玩法图
    # 拼好再想办法 —— 而两张图之间不能连线,等于做不了。
    gk_tree_type = ("GKGameLogicTree", "GKAnimationTree")
    gk_doc = ("与 / 或 / 非 / 异或。与、或可以按『输入数量』加引脚"
              "(A、B、C…),照蓝图那种『多输入 AND』")
    gk_inputs = (S.pin(BOOL, "A", False), S.pin(BOOL, "B", False),)
    gk_prop_defs = (
        ("gk_op", "enum", "AND", "运算", "", (
            ("AND", "与 and(A 且 B)", ""),
            ("OR", "或 or(A 或 B)", ""),
            ("NOT", "非 not(A)", "只用 A 一个输入"),
            ("XOR", "异或 xor(A、B 不同为真)", ""),
        ), {"update": B._on_pin_count_changed}),
        # 属性名里含 "count" -> node_base 自动给它挂"重建引脚"的回调
        ("gk_input_count", "int", 2, "输入数量",
         "与/或 有几个输入(2~8)", None, {"min": 2, "max": 8}),
    )
    gk_outputs = (S.pin(BOOL, "结果", False),)


    def build_sockets(self):
        """按运算方式重建输入引脚。

        NOT 只需要 A 一个;与/或/异或按『输入数量』长出 A、B、C…。
        『非』把 B 也画出来的话,用户会以为它有用 —— 实际被忽略,
        那是最容易误导人的画法。
        """
        op = str(getattr(self, "gk_op", "AND"))
        if op == "NOT":
            names = ["A"]
        else:
            count = max(2, min(8, int(getattr(self, "gk_input_count", 2) or 2)))
            names = [chr(ord("A") + index) for index in range(count)]
        self.inputs.clear()
        for name in names:
            socket = self.inputs.new(S.BOOL[0], name)
            socket.default_value = False
        self.outputs.clear()
        for spec in self.gk_outputs:
            self.outputs.new(spec["kind"][0], spec["name"])

    def active_inputs(self):
        """当前真正参与运算的输入引脚名(按顺序)。"""
        return [socket.name for socket in self.inputs]

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_op", text="")
        if str(getattr(self, "gk_op", "AND")) != "NOT":
            layout.prop(self, "gk_input_count", text="输入")


@N
class GKNodeRandomRange(B.GKExpressionNode):
    bl_idname = "GKNodeRandomRange"
    bl_label = "数学_随机整数"
    bl_icon = "MOD_PARTICLES"
    gk_category = "运算"
    gk_doc = "取 [最小, 最大) 之间的随机整数(对应 randi_range)"
    gk_inputs = (S.pin(INT, "最小", 0), S.pin(INT, "最大", 10),)
    gk_input_props = {"最小": "gk_min", "最大": "gk_max"}
    gk_prop_defs = (("gk_min", "int", 0, "最小", ""), ("gk_max", "int", 10, "最大", ""))
    gk_outputs = (S.pin(INT, "结果", 0),)



@N
class GKNodeVectorMake(B.GKExpressionNode):
    bl_idname = "GKNodeVectorMake"
    bl_label = "向量_合并"
    bl_icon = "ADD"
    gk_category = "运算"
    gk_doc = "把 X / Y / Z 合成一个向量，可切 2D / 3D"
    gk_prop_defs = (
        ("gk_x", "float", 0.0, "X", ""),
        ("gk_y", "float", 0.0, "Y", ""),
        ("gk_z", "float", 0.0, "Z", ""),
        ("gk_dim", "enum", "V3", "维度", "", (("V2", "二维 Vector2", ""), ("V3", "三维 Vector3", ""))),
    )
    # 引脚按 gk_dim 动态生成，见 build_sockets;这里保留声明供菜单/校验使用
    gk_inputs = (S.pin(FLOAT, "X", 0.0), S.pin(FLOAT, "Y", 0.0), S.pin(FLOAT, "Z", 0.0),)
    gk_input_props = {"X": "gk_x", "Y": "gk_y", "Z": "gk_z"}
    gk_outputs = (S.pin(VECTOR, "向量", (0.0, 0.0, 0.0)),)


    def build_sockets(self):
        dim = getattr(self, "gk_dim", "V3")
        wanted = ("X", "Y") if dim == "V2" else ("X", "Y", "Z")
        self.inputs.clear()
        self.outputs.clear()
        for spec in self.gk_inputs:
            if spec["name"] not in wanted:
                continue
            socket = self.inputs.new(spec["kind"][0], spec["name"])
            socket.default_value = 0.0
        self.outputs.new(S.VECTOR[0], "向量")

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_dim", text="")


@N
class GKNodeVectorSplit(B.GKExpressionNode):
    bl_idname = "GKNodeVectorSplit"
    bl_label = "向量_拆分"
    bl_icon = "ARROW_LEFTRIGHT"
    gk_category = "运算"
    gk_doc = "把向量拆成 X / Y / Z 三个数值"
    gk_inputs = (S.pin(VECTOR, "向量", (0.0, 0.0, 0.0)),)

    gk_outputs = (S.pin(FLOAT, "X", 0.0), S.pin(FLOAT, "Y", 0.0), S.pin(FLOAT, "Z", 0.0),)


@N
class GKNodeDistance(B.GKExpressionNode):
    bl_idname = "GKNodeDistance"
    bl_label = "取值_距离"
    bl_icon = "DRIVER_DISTANCE"
    gk_category = "运算"
    gk_doc = ("两个物体之间隔多远(米)。拾取/靠近/远离这类判定全靠它:"
              "『取值_距离』-> 『逻辑_比较』(< 1.2)-> 『如果_否则』。"
              "『甲』留空 = 脚本自己(所以\"离玩家多远\"只要填『乙』)。")
    gk_inputs = (
        S.pin(OBJECT, "甲", TARGET_SELF),
        S.pin(OBJECT, "乙", None),
    )
    gk_prop_defs = (
        ("gk_a", "node", "", "甲", "留空 = 脚本自己"),
        ("gk_b", "node", "", "乙", "另一个物体"),
    )
    gk_outputs = (S.pin(FLOAT, "距离", 0.0),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.label(text="甲")
        B.draw_target_picker(self, row, "gk_a")
        row = layout.row(align=True)
        row.label(text="乙")
        B.draw_target_picker(self, row, "gk_b")


@N
class GKNodeGetPosition(B.GKExpressionNode):
    bl_idname = "GKNodeGetPosition"
    bl_label = "取值_位置"
    bl_icon = "EMPTY_AXIS"
    gk_category = "运算"
    gk_doc = ("读一个物体现在在哪。输出**分开的 X / Y / Z**(要哪个接哪个),"
              "和『逻辑_比较』配合就能做\"掉出地图了吗\"\"走到终点了吗\"这类判定。")
    gk_inputs = (S.pin(OBJECT, "物体", TARGET_SELF),)
    gk_prop_defs = (
        ("gk_target", "node", "", "物体", "留空 = 脚本自己"),
        ("gk_global", "bool", True, "世界坐标",
         "勾上 = 世界坐标(推荐);取消 = 相对父节点"),
    )
    gk_outputs = (
        S.pin(FLOAT, "X", 0.0),
        S.pin(FLOAT, "Y", 0.0),
        S.pin(FLOAT, "Z", 0.0),
    )

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)
        layout.prop(self, "gk_global", text="世界坐标")


@N
class GKNodeSetPosition(B.GKStatementNode):
    bl_idname = "GKNodeSetPosition"
    bl_label = "移动_设置位置"
    # 图标必须是 Blender 认的枚举值:"TRANSFORM_MOVE" 不在列表里,
    # 会让**整个节点注册失败**(排查了半天:导出时报"尚未定义节点类型")
    bl_icon = "TRANSFORM_ORIGINS"
    gk_category = "移动"
    gk_doc = ("把物体**直接摆到**某个位置(不同于『移动_按方向』那种逐帧推)。"
              "掉出地图送回出生点、传送门、重置关卡都用它。"
              "三个数值可以连线,也能在节点上填。")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "X", 0.0),
        S.pin(FLOAT, "Y", 0.0),
        S.pin(FLOAT, "Z", 0.0),
    )
    gk_input_props = {"X": "gk_x", "Y": "gk_y", "Z": "gk_z"}
    gk_prop_defs = (
        ("gk_x", "float", 0.0, "X", ""),
        ("gk_y", "float", 0.0, "Y", ""),
        ("gk_z", "float", 0.0, "Z", "2D 里不用填"),
        ("gk_relative", "bool", False, "相对现在的位置",
         "勾上 = 在当前位置上再加这三个数(位移)"),
        ("gk_target", "node", "", "物体", "留空 = 脚本自己"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_x", text="X")
        row.prop(self, "gk_y", text="Y")
        row.prop(self, "gk_z", text="Z")
        layout.prop(self, "gk_relative", text="相对")
        B.draw_target_picker(self, layout)


@N
class GKNodeTextJoin(B.GKExpressionNode):
    bl_idname = "GKNodeTextJoin"
    bl_label = "文本_拼接"
    bl_icon = "SORT_ASC"
    gk_category = "运算"
    gk_doc = ("把两段文字接起来(A + B)。HUD 上显示\"金币 3/5\"这种做法:"
              "『变量_读取』-> 『文本_拼接』-> 『界面_文本』的『文字』。"
              "数字会自动转成文字,不用手动转换。")
    gk_inputs = (
        S.pin(TEXT, "甲", ""),
        S.pin(TEXT, "乙", ""),
    )
    gk_prop_defs = (
        ("gk_a", "string", "", "甲", "没连线时用这段文字"),
        ("gk_b", "string", "", "乙", ""),
        ("gk_middle", "string", "", "中间加", "比如一个空格或斜杠"),
    )
    gk_outputs = (S.pin(TEXT, "结果", ""),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_a", text="")
        row.prop(self, "gk_middle", text="")
        row.prop(self, "gk_b", text="")


@N
class GKNodeVectorLength(B.GKExpressionNode):
    bl_idname = "GKNodeVectorLength"
    bl_label = "向量_长度"
    bl_icon = "DRIVER_DISTANCE"
    gk_category = "运算"
    gk_doc = "向量长度(模)"
    gk_inputs = (S.pin(VECTOR, "向量", (0.0, 0.0, 0.0)),)

    gk_outputs = (S.pin(FLOAT, "长度", 0.0),)



@N
class GKNodeVectorNormalize(B.GKExpressionNode):
    bl_idname = "GKNodeVectorNormalize"
    bl_label = "向量_归一化"
    bl_icon = "SNAP_ON"
    gk_category = "运算"
    gk_doc = "把向量变成单位长度(保留方向)"
    gk_inputs = (S.pin(VECTOR, "向量", (0.0, 0.0, 0.0)),)

    gk_outputs = (S.pin(VECTOR, "单位向量", (0.0, 0.0, 0.0)),)



@N
class GKNodeConvert(B.GKExpressionNode):
    bl_idname = "GKNodeConvert"
    bl_label = "转换_类型"
    bl_icon = "FILE_REFRESH"
    gk_category = "运算"
    gk_doc = "在 数值 / 整数 / 文本 / 布尔 / 向量 之间转换"
    gk_inputs = (S.pin(ANY, "输入", 0.0),)

    gk_prop_defs = (
        ("gk_to", "enum", "float", "转为", "", (
            ("float", "数值 float", ""),
            ("int", "整数 int", ""),
            ("String", "文本 String", ""),
            ("bool", "布尔 bool", ""),
            ("Vector2", "二维向量 Vector2", ""),
            ("Vector3", "三维向量 Vector3", ""),
        )),
    )
    gk_outputs = (S.pin(ANY, "输出", None),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_to", text="")


# ==========================================================================
# 显示
# ==========================================================================
@N
class GKNodeSetVisible(B.GKStatementNode):
    bl_idname = "GKNodeSetVisible"
    bl_label = "显示_设置可见"
    bl_icon = "HIDE_OFF"
    gk_category = "显示"
    gk_doc = "显示或隐藏一个节点(2D / 3D 通用，对应 visible)"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(BOOL, "可见", True),)
    gk_input_props = {"可见": "gk_visible"}
    gk_prop_defs = (
        ("gk_visible", "bool", True, "可见", "未连线时用它决定显示还是隐藏"),
        ("gk_target", "node", TARGET_SELF, "目标", "要显示 / 隐藏的节点"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_visible", text="", toggle=True,
                 icon="HIDE_OFF" if getattr(self, "gk_visible", True) else "HIDE_ON")
        B.draw_target_picker(self, layout)


@N
class GKNodeSetPaused(B.GKStatementNode):
    bl_idname = "GKNodeSetPaused"
    bl_label = "显示_暂停节点"
    bl_icon = "PAUSE"
    gk_category = "显示"
    gk_doc = "暂停 / 恢复一个节点的处理(对应 process_mode，不需要自己写计数逻辑)"
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_paused", "bool", True, "暂停", "勾上=停止该节点的 _process / _physics_process"),
        ("gk_target", "node", TARGET_SELF, "目标", "要暂停 / 恢复的节点"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_paused", text="", toggle=True,
                 icon="PAUSE" if getattr(self, "gk_paused", True) else "PLAY")
        B.draw_target_picker(self, layout)


# ==========================================================================
# 相机
# ==========================================================================
# ==========================================================================
# 摄像机套件(照虚幻蓝图 / UE 的 Camera + SpringArm 设计)
# ==========================================================================
# ── 为什么要重做一套(用户 2026-10-03 第四轮)──────────────────────
# 用户原话:"进行摄像机的设计:摄像机的设置,切换,启用与禁用,视距,视野,
# 分支的场景摄像机的设置,切换,启用与禁用,弹簧臂的设计,摄像机的旋转,
# 缩放,位置等向量,浮点,两变量的设计,同样是按照虚幻蓝图进行"。
#
# 虚幻里的对应关系(我们照这个来):
#     CameraComponent.FieldOfView            -> 『相机_缩放』(视野 / 缩放)
#     CameraComponent.Activate / Deactivate  -> 『相机_启用』
#     Set View Target with Blend             -> 『相机_切换』(过渡秒数)
#     SpringArm.TargetArmLength              -> 『相机_弹簧臂』臂长(视距)
#     SpringArm.SocketOffset / TargetOffset  -> 『相机_弹簧臂』偏移
#     SpringArm.bDoCollisionTest             -> 『相机_弹簧臂』碰撞检测
#     SpringArm.CameraLag                    -> 『相机_弹簧臂』平滑
#     SetActorLocation / SetActorRotation    -> 『相机_位置』/『相机_旋转』
#     GetCameraComponent 的各种取值           -> 『相机_取状态』
#
# 每个"设"节点都有『保留』(相对)和『直接设』(绝对)两种模式,和虚幻的
# SetActorLocation(Teleport / Sweep)一个思路:相对 = 在现在的基础上加。
@N
class GKNodeCameraActive(B.GKStatementNode):
    bl_idname = "GKNodeCameraActive"
    bl_label = "相机_启用"
    bl_icon = "CAMERA_DATA"
    gk_category = "相机"
    gk_doc = ("启用 / 禁用一个相机(照虚幻的 Activate / Deactivate)。"
              "『启用』= 切到它;『禁用』= 如果它正开着就交还控制权"
              "(画面会黑一下,所以一般接着『相机_切换』到另一台)。"
              "场景里有多台相机时,用『如果_否则』分场景挑一台 —— 这就是"
              "'分支的场景摄像机设置'")
    gk_inputs = (
        S.pin(FLOW, "执行"),
    )
    gk_prop_defs = (
        ("gk_active", "bool", True, "启用", "关掉 = 禁用(把它从当前相机上摘下来)"),
        ("gk_target", "node", "", "相机", "留空 = 用场景里的当前相机"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_active", text="启用" if self.gk_active else "禁用",
                 toggle=True, icon="CHECKBOX_HLT" if self.gk_active
                 else "CHECKBOX_DEHLT")
        B.draw_target_picker(self, row)


@N
class GKNodeCameraSetup(B.GKStatementNode):
    bl_idname = "GKNodeCameraSetup"
    bl_label = "相机_设置"
    bl_icon = "PREFERENCES"
    gk_category = "相机"
    gk_doc = ("一次把一台相机设好:**视野**(或 2D 的缩放)、**视距**"
              "(沿相机朝向推远/拉近多少)、朝向(可选)。"
              "带『平滑』就是渐变过去,0 = 立刻生效。"
              "场景里每台相机各放一个『相机_设置』,用分支挑 —— "
              "这就是多场景机位的做法")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "视野", 0.0),
        S.pin(FLOAT, "视距", 0.0),
    )
    gk_input_props = {"视野": "gk_fov", "视距": "gk_distance"}
    gk_prop_defs = (
        ("gk_fov", "float", 0.0, "视野",
         "3D = 垂直视场角(度);2D = 缩放倍数。0 = 不动它"),
        ("gk_distance", "float", 0.0, "视距",
         "沿相机当前朝向推远(正)/拉近(负)多少米。0 = 不动它"),
        ("gk_smooth", "float", 0.0, "平滑", "秒;0 = 立刻生效"),
        ("gk_target", "node", "", "相机", "留空 = 场景里的当前相机"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_fov", text="视野")
        row.prop(self, "gk_distance", text="视距")
        row.prop(self, "gk_smooth", text="秒")
        B.draw_target_picker(self, layout)


@N
class GKNodeCameraPosition(B.GKStatementNode):
    bl_idname = "GKNodeCameraPosition"
    bl_label = "相机_位置"
    bl_icon = "EMPTY_ARROWS"
    gk_category = "相机"
    gk_doc = ("设相机位置(照虚幻的 SetActorLocation)。"
              "**两种给法,接哪种用哪种**:①『位置』引脚接一个『向量_合并』;"
              "②用 X / Y / Z 三个浮点分别给。"
              "『保留』= 在当前位置上加(相对),『直接设』= 换成这个坐标(绝对)")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(VECTOR, "位置", (0.0, 0.0, 0.0)),
        S.pin(FLOAT, "X", 0.0),
        S.pin(FLOAT, "Y", 0.0),
        S.pin(FLOAT, "Z", 0.0),
    )
    gk_input_props = {"X": "gk_x", "Y": "gk_y", "Z": "gk_z"}
    gk_prop_defs = (
        ("gk_mode", "enum", "ADD", "怎么用", "",
         (("ADD", "保留(在当前位置上加)", "相对 —— 和虚幻的 AddActorLocalOffset 一个意思"),
          ("SET", "直接设(换成这个坐标)", "绝对 —— 和 SetActorLocation 一个意思"))),
        ("gk_smooth", "float", 0.0, "平滑", "秒;0 = 立刻到位"),
        ("gk_x", "float", 0.0, "X", "没接『位置』向量时用它"),
        ("gk_y", "float", 0.0, "Y", ""),
        ("gk_z", "float", 0.0, "Z", ""),
        ("gk_target", "node", "", "相机", "留空 = 场景里的当前相机"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_mode", text="")
        row.prop(self, "gk_smooth", text="秒")
        row = layout.row(align=True)
        row.prop(self, "gk_x", text="X")
        row.prop(self, "gk_y", text="Y")
        row.prop(self, "gk_z", text="Z")
        B.draw_target_picker(self, layout)


@N
class GKNodeCameraRotation(B.GKStatementNode):
    bl_idname = "GKNodeCameraRotation"
    bl_label = "相机_旋转"
    bl_icon = "ORIENTATION_LOCAL"
    gk_category = "相机"
    gk_doc = ("设相机朝向(照虚幻的 SetActorRotation),用偏航 / 俯仰 / 翻滚"
              "三个浮点给值(单位度)。"
              "第一人称转视角就是每帧『保留』+ 给一点增量;"
              "想反转某一轴,勾下面的『反转』,或者把增量填成负数。")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        # 『旋转』向量引脚**删掉**(用户两次提"两套给法冗余"):
        # 偏航/俯仰/翻滚三个浮点已经覆盖全部用法,而且在节点上就能填,
        # 不用先拉一个『向量_合并』。老图里接在这个引脚上的线会断
        # (引脚不存在了),需要改接到三个浮点上。
        S.pin(FLOAT, "偏航", 0.0),
        S.pin(FLOAT, "俯仰", 0.0),
        S.pin(FLOAT, "翻滚", 0.0),
    )
    gk_input_props = {"偏航": "gk_yaw", "俯仰": "gk_pitch", "翻滚": "gk_roll"}
    gk_prop_defs = (
        ("gk_mode", "enum", "ADD", "怎么用", "",
         (("ADD", "保留(在现有朝向上加)", "相对 —— 转视角用这个"),
          ("SET", "直接设(换成这个朝向)", "绝对 —— 摆机位用这个"))),
        ("gk_smooth", "float", 0.0, "平滑", "秒;0 = 立刻转过去"),
        ("gk_yaw", "float", 0.0, "偏航", "左右转(度);接『鼠标_移动量』的 X 就是转视角"),
        ("gk_pitch", "float", 0.0, "俯仰", "上下看(度);正 = 抬头"),
        ("gk_roll", "float", 0.0, "翻滚", "歪头(度)"),
        ("gk_clamp_pitch", "bool", True, "限制俯仰 ±89°",
         "勾上 = 不会翻过头(第一人称一定要勾)"),
        # ── 反转开关(用户 2026-10-03:"可以自定义方向,但默认跟随鼠标")──
        # 默认跟随鼠标(往右移 -> 视角往右)。想反过来(飞行模拟那种
        # "上移 = 低头")就勾上,不用再去改数学节点的符号。
        ("gk_invert_x", "bool", False, "反转左右",
         "勾上 = 鼠标往右移视角往左(默认跟随鼠标)"),
        ("gk_invert_y", "bool", False, "反转上下",
         "勾上 = 鼠标往上移视角往下(默认跟随鼠标)"),
        ("gk_target", "node", "", "相机", "留空 = 场景里的当前相机"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_mode", text="")
        row.prop(self, "gk_smooth", text="秒")
        row = layout.row(align=True)
        row.prop(self, "gk_yaw", text="偏航")
        row.prop(self, "gk_pitch", text="俯仰")
        row.prop(self, "gk_roll", text="翻滚")
        B.draw_target_picker(self, layout)


@N
class GKNodeCameraSpringArm(B.GKStatementNode):
    bl_idname = "GKNodeCameraSpringArm"
    bl_label = "相机_弹簧臂"
    bl_icon = "CON_FOLLOWPATH"
    gk_category = "相机"
    gk_doc = ("**虚幻的弹簧臂(SpringArm)**:相机挂在一条『会缩的杆子』上 —— "
              "平时退到『臂长』那么远,杆子撞到墙就自动缩短,所以镜头**不会"
              "穿墙**。第三人称/越肩视角的标准做法。"
              "参数对照虚幻:臂长 = TargetArmLength,高度/侧向偏移 = "
              "SocketOffset,俯仰 = 杆子往下压的角度,碰撞检测 = "
              "bDoCollisionTest,平滑 = CameraLag。"
              "引擎里【每物理帧】更新一次(和角色同一个时钟,画面才不抖)。"
              "── 左边接引脚就能**运行中改**(不接就用节点上填的值)──"
              "『杆子挂在哪』『臂长』『偏移』『俯仰』四个都能接线;"
              "右边还能**读出来**:枢轴 / 相机位置 / 方向(做准星、判断有没有"
              "被挡住都用得上)")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        # ── 引脚优先于属性(和全项目一致):接了线就用线那头的值 ──────
        S.pin(OBJECT, "弹簧臂"),
        S.pin(FLOAT, "臂长", 4.0),
        S.pin(VECTOR, "偏移", (0.0, 1.5, 0.5)),
        S.pin(FLOAT, "俯仰", -15.0),
    )
    gk_input_props = {"臂长": "gk_arm", "俯仰": "gk_pitch"}
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "跟随谁",
         "杆子挂在谁身上(默认脚本所在节点)"),
        ("gk_arm_node", "node", "", "弹簧臂",
         "杆子挂在哪个节点上(留空 = 就用『跟随谁』);"
         "想让杆子挂在肩膀/摄像机架子上就填它"),
        ("gk_camera", "node", "", "相机", "留空 = 场景里的当前相机"),
        ("gk_arm", "float", 4.0, "臂长", "视距:想退到多远(米)"),
        ("gk_height", "float", 1.5, "高度偏移", "枢轴抬高多少(瞄肩膀/头)"),
        ("gk_side", "float", 0.5, "侧向偏移", "越肩视角往右挪一点"),
        ("gk_pitch", "float", -15.0, "俯仰", "杆子往下压的角度(度);-15 微俯视"),
        ("gk_yaw_offset", "float", 0.0, "偏航偏移", "相对跟随对象的左右偏(度)"),
        ("gk_collision", "bool", True, "碰撞检测",
         "撞到墙就把杆子缩短(关掉会穿墙,除非你确定场景里没东西)"),
        ("gk_radius", "float", 0.3, "碰撞半径", "留一点余量,免得贴脸"),
        ("gk_lag", "float", 8.0, "位置平滑", "越大越跟手;0 = 硬跟(会有点抖)"),
        ("gk_lag_rot", "float", 0.0, "旋转平滑", "0 = 朝向立刻跟;>0 会有一点拖尾"),
        ("gk_enabled", "bool", True, "启用", "关掉 = 这条杆子不工作"),
    )
    gk_outputs = (
        S.pin(FLOW, "完成"),
        # ── 右侧输出:运行中把这两点读出来 ──────────────────────────
        S.pin(VECTOR, "枢轴", (0.0, 0.0, 0.0)),
        S.pin(VECTOR, "相机位置", (0.0, 0.0, 0.0)),
        S.pin(VECTOR, "方向", (0.0, 0.0, 1.0)),
    )

    def draw_buttons(self, context, layout):
        # ── 节点体上的排布(用户 2026-10-03:"先优化一下现在的节点图")──
        # 以前是三行**没有标注**的数字框 + 一个看不懂的图标按钮,
        # 谁也不知道 4.00 / 1.50 / 0.50 分别是什么。现在每行都带标题,
        # 而且按"先摆杆子、再调手感、最后挂相机"的顺序排。
        row = layout.row(align=True)
        row.prop(self, "gk_arm", text="臂长")
        row.prop(self, "gk_height", text="高度")
        row.prop(self, "gk_side", text="侧向")
        row = layout.row(align=True)
        row.prop(self, "gk_pitch", text="俯仰")
        row.prop(self, "gk_lag", text="平滑")
        row = layout.row(align=True)
        row.prop(self, "gk_collision", text="碰墙缩短", toggle=True)
        row.prop(self, "gk_enabled", text="启用", toggle=True)
        row = layout.row(align=True)
        row.label(text="跟随:", icon="CON_FOLLOWPATH")
        B.draw_target_picker(self, row, "gk_target")

    def draw_buttons_ext(self, context, layout):
        layout.label(text="弹簧臂挂在哪:留空 = 用『跟随谁』", icon="EMPTY_ARROWS")
        B.draw_target_picker(self, layout, "gk_arm_node")
        layout.label(text="相机:留空 = 用场景里的当前相机", icon="CAMERA_DATA")
        B.draw_target_picker(self, layout, "gk_camera")
        layout.label(text="跟随谁:留空 = 脚本所在的那个节点", icon="CON_FOLLOWPATH")
        B.draw_target_picker(self, layout, "gk_target")
        row = layout.row(align=True)
        row.prop(self, "gk_yaw_offset", text="偏航偏移")
        row.prop(self, "gk_lag_rot", text="旋转平滑")
        row = layout.row(align=True)
        row.prop(self, "gk_radius", text="碰撞半径")


@N
class GKNodeCameraStatus(B.GKExpressionNode):
    bl_idname = "GKNodeCameraStatus"
    bl_label = "相机_取状态"
    bl_icon = "INFO"
    gk_category = "相机"
    gk_tree_type = BOTH_TREES
    gk_doc = ("读当前相机的情况:名字 / 位置 / 朝向(度) / 视野 / 是不是开着。"
              "做逻辑判断用:比如『如果(当前相机 == 特写机位)』才允许开枪")
    gk_outputs = (
        S.pin(TEXT, "相机名", ""),
        S.pin(VECTOR, "位置", (0.0, 0.0, 0.0)),
        S.pin(VECTOR, "朝向", (0.0, 0.0, 0.0)),
        S.pin(FLOAT, "视野", 0.0),
        S.pin(BOOL, "开着吗", False),
    )


@N
class GKNodeCameraPath(B.GKStatementNode):
    bl_idname = "GKNodeCameraPath"
    bl_label = "相机_路径"
    bl_icon = "CURVE_PATH"
    gk_category = "相机"
    gk_doc = ("**让相机沿一条曲线走**(运镜轨道)。"
              "在 Blender 里画一条**曲线**当轨道,点『选曲线』挑它 —— "
              "导出时会把曲线**采样成一条路径**写进脚本(曲线本身不进游戏,"
              "只留形状),相机就沿它移动。"
              "── 两种用法 ──"
              "(1) 『位置』给 0~1:直接摆到轨道上的某一点"
              "(从一处切到另一处 = 把『位置』缓动过去);"
              "(2) 『速度』> 0:自动沿轨道前进(开场运镜 / 过场动画)。"
              "这就是『弹簧臂其实是一条曲线,相机挂在上面,曲线变长变短"
              "相机跟着动』那条路子 —— 直杆用『相机_弹簧臂』,走曲线用这个")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "位置", 0.0),
        S.pin(FLOAT, "速度", 0.0),
    )
    gk_input_props = {"位置": "gk_position", "速度": "gk_speed"}
    gk_prop_defs = (
        ("gk_target", "node", "", "相机", "留空 = 场景里的当前相机"),
        ("gk_curve", "string", "", "曲线",
         "Blender 里的曲线物体(点右边的『选曲线』挑一个)"),
        ("gk_position", "float", 0.0, "位置", "0 = 起点,1 = 终点"),
        ("gk_speed", "float", 0.0, "速度",
         "每秒走轨道的百分之几(0 = 不自动走,只按『位置』摆着)"),
        ("gk_look_mode", "enum", "FORWARD", "朝向", "",
         (("FORWARD", "看向前方(沿轨道方向)", "运镜最常用"),
          ("TARGET", "看向某个节点", "绕着一个东西转圈"),
          ("KEEP", "不改朝向", "自己另外用『相机_旋转』控"))),
        ("gk_look_target", "node", "", "看向谁", "朝向 = 『看向某个节点』时用"),
        ("gk_smooth", "float", 8.0, "平滑", "位置平滑;0 = 硬跟"),
        ("gk_loop", "bool", False, "到终点后从头再来", ""),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_position", text="位置")
        row.prop(self, "gk_speed", text="速度")
        row.prop(self, "gk_loop", text="循环", toggle=True)
        row = layout.row(align=True)
        row.prop(self, "gk_look_mode", text="")
        B.draw_target_picker(self, row)
        row = layout.row(align=True)
        row.prop(self, "gk_curve", text="", icon="CURVE_DATA")
        pick = row.operator("gk.pick_curve", text="", icon="VIEWZOOM")
        pick.node_name = self.name


@N
class GKNodeCameraSetCurrent(B.GKStatementNode):
    bl_idname = "GKNodeCameraSetCurrent"
    bl_label = "相机_切换"
    bl_icon = "CAMERA_DATA"
    gk_category = "相机"
    gk_doc = ("把某个相机设为当前相机(2D / 3D 通用)——做镜头切换、Boss 特写用。"
              "『过渡(秒)』填了就是**丝滑推镜**:新相机从旧相机的视角平滑"
              "推到它自己的位置,而不是硬切。"
              "做法:让新相机先站到旧相机的位姿上,再插值回它自己 —— "
              "两台相机都不用被挪走,来回切也不会跑位")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_target", "node", "", "相机", "要激活的 Camera2D / Camera3D"),
        ("gk_blend", "float", 0.0, "过渡(秒)",
         "0 = 立刻切;填 0.5 就是半秒推过去"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        B.draw_target_picker(self, row)
        row.prop(self, "gk_blend", text="过渡")


@N
class GKNodeCameraZoom(B.GKStatementNode):
    bl_idname = "GKNodeCameraZoom"
    bl_label = "相机_缩放"
    bl_icon = "ZOOM_IN"
    gk_category = "相机"
    gk_doc = ("设置相机缩放:2D 用 zoom,3D 用视场角 fov(自动按相机类型选)。"
              "『时长』填了就是**平滑变化**(视野慢慢推近 / 拉远);"
              "默认 0 = 立刻变 —— 这样**老图生成的结果一字不变**")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "数值", 1.0),)
    gk_input_props = {"数值": "gk_value"}
    gk_prop_defs = (
        ("gk_value", "float", 1.0, "数值", "2D: zoom 越大画面越近;3D: 垂直视场角(度)"),
        ("gk_time", "float", 0.0, "时长", "秒;0 = 立刻变(默认,和以前一样)"),
        # ── 范围限制(用户 2026-10-03:"可以无限拉长,应该添加自定义范围")──
        # 滚轮缩放那条链是"当前视野 + 滚轮量",没有上下限就会一路推下去:
        # 视野拉到 170 度画面变形、拉到负数直接废掉。这里给一对上下限,
        # **0 = 不限制**(老图生成的结果一字不变)。
        ("gk_min_value", "float", 0.0, "最小",
         "视野/缩放的下限;0 = 不限制(推荐:3D 视野填 20)"),
        ("gk_max_value", "float", 0.0, "最大",
         "视野/缩放的上限;0 = 不限制(推荐:3D 视野填 90)"),
        ("gk_target", "node", "", "相机", "留空则用当前相机"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_value", text="")
        row.prop(self, "gk_time", text="秒")
        row = layout.row(align=True)
        row.prop(self, "gk_min_value", text="最小")
        row.prop(self, "gk_max_value", text="最大")
        B.draw_target_picker(self, layout)


@N
class GKNodeCameraShake(B.GKStatementNode):
    bl_idname = "GKNodeCameraShake"
    bl_label = "相机_震动"
    bl_icon = "RNDCURVE"
    gk_category = "相机"
    gk_doc = ("让相机抖动一下(受击 / 爆炸反馈)。用相机的偏移量实现，"
              "不会破坏相机原本的位置——所以可以和『跟随』同时用")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "强度", 8.0),
        S.pin(FLOAT, "持续", 0.25),
    )
    gk_input_props = {"强度": "gk_strength", "持续": "gk_duration"}
    gk_prop_defs = (
        ("gk_strength", "float", 8.0, "强度", "最大偏移像素"),
        ("gk_duration", "float", 0.25, "持续", "震动时长(秒)"),
        ("gk_target", "node", "", "相机", "留空则用 $Camera2D / $Camera3D"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_strength", text="")
        row.prop(self, "gk_duration", text="")
        B.draw_target_picker(self, layout)


@N
class GKNodeCameraFollow(B.GKStatementNode):
    bl_idname = "GKNodeCameraFollow"
    bl_label = "相机_跟随"
    bl_icon = "CON_FOLLOWPATH"
    gk_category = "相机"
    gk_doc = ("让相机跟随某个节点(2D / 3D 通用)。"
              "平台跳跃游戏一定要放这个 —— 不放的话角色一跑远就出画面了")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "平滑", 0.12),
    )
    gk_input_props = {"平滑": "gk_smooth"}
    gk_prop_defs = (
        ("gk_smooth", "float", 0.12, "平滑",
         "0 = 硬跟随(相机与角色完全同步)；越大越拖，0.1~0.2 手感较好"),
        ("gk_target", "node", TARGET_SELF, "跟随谁", "要跟随的节点，默认自身"),
        ("gk_camera", "node", "", "相机", "留空则自动找场景里第一个相机"),
        ("gk_offset_x", "float", 0.0, "偏移 X", "瞄准角色上方/下方时的偏移"),
        ("gk_offset_y", "float", 0.0, "偏移 Y", ""),
        ("gk_distance", "float", 0.0, "3D 距离",
         "3D 专用:留 0 = 保持相机一开始相对角色的位置(推荐,"
         "在 Blender 里摆好相机角度即可);填了就固定站在角色身后这个距离"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_smooth", text="")
        row.prop(self, "gk_offset_y", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_camera", text="")
        B.draw_target_picker(self, row, "gk_camera")

    def draw_buttons_ext(self, context, layout):
        layout.label(text="相机:留空 = 自动找场景里第一个相机(推荐)")
        B.draw_target_picker(self, layout, "gk_camera")
        layout.label(text="跟随谁:留空 = 跟随脚本所在的那个节点")
        B.draw_target_picker(self, layout, "gk_target")


# ==========================================================================
# 骨骼(照 UPBGE 的『动画 ▸ Get/Set Bone Data』那一栏)
# ==========================================================================
# ── 为什么要加这一栏(用户 2026-10-03 第四轮)──────────────────────
# 用户贴了 UPBGE 的截图:"相机与动画的节点都可以整合成类似 upbge 一样" ——
# UPBGE 的『动画』下面除了播放/停止,还有
#     Get Bone Data ▸(Get Bone Name / Location / Euler Rotation / Head / …)
#     Set Bone Data ▸(Set Bone Location / Euler Rotation / Scale / …)
# 我们一个都没有。而有骨骼的模型(用户的胡桃就是 MMD 骨架)最常见的需求
# 恰恰是"让某根骨头动一下":头跟着视角转、手举武器、尾巴甩。
#
# Godot 那边对应 Skeleton3D 的 `get/set_bone_pose_*`(局部姿势,相对父骨),
# 以及 `get_bone_global_pose`(世界姿势,算枪口位置要用)。
BONE_HELP = ("填骨骼名(和 Godot 里 Skeleton3D 的骨头名一致)。"
             "Blender 里的骨头名导出后一般不变;拿不准就在 Godot 里"
             "点开模型看一眼")


@N
class GKNodeGetBone(B.GKExpressionNode):
    bl_idname = "GKNodeGetBone"
    bl_label = "骨骼_取数据"
    bl_icon = "BONE_DATA"
    gk_category = "动画"
    gk_doc = ("读一根骨头的姿势:位置 / 旋转(度) / 缩放,"
              "还能算它的**世界位置**(比如枪口在哪 —— 生成特效要用)。"
              "照 UPBGE 的 Get Bone Data 那一栏做的")
    gk_prop_defs = (
        ("gk_bone", "string", "head", "骨骼名", BONE_HELP),
        ("gk_skeleton_node", "node", "", "骨架节点",
         "留空 = 从脚本所在节点往下自动找 Skeleton3D"),
    )
    gk_outputs = (
        S.pin(VECTOR, "位置", (0.0, 0.0, 0.0)),
        S.pin(VECTOR, "旋转", (0.0, 0.0, 0.0)),
        S.pin(VECTOR, "缩放", (1.0, 1.0, 1.0)),
        S.pin(VECTOR, "世界位置", (0.0, 0.0, 0.0)),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_bone", text="", icon="BONE_DATA")
        B.draw_target_picker(self, layout, "gk_skeleton_node")


@N
class GKNodeSetBone(B.GKStatementNode):
    bl_idname = "GKNodeSetBone"
    bl_label = "骨骼_设置"
    bl_icon = "BONE_DATA"
    gk_category = "动画"
    gk_doc = ("摆一根骨头的姿势(照 UPBGE 的 Set Bone Data)。"
              "『保留』= 在现在的姿势上**加**(甩头、点头用这个);"
              "『直接设』= 换成给的姿势。"
              "注意是**相对父骨**的姿势 —— 和 Godot 里的 bone pose 一致")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(VECTOR, "旋转", (0.0, 0.0, 0.0)),
        S.pin(VECTOR, "位置", (0.0, 0.0, 0.0)),
        S.pin(VECTOR, "缩放", (1.0, 1.0, 1.0)),
    )
    gk_prop_defs = (
        ("gk_mode", "enum", "ADD", "怎么用", "",
         (("ADD", "保留(在现在的姿势上加)", "相对 —— 点个头、甩个头"),
          ("SET", "直接设(换成这个姿势)", "绝对"))),
        ("gk_bone", "string", "head", "骨骼名", BONE_HELP),
        ("gk_skeleton_node", "node", "", "骨架节点", "留空 = 自动找"),
        ("gk_do_rot", "bool", True, "改旋转", ""),
        ("gk_do_pos", "bool", False, "改位置", ""),
        ("gk_do_scale", "bool", False, "改缩放", ""),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_mode", text="")
        row.prop(self, "gk_bone", text="", icon="BONE_DATA")
        row = layout.row(align=True)
        row.prop(self, "gk_do_rot", text="旋转", toggle=True)
        row.prop(self, "gk_do_pos", text="位置", toggle=True)
        row.prop(self, "gk_do_scale", text="缩放", toggle=True)
        B.draw_target_picker(self, layout, "gk_skeleton_node")


@N
class GKNodeBoneLookAtCamera(B.GKStatementNode):
    bl_idname = "GKNodeBoneLookAtCamera"
    bl_label = "骨骼_跟随视角"
    bl_icon = "CAMERA_DATA"
    gk_category = "动画"
    gk_doc = ("**让一根骨头跟着相机转** —— 回答第四轮那个问题"
              "『第一人称转视角时能不能让角色一部分模型跟着转』:**能**。"
              "典型用法:头 / 上半身跟着镜头偏,但只跟一部分"
              "(权重 0.3~0.6)、并且限制角度(脖子不能拧 180°)。"
              "每物理帧更新,和相机同一个时钟")
    gk_inputs = (
        S.pin(FLOW, "执行"),
    )
    gk_prop_defs = (
        ("gk_bone", "string", "head", "骨骼名", BONE_HELP),
        ("gk_skeleton_node", "node", "", "骨架节点", "留空 = 自动找"),
        ("gk_camera", "node", "", "相机", "留空 = 当前相机"),
        ("gk_yaw_weight", "float", 0.5, "左右权重",
         "跟着转多少:0 = 不跟,1 = 完全跟。0.3~0.6 看着最自然"),
        ("gk_pitch_weight", "float", 0.35, "上下权重", "同上(上下跟得少一点更像人)"),
        ("gk_limit", "float", 60.0, "最大角度",
         "最多偏多少度(超过就不再跟了,免得脖子拧断)"),
        ("gk_smooth", "float", 8.0, "平滑", "越大越跟手;0 = 硬跟"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_bone", text="", icon="BONE_DATA")
        row.prop(self, "gk_yaw_weight", text="左右")
        row.prop(self, "gk_pitch_weight", text="上下")
        B.draw_target_picker(self, layout, "gk_skeleton_node")

    def draw_buttons_ext(self, context, layout):
        layout.label(text="相机:留空 = 用当前相机")
        B.draw_target_picker(self, layout, "gk_camera")
        layout.label(text="骨架:留空 = 从脚本节点往下自动找")
        B.draw_target_picker(self, layout, "gk_skeleton_node")


# ==========================================================================
# 时间
# ==========================================================================
@N
class GKNodeSetTimeScale(B.GKStatementNode):
    bl_idname = "GKNodeSetTimeScale"
    bl_label = "时间_设置倍速"
    bl_icon = "TIME"
    gk_category = "时间"
    gk_doc = ("设置全局时间倍速(对应 Engine.time_scale):0.3 = 子弹时间，"
              "1.0 = 正常，0 = 完全暂停")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "倍速", 1.0),)
    gk_input_props = {"倍速": "gk_scale"}
    gk_prop_defs = (("gk_scale", "float", 1.0, "倍速", "1.0 为正常速度"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_scale", text="")


# ==========================================================================
# 音效
# ==========================================================================
@N
class GKNodePlaySound(B.GKStatementNode):
    bl_idname = "GKNodePlaySound"
    bl_label = "音效_播放"
    bl_icon = "SOUND"
    gk_category = "音效"
    gk_doc = ("播放一次音效(跳跃 / 受击 / 拾取)。播放器是运行时自动建的，"
              "播完自动销毁，不需要你在场景里摆节点")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "音频路径", "res://art/jump.wav"),
        S.pin(FLOAT, "音量", 0.0),
        S.pin(FLOAT, "音高", 1.0),
    )
    gk_input_props = {
        "音频路径": "gk_path",
        "音量": "gk_volume",
        "音高": "gk_pitch",
    }
    gk_prop_defs = (
        ("gk_path", "string", "res://art/jump.wav", "音频路径", "res:// 开头的音频文件"),
        ("gk_volume", "float", 0.0, "音量", "分贝，0 为原始音量，-6 约减半"),
        ("gk_pitch", "float", 1.0, "音高", "1.0 原速，>1 更尖更快"),
        ("gk_2d", "bool", True, "2D 音效", "勾上=按距离衰减(3D 项目取消勾选)"),
        ("gk_target", "node", "", "挂到", "留空则挂到当前脚本所在节点"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_path", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_volume", text="")
        row.prop(self, "gk_pitch", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_2d", text="2D", toggle=True)
        B.draw_target_picker(self, layout)


@N
class GKNodePlayMusic(B.GKStatementNode):
    bl_idname = "GKNodePlayMusic"
    bl_label = "音效_音乐"
    bl_icon = "PLAY_SOUND"
    gk_category = "音效"
    gk_doc = "播放背景音乐(BGM)。重复调用会切换曲目并做淡入淡出，不会叠着播"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "音频路径", "res://art/bgm.ogg"),
        S.pin(FLOAT, "音量", -6.0),
    )
    gk_input_props = {"音频路径": "gk_path", "音量": "gk_volume"}
    gk_prop_defs = (
        ("gk_path", "string", "res://art/bgm.ogg", "音频路径", "res:// 开头的音频文件"),
        ("gk_volume", "float", -6.0, "音量", "分贝，0 为原始音量"),
        ("gk_fade", "float", 0.5, "淡入秒数", "0 表示直接开始"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_path", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_volume", text="")
        row.prop(self, "gk_fade", text="")


@N
class GKNodeStopSound(B.GKStatementNode):
    bl_idname = "GKNodeStopSound"
    bl_label = "音效_停止音乐"
    bl_icon = "MUTE_IPO_OFF"
    gk_category = "音效"
    gk_doc = "停止背景音乐(可做淡出)。音效是一次性的，播完自己就没了，不用手动停"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "淡出秒数", 0.5),)
    gk_input_props = {"淡出秒数": "gk_fade"}
    gk_prop_defs = (("gk_fade", "float", 0.5, "淡出秒数", "0 表示立刻停"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_fade", text="")


# ==========================================================================
# 存档
# ==========================================================================
@N
class GKNodeSaveSet(B.GKStatementNode):
    bl_idname = "GKNodeSaveSet"
    bl_label = "存档_写入"
    bl_icon = "IMPORT"
    gk_category = "存档"
    gk_doc = ("往存档里写一个值(血量 / 关卡 / 金币)。只改内存，"
              "要真正落盘请接『存档_保存』")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "键名", "hp"),
        S.pin(ANY, "值", 3),
    )
    gk_input_props = {"键名": "gk_key"}
    gk_prop_defs = (("gk_key", "string", "hp", "键名", "存档里这一项的键"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_key", text="")


@N
class GKNodeSaveGet(B.GKStatementNode):
    bl_idname = "GKNodeSaveGet"
    bl_label = "存档_读取到变量"
    bl_icon = "EXPORT"
    gk_category = "存档"
    gk_doc = ("把存档里的值读进一个脚本变量，然后用『变量_读取』节点取用。"
              "键不存在时用『默认值』")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "键名", "hp"),
    )
    gk_input_props = {"键名": "gk_key"}
    gk_prop_defs = (
        ("gk_key", "string", "hp", "键名", "存档里这一项的键"),
        ("gk_var", "string", "hp", "写入变量", "读出来的值放进哪个脚本变量"),
        ("gk_default", "float", 0.0, "默认值", "键不存在时用它"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_key", text="")
        row.prop(self, "gk_var", text="")
        layout.prop(self, "gk_default", text="")


@N
class GKNodeSaveWrite(B.GKStatementNode):
    bl_idname = "GKNodeSaveWrite"
    bl_label = "存档_保存"
    bl_icon = "FILE_TICK"
    gk_category = "存档"
    gk_doc = "把内存里的存档数据写进磁盘文件(user:// 目录下)"
    gk_inputs = (S.pin(FLOW, "执行"),)

    gk_prop_defs = (
        ("gk_slot", "string", "save1", "存档槽", "不同槽位互不影响，做多存档用"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_slot", text="")


@N
class GKNodeSaveLoad(B.GKStatementNode):
    bl_idname = "GKNodeSaveLoad"
    bl_label = "存档_载入"
    bl_icon = "FILE_FOLDER"
    gk_category = "存档"
    gk_doc = "从磁盘把存档读进内存。文件不存在时不会出错，只是读到空存档"
    gk_inputs = (S.pin(FLOW, "执行"),)

    gk_prop_defs = (
        ("gk_slot", "string", "save1", "存档槽", "要和『存档_保存』用同一个槽位"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_slot", text="")


@N
class GKNodeSaveHas(B.GKStatementNode):
    bl_idname = "GKNodeSaveHas"
    bl_label = "存档_是否存在"
    bl_icon = "QUESTION"
    gk_category = "存档"
    gk_doc = "判断某个槽位有没有存档文件(做『继续游戏』按钮的可用状态)"
    gk_inputs = (S.pin(FLOW, "执行"),)

    gk_prop_defs = (
        ("gk_slot", "string", "save1", "存档槽", "要检查的槽位"),
    )
    # 出口故意不叫『完成』:见 codegen 里 _stmt_save_has 的说明 ——
    # emit_statement 会自动续接『完成』，那样 else 分支会跑两遍。
    gk_outputs = (S.pin(FLOW, "有档"), S.pin(FLOW, "没档"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_slot", text="")


@N
class GKNodeSaveDelete(B.GKStatementNode):
    bl_idname = "GKNodeSaveDelete"
    bl_label = "存档_删除"
    bl_icon = "TRASH"
    gk_category = "存档"
    gk_doc = "删除某个槽位的存档文件(删档 / 重新开始)"
    gk_inputs = (S.pin(FLOW, "执行"),)

    gk_prop_defs = (
        ("gk_slot", "string", "save1", "存档槽", "要删除的槽位"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_slot", text="")


# ==========================================================================
# 屏幕效果
# ==========================================================================
@N
class GKNodeScreenFlash(B.GKStatementNode):
    bl_idname = "GKNodeScreenFlash"
    bl_label = "屏幕_闪白"
    bl_icon = "IMAGE_ZDEPTH"
    gk_category = "屏幕效果"
    gk_doc = ("整屏闪一下(受击 / 爆炸 / 治疗)。用 ColorRect + Tween 做，"
              "不占渲染管线，也不需要着色器")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(COLOR, "颜色", (1.0, 1.0, 1.0, 1.0)),
        S.pin(FLOAT, "时长", 0.3),
    )
    gk_input_props = {"时长": "gk_time"}
    gk_prop_defs = (
        ("gk_color", "color", (1.0, 1.0, 1.0, 1.0), "颜色", "闪光颜色"),
        ("gk_time", "float", 0.3, "时长", "淡出到透明所需秒数"),
        ("gk_alpha", "float", 0.6, "起始透明度", "1 为全不透明"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_color", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_time", text="")
        row.prop(self, "gk_alpha", text="")


@N
class GKNodeScreenFade(B.GKStatementNode):
    bl_idname = "GKNodeScreenFade"
    bl_label = "屏幕_渐变遮罩"
    bl_icon = "IMAGE_ALPHA"
    gk_category = "屏幕效果"
    gk_doc = ("整屏渐变到某个颜色(黑场转场 / 淡入开场)。"
              "『到不透明』用于转场，『到透明』用于淡回游戏")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "时长", 1.0),
    )
    gk_input_props = {"时长": "gk_time"}
    gk_prop_defs = (
        ("gk_color", "color", (0.0, 0.0, 0.0, 1.0), "颜色", "遮罩颜色，默认黑"),
        ("gk_time", "float", 1.0, "时长", "渐变秒数"),
        ("gk_to_opaque", "bool", True, "渐变到不透明", "取消勾选=淡回透明"),
        ("gk_hold", "float", 0.0, "保持秒数", "转场时全黑停留多久(0 = 一直留着)"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_color", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_time", text="")
        row.prop(self, "gk_hold", text="")
        layout.prop(self, "gk_to_opaque", text="渐变到不透明", toggle=True)


# ==========================================================================
# 界面
# ==========================================================================
@N
class GKNodeUiText(B.GKStatementNode):
    bl_idname = "GKNodeUiText"
    bl_label = "界面_文本"
    bl_icon = "FONT_DATA"
    gk_category = "界面"
    gk_doc = ("在屏幕上显示一行字(分数 / 提示 / 对话)。UI 层是自动建的，"
              "不需要你在场景里摆控件；重复调用同一个节点是改文字，不会叠出新的一行")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "文字", "分数: 0"),
        S.pin(FLOAT, "X", 20.0),
        S.pin(FLOAT, "Y", 20.0),
    )
    gk_input_props = {"文字": "gk_text", "X": "gk_x", "Y": "gk_y"}
    gk_prop_defs = (
        ("gk_text", "string", "分数: 0", "文字", "未连线时显示的内容"),
        ("gk_x", "float", 20.0, "X", "屏幕坐标(左上角为原点)"),
        ("gk_y", "float", 20.0, "Y", "屏幕坐标"),
        ("gk_size", "int", 24, "字号", "像素"),
        ("gk_color", "color", (1.0, 1.0, 1.0, 1.0), "颜色", "文字颜色"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_text", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_x", text="X")
        row.prop(self, "gk_y", text="Y")
        row = layout.row(align=True)
        row.prop(self, "gk_size", text="")
        row.prop(self, "gk_color", text="")


@N
class GKNodeUiBar(B.GKStatementNode):
    bl_idname = "GKNodeUiBar"
    bl_label = "界面_进度条"
    bl_icon = "COLOR"
    gk_category = "界面"
    gk_doc = "在屏幕上显示一条进度条(血条 / 蓝条 / 蓄力条)。数值会自动钳在 0~最大值"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "数值", 100.0),
        S.pin(FLOAT, "X", 20.0),
        S.pin(FLOAT, "Y", 60.0),
    )
    gk_input_props = {"数值": "gk_value", "X": "gk_x", "Y": "gk_y"}
    gk_prop_defs = (
        ("gk_value", "float", 100.0, "数值", "当前值"),
        ("gk_max", "float", 100.0, "最大值", "满值"),
        ("gk_x", "float", 20.0, "X", "屏幕坐标"),
        ("gk_y", "float", 60.0, "Y", "屏幕坐标"),
        ("gk_width", "float", 200.0, "宽", "像素"),
        ("gk_height", "float", 16.0, "高", "像素"),
        ("gk_color", "color", (0.85, 0.25, 0.25, 1.0), "颜色", "填充色"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_value", text="")
        row.prop(self, "gk_max", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_x", text="X")
        row.prop(self, "gk_y", text="Y")
        row = layout.row(align=True)
        row.prop(self, "gk_width", text="")
        row.prop(self, "gk_height", text="")
        layout.prop(self, "gk_color", text="")


@N
class GKNodeUiButton(B.GKStatementNode):
    bl_idname = "GKNodeUiButton"
    bl_label = "界面_按钮"
    bl_icon = "PLAY"
    gk_category = "界面"
    gk_doc = ("在屏幕上放一个按钮，点击后沿『点击』出口继续。"
              "按钮是持久的(不会点一下就消失)，重复调用只改文字")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "文字", "开始游戏"),
        S.pin(FLOAT, "X", 100.0),
        S.pin(FLOAT, "Y", 200.0),
    )
    gk_input_props = {"文字": "gk_text", "X": "gk_x", "Y": "gk_y"}
    gk_prop_defs = (
        ("gk_text", "string", "开始游戏", "文字", "按钮上显示的字"),
        ("gk_x", "float", 100.0, "X", "屏幕坐标"),
        ("gk_y", "float", 200.0, "Y", "屏幕坐标"),
        ("gk_width", "float", 160.0, "宽", "像素"),
        ("gk_height", "float", 48.0, "高", "像素"),
    )
    # 出口故意不叫『完成』:emit_statement 会自动续接『完成』，
    # 那样按钮还没点就会立刻往下跑。见 _stmt_ui_button 的说明。
    gk_outputs = (S.pin(FLOW, "点击"),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_text", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_x", text="X")
        row.prop(self, "gk_y", text="Y")
        row = layout.row(align=True)
        row.prop(self, "gk_width", text="")
        row.prop(self, "gk_height", text="")


@N
class GKNodeUiHide(B.GKStatementNode):
    bl_idname = "GKNodeUiHide"
    bl_label = "界面_隐藏控件"
    bl_icon = "HIDE_ON"
    gk_category = "界面"
    gk_doc = "隐藏某个界面控件(比如捡到道具后让提示消失)。控件名就是创建它的节点名"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(TEXT, "控件名", "提示"),)
    gk_input_props = {"控件名": "gk_widget"}
    gk_prop_defs = (
        ("gk_widget", "string", "提示", "控件名", "要隐藏的界面节点名"),
        ("gk_visible", "bool", False, "显示", "勾上=显示，取消=隐藏"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_widget", text="")
        row.prop(self, "gk_visible", text="", toggle=True)


# ==========================================================================
# 射线
# ==========================================================================
@N
class GKNodeRaycast(B.GKStatementNode):
    bl_idname = "GKNodeRaycast"
    bl_label = "射线_检测"
    bl_icon = "RESTRICT_SELECT_OFF"
    gk_category = "射线"
    gk_doc = ("从一个点沿某个方向打一条射线，看打到什么(开枪命中 / 视线判定 / "
              "地面检测)。打到东西走『命中』，没打到走『没打中』")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(VECTOR, "方向", (1.0, 0.0, 0.0)),
        S.pin(FLOAT, "距离", 200.0),
    )
    gk_input_props = {"方向": "gk_direction", "距离": "gk_distance"}
    gk_prop_defs = (
        ("gk_direction", "float_vector", (1.0, 0.0, 0.0), "方向", "单位向量，会自动归一化"),
        ("gk_distance", "float", 200.0, "距离", "射线长度(像素 / 米)"),
        ("gk_offset", "float_vector", (0.0, 0.0, 0.0), "起点偏移", "相对自身位置的偏移"),
        ("gk_layer", "int", 1, "碰撞层", "只检测这些层(位掩码，1 表示第 1 层)"),
    )
    # 出口不叫『完成』:见 codegen 里 _stmt_raycast 的说明 ——
    # 叫『完成』会被 emit_statement 自动续接，分支就废了。
    gk_outputs = (
        S.pin(FLOW, "命中"),
        S.pin(FLOW, "没打中"),
        S.pin(OBJECT, "打到的东西", None),
        S.pin(VECTOR, "命中点", (0.0, 0.0, 0.0)),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_distance", text="")
        row.prop(self, "gk_layer", text="")
        layout.prop(self, "gk_direction", text="")


@N
class GKNodeRaycastCheck(B.GKExpressionNode):
    bl_idname = "GKNodeRaycastCheck"
    bl_label = "射线_有东西吗"
    bl_icon = "QUESTION"
    gk_category = "射线"
    gk_doc = ("往某个方向看一眼有没有东西(视线判定 / 撞墙检测)，"
              "结果接到『如果_否则』的条件上")
    gk_inputs = (
        S.pin(VECTOR, "方向", (1.0, 0.0, 0.0)),
        S.pin(FLOAT, "距离", 200.0),
    )
    gk_input_props = {"方向": "gk_direction", "距离": "gk_distance"}
    gk_prop_defs = (
        ("gk_direction", "float_vector", (1.0, 0.0, 0.0), "方向", "单位向量，会自动归一化"),
        ("gk_distance", "float", 200.0, "距离", "射线长度"),
        ("gk_layer", "int", 1, "碰撞层", "只检测这些层"),
    )
    # 纯表达式:取值时把射线"就地"射出去。
    # GDScript 的表达式在被使用的作用域里求值，所以生成的多行代码
    # 正好落在需要这个布尔值的位置(比如 if 的条件上)。
    #
    # gk_no_cache:射线每次求值都必须**重新射**。生成器默认会缓存表达式文本，
    # 缓存之后第二次求值只会复用第一次的结果，射线就永远停在那里了。
    gk_no_cache = True
    gk_outputs = (S.pin(BOOL, "有东西", False),)



# ==========================================================================
# 定时
# ==========================================================================
@N
class GKNodeEverySeconds(B.GKStatementNode):
    bl_idname = "GKNodeEverySeconds"
    bl_label = "定时_每隔几秒"
    bl_icon = "TIME"
    gk_category = "定时"
    gk_doc = ("每隔 N 秒执行一次(刷怪 / 回血 / 自动存档)。"
              "用独立计时器,不会被帧率影响")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "间隔", 1.0),
    )
    gk_input_props = {"间隔": "gk_interval"}
    gk_prop_defs = (
        ("gk_interval", "float", 1.0, "间隔", "秒"),
        ("gk_start_now", "bool", False, "立即开始", "勾上=不等第一个间隔就先执行一次"),
    )
    # 出口不叫『完成』:要等计时器到点才走，见 _stmt_every_seconds 的说明。
    gk_outputs = (S.pin(FLOW, "到点了"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_interval", text="")
        row.prop(self, "gk_start_now", text="", toggle=True)


@N
class GKNodeTweenValue(B.GKStatementNode):
    bl_idname = "GKNodeTweenValue"
    bl_label = "定时_缓动数值"
    bl_icon = "IPO_EASE_IN_OUT"
    gk_category = "定时"
    gk_doc = ("把一个脚本变量在指定时间内平滑地变到目标值(血条缓动 / 镜头推近 / "
              "UI 滑入)。对应 Godot 的 Tween")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "目标值", 100.0),
        S.pin(FLOAT, "时长", 0.5),
    )
    gk_input_props = {"目标值": "gk_to", "时长": "gk_time"}
    gk_prop_defs = (
        ("gk_var", "string", "value", "变量名", "要在 Blender 侧声明过的脚本变量"),
        ("gk_to", "float", 100.0, "目标值", "缓动到多少"),
        ("gk_time", "float", 0.5, "时长", "秒"),
        ("gk_trans", "enum", "TRANS_LINEAR", "曲线", "速度曲线", (
            ("TRANS_LINEAR", "匀速", ""),
            ("TRANS_SINE", "正弦(平滑)", ""),
            ("TRANS_CUBIC", "三次(先慢后快)", ""),
            ("TRANS_BACK", "回弹(冲过头再回来)", ""),
            ("TRANS_BOUNCE", "弹跳", ""),
            ("TRANS_ELASTIC", "橡皮筋", ""),
        )),
        ("gk_ease", "enum", "EASE_IN_OUT", "缓动方式", "", (
            ("EASE_IN", "慢进", ""),
            ("EASE_OUT", "慢出", ""),
            ("EASE_IN_OUT", "两头慢", ""),
        )),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_var", text="")
        row.prop(self, "gk_to", text="")
        row.prop(self, "gk_time", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_trans", text="")
        row.prop(self, "gk_ease", text="")


# ==========================================================================
# 时间轴 —— 把任意节点的任意属性在时间里推过去
# ==========================================================================
# 用户 2026-10-03 要的"时间轴":
#   * 摄像机视野变化        -> 缓动相机的 fov
#   * 场景动画(门/平台…)   -> 缓动它们的位置 / 旋转 / 颜色
#   * 不同动画的整体联动    -> 几条缓动 + 几段动画一起启动(配『序列』)
#
# ── 为什么不做成"曲线编辑器"那种时间轴 ────────────────────────────
# 那种 UI 在 Blender 节点里画不出来(要自己造一个编辑器),而效果完全可以用
# "一个节点管一条轨道"拼出来 —— 而且**能和其他节点混着摆**:
# 要几条轨道就放几个节点,再串在『序列』里,顺序/同时都看得见。
# Godot 的 Tween 本身是**异步**的:tween_property 一调用就返回,曲线在
# 后台自己跑,所以一个节点接着另一个 = 天然并行。
@N
class GKNodeTweenProperty(B.GKStatementNode):
    bl_idname = "GKNodeTweenProperty"
    bl_label = "定时_缓动属性"
    bl_icon = "IPO_EASE_IN_OUT"
    gk_category = "定时"
    gk_doc = ("把**某个节点的某个属性**在指定时间里平滑地推到目标值 —— "
              "时间轴的一条轨道。"
              "『属性』填 Godot 的属性名,支持带子项:`fov`、`position:y`、"
              "`rotation:y`、`modulate:a`、`scale`、`volume_db` …"
              "常用组合:相机 fov 拉近、门 position:y 升起、"
              "灯 light_energy 变暗、角色 modulate:a 淡出。"
              "Tween 是异步的:调用完立刻往下走,动画在后台自己跑 —— "
              "所以几条缓动**天生就是同时**的")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(ANY, "到", 0.0),
    )
    gk_input_props = {"到": "gk_to"}
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "目标", "要动哪个节点(默认自己)"),
        ("gk_property", "string", "fov", "属性",
         "Godot 的属性名,可用 `position:y` / `modulate:a` 这种子路径"),
        ("gk_to", "float", 0.0, "到",
         "缓动到多少(想给向量/颜色就把『到』引脚连一个『常量_向量』上来)"),
        ("gk_time", "float", 0.5, "时长", "秒"),
        ("gk_trans", "enum", "TRANS_SINE", "曲线", "速度曲线", (
            ("TRANS_LINEAR", "匀速", ""),
            ("TRANS_SINE", "正弦(平滑)", ""),
            ("TRANS_CUBIC", "三次(先慢后快)", ""),
            ("TRANS_QUAD", "二次", ""),
            ("TRANS_BACK", "回弹(冲过头再回来)", ""),
            ("TRANS_BOUNCE", "弹跳", ""),
            ("TRANS_ELASTIC", "橡皮筋", ""),
        )),
        ("gk_ease", "enum", "EASE_IN_OUT", "缓动方式", "", (
            ("EASE_IN", "慢进", ""),
            ("EASE_OUT", "慢出", ""),
            ("EASE_IN_OUT", "两头慢", ""),
        )),
        # 缓动结束后要不要补一句"精确定位"?
        # 浮点插值最后一帧可能差 0.0001,做"门完全关上"这种判定时会差一点。
        ("gk_snap", "bool", True, "结束时对齐",
         "缓动结束时把值精确设成目标值(避免浮点误差差一点点)"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        B.draw_target_picker(self, row)
        row.prop(self, "gk_property", text="", icon="PROPERTIES")
        row = layout.row(align=True)
        row.prop(self, "gk_to", text="到")
        row.prop(self, "gk_time", text="秒")
        row = layout.row(align=True)
        row.prop(self, "gk_trans", text="")
        row.prop(self, "gk_ease", text="")


# ==========================================================================
# 列表
# ==========================================================================
@N
class GKNodeListNew(B.GKStatementNode):
    bl_idname = "GKNodeListNew"
    bl_label = "列表_新建"
    bl_icon = "OUTLINER_OB_GROUP_INSTANCE"
    gk_category = "列表"
    gk_doc = "新建一个空列表(背包 / 刷怪表 / 任务清单)"
    gk_inputs = (S.pin(FLOW, "执行"),)

    gk_prop_defs = (("gk_var", "string", "items", "变量名", "列表变量名"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_var", text="")


@N
class GKNodeListAdd(B.GKStatementNode):
    bl_idname = "GKNodeListAdd"
    bl_label = "列表_添加"
    bl_icon = "ADD"
    gk_category = "列表"
    gk_doc = "往列表末尾加一个东西(捡到道具 / 记录敌人)"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(ANY, "值", 0),
    )
    gk_prop_defs = (("gk_var", "string", "items", "变量名", "列表变量名"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_var", text="")


@N
class GKNodeListRemove(B.GKStatementNode):
    bl_idname = "GKNodeListRemove"
    bl_label = "列表_移除"
    bl_icon = "REMOVE"
    gk_category = "列表"
    gk_doc = "从列表里删掉一个值(用掉道具 / 敌人死亡)"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(ANY, "值", 0),
    )
    gk_prop_defs = (("gk_var", "string", "items", "变量名", "列表变量名"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_var", text="")


@N
class GKNodeListGet(B.GKStatementNode):
    bl_idname = "GKNodeListGet"
    bl_label = "列表_取第几个到变量"
    bl_icon = "FULLSCREEN_ENTER"
    gk_category = "列表"
    gk_doc = "把列表里第 N 个取进一个脚本变量。下标从 0 开始；越界时会用『默认值』"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(INT, "下标", 0),
    )
    gk_input_props = {"下标": "gk_index"}
    gk_prop_defs = (
        ("gk_var", "string", "items", "列表变量名", "从哪个列表取"),
        ("gk_index", "int", 0, "下标", "从 0 开始"),
        ("gk_target_var", "string", "item", "写入变量", "取出来的值放进哪个脚本变量"),
        ("gk_default", "float", 0.0, "默认值", "越界时用它"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_var", text="")
        row.prop(self, "gk_index", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_target_var", text="")
        row.prop(self, "gk_default", text="")


# ==========================================================================
# 字典
# ==========================================================================
@N
class GKNodeDictSet(B.GKStatementNode):
    bl_idname = "GKNodeDictSet"
    bl_label = "字典_写入"
    bl_icon = "KEYINGSET"
    gk_category = "字典"
    gk_doc = "往字典里写一个键值对(按名字存数据,比列表好查)"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "键", "hp"),
        S.pin(ANY, "值", 3),
    )
    gk_input_props = {"键": "gk_key"}
    gk_prop_defs = (
        ("gk_var", "string", "stats", "字典变量名", "往哪个字典写"),
        ("gk_key", "string", "hp", "键", "键名"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_var", text="")
        row.prop(self, "gk_key", text="")


@N
class GKNodeDictGet(B.GKStatementNode):
    bl_idname = "GKNodeDictGet"
    bl_label = "字典_读取到变量"
    bl_icon = "EXPORT"
    gk_category = "字典"
    gk_doc = "按键从字典取值放进脚本变量。键不存在时用『默认值』"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "键", "hp"),
    )
    gk_input_props = {"键": "gk_key"}
    gk_prop_defs = (
        ("gk_var", "string", "stats", "字典变量名", "从哪个字典读"),
        ("gk_key", "string", "hp", "键", "键名"),
        ("gk_target_var", "string", "value", "写入变量", "取出来的值放进哪个脚本变量"),
        ("gk_default", "float", 0.0, "默认值", "键不存在时用它"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_var", text="")
        row.prop(self, "gk_key", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_target_var", text="")
        row.prop(self, "gk_default", text="")


@N
class GKNodeDictHas(B.GKStatementNode):
    bl_idname = "GKNodeDictHas"
    bl_label = "字典_有键吗"
    bl_icon = "QUESTION"
    gk_category = "字典"
    gk_doc = "判断字典里有没有某个键"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "键", "hp"),
    )
    gk_input_props = {"键": "gk_key"}
    gk_prop_defs = (
        ("gk_var", "string", "stats", "字典变量名", "查哪个字典"),
        ("gk_key", "string", "hp", "键", "键名"),
    )
    gk_outputs = (S.pin(FLOW, "有"), S.pin(FLOW, "没有"),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_var", text="")
        row.prop(self, "gk_key", text="")


# ==========================================================================
# 手柄
# ==========================================================================
_JOY_AXES = (
    ("JOY_AXIS_LEFT_X", "左摇杆 左右", ""),
    ("JOY_AXIS_LEFT_Y", "左摇杆 上下", ""),
    ("JOY_AXIS_RIGHT_X", "右摇杆 左右", ""),
    ("JOY_AXIS_RIGHT_Y", "右摇杆 上下", ""),
    ("JOY_AXIS_TRIGGER_LEFT", "左扳机", ""),
    ("JOY_AXIS_TRIGGER_RIGHT", "右扳机", ""),
)

_JOY_BUTTONS = (
    ("JOY_BUTTON_A", "A(×)", ""),
    ("JOY_BUTTON_B", "B(○)", ""),
    ("JOY_BUTTON_X", "X(□)", ""),
    ("JOY_BUTTON_Y", "Y(△)", ""),
    ("JOY_BUTTON_LEFT_SHOULDER", "左肩键 LB", ""),
    ("JOY_BUTTON_RIGHT_SHOULDER", "右肩键 RB", ""),
    ("JOY_BUTTON_BACK", "返回 / Select", ""),
    ("JOY_BUTTON_START", "开始 / Start", ""),
    ("JOY_BUTTON_DPAD_UP", "十字键 上", ""),
    ("JOY_BUTTON_DPAD_DOWN", "十字键 下", ""),
    ("JOY_BUTTON_DPAD_LEFT", "十字键 左", ""),
    ("JOY_BUTTON_DPAD_RIGHT", "十字键 右", ""),
)


@N
class GKNodeJoyAxis(B.GKExpressionNode):
    bl_idname = "GKNodeJoyAxis"
    bl_label = "手柄_摇杆轴"
    bl_icon = "DRIVER"
    gk_category = "输入"
    gk_doc = "读手柄某个轴/扳机的强度(-1 ~ 1)。自带死区，不会因为摇杆回中不准而漂移"
    gk_prop_defs = (
        ("gk_axis", "enum", "JOY_AXIS_LEFT_X", "轴", "", _JOY_AXES),
        ("gk_deadzone", "float", 0.2, "死区", "小于这个值算 0"),
        ("gk_device", "int", 0, "手柄编号", "0 表示第一个手柄"),
    )
    gk_outputs = (S.pin(FLOAT, "强度", 0.0),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_axis", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_deadzone", text="")
        row.prop(self, "gk_device", text="")


@N
class GKNodeJoyVector(B.GKExpressionNode):
    bl_idname = "GKNodeJoyVector"
    bl_label = "手柄_摇杆方向"
    bl_icon = "ORIENTATION_GIMBAL"
    gk_category = "输入"
    gk_doc = ("把一根摇杆读成一个方向向量，可以直接接到『移动_按方向』上。"
              "建议配合『逻辑_比较』判断长度是否大于 0")
    gk_prop_defs = (
        ("gk_stick", "enum", "LEFT", "摇杆", "", (
            ("LEFT", "左摇杆", ""),
            ("RIGHT", "右摇杆", ""),
        )),
        ("gk_deadzone", "float", 0.2, "死区", "小于这个值算 0"),
        ("gk_device", "int", 0, "手柄编号", "0 表示第一个手柄"),
    )
    gk_outputs = (S.pin(VECTOR2, "方向", (0.0, 0.0)),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_stick", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_deadzone", text="")
        row.prop(self, "gk_device", text="")


@N
class GKNodeJoyButton(B.GKExpressionNode):
    bl_idname = "GKNodeJoyButton"
    bl_label = "手柄_按键按住"
    bl_icon = "HAND"
    gk_category = "输入"
    gk_doc = "手柄按键是否按住"
    gk_prop_defs = (
        ("gk_button", "enum", "JOY_BUTTON_A", "按键", "", _JOY_BUTTONS),
        ("gk_device", "int", 0, "手柄编号", "0 表示第一个手柄"),
    )
    gk_outputs = (S.pin(BOOL, "按住", False),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_button", text="")
        row.prop(self, "gk_device", text="")


@N
class GKNodeJoyButtonDown(B.GKExpressionNode):
    bl_idname = "GKNodeJoyButtonDown"
    bl_label = "手柄_按键按下"
    bl_icon = "HAND"
    gk_category = "输入"
    gk_doc = "手柄按键是否刚按下(本帧)。做跳跃 / 攻击用这个，避免按住连发"
    gk_prop_defs = (
        ("gk_button", "enum", "JOY_BUTTON_A", "按键", "", _JOY_BUTTONS),
        ("gk_device", "int", 0, "手柄编号", "0 表示第一个手柄"),
    )
    gk_outputs = (S.pin(BOOL, "刚按下", False),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_button", text="")
        row.prop(self, "gk_device", text="")


@N
class GKNodeJoyVibrate(B.GKStatementNode):
    bl_idname = "GKNodeJoyVibrate"
    bl_label = "手柄_震动"
    bl_icon = "RNDCURVE"
    gk_category = "输入"
    gk_doc = "手柄震动一下(受击 / 爆炸反馈)。用场景树计时器自动停，不需要自己收尾"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "强弱", 0.6),
        S.pin(FLOAT, "时长", 0.2),
    )
    gk_input_props = {"强弱": "gk_weak", "时长": "gk_time"}
    gk_prop_defs = (
        ("gk_weak", "float", 0.6, "弱马达", "0~1"),
        ("gk_strong", "float", 0.6, "强马达", "0~1"),
        ("gk_time", "float", 0.2, "时长", "秒"),
        ("gk_device", "int", 0, "手柄编号", "0 表示第一个手柄"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_weak", text="")
        row.prop(self, "gk_strong", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_time", text="")
        row.prop(self, "gk_device", text="")


# ==========================================================================
# 触屏
# ==========================================================================
@N
class GKNodeTouchButton(B.GKExpressionNode):
    bl_idname = "GKNodeTouchButton"
    bl_label = "触屏_按键按住"
    bl_icon = "HAND"
    gk_category = "输入"
    gk_doc = ("触屏上某个输入动作是否按住。移动端的虚拟按键通常会映射成输入动作，"
              "所以和键盘共用同一套动作名")
    gk_prop_defs = (
        ("gk_action", "string", "jump", "动作名",
         "Godot 输入映射里的动作名。用 move_*/jump 这类名字导出时会自动绑好按键"),
    )
    gk_outputs = (S.pin(BOOL, "按住", False),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_action", text="")


@N
class GKNodeTouchDrag(B.GKExpressionNode):
    bl_idname = "GKNodeTouchDrag"
    bl_label = "触屏_拖动方向"
    bl_icon = "ORIENTATION_GIMBAL"
    gk_category = "输入"
    gk_doc = ("手指这次拖动产生的方向向量(松开手指归零)。做虚拟摇杆用。"
              "配合『输入_方向向量』那种判定逻辑即可驱动移动")
    gk_inputs = (S.pin(FLOW, "执行"),)

    gk_prop_defs = (
        ("gk_scale", "float", 0.02, "灵敏度", "像素转成向量的比例"),
        ("gk_clamp", "float", 1.0, "最大长度", "向量长度上限"),
        ("gk_reset", "bool", True, "松手归零", "手指松开后是否把向量清零"),
    )
    gk_outputs = (S.pin(VECTOR2, "方向", (0.0, 0.0)),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_scale", text="")
        row.prop(self, "gk_clamp", text="")
        layout.prop(self, "gk_reset", text="松手归零", toggle=True)


# ==========================================================================
# 屏幕效果
# ==========================================================================
@N
class GKNodeScreenFilter(B.GKStatementNode):
    bl_idname = "GKNodeScreenFilter"
    bl_label = "屏幕_滤镜"
    bl_icon = "SHADERFX"
    gk_category = "屏幕效果"
    gk_doc = ("给整个画面蒙一层颜色(受击变红 / 中毒变绿 / 回忆泛黄)。"
              "用全屏着色器材质实现，强度可平滑过渡")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "强度", 0.5),
    )
    gk_input_props = {"强度": "gk_amount"}
    gk_prop_defs = (
        ("gk_amount", "float", 0.5, "强度", "0 = 原画面，1 = 完全染色"),
        ("gk_color", "color", (1.0, 0.15, 0.15, 1.0), "颜色", "叠加的颜色"),
        ("gk_softness", "float", 0.0, "过渡秒数", "0 表示立刻生效"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_color", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_amount", text="")
        row.prop(self, "gk_softness", text="")


# ==========================================================================
# 实用
# ==========================================================================
@N
class GKNodeComment(B.GKEventNode):
    bl_idname = "GKNodeComment"
    bl_label = "注释框"
    bl_icon = "INFO"
    gk_category = "实用"
    gk_doc = "在节点图里写备注，不生成任何代码"
    gk_is_comment = True
    gk_outputs = ()
    gk_prop_defs = (("gk_text", "string", "在这里写备注", "备注", ""),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_text", text="")


# ==========================================================================
# 引脚改名迁移
# ==========================================================================
# ── 为什么必须做(实测踩过)──────────────────────────────────────
# `build_sockets()` 只在节点 **init()** 时跑一次。所以改了
# `gk_inputs` 里的引脚名之后,**已经存在于 .blend 里的节点不会更新** ——
# 文件里那个节点的 `inputs` 还是旧名字。
#
# 表现极其隐蔽:
#     * 类的 gk_inputs 显示『上游』
#     * 文件里的节点 inputs 却是 ['进入', '条件']
#     * 用户按新名字连线连不上
#     * 导出时链路是空的,`_physics_process` 一片空白,**没有告警**
#
# 所以注册时主动扫一遍并把旧名字换掉 —— 顺便**恢复被改名丢掉的连线**
# (Blender 在 socket 名字变了之后会把原来的 link 丢掉)。
PIN_RENAMES = {
    "GKNodeAnimTransition": {"进入": "上游"},
}


def migrate_pin_names():
    """把所有 GK 节点树里过期的引脚名换成新的。返回改了几个节点。"""
    try:
        import bpy
    except Exception:
        return 0
    changed = 0
    for tree in getattr(getattr(bpy, "data", None), "node_groups", ()) or ():
        if getattr(tree, "bl_idname", "") not in ("GKGameLogicTree",
                                                  "GKAnimationTree"):
            continue
        for node in getattr(tree, "nodes", ()) or ():
            mapping = PIN_RENAMES.get(getattr(node, "bl_idname", ""), {})
            wanted = [spec.get("name") for spec in (node.gk_inputs or ())]
            # ── 除了改名,还要删掉**已经不存在**的引脚(用户 2026-10-03)──
            # 用户反复问"这些重复为什么删不掉":我在代码里把重复的物体引脚
            # 删了,但他**已经保存的 .blend** 里,那些引脚是当数据存着的 ——
            # 新建的节点没这个问题,老节点会一直顶着旧引脚。
            # 判据:当前引脚名既不在声明里、也不在"动态引脚"的名单里
            # (『进入 2』『出口 3』这种是按数量生成的),就重建。
            dynamic_prefixes = ("进入 ", "出口 ", "分支 ", "值 ", "输入 ")
            stale = []
            for socket in getattr(node, "inputs", ()) or ():
                name = getattr(socket, "name", "")
                if name in wanted:
                    continue
                if any(name.startswith(p) for p in dynamic_prefixes):
                    continue
                stale.append(name)
            if not stale:
                continue
            rebuild = getattr(node, "rebuild_sockets", None)
            if rebuild is None:
                continue
            try:
                rebuild()
                changed += 1
            except Exception:
                pass
    return changed


# ==========================================================================
# 动画树里能用的"取值"节点 —— 一份名单,改这里就够了
# ==========================================================================
# ── 为什么用名单而不是在每个类上写 gk_tree_type ──────────────────────
# 因为要回答的问题只有一个是重要的:"动画树的转场条件里到底能拿什么?"
# 写在 25 个类里的话,这个问题要翻遍 3600 行才答得上来,而且**很容易漏**。
#
# 判据:**纯取值**(没有执行流、什么时候求值都安全)。执行流节点
# (打印/移动/生成实例…)不放进来 —— 动画树是"每帧算一次条件"的图,
# 塞语句进去会让人以为能写逻辑,而且求值时机完全不可控。
#
# 用户 2026-10-03 的要求:"转场条件要能像蓝图那样就地拼"
# (他的实际报错是动画树里加『常量_数值』直接 RuntimeError)。
ANIM_TREE_VALUE_NODES = (
    # 常量 / 数学 / 向量 / 逻辑 / 转换 —— 转场条件的基本材料
    "GKNodeConstFloat", "GKNodeConstText", "GKNodeConstBool",
    "GKNodeConstVector",
    "GKNodeMath", "GKNodeRandomRange",
    "GKNodeVectorMake", "GKNodeVectorSplit", "GKNodeVectorLength",
    "GKNodeVectorNormalize",
    "GKNodeCompare", "GKNodeLogic", "GKNodeConvert",
    # 输入 / 手柄 —— "按住 A 且在地面"这类条件不用回玩法图拼
    "GKNodeActionPressed", "GKNodeActionJustPressed",
    "GKNodeGetAxis", "GKNodeGetVector",
    "GKNodeJoyAxis", "GKNodeJoyVector",
    "GKNodeJoyButton", "GKNodeJoyButtonDown",
    # 动画自身的查询 —— "这段播完了吗""还有多久"
    "GKNodeAnimPlaying", "GKNodeAnimLength",
    # 角色状态 —— 和『动画判断_移动状态』输出的是同一批判断
    "GKNodeIsOnFloor",
)

for _cls in CLASSES:
    if _cls.bl_idname in ANIM_TREE_VALUE_NODES:
        _cls.gk_tree_type = BOTH_TREES

ALL_CLASSES = tuple(CLASSES)

# "添加节点"菜单里的分类排列顺序
CATEGORY_ORDER = ("事件", "流程", "移动", "动画", "动画系统", "实例", "变量", "信号", "输入",
                  "手柄", "触屏", "运算", "显示", "相机", "音效", "存档", "界面",
                  "屏幕效果", "射线", "定时", "列表", "字典", "时间", "实用", "动画(旧)")

# ── 二级子分类 ──────────────────────────────────────────────────────────
# 用户 2026-10-03(对着 UPBGE 的截图):"输入里分类键盘、鼠标、手柄,
# 其他的排布优化"。UPBGE 的『输入』下面是 键盘 / 鼠标 / 游戏手柄 / VR,
# 我们照这个把『输入』收成一个父分类。
#
# 87 个节点如果只按一级分类铺开，最大的「运算」有 13 项，
# 「事件/流程/移动」也不少，翻起来还是累。
# 这张表给**大类**再切一层，菜单变成两级:
#     运算 ▸ 常量 / 数学 / 向量 / 逻辑 / 转换
#     输入 ▸ 键盘 / 鼠标 / 手柄 / 触屏
# 没列在这里的分类保持一级(节点本来就少，再套一层反而多点一次)。
SUBCATEGORY_ORDER = {
    "运算": ("常量", "数学", "向量", "逻辑", "转换"),
    "输入": ("键盘", "鼠标", "手柄", "触屏"),
    "动画": ("播放", "骨骼"),
}

# 节点 bl_idname -> 二级分类名。只给上面列了 SUBCATEGORY_ORDER 的分类登记。
SUBCATEGORY_OF = {
    # 常量
    "GKNodeConstFloat": "常量",
    "GKNodeConstText": "常量",
    "GKNodeConstBool": "常量",
    "GKNodeConstVector": "常量",
    # 数学
    "GKNodeMath": "数学",
    "GKNodeRandomRange": "数学",
    # 向量
    "GKNodeVectorMake": "向量",
    "GKNodeVectorSplit": "向量",
    "GKNodeVectorLength": "向量",
    # 距离也放"向量"下(它算的就是两个位置之间那个向量有多长)
    "GKNodeDistance": "向量",
    "GKNodeGetPosition": "向量",
    # 文本拼接放"逻辑"旁边(和『逻辑_比较』一样是"加工数据"的)
    "GKNodeTextJoin": "逻辑",
    "GKNodeVectorNormalize": "向量",
    # 逻辑
    "GKNodeCompare": "逻辑",
    "GKNodeLogic": "逻辑",
    # 转换
    "GKNodeConvert": "转换",
    # 输入 / 键盘
    "GKNodeKey": "键盘",
    "GKNodeActionPressed": "键盘",
    "GKNodeActionJustPressed": "键盘",
    "GKNodeGetAxis": "键盘",
    "GKNodeInputDirection": "键盘",
    "GKNodeGetVector": "键盘",
    "GKNodeOnInput": "键盘",
    # 输入 / 鼠标
    "GKNodePlayAnim": "播放",   # 动画_播放
    "GKNodeAnimParam": "播放",   # 动画_设混合参数
    "GKNodeAnimStatus": "播放",   # 动画_运行状态
    "GKNodeAnimStateMachineRef": "播放",   # 动画_状态机
    "GKNodeAnimPlaying": "播放",   # 动画_是否在播
    "GKNodeAnimPause": "播放",   # 动画_暂停/继续
    "GKNodeAnimStop": "播放",   # 动画_停止
    "GKNodeAnimSetSpeed": "播放",   # 动画_设播放速度
    "GKNodeAnimLength": "播放",   # 动画_时长
    "GKNodeAnimFinished": "播放",   # 动画_播放完成
    "GKNodeSetState": "播放",   # 动画_切换状态
    "GKNodeGetBone": "骨骼",   # 骨骼_取数据
    "GKNodeSetBone": "骨骼",   # 骨骼_设置
    "GKNodeBoneLookAtCamera": "骨骼",   # 骨骼_跟随视角
    "GKNodeMouseButton": "鼠标",
    "GKNodeMouseButtonDown": "鼠标",
    "GKNodeMouseWheel": "鼠标",
    "GKNodeMousePosition": "鼠标",
    "GKNodeMouseMotion": "鼠标",
    "GKNodeMouseMode": "鼠标",
    # 输入 / 手柄
    "GKNodeJoyAxis": "手柄",
    "GKNodeJoyVector": "手柄",
    "GKNodeJoyButton": "手柄",
    "GKNodeJoyButtonDown": "手柄",
    "GKNodeJoyVibrate": "手柄",
    # 输入 / 触屏
    "GKNodeTouchButton": "触屏",
    "GKNodeTouchDrag": "触屏",
}

# 「常用」短名单:新手最需要的前 12 个，直接放菜单最上层。
# 目的是让第一次打开菜单的人不用翻 23 个分类就能起步。
COMMON_NODES = (
    "GKNodeOnReady",
    "GKNodeOnProcess",
    "GKNodeOnPhysicsProcess",
    "GKNodeOnInput",
    "GKNodeBranch",
    "GKNodeSequence",
    "GKNodeDoOnce",
    "GKNodeForLoop",
    "GKNodePrint",
    "GKNodeWait",
    "GKNodeSetVar",
    "GKNodeGetVar",
    "GKNodeMoveDirection",
    "GKNodeMoveAndSlide",
    "GKNodeJump",
    "GKNodeActionJustPressed",
    "GKNodeGetVector",
    "GKNodeComment",
)
