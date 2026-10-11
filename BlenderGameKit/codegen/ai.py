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


def _patrol_point_filled(state, prop):
    """这个巡逻点填了吗?空 / 『自身』都算没填(和 `_ai_point` 同一套判断)。"""
    raw = str(getattr(state, prop, "") or "").strip()
    return bool(raw) and raw != TARGET_SELF


def _patrol_has_extra_points(state):
    """填了『巡逻点 C / D』吗?(填了才需要"索引 + 方向"那套)"""
    return any(_patrol_point_filled(state, prop)
               for prop in ("gk_patrol_c", "gk_patrol_d"))


def _patrol_uses_waypoints(state):
    """这个状态会走"索引 + 方向"那条往返路吗?

    要求"**填了 A** 且填了 C / D":A 都没填的话 `_emit_ai_behavior` 会
    直接站着不动(图解析已经报过),那种时候别白白声明两个用不上的成员变量。
    """
    if str(getattr(state, "gk_behavior", "") or "") != "PATROL":
        return False
    return (_patrol_point_filled(state, "gk_patrol_a")
            and _patrol_has_extra_points(state))


def _ai_enter_on_entry(state):
    """"进入这个状态"要不要跑它的『进入时』?

    ── 为什么『攻击』例外(第七十九轮)────────────────────────────────
    『攻击』状态的『进入时』= **每打一下跑一次**(见 `_emit_ai_behavior`)。
    要是进状态时也照常跑一遍,那"看见玩家 -> 切进攻击状态"就会在怪还在
    往玩家那边赶路的时候先打一下空拳(表现:凭空掉血 / 响一声)。
    所以攻击状态只在"冷却到点 + 已在攻击范围内"时跑它。
    """
    return str(getattr(state, "gk_behavior", "") or "") != "ATTACK"


def _emit_patrol_waypoints(ctx, indent, self_expr, reader, stop, speed, facing,
                           points, index_var, dir_var, nav_layers=None):
    """三点以上的巡逻:按**填了的点**往返走(第七十八轮)。

    A→B→C→D 然后 D→C→B→A。当前第几个点 + 往前往后各占一个成员变量 ——
    两点那套"翻面布尔"表达不了四个点(它只有"在哪一头"这个信息)。

    点的顺序就是 A→B→C→D 里**填了的那些**(跳着填 = 中间那点不存在,
    不会走一个空引用)。
    """
    here = ctx.new_temp("ai_here")
    goal_at = ctx.new_temp("ai_goal")
    diff = ctx.new_temp("ai_diff")
    last = len(points) - 1
    ctx.add_line(indent, f"var {here} := {reader}({self_expr})")
    # 先按第 0 个点取一次,再按索引覆盖 —— 比一串嵌套三元好读
    ctx.add_line(indent, f"var {goal_at} := {reader}({points[0]})")
    for order, point in enumerate(points[1:], start=1):
        ctx.add_line(indent,
                     f"{'if' if order == 1 else 'elif'} {index_var} == {order}:")
        ctx.add_line(indent + 1, f"{goal_at} = {reader}({point})")
    ctx.add_line(indent, f"var {diff} := {goal_at} - {here}")
    # 巡逻同样绕障碍(和两点那条一样)
    walk = _walk_direction(ctx, indent, self_expr, goal_at, diff, nav_layers)
    ctx.add_line(indent, f"if {diff}.length() <= {stop}:")
    # 到两头的边界就掉头,其余沿当前方向继续 —— 于是 A→B→C→D→C→B→A
    ctx.add_line(indent + 1, f"if {index_var} >= {last}:")
    ctx.add_line(indent + 2, f"{dir_var} = false")
    ctx.add_line(indent + 1, f"elif {index_var} <= 0:")
    ctx.add_line(indent + 2, f"{dir_var} = true")
    ctx.add_line(indent + 1, f"if {dir_var}:")
    ctx.add_line(indent + 2, f"{index_var} += 1")
    ctx.add_line(indent + 1, "else:")
    ctx.add_line(indent + 2, f"{index_var} -= 1")
    # 到点这一帧**刹住**(和两点那条一样:不清速度会多滑一步)
    ctx.add_line(indent + 1, f"{self_expr}.velocity = {_vector_zero(ctx)}")
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f"{self_expr}.velocity = {walk} * {speed}")
    _emit_ai_facing(ctx, indent, self_expr, diff, facing)
    ctx.add_line(indent, f"{self_expr}.move_and_slide()")


