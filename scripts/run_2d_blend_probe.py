"""把 2.5D 示例导出的 Godot 工程**真跑起来**(模拟按键),并断言行为。

用法:
    python scripts/run_2d_blend_probe.py            # 只准备工程
    godot --headless --fixed-fps 60 --path <工程> --quit-after 300

── 这一层解决什么(用户 2026-10-03 第七轮)────────────────────────
用户要的是"创建 godot,直接运行"。光"导出成功"不算数 ——
这个脚本把导出的工程补上一个**探针场景**(带玩家 + 地面 + 两台相机),
然后在真 Godot 里:
    * 模拟按键 D  -> 角色该往右走
    * 模拟按键空格 -> 该跳起来
    * 模拟按键 C  -> 当前相机该变成『俯瞰机位』(2.5D 视角)
    * 模拟滚轮     -> 视野(fov)该变大

探针脚本是**手写**的(它是测试,不是产品);被测的是节点图生成的那份
`game_logic.gd`。
"""

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORT = os.path.join(os.path.expanduser("~"), "BlenderGameKit_Export")
OUT = os.path.join(ROOT, "_dist", "demo_25d_probe")

PROJECT = """config_version=5

[application]

config/name="2.5D Probe"
run/main_scene="res://Probe.tscn"
config/features=PackedStringArray("4.7")
"""

# 玩家(挂生成的脚本)+ 地面 + 两台相机 —— 和示例 .blend 里的对象一一对应。
# 名字必须对上:`$"侧视机位"` / `$"俯瞰机位"` 是生成的代码里写死的节点路径。
SCENE = """[gd_scene load_steps=8 format=3]

[ext_resource type="Script" path="res://scripts/game_logic.gd" id="1_script"]
[ext_resource type="Script" path="res://scripts/probe.gd" id="2_probe"]

[sub_resource type="BoxShape3D" id="player_shape"]
size = Vector3(1, 1, 1)

[sub_resource type="BoxShape3D" id="ground_shape"]
size = Vector3(40, 0.2, 40)

[sub_resource type="Animation" id="anim_idle"]
resource_name = "待机"
length = 1.0

[sub_resource type="Animation" id="anim_run"]
resource_name = "跑"
length = 0.5

[sub_resource type="AnimationLibrary" id="anim_lib"]
_data = {
"待机": SubResource("anim_idle"),
"走": SubResource("anim_idle"),
"跑": SubResource("anim_run"),
"跳": SubResource("anim_idle")
}

[node name="Main" type="Node3D"]

[node name="Player" type="CharacterBody3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0)
script = ExtResource("1_script")

[node name="Shape" type="CollisionShape3D" parent="Player"]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0.5, 0)
shape = SubResource("player_shape")

[node name="AnimationPlayer" type="AnimationPlayer" parent="Player"]
libraries = {
"": SubResource("anim_lib")
}

[node name="侧视机位" type="Camera3D" parent="Player"]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 3.2, 9)
fov = 70.0
current = true

[node name="俯瞰机位" type="Camera3D" parent="Player"]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 12, 0.35)
fov = 70.0

[node name="Ground" type="StaticBody3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, -0.1, 0)

[node name="GroundShape" type="CollisionShape3D" parent="Ground"]
shape = SubResource("ground_shape")

[node name="Probe" type="Node" parent="."]
script = ExtResource("2_probe")
"""

