"""Animation event ownership under replay, interruption, pause and deletion."""
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


def generated_controller():
    from BlenderGameKit import codegen
    from BlenderGameKit.nodes import nodes as NN
    tree = gk_stub.StubNodeTree("动画播放生命周期")
    tree.new_node(NN.GKNodeOnReady, "准备")
    specs = [("auto", "", "REPLACE"), ("a", "A", "REPLACE"),
             ("b", "B", "REPLACE"), ("loop", "Loop", "REPLACE"),
             ("keep", "A", "CONTINUE"), ("silent", "A", "REPLACE")]
    for key, clip, mode in specs:
        name = "fire_" + key
        decl = tree.gk_funcs.add()
        decl.func_name, decl.return_type, decl.params = name, "void", ""
        define = tree.new_node(NN.GKNodeFunctionDefine, name, gk_func_name=name)
        play = tree.new_node(NN.GKNodePlayAnim, "Play_" + key,
                             gk_anim_name=clip, gk_blend_mode=mode)
        gk_stub.connect(tree, define, "执行", play, "执行")
        if key != "silent":
            for pin, suffix in (("开始时", "started"), ("每帧", "running"),
                                ("结束时", "ended"), ("完成", "dispatched")):
                counter = tree.new_node(NN.GKNodeAddVar, key + "_" + suffix,
                                        gk_var_name=key + "_" + suffix, gk_amount=1.0)
                gk_stub.connect(tree, play, pin, counter, "执行")
    for name, cls, props in (("stop_clip", NN.GKNodeAnimStop, {}),
                             ("pause_clip", NN.GKNodeAnimPause, {"gk_paused": True}),
                             ("resume_clip", NN.GKNodeAnimPause, {"gk_paused": False})):
        decl = tree.gk_funcs.add()
        decl.func_name, decl.return_type, decl.params = name, "void", ""
        define = tree.new_node(NN.GKNodeFunctionDefine, name, gk_func_name=name)
        operation = tree.new_node(cls, name + "_node", **props)
        gk_stub.connect(tree, define, "执行", operation, "执行")
    return codegen.generate(tree, dict(dimension="3", base_class="Node3D"))


