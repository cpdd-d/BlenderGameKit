"""相机套件:切换、机位、弹簧臂、路径、缩放、抖动、跟随。

从原 `codegen/core.py` 里独立成一块,避免与运动/动画逻辑相互干扰。"""

from .. import curve_assets
from ..nodes.node_base import TARGET_SELF, gd_node_path, gd_string, snake_case

from . import easing
from .literals import gd_number
from .registry import multi_output, stmt_node
from .statements import _tunable_number





def _path_name(path):
    """从生成的节点引用表达式里取出**节点名**。

    `$"侧视机位"` / `$Camera` -> `视视机位` / `Camera`;
    不是这种形式的(比如 `self`)原样返回。
    """
    text = str(path or "").strip()
    if text.startswith("$"):
        text = text[1:].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("\"", "'"):
        text = text[1:-1]
    return text

def _camera_expr(ctx, node, auto="first"):
    """求值相机目标。

    三种情况:
      * 图里指定了某个节点 -> 用它的 @onready 变量
      * 图里两个相机节点 -> 按路径引用
      * 都没填 -> 不报警、不生成 @onready，而是在运行时**自动找第一个相机**。
        对新手来说"我什么都没填但震动生效了"比"必须先在面板里选相机"友好得多。
    """
    raw = (getattr(node, "gk_target", "") or "").strip()
    if raw and raw != TARGET_SELF:
        return ctx.target_expr(node, hint="cam"), True
    if auto == "first":
        # ── "没填相机"就找**正在用的那台**(2.5D 示例真跑抓出来的坑)──────
        # 顺序:①节点上 if 指定的 ②视口里正在用的 ③gk_camera 分组
        #      ④场景里第一台
        #
        # ── 为什么走**函数**而不是嵌套三元(第 28 轮,Godot 调试器实证)──
        # 以前生成的是一行嵌套三元:
        #     _gk_cam_auto if _gk_cam_auto != null else
        #       (get_camera_3d() if ... else (get_camera_2d() if ... else
        #         get_first_node_in_group("gk_camera")))
        # 三个分支的类型是 Camera3D / Camera2D / Node —— Godot 报
        #     INCOMPATIBLE_TERNARY: Values of the ternary operator are not
        #     mutually compatible
        # (实测一次报 4 条,分属 130/134/138/141 行),而且那一行**算不出
        # 可用相机** -> 弹簧臂每帧都走"没找到相机"分支 -> 完全不生效。
        # 现在统一成 `_gk_resolve_camera(self)`,内部全是普通 if,返回 Node。
        ctx.need_resolve_camera = True
        ctx.note(f"『{node.bl_label}』没指定相机 —— 运行时自动用当前相机"
                 f"(单相机项目这样就行;想固定某台就在节点上选)")
        return "_gk_resolve_camera(self)", True
def _camera_node_expr(ctx, node, prop="gk_target", hint="相机"):
    """取"这台相机"的表达式(留空 = 运行时找当前相机)。

    ── 为什么区分"指定相机"和"当前相机"(照虚幻的习惯)──────────────
    虚幻里 Activate 是作用在**具体某个组件**上的,而"当前在用哪台"
    则是 ViewTarget。我们两种都给:填了就用填的(@onready 引用),
    留空就用 `get_viewport().get_camera_3d()/2d()` —— 单相机项目
    不用填任何东西就能用。
    """
    raw = str(getattr(node, prop, "") or "").strip()
    if raw and raw != TARGET_SELF:
        return ctx.target_expr(node, prop, hint)
    # ── 同样走**类型统一的函数**,不要嵌套三元(第 28 轮)───────────
    # 嵌套三元里的三个分支是 Camera3D / Camera2D / Node,
    # Godot 报 `INCOMPATIBLE_TERNARY: Values of the ternary operator are
    # not mutually compatible`(用户调试器里 4 条,130/134/138/141 行),
    # 而且那个表达式**算不出可用相机** -> 弹簧臂每帧"没找到相机"。
    ctx.need_resolve_camera = True
    ctx.note(f"『{node.bl_label}』没指定相机 —— 运行时自动用当前相机")
    return "_gk_resolve_camera(self)"


@stmt_node("GKNodeCameraActive")
def _stmt_camera_active(ctx, node, indent):
    """启用 / 禁用相机(照虚幻的 Activate / Deactivate)。"""
    camera = _camera_node_expr(ctx, node)
    temp = ctx.new_temp("cam")
    ctx.add_line(indent, f"var {temp} = {camera}")
    ctx.add_line(indent, f"if {temp} == null:")
    ctx.add_line(indent + 1, 'push_warning("[GK] 相机_启用:没找到相机")')
    if getattr(node, "gk_active", True):
        ctx.add_line(indent, f"else:")
        ctx.add_line(indent + 1, f"{temp}.make_current()")
    else:
        # 禁用 = 正开着才摘下来(不是它开着的话什么都不做,免得把别人的画面弄黑)
        ctx.add_line(indent, f"elif {temp} is Camera3D and "
                             f"({temp} as Camera3D).is_current():")
        ctx.add_line(indent + 1, f"({temp} as Camera3D).clear_current()")
        ctx.add_line(indent, f"elif {temp} is Camera2D and "
                             f"({temp} as Camera2D).is_current():")
        ctx.add_line(indent + 1, f"({temp} as Camera2D).clear_current()")


@stmt_node("GKNodeCameraSetup")
def _stmt_camera_setup(ctx, node, indent):
    """一次设好:视野 + 视距(+ 平滑)。

    ── 0 = **不动它**(用户 2026-10-03 真跑踩出来的坑)────────────────
    文档里写的就是"0 = 不动它",但代码是**无条件**写进去的 ——
    用户在节点上没填视野(默认 0),于是生成 `camera.fov = 0`:
    Godot 里 fov=0 等于镜头废掉,**整个画面就废了**,
    表现是"视角完全不对/看不到东西"。
    现在:没填(0)且没连线的项直接不生成,并且在导出报告里说清楚
    这次到底改了什么。

    平滑走 `create_tween()` —— 和『定时_缓动属性』同一个机制。
    """
    camera = _camera_node_expr(ctx, node)
    fov_socket = ctx._socket(node, "视野")
    dist_socket = ctx._socket(node, "视距")
    fov_linked = bool(fov_socket is not None
                      and getattr(fov_socket, "is_linked", False))
    dist_linked = bool(dist_socket is not None
                       and getattr(dist_socket, "is_linked", False))
    fov_value = float(getattr(node, "gk_fov", 0.0) or 0.0)
    dist_value = float(getattr(node, "gk_distance", 0.0) or 0.0)
    want_fov = fov_linked or abs(fov_value) > 0.0001
    want_dist = dist_linked or abs(dist_value) > 0.0001
    smooth = float(getattr(node, "gk_smooth", 0.0) or 0.0)
    if not want_fov and not want_dist:
        ctx.warn("『相机_设置』什么都没设(视野 0、视距 0)—— 它不会改任何"
                 "东西。想改视野就填一个角度(比如 55),想推远就填视距;"
                 "两个都不填的话,这个节点可以删掉")
        return
    temp = ctx.new_temp("cam")
    ctx.add_line(indent, f"var {temp} = {camera}")
    ctx.add_line(indent, f"if {temp} == null:")
    ctx.add_line(indent + 1, 'push_warning("[GK] 相机_设置:没找到相机")')
    ctx.add_line(indent, f"else:")
    body = indent + 1
    setter = None
    if smooth > 0.0:
        tween = ctx.new_temp("tween")
        ctx.add_line(body, f"var {tween} = create_tween()")
        ctx.add_line(body, f"{tween}.set_trans(Tween.TRANS_SINE)")
        ctx.add_line(body, f"{tween}.set_ease(Tween.EASE_IN_OUT)")
        setter = f"{tween}.tween_property"
    if want_fov:
        fov = ctx.input_number(node, "视野", "0.0")
        ctx.add_line(body, f"if {temp} is Camera2D:")
        if setter:
            ctx.add_line(body + 1,
                         f"{setter}({temp}, \"zoom\", Vector2({fov}, {fov}), "
                         f"{gd_number(smooth)})")
        else:
            ctx.add_line(body + 1, f"{temp}.zoom = Vector2({fov}, {fov})")
        ctx.add_line(body, f"elif {temp} is Camera3D:")
        if setter:
            ctx.add_line(body + 1,
                         f"{setter}({temp}, \"fov\", {fov}, "
                         f"{gd_number(smooth)})")
        else:
            ctx.add_line(body + 1, f"{temp}.fov = {fov}")
    if want_dist:
        # 视距:沿**相机自己的朝向**推远(负 = 拉近)。
        # 用 global_transform 而不是 position,父节点有旋转也照样对。
        distance = ctx.input_number(node, "视距", "0.0")
        ctx.add_line(body, f"if {temp} is Node3D:")
        ctx.add_line(body + 1, f"var _gk_back := "
                               f"({temp} as Node3D).global_transform.basis.z")
        if setter:
            ctx.add_line(body + 1,
                         f"{setter}({temp}, \"global_position\", "
                         f"({temp} as Node3D).global_position + _gk_back * "
                         f"{distance}, {gd_number(smooth)})")
        else:
            ctx.add_line(body + 1, f"({temp} as Node3D).global_position += "
                                   f"_gk_back * {distance}")


