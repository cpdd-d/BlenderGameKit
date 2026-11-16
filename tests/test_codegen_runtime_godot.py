"""B2 / B4-1 的 Godot 无头行为测试。

跑法:
    python tests/test_codegen_runtime_godot.py
    set GK_GODOT=D:\\godot\\Godot_v4.7.2-stable_win64_console.exe

这两条不是"断言生成代码里出现了某段文字",而是把生成物真的挂进
Godot 跑一遍,复现原始故障:

  * B2:模拟"按住 W + 窗口失焦" —— 没有守卫时 `Input` 的动作状态
    永远停在按下(角色自己走);有守卫时失焦那一刻被清成 0。
  * B4-1:『每当_物理帧』里生成带碰撞的场景 —— 同步 add_child 会让
    节点的 `_ready` 在物理步进里跑(`Engine.is_in_physics_frame()`
    为 true,正是 200~400ms 长帧的成因);延迟添加后会在空闲帧跑。

找不到 Godot 可执行文件时整条测试跳过(和 test_content_packs_godot 一致)。
"""

import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import gk_stub  # noqa: E402

gk_stub.install()
gk_stub.load_addon(os.path.join(ROOT, "BlenderGameKit"))

from BlenderGameKit import codegen  # noqa: E402
from BlenderGameKit.nodes import nodes as NN  # noqa: E402

try:
    import pytest
except ImportError:  # pragma: no cover - 取决于开发环境
    pytest = None
if pytest is not None:
    pytestmark = pytest.mark.godot

DEFAULT_GODOT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"
GODOT = os.environ.get("GK_GODOT", DEFAULT_GODOT)

V3 = dict(dimension="3", base_class="CharacterBody3D")

PROJECT = """config_version=5

[application]

config/name="gk_codegen_runtime_test"
run/main_scene="res://main.tscn"

[rendering]

renderer/rendering_method="gl_compatibility"
"""


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def make_tree():
    return gk_stub.StubNodeTree("运行时测试")


def build_polling_tree():
    """『每当_物理帧』+『输入_方向』+『移动_按方向』:一条真实的轮询链。"""
    tree = make_tree()
    physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
    direction = tree.new_node(NN.GKNodeInputDirection, "方向")
    move = tree.new_node(NN.GKNodeMoveDirection, "移动", gk_speed=5.0)
    gk_stub.connect(tree, physics, "执行", move, "执行")
    gk_stub.connect(tree, direction, "方向", move, "方向")
    return tree


def build_spawn_tree(scene_path):
    """『每当_物理帧』+『实例_生成』:物理步进里往场景树加节点。"""
    tree = make_tree()
    physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
    spawn = tree.new_node(NN.GKNodeSpawn, "生成", gk_scene_path=scene_path)
    gk_stub.connect(tree, physics, "执行", spawn, "执行")
    return tree


def run_godot(project_dir):
    env = dict(os.environ)
    env["APPDATA"] = os.path.join(project_dir, "_appdata")
    env["LOCALAPPDATA"] = os.path.join(project_dir, "_localappdata")
    env["XDG_DATA_HOME"] = os.path.join(project_dir, "_xdg")
    for name in ("_appdata", "_localappdata", "_xdg"):
        os.makedirs(os.path.join(project_dir, name), exist_ok=True)
    proc = subprocess.run(
        [GODOT, "--headless", "--path", project_dir],
        cwd=project_dir, env=env, capture_output=True, text=True, timeout=180)
    return proc, (proc.stdout or "") + "\n" + (proc.stderr or "")


