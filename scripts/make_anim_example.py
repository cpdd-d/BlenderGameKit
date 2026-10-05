"""生成一个可以直接照抄的『动画状态机』示例工程(.blend)。

用法(必须在 Blender 里跑,它会建节点树和动作):
    blender -b --python scripts\\make_anim_example.py
生成:
    examples/demo_anim_tree/动画状态机示例.blend

── 这个示例演示了什么 ────────────────────────────────────────────
用户 2026-10-03 要的"一个状态多条转场"的照抄样板,外加两件他当时
卡住的事:

  1. **一个状态多条出线**:待机 / 走 / 跑 各自有好几条转场,
     按画布上从上到下的顺序判定(和虚幻 5 一样)
  2. **自定义条件引脚**:转场『条件来源』= 自己写,条件由
     『逻辑_布尔运算』+『输入_按键刚按下』+『动画_取变量』拼出来
  3. **自定义布尔变量**:动画树的『变量』面板里声明 is_hurt /
     is_attacking,用『动画_取变量』拉进图当条件;变量由**游戏逻辑图**
     里的『变量_设置』改(两张图编译成同一个脚本,变量是同一个)
  4. **状态直连 = 播完就切**:受击 / 攻击 的『转到』直接连回『待机』,
     不用放转场节点

示例角色是个方块(4~6 个动作都是它的位移/缩放关键帧),所以
"换动作"这件事一眼能看见 —— 把动作换成你自己角色的就行。
"""

import os
import sys

import bpy  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "examples", "demo_anim_tree")
OUT_BLEND = os.path.join(OUT_DIR, "动画状态机示例.blend")

# 状态名 = 动画名(导出后 Godot 的 AnimationPlayer 里就是这些名字)
STATES = ("待机", "走", "跑", "跳", "受击", "攻击")

# 每个动作:(帧 -> (位置, 缩放)),用方块的位置/缩放做"动作"
ACTIONS = {
    "待机": [(1, (0.0, 0.0, 0.0), 1.00), (20, (0.0, 0.0, 0.06), 1.02),
             (40, (0.0, 0.0, 0.0), 1.00)],
    "走": [(1, (0.0, 0.0, 0.0), 1.00), (12, (0.0, 0.0, 0.05), 1.00),
           (24, (0.0, 0.0, 0.0), 1.00)],
    "跑": [(1, (0.0, 0.0, 0.0), 1.05), (8, (0.0, 0.0, 0.12), 1.10),
           (16, (0.0, 0.0, 0.0), 1.05)],
    "跳": [(1, (0.0, 0.0, 0.0), 1.00), (12, (0.0, 0.0, 0.9), 1.00),
           (24, (0.0, 0.0, 0.0), 0.94)],
    "受击": [(1, (0.0, 0.0, 0.0), 1.00), (6, (0.25, 0.0, 0.0), 0.94),
             (14, (0.0, 0.0, 0.0), 1.00)],
    "攻击": [(1, (0.0, 0.0, 0.0), 1.00), (8, (0.5, 0.0, 0.1), 1.08),
             (18, (0.0, 0.0, 0.0), 1.00)],
}


def log(*parts):
    print("[示例]", *parts)


# ==========================================================================
# 场景:一个方块 + 6 个动作
# ==========================================================================
def clear_scene():
    """手动清场景。

    **不能**用 `read_factory_settings()`:那会把已启用的插件一起重置掉
    (README 第 17 条,实测踩过)。
    """
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    for group in list(bpy.data.node_groups):
        if getattr(group, "bl_idname", "") in ("GKAnimationTree",
                                               "GKGameLogicTree"):
            bpy.data.node_groups.remove(group)


