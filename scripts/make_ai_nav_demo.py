"""AI 绕墙追击示例(第六十三轮)—— 『AI_状态』的追击直接走导航网格。

用法:
    blender -b --python scripts\\make_ai_nav_demo.py
产出:
    examples/demo_ai_nav/AI寻路示例.blend
    examples/demo_ai_nav/AI寻路示例_godot/   (可用 Godot 直接打开)

── 这一屏在看什么 ────────────────────────────────────────────────
一堵 **5 米宽的墙**把『敌人』和『玩家』隔开(两者在 Godot 里 z 差 4 米,
直线距离只有 4 米),中间是墙。敌人是纯 AI 驱动(『AI_状态』的**追击**
行为),**没有**接任何『移动_寻路』节点。

* 场景设置里**没勾『寻路』** -> 它直线撞墙,贴住不动(永远追不上);
* 勾了『寻路』 -> 生成的追击代码改问 `NavigationServer3D.map_get_path`
  要路径,它会**从墙端绕过去**追到人。

这就是"寻路整合进 AI"的意思:**AI 的移动行为默认就走导航网格**,
不需要用户给每个追击状态再手动拼一套寻路。

| 用到的 | 在游戏里怎么看 |
|---|---|
| **『AI_状态』(追击)** | 敌人每帧朝玩家走;勾了寻路就绕开墙 |
| **『AI_状态』(逃跑,第八十轮)** | 贴脸 0.9 米就转身逃:朝你反方向投影 8 米,被**第二堵墙**挡住时同样绕墙 |
| **『AI_启用』** | 玩法图里把那张 AI 图挂到『敌人』身上 |
| **场景设置的『寻路』** | 总开关:它决定"有没有导航网格可走"(追击 / 逃跑共用) |
| **『编辑器侧栏』→『导出脚本』→『寻路』** | 勾上它会多烤一块导航网格 |

跑法(Godot):打开 `AI寻路示例_godot` 直接 F5(想飞着看按 Tab)。
自动检查(不用 Blender,要 Godot):`python scripts\\check_ai_nav_demo.py`
"""

import math
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "examples", "demo_ai_nav")
BLEND_PATH = os.path.join(OUT_DIR, "AI寻路示例.blend")

ENEMY = "敌人"
PLAYER = "玩家"
WALL = "导航墙"
FLEE_WALL = "逃跑墙"
AI_TREE = "敌人AI"
LOGIC_TREE = "AI寻路逻辑"

# ── 逃跑(第八十轮 D-11)──────────────────────────────────────────
# 追得太贴(0.9 米)就切『逃跑』:朝玩家反方向投影 8 米外的一个点交给导航。
# 这堵墙横在逃跑路线上 —— 直线逃会贴墙站住,勾了寻路就该从墙端绕过去。
FLEE_TRIGGER = 0.9     # 贴到 0.9 米内触发逃跑(小于追击停距 1.2,不会误触)
FLEE_DISTANCE = 8.0    # 逃到 8 米外
FLEE_STOP = 10.0       # 威胁在 10 米内就一直跑(保证能跑到投影点)
FLEE_BACK_SIGHT = 20.0 # 拉开 20 米才算"又安全了"(探针里不会发生)

# 几何(Blender:Y 轴换到 Godot 的 -Z)────────────────────────────────
# 墙沿 Blender X 铺 5 米,厚 0.5;敌人在 Blender y=+2(Godot z=-2),
# 玩家在 Blender y=-2(Godot z=+2)—— 直线 4 米,中间是墙。
WALL_SPAN = 5.0
WALL_THICK = 0.5
WALL_HEIGHT = 2.2
ENEMY_SPEED = 4.5      # 米/秒(绕一圈约 9 米 -> 2 秒多)
STOP_DISTANCE = 1.2    # 贴到多近算"追上了"
# ── 两个会被"绕墙"这件事放大的尺寸(实测调出来的)──────────────────
# 『敌人』是个 0.8 米见方的盒子:它绕墙时的**半个对角**是 0.57 米 ——
# 导航网格的『半径』必须大于它,否则路径会贴着墙角走,敌人**卡在墙角**
# (第一版半径 0.5 + 敌人 1.2 米宽,实测卡死在 (-2.83, -0.76) 一动不动)。
ENEMY_SIZE = 0.8
NAV_RADIUS = 0.8


def log(message):
    print(f"[AI寻路示例] {message}")


def clear_scene():
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for group in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(group)
    scene.name = "AI寻路示例"
    return scene


