"""物理测试场(第二十八轮):把碰撞/物理的边界情况走一遍。

用户 2026-10-05:"**碰撞体系统的打磨**吧,在与正方体模型对角上重叠会穿模"。
第十九/二十轮修完了判据和偏移,但**一个真探针都没跑过物理** ——
这一关就是那件事的"现场":十个工位排成一排,探针在 Godot 里驱动角色
一个个走一遍,有穿模/卡住/推不动就抓出来。

| 工位 | 测什么 | 期望 |
|---|---|---|
| **斜坡** | 走上 20° 的坡 | 能爬上去(y 升高 ≥ 0.8),不卡在坡脚 |
| **台阶** | 低台阶 0.35(在『上台阶高度』0.5 内)+ 高台阶 1.05(差 0.7) | 低台阶**迈得上去**、高台阶**仍然挡住**(不穿模、不"骑"上去) |
| **对角方块** | 一个转 45° 的方块(角朝着人)+ 另一个方块**斜对角重叠** | 角色停在**角**外面 —— 球碰撞会内切进去(用户报过的现象) |
| **推箱子** | 撞 RigidBody 箱子 | 箱子被推动 ≥ 0.5 米,角色不陷进箱子 —— 靠『推开刚体』 |
| **多碰撞体** | 头 / 身 / 腿三块的碰撞体 | 三块都在**摆的位置**上:三条射线分别命中,缝里那条**不**命中 |
| **窄缝** | 1.4 米宽的缝(角色 1.0 宽) | 能穿过去,不卡住 |
| **薄墙** | 0.25 米厚的墙 | 全速撞 2.5 秒**不穿透** |
| **移动平台** | 时间轴(PINGPONG)横着推的 AnimatableBody 平台 | 站上去**被带着走**(位移 = 平台位移)—— StaticBody 是"瞬移",人会留在原地 |
| **载具** | CharacterBody 车 + E 上下车 + W/S 前后 · A/D 转车头 | 上车后角色被钉在座位上跟着车走;下车后角色能走、车不跟着跑 |
| **绕墙追击** | 『移动_寻路』追墙那头的玩家(第三十四轮) | **绕过墙**追到人(从墙端过去)—— 直线追会贴墙卡住 |

跑法:
    blender -b --python scripts\\make_physics_demo.py
自动检查(不用 Blender,要 Godot):`python scripts\\check_physics_demo.py`
"""

import math
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "examples", "demo_physics")
BLEND_PATH = os.path.join(OUT_DIR, "物理测试场.blend")

PLAYER = "小玩家"
# 出生点:**Blender 坐标**(角色站在 Blender y=-6 处,朝 +Y 走)
PLAYER_START_Y = -6.0
PLAYER_Z = 0.9
# 工位间距(每个工位一个 X,障碍物在 +Y 方向)
STATION_STEP = 7.0
STATION_X = [index * STATION_STEP for index in range(10)]
# ⚠ 坐标换算:Godot(x, y, z) = Blender(x, z, -y) —— 所以
# "在 Godot 里往 -Z 走"(按 W,相机 yaw=0)在 Blender 里就是**往 +Y 走**。
# 这一关所有障碍物都摆在角色的 +Y 方向,探针只要按 W 就能撞上。

