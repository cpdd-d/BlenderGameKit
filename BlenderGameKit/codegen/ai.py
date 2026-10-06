"""AI 图(『游戏AI』树)的代码生成 —— 状态机 + 行为(第四十一轮)。

『AI_启用』被走到时,生成器**现场把 AI 图展开**(和『动画_状态机』
展开动画树、『界面_套用设计』展开设计图同一套路):

  * 每个『AI_状态』-> 一段"行为代码"(巡逻 / 追击 / 逃跑…),
    每物理帧执行**当前状态**的那一段;
  * 每条『AI_转场』-> 一条 if 判断(画布上谁在上面谁先判),
    条件成立就改状态变量。

生成的骨架(以"巡逻 / 追击"两个状态为例):

    # _physics_process 里,每个物理帧:
    if ai_state == "巡逻":
        <巡逻行为:去 A / 去 B,到了翻面>
    elif ai_state == "追击":
        <追击行为:朝玩家走,贴近了停>
    if ai_state == "巡逻":
        if <看见玩家>: ai_state = "追击"
    elif ai_state == "追击":
        if <丢失玩家>: ai_state = "巡逻"

行为一律走 `velocity + move_and_slide()`(和『移动_朝向目标』同一套),
所以『作用在谁』必须是 CharacterBody —— validation.required_base_class
会**先扫出来**定基类,否则生成完才发现"这个基类没有 velocity"就晚了。
"""

from ..nodes.node_base import (TARGET_SELF, gd_string, snake_case)
from ..nodes import nodes as node_lib

from .literals import gd_number
from .registry import _chain, expr_node, stmt_node


# ==========================================================================
# 小工具
# ==========================================================================
def _ai_point(ctx, state, prop):
    """巡逻点的节点引用表达式;没选返回 None(调用方退化 + 图解析已报过)。"""
    raw = str(getattr(state, prop, "") or "").strip()
    if not raw or raw == TARGET_SELF:
        return None
    return ctx.target_expr(state, prop, hint="节点")


def _ai_goal_expr(ctx, node, prop="gk_goal"):
    """转场 / 状态卡上的『目标』引用表达式;没选返回 None。"""
    raw = str(getattr(node, prop, "") or "").strip()
    if not raw or raw == TARGET_SELF:
        return None
    return ctx.target_expr(node, prop, hint="节点")


def _vector_zero(ctx):
    return "Vector2.ZERO" if ctx.dim == 2 else "Vector3.ZERO"


def _emit_ai_facing(ctx, indent, self_expr, face_vec, facing):
    """按『朝向』档位生成转身代码。

    face_vec 给了才转 —— 取不到目标的退化分支上直接不生成。
    GOAL: 看着那个向量(目标 / 威胁 / 下一个巡逻点);
    MOVE: 头跟着**速度方向**转(停下不动时不转)。
    """
    if facing == "KEEP" or face_vec is None:
        return
    if facing == "MOVE":
        if ctx.dim == 2:
            ctx.add_line(indent, f"if {self_expr}.velocity.length() > 0.001:")
            ctx.add_line(indent + 1,
                         f"{self_expr}.rotation = atan2({self_expr}.velocity.y, "
                         f"{self_expr}.velocity.x)")
        else:
            ctx.add_line(indent, f"if {self_expr}.velocity.length() > 0.001:")
            ctx.add_line(indent + 1,
                         f"if absf({self_expr}.velocity.x) + absf({self_expr}.velocity.z) > 0.001:")
            ctx.add_line(indent + 2,
                         f"{self_expr}.look_at({self_expr}.global_position + "
                         f"Vector3({self_expr}.velocity.x, 0.0, "
                         f"{self_expr}.velocity.z), Vector3.UP)")
        return
    # GOAL:面向关注点。3D 要**先压平 Y** —— 目标在头顶正上方时
    # look_at 的 up 向量和方向重合,Godot 会报 "Up vector and direction
    # between node origin and target are aligned"(和『移动_朝向目标』
    # 同一个坑,第三十六轮修过)。
    flat = ctx.new_temp("ai_flat")
    if ctx.dim == 2:
        ctx.add_line(indent, f"var {flat} := {face_vec}")
        ctx.add_line(indent, f"if {flat}.length() > 0.001:")
        ctx.add_line(indent + 1,
                     f"{self_expr}.rotation = atan2({flat}.y, {flat}.x)")
    else:
        ctx.add_line(indent, f"var {flat} := Vector3({face_vec}.x, 0.0, {face_vec}.z)")
        ctx.add_line(indent, f"if {flat}.length() > 0.001:")
        ctx.add_line(indent + 1,
                     f"{self_expr}.look_at({self_expr}.global_position + {flat}, Vector3.UP)")


