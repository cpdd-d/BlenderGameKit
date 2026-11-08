"""我的世界示例的真机检查(第一百九十三轮;第一百九十七轮改为 .blend 工程)。

先用真 Blender 重建 `examples/demo_minecraft/我的世界.blend` 并导出
`我的世界_godot/`,再起无头 Godot 验证:

  * 开始界面:世界菜单开着、世界是暂停的(按 move_right 不动);
    点『创建游戏』弹创建面板、点『取消』回主菜单、点『开始游戏』收起菜单;
  * 玩家站在方块地面上(`is_on_floor`),按 move_right 真的走、按 jump 真的跳;
  * 准星射线真的能拆掉一块方块;
  * 右键放的是**当前选中的那种**方块(草 / 石 / 木,名字 + 材质颜色);
  * 三只怪物追到 2 米内,贴身后生命下降,HUD 文字跟着变。

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
DEMO = os.path.join(ROOT, "examples", "demo_minecraft", "我的世界_godot")
WORK = os.path.join(ROOT, "_dist", "minecraft_probe")
BLEND_DEFAULT = r"D:\blender-4.5.5-windows-x64\blender.exe"
GODOT_DEFAULT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"

PROBE = '''extends SceneTree

func _initialize() -> void:
	call_deferred("run")


func press(action: String, frames: int = 4) -> void:
	Input.action_press(action)
	for i in range(frames):
		await physics_frame
	Input.action_release(action)
	for i in range(4):
		await physics_frame


func static_count(root: Node) -> int:
	var total := 0
	for child in root.get_children():
		if child is StaticBody3D:
			total += 1
	return total


func first_color(node: Node) -> Color:
	for child in node.get_children():
		if child is MeshInstance3D:
			var mesh_node := child as MeshInstance3D
			var material: Material = mesh_node.material_override
			if material == null and mesh_node.mesh != null \\
					and mesh_node.mesh.get_surface_count() > 0:
				material = mesh_node.mesh.surface_get_material(0)
			if material is ShaderMaterial:
				var value = material.get_shader_parameter("albedo_color")
				if value is Color:
					return value
			if material is StandardMaterial3D:
				return (material as StandardMaterial3D).albedo_color
		var found := first_color(child)
		if found.a > 0.0:
			return found
	return Color(0, 0, 0, 0)


func run() -> void:
	var scene = load("res://scenes/World.tscn").instantiate()
	root.add_child(scene)
	current_scene = scene
	for i in range(30):
		await physics_frame
	var player = scene.get_node("Colliders/Player") as CharacterBody3D
	var colliders = scene.get_node("Colliders")
	var before = static_count(colliders)

	# 开始界面:世界菜单该是开着的,而且树是暂停的(按 move_right 不动)
	var menu_nodes: Array = scene.find_children("世界菜单", "Panel", true, false)
	var start_nodes: Array = scene.find_children("开始游戏", "Button", true, false)
	var create_nodes: Array = scene.find_children("创建游戏", "Button", true, false)
	var create_panel_nodes: Array = scene.find_children(
		"创建世界面板", "Panel", true, false)
	var cancel_nodes: Array = scene.find_children("取消创建", "Button", true, false)
	var menu_ok: bool = (menu_nodes.size() > 0 and menu_nodes[0].visible
		and start_nodes.size() > 0)

	var px0 = player.global_position.x
	Input.action_press("move_right")
	for i in range(10):
		await physics_frame
	Input.action_release("move_right")
	var frozen: bool = absf(player.global_position.x - px0) < 0.05

	# 创建游戏 -> 创建世界面板;取消 -> 回主菜单
	var create_ok := false
	var cancel_ok := false
	if menu_ok and create_nodes.size() > 0 and create_panel_nodes.size() > 0:
		create_nodes[0].pressed.emit()
		for i in range(3):
			await physics_frame
		create_ok = (create_panel_nodes[0].visible and not menu_nodes[0].visible)
		if cancel_nodes.size() > 0:
			cancel_nodes[0].pressed.emit()
			for i in range(3):
				await physics_frame
			cancel_ok = (not create_panel_nodes[0].visible
				and menu_nodes[0].visible)

	# 开始游戏 -> 菜单收起、世界解冻
	var started := false
	if menu_ok:
		start_nodes[0].pressed.emit()
		for i in range(5):
			await physics_frame
		started = (not menu_nodes[0].visible and not paused)

	# 解冻之后才开始落体 / 落地判定(暂停期间物理不走)
	for i in range(10):
		await physics_frame
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
	var camera = scene.get_node("第一人称相机") as Camera3D
	camera.rotation_degrees = Vector3(-40.0, 20.0, 0.0)
	for i in range(3):
		await physics_frame
	await press("mine")
	var after = static_count(colliders)
	var broke = after < before

	# 默认这一档是草方块:对着另一块按 Q
	camera.rotation_degrees = Vector3(-40.0, -20.0, 0.0)
	for i in range(3):
		await physics_frame
	await press("place")
	var grass_nodes: Array = scene.find_children(
		"草方块", "StaticBody3D", true, false)
	var placed = grass_nodes.size() > 0
	var grass_color_ok := false
	if grass_nodes.size() > 0:
		var c := first_color(grass_nodes[0])
		grass_color_ok = abs(c.r - 0.24) < 0.03 and abs(c.g - 0.60) < 0.03

	# 按 2 换石头 -> 放下来的是石头方块(不是草)
	camera.rotation_degrees = Vector3(-40.0, 50.0, 0.0)
	for i in range(3):
		await physics_frame
	await press("key_2")
	await press("place")
	var stone_nodes: Array = scene.find_children(
		"石头方块", "StaticBody3D", true, false)
	var stone_color_ok := false
	if stone_nodes.size() > 0:
		var c := first_color(stone_nodes[0])
		stone_color_ok = abs(c.r - 0.46) < 0.03 and abs(c.b - 0.52) < 0.03

	# 按 4 换木头
	camera.rotation_degrees = Vector3(-40.0, -50.0, 0.0)
	for i in range(3):
		await physics_frame
	await press("key_4")
	await press("place")
	var wood_nodes: Array = scene.find_children(
		"木头方块", "StaticBody3D", true, false)
	var wood_color_ok := false
	if wood_nodes.size() > 0:
		var c := first_color(wood_nodes[0])
		wood_color_ok = abs(c.r - 0.42) < 0.03 and abs(c.g - 0.27) < 0.03

	var switched = (stone_nodes.size() == 1 and wood_nodes.size() == 1
		and stone_color_ok and wood_color_ok)

	# 怪物:三只都该追到 2 米内,并且贴身后生命往下掉、HUD 跟着变
	var hp0 := float(player.get("生命"))
	var farthest := 0.0
	for i in range(240):
		await physics_frame
	for name in ["怪物1", "怪物2", "怪物3"]:
		var monster = scene.get_node("Colliders/" + name) as CharacterBody3D
		farthest = maxf(farthest,
			player.global_position.distance_to(monster.global_position))
	var hp1 := float(player.get("生命"))
	var chased := farthest <= 2.0
	var hurt := hp1 < hp0 and hp1 < 100.0
	var hp_labels: Array = scene.find_children("生命文本", "Label", true, false)
	var hud_ok: bool = (hp_labels.size() > 0
		and str(hp_labels[0].text).begins_with("生命 "))

	var ok = (on_floor and moved and jumped and broke and placed
		and grass_color_ok and switched and chased and hurt and hud_ok
		and menu_ok and frozen and create_ok and cancel_ok and started)
	print("MINECRAFT on_floor=", on_floor, " moved=", moved,
		" jumped=", jumped, " blocks=", before, "->", after,
		" broke=", broke, " placed=", placed,
		" grass=", grass_nodes.size(), " grass_color=", grass_color_ok,
		" stone=", stone_nodes.size(), " stone_color=", stone_color_ok,
		" wood=", wood_nodes.size(), " wood_color=", wood_color_ok,
		" farthest=", farthest, " hp=", hp0, "->", hp1,
		" chased=", chased, " hurt=", hurt, " hud=", hud_ok,
		" menu=", menu_ok, " frozen=", frozen,
		" create=", create_ok, " cancel=", cancel_ok, " started=", started)
	print("MINECRAFT_CHECK=", "OK" if ok else "FAIL")
	quit(0 if ok else 1)
'''


def find_blender(explicit=""):
    candidate = (explicit or os.environ.get("GK_BLENDER")
                 or BLEND_DEFAULT)
    return candidate if os.path.isfile(candidate) else ""


def find_godot(explicit=""):
    candidate = explicit or os.environ.get("GK_GODOT") or GODOT_DEFAULT
    return candidate if os.path.isfile(candidate) else ""


def rebuild(blender):
    script = os.path.join(HERE, "make_minecraft_demo.py")
    result = subprocess.run(
        [blender, "-b", "--gpu-backend", "vulkan", "--python", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600)
    text = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 or "已保存" not in text:
        print(text[-4000:])
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="我的世界示例真机检查")
    parser.add_argument("--godot", default="")
    parser.add_argument("--blender", default="")
    args = parser.parse_args()
    blender = find_blender(args.blender)
    if not blender:
        print(f"跳过:找不到 Blender({args.blender or BLEND_DEFAULT})")
        return 0
    godot = find_godot(args.godot)
    if not godot:
        print(f"跳过:找不到 Godot({args.godot or GODOT_DEFAULT})")
        return 0
    if not rebuild(blender):
        print("MINECRAFT=FAILED(重新生成 .blend / 导出失败)")
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
    for line in text.splitlines()[-16:]:
        print("  " + line.strip())
    return 1


if __name__ == "__main__":
    sys.exit(main())