RAMP_ANGLE = 20.0          # 斜坡角度(度)< Godot 的 floor_max_angle(45°)
# 台阶(第二十九轮):低台阶 + 高台阶。角色预设开着『上台阶高度』0.5 ——
# 0.35 的该迈上去;从低台阶到高台阶差 0.7(> 0.5)该**仍然挡住**。
STEP_HEIGHTS = (0.35, 1.05)
BOX_MASS = 2.0             # 刚体箱子的质量
DIAMOND_AT = 3.0           # 对角方块:斜放的那个的 Y(角朝着角色)
# 移动平台(第三十轮):时间轴 PINGPONG 推着它从 PLATFORM_FROM 走到 TO
PLATFORM_FROM = 47.0
PLATFORM_TO = 51.0
PLATFORM_TIME = 2.0        # 单程秒数(往返一轮 4 秒,探针窗口里至少跑完一趟)
PLATFORM_TOP = 0.4         # 平台高度(上表面 = 这个数)
VEHICLE_SPEED = 10.0       # 载具速度(米/秒,『载具_驾驶』里填的那个)
# 第 ⑩ 工位:绕墙追击(第三十四轮:寻路)────────────────────────────
# 一堵 **5 米宽**的墙把『追踪者』和玩家隔开(玩家在 Godot z=+2,追踪者在
# z=-2):直线只有 4 米,可中间是墙 —— 想碰到人只能从墙端绕过去。
NAV_WALL_SPAN = 5.0        # 墙宽(Godot x 方向)
NAV_WALL_THICK = 0.5       # 墙厚
NAV_WALL_HEIGHT = 2.2
NAV_CHASE_SPEED = 4.5      # 追踪者速度(米/秒;绕一圈约 9 米 -> 2 秒多)
NAV_STOP = 1.2             # 贴到多近算"追上了"


def log(message):
    print(f"[物理] {message}")


def _apply_settings(scene, pairs):
    """把一批导出设置写进场景的 gk_settings(缺属性 / 只读就跳过)。

    和 make_demo_level 里那个同款:老版本插件 / 精简环境里某个属性可能
    不存在,写设置绝不该让整个脚本崩掉。
    """
    settings = getattr(scene, "gk_settings", None)
    if settings is None:
        return
    for attr, value in pairs:
        if hasattr(settings, attr):
            try:
                setattr(settings, attr, value)
            except Exception:                             # noqa: BLE001
                pass


def clear_scene():
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for group in list(bpy.data.node_groups):
        if getattr(group, "bl_idname", "").startswith("GK"):
            bpy.data.node_groups.remove(group)
    scene.name = "物理测试场"
    return scene


def make_box(name, size, location, color=None, scene=None, rotation=None):
    scene = scene or bpy.context.scene
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.active_object
    obj.name = name
    # ⚠ 基础方块是 1×1×1,scale 直接就是**目标尺寸**(别再乘 0.5)
    obj.scale = (size[0], size[1], size[2])
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if rotation is not None:
        obj.rotation_euler = rotation
    if color is not None:
        material = bpy.data.materials.new(f"{name}_材质")
        material.use_nodes = True
        principled = material.node_tree.nodes.get("Principled BSDF")
        if principled is not None:
            principled.inputs["Base Color"].default_value = color
        obj.data.materials.append(material)
    return obj


def make_sun(rotation=(50.0, 0.0, 30.0)):
    light_data = bpy.data.lights.new("太阳光数据", type="SUN")
    light_data.energy = 3.0
    light = bpy.data.objects.new("太阳光", light_data)
    bpy.context.scene.collection.objects.link(light)
    light.rotation_euler = tuple(math.radians(value) for value in rotation)
    return light


def make_camera(name, location, rotation):
    data = bpy.data.cameras.new(f"{name}数据")
    camera = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = location
    camera.rotation_euler = rotation
    return camera