@stmt_node("GKNodeCameraPosition")
def _stmt_camera_position(ctx, node, indent):
    """设位置:向量优先,没接就用 X/Y/Z 三个浮点。"""
    camera = _camera_node_expr(ctx, node)
    socket = ctx._socket(node, "位置")
    if socket is not None and getattr(socket, "is_linked", False):
        target = ctx.expr_of(socket)
    else:
        x = ctx.input_number(node, "X", "0.0")
        y = ctx.input_number(node, "Y", "0.0")
        z = ctx.input_number(node, "Z", "0.0")
        target = f"Vector3({x}, {y}, {z})"
    mode = str(getattr(node, "gk_mode", "ADD"))
    smooth = float(getattr(node, "gk_smooth", 0.0) or 0.0)
    temp = ctx.new_temp("cam")
    ctx.add_line(indent, f"var {temp} = {camera}")
    ctx.add_line(indent, f"if {temp} is Node3D:")
    if mode == "SET":
        expr = target
    else:
        expr = f"({temp} as Node3D).global_position + ({target})"
    if smooth > 0.0:
        tween = ctx.new_temp("tween")
        ctx.add_line(indent + 1, f"var {tween} = create_tween()")
        ctx.add_line(indent + 1, f"{tween}.set_trans(Tween.TRANS_SINE)")
        ctx.add_line(indent + 1, f"{tween}.tween_property({temp}, "
                                 f"\"global_position\", {expr}, "
                                 f"{gd_number(smooth)})")
    else:
        ctx.add_line(indent + 1, f"({temp} as Node3D).global_position = {expr}")


@stmt_node("GKNodeCameraRotation")
def _stmt_camera_rotation(ctx, node, indent):
    """设朝向:向量优先(单位度),没接就用偏航/俯仰/翻滚。"""
    camera = _camera_node_expr(ctx, node)
    yaw = ctx.input_number(node, "偏航", "0.0")
    pitch = ctx.input_number(node, "俯仰", "0.0")
    roll = ctx.input_number(node, "翻滚", "0.0")
    # ── 反转开关(用户要求:默认跟随鼠标,但要能自定义方向)──────────
    # 在**生成时**给表达式套个负号:填了常量就直接取负,是表达式就
    # `-(expr)` —— 生成的代码仍然是一句干净的话。
    def flip(expr, invert):
        if not invert or expr in ("0.0", "0"):
            return expr
        if expr.startswith("-"):
            return expr[1:]
        return f"-({expr})"

    yaw = flip(yaw, bool(getattr(node, "gk_invert_x", False)))
    pitch = flip(pitch, bool(getattr(node, "gk_invert_y", False)))
    target = f"Vector3({pitch}, {yaw}, {roll})"
    mode = str(getattr(node, "gk_mode", "ADD"))
    smooth = float(getattr(node, "gk_smooth", 0.0) or 0.0)
    clamp = bool(getattr(node, "gk_clamp_pitch", True))
    temp = ctx.new_temp("cam")
    base = f"({temp} as Node3D).rotation_degrees"
    ctx.add_line(indent, f"var {temp} = {camera}")
    # ── 手动转视角要**打断**正在进行的相机过渡(用户:"视角会卡住")──────
    # 相机切换的过渡是每帧把位姿插值覆盖回去的;用户这时候转视角,
    # 刚转过去下一帧就被覆盖 -> 表现就是"转了没效果 / 视角卡住"。
    # 手动操作 = 打断过渡,并且把"家"更新成用户现在摆的位置。
    left = ctx.declare_var("_gk_cam_blend_left", "float")
    homes = ctx.declare_var("_gk_cam_homes", "Dictionary")
    ctx.add_line(indent, f"if {temp} != null and {left} > 0.0:")
    ctx.add_line(indent + 1, f"{left} = 0.0")
    ctx.add_line(indent + 1, f"if {temp} is Node3D:")
    ctx.add_line(indent + 2, f"{homes}[{temp}] = "
                             f"({temp} as Node3D).global_transform")
    ctx.add_line(indent, f"if {temp} is Node3D:")
    body = indent + 1
    if mode == "SET":
        ctx.add_line(body, f"var _gk_rot: Vector3 = {target}")
    else:
        ctx.add_line(body, f"var _gk_rot: Vector3 = {base} + ({target})")
    if clamp:
        # 俯仰限制在 ±89°:第一人称转视角不加这个会翻过头(画面上下颠倒)
        ctx.add_line(body, "if _gk_rot.x > 89.0:")
        ctx.add_line(body + 1, "_gk_rot.x = 89.0")
        ctx.add_line(body, "elif _gk_rot.x < -89.0:")
        ctx.add_line(body + 1, "_gk_rot.x = -89.0")
    if smooth > 0.0:
        tween = ctx.new_temp("tween")
        ctx.add_line(body, f"var {tween} = create_tween()")
        ctx.add_line(body, f"{tween}.set_trans(Tween.TRANS_SINE)")
        ctx.add_line(body, f"{tween}.tween_property({temp}, "
                           f"\"rotation_degrees\", _gk_rot, "
                           f"{gd_number(smooth)})")
    else:
        ctx.add_line(body, f"({temp} as Node3D).rotation_degrees = _gk_rot")


def _ease_factor(ctx, node, raw, prop="gk_lag_trans", curve_prop="gk_lag_curve"):
    """把"线性比例"套上节点上选的缓动档位。

    ── 为什么线性档**不加壳**(整个项目的约定:老图产物一字不变)──────
    `_gk_ease(raw, TRANS_LINEAR, EASE_IN_OUT)` 数学上等于 raw,但会让
    所有老图生成的代码多一层调用(测试和用户对照都要求"没改设置 = 产物
    一模一样")。所以线性档直接返回原表达式。
    """
    preset = str(getattr(node, prop, "LINEAR") or "LINEAR")
    if preset == "LINEAR":
        return raw
    return easing.apply_ease(ctx, node, raw, prop, curve_prop)


def _spring_tag(ctx, node):
    """给『相机_弹簧臂』的成员变量算一个**按节点唯一**的后缀。

    ── 为什么需要(用户 2026-10-04:"俯视图……动不了")──────────────────
    多根杆子各自管一台相机(侧视 / 俯瞰)时,成员变量名原来**不带后缀** ——
    `_gk_spring_pos` / `_gk_spring_ready` 是全局共享的,两根杆子每帧轮流
    写同一个变量:第一根把相机摆好,第二根接着在**第一根的结果**上继续
    lerp —— 两台相机都被拖成一个中间值。表现就是"镜头乱飘 / 切到俯瞰
    没反应"。按节点加后缀后各管各的状态。第一根杆子拿到空后缀(名字不变),
    第二根起 `_2` / `_3`。
    """
    tags = getattr(ctx, "_gk_spring_tags", None)
    if tags is None:
        tags = {}
        ctx._gk_spring_tags = tags
    if node not in tags:
        tags[node] = "" if not tags else f"_{len(tags) + 1}"
    return tags[node]


