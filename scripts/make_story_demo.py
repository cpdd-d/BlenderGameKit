"""3D 恐怖剧情闯关(第一/三人称可切换)示例 —— 生成脚本。

用法:
    blender -b --gpu-backend vulkan --python scripts\\make_story_demo.py

产出:
    examples/demo_story/恐怖闯关示例.blend       (一个 .blend,**三个 Blender 场景**)
    examples/demo_story/恐怖闯关示例_godot/      (可直接用 Godot 打开;每关一张 .tscn)

── 三关(一句话一个)──────────────────────────────────────────────
在 Blender 里"一关 = 一个场景",每个场景用场景设置里的 `logic_tree` 指到
自己的『游戏逻辑』图,`asset_export.export_all_scenes()` 就会一关导一张 .tscn,
关底用『场景_切换关卡』切过去(`gk_settings` **每场景一份** —— 所以每关的
节点图 / 环境 / BGM 互不串)。

| 关 | Blender 场景 | 内容 | 用到的 |
|---|---|---|---|
| 1 | 大堂   | 门厅(地面+四壁+柱子)、章节卡+HUD、钥匙(拾取)、**手电(电量)**、会升起的大门、存档 | 角色预设 / 触发区 / 灯光_设置 / 时间轴 / 存档 |
| 2 | 走廊   | 长走廊、开场对话+**剧情选择**、巡逻 AI(3 点)+看见追击 / 丢失去查看、近身**惊吓三连**+心跳紧张度、**2 个柜子躲藏**、尽头门 | AI_状态 / AI_转场 / 惊吓 / 心跳 / 暗角 / 选择 |
| 3 | 结局   | 小房间、**结局选择**(回头救她 / 独自逃出去)、存档(结局键)、结局大字、**BGM 淡出**、回大堂**闭环** | 对话_选择 / 存档 / 总线音量 / 切关卡 |

**第一 / 第三人称**(每关共用同一套约定,事实来自 `presets.py`):
预设角色会建**两根弹簧臂 + 两台相机**,并按 **C / V** 在它们之间硬切 ——
    * **C 键 → 第一人称机位**(每关一台:`大堂第一人称机位` / `走廊第一人称机位` /
      `结局第一人称机位`):把『弹簧臂(俯瞰)』那条杆子的臂长收到 0.25、
      高度抬到 1.55(= 眼睛)、俯仰 0(平视),相机基本贴在头上 —— 这就是
      **第一人称**(见 `codegen/camera.py`:"臂长填 0 = 杆子收到底,相机贴在
      枢轴上 —— 那是第一人称的做法")。
    * **V 键 → 第三人称机位**(`大堂第三人称机位` / …):预设默认那根杆子
      (臂长 5 / 高度 1.4 / 俯仰 12)= **第三人称**(越肩弹簧臂,撞墙自动缩短)。
两台相机都在场景里建好(名字就是上面那两套);鼠标转视角 / 滚轮缩放 / WASD
走都由预设的图提供。

⚠ 相机 / 角色 / 物体的名字**每关要唯一**(`大堂玩家` / `走廊玩家` / …)——
  一个 .blend 里所有场景共用一个命名空间,重名会被 Blender 悄悄改成
  `玩家.001`,而图里的弹簧臂 / 触发过滤都是按名字找对象的(踩过)。

按键:WASD 走 / 空格 跳 / 鼠标转视角 / 滚轮 缩放 / **C 第一人称 · V 第三人称** /
**F 手电开关键** / 大堂里走钥匙、走大门 / 走廊里钻柜子 / 结局按 **1 或 2**。
(鼠标被锁定,先点一下游戏窗口再按。)

跑法(Godot):打开 `恐怖闯关示例_godot` 直接 F5(主场景 = 大堂)。
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

OUT_DIR = os.path.join(ROOT, "examples", "demo_story")
BLEND_PATH = os.path.join(OUT_DIR, "恐怖闯关示例.blend")

# ── 三关(Blender 场景名 = 导出的 .tscn 名)────────────────────────────
LOBBY = "大堂"
CORRIDOR = "走廊"
ENDING = "结局"

LOBBY_RES = "res://scenes/大堂.tscn"
CORRIDOR_RES = "res://scenes/走廊.tscn"
ENDING_RES = "res://scenes/结局.tscn"

# ── 每关的名字(场景名 / 角色名 / 相机名一眼能对上)──────────────────
# ⚠ 名字必须**每关唯一**:一个 .blend 里所有场景的物体共用一个命名空间,
#   重名的会被 Blender 悄悄改成『玩家.001』『第三人称机位.001』;而这套图
#   里相机 / 触发过滤 / 弹簧臂都是**按名字**找对象的 —— 一改名就全对不上
#   (第一版三个场景都用『第三人称机位』,第二 / 三关的弹簧臂直接找不到相机)。
#   所以按关卡加前缀(和 demo_horror 的 教室机位 / 走廊机位 同一个做法)。
PLAYER_LOBBY = "大堂玩家"
PLAYER_CORRIDOR = "走廊玩家"
PLAYER_ENDING = "结局玩家"
CAM3_LOBBY = "大堂第三人称机位"
CAM1_LOBBY = "大堂第一人称机位"
CAM3_CORRIDOR = "走廊第三人称机位"
CAM1_CORRIDOR = "走廊第一人称机位"
CAM3_ENDING = "结局第三人称机位"
CAM1_ENDING = "结局第一人称机位"
TORCH = "手电筒"

# 大堂
KEY = "钥匙"
KEY_COUNT = "钥匙数"
DOOR_PANEL = "大门门板"
DOOR_AREA = "大门区域"
DOOR_Z_DOWN = 2.3            # 门板中心 z(关着)
DOOR_Z_UP = 6.4              # 门板中心 z(升起后;Blender 的 Z = Godot 的 Y)
BATTERY = "手电电量"
TORCH_ON = "手电开"
BATTERY_MAX = 100.0
BATTERY_DRAIN = 1.5          # 每秒耗电(亮度开销)

# 走廊
ENEMY = "怪物"
PATROL_A = "巡逻点甲"
PATROL_B = "巡逻点乙"
PATROL_C = "巡逻点丙"
CABINET_A = "柜子甲"
CABINET_B = "柜子乙"
CORRIDOR_END = "走廊尽头"
AI_TREE_NAME = "怪物AI"
PATROL_STATE = "巡逻"
HIDE_STATE = "躲藏着"        # 一个**不存在**的状态名 —— 见"躲藏"那段注释
AI_STATE_VAR = "ai_state"    # 和『AI_启用』的『状态变量名』一致
CHOICE_VAR = "走廊选择"

SIGHT = 6.0
LOST_RANGE = 9.0
PATROL_SPEED = 2.6
CHASE_SPEED = 4.4
INVESTIGATE_SPEED = 3.4
TENSION_NEAR = 3.0
TENSION_FAR = 16.0
ATTACK_RANGE = 1.8           # 玩家离怪物这么近 = "贴脸",触发惊吓三连

FIRST_ARM = 0.25             # 第一人称弹簧臂长(收到贴头)
EYE_HEIGHT = 1.55            # 第一人称枢轴高度(眼睛)
THIRD_ARM = 5.0
THIRD_HEIGHT = 1.4
THIRD_PITCH = 12.0
MOVE_SPEED = 5.5

# ── 音频(Node 里引用的是 res:// 路径;文件由 write_audio 生成)──────────
PICKUP_FILE = "res://audio/pickup.wav"
DOOR_FILE = "res://audio/door.wav"
STING_FILE = "res://audio/sting.wav"
HEART_FILE = "res://audio/heartbeat.wav"
# 开局 BGM 走**侧栏设置**(scene_bgm),导出时会把本地文件复制进 audio/ ——
# 所以这里给的是**磁盘路径**,文件先生成到 OUT_DIR 下一个源目录里。
BGM_DIR = os.path.join(OUT_DIR, "_bgm")
BGM_LOBBY = os.path.join(BGM_DIR, "bgm_lobby.wav")
BGM_CORRIDOR = os.path.join(BGM_DIR, "bgm_corridor.wav")
BGM_ENDING = os.path.join(BGM_DIR, "bgm_ending.wav")


def log(message):
    print(f"[恐怖闯关] {message}")


# ==========================================================================
# 场景与物体(每个物体链接进指定的 Blender 场景)
# ==========================================================================
def clear_all():
    """清空所有数据块,**不能用 `read_factory_settings`**(会把插件一起卸载)。

    清完把唯一剩下的场景命名为『大堂』(另外两关在后面新建)。
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
    scene.name = LOBBY
    return scene