# ==========================================================================
# 场景:七个工位
# ==========================================================================
def build_scene():
    scene = clear_scene()
    # 地面:盖住全部工位(x 到 44 以外)
    make_box("地面", (110.0, 44.0, 0.4), (STATION_X[-1] / 2, 0.0, -0.2),
             (0.30, 0.36, 0.42, 1.0))
    wall_color = (0.5, 0.5, 0.55, 1.0)

    ramp_x = STATION_X[0]
    # ── ① 斜坡(向 +Y 升高 20°)────────────────────────────────────
    # 低端的上表面正好落在 z≈0(地面高度),角色走上去直接"上坡"
    make_box("斜坡", (4.0, 6.5, 0.5), (ramp_x, 1.5, 0.85),
             (0.35, 0.5, 0.75, 1.0), rotation=(math.radians(RAMP_ANGLE), 0.0, 0.0))

    # ── ② 台阶(三级)─────────────────────────────────────────────
    stairs_x = STATION_X[1]
    depth = 1.6
    for index, height in enumerate(STEP_HEIGHTS):
        near = 0.5 + index * depth
        make_box(f"台阶{index + 1}", (4.0, depth, height),
                 (stairs_x, near + depth / 2.0, height / 2.0),
                 (0.55, 0.5, 0.4, 1.0))

    # ── ③ 对角方块:一个**转 45°**(角朝着角色)+ 一个斜对角重叠 ────
    diagonal_x = STATION_X[2]
    make_box("斜方块", (2.0, 2.0, 2.0), (diagonal_x, DIAMOND_AT, 1.0),
             (0.8, 0.3, 0.3, 1.0), rotation=(0.0, 0.0, math.radians(45.0)))
    # 和斜方块的**角**斜对角重叠(用户报的"与正方体对角上重叠")
    make_box("叠方块", (2.0, 2.0, 2.0), (diagonal_x + 1.6, DIAMOND_AT + 1.4, 1.0),
             (0.7, 0.35, 0.35, 1.0))

    # ── ④ 刚体箱子(推箱子)────────────────────────────────────────
    push_x = STATION_X[3]
    box = make_box("箱子", (1.2, 1.2, 1.2), (push_x, 2.5, 0.6),
                   (0.85, 0.65, 0.25, 1.0))

    # ── ⑤ 多碰撞体宿主(本体 + 头 / 腿两块)─────────────────────────
    # 本体是"躯干"(细一点),头在头顶、腿在脚下 —— 三块之间有**明显的缝**:
    # 探针的射线"命中三块、漏掉两条缝"才说明三块真的摆在摆的位置上。
    multi_x = STATION_X[4]
    body = make_box("拼装人", (0.6, 0.6, 1.0), (multi_x, 2.5, 1.6),
                    (0.4, 0.6, 0.5, 1.0))
    head = make_box("拼装人头", (0.7, 0.7, 0.7), (multi_x, 2.5, 2.45),
                    (0.4, 0.6, 0.5, 1.0))
    legs = make_box("拼装人腿", (1.0, 1.0, 0.8), (multi_x, 2.5, 0.4),
                    (0.4, 0.6, 0.5, 1.0))

    # ── ⑥ 窄缝(1.6 米宽,角色 1.0 宽 —— 每边留 0.3 的余量)────────────
    narrow_x = STATION_X[5]
    for name, offset in (("窄缝左墙", -1.0), ("窄缝右墙", 1.0)):
        make_box(name, (0.4, 4.0, 3.0), (narrow_x + offset, 3.0, 1.5),
                 wall_color)

    # ── ⑦ 薄墙(0.25 米厚,防穿透)─────────────────────────────────
    thin_x = STATION_X[6]
    make_box("薄墙", (4.0, 0.25, 3.0), (thin_x, 3.0, 1.5), wall_color)

    # ── ⑧ 移动平台(第三十轮:时间轴推着横着走,人要跟着走)────────
    # 平台做成 **AnimatableBody**(见 write_physics):被时间轴每帧挪位置时
    # 它的速度会传给站在上面的角色;StaticBody 是"瞬移",人留在原地。
    platform_x = STATION_X[7]
    platform = make_box("移动平台", (4.0, 4.0, PLATFORM_TOP),
                        (platform_x, -1.5, PLATFORM_TOP / 2.0),
                        (0.45, 0.65, 0.5, 1.0))

    # ── ⑨ 可驾驶载具(第三十轮:CharacterBody + 上/下车 + 驾驶)──────
    # 车头 = Godot 的 -Z = Blender 的 +Y:按 W 就往"角色来"的方向开走。
    vehicle_x = STATION_X[8]
    vehicle = make_box("载具", (3.0, 4.0, 1.2), (vehicle_x, 1.0, 0.6),
                       (0.75, 0.4, 0.3, 1.0))

    # ── ⑩ 绕墙追击(第三十四轮:寻路)──────────────────────────────
    # 墙在 Godot z=0(Blender y=0),追踪者在 Godot z=-2(Blender y=+2),
    # 玩家那一侧是 Godot z=+2 —— 中间的直线被墙挡死(探针把玩家放在那一侧)。
    nav_x = STATION_X[9]
    make_box("导航墙", (NAV_WALL_SPAN, NAV_WALL_THICK, NAV_WALL_HEIGHT),
             (nav_x, 0.0, NAV_WALL_HEIGHT / 2.0), wall_color)
    chaser = make_box("追踪者", (1.2, 1.2, 1.6), (nav_x, 2.0, 0.8),
                      (0.85, 0.35, 0.9, 1.0))

    player = make_box(PLAYER, (1.0, 1.0, 1.8),
                      (STATION_X[0], PLAYER_START_Y, PLAYER_Z),
                      (0.95, 0.55, 0.25, 1.0))

    # 相机:yaw 都为 0(前方 = Godot 的 -Z = Blender 的 +Y)—— 按 W 就是往
    # 障碍物那边走,探针的方向是确定的。
    side = make_camera("侧视机位", (STATION_X[3], -16.0, 5.0),
                       (math.radians(72.0), 0.0, 0.0))
    make_camera("俯瞰机位", (STATION_X[3], -9.0, 18.0),
                (math.radians(25.0), 0.0, 0.0))
    scene.camera = side
    make_sun()
    return scene, player, box, body, (head, legs), platform, vehicle, chaser


