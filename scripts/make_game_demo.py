"""小游戏示例(第二十四~二十七轮):吃金币 + 躲敌人 + 胜负提示 + R 重开。

用户 2026-10-05:"用现有节点拼一个完整的可玩小关(...),专门用来暴露真实
使用中的缺口"。所以这一关的目的不是"炫",而是**把整条链路用一遍**:

| 用到的 | 在游戏里怎么看 |
|---|---|
| 角色预设 | WASD 移动 / 空格跳 / 鼠标转视角 / 滚轮缩放 / C·V 切机位 |
| 触发区拾取 | 走进金币 -> 金币数 +1(『变量_增减』一个节点) |
| **追击敌人**(第二十五轮)| 敌2 是会**追着玩家跑**的红色方块(『移动_朝向目标』)|
| **无敌帧**(第二十五轮)| 碰到敌人扣血后有冷却:同一波接触只掉一次血(『流程_冷却』) |
| **到达 + 加速**(第二十六轮)| 追人的判据直接用『到达』输出(图里少两个节点);起步/刹车平滑(『加速』)|
| **夹范围**(第二十六轮)| 血量夹 0~3、金币夹 0~5 —— 扣血不会再扣成负数(『限制范围』)|
| **受击反馈**(第二十六轮)| 挨打时玩家模型**闪红** + 头顶飘 "-1"(『特效_闪红』/『界面_飘字』)|
| **拾取飘字**(第二十六轮)| 吃金币时金币位置飘 "+1"(一次性 Label3D,不占 HUD)|
| **动画:先放完再重复**(第二十七轮)| 按 **E** 让敌1 抖一下(真对象动画);连着按**不会打断当前这一轮**,放完了才接下一次(『动画_播放』→『重复触发时 = 先放完再说』)|
| 粒子特效 | 吃到金币时在**原地**爆一下火花(空物体发射器 + 『移动_设置位置』) |
| 时间轴(往返) | 敌1 左右来回巡逻(网格动、碰撞代理跟着走) |
| 变量 + HUD | 左上角实时显示"金币 3""血量 2";另有操作提示和说明 |
| **数字转文字**(第二十五轮)| HUD 显示"血量 2"而不是"血量 2.0"(『文本_数字』)|
| 受伤 | 碰到敌人:血量 -1 + 屏幕闪白 + 相机震动(带打印) |
| 胜负 | 收齐 5 枚 -> "全部收齐!";血量归零 -> "被抓到了……" |
| 重开 | 按 **R** 重开(『实例_重载场景』)—— 金币、血量、位置全部复位 |
| 掉出地图 | 掉下去自动送回出生点 |
| 上帝机 / 调试面板 | **Tab** 自由飞;**F1** 弹调试面板(能看到金币数/血量)、**F2** 复制 |

跑法:
    blender -b --python scripts\\make_game_demo.py
之后在 Godot 里打开 `examples/demo_game/小游戏示例_godot`(主场景 World.tscn)。
自动检查(不用 Blender,要 Godot):`python scripts\\check_game_demo.py`
"""

import math
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "examples", "demo_game")
BLEND_PATH = os.path.join(OUT_DIR, "小游戏示例.blend")

PLAYER = "小玩家"
# 出生点:**Blender 坐标**(物体摆位用)
SPAWN = (0.0, -9.0, 0.9)
# ⚠ 同一个位置,但换成 **Godot 坐标** —— 节点图上『移动_设置位置』的
# X/Y/Z 填的是运行时 Godot 的坐标(Blender(x,y,z) -> Godot(x, z, -y))。
# 这里踩过一次:直接填 Blender 那三个数 -> 复位把角色送到地板**下面**
# (y=-9),它每帧往下掉、每帧复位 —— 探针看到的就是"按 W 走不动"。
SPAWN_GODOT = (0.0, 0.9, 9.0)
COIN_SPOTS = [(-6.5, -5.0, 0.6), (6.5, -5.0, 0.6), (0.0, 0.0, 0.6),
              (-6.5, 5.0, 0.6), (6.5, 5.0, 0.6)]
# 敌人在 y=-1 和 y=2.5 两条线上:敌1 **左右来回巡逻**(时间轴轨道1),
# 敌2 **追着玩家跑**(『移动_朝向目标』),碰到都会扣血。
ENEMY_A = ("敌1", (0.0, -1.0, 0.7))
ENEMY_B = ("敌2", (0.0, 2.5, 0.7))
# 敌1 的受击抖动动画(第二十七轮):按 E 播它 —— 连按时"先放完再重复"
ENEMY_ANIM = "敌1抖动"
ENEMY_ANIM_FRAMES = ((1, (1.0, 1.0, 1.0)), (5, (1.35, 0.65, 1.35)),
                     (9, (0.8, 1.25, 0.8)), (13, (1.15, 0.85, 1.15)),
                     (21, (1.0, 1.0, 1.0)))
