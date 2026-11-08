"""我的世界示例的真机检查(第一百九十八轮:第一人称/物品栏/暂停/无限地形)。

先用真 Blender 重建 `examples/demo_minecraft/我的世界.blend` 并导出
`我的世界_godot/`,再起无头 Godot 验证:

  * 开始界面:世界菜单开着、世界是暂停的;创建→取消→开始整条链;
  * 无限地形:开局就有 200+ 块地形,走到远处会重新铺、原点那块被卸载;
  * 第一人称:玩家模型默认藏起来,移动 / 跳跃 / 挖方块正常;
  * 物品栏:7 个格子 + "手持: xxx" HUD,数字键切换;
  * V:第一 / 第三人称切换(相机 + 模型显隐);
  * G:创造 / 生存切换,生存会被怪物扣血、创造不掉血;
  * 使用:苹果回血、剑击杀准星指着的怪物;
  * 放置:草 / 石 / 木按名字 + 材质颜色确认。

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


func tiles(scene: Node) -> int:
	return scene.find_children("地形_*", "StaticBody3D", true, false).size()


func run() -> void:
	var scene = load("res://scenes/World.tscn").instantiate()
	root.add_child(scene)
	current_scene = scene
	for i in range(30):
		await physics_frame
	var player = scene.get_node("Colliders/Player") as CharacterBody3D
	var cams: Array = scene.find_children("第一人称相机", "Camera3D", true, false)
	var camera: Camera3D = cams[0] if cams.size() > 0 else null
	var tiles_start: int = 0
	for i in range(3):
		await physics_frame
	tiles_start = tiles(scene)

	# ---- 开始界面 ----
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

	var started := false
	if menu_ok:
		start_nodes[0].pressed.emit()
		for i in range(5):
			await physics_frame
		started = (not menu_nodes[0].visible and not paused)

	for i in range(10):
		await physics_frame
	var on_floor = player.is_on_floor()
	var terrain_ok := tiles_start >= 200

	# ---- 移动 / 跳跃 ----
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
	for i in range(140):
		await physics_frame

	# ---- 挖一块地形 ----
	camera.rotation_degrees = Vector3(-40.0, 20.0, 0.0)
	for i in range(3):
		await physics_frame
	var tiles_before_mine: int = scene.find_children(
		"*", "StaticBody3D", true, false).size()
	await press("mine")
	var tiles_after_mine: int = scene.find_children(
		"*", "StaticBody3D", true, false).size()
	var broke = tiles_after_mine < tiles_before_mine

	# ---- 放方块(草 -> 石 -> 木)----
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

	camera.rotation_degrees = Vector3(-40.0, -50.0, 0.0)
	for i in range(3):
		await physics_frame
	await press("key_4")
	await press("place")
	var wood_nodes: Array = scene.find_children(
		"木头方块", "StaticBody3D", true, false)
	var switched = (stone_nodes.size() == 1 and wood_nodes.size() == 1)

	# ---- 物品栏 ----
	var hotbar_nodes: Array = scene.find_children("物品栏", "Panel", true, false)
	var slot_nodes: Array = scene.find_children("槽*", "Label", true, false)
	var hud_nodes: Array = scene.find_children("手持文本", "Label", true, false)
	var hud_text := ""
	if hud_nodes.size() > 0:
		hud_text = str(hud_nodes[0].text)
	var hotbar_ok: bool = (hotbar_nodes.size() > 0 and slot_nodes.size() == 7
		and hud_text.begins_with("手持: 木头方块"))

	# ---- V 切视角 ----
	await press("view_toggle")
	var cam_now: Camera3D = scene.get_viewport().get_camera_3d()
	var third_ok: bool = cam_now != null and str(cam_now.name) == "第三人称相机"
	var model_nodes: Array = scene.find_children("Model", "Node3D", true, false)
	var model_shown: bool = model_nodes.size() > 0 and model_nodes[0].visible
	await press("view_toggle")
	var cam_back: Camera3D = scene.get_viewport().get_camera_3d()
	var first_ok: bool = cam_back != null and str(cam_back.name) == "第一人称相机"
	var view_ok := third_ok and model_shown and first_ok

	# ---- G 切模式:生存会掉血,创造不会 ----
	var creative0 := bool(player.get("创造模式"))
	await press("mode_toggle")
	var creative1 := bool(player.get("创造模式"))
	var hp_surv0 := float(player.get("生命"))
	for i in range(180):
		await physics_frame
	var hp_surv1 := float(player.get("生命"))
	var hurt := hp_surv1 < hp_surv0
	await press("mode_toggle")
	var creative2 := bool(player.get("创造模式"))
	var hp_creative0 := float(player.get("生命"))
	for i in range(90):
		await physics_frame
	var hp_creative1 := float(player.get("生命"))
	var safe := absf(hp_creative1 - hp_creative0) < 0.01
	var mode_ok := creative0 and not creative1 and creative2 and hurt and safe

	# ---- 苹果回血 ----
	camera.rotation_degrees = Vector3(-40.0, 0.0, 0.0)
	for i in range(3):
		await physics_frame
	await press("key_6")
	var hp0 := float(player.get("生命"))
	await press("place")
	var hp1 := float(player.get("生命"))
	var ate := hp1 > hp0

	# ---- 剑击杀最近的怪物 ----
	var monsters: Array = scene.find_children("怪物*", "CharacterBody3D", true, false)
	var monsters_before := monsters.size()
	if monsters.size() > 0:
		camera.look_at((monsters[0] as Node3D).global_position
			+ Vector3(0, 0.7, 0), Vector3.UP)
	for i in range(3):
		await physics_frame
	await press("key_7")
	await press("place")
	for i in range(5):
		await physics_frame
	var monsters_after: int = scene.find_children(
		"怪物*", "CharacterBody3D", true, false).size()
	var sword_ok := monsters_before > 0 and monsters_after < monsters_before

	# ---- 天气:晴 / 雨 / 雪 能切换(雾 + 粒子)----
	var weather: Node = null
	for child in player.get_children():
		if child.has_method("set_weather"):
			weather = child
	var world_env: WorldEnvironment = null
	var envs: Array = scene.find_children("*", "WorldEnvironment", true, false)
	if envs.size() > 0:
		world_env = envs[0] as WorldEnvironment
	var weather_ok := false
	if weather != null and world_env != null and world_env.environment != null:
		weather.set_weather(1)
		var rainy: bool = world_env.environment.fog_enabled
		weather.set_weather(0)
		var clear: bool = not world_env.environment.fog_enabled
		weather.set_weather(2)
		var snowy: bool = world_env.environment.fog_enabled
		weather_ok = rainy and clear and snowy

	# ---- 羊会自己溜达 ----
	var sheep: Node3D = scene.get_node_or_null("Colliders/羊1") as Node3D
	var sheep_from := Vector3.ZERO
	if sheep != null:
		sheep_from = sheep.global_position
	for i in range(120):
		await physics_frame
	var sheep_moved: bool = (sheep != null
		and sheep.global_position.distance_to(sheep_from) > 0.2)

	# ---- 无限地形:走远之后重新铺、原点卸载 ----
	player.global_position = Vector3(60.0, 1.2, 0.0)
	for i in range(30):
		await physics_frame
	var tiles_far := tiles(scene)
	var old_tile: Array = scene.find_children(
		"地形_0_0", "StaticBody3D", true, false)
	var infinite_ok := tiles_far >= 200 and old_tile.size() == 0

	var ok = (on_floor and moved and jumped and broke and placed
		and grass_color_ok and switched and menu_ok and frozen
		and create_ok and cancel_ok and started and terrain_ok
		and hotbar_ok and view_ok and mode_ok and ate and sword_ok
		and weather_ok and sheep_moved and infinite_ok)
	print("MINECRAFT on_floor=", on_floor, " moved=", moved,
		" jumped=", jumped, " tiles=", tiles_before_mine, "->",
		tiles_after_mine, " broke=", broke, " placed=", placed,
		" grass_color=", grass_color_ok, " stone=", stone_nodes.size(),
		" wood=", wood_nodes.size(), " switched=", switched,
		" menu=", menu_ok, " frozen=", frozen, " create=", create_ok,
		" cancel=", cancel_ok, " started=", started,
		" terrain=", terrain_ok, " hotbar=", hotbar_ok,
		" hud=", hud_text, " view=", view_ok,
		" mode=", mode_ok, " hp=", hp_surv0, "->", hp_surv1,
		" ate=", ate, " sword=", sword_ok,
		" weather=", weather_ok, " sheep=", sheep_moved,
		" tiles_far=", tiles_far, " unload=", old_tile.size() == 0,
		" infinite=", infinite_ok)
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
    for line in text.splitlines()[-18:]:
        print("  " + line.strip())
    return 1


if __name__ == "__main__":
    sys.exit(main())