# ==========================================================================
# AI 走导航网格(第六十三轮:"让 AI 行为直接用寻路")
# ==========================================================================
# ── 为什么是"自动的",不再给节点加开关 ──────────────────────────────
# 用户的原话:"寻路应该整合到游戏AI那里去"。追人的敌人一头撞墙是这个插件
# 里最刺眼的表现之一,而『移动_寻路』只是个**手动**节点 —— 要用户自己把
# AI 的每个追击状态再拼一套寻路,基本没人会做。
#
# 所以这里让 AI 的移动行为**默认就走导航网格**,开关只有一个:**场景设置
# 里那个『寻路』**(它决定有没有导航网格可走)。没勾 -> 一行代码都不变
# (老图产物逐字不变);勾了 -> 追击 / 跟随 / 攻击 / 去查看 / 巡逻 / 返回 /
# 逃跑 自动绕开墙和石头。不再加节点上的开关:场景那个开关已经是"要不要
# 寻路"的总闸,再加一层只会让人猜"到底哪个生效"。
#
# 『逃跑』第八十轮也接上了:投影点取在"背对威胁『逃跑距离』外",
#   导航只会带着它绕障碍远离威胁,不会绕到威胁脸上;投影点不可达时
#   helper 自带退回直线。
def _nav_dir_helper(ctx):
    """生成 `_gk_ai_nav_dir(host, goal)` —— "下一步朝哪走"(绕开障碍)。

    ── 为什么抽成顶层函数,不在每个行为里抄一遍 ─────────────────────
    『移动_寻路』那份路径代码有二十来行(查路径 / 盯第 1 个点 / 压平 Y /
    脚下点退化),AI 有五种行为要用。抄五遍 = 改一处要改五处。
    抽出来之后每个行为只多一句 `var dir := _gk_ai_nav_dir(self, 目标点)`。

    ── 跟服务器的路径走,不用 NavigationAgent 的 get_next_path_position ──
    理由和『移动_寻路』完全一样(实测:agent 的内部路径状态和"贴网格点 /
    父节点原点的高度差"纠缠,一直返回自己)。这里只借 `map_get_path`,
    行为完全可预测;NavigationAgent 只在『移动_寻路』勾了避障时另用。

    `host: Node` + 里面显式 cast:调用点的表达式可能是 `self`,也可能是
    @onready 的引用,标成 Node 最稳(和 `_gk_heard_*` 同一个理由)。
    """
    from .expressions import ensure_position_helper
    reader = ensure_position_helper(ctx)

    made = getattr(ctx, "_ai_nav_helper", None)
    if made is not None:
        return made

    if ctx.dim == 2:
        vector = "Vector2"
        server = "NavigationServer2D"
        body_type = "CharacterBody2D"
        world = "get_world_2d"
    else:
        vector = "Vector3"
        server = "NavigationServer3D"
        body_type = "CharacterBody3D"
        world = "get_world_3d"

    name = "_gk_ai_nav_dir"
    lines = [
        f"    var body := host as {body_type}",
        "    if body == null:",
        f"        return {vector}.ZERO",
        f"    var here := {reader}(body)",
        f"    var path := {server}.map_get_path(body.{world}().navigation_map, "
        f"here, goal, true, layers)",
        f"    var step: {vector} = {vector}.ZERO",
        "    # ── 从第 1 个点往后找**第一个真的在别处的点**────────────────",
        "    # 不能只看 path[1]:第 0 个点是『离自己最近的网格点』,当自己正好",
        "    # 站在拐角上时,`path[1]` 会**和自己在同一个位置**(实测:敌人卡在",
        "    # 墙角时 `path[1] - here` 只有 0.08 米,于是退回直线 -> 直着顶墙",
        "    # -> 卡死)。跳过这些退化点就不会。",
        "    for i in range(1, path.size()):",
        f"        step = path[i] - here",
    ]
    if ctx.dim == 3:
        lines.append("        step.y = 0.0      # 路点高度压平,不压会上下拱")
    lines += [
        "        if step.length() >= 0.25:",
        "            return step.normalized()",
        "    # 一条路都拿不到(网格还没烤完 / 目标不可达)-> 退回直线,",
        "    # 至少不会站着不动",
        f"    var direct: {vector} = goal - here",
        "    if direct.length() < 0.001:",
        f"        return {vector}.ZERO",
        "    return direct.normalized()",
    ]
    ctx.extra_sections.append({
        "signature": f"func {name}(host: Node, goal: {vector}, "
                     f"layers: int = 1) -> {vector}:",
        "lines": lines,
        "empty_body": f"    return {vector}.ZERO",
        "separator": True,
    })
    ctx._ai_nav_helper = name
    return name


def _ai_nav_layers(state):
    """这个 AI 状态『走哪层』-> 层掩码(默认 1)。

    多层导航(第八十五轮):和物体『寻路层』/『移动_寻路』的『走哪层』
    同一套口径 —— 生成的掩码就交给 `_gk_ai_nav_dir` 的第 3 个参数。
    """
    from .statements import nav_layer_mask_of
    return nav_layer_mask_of(state)


def _walk_direction(ctx, indent, self_expr, goal_at, diff, layers=None):
    """这个行为**这一帧朝哪走** —— 返回一个 GDScript 表达式字符串。

    场景勾了『寻路』-> 走导航网格算出来的方向(AI 绕开障碍);
    没勾 -> 直线方向,生成的代码和以前**逐字一样**。

    `layers`(第八十五轮:多层导航)= 这个状态的『走哪层』掩码。
    第 1 层(默认)不带参数 —— 老图的调用点一个字都不变,只有那个公共
    helper 的签名多了个默认形参(行为完全一样)。
    """
    if not bool(getattr(ctx, "nav_enabled", False)):
        return f"{diff}.normalized()"
    helper = _nav_dir_helper(ctx)
    direction = ctx.new_temp("ai_dir")
    extra = f", {layers}" if layers and int(layers) != 1 else ""
    ctx.add_line(indent,
                 f"var {direction} := {helper}({self_expr}, {goal_at}{extra})")
    return direction


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


def _emit_investigate_reset(ctx, indent, machine, at_var, now_var):
    """听到**新噪音**的这一刻先把环顾的门关好(第八十轮 D-12③)。

    ⚠ 真机探针抓到:这段最早只写在"到点"分支里 —— 可敌人赶路要 3 秒,
      噪音记忆只有 1.2 秒,记忆一过 `not heard and inv_done`(默认 true)
      立刻成立,敌人半路就被『安静下来』拉回巡逻,根本走不到噪音点。
      现在赶路途中每帧也检查:听到时刻一变就 done=false、start=-1
      (-1 = 还没到点;到点那一帧才记起点朝向和环顾起点)。
    """
    inv = machine.get("inv")
    if not inv:
        return
    ctx.add_line(indent, f"if {inv['noise']} != {at_var}:")
    ctx.add_line(indent + 1, f"{inv['noise']} = {at_var}")
    ctx.add_line(indent + 1, f"{inv['on']} = true")
    ctx.add_line(indent + 1, f"{inv['done']} = false")
    ctx.add_line(indent + 1, f"{inv['start']} = -1.0")


def _emit_investigate_look(ctx, indent, machine, state, self_expr, now_var):
    """『去查看』走到噪音点后的**原地环顾**(第八十轮 D-12③)。

    到点的这一帧(`start == -1` 的那一帧)记住现在的朝向 + 起点时间,
    然后在『环顾秒数』内用正弦波左右摆头(±『环顾角度』);摆完把朝向
    还原,并置位 done —— CALM 边拿着这个 done 当"看完了吗"的门(见
    `_ai_edge_condition`)。赶路途中来了新噪音会把 start 重置成 -1
    (见 `_emit_investigate_reset`),到点自动从头再看一遍。
    """
    ctx.add_line(indent, f"{self_expr}.velocity = {_vector_zero(ctx)}")
    inv = machine.get("inv")
    if not inv:
        return  # 理论上不会:图里有 INVESTIGATE 状态就会声明(见启用节点)
    if ctx.dim == 2:
        rot = f"({self_expr} as Node2D).rotation"
    else:
        rot = f"({self_expr} as Node3D).rotation.y"
    angle = gd_number(float(getattr(state, "gk_look_angle", 70.0) or 0.0))
    secs = max(0.0, float(getattr(state, "gk_look_time", 2.0) or 0.0))
    secs_str = gd_number(secs)
    el_var = ctx.new_temp("ai_look_el")
    # 到点第一帧:记起点朝向 + 环顾起点(start 被 reset 置成 -1)
    ctx.add_line(indent, f"if {inv['on']} and {inv['start']} < 0.0:")
    ctx.add_line(indent + 1, f"{inv['start']} = {now_var}")
    ctx.add_line(indent + 1, f"{inv['base']} = {rot}")
    ctx.add_line(indent, f"if {inv['on']}:")
    ctx.add_line(indent + 1, f"var {el_var} := {now_var} - {inv['start']}")
    if secs <= 0.0:
        # 秒数填 0 = 不环顾(首帧立即收工)
        ctx.add_line(indent + 1, f"if {el_var} >= 0.0:")
    else:
        ctx.add_line(indent + 1, f"if {el_var} >= {secs_str}:")
    ctx.add_line(indent + 2, f"{inv['on']} = false")
    ctx.add_line(indent + 2, f"{inv['done']} = true")
    ctx.add_line(indent + 2, f"{rot} = {inv['base']}")
    if secs > 0.0:
        ctx.add_line(indent + 1, "else:")
        # 正弦摆头:TAU * t / 秒数 -> 一个周期正好左右各一次再回到正中
        ctx.add_line(indent + 2,
                     f"{rot} = {inv['base']} + deg_to_rad({angle}) * "
                     f"sin(TAU * {el_var} / {secs_str})")


