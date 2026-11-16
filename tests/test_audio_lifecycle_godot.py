"""真实 Godot：同素材循环/一次性隔离、播放完成释放及音乐淡出重入。

运行：python tests/test_audio_lifecycle_godot.py
可用 GK_GODOT 指定引擎；测试工程及用户数据均在临时目录。
"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_audio_lifecycle import generated_audio

try:
    import pytest
except ImportError:
    pass
else:
    pytestmark = pytest.mark.godot

GODOT = os.environ.get("GK_GODOT", r"D:\godot\Godot_v4.7.2-stable_win64_console.exe")

PROBE = '''extends SceneTree

var failures := 0

func check(ok: bool, message: String) -> void:
    if not ok:
        failures += 1
        printerr("AUDIO_FAIL: " + message)

func _initialize() -> void:
    call_deferred("run_probe")

func run_probe() -> void:
    var source := load("res://clip.tres") as AudioStreamWAV
    var imported := load("res://imported_loop.tres") as AudioStreamWAV
    var host := Node3D.new()
    host.set_script(load("res://audio.gd"))
    root.add_child(host)
    host.play_loop()
    var loop_player: AudioStreamPlayer = host._gk_loops["wind"]
    check(source.loop_mode == AudioStreamWAV.LOOP_DISABLED, "loop changed cached source")
    check(loop_player.stream != source, "loop must own its stream settings")
    check(loop_player.stream.loop_mode == AudioStreamWAV.LOOP_FORWARD, "loop did not loop")
    host.play_once()
    host.play_imported_once()
    check(host.get_child_count() == 3, "expected loop plus two one-shots")
    check(imported.loop_mode == AudioStreamWAV.LOOP_FORWARD, "one-shot changed imported source")
    for child in host.get_children():
        if child != loop_player:
            check(child.stream.loop_mode == AudioStreamWAV.LOOP_DISABLED, "one-shot inherited loop")
    await create_timer(0.7).timeout
    check(host.get_child_count() == 1, "finished one-shot players leaked")
    check(loop_player.playing, "loop stopped when one-shots finished")
    host.play_loop()
    await process_frame
    check(host.get_child_count() == 1, "named loop replacement stacked players")
    host.stop_loop()
    await process_frame
    check(host.get_child_count() == 0 and host._gk_loops.is_empty(), "named stop leaked")

    host.play_music()
    var first: AudioStreamPlayer = host._gk_music
    host.play_music()
    check(host._gk_music == first, "same BGM restarted after stream duplication")
    check(source.loop_mode == AudioStreamWAV.LOOP_DISABLED, "BGM changed cached source")
    host.play_music_once()
    check(host._gk_music != first, "loop-mode change did not replace BGM")
    await create_timer(0.7).timeout
    check(not host._gk_music.playing, "non-looping BGM did not finish")

    host.play_music()
    await create_timer(0.08).timeout
    host.stop_music()
    var old_fade: Tween = host._gk_music_fade
    host.stop_music()
    check(host._gk_music_fade == old_fade, "repeated stop restarted fade")
    await create_timer(0.08).timeout
    host.play_music()
    await create_timer(0.5).timeout
    check(host._gk_music.playing, "stale fade stopped restarted BGM")
    check(is_equal_approx(host._gk_music.volume_db, -6.0), "new BGM volume lost fade race")
    check(host.get_child_count() == 1, "BGM switching stacked players")
    host.stop_music()
    await create_timer(0.4).timeout
    check(not host._gk_music.playing, "fade stop never completed")
    host.queue_free()
    await process_frame
    if failures == 0:
        print("AUDIO_LIFECYCLE_RUNTIME=OK")
    quit(1 if failures else 0)
'''


@unittest.skipUnless(os.path.isfile(GODOT), "Godot executable unavailable: " + GODOT)
class AudioLifecycleGodotTests(unittest.TestCase):
    def test_generated_graph_resource_isolation_and_lifecycle(self):
        code, warnings = generated_audio()
        self.assertEqual(warnings, [])
        with tempfile.TemporaryDirectory(prefix="gk_audio_") as directory:
            root = Path(directory)
            (root / "project.godot").write_text(
                'config_version=5\n[application]\nconfig/name="AudioLifecycle"\n'
                '[rendering]\nrenderer/rendering_method="gl_compatibility"\n', encoding="utf-8")
            (root / "audio.gd").write_text(code, encoding="utf-8")
            (root / "probe.gd").write_text(PROBE, encoding="utf-8")
            for filename, mode in (("clip.tres", 0), ("imported_loop.tres", 1)):
                (root / filename).write_text(
                    '[gd_resource type="AudioStreamWAV" format=3]\n[resource]\n'
                    f'format = 0\nmix_rate = 8000\nloop_mode = {mode}\nloop_end = 1600\n'
                    'data = PackedByteArray(' + ','.join(['128'] * 1600) + ')\n', encoding="utf-8")
            env = dict(os.environ)
            for key in ("APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME"):
                folder = root / key.lower()
                folder.mkdir()
                env[key] = str(folder)
            proc = subprocess.run(
                [GODOT, "--headless", "--audio-driver", "Dummy", "--path", str(root),
                 "--script", "res://probe.gd"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", env=env, timeout=30)
            output = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 0, output)
            self.assertNotIn("SCRIPT ERROR", output)
            self.assertNotIn("ERROR:", output)
            self.assertIn("AUDIO_LIFECYCLE_RUNTIME=OK", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