PATROL_X = 7.0           # 巡逻的左右极限
CHASE_SPEED = 2.2        # 追击速度(3D 单位 = 米/秒)
CHASE_ACCEL = 6.0        # 加减速(『加速』:平滑起步/刹车,不再瞬起瞬停)
# 追到 1.2 米就停下 —— 也是"贴上了吗"的判据(直接用『移动_朝向目标』的
# 『到达』输出,图里少两个节点:『取值_距离』+『逻辑_比较』)
CHASE_STOP = 1.2
HURT_COOLDOWN_AREA = 1.5      # 敌1(触发区)
HURT_COOLDOWN_CHASER = 2.0    # 敌2(追击)
COIN_GOAL = 5
MAX_HP = 3


def log(message):
    print(f"[小游戏] {message}")


def clear_scene():
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for group in list(bpy.data.node_groups):
        if getattr(group, "bl_idname", "").startswith("GK"):
            bpy.data.node_groups.remove(group)
    scene.name = "小游戏"
    return scene


def make_box(name, size, location, color=None, scene=None):
    scene = scene or bpy.context.scene
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.active_object
    obj.name = name
    # ⚠ 基础方块是 1×1×1,scale 直接就是**目标尺寸**(别再乘 0.5 ——
    # 踩过:全场景只有一半大,地面变成 12×12,角色出生点直接在地板外面,
    # 一进去就往下掉,探针看到的是"按 W 走不动")
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
    make_box("地面", (24.0, 24.0, 0.4), (0.0, 0.0, -0.2),
             (0.28, 0.42, 0.30, 1.0))
    # 四面墙:别让角色掉出去(掉出去会被送回出生点,但围起来更像"关卡")
    make_box("北墙", (24.0, 0.6, 3.0), (0.0, 12.0, 1.5),
             (0.5, 0.5, 0.55, 1.0))
    make_box("南墙", (24.0, 0.6, 3.0), (0.0, -12.0, 1.5),
             (0.5, 0.5, 0.55, 1.0))
    make_box("东墙", (0.6, 24.0, 3.0), (12.0, 0.0, 1.5),
             (0.5, 0.5, 0.55, 1.0))
    make_box("西墙", (0.6, 24.0, 3.0), (-12.0, 0.0, 1.5),
             (0.5, 0.5, 0.55, 1.0))

    player = make_box(PLAYER, (1.0, 1.0, 1.8), SPAWN, (0.95, 0.55, 0.25, 1.0))
    coins = [make_box(f"金币{index + 1}", (0.8, 0.8, 0.8), spot,
                      (0.95, 0.82, 0.25, 1.0))
             for index, spot in enumerate(COIN_SPOTS)]
    enemies = [make_box(name, (1.4, 1.4, 1.4), spot, (0.85, 0.25, 0.25, 1.0))
               for name, spot in (ENEMY_A, ENEMY_B)]

    # 金币火花:一个**空物体**当发射器(只标位置和朝向)。吃到金币时把它
    # 挪到那枚金币的位置再『特效_爆发』—— 一个发射器服务全部金币。
    spark = bpy.data.objects.new("金币火花", None)
    scene.collection.objects.link(spark)
    spark.location = (0.0, 0.0, 1.0)

    side = make_camera("侧视机位", (0.0, -14.0, 4.0), (math.radians(72.0), 0.0, 0.0))
    make_camera("俯瞰机位", (0.0, -6.0, 16.0), (math.radians(20.0), 0.0, 0.0))
    scene.camera = side

    light_data = bpy.data.lights.new("太阳光数据", type="SUN")
    light_data.energy = 3.0
    light = bpy.data.objects.new("太阳光", light_data)
    scene.collection.objects.link(light)
    light.rotation_euler = (math.radians(50.0), 0.0, math.radians(30.0))
    return scene, player, coins, enemies