# ==========================================================================
# 行为:每个『AI_状态』生成一段
# ==========================================================================
def _emit_ai_behavior(ctx, indent, machine, state):
    """把一张 AI 状态卡的**行为**生成成代码(每帧执行一次)。"""
    from .expressions import ensure_position_helper
    reader = ensure_position_helper(ctx)

    behavior = str(getattr(state, "gk_behavior", "PATROL") or "PATROL")
    state_name = node_lib.ai_state_name(state)
    self_expr = machine["self"]
    facing = str(getattr(state, "gk_facing", "GOAL") or "GOAL")
    speed = gd_number(float(getattr(state, "gk_speed", 3.0) or 0.0))
    stop = gd_number(float(getattr(state, "gk_stop_distance", 0.6) or 0.0))
    # 多层导航(第八十五轮):这个状态走哪一层的网格(默认第 1 层)
    nav_layers = _ai_nav_layers(state)

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
        goal_at = ctx.new_temp("ai_goal")
        diff = ctx.new_temp("ai_diff")
        dist = ctx.new_temp("ai_dist")
        ctx.add_line(indent, f"var {here} := {reader}({self_expr})")
        ctx.add_line(indent, f"var {goal_at} := {reader}({goal})")
        ctx.add_line(indent, f"var {diff} := {goal_at} - {here}")
        ctx.add_line(indent, f"var {dist} := {diff}.length()")
        # 走法:勾了『寻路』就绕开障碍,没勾就是直线(见 _walk_direction)
        walk = _walk_direction(ctx, indent, self_expr, goal_at, diff, nav_layers)
        ctx.add_line(indent, f"if {dist} > {stop}:")
        ctx.add_line(indent + 1, f"{self_expr}.velocity = {walk} * {speed}")
        ctx.add_line(indent, "else:")
        ctx.add_line(indent + 1, f"{self_expr}.velocity = {_vector_zero(ctx)}")
        # 朝向仍看**目标**(不是看绕路的那个方向):追人的怪应该盯着人
        _emit_ai_facing(ctx, indent, self_expr, diff, facing)
        ctx.add_line(indent, f"{self_expr}.move_and_slide()")
        return

    # ---- 攻击:走进攻击距离就停,每『冷却』秒"打一下"(第七十九轮)----
    # "打一下"= 跑该状态的『进入时』出口。插件**不带**血量系统:用户把
    # 『变量_增减』(扣目标血) / 『音效_播放』 / 『信号_发出』接在出口上。
    if behavior == "ATTACK":
        goal = _ai_goal_expr(ctx, state)
        timer = machine.get("attack_timer")
        if goal is None or not timer:
            # 图解析已经报过"没选目标";这里退化,免得生成出指向空物的代码
            _stop_and_slide(ctx, indent, self_expr)
            return
        # 攻击距离留 0 = 用『停在多远』(贴到停距就打是最常见的写法)
        reach = float(getattr(state, "gk_attack_range", 0.0) or 0.0)
        if reach <= 0.0:
            reach = float(getattr(state, "gk_stop_distance", 0.6) or 0.0)
        reach_str = gd_number(reach)
        cooldown = gd_number(float(getattr(state, "gk_cooldown", 1.5) or 0.0))
        here = ctx.new_temp("ai_here")
        goal_at = ctx.new_temp("ai_goal")
        diff = ctx.new_temp("ai_diff")
        dist = ctx.new_temp("ai_dist")
        ctx.add_line(indent, f"var {here} := {reader}({self_expr})")
        ctx.add_line(indent, f"var {goal_at} := {reader}({goal})")
        ctx.add_line(indent, f"var {diff} := {goal_at} - {here}")
        ctx.add_line(indent, f"var {dist} := {diff}.length()")
        # 还没进范围就照常走过去(勾了『寻路』就绕开障碍,见 _walk_direction)
        walk = _walk_direction(ctx, indent, self_expr, goal_at, diff)
        ctx.add_line(indent, f"if {dist} > {reach_str}:")
        ctx.add_line(indent + 1, f"{self_expr}.velocity = {walk} * {speed}")
        # 走出范围就把冷却蓄满 —— 追出去再回来能马上打,不用干等
        ctx.add_line(indent + 1, f"{timer} = 0.0")
        ctx.add_line(indent, "else:")
        ctx.add_line(indent + 1, f"{self_expr}.velocity = {_vector_zero(ctx)}")
        # 冷却没到就只是数秒;到点了打一下,并把冷却重新压满
        ctx.add_line(indent + 1, f"if {timer} > 0.0:")
        ctx.add_line(indent + 2, f"{timer} -= delta")
        ctx.add_line(indent + 1, "else:")
        ctx.add_line(indent + 2, f"{timer} = {cooldown}")
        strike = (machine.get("enter_hooks") or {}).get(state_name)
        if strike:
            ctx.add_line(indent + 2, f"{strike}()")
        # 打的时候仍然看着目标(甩头打不像话)
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
        if not bool(getattr(ctx, "nav_enabled", False)):
            # 没勾『寻路』:老图逐字不变 —— 直接背对威胁直线后退
            ctx.add_line(indent, f"if {dist} <= {stop}:")
            ctx.add_line(indent + 1, f"{self_expr}.velocity = {away}.normalized() * {speed}")
            ctx.add_line(indent, "else:")
            ctx.add_line(indent + 1, f"{self_expr}.velocity = {_vector_zero(ctx)}")
        else:
            # 勾了『寻路』(第八十轮):朝威胁反方向投影一个点交给导航网格 ——
            # 没墙时这个方向和直线后退一模一样;有墙时导航会绕开。
            flee_distance = max(
                0.0, float(getattr(state, "gk_flee_distance", 8.0) or 0.0))
            flee_goal = ctx.new_temp("ai_flee_goal")
            ctx.add_line(
                indent,
                f"var {flee_goal} := {here} + {away}.normalized() "
                f"* {gd_number(flee_distance)}")
            walk = _walk_direction(ctx, indent, self_expr, flee_goal,
                                   f"({flee_goal} - {here})", nav_layers)
            ctx.add_line(indent, f"if {dist} <= {stop}:")
            # 威胁和自己几乎重合时"反方向"不存在,别让 NaN / 零向量抖
            ctx.add_line(indent + 1, f"if {dist} < 0.001:")
            ctx.add_line(indent + 2, f"{self_expr}.velocity = {_vector_zero(ctx)}")
            ctx.add_line(indent + 1, "else:")
            ctx.add_line(indent + 2, f"{self_expr}.velocity = {walk} * {speed}")
            ctx.add_line(indent, "else:")
            ctx.add_line(indent + 1, f"{self_expr}.velocity = {_vector_zero(ctx)}")
        # 『朝目标』= 看着威胁(追兵):face_vec 用「目标 - 自己」= -away
        face = ctx.new_temp("ai_face")
        ctx.add_line(indent, f"var {face} := -{away}")
        _emit_ai_facing(ctx, indent, self_expr, face, facing)
        ctx.add_line(indent, f"{self_expr}.move_and_slide()")
        return

    # ---- 去查看:朝**最近听到的噪音点**走(第四十一轮:听觉)----------
    if behavior == "INVESTIGATE":
        prefix = _hear_prefix(machine["var"])
        _heard_fn, pos_var, at_var, _type_var = _ensure_ai_hear_helper(
            ctx, prefix)
        # 从没听到过声音就别乱跑 —— 不判的话它会一路走向世界原点
        # (Vector 零值),看起来像"AI 发疯了"。
        ctx.add_line(indent, f"if {at_var} < {_NEVER_HEARD} * 0.5:")
        _stop_and_slide(ctx, indent + 1, self_expr)
        ctx.add_line(indent, "else:")
        inner = indent + 1
        here = ctx.new_temp("ai_here")
        diff = ctx.new_temp("ai_diff")
        dist = ctx.new_temp("ai_dist")
        ctx.add_line(inner, f"var {here} := {reader}({self_expr})")
        ctx.add_line(inner, f"var {diff} := {pos_var} - {here}")
        ctx.add_line(inner, f"var {dist} := {diff}.length()")
        now_var = ctx.new_temp("ai_now")
        ctx.add_line(
            inner, f"var {now_var} := float(Time.get_ticks_msec()) * 0.001")
        # 新噪音先关门(done=false):赶路这几秒里『安静下来』不许成立
        _emit_investigate_reset(ctx, inner, machine, at_var, now_var)
        # 噪音点是个**坐标**(不是一个物体),同样能走导航网格
        walk = _walk_direction(ctx, inner, self_expr, pos_var, diff, nav_layers)
        ctx.add_line(inner, f"if {dist} > {stop}:")
        ctx.add_line(inner + 1, f"{self_expr}.velocity = {walk} * {speed}")
        # 赶路时看着噪音点;到点后**环顾的朝向由环顾代码自己管**,
        # 这里再 look_at 会和摆头互相打架(第八十轮)。
        _emit_ai_facing(ctx, inner + 1, self_expr, diff, facing)
        ctx.add_line(inner, "else:")
        _emit_investigate_look(ctx, inner + 1, machine, state, self_expr,
                               now_var)
        ctx.add_line(inner, f"{self_expr}.move_and_slide()")
        return

    # ---- 巡逻 / 返回:去巡逻点(A / B / C / D,或只去 A)----
    point_a = _ai_point(ctx, state, "gk_patrol_a")
    point_b = _ai_point(ctx, state, "gk_patrol_b") if behavior == "PATROL" else None
    if point_a is None:
        _stop_and_slide(ctx, indent, self_expr)
        return
    # ── 第三 / 第四个巡逻点(第七十八轮)────────────────────────────
    # 填了才走"索引 + 方向"那条往返路;只填 A / B 的老图**不走这条**,
    # 还是下面那个翻面布尔 —— 生成物一字不变。
    extra_points = []
    if behavior == "PATROL":
        extra_points = [point for point in (
            _ai_point(ctx, state, "gk_patrol_c"),
            _ai_point(ctx, state, "gk_patrol_d")) if point is not None]
    wp_index = machine.get("wp_index")
    if extra_points and wp_index:
        _emit_patrol_waypoints(
            ctx, indent, self_expr, reader, stop, speed, facing,
            [point for point in (point_a, point_b) if point is not None]
            + extra_points,
            wp_index, machine.get("wp_dir"), nav_layers)
        return
    flip = machine.get("flip")
    here = ctx.new_temp("ai_here")
    goal_at = ctx.new_temp("ai_goal")
    diff = ctx.new_temp("ai_diff")
    ctx.add_line(indent, f"var {here} := {reader}({self_expr})")
    if point_b is not None and flip:
        # 到了哪头就往另一头走(翻面开关存在成员变量里 —— 要跨帧记)
        ctx.add_line(indent,
                     f"var {goal_at} := {reader}({point_a}) if {flip} "
                     f"else {reader}({point_b})")
    else:
        ctx.add_line(indent, f"var {goal_at} := {reader}({point_a})")
    ctx.add_line(indent, f"var {diff} := {goal_at} - {here}")
    # 巡逻同样绕障碍 —— 巡逻路线穿过一堵墙是最常见的"看起来像卡住了"
    walk = _walk_direction(ctx, indent, self_expr, goal_at, diff, nav_layers)
    ctx.add_line(indent, f"if {diff}.length() <= {stop}:")
    if point_b is not None and flip:
        ctx.add_line(indent + 1, f"{flip} = not {flip}")
    # 到点这一帧**刹住**:不清的话会用上一帧的速度多滑一步(0.05 米左右的
    # 抖动 —— 单看不明显,但"到点回头"时会看出一个小顿挫)
    ctx.add_line(indent + 1, f"{self_expr}.velocity = {_vector_zero(ctx)}")
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f"{self_expr}.velocity = {walk} * {speed}")
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

    # ---- 听见动静 / 安静下来(第四十一轮:听觉)------------------------
    # 和"看见 / 丢失"完全对称的一对,只是判断的依据从"和目标的距离"换成
    # "最近有没有听到噪音"(『声音_发出噪音』广播过来的)。
    if kind in node_lib.AI_HEAR_COND_SOURCES:
        prefix = _hear_prefix(machine["var"])
        heard_fn, _pos, _at, _type = _ensure_ai_hear_helper(ctx, prefix)
        ear = gd_number(float(getattr(trans, "gk_ear", 0.0) or 0.0))
        memory = gd_number(float(getattr(trans, "gk_memory", 3.0) or 0.0))
        want_type = gd_string(
            str(getattr(trans, "gk_hear_type", "") or "").strip())
        heard = (f"{heard_fn}({machine['self']}, {ear}, {memory}, "
                 f"{want_type})")
        # 『安静下来』就是"听不见" —— 两个条件互为反面,和 SEE/LOST 一样。
        # 但必须**先听过才能"安静下来"**吗?不用:从没听到过声音时,
        # 『听见动静』为假、『安静下来』为真,正好是"没什么事、该干嘛干嘛"。
        if kind == "HEARD":
            return heard
        # 第八十轮:图里有『去查看』时,到点后的**环顾**没看完不许安静 —
        # 否则它刚在噪音点站定就立刻"安静了 -> 回岗",环顾卡纯属摆设。
        # 环顾变量默认 done=true:从没去查看过的初始帧仍然算安静(见上)。
        inv = machine.get("inv") or {}
        done_var = inv.get("done")
        if done_var:
            return f"not {heard} and {done_var}"
        return f"not {heard}"

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
    # 『AI_听见』要知道该写进哪一份听觉状态(一台机器一份,见 _hear_prefix)
    saved_var = getattr(ctx, "_ai_hear_var", None)
    ctx._ai_hear_var = machine["var"]
    try:
        return ctx.expr_of(socket)
    finally:
        ctx._ai_self_expr = saved
        ctx._ai_hear_var = saved_var


