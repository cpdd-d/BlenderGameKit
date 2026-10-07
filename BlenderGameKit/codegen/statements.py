"""非动画的语句发射器(运动/属性/射线/定时/容器/流程控制)。

每个函数往 `_Statements` 里追加若干行 GDScript,由 `@stmt_node` 登记。"""

from ..nodes.node_base import (TARGET_SELF, gd_node_path, gd_string,
                               safe_function_name, snake_case)
from ..nodes import nodes as node_lib

from . import easing
from .literals import _gd_type_of, _vector_ctor, gd_literal, gd_number, gd_vector
from .registry import _chain, _next_nodes, multi_output, stmt_node




@stmt_node("GKNodeSetPosition")
def _stmt_set_position(ctx, node, indent):
    """直接摆到某个位置(传送 / 回出生点)。"""
    target = ctx.target_expr(node, "gk_target", hint="节点")
    x = ctx.input_number(node, "X", "0.0")
    y = ctx.input_number(node, "Y", "0.0")
    z = ctx.input_number(node, "Z", "0.0")
    relative = bool(getattr(node, "gk_relative", False))
    if ctx.dim == 2:
        point = f"Vector2({x}, {y})"
    else:
        point = f"Vector3({x}, {y}, {z})"
    if relative:
        ctx.add_line(indent, f"{target}.global_position += {point}")
    else:
        ctx.add_line(indent, f"{target}.global_position = {point}")


# ==========================================================================
# 语句节点处理器
# ==========================================================================





@stmt_node("GKNodeMouseMode")
def _stmt_mouse_mode(ctx, node, indent):
    """鼠标模式:第一人称锁鼠标 / 菜单放出来。"""
    mode = {
        "VISIBLE": "MOUSE_MODE_VISIBLE",
        "HIDDEN": "MOUSE_MODE_HIDDEN",
        "CAPTURED": "MOUSE_MODE_CAPTURED",
        "CONFINED": "MOUSE_MODE_CONFINED",
    }.get(str(getattr(node, "gk_mode", "CAPTURED")), "MOUSE_MODE_CAPTURED")
    ctx.add_line(indent, f"Input.mouse_mode = Input.{mode}")


def _tunable_number(ctx, node, socket_name, fallback, base_name,
                    clamp_max=None, clamp_hint=""):
    """未连线的数值输入 -> 生成 @export 参数，可在 Godot 检查器里直接调。

    连线了就说明这个值由图算出来，不该暴露成外部参数。

    clamp_max: 超过这个值就夹住，并给一条告警。
    用于"单位在不同维度下含义不同"的参数 —— 见 _stmt_move_direction。
    """
    if ctx.export_tunables:
        socket = ctx._socket(node, socket_name)
        if socket is None or not getattr(socket, "is_linked", False):
            # -- 同一个节点的同一个输入只声明一次(踩过的坑)------------
            # 多输出表达式节点(移动状态)按输出引脚求值,一个节点会被
            # 调用 8 次,每次都声明一遍 @export ->
            #     @export var move_threshold: float = 0.1
            #     @export var run_threshold: float = 3.0
            #     @export var move_threshold_2: float = 0.1   <- 重复
            #     @export var run_threshold_2: float = 3.0    <- 重复
            # 用户在检查器里改第一个,实际生效的可能是后面那个。
            cache = getattr(ctx, "_tunable_cache", None)
            if cache is None:
                cache = {}
                ctx._tunable_cache = cache
            key = (node_lib.node_key(node), socket_name)
            if key in cache:
                return cache[key]
            literal = ctx.input_number(node, socket_name, fallback)
            if clamp_max is not None:
                try:
                    value = float(literal)
                except (TypeError, ValueError):
                    value = None
                if value is not None and value > clamp_max:
                    label = getattr(node, "label", "") or node.bl_label
                    ctx.warn(
                        f"『{label}』的『{socket_name}』是 {value:g},"
                        f"已按 {clamp_max:g} 生成。{clamp_hint}")
                    literal = f"{clamp_max:g}"
            label = getattr(node, "label", "") or node.bl_label
            result = ctx.declare_export(base_name, "float", literal,
                                        f"{label} · {socket_name}")
            cache[key] = result
            return result
    return ctx.input_number(node, socket_name, fallback)


def _tunable_vector(ctx, node, socket_name, dim, base_name):
    """未连线的向量输入 -> 生成 @export 参数。"""
    if ctx.export_tunables:
        socket = ctx._socket(node, socket_name)
        if socket is None or not getattr(socket, "is_linked", False):
            literal = ctx.input_vector(node, socket_name, dim=dim)
            label = getattr(node, "label", "") or node.bl_label
            return ctx.declare_export(base_name, _vector_ctor(dim), literal,
                                      f"{label} · {socket_name}")
    return ctx.input_vector(node, socket_name, dim=dim)


@stmt_node("GKNodePrint")
def _stmt_print(ctx, node, indent):
    text = ctx.input_string(node, "内容", "")
    ctx.add_line(indent, f"print({text})")
    # 开着**游戏内调试面板**(第二十二轮)时,同一句话也进面板的日志缓冲。
    # 控制台那一行照打 —— 老行为一个字不变,面板只是多一个去处。
    if bool(ctx.options.get("debug_panel", False)):
        ctx.need_debug_helper = True
        ctx.add_line(indent, f"_gk_debug_log_append({text})")


@stmt_node("GKNodeSetVelocity")
def _stmt_set_velocity(ctx, node, indent):
    target = ctx.target_expr(node)
    velocity = _tunable_vector(ctx, node, "速度", ctx.dim, "start_velocity")
    ctx.add_line(indent, f"{target}.velocity = {velocity}")


@stmt_node("GKNodeMoveAndSlide")
def _stmt_move_and_slide(ctx, node, indent):
    ctx.add_line(indent, f"{ctx.target_expr(node)}.move_and_slide()")


def _move_reference(ctx, node):
    """『移动_按方向』的参考系:引脚优先,否则勾选『随相机朝向』用当前相机。

    只在 3D 下有"绕着角色转的视角"这个概念 —— 2D 直接返回 None(不改方向,
    老图/2D 示例生成结果一字不变)。返回 None = 不做参考系变换。
    """
    if ctx.dim != 3:
        return None
    socket = ctx._socket(node, "参考系")
    if socket is not None and getattr(socket, "is_linked", False):
        return ctx.expr_of(socket)
    if bool(getattr(node, "gk_follow_camera", False)):
        ctx.need_resolve_camera = True
        return "_gk_resolve_camera(self)"
    return None


def _rotate_by_reference(ctx, node, indent, direction, reference):
    """把输入方向按参考节点的**水平朝向**变换成世界方向。

    参考系的"前方" F = -basis.z、"右方" R = basis.x,各自压到水平面(y=0)
    再归一化;世界方向 = R * 输入.左右(x) + F * 输入.前后(z)。
    默认世界轴时 R=(1,0,0)、F=(0,0,-1),结果与老逻辑完全一致。

    参考系不是 Node3D、或朝向正好竖直(F/R 水平投影为 0)时,原样保留输入方向,
    不会把角色推成零速度。
    """
    ref_var = ctx.new_temp("ref")
    world_var = ctx.new_temp("move_dir")
    f_var = ctx.new_temp("ref_f")
    r_var = ctx.new_temp("ref_r")
    ctx.add_line(indent, f"var {world_var} := {direction}")
    ctx.add_line(indent, f"var {ref_var} = {reference}")
    ctx.add_line(indent, f"if {ref_var} is Node3D:")
    ctx.add_line(indent + 1, f"var {f_var} := "
                             f"-({ref_var} as Node3D).global_transform.basis.z")
    ctx.add_line(indent + 1, f"{f_var}.y = 0.0")
    ctx.add_line(indent + 1, f"var {r_var} := "
                             f"({ref_var} as Node3D).global_transform.basis.x")
    ctx.add_line(indent + 1, f"{r_var}.y = 0.0")
    ctx.add_line(indent + 1, f"if {f_var}.length_squared() > 0.0001:")
    ctx.add_line(indent + 2, f"{f_var} = {f_var}.normalized()")
    ctx.add_line(indent + 2, f"{world_var} = {f_var} * ({direction}).z")
    ctx.add_line(indent + 1, f"if {r_var}.length_squared() > 0.0001:")
    ctx.add_line(indent + 2, f"{r_var} = {r_var}.normalized()")
    ctx.add_line(indent + 2, f"{world_var} += {r_var} * ({direction}).x")
    return world_var


def _god_camera_on(ctx):
    """这个图是不是开着『上帝摄像机』(关卡测试用;见 generator 里的守卫)。

    上帝机切出来之后,WASD 是**飞来着看这一关**的。让角色照样跟着 WASD
    走,看画面就是"飞相机时角色也在跑" —— 用户原话:"上帝视角还会影响
    角色移动"。所以这类"玩家输入直接驱动"的语句要加守卫。
    """
    return bool(getattr(ctx, "god_camera_enabled", False))


def _guarded_line(ctx, indent, line):
    """带守卫地写一行语句:上帝视角期间不执行它。

    用**单行 if** 而不是把整段包进 if 块 —— 踩过的坑:『方向』引脚取输入
    的那几行是 `ctx.input_vector` 按固定缩进写出来的,包 if 的话它们不会
    跟着缩进,Godot 直接报
         Parse Error: Expected indented block after "if" block
    单行形式没有"块"的问题。
    """
    if _god_camera_on(ctx):
        ctx.add_line(indent, f"if not _gk_god_camera_active(): {line}")
    else:
        ctx.add_line(indent, line)


@stmt_node("GKNodeMoveDirection")
def _stmt_move_direction(ctx, node, indent):
    target = ctx.target_expr(node)
    # 方向先归一化:否则"手柄斜推"会比"直推"快 1.41 倍(独立游戏常见 bug)
    direction = ctx.input_vector(node, "方向")
    # ── 默认速度必须按维度给 ────────────────────────────────────────
    # 节点属性上写的 300 是给 **2D** 的(像素/秒),在 2D 里是正常手感。
    # 但 3D 的世界单位是**米**:300 米/秒 = 一帧跑 5 米 ——
    # 实测:角色 1.25 秒冲出 125 米,画面里瞬间消失,
    # 用户看到的是"按键完全没反应"(其实动得飞快,早出画面了)。
    # 所以 3D 下超过 50 就夹到 6 米/秒(正常走路速度),并告警说明。
    if ctx.dim == 2:
        default_speed = "300.0"
        speed = _tunable_number(ctx, node, "速度", default_speed, "move_speed")
    else:
        default_speed = "6.0"
        speed = _tunable_number(
            ctx, node, "速度", default_speed, "move_speed",
            clamp_max=50.0,
            clamp_hint=("『速度』在 2D 里单位是像素/秒,在 3D 里是**米/秒**,"
                        "同一个数值在 3D 里会快几百倍(角色一眨眼就飞出画面)。"
                        "3D 一般用 3~10。"))
    temp = ctx.new_temp("dir")
    ctx.add_line(indent, f"var {temp} := ({direction}).normalized()")
    # 上帝视角期间把方向抹成零 —— 那时候 WASD 是飞相机的(见 _god_camera_on)
    if _god_camera_on(ctx):
        ctx.add_line(indent, "if _gk_god_camera_active():")
        zero = "Vector3.ZERO" if ctx.dim == 3 else "Vector2.ZERO"
        ctx.add_line(indent + 1, f"{temp} = {zero}")

    # ── 第三人称:方向跟着相机 / 参考系转(用户 2026-10-04)──────────
    # 3D 下给了『参考系』(引脚优先)或勾了『随相机朝向』时,把输入方向按
    # 参考节点的**水平朝向**变换成世界方向 —— 转视角后"前"始终是镜头前方,
    # 方向感不会错位。没给参考系时走下面原来的轴映射,老图结果一字不变。
    world_dir = None
    if ctx.dim == 3:
        reference = _move_reference(ctx, node)
        if reference is not None:
            world_dir = _rotate_by_reference(ctx, node, indent, temp, reference)

    # ── 输入往哪个世界轴走(用户要求:像 UPBGE 那样能设)──────────────
    # 默认:横移走 X,前进走 Z 的**负向**(Godot / glTF 的"前"就是 -Z)。
    # 实测踩过:相机朝向一变,"按前往后、按左往右"完全可能 ——
    # 这不该逼用户去改脚本,给两个选项翻过来就行。
    # 2D 只有 X 轴,这两项直接忽略。
    if ctx.dim == 2:
        axis_h, axis_v = "x", None
        horiz_expr, vert_expr = f"{temp}.x", f"{temp}.y"
    else:
        axis_h = str(getattr(node, "gk_axis_h", "X") or "X").lower()
        axis_v = str(getattr(node, "gk_axis_v", "Z") or "Z").lower()
        negate = str(getattr(node, "gk_forward_sign", "NEG")) == "NEG"
        horiz_expr = f"{temp}.{axis_h}"
        vert_expr = (f"(-{temp}.{axis_v})" if negate else f"{temp}.{axis_v}")

    if world_dir is not None:
        # 参考系变换后已经是**世界方向**(y 已压平),不用再按轴映射。
        if getattr(node, "gk_keep_y", False):
            ctx.add_line(indent, f"{target}.velocity += {target}.get_gravity() * "
                                 f"get_physics_process_delta_time()")
            ctx.add_line(indent, f"{target}.velocity.x = {world_dir}.x * {speed}")
            ctx.add_line(indent, f"{target}.velocity.z = {world_dir}.z * {speed}")
        else:
            ctx.add_line(indent, f"{target}.velocity = {world_dir} * {speed}")
    elif getattr(node, "gk_keep_y", False):
        # ── 有重力时必须做两件事 ────────────────────────────────────
        # 1) Godot 4 的 CharacterBody2D/3D **不会自动施加重力** ——
        #    它只是"按你设的 velocity 移动"。不自己累加重力，
        #    角色就永远浮在原地(实测:velocity 恒为 (0,0)、
        #    is_on_floor() 恒为 false，画面看起来就像"没反应")。
        # 2) 所以只能写水平分量，纵向留给重力累加。
        #    `velocity = dir * speed` 会把 velocity.y 一起清成 0，
        #    重力刚加上去就被抹掉。
        ctx.add_line(indent, f"{target}.velocity += {target}.get_gravity() * "
                             f"get_physics_process_delta_time()")
        if axis_v is None:
            ctx.add_line(indent, f"{target}.velocity.x = {horiz_expr} * {speed}")
        else:
            # 3D:按用户选的轴写。**Y 轴永远留给重力** ——
            # 用户真把某个轴设成 Y 时也不动它,否则 gravity 刚加上就被抹掉
            # (那正是"重力模式"要解决的问题)。
            written = set()
            for axis, expr in ((axis_h, horiz_expr), (axis_v, vert_expr)):
                if axis == "y":
                    continue
                op = "=" if axis not in written else "+="
                ctx.add_line(indent,
                             f"{target}.velocity.{axis} {op} {expr} * {speed}")
                written.add(axis)
            if not written:
                # 两个轴都选了 Y —— 退回默认的 X,至少能动
                ctx.add_line(indent, f"{target}.velocity.x = {horiz_expr} * {speed}")
    else:
        ctx.add_line(indent, f"{target}.velocity = {temp} * {speed}")
    ctx.add_line(indent, f"{target}.move_and_slide()")
    # 『推开刚体』(用户 2026-10-05 第二十八轮)—— 必须放在 move_and_slide 之后,
    # 那时候才有 get_slide_collision() 可查。
    if bool(getattr(node, "gk_push_bodies", False)):
        _push_rigid_bodies(ctx, node, target, indent)
    # 『上台阶』(用户 2026-10-05 第二十九轮)—— 也在 move_and_slide 之后:
    # 正因为这一帧"滑不过去"(撞在台阶前脸),才轮到抬腿。
    step_height = float(getattr(node, "gk_step_height", 0.0) or 0.0)
    if step_height > 0.0:
        _step_up(ctx, node, target, indent, step_height)