def make_box(name, size, location, color=None, scene=None, glow=None):
    """一个长方体(地面 / 墙 / 柱子 / 门板 / 钥匙都用它)。

    用底层 API 建 + 直接链接进目标场景 —— **不用 `bpy.ops`**(那个只会加进
    当前上下文场景,造不出第二 / 第三关)。

    glow = (颜色, 强度):给材质加自发光(钥匙"一闪一闪"看着像件道具,
    Blender 4.x 的 Principled 输入名是 `Emission Color` / `Emission Strength`)。
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
            if glow:
                try:
                    principled.inputs["Emission Color"].default_value = glow[0]
                    principled.inputs["Emission Strength"].default_value = glow[1]
                except Exception:                         # noqa: BLE001
                    pass
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


def make_light(name, kind, location, energy, color, scene,
               rotation=(0.0, 0.0, 0.0), cutoff=12.0, spot_size=None,
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
    """把一批导出设置写进场景的 gk_settings(缺属性 / 只读就跳过)。

    用 getattr / try 包着 —— 老版本 .blend 或精简环境里某个属性可能不存在,
    写设置绝不该让整个脚本崩掉(照 make_demo_level 的写法)。
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


# ── 三关的环境氛围(都走『显示_环境氛围』那张卡的字段)──────────────────
ENV_LOBBY = (
    ("env_enabled", True),
    ("env_background", "COLOR"),
    ("env_bg_color", (0.045, 0.05, 0.075, 1.0)),
    ("env_ambient_color", (0.52, 0.56, 0.72, 1.0)),
    ("env_ambient_energy", 0.22),
    ("env_fog", True),
    ("env_fog_color", (0.20, 0.22, 0.32, 1.0)),
    ("env_fog_density", 0.035),
    ("env_glow", True),
    ("env_glow_intensity", 0.9),
    ("env_glow_bloom", 0.15),
    ("env_tonemap", "ACES"),
)
ENV_CORRIDOR = (
    ("env_enabled", True),
    ("env_background", "COLOR"),
    ("env_bg_color", (0.015, 0.016, 0.03, 1.0)),
    ("env_ambient_color", (0.38, 0.42, 0.60, 1.0)),
    ("env_ambient_energy", 0.09),
    ("env_fog", True),
    ("env_fog_color", (0.12, 0.14, 0.22, 1.0)),
    ("env_fog_density", 0.10),
    ("env_glow", True),
    ("env_glow_intensity", 1.3),
    ("env_glow_bloom", 0.25),
    ("env_tonemap", "ACES"),
)
ENV_ENDING = (
    ("env_enabled", True),
    ("env_background", "COLOR"),
    ("env_bg_color", (0.03, 0.025, 0.045, 1.0)),
    ("env_ambient_color", (0.45, 0.44, 0.62, 1.0)),
    ("env_ambient_energy", 0.16),
    ("env_fog", True),
    ("env_fog_color", (0.16, 0.15, 0.24, 1.0)),
    ("env_fog_density", 0.05),
    ("env_glow", True),
    ("env_glow_intensity", 1.0),
    ("env_glow_bloom", 0.2),
    ("env_tonemap", "ACES"),
)


# ==========================================================================
# 三关:物体
# ==========================================================================
def build_lobby(scene):
    """第一关 大堂:门厅(地面+四壁+柱子)+ 钥匙 + 手电 + 会升起的大门。

    ⚠ 用**已有的那个场景**(clear_all 已经把它改名成『大堂』),不要再
    `scenes.new("大堂")` —— 重名会被 Blender 改成『大堂.001』,于是导出
    多出一关空场景(踩过)。
    ⚠ 场景里**不能只有角色一个网格**:整场景只有一个网格时插件不生成主
    场景(踩过)—— 所以门厅的地面/墙/柱子既是空间感也是"有主场景"的保证。
    """
    make_box("大堂地面", (30.0, 24.0, 0.4), (0.0, 0.0, -0.2),
             (0.13, 0.13, 0.17, 1.0), scene=scene)
    make_box("大堂北墙", (30.0, 0.5, 6.0), (0.0, 12.0, 3.0),
             (0.17, 0.18, 0.23, 1.0), scene=scene)
    make_box("大堂南墙", (30.0, 0.5, 6.0), (0.0, -12.0, 3.0),
             (0.17, 0.18, 0.23, 1.0), scene=scene)
    make_box("大堂东墙", (0.5, 24.0, 6.0), (15.0, 0.0, 3.0),
             (0.17, 0.18, 0.23, 1.0), scene=scene)
    make_box("大堂西墙", (0.5, 24.0, 6.0), (-15.0, 0.0, 3.0),
             (0.17, 0.18, 0.23, 1.0), scene=scene)
    for index, (x, y) in enumerate(((-7.0, 3.0), (0.0, 3.0), (7.0, 3.0))):
        make_box(f"大堂柱{index + 1}", (1.4, 1.4, 6.0), (x, y, 3.0),
                 (0.22, 0.22, 0.27, 1.0), scene=scene)
    # 大门:一块会升起的门板 + 门口触发区(判定"钥匙够不够")
    make_box(DOOR_PANEL, (4.0, 0.7, 4.6), (0.0, 11.6, DOOR_Z_DOWN),
             (0.30, 0.24, 0.16, 1.0), scene=scene)
    make_box(DOOR_AREA, (5.0, 3.0, 0.2), (0.0, 10.0, 0.1),
             (0.22, 0.32, 0.30, 1.0), scene=scene)
    # 钥匙:悬空的小盒子 + 自发光(一闪一闪像个道具)
    make_box(KEY, (0.8, 0.8, 0.8), (-8.0, 5.0, 1.3),
             (0.95, 0.82, 0.30, 1.0), scene=scene,
             glow=((1.0, 0.85, 0.35, 1.0), 4.0))
    player = make_box(PLAYER_LOBBY, (1.0, 1.0, 1.8), (0.0, -8.0, 1.0),
                      (0.80, 0.82, 0.90, 1.0), scene=scene)
    make_camera(CAM3_LOBBY, (0.0, -16.0, 5.5),
                (math.radians(72.0), 0.0, 0.0), scene)
    make_camera(CAM1_LOBBY, (0.0, -8.0, 1.6),
                (math.radians(90.0), 0.0, 0.0), scene)
    scene.camera = bpy.data.objects[CAM3_LOBBY]
    # 手电筒:聚光灯,运行时每帧对齐相机(手电筒的手感)—— 开局关着(见逻辑图)
    make_light(TORCH, "SPOT", (0.0, -8.0, 1.6), 1400.0, (1.0, 0.96, 0.85),
               scene, rotation=(math.radians(80.0), 0.0, 0.0),
               spot_size=math.radians(46.0))
    make_light("大堂吊灯", "POINT", (0.0, 0.0, 5.0), 120.0, (0.85, 0.80, 0.62),
               scene, cutoff=16.0)
    log(f"{LOBBY}:地面 + 四壁 + 3 根柱子 + {DOOR_PANEL} + {DOOR_AREA} + "
        f"{KEY} + 玩家 + {TORCH} + 两台相机")
    return player