def write_physics(scene, player, box, body, pieces, platform, vehicle, chaser):
    """物理类型:全部静态障碍 + 一个刚体箱子 + 角色;碰撞块挂到宿主上。"""
    for obj in scene.objects:
        physics = getattr(obj, "gk_physics", None)
        if physics is None:
            continue
        try:
            physics.shape_size = (0.0, 0.0, 0.0)      # 全 0 = 按模型自动贴合
        except Exception:                                 # noqa: BLE001
            pass
        if obj is player:
            physics.body_type = "CharacterBody3D"
            physics.dimension = "3"
            physics.script_target = True
        elif obj is box:
            # 刚体:会被『推开刚体』推着走(质量 2,推起来更"沉")
            physics.body_type = "RigidBody3D"
            physics.dimension = "3"
            physics.mass = BOX_MASS
        elif obj is platform:
            # 会动的平台必须 **AnimatableBody**:站上去才会被带走
            physics.body_type = "AnimatableBody3D"
            physics.dimension = "3"
            # 网格要跟着碰撞体走 -> 当"资产"导出(Model 子节点复用 glb)
            physics.asset_name = "移动平台"
        elif obj is vehicle:
            # 载具:CharacterBody(和角色同款,『载具_驾驶』用 velocity 开它)
            physics.body_type = "CharacterBody3D"
            physics.dimension = "3"
            physics.asset_name = "载具"
        elif obj is chaser:
            # 追踪者(第三十四轮):角色体 + **不**当资产 —— 它的网格留在
            # 世界 glb 里,和碰撞代理按名字配对(和小游戏示例的敌2 同款)。
            physics.body_type = "CharacterBody3D"
            physics.dimension = "3"
        elif obj in pieces:
            # 『作为父级的碰撞块』:网格不进 glb,形状并进宿主
            physics.body_type = "StaticBody3D"
            physics.dimension = "3"
            physics.collision_piece = True
        elif getattr(obj, "type", "") == "MESH":
            physics.body_type = "StaticBody3D"
            physics.dimension = "3"
    for piece in pieces:
        piece.parent = body
        piece.matrix_parent_inverse = body.matrix_world.inverted()