def _stop_and_slide(ctx, indent, self_expr):
    """退化 / 待机:刹住 + 走一帧(保持和碰撞、地面的接触)。"""
    ctx.add_line(indent, f"{self_expr}.velocity = {_vector_zero(ctx)}")
    ctx.add_line(indent, f"{self_expr}.move_and_slide()")


# ==========================================================================
# 行为:每个『AI_状态』生成一段
# ==========================================================================
def _emit_ai_behavior(ctx, indent, machine, state):
    """把一张 AI 状态卡的**行为**生成成代码(每帧执行一次)。"""
    from .expressions import ensure_position_helper
    reader = ensure_position_helper(ctx)

    behavior = str(getattr(state, "gk_behavior", "PATROL") or "PATROL")
    self_expr = machine["self"]
    facing = str(getattr(state, "gk_facing", "GOAL") or "GOAL")
    speed = gd_number(float(getattr(state, "gk_speed", 3.0) or 0.0))
    stop = gd_number(float(getattr(state, "gk_stop_distance", 0.6) or 0.0))

    if behavior == "STAND":
        _stop_and_slide(ctx, indent, self_expr)
        return

    # ---- 追击 / 跟随:朝目标走,贴近了停 ----
    if behavior in ("CHASE", "FOLLOW"):
        goal = _ai_goal_expr(ctx, state)
        if goal is None:
            # 图解析已经报过"没选目标",这里退化,免得生成出指向空物的代码
            _stop_and_slide(ctx, indent, self_expr)
            return
        here = ctx.new_temp("ai_here")
        diff = ctx.new_temp("ai_diff")
        dist = ctx.new_temp("ai_dist")
        ctx.add_line(indent, f"var {here} := {reader}({self_expr})")
        ctx.add_line(indent, f"var {diff} := {reader}({goal}) - {here}")
        ctx.add_line(indent, f"var {dist} := {diff}.length()")
        ctx.add_line(indent, f"if {dist} > {stop}:")
        ctx.add_line(indent + 1, f"{self_expr}.velocity = {diff}.normalized() * {speed}")
        ctx.add_line(indent, "else:")
        ctx.add_line(indent + 1, f"{self_expr}.velocity = {_vector_zero(ctx)}")
        _emit_ai_facing(ctx, indent, self_expr, diff, facing)
        ctx.add_line(indent, f"{self_expr}.move_and_slide()")
        return

    # ---- 逃跑:目标靠近到『停在多远』以内就往反方向跑 ----
    if behavior == "FLEE":
        goal = _ai_goal_expr(ctx, state)
        if goal is None:
            _stop_and_slide(ctx, indent, self_expr)
            return
        here = ctx.new_temp("ai_here")
        away = ctx.new_temp("ai_away")
        dist = ctx.new_temp("ai_dist")
        ctx.add_line(indent, f"var {here} := {reader}({self_expr})")
        ctx.add_line(indent, f"var {away} := {here} - {reader}({goal})")
        ctx.add_line(indent, f"var {dist} := {away}.length()")
        ctx.add_line(indent, f"if {dist} <= {stop}:")
        ctx.add_line(indent + 1, f"{self_expr}.velocity = {away}.normalized() * {speed}")
        ctx.add_line(indent, "else:")
        ctx.add_line(indent + 1, f"{self_expr}.velocity = {_vector_zero(ctx)}")
        # 『朝目标』= 看着威胁(追兵):face_vec 用「目标 - 自己」= -away
        face = ctx.new_temp("ai_face")
        ctx.add_line(indent, f"var {face} := -{away}")
        _emit_ai_facing(ctx, indent, self_expr, face, facing)
        ctx.add_line(indent, f"{self_expr}.move_and_slide()")
        return

    # ---- 巡逻 / 返回:去巡逻点(A / B 或只去 A)----
    point_a = _ai_point(ctx, state, "gk_patrol_a")
    point_b = _ai_point(ctx, state, "gk_patrol_b") if behavior == "PATROL" else None
    if point_a is None:
        _stop_and_slide(ctx, indent, self_expr)
        return
    flip = machine.get("flip")
    here = ctx.new_temp("ai_here")
    goal = ctx.new_temp("ai_goal")
    diff = ctx.new_temp("ai_diff")
    ctx.add_line(indent, f"var {here} := {reader}({self_expr})")
    if point_b is not None and flip:
        # 到了哪头就往另一头走(翻面开关存在成员变量里 —— 要跨帧记)
        ctx.add_line(indent,
                     f"var {goal} := {reader}({point_a}) if {flip} "
                     f"else {reader}({point_b})")
    else:
        ctx.add_line(indent, f"var {goal} := {reader}({point_a})")
    ctx.add_line(indent, f"var {diff} := {goal} - {here}")
    ctx.add_line(indent, f"if {diff}.length() <= {stop}:")
    if point_b is not None and flip:
        ctx.add_line(indent + 1, f"{flip} = not {flip}")
    # 到点这一帧**刹住**:不清的话会用上一帧的速度多滑一步(0.05 米左右的
    # 抖动 —— 单看不明显,但"到点回头"时会看出一个小顿挫)
    ctx.add_line(indent + 1, f"{self_expr}.velocity = {_vector_zero(ctx)}")
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f"{self_expr}.velocity = {diff}.normalized() * {speed}")
    _emit_ai_facing(ctx, indent, self_expr, diff, facing)
    ctx.add_line(indent, f"{self_expr}.move_and_slide()")


