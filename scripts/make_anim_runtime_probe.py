"""把示例生成的脚本包成一个最小 Godot 工程,交给 Godot 真跑几秒。

用法:
    python scripts\\make_anim_runtime_probe.py
然后:
    godot --headless --path _dist\\anim_probe --quit-after 400

── 为什么要这一层(踩过的坑)────────────────────────────────────
"Godot 解析通过"只说明**语法**对。实测踩过两次:
  * `_cam_to2` 没声明 —— 局部变量被用到了另一帧的函数里,解析期就报
  * 相机过渡里 `get_viewport().get_camera_3d()` 这类 API 用错,
    **解析期完全看不出来**,要跑起来才炸
所以新功能(相机过渡 / 缓动 / 状态进出时)必须真跑一次。
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_GD = os.path.join(ROOT, "_dist", "anim_example.gd")
OUT = os.path.join(ROOT, "_dist", "anim_probe")

PROJECT = """config_version=5

[application]

config/name="anim_probe"
run/main_scene="res://Main.tscn"
config/features=PackedStringArray("4.7")
"""

# 一台主相机 + 一台特写机位 + 一个播放器(里面放几段空动画)
SCENE = """[gd_scene load_steps=9 format=3]

[ext_resource type="Script" path="res://scripts/game_logic.gd" id="1_script"]
[ext_resource type="Script" path="res://scripts/probe_check.gd" id="2_check"]

[sub_resource type="Animation" id="anim_idle"]
resource_name = "待机"
length = 1.0
tracks/0/type = "value"
tracks/0/imported = false
tracks/0/enabled = true
tracks/0/path = NodePath(".:scale")
tracks/0/interp = 1
tracks/0/loop_wrap = true
tracks/0/keys = {
"times": PackedFloat32Array(0, 1),
"transitions": PackedFloat32Array(1, 1),
"update": 0,
"values": [Vector3(1, 1, 1), Vector3(1.05, 1.05, 1.05)]
}

[sub_resource type="Animation" id="anim_attack"]
resource_name = "攻击"
length = 0.6
tracks/0/type = "value"
tracks/0/imported = false
tracks/0/enabled = true
tracks/0/path = NodePath(".:position")
tracks/0/interp = 1
tracks/0/loop_wrap = true
tracks/0/keys = {
"times": PackedFloat32Array(0, 0.6),
"transitions": PackedFloat32Array(1, 1),
"update": 0,
"values": [Vector3(0, 0, 0), Vector3(0.5, 0, 0)]
}

[sub_resource type="Animation" id="anim_hurt"]
resource_name = "受击"
length = 0.4

[sub_resource type="AnimationLibrary" id="anim_lib"]
_data = {
"待机": SubResource("anim_idle"),
"攻击": SubResource("anim_attack"),
"受击": SubResource("anim_hurt")
}

[node name="Probe" type="CharacterBody3D"]
script = ExtResource("1_script")

[node name="AnimationPlayer" type="AnimationPlayer" parent="."]
libraries = {
"": SubResource("anim_lib")
}

