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

**第一 / 第三人称**：玩家拥有两根弹簧臂和两台子相机，C/V 切换。
第一人称高度 1.55、臂长 0.25、俯仰 0；第三人称臂长 5、高度 1.4、俯仰 12。
一张角色图负责鼠标转向、移动、手电和电量。三关共用同一个运行时玩家。

最后由 story_upgrade 将构建时的旧角色占位合并成一个“玩家”层级；两台相机和
手电成为玩家子对象，其余关卡只保留出生点。运行时保留同一个玩家实例。

按键:WASD 走 / 空格 跳 / 鼠标转视角 / 滚轮 缩放 / **C 第一人称 · V 第三人称** /
**F 手电开关键** / **Esc 暂停** / **Alt 呼出鼠标** / 大堂里走钥匙、走大门 /
走廊里钻柜子(真潜行)/ 结局按 **1 或 2**。
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
if HERE not in sys.path:
    sys.path.insert(0, HERE)

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
AI_STATE_VAR = "ai_state"    # 和『AI_启用』的『状态变量名』一致
CHOICE_VAR = "走廊选择"

SIGHT = 6.0
LOST_RANGE = 9.0
# 视野锥(度,总张角):120 = 正前方 ±60°。0 是"不看角度"(360° 全向)。
# 第一百二十六轮从 0 改成 120 —— 用户要的是"别被它看见"能靠**绕背后**做到,
# 全向感知下这条不可能成立。⚠ 改这个 = 改难度:以前 6 米内无死角。
SIGHT_ANGLE = 120.0
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

# ── 音频(第一百一十一轮起**音效也走"本地素材"**)────────────────────
# 上一版这几条是 `res://audio/pickup.wav` 字面量:文件由 write_audio 直接
# 写进**示例自己那个工程**(恐怖闯关示例_godot)。可用户按 F5 时导出的是
# **推导出来的那个工程**(中文名压不出 ASCII -> 回退 `game_godot`),
# 于是那些 wav 根本不在 —— 真机日志刷屏 `Resource file not found`,
# 音效一声不响。现在改成和 BGM 一样的做法:源文件生成到 OUT_DIR 下的
# `_sfx/`,节点上填**磁盘路径**,导出时由插件复制进工程的 audio/。
SFX_DIR = os.path.join(OUT_DIR, "_sfx")
PICKUP_FILE = os.path.join(SFX_DIR, "pickup.wav")
DOOR_FILE = os.path.join(SFX_DIR, "door.wav")
STING_FILE = os.path.join(SFX_DIR, "sting.wav")
HEART_FILE = os.path.join(SFX_DIR, "heartbeat.wav")
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
    faces = [(3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5),
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
               shadow=True):
    data = bpy.data.lights.new(f"{name}数据", type=kind)
    data.energy = energy
    data.color = color
    data.use_shadow = shadow
    if kind == "SPOT" and spot_size is not None:
        data.spot_size = spot_size
    if kind in ("POINT", "SPOT"):
        data.shadow_soft_size = 0.12
        data.use_custom_distance = True
        data.cutoff_distance = cutoff
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


# 场景环境字段 -> 预设字段的映射(和 ui.py 的 _ENV_PRESET_FIELDS 一致;
# 预设风格字段 p_style_* 另有一套,见 _save_env_presets)
_ENV_TO_PRESET = (
    ("p_background", "env_background"), ("p_bg_color", "env_bg_color"),
    ("p_ambient_color", "env_ambient_color"),
    ("p_ambient_energy", "env_ambient_energy"),
    ("p_fog", "env_fog"), ("p_fog_color", "env_fog_color"),
    ("p_fog_density", "env_fog_density"), ("p_glow", "env_glow"),
    ("p_glow_intensity", "env_glow_intensity"),
    ("p_glow_bloom", "env_glow_bloom"), ("p_adjust", "env_adjust"),
    ("p_brightness", "env_brightness"), ("p_contrast", "env_contrast"),
    ("p_saturation", "env_saturation"),
)
_STYLE_TO_PRESET = (
    ("p_style_shadow_step", "style_shadow_step"),
    ("p_style_shadow_tint", "style_shadow_tint"),
    ("p_style_three_tone", "style_three_tone"),
    ("p_style_rim", "style_rim"),
    ("p_style_specular", "style_specular"),
)


def _save_env_presets(lobby, corridor, ending):
    """把三关的氛围 + 卡通风格存进文件级预设库(示范环境系统)。

    打开 .blend 后,『游戏环境设置』卡的预设下拉里就有这三套命名预设,
    能一键套到别的关卡;游戏里『环境_应用预设』也能按名字切。
    重建示例要**幂等** —— 先把旧预设清空再存。

    ── 第 123 轮改名:名字里带上"卡通"两个字 ────────────────────────
    下拉里同时有内置那几档(★ 卡通日间 / 卡通黄昏 / 卡通夜晚 / 恐怖夜…),
    示例自己的三套如果只叫"大堂氛围 / 走廊氛围 / 结局氛围",用户看不出
    它们**也是二次元配方**(他这次要的正是这个),所以名字里点明。
    """
    from BlenderGameKit import properties

    library = properties.env_preset_library()
    library.env_presets.clear()
    for preset_name, scene in (("示例·卡通门厅", lobby),
                               ("示例·卡通走廊", corridor),
                               ("示例·卡通结局", ending)):
        preset = library.env_presets.add()
        preset.preset_name = preset_name
        settings = getattr(scene, "gk_settings", None)
        if settings is None:
            continue
        for p_field, e_field in _ENV_TO_PRESET:
            setattr(preset, p_field, getattr(settings, e_field))
        for p_field, e_field in _STYLE_TO_PRESET:
            setattr(preset, p_field, getattr(settings, e_field))
    log("已把三关氛围 + 卡通风格存进预设库:示例·卡通门厅 / 示例·卡通走廊 / 示例·卡通结局")


# ── 三关的环境氛围(都走『游戏环境设置』那张卡的字段)──────────────────
# ── 第 123 轮:整体改配成**卡通二次元**观感(用户实测反馈)─────────────
# 用户原话:"现在的环境渲染成了『中世纪』的风格了,但我想要的是卡通二次元"。
# 上一版的配方(亮部 br≈0.95、环境光 0.09~0.22、饱和度 0.75~1.0、
# 色调映射 ACES + 暖棕色墙)叠起来就**正好是写实奇幻**:
#   * ACES 把高光压下去、饱和度抽掉一层 -> 颜色发灰发土;
#   * 环境光只有 0.1 量级 -> 暗部直接死黑,只剩灯打到的那几块暖棕;
#   * 暖棕的墙 + 暖色的灯 -> "中世纪酒馆"。
# 二次元要的是反过来:**高调(亮部干净)、暗部有色相(冷)、颜色够艳**。
# 所以这一版:
#   * `env_tonemap` 从 ACES 换回 **LINEAR**(ACES 是"电影感"的元凶);
#   * `env_ambient_energy` 抬到 0.78~0.95(暗部不再死黑,这是最关键的一条);
#   * 墙/地/柱的 albedo 整体抬亮、去掉那层棕(见 build_lobby 的 make_box);
#   * 阴影色一律**冷色相**(原来那几组已经是冷紫,保留并统一);
#   * 灯保留暖色(暖光 + 冷影是动画的经典对比),但强度上调。
# ⚠ 恐怖那一关(走廊)仍然是**冷蓝压抑**,只是不再"脏"—— 这是有意的:
#   剧情恐怖和卡通二次元在**这一关**的平衡点是"颜色干净的冷",不是"糊一层灰"。
# ── 雾密度(第一百一十一轮标定保留)──────────────────────────────────
# 按"近处清楚、远处发白"定:5 米处 ~6%、20 米处 ~20~45%,是薄雾不是牛奶。
# (Godot 公式: 遮挡比例 = 1 - exp(-密度 × 距离))
ENV_LOBBY = (
    ("env_enabled", True),
    ("env_background", "COLOR"),
    # 背景色:暖灰蓝的"室内" —— 原来的 (0.045,0.05,0.075) 太黑,
    # 二次元里背光处应该是**有颜色的暗**,不是黑。
    ("env_bg_color", (0.16, 0.18, 0.26, 1.0)),
    ("env_ambient_color", (0.62, 0.68, 0.88, 1.0)),
    ("env_ambient_energy", 0.85),
    ("env_fog", True),
    ("env_fog_color", (0.42, 0.48, 0.66, 1.0)),
    ("env_fog_density", 0.008),
    ("env_glow", True),
    ("env_glow_intensity", 0.6),
    ("env_glow_bloom", 0.08),
    # ⚠ LINEAR 不是 ACES:ACES 会把颜色压灰,是"中世纪感"的一半来源。
    ("env_tonemap", "LINEAR"),
    ("env_adjust", True),
    ("env_brightness", 1.06),
    ("env_contrast", 1.06),
    ("env_saturation", 1.18),
)
ENV_CORRIDOR = (
    ("env_enabled", True),
    ("env_background", "COLOR"),
    ("env_bg_color", (0.07, 0.09, 0.17, 1.0)),
    ("env_ambient_color", (0.48, 0.55, 0.86, 1.0)),
    ("env_ambient_energy", 0.78),
    ("env_fog", True),
    ("env_fog_color", (0.26, 0.31, 0.52, 1.0)),
    ("env_fog_density", 0.022),
    ("env_glow", True),
    ("env_glow_intensity", 0.95),
    ("env_glow_bloom", 0.14),
    ("env_tonemap", "LINEAR"),
    ("env_adjust", True),
    ("env_brightness", 1.04),
    ("env_contrast", 1.08),
    ("env_saturation", 1.16),
)
ENV_ENDING = (
    ("env_enabled", True),
    ("env_background", "COLOR"),
    ("env_bg_color", (0.10, 0.10, 0.20, 1.0)),
    ("env_ambient_color", (0.56, 0.56, 0.88, 1.0)),
    ("env_ambient_energy", 0.80),
    ("env_fog", True),
    ("env_fog_color", (0.34, 0.33, 0.56, 1.0)),
    ("env_fog_density", 0.010),
    ("env_glow", True),
    ("env_glow_intensity", 0.80),
    ("env_glow_bloom", 0.12),
    ("env_tonemap", "LINEAR"),
    ("env_adjust", True),
    ("env_brightness", 1.05),
    ("env_contrast", 1.07),
    ("env_saturation", 1.20),
)