def write_physics(scene, player, coins, enemies):
    """物理类型:玩家 = 角色 + 挂脚本;金币 / 敌1 = 触发区(Area3D);
    敌2 = **角色体**(它自己要 move_and_slide 追人,触发区没有 velocity)。"""
    chaser = enemies[1].name
    trigger_names = {obj.name for obj in coins} | {enemies[0].name}
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
        elif obj.name == chaser:
            physics.body_type = "CharacterBody3D"
            physics.dimension = "3"
        elif obj.name in trigger_names:
            # 触发区:只检测重叠、不阻挡 —— 金币和敌1 都是"碰到就发生什么"
            physics.body_type = "Area3D"
            physics.dimension = "3"
            physics.is_trigger = True
        elif getattr(obj, "type", "") == "MESH":
            physics.body_type = "StaticBody3D"
            physics.dimension = "3"


def write_fx(scene):
    """金币火花:一次性 GPU 粒子(吃到金币时爆发)。"""
    spark = scene.objects.get("金币火花")
    if spark is None:
        return
    fx = getattr(spark, "gk_fx", None)
    if fx is None:
        return
    fx.enabled = True
    fx.auto_play = False          # 靠『特效_爆发』启动
    fx.one_shot = True
    fx.explosiveness = 1.0
    fx.amount = 24
    fx.lifetime = 0.7
    fx.speed = 3.5
    fx.spread = 70.0
    fx.gravity = 9.0              # 火花四散后往下掉
    fx.radius = 0.1
    fx.start_size = 0.12
    fx.end_size = 0.0
    fx.additive = True


def write_animations(scene, enemies):
    """给敌1 挂一条"受击抖动"动画(第二十七轮加的试验场)。

    ── 为什么要有一条真动画 ────────────────────────────────────────
    『动画_播放』的『先放完再说』(重复触发不打断当前这一轮)以前**没有
    任何示例验证过** —— 小游戏里有触发器和按键,但一个动画都没有。
    这里给敌1 做一条 0.83 秒的"被拍扁再弹回来":按 E 播它,
    连按 E 就能看到"先放完再重复"(不会被打回第 0 帧重来)。

    ── 做法 ────────────────────────────────────────────────────────
    在敌1 对象上建一个 Action、给 scale 打 5 个关键帧。导出时
    `export_animations=True` 会把它带进 World.glb,Godot 导入后在
    模型实例内部生成 AnimationPlayer(名字 = Action 名)。
    """
    enemy = enemies[0]
    if enemy.animation_data is None:
        enemy.animation_data_create()
    action = bpy.data.actions.new(ENEMY_ANIM)
    action.use_fake_user = True       # 免得保存 .blend 时被当成废数据清掉
    enemy.animation_data.action = action
    for frame, scale in ENEMY_ANIM_FRAMES:
        enemy.scale = scale
        enemy.keyframe_insert("scale", frame=frame)
    # 摆回原样:动画只在播的时候改它,平时敌1 就是原始大小
    enemy.scale = (1.0, 1.0, 1.0)
    # 帧率:1~21 帧 ÷ 24fps ≈ 0.83 秒(探针的连按窗口比它短,才测得准)
    log(f"敌1 受击动画:『{action.name}』{ENEMY_ANIM_FRAMES[0][0]}~"
        f"{ENEMY_ANIM_FRAMES[-1][0]} 帧 / {len(ENEMY_ANIM_FRAMES)} 个关键帧"
        f"(约 {(ENEMY_ANIM_FRAMES[-1][0] - ENEMY_ANIM_FRAMES[0][0]) / 24.0:.2f} 秒)")


# ==========================================================================
# 节点图
# ==========================================================================
def build_graph(scene, player, coins, enemies):
    from BlenderGameKit import presets

    logic, _anim, notes = presets.create_character_preset(
        scene, character=player,
        logic_name="小游戏逻辑",
        class_name="MiniGamePlayer",
        with_animation=False,
        with_godot_camera=True,          # Tab 上帝机
        camera_side="侧视机位", camera_top="俯瞰机位",
    )
    for note in notes:
        log(f"预设: {note}")
    _declare_vars(logic)
    _extend_graph(logic, coins, enemies)
    return logic


def _declare_vars(tree):
    """声明三个变量(『变量』框里那种,带类型和初值)。

    ⚠ 血量是 **float**:HUD 上要靠『文本_数字』把它显示成"血量 2" ——
    正好是"Godot 的 str(2.0) 是 '2.0'"这个坑的现场。
    """
    for name, kind, initial in (("金币数", "int", "0"),
                                ("血量", "float", str(float(MAX_HP))),
                                ("结束", "bool", "false")):
        var = tree.gk_vars.add()
        var.var_name = name
        var.var_type = kind
        var.default_value = initial
        var.comment = "小游戏状态"
        var.watch = True


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


