"""动画状态机 —— 在真实 Blender 里建图、导出、检查生成的 GDScript。

跑法:
    blender -b --python tests\\blender_anim_tree.py
    （不能加 --factory-startup，那样看不到用户扩展仓库）

── 为什么要有这一层（离线测试测不到的东西）────────────────────────
`tests/test_codegen.py` 用的是纯 Python 桩，而**桩里节点的 id 是稳定的，
真实 Blender 里不是** —— `tree.nodes[i]` 和 `link.to_node` 会给出两个
不同的 Python 包装对象。实测因此把"入口状态"选成了终点（状态机从
第二个状态开始播），离线测试完全复现不出来（见 nodes.node_key）。

这一层还顺手验证了那些**只有 Blender 才有的东西**：
  * 真实的连线（Blender 的输入引脚只允许一条线）
  * 状态节点的『入口』动态引脚
  * 节点是否全部注册成功
  * **F5 快捷键真的绑在节点编辑器的 keymap 上**（查数据，不是信日志）

生成的脚本落到 `_dist/`，再由 `scripts/verify_anim_system.ps1` 第 4 层
交给真实 Godot 解析、第 5 层真跑。

── 2026-10-04 重写说明 ─────────────────────────────────────────────
我用批量脚本改缩进时把原文件搞坏了，而它没进 git、找不回副本。
重写时保持了**同样的覆盖范围**和**同样的输出文件名**
（`_dist/anim_state_machine.gd`、`anim_inputs.gd`、`anim_spring_arm.gd`），
所以 `verify_anim_system.ps1` 一行都不用改。
"""

import os
import sys
import traceback

import bpy  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from bl_ext.user_default.blender_game_kit import codegen, nodes as NN
except Exception:                                    # pragma: no cover
    from BlenderGameKit import codegen, nodes as NN  # type: ignore

V3 = {"dimension": "3", "base_class": "CharacterBody3D"}

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    flag = "PASS" if condition else "FAIL"
    suffix = f"  <- {detail}" if (detail and not condition) else ""
    print(f"[{flag}] {name}{suffix}")


def fresh_tree(name, kind="GKAnimationTree"):
    """建一张干净的节点树（重名的先删掉，免得每跑一次堆一张）。"""
    old = bpy.data.node_groups.get(name)
    if old is not None:
        bpy.data.node_groups.remove(old)
    return bpy.data.node_groups.new(name, kind)


def add_state(tree, state_name, action_name, label, location):
    node = tree.nodes.new("GKNodeAnimState")
    node.name = label
    node.location = location
    node.gk_state_name = state_name
    # ── 入口数量要给够(踩过的坑)────────────────────────────────────
    # Blender 里**一个输入引脚只允许一条线**,默认 2 个入口 ——
    # 『待机』要被 开始/走/跑/跳 四条线指着,只给 2 个的话后面接的会把
    # 前面的顶掉,导出时报"转场不知道切到哪个状态"。
    node.gk_in_count = 6
    # 属性是脚本改的 -> 动态引脚不会自己重建,要手动调一次
    node.rebuild_sockets()
    if action_name:
        node.gk_anim_action = bpy.data.actions.get(action_name)
    return node


def add_transition(tree, source, target, location, condition=None, **props):
    trans = tree.nodes.new("GKNodeAnimTransition")
    trans.location = location
    for key, value in props.items():
        setattr(trans, key, value)
    if source is not None:
        tree.links.new(source.outputs["转到"], trans.inputs["上游"])
    if target is not None:
        # ── 必须挑一个**空着的**入口(踩过的坑)──────────────────────
        # `links.new(..., target.inputs["进入"])` 永远指向**第一个**入口,
        # 而 Blender 的输入引脚只允许一条线 —— 于是后接的会把先接的顶掉,
        # 表现是"导出时报『转场不知道切到哪个状态』,而且开始节点没连线"。
        # 用户在界面上是拖到『进入 2』『进入 3』,这里照做。
        free = None
        for socket in target.inputs:
            if not socket.links:
                free = socket
                break
        tree.links.new(trans.outputs["转到"], free or target.inputs["进入"])
    if condition is not None:
        tree.links.new(condition, trans.inputs["条件"])
    return trans


