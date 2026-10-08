"""AI 示例(第四十一轮:游戏AI)—— 一个会巡逻、会追你、追丢了自己回去的敌人。

用法:
    blender -b --gpu-backend vulkan --python scripts\\make_ai_demo.py
产出:
    examples/demo_ai/AI示例.blend
    examples/demo_ai/AI示例_godot/     (可用 Godot 直接打开)

── 这一屏在看什么 ────────────────────────────────────────────────
一只红方块(敌人)在两根绿桩之间**来回巡逻**;你(橙方块)走近到 9 米内,
它**看见**了就掉头追你;你跑远(超过 11 米)它**跟丢**了,自己走回巡逻路线。
AI 的规则**不在玩法图里**,画在一张独立的『游戏AI』节点图里 —— 玩法图
只有一个『AI_启用』节点引用它(和『动画_状态机』『界面_套用设计』同一套路)。

| 用到的 | 在游戏里怎么看 |
|---|---|
| **游戏AI(单独一张图)** | Blend 里叫『敌人AI』:巡逻 --看见玩家(9 米)--> 追击 --丢失玩家(11 米)--> 巡逻 |
| **AI_状态(巡逻)** | 敌人在『巡逻点甲』『巡逻点乙』之间来回走(到了就折返) |
| **AI_状态(追击)** | 看见你之后朝你走,贴近 1.6 米就停下(面向你) |
| **AI_转场(看见 / 丢失目标)** | 条件就填在卡片上(视野距离),不用拼"距离 + 比较"两个节点 |
| **AI_状态(去查看)** | **听见动静**但看不见你时,走到那个位置看看(第五十七轮) |
| **AI_转场(听见动静 / 安静下来)** | 也是填在卡片上:记住多久、耳朵多远 |
| **声音_发出噪音** | 玩家脚本里:按住 **K** = 弄出动静(响度 16 米,最快 0.2 秒一次) |
| **AI_启用** | 玩法图里唯一的 AI 入口 —— 作用在『敌人』身上(AI 代码在玩家脚本里,和追击怪同一个做法) |
| **打印状态变化** | 勾上了 —— Godot 输出面板里能看到 "切到状态: 追击" |
| **上帝机(Tab)** | 飞起来看一眼它的巡逻路线 |

按键:WASD 移动 / 空格 跳 / 鼠标转视角 / C·V 切机位 / Tab 上帝机 /
**按住 K 弄出动静**(引出听觉那条路)。

> **听觉和视觉的区别(这张图里最能看出门道的一处)**:『看见』才 9 米,
> 『听见』有 16 米 —— 所以**站在 13 米外、按住 K**,敌人会**朝着声音过来**
> 而不是直冲你(它只知道"那边有响动",不知道你在哪)。这就是恐怖游戏里
> "扔石头把怪物引开"的那个机制。

跑法(Godot):打开 `AI示例_godot` 直接 F5。
自动检查(不用 Blender,要 Godot):`python scripts\\check_ai_demo.py`
"""

import math
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "examples", "demo_ai")
BLEND_PATH = os.path.join(OUT_DIR, "AI示例.blend")

PLAYER = "玩家"
ENEMY = "敌人"
PATROL_A = "巡逻点甲"
PATROL_B = "巡逻点乙"
AI_TREE_NAME = "敌人AI"

SIGHT = 9.0            # 看见玩家(米)
LOST_RANGE = 11.0      # 跟丢(米)
PATROL_SPEED = 3.0
CHASE_SPEED = 4.6
CHASE_STOP = 1.6

# ── 听觉(第五十七轮)─────────────────────────────────────────────
NOISE_KEY = "K"        # 按 K = 弄出动静(跑步 / 踢到东西)
NOISE_ACTION = "key_k"
# ⚠ 响度(16)必须**大于**『看见』的距离(9)—— 否则"听得见"的那个距离上
#   敌人早就**看得见**你了,会直接切『追击』,听觉那条路根本轮不到。
#   16 > 9 才有"看不见但听得见"的窗口(13 米处:看不见、听得见)。
NOISE_LOUD = 16.0      # 这声音能传 16 米
NOISE_INTERVAL = 0.2   # 最快 0.2 秒发一次(按住 K 就是连续动静)
EAR_MEMORY = 1.2       # 听到之后记 1.2 秒 —— 也就是"去查看"会持续这么久
LOOK_SPEED = 4.0
LOOK_STOP = 1.0


def log(message):
    print(f"[AI示例] {message}")


def clear_scene():
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for group in list(bpy.data.node_groups):
        if getattr(group, "bl_idname", "").startswith("GK"):
            bpy.data.node_groups.remove(group)
    scene.name = "AI示例"
    return scene


def make_box(name, size, location, color=None):
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


def make_camera(name, location, rotation):
    data = bpy.data.cameras.new(f"{name}数据")
    camera = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = location
    camera.rotation_euler = rotation
    return camera


