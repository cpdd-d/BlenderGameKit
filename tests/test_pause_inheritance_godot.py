"""Generated dialogue/menu hosts must not pass ALWAYS to gameplay children."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import gk_stub
gk_stub.install()
gk_stub.load_addon(str(ROOT / "BlenderGameKit"))

try:
    import pytest
except ImportError:
    pass
else:
    pytestmark = pytest.mark.godot

GODOT = os.environ.get("GK_GODOT", r"D:\godot\Godot_v4.7.2-stable_win64_console.exe")


def generate_host(kind):
    from BlenderGameKit import codegen
    from BlenderGameKit.nodes import nodes as NN
    tree = gk_stub.StubNodeTree("暂停继承_" + kind)
    ready = tree.new_node(NN.GKNodeOnReady, "准备")
    panel = tree.new_node(NN.GKNodeUiPanel, "菜单")
    gk_stub.connect(tree, ready, "执行", panel, "执行")
    decl = tree.gk_funcs.add()
    decl.func_name, decl.return_type, decl.params = "open_overlay", "void", ""
    define = tree.new_node(NN.GKNodeFunctionDefine, "打开界面", gk_func_name="open_overlay")
    overlay = tree.new_node(NN.GKNodeUiPauseGame if kind == "pause" else NN.GKNodeUiDialog,
                            "界面", gk_text="暂停测试")
    gk_stub.connect(tree, define, "执行", overlay, "执行")
    return codegen.generate(tree, dict(dimension="3", base_class="Node3D"))


PROBE = '''extends SceneTree

var checks := 0
var failures := 0

func check(ok: bool, message: String) -> void:
    checks += 1
    if not ok:
        failures += 1
        printerr("PAUSE_INHERITANCE_FAIL: " + message)

func counter(mode: int = Node.PROCESS_MODE_INHERIT) -> Node:
    var node := Node.new()
    node.set_script(load("res://counter.gd"))
    node.process_mode = mode
    return node

func _initialize() -> void:
    call_deferred("run_probe")

func run_probe() -> void:
    for kind in ["pause", "dialog"]:
        var host := Node3D.new()
        host.set_script(load("res://" + kind + ".gd"))
        var gameplay := Node3D.new()
        gameplay.name = "Gameplay"
        host.add_child(gameplay)
        var existing := counter()
        gameplay.add_child(existing)
        var nested_always := counter(Node.PROCESS_MODE_ALWAYS)
        gameplay.add_child(nested_always)
        var player := AnimationPlayer.new()
        gameplay.add_child(player)
        var library := AnimationLibrary.new()
        var animation := Animation.new()
        animation.length = 10.0
        library.add_animation("Idle", animation)
        player.add_animation_library("", library)
        var explicit_always := counter(Node.PROCESS_MODE_ALWAYS)
        host.add_child(explicit_always)
        root.add_child(host)
        player.play("Idle")

        # Direct children added after _ready must receive the same policy.
        var dynamic := counter()
        host.add_child(dynamic)
        var dynamic_always := counter(Node.PROCESS_MODE_ALWAYS)
        host.add_child(dynamic_always)
        var ui: CanvasLayer = host._gk_ui_layer
        var ui_counter := counter()
        ui.add_child(ui_counter)
        check(host.process_mode == Node.PROCESS_MODE_ALWAYS, kind + ": host must remain ALWAYS")
        check(gameplay.process_mode == Node.PROCESS_MODE_PAUSABLE, kind + ": existing inherited subtree leaked ALWAYS")
        check(player.process_mode == Node.PROCESS_MODE_INHERIT, kind + ": native player inheritance unexpectedly rewritten")
        check(dynamic.process_mode == Node.PROCESS_MODE_PAUSABLE, kind + ": dynamic default child leaked ALWAYS")
        check(explicit_always.process_mode == Node.PROCESS_MODE_ALWAYS, kind + ": explicit existing mode overwritten")
        check(dynamic_always.process_mode == Node.PROCESS_MODE_ALWAYS, kind + ": explicit dynamic mode overwritten")
        check(ui.process_mode == Node.PROCESS_MODE_ALWAYS, kind + ": generated menu layer no longer ALWAYS")
        await create_timer(0.08).timeout
        check(existing.ticks > 0 and dynamic.ticks > 0 and player.current_animation_position > 0,
            kind + ": gameplay did not run before pause")
        var existing_before: int = existing.ticks
        var dynamic_before: int = dynamic.ticks
        var always_before: int = explicit_always.ticks
        var dynamic_always_before: int = dynamic_always.ticks
        var nested_before: int = nested_always.ticks
        var ui_before: int = ui_counter.ticks
        var position_before := player.current_animation_position
        paused = true
        await create_timer(0.1, true).timeout
        check(host.can_process(), kind + ": host cannot receive paused input")
        check(existing.ticks == existing_before and dynamic.ticks == dynamic_before,
            kind + ": gameplay counters advanced during pause")
        check(is_equal_approx(player.current_animation_position, position_before),
            kind + ": native AnimationPlayer advanced during pause")
        check(explicit_always.ticks > always_before and dynamic_always.ticks > dynamic_always_before,
            kind + ": explicit ALWAYS children stopped")
        check(nested_always.ticks > nested_before, kind + ": nested explicit ALWAYS override stopped")
        check(ui_counter.ticks > ui_before and ui.can_process() and host._gk_ui_root.can_process(),
            kind + ": generated UI descendants stopped")
        # This child is inserted while the tree is already paused.
        var during_pause := counter()
        host.add_child(during_pause)
        await create_timer(0.05, true).timeout
        check(during_pause.process_mode == Node.PROCESS_MODE_PAUSABLE and during_pause.ticks == 0,
            kind + ": child added during pause processed gameplay")
        paused = false
        await create_timer(0.08).timeout
        check(existing.ticks > existing_before and dynamic.ticks > dynamic_before and during_pause.ticks > 0,
            kind + ": inherited gameplay did not resume")
        check(player.current_animation_position > position_before, kind + ": native animation did not resume")
        host.queue_free()
        await process_frame
    if failures == 0:
        print("PAUSE_INHERITANCE=OK checks=", checks)
    quit(1 if failures else 0)
'''


@unittest.skipUnless(os.path.isfile(GODOT), "Godot unavailable: " + GODOT)
class PauseInheritanceTests(unittest.TestCase):
    def test_generated_dialogue_and_pause_hosts(self):
        output = ROOT / "_dist"
        output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="pause_inheritance_", dir=output) as directory:
            root = Path(directory)
            (root / "project.godot").write_text('config_version=5\n', encoding="utf-8")
            for kind in ("pause", "dialog"):
                code, warnings = generate_host(kind)
                self.assertEqual(warnings, [])
                (root / (kind + ".gd")).write_text(code, encoding="utf-8")
            (root / "probe.gd").write_text(PROBE, encoding="utf-8")
            (root / "counter.gd").write_text(
                'extends Node\nvar ticks := 0\nfunc _process(_delta: float) -> void:\n    ticks += 1\n',
                encoding="utf-8")
            env = dict(os.environ)
            for key in ("APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME"):
                folder = root / key.lower()
                folder.mkdir()
                env[key] = str(folder)
            proc = subprocess.run([GODOT, "--headless", "--path", str(root),
                                   "--script", "res://probe.gd"], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", env=env, timeout=30)
            report = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 0, report)
            self.assertNotIn("ERROR:", report)
            self.assertIn("PAUSE_INHERITANCE=OK", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
