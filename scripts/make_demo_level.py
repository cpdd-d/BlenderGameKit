"""做一个**能玩的关卡**并导出成 Godot 工程(第 22 轮)。

用户原话:"先做个能玩的关卡吧"。

关卡设计(小但完整,该有的机制都有):
    * 30x30 的地面 + 四面墙(不让人掉出去) + 3 个高台
    * 5 枚金币:走近就吃掉,**分数 +1**,金币消失
    * HUD:左上角显示"金币 0/5"和一句操作提示
    * 集满 5 枚 -> HUD 变成"通关!",相机运镜转场,然后**切换到第二关**
    * 掉出地面(走出边界外) -> **回到出生点**(不惩罚,只是别卡住)
    * **电梯**(第三十一轮):贴中央高台的会动平台 —— 站上去被抬到 2.4 米,
      直接走到高台上(高台那枚金币以前只能跳上去)
    * **载具**(第三十一轮):一辆能开的车 —— 走到旁边按 E 上车,
      W/S 开、A/D 打方向,再按 E 下车

关卡是 .blend 里的**两个 Blender 场景**(用户 2026-10-04):
    * 『关卡1』—— 上面那套完整玩法(逻辑图『玩家逻辑』)
    * 『关卡2』—— 通关后切过去,按 E 回第一关 / 按 Esc 退出(逻辑图『关卡2逻辑』)
导出时每个场景各出一张 .tscn(插件 asset_export.export_all_scenes),
"关卡"在 Blender 里就是**场景**,不是第二棵节点图。
跑法:
    blender -b --python scripts/make_demo_level.py
"""

import math
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "examples", "demo_level")
BLEND_PATH = os.path.join(OUT_DIR, "关卡示例.blend")


def log(message):
    print(f"[关卡示例] {message}")


def apply_settings(scene, pairs):
    """把一批导出设置写进场景的 gk_settings(缺属性 / 只读就跳过)。

    用 getattr / try 包着 —— 老版本 .blend 或精简环境里某个属性可能不存在,
    写设置绝不该让整个脚本崩掉。
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
    """清空场景 —— **不能用 `read_factory_settings`**(踩过的坑)。

    那个操作会把插件一起卸载,之后 `node_groups.new(..., "GKGameLogicTree")`
    就报 `enum "GKGameLogicTree" not found`(因为类型已经注销了)。
    所以老老实实自己删。

    清完把这**唯一剩下的场景**命名为『关卡1』,并把它的逻辑图指到
    『玩家逻辑』(另一个 Blender 场景『关卡2』在后面新建)。返回这个场景。
    """
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for group in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(group)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    scene = bpy.context.scene
    scene.name = "关卡1"
    apply_settings(scene, (("logic_tree", "玩家逻辑"),))
    scene.unit_settings.system = "METRIC"
    scene.frame_start = 1
    scene.frame_end = 40
    return scene


def make_box(name, size, location, material_color=None, scene=None):
    """一个长方体(地面 / 墙 / 高台 / 金币都用它)。

    scene 指定这个物体链接进哪个 Blender 场景(不传 = 当前上下文场景)。
    关卡1 用默认场景,关卡2 用 `build_second_level()` 新建的那个。
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
    if material_color:
        material = bpy.data.materials.get(f"{name}材质")
        if material is None:
            material = bpy.data.materials.new(f"{name}材质")
            material.use_nodes = True
            node = material.node_tree.nodes.get("Principled BSDF")
            if node is not None:
                node.inputs["Base Color"].default_value = material_color
        obj.data.materials.append(material)
    return obj


# ── 关卡尺寸 ────────────────────────────────────────────────────────
ARENA = 30.0                      # 场地边长
WALL_H = 1.2
COIN_POSITIONS = [
    (-9.0, -9.0, 0.9),
    (9.0, -9.0, 0.9),
    (0.0, 0.0, 3.2),              # 中央高台上那枚(要跳上去)
    (11.0, 9.0, 0.9),
    (-11.0, 9.0, 3.4),            # 另一个高台上
]
PLATFORMS = [
    ("中央高台", (6.0, 6.0, 2.4), (0.0, 0.0, 1.2)),
    ("东高台", (4.0, 4.0, 2.6), (11.0, 9.0, 1.3)),
    ("西高台", (4.0, 4.0, 2.6), (-11.0, 9.0, 1.3)),
]
# 出生点:身高 1.6 的方块是**以原点为中心**建的,
# 所以脚底踩地 = z 取半个身高(0.8),再留 0.1 让它轻轻落下
SPAWN = (0.0, -11.0, 0.9)
FALL_LIMIT = -6.0                 # 掉到这个高度以下就送回出生点

# ── 第三十一轮:电梯 + 载具 ─────────────────────────────────────────
# 电梯:贴着中央高台东面(x=3),从地面(上表面 0.4)升到高台面(2.4)——
# 站上去就能被抬到高台上,不用跳。高度走时间轴(往返),所以是
# **AnimatableBody**:被脚本每帧挪位置时会把速度传给站在上面的角色。
ELEVATOR_X = 4.75                 # 中心 x:x ∈ [3.0, 6.5],东边正好贴住高台
ELEVATOR_SIZE = (3.5, 3.0, 0.4)
ELEVATOR_DOWN = 0.2               # 低位中心 z(上表面 = 0.4,和地面走上来齐平)
ELEVATOR_UP = 2.2                 # 高位中心 z(上表面 = 2.4 = 中央高台面)
ELEVATOR_TIME = 2.5               # 单程秒数(往返一轮 5 秒)
# 载具:停在西南空地(x∈[-7.2,-4.8], y∈[-9,-5]),车头朝北(+Y)
VEHICLE_POS = (-6.0, -7.0, 0.6)
VEHICLE_SIZE = (2.4, 4.0, 1.2)
VEHICLE_SPEED = 10.0              # 米/秒(『载具_驾驶』里填的)

# ── 通关后要切过去的第二关(用户 2026-10-04)─────────────────────────
# 这是 **Godot 工程里的 res:// 路径**,不是磁盘路径。第二关是同一个 .blend
# 里的另一个 **Blender 场景**『关卡2』,由插件 `asset_export.export_all_scenes`
# 导出成 `scenes/关卡2.tscn`(不再由 check_level_example.py 手写补文件)。
SECOND_LEVEL_RES = "res://scenes/关卡2.tscn"
# 第二关里按 E 回第一关 —— 第一关同样是这个 .blend 的『关卡1』场景导出的。
FIRST_LEVEL_RES = "res://scenes/关卡1.tscn"

# ── 节点图里的坐标数值填的是 **Godot 坐标**,不是 Blender 坐标 ──────────
# 生成的代码直接把它写进 `Vector3(x, y, z)`,Blender -> Godot 的换算是
#     (x, y, z) -> (x, z, -y)
# 忘了换的话『移动_设置位置』会把角色送到 (0, -11, 0.9) —— 而"掉下去了吗"
# 判的是 y < -6,这个位置**立刻又满足**,于是每一帧都被再送回一次:
# 角色永远卡在地面底下,表现就是"角色自己乱跑 / 位置不对"(实测)。
# 而『移动_按方向』/『在地面吗』这些走的是节点自己,不涉及这里的数值。
SPAWN_GODOT = (SPAWN[0], SPAWN[2], -SPAWN[1])


