"""生成 **2.5D 示例工程(.blend)** —— 可以直接打开看节点怎么连。

用法:
    blender -b --python scripts\\make_demo_2d_blend.py
产出:
    examples/demo_2d_blend/2.5D示例.blend

── 这个示例是干什么的(用户 2026-10-03 第七轮的要求)────────────────
用户原话:"我想看的是创建一个 blender 文件,创建一个平面,添加两个摄像机
(运行时可以用滚轮放大缩小视野,切换2.5D视角),在游戏逻辑做输入,
动画_状态机,在游戏动画做切换动画等...然后创建 godot,直接运行。
这给的示例是 blender 文件,不然不知道节点怎么连,怎么设置。"

所以这个脚本产出的是一份**能直接打开的 .blend**:
  * 一块平面当地面、一个方块当角色、**两台相机**(侧视 / 俯瞰)
  * 『游戏动画』树:待机 / 走 / 跑 / 跳 四个状态 + 转场规则
  * 『游戏逻辑』树:输入 → 移动 → 跳跃 → 驱动状态机;滚轮缩放视野;
    按 C / V 在两台相机之间丝滑切换(这就是"切换 2.5D 视角")
打开之后:节点编辑器左上角把类型切成『游戏逻辑』或『游戏动画』,
两棵树的连线、节点上的参数**都是现成的**,照着抄即可。
"""

import os
import sys

import bpy  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "examples", "demo_2d_blend")
OUT_BLEND = os.path.join(OUT_DIR, "2.5D示例.blend")

STATES = ("待机", "走", "跑", "跳")

# 每个动作:(帧 -> (位置, 缩放))。方块的位置/缩放就是"动画",
# 换动作时一眼能看出区别 —— 把动作换成你自己角色的即可。
ACTIONS = {
    "待机": [(1, (0.0, 0.0, 0.0), 1.00), (20, (0.0, 0.0, 0.06), 1.03),
             (40, (0.0, 0.0, 0.0), 1.00)],
    "走": [(1, (0.0, 0.0, 0.0), 1.00), (12, (0.0, 0.0, 0.08), 1.00),
           (24, (0.0, 0.0, 0.0), 1.00)],
    "跑": [(1, (0.0, 0.0, 0.0), 1.06), (8, (0.0, 0.0, 0.16), 1.08),
           (16, (0.0, 0.0, 0.0), 1.06)],
    "跳": [(1, (0.0, 0.0, 0.00), 0.92), (10, (0.0, 0.0, 0.40), 1.15),
           (20, (0.0, 0.0, 0.00), 0.95)],
}


def log(*parts):
    print("[2.5D示例]", *parts)


def clear_scene():
    """手动清场景 —— 不能用 read_factory_settings(),那会把插件一起重置。"""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    for group in list(bpy.data.node_groups):
        if getattr(group, "bl_idname", "") in ("GKAnimationTree",
                                               "GKGameLogicTree"):
            bpy.data.node_groups.remove(group)


def make_mesh(name, verts, faces, location=(0.0, 0.0, 0.0),
              scale=(1.0, 1.0, 1.0)):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    obj.scale = scale
    return obj


def make_ground():
    """一块 20×20 的平面当地面(2.5D 里角色就在它上面跑)。"""
    ground = make_mesh(
        "地面",
        [(-10.0, -10.0, 0.0), (10.0, -10.0, 0.0),
         (10.0, 10.0, 0.0), (-10.0, 10.0, 0.0)],
        [(0, 1, 2, 3)])
    ground.location = (0.0, 0.0, -0.05)
    log("地面: 20×20 的平面(Blender 的 Z 轴向上;导出后在 Godot 里是地面)")
    return ground


