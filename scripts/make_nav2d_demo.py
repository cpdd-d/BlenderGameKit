"""2D 寻路示例(第三十六轮:寻路第二版)—— 俯视 2D 里**真绕障碍**追人。

用法:
    blender -b --gpu-backend vulkan --python scripts\\make_nav2d_demo.py
产出:
    examples/demo_nav2d/2D寻路示例.blend
    examples/demo_nav2d/2D寻路示例_godot/     (可用 Godot 直接打开)

── 这一关在看什么 ────────────────────────────────────────────────
俯视 2D 场景(不是横版平台):中间一堵 10 米宽的障碍,把『追踪者』和
『小玩家』隔开 —— 直线只有 12 米,但走不通。追踪者挂『移动_寻路』:

  * 场景设置里勾了『寻路』-> 启动时按**静态碰撞体**烤一块 NavigationPolygon
    (2D 的烘焙语义和 3D 不一样:碰撞体是**障碍**,先按所有障碍的包围盒
    算一个"外框"再烤 —— 详见环境脚本里的说明);
  * 追踪者每帧查 NavigationServer2D 的绕行路径 -> **从障碍两端绕过去**
    追到玩家(直线追只会一头顶在障碍上)。

── 第八十二轮:2D 避障真机探针用的两对对撞车道 ────────────────────
场地里还有四个人分成两对,从 x=±5 对走(终点是对方那头的小标记):

  * **避障对**(避障甲 / 避障乙,y=+3):勾了『避障』—— NavigationAgent2D
    的 RVO 让两人**互相绕开**,都走到终点;
  * **对照对**(对照丙 / 对照丁,y=-3):同一套设置**不勾避障** ——
    两人正对撞上就顶死在中点(证明"绕开"是避障干的,不是碰巧)。

⚠ 终点标记用 **Area2D**:2D 烘焙只认 StaticBody2D,标记要是 StaticBody2D
  就会被挖成障碍(终点变成不可达)。

── 2D 寻路的注意事项(和 3D 的唯一区别)──────────────────────────
**2D 里碰撞体 = 障碍物**。所以给"墙 / 箱子 / 障碍"加碰撞体,给"地面 /
地板"**不要**加(整块地板会被当成一个巨大的障碍挖掉) —— 俯视 2D 游戏
本来也不需要给地板加碰撞。

跑法(Godot):打开 `2D寻路示例_godot` 直接 F5。没有相机也能看到画面
(2D 默认视图就在原点);想看全图在 Godot 里加一台 Camera2D 即可。
自动检查(不用 Blender,要 Godot):`python scripts\\check_nav2d_demo.py`
"""

import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "examples", "demo_nav2d")
BLEND_PATH = os.path.join(OUT_DIR, "2D寻路示例.blend")