def _step_up(ctx, node, target, indent, height):
    """『上台阶』:自动迈上矮台阶(Godot 的运动学角色不会自己上)。

    ── 为什么必须自己写(实测:0.35 米的台阶就是三堵墙)────────────
    CharacterBody 是**运动学体**:`move_and_slide()` 撞到台阶前脸只会
    "贴着滑走" —— 玩家看到的就是"上不去、卡住"。Godot 官方给的做法
    就是这段"三关探测",三关都过了才 `global_position` 平移过去
    (不是"用力挤" —— 挤出来的姿势会穿模/抖):

      ① 前面确实被**竖着的东西**挡住,而且走的这一小步被挡住了
         (台阶前脸 / 墙)。坡面不算 —— 法线的竖直分量 ≥ 0.5 当它是
         地面/斜坡,不然上坡会一蹦一蹦(实测过);
      ② 抬到『上台阶高度』再往前,这条路是通的 —— 台阶比它高就会
         撞上,原地不动 = 仍然挡住;
      ③ 抬起来之后**还得走得动**(在这一高度再往前探一步不撞东西)。
         这条是实测补上的:胶囊碰撞体的下端是**尖的**,抬起来后那个尖
         能从高台阶前脸旁边擦过去 —— 少了这一关,角色会顶着高墙
         原地一蹦一蹦(探针里 max_y 多出 0.5);
      ④ 落点下面有地,至少比现在高 5 厘米,而且落的是**平面**
         (法线朝上;顶角蹭上来的侧向接触不算)。
    """
    up = "Vector2.UP" if ctx.dim == 2 else "Vector3.UP"
    down = "Vector2.DOWN" if ctx.dim == 2 else "Vector3.DOWN"
    collision = "KinematicCollision2D" if ctx.dim == 2 else "KinematicCollision3D"
    if ctx.dim == 2:
        flat_expr = f"Vector2({target}.velocity.x, 0.0)"
    else:
        flat_expr = f"Vector3({target}.velocity.x, 0.0, {target}.velocity.z)"
    height_text = gd_number(height)
    # 往下探的距离 = 台阶高度 − 5 厘米:探得到 -> 落点比现在高 ≥ 5 厘米;
    # 探不到 -> 落回同一层(顶着高墙原地蹦)或者前面是悬崖 —— 都不迈。
    down_text = gd_number(max(height - 0.05, 0.05))
    flat = ctx.new_temp("step_flat")
    forward = ctx.new_temp("step_fwd")
    probe = ctx.new_temp("step_col")
    lift = ctx.new_temp("step_lift")
    ahead = ctx.new_temp("step_ahead")
    land = ctx.new_temp("step_land")
    ctx.add_line(indent, f"if {target}.is_on_floor() and "
                         f"{target}.get_slide_collision_count() > 0:")
    ctx.add_line(indent + 1, f"var {flat} := {flat_expr}")
    ctx.add_line(indent + 1, f"if {flat}.length_squared() > 0.0001:")
    # 这一小步往前迈 0.35 米(半个身位):够把脚踩到台阶面上,
    # 又不至于一步跨过窄台面。
    ctx.add_line(indent + 2, f"var {forward} := {flat}.normalized() * 0.35")
    ctx.add_line(indent + 2, f"var {probe} := {collision}.new()")
    ctx.add_line(indent + 2, f"if {target}.test_move({target}.global_transform, "
                             f"{forward}, {probe}) and "
                             f"absf({probe}.get_normal().y) < 0.5:")
    ctx.add_line(indent + 3,
                 f"var {lift} := {up} * {height_text} + {forward}")
    ctx.add_line(indent + 3,
                 f"if not {target}.test_move({target}.global_transform, {lift}):")
    ctx.add_line(indent + 4,
                 f"var {ahead} := {target}.global_transform.translated({lift})")
    ctx.add_line(indent + 4, f"if not {target}.test_move({ahead}, {forward}):")
    ctx.add_line(indent + 5, f"var {land} := {collision}.new()")
    ctx.add_line(indent + 5,
                 f"if {target}.test_move({ahead}, {down} * {down_text}, {land}) "
                 f"and {land}.get_normal().y >= 0.5:")
    ctx.add_line(indent + 6, f"{target}.global_position += {lift}")


# ==========================================================================
# 载具(用户 2026-10-05 第三十轮:可驾驶载具)
# ==========================================================================
#
# ── 状态为什么放在载具**自己身上**(而不是成员变量)──────────────────
# 上/下车/驾驶是三个节点,却必须共享同一份"这辆车有没有人在开"。
# 生成成员变量的话,两辆车的节点会抢同一个名字;按名字区分又怕用户
# 复制节点。所以状态挂在载具节点自己身上(`set_meta` / `get_meta`)——
# 同一辆车的三个节点自动是同一份,场景里摆两辆车也互不串。


def _vehicle_target(ctx, node, label="载具"):
    """载具引用:必须是场景里**另一个**物体(没选 / 自身 -> 告警并跳过)。"""
    raw = (getattr(node, "gk_vehicle", "") or "").strip()
    if not raw or raw == TARGET_SELF:
        ctx.warn(f"『{label}』的『载具』还没选 —— 点右边的放大镜从场景里"
                 f"挑一个物体(载具要做成 CharacterBody)。这一句跳过")
        return None
    body_type = "CharacterBody2D" if ctx.dim == 2 else "CharacterBody3D"
    return ctx.target_expr(node, "gk_vehicle", hint="载具", node_type=body_type)


def _vehicle_rider(ctx, node, label="载具"):
    """坐车的那个人:默认就是脚本挂载的角色本身。"""
    scene_type = "Node2D" if ctx.dim == 2 else "Node3D"
    return ctx.target_expr(node, "gk_target", hint="角色", node_type=scene_type)


@stmt_node("GKNodeEnterVehicle")
def _stmt_enter_vehicle(ctx, node, indent):
    """『载具_上车』:把人放进车里(状态记到载具自己身上)。

    ── 为什么还要"把角色摆到座位上"────────────────────────────────
    角色节点是**镜头、距离判定、触发区**认的那个位置。让角色每帧坐在
    座位上(『载具_驾驶』负责),这些系统就自动跟着车走 —— 不用另外
    接一套"跟随载具的相机"。
    """
    vehicle = _vehicle_target(ctx, node, "载具_上车")
    if vehicle is None:
        return
    who = _vehicle_rider(ctx, node, "载具_上车")
    seat = gd_vector(tuple(getattr(node, "gk_seat", (0.0, 1.2, 0.0)) or ()),
                     dim=ctx.dim)
    lines = [f'{vehicle}.set_meta("gk_driving", true)',
             f'{vehicle}.set_meta("gk_seat", {seat})']
    if bool(getattr(node, "gk_hide", True)):
        lines.append(f"{who}.visible = false")
    if bool(getattr(node, "gk_disable_collision", True)):
        ctx.need_collision_helper = True
        lines.append(f"_gk_set_collision({who}, false)")
    lines.append(f"{who}.global_position = {vehicle}.global_transform * {seat}")
    distance = float(getattr(node, "gk_max_distance", 0.0) or 0.0)
    if distance > 0.0:
        # 离得够近才上得去 —— 免得在地图另一头按一下键就瞬移上车。
        from .expressions import ensure_position_helper
        reader = ensure_position_helper(ctx)
        ctx.add_line(indent, f"if ({reader}({who}) - {reader}({vehicle})).length() "
                             f"<= {gd_number(distance)}:")
        for line in lines:
            ctx.add_line(indent + 1, line)
    else:
        for line in lines:
            ctx.add_line(indent, line)


@stmt_node("GKNodeExitVehicle")
def _stmt_exit_vehicle(ctx, node, indent):
    """『载具_下车』:放人下车(位置 = 载具本地空间的一个点)。"""
    vehicle = _vehicle_target(ctx, node, "载具_下车")
    if vehicle is None:
        return
    who = _vehicle_rider(ctx, node, "载具_下车")
    exit_point = gd_vector(tuple(getattr(node, "gk_exit", (2.0, 0.5, 0.0)) or ()),
                           dim=ctx.dim)
    ctx.add_line(indent, f'{vehicle}.set_meta("gk_driving", false)')
    ctx.add_line(indent, f"{who}.global_position = "
                         f"{vehicle}.global_transform * {exit_point}")
    ctx.add_line(indent, f"{who}.visible = true")
    ctx.need_collision_helper = True
    ctx.add_line(indent, f"_gk_set_collision({who}, true)")


@stmt_node("GKNodeDriveVehicle")
def _stmt_drive_vehicle(ctx, node, indent):
    """『载具_驾驶』:有人开时每帧开一次车,并把角色钉在座位上。

    ── 车感(不是"和角色一样的平移")──────────────────────────────
    W/S = 沿**车头**方向前进 / 后退,A/D = 转车头 —— 所以按 S 是倒车,
    按 A/D 是打方向盘,和走路那套"平移"区分开。
    ── 为什么每帧要钉座位 ─────────────────────────────────────────
    角色自己那套『移动_按方向』还在每帧跑(没人在开车时它才是主角),
    它会按 WASD 把角色挪走 —— 钉座位这一句排在它**后面**,把角色按回
    座位(节点的『完成』要接在『移动_按方向』后面,节点上也写了)。
    """
    vehicle = _vehicle_target(ctx, node, "载具_驾驶")
    if vehicle is None:
        return
    who = _vehicle_rider(ctx, node, "载具_驾驶")
    speed = gd_number(float(getattr(node, "gk_speed", 10.0) or 0.0))
    turn = gd_number(float(getattr(node, "gk_turn_speed", 120.0) or 0.0))
    direction = ctx.input_vector(node, "方向")
    temp = ctx.new_temp("drive")
    fwd = ctx.new_temp("drive_fwd")
    seat_type = "Vector2" if ctx.dim == 2 else "Vector3"
    # 上帝视角期间不驾驶(用户 2026-10-06 实测:"上车后按 Tab 进上帝视角,
    # 还能控制车辆移动")—— 那时 WASD 是**飞相机**的。和角色移动同一个守卫:
    # 没开上帝摄像机的项目不生成这一截(不会引用不存在的函数)。
    if _god_camera_on(ctx):
        ctx.add_line(indent, f'if {vehicle}.get_meta("gk_driving", false) '
                             f'and not _gk_god_camera_active():')
    else:
        ctx.add_line(indent, f'if {vehicle}.get_meta("gk_driving", false):')
    ctx.add_line(indent + 1, f"var {temp} := ({direction}).normalized()")
    # 重力先累加,再把水平分量**盖掉** —— 和角色那套同一个套路,不然
    # move_and_slide() 会把刚加上的重力当"撞墙"清零。
    ctx.add_line(indent + 1, f"{vehicle}.velocity += {vehicle}.get_gravity() * "
                             f"get_physics_process_delta_time()")
    if ctx.dim == 2:
        ctx.add_line(indent + 1, f"{vehicle}.velocity.x = {temp}.x * {speed}")
    else:
        # ⚠ `rotation.y` 是**弧度**,而『转向速度』填的是**度/秒** ——
        # 直接减会把 120°/秒 当成 120 弧度/秒(实测:车每帧转 1.4 弧度,
        # 肉眼就是疯狂打转)。探针抓到的。
        ctx.add_line(indent + 1, f"{vehicle}.rotation.y -= deg_to_rad({temp}.x * "
                                 f"{turn} * get_physics_process_delta_time())")
        ctx.add_line(indent + 1, f"var {fwd} := -{vehicle}.global_transform.basis.z")
        ctx.add_line(indent + 1, f"{fwd}.y = 0.0")
        ctx.add_line(indent + 1, f"if {fwd}.length_squared() > 0.0001:")
        ctx.add_line(indent + 2, f"{fwd} = {fwd}.normalized()")
        ctx.add_line(indent + 1, f"{vehicle}.velocity.x = {fwd}.x * {temp}.z * {speed}")
        ctx.add_line(indent + 1, f"{vehicle}.velocity.z = {fwd}.z * {temp}.z * {speed}")
    ctx.add_line(indent + 1, f"{vehicle}.move_and_slide()")
    # 『推开刚体』(用户 2026-10-06 实测:"上车后推不动箱子")—— 和角色那套
    # 同一个坑、同一段代码:运动学体撞刚体只会滑走,得自己施冲量。
    # 必须放在 move_and_slide() 之后(那之后才有 get_slide_collision 可查)。
    if bool(getattr(node, "gk_push_bodies", False)):
        _push_rigid_bodies(
            ctx, node, vehicle, indent + 1,
            character_pin=(who if bool(getattr(node, "gk_push_characters", True))
                           else None))
    ctx.add_line(indent + 1,
                 f"{who}.global_position = {vehicle}.global_transform * "
                 f'({vehicle}.get_meta("gk_seat", {seat_type}.ZERO) as {seat_type})')


@multi_output("GKNodeDriveVehicle")
def _expr_drive_outputs(ctx, node):
    """『载具_驾驶』的『驾驶中』输出:读的是**载具自己身上**那份状态。

    必须是**自包含表达式**(和『移动_朝向目标』的『到达』同一个坑):
    输出可以在图里任何地方被读,语句里那几个临时变量不在作用域里。
    """
    vehicle = _vehicle_target(ctx, node, "载具_驾驶")
    if vehicle is None:
        return {"驾驶中": "false"}
    return {"驾驶中": f'{vehicle}.get_meta("gk_driving", false)'}


def _push_rigid_bodies(ctx, node, target, indent, character_pin=None):
    """把撞到的刚体推一把(CharacterBody 自己**不会**推刚体)。

    ── 为什么必须自己写(实测:推箱子推不动)───────────────────────
    Godot 的 CharacterBody 是**运动学体**:`move_and_slide()` 撞到刚体时
    只会"贴着它滑走",不给它任何力 —— 看起来就是"箱子焊在地上"。
    官方做法就是在碰撞循环里自己施加冲量。

    ── 冲量怎么算 ──────────────────────────────────────────────────
    每物理帧对每个撞到的刚体施 `推力 × 帧时长 × 质量` 的冲量
    (= 持续施加『推力』牛顿)。乘质量是为了让轻重箱子**手感一致**
    (重箱子需要更大的力才动)。方向取碰撞法线的水平反向:
    角色往哪推、箱子就往哪走;Y 分量丢掉,免得把箱子往地里/天上按。

    ── `character_pin`:顺带"顶开角色"(第四十六轮)──────────────────
    用户:"角色上车后与 AI 角色碰撞会弹开 AI";确认的期望是
    **车能把角色顶开**。实机量到的真相是:两边都是运动学体,
    `move_and_slide` 互相只会阻挡 —— 撞上去是"双方都顶停",
    AI 一分钱都推不走(上面那段冲量只对刚体生效,永远不会触发)。
    所以这里给角色体补一段**每帧限速的位移**:车开过去能把人顶着走,
    但每帧最多 0.15 米(≈9 m/s),**绝不会弹飞**。
    `character_pin` = 车上那个人(驾驶员),要跳过 —— 他正被钉在座位上。
    """
    force = gd_number(float(getattr(node, "gk_push_force", 30.0) or 0.0))
    body_type = "RigidBody2D" if ctx.dim == 2 else "RigidBody3D"
    index = ctx.new_temp("push")
    hit = ctx.new_temp("hit")
    rigid = ctx.new_temp("rigid")
    ctx.add_line(indent, f"for {index} in {target}.get_slide_collision_count():")
    ctx.add_line(indent + 1, f"var {hit} := {target}.get_slide_collision({index})")
    ctx.add_line(indent + 1, f"var {rigid} := {hit}.get_collider()")
    ctx.add_line(indent + 1, f"if {rigid} is {body_type}:")
    if ctx.dim == 2:
        direction = f"-{hit}.get_normal()"
        impulse = (f"{direction} * {force} * get_physics_process_delta_time() "
                   f"* ({rigid} as {body_type}).mass")
    else:
        direction = (f"Vector3(-{hit}.get_normal().x, 0.0, "
                     f"-{hit}.get_normal().z)")
        impulse = (f"{direction} * {force} * get_physics_process_delta_time() "
                   f"* ({rigid} as {body_type}).mass")
    ctx.add_line(indent + 2,
                 f"({rigid} as {body_type}).apply_central_impulse({impulse})")
    if character_pin:
        char_type = "CharacterBody2D" if ctx.dim == 2 else "CharacterBody3D"
        shove = ctx.new_temp("shove")
        ctx.add_line(indent + 1,
                     f"elif {rigid} is {char_type} and {rigid} != {character_pin}:")
        if ctx.dim == 2:
            ctx.add_line(indent + 2, f"var {shove} := -{hit}.get_normal()")
        else:
            ctx.add_line(indent + 2,
                         f"var {shove} := Vector3(-{hit}.get_normal().x, 0.0, "
                         f"-{hit}.get_normal().z)")
        cap = gd_number(float(getattr(node, "gk_character_speed", 6.0) or 6.0))
        ctx.add_line(indent + 2,
                     f"({rigid} as {char_type}).global_position += {shove} * "
                     f"minf({cap} * get_physics_process_delta_time(), 0.15)")


