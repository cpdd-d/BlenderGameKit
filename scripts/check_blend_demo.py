"""动画混合树示例的自动检查(第六十一轮)。

用法:
    python scripts\\check_blend_demo.py
    python scripts\\check_blend_demo.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

它会:
  1. 把 `examples/demo_blend/混合示例_godot` 拷到 `_dist/blend_probe`(**不碰示例本身**);
  2. 在副本里加一个探针场景,把主场景指过去;
  3. 用 Godot 无头跑,读探针打印的 BLEND_* 行,汇总 PASS / FAIL。

探针查的是"混合真的发生了"(不是代码长什么样):
  * 方块上真的建出了 **AnimationTree**(名字 GKBlendTree);
  * 把 `混合位置` 置 0(全是『走』)时,方块这一秒里最高只抬到 ~0.2 米;
  * 置 1(全是『跑』)时能抬到 ~2.0 米 —— **两个数差得开**,说明每一段
    动画都真的进了 glb、而且混合位置真的在起作用;
  * 置 0.5 时高度落在两者**之间**(这才是"混合"而不是"二选一");
  * 按 **1 / 2** 能把 `混合位置` 改成 0 / 1(按键那条链通)。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "examples", "demo_blend", "混合示例_godot")
OUT = os.path.join(ROOT, "_dist", "blend_probe")
GODOT_DEFAULT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"

PROBE_GD = '''extends Node

# 混合树示例的探针。结论行统一 BLEND_ 前缀。
# ⚠ 方块是 CharacterBody3D,_ready 里没挂东西;动画作用在它下面(glb 里)
#   的那个网格节点上 —— 所以读**网格的世界高度**最直接。

var world: Node = null
var host: Node = null
var mesh: Node3D = null
var elapsed := 0.0
var phase := 0
var y0 := 0.0
var y1 := 0.0
var y05 := 0.0
var track := 0.0


func _find_mesh(node: Node) -> Node3D:
	for child in node.get_children():
		if child is MeshInstance3D:
			return child
		var deeper := _find_mesh(child)
		if deeper != null:
			return deeper
	return null


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	world = load("res://scenes/World.tscn").instantiate()
	add_child(world)
	host = world.find_child("演示方块", true, false)
	if host != null:
		mesh = _find_mesh(host)


func _set_blend(value: float) -> void:
	if host != null:
		host.set("混合位置", value)


func _height() -> float:
	if mesh == null:
		return -999.0
	return mesh.global_position.y


func _physics_process(delta: float) -> void:
	elapsed += delta
	match phase:
		0:
			if elapsed >= 0.2:
				phase = 1
				print("BLEND_START host=", host != null, " mesh=", mesh != null,
					" tree=", host != null and host.get_node_or_null("GKBlendTree") != null)
				_set_blend(0.0)
				track = 0.0
		1:
			# 走:这一秒里最高的高度(动画是上下晃,取峰值最稳)
			track = maxf(track, _height())
			if elapsed >= 1.4:
				phase = 2
				y0 = track
				_set_blend(1.0)
				track = 0.0
		2:
			track = maxf(track, _height())
			if elapsed >= 2.6:
				phase = 3
				y1 = track
				_set_blend(0.5)
				track = 0.0
		3:
			track = maxf(track, _height())
			if elapsed >= 3.8:
				phase = 4
				y05 = track
				print("BLEND_HEIGHTS walk=", snappedf(y0, 0.001),
					" run=", snappedf(y1, 0.001),
					" half=", snappedf(y05, 0.001))
				# 按键那条链:按 1 -> 混合位置 = 0
				Input.action_press("key_1")
		4:
			if elapsed >= 4.1:
				phase = 5
				Input.action_release("key_1")
		5:
			if elapsed >= 4.4:
				phase = 6
				print("BLEND_KEY1 value=", host.get("混合位置"))
				Input.action_press("key_2")
		6:
			if elapsed >= 4.7:
				phase = 7
				Input.action_release("key_2")
		7:
			if elapsed >= 5.0:
				phase = 8
				print("BLEND_KEY2 value=", host.get("混合位置"))
				print("BLEND_DONE")
				set_physics_process(false)
				get_tree().quit()
'''

PROBE_TSCN = '''; 探针(临时文件,不写回示例)
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://probe.gd" id="1_probe"]

[node name="BlendProbe" type="Node"]
script = ExtResource("1_probe")
'''


def prepare():
    if not os.path.isdir(SRC):
        print(f"找不到示例工程 {SRC} —— 先跑 make_blend_demo.py")
        return False
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    shutil.copytree(SRC, OUT, ignore=shutil.ignore_patterns(".godot", "*.import"))
    with open(os.path.join(OUT, "probe.gd"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE_GD)
    with open(os.path.join(OUT, "probe.tscn"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE_TSCN)
    project_file = os.path.join(OUT, "project.godot")
    with open(project_file, "r", encoding="utf-8") as handle:
        text = handle.read()
    text = re.sub(r'run/main_scene="[^"]*"',
                  'run/main_scene="res://probe.tscn"', text)
    with open(project_file, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return True


def run_godot(godot):
    subprocess.run([godot, "--headless", "--path", OUT, "--import"],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
    run = subprocess.run([godot, "--headless", "--path", OUT,
                          "--quit-after", "1800"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    return (run.stdout or "") + (run.stderr or "")


def _grab(text, pattern):
    match = re.search(pattern, text)
    return match.group(0) if match else "<没跑到>"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--godot", default=GODOT_DEFAULT)
    args = parser.parse_args()
    if not prepare():
        return 1
    text = run_godot(args.godot)
    for line in text.splitlines():
        if "BLEND_" in line or "SCRIPT ERROR" in line or "Parse Error" in line:
            print(line.strip())

    problems = []
    for line in text.splitlines():
        if "SCRIPT ERROR" in line or "Parse Error" in line:
            problems.append(f"Godot 脚本 / 解析错误: {line.strip()}")
            break

    if not re.search(r"BLEND_START host=true mesh=true tree=true", text):
        problems.append("方块 / 网格 / AnimationTree 没找齐: "
                        + _grab(text, r"BLEND_START[^\n]*"))

    heights = re.search(r"BLEND_HEIGHTS walk=([\-\d.]+) run=([\-\d.]+) "
                        r"half=([\-\d.]+)", text)
    if not heights:
        problems.append("三个高度没量出来: " + _grab(text, r"BLEND_HEIGHTS[^\n]*"))
    else:
        walk = float(heights.group(1))
        run = float(heights.group(2))
        half = float(heights.group(3))
        # 『走』只晃 0.2,『跑』晃 2.0 —— 两个峰值必须差得开
        if run - walk < 0.5:
            problems.append(
                f"混合位置 0 / 1 的高度几乎一样({walk} vs {run})—— "
                f"混合没生效(或者两段动画没都进 glb)")
        # 0.5 应该落在两者之间(说明是"混",不是"二选一")
        lo, hi = min(walk, run), max(walk, run)
        if not (lo - 0.05 <= half <= hi + 0.05) or abs(half - lo) < 0.05:
            problems.append(f"混合位置 0.5 的高度 {half} 没落在 {walk}~{run} 之间")

    if not re.search(r"BLEND_KEY1 value=0", text):
        problems.append("按 1 没把『混合位置』设成 0: "
                        + _grab(text, r"BLEND_KEY1[^\n]*"))
    if not re.search(r"BLEND_KEY2 value=1", text):
        problems.append("按 2 没把『混合位置』设成 1: "
                        + _grab(text, r"BLEND_KEY2[^\n]*"))
    if "BLEND_DONE" not in text:
        problems.append("探针没跑完(可能中途报错)")

    # ── 生成物层面的证据 ────────────────────────────────────────────
    script_path = os.path.join(OUT, "scripts", "game_logic.gd")
    if os.path.isfile(script_path):
        with open(script_path, "r", encoding="utf-8") as handle:
            code = handle.read()
        for needle, why in (
                ("func _gk_ensure_blend_tree(", "建混合树的辅助函数"),
                ("AnimationTree.new()", "真的建 AnimationTree"),
                ("AnimationNodeBlendSpace1D.new()", "1D 混合空间"),
                ("space.add_blend_point(", "加混合点"),
                ('["走", 0.0]', "混合点『走』在位置 0"),
                ('["跑", 1.0]', "混合点『跑』在位置 1"),
                ('set("parameters/blend_position"', "每帧推混合位置"),
                ("混合位置", "参数来自变量『混合位置』"),
        ):
            if needle not in code:
                problems.append(f"生成的脚本里没有{why}: 找不到 {needle!r}")
    else:
        problems.append(f"没找到生成的脚本 {script_path}")

    scene_path = os.path.join(OUT, "scenes", "World.tscn")
    if os.path.isfile(scene_path):
        with open(scene_path, "r", encoding="utf-8") as handle:
            scene = handle.read()
        if '[node name="演示方块" type="CharacterBody3D"' not in scene:
            problems.append("World.tscn 里没有方块(演示方块)")
        if 'name="Model"' not in scene:
            problems.append("World.tscn 里没有 Model 子节点(动画在里面)")
    else:
        problems.append(f"没找到主场景 {scene_path}")

    for item in dict.fromkeys(problems):
        print(f"✗ {item}")
    print("CHECK_BLEND_DEMO=" + ("OK" if not problems else "FAIL"))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())