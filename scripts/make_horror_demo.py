"""恐怖示例(第五十九 / 六十 / 七十轮:阶段④⑤)—— 熄灯的教学楼(短篇)。
用法:
    blender -b --python scripts\\make_horror_demo.py
产出:
    examples/demo_horror/恐怖示例.blend          (一个 .blend,**四个 Blender 场景**)
    examples/demo_horror/恐怖示例_godot/         (可用 Godot 直接打开;每关一张 .tscn)

── 四关(第六十轮拆三关 + 第七十轮补标题页)────────────────────────
在 Blender 里"一关 = 一个场景",每个场景用场景设置里的 `logic_tree` 指到
自己的『游戏逻辑』图,`asset_export.export_all_scenes()` 就会一关导一张
.tscn,关底用『场景_切换关卡』切过去。

| 幕 | Blender 场景 | 内容 | 用到的 |
|---|---|---|---|
| 0 | 标题 | 标题 + 『开始游戏』按钮 | 界面_文本 / 界面_按钮 / 切关卡 |
| 1 | 教室 | **开场俯冲曲线运镜** + 月光/台灯、3 张纸条、门口通往走廊 | 相机_切换(沿曲线)/ 灯光 / 触发区 / 存档 |
| 2 | 走廊 | 手电筒(聚光跟相机)、雾、**巡逻怪物(会听动静)**、惊吓区 | 听觉 AI / 心跳紧张度 / 惊吓三连 / 总线音量 |
| 3 | 楼梯间 | 门口 + 结局分支 + 存档,**演完回标题** | 对话_选择 / 存档 / 比较分支 |

**跨关的数据**(纸条数)走**存档**:教室拾满时 `存档_保存` 落盘,
楼梯间开局 `存档_载入` + `存档_读取到变量` 读回来,门口按时判"够不够 3 张"。
两处『存档_保存』都勾了**『防改档』**(第九十三轮):落盘是带校验和的信封,
玩家手改文件就当没存档;探针里专门有两趟 —— 读回生成代码自己写的信封、
塞一份改过的档验证被拒(`check_horror_demo.py` 的 HORROR_ENVELOPE / HORROR_TAMPER)。

按键:标题页点『开始游戏』/ 游戏里 WASD 移动 / 空格 跳 / 鼠标转视角 /
C·V 切机位 / Tab 上帝机 / **按住 K 弄出动静** / 门口按 **1 或 2** 选结局。

跑法(Godot):打开 `恐怖示例_godot` 直接 F5(主场景是标题页)。
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

# ── 四关(Blender 场景名 = 导出的 .tscn 名)──────────────────────────
# 第七十轮加了**标题页**:开局进标题,点『开始游戏』进教室;两个结局演完
# 都回标题(一段可循环的短篇)。
TITLE = "标题"
ROOM = "教室"
CORRIDOR = "走廊"
STAIRS = "楼梯间"

TITLE_RES = "res://scenes/标题.tscn"
ROOM_RES = "res://scenes/教室.tscn"
CORRIDOR_RES = "res://scenes/走廊.tscn"
STAIRS_RES = "res://scenes/楼梯间.tscn"

NOTE_NAMES = ("纸条1", "纸条2", "纸条3")
SCARE_AREA = "惊吓区"
TO_ROOM = "去走廊"
TO_STAIRS = "去楼梯间"
DOOR_AREA = "门口"
ENEMY = "怪物"
PATROL_A = "巡逻点甲"
PATROL_B = "巡逻点乙"
TORCH = "手电筒"
AI_TREE_NAME = "怪物AI"

# 开场运镜(第七十轮):一条 3D 曲线当轨道 + 一台门口特写机位。
# 相机从『教室机位』起飞,鼓个弯越过课桌,**俯冲**到门口特写;
# 特写停 1.5 秒,**再切回第三人称**(不切回去玩家就没了跟随机位 ——
# 表现是"人还能动,但视角钉在特写上不肯走",用户实测报过)。
ROOM_SIDE = "教室机位"
ROOM_TOP = "教室俯瞰"
ROOM_TRACK = "教室运镜轨道"
ROOM_CLOSE = "教室特写"
ROOM_CLOSE_POS = (0.0, -12.0, 3.2)
ROOM_TRACK_POINTS = [
    (0.0, -15.0, 5.5),     # 贴着『教室机位』(起点)
    (5.0, -8.0, 12.0),     # 升起来
    (-4.0, 1.0, 9.0),      # 越过课桌(侧面鼓出去,一眼看出是曲线)
    ROOM_CLOSE_POS,        # 俯冲到门口特写(终点)
]
OPENING_BLEND = 2.5        # 开场运镜时长(秒)
RETURN_BLEND = 1.0         # 切回第三人称的过渡(秒)
# ⚠ 『相机_切换』的『完成』是"开始过渡了"就返回(过渡异步跑),所以要**等够**
#   运镜的时长再停 1.5 秒,然后才切回来。
OPENING_HOLD = OPENING_BLEND + 1.5

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

TENSION_NEAR = 3.0
TENSION_FAR = 14.0

BGM_FILE = "res://audio/ambient.wav"
HEART_FILE = "res://audio/heartbeat.wav"
STING_FILE = "res://audio/sting.wav"


def log(message):
    print(f"[恐怖示例] {message}")


# ==========================================================================
# 场景与物体(每个物体链接进指定的 Blender 场景)
# ==========================================================================
def clear_all():
    """清空所有数据块,**不能用 `read_factory_settings`**(会把插件一起卸载)。

    清完把唯一剩下的场景命名为『教室』(另外几关在后面新建)。
    """
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for group in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(group)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    for curve in list(bpy.data.curves):
        bpy.data.curves.remove(curve)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for scene in list(bpy.data.scenes)[1:]:
        bpy.data.scenes.remove(scene)
    scene = bpy.context.scene
    scene.name = ROOM
    return scene


def make_box(name, size, location, color=None, scene=None):
    """一个长方体(地面 / 墙 / 纸条 / 触发板都用它)。

    用底层 API 建 + 直接链接进目标场景 —— **不用 `bpy.ops`**(那个只会加进
    当前上下文场景,造不出第二 / 第三关)。
    """
    sx, sy, sz = [v * 0.5 for v in size]
    verts = [(-sx, -sy, -sz), (sx, -sy, -sz), (sx, sy, -sz), (-sx, sy, -sz),
             (-sx, -sy, sz), (sx, -sy, sz), (sx, sy, sz), (-sx, sy, sz)]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5),
             (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh = bpy.data.meshes.new(f"{name}网格")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    (scene or bpy.context.scene).collection.objects.link(obj)
    obj.location = location
    if color:
        material = bpy.data.materials.new(f"{name}材质")
        material.use_nodes = True
        principled = material.node_tree.nodes.get("Principled BSDF")
        if principled is not None:
            principled.inputs["Base Color"].default_value = color
        obj.data.materials.append(material)
    return obj


def make_camera(name, location, rotation, scene, lens=50.0):
    data = bpy.data.cameras.new(f"{name}数据")
    data.lens = lens
    camera = bpy.data.objects.new(name, data)
    scene.collection.objects.link(camera)
    camera.location = location
    camera.rotation_euler = rotation
    return camera


def make_curve(name, points, scene):
    """一条贝塞尔曲线(自动手柄)—— 当运镜轨道用(『相机_切换』的 CURVE 模式)。

    导出时会被采样成 Godot 的 `Curve3D` 资源(curves/*.tres),相机沿它飞。
    """
    data = bpy.data.curves.new(f"{name}数据", type="CURVE")
    data.dimensions = "3D"
    spline = data.splines.new("BEZIER")
    spline.bezier_points.add(len(points) - 1)
    for handle, co in zip(spline.bezier_points, points):
        handle.co = co
        handle.handle_left_type = "AUTO"
        handle.handle_right_type = "AUTO"
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    return obj


def make_light(name, kind, location, energy, color, scene,
               rotation=(0.0, 0.0, 0.0), cutoff=14.0, spot_size=None,
               shadow=False):
    data = bpy.data.lights.new(f"{name}数据", type=kind)
    data.energy = energy
    data.color = color
    data.use_shadow = shadow
    if kind == "SPOT" and spot_size is not None:
        data.spot_size = spot_size
    if kind in ("POINT", "SPOT"):
        data.shadow_soft_size = cutoff * 0.15
    light = bpy.data.objects.new(name, data)
    scene.collection.objects.link(light)
    light.location = location
    light.rotation_euler = rotation
    return light


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


# 幽暗教学楼的三个环境预设(都走『显示_环境氛围』那张卡的字段)
ENV_DARK = (
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
)
ENV_ROOM = (
    ("env_enabled", True),
    ("env_background", "COLOR"),
    ("env_bg_color", (0.05, 0.06, 0.11, 1.0)),
    ("env_ambient_color", (0.55, 0.60, 0.78, 1.0)),
    ("env_ambient_energy", 0.28),
    ("env_fog", True),
    ("env_fog_color", (0.22, 0.25, 0.36, 1.0)),
    ("env_fog_density", 0.04),
    ("env_glow", True),
    ("env_glow_intensity", 0.9),
    ("env_glow_bloom", 0.15),
    ("env_tonemap", "ACES"),
)
ENV_STAIRS = (
    ("env_enabled", True),
    ("env_background", "COLOR"),
    ("env_bg_color", (0.015, 0.015, 0.03, 1.0)),
    ("env_ambient_color", (0.35, 0.40, 0.58, 1.0)),
    ("env_ambient_energy", 0.08),
    ("env_fog", True),
    ("env_fog_color", (0.12, 0.14, 0.22, 1.0)),
    ("env_fog_density", 0.10),
    ("env_glow", True),
    ("env_glow_intensity", 1.3),
    ("env_glow_bloom", 0.25),
    ("env_tonemap", "ACES"),
)
# 标题页:几乎全黑(只有 UI 亮着),一点雾把背景压住
ENV_TITLE = (
    ("env_enabled", True),
    ("env_background", "COLOR"),
    ("env_bg_color", (0.01, 0.01, 0.02, 1.0)),
    ("env_ambient_color", (0.30, 0.34, 0.50, 1.0)),
    ("env_ambient_energy", 0.10),
    ("env_fog", True),
    ("env_fog_color", (0.10, 0.11, 0.18, 1.0)),
    ("env_fog_density", 0.03),
    ("env_glow", True),
    ("env_glow_intensity", 0.8),
    ("env_glow_bloom", 0.2),
    ("env_tonemap", "ACES"),
)


# ==========================================================================
# 三关:物体
# ==========================================================================
def build_room(scene):
    """第一关 教室:月光 + 台灯 + 3 张纸条 + 通往走廊的门。

    ⚠ 用**已有的那个场景**(clear_all 已经把它改名成『教室』),不要再
    `scenes.new("教室")` —— 重名会被 Blender 改成『教室.001』,于是导出
    多出一关空场景(踩过)。
    """
    make_box("教室地面", (24.0, 24.0, 0.4), (0.0, 0.0, -0.2),
             (0.12, 0.12, 0.16, 1.0), scene=scene)
    # 课桌(纯粹好看 + 挡一挡视线)
    for index, (x, y) in enumerate(((-4.0, 2.0), (0.0, 2.0), (4.0, 2.0),
                                    (-4.0, -2.0), (0.0, -2.0), (4.0, -2.0))):
        make_box(f"课桌{index + 1}", (2.2, 1.4, 1.4), (x, y, 0.7),
                 (0.22, 0.20, 0.18, 1.0), scene=scene)
    player = make_box("教室玩家", (1.0, 1.0, 1.6), (-8.0, -8.0, 0.9),
                      (0.85, 0.85, 0.90, 1.0), scene=scene)
    note_spots = ((-6.0, -6.0), (6.0, -6.0), (0.0, 7.0))
    for name, (x, y) in zip(NOTE_NAMES, note_spots):
        make_box(name, (1.0, 1.0, 0.6), (x, y, 0.3),
                 (0.92, 0.90, 0.72, 1.0), scene=scene)
    make_box(TO_ROOM, (5.0, 5.0, 0.2), (0.0, -10.0, 0.1),
             (0.22, 0.32, 0.30, 1.0), scene=scene)
    side = make_camera(ROOM_SIDE, (0.0, -15.0, 5.5),
                       (math.radians(72.0), 0.0, 0.0), scene)
    make_camera(ROOM_TOP, (0.0, -6.0, 18.0), (math.radians(22.0), 0.0, 0.0),
                scene)
    # 开场运镜的落点(门口特写)+ 轨道曲线(相机沿它从『教室机位』俯冲过来)
    make_camera(ROOM_CLOSE, ROOM_CLOSE_POS,
                (math.radians(74.0), 0.0, 0.0), scene)
    make_curve(ROOM_TRACK, ROOM_TRACK_POINTS, scene)
    scene.camera = side
    make_light("月光", "SUN", (5.0, -5.0, 10.0), 1.4, (0.65, 0.72, 0.95),
               scene, rotation=(math.radians(52.0), 0.0, math.radians(28.0)),
               shadow=True)
    make_light("台灯", "POINT", (3.0, 2.0, 1.6), 160.0, (1.0, 0.78, 0.45),
               scene, cutoff=9.0)
    log(f"{ROOM}:地面 + 6 张课桌 + 玩家 + 3 张纸条 + {TO_ROOM} + "
        f"月光 / 台灯 + 两台相机")
    return player


def build_corridor():
    """第二关 走廊:手电筒 + 巡逻怪物(会听动静)+ 惊吓区。"""
    scene = bpy.data.scenes.new(CORRIDOR)
    make_box("走廊地面", (30.0, 20.0, 0.4), (0.0, 0.0, -0.2),
             (0.10, 0.11, 0.14, 1.0), scene=scene)
    for index, (x, y) in enumerate(((-9.0, 6.5), (9.0, 6.5),
                                    (-9.0, -6.5), (9.0, -6.5))):
        make_box(f"走廊立柱{index + 1}", (1.2, 1.2, 4.0), (x, y, 2.0),
                 (0.16, 0.15, 0.20, 1.0), scene=scene)
    # ⚠ 出生点离怪物(巡逻线 y=0)要**大于视野 9 米** —— 不然一进走廊就被
    #   发现、直接开追,恐怖游戏的"慢慢摸过去"就没有了(实测:放 (0,-8)
    #   只有 8 米,开局就在追击)。
    player = make_box("走廊玩家", (1.0, 1.0, 1.6), (-12.0, -8.0, 0.9),
                      (0.85, 0.85, 0.90, 1.0), scene=scene)
    enemy = make_box(ENEMY, (1.4, 1.4, 1.8), (0.0, 0.0, 0.9),
                     (0.55, 0.10, 0.12, 1.0), scene=scene)
    make_box(PATROL_A, (0.5, 0.5, 2.0), (-7.0, 0.0, 1.0),
             (0.25, 0.30, 0.28, 1.0), scene=scene)
    make_box(PATROL_B, (0.5, 0.5, 2.0), (7.0, 0.0, 1.0),
             (0.25, 0.30, 0.28, 1.0), scene=scene)
    make_box(SCARE_AREA, (6.0, 6.0, 0.2), (-11.0, 3.0, 0.1),
             (0.30, 0.10, 0.12, 1.0), scene=scene)
    make_box(TO_STAIRS, (5.0, 5.0, 0.2), (0.0, 8.0, 0.1),
             (0.22, 0.32, 0.30, 1.0), scene=scene)
    side = make_camera("走廊机位", (0.0, -17.0, 5.5),
                       (math.radians(72.0), 0.0, 0.0), scene)
    make_camera("走廊俯瞰", (0.0, -6.0, 20.0), (math.radians(22.0), 0.0, 0.0),
                scene)
    scene.camera = side
    torch = make_light(TORCH, "SPOT", (0.0, -8.0, 1.6), 1400.0,
                       (1.0, 0.96, 0.85), scene,
                       rotation=(math.radians(80.0), 0.0, 0.0),
                       spot_size=math.radians(42.0))
    log(f"{CORRIDOR}:地面 + 4 根立柱 + 玩家 + {ENEMY} + 两个巡逻点 + "
        f"{SCARE_AREA} + {TO_STAIRS} + {TORCH} + 两台相机")
    return scene, player, enemy, torch


def build_stairs():
    """第三关 楼梯间:门口 + 结局分支。"""
    scene = bpy.data.scenes.new(STAIRS)
    make_box("楼梯间地面", (18.0, 24.0, 0.4), (0.0, 0.0, -0.2),
             (0.10, 0.10, 0.13, 1.0), scene=scene)
    for index, z in enumerate((0.5, 1.1, 1.7, 2.3)):
        make_box(f"台阶{index + 1}", (8.0, 1.6, 0.6), (0.0, 4.0 + index * 1.6, z),
                 (0.20, 0.21, 0.26, 1.0), scene=scene)
    player = make_box("楼梯间玩家", (1.0, 1.0, 1.6), (0.0, -8.0, 0.9),
                      (0.85, 0.85, 0.90, 1.0), scene=scene)
    make_box(DOOR_AREA, (5.0, 5.0, 0.2), (-6.0, 0.0, 0.1),
             (0.20, 0.35, 0.30, 1.0), scene=scene)
    side = make_camera("楼梯间机位", (0.0, -16.0, 6.0),
                       (math.radians(72.0), 0.0, 0.0), scene)
    make_camera("楼梯间俯瞰", (0.0, -4.0, 18.0), (math.radians(24.0), 0.0, 0.0),
                scene)
    scene.camera = side
    make_light("楼梯间灯", "POINT", (0.0, 2.0, 3.4), 90.0, (0.85, 0.88, 1.0),
               scene, cutoff=12.0)
    log(f"{STAIRS}:地面 + 4 级台阶 + 玩家 + {DOOR_AREA} + 楼梯间灯 + 两台相机")
    return scene, player


def build_title():
    """第零幕 标题页:一块"看不见的管理器"(挂脚本)+ 地面 + 相机 + 灯。

    ⚠ 『脚本挂载目标』必须是**网格物体**(`exporter.script_targets` 只认
      `obj.type == "MESH"`),所以这里用一个缩得很小、摆在画面外的方盒当
      管理器 —— 不用 Empty。
    ⚠ 管理器不要用 StaticBody3D:AI 图是**全局**的,`required_base_class`
      会把它扫成 CharacterBody3D(脚本 extends 对不上节点类型,Godot 拒载)。
      直接用角色体最省事 —— 标题脚本里没有任何移动代码,它就一直站着。
    """
    scene = bpy.data.scenes.new(TITLE)
    make_box("标题地面", (20.0, 20.0, 0.4), (0.0, 0.0, -0.2),
             (0.06, 0.06, 0.09, 1.0), scene=scene)
    target = make_box("标题管理", (0.3, 0.3, 0.3), (0.0, 0.0, -6.0),
                      (0.10, 0.10, 0.14, 1.0), scene=scene)
    camera = make_camera("标题机位", (0.0, -10.0, 2.4),
                         (math.radians(76.0), 0.0, 0.0), scene)
    scene.camera = camera
    make_light("标题灯", "SUN", (3.0, -4.0, 6.0), 1.0, (0.70, 0.75, 0.92),
               scene, rotation=(math.radians(50.0), 0.0, math.radians(25.0)))
    log(f"{TITLE}:标题管理(挂脚本)+ 标题地面 + 标题机位 + 标题灯")
    return scene, target


def write_physics(scene, player, enemy=None, torch=None, triggers=()):
    """角色体 / 静态体 / 触发区 / 手电筒,按物体归位。"""
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
        elif enemy is not None and obj is enemy:
            physics.body_type = "CharacterBody3D"
            physics.dimension = "3"
        elif obj.name in set(triggers):
            physics.body_type = "Area3D"
            physics.dimension = "3"
            physics.is_trigger = True
        elif getattr(obj, "type", "") == "MESH":
            physics.body_type = "StaticBody3D"
            physics.dimension = "3"
    if torch is not None:
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
    """走廊的怪物 AI:巡逻 ←→ 追击 + 听见动静去查看。"""
    tree = bpy.data.node_groups.new(AI_TREE_NAME, "GKGameAiTree")
    tree.use_fake_user = True

    patrol = _add(tree, "GKNodeAiState", "巡逻", -60.0, 0.0,
                  gk_state_name="巡逻", gk_behavior="PATROL",
                  gk_patrol_a=PATROL_A, gk_patrol_b=PATROL_B,
                  gk_speed=PATROL_SPEED, gk_stop_distance=0.7,
                  gk_facing="MOVE")
    chase = _add(tree, "GKNodeAiState", "追击", 700.0, 0.0,
                 gk_state_name="追击", gk_behavior="CHASE",
                 gk_goal="走廊玩家", gk_speed=CHASE_SPEED,
                 gk_stop_distance=CHASE_STOP, gk_facing="GOAL")
    look = _add(tree, "GKNodeAiState", "去查看", 700.0, 340.0,
                gk_state_name="去查看", gk_behavior="INVESTIGATE",
                gk_speed=LOOK_SPEED, gk_stop_distance=LOOK_STOP,
                gk_facing="MOVE")
    see = _add(tree, "GKNodeAiTransition", "看见玩家", 320.0, 180.0,
               gk_cond_source="SEE", gk_goal="走廊玩家", gk_sight=SIGHT)
    back = _add(tree, "GKNodeAiTransition", "丢失玩家", 320.0, -210.0,
                gk_cond_source="LOST", gk_goal="走廊玩家", gk_sight=LOST_RANGE)
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
    # 巡逻有两条转场指进来 -> 第二条必须用『进入 2』
    _link(tree, calm, "转到", patrol, "进入 2")
    log(f"AI 图『{tree.name}』:巡逻 --看见({SIGHT:g})--> 追击 "
        f"--丢失({LOST_RANGE:g})--> 巡逻;巡逻 --听见--> 去查看 --安静--> 巡逻")
    return tree


def _preset(scene, player, logic_name, class_name, side_cam, top_cam):
    from BlenderGameKit import presets
    logic, _anim, notes = presets.create_character_preset(
        scene, character=player,
        logic_name=logic_name,
        class_name=class_name,
        with_animation=False,
        with_ports=False,
        camera_side=side_cam,
        camera_top=top_cam,
    )
    for note in notes:
        log(f"预设({logic_name}): {note}")
    ready = None
    for node in logic.nodes:
        if node.bl_idname == "GKNodeOnReady":
            ready = node
    if ready is None:
        raise RuntimeError(f"{logic_name} 里没有『准备』节点")
    return logic, ready


def build_room_logic(scene, player):
    """第一关逻辑:提示 + 3 张纸条 + 开场运镜 + (存档)切到走廊。"""
    logic, ready = _preset(scene, player, "教室逻辑", "HorrorRoomPlayer",
                           ROOM_SIDE, ROOM_TOP)
    hint = _add(logic, "GKNodeUiText", "教室提示", -300.0, 1200.0,
                gk_text=("WASD 移动 / 空格 跳 / 鼠标转视角 / Tab 上帝机 / "
                         "按住 K 弄出动静"),
                gk_x=18.0, gk_y=18.0, gk_size=18)
    goal = _add(logic, "GKNodeUiText", "教室目标", -300.0, 1040.0,
                gk_text=f"晚自习后教学楼熄了灯……先找齐 {NOTE_GOAL} 张纸条",
                gk_x=18.0, gk_y=46.0, gk_size=18)
    bgm = _add(logic, "GKNodePlayMusic", "教室BGM", -300.0, 880.0,
               gk_path=BGM_FILE, gk_volume=-18.0, gk_fade=2.0)
    vignette = _add(logic, "GKNodeScreenVignette", "教室暗角", -300.0, 720.0,
                    gk_amount=0.35, gk_radius=0.65, gk_edge=0.40)
    # 开场运镜(第七十轮):相机沿『教室运镜轨道』从『教室机位』俯冲到
    # 『教室特写』(2.5 秒 · 缓出)。这就是 demo_curve 那套做法。
    opening = _add(logic, "GKNodeCameraSetCurrent", "开场运镜", -300.0, 560.0,
                   gk_target=ROOM_CLOSE, gk_blend=OPENING_BLEND,
                   gk_blend_trans="EASE_OUT",
                   gk_path_mode="CURVE", gk_path_curve=ROOM_TRACK)
    # ⚠ **必须切回来**:『相机_切换』把当前相机换成了特写机位,而预设的弹簧臂
    #   只驱动『教室机位』—— 不切回来的话人还能走、视角却钉在特写上不动,
    #   看起来就是"卡住"(用户实测报过)。留 1.5 秒给玩家看清,再 1 秒切回。
    hold = _add(logic, "GKNodeWait", "特写停留", -300.0, 400.0)
    hold.inputs["秒"].default_value = OPENING_HOLD
    back = _add(logic, "GKNodeCameraSetCurrent", "回第三人称", -300.0, 260.0,
                gk_target=ROOM_SIDE, gk_blend=RETURN_BLEND,
                gk_blend_trans="EASE_OUT")
    _link(logic, ready, "执行", hint, "执行")
    tail = hint
    for node in (goal, bgm, vignette, opening, hold, back):
        _link(logic, tail, "完成", node, "执行")
        tail = node

    # 3 张纸条:各一条独立触发链(一个执行引脚只能接一条线)
    for index, name in enumerate(NOTE_NAMES):
        y = 480.0 - index * 240.0
        touched = _add(logic, "GKNodeOnAreaEntered", f"碰到{name}", -300.0, y,
                       gk_area=name, gk_filter="教室玩家")
        add_one = _add(logic, "GKNodeAddVar", f"纸条+1_{index + 1}", -60.0, y,
                       gk_var_name="纸条数", gk_amount=1.0,
                       gk_clamp=True, gk_min=0.0, gk_max=float(NOTE_GOAL))
        say = _add(logic, "GKNodePrint", f"拾到纸条{index + 1}", 180.0, y,
                   gk_text="拾到一张纸条……")
        _link(logic, touched, "执行", add_one, "执行")
        _link(logic, add_one, "完成", say, "执行")

    # 走到门 -> 把纸条数写进存档 -> 保存 -> 切到走廊
    door = _add(logic, "GKNodeOnAreaEntered", "走到教室门口", -300.0, -420.0,
                gk_area=TO_ROOM, gk_filter="教室玩家")
    read = _add(logic, "GKNodeGetVar", "读纸条数", -60.0, -420.0,
                gk_var_name="纸条数")
    write = _add(logic, "GKNodeSaveSet", "存纸条数", 160.0, -420.0,
                 gk_key="纸条数")
    save = _add(logic, "GKNodeSaveWrite", "保存纸条数", 380.0, -420.0,
                gk_slot="save1", gk_save_verify=True)
    change = _add(logic, "GKNodeChangeScene", "去走廊", 600.0, -420.0,
                  gk_scene_path=CORRIDOR_RES)
    _link(logic, read, "值", write, "值")
    _link(logic, door, "执行", write, "执行")
    _link(logic, write, "完成", save, "执行")
    _link(logic, save, "完成", change, "执行")
    log(f"{ROOM}逻辑:提示/BGM/暗角/开场运镜 + 3 张纸条 + "
        f"门口存纸条数 -> 切 {CORRIDOR_RES}")
    return logic


def build_title_logic(scene):
    """标题页逻辑:显示标题 + 『开始游戏』按钮 -> 切到教室。"""
    logic = bpy.data.node_groups.new("标题逻辑", "GKGameLogicTree")
    logic.use_fake_user = True
    ready = _add(logic, "GKNodeOnReady", "每当_准备", -300.0, 0.0)
    title = _add(logic, "GKNodeUiText", "标题文字", -60.0, 0.0,
                 gk_text="恐怖示例 · 熄灯的教学楼", gk_align="TOP_CENTER",
                 gk_margin=120.0, gk_size=42)
    tip = _add(logic, "GKNodeUiText", "提示文字", 200.0, 0.0,
               gk_text="点『开始游戏』走进教室", gk_align="TOP_CENTER",
               gk_margin=196.0, gk_size=20)
    start = _add(logic, "GKNodeUiButton", "开始按钮", 460.0, 0.0,
                 gk_text="开始游戏", gk_align="CENTER", gk_margin=0.0,
                 gk_width=220.0, gk_height=56.0)
    change = _add(logic, "GKNodeChangeScene", "去教室", 720.0, 0.0,
                  gk_scene_path=ROOM_RES)
    _link(logic, ready, "执行", title, "执行")
    _link(logic, title, "完成", tip, "执行")
    _link(logic, tip, "完成", start, "执行")
    # ⚠ 按钮用『点击』出口:『完成』= 按钮建好了(建完就往下跑会把游戏立刻切走)
    _link(logic, start, "点击", change, "执行")
    _apply_settings(scene, (("dimension", "3"), ("toon_follow_light", True)))
    settings = getattr(scene, "gk_settings", None)
    if settings is not None and hasattr(settings, "logic_tree"):
        try:
            settings.logic_tree = logic.name
        except Exception:                                 # noqa: BLE001
            pass
    log(f"{TITLE}逻辑:标题 + 开始按钮 -> 切 {ROOM_RES}")
    return logic


def build_corridor_logic(scene, player, enemy, ai_tree):
    """第二关逻辑:手电筒氛围 + 心跳 + 动静 + 听觉 AI + 惊吓区 + 切楼梯间。"""
    logic, ready = _preset(scene, player, "走廊逻辑", "HorrorCorridorPlayer",
                           "走廊机位", "走廊俯瞰")
    hint = _add(logic, "GKNodeUiText", "走廊提示", -300.0, 1500.0,
                gk_text="走廊里有东西……按住 K 弄出动静能把它引开",
                gk_x=18.0, gk_y=18.0, gk_size=18)
    bgm = _add(logic, "GKNodePlayMusic", "走廊BGM", -300.0, 1340.0,
               gk_path=BGM_FILE, gk_volume=-14.0, gk_fade=1.5)
    vignette = _add(logic, "GKNodeScreenVignette", "走廊暗角", -300.0, 1180.0,
                    gk_amount=0.45, gk_radius=0.60, gk_edge=0.40)
    enable = _add(logic, "GKNodeAiEnable", "启动怪物AI", -300.0, 1020.0,
                  gk_tree_name=ai_tree.name, gk_target=ENEMY,
                  gk_debug_print=True)
    _link(logic, ready, "执行", hint, "执行")
    tail = hint
    for node in (bgm, vignette, enable):
        _link(logic, tail, "完成", node, "执行")
        tail = node

    # 心跳(每物理帧)
    phys_beat = _add(logic, "GKNodeOnPhysicsProcess", "物理帧(心跳)",
                     -300.0, 680.0)
    tension = _add(logic, "GKNodeTension", "紧张度", -60.0, 680.0,
                   gk_threat=ENEMY, gk_near=TENSION_NEAR, gk_far=TENSION_FAR)
    heartbeat = _add(logic, "GKNodeHeartbeat", "心跳", 300.0, 680.0,
                     gk_path=HEART_FILE, gk_slow_interval=1.1,
                     gk_fast_interval=0.32, gk_quiet_db=-19.0, gk_loud_db=-5.0,
                     gk_quiet=0.15, gk_smooth=1.5)
    _link(logic, phys_beat, "执行", heartbeat, "执行")
    _link(logic, tension, "紧张度", heartbeat, "紧张度")

    # 弄出动静(每物理帧)
    phys_noise = _add(logic, "GKNodeOnPhysicsProcess", "物理帧(动静)",
                      -300.0, 400.0)
    noise_key = _add(logic, "GKNodeKey", "按住K", -60.0, 400.0,
                     gk_key=NOISE_KEY, gk_action=NOISE_ACTION,
                     gk_trigger="PRESSED")
    noise_branch = _add(logic, "GKNodeBranch", "按住K?", 140.0, 400.0)
    noise = _add(logic, "GKNodeNoise", "弄出动静", 340.0, 400.0,
                 gk_loudness=NOISE_LOUD, gk_interval=NOISE_INTERVAL,
                 gk_target="走廊玩家", gk_debug_print=True)
    _link(logic, noise_key, "结果", noise_branch, "条件")
    _link(logic, phys_noise, "执行", noise_branch, "执行")
    _link(logic, noise_branch, "是", noise, "执行")

    # 惊吓区:三连 + 压低总线
    scared = _add(logic, "GKNodeOnAreaEntered", "走过惊吓区", -300.0, 120.0,
                  gk_area=SCARE_AREA, gk_filter="走廊玩家")
    scare = _add(logic, "GKNodeScare", "受惊一下", -60.0, 120.0,
                 gk_strength=18.0, gk_duration=0.5,
                 gk_color=(0.85, 0.10, 0.10, 1.0), gk_alpha=0.55,
                 gk_flash_time=0.35, gk_path=STING_FILE, gk_volume=-3.0)
    duck = _add(logic, "GKNodeBusVolume", "压低一点", 200.0, 120.0,
                gk_bus="Master", gk_db=-18.0, gk_fade=0.3)
    _link(logic, scared, "执行", scare, "执行")
    _link(logic, scare, "完成", duck, "执行")

    # 走到楼梯间 -> 切关卡
    door = _add(logic, "GKNodeOnAreaEntered", "走到楼梯间", -300.0, -260.0,
                gk_area=TO_STAIRS, gk_filter="走廊玩家")
    change = _add(logic, "GKNodeChangeScene", "去楼梯间", -60.0, -260.0,
                  gk_scene_path=STAIRS_RES)
    _link(logic, door, "执行", change, "执行")
    log(f"{CORRIDOR}逻辑:提示/BGM/暗角/AI + 心跳 + 动静 + 惊吓区 + "
        f"楼梯间门 -> 切 {STAIRS_RES}")
    return logic


def build_stairs_logic(scene, player):
    """第三关逻辑:载入纸条数 -> 门口判够不够 -> 两结局 + 存档。"""
    logic, ready = _preset(scene, player, "楼梯间逻辑", "HorrorStairsPlayer",
                           "楼梯间机位", "楼梯间俯瞰")
    load = _add(logic, "GKNodeSaveLoad", "载入存档", -300.0, 1200.0,
                gk_slot="save1")
    read_back = _add(logic, "GKNodeSaveGet", "读回纸条数", -60.0, 1200.0,
                     gk_key="纸条数", gk_var="纸条数", gk_default=0.0)
    hint = _add(logic, "GKNodeUiText", "楼梯间提示", 180.0, 1200.0,
                gk_text="楼梯间的门就在左边 —— 按 1 或 2 决定结局",
                gk_x=18.0, gk_y=18.0, gk_size=18)
    bgm = _add(logic, "GKNodePlayMusic", "楼梯间BGM", 420.0, 1200.0,
               gk_path=BGM_FILE, gk_volume=-20.0, gk_fade=1.5)
    vignette = _add(logic, "GKNodeScreenVignette", "楼梯间暗角", 660.0, 1200.0,
                    gk_amount=0.55, gk_radius=0.55, gk_edge=0.35)
    _link(logic, ready, "执行", load, "执行")
    _link(logic, load, "完成", read_back, "执行")
    tail = read_back
    for node in (hint, bgm, vignette):
        _link(logic, tail, "完成", node, "执行")
        tail = node

    # 门口:纸条够 3 张才给选择
    door = _add(logic, "GKNodeOnAreaEntered", "走到门口", -300.0, 600.0,
                gk_area=DOOR_AREA, gk_filter="楼梯间玩家")
    read = _add(logic, "GKNodeGetVar", "读纸条数", -60.0, 760.0,
                gk_var_name="纸条数")
    # ⚠ B 要显式填 3:它是**未连线时的字面量**(gk_b),不填就是默认 0,
    #   门槛会变成"纸条数 >= 0"(等于没门槛)—— 踩过一次。
    compare = _add(logic, "GKNodeCompare", "够3张吗?", 160.0, 760.0,
                   gk_op="GE", gk_b=str(NOTE_GOAL))
    branch = _add(logic, "GKNodeBranch", "纸条够吗?", 380.0, 600.0)
    short = _add(logic, "GKNodeUiText", "还差几张", 640.0, 900.0,
                 gk_text="门打不开……纸条还没找齐", gk_x=18.0, gk_y=60.0,
                 gk_size=22)
    _link(logic, read, "值", compare, "A")
    _link(logic, compare, "结果", branch, "条件")
    _link(logic, door, "执行", branch, "执行")
    _link(logic, branch, "否", short, "执行")

    choice = _add(logic, "GKNodeUiChoice", "结局选择", 640.0, 300.0,
                  gk_question="楼梯间的门开了,你要怎么做?",
                  gk_option_count=2,
                  gk_options="回头救同学|独自逃出", gk_speed=0.0)
    _link(logic, branch, "是", choice, "执行")

    endings = ((1, "选项1", "回头救她"), (2, "选项2", "独自逃出"))
    for number, pin, label in endings:
        y = 120.0 - number * 300.0
        value = _add(logic, "GKNodeConstFloat", f"结局值{number}", 880.0, y,
                     gk_value=float(number))
        set_end = _add(logic, "GKNodeSetVar", f"记结局{number}", 1100.0, y,
                       gk_var_name="结局")
        write = _add(logic, "GKNodeSaveSet", f"写结局{number}", 1340.0, y,
                     gk_key="结局")
        save = _add(logic, "GKNodeSaveWrite", f"保存{number}", 1580.0, y,
                    gk_slot="save1", gk_save_verify=True)
        text = _add(logic, "GKNodeUiText", f"结局文字{number}", 1820.0, y,
                    gk_text=f"结局:{label}", gk_x=18.0, gk_y=60.0, gk_size=26)
        # 演完停 3 秒让玩家看清,再回标题页(一段可循环的短篇)
        wait = _add(logic, "GKNodeWait", f"停留{number}", 2060.0, y)
        wait.inputs["秒"].default_value = 3.0
        back = _add(logic, "GKNodeChangeScene", f"回标题{number}", 2300.0, y,
                    gk_scene_path=TITLE_RES)
        _link(logic, value, "值", set_end, "值")
        _link(logic, value, "值", write, "值")
        _link(logic, choice, pin, set_end, "执行")
        _link(logic, set_end, "完成", write, "执行")
        _link(logic, write, "完成", save, "执行")
        _link(logic, save, "完成", text, "执行")
        _link(logic, text, "完成", wait, "执行")
        _link(logic, wait, "完成", back, "执行")
    log(f"{STAIRS}逻辑:载入纸条数 + 门口判 >=3 + 两结局 + 存档 + 回标题")
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
    """生成三个小 wav(示例自包含):心跳 / 惊吓 / 环境低鸣。"""
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


def save_blend(path):
    if not bpy.app.background:
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return
    blocks = set(bpy.data.objects) | set(bpy.data.meshes) | \
        set(bpy.data.materials) | set(bpy.data.node_groups) | \
        set(bpy.data.cameras) | set(bpy.data.lights) | set(bpy.data.curves)
    scenes = list(bpy.data.scenes)
    bpy.data.libraries.write(path, blocks | set(scenes), fake_user=True)


def main():
    from BlenderGameKit import asset_export, project

    os.makedirs(OUT_DIR, exist_ok=True)
    room = clear_all()

    # ---- 第零幕 标题页(开局进这里)----
    # ⚠ 先建在**最后**也行:导出时场景名 = .tscn 名,主场景最后显式指过去。
    title, title_target = build_title()
    write_physics(title, title_target)
    build_title_logic(title)
    _apply_settings(title, (("dimension", "3"),) + ENV_TITLE)

    # ---- 第一关 教室(用已有的那个场景)----
    room_player = build_room(room)
    write_physics(room, room_player,
                  triggers=set(NOTE_NAMES) | {TO_ROOM})
    build_room_logic(room, room_player)
    _apply_settings(room, (("dimension", "3"),
                           ("toon_follow_light", True)) + ENV_ROOM)

    # ---- 第二关 走廊 ----
    corridor, corridor_player, enemy, torch = build_corridor()
    write_physics(corridor, corridor_player, enemy=enemy, torch=torch,
                  triggers={SCARE_AREA, TO_STAIRS})
    ai_tree = build_ai_tree()
    build_corridor_logic(corridor, corridor_player, enemy, ai_tree)
    _apply_settings(corridor, (("dimension", "3"),
                               ("toon_follow_light", True)) + ENV_DARK)

    # ---- 第三关 楼梯间 ----
    stairs, stairs_player = build_stairs()
    write_physics(stairs, stairs_player, triggers={DOOR_AREA})
    build_stairs_logic(stairs, stairs_player)
    _apply_settings(stairs, (("dimension", "3"),
                             ("toon_follow_light", True)) + ENV_STAIRS)

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
    # 主场景显式指到**标题页**:开局先给标题,点『开始游戏』才进教室
    exported = {f"res://scenes/{e['scene_name']}.tscn"
                for e in result.get("scenes", []) if e.get("ok")}
    if TITLE_RES in exported:
        project.set_main_scene(directory, TITLE_RES)
    log(f"主场景: {TITLE_RES}")

    write_audio(directory)

    save_blend(BLEND_PATH)
    log(f"已保存 {BLEND_PATH}")
    log(f"工程目录: {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())