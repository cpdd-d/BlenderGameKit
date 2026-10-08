"""恐怖演示关(第五十九轮:阶段④收尾)—— 熄灯的教学楼。

用法:
    blender -b --python scripts\\make_horror_demo.py
产出:
    examples/demo_horror/恐怖示例.blend
    examples/demo_horror/恐怖示例_godot/     (可用 Godot 直接打开)

── 这一屏在看什么 ────────────────────────────────────────────────
幽暗的教室 + 雾 + 四角压暗;你拿手电筒在教室里找 **3 张纸条**;
一只东西在走廊里**巡逻** —— 你跑动会弄出动静,它**听见**就过来查看
(第五十七轮的听觉),照到它 / 走到它面前它才**看见**你并开始追。
跟着你的**心跳**会随它靠近越来越快越响(『氛围_紧张度』+『氛围_心跳』);
路过某个位置会**突然受惊**(『惊吓_事件』三连:震屏 + 闪白 + 音效);
拾齐纸条走到门口 → **结局选择**(回头救同学 / 独自逃出)→ 存进存档。

| 用到的 | 在游戏里怎么看 |
|---|---|
| **游戏AI(单独一张图)** | 巡逻 --看见(9 米)--> 追击 --丢失(11 米)--> 巡逻;巡逻 --听见动静--> 去查看 --安静--> 巡逻 |
| **声音_发出噪音** | 按住 **K** = 弄出动静(响度 14 米,最快 0.25 秒一次) |
| **氛围_紧张度** | 离怪物越近越高(3 米 = 1,14 米 = 0),接到心跳上 |
| **氛围_心跳** | 越紧张**跳得越快越响**,平静时停(第五十九轮) |
| **惊吓_事件** | 走过『惊吓区』:相机震 + 屏闪白 + 一声音效,**一个节点**(第五十九轮) |
| **音效_总线音量** | 受惊时把 Master 压低一点,走到结局再放回来(第五十九轮) |
| **屏幕_暗角** | 全程四角压暗,视线收窄(第五十一轮) |
| **灯光(聚光跟随相机)** | 手电筒 —— 运行时贴在相机上 |
| **环境氛围** | 深蓝背景 + 雾 + 泛光 + 低环境光(第四十九轮) |
| **对话_选择** | 门口:两选一,各走一条结局链(第五十轮) |
| **存档_写入 / 保存** | 结局写进 `user://gk_save1.json` |
| **进入区域** | 3 张纸条 / 惊吓区 / 门口 都是触发区 |
| **上帝机(Tab)** | 飞起来看一眼巡逻路线和纸条都在哪 |

按键:WASD 移动 / 空格 跳 / 鼠标转视角 / C·V 切机位 / Tab 上帝机 /
**按住 K 弄出动静** / 门口按 **1 或 2** 选结局。

跑法(Godot):打开 `恐怖示例_godot` 直接 F5。
自动检查(不用 Blender,要 Godot):`python scripts\\check_horror_demo.py`
"""

import math
import os
import struct
import sys
import wave

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "examples", "demo_horror")
BLEND_PATH = os.path.join(OUT_DIR, "恐怖示例.blend")

PLAYER = "玩家"
ENEMY = "怪物"
PATROL_A = "巡逻点甲"
PATROL_B = "巡逻点乙"
NOTE_NAMES = ("纸条1", "纸条2", "纸条3")
SCARE_AREA = "惊吓区"
DOOR_AREA = "门口"
TORCH = "手电筒"
AI_TREE_NAME = "怪物AI"

NOTE_GOAL = 3
SIGHT = 9.0
LOST_RANGE = 11.0
PATROL_SPEED = 3.0
CHASE_SPEED = 4.8
CHASE_STOP = 1.6
LOOK_SPEED = 4.0
LOOK_STOP = 1.0

NOISE_KEY = "K"
NOISE_ACTION = "key_k"
NOISE_LOUD = 14.0          # 响度 > 视野(9),才有"看不见但听得见"的窗口
NOISE_INTERVAL = 0.25
EAR_MEMORY = 1.2

# 紧张度:3 米以内 = 1,14 米以外 = 0
TENSION_NEAR = 3.0
TENSION_FAR = 14.0