def _extend_graph(tree, coins, enemies):
    ready_tail = _by_name(tree, "设定初始视野")
    physics = _by_name(tree, "每当_物理帧")

    # ---- ① 启动:操作提示 + 目标 + HUD + 敌人巡逻开始 ----
    hint = _add(tree, "GKNodeUiText", "操作提示", -300.0, 980.0,
                gk_text=("WASD 移动 / 空格 跳 / 鼠标转视角 / C·V 切机位 / "
                         "E 拍敌1 / R 重开 / Tab 上帝机 / F1 调试面板"),
                gk_x=18.0, gk_y=18.0, gk_size=18)
    goal = _add(tree, "GKNodeUiText", "目标提示", -300.0, 800.0,
                gk_text=(f"目标:收齐 {COIN_GOAL} 枚金币;红方块碰到会扣血"
                         f"(共 {MAX_HP} 点,其中一个会追你)"),
                gk_x=18.0, gk_y=46.0, gk_size=18)
    coin_hud = _add(tree, "GKNodeUiText", "金币HUD", -300.0, 620.0,
                    gk_text="金币 0", gk_x=18.0, gk_y=84.0, gk_size=24)
    hp_hud = _add(tree, "GKNodeUiText", "血量HUD", -300.0, 460.0,
                  gk_text=f"血量 {MAX_HP}", gk_x=18.0, gk_y=120.0, gk_size=24)
    patrol = _patrol_timeline(tree, -300.0, 260.0, enemies[:1])
    _link(tree, ready_tail, "完成", hint, "执行")
    _link(tree, hint, "完成", goal, "执行")
    # ⚠ 两个 HUD **不能**既挂在开场链上、又挂在物理帧链上 ——
    # 执行流对"同一个节点"只会认**一条入口**(`_claimed` 按节点+入口记账),
    # 另一条会被当成"抢"直接跳过。结果就是:开场链走到一半断了,
    # HUD 和后面的巡逻时间轴**都不生成**(小游戏探针抓到的)。
    # HUD 本来就该每物理帧刷一次,所以只留在物理帧链上(第一帧就会建出来)。
    _link(tree, goal, "完成", patrol, "执行")

    # ---- ② 每物理帧:HUD 刷新 + 掉出地图复位 + 胜负 + R 重开 + Esc 退出 ----
    _refresh_hud(tree, physics, coin_hud, hp_hud, -900.0, -140.0)
    _fall_guard(tree, physics, -900.0, -380.0)
    _win_lose(tree, physics, -900.0, -620.0)
    _restart_and_quit(tree, physics, -900.0, -1120.0)

    # ---- ③ 追击:敌2 追着玩家跑 + 贴近了扣血(带无敌帧)----
    _chase_chain(tree, physics, enemies[1].name, 340.0, -620.0)

    # ---- ④ 吃金币:每枚一条链(触发区 -> 计数 -> 火花 -> 销毁)----
    for index, coin in enumerate(coins):
        _coin_chain(tree, coin.name, index, -260.0, -60.0 - index * 300.0)

    # ---- ⑤ 敌1 巡逻 + 受伤:碰到就扣血(带无敌帧:同一波接触只掉一次)----
    for index, enemy in enumerate(enemies[:1]):
        _hurt_by_touch(tree, enemy.name, index, 340.0, -60.0 - index * 240.0)

    # ---- ⑥ 按 E:拍一下敌1(试『动画_播放』的"先放完再重复")----
    _pose_chain(tree, physics, 340.0, -1500.0)

    log(f"小游戏逻辑:共 {len(tree.nodes)} 个节点 / {len(tree.links)} 条连线")


def _patrol_timeline(tree, x, y, enemies):
    """敌1 的左右巡逻:一条**往返**时间轴(敌2 交给『移动_朝向目标』追人)。"""
    timeline = _add(tree, "GKNodeTimeline", "敌人巡逻", x, y,
                    gk_time=6.0, gk_mode="PINGPONG",
                    gk_track_count=1,
                    gk_track1_from=-PATROL_X, gk_track1_to=PATROL_X)
    previous = timeline
    for index, enemy in enumerate(enemies):
        # 只让 X 走轨道值,Y / Z 保持**敌人当前**的(所以它只左右动)
        where = _add(tree, "GKNodeGetPosition", f"取{enemy.name}位置",
                     x + 320.0, y - 120.0 - index * 200.0,
                     gk_target=enemy.name, gk_global=True)
        move = _add(tree, "GKNodeSetPosition", f"{enemy.name}巡逻",
                    x + 560.0, y - 120.0 - index * 200.0, gk_target=enemy.name)
        _link(tree, timeline, f"轨道{index + 1}值", move, "X")
        _link(tree, where, "Y", move, "Y")
        _link(tree, where, "Z", move, "Z")
        _link(tree, previous, "更新" if index == 0 else "完成",
              move, "执行")
        previous = move
    return timeline