@stmt_node("GKNodeCameraSpringArm")
def _stmt_camera_spring_arm(ctx, node, indent):
    """**弹簧臂**(虚幻的 SpringArmComponent)。

    ── 每物理帧做五件事 ───────────────────────────────────────────
    1. 算出"枢轴"(挂在跟随对象身上的支点)
    2. 从枢轴沿杆子方向量出"想到的位置"
    3. 打一条射线,撞到东西就把杆子缩短(这就是不穿墙的全部秘密)
    4. 平滑(位置 / 朝向各自可调)
    5. 把相机摆过去,并让它看向枢轴

    ── 为什么挂在 _physics_process(而不是 _process)────────────────
    和角色移动同一个时钟。一个在物理帧跑、一个在渲染帧跑,两个节拍一差
    画面就抖(相机跟随当年就是踩了这个,见 physics_hooks 的说明)。
    """
    follow = ctx.target_expr(node, "gk_target", "跟随")
    camera = _camera_node_expr(ctx, node, "gk_camera")
    # 这根杆子的成员变量后缀(多根杆子各管一台相机,状态不能共享)
    tag = _spring_tag(ctx, node)
    # 相机过渡用的变量(过渡期间弹簧臂不抢相机 —— 用户第 25 轮:
    # "按视角切换会闪一下":过渡每帧插值写位姿,弹簧臂也每帧写,
    # 相机就在两个位置之间来回跳。谁在过渡谁说了算。)
    # 必须在函数**最外层**声明:写在某个 if 分支里的话,别处引用会
    # UnboundLocalError(踩过)。
    blend_left = ctx.declare_var("_gk_cam_blend_left", "float")
    # ── 杆子挂在哪:接了引脚就用引脚,否则用『弹簧臂』属性,再否则跟随对象 ──
    # 用户 2026-10-03:"应该还要加设置哪个为弹簧臂引脚" —— 想让杆子挂在
    # 肩膀 / 摄像机架子 / 空节点上时,填这个(或接线)就行。
    arm_socket = ctx._socket(node, "弹簧臂")
    if arm_socket is not None and getattr(arm_socket, "is_linked", False):
        arm_node = ctx.expr_of(arm_socket)
    else:
        raw_arm = str(getattr(node, "gk_arm_node", "") or "").strip()
        arm_node = (ctx.target_expr(node, "gk_arm_node", "弹簧臂")
                    if raw_arm and raw_arm != TARGET_SELF else follow)
    # ── 运行中可改的三个量:引脚 > 属性 ────────────────────────────
    arm = ctx.input_number(node, "臂长", gd_number(
        float(getattr(node, "gk_arm", 4.0) or 0.0)))
    yaw_in = ctx.input_number(node, "偏航", gd_number(
        float(getattr(node, "gk_yaw", 0.0) or 0.0)))
    pitch = ctx.input_number(node, "俯仰", gd_number(
        float(getattr(node, "gk_pitch", 15.0) or 0.0)))
    off_socket = ctx._socket(node, "偏移")
    height = float(getattr(node, "gk_height", 1.5) or 0.0)
    side = float(getattr(node, "gk_side", 0.0) or 0.0)
    if off_socket is not None and getattr(off_socket, "is_linked", False):
        offset_expr = ctx.expr_of(off_socket)
    else:
        offset_expr = f"Vector3(0.0, {gd_number(height)}, {gd_number(side)})"
    yaw_offset = float(getattr(node, "gk_yaw_offset", 0.0) or 0.0)
    collision = bool(getattr(node, "gk_collision", True))
    radius = float(getattr(node, "gk_radius", 0.3) or 0.0)
    # ── 最短臂长(第 126 轮)─────────────────────────────────────────
    # 撞得再近,杆子也不会短过这个数 —— 兜底,别让相机贴到枢轴上。
    #
    # ── 为什么默认还是 0.2(试过调大,量出来是错的)──────────────────
    # 直觉是"调大到角色半径 0.5 + 近平面 0.1 = 0.6,相机就进不了身体"。
    # 真机 A/B 打脸:0.6 会把**贴墙静止**时相机离墙的余量从 0.3500 压到
    # **0.0475**(< near 0.1)—— 角色的穿模换成了**墙**被近平面切,
    # 画面更糟(相机都快贴到墙皮上了)。所以默认保持 0.2:没改这个值的老图
    # 生成结果**逐字不变**。
    #
    # 那 0.6 什么时候有用:角色**原点在脚底**时(枢轴 1.4 米 = 胸口),
    # 贴墙静止时相机离枢轴只有 0.2975 米 —— 那已经在胸腔里了,而示例里
    # 之所以没事,纯粹因为它的模型以身体中心为原点、枢轴比头顶还高 0.5 米。
    # 这种工程把这一项调到 0.6 左右能救;代价是墙比它近时会切墙,见属性说明。
    min_arm = float(getattr(node, "gk_min_arm", 0.2) or 0.0)
    # 第一人称"眼睛"要在碰撞体上沿**往里留**多少(米)。见下面夹取那段的说明:
    # 正好夹到上沿时,胶囊顶和眼睛一起贴天花板,真机仍有 7 帧相机在实心体里。
    # 留一点(比相机 near 0.1 略大)让胶囊顶先顶住。
    eye_margin = 0.15
    lag = float(getattr(node, "gk_lag", 8.0) or 0.0)
    lag_rot = float(getattr(node, "gk_lag_rot", 0.0) or 0.0)
    enabled = bool(getattr(node, "gk_enabled", True))
    # A camera with a very short arm and no pitch/lag is the convention used
    # by the story demo for first-person view.  In that mode movement must not
    # become an accidental yaw input: CharacterBody rotation can change while
    # sliding against geometry, so capture the arm yaw once and let only the
    # explicit mouse-yaw value update the view.
    first_person_lock = (abs(float(getattr(node, "gk_arm", 4.0) or 0.0)) <= 0.5
                         and abs(float(getattr(node, "gk_pitch", 15.0) or 0.0)) <= 0.001
                         and lag <= 0.0)
    if enabled and str(arm) in ("0.0", "0"):
        # 臂长填 0 = 杆子收到底,相机贴在枢轴上 —— 那是"第一人称"的做法,
        # 但多数人填 0 是想"先不生效"。这里给一句提醒,免得画面变成贴脸。
        ctx.warn("『相机_弹簧臂』的臂长是 0 —— 相机会贴在枢轴上(第一人称"
                 "效果)。想暂时关掉这条杆子,请把『启用』的勾去掉")

    cam_var = ctx.declare_var(f"_gk_spring_cam{tag}", "Node")
    # 绑定写在执行点(通常是 _ready);每帧逻辑进 physics_hooks
    ctx.add_line(indent, f"{cam_var} = {camera}")
    ctx.add_line(indent, f"if {cam_var} == null:")
    ctx.add_line(indent + 1, 'push_warning("[GK] 相机_弹簧臂:没找到相机")')
    if not enabled:
        # 关掉 = 连每帧用的成员变量都不声明,更不注册逻辑
        # (不是"注册了再判断"—— 那样白跑一辈子,还占着名字)
        return
    pos_var = ctx.declare_var(f"_gk_spring_pos{tag}", "Vector3")
    ready_var = ctx.declare_var(f"_gk_spring_ready{tag}", "bool", initial="false")
    # 右侧输出的三个量(枢轴 / 方向 / 相机位置)——
    # 存成员变量,所以**别的地方**随时读都是最新值(不是只在执行点算一次)
    pivot_var = ctx.declare_var(f"_gk_spring_pivot{tag}", "Vector3")
    dir_var = ctx.declare_var(f"_gk_spring_dir{tag}", "Vector3")
    base_yaw_var = None
    base_yaw_ready_var = None
    if first_person_lock:
        base_yaw_var = ctx.declare_var(f"_gk_spring_base_yaw{tag}", "float")
        base_yaw_ready_var = ctx.declare_var(
            f"_gk_spring_base_yaw_ready{tag}", "bool", initial="false")

    follow3d = f"({follow} as Node3D)"
    arm3d = f"({arm_node} as Node3D)"
    # ── ★ 枢轴不许跑到角色的碰撞体**外面**(第 129 轮)────────────────────
    # ── 用户原话 + 真机量出来的链条 ──────────────────────────────────────
    # 用户:"2.还有第一人称还会触发穿模（幅度变小了）。"
    # 真机(第三关方块顶跳,113 帧,`.`audit/camclip`):
    #     第一人称枢轴 = 玩家原点 + **1.55**,而玩家胶囊是
    #     `CapsuleShape3D(r=0.5, h=1.8)` -> 沿 y 只到 **+0.9**。
    #     于是"眼睛"恒定落在**头顶上方 0.65 米处**(1.55 - 0.9):
    #        相机高出胶囊顶 = **+0.6500 恒定**
    #        相机→最近表面 = **-1.0 ~ 0.5497**(负数 = 已经在实心体里)
    #        比 near(0.1) 更近 = **5 帧**,最坏 **0.0066 米**
    #        相机在实心体里的帧 = **10 帧**(撞 `结局天花板`)
    #     而玩家胶囊中心到最近表面始终 0.9002~1.4725 ——
    #     **身体一直在外面,只有眼睛伸出去了**。
    #
    # 为什么 `1.55` 会是错的:`1.55` 是**按"角色原点在脚底"写的**(1.8 米的人,
    # 眼睛离地 1.55 = 标准写法)。可这个示例的角色原点在**身体中心**,
    # 于是同一串数字把眼睛顶到离地 2.45 米 —— 水平方向还被胶囊挡着(相机
    # 只在轴线外 0.25 米,胶囊半径 0.5),**竖直方向完全裸奔**,顶天花板就穿。
    #
    # 修法:夹一次,不让枢轴超出跟随对象碰撞形状的**半高**。
    #   * 原点在**中心** -> 半高约 0.9,1.55 被夹到 0.9 以内 -> 眼睛回到身体里;
    #   * 原点在**脚底** -> 碰撞形状的中心在 +0.9,上沿在 +1.8,1.55 合法不动。
    # 也就是说**同一个属性值两种原点都对**,不用让用户去猜该填多少。
    # ⚠ 只在**第一人称**夹(第三人称的 1.4 是要"举过头顶看",必须允许超出),
    #   而且只在拿得到碰撞形状时才夹(拿不到就保持原值,老工程行为不变)。
    # ⚠ 夹的是"沿**角色自身竖直轴**的高度",不是世界 y —— 角色躺着也成立。
    clamp_lines = []
    if first_person_lock:
        # 让 generator 把 `_gk_body_half_up()` 写出来(只声明需要,不重复生成)
        ctx.need_pivot_clamp = True
        # ── 为什么要留 0.15 的余量(真机量出来的,别改成 0)────────────────
        # 第一版夹到**正好等于胶囊上沿**,真机复跑仍然有 **7 帧**相机在实心体里、
        # 水平离表面 **0.0001 米**:站在方块上跳时胶囊顶(0.9)会顶到天花板底面,
        # 而眼睛正好也在 0.9 —— **眼睛和胶囊顶一起贴到天花板上了**。
        # 留 0.15(比相机 near 0.1 略大)之后眼睛落在胶囊内更低处,
        # 天花板由**胶囊顶**先顶上,眼睛够不着。
        # 0.15 这个数是"near + 一点",不是随手填的:比 near 小就等于没留。
        clamp_lines = [
            f"    var _gk_half_up := _gk_body_half_up({follow3d})",
            "    if _gk_half_up > 0.0:",
            f"        var _gk_axis: Vector3 = {arm3d}.global_transform.basis.y",
            "        if _gk_axis.length_squared() > 1e-8:",
            "            _gk_axis = _gk_axis.normalized()",
            f"            var _gk_up_off: float = (_gk_pivot "
            f"- {follow3d}.global_position).dot(_gk_axis)",
            "            var _gk_limit: float = _gk_half_up "
            f"- {gd_number(eye_margin)}",
            "            if _gk_limit < 0.0:",
            "                _gk_limit = 0.0",
            "            if _gk_up_off > _gk_limit:",
            "                _gk_pivot -= _gk_axis * (_gk_up_off - _gk_limit)",
        ]
    lines = [
        f"if {cam_var} != null and {arm_node} is Node3D:",
        # 枢轴 = 杆子挂点的位置 + 偏移向量(偏移可以接线,运行中改)
        f"    var _gk_pivot: Vector3 = {arm3d}.global_position "
        f"+ ({offset_expr})",
        # ── 偏航 = 挂点自己的朝向 **减去** 节点上的偏移与『偏航』引脚。
        # ── 为什么是"减"(用户 2026-10-04:"视角旋转逻辑感觉颠倒了")──────
        # Godot 里 rotation.y 正向 = 从上看**逆时针 = 左转**;鼠标右移给出的
        # 是正值,直接加就成了"鼠标往右、视角往左"。取负之后:鼠标右移 ->
        # 杆子绕到角色左后方 -> 视线右转,和直觉一致。挂点自己的旋转
        # (rotation.y)保持原样,杆子才始终待在角色**正后方**。
    ] + clamp_lines
    if first_person_lock:
        lines += [
            f"    if not {base_yaw_ready_var}:",
            f"        {base_yaw_var} = {arm3d}.rotation.y",
            f"        {base_yaw_ready_var} = true",
            f"    var _gk_yaw: float = {base_yaw_var} "
            f"- deg_to_rad({gd_number(yaw_offset)} + ({yaw_in}))",
        ]
    else:
        lines += [
            f"    var _gk_yaw: float = {arm3d}.rotation.y "
            f"- deg_to_rad({gd_number(yaw_offset)} + ({yaw_in}))",
        ]
    lines += [
        # 杆子的方向 = 先按俯仰抬起来、再按偏航绕一圈。
        # 从 Basis 取 .z 是因为 Godot 的相机朝 -Z 看 —— 杆子往 +Z 退,
        # 相机正好回头看向枢轴,不用再手动翻 180°。
        # ── 俯仰也要取负(同上,"颠倒"的另一半)────────────────────────
        # `Basis.from_euler(Vector3(p, y, 0))` 的 .z 是
        # (sinY·cosP, -sinP, cosY·cosP) —— 填正值相机跑到枢轴**下方**
        # (俯仰 12° 相机比枢轴还低,填到 70° 直接钻到地下)。取负之后
        # 0 = 平视、正数 = 抬高俯视,和面板上"俯仰夹在 -20~70"、70 = 高空
        # 俯视的用法对得上。
        "    var _gk_arm_basis := Basis.from_euler(Vector3("
        f"-deg_to_rad({pitch}), _gk_yaw, 0.0))",
        "    var _gk_dir: Vector3 = _gk_arm_basis.z",
        f"    var _gk_want: Vector3 = _gk_pivot + _gk_dir * ({arm})",
        # 右侧输出的三个量:每帧更新,别处随时读都是新的
        f"    {pivot_var} = _gk_pivot",
        f"    {dir_var} = _gk_dir",
    ]
    if collision:
        # 射线从枢轴打到"想到的位置";撞到就把相机拉到撞点**前面**一点。
        # 排除跟随对象和相机自己 —— 不排除的话杆子会被"自己"顶住,
        # 相机会一下贴到脸上(实测最容易踩的就是这个)。
        lines += [
            f"    var _gk_space := {follow3d}.get_world_3d().direct_space_state",
            "    var _gk_exclude: Array[RID] = []",
            f"    if {follow} is CollisionObject3D:",
            f"        _gk_exclude.append({follow3d}.get_rid())",
            f"    if {cam_var} is CollisionObject3D:",
            f"        _gk_exclude.append(({cam_var} as CollisionObject3D)"
            ".get_rid())",
            "    var _gk_query := PhysicsRayQueryParameters3D.create("
            "_gk_pivot, _gk_want, 0xFFFFFFFF, _gk_exclude)",
            "    var _gk_hit := _gk_space.intersect_ray(_gk_query)",
            # ── 兜底:撞到的**就是跟随对象自己**时不算数(第 126 轮)────────
            # 上面虽然排除了 follow,但排除**只在 follow 是 CollisionObject3D
            # 时**才拿得到 RID。拿不到 RID 的那一刻,射线在枢轴处就会打中
            # 玩家自己的身体(真机量过:从枢轴往身后打,在 **0.11 米**处命中
            # 自己的胶囊)-> `0.11-0.35` 变负数 -> 被最短臂长夹住 ->
            # **相机被永久钉在最短距离上 = 每一帧都穿模**。
            # 一行判断把这条最容易踩的路堵死(拿不到 RID 也不会毁掉画面)。
            f"    if not _gk_hit.is_empty() and _gk_hit[\"collider\"] == {follow}:",
            "        _gk_hit = {}",
            "    if not _gk_hit.is_empty():",
            "        var _gk_dist: float = _gk_pivot.distance_to("
            "_gk_hit[\"position\"])",
            f"        _gk_dist = maxf(_gk_dist - {gd_number(radius)},"
            f" {gd_number(min_arm)})",
            "        _gk_want = _gk_pivot + _gk_dir * _gk_dist",
        ]
        # ── 天花板兜底:相机不许跑到"头顶那块天花板"上面(第 128 轮)────────
        # 用户原话:"摄像机穿模是我站在**第三关卡的那个正方体上面**,然后跳跃,
        # 就出现了"。
        #
        # ── 真机量到的链条(示例产物副本,Godot 4.7.2;第 3 关小房间)────────
        # 房间:地板 y=0、天花板底面 y=5.0(结局天花板 18×0.3×18 @ y=5.15)。
        # 玩家站在 `结局箱甲`(2.2×2.0×2.2,顶面 y=2.0)上:原点 y=2.90、
        # 枢轴(= 原点 + 1.4)y=4.30,离天花板还有 0.70 —— 这时射线撞天花板,
        # 相机被压在底面下方 y=4.927(**88/90 帧卡着不动**,是对的)。
        # 一发跳:头顶顶住天花板(原点最高 4.10,胶囊顶 4.10+0.9 = 5.0),
        # 而**枢轴冲到 y=5.50 —— 比天花板底面高 0.50、比它顶面还高 0.20**。
        # 从那时起主射线**什么都撞不到**(起点已经在天花板里/上面,
        # `intersect_ray` 不报自己所在的那块实心体)-> 杆子恢复到全长 5 米 ->
        # 相机跟着枢轴跑到 **y=6.539 = 屋子外面**。真机:f116~f140 共 25 帧
        # (0.42 秒)相机在天花板上面,屏幕上只剩天花板顶面、玩家不见了。
        # 截图对照:.audit/camclip/shot_before_up_jump.png(满屏一片紫)
        #           .audit/camclip/shot_after_up_jump.png(正常看到房间)
        #
        # ⚠⚠ 起点**必须是跟随对象(身体)的位置,不能是枢轴** ⚠⚠
        # 这一条是整套修法的关键,后来人千万别"顺手统一成枢轴":
        #   身体的位置一定在天花板**下面**(它就在屋里),从它往上打永远打得中;
        #   而枢轴正是那个穿到天花板里/上面的点,从固体内部往外打**打不到背面**
        #   —— 实测"从枢轴竖直向上打一条"完全没用(相机照样停在 y=6.0)。
        # 射线顺着"物体向上"而不是顺着杆子方向,也正是为了**够得着**:
        # 另外试过"从身体再打一条与杆子同方向的射线",仰角 12° 时天花板在
        # 那条线上有 5~10 米远、而杆子只有 5 米,玩家站得低时它**够不到**
        # (25 帧里只救回 12 帧);竖直向上那条只要"天花板-身体"这么长。
        #
        # 为什么加 10 米:相机最高也就到"枢轴 + 杆长"这个量级,封顶 10 米足够
        # 覆盖任何真能挡到它的天花板;打得更远也不会误夹(只有 `_gk_want`
        # 比命中点还高时才夹)。
        # 为什么留 0.1 米余量:相机的近平面是 0.1 —— 贴着天花板底面放会切天花板。
        lines += [
            f"    var _gk_up: Vector3 = {follow3d}.global_position",
            "    var _gk_up_hit := _gk_space.intersect_ray("
            "PhysicsRayQueryParameters3D.create("
            "_gk_up, _gk_up + Vector3(0.0, 10.0, 0.0), "
            "0xFFFFFFFF, _gk_exclude))",
            "    if not _gk_up_hit.is_empty():",
            "        var _gk_ceil_y: float = _gk_up_hit[\"position\"].y - 0.1",
            "        if _gk_want.y > _gk_ceil_y:",
            "            _gk_want.y = _gk_ceil_y",
        ]
    if lag > 0.0:
        # 指数平滑:系数按 delta 换算,**和帧率无关**。
        # (固定系数的 lerp 在 30 帧和 144 帧下手感完全不一样。)
        #
        # ── 『过渡曲线』(用户 2026-10-04 第十二轮)────────────────────
        # 每帧的跟随比例会经过节点上选的缓动曲线:
        #   * 线性(默认)= 原样,生成结果与以前**逐字一致**
        #   * 缓入 / 缓出 = 起步或收尾更柔
        #   * 回弹 = 比例会超过 1(过冲一点再回来)—— 弹簧手感
        raw_factor = (f"clampf({gd_number(lag)} * "
                      f"get_physics_process_delta_time(), 0.0, 1.0)")
        factor = _ease_factor(ctx, node, raw_factor, "gk_lag_trans",
                              "gk_lag_curve")
        lines += [
            f"    if not {ready_var}:",
            f"        {ready_var} = true",
            f"        {pos_var} = _gk_want",
            # ── 竖直方向**硬跟**:相机不许在角色上下移动时落后(第 126 轮)──
            # 用户原话:"有时跳跃,切换摄像机会穿模"。
            #
            # ── 真机量到的机制(示例产物副本,Godot 4.7.2,窗口化+无头各一遍)──
            # 平滑的是**世界坐标**,而跳跃时角色以 ~7 m/s 上升:
            # 系数 8*dt ≈ 0.133/帧 -> 相机相对枢轴下沉 0.42~0.49 米
            # (实测相机在玩家局部系里的 y:静止 +1.462 -> 跳跃中 **+0.939**,
            #  而玩家模型的盒顶在 **+0.900**)—— 正好掉到头顶那一层。
            # 模型是玩家的子节点、**零滞后**,于是两边错开半个身位;
            # 再叠上"贴着墙时杆子被压到 0.29 米",相机离模型表面只剩
            # **0.0385~0.0756 米**,比相机自己的近平面(near=0.1)还近 ——
            # 比近平面更近的面片被裁掉 + 背面剔除 -> 看到身体内部 = 穿模。
            # 开阔地跳不穿(2.5 米),贴墙跳才穿 —— 这正是用户说的"有时"。
            #
            # 这一行只把 Y 先拨到当前的 `_gk_want.y`:紧接着的整段 lerp 在 Y 上
            # 就是恒等(连"回弹"曲线的过冲也影响不到 Y),XZ 仍是原来的
            # 世界位置 lerp —— 走路时那股"松垮感"和贴墙时的画面**一个像素都不变**
            # (真机 A/B:贴墙静止 0.5619 -> 0.5619、离墙余量 0.3500 -> 0.3500、
            #  走路稳态臂长 5.4051 -> 5.4124;而穿模那几帧 0.0756->0.5416 /
            #  0.0539->0.5618,进近平面的帧数 3->0 / 12->0)。
            #
            # ── 为什么不用"整根杆向量一起平滑"(试过,量过)────────────────
            # 那样水平也变成硬跟:走路稳态臂长从 5.405 掉到 **4.821**,
            # "相机追不上角色"的跟随感没了 —— 修好穿模但把手感改掉了。
            f"    {pos_var}.y = _gk_want.y",
            f"    {pos_var} = {pos_var}.lerp(_gk_want, {factor})",
        ]
        lines.append(f"    if {blend_left} <= 0.0:")
        lines.append(f"        ({cam_var} as Node3D).global_position = {pos_var}")
    else:
        # ── 过渡进行中就**别抢**相机(用户第 25 轮:"切视角会闪一下")────
        # 相机过渡每帧插值写位姿,弹簧臂也每帧写 —— 两边同时写,
        # 相机就在两个位置之间来回跳。谁在过渡谁说了算。
        lines.append(f"    if {blend_left} <= 0.0:")
        lines.append(f"        ({cam_var} as Node3D).global_position = _gk_want")
    # ── 朝向 ────────────────────────────────────────────────────────
    # 默认 `look_at(枢轴)`:比手算欧拉角稳,也没有万向节问题。
    # 有旋转平滑时改成 slerp 一个 Basis(相机朝 -Z 看,所以目标是 -_gk_dir)。
    if lag_rot > 0.0:
        # 朝向平滑才需要这个临时变量(声明了不用会被 Godot 警告)
        basis_var = ctx.declare_var(f"_gk_spring_basis{tag}", "Basis")
        lines += [
            "    var _gk_want_basis := Basis.looking_at(-_gk_dir, Vector3.UP)",
            f"    if not {ready_var}:",
            f"        {ready_var} = true",
            f"        {basis_var} = _gk_want_basis",
            f"    {basis_var} = {basis_var}.slerp(_gk_want_basis, clampf("
            f"{gd_number(lag_rot)} * get_physics_process_delta_time(), "
            f"0.0, 1.0))",
            f"    var _gk_xform := ({cam_var} as Node3D).global_transform",
            f"    _gk_xform.basis = {basis_var}",
            f"    ({cam_var} as Node3D).global_transform = _gk_xform",
        ]
    else:
        lines.append(f"    ({cam_var} as Node3D).look_at(_gk_pivot, Vector3.UP)")
    ctx.physics_hooks.setdefault("camera_spring_arm", []).extend(lines)