def build_corridor():
    """第二关 走廊:长走廊 + 巡逻怪物(3 个巡逻点)+ 2 个柜子 + 尽头门。"""
    scene = bpy.data.scenes.new(CORRIDOR)
    make_box("走廊地面", (48.0, 14.0, 0.4), (0.0, 0.0, -0.2),
             (0.11, 0.11, 0.15, 1.0), scene=scene)
    make_box("走廊墙北", (48.0, 0.5, 6.0), (0.0, 7.0, 3.0),
             (0.15, 0.15, 0.20, 1.0), scene=scene)
    make_box("走廊墙南", (48.0, 0.5, 6.0), (0.0, -7.0, 3.0),
             (0.15, 0.15, 0.20, 1.0), scene=scene)
    for index, x in enumerate((-13.0, 0.0, 13.0)):
        make_box(f"门框{index + 1}", (0.5, 13.0, 0.5), (x, 0.0, 4.6),
                 (0.20, 0.20, 0.26, 1.0), scene=scene)
    make_box("走廊尽头门", (3.0, 0.6, 4.6), (23.2, 0.0, 2.3),
             (0.26, 0.30, 0.24, 1.0), scene=scene)
    make_box(CORRIDOR_END, (5.0, 5.0, 0.2), (21.0, 0.0, 0.1),
             (0.22, 0.32, 0.30, 1.0), scene=scene)
    # ⚠ 出生点离巡逻线要**大于视野**:不然一进走廊就被发现、直接开追,
    #   恐怖游戏的"慢慢摸过去"就没有了(horror 示例实测踩过)。
    player = make_box(PLAYER_CORRIDOR, (1.0, 1.0, 1.8), (-20.0, -3.0, 1.0),
                      (0.80, 0.82, 0.90, 1.0), scene=scene)
    enemy = make_box(ENEMY, (1.4, 1.4, 2.0), (-9.0, 0.0, 1.0),
                     (0.55, 0.10, 0.12, 1.0), scene=scene)
    for name, x in ((PATROL_A, -9.0), (PATROL_B, 0.0), (PATROL_C, 9.0)):
        make_box(name, (0.5, 0.5, 2.0), (x, 0.0, 1.0),
                 (0.25, 0.30, 0.28, 1.0), scene=scene)
    # 2 个"柜子":躲藏区(区域 + 看得见的柜体)
    make_box(CABINET_A, (2.0, 2.0, 3.2), (-4.0, 5.2, 1.6),
             (0.28, 0.22, 0.18, 1.0), scene=scene)
    make_box(CABINET_B, (2.0, 2.0, 3.2), (6.0, -5.2, 1.6),
             (0.28, 0.22, 0.18, 1.0), scene=scene)
    make_camera(CAM3_CORRIDOR, (0.0, -20.0, 5.5),
                (math.radians(72.0), 0.0, 0.0), scene)
    make_camera(CAM1_CORRIDOR, (0.0, -10.0, 1.6),
                (math.radians(90.0), 0.0, 0.0), scene)
    scene.camera = bpy.data.objects[CAM3_CORRIDOR]
    make_light("走廊壁灯", "POINT", (0.0, 0.0, 4.6), 70.0, (0.80, 0.84, 1.0),
               scene, cutoff=18.0)
    log(f"{CORRIDOR}:地面 + 两侧墙 + 3 个门框 + 尽头门 + 玩家 + {ENEMY} + "
        f"3 个巡逻点 + {CABINET_A}/{CABINET_B} + 两台相机")
    return scene, player, enemy


def build_ending():
    """第三关 结局:一个小房间 + 一点灯光。"""
    scene = bpy.data.scenes.new(ENDING)
    make_box("结局地面", (18.0, 18.0, 0.4), (0.0, 0.0, -0.2),
             (0.12, 0.11, 0.15, 1.0), scene=scene)
    make_box("结局北墙", (18.0, 0.5, 5.0), (0.0, 9.0, 2.5),
             (0.16, 0.15, 0.20, 1.0), scene=scene)
    make_box("结局南墙", (18.0, 0.5, 5.0), (0.0, -9.0, 2.5),
             (0.16, 0.15, 0.20, 1.0), scene=scene)
    make_box("结局箱甲", (2.2, 2.2, 2.0), (-5.0, 3.0, 1.0),
             (0.26, 0.24, 0.22, 1.0), scene=scene)
    make_box("结局箱乙", (2.2, 2.2, 2.0), (5.0, 3.0, 1.0),
             (0.26, 0.24, 0.22, 1.0), scene=scene)
    player = make_box(PLAYER_ENDING, (1.0, 1.0, 1.8), (0.0, -6.0, 1.0),
                      (0.80, 0.82, 0.90, 1.0), scene=scene)
    make_camera(CAM3_ENDING, (0.0, -14.0, 5.5),
                (math.radians(72.0), 0.0, 0.0), scene)
    make_camera(CAM1_ENDING, (0.0, -7.0, 1.6),
                (math.radians(90.0), 0.0, 0.0), scene)
    scene.camera = bpy.data.objects[CAM3_ENDING]
    make_light("结局灯", "POINT", (0.0, 0.0, 4.2), 90.0, (0.70, 0.78, 1.0),
               scene, cutoff=14.0)
    log(f"{ENDING}:地面 + 两墙 + 2 个箱子 + 玩家 + 两台相机 + 结局灯")
    return scene, player


def write_physics(scene, player, enemy=None, torch=None, door=None, triggers=()):
    """角色体 / 静态体 / 触发区 / 会动的门 / 手电筒,按物体归位。

    ── 为什么按物体分(踩过的坑)────────────────────────────────────
    角色必须是 CharacterBody3D 且勾『脚本挂载目标』(不然生成的角色移动代码
    找不到宿主);触发区必须是 Area3D + 『作为触发器』;会被时间轴每帧挪位置
    的门板必须是 **AnimatableBody3D**(StaticBody 是"瞬移",站上去的人会被
    留在原地 —— demo_level 的电梯同一个道理)。
    """
    trigger_names = set(triggers)
    for obj in scene.objects:
        physics = getattr(obj, "gk_physics", None)
        if physics is None:
            continue
        try:
            physics.shape_size = (0.0, 0.0, 0.0)         # 全 0 = 自动贴合
        except Exception:                                 # noqa: BLE001
            pass
        if obj is player:
            physics.body_type = "CharacterBody3D"
            physics.dimension = "3"
            physics.script_target = True
        elif enemy is not None and obj is enemy:
            physics.body_type = "CharacterBody3D"
            physics.dimension = "3"
        elif door is not None and obj is door:
            physics.body_type = "AnimatableBody3D"
            physics.dimension = "3"
            physics.asset_name = DOOR_PANEL
        elif obj.name in trigger_names:
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
# 节点图小工具
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


# ==========================================================================
# 走廊的怪物 AI(『游戏AI』树)
# ==========================================================================
def build_ai_tree(player_name):
    """走廊怪物 AI:在 3 个巡逻点之间来回 —— 看见就追,丢失去查看,安静回岗。

    ── 起始状态怎么定(踩过的坑)──────────────────────────────────
    老规矩是"没有任何转场指向的状态 = 起点",可这里**三个状态都有人指**
    (追击会退回巡逻),猜不出来。所以显式放一个『AI_开始』把起点钉死在
    『巡逻』(不加它也能跑,但起点会变成"随便挑一个",读图的人会一头雾水)。
    巡逻被 2 条转场指着 + 『AI_开始』1 条 → 『入口数量』要开 3。
    """
    tree = bpy.data.node_groups.new(AI_TREE_NAME, "GKGameAiTree")
    # 没有用户引用它的节点树,存盘时会被直接丢掉 —— 必须 use_fake_user
    tree.use_fake_user = True

    patrol = _add(tree, "GKNodeAiState", "巡逻", -200.0, 0.0,
                  gk_state_name=PATROL_STATE, gk_behavior="PATROL",
                  gk_patrol_a=PATROL_A, gk_patrol_b=PATROL_B,
                  gk_patrol_c=PATROL_C, gk_speed=PATROL_SPEED,
                  gk_stop_distance=0.7, gk_facing="MOVE", gk_in_count=3)
    chase = _add(tree, "GKNodeAiState", "追击", 700.0, 0.0,
                 gk_state_name="追击", gk_behavior="CHASE",
                 gk_goal=player_name, gk_speed=CHASE_SPEED,
                 gk_stop_distance=1.6, gk_facing="GOAL")
    look = _add(tree, "GKNodeAiState", "去查看", 700.0, 360.0,
                gk_state_name="去查看", gk_behavior="INVESTIGATE",
                gk_speed=INVESTIGATE_SPEED, gk_stop_distance=1.0,
                gk_facing="MOVE")
    start = _add(tree, "GKNodeAiStart", "AI开始", -520.0, 0.0)
    see = _add(tree, "GKNodeAiTransition", "看见玩家", 300.0, 200.0,
               gk_cond_source="SEE", gk_goal=player_name, gk_sight=SIGHT)
    back = _add(tree, "GKNodeAiTransition", "丢失玩家", 300.0, -220.0,
                gk_cond_source="LOST", gk_goal=player_name, gk_sight=LOST_RANGE)
    calm = _add(tree, "GKNodeAiTransition", "安静下来", 980.0, 220.0,
                gk_cond_source="CALM", gk_ear=0.0, gk_memory=1.5)

    _link(tree, start, "开始", patrol, "进入")
    _link(tree, patrol, "转到", see, "上游")
    _link(tree, see, "转到", chase, "进入")
    _link(tree, chase, "转到", back, "上游")
    _link(tree, back, "转到", look, "进入")
    _link(tree, look, "转到", calm, "上游")
    # 巡逻已经有『AI_开始』指着的『进入』,转场退回走『进入 2』
    _link(tree, calm, "转到", patrol, "进入 2")
    log(f"AI 图『{tree.name}』:{PATROL_STATE} --看见({SIGHT:g})--> 追击 "
        f"--丢失({LOST_RANGE:g})--> 去查看 --安静--> {PATROL_STATE}(从 AI_开始 起)")
    return tree