def _refresh_hud(tree, physics, coin_hud, hp_hud, x, y):
    """每物理帧把两个 HUD 的文本刷成变量当前值。

    ⚠ 『文本_数字』不能省:血量是 float,直接拼会显示成"血量 2.0"
    (Godot 的 str(2.0) 就是 "2.0")。
    """
    for index, (hud, prefix, var) in enumerate(
            ((coin_hud, "金币 ", "金币数"), (hp_hud, "血量 ", "血量"))):
        read = _add(tree, "GKNodeGetVar", f"读{var}HUD", x + 40.0,
                    y - index * 160.0, gk_var_name=var)
        number = _add(tree, "GKNodeNumberText", f"{var}转文字", x + 240.0,
                      y - index * 160.0)
        join = _add(tree, "GKNodeTextJoin", f"拼{var}", x + 440.0,
                    y - index * 160.0, gk_a=prefix, gk_b="")
        _link(tree, read, "值", number, "数值")
        _link(tree, number, "文字", join, "乙")
        _link(tree, physics, "执行", hud, "执行")
        _link(tree, join, "结果", hud, "文字")


def _fall_guard(tree, physics, x, y):
    """掉出地图 -> 送回出生点。"""
    where = _add(tree, "GKNodeGetPosition", "取自己位置", x + 40.0, y,
                 gk_global=True)
    compare = _add(tree, "GKNodeCompare", "掉下去了?", x + 260.0, y,
                   gk_op="LT", gk_b="-6.0")
    branch = _add(tree, "GKNodeBranch", "掉出地图?", x + 480.0, y)
    back = _add(tree, "GKNodeSetPosition", "送回出生点", x + 700.0, y,
                gk_x=SPAWN_GODOT[0], gk_y=SPAWN_GODOT[1], gk_z=SPAWN_GODOT[2])
    warn = _add(tree, "GKNodePrint", "复位提示", x + 920.0, y,
                gk_text="掉出地图:已送回出生点")
    _link(tree, where, "Y", compare, "A")
    _link(tree, compare, "结果", branch, "条件")
    _link(tree, physics, "执行", branch, "执行")
    _link(tree, branch, "是", back, "执行")
    _link(tree, back, "完成", warn, "执行")


def _win_lose(tree, physics, x, y):
    """收齐金币 = 胜;血量没了 = 败。都只做一次(靠『结束』变量挡住)。"""
    over = _add(tree, "GKNodeBranch", "已经结束了?", x + 40.0, y)
    coin_ok = _add(tree, "GKNodeCompare", "金币够了吗?", x + 260.0, y,
                   gk_op="GE", gk_b=str(COIN_GOAL))
    branch_win = _add(tree, "GKNodeBranch", "赢了吗?", x + 480.0, y)
    set_win = _add(tree, "GKNodeSetVar", "标记结束(胜)", x + 700.0, y,
                   gk_var_name="结束", gk_value=True)
    win_text = _add(tree, "GKNodeUiText", "胜利提示", x + 920.0, y,
                    gk_text="全部收齐!按 R 再来一次",
                    gk_x=430.0, gk_y=300.0, gk_size=36,
                    gk_color=(1.0, 0.9, 0.35, 1.0))
    hp_low = _add(tree, "GKNodeCompare", "血没了吗?", x + 260.0, y - 300.0,
                  gk_op="LE", gk_b="0")
    branch_lose = _add(tree, "GKNodeBranch", "输了吗?", x + 480.0, y - 300.0)
    set_lose = _add(tree, "GKNodeSetVar", "标记结束(败)", x + 700.0, y - 300.0,
                    gk_var_name="结束", gk_value=True)
    lose_text = _add(tree, "GKNodeUiText", "失败提示", x + 920.0, y - 300.0,
                     gk_text="被抓到了……按 R 重来",
                     gk_x=430.0, gk_y=300.0, gk_size=36,
                     gk_color=(1.0, 0.4, 0.4, 1.0))

    read_over = _add(tree, "GKNodeGetVar", "读结束", x + 40.0, y - 120.0,
                     gk_var_name="结束")
    read_coins = _add(tree, "GKNodeGetVar", "读金币数", x + 40.0, y - 220.0,
                      gk_var_name="金币数")
    read_hp = _add(tree, "GKNodeGetVar", "读血量", x + 40.0, y - 380.0,
                   gk_var_name="血量")
    _link(tree, read_over, "值", over, "条件")
    _link(tree, read_coins, "值", coin_ok, "A")
    _link(tree, coin_ok, "结果", branch_win, "条件")
    _link(tree, read_hp, "值", hp_low, "A")
    _link(tree, hp_low, "结果", branch_lose, "条件")
    _link(tree, physics, "执行", over, "执行")
    _link(tree, over, "否", branch_win, "执行")       # 结束之前才判胜负
    _link(tree, branch_win, "是", set_win, "执行")
    _link(tree, set_win, "完成", win_text, "执行")
    _link(tree, branch_win, "否", branch_lose, "执行")
    _link(tree, branch_lose, "是", set_lose, "执行")
    _link(tree, set_lose, "完成", lose_text, "执行")