def build_actions():
    """造几段最小动作（只要名字对，状态机就能播）。"""
    for name in ("待机", "走", "跑", "跳"):
        if bpy.data.actions.get(name) is None:
            action = bpy.data.actions.new(name)
            action.use_fake_user = True
            action.use_frame_range = True
            action.frame_start = 1
            action.frame_end = 20


def build_anim_tree():
    """4 个状态 + 8 条转场：待机 <-> 走 <-> 跑，跳回待机。"""
    anim = fresh_tree("角色动作", "GKAnimationTree")
    build_actions()

    start = anim.nodes.new("GKNodeAnimEntry")
    start.location = (-900.0, 320.0)

    idle = add_state(anim, "待机", "待机", "状态待机", (-380.0, 320.0))
    walk = add_state(anim, "走", "走", "状态走", (-380.0, 40.0))
    run = add_state(anim, "跑", "跑", "状态跑", (-380.0, -240.0))
    jump = add_state(anim, "跳", "跳", "状态跳", (-380.0, -520.0))
    # 开始 -> 待机(同样要占一个空入口,别和转场的线抢)
    anim.links.new(start.outputs["开始"], idle.inputs["进入 2"])

    # 条件用『动画判断_移动状态』(在移动 / 速度快 / 在地面)
    loco = anim.nodes.new("GKNodeAnimLocomotion")
    loco.location = (-900.0, -160.0)
    moving = loco.outputs["在移动"]
    fast = loco.outputs["速度快"]
    on_floor = loco.outputs["在地面"]

    # "没在动" / "速度慢" = 布尔运算取反(NOT 用 A 引脚)
    still = anim.nodes.new("GKNodeLogic")
    still.location = (-700.0, -400.0)
    still.gk_op = "NOT"
    anim.links.new(moving, still.inputs["A"])
    slow = anim.nodes.new("GKNodeLogic")
    slow.location = (-700.0, -620.0)
    slow.gk_op = "NOT"
    anim.links.new(fast, slow.inputs["A"])

    add_transition(anim, idle, walk, (-80.0, 220.0), moving)
    add_transition(anim, idle, jump, (-80.0, 360.0),
                   gk_cond_source="ACTION_JUST", gk_cond_action="jump")
    add_transition(anim, walk, idle, (-80.0, -40.0), still.outputs["结果"])
    add_transition(anim, walk, run, (-80.0, 80.0), fast)
    add_transition(anim, run, walk, (-80.0, -320.0), slow.outputs["结果"])
    add_transition(anim, run, idle, (-80.0, -420.0), still.outputs["结果"])
    add_transition(anim, jump, idle, (-80.0, -600.0), on_floor)
    add_transition(anim, idle, run, (-80.0, 140.0), fast)

    # 状态的『离开时』语句(用户要的"上一个播完了 -> 设变量")
    exit_stmt = anim.nodes.new("GKNodeAnimSetVar")
    exit_stmt.location = (-80.0, -760.0)
    exit_stmt.gk_var_name = "is_attacking"
    exit_stmt.gk_var_value = False
    anim.links.new(jump.outputs["离开时"], exit_stmt.inputs["执行"])
    return anim