def build_arena(scene=None):
    make_box("地面", (ARENA, ARENA, 0.4), (0.0, 0.0, -0.2),
             (0.28, 0.30, 0.34, 1.0), scene=scene)
    half = ARENA * 0.5
    wall_size = (ARENA + 0.4, 0.4, WALL_H)
    make_box("北墙", wall_size, (0.0, half, WALL_H * 0.5),
             (0.20, 0.22, 0.26, 1.0), scene=scene)
    make_box("南墙", wall_size, (0.0, -half, WALL_H * 0.5),
             (0.20, 0.22, 0.26, 1.0), scene=scene)
    side = (0.4, ARENA + 0.4, WALL_H)
    make_box("东墙", side, (half, 0.0, WALL_H * 0.5), (0.20, 0.22, 0.26, 1.0),
             scene=scene)
    make_box("西墙", side, (-half, 0.0, WALL_H * 0.5), (0.20, 0.22, 0.26, 1.0),
             scene=scene)
    for name, size, location in PLATFORMS:
        make_box(name, size, location, (0.36, 0.34, 0.30, 1.0), scene=scene)


def build_coins(scene=None):
    coins = []
    for index, (x, y, z) in enumerate(COIN_POSITIONS, start=1):
        coin = make_box(f"金币{index}", (0.7, 0.7, 0.7), (x, y, z),
                        (1.0, 0.82, 0.20, 1.0), scene=scene)
        coin.rotation_euler = (0.0, 0.0, math.radians(45.0 * index))
        coins.append(coin)
    return coins


def build_goal(scene=None):
    """终点门:集满金币之后走过去……这里只做一个视觉标记 + 判定。"""
    return make_box("终点门", (2.0, 0.4, 2.4), (0.0, ARENA * 0.5 - 0.6, 1.2),
                    (0.20, 0.85, 0.45, 1.0), scene=scene)


def build_ride(scene=None):
    """第三十一轮:电梯(会动的平台)+ 载具(能开的车)。

    ── 两者在 Godot 里是**两种不同的体** ──────────────────────────
    * 电梯 = **AnimatableBody**(被时间轴每帧挪位置,站上去**被带走**);
    * 车   = **CharacterBody**(和角色同款,用 velocity + move_and_slide 开,
      撞墙会停)。两个物体的网格都按**资产**导出(各自的 glb + Model
      子节点),所以碰撞体一挪,画面里的模型跟着挪。
    """
    elevator = make_box("电梯", ELEVATOR_SIZE,
                        (ELEVATOR_X, 0.0, ELEVATOR_DOWN),
                        (0.50, 0.56, 0.66, 1.0), scene=scene)
    vehicle = make_box("载具", VEHICLE_SIZE, VEHICLE_POS,
                       (0.72, 0.42, 0.28, 1.0), scene=scene)
    return elevator, vehicle


def build_player(scene=None):
    """方块角色(z=0 是脚底 —— 碰撞体会自动对齐,见导出器)。"""
    player = make_box("示例角色", (1.0, 1.0, 1.6), (0.0, 0.0, 0.8),
                      (0.30, 0.55, 0.95, 1.0), scene=scene)
    player.location = SPAWN
    player.scale = (1.0, 1.0, 1.0)

    # 一段"待机"动作(有动作才像样;`use_fake_user` 否则存盘会丢)
    action = bpy.data.actions.new("待机")
    action.use_fake_user = True
    action.use_frame_range = True
    action.frame_start = 1
    action.frame_end = 40
    player.animation_data_create()
    player.animation_data.action = action
    return player


def build_cameras(scene=None):
    target = scene or bpy.context.scene
    # 相机要在 `objects.new` 时就把 data 给对 —— 先建空物体再塞
    # `Camera` 数据会报 `Object.data expected a Image type, not Camera`(踩过)
    side_data = bpy.data.cameras.new("侧视机位数据")
    side_data.lens = 50.0
    side = bpy.data.objects.new("侧视机位", side_data)
    target.collection.objects.link(side)
    side.location = (0.0, -16.0, 6.0)
    side.rotation_euler = (math.radians(68.0), 0.0, 0.0)

    top_data = bpy.data.cameras.new("俯瞰机位数据")
    top_data.lens = 50.0
    top = bpy.data.objects.new("俯瞰机位", top_data)
    target.collection.objects.link(top)
    top.location = (0.0, 0.0, 24.0)
    top.rotation_euler = (0.0, 0.0, 0.0)

    # ── 转场机位(用户 2026-10-04:"吃完金币 -> 相机动画转场")────────
    # 通关演出的落点:从南边更高更远的地方拍整个场地(60° 俯角)。
    # 它自己没有任何每帧逻辑(不是弹簧臂),所以不会和别的机位抢相机;
    # 『相机_切换』(带过渡)会从**当前相机**平滑推到这里,这就是那段运镜。
    show_data = bpy.data.cameras.new("转场机位数据")
    show_data.lens = 50.0
    showcase = bpy.data.objects.new("转场机位", show_data)
    target.collection.objects.link(showcase)
    showcase.location = (0.0, -24.0, 13.0)
    showcase.rotation_euler = (math.radians(60.0), 0.0, 0.0)
    return side, top


def build_light(scene=None):
    light_data = bpy.data.lights.new("Sun数据", type="SUN")
    light_data.energy = 4.0
    light = bpy.data.objects.new("Sun", light_data)
    (scene or bpy.context.scene).collection.objects.link(light)
    light.location = (6.0, -6.0, 12.0)
    return light


# ==========================================================================
# 节点图
# ==========================================================================
def mark_start(node):
    """给节点标上"玩家逻辑"树需要的位置(纯粹为了看图时好读)。"""
    return node