# ── 三关的卡通全局风格(第一百一十六轮建 / 第 123 轮改配二次元)────────
# 『游戏环境设置』卡勾『全局卡通风格』后,这些值作为默认写进所有没调过
# 参数的材质 —— 阴影偏冷紫、明暗分界清晰(不用逐材质调)。
# ── 第 123 轮:从"恐怖氛围"改成"卡通二次元"──────────────────────────
#   * `three_tone` 0 -> **0.45~0.55**:动画赛璐璐是**多段**明暗
#     (亮部 / 中间调 / 暗部);只有两段会显得"平",而"平 + 冷紫 + 暖棕"
#     叠在一起,用户看到的就是"中世纪"。
#   * `shadow_step` 收一点(0.15 -> 0.12):分界线靠近亮部,亮面更干净。
#   * `shadow_tint` 保留冷紫(动画暗部的习惯),但比原来**浅一档** ——
#     原来那几组把暗部压得太重,也是"脏"的来源之一。
#   * `rim` 仍然 **0**:标准说不清就"两个都写清楚":
#     代码里它确实是 **ALBEDO 加法项**(gk_toon.gdshader:334),但第 117 轮
#     真机标定过"抬一点点整关发灰"。想加边缘光请在 Godot 里对单个材质手动
#     调,或用预设里的 `p_style_rim`(内置「卡通*」三档给的是按标定的值)。
STYLE_LOBBY = (
    ("style_enabled", True),
    ("style_shadow_step", 0.12),
    ("style_shadow_tint", (0.62, 0.62, 0.88, 1.0)),
    ("style_three_tone", 0.50),
    ("style_rim", 0.0),
    ("style_specular", 0.0),
)
STYLE_CORRIDOR = (
    ("style_enabled", True),
    ("style_shadow_step", 0.16),
    ("style_shadow_tint", (0.50, 0.50, 0.82, 1.0)),
    ("style_three_tone", 0.45),
    ("style_rim", 0.0),
    ("style_specular", 0.0),
)
STYLE_ENDING = (
    ("style_enabled", True),
    ("style_shadow_step", 0.14),
    ("style_shadow_tint", (0.56, 0.54, 0.86, 1.0)),
    ("style_three_tone", 0.55),
    ("style_rim", 0.0),
    ("style_specular", 0.0),
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
             (0.34, 0.36, 0.44, 1.0), scene=scene)
    make_box("大堂北墙", (30.0, 0.5, 6.0), (0.0, 12.0, 3.0),
             (0.52, 0.54, 0.62, 1.0), scene=scene)
    make_box("大堂南墙", (30.0, 0.5, 6.0), (0.0, -12.0, 3.0),
             (0.52, 0.54, 0.62, 1.0), scene=scene)
    make_box("大堂东墙", (0.5, 24.0, 6.0), (15.0, 0.0, 3.0),
             (0.52, 0.54, 0.62, 1.0), scene=scene)
    make_box("大堂西墙", (0.5, 24.0, 6.0), (-15.0, 0.0, 3.0),
             (0.52, 0.54, 0.62, 1.0), scene=scene)
    for index, (x, y) in enumerate(((-7.0, 3.0), (0.0, 3.0), (7.0, 3.0))):
        make_box(f"大堂柱{index + 1}", (1.4, 1.4, 6.0), (x, y, 3.0),
                 (0.60, 0.62, 0.70, 1.0), scene=scene)
    # 大门:一块会升起的门板 + 门口触发区(判定"钥匙够不够")
    make_box(DOOR_PANEL, (4.0, 0.7, 4.6), (0.0, 11.6, DOOR_Z_DOWN),
             (0.52, 0.42, 0.28, 1.0), scene=scene)
    make_box(DOOR_AREA, (5.0, 3.0, 0.2), (0.0, 10.0, 0.1),
             (0.36, 0.52, 0.48, 1.0), scene=scene)
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
    # ── bug5:用户第二次实测说"手电筒亮度太低了" ─────────────────────
    #   本插件的换算约定是 **1000W ≈ Godot 的 light_energy 1.0**
    #   (见 asset_export._light_energy_for,上限夹在 8.0)。上一版填 2400W
    #   -> 只有 2.4,照在深色卡通材质上几乎看不出来。现在直接顶到上限:
    #   8000W -> 8.0(= 换算上限)。
    # ── 第一百一十三轮:用户实测"照亮范围太大" ────────────────────────
    #   84° 锥角在地面上铺出半屏光斑(截图),不像手电筒像探照灯。
    #   收窄到 36°(真手电的主光斑量级);窄了能量更集中,顺手降一档
    #   (6000W = 6.0)免得过曝;射程 30 → 20(远处的墙不必照到)。
    torch_light = make_light(TORCH, "SPOT", (0.0, -8.0, 1.6), 6000.0,
                             (1.0, 0.96, 0.85), scene,
                             rotation=(math.radians(80.0), 0.0, 0.0),
                             spot_size=math.radians(36.0))
    torch_light.data.cutoff_distance = 20.0
    # ── 吊灯:120W 太暗了(第一百一十一轮)──────────────────────────
    # 卡通材质是 unshaded 的 —— 场景灯的亮度**原样**当 shader 的
    # `light_energy` 用(1000W = 1.0),而这个房间的墙面 albedo 只有 0.13:
    #   120W = 0.12 -> 0.13 × 0.12 ≈ 0.016,等于没开灯(用户:"灯光强度
    #   还是很低"、真机整间屋子黑着)。要"看得见但有气氛",给到 3.0:
    #   亮面 0.13 × 3 ≈ 0.4,暗面仍是冷色暗部。
    make_light("大堂吊灯", "POINT", (0.0, 0.0, 5.0), 3000.0, (0.85, 0.80, 0.62),
               scene, cutoff=16.0)
    log(f"{LOBBY}:地面 + 四壁 + 3 根柱子 + {DOOR_PANEL} + {DOOR_AREA} + "
        f"{KEY} + 玩家 + {TORCH} + 两台相机")
    return player