# ==========================================================================
# 节点图(角色预设 + 提示 + Esc)
# ==========================================================================
def build_graph(scene, player):
    from BlenderGameKit import presets

    logic, _anim, notes = presets.create_character_preset(
        scene, character=player,
        logic_name="物理测试逻辑",
        class_name="PhysicsProbePlayer",
        with_animation=False,
        with_godot_camera=True,
        camera_side="侧视机位", camera_top="俯瞰机位",
    )
    for note in notes:
        log(f"预设: {note}")
    physics = _by_name(logic, "每当_物理帧")
    _tune_move_node(logic)
    _add_field_extras(logic)
    _add_nav_station(logic, physics)
    _hint_chain(logic, physics)
    return logic


def _add_nav_station(tree, physics):
    """第 ⑩ 工位(第三十四轮):追玩家的人 —— 它要**绕墙**才能碰到人。

    ── 为什么要有『追击开关』──────────────────────────────────────
    追踪者一进游戏就追的话,它会跟着玩家跑遍前九个工位,把推箱子 / 窄缝 /
    载具那些判据全搅乱(探针是**按物理现象**断言的)。所以默认关着,
    探针开始第 ⑩ 工位时把变量置真(`host.set("追击开关", true)`)——
    这比"按时间/坐标判断"稳,也不会让别的工位受一点影响。

    ── 为什么用『移动_寻路』而不是『移动_朝向目标』──────────────────
    直线追的结果是**贴着墙原地蹭**(走到墙前就停住)——那正是这一轮要
    给大家看的对比:同一个"追人",一个卡墙、一个绕过去。
    """
    var = tree.gk_vars.add()
    var.var_name = "追击开关"
    var.var_type = "bool"
    var.default_value = "false"
    var.comment = "第 ⑩ 工位:让『追踪者』开始追(探针置真)"
    var.group = "测试场"

    read = _add(tree, "GKNodeGetVar", "读追击开关", 200.0, 1520.0,
                gk_var_name="追击开关")
    branch = _add(tree, "GKNodeBranch", "开始追?", 360.0, 1520.0)
    chase = _add(tree, "GKNodeNavigate", "追踪者绕墙追人", 600.0, 1520.0,
                 gk_target="追踪者", gk_goal=PLAYER, gk_speed=NAV_CHASE_SPEED,
                 gk_stop_distance=NAV_STOP, gk_turn=True, gk_accel=8.0)
    _link(tree, physics, "执行", branch, "执行")
    _link(tree, read, "值", branch, "条件")
    _link(tree, branch, "是", chase, "执行")
    log("测试场:⑩ 追踪者(『移动_寻路』绕墙,探针用『追击开关』开门)已接好")