# 音频(运行时生成 wav 放进 Godot 工程的 audio/,见 write_audio)
BGM_FILE = "res://audio/ambient.wav"
HEART_FILE = "res://audio/heartbeat.wav"
STING_FILE = "res://audio/sting.wav"


def log(message):
    print(f"[恐怖示例] {message}")


# ==========================================================================
# 场景
# ==========================================================================
def clear_scene():
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for group in list(bpy.data.node_groups):
        if getattr(group, "bl_idname", "").startswith("GK"):
            bpy.data.node_groups.remove(group)
    scene.name = "恐怖示例"
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


def make_light(name, kind, location, energy, color, rotation=(0.0, 0.0, 0.0),
               cutoff=14.0, spot_size=None, shadow=False):
    data = bpy.data.lights.new(f"{name}数据", type=kind)
    data.energy = energy
    data.color = color
    if kind == "SPOT" and spot_size is not None:
        data.spot_size = spot_size
    light = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(light)
    light.location = location
    light.rotation_euler = rotation
    return light, data, cutoff, shadow


def build_scene():
    scene = clear_scene()
    # ⚠ 必须有不止一个网格:整场景只有"角色"一个网格时插件不会生成主场景
    make_box("地面", (28.0, 28.0, 0.4), (0.0, 0.0, -0.2),
             (0.10, 0.11, 0.14, 1.0))
    player = make_box(PLAYER, (1.0, 1.0, 1.6), (-9.0, -9.0, 0.9),
                      (0.85, 0.85, 0.90, 1.0))
    enemy = make_box(ENEMY, (1.4, 1.4, 1.8), (0.0, 0.0, 0.9),
                     (0.55, 0.10, 0.12, 1.0))
    make_box(PATROL_A, (0.5, 0.5, 2.0), (-7.0, 0.0, 1.0),
             (0.25, 0.30, 0.28, 1.0))
    make_box(PATROL_B, (0.5, 0.5, 2.0), (7.0, 0.0, 1.0),
             (0.25, 0.30, 0.28, 1.0))
    # 3 张纸条(自己就是触发区)
    note_spots = ((-6.0, -6.0), (6.0, -6.0), (0.0, 7.0))
    for name, (x, y) in zip(NOTE_NAMES, note_spots):
        make_box(name, (1.0, 1.0, 0.6), (x, y, 0.3), (0.92, 0.90, 0.72, 1.0))
    # 惊吓区 / 门口:贴地的触发板
    make_box(SCARE_AREA, (6.0, 6.0, 0.2), (0.0, 9.0, 0.1),
             (0.30, 0.10, 0.12, 1.0))
    make_box(DOOR_AREA, (4.0, 4.0, 0.2), (0.0, -11.0, 0.1),
             (0.20, 0.35, 0.30, 1.0))
    make_camera("侧视机位", (0.0, -17.0, 5.5), (math.radians(72.0), 0.0, 0.0))
    make_camera("俯瞰机位", (0.0, -8.0, 19.0), (math.radians(20.0), 0.0, 0.0))
    scene.camera = bpy.data.objects["侧视机位"]
    # 手电筒:聚光,运行时贴到相机上(下面勾『跟随相机』)
    torch, torch_data, torch_cutoff, torch_shadow = make_light(
        TORCH, "SPOT", (0.0, 0.0, 1.5), 1400.0, (1.0, 0.96, 0.85),
        rotation=(math.radians(80.0), 0.0, 0.0),
        spot_size=math.radians(42.0), shadow=False)
    torch_data.use_shadow = torch_shadow
    log(f"场景:地面 + {PLAYER} + {ENEMY} + 两个巡逻点 + 3 张纸条 + "
        f"{SCARE_AREA} + {DOOR_AREA} + {TORCH} + 两台相机")
    return scene, player, enemy, torch


def write_physics(scene, player, enemy, torch):
    """玩家 = 角色体 + 挂脚本;怪物 = 角色体(AI 要 velocity);
    纸条 / 惊吓区 / 门口 = 触发区(Area3D);手电筒 = 勾『跟随相机』。"""
    trigger_names = set(NOTE_NAMES) | {SCARE_AREA, DOOR_AREA}
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
        elif obj.name in trigger_names:
            physics.body_type = "Area3D"
            physics.dimension = "3"
            physics.is_trigger = True
        elif getattr(obj, "type", "") == "MESH":
            physics.body_type = "StaticBody3D"
            physics.dimension = "3"
    torch_physics = getattr(torch, "gk_physics", None)
    if torch_physics is not None:
        torch_physics.light_follow_camera = True
        log(f"{TORCH}:已勾『跟随相机(手电筒)』")