def make_box(name, size, location, color=None):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if color is not None:
        material = bpy.data.materials.new(f"{name}_材质")
        material.use_nodes = True
        principled = material.node_tree.nodes.get("Principled BSDF")
        if principled is not None:
            principled.inputs["Base Color"].default_value = color
        obj.data.materials.append(material)
    return obj


def make_camera(name, location, rotation):
    data = bpy.data.cameras.new(f"{name}数据")
    camera = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = location
    camera.rotation_euler = rotation
    return camera


def build_scene():
    scene = clear_scene()
    make_box("地面", (20.0, 20.0, 0.4), (0.0, 0.0, -0.2), (0.16, 0.18, 0.22, 1.0))
    make_box(WALL, (WALL_SPAN, WALL_THICK, WALL_HEIGHT),
             (0.0, 0.0, WALL_HEIGHT / 2.0), (0.55, 0.55, 0.60, 1.0))
    # ── 逃跑墙(第八十轮):横在敌人"背对玩家"的逃跑路线上 ─────────────
    # Blender y=+4(Godot z=-4):敌人被贴脸后朝 -z 逃 8 米,这堵墙挡路,
    # 勾了寻路就该横着绕出 ±2.5 米墙端再折回。
    make_box(FLEE_WALL, (WALL_SPAN, WALL_THICK, WALL_HEIGHT),
             (0.0, 4.0, WALL_HEIGHT / 2.0), (0.60, 0.45, 0.35, 1.0))
    enemy = make_box(ENEMY, (ENEMY_SIZE, ENEMY_SIZE, 1.6), (0.0, 2.0, 0.8),
                     (0.85, 0.35, 0.35, 1.0))
    make_box(PLAYER, (0.8, 0.8, 1.6), (0.0, -2.0, 0.8),
             (0.35, 0.75, 0.45, 1.0))
    side = make_camera("侧视机位", (0.0, -12.0, 8.0),
                       (math.radians(66.0), 0.0, 0.0))
    make_camera("俯瞰机位", (0.0, -2.0, 16.0), (math.radians(18.0), 0.0, 0.0))
    scene.camera = side
    light_data = bpy.data.lights.new("太阳光数据", type="SUN")
    light_data.energy = 3.0
    light = bpy.data.objects.new("太阳光", light_data)
    scene.collection.objects.link(light)
    light.location = (5.0, -5.0, 10.0)
    light.rotation_euler = (math.radians(52.0), 0.0, math.radians(28.0))
    log(f"场景:地面 + {WALL}(5 米宽)+ {ENEMY} / {PLAYER} 隔墙相望 + 相机")
    return scene, enemy


def write_physics(scene, enemy):
    for obj in scene.objects:
        physics = getattr(obj, "gk_physics", None)
        if physics is None:
            continue
        try:
            physics.shape_size = (0.0, 0.0, 0.0)
        except Exception:                                 # noqa: BLE001
            pass
        if obj is enemy:
            # 敌人是**跑 AI 的那一个**:它自己挂脚本,自己 move_and_slide
            physics.body_type = "CharacterBody3D"
            physics.dimension = "3"
            physics.script_target = True
        elif obj.name == PLAYER:
            # 玩家只是"一个坐标":**不给碰撞** —— 它既不挡路,也不会在
            # 导航网格上挖个洞(挖了的话 AI 永远靠不到 1.2 米以内)
            physics.body_type = "Node3D"
            physics.dimension = "3"
        elif getattr(obj, "type", "") == "MESH":
            # 地面 / 墙:静态碰撞体 —— 墙会被烤进导航网格(这就是那堵要绕的墙)
            physics.body_type = "StaticBody3D"
            physics.dimension = "3"


def _add(tree, kind, name, x, y, **props):
    node = tree.nodes.new(kind)
    node.name = name
    node.location = (x, y)
    for key, value in props.items():
        setattr(node, key, value)
    return node


def _link(tree, source, source_pin, target, target_pin):
    tree.links.new(source.outputs[source_pin], target.inputs[target_pin])