def build_logic(player, coins, goal, elevator=None, vehicle=None):
    old = bpy.data.node_groups.get("玩家逻辑")
    if old is not None:
        bpy.data.node_groups.remove(old)
    tree = bpy.data.node_groups.new("玩家逻辑", "GKGameLogicTree")
    # ── 没有用户的节点图**存盘时会被丢掉**(踩过的坑,README 第 14 条)──
    # 表现:生成脚本跑完一切正常,重新打开 .blend 一看"节点图数量=0",
    # 导出时报"找不到节点图"。动作也一样,都要 use_fake_user。
    tree.use_fake_user = True

    def add(kind, name, x, y, **props):
        node = tree.nodes.new(kind)
        node.name = name
        node.location = (x, y)
        for key, value in props.items():
            setattr(node, key, value)
        return node

    # ---- 对外端口(第 21 轮加的"真接口")----
    # 视角用的两个变量(鼠标累加进去,弹簧臂读它们)
    for name, value in (("视角偏航", "0"), ("视角俯仰", "12")):
        port0 = tree.gk_ports.add()
        port0.port_name = name
        port0.port_type = "float"
        port0.direction = "out"
        port0.default_value = value
        port0.comment = "第三人称视角(鼠标累加)"

    port = tree.gk_ports.add()
    port.port_name = "金币数"
    port.port_type = "float"
    port.direction = "out"
    port.default_value = "0"
    port.comment = "收集到的金币数量(UI 和别的图都能读)"
    port2 = tree.gk_ports.add()
    port2.port_name = "通关"
    port2.port_type = "bool"
    port2.direction = "out"
    port2.default_value = "false"
    port2.comment = "集满 5 枚金币"

    # ---- 每当_准备:锁鼠标 + 机位 + HUD ----
    ready = add("GKNodeOnReady", "每当_准备", -1400.0, 500.0)
    lock = add("GKNodeMouseMode", "锁定鼠标", -1100.0, 500.0, gk_mode="CAPTURED")
    # 开场只设**视野**(机位交给弹簧臂:它才是这台相机的主人)
    setup = add("GKNodeCameraSetup", "设定初始视野", -800.0, 500.0,
                gk_target="侧视机位", gk_fov=60.0, gk_distance=0.0,
                gk_smooth=0.0)
    # 操作提示那一行(静态,只写一次)。金币数显示**不在这里** —— 它每帧
    # 刷新,见下面的『HUD金币』。UI 文本用的是 gk_x / gk_y(**没有**
    # gk_anchor 那个属性 —— 我一开始猜错了名字)。
    hud_hint = add("GKNodeUiText", "HUD提示", -500.0, 300.0,
                   gk_control="", gk_text=("WASD 移动 · 空格 跳 · 鼠标转视角 · "
                                           "滚轮 缩放 · C/V 切视角 · E 上/下车 · "
                                           "Tab 上帝机(T 时停)"),
                   gk_x=20.0, gk_y=56.0, gk_size=15)
    tree.links.new(ready.outputs["执行"], lock.inputs["执行"])
    tree.links.new(lock.outputs["完成"], setup.inputs["执行"])
    tree.links.new(setup.outputs["完成"], hud_hint.inputs["执行"])

    # ---- 每当_物理帧:移动 + 跳跃 + 相机跟随 + 转视角 ----
    physics = add("GKNodeOnPhysicsProcess", "每当_物理帧", -1400.0, 0.0)

    direction = add("GKNodeInputDirection", "输入_方向", -1400.0, -260.0,
                    gk_up="W", gk_down="S", gk_left="A", gk_right="D",
                    gk_left_action="key_a", gk_right_action="key_d",
                    gk_up_action="key_w", gk_down_action="key_s")
    # ── 第三人称:移动方向跟着相机转(用户 2026-10-04)────────────────
    # 勾上『随相机朝向』:『方向』按**当前相机**的水平朝向变换成世界方向。
    # 这样鼠标转过视角后,按 W 永远是"往镜头前方走",方向感才对;
    # 不勾的话方向写死在世界轴上,视角一转"按前"就变成往原来的方向走。
    move = add("GKNodeMoveDirection", "移动_按方向", -1000.0, 0.0,
               gk_speed=6.0, gk_keep_y=True, gk_follow_camera=True,
               # ── 第三十一轮:能迈上矮台阶 ────────────────────────────
               # 电梯停在一楼时甲板高 0.4 米 —— 不填这个,角色走到电梯前
               # 会被"一堵墙"挡住上不去(Godot 的运动学角色不会自己上台阶)。
               gk_step_height=0.5)
    tree.links.new(physics.outputs["执行"], move.inputs["执行"])
    tree.links.new(direction.outputs["方向"], move.inputs["方向"])

    on_floor = add("GKNodeIsOnFloor", "在地面吗", -1000.0, -300.0)
    jump_key = add("GKNodeKey", "空格", -1000.0, -520.0,
                   gk_key="SPACE", gk_action="jump", gk_trigger="JUST")
    both = add("GKNodeLogic", "能跳吗", -760.0, -400.0, gk_op="AND")
    jump_branch = add("GKNodeBranch", "如果_否则", -500.0, -400.0)
    do_jump = add("GKNodeJump", "起跳", -220.0, -400.0, gk_jump_force=7.0)
    tree.links.new(on_floor.outputs["在地面"], both.inputs["A"])
    tree.links.new(jump_key.outputs["结果"], both.inputs["B"])
    tree.links.new(both.outputs["结果"], jump_branch.inputs["条件"])
    tree.links.new(physics.outputs["执行"], jump_branch.inputs["执行"])
    tree.links.new(jump_branch.outputs["是"], do_jump.inputs["执行"])

    # ── 第三人称:弹簧臂绕角色 360 度,穿模时自动缩短 ────────────────
    # 用户第 24 轮:"第三人称摄像机一般都是绕着角色 360 度旋转,
    # 在有摄像机穿模的情况下就减少"。弹簧臂这个节点本来就是干这个的:
    # 杆子挂在角色身上,鼠标转的是**杆子的朝向**,相机在杆子末端;
    # 射线撞到墙/地面就把杆子缩短 —— "绕 360 度"和"不穿模"一次都有。
    # 鼠标偏航/俯仰要**累加**(每帧转一点),所以用一对"读->加->写"的变量。
    # 杆子的朝向读这两个变量 —— 相机在杆子末端,于是绕着角色转。
    yaw_var = add("GKNodeGetVar", "读偏航", 200.0, -2200.0, gk_var_name="视角偏航")
    yaw_add = add("GKNodeMath", "偏航增量", 440.0, -2200.0, gk_op="ADD",
                  gk_a=0.0, gk_b=0.0, gk_c=0.0)
    yaw_set = add("GKNodeSetVar", "写偏航", 680.0, -2200.0, gk_var_name="视角偏航")
    pitch_var = add("GKNodeGetVar", "读俯仰", 200.0, -2380.0, gk_var_name="视角俯仰")
    pitch_add = add("GKNodeMath", "俯仰增量", 440.0, -2380.0, gk_op="ADD",
                    gk_a=0.0, gk_b=0.0, gk_c=0.0)
    pitch_set = add("GKNodeSetVar", "写俯仰", 680.0, -2380.0, gk_var_name="视角俯仰")
    # 俯仰先夹在 -20~70 度:太低会穿到地面下,太高变俯视,都不好看
    pitch_clamp = add("GKNodeMath", "夹住俯仰", 680.0, -2560.0, gk_op="CLAMP",
                      gk_a=0.0, gk_b=-20.0, gk_c=70.0)

    # ── 相机必须**钉死**在侧视机位上(不能留空 = 自动当前相机)──────────
    # 留空时弹簧臂每物理帧都 `_gk_resolve_camera(self)` 去拿"当前相机",
    # 于是『相机_切换』把俯瞰机位设为当前之后,弹簧臂下一帧就把俯瞰机位
    # 也**夺过来**当跟随相机了 —— 表现是"切到俯瞰没反应 / 俯瞰也在跟人跑"。
    # 钉死之后它只认侧视机位,切到俯瞰时不会去动它。
    arm = add("GKNodeCameraSpringArm", "弹簧臂(第三人称)", 980.0, -1900.0,
              gk_target="",              # 跟着脚本自己(角色)
              gk_camera="侧视机位",       # 钉死:这台相机的主人是这根杆子
              gk_arm=5.0, gk_height=1.4, gk_pitch=12.0, gk_side=0.0,
              # 『位置平滑』的单位是"每秒跟手的倍数"(默认 8.0,越大越跟手),
              # **不是秒**。这里曾经填 0.12(当秒用了)—— 生成出来是
              # clampf(0.12 * delta) ≈ 0.002,相机要好几秒才挪到位,
              # 表现就是"鼠标转视角没反应"(实测)。
              gk_collision=True, gk_radius=0.35, gk_lag=8.0)
    tree.links.new(physics.outputs["执行"], yaw_set.inputs["执行"])
    tree.links.new(yaw_var.outputs["值"], yaw_add.inputs["A"])
    # ★ 这根线漏了:没有它,『写偏航』的『值』引脚空着 -> 用节点上的默认值
    # 100.0 -> 偏航每帧被写成 100 度(实测就是这么发现的)
    tree.links.new(yaw_add.outputs["结果"], yaw_set.inputs["值"])
    tree.links.new(yaw_set.outputs["完成"], pitch_set.inputs["执行"])
    tree.links.new(pitch_var.outputs["值"], pitch_add.inputs["A"])
    tree.links.new(pitch_add.outputs["结果"], pitch_clamp.inputs["A"])
    tree.links.new(pitch_clamp.outputs["结果"], pitch_set.inputs["值"])
    tree.links.new(yaw_var.outputs["值"], arm.inputs["偏航"])
    tree.links.new(pitch_var.outputs["值"], arm.inputs["俯仰"])
    tree.links.new(physics.outputs["执行"], arm.inputs["执行"])

    # ── 俯瞰机位也要能跟随 + 旋转(用户 2026-10-04:"俯视图……动不了")──
    # 原来俯瞰机位是一台**钉死在 (0,0,24) 的静态相机**:场地边长 30,它在
    # 24 米高、50mm 镜头下只能看见 ±8.6 米,而角色出生在 z=11 —— 根本
    # **不在画面里**,看起来就是"俯视图里看不到角色、按 WASD 像没反应";
    # 而且鼠标只驱动侧视那根杆子,俯瞰机位纹丝不动。
    # 现在给俯瞰机位配**它自己的**弹簧臂:跟着角色、俯仰抬到 80°(近乎
    # 垂直俯视)、偏航接同一个鼠标累加量 —— 切过去就能跟着跑、鼠标也能转。
    # (每根杆子的成员变量带节点后缀,互不覆盖;见 codegen 的 _spring_tag)
    top_arm = add("GKNodeCameraSpringArm", "弹簧臂(俯瞰)", 980.0, -2560.0,
                  gk_target="",
                  gk_camera="俯瞰机位",
                  gk_arm=16.0, gk_height=0.0, gk_pitch=80.0, gk_side=0.0,
                  gk_collision=False, gk_radius=0.35, gk_lag=8.0)
    tree.links.new(yaw_var.outputs["值"], top_arm.inputs["偏航"])
    tree.links.new(arm.outputs["完成"], top_arm.inputs["执行"])

    # 鼠标转视角:像素位移 x 每像素多少度(和 2.5D 示例同一套)
    look = add("GKNodeMouseMotion", "鼠标移动量", -1400.0, -800.0)
    split = add("GKNodeVectorSplit", "拆成 XY", -1150.0, -800.0)
    # 灵敏度 = 每像素多少度(不要乘帧间隔,否则慢 60 倍)
    # 灵敏度 = 每像素多少度。用乘法节点当"常量":A=0.25, B=1.0
    # (A 写 0 的话结果恒为 0 —— 实测鼠标怎么动视角都不转)
    delta = add("GKNodeMath", "灵敏度", -1000.0, -700.0, gk_op="MUL",
                gk_a=0.25, gk_b=1.0, gk_c=0.0)
    # ── 左右/上下灵敏度 ─────────────────────────────────────────────
    # A、B 两端**都被连线覆盖**(A = 灵敏度常量,B = 鼠标位移),所以节点上
    # 填的 gk_a / gk_b 是死值 —— 别在这儿改符号,改了也不生效(实测被坑过)。
    # "视角旋转方向"的符号统一由 codegen 的弹簧臂数学负责(俯仰/偏航取负),
    # 这里两个 MUL 只要保证"位移 × 灵敏度"就行。
    yaw = add("GKNodeMath", "左右灵敏度", -780.0, -760.0, gk_op="MUL",
              gk_a=1.0, gk_b=1.0, gk_c=0.0)
    pitch = add("GKNodeMath", "上下灵敏度", -780.0, -900.0, gk_op="MUL",
                gk_a=1.0, gk_b=1.0, gk_c=0.0)
    tree.links.new(look.outputs["移动量"], split.inputs["向量"])
    # 鼠标位移 × 灵敏度 -> 直接进"偏航/俯仰增量"的 B 端
    tree.links.new(delta.outputs["结果"], yaw.inputs["A"])
    tree.links.new(split.outputs["X"], yaw.inputs["B"])
    tree.links.new(delta.outputs["结果"], pitch.inputs["A"])
    tree.links.new(split.outputs["Y"], pitch.inputs["B"])
    tree.links.new(yaw.outputs["结果"], yaw_add.inputs["B"])
    tree.links.new(pitch.outputs["结果"], pitch_add.inputs["B"])
    # 『相机_旋转』(turn)不再需要 —— 它改的是相机自己的朝向,
    # 而弹簧臂每帧 look_at 会覆盖掉(这就是"鼠标没反应"的原因)。
    # 现在鼠标驱动的是杆子,相机跟着杆子转。

    # ── 滚轮缩放视野(用户 2026-10-04:"滑动滚轮视角不变")──────────
    # 关卡示例最早**根本没有滚轮节点**(只有 2.5D 示例有),所以滚轮当然
    # 一点反应都没有。照 2.5D 那套补上:读当前视野 -> 加/减"滚轮 × 每格
    # 度数" -> 写回去,0.15 秒平滑,并夹在 20~90 度(不夹的话能一路推到
    # 画面变形甚至负数)。滚轮往上(远离自己)= 推近:每格 -4 度 fov。
    wheel = add("GKNodeMouseWheel", "滚轮", -1400.0, -1640.0)
    wheel_step = add("GKNodeMath", "滚轮转视野", -1150.0, -1640.0,
                     gk_op="MUL", gk_b=-4.0)
    cam_view = add("GKNodeCameraStatus", "当前视野", -1400.0, -1820.0)
    view_new = add("GKNodeMath", "算出新视野", -900.0, -1700.0,
                   gk_op="ADD")
    view_zero = add("GKNodeConstFloat", "零", -1400.0, -1980.0, gk_value=0.0)
    wheel_moved = add("GKNodeCompare", "滚轮动了吗", -1150.0, -1980.0,
                      gk_op="NE")
    wheel_branch = add("GKNodeBranch", "如果_否则", -900.0, -1980.0)
    apply_view = add("GKNodeCameraZoom", "改视野", -640.0, -1700.0,
                     gk_time=0.15, gk_min_value=20.0, gk_max_value=90.0)
    tree.links.new(wheel.outputs["滚轮"], wheel_step.inputs["A"])
    tree.links.new(cam_view.outputs["视野"], view_new.inputs["A"])
    tree.links.new(wheel_step.outputs["结果"], view_new.inputs["B"])
    tree.links.new(view_new.outputs["结果"], apply_view.inputs["数值"])
    tree.links.new(wheel.outputs["滚轮"], wheel_moved.inputs["A"])
    tree.links.new(view_zero.outputs["值"], wheel_moved.inputs["B"])
    tree.links.new(wheel_moved.outputs["结果"], wheel_branch.inputs["条件"])
    # 和切视角一样从**事件**单独拉一条线 —— 挂在别的语句后面的话,
    # 就只有"上一步刚好执行"时滚轮才生效(2.5D 示例踩过这个坑)。
    tree.links.new(physics.outputs["执行"], wheel_branch.inputs["执行"])
    tree.links.new(wheel_branch.outputs["是"], apply_view.inputs["执行"])

    # 切视角:C 俯瞰 / V 侧视
    # ── C / V:『相机_切换』把当前相机交给俯瞰机位 / 侧视机位 ──────────
    # 踩过的坑 1:这里以前放的是**第二个、第三个弹簧臂**(拉远 / 收回)。
    #   弹簧臂会把自己那段每帧逻辑**无条件**注册进 physics_hooks,三条
    #   杆子一起跑,最后注册的那条(拉远 55°)每帧覆盖前面 —— 结果就是
    #   "鼠标转视角没反应、C/V 也切不动"。
    # 踩过的坑 2:单靠『相机_切换』也不行 —— 弹簧臂留空(自动当前相机)时
    #   每帧都会 `_gk_resolve_camera` 把当前相机夺过来当跟随相机。
    # 所以正解是两条一起改:相机的主人**只有一根杆子**、而且**钉死在侧视
    # 机位**上;俯瞰/侧视的切换交给『相机_切换』(它是换"当前相机",不是
    # 抢同一台相机的位置),两者不再打架。
    # 过渡填 0(硬切):侧视机位每帧被杆子摆在跟随位,硬切过去正好接得上,
    # 不会出现"先飘回旧位置再被杆子拽回来"的跳变。
    view_key = add("GKNodeKey", "按 C", -1400.0, -1100.0,
                   gk_key="C", gk_action="view_top", gk_trigger="JUST")
    view_branch = add("GKNodeBranch", "如果_否则", -1000.0, -1100.0)
    to_top = add("GKNodeCameraSetCurrent", "切到俯瞰", -700.0, -1100.0,
                 gk_target="俯瞰机位", gk_blend=0.0)
    tree.links.new(physics.outputs["执行"], view_branch.inputs["执行"])
    tree.links.new(view_key.outputs["结果"], view_branch.inputs["条件"])
    tree.links.new(view_branch.outputs["是"], to_top.inputs["执行"])

    back_key = add("GKNodeKey", "按 V", -1400.0, -1320.0,
                   gk_key="V", gk_action="view_side", gk_trigger="JUST")
    back_branch = add("GKNodeBranch", "如果_否则", -1000.0, -1320.0)
    to_side = add("GKNodeCameraSetCurrent", "切回侧视", -700.0, -1320.0,
                  gk_target="侧视机位", gk_blend=0.0)
    tree.links.new(physics.outputs["执行"], back_branch.inputs["执行"])
    tree.links.new(back_key.outputs["结果"], back_branch.inputs["条件"])
    tree.links.new(back_branch.outputs["是"], to_side.inputs["执行"])

    # ---- 金币:『进入区域』触发器,碰到就吃掉(用户 2026-10-04 第 6 条)----
    # 以前靠"每物理帧算一次『到金币的距离 < 1.3 吗』"来判定。金币是 Area3D
    # (`write_export_settings` 里给『金币*』设了 body_type="Area3D"),
    # 角色是 CharacterBody3D,所以金币的 `body_entered` 信号会在角色身体
    # 真的碰到金币那一帧触发一次 —— 比"每帧比距离"更直接,也不会因为
    # 穿模 / 帧率漏判。
    #
    # ── 为什么每枚金币各走一条完整链(踩过的坑)──────────────────────
    # 用户 2026-10-04:"吃完金币没有触发场景动画,且另一个关卡没打开"。
    # 根因:原来把 5 枚金币的『吃掉金币N·完成』都接到**同一个**执行输入
    # 引脚 —— 而 **Blender 一个执行输入引脚只能接一根线**,后连的顶掉前连的,
    # 只有最后一枚金币留在加分链上。金币数永远到不了 5,通关演出 / 切关卡
    # 自然都不触发。
    # 正解:每枚金币**各自一条链**(触发器 -> 销毁 -> 读分数 -> 加一 ->
    # 写回 -> 刷新 HUD),谁也不挤谁的引脚;读/加/写虽然是表达式节点,
    # 但每条链各有一套(5 枚,重复得起的量),互不干扰。
    # 分数变量仍是同一个『金币数』(对外端口),所以每吃一枚都 +1。
    coin_root = -1400.0
    for index, coin in enumerate(coins, start=1):
        row = -1700.0 - index * 260.0
        # gk_area = 要监听的 Area(金币本体);gk_filter = 只认角色进来
        trigger = add("GKNodeOnAreaEntered", f"进入区域{index}",
                      coin_root, row, gk_area=coin.name, gk_filter="示例角色")
        kill = add("GKNodeQueueFree", f"吃掉金币{index}",
                   coin_root + 320.0, row, gk_target=coin.name)
        read_coin = add("GKNodeGetVar", f"读金币数{index}",
                        coin_root + 620.0, row - 90.0, gk_var_name="金币数")
        plus_coin = add("GKNodeMath", f"加一{index}",
                        coin_root + 860.0, row - 90.0, gk_op="ADD", gk_b=1.0)
        write_coin = add("GKNodeSetVar", f"写回分数{index}",
                         coin_root + 1100.0, row, gk_var_name="金币数")
        tree.links.new(trigger.outputs["执行"], kill.inputs["执行"])
        tree.links.new(kill.outputs["完成"], write_coin.inputs["执行"])
        tree.links.new(read_coin.outputs["值"], plus_coin.inputs["A"])
        tree.links.new(plus_coin.outputs["结果"], write_coin.inputs["值"])

    # ---- HUD 金币数:每物理帧把"金币 N/5"刷出来 -----------------------
    # 只用一个文本控件。『界面_文本』**每个节点都会 `Label.new()` 一个**
    # 属于自己的控件(见 codegen/services.py 的 _stmt_ui_text),所以给 5 条
    # 拾取链各配一个的话,5 个 Label 会全叠在同一坐标上、糊成一团(实测)。
    # 金币数是全局变量,每帧读一次显示即可,跟"是哪一枚被吃掉"无关。
    hud_read = add("GKNodeGetVar", "读金币数(HUD)", 200.0, -2980.0,
                   gk_var_name="金币数")
    hud_head = add("GKNodeTextJoin", "拼金币前缀", 440.0, -2980.0,
                   gk_a="金币 ")
    hud_tail = add("GKNodeTextJoin", "拼金币总数", 680.0, -2980.0,
                   gk_b="/5")
    hud_coin = add("GKNodeUiText", "HUD金币", 920.0, -2980.0,
                   gk_control="", gk_text="金币 0/5",
                   gk_x=20.0, gk_y=18.0, gk_size=22)
    tree.links.new(physics.outputs["执行"], hud_coin.inputs["执行"])
    tree.links.new(hud_read.outputs["值"], hud_head.inputs["乙"])
    tree.links.new(hud_head.outputs["结果"], hud_tail.inputs["甲"])
    tree.links.new(hud_tail.outputs["结果"], hud_coin.inputs["文字"])

    # ---- 掉出去 -> 回出生点 ----
    # 判定用"自己的 Y 小于 -6 吗"(比射线稳,也不依赖地图边界):
    #   『取值_位置』-> 『逻辑_比较』(< -6)-> 『如果_否则』-> 『移动_设置位置』
    my_pos = add("GKNodeGetPosition", "读自己的位置", -1400.0, -2800.0,
                 gk_target="", gk_global=True)
    below = add("GKNodeCompare", "掉下去了吗", -1100.0, -2800.0,
                gk_op="LT", gk_b=str(FALL_LIMIT))
    fell_branch = add("GKNodeBranch", "如果_否则", -820.0, -2800.0)
    reset = add("GKNodeSetPosition", "送回出生点", -520.0, -2800.0,
                gk_target="", gk_x=SPAWN_GODOT[0], gk_y=SPAWN_GODOT[1],
                gk_z=SPAWN_GODOT[2])
    tree.links.new(my_pos.outputs["Y"], below.inputs["A"])
    tree.links.new(below.outputs["结果"], fell_branch.inputs["条件"])
    tree.links.new(physics.outputs["执行"], fell_branch.inputs["执行"])
    tree.links.new(fell_branch.outputs["是"], reset.inputs["执行"])

    # ---- 通关:金币数 >= 5 -> 端口『通关』= true + HUD 改字 ----
    read_for_win = add("GKNodeGetVar", "读金币数(通关)", -700.0, -2700.0,
                       gk_var_name="金币数")
    win_check = add("GKNodeCompare", "集满了吗", -420.0, -2700.0,
                    gk_op="GE", gk_b=str(len(coins)))
    win_set = add("GKNodeLogicOutput", "通关端口", -160.0, -2700.0,
                  gk_output_name="通关", gk_value=True)
    win_text = add("GKNodeUiText", "通关提示", 120.0, -2700.0,
                   gk_control="", gk_text="通关!按 Esc 退出",
                   gk_x=460.0, gk_y=200.0, gk_size=30)
    win_branch = add("GKNodeBranch", "如果_否则", -420.0, -2950.0)
    # 通关判定改由『每当_物理帧』驱动(用户 2026-10-04):拾取链现在每枚
    # 金币各走各的,没有"唯一的『写回分数·完成』"可挂了;而真正的条件本来
    # 就是"金币数 >= 5",跟"是哪一枚被吃掉"无关 —— 每帧读一次金币数判定,
    # 条件只由金币数决定,不会被某个拾取链的引脚绑死。
    tree.links.new(read_for_win.outputs["值"], win_check.inputs["A"])
    tree.links.new(physics.outputs["执行"], win_branch.inputs["执行"])
    tree.links.new(win_check.outputs["结果"], win_branch.inputs["条件"])
    tree.links.new(win_branch.outputs["是"], win_set.inputs["执行"])
    tree.links.new(win_set.outputs["完成"], win_text.inputs["执行"])

    # ---- 通关演出:相机动画转场 -> 等它播完 -> 切换到第二关 ----
    # 用户 2026-10-04:"吃完金币,进行场景摄像机动画转场,然后打开另一个关卡"。
    # 三步全部用现成零件拼(没有为它加任何新节点):
    #   1.『相机_切换』带 1.4 秒过渡 = 丝滑推镜(从当前相机推到转场机位)
    #   2.『等待_秒』给运镜留出时间 —— 它是 await,**故意放在这条链的最末尾**:
    #      await 会把"这一次 _physics_process 调用"剩下的部分挂起,而这条链
    #      后面没有别的要跑的东西(切场景就是终点),所以不影响其它每帧逻辑
    #   3.『场景_切换关卡』换到第二关
    # 第二关是同一个 .blend 里的『关卡2』场景,由插件的
    # `asset_export.export_all_scenes` 导出成 scenes/关卡2.tscn(不再手写补文件)。
    to_showcase = add("GKNodeCameraSetCurrent", "转场推镜", 380.0, -2700.0,
                      gk_target="转场机位", gk_blend=1.4)
    hold = add("GKNodeWait", "等运镜播完", 640.0, -2700.0)
    # 『等待_秒』的时长是引脚默认值(节点上没有 gk_seconds 属性),得写引脚
    hold.inputs["秒"].default_value = 1.6
    change_scene = add("GKNodeChangeScene", "切到第二关", 900.0, -2700.0,
                       gk_scene_path=SECOND_LEVEL_RES)
    tree.links.new(win_text.outputs["完成"], to_showcase.inputs["执行"])
    tree.links.new(to_showcase.outputs["完成"], hold.inputs["执行"])
    tree.links.new(hold.outputs["完成"], change_scene.inputs["执行"])

    # ---- 第三十一轮:电梯(时间轴往返抬人)+ 载具(E 上/下车 + 驾驶)----
    # 电梯的接法和示例里"抬门"一模一样:时间轴『更新』每帧把轨道值写到
    # 电梯的 Y 上(X / Z 用它**当前**坐标,所以只上下动);往返模式永不结束。
    if elevator is not None and vehicle is not None:
        lift_timeline = add("GKNodeTimeline", "电梯时间轴", 0.0, -3400.0,
                            gk_time=ELEVATOR_TIME, gk_mode="PINGPONG",
                            gk_track_count=1,
                            gk_track1_from=ELEVATOR_DOWN,
                            gk_track1_to=ELEVATOR_UP)
        tree.links.new(hud_hint.outputs["完成"], lift_timeline.inputs["执行"])
        lift_here = add("GKNodeGetPosition", "取电梯位置", -560.0, -3400.0,
                        gk_target=elevator.name, gk_global=True)
        lift_push = add("GKNodeSetPosition", "推电梯", -220.0, -3400.0,
                        gk_target=elevator.name)
        tree.links.new(lift_timeline.outputs["轨道1值"], lift_push.inputs["Y"])
        tree.links.new(lift_here.outputs["X"], lift_push.inputs["X"])
        tree.links.new(lift_here.outputs["Z"], lift_push.inputs["Z"])
        tree.links.new(lift_timeline.outputs["更新"], lift_push.inputs["执行"])

        # 载具:『载具_驾驶』接在角色移动**后面** —— 钉座位那一下必须排在
        # 角色被移动之后,不然会被角色的移动覆盖掉。
        drive = add("GKNodeDriveVehicle", "驾驶载具", 700.0, -3400.0,
                    gk_vehicle=vehicle.name, gk_speed=VEHICLE_SPEED)
        tree.links.new(move.outputs["完成"], drive.inputs["执行"])
        tree.links.new(direction.outputs["方向"], drive.inputs["方向"])
        # 一个键上车/下车:**两层分支**(先判"按了 E 吗",再判"在开吗")。
        # ⚠ 不能写成"E 且 驾驶中 → 如果_否则":那个『否』口没按 E 的帧
        # 也会走,刚下车就被下一帧塞回座位(第三十轮探针实测踩过)。
        key_e = add("GKNodeKey", "E 键", -1400.0, -3700.0, gk_key="E",
                    gk_action="key_e", gk_trigger="JUST")
        pressed = add("GKNodeBranch", "按了 E?", -1150.0, -3700.0)
        driving = add("GKNodeBranch", "在开吗?", -900.0, -3700.0)
        leave_car = add("GKNodeExitVehicle", "下车", -620.0, -3600.0,
                        gk_vehicle=vehicle.name, gk_exit=(2.0, 0.5, 0.0))
        enter_car = add("GKNodeEnterVehicle", "上车", -620.0, -3820.0,
                        gk_vehicle=vehicle.name, gk_seat=(0.0, 2.0, 0.0))
        tree.links.new(key_e.outputs["结果"], pressed.inputs["条件"])
        tree.links.new(drive.outputs["完成"], pressed.inputs["执行"])
        tree.links.new(pressed.outputs["是"], driving.inputs["执行"])
        tree.links.new(drive.outputs["驾驶中"], driving.inputs["条件"])
        tree.links.new(driving.outputs["是"], leave_car.inputs["执行"])
        tree.links.new(driving.outputs["否"], enter_car.inputs["执行"])

    # ---- Esc 退出 ----
    quit_key = add("GKNodeKey", "Esc", -1400.0, -3100.0,
                   gk_key="ESC", gk_action="quit_game", gk_trigger="JUST")
    quit_branch = add("GKNodeBranch", "如果_否则", -1000.0, -3100.0)
    do_quit = add("GKNodeQuitGame", "退出游戏", -700.0, -3100.0)
    tree.links.new(physics.outputs["执行"], quit_branch.inputs["执行"])
    tree.links.new(quit_key.outputs["结果"], quit_branch.inputs["条件"])
    tree.links.new(quit_branch.outputs["是"], do_quit.inputs["执行"])

    return tree