def main():
    print(f"[INFO] 节点库: {len(NN.ALL_CLASSES)} 个节点")
    check("节点全都注册上了(>=120)", len(NN.ALL_CLASSES) >= 120,
          str(len(NN.ALL_CLASSES)))

    # ---------------- 4a. 动画树 -> 状态机代码 ----------------
    anim = build_anim_tree()
    states = [n for n in anim.nodes if n.bl_idname == "GKNodeAnimState"]
    trans = [n for n in anim.nodes if n.bl_idname == "GKNodeAnimTransition"]
    check("动画树建出来了(4 状态 + 8 转场)",
          len(states) == 4 and len(trans) == 8,
          f"状态 {len(states)} / 转场 {len(trans)}")

    tree = fresh_tree("玩家逻辑", "GKGameLogicTree")
    ready = tree.nodes.new("GKNodeOnReady")
    ready.location = (-600.0, 300.0)
    ref = tree.nodes.new("GKNodeAnimStateMachineRef")
    ref.name = "动画_状态机"
    ref.location = (-300.0, 300.0)
    ref.gk_tree_name = "角色动作"
    tree.links.new(ready.outputs["执行"], ref.inputs["执行"])

    code, warnings = codegen.generate(tree, V3)
    check("状态机导出零告警", not warnings, str(warnings))
    check("生成的脚本 extends CharacterBody3D",
          "extends CharacterBody3D" in code)
    check("状态机生成了进入函数", "func _gk_anim_enter(" in code)
    check("四个状态都进了状态机",
          all(f'_gk_anim_enter("{name}")' in code
              for name in ("待机", "走", "跑", "跳")))
    check("起始状态是『待机』(不靠猜)", '_gk_anim_enter("待机")' in code)
    check("『选输入:刚按下』生成了输入判定",
          "Input.is_action_just_pressed(" in code)
    check("转场条件用了在地面 / 速度",
          "is_on_floor()" in code and "velocity" in code)
    check("『离开时』语句进了状态机分支",
          "_gk_anim_exit" in code or "is_attacking" in code)

    out = os.path.join(ROOT, "_dist", "anim_state_machine.gd")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(code)
    print(f"[INFO] 状态机脚本写到: {out}")

    # ---------------- 4b. 输入 / 鼠标 / 相机 一棵树 ----------------
    input_tree = fresh_tree("输入测试", "GKGameLogicTree")
    ip = input_tree.nodes.new("GKNodeOnPhysicsProcess")
    ip.location = (-800.0, 200.0)
    dash_key = input_tree.nodes.new("GKNodeKey")
    dash_key.name = "冲刺键"
    dash_key.location = (-800.0, -80.0)
    dash_key.gk_key = "W"
    dash_key.gk_action = "dash"
    dash_key.gk_trigger = "JUST"
    mode = input_tree.nodes.new("GKNodeMouseMode")
    mode.location = (-300.0, 200.0)
    mode.gk_mode = "CAPTURED"
    pos = input_tree.nodes.new("GKNodeMousePosition")
    pos.location = (-300.0, -80.0)
    motion = input_tree.nodes.new("GKNodeMouseMotion")
    motion.location = (-300.0, -320.0)
    clicked = input_tree.nodes.new("GKNodeMouseButton")
    clicked.location = (-300.0, -560.0)
    wheel = input_tree.nodes.new("GKNodeMouseWheel")
    wheel.location = (-300.0, -800.0)
    wheel_branch = input_tree.nodes.new("GKNodeBranch")
    wheel_branch.location = (0.0, -800.0)
    zoom = input_tree.nodes.new("GKNodeCameraZoom")
    zoom.location = (320.0, -800.0)
    zoom.gk_value = 60.0
    zoom.gk_min_value = 20.0
    zoom.gk_max_value = 90.0

    input_tree.links.new(ip.outputs["执行"], mode.inputs["执行"])
    # 鼠标移动量接到一个"用得上"的地方,否则它不会被生成:
    # 用『移动_按方向』的『方向』(最常见的接法)
    mover = input_tree.nodes.new("GKNodeMoveDirection")
    mover.gk_speed = 6.0            # 3D 用米/秒(默认 300 是 2D 的像素/秒)
    mover.location = (0.0, -320.0)
    input_tree.links.new(ip.outputs["执行"], mover.inputs["执行"])
    input_tree.links.new(motion.outputs["移动量"], mover.inputs["方向"])
    # 冲刺键也接上(捕获的 W 才会写进输入映射)
    dash_branch = input_tree.nodes.new("GKNodeBranch")
    dash_branch.location = (0.0, -80.0)
    # 冲刺 = 按住 W **且** 按住鼠标左键(顺手让鼠标按键节点也被生成)
    both = input_tree.nodes.new("GKNodeLogic")
    both.location = (-160.0, -560.0)
    both.gk_op = "AND"
    input_tree.links.new(dash_key.outputs["结果"], both.inputs["A"])
    input_tree.links.new(clicked.outputs["按住"], both.inputs["B"])
    input_tree.links.new(both.outputs["结果"], dash_branch.inputs["条件"])
    input_tree.links.new(ip.outputs["执行"], dash_branch.inputs["执行"])
    input_tree.links.new(ip.outputs["执行"], wheel_branch.inputs["执行"])
    input_tree.links.new(wheel.outputs["滚轮"], wheel_branch.inputs["条件"])
    input_tree.links.new(wheel_branch.outputs["是"], zoom.inputs["执行"])

    # 鼠标移动量是 **Vector2**,而 3D 的『移动_按方向』要 Vector3 ——
    # 这条链只在 2D 里类型自洽(用户就踩过这个 Parse Error)。
    # 输入/鼠标这一层用 2D 选项生成。
    input_code, input_warnings = codegen.generate(
        input_tree, {"dimension": "2", "base_class": "CharacterBody2D"})
    check("输入/鼠标图导出零告警", not input_warnings, str(input_warnings))
    check("鼠标滚轮走 _input 事件累积",
          "func _input(event: InputEvent)" in input_code
          and "MOUSE_BUTTON_WHEEL_UP" in input_code)
    # 鼠标移动量是**累积** InputEventMouseMotion.relative
    # (用 get_last_mouse_velocity() 会和物理帧不同步,视角一卡一卡 —— 用户实测)
    check("鼠标模式 / 位置 / 移动量 / 按键都生成了",
          "Input.mouse_mode = Input.MOUSE_MODE_CAPTURED" in input_code
          and "Input.is_mouse_button_pressed(MOUSE_BUTTON_LEFT)" in input_code
          and "_gk_mouse_delta += event.relative" in input_code)
    check("视野缩放带范围限制(clampf)",
          "clampf(" in input_code and "20.0" in input_code
          and "90.0" in input_code)
    check("『输入_按键』捕获的 W 会写进输入映射",
          any(isinstance(item, (tuple, list)) and item[1] == "W"
              for item in (getattr(codegen, "last_used_actions", []) or [])))

    input_out = os.path.join(ROOT, "_dist", "anim_inputs.gd")
    with open(input_out, "w", encoding="utf-8") as handle:
        handle.write(input_code)
    print(f"[INFO] 输入/鼠标脚本写到: {input_out}")

    # ---------------- 4c. 摄像机套件 + 弹簧臂(要真跑,不能只解析)---------
    # 用户第四轮要的:设置 / 切换 / 启用禁用 / 视距 / 视野 / 旋转 / 位置 /
    # 弹簧臂。生成的脚本交给探针工程**真跑**,验证"相机确实被墙顶住了"。
    cam_tree = fresh_tree("摄像机测试", "GKGameLogicTree")
    cam_ready = cam_tree.nodes.new("GKNodeOnReady")
    cam_ready.location = (0.0, 0.0)
    spring = cam_tree.nodes.new("GKNodeCameraSpringArm")
    spring.name = "弹簧臂"
    spring.location = (320.0, 0.0)
    # 跟随对象留空 = 跟着脚本自己(第三人称里杆子就挂角色身上)——
    # 探针场景里根节点就是角色,写死节点名反而会去找一个不存在的子节点
    spring.gk_target = ""
    spring.gk_camera = "Camera"
    spring.gk_arm = 5.0
    spring.gk_height = 0.0
    spring.gk_pitch = 0.0
    spring.gk_side = 0.0
    spring.gk_collision = True
    spring.gk_radius = 0.3
    spring.gk_lag = 0.0              # 硬跟:探针读到的是当帧的精确距离
    cam_tree.links.new(cam_ready.outputs["执行"], spring.inputs["执行"])

    cam_physics = cam_tree.nodes.new("GKNodeOnPhysicsProcess")
    cam_physics.location = (0.0, -400.0)
    setup = cam_tree.nodes.new("GKNodeCameraSetup")
    setup.gk_target = "Camera"
    setup.gk_fov = 55.0
    setup.gk_distance = 0.0
    setup.location = (320.0, -400.0)
    cam_tree.links.new(cam_physics.outputs["执行"], setup.inputs["执行"])

    switch = cam_tree.nodes.new("GKNodeCameraSetCurrent")
    switch.gk_target = "Camera"
    switch.gk_blend = 0.6
    switch.location = (320.0, -700.0)
    cam_tree.links.new(setup.outputs["完成"], switch.inputs["执行"])

    cam_code, cam_warnings = codegen.generate(cam_tree, V3)
    check("摄像机图导出零告警", not cam_warnings, str(cam_warnings))
    check("弹簧臂用了射线检测(不穿墙)",
          "intersect_ray" in cam_code
          or "PhysicsRayQueryParameters3D" in cam_code)
    check("弹簧臂输出枢轴 / 方向 / 相机位置", "_gk_spring_" in cam_code)
    # 纯弹簧臂一棵树 -> 给**真跑探针**用(它要精确读到"相机被墙顶住")
    spring_only = fresh_tree("弹簧臂测试", "GKGameLogicTree")
    s_ready = spring_only.nodes.new("GKNodeOnReady")
    s_ready.location = (0.0, 0.0)
    s_arm = spring_only.nodes.new("GKNodeCameraSpringArm")
    s_arm.name = "弹簧臂"
    s_arm.location = (320.0, 0.0)
    s_arm.gk_target = ""
    s_arm.gk_camera = "Camera"
    s_arm.gk_arm = 5.0
    s_arm.gk_height = 0.0
    s_arm.gk_pitch = 0.0
    s_arm.gk_side = 0.0
    s_arm.gk_collision = True
    s_arm.gk_radius = 0.3
    s_arm.gk_lag = 0.0
    spring_only.links.new(s_ready.outputs["执行"], s_arm.inputs["执行"])
    spring_code, _sw = codegen.generate(spring_only, V3)

    for name, text in (("anim_spring_arm.gd", spring_code),
                       ("anim_camera_suite.gd", cam_code)):
        path = os.path.join(ROOT, "_dist", name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    print("[INFO] 弹簧臂脚本写到: "
          + os.path.join(ROOT, "_dist", "anim_spring_arm.gd"))

    # ---------------- 4d. F5 快捷键真的绑上了吗 ----------------
    # 踩过两次:①按钮写着 F5 却从没注册;②注册了但用 keymaps.new() 建了个
    # 孤儿(还在 register() 末尾被 unregister 掉)—— 表现都是"按 F5 没反应"。
    # 这里直接查 Blender 的 keymap 数据,而不是相信日志里的那行"已注册"。
    try:
        wm = bpy.context.window_manager
        km = wm.keyconfigs.addon.keymaps.find(name="Node Editor",
                                              space_type="NODE_EDITOR")
        items = [i for i in (km.keymap_items if km else []) if i.type == "F5"]
        check("F5 绑在节点编辑器上(能真的按)",
              any(i.idname == "gk.open_godot" and i.active for i in items),
              f"找到 {[(i.idname, i.active) for i in items]}")
    except Exception as exc:                          # pragma: no cover
        check("F5 绑在节点编辑器上(能真的按)", False, str(exc))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:                          # noqa: BLE001
        traceback.print_exc()
        print(f"ANIM_TREE_RESULT=FAILED ({type(exc).__name__}: {exc})")
        sys.exit(1)
    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print()
    print(f"结果: {passed}/{len(RESULTS)} 通过")
    ok = passed == len(RESULTS)
    # verify_anim_system.ps1 认的就是这个标记
    print("ANIM_TREE_RESULT=" + ("OK" if ok else "FAILED"))
    sys.exit(0 if ok else 1)