# ==========================================================================
# 角色预设 + 第一/第三人称
# ==========================================================================
def _make_first_person(logic, cam_first):
    """把预设里那根"俯瞰杆子"改成**第一人称**(相机贴头,cam_first = 那台机位名)。

    预设的两根杆子:侧视那根 = 第三人称(臂长 5);俯瞰那根默认臂长 16、
    俯仰 80(从高处往下看)。这里把俯瞰那根**收成贴头**:
    臂长 0.25(几乎贴在枢轴上)、高度 1.55(眼睛)、俯仰 0(平视)、
    关掉穿墙检测(贴脸距离没必要射射线)。

    ⚠ 值直接写在**属性**上就生效 —— codegen 读『臂长』『俯仰』时会先问
      节点属性(`node.input_value` 的 `gk_input_props` 映射),引脚没接线就
      用属性值(见 codegen/camera.py 的 `input_number`)。
    """
    changed = 0
    for node in logic.nodes:
        if getattr(node, "bl_idname", "") != "GKNodeCameraSpringArm":
            continue
        if str(getattr(node, "gk_camera", "")) != cam_first:
            continue
        node.gk_arm = FIRST_ARM
        node.gk_height = EYE_HEIGHT
        node.gk_pitch = 0.0
        node.gk_collision = False
        node.gk_lag = 12.0
        changed += 1
    # 顺手把预设里那两个"俯瞰/侧视"的名字换成第一/三人称,读图不用换算
    for node in logic.nodes:
        if node.name == "切到俯瞰":
            node.name = "切到第一人称"
        elif node.name == "切回侧视":
            node.name = "切到第三人称"
    log(f"第一/三人称:C→{cam_first}(第一人称) ;改写了 {changed} 根杆子")


def build_character(logic_scene, player, logic_name, class_name, cam_third,
                    cam_first):
    """用『角色预设』生成"跑得起来"的角色图(WASD / 空格 / 鼠标 / 滚轮 / C·V)。

    预设自带两台相机的 C/V 切换 —— 我们把 cam_third 取作第三人称、
    cam_first 取作第一人称(名字一填,预设就按这两个名字找相机、切相机;
    名字必须**每关唯一**,见文件头那段说明)。
    返回 (逻辑树, 『每当_准备』节点)。
    """
    from BlenderGameKit import presets
    logic, _anim, notes = presets.create_character_preset(
        logic_scene, character=player,
        logic_name=logic_name, class_name=class_name,
        with_animation=False, with_ports=False,
        camera_side=cam_third, camera_top=cam_first,
        move_speed=MOVE_SPEED,
        arm_length=THIRD_ARM, arm_height=THIRD_HEIGHT, aim_pitch=THIRD_PITCH,
    )
    for note in notes:
        log(f"预设({logic_name}): {note}")
    _make_first_person(logic, cam_first)
    ready = None
    for node in logic.nodes:
        if node.bl_idname == "GKNodeOnReady":
            ready = node
    if ready is None:
        raise RuntimeError(f"{logic_name} 里没有『准备』节点")
    return logic, ready


# ==========================================================================
# HUD 骨架 —— 一处定义、三关复用
# ==========================================================================
def build_hud(logic, chapter, goal, keys=False, battery=False, hint=False):
    """给一关建 HUD(目标 + 可选 钥匙数 / 电量 / 提示 + 章节卡)。

    **三关只传不同的『章节名』和『目标文字』**,别的都在这儿建 —— 这是
    "一处定义、三关复用"的硬性要求(别三关各抄一遍)。

    返回 (头节点, {控件名}) —— 头节点由调用方**最后**接到『每当_准备』:

    ── 两条顺序讲究(都踩过)────────────────────────────────────────
    ① **常驻 HUD 排在前、章节卡排在后**:章节卡"3 秒后淡出"生成的是
       `await get_tree().create_timer(3.0)`,会**挂起 `_ready()` 剩下的语句**。
       要是章节卡排在最前,目标 / 电量这些就得等 3 秒才建出来 —— 开局那几秒
       连 HUD 都没有。所以常驻的排前面(第一帧就在),await 挪到链尾。
    ② **整串的 Y 排到最后(1500+)、头节点最后接 ready**:同一张图里接到
       『每当_准备』的其它语句(存档 / 开手电 / 启动 AI / 开场对话)是按**节点
       位置的 Y** 排序一路铺下去的(见 codegen/registry 的 `_next_nodes`)。
       HUD 的 await 排在最后,才不会把"开局就该生效"的几句一起拖三秒;
       而开场对话的 Y(1900)又在 HUD 之后 —— 正好"先亮章节卡、再弹对话"。

    返回的控件名给后面的『界面_设置』按名字改字(控件名 = 建它的节点名)。
    """
    goal_text = _add(logic, "GKNodeUiText", "目标文本", -300.0, 1500.0,
                     gk_text=goal, gk_x=18.0, gk_y=18.0, gk_size=20,
                     gk_color=(0.90, 0.92, 1.0, 1.0))
    head = goal_text
    tail = goal_text
    handles = {"goal": goal_text.name, "keys": None, "battery": None,
               "hint": None}
    if keys:
        keys_text = _add(logic, "GKNodeUiText", "钥匙文本", -60.0, 1500.0,
                         gk_text="钥匙:0", gk_x=18.0, gk_y=48.0, gk_size=20,
                         gk_color=(0.95, 0.85, 0.40, 1.0))
        _link(logic, tail, "完成", keys_text, "执行")
        tail = keys_text
        handles["keys"] = keys_text.name
    if battery:
        battery_text = _add(logic, "GKNodeUiText", "电量文本", 180.0, 1500.0,
                            gk_text="手电电量:100", gk_x=18.0, gk_y=78.0,
                            gk_size=18, gk_color=(0.60, 0.85, 1.0, 1.0))
        _link(logic, tail, "完成", battery_text, "执行")
        tail = battery_text
        handles["battery"] = battery_text.name
    if hint:
        hint_text = _add(logic, "GKNodeUiText", "提示文本", 420.0, 1500.0,
                         gk_text="", gk_align="CENTER", gk_size=24,
                         gk_color=(1.0, 0.55, 0.55, 1.0))
        _link(logic, tail, "完成", hint_text, "执行")
        tail = hint_text
        handles["hint"] = hint_text.name
    # 章节卡排链尾:亮 3 秒 → 淡出(生成的 await 也就落在最后一句)
    card = _add(logic, "GKNodeUiText", "章节卡", 660.0, 1500.0,
                gk_text=chapter, gk_align="CENTER", gk_size=40,
                gk_color=(0.92, 0.92, 1.0, 1.0))
    hold = _add(logic, "GKNodeWait", "章节停留", 900.0, 1500.0)
    hold.inputs["秒"].default_value = 3.0
    fade = _add(logic, "GKNodeUiSet", "章节淡出", 1140.0, 1500.0,
                gk_widget=card.name, gk_set_alpha=True, gk_alpha=0.0)
    _link(logic, tail, "完成", card, "执行")
    _link(logic, card, "完成", hold, "执行")
    _link(logic, hold, "完成", fade, "执行")
    log(f"HUD『{chapter}』:{goal}"
        f"{' + 钥匙数' if keys else ''}{' + 电量' if battery else ''}"
        f"{' + 提示' if hint else ''} + 章节卡")
    return head, handles