@stmt_node("GKNodeMoveToward")
def _stmt_move_toward(ctx, node, indent):
    """朝目标走过去(追人的敌人 / 跟随的宠物 / 自动靠近的 NPC)。

    ── 和『移动_按方向』的关系 ────────────────────────────────────
    那两个都是"角色体那套"(velocity + move_and_slide),所以这里的
    『谁走』用 **CharacterBody 类型**去取 @onready —— 取成 Node 的话
    `.velocity` / `.move_and_slide()` 会被 Godot 静态检查拒绝(整个脚本
    加载不了)。速度的单位坑也一样(2D 像素/秒 vs 3D 米/秒)。
    """
    body_type = "CharacterBody2D" if ctx.dim == 2 else "CharacterBody3D"
    target = ctx.target_expr(node, "gk_target", hint="节点", node_type=body_type)
    goal = _move_toward_goal(ctx, node)
    if goal is None:
        ctx.warn("『移动_朝向目标』不知道往哪走 —— 在『目标』里选一个物体,"
                 "或者把『目标』引脚接到一个节点引用上。这一句跳过")
        return

    if ctx.dim == 2:
        speed = _tunable_number(ctx, node, "速度", "300.0", "chase_speed")
    else:
        speed = _tunable_number(
            ctx, node, "速度", "6.0", "chase_speed",
            clamp_max=50.0,
            clamp_hint=("『速度』在 2D 里单位是像素/秒,在 3D 里是**米/秒**"
                        "(追人的敌人一般 2~6)。"))

    stop = gd_number(float(getattr(node, "gk_stop_distance", 0.0) or 0.0))
    turn = bool(getattr(node, "gk_turn", False))
    accel = float(getattr(node, "gk_accel", 0.0) or 0.0)
    vector = "Vector2" if ctx.dim == 2 else "Vector3"
    # ── 读位置复用『取值_位置』那个 helper(函数内导入)──────────────
    # 为什么不在文件顶端导入:`statements` <-> `expressions` 通过 `camera`
    # 绕成一圈(camera 顶部 `from .statements import _tunable_number`),
    # 模块级导入会在 camera 只加载了一半时报
    #     cannot import name '_camera_node_expr' from partially initialized module
    from .expressions import ensure_position_helper
    reader = ensure_position_helper(ctx)

    here = ctx.new_temp("here")
    diff = ctx.new_temp("to_goal")
    distance = ctx.new_temp("goal_dist")
    ctx.add_line(indent, f"var {here} := {reader}({target})")
    ctx.add_line(indent, f"var {diff} := {reader}({goal}) - {here}")
    ctx.add_line(indent, f"var {distance} := {diff}.length()")
    if accel > 0.0:
        # ── 平滑加减速(用户 2026-10-05 第二十六轮)──────────────────
        # 直接写 `velocity = 方向 * 速度` 是**瞬起瞬停** —— 追人的敌人
        # 看起来像"啪一下开始跑、啪一下定住"。move_toward 把"这一帧最多
        # 变多少速度"交给『加速』(米/秒²),停下来时也照样慢慢刹住。
        wanted = ctx.new_temp("wanted")
        ctx.add_line(indent, f"var {wanted} := {vector}.ZERO")
        ctx.add_line(indent, f"if {distance} > {stop}:")
        ctx.add_line(indent + 1, f"{wanted} = {diff}.normalized() * {speed}")
        ctx.add_line(indent, f"{target}.velocity = {target}.velocity.move_toward("
                             f"{wanted}, {gd_number(accel)} "
                             f"* get_physics_process_delta_time())")
    else:
        ctx.add_line(indent, f"if {distance} <= {stop}:")
        ctx.add_line(indent + 1, f"{target}.velocity = {vector}.ZERO")
        ctx.add_line(indent, "else:")
        ctx.add_line(indent + 1, f"{target}.velocity = {diff}.normalized() * {speed}")
    if turn and ctx.dim == 2:
        # 2D 没有"面朝"这个概念,靠 rotation 转 —— 和『移动_设置朝向』一致。
        # 放在 if/else **外面**:停下来的时候也照样看着目标
        ctx.add_line(indent, f"{target}.rotation = atan2({diff}.y, {diff}.x)")
    if turn and ctx.dim == 3:
        # ── 只按**水平方向**转(踩过的坑)──────────────────────────
        # 目标在头顶正上方时,look_at 的方向和 up 向量重合,Godot 会报
        #     Up vector and direction between node origin and target
        #     are aligned
        # 所以先压平 Y,再判"水平方向是不是接近零"(停下来的时候也要看着目标)。
        ctx.add_line(indent, f"if absf({diff}.x) + absf({diff}.z) > 0.001:")
        ctx.add_line(indent + 1, f"{target}.look_at({here} + {vector}("
                                 f"{diff}.x, 0.0, {diff}.z), Vector3.UP)")
    ctx.add_line(indent, f"{target}.move_and_slide()")


def _move_toward_goal(ctx, node):
    """『移动_朝向目标』往哪走:引脚优先(可以是运行时实例),否则读节点上的『目标』。

    语句和『到达』输出两边都用这一份 —— 两处各写一遍的话,以后改
    "引脚优先"规则必然只改到一处,输出和实际移动会对不上。
    没目标返回 None(由调用方决定是告警还是给个 false)。
    """
    socket = ctx._socket(node, "目标")
    if socket is not None and getattr(socket, "is_linked", False):
        return ctx.expr_of(socket)
    raw = str(getattr(node, "gk_goal", "") or "").strip()
    if not raw:
        return None
    # ── 2D 的目标引用必须限成 Node2D(第三十六轮,真 Godot 实测)─────────
    # glb 里有一份**和物体同名**的网格节点(MeshInstance3D),导出后
    # 它在树里的顺序往往**排在碰撞代理前面**。宽松的 "Node" 类型会让
    # _gk_ref_typed 命中那个网格 —— 3D 里没事(网格也是 Node3D,位置
    # 就是物体位置),但 2D 里 MeshInstance3D **不是** Node2D,位置读取
    # 掉进"返回 (0,0)"的兜底:追踪者被引到原点、一头顶在障碍上
    # (实测:追了两秒卡死、永远到不了)。
    return ctx.target_expr(node, "gk_goal", hint="节点",
                           node_type="Node2D" if ctx.dim == 2 else "")


@multi_output("GKNodeMoveToward")
def _expr_move_toward_outputs(ctx, node):
    """『移动_朝向目标』的『到达』输出:离目标进了『停在多远』的范围没有。

    ⚠ 必须是**自包含表达式**(踩过的坑):输出可以在图里任何地方被读,
    读的时候语句里那几个临时变量(`_here` / `_to_goal` / `_goal_dist`)
    根本不在作用域里。所以这里用位置 helper **重新算一遍**,算法和语句
    里那句 `distance <= stop` 逐字一致(两处不同步的话,输出说"到了"、
    移动却还在走)。
    """
    goal = _move_toward_goal(ctx, node)
    if goal is None:
        # 语句那边已经报过"不知道往哪走",这里不重复刷告警
        return {"到达": "false"}
    body_type = "CharacterBody2D" if ctx.dim == 2 else "CharacterBody3D"
    target = ctx.target_expr(node, "gk_target", hint="节点", node_type=body_type)
    stop = gd_number(float(getattr(node, "gk_stop_distance", 0.0) or 0.0))
    from .expressions import ensure_position_helper
    reader = ensure_position_helper(ctx)
    return {"到达": f"(({reader}({goal}) - {reader}({target})).length() <= {stop})"}


# ==========================================================================
# 寻路(第三十四轮:用户选的"敌人会绕墙追你")
# ==========================================================================
@stmt_node("GKNodeNavigate")
def _stmt_navigate(ctx, node, indent):
    """『移动_寻路』:朝目标走,方向由**导航网格**算(不是直线)。

    ── 为什么直接跟服务器的路径,不用 NavigationAgent 的路径 ──────────
    实测(真 Godot,物理测试场第 ⑩ 工位):服务器的
    `map_get_path` 给出 10 个点的绕墙路径(从墙端拐过去),
    但 `NavigationAgent3D.get_next_path_position()` **一直返回它自己的位置** ——
    代理的内部路径状态和"贴网格的点/父节点原点的高度差"纠缠在一起,
    调试代价高。自管路径没有这层内部状态:**每帧拿一条路径,盯住第 1 个点**
    (`_path[0]` 就是自己站的那个点)。代价是每帧一次路径查询,
    对"几个追人的敌人"这个量级完全够,而且行为完全可预测。
    (NavigationAgent 只用来做**避障**:它那套 RVO 整流没法手写,
    见下面的 _gk_nav_move。)

    ── 三层降级(用户视角:少开一个开关不该让游戏坏掉)────────────────
      1. 场景没勾『寻路』(没有导航网格)-> 直线走(和『移动_朝向目标』一样);
      2. 路径只有 0/1 个点(网格还没烤完 / 目标不可达)-> 这一帧直线走。
    ⚠ 第二条**不刷告警**(它是每帧都可能发生的运行时状态);
      第一条刷告警 —— 那是"设置漏了",用户该知道。

    ── 2D / 3D(第三十六轮)────────────────────────────────────────
    两个维度走同一套:2D 用 `NavigationServer2D.map_get_path` +
    `get_world_2d()`,3D 用 3D 版;只有"把路点高度压平"(3D 专有)
    和"转向写法"不同。
    """
    body_type = "CharacterBody2D" if ctx.dim == 2 else "CharacterBody3D"
    target = ctx.target_expr(node, "gk_target", hint="节点", node_type=body_type)
    goal = _move_toward_goal(ctx, node)
    if goal is None:
        ctx.warn("『移动_寻路』不知道往哪走 —— 在『目标』里选一个物体,"
                 "或者把『目标』引脚接到一个节点引用上。这一句跳过")
        return

    if ctx.dim == 2:
        server = "NavigationServer2D"
        map_expr = f"{target}.get_world_2d().navigation_map"
    else:
        server = "NavigationServer3D"
        map_expr = f"{target}.get_world_3d().navigation_map"

    nav_ok = bool(getattr(ctx, "nav_enabled", False))
    if not nav_ok:
        ctx.warn("『移动_寻路』要用导航网格绕路,但这一关的场景设置里"
                 "**没勾『寻路』** —— 它现在退化成直线走(和"
                 "『移动_朝向目标』一样)。想让它绕墙:侧栏『导出脚本』"
                 "里勾上『寻路』,再重新导出")

    if ctx.dim == 2:
        speed = _tunable_number(ctx, node, "速度", "300.0", "nav_speed")
    else:
        speed = _tunable_number(
            ctx, node, "速度", "6.0", "nav_speed", clamp_max=50.0,
            clamp_hint=("『速度』在 3D 里是**米/秒**(追人的敌人一般 2~6);"
                        "2D 里是像素/秒。"))
    stop = gd_number(float(getattr(node, "gk_stop_distance", 0.0) or 0.0))
    turn = bool(getattr(node, "gk_turn", False))
    accel = float(getattr(node, "gk_accel", 0.0) or 0.0)
    # 避障(第三十六轮):期望速度交给 NavigationAgent,回调拿安全速度。
    avoid = bool(getattr(node, "gk_avoid", False))
    avoid_radius = gd_number(max(0.01, float(
        getattr(node, "gk_avoid_radius", 0.5) or 0.5)))
    vector = "Vector2" if ctx.dim == 2 else "Vector3"

    from .expressions import ensure_position_helper
    reader = ensure_position_helper(ctx)

    here = ctx.new_temp("here")
    goal_at = ctx.new_temp("nav_goal")
    diff = ctx.new_temp("to_goal")
    distance = ctx.new_temp("goal_dist")
    ctx.add_line(indent, f"var {here} := {reader}({target})")
    ctx.add_line(indent, f"var {goal_at} := {reader}({goal})")
    ctx.add_line(indent, f"var {diff} := {goal_at} - {here}")
    ctx.add_line(indent, f"var {distance} := {diff}.length()")

    if nav_ok:
        path = ctx.new_temp("nav_path")
        step = ctx.new_temp("nav_step")
        ctx.add_line(indent, f"var {path} := {server}.map_get_path("
                             f"{map_expr}, "
                             f"{here}, {goal_at}, true)")
        ctx.add_line(indent, f"var {step} := {diff}")
        # ── 盯住路径的**第 1 个点**(第 0 个点就是自己站的那个)──────────
        # 只有 0/1 个点 = 网格还没烤完 / 目标不可达 -> 保持直线。
        ctx.add_line(indent, f"if {path}.size() > 1:")
        ctx.add_line(indent + 1, f"{step} = {path}[1] - {here}")
        if ctx.dim == 3:
            # 路点高度要压平(网格贴在碰撞体上表面,不压平会上下拱);
            # 2D 没有"高度"这回事,不压。
            ctx.add_line(indent, f"{step}.y = 0.0")
        # 点几乎在脚下(路径只有起点)-> 这一帧直线走,别站着不动
        ctx.add_line(indent, f"if {step}.length() < 0.05:")
        ctx.add_line(indent + 1, f"{step} = {diff}")
        direction = step
    else:
        direction = diff

    if avoid:
        # ── 避障(第三十六轮)─────────────────────────────────────────
        # 把"期望速度"交给 NavigationAgent:它和附近别的 agent 一起算
        # RVO,得出的**安全速度**在 velocity_computed 回调里赋值给角色
        # 并 move_and_slide(回调只接一次,见 _gk_nav_agent)。
        # 这里自己不再 move —— 交给回调(否则一帧里走两遍)。
        wanted = ctx.new_temp("wanted")
        ctx.add_line(indent, f"var {wanted} := {vector}.ZERO")
        ctx.add_line(indent, f"if {distance} > {stop}:")
        ctx.add_line(indent + 1,
                     f"{wanted} = {direction}.normalized() * {speed}")
        if ctx.dim == 2:
            ctx.need_nav_avoid_2d = True
            helper = "_gk_nav_move2d"
        else:
            ctx.need_nav_avoid = True
            helper = "_gk_nav_move"
        if accel > 0.0:
            # 平滑:期望速度朝目标速度靠(拿到的是避障后的实际速度,
            # 正好带一点"被邻居挤住时减下来"的自然)。
            ctx.add_line(indent, f"{target}.velocity = {target}.velocity.move_toward("
                                 f"{wanted}, {gd_number(accel)} "
                                 f"* get_physics_process_delta_time())")
            wanted_expr = f"{target}.velocity"
        else:
            wanted_expr = wanted
        ctx.add_line(indent, f"{helper}({target}, {wanted_expr}, {goal_at}, "
                             f"{avoid_radius}, {speed})")
    elif accel > 0.0:
        wanted = ctx.new_temp("wanted")
        ctx.add_line(indent, f"var {wanted} := {vector}.ZERO")
        ctx.add_line(indent, f"if {distance} > {stop}:")
        ctx.add_line(indent + 1, f"{wanted} = {direction}.normalized() * {speed}")
        ctx.add_line(indent, f"{target}.velocity = {target}.velocity.move_toward("
                             f"{wanted}, {gd_number(accel)} "
                             f"* get_physics_process_delta_time())")
    else:
        ctx.add_line(indent, f"if {distance} <= {stop}:")
        ctx.add_line(indent + 1, f"{target}.velocity = {vector}.ZERO")
        ctx.add_line(indent, "else:")
        ctx.add_line(indent + 1, f"{target}.velocity = {direction}.normalized() * {speed}")
    if turn and ctx.dim == 2:
        ctx.add_line(indent, f"{target}.rotation = atan2({direction}.y, {direction}.x)")
    if turn and ctx.dim == 3:
        # 朝向**前进方向**(绕墙时头跟着路转);同样要压平 + 防"方向对不上
        # up 向量"那个 look_at 报错(和『移动_朝向目标』同一个坑)。
        ctx.add_line(indent, f"if absf({direction}.x) + absf({direction}.z) > 0.001:")
        ctx.add_line(indent + 1, f"{target}.look_at({here} + {vector}("
                                 f"{direction}.x, 0.0, {direction}.z), Vector3.UP)")
    if not avoid:
        # 避障时移动在回调里做(见 _gk_nav_move),这里不重复走。
        ctx.add_line(indent, f"{target}.move_and_slide()")