def _emit_ai_state_hooks(ctx, graph, machine):
    """给接了『进入时』/『离开时』的状态各生成一个函数(第七十九轮)。

    返回 ({状态名: 进入函数名}, {状态名: 离开函数名})。

    ── 为什么拆成函数而不是内联(照『动画_状态』的理由)──────────────
    内联的话,每个状态的钩子语句会被抄进**每一处**引用它的地方(起始状态
    初始化 / 每一条转场)。拆成函数之后,"进状态"和"出状态"各只有一个
    定义点,状态一多也不会把 `_physics_process` 撑成一坨。

    ── 『攻击』的『进入时』是特例 ────────────────────────────────────
    它是"每打一下跑一次"(由 `_emit_ai_behavior` 调),所以这里照常生成
    函数;什么时候调由调用方按 `_ai_enter_on_entry` 决定。
    """
    enter_hooks = {}
    exit_hooks = {}
    self_expr = machine["self"]
    var_name = machine["var"]
    for state in graph["states"]:
        name = node_lib.ai_state_name(state)
        if not name:
            continue
        safe = snake_case(name, "state")
        for pin_name, hooks, prefix in (
                ("进入时", enter_hooks, "_gk_ai_enter"),
                ("离开时", exit_hooks, "_gk_ai_exit")):
            if not node_lib.link_targets(state, pin_name):
                continue
            with ctx.capture() as lines:
                ctx._active.clear()
                ctx._current_event = f"AI状态{name}{pin_name}"
                ctx._expr_indent = 1
                ctx._current_indent = 1
                # 钩子里可能用到『AI_看得见』这类要"自己是谁"的表达式 ——
                # 和转场条件同一处理(见 _ai_edge_condition)
                saved_self = getattr(ctx, "_ai_self_expr", None)
                saved_hear = getattr(ctx, "_ai_hear_var", None)
                ctx._ai_self_expr = self_expr
                ctx._ai_hear_var = var_name
                try:
                    _chain(ctx, state, 1, pin_name)
                finally:
                    ctx._ai_self_expr = saved_self
                    ctx._ai_hear_var = saved_hear
                if len(lines) == 0:
                    lines.add(1, "pass")
            func_name = ctx.declare_func_name(f"{prefix}_{safe}")
            ctx.extra_sections.append({
                "signature": f"func {func_name}() -> void:",
                "lines": list(lines.lines),
                "empty_body": "    pass",
                "separator": True,
            })
            hooks[name] = func_name
    return enter_hooks, exit_hooks