# ==========================================================================
# 转场:每条『AI_转场』一个 if
# ==========================================================================
def _ai_edge_condition(ctx, edge, machine):
    """一条边的条件表达式;算不出来返回 None(图解析已经报过原因)。"""
    from .expressions import ensure_position_helper
    reader = ensure_position_helper(ctx)
    trans = edge.get("transition")
    kind = edge.get("kind")

    if kind in ("SEE", "LOST"):
        goal = _ai_goal_expr(ctx, trans)
        if goal is None:
            return None
        sight = gd_number(float(getattr(trans, "gk_sight", 12.0) or 12.0))
        distance = (f"({reader}({goal}) - "
                    f"{reader}({machine['self']})).length()")
        return (f"{distance} <= {sight}" if kind == "SEE"
                else f"{distance} > {sight}")

    # kind == "pin":接在『条件』引脚上的表达式。
    socket = ctx._socket(trans, "条件")
    if socket is None or not getattr(socket, "is_linked", False):
        return None
    # ── AI 图里的『AI_看得见』要知道"自己"是谁 ────────────────────────
    # 玩法图里"自己"= 脚本物体;AI 图里"自己"= 『AI_启用』的作用物体
    # (它可能不是脚本物体 —— 比如一个管理器脚本统管所有敌人)。
    # 用上下文变量把这个身份带进表达式生成(见 _expr_ai_see)。
    saved = getattr(ctx, "_ai_self_expr", None)
    ctx._ai_self_expr = machine["self"]
    try:
        return ctx.expr_of(socket)
    finally:
        ctx._ai_self_expr = saved


def _emit_ai_transitions(ctx, indent, machine):
    """状态内按画布顺序判转场:第一个成立的就切(和动画状态机同一套)。"""
    graph = machine["graph"]
    var = machine["var"]
    emitted = 0
    for state in graph["states"]:
        name = node_lib.ai_state_name(state)
        if not name:
            continue
        state_key = node_lib.node_key(state)
        outgoing = [e for e in graph["edges"]
                    if node_lib.node_key(e["from"]) == state_key]
        if not outgoing:
            continue
        outgoing.sort(key=lambda e: (e["order"], node_lib.ai_state_name(e["to"])))
        ctx.add_line(indent, f"{'if' if emitted == 0 else 'elif'} "
                             f"{var} == {gd_string(name)}:")
        first = True
        for edge in outgoing:
            condition = _ai_edge_condition(ctx, edge, machine)
            if not condition:
                continue
            target = node_lib.ai_state_name(edge["to"])
            if not target:
                continue
            ctx.add_line(indent + 1, f"{'if' if first else 'elif'} {condition}:")
            ctx.add_line(indent + 2, f"{var} = {gd_string(target)}")
            if machine["debug"]:
                # 打印**变量本身**(状态名是用户填的,可能带引号 ——
                # 直接拼进字符串字面量会把生成的脚本弄坏)
                ctx.add_line(indent + 2, f'print("[AI] 切到状态: ", {var})')
            first = False
        if first:
            # 这一段一条转移都没生成出来 —— 留个 pass 免得语法错误
            ctx.add_line(indent + 1, "pass")
        emitted += 1
    if emitted == 0:
        ctx.add_line(indent, "pass")


