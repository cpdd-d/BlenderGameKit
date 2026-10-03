"""做一个**能玩的关卡**并导出成 Godot 工程(第 22 轮)。

用户原话:"先做个能玩的关卡吧"。

关卡设计(小但完整,该有的机制都有):
    * 30x30 的地面 + 四面墙(不让人掉出去) + 3 个高台
    * 5 枚金币:走近就吃掉,**分数 +1**,金币消失
    * HUD:左上角显示"金币 0/5"和一句操作提示
    * 集满 5 枚 -> HUD 变成"通关!",并且允许按 Esc 退出
    * 掉出地面(走出边界外) -> **回到出生点**(不惩罚,只是别卡住)

节点图分两棵:『玩家逻辑』(玩法) —— 这是"能玩的关卡"的最小完备集合。
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


def clear_scene():
    """清空场景 —— **不能用 `read_factory_settings`**(踩过的坑)。

    那个操作会把插件一起卸载,之后 `node_groups.new(..., "GKGameLogicTree")`
    就报 `enum "GKGameLogicTree" not found`(因为类型已经注销了)。
    所以老老实实自己删。
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
    scene.unit_settings.system = "METRIC"
    scene.frame_start = 1
    scene.frame_end = 40
    return scene


def make_box(name, size, location, material_color=None):
    """一个长方体(地面 / 墙 / 高台 / 金币都用它)。"""
    sx, sy, sz = [v * 0.5 for v in size]
    verts = [(-sx, -sy, -sz), (sx, -sy, -sz), (sx, sy, -sz), (-sx, sy, -sz),
             (-sx, -sy, sz), (sx, -sy, sz), (sx, sy, sz), (-sx, sy, sz)]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5),
             (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh = bpy.data.meshes.new(f"{name}网格")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
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


def build_arena():
    make_box("地面", (ARENA, ARENA, 0.4), (0.0, 0.0, -0.2),
             (0.28, 0.30, 0.34, 1.0))
    half = ARENA * 0.5
    wall_size = (ARENA + 0.4, 0.4, WALL_H)
    make_box("北墙", wall_size, (0.0, half, WALL_H * 0.5),
             (0.20, 0.22, 0.26, 1.0))
    make_box("南墙", wall_size, (0.0, -half, WALL_H * 0.5),
             (0.20, 0.22, 0.26, 1.0))
    side = (0.4, ARENA + 0.4, WALL_H)
    make_box("东墙", side, (half, 0.0, WALL_H * 0.5), (0.20, 0.22, 0.26, 1.0))
    make_box("西墙", side, (-half, 0.0, WALL_H * 0.5), (0.20, 0.22, 0.26, 1.0))
    for name, size, location in PLATFORMS:
        make_box(name, size, location, (0.36, 0.34, 0.30, 1.0))


def build_coins():
    coins = []
    for index, (x, y, z) in enumerate(COIN_POSITIONS, start=1):
        coin = make_box(f"金币{index}", (0.7, 0.7, 0.7), (x, y, z),
                        (1.0, 0.82, 0.20, 1.0))
        coin.rotation_euler = (0.0, 0.0, math.radians(45.0 * index))
        coins.append(coin)
    return coins


def build_goal():
    """终点门:集满金币之后走过去……这里只做一个视觉标记 + 判定。"""
    return make_box("终点门", (2.0, 0.4, 2.4), (0.0, ARENA * 0.5 - 0.6, 1.2),
                    (0.20, 0.85, 0.45, 1.0))


def build_player():
    """方块角色(z=0 是脚底 —— 碰撞体会自动对齐,见导出器)。"""
    player = make_box("示例角色", (1.0, 1.0, 1.6), (0.0, 0.0, 0.8),
                      (0.30, 0.55, 0.95, 1.0))
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


def build_cameras():
    # 相机要在 `objects.new` 时就把 data 给对 —— 先建空物体再塞
    # `Camera` 数据会报 `Object.data expected a Image type, not Camera`(踩过)
    side_data = bpy.data.cameras.new("侧视机位数据")
    side_data.lens = 50.0
    side = bpy.data.objects.new("侧视机位", side_data)
    bpy.context.scene.collection.objects.link(side)
    side.location = (0.0, -16.0, 6.0)
    side.rotation_euler = (math.radians(68.0), 0.0, 0.0)

    top_data = bpy.data.cameras.new("俯瞰机位数据")
    top_data.lens = 50.0
    top = bpy.data.objects.new("俯瞰机位", top_data)
    bpy.context.scene.collection.objects.link(top)
    top.location = (0.0, 0.0, 24.0)
    top.rotation_euler = (0.0, 0.0, 0.0)
    return side, top


def build_light():
    light_data = bpy.data.lights.new("Sun数据", type="SUN")
    light_data.energy = 4.0
    light = bpy.data.objects.new("Sun", light_data)
    bpy.context.scene.collection.objects.link(light)
    light.location = (6.0, -6.0, 12.0)
    return light


# ==========================================================================
# 节点图
# ==========================================================================
def mark_start(node):
    """给节点标上"玩家逻辑"树需要的位置(纯粹为了看图时好读)。"""
    return node


def build_logic(player, coins, goal):
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
    hud_title = add("GKNodeUiText", "HUD标题", -500.0, 500.0,
                    gk_control="", gk_text="金币 0/5",
                    gk_x=20.0, gk_y=18.0, gk_size=22)
    # 两行 HUD 必须**错开位置**:同一个坐标会叠在一起(截图里就是
    # "金币 0/5"和提示糊成一团)。UI 文本用的是 gk_x / gk_y
    # (**没有** gk_anchor 那个属性 —— 我一开始猜错了名字)。
    hud_hint = add("GKNodeUiText", "HUD提示", -500.0, 300.0,
                   gk_control="", gk_text="WASD 移动 · 空格 跳 · 鼠标转视角 · C/V 切视角",
                   gk_x=20.0, gk_y=56.0, gk_size=15)
    tree.links.new(ready.outputs["执行"], lock.inputs["执行"])
    tree.links.new(lock.outputs["完成"], setup.inputs["执行"])
    tree.links.new(setup.outputs["完成"], hud_title.inputs["执行"])
    tree.links.new(hud_title.outputs["完成"], hud_hint.inputs["执行"])

    # ---- 每当_物理帧:移动 + 跳跃 + 相机跟随 + 转视角 ----
    physics = add("GKNodeOnPhysicsProcess", "每当_物理帧", -1400.0, 0.0)

    direction = add("GKNodeInputDirection", "输入_方向", -1400.0, -260.0,
                    gk_up="W", gk_down="S", gk_left="A", gk_right="D",
                    gk_left_action="key_a", gk_right_action="key_d",
                    gk_up_action="key_w", gk_down_action="key_s")
    move = add("GKNodeMoveDirection", "移动_按方向", -1000.0, 0.0,
               gk_speed=6.0, gk_keep_y=True)
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
    arm = add("GKNodeCameraSpringArm", "弹簧臂(第三人称)", 200.0, -1900.0,
              gk_target="",              # 跟着脚本自己(角色)
              gk_camera="",              # 相机交给它自己造/接管
              gk_arm=5.0, gk_height=1.4, gk_pitch=12.0, gk_side=0.0,
              gk_collision=True, gk_radius=0.35, gk_lag=0.12)
    tree.links.new(physics.outputs["执行"], arm.inputs["执行"])

    # 鼠标转视角:像素位移 x 每像素多少度(和 2.5D 示例同一套)
    look = add("GKNodeMouseMotion", "鼠标移动量", -1400.0, -800.0)
    split = add("GKNodeVectorSplit", "拆成 XY", -1150.0, -800.0)
    # 灵敏度 = 每像素多少度(不要乘帧间隔,否则慢 60 倍)
    delta = add("GKNodeMath", "灵敏度", -1000.0, -700.0, gk_op="MUL",
                gk_b=0.25)
    yaw = add("GKNodeMath", "左右灵敏度", -780.0, -760.0, gk_op="MUL",
              gk_b=-1.0)
    pitch = add("GKNodeMath", "上下灵敏度", -780.0, -900.0, gk_op="MUL",
                gk_b=-1.0)
    turn = add("GKNodeCameraRotation", "转动视角", -420.0, -800.0,
               gk_mode="ADD", gk_clamp_pitch=True)
    tree.links.new(look.outputs["移动量"], split.inputs["向量"])
    tree.links.new(delta.outputs["结果"], yaw.inputs["A"])
    tree.links.new(split.outputs["X"], yaw.inputs["B"])
    tree.links.new(delta.outputs["结果"], pitch.inputs["A"])
    tree.links.new(split.outputs["Y"], pitch.inputs["B"])
    tree.links.new(physics.outputs["执行"], turn.inputs["执行"])
    tree.links.new(yaw.outputs["结果"], turn.inputs["偏航"])
    tree.links.new(pitch.outputs["结果"], turn.inputs["俯仰"])

    # 切视角:C 俯瞰 / V 侧视
    # ── C / V:改**弹簧臂的杆长**(不是"切相机")──────────────────────
    # 踩过的坑:原来用『相机_切换』,而弹簧臂每帧都在写同一台相机的位置
    # —— 切相机的过渡**永远被弹簧臂覆盖**,表现就是"切了没反应/闪一下"。
    # 弹簧臂才是这台相机的主人,所以 C/V 改成改它的参数:
    #   C = 拉远到 14 米、俯仰 55°(看清全局)
    #   V = 收回 5 米、俯仰 12°(第三人称)
    view_key = add("GKNodeKey", "按 C", -1400.0, -1100.0,
                   gk_key="C", gk_action="view_top", gk_trigger="JUST")
    view_branch = add("GKNodeBranch", "如果_否则", -1000.0, -1100.0)
    to_top = add("GKNodeCameraSpringArm", "拉远(全局视角)", -700.0, -1100.0,
                 gk_target="", gk_camera="", gk_arm=14.0, gk_height=2.0,
                 gk_pitch=55.0, gk_side=0.0, gk_collision=True,
                 gk_radius=0.35, gk_lag=0.15)
    tree.links.new(physics.outputs["执行"], view_branch.inputs["执行"])
    tree.links.new(view_key.outputs["结果"], view_branch.inputs["条件"])
    tree.links.new(view_branch.outputs["是"], to_top.inputs["执行"])

    back_key = add("GKNodeKey", "按 V", -1400.0, -1320.0,
                   gk_key="V", gk_action="view_side", gk_trigger="JUST")
    back_branch = add("GKNodeBranch", "如果_否则", -1000.0, -1320.0)
    to_side = add("GKNodeCameraSpringArm", "收回(第三人称)", -700.0, -1320.0,
                  gk_target="", gk_camera="", gk_arm=5.0, gk_height=1.4,
                  gk_pitch=12.0, gk_side=0.0, gk_collision=True,
                  gk_radius=0.35, gk_lag=0.12)
    tree.links.new(physics.outputs["执行"], back_branch.inputs["执行"])
    tree.links.new(back_key.outputs["结果"], back_branch.inputs["条件"])
    tree.links.new(back_branch.outputs["是"], to_side.inputs["执行"])

    # ---- 金币:走近就吃掉 ----
    # 分数用一个脚本变量记着(『变量_设置』),顺手发给对外端口
    # 加分 = 读出来 + 1 再写回去(『变量_设置』本身没有"自增"选项:
    # 想省节点就得再加一个『变量_自增』节点,这轮先用手上的零件拼)
    read_before = add("GKNodeGetVar", "读当前分数", -1120.0, -1620.0,
                      gk_var_name="金币数")
    plus_one = add("GKNodeMath", "加一", -1000.0, -1700.0, gk_op="ADD",
                   gk_b=1.0)
    score_var = add("GKNodeSetVar", "写回分数", -820.0, -1700.0,
                    gk_var_name="金币数")
    tree.links.new(read_before.outputs["值"], plus_one.inputs["A"])
    tree.links.new(plus_one.outputs["结果"], score_var.inputs["值"])
    coin_root = -1400.0
    for index, coin in enumerate(coins, start=1):
        distance = add("GKNodeDistance", f"到金币{index}的距离",
                       coin_root, -1700.0 - index * 260.0,
                       gk_a="", gk_b=coin.name)
        compare = add("GKNodeCompare", f"够近吗{index}",
                      coin_root + 320.0, -1700.0 - index * 260.0,
                      gk_op="LT", gk_b="1.3")
        branch = add("GKNodeBranch", f"如果_否则{index}",
                     coin_root + 620.0, -1700.0 - index * 260.0)
        kill = add("GKNodeQueueFree", f"吃掉金币{index}",
                   coin_root + 920.0, -1700.0 - index * 260.0,
                   gk_target=coin.name)
        tree.links.new(distance.outputs["距离"], compare.inputs["A"])
        tree.links.new(compare.outputs["结果"], branch.inputs["条件"])
        tree.links.new(physics.outputs["执行"], branch.inputs["执行"])
        tree.links.new(branch.outputs["是"], kill.inputs["执行"])
        tree.links.new(kill.outputs["完成"], score_var.inputs["执行"])

    # ---- HUD 刷新:读变量 -> 拼文本 -> 写回界面 ----
    # "金币 3/5" 是两段拼接:『文本_拼接』(变量) + 『文本_拼接』("/5")
    read_score = add("GKNodeGetVar", "读金币数", -700.0, -2400.0,
                     gk_var_name="金币数")
    join_head = add("GKNodeTextJoin", "拼前半", -420.0, -2400.0,
                    gk_a="金币 ")
    join_tail = add("GKNodeTextJoin", "拼后半", -160.0, -2400.0,
                    gk_b="/5")
    hud_update = add("GKNodeUiText", "刷新HUD", 120.0, -2400.0,
                     gk_control="", gk_text="金币 0/5",
                     gk_x=20.0, gk_y=18.0, gk_size=22)
    tree.links.new(read_score.outputs["值"], join_head.inputs["乙"])
    tree.links.new(join_head.outputs["结果"], join_tail.inputs["甲"])
    tree.links.new(join_tail.outputs["结果"], hud_update.inputs["文字"])
    # 每次吃到金币就刷一次 HUD(接在加分后面)
    tree.links.new(score_var.outputs["完成"], hud_update.inputs["执行"])

    # ---- 掉出去 -> 回出生点 ----
    # 判定用"自己的 Y 小于 -6 吗"(比射线稳,也不依赖地图边界):
    #   『取值_位置』-> 『逻辑_比较』(< -6)-> 『如果_否则』-> 『移动_设置位置』
    my_pos = add("GKNodeGetPosition", "读自己的位置", -1400.0, -2800.0,
                 gk_target="", gk_global=True)
    below = add("GKNodeCompare", "掉下去了吗", -1100.0, -2800.0,
                gk_op="LT", gk_b=str(FALL_LIMIT))
    fell_branch = add("GKNodeBranch", "如果_否则", -820.0, -2800.0)
    reset = add("GKNodeSetPosition", "送回出生点", -520.0, -2800.0,
                gk_target="", gk_x=SPAWN[0], gk_y=SPAWN[1], gk_z=SPAWN[2])
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
    tree.links.new(read_for_win.outputs["值"], win_check.inputs["A"])
    tree.links.new(score_var.outputs["完成"], win_branch.inputs["执行"])
    tree.links.new(win_check.outputs["结果"], win_branch.inputs["条件"])
    tree.links.new(win_branch.outputs["是"], win_set.inputs["执行"])
    tree.links.new(win_set.outputs["完成"], win_text.inputs["执行"])

    # ---- Esc 退出 ----
    quit_key = add("GKNodeKey", "Esc", -1400.0, -3100.0,
                   gk_key="ESC", gk_action="quit_game", gk_trigger="JUST")
    quit_branch = add("GKNodeBranch", "如果_否则", -1000.0, -3100.0)
    do_quit = add("GKNodeQuitGame", "退出游戏", -700.0, -3100.0)
    tree.links.new(physics.outputs["执行"], quit_branch.inputs["执行"])
    tree.links.new(quit_key.outputs["结果"], quit_branch.inputs["条件"])
    tree.links.new(quit_branch.outputs["是"], do_quit.inputs["执行"])

    return tree


def write_export_settings(scene, player):
    settings = getattr(scene, "gk_settings", None)
    if settings is not None:
        for attr, value in (("dimension", "3"),
                            ("base_class", "CharacterBody3D"),
                            ("class_name", "LevelPlayer")):
            if hasattr(settings, attr):
                try:
                    setattr(settings, attr, value)
                except Exception:
                    pass
    for obj in bpy.data.objects:
        physics = getattr(obj, "gk_physics", None)
        if physics is None:
            continue
        try:
            physics.shape_size = (0.0, 0.0, 0.0)     # 全 0 = 自动贴合
        except Exception:
            pass
        if obj is player:
            physics.enabled = True
            physics.body_type = "CharacterBody3D"
            physics.dimension = "3"
            physics.script_target = True
        elif obj.name.startswith("金币"):
            physics.body_type = "Area3D"
        else:
            physics.body_type = "StaticBody3D"


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    scene = clear_scene()
    build_arena()
    coins = build_coins()
    goal = build_goal()
    player = build_player()
    build_cameras()
    build_light()
    build_logic(player, coins, goal)
    write_export_settings(scene, player)

    bpy.ops.wm.save_as_mainfile(filepath=BLEND_PATH)
    log(f"已保存: {BLEND_PATH}")
    log(f"关卡: 地面 {ARENA}x{ARENA} · 高台 {len(PLATFORMS)} · "
        f"金币 {len(coins)} · 终点门 1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