# ==========================================================================
# 第一关 大堂 的逻辑
# ==========================================================================
def build_lobby_logic(scene, player, player_name, cam_third, cam_first):
    """大堂:章节卡/HUD + 手电(开关/电量) + 拾取钥匙 + 大门(升起/切场景)+ 存档。"""
    logic, ready = build_character(scene, player, "大堂逻辑", "StoryLobbyPlayer",
                                   cam_third, cam_first)
    # HUD 先建(拿到控件名),但**头节点最后才接 ready**(见 build_hud 的说明)
    hud_head, hud = build_hud(logic, "第一章:门厅",
                              "目标:找到钥匙,打开大门",
                              keys=True, battery=True, hint=True)

    # 开局:手电关着 + **电量满格** + 自动存档一次(槽位 story)
    # ⚠ 电量必须**显式**初始化成满格:变量默认是 0,而"灯亮"的条件里带着
    #   『还有电』(灯亮 = 手电开 且 电量 > 0)—— 不初始化的话开局就没电,
    #   手电永远点不亮(真 Godot 探针实测抓到的)。
    battery_full = _add(logic, "GKNodeSetVar", "电量开局满", -900.0, 600.0,
                        gk_var_name=BATTERY, gk_value=BATTERY_MAX)
    _link(logic, ready, "执行", battery_full, "执行")
    # ⚠ 『手电开』要**用布尔字面量**初始化:它是真假变量,可『强制关』那句
    #   写的是数字 0.0 —— 谁先出现谁定类型,数字先出现的话整段会被当成
    #   float,后面 `手电开 = (not 手电开)` 赋布尔进去,Godot 解析期直接报
    #   类型不符、整个脚本加载失败(所以这里显式写一个 false 把类型钉死)。
    torch_off_flag = _add(logic, "GKNodeConstBool", "手电关常量", -1150.0, 660.0,
                          gk_value=False)
    off_flag = _add(logic, "GKNodeSetVar", "手电开局关标志", -900.0, 660.0,
                    gk_var_name=TORCH_ON)
    _link(logic, torch_off_flag, "值", off_flag, "值")
    _link(logic, ready, "执行", off_flag, "执行")
    torch_off = _add(logic, "GKNodeSetLight", "手电开局关", -900.0, 760.0,
                     gk_light=TORCH, gk_on=False)
    _link(logic, ready, "执行", torch_off, "执行")
    save = _add(logic, "GKNodeSaveWrite", "大堂存档", -620.0, 760.0,
                gk_slot="story", gk_save_verify=True)
    _link(logic, ready, "执行", save, "执行")

    # ---- 手电:F 开 / 关 + 每秒掉电 + 没电自动关 ----
    # F 键:读『手电开』取反写回(真正的开关动作在下面的每帧链里统一应用)
    # ⚠ 这条分支必须挂在**物理帧**上:`按F了吗` 判断的是"这一帧刚按下没"
    #   (`Input.is_action_just_pressed`),挂在『每当_准备』上就只在开局判定
    #   一次,运行中按 F 根本没反应(真 Godot 探针实测抓到的)。
    key_f = _add(logic, "GKNodeKey", "按F", -900.0, 460.0,
                 gk_key="F", gk_action="key_f", gk_trigger="JUST")
    f_branch = _add(logic, "GKNodeBranch", "按F了吗", -660.0, 460.0)
    read_on = _add(logic, "GKNodeGetVar", "读手电开", -420.0, 560.0,
                   gk_var_name=TORCH_ON)
    not_on = _add(logic, "GKNodeLogic", "取反", -180.0, 560.0, gk_op="NOT")
    write_on = _add(logic, "GKNodeSetVar", "写手电开", 60.0, 460.0,
                    gk_var_name=TORCH_ON)
    _link(logic, key_f, "结果", f_branch, "条件")
    _link(logic, f_branch, "是", write_on, "执行")
    _link(logic, read_on, "值", not_on, "A")
    _link(logic, not_on, "结果", write_on, "值")

    # 每帧:灯亮 = 手电开 且 还有电;亮着才掉电;掉到 0 就强制关 + 提示
    phys = _add(logic, "GKNodeOnPhysicsProcess", "物理帧(手电)", -900.0, 100.0)
    _link(logic, phys, "执行", f_branch, "执行")
    read_on2 = _add(logic, "GKNodeGetVar", "读手电开2", -900.0, -160.0,
                    gk_var_name=TORCH_ON)
    read_bat = _add(logic, "GKNodeGetVar", "读电量", -900.0, -320.0,
                    gk_var_name=BATTERY)
    has_power = _add(logic, "GKNodeCompare", "还有电吗", -660.0, -320.0,
                     gk_op="GT", gk_b="0")
    lit = _add(logic, "GKNodeLogic", "灯亮吗", -420.0, -160.0, gk_op="AND")
    apply_light = _add(logic, "GKNodeSetLight", "应用手电", -180.0, 100.0,
                       gk_light=TORCH)
    lit_branch = _add(logic, "GKNodeBranch", "亮着吗", 60.0, 100.0)
    drain = _add(logic, "GKNodeMath", "每秒耗电", 300.0, -160.0,
                 gk_op="MUL", gk_a=-BATTERY_DRAIN)
    spend = _add(logic, "GKNodeAddVar", "扣电", 300.0, 100.0,
                 gk_var_name=BATTERY, gk_clamp=True, gk_min=0.0,
                 gk_max=BATTERY_MAX)
    empty = _add(logic, "GKNodeCompare", "没电了吗", 540.0, -160.0,
                 gk_op="LE", gk_b="0")
    empty_branch = _add(logic, "GKNodeBranch", "没电分支", 780.0, 100.0)
    force_off = _add(logic, "GKNodeSetVar", "强制关", 1020.0, -100.0,
                     gk_var_name=TORCH_ON, gk_value=0.0)
    dead_hint = _add(logic, "GKNodeUiSet", "没电提示", 1260.0, -100.0,
                     gk_widget=hud["hint"], gk_set_text=True,
                     gk_text="手电没电了")
    # 电量显示(每帧刷新)
    bat_round = _add(logic, "GKNodeMath", "电量取整", 300.0, -420.0,
                     gk_op="ROUND")
    bat_join = _add(logic, "GKNodeTextJoin", "电量文字", 540.0, -420.0,
                    gk_a="手电电量:")
    bat_ui = _add(logic, "GKNodeUiSet", "刷新电量", 780.0, -260.0,
                  gk_widget=hud["battery"], gk_set_text=True)

    _link(logic, read_on2, "值", lit, "A")
    _link(logic, read_bat, "值", has_power, "A")
    _link(logic, has_power, "结果", lit, "B")
    _link(logic, phys, "执行", apply_light, "执行")
    _link(logic, lit, "结果", apply_light, "开启")
    _link(logic, apply_light, "完成", lit_branch, "执行")
    _link(logic, lit, "结果", lit_branch, "条件")
    _link(logic, phys, "帧间隔", drain, "B")
    _link(logic, drain, "结果", spend, "增减")
    _link(logic, lit_branch, "是", spend, "执行")
    _link(logic, spend, "完成", empty_branch, "执行")
    _link(logic, spend, "新值", empty, "A")
    _link(logic, empty, "结果", empty_branch, "条件")
    _link(logic, empty_branch, "是", force_off, "执行")
    _link(logic, force_off, "完成", dead_hint, "执行")
    # 电量文字:从"物理帧"单独拉一条,免得被上面的分支挡住
    _link(logic, phys, "执行", bat_ui, "执行")
    _link(logic, read_bat, "值", bat_round, "A")
    _link(logic, bat_round, "结果", bat_join, "乙")
    _link(logic, bat_join, "结果", bat_ui, "文字")

    # ---- 拾取钥匙:钥匙数 +1 → 刷新 UI → 音效 → 钥匙消失 → 改目标文字 ----
    pick = _add(logic, "GKNodeOnAreaEntered", "碰到钥匙", -900.0, -700.0,
                gk_area=KEY, gk_filter=player_name)
    add_key = _add(logic, "GKNodeAddVar", "钥匙加一", -660.0, -700.0,
                   gk_var_name=KEY_COUNT, gk_amount=1.0, gk_clamp=True,
                   gk_min=0.0, gk_max=1.0)
    keys_ui = _add(logic, "GKNodeUiSet", "刷新钥匙", -420.0, -700.0,
                   gk_widget=hud["keys"], gk_set_text=True, gk_text="钥匙:1")
    pick_sfx = _add(logic, "GKNodePlaySound", "拾取音效", -180.0, -700.0,
                    gk_path=PICKUP_FILE, gk_volume=-4.0, gk_2d=False)
    hide_key = _add(logic, "GKNodeSetVisible", "钥匙消失", 60.0, -700.0,
                    gk_target=KEY, gk_visible=False)
    goal_ui = _add(logic, "GKNodeUiSet", "改目标", 300.0, -700.0,
                   gk_widget=hud["goal"], gk_set_text=True,
                   gk_text="目标:打开大门")
    _link(logic, pick, "执行", add_key, "执行")
    _link(logic, add_key, "完成", keys_ui, "执行")
    _link(logic, keys_ui, "完成", pick_sfx, "执行")
    _link(logic, pick_sfx, "完成", hide_key, "执行")
    _link(logic, hide_key, "完成", goal_ui, "执行")

    # ---- 大门:钥匙够 1 把 → 开门音效 → 门板升起 → 切到走廊;不够 → 提示 ----
    door_in = _add(logic, "GKNodeOnAreaEntered", "走到大门", -900.0, -1100.0,
                   gk_area=DOOR_AREA, gk_filter=player_name)
    read_key = _add(logic, "GKNodeGetVar", "读钥匙数", -660.0, -1020.0,
                    gk_var_name=KEY_COUNT)
    enough = _add(logic, "GKNodeCompare", "钥匙够吗", -420.0, -1020.0,
                  gk_op="GE", gk_b="1")
    door_branch = _add(logic, "GKNodeBranch", "门开吗", -180.0, -1100.0)
    locked = _add(logic, "GKNodeUiSet", "锁着提示", 60.0, -920.0,
                  gk_widget=hud["hint"], gk_set_text=True,
                  gk_text="门锁着(还缺钥匙)")
    open_sfx = _add(logic, "GKNodePlaySound", "开门音效", 60.0, -1260.0,
                    gk_path=DOOR_FILE, gk_volume=-3.0, gk_2d=False)
    lift = _add(logic, "GKNodeTimeline", "门板升起", 300.0, -1260.0,
                gk_time=0.9, gk_mode="ONCE", gk_track_count=1,
                gk_track1_from=DOOR_Z_DOWN, gk_track1_to=DOOR_Z_UP)
    door_pos = _add(logic, "GKNodeGetPosition", "取门位置", 300.0, -1460.0,
                    gk_target=DOOR_PANEL, gk_global=True)
    door_push = _add(logic, "GKNodeSetPosition", "推门", 540.0, -1260.0,
                     gk_target=DOOR_PANEL)
    change = _add(logic, "GKNodeChangeScene", "去走廊", 800.0, -1260.0,
                  gk_scene_path=CORRIDOR_RES)
    _link(logic, read_key, "值", enough, "A")
    _link(logic, enough, "结果", door_branch, "条件")
    _link(logic, door_in, "执行", door_branch, "执行")
    _link(logic, door_branch, "否", locked, "执行")
    _link(logic, door_branch, "是", open_sfx, "执行")
    _link(logic, open_sfx, "完成", lift, "执行")
    _link(logic, lift, "更新", door_push, "执行")
    _link(logic, lift, "轨道1值", door_push, "Y")
    _link(logic, door_pos, "X", door_push, "X")
    _link(logic, door_pos, "Z", door_push, "Z")
    _link(logic, lift, "已结束", change, "执行")

    # HUD 头节点最后接 ready(它的 await 不能挡前面几句)
    _link(logic, ready, "执行", hud_head, "执行")
    log(f"{LOBBY}逻辑:章节/HUD + 手电(F/电量) + 钥匙拾取 + 大门升起 -> "
        f"切 {CORRIDOR_RES} + 存档 story")
    return logic