def _add_field_extras(tree):
    """第八/九工位要的节点:平台的时间轴 + 载具的上/下车与驾驶。

    ── 平台怎么动(用现成的『定时_时间轴』)─────────────────────────
    时间轴(PINGPONG)**一进游戏就开**,往返模式永不停;每帧『更新』把
    轨道值写到平台的 X 上(Y / Z 用平台**当前**坐标 —— 所以只横着走)。
    和示例里"抬门"一模一样的套路(见 make_curve_demo)。

    ── 载具:一个键上车/下车(两层分支,别省)──────────────────────
    先判『按了 E 吗』(外层),再判『在开吗』(内层)→ 是 = 下车,否 = 上车。
    ⚠ 不能写成"E 且 驾驶中 → 如果_否则":那个『否』口**每帧**都会走
    (没按 E 的帧条件也是假)→ 刚下车就被下一帧的"否"塞回座位
    (探针实测:ride_gap 恒 0、怎么按都下不了车)。
    『载具_驾驶』接在角色『移动_按方向』**后面** —— 钉座位那一下必须
    在角色被移动之后,不然会被角色的移动覆盖掉。
    """
    setup = _by_name(tree, "设定初始视野")
    timeline = _add(tree, "GKNodeTimeline", "平台时间轴", 0.0, 760.0,
                    gk_time=PLATFORM_TIME, gk_mode="PINGPONG",
                    gk_track_count=1,
                    gk_track1_from=PLATFORM_FROM, gk_track1_to=PLATFORM_TO)
    _link(tree, setup, "完成", timeline, "执行")
    platform_here = _add(tree, "GKNodeGetPosition", "取平台位置", -560.0, 720.0,
                         gk_target="移动平台", gk_global=True)
    platform_move = _add(tree, "GKNodeSetPosition", "推平台", -280.0, 720.0,
                         gk_target="移动平台")
    _link(tree, timeline, "轨道1值", platform_move, "X")
    _link(tree, platform_here, "Y", platform_move, "Y")
    _link(tree, platform_here, "Z", platform_move, "Z")
    _link(tree, timeline, "更新", platform_move, "执行")

    move = _by_name(tree, "移动_按方向")
    direction = _by_name(tree, "输入_方向")
    drive = _add(tree, "GKNodeDriveVehicle", "驾驶载具", 0.0, 940.0,
                 gk_vehicle="载具", gk_speed=VEHICLE_SPEED)
    _link(tree, move, "完成", drive, "执行")
    _link(tree, direction, "方向", drive, "方向")

    key_e = _add(tree, "GKNodeKey", "E 键", -760.0, 1160.0, gk_key="E",
                 gk_action="key_e", gk_trigger="JUST")
    pressed = _add(tree, "GKNodeBranch", "按了 E?", -560.0, 1160.0)
    driving = _add(tree, "GKNodeBranch", "在开吗?", -320.0, 1160.0)
    leave = _add(tree, "GKNodeExitVehicle", "下车", -60.0, 1260.0,
                 gk_vehicle="载具", gk_exit=(2.6, 0.5, 0.0))
    enter = _add(tree, "GKNodeEnterVehicle", "上车", -60.0, 1060.0,
                 gk_vehicle="载具", gk_seat=(0.0, 2.0, 0.0))
    _link(tree, key_e, "结果", pressed, "条件")
    _link(tree, drive, "完成", pressed, "执行")
    _link(tree, pressed, "是", driving, "执行")
    _link(tree, drive, "驾驶中", driving, "条件")
    _link(tree, driving, "是", leave, "执行")
    _link(tree, driving, "否", enter, "执行")
    log("测试场:平台时间轴(PINGPONG)+ 载具(E 上车/下车)已接好")


def _tune_move_node(logic):
    """把角色预设的『移动_按方向』调成测试场要的样子:开相机朝向 + 开上台阶。

    ── 『随相机朝向』为什么改成**开**(用户 2026-10-06)────────────
    第二十八轮为了"探针方向确定"把它关掉了(那时弹簧臂有跟随平滑,角色被
    传送到工位时相机还没追上,方向是斜的,实测偏航 -28°~-74°)。
    用户实测后要求"按 W 就该往镜头前方走"—— 现在探针每个工位开始时把
    **『视角偏航』钉回 0**(见 check_physics_demo 的 _start_station),
    方向依旧是确定的世界 -Z,而人玩的时候 W 老老实实跟着镜头走。

    ── 为什么要开『上台阶高度』(第二十九轮)──────────────────────
    台阶工位就是按这个高度设计的:0.5 能把 0.35 的低台阶迈上去,
    1.05 的高台阶差 0.7,迈不上去 —— "能上一级、挡在第二级前"。
    """
    move = _by_name(logic, "移动_按方向")
    move.gk_follow_camera = True
    log("测试场:『移动_按方向』开了『随相机朝向』(探针每站把偏航钉回 0)")
    move.gk_step_height = 0.5
    log("测试场:『上台阶高度』= 0.5(低台阶 0.35 能上 / 高台阶 1.05 挡住)")


def _add(tree, kind, name, x, y, **props):
    node = tree.nodes.new(kind)
    node.name = name
    node.location = (x, y)
    for key, value in props.items():
        setattr(node, key, value)
    return node


def _link(tree, source, source_pin, target, target_pin):
    tree.links.new(source.outputs[source_pin], target.inputs[target_pin])