@stmt_node("GKNodeRebakeNavigation")
def _stmt_rebake_navigation(ctx, node, indent):
    """『移动_重烤导航』:门 / 平台挪了之后重烤一次导航网格。

    真正的活(3D 按静态碰撞体烤 / 2D 算外框再烤)在**环境脚本**里 ——
    生成脚本这边只把一个转发函数带进公共前缀(按需注入,
    见 generator 的 _emit_nav_helpers)。
    """
    ctx.need_nav_rebake = True
    ctx.add_line(indent, "_gk_rebake_navigation()")


@multi_output("GKNodeNavigate")
def _expr_navigate_outputs(ctx, node):
    """『移动_寻路』的『到达』输出 —— 和『移动_朝向目标』同一套算法。

    两个节点的『到达』都是"离目标进了『停在多远』"(和走的是不是直线无关):
    绕路绕到目标旁边也算到达。两处各写一遍的话,以后改口径必然漏一个。
    """
    return _expr_move_toward_outputs(ctx, node)


@stmt_node("GKNodeJump")
def _stmt_jump(ctx, node, indent):
    """起跳:竖直速度给一个冲量。

    ── 2D 和 3D 的"上"是**反的**(2.5D 示例真跑抓出来的坑)──────────
    2D 里屏幕的上方是 **-Y**,3D 里世界的上方是 **+Y**。
    老代码一律写 `velocity.y = -强度` —— 3D 项目里这是**把角色往地里按**,
    而 `move_and_slide()` 会把它当"撞墙"直接把速度清零:
    表现就是"按了跳跃键完全没反应"(实测:插桩看到起跳那一行执行了、
    速度设成了 -6,下一帧就被地面挡回 0,角色一动不动)。

    2D 的写法不动(老图/2D 示例生成结果一个字符都不变),
    3D 走 `+强度`。
    """
    target = ctx.target_expr(node)
    force = _tunable_number(ctx, node, "强度", "400.0", "jump_force")
    if ctx.dim == 2:
        _guarded_line(ctx, indent, f"{target}.velocity.y = -{force}")
    else:
        _guarded_line(ctx, indent, f"{target}.velocity.y = {force}")


@stmt_node("GKNodeAddImpulse")
def _stmt_add_impulse(ctx, node, indent):
    target = ctx.target_expr(node)
    impulse = ctx.input_vector(node, "冲量")
    ctx.add_line(indent, f"{target}.apply_impulse({impulse})")


@stmt_node("GKNodeSetRotation")
def _stmt_set_rotation(ctx, node, indent):
    target = ctx.target_expr(node)
    angle = ctx.input_number(node, "角度", "0.0")
    axis = getattr(node, "gk_axis", "Y")
    degrees = bool(getattr(node, "gk_degrees", True))
    if ctx.dim == 2:
        ctx.add_line(indent, f"{target}.rotation = deg_to_rad({angle})")
    else:
        prop = "rotation_degrees" if degrees else "rotation"
        value = angle if degrees else f"deg_to_rad({angle})"
        ctx.add_line(indent, f"{target}.{prop}.{axis.lower()} = {value}")


@stmt_node("GKNodeFlip")
def _stmt_flip(ctx, node, indent):
    target = ctx.target_expr(node)
    socket = ctx._socket(node, "朝左")
    if socket is not None and getattr(socket, "is_linked", False):
        left = ctx.expr_of(socket)
    else:
        mode = getattr(node, "gk_mode", "AUTO")
        if mode == "LEFT":
            left = "true"
        elif mode == "RIGHT":
            left = "false"
        else:
            # 自动:按当前朝速度方向翻转(横版二次元角色最常用的行为)
            left = f"{target}.velocity.x < 0.0"
    ctx.add_line(indent, f"if {left}:")
    ctx.add_line(indent + 1, f"{target}.scale.x = -abs({target}.scale.x)")
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f"{target}.scale.x = abs({target}.scale.x)")


@stmt_node("GKNodeTweenProperty")
def _stmt_tween_property(ctx, node, indent):
    """把某个节点的某个属性在时间里推过去 —— 时间轴的一条轨道。

    ── 用 Godot 的 Tween,而不是自己写每帧插值 ─────────────────────
    `create_tween().tween_property(node, "fov", 30.0, 0.8)` 一句话搞定,
    而且 Tween 是**异步**的:调用完立刻返回,曲线在后台自己跑。
    这意味着"几条缓动同时进行"是**天然的**,不需要线程/协程
    —— 用户要的"不同动画的整体联动"就是这么拼出来的。
    """
    target = ctx.target_expr(node, "gk_target")
    prop = (getattr(node, "gk_property", "") or "").strip()
    if not prop:
        ctx.warn(f"『{node.bl_label}』没填『属性』,这一句被跳过了"
                 f"(要动相机视野就填 fov,动位置填 position 或 position:y)")
        return
    socket = ctx._socket(node, "到")
    if socket is not None and getattr(socket, "is_linked", False):
        to_expr = ctx.expr_of(socket)
    else:
        to_expr = _tunable_number(ctx, node, "到", "0.0", "tween_to")
    duration = _tunable_number(ctx, node, "时长", "0.5", "tween_time")
    trans = getattr(node, "gk_trans", "TRANS_SINE") or "TRANS_SINE"
    ease = getattr(node, "gk_ease", "EASE_IN_OUT") or "EASE_IN_OUT"

    tween_var = ctx.new_temp("tween")
    ctx.add_line(indent, f"var {tween_var} = create_tween()")
    ctx.add_line(indent, f"{tween_var}.set_trans(Tween.{trans})")
    ctx.add_line(indent, f"{tween_var}.set_ease(Tween.{ease})")
    ctx.add_line(indent, f"if {target} != null:")
    ctx.add_line(indent + 1,
                 f"{tween_var}.tween_property({target}, "
                 f"{gd_string(prop)}, {to_expr}, {duration})")
    if getattr(node, "gk_snap", True):
        # 浮点插值结束时可能差 0.0001 —— 做"门完全关上""视野正好 30"这类
        # 判定时会差一点点,所以在下一句补一个精确赋值。
        # `set_index` 让这句**等前面那条缓动跑完**再执行(Tween 的链式用法)。
        ctx.add_line(indent, f"{tween_var}.set_parallel(false)")
        ctx.add_line(indent, f"{tween_var}.chain().tween_callback("
                             f"func(): {target}.set({gd_string(prop)}, "
                             f"{to_expr}))")


# ==========================================================================
# 时间轴(照虚幻蓝图的 Timeline)
# ==========================================================================
@stmt_node("GKNodeTimeline")
def _stmt_timeline(ctx, node, indent):
    """一条时间轴:『执行』开始,每帧从『更新』口走,走完从『已结束』口走。

    ── 为什么要做成"一个节点 + 两个执行口"(照虚幻)────────────────
    用户 2026-10-04 第十二轮:"动画的时间轴好像还没设计完成,虚幻蓝图的
    设计逻辑还不错,但这里可以实现吗?"

    虚幻的 Timeline 就是这套接口:Play / Update / Finished + 轨道 + Alpha。
    它好用的地方在于:**进度**是节点替你管的(不用自己写"计时器 + 每帧
    加 delta + 判断到头"),你只管在 Update 里用当前值布置画面。

    ── 生成成什么 ────────────────────────────────────────────────
    状态全是成员变量(每节点一份,见 ctx.node_tag);每帧的推进挂在
    `_process` 钩子里(动画/镜头是**画面**的事,用渲染帧才不抖);
    『更新』/『已结束』后面的用户逻辑各自生成成一个函数,由钩子调用
    —— 和『定时_每隔几秒』的『到点了』同一个做法。
    """
    tag = ctx.node_tag(node, "timeline")
    prefix = f"_gk_tl{tag}"
    duration_value = float(getattr(node, "gk_time", 1.0) or 1.0)
    if duration_value <= 0.001:
        ctx.warn("『定时_时间轴』的时长是 0 —— 已按 0.001 秒处理"
                 "(想让它立刻走完就直接不接这个节点)")
        duration_value = 0.001
    duration = gd_number(duration_value)
    mode = str(getattr(node, "gk_mode", "ONCE") or "ONCE")
    # 重复触发时怎么办(用户 2026-10-06:"想让它先放完再重复")
    repeat = str(getattr(node, "gk_repeat", "RESTART") or "RESTART")
    if repeat == "QUEUE" and mode != "ONCE":
        ctx.note(f"『{node.bl_label}』的『重复触发时』选了『等播完再重播』,"
                 f"但播放模式是循环/往返 —— 那两种没有'播完'那一刻,"
                 f"已按『播的时候忽略』处理(想排队就用『一次』)")
        repeat = "IGNORE"

    time_var = ctx.declare_var(f"{prefix}_time", "float")
    dir_var = ctx.declare_var(f"{prefix}_dir", "float", initial="1.0")
    play_var = ctx.declare_var(f"{prefix}_play", "bool", initial="false")
    done_var = ctx.declare_var(f"{prefix}_done", "bool", initial="false")
    alpha_var = ctx.declare_var(f"{prefix}_alpha", "float")
    path_var = ctx.declare_var(f"{prefix}_path", "Vector3")
    # 排队的"再来一遍"请求(只有『重复触发时 = 等播完再重播』才需要)——
    # 别的模式不声明它:老图产物必须一个字符都不变(有测试钉着)。
    pending_var = (ctx.declare_var(f"{prefix}_pending", "bool", initial="false")
                   if repeat == "QUEUE" else "")

    # ---- 轨道:从 / 到 + (可选)自己的曲线 ----
    count = max(0, min(4, int(getattr(node, "gk_track_count", 2) or 0)))
    tracks = []
    for index in range(1, count + 1):
        start_value = float(getattr(node, f"gk_track{index}_from", 0.0) or 0.0)
        end_value = float(getattr(node, f"gk_track{index}_to", 0.0) or 0.0)
        curve_name = str(
            getattr(node, f"gk_track{index}_curve", "") or "").strip()
        curve_expr = (easing.curve_expr(ctx, curve_name, "Curve")
                      if curve_name else "null")
        tracks.append((ctx.declare_var(f"{prefix}_tr{index}", "float"),
                       gd_number(start_value), gd_number(end_value),
                       curve_expr))

    # ---- 时间轴自己的进度曲线(管 Alpha) ----
    raw_var = f"_gk_tl{tag}_raw"
    alpha_curve_name = str(getattr(node, "gk_curve", "") or "").strip()
    alpha_curve_expr = (easing.curve_expr(ctx, alpha_curve_name, "Curve")
                        if alpha_curve_name else "null")
    alpha_shape = (f"_gk_ease({raw_var}, 0, 0, {alpha_curve_expr})"
                   if alpha_curve_expr != "null" else raw_var)

    # ---- 曲线轨道(3D 曲线 -> 输出曲线上的位置) ----
    path_curve_name = str(getattr(node, "gk_path_curve", "") or "").strip()
    fly_var = ctx.declare_var(f"{prefix}_fly", "Curve3D")
    fly_len = ctx.declare_var(f"{prefix}_fly_len", "float")
    fly_expr = (easing.curve_expr(ctx, path_curve_name, "Curve3D")
                if path_curve_name else "null")

    if alpha_curve_expr != "null" or any(
            track[3] != "null" for track in tracks):
        easing.need_ease_helper(ctx)

    # ---- 『更新』/『已结束』后面的用户逻辑:各生成一个函数 ----
    tick_func = ctx.declare_func_name(f"_gk_timeline_tick{tag}",
                                      key=node_lib.node_key(node))
    done_func = ctx.declare_func_name(f"_gk_timeline_done{tag}",
                                      key=node_lib.node_key(node))
    # ── 『执行』和『重播』共用同一个"启动"函数(用户 2026-10-05)────────
    # 两个入口做的事**完全一样**(时间归零、从头播),所以抽成一个函数,
    # 两个入口各调一次。这同时解决了"第二个入口会不会把整段生成两遍"
    # 的问题:每帧钩子和分支函数只在**第一次**进来时生成(见
    # ctx._timeline_started),第二次进来只补一句调用。
    play_func = ctx.declare_func_name(f"_gk_timeline_play{tag}",
                                      key=node_lib.node_key(node))
    node_key = node_lib.node_key(node)
    first_time = node_key not in ctx._timeline_started
    if first_time:
        ctx._timeline_started.add(node_key)
        prev_event = ctx._current_event
        ctx._current_event = node.name
        has_update = _timeline_branch(ctx, node, "更新", tick_func)
        has_done = _timeline_branch(ctx, node, "已结束", done_func)
        ctx._current_event = prev_event

        # ---- 每帧推进 ----
        lines = [f"if {play_var}:"]
        if mode == "LOOP":
            lines += [
                f"    {time_var} += delta",
                f"    if {time_var} >= {duration}:",
                f"        {time_var} = fmod({time_var}, {duration})",
            ]
        elif mode == "PINGPONG":
            lines += [
                f"    {time_var} += {dir_var} * delta",
                f"    if {time_var} >= {duration}:",
                f"        {time_var} = {duration}",
                f"        {dir_var} = -1.0",
                f"    elif {time_var} <= 0.0:",
                f"        {time_var} = 0.0",
                f"        {dir_var} = 1.0",
            ]
        else:
            lines += [
                f"    {time_var} += delta",
                f"    if {time_var} > {duration}:",
                f"        {time_var} = {duration}",
            ]
        lines.append(f"    var {raw_var}: float = clampf({time_var} / {duration}, "
                     f"0.0, 1.0)")
        lines.append(f"    {alpha_var} = {alpha_shape}")
        for value_var, start_value, end_value, curve_expr in tracks:
            shape = (f"_gk_ease({raw_var}, 0, 0, {curve_expr})"
                     if curve_expr != "null" else raw_var)
            lines.append(f"    {value_var} = {start_value} + ({end_value} - "
                         f"{start_value}) * {shape}")
        if path_curve_name:
            lines += [
                f"    if {fly_var} != null:",
                f"        {path_var} = {fly_var}.sample_baked("
                f"{raw_var} * {fly_len}, true)",
            ]
        if has_update:
            lines.append(f"    {tick_func}()")
        if mode == "ONCE":
            lines += [
                f"    if {time_var} >= {duration}:",
                f"        {play_var} = false",
                f"        {done_var} = true",
            ]
            if has_done:
                lines.append(f"        {done_func}()")
            if repeat == "QUEUE":
                # ── 排队的"再来一遍"(用户 2026-10-06)────────────────
                # 『重复触发时 = 等播完再重播』:播放中来的触发只是把
                # pending 置真,播完这一刻才真的再放一遍 ——
                # 所以"先放完再重复"是**自然排队**的(不会两次叠在一起)。
                lines += [
                    f"        if {pending_var}:",
                    f"            {play_func}()",
                ]
        ctx.process_hooks.setdefault(f"timeline{tag}", []).extend(lines)

        # ---- 启动函数:时间归零、从头播(『执行』/『重播』都走它)----
        start_lines = []
        if repeat in ("QUEUE", "IGNORE"):
            # 正在播的时候又触发:排队或忽略(用户 2026-10-06)
            start_lines += [
                f"    if {play_var}:",
            ]
            if repeat == "QUEUE":
                start_lines.append(f"        {pending_var} = true")
            start_lines += [
                "        return",
            ]
        start_lines += [
            f"    {time_var} = 0.0",
            f"    {dir_var} = 1.0",
            f"    {done_var} = false",
            f"    {play_var} = true",
            f"    {alpha_var} = 0.0",
        ]
        if pending_var:
            # 真的开始播了 -> 把"排队"的请求消费掉。
            # 插在 guard(3 行:if / 置 pending / return)**之后**,
            # 所以"播放中触发"那一支不会把它清掉。
            start_lines.insert(3, f"    {pending_var} = false")
        for value_var, start_value, _end, _curve in tracks:
            start_lines.append(f"    {value_var} = {start_value}")
        start_lines += [
            f"    {fly_var} = {fly_expr}",
            f"    {fly_len} = 0.0",
            f"    if {fly_var} != null:",
            f"        {fly_len} = {fly_var}.get_baked_length()",
        ]
        ctx.extra_sections.append({
            "signature": f"func {play_func}() -> void:",
            "lines": start_lines,
            "empty_body": "    pass",
            "separator": True,
        })

    # 入口本体:『执行』与『重播』都只是"从头开始"
    ctx.add_line(indent, f"{play_func}()")


def _timeline_branch(ctx, node, output, func_name):
    """把时间轴某个执行口后面的逻辑生成成一个函数。返回"有没有内容"。"""
    with ctx.capture() as section:
        ctx._active.clear()
        _chain(ctx, node, 1, output)
    if len(section) == 0:
        return False
    ctx.extra_sections.append({
        "signature": f"func {func_name}() -> void:",
        "lines": list(section.lines),
        "empty_body": "    pass",
        "separator": True,
    })
    return True