@multi_output("GKNodeCameraStatus")
def _expr_camera_status(ctx, node):
    """『相机_取状态』:当前相机是谁、在哪、朝哪、视野多少、开没开。

    ── 为什么走**辅助函数**而不是"就地落局部变量"(踩过两次)────────────
    第一版把表达式内联在每个输出里 -> 「滚轮改视野」那条链生成出一行
    **500 多字符**的怪物(同样的表达式重复 8 遍)。

    第二版改成"求值时先 add_line 落几个局部变量" —— 更糟:表达式是**在
    语句生成途中**求值的,那时候"当前缩进"是上一条语句的缩进,
    于是那几个 var 和包着它的 `if` **缩进对不上**,滚轮那段被嵌进了
    跳跃分支里(生成的代码看着像对,跑起来逻辑全乱)。

    现在每个值一个小函数:输出引脚就是一句 `_gk_cam_fov()` ——
    短、缩进无关、求值几次都不会有副作用。
    """
    cam = ctx.declare_var("_gk_cam_query", "Node")
    ctx.need_camera_query_helper = True
    helpers = [
        ("_gk_cam_name", "String", [
            "    var cam := _gk_current_camera()",
            "    return String(cam.name) if cam != null else \"\"",
        ]),
        ("_gk_cam_fov", "float", [
            "    var cam := _gk_current_camera()",
            "    if cam is Camera3D:",
            "        return (cam as Camera3D).fov",
            "    if cam is Camera2D:",
            "        return (cam as Camera2D).zoom.x",
            "    return 0.0",
        ]),
        ("_gk_cam_open", "bool", [
            "    var cam := _gk_current_camera()",
            "    if cam is Camera3D:",
            "        return (cam as Camera3D).is_current()",
            "    if cam is Camera2D:",
            "        return (cam as Camera2D).is_current()",
            "    return false",
        ]),
    ]
    if ctx.dim == 2:
        helpers.append(("_gk_cam_pos", "Vector2", [
            "    var cam := _gk_current_camera()",
            "    return (cam as Node2D).global_position if cam is Node2D "
            "else Vector2.ZERO",
        ]))
    else:
        helpers.append(("_gk_cam_pos", "Vector3", [
            "    var cam := _gk_current_camera()",
            "    return (cam as Node3D).global_position if cam is Node3D "
            "else Vector3.ZERO",
        ]))
    helpers.append(("_gk_cam_rot", "Vector3", [
        "    var cam := _gk_current_camera()",
        "    return (cam as Node3D).rotation_degrees if cam is Node3D "
        "else Vector3.ZERO",
    ]))
    made = getattr(ctx, "_cam_status_funcs", None)
    if made is None:
        made = set()
        ctx._cam_status_funcs = made
    for base, ret, body in helpers:
        if base in made:
            continue
        made.add(base)
        ctx.extra_sections.append({
            "signature": f"func {base}() -> {ret}:",
            "lines": body,
            "empty_body": "    pass",
            "separator": True,
        })
    return {
        "相机名": "_gk_cam_name()",
        "位置": "_gk_cam_pos()",
        "朝向": "_gk_cam_rot()",
        "视野": "_gk_cam_fov()",
        "开着吗": "_gk_cam_open()",
    }


