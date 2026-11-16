"""Build a self-contained Blender -> Godot blueprint integration example.

Run Blender with --background --factory-startup --python this_file.
Only the dedicated examples/demo_blueprint_lab output is written.
"""
from pathlib import Path
import json
import math
import struct
import sys
import wave

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "examples" / "demo_blueprint_lab"
PROJECT = OUT / "BlueprintLab_godot"
BLEND = OUT / "BlueprintLab.blend"


def node(tree, kind, name, x, y, **props):
    item = tree.nodes.new(kind)
    item.name = name
    item.label = name
    item.location = (x, y)
    for key, value in props.items():
        setattr(item, key, value)
    return item


def link(tree, source, output, target, input_name="执行"):
    tree.links.new(source.outputs[output], target.inputs[input_name])


def box(name, size, position, color, *, solid=True):
    bpy.ops.mesh.primitive_cube_add(size=1, location=position)
    obj = bpy.context.object
    obj.name = name
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    material = bpy.data.materials.new(name + "Material")
    material.diffuse_color = color
    material.use_nodes = True
    material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = color
    material.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.78
    obj.data.materials.append(material)
    obj.gk_physics.enabled = solid
    obj.gk_physics.body_type = "StaticBody3D"
    obj.gk_physics.dimension = "3"
    if not solid:
        obj.gk_physics.collision_shape = "none"
    return obj


def make_sound():
    path = OUT / "activation.wav"
    rate = 22050
    duration = 0.45
    with wave.open(str(path), "wb") as handle:
        handle.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        handle.writeframes(b"".join(struct.pack("<h", int(11000 *
            min(1.0, t / 0.02) * (1.0 - t / duration) ** 2 *
            (math.sin(2 * math.pi * 660 * t) + 0.3 * math.sin(2 * math.pi * 990 * t))))
            for t in (i / rate for i in range(int(rate * duration)))))
    return path


