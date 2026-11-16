"""Rebuild and exercise examples/demo_blueprint_lab in real Blender and Godot.

The probe uses a dedicated _dist copy, never rewrites the exported example.
--skip-build checks an existing build; --render additionally saves GPU screenshots.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "demo_blueprint_lab" / "BlueprintLab_godot"
OUT = ROOT / "_dist" / "blueprint_lab_probe"

PROBE = r'''extends Node
var failures: Array[String] = []
var world: Node
var host: Node
var player: CharacterBody3D
var gates: Array[Node] = []
var audio_seen := [false, false]
var anim_seen := [false, false]

func check(label: String, value: bool) -> void:
    print("LAB_CHECK ", label, "=", value)
    if not value:
        failures.append(label)

func frames(count: int) -> void:
    for i in range(count):
        await get_tree().physics_frame
        await get_tree().process_frame

func tap(action: String) -> void:
    Input.action_press(action)
    await frames(2)
    Input.action_release(action)
    await frames(2)

func key(code: Key) -> void:
    for pressed in [true, false]:
        var event := InputEventKey.new()
        event.keycode = code
        event.physical_keycode = code
        event.pressed = pressed
        Input.parse_input_event(event)
        await frames(2)

func status() -> String:
    var label := world.find_child("Status", true, false) as Label
    return label.text if label else ""

func screenshot(name: String) -> void:
    if "--capture" not in OS.get_cmdline_user_args():
        return
    await RenderingServer.frame_post_draw
    var img := get_viewport().get_texture().get_image()
    img.save_png("res://" + name + ".png")

func _process(_delta: float) -> void:
    for i in range(gates.size()):
        if not is_instance_valid(gates[i]):
            continue
        var sounds := gates[i].find_children("*", "AudioStreamPlayer", true, false)
        sounds.append_array(gates[i].find_children("*", "AudioStreamPlayer2D", true, false))
        sounds.append_array(gates[i].find_children("*", "AudioStreamPlayer3D", true, false))
        for audio in sounds:
            audio_seen[i] = audio_seen[i] or (audio.playing and audio.stream != null)
        for anim in gates[i].find_children("*", "AnimationPlayer", true, false):
            anim_seen[i] = anim_seen[i] or anim.is_playing()

func collect_hosts() -> void:
    gates.clear()
    host = world
    player = world.find_children("Player", "CharacterBody3D", true, false)[0]
    for gate_name in ["EnergyGateA", "EnergyGateB"]:
        gates.append(world.find_children(gate_name, "StaticBody3D", true, false)[0])

func motion_test(x: float) -> Transform3D:
    var transform := player.global_transform
    transform.origin = Vector3(x, 0.72, -1.8)
    return transform

func approach(index: int) -> void:
    player.global_position = Vector3(-3.2 if index == 0 else 3.2, 0.72, 1.3)
    player.velocity = Vector3.ZERO
    await frames(4)

func _ready() -> void:
    process_mode = Node.PROCESS_MODE_ALWAYS
    world = load("res://scenes/World.tscn").instantiate()
    get_tree().root.add_child.call_deferred(world)
    await frames(2)
    get_tree().current_scene = world
    collect_hosts()
    check("separate_role_and_level", host.get("opened_count") != null and player != null and gates.size() == 2)
    await frames(12)
    check("initial_hud", status().contains("0 / 2"))
    check("instance_state_starts_independent", gates[0].get("activation_count") == 0 and gates[1].get("activation_count") == 0)
    var anim_a := gates[0].find_children("*", "AnimationPlayer", true, false)
    var anim_b := gates[1].find_children("*", "AnimationPlayer", true, false)
    check("independent_native_animation_players", anim_a.size() == 1 and anim_b.size() == 1 and anim_a[0] != anim_b[0])
    await screenshot("blueprint_lab_start")
    var start := player.global_position
    Input.action_press("move_right")
    await frames(20)
    Input.action_release("move_right")
    check("role_input_moves_player", player.global_position.distance_to(start) > 0.3)
    await tap("interact")
    check("remote_activation_rejected", gates[0].get("activation_count") == 0 and gates[1].get("activation_count") == 0)
    var closed_a := motion_test(-3.2)
    var closed_b := motion_test(3.2)
    check("both_closed_gates_block", player.test_move(closed_a, Vector3(0, 0, -2.4)) and player.test_move(closed_b, Vector3(0, 0, -2.4)))
    var button_a := world.find_child("InteractButton1", true, false) as Button
    var button_b := world.find_child("InteractButton2", true, false) as Button
    player.global_position = Vector3(-3.2, 0.72, 1.7)
    player.velocity = Vector3.ZERO
    await frames(3)
    button_a.pressed.emit()
    await frames(3)
    check("cross_call_short_radius_rejects", gates[0].get("activation_count") == 0 and gates[1].get("activation_count") == 0)
    var feedback := host.get("_gk_dialog_text") as Label
    check("cross_call_false_return_updates_feedback", feedback != null and feedback.text.contains("未接受"))
    await approach(0)
    var short_result: Variant = gates[0].call("try_activate", 0.1)
    check("typed_function_returns_false_for_short_radius", typeof(short_result) == TYPE_BOOL and not short_result and gates[0].get("activation_count") == 0)
    await tap("interact")
    check("first_instance_activates_once", gates[0].get("activation_count") == 1)
    check("second_instance_not_activated", gates[1].get("activation_count") == 0)
    check("level_waits_for_animation_completion", host.get("opened_count") == 0)
    await key(KEY_ESCAPE)
    var anim_position: float = anim_a[0].current_animation_position
    var shape := gates[0].get_node("Collision") as CollisionShape3D
    var paused_shape := shape.global_transform
    await get_tree().create_timer(0.4, true).timeout
    check("pause_holds_native_animation_and_collider", get_tree().paused and is_equal_approx(anim_a[0].current_animation_position, anim_position) and shape.global_transform.is_equal_approx(paused_shape))
    check("pause_does_not_complete_gate", gates[0].get("completed") == 0 and host.get("opened_count") == 0)
    await key(KEY_ESCAPE)
    check("escape_resumes_native_animation", not get_tree().paused and anim_a[0].is_playing())
    for i in range(5):
        await tap("interact")
    await get_tree().create_timer(1.4).timeout
    check("first_native_animation_and_audio", anim_seen[0] and audio_seen[0])
    check("first_completion_event", gates[0].get("completed") == 1 and host.get("opened_count") == 1)
    check("first_completion_hud", status().contains("1 / 2"))
    check("second_animation_did_not_run", not anim_seen[1] and not audio_seen[1] and gates[1].get("completed") == 0)
    check("first_gate_passable_second_blocked", not player.test_move(closed_a, Vector3(0, 0, -2.4)) and player.test_move(closed_b, Vector3(0, 0, -2.4)))
    check("instance_buttons_exist", button_a != null and button_b != null)
    button_a.pressed.emit()
    button_b.pressed.emit()
    await frames(4)
    check("ui_obeys_same_distance_and_idempotency", gates[0].get("activation_count") == 1 and gates[1].get("activation_count") == 0 and host.get("opened_count") == 1)
    player.set_physics_process(false)
    player.global_transform = closed_a
    player.move_and_collide(Vector3(0, 0, -4.4))
    await frames(4)
    check("first_gate_physical_traversal", player.global_position.z < -5.8)
    player.move_and_collide(Vector3(1.9, 0, 0))
    await frames(8)
    check("one_gate_cannot_win", host.get("victory") == 0)
    await screenshot("blueprint_lab_one_gate")
    await approach(1)
    player.global_position.z = 1.7
    await frames(3)
    button_b.pressed.emit()
    await frames(4)
    check("second_ui_activates_own_instance", gates[1].get("activation_count") == 1 and gates[0].get("activation_count") == 1)
    check("cross_call_longer_radius_accepts_same_distance", gates[1].get("accepted") == true and gates[1].get("completed") == 0)
    feedback = host.get("_gk_dialog_text") as Label
    check("cross_call_true_return_updates_feedback", feedback != null and feedback.text.contains("已接受"))
    for i in range(3):
        await tap("interact")
    await get_tree().create_timer(1.4).timeout
    check("second_native_animation_and_audio", anim_seen[1] and audio_seen[1])
    check("both_completed_independently", gates[0].get("completed") == 1 and gates[1].get("completed") == 1 and host.get("opened_count") == 2)
    check("both_open_hud", status().contains("2 / 2"))
    await key(KEY_ESCAPE)
    await frames(4)
    check("pause_after_completion_preserves_progress", get_tree().paused and host.get("opened_count") == 2 and gates[0].get("completed") == 1 and gates[1].get("completed") == 1)
    await key(KEY_ESCAPE)
    check("both_gates_physically_passable", not player.test_move(closed_a, Vector3(0, 0, -2.4)) and not player.test_move(closed_b, Vector3(0, 0, -2.4)))
    player.global_transform = closed_b
    player.move_and_collide(Vector3(0, 0, -4.4))
    await frames(4)
    check("second_gate_physical_traversal", player.global_position.z < -5.8)
    player.move_and_collide(Vector3(-1.9, 0, 0))
    await frames(8)
    check("goal_trigger_victory", host.get("victory") == 1)
    check("victory_hud", status().contains("实验完成"))
    await screenshot("blueprint_lab_complete")
    var old_id := world.get_instance_id()
    await tap("restart")
    await frames(3)
    world = get_tree().current_scene
    check("restart_input_reloads_scene", is_instance_valid(world) and world.get_instance_id() != old_id)
    collect_hosts()
    await frames(3)
    check("reload_resets_both_instances", gates[0].get("activation_count") == 0 and gates[1].get("activation_count") == 0 and gates[0].get("completed") == 0 and gates[1].get("completed") == 0)
    check("reload_resets_level", host.get("opened_count") == 0 and host.get("victory") == 0 and status().contains("0 / 2"))
    check("reload_closes_both_colliders", player.test_move(motion_test(-3.2), Vector3(0, 0, -2.4)) and player.test_move(motion_test(3.2), Vector3(0, 0, -2.4)))
    await approach(0)
    var accepted_result: Variant = gates[0].call("try_activate", 2.2)
    check("typed_function_returns_true_once", typeof(accepted_result) == TYPE_BOOL and accepted_result and gates[0].get("activation_count") == 1)
    var duplicate_result: Variant = gates[0].call("try_activate", 2.2)
    check("typed_function_repeat_returns_false", typeof(duplicate_result) == TYPE_BOOL and not duplicate_result and gates[0].get("activation_count") == 1)
    check("parameterized_calls_keep_other_instance_untouched", gates[1].get("activation_count") == 0 and gates[1].get("completed") == 0 and gates[1].get("accepted") == false)
    check("mid_animation_restart_starts_pending_gate", gates[0].get("activation_count") == 1 and gates[0].get("completed") == 0)
    old_id = world.get_instance_id()
    await tap("restart")
    await frames(4)
    world = get_tree().current_scene
    collect_hosts()
    check("mid_animation_restart_replaces_world", world.get_instance_id() != old_id)
    await get_tree().create_timer(1.3).timeout
    check("old_animation_cannot_complete_restarted_level", gates[0].get("activation_count") == 0 and gates[0].get("completed") == 0 and host.get("opened_count") == 0 and status().contains("0 / 2"))
    print("BLUEPRINT_LAB_RUNTIME=", "OK" if failures.is_empty() else "FAILED")
    get_tree().quit(0 if failures.is_empty() else 1)
'''


UI_PROBE = r'''extends Node
var failures: Array[String] = []
var world: Node
var player: CharacterBody3D
var gates: Array[Node] = []

func check(label: String, value: bool) -> void:
    print("UI_CHECK ", label, "=", value)
    if not value:
        failures.append(label)

func frames(count: int) -> void:
    for i in range(count):
        await get_tree().physics_frame
        await get_tree().process_frame

func key(code: Key) -> void:
    for pressed in [true, false]:
        var event := InputEventKey.new()
        event.keycode = code
        event.physical_keycode = code
        event.pressed = pressed
        Input.parse_input_event(event)
        await frames(2)

func click(widget: Control) -> void:
    var pos := widget.get_global_rect().get_center()
    var motion := InputEventMouseMotion.new()
    motion.position = pos
    motion.global_position = pos
    if DisplayServer.get_name() == "headless":
        get_viewport().push_input(motion, true)
    else:
        Input.parse_input_event(motion)
    await frames(1)
    for pressed in [true, false]:
        var event := InputEventMouseButton.new()
        event.position = pos
        event.global_position = pos
        event.button_index = MOUSE_BUTTON_LEFT
        event.pressed = pressed
        if DisplayServer.get_name() == "headless":
            get_viewport().push_input(event, true)
        else:
            Input.parse_input_event(event)
        await frames(2)

func widget(name: String) -> Control:
    return world.find_child(name, true, false) as Control

func within(child: Control, parent_rect: Rect2) -> bool:
    return child != null and parent_rect.grow(0.5).encloses(child.get_global_rect())

func screenshot(name: String) -> void:
    if "--capture" not in OS.get_cmdline_user_args():
        return
    await RenderingServer.frame_post_draw
    var size := get_viewport().get_visible_rect().size
    get_viewport().get_texture().get_image().save_png("res://blueprint_lab_" + name + "_" + str(int(size.x)) + "x" + str(int(size.y)) + ".png")

func collect_hosts() -> void:
    player = world.find_children("Player", "CharacterBody3D", true, false)[0]
    gates.clear()
    for name in ["EnergyGateA", "EnergyGateB"]:
        gates.append(world.find_children(name, "StaticBody3D", true, false)[0])

func _ready() -> void:
    process_mode = Node.PROCESS_MODE_ALWAYS
    var expected_size := ""
    for arg in OS.get_cmdline_user_args():
        if arg.begins_with("--expected-size="):
            expected_size = arg.trim_prefix("--expected-size=")
    var is_headless := DisplayServer.get_name() == "headless"
    if is_headless:
        # Headless's dummy Window stays 64x64. Test a real logical viewport,
        # and dispatch mouse events in that viewport's local coordinates.
        var dimensions := expected_size.split("x")
        get_window().content_scale_size = Vector2i(int(dimensions[0]), int(dimensions[1]))
        get_window().content_scale_mode = Window.CONTENT_SCALE_MODE_CANVAS_ITEMS
        await frames(2)
    world = load("res://scenes/World.tscn").instantiate()
    get_tree().root.add_child.call_deferred(world)
    await frames(12)
    get_tree().current_scene = world
    collect_hosts()
    var viewport := get_viewport().get_visible_rect()
    var actual_size := str(int(viewport.size.x)) + "x" + str(int(viewport.size.y))
    var window_size := str(get_window().size.x) + "x" + str(get_window().size.y)
    print("UI_SIZE viewport=", actual_size, " window=", window_size, " expected=", expected_size, " display=", DisplayServer.get_name())
    check("requested_ui_coordinate_space", actual_size == expected_size and (is_headless or window_size == expected_size))
    var header := widget("LabHeader")
    check("header_inside_viewport", within(header, viewport))
    check("header_centered", absf(header.get_global_rect().get_center().x - viewport.get_center().x) < 1.0)
    var contained := true
    for name in ["Title", "Status", "Controls", "InteractButton1", "InteractButton2", "PauseButton"]:
        contained = contained and within(widget(name), header.get_global_rect())
    check("hud_text_and_buttons_inside_header", contained)
    await screenshot("hud")
    player.global_position = Vector3(-3.2, 0.72, 1.3)
    player.velocity = Vector3.ZERO
    await frames(4)
    await key(KEY_ESCAPE)
    var panel := widget("PauseMenu")
    check("escape_opens_pause", get_tree().paused and panel.visible)
    check("pause_menu_inside_viewport", within(panel, viewport))
    contained = true
    for name in ["PauseTitle", "PauseHint", "ResumeButton", "RestartButton"]:
        contained = contained and within(widget(name), panel.get_global_rect())
    check("pause_controls_inside_panel", contained)
    check("resume_has_initial_keyboard_focus", get_viewport().gui_get_focus_owner() == widget("ResumeButton"))
    var start := player.global_position
    Input.action_press("move_right")
    await frames(12)
    Input.action_release("move_right")
    check("paused_world_movement_stops", player.global_position.distance_to(start) < 0.001)
    await click(widget("InteractButton1"))
    check("pause_blocks_underlying_hud_click", get_tree().paused and gates[0].get("activation_count") == 0)
    await key(KEY_E)
    check("paused_interaction_key_stops", gates[0].get("activation_count") == 0)
    var focus_trapped := true
    for i in range(4):
        await key(KEY_TAB)
        var focus := get_viewport().gui_get_focus_owner()
        focus_trapped = focus_trapped and focus != null and panel.is_ancestor_of(focus)
    check("keyboard_focus_stays_inside_pause_menu", focus_trapped)
    await screenshot("pause")
    widget("ResumeButton").grab_focus()
    await key(KEY_ENTER)
    check("enter_resumes_game", not get_tree().paused and not panel.visible)
    await frames(8)
    check("resume_does_not_replay_interact", gates[0].get("activation_count") == 0)
    await click(widget("PauseButton"))
    check("hud_pause_button_opens_menu", get_tree().paused and panel.visible)
    var old_id := world.get_instance_id()
    await click(widget("RestartButton"))
    await frames(8)
    world = get_tree().current_scene
    collect_hosts()
    check("pause_restart_reloads_and_unpauses", world.get_instance_id() != old_id and not get_tree().paused and not widget("PauseMenu").visible)
    check("pause_restart_resets_instances", gates[0].get("activation_count") == 0 and gates[1].get("activation_count") == 0 and world.get("opened_count") == 0)
    print("BLUEPRINT_LAB_UI=", "OK" if failures.is_empty() else "FAILED")
    get_tree().quit(0 if failures.is_empty() else 1)
'''


def run(command, name, env, timeout=180):
    try:
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as error:
        partial = []
        for stream in (error.stdout, error.stderr):
            partial.append(stream.decode("utf-8", errors="replace") if isinstance(stream, bytes) else (stream or ""))
        (OUT / (name + ".log")).write_text("".join(partial), encoding="utf-8")
        raise RuntimeError(f"{name} timed out; see {OUT / (name + '.log')}") from error
    output = result.stdout + result.stderr
    (OUT / (name + ".log")).write_text(output, encoding="utf-8")
    print(output[-10000:])
    if result.returncode:
        raise RuntimeError(f"{name} exited {result.returncode}; see {OUT / (name + '.log')}")
    # Godot sometimes returns 0 after script parser/runtime errors.
    if re.search(r"^(?:SCRIPT ERROR:|ERROR:|Traceback)|Parse Error:", output, re.MULTILINE):
        raise RuntimeError(f"{name}: Godot reported a script/resource error")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", default=r"D:\blender-4.5.5-windows-x64\blender.exe")
    parser.add_argument("--godot", default=r"D:\godot\Godot_v4.7.2-stable_win64_console.exe")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--ui-matrix", action="store_true",
                        help="Also check pause/input/layout at 960x540, 1280x800 and 1920x800")
    parser.add_argument("--ui-only", action="store_true", help="Only run the short UI matrix")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(json.dumps({"status": "RUNNING", "checks": {}}), encoding="utf-8")
    config = OUT / "config"
    config.mkdir(exist_ok=True)
    env = dict(os.environ, APPDATA=str(config), LOCALAPPDATA=str(config), PYTHONIOENCODING="utf-8")
    if not args.skip_build:
        output = run([args.blender, "--background", "--factory-startup", "--python-exit-code", "1", "--python",
                      str(ROOT / "scripts" / "make_blueprint_lab.py")], "blender", env)
        if "BLUEPRINT_LAB_BUILD=OK" not in output:
            raise RuntimeError("Blender did not finish the example build")
        audit = run([args.blender, "--background", "--factory-startup", "--python-exit-code", "1", "--python",
                     str(ROOT / "tests" / "blender_graph_audit.py")], "graph_audit", env)
        if "GRAPH_AUDIT=OK" not in audit:
            raise RuntimeError("Blender graph audit did not finish successfully")
        instances = run([args.blender, "--background", "--factory-startup", "--python-exit-code", "1", "--python",
                         str(ROOT / "tests" / "blender_blueprint_instances.py")], "instances", env)
        if "BLUEPRINT_INSTANCES=OK" not in instances:
            raise RuntimeError("Saved example did not retain shared graph and instance bindings")
    if not (SOURCE / "project.godot").is_file():
        raise RuntimeError("Missing exported project; run without --skip-build")
    project = OUT / "project"
    # Validate this disposable copy before deletion; never delete through a junction.
    resolved_out = OUT.resolve()
    if not resolved_out.is_relative_to(ROOT.resolve()) or project.resolve() != resolved_out / "project":
        raise RuntimeError(f"Refusing to replace a probe outside the workspace: {project}")
    if project.exists():
        shutil.rmtree(project)
    shutil.copytree(SOURCE, project,
                    ignore=shutil.ignore_patterns(".godot", "*.blend1"))
    (project / "lab_probe.gd").write_text(PROBE, encoding="utf-8")
    (project / "lab_probe.tscn").write_text(
        '[gd_scene load_steps=2 format=3]\n[ext_resource type="Script" path="res://lab_probe.gd" id="1"]\n'
        '[node name="LabProbe" type="Node"]\nscript = ExtResource("1")\n', encoding="utf-8")
    (project / "ui_probe.gd").write_text(UI_PROBE, encoding="utf-8")
    (project / "ui_probe.tscn").write_text(
        '[gd_scene load_steps=2 format=3]\n[ext_resource type="Script" path="res://ui_probe.gd" id="1"]\n'
        '[node name="UiProbe" type="Node"]\nscript = ExtResource("1")\n', encoding="utf-8")
    run([args.godot, "--headless", "--path", str(project), "--editor", "--import"], "import", env)
    command = [args.godot, "--path", str(project), "--audio-driver", "Dummy"]
    if args.render:
        command += ["--rendering-method", "gl_compatibility", "--resolution", "1280x800"]
    else:
        command += ["--headless"]
    command += ["res://lab_probe.tscn"]
    if args.render:
        command += ["--", "--capture"]
    checks = {}
    if not args.ui_only:
        output = run(command, "runtime", env, timeout=60)
        if "BLUEPRINT_LAB_RUNTIME=OK" not in output:
            raise RuntimeError("Runtime probe did not finish successfully")
        checks = dict(re.findall(r"LAB_CHECK ([a-z_]+)=(true|false)", output))
    ui_sizes = {}
    if args.ui_matrix or args.ui_only:
        for resolution in ("960x540", "1280x800", "1920x800"):
            ui_command = [args.godot, "--path", str(project), "--audio-driver", "Dummy", "--resolution", resolution]
            ui_command += ["--rendering-method", "gl_compatibility"] if args.render else ["--headless"]
            ui_command += ["res://ui_probe.tscn"]
            ui_command += ["--", "--expected-size=" + resolution]
            if args.render:
                ui_command += ["--capture"]
            output = run(ui_command, "ui_" + resolution, env, timeout=60)
            if "BLUEPRINT_LAB_UI=OK" not in output:
                raise RuntimeError("UI probe did not finish successfully at " + resolution)
            checks.update({resolution + ":" + key: value for key, value in re.findall(r"UI_CHECK ([a-z_]+)=(true|false)", output)})
            sizes = re.search(r"UI_SIZE viewport=(\d+x\d+) window=(\d+x\d+) expected=(\d+x\d+) display=(\w+)", output)
            if not sizes:
                raise RuntimeError("UI probe did not report actual dimensions at " + resolution)
            ui_sizes[resolution] = {"viewport": sizes[1], "window": sizes[2], "expected": sizes[3], "display": sizes[4],
                                    "window_kind": "dummy" if sizes[4] == "headless" else "native"}
    (OUT / "result.json").write_text(json.dumps({"status": "OK", "passed": len(checks), "checks": checks, "ui_sizes": ui_sizes}, indent=2), encoding="utf-8")
    print("BLUEPRINT_LAB=OK")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.exit(main())
    except Exception as error:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "result.json").write_text(json.dumps({"status": "FAILED", "error": str(error)}), encoding="utf-8")
        print(f"BLUEPRINT_LAB=FAILED: {error}", file=sys.stderr)
        sys.exit(1)