def _restart_and_quit(tree, physics, x, y):
    """R = 重开(重载场景);Esc = 退出。"""
    key_r = _add(tree, "GKNodeKey", "R 键", x, y, gk_key="R",
                 gk_action="key_r", gk_trigger="JUST")
    branch_r = _add(tree, "GKNodeBranch", "按了 R?", x + 220.0, y)
    # ⚠ 存档槽留空 = 重载**当前场景**(填了会变成"读存档"—— 那不是重开)
    reload_scene = _add(tree, "GKNodeReloadScene", "重开这一关", x + 440.0, y,
                        gk_slot="")
    _link(tree, key_r, "结果", branch_r, "条件")
    _link(tree, physics, "执行", branch_r, "执行")
    _link(tree, branch_r, "是", reload_scene, "执行")

    key_esc = _add(tree, "GKNodeKey", "Esc 键", x, y + 220.0, gk_key="ESC",
                   gk_action="key_esc", gk_trigger="JUST")
    branch_esc = _add(tree, "GKNodeBranch", "按了 Esc?", x + 220.0, y + 220.0)
    quit_game = _add(tree, "GKNodeQuitGame", "退出游戏", x + 440.0, y + 220.0)
    _link(tree, key_esc, "结果", branch_esc, "条件")
    _link(tree, physics, "执行", branch_esc, "执行")
    _link(tree, branch_esc, "是", quit_game, "执行")


def _coin_chain(tree, coin_name, index, x, y):
    """一枚金币:走进触发区 -> 金币数 +1 -> 飘个 "+1" -> 火花爆一下 -> 金币消失。

    ⚠ 『变量_增减』一个节点就顶掉了原来的
    『变量_读取』->『数学_运算』->『变量_设置』三件套;
    『限制范围』勾上 0~5,金币数不可能被加冒;
    飘字是**一次性的 Label3D**(第二十六轮):贴着金币冒 "+1",飘完自删,
    不占屏幕角落的 HUD。
    """
    touched = _add(tree, "GKNodeOnAreaEntered", f"碰到{coin_name}", x, y,
                   gk_area=coin_name, gk_filter=PLAYER)
    add_one = _add(tree, "GKNodeAddVar", f"金币+1_{index + 1}", x + 240.0, y,
                   gk_var_name="金币数", gk_amount=1.0,
                   gk_clamp=True, gk_min=0.0, gk_max=float(COIN_GOAL))
    floating = _add(tree, "GKNodeFloatText", f"金币飘字{index + 1}",
                    x + 480.0, y + 180.0, gk_target=coin_name,
                    gk_prefix="+", gk_value=1.0)
    where = _add(tree, "GKNodeGetPosition", f"取{coin_name}位置",
                 x + 480.0, y - 220.0, gk_target=coin_name, gk_global=True)
    move_spark = _add(tree, "GKNodeSetPosition", f"火花就位{index + 1}",
                      x + 700.0, y, gk_target="金币火花")
    burst = _add(tree, "GKNodeFxBurst", f"火花爆发{index + 1}",
                 x + 920.0, y, gk_target="金币火花")
    kill = _add(tree, "GKNodeQueueFree", f"金币消失{index + 1}",
                x + 1140.0, y, gk_target=coin_name)
    _link(tree, where, "X", move_spark, "X")
    _link(tree, where, "Y", move_spark, "Y")
    _link(tree, where, "Z", move_spark, "Z")
    _link(tree, touched, "执行", add_one, "执行")
    _link(tree, add_one, "完成", floating, "执行")
    _link(tree, floating, "完成", move_spark, "执行")
    _link(tree, move_spark, "完成", burst, "执行")
    _link(tree, burst, "完成", kill, "执行")