@multi_output("GKNodeCameraSpringArm")
def _expr_spring_arm_outputs(ctx, node):
    """弹簧臂右侧的三个输出 —— 让别处能读到杆子的状态。

    用户 2026-10-03:"向量右侧输出引脚:便于在运行过程中设置向量"。
    三个都是**成员变量**,由每帧那段逻辑更新 —— 所以在任何地方读都是
    最新值(准星、判断"镜头有没有被墙顶进来"、把相机位置喂给别的节点)。

    注意:这个节点同时是**语句节点**(有执行流)。两边都注册:
    走执行流那支用 STMT_HANDLERS,取数据那支用 MULTI_OUTPUT_FUNCS。
    """
    tag = _spring_tag(ctx, node)
    pivot = ctx.declare_var(f"_gk_spring_pivot{tag}", "Vector3")
    direction = ctx.declare_var(f"_gk_spring_dir{tag}", "Vector3")
    cam = ctx.declare_var(f"_gk_spring_cam{tag}", "Node")
    return {
        "枢轴": pivot,
        "方向": direction,
        "相机位置": (f"(({cam} as Node3D).global_position if {cam} is Node3D "
                     f"else Vector3.ZERO)"),
    }


def _path_name(path):
    """从生成的节点引用表达式里取出**节点名**。

    `$"侧视机位"` / `$Camera` -> `侧视机位` / `Camera`;
    不是这种形式的(比如 `self` 或已经算好的表达式)原样返回。
    """
    text = str(path or "").strip()
    if text.startswith("$"):
        text = text[1:].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("\"", "'"):
        text = text[1:-1]
    return text


def _sample_curve_points(curve_name, samples=48):
    """把一条 Blender 曲线采样成世界坐标点列(转发到 `curve_assets`)。

    ── 为什么在**导出时**采样(而不是把曲线导进游戏)────────────────
    用户的想法是"在场景里建模一条曲线,相机挂在上面"。但 glTF **不支持
    曲线**:导出时曲线会被转成网格(那就变成一根有厚度的管子,不是轨道了)。
    所以『相机_路径』把曲线的形状**采样成点**写进生成的脚本 ——
    游戏里只需要一串 Vector3,不依赖任何曲线资源。

    (第十二轮把采样实现搬到了 `curve_assets`,因为"沿曲线运镜 / 时间轴"
    也要用同一套采样;这里保留这个外壳,调用方一字不改。)
    """
    return curve_assets.sample_path_points(curve_name, samples)