def make_scene():
    scene = bpy.context.scene
    scene.name = "BlueprintLab"
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for tree in list(bpy.data.node_groups):
        if tree.bl_idname.startswith("GK"):
            bpy.data.node_groups.remove(tree)
    navy = (0.035, 0.065, 0.105, 1)
    slate = (0.11, 0.19, 0.26, 1)
    teal = (0.05, 0.66, 0.64, 1)
    gold = (0.95, 0.52, 0.12, 1)
    box("Floor", (14, 14, 0.5), (0, 0, -0.25), navy)
    for x in (-6.8, 6.8):
        box("SideRail", (0.3, 14, 0.7), (x, 0, 0.35), slate)
    for x, width in ((-6.2, 1.2), (0, 1.8), (6.2, 1.2)):
        box("GateWall", (width, 0.5, 2.4), (x, 3, 1.2), slate)
    gates, terminals = [], []
    for suffix, x, color in (("A", -3.2, teal), ("B", 3.2, gold)):
        gates.append(box("EnergyGate" + suffix, (4.2, 0.35, 2.4), (x, 3, 1.2), color))
        box("GateHeader" + suffix, (4.7, 0.7, 0.35), (x, 3, 3.5), color)
        terminals.append(box("Terminal" + suffix, (1.1, 0.8, 1.4), (x, 0, 0.7), slate))
        box("TerminalScreen" + suffix, (0.85, 0.08, 0.5), (x, -0.44, 1.15), color, solid=False)
    for y in (-4.5, -3, -1.5, 0, 1.5, 4.5, 6):
        box("GuideLight", (0.12, 0.65, 0.02), (0, y, 0.02), teal, solid=False)
    player = box("Player", (0.65, 0.65, 1.3), (0, -4.5, 0.7), gold)
    player.gk_physics.body_type = "CharacterBody3D"
    player.gk_physics.script_target = True
    player.gk_physics.shape_size = (0, 0, 0)
    # Reusable static asset: one mesh, three placed instances.
    for i, x in enumerate((-4.5, 0, 4.5)):
        prop = box("Beacon" + str(i), (0.6, 0.6, 1.6), (x, 5.5, 0.8), teal)
        prop.gk_physics.asset_name = "LabBeacon"
        prop.gk_physics.asset_instance = i > 0
    goal = box("GoalZone", (3.5, 1.5, 2), (0, 6.2, 1), teal)
    goal.gk_physics.body_type = "Area3D"
    goal.gk_physics.is_trigger = True
    goal.gk_physics.hide_mesh = True
    goal.gk_physics.collision_shape = "box"
    camera_data = bpy.data.cameras.new("LabCamera")
    camera = bpy.data.objects.new("LabCamera", camera_data)
    scene.collection.objects.link(camera)
    camera.location = (0, -15, 13)
    camera.rotation_euler = (Vector((0, 1.5, 2.5)) - camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = 26
    scene.camera = camera
    lamp_data = bpy.data.lights.new("KeyLight", "SUN")
    lamp_data.energy = 2.2
    lamp = bpy.data.objects.new("KeyLight", lamp_data)
    scene.collection.objects.link(lamp)
    lamp.rotation_euler = (0.6, -0.4, -0.5)
    scene.render.fps = 30
    scene.frame_start, scene.frame_end = 1, 31
    action = bpy.data.actions.new("GateOpen")
    action["gk_anim_category"] = "其他"
    for gate in gates:
        gate.animation_data_create()
        gate.animation_data.action = action
    for frame, height in ((1, 1.2), (31, 4.0)):
        gates[0].location.z = height
        gates[0].keyframe_insert("location", index=2, frame=frame)
    # Blender 4.5 creates the Action slot on first insertion, after assignment.
    # The second object must select that same OBJECT slot to sample the Action.
    action_slot = getattr(gates[0].animation_data, "action_slot", None)
    if action_slot is not None:
        for gate in gates[1:]:
            gate.animation_data.action_slot = action_slot
    scene.frame_set(1)
    settings = scene.gk_settings
    settings.project_dir = str(PROJECT)
    settings.toon_materials = True
    settings.toon_follow_light = True
    settings.env_ambient_energy = 0.7
    return scene, player, gates, terminals, action


def make_role(scene, player):
    tree = bpy.data.node_groups.new("角色·只负责移动", "GKGameLogicTree")
    tree.gk_scope = "ROLE"
    tree.use_fake_user = True
    player.gk_physics.logic_tree = tree.name
    tick = node(tree, "GKNodeOnPhysicsProcess", "角色物理帧", 0, 0)
    direction = node(tree, "GKNodeInputDirection", "WASD输入", 0, -220)
    move = node(tree, "GKNodeMoveDirection", "角色移动", 280, 0,
                gk_speed=4.0, gk_keep_y=True, gk_follow_camera=True)
    link(tree, tick, "执行", move)
    link(tree, direction, "方向", move, "方向")
    return tree


def make_design():
    tree = bpy.data.node_groups.new("界面·实验室HUD", "GKUiDesignTree")
    tree.use_fake_user = True
    node(tree, "GKNodeUiPanel", "LabHeader", 0, 0,
         gk_align="TOP_CENTER", gk_margin=18, gk_width=650, gk_height=152, gk_radius=14,
         gk_color=(0.02, 0.04, 0.07, 0.92))
    for i, (name, text, size, color) in enumerate((
        ("Title", "能源门 / BLUEPRINT LAB", 22, (0.25, 0.95, 0.87, 1)),
        ("Status", "已开启 0 / 2：靠近两侧控制台，分别按 E", 18, (1, 1, 1, 1)),
        ("Controls", "WASD 移动 · E 交互 · Esc 暂停 · R 重开", 14, (0.67, 0.76, 0.83, 1)),
    )):
        node(tree, "GKNodeUiText", name, 280 + i * 280, 0,
             gk_text=text, gk_size=size, gk_color=color,
             gk_x=20, gk_y=12 + i * 31, gk_parent_widget="LabHeader")
    return tree


def declare(tree, name, kind="int", default="0"):
    item = tree.gk_vars.add()
    item.var_name, item.var_type, item.default_value = name, kind, default
    item.group = "实例状态"
    item.watch = True


def function(tree, name, return_type="void", params=""):
    item = tree.gk_funcs.add()
    item.func_name, item.return_type, item.params = name, return_type, params


def make_gate_behavior(gates, terminals, player, action, sound):
    """One role graph, two independent instances; no gate names in this graph."""
    tree = bpy.data.node_groups.new("组件·可复用能源门", "GKGameLogicTree")
    tree.gk_scope = "ROLE"
    tree.use_fake_user = True
    for gate, terminal in zip(gates, terminals):
        gate.gk_physics.script_target = True
        gate.gk_physics.collision_shape = "box"
        gate.gk_physics.logic_tree = tree.name
        for name, target in (("玩家", player), ("控制台", terminal)):
            binding = gate.gk_physics.logic_bindings.add()
            binding.binding_name, binding.target = name, target
    for name in ("activation_count", "completed"):
        declare(tree, name)
    declare(tree, "accepted", "bool", "false")
    function(tree, "try_activate", "bool", "radius:float")
    tick = node(tree, "GKNodeOnPhysicsProcess", "实例输入", 0, 300)
    key = node(tree, "GKNodeKey", "交互键E", 0, 60, gk_key="E", gk_action="interact", gk_trigger="JUST")
    branch = node(tree, "GKNodeBranch", "按下E", 260, 300)
    call = node(tree, "GKNodeFunctionCall", "调用共享交互函数", 520, 300, gk_func_name="try_activate")
    call.build_sockets()
    keyboard_radius = node(tree, "GKNodeConstFloat", "键盘交互半径", 260, 60, gk_value=2.2)
    link(tree, keyboard_radius, "值", call, "radius")
    link(tree, tick, "执行", branch)
    link(tree, key, "结果", branch, "条件")
    link(tree, branch, "是", call)
    entry = node(tree, "GKNodeFunctionDefine", "共享交互入口", 0, -300, gk_func_name="try_activate")
    reset = node(tree, "GKNodeSetVar", "默认拒绝本次请求", 0, -60, gk_var_name="accepted", gk_value=0)
    accepted = node(tree, "GKNodeGetVar", "返回本次是否接受", -260, -300, gk_var_name="accepted")
    radius = node(tree, "GKNodeGetVar", "读取调用方半径", 520, -760, gk_var_name="radius")
    state = node(tree, "GKNodeGetVar", "本实例激活次数", 0, -540, gk_var_name="activation_count")
    pending = node(tree, "GKNodeCompare", "本实例尚未激活", 260, -540, gk_op="EQ", gk_b="0")
    available = node(tree, "GKNodeBranch", "允许交互", 260, -300)
    distance = node(tree, "GKNodeDistance", "引用玩家与控制台", 520, -540, gk_a="@玩家", gk_b="@控制台")
    compare = node(tree, "GKNodeCompare", "按传入半径验证距离", 780, -540, gk_op="LE")
    near = node(tree, "GKNodeBranch", "附近才触发", 780, -300)
    once = node(tree, "GKNodeDoOnce", "本实例只激活一次", 1040, -300)
    count = node(tree, "GKNodeAddVar", "记录本实例次数", 1300, -300, gk_var_name="activation_count", gk_amount=1)
    accept = node(tree, "GKNodeSetVar", "接受本次请求", 1300, -60, gk_var_name="accepted", gk_value=1)
    audio = node(tree, "GKNodePlaySound", "实例确认音", 1560, -300, gk_path=str(sound), gk_volume=-9, gk_2d=False)
    play = node(tree, "GKNodePlayAnim", "本实例原生开门动作", 1820, -300,
                gk_anim_action=action, gk_blend_mode="CONTINUE")
    done = node(tree, "GKNodeSetVar", "记录本实例完成", 2080, -300, gk_var_name="completed", gk_value=1)
    signal = node(tree, "GKNodeEmitSignal", "广播本实例已开启", 2340, -300, gk_signal_name="opened")
    link(tree, entry, "执行", reset)
    link(tree, reset, "完成", available)
    link(tree, accepted, "值", entry, "返回")
    link(tree, state, "值", pending, "A")
    link(tree, pending, "结果", available, "条件")
    link(tree, available, "是", near)
    link(tree, distance, "距离", compare, "A")
    link(tree, radius, "值", compare, "B")
    link(tree, compare, "结果", near, "条件")
    link(tree, near, "是", once)
    link(tree, once, "首次", count)
    link(tree, count, "完成", accept)
    link(tree, accept, "完成", audio)
    link(tree, audio, "完成", play)
    link(tree, play, "结束时", done)
    link(tree, done, "完成", signal)
    return tree


def make_level(scene, gates, design, behavior):
    """Level aggregates opened events; instance behavior stays in the shared role."""
    tree = bpy.data.node_groups.new("关卡·双门协作与通关", "GKGameLogicTree")
    tree.gk_scope = "LEVEL"
    tree.use_fake_user = True
    scene.gk_settings.logic_tree = tree.name
    for name in ("opened_count", "victory"):
        declare(tree, name)
    function(tree, "on_gate_opened")
    ready = node(tree, "GKNodeOnReady", "关卡初始化", 0, 600)
    apply = node(tree, "GKNodeUiApplyDesign", "创建HUD一次", 260, 600, gk_design=design.name)
    link(tree, ready, "执行", apply)
    make_pause_menu(tree, apply)
    for index, gate in enumerate(gates):
        connect = node(tree, "GKNodeConnect", "订阅门" + str(index + 1), 520, 600 - index * 220,
                       gk_target=gate.name, gk_signal_name="opened", gk_handler="on_gate_opened")
        button = node(tree, "GKNodeUiButton", "InteractButton" + str(index + 1), 780, 600 - index * 220,
                      gk_text=("青色" if index == 0 else "金色") + "控制台 · " + ("1.4m" if index == 0 else "2.2m"),
                      gk_x=20 + index * 210, gk_y=106, gk_width=196, gk_height=34,
                      gk_parent_widget="LabHeader")
        call = node(tree, "GKNodeFunctionCall", "带参调用门实例" + str(index + 1), 1040, 600 - index * 220,
                    gk_function_tree=behavior.name, gk_target=gate.name, gk_func_name="try_activate")
        call.build_sockets()
        radius = node(tree, "GKNodeConstFloat", "按钮交互半径" + str(index + 1), 1040, 850 - index * 220,
                      gk_value=1.4 if index == 0 else 2.2)
        feedback = node(tree, "GKNodeBranch", "读取接受结果" + str(index + 1), 1300, 600 - index * 220)
        accepted = node(tree, "GKNodeUiSubtitle", "接受反馈" + str(index + 1), 1560, 600 - index * 220,
                        gk_text="启动请求已接受，等待能源门升起。", gk_seconds=0.8, gk_fade=0.1, gk_speed=0)
        rejected = node(tree, "GKNodeUiSubtitle", "拒绝反馈" + str(index + 1), 1820, 600 - index * 220,
                        gk_text="请求未接受：请靠近控制台，并确认此门尚未开启。", gk_seconds=0.8, gk_fade=0.1, gk_speed=0)
        link(tree, apply, "完成", connect)
        link(tree, connect, "完成", button)
        link(tree, button, "点击", call)
        link(tree, radius, "值", call, "radius")
        link(tree, call, "完成", feedback)
        link(tree, call, "结果", feedback, "条件")
        link(tree, feedback, "是", accepted)
        link(tree, feedback, "否", rejected)
    tick = node(tree, "GKNodeOnPhysicsProcess", "只监听重开", 0, 100)
    restart_key = node(tree, "GKNodeKey", "重开键R", 0, -120, gk_key="R", gk_action="restart", gk_trigger="JUST")
    branch = node(tree, "GKNodeBranch", "重开判断", 260, 100)
    restart = node(tree, "GKNodeReloadScene", "重新开始", 520, 100)
    link(tree, tick, "执行", branch)
    link(tree, restart_key, "结果", branch, "条件")
    link(tree, branch, "是", restart)
    event = node(tree, "GKNodeFunctionDefine", "门开启事件", 0, -500, gk_func_name="on_gate_opened")
    count = node(tree, "GKNodeAddVar", "汇总已开启数量", 260, -500, gk_var_name="opened_count", gk_amount=1)
    compare = node(tree, "GKNodeCompare", "两门都已开启", 520, -740, gk_op="GE", gk_b="2")
    done = node(tree, "GKNodeBranch", "更新任务进度", 520, -500)
    all_open = node(tree, "GKNodeUiSet", "完成双门目标", 780, -500, gk_widget="Status", gk_set_text=True,
                    gk_text="已开启 2 / 2：穿过能源门，抵达中央信标！")
    partial = node(tree, "GKNodeUiSet", "还剩一扇门", 780, -760, gk_widget="Status", gk_set_text=True,
                   gk_text="已开启 1 / 2：另一扇门的状态仍然独立。")
    link(tree, event, "执行", count)
    link(tree, count, "新值", compare, "A")
    link(tree, count, "完成", done)
    link(tree, compare, "结果", done, "条件")
    link(tree, done, "是", all_open)
    link(tree, done, "否", partial)
    arrived = node(tree, "GKNodeOnAreaEntered", "抵达出口信标", 0, -1100, gk_area="GoalZone", gk_filter="Player")
    opened = node(tree, "GKNodeGetVar", "读取关卡进度", 0, -1340, gk_var_name="opened_count")
    enough = node(tree, "GKNodeCompare", "完成两门才能通关", 260, -1340, gk_op="GE", gk_b="2")
    valid = node(tree, "GKNodeBranch", "验证通关条件", 260, -1100)
    victory = node(tree, "GKNodeSetVar", "任务完成", 520, -1100, gk_var_name="victory", gk_value=1)
    message = node(tree, "GKNodeUiSet", "通关反馈", 780, -1100, gk_widget="Status", gk_set_text=True,
                   gk_text="实验完成！同一蓝图，两个独立实例。按 R 再试。")
    link(tree, arrived, "执行", valid)
    link(tree, opened, "值", enough, "A")
    link(tree, enough, "结果", valid, "条件")
    link(tree, valid, "是", victory)
    link(tree, victory, "完成", message)
    return tree


def make_pause_menu(tree, ready_tail):
    """Build a modal pause menu entirely from existing UI nodes."""
    panel = node(tree, "GKNodeUiPanel", "PauseMenu", 0, 1200,
                 gk_align="CENTER", gk_margin=0, gk_width=420, gk_height=252,
                 gk_start_hidden=True, gk_bottom=False, gk_block_click=True,
                 gk_focus_first=True, gk_color=(0.025, 0.055, 0.085, 0.98))
    title = node(tree, "GKNodeUiText", "PauseTitle", 260, 1200,
                 gk_text="已暂停", gk_size=28, gk_align="TOP_CENTER", gk_margin=22,
                 gk_parent_widget="PauseMenu")
    hint = node(tree, "GKNodeUiText", "PauseHint", 520, 1200,
                gk_text="进度与动画停在当前时刻", gk_size=16,
                gk_align="TOP_CENTER", gk_margin=64, gk_parent_widget="PauseMenu")
    resume_button = node(tree, "GKNodeUiButton", "ResumeButton", 780, 1200,
                         gk_text="继续游戏  [Enter]", gk_x=70, gk_y=104,
                         gk_width=280, gk_height=46, gk_parent_widget="PauseMenu")
    restart_button = node(tree, "GKNodeUiButton", "RestartButton", 1040, 1200,
                          gk_text="重新开始", gk_x=70, gk_y=164,
                          gk_width=280, gk_height=46, gk_parent_widget="PauseMenu")
    arm = node(tree, "GKNodeUiPauseGame", "注册Esc暂停", 1300, 1200,
               gk_pause=False, gk_menu="PauseMenu", gk_hotkey=True, gk_resume_cooldown=0)
    resume = node(tree, "GKNodeUiPauseGame", "从暂停恢复", 1040, 950,
                  gk_pause=False, gk_menu="PauseMenu", gk_hotkey=False, gk_resume_cooldown=0)
    unpause = node(tree, "GKNodeUiPauseGame", "重开前恢复", 1300, 950,
                   gk_pause=False, gk_menu="PauseMenu", gk_hotkey=False, gk_resume_cooldown=0)
    restart = node(tree, "GKNodeReloadScene", "暂停菜单重开", 1560, 950)
    open_button = node(tree, "GKNodeUiButton", "PauseButton", 1560, 1200,
                       gk_text="暂停 [Esc]", gk_x=440, gk_y=106, gk_width=190, gk_height=34,
                       gk_parent_widget="LabHeader")
    pause = node(tree, "GKNodeUiPauseGame", "打开暂停菜单", 1820, 1200,
                 gk_pause=True, gk_menu="PauseMenu", gk_hotkey=False)
    for source, target in ((ready_tail, panel), (panel, title), (title, hint),
                           (hint, resume_button), (resume_button, restart_button),
                           (restart_button, arm), (arm, open_button)):
        link(tree, source, "完成", target)
    link(tree, resume_button, "点击", resume)
    link(tree, restart_button, "点击", unpause)
    link(tree, unpause, "完成", restart)
    link(tree, open_button, "点击", pause)


def main():
    import BlenderGameKit as addon
    from BlenderGameKit import asset_export, project, ui
    addon.register()
    OUT.mkdir(parents=True, exist_ok=True)
    scene, player, gates, terminals, action = make_scene()
    sound = make_sound()
    role = make_role(scene, player)
    design = make_design()
    behavior = make_gate_behavior(gates, terminals, player, action, sound)
    level = make_level(scene, gates, design, behavior)
    assets = ui._asset_tree_or_new()
    ui._fill_asset_cards(assets)
    ui._ensure_asset_card(assets, "sound", str(sound))
    project.create_project(str(PROJECT), title="能源门 / Blueprint Lab", blend_file_name=BLEND.name)
    result = asset_export.export_all_scenes(str(PROJECT), dimension="3")
    if result.get("error") or any(not s.get("ok") for s in result.get("scenes", [])):
        raise RuntimeError(result)
    # Store portable paths in the source; the first export above uses known absolutes.
    # Empty uses the project's supported "follow .blend" path derivation.
    scene.gk_settings.project_dir = ""
    for tree in (behavior, assets):
        for item in tree.nodes:
            if getattr(item, "gk_path", "") == str(sound):
                item.gk_path = "//activation.wav"
    # A library-only .blend has no active screen/scene on normal File > Open.
    # Save an actual main file so the user lands in the playable source scene.
    for window in bpy.context.window_manager.windows:
        window.scene = scene
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.shading.type = "SOLID"
                area.spaces.active.region_3d.view_perspective = "CAMERA"
    preferences = bpy.context.preferences.filepaths
    preview_type = preferences.file_preview_type
    try:
        preferences.file_preview_type = "NONE"
        result_save = bpy.ops.wm.save_as_mainfile(filepath=str(BLEND), check_existing=False)
        if "FINISHED" not in result_save:
            raise RuntimeError(f"Could not save playable source: {result_save}")
    finally:
        preferences.file_preview_type = preview_type
    summary = {
        "source": BLEND.name,
        "trees": [{"name": t.name, "nodes": len(t.nodes), "links": len(t.links)}
                  for t in (role, behavior, level, design, assets)],
        "shared_instances": [{"object": gate.name, "tree": gate.gk_physics.logic_tree,
                              "bindings": {b.binding_name: b.target.name for b in gate.gk_physics.logic_bindings}}
                             for gate in gates],
        "warnings": result.get("warnings", []),
        "scenes": [{"name": s["scene"], "scene_name": s["scene_name"]} for s in result["scenes"]],
    }
    (OUT / "build_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("BLUEPRINT_LAB_BUILD=OK", json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