def _hurt_feedback(tree, index, x, y, tag):
    """受伤反馈(两条受伤链共用):飘 "-1" + 角色闪红。

    ── 为什么要抽出来(用户 2026-10-05 第二十六轮)────────────────
    敌1(触发区)和敌2(追击)两条链都要这一套。闪红闪的是**玩家自己**
    (『目标』留空 = 脚本所在节点),飘字也是从玩家头顶冒 —— 谁挨打
    就闪谁、就在谁头上飘,混战里分得清。
    """
    floating = _add(tree, "GKNodeFloatText", f"{tag}飘字{index + 1}",
                    x, y, gk_prefix="-", gk_value=1.0,
                    gk_color=(1.0, 0.35, 0.35, 1.0))
    flash = _add(tree, "GKNodeFlashRed", f"{tag}闪红{index + 1}",
                 x + 220.0, y, gk_time=0.25)
    return floating, flash


def _pose_chain(tree, physics, x, y):
    """按 E:拍一下敌1 —— 试『动画_播放』的『先放完再说』(第二十七轮)。

    ── 这条链为什么存在 ──────────────────────────────────────────
    用户原话:"重复触发时它会重复开始动画,如果我想让它先放完再重复"。
    按 E 就是这个"重复触发"的现场:连着按(探针一秒能按几十次),
    敌1 的抖动动画**只播一轮**,不会被打回第 0 帧重来;
    这一轮放完之后再按,才会接着播下一轮 —— 这就是"先放完再重复"。

    ── 为什么『动画播放器』填的是"敌1"而不是留空 ──────────────────
    敌1 的网格在世界 glb 里、动画播放器藏在那个网格内部,所以必须
    走"从敌1 开始找播放器"那条路(填物体名 = 导出后自动找到它的
    AnimationPlayer)。留空也行,但会退化成"全场景找第一个播放器"。
    """
    key = _add(tree, "GKNodeKey", "E 键", x, y, gk_key="E",
               gk_action="key_e", gk_trigger="JUST")
    branch = _add(tree, "GKNodeBranch", "按了 E?", x + 220.0, y)
    play = _add(tree, "GKNodePlayAnim", "敌1受击抖动", x + 440.0, y,
                gk_player=ENEMY_A[0], gk_anim_name=ENEMY_ANIM,
                gk_blend_mode="CONTINUE")
    _link(tree, key, "结果", branch, "条件")
    _link(tree, physics, "执行", branch, "执行")
    _link(tree, branch, "是", play, "执行")


def _hurt_by_touch(tree, enemy_name, index, x, y):
    """碰到敌人:先过**无敌帧**,再血量 -1 + 飘字 + 闪红 + 闪白 + 相机震动。

    ── 为什么受伤前面必须挂一个『流程_冷却』(用户实测)──────────────
    触发区是"进区域那一瞬间"报的事,而敌人/子弹会在窗口里连着触发,
    于是扣血、闪白、音效全都重来一遍。挂上冷却之后:**一波接触只掉
    一次血**,想改成"两秒无敌"就改那个『秒数』。

    ── 第二十六轮加的两样(受击反馈)────────────────────────────
    * 『界面_飘字』:玩家头顶冒 "-1"(一次性,飘完自删);
    * 『特效_闪红』:玩家整个模型红 0.25 秒 —— 比全屏闪白更"指得准",
      混战里知道是谁挨打了。
    """
    touched = _add(tree, "GKNodeOnAreaEntered", f"撞到{enemy_name}", x, y,
                   gk_area=enemy_name, gk_filter=PLAYER)
    cooldown = _add(tree, "GKNodeCooldown", f"受伤冷却{index + 1}",
                    x + 240.0, y, gk_seconds=HURT_COOLDOWN_AREA)
    add_hp = _add(tree, "GKNodeAddVar", f"血量-1_{index + 1}",
                  x + 480.0, y, gk_var_name="血量", gk_amount=-1.0,
                  gk_clamp=True, gk_min=0.0, gk_max=float(MAX_HP))
    floating, hurt_flash = _hurt_feedback(tree, index, x + 720.0, y, "受伤")
    flash = _add(tree, "GKNodeScreenFlash", f"受伤闪白{index + 1}",
                 x + 1180.0, y, gk_color=(1.0, 0.25, 0.25, 1.0),
                 gk_time=0.3, gk_alpha=0.45)
    shake = _add(tree, "GKNodeCameraShake", f"受伤震动{index + 1}",
                 x + 1420.0, y, gk_target="侧视机位",
                 gk_strength=14.0, gk_duration=0.25)
    say = _add(tree, "GKNodePrint", f"受伤提示{index + 1}",
               x + 1660.0, y,
               gk_text=f"被 {enemy_name} 碰到:血量 -1(冷却 {HURT_COOLDOWN_AREA:g} 秒)")
    _link(tree, touched, "执行", cooldown, "执行")
    _link(tree, cooldown, "可以", add_hp, "执行")
    # 『还在冷却』什么都不接 = 这段时间里再来触发就什么都不做
    _link(tree, add_hp, "完成", floating, "执行")
    _link(tree, floating, "完成", hurt_flash, "执行")
    _link(tree, hurt_flash, "完成", flash, "执行")
    _link(tree, flash, "完成", shake, "执行")
    _link(tree, shake, "完成", say, "执行")