def _emit_ai_transitions(ctx, indent, machine):
    """状态内按画布顺序判转场:第一个成立的就切(和动画状态机同一套)。"""
    graph = machine["graph"]
    var = machine["var"]
    enter_hooks = machine.get("enter_hooks") or {}
    exit_hooks = machine.get("exit_hooks") or {}
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
            # ── 『离开时』必须在状态变量被改写**之前**跑 ──────────────
            # 和动画状态机同一条理由:钩子里要是判断"现在在哪个状态",
            # 改完了读到的就是新状态了。
            exit_hook = exit_hooks.get(name)
            if exit_hook:
                ctx.add_line(indent + 2, f"{exit_hook}()")
            ctx.add_line(indent + 2, f"{var} = {gd_string(target)}")
            # 进新状态跑『进入时』—— 攻击状态除外(它的『进入时』是
            # "每打一下",由攻击行为自己调,见 _ai_enter_on_entry)
            if _ai_enter_on_entry(edge["to"]):
                enter_hook = enter_hooks.get(target)
                if enter_hook:
                    ctx.add_line(indent + 2, f"{enter_hook}()")
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
    # ── 对外布尔输出(第六十九轮)────────────────────────────────────
    # 每帧按**当前状态**重算一次:玩法图读到的就是"这一帧它在不在追我"。
    # 放在转场之后 —— 切完状态再算,读到的就是切完的那个状态(和肉眼看到
    # 的一致);放前面会差一帧,表现为"音乐慢半拍"。
    for var, state_name in machine.get("outputs") or ():
        ctx.add_line(indent, f"{var} = {machine['var']} == {gd_string(state_name)}")