PLAYER = "小玩家"
CHASER = "追踪者"
# 场地(Blender 的 XY 平面 = 2D 世界的平面;Z 轴在 2D 里没有意义)
ARENA_HALF_X = 12.0
ARENA_HALF_Y = 8.0
WALL_THICK = 1.0
OBSTACLE_HALF = 5.0        # 中间障碍半宽(10 米宽,2 米厚)
CHASE_SPEED = 3.0          # 追踪者速度(2D 单位/秒)
NAV_RADIUS = 0.5           # 寻路代理半径(和角色半宽一致)
CHASER_START = (0.0, 6.0)
PLAYER_START = (0.0, -6.0)
# ── 第八十二轮:2D 避障(AVOID)真机探针用的两对"对撞"车道 ────────────
# 每对两个人从 x=±LANE_X 对走(终点是对方那头的小标记)。上面一对勾
# 『避障』(NavigationAgent2D 的 RVO),下面一对**不勾** —— 探针拿两对
# 做对照:勾了的那对要**互相绕开、都到达**;不勾的那对会**顶死在中点**。
LANE_X = 5.0               # 车道两端(离中心 5 米)
LANE_SPEED = 3.5           # 车道速度(比追踪者快一点,把探针窗口压短)
LANE_STOP = 0.5            # 到点多近算到了
AVOID_RADIUS = 0.6         # 避障半径(RVO 里"我占多大地方")
AVOID_LANE_Y = 3.0         # 勾避障那对的车道
PLAIN_LANE_Y = -3.0        # 不勾避障(对照)那对的车道
# ── 第八十三轮:动态障碍(NavigationObstacle)─────────────────────────
# 两条车道正中各摆一个"幽灵障碍":物体是 **Area2D**(没有物理碰撞,
# 谁也撞不到它),但在『游戏物理』里勾了『动态障碍』-> 导出时挂一个
# NavigationObstacle2D —— **勾了避障的人会绕开它,不勾的直接穿过去**。
# 探针还会每帧把它沿 x 来回挪(模拟会动的柱子),证明不用重烤导航网格。
GHOST_RADIUS = 0.8         # 幽灵障碍半径(避障系统按它保距)
GHOST_SWING = 1.0          # 探针来回挪的幅度(米)
GHOST_NAME_A = "幽灵甲"
GHOST_NAME_B = "幽灵乙"


def log(message):
    print(f"[2D寻路] {message}")


def _apply_settings(scene, pairs):
    """把一批导出设置写进场景的 gk_settings(缺属性 / 只读就跳过)。"""
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
    scene.name = "2D寻路示例"
    return scene


def make_box(name, size, location, color=None, scene=None):
    scene = scene or bpy.context.scene
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (size[0], size[1], size[2])
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if color is not None:
        material = bpy.data.materials.new(f"{name}_材质")
        material.use_nodes = True
        principled = material.node_tree.nodes.get("Principled BSDF")
        if principled is not None:
            principled.inputs["Base Color"].default_value = color
        obj.data.materials.append(material)
    return obj