@stmt_node("GKNodeCameraPath")
def _stmt_camera_path(ctx, node, indent):
    """相机沿曲线走(运镜轨道)。

    曲线在**导出时**被采样成点列写进脚本 —— 游戏里只是一串 Vector3,
    不依赖任何曲线资源(glTF 也不支持曲线,见 _sample_curve_points)。
    """
    curve_name = str(getattr(node, "gk_curve", "") or "").strip()
    points = _sample_curve_points(curve_name)
    if not curve_name:
        ctx.warn("『相机_路径』还没选曲线 —— 点节点上的『选曲线』挑一条"
                 "(场景里没有的话:添加 → 曲线 → 路径)")
        return
    if len(points) < 2:
        ctx.warn(f"『相机_路径』的曲线『{curve_name}』采不到点 —— "
                 f"确认它是曲线物体,而且有至少两个点")
        return
    path_var = ctx.declare_var("_gk_cam_path", "PackedVector3Array")
    ctx.var_initials[path_var] = "PackedVector3Array([" + ", ".join(
        f"Vector3({gd_number(x)}, {gd_number(y)}, {gd_number(z)})"
        for x, y, z in points) + "])"
    camera = _camera_node_expr(ctx, node)
    cam_var = ctx.declare_var("_gk_path_cam", "Node")
    pos_var = ctx.declare_var("_gk_path_pos", "Vector3")
    t_var = ctx.declare_var("_gk_path_t", "float")
    ctx.add_line(indent, f"{cam_var} = {camera}")
    position = ctx.input_number(node, "位置", "0.0")
    speed = float(getattr(node, "gk_speed", 0.0) or 0.0)
    smooth = float(getattr(node, "gk_smooth", 8.0) or 0.0)
    loop = bool(getattr(node, "gk_loop", False))
    mode = str(getattr(node, "gk_look_mode", "FORWARD"))
    target = ""
    if mode == "TARGET":
        raw = str(getattr(node, "gk_look_target", "") or "").strip()
        target = (ctx.target_expr(node, "gk_look_target", "看向") if raw
                  else "null")
    frame = [
        f"if {cam_var} != null and {cam_var} is Node3D:",
        f"    var _gk_count: int = {path_var}.size()",
        f"    if _gk_count >= 2:",
        f"        {t_var} = clampf({t_var}, 0.0, 1.0)",
        f"        var _gk_f: float = {t_var} * float(_gk_count - 1)",
        "        var _gk_i: int = int(floor(_gk_f))",
        "        var _gk_frac: float = _gk_f - float(_gk_i)",
        f"        var _gk_want: Vector3 = {path_var}[_gk_i].lerp("
        f"{path_var}[mini(_gk_i + 1, _gk_count - 1)], _gk_frac)",
    ]
    if smooth > 0.0:
        t = (f"clampf({gd_number(smooth)} * "
             f"get_physics_process_delta_time(), 0.0, 1.0)")
        frame += [
            f"        {pos_var} = {pos_var}.lerp(_gk_want, {t})",
        ]
    else:
        frame += [f"        {pos_var} = _gk_want"]
    frame += [
        f"        ({cam_var} as Node3D).global_position = {pos_var}",
    ]
    if mode == "FORWARD":
        # 朝前 = 用"下一个点 - 当前位置"当方向(算出来才准,
        # 拿曲线切线的话还得再采样一次)
        frame += [
            f"        var _gk_ahead: Vector3 = {path_var}[mini(_gk_i + 1, "
            f"_gk_count - 1)]",
            f"        if ({cam_var} as Node3D).global_position.distance_to("
            f"_gk_ahead) > 0.01:",
            f"            ({cam_var} as Node3D).look_at(_gk_ahead, Vector3.UP)",
        ]
    elif mode == "TARGET" and target:
        frame += [
            f"        if {target} is Node3D:",
            f"            ({cam_var} as Node3D).look_at("
            f"({target} as Node3D).global_position, Vector3.UP)",
        ]
    if speed > 0.0:
        frame += [
            f"        {t_var} += {gd_number(speed / 100.0)} * "
            f"get_physics_process_delta_time()",
        ]
        if loop:
            frame += [
                f"        if {t_var} > 1.0:",
                f"            {t_var} = 0.0",
            ]
        else:
            frame += [f"        {t_var} = minf({t_var}, 1.0)"]
    # 起始位置由执行点的『位置』引脚给(通常是 0)
    ctx.add_line(indent, f"{t_var} = {position}")
    ctx.physics_hooks.setdefault("camera_path", []).extend(frame)


@stmt_node("GKNodeCameraSetCurrent")
def _stmt_camera_set_current(ctx, node, indent):
    """切相机;填了『过渡』就是丝滑推镜,还能沿曲线飞过去。

    ── 丝滑推镜怎么做的(不用 await,也不用挪走旧相机)────────────
    1. 先记住**新相机自己**的位姿(end)
    2. 把新相机**瞬移**到旧相机的位姿(start),然后 make_current()
    3. 之后每帧把它从 start 插值回 end(登记进 physics_hooks)
    4. 到位后精确写回 end

    关键在"动的是**新**相机":它最后停在的正是自己本来的位姿,所以
    过渡结束不需要任何还原;旧相机从头到尾没被碰过,来回切也不会跑位。
    (先动旧相机再切的做法会把它永久挪到新位置 —— 切回去就发现镜头歪了。)

    每帧逻辑挂在 `_physics_process`(和角色移动同一个时钟),避免相机
    在渲染帧里追、角色在物理帧里跑,两个节拍一差拍画面就抖。

    ── 第十二轮加的两种"好看"(用户:幻灯片式切镜太硬 / 想要曲线运动)──
    * 『过渡曲线』:过渡比例不再匀速,而是过一遍缓动档位(或你画的曲线)
      —— 起手和收手都柔,或者带一点回弹
    * 『沿曲线运镜』:过渡期间相机的位置不走在直线上,而是沿着**场景里
      画的一条曲线**(导出成 Godot 的 Curve3D 资源)飞过去,朝向仍然是
      从旧机位平滑转到目标机位。开场运镜 / 绕柱切镜用这个
    """
    camera, _auto = _camera_expr(ctx, node)
    blend = float(getattr(node, "gk_blend", 0.0) or 0.0)
    # `to_var` 必须是**成员变量**,而且这里**不能写 `var`**:
    # 过渡逻辑跑在 _physics_process 里(另一个函数),用局部变量会
    #   1) `Identifier "_cam_to2" not declared in the current scope`(实测)
    #   2) 就算声明成成员变量,再写一次 `var` 会**遮蔽**它 ——
    #      局部那个出了 if 块就没了,钩子里读到的永远是 null,
    #      相机过渡**静默失效**(测试里就是这么发现的第一版)
    to_var = ctx.declare_var("_gk_cam_to", "Node")
    ctx.add_line(indent, f"{to_var} = {camera}")
    ctx.add_line(indent, f"if {to_var} == null:")
    ctx.add_line(indent + 1, f"push_warning(\"[GK] 相机_切换:没找到相机\")")
    if blend <= 0.0:
        ctx.add_line(indent, f"else:")
        ctx.add_line(indent + 1, f"{to_var}.make_current()")
        return

    # ---- 带过渡 ----
    from_var = ctx.declare_var("_gk_cam_from", "Node")
    left_var = ctx.declare_var("_gk_cam_blend_left", "float")
    total_var = ctx.declare_var("_gk_cam_blend_total", "float")
    start3 = ctx.declare_var("_gk_cam_start3", "Transform3D")
    # ── 目标相机"本来的位姿"要**缓存**,不能每次现读(用户 2026-10-03)──
    # 连续点 C/V 时相机一路往后飘,原因就在这:
    #   第一次切换:end = 相机本来的位姿(对的) -> 过渡到 end
    #   过渡还没走完又按一次:此时相机正停在**半路**,再读一次"它本来的
    #   位姿"读到的就是那个半路位置 -> end 被改成了半路位置 ->
    #   下一次再往后退一点…… 按几次退几次。
    # 现在把每台相机的"家"记在字典里,第一次见到它时存下来,以后一直用它。
    homes = ctx.declare_var("_gk_cam_homes", "Dictionary")
    end3 = ctx.declare_var("_gk_cam_end3", "Transform3D")
    start2 = ctx.declare_var("_gk_cam_start2", "Vector2")
    end2 = ctx.declare_var("_gk_cam_end2", "Vector2")
    zoom2 = ctx.declare_var("_gk_cam_endzoom", "Vector2")
    # ── 过渡曲线 / 沿曲线运镜的五个成员变量 ────────────────────────
    # (钩子跑在另一个函数里,只能用成员变量;见 to_var 那段说明)
    trans_var = ctx.declare_var("_gk_cam_trans", "int")
    ease_var = ctx.declare_var("_gk_cam_ease", "int")
    curve_var = ctx.declare_var("_gk_cam_ease_curve", "Curve")
    # 沿曲线飞的轨道。**不叫 `_gk_cam_path`** —— 那个名字是『相机_路径』
    # 的 PackedVector3Array(同一张图里两个节点都出现时会撞类型)。
    fly_var = ctx.declare_var("_gk_cam_fly_curve", "Curve3D")
    fly_len = ctx.declare_var("_gk_cam_fly_len", "float")
    ctx.add_line(indent, f"else:")
    ctx.add_line(indent + 1, f"{from_var} = get_viewport().get_camera_3d()")
    ctx.add_line(indent + 1, f"if {from_var} == null:")
    ctx.add_line(indent + 2, f"{from_var} = get_viewport().get_camera_2d()")
    # ---- 这一趟过渡用哪条曲线(每次切换都重设,不会残留上一次的)----
    preset = str(getattr(node, "gk_blend_trans", "LINEAR") or "LINEAR")
    if preset == "CUSTOM":
        # 曲线能采到就是那句 load(...);采不到是 "null"(此时 trans/ease
        # 用线性 —— 钩子里的 `_gk_ease` 看到 curve == null 就走引擎档位)
        curve_expr = easing.curve_expr(
            ctx, getattr(node, "gk_blend_curve", ""), "Curve")
        ctx.add_line(indent + 1, f"{curve_var} = {curve_expr}")
        ctx.add_line(indent + 1, f"{trans_var} = Tween.TRANS_LINEAR")
        ctx.add_line(indent + 1, f"{ease_var} = Tween.EASE_IN_OUT")
    else:
        trans, ease = easing.ease_constants(preset)
        ctx.add_line(indent + 1, f"{curve_var} = null")
        ctx.add_line(indent + 1, f"{trans_var} = {trans}")
        ctx.add_line(indent + 1, f"{ease_var} = {ease}")
    path_mode = str(getattr(node, "gk_path_mode", "DIRECT") or "DIRECT")
    if path_mode == "CURVE":
        path_expr = easing.curve_expr(
            ctx, getattr(node, "gk_path_curve", ""), "Curve3D")
    else:
        path_expr = "null"
    ctx.add_line(indent + 1, f"{fly_var} = {path_expr}")
    ctx.add_line(indent + 1, f"{fly_len} = 0.0")
    ctx.add_line(indent + 1, f"if {fly_var} != null:")
    ctx.add_line(indent + 2, f"{fly_len} = {fly_var}.get_baked_length()")
    ctx.add_line(indent + 1, f"if {to_var} is Camera3D and {from_var} is Camera3D:")
    # 第一次见到这台相机 -> 把它的"家"记下来;以后每次都回到那个家
    ctx.add_line(indent + 2, f"if not {homes}.has({to_var}):")
    ctx.add_line(indent + 3, f"{homes}[{to_var}] = "
                             f"({to_var} as Camera3D).global_transform")
    ctx.add_line(indent + 2, f"{end3} = {homes}[{to_var}]")
    ctx.add_line(indent + 2, f"{start3} = ({from_var} as Camera3D).global_transform")
    ctx.add_line(indent + 2, f"({to_var} as Camera3D).global_transform = {start3}")
    ctx.add_line(indent + 2, f"({to_var} as Camera3D).make_current()")
    ctx.add_line(indent + 2, f"{total_var} = maxf({gd_number(blend)}, 0.0001)")
    ctx.add_line(indent + 2, f"{left_var} = {total_var}")
    ctx.add_line(indent + 1, f"elif {to_var} is Camera2D and {from_var} is Camera2D:")
    ctx.add_line(indent + 2, f"if not {homes}.has({to_var}):")
    ctx.add_line(indent + 3, f"{homes}[{to_var}] = ["
                             f"({to_var} as Camera2D).global_position, "
                             f"({to_var} as Camera2D).zoom]")
    ctx.add_line(indent + 2, f"{end2} = ({homes}[{to_var}] as Array)[0]")
    ctx.add_line(indent + 2, f"{zoom2} = ({homes}[{to_var}] as Array)[1]")
    ctx.add_line(indent + 2, f"{start2} = ({from_var} as Camera2D).global_position")
    ctx.add_line(indent + 2, f"({to_var} as Camera2D).global_position = {start2}")
    ctx.add_line(indent + 2, f"({to_var} as Camera2D).zoom = "
                             f"({from_var} as Camera2D).zoom")
    ctx.add_line(indent + 2, f"({to_var} as Camera2D).make_current()")
    ctx.add_line(indent + 2, f"{total_var} = maxf({gd_number(blend)}, 0.0001)")
    ctx.add_line(indent + 2, f"{left_var} = {total_var}")
    ctx.add_line(indent + 1, f"else:")
    ctx.add_line(indent + 2, f"{to_var}.make_current()")

    # ---- 每帧推进(和相机震动同一个机制)----
    #
    # ── `_gk_ct` 是"过渡比例",它可能被『过渡曲线』加工过 ──────────────
    # 线性档**不加壳**(生成结果与老版本逐字一致);其它档位过一遍
    # `_gk_ease`(内置档位走引擎的 TRANS/EASE,自定义档走 Blender 画的曲线)。
    raw_ct = (f"clampf(1.0 - {left_var} / {total_var}, 0.0, 1.0)")
    wants_ease = preset != "LINEAR"
    if not wants_ease:
        ct_line = f"    var _gk_ct: float = {raw_ct}"
    else:
        eased = easing.apply_ease(ctx, node, raw_ct, "gk_blend_trans",
                                 "gk_blend_curve", "Curve")
        ct_line = f"    var _gk_ct: float = {eased}"
    hook_key = "camera_blend"
    # ── 钩子只生成一次,但"要不要过缓动"取决于**每一台**切换节点 ─────────
    # 图里可以有几台『相机_切换』(多机位),各自的过渡曲线可能不同。
    # 第一版只有第一个节点说了算:先遇到一个匀速的,后面那个"回弹"就
    # **静默不生效**(钩子里没有 _gk_ease 那一层)。所以后遇到的缓动节点
    # 会把钩子**升级**一次(成员变量是共享的,升级是安全的)。
    hook = ctx.physics_hooks.get(hook_key)
    if hook is None or (wants_ease and not getattr(ctx, "_gk_cam_eased", False)):
        ctx._gk_cam_eased = getattr(ctx, "_gk_cam_eased", False) or wants_ease
        ctx.physics_hooks[hook_key] = _camera_blend_hook(
            left_var, total_var, ct_line, to_var, from_var, start3, end3,
            start2, end2, zoom2, fly_var, fly_len,
            # ── 上帝视角期间**暂停**过渡(用户 2026-10-05)────────────────
            # "一开始如果按上帝模式就不应被曲线视角控制":看上帝机的时候,
            # 这条过渡还在按每帧比例推着目标相机走 —— 用户在看戏,镜头却在
            # 背后自己跑;等切回来时人已经被运到半路了。
            # 现在:上帝机在用就**不动**(也不消耗过渡时间),切回来接着走。
            god_guard=bool(getattr(ctx, "god_camera_enabled", False)))


