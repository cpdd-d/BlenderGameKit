"""Run generated input-edge graphs in Godot with injected mouse/joypad events.

Example (Blender's bundled Python also works):
    python scripts/check_expression_logic.py --godot D:/godot/Godot_v4.7.2-stable_win64_console.exe

Uses an isolated Godot project and APPDATA/LOCALAPPDATA. Pass --output to retain
the generated scripts and logs; otherwise all files live in a temporary folder.
"""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import gk_stub  # noqa: E402

gk_stub.install()
gk_stub.load_addon(str(ROOT / "BlenderGameKit"))
from BlenderGameKit import codegen  # noqa: E402
from BlenderGameKit.nodes import nodes as NN  # noqa: E402


def make_graph(node_class):
    tree = gk_stub.StubNodeTree("input_edge_behavior")
    gate = tree.gk_vars.add()
    gate.var_name, gate.var_type, gate.default_value = "gate", "bool", "false"
    down = tree.new_node(node_class, "shared_down", gk_device=2)
    conditional = tree.new_node(node_class, "conditional_down", gk_device=2)
    read_gate = tree.new_node(NN.GKNodeGetVar, "read_gate", gk_var_name="gate")
    for prefix, event_class in (("process", NN.GKNodeOnProcess),
                                ("physics", NN.GKNodeOnPhysicsProcess)):
        event = tree.new_node(event_class, prefix)
        sequence = tree.new_node(NN.GKNodeSequence, prefix + "_sequence", gk_step_count=3)
        gk_stub.connect(tree, event, "执行", sequence, "执行")
        for index, suffix in enumerate(("a", "b", "conditional"), 1):
            name = prefix + "_" + suffix
            counter = tree.gk_vars.add()
            counter.var_name, counter.var_type, counter.default_value = name, "int", "0"
            branch = tree.new_node(NN.GKNodeBranch, name + "_edge")
            increment = tree.new_node(NN.GKNodeAddVar, name + "_increment",
                                      gk_var_name=name, gk_amount=1.0)
            gk_stub.connect(tree, branch, "是", increment, "执行")
            if suffix == "conditional":
                outer = tree.new_node(NN.GKNodeBranch, name + "_gate")
                gk_stub.connect(tree, sequence, str(index), outer, "执行")
                gk_stub.connect(tree, read_gate, "值", outer, "条件")
                gk_stub.connect(tree, outer, "是", branch, "执行")
                gk_stub.connect(tree, conditional, "刚按下", branch, "条件")
            else:
                gk_stub.connect(tree, sequence, str(index), branch, "执行")
                gk_stub.connect(tree, down, "刚按下", branch, "条件")
    result = codegen.generate_result(tree, {"base_class": "Node", "dimension": "3"})
    if result.warnings:
        raise AssertionError(result.warnings)
    return result.code