def build_ai_tree():
    """AI 图:追击(显式入口)+ 逃跑。

    ⚠ 这里**故意不放『移动_寻路』**:整个示例要证明的是"AI 自己的移动行为
      就走导航网格"。放了寻路节点就说不清了。

    第八十轮:追得太贴(0.9 米)就切『逃跑』 —— FLEE 勾了寻路后,朝威胁
    反方向投影一个 8 米外的点交给同一条 `_gk_ai_nav_dir`,逃跑墙挡路时
    一样绕墙。
    """
    ai = bpy.data.node_groups.new(AI_TREE, "GKGameAiTree")
    ai.use_fake_user = True
    start = _add(ai, "GKNodeAiStart", "开始", -420.0, 0.0)
    chase = _add(ai, "GKNodeAiState", "追击", 0.0, 0.0,
                 gk_state_name="追击",
                 gk_behavior="CHASE",
                 gk_goal=PLAYER,
                 gk_speed=ENEMY_SPEED,
                 gk_stop_distance=STOP_DISTANCE,
                 gk_facing="GOAL")
    flee = _add(ai, "GKNodeAiState", "逃跑", 420.0, 0.0,
                gk_state_name="逃跑",
                gk_behavior="FLEE",
                gk_goal=PLAYER,
                gk_speed=ENEMY_SPEED,
                gk_stop_distance=FLEE_STOP,
                gk_flee_distance=FLEE_DISTANCE,
                gk_facing="GOAL")
    too_close = _add(ai, "GKNodeAiTransition", "贴脸了", 210.0, 200.0,
                     gk_cond_source="SEE", gk_goal=PLAYER,
                     gk_sight=FLEE_TRIGGER)
    safe_again = _add(ai, "GKNodeAiTransition", "又安全了", 630.0, -220.0,
                      gk_cond_source="LOST", gk_goal=PLAYER,
                      gk_sight=FLEE_BACK_SIGHT)
    _link(ai, start, "开始", chase, "进入")
    _link(ai, chase, "转到", too_close, "上游")
    _link(ai, too_close, "转到", flee, "进入")
    _link(ai, flee, "转到", safe_again, "上游")
    _link(ai, safe_again, "转到", chase, "进入 2")
    log(f"AI 图『{AI_TREE}』:追击 --贴脸({FLEE_TRIGGER:g} 米)--> 逃跑"
        f"(投影 {FLEE_DISTANCE:g} 米,走导航绕开『{FLEE_WALL}』)"
        f" --拉开 {FLEE_BACK_SIGHT:g} 米--> 追击")
    return ai


def build_graph():
    """玩法图:一句话 —— 把 AI 图挂到敌人身上。"""
    logic = bpy.data.node_groups.new(LOGIC_TREE, "GKGameLogicTree")
    logic.use_fake_user = True
    ready = _add(logic, "GKNodeOnReady", "准备", -400.0, 400.0)
    enable = _add(logic, "GKNodeAiEnable", "启用AI", -120.0, 400.0,
                  gk_tree_name=AI_TREE, gk_state_var="ai_state")
    _link(logic, ready, "执行", enable, "执行")
    hint = _add(logic, "GKNodeUiText", "提示", 160.0, 400.0,
                gk_text="敌人会绕墙追过来(场景设置里勾了『寻路』)",
                gk_x=18.0, gk_y=18.0, gk_size=20)
    _link(logic, enable, "完成", hint, "执行")
    log(f"玩法图『{LOGIC_TREE}』:『AI_启用』-> AI 图『{AI_TREE}』(作用在自身)")
    return logic


def _apply_settings(scene, pairs):
    settings = getattr(scene, "gk_settings", None)
    if settings is None:
        return
    for attr, value in pairs:
        if hasattr(settings, attr):
            try:
                setattr(settings, attr, value)
            except Exception:                             # noqa: BLE001
                pass


def save_blend(path):
    if not bpy.app.background:
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return
    blocks = set(bpy.data.objects) | set(bpy.data.meshes) | \
        set(bpy.data.materials) | set(bpy.data.node_groups) | \
        set(bpy.data.cameras) | set(bpy.data.lights)
    scenes = list(bpy.data.scenes)
    bpy.data.libraries.write(path, blocks | set(scenes), fake_user=True)


def main():
    from BlenderGameKit import asset_export, project

    os.makedirs(OUT_DIR, exist_ok=True)
    scene, enemy = build_scene()
    write_physics(scene, enemy)
    build_ai_tree()
    build_graph()
    _apply_settings(scene, (
        ("dimension", "3"),
        ("base_class", "CharacterBody3D"),
        ("logic_tree", LOGIC_TREE),
        ("god_camera", True),
        # ── 关键开关:没有它 AI 就退回直线走(撞墙)─────────────────
        ("nav_enabled", True),
        # 半径要盖过敌人的半个对角(见 NAV_RADIUS 的说明)
        ("nav_agent_radius", NAV_RADIUS),
        ("nav_agent_height", 1.8),
        ("nav_max_slope", 45.0),
    ))

    directory = os.path.join(OUT_DIR, "AI寻路示例_godot")
    project.create_project(directory, title="AI寻路示例",
                           blend_file_name=os.path.basename(BLEND_PATH))
    result = asset_export.export_all_scenes(directory, dimension="3")
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

    save_blend(BLEND_PATH)
    log(f"已保存 {BLEND_PATH}")
    log(f"工程目录: {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