def make_character():
    """方块角色 + 四个动作(待机 / 走 / 跑 / 跳)。"""
    obj = make_mesh(
        "示例角色",
        [(-0.5, -0.5, 0.0), (0.5, -0.5, 0.0), (0.5, 0.5, 0.0),
         (-0.5, 0.5, 0.0), (-0.5, -0.5, 1.0), (0.5, -0.5, 1.0),
         (0.5, 0.5, 1.0), (-0.5, 0.5, 1.0)],
        [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5),
         (2, 3, 7, 6), (3, 0, 4, 7)])

    obj.animation_data_create()
    for name, keys in ACTIONS.items():
        action = bpy.data.actions.new(name)
        action.use_fake_user = True      # 没有用户会被存盘丢掉(踩过)
        obj.animation_data.action = action
        for frame, location, scale in keys:
            obj.location = location
            obj.scale = (scale, scale, scale)
            obj.keyframe_insert(data_path="location", frame=frame)
            obj.keyframe_insert(data_path="scale", frame=frame)
        action.use_frame_range = True
        action.frame_start = keys[0][0]
        action.frame_end = keys[-1][0]
        log(f"动作 {name}: {keys[0][0]}~{keys[-1][0]} 帧")
    obj.animation_data.action = bpy.data.actions.get("待机")
    # ── 出生点要**站在地面上**(用户 2026-10-03:"站不住")────────────
    # 地面顶面在 z=0,而方块角色是 1x1x1、原点在几何中心:
    # 放在 z=0 时它有一半埋在地里 —— Blender 里看着像"贴着地面",
    # 到了 Godot 里胶囊碰撞体就有一半在地面之下,物理把它往下挤/穿过去。
    # 抬高半个身高(0.5)是标准做法:脚底正好踩在地面顶面。
    obj.location = (0.0, 0.0, 0.5)
    obj.scale = (1.0, 1.0, 1.0)
    return obj


def make_cameras():
    """**两台相机** —— 一台侧视(2D 玩法视角)、一台俯瞰(2.5D 视角)。

    运行时按 C / V 在这两台之间丝滑切换(见逻辑树里的『相机_切换』),
    滚轮则改当前相机的视野(fov)做放大缩小。
    """
    side_data = bpy.data.cameras.new("Camera")
    side_data.lens = 50.0
    side = bpy.data.objects.new("侧视机位", side_data)
    # Blender:相机朝 -Z 看。放在 -Y 侧、稍微抬高,正对地面中央
    side.location = (0.0, -9.0, 3.2)
    side.rotation_euler = (1.30, 0.0, 0.0)
    bpy.context.scene.collection.objects.link(side)
    bpy.context.scene.camera = side

    top_data = bpy.data.cameras.new("Camera")
    top_data.lens = 50.0
    top = bpy.data.objects.new("俯瞰机位", top_data)
    top.location = (0.0, -0.35, 12.0)
    top.rotation_euler = (0.10, 0.0, 0.0)
    bpy.context.scene.collection.objects.link(top)

    light_data = bpy.data.lights.new("Sun", type="SUN")
    light_data.energy = 3.0
    light = bpy.data.objects.new("Sun", light_data)
    light.location = (3.0, -3.0, 6.0)
    bpy.context.scene.collection.objects.link(light)
    log("相机: 侧视机位(2D 玩法) + 俯瞰机位(2.5D)")
    return side, top


# ==========================================================================
# 游戏动画树:待机 / 走 / 跑 / 跳
# ==========================================================================
def new_state(tree, name, location):
    state = tree.nodes.new("GKNodeAnimState")
    state.name = f"状态_{name}"
    state.location = location
    action = bpy.data.actions.get(name)
    if action is not None:
        state.gk_anim_action = action
    else:
        state.gk_state_name = name
    return state


def free_inlet(state):
    """给状态找一个空的『进入』引脚;都占了就把入口数量调大。"""
    for socket in state.inputs:
        if not socket.is_linked:
            return socket
    state.gk_in_count = min(8, len(state.inputs) + 1)
    state.rebuild_sockets()
    return state.inputs[-1]


def add_transition(tree, location, from_state, to_state, cond_node=None,
                   cond_pin=None, cond_source="PIN", cond_action=""):
    trans = tree.nodes.new("GKNodeAnimTransition")
    trans.location = location
    trans.gk_cond_source = cond_source
    if cond_action:
        trans.gk_cond_action = cond_action
    tree.links.new(from_state.outputs["转到"], trans.inputs["上游"])
    tree.links.new(trans.outputs["转到"], free_inlet(to_state))
    if cond_node is not None and cond_pin:
        tree.links.new(cond_node.outputs[cond_pin], trans.inputs["条件"])
    return trans