def build_second_logic(scene, player):
    """第二关的逻辑:角色预设(移动 / 跳跃 / 相机)+ 回第一关 + Esc 退出。

    ── 为什么改成"角色预设"(用户 2026-10-06 实测报的)────────────────
    第二关当年是当**通关页**做的:一棵只有标题 + 两个按键的图,角色也没配
    移动 —— 所以切过来之后"移动 / 视角 / 上帝机 / Esc 全失效"(实测确认:
    角色在场景里是个 StaticBody,图里也没有任何移动节点;上帝机开关还写死
    False)。现在直接拿**角色预设**(和物理测试场同一套)生成,再把
    "按 E 回第一关 / 按 Esc 退出"两条接上去 —— 第二关是一张真能走的关卡。
    """
    from BlenderGameKit import presets

    logic, _anim, notes = presets.create_character_preset(
        scene, character=player,
        logic_name="关卡2逻辑",
        class_name="Level2Player",
        with_animation=False,
        with_godot_camera=True,          # Tab 上帝机(第二关也要能飞着看)
        camera_side="第二关机位", camera_top="第二关俯瞰",
    )
    for note in notes:
        log(f"关卡2 预设: {note}")

    def add(kind, name, x, y, **props):
        node = logic.nodes.new(kind)
        node.name = name
        node.location = (x, y)
        for key, value in props.items():
            setattr(node, key, value)
        return node

    def link(source, source_pin, target, target_pin):
        logic.links.new(source.outputs[source_pin], target.inputs[target_pin])

    # ---- 标题(挂在预设的『设定初始视野』后面,链子不断)----
    ready_tail = logic.nodes.get("设定初始视野")
    title = add("GKNodeUiText", "第二关标题", -600.0, 500.0,
                gk_control="",
                gk_text=("第二关 —— 这里也能走动了:WASD / 空格 / 鼠标转视角 / "
                         "C·V 切机位 / Tab 上帝机(T 时停);"
                         "按 E 回第一关,按 Esc 退出"),
                gk_x=20.0, gk_y=18.0, gk_size=20)
    if ready_tail is not None:
        link(ready_tail, "完成", title, "执行")

    # ---- 每当_物理帧:按 E 回第一关 / 按 Esc 退出 ----
    physics = logic.nodes.get("每当_物理帧")

    back_key = add("GKNodeKey", "按键E", -400.0, -1600.0,
                   gk_key="E", gk_action="back_level", gk_trigger="JUST")
    back_branch = add("GKNodeBranch", "如果_否则", -180.0, -1600.0)
    back_scene = add("GKNodeChangeScene", "切回第一关", 60.0, -1600.0,
                     gk_scene_path=FIRST_LEVEL_RES)
    link(physics, "执行", back_branch, "执行")
    link(back_key, "结果", back_branch, "条件")
    link(back_branch, "是", back_scene, "执行")

    quit_key = add("GKNodeKey", "按键Esc", -400.0, -1800.0,
                   gk_key="ESC", gk_action="quit_game", gk_trigger="JUST")
    quit_branch = add("GKNodeBranch", "如果_否则", -180.0, -1800.0)
    do_quit = add("GKNodeQuitGame", "退出游戏", 60.0, -1800.0)
    link(physics, "执行", quit_branch, "执行")
    link(quit_key, "结果", quit_branch, "条件")
    link(quit_branch, "是", do_quit, "执行")

    log(f"关卡2 逻辑:共 {len(logic.nodes)} 个节点 / {len(logic.links)} 条连线"
        f"(移动 / 跳跃 / 相机 / 上帝机 + 回第一关 + Esc 退出)")
    return logic