@expr_node("GKNodeAiLogic")
def _expr_ai_logic(ctx, node):
    """『AI_逻辑判断』:与 / 或 / 非(和玩法图的『逻辑_布尔运算』同一套)。

    单独登记一个(而不是让 `GKNodeLogic` 也能进 AI 图),是因为两张图的
    **条件来源**不一样:AI 图里 A/B 接的是『AI_看得见』『AI_听见』这类
    带"自己是谁"语义的条件,而玩法图里接的是玩家输入之类。
    """
    from .expressions import _expr_logic
    return _expr_logic(ctx, node)


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

    # ── 对外布尔输出(第六十九轮)────────────────────────────────────
    # 玩法图要靠这些布尔做分支("它在追我 -> 放紧张音乐")。声明成**按名字
    # 共享**的成员变量(不传 key)—— 玩法图的『变量_读取』照同一个名字读。
    # 变量名 / 状态名没填对的情况图解析已经报过问题,这里跳过那一条。
    state_names = {node_lib.ai_state_name(s) for s in graph["states"]}
    outputs = []
    for output in graph.get("outputs") or ():
        var = str(getattr(output, "gk_var", "") or "").strip()
        state_name = str(getattr(output, "gk_state_name", "") or "").strip()
        if not var or state_name not in state_names:
            continue
        outputs.append((ctx.declare_var(var, "bool", initial="false"),
                        state_name))

    # ── 状态的『进入时』/『离开时』钩子(第七十九轮)────────────────
    # 必须在"填起始状态"之前生成 —— 起始状态那一句后面要跟着它的『进入时』。
    enter_hooks, exit_hooks = _emit_ai_state_hooks(
        ctx, graph, {"self": target, "var": var_name})

    # ── 进入起始状态 ────────────────────────────────────────────────
    # 守卫 `.is_empty()`:这个节点接在『每当_物理帧』后面也安全 ——
    # 没有守卫的话每帧都会把状态重置回起点,永远走不出起始状态。
    entry_name = node_lib.ai_state_name(graph["entry"])
    ctx.add_line(indent, f"if {var_name}.is_empty():")
    ctx.add_line(indent + 1, f"{var_name} = {gd_string(entry_name)}")
    # 起始状态也要跑一次它的『进入时』(攻击状态除外,见 _ai_enter_on_entry)
    if _ai_enter_on_entry(graph["entry"]):
        entry_hook = enter_hooks.get(entry_name)
        if entry_hook:
            ctx.add_line(indent + 1, f"{entry_hook}()")

    # ── 巡逻的"当前去哪头"开关(要跨帧记 -> 成员变量)────────────────
    has_patrol = any(str(getattr(s, "gk_behavior", "") or "") == "PATROL"
                     for s in graph["states"])
    flip_var = None
    wp_index = wp_dir = None
    if has_patrol:
        index = len(getattr(ctx, "ai_machines", ()) or ()) + 1
        flip_var = ctx.declare_var(f"_gk_ai_patrol_{index}", "bool",
                                   initial="false")
        # ── 三个点以上的巡逻:还要"当前第几个点 + 往前往后"(第七十八轮)──
        # 只填 A / B 的图**不声明**这两个 —— 老图产物一字不变。
        if any(_patrol_uses_waypoints(s) for s in graph["states"]):
            wp_index = ctx.declare_var(f"_gk_ai_wp_index_{index}", "int",
                                       initial="0")
            wp_dir = ctx.declare_var(f"_gk_ai_wp_dir_{index}", "bool",
                                     initial="true")

    # ── 攻击的冷却计时器(第七十九轮)────────────────────────────────
    # 一个 AI 机器一个就够(同一时刻只有一个状态在跑);0 = 冷却好了。
    # 没有攻击状态的图**不声明** —— 老图产物一字不变。
    attack_timer = None
    if any(str(getattr(s, "gk_behavior", "") or "") == "ATTACK"
           for s in graph["states"]):
        index = len(getattr(ctx, "ai_machines", ()) or ()) + 1
        attack_timer = ctx.declare_var(f"_gk_ai_atk_{index}", "float",
                                       initial="0.0")

    # ── 『去查看』到点后环顾的跨帧状态(第八十轮)────────────────────
    # on   = 正在环顾;done = 看完了(CALM 边拿它当门,见 _ai_edge_condition);
    # start/base = 本轮开始时刻 / 开始时的朝向;noise = 触发本轮的噪音时刻。
    # 没有『去查看』状态的图**一个都不声明** —— 老图产物一字不变。
    # done 默认 true:从没去查看过声音时"安静下来"仍然成立(初始语义不变)。
    inv_vars = None
    if any(str(getattr(s, "gk_behavior", "") or "") == "INVESTIGATE"
           for s in graph["states"]):
        index = len(getattr(ctx, "ai_machines", ()) or ()) + 1
        inv_vars = {
            "on": ctx.declare_var(f"_gk_inv_on_{index}", "bool",
                                  initial="false"),
            "done": ctx.declare_var(f"_gk_inv_done_{index}", "bool",
                                    initial="true"),
            # -1 = 还没到点(到点那一帧才记环顾起点);默认值不能是 0,
            # 否则首帧 now>=0 会让"秒数填 0 立即收工"在赶路时误触发
            "start": ctx.declare_var(f"_gk_inv_start_{index}", "float",
                                     initial="-1.0"),
            "base": ctx.declare_var(f"_gk_inv_base_{index}", "float",
                                    initial="0.0"),
            "noise": ctx.declare_var(f"_gk_inv_noise_{index}", "float",
                                     initial=_NEVER_HEARD),
        }

    # ── 听觉运行时(第四十一轮)────────────────────────────────────
    # 图里用到听觉就先把它生成出来 —— 广播方(machine['self'])得是
    # `gk_ai_hearer` 组的成员、并且有 `_gk_hear_noise` 可以被调到;
    # 而登记进组是**懒登记**(在 `_gk_heard` 里做),所以这里只要保证
    # 那套函数存在即可。
    #
    # ⚠ 必须在 `ctx.ai_machines.append` **之前**:转场/行为是稍后在
    #   `_emit_ai_graph` 里生成的,那时 `_ai_hear_helpers` 要已经能查到。
    if _ai_uses_hearing(graph):
        _ensure_ai_hear_helper(ctx, _hear_prefix(var_name))
        # ── 显式登记进"听觉体"组 ──────────────────────────────────
        # 光靠 `_gk_heard` 里的懒登记**不够**:如果这张图只有『去查看』
        # 行为、没有『听见动静』那个转场,`_gk_heard` 从头到尾不会被调用,
        # AI 就**永远收不到噪音**(表现为"怪物像聋子",而且不报错)。
        # 所以这里在启用时明确登记一次。
        ctx.add_line(indent,
                     f'if not {target}.is_in_group("{HEAR_GROUP}"):')
        ctx.add_line(indent + 1, f'{target}.add_to_group("{HEAR_GROUP}")')

    ctx.ai_machines.append({
        "node": node,
        "graph": graph,
        "var": var_name,
        "self": target,
        "debug": bool(getattr(node, "gk_debug_print", False)),
        "flip": flip_var,
        "wp_index": wp_index,
        "wp_dir": wp_dir,
        "outputs": outputs,
        # 第七十九轮:攻击冷却 + 状态自己的『进入时』/『离开时』钩子
        "attack_timer": attack_timer,
        "enter_hooks": enter_hooks,
        "exit_hooks": exit_hooks,
        # 第八十轮:去查看到点后的环顾状态(没有该状态时为 None)
        "inv": inv_vars,
    })
    ctx.need_ai_process = True

    # ⚠ 这里**不能**自己再 `_chain` 一次(第七十一轮清账):`emit_statement()`
    #   调完处理器**已经**会沿『完成』往下走,再走一遍下游整段就生成两遍。
    #   小游戏示例里表现为『界面_套用设计』的三个控件各建两遍;下游要是
    #   『变量_增减』『音效_播放』这种有副作用的节点,游戏里就是真跑两次。
    #   (同样的坑『等待_秒』踩过,见 statements._stmt_wait 的注释。)


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
            # 真潜行(第一百零九轮):目标躲进 `gk_hide_spot` 组里的躲藏区就判
            # 看不见 —— 哪怕距离够近、正对着。组名和 asset_export 的
            # HIDE_SPOT_GROUP **逐字一致**(不抽常量是因为这里是给 Godot 的
            # 生成文本,和 .tscn 那边是两份代码,靠注释互相钉住)。
            "    for _hide in get_tree().get_nodes_in_group(\"gk_hide_spot\"):",
            "        if _hide is Area2D and target is PhysicsBody2D:",
            "            if (_hide as Area2D).overlaps_body(target as PhysicsBody2D):",
            "                return false",
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
            # 真潜行(第一百零九轮):同上,3D 用 Area3D / PhysicsBody3D。
            "    for _hide in get_tree().get_nodes_in_group(\"gk_hide_spot\"):",
            "        if _hide is Area3D and target is PhysicsBody3D:",
            "            if (_hide as Area3D).overlaps_body(target as PhysicsBody3D):",
            "                return false",
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