@multi_output("GKNodeTimeline")
def _expr_timeline_outputs(ctx, node):
    """时间轴的右侧输出:Alpha / 各轨道值 / 曲线位置 / 已结束吗。

    全是成员变量(推进钩子每帧更新),所以**在任何地方读都是最新值** ——
    和弹簧臂那三个输出一个道理。
    """
    tag = ctx.node_tag(node, "timeline")
    prefix = f"_gk_tl{tag}"
    out = {
        "Alpha": ctx.declare_var(f"{prefix}_alpha", "float"),
        "已结束吗": ctx.declare_var(f"{prefix}_done", "bool", initial="false"),
        "曲线位置": ctx.declare_var(f"{prefix}_path", "Vector3"),
    }
    count = max(0, min(4, int(getattr(node, "gk_track_count", 2) or 0)))
    for index in range(1, count + 1):
        out[f"轨道{index}值"] = ctx.declare_var(f"{prefix}_tr{index}", "float")
    return out


@stmt_node("GKNodeSpawn")
def _stmt_spawn(ctx, node, indent):
    path = ctx.input_string(node, "场景路径", "res://scenes/Bullet.tscn")
    temp = ctx.new_temp("scene")
    inst = ctx.new_temp("inst")
    # 注意:整段都保持在同一个缩进层级，"实例"输出才能在后续语句里被引用
    ctx.add_line(indent, f"var {temp} := load({path}) as PackedScene")
    ctx.add_line(indent, f"if {temp} == null:")
    ctx.add_line(indent + 1, f'push_error("场景加载失败: %s" % {path})')
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f"var {inst} := {temp}.instantiate()")
    position = ctx.input_vector(node, "位置", dim=ctx.dim)
    ctx.add_line(indent + 1, f"if {inst} is Node3D or {inst} is Node2D:")
    ctx.add_line(indent + 2, f"{inst}.position = {position}")
    parent = (getattr(node, "gk_parent", "") or "").strip()
    if parent and parent != TARGET_SELF:
        parent_expr = ctx.target_expr(node, "gk_parent", hint="parent")
        ctx.add_line(indent + 1, f"{parent_expr}.add_child({inst})")
    else:
        ctx.add_line(indent + 1, f"get_tree().current_scene.add_child({inst})")


# ==========================================================================
# 显示 / 相机 / 时间
# ==========================================================================
# 只有这些基类带 `visible` 属性。往 Node 上写 `visible` 会让 GDScript
# 直接报 "Identifier not declared"，所以必须提前拦下来给告警。
_VISIBLE_BASES = {
    "Node2D", "Node3D", "Control", "CanvasItem",
    "Sprite2D", "Sprite3D", "AnimatedSprite2D", "AnimatedSprite3D",
    "CharacterBody2D", "CharacterBody3D", "RigidBody2D", "RigidBody3D",
    "StaticBody2D", "StaticBody3D", "Area2D", "Area3D",
    "Label", "Button", "ProgressBar", "TextureRect", "NinePatchRect",
    "TileMap", "TileMapLayer", "CollisionShape2D", "CollisionShape3D",
}


def _base_supports_visible(base_class):
    text = str(base_class or "")
    return text in _VISIBLE_BASES or text.endswith(("2D", "3D"))


@stmt_node("GKNodeSetVisible")
def _stmt_set_visible(ctx, node, indent):
    target = ctx.target_expr(node)
    if target == "self" and not _base_supports_visible(ctx.base_class):
        ctx.warn(f"『{node.bl_label}』的目标是自身，但脚本基类 {ctx.base_class} "
                 f"没有 visible 属性(Godot 会报错)，已跳过这一句")
        return
    socket = ctx._socket(node, "可见")
    if socket is not None and getattr(socket, "is_linked", False):
        value = ctx.expr_of(socket)
    else:
        value = "true" if getattr(node, "gk_visible", True) else "false"
    ctx.add_line(indent, f"{target}.visible = {value}")


@stmt_node("GKNodeSetPaused")
def _stmt_set_paused(ctx, node, indent):
    mode = "PROCESS_MODE_DISABLED" if getattr(node, "gk_paused", True) else "PROCESS_MODE_INHERIT"
    ctx.add_line(indent, f"{ctx.target_expr(node)}.process_mode = Node.{mode}")


@stmt_node("GKNodeSetTimeScale")
def _stmt_set_time_scale(ctx, node, indent):
    scale = _tunable_number(ctx, node, "倍速", "1.0", "time_scale")
    ctx.add_line(indent, f"Engine.time_scale = {scale}")


# ==========================================================================
# 射线
# ==========================================================================
def _ray_origin_and_space(ctx, node, indent):
    """生成射线的查询空间，返回 (space变量, params类型, 起点表达式, 维度前缀)。

    2D / 3D 的 API 名字不同，而且基类上不存在的方法**在编译期**就会被拒绝
    (实测 `Node2D` 上写 get_world_3d() 直接报 Function not found)，
    所以这里按项目维度生成对应的一套，而不是运行时判断。
    """
    if ctx.dim == 2:
        space_type = "PhysicsDirectSpaceState2D"
        params_type = "PhysicsRayQueryParameters2D"
        world_call = "get_world_2d"
        origin = "global_position"
    else:
        space_type = "PhysicsDirectSpaceState3D"
        params_type = "PhysicsRayQueryParameters3D"
        world_call = "get_world_3d"
        # Node3D 用 global_transform.origin:它在非物理节点上也存在
        origin = "global_transform.origin"
    space = ctx.new_temp("space")
    ctx.add_line(indent, f"var {space}: {space_type} = {world_call}().direct_space_state")
    return space, params_type, origin


def _emit_ray_branch(ctx, node, indent, hit_var, origin_expr, params_type, space):
    """把射线射出去，结果写进 hit_var。"""
    direction = ctx.input_vector(node, "方向")
    distance = ctx.input_number(node, "距离", "200.0")
    offset = getattr(node, "gk_offset", (0.0, 0.0, 0.0)) or (0.0, 0.0, 0.0)
    layer = int(getattr(node, "gk_layer", 1) or 1)

    vector_ctor = "Vector2" if ctx.dim == 2 else "Vector3"
    offset_expr = f"{vector_ctor}({gd_number(offset[0])}, {gd_number(offset[1])}" + (
        "" if ctx.dim == 2 else f", {gd_number(offset[2])}") + ")"

    dir_var = ctx.new_temp("dir")
    from_var = ctx.new_temp("from")
    to_var = ctx.new_temp("to")
    query_var = ctx.new_temp("query")

    ctx.add_line(indent, f"var {dir_var} := ({direction}).normalized()")
    ctx.add_line(indent, f"var {from_var} := {origin_expr} + {offset_expr}")
    ctx.add_line(indent, f"var {to_var} := {from_var} + {dir_var} * {distance}")
    ctx.add_line(indent, f"var {query_var} := {params_type}.create({from_var}, {to_var})")
    ctx.add_line(indent, f"{query_var}.collision_mask = {layer}")
    ctx.add_line(indent, f"var {hit_var}: Dictionary = {space}.intersect_ray({query_var})")
    return hit_var


@stmt_node("GKNodeRaycast")
def _stmt_raycast(ctx, node, indent):
    """射线检测:命中 / 没打中 两个出口。

    ── 为什么出口不叫『完成』 ──────────────────────────────────────
    和按钮、存档判断同理:emit_statement 会自动沿『完成』续接，
    那样无论有没有打到东西后面的逻辑都会跑，分支就废了。
    """
    space, params_type, origin = _ray_origin_and_space(ctx, node, indent)
    hit_var = ctx.new_temp("hit")
    _emit_ray_branch(ctx, node, indent, hit_var, origin, params_type, space)

    # 打到的东西 / 命中点存进成员变量，下游可直接引用
    target_var = ctx.declare_var(f"gk_ray_{snake_case(node.name, 'hit')}", "Variant",
                                 key=node_lib.node_key(node))
    point_var = ctx.declare_var(f"gk_ray_point_{snake_case(node.name, 'hit')}", "Variant",
                                key=node_lib.node_key(node))
    ctx.read_vars.add(target_var)
    ctx.read_vars.add(point_var)

    ctx.add_line(indent, f"if not {hit_var}.is_empty():")
    ctx.add_line(indent + 1, f"{target_var} = {hit_var}[\"collider\"]")
    ctx.add_line(indent + 1, f"{point_var} = {hit_var}[\"position\"]")
    before = len(ctx.sink)
    _chain(ctx, node, indent + 1, "命中")
    if len(ctx.sink) == before:
        ctx.add_line(indent + 1, "pass")
    has_else = any(True for _ in _next_nodes(node, "没打中"))
    if has_else:
        ctx.add_line(indent, "else:")
        before = len(ctx.sink)
        _chain(ctx, node, indent + 1, "没打中")
        if len(ctx.sink) == before:
            ctx.add_line(indent + 1, "pass")


# ==========================================================================
# 定时
# ==========================================================================
@stmt_node("GKNodeEverySeconds")
def _stmt_every_seconds(ctx, node, indent):
    """每隔 N 秒触发一次。

    ── 为什么出口不叫『完成』 ──────────────────────────────────────
    按钮同理:叫『完成』会在建好计时器的瞬间就往下执行，
    而不是"等时间到了再执行"。所以出口叫『到点了』，
    真正的续接逻辑放进 timeout 回调。
    """
    interval = _tunable_number(ctx, node, "间隔", "1.0", "interval")
    timer_var = ctx.declare_var(f"gk_timer_{snake_case(node.name, 'every')}", "Timer",
                                key=node_lib.node_key(node))
    handler = ctx.declare_func_name(f"gk_on_{snake_case(node.name, 'every')}", key=node_lib.node_key(node))
    autostart = "true" if getattr(node, "gk_start_now", False) else "false"

    ctx.add_line(indent, f"if {timer_var} == null:")
    ctx.add_line(indent + 1, f"{timer_var} = Timer.new()")
    ctx.add_line(indent + 1, f"{timer_var}.name = {gd_string(node.name)}")
    ctx.add_line(indent + 1, f"{timer_var}.wait_time = {interval}")
    # one_shot = false 才是"每隔 N 秒"而不是"N 秒后一次"
    ctx.add_line(indent + 1, f"{timer_var}.one_shot = false")
    ctx.add_line(indent + 1, f"{timer_var}.autostart = {autostart}")
    ctx.add_line(indent + 1, f"{timer_var}.timeout.connect({handler})")
    ctx.add_line(indent + 1, "self.add_child(" + timer_var + ")")

    # ⚠ 写入目标要**还回去**(见 GenContext.capture 的说明):不还的话,
    #   这个节点『完成』出口后面的逻辑会写进 timeout 回调段、而且在
    #   快照之后写入 = 被静默丢掉(和『界面_按钮』同一条坑)。
    saved_sink = ctx.sink
    lines = ctx.new_section()
    ctx._active.clear()
    prev_event = ctx._current_event
    ctx._current_event = node.name
    _chain(ctx, node, 1, "到点了")
    if len(lines) == 0:
        lines.add(1, "pass")
    ctx._current_event = prev_event
    ctx.sink = saved_sink
    ctx.extra_sections.append({
        "signature": f"func {handler}() -> void:",
        "lines": list(lines.lines),
        "empty_body": "    pass",
        "separator": True,
    })


@stmt_node("GKNodeTweenValue")
def _stmt_tween_value(ctx, node, indent):
    raw_var = (getattr(node, "gk_var", "") or "").strip()
    if not raw_var:
        ctx.warn(f"『{node.bl_label}』没有填变量名，这一句被跳过了")
        return
    var_name = ctx.declare_var(raw_var, "float")
    ctx.read_vars.add(var_name)
    to_value = _tunable_number(ctx, node, "目标值", "0.0", "tween_to")
    duration = _tunable_number(ctx, node, "时长", "0.5", "tween_time")
    trans = getattr(node, "gk_trans", "TRANS_LINEAR") or "TRANS_LINEAR"
    ease = getattr(node, "gk_ease", "EASE_IN_OUT") or "EASE_IN_OUT"

    tween_var = ctx.new_temp("tween")
    ctx.add_line(indent, f"var {tween_var} := create_tween()")
    ctx.add_line(indent, f"{tween_var}.set_trans(Tween.{trans})")
    ctx.add_line(indent, f"{tween_var}.set_ease(Tween.{ease})")
    # 用 "变量名" 字符串做属性路径:tween_property 需要 NodePath 形式，
    # 对脚本成员变量来说就是变量名字符串本身。
    ctx.add_line(indent, f"{tween_var}.tween_property(self, {gd_string(var_name)}, "
                         f"{to_value}, {duration})")


# ==========================================================================
# 列表 / 字典
# ==========================================================================
def _list_var(ctx, node, gd_type="Array"):
    """列表 / 字典的目标变量:按名字共享(同一个名字就是同一个容器)。"""
    raw = (getattr(node, "gk_var", "") or "").strip()
    if not raw:
        return None
    return ctx.declare_var(raw, gd_type)


def _any_value_expr(ctx, node, socket_name="值"):
    """取一个"任意值"输入:连线了用上游表达式，否则用字面量。"""
    socket = ctx._socket(node, socket_name)
    if socket is not None and getattr(socket, "is_linked", False):
        return ctx.expr_of(socket)
    return gd_literal(node.input_value(ctx, socket_name, 0))


@stmt_node("GKNodeListNew")
def _stmt_list_new(ctx, node, indent):
    var = _list_var(ctx, node)
    if var is None:
        ctx.warn(f"『{node.bl_label}』没有填变量名，这一句被跳过了")
        return
    # 赋成空数组而不是 clear():这样"新建"是真的重新开始，语义更直白
    ctx.add_line(indent, f"{var} = []")


@stmt_node("GKNodeListAdd")
def _stmt_list_add(ctx, node, indent):
    var = _list_var(ctx, node)
    if var is None:
        ctx.warn(f"『{node.bl_label}』没有填变量名，这一句被跳过了")
        return
    ctx.add_line(indent, f"{var}.append({_any_value_expr(ctx, node)})")


@stmt_node("GKNodeListRemove")
def _stmt_list_remove(ctx, node, indent):
    var = _list_var(ctx, node)
    if var is None:
        ctx.warn(f"『{node.bl_label}』没有填变量名，这一句被跳过了")
        return
    ctx.add_line(indent, f"{var}.erase({_any_value_expr(ctx, node)})")


@stmt_node("GKNodeListGet")
def _stmt_list_get(ctx, node, indent):
    var = _list_var(ctx, node)
    if var is None:
        ctx.warn(f"『{node.bl_label}』没有填变量名，这一句被跳过了")
        return
    target_name = (getattr(node, "gk_target_var", "") or "").strip()
    if not target_name:
        ctx.warn(f"『{node.bl_label}』没有填『写入变量』，这一句被跳过了")
        return
    target = ctx.declare_var(target_name, "Variant")
    ctx.read_vars.add(target)
    index = ctx.input_number(node, "下标", "0")
    default = gd_literal(node.input_value(ctx, "默认值", 0.0))
    index_var = ctx.new_temp("idx")
    ctx.add_line(indent, f"var {index_var} := int({index})")
    # 越界必须给默认值:直接 items[99] 在 Godot 里会报错并中断这一帧
    ctx.add_line(indent, f"if {index_var} >= 0 and {index_var} < {var}.size():")
    ctx.add_line(indent + 1, f"{target} = {var}[{index_var}]")
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f"{target} = {default}")


@stmt_node("GKNodeDictSet")
def _stmt_dict_set(ctx, node, indent):
    var = _list_var(ctx, node, "Dictionary")
    if var is None:
        ctx.warn(f"『{node.bl_label}』没有填变量名，这一句被跳过了")
        return
    key = ctx.input_string(node, "键", "")
    ctx.add_line(indent, f"{var}[{key}] = {_any_value_expr(ctx, node)}")


@stmt_node("GKNodeDictGet")
def _stmt_dict_get(ctx, node, indent):
    var = _list_var(ctx, node, "Dictionary")
    if var is None:
        ctx.warn(f"『{node.bl_label}』没有填变量名，这一句被跳过了")
        return
    target_name = (getattr(node, "gk_target_var", "") or "").strip()
    if not target_name:
        ctx.warn(f"『{node.bl_label}』没有填『写入变量』，这一句被跳过了")
        return
    target = ctx.declare_var(target_name, "Variant")
    ctx.read_vars.add(target)
    key = ctx.input_string(node, "键", "")
    default = gd_literal(node.input_value(ctx, "默认值", 0.0))
    ctx.add_line(indent, f"{target} = {var}.get({key}, {default})")