def make_character():
    """建一个带 6 个动作的方块,当"示例角色"。"""
    mesh = bpy.data.meshes.new("示例角色")
    mesh.from_pydata(
        [(-0.5, -0.5, -0.5), (0.5, -0.5, -0.5), (0.5, 0.5, -0.5),
         (-0.5, 0.5, -0.5), (-0.5, -0.5, 0.5), (0.5, -0.5, 0.5),
         (0.5, 0.5, 0.5), (-0.5, 0.5, 0.5)],
        [], [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5),
             (2, 3, 7, 6), (3, 0, 4, 7)])
    mesh.update()
    obj = bpy.data.objects.new("示例角色", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = (0.0, 0.0, 0.0)

    obj.animation_data_create()
    for name, keys in ACTIONS.items():
        action = bpy.data.actions.new(name)
        obj.animation_data.action = action
        for frame, location, scale in keys:
            obj.location = location
            obj.scale = (scale, scale, scale)
            obj.keyframe_insert(data_path="location", frame=frame)
            obj.keyframe_insert(data_path="scale", frame=frame)
        # 动作时长 = 最后一帧(导出成 glTF 动画的长度)
        action.use_frame_range = True
        action.frame_start = keys[0][0]
        action.frame_end = keys[-1][0]
        log(f"动作 {name}: {keys[0][0]}~{keys[-1][0]} 帧")
    obj.animation_data.action = bpy.data.actions.get("待机")
    obj.scale = (1.0, 1.0, 1.0)
    obj.location = (0.0, 0.0, 0.0)
    return obj


def make_camera():
    """两台相机:主相机 + 特写机位(演示『相机_切换』的丝滑过渡)。"""
    camera_data = bpy.data.cameras.new("Camera")
    camera_data.lens = 50.0
    camera = bpy.data.objects.new("Camera", camera_data)
    camera.location = (0.0, -5.0, 1.6)
    camera.rotation_euler = (1.4, 0.0, 0.0)
    bpy.context.scene.collection.objects.link(camera)
    bpy.context.scene.camera = camera

    close_data = bpy.data.cameras.new("Camera")
    close_data.lens = 80.0
    close = bpy.data.objects.new("特写机位", close_data)
    close.location = (1.6, -2.2, 1.4)
    close.rotation_euler = (1.35, 0.0, 0.5)
    bpy.context.scene.collection.objects.link(close)

    light_data = bpy.data.lights.new("Sun", type="SUN")
    light_data.energy = 2.0
    light = bpy.data.objects.new("Sun", light_data)
    light.location = (2.0, -2.0, 4.0)
    bpy.context.scene.collection.objects.link(light)
    return camera, close


# ==========================================================================
# 节点树的搭法(照抄这一段就是搭法本身)
# ==========================================================================
def new_state(tree, name, location):
    state = tree.nodes.new("GKNodeAnimState")
    state.name = f"状态_{name}"
    state.location = location
    action = bpy.data.actions.get(name)
    if action is not None:
        state.gk_anim_action = action      # 选了动作 -> 状态名就是动画名
    else:
        state.gk_state_name = name
    return state


def new_transition(tree, location, from_state, to_state, cond_node=None,
                   cond_pin=None, cond_source="PIN", cond_action=""):
    """接一条转场:上游 <- 状态的『转到』,转到 -> 下一个状态的『进入』。

    『进入』有多个(『入口数量』可调)—— **一个输入引脚只允许一条线**,
    所以要先找个空的。都满了就把入口数量调大(重建引脚时会保住原有连线)。
    """
    trans = tree.nodes.new("GKNodeAnimTransition")
    trans.location = location
    trans.gk_cond_source = cond_source
    if cond_action:
        trans.gk_cond_action = cond_action
    tree.links.new(from_state.outputs["转到"], trans.inputs["上游"])

    inlet = None
    for socket in to_state.inputs:
        if not socket.is_linked:
            inlet = socket
            break
    if inlet is None:
        to_state.gk_in_count = min(8, len(to_state.inputs) + 1)
        to_state.rebuild_sockets()
        inlet = to_state.inputs[-1]
    tree.links.new(trans.outputs["转到"], inlet)

    if cond_node is not None:
        tree.links.new(cond_node.outputs[cond_pin], trans.inputs["条件"])
    return trans


def build_anim_tree():
    tree = bpy.data.node_groups.new("角色动作", "GKAnimationTree")
    # ── 必须给假用户,否则存盘时会被丢掉(踩过的坑)──────────────────
    # 没有任何物体/编辑器引用它的节点树 = 0 个用户,Blender 存盘时
    # **直接不写进文件**(也不报错)。第一次生成示例时就是这样:
    # 打开 .blend 里面空空如也。
    tree.use_fake_user = True

    # ---- 变量:照蓝图那样,一处声明、图里用『动画_取变量』取 ----
    for name, kind, initial, comment in (
            ("is_hurt", "bool", "0", "受击期间为真(玩法图里设)"),
            ("is_attacking", "bool", "0", "攻击期间为真(玩法图里设)"),
            ("walk_speed", "float", "1.2", "走路速度门槛(玩法图里设)")):
        spec = tree.gk_variables.add()
        spec.var_name = name
        spec.var_type = kind
        spec.default_value = initial
        spec.comment = comment
    log("动画树变量:", [v.var_name for v in tree.gk_variables])

    # ---- 判断节点 + 变量取值节点 ----
    loco = tree.nodes.new("GKNodeAnimLocomotion")
    loco.location = (-900.0, -120.0)
    loco.gk_speed_threshold = 0.6
    loco.gk_fast_threshold = 2.5

    get_hurt = tree.nodes.new("GKNodeAnimGetVar")
    get_hurt.location = (-900.0, -520.0)
    get_hurt.gk_var_name = "is_hurt"

    # 「跳」的条件:刚按下空格 与 在地面  (演示"转场条件就地拼")
    press_jump = tree.nodes.new("GKNodeActionJustPressed")
    press_jump.location = (-900.0, -300.0)
    press_jump.gk_action = "jump"

    can_jump = tree.nodes.new("GKNodeLogic")
    can_jump.location = (-640.0, -420.0)
    can_jump.gk_op = "AND"
    can_jump.gk_input_count = 2
    can_jump.build_sockets()
    tree.links.new(press_jump.outputs["刚按下"], can_jump.inputs["A"])
    tree.links.new(loco.outputs["在地面"], can_jump.inputs["B"])

    # ---- 状态 ----
    states = {}
    for index, name in enumerate(STATES):
        states[name] = new_state(tree, name,
                                 (0.0, -index * 200.0))

    # ---- 开始口 / 结束口(第三轮需求)----
    # 『动画_开始』:显式说明"从哪个状态开始",不再靠"谁没被指到"去猜;
    # 开始条件可以自定义(这里用"立刻",也能选变量 / 按键 / 上一段播完 / 接引脚)。
    start_node = tree.nodes.new("GKNodeAnimEntry")
    start_node.name = "动画开始"
    start_node.location = (-900.0, 300.0)
    start_node.gk_start_mode = "NOW"
    tree.links.new(start_node.outputs["开始"], states["待机"].inputs["进入"])

    # 『动画_结束』:受击 / 攻击 演完到这里 —— 状态机收工,并把
    # 『已结束』置真,玩法图用『动画_运行状态』读到后做下一步逻辑。
    exit_node = tree.nodes.new("GKNodeAnimExit")
    exit_node.name = "动画结束"
    exit_node.location = (900.0, 300.0)

    # ---- 转场(一个状态多条出线,从上到下判定)----
    new_transition(tree, (-420.0, -100.0), states["待机"], states["受击"],
                   get_hurt, "值")
    # 「攻击」用**条件来源 = 选输入**,不用拉节点(演示另一种写法)
    new_transition(tree, (220.0, -140.0), states["待机"], states["攻击"],
                   cond_source="ACTION_JUST", cond_action="attack")
    new_transition(tree, (900.0, -180.0), states["待机"], states["跳"],
                   can_jump, "结果")
    new_transition(tree, (1500.0, -220.0), states["待机"], states["走"],
                   loco, "在移动")

    new_transition(tree, (1500.0, -420.0), states["走"], states["跑"],
                   loco, "速度快")
    new_transition(tree, (2100.0, -460.0), states["走"], states["待机"],
                   loco, "速度慢")
    new_transition(tree, (2700.0, -500.0), states["走"], states["受击"],
                   get_hurt, "值")

    new_transition(tree, (1500.0, -700.0), states["跑"], states["待机"],
                   loco, "速度慢")
    new_transition(tree, (2100.0, -740.0), states["跑"], states["受击"],
                   get_hurt, "值")

    # 跳:落地就回待机(用『选输入』的兄弟 —— 这里用判断节点的『在地面』)
    new_transition(tree, (2700.0, -900.0), states["跳"], states["待机"],
                   loco, "在地面")

    # ---- 状态直连 = 这个动作播完就切过去(不用放转场节点)----
    def link_back(state, target=None):
        target = target if target is not None else states["待机"]
        inlet = None
        for socket in target.inputs:
            if not socket.is_linked:
                inlet = socket
                break
        if inlet is None:
            target.gk_in_count = min(8, len(target.inputs) + 1)
            target.rebuild_sockets()
            inlet = target.inputs[-1]
        tree.links.new(state.outputs["转到"], inlet)

    link_back(states["受击"])
    # 攻击打完了就**收工**(接结束口),由玩法图决定下一步 —— 演示结束口
    link_back(states["攻击"], exit_node)

    # ---- 状态的『进入时』/『离开时』:进这个状态时自己维护标志位 ----
    # 这就是"设置布尔变量"最自然的写法:受击状态**自己**负责开关
    # `is_hurt`,不用在玩法图里算计时器(也不会阻塞帧)。
    add_state_flag(tree, states["受击"], "is_hurt")
    add_state_flag(tree, states["攻击"], "is_attacking")

    log("动画树: %d 状态, %d 条连线(含开始/结束口 + 2 条直连 + 进入/离开时)"
        % (len(states), len(tree.links)))
    return tree


def add_state_flag(tree, state, var_name):
    """给一个状态装上"进入时置真 / 离开时置假"的两个设变量节点。

    布尔值要接『常量_布尔』—— 『变量_设置/动画_设变量』的『值』引脚是
    任意值,节点上那个数字框只能填数字。
    """
    for pin, value, offset in (("进入时", True, (620.0, 120.0)),
                               ("离开时", False, (620.0, -120.0))):
        const = tree.nodes.new("GKNodeConstBool")
        const.location = (state.location[0] + 200.0,
                          state.location[1] + offset[1])
        const.gk_value = value
        setter = tree.nodes.new("GKNodeAnimSetVar")
        setter.location = (state.location[0] + 420.0,
                           state.location[1] + offset[0])
        setter.gk_var_name = var_name
        tree.links.new(state.outputs[pin], setter.inputs["执行"])
        tree.links.new(const.outputs["值"], setter.inputs["值"])


def build_logic_tree():
    tree = bpy.data.node_groups.new("角色游戏逻辑", "GKGameLogicTree")
    tree.use_fake_user = True      # 同动画树:没有用户就会被存盘丢掉

    # ---- 变量声明(第三十三轮:用『变量分组』把示例整理干净)----------
    # 这三个原来只在图里**隐式使用**(第一次『变量_设置』写它就自动建成
    # 成员变量),侧栏『变量』框里看到的是一堆"未声明"。这里补上声明 ——
    # 名字 / 类型 / 初值 / 说明 / 分组一处说清,侧栏按组折叠显示。
    #
    # ⚠ is_hurt / is_attacking 在**动画树的变量面板**里也有一份(动画状态
    #   自己维护它们)。两边同名、同类型、同初值,生成出来还是同一个变量;
    #   玩法图这份只是让引用区认得出"它声明过"。
    # ⚠ combo_ready 只在这张图里(演示"一张卡片设两个变量")。
    for name, initial, comment, group in (
            ("is_hurt", "false", "受击期间为真(动画树用它当转场条件)", "状态"),
            ("is_attacking", "false", "攻击期间为真(动画树里自己维护)", "状态"),
            ("combo_ready", "false", "连段窗口(演示一张卡片设两个变量)", "状态")):
        var = tree.gk_vars.add()
        var.var_name = name
        var.var_type = "bool"
        var.default_value = initial
        var.comment = comment
        var.group = group

    # ---- 玩法图:一进游戏就启动状态机 + 摆好机位 ----
    ready = tree.nodes.new("GKNodeOnReady")
    ready.location = (-400.0, 200.0)
    ref = tree.nodes.new("GKNodeAnimStateMachineRef")
    ref.location = (0.0, 200.0)
    ref.gk_debug_print = True
    tree.links.new(ready.outputs["执行"], ref.inputs["执行"])

    # 开场:视野从 70 度缓动推到 45 度(1.2 秒)—— 时间轴的一条轨道
    tween_fov = tree.nodes.new("GKNodeTweenProperty")
    tween_fov.location = (400.0, 200.0)
    tween_fov.gk_target = "Camera"
    tween_fov.gk_property = "fov"
    tween_fov.gk_to = 45.0
    tween_fov.gk_time = 1.2
    tween_fov.gk_trans = "TRANS_CUBIC"
    tree.links.new(ref.outputs["完成"], tween_fov.inputs["执行"])
    # 开场顺手把机位丝滑推到特写(过渡 0.8 秒)—— 不按键也能看到效果
    open_switch = tree.nodes.new("GKNodeCameraSetCurrent")
    open_switch.location = (400.0, 380.0)
    open_switch.gk_target = "特写机位"
    open_switch.gk_blend = 0.8
    tree.links.new(tween_fov.outputs["完成"], open_switch.inputs["执行"])
    # 相机 fov 的起点(Scene 里那把相机默认 70)
    if bpy.context.scene.camera is not None:
        bpy.context.scene.camera.data.angle = 1.22   # ≈70 度

    # ---- 摄像机套件(第四轮,照虚幻的 Camera + SpringArm)--------------
    # ① 弹簧臂:主相机挂在"会缩的杆子"上 —— 第三人称跟随的标准做法,
    #    撞到墙会自动缩短(不会穿墙)。
    spring = tree.nodes.new("GKNodeCameraSpringArm")
    spring.name = "弹簧臂"
    spring.location = (400.0, 620.0)
    spring.gk_target = ""             # 留空 = 跟着脚本自己(角色)
    spring.gk_camera = "Camera"
    spring.gk_arm = 4.0
    spring.gk_height = 1.5
    spring.gk_side = 0.4
    spring.gk_pitch = -12.0
    spring.gk_collision = True
    spring.gk_lag = 8.0
    tree.links.new(ref.outputs["完成"], spring.inputs["执行"])

    # ② 相机_设置:一次把特写机位的视野 + 视距设好(带平滑)
    setup_close = tree.nodes.new("GKNodeCameraSetup")
    setup_close.name = "特写机位设置"
    setup_close.location = (400.0, 800.0)
    setup_close.gk_target = "特写机位"
    setup_close.gk_fov = 40.0
    setup_close.gk_distance = 0.0
    setup_close.gk_smooth = 0.6
    tree.links.new(spring.outputs["完成"], setup_close.inputs["执行"])

    # ---- 每个物理帧:移动(勾『重力模式』,它自己会 move_and_slide)+ 翻转 ----
    # 不要再接一个『移动_滑动』—— 『移动_按方向』里已经调过 move_and_slide(),
    # 接两个会生成两行 move_and_slide()(实测就是这么写出来的)。
    physics = tree.nodes.new("GKNodeOnPhysicsProcess")
    physics.location = (-400.0, -100.0)
    direction = tree.nodes.new("GKNodeGetVector")
    direction.location = (-400.0, -260.0)
    move = tree.nodes.new("GKNodeMoveDirection")
    move.location = (0.0, -100.0)
    move.gk_keep_y = True          # 「重力模式」:平台 / 有重力的游戏必须勾
    move.gk_speed = 6.0            # 3D 的单位是米/秒(默认 300 是 2D 的像素/秒)
    flip = tree.nodes.new("GKNodeFlip")
    flip.location = (400.0, -100.0)
    tree.links.new(physics.outputs["执行"], move.inputs["执行"])
    tree.links.new(direction.outputs["方向"], move.inputs["方向"])
    tree.links.new(move.outputs["完成"], flip.inputs["执行"])

    # ---- 按 H:把 is_hurt 打开(动画树那边用它当转场条件)----
    # ── 为什么不在玩法图里等 0.6 秒再关掉(踩过的坑)──────────────────
    # 一开始这里是 `等待_秒 -> 变量_设置(false)`。但 等待_秒 生成的是
    # `await`,而这段代码在 **_physics_process 里** —— await 会把后面的
    # 移动逻辑一起挂起,按一下受击键角色就"卡"住不动 0.6 秒。
    #
    # 改成"谁负责关"写在动画自己身上:**受击动画不在播了**就把它关掉。
    # 这样既不阻塞帧,又演示了『动画_是否在播』这个节点。
    const_true = tree.nodes.new("GKNodeConstBool")
    const_true.location = (200.0, -420.0)
    const_true.gk_value = True
    hurt_key = tree.nodes.new("GKNodeActionJustPressed")
    hurt_key.location = (-400.0, -520.0)
    hurt_key.gk_action = "hurt"
    hurt_branch = tree.nodes.new("GKNodeBranch")
    hurt_branch.location = (0.0, -520.0)
    set_hurt = tree.nodes.new("GKNodeSetVar")
    set_hurt.location = (400.0, -520.0)
    set_hurt.gk_var_name = "is_hurt"
    tree.links.new(physics.outputs["执行"], hurt_branch.inputs["执行"])
    tree.links.new(hurt_key.outputs["刚按下"], hurt_branch.inputs["条件"])
    tree.links.new(hurt_branch.outputs["是"], set_hurt.inputs["执行"])
    tree.links.new(const_true.outputs["值"], set_hurt.inputs["值"])

    # ---- 受击动画播完 -> 把 is_hurt 关掉 ----
    read_hurt = tree.nodes.new("GKNodeGetVar")
    read_hurt.location = (-400.0, -760.0)
    read_hurt.gk_var_name = "is_hurt"
    playing = tree.nodes.new("GKNodeAnimPlaying")
    playing.location = (-400.0, -900.0)
    playing.gk_anim_name = "受击"
    not_playing = tree.nodes.new("GKNodeLogic")
    not_playing.location = (-120.0, -900.0)
    not_playing.gk_op = "NOT"
    not_playing.gk_input_count = 1
    not_playing.build_sockets()
    both = tree.nodes.new("GKNodeLogic")
    both.location = (140.0, -820.0)
    both.gk_op = "AND"
    both.gk_input_count = 2
    both.build_sockets()
    clear_branch = tree.nodes.new("GKNodeBranch")
    clear_branch.location = (420.0, -760.0)
    const_false = tree.nodes.new("GKNodeConstBool")
    const_false.location = (600.0, -960.0)
    const_false.gk_value = False
    clear_hurt = tree.nodes.new("GKNodeSetVar")
    clear_hurt.location = (800.0, -760.0)
    clear_hurt.gk_var_name = "is_hurt"
    tree.links.new(physics.outputs["执行"], clear_branch.inputs["执行"])
    tree.links.new(read_hurt.outputs["值"], both.inputs["A"])
    tree.links.new(playing.outputs["在播"], not_playing.inputs["A"])
    tree.links.new(not_playing.outputs["结果"], both.inputs["B"])
    tree.links.new(both.outputs["结果"], clear_branch.inputs["条件"])
    tree.links.new(clear_branch.outputs["是"], clear_hurt.inputs["执行"])
    tree.links.new(const_false.outputs["值"], clear_hurt.inputs["值"])

    # ---- 按 C:丝滑切到特写机位 + 视野推进(时间轴的两条轨道)----
    # 两条语句串在一起,但 Tween 是**异步**的:调用完立刻返回,
    # 所以"换机位"和"推视野"是**同时在跑**的 —— 这就是"整体联动"。
    #
    # 按键用**新的『输入_按键』**(第三轮):节点上点『按一下键』就绑好了,
    # 不用记"switch_camera 绑哪个键"这套约定。这里显式填了动作名,
    # 是为了让它和脚本里已有的 switch_camera 对上。
    cam_key = tree.nodes.new("GKNodeKey")
    cam_key.location = (-400.0, -1100.0)
    cam_key.gk_action = "switch_camera"
    cam_key.gk_key = "C"               # 捕获到的键(导出时写进 project.godot)
    cam_key.gk_trigger = "JUST"        # 刚按下(一个节点顶三个)
    cam_branch = tree.nodes.new("GKNodeBranch")
    cam_branch.location = (0.0, -1100.0)
    switch_cam = tree.nodes.new("GKNodeCameraSetCurrent")
    switch_cam.location = (400.0, -1100.0)
    switch_cam.gk_target = "特写机位"
    switch_cam.gk_blend = 0.6          # 丝滑推镜
    tween_close = tree.nodes.new("GKNodeTweenProperty")
    tween_close.location = (800.0, -1100.0)
    tween_close.gk_target = "特写机位"
    tween_close.gk_property = "fov"
    tween_close.gk_to = 34.0
    tween_close.gk_time = 0.8
    tree.links.new(physics.outputs["执行"], cam_branch.inputs["执行"])
    tree.links.new(cam_key.outputs["结果"], cam_branch.inputs["条件"])
    tree.links.new(cam_branch.outputs["是"], switch_cam.inputs["执行"])
    tree.links.new(switch_cam.outputs["完成"], tween_close.inputs["执行"])

    # ---- 按 V:把特写机位**禁用**并切回主相机(演示启用/禁用)----------
    # 这就是"分支的场景摄像机设置":同一个事件里按不同相机各写一套
    # (『相机_启用』+『相机_切换』),用分支条件决定走哪一支。
    back_key = tree.nodes.new("GKNodeKey")
    back_key.name = "按V回主相机"
    back_key.location = (-400.0, -1900.0)
    back_key.gk_action = "back_camera"
    back_key.gk_key = "V"
    back_key.gk_trigger = "JUST"
    back_branch = tree.nodes.new("GKNodeBranch")
    back_branch.location = (0.0, -1900.0)
    disable_close = tree.nodes.new("GKNodeCameraActive")
    disable_close.name = "禁用特写机位"
    disable_close.location = (400.0, -1900.0)
    disable_close.gk_target = "特写机位"
    disable_close.gk_active = False
    back_switch = tree.nodes.new("GKNodeCameraSetCurrent")
    back_switch.name = "切回主相机"
    back_switch.location = (800.0, -1900.0)
    back_switch.gk_target = "Camera"
    back_switch.gk_blend = 0.5
    tree.links.new(physics.outputs["执行"], back_branch.inputs["执行"])
    tree.links.new(back_key.outputs["结果"], back_branch.inputs["条件"])
    tree.links.new(back_branch.outputs["是"], disable_close.inputs["执行"])
    tree.links.new(disable_close.outputs["完成"], back_switch.inputs["执行"])

    # ---- 动画"收工"之后:一次设两个变量(第三轮:一张卡片多个变量)----
    # 『动画_运行状态』把动画图的布尔情况交给玩法图 —— 这就是用户要的
    # "输出这一个游戏动画的布尔情况,以便于在游戏逻辑里判断执行下一步逻辑"。
    status = tree.nodes.new("GKNodeAnimStatus")
    status.location = (-400.0, -1400.0)
    done_branch = tree.nodes.new("GKNodeBranch")
    done_branch.location = (0.0, -1400.0)
    multi = tree.nodes.new("GKNodeSetVar")
    multi.location = (400.0, -1400.0)
    multi.gk_var_count = 2             # 一张卡片设两个变量(仿蓝图 Set)
    multi.gk_var_name = "is_attacking"
    multi.gk_var_name_2 = "combo_ready"
    multi.gk_value_2 = 1.0             # 第 2 个用数字框
    tree.links.new(physics.outputs["执行"], done_branch.inputs["执行"])
    tree.links.new(status.outputs["已结束"], done_branch.inputs["条件"])
    tree.links.new(done_branch.outputs["是"], multi.inputs["执行"])
    # 值 1 接『常量_布尔』;值 2 接另一个常量 —— **每个值引脚接不同节点**
    not_attacking = tree.nodes.new("GKNodeConstBool")
    not_attacking.location = (120.0, -1520.0)
    not_attacking.gk_value = False
    combo_flag = tree.nodes.new("GKNodeConstBool")
    combo_flag.location = (120.0, -1640.0)
    combo_flag.gk_value = True
    tree.links.new(not_attacking.outputs["值"], multi.inputs["值"])
    tree.links.new(combo_flag.outputs["值"], multi.inputs["值 2"])
    return tree


def main():
    clear_scene()
    character = make_character()
    make_camera()
    build_anim_tree()
    build_logic_tree()

    # 导出设置:脚本挂在示例角色身上(『游戏物理』面板里的『挂游戏逻辑脚本』)
    physics = getattr(character, "gk_physics", None)
    if physics is not None:
        physics.script_target = True
        physics.body_type = "CharacterBody3D"
    scene = bpy.context.scene
    settings = getattr(scene, "gk_settings", None)
    if settings is not None:
        settings.dimension = "3"
        settings.base_class = "CharacterBody3D"

    os.makedirs(OUT_DIR, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=OUT_BLEND)
    log("已保存:", OUT_BLEND)
    log("打开它 -> 节点编辑器切到『游戏动画』看『角色动作』这张树")
    print("ANIM_EXAMPLE_DONE")


if __name__ == "__main__":
    main()
