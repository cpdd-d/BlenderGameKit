"""音频蓝图的资源所有权及播放生命周期回归，不需要 Blender。"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import gk_stub

gk_stub.install()
gk_stub.load_addon(os.path.join(ROOT, "BlenderGameKit"))
from BlenderGameKit import codegen
from BlenderGameKit.nodes import nodes as NN


def build_audio_graph():
    """真实函数蓝图；运行时探针调用这些生成函数，不复制发射器实现。"""
    tree = gk_stub.StubNodeTree("音频生命周期")
    tree.new_node(NN.GKNodeOnReady, "准备")
    specs = [
        ("play_loop", NN.GKNodePlaySound,
         dict(gk_path="res://clip.tres", gk_loop=True, gk_name="wind", gk_2d=False)),
        ("play_once", NN.GKNodePlaySound,
         dict(gk_path="res://clip.tres", gk_loop=False, gk_2d=False)),
        ("play_imported_once", NN.GKNodePlaySound,
         dict(gk_path="res://imported_loop.tres", gk_loop=False, gk_2d=False)),
        ("stop_loop", NN.GKNodeStopSound, dict(gk_name="wind")),
        ("play_music", NN.GKNodePlayMusic,
         dict(gk_path="res://clip.tres", gk_loop=True, gk_fade=0.05)),
        ("play_music_once", NN.GKNodePlayMusic,
         dict(gk_path="res://clip.tres", gk_loop=False, gk_fade=0.0)),
        ("stop_music", NN.GKNodeStopSound, dict(gk_fade=0.3)),
    ]
    for name, cls, props in specs:
        spec = tree.gk_funcs.add()
        spec.func_name, spec.return_type, spec.params = name, "void", ""
        define = tree.new_node(NN.GKNodeFunctionDefine, "定义_" + name, gk_func_name=name)
        node = tree.new_node(cls, name, **props)
        gk_stub.connect(tree, define, "执行", node, "执行")
    return tree


def generated_audio():
    return codegen.generate(build_audio_graph(), dict(dimension="3", base_class="Node3D"))


class AudioLifecycleTests(unittest.TestCase):
    def test_functions_compile_without_graph_warnings(self):
        code, warnings = generated_audio()
        self.assertEqual(warnings, [])
        for name in ("play_loop", "play_once", "play_music", "stop_music"):
            self.assertIn("func " + name + "(", code)

    def test_each_playback_owns_its_loop_configuration(self):
        code, _ = generated_audio()
        self.assertIn(".duplicate() as AudioStream", code)
        for typename in ("AudioStreamOggVorbis", "AudioStreamMP3"):
            self.assertIn(f"as {typename}).loop = false", code)
            self.assertIn(f"as {typename}).loop = true", code)
        self.assertIn("AudioStreamWAV.LOOP_DISABLED", code)
        self.assertIn("AudioStreamWAV.LOOP_FORWARD", code)

    def test_bgm_deduplicates_by_source_and_mode(self):
        code, _ = generated_audio()
        self.assertIn('get_meta("gk_audio_source", null)', code)
        self.assertIn('get_meta("gk_audio_loop", false)', code)
        self.assertIn("_gk_music_fade.kill()", code)
        self.assertIn("not _gk_music_stopping", code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