def _chase_chain(tree, physics, chaser_name, x, y):
    """敌2 追着玩家跑;贴近了扣血(和触发区那条一样带无敌帧)。

    ── 两条链的区别 ──────────────────────────────────────────────
    巡逻那条是『移动_设置位置』(按名字摆网格);这条是
    『移动_朝向目标』(velocity + move_and_slide 的角色体)——
    追人必须用后者:位置每帧硬摆会**穿过**墙和别的敌人。

    ── 第二十六轮(用户:"节点图看着有点复杂")──────────────────
    判"贴上了吗"不再挂『取值_距离』+『逻辑_比较』两个节点,直接用
    『移动_朝向目标』的『到达』输出(= 距离进了『停在多远』);
    『加速』让它平滑起步/刹车,不再瞬起瞬停。
    """
    chase = _add(tree, "GKNodeMoveToward", "追击玩家", x, y,
                 gk_target=chaser_name, gk_goal=PLAYER,
                 gk_speed=CHASE_SPEED, gk_stop_distance=CHASE_STOP,
                 gk_turn=True, gk_accel=CHASE_ACCEL)
    branch = _add(tree, "GKNodeBranch", "贴上了?", x + 300.0, y)
    cooldown = _add(tree, "GKNodeCooldown", "追击伤害冷却", x + 540.0, y,
                    gk_seconds=HURT_COOLDOWN_CHASER)
    add_hp = _add(tree, "GKNodeAddVar", "追击扣血", x + 780.0, y,
                  gk_var_name="血量", gk_amount=-1.0,
                  gk_clamp=True, gk_min=0.0, gk_max=float(MAX_HP))
    floating, hurt_flash = _hurt_feedback(tree, 9, x + 1020.0, y, "追击")
    flash = _add(tree, "GKNodeScreenFlash", "追击闪白", x + 1480.0, y,
                 gk_color=(1.0, 0.2, 0.6, 1.0), gk_time=0.3, gk_alpha=0.4)
    shake = _add(tree, "GKNodeCameraShake", "追击震动", x + 1720.0, y,
                 gk_target="侧视机位", gk_strength=10.0, gk_duration=0.2)
    say = _add(tree, "GKNodePrint", "追击提示", x + 1960.0, y,
               gk_text=f"被 {chaser_name} 追上:血量 -1"
                       f"(冷却 {HURT_COOLDOWN_CHASER:g} 秒)")
    _link(tree, physics, "执行", chase, "执行")
    _link(tree, chase, "完成", branch, "执行")
    _link(tree, chase, "到达", branch, "条件")
    _link(tree, branch, "是", cooldown, "执行")
    _link(tree, cooldown, "可以", add_hp, "执行")
    _link(tree, add_hp, "完成", floating, "执行")
    _link(tree, floating, "完成", hurt_flash, "执行")
    _link(tree, hurt_flash, "完成", flash, "执行")
    _link(tree, flash, "完成", shake, "执行")
    _link(tree, shake, "完成", say, "执行")


def save_blend(path):
    """保存 .blend(无头模式下用 libraries.write,见 make_curve_demo 的说明)。"""
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
    scene, player, coins, enemies = build_scene()
    write_physics(scene, player, coins, enemies)
    write_fx(scene)
    write_animations(scene, enemies)
    build_graph(scene, player, coins, enemies)
    # 上帝机(Tab)+ 调试面板(F1 / F2)—— 这两样也是"链路"的一部分
    settings = scene.gk_settings
    settings.god_camera = True
    settings.debug_panel = True

    directory = os.path.join(OUT_DIR, "小游戏示例_godot")
    project.create_project(directory, title="小游戏示例",
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