PROBE = '''extends SceneTree

var checks := 0
var failures := 0

func check(ok: bool, message: String) -> void:
    checks += 1
    if not ok:
        failures += 1
        printerr("ANIM_PLAYBACK_FAIL: " + message)

func controller() -> Node3D:
    var host := Node3D.new()
    host.set_script(load("res://controller.gd"))
    var model := Node3D.new()
    model.name = "Model"
    host.add_child(model)
    var player := AnimationPlayer.new()
    player.name = "AnimationPlayer"
    # Explicit advance() makes the signal assertions deterministic.
    player.callback_mode_process = AnimationMixer.ANIMATION_CALLBACK_MODE_PROCESS_MANUAL
    model.add_child(player)
    var library := AnimationLibrary.new()
    for clip in ["A", "B", "Loop"]:
        var animation := Animation.new()
        animation.length = 1.0
        if clip == "Loop":
            animation.loop_mode = Animation.LOOP_LINEAR
        library.add_animation(clip, animation)
    player.add_animation_library("", library)
    root.add_child(host)
    host.set_process(false)
    return host

func _initialize() -> void:
    call_deferred("run_probe")

func run_probe() -> void:
    var a := controller()
    var b := controller()
    var pa: AnimationPlayer = a.get_node("Model/AnimationPlayer")
    var pb: AnimationPlayer = b.get_node("Model/AnimationPlayer")
    a.fire_auto()
    check(pa.current_animation == "A", "blank name must resolve the actual first clip")
    check(a.auto_started == 1 and a.auto_dispatched == 1, "blank-name started/immediate output missing")
    a._process(0.1)
    check(a.auto_running == 1, "blank-name running output missing")
    pa.advance(1.1)
    check(a.auto_ended == 1, "blank-name finished output missing")
    check(not pa.has_meta("gk_playback_events"), "completed playback kept subscriptions")

    a.fire_a()
    pa.advance(0.3)
    a.fire_a()
    check(pa.current_animation_position == 0.0, "REPLACE did not restart the same clip")
    check(a.a_started == 2, "repeated playback did not start exactly twice")
    check(pa.animation_finished.get_connections().size() == 2, "replay accumulated finish callbacks")
    pa.advance(1.1)
    check(a.a_ended == 1, "replay must finish only the surviving playback")

    a.fire_a()
    a.fire_b()
    pa.advance(1.1)
    check(a.a_ended == 1 and a.b_ended == 1, "switch completed the interrupted clip")
    pa.play("A")
    pa.advance(1.1)
    check(a.a_ended == 1, "later same-name native playback triggered a stale callback")

    a.fire_a()
    pa.play("B")
    pa.advance(1.1)
    pa.play("A")
    pa.advance(1.1)
    check(a.a_ended == 1, "external different-clip switch did not cancel old callback")
    a.fire_a()
    a.stop_clip()
    pa.play("A")
    pa.advance(1.1)
    check(a.a_ended == 1, "Stop node left a stale completion callback")
    a.fire_a()
    a.fire_silent()
    pa.advance(1.1)
    check(a.a_ended == 1, "play node without events failed to cancel previous playback")

    a.fire_keep()
    pa.advance(0.3)
    a.fire_keep()
    check(is_equal_approx(pa.current_animation_position, 0.3), "CONTINUE restarted the clip")
    check(a.keep_started == 1, "CONTINUE duplicated started callback")
    pa.advance(0.8)
    check(a.keep_ended == 1, "CONTINUE lost completion")

    a.fire_a()
    pa.advance(0.25)
    a.pause_clip()
    check(not pa.is_playing(), "Pause did not pause")
    check(pa.has_meta("gk_playback_events"), "Pause cancelled the pending completion")
    a.resume_clip()
    check(pa.is_playing(), "Resume failed because current_animation was empty")
    check(is_equal_approx(pa.current_animation_position, 0.25), "Resume lost original position")
    pa.advance(0.8)
    check(a.a_ended == 2, "Pause/Resume lost completion")

    a.fire_loop()
    pa.advance(3.4)
    a._process(0.1)
    check(a.loop_ended == 0 and a.loop_running == 1, "Loop falsely completed")
    a.stop_clip()
    check(not pa.has_meta("gk_playback_events"), "Loop stop retained subscriptions")

    a.fire_a()
    b.fire_a()
    pb.advance(1.1)
    check(b.a_ended == 1 and a.a_ended == 2, "shared blueprint instances crossed completion")
    pa.advance(1.1)
    check(a.a_ended == 3, "first instance failed independently")
    a.fire_a()
    pa.queue_free()
    await process_frame
    a._process(0.1)
    check(a.a_ended == 3, "deleted player falsely completed")
    b.fire_loop()
    b.queue_free()
    a.queue_free()
    await process_frame
    if failures == 0:
        print("ANIMATION_PLAYBACK=OK checks=", checks)
    quit(1 if failures else 0)
'''


@unittest.skipUnless(os.path.isfile(GODOT), "Godot unavailable: " + GODOT)
class AnimationPlaybackTests(unittest.TestCase):
    def test_generated_playback_lifecycle(self):
        code, warnings = generated_controller()
        self.assertEqual(warnings, [])
        # Keep all generated files in the workspace; some Windows runners
        # provide a TEMP path whose child files cannot be reopened.
        output = ROOT / "_dist"
        output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="animation_playback_", dir=output) as directory:
            root = Path(directory)
            (root / "project.godot").write_text('config_version=5\n', encoding="utf-8")
            (root / "controller.gd").write_text(code, encoding="utf-8")
            (root / "probe.gd").write_text(PROBE, encoding="utf-8")
            env = dict(os.environ)
            for key in ("APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME"):
                folder = root / key.lower()
                folder.mkdir()
                env[key] = str(folder)
            proc = subprocess.run([GODOT, "--headless", "--path", str(root),
                                   "--script", "res://probe.gd"], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace",
                                  env=env, timeout=30)
            report = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 0, report)
            self.assertNotIn("ERROR:", report)
            self.assertIn("ANIMATION_PLAYBACK=OK", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
