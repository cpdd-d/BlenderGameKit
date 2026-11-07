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
| 3 | 结局   | 小房间、章节卡 + HUD、暗角、环境底噪;**不冻世界、不切关卡**,留给玩家继续观察环境 | 界面 / 屏幕_暗角 / 音效 / 灯光 |

**第一 / 第三人称**：玩家拥有两根弹簧臂和两台子相机，C/V 切换。
第一人称高度 1.55、臂长 0.25、俯仰 0；第三人称臂长 5、高度 1.4、俯仰 12。
一张角色图负责鼠标转向、移动、手电和电量。三关共用同一个运行时玩家。

最后由 story_upgrade 将构建时的旧角色占位合并成一个“玩家”层级；两台相机和
手电成为玩家子对象，其余关卡只保留出生点。运行时保留同一个玩家实例。

按键:WASD 走 / 空格 跳 / 鼠标转视角 / 滚轮 缩放 / **C 第一人称 · V 第三人称** /
**F 手电开关键** / **Esc 暂停** / **Alt 呼出鼠标** / 大堂里走钥匙、走大门 /
走廊里钻柜子(真潜行)/ 结局可以继续走动观察环境(不切关卡)。
(鼠标被锁定,先点一下游戏窗口再按。)

跑法(Godot):打开 `恐怖闯关示例_godot` 直接 F5(主场景 = 大堂)。
"""

import math
import os
import struct
import sys
import wave

import bpy
from mathutils import Vector

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
# ── 血量(第一百五十七轮,用户第 2 条)────────────────────────────────
# 以前"被它抓到"= 一次就死。现在给玩家三条命:每次贴脸扣 1 点,
# 扣到 0 才走失败链。怪物只在**玩家还有血**时才攻击(血量 0 = 已经倒下,
# 再打就是"惊吓 + 倒地 + 重开"叠在一起,画面和声音全乱)。
HEALTH = "生命"
HEALTH_MAX = 3
# 跨场景的"这一局是失败重开"标记(走『偏好_记住』,不落盘 ——
# 重开只在本局内有效,下次启动游戏还是从头开始)
RETRY = "重试"
PATROL_A = "巡逻点甲"
PATROL_B = "巡逻点乙"
PATROL_C = "巡逻点丙"
CABINET_A = "柜子甲"
CABINET_B = "柜子乙"
# 第一百四十七轮:走廊南墙外扩一个储藏间,里面放第三个柜子
CABINET_C = "柜子丙"
SIDE_ROOM = "走廊储藏间"
# 第一百五十六轮:大堂东侧的门卫室(钥匙搬到里面去了)
GUARD_ROOM = "门卫室"
GUARD_DOOR_AREA = "门卫室入口"
GUARD_MONITOR_AREA = "门卫室监控"
GUARD_LAMP = "门卫室灯"
GUARD_SCREEN_LIGHT = "门卫室屏幕光"
# ── 监控画面(第一百五十七轮,用户第 3 条)────────────────────────────
# 中间那块屏改成"实时画面":挂一台 SubViewport 相机,拍大堂的监控机位。
GUARD_SCREEN_LIVE = f"{GUARD_ROOM}屏幕2面"
# 摄像头装在大堂天花板下(朝向大门 —— 门后就是走廊),屏幕上就是它拍到的
GUARD_CCTV = "大堂摄像头"
CCTV_POS = (0.0, 6.5, 4.6)
CORRIDOR_END = "走廊尽头"
# 第一百四十六轮:追击时点亮的应急红灯 / 追击时会闪的那盏吊灯
ALARM_LIGHT = "走廊应急红光"
FLICKER_LIGHT = "走廊吊灯3"          # 警戒时压暗的那盏
# 第一百五十轮:大堂吊灯的摆动父节点 / 吱呀声
PENDANT_SWAY = "大堂吊灯摆动"
# 走廊开场运镜(第一百五十轮):推近看怪物 -> 停留 -> 缩回第三人称
MONSTER_CAM = "走廊怪物特写"
MONSTER_TRACK = "走廊运镜轨道"
# 第一百四十七轮:接触不良、一直闪的那盏(入口第一盏)
BROKEN_LAMP = "走廊吊灯1"
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

# ── 第一百四十七轮:第一人称的上下视角 ─────────────────────────────
# 预设把俯仰夹在 -20~70(那个范围是给**第三人称弹簧臂**定的:70 = 从高处
# 往下看)。第一人称要能抬头看天花板、低头看脚下,所以示例把夹紧的上下限
# 改成两个**变量**,C/V 切换时改它们:
#   第一人称 -82~82(几乎能看到正上/正下,但不至于翻过去)
#   第三人称 -20~70(原样,不然杆子会钻到地板下面)
PITCH_MIN_VAR = "俯仰下限"
PITCH_MAX_VAR = "俯仰上限"
PITCH_LIMITS_THIRD = (-20.0, 70.0)
PITCH_LIMITS_FIRST = (-82.0, 82.0)

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
# 第一百四十六轮:踩碎玻璃的声音(走廊的两处玻璃触发区)
GLASS_FILE = os.path.join(SFX_DIR, "glass.wav")
# 第一百五十轮:年久失修吊灯的吱呀声(循环)
CREAK_FILE = os.path.join(SFX_DIR, "creak.wav")
# 第一百五十一轮:自定义脚本示例(Blender 文本块 -> scripts/custom/*.gd)
DEMO_SCRIPT_NAME = "GK示例脚本"
# 开局 BGM 走**侧栏设置**(scene_bgm),导出时会把本地文件复制进 audio/ ——
# 所以这里给的是**磁盘路径**,文件先生成到 OUT_DIR 下一个源目录里。
BGM_DIR = os.path.join(OUT_DIR, "_bgm")
BGM_LOBBY = os.path.join(BGM_DIR, "bgm_lobby.wav")
BGM_CORRIDOR = os.path.join(BGM_DIR, "bgm_corridor.wav")
BGM_ENDING = os.path.join(BGM_DIR, "bgm_ending.wav")
# 第一百四十七轮:每关的**环境底噪**(和 BGM 叠着放,循环)
AMB_LOBBY = os.path.join(BGM_DIR, "amb_lobby.wav")
AMB_CORRIDOR = os.path.join(BGM_DIR, "amb_corridor.wav")
AMB_ENDING = os.path.join(BGM_DIR, "amb_ending.wav")


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


def no_shadow(obj):
    """让这个物体**不投阴影**(灯罩 / 灯泡 / 灯芯这种小零件)。

    ── 为什么需要(第一百四十八轮)────────────────────────────────
    灯罩是个圆台,点光在它下面 0.34 米。灯罩投在天花板上的影子本来只有
    **一个圆环**,但 Godot 的软阴影过滤在这么大的投影范围上会把它切成一
    圈圈同心圆(实机截图里像水波纹)。灯罩不投阴影之后,天花板是干净的
    一片暗,而**其他物体(柜子 / 怪物)照样投影**。
    """
    physics = getattr(obj, "gk_physics", None)
    if physics is not None:
        try:
            physics.render_no_shadow = True
        except Exception:                                 # noqa: BLE001
            pass
    return obj


def make_camera(name, location, rotation, scene, lens=50.0):
    data = bpy.data.cameras.new(f"{name}数据")
    data.lens = lens
    camera = bpy.data.objects.new(name, data)
    scene.collection.objects.link(camera)
    camera.location = location
    camera.rotation_euler = rotation
    return camera


def make_curve(name, points, scene, dimensions="3D"):
    """一条贝塞尔曲线(自动手柄)—— 导出时采样成 Godot 的 Curve3D。"""
    data = bpy.data.curves.new(f"{name}数据", type="CURVE")
    data.dimensions = dimensions
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
               rotation=(0.0, 0.0, 0.0), cutoff=12.0, spot_size=None,
               shadow=True, blend=0.5):
    data = bpy.data.lights.new(f"{name}数据", type=kind)
    data.energy = energy
    data.color = color
    data.use_shadow = shadow
    if kind == "SPOT" and spot_size is not None:
        data.spot_size = spot_size
        # 锥边柔化(第一百四十八轮):导出成 Godot 的
        # `spot_angle_attenuation`。0.5 = 边缘很柔,照在墙上是渐变的
        # 一片光而不是一个硬边椭圆(0.15 = Blender 出厂 = 硬边)。
        data.spot_blend = blend
    if kind in ("POINT", "SPOT"):
        data.shadow_soft_size = 0.12
        data.use_custom_distance = True
        data.cutoff_distance = cutoff
    light = bpy.data.objects.new(name, data)
    scene.collection.objects.link(light)
    light.location = location
    light.rotation_euler = rotation
    return light


def make_cone(name, radius_bottom, radius_top, depth, location, color,
              scene=None, glow=None, segments=24):
    """圆台 / 圆锥(灯罩专用):上小下大、**底面开口**。

    ── 为什么灯罩不能再用 make_box(第一百四十八轮)──────────────────
    方块灯罩在实机里读起来是"天花板下面挂了个白盒子"。真实吊灯的灯罩是
    一个上窄下宽的伞形,从下面看是暗的、边缘一圈被灯泡照亮 —— 这个形状
    本身就是"这是灯"的视觉语言。所以这里用底层 API 建一个不带底面的圆台
    (`make_box` 只能做长方体)。

    底面开口很重要:开口朝下,光才能从罩子里漏出去;同时从下往上看,
    `cull_disabled` 会让内侧也可见(深色),像真的灯罩内壁。
    """
    verts, faces = [], []
    for index in range(segments):
        angle = 2.0 * math.pi * index / segments
        verts.append((math.cos(angle) * radius_bottom,
                      math.sin(angle) * radius_bottom, -depth / 2.0))
    for index in range(segments):
        angle = 2.0 * math.pi * index / segments
        verts.append((math.cos(angle) * radius_top,
                      math.sin(angle) * radius_top, depth / 2.0))
    for index in range(segments):
        nxt = (index + 1) % segments
        faces.append((index, nxt, segments + nxt, segments + index))
    faces.append(tuple(range(segments * 2 - 1, segments - 1, -1)))  # 顶盖
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


def make_aimed_light(name, location, target, energy, color, scene,
                     spot_size=60.0, cutoff=18.0, shadow=True, volume=1.0,
                     softness=0.07):
    """从 `location` 照向 `target` 的聚光灯(窗光 / 天窗光 / 手电用)。

    自动算朝向 —— 手动填 rotation 太难对:一盏"从窗外斜打进来"的灯,
    三个欧拉角只要差一点,光斑就跑到墙上了。
    """
    light = make_light(name, "SPOT", location, energy, color, scene,
                       cutoff=cutoff, spot_size=math.radians(spot_size),
                       shadow=shadow)
    direction = Vector(target) - Vector(location)
    if direction.length > 1e-4:
        light.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    light.data.shadow_soft_size = softness
    if hasattr(light.data, "volume_factor"):
        light.data.volume_factor = volume
    return light


# ==========================================================================
# 房间尺寸(一处定义、三关 + story_upgrade 共用)
# ==========================================================================
# ── 第一百四十六轮:走廊从 14 米宽收到 8.4 米 ────────────────────────
# 以前 48×14 的空间读起来像"大厅",怪物和玩家之间的视线死角太多、
# 灯光也铺不满;恐怖游戏要的是窄、长、只能往前 —— 所以收窄一半。
# ⚠ 天花板也跟着从 6 降到 4.8:6 米高的窄走廊像隧道,压不出压迫感。
ROOM = {
    LOBBY: (30.0, 24.0, 6.0),
    CORRIDOR: (48.0, 8.4, 4.8),
    # 第一百四十七轮:结局房间 18×18 -> 22×20(关卡区域扩展)
    ENDING: (22.0, 20.0, 5.0),
}


def hide_mesh(obj):
    """判定体:网格**不进 glb**(看不见),但碰撞体 / Area3D 照旧生成。

    ── 为什么需要(第一百五十六轮)─────────────────────────────────
    触发区的网格是会被导出的:门洞里那个 2.2 米高的入口触发盒,实机里
    就是一块黑墙把门口堵死;地上的区域薄片则是一块发白的地板。
    以前只能靠"涂成近黑 + 做薄"糊过去,现在有 `gk_physics.hide_mesh`。
    """
    physics = getattr(obj, "gk_physics", None)
    if physics is not None:
        try:
            physics.hide_mesh = True
        except Exception:                                 # noqa: BLE001
            pass
    return obj


def decor(obj):
    """装饰件:不生成碰撞体。

    ── 为什么必须显式关掉 ──────────────────────────────────────────
    `write_physics` 会把场景里每个网格都设成 StaticBody3D,而导出侧
    对"自动形状"的处理是**按包围盒猜**:长条(窗棂 / 横梁 / 地毯)
    会被判成胶囊,半径 = 最长边的一半 —— 一根 8 米长的窗框会变成
    半径 4 米的隐形球把走廊堵死(第一百一十轮门框巨球同一个坑)。
    装饰件压根不该参与碰撞,所以这里直接选『无』。
    """
    physics = getattr(obj, "gk_physics", None)
    if physics is not None:
        try:
            physics.collision_shape = "none"
        except Exception:                                 # noqa: BLE001
            pass
    return obj


def solid_box(obj):
    """结构件:显式用盒子碰撞(别让"自动"把长条猜成胶囊)。"""
    physics = getattr(obj, "gk_physics", None)
    if physics is not None:
        try:
            physics.collision_shape = "box"
        except Exception:                                 # noqa: BLE001
            pass
    return obj


def make_wall_pieces(prefix, axis, wall_pos, span, thickness, height, scene,
                     openings, color):
    """带窗洞的墙:上下两条墙带 + 窗间墙柱(窗洞处留空)。

    `axis="x"` = 墙沿 X 展开(法线朝 ±Y);`axis="y"` = 沿 Y 展开。
    `openings` = [(中心, 宽), ...]。留空的洞真的能看到天(没有玻璃)。
    """
    start, end = span
    length = end - start
    sill = 1.05                 # 窗台高度
    head = 3.55                 # 窗顶高度
    pieces = []
    # 下墙带 / 上墙带(整条)
    for tag, z0, z1 in (("下墙带", 0.0, sill), ("上墙带", head, height)):
        size = (length, thickness, z1 - z0) if axis == "x" \
            else (thickness, length, z1 - z0)
        loc = (((start + end) / 2), wall_pos, (z0 + z1) / 2) if axis == "x" \
            else (wall_pos, (start + end) / 2, (z0 + z1) / 2)
        pieces.append(make_box(f"{prefix}{tag}", size, loc, color, scene=scene))
    # 窗间墙柱
    edges = [start]
    for center, width in sorted(openings):
        edges += [center - width / 2, center + width / 2]
    edges.append(end)
    index = 0
    for i in range(0, len(edges), 2):
        left, right = edges[i], edges[i + 1]
        if right - left <= 0.05:
            continue
        index += 1
        size = (right - left, thickness, head - sill) if axis == "x" \
            else (thickness, right - left, head - sill)
        loc = ((left + right) / 2, wall_pos, (sill + head) / 2) if axis == "x" \
            else (wall_pos, (left + right) / 2, (sill + head) / 2)
        pieces.append(make_box(f"{prefix}墙柱{index}", size, loc, color,
                               scene=scene))
    for piece in pieces:
        solid_box(piece)
    return pieces


def make_window(prefix, center, wall_pos, width, scene, axis="x",
                color=(0.20, 0.22, 0.26, 1.0)):
    """窗框:四边框 + 一根竖梃 + 一根横档(纯装饰,不挡人)。"""
    sill, head = 1.05, 3.55
    bar = 0.14
    depth = 0.30
    height = head - sill
    parts = []
    if axis == "x":
        # 上下框
        parts.append(make_box(f"{prefix}窗框下", (width, depth, bar),
                              (center, wall_pos, sill), color, scene=scene))
        parts.append(make_box(f"{prefix}窗框上", (width, depth, bar),
                              (center, wall_pos, head), color, scene=scene))
        # 左右框
        for side in (-1, 1):
            parts.append(make_box(
                f"{prefix}窗框{'左右'[side > 0]}",
                (bar, depth, height),
                (center + side * (width / 2 - bar / 2), wall_pos,
                 (sill + head) / 2), color, scene=scene))
        # 竖梃 + 横档
        parts.append(make_box(f"{prefix}窗棂竖", (0.10, depth, height),
                              (center, wall_pos, (sill + head) / 2),
                              color, scene=scene))
        parts.append(make_box(f"{prefix}窗棂横", (width, depth, 0.10),
                              (center, wall_pos, (sill + head) / 2 + 0.35),
                              color, scene=scene))
    else:
        parts.append(make_box(f"{prefix}窗框下", (depth, width, bar),
                              (wall_pos, center, sill), color, scene=scene))
        parts.append(make_box(f"{prefix}窗框上", (depth, width, bar),
                              (wall_pos, center, head), color, scene=scene))
        for side in (-1, 1):
            parts.append(make_box(
                f"{prefix}窗框{'左右'[side > 0]}",
                (depth, bar, height),
                (wall_pos, center + side * (width / 2 - bar / 2),
                 (sill + head) / 2), color, scene=scene))
        parts.append(make_box(f"{prefix}窗棂竖", (depth, 0.10, height),
                              (wall_pos, center, (sill + head) / 2),
                              color, scene=scene))
        parts.append(make_box(f"{prefix}窗棂横", (depth, width, 0.10),
                              (wall_pos, center, (sill + head) / 2 + 0.35),
                              color, scene=scene))
    for part in parts:
        decor(part)
    return parts


def make_pendant(name, x, ceiling_z, scene, energy=6000.0, cutoff=12.0,
                 color=(1.0, 0.93, 0.85), shade=(0.18, 0.17, 0.15, 1.0),
                 glow=0.12, on=True, broken=False, y=0.0):
    """一盏吊灯:吊杆 + **圆台灯罩** + 自发光灯泡 + 向下的聚光 + 一点上照。

    ── 第一百四十八轮:灯罩从"方块"换成"圆台"──────────────────────
    以前灯罩是 0.62 的方盒子,实机里读起来就是"天花板上挂了个白方块"。
    换成上窄下宽的圆台(r 0.42 -> 0.15)、开口朝下,再加一颗自发光灯泡,
    一眼就认得出是灯。

    ── 主光为什么是 70° 聚光(不是点光 / 不是 118°)───────────────
      * 点光:天花板离灯只有 1.5 米,平方反比把顶棚照到过曝 —— 整间屋子
        又变回"平光";
      * 118° 聚光:锥边**打在两面墙上**,切出一条硬斜线;
      * 70° 聚光:半径只到 2.4 米,碰不到 4.2 米外的墙,地面上是一个圆光斑 ——
        灯罩本来就该是这个效果。
    """
    rod = 0.9
    lamp_z = ceiling_z - rod - 0.18
    # ⚠ 吊杆 / 灯罩**不能有碰撞**(第一百五十五轮):它们挂在天花板下 4 米,
    #   玩家永远碰不到,却会挡住第三人称相机的避让射线 —— 相机被"杆子"
    #   顶住后正好贴在灯泡上(用户截图:满屏一个白色发光球)。
    decor(no_shadow(make_box(f"{name}吊杆", (0.06, 0.06, rod),
                             (x, y, ceiling_z - rod / 2),
                             (0.09, 0.09, 0.10, 1.0), scene=scene)))
    shade_color = (0.10, 0.09, 0.08, 1.0) if broken else shade
    shade_obj = decor(no_shadow(make_cone(
        f"{name}灯罩", 0.42, 0.15, 0.34, (x, y, lamp_z), shade_color,
        scene=scene,
        glow=None if broken else ((1.0, 0.68, 0.36, 1.0), glow))))
    light = None
    if not broken:
        bulb = no_shadow(make_box(
            f"{name}灯泡", (0.17, 0.17, 0.11),
            (x, y, lamp_z - 0.12), (1.0, 0.92, 0.78, 1.0),
            scene=scene, glow=((1.0, 0.88, 0.66, 1.0), 2.2)))
        decor(bulb)
        # 灯点在**罩口下面**:在罩子内部的话,罩子会把它整个挡黑。
        #
        # ── 主光:70° 聚光,朝正下方(第一百四十八轮第六版)──────────
        # 三种都试过,只有这一种同时满足"有光斑 / 天花板不炸 / 墙上没硬线":
        #   * 点光:天花板离灯只有 1.5 米,平方反比把顶棚照到过曝,整间
        #     屋子又变回"平光";(灯罩不投影之后它挡不住向上的光)
        #   * 118° 聚光:锥边**打在两面墙上**,切出一条硬斜线;
        #   * 70° 聚光:半径只到 2.4 米,碰不到 4.2 米外的墙,地面上是
        #     一个圆光斑 —— 灯罩本来就该是这个效果。
        light = make_light(name, "SPOT", (x, y, lamp_z - 0.34), energy, color,
                           scene, cutoff=cutoff,
                           spot_size=math.radians(70.0))
        if hasattr(light.data, "volume_factor"):
            light.data.volume_factor = 0.9
        # 第一百五十一轮第二轮反馈:"灯光范围过于完美"。
        # 吊灯的光池如果是一个标准圆/椭圆,投在地上像教学图。挂一张
        # 柔边不规则 cookie:三档明暗还在,但边缘和亮度都有一点起伏。
        pendant_settings = getattr(light, "gk_light", None)
        if pendant_settings is not None:
            pendant_settings.projector = "SOFT"
        # 上照:灯罩上方一小盏,只把天花板提亮一点(第一百四十八轮)。
        # ⚠ 它的能量必须**很小**:灯离天花板只有 1 米,平方反比会把它放大
        #   十倍 —— 0.34 倍能量时天花板是一块过曝白斑,连灯罩形状都糊掉了。
        #   0.10 倍 + 射程 5.5 米 = 抬头看是"灯照亮了顶棚",不是"顶棚有个灯"。
        # ── 第一百五十一轮第二轮反馈:从点光改成**朝上的宽角聚光** ────
        # 点光在平顶上必然是一个完美正圆;宽角聚光 + `SOFT` cookie 才能
        # 让光斑的边缘和亮度都"不规整"一点,同时保留"灯罩把顶棚染暖"的
        # 观感。锥角 135°(Godot 半角 67.5°),配 0.35 的软边。
        up = make_aimed_light(f"{name}上照", (x, y, lamp_z + 0.10),
                              (x, y, ceiling_z + 1.0),
                              max(energy * 0.10, 260.0), color, scene,
                              spot_size=135.0, cutoff=5.5, shadow=False,
                              volume=0.6, softness=0.35)
        # 锥边再压软一档(Godot spot_angle_attenuation ≈ 2.9)—— 上照的光斑
        # 不该有一条看得出来的边界。
        up.data.spot_blend = 0.95
        up_settings = getattr(up, "gk_light", None)
        if up_settings is not None:
            up_settings.projector = "SOFT"
    if not on and light is not None:
        light.data.energy = 0.0
    return shade_obj, light


def make_sconce(name, location, scene, energy=3200.0, cutoff=9.0,
                color=(1.0, 0.91, 0.81), glow=0.55, facing=1, axis="y"):
    """壁灯:底座 + 深色金属罩 + 发光片 + 点光。位置 = 灯罩中心。

    `axis="y"` = 装在沿 X 展开的墙上(南 / 北墙),法线是 Y;
    `axis="x"` = 装在沿 Y 展开的墙上(东 / 西墙),法线是 X。
    `facing` = 房间在法线的哪一侧(+1 / -1)。

    ── 第一百四十八轮:灯罩不再是"一块浅色发光板"──────────────────
    以前罩子 albedo 是 (0.92, 0.72, 0.40) + 自发光,离灯 0.28 米 ——
    灯把它照到全通道过 1,再加泛光,墙上就是一团白。现在罩子是**深色
    金属**(0.22),真正发光的是罩子里那片小玻璃(自发光),灯在外面。
    这样"亮"和"发光"分开:亮的是玻璃,暗的是灯罩,形状才读得出来。
    """
    if axis == "y":
        base_size, shade_size = (0.34, 0.14, 0.62), (0.28, 0.24, 0.36)
        glass_size = (0.20, 0.05, 0.26)
        base_loc = (location[0], location[1] - facing * 0.07, location[2])
        shade_loc = (location[0], location[1] + facing * 0.12, location[2])
        glass_loc = (location[0], location[1] + facing * 0.25, location[2])
        # 0.40 -> 0.85(第一百四十八轮第二版):灯离墙只有 0.4 米时,
        # 墙面照度按平方反比冲到 18 倍,实机里是一团白 + 一大圈泛光。
        # 第一百五十一轮:再收到 0.45,让聚光贴着墙面往下洗。
        light_loc = (location[0], location[1] + facing * 0.45,
                     location[2] + 0.10)
        aim_loc = (location[0], location[1] + facing * 0.62,
                   location[2] - 2.4)
    else:
        base_size, shade_size = (0.14, 0.34, 0.62), (0.24, 0.28, 0.36)
        glass_size = (0.05, 0.20, 0.26)
        base_loc = (location[0] - facing * 0.07, location[1], location[2])
        shade_loc = (location[0] + facing * 0.12, location[1], location[2])
        glass_loc = (location[0] + facing * 0.25, location[1], location[2])
        light_loc = (location[0] + facing * 0.45, location[1],
                     location[2] + 0.10)
        aim_loc = (location[0] + facing * 0.62, location[1],
                   location[2] - 2.4)
    base = make_box(f"{name}底座", base_size, base_loc,
                    (0.13, 0.13, 0.13, 1.0), scene=scene)
    shade = make_box(f"{name}灯罩", shade_size, shade_loc,
                     (0.22, 0.20, 0.17, 1.0), scene=scene)
    glass = make_box(f"{name}灯芯", glass_size, glass_loc,
                     (1.0, 0.86, 0.64, 1.0), scene=scene,
                     glow=((1.0, 0.78, 0.48, 1.0), glow))
    # ── 壁灯:贴墙 + 朝下洗墙(第一百五十一轮)────────────────────
    # 用户原话:"灯的光照的范围太圆了"。
    # 以前是离墙 1.15 米的点光,墙上必然是一个**正圆**。现在改成贴墙
    # 0.45 米的聚光,朝下方 2.4 米处照 —— 光顺着墙面往下铺,形状是
    # 竖向椭圆;再挂一张『灯罩竖纹』cookie,把残余的正圆边缘打散。
    # 锥边给 110°,配 0.35 的柔化,避免上一版"硬边椭圆像手电"的问题。
    light = make_aimed_light(name, light_loc, aim_loc, energy * 0.75, color,
                             scene, spot_size=110.0, cutoff=cutoff,
                             volume=1.0, softness=0.35)
    light.data.spot_blend = 0.65
    settings = getattr(light, "gk_light", None)
    if settings is not None:
        settings.override = True
        settings.energy = energy * 0.75 / 1000.0
        settings.range_m = cutoff
        settings.attenuation = 1.15
        settings.shadow = True
        settings.shadow_blur = 2.0
        settings.shadow_opacity = 0.8
        settings.projector = "LAMP"
    for part in (base, shade, glass):
        decor(part)
        no_shadow(part)
    return shade, light


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
    # ── 第 124 轮:9 个高影响键(和 ui._ENV_PRESET_FIELDS 一一对应)──────
    # 漏掉的话,『存为预设』不会把雾亮度 / 泛光门槛这些值存进去 ——
    # 用户切一圈预设回来,自己调过的值就静默回到出厂(和 ui 那边表里
    # "表里少一项值就静默不进去"是同一条坑)。
    ("p_fog_sky_affect", "env_fog_sky_affect"),
    ("p_fog_light_energy", "env_fog_light_energy"),
    ("p_fog_aerial", "env_fog_aerial_perspective"),
    ("p_glow_strength", "env_glow_strength"),
    ("p_glow_threshold", "env_glow_threshold"),
    ("p_glow_blend", "env_glow_blend"),
    ("p_tonemap_exposure", "env_tonemap_exposure"),
    ("p_tonemap_white", "env_tonemap_white"),
    ("p_ambient_sky", "env_ambient_sky_contribution"),
)
_STYLE_TO_PRESET = (
    ("p_style_shadow_step", "style_shadow_step"),
    ("p_style_shadow_step_2", "style_shadow_step_2"),
    ("p_style_shadow_tint", "style_shadow_tint"),
    ("p_style_shadow_tint_2", "style_shadow_tint_2"),
    ("p_style_shadow_smooth", "style_shadow_smooth"),
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


def _build_post_graph(library):
    """在『游戏环境与后期』图里连一条真实的链(天空 / 云 / 天气 / 月亮 + 后期)。

    ── 第一百四十六轮:把新环境系统真正用起来 ─────────────────────────
    上一版这张图只有"默认环境 -> 设置色调 -> 睁眼",而三关的墙全是实心的
    —— 天空盒 / 云 / 月亮这些能力在示例里一个都看不见。这一版先在墙上
    开窗(见 build_corridor / build_ending),再把链补齐:

        『环境_默认环境』
          -> 『环境_天空盒』(深夜蓝,带太阳盘)
          -> 『环境_云层』(乌云,慢慢飘;白天云量自动变多)
          -> 『环境_天气』(阴 —— 乘子在 ENV_* 里已经除回去了)
          -> 『环境_昼夜循环』(定格 0 点;『只改天空』保住场地环境光 / 雾)
          -> 『环境_太阳』(按时刻转;结局场景里那盏『太阳』)
          -> 『环境_月亮』(冷白,满月,投影:窗外的月光真的照进来)
          -> 『环境_设置色调』(带暗角蒙版)
          -> 『环境_睁眼效果』(进关卡自动播)

    ⚠ 这张图是**文件级**的,三关共用;起始时刻 0 点 + 自动流逝关 = 三关
      一进去都定格在午夜(月亮正北 22°)。结局那一关由逻辑图的
      『环境_昼夜交替』接管时间:月亮走一条夜路 → 太阳升起来 → 云变多。
    ⚠ 云的颜色按**白天**调亮:昼夜循环会把云的亮度按 day_amount 压一道,
      夜里乘完 0.45 正好回到原来的"乌云"量级。

    重建要幂等:先把这张图清空(和预设那一列清空同一个道理)。
    """
    library.nodes.clear()
    links = library.links

    start = library.nodes.new("GKNodeEnvDefault")
    start.location = (-1200, 0)

    sky = library.nodes.new("GKNodeSkyBox")
    sky.location = (-960, 0)
    # 顶色不能太黑:天窗是**朝正上方**看的,只会看到 top 色 ——
    # 实机里那块黑洞洞的,像天花板少了一块。
    sky.gk_sky_top = (0.030, 0.040, 0.090, 1.0)
    sky.gk_sky_horizon = (0.070, 0.085, 0.160, 1.0)
    sky.gk_ground_color = (0.020, 0.022, 0.030, 1.0)
    sky.gk_sun_size = 0.06                    # 太阳盘:夜里在地平线以下看不见
    sky.gk_horizon_glow = 0.0
    # 星星(第一百五十一轮第四轮):天空够暗时由着色器自动画出来。
    sky.gk_star_amount = 1.0
    sky.gk_star_brightness = 0.75

    clouds = library.nodes.new("GKNodeClouds")
    clouds.location = (-720, 0)
    # ── 第一百五十一轮第三轮:云层"设计感"──────────────────────────
    # 云量 0.55:云密度是 CPU 烘的贴图,0.55 是"几团云、偶尔遮月";
    # 太少会像几块脏斑,太多会把月盘整个挡住。着色器里还有第二层
    # 更慢的云 + 月亮附近的银边(见 gk_sky.gdshader)。
    clouds.gk_cloud_amount = 0.65
    # 0.012 太慢(实机里几十秒才挪一个云团);0.06 ≈ 3 秒能看出位移。
    clouds.gk_cloud_speed = 0.06
    # 第一百五十一轮第四轮:云密度改成 **CPU 烘的无缝贴图**(避免部分
    # GPU 把逐像素噪声量化成方块);云量 0.55 + 尺度 1.4 = 几团大云。
    clouds.gk_cloud_scale = 1.4
    clouds.gk_cloud_height = 0.05
    clouds.gk_cloud_softness = 0.36
    # ⚠ 昼夜循环会把云的亮度按 `day_amount` 压一道(夜里 ×0.45)。
    #   所以这两个颜色要按**白天**调亮;夜里乘完 0.45 正好回到原来的
    #   "乌云"量级(旧版没有昼夜循环,颜色是按夜里直接给的)。
    clouds.gk_cloud_light = (0.62, 0.70, 0.86, 1.0)
    clouds.gk_cloud_dark = (0.10, 0.12, 0.18, 1.0)
    # 云影(第一百五十轮):云缓缓飘过月面时,窗外月光一暗一明。
    # 0.55 是"看得出来的呼吸感"——再高走廊会变成频闪,再低等于没有。
    clouds.gk_cloud_shadow = 0.55
    # 白天云量增加(第一百五十一轮第六轮):日出之后云自己变多。
    # 0.25 在基础云量 0.65 上再加两成半 —— 实机里是"天亮后天上明显更满"。
    clouds.gk_cloud_day_boost = 0.25

    weather = library.nodes.new("GKNodeWeather")
    weather.location = (-480, 0)
    # 第一百五十四轮:改成**雪**——走廊中段开了天井,雪花从洞口飘进来,
    # 正好同时展示新天气的粒子和星星闪动。嫌太花可以换回 CLOUDY。
    weather.gk_weather = "SNOWY"

    # ── 昼夜循环(第一百五十一轮第六轮)──────────────────────────────
    # 用户:"设计月亮的位置变化,之后是太阳的出现,和云的增多"。
    # 这张卡给整条时间轴:0/5 点夜晚,7 点清晨,12 点白天,18 点黄昏,
    # 22 点回到夜晚。**起始 0 点 + 自动流逝关** —— 大厅 / 走廊里时间
    # 定格在午夜(月亮正北 22°,北窗窗形月光和上一版一致);结局那一关
    # 由逻辑图的『环境_昼夜交替』在章节卡淡出后接管时间。
    # 『只改天空』勾着 = 环境光 / 雾仍用三关手工调好的设置,不被预设覆盖。
    daynight = library.nodes.new("GKNodeDayNight")
    daynight.location = (-240, 0)
    daynight.gk_keyframes = "0=夜晚|5=夜晚|7=清晨|12=白天|18=黄昏|22=夜晚"
    daynight.gk_day_seconds = 180.0
    daynight.gk_start_hour = 0.0
    daynight.gk_auto = False
    daynight.gk_sky_only = True
    daynight.gk_brightness = 1.0

    # ── 太阳(第一百五十一轮第七轮)──────────────────────────────────
    # 只在结局场景里放了同名平行光;太阳走 **东 → 北 → 西** 的正弧线
    # (和月亮同一条弧、相位差 12 小时),6 点东升、12 点正北最高、18 点西落。
    # 正午 22° 而不是更高:窗顶的仰角有限,再高就要贴着窗边才看得到太阳。
    # 夜里关键帧给 0.06 的强度 × 出厂 1.68 —— 天几乎黑着,太阳"还没出来"。
    sun = library.nodes.new("GKNodeSun")
    sun.location = (0, 0)
    sun.gk_sun_name = "太阳"
    sun.gk_sun_orbit = True
    sun.gk_sun_latitude = 22.0
    sun.gk_sun_tilt = 0.0
    sun.gk_sun_scale = 1.0
    sun.gk_sun_shadow = True

    moon = library.nodes.new("GKNodeMoon")
    moon.location = (240, 0)
    moon.gk_moon_enabled = True
    # 第一百五十一轮:原来的 (0.72, 0.80, 1.0) 在泛光下会烧成"白里透蓝"的
    # 大圆盘;改成接近中性冷白,月盘才不会抢过天空。
    moon.gk_moon_color = (0.86, 0.90, 0.98, 1.0)
    # ⚠ 月盘别开大:0.05 + 0.55 能量时,窗外整块被月光晕洗成浅蓝
    #   (实机里像白天)。0.022 是"看得出是月亮、又不盖住夜空"的量级。
    # 0.8:移除 4 盏窗光聚光之后,月光是走廊唯一的"冷光"来源 ——
    # 0.5 的光斑在实机里偏弱,0.8 才在地面铺出看得清的窗形光斑。
    # 第一百五十一轮第三轮:结局的窗外聚光也撤了(和月亮重复、低角度
    # 拉长影子),月光再提一档到 0.95,让窗口的窗形光真的能读出来。
    moon.gk_moon_energy = 0.95
    # ⚠ 月盘大小按**角半径**算(单位弧度),不是旧版的 dot 阈值。
    #   0.06 = 3.4° 半径 / 6.9° 直径;旧版 0.026 实机里是 13° 半径,
    #   月亮占满半个窗户(用户截图)。0.06 在 5 米大窗里是"一轮清楚的月亮"。
    moon.gk_moon_size = 0.06
    moon.gk_moon_phase = 1.0
    # 月相动画(第一百五十一轮):24 秒走完 满月 → 一弯细月 → 满月。
    # 站在窗口/天窗下就能看出月面在慢慢"缺"掉又圆回来。
    moon.gk_moon_phase_animate = True
    moon.gk_moon_phase_seconds = 24.0
    moon.gk_moon_phase_min = 0.18
    moon.gk_moon_shadow = True                # 窗形光斑要靠它投影
    # ── 第一百五十一轮第六轮:随昼夜走一条夜路 ─────────────────────
    # 用户:"设计月亮的位置变化,之后是太阳的出现,和云的增多"。
    # 月亮不再钉死在北窗:18 点东升 → 半夜在正北最高 → 6 点西落。
    # 三关的起始时刻都定在 **0 点**(见下面的『环境_昼夜循环』),所以在
    # 大厅 / 走廊里月亮正好停在"正北 22°"—— 北窗的窗形月光和上一版一致。
    # 结局那一关章节卡淡出后会启动『环境_昼夜交替』,月亮才开始走。
    moon.gk_moon_orbit = True
    # 方位 / 高度:orbit 时只有『高度角』还起作用 = 半夜最高挂多高。
    # 22° 沿用上一版:走廊天花板边缘的仰角上限只有 ~31°,
    # 再高月盘会被天花板挡住。
    moon.gk_moon_azimuth = 0.0
    moon.gk_moon_elevation = 22.0

    grade = library.nodes.new("GKNodeEnvGrade")
    grade.location = (520, 0)
    grade.gk_brightness = 1.04
    grade.gk_contrast = 1.12
    grade.gk_saturation = 1.15
    grade.gk_tint = (1.0, 0.86, 0.72, 1.0)      # 暖橙
    grade.gk_tint_amount = 0.20

    mask = library.nodes.new("GKNodeScreenPos")
    mask.location = (520, -260)
    mask.gk_round = True
    mask.gk_radius = 0.62
    mask.gk_feather = 0.28
    mask.gk_invert = True                        # 反转 = 暗角

    eye = library.nodes.new("GKNodeEyeOpen")
    eye.location = (800, 0)
    # ── 为什么示例选『进关卡自动播』(用户 2026-10-16)──────────────────
    # 卡片的**默认**是『逻辑触发』(用户拍板的设计,只由『后期_播放睁眼』调),
    # 但示例里没有任何节点去调它 —— 于是"图里配了效果、进游戏什么也看不到",
    # 用户的原话就是"游戏环境与后期的节点图逻辑并未在游戏中生效"。
    # 示例是给人看的:改成进关卡自动播,一进大厅就能看到眼皮张开。
    eye.gk_trigger = "AUTO"
    eye.gk_duration = 1.6
    eye.gk_slit = 0.06
    eye.gk_hold = 0.0

    links.new(start.outputs["执行"], sky.inputs["执行"])
    links.new(sky.outputs["完成"], clouds.inputs["执行"])
    links.new(clouds.outputs["完成"], weather.inputs["执行"])
    links.new(weather.outputs["完成"], daynight.inputs["执行"])
    links.new(daynight.outputs["完成"], sun.inputs["执行"])
    links.new(sun.outputs["完成"], moon.inputs["执行"])
    links.new(moon.outputs["完成"], grade.inputs["执行"])
    links.new(grade.outputs["完成"], eye.inputs["执行"])
    links.new(mask.outputs["屏幕位置"], grade.inputs["屏幕位置"])
    log("已连好『游戏环境与后期』图:默认环境 -> 天空盒 -> 云层 -> 天气(阴)"
        " -> 昼夜循环 -> 太阳 -> 月亮 -> 设置色调(暗角蒙版)-> 睁眼")


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
    # ── 第 124 轮:环境光 0.85 -> 0.34(和走廊同一条实测结论)──────────
    # 环境光高于灯 = 画面被一层平光洗白、明暗层次糊掉。大堂有吊灯 + 手电筒,
    # 环境光退到"只负责暗部色相"的位置,明暗交给灯画。
    # 第一百四十八轮:0.34 -> 0.16。灯提亮之后,环境光再压一档,
    # 让"吊灯底下亮、门厅角落暗"真的成立(不然墙始终是一层平光)。
    ("env_ambient_color", (0.54, 0.60, 0.86, 1.0)),
    ("env_ambient_energy", 0.28),
    ("env_fog", True),
    ("env_fog_color", (0.17, 0.20, 0.33, 1.0)),
    # ── 第一百四十六轮:雾浓度按『天气 = 阴』的乘子补偿 ────────────────
    # 环境图一旦挂了天空盒 / 云层 / 天气,导出的环境脚本会在 `_ready` 里
    # 用『天气』的倍数乘一遍雾和泛光(见 asset_export._WEATHERS):
    #   晴 0.35 / 阴 0.90 / 雨 1.80 / 雾 2.60(雾),泛光 1.00/0.75/0.55/0.40。
    # 这一关选『阴』(安静的阴天夜),所以这里先**除回去**:
    #   0.008 ÷ 0.9 ≈ 0.0089,泛光 0.60 ÷ 0.75 = 0.80 ——
    # 运行时看到的还是第 124 轮标定的那套浓度,不会被天气悄悄稀释。
    ("env_fog_density", 0.0045),
    # ── 第 124 轮:雾色**不许**盖掉背景色(显式设 0)──────────────────
    # Godot 出厂值是 1.0:雾色会把背景整个换掉。真机实测(走廊)那一步
    # 让整帧从 0.3803 掉到 0.3460 —— 也就是"取色器上挑的背景色在游戏里
    # 根本看不见",而 Blender 预览(雾是独立体积盒子、不盖世界底色)是看得见的,
    # 这正是"预览和运行对不上"的一半。
    # ⚠ 这个值**必须显式写**:字段默认留 1.0(= Godot 出厂),因为一刀切成 0
    #   会把恐怖示例整帧压暗 70%(见 properties.py 那段说明)。示例是"作者
    #   明确要的观感",所以在这里点明。
    ("env_fog_sky_affect", 0.0),
    ("env_fog_light_energy", 0.45),
    # 高度雾 + 体积雾(第一百四十八轮,插件新能力)
    ("env_fog_height", 0.6),
    ("env_fog_height_density", 0.10),
    # ⚠ 第一百四十八轮:体积雾**关掉**。实机里它在低浓度下会出
    #   "同心圆 / 流光"伪影(Godot 的 froxel + 时域重投影,灯越多越明显),
    #   天花板像被打了一圈极光。窗光靠阴影贴图就有光斑,不需要它。
    #   插件能力保留(环境卡『体积雾』),示例不开。
    ("env_volumetric", False),
    ("env_volumetric_density", 0.010),
    ("env_volumetric_albedo", (0.70, 0.76, 0.95, 1.0)),
    ("env_volumetric_anisotropy", 0.35),
    ("env_volumetric_length", 44.0),
    ("env_ssao", True),
    ("env_ssao_intensity", 4.0),
    ("env_ssao_radius", 1.8),
    ("env_ssao_light_affect", 0.65),
    ("env_glow", True),
    # 0.60 ÷ 0.75(阴天的泛光乘子)= 0.80,见上面『雾浓度』那段说明
    ("env_glow_intensity", 0.80),
    ("env_glow_bloom", 0.08),
    ("env_glow_threshold", 0.90),
    ("env_glow_strength", 0.95),
    # ⚠ LINEAR 不是 ACES:ACES 会把颜色压灰,是"中世纪感"的一半来源。
    ("env_tonemap", "LINEAR"),
    ("env_adjust", True),
    ("env_brightness", 1.06),
    ("env_contrast", 1.08),
    ("env_saturation", 1.18),
)
ENV_CORRIDOR = (
    ("env_enabled", True),
    ("env_background", "COLOR"),
    ("env_bg_color", (0.07, 0.09, 0.17, 1.0)),
    # ── 第 124 轮:环境光 0.78 -> 0.30(用户:"优化运行画面的显示")────────
    # 真机实测(走廊,第一人称实际游玩机位,帧均值亮度):
    #   * 关掉 7 盏导出的壁灯 -> 整帧**一个像素都不变**(gain 0.0000):
    #     环境光 0.78 把灯全压没了,画面是一层平蓝的"洗白"(sd 只有 0.092);
    #   * 环境光 0.78 -> 0 时灯才开始起作用(gain 0.0205),但仍偏弱;
    #   * 降到 0.28 + 灯 10.0 + 射程 28 米 -> 灯 gain 0.164,对比度 sd 0.092 -> 0.163。
    # 二次元要看得出**明暗两段**,而"环境光压过灯"出来的是一段 —— 所以这里
    # 把环境光降到"暗部不死黑(它仍然有色相)"的量级,把明暗交给壁灯去画。
    # ⚠ 不能再往下压:第 123 轮用户的诉求是"暗部要**有颜色的暗**,不是黑",
    #   0.28 是"暗部仍有冷紫色相"的下限(再低墙裙和角落就糊成一团黑)。
    # 第一百四十八轮:0.28 -> 0.13。灯从 2.4 提到 6.0 之后,环境光必须
    # 退到"只给暗部一点色相"的位置 —— 否则墙面被平光照满,光斑和阴影
    # 都会看不见(实机截图里整条走廊是一层均匀的蓝灰)。
    ("env_ambient_color", (0.40, 0.49, 0.88, 1.0)),
    ("env_ambient_energy", 0.30),
    ("env_fog", True),
    ("env_fog_color", (0.12, 0.15, 0.29, 1.0)),
    # 0.014 ÷ 0.9(阴天的雾乘子)= 0.0156,和 ENV_LOBBY 同一个道理
    ("env_fog_density", 0.0042),
    # 雾色不许盖掉背景色(见 ENV_LOBBY 里那段说明:默认留 1.0,Godot 出厂行为)
    ("env_fog_sky_affect", 0.0),
    # 雾亮度 0.55:雾色本身是亮的蓝灰(0.26,0.31,0.52),原样铺过去等于给
    # 整条走廊加一层"牛奶"(实测关雾 0.2551 vs 开雾 0.3547)。压到 0.55 之后
    # 雾还是看得见(远处发白),但不再主导画面亮度。
    ("env_fog_light_energy", 0.34),
    # ── 高度雾 + 体积雾 + SSAO(第一百四十八轮,插件新能力)────────────
    # 高度雾:雾贴着地面 0.5 米最浓,到天花板基本干净 —— "纵向层次"。
    # 体积雾:窗外的聚光 / 吊灯会在空气里打出光柱(需要 Forward+)。
    # SSAO 的『影响直射光』从 0 拧到 0.65:不然墙角 / 柜子根部没有接触阴影。
    ("env_fog_height", 0.5),
    ("env_fog_height_density", 0.12),
    # ⚠ 第一百四十八轮:体积雾**关掉**。实机里它在低浓度下会出
    #   "同心圆 / 流光"伪影(Godot 的 froxel + 时域重投影,灯越多越明显),
    #   天花板像被打了一圈极光。窗光靠阴影贴图就有光斑,不需要它。
    #   插件能力保留(环境卡『体积雾』),示例不开。
    ("env_volumetric", False),
    ("env_volumetric_density", 0.008),
    ("env_volumetric_albedo", (0.56, 0.64, 0.86, 1.0)),
    ("env_volumetric_anisotropy", 0.30),
    ("env_volumetric_length", 56.0),
    ("env_ssao", True),
    ("env_ssao_intensity", 4.0),
    ("env_ssao_radius", 1.8),
    ("env_ssao_light_affect", 0.65),
    ("env_glow", True),
    # 0.95 ÷ 0.75 = 1.27(阴天的泛光乘子)
    ("env_glow_intensity", 1.27),
    ("env_glow_bloom", 0.14),
    # 泛光的真扳机是门槛 + 力度,不是 intensity:实测门槛停在 Godot 出厂值
    # 1.0 时,开/关泛光整帧只差 0.0044(等于没有);打到 0.85 + 力度 1.6
    # 之后壁灯 / 灯罩真的"毛"起来(这才是恐怖走廊要的那点光晕)。
    ("env_glow_threshold", 1.0),
    ("env_glow_strength", 1.05),
    ("env_tonemap", "LINEAR"),
    ("env_adjust", True),
    ("env_brightness", 1.02),
    ("env_contrast", 1.12),
    # 1.18 在实机里把暖灯的橙推得太艳(整条走廊发橙);1.08 保住卡通的高饱和,
    # 又不至于变成"橙色隧道"
    ("env_saturation", 1.08),
)
ENV_ENDING = (
    ("env_enabled", True),
    ("env_background", "COLOR"),
    ("env_bg_color", (0.10, 0.10, 0.20, 1.0)),
    # 同上:环境光 0.80 -> 0.32,把明暗让给结局灯(小房间,一盏灯铺得满)
    ("env_ambient_color", (0.48, 0.54, 0.88, 1.0)),
    ("env_ambient_energy", 0.30),
    ("env_fog", True),
    ("env_fog_color", (0.14, 0.15, 0.30, 1.0)),
    # 0.010 ÷ 0.9 = 0.0111(阴天的雾乘子)
    ("env_fog_density", 0.0055),
    ("env_fog_sky_affect", 0.0),
    ("env_fog_light_energy", 0.34),
    # ── 高度雾 + 体积雾 + SSAO(第一百四十八轮,插件新能力)────────────
    # 高度雾:雾贴着地面 0.5 米最浓,到天花板基本干净 —— "纵向层次"。
    # 体积雾:窗外的聚光 / 吊灯会在空气里打出光柱(需要 Forward+)。
    # SSAO 的『影响直射光』从 0 拧到 0.65:不然墙角 / 柜子根部没有接触阴影。
    ("env_fog_height", 0.5),
    ("env_fog_height_density", 0.12),
    # ⚠ 第一百四十八轮:体积雾**关掉**。实机里它在低浓度下会出
    #   "同心圆 / 流光"伪影(Godot 的 froxel + 时域重投影,灯越多越明显),
    #   天花板像被打了一圈极光。窗光靠阴影贴图就有光斑,不需要它。
    #   插件能力保留(环境卡『体积雾』),示例不开。
    ("env_volumetric", False),
    ("env_volumetric_density", 0.008),
    ("env_volumetric_albedo", (0.56, 0.64, 0.86, 1.0)),
    ("env_volumetric_anisotropy", 0.30),
    ("env_volumetric_length", 56.0),
    ("env_ssao", True),
    ("env_ssao_intensity", 4.0),
    ("env_ssao_radius", 1.8),
    ("env_ssao_light_affect", 0.65),
    ("env_glow", True),
    # 0.80 ÷ 0.75 = 1.07
    ("env_glow_intensity", 1.07),
    ("env_glow_bloom", 0.12),
    ("env_glow_threshold", 1.0),
    ("env_glow_strength", 0.95),
    ("env_tonemap", "LINEAR"),
    ("env_adjust", True),
    ("env_brightness", 1.05),
    ("env_contrast", 1.09),
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
    # 0.12 几乎是"全亮"(分界贴着轮廓);0.30 才在墙面 / 家具侧面留下
    # 真正的暗面。配合下面的『分界柔和』0.02 = 硬边赛璐璐。
    # ── 第一百五十一轮:三段阴影(用户"太单调")────────────────────
    # 亮部 -> 浅阴影(shadow_tint)-> 深阴影(shadow_tint_2)。
    # 只有 `three_tone > 0.5` 才会启用第三档,所以这里给 0.85。
    ("style_shadow_step", 0.20),
    # 0.06 -> 0.18(第一百五十一轮第二轮反馈):明暗分界要"缓和",
    # 不能像刻出来的一条线。0.18 仍是两段色阶,但边界是渐变的。
    ("style_shadow_smooth", 0.18),
    ("style_shadow_tint", (0.62, 0.64, 0.80, 1.0)),
    ("style_shadow_tint_2", (0.30, 0.31, 0.48, 1.0)),
    ("style_shadow_step_2", -0.15),
    ("style_three_tone", 0.85),
    ("style_rim", 0.0),
    ("style_specular", 0.0),
)
STYLE_CORRIDOR = (
    ("style_enabled", True),
    ("style_shadow_step", 0.22),
    ("style_shadow_smooth", 0.18),
    ("style_shadow_tint", (0.58, 0.60, 0.76, 1.0)),
    ("style_shadow_tint_2", (0.26, 0.27, 0.42, 1.0)),
    ("style_shadow_step_2", -0.18),
    ("style_three_tone", 0.85),
    ("style_rim", 0.0),
    ("style_specular", 0.0),
)
STYLE_ENDING = (
    ("style_enabled", True),
    ("style_shadow_step", 0.22),
    ("style_shadow_smooth", 0.18),
    ("style_shadow_tint", (0.60, 0.62, 0.78, 1.0)),
    ("style_shadow_tint_2", (0.30, 0.31, 0.46, 1.0)),
    ("style_shadow_step_2", -0.16),
    ("style_three_tone", 0.90),
    ("style_rim", 0.0),
    ("style_specular", 0.0),
)


# ==========================================================================
# 三关:物体
# ==========================================================================
def build_guard_room(scene, floor_color, wall_color, trim_color):
    """大堂东侧的**门卫室**(6.4 × 7.0 × 3.4):关卡区域扩展 + 钥匙所在地。

    ── 第一百五十六轮:为什么把钥匙搬进这里 ───────────────────────
    以前钥匙就悬在大堂右侧半空,玩家进门第一眼看到、走过去就完事 ——
    整个第一关只有 12 秒。搬进侧屋之后,路线变成:
        进大堂 -> 发现大门锁着 -> 看见东墙有个门洞 -> 进值班室 -> 拿钥匙
    侧屋本身也承担叙事:监控还亮着(说明有人刚离开)、值班表、挂钟、
    台灯 —— 让"这里发生过什么"有痕迹。

    ⚠ 门洞开在东墙上(x=15),房间在 x 15.2~21.6;大堂的天花板/地面
      都只到 x=15,所以侧屋必须自带地面和天花板,不然抬头是天空。
    """
    x0, x1 = 15.2, 21.6
    y0, y1 = -2.8, 4.2
    h = 3.4
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    w, d = x1 - x0, y1 - y0
    solid_box(make_box(f"{GUARD_ROOM}地面", (w + 0.8, d + 0.8, 0.4),
                       (cx, cy, -0.2), floor_color, scene=scene))
    solid_box(make_box(f"{GUARD_ROOM}天花板", (w + 0.8, d + 0.8, 0.3),
                       (cx, cy, h + 0.15), (0.18, 0.19, 0.22, 1.0),
                       scene=scene))
    solid_box(make_box(f"{GUARD_ROOM}东墙", (0.5, d + 0.8, h),
                       (x1 + 0.25, cy, h / 2), wall_color, scene=scene))
    solid_box(make_box(f"{GUARD_ROOM}北墙", (w + 0.8, 0.5, h),
                       (cx, y1 + 0.25, h / 2), wall_color, scene=scene))
    solid_box(make_box(f"{GUARD_ROOM}南墙", (w + 0.8, 0.5, h),
                       (cx, y0 - 0.25, h / 2), wall_color, scene=scene))

    # ---- 门洞门套 + 自发光门牌(在**大堂这一侧**,给玩家方向)----
    for y in (-0.42, 2.42):
        decor(make_box(f"{GUARD_ROOM}门柱{y:+.2f}", (0.72, 0.20, 2.86),
                       (15.0, y, 1.43), trim_color, scene=scene))
    decor(make_box(f"{GUARD_ROOM}门楣", (0.72, 3.04, 0.22),
                   (15.0, 1.0, 2.84), trim_color, scene=scene))
    decor(make_box(f"{GUARD_ROOM}门牌", (0.06, 1.5, 0.32),
                   (14.70, 1.0, 2.52), (0.16, 0.22, 0.18, 1.0),
                   scene=scene, glow=((0.45, 1.0, 0.62, 1.0), 1.1)))

    # ---- 监控台(房间主视觉)+ 三块屏幕 ----
    solid_box(make_box(f"{GUARD_ROOM}监控台", (1.5, 3.4, 0.88),
                       (20.55, 0.9, 0.44), (0.26, 0.24, 0.22, 1.0),
                       scene=scene))
    decor(make_box(f"{GUARD_ROOM}台面", (1.7, 3.6, 0.08),
                   (20.55, 0.9, 0.92), (0.14, 0.13, 0.12, 1.0),
                   scene=scene))
    for index, y in enumerate((-0.10, 0.90, 1.90)):
        decor(make_box(f"{GUARD_ROOM}屏幕{index + 1}", (0.10, 0.80, 0.54),
                       (20.98, y, 1.30), (0.08, 0.09, 0.10, 1.0),
                       scene=scene))
        decor(make_box(f"{GUARD_ROOM}屏幕{index + 1}面", (0.04, 0.66, 0.40),
                       (20.91, y, 1.30), (0.22, 0.42, 0.52, 1.0),
                       scene=scene, glow=((0.36, 0.74, 0.96, 1.0), 1.01)))
    # 屏幕自己的一小片冷光(照亮台面和操作员的脸 / 后背)
    screen_light = make_light(GUARD_SCREEN_LIGHT, "POINT",
                              (20.1, 0.9, 1.55), 700.0,
                              (0.42, 0.72, 1.0), scene, cutoff=4.2,
                              shadow=False)
    screen_light.data.shadow_soft_size = 0.30

    # ---- 椅子(坐着的人刚离开)+ 台灯 + 值班表 + 挂钟 ----
    solid_box(make_box(f"{GUARD_ROOM}椅子", (0.70, 0.70, 0.12),
                       (19.35, 0.9, 0.52), (0.15, 0.13, 0.12, 1.0),
                       scene=scene))
    decor(make_box(f"{GUARD_ROOM}椅背", (0.12, 0.70, 0.66),
                   (19.00, 0.9, 0.90), (0.13, 0.11, 0.10, 1.0),
                   scene=scene))
    for dx in (-0.28, 0.28):
        for dy in (-0.28, 0.28):
            decor(make_box(f"{GUARD_ROOM}椅腿{dx:+.2f}{dy:+.2f}",
                           (0.08, 0.08, 0.46),
                           (19.35 + dx, 0.9 + dy, 0.23),
                           (0.10, 0.09, 0.08, 1.0), scene=scene))
    # 台灯:底座 + 杆 + 罩 + 一点暖光(和屏幕的冷光形成对比)
    decor(make_box(f"{GUARD_ROOM}台灯座", (0.30, 0.30, 0.07),
                   (20.30, -0.62, 1.00), (0.14, 0.13, 0.12, 1.0),
                   scene=scene))
    decor(make_box(f"{GUARD_ROOM}台灯杆", (0.06, 0.06, 0.46),
                   (20.30, -0.62, 1.26), (0.18, 0.17, 0.15, 1.0),
                   scene=scene))
    decor(make_box(f"{GUARD_ROOM}台灯罩", (0.42, 0.42, 0.22),
                   (20.30, -0.62, 1.52), (0.30, 0.24, 0.16, 1.0),
                   scene=scene, glow=((1.0, 0.78, 0.45, 1.0), 0.8)))
    make_light(f"{GUARD_ROOM}台灯光", "POINT", (20.22, -0.62, 1.44),
               900.0, (1.0, 0.80, 0.52), scene, cutoff=4.6, shadow=False)
    # 值班表:一块软木板 + 几张纸
    decor(make_box(f"{GUARD_ROOM}值班表", (1.35, 0.07, 0.95),
                   (18.20, y1 - 0.10, 1.95), (0.36, 0.28, 0.18, 1.0),
                   scene=scene))
    for index, (dx, dy, rot) in enumerate(((-0.32, 0.16, 0.04),
                                           (0.24, 0.10, -0.05),
                                           (-0.04, -0.22, 0.02))):
        sheet = make_box(f"{GUARD_ROOM}值班纸{index + 1}",
                         (0.34, 0.02, 0.44),
                         (18.20 + dx, y1 - 0.16, 1.95 + dy),
                         (0.72, 0.70, 0.62, 1.0), scene=scene)
        sheet.rotation_euler = (0.0, rot, 0.0)
        decor(sheet)
    # 挂钟:钟面 + 两根指针(都贴在南墙上,纯装饰)
    decor(make_box(f"{GUARD_ROOM}挂钟", (0.62, 0.08, 0.62),
                   (19.80, y0 + 0.06, 2.35), (0.62, 0.60, 0.54, 1.0),
                   scene=scene))
    decor(make_box(f"{GUARD_ROOM}时针", (0.05, 0.03, 0.20),
                   (19.80, y0 + 0.11, 2.42), (0.08, 0.07, 0.07, 1.0),
                   scene=scene))
    decor(make_box(f"{GUARD_ROOM}分针", (0.18, 0.03, 0.04),
                   (19.88, y0 + 0.11, 2.35), (0.08, 0.07, 0.07, 1.0),
                   scene=scene))

    # ---- 地面杂物:纸箱 + 散落的纸 ----
    for index, (x, y, size) in enumerate(((16.4, -2.0, 0.9),
                                           (17.2, -2.2, 0.7))):
        solid_box(make_box(f"{GUARD_ROOM}纸箱{index + 1}",
                           (size, size, size), (x, y, size / 2),
                           (0.40, 0.30, 0.18, 1.0), scene=scene))
    for index, (x, y, rot) in enumerate(((17.0, 0.6, 0.4),
                                          (17.6, 0.2, -0.7))):
        sheet = make_box(f"{GUARD_ROOM}散纸{index + 1}",
                         (0.5, 0.36, 0.012), (x, y, 0.02),
                         (0.58, 0.56, 0.48, 1.0), scene=scene)
        sheet.rotation_euler = (0.0, 0.0, rot)
        decor(sheet)

    # ---- 顶灯(房间自己的光源,不靠大堂的吊灯)----
    make_pendant(GUARD_LAMP, 18.1, h, scene, energy=2400.0, cutoff=8.0,
                 y=0.9)

    # ---- 两个触发区:进门口 / 走到监控台前 ----
    # ⚠ 触发区的网格**会被导出并渲染**:2.6×3.2 的浅色薄片铺在地上,
    #   实机里就是一块发白的地板(第一版就是这样)。判定位不需要被看见 ——
    #   压到近黑就够了。
    # ⚠ **不能** decor():decor 会把 collision_shape 设成 none,导出时
    #   这具物体就不再是 Area3D 了 —— 生成的脚本里
    #   `var area_门卫室入口: Area3D = ...` 会拿到一个 MeshInstance3D,
    #   Godot 直接报 "Trying to assign value of type MeshInstance3D to a
    #   variable of type Area3D",整个大堂脚本加载失败(实机踩到)。
    #   颜色压到近黑就够了:判定位看不见,但仍然是 Area3D。
    hide_mesh(make_box(GUARD_DOOR_AREA, (1.2, 2.8, 2.2), (15.0, 1.0, 1.1),
                       (0.10, 0.12, 0.12, 1.0), scene=scene))
    hide_mesh(make_box(GUARD_MONITOR_AREA, (2.6, 3.2, 0.2), (19.6, 0.9, 0.1),
                       (0.10, 0.12, 0.12, 1.0), scene=scene))
    log(f"{GUARD_ROOM}:6.4×7.0 + 监控台/三屏 + 椅子/台灯/值班表/挂钟 + "
        f"{GUARD_LAMP} + 两个触发区")


def build_lobby(scene):
    """第一关 大堂:门厅(地面/四壁/柱列/天窗/吊灯/家具)+ 钥匙 + 手电 + 大门。

    ── 第一百四十六轮:空间重塑 ────────────────────────────────────
    上一版的大堂是"一个空盒子 + 六个发光方块":墙、地、柱、灯都有,
    但没有**尺度参照**(家具 / 门套 / 天窗),所以读起来像测试场景,
    不像有人住过的地方。这一版加的是"能让眼睛读懂空间"的东西:

      * **天窗**:天花板中间开一个 8×6 的洞,月光真的会洒进来
        (天空盒 + 月亮平行光从洞口打进来,地面上一块冷色光斑);
      * **吊灯**:吊杆 + 灯罩 + 一盏点光,是房间的主光(以前只有一盏
        悬空的点光,没有灯具,光像是凭空来的);
      * **门套**:大门两侧加门柱和门楣,门才"长在墙上";
      * **家具**:接待台 / 长椅 / 木箱 / 地毯 —— 也给玩家一个尺度参照;
      * **柱头 / 柱基**:三根柱子不再是三根等粗的长条。

    ⚠ 装饰件全部走 `decor()`(不生成碰撞):窗棂 / 吊杆 / 地毯这种
      细长件在"自动碰撞"下会被猜成几米半径的胶囊,直接把路堵死。

    ⚠ 场景里**不能只有角色一个网格**:整场景只有一个网格时插件不生成主
      场景(踩过)—— 门厅的地面/墙/柱子既是空间感也是"有主场景"的保证。
    """
    width, depth, height = ROOM[LOBBY]
    half_w, half_d = width / 2.0, depth / 2.0
    # 第一百四十八轮:整体压暗一档 —— 原来的墙 (0.52) 在"灯只管明暗"的
    # 布光下会整面反光,画面发灰;0.30 的暖灰石材 + 冷月光才有对比。
    floor_color = (0.24, 0.23, 0.24, 1.0)
    wall_color = (0.36, 0.33, 0.30, 1.0)
    trim_color = (0.12, 0.11, 0.11, 1.0)

    make_box("大堂地面", (width, depth, 0.4), (0.0, 0.0, -0.2),
             floor_color, scene=scene)
    make_box("大堂北墙", (width, 0.5, height), (0.0, half_d, height / 2),
             wall_color, scene=scene)
    make_box("大堂南墙", (width, 0.5, height), (0.0, -half_d, height / 2),
             wall_color, scene=scene)
    # ── 东墙:中间开一个 2.8 米宽的门洞通向门卫室(第一百五十六轮)──────
    # 和走廊南墙同一个做法:左段 + 右段 + 门楣,洞高 2.7。
    guard_lo, guard_hi, guard_top = -0.4, 2.4, 2.7
    make_box("大堂东墙南", (0.5, guard_lo + half_d, height),
             (half_w, (-half_d + guard_lo) / 2, height / 2),
             wall_color, scene=scene)
    make_box("大堂东墙北", (0.5, half_d - guard_hi, height),
             (half_w, (guard_hi + half_d) / 2, height / 2),
             wall_color, scene=scene)
    make_box("大堂东墙门楣", (0.5, guard_hi - guard_lo, height - guard_top),
             (half_w, (guard_lo + guard_hi) / 2,
              (guard_top + height) / 2), wall_color, scene=scene)
    make_box("大堂西墙", (0.5, depth, height), (-half_w, 0.0, height / 2),
             wall_color, scene=scene)

    build_guard_room(scene, floor_color, wall_color, trim_color)

    # ---- 墙面分件:壁柱 + 护墙板(第一百五十六轮)--------------------
    # 大堂 30×24 米,四壁以前是四块**纯色大板**,灯一打就是一片平光。
    # 壁柱每 6 米一道(进深 0.28 米),1.05 米高处一道护墙板 ——
    # 墙就有了尺度,灯也能在柱子上留下明暗边。
    # 全部走 decor:它们贴着墙,不能让角色被 20 厘米的凸起卡住。
    for x in (-12.0, -6.0, 6.0, 12.0):
        for side in (-1, 1):
            decor(make_box(f"大堂壁柱{x:+.0f}{'南北'[side > 0]}",
                           (0.5, 0.26, height - 0.6),
                           (x, side * (half_d - 0.30), (height - 0.6) / 2),
                           trim_color, scene=scene))
    for y in (-8.0, -3.5, 7.5):
        for side in (-1, 1):
            decor(make_box(f"大堂壁柱东{y:+.1f}{'西' if side < 0 else ''}",
                           (0.26, 0.5, height - 0.6),
                           (side * (half_w - 0.30), y, (height - 0.6) / 2),
                           trim_color, scene=scene))
    for side in (-1, 1):
        decor(make_box(f"大堂护墙板{'南北'[side > 0]}",
                       (width - 0.8, 0.10, 0.9),
                       (0.0, side * (half_d - 0.06), 0.75),
                       (0.16, 0.15, 0.13, 1.0), scene=scene))
    decor(make_box("大堂护墙板东", (0.10, 12.0, 0.9),
                   (half_w - 0.06, -7.4, 0.75),
                   (0.16, 0.15, 0.13, 1.0), scene=scene))
    decor(make_box("大堂护墙板西", (0.10, depth - 0.8, 0.9),
                   (-half_w + 0.06, 0.0, 0.75),
                   (0.16, 0.15, 0.13, 1.0), scene=scene))

    # ---- 柱子:柱身 + 柱头 + 柱基(柱头/柱基纯装饰,不额外挡路)----
    # ⚠ 别再放"正中一根":大门在 (0, 11.6),正中的柱子会把它整块挡住
    #   (实机截图里进门只看见一根柱子的背面)。两根一对、让出中央轴线。
    for index, (x, y) in enumerate(((-9.5, 3.0), (-3.5, 3.0),
                                    (3.5, 3.0), (9.5, 3.0))):
        make_box(f"大堂柱{index + 1}", (1.3, 1.3, height), (x, y, height / 2),
                 (0.58, 0.60, 0.68, 1.0), scene=scene)
        decor(make_box(f"大堂柱{index + 1}柱头", (1.8, 1.8, 0.34),
                       (x, y, height - 0.30), trim_color, scene=scene))
        decor(make_box(f"大堂柱{index + 1}柱基", (1.7, 1.7, 0.40),
                       (x, y, 0.20), trim_color, scene=scene))

    # ---- 天花板:中间留一个天窗洞(四块板拼)----
    # 洞口 8(X) × 6(Y),中心 (0, 4)。板厚 0.3,顶在 z = height + 0.15。
    ceiling_z = height + 0.15
    ceiling_color = (0.20, 0.21, 0.25, 1.0)
    skylight = (8.0, 6.0, 4.0)              # 宽 / 深 / 中心 y
    half_open_x, half_open_y = skylight[0] / 2, skylight[1] / 2
    cy = skylight[2]
    panels = (
        ("大堂天花板", (width, (cy - half_open_y) + half_d,
                        (cy - half_open_y - half_d) / 2)),
        ("大堂天花板北", (width, half_d - (cy + half_open_y),
                          (half_d + cy + half_open_y) / 2)),
        ("大堂天花板西", (half_w - half_open_x, skylight[1], -(half_w + half_open_x) / 2)),
        ("大堂天花板东", (half_w - half_open_x, skylight[1], (half_w + half_open_x) / 2)),
    )
    for name, (size_x, size_y, center) in panels:
        if name.endswith(("西", "东")):
            panel = make_box(name, (size_x, size_y, 0.3),
                             (center, cy, ceiling_z), ceiling_color, scene=scene)
        else:
            panel = make_box(name, (size_x, size_y, 0.3),
                             (0.0, center, ceiling_z), ceiling_color, scene=scene)
        solid_box(panel)
    # 天窗边框(装饰)
    for name, size, loc in (
        ("大堂天窗框西", (0.22, skylight[1], 0.45),
         (-half_open_x, cy, height - 0.1)),
        ("大堂天窗框东", (0.22, skylight[1], 0.45),
         (half_open_x, cy, height - 0.1)),
        ("大堂天窗框南", (skylight[0], 0.22, 0.45),
         (0.0, cy - half_open_y, height - 0.1)),
        ("大堂天窗框北", (skylight[0], 0.22, 0.45),
         (0.0, cy + half_open_y, height - 0.1)),
    ):
        decor(make_box(name, size, loc, trim_color, scene=scene))
    for offset in (-2.0, 2.0):
        decor(make_box(f"大堂天窗棂{offset:+.0f}", (0.10, skylight[1], 0.14),
                       (offset, cy, height - 0.02), trim_color, scene=scene))
    # ── 天窗光(第一百四十八轮)────────────────────────────────────
    # 和走廊窗光同一个做法:灯在**屋顶上方**,天花板四块板把光挡掉,
    # 只有 8×6 的洞漏下来 —— 地面正中一块冷色光斑 + 柱子的长影。
    sky_light = make_aimed_light(
        "大堂天窗光", (0.0, cy, 11.0), (0.0, cy, 0.0),
        # 8000W 的 104° 聚光在地面上是一个**巨大的正圆白斑**,用户看到的
        # "月亮颜色有问题"其实就是它。现在降能量、收色相,再挂矩形 cookie:
        # 光斑变成天窗形状的柔边矩形,而不是一轮月亮。
        4200.0, (0.66, 0.74, 0.94), scene,
        spot_size=88.0, cutoff=20.0, volume=0.9, softness=0.30)
    sky_light.data.spot_blend = 0.6
    sky_settings = getattr(sky_light, "gk_light", None)
    if sky_settings is not None:
        sky_settings.override = True
        sky_settings.energy = 4.2
        sky_settings.range_m = 20.0
        sky_settings.attenuation = 1.0
        sky_settings.shadow = True
        sky_settings.shadow_blur = 3.5
        sky_settings.shadow_opacity = 0.85
        sky_settings.projector = "RECT"

    # ---- 大门:门板 + 门套(门柱 / 门楣)+ 门口触发区 ----
    door_x = 0.0
    door_panel = make_box(DOOR_PANEL, (4.0, 0.7, 4.6),
                          (door_x, half_d - 0.4, DOOR_Z_DOWN),
                          (0.52, 0.42, 0.28, 1.0), scene=scene)
    # 门缝 + 门把手 + 门牌:一块光板看不出是门(实机截图里像一面墙)
    door_parts = [
        decor(make_box("大门门缝", (0.05, 0.04, 4.2), (door_x, half_d - 0.78,
                                                      DOOR_Z_DOWN),
                       (0.16, 0.13, 0.10, 1.0), scene=scene)),
    ]
    # 门板上的两条凹槽:把 4 米宽的门分成三块,读得出"这是双开门"
    for offset in (-1.25, 1.25):
        door_parts.append(decor(make_box(
            f"大门凹槽{offset:+.2f}", (0.06, 0.05, 3.6),
            (door_x + offset, half_d - 0.78, DOOR_Z_DOWN),
            (0.30, 0.24, 0.16, 1.0), scene=scene)))
    for side in (-1, 1):
        door_parts.append(decor(make_box(
            f"大门把手{'左右'[side > 0]}", (0.12, 0.16, 0.42),
            (door_x + side * 0.55, half_d - 0.80, 2.2),
            (0.80, 0.68, 0.36, 1.0), scene=scene)))
    door_parts.append(decor(make_box(
        "大门门牌", (1.5, 0.05, 0.34), (door_x, half_d - 0.80, 4.0),
        (0.30, 0.34, 0.24, 1.0), scene=scene)))
    # ── 门锁 / 把手 / 门缝 / 门牌必须是门板的**子物体**(第一百五十轮)──
    # 用户原话:"'门'的打开动画,门锁之类的并未跟随其位置变化而变化。"
    # 这扇门的静态资产只导了门板本身,子件留在世界 glb 里 —— 门板升起,
    # 它们原地不动。挂成子物体后:整棵子树归资产(见 asset_tree_objects),
    # 移动门板时门缝 / 凹槽 / 把手 / 门牌一起走。
    for part in door_parts:
        part.parent = door_panel
        part.matrix_parent_inverse = door_panel.matrix_world.inverted()
    for side in (-1, 1):
        decor(make_box(f"大堂门套{'左右'[side > 0]}", (0.55, 0.9, 5.2),
                       (door_x + side * 2.35, half_d - 0.55, 2.6),
                       trim_color, scene=scene))
    decor(make_box("大堂门楣", (5.25, 0.9, 0.65),
                   (door_x, half_d - 0.55, 5.15), trim_color, scene=scene))
    hide_mesh(make_box(DOOR_AREA, (5.0, 3.0, 0.2), (0.0, 10.0, 0.1),
                       (0.12, 0.18, 0.16, 1.0), scene=scene))
    # 门两侧的壁灯:告诉玩家"这里是要去的地方"
    for side in (-1, 1):
        make_sconce(f"大堂门灯{'左右'[side > 0]}",
                    (door_x + side * 3.5, half_d - 0.45, 3.0), scene,
                    energy=4200.0, cutoff=10.0, facing=-1)

    # ---- 家具 / 地毯:尺度参照 + 视觉纵深 ----
    # 地毯:0.02 厚的薄片,装饰件(不能有碰撞,否则角色会被 2 厘米绊住)
    decor(make_box("大堂地毯", (7.0, 16.0, 0.03), (0.0, 0.0, 0.015),
                   (0.07, 0.022, 0.030, 1.0), scene=scene))
    # 接待台:台身 + 台面
    solid_box(make_box("大堂接待台", (4.2, 1.2, 1.05), (-8.6, -3.2, 0.525),
                       (0.42, 0.34, 0.26, 1.0), scene=scene))
    decor(make_box("大堂接待台面", (4.6, 1.5, 0.12), (-8.6, -3.2, 1.11),
                   (0.58, 0.48, 0.34, 1.0), scene=scene))
    # 长椅:坐面 + 靠背 + 两条腿
    solid_box(make_box("大堂长椅", (3.2, 0.85, 0.18), (9.0, -1.5, 0.46),
                       (0.40, 0.30, 0.22, 1.0), scene=scene))
    decor(make_box("大堂长椅靠背", (3.2, 0.16, 0.55), (9.0, -1.9, 0.80),
                   (0.38, 0.28, 0.20, 1.0), scene=scene))
    for side in (-1, 1):
        decor(make_box(f"大堂长椅腿{'左右'[side > 0]}", (0.18, 0.7, 0.38),
                       (9.0 + side * 1.3, -1.5, 0.19),
                       (0.24, 0.20, 0.16, 1.0), scene=scene))
    # 三个木箱:堆在角落,给"这里搬过东西"的暗示
    for index, (x, y, z, size) in enumerate((
            (-13.0, 7.5, 0.45, 0.9), (-12.2, 8.3, 0.35, 0.7),
            (13.2, -7.4, 0.50, 1.0))):
        solid_box(make_box(f"大堂木箱{index + 1}", (size, size, size),
                           (x, y, z), (0.44, 0.32, 0.20, 1.0), scene=scene))
    # 一摞纸 / 掉落的文件:地面上几片薄片(装饰)
    for index, (x, y, rot) in enumerate(((-6.8, -1.2, 0.3), (-6.2, -1.6, -0.5))):
        sheet = make_box(f"大堂文件{index + 1}", (0.5, 0.36, 0.012),
                         (x, y, 0.02), (0.56, 0.54, 0.47, 1.0), scene=scene)
        sheet.rotation_euler = (0.0, 0.0, rot)
        decor(sheet)

    # ---- 大堂中央的视觉焦点:铜饰地雕 + 落地钟(第一百五十七轮)--------
    # 用户原话:"大堂中央补视觉焦点"。
    # 之前的大堂中央是**空的**:玩家从 (0,-8) 出生往北看,一条 7 米宽的地毯
    # 通到底,尽头只有一块 4 米宽的门板 —— 中间 15 米没有任何东西可看,
    # 空间读起来是"一条走廊 + 一扇门",不是"一个厅"。
    # 补两样:
    #   ① **铜饰地雕**:地毯正中一个 2.4 米的圆盘(锥形当圆盘用),
    #      给"中央"一个明确的圆心;
    #   ② **落地钟**:2.3 米高的老式立钟,自带一盏小暖光。它是"有人在这
    #      住过 / 时间停在某处"的叙事道具,也是玩家一进门的正前方焦点。
    # ⚠ 钟体是 0.9 × 0.5,自动碰撞猜成胶囊的半径只有 0.45 —— 可以挡人
    #   (实心);钟面 / 指针 / 摆锤一律 decor,别让细长件变成几米的大胶囊。
    decor(make_cone("大堂地雕", 2.4, 2.4, 0.04, (0.0, 0.0, 0.05),
                    (0.42, 0.32, 0.16, 1.0), scene=scene, segments=32))
    solid_box(make_box("大堂落地钟", (0.90, 0.50, 2.30), (0.0, 0.0, 1.15),
                       (0.30, 0.20, 0.12, 1.0), scene=scene))
    decor(make_box("大堂落地钟顶", (1.05, 0.62, 0.30), (0.0, 0.0, 2.44),
                   (0.34, 0.23, 0.14, 1.0), scene=scene))
    decor(make_box("大堂落地钟面", (0.62, 0.04, 0.62), (0.0, -0.27, 1.92),
                   (0.88, 0.84, 0.72, 1.0), scene=scene,
                   glow=((1.0, 0.94, 0.78, 1.0), 0.22)))
    decor(make_box("大堂落地钟摆", (0.10, 0.03, 0.86), (0.0, -0.24, 1.34),
                   (0.72, 0.60, 0.32, 1.0), scene=scene))
    decor(make_box("大堂落地钟砣", (0.16, 0.06, 0.16), (0.0, -0.25, 0.92),
                   (0.78, 0.66, 0.34, 1.0), scene=scene))
    # 钟自己的一小片暖光:让它在暗厅里"自己亮着"(比被吊灯照到更抓眼)
    clock_light = make_light("大堂落地钟光", "POINT", (0.0, -0.6, 1.95),
                             520.0, (1.0, 0.82, 0.55), scene, cutoff=3.2,
                             shadow=False)
    clock_light.data.shadow_soft_size = 0.22

    # ---- 监控摄像头:门卫室那块屏的画面就是从这里来的(第一百五十七轮)--
    # 装在天花板下、朝向大门(门后就是走廊)。它同时是"机位的可视化":
    # 逻辑图里『显示_监控画面』的『机位』填的就是这个位置。
    cx, cy, cz = CCTV_POS
    decor(make_box(f"{GUARD_CCTV}杆", (0.08, 0.08, 1.2),
                   (cx, cy, cz + 0.6), trim_color, scene=scene))
    decor(make_box(GUARD_CCTV, (0.34, 0.22, 0.20), (cx, cy, cz),
                   (0.16, 0.16, 0.17, 1.0), scene=scene))
    decor(make_box(f"{GUARD_CCTV}镜头", (0.14, 0.06, 0.14),
                   (cx, cy - 0.13, cz - 0.02), (0.05, 0.05, 0.06, 1.0),
                   scene=scene, glow=((1.0, 0.22, 0.20, 1.0), 0.9)))

    # ---- 灯:吊灯(主光)+ 四盏壁灯(边光)----
    pendant_shade, pendant_light = make_pendant(
        "大堂吊灯", 0.0, height, scene, energy=8000.0, cutoff=26.0)
    # ── 漫射衰减 / 柔和阴影(第一百五十轮)──────────────────────────
    # 吊灯是这间屋子唯一的主光:用『用这里的值导出』把射程 / 衰减 / 阴影
    # 边缘单独定死,演示"灯光进一步设计"里"漫射光线的衰减、照射范围的柔和"。
    # 8000W 换算成 Godot 的 8.0;射程 26 米保持不变,衰减 1.25 让近处更亮、
    # 远处收得更快;阴影边缘 2.5 米柔和 + 浓度 0.8 让影子不是一刀切的黑。
    if pendant_light is not None and hasattr(pendant_light, "gk_light"):
        light = pendant_light.gk_light
        light.override = True
        light.energy = 8.0
        light.range_m = 26.0
        light.attenuation = 1.25
        light.shadow = True
        light.shadow_blur = 2.5
        light.shadow_opacity = 0.8
    # ── 吊灯摆动父节点(第一百五十轮)────────────────────────────────
    # 把吊杆 / 灯罩 / 灯泡 / 聚光 / 上照全部挂到天花板下的空物体上:
    # 摆动节点转这个空物体,整盏灯就绕着**吊点**晃,而不是每块零件各转各的。
    # 灯是 Light3D,导出后不在 glb 里 —— 环境脚本会按 `light_parents`
    # 在运行时把它们重新挂到这个空物体下面(见 asset_export)。
    pendant_pivot = bpy.data.objects.new(PENDANT_SWAY, None)
    pendant_pivot.empty_display_type = "PLAIN_AXES"
    pendant_pivot.empty_display_size = 0.3
    scene.collection.objects.link(pendant_pivot)
    pendant_pivot.location = (0.0, 0.0, height)
    for obj in list(scene.objects):
        if obj is pendant_pivot or not obj.name.startswith("大堂吊灯"):
            continue
        obj.parent = pendant_pivot
        obj.matrix_parent_inverse = pendant_pivot.matrix_world.inverted()
    for side in (-1, 1):
        for index, y in enumerate((-5.0, 5.0)):
            make_sconce(f"大堂壁灯{'东西'[side > 0]}{index + 1}",
                        (side * (half_w - 0.45), y, 3.1), scene,
                        energy=2600.0, cutoff=10.0,
                        facing=-side, axis="x")

    # ---- 钥匙 / 玩家 / 相机 / 手电 ----
    # 第一百五十六轮:钥匙从大堂半空搬进**门卫室的监控台**上 ——
    # 玩家必须先找到东墙的门洞,第一关才有"探索"这段。
    # 桌上先垫一张纸,钥匙才有"被人放在这"的落点
    decor(make_box("门卫室钥匙垫纸", (0.62, 0.62, 0.01),
                   (20.28, 0.86, 0.975), (0.70, 0.68, 0.60, 1.0),
                   scene=scene))
    make_box(KEY, (0.40, 0.40, 0.40), (20.28, 0.86, 1.16),
             (0.95, 0.82, 0.30, 1.0), scene=scene,
             glow=((1.0, 0.86, 0.40, 1.0), 0.28))
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
    log(f"{LOBBY}:地面 + 四壁 + 3 柱(含柱头柱基)+ 天窗 + 吊灯 + 4 壁灯 + "
        f"门套/门灯 + 地雕/落地钟 + {GUARD_CCTV} + {DOOR_PANEL} + {KEY} + "
        f"玩家 + {TORCH} + 两台相机")
    return player


def build_corridor():
    """第二关 走廊:窄长廊 + 窗(夜空)+ 吊灯 + 巡逻怪物 + 2 个柜子 + 尽头门。

    ── 第一百四十六轮:从"大厅"改成"走廊"────────────────────────────
    上一版 48×14 米,宽得能并排走三排人 —— 怪物的视野死角太多、
    灯也铺不满,玩家看不到"必须往前"的压迫感。这一版:

      * 宽 14 → **8.4 米**,高 6 → **4.8 米**(窄 + 低 = 隧道感);
      * 北墙开 **4 扇窗**(洞口无玻璃,直接看到天空盒的夜空和月亮),
        窗间是墙柱;天花板每 12 米一道横梁,把 48 米切成有节奏的段落;
      * 灯换成 **4 盏吊灯**(两盏亮 / 一盏坏 / 一盏会闪),地上的光斑
        一段一段的 —— 潜行游戏的"明暗路线"就是这么来的;
      * 尽头门上方有 **安全出口绿灯**,怪物出生点墙上有 **应急红灯**
        (开局灭,追击时由逻辑点亮);
      * 地面撒两处 **碎玻璃**(踩上去会响,引出 AI 听觉)和一块 **电池**
        (补手电电量)。

    ⚠ 巡逻点位置是**功能**(AI 每帧拿它当目标点),一个都不能挪;
      它们的"非实体"由 `write_physics` 的 triggers 参数保证。
    """
    width, depth, height = ROOM[CORRIDOR]
    half_w, half_d = width / 2.0, depth / 2.0
    # 走廊:冷灰绿的旧机构感(墙),地面比墙更暗 —— 让灯的光斑跳出来
    floor_color = (0.15, 0.16, 0.18, 1.0)
    wall_color = (0.30, 0.33, 0.33, 1.0)
    trim_color = (0.09, 0.10, 0.10, 1.0)
    scene = bpy.data.scenes.new(CORRIDOR)

    make_box("走廊地面", (width, depth, 0.4), (0.0, 0.0, -0.2),
             floor_color, scene=scene)
    # ── 南墙:不再是整面实墙 —— x 2.0~5.0 留一个门洞通向储藏间 ────────
    # (第一百四十七轮"关卡区域扩展":走廊 + 南侧储藏间)
    door_lo, door_hi, door_top = 2.0, 5.0, 3.2
    make_box("走廊墙南左", (door_lo + half_w, 0.5, height),
             ((-half_w + door_lo) / 2, -half_d + 0.25, height / 2),
             wall_color, scene=scene)
    make_box("走廊墙南右", (half_w - door_hi, 0.5, height),
             ((door_hi + half_w) / 2, -half_d + 0.25, height / 2),
             wall_color, scene=scene)
    decor(make_box("走廊墙南门楣", (door_hi - door_lo, 0.5, height - door_top),
                   ((door_lo + door_hi) / 2, -half_d + 0.25,
                    (door_top + height) / 2), wall_color, scene=scene))
    for x in (door_lo, door_hi):
        decor(make_box(f"走廊南门套{x:+.0f}", (0.22, 0.62, door_top),
                       (x, -half_d + 0.25, door_top / 2),
                       trim_color, scene=scene))
    windows = [(-18.0, 3.4), (-6.0, 3.4), (6.0, 3.4), (18.0, 3.4)]
    # 窗间墙用**更暗**的一档:月光从窗洞斜切进来,第一道被打亮的就是它们,
    # 原色 (0.30) 实机里是一排发白的板子,把窗洞的对比压掉了。
    make_wall_pieces("走廊北", "x", half_d - 0.25, (-half_w, half_w), 0.5,
                     height, scene, windows, (0.19, 0.21, 0.21, 1.0))
    for center, window_width in windows:
        make_window(f"走廊窗{center:+.0f}", center, half_d - 0.25,
                    window_width, scene)
        # ── 窗光改由月亮投影负责(第一百四十九轮)──────────────────
        # 上一轮这里摆了 4 盏屋外聚光"硬造窗光":虽然也有窗形光斑,但
        # ① 月盘和月光都对不上(月亮方向是随机的);② 4 盏聚光 + 4 张
        # 阴影贴图,白白烧性能。现在『环境_月亮』固定朝北窗、投影打开,
        # 平行光的阴影**天然**是窗形光斑(墙挡光、窗洞漏光)—— 这里
        # 什么都不用再放。
    # 北墙上的壁柱(在墙柱正中,给立面加竖线)
    for x in (-12.0, 0.0, 12.0):
        decor(make_box(f"走廊北壁柱{x:+.0f}", (0.55, 0.30, height - 0.4),
                       (x, half_d - 0.62, (height - 0.4) / 2),
                       trim_color, scene=scene))
    # 南墙:壁柱 + 一根沿墙的管道
    for x in (-12.0, 0.0, 12.0):
        decor(make_box(f"走廊南壁柱{x:+.0f}", (0.55, 0.30, height - 0.4),
                       (x, -half_d + 0.62, (height - 0.4) / 2),
                       trim_color, scene=scene))
    decor(make_box("走廊管道", (width - 1.0, 0.22, 0.22),
                   (0.0, -half_d + 0.52, height - 0.55),
                   (0.34, 0.30, 0.26, 1.0), scene=scene))

    # ── 天花板:中段开一个天井(第一百五十四轮)──────────────────
    # 走廊 48 米全是顶,抬头永远是一块板 —— 开一个 6×4.4 的洞,
    # 夜里能直接看到星空(正好把『星星闪动』和半球开关展示出来),
    # 月光也会从洞口落下来一池。四周留 2 米宽的板,结构上说得过去。
    ceil_color = (0.20, 0.21, 0.25, 1.0)
    ceil_z = height + 0.15
    well_lo, well_hi = -9.0, -3.0         # 洞口在 x 方向
    well_y = 2.2                          # 洞口在 y 方向半深
    solid_box(make_box("走廊天花板西", (well_lo - (-half_w), depth, 0.3),
                       ((-half_w + well_lo) / 2, 0.0, ceil_z),
                       ceil_color, scene=scene))
    solid_box(make_box("走廊天花板东", (half_w - well_hi, depth, 0.3),
                       ((well_hi + half_w) / 2, 0.0, ceil_z),
                       ceil_color, scene=scene))
    for tag, y in (("北", (well_y + half_d) / 2), ("南", (-half_d - well_y) / 2)):
        solid_box(make_box(f"走廊天花板{tag}井",
                           (well_hi - well_lo, half_d - well_y, 0.3),
                           ((well_lo + well_hi) / 2, y, ceil_z),
                           ceil_color, scene=scene))
    # 天井框:四条窄边,洞才不是"天花板破了一块"
    for tag, size, loc in (
            ("西", (0.22, well_y * 2 + 0.44, 0.42),
             (well_lo, 0.0, height - 0.05)),
            ("东", (0.22, well_y * 2 + 0.44, 0.42),
             (well_hi, 0.0, height - 0.05)),
            ("北", (well_hi - well_lo, 0.22, 0.42),
             ((well_lo + well_hi) / 2, well_y, height - 0.05)),
            ("南", (well_hi - well_lo, 0.22, 0.42),
             ((well_lo + well_hi) / 2, -well_y, height - 0.05))):
        decor(make_box(f"走廊天井框{tag}", size, loc, trim_color, scene=scene))
    # 天井光:从洞口上方朝下,像月光落进来(墙/板把光挡成矩形)
    make_aimed_light("走廊天井光", ((well_lo + well_hi) / 2, 0.0, 9.6),
                     ((well_lo + well_hi) / 2, 0.0, 0.0), 6800.0,
                     (0.55, 0.68, 1.0), scene, spot_size=78.0,
                     cutoff=17.0, volume=1.2, softness=0.10)
    for x in (-12.0, 0.0, 12.0):
        decor(make_box(f"走廊横梁{x:+.0f}", (0.45, depth, 0.35),
                       (x, 0.0, height - 0.18), trim_color, scene=scene))
    # ── 墙面分缝 + 腰线(第一百四十八轮)──────────────────────────
    # 一面 48 米长的纯色墙,眼睛读不出"有多大"。每隔 6 米一道浅缝、
    # 1 米高处一道腰线,墙就有了**尺度**;顺便把灯的光斑切成一段一段。
    # 缝在 x = ±3, ±9, ±15, ±21(避开 ±6 / ±18 的窗洞)。
    for x in (-21.0, -15.0, -9.0, -3.0, 3.0, 9.0, 15.0, 21.0):
        decor(make_box(f"走廊墙缝{x:+.0f}", (0.05, 0.06, height - 1.6),
                       (x, half_d - 0.28, height / 2 - 0.1),
                       trim_color, scene=scene))
    for side in (-1, 1):
        decor(make_box(f"走廊腰线{'北南'[side < 0]}", (width - 0.4, 0.06, 0.16),
                       (0.0, side * (half_d - 0.28), 1.05),
                       trim_color, scene=scene))
        # 踢脚线(第一百五十三轮):墙脚压一条更深的线,墙和地面才"接得上"
        decor(make_box(f"走廊踢脚线{'北南'[side < 0]}",
                       (width - 0.4, 0.05, 0.20),
                       (0.0, side * (half_d - 0.30), 0.10),
                       (0.055, 0.065, 0.065, 1.0), scene=scene))
    # ── 南墙壁灯(第一百四十八轮第二版)──────────────────────────
    # 只有吊灯时,墙是一整面均匀的灰 —— 因为灯在天花板中间,墙只吃到
    # 掠射光。南墙没有窗,正好挂 4 盏暖壁灯:墙上出现一排光斑,
    # 和北墙的冷窗光形成**冷暖对打**,走廊的纵深就出来了。
    for index, x in enumerate((-16.0, -8.0, 8.0, 16.0)):
        make_sconce(f"走廊壁灯{index + 1}", (x, -half_d + 0.42, 3.0),
                    scene, energy=1500.0, cutoff=8.0, facing=1)

    # ── 两端封头(第一百五十六轮)──────────────────────────────────
    # 之前走廊只有南北两面墙:西端是**敞开的**,第三人称相机在出生点
    # 往后退(臂长 5 米)就直接退到场景外面 —— 画面里一片虚空。
    # 东端也只有一扇 3 米宽的门板,两侧和门顶都是洞。
    # 补上两端,顺便给西端加"封条",叙事上说明为什么只能往前。
    solid_box(make_box("走廊西墙", (0.5, depth, height),
                       (-half_w + 0.25, 0.0, height / 2),
                       wall_color, scene=scene))
    for index, z in enumerate((0.85, 1.75, 2.65, 3.55)):
        board = make_box(f"走廊封条{index + 1}", (0.14, depth - 0.9, 0.36),
                         (-half_w + 0.06, 0.0, z), trim_color, scene=scene)
        board.rotation_euler = (0.0, 0.0, 0.0)
        decor(board)
    door_x = half_w - 0.8
    # 东端两段墙用**更暗**的颜色:出口灯(绿)就在门上方 1 米,
    # 原色 (0.30) 在实机里被洗成一大块浅灰板,和整条走廊的暗调脱节。
    end_wall = (0.17, 0.19, 0.19, 1.0)
    solid_box(make_box("走廊东墙南", (0.5, half_d - 1.5, height),
                       (door_x, (-half_d - 1.5) / 2, height / 2),
                       end_wall, scene=scene))
    solid_box(make_box("走廊东墙北", (0.5, half_d - 1.5, height),
                       (door_x, (half_d + 1.5) / 2, height / 2),
                       end_wall, scene=scene))
    decor(make_box("走廊东墙门楣", (0.5, 3.0, height - 3.6),
                   (door_x, 0.0, (3.6 + height) / 2),
                   wall_color, scene=scene))
    make_box("走廊尽头门", (0.6, 3.0, 3.6), (door_x, 0.0, 1.8),
             (0.44, 0.52, 0.42, 1.0), scene=scene)
    for side in (-1, 1):
        decor(make_box(f"走廊门套{'左右'[side > 0]}", (0.75, 0.45, 4.0),
                       (door_x - 0.35, side * 1.7, 2.0),
                       trim_color, scene=scene))
    decor(make_box("走廊门楣", (0.75, 3.7, 0.5), (door_x - 0.35, 0.0, 3.85),
                   trim_color, scene=scene))
    hide_mesh(make_box(CORRIDOR_END, (5.0, 5.0, 0.2), (half_w - 3.0, 0.0, 0.1),
                       (0.12, 0.18, 0.16, 1.0), scene=scene))
    # 出口指示灯:绿色自发光片 + 一盏很小的绿光
    exit_sign = make_box("走廊安全出口灯", (0.10, 0.9, 0.34),
                         (door_x - 0.95, 0.0, 4.15),
                         (0.30, 0.90, 0.50, 1.0), scene=scene,
                         glow=((0.25, 1.0, 0.45, 1.0), 1.8))
    decor(exit_sign)
    exit_light = make_light("走廊安全出口光", "POINT",
                            (door_x - 1.2, 0.0, 4.10), 1500.0,
                            (0.35, 1.0, 0.55), scene, cutoff=6.5)
    exit_light.data.shadow_soft_size = 0.08

    # 怪物出生点旁的应急红灯(开局灭,追击时逻辑点亮)
    alarm = make_box("走廊应急红灯", (0.34, 0.16, 0.34),
                     (-9.0, -half_d + 0.42, 3.85),
                     (0.55, 0.12, 0.12, 1.0), scene=scene,
                     glow=((1.0, 0.10, 0.08, 1.0), 1.2))
    decor(alarm)
    # ⚠ 这盏灯的能量必须是**真实值**(4200W):『灯光_设置』的『开启』只改
    #   `visible`,不碰 `light_energy` —— 导出时填 0 的话,逻辑再怎么"开"
    #   也还是一片黑(踩过这个坑的思路)。开局熄灭交给逻辑图『准备好』那一句。
    make_light("走廊应急红光", "POINT", (-9.0, -half_d + 0.75, 3.80), 5200.0,
               (1.0, 0.16, 0.10), scene, cutoff=10.0)

    # 4 盏吊灯:x = ±14.4 / ±4.8;第 2 盏坏的、第 3 盏会闪
    for index, x in enumerate((-14.4, -4.8, 4.8, 14.4)):
        make_pendant(f"走廊吊灯{index + 1}", x, height, scene,
                     energy=6000.0, cutoff=14.0,
                     color=(1.0, 0.95, 0.89),
                     shade=(0.17, 0.17, 0.16, 1.0),
                     broken=(index == 1))

    # ── 储藏间(南侧,8.4 米 × 6.4 米):走廊之外的第二块可走区域 ──────
    # 它是"扩展"而不是"新关卡":同一个场景、同一张关卡图,走过去就能进。
    # 里面放第三个柜子(躲藏)+ 一堆箱子 + 一处碎玻璃 ——
    # 听见玻璃声的怪物会走到门口来看(门洞在 x 2~5)。
    room_half_x = 4.2
    room_x = 4.1
    room_front = -half_d          # -4.2(走廊南墙内侧)
    room_back = -half_d - 6.4     # -10.6
    room_mid_y = (room_front + room_back) / 2
    room_depth = room_front - room_back
    room_height = 3.8
    make_box("储藏间地面", (room_half_x * 2 + 0.4, room_depth + 0.4, 0.4),
             (room_x, room_mid_y, -0.2), floor_color, scene=scene)
    solid_box(make_box("储藏间天花板", (room_half_x * 2 + 0.4, room_depth + 0.4, 0.3),
                       (room_x, room_mid_y, room_height + 0.15),
                       (0.18, 0.19, 0.23, 1.0), scene=scene))
    # 西 / 东 / 南三面墙(北面就是走廊南墙,门洞已经在上面开了)
    for tag, size, loc in (
            ("西墙", (0.5, room_depth + 0.4, room_height),
             (room_x - room_half_x, room_mid_y, room_height / 2)),
            ("东墙", (0.5, room_depth + 0.4, room_height),
             (room_x + room_half_x, room_mid_y, room_height / 2))):
        solid_box(make_box(f"储藏间{tag}", size, loc, wall_color, scene=scene))
    # ── 南墙:给"配电间"留 2 米门洞(第一百五十二轮)────────────────
    pit_lo, pit_hi, pit_top = room_x + 0.2, room_x + 2.2, 2.2
    south_lo = room_x - room_half_x - 0.25
    south_hi = room_x + room_half_x + 0.25
    solid_box(make_box("储藏间南墙左", (pit_lo - south_lo, 0.5, room_height),
                       ((south_lo + pit_lo) / 2, room_back, room_height / 2),
                       wall_color, scene=scene))
    solid_box(make_box("储藏间南墙右", (south_hi - pit_hi, 0.5, room_height),
                       ((pit_hi + south_hi) / 2, room_back, room_height / 2),
                       wall_color, scene=scene))
    solid_box(make_box("配电间门楣", (pit_hi - pit_lo, 0.5,
                                     room_height - pit_top),
                       ((pit_lo + pit_hi) / 2, room_back,
                        (pit_top + room_height) / 2), wall_color, scene=scene))
    for x in (pit_lo, pit_hi):
        decor(make_box(f"配电间门套{x:+.0f}", (0.18, 0.62, pit_top),
                       (x, room_back, pit_top / 2), trim_color, scene=scene))

    # ── 配电间(里间,4×3.6 米):区域扩展 + 区域任务 ──────────────────
    pit_x = (pit_lo + pit_hi) / 2
    pit_depth, pit_h = 3.6, 3.0
    pit_back = room_back - pit_depth
    pit_mid = room_back - pit_depth / 2
    solid_box(make_box("配电间地面", (2.8, pit_depth + 0.4, 0.4),
                       (pit_x, pit_mid, -0.2), floor_color, scene=scene))
    solid_box(make_box("配电间天花板", (2.8, pit_depth + 0.4, 0.3),
                       (pit_x, pit_mid, pit_h + 0.15),
                       (0.18, 0.19, 0.22, 1.0), scene=scene))
    for tag, size, loc in (
            ("西墙", (0.4, pit_depth + 0.4, pit_h),
             (pit_x - 1.4, pit_mid, pit_h / 2)),
            ("东墙", (0.4, pit_depth + 0.4, pit_h),
             (pit_x + 1.4, pit_mid, pit_h / 2)),
            ("南墙", (2.8, 0.4, pit_h), (pit_x, pit_back, pit_h / 2))):
        solid_box(make_box(f"配电间{tag}", size, loc, wall_color, scene=scene))
    # 配电箱:深色柜体 + 面板 + 三颗待机指示灯(灯灭时也亮着)
    solid_box(make_box("配电箱", (1.4, 0.55, 1.5),
                       (pit_x, pit_back + 0.45, 0.95),
                       (0.18, 0.20, 0.22, 1.0), scene=scene))
    decor(make_box("配电箱面板", (1.1, 0.06, 1.1),
                   (pit_x, pit_back + 0.20, 1.0),
                   (0.30, 0.33, 0.35, 1.0), scene=scene))
    for index, offset in enumerate((-0.35, 0.0, 0.35)):
        decor(make_box(f"配电箱指示灯{index + 1}", (0.08, 0.04, 0.08),
                       (pit_x + offset, pit_back + 0.16, 1.36),
                       (0.20, 1.0, 0.40, 1.0), scene=scene,
                       glow=((0.25, 1.0, 0.45, 1.0), 0.7)))
    # 里间灯:开局灭,拉闸后由逻辑点亮
    # ⚠ 不能填 0:能量 0 的灯导出时会被整盏跳过(和"应急红灯"同一个坑)。
    # 开局给 800W 的"待机微光",拉闸后逻辑把它调到 2400W —— 视觉上就是来电。
    pit_light = make_light("配电间灯", "POINT",
                           (pit_x, pit_mid, 2.55), 800.0,
                           (1.0, 0.92, 0.80), scene, cutoff=6.0)
    # ── 环境叙事细节(第一百五十三轮)──────────────────────────────
    # 一个房间"像不像有人用过",靠的是这些小东西:线缆、门牌、警示带。
    # 全部走 decor(不生成碰撞),玩家不会被 5 厘米的薄片绊住。
    for index, offset in enumerate((-0.45, 0.0, 0.45)):
        decor(make_box(f"配电间线缆{index + 1}", (0.05, 0.05, pit_h - 1.7),
                       (pit_x + offset, pit_back + 0.52,
                        1.7 + (pit_h - 1.7) / 2),
                       (0.07, 0.08, 0.09, 1.0), scene=scene))
    # 门牌:门洞上方一块自发光小牌(深绿底,像应急指示牌)
    decor(make_box("配电间门牌", (0.92, 0.06, 0.26),
                   ((pit_lo + pit_hi) / 2, room_back - 0.28,
                    pit_top + 0.26),
                   (0.10, 0.34, 0.18, 1.0), scene=scene,
                   glow=((0.35, 1.0, 0.55, 1.0), 0.9)))
    # 配电箱前的两道警示带(贴地薄片,黄黑工业色)
    for offset in (-0.55, 0.55):
        decor(make_box(f"配电间警示带{offset:+.2f}", (1.9, 0.12, 0.02),
                       (pit_x, pit_back + 1.95 + offset, 0.012),
                       (0.42, 0.30, 0.05, 1.0), scene=scene))
    # 储藏间地面两滩水渍(深色薄片 + 一点点反光感)
    for index, (x, y, sx, sy) in enumerate((
            (room_x - 1.8, room_mid_y + 0.4, 1.7, 1.2),
            (room_x + 1.1, room_back + 2.0, 1.1, 0.9))):
        decor(make_box(f"储藏间水渍{index + 1}", (sx, sy, 0.012),
                       (x, y, 0.008), (0.10, 0.12, 0.14, 1.0),
                       scene=scene))

    # 两个触发区:门口(改任务目标)/ 配电箱前(拉闸)
    hide_mesh(make_box("配电间入口", (2.0, 1.2, 0.2),
                       (pit_x, room_back - 0.7, 0.1),
                       (0.10, 0.20, 0.20, 1.0), scene=scene))
    hide_mesh(make_box("配电间闸刀", (1.6, 1.2, 0.2),
                       (pit_x, pit_back + 1.7, 0.1),
                       (0.10, 0.20, 0.20, 1.0), scene=scene))
    make_pendant("储藏间吊灯", room_x, room_height, scene,
                 energy=2600.0, cutoff=9.0, y=room_mid_y,
                 color=(1.0, 0.86, 0.66), shade=(0.20, 0.18, 0.16, 1.0))
    # ── 货架(第一百四十八轮:从"一块 5 米的厚板"改成真的架子)──────
    # 原来是一个 0.5×5×2.2 的实心长方体,站在门口看就是"房间正中一堵墙"。
    # 现在是 2 根立柱 + 3 层隔板 —— 有缝隙、有横向线条,才读得出是货架,
    # 光也能从层板之间穿过去。
    shelf_x = room_x - room_half_x + 0.55
    for index, z in enumerate((0.35, 1.15, 1.95)):
        solid_box(make_box(f"储藏间货架板{index + 1}", (0.55, 4.6, 0.09),
                           (shelf_x, room_mid_y, z),
                           (0.32, 0.27, 0.21, 1.0), scene=scene))
    for index, y in enumerate((room_mid_y - 2.2, room_mid_y + 2.2)):
        solid_box(make_box(f"储藏间货架柱{index + 1}", (0.10, 0.10, 2.1),
                           (shelf_x - 0.22, y, 1.05),
                           (0.24, 0.20, 0.16, 1.0), scene=scene))
    # 南墙上的通风格栅(横条 + 后挡板):给侧厅一点"有设备"的细节
    solid_box(make_box("储藏间通风口", (2.2, 0.10, 1.0),
                       (room_x + 0.2, room_back + 0.22, 2.55),
                       (0.05, 0.055, 0.06, 1.0), scene=scene))
    for index in range(4):
        decor(make_box(f"储藏间格栅{index + 1}", (2.0, 0.06, 0.06),
                       (room_x + 0.2, room_back + 0.28,
                        2.15 + index * 0.26),
                       (0.16, 0.17, 0.18, 1.0), scene=scene))
    for index, (x, y, size) in enumerate((
            (room_x + 2.2, room_back + 1.1, 1.0),
            (room_x + 2.9, room_back + 1.6, 0.7),
            (room_x - 2.4, room_back + 1.4, 0.9))):
        solid_box(make_box(f"储藏间木箱{index + 1}", (size, size, size),
                           (x, y, size / 2), (0.42, 0.32, 0.20, 1.0),
                           scene=scene))

    # 柜子(躲藏区)
    cab_a = make_box(CABINET_A, (1.8, 1.8, 3.0), (-4.0, half_d - 1.35, 1.5),
                     (0.56, 0.44, 0.32, 1.0), scene=scene)
    cab_b = make_box(CABINET_B, (1.8, 1.8, 3.0), (6.0, -half_d + 1.35, 1.5),
                     (0.56, 0.44, 0.32, 1.0), scene=scene)
    # 第三个柜子:在储藏间里,靠南墙、柜门朝 +Y(朝房间)
    cab_c = make_box(CABINET_C, (1.8, 1.8, 3.0),
                     (room_x - 1.6, room_back + 1.35, 1.5),
                     (0.52, 0.40, 0.30, 1.0), scene=scene)
    for cabinet in (cab_a, cab_b, cab_c):
        cabinet_physics = getattr(cabinet, "gk_physics", None)
        if cabinet_physics is not None:
            cabinet_physics.is_hide_spot = True
    # 柜门缝:两片深色薄板,让柜子看得出是柜子
    # 柜门位置 = 柜心 - facing * 0.93(公式在下面那句里):
    #   甲在北墙(y +2.85),房间在南 -> facing = +1,门在 y +1.92;
    #   乙在南墙(y -2.85),房间在北 -> facing = -1,门在 y -1.92;
    #   丙在储藏间最里面(y -9.25,靠南墙),房间在北 -> facing = **-1**。
    # ⚠ 丙上一版填的是 +1,门贴在墙上,实机里只能看到一块没有门缝的方块。
    for side, cabinet, facing in (("甲", cab_a, 1), ("乙", cab_b, -1),
                                  ("丙", cab_c, -1)):
        for offset in (-0.44, 0.44):
            decor(make_box(f"柜子{side}门{offset:+.0f}", (0.84, 0.06, 2.6),
                           (cabinet.location.x + offset,
                            cabinet.location.y - facing * 0.93, 1.45),
                           (0.30, 0.23, 0.16, 1.0), scene=scene))

    # 碎玻璃(踩到会响:引出 AI 听觉)
    # ── 第一百四十六轮:一块 1.9×1.9 的"玻璃板"在实机里像一块白地垫 ─────
    # 现在拆成「一块很薄的深色触发底板 + 几片随机的小碎片」:
    # 底板负责判定(几乎看不见),碎片负责"这里有碎玻璃"的观感。
    for index, (x, y) in enumerate(((-15.0, 1.6), (8.5, -1.8),
                                    (room_x + 0.6, room_back + 2.4))):
        # 底板只是判定体:导出时不渲染(碎片负责观感)
        plate = hide_mesh(make_box(f"碎玻璃{index + 1}", (1.9, 1.9, 0.02),
                                   (x, y, 0.01), (0.09, 0.11, 0.13, 1.0),
                                   scene=scene))
        solid_box(plate)
        plate.gk_physics.is_trigger = True
        plate.gk_physics.body_type = "Area3D"
        for shard_index, (dx, dy, size, rot) in enumerate((
                (-0.55, -0.40, 0.44, 0.5), (-0.10, 0.45, 0.34, -0.8),
                (0.35, -0.15, 0.52, 1.2), (0.62, 0.42, 0.28, -0.3),
                (-0.62, 0.22, 0.30, 2.0))):
            piece = make_box(f"碎玻璃{index + 1}片{shard_index + 1}",
                             (size, size * 0.72, 0.02),
                             (x + dx, y + dy, 0.02),
                             (0.55, 0.64, 0.72, 1.0), scene=scene)
            piece.rotation_euler = (0.0, 0.0, rot)
            decor(piece)
    # ── 为什么没有"电池补给" ────────────────────────────────────────
    # 电量变量住在**角色图**(story_upgrade 把整条手电链搬进『玩家角色逻辑』),
    # 关卡图里再写一个『电量』就是另一个脚本里的另一个变量 —— HUD 会显示
    # 关卡图那个,手电看的却是角色图那个,两套数字对不上。跨脚本改变量要
    # 走信号,代价远大于"捡一块电池"的收益,所以这一版先不做。

    # ⚠ 出生点离巡逻线要**大于视野**:不然一进走廊就被发现、直接开追,
    #   恐怖游戏的"慢慢摸过去"就没有了(horror 示例实测踩过)。
    player = make_box(PLAYER_CORRIDOR, (1.0, 1.0, 1.8), (-20.0, -2.0, 1.0),
                      (0.80, 0.82, 0.90, 1.0), scene=scene)
    # ── 出生朝向(第一百五十一轮)──────────────────────────────────
    # 走廊沿 X 展开,出生点在西端;默认 rotation=0 时角色面朝 -Z(北墙),
    # 第三人称相机就一头撞在北墙/窗户上 —— 用户看到的"开局视角不对"
    # 就是这个。Blender 的前方是 -Y,绕 Z 转 **+90°** 才把它转到 +X
    # (走廊深处);写成 -90° 会转到 -X(西墙),回镜后正对一面墙角。
    player.rotation_euler = (0.0, 0.0, math.radians(90.0))
    # ⚠ 怪物的颜色要**暗**:0.72 的红在 sRGB 里是 0.86,再乘灯的能量就
    #   全通道爆到 1.0 —— 实机里是一团发光的白块(第一次调完就是这样)。
    #   0.26/0.04/0.05 在卡通色阶下是"暗红",灯光打上去才看得出是红色。
    enemy = make_box(ENEMY, (1.4, 1.4, 2.0), (-9.0, 0.0, 1.0),
                     (0.26, 0.04, 0.05, 1.0), scene=scene)
    for name, x in ((PATROL_A, -9.0), (PATROL_B, 0.0), (PATROL_C, 9.0)):
        # ── 第一百二十六轮:巡逻点改**非实体**(见 write_physics)──────────
        # ⚠ **不要**把它们挪位置、更不要"抬到天花板里" —— 它们**同时是
        #   巡逻路点**:AI 每帧用 `_gk_global_position_3d(节点_巡逻点X)` 当
        #   目标点(实测:把三个点抬到 Godot y=5.3 之后,怪物跟着爬到
        #   y=5.0、卡在天花板里,`ray_blocker=走廊天花板`,AI 直接瞎了)。
        #   位置是**功能**,不能动;能动的是"它挡不挡人"。
        # ── 第一百四十六轮:外观从"半米宽两米高的灰盒子"改成小暗桩 ──────
        # 它们是**路点**,不是家具:实机里三个亮盒子站在走廊正中,玩家
        # 会以为那是能躲的东西。位置/名字/Area 身份都不变,只改大小和颜色。
        marker = make_box(name, (0.14, 0.14, 0.14), (x, 0.0, 0.07),
                          (0.10, 0.11, 0.13, 1.0), scene=scene)
        decor(marker)
    make_camera(CAM3_CORRIDOR, (0.0, -20.0, 5.5),
                (math.radians(72.0), 0.0, 0.0), scene)
    make_camera(CAM1_CORRIDOR, (0.0, -10.0, 1.6),
                (math.radians(90.0), 0.0, 0.0), scene)
    # ── 开场"看一眼敌人"的特写机位 + 曲线轨道(第一百五十轮)──────────
    # 玩家出生在 (-20,-2) 且面朝 +X,第三人称相机在身后 x≈-23.5；
    # 轨道从那里起,沿走廊南侧向东,落到怪物**西侧 6 米**的低机位。
    # ⚠ 不要落到怪物正南 3.4 米:走廊只有 8.4 宽,那个距离下 1.4 米的
    #   怪物会占满半个屏幕(上一版用户看到的"视角不对"就是贴太近)。
    # 『相机_运镜』现在严格按曲线的**世界落点**走:开头 0.35 秒从
    # 当前相机并进曲线,之后沿轨道飞 —— 不会再因为相机起飞点不同
    # 把整条曲线平移、把落点也挪走(上一版就是这样扎到怪物脸上的)。
    monster_cam = make_camera(MONSTER_CAM, (-15.0, -3.4, 2.0),
                              (math.radians(82.0), 0.0, 0.0), scene)
    # 运镜专用机位:不要让导出的环境脚本把它当成"主相机"点亮 ——
    # 否则一进走廊画面就钉在特写上,玩家的第三人称相机被挤掉。
    monster_cam["gk_not_main_camera"] = True
    # ── 曲线是世界坐标(第一百五十一轮)────────────────────────────
    # 第一点放在出生点身后(Godot x≈-23.5、眼高 y≈3.3),沿走廊南侧
    # (Godot z≈3)向东、下压,最后落在怪物西侧 6 米的低机位。
    # Blender 坐标 = (x, -z, y)。
    make_curve(MONSTER_TRACK,
               [(-23.5, -2.0, 3.3), (-21.0, -3.0, 4.0),
                (-18.0, -3.2, 3.1), (-15.0, -3.4, 2.0)], scene)
    scene.camera = bpy.data.objects[CAM3_CORRIDOR]
    log(f"{CORRIDOR}:{width:g}x{depth:g}x{height:g} + 4 窗 + 4 吊灯(1 坏/1 闪)+ "
        f"3 壁柱 + 应急红灯 + 出口绿灯 + 玩家 + {ENEMY} + 3 巡逻点(非实体)+ "
        f"{CABINET_A}/{CABINET_B}/{CABINET_C} + 储藏间 + 碎玻璃 x3")
    return scene, player, enemy, (PATROL_A, PATROL_B, PATROL_C)


def build_ending():
    """第三关 结局:一个小房间 + 一扇大窗(月光)+ 一张桌子 + 两根蜡烛。

    ── 第一百四十六轮:补成"看得出去"的房间 ──────────────────────────
    上一版只有南北两堵墙,左右两边直接露出世界底色(实机截图里是两块
    纯黑),而且三盏壁灯排在一面墙上 —— 像布景板,不像房间。这一版:

      * 四面墙 + 天花板,把空间围起来;
      * 北墙一扇 **5 米宽的大窗**:外面是夜空和月亮,月光真的照进来
        (天空盒 + 月亮平行光 + 窗外一盏冷色聚光,打出窗形光斑);
      * 窗下一条 **长桌 + 两根蜡烛**(暖光),和冷月光对着 ——
        一冷一暖,结局房间的情绪就在这对比里。
    """
    width, depth, height = ROOM[ENDING]
    half_w, half_d = width / 2.0, depth / 2.0
    floor_color = (0.21, 0.18, 0.19, 1.0)
    wall_color = (0.36, 0.32, 0.30, 1.0)
    trim_color = (0.11, 0.10, 0.10, 1.0)
    scene = bpy.data.scenes.new(ENDING)

    make_box("结局地面", (width, depth, 0.4), (0.0, 0.0, -0.2),
             floor_color, scene=scene)
    # 南 / 东 / 西墙
    make_box("结局南墙", (width, 0.5, height), (0.0, -half_d, height / 2),
             wall_color, scene=scene)
    make_box("结局东墙", (0.5, depth, height), (half_w, 0.0, height / 2),
             wall_color, scene=scene)
    make_box("结局西墙", (0.5, depth, height), (-half_w, 0.0, height / 2),
             wall_color, scene=scene)
    # 北墙:中间一扇 5 米宽的窗
    make_wall_pieces("结局北", "x", half_d - 0.25, (-half_w, half_w), 0.5,
                     height, scene, [(0.0, 5.0)], wall_color)
    make_window("结局窗", 0.0, half_d - 0.25, 5.0, scene)
    solid_box(make_box("结局天花板", (width, depth, 0.3),
                       (0.0, 0.0, height + 0.15),
                       (0.20, 0.20, 0.24, 1.0), scene=scene))
    # ── 窗光交给月亮,不再补一盏聚光(第一百五十一轮第三轮反馈)────────
    # 这里以前还有一盏 7600W 的『结局窗光』聚光(在窗外朝屋里打)。
    # 它和月亮重复,而且是一盏**从低角度斜切进来**的灯 —— 蜡烛、椅子
    # 都被它拉出长长的影子,用户看到的就是"明明只有顶灯,地上却有一条
    # 很长的黑影"。现在只留月亮:窗形光由月亮的投影自然形成,方向也和
    # 天上的月亮一致。
    #
    # 门套(玩家进来的那面墙,给一个"回不去"的暗示)
    for side in (-1, 1):
        decor(make_box(f"结局门套{'左右'[side > 0]}", (0.5, 0.8, 4.2),
                       (side * 1.65, -half_d + 0.55, 2.1),
                       trim_color, scene=scene))
    decor(make_box("结局门楣", (4.0, 0.8, 0.55),
                   (0.0, -half_d + 0.55, 4.15), trim_color, scene=scene))

    # ---- 护墙板:1 米高的深色木裙,把墙"压"住,和地板接得上 ----
    # (第一百五十六轮:和走廊踢脚线 / 大堂护墙板同一套语言)
    for tag, size, loc in (
            ("南", (width - 0.6, 0.10, 0.95), (0.0, -half_d + 0.06, 0.50)),
            ("东", (0.10, depth - 0.6, 0.95), (half_w - 0.06, 0.0, 0.50)),
            ("西", (0.10, depth - 0.6, 0.95), (-half_w + 0.06, 0.0, 0.50))):
        decor(make_box(f"结局护墙板{tag}", size, loc,
                       (0.14, 0.12, 0.11, 1.0), scene=scene))

    # 窗下的长桌 + 两根蜡烛
    solid_box(make_box("结局长桌", (5.4, 1.1, 0.16), (0.0, 6.6, 0.86),
                       (0.40, 0.30, 0.22, 1.0), scene=scene))
    for side in (-1, 1):
        decor(make_box(f"结局桌腿{'左右'[side > 0]}", (0.22, 0.9, 0.78),
                       (side * 2.2, 6.6, 0.39),
                       (0.32, 0.24, 0.18, 1.0), scene=scene))
        decor(make_box(f"结局蜡烛{'左右'[side > 0]}", (0.16, 0.16, 0.42),
                       (side * 0.55, 6.6, 1.15),
                       (0.92, 0.88, 0.72, 1.0), scene=scene,
                       glow=((1.0, 0.78, 0.45, 1.0), 1.2)))
    # ── 烛光:只做"桌面上的暖光点",不投影(第一百五十一轮第三轮反馈)──
    # 以前 4200W + 投影:一盏 1.45 米高的点光把蜡烛、桌沿、椅子拉到
    # 好几米外的地上,读起来像"低角度射灯"。现在压到 900W、射程 6 米,
    # 关掉投影 —— 暖光只停在桌面附近,长黑影交给吊灯(正上方)。
    candle_light = make_light("结局烛光", "POINT", (0.0, 6.6, 1.45), 900.0,
                              (1.0, 0.72, 0.42), scene, cutoff=6.0,
                              shadow=False)
    # 东西墙各一盏壁灯:不然整间屋子只有窗是亮的,家具全埋在黑里
    for side in (-1, 1):
        make_sconce(f"结局壁灯{'东西'[side > 0]}",
                    (side * (half_w - 0.42), 1.0, 2.7), scene,
                    energy=1900.0, cutoff=8.0, facing=-side, axis="x")
    # 桌子正上方一盏吊灯:房间 22×20 太大,两侧壁灯照不到中间,
    # 实机里只有窗是亮的。一盏暖吊灯把"最后一桌"圈成一个焦点。
    make_pendant("结局吊灯", 0.0, height, scene, energy=5200.0,
                 cutoff=11.0, y=6.6)
    # ── 太阳(第一百五十一轮第六轮)────────────────────────────────
    # 昼夜脚本按名字"太阳"找这盏平行光:夜里强度被压到近乎为零,
    # 天亮后由『环境_太阳』按时刻推到正常强度 —— 用户要的"太阳的出现"。
    # Blender 里 -60° 的俯仰 = 光从**正北偏上 30°**打进来(Godot 的
    # 北 = -Z = 这扇北窗的方向),正午最亮时窗形光斑落在地毯上。
    make_light("太阳", "SUN", (0.0, 0.0, height + 2.0), 1.4,
               (1.0, 0.93, 0.82), scene,
               rotation=(math.radians(-60.0), 0.0, 0.0), shadow=True)

    # ── 为什么这里没有"窗外补一盏月光聚光"──────────────────────────
    # 试过:聚光从窗外斜打进来,结果它把**窗框内侧**照得比窗外的天还亮,
    # 实机里那扇窗一半亮一半黑,像装了两块不同的玻璃。
    # 月光交给环境图的『环境_月亮』(平行光 + 投影):墙会把光挡住,
    # 只有窗口那一块漏进来 —— 这才是"窗形光斑"的正确来源。

    # ── 箱子 / 杂物(第一百五十六轮:从"白盒子"改成木箱)─────────────
    # 之前 albedo 0.52 在灯下几乎是白的,读起来像两块石膏。现在压到 0.30,
    # 再在正面加两条横板条 + 一条竖板条 —— 有缝才知道那是钉起来的木箱。
    for index, x in enumerate((-5.0, 5.0)):
        make_box(f"结局箱{'甲乙'[index]}", (2.2, 2.2, 2.0),
                 (x, 3.0, 1.0), (0.30, 0.24, 0.18, 1.0), scene=scene)
        for band, loc_z in enumerate((0.35, 1.65)):
            decor(make_box(f"结局箱{'甲乙'[index]}板条{band + 1}",
                           (2.24, 2.24, 0.14), (x, 3.0, loc_z),
                           (0.16, 0.13, 0.10, 1.0), scene=scene))
        decor(make_box(f"结局箱{'甲乙'[index]}竖条",
                       (0.16, 0.05, 2.0), (x, 4.08, 1.0),
                       (0.14, 0.11, 0.09, 1.0), scene=scene))
    for index, (x, y, size) in enumerate(((-6.6, -5.6, 0.9),
                                          (-5.6, -6.4, 0.7),
                                          (6.4, -5.8, 1.0))):
        solid_box(make_box(f"结局散箱{index + 1}", (size, size, size),
                           (x, y, size / 2), (0.42, 0.32, 0.22, 1.0),
                           scene=scene))
    decor(make_box("结局地毯", (5.0, 4.0, 0.03), (0.0, 2.0, 0.015),
                   (0.13, 0.075, 0.10, 1.0), scene=scene))
    # ── 房间从 18×18 扩到 22×20 之后,原来的家具撑不住这么大的空间 ──────
    # 补三件:西墙书架、桌边一把椅子、东墙两只木箱。
    solid_box(make_box("结局书架", (0.8, 4.2, 2.8), (-half_w + 0.9, 2.0, 1.4),
                       (0.36, 0.28, 0.20, 1.0), scene=scene))
    solid_box(make_box("结局椅子", (0.7, 0.7, 0.5), (0.0, 4.6, 0.25),
                       (0.40, 0.30, 0.22, 1.0), scene=scene))
    decor(make_box("结局椅背", (0.7, 0.14, 0.9), (0.0, 4.95, 0.75),
                   (0.36, 0.27, 0.19, 1.0), scene=scene))
    for index, (x, y, size) in enumerate(((half_w - 1.2, -2.0, 1.0),
                                          (half_w - 2.2, -2.4, 0.8))):
        solid_box(make_box(f"结局东箱{index + 1}", (size, size, size),
                           (x, y, size / 2), (0.40, 0.30, 0.20, 1.0),
                           scene=scene))
    # ── 房间从 18×18 扩到 22×20 之后,原来的家具撑不住这么大的空间 ──────
    # 补三件:西墙书架、桌边一把椅子、东墙两只木箱。
    solid_box(make_box("结局书架", (0.8, 4.2, 2.8), (-half_w + 0.9, 2.0, 1.4),
                       (0.36, 0.28, 0.20, 1.0), scene=scene))
    solid_box(make_box("结局椅子", (0.7, 0.7, 0.5), (0.0, 4.6, 0.25),
                       (0.40, 0.30, 0.22, 1.0), scene=scene))
    decor(make_box("结局椅背", (0.7, 0.14, 0.9), (0.0, 4.95, 0.75),
                   (0.36, 0.27, 0.19, 1.0), scene=scene))
    for index, (x, y, size) in enumerate(((half_w - 1.2, -2.0, 1.0),
                                          (half_w - 2.2, -2.4, 0.8))):
        solid_box(make_box(f"结局东箱{index + 1}", (size, size, size),
                           (x, y, size / 2), (0.40, 0.30, 0.20, 1.0),
                           scene=scene))

    player = make_box(PLAYER_ENDING, (1.0, 1.0, 1.8), (0.0, -6.0, 1.0),
                      (0.80, 0.82, 0.90, 1.0), scene=scene)
    make_camera(CAM3_ENDING, (0.0, -14.0, 5.5),
                (math.radians(72.0), 0.0, 0.0), scene)
    make_camera(CAM1_ENDING, (0.0, -7.0, 1.6),
                (math.radians(90.0), 0.0, 0.0), scene)
    scene.camera = bpy.data.objects[CAM3_ENDING]
    log(f"{ENDING}:四壁 + 天花板 + 5 米窗 + 长桌/烛光 + 月光 + 2 箱 + 3 散箱 + "
        f"玩家 + 两台相机")
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
    # ── 第一百四十六轮:追击多了一条入口(去查看 -> 看见 -> 直接追)────
    chase = _add(tree, "GKNodeAiState", "追击", 80.0, 0.0,
                 gk_state_name="追击", gk_behavior="CHASE",
                 gk_goal=player_name, gk_speed=CHASE_SPEED,
                 gk_stop_distance=1.6, gk_facing="GOAL", gk_in_count=2)
    # 去查看也多了入口(巡逻 -> 听见玻璃声 -> 去查看)
    look = _add(tree, "GKNodeAiState", "去查看", 560.0, 0.0,
                gk_state_name="去查看", gk_behavior="INVESTIGATE",
                gk_speed=INVESTIGATE_SPEED, gk_stop_distance=1.0,
                gk_facing="MOVE", gk_in_count=2,
                # 走到噪音点后左右张望 —— 玩家躲进柜子时"它就在外面"的压迫感
                gk_look_angle=85.0, gk_look_time=2.4)
    start = _add(tree, "GKNodeAiStart", "AI开始", -640.0, 0.0)
    # ── 真潜行(第一百零九轮):『看见』走『AI_看得见』(PIN),而不是转场里
    #   那个『看见目标』快捷(SEE)。两者的区别:快捷只看距离;『AI_看得见』
    #   生成的 `_gk_ai_can_see_3d` 会先扫 `gk_hide_spot` 组 —— 玩家在柜子里
    #   就判看不见。
    eye = _add(tree, "GKNodeAiSee", "看见玩家(潜行)", -150.0, 300.0,
               gk_goal=player_name, gk_distance=SIGHT, gk_angle=SIGHT_ANGLE)
    see = _add(tree, "GKNodeAiTransition", "看见玩家", -160.0, 0.0,
               gk_cond_source="PIN")
    _link(tree, eye, "看得见", see, "条件")
    back = _add(tree, "GKNodeAiTransition", "丢失玩家", 320.0, 0.0,
                gk_cond_source="LOST", gk_goal=player_name, gk_sight=LOST_RANGE)
    calm = _add(tree, "GKNodeAiTransition", "安静下来", 800.0, 0.0,
                gk_cond_source="CALM", gk_ear=0.0, gk_memory=1.5)
    # ── 听觉(第一百四十六轮):踩碎玻璃 -> 去查看 ────────────────────
    # 条件来源选『听见动静』,并且把『只听类型』填成 玻璃 —— 走廊里
    # 只有碎玻璃会发出噪音(脚步不发),所以玩家"贴着墙走"真的能绕开它。
    hear = _add(tree, "GKNodeAiTransition", "听见玻璃声", -160.0, -300.0,
                gk_cond_source="HEARD", gk_ear=18.0, gk_memory=3.5,
                gk_hear_type="玻璃")
    # 去查看时如果撞见玩家,直接转追击(以前只能等它自己安静下来)
    look_eye = _add(tree, "GKNodeAiSee", "去查看也看得见", 380.0, 300.0,
                    gk_goal=player_name, gk_distance=SIGHT,
                    gk_angle=SIGHT_ANGLE)
    look_see = _add(tree, "GKNodeAiTransition", "查看时撞见", 380.0, -160.0,
                    gk_cond_source="PIN")
    _link(tree, look_eye, "看得见", look_see, "条件")
    # ── 为什么这里没有『AI_输出』────────────────────────────────────
    # 它把状态写成**怪物脚本**里的布尔变量;关卡脚本读同名变量,读到的是
    # 另一个脚本里的另一个变量(永远 false)。跨脚本读状态目前没有节点
    # 支持,所以"它靠近 -> 红灯 / 压暗吊灯"改为关卡图按**距离**判定
    # (见 build_corridor_logic 的『警戒轮询』)。
    _add(tree, "GKNodeComment", "状态机说明", -640.0, 480.0,
         gk_text="AI 状态机的转场一路指回『巡逻』 = 正常循环;"
                 "成环的线被 Blender 画红只是提醒,不是错误。"
                 "踩碎玻璃会把它引过来(听觉),躲进柜子它就看不见你")

    _link(tree, start, "开始", patrol, "进入")
    _link(tree, patrol, "转到", see, "上游")
    _link(tree, see, "转到", chase, "进入")
    _link(tree, chase, "转到", back, "上游")
    _link(tree, back, "转到", look, "进入")
    _link(tree, look, "转到", calm, "上游")
    # 巡逻已经有『AI_开始』指着的『进入』,转场退回走『进入 2』
    _link(tree, calm, "转到", patrol, "进入 2")
    # 听觉链路:巡逻 -> 去查看(进入 2);去查看 -> 撞见 -> 追击(进入 2)
    _link(tree, patrol, "转到", hear, "上游")
    _link(tree, hear, "转到", look, "进入 2")
    _link(tree, look, "转到", look_see, "上游")
    _link(tree, look_see, "转到", chase, "进入 2")
    log(f"AI 图『{tree.name}』:{PATROL_STATE} --看见({SIGHT:g})--> 追击 "
        f"--丢失({LOST_RANGE:g})--> 去查看 --安静--> {PATROL_STATE};"
        f"玻璃声({18:g} 米)--> 去查看;去查看撞见 --> 追击")
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


def _wire_pitch_range(logic):
    """把"俯仰夹紧"从写死的 -20~70 改成两个变量,C/V 切换时改范围。

    ── 为什么不能直接把常量改成 -82~82 ──────────────────────────────
    同一张角色图同时服务第一 / 第三人称;第三人称的 -20~70 是**弹簧臂**
    的合理区间(70 = 从高处往下看,再往上是杆子捅穿天花板)。
    所以只能在切机位的那一刻换夹紧范围 —— 这正是"变量 + 两张设置卡"。

    图和预设里那个节点叫『夹住俯仰』(数学_运算 / CLAMP)。这里把它的
    B/C 两个引脚改接『读俯仰下限 / 读俯仰上限』,并在:
      * 『每当_准备』链首写一次初值(第三人称那套);
      * 『藏自身模型』(= 切第一人称)后写 -82 / 82;
      * 『显自身模型』(= 切第三人称)后写回 -20 / 70。
    """
    clamp = logic.nodes.get("夹住俯仰")
    if clamp is None:
        log("! 没找到『夹住俯仰』节点,第一人称俯仰范围没改")
        return
    for pin in ("B", "C"):
        socket = clamp.inputs.get(pin)
        if socket is None:
            continue
        for link in list(socket.links):
            logic.links.remove(link)
    low = _add(logic, "GKNodeGetVar", "读俯仰下限",
               clamp.location[0] - 320.0, clamp.location[1] - 210.0,
               gk_var_name=PITCH_MIN_VAR)
    high = _add(logic, "GKNodeGetVar", "读俯仰上限",
                clamp.location[0] - 320.0, clamp.location[1] - 340.0,
                gk_var_name=PITCH_MAX_VAR)
    _link(logic, low, "值", clamp, "B")
    _link(logic, high, "值", clamp, "C")

    # 初值:插在『每当_准备』->『恢复第一人称?』之间(链条已经存在)
    branch = logic.nodes.get("恢复第一人称?")
    ready = next((n for n in logic.nodes
                  if n.bl_idname == "GKNodeOnReady"), None)
    if ready is not None and branch is not None:
        for link in list(ready.outputs["执行"].links):
            if link.to_node == branch:
                logic.links.remove(link)
        init_low = _add(logic, "GKNodeSetVar", "俯仰下限初值",
                        -1020.0, 1680.0, gk_var_name=PITCH_MIN_VAR,
                        gk_value=PITCH_LIMITS_THIRD[0])
        init_high = _add(logic, "GKNodeSetVar", "俯仰上限初值",
                         -820.0, 1680.0, gk_var_name=PITCH_MAX_VAR,
                         gk_value=PITCH_LIMITS_THIRD[1])
        _link(logic, ready, "执行", init_low, "执行")
        _link(logic, init_low, "完成", init_high, "执行")
        _link(logic, init_high, "完成", branch, "执行")

    for name, limits in (("藏自身模型", PITCH_LIMITS_FIRST),
                         ("显自身模型", PITCH_LIMITS_THIRD)):
        node = logic.nodes.get(name)
        if node is None:
            continue
        set_low = _add(logic, "GKNodeSetVar", f"{name}·下限",
                       node.location[0] + 320.0, node.location[1] - 240.0,
                       gk_var_name=PITCH_MIN_VAR, gk_value=limits[0])
        set_high = _add(logic, "GKNodeSetVar", f"{name}·上限",
                        node.location[0] + 540.0, node.location[1] - 240.0,
                        gk_var_name=PITCH_MAX_VAR, gk_value=limits[1])
        _link(logic, node, "完成", set_low, "执行")
        _link(logic, set_low, "完成", set_high, "执行")
    log(f"视角夹紧改成变量:{PITCH_MIN_VAR}/{PITCH_MAX_VAR}"
        f"(第一人称 {PITCH_LIMITS_FIRST[0]:g}~{PITCH_LIMITS_FIRST[1]:g},"
        f"第三人称 {PITCH_LIMITS_THIRD[0]:g}~{PITCH_LIMITS_THIRD[1]:g})")


def _wire_paper_doll(logic):
    """第一人称时,在屏幕左上角开一个小窗放角色模型(第一百五十七轮)。

    ── 用户原话 ────────────────────────────────────────────────────
    "第一人称下,在屏幕左上角渲染一个角色模型(类似我的世界基岩版的
     布娃娃),丰富视觉并知晓模型动作。"

    第一人称会把 `./Model` 藏掉(不然相机在模型内部,满屏是自己的内壁),
    代价是玩家**看不见自己在做什么动作** —— 跑 / 跳 / 攻击全靠猜。
    小窗里放的是那个模型的**副本**:独立世界 + 自己的灯,每物理帧把
    AnimationPlayer 的动画名和播放位置同步过去,所以动作是实时的。

    『只在隐藏时显示』勾着:第三人称能直接看见自己,小窗自动收起。
    """
    ready = next((n for n in logic.nodes
                  if n.bl_idname == "GKNodeOnReady"), None)
    if ready is None:
        log("! 没找到『每当_准备』,纸娃娃小窗没接上")
        return
    doll = _add(logic, "GKNodePaperDoll", "第一人称纸娃娃",
                -1020.0, 1420.0,
                # ⚠ 必须写 "./Model":只写 "Model" 会被当成**全局名字**,
                #   场景里门板 / 柜子也有各自的 Model 子节点,会拿到别人的。
                gk_target="./Model", gk_size=150, gk_x=24, gk_y=150,
                gk_fov=40.0, gk_zoom=1.0, gk_turn=12.0, gk_only_hidden=True)
    _link(logic, ready, "执行", doll, "执行")
    log("角色图:第一人称纸娃娃小窗(左上 150px,只在模型隐藏时出现)")


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
        # ⚠ 必须排在 `_wire_pref_restore` **之后**:那个函数才建出
        #   『恢复第一人称?』分支,而这里要往"准备 → 分支"之间插初值。
        #   顺序反了的话初值节点找不到分支、不会接进执行链 ——
        #   两个变量停在 0,俯仰被 clamp(0,0) 冻住(实机 P0 limit=0..0)。
        _wire_pitch_range(logic)
        _wire_paper_doll(logic)
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


def build_hud(logic, design, chapter, goal, keys=False, battery=False,
              hint=False, danger=False, health=False):
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
               "hint": None, "danger": None, "health": None}
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
    if danger:
        # ── 第一百四十六轮:危险提示单独一行(顶部居中、鲜红)──────────
        # 为什么不复用上面那个『提示文本』:那条是"操作提示"(躲柜子 /
        # 没电了),会被躲藏进出事件反复清空。追击提示要一直挂在屏幕上,
        # 两条写同一个控件就会互相覆盖 —— 所以各占一行。
        danger_text = _add(design, "GKNodeUiText", "危险文本", 660.0, 1500.0,
                           gk_text="", gk_align="TOP_CENTER", gk_margin=64.0,
                           gk_size=30, gk_color=(1.0, 0.22, 0.18, 1.0))
        handles["danger"] = danger_text.name
    if health:
        # ── 生命(第一百五十七轮)────────────────────────────────────
        # 放在电量下面一行(108),红色 —— 掉血的时候眼睛能立刻找到。
        health_text = _add(design, "GKNodeUiText", "生命文本", 900.0, 1500.0,
                           gk_text="生命:3", gk_x=18.0, gk_y=108.0, gk_size=20,
                           gk_color=(1.0, 0.42, 0.40, 1.0))
        handles["health"] = health_text.name
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
        f"{' + 提示' if hint else ''}{' + 危险提示' if danger else ''}"
        f"{' + 生命' if health else ''}"
        f" + 章节卡(按键提示在主界面)")
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
    clear_hint = _add(logic, "GKNodeUiSet", "清探索提示", 540.0, -700.0,
                      gk_widget=hud["hint"], gk_set_text=True, gk_text="")
    _link(logic, goal_ui, "完成", clear_hint, "执行")

    # ---- 区域任务(第一百五十六轮):进门卫室 / 走到监控台 ----------------
    # 两级、都不是主线阻塞:
    #   ① 踏进门洞 -> 目标改成"拿走监视台上的钥匙" + 一行提示(告诉玩家
    #      这间屋子值得看);
    #   ② 走到监控台前 -> 一句旁白(『界面_字幕』,不阻塞),只播一次。
    # 这就是"区域任务"的最小完整形态:区域触发 + 一次性 + 目标/文本反馈。
    guard_in = _add(logic, "GKNodeOnAreaEntered", "进门卫室", -900.0, -1080.0,
                    gk_area=GUARD_DOOR_AREA, gk_filter=player_name)
    guard_goal = _add(logic, "GKNodeUiSet", "目标:桌上的钥匙", -660.0, -1080.0,
                      gk_widget=hud["goal"], gk_set_text=True,
                      gk_text="目标:拿走监视台上的钥匙")
    guard_hint = _add(logic, "GKNodeUiSet", "提示:监控还亮着", -420.0, -1080.0,
                      gk_widget=hud["hint"], gk_set_text=True,
                      gk_text="监控还亮着 —— 有人刚离开")
    _link(logic, guard_in, "执行", guard_goal, "执行")
    _link(logic, guard_goal, "完成", guard_hint, "执行")

    mon_in = _add(logic, "GKNodeOnAreaEntered", "到监控台", -900.0, -1280.0,
                  gk_area=GUARD_MONITOR_AREA, gk_filter=player_name)
    mon_once = _add(logic, "GKNodeDoOnce", "只看一次", -660.0, -1280.0)
    mon_line = _add(logic, "GKNodeUiSubtitle", "监控旁白", -420.0, -1280.0,
                    # 第一百五十七轮:屏上不再是静态贴图,而是大堂摄像头拍的
                    # 实时画面(朝大门)—— 旁白跟着改成"看得见的东西"。
                    gk_text="画面里,大门外的走廊尽头有什么东西在慢慢移动。",
                    gk_seconds=4.0, gk_speed=22.0)
    _link(logic, mon_in, "执行", mon_once, "执行")
    _link(logic, mon_once, "首次", mon_line, "执行")

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

    amb = _add(logic, "GKNodePlaySound", "环境底噪", -1380.0, 760.0,
               gk_path=AMB_LOBBY, gk_volume=-21.0, gk_2d=False,
               gk_loop=True, gk_name="环境底噪")
    # ── 监控画面(第一百五十七轮,用户第 3 条)────────────────────────
    # 门卫室中间那块屏不再是"贴图",而是一路**实时画面**:摄像头装在大堂
    # 天花板下、朝大门(门后就是走廊),屏上就是它这一刻拍到的。
    # 『机位』填的是 Blender 世界坐标(导出会转成 Godot 的 x, z, -y)。
    live = _add(logic, "GKNodeLiveScreen", "监控画面上屏", -1120.0, 760.0,
                gk_screen=GUARD_SCREEN_LIVE, gk_look=DOOR_PANEL,
                gk_pos=CCTV_POS, gk_width=512, gk_height=288,
                # 55° 在 5 米外把 4 米宽的大门收进画面,还能看到门前地面
                gk_fov=55.0, gk_near=0.2, gk_far=40.0,
                # 偏青绿的色调:老式监控的样子,也把画面和真场景区分开
                gk_tint=(0.74, 0.94, 0.88, 1.0))
    _link(logic, amb, "完成", live, "执行")
    # ── 年久失修的吊灯(第一百五十轮)────────────────────────────────
    # 远看是"光在轻轻晃",近看是整盏灯绕着吊点摆;吱呀声是另一条循环音,
    # 不跟摆动节点耦合 —— 想换音效 / 停掉它,只动『音效_播放』那张卡。
    sway = _add(logic, "GKNodeSway", "吊灯年久失修", -1380.0, 900.0,
                gk_target=PENDANT_SWAY, gk_axis="Z", gk_amplitude=5.0,
                gk_period=4.5, gk_phase=0.0)
    creak = _add(logic, "GKNodePlaySound", "吊灯吱呀", -1380.0, 1040.0,
                 gk_path=CREAK_FILE, gk_volume=-24.0, gk_2d=False,
                 gk_loop=True, gk_name="吊灯吱呀")
    # ── 自定义脚本示例(第一百五十一轮)─────────────────────────────
    # 用户在文本编辑器里写的 GDScript,由『脚本_加载』在开局挂成子节点。
    # 内容见 main() 里的 `GK示例脚本` 文本块;这里只负责"挑它 + 启动"。
    script_demo = _add(logic, "GKNodeScriptLoad", "自定义脚本示例",
                       -1450.0, 1180.0,
                       gk_script_text=bpy.data.texts[DEMO_SCRIPT_NAME],
                       gk_start_method="start")
    # HUD 头节点最后接 ready(它的 await 不能挡前面几句)
    _sequence(logic, ready, "大堂开局顺序", -1160.0, 960.0,
              (battery_full, off_flag, torch_off, amb, sway, creak, save,
               hud_head))
    # 序列最多 8 个出口,自定义脚本示例挂在存档之后(同一个开局阶段)
    _link(logic, save, "完成", script_demo, "执行")
    # 再演示『调用』和『停止』:调用一个方法后立刻停掉实例。
    # ⚠ 这里**不能插『等待』**:生成器把这条链排在 HUD 之前,一个 1 秒的
    #   await 会让目标文字 / 钥匙数 / 章节卡晚 1 秒才建出来(探针当场红)。
    script_call = _add(logic, "GKNodeScriptCall", "自定义脚本调用",
                       -1450.0, 1260.0,
                       gk_script_text=bpy.data.texts[DEMO_SCRIPT_NAME], gk_method="hello")
    script_stop = _add(logic, "GKNodeScriptStop", "自定义脚本停止",
                       -1450.0, 1340.0,
                       gk_script_text=bpy.data.texts[DEMO_SCRIPT_NAME], gk_stop_method="stop")
    _link(logic, script_demo, "完成", script_call, "执行")
    _link(logic, script_call, "完成", script_stop, "执行")
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
                                        "目标:穿过走廊,别被它看见",
                                        hint=True, danger=True, health=True)
    # (暂停菜单 / 按键提示在『主界面』里 —— 这里不用再建任何界面链)

    # 开局:启动 AI + 自动存档
    enable = _add(logic, "GKNodeAiEnable", "启动怪物AI", -900.0, 700.0,
                  gk_tree_name=ai_tree.name, gk_target=ENEMY,
                  gk_state_var=AI_STATE_VAR, gk_debug_print=True)
    save = _add(logic, "GKNodeSaveWrite", "走廊存档", -620.0, 700.0,
                gk_slot="story", gk_save_verify=True)
    # ── 血量初始化(第一百五十七轮)──────────────────────────────────
    # 变量默认是 0,而"怪物还打不打"判的是 `生命 > 0` —— 不显式给满血的话
    # 开局就是 0,玩家一进走廊怪物的攻击链直接不工作(永远打不到人,
    # 也永远死不了)。和手电电量同一个坑。
    hp_init = _add(logic, "GKNodeSetVar", "血量开局满", -620.0, 620.0,
                   gk_var_name=HEALTH, gk_value=HEALTH_MAX)

    # ---- 开场:剧情那套已删(第一百三十九轮,用户第 2 条)----------
    # 用户原话:"现在把所有的剧情选项与剧情显示给删了,只用修理画面与环境的,
    # 这个留到下次。" 所以这里不再有旁白 / 剧情选择 / 合流函数 / 合流字幕,
    # 也不再"开局冻结世界 + 放鼠标"(没有面板要点,世界就别冻)——
    # 章节卡淡出之后这一关直接开跑(`card_end` 后面不再接东西)。

    # ---- 心跳 + 紧张度(每物理帧)----
    # ── 第一百四十六轮:不用『氛围_紧张度』,改自己算 ─────────────────
    # 那个节点量的是「威胁 *到脚本宿主* 的距离」,而这张图挂在**关卡根**上
    # (贴在场景原点)。于是它算的是"怪物离原点多远",和玩家在哪儿毫无关系:
    # 实机探针里怪物贴着玩家,紧张度只有 0.25~0.47(取决于怪物走到哪了)。
    #
    # 正确做法(也是关卡图里该有的写法):用『距离』显式量
    # 玩家 <-> 怪物,再做一次 (远距 - 距离) / 跨度 并夹到 0~1 ——
    # 四张取值卡,语义一眼能看懂。
    beat = _add(logic, "GKNodeOnPhysicsProcess", "物理帧(心跳)", -1060.0,
                -1600.0)
    near_by = _add(logic, "GKNodeDistance", "到怪物距离", -1060.0, -1720.0,
                   gk_a=player_name, gk_b=ENEMY)
    far_gap = _add(logic, "GKNodeMath", "远距减距离", -820.0, -1720.0,
                   gk_op="SUB", gk_a=TENSION_FAR)
    ratio = _add(logic, "GKNodeMath", "除以跨度", -580.0, -1720.0,
                 gk_op="DIV", gk_b=(TENSION_FAR - TENSION_NEAR))
    tension = _add(logic, "GKNodeMath", "夹到 0~1", -340.0, -1720.0,
                   gk_op="CLAMP", gk_b=0.0, gk_c=1.0)
    _link(logic, near_by, "距离", far_gap, "B")
    _link(logic, far_gap, "结果", ratio, "A")
    _link(logic, ratio, "结果", tension, "A")
    # ── 平滑 0.5 秒(1.5 -> 0.5)────────────────────────────────────
    # `gk_smooth` 是**时间常数**(生成的代码是 `t += (目标 - t) * dt/smooth`):
    # 1.5 秒意味着"怪物已经贴到脸上,心跳还要一秒半才跟上"。恐怖游戏里
    # 这个反应太慢。0.5 秒:靠近时心跳在半秒内顶上去,走远也退得干脆。
    heart = _add(logic, "GKNodeHeartbeat", "心跳", -100.0, -1600.0,
                 gk_path=HEART_FILE, gk_slow_interval=1.1,
                 gk_fast_interval=0.30, gk_quiet_db=-19.0, gk_loud_db=-5.0,
                 gk_quiet=0.15, gk_smooth=0.5)
    _link(logic, beat, "执行", heart, "执行")
    _link(logic, tension, "结果", heart, "紧张度")

    # ---- 近身惊吓三连(每物理帧;贴脸 + 冷却,避免每帧都闪)----
    near = _add(logic, "GKNodeOnPhysicsProcess", "物理帧(近身)", -900.0,
                -1950.0)
    dist = _add(logic, "GKNodeDistance", "到怪物距离", -660.0, -1950.0,
                gk_a=player_name, gk_b=ENEMY)
    close = _add(logic, "GKNodeCompare", "贴脸了吗", -420.0, -1950.0,
                 gk_op="LT", gk_b=str(ATTACK_RANGE))
    near_branch = _add(logic, "GKNodeBranch", "贴脸分支", -180.0, -1950.0)
    # ── 血量闸门(第一百五十七轮,用户第 2 条)────────────────────────
    # 用户原话:"敌人攻击逻辑:设定血量,按血量决定是否攻击;没血(失败)时
    #   停止攻击,避免效果叠加。"
    # 贴脸之后**先问一句还有没有血**:
    #   * 有血 -> 打一次(惊吓 + 扣 1 点),冷却 1.6 秒不会每帧都打;
    #   * 没血 -> 什么都不做。没血意味着玩家已经在"倒地 → 黑屏 → 重开"
    #     那 3 秒里了,再打一次就是惊吓 + 倒地 + 重载**三条链一起跑**
    #     (画面抖两遍、音效叠两层、重载排队两次)—— 正是用户说的"效果叠加"。
    hp_read = _add(logic, "GKNodeGetVar", "读生命", -420.0, -2100.0,
                   gk_var_name=HEALTH)
    hp_alive = _add(logic, "GKNodeCompare", "还有血吗", -180.0, -2100.0,
                    gk_op="GT", gk_b="0")
    alive_branch = _add(logic, "GKNodeBranch", "还活着吗", 60.0, -2100.0)
    cool = _add(logic, "GKNodeCooldown", "攻击冷却", 60.0, -1950.0,
                gk_seconds=1.6)
    scare = _add(logic, "GKNodeScare", "惊吓三连", 300.0, -1950.0,
                 gk_strength=20.0, gk_duration=0.5,
                 gk_color=(0.85, 0.08, 0.08, 1.0), gk_alpha=0.55,
                 gk_flash_time=0.35, gk_path=STING_FILE, gk_volume=-3.0)
    hurt = _add(logic, "GKNodeAddVar", "扣一点血", 540.0, -1950.0,
                gk_var_name=HEALTH, gk_amount=-1.0, gk_clamp=True,
                gk_min=0.0, gk_max=HEALTH_MAX)
    # ⚠ 取整必须用 **INT**,不能用 ROUND:`roundf()` 返回的还是 float,
    #   拼进"生命:"后面会显示成 `生命:2.0`(血条踩过;电量同理)。
    hp_round = _add(logic, "GKNodeMath", "生命取整", 540.0, -2120.0,
                    gk_op="INT")
    hp_join = _add(logic, "GKNodeTextJoin", "生命文字", 780.0, -2120.0,
                   gk_a="生命:")
    hp_ui = _add(logic, "GKNodeUiSet", "刷新生命", 780.0, -1950.0,
                 gk_widget=hud["health"], gk_set_text=True)
    hp_zero = _add(logic, "GKNodeCompare", "没血了吗", 1020.0, -2120.0,
                   gk_op="LE", gk_b="0")
    dead_branch = _add(logic, "GKNodeBranch", "要失败了吗", 1020.0, -1950.0)
    _link(logic, dist, "距离", close, "A")
    _link(logic, close, "结果", near_branch, "条件")
    _link(logic, near, "执行", near_branch, "执行")
    _link(logic, hp_read, "值", hp_alive, "A")
    _link(logic, hp_alive, "结果", alive_branch, "条件")
    _link(logic, near_branch, "是", alive_branch, "执行")
    _link(logic, alive_branch, "是", cool, "执行")
    _link(logic, cool, "可以", scare, "执行")
    _link(logic, scare, "完成", hurt, "执行")
    # 刷新生命:'新值' 直接拿去显示,不用再读一次变量
    _link(logic, hurt, "完成", hp_ui, "执行")
    _link(logic, hurt, "新值", hp_round, "A")
    _link(logic, hp_round, "结果", hp_join, "乙")
    _link(logic, hp_join, "结果", hp_ui, "文字")
    _link(logic, hurt, "新值", hp_zero, "A")
    _link(logic, hp_zero, "结果", dead_branch, "条件")
    _link(logic, hp_ui, "完成", dead_branch, "执行")

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

    # ---- 警戒(第一百四十六轮):它靠近 -> 红灯亮 + 吊灯压暗 + 危险提示 ----
    # ── 为什么用"距离"而不是"AI 状态"────────────────────────────────
    # AI 的状态变量住在**怪物自己的脚本**里;关卡脚本读同名变量只会读到
    # 另一个脚本里的另一个变量(永远是 false)。跨脚本读状态得走信号,
    # 而这里要的只是"它快到了"这个**氛围**信号 —— 距离就够,而且更稳:
    # 不管它是在追、在找、还是只是路过,只要贴近了,灯就该变。
    #
    # 三件事一起发生(环境跟着玩法走):
    #   ① 墙上那盏应急红灯亮(走廊里唯一一盏红光);
    #   ② 顶部出现红色危险提示(单独一行,不和躲藏提示抢控件);
    #   ③ 头顶那盏吊灯压暗到 900W —— 灯一暗,玩家自己就会紧张。
    #
    # 0.2 秒轮询一次:UI 文本 + 灯光都是"幂等"操作,重复写没有副作用;
    # 用『每当_物理帧』反而每秒写 60 次,纯属浪费。
    guard = _add(logic, "GKNodeEverySeconds", "警戒轮询", -1200.0, -2650.0,
                 gk_interval=0.2, gk_start_now=True)
    near_dist = _add(logic, "GKNodeDistance", "它离我多远", -960.0, -2560.0,
                     gk_a=player_name, gk_b=ENEMY)
    near_compare = _add(logic, "GKNodeCompare", "逼到 5.5 米内?", -720.0,
                        -2560.0, gk_op="LT", gk_b="5.5")
    near_branch = _add(logic, "GKNodeBranch", "要警戒吗", -480.0, -2650.0)
    red_on = _add(logic, "GKNodeSetLight", "开应急红灯", -240.0, -2740.0,
                  gk_light=ALARM_LIGHT, gk_on=True, gk_fade=0.25)
    # 应急灯不是"常亮",是**脉冲**(警报灯的语义)。『灯光_闪烁』的 ALARM
    # 模式 = 方波;它只改亮度,所以可以先开灯再让它自己脉动。
    # 重复触发不会重复登记(同一个灯只登记一次,见 _gk_light_flicker_start)。
    red_pulse = _add(logic, "GKNodeLightFlicker", "应急灯脉冲",
                     -240.0, -2560.0, gk_light=ALARM_LIGHT, gk_mode="ALARM",
                     gk_speed=1.0, gk_depth=0.8)
    # ⚠ 这里的『亮度』是 **Godot 的 light_energy**,不是 Blender 的瓦数!
    #   导出侧把 Blender 的 W 除以 1000 写进 .tscn(2400W -> 2.4),
    #   逻辑节点是**运行时**直接赋值,填 2400 就是 2400 倍亮度 ——
    #   实机里整条走廊被这一盏灯烧成一片白(排查了两轮才定位到它)。
    #   0.9 = 比 2.4 暗一档半,足够"灯一暗,玩家自己就紧张"。
    # 渐变 0.45 秒:灯"沉"下去,而不是"跳"下去 —— 『灯光_设置』的新旋钮
    lamp_dim = _add(logic, "GKNodeSetLight", "吊灯压暗", 0.0, -2740.0,
                    gk_light=FLICKER_LIGHT, gk_on=True, gk_energy=0.9,
                    gk_fade=0.45)
    danger_on = _add(logic, "GKNodeUiSet", "亮危险提示", 240.0, -2740.0,
                     gk_widget=hud["danger"], gk_set_text=True,
                     gk_text="它就在附近 —— 别出声")
    red_off = _add(logic, "GKNodeSetLight", "关应急红灯", -240.0, -2960.0,
                   gk_light=ALARM_LIGHT, gk_on=False, gk_fade=0.3)
    lamp_up = _add(logic, "GKNodeSetLight", "吊灯恢复", 0.0, -2960.0,
                   gk_light=FLICKER_LIGHT, gk_on=True, gk_energy=6.0,
                   gk_fade=0.6)
    danger_off = _add(logic, "GKNodeUiSet", "清危险提示", 240.0, -2960.0,
                      gk_widget=hud["danger"], gk_set_text=True, gk_text="")

    _link(logic, near_dist, "距离", near_compare, "A")
    _link(logic, near_compare, "结果", near_branch, "条件")
    _link(logic, guard, "到点了", near_branch, "执行")
    _link(logic, near_branch, "是", red_on, "执行")
    _link(logic, red_on, "完成", red_pulse, "执行")
    _link(logic, red_pulse, "完成", lamp_dim, "执行")
    _link(logic, lamp_dim, "完成", danger_on, "执行")
    _link(logic, near_branch, "否", red_off, "执行")
    _link(logic, red_off, "完成", lamp_up, "执行")
    _link(logic, lamp_up, "完成", danger_off, "执行")

    # ---- 没血了 -> 锁输入 -> 倒地 -> 黑屏 -> 重开一局 --------------------
    # 这一关的**失败状态**(第一百五十六轮建,第一百五十七轮改:
    # 以前的判定是"贴到 1.7 米",一碰就死;现在改成"血量扣到 0"才走这条链,
    # 由上面的『要失败了吗』分支接过来)。
    #
    # 四步,每一步都能单独替换:
    #   ① **锁输入**:把**玩家节点**的处理停掉(PROCESS_MODE_DISABLED)。
    #      以前这段 1.9 秒里玩家还能 WASD 走动 —— 倒地的镜头在演,人却自己
    #      走开了,穿帮(用户原话:"被抓的 3 秒里玩家还能走动")。
    #      停的是玩家自己,关卡脚本的等待 / 重载照常跑,世界不会被冻住;
    #   ② 记一个"这一局是重开"的标记(走偏好,**不落盘** —— 下次启动
    #      游戏还是从头开始):下一条命进来时不该再演一遍开场运镜;
    #   ③ 『相机_倒地』:下坠 0.95 米 + 侧翻 78° + 前倾 20°,倒完淡黑。
    #      它会把锁写到相机节点上(meta),弹簧臂自动让位;
    #   ④ 等黑屏铺满再『实例_重载场景』—— 重开的是**当前关卡**,
    #      所以玩家回到走廊入口,AI 状态、灯、箱子全部复位;血量由
    #      『血量开局满』重新给满。
    dead_hint = _add(logic, "GKNodeUiSet", "被抓提示", 1260.0, -1950.0,
                     gk_widget=hud["hint"], gk_set_text=True,
                     gk_text="被它抓住了……")
    dead_lock = _add(logic, "GKNodeSetPaused", "锁住输入", 1500.0, -1950.0,
                     gk_target=player_name, gk_paused=True)
    dead_pref = _add(logic, "GKNodePrefSave", "记下:这是重开", 1740.0, -1950.0,
                     gk_pref_key=RETRY, gk_kind="BOOL", gk_value_bool=True,
                     gk_save_disk=False)
    dead_fall = _add(logic, "GKNodeCameraFall", "镜头倒地", 1980.0, -1950.0,
                     gk_time=1.2, gk_drop=0.95, gk_roll=78.0,
                     gk_pitch=20.0, gk_fade=0.45, gk_black=0.97,
                     gk_restore=False)
    dead_wait = _add(logic, "GKNodeWait", "等黑屏铺满", 2220.0, -1950.0)
    dead_wait.inputs["秒"].default_value = 1.9
    dead_reload = _add(logic, "GKNodeReloadScene", "重开一局", 2460.0, -1950.0,
                       gk_slot="")
    _link(logic, dead_branch, "是", dead_hint, "执行")
    _link(logic, dead_hint, "完成", dead_lock, "执行")
    _link(logic, dead_lock, "完成", dead_pref, "执行")
    _link(logic, dead_pref, "完成", dead_fall, "执行")
    _link(logic, dead_fall, "完成", dead_wait, "执行")
    _link(logic, dead_wait, "完成", dead_reload, "执行")

    # ---- 灯管接触不良(第一百四十八轮:改用插件的新节点)--------------
    # 上一版这条链是**六个节点**拼出来的(定时 0.09s → 随机 → 比较 → 分支
    # → 两个『灯光_设置』),而且"开 / 关"两态出来的是方波,看着像频闪灯。
    # 现在有『灯光_闪烁』:一个节点、逐帧算亮度,模式选『接触不良』
    # (= 高频抖动 + 偶发全灭)—— 这才是坏灯管。
    #
    # 只动这一盏:整条走廊都闪就成了频闪灯,反而廉价。
    tube = _add(logic, "GKNodeLightFlicker", "灯管接触不良",
                -1200.0, -3300.0, gk_light=BROKEN_LAMP, gk_mode="FAULT",
                gk_speed=1.0, gk_depth=0.72)

    # ---- 区域任务:配电间(第一百五十二轮)--------------------------
    # 两级任务:进门口 -> 目标改成"拉闸";走到配电箱前 -> 灯亮 + 目标更新。
    # 用『进入区域』(Area body_entered),天然只触发一次,不用变量锁。
    pit_in = _add(logic, "GKNodeOnAreaEntered", "进配电间", -900.0, -4300.0,
                  gk_area="配电间入口", gk_filter=player_name)
    pit_goal = _add(logic, "GKNodeUiSet", "改目标:拉闸", -660.0, -4300.0,
                    gk_widget=hud["goal"], gk_set_text=True,
                    gk_text="拉下配电箱的闸刀")
    pit_hum = _add(logic, "GKNodeRandomSfx", "配电间电流", -420.0, -4300.0,
                   gk_paths=CREAK_FILE, gk_volume=-9.0, gk_random_pitch=False)
    _link(logic, pit_in, "执行", pit_goal, "执行")
    _link(logic, pit_goal, "完成", pit_hum, "执行")

    pit_on = _add(logic, "GKNodeOnAreaEntered", "到配电箱", -900.0, -4500.0,
                  gk_area="配电间闸刀", gk_filter=player_name)
    # ⚠ 『亮度』是 **Godot 的 light_energy**,不是 Blender 瓦数:
    #   2400W 在导出侧等于 2.4,这里填 2400 会让整间配电间白屏(踩过)。
    pit_lamp = _add(logic, "GKNodeSetLight", "配电间来电", -660.0, -4500.0,
                    gk_light="配电间灯", gk_on=True, gk_energy=2.4)
    pit_done = _add(logic, "GKNodeUiSet", "改目标:供电恢复", -420.0, -4500.0,
                    gk_widget=hud["goal"], gk_set_text=True,
                    gk_text="供电恢复 —— 走廊深处有动静")
    pit_sting = _add(logic, "GKNodeRandomSfx", "送电声", -180.0, -4500.0,
                     gk_paths=STING_FILE, gk_volume=-5.0, gk_random_pitch=False)
    _link(logic, pit_on, "执行", pit_lamp, "执行")
    _link(logic, pit_lamp, "完成", pit_done, "执行")
    _link(logic, pit_done, "完成", pit_sting, "执行")

    # ---- 恐怖事件:断电 / 掉落 / 随机怪声(第一百五十二轮)------------
    # 三个**独立定时器**,节奏错开(26 / 44 / 61 秒),玩家不会连着中招。
    # 断电只关「走廊吊灯3」、黑 0.35 秒 —— 黑太久会被当成游戏崩了;
    # 掉落用储藏间的木箱(声音从侧厅传出来,玩家在走廊只听到"咚")。
    scare_tick = _add(logic, "GKNodeOnTimer", "怪声节拍", -1800.0, -3600.0,
                      gk_seconds=26.0, gk_repeat=True)
    scare_play = _add(logic, "GKNodeRandomSfx", "走廊怪声", -1560.0, -3600.0,
                      gk_paths="|".join((CREAK_FILE, DOOR_FILE, GLASS_FILE)),
                      gk_volume=-6.0, gk_random_pitch=True)
    _link(logic, scare_tick, "执行", scare_play, "执行")

    blackout_tick = _add(logic, "GKNodeOnTimer", "断电节拍", -1800.0, -3800.0,
                         gk_seconds=44.0, gk_repeat=True)
    blackout = _add(logic, "GKNodeBlackout", "灯突然灭", -1560.0, -3800.0,
                    gk_lights="走廊吊灯3", gk_hold=0.35, gk_alpha=0.94,
                    gk_fade=0.06, gk_path=STING_FILE, gk_restore=True)
    _link(logic, blackout_tick, "执行", blackout, "执行")

    drop_tick = _add(logic, "GKNodeOnTimer", "掉落节拍", -1800.0, -4000.0,
                     gk_seconds=61.0, gk_repeat=True)
    drop = _add(logic, "GKNodeDrop", "箱子掉下来", -1560.0, -4000.0,
                gk_target="储藏间木箱1", gk_height=3.2, gk_fall_time=0.55,
                gk_path=GLASS_FILE, gk_volume=-2.0)
    _link(logic, drop_tick, "执行", drop, "执行")

    # ---- 碎玻璃:踩上去 = 有声音 = 怪物过去查看(听觉链路)------------
    # 『声音_发出噪音』只给 AI 听(玩家听到的是上面那条『音效_播放』),
    # 类型填『玻璃』,和 AI 图里那条转场的『只听类型』对上 ——
    # 这样只有玻璃会惊动它,玩家走路本身不会。
    for index, area in enumerate(("碎玻璃1", "碎玻璃2", "碎玻璃3")):
        row = -3400.0 - index * 460.0
        stepped = _add(logic, "GKNodeOnAreaEntered", f"踩到{area}",
                       -1200.0, row, gk_area=area, gk_filter=player_name)
        crash = _add(logic, "GKNodePlaySound", f"玻璃声{index + 1}",
                     -960.0, row, gk_path=GLASS_FILE, gk_volume=-2.0,
                     gk_2d=False)
        noise = _add(logic, "GKNodeNoise", f"玻璃噪音{index + 1}",
                     -720.0, row, gk_loudness=16.0, gk_interval=1.2,
                     gk_target=area, gk_noise_type="玻璃")
        _link(logic, stepped, "执行", crash, "执行")
        _link(logic, crash, "完成", noise, "执行")

    # ---- 走到尽头 → 切到结局 ----
    end = _add(logic, "GKNodeOnAreaEntered", "走到尽头", -900.0, -3600.0,
               gk_area=CORRIDOR_END, gk_filter=player_name)
    change = _add(logic, "GKNodeChangeScene", "去结局", -660.0, -3600.0,
                  gk_scene_path=ENDING_RES)
    _link(logic, end, "执行", change, "执行")

    # 开局:应急红灯先关掉(它导出时是亮的),再启动 AI / 存档 / HUD / 警戒轮询
    alarm_off = _add(logic, "GKNodeSetLight", "应急红灯开局关", -1450.0, 1000.0,
                     gk_light=ALARM_LIGHT, gk_on=False)
    # ── 开场运镜:沿曲线推近看怪物 -> 停留 -> 缩回第三人称(第一百五十轮)
    # 这就是『曲线时间轴示例』那套"沿曲线飞"的通用版:轨道只定义怎么绕,
    # 起点在运行时对齐当前相机;『看向』选怪物,整段都把它保持在画面中心;
    # 推近时视野 60 -> 45,最后 1 秒缓出缩回玩家背后。
    opening = _add(logic, "GKNodeCameraShot", "开场看一眼怪物", -1450.0,
                   840.0, gk_camera=MONSTER_CAM, gk_curve=MONSTER_TRACK,
                   gk_look_target=ENEMY, gk_time=2.5, gk_hold=1.5,
                   gk_return_time=1.0, gk_return_home=True,
                   # 35° 太窄,怪物贴脸时画面只剩一块红;45° 保留身体轮廓。
                   gk_fov_from=60.0, gk_fov_to=45.0, gk_ease="EASE_OUT")
    # 环境底噪:循环音,靠『名字』管理和停止(见音效_播放的循环说明)
    # gk_2d=False = **非定位**的 AudioStreamPlayer:环境底噪是"房间本身的
    # 声音",不该跟着角色位置衰减(勾 2D 会变成 AudioStreamPlayer2D,
    # 在 3D 关卡里听起来忽大忽小,还找不到声源)。
    amb = _add(logic, "GKNodePlaySound", "环境底噪", -1680.0, 760.0,
               gk_path=AMB_CORRIDOR, gk_volume=-20.0, gk_2d=False,
               gk_loop=True, gk_name="环境底噪")
    # ── 重开时不再演开场运镜(第一百五十七轮,用户第 1 条)────────────
    # 用户原话:"第二关失败重开:不要再启动开场动画与场景摄像机动画"。
    # 死亡链里记了一个『重试』标记,这里在运镜之前读一次:
    #   * 没标记(第一次进这一关)-> 照旧推近看怪物;
    #   * 有标记(刚被抓过)    -> 跳过运镜,顺手把标记清掉,玩家立刻能玩。
    # 标记走『偏好_记住』且**不落盘**:重开只在本次游戏内有效。
    retry_read = _add(logic, "GKNodePrefLoad", "取回重开标记", -1150.0, 620.0,
                      gk_pref_key=RETRY, gk_kind="BOOL",
                      gk_default_bool=False)
    retry_branch = _add(logic, "GKNodeBranch", "是重开吗", -1000.0, 620.0)
    retry_clear = _add(logic, "GKNodePrefSave", "清掉重开标记", -850.0, 620.0,
                       gk_pref_key=RETRY, gk_kind="BOOL",
                       gk_value_bool=False, gk_save_disk=False)
    _link(logic, retry_read, "值", retry_branch, "条件")
    # ── 开场运镜排在章节卡**淡出之后**(第一百五十一轮)────────────
    # 以前它和章节卡同时起跑:镜头演到一半全被那块"第二章:走廊"
    # 的红底大标题盖住,用户根本看不出镜头在动。现在挂在 card_end
    # 后面 —— 标题淡出、视野干净了再推近看怪物。
    _link(logic, card_end, "完成", retry_branch, "执行")
    _link(logic, retry_branch, "否", opening, "执行")
    _link(logic, retry_branch, "是", retry_clear, "执行")
    # ⚠ 顺序:定时器(guard / tube)必须排在 **hud_head 之前** ——
    #   HUD 链尾是"章节卡停 3 秒再淡出"的 await,排在它后面的话,
    #   开局前 3 秒警戒和灯闪都不会跑(实机探针里灯一直没闪,就是这原因)。
    _sequence(logic, ready, "走廊开局顺序", -1150.0, 1100.0,
              (alarm_off, amb, guard, tube, enable, hp_init, save, hud_head))
    # (暂停菜单在『主界面』里;Alt 鼠标在角色图里)
    log(f"{CORRIDOR}逻辑:章节/HUD + AI(3 巡逻点) + 心跳/惊吓 + 躲藏 x2 + "
        f"开场对话/选择(合流函数)-> 切 {ENDING_RES}")
    return logic


# ==========================================================================
# 第三关 结局 的逻辑
# ==========================================================================
def build_ending_logic(scene, player, cam_third, cam_first):
    """结局:**自由观察环境**的一关(第一百五十一轮第六轮)。

    用户原话:"在游戏逻辑的结局关卡不需要再有切换关卡等的逻辑了,
    这对关卡环境的持续观察有害。" 所以这一版:
      * 不再冻住世界(`UiPauseGame` 删除)—— 玩家可以继续走、继续看;
      * 不再有『回到开场』/『退出游戏』两个按钮和 `<ChangeScene>` 节点;
      * 章节卡淡出后启动『环境_昼夜交替』:月亮走夜路 → 太阳升起 →
        云量变多 → 黄昏回落,一整天循环,站着就能看完。
    想退出直接按 Esc 用主界面的暂停菜单(那是全局的,不在这张图里)。
    """
    build_character(scene, player, "结局角色", "StoryEndingPlayer",
                    cam_third, cam_first)
    logic = bpy.data.node_groups.new("结局逻辑", "GKGameLogicTree")
    logic.gk_scope = "LEVEL"
    logic.use_fake_user = True
    ready = logic.nodes.new("GKNodeOnReady")
    ready.name = "每当_准备"
    scene.gk_settings.logic_tree = "结局逻辑"
    design = _new_story_ui_tree("结局界面")
    hud_head, _hud, card_end = build_hud(logic, design, "终章:余晖",
                                         "目标:自由观察环境")

    vig = _add(logic, "GKNodeScreenVignette", "结局暗角", -900.0, 800.0,
               gk_amount=0.60, gk_radius=0.50, gk_edge=0.35)

    # ── 章节卡淡出之后:让这一关的夜空自己演变(第一百五十一轮第六轮)──
    # 用户:"设计月亮的位置变化,之后是太阳的出现,和云层的增多"。
    # 环境时钟从 0 点慢慢推到 12 点(45 秒):月亮走一条夜路并落下 ->
    # 6 点后太阳从窗外升起来 -> 天亮后云量 +0.25 自动变多。
    # 停留 25 秒再慢慢回到 0 点(黄昏 → 夜晚),然后一直循环 —— 玩家可以
    # 站在窗前把一整天的天色看完,不用切关卡、世界也不冻。
    alt = _add(logic, "GKNodeEnvAlternate", "结局昼夜交替", 1420.0, 800.0,
               gk_alt_day=12.0, gk_alt_night=0.0,
               gk_alt_fade=45.0, gk_alt_hold=25.0)
    _link(logic, card_end, "完成", alt, "执行")

    amb = _add(logic, "GKNodePlaySound", "环境底噪", -1380.0, 900.0,
               gk_path=AMB_ENDING, gk_volume=-21.0, gk_2d=False,
               gk_loop=True, gk_name="环境底噪")
    _sequence(logic, ready, "结局开局顺序", -1150.0, 1100.0,
              (vig, amb, hud_head))
    # (暂停菜单在『主界面』里;Alt 鼠标在角色图里)
    log(f"{ENDING}逻辑:章节/HUD + 暗角 + 昼夜交替(自由观察、不切关卡)")
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

    # ── 环境底噪(第一百四十七轮)────────────────────────────────────
    # 和 BGM 是**两层**:BGM 是"曲子",底噪是"房间本身的声音"
    # (通风 / 电流 / 远处的风)。做法要点:
    #   * 所有频率都是 **0.125 Hz 的整数倍** —— 8 秒里正好走完整数个周期,
    #     首尾相接不会"咔"一声(这是循环音最基本的条件);
    #   * 两个相差 0.25 Hz 的音叠在一起会有很慢的"打拍",听起来像呼吸;
    #   * 振幅很低(0.05~0.07),它只负责"有空间感",不能盖过 BGM。
    def ambient(freqs, seconds=8.0, amp=0.06):
        total = int(rate * seconds)
        out = []
        for i in range(total):
            time = i / rate
            tone = sum(math.sin(2.0 * math.pi * f * time) for f in freqs)
            breath = 1.0 + 0.15 * math.sin(2.0 * math.pi * 0.25 * time)
            out.append(amp * tone / len(freqs) * breath)
        return out

    _write_wav(AMB_LOBBY, ambient((55.0, 55.25, 82.5), amp=0.055), rate)
    _write_wav(AMB_CORRIDOR, ambient((45.0, 45.25, 67.5, 90.0), amp=0.07), rate)
    _write_wav(AMB_ENDING, ambient((60.0, 60.5, 90.0), amp=0.05), rate)
    log(f"BGM 源:{BGM_DIR}(大堂/走廊/结局)+ 环境底噪 x3")


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
    # ── 吊灯吱呀(第一百五十轮)──────────────────────────────────────
    # 4.5 秒一个循环,里面两次"吱——"；包络在首尾为 0,循环接缝不会"咔"。
    # 频率用 520/780/1170 Hz(整数倍 + 谐波),听起来像金属钩子在木头里磨。
    creak = []
    for index in range(int(rate * 4.5)):
        t = index / rate
        env = 0.0
        for start, span in ((0.35, 0.9), (2.45, 0.9)):
            if start <= t < start + span:
                u = (t - start) / span
                env = max(env, math.sin(math.pi * u) ** 2)
        tone = (math.sin(2.0 * math.pi * 520.0 * t) * 0.5
                + math.sin(2.0 * math.pi * 780.0 * t) * 0.25
                + math.sin(2.0 * math.pi * 1170.0 * t) * 0.10)
        creak.append(0.45 * env * tone)
    _write_wav(CREAK_FILE, creak, rate)
    # 玻璃:几段高频 + 一点噪声,前面带一小段"踩上去"的碎响
    glass = []
    for index in range(int(rate * 0.55)):
        t = index / rate
        env = math.exp(-14.0 * t)
        tone = (math.sin(2.0 * math.pi * 2400.0 * t) * 0.5
                + math.sin(2.0 * math.pi * 3300.0 * t) * 0.35
                + math.sin(2.0 * math.pi * 4150.0 * t) * 0.25)
        # 用正弦叠一个确定性的"沙沙",避免引入 random(重建要幂等)
        hiss = math.sin(2.0 * math.pi * 6100.0 * t + math.sin(90.0 * t)) * 0.25
        glass.append(0.6 * env * (tone + hiss) * 0.5)
    _write_wav(GLASS_FILE, glass, rate)
    log(f"音效源:heartbeat / sting / pickup / door / glass -> {SFX_DIR}")


def save_blend(path):
    if not bpy.app.background:
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return
    blocks = set(bpy.data.objects) | set(bpy.data.meshes) | \
        set(bpy.data.materials) | set(bpy.data.node_groups) | \
        set(bpy.data.cameras) | set(bpy.data.lights) | set(bpy.data.curves) | \
        set(bpy.data.texts)
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
    # ── 自定义脚本示例(第一百五十一轮)────────────────────────────
    # 用户在文本编辑器里写 GDScript,『脚本_加载』挑这个文本块;导出时
    # 自动写到工程的 scripts/custom/。示例只打印几行,证明真的跑起来了。
    example = bpy.data.texts.get(DEMO_SCRIPT_NAME)
    if example is None:
        example = bpy.data.texts.new(DEMO_SCRIPT_NAME)
    example.use_fake_user = True
    example.clear()
    example.write(
        "extends Node\n"
        "# 第一百五十一轮:『脚本_加载』示例。\n"
        "# 这个文本块由 Blender 自动导出到 res://scripts/custom/。\n"
        "\n"
        "func _ready() -> void:\n"
        "    print(\"[GK示例脚本] 已加载\")\n"
        "\n"
        "func start() -> void:\n"
        "    print(\"[GK示例脚本] start\")\n"
        "\n"
        "func hello() -> void:\n"
        "    print(\"[GK示例脚本] hello\")\n"
        "\n"
        "func stop() -> void:\n"
        "    print(\"[GK示例脚本] stop\")\n")
    lobby_player = build_lobby(lobby)
    lobby_door = bpy.data.objects[DOOR_PANEL]
    lobby_torch = bpy.data.objects[TORCH]
    write_physics(lobby, lobby_player, torch=lobby_torch, door=lobby_door,
                  triggers={KEY, DOOR_AREA, GUARD_DOOR_AREA,
                            GUARD_MONITOR_AREA})
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
                  triggers={CABINET_A, CABINET_B, CABINET_C, CORRIDOR_END,
                            "碎玻璃1", "碎玻璃2", "碎玻璃3",
                            "配电间入口", "配电间闸刀"} | set(patrol_points))
    ai_tree = build_ai_tree(PLAYER_CORRIDOR)
    build_corridor_logic(corridor, corridor_player, enemy, ai_tree,
                         (CABINET_A, CABINET_B, CABINET_C), PLAYER_CORRIDOR,
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
    # ── 第 125 轮:再连一条真实的后期链(用户:"打开看看真实的效果")──────
    # ⚠ 必须在 `_save_env_presets` **之后** —— 它开头会
    #   `library.env_presets.clear()`,但不清节点;反过来的话这里
    #   `nodes.clear()` 之后预设还在,两边互不干扰。顺序写死是为了
    #   以后有人调换时能一眼看出"谁清谁"。
    from BlenderGameKit import properties as _props
    _build_post_graph(_props.env_preset_library())

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
        "结局可继续走动观察环境(不切关卡)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