def _camera_blend_hook(left_var, total_var, ct_line, to_var, from_var, start3,
                       end3, start2, end2, zoom2, fly_var, fly_len,
                       god_guard=False):
    """切相机每帧推进的那段代码(唯一的用处是给『相机_切换』用)。

    ── 沿曲线飞怎么写的(踩过的坑)──────────────────────────────────
    先算出"插值后的位姿"(朝向从旧机位转到目标机位),再把**位置**换成
    曲线上的点 —— 也就是"插值的朝向 + 曲线上的位置"。

    第一版把 `var _gk_fly = sample_baked(...)` 写在 `if 有曲线:` 块里、
    出了块再拿它拼 `_gk_xf.origin` —— Godot 里块内局部变量出了块就没了,
    生成的脚本**连解析都过不去**(真 Blender 导出后一眼就看见)。
    现在:`_gk_xf` 在外面声明,曲线只在块内**改它的 origin**。

    `clampf` 是因为回弹档的 _gk_ct 会略微超出 0~1(采样不能越界)。

    god_guard:开着上帝摄像机时,过渡**暂停**(既不推进也不写相机)——
    只在角色脚本里带了 `_gk_god_camera_active()` 时才能加这个条件。
    """
    fly_origin = (f"            _gk_xf.origin = {fly_var}.sample_baked("
                  f"clampf(_gk_ct, 0.0, 1.0) * {fly_len}, true)")
    guard = " and not _gk_god_camera_active()" if god_guard else ""
    return [
        f"if {left_var} > 0.0{guard}:",
        f"    {left_var} -= delta",
        ct_line,
        f"    if ({to_var} is Camera3D) and ({from_var} is Camera3D):",
        f"        var _gk_xf: Transform3D = {start3}.interpolate_with("
        f"{end3}, _gk_ct)",
        f"        if {fly_var} != null:",
        fly_origin,
        f"        ({to_var} as Camera3D).global_transform = _gk_xf",
        f"    elif ({to_var} is Camera2D) and ({from_var} is Camera2D):",
        f"        if {fly_var} != null:",
        f"            var _gk_fly2: Vector3 = {fly_var}.sample_baked("
        f"clampf(_gk_ct, 0.0, 1.0) * {fly_len}, true)",
        f"            ({to_var} as Camera2D).global_position = "
        f"Vector2(_gk_fly2.x, _gk_fly2.y)",
        f"        else:",
        f"            ({to_var} as Camera2D).global_position = "
        f"{start2}.lerp({end2}, _gk_ct)",
        f"        ({to_var} as Camera2D).zoom = "
        f"({from_var} as Camera2D).zoom.lerp({zoom2}, _gk_ct)",
        f"    if {left_var} <= 0.0:",
        # 到位:精确写回,免得浮点插值差一点点(镜头会"差一丝")
        f"        if {to_var} is Camera3D:",
        f"            ({to_var} as Camera3D).global_transform = {end3}",
        f"        elif {to_var} is Camera2D:",
        f"            ({to_var} as Camera2D).global_position = {end2}",
        f"            ({to_var} as Camera2D).zoom = {zoom2}",
    ]


@stmt_node("GKNodeCameraZoom")
def _stmt_camera_zoom(ctx, node, indent):
    """缩放 / 视野;填了『时长』就用 Tween 平滑过去。"""
    camera, _auto = _camera_expr(ctx, node)
    value = _tunable_number(ctx, node, "数值", "1.0", "camera_zoom")
    duration = float(getattr(node, "gk_time", 0.0) or 0.0)
    # ── 上下限(用户 2026-10-03:"可以无限拉长,应该添加自定义范围")──────
    # 0 = 不限制。填了就在**生成时**夹一次:
    #   zoom 当固定值 -> 直接 clamp 那个常量,生成的代码还是干净的
    #   zoom 是表达式(滚轮链)-> 用 clampf(...) 包一层
    low = float(getattr(node, "gk_min_value", 0.0) or 0.0)
    high = float(getattr(node, "gk_max_value", 0.0) or 0.0)
    if low != 0.0 or high != 0.0:
        lo = gd_number(low) if low != 0.0 else "-INF"
        hi = gd_number(high) if high != 0.0 else "INF"
        value = f"clampf({value}, {lo}, {hi})"
    temp = ctx.new_temp("cam")
    # 2D 的 zoom 是"越大越近"的倍数,3D 的 fov 是垂直视场角(度)。
    # 两者语义不同，所以按相机真实类型分派，而不是按项目维度猜。
    ctx.add_line(indent, f"var {temp} = {camera}")
    if duration > 0.0:
        tween_var = ctx.new_temp("tween")
        ctx.add_line(indent, f"if {temp} != null:")
        ctx.add_line(indent + 1, f"var {tween_var} = create_tween()")
        ctx.add_line(indent + 1, f"{tween_var}.set_trans(Tween.TRANS_SINE)")
        ctx.add_line(indent + 1, f"{tween_var}.set_ease(Tween.EASE_IN_OUT)")
        ctx.add_line(indent + 1, f"if {temp} is Camera2D:")
        ctx.add_line(indent + 2, f"{tween_var}.tween_property({temp}, "
                                 f"\"zoom\", Vector2({value}, {value}), "
                                 f"{gd_number(duration)})")
        ctx.add_line(indent + 1, f"elif {temp} is Camera3D:")
        ctx.add_line(indent + 2, f"{tween_var}.tween_property({temp}, "
                                 f"\"fov\", {value}, {gd_number(duration)})")
        return
    ctx.add_line(indent, f"if {temp} is Camera2D:")
    ctx.add_line(indent + 1, f"{temp}.zoom = Vector2({value}, {value})")
    ctx.add_line(indent, f"elif {temp} is Camera3D:")
    ctx.add_line(indent + 1, f"{temp}.fov = {value}")