@stmt_node("GKNodeDictHas")
def _stmt_dict_has(ctx, node, indent):
    var = _list_var(ctx, node, "Dictionary")
    if var is None:
        ctx.warn(f"『{node.bl_label}』没有填变量名，这一句被跳过了")
        return
    key = ctx.input_string(node, "键", "")
    ctx.add_line(indent, f"if {var}.has({key}):")
    before = len(ctx.sink)
    _chain(ctx, node, indent + 1, "有")
    if len(ctx.sink) == before:
        ctx.add_line(indent + 1, "pass")
    has_else = any(True for _ in _next_nodes(node, "没有"))
    if has_else:
        ctx.add_line(indent, "else:")
        before = len(ctx.sink)
        _chain(ctx, node, indent + 1, "没有")
        if len(ctx.sink) == before:
            ctx.add_line(indent + 1, "pass")


@stmt_node("GKNodeJoyVibrate")
def _stmt_joy_vibrate(ctx, node, indent):
    weak = gd_number(getattr(node, "gk_weak", 0.6))
    strong = gd_number(getattr(node, "gk_strong", 0.6))
    duration = _tunable_number(ctx, node, "时长", "0.2", "rumble_time")
    device = int(getattr(node, "gk_device", 0) or 0)
    ctx.add_line(indent, f"Input.start_joy_vibration({device}, {weak}, {strong}, {duration})")
    # 到点自动停:不依赖玩家再点一次，也不会一直震
    ctx.add_line(indent, f"get_tree().create_timer({duration}).timeout.connect("
                         f"func(): Input.stop_joy_vibration({device}))")




_PORT_GD_TYPES = {"bool": "bool", "int": "int", "float": "float",
                  "string": "String", "vector": "Vector3"}


def _emit_logic_ports(ctx):
    """把侧栏声明的端口变成脚本变量(带类型和初值)。"""
    tree = getattr(ctx, "tree", None)
    ports = getattr(tree, "gk_ports", None) if tree is not None else None
    if not ports:
        return
    try:
        for port in ports:
            raw = str(port.port_name).strip()
            if not raw:
                continue
            gd_type = _PORT_GD_TYPES.get(str(port.port_type), "Variant")
            initial = str(port.default_value or "").strip()
            if not initial:
                initial = {"bool": "false", "int": "0", "float": "0.0",
                           "string": '\"\"', "vector": "Vector3.ZERO"}.get(
                               str(port.port_type), "null")
            name = ctx.declare_var(f"输出_{raw}", gd_type, initial=initial)
            # 让『变量_设置/『变量_读取』用同名时命中**这一个**变量 ——
            # 否则端口那份永远是初值(它俩会各写一份,实测踩到)
            try:
                ctx._var_aliases[raw] = name
                # ── 名字里有大写字母时,别名要按**规范化后的键**也登记一份 ──
                # 查找那侧走的是 `snake_case(name)`(见 context.declare_var),
                # 『位置X』会变成『位置x』。只登记原样的话两边对不上 ——
                # 表现是同一个端口被声明成**两个**变量(输出_位置x 和 位置x),
                # 端口永远停在初值(预设的单测抓到的)。
                from ..nodes.node_base import snake_case
                ctx._var_aliases.setdefault(snake_case(raw, ""), name)
            except Exception:
                pass
            ctx.note(f"对外端口『{raw}』({gd_type})-> {name}")
    except Exception:
        pass


def _emit_logic_vars(ctx):
    """把侧栏**声明的变量**变成脚本成员变量(带类型和初值)。

    在遍历节点之前调用(见 Generator.__init__)—— 这样第一个『变量_设置』
    拿到的就是这里声明的类型/初值,而不是"按连线现推"的那一份。
    没声明过的变量仍然由 declare_var 在第一次使用时自动建出来(老图不变)。
    """
    tree = getattr(ctx, "tree", None)
    variables = getattr(tree, "gk_vars", None) if tree is not None else None
    if not variables:
        return
    try:
        for var in variables:
            raw = str(var.var_name).strip()
            if not raw:
                continue
            var_type = str(getattr(var, "var_type", "float"))
            gd_type = _PORT_GD_TYPES.get(var_type, "Variant")
            initial = str(var.default_value or "").strip()
            if not initial:
                initial = {"bool": "false", "int": "0", "float": "0.0",
                           "string": '\"\"', "vector": "Vector3.ZERO"}.get(
                               var_type, "null")
            # ── 初值要按类型收拾一下,否则会生成非法 GDScript ─────────
            # 初值框默认是 "0",用户声明布尔变量时很容易忘了改 ——
            # `var x: bool = 0` 会被 Godot 报类型不符,整个脚本加载失败。
            # 文本同理:`var x: String = hello` 少了引号。
            if gd_type == "bool":
                initial = ("false" if initial.lower()
                           in ("", "0", "false", "no", "off") else "true")
            elif gd_type == "String" and not initial.startswith(("\"", "'")):
                initial = gd_string(initial)
            name = ctx.declare_var(raw, gd_type, initial=initial)
            ctx.note(f"声明变量『{raw}』({gd_type}) = {initial} -> {name}")
    except Exception:
        pass


def _declared_ports(ctx, direction):
    """当前图在侧栏声明过的端口名集合;读不到就返回 None(离线/老文件)。

    返回 None 表示"没法校验",调用方**不要**因此报警 —— 校验是加分项,
    不能因为拿不到 UI 数据就让导出失败。
    """
    tree = getattr(ctx, "tree", None)
    ports = getattr(tree, "gk_ports", None) if tree is not None else None
    if ports is None:
        return None
    try:
        return {str(p.port_name).strip() for p in ports
                if str(getattr(p, "direction", "out")) == direction
                and str(p.port_name).strip()}
    except Exception:
        return None

@stmt_node("GKNodeQuitGame")
def _stmt_quit_game(ctx, node, indent):
    """退出游戏(桌面版)。

    延迟 > 0 时用 Timer 等一会儿再退 —— 让"退出动画/音效"播完。
    没有延迟就直接 `get_tree().quit()`。
    """
    delay = float(getattr(node, "gk_delay", 0.0) or 0.0)
    if delay > 0.0:
        ctx.add_line(indent, f"await get_tree().create_timer("
                             f"{gd_number(delay)}).timeout")
        ctx.note("『流程_退出游戏』用了延迟 —— 生成的是 `await`,"
                 "别把它接在 _physics_process 的中间(会挂起后面的逻辑)")
    ctx.add_line(indent, "get_tree().quit()")


@stmt_node("GKNodeLogicOutput")
def _stmt_logic_output(ctx, node, indent):
    """『逻辑_输出结果』:把一个布尔发布成脚本变量 `输出_<名字>`。

    用户第七轮的计划:"一个游戏逻辑最后应该输出布尔变量,以便与其他的
    游戏逻辑联动"。所有节点图编译成**同一个脚本**,所以"输出"就是
    "约定一个变量名":这里写,别处用『变量_读取』读。

    导出报告里会列出来(见下面的 note),方便对照谁输出了什么。
    """
    raw = str(getattr(node, "gk_output_name", "") or "").strip()
    if not raw:
        ctx.warn("『逻辑_输出结果』没填输出名 —— 它不会生成任何东西")
        return
    name = ctx.declare_var(f"输出_{raw}", "bool", initial="false")
    socket = ctx._socket(node, "值")
    if socket is not None and getattr(socket, "is_linked", False):
        value_expr = ctx.expr_of(socket)
    else:
        value_expr = "true" if getattr(node, "gk_value", True) else "false"
    # 值可能是个任意表达式(比如『逻辑_比较』的结果)—— bool 变量收别的类型
    # 会报类型不符,这里统一裹一层 bool()。字面量 true/false 就不用裹了。
    if value_expr not in ("true", "false"):
        value_expr = f"bool({value_expr})"
    ctx.add_line(indent, f"{name} = {value_expr}")
    outputs = getattr(ctx, "_logic_outputs", None)
    if outputs is None:
        outputs = []
        ctx._logic_outputs = outputs
    outputs.append((raw, name))
    # ── 端口校验(用户第 21 轮:"真接口")────────────────────────────
    # 图在侧栏声明过端口的话,这里比对一下:节点上填的名字**必须**在
    # 声明表里。以前是随便填 —— 打错一个字母不会报错,别的图读到空值,
    # 你只会以为是逻辑没跑到(这类"静默失败"最费时间)。
    declared = _declared_ports(ctx, "out")
    if declared is not None and raw not in declared:
        ctx.warn(f"『逻辑_输出结果』写的端口『{raw}』没有在侧栏"
                 f"『对外端口』里声明过 —— 别的图读它只会读到空值。"
                 f"要么去声明一个,要么改成已声明的:"
                 f"{'、'.join(sorted(declared)) or '(还没有)'}")


@stmt_node("GKNodeReloadScene")
def _stmt_reload_scene(ctx, node, indent):
    # 把 reload 推迟到帧末:这个节点常常是"按钮回调 / 计时器回调"里调的，
    # 在信号回调中间直接把场景树换掉，Godot 会报
    # "Parent node is busy setting up children"。
    ctx.add_line(indent, "get_tree().call_deferred(\"reload_current_scene\")")


@stmt_node("GKNodeChangeScene")
def _stmt_change_scene(ctx, node, indent):
    """『场景_切换关卡』:把整棵场景树换成另一张 .tscn。

    ── 为什么也走 call_deferred ──────────────────────────────────
    和『实例_重载场景』同一个坑:这个节点多半接在"吃到最后一枚金币"
    这类链路后面,而那条链路正在 `_physics_process` 里跑 —— 在帧中间
    直接换场景树,Godot 会报 "Parent node is busy setting up children"。
    推迟到帧末最稳。

    ── 为什么这里**不**做"等待/转场" ─────────────────────────────
    转场是"切之前"那段运镜:用『相机_切换』(带过渡)或
    『相机_位置』/『相机_旋转』(带平滑)摆好,再跟一个『等待_秒』
    给它时间播完。这个节点只负责"换场"这一下 —— 职责单一,
    也不会把 await 偷偷塞进调用方的函数里(那条链后面还有别的逻辑)。
    """
    path = str(getattr(node, "gk_scene_path", "") or "").strip()
    if not path:
        ctx.warn("『场景_切换关卡』没填目标场景 —— 它不会生成任何东西")
        return
    if not path.startswith("res://"):
        ctx.warn(f"『场景_切换关卡』的目标『{path}』不是 res:// 路径 —— "
                 f"Godot 的 change_scene_to_file 只认 res:// 或绝对路径,"
                 f"建议改成 res://scenes/xxx.tscn")
    ctx.add_line(indent, f"get_tree().call_deferred(\"change_scene_to_file\", "
                         f"{gd_string(path)})")


@stmt_node("GKNodeFunctionDefine")
def _stmt_function_define(ctx, node, indent):
    """『函数_定义』不是执行流的一环 —— 它是函数的**起点**。

    有人往里接执行流时给一条能照着改的告警(而不是静默生成一堆看不懂的
    代码):这段逻辑挂不上去,得用『函数_调用』去叫它。
    """
    name = str(getattr(node, "gk_func_name", "") or "").strip() or "?"
    ctx.warn(f"『函数_定义({name})』是函数的**起点**,不用接执行流 —— "
             f"它左边那个『执行』引脚的连线被忽略了。"
             f"想在别处叫这个函数,用『函数_调用』节点")


@stmt_node("GKNodeFunctionCall")
def _stmt_function_call(ctx, node, indent):
    """调用一个声明过的函数(用户 2026-10-05 第十七轮)。

    实参按**声明里的顺序**取;名字没声明过 / 参数引脚找不到时明确告警,
    而不是生成一个猜出来的调用(Godot 会直接拒绝整个脚本)。
    """
    from .. import properties as props
    from ..nodes.node_base import safe_function_name

    raw = str(getattr(node, "gk_func_name", "") or "").strip()
    if not raw:
        ctx.warn("『函数_调用』没填『函数』—— 这一句被跳过了"
                 "(旁边放大镜可以从侧栏声明里挑)")
        return
    tree = getattr(ctx, "tree", None)
    spec = props.tree_functions(tree).get(raw) if tree is not None else None
    if spec is None:
        ctx.warn(f"『函数_调用』填的『{raw}』没有在侧栏『函数』框里声明过 —— "
                 f"这一句被跳过了")
        return

    args = []
    for param_name, param_type in (spec.get("params") or ()):
        socket = ctx._socket(node, param_name)
        if socket is not None and getattr(socket, "is_linked", False):
            args.append(ctx.expr_of(socket))
            continue
        # 没接线:用引脚上填的值;引脚都没有(声明改过还没重建)时给兜底
        if socket is not None:
            args.append(ctx.socket_default(socket))
        else:
            args.append("0" if param_type in ("int", "float") else
                        ("false" if param_type == "bool" else "null"))
    call = f"{safe_function_name(raw)}({', '.join(args)})"
    if not spec.get("return"):
        ctx.add_line(indent, call)
        return
    # 有返回值 -> 存一个临时变量,『结果』输出引用它(见下面的多输出处理器)
    result = ctx.new_temp("fres")
    ctx.function_results[node_lib.node_key(node)] = result
    ctx.add_line(indent, f"var {result} = {call}")


@multi_output("GKNodeFunctionCall")
def _expr_function_call_outputs(ctx, node):
    """『函数_调用』的『结果』输出 = 上面那句 `var _fres1 = ...`。"""
    result = ctx.function_results.get(node_lib.node_key(node))
    if not result:
        ctx.warn("『函数_调用』的『结果』被引用了,但这个函数声明的是**无返回** "
                 "—— 已按 null 处理。要取返回值,去侧栏『函数』框把它的"
                 "『返回』改成对应类型")
        return {"结果": "null"}
    return {"结果": result}


@stmt_node("GKNodeQueueFree")
def _stmt_queue_free(ctx, node, indent):
    # ── 『目标』引脚接了线 = 销毁一个**运行时**的节点引用 ────────────
    # 典型接法:『资产_创建实例』的『实例』输出 -> 这里(用完自己清掉)。
    # 这种节点自带网格,直接 queue_free 即可 —— 不能再走
    # _gk_free_with_visual:它是按**名字**去全树找同名网格清的,
    # 万一场景里恰好有个同名物体,会被连坐删掉(实测过这一族坑)。
    socket = ctx._socket(node, "目标")
    if socket is not None and getattr(socket, "is_linked", False):
        inst = ctx.expr_of(socket)
        ctx.add_line(indent, f"if {inst} != null and is_instance_valid({inst}):")
        ctx.add_line(indent + 1, f"{inst}.queue_free()")
        return
    raw = getattr(node, "gk_target", TARGET_SELF)
    if raw == TARGET_SELF:
        ctx.add_line(indent, "queue_free()")
    else:
        # ── 连"同名网格"一起清(用户 2026-10-04,实测)────────────────
        # 导出后一个物体常常是两个节点:网格在 glb 实例里、碰撞代理在
        # Colliders 下。只 free 代理的话物体还在画面上 ——
        # 实测:金币吃掉后还杵在原地(只有碰撞体消失了)。
        # 见 generator 里 _gk_free_with_visual 的说明。
        ctx.need_free_visual_helper = True
        ctx.add_line(indent, f"_gk_free_with_visual({ctx.target_expr(node)})")


def _shape_dict_expr(entry, dim):
    """一个碰撞形状 -> GDScript 字典字面量(『资产_创建实例』用)。"""
    vector = "Vector2" if dim == 2 else "Vector3"
    dims = tuple(entry.get("dims") or (1.0, 1.0, 1.0))
    offset = tuple(entry.get("offset") or (0.0, 0.0, 0.0))
    parts = [
        f'"kind": {int(entry.get("kind", 0))}',
        f'"dims": {vector}(' + ", ".join(
            gd_number(value) for value in dims[:dim]) + ")",
        f'"radius": {gd_number(float(entry.get("radius") or 0.5))}',
        f'"height": {gd_number(float(entry.get("height") or 1.0))}',
        f'"offset": {vector}(' + ", ".join(
            gd_number(value) for value in offset[:dim]) + ")",
    ]
    points = list(entry.get("points") or [])
    if points:
        flat = []
        for point in points:
            flat.extend((point[0], point[1], point[2]))
        parts.append('"points": PackedVector3Array(['
                     + ", ".join(gd_number(value) for value in flat) + "])")
    return "{" + ", ".join(parts) + "}"