def build_anim_tree():
    tree = bpy.data.node_groups.new("角色动作", "GKAnimationTree")
    tree.use_fake_user = True      # 没有用户 -> 存盘就没了(踩过的坑)

    # ── 开始口:写明"从待机开始"(开始方式 = 立刻开始)──────────────
    start = tree.nodes.new("GKNodeAnimEntry")
    start.name = "动画开始"
    start.location = (-700.0, 300.0)
    start.gk_start_mode = "NOW"

    states = {}
    for index, name in enumerate(STATES):
        states[name] = new_state(tree, name, (0.0, -index * 260.0))
    tree.links.new(start.outputs["开始"], free_inlet(states["待机"]))

    # ── 条件来源:同一个『动画判断_移动状态』喂好几条转场 ────────────
    loco = tree.nodes.new("GKNodeAnimLocomotion")
    loco.name = "移动状态"
    loco.location = (-760.0, -120.0)
    loco.gk_slow = 0.1
    loco.gk_fast = 4.0

    # 待机 -> 走:在移动
    add_transition(tree, (-380.0, -60.0), states["待机"], states["走"],
                   loco, "在移动")
    # 走 -> 跑:速度快
    add_transition(tree, (-380.0, -320.0), states["走"], states["跑"],
                   loco, "速度快")
    # 跑 -> 走:速度慢下来(还在动)
    add_transition(tree, (-380.0, -560.0), states["跑"], states["走"],
                   loco, "速度慢")

    # 走 / 跑 -> 待机:不移动了就回待机
    speed_cond = tree.nodes.new("GKNodeLogic")
    speed_cond.name = "没在动"
    speed_cond.location = (-760.0, -420.0)
    speed_cond.gk_op = "NOT"
    speed_cond.gk_input_count = 1
    speed_cond.build_sockets()
    tree.links.new(loco.outputs["在移动"], speed_cond.inputs["A"])
    add_transition(tree, (-380.0, -120.0), states["走"], states["待机"],
                   speed_cond, "结果")
    add_transition(tree, (-380.0, -420.0), states["跑"], states["待机"],
                   speed_cond, "结果")

    # 待机 / 走 / 跑 -> 跳:刚按下空格(『选输入』这种不用拉节点)
    add_transition(tree, (620.0, -60.0), states["待机"], states["跳"],
                   cond_source="ACTION_JUST", cond_action="jump")
    add_transition(tree, (620.0, -320.0), states["走"], states["跳"],
                   cond_source="ACTION_JUST", cond_action="jump")
    add_transition(tree, (620.0, -560.0), states["跑"], states["跳"],
                   cond_source="ACTION_JUST", cond_action="jump")

    # 跳 -> 待机:落地(用『动画判断_移动状态』的『在地面』)
    add_transition(tree, (620.0, -800.0), states["跳"], states["待机"],
                   loco, "在地面")

    log(f"动画树『{tree.name}』: {len(states)} 状态, {len(tree.links)} 条连线")
    return tree