INPUT_GUARD_PROBE = r'''extends Node

var failures := 0


func _check(condition: bool, message: String) -> void:
    if not condition:
        failures += 1
        printerr("INPUT_GUARD_FAIL: " + message)


func _ready() -> void:
    # 运行时加动作,不依赖 project.godot 的输入映射。
    for action in ["move_up", "move_down", "move_left", "move_right"]:
        if not InputMap.has_action(action):
            InputMap.add_action(action)

    await get_tree().physics_frame
    Input.action_press("move_up", 1.0)
    await get_tree().physics_frame

    # 前置条件:模拟"按住"确实生效 —— 这是故障发生时的状态。
    _check(Input.get_action_strength("move_up") > 0.5,
        "模拟按住 move_up 后强度应大于 0")
    _check(Input.is_action_pressed("move_up"),
        "模拟按住 move_up 后 is_action_pressed 应为真")

    var player := get_tree().current_scene.get_node("Player")
    player.notification(NOTIFICATION_APPLICATION_FOCUS_OUT)

    # 原问题:没有守卫时这里仍是 1.0,角色会一直往前走。
    _check(Input.get_action_strength("move_up") == 0.0,
        "失焦后 move_up 强度应归零,实际:" + str(Input.get_action_strength("move_up")))
    _check(not Input.is_action_pressed("move_up"),
        "失焦后 is_action_pressed 应为假")
    _check(Input.get_action_strength("move_left") == 0.0,
        "失焦后所有动作都应释放")

    # 鼠标键在 headless 下没有真实设备,`Input.parse_input_event` 也不会
    # 改变 `is_mouse_button_pressed`,所以这里只验证守卫里的鼠标补发
    # released 不报错(它和动作释放走同一个 _gk_clear_input)。
    player.notification(NOTIFICATION_APPLICATION_FOCUS_OUT)
    _check(Input.get_action_strength("move_right") == 0.0,
        "重复失焦也不应留下动作状态")

    if failures == 0:
        print("INPUT_GUARD_RUNTIME=OK")
    get_tree().quit(1 if failures > 0 else 0)
'''

INPUT_GUARD_MAIN = """[gd_scene load_steps=3 format=3]

[ext_resource type="Script" path="res://player.gd" id="1"]
[ext_resource type="Script" path="res://probe.gd" id="2"]

[node name="Main" type="Node"]

[node name="Player" type="CharacterBody3D" parent="."]
script = ExtResource("1")

[node name="Probe" type="Node" parent="."]
script = ExtResource("2")
"""

SPAWN_PROBE = r'''extends Node

var failures := 0


func _check(condition: bool, message: String) -> void:
    if not condition:
        failures += 1
        printerr("SPAWN_FAIL: " + message)


func _ready() -> void:
    await get_tree().physics_frame
    await get_tree().physics_frame
    await get_tree().process_frame

    var values: Array = get_tree().root.get_meta("dummy_ready_in_physics", [])
    _check(values.size() > 0, "生成的 Dummy 应至少进树一次")
    for value in values:
        # 同步 add_child 时为 true —— 正是物理服务器被踩、偶发长帧的成因。
        _check(value == false,
            "Dummy 的 _ready 不应在物理步进里执行,实际:" + str(value))

    var spawned := get_tree().current_scene.get_node_or_null("Dummy")
    _check(spawned != null, "延迟添加最终仍应把 Dummy 挂进场景")

    if failures == 0:
        print("SPAWN_RUNTIME=OK")
    get_tree().quit(1 if failures > 0 else 0)
'''

SPAWN_MAIN = """[gd_scene load_steps=3 format=3]

[ext_resource type="Script" path="res://spawner.gd" id="1"]
[ext_resource type="Script" path="res://probe.gd" id="2"]

[node name="Main" type="Node"]

[node name="Spawner" type="CharacterBody3D" parent="."]
script = ExtResource("1")

[node name="Probe" type="Node" parent="."]
script = ExtResource("2")
"""

DUMMY_SCENE = """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://dummy/Dummy.gd" id="1"]

[node name="Dummy" type="Node3D"]
script = ExtResource("1")
"""

DUMMY_SCRIPT = r'''extends Node3D


func _ready() -> void:
    var values: Array = get_tree().root.get_meta("dummy_ready_in_physics", [])
    values.append(Engine.is_in_physics_frame())
    get_tree().root.set_meta("dummy_ready_in_physics", values)
'''