@stmt_node("GKNodeAssetSpawn")
def _stmt_asset_spawn(ctx, node, indent):
    """『资产_创建实例』(用户 2026-10-05):运行时按资产名摆一份新的。

    ── 资产名 -> glb/碰撞尺寸 是谁给的 ─────────────────────────────
    名字是用户在 Blender 里随手起的,只有导出侧知道它对应哪份 glb、
    碰撞该生成多大 —— 所以导出时把这份表塞进代码生成选项
    (见 exporter.build_settings → asset_export.asset_codegen_map),
    这里只查表 + 生成一行调用。

    查不到名字时给**可照着改**的告警(而不是生成一份空路径):
    最常见的错就是把『资产名』拼错,或者那个物体是别的场景里的。
    """
    raw = str(getattr(node, "gk_asset", "") or "").strip()
    assets = dict(ctx.options.get("assets") or {})
    if not raw:
        ctx.warn("『资产_创建实例』没填『资产名』—— 这一句被跳过了"
                 "(右边放大镜可以从场景里的资产列表里挑)")
        return
    info = assets.get(raw)
    if info is None:
        known = "、".join(sorted(assets)) if assets else "这个场景里还没有资产"
        ctx.warn(f"『资产_创建实例』填的『{raw}』不在这个场景的资产里 —— "
                 f"这一句被跳过了。可用的是:{known}")
        return

    where = ctx.input_vector(node, "位置", dim=ctx.dim)
    solid = bool(getattr(node, "gk_solid", True)) and int(info.get("shape", -1)) >= 0
    # ── 形状表(本轮:多碰撞体 + 凸包)──────────────────────────────
    # 以前只传"一个形状 + 一组尺寸";现在传**形状字典的数组** ——
    # 本体 + 每个碰撞块各一条(和 .tscn 导出同一套数据),
    # 生成的实例和本体长得一模一样,不会本体挡人、这份穿过去。
    shapes = list(info.get("shapes") or []) if solid else []
    shapes_expr = ("[" + ", ".join(_shape_dict_expr(entry, ctx.dim)
                                   for entry in shapes) + "]")
    inst = ctx.new_temp("asset")
    ctx.asset_instances[node_lib.node_key(node)] = inst
    ctx.need_asset_spawn_helper = True
    ctx.add_line(
        indent,
        f"var {inst} := _gk_spawn_asset({gd_string(str(info.get('path') or ''))}, "
        f"{gd_string(raw)}, {where}, {shapes_expr}, "
        f"{int(info.get('layer') or 1)}, {int(info.get('mask') or 1)})")
    ctx.add_line(indent, f"if {inst} != null:")
    parent = (getattr(node, "gk_parent", "") or "").strip()
    if parent and parent != TARGET_SELF:
        parent_expr = ctx.target_expr(node, "gk_parent", hint="parent")
        ctx.add_line(indent + 1, f"{parent_expr}.add_child({inst})")
    else:
        ctx.add_line(indent + 1, f"get_tree().current_scene.add_child({inst})")


@multi_output("GKNodeAssetSpawn")
def _expr_asset_spawn_outputs(ctx, node):
    """『资产_创建实例』的『实例』输出 = 上面那句 `var _asset1 := ...`。

    ── 为什么是个局部变量 ────────────────────────────────────────
    运行时的节点引用只能拿变量传。创建它和用它(销毁 / 移动)通常在**同一条
    执行流**上,所以局部变量就够 —— 用不着为它开成员变量。
    输出被引用、但创建那一句还没生成(接到别的执行流上)时给一句告警,
    而不是生成一个未声明的名字(Godot 会直接拒绝整个脚本)。
    """
    inst = ctx.asset_instances.get(node_lib.node_key(node))
    if not inst:
        ctx.warn("『资产_创建实例』的『实例』被引用了,但创建那一句还没生成 —— "
                 "把它和用它的节点接在**同一条执行流**上(比如紧跟着接"
                 "『实例_销毁』)")
        return None
    return {"实例": inst}


@stmt_node("GKNodeSetCollision")
def _stmt_set_collision(ctx, node, indent):
    """『物理_启用碰撞』(用户 2026-10-05)。

    ── 为什么要一个辅助函数,而不是一行直写 ────────────────────────
    "关掉碰撞"在 Godot 4 里**没有一行现成的写法** ——
    `CollisionObject3D` 没有布尔型 `disabled`(实测:StaticBody3D 上
    只有 `disable_mode`),真正管用的是每个 `CollisionShape3D/2D` 自己的
    `disabled`。所以要把"目标 + 它下面所有碰撞形状"一起切,
    而且必须 `set_deferred` —— 在物理回调里直接改,Godot 会报
        Can't change this state while flushing queries
    这几件事散在每个用到的节点里会越写越乱,收进一个函数只写一次。
    """
    target = ctx.target_expr(node, "gk_target")
    socket = ctx._socket(node, "启用")
    if socket is not None and getattr(socket, "is_linked", False):
        on = ctx.expr_of(socket)
    else:
        on = "true" if getattr(node, "gk_enable", True) else "false"
    ctx.need_collision_helper = True
    ctx.add_line(indent, f"_gk_set_collision({target}, {on})")


# ==========================================================================
# 粒子特效的播放 / 停止 / 爆发(用户 2026-10-05 第二十一轮,需求 5)
# ==========================================================================
#
# ── 为什么这三个节点要"查表"才知道目标的类型 ──────────────────────
# `.emitting` 和 `.restart()` 在**具体的粒子类**上
# (GPUParticles3D / CPUParticles3D / …2D 那两版)。GDScript 是静态类型:
# 变量声明成 `Node` 却调用 `.emitting`,**整个脚本编译不过**。
# 而目标该是 GPU 还是 CPU、2D 还是 3D,只有导出侧知道(面板上选的)——
# 由 build_settings 塞进 options["fx"](见 asset_export.fx_codegen_map)。
# 表里查不到(选错物体 / 忘了勾『作为粒子发射器』)时给告警并跳过,
# 而不是生成一个不存在的节点名(Godot 会直接拒绝加载脚本)。

def _fx_target(ctx, node):
    """『特效_*』节点的目标引用(带准确类型)。给不了就告警 + 返回空串。"""
    raw = (getattr(node, "gk_target", "") or "").strip()
    if not raw or raw == TARGET_SELF:
        ctx.warn("『特效』节点的『目标』是『自身』—— 但脚本挂在角色身上,"
                 "粒子发射器是**另一个物体**。点目标右边的放大镜从场景里"
                 "挑那个发射器(比如『火把』),这一句才生成得出来")
        return ""
    info = dict(ctx.options.get("fx") or {}).get(raw)
    if info is None:
        known = "、".join(sorted(ctx.options.get("fx") or {})) \
            or "这个场景里还没有发射器"
        ctx.warn(f"『特效』节点的目标『{raw}』不是这个场景里的粒子发射器 —— "
                 f"在『游戏物理』→『粒子特效』里给它勾上『作为粒子发射器』。"
                 f"可用的是:{known}(这一句被跳过了)")
        return ""
    node_type = str(info.get("node_type") or "GPUParticles3D")
    return ctx.target_expr(node, "gk_target", hint="特效", node_type=node_type)


@stmt_node("GKNodeFxPlay")
def _stmt_fx_play(ctx, node, indent):
    """『特效_播放』:让发射器开始喷(不打断已经在喷的那一轮)。"""
    target = _fx_target(ctx, node)
    if not target:
        return
    ctx.add_line(indent, f"if {target} != null:")
    ctx.add_line(indent + 1, f"{target}.emitting = true")


@stmt_node("GKNodeFxStop")
def _stmt_fx_stop(ctx, node, indent):
    """『特效_停止』:停喷。

    ── 只停"新粒子",已经在飞的会自然消散完(Godot 就是这么设计的)──
    想要立刻清干净,用『特效_爆发』那种 restart 思路做不到
    (`restart()` 是重来)。所以这个节点语义写清楚:停 = 不再新生成。
    """
    target = _fx_target(ctx, node)
    if not target:
        return
    ctx.add_line(indent, f"if {target} != null:")
    ctx.add_line(indent + 1, f"{target}.emitting = false")


@stmt_node("GKNodeFxBurst")
def _stmt_fx_burst(ctx, node, indent):
    """『特效_爆发』:重新喷一轮(一次性冲击 / 受击火花 / 落地烟尘)。"""
    target = _fx_target(ctx, node)
    if not target:
        return
    ctx.add_line(indent, f"if {target} != null:")
    # restart() 四个粒子类都有(GPU / CPU × 2D / 3D),它把在飞的粒子清掉、
    # 从第 0 帧重来 —— 一次性发射器就是"再放一轮",循环发射器就是"重开一段"。
    ctx.add_line(indent + 1, f"{target}.restart()")
    ctx.add_line(indent + 1, f"{target}.emitting = true")


@stmt_node("GKNodeFlashRed")
def _stmt_flash_red(ctx, node, indent):
    """『特效_闪红』:把目标整棵模型染成同一个色,过一会儿自动恢复。

    主体在 `ensure_flash_helper` 生成的那个函数里(理由见那边的说明)——
    这里只负责"目标可能为空"的守卫和参数拼装。
    """
    target = ctx.target_expr(node, "gk_target", hint="节点")
    seconds = ctx.input_number(node, "秒", "0.25")
    color = getattr(node, "gk_color", (1.0, 0.25, 0.25, 1.0))
    from .expressions import ensure_flash_helper
    helper = ensure_flash_helper(ctx)
    # self 永远是有效的,不用包 `if self != null`(包了只是噪音)
    body = indent
    if target != "self":
        ctx.add_line(indent, f"if {target} != null:")
        body = indent + 1
    ctx.add_line(body,
                 f"{helper}({target}, Color({gd_number(color[0])}, "
                 f"{gd_number(color[1])}, {gd_number(color[2])}, "
                 f"{gd_number(color[3])}), {seconds})")


def _link_type(socket):
    """从上游输出引脚的**种类**推断 GDScript 类型(推不出来就 None)。

    ── 为什么要这个(用户 2026-10-03)────────────────────────────
    `变量_设置` 的『值』接了线时,原来一律当成 Variant ——
    于是「变量_设置(is_hurt) <- 常量_布尔」生成出
    `var is_hurt: Variant`,再和其它地方按 bool 用它的代码撞上就报
    "同时被当成 bool 和 Variant 使用"。

    线那头是什么引脚其实**是知道的**(常量_布尔 的输出就是 NodeSocketBool),
    照着它声明类型即可,不用猜。
    """
    links = getattr(socket, "links", ()) or ()
    for link in links:
        from_socket = getattr(link, "from_socket", None)
        kind = (getattr(from_socket, "bl_idname", "")
                or getattr(from_socket, "type", ""))
        mapping = {
            "NodeSocketBool": "bool",
            "NodeSocketFloat": "float",
            "NodeSocketInt": "int",
            "NodeSocketString": "String",
        }
        if kind in mapping:
            return mapping[kind]
    return None


@stmt_node("GKNodeSetVar")
def _stmt_set_var(ctx, node, indent):
    """『变量_设置』:一张卡片可以设好几个变量(仿蓝图 Set 节点)。

    ── 为什么第 1 个用老属性名(踩过的坑)────────────────────────────
    第 1 个变量仍然读 `gk_var_name` / `gk_value`、引脚仍然叫『值』——
    老文件里接好的线、填好的名字一个字都不用改。第 2 个开始才是
    `gk_var_name_2` / 『值 2』。
    """
    pairs = []
    slot_getter = getattr(node, "slots", None)
    if callable(slot_getter):
        pairs = list(slot_getter())
    if not pairs:
        # 桩环境/老节点:退回"只有第一个"的老路径
        raw_name = getattr(node, "gk_var_name", "value")
        pairs = [(1, raw_name, getattr(node, "gk_value", 0.0))]

    for index, raw_name, fallback in pairs:
        # 引脚名:第 1 个叫『值』(老图接的就是它),第 2 个起叫『值 2』『值 3』。
        # 这里 index 是**槽位号**(1 起),别再 +1 —— 写成 "值 {index+1}"
        # 会去找一个不存在的『值 3』,于是第 2 个槽永远读不到连线,
        # 悄悄退回数字框里的值(示例里就是这么被抓到的)。
        pin_name = "值" if index == 1 else f"值 {index}"
        raw_value = None
        socket = ctx._socket(node, pin_name)
        if socket is not None and getattr(socket, "is_linked", False):
            value_expr = ctx.expr_of(socket)
            # ── 值接了线就**不要**替它声明类型(踩过的坑)────────────────
            # 这里原来写的是 `gd_type = "Variant"` —— 于是
            # 「变量_设置(is_hurt) <- 常量_布尔」这种最常见的写法会报
            #     变量『is_hurt』同时被当成 bool 和 Variant 使用,请改用不同的变量名
            # 用户会去改一个**根本没问题**的变量名,而真正的类型(布尔)
            # 本来是清楚的:线的那头就是常量_布尔。
            # 传 None = "我没意见",由别处(动画树的变量面板/取变量节点)决定类型。
            # 能从上游引脚的种类看出来就用它 —— 见 _link_type
            gd_type = _link_type(socket)
        else:
            raw_value = node.input_value(ctx, pin_name, fallback)
            # ── 空值要退回属性里的值(踩过的坑)──────────────────────
            # 多槽卡片的第 2、3… 个引脚是 `build_sockets` 现造的,
            # 没设过 default_value -> `input_value` 返回 **None** ->
            # 生成出 `score = null`,而 `var score: float` 收不了 null,
            # Godot **解析期**就报
            #     Parse Error: Cannot assign a value of type "null" as "float"
            # 整个脚本加载失败。这是 2D 示例探针抓出来的。
            if raw_value is None:
                raw_value = fallback
            value_expr = gd_literal(raw_value)
            gd_type = _gd_type_of(raw_value)
        source_type = gd_type                       # 线那头(或字面量)是什么
        _assign_var_value(ctx, indent, raw_name, value_expr, source_type, raw_value)


def _assign_var_value(ctx, indent, raw_name, value_expr, source_type,
                      raw_value=None):
    """把**最终值表达式**写进变量:类型兜底 + setter 路径。

    ── 为什么单独抽出来 ────────────────────────────────────────────
    『变量_设置』和『变量_增减』干的是同一件事(把某个值写进变量、
    该转 int 就转、绑了 setter 就调 setter),差别只在"值表达式怎么来"。
    两条路各写一份的话,以后修类型兜底必然漏一个 —— 而漏掉的表现是
    Godot 报类型不符、"整个脚本加载失败"。

    返回实际写进去的变量名(可能因为和检查器参数重名被改名)。
    """
    # ── 已经声明过类型的变量:让"数值 <-> 整数 / 真假"这种**明确**的
    #    转换安静地做掉,不要再报"变量同时被当成 X 和 Y,请改个名字"──
    #    那两个告警以前会把用户指到错的地方:变量名根本没问题,是
    #    『数学_运算』这类节点天生吐 float,而变量声明的是 int / bool。
    #    (2026-10-05 第二十四轮做小游戏示例时踩到的:两条警告都是虚惊。)
    declared = _declared_type(ctx, raw_name)        # 这个变量声明成什么
    gd_type = source_type
    if declared == "bool" and source_type in ("float", "int"):
        gd_type = "bool"                            # 数字 0/1 当真假用
    elif declared == "int" and source_type == "float":
        gd_type = "int"                             # 下面会补 int(...)
    var_name = ctx.declare_var(raw_name, gd_type)
    # ── 布尔变量被喂了数字:转成真假再赋值(不转的话 Godot 报类型不符)──
    # 两种写法都要管:
    #   * 字面量(节点上那个数字框只能填数字)-> `= true / false`;
    #   * 接了线的数值表达式 -> `= (表达式 != 0.0)`。
    # ⚠ 以前只处理了字面量,而且用 `raw_value or 0` —— 接线时 raw_value
    #   是 None,于是**表达式被整个丢掉、生成恒 false**。这条路径
    #   以前没有示例走到过(写布尔变量时大家都用数字框),现在补上:
    #   两种写法都要认。
    if ctx.vars.get(var_name) == "bool":
        if source_type in ("float", "int"):
            if raw_value is None:
                value_expr = f"({value_expr} != 0.0)"
            else:
                try:
                    value_expr = "true" if float(raw_value) != 0 else "false"
                except (TypeError, ValueError):
                    value_expr = "false"
        elif source_type is None and raw_value is None:
            # 线那头的类型**推不出来**(任意值引脚 —— 比如『变量_读取』):
            # 用 bool() 显式转。不转的话 Godot 会因为"往布尔变量里赋
            # 浮点 / 变体"报错或行为不定。bool(数值) 就是"非零为真",
            # 和上面那条规则一个意思。
            value_expr = f"bool({value_expr})"
    # ── 往整数变量里写浮点表达式要**显式转换**(第 22 轮)──────────
    # 『数学_运算』出来的都是 float,而端口/变量声明的可能是 int:
    # 直接写会得到 `var x: int` + `x = (x + 1.0)` —— Godot 报
    # "Cannot assign a value of type float as int",整个脚本加载失败。
    # 蓝图里这种转换是隐式的,这里补上,不用用户自己套『转换_类型』。
    if ctx.vars.get(var_name) == "int" and value_expr is not None:
        text = str(value_expr).strip()
        looks_int = (text.lstrip("-").isdigit()
                     or text.startswith(("int(", "true", "false")))
        if not looks_int:
            value_expr = f"int({text})"
    # ── 变量绑了 setter -> 改成调用它(用户 2026-10-05)────────────
    # 照 UE5:写这个变量时走"写入时调用的函数"。图里照样是『变量_设置』,
    # 生成出来是 `受伤扣血(10.0)` —— 这样"写的时候顺带做别的事"
    # (播音效、界面刷新、夹范围)只写一处。
    # ⚠ 挂载点必须在这里:上面的类型兜底(int() 转换 / bool 转换)已经算完,
    #    传进去的是**最终**那个值表达式。
    impl = _variable_impl(ctx, raw_name)
    setter = (impl or {}).get("setter") or ""
    if setter and _setter_ok(ctx, setter):
        ctx.add_line(indent, f"{safe_function_name(setter)}({value_expr})")
        return var_name
    ctx.add_line(indent, f"{var_name} = {value_expr}")
    return var_name