def build_corridor():
    """第二关 走廊:长走廊 + 巡逻怪物(3 个巡逻点)+ 2 个柜子 + 尽头门。"""
    scene = bpy.data.scenes.new(CORRIDOR)
    make_box("走廊地面", (48.0, 14.0, 0.4), (0.0, 0.0, -0.2),
             (0.30, 0.32, 0.40, 1.0), scene=scene)
    make_box("走廊墙北", (48.0, 0.5, 6.0), (0.0, 7.0, 3.0),
             (0.46, 0.48, 0.58, 1.0), scene=scene)
    make_box("走廊墙南", (48.0, 0.5, 6.0), (0.0, -7.0, 3.0),
             (0.46, 0.48, 0.58, 1.0), scene=scene)
    for index, x in enumerate((-13.0, 0.0, 13.0)):
        make_box(f"门框{index + 1}", (0.5, 13.0, 0.5), (x, 0.0, 4.6),
                 (0.54, 0.56, 0.66, 1.0), scene=scene)
    make_box("走廊尽头门", (3.0, 0.6, 4.6), (23.2, 0.0, 2.3),
             (0.44, 0.52, 0.42, 1.0), scene=scene)
    make_box(CORRIDOR_END, (5.0, 5.0, 0.2), (21.0, 0.0, 0.1),
             (0.36, 0.52, 0.48, 1.0), scene=scene)
    # ⚠ 出生点离巡逻线要**大于视野**:不然一进走廊就被发现、直接开追,
    #   恐怖游戏的"慢慢摸过去"就没有了(horror 示例实测踩过)。
    player = make_box(PLAYER_CORRIDOR, (1.0, 1.0, 1.8), (-20.0, -3.0, 1.0),
                      (0.80, 0.82, 0.90, 1.0), scene=scene)
    enemy = make_box(ENEMY, (1.4, 1.4, 2.0), (-9.0, 0.0, 1.0),
                     (0.72, 0.16, 0.20, 1.0), scene=scene)
    for name, x in ((PATROL_A, -9.0), (PATROL_B, 0.0), (PATROL_C, 9.0)):
        # ── 第一百二十六轮:巡逻点改**非实体**(见 write_physics)──────────
        # ⚠ **不要**把它们挪位置、更不要"抬到天花板里" —— 它们**同时是
        #   巡逻路点**:AI 每帧用 `_gk_global_position_3d(节点_巡逻点X)` 当
        #   目标点(实测:把三个点抬到 Godot y=5.3 之后,怪物跟着爬到
        #   y=5.0、卡在天花板里,`ray_blocker=走廊天花板`,AI 直接瞎了)。
        #   位置是**功能**,不能动;能动的是"它挡不挡人"。
        #
        # 它们在物理上原本是实心 `StaticBody3D`(胶囊 r=0.25,总高 2.5),
        # 摆在过道正中、角色高度。带来两个问题:
        #   ① 玩家沿走廊走过去会撞上一根**看不见的柱子**
        #      (真机量到玩家停在离巡逻点中心 0.760 m = 0.25 + 玩家半径 0.5);
        #   ② 新的遮挡射线会把它当遮挡物 —— 实测连线通常只擦过胶囊顶端
        #      半球(r 已收敛到 ~0.15)**打不中**,但换个角色高度 / 坡度就会
        #      命中,属于"偶发假看不见"。
        # 修法走仓库**现成**的那条路:`write_physics` 里已经有一套
        # "作为触发区 = Area3D = 不阻挡"的标准处理(和柜子 / 终点门同一个
        # 机制)。所以这里只把它们列进 `triggers`,形状 / 位置 / 外观都不变。
        make_box(name, (0.5, 0.5, 2.0), (x, 0.0, 1.0),
                 (0.42, 0.50, 0.48, 1.0), scene=scene)
    # 2 个"柜子":躲藏区(区域 + 看得见的柜体)
    # ── 真潜行(第一百零九轮)─────────────────────────────────────
    # 柜子触发区勾『躲藏区』:导出后那个 Area 挂进 Godot group
    # `gk_hide_spot`,怪物的『看见』判定(`_gk_ai_can_see_3d`)会先扫这个组
    # —— 玩家和躲藏区重叠就判看不见(哪怕正对、够近)。以前只能把 AI 状态
    # 冻成不存在的名字,现在这是真能力。
    cab_a = make_box(CABINET_A, (2.0, 2.0, 3.2), (-4.0, 5.2, 1.6),
                     (0.56, 0.44, 0.32, 1.0), scene=scene)
    cab_b = make_box(CABINET_B, (2.0, 2.0, 3.2), (6.0, -5.2, 1.6),
                     (0.56, 0.44, 0.32, 1.0), scene=scene)
    for cab in (cab_a, cab_b):
        cab_physics = getattr(cab, "gk_physics", None)
        if cab_physics is not None:
            cab_physics.is_hide_spot = True
    make_camera(CAM3_CORRIDOR, (0.0, -20.0, 5.5),
                (math.radians(72.0), 0.0, 0.0), scene)
    make_camera(CAM1_CORRIDOR, (0.0, -10.0, 1.6),
                (math.radians(90.0), 0.0, 0.0), scene)
    scene.camera = bpy.data.objects[CAM3_CORRIDOR]
    make_light("走廊壁灯", "POINT", (0.0, 0.0, 4.6), 900.0, (0.80, 0.84, 1.0),
               scene, cutoff=18.0)
    log(f"{CORRIDOR}:地面 + 两侧墙 + 3 个门框 + 尽头门 + 玩家 + {ENEMY} + "
        f"3 个巡逻点(非实体)+ {CABINET_A}/{CABINET_B} + 两台相机")
    return scene, player, enemy, (PATROL_A, PATROL_B, PATROL_C)