RUNNER = '''extends SceneTree

const MouseGraph = preload("res://mouse.gd")
const JoyGraph = preload("res://joy.gd")
var failures: int = 0

func _initialize() -> void:
    Input.use_accumulated_input = false
    _run.call_deferred()

func _send_mouse(pressed: bool) -> void:
    var event := InputEventMouseButton.new()
    event.button_index = MOUSE_BUTTON_LEFT
    event.pressed = pressed
    Input.parse_input_event(event)
    Input.flush_buffered_events()

func _send_joy(pressed: bool, device: int = 2) -> void:
    var event := InputEventJoypadButton.new()
    event.device = device
    event.button_index = JOY_BUTTON_A
    event.pressed = pressed
    event.pressure = 1.0 if pressed else 0.0
    Input.parse_input_event(event)
    Input.flush_buffered_events()

func _settle() -> void:
    for _frame in range(5):
        await physics_frame
        await process_frame

func _check(node: Node, shared: int, gated: int, stage: String) -> void:
    for clock in ["process", "physics"]:
        for branch in ["a", "b", "conditional"]:
            var expected: int = gated if branch == "conditional" else shared
            var actual: int = int(node.get(clock + "_" + branch))
            if actual != expected:
                failures += 1
                printerr("BEHAVIOR_FAIL %s/%s/%s: expected %d, got %d" % [
                    stage, clock, branch, expected, actual])

func _run() -> void:
    var mouse := MouseGraph.new()
    var joy := JoyGraph.new()
    root.add_child(mouse)
    root.add_child(joy)
    await _settle()
    _check(mouse, 0, 0, "mouse initial")
    _check(joy, 0, 0, "joy initial")

    _send_joy(true, 1)
    await _settle()
    _check(joy, 0, 0, "wrong device")
    _send_joy(false, 1)
    _send_mouse(true)
    _send_joy(true)
    await _settle()
    _check(mouse, 1, 0, "mouse first press")
    _check(joy, 1, 0, "joy first press")

    mouse.set("gate", true)
    joy.set("gate", true)
    await _settle()
    _check(mouse, 1, 0, "mouse held after gate opens")
    _check(joy, 1, 0, "joy held after gate opens")

    _send_mouse(false)
    _send_joy(false)
    await _settle()
    _check(mouse, 1, 0, "mouse release")
    _check(joy, 1, 0, "joy release")

    _send_mouse(true)
    _send_joy(true)
    await _settle()
    _check(mouse, 2, 1, "mouse second press")
    _check(joy, 2, 1, "joy second press")

    # Skip the conditional chain for a complete release/press cycle. Opening
    # the gate while held must not report an old press as a new edge.
    mouse.set("gate", false)
    joy.set("gate", false)
    _send_mouse(false)
    _send_joy(false)
    await _settle()
    _send_mouse(true)
    _send_joy(true)
    await _settle()
    mouse.set("gate", true)
    joy.set("gate", true)
    await _settle()
    _check(mouse, 3, 1, "mouse skipped conditional cycle")
    _check(joy, 3, 1, "joy skipped conditional cycle")
    _send_mouse(false)
    _send_joy(false)
    await _settle()
    mouse.queue_free()
    joy.queue_free()
    if failures == 0:
        print("EXPRESSION_INPUT_BEHAVIOR=OK")
    quit(1 if failures else 0)
'''


def run(godot, directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/name="GK expression regression"\n'
        '[rendering]\nrenderer/rendering_method="gl_compatibility"\n', encoding="utf-8")
    (directory / "mouse.gd").write_text(make_graph(NN.GKNodeMouseButtonDown), encoding="utf-8")
    (directory / "joy.gd").write_text(make_graph(NN.GKNodeJoyButtonDown), encoding="utf-8")
    (directory / "runner.gd").write_text(RUNNER, encoding="utf-8")
    env = os.environ.copy()
    for variable in ("APPDATA", "LOCALAPPDATA"):
        path = directory / variable.lower()
        path.mkdir(exist_ok=True)
        env[variable] = str(path)
    command = [str(godot), "--headless", "--path", str(directory),
               "--log-file", str(directory / "godot.log"), "--fixed-fps", "60",
               "--script", "res://runner.gd"]
    result = subprocess.run(command, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=env, timeout=45)
    output = result.stdout + result.stderr
    (directory / "check.log").write_text(output, encoding="utf-8")
    print(output.strip())
    assert result.returncode == 0, f"Godot exited with {result.returncode}"
    assert "EXPRESSION_INPUT_BEHAVIOR=OK" in output, "Behavior assertions did not complete"
    # Windows sandbox profiles can lack access to the host certificate store.
    # This offline probe never uses TLS; keep every other engine/script error
    # fatal, as tests/blender_role_logic.py does for the same environment.
    errors = [line for line in output.splitlines()
              if any(marker in line for marker in ("SCRIPT ERROR", "ERROR:", "BEHAVIOR_FAIL"))
              and line.strip() != "ERROR: Failed to read the root certificate store."]
    assert not errors, "\n".join(errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--godot", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    godot = args.godot.resolve()
    if not godot.is_file():
        parser.error(f"Godot executable does not exist: {godot}")
    if args.output:
        run(godot, args.output.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="gk_expression_logic_") as directory:
            run(godot, Path(directory))


if __name__ == "__main__":
    main()