@multi_output("GKNodeAddVar")
def _expr_add_var_outputs(ctx, node):
    """『变量_增减』的『新值』输出 = 这个变量**本身**。

    ── 为什么直接给变量名(而不是"先算个临时变量")────────────────
    节点的赋值语句刚刚写过它("加完之后"再读),读到的就是新值;
    绑了 setter 的变量也一样 —— setter 最终也是写回同一个变量。
    表达式缓存也是安全的:缓存的是"变量名"这个文本,每次求值读到的
    都是当时的值(不是快照)。
    """
    raw_name = str(getattr(node, "gk_var_name", "") or "").strip()
    if not raw_name:
        return {"新值": "0.0"}
    declared = _declared_type(ctx, raw_name)
    if declared in ("bool", "String"):
        # 语句那边已经报过"只能给数值变量加减",这里不重复刷告警
        return {"新值": "0.0"}
    var_name = ctx.declare_var(raw_name, declared or "float")
    return {"新值": var_name}


@stmt_node("GKNodeAddVar")
def _stmt_add_var(ctx, node, indent):
    """『变量_增减』:给变量加一个数(负数就是减)。

    生成的就是一句 `变量 = int((变量 + 1.0))`(类型兜底见 _assign_var_value);
    和『变量_读取』->『数学_运算』->『变量_设置』那三件套等价,但只有一个节点。
    """
    raw_name = str(getattr(node, "gk_var_name", "") or "").strip()
    if not raw_name:
        ctx.warn("『变量_增减』没有填变量名 —— 这一句跳过。"
                 "去侧栏『变量』框声明一个,再用节点上的放大镜选上")
        return
    declared = _declared_type(ctx, raw_name)
    if declared in ("bool", "String"):
        ctx.warn(f"『变量_增减』只能给**数值**变量加减,而『{raw_name}』是 "
                 f"{declared} 变量 —— 这一句跳过")
        return
    fallback = gd_number(float(getattr(node, "gk_amount", 1.0) or 0.0))
    amount = ctx.input_number(node, "增减", fallback)
    var_name = ctx.declare_var(raw_name, declared or "float")
    value_expr = f"({var_name} + {amount})"
    # ── 夹范围(用户 2026-10-05 第二十六轮)─────────────────────────
    # 扣血扣到 -2、金币加到 99 都是"忘了夹"的典型表现。勾了『限制范围』
    # 就在**加完之后**夹一层:血量 0~3、弹药 0~30 只用勾一下。
    # int 变量的 `int(...)` 由 _assign_var_value 自动补(它认 `clampf(`
    # 不是整数字面量 -> 包一层 int())。
    if bool(getattr(node, "gk_clamp", False)):
        # ⚠ 这里**不能**写 `getattr(...) or 默认值`:'最小/最大' 填 0 是
        # 有意义的(夹成常量 0 也是一种用法),`0.0 or 100.0` 会把它吃掉。
        try:
            low_value = float(getattr(node, "gk_min", 0.0))
            high_value = float(getattr(node, "gk_max", 100.0))
        except (TypeError, ValueError):
            low_value, high_value = 0.0, 100.0
        if low_value > high_value:
            ctx.warn(f"『变量_增减』的『最小』({gd_number(low_value)})比"
                     f"『最大』({gd_number(high_value)})还大 —— 夹完恒等于"
                     f"『最小』,检查一下是不是填反了")
        value_expr = (f"clampf({value_expr}, {gd_number(low_value)}, "
                      f"{gd_number(high_value)})")
    _assign_var_value(ctx, indent, raw_name, value_expr, "float")


def _declared_type(ctx, raw_name):
    """这个变量**已经声明过**的类型(侧栏『变量』框 / 对外端口);没有就 None。

    用途见 `_stmt_set_var`:声明说 int/bool 而节点吐 float 时,直接按声明的
    类型来(顺手做掉转换),而不是报一条"请改个变量名"的虚惊告警。
    """
    name = str(raw_name or "").strip()
    if not name:
        return None
    aliased = getattr(ctx, "_var_aliases", {}).get(name, name)
    return ctx.vars.get(aliased) or ctx.vars.get(name)


def _variable_impl(ctx, var_name):
    """这个变量绑了什么"实现"(getter / setter);没绑返回 None。"""
    tree = getattr(ctx, "tree", None)
    if tree is None:
        return None
    try:
        from .. import properties as props
        return props.variable_implementations(tree).get(str(var_name))
    except Exception:                                     # noqa: BLE001
        return None


def _setter_ok(ctx, name):
    """setter 必须**正好一个参数**(多了少了都是运行时/编译期错误)。"""
    tree = getattr(ctx, "tree", None)
    spec = None
    try:
        from .. import properties as props
        spec = props.tree_functions(tree).get(str(name)) if tree else None
    except Exception:                                     # noqa: BLE001
        spec = None
    if spec is None:
        ctx.warn(f"变量绑定的『写入时调用』函数『{name}』没有在侧栏『函数』框里"
                 f"声明过 —— 这次改回直接赋值")
        return False
    params = list(spec.get("params") or ())
    if len(params) != 1:
        ctx.warn(f"变量绑定的 setter『{name}』需要**正好一个参数**(现在 "
                 f"{len(params)} 个)—— 这次改回直接赋值。"
                 f"去侧栏『函数』框把它的参数改成 1 个(例:value:{spec.get('type') or 'float'})")
        return False
    return True


@stmt_node("GKNodeEmitSignal")
def _stmt_emit_signal(ctx, node, indent):
    name = snake_case(getattr(node, "gk_signal_name", "signal_name"), "signal_name")
    if name not in ctx.signals and getattr(node, "gk_signal_declare", True):
        ctx.signals.append(name)
    ctx.add_line(indent, f"{name}.emit()")


@stmt_node("GKNodeConnect")
def _stmt_connect(ctx, node, indent):
    target = ctx.target_expr(node)
    signal_name = snake_case(getattr(node, "gk_signal_name", "pressed"), "pressed")
    handler = snake_case(getattr(node, "gk_handler", "on_signal"), "on_signal")
    ctx.add_line(indent, f"if not {target}.{signal_name}.is_connected({handler}):")
    ctx.add_line(indent + 1, f"{target}.{signal_name}.connect({handler})")


@stmt_node("GKNodeCallFunction")
def _stmt_call_function(ctx, node, indent):
    func_name = snake_case(getattr(node, "gk_func_name", "my_function"), "my_function")
    raw = getattr(node, "gk_target", TARGET_SELF)
    if raw == TARGET_SELF:
        ctx.add_line(indent, f"{func_name}()")
    else:
        ctx.add_line(indent, f"{ctx.target_expr(node)}.{func_name}()")


@stmt_node("GKNodeBranch")
def _stmt_branch(ctx, node, indent):
    condition = ctx.input_bool_expr(node, "条件")
    ctx.add_line(indent, f"if {condition}:")
    body_indent = indent + 1
    before = len(ctx.sink)
    _chain(ctx, node, body_indent, "是")
    if len(ctx.sink) == before:
        ctx.add_line(body_indent, "pass")
    has_else = any(True for _ in _next_nodes(node, "否"))
    if has_else:
        ctx.add_line(indent, "else:")
        before = len(ctx.sink)
        _chain(ctx, node, body_indent, "否")
        if len(ctx.sink) == before:
            ctx.add_line(body_indent, "pass")


@stmt_node("GKNodeDoOnce")
def _stmt_do_once(ctx, node, indent):
    var_name = ctx.declare_var(f"done_{node.name}", "bool")
    ctx.add_line(indent, f"if not {var_name}:")
    ctx.add_line(indent + 1, f"{var_name} = true")
    before = len(ctx.sink)
    _chain(ctx, node, indent + 1, "首次")
    if len(ctx.sink) == before:
        ctx.add_line(indent + 1, "pass")


@stmt_node("GKNodeCooldown")
def _stmt_cooldown(ctx, node, indent):
    """『流程_冷却』:隔 N 秒才放行一次(冷却门 / 无敌帧)。

    ── 生成长这样 ──────────────────────────────────────────────────
        if Time.get_ticks_msec() - _gk_cd_ticks >= int((1.0) * 1000.0):
            _gk_cd_ticks = Time.get_ticks_msec()
            <『可以』那条链>
        else:
            <『还在冷却』那条链>

    ── 为什么用毫秒时间戳,而不是累加 delta ─────────────────────────
    触发是"事件来的时候"判的(body_entered / 按键),不是每帧都跑 ——
    累加 delta 需要额外的每帧节点,还得自己处理"没在跑的时候算不算"。
    `Time.get_ticks_msec()` 是引擎的单调时钟,随时读都准。
    初值 -999999 = 第一次一定放行(游戏刚开始时钟才几十毫秒)。
    """
    seconds = ctx.input_number(node, "秒数", "1.0")
    # 每个节点一份状态(见 ctx.node_tag):图里放两个冷却门时互不影响
    last = ctx.declare_var(f"_gk_cd_ticks{ctx.node_tag(node, 'cd')}", "int",
                           initial="-999999")
    # ── 『每几次放行一次』(用户 2026-10-05 第二十六轮)───────────────
    # ⚠ 只有 >1 才生成计数器:默认 1 的老图产物**逐字不变**(老的
    # Godot 脚本、老的探针断言都不受影响)。
    try:
        every = int(getattr(node, "gk_every", 1))
    except (TypeError, ValueError):
        every = 1
    every = max(1, min(100, every))
    condition = f"Time.get_ticks_msec() - {last} >= int(({seconds}) * 1000.0)"
    if every > 1:
        count = ctx.declare_var(f"_gk_cd_count{ctx.node_tag(node, 'cd')}", "int",
                                initial="0")
        # 每次到达先计数,再拿"第偶数次"进条件 —— 和秒数两个条件**同时**
        # 满足才放行,所以"每 2 次 + 冷却 0.5 秒"= 两次里挑一次且不超频
        ctx.add_line(indent, f"{count} += 1")
        condition += f" and {count} % {every} == 0"
    ctx.add_line(indent, f"if {condition}:")
    ctx.add_line(indent + 1, f"{last} = Time.get_ticks_msec()")
    before = len(ctx.sink)
    _chain(ctx, node, indent + 1, "可以")
    if len(ctx.sink) == before:
        ctx.add_line(indent + 1, "pass")
    # 没人接『还在冷却』就不画 else(GDScript 的空 else 要补 pass,没意义)
    if not any(True for _ in _next_nodes(node, "还在冷却")):
        return
    ctx.add_line(indent, "else:")
    before = len(ctx.sink)
    _chain(ctx, node, indent + 1, "还在冷却")
    if len(ctx.sink) == before:
        ctx.add_line(indent + 1, "pass")


@stmt_node("GKNodeSequence")
def _stmt_sequence(ctx, node, indent):
    for socket in node.outputs:
        _chain(ctx, node, indent, socket.name)


@stmt_node("GKNodeSwitch")
def _stmt_switch(ctx, node, indent):
    """多路分支:按一个整数走不同出口。

    生成的 GDScript 用 `match`:

        var _sw1 := 2
        match _sw1:
            0:
                <出口 0 的链>
            1:
                <出口 1 的链>
            _:
                <默认的链>

    ── 两个容易出错的地方 ──────────────────────────────────────────
    1. `match` 的每个分支体**不能为空** —— 没连线时要补 `pass`,
       否则 GDScript 直接语法错误。
    2. 出口名就是匹配的整数值,最后一条固定叫『默认』(生成成 `_`)。
       所以这里必须把"默认"和数字分开处理,不能一视同仁地 int()。
    """
    value = ctx.input_number(node, "值", "0")
    temp = ctx.new_temp("sw")
    ctx.add_line(indent, f"var {temp} := int({value})")
    ctx.add_line(indent, f"match {temp}:")

    # 出口顺序就是 build_sockets 建的顺序(0..n-1,然后"默认")
    default_socket = None
    branches = []
    for socket in node.outputs:
        if socket.name == "默认":
            default_socket = socket
        else:
            branches.append(socket)

    for socket in branches:
        try:
            case_value = int(socket.name)
        except (TypeError, ValueError):
            continue
        ctx.add_line(indent + 1, f"{case_value}:")
        before = len(ctx.sink)
        _chain(ctx, node, indent + 2, socket.name)
        if len(ctx.sink) == before:
            ctx.add_line(indent + 2, "pass")

    if default_socket is not None:
        ctx.add_line(indent + 1, "_:")
        before = len(ctx.sink)
        _chain(ctx, node, indent + 2, "默认")
        if len(ctx.sink) == before:
            ctx.add_line(indent + 2, "pass")
    elif not branches:
        # 一个出口都没有(理论上不会,防御一下)
        ctx.add_line(indent + 1, "_:")
        ctx.add_line(indent + 2, "pass")


@stmt_node("GKNodeForLoop")
def _stmt_for_loop(ctx, node, indent):
    count = ctx.input_number(node, "次数", "3")
    index_name = snake_case(getattr(node, "gk_index_name", "i"), "i")
    ctx.add_line(indent, f"for {index_name} in int({count}):")
    before = len(ctx.sink)
    _chain(ctx, node, indent + 1, "循环体")
    if len(ctx.sink) == before:
        ctx.add_line(indent + 1, "pass")
    _chain(ctx, node, indent, "结束后")


@stmt_node("GKNodeWait")
def _stmt_wait(ctx, node, indent):
    """等待 N 秒(await),然后继续往下走。

    ── 这里**不能**自己再 `_chain` 一次(踩过的坑)──────────────────
    `emit_statement()` 在调用完处理器之后**已经**会沿『完成』往下走了。
    处理器里再 `_chain(ctx, node, indent)` 就会把下游整段代码生成**两遍**:

        等待_秒 -> 变量_设置(is_hurt = false)
        生成结果:
            await get_tree().create_timer(0.6).timeout
            is_hurt = false
            is_hurt = false      <- 多出来的一份

    接在等待后面的要是『音效_播放』『实例_生成』这种有副作用的节点,
    就是**真的执行两次**(放两个音效、生成两个实例),不只是难看。
    `_active` 那个断环保护挡不住它 —— 第二次进来时第一次已经出栈了。

    也不需要担心"等待之后没往下走":走不走由 `emit_statement` 统一负责。
    """
    seconds = ctx.input_number(node, "秒", "1.0")
    use_process = bool(getattr(node, "gk_use_process", False))
    if use_process:
        ctx.add_line(indent, f"await get_tree().create_timer({seconds}, true, false, true).timeout")
    else:
        ctx.add_line(indent, f"await get_tree().create_timer({seconds}).timeout")