class _Base(unittest.TestCase):
    def assert_clean(self, proc, output, marker):
        self.assertNotIn("SCRIPT ERROR", output)
        self.assertNotIn("Parse Error", output)
        self.assertNotIn("_FAIL: ", output)
        self.assertIn(marker, output)
        self.assertEqual(proc.returncode, 0, output)


@unittest.skipUnless(os.path.isfile(GODOT),
                     f"找不到 Godot 可执行文件: {GODOT}")
class CodegenRuntimeTests(_Base):
    def test_focus_loss_releases_held_input(self):
        """B2:失焦后轮询状态必须归零(否则角色自己走 / 一直在挖)。"""
        with tempfile.TemporaryDirectory() as tmp:
            code, warnings = codegen.generate(build_polling_tree(), V3)
            self.assertEqual(warnings, [], warnings)
            write(os.path.join(tmp, "project.godot"), PROJECT)
            write(os.path.join(tmp, "player.gd"), code)
            write(os.path.join(tmp, "probe.gd"), INPUT_GUARD_PROBE)
            write(os.path.join(tmp, "main.tscn"), INPUT_GUARD_MAIN)

            proc, output = run_godot(tmp)
            self.assert_clean(proc, output, "INPUT_GUARD_RUNTIME=OK")

    def test_spawn_in_physics_frame_is_deferred(self):
        """B4-1:物理帧生成的节点不能当帧进树(_ready 不在物理步进里跑)。"""
        with tempfile.TemporaryDirectory() as tmp:
            code, warnings = codegen.generate(
                build_spawn_tree("res://dummy/Dummy.tscn"), V3)
            self.assertEqual(warnings, [], warnings)
            self.assertIn("_gk_add_on_idle(", code)
            self.assertIn("func _gk_add_child_idle(", code)
            write(os.path.join(tmp, "project.godot"), PROJECT)
            write(os.path.join(tmp, "spawner.gd"), code)
            write(os.path.join(tmp, "probe.gd"), SPAWN_PROBE)
            write(os.path.join(tmp, "main.tscn"), SPAWN_MAIN)
            write(os.path.join(tmp, "dummy", "Dummy.tscn"), DUMMY_SCENE)
            write(os.path.join(tmp, "dummy", "Dummy.gd"), DUMMY_SCRIPT)

            proc, output = run_godot(tmp)
            self.assert_clean(proc, output, "SPAWN_RUNTIME=OK")


class CodegenShapeTests(unittest.TestCase):
    """不需要 Godot 的补充断言:没轮询的产物必须零差异 / 渲染帧保持同步。"""

    def test_no_polling_has_no_guard(self):
        tree = make_tree()
        ready = tree.new_node(NN.GKNodeOnReady, "启动")
        printer = tree.new_node(NN.GKNodePrint, "打印")
        text = tree.new_node(NN.GKNodeConstText, "文本", gk_value="你好")
        gk_stub.connect(tree, ready, "执行", printer, "执行")
        gk_stub.connect(tree, text, "值", printer, "内容")
        code, _ = codegen.generate(tree, V3)
        self.assertNotIn("_gk_clear_input", code)
        self.assertNotIn("func _notification", code)

    def test_polling_adds_guard(self):
        code, _ = codegen.generate(build_polling_tree(), V3)
        self.assertIn("func _gk_clear_input() -> void:", code)
        self.assertIn("func _notification(what: int) -> void:", code)
        self.assertIn("InputMap.get_actions()", code)

    def test_spawn_in_process_stays_synchronous(self):
        tree = make_tree()
        process = tree.new_node(NN.GKNodeOnProcess, "帧")
        spawn = tree.new_node(NN.GKNodeSpawn, "生成",
                              gk_scene_path="res://dummy/Dummy.tscn")
        gk_stub.connect(tree, process, "执行", spawn, "执行")
        code, _ = codegen.generate(tree, V3)
        self.assertIn("get_tree().current_scene.add_child(", code)
        self.assertNotIn("_gk_add_on_idle", code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