# ==========================================================================
# 听觉:『声音_发出噪音』喊一声,听得见的 AI 记下位置
# ==========================================================================
# ── 为什么走"分组广播"而不是自动加载单例 ────────────────────────────
# 出声的和听声的通常**不是同一个物体**(玩家跑步 -> 怪物听见),所以需要
# 一条跨物体的通道。三种做法里:
#   * 自动加载单例(autoload):得改 project.godot,还要保证导出顺序;
#   * 信号总线节点:得让用户在场景里摆一个,忘了就没声音;
#   * **分组广播**(选这个):声音去 `get_tree().get_nodes_in_group(
#     "gk_ai_hearer")` 里挨个调方法。零配置、跨场景都有效、出问题一眼能查
#     (在 Godot 里看那个怪物在不在这个组里就知道)。
# 代价是"每帧遍历一组"—— 但一组通常只有几个敌人,而且发声本来就有间隔。
HEAR_GROUP = "gk_ai_hearer"

# 没听到过任何声音时的时间戳(用一个很负的数当哨兵,比 null 好判)
_NEVER_HEARD = "-1000000000.0"


def _hear_prefix(var_name):
    """听觉变量名的前缀 —— 按"哪个状态机"分开。

    ── 为什么要分(踩过的坑)────────────────────────────────────────
    一个脚本里可能有两台『AI_启用』(比如巡逻兵 + 狗)。如果听觉状态共用
    一份成员变量,两台机器就会互相覆盖 —— 狗听到的声音会把巡逻兵也引过去。
    所以按下发它们的『状态变量名』各存一份。
    """
    return f"{snake_case(str(var_name or 'ai_state'), 'ai_state')}_hear"


def _dim_vector(ctx):
    return ("Vector2", "Vector2.ZERO") if ctx.dim == 2 else ("Vector3",
                                                            "Vector3.ZERO")


def _ensure_ai_hear_helper(ctx, prefix):
    """确保听觉运行时已生成,返回 (查询函数名, 位置变量名, 时间变量名)。

    生成的东西(都挂在**脚本所在节点**上):
      * `{prefix}_pos` / `{prefix}_loud` / `{prefix}_at` / `{prefix}_type`
        —— 记住了什么声音(位置 / 响度 / 时刻 / 类型,第八十轮);
      * `_gk_hear_noise(at, loudness, noise_type)` —— 『声音_发出噪音』广播时
        调它;名字**固定**,因为广播方不知道听方的前缀是什么 —— 它只按
        `gk_ai_hearer` 组挨个调,所以这个函数的签名必须统一(见下面"派发器");
      * `_gk_heard_{prefix}(host, ear, memory, want_type)` —— "听得见吗",
        顺带把自己登记进 `gk_ai_hearer` 组(**懒登记**:不依赖 _ready 的执行
        顺序);`want_type` 非空时只认真同类型的噪音(第八十轮)。
    """
    from .expressions import ensure_position_helper
    reader = ensure_position_helper(ctx)

    made = getattr(ctx, "_ai_hear_helpers", None)
    if made is None:
        made = {}
        ctx._ai_hear_helpers = made
    if prefix in made:
        return made[prefix]

    vector, zero = _dim_vector(ctx)
    pos_var = ctx.declare_var(f"{prefix}_pos", vector, initial=zero)
    loud_var = ctx.declare_var(f"{prefix}_loud", "float", initial="0.0")
    at_var = ctx.declare_var(f"{prefix}_at", "float", initial=_NEVER_HEARD)
    type_var = ctx.declare_var(f"{prefix}_type", "String", initial='""')
    heard_fn = f"_gk_heard_{prefix}"

    ctx.extra_sections.append({
        "signature": f"func {heard_fn}(host: Node, ear: float, "
                     f"memory: float, want_type: String) -> bool:",
        "lines": [
            # ── 懒登记(而不是写在 _ready 里)─────────────────────────
            # 谁用听觉谁自己加进组。写在 _ready 里的话得知道"哪些脚本要听",
            # 而『AI_听见』可能出现在玩法图、也可能只在 AI 图里 —— 各处去补
            # _ready 迟早漏一处(漏了就是"敌人像聋子",而且**不报错**)。
            f'    if not is_in_group("{HEAR_GROUP}"):',
            f'        add_to_group("{HEAR_GROUP}")',
            f"    if {at_var} < {_NEVER_HEARD} * 0.5:",
            "        return false      # 从来没听到过任何声音",
            # ── 按类型挑声音(第八十轮)──────────────────────────────
            # 听方留空 = 什么都听(老图语义);填了就只认真同类型的噪音 —
            # "玻璃碎了去查窗、脚步声不惊动"靠这一行。
            '    if want_type != "":',
            f"        if {type_var} != want_type:",
            "            return false      # 最后那个声音不是我要听的类型",
            "    if memory > 0.0:",
            f"        var age := float(Time.get_ticks_msec()) * 0.001 - {at_var}",
            "        if age > memory:",
            "            return false",
            f"    var d := ({reader}(host) - {pos_var}).length()",
            f"    if d > {loud_var}:",
            "        return false      # 太远了:这个声音传不到这儿",
            "    if ear > 0.0 and d > ear:",
            "        return false      # 太远了:这只耳朵听不到",
            "    return true",
        ],
        "empty_body": "    return false",
        "separator": True,
    })
    made[prefix] = (heard_fn, pos_var, at_var, type_var)
    _rebuild_hear_dispatcher(ctx)
    return made[prefix]