def _by_name(tree, name):
    node = tree.nodes.get(name)
    if node is None:
        raise KeyError(f"节点图里没有『{name}』")
    return node


def _hint_chain(tree, physics):
    """提示文字 + Esc 退出(探针不看这些,人在 Godot 里看)。"""
    hint = _add(tree, "GKNodeUiText", "操作提示", -300.0, 980.0,
                gk_text=("WASD 移动 / 空格 跳 / 鼠标转视角 / C·V 切机位 / "
                         "Tab 上帝机 / E 上·下车 / Esc 退出 —— "
                         "十个工位摆在 +Y 方向"),
                gk_x=18.0, gk_y=18.0, gk_size=18)
    goal = _add(tree, "GKNodeUiText", "工位说明", -300.0, 820.0,
                gk_text=("工位(从近到远):斜坡 / 台阶 / 对角方块 / 推箱子 / "
                         "多碰撞体 / 窄缝 / 薄墙 / 移动平台 / 载具 / 绕墙追击"),
                gk_x=18.0, gk_y=46.0, gk_size=18)
    setup = _by_name(tree, "设定初始视野")
    _link(tree, setup, "完成", hint, "执行")
    _link(tree, hint, "完成", goal, "执行")

    key_esc = _add(tree, "GKNodeKey", "Esc 键", -300.0, 640.0, gk_key="ESC",
                   gk_action="key_esc", gk_trigger="JUST")
    branch = _add(tree, "GKNodeBranch", "按了 Esc?", -100.0, 640.0)
    quit_game = _add(tree, "GKNodeQuitGame", "退出游戏", 120.0, 640.0)
    _link(tree, key_esc, "结果", branch, "条件")
    _link(tree, physics, "执行", branch, "执行")
    _link(tree, branch, "是", quit_game, "执行")


def save_blend(path):
    """保存 .blend(无头模式下用 libraries.write)。"""
    if not bpy.app.background:
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return
    blocks = set(bpy.data.objects) | set(bpy.data.meshes) | \
        set(bpy.data.materials) | set(bpy.data.curves) | \
        set(bpy.data.node_groups) | set(bpy.data.cameras) | set(bpy.data.lights)
    scenes = list(bpy.data.scenes)
    bpy.data.libraries.write(path, blocks | set(scenes), fake_user=True)


def main():
    from BlenderGameKit import asset_export, project

    os.makedirs(OUT_DIR, exist_ok=True)
    scene, player, box, body, pieces, platform, vehicle, chaser = build_scene()
    write_physics(scene, player, box, body, pieces, platform, vehicle, chaser)
    build_graph(scene, player)
    settings = scene.gk_settings
    settings.god_camera = True
    # ── 寻路(第三十四轮)────────────────────────────────────────────
    # 第 ⑩ 工位靠它绕墙:导出时会写一块 NavigationMesh(只解析静态碰撞体),
    # 环境脚本启动时烤。参数和角色的碰撞半径对齐(0.5)。
    _apply_settings(scene, (
        ("nav_enabled", True),
        ("nav_agent_radius", 0.5),
        ("nav_agent_height", 1.8),
        ("nav_max_slope", 45.0),
    ))

    directory = os.path.join(OUT_DIR, "物理测试场_godot")
    project.create_project(directory, title="物理测试场",
                           blend_file_name=os.path.basename(BLEND_PATH))
    result = asset_export.export_all_scenes(directory, dimension="3")
    if result.get("error"):
        log(f"导出失败: {result['error']}")
        return 1
    for entry in result.get("scenes", []):
        log(f"场景 {entry['scene']} -> {entry['scene_name']}.tscn"
            f"(脚本 {entry['script_name']})")
    for warning in result.get("warnings", []):
        log(f"告警: {warning}")
    if result.get("deleted"):
        log(f"清理旧文件: {result['deleted']}")
    log(f"工程目录: {directory}")

    save_blend(BLEND_PATH)
    log(f"已保存 {BLEND_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())