def build_scene():
    """俯视布局:四面墙围一块场地,中间一堵 10 米宽的障碍。"""
    scene = clear_scene()
    wall_color = (0.6, 0.6, 0.65, 1.0)
    # 四面墙(把场地围起来 —— 2D 里它们就是"不可走"的障碍)
    make_box("墙_上", (ARENA_HALF_X * 2 + WALL_THICK * 2, WALL_THICK, 1.0),
             (0.0, ARENA_HALF_Y + WALL_THICK * 0.5, 0.0), wall_color)
    make_box("墙_下", (ARENA_HALF_X * 2 + WALL_THICK * 2, WALL_THICK, 1.0),
             (0.0, -ARENA_HALF_Y - WALL_THICK * 0.5, 0.0), wall_color)
    make_box("墙_左", (WALL_THICK, ARENA_HALF_Y * 2, 1.0),
             (-ARENA_HALF_X - WALL_THICK * 0.5, 0.0, 0.0), wall_color)
    make_box("墙_右", (WALL_THICK, ARENA_HALF_Y * 2, 1.0),
             (ARENA_HALF_X + WALL_THICK * 0.5, 0.0, 0.0), wall_color)
    # 中间障碍:10 米宽 —— 直线过不去,必须绕两端
    make_box("中间障碍", (OBSTACLE_HALF * 2.0, 2.0, 1.0), (0.0, 0.0, 0.0),
             (0.75, 0.5, 0.35, 1.0))
    chaser = make_box(CHASER, (1.0, 1.0, 1.0),
                      (CHASER_START[0], CHASER_START[1], 0.0),
                      (0.85, 0.35, 0.4, 1.0))
    player = make_box(PLAYER, (1.0, 1.0, 1.0),
                      (PLAYER_START[0], PLAYER_START[1], 0.0),
                      (0.4, 0.65, 0.9, 1.0))
    # ── 第八十二轮:两对对撞的人 + 四个小终点标记 ──────────────────
    # 标记给 **Area2D** 是有意的:2D 烘焙只认 StaticBody2D(见环境脚本),
    # 但标记要是 StaticBody2D 就会被挖成障碍 —— 改用 Area2D 就既不挡路、
    # 也不进烘焙,只提供"终点坐标"。
    avoid_color = (0.9, 0.75, 0.3, 1.0)
    plain_color = (0.55, 0.6, 0.7, 1.0)
    marker_color = (0.45, 0.9, 0.5, 1.0)
    lanes = {}
    for left, right, goal_left, goal_right, y, color in (
            ("避障甲", "避障乙", "终点甲", "终点乙",
             AVOID_LANE_Y, avoid_color),
            ("对照丙", "对照丁", "终点丙", "终点丁",
             PLAIN_LANE_Y, plain_color)):
        lanes[left] = make_box(left, (1.0, 1.0, 1.0), (-LANE_X, y, 0.0), color)
        lanes[right] = make_box(right, (1.0, 1.0, 1.0), (LANE_X, y, 0.0), color)
        lanes[goal_left] = make_box(goal_left, (0.6, 0.6, 1.0),
                                    (LANE_X, y, 0.0), marker_color)
        lanes[goal_right] = make_box(goal_right, (0.6, 0.6, 1.0),
                                     (-LANE_X, y, 0.0), marker_color)
    # 幽灵障碍(第八十三轮):两条车道正中各一个(Area2D + 动态障碍)
    ghost_color = (0.95, 0.45, 0.95, 1.0)
    lanes[GHOST_NAME_A] = make_box(
        GHOST_NAME_A, (1.2, 1.2, 1.0), (0.0, AVOID_LANE_Y, 0.0), ghost_color)
    lanes[GHOST_NAME_B] = make_box(
        GHOST_NAME_B, (1.2, 1.2, 1.0), (0.0, PLAIN_LANE_Y, 0.0), ghost_color)
    log(f"幽灵障碍:{GHOST_NAME_A} / {GHOST_NAME_B}(半径 {GHOST_RADIUS:g},"
        f"无物理碰撞;探针会沿 x 来回挪 ±{GHOST_SWING:g})")
    log(f"对撞车道:避障对 y={AVOID_LANE_Y:g}(甲/乙) / "
        f"对照对 y={PLAIN_LANE_Y:g}(丙/丁);两端 x=±{LANE_X:g}")
    log(f"场景:场地 {ARENA_HALF_X * 2:.0f}x{ARENA_HALF_Y * 2:.0f} / "
        f"中间障碍 {OBSTACLE_HALF * 2:.0f} 米宽 / "
        f"追踪者 {CHASER_START} 追玩家 {PLAYER_START}")
    return scene, chaser, player, lanes


def write_physics(scene, chaser, player, lanes):
    """2D 物理:墙/障碍 = StaticBody2D(会被烤成导航障碍);
    追踪者 = CharacterBody2D(挂脚本);玩家 = CharacterBody2D(不参与烘焙);
    对撞的人 = CharacterBody2D(不挂脚本,由同一张图按名字驱动);
    终点标记 = Area2D(只提供坐标,不挡路也不进烘焙)。"""
    for obj in scene.objects:
        physics = getattr(obj, "gk_physics", None)
        if physics is None:
            continue
        try:
            physics.shape_size = (0.0, 0.0, 0.0)       # 全 0 = 按模型自动贴合
        except Exception:                              # noqa: BLE001
            pass
        physics.dimension = "2"
        if obj is chaser:
            physics.body_type = "CharacterBody2D"
            physics.script_target = True
            log("追踪者:CharacterBody2D + 挂脚本(『移动_寻路』)")
        elif obj is player:
            physics.body_type = "CharacterBody2D"
            log("玩家:CharacterBody2D(静止目标;不参与导航烘焙)")
        elif obj.name in lanes:
            if obj.name.startswith("终点"):
                physics.body_type = "Area2D"
            elif obj.name.startswith("幽灵"):
                # 幽灵障碍(第八十三轮):Area2D(没有物理碰撞)+ 动态障碍 ——
                # 只有"避障"会理它,所以对照对会直接穿过去(探针拿这个当证据)
                physics.body_type = "Area2D"
                physics.nav_obstacle = True
                physics.nav_obstacle_radius = GHOST_RADIUS
            else:
                physics.body_type = "CharacterBody2D"
        elif getattr(obj, "type", "") == "MESH":
            physics.body_type = "StaticBody2D"