def build_scene():
    scene = clear_scene()
    # ⚠ 必须有不止一个网格:整场景只有"角色"一个网格时插件不会生成主场景
    # .tscn(它以为这关没东西可看)—— 探针就没有 World.tscn 可加载了。
    make_box("地面", (24.0, 24.0, 0.4), (0.0, 0.0, -0.2),
             (0.26, 0.34, 0.28, 1.0))
    player = make_box(PLAYER, (1.0, 1.0, 1.6), (-8.0, 0.0, -8.0),
                      (0.95, 0.55, 0.25, 1.0))
    enemy = make_box(ENEMY, (1.4, 1.4, 1.4), (0.0, 0.0, 0.7),
                     (0.85, 0.25, 0.25, 1.0))
    # 两根绿桩 = 巡逻路线的两头(敌人在这两点之间折返)
    make_box(PATROL_A, (0.6, 0.6, 1.8), (-6.0, 0.0, 0.9),
             (0.30, 0.75, 0.40, 1.0))
    make_box(PATROL_B, (0.6, 0.6, 1.8), (6.0, 0.0, 0.9),
             (0.30, 0.75, 0.40, 1.0))
    side = make_camera("侧视机位", (0.0, -15.0, 5.0),
                       (math.radians(72.0), 0.0, 0.0))
    make_camera("俯瞰机位", (0.0, -7.0, 17.0), (math.radians(20.0), 0.0, 0.0))
    scene.camera = side
    light_data = bpy.data.lights.new("太阳光数据", type="SUN")
    light_data.energy = 3.0
    light = bpy.data.objects.new("太阳光", light_data)
    scene.collection.objects.link(light)
    light.rotation_euler = (math.radians(50.0), 0.0, math.radians(30.0))
    log(f"场景:地面 + {PLAYER} + {ENEMY} + 两个巡逻点"
        f"({PATROL_A} / {PATROL_B})+ 两台相机")
    return scene, player, enemy


def write_physics(scene, player, enemy):
    """玩家 = 角色体 + 挂脚本;敌人 = **角色体**(AI 行为要 velocity);
    其余网格 = 静态体。"""
    for obj in scene.objects:
        physics = getattr(obj, "gk_physics", None)
        if physics is None:
            continue
        try:
            physics.shape_size = (0.0, 0.0, 0.0)
        except Exception:                                 # noqa: BLE001
            pass
        if obj is player:
            physics.body_type = "CharacterBody3D"
            physics.dimension = "3"
            physics.script_target = True
        elif obj is enemy:
            physics.body_type = "CharacterBody3D"
            physics.dimension = "3"
        elif getattr(obj, "type", "") == "MESH":
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
    """AI 图:巡逻 ←→ 追击(第四十一轮的主角)。

    ── 这张图跑在哪 ────────────────────────────────────────────────
    由玩法图的『AI_启用』驱动(见 `build_graph`),作用在**敌人**身上 ——
    所以"敌人"必须是角色体(行为是 velocity + move_and_slide 那一套)。

    ── 为什么巡逻用两个**物体**当点 ────────────────────────────────
    巡逻 = "在这两点之间来回走",两点必须在场景里看得见(两根绿桩),
    用户也才能在视口里对着摆。导出后它们就是场景里两个静态节点。
    """
    tree = bpy.data.node_groups.new(AI_TREE_NAME, "GKGameAiTree")
    tree.use_fake_user = True

    patrol = _add(tree, "GKNodeAiState", "巡逻", -60.0, 0.0,
                  gk_state_name="巡逻", gk_behavior="PATROL",
                  gk_patrol_a=PATROL_A, gk_patrol_b=PATROL_B,
                  gk_speed=PATROL_SPEED, gk_stop_distance=0.7,
                  gk_facing="MOVE")
    chase = _add(tree, "GKNodeAiState", "追击", 700.0, 0.0,
                 gk_state_name="追击", gk_behavior="CHASE",
                 gk_goal=PLAYER, gk_speed=CHASE_SPEED,
                 gk_stop_distance=CHASE_STOP, gk_facing="GOAL")
    # ── 听觉:听到动静就过去看看(第五十七轮)──────────────────────────
    # 别和『追击』混:追击是"看见你"直接冲过来;去查看是"听见响动但不知道
    # 你在哪",走到那个位置看看 —— 恐怖游戏里最经典的那个"引开 / 被引开"。
    look = _add(tree, "GKNodeAiState", "去查看", 700.0, 340.0,
                gk_state_name="去查看", gk_behavior="INVESTIGATE",
                gk_speed=LOOK_SPEED, gk_stop_distance=LOOK_STOP,
                gk_facing="MOVE")
    see = _add(tree, "GKNodeAiTransition", "看见玩家", 320.0, 180.0,
               gk_cond_source="SEE", gk_goal=PLAYER, gk_sight=SIGHT)
    back = _add(tree, "GKNodeAiTransition", "丢失玩家", 320.0, -210.0,
                gk_cond_source="LOST", gk_goal=PLAYER, gk_sight=LOST_RANGE)
    heard = _add(tree, "GKNodeAiTransition", "听见动静", 320.0, 340.0,
                 gk_cond_source="HEARD", gk_ear=0.0, gk_memory=EAR_MEMORY)
    calm = _add(tree, "GKNodeAiTransition", "安静下来", 700.0, 480.0,
                gk_cond_source="CALM", gk_ear=0.0, gk_memory=EAR_MEMORY)

    # 环用连线搭(实测 Blender 4.5.5 允许,保存 / 读回都正常)
    _link(tree, patrol, "转到", see, "上游")
    _link(tree, see, "转到", chase, "进入")
    _link(tree, chase, "转到", back, "上游")
    _link(tree, back, "转到", patrol, "进入")
    # 听觉那条支路:巡逻 --听见动静--> 去查看 --安静下来--> 巡逻
    _link(tree, patrol, "转到", heard, "上游")
    _link(tree, heard, "转到", look, "进入")
    _link(tree, look, "转到", calm, "上游")
    # ⚠ 巡逻现在有**两条**转场指进来(『丢失玩家』和『安静下来』)——
    #   Blender 的 links.new 接一个**已经接过线的输入**是**替换**旧线,
    #   所以第二条必须用『进入 2』(『入口数量』默认 2,正好够)。
    #   不这么做的话『丢失玩家』那条会**无声消失**。
    _link(tree, calm, "转到", patrol, "进入 2")
    log(f"AI 图『{tree.name}』:巡逻 --看见({SIGHT:g} 米)--> 追击 "
        f"--丢失({LOST_RANGE:g} 米)--> 巡逻;"
        f"巡逻 --听见动静({NOISE_LOUD:g} 米内)--> 去查看 "
        f"--安静({EAR_MEMORY:g} 秒)--> 巡逻({len(tree.nodes)} 个节点,"
        f"起始状态 = 巡逻)")
    return tree