def write_export_settings(scene, player):
    """把这一关的导出设置 + 每个物体的物理类型写好。

    scene  —— 这一关的 Blender 场景(『关卡1』/『关卡2』)
    player —— 这一关挂游戏逻辑脚本的物体(『示例角色』/『第二关角色』)

    ── 为什么要按场景分开写(用户 2026-10-04)──────────────────────
    一个 .blend 里现在有两个 Blender 场景,写设置时**只能看这一关自己的
    物体**(`scene.objects`)。用 `bpy.data.objects` 会连另一关的一起改 ——
    给『第二关角色』勾 script_target 时,`else` 分支会把『示例角色』顺手
    改成 StaticBody3D,第一关的角色就站不住了。
    场景级设置(class_name / 上帝摄像机)同理:**关卡1 在这里写全**,
    关卡2 的 `build_second_level()` 自己写好(Level2Player、开上帝摄像机),
    这里**不要覆盖**,否则会被第一关的 LevelPlayer 顶掉。
    """
    # 场景级设置:只给第一关写全;第二关交给 build_second_level()。
    if getattr(scene, "name", "") == "关卡1":
        apply_settings(scene, (
            ("dimension", "3"),
            ("debug_runtime", True),
            # 上帝摄像机(用户 2026-10-04):这一关开着一台自由飞行相机,
            # 运行时按 Tab 切过去满场飞着看
            ("god_camera", True),
            ("base_class", "CharacterBody3D"),
            ("class_name", "LevelPlayer"),
        ))
    for obj in scene.objects:
        physics = getattr(obj, "gk_physics", None)
        if physics is None:
            continue
        try:
            physics.shape_size = (0.0, 0.0, 0.0)     # 全 0 = 自动贴合
        except Exception:                             # noqa: BLE001
            pass
        if obj is player:
            physics.enabled = True
            physics.body_type = "CharacterBody3D"
            physics.dimension = "3"
            physics.script_target = True
        elif obj.name.startswith("金币"):
            physics.body_type = "Area3D"
        elif obj.name == "电梯":
            # 会动的平台 **必须** AnimatableBody:被时间轴每帧挪位置时,
            # 速度才会传给站在上面的角色(StaticBody 是"瞬移",人会被留在原地)
            physics.body_type = "AnimatableBody3D"
            physics.asset_name = "电梯"
        elif obj.name == "载具":
            # 载具 = CharacterBody(和角色同款,『载具_驾驶』用 velocity 开它)
            physics.body_type = "CharacterBody3D"
            physics.asset_name = "载具"
        else:
            physics.body_type = "StaticBody3D"
        # ── 高台必须显式钉成盒子(用户 2026-10-04)─────────────────────
        # 东/西高台是 4×4×2.6,按"自动"判定时两个较小边 4 / 2.6 ≈ 1.54
        # 会落到**球**(三边接近),角色站上去会顺着球面滑走。这两座高台
        # 是要跳上去站的,所以直接选盒子(尺寸走自动贴合,正好 4×2.6×4)。
        if obj.name in ("东高台", "西高台"):
            try:
                physics.collision_shape = "box"
            except Exception:                         # noqa: BLE001
                pass