def _emit_ai_graph(ctx, indent, machine):
    """把一台 AI 状态机生成到 _physics_process:先行为,后转场。"""
    emitted = 0
    for state in machine["graph"]["states"]:
        name = node_lib.ai_state_name(state)
        if not name:
            continue
        ctx.add_line(indent, f"{'if' if emitted == 0 else 'elif'} "
                             f"{machine['var']} == {gd_string(name)}:")
        _emit_ai_behavior(ctx, indent + 1, machine, state)
        emitted += 1
    if emitted == 0:
        ctx.add_line(indent, "pass")
    _emit_ai_transitions(ctx, indent, machine)


# ==========================================================================
# 节点处理器
# ==========================================================================
@stmt_node("GKNodeAiEnable")
def _stmt_ai_enable(ctx, node, indent):
    """『AI_启用』:把一张 AI 图展开成状态机 + 行为(见模块说明)。"""
    raw = str(getattr(node, "gk_tree_name", "") or "").strip()
    tree = node_lib.find_ai_tree(raw)
    if tree is None:
        ctx.warn(f"节点『{node.bl_label}』找不到 AI 图"
                 f"(填的是『{raw}』)—— 在侧栏『游戏AI』面板里点"
                 f"『打开/新建 AI 图』建一张,放几个『AI_状态』")
        return
    graph = node_lib.ai_tree_graph(tree)
    for problem in graph["problems"]:
        ctx.warn(problem)
    if graph["entry"] is None:
        ctx.warn(f"AI 图『{tree.name}』里没有可用的状态 —— 先放一个"
                 f"『AI_状态』(它没被任何转场指到 = 起始状态)")
        return

    # ── 谁跑这张图(必须是角色体:行为都是 velocity + move_and_slide)──
    body_type = "CharacterBody2D" if ctx.dim == 2 else "CharacterBody3D"
    target = ctx.target_expr(node, "gk_target", hint="角色", node_type=body_type)

    # ── 状态变量:按**名字**共享(和『动画_状态机』同一套)────────────
    # 名字就是身份:一台机器占一个名字;第二台还想用同一个名字时明确告警,
    # 让它去改『状态变量名』—— 而不是默默互相覆盖(敌人会乱切状态)。
    requested = snake_case(getattr(node, "gk_state_var", "ai_state"), "ai_state")
    owners = getattr(ctx, "_ai_state_var_owners", None)
    if owners is None:
        owners = {}
        ctx._ai_state_var_owners = owners
    mine = node_lib.node_key(node)
    if requested in owners and owners[requested] != mine:
        ctx.warn(f"有两个『AI_启用』都用『状态变量名 = {requested}』—— "
                 f"它们会互相覆盖(AI 会乱切状态)。把其中一个改个名字"
                 f"(比如 ai_state_2)")
    owners[requested] = mine
    var_name = ctx.declare_var(requested, "String")

    # ── 进入起始状态 ────────────────────────────────────────────────
    # 守卫 `.is_empty()`:这个节点接在『每当_物理帧』后面也安全 ——
    # 没有守卫的话每帧都会把状态重置回起点,永远走不出起始状态。
    entry_name = node_lib.ai_state_name(graph["entry"])
    ctx.add_line(indent, f"if {var_name}.is_empty():")
    ctx.add_line(indent + 1, f"{var_name} = {gd_string(entry_name)}")

    # ── 巡逻的"当前去哪头"开关(要跨帧记 -> 成员变量)────────────────
    has_patrol = any(str(getattr(s, "gk_behavior", "") or "") == "PATROL"
                     for s in graph["states"])
    flip_var = None
    if has_patrol:
        index = len(getattr(ctx, "ai_machines", ()) or ()) + 1
        flip_var = ctx.declare_var(f"_gk_ai_patrol_{index}", "bool",
                                   initial="false")

    ctx.ai_machines.append({
        "node": node,
        "graph": graph,
        "var": var_name,
        "self": target,
        "debug": bool(getattr(node, "gk_debug_print", False)),
        "flip": flip_var,
    })
    ctx.need_ai_process = True

    _chain(ctx, node, indent, "完成")