# ==========================================================================
# 第二关 走廊 的逻辑
# ==========================================================================
def build_corridor_logic(scene, player, enemy, ai_tree, cabinets, player_name,
                         cam_third, cam_first):
    """走廊:章节目录 + 开场对话/选择(两链汇合)+ 巡逻 AI + 惊吓 + 躲藏 + 尽头门。"""
    logic, ready = build_character(scene, player, "走廊逻辑",
                                   "StoryCorridorPlayer", cam_third, cam_first)
    hud_head, hud = build_hud(logic, "第二章:走廊",
                              "目标:穿过走廊,别被它看见", hint=True)

    # 开局:启动 AI + 自动存档
    enable = _add(logic, "GKNodeAiEnable", "启动怪物AI", -900.0, 700.0,
                  gk_tree_name=ai_tree.name, gk_target=ENEMY,
                  gk_state_var=AI_STATE_VAR, gk_debug_print=True)
    _link(logic, ready, "执行", enable, "执行")
    save = _add(logic, "GKNodeSaveWrite", "走廊存档", -620.0, 700.0,
                gk_slot="story", gk_save_verify=True)
    _link(logic, ready, "执行", save, "执行")

    # ---- 开场:旁白 + 主角两句 + 剧情选择 ----
    # ⚠ 对话框是**阻塞**的(await 等玩家点),所以这一串的 Y 排在 HUD 之后
    #   (1900),它是最后一段 —— 让章节卡先播 3 秒、再弹对话。
    d1 = _add(logic, "GKNodeUiDialog", "旁白", -300.0, 1900.0,
              gk_speaker="", gk_text="走廊很长,灯全灭了。", gk_speed=24.0)
    d2 = _add(logic, "GKNodeUiDialog", "主角1", -60.0, 1900.0,
              gk_speaker="我", gk_text="必须穿过去。", gk_speed=24.0)
    d3 = _add(logic, "GKNodeUiDialog", "主角2", 180.0, 1900.0,
              gk_speaker="我", gk_text="……那声音越来越近了。", gk_speed=24.0)
    choice = _add(logic, "GKNodeUiChoice", "走廊选择", 420.0, 1900.0,
                  gk_question="怎么办?", gk_option_count=2,
                  gk_options="往前跑|先躲一下", gk_speed=24.0)
    _link(logic, ready, "执行", d1, "执行")
    _link(logic, d1, "完成", d2, "执行")
    _link(logic, d2, "完成", d3, "执行")
    _link(logic, d3, "完成", choice, "执行")

    # ---- 两条选择链 + 汇合 ----
    # ⚠ Blender **一个执行输入引脚只允许一条线**,所以两个选项没法"直接插进
    #   同一个节点"。这里的做法是:两条链各做自己的事(记下选了哪个),然后
    #   **都调用同一个函数『走廊合流』** —— 那份共享后续只写一遍,第二条链
    #   也不会断(硬性要求)。
    merge_spec = logic.gk_funcs.add()
    merge_spec.func_name = "走廊合流"
    merge_spec.return_type = "void"
    merge_spec.params = ""
    merge_spec.comment = "两条选择链的汇合点(共享后续)"
    merge_head = _add(logic, "GKNodeFunctionDefine", "合流函数", -60.0, 1300.0,
                      gk_func_name="走廊合流")
    shared = _add(logic, "GKNodeUiSubtitle", "合流字幕", 180.0, 1300.0,
                  gk_text="穿过走廊 —— 别被它看见。", gk_seconds=3.0)
    _link(logic, merge_head, "执行", shared, "执行")

    for number, pin, label in ((1, "选项1", "往前跑"), (2, "选项2", "先躲一下")):
        row = 1600.0 - number * 220.0
        value = _add(logic, "GKNodeConstFloat", f"选择值{number}", 660.0, row,
                     gk_value=float(number))
        store = _add(logic, "GKNodeSetVar", f"记选择{number}", 900.0, row,
                     gk_var_name=CHOICE_VAR)
        say = _add(logic, "GKNodePrint", f"打印选择{number}", 1140.0, row,
                   gk_text=f"你选择:{label}")
        call = _add(logic, "GKNodeFunctionCall", f"调用合流{number}",
                    1380.0, row, gk_func_name="走廊合流")
        _link(logic, value, "值", store, "值")
        _link(logic, choice, pin, store, "执行")
        _link(logic, store, "完成", say, "执行")
        _link(logic, say, "完成", call, "执行")

    # ---- 心跳 + 紧张度(每物理帧)----
    beat = _add(logic, "GKNodeOnPhysicsProcess", "物理帧(心跳)", -900.0,
                -1600.0)
    tension = _add(logic, "GKNodeTension", "紧张度", -660.0, -1600.0,
                   gk_threat=ENEMY, gk_near=TENSION_NEAR, gk_far=TENSION_FAR)
    heart = _add(logic, "GKNodeHeartbeat", "心跳", -180.0, -1600.0,
                 gk_path=HEART_FILE, gk_slow_interval=1.1,
                 gk_fast_interval=0.30, gk_quiet_db=-19.0, gk_loud_db=-5.0,
                 gk_quiet=0.15, gk_smooth=1.5)
    _link(logic, beat, "执行", heart, "执行")
    _link(logic, tension, "紧张度", heart, "紧张度")

    # ---- 近身惊吓三连(每物理帧;贴脸 + 冷却,避免每帧都闪)----
    near = _add(logic, "GKNodeOnPhysicsProcess", "物理帧(近身)", -900.0,
                -1950.0)
    dist = _add(logic, "GKNodeDistance", "到怪物距离", -660.0, -1950.0,
                gk_b=ENEMY)
    close = _add(logic, "GKNodeCompare", "贴脸了吗", -420.0, -1950.0,
                 gk_op="LT", gk_b=str(ATTACK_RANGE))
    near_branch = _add(logic, "GKNodeBranch", "贴脸分支", -180.0, -1950.0)
    cool = _add(logic, "GKNodeCooldown", "惊吓冷却", 60.0, -1950.0,
                gk_seconds=1.6)
    scare = _add(logic, "GKNodeScare", "惊吓三连", 300.0, -1950.0,
                 gk_strength=20.0, gk_duration=0.5,
                 gk_color=(0.85, 0.08, 0.08, 1.0), gk_alpha=0.55,
                 gk_flash_time=0.35, gk_path=STING_FILE, gk_volume=-3.0)
    _link(logic, dist, "距离", close, "A")
    _link(logic, close, "结果", near_branch, "条件")
    _link(logic, near, "执行", near_branch, "执行")
    _link(logic, near_branch, "是", cool, "执行")
    _link(logic, cool, "可以", scare, "执行")

    # ---- 躲藏(2 个柜子)----
    # ── 这是**近似**做法,插件没有"潜行 / 玩家隐藏"设施 ────────────────
    # 钻进柜子 → 把 AI 的**状态变量**设成一个**不存在的状态名**(『躲藏着』):
    # 生成的 `if ai_state == "巡逻" / "追击" / "去查看"` 一条都不成立,行为
    # 和转场全不跑 → 等于把这台 AI **冻住**(这就借用"AI 停用一段时间"的
    # 效果);同时暗角加深、HUD 提示"屏住呼吸…"。
    # 离开柜子 → 把状态写回『巡逻』,AI 恢复。
    # ⚠ 它不是真潜行:冻结期间玩家若站在怪物正前方,一出柜子它照样会看见 ——
    #   插件的 AI 没有"看不见躲藏的玩家"这个概念,只能做到这个程度。
    for index, cab in enumerate(cabinets):
        row = -2400.0 - index * 500.0
        entered = _add(logic, "GKNodeOnAreaEntered", f"躲进{cab}", -900.0, row,
                       gk_area=cab, gk_filter=player_name)
        hide_state = _add(logic, "GKNodeConstText", f"躲藏状态{index + 1}",
                          -660.0, row - 120.0, gk_value=HIDE_STATE)
        freeze = _add(logic, "GKNodeSetVar", f"冻结AI{index + 1}", -660.0, row,
                      gk_var_name=AI_STATE_VAR)
        vig_in = _add(logic, "GKNodeScreenVignette", f"暗角加深{index + 1}",
                      -420.0, row, gk_amount=0.82, gk_radius=0.42,
                      gk_edge=0.5, gk_fade=0.4)
        hint_in = _add(logic, "GKNodeUiSet", f"屏息提示{index + 1}",
                       -180.0, row, gk_widget=hud["hint"], gk_set_text=True,
                       gk_text="屏住呼吸…")
        _link(logic, hide_state, "值", freeze, "值")
        _link(logic, entered, "执行", freeze, "执行")
        _link(logic, freeze, "完成", vig_in, "执行")
        _link(logic, vig_in, "完成", hint_in, "执行")

        left = _add(logic, "GKNodeOnAreaExited", f"离开{cab}", 300.0, row,
                    gk_area=cab, gk_filter=player_name)
        back_state = _add(logic, "GKNodeConstText", f"巡逻状态{index + 1}",
                          540.0, row - 120.0, gk_value=PATROL_STATE)
        resume = _add(logic, "GKNodeSetVar", f"恢复AI{index + 1}", 540.0, row,
                      gk_var_name=AI_STATE_VAR)
        vig_out = _add(logic, "GKNodeScreenVignette", f"暗角复原{index + 1}",
                       780.0, row, gk_amount=0.45, gk_radius=0.60,
                       gk_edge=0.40, gk_fade=0.4)
        hint_out = _add(logic, "GKNodeUiSet", f"清提示{index + 1}", 1020.0, row,
                        gk_widget=hud["hint"], gk_set_text=True, gk_text="")
        _link(logic, back_state, "值", resume, "值")
        _link(logic, left, "执行", resume, "执行")
        _link(logic, resume, "完成", vig_out, "执行")
        _link(logic, vig_out, "完成", hint_out, "执行")

    # ---- 走到尽头 → 切到结局 ----
    end = _add(logic, "GKNodeOnAreaEntered", "走到尽头", -900.0, -3600.0,
               gk_area=CORRIDOR_END, gk_filter=player_name)
    change = _add(logic, "GKNodeChangeScene", "去结局", -660.0, -3600.0,
                  gk_scene_path=ENDING_RES)
    _link(logic, end, "执行", change, "执行")

    _link(logic, ready, "执行", hud_head, "执行")
    log(f"{CORRIDOR}逻辑:章节/HUD + AI(3 巡逻点) + 心跳/惊吓 + 躲藏 x2 + "
        f"开场对话/选择(合流函数)-> 切 {ENDING_RES}")
    return logic