def build_second_level():
    """新建**第二个 Blender 场景**『关卡2』(用户 2026-10-04)。

    ── 为什么是"新建场景"而不是"再画一棵节点图"────────────────────
    用户的原话:通关后要"打开另一个关卡"。在 Blender 里,"关卡"就是
    **一个场景**(scene),不是一个节点图 —— 只有新建场景,插件导出时
    才会把它导出成独立的一张 .tscn(`asset_export.export_all_scenes`),
    切关卡才真的有地方可去。每个场景用哪棵逻辑图由场景设置里的
    `logic_tree` 指定(导出器按它选),所以这里建完场景顺手指过去。

    返回 (场景, 挂脚本的物体),交给 main() 依次写导出设置。
    """
    scene = bpy.data.scenes.new("关卡2")
    apply_settings(scene, (
        ("logic_tree", "关卡2逻辑"),
        ("dimension", "3"),
        ("base_class", "CharacterBody3D"),
        # class_name 必须和第一关(LevelPlayer)**不同** —— Godot 一个工程里
        # 同名 class_name 只能有一个,重了会报
        # "Class 'LevelPlayer' hides a global script class"。
        ("class_name", "Level2Player"),
        # 上帝摄像机(用户 2026-10-06 实测:"切到第二关后 Tab 失效")——
        # 当年第二关是"通关页"所以关着,现在它也照样开着。
        ("god_camera", True),
    ))

    # 一块地面 + 两道矮墙(别让角色掉出去;第二关不再放金币 / 高台)
    make_box("第二关地面", (ARENA, ARENA, 0.4), (0.0, 0.0, -0.2),
             (0.24, 0.26, 0.30, 1.0), scene=scene)
    make_box("第二关北墙", (ARENA + 0.4, 0.4, WALL_H),
             (0.0, ARENA * 0.5, WALL_H * 0.5), (0.18, 0.20, 0.24, 1.0),
             scene=scene)
    make_box("第二关南墙", (ARENA + 0.4, 0.4, WALL_H),
             (0.0, -ARENA * 0.5, WALL_H * 0.5), (0.18, 0.20, 0.24, 1.0),
             scene=scene)

    # 相机(要在 objects.new 时就把 Camera 数据给对,见 build_cameras 的说明)
    # 两台:角色预设要一台第三人称侧视 + 一台俯瞰(C / V 切换用)
    cam_data = bpy.data.cameras.new("第二关机位数据")
    cam_data.lens = 50.0
    camera = bpy.data.objects.new("第二关机位", cam_data)
    scene.collection.objects.link(camera)
    camera.location = (0.0, -16.0, 6.0)
    camera.rotation_euler = (math.radians(68.0), 0.0, 0.0)
    scene.camera = camera

    top_data = bpy.data.cameras.new("第二关俯瞰数据")
    top_data.lens = 50.0
    top = bpy.data.objects.new("第二关俯瞰", top_data)
    scene.collection.objects.link(top)
    top.location = (0.0, 0.0, 24.0)
    top.rotation_euler = (0.0, 0.0, 0.0)

    # 阳光
    light_data = bpy.data.lights.new("第二关Sun数据", type="SUN")
    light_data.energy = 4.0
    light = bpy.data.objects.new("第二关Sun", light_data)
    scene.collection.objects.link(light)
    light.location = (6.0, -6.0, 12.0)

    # 第二关的角色(挂脚本;名字必须和第一关的『示例角色』不同,否则
    # bpy.data.objects 会自动改名成『示例角色.001』,脚本挂载目标就对不上了)
    player = make_box("第二关角色", (1.0, 1.0, 1.6), (0.0, 0.0, 0.8),
                      (0.85, 0.35, 0.35, 1.0), scene=scene)
    player.location = (0.0, -6.0, 0.9)

    build_second_logic(scene, player)
    return scene, player