# ==========================================================================
# 节点图
# ==========================================================================
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
    """AI 图:巡逻 ←→ 追击 + 听见动静去查看(和 demo_ai 同一套结构)。"""
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

    _link(tree, patrol, "转到", see, "上游")
    _link(tree, see, "转到", chase, "进入")
    _link(tree, chase, "转到", back, "上游")
    _link(tree, back, "转到", patrol, "进入")
    _link(tree, patrol, "转到", heard, "上游")
    _link(tree, heard, "转到", look, "进入")
    _link(tree, look, "转到", calm, "上游")
    # 巡逻有两条转场指进来 -> 第二条必须用『进入 2』(输入引脚只能接一条线)
    _link(tree, calm, "转到", patrol, "进入 2")
    log(f"AI 图『{tree.name}』:巡逻 --看见({SIGHT:g})--> 追击 "
        f"--丢失({LOST_RANGE:g})--> 巡逻;巡逻 --听见--> 去查看 --安静--> 巡逻")
    return tree


def build_graph(scene, player, enemy, ai_tree):
    """玩法图:角色预设 + 氛围 + 听觉 + 惊吓 + 结局。"""
    from BlenderGameKit import presets

    logic, _anim, notes = presets.create_character_preset(
        scene, character=player,
        logic_name="玩家逻辑",
        class_name="HorrorDemoPlayer",
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

    # ---- ① 开场链:提示 + BGM + 暗角 + 启动 AI ----
    hint = _add(logic, "GKNodeUiText", "操作提示", -300.0, 1400.0,
                gk_text=("WASD 移动 / 空格 跳 / 鼠标转视角 / Tab 上帝机 / "
                         "按住 K 弄出动静"),
                gk_x=18.0, gk_y=18.0, gk_size=18)
    goal = _add(logic, "GKNodeUiText", "目标提示", -300.0, 1240.0,
                gk_text=f"找齐 {NOTE_GOAL} 张纸条,走到门口离开教学楼",
                gk_x=18.0, gk_y=46.0, gk_size=18)
    bgm = _add(logic, "GKNodePlayMusic", "恐怖BGM", -300.0, 1080.0,
               gk_path=BGM_FILE, gk_volume=-16.0, gk_fade=2.0)
    vignette = _add(logic, "GKNodeScreenVignette", "暗角", -300.0, 920.0,
                    gk_amount=0.45, gk_radius=0.60, gk_edge=0.40)
    enable = _add(logic, "GKNodeAiEnable", "启动怪物AI", -300.0, 760.0,
                  gk_tree_name=ai_tree.name, gk_target=enemy.name,
                  gk_debug_print=True)
    # ⚠ 『每当_准备』是个**事件**,只有『执行』出口、没有『完成』——
    #   第一段必须从它的『执行』接出去(接『完成』会 KeyError)。
    _link(logic, ready, "执行", hint, "执行")
    tail = hint
    for node in (goal, bgm, vignette, enable):
        _link(logic, tail, "完成", node, "执行")
        tail = node

    # ---- ② 心跳(每物理帧):紧张度 <- 氛围_紧张度(离怪物多近)----
    phys_beat = _add(logic, "GKNodeOnPhysicsProcess", "物理帧(心跳)",
                     -300.0, 480.0)
    tension = _add(logic, "GKNodeTension", "紧张度", -60.0, 480.0,
                   gk_threat=enemy.name, gk_near=TENSION_NEAR,
                   gk_far=TENSION_FAR)
    heartbeat = _add(logic, "GKNodeHeartbeat", "心跳", 300.0, 480.0,
                     gk_path=HEART_FILE, gk_slow_interval=1.1,
                     gk_fast_interval=0.32, gk_quiet_db=-19.0, gk_loud_db=-5.0,
                     gk_quiet=0.15, gk_smooth=1.5)
    _link(logic, phys_beat, "执行", heartbeat, "执行")
    _link(logic, tension, "紧张度", heartbeat, "紧张度")

    # ---- ③ 弄出动静(每物理帧)----
    phys_noise = _add(logic, "GKNodeOnPhysicsProcess", "物理帧(动静)",
                      -300.0, 200.0)
    noise_key = _add(logic, "GKNodeKey", "按住K", -60.0, 200.0,
                     gk_key=NOISE_KEY, gk_action=NOISE_ACTION,
                     gk_trigger="PRESSED")
    noise_branch = _add(logic, "GKNodeBranch", "按住K?", 140.0, 200.0)
    noise = _add(logic, "GKNodeNoise", "弄出动静", 340.0, 200.0,
                 gk_loudness=NOISE_LOUD, gk_interval=NOISE_INTERVAL,
                 gk_target=player.name, gk_debug_print=True)
    _link(logic, noise_key, "结果", noise_branch, "条件")
    _link(logic, phys_noise, "执行", noise_branch, "执行")
    _link(logic, noise_branch, "是", noise, "执行")

    # ---- ④ 拾取纸条(各一条独立链)----
    for index, name in enumerate(NOTE_NAMES):
        x = -300.0 + index * 0.0
        y = -120.0 - index * 260.0
        touched = _add(logic, "GKNodeOnAreaEntered", f"碰到{name}", x, y,
                       gk_area=name, gk_filter=PLAYER)
        add_one = _add(logic, "GKNodeAddVar", f"纸条+1_{index + 1}", x + 240.0, y,
                       gk_var_name="纸条数", gk_amount=1.0,
                       gk_clamp=True, gk_min=0.0, gk_max=float(NOTE_GOAL))
        say = _add(logic, "GKNodePrint", f"拾到纸条{index + 1}", x + 480.0, y,
                   gk_text="拾到一张纸条……")
        _link(logic, touched, "执行", add_one, "执行")
        _link(logic, add_one, "完成", say, "执行")

    # ---- ⑤ 惊吓区:三连(震 + 闪 + 响)+ 压低总线 ----
    scared = _add(logic, "GKNodeOnAreaEntered", "走过惊吓区", -300.0, -980.0,
                  gk_area=SCARE_AREA, gk_filter=PLAYER)
    scare = _add(logic, "GKNodeScare", "受惊一下", -60.0, -980.0,
                 gk_strength=18.0, gk_duration=0.5,
                 gk_color=(0.85, 0.10, 0.10, 1.0), gk_alpha=0.55,
                 gk_flash_time=0.35, gk_path=STING_FILE, gk_volume=-3.0)
    duck = _add(logic, "GKNodeBusVolume", "压低一点", 200.0, -980.0,
                gk_bus="Master", gk_db=-18.0, gk_fade=0.3)
    _link(logic, scared, "执行", scare, "执行")
    _link(logic, scare, "完成", duck, "执行")

    # ---- ⑥ 门口:结局选择(两选一)+ 存档 ----
    door = _add(logic, "GKNodeOnAreaEntered", "走到门口", -300.0, -1340.0,
                gk_area=DOOR_AREA, gk_filter=PLAYER)
    choice = _add(logic, "GKNodeUiChoice", "结局选择", -60.0, -1340.0,
                  gk_question="楼梯间的门开了,你要怎么做?",
                  gk_option_count=2,
                  gk_options="回头救同学|独自逃出", gk_speed=0.0)
    _link(logic, door, "执行", choice, "执行")

    endings = ((1, "选项1", "回头救她"), (2, "选项2", "独自逃出"))
    for number, pin, label in endings:
        y = -1240.0 - number * 260.0
        # ⚠ 『变量_设置』/『存档_写入』的『值』是**任意值引脚**,没有可写的
        #   默认值 —— 要用一个『常量_数值』连进去(GKSocketAny.default_value
        #   是 None,直接在 Python 里设 default_value 会被静默忽略)。
        value = _add(logic, "GKNodeConstFloat", f"结局值{number}", 60.0, y,
                     gk_value=float(number))
        set_end = _add(logic, "GKNodeSetVar", f"记结局{number}", 260.0, y,
                       gk_var_name="结局")
        write = _add(logic, "GKNodeSaveSet", f"写结局{number}", 500.0, y,
                     gk_key="结局")
        save = _add(logic, "GKNodeSaveWrite", f"保存{number}", 740.0, y,
                    gk_slot="save1")
        text = _add(logic, "GKNodeUiText", f"结局文字{number}", 980.0, y,
                    gk_text=f"结局:{label}", gk_x=18.0, gk_y=90.0,
                    gk_size=26)
        restore = _add(logic, "GKNodeBusVolume", f"音量恢复{number}",
                       1220.0, y, gk_bus="Master", gk_db=0.0, gk_fade=1.0)
        _link(logic, value, "值", set_end, "值")
        _link(logic, value, "值", write, "值")
        _link(logic, choice, pin, set_end, "执行")
        _link(logic, set_end, "完成", write, "执行")
        _link(logic, write, "完成", save, "执行")
        _link(logic, save, "完成", text, "执行")
        _link(logic, text, "完成", restore, "执行")

    log(f"玩法图『{logic.name}』:开场(提示/BGM/暗角/AI) + 心跳 + 动静 + "
        f"3 张纸条 + 惊吓区 + 门口结局选择")
    return logic


# ==========================================================================
# 音频(运行时生成小 wav,放进 Godot 工程的 audio/)
# ==========================================================================
def _write_wav(path, samples, rate=22050):
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = b"".join(struct.pack("<h", max(-32767, min(32767, int(s * 32767))))
                          for s in samples)
        handle.writeframes(frames)


def write_audio(directory):
    """生成三个小 wav(不用外部素材,示例自包含)。

    心跳:55Hz 低音,指数衰减;
    惊吓:噪声爆 + 300Hz,快速衰减;
    环境:60/90Hz 双正弦低鸣。
    """
    audio_dir = os.path.join(directory, "audio")
    os.makedirs(audio_dir, exist_ok=True)
    rate = 22050

    def thump(duration, freq, decay):
        total = int(rate * duration)
        out = []
        for i in range(total):
            t = i / rate
            env = math.exp(-decay * t)
            out.append(0.55 * env * math.sin(2.0 * math.pi * freq * t))
        return out

    _write_wav(os.path.join(audio_dir, "heartbeat.wav"),
               thump(0.16, 55.0, 26.0), rate)
    _write_wav(os.path.join(audio_dir, "sting.wav"),
               thump(0.40, 300.0, 9.0), rate)
    # 环境:双低音,首尾淡入淡出(免得硬切)
    total = int(rate * 2.0)
    ambient = []
    for i in range(total):
        t = i / rate
        fade = min(1.0, t / 0.3, (2.0 - t) / 0.3)
        tone = (math.sin(2.0 * math.pi * 60.0 * t)
                + 0.6 * math.sin(2.0 * math.pi * 90.0 * t))
        ambient.append(0.12 * max(0.0, fade) * tone / 1.6)
    _write_wav(os.path.join(audio_dir, "ambient.wav"), ambient, rate)
    log(f"音频:heartbeat / sting / ambient -> {audio_dir}")


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
    scene, player, enemy, torch = build_scene()
    write_physics(scene, player, enemy, torch)
    ai_tree = build_ai_tree()
    build_graph(scene, player, enemy, ai_tree)
    # 幽暗教学楼:深蓝背景 + 雾 + 泛光 + 低环境光(『显示_环境氛围』那张卡的字段)
    _apply_settings(scene, (
        ("dimension", "3"),
        ("toon_follow_light", True),
        ("env_enabled", True),
        ("env_background", "COLOR"),
        ("env_bg_color", (0.02, 0.02, 0.04, 1.0)),
        ("env_ambient_color", (0.42, 0.48, 0.66, 1.0)),
        ("env_ambient_energy", 0.14),
        ("env_fog", True),
        ("env_fog_color", (0.16, 0.19, 0.28, 1.0)),
        ("env_fog_density", 0.07),
        ("env_glow", True),
        ("env_glow_intensity", 1.1),
        ("env_glow_bloom", 0.2),
        ("env_tonemap", "ACES"),
    ))

    directory = os.path.join(OUT_DIR, "恐怖示例_godot")
    project.create_project(directory, title="恐怖示例",
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

    # 音频要在导出**之后**写(导出会清理它自己标记的文件)
    write_audio(directory)

    save_blend(BLEND_PATH)
    log(f"已保存 {BLEND_PATH}")
    log(f"工程目录: {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())