# ==========================================================================
# 第三关 结局 的逻辑
# ==========================================================================
def build_ending_logic(scene, player, cam_third, cam_first):
    """结局:章节/HUD + 旁白 + 结局选择(两个结局各自存档 / 大字 / BGM 淡出)-> 回大堂。"""
    logic, ready = build_character(scene, player, "结局逻辑", "StoryEndingPlayer",
                                   cam_third, cam_first)
    hud_head, _hud = build_hud(logic, "终章:抉择", "目标:做出你的选择")

    vig = _add(logic, "GKNodeScreenVignette", "结局暗角", -900.0, 800.0,
               gk_amount=0.60, gk_radius=0.50, gk_edge=0.35)
    _link(logic, ready, "执行", vig, "执行")

    d1 = _add(logic, "GKNodeUiDialog", "旁白", -300.0, 1900.0,
              gk_speaker="", gk_text="出口就在前面。", gk_speed=24.0)
    d2 = _add(logic, "GKNodeUiDialog", "主角", -60.0, 1900.0,
              gk_speaker="我", gk_text="……她还在后面。", gk_speed=24.0)
    choice = _add(logic, "GKNodeUiChoice", "结局选择", 180.0, 1900.0,
                  gk_question="你要怎么做?", gk_option_count=2,
                  gk_options="回头救她|独自逃出去", gk_speed=24.0)
    _link(logic, ready, "执行", d1, "执行")
    _link(logic, d1, "完成", d2, "执行")
    _link(logic, d2, "完成", choice, "执行")

    # 两个结局:各走各的链(存档 story_end + 记「结局」键 → 大字 → 总线淡出
    # → 停留 → 回大堂,形成闭环)。
    endings = ((1, "选项1", "回头救她"), (2, "选项2", "独自逃出去"))
    for number, pin, label in endings:
        row = 1600.0 - number * 320.0
        value = _add(logic, "GKNodeConstFloat", f"结局值{number}", 420.0, row,
                     gk_value=float(number))
        set_end = _add(logic, "GKNodeSetVar", f"记结局{number}", 660.0, row,
                       gk_var_name="结局")
        write = _add(logic, "GKNodeSaveSet", f"写结局{number}", 900.0, row,
                     gk_key="结局")
        save = _add(logic, "GKNodeSaveWrite", f"保存结局{number}", 1140.0, row,
                    gk_slot="story_end", gk_save_verify=True)
        text = _add(logic, "GKNodeUiText", f"结局大字{number}", 1380.0, row,
                    gk_text=f"结局:{label}", gk_align="CENTER", gk_size=34,
                    gk_color=(0.95, 0.92, 0.80, 1.0))
        fade = _add(logic, "GKNodeBusVolume", f"BGM淡出{number}", 1620.0, row,
                    gk_bus="Master", gk_db=-60.0, gk_fade=3.0)
        wait = _add(logic, "GKNodeWait", f"停留{number}", 1860.0, row)
        wait.inputs["秒"].default_value = 4.0
        back = _add(logic, "GKNodeChangeScene", f"回大堂{number}", 2100.0, row,
                    gk_scene_path=LOBBY_RES)
        _link(logic, value, "值", set_end, "值")
        _link(logic, value, "值", write, "值")
        _link(logic, choice, pin, set_end, "执行")
        _link(logic, set_end, "完成", write, "执行")
        _link(logic, write, "完成", save, "执行")
        _link(logic, save, "完成", text, "执行")
        _link(logic, text, "完成", fade, "执行")
        _link(logic, fade, "完成", wait, "执行")
        _link(logic, wait, "完成", back, "执行")

    _link(logic, ready, "执行", hud_head, "执行")
    log(f"{ENDING}逻辑:章节/HUD + 旁白/结局选择(2 结局:存档 story_end + "
        f"「结局」键 + 大字 + BGM 淡出)-> 回 {LOBBY_RES}")
    return logic