def main():
    from BlenderGameKit import asset_export, project

    os.makedirs(OUT_DIR, exist_ok=True)
    scene = clear_scene()

    # ---- 关卡1:默认上下文场景 ----
    build_arena(scene=scene)
    coins = build_coins(scene=scene)
    goal = build_goal(scene=scene)
    elevator, vehicle = build_ride(scene=scene)
    player = build_player(scene=scene)
    build_cameras(scene=scene)
    build_light(scene=scene)
    build_logic(player, coins, goal, elevator, vehicle)
    write_export_settings(scene, player)

    # ---- 关卡2:另一个 Blender 场景(通关后切过去)----
    level2, level2_player = build_second_level()
    write_export_settings(level2, level2_player)

    bpy.ops.wm.save_as_mainfile(filepath=BLEND_PATH)
    log(f"已保存: {BLEND_PATH}")
    log(f"关卡1: 地面 {ARENA}x{ARENA} · 高台 {len(PLATFORMS)} · "
        f"金币 {len(coins)} · 终点门 1 · 电梯 1 · 载具 1")
    log(f"关卡2: 场景『{level2.name}』· 逻辑图『关卡2逻辑』"
        f"(按 E 回 {FIRST_LEVEL_RES})")

    # ---- 导出成**可玩的** Godot 工程(第三十一轮)────────────────────
    # 以前 `game_godot/` 要靠用户在插件面板里导出一次(README 的流程);
    # 现在这一条命令就把 .blend **和** 能按 F5 的工程一起出好(输入映射、
    # 主场景 = 关卡1、每个 Blender 场景一张 .tscn)。关卡自查脚本也拿它跑
    # 运行时探针(坐电梯 / 开车),不必再手动导一次。
    game_dir = os.path.join(OUT_DIR, "game_godot")
    project.create_project(game_dir, title="关卡示例",
                           blend_file_name=os.path.basename(BLEND_PATH))
    result = asset_export.export_all_scenes(game_dir, dimension="3")
    if result.get("error"):
        log(f"工程导出失败: {result['error']}")
        return 1
    for entry in result.get("scenes", []):
        log(f"场景 {entry['scene']} -> {entry['scene_name']}.tscn"
            f"(脚本 {entry['script_name']})")
    for warning in result.get("warnings", []):
        log(f"告警: {warning}")
    if result.get("deleted"):
        log(f"清理旧文件: {result['deleted']}")
    log(f"可玩工程: {game_dir}(Godot 打开后 F5,主场景 = "
        f"{result.get('main_scene', '')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
