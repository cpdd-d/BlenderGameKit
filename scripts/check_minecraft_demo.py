"""我的世界示例的真机检查(第一百九十三轮)。

不是"文件存在"检查 —— 真起 Godot(无头),跑几帧后验证:

  * 玩家站在方块地面上(`is_on_floor`);
  * 按 move_right 真的往右走;
  * 按 jump 真的跳起来;
  * 鼠标左键 + 准星射线真的能拆掉一块方块(Blocks 子节点数下降)。

跑法:
    python scripts\\check_minecraft_demo.py
"""

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEMO = os.path.join(ROOT, "examples", "demo_minecraft")
WORK = os.path.join(ROOT, "_dist", "minecraft_probe")
GODOT_DEFAULT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"

PROBE = '''extends SceneTree

func _initialize() -> void:
	call_deferred("run")


func run() -> void:
	var scene = load("res://Main.tscn").instantiate()
	root.add_child(scene)
	current_scene = scene
	for i in range(30):
		await physics_frame
	var player = scene.get_node("Player") as CharacterBody3D
	var blocks = scene.get_node("Blocks")
	var before = blocks.get_child_count()
	var on_floor = player.is_on_floor()

	var x0 = player.global_position.x
	Input.action_press("move_right")
	for i in range(20):
		await physics_frame
	Input.action_release("move_right")
	var moved = player.global_position.x > x0 + 0.3

	var y0 = player.global_position.y
	Input.action_press("jump")
	await physics_frame
	Input.action_release("jump")
	for i in range(3):
		await physics_frame
	var jumped = player.global_position.y > y0 + 0.05
	# 等落地(跳 8.5 米/秒、重力 9.8:大约 1.8 秒才回到地面)
	for i in range(140):
		await physics_frame

	# 低头看地面,然后按 E 拆准星指着的那一块
	# (左键在无头窗口里没法可靠地更新 Input 状态,所以用同一分支的 E 验证)
	var camera = player.get_node("第一人称相机") as Camera3D
	camera.rotation_degrees = Vector3(-40.0, 20.0, 0.0)
	for i in range(3):
		await physics_frame
	Input.action_press("mine")
	for i in range(4):
		await physics_frame
	Input.action_release("mine")
	for i in range(6):
		await physics_frame
	var after = blocks.get_child_count()
	var broke = after < before

	# 换个角度对着另一块,按 Q 放一块(右键在无头窗口里同样不好模拟)
	camera.rotation_degrees = Vector3(-40.0, -20.0, 0.0)
	for i in range(3):
		await physics_frame
	var total_before_place = scene.find_children(
		"*", "StaticBody3D", true, false).size()
	Input.action_press("place")
	for i in range(4):
		await physics_frame
	Input.action_release("place")
	for i in range(6):
		await physics_frame
	var total_after_place = scene.find_children(
		"*", "StaticBody3D", true, false).size()
	var placed_nodes = scene.find_children(
		"方块", "StaticBody3D", true, false).size()
	var placed = (total_after_place > total_before_place
		and placed_nodes > 0)

	var ok = on_floor and moved and jumped and broke and placed
	print("MINECRAFT on_floor=", on_floor, " moved=", moved,
		" jumped=", jumped, " blocks=", before, "->", after,
		" broke=", broke, " placed=", placed,
		" static=", total_before_place, "->", total_after_place,
		" placed_nodes=", placed_nodes)
	print("MINECRAFT_CHECK=", "OK" if ok else "FAIL")
	quit(0 if ok else 1)
'''


def find_godot(explicit=""):
    candidate = explicit or os.environ.get("GK_GODOT") or GODOT_DEFAULT
    return candidate if os.path.isfile(candidate) else ""


def rebuild():
    script = os.path.join(HERE, "make_minecraft_demo.py")
    result = subprocess.run([sys.executable, script],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    if result.returncode != 0:
        print((result.stdout or "") + (result.stderr or ""))
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="我的世界示例真机检查")
    parser.add_argument("--godot", default="")
    args = parser.parse_args()
    godot = find_godot(args.godot)
    if not godot:
        print(f"跳过:找不到 Godot({args.godot or GODOT_DEFAULT})")
        return 0
    if not rebuild():
        print("MINECRAFT=FAILED(重新生成示例失败)")
        return 1
    if os.path.isdir(WORK):
        shutil.rmtree(WORK)
    shutil.copytree(DEMO, WORK,
                    ignore=shutil.ignore_patterns(".godot", "*.import"))
    with open(os.path.join(WORK, "probe.gd"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE)

    env = dict(os.environ, GODOT_SILENCE_ROOT_WARNING="1")
    common = dict(capture_output=True, text=True, encoding="utf-8",
                  errors="replace", env=env, timeout=180)
    subprocess.run([godot, "--headless", "--path", WORK, "--import"],
                   **common)
    result = subprocess.run(
        [godot, "--headless", "--path", WORK, "--script", "res://probe.gd"],
        **common)
    text = (result.stdout or "") + (result.stderr or "")
    for line in text.splitlines():
        if "MINECRAFT" in line or "ERROR" in line:
            print(line.strip())
    if "MINECRAFT_CHECK=OK" in text:
        print("MINECRAFT=OK")
        return 0
    print("MINECRAFT=FAILED")
    for line in text.splitlines()[-12:]:
        print("  " + line.strip())
    return 1


if __name__ == "__main__":
    sys.exit(main())