def build_graph(scene, player, enemy, ai_tree):
    """玩法图:角色预设(你自己的控制)+ 一个『AI_启用』驱动敌人。

    ── 为什么 AI 代码写在**玩家脚本**里 ────────────────────────────
    『AI_启用』的『作用在谁』可以选别的物体(默认才是脚本自己)——
    和示例里的追击怪同一个做法:一个脚本统管全场,不用给每个敌人
    各挂一份脚本。行为代码作用在『敌人』的 velocity 上,玩家这边
    只管自己的移动 / 相机,两边互不干扰。
    """
    from BlenderGameKit import presets

    logic, _anim, notes = presets.create_character_preset(
        scene, character=player,
        logic_name="玩家逻辑",
        class_name="AiDemoPlayer",
        with_animation=False,
        with_ports=False,
        camera_side="侧视机位",
        camera_top="俯瞰机位",
    )
    for note in notes:
        log(f"预设: {note}")
    ready = None
    for node in logic.nodes:
        if node.bl_idname == "GKNodeOnReady":
            ready = node
    if ready is None:
        raise RuntimeError("角色预设里没有『准备』节点")

    enable = _add(logic, "GKNodeAiEnable", "启动敌人AI", 0.0, -720.0,
                  gk_tree_name=ai_tree.name,
                  gk_target=enemy.name,
                  gk_debug_print=True)
    _link(logic, ready, "执行", enable, "执行")

    # ── 弄出动静(第五十七轮:AI 听觉)──────────────────────────────
    # 接在**物理帧**后面 + 填『间隔秒』:这就是"跑步声/心跳声"的写法 ——
    # 不填间隔的话每帧都广播,敌人的"最后听到时间"永远是刚发生的,
    # 『安静下来』永远不成立,它会一直黏在噪音点。
    physics = _add(logic, "GKNodeOnPhysicsProcess", "每当物理帧", 0.0, -980.0)
    # ⚠ 『输入_按键』是**表达式**节点(输出真假),没有执行引脚 —— 要经过
    #   『流程_分支』才能驱动执行流(和 chase 示例里"R 键重开"同一套接法)。
    noise_key = _add(logic, "GKNodeKey", "按K弄出动静", -340.0, -980.0,
                     gk_key=NOISE_KEY, gk_action=NOISE_ACTION,
                     gk_trigger="PRESSED")
    noise_branch = _add(logic, "GKNodeBranch", "按住K?", -140.0, -980.0)
    noise = _add(logic, "GKNodeNoise", "弄出动静", 60.0, -980.0,
                 gk_loudness=NOISE_LOUD, gk_interval=NOISE_INTERVAL,
                 gk_target=player.name, gk_debug_print=True)
    _link(logic, noise_key, "结果", noise_branch, "条件")
    _link(logic, physics, "执行", noise_branch, "执行")
    _link(logic, noise_branch, "是", noise, "执行")
    log(f"玩家逻辑:『每当_物理帧』->『按键 {NOISE_KEY}』->『弄出动静』"
        f"(响度 {NOISE_LOUD:g} 米,间隔 {NOISE_INTERVAL:g} 秒)")
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
    scene, player, enemy = build_scene()
    write_physics(scene, player, enemy)
    ai_tree = build_ai_tree()
    build_graph(scene, player, enemy, ai_tree)
    _apply_settings(scene, (("dimension", "3"),))

    directory = os.path.join(OUT_DIR, "AI示例_godot")
    project.create_project(directory, title="AI示例",
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