[node name="Camera" type="Camera3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1.6, 5)
fov = 70.0
current = true

[node name="特写机位" type="Camera3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 1.6, 1.4, 2.2)
fov = 80.0

[node name="ProbeCheck" type="Node" parent="."]
script = ExtResource("2_check")
"""

# ── 弹簧臂的独立测试场景 ────────────────────────────────────────────
# 一根 5 米长的杆子挂在原点、身后 2 米处竖一堵墙。
# 没有碰撞检测时相机会退到 5 米处(**穿进墙里**);
# 有碰撞检测时必须被墙挡下来 —— 这就是"不穿墙"的全部意义。
SPRING_SCENE = """[gd_scene load_steps=4 format=3]

[ext_resource type="Script" path="res://scripts/spring_logic.gd" id="1_spring"]
[ext_resource type="Script" path="res://scripts/probe_check.gd" id="2_check"]

[sub_resource type="BoxShape3D" id="wall_shape"]
size = Vector3(20, 20, 1)

[node name="SpringHost" type="CharacterBody3D"]
script = ExtResource("1_spring")

[node name="Camera" type="Camera3D" parent="."]
current = true

[node name="Wall" type="StaticBody3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 2)

[node name="WallShape" type="CollisionShape3D" parent="Wall"]
shape = SubResource("wall_shape")

[node name="ProbeCheck" type="Node" parent="."]
script = ExtResource("2_check")
"""

# 弹簧臂探针:把相机被顶到多远打出来
SPRING_CHECK = """extends Node

var elapsed := 0.0
var fired := false

func _process(delta: float) -> void:
	elapsed += delta
	if elapsed < 0.8 or fired:
		return
	fired = true
	var host := get_parent()
	var cam: Node3D = null
	for child in host.get_children():
		if child is Camera3D:
			cam = child
	var dist := -1.0
	if cam != null:
		dist = host.global_position.distance_to(cam.global_position)
	print("SPRING arm_wanted=5.0 camera_distance=", "%.2f" % dist)
	print("SPRING done")
"""

# 行为自查:三个时间点各看一次。
#   t=0.5s 动画应该正在播(待机,1.0 秒长)
#   t=0.6s **手动进"攻击"状态**(没键盘可按,直接调状态机入口),
#          攻击动画 0.6 秒 —— 演完会走"直连 -> 结束口"
#   t=1.5s 相机过渡(0.8 秒)应该完成(当前相机 = 特写机位),
#          并且攻击应该已经**收工**:anim_state 清空 + 已结束 = true
# 时间点不能随便挑:一次性动画播完之后 Godot 会把 current_animation
# 清成空串(见 README 第 22 条),1.5 秒时再查"在播什么"就什么都看不到。
CHECK = """extends Node

var elapsed := 0.0
var marks := [0.5, 0.6, 1.5]
var done := {}

func _process(delta: float) -> void:
	elapsed += delta
	for mark in marks:
		if elapsed >= mark and not done.has(mark):
			done[mark] = true
			if mark == 0.6:
				_start_attack()
			_report(mark)

func _start_attack() -> void:
	# 没有键盘可按,直接调状态机的入口函数(生成的代码里就这一个入口)
	var host := get_parent()
	if host.has_method("_gk_anim_enter"):
		host.call("_gk_anim_enter", "攻击")
	else:
		print("PROBE WARNING 找不到 _gk_anim_enter")

func _report(mark: float) -> void:
	var cam := get_viewport().get_camera_3d()
	var cam_name := "<null>"
	var fov := -1.0
	if cam != null:
		cam_name = String(cam.name)
		fov = cam.fov
	var anim := "<none>"
	var playing := false
	for child in get_parent().get_children():
		if child is AnimationPlayer:
			anim = String(child.current_animation)
			playing = child.is_playing()
	var host := get_parent()
	var state := "<none>"
	var finished := "<none>"
	if "anim_state" in host:
		state = String(host.get("anim_state"))
	if "_gk_anim_done" in host:
		finished = str(host.get("_gk_anim_done"))
	print("PROBE t=", mark, " camera=", cam_name, " fov=", fov,
		" animation=", anim, " playing=", playing,
		" anim_state=", state, " done=", finished)
	if mark >= 1.5:
		print("PROBE_DONE")
		set_process(false)
"""


def main():
    if not os.path.exists(SRC_GD):
        print(f"找不到 {SRC_GD} —— 先跑 check_anim_example.py 生成它")
        return 1
    os.makedirs(os.path.join(OUT, "scripts"), exist_ok=True)
    with open(os.path.join(OUT, "project.godot"), "w", encoding="utf-8") as fh:
        fh.write(PROJECT)
    with open(os.path.join(OUT, "Main.tscn"), "w", encoding="utf-8") as fh:
        fh.write(SCENE)
    with open(SRC_GD, encoding="utf-8") as fh:
        code = fh.read()
    # 生成的脚本是 extends CharacterBody3D,和场景根节点一致
    with open(os.path.join(OUT, "scripts", "game_logic.gd"), "w",
              encoding="utf-8") as fh:
        fh.write(code)
    with open(os.path.join(OUT, "scripts", "probe_check.gd"), "w",
              encoding="utf-8") as fh:
        fh.write(CHECK)

    # ── 把脚本用到的输入动作写进 project.godot ──────────────────────
    # **复用插件自己的写入器**(project.set_input_actions),不另写一份 ——
    # 顺便也验证了它写出来的格式 Godot 真的认。
    actions = re.findall(r'^const ACTION_\w+ := "([^"]+)"', code, re.M)
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import gk_stub  # noqa: E402

    gk_stub.install()
    gk_stub.load_addon(os.path.join(ROOT, "BlenderGameKit"))
    from BlenderGameKit import project as gk_project  # noqa: E402

    written = gk_project.set_input_actions(OUT, actions)
    print(f"写入输入动作 {written} 个: {actions}")

    _write_spring_probe()
    print(f"探针工程已生成: {OUT}")
    print("跑法: godot --headless --path _dist\\anim_probe --quit-after 400")
    print("ANIM_PROBE_READY")
    return 0


def _write_spring_probe():
    """第二个探针工程:**弹簧臂不穿墙**(相机被墙顶住)。"""
    out = os.path.join(ROOT, "_dist", "spring_probe")
    os.makedirs(os.path.join(out, "scripts"), exist_ok=True)
    with open(os.path.join(out, "project.godot"), "w", encoding="utf-8") as fh:
        fh.write(PROJECT.replace('res://Main.tscn', 'res://Spring.tscn'))
    with open(os.path.join(out, "Spring.tscn"), "w", encoding="utf-8") as fh:
        fh.write(SPRING_SCENE)
    with open(os.path.join(out, "scripts", "probe_check.gd"), "w",
              encoding="utf-8") as fh:
        fh.write(SPRING_CHECK)
    # 弹簧臂脚本:直接用**真 Blender 生成的那份**(由 blender_anim_tree.py
    # 写出来的 _dist/anim_spring_arm.gd)—— 不是手写的,测的就是生成器
    src = os.path.join(ROOT, "_dist", "anim_spring_arm.gd")
    if not os.path.exists(src):
        print(f"!! 找不到 {src},弹簧臂探针跳过(先跑 blender_anim_tree.py)")
        return
    with open(src, encoding="utf-8") as fh:
        code = fh.read()
    with open(os.path.join(out, "scripts", "spring_logic.gd"), "w",
              encoding="utf-8") as fh:
        fh.write(code)
    print(f"弹簧臂探针已生成: {out}")


if __name__ == "__main__":
    sys.exit(main())