# ==========================================================================
# 音频(运行时生成小 wav,示例自包含)
# ==========================================================================
def _write_wav(path, samples, rate=22050):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = b"".join(struct.pack("<h", max(-32767, min(32767, int(s * 32767))))
                          for s in samples)
        handle.writeframes(frames)


def write_bgm_source():
    """生成三首 BGM(开局 BGM 走侧栏设置,导出时会复制进项目的 audio/)。

    都是低沉的循环小段:大堂 / 走廊更低更闷,结局偏凄清 —— 几十 KB,
    示例自包含,不依赖任何外部文件(照 make_horror_demo 的做法)。
    """
    rate = 22050

    def drone(freqs, seconds, amp):
        total = int(rate * seconds)
        out = []
        for i in range(total):
            t = i / rate
            fade = min(1.0, t / 0.4, (seconds - t) / 0.4)
            tone = sum(math.sin(2.0 * math.pi * f * t) for f in freqs)
            out.append(amp * max(0.0, fade) * tone / len(freqs))
        return out

    _write_wav(BGM_LOBBY, drone((55.0, 82.5), 4.0, 0.16), rate)
    _write_wav(BGM_CORRIDOR, drone((45.0, 66.0, 90.0), 4.0, 0.18), rate)
    _write_wav(BGM_ENDING, drone((60.0, 90.0), 4.0, 0.14), rate)
    log(f"BGM 源:{BGM_DIR}(大堂/走廊/结局)")


def write_audio(directory):
    """生成 Node 引用的音效(心跳 / 惊吓 / 拾取 / 开门)到项目的 audio/。

    这些节点的『音频路径』是 `res://audio/...` 字面量 —— 直接写进工程的
    audio/ 即可(和『资产_声音卡』那条复制管线不同,节点路径不做复制)。
    """
    audio_dir = os.path.join(directory, "audio")
    rate = 22050

    def thump(duration, freq, decay, amp=0.55):
        total = int(rate * duration)
        out = []
        for i in range(total):
            t = i / rate
            out.append(amp * math.exp(-decay * t) * math.sin(2.0 * math.pi * freq * t))
        return out

    _write_wav(os.path.join(audio_dir, "heartbeat.wav"),
               thump(0.16, 55.0, 26.0), rate)
    _write_wav(os.path.join(audio_dir, "sting.wav"),
               thump(0.40, 320.0, 9.0), rate)
    # 拾取:两个上扬的短音
    pickup = thump(0.10, 880.0, 30.0, 0.4) + thump(0.14, 1320.0, 24.0, 0.4)
    _write_wav(os.path.join(audio_dir, "pickup.wav"), pickup, rate)
    # 开门:低频"咯——"
    _write_wav(os.path.join(audio_dir, "door.wav"),
               thump(0.8, 120.0, 3.0, 0.5), rate)
    log(f"音效:heartbeat / sting / pickup / door -> {audio_dir}")


def save_blend(path):
    if not bpy.app.background:
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return
    blocks = set(bpy.data.objects) | set(bpy.data.meshes) | \
        set(bpy.data.materials) | set(bpy.data.node_groups) | \
        set(bpy.data.cameras) | set(bpy.data.lights) | set(bpy.data.curves)
    scenes = list(bpy.data.scenes)
    bpy.data.libraries.write(path, blocks | set(scenes), fake_user=True)


def _set_scene_bgm(scene, path, volume):
    """把这一关的开局 BGM 写进场景设置(每关一首,切场景自动跟着切)。"""
    settings = getattr(scene, "gk_settings", None)
    if settings is None:
        return
    for attr, value in (("scene_bgm", path), ("scene_bgm_volume", volume),
                        ("scene_bgm_loop", True), ("scene_bgm_bus", "")):
        if hasattr(settings, attr):
            try:
                setattr(settings, attr, value)
            except Exception:                             # noqa: BLE001
                pass


def main():
    from BlenderGameKit import asset_export, project

    os.makedirs(OUT_DIR, exist_ok=True)
    # BGM 源文件要**先**生成 —— 侧栏 scene_bgm 指向磁盘文件,导出时才复制
    write_bgm_source()

    # ---- 第一关 大堂(用已有的那个场景)----
    lobby = clear_all()
    lobby_player = build_lobby(lobby)
    lobby_door = bpy.data.objects[DOOR_PANEL]
    lobby_torch = bpy.data.objects[TORCH]
    write_physics(lobby, lobby_player, torch=lobby_torch, door=lobby_door,
                  triggers={KEY, DOOR_AREA})
    build_lobby_logic(lobby, lobby_player, PLAYER_LOBBY, CAM3_LOBBY, CAM1_LOBBY)
    _apply_settings(lobby, (("dimension", "3"), ("toon_follow_light", True))
                    + ENV_LOBBY)
    _set_scene_bgm(lobby, BGM_LOBBY, -14.0)

    # ---- 第二关 走廊 ----
    corridor, corridor_player, enemy = build_corridor()
    write_physics(corridor, corridor_player, enemy=enemy,
                  triggers={CABINET_A, CABINET_B, CORRIDOR_END})
    ai_tree = build_ai_tree(PLAYER_CORRIDOR)
    build_corridor_logic(corridor, corridor_player, enemy, ai_tree,
                         (CABINET_A, CABINET_B), PLAYER_CORRIDOR,
                         CAM3_CORRIDOR, CAM1_CORRIDOR)
    _apply_settings(corridor, (("dimension", "3"),
                               ("toon_follow_light", True)) + ENV_CORRIDOR)
    _set_scene_bgm(corridor, BGM_CORRIDOR, -14.0)

    # ---- 第三关 结局 ----
    ending, ending_player = build_ending()
    write_physics(ending, ending_player)
    build_ending_logic(ending, ending_player, CAM3_ENDING, CAM1_ENDING)
    _apply_settings(ending, (("dimension", "3"), ("toon_follow_light", True))
                    + ENV_ENDING)
    _set_scene_bgm(ending, BGM_ENDING, -16.0)

    directory = os.path.join(OUT_DIR, "恐怖闯关示例_godot")
    project.create_project(directory, title="恐怖闯关示例",
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
    # 主场景显式指到**大堂**(开局从这里进)
    exported = {f"res://scenes/{e['scene_name']}.tscn"
                for e in result.get("scenes", []) if e.get("ok")}
    if LOBBY_RES in exported:
        project.set_main_scene(directory, LOBBY_RES)
    log(f"主场景: {LOBBY_RES}")

    write_audio(directory)

    save_blend(BLEND_PATH)
    log(f"已保存 {BLEND_PATH}")
    log(f"工程目录: {directory}")
    log("按键:WASD 走 / 空格 跳 / 鼠标转视角 / 滚轮 缩放 / "
        "C 第一人称 · V 第三人称 / F 手电 / 结局按 1 或 2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