def _rebuild_hear_dispatcher(ctx):
    """重建 `_gk_hear_noise` —— 它要把噪音写进**本脚本所有**听觉前缀。

    ── 为什么要有"派发器"这一层(名字必须固定)──────────────────────
    『声音_发出噪音』是**跨物体**广播的:它不知道对面脚本里用的是哪个前缀,
    只知道自己在一堆 `gk_ai_hearer` 组成员里挨个调。所以函数名必须统一
    (`_gk_hear_noise`),而"具体写进哪几个变量"由各脚本自己决定。

    一个脚本里可能有多台 AI(前缀不同),所以这个函数要**汇总所有前缀** ——
    每发现一个新前缀就重写一遍它的函数体(直接改 extra_sections 里那个
    dict,生成器最后才读它)。
    """
    made = getattr(ctx, "_ai_hear_helpers", None) or {}
    names = sorted(made)
    if not names:
        return
    vector, _zero = _dim_vector(ctx)
    lines = [f"    var now := float(Time.get_ticks_msec()) * 0.001"]
    for prefix in names:
        _heard_fn, pos_var, at_var, type_var = made[prefix]
        loud_var = ctx.declare_var(f"{prefix}_loud", "float", initial="0.0")
        lines.append(f"    {pos_var} = at")
        lines.append(f"    {loud_var} = loudness")
        lines.append(f"    {at_var} = now")
        # 噪音类型(第八十轮):发声卡贴的标签,听方按它挑声音
        lines.append(f"    {type_var} = noise_type")
    section = getattr(ctx, "_ai_hear_dispatcher", None)
    if section is None:
        section = {
            "signature": f"func _gk_hear_noise(at: {vector}, "
                         f"loudness: float, noise_type: String) -> void:",
            "lines": lines,
            "empty_body": "    pass",
            "separator": True,
        }
        ctx._ai_hear_dispatcher = section
        ctx.extra_sections.append(section)
    else:
        section["lines"] = lines


def _ai_uses_hearing(graph):
    """这张 AI 图用不用听觉(用来决定要不要生成那套运行时)。

    两条路都算:有状态用『去查看』、有转场用『听见动静 / 安静下来』。
    (条件引脚上接了『AI_听见』的情况,表达式生成时自己会 ensure ——
     那时"前缀"才知道,提前扫不出来。)
    """
    for state in graph.get("states") or ():
        if str(getattr(state, "gk_behavior", "") or "") == "INVESTIGATE":
            return True
    for edge in graph.get("edges") or ():
        if edge.get("kind") in node_lib.AI_HEAR_COND_SOURCES:
            return True
    return False


@stmt_node("GKNodeNoise")
def _stmt_noise(ctx, node, indent):
    """『声音_发出噪音』:按间隔把"谁在哪弄出多大声"广播给听觉体。

    ── 间隔为什么必须存在(不是可选优化)────────────────────────────
    脚步声的写法是"接在『每当_物理帧』后面",也就是每秒 60 次。没有间隔
    的话 —— 每帧都广播一次,听觉体的"最后一次听到"永远是最新的一帧,
    『安静下来』**永远不会成立**,敌人会一直黏在噪音点。所以这里用成员
    变量记时间戳,默认 0.5 秒发一次。
    """
    from .expressions import ensure_position_helper
    reader = ensure_position_helper(ctx)

    loudness = ctx.input_number(node, "响度", "8.0")
    interval = gd_number(float(getattr(node, "gk_interval", 0.5) or 0.0))
    target = ctx.target_expr(node, "gk_target", hint="物体")
    # 噪音类型(第八十轮):留空 = 普通噪音,听方什么都听
    noise_type = gd_string(
        str(getattr(node, "gk_noise_type", "") or "").strip())
    debug = bool(getattr(node, "gk_debug_print", False))

    # 时间戳要**跨帧**记 -> 成员变量。每个『声音_发出噪音』各占一个
    # (用节点名加后缀),两个节点各发各的、互不影响间隔。
    stamp = ctx.declare_var(f"_gk_noise_stamp_{snake_case(node.name, 'n')}",
                            "float", initial=_NEVER_HEARD)
    now = ctx.new_temp("gk_now")
    at = ctx.new_temp("gk_noise_at")
    hearer = ctx.new_temp("gk_hearer")

    ctx.add_line(indent, f"var {now} := float(Time.get_ticks_msec()) * 0.001")
    ctx.add_line(indent, f"if {now} - {stamp} >= {interval}:")
    ctx.add_line(indent + 1, f"{stamp} = {now}")
    ctx.add_line(indent + 1, f"var {at} := {reader}({target})")
    ctx.add_line(indent + 1, f'var {hearer}s := '
                             f'get_tree().get_nodes_in_group("{HEAR_GROUP}")')
    if debug:
        ctx.add_line(indent + 1,
                     f'print("[GK 噪音] 位置=", {at}, " 响度=", {loudness},'
                     f' " 类型=", {noise_type},'
                     f' " 听觉体=", {hearer}s.size())')
    ctx.add_line(indent + 1, f"for {hearer} in {hearer}s:")
    # 听不听得到由**听方**自己判断(它有自己的耳朵距离、记忆时长和类型
    # 过滤)—— 广播方只管把"哪、多大声、什么类型"送到。
    ctx.add_line(indent + 2,
                 f'if {hearer}.has_method("_gk_hear_noise"):')
    ctx.add_line(indent + 3,
                 f"{hearer}._gk_hear_noise({at}, {loudness}, {noise_type})")
    # ⚠ 同上:不许自己再走一遍下游(见 _stmt_ai_enable 的说明)。


@expr_node("GKNodeAiHear")
def _expr_ai_hear(ctx, node):
    """『AI_听见』:距离 + 记忆 + 类型过滤 -> 布尔。"""
    prefix = _hear_prefix(getattr(ctx, "_ai_hear_var", "ai_state"))
    heard_fn, _pos, _at, _type = _ensure_ai_hear_helper(ctx, prefix)
    self_expr = getattr(ctx, "_ai_self_expr", None) or "self"
    ear = ctx.input_number(node, "听觉距离", "0.0")
    memory = ctx.input_number(node, "记忆秒数", "3.0")
    want_type = gd_string(
        str(getattr(node, "gk_hear_type", "") or "").strip())
    return f"{heard_fn}({self_expr}, {ear}, {memory}, {want_type})"