PROBE = """extends Node
# 手写的验证探针:模拟按键 + 读回游戏状态。被测的是节点图生成的脚本。
#
# 用 _physics_process(delta) 而不是 _process:无头模式下 _process 的 delta
# 是**真实耗时**,帧跑得飞快,时间点根本到不了(踩过)。

var player: CharacterBody3D = null
var start_pos := Vector3.ZERO
var moved := 0.0
var jump_rise := 0.0
var cam_before := ""
var cam_after := ""
var fov_before := 0.0
var fov_after := 0.0
var state_before := ""

var t := 0.0
var marks := [0.2, 0.6, 0.8, 1.1, 1.15, 1.4, 1.7, 2.0]
var done := {}

func _ready() -> void:
	player = get_parent().get_node_or_null("Player")
	if player != null:
		start_pos = player.global_position

func _physics_process(delta: float) -> void:
	t += delta
	if player != null and t > 1.1 and t < 1.4:
		jump_rise = maxf(jump_rise, player.global_position.y - start_pos.y)
	for mark in marks:
		if t >= mark and not done.has(mark):
			done[mark] = true
			_step(mark)
	if t > 2.2:
		print("PROBE25 done")
		set_physics_process(false)

func _press_key(code: int, pressed: bool) -> void:
	# 发一个真实的键盘事件 —— 比 Input.action_press 更接近玩家真的按键
	var event := InputEventKey.new()
	event.physical_keycode = code
	event.pressed = pressed
	Input.parse_input_event(event)

func _camera_name() -> String:
	var cam := get_viewport().get_camera_3d()
	return String(cam.name) if cam != null else "<none>"

func _fov() -> float:
	var cam := get_viewport().get_camera_3d()
	return cam.fov if cam != null else -1.0

func _step(mark: float) -> void:
	match mark:
		0.2:
			Input.action_press("key_d")            # 节点图里捕获的 D
		0.6:
			Input.action_release("key_d")
			if player != null:
				moved = player.global_position.x - start_pos.x
		0.8:
			# 滚轮:直接给一个"往上滚一格"的事件(节点图靠 _input 累积)
			var wheel := InputEventMouseButton.new()
			wheel.button_index = MOUSE_BUTTON_WHEEL_UP
			wheel.pressed = true
			Input.parse_input_event(wheel)
			fov_before = _fov()
		1.1:
			print("DBG wheel=", player.get("_gk_wheel"),
				" vy=", "%.2f" % player.velocity.y,
				" on_floor=", player.is_on_floor())
			fov_after = _fov()
			cam_before = _camera_name()
			state_before = String(player.get("anim_state"))
			# 真实按键事件:走 Godot 正常输入管线,`just_pressed` 才准
			_press_key(KEY_SPACE, true)
		1.15:
			print("DBG jump vy=", "%.2f" % player.velocity.y,
				" y=", "%.2f" % player.global_position.y)
		1.4:
			_press_key(KEY_SPACE, false)
			_press_key(67, true)   # C 的物理键码                # 节点图里捕获的 C
		1.7:
			_press_key(67, false)
			cam_after = _camera_name()
		2.0:
			print("PROBE25 moved_x=", "%.2f" % moved)
			print("PROBE25 jump_rise=", "%.2f" % jump_rise)
			print("PROBE25 anim_state=", state_before)
			print("PROBE25 camera=", cam_before, " -> ", cam_after)
			print("PROBE25 fov=", "%.1f" % fov_before, " -> ",
				"%.1f" % fov_after)
			print("PROBE25_DONE")
"""


def main():
    script = os.path.join(EXPORT, "scripts", "game_logic.gd")
    if not os.path.exists(script):
        print(f"找不到导出的脚本: {script}")
        print("先跑: blender -b --python scripts\\check_2d_blend_example.py")
        return 1
    os.makedirs(os.path.join(OUT, "scripts"), exist_ok=True)
    with open(os.path.join(OUT, "project.godot"), "w", encoding="utf-8") as fh:
        fh.write(PROJECT)
    with open(os.path.join(OUT, "Probe.tscn"), "w", encoding="utf-8") as fh:
        fh.write(SCENE)
    with open(os.path.join(OUT, "scripts", "probe.gd"), "w",
              encoding="utf-8") as fh:
        fh.write(PROBE)
    shutil.copyfile(script, os.path.join(OUT, "scripts", "game_logic.gd"))

    # 输入映射:把节点图用到的动作(**带捕获的键**)写进 project.godot ——
    # 不写的话真跑时报 The InputMap action "key_d" doesn't exist。
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import gk_stub  # noqa: E402

    gk_stub.install()
    gk_stub.load_addon(os.path.join(ROOT, "BlenderGameKit"))
    from BlenderGameKit import project as gk_project  # noqa: E402

    code = open(script, encoding="utf-8").read()
    import re  # noqa: E402

    consts = dict(re.findall(r'^const (ACTION_\w+) := "([^"]+)"', code, re.M))
    keys = {"key_w": "W", "key_a": "A", "key_s": "S", "key_d": "D",
            "jump": "SPACE", "view_top": "C", "view_side": "V"}
    actions = [(name, keys.get(name, "")) for name in consts.values()]
    written = gk_project.set_input_actions(OUT, actions)
    print(f"写入输入动作 {written} 个: {[a for a, _k in actions]}")
    print(f"探针工程: {OUT}")
    print("跑法: godot --headless --fixed-fps 60 --path "
          f"{OUT} --quit-after 240")
    print("PROBE25_READY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