# ==========================================================================
# 游戏逻辑树:输入 → 移动 → 状态机;滚轮缩放;C/V 切视角
# ==========================================================================
def build_logic_tree():
    tree = bpy.data.node_groups.new("玩家逻辑", "GKGameLogicTree")
    tree.use_fake_user = True

    # ---- 准备:启动状态机 + 摆好两台相机 ----
    ready = tree.nodes.new("GKNodeOnReady")
    ready.location = (-900.0, 320.0)
    ref = tree.nodes.new("GKNodeAnimStateMachineRef")
    ref.name = "动画_状态机"
    ref.location = (-560.0, 320.0)
    ref.gk_tree_name = "角色动作"
    ref.gk_debug_print = True
    tree.links.new(ready.outputs["执行"], ref.inputs["执行"])

    # 开场先把鼠标**锁定并隐藏** —— 3D/2.5D 动作游戏的标准做法
    # (用户反馈"鼠标未隐藏":这个节点得自己加,不会自动生效)
    lock_mouse = tree.nodes.new("GKNodeMouseMode")
    lock_mouse.name = "锁定鼠标"
    lock_mouse.location = (-560.0, 480.0)
    lock_mouse.gk_mode = "CAPTURED"
    tree.links.new(ready.outputs["执行"], lock_mouse.inputs["执行"])

    # 开场:把侧视机位的视野设成 55 度(带 0.8 秒平滑)
    setup = tree.nodes.new("GKNodeCameraSetup")
    setup.name = "设定初始视野"
    setup.location = (-200.0, 320.0)
    setup.gk_target = "侧视机位"
    setup.gk_fov = 55.0
    setup.gk_smooth = 0.8
    tree.links.new(lock_mouse.outputs["完成"], setup.inputs["执行"])

    # ---- 物理帧:方向输入 → 移动 ----
    physics = tree.nodes.new("GKNodeOnPhysicsProcess")
    physics.location = (-900.0, -80.0)

    direction = tree.nodes.new("GKNodeInputDirection")
    direction.name = "方向输入"
    direction.location = (-900.0, -260.0)
    # 四个键各点各的『按一下键』捕获 —— 这里直接写好(导出的输入映射会带上)
    direction.gk_key_up = "W"
    direction.gk_key_down = "S"
    direction.gk_key_left = "A"
    direction.gk_key_right = "D"

    move = tree.nodes.new("GKNodeMoveDirection")
    move.name = "移动"
    move.location = (-520.0, -80.0)
    move.gk_speed = 6.0            # 3D 单位是米/秒
    move.gk_keep_y = True          # 有重力的游戏必须勾(平台跳跃)
    move.gk_turn_to_move = True
    tree.links.new(physics.outputs["执行"], move.inputs["执行"])
    tree.links.new(direction.outputs["方向"], move.inputs["方向"])

    # ---- 跳:在地面 且 刚按下空格 ----
    on_floor = tree.nodes.new(NN_IS_ON_FLOOR)
    on_floor.location = (-520.0, -400.0)
    jump_key = tree.nodes.new("GKNodeKey")
    jump_key.name = "跳跃键"
    jump_key.location = (-900.0, -520.0)
    jump_key.gk_key = "SPACE"
    jump_key.gk_action = "jump"
    jump_key.gk_trigger = "JUST"
    can_jump = tree.nodes.new("GKNodeLogic")
    can_jump.name = "能跳吗"
    can_jump.location = (-200.0, -420.0)
    can_jump.gk_op = "AND"
    can_jump.gk_input_count = 2
    can_jump.build_sockets()
    tree.links.new(on_floor.outputs["在地面"], can_jump.inputs["A"])
    tree.links.new(jump_key.outputs["结果"], can_jump.inputs["B"])
    jump_branch = tree.nodes.new("GKNodeBranch")
    jump_branch.location = (120.0, -420.0)
    do_jump = tree.nodes.new("GKNodeJump")
    do_jump.name = "起跳"
    do_jump.location = (440.0, -420.0)
    do_jump.gk_jump_force = 6.0    # 3D 的跳跃冲量也是米/秒
    tree.links.new(move.outputs["完成"], jump_branch.inputs["执行"])
    tree.links.new(can_jump.outputs["结果"], jump_branch.inputs["条件"])
    tree.links.new(jump_branch.outputs["是"], do_jump.inputs["执行"])

    # ---- 滚轮缩放视野(2.5D 里最常用的镜头操作)------------------
    # 读当前视野 -> 加上"滚轮 × 灵敏度" -> 写回去,并用 0.15 秒平滑
    wheel = tree.nodes.new("GKNodeMouseWheel")
    wheel.name = "滚轮"
    wheel.location = (-900.0, -760.0)
    zoom_step = tree.nodes.new("GKNodeMath")
    zoom_step.name = "滚轮转视野"
    zoom_step.location = (-620.0, -760.0)
    zoom_step.gk_op = "MUL"
    zoom_step.gk_b = 6.0           # 一格滚轮 = 6 度视野
    tree.links.new(wheel.outputs["滚轮"], zoom_step.inputs["A"])

    cam_status = tree.nodes.new("GKNodeCameraStatus")
    cam_status.name = "当前视野"
    cam_status.location = (-900.0, -940.0)
    add_zoom = tree.nodes.new("GKNodeMath")
    add_zoom.name = "算出新视野"
    add_zoom.location = (-340.0, -860.0)
    add_zoom.gk_op = "ADD"
    tree.links.new(cam_status.outputs["视野"], add_zoom.inputs["A"])
    tree.links.new(zoom_step.outputs["结果"], add_zoom.inputs["B"])

    apply_zoom = tree.nodes.new("GKNodeCameraZoom")
    # 视野范围:3D 里 20~90 度是舒服的区间 —— 不限制的话滚轮能一路把
    # 视野推到 170 度(画面变形)甚至负数(镜头废掉)
    apply_zoom.gk_min_value = 20.0
    apply_zoom.gk_max_value = 90.0
    apply_zoom.name = "改视野"
    apply_zoom.location = (0.0, -860.0)
    apply_zoom.gk_time = 0.15
    zoom_branch = tree.nodes.new("GKNodeBranch")
    zoom_branch.name = "滚轮动了吗"
    zoom_branch.location = (-340.0, -1060.0)
    zoom_gate = tree.nodes.new("GKNodeCompare")
    zoom_gate.gk_op = "NE"
    zoom_gate.location = (-620.0, -1060.0)
    zero = tree.nodes.new("GKNodeConstFloat")
    zero.gk_value = 0.0
    zero.location = (-760.0, -1140.0)
    printer = tree.nodes.new("GKNodePrint")
    printer.name = "调试打印"
    printer.gk_text = "滚轮缩放"
    printer.location = (440.0, -860.0)

    # 注意:这里是从**事件**直接拉的第二条执行线(不是接在『起跳』后面)——
    # 接在起跳后面的话,滚轮缩放和切视角就只在"跳起来"时才生效(踩过)
    tree.links.new(physics.outputs["执行"], zoom_branch.inputs["执行"])
    tree.links.new(wheel.outputs["滚轮"], zoom_gate.inputs["A"])
    tree.links.new(zero.outputs["值"], zoom_gate.inputs["B"])
    tree.links.new(zoom_gate.outputs["结果"], zoom_branch.inputs["条件"])
    tree.links.new(zoom_branch.outputs["是"], apply_zoom.inputs["执行"])
    tree.links.new(add_zoom.outputs["结果"], apply_zoom.inputs["数值"])
    # 『调试打印』挂在缩放后面,但**它是这条线的尽头** ——
    # 后面的切视角/跟随都从事件单独拉线(见下面)。
    tree.links.new(apply_zoom.outputs["完成"], printer.inputs["执行"])

    # ---- 按 C / V 切换 2D ↔ 2.5D 视角(丝滑过渡)------------------
    view_key = tree.nodes.new("GKNodeKey")
    view_key.name = "按 C 切俯瞰"
    view_key.location = (-900.0, -1320.0)
    view_key.gk_key = "C"
    view_key.gk_action = "view_top"
    view_key.gk_trigger = "JUST"
    view_branch = tree.nodes.new("GKNodeBranch")
    view_branch.location = (-520.0, -1320.0)
    to_top = tree.nodes.new("GKNodeCameraSetCurrent")
    to_top.name = "切到俯瞰(2.5D)"
    to_top.location = (-180.0, -1320.0)
    to_top.gk_target = "俯瞰机位"
    to_top.gk_blend = 0.6
    # ── 每条判断都从事件单独拉一条线(蓝图里就是这么连的)────────────
    # 踩过的坑:把"切视角/相机跟随"接在**上一个分支的里面**,它们就只在
    # 那个分支成立时才跑(生成的代码里整段被嵌进了 `if 滚轮动了吗:` 里)。
    # 一个事件输出可以拉好几条并行执行线 —— 各管各的,这才是蓝图的做法。
    tree.links.new(physics.outputs["执行"], view_branch.inputs["执行"])
    tree.links.new(view_key.outputs["结果"], view_branch.inputs["条件"])
    tree.links.new(view_branch.outputs["是"], to_top.inputs["执行"])

    back_key = tree.nodes.new("GKNodeKey")
    back_key.name = "按 V 切侧视"
    back_key.location = (-900.0, -1560.0)
    back_key.gk_key = "V"
    back_key.gk_action = "view_side"
    back_key.gk_trigger = "JUST"
    back_branch = tree.nodes.new("GKNodeBranch")
    back_branch.location = (-520.0, -1560.0)
    to_side = tree.nodes.new("GKNodeCameraSetCurrent")
    to_side.name = "切回侧视(2D)"
    to_side.location = (-180.0, -1560.0)
    to_side.gk_target = "侧视机位"
    to_side.gk_blend = 0.6
    tree.links.new(physics.outputs["执行"], back_branch.inputs["执行"])
    tree.links.new(back_key.outputs["结果"], back_branch.inputs["条件"])
    tree.links.new(back_branch.outputs["是"], to_side.inputs["执行"])

    # ---- 鼠标转视角(用户反馈"转动不了视角")--------------------
    # 鼠标移动量 × 灵敏度 -> 『相机_旋转』(保留模式 = 在现有朝向上加)。
    # 侧视/俯瞰两个机位都能这么转;俯仰加了 ±89° 限制,不会翻过头。
    look = tree.nodes.new("GKNodeMouseMotion")
    look.name = "鼠标移动量"
    look.location = (200.0, -1900.0)
    # 鼠标移动量是**二维**的(左右 x / 上下 y),而『相机_旋转』要三维 ——
    # 所以要拆成两个浮点,分别接到『偏航』『俯仰』(第一版直接接『旋转』,
    # Godot 解析期就报 Invalid operands "Vector3" and "Vector2")
    split = tree.nodes.new("GKNodeVectorSplit")
    split.name = "拆成 XY"
    split.location = (480.0, -1900.0)
    # ── 灵敏度必须乘"这一帧的时间"(用户反馈"旋转幅度过大")──────────
    # `鼠标_移动量` 是 **像素/秒**(Godot 的 get_last_mouse_velocity),
    # 直接加到角度上 = 每秒 1000 像素就转 1000 x 灵敏度 度/**帧** ——
    # 鼠标一甩画面直接飞出去。正确做法和第一人称游戏一样:
    #     角度增量 = 像素/秒 x 这一帧的秒数 x 每像素多少度
    # 0.12 = 鼠标移 100 像素转 12 度,手感正常。
    delta_scale = tree.nodes.new("GKNodeMath")
    # 灵敏度 = 每像素多少度。**不要再乘帧间隔** —— 移动量已经是"每帧像素",
    # 再乘一次 delta 会慢 60 倍(实测反馈:"灵敏度太低了,得滑好几次")
    delta_scale.name = "灵敏度"
    delta_scale.gk_op = "MUL"
    delta_scale.gk_b = 0.25
    delta_scale.location = (620.0, -1700.0)
    yaw_speed = tree.nodes.new("GKNodeMath")
    yaw_speed.name = "左右灵敏度"
    yaw_speed.gk_op = "MUL"
    # ── 符号决定方向(用户:"鼠标右移,视角往左……默认应该跟随鼠标")────
    # Godot 里相机 Y 轴**正**旋转 = 向左转,所以"鼠标往右 -> 视角往右"
    # 需要乘 **-1**。想反过来(飞行模拟那种)就把这个数改成正的。
    yaw_speed.gk_b = -1.0
    yaw_speed.location = (760.0, -1830.0)
    pitch_speed = tree.nodes.new("GKNodeMath")
    pitch_speed.name = "上下灵敏度"
    pitch_speed.gk_op = "MUL"
    # 鼠标往上(移动量 y 是负的)-> 相机 X 轴正旋转 = 往下看,
    # 所以这里乘 **-1** 才是"鼠标上移 -> 抬头"。同样:改符号就反转。
    pitch_speed.gk_b = -1.0
    pitch_speed.location = (760.0, -1990.0)
    turn = tree.nodes.new("GKNodeCameraRotation")
    turn.name = "转动视角"
    turn.gk_mode = "ADD"
    turn.gk_clamp_pitch = True
    turn.location = (1080.0, -1900.0)
    tree.links.new(look.outputs["移动量"], split.inputs["向量"])
    tree.links.new(delta_scale.outputs["结果"], yaw_speed.inputs["A"])
    tree.links.new(split.outputs["X"], yaw_speed.inputs["B"])
    tree.links.new(delta_scale.outputs["结果"], pitch_speed.inputs["A"])
    tree.links.new(split.outputs["Y"], pitch_speed.inputs["B"])
    tree.links.new(physics.outputs["执行"], turn.inputs["执行"])
    tree.links.new(yaw_speed.outputs["结果"], turn.inputs["偏航"])
    tree.links.new(pitch_speed.outputs["结果"], turn.inputs["俯仰"])

    # ---- 按 Esc 退出(用户:"没做运行后退出键盘按钮如 esc 退出")----
    quit_key = tree.nodes.new("GKNodeKey")
    quit_key.name = "Esc 退出"
    quit_key.location = (200.0, -2150.0)
    quit_key.gk_key = "ESC"
    quit_key.gk_action = "quit_game"
    quit_key.gk_trigger = "JUST"
    quit_branch = tree.nodes.new("GKNodeBranch")
    quit_branch.location = (560.0, -2150.0)
    do_quit = tree.nodes.new("GKNodeQuitGame")
    do_quit.location = (900.0, -2150.0)
    tree.links.new(physics.outputs["执行"], quit_branch.inputs["执行"])
    tree.links.new(quit_key.outputs["结果"], quit_branch.inputs["条件"])
    tree.links.new(quit_branch.outputs["是"], do_quit.inputs["执行"])

    # ---- 相机跟随:镜头跟着角色走(侧视时尤其需要)----------------
    follow = tree.nodes.new("GKNodeCameraFollow")
    follow.name = "相机跟随"
    follow.location = (200.0, -1560.0)
    follow.gk_smooth = 0.15
    follow.gk_camera = ""
    follow.gk_offset_x = 0.0
    follow.gk_offset_y = 0.0
    # 相机跟随要**每帧**跑 -> 直接从事件拉线,不要接在分支后面
    tree.links.new(physics.outputs["执行"], follow.inputs["执行"])

    log(f"逻辑树『{tree.name}』: {len(tree.nodes)} 个节点, "
        f"{len(tree.links)} 条连线")
    return tree