@expr_node("GKNodeAiSee")
def _expr_ai_see(ctx, node):
    """『AI_看得见』:距离 + 视野角 -> 布尔(helper 里的算法见生成体)。"""
    from .expressions import ensure_position_helper
    reader = ensure_position_helper(ctx)
    helper = _ensure_ai_see_helper(ctx)

    socket = ctx._socket(node, "目标")
    if socket is not None and getattr(socket, "is_linked", False):
        goal = ctx.expr_of(socket)
    else:
        goal = _ai_goal_expr(ctx, node)
        if goal is None:
            ctx.warn(f"『{node.bl_label}』没选『目标』也没接引脚 —— "
                     f"它永远返回 false")
            return "false"
    # "自己"是谁:AI 图里由『AI_启用』的作用物体决定(见 _ai_edge_condition);
    # 玩法图里就是脚本所在物体。
    self_expr = getattr(ctx, "_ai_self_expr", None) or "self"
    dist = ctx.input_number(node, "视野距离", "12.0")
    angle = ctx.input_number(node, "视野角", "120.0")
    return f"{helper}({self_expr}, {goal}, {dist}, {angle})"


def _ensure_ai_see_helper(ctx):
    """确保"看得见吗"的辅助函数已经生成,返回它的名字。

    3D 用**物体的前方**(-Z)和目标的水平方向在平面内比夹角;
    2D 用本地 X 轴(Vector2.RIGHT 按 rotation 转)。
    `angle_deg <= 0 或 >= 360` = 不看角度(只看距离)。
    """
    from .expressions import ensure_position_helper
    reader = ensure_position_helper(ctx)
    made = getattr(ctx, "_ai_see_helpers", None)
    if made is None:
        made = set()
        ctx._ai_see_helpers = made
    key = "2d" if ctx.dim == 2 else "3d"
    name = f"_gk_ai_can_see_{key}"
    if key in made:
        return name
    made.add(key)
    signature = (f"func {name}(watcher: Node, target: Node, dist: float, "
                 f"angle_deg: float) -> bool:")
    if ctx.dim == 2:
        body = [
            "    if target == null:",
            "        return false",
            f"    var diff := {reader}(target) - {reader}(watcher)",
            "    if diff.length() > dist:",
            "        return false",
            "    if angle_deg <= 0.0 or angle_deg >= 360.0:",
            "        return true",
            "    if watcher is not Node2D:",
            "        return true      # 拿不到朝向就不判角度(只看距离)",
            "    var forward: Vector2 = Vector2.RIGHT.rotated("
            "(watcher as Node2D).global_rotation)",
            "    if diff.length() < 0.001:",
            "        return true",
            "    return absf(rad_to_deg(forward.angle_to(diff))) <= angle_deg * 0.5",
        ]
    else:
        body = [
            "    if target == null:",
            "        return false",
            f"    var diff := {reader}(target) - {reader}(watcher)",
            "    if diff.length() > dist:",
            "        return false",
            "    if angle_deg <= 0.0 or angle_deg >= 360.0:",
            "        return true",
            "    if watcher is not Node3D:",
            "        return true      # 拿不到朝向就不判角度(只看距离)",
            "    var forward := -(watcher as Node3D).global_transform.basis.z",
            "    var flat := Vector3(diff.x, 0.0, diff.z)",
            "    if flat.length() < 0.001:",
            "        return true",
            "    return absf(rad_to_deg("
            "forward.signed_angle_to(flat, Vector3.UP))) <= angle_deg * 0.5",
        ]
    ctx.extra_sections.append({
        "signature": signature,
        "lines": body,
        "empty_body": "    return false",
        "separator": True,
    })
    return name