def build_ending():
    """第三关 结局:一个小房间 + 一点灯光。"""
    scene = bpy.data.scenes.new(ENDING)
    make_box("结局地面", (18.0, 18.0, 0.4), (0.0, 0.0, -0.2),
             (0.32, 0.30, 0.40, 1.0), scene=scene)
    make_box("结局北墙", (18.0, 0.5, 5.0), (0.0, 9.0, 2.5),
             (0.48, 0.46, 0.58, 1.0), scene=scene)
    make_box("结局南墙", (18.0, 0.5, 5.0), (0.0, -9.0, 2.5),
             (0.48, 0.46, 0.58, 1.0), scene=scene)
    make_box("结局箱甲", (2.2, 2.2, 2.0), (-5.0, 3.0, 1.0),
             (0.52, 0.48, 0.42, 1.0), scene=scene)
    make_box("结局箱乙", (2.2, 2.2, 2.0), (5.0, 3.0, 1.0),
             (0.52, 0.48, 0.42, 1.0), scene=scene)
    player = make_box(PLAYER_ENDING, (1.0, 1.0, 1.8), (0.0, -6.0, 1.0),
                      (0.80, 0.82, 0.90, 1.0), scene=scene)
    make_camera(CAM3_ENDING, (0.0, -14.0, 5.5),
                (math.radians(72.0), 0.0, 0.0), scene)
    make_camera(CAM1_ENDING, (0.0, -7.0, 1.6),
                (math.radians(90.0), 0.0, 0.0), scene)
    scene.camera = bpy.data.objects[CAM3_ENDING]
    make_light("结局灯", "POINT", (0.0, 0.0, 4.2), 1400.0, (0.70, 0.78, 1.0),
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


def _sequence(tree, event, name, x, y, targets):
    sequence = _add(tree, "GKNodeSequence", name, x, y,
                    gk_step_count=len(targets))
    _link(tree, event, "执行", sequence, "执行")
    for index, target in enumerate(targets, 1):
        _link(tree, sequence, str(index), target, "执行")
    return sequence


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

    # ── 布局(第一百一十七轮:按状态机流程一行排开)──────────────────
    # 状态机的转场链**本来就成环**(巡逻→看见→追击→丢失→查看→安静→回
    # 巡逻),Blender 会把成环的线画成红色 —— 那只是"成环提醒",不是错误。
    # 以前网格状乱摆,红线横穿全图,读图的人(用户实测)以为 AI 出了 bug。
    # 现在按流程从左到右排成一条链、转场卡在两个状态之间,回环只剩一条
    # 从右往左的线,一眼能读;再放一个注释卡把"红线 = 正常"写在图上。
    patrol = _add(tree, "GKNodeAiState", "巡逻", -400.0, 0.0,
                  gk_state_name=PATROL_STATE, gk_behavior="PATROL",
                  gk_patrol_a=PATROL_A, gk_patrol_b=PATROL_B,
                  gk_patrol_c=PATROL_C, gk_speed=PATROL_SPEED,
                  gk_stop_distance=0.7, gk_facing="MOVE", gk_in_count=3)
    chase = _add(tree, "GKNodeAiState", "追击", 80.0, 0.0,
                 gk_state_name="追击", gk_behavior="CHASE",
                 gk_goal=player_name, gk_speed=CHASE_SPEED,
                 gk_stop_distance=1.6, gk_facing="GOAL")
    look = _add(tree, "GKNodeAiState", "去查看", 560.0, 0.0,
                gk_state_name="去查看", gk_behavior="INVESTIGATE",
                gk_speed=INVESTIGATE_SPEED, gk_stop_distance=1.0,
                gk_facing="MOVE")
    start = _add(tree, "GKNodeAiStart", "AI开始", -640.0, 0.0)
    # ── 真潜行(第一百零九轮):『看见』走『AI_看得见』(PIN),而不是转场里
    #   那个『看见目标』快捷(SEE)。两者的区别:快捷只看距离;『AI_看得见』
    #   生成的 `_gk_ai_can_see_3d` 会先扫 `gk_hide_spot` 组 —— 玩家在柜子里
    #   就判看不见。
    #
    # ── 第一百二十六轮(用户第 1 条"AI 遮挡射线")改了这里两件事 ────────
    # ① 视野角 `0.0 -> 120.0`。
    #    以前填 0 = **不看角度**(360° 全向):走廊里 6 米内**正后方**也会被
    #    发现,"绕背后摸过去"根本不成立 —— 潜行玩法等于没有。填 120 =
    #    正前方 ±60° 的锥。
    #    ⚠ **难度变了**:以前是"6 米内无死角",现在背后可以摸过去(见下方
    #      `check_story_demo.py` 里那条断言也一并从"+Z 方向"改成"沿着怪物
    #      当前前方")。
    # ② 遮挡射线现在是**默认开**的(场景设置『AI 视线遮挡』),所以墙真的
    #    会挡视线 —— 配套把 `巡逻点甲/乙/丙` 抬到天花板里(见 build_corridor),
    #    免得它们在眼高线上当"看不见的柱子"。
    eye = _add(tree, "GKNodeAiSee", "看见玩家(潜行)", -150.0, 300.0,
               gk_goal=player_name, gk_distance=SIGHT, gk_angle=SIGHT_ANGLE)
    see = _add(tree, "GKNodeAiTransition", "看见玩家", -160.0, 0.0,
               gk_cond_source="PIN")
    _link(tree, eye, "看得见", see, "条件")
    back = _add(tree, "GKNodeAiTransition", "丢失玩家", 320.0, 0.0,
                gk_cond_source="LOST", gk_goal=player_name, gk_sight=LOST_RANGE)
    calm = _add(tree, "GKNodeAiTransition", "安静下来", 800.0, 0.0,
                gk_cond_source="CALM", gk_ear=0.0, gk_memory=1.5)
    _add(tree, "GKNodeComment", "状态机说明", -640.0, 480.0,
         gk_text="AI 状态机的转场一路指回『巡逻』 = 正常循环;"
                 "成环的线被 Blender 画红只是提醒,不是错误")

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

    ⚠ 平滑(『平滑』)必须给 **0**(第一百一十轮修,用户原话"仍有第一人称
      移动输入控制视角旋转的问题")。为什么:
        弹簧臂每帧把相机摆到"枢轴 + 杆方向 × 臂长"的位置,再 `look_at(枢轴)`。
        臂长只有 0.25 米,而平滑的稳态滞后 = 速度 ÷ 平滑系数
        (5.5 m/s ÷ 12 ≈ 0.46 米)—— **比杆子还长**。往侧面走(A/D)时相机被
        落在侧面半米外,它还在盯着枢轴看,视线就被甩过去几十度:
        玩家看到的是"按移动键 = 视角自己转"。填 0 = 相机每帧硬贴在头上,
        `look_at` 的方向永远等于杆子方向,视角只剩鼠标能动(真 FPS 的手感)。

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
        node.gk_lag = 0.0
        changed += 1
    # 顺手把预设里那两个"俯瞰/侧视"的名字换成第一/三人称,读图不用换算
    for node in logic.nodes:
        if node.name == "切到俯瞰":
            node.name = "切到第一人称"
        elif node.name == "切回侧视":
            node.name = "切到第三人称"
    log(f"第一/三人称:C→{cam_first}(第一人称) ;改写了 {changed} 根杆子")


def _wire_first_person_model(logic, cam_first, cam_third):
    """第一人称时把角色自己的网格藏掉,第三人称再显示回来。

    ── 为什么会有"看到自己身体"的 bug ─────────────────────────────
    角色是预设生成的 CharacterBody3D,下面挂着一个 `Model` 子节点(角色的
    网格)。第一人称把相机贴到头上的枢轴,相机就**落在网格内部**—— 画面
    里全是自己盒子的内壁 / 面。所以切到第一人称那一帧要把 `Model` 藏掉,
    切回第三人称再显示。

    做法:挂在预设那两台 `相机_切换` 节点(已被 `_make_first_person` 改名成
    『切到第一人称』/『切到第三人称』)的『完成』后面 —— C 藏、V 显。
    不每帧读"当前相机是谁":切换那一刻就知道结果,少一条每帧开销。
    """
    for node in logic.nodes:
        if getattr(node, "bl_idname", "") != "GKNodeCameraSetCurrent":
            continue
        target = str(getattr(node, "gk_target", "") or "")
        # ⚠ gk_target 填 "Model" 会被生成器按**全局名字**找:场景里门板 /
        #   柜子也有各自的 Model 子节点,全局找会拿到**第一个** Model(不是
        #   角色的)。填 "./Model" = 显式"自己脚下的 Model" —— 全局按名
        #   找不到 "./Model",生成器退回 `_gk_ref` 的相对路径查找,正好命中
        #   脚本所在角色自己的网格(真 Godot 探针踩出来的)。
        if target == cam_first:
            hide = _add(logic, "GKNodeSetVisible", "藏自身模型",
                        node.location[0] + 320.0, node.location[1],
                        gk_target="./Model", gk_visible=False)
            _link(logic, node, "完成", hide, "执行")
            # ── 第一百一十三轮:记住选择(跨关卡)─────────────────────
            remember = _add(logic, "GKNodePrefSave", "记住:第一人称",
                            node.location[0] + 640.0, node.location[1],
                            gk_pref_key="视角", gk_kind="TEXT",
                            gk_value_text="第一")
            _link(logic, hide, "完成", remember, "执行")
        elif target == cam_third:
            show = _add(logic, "GKNodeSetVisible", "显自身模型",
                        node.location[0] + 320.0, node.location[1] + 120.0,
                        gk_target="./Model", gk_visible=True)
            _link(logic, node, "完成", show, "执行")
            remember3 = _add(logic, "GKNodePrefSave", "记住:第三人称",
                             node.location[0] + 640.0, node.location[1] + 120.0,
                             gk_pref_key="视角", gk_kind="TEXT",
                             gk_value_text="第三")
            _link(logic, show, "完成", remember3, "执行")


def _wire_pref_restore(logic, ready_node, cam_first):
    """开局恢复上一关记住的视角模式(第一百一十三轮)。

    默认(从没记过 / 记的是第三人称)= 初始态就是第三人称,什么都不用做;
    只有"记住的是第一人称"才要恢复:切第一人称机位 + 藏自身模型 ——
    和 C 键那两个动作一模一样。接在 HUD 建完之后(hud 的『完成』出口),
    不挤『每当_准备』的主链。
    """
    load = _add(logic, "GKNodePrefLoad", "取回视角偏好",
                -1050.0, 1500.0, gk_pref_key="视角", gk_kind="TEXT",
                gk_default_text="第三")
    compare = _add(logic, "GKNodeCompare", "上次是第一人称?",
                   -900.0, 1500.0, gk_op="EQ", gk_b="第一")
    _link(logic, load, "值", compare, "A")
    branch = _add(logic, "GKNodeBranch", "恢复第一人称?",
                  -750.0, 1500.0)
    _link(logic, compare, "结果", branch, "条件")
    switch = _add(logic, "GKNodeCameraSetCurrent", "恢复:切第一人称",
                  -600.0, 1500.0, gk_target=cam_first)
    hide = _add(logic, "GKNodeSetVisible", "恢复:藏自身模型",
                -450.0, 1500.0, gk_target="./Model", gk_visible=False)
    _link(logic, branch, "是", switch, "执行")
    _link(logic, switch, "完成", hide, "执行")
    # ⚠ 接『每当_准备』直连,不接 HUD 链尾 —— 走廊的 HUD 链尾排在
    #   开场对话后面,对话不点完恢复就永远不跑(真机探针抓到的)。
    #   『每当_准备』可以扇出多条,_ready 里恢复最先执行。
    _link(logic, ready_node, "执行", branch, "执行")


def build_character(logic_scene, player, logic_name, class_name, cam_third,
                    cam_first):
    """三关共用一张角色图，相机通过角色实例的命名引用绑定。"""
    from BlenderGameKit import presets
    logic = bpy.data.node_groups.get("玩家角色逻辑")
    if logic is None:
        logic, _anim, notes = presets.create_character_preset(
            logic_scene, character=player,
            logic_name="玩家角色逻辑", class_name=class_name,
            with_animation=False, with_ports=False,
            camera_side="@第三人称相机", camera_top="@第一人称相机",
            move_speed=MOVE_SPEED,
            arm_length=THIRD_ARM, arm_height=THIRD_HEIGHT, aim_pitch=THIRD_PITCH,
        )
        _make_first_person(logic, "@第一人称相机")
        _wire_first_person_model(logic, "@第一人称相机", "@第三人称相机")
        build_alt_mouse(logic)
        ready = next(n for n in logic.nodes if n.bl_idname == "GKNodeOnReady")
        _wire_pref_restore(logic, ready, "@第一人称相机")
        organize_character_graph(logic)
    ready = next(n for n in logic.nodes if n.bl_idname == "GKNodeOnReady")
    logic_scene.gk_settings.base_class = "CharacterBody3D"
    logic_scene.gk_settings.class_name = class_name
    logic.gk_scope = "ROLE"
    if player is not None:
        physics = player.gk_physics
        physics.logic_tree = logic.name
        physics.script_target = True
        for name, target in (("第一人称相机", cam_first), ("第三人称相机", cam_third)):
            binding = next((b for b in physics.logic_bindings if b.binding_name == name), None)
            if binding is None:
                binding = physics.logic_bindings.add()
                binding.binding_name = name
            binding.target = bpy.data.objects[target]
        log(f"角色『{player.name}』共用『{logic.name}』")
    return logic, ready


def organize_character_graph(tree):
    """先用序列固定执行顺序，再按职责排成可阅读的六个分组。"""
    from BlenderGameKit.codegen.graph import position_key
    if any(n.bl_idname == "NodeFrame" for n in tree.nodes):
        return
    # 固定旧图的分支顺序，整理位置不会改变行为。
    for node in list(tree.nodes):
        for socket in list(node.outputs):
            if socket.bl_idname != "GKSocketFlow" or len(socket.links) < 2:
                continue
            links = sorted(socket.links, key=lambda l: position_key(l.to_node))
            if len(links) > 8:
                raise ValueError("角色流程分支超过八项")
            targets = [link.to_socket for link in links]
            for link in links:
                tree.links.remove(link)
            sequence = tree.nodes.new("GKNodeSequence")
            sequence.name = node.name + " · 顺序"
            sequence.gk_step_count = len(targets)
            tree.links.new(socket, sequence.inputs["执行"])
            for index, target in enumerate(targets):
                tree.links.new(sequence.outputs[index], target)
    groups = {title: [] for title in (
        "01 · 初始化与视角偏好", "02 · 移动与跳跃", "03 · 鼠标转向与相机",
        "04 · 滚轮视野", "05 · 第一／第三人称切换", "06 · Alt 鼠标捕获")}
    for node in tree.nodes:
        name = node.name
        if any(s in name for s in ("准备", "锁定鼠标", "初始视野", "偏好", "恢复", "上次")):
            group = 0
        elif "Alt" in name:
            group = 5
        elif any(s in name for s in ("滚轮", "当前视野", "新视野", "改视野")) or name in ("零", "如果_否则.001"):
            group = 3
        elif any(s in name for s in ("按 C", "按 V", "切到", "自身模型", "记住:")) or name in ("如果_否则.002", "如果_否则.003"):
            group = 4
        elif any(s in name for s in ("鼠标移动", "拆成", "灵敏度", "偏航", "俯仰", "弹簧臂")):
            group = 2
        else:
            group = 1
        groups[list(groups)[group]].append(node)
    row_top = 0.0
    for index, (title, nodes) in enumerate(groups.items()):
        frame = tree.nodes.new("NodeFrame")
        frame.label = title
        frame.name = title
        frame.use_custom_color = True
        frame.color = (0.12, 0.20, 0.28)
        # 同组连线从左向右；列中节点竖排，给展开后的属性留足空间。
        depth = {node: 0 for node in nodes}
        for _ in nodes:
            changed = False
            for link in tree.links:
                if link.from_node in depth and link.to_node in depth:
                    level = depth[link.from_node] + 1
                    if level > depth[link.to_node]:
                        depth[link.to_node] = level
                        changed = True
            if not changed:
                break
        columns = {}
        frame.location = (0.0, row_top)
        for node in nodes:
            node.parent = frame
            column = depth[node]
            row = columns.get(column, 0)
            node.location = (column * 360.0, -row * 540.0)
            node.width = 260.0
            columns[column] = row + 1
        row_top -= max(columns.values(), default=1) * 540.0 + 240.0


# ==========================================================================
# HUD 骨架 —— 一处定义、三关复用
# ==========================================================================
def _new_story_ui_tree(name):
    tree = bpy.data.node_groups.new(name, "GKUiDesignTree")
    tree.use_fake_user = True
    return tree


def build_hud(logic, design, chapter, goal, keys=False, battery=False, hint=False):
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
    goal_text = _add(design, "GKNodeUiText", "目标文本", -300.0, 1500.0,
                     gk_text=goal, gk_x=18.0, gk_y=18.0, gk_size=20,
                     gk_color=(0.90, 0.92, 1.0, 1.0))
    handles = {"goal": goal_text.name, "keys": None, "battery": None,
               "hint": None}
    if keys:
        keys_text = _add(design, "GKNodeUiText", "钥匙文本", -60.0, 1500.0,
                         gk_text="钥匙:0", gk_x=18.0, gk_y=48.0, gk_size=20,
                         gk_color=(0.95, 0.85, 0.40, 1.0))
        handles["keys"] = keys_text.name
    if battery:
        battery_text = _add(design, "GKNodeUiText", "电量文本", 180.0, 1500.0,
                            gk_text="手电电量:100", gk_x=18.0, gk_y=78.0,
                            gk_size=18, gk_color=(0.60, 0.85, 1.0, 1.0))
        handles["battery"] = battery_text.name
    if hint:
        hint_text = _add(design, "GKNodeUiText", "提示文本", 420.0, 1500.0,
                         gk_text="", gk_align="CENTER", gk_size=24,
                         gk_color=(1.0, 0.55, 0.55, 1.0))
        handles["hint"] = hint_text.name
    # ── 按键提示(第六轮 bug3)已搬到『主界面』(第一百一十二轮)────────
    # 三关是同一行字,以前每关各建一份;"知道按哪个键"这行现在由跨场景
    # 常驻的主界面兜住(见 build_main_ui)。handles 里仍留名字,方便引用。
    handles["keyhint"] = "按键提示"
    # 章节卡排链尾:亮 3 秒 → 淡出(生成的 await 也就落在最后一句)
    card = _add(design, "GKNodeUiText", "章节卡", 660.0, 1500.0,
                gk_text=chapter, gk_align="CENTER", gk_size=40,
                gk_color=(0.92, 0.92, 1.0, 1.0))
    apply = _add(logic, "GKNodeUiApplyDesign", f"套用{chapter}界面",
                 -300.0, 1500.0, gk_design=design.name)
    hold = _add(logic, "GKNodeWait", "章节停留", 900.0, 1500.0)
    hold.inputs["秒"].default_value = 3.0
    fade = _add(logic, "GKNodeUiSet", "章节淡出", 1140.0, 1500.0,
                gk_widget=card.name, gk_set_alpha=True, gk_alpha=0.0)
    _link(logic, apply, "完成", hold, "执行")
    _link(logic, hold, "完成", fade, "执行")
    log(f"HUD『{chapter}』:{goal}"
        f"{' + 钥匙数' if keys else ''}{' + 电量' if battery else ''}"
        f"{' + 提示' if hint else ''} + 章节卡(按键提示在主界面)")
    # 第三个返回值 = **章节卡链的尾巴**(淡出那句):开场对话要接在它后面,
    # 才能"先亮章节卡、再弹对话"(以前靠"菜单热键排在最前"顺带带出来的
    # 顺序;第一百一十二轮菜单搬走后,显式从这里接)。
    return apply, handles, fade


# ==========================================================================
# 三关共用的两块:暂停菜单 + Alt 呼出鼠标
# ==========================================================================
def build_main_ui():
    """主界面(跨场景常驻):按键提示 + 暂停菜单 + 设置页 —— 一次定义、全局生效。

    ── 第一百一十二轮:整块从玩法图搬进『游戏界面』────────────────────
    本函数原来叫 `build_pause_menu(logic, design)`,在三个关卡**各跑一次**
    —— 三份重复控件 + 三份按钮绑定,正是用户说的"绘制多个重复 UI"。现在:

      * 控件 **和** 行为都写在**一张**设计图『主界面』里(勾『主界面』);
      * 导出成 Godot autoload:开局建一次、**切关卡不消失**;
      * 玩法图里一个界面节点都不用 —— 界面自带的行为在这张图里生效
        (没有从别处接进来的语句 = 界面建好后执行一次;按钮『点击』出口
        = 回调;Esc 由『界面_暂停游戏』的『Esc 也能开关』挂着)。

    ── 三个踩过的坑(照旧,别改回去)────────────────────────────────
    ⚠ 按钮在暂停时点得动:`get_tree().paused = true` 之后按钮的 `pressed`
      照样触发(GUI 输入是视口在收),不用改 process_mode。
    ⚠ 设置面板**挂在『暂停菜单』下面**(填『父控件』):"继续游戏"把菜单
      收起来时,设置面板跟着一起消失;不然按 Esc 继续之后它留在画面上。
    ⚠ 设置面板**不能勾『垫底』**:勾了会被挪到父节点最下面,菜单按钮就
      盖住它了。
    ⚠ Esc 热键那一下**不勾『暂停』**:这句在界面建好后先执行一遍,
      "勾了"= 开局直接暂停弹菜单;"不勾"= 初始化成没暂停 + 挂上开关。
    """
    design = _new_story_ui_tree("主界面")
    design.gk_main_ui = True
    y = 1700.0

    # ---- 按键提示(三关同一行字;以前在 build_hud 里每关建一份)----
    _add(design, "GKNodeUiText", "按键提示", 540.0, 1500.0,
         gk_text="WASD 移动 · 鼠标视角 · C/V 视角 · F 手电 · "
                 "回车 对话 · 1/2 选择 · Esc 暂停 · Alt 鼠标",
         gk_align="BOTTOM_CENTER", gk_margin=8.0, gk_size=16,
         gk_color=(0.85, 0.88, 0.95, 0.9))

    # ---- 暂停菜单(照旧,只是从此只有一份)----
    menu = _add(design, "GKNodeUiPanel", "暂停菜单", -200.0, y,
                gk_width=320.0, gk_height=250.0, gk_radius=18,
                gk_align="CENTER", gk_start_hidden=True, gk_focus_first=True,
                gk_color=(0.05, 0.06, 0.09, 0.90))
    title = _add(design, "GKNodeUiText", "暂停标题", 40.0, y,
                 gk_text="已暂停", gk_size=26, gk_align="TOP_CENTER",
                 gk_margin=18.0, gk_parent_widget="暂停菜单")
    resume_btn = _add(design, "GKNodeUiButton", "继续按钮", 280.0, y,
                      gk_text="继续游戏", gk_x=90.0, gk_y=72.0,
                      gk_width=140.0, gk_height=42.0, gk_parent_widget="暂停菜单")
    settings_btn = _add(design, "GKNodeUiButton", "设置按钮", 280.0, y - 200.0,
                        gk_text="设置", gk_x=90.0, gk_y=120.0,
                        gk_width=140.0, gk_height=42.0,
                        gk_parent_widget="暂停菜单")
    quit_btn = _add(design, "GKNodeUiButton", "退出按钮", 280.0, y - 400.0,
                    gk_text="退出游戏", gk_x=90.0, gk_y=168.0,
                    gk_width=140.0, gk_height=42.0, gk_parent_widget="暂停菜单")

    # 设置页作为暂停菜单的子控件,按 Esc 收起菜单时会一起隐藏。
    sheet = _add(design, "GKNodeUiPanel", "设置面板", 520.0, y - 200.0,
                 gk_width=300.0, gk_height=238.0, gk_radius=14,
                 gk_x=10.0, gk_y=6.0, gk_bottom=False,
                 gk_start_hidden=True, gk_block_click=True,
                 gk_focus_first=True,
                 gk_parent_widget="暂停菜单",
                 gk_color=(0.08, 0.09, 0.13, 0.98))
    sheet_title = _add(design, "GKNodeUiText", "设置标题", 760.0, y - 200.0,
                       gk_text="设置", gk_size=22, gk_align="TOP_CENTER",
                       gk_margin=6.0, gk_parent_widget="设置面板")
    volume = _add(design, "GKNodeUiSlider", "音量滑块", 760.0, y - 400.0,
                  gk_min=-40.0, gk_max=0.0, gk_step=1.0, gk_value=-14.0,
                  gk_label="音量", gk_suffix=" dB",
                  gk_x=14.0, gk_y=48.0, gk_width=272.0, gk_height=34.0,
                  gk_parent_widget="设置面板")
    brightness = _add(design, "GKNodeUiSlider", "亮度滑块", 760.0, y - 460.0,
                      gk_min=0.0, gk_max=0.30, gk_step=0.02, gk_value=0.0,
                      gk_label="亮度", gk_suffix="",
                      gk_x=14.0, gk_y=88.0, gk_width=272.0, gk_height=34.0,
                      gk_parent_widget="设置面板")
    quality_low = _add(design, "GKNodeUiButton", "画质低按钮", 760.0, y - 520.0,
                       gk_text="低画质", gk_x=14.0, gk_y=98.0,
                       gk_width=84.0, gk_height=38.0,
                       gk_parent_widget="设置面板")
    quality_mid = _add(design, "GKNodeUiButton", "画质中按钮", 760.0, y - 580.0,
                       gk_text="中画质", gk_x=108.0, gk_y=98.0,
                       gk_width=84.0, gk_height=38.0,
                       gk_parent_widget="设置面板")
    quality_high = _add(design, "GKNodeUiButton", "画质高按钮", 760.0, y - 640.0,
                        gk_text="高画质", gk_x=202.0, gk_y=98.0,
                        gk_width=84.0, gk_height=38.0,
                        gk_parent_widget="设置面板")
    back_btn = _add(design, "GKNodeUiButton", "返回按钮", 760.0, y - 700.0,
                    gk_text="返回", gk_x=14.0, gk_y=174.0,
                    gk_width=272.0, gk_height=38.0,
                    gk_parent_widget="设置面板")

    # ---- 行为:全部写在**设计图内部**(顶层语句 = 建好后执行一次;
    #      按钮『点击』/ 滑块『变了』出口 = 回调)----
    interface_y = y - 1100.0
    resume_click = _add(design, "GKNodeUiOnClick", "继续游戏点击", -200.0,
                        interface_y, gk_widget="继续按钮")
    settings_click = _add(design, "GKNodeUiOnClick", "打开设置点击", -200.0,
                          interface_y - 20.0, gk_widget="设置按钮")
    quit_click = _add(design, "GKNodeUiOnClick", "退出游戏点击", -200.0,
                      interface_y - 40.0, gk_widget="退出按钮")
    volume_change = _add(design, "GKNodeUiOnValueChanged", "音量变化", -200.0,
                         interface_y - 60.0, gk_widget="音量滑块")
    brightness_change = _add(design, "GKNodeUiOnValueChanged", "亮度变化",
                             -200.0, interface_y - 80.0, gk_widget="亮度滑块")
    quality_clicks = [
        _add(design, "GKNodeUiOnClick", f"画质{label}点击", -200.0,
             interface_y - 100.0 - index * 20.0, gk_widget=f"画质{label}按钮")
        for index, label in enumerate(("低", "中", "高"))
    ]
    back_click = _add(design, "GKNodeUiOnClick", "返回设置点击", -200.0,
                      interface_y - 180.0, gk_widget="返回按钮")

    resume_act = _add(design, "GKNodeUiPauseGame", "继续游戏", 60.0, y - 20.0,
                      gk_pause=False, gk_menu="暂停菜单", gk_hotkey=False)
    open_sheet = _add(design, "GKNodeUiHide", "开设置面板", 60.0, y - 140.0,
                      gk_widget="设置面板", gk_visible=True, gk_raise=True)
    close_sheet = _add(design, "GKNodeUiHide", "关设置面板", 60.0, y - 860.0,
                       gk_widget="设置面板", gk_visible=False)
    quit_unpause = _add(design, "GKNodeUiPauseGame", "退出前解除暂停", 60.0,
                        y - 260.0, gk_pause=False, gk_menu="暂停菜单",
                        gk_hotkey=False)
    quit_game = _add(design, "GKNodeQuitGame", "退出游戏", 300.0, y - 260.0)
    volume_action = _add(design, "GKNodeBusVolume", "应用总线音量", 60.0,
                         y - 380.0, gk_bus="Master", gk_fade=0.0)
    brightness_action = _add(design, "GKNodeScreenFilter", "应用屏幕亮度",
                             60.0, y - 500.0,
                             gk_color=(1.0, 1.0, 1.0, 1.0))
    quality_nodes = [
        _add(design, "GKNodeQualitySet", f"画质{level}档", 60.0,
             y - 620.0 - index * 80.0, gk_level=level)
        for index, level in enumerate(("LOW", "MID", "HIGH"))
    ]
    _link(design, resume_click, "点击", resume_act, "执行")
    _link(design, settings_click, "点击", open_sheet, "执行")
    _link(design, back_click, "点击", close_sheet, "执行")
    _link(design, quit_click, "点击", quit_unpause, "执行")
    _link(design, quit_unpause, "完成", quit_game, "执行")
    _link(design, volume_change, "变了", volume_action, "执行")
    _link(design, volume_change, "值", volume_action, "目标音量")
    _link(design, brightness_change, "变了", brightness_action, "执行")
    _link(design, brightness_change, "值", brightness_action, "强度")
    for click, action in zip(quality_clicks, quality_nodes):
        _link(design, click, "点击", action, "执行")
    # Esc 热键(开/关型;建好先跑一遍"取消暂停",那是**初始化**,不是玩法)
    _add(design, "GKNodeUiPauseGame", "暂停热键", -200.0, interface_y - 200.0,
         gk_pause=False, gk_menu="暂停菜单", gk_hotkey=True)
    log("主界面:按键提示 + 暂停菜单(Esc 开关 / 继续 / 设置 / 退出)—— "
        "跨场景常驻,全局一份")
    return design


def build_alt_mouse(logic):
    """每关物理帧:按住 Alt 鼠标可见、松开锁回(不暂停世界,方便点 HUD)。

    读 Alt(『输入_按键』按住)→ 分支:按住走『鼠标_模式』VISIBLE、
    没按走 CAPTURED。挂在角色预设那台『每当_物理帧』后面 —— 暂停时
    物理帧不跑,不会和暂停菜单的鼠标互抢。
    """
    physics = None
    for node in logic.nodes:
        if node.bl_idname == "GKNodeOnPhysicsProcess" and node.name == "每当_物理帧":
            physics = node
            break
    if physics is None:
        for node in logic.nodes:
            if node.bl_idname == "GKNodeOnPhysicsProcess":
                physics = node
                break
    if physics is None:
        return
    alt = _add(logic, "GKNodeKey", "按Alt", -1200.0, -3400.0,
               gk_key="LEFT_ALT", gk_trigger="PRESSED")
    branch = _add(logic, "GKNodeBranch", "Alt分支", -950.0, -3400.0)
    show_mouse = _add(logic, "GKNodeMouseMode", "Alt显鼠标", -700.0, -3300.0,
                      gk_mode="VISIBLE")
    lock_mouse = _add(logic, "GKNodeMouseMode", "Alt锁鼠标", -700.0, -3500.0,
                      gk_mode="CAPTURED")
    _link(logic, physics, "执行", branch, "执行")
    _link(logic, alt, "结果", branch, "条件")
    _link(logic, branch, "是", show_mouse, "执行")
    _link(logic, branch, "否", lock_mouse, "执行")
    log("Alt 呼出鼠标:按住 Alt 鼠标可见、松开锁回(挂物理帧)")


# ==========================================================================
# 第一关 大堂 的逻辑
# ==========================================================================
def build_lobby_logic(scene, player, player_name, cam_third, cam_first):
    """大堂:章节卡/HUD + 手电(开关/电量) + 拾取钥匙 + 大门(升起/切场景)+ 存档。

    ── 第一百一十九轮:角色逻辑独立 ────────────────────────────────
    角色图(移动/相机/视角)由 build_character 建并挂在角色物体上;
    这里的关卡流程(手电/钥匙/门/HUD/对话/章节/AI)建在**关卡图**里,
    由场景的 logic_tree 指向它 —— 两张图各司其职。
    """
    build_character(scene, player, "大堂角色", "StoryLobbyPlayer",
                    cam_third, cam_first)
    # 关卡图:手电/钥匙/门/HUD/对话/章节(AI 在走廊)
    logic = bpy.data.node_groups.new("大堂逻辑", "GKGameLogicTree")
    logic.gk_scope = "LEVEL"
    logic.use_fake_user = True
    ready = logic.nodes.new("GKNodeOnReady")
    ready.name = "每当_准备"
    scene.gk_settings.logic_tree = "大堂逻辑"
    design = _new_story_ui_tree("大堂界面")
    # HUD 先建(拿到控件名),但**头节点最后才接 ready**(见 build_hud 的说明)
    hud_head, hud, _card_end = build_hud(logic, design, "第一章:门厅",
                              "目标:找到钥匙,打开大门",
                              keys=True, battery=True, hint=True)

    # 开局:手电关着 + **电量满格** + 自动存档一次(槽位 story)
    # ⚠ 电量必须**显式**初始化成满格:变量默认是 0,而"灯亮"的条件里带着
    #   『还有电』(灯亮 = 手电开 且 电量 > 0)—— 不初始化的话开局就没电,
    #   手电永远点不亮(真 Godot 探针实测抓到的)。
    battery_full = _add(logic, "GKNodeSetVar", "电量开局满", -900.0, 600.0,
                        gk_var_name=BATTERY, gk_value=BATTERY_MAX)
    # ⚠ 『手电开』要**用布尔字面量**初始化:它是真假变量,可『强制关』那句
    #   写的是数字 0.0 —— 谁先出现谁定类型,数字先出现的话整段会被当成
    #   float,后面 `手电开 = (not 手电开)` 赋布尔进去,Godot 解析期直接报
    #   类型不符、整个脚本加载失败(所以这里显式写一个 false 把类型钉死)。
    torch_off_flag = _add(logic, "GKNodeConstBool", "手电关常量", -1150.0, 660.0,
                          gk_value=False)
    off_flag = _add(logic, "GKNodeSetVar", "手电开局关标志", -900.0, 660.0,
                    gk_var_name=TORCH_ON)
    _link(logic, torch_off_flag, "值", off_flag, "值")
    torch_off = _add(logic, "GKNodeSetLight", "手电开局关", -900.0, 760.0,
                     gk_light=TORCH, gk_on=False)
    save = _add(logic, "GKNodeSaveWrite", "大堂存档", -620.0, 760.0,
                gk_slot="story", gk_save_verify=True)

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
    _sequence(logic, ready, "大堂开局顺序", -1160.0, 960.0,
              (battery_full, off_flag, torch_off, save, hud_head))
    _sequence(logic, phys, "手电每帧顺序", -1150.0, 100.0,
              (f_branch, apply_light, bat_ui))
    # 暂停菜单 / 按键提示在『主界面』里(跨场景常驻,全局一份)——
    # 玩法图这边不用再接任何界面链(第一百一十二轮);Alt 鼠标在角色图里。
    log(f"{LOBBY}逻辑:章节/HUD + 手电(F/电量) + 钥匙拾取 + 大门升起 -> "
        f"切 {CORRIDOR_RES} + 存档 story")
    return logic


# ==========================================================================
# 第二关 走廊 的逻辑
# ==========================================================================
def build_corridor_logic(scene, player, enemy, ai_tree, cabinets, player_name,
                         cam_third, cam_first):
    """走廊:章节目录 + 开场对话/选择(两链汇合)+ 巡逻 AI + 惊吓 + 躲藏 + 尽头门。"""
    build_character(scene, player, "走廊角色", "StoryCorridorPlayer",
                    cam_third, cam_first)
    logic = bpy.data.node_groups.new("走廊逻辑", "GKGameLogicTree")
    logic.gk_scope = "LEVEL"
    logic.use_fake_user = True
    ready = logic.nodes.new("GKNodeOnReady")
    ready.name = "每当_准备"
    scene.gk_settings.logic_tree = "走廊逻辑"
    design = _new_story_ui_tree("走廊界面")
    hud_head, hud, card_end = build_hud(logic, design, "第二章:走廊",
                                        "目标:穿过走廊,别被它看见", hint=True)
    # (暂停菜单 / 按键提示在『主界面』里 —— 这里不用再建任何界面链)

    # 开局:启动 AI + 自动存档
    enable = _add(logic, "GKNodeAiEnable", "启动怪物AI", -900.0, 700.0,
                  gk_tree_name=ai_tree.name, gk_target=ENEMY,
                  gk_state_var=AI_STATE_VAR, gk_debug_print=True)
    save = _add(logic, "GKNodeSaveWrite", "走廊存档", -620.0, 700.0,
                gk_slot="story", gk_save_verify=True)

    # ---- 开场:旁白 + 主角两句 + 剧情选择 ----
    # ⚠ 对话框是**阻塞**的(await 等玩家点)。开场先把世界暂停 + 鼠标放出
    #   (『开局选单态』,照 demo_ui):不然玩家鼠标还是 CAPTURED 锁着,点不到
    #   选择按钮;选完再继续世界 + 锁回鼠标(见下面『合流函数』)。
    startup = _add(logic, "GKNodeUiPauseGame", "开局选单态", -540.0, 1900.0,
                   gk_pause=True, gk_show_mouse=True, gk_menu="",
                   gk_hotkey=False)
    d1 = _add(logic, "GKNodeUiDialog", "旁白", -300.0, 1900.0,
              gk_speaker="", gk_text="走廊很长,灯全灭了。", gk_speed=24.0)
    d2 = _add(logic, "GKNodeUiDialog", "主角1", -60.0, 1900.0,
              gk_speaker="我", gk_text="必须穿过去。", gk_speed=24.0)
    d3 = _add(logic, "GKNodeUiDialog", "主角2", 180.0, 1900.0,
              gk_speaker="我", gk_text="……那声音越来越近了。", gk_speed=24.0)
    choice = _add(logic, "GKNodeUiChoice", "走廊选择", 420.0, 1900.0,
                  gk_question="怎么办?", gk_option_count=2,
                  gk_options="往前跑|先躲一下", gk_speed=24.0)
    # 开场对话接在**章节卡链尾**(亮 3 秒 + 淡出之后才弹)。暂停菜单不在
    # 这条链里了 —— 它在『主界面』(autoload)里,开局就已挂好 Esc 热键
    # (第一百一十二轮)。选单态本身照旧:暂停世界 + 放鼠标给玩家点选。
    _link(logic, card_end, "完成", startup, "执行")
    _link(logic, startup, "完成", d1, "执行")
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
    # 选完之后:继续世界 + 把鼠标锁回去(开场"选单态"的反向操作)
    resume = _add(logic, "GKNodeUiPauseGame", "选完恢复", 420.0, 1300.0,
                  gk_pause=False, gk_show_mouse=True, gk_menu="",
                  gk_hotkey=False)
    lock_mouse = _add(logic, "GKNodeMouseMode", "锁回鼠标", 660.0, 1300.0,
                      gk_mode="CAPTURED")
    _link(logic, merge_head, "执行", shared, "执行")
    _link(logic, shared, "完成", resume, "执行")
    _link(logic, resume, "完成", lock_mouse, "执行")

    for number, pin in ((1, "选项1"), (2, "选项2")):
        row = 1600.0 - number * 220.0
        value = _add(logic, "GKNodeConstFloat", f"选择值{number}", 660.0, row,
                     gk_value=float(number))
        store = _add(logic, "GKNodeSetVar", f"记选择{number}", 900.0, row,
                     gk_var_name=CHOICE_VAR)
        call = _add(logic, "GKNodeFunctionCall", f"调用合流{number}",
                    1380.0, row, gk_func_name="走廊合流")
        _link(logic, value, "值", store, "值")
        _link(logic, choice, pin, store, "执行")
        _link(logic, store, "完成", call, "执行")

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
                gk_a=player_name, gk_b=ENEMY)
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
    # ── 真潜行(第一百零九轮)────────────────────────────────────────
    # 柜子触发区已经勾了『躲藏区』(见 build_corridor),怪物的『看见』判定
    # 会自动跳过躲在里面的玩家 —— **不用再把 AI 状态冻成不存在的名字**。
    # 这里只留"钻柜 / 出柜"的氛围:暗角加深 + HUD 提示"屏住呼吸…"、
    # 出柜恢复。看不见这件事由 `gk_hide_spot` 那套真能力负责。
    for index, cab in enumerate(cabinets):
        row = -2400.0 - index * 500.0
        entered = _add(logic, "GKNodeOnAreaEntered", f"躲进{cab}", -900.0, row,
                       gk_area=cab, gk_filter=player_name)
        vig_in = _add(logic, "GKNodeScreenVignette", f"暗角加深{index + 1}",
                      -660.0, row, gk_amount=0.82, gk_radius=0.42,
                      gk_edge=0.5, gk_fade=0.4)
        hint_in = _add(logic, "GKNodeUiSet", f"屏息提示{index + 1}",
                       -420.0, row, gk_widget=hud["hint"], gk_set_text=True,
                       # ── 提示里必须写"怎么出去"(第一百一十轮)──────────
                       # 用户原话:"部分提示没有按键显示,如屏住呼吸…" ——
                       # 只说"屏住呼吸"玩家会以为要按什么键屏息,站在柜子里干等。
                       # 这里是**走进柜子**(触发区进出自动切),不是按键动作,
                       # 所以要把动作说清楚:走出去就解除。
                       gk_text="屏住呼吸…(按 WASD 走出柜子就安全)")
        _link(logic, entered, "执行", vig_in, "执行")
        _link(logic, vig_in, "完成", hint_in, "执行")

        left = _add(logic, "GKNodeOnAreaExited", f"离开{cab}", 300.0, row,
                    gk_area=cab, gk_filter=player_name)
        vig_out = _add(logic, "GKNodeScreenVignette", f"暗角复原{index + 1}",
                       540.0, row, gk_amount=0.45, gk_radius=0.60,
                       gk_edge=0.40, gk_fade=0.4)
        hint_out = _add(logic, "GKNodeUiSet", f"清提示{index + 1}", 780.0, row,
                        gk_widget=hud["hint"], gk_set_text=True, gk_text="")
        _link(logic, left, "执行", vig_out, "执行")
        _link(logic, vig_out, "完成", hint_out, "执行")

    # ---- 走到尽头 → 切到结局 ----
    end = _add(logic, "GKNodeOnAreaEntered", "走到尽头", -900.0, -3600.0,
               gk_area=CORRIDOR_END, gk_filter=player_name)
    change = _add(logic, "GKNodeChangeScene", "去结局", -660.0, -3600.0,
                  gk_scene_path=ENDING_RES)
    _link(logic, end, "执行", change, "执行")

    _sequence(logic, ready, "走廊开局顺序", -1150.0, 1100.0,
              (enable, save, hud_head))
    # (暂停菜单在『主界面』里;Alt 鼠标在角色图里)
    log(f"{CORRIDOR}逻辑:章节/HUD + AI(3 巡逻点) + 心跳/惊吓 + 躲藏 x2 + "
        f"开场对话/选择(合流函数)-> 切 {ENDING_RES}")
    return logic


# ==========================================================================
# 第三关 结局 的逻辑
# ==========================================================================
def build_ending_logic(scene, player, cam_third, cam_first):
    """结局:章节/HUD + 旁白 + 结局选择(两个结局各自存档 / 大字 / BGM 淡出)-> 回大堂。"""
    build_character(scene, player, "结局角色", "StoryEndingPlayer",
                    cam_third, cam_first)
    logic = bpy.data.node_groups.new("结局逻辑", "GKGameLogicTree")
    logic.gk_scope = "LEVEL"
    logic.use_fake_user = True
    ready = logic.nodes.new("GKNodeOnReady")
    ready.name = "每当_准备"
    scene.gk_settings.logic_tree = "结局逻辑"
    design = _new_story_ui_tree("结局界面")
    _add(design, "GKNodeUiText", "结局大字", 1380.0, 1200.0,
         gk_text="", gk_align="CENTER", gk_size=34,
         gk_color=(0.95, 0.92, 0.80, 1.0))
    hud_head, _hud, card_end = build_hud(logic, design, "终章:抉择",
                                         "目标:做出你的选择")
    # (暂停菜单 / 按键提示在『主界面』里)

    vig = _add(logic, "GKNodeScreenVignette", "结局暗角", -900.0, 800.0,
               gk_amount=0.60, gk_radius=0.50, gk_edge=0.35)

    # 开场:暂停世界 + 放鼠标(不然结局选择点不到),选完再继续 + 锁回
    startup = _add(logic, "GKNodeUiPauseGame", "开局选单态", -540.0, 1900.0,
                   gk_pause=True, gk_show_mouse=True, gk_menu="",
                   gk_hotkey=False)
    d1 = _add(logic, "GKNodeUiDialog", "旁白", -300.0, 1900.0,
              gk_speaker="", gk_text="出口就在前面。", gk_speed=24.0)
    d2 = _add(logic, "GKNodeUiDialog", "主角", -60.0, 1900.0,
              gk_speaker="我", gk_text="……她还在后面。", gk_speed=24.0)
    choice = _add(logic, "GKNodeUiChoice", "结局选择", 180.0, 1900.0,
                  gk_question="你要怎么做?", gk_option_count=2,
                  gk_options="回头救她|独自逃出去", gk_speed=24.0)
    # 和走廊同一个顺序讲究:开场对话接在**章节卡链尾**(淡出之后才弹)。
    _link(logic, card_end, "完成", startup, "执行")
    _link(logic, startup, "完成", d1, "执行")
    _link(logic, d1, "完成", d2, "执行")
    _link(logic, d2, "完成", choice, "执行")

    # 两个结局:每个选项先"继续世界 + 锁回鼠标",再走各自结局链(存档
    # story_end + 记「结局」键 → 大字 → 总线淡出 → 停留 → 回大堂闭环)。
    endings = ((1, "选项1", "回头救她"), (2, "选项2", "独自逃出去"))
    for number, pin, label in endings:
        row = 1600.0 - number * 320.0
        resume = _add(logic, "GKNodeUiPauseGame", f"选完恢复{number}",
                      420.0, row - 140.0, gk_pause=False, gk_show_mouse=True,
                      gk_menu="", gk_hotkey=False)
        lock_mouse = _add(logic, "GKNodeMouseMode", f"锁回鼠标{number}",
                          420.0, row + 20.0, gk_mode="CAPTURED")
        value = _add(logic, "GKNodeConstFloat", f"结局值{number}", 420.0, row,
                     gk_value=float(number))
        set_end = _add(logic, "GKNodeSetVar", f"记结局{number}", 660.0, row,
                       gk_var_name="结局")
        write = _add(logic, "GKNodeSaveSet", f"写结局{number}", 900.0, row,
                     gk_key="结局")
        save = _add(logic, "GKNodeSaveWrite", f"保存结局{number}", 1140.0, row,
                    gk_slot="story_end", gk_save_verify=True)
        text = _add(logic, "GKNodeUiSet", f"结局大字{number}", 1380.0, row,
                    gk_widget="结局大字", gk_set_text=True,
                    gk_text=f"结局:{label}")
        fade = _add(logic, "GKNodeBusVolume", f"BGM淡出{number}", 1620.0, row,
                    gk_bus="Master", gk_db=-60.0, gk_fade=3.0)
        wait = _add(logic, "GKNodeWait", f"停留{number}", 1860.0, row)
        wait.inputs["秒"].default_value = 4.0
        back = _add(logic, "GKNodeChangeScene", f"回大堂{number}", 2100.0, row,
                    gk_scene_path=LOBBY_RES)
        _link(logic, value, "值", set_end, "值")
        _link(logic, value, "值", write, "值")
        _link(logic, choice, pin, resume, "执行")
        _link(logic, resume, "完成", lock_mouse, "执行")
        _link(logic, lock_mouse, "完成", set_end, "执行")
        _link(logic, set_end, "完成", write, "执行")
        _link(logic, write, "完成", save, "执行")
        _link(logic, save, "完成", text, "执行")
        _link(logic, text, "完成", fade, "执行")
        _link(logic, fade, "完成", wait, "执行")
        _link(logic, wait, "完成", back, "执行")

    _sequence(logic, ready, "结局开局顺序", -1150.0, 1100.0,
              (vig, hud_head))
    # (暂停菜单在『主界面』里;Alt 鼠标在角色图里)
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


def write_audio():
    """生成四个音效的**源文件**(心跳 / 惊吓 / 拾取 / 开门)到 `_sfx/`。

    和 BGM 同一个立场(见文件头):源文件放在示例目录里,节点上填磁盘
    路径;导出时插件把它们复制进**当前那个**工程的 audio/ 并改写成
    `res://audio/...`(第一百一十一轮)。所以导出到哪个工程都不会再缺文件。
    """
    rate = 22050

    def thump(duration, freq, decay, amp=0.55):
        total = int(rate * duration)
        out = []
        for i in range(total):
            t = i / rate
            out.append(amp * math.exp(-decay * t) * math.sin(2.0 * math.pi * freq * t))
        return out

    _write_wav(HEART_FILE, thump(0.16, 55.0, 26.0), rate)
    _write_wav(STING_FILE, thump(0.40, 320.0, 9.0), rate)
    # 拾取:两个上扬的短音
    pickup = thump(0.10, 880.0, 30.0, 0.4) + thump(0.14, 1320.0, 24.0, 0.4)
    _write_wav(PICKUP_FILE, pickup, rate)
    # 开门:低频"咯——"
    _write_wav(DOOR_FILE, thump(0.8, 120.0, 3.0, 0.5), rate)
    log(f"音效源:heartbeat / sting / pickup / door -> {SFX_DIR}")


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
    from BlenderGameKit import asset_export, project, register

    if bpy.types.NodeTree.bl_rna_get_subclass_py("GKUiDesignTree", None) is None:
        register()

    os.makedirs(OUT_DIR, exist_ok=True)
    # BGM / 音效的**源文件**要先有 —— 侧栏 scene_bgm 和节点路径都指向磁盘,
    # 导出时才复制进工程的 audio/(见 write_bgm_source / write_audio)
    write_bgm_source()
    write_audio()

    # ---- 第一关 大堂(用已有的那个场景)----
    lobby = clear_all()
    # ---- 主界面(跨场景常驻)----
    # 按键提示 + 暂停菜单 + 设置页:一张设计图、勾『主界面』、导成 autoload。
    # ⚠ 必须排在 clear_all() **之后** —— 它会把所有节点图清光。
    build_main_ui()
    lobby_player = build_lobby(lobby)
    lobby_door = bpy.data.objects[DOOR_PANEL]
    lobby_torch = bpy.data.objects[TORCH]
    write_physics(lobby, lobby_player, torch=lobby_torch, door=lobby_door,
                  triggers={KEY, DOOR_AREA})
    build_lobby_logic(lobby, lobby_player, PLAYER_LOBBY, CAM3_LOBBY, CAM1_LOBBY)
    _apply_settings(lobby, (("dimension", "3"), ("toon_follow_light", True))
                    + ENV_LOBBY + STYLE_LOBBY)
    _set_scene_bgm(lobby, BGM_LOBBY, -14.0)

    # ---- 第二关 走廊 ----
    corridor, corridor_player, enemy, patrol_points = build_corridor()
    # ⚠ 巡逻点列进 `triggers` = 导出成 Area3D(非实体)。
    #   为什么:它们既是巡逻路点(位置不能动)又是过道正中的实心碰撞体 ——
    #   玩家会撞上"看不见的柱子",新的遮挡射线也会把它当遮挡物。
    #   详见 build_corridor 里那段注释(第一百二十六轮)。
    write_physics(corridor, corridor_player, enemy=enemy,
                  triggers={CABINET_A, CABINET_B, CORRIDOR_END} | set(patrol_points))
    ai_tree = build_ai_tree(PLAYER_CORRIDOR)
    build_corridor_logic(corridor, corridor_player, enemy, ai_tree,
                         (CABINET_A, CABINET_B), PLAYER_CORRIDOR,
                         CAM3_CORRIDOR, CAM1_CORRIDOR)
    _apply_settings(corridor, (("dimension", "3"),
                               ("toon_follow_light", True)) + ENV_CORRIDOR
                    + STYLE_CORRIDOR)
    _set_scene_bgm(corridor, BGM_CORRIDOR, -14.0)

    # ---- 第三关 结局 ----
    ending, ending_player = build_ending()
    write_physics(ending, ending_player)
    build_ending_logic(ending, ending_player, CAM3_ENDING, CAM1_ENDING)
    _apply_settings(ending, (("dimension", "3"), ("toon_follow_light", True))
                    + ENV_ENDING + STYLE_ENDING)
    _set_scene_bgm(ending, BGM_ENDING, -16.0)

    from story_upgrade import upgrade
    upgrade()

    # 把三关氛围 + 卡通风格存进预设库(第一百一十六轮:示范环境系统)
    _save_env_presets(lobby, corridor, ending)

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

    save_blend(BLEND_PATH)
    log(f"已保存 {BLEND_PATH}")
    log(f"工程目录: {directory}")
    log("按键:WASD 走 / 空格 跳 / 鼠标转视角 / 滚轮 缩放 / "
        "C 第一人称 · V 第三人称 / F 手电 / Esc 暂停 / Alt 呼出鼠标 / "
        "结局按 1 或 2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