@stmt_node("GKNodeCameraShake")
def _stmt_camera_shake(ctx, node, indent):
    """相机震动。

    ── 为什么不是"一次性语句" ──────────────────────────────────────
    震动要跨帧衰减，所以真正的每帧逻辑必须挂在 _process 里。
    做法:这里只负责"装上"(记下相机 + 重置计时器)，
    衰减逻辑登记进 ctx.process_hooks，由 generate() 保证 _process 存在。
    """
    _install_camera_shake(ctx, node, indent)


def _install_camera_shake(ctx, node, indent):
    """把"震动装上去"的代码吐在 indent 处。

    ── 为什么抽成独立函数(第五十九轮)────────────────────────────
    『惊吓_事件』(相机震动 + 闪白 + 音效三连)也要装同一次震动 ——
    它读的属性名和『相机_震动』完全一样(gk_strength / gk_duration /
    gk_target)，所以两个节点共用这一份实现，不必抄一遍。
    """
    camera, is_specific = _camera_expr(ctx, node)
    strength = gd_number(getattr(node, "gk_strength", 8.0))
    duration = gd_number(getattr(node, "gk_duration", 0.25))

    cam_var = ctx.declare_var("_gk_shake_cam", "Node")
    time_var = ctx.declare_var("_gk_shake_left", "float")
    power_var = ctx.declare_var("_gk_shake_power", "float")
    total_var = ctx.declare_var("_gk_shake_total", "float")

    if is_specific:
        ctx.add_line(indent, f"{cam_var} = {camera}")
    else:
        # 优先用放进 "gk_camera" 组的相机(用户显式指定)，
        # 没有就退回到场景里第一个摄像机(新手不填也能用)。
        ctx.add_line(indent, f'{cam_var} = get_tree().get_first_node_in_group("gk_camera")')
        ctx.add_line(indent, f"if {cam_var} == null:")
        ctx.add_line(indent + 1, f"{cam_var} = _gk_find_camera(self)")

    ctx.add_line(indent, f"{power_var} = {strength}")
    ctx.add_line(indent, f"{total_var} = maxf({duration}, 0.0001)")
    ctx.add_line(indent, f"{time_var} = {total_var}")

    # ---- 每帧衰减逻辑 ----
    hook_key = "camera_shake"
    if hook_key not in ctx.process_hooks:
        lines = [
            f"if {time_var} > 0.0:",
            f"    {time_var} -= delta",
            f"    var _decay: float = {time_var} / {total_var}",
            f"    var _ox: float = randf_range(-{power_var}, {power_var}) * _decay",
            f"    var _oy: float = randf_range(-{power_var}, {power_var}) * _decay",
            f"    if {cam_var} is Camera2D:",
            f"        ({cam_var} as Camera2D).offset = Vector2(_ox, _oy)",
            f"    elif {cam_var} is Camera3D:",
            f"        ({cam_var} as Camera3D).h_offset = _ox",
            f"        ({cam_var} as Camera3D).v_offset = _oy",
            f"elif {cam_var} != null:",
            "    # 震动结束:偏移归零，否则相机会一直歪着",
            f"    if {cam_var} is Camera2D:",
            f"        ({cam_var} as Camera2D).offset = Vector2.ZERO",
            f"    elif {cam_var} is Camera3D:",
            f"        ({cam_var} as Camera3D).h_offset = 0.0",
            f"        ({cam_var} as Camera3D).v_offset = 0.0",
        ]
        ctx.process_hooks[hook_key] = lines

    # 自动找相机需要一个小工具函数
    ctx.need_camera_finder = True


@stmt_node("GKNodeCameraFollow")
def _stmt_camera_follow(ctx, node, indent):
    """相机跟随。

    ── 为什么这件事必须由生成器负责 ────────────────────────────────
    Godot 的 Camera2D / Camera3D 本身**不跟随**任何东西:它站在哪就拍哪。
    平台跳跃游戏不放跟随，角色一跑远就出画面 —— 这是新手最常见的
    "我角色不见了"来源。做成一个节点，而不是让人自己写脚本。
    """
    raw_cam = (getattr(node, "gk_camera", "") or "").strip()
    # ── 相机引用不能生成 `$名字`(踩过的坑)─────────────────────────
    # 以前填了相机名就生成 `@onready var cam_x: Camera3D = $MyCam` ——
    # 那是个**相对本节点的路径**。名字对不上时 Godot 加载脚本直接失败,
    # 报 "Node not found: MyCam (relative to /root/Cube)",
    # 整个脚本连 _process 都跑不起来(用户实测正是这个现象)。
    #
    # 而节点引用下拉里列的是**节点图里的节点**,场景里几乎不可能有同名
    # 物体 —— 用户"怎么选都没影响"的观感就是这么来的。
    #
    # 改成按**名字在场景里搜**,搜不到就退回"场景里第一个相机":
    # 填错只是退回自动模式,不会把脚本搞崩。
    if raw_cam and raw_cam != TARGET_SELF:
        camera = (ctx.target_expr(node, "gk_camera", hint="cam") if raw_cam.startswith("@")
                  else f"_gk_find_camera_named(self, {gd_string(raw_cam)})")
    else:
        # 交给一个纯查询函数:这个节点每帧都在跑，而相机可能还没进场景。
        # 用函数而不是内联代码，是为了把 null 情况收在一处处理。
        camera = "_gk_find_camera(self)"
    ctx.need_camera_finder = True

    raw_target = (getattr(node, "gk_target", TARGET_SELF) or TARGET_SELF).strip()
    target = "self" if raw_target == TARGET_SELF else ctx.target_expr(
        node, "gk_target", hint="node")

    smooth = float(getattr(node, "gk_smooth", 0.12) or 0.0)
    offset_x = gd_number(getattr(node, "gk_offset_x", 0.0))
    offset_y = gd_number(getattr(node, "gk_offset_y", 0.0))
    vec_ctor = "Vector2" if ctx.dim == 2 else "Vector3"

    cam_var = ctx.declare_var("_gk_follow_cam", "Node")
    look_var = ctx.declare_var("_gk_follow_look", "Node")

    if ctx.dim == 2:
        # 2D:相机就在同一平面上，直接跟到目标位置(+偏移)
        target_vec = (f"{vec_ctor}({look_var}.global_position.x + {offset_x}, "
                      f"{look_var}.global_position.y + {offset_y})")
    else:
        # ── 3D 的相机必须**保持一段距离**，不能贴到角色身上 ──────────
        # 踩过的坑:3D 里把相机直接放到角色位置，相机就进了模型内部 ——
        # 画面上只剩一片模型的背面(用户看到的是"一片色块")。
        # 正确做法是记下"相机相对角色的偏移"，然后保持这个偏移跟随:
        # 相机在哪儿一开始由你在 Blender 里摆好，跟随过程中不改变视角。
        dist = getattr(node, "gk_distance", 0.0) or 0.0
        if dist:
            # 用户指定了距离:相机站在角色背后 dist 处、抬高 offset_y
            target_vec = (f"{look_var}.global_position "
                          f"+ {vec_ctor}({offset_x}, {offset_y}, {gd_number(dist)})")
        else:
            # 没指定距离:沿用"一开始的相对位置"，只跟着目标平移。
            # 这样"相机在 Blender 里摆在哪儿"就决定了看角色的角度。
            target_vec = (f"{look_var}.global_position + _gk_follow_delta "
                          f"+ {vec_ctor}({offset_x}, {offset_y}, 0.0)")

    # ── 跟随逻辑挂到**物理帧**,不挂在 _process(踩过的坑)────────────
    # 角色移动在 _physics_process 里(固定步长),相机跟随原来在 _process
    # (跟渲染帧率)—— 两个时钟不同步时相机就会抖。
    # 用户实测:把『每秒物理周期数』从 60 改成和刷新率一样,闪烁就没了,
    # 正是这个原因。相机跟着角色走,就必须和角色在**同一个时钟**里更新。
    #
    # 顺带一个好处:事件里连没连执行流都不影响 —— 只要图里有『相机_跟随』
    # 节点,相机就会跟着走。以前必须把它接在『每当_每帧』后面才行。
    hook_lines = []
    hook_lines.append(f"if {cam_var} == null:")
    hook_lines.append(f"    {cam_var} = {camera}")
    hook_lines.append(f"{look_var} = {target}")
    hook_lines.append(f"if {cam_var} != null and {look_var} != null:")
    if ctx.dim != 2 and not (getattr(node, "gk_distance", 0.0) or 0.0):
        setup_var = ctx.declare_var("_gk_follow_setup", "bool")
        delta_var = ctx.declare_var("_gk_follow_delta", vec_ctor)
        hook_lines.append(f"    if not {setup_var}:")
        hook_lines.append(f"        {setup_var} = true")
        hook_lines.append(f"        {delta_var} = {cam_var}.global_position "
                          f"- {look_var}.global_position")
    if smooth > 0.0:
        hook_lines.append(f"    {cam_var}.global_position = "
                          f"{cam_var}.global_position.lerp({target_vec}, "
                          f"{gd_number(smooth)})")
    else:
        hook_lines.append(f"    {cam_var}.global_position = {target_vec}")
    ctx.physics_hooks.setdefault("camera_follow", []).extend(hook_lines)
    # 事件体里留一行提示,让人知道逻辑在下面的物理帧里
    ctx.add_line(indent, "# 相机跟随的逻辑在 _physics_process 里(和角色同一个时钟)")