# 「取值_是否在地面」的类名 —— 单独提出来是为了让上面的代码读起来干净
NN_IS_ON_FLOOR = "GKNodeIsOnFloor"


def main():
    clear_scene()
    make_ground()
    make_character()
    make_cameras()
    build_anim_tree()
    build_logic_tree()

    # ── 把导出设置也一起存进 .blend(用户要"打开就能导出")────────────
    # 不设的话侧栏『脚本基类』是"自动",批量导出时会提示
    # "节点图里用了 move_and_slide,基类得是 CharacterBody3D"。
    # 设好之后:打开 -> 点导出 -> 直接就是对的。
    settings = getattr(bpy.context.scene, "gk_settings", None)
    if settings is not None:
        for attr, value in (("dimension", "3"),
                            ("base_class", "CharacterBody3D"),
                            ("class_name", "PlayerController")):
            if hasattr(settings, attr):
                try:
                    setattr(settings, attr, value)
                except Exception:
                    pass
        log("导出设置: 3D / CharacterBody3D / 类名 PlayerController")
    # ── 所有物体的"手填碰撞尺寸"清成 0 = 自动贴合 ────────────────────
    # 旧版本的 shape_size 默认值是 (0.5, 1.0, 0.5),老 .blend 里存着这组
    # 数字;现在虽然"自动"模式已经不理它了,但示例本身就该是干净的 ——
    # 用户打开面板时不该看见一堆莫名其妙的手动尺寸。
    for obj in bpy.data.objects:
        physics = getattr(obj, "gk_physics", None)
        if physics is None:
            continue
        try:
            physics.shape_size = (0.0, 0.0, 0.0)
        except Exception:
            pass

    # 角色物体在 Godot 里要是"角色体"(碰撞 + move_and_slide)
    for obj in bpy.data.objects:
        physics = getattr(obj, "gk_physics", None)
        if physics is not None and getattr(obj, "name", "") == "示例角色":
            for attr, value in (("body_type", "CharacterBody3D"),
                                ("dimension", "3")):
                if hasattr(physics, attr):
                    try:
                        setattr(physics, attr, value)
                    except Exception:
                        pass

    os.makedirs(OUT_DIR, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=OUT_BLEND)
    log(f"已保存: {OUT_BLEND}")
    log("打开它 -> 节点编辑器左上角切换『游戏逻辑』/『游戏动画』看两棵树")
    log("导出:侧栏『BlenderGameKit』-> 导出为 Godot 脚本 + 同时生成示例场景")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        sys.exit(1)