def build_graph(scene):
    """图:每当_物理帧 -> 『移动_寻路』(追踪者绕开障碍追玩家)。

    2D 里『移动_寻路』查的是 NavigationServer2D 的路径(和 3D 同一套写法),
    速度单位是"2D 单位/秒"。
    """
    tree = bpy.data.node_groups.new("2D寻路逻辑", "GKGameLogicTree")
    physics = _add(tree, "GKNodeOnPhysicsProcess", "每当_物理帧", 0.0, 0.0)
    chase = _add(tree, "GKNodeNavigate", "追踪者绕障追人", 260.0, 0.0,
                 gk_target=CHASER, gk_goal=PLAYER, gk_speed=CHASE_SPEED,
                 gk_stop_distance=1.0, gk_turn=True)
    _link(tree, physics, "执行", chase, "执行")
    # ── 第八十二轮:两对对撞的车道 ──────────────────────────────────
    # 同一张图、同一个脚本驱动四个人:『目标』按名字选到谁就动谁。
    # 上面一对勾『避障』(RVO 互相绕开),下面一对不勾(对照:会顶死)。
    lane_nodes = []
    y = -260.0
    for name, goal, avoid in (
            ("避障甲", "终点甲", True), ("避障乙", "终点乙", True),
            ("对照丙", "终点丙", False), ("对照丁", "终点丁", False)):
        node = _add(tree, "GKNodeNavigate", f"{name}走", 520.0, y,
                    gk_target=name, gk_goal=goal, gk_speed=LANE_SPEED,
                    gk_stop_distance=LANE_STOP, gk_turn=True,
                    gk_avoid=avoid, gk_avoid_radius=AVOID_RADIUS)
        lane_nodes.append(node)
        y -= 140.0
    previous = chase
    for node in lane_nodes:
        _link(tree, previous, "完成", node, "执行")
        previous = node
    log("图:『每当_物理帧』->『移动_寻路』(追踪者追玩家) -> "
        "对撞车道(避障甲 / 避障乙 / 对照丙 / 对照丁)已接好")
    return tree


def _add(tree, kind, name, x, y, **props):
    node = tree.nodes.new(kind)
    node.name = name
    node.location = (x, y)
    for key, value in props.items():
        setattr(node, key, value)
    return node


def _link(tree, source, source_pin, target, target_pin):
    tree.links.new(source.outputs[source_pin], target.inputs[target_pin])


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
    scene, chaser, player, lanes = build_scene()
    write_physics(scene, chaser, player, lanes)
    build_graph(scene)
    # ── 2D + 寻路(第三十六轮)──────────────────────────────────────
    # dimension=2:导出成 NavigationRegion2D + NavigationPolygon;
    # 环境脚本启动时按**静态碰撞体(障碍)**烤 —— 2D 里"碰撞体 = 障碍",
    # 所以只有墙/障碍有碰撞体,地板没有。
    _apply_settings(scene, (
        ("dimension", "2"),
        ("nav_enabled", True),
        ("nav_agent_radius", NAV_RADIUS),
    ))

    directory = os.path.join(OUT_DIR, "2D寻路示例_godot")
    project.create_project(directory, title="2D寻路示例",
                           blend_file_name=os.path.basename(BLEND_PATH))
    result = asset_export.export_all_scenes(directory, dimension="2")
    if result.get("error"):
        log(f"导出失败:{result['error']}")
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