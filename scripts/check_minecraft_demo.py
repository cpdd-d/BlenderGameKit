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
## 真实旧档(format 2):上一版构建真机保存的存档,迁移用例直接用它。
LEGACY_SAVE = os.path.join(ROOT, "tests", "fixtures", "minecraft",
                           "legacy_save_format2.json")
BLEND_DEFAULT = r"D:\blender-4.5.5-windows-x64\blender.exe"
GODOT_DEFAULT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"

PROBE = '''extends SceneTree

func _initialize() -> void:
	call_deferred("run")


func press(action: String, frames: int = 4) -> void:
	# 挖掘 / 放置只剩鼠标左 / 右键(E、Q 已删),直接发鼠标事件
	if action == "mine" or action == "place":
		await _tap_mouse(MOUSE_BUTTON_LEFT if action == "mine"
			else MOUSE_BUTTON_RIGHT, frames)
		return
	# 真键盘事件走输入管线:游戏在 _process 里用 is_action_just_pressed 判定,
	# 而 Input.action_press() 是在"当前帧"之外按下去的,那一帧的 _process
	# 早就跑完了,下一次已经是 pressed_frame+1,判定永远看不到 —— 放置会随机丢。
	# 这里优先按 InputMap 里绑的键发一对 down/up 事件,和真人按键同一路径。
	for event in InputMap.action_get_events(action):
		if event is InputEventKey:
			var key := (event as InputEventKey).physical_keycode
			await _tap_key(key, frames)
			return
	Input.action_press(action)
	for i in range(frames):
		await process_frame
		await physics_frame
	Input.action_release(action)
	for i in range(4):
		await process_frame
		await physics_frame


func _tap_mouse(button: MouseButton, frames: int) -> void:
	var down := InputEventMouseButton.new()
	down.button_index = button
	down.pressed = true
	Input.parse_input_event(down)
	for i in range(frames):
		await process_frame
		await physics_frame
	var up := InputEventMouseButton.new()
	up.button_index = button
	up.pressed = false
	Input.parse_input_event(up)
	for i in range(4):
		await process_frame
		await physics_frame


func _tap_key(keycode: Key, frames: int) -> void:
	var down := InputEventKey.new()
	down.physical_keycode = keycode
	down.pressed = true
	Input.parse_input_event(down)
	for i in range(frames):
		await process_frame
		await physics_frame
	var up := InputEventKey.new()
	up.physical_keycode = keycode
	up.pressed = false
	Input.parse_input_event(up)
	for i in range(4):
		await process_frame
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


func first_texture(node: Node) -> String:
	for child in node.get_children():
		if child is MeshInstance3D:
			var mesh_node := child as MeshInstance3D
			var material: Material = mesh_node.material_override
			if material == null and mesh_node.mesh != null \\
					and mesh_node.mesh.get_surface_count() > 0:
				material = mesh_node.mesh.surface_get_material(0)
			if material is ShaderMaterial:
				var value = material.get_shader_parameter("albedo_texture")
				if value is Texture2D:
					return (value as Texture2D).resource_path
			if material is StandardMaterial3D:
				var albedo := (material as StandardMaterial3D).albedo_texture
				if albedo != null:
					return albedo.resource_path
		var found := first_texture(child)
		if not found.is_empty():
			return found
	return ""


func by_y(a, b) -> bool:
	return (a as Node3D).global_position.y < (b as Node3D).global_position.y


## 帧时分布的 p50 / p95 / max(M2a 第三段:连续挖 10 格只测量,不优化)。
func percentile(values: Array, ratio: float) -> float:
	if values.is_empty():
		return 0.0
	var sorted := values.duplicate()
	sorted.sort()
	var index := clampi(int(round(float(sorted.size() - 1) * ratio)), 0,
		sorted.size() - 1)
	return float(sorted[index])


func count_over(values: Array, limit: float) -> int:
	var total := 0
	for value in values:
		if float(value) > limit:
			total += 1
	return total


## 某个格子地表上方 1.2 格:探针要把人挪到没铺过的地方时先算落点。
func ground_point(chunk: Node, cell: Vector2i) -> Vector3:
	var height := int(chunk.call("_height_at", cell))
	return Vector3(cell.x + 0.5, float(height) + 1.2, cell.y + 0.5)


## 进程常驻内存(MB)。Godot 没有直接读 RSS 的接口,借一次 PowerShell 查。
func process_rss_mb() -> float:
	var out: Array = []
	var pid := OS.get_process_id()
	var code := OS.execute("powershell.exe", ["-NoProfile", "-Command",
		"(Get-Process -Id %d).WorkingSet64" % pid], out, false)
	if code == 0 and out.size() > 0:
		var text := str(out[0]).strip_edges()
		if text.is_valid_int():
			return float(int(text)) / 1048576.0
	return -1.0


## 近景分段网格里出现过多少种亮度(顶点色 r)。柔和光影开时 AO 会造出
## 很多中间值,关掉后只剩 4 个面亮度。取所有分段里的最大值。
func shade_levels(scene: Node) -> int:
	var best := 0
	for node in scene.find_children("段块_*", "MeshInstance3D", true, false):
		var mesh_node := node as MeshInstance3D
		if mesh_node.mesh == null or mesh_node.mesh.get_surface_count() == 0:
			continue
		var arrays := mesh_node.mesh.surface_get_arrays(0)
		if arrays.size() == 0:
			continue
		var colors: PackedColorArray = arrays[Mesh.ARRAY_COLOR]
		var levels := {}
		for color in colors:
			levels[snappedf(color.r, 0.001)] = true
		best = maxi(best, levels.size())
	return best


func first_material(node: Node) -> Material:
	for child in node.get_children():
		if child is MeshInstance3D:
			var mesh_node := child as MeshInstance3D
			if mesh_node.material_override != null:
				return mesh_node.material_override
			if mesh_node.mesh != null and mesh_node.mesh.get_surface_count() > 0:
				return mesh_node.mesh.surface_get_material(0)
		var found := first_material(child)
		if found != null:
			return found
	return null


func tiles(scene: Node) -> int:
	return scene.find_children("地形_*", "StaticBody3D", true, false).size()


func inv_total(node: Node) -> int:
	var total := 0
	for slot in node.call("export_slots"):
		if slot is Array:
			total += int(slot[1])
	return total


func wipe_dir(path: String) -> void:
	var dir := DirAccess.open(path)
	if dir == null:
		return
	dir.list_dir_begin()
	var entry := dir.get_next()
	while entry != "":
		if entry.begins_with("."):
			entry = dir.get_next()
			continue
		if dir.current_is_dir():
			wipe_dir(path + "/" + entry)
		else:
			DirAccess.remove_absolute(path + "/" + entry)
		entry = dir.get_next()
	dir.list_dir_end()
	DirAccess.remove_absolute(path)


func run() -> void:
	# 设置文件会跨进程留在 user://:探针每次从默认值开始
	if FileAccess.file_exists("user://settings.cfg"):
		DirAccess.remove_absolute(ProjectSettings.globalize_path("user://settings.cfg"))
	# 用户自己的存档不能动:整个目录先挪到备份位置,跑完再原样放回。
	# 探针要从"一个存档都没有"的干净状态开始,才能验证开始菜单的收敛逻辑。
	var saves_abs := ProjectSettings.globalize_path("user://saves")
	var backup_abs := ProjectSettings.globalize_path("user://saves_probe_backup")
	var had_saves := DirAccess.dir_exists_absolute(saves_abs)
	if had_saves:
		wipe_dir(backup_abs)
		DirAccess.rename_absolute(saves_abs, backup_abs)
	var scene = load("res://scenes/World.tscn").instantiate()
	root.add_child(scene)
	current_scene = scene
	for i in range(30):
		await physics_frame
	# 开始菜单是暂停的:地形脚本在 _process 里按帧分摊铺,先等它铺满,
	# 后面的"铺了几块 / 挖掉一块"这类计数才不会被打断。
	# 等 is_ready() 而不是死等固定帧数:渲染距离档位最大到 32 区块,
	# 固定帧数在不同档位下要么白等、要么不够。
	var preload_node: Node = null
	for child in scene.get_node("Colliders/Player").get_children():
		if child.has_method("is_ready") and child.has_method("load_progress"):
			preload_node = child
			break
	var preload_frames := 0
	for i in range(9000):
		await process_frame
		preload_frames += 1
		if preload_node != null and bool(preload_node.call("is_ready")):
			break
	print("MINECRAFT_DEBUG preload frames=", preload_frames)
	var player = scene.get_node("Colliders/Player") as CharacterBody3D
	## 出生点(第两百一十六轮):地形脚本挑出来的坐标,后面站位用例都用它,
	## 不再假设原点是一块被压平的 1 层地。
	var spawn_point := Vector3.ZERO
	var cams: Array = scene.find_children("第一人称相机", "Camera3D", true, false)
	var camera: Camera3D = cams[0] if cams.size() > 0 else null
	var tiles_start: int = 0
	for i in range(3):
		await physics_frame
	tiles_start = tiles(scene)

	# 开始菜单的背景(第两百零八轮:整层 Control = 风景 + 暗罩 + 云)
	var bg_nodes: Array = scene.find_children("菜单背景", "Control", true, false)
	var bg_ok: bool = false
	if bg_nodes.size() > 0:
		var menu_root: Control = bg_nodes[0] as Control
		var picture: Node = menu_root.find_child("风景", true, false)
		var dim: Node = menu_root.find_child("暗罩", true, false)
		bg_ok = (menu_root.visible
			and picture is TextureRect
			and (picture as TextureRect).texture != null
			and dim is ColorRect and (dim as ColorRect).color.a > 0.3)
	var chunk_node: Node = null
	var save_node: Node = null
	for child in player.get_children():
		if child.has_method("save_state"):
			chunk_node = child
		if child.has_method("open_menu_primary"):
			save_node = child
	# 第两百零八轮:渲染距离按原版档位(8/12/16/24/32 区块),默认 12 区块 = 192 格
	var range_ok: bool = (chunk_node != null
		and int(chunk_node.get("_radius")) >= 128)
	var tree_default_ok: bool = (chunk_node != null
		and int(chunk_node.get("_tree_percent")) == 10)
	var far_nodes: Array = scene.find_children(
		"远景网格", "GeometryInstance3D", true, false)
	var far_solid := false
	if far_nodes.size() > 0:
		var probe_node: Node = far_nodes[0]
		while probe_node != null and not (probe_node is StaticBody3D):
			probe_node = probe_node.get_parent()
		far_solid = probe_node is StaticBody3D
	var tier_ok: bool = (far_nodes.size() >= 120
		and not far_solid and tiles_start >= 200)
	print("MINECRAFT_DEBUG far=", far_nodes.size(), " tiles_start=", tiles_start,
		" far_solid=", far_solid, " radius=", chunk_node.get("_radius") if chunk_node != null else -1)

	# ---- M2a 第二段:分段网格(16×16×SECTION_HEIGHT)----
	var section_ok := false
	var section_detail := ""
	var place_section_ok := false
	var place_section_detail := "n/a"
	var section_nodes: Array = scene.find_children("分段_*", "Node3D", true, false)
	var section_meshes: Array = scene.find_children("段块_*", "MeshInstance3D",
		true, false)
	if chunk_node != null:
		var stats: Dictionary = chunk_node.call("section_stats")
		var audit: Dictionary = chunk_node.call("section_audit")
		var empty_meshes := 0
		for item in section_meshes:
			var mesh_node := item as MeshInstance3D
			if mesh_node.mesh == null or mesh_node.mesh.get_surface_count() == 0:
				empty_meshes += 1
		# 每个已建 section 至少一个非空类型的网格;段里没有的类型不能建空 mesh
		var holder_ok := section_nodes.size() > 0
		for holder in section_nodes:
			var kinds := 0
			for child in (holder as Node).get_children():
				if child is MeshInstance3D:
					kinds += 1
			if kinds < 1:
				holder_ok = false
		section_ok = (holder_ok and empty_meshes == 0
			and section_meshes.size() >= 4
			and int(stats.get("sections", 0)) == section_nodes.size()
			and int(audit.get("missing", -1)) == 0
			and int(audit.get("extra", -1)) == 0
			and int(audit.get("duplicated", -1)) == 0
			and int(audit.get("malformed", -1)) == 0
			and int(audit.get("emitted", 0)) > 0)
		section_detail = "nodes=%d meshes=%d empty=%d builds=%d max_us=%d audit=%s" % [
			section_nodes.size(), section_meshes.size(), empty_meshes,
			int(stats.get("builds", 0)), int(stats.get("max_us", 0)),
			str(audit)]
		var log_tail: Array = chunk_node.call("section_rebuild_log")
		var tail: Array = []
		for i in range(maxi(0, log_tail.size() - 6), log_tail.size()):
			tail.append(log_tail[i])
		print("MINECRAFT_DEBUG section_log_tail=", tail)
	print("MINECRAFT_DEBUG section=", section_ok, " ", section_detail)

	# ---- 柔和光影(面亮度差 + 角落 AO):默认开,可关 ----
	# 放在分段测试后面测:这里近景段刚校验过(25 段 / 46 个网格),样本稳定;
	# 后面的水 / 读档用例会把玩家挪走、分段重排,数不到完整的 AO 层级。
	# set_soft_light 是同步重建,暂停中也能调。
	var soft_ok := false
	var soft_detail := ""
	if chunk_node != null and chunk_node.has_method("set_soft_light"):
		var default_on := bool(chunk_node.call("soft_light_enabled"))
		var varied_on := shade_levels(scene)
		chunk_node.call("set_soft_light", false)
		var off_now := bool(chunk_node.call("soft_light_enabled"))
		var varied_off := shade_levels(scene)
		chunk_node.call("set_soft_light", true)
		var on_now := bool(chunk_node.call("soft_light_enabled"))
		var varied_back := shade_levels(scene)
		var soft_box: Array = scene.find_children("柔和光影勾选", "CheckBox",
			true, false)
		soft_ok = (default_on and not off_now and on_now and varied_on > 4
			and varied_off <= 4 and varied_back > 4 and soft_box.size() > 0)
		soft_detail = "levels on=%d off=%d back=%d box=%d" % [
			varied_on, varied_off, varied_back, soft_box.size()]
	print("MINECRAFT soft=", soft_ok, soft_detail)

	# ---- 开始界面(第两百零八轮:按设计稿做成四个固定入口 + 悬停箭头)----
	var menu_nodes: Array = scene.find_children("世界菜单", "Panel", true, false)
	var start_nodes: Array = []
	var create_nodes: Array = []
	var read_nodes: Array = []
	var quit_nodes: Array = []
	var menu_ok := false
	var menu_fresh_ok := false
	var menu_style_ok := false
	var menu_cloud_ok := false
	if menu_nodes.size() > 0:
		var menu: Panel = menu_nodes[0]
		var primary: Node = menu.find_child("继续游戏", true, false)
		var create_node: Node = menu.find_child("新建世界", true, false)
		var read_node: Node = menu.find_child("读取存档", true, false)
		var quit_node: Node = menu.find_child("退出游戏", true, false)
		var subtitle: Node = menu.find_child("菜单副标题", true, false)
		var note: Node = menu.find_child("菜单提示", true, false)
		if primary != null:
			start_nodes = [primary]
		if create_node != null:
			create_nodes = [create_node]
		if read_node != null:
			read_nodes = [read_node]
		if quit_node != null:
			quit_nodes = [quit_node]
		menu_ok = (menu.visible and start_nodes.size() > 0
			and create_nodes.size() > 0 and quit_nodes.size() > 0)
		# 单入口:没存档时『开始游戏』置灰(只做继续),『新建世界』永远可点;
		# 『读取存档』也置灰(设计稿的 disabled 态,不再整行收起)。
		menu_fresh_ok = (menu_ok
			and str((start_nodes[0] as Button).text) == "开始游戏"
			and (start_nodes[0] as Button).disabled
			and (create_nodes[0] as Control).visible
			and not (create_nodes[0] as Button).disabled
			and (read_nodes[0] as Button).disabled
			and absf((create_nodes[0] as Control).position.y - 300.0) < 0.5
			and str((create_nodes[0] as Button).text) == "新建世界")
		# 设计稿样式:副标题 / 按钮四态 / 悬停才出现的『›』
		# (拿『新建世界』测:没存档时主按钮是禁用的,禁用键不弹箭头)
		if menu_ok and subtitle != null and note != null:
			var probe_button: Button = create_nodes[0] as Button
			var arrow: Node = probe_button.get_node_or_null("箭头")
			var hidden_first: bool = arrow != null and not (arrow as Label).visible
			probe_button.emit_signal("mouse_entered")
			var shown_hover: bool = arrow != null and (arrow as Label).visible
			probe_button.emit_signal("mouse_exited")
			menu_style_ok = (hidden_first and shown_hover
				and probe_button.has_theme_stylebox_override("hover")
				and (menu as Control).has_theme_stylebox_override("panel")
				and (subtitle as Label).text.begins_with("MINECRAFT")
				and (note as Label).text.contains("16 区块"))
		# 背景层:风景 + 暗罩 + 会飘的云(云的位置跑几帧必须变)
		var menu_bg_nodes: Array = scene.find_children("菜单背景", "Control",
			true, false)
		var cloud_nodes: Array = scene.find_children("云*", "TextureRect", true, false)
		if menu_bg_nodes.size() > 0 and cloud_nodes.size() >= 5:
			var cloud_x := (cloud_nodes[0] as Control).position.x
			for i in range(20):
				await process_frame
			menu_cloud_ok = absf((cloud_nodes[0] as Control).position.x - cloud_x) > 1.0
	var create_panel_nodes: Array = scene.find_children(
		"创建面板", "Control", true, false)
	var cancel_nodes: Array = scene.find_children("创建取消", "Button", true, false)

	var px0 = player.global_position.x
	Input.action_press("move_right")
	for i in range(10):
		await physics_frame
	Input.action_release("move_right")
	var frozen: bool = absf(player.global_position.x - px0) < 0.05

	var create_ok := false
	var cancel_ok := false
	if menu_ok and create_panel_nodes.size() > 0 and create_nodes.size() > 0:
		# 单入口:『新建世界』专门新建;『开始游戏』只做继续(没档时置灰)
		create_nodes[0].pressed.emit()
		for i in range(3):
			await physics_frame
		# 新建世界面板:名称 / 种子 / 地形 / 大小 / 植被 / 昼夜 / 渲染距离 / 模式
		var fields: Array = scene.find_children("新世界名输入", "LineEdit", true, false)
		var seeds: Array = scene.find_children("新种子输入", "LineEdit", true, false)
		var modes: Array = scene.find_children("创造模式", "Button", true, false)
		create_ok = (create_panel_nodes[0].visible and fields.size() == 1
			and seeds.size() == 1 and modes.size() == 1)
		if cancel_nodes.size() > 0:
			cancel_nodes[0].pressed.emit()
			for i in range(3):
				await physics_frame
			cancel_ok = (not create_panel_nodes[0].visible
				and menu_nodes[0].visible)

	var started := false
	var mouse_ok := false
	var load_ui_ok := false
	var load_seconds := -1.0
	## M2a 第三段:先加载后进入的指标(在 if 外声明,最后要并进 ok)
	var world_load_s := -1.0
	var entry_max_ms := 0.0
	var entry_frames := 0
	var world_load_ok := false
	var coverage_ok := false
	var coverage_detail := "n/a"
	var fog_hole_ok := false
	var fog_hole_detail := "n/a"
	if menu_ok and create_nodes.size() > 0:
		create_nodes[0].pressed.emit()
		for i in range(3):
			await physics_frame
		var creative_start: Array = scene.find_children(
			"创造模式", "Button", true, false)
		var load_started_msec := Time.get_ticks_msec()
		# 加载页在开场就建好了:先拿好引用,别在加载循环里每帧扫整棵树
		var load_ui_nodes: Array = scene.find_children("加载面板", "Control",
			true, false)
		var load_panel: Control = null
		if load_ui_nodes.size() > 0:
			load_panel = load_ui_nodes[0] as Control
		var load_bars: Array = scene.find_children("加载进度", "ProgressBar",
			true, false)
		var load_bar: ProgressBar = null
		if load_bars.size() > 0:
			load_bar = load_bars[0] as ProgressBar
		if creative_start.size() > 0:
			# 进世界这一档的帧时单独统计:先清空分区统计,再点开始
			if save_node != null:
				save_node.call("perf_reset")
			creative_start[0].pressed.emit()
		# 新建后脚本按帧铺出生点(『正在生成世界…』+ 真实进度)再解除暂停
		var saw_loading := false
		var saw_middle := false
		var max_ratio := 0.0
		var load_frames := 0
		for i in range(3600):
			await process_frame
			load_frames += 1
			if load_panel != null:
				if load_panel.visible:
					saw_loading = true
				if load_bar != null:
					max_ratio = maxf(max_ratio, float(load_bar.value))
					if float(load_bar.value) >= 1.0 \
							and float(load_bar.value) <= 99.0:
						saw_middle = true
			if chunk_node != null and load_frames % 300 == 0:
				print("MINECRAFT_DEBUG loading frames=", load_frames, " near=",
					(chunk_node.get("_near_queue") as Array).size(),
					" regions=",
					(chunk_node.get("_region_queue") as Array).size(),
					" far=", (chunk_node.get("_far_regions") as Dictionary).size(),
					" total=", chunk_node.get("_progress_total"))
			# 出生点改成脚本挑之后,解除暂停发生在传送之前;必须等加载屏真的
			# 收了(传送 + 铺地都做完)再读玩家坐标,否则读到的是旧位置。
			if not paused and (load_panel == null or not load_panel.visible):
				break
		load_seconds = float(Time.get_ticks_msec() - load_started_msec) / 1000.0
		print("MINECRAFT_DEBUG load frames=", load_frames, " seconds=",
			load_seconds)
		load_ui_ok = (saw_loading and load_panel != null
			and not load_panel.visible and max_ratio >= 50.0 and saw_middle)
		# ---- M2a 第三段:先加载后进入 ----
		# 放开操作的那一刻:5×5 的碰撞列 / 网格全在,未完成区域只在雾后。
		if save_node != null:
			world_load_s = float(save_node.call("world_load_seconds"))
			var zones: Dictionary = save_node.call("perf_report")
			if zones.has("进世界"):
				var zone: Dictionary = zones["进世界"]
				entry_max_ms = float(zone.get("max_ms", 0.0))
				entry_frames = int(zone.get("frames", 0))
		world_load_ok = (world_load_s >= 0.0 and world_load_s <= 5.0)
		if chunk_node != null:
			var cover: Dictionary = chunk_node.call("spawn_coverage", 2)
			coverage_ok = (int(cover.get("cells_total", 0)) > 0
				and int(cover.get("cells_ready", 0))
					== int(cover.get("cells_total", -1))
				and int(cover.get("regions", 0)) > 0
				and int(cover.get("meshed", 0)) == int(cover.get("regions", -1)))
			coverage_detail = "cells=%d/%d regions=%d/%d" % [
				int(cover.get("cells_ready", 0)), int(cover.get("cells_total", 0)),
				int(cover.get("meshed", 0)), int(cover.get("regions", 0))]
			var fog_begin := float(chunk_node.call("fog_begin_distance"))
			var hole_min := float(chunk_node.call("incomplete_min_distance"))
			fog_hole_ok = hole_min >= fog_begin - 16.0
			fog_hole_detail = "fog_begin=%.0f hole_min=%.0f" % [fog_begin, hole_min]
		print("MINECRAFT_DEBUG world_load_s=%.2f entry_frames=%d entry_max_ms=%.1f" % [
			world_load_s, entry_frames, entry_max_ms],
			" coverage=", coverage_ok, " ", coverage_detail,
			" fog_hole=", fog_hole_ok, " ", fog_hole_detail)
		started = (not menu_nodes[0].visible and not paused)
		# 进世界必须把鼠标锁回去(无头读不到真实光标,读脚本记下的值)
		mouse_ok = (save_node != null
			and int(save_node.get("_last_mouse_mode")) == 2)
		spawn_point = Vector3(float(player.get("出生点x")),
			float(player.get("出生点y")), float(player.get("出生点z")))
	for i in range(3):
		await process_frame
	var bg_hidden: bool = bg_nodes.size() > 0 and not bg_nodes[0].visible

	for i in range(10):
		await physics_frame
	var on_floor = player.is_on_floor()
	var terrain_ok := tiles_start >= 200

	# ---- 鼠标转视角:物理帧里消费位移,必须真的转动相机 ----
	var yaw0: float = camera.rotation_degrees.y
	var yaw1 := yaw0
	# 事件偶尔会落在"刚清过位移"的那一帧(加载 / 建列忙时),重发几次更稳
	for attempt in range(3):
		var motion := InputEventMouseMotion.new()
		motion.relative = Vector2(40.0, 0.0)
		Input.parse_input_event(motion)
		for i in range(4):
			await physics_frame
		yaw1 = camera.rotation_degrees.y
		if absf(yaw1 - yaw0) > 0.5:
			break
	var look_ok: bool = absf(yaw1 - yaw0) > 0.5
	var look_detail := "%.2f->%.2f" % [yaw0, yaw1]

	# ---- 怪物先站着不动(这轮不做追击 / 攻击)----
	var monster_from := {}
	for body in scene.find_children("怪物*", "CharacterBody3D", true, false):
		monster_from[str(body.name)] = (body as Node3D).global_position
	for i in range(120):
		await physics_frame
	var monsters_still := true
	var monster_drift := 0.0
	for body in scene.find_children("怪物*", "CharacterBody3D", true, false):
		var label := str(body.name)
		if not monster_from.has(label):
			continue
		var gap: float = (body as Node3D).global_position.distance_to(
			monster_from[label])
		monster_drift = maxf(monster_drift, gap)
		if gap > 0.3:
			monsters_still = false
	monsters_still = monsters_still and monster_from.size() >= 2

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
	var removed_before: int = chunk_node.get("_removed").size()
	await press("mine")
	var tiles_after_mine: int = scene.find_children(
		"*", "StaticBody3D", true, false).size()
	var removed_after: int = chunk_node.get("_removed").size()
	# 挖没挖掉看区块脚本自己的记录(地形还在流式铺,直接数节点会被新铺的列盖住)
	var broke = removed_after > removed_before

	# ---- 分段重建:挖中间一格只重建所在 section,碰撞拆成上下两段 ----
	var mid_split_ok := false
	var only_section_ok := false
	var mid_detail := ""
	if chunk_node != null:
		var spawn_cell := Vector2i(int(floor(player.global_position.x)),
			int(floor(player.global_position.z)))
		var target_cell := Vector2i(1 << 30, 1 << 30)
		for dz in range(-24, 25):
			for dx in range(-24, 25):
				var c := spawn_cell + Vector2i(dx, dz)
				# 选区块内部(离边界 ≥2 格)、高度 ≥4 的列;
				# 必须落在近景区块里 —— 出生圈外圈只有碰撞没有分段网格,
				# 挖它不会触发 section 重建,测出来的"0 重建"是假象。
				var lx := posmod(c.x, 16)
				var lz := posmod(c.y, 16)
				if lx < 2 or lx > 13 or lz < 2 or lz > 13:
					continue
				var near_regions: Dictionary = chunk_node.get("_near_regions")
				if not near_regions.has(Vector2i(int(floor(float(c.x) / 16.0)),
						int(floor(float(c.y) / 16.0)))):
					continue
				if int(chunk_node.call("_height_at", c)) >= 4 \
						and chunk_node.call("column_blocks", c).size() >= 4:
					target_cell = c
					break
			if target_cell.x != (1 << 30):
				break
		if target_cell.x != (1 << 30):
			var body: Node = scene.find_child("地形_%d_%d" % [target_cell.x,
				target_cell.y], true, false)
			var kinds: Array = body.get_meta("gk_kinds") if body != null else []
			var level := mini(2, kinds.size() - 2)
			var kind := str(kinds[level])
			var region := Vector2i(int(floor(float(target_cell.x) / 16.0)),
				int(floor(float(target_cell.y) / 16.0)))
			var stats_before: Dictionary = chunk_node.call("section_stats")
			var builds_before := int(stats_before["builds"])
			# 直接走和挖掘同一条函数(改 gk_kinds / 记录 / 碰撞 / 重建 section)
			chunk_node.call("_remove_block", body, target_cell, level, kind, true)
			var shapes: Array = chunk_node.call("column_shapes", target_cell)
			mid_split_ok = (shapes.size() == 2
				and float(shapes[0]["end"]) <= float(level)
				and float(shapes[1]["start"]) >= float(level + 1))
			var log_after: Array = chunk_node.call("section_rebuild_log")
			var stats_after: Dictionary = chunk_node.call("section_stats")
			var builds_after := int(stats_after["builds"])
			var fresh := mini(builds_after - builds_before, log_after.size())
			var expected := Vector3i(region.x, int(level / 16), region.y)
			only_section_ok = fresh == 1
			var mine_us := 0
			for i in range(fresh):
				var entry: Dictionary = log_after[log_after.size() - fresh + i]
				mine_us = int(entry.get("us", -1))
				if entry.get("key", Vector3i.ZERO) != expected \
						or str(entry.get("reason", "")) != "mine" \
						or int(entry.get("us", -1)) < 0:
					only_section_ok = false
			mid_detail = "cell=%s level=%d shapes=%d fresh=%d expected=%s mine_us=%d" % [
				target_cell, level, shapes.size(), fresh, expected, mine_us]
			print("MINECRAFT_DEBUG split shapes=", shapes)
	print("MINECRAFT_DEBUG mid_split=", mid_split_ok, " only_section=",
		only_section_ok, " ", mid_detail)

	# ---- M2a 第三段:连续挖 10 格的帧时分布(只测量,不优化)----
	# 第二段留下"单格 section 重建 7.26ms"这个数,这里看连挖时帧时到底怎样:
	# 按住左键慢慢横扫,创造模式每 0.12 秒碎一格,记录每一帧的间隔。
	var dig_frames: Array = []
	var dig_section_us: Array = []
	var dig_ok := false
	var dig_detail := "n/a"
	if chunk_node != null:
		player.set("创造模式", true)
		camera.rotation_degrees = Vector3(-55.0, 130.0, 0.0)
		for i in range(4):
			await physics_frame
		var removed_dig0 := int(chunk_node.get("_removed").size())
		var builds_dig0 := int(chunk_node.call("section_stats")["builds"])
		var last_us := Time.get_ticks_usec()
		var dig_down := InputEventMouseButton.new()
		dig_down.button_index = MOUSE_BUTTON_LEFT
		dig_down.pressed = true
		Input.parse_input_event(dig_down)
		for i in range(150):
			await process_frame
			var now_us := Time.get_ticks_usec()
			dig_frames.append(float(now_us - last_us) / 1000.0)
			last_us = now_us
			if i % 8 == 7:
				camera.rotation_degrees.y += 4.0
		var dig_up := InputEventMouseButton.new()
		dig_up.button_index = MOUSE_BUTTON_LEFT
		dig_up.pressed = false
		Input.parse_input_event(dig_up)
		for i in range(4):
			await process_frame
		var digs := int(chunk_node.get("_removed").size()) - removed_dig0
		var stats_dig: Dictionary = chunk_node.call("section_stats")
		var fresh_dig := mini(int(stats_dig["builds"]) - builds_dig0,
			int(chunk_node.call("section_rebuild_log").size()))
		var log_dig: Array = chunk_node.call("section_rebuild_log")
		for i in range(log_dig.size() - fresh_dig, log_dig.size()):
			if i >= 0 and str(log_dig[i].get("reason", "")) == "mine":
				dig_section_us.append(int(log_dig[i].get("us", 0)))
		dig_ok = digs >= 10
		dig_detail = ("digs=%d frames=%d p50=%.2f p95=%.2f max=%.2f"
			+ " over16=%d over33=%d section_n=%d section_p50=%.0f section_max=%.0f") % [
			digs, dig_frames.size(), percentile(dig_frames, 0.5),
			percentile(dig_frames, 0.95), percentile(dig_frames, 1.0),
			count_over(dig_frames, 16.7), count_over(dig_frames, 33.4),
			dig_section_us.size(), percentile(dig_section_us, 0.5),
			percentile(dig_section_us, 1.0)]
		print("MINECRAFT_DEBUG dig10=", dig_ok, " ", dig_detail)
		player.set("创造模式", true)

	# ---- 生存挖掘时间(第两百一十四轮):按住累积 / 松手清零 / 10 阶段裂纹 ----
	player.set("创造模式", false)
	# 上面创造模式那一下把准星正前方那格挖掉了,这里换个反方向看,
	# 否则射线会正好从刚挖出来的 1 格空洞穿过去(命中为空,永远挖不到东西)。
	camera.rotation_degrees = Vector3(-50.0, -40.0, 0.0)
	for i in range(4):
		await physics_frame
	var removed_s := int(chunk_node.get("_removed").size())
	var mine_down := InputEventMouseButton.new()
	mine_down.button_index = MOUSE_BUTTON_LEFT
	mine_down.pressed = true
	Input.parse_input_event(mine_down)
	# 先按约 0.13 秒:草方块硬度 0.6 → 徒手 0.9 秒,这时不该碎,但裂纹应已出来
	for i in range(8):
		await process_frame
		await physics_frame
	var survival_hold := int(chunk_node.get("_removed").size()) == removed_s
	var crack_nodes: Array = scene.find_children("破坏裂纹", "MeshInstance3D", true, false)
	var survival_crack: bool = (crack_nodes.size() > 0
		and (crack_nodes[0] as Node3D).visible)
	# 继续按到 0.8 秒以上:必须挖掉
	for i in range(55):
		await process_frame
		await physics_frame
	var mine_up := InputEventMouseButton.new()
	mine_up.button_index = MOUSE_BUTTON_LEFT
	mine_up.pressed = false
	Input.parse_input_event(mine_up)
	for i in range(4):
		await process_frame
		await physics_frame
	var survival_break := int(chunk_node.get("_removed").size()) > removed_s
	var mine_time_ok := survival_hold and survival_crack and survival_break
	print("MINECRAFT_DEBUG mine_time=", mine_time_ok, " hold=", survival_hold,
		" crack=", survival_crack, " break=", survival_break,
		" stage=", chunk_node.get("_crack_stage"))
	player.set("创造模式", true)

	# ---- 放方块(草 -> 石 -> 木)----
	camera.rotation_degrees = Vector3(-40.0, -20.0, 0.0)
	for i in range(3):
		await physics_frame
	await press("place")
	var grass_nodes: Array = scene.find_children(
		"草方块", "StaticBody3D", true, false)
	var placed = grass_nodes.size() > 0
	# ---- 放下的方块"看得见":网格必须就在方块自己身上,不能偏走 ----
	# (真机踩过:资产 glb 把父级空物体的 -40 位移烘进模型,方块放是放下了,
	#  网格却被埋到地下 40 格 —— 用户看到的就是"无法放置"。)
	var placed_visible := false
	if grass_nodes.size() > 0:
		var placed_body := grass_nodes[0] as Node3D
		var meshes: Array = placed_body.find_children(
			"*", "MeshInstance3D", true, false)
		if meshes.size() > 0:
			var placed_mesh := meshes[0] as Node3D
			placed_visible = placed_mesh.global_position.distance_to(
				placed_body.global_position) < 2.0
	var grass_color_ok := false
	if grass_nodes.size() > 0:
		grass_color_ok = first_texture(grass_nodes[0]).ends_with("mc_grass.png")

	camera.rotation_degrees = Vector3(-40.0, 50.0, 0.0)
	for i in range(3):
		await physics_frame
	await press("key_2")
	await press("place")
	var stone_nodes: Array = scene.find_children(
		"石头方块", "StaticBody3D", true, false)
	var stone_color_ok := false
	if stone_nodes.size() > 0:
		stone_color_ok = first_texture(stone_nodes[0]).ends_with("mc_stone.png")

	camera.rotation_degrees = Vector3(-40.0, -50.0, 0.0)
	for i in range(3):
		await physics_frame
	await press("key_4")
	await press("place")
	var wood_nodes: Array = scene.find_children(
		"木头方块", "StaticBody3D", true, false)
	var switched = (stone_nodes.size() == 1 and wood_nodes.size() == 1)

	# ---- F2 回归:同一种方块放第二块(Godot 会自动改名成『草方块2』)----
	# 换一个和前三块都不重叠的朝向、俯角放浅一点:贴脸放会被"别把人卡进方块"
	# 的兜底挪走或收掉,那测的就不是同名匹配了。
	camera.rotation_degrees = Vector3(-30.0, -95.0, 0.0)
	for i in range(4):
		await physics_frame
	await press("key_1")
	await press("place")
	var grass_all: Array = []
	# 按类型找,不能按名字找:第二块起 Godot 会把同名实例改成内部名
	# "@StaticBody3D@123",名字里根本没有"草方块"这三个字。
	for node in scene.find_children("*", "StaticBody3D", true, false):
		if str(chunk_node.call("_placed_kind", node)) == "grass" \
				and not node.has_meta("gk_cell"):
			grass_all.append(node)
	var two_same_ok: bool = grass_all.size() == 2
	if grass_all.size() == 2:
		var gap: float = (grass_all[0] as Node3D).global_position.distance_to(
			(grass_all[1] as Node3D).global_position)
		two_same_ok = gap > 0.9
	for g in grass_all:
		print("MINECRAFT_DEBUG grass node=", g.name, " kind=",
			chunk_node.call("_placed_kind", g), " pos=",
			(g as Node3D).global_position, " kids=", g.get_child_count())
		for m in g.find_children("*", "MeshInstance3D", true, false):
			print("MINECRAFT_DEBUG   mesh=", m.name, " override=",
				(m as MeshInstance3D).material_override)

	# ---- 分段重建(放置):只重建新方块碰到的 section,耗时逐个有记录 ----
	if chunk_node != null:
		var place_log: Array = chunk_node.call("section_rebuild_log")
		var place_entries := 0
		var place_scoped := true
		for entry in place_log:
			if str(entry.get("reason", "")) != "place":
				continue
			place_entries += 1
			if int(entry.get("us", -1)) < 0:
				place_scoped = false
		place_section_ok = (place_entries >= 1 and place_scoped)
		place_section_detail = "entries=%d scoped=%s" % [place_entries,
			place_scoped]
	print("MINECRAFT_DEBUG place_section=", place_section_ok,
		place_section_detail)

	# ---- 连续贴脸放置:方块不能和玩家碰撞体重叠(会把人卡进方块里)----
	camera.rotation_degrees = Vector3(-89.0, 0.0, 0.0)
	for i in range(4):
		await physics_frame
	await press("key_2")
	await press("place")
	await press("place")
	for i in range(40):
		await physics_frame
	var overlap_ok := true
	for node in scene.find_children("石头方块*", "StaticBody3D", true, false):
		var body := node as Node3D
		var low := body.global_position.y
		if absf(body.global_position.x - player.global_position.x) < 0.8 \
				and absf(body.global_position.z - player.global_position.z) < 0.8 \
				and low + 1.0 > player.global_position.y + 0.1 \
				and low < player.global_position.y + 1.6:
			overlap_ok = false
	# 物品栏那一段预期手上是木头,把选中的物品切回去
	await press("key_4")

	# ---- 放置的方块也受渲染距离影响:挪到渲染距离外必须隐藏 ----
	# (距离要跟着档位走:默认 16 区块 = 256 格,以前写死 120 格还在范围内)
	var cull_ok := false
	if grass_nodes.size() > 0:
		var block := grass_nodes[0] as Node3D
		var home := block.global_position
		block.global_position = home + Vector3(
			float(chunk_node.get("_radius")) + 60.0, 0.0, 0.0)
		for i in range(30):
			await physics_frame
		cull_ok = not block.visible
		block.global_position = home
		for i in range(30):
			await physics_frame

	# ---- 物品栏 ----
	var hotbar_nodes: Array = scene.find_children("物品栏", "Panel", true, false)
	var slot_nodes: Array = scene.find_children("槽图标*", "TextureRect", true,
		false)
	var hud_nodes: Array = scene.find_children("手持文本", "Label", true, false)
	var hud_text := ""
	if hud_nodes.size() > 0:
		hud_text = str(hud_nodes[0].text)
	var hotbar_ok: bool = (hotbar_nodes.size() > 0 and slot_nodes.size() == 9
		and hud_text.begins_with("手持: 木头方块"))
	var icon_ok := false
	if slot_nodes.size() > 0:
		var icon := slot_nodes[0] as TextureRect
		icon_ok = icon.texture != null

	# ---- 原版观感:准星 / 地形贴图 / 方块云 ----
	var cross_nodes: Array = scene.find_children("准星*", "Panel", true, false)
	var cross_ok := cross_nodes.size() >= 2
	# 出生点不再强制是草地:只要求地形用上了 res://art/mc_*.png 贴图。
	# M2a 第二段起地形可视网格在分段节点("段块_*")上,不在列碰撞体里。
	var terrain_node: Array = []
	var terrain_grass: Array = []
	for holder in scene.find_children("分段_*", "Node3D", true, false):
		var texture := first_texture(holder)
		if texture.begins_with("res://art/mc_") and texture.ends_with(".png"):
			if terrain_node.is_empty():
				terrain_node.append(holder)
			if texture.ends_with("mc_grass.png"):
				terrain_grass.append(holder)
				break
	var terrain_textured: bool = terrain_node.size() > 0
	# 放置实例必须和地形共用同一份材质(最近邻过滤),不能是资产自带的线性过滤。
	# 出生点附近可能一格草都没有(沙地 / 裸石),那就只验"贴图和过滤对不对"。
	var placed_mat_ok := false
	if grass_all.size() > 0:
		var placed_mat: Material = first_material(grass_all[0])
		placed_mat_ok = (placed_mat is StandardMaterial3D
			and (placed_mat as StandardMaterial3D).texture_filter
				== BaseMaterial3D.TEXTURE_FILTER_NEAREST)
		var tex := (placed_mat as StandardMaterial3D).albedo_texture
		placed_mat_ok = placed_mat_ok and tex != null \
			and tex.resource_path.ends_with("mc_grass.png")
		if terrain_grass.size() > 0:
			placed_mat_ok = placed_mat_ok \
				and placed_mat == first_material(terrain_grass[0])

	var cloud_nodes: Array = scene.find_children("方块云", "MeshInstance3D",
		true, false)
	var cloud_ok := cloud_nodes.size() > 0
	# ---- 选中方块的黑框(准星指着地面时应出现)----
	camera.rotation_degrees = Vector3(-40.0, 0.0, 0.0)
	for i in range(4):
		await physics_frame
	var outline_nodes: Array = scene.find_children("选中框", "MeshInstance3D",
		true, false)
	var outline_ok: bool = (outline_nodes.size() > 0
		and (outline_nodes[0] as MeshInstance3D).visible)

	# ---- V 切视角 ----
	await press("view_toggle")
	var cam_now: Camera3D = scene.get_viewport().get_camera_3d()
	var third_ok: bool = cam_now != null and str(cam_now.name) == "第三人称相机"
	var model_nodes: Array = player.find_children("Model", "Node3D", true, false)
	var model_shown: bool = model_nodes.size() > 0 and model_nodes[0].visible
	await press("view_toggle")
	var cam_back: Camera3D = scene.get_viewport().get_camera_3d()
	var first_ok: bool = cam_back != null and str(cam_back.name) == "第一人称相机"
	# 第一人称往下看不能看到自己那身蓝模型(用户反馈过)
	var model_hidden: bool = model_nodes.size() > 0 and not model_nodes[0].visible
	var view_ok := third_ok and model_shown and first_ok and model_hidden

	# ---- 相机跟随模型:第三人称下走两步,相机跟玩家的距离必须保持不变 ----
	# (真机踩过:两个『相机_跟随』节点共用一套变量,第二台相机从不被推,
	#  人物走远了画面还停在出生点。)
	await press("view_toggle")
	# 切相机有 0.2 秒的过渡:等它落位再量距离
	for i in range(16):
		await physics_frame
	var p0: Vector3 = player.global_position
	var cam0: Camera3D = scene.get_viewport().get_camera_3d()
	var gap0: float = cam0.global_position.distance_to(p0) if cam0 else 0.0
	# 直接挪 2 米(不用输入:出生点周围可能已经放了方块,走会被挡住)
	player.global_position = p0 + Vector3(0.0, 0.0, 2.0)
	for i in range(6):
		await physics_frame
	var p1: Vector3 = player.global_position
	var cam1: Camera3D = scene.get_viewport().get_camera_3d()
	var gap1: float = cam1.global_position.distance_to(p1) if cam1 else 0.0
	var cam_follow_ok: bool = (cam0 != null and cam1 != null
		and str(cam1.name) == "第三人称相机"
		and p1.distance_to(p0) > 0.5 and absf(gap1 - gap0) < 0.05)
	var cam_follow_detail := " %s/%s d=%.2f gap=%.2f->%.2f" % [
		str(cam0.name) if cam0 else "null", str(cam1.name) if cam1 else "null",
		p1.distance_to(p0), gap0, gap1]
	await press("view_toggle")

	# ---- G 切模式:怪物这轮不攻击,生存 / 创造都不该掉血 ----
	var creative0 := bool(player.get("创造模式"))
	await press("mode_toggle")
	var creative1 := bool(player.get("创造模式"))
	var hp_surv0 := float(player.get("生命"))
	for i in range(120):
		await physics_frame
	var hp_surv1 := float(player.get("生命"))
	var safe_survival := absf(hp_surv1 - hp_surv0) < 0.01
	await press("mode_toggle")
	var creative2 := bool(player.get("创造模式"))
	var hp_creative0 := float(player.get("生命"))
	for i in range(60):
		await physics_frame
	var hp_creative1 := float(player.get("生命"))
	var safe_creative := absf(hp_creative1 - hp_creative0) < 0.01
	var mode_ok := (creative0 and not creative1 and creative2
		and safe_survival and safe_creative)

	# ---- 苹果回血 ----
	camera.rotation_degrees = Vector3(-40.0, 0.0, 0.0)
	for i in range(3):
		await physics_frame
	player.set("生命", 50.0)
	for i in range(3):
		await physics_frame
	await press("key_6")
	var hp0 := float(player.get("生命"))
	await press("place")
	var hp1 := float(player.get("生命"))
	var ate := hp1 > hp0

	# ---- 剑击杀怪物(怪物不追人了:把一只摆到准星前再砍)----
	var monsters: Array = scene.find_children("怪物*", "CharacterBody3D", true, false)
	var monsters_before := monsters.size()
	if monsters.size() > 0:
		# 第两百一十六轮:怪物会被挪到出生保护圈外,原来的"传送到怪物旁边"
		# 可能落在没铺地形的地方,玩家一边掉一边砍就时中时不中。
		# 改成:站在出生点,把怪物摆到准星正前方 2 格(同一高度)再砍。
		player.global_position = spawn_point + Vector3(0.0, 0.5, 0.0)
		for i in range(8):
			await physics_frame
		camera.rotation_degrees = Vector3(0.0, 0.0, 0.0)
		for i in range(4):
			await physics_frame
		var nearest := monsters[0] as Node3D
		nearest.global_position = camera.global_position \
			- camera.global_transform.basis.z * 2.2
		for i in range(4):
			await physics_frame
		# 摆到山坡上时物理会把怪物顶到地表,它的胶囊中心比放下去的位置高;
		# 等它稳定后再把准星对到胶囊中心,不然射线从脚底下擦过去。
		camera.look_at(nearest.global_position + Vector3(0, 1.0, 0))
	for i in range(3):
		await physics_frame
	await press("key_7")
	await press("place")
	for i in range(5):
		await physics_frame
	var monsters_after: int = scene.find_children(
		"怪物*", "CharacterBody3D", true, false).size()
	var sword_ok := monsters_before > 0 and monsters_after < monsters_before

	# ---- 滚轮换物品:序号 +1、名字提示弹出、选中框跟着挪 ----
	await press("key_1")
	for i in range(3):
		await physics_frame
	var slot_before := int(player.get("方块序号"))
	var wheel := InputEventMouseButton.new()
	wheel.button_index = MOUSE_BUTTON_WHEEL_DOWN
	wheel.pressed = true
	wheel.position = root.get_visible_rect().size * 0.5
	Input.parse_input_event(wheel)
	for i in range(6):
		await physics_frame
	var slot_after := int(player.get("方块序号"))
	var hovered: Control = root.gui_get_hovered_control()
	var hover_name := ""
	if hovered != null:
		hover_name = str(hovered.name)
	var hint_nodes: Array = scene.find_children("物品名提示", "Label", true,
		false)
	var select_nodes: Array = scene.find_children("选中框", "Panel", true, false)
	var hint_ok: bool = (hint_nodes.size() > 0 and hint_nodes[0].visible
		and str(hint_nodes[0].text) == "石头方块")
	var frame_ok: bool = (select_nodes.size() > 0
		and absf((select_nodes[0] as Control).position.x - 88.0) < 1.0)
	var wheel_ok: bool = (slot_after == slot_before + 1 and hint_ok
		and frame_ok)
	var wheel_detail := " hover=" + hover_name
	if hint_nodes.size() > 0:
		wheel_detail += " hint=%s/%s" % [hint_nodes[0].visible,
			hint_nodes[0].text]
	if select_nodes.size() > 0:
		wheel_detail += " x=%.1f" % (select_nodes[0] as Control).position.x

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
		var rainy: bool = (world_env.environment.fog_enabled
			and world_env.environment.fog_density >= 0.02)
		var rain_left := float(weather.get("_duration"))
		weather.set_weather(0)
		var clear: bool = (world_env.environment.fog_enabled
			and world_env.environment.fog_density <= 0.02)
		var sun_left := float(weather.get("_duration"))
		weather.set_weather(2)
		var snowy: bool = (world_env.environment.fog_enabled
			and world_env.environment.fog_density >= 0.04)
		# 持续时长:晴天明显比雨雪长(不再一直下);并且到点会自动换天气
		var weather_timed := (sun_left > rain_left + 10.0)
		weather.set_weather(0)
		var before_index := int(weather.get("index"))
		weather.set("_timer", 999.0)
		for i in range(4):
			await physics_frame
		var cycled := int(weather.get("index")) != before_index
		weather_ok = rainy and clear and snowy and weather_timed and cycled

	# ---- 怪物 / 羊的模型跟着碰撞体走 ----
	# 导出后碰撞体在 Colliders/ 下,看得见的网格在 World/Monsters/ 下;
	# 节点图推的是碰撞体,网格必须每帧同步过去(否则只有隐形碰撞体在追人)。
	var follow_ok := true
	var follow_detail := ""
	var moved_bodies := 0
	var spawns := {"怪物1": Vector3(2.5, 1.0, -2.5),
		"怪物2": Vector3(3.5, 1.0, -4.0), "怪物3": Vector3(-2.0, 1.0, -3.5),
		"羊1": Vector3(4.0, 1.0, 1.0), "羊2": Vector3(-4.0, 1.0, 1.5)}
	for body in scene.find_children("*", "CharacterBody3D", true, false):
		var body3 := body as Node3D
		var label := str(body3.name)
		if not (label.begins_with("怪物") or label.begins_with("羊")):
			continue
		var visual: Node3D = scene.get_node_or_null(
			"Monsters/" + label) as Node3D
		if visual == null:
			follow_ok = false
			follow_detail += " 没模型:" + label
			continue
		var gap := visual.global_position.distance_to(body3.global_position)
		if gap > 0.4:
			follow_ok = false
			follow_detail += " %s 偏离 %.2f" % [label, gap]
		if spawns.has(label) \
				and body3.global_position.distance_to(spawns[label]) > 1.0:
			moved_bodies += 1
	# 让一只怪物真的挪一下:网格必须在几帧内跟过去。别依赖羊自己溜达 ——
	# 那是随机的,偶尔一只都没走远,这条检查就会无辜变红。
	var nudged_sync := false
	var first_body: Node3D = null
	for body in scene.find_children("*", "CharacterBody3D", true, false):
		if str(body.name).begins_with("怪物"):
			first_body = body as Node3D
			break
	if first_body != null:
		first_body.global_position.x += 1.6
		for i in range(6):
			await physics_frame
		var nudged_visual: Node3D = scene.get_node_or_null(
			"Monsters/" + str(first_body.name)) as Node3D
		nudged_sync = (nudged_visual != null and nudged_visual.global_position
			.distance_to(first_body.global_position) <= 0.4)
	var follow_sync: bool = follow_ok and (moved_bodies >= 1 or nudged_sync)

	# ---- 羊会自己溜达(给它 5 秒:偶尔会被一格台阶卡住,换方向后能绕出来)----
	var sheep: Node3D = scene.get_node_or_null("Colliders/羊1") as Node3D
	var sheep_from := Vector3.ZERO
	if sheep != null:
		sheep_from = sheep.global_position
	var sheep_moved := false
	for i in range(300):
		await physics_frame
		if sheep != null and sheep.global_position.distance_to(sheep_from) > 0.2:
			sheep_moved = true
			break
	print("MINECRAFT_DEBUG sheep from=", sheep_from, " to=",
		sheep.global_position if sheep != null else Vector3.ZERO,
		" moved=", sheep_moved)

	# ---- R2 回归:改渲染距离只重算范围,不能整屏卸载重建 ----
	# 锚点不能写死『地形_0_0』:出生点(第两百一十六轮)是挑出来的,原点那块
	# 可能根本没铺、也可能刚被挖掉。取离玩家最近的一块地形稳一点。
	var radius_keep := false
	if chunk_node != null:
		var radius_before := int(chunk_node.get("_radius"))
		var anchor: Node = null
		var anchor_away := 1e9
		for tile in scene.find_children("地形_*", "StaticBody3D", true, false):
			var away: float = (tile as Node3D).global_position.distance_to(
				(player as Node3D).global_position)
			if away < anchor_away:
				anchor_away = away
				anchor = tile
		chunk_node.call("set_radius", 24)
		for i in range(20):
			await process_frame
		var anchor_after: Node = scene.find_child(str(anchor.name), true, false) \
			if anchor != null else null
		radius_keep = (anchor != null and anchor == anchor_after
			and int(chunk_node.get("_radius")) == 24)
		chunk_node.call("set_radius", radius_before)
		for i in range(20):
			await process_frame
	# 雾浓度要跟着渲染距离变淡,否则拉远视距中段会被雾糊掉。
	# 第两百零八轮改成原版那种"渲染距离雾":深度雾,终点 = 渲染距离。
	var fog_ok := false
	var env_nodes: Array = scene.find_children("*", "WorldEnvironment", true, false)
	if env_nodes.size() > 0:
		var env := (env_nodes[0] as WorldEnvironment).environment
		fog_ok = (env != null and env.fog_enabled
			and env.fog_mode == Environment.FOG_MODE_DEPTH
			and env.fog_depth_end >= 100.0
			and env.fog_depth_begin > 0.0
			and env.fog_depth_begin < env.fog_depth_end
			and not env.glow_enabled and not env.volumetric_fog_enabled)
		print("MINECRAFT_DEBUG fog mode=", env.fog_mode, " begin=",
			env.fog_depth_begin, " end=", env.fog_depth_end)

	# ---- 无限地形:走远之后重新铺、原点卸载 ----
	# 一帧只建 1 列(一帧建 2 列会偶发长帧),所以这里多等一会儿让新区铺满。
	player.global_position = Vector3(60.0, 1.2, 0.0)
	for i in range(300):
		await physics_frame
	var tiles_far := tiles(scene)
	var old_tile: Array = scene.find_children(
		"地形_0_0", "StaticBody3D", true, false)
	var infinite_ok := tiles_far >= 200 and old_tile.size() == 0

	# ---- M2a 第三段:卸载迟滞(加载边界外扩 2 区块)----
	# 站到加载边界附近,在迟滞区里来回跨一格区块边:被保留的远景不该重建。
	# 挪人时每 5 帧钉一次高度:新区块还没铺到脚下时不能让他掉出世界。
	var hyst_ok := false
	var hyst_detail := "n/a"
	var deferred_after_out := 0
	var deferred_released := -1
	var rebuild_in_band := -1
	if chunk_node != null:
		var band_radius := float(chunk_node.get("_radius"))
		var band_cell := Vector2i(int(band_radius) - 16, 0)
		for i in range(9000):
			await process_frame
			if i % 5 == 0:
				player.global_position = ground_point(chunk_node, band_cell)
			if bool(chunk_node.call("is_ready")):
				break
		var rebuild0 := int(chunk_node.call("chunk_rebuild_count"))
		for step in range(3):
			player.global_position = ground_point(chunk_node,
				band_cell + Vector2i(16, 0))
			for i in range(100):
				await process_frame
			deferred_after_out = int(chunk_node.call("unload_deferred_chunks"))
			player.global_position = ground_point(chunk_node, band_cell)
			for i in range(100):
				await process_frame
		rebuild_in_band = int(chunk_node.call("chunk_rebuild_count")) - rebuild0
		# 走出去:先前多留的区块必须真的释放(检查出生点方向最外圈那一块)
		var far_before_release := bool(chunk_node.call(
			"far_loaded", Vector2i(-1, 0)))
		player.global_position = ground_point(chunk_node,
			band_cell + Vector2i(160, 0))
		for i in range(80):
			await process_frame
		deferred_released = int(chunk_node.call("unload_deferred_chunks"))
		var released := (far_before_release
			and not bool(chunk_node.call("far_loaded", Vector2i(-1, 0))))
		hyst_ok = (rebuild_in_band == 0 and deferred_after_out > 0 and released)
		hyst_detail = "band_rebuild=%d deferred=%d released=%s deferred_after=%d radius=%.0f" % [
			rebuild_in_band, deferred_after_out, str(released),
			deferred_released, band_radius]
		print("MINECRAFT_DEBUG hysteresis=", hyst_ok, " ", hyst_detail)
		# 回出生点:后面存档 / 摔落用例还在那片地
		for i in range(9000):
			await process_frame
			if i % 5 == 0:
				player.global_position = spawn_point + Vector3(0.0, 0.8, 0.0)
			if bool(chunk_node.call("is_ready")):
				break

	# ---- 设置:画面 / 控制面板能打开,拖灵敏度真的写进玩家变量 ----
	await press("ui_cancel")
	var settings_buttons: Array = scene.find_children(
		"暂停设置", "Button", true, false)
	var settings_open := false
	if settings_buttons.size() > 0:
		settings_buttons[0].pressed.emit()
		for i in range(3):
			await process_frame
		var settings_panel: Array = scene.find_children(
			"设置面板", "Control", true, false)
		settings_open = (settings_panel.size() > 0
			and settings_panel[0].visible)
	var sens_nodes: Array = scene.find_children(
		"灵敏度滑条", "HSlider", true, false)
	var sens_ok := false
	if sens_nodes.size() > 0:
		(sens_nodes[0] as HSlider).value = 2.0
		for i in range(3):
			await process_frame
		sens_ok = absf(float(player.get("灵敏度x")) - 2.0) < 0.01
	var settings_close: Array = scene.find_children(
		"设置关闭", "Button", true, false)
	if settings_close.size() > 0:
		settings_close[0].pressed.emit()
	for i in range(3):
		await process_frame

	# ---- 命名存档:存(暂停菜单)→ 读档列表(主菜单按钮)→ 人物 / 世界改动回来 ----
	# 摆一个确定的状态:血量 42、站在出生点;前面已经挖掉过地形、放过方块。
	spawn_point = Vector3(float(player.get("出生点x")),
		float(player.get("出生点y")), float(player.get("出生点z")))
	var save_file := "user://saves/探针存档.json"
	if FileAccess.file_exists(save_file):
		DirAccess.remove_absolute(ProjectSettings.globalize_path(save_file))
	player.global_position = spawn_point + Vector3(0.0, 0.6, 0.0)
	player.set("生命", 42.0)
	for i in range(20):
		await physics_frame
	var saved_pos: Vector3 = player.global_position
	# 先往一个箱子里塞 9 个苹果,验证存档闭环带上箱子内容(第两百一十三轮)
	var chest_probe: Node = null
	for child in player.get_children():
		if child.has_method("export_chests"):
			chest_probe = child
	if chest_probe != null:
		var probe_chest = chest_probe.call("_new_chest")
		probe_chest[0] = {"kind": "apple", "count": 9}
		var probe_chests: Dictionary = chest_probe.get("_chests")
		probe_chests["7,2,7"] = probe_chest
	await press("ui_cancel")
	var save_buttons: Array = scene.find_children("保存游戏", "Button", true, false)
	var save_open := false
	if save_buttons.size() > 0:
		save_buttons[0].pressed.emit()
		for i in range(3):
			await process_frame
		var panel_nodes: Array = scene.find_children(
			"保存面板", "Control", true, false)
		save_open = panel_nodes.size() > 0 and panel_nodes[0].visible
	var input_nodes: Array = scene.find_children(
		"存档名输入", "LineEdit", true, false)
	if input_nodes.size() > 0:
		input_nodes[0].text = "探针存档"
	var confirm_nodes: Array = scene.find_children(
		"保存确定", "Button", true, false)
	if confirm_nodes.size() > 0:
		confirm_nodes[0].pressed.emit()
	for i in range(5):
		await physics_frame
	var saved := FileAccess.file_exists(save_file)
	var saved_player := false
	var saved_world := false
	var saved_settings := false
	var saved_inventory := false
	var saved_chests := false
	if saved:
		var handle := FileAccess.open(save_file, FileAccess.READ)
		if handle != null:
			var data = JSON.parse_string(handle.get_as_text())
			handle.close()
			if data is Dictionary:
				var info: Dictionary = data.get("player", {})
				var world: Dictionary = data.get("world", {})
				var settings: Dictionary = data.get("world_settings", {})
				saved_player = absf(float(info.get("health", 0.0)) - 42.0) < 0.01
				saved_world = (world.get("removed", {}).size() >= 1
					and world.get("placed", {}).size() >= 1)
				var inv_data = data.get("inventory", null)
				saved_inventory = (inv_data is Array and inv_data.size() == 36
					and inv_data[0] is Array and str(inv_data[0][0]) == "grass"
					and int(inv_data[0][1]) > 0)
				var chest_data = data.get("chests", null)
				saved_chests = (chest_data is Dictionary
					and chest_data.has("7,2,7")
					and chest_data["7,2,7"] is Array
					and chest_data["7,2,7"][0] is Array
					and str(chest_data["7,2,7"][0][0]) == "apple"
					and int(chest_data["7,2,7"][0][1]) == 9)
				saved_settings = (settings.has("seed")
					and settings.has("terrain") and settings.has("size")
					and settings.has("vegetation") and settings.has("time")
					and settings.has("radius")
					and int(settings.get("radius", 0)) >= 1
					# M2a:新档必须带高度字段(format 3)
					and int(data.get("format", 0)) == 3
					and int(settings.get("world_height", 0)) == 64
					and int(settings.get("sea_level", 0)) == 32
					and int(settings.get("section_height", 0)) == 16)
				# 两块同类方块都要进存档(第二块的名字是『草方块2』)
				var grass_saved := 0
				var placed_data = world.get("placed", {})
				if placed_data is Dictionary:
					for key in placed_data.keys():
						if str(placed_data[key]) == "grass":
							grass_saved += 1
				saved_world = saved_world and grass_saved >= 2
				print("MINECRAFT_DEBUG grass_saved=", grass_saved,
					" placed=", world.get("placed", {}).size(),
					" removed=", world.get("removed", {}).size())
	var resumed_save: bool = not paused
	# 有存档之后菜单要翻面:主按钮『继续游戏』,读取存档从置灰变可用
	var menu_saved_ok := false
	var save_script: Node = null
	for child in player.get_children():
		if child.has_method("open_menu_primary"):
			save_script = child
	if save_script != null and menu_nodes.size() > 0:
		save_script.call("_refresh_menu")
		for i in range(2):
			await process_frame
		var menu: Panel = menu_nodes[0]
		var primary: Node = menu.find_child("继续游戏", true, false)
		var create_node: Node = menu.find_child("新建世界", true, false)
		var read_node: Node = menu.find_child("读取存档", true, false)
		menu_saved_ok = (primary != null
			and str((primary as Button).text) == "继续游戏"
			and not (primary as Button).disabled
			and create_node != null
			and absf((create_node as Control).position.y - 300.0) < 0.5
			and str((create_node as Button).text) == "新建世界"
			and read_node != null
			and not (read_node as Button).disabled
			and absf((read_node as Control).position.y - 230.0) < 0.5)
	# 主菜单的『读取存档』→ 列表里的第一条(刚存的那个)
	var read_buttons: Array = scene.find_children(
		"读取存档", "Button", true, false)
	if read_buttons.size() > 0:
		read_buttons[0].pressed.emit()
	for i in range(3):
		await process_frame
	var entry_nodes: Array = scene.find_children(
		"存档项*", "Button", true, false)
	var list_ok := entry_nodes.size() >= 1
	if entry_nodes.size() > 0:
		entry_nodes[0].pressed.emit()
	# 读档后同样等近景铺好再解除暂停(『正在生成世界…』)
	for i in range(400):
		await process_frame
		if not paused:
			break
	var loaded_health := float(player.get("生命"))
	var loaded_pos: Vector3 = player.global_position
	var resumed_load: bool = not paused
	var chunk_script: Node = null
	for child in player.get_children():
		if child.has_method("save_state"):
			chunk_script = child
	var world_ok := false
	var loaded_grass := 0
	if chunk_script != null:
		world_ok = (chunk_script.get("_removed").size() >= 1
			and chunk_script.get("_placed").size() >= 1)
	# 两块同种草方块读档后都要重建出来(按 gk_placed 标记数,不看节点名)
	for body in scene.find_children("*", "StaticBody3D", true, false):
		if bool(body.get_meta("gk_placed", false)) \
				and first_texture(body).ends_with("mc_grass.png"):
			loaded_grass += 1
	var two_same_loaded: bool = loaded_grass >= 2
	for i in range(300):
		await process_frame
	var tiles_loaded := tiles(scene)
	var flat_gap := Vector2(loaded_pos.x - saved_pos.x,
		loaded_pos.z - saved_pos.z).length()
	# 背包也要跟着读档回来(36 格 + 第 1 格还是草方块)
	var inv_node: Node = null
	for child in player.get_children():
		if child.has_method("export_slots"):
			inv_node = child
	var loaded_inventory := false
	var loaded_chests := false
	if inv_node != null:
		var loaded_slots: Array = inv_node.call("export_slots")
		loaded_inventory = (loaded_slots.size() == 36
			and loaded_slots[0] is Array and int(loaded_slots[0][1]) > 0)
		var loaded_chest_data: Dictionary = inv_node.call("export_chests")
		loaded_chests = (loaded_chest_data.has("7,2,7")
			and loaded_chest_data["7,2,7"][0] is Array
			and str(loaded_chest_data["7,2,7"][0][0]) == "apple"
			and int(loaded_chest_data["7,2,7"][0][1]) == 9)
	var load_ok: bool = (save_open and saved and saved_player and saved_world
		and saved_settings and saved_chests and resumed_save and list_ok
		and resumed_load and world_ok and saved_inventory and loaded_inventory
		and loaded_chests
		and absf(loaded_health - 42.0) < 0.01 and flat_gap < 0.6
		and tiles_loaded >= 200)
	print("MINECRAFT_DEBUG save_chests=", saved_chests,
		" loaded_chests=", loaded_chests)

	# ---- 新建世界流程(真点面板):名称 / 种子 / 创造模式 → 进世界 ----
	var create_flow := false
	if create_nodes.size() > 0 and chunk_script != null:
		create_nodes[0].pressed.emit()
		for i in range(2):
			await process_frame
		var name_inputs: Array = scene.find_children(
			"新世界名输入", "LineEdit", true, false)
		var seed_inputs: Array = scene.find_children(
			"新种子输入", "LineEdit", true, false)
		if name_inputs.size() > 0:
			name_inputs[0].text = "探针新世界"
		if seed_inputs.size() > 0:
			seed_inputs[0].text = "abc123"
		var creative_buttons: Array = scene.find_children(
			"创造模式", "Button", true, false)
		if creative_buttons.size() > 0:
			creative_buttons[0].pressed.emit()
		# 出生点现在由脚本挑,传送发生在加载屏内部的两次 process_frame 之后;
		# 只等"不暂停"会读到传送前的旧坐标,所以要多等到玩家真的落在出生点。
		for i in range(400):
			await process_frame
			if not paused:
				var here := Vector3(float(player.get("出生点x")),
					float(player.get("出生点y")), float(player.get("出生点z")))
				if player.global_position.distance_to(here) < 2.0:
					break
		create_flow = (not paused
			and bool(player.get("创造模式"))
			and str(player.get("世界名")) == "探针新世界"
			and int(chunk_script.get("_world_seed")) != 0
			and player.global_position.distance_to(Vector3(
				float(player.get("出生点x")), float(player.get("出生点y")),
				float(player.get("出生点z")))) < 2.0)
		# 新世界要清空箱子（不然上个世界的箱子内容会串过来）
		if inv_node != null:
			var fresh_chests: Dictionary = inv_node.call("export_chests")
			create_flow = create_flow and fresh_chests.is_empty()

	# ---- 背包(第两百零九轮):36 格 / 开关鼠标 / 拾起放下 / 悬停名 ----
	var inv_ok := false
	var inv_pick_ok := false
	var inv_spend_ok := false
	var inv_open_ok := false
	var inv_move_ok := false
	var inv_hover_ok := false
	var inv_close_ok := false
	if inv_node != null:
		var cells: Array = scene.find_children("背包格*", "Panel", true, false)
		inv_ok = cells.size() == 36
		# 挖脚下一块 → 背包总数 +1(出生点地表可能是草 / 沙 / 石,不写死种类)
		camera.rotation_degrees = Vector3(-89.0, 0.0, 0.0)
		for i in range(4):
			await physics_frame
		var pick_before := inv_total(inv_node)
		await press("mine")
		for i in range(4):
			await process_frame
		inv_pick_ok = inv_total(inv_node) == pick_before + 1
		# 生存模式放一块石头 → 背包石头 -1(创造模式不扣)
		camera.rotation_degrees = Vector3(-55.0, 0.0, 0.0)
		for i in range(4):
			await physics_frame
		await press("mode_toggle")
		await press("key_2")
		for i in range(3):
			await physics_frame
		# 放置消耗由节点图在创建实例前调 consume_current();这里直接验背包侧
		# (真实放置的占位检查把玩家 / 羊也算进去了,固定机位可能正好被羊挡住)
		var stone_before := int(inv_node.call("total_of", "stone"))
		inv_node.call("consume_current")
		for i in range(3):
			await process_frame
		inv_spend_ok = int(inv_node.call("total_of", "stone")) == stone_before - 1
		await press("mode_toggle")
		for i in range(3):
			await physics_frame
		# I 开背包:世界暂停 + 鼠标放出来
		await _tap_key(KEY_I, 3)
		for i in range(8):
			await process_frame
		var panels: Array = scene.find_children("背包界面", "Control", true, false)
		inv_open_ok = (panels.size() > 0 and panels[0].visible and paused
			and Input.mouse_mode != Input.MOUSE_MODE_CAPTURED)
		# 点第 1 格拿起,再点第 36 格放下;悬停显示物品名
		cells = scene.find_children("背包格*", "Panel", true, false)
		if cells.size() == 36:
			var click := InputEventMouseButton.new()
			click.button_index = MOUSE_BUTTON_LEFT
			click.pressed = true
			cells[0].emit_signal("gui_input", click)
			cells[35].emit_signal("gui_input", click)
			cells[35].emit_signal("mouse_entered")
			for i in range(2):
				await process_frame
			var slots: Array = inv_node.call("export_slots")
			inv_move_ok = (slots[0] == null and slots[35] is Array
				and str(slots[35][0]) == "grass" and int(slots[35][1]) == 64)
			var hover_nodes: Array = scene.find_children(
				"背包悬停名", "Label", true, false)
			inv_hover_ok = (hover_nodes.size() > 0
				and str(hover_nodes[0].text).begins_with("草方块"))
		# I 关背包:继续跑 + 鼠标锁回去
		await _tap_key(KEY_I, 3)
		for i in range(10):
			await process_frame
		inv_close_ok = (panels.size() > 0 and not panels[0].visible and not paused
			and save_script != null
			and int(save_script.get("_last_mouse_mode")) == 2)

	# ---- 合成(第两百一十四轮):3×3 格子 / 点料 / 产物进背包 / 缺料不出货 ----
	var craft_ok := false
	var craft_gate_ok := false
	var craft_close_ok := false
	var craft_detail := ""
	if inv_node != null:
		inv_node.call("add_item", "wood", 3)
		var wood_before := int(inv_node.call("total_of", "wood"))
		var plank_before := int(inv_node.call("total_of", "plank"))
		# 合成不占独立键:背包标题栏的『合成』按钮 / 右键工作台都能开
		await _tap_key(KEY_I, 3)
		for i in range(4):
			await process_frame
		var open_craft_buttons: Array = scene.find_children("打开合成", "Button", true, false)
		if open_craft_buttons.size() > 0:
			open_craft_buttons[0].emit_signal("pressed")
		for i in range(8):
			await process_frame
		var craft_nodes: Array = scene.find_children("合成界面", "Control", true, false)
		var craft_open_ok: bool = (craft_nodes.size() > 0
			and craft_nodes[0].visible and paused)
		var craft_cells: Array = scene.find_children("合成格*", "Panel", true, false)
		var result_cell: Node = scene.find_child("合成产物", true, false)
		if craft_open_ok and craft_cells.size() == 9 and result_cell != null:
			var craft_slots: Array = inv_node.get("_craft_inputs")
			var live: Array = inv_node.get("_slots")
			var wood_index := -1
			for i in range(live.size()):
				if live[i] is Dictionary and str(live[i]["kind"]) == "wood":
					wood_index = i
					break
			if wood_index >= 0:
				# 背包里点起木头 -> 放进第一格 -> 产物格出现木板
				inv_node.call("_click_slot", wood_index)
				inv_node.call("_click_container", craft_slots, 0)
				inv_node.call("_refresh_all")
				for i in range(4):
					await process_frame
				var match_ok: bool = not (inv_node.get("_craft_match") as Dictionary).is_empty()
				# 点产物拿到手里,再放进背包空格
				inv_node.call("_take_craft_result")
				var empty_index := -1
				for i in range(live.size()):
					if live[i] == null:
						empty_index = i
						break
				if empty_index >= 0:
					inv_node.call("_click_slot", empty_index)
				# 先把材料退回背包再对总数:放在格子里时 total_of 只数背包
				inv_node.call("_return_craft_inputs")
				inv_node.call("_refresh_craft")
				craft_ok = (match_ok
					and int(inv_node.call("total_of", "wood")) == wood_before - 1
					and int(inv_node.call("total_of", "plank")) == plank_before + 4)
				# 缺料闸门:格子清空后产物格必须变空
				craft_gate_ok = bool(
					(inv_node.get("_craft_match") as Dictionary).is_empty())
				craft_detail = "wood %d->%d plank %d->%d match=%s gate=%s" % [
					wood_before, int(inv_node.call("total_of", "wood")),
					plank_before, int(inv_node.call("total_of", "plank")),
					match_ok, craft_gate_ok]
		inv_node.call("close_craft")
		for i in range(8):
			await process_frame
		craft_close_ok = (not (craft_nodes[0] as Control).visible and not paused)

	# ---- 箱子(第两百一十三轮):放置 / 识别 / 27 格存取 / 关掉 ----
	var chest_place_ok := false
	var chest_open_ok := false
	var chest_store_ok := false
	var chest_take_ok := false
	var chest_close_ok := false
	var chest_reload_ok := false
	var chest_hover_ok := false
	var chest_drag_ok := false
	var chest_detail := ""
	if inv_node != null and chunk_script != null:
		# 站到地形脚本选中的出生点,朝下放一个箱子
		spawn_point = Vector3(float(player.get("出生点x")),
			float(player.get("出生点y")), float(player.get("出生点z")))
		player.global_position = spawn_point + Vector3(0.0, 1.0, 0.0)
		player.velocity = Vector3.ZERO
		for i in range(30):
			await physics_frame
		inv_node.call("add_item", "chest", 1)
		var chest_slot := -1
		var live_slots: Array = inv_node.get("_slots")
		for i in range(9):
			if live_slots[i] is Dictionary and str(live_slots[i]["kind"]) == "chest":
				chest_slot = i
				break
		if chest_slot < 0:
			# 背包可能已经被前面的用例塞满:直接占一格快捷栏,只测"放置/开箱"
			for i in range(9):
				if live_slots[i] == null:
					chest_slot = i
					break
			if chest_slot < 0:
				chest_slot = 0
			live_slots[chest_slot] = {"kind": "chest", "count": 1}
			inv_node.call("_refresh_inventory")
		if chest_slot >= 0 and chest_slot < 9:
			inv_node.call("_select", chest_slot)
			camera.rotation_degrees = Vector3(-55.0, 0.0, 0.0)
			for i in range(4):
				await physics_frame
			await press("place")
			for i in range(10):
				await physics_frame
			var chest_body: Node3D = null
			for node in scene.find_children("*", "StaticBody3D", true, false):
				if node.find_child("箱子_源", true, false) != null:
					chest_body = node
					break
			chest_place_ok = chest_body != null
			if chest_body != null:
				var detected = inv_node.call("_chest_node_of", chest_body)
				var key := str(inv_node.call("chest_key_of", chest_body))
				var grass_total := int(inv_node.call("total_of", "grass"))
				# 走真实路径:把相机对准箱子,右键 -> 区块脚本 can_place_here
				# 会先调 try_open_chest(没有单独的开箱键)
				player.global_position = chest_body.global_position + Vector3(0.0, 1.0, 2.2)
				player.velocity = Vector3.ZERO
				for i in range(8):
					await physics_frame
				camera.look_at(chest_body.global_position + Vector3(0.0, 0.5, 0.0))
				var opened := bool(inv_node.call("try_open_chest"))
				for i in range(4):
					await process_frame
				chest_open_ok = (opened and detected != null and paused
					and scene.find_children("箱子格*", "Panel", true, false).size() == 27)
				var inv_cells: Array = scene.find_children("背包格*", "Panel", true, false)
				var chest_cells: Array = scene.find_children("箱子格*", "Panel", true, false)
				# 悬停高亮:鼠标压上去换一套亮边框样式
				if chest_cells.size() > 0:
					chest_cells[0].emit_signal("mouse_entered")
					chest_hover_ok = chest_cells[0].get_theme_stylebox("panel") is StyleBoxFlat
					chest_cells[0].emit_signal("mouse_exited")
				var click2 := InputEventMouseButton.new()
				click2.button_index = MOUSE_BUTTON_LEFT
				click2.pressed = true
				# 背包第 36 格(草方块 x64)点进箱子第 1 格
				inv_cells[35].emit_signal("gui_input", click2)
				chest_cells[0].emit_signal("gui_input", click2)
				for i in range(2):
					await process_frame
				var chests: Dictionary = inv_node.call("export_chests")
				chest_store_ok = (chests.has(key)
					and chests[key][0] is Array
					and str(chests[key][0][0]) == "grass")
				# 再点回来:箱子第 1 格 -> 背包第 36 格
				chest_cells[0].emit_signal("gui_input", click2)
				inv_cells[35].emit_signal("gui_input", click2)
				for i in range(2):
					await process_frame
				var chests2: Dictionary = inv_node.call("export_chests")
				chest_take_ok = (chests2.has(key) and chests2[key][0] == null
					and int(inv_node.call("total_of", "grass")) == grass_total)
				# 拖拽:按下拿起,松手落在另一边,不用第二次点击
				var drag_down := InputEventMouseButton.new()
				drag_down.button_index = MOUSE_BUTTON_LEFT
				drag_down.pressed = true
				var drag_up := InputEventMouseButton.new()
				drag_up.button_index = MOUSE_BUTTON_LEFT
				drag_up.pressed = false
				inv_cells[35].emit_signal("gui_input", drag_down)
				chest_cells[1].emit_signal("gui_input", drag_up)
				for i in range(2):
					await process_frame
				var chests_drag: Dictionary = inv_node.call("export_chests")
				chest_drag_ok = (chests_drag.has(key)
					and chests_drag[key][1] is Array
					and str(chests_drag[key][1][0]) == "grass")
				# 再拖回背包,恢复成"草方块在背包"的状态
				chest_cells[1].emit_signal("gui_input", drag_down)
				inv_cells[35].emit_signal("gui_input", drag_up)
				for i in range(2):
					await process_frame
				# 再放回箱子里,做一次"保存 -> 清空 -> 读档"的闭环
				inv_cells[35].emit_signal("gui_input", click2)
				chest_cells[0].emit_signal("gui_input", click2)
				for i in range(2):
					await process_frame
				inv_node.call("close_chest")
				for i in range(4):
					await process_frame
				var saved_chest := bool(save_script.call("save_game", "探针箱子"))
				inv_node.call("import_chests", {})
				var loaded_chest := bool(save_script.call("load_game", "探针箱子.json"))
				for i in range(20):
					await process_frame
				var rebuilt: Node3D = null
				for node in scene.find_children("*", "StaticBody3D", true, false):
					if str(node.get_meta("gk_kind", "")) == "chest":
						rebuilt = node
						break
				var detected2 = inv_node.call("_chest_node_of", rebuilt)
				var key2 := str(inv_node.call("chest_key_of", rebuilt))
				var chests3: Dictionary = inv_node.call("export_chests")
				chest_reload_ok = (saved_chest and loaded_chest and rebuilt != null
					and detected2 != null and key2 == key
					and chests3.has(key2) and chests3[key2][0] is Array
					and str(chests3[key2][0][0]) == "grass")
				var chest_panels: Array = scene.find_children("箱子面板", "Panel", true, false)
				chest_close_ok = (not paused
					and chest_panels.size() > 0 and not chest_panels[0].visible)
				chest_detail = "key=%s open=%s store=%s take=%s drag=%s reload=%s %s->%s" % [
					key, chest_open_ok, chest_store_ok, chest_take_ok,
					chest_drag_ok, chest_reload_ok, key, key2]

	# ---- 工作台(第两百一十四轮):右键工作台开 3×3 合成界面 ----
	var table_place_ok := false
	var table_open_ok := false
	var table_detail := ""
	if inv_node != null and chunk_script != null:
		# 箱子放在 -Z 方向,这里转 90° 朝 -X 放工作台,免得两格打架
		player.global_position = spawn_point + Vector3(0.0, 1.0, 0.0)
		player.velocity = Vector3.ZERO
		for i in range(20):
			await physics_frame
		inv_node.call("add_item", "table", 1)
		var table_slot := -1
		var slots_now: Array = inv_node.get("_slots")
		for i in range(9):
			if slots_now[i] is Dictionary and str(slots_now[i]["kind"]) == "table":
				table_slot = i
				break
		if table_slot < 0:
			for i in range(9):
				if slots_now[i] == null:
					table_slot = i
					break
			if table_slot < 0:
				table_slot = 0
			slots_now[table_slot] = {"kind": "table", "count": 1}
			inv_node.call("_refresh_inventory")
		if table_slot >= 0:
			# 走数字键那条路径:只改 方块序号 不会重算手持方块
			# (_last_index 没变),会继续拿着上一格的箱子。
			inv_node.call("_select", table_slot)
			camera.rotation_degrees = Vector3(-55.0, 90.0, 0.0)
			for i in range(4):
				await physics_frame
			await press("place")
			for i in range(10):
				await physics_frame
			var table_body: Node3D = null
			for node in scene.find_children("*", "StaticBody3D", true, false):
				if node.find_child("工作台_源", true, false) != null:
					table_body = node
					break
			table_place_ok = table_body != null
			if table_body != null:
				player.global_position = table_body.global_position + Vector3(0.0, 1.0, 2.2)
				player.velocity = Vector3.ZERO
				for i in range(8):
					await physics_frame
				camera.look_at(table_body.global_position + Vector3(0.0, 0.5, 0.0))
				var opened_craft := bool(inv_node.call("try_open_craft"))
				for i in range(4):
					await process_frame
				var craft_cells2: Array = scene.find_children("合成格*", "Panel", true, false)
				table_open_ok = (opened_craft and paused and craft_cells2.size() == 9)
				inv_node.call("close_craft")
				for i in range(4):
					await process_frame
			table_detail = "place=%s open=%s" % [table_place_ok, table_open_ok]

	# ---- 地形噪声:每一列按世界坐标插值,不再是 3×3 格的台阶复制 ----
	var noise_ok := false
	var noise_detail := ""
	if chunk_script != null:
		var distinct := {}
		var inside_group_differs := false
		for z in range(30, 90):
			for x in range(30, 90):
				var h := int(chunk_script.call("_height_at", Vector2i(x, z)))
				distinct[h] = true
				if x % 3 != 2:
					if h != int(chunk_script.call("_height_at",
							Vector2i(x + 1, z))):
						inside_group_differs = true
		# 树林也不能排成 3 的倍数网格(旧写法就是这么排的)
		var off_grid_tree := false
		for z in range(50, 95):
			for x in range(50, 95):
				if (x % 3 != 0 or z % 3 != 0) \
						and bool(chunk_script.call("_has_tree", Vector2i(x, z))):
					off_grid_tree = true
		# 成片林地:8×8 一块统计树数,必须同时有"密林块"和"空地块"
		var dense_blocks := 0
		var empty_blocks := 0
		var total_trees := 0
		for bz in range(8):
			for bx in range(8):
				var count := 0
				for z2 in range(bz * 8, bz * 8 + 8):
					for x2 in range(bx * 8, bx * 8 + 8):
						if bool(chunk_script.call("_has_tree",
								Vector2i(x2 + 40, z2 + 40))):
							count += 1
				total_trees += count
				if count >= 3:
					dense_blocks += 1
				elif count == 0:
					empty_blocks += 1
		var forest_ok := (dense_blocks >= 2 and empty_blocks >= 8
			and total_trees > 0)
		# 地形分区(M2b):按世界高度 64 重定后,±320 步长 8 的采样要落在
		# p50≈38 / p90≈48 / max≤60;低地(<海平面 32)要有成片水面。
		var heights: Array = []
		var sea_cols := 0
		var zone_samples := 0
		var hmax := 0
		# M2a:高度上限不许再写死 24,必须读区块脚本常量;
		# M2b:地表真正长到 60 层,世界容量仍是 64。
		var world_height := int(chunk_script.call("world_height"))
		var sea_level := int(chunk_script.call("sea_level"))
		var section_height := int(chunk_script.call("section_height"))
		var terrain_max := int(chunk_script.call("terrain_max"))
		var height_ok := (world_height == 64 and sea_level == 32
			and section_height == 16 and terrain_max >= 56
			and terrain_max < world_height)
		for z3 in range(-320, 320, 8):
			for x3 in range(-320, 320, 8):
				var ht := int(chunk_script.call("_height_at", Vector2i(x3, z3)))
				zone_samples += 1
				heights.append(ht)
				hmax = maxi(hmax, ht)
				if ht < sea_level:
					sea_cols += 1
		heights.sort()
		var h_p50 := int(heights[int(round(float(heights.size() - 1) * 0.5))])
		var h_p90 := int(heights[int(round(float(heights.size() - 1) * 0.9))])
		var water_pct := sea_cols * 100 / zone_samples
		var zone_ok := (h_p50 >= 35 and h_p50 <= 41
			and h_p90 >= 45 and h_p90 <= 51
			and hmax <= terrain_max and sea_cols > 0
			and water_pct >= 5 and water_pct <= 35)
		noise_ok = (distinct.size() >= 2 and inside_group_differs
			and off_grid_tree and forest_ok and zone_ok and height_ok)
		noise_detail = ("h=%d group=%s tree=%s forest=%s dense=%d empty=%d "
			+ "n=%d zone=%s hmax=%d p50=%d p90=%d water=%d%% height=%s") % [
			distinct.size(), inside_group_differs, off_grid_tree, forest_ok,
			dense_blocks, empty_blocks, total_trees, zone_ok, hmax,
			h_p50, h_p90, water_pct, height_ok]

	# ---- 静态水(M2b):水面 32 / 低地灌水 / 游泳 / 溺水 / 水下雾 ----
	var water_ok := false
	var swim_ok := false
	var sink_ok := false
	var drown_ok := false
	var water_fog_ok := false
	var water_detail := ""
	var swim_detail := ""
	var sink_detail := ""
	var drown_detail := ""
	var water_fog_detail := ""
	if chunk_script != null and camera != null:
		var sea := int(chunk_script.call("sea_level"))
		# 找离原点最近的深水格子(海平面以下 3 格以上),再把人挪过去
		var water_cell := Vector2i(1 << 30, 1 << 30)
		var water_best := 1 << 30
		for z in range(-128, 129, 4):
			for x in range(-128, 129, 4):
				var cell := Vector2i(x, z)
				if int(chunk_script.call("_height_at", cell)) > sea - 3:
					continue
				if x * x + z * z < water_best:
					water_best = x * x + z * z
					water_cell = cell
		if water_cell.x < (1 << 29):
			var home := Vector3(float(player.get("出生点x")),
				float(player.get("出生点y")), float(player.get("出生点z")))
			var water_point := Vector3(water_cell.x + 0.5, float(sea) - 2.0,
				water_cell.y + 0.5)
			# 挪到水里,等这一片地形 / 水面建好(每 20 帧钉一次位置,别沉下去)
			player.global_position = water_point
			player.velocity = Vector3.ZERO
			for i in range(1500):
				await process_frame
				if i % 20 == 0:
					player.global_position = water_point
				if bool(chunk_script.call("is_ready")):
					break
			var underwater := bool(chunk_script.call("is_underwater_point",
				water_point))
			var above_water := bool(chunk_script.call("is_underwater_point",
				Vector3(water_cell.x + 0.5, float(sea) + 1.0,
					water_cell.y + 0.5)))
			var near_water: Array = scene.find_children("段水",
				"MeshInstance3D", true, false)
			var far_water: Array = scene.find_children("远景水面",
				"MultiMeshInstance3D", true, false)
			# 水面只能是一张水平面(逐顶点局部 y = 0),不能出现垂直水墙
			var flat_ok := near_water.size() > 0
			for item in near_water:
				var mesh_node := item as MeshInstance3D
				if mesh_node.mesh == null \
						or mesh_node.mesh.get_surface_count() == 0:
					flat_ok = false
					continue
				if absf(mesh_node.global_position.y - float(sea)) > 0.01:
					flat_ok = false
				var arrays := mesh_node.mesh.surface_get_arrays(0)
				if arrays.size() == 0:
					flat_ok = false
					continue
				var verts: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
				for v in verts:
					if absf(v.y) > 0.001:
						flat_ok = false
						break
			water_ok = (sea == 32 and underwater and not above_water
				and flat_ok and far_water.size() > 0)
			water_detail = ("cell=%s sea=%d under=%s above=%s near=%d far=%d "
				+ "flat=%s") % [str(water_cell), sea, underwater, above_water,
				near_water.size(), far_water.size(), flat_ok]
			# 游泳:按住跳跃上浮(创造模式,不扣血)
			player.set("创造模式", true)
			player.set("生命", 100.0)
			player.global_position = water_point
			player.velocity = Vector3.ZERO
			for i in range(10):
				await physics_frame
			var swim_y0: float = player.global_position.y
			Input.action_press("jump")
			for i in range(45):
				await physics_frame
			Input.action_release("jump")
			var swim_y1: float = player.global_position.y
			swim_ok = swim_y1 > swim_y0 + 1.0
			swim_detail = "y=%.2f->%.2f" % [swim_y0, swim_y1]
			# 站在水面(脚正好 32)不许被浮力顶住:不按跳跃,1 秒内要沉下去
			player.global_position = Vector3(water_cell.x + 0.5, float(sea),
				water_cell.y + 0.5)
			player.velocity = Vector3.ZERO
			for i in range(60):
				await physics_frame
			var sink_y: float = player.global_position.y
			sink_ok = sink_y < float(sea) - 0.8
			sink_detail = "y=%.2f" % sink_y
			# 溺水:头一直在水下,6 秒后开始每秒扣 2 点
			player.set("创造模式", false)
			player.set("生命", 100.0)
			player.global_position = Vector3(water_cell.x + 0.5,
				float(sea) - 4.0, water_cell.y + 0.5)
			player.velocity = Vector3.ZERO
			for i in range(480):
				await physics_frame
			var drown_hp := float(player.get("生命"))
			drown_ok = drown_hp < 100.0
			drown_detail = "hp=%.0f" % drown_hp
			# 水下雾:相机在水里时雾被压近(能看清海底),回到岸上恢复
			player.global_position = Vector3(water_cell.x + 0.5,
				float(sea) - 5.0, water_cell.y + 0.5)
			for i in range(20):
				await physics_frame
			var fog_env: Environment = null
			for found in scene.find_children("*", "WorldEnvironment", true, false):
				var holder := found as WorldEnvironment
				if holder != null and holder.environment != null:
					fog_env = holder.environment
					break
			var fog_under := (fog_env != null and fog_env.fog_enabled
				and fog_env.fog_depth_end <= 30.0)
			var under_detail := "none"
			if fog_env != null:
				under_detail = "%.0f/%.0f" % [fog_env.fog_depth_begin,
					fog_env.fog_depth_end]
			player.global_position = home + Vector3(0.0, 1.0, 0.0)
			player.velocity = Vector3.ZERO
			for i in range(30):
				await physics_frame
			var fog_home := (fog_env != null and fog_env.fog_depth_end > 40.0)
			water_fog_ok = fog_under and fog_home
			water_fog_detail = "under=%s home=%s" % [under_detail, fog_home]
			player.set("生命", 100.0)
		else:
			water_detail = "no water cell in +-128"
	print("MINECRAFT water=", water_ok, water_detail, " swim=", swim_ok,
		swim_detail, " sink=", sink_ok, sink_detail, " drown=", drown_ok,
		drown_detail, " fog=", water_fog_ok, water_fog_detail)

	# ---- 输入保护:失焦 / 暂停 后不能"按下还挂着" ----
	var input_ok := false
	var input_detail := ""
	var guard: Node = null
	for child in player.get_children():
		if child.has_method("clear_all"):
			guard = child
			break
	var key_down := InputEventKey.new()
	key_down.physical_keycode = KEY_W
	key_down.pressed = true
	Input.parse_input_event(key_down)
	for i in range(4):
		await physics_frame
	var held_move := Input.get_action_strength("move_up")
	root.propagate_notification(NOTIFICATION_APPLICATION_FOCUS_OUT)
	for i in range(4):
		await physics_frame
	var cleared_move := Input.get_action_strength("move_up")
	var mouse_down := InputEventMouseButton.new()
	mouse_down.button_index = MOUSE_BUTTON_LEFT
	mouse_down.pressed = true
	Input.parse_input_event(mouse_down)
	for i in range(4):
		await physics_frame
	var held_mouse := Input.is_mouse_button_pressed(MOUSE_BUTTON_LEFT)
	root.propagate_notification(NOTIFICATION_APPLICATION_FOCUS_OUT)
	for i in range(4):
		await physics_frame
	var cleared_mouse := Input.is_mouse_button_pressed(MOUSE_BUTTON_LEFT)
	# 暂停(开背包 / 开菜单都走树暂停)时也要清
	Input.action_press("move_up")
	for i in range(2):
		await process_frame
	paused = true
	for i in range(3):
		await process_frame
	var cleared_pause := Input.get_action_strength("move_up")
	paused = false
	for i in range(3):
		await process_frame
	Input.action_release("move_up")
	input_ok = (guard != null and held_move > 0.5 and cleared_move < 0.01
		and held_mouse and not cleared_mouse and cleared_pause < 0.01)
	input_detail = "guard=%s move=%.1f->%.1f mouse=%s->%s pause=%.2f" % [
		guard != null, held_move, cleared_move, held_mouse, cleared_mouse,
		cleared_pause]
	print("MINECRAFT input=", input_ok, input_detail)

	# ---- 出生点(第两百一十六轮):不压平地形,选落差最小的地,保护圈内无怪 ----
	var spawn_ok := false
	var plant_ok := false
	var spawn_detail := ""
	var plant_detail := ""
	if chunk_script != null:
		spawn_point = Vector3(float(player.get("出生点x")),
			float(player.get("出生点y")), float(player.get("出生点z")))
		var spawn_cell := Vector2i(int(floor(spawn_point.x)),
			int(floor(spawn_point.z)))
		var spawn_height := int(chunk_script.call("_height_at", spawn_cell))
		var spawn_gap := 0
		for dz in range(-2, 3):
			for dx in range(-2, 3):
				spawn_gap = maxi(spawn_gap, absi(int(chunk_script.call(
					"_height_at", spawn_cell + Vector2i(dx, dz))) - spawn_height))
		# 旧出生场地(±12)不再被压成一层:这里必须出现多个高度
		var old_site := {}
		for z in range(-12, 13):
			for x in range(-12, 13):
				old_site[int(chunk_script.call("_height_at",
					Vector2i(x, z)))] = true
		var monsters_safe := true
		for body in scene.find_children("怪物*", "CharacterBody3D", true, false):
			var body3 := body as Node3D
			if Vector2(body3.global_position.x - spawn_point.x,
					body3.global_position.z - spawn_point.z).length() < 15.99:
				monsters_safe = false
		spawn_ok = (absf(spawn_point.y - float(spawn_height)) < 0.01
			and spawn_gap <= 2 and old_site.size() >= 3 and monsters_safe)
		spawn_detail = "at=%s h=%d gap=%d site_h=%d safe=%s" % [
			spawn_point, spawn_height, spawn_gap, old_site.size(), monsters_safe]
		# 植被:近景里真的有节点,三种也都按群系分布得出来
		var plants: Array = scene.find_children("植物_*", "Area3D", true, false)
		var seen_kinds := {}
		var grass_cells := 0
		var grasses := 0
		var flowers := 0
		for z in range(-64, 65, 2):
			for x in range(-64, 65, 2):
				var cell := Vector2i(x, z)
				var top := str(chunk_script.call("_kind_at", cell,
					int(chunk_script.call("_height_at", cell))))
				if top != "grass":
					continue
				grass_cells += 1
				var kind := str(chunk_script.call("_plant_at", cell, "grass"))
				if kind.is_empty():
					continue
				seen_kinds[kind] = true
				if kind == "tall_grass":
					grasses += 1
				else:
					flowers += 1
		# 植被加密(第一批)的验收线:固定种子实测草从 214‰ 涨到 318‰,
		# 这里只兜底下限(不同随机种子有 ±15% 浮动),同时防"每格一丛"。
		var grass_pm := float(grasses) * 1000.0 / maxf(1.0, float(grass_cells))
		plant_ok = (plants.size() > 0 and seen_kinds.size() >= 3
			and grass_pm > 235.0 and grass_pm < 700.0)
		plant_detail = "nodes=%d kinds=%d grass=%.0fpm flower=%.0fpm" % [
			plants.size(), seen_kinds.size(), grass_pm,
			float(flowers) * 1000.0 / maxf(1.0, float(grass_cells))]

	# ---- 猪(第一批):出生点保护圈外 + 漫步 + 受击逃跑 + 掉猪肉 ----
	var pig_ok := false
	var pig_detail := ""
	var pig_home := Vector3.ZERO
	var cam_local := camera.position if camera != null else Vector3.ZERO
	if chunk_script != null and camera != null:
		var pig_script: Node = null
		for child in player.get_children():
			if child.has_method("pig_hit"):
				pig_script = child
				break
		var pig_nodes: Array = scene.find_children("猪*", "CharacterBody3D",
			true, false)
		var pigs_safe := pig_nodes.size() >= 2
		for node in pig_nodes:
			var body3 := node as Node3D
			if body3 == null:
				continue
			pig_home = body3.global_position
			if Vector2(body3.global_position.x - spawn_point.x,
					body3.global_position.z - spawn_point.z).length() <= 16.0:
				pigs_safe = false
		if pig_script != null and pig_nodes.size() >= 2 and pigs_safe:
			var pig = pig_nodes[0] as CharacterBody3D
			# 漫步:60 帧内自己会挪动
			var walked_from: Vector3 = pig.global_position
			for i in range(60):
				await physics_frame
			var walked: bool = pig.global_position.distance_to(walked_from) > 0.05
			# 受击逃跑 / 掉猪肉:把猪挪到出生点东边 3 格(那里是挑出来的平地),
			# 玩家站在出生点、相机从西往东打 —— 逃跑方向是远离玩家的 +x,
			# 不会因为背靠山坡卡住而测成"没跑"。
			var pig_cell := Vector2i(int(floor(spawn_point.x)) + 3,
				int(floor(spawn_point.z)))
			pig.global_position = Vector3(pig_cell.x + 0.5,
				float(chunk_script.call("_height_at", pig_cell)) + 0.9,
				pig_cell.y + 0.5)
			pig.velocity = Vector3.ZERO
			player.global_position = spawn_point + Vector3(0.0, 0.5, 0.0)
			player.velocity = Vector3.ZERO
			for i in range(8):
				await physics_frame
			var pork_before := 0
			if inv_node != null:
				pork_before = int(inv_node.call("total_of", "pork"))
			camera.global_position = pig.global_position + Vector3(-2.8, 1.6, 0.0)
			camera.look_at(pig.global_position + Vector3(0.0, 0.6, 0.0),
				Vector3.UP)
			pig_script.call("pig_hit")
			var flee_from: Vector3 = pig.global_position
			for i in range(36):
				await physics_frame
			var fled: bool = pig.global_position.distance_to(flee_from) > 0.4
			# 第二剑:打死掉猪肉
			camera.global_position = pig.global_position + Vector3(-2.8, 1.6, 0.0)
			camera.look_at(pig.global_position + Vector3(0.0, 0.6, 0.0),
				Vector3.UP)
			pig_script.call("pig_hit")
			for i in range(6):
				await physics_frame
			var pork_gain := 0
			if inv_node != null:
				pork_gain = int(inv_node.call("total_of", "pork")) - pork_before
			var pig_gone := not is_instance_valid(pig)
			pig_ok = walked and fled and pork_gain == 1 and pig_gone
			pig_detail = "n=%d safe=%s walk=%s flee=%s pork=%d gone=%s" % [
				pig_nodes.size(), pigs_safe, walked, fled, pork_gain, pig_gone]
		else:
			pig_detail = "n=%d safe=%s script=%s" % [
				pig_nodes.size(), pigs_safe, pig_script != null]
		# 机位 / 玩家都放回原处,后面的用例还按老相机原点算射线
		player.global_position = spawn_point + Vector3(0.0, 0.5, 0.0)
		player.velocity = Vector3.ZERO
		camera.position = cam_local
		camera.rotation = Vector3.ZERO
	print("MINECRAFT pig=", pig_ok, pig_detail, " home=", pig_home)

	# ---- 摔落扣血 / 掉出世界重生 ----
	var fall_ok := false
	var respawn_ok := false
	var fall_detail := ""
	if save_script != null:
		player.set("创造模式", false)
		player.set("生命", 100.0)
		# 先挪到一块没被挖过的地形上落稳:前面的挖方块用例把出生点脚下
		# 那一格挖掉了,出生点附近只有一层地形,人正往虚空掉。
		# 合批(第两百一十七轮)后一列只有一个物理体、位置在 y=0,
		# 不能再"按 global_position.y 找最高的那一块":改用 column_blocks()
		# 拿每一列还在的方块中心,挑最高的一块站。
		var stand_top := Vector3.ZERO
		var found_ground := false
		for item in scene.find_children("地形_*", "StaticBody3D", true, false):
			var body := item as Node3D
			var column := Vector2i(roundi(body.global_position.x - 0.5),
				roundi(body.global_position.z - 0.5))
			var blocks: Array = chunk_script.call("column_blocks", column)
			if blocks.is_empty():
				continue
			var top: Vector3 = blocks[blocks.size() - 1]
			if not found_ground or top.y > stand_top.y:
				stand_top = top
				found_ground = true
		if found_ground:
			player.global_position = stand_top + Vector3(0.0, 1.1, 0.0)
		else:
			player.global_position = (player as Node3D).global_position \
				+ Vector3(0.0, 1.0, 0.0)
		player.velocity = Vector3.ZERO
		for i in range(160):
			await physics_frame
			if i > 2 and player.is_on_floor():
				break
		var stand: Vector3 = player.global_position
		player.global_position = stand + Vector3(0.0, 7.0, 0.0)
		player.velocity = Vector3.ZERO
		for i in range(300):
			await physics_frame
			if i > 5 and player.is_on_floor():
				break
		for i in range(6):
			await physics_frame
		var hp_fall := float(player.get("生命"))
		fall_ok = hp_fall < 99.0
		fall_detail = "hp=%.0f" % hp_fall
		player.global_position = Vector3(0.5, -30.0, 0.5)
		for i in range(12):
			await physics_frame
		# 第两百一十六轮:出生点由地形脚本挑(玩家变量 出生点x/y/z),
		# 不再是固定的 (0.5,1,0.5)。
		spawn_point = Vector3(float(player.get("出生点x")),
			float(player.get("出生点y")), float(player.get("出生点z")))
		respawn_ok = (float(player.get("生命")) > 99.0
			and player.global_position.distance_to(spawn_point) < 3.0)

	# ---- 放置前的占用检查 + 网格吸附:已有方块的格子拒绝,空格允许 ----
	var place_guard_ok := false
	var snap_ok := false
	var guard_detail := ""
	if chunk_script != null:
		# 先让准星离开箱子:右键开箱的检查会接管这次放置判定
		if inv_node != null and bool(inv_node.get("_chest_open")):
			inv_node.call("close_chest")
			for i in range(3):
				await process_frame
		if camera != null:
			camera.rotation_degrees = Vector3(-30.0, 180.0, 0.0)
		var terrain_bodies: Array = scene.find_children(
			"地形_*", "StaticBody3D", true, false)
		# 按列分组:最高的那块顶面之上是空的(必须允许),
		# 中间被压住的那块顶面之上已经有方块(必须拒绝)。
		# 合批后一列一个物理体、位置在 y=0:改用 column_blocks() 拿逐格中心。
		var stack: Array = []
		for item in terrain_bodies:
			var body := item as Node3D
			var column := Vector2i(roundi(body.global_position.x - 0.5),
				roundi(body.global_position.z - 0.5))
			var blocks: Array = chunk_script.call("column_blocks", column)
			if blocks.size() > stack.size():
				stack = blocks
		if stack.size() > 0:
			var top_block: Vector3 = stack[stack.size() - 1]
			player.set("放置命中",
				top_block + Vector3(0.0, 0.5, 0.0))
			player.set("放置法线", Vector3.UP)
			var free_here := bool(chunk_script.call("can_place_here"))
			var origin: Vector3 = player.get("放置目标")
			# 目标块的底面应该正好落在被命中方块的顶面上(差半格就是旧 bug)
			snap_ok = absf(origin.y - (top_block.y + 0.5)) < 0.01
			var refused := false
			if stack.size() > 1:
				var buried: Vector3 = stack[0]
				player.set("放置命中",
					buried + Vector3(0.0, 0.5, 0.0))
				refused = not bool(chunk_script.call("can_place_here"))
			place_guard_ok = free_here and refused
			guard_detail = "free=%s refused=%s snap=%s" % [
				free_here, refused, snap_ok]

	# ---- 第两百一十五轮:放置不能塞进玩家自己身体里 ----
	var self_block_ok := false
	var self_detail := ""
	# ---- 第两百一十五轮:原版挖掘公式(硬度 × 1.5 ÷ 倍率 / 5×硬度不掉落)----
	var mine_formula_ok := false
	var mine_formula_detail := ""
	# ---- 第两百一十五轮:右键拆半 / 放 1 个 / Shift 快捷移动 ----
	var ui_ops_ok := false
	var ui_ops_detail := ""
	if chunk_script != null:
		# 玩家脚下那一格:目标格中心正好和玩家碰撞体重叠,必须拒绝
		if camera != null:
			camera.rotation_degrees = Vector3(60.0, 0.0, 0.0)
		var feet: Vector3 = player.global_position
		var feet_cell := Vector3i(floori(feet.x), floori(feet.y),
			floori(feet.z))
		player.set("放置命中", Vector3(feet_cell.x + 0.5,
			float(feet_cell.y), feet_cell.z + 0.5))
		player.set("放置法线", Vector3.UP)
		self_block_ok = not bool(chunk_script.call("can_place_here"))
		self_detail = "cell=%s allowed=%s" % [feet_cell, not self_block_ok]
		var hand := {"type": "", "tier": 0, "speed": 1.0}
		var pick := {"type": "pickaxe", "tier": 1, "speed": 2.0}
		var stone_hand: Dictionary = chunk_script.call("_break_time",
			"stone", hand)
		var stone_pick: Dictionary = chunk_script.call("_break_time",
			"stone", pick)
		var dirt_hand: Dictionary = chunk_script.call("_break_time",
			"dirt", hand)
		mine_formula_ok = (absf(float(stone_hand["time"]) - 7.5) < 0.001
			and not bool(stone_hand["drops"])
			and absf(float(stone_pick["time"]) - 1.125) < 0.001
			and bool(stone_pick["drops"])
			and absf(float(dirt_hand["time"]) - 0.75) < 0.001
			and bool(dirt_hand["drops"]))
		mine_formula_detail = "stone_hand=%.3f/%s stone_pick=%.3f dirt=%.3f" % [
			float(stone_hand["time"]), str(stone_hand["drops"]),
			float(stone_pick["time"]), float(dirt_hand["time"])]
	if inv_node != null:
		var saved_slots: Array = inv_node.call("export_slots")
		inv_node.call("open")
		for i in range(3):
			await process_frame
		var bag: Array = inv_node.get("_slots")
		for i in range(bag.size()):
			bag[i] = null
		bag[0] = {"kind": "wood", "count": 7}
		var right_ev := InputEventMouseButton.new()
		right_ev.button_index = MOUSE_BUTTON_RIGHT
		right_ev.pressed = true
		inv_node.call("_on_slot_input", right_ev, 0)
		var half_ok := (int(inv_node.get("_held_count")) == 4
			and bag[0] is Dictionary and int(bag[0]["count"]) == 3)
		inv_node.call("_on_slot_input", right_ev, 1)
		var one_ok := (bag[1] is Dictionary
			and int(bag[1]["count"]) == 1
			and int(inv_node.get("_held_count")) == 3)
		# 清空手上,继续测 Shift
		inv_node.set("_held_kind", "")
		inv_node.set("_held_count", 0)
		bag[2] = {"kind": "stone", "count": 10}
		var shift_ev := InputEventMouseButton.new()
		shift_ev.button_index = MOUSE_BUTTON_LEFT
		shift_ev.pressed = true
		shift_ev.shift_pressed = true
		inv_node.call("_on_slot_input", shift_ev, 2)
		var shifted := int(inv_node.call("_count_in", bag, "stone")) == 10 \
			and bag[2] == null
		inv_node.call("close")
		# 箱子面板:右键拆半 + 背包 Shift 存进箱子
		inv_node.call("open_chest", "0,1,-1")
		for i in range(3):
			await process_frame
		var chest: Array = inv_node.get("_chest_slots")
		for i in range(chest.size()):
			chest[i] = null
		chest[0] = {"kind": "apple", "count": 5}
		inv_node.call("_on_chest_slot_input", right_ev, 0)
		var chest_half := (int(inv_node.get("_held_count")) == 3
			and int(chest[0]["count"]) == 2)
		inv_node.set("_held_kind", "")
		inv_node.set("_held_count", 0)
		bag[3] = {"kind": "wood", "count": 6}
		inv_node.call("_on_slot_input", shift_ev, 3)
		var chest_shift := (bag[3] == null
			and int(inv_node.call("_count_in", chest, "wood")) == 6)
		inv_node.call("close_chest")
		for i in range(3):
			await process_frame
		# 合成面板:背包 Shift 进 3×3 材料格
		inv_node.call("open_craft")
		for i in range(3):
			await process_frame
		var grid: Array = inv_node.get("_craft_inputs")
		for i in range(grid.size()):
			grid[i] = null
		bag[4] = {"kind": "plank", "count": 3}
		inv_node.call("_on_slot_input", shift_ev, 4)
		var craft_shift := (bag[4] == null
			and int(inv_node.call("_count_in", grid, "plank")) == 3)
		inv_node.call("close_craft")
		for i in range(3):
			await process_frame
		ui_ops_ok = (half_ok and one_ok and shifted and chest_half
			and chest_shift and craft_shift)
		ui_ops_detail = ("half=%s one=%s shift=%s chest_half=%s "
			+ "chest_shift=%s craft_shift=%s") % [
			half_ok, one_ok, shifted, chest_half, chest_shift, craft_shift]
		inv_node.call("import_slots", saved_slots)
		inv_node.call("_refresh_all")

	# ---- 第两百一十五轮:伪群系(温度 / 湿度两个 2D 噪声) ----
	var biome_ok := false
	var biome_detail := ""
	if chunk_script != null:
		var biomes := {}
		var snow_cols := 0
		for z in range(-240, 241, 24):
			for x in range(-240, 241, 24):
				var cell := Vector2i(x, z)
				biomes[str(chunk_script.call("_biome_at", cell))] = true
				var h := int(chunk_script.call("_height_at", cell))
				if str(chunk_script.call("_kind_at", cell, h)) == "snow":
					snow_cols += 1
		# 至少出现三种伪群系,且雪原真的铺出了雪方块
		biome_ok = biomes.size() >= 3 and snow_cols > 0
		biome_detail = "kinds=%s snow=%d" % [str(biomes.keys()), snow_cols]

	# ---- 雨滴尺寸 / 云层透明度 ----
	var rain_ok := false
	var cloud_soft_ok := false
	var rain_detail := ""
	# 天气在晴→雨→雪之间自动轮换,粒子当前挂的 mesh 可能是雪;
	# 直接读天气脚本建好的雨滴 mesh(细长条 0.015 × 0.34 × 0.015)。
	for node in scene.get_tree().root.find_children("*", "Node", true, false):
		if not node.has_method("set_weather"):
			continue
		var rain_mesh = node.get("_rain_mesh")
		if rain_mesh is BoxMesh:
			var box := (rain_mesh as BoxMesh).size
			rain_ok = box.y > 0.2 and box.x < 0.05 and box.z < 0.05
			rain_detail = "%.3f x %.3f x %.3f" % [box.x, box.y, box.z]
		break
	var clouds: Array = scene.find_children("方块云", "MeshInstance3D", true, false)
	if clouds.size() > 0:
		var cloud_mesh := clouds[0] as MeshInstance3D
		var cloud_mat := cloud_mesh.material_override as StandardMaterial3D
		if cloud_mat != null:
			var faces := 0
			if cloud_mesh.mesh != null:
				faces = cloud_mesh.mesh.get_faces().size() / 3
			cloud_soft_ok = (cloud_mat.albedo_color.a < 0.95
				and cloud_mat.transparency == BaseMaterial3D.TRANSPARENCY_ALPHA
				and faces > 300)
			rain_detail += " cloud_faces=%d alpha=%.2f" % [
				faces, cloud_mat.albedo_color.a]

	# ---- 羊模型:补了头 / 耳朵 / 四条腿(至少三种材质) ----
	var sheep_ok := false
	var sheep_detail := ""
	var sheep_nodes: Array = scene.find_children("羊1", "MeshInstance3D", true, false)
	if sheep_nodes.size() > 0:
		var sheep_mesh := (sheep_nodes[0] as MeshInstance3D).mesh
		if sheep_mesh != null:
			sheep_detail = "surfaces=%d" % sheep_mesh.get_surface_count()
			sheep_ok = sheep_mesh.get_surface_count() >= 3

	# ---- 玩家皮肤:64×64 贴图 + 外套层(至少两个材质槽) ----
	var skin_ok := false
	var skin_detail := ""
	var model_node := player.get_node_or_null("Model")
	if model_node != null:
		for item in (model_node as Node).find_children(
				"*", "MeshInstance3D", true, false):
			var body := (item as MeshInstance3D).mesh
			if body == null:
				continue
			var skin_mat = (item as MeshInstance3D).get_surface_override_material(0)
			var has_texture := (skin_mat is StandardMaterial3D
				and (skin_mat as StandardMaterial3D).albedo_texture != null)
			skin_ok = body.get_surface_count() >= 2 and has_texture
			skin_detail = "surfaces=%d tex=%s" % [
				body.get_surface_count(), has_texture]

	# ---- 帧率设置:上限 / 垂直同步 / 画质真的落到引擎上 ----
	var perf_ok := false
	var perf_detail := ""
	for node in scene.get_tree().root.find_children("*", "Node", true, false):
		if node.has_method("_apply_perf"):
			node.set("_fps_index", 0)
			node.call("_apply_perf")
			var capped := Engine.max_fps == 30
			node.set("_fps_index", 4)
			node.call("_apply_perf")
			var uncapped := Engine.max_fps == 0
			perf_ok = capped and uncapped
			perf_detail = "cap30=%s uncap=%s" % [capped, uncapped]
			node.set("_fps_index", 1)
			node.call("_apply_perf")
			break

	# ---- 生存 HUD:底部血条 + 10 格饥饿条;创造模式隐藏 ----
	var hud_bottom_ok := false
	var hud_creative_ok := false
	if save_script != null:
		var surv_hud: Array = scene.find_children("生存HUD", "Control", true, false)
		var hunger_cells: Array = scene.find_children(
			"饥饿格*", "ColorRect", true, false)
		var hp_fills: Array = scene.find_children("血条", "ColorRect", true, false)
		var old_health: Array = scene.find_children("生命文本", "Label", true, false)
		player.set("创造模式", false)
		player.set("生命", 50.0)
		for i in range(4):
			await process_frame
		var shown := (surv_hud.size() > 0
			and (surv_hud[0] as CanvasItem).visible)
		var fill_ok := (hp_fills.size() > 0
			and absf((hp_fills[0] as ColorRect).size.x - 103.0) < 2.0)
		var old_hidden := (old_health.size() > 0
			and not (old_health[0] as CanvasItem).visible)
		hud_bottom_ok = (shown and fill_ok and hunger_cells.size() == 10
			and old_hidden)
		print("MINECRAFT_DEBUG hud surv=", surv_hud.size(), " vis=", shown,
			" hunger=", hunger_cells.size(), " fills=", hp_fills.size(),
			" fill=", (hp_fills[0] as ColorRect).size.x \\
				if hp_fills.size() > 0 else -1.0,
			" old=", old_health.size(), " oldvis=",
			(old_health[0] as CanvasItem).visible \\
				if old_health.size() > 0 else false)
		player.set("创造模式", true)
		for i in range(4):
			await process_frame
		hud_creative_ok = (surv_hud.size() > 0
			and not (surv_hud[0] as CanvasItem).visible)
		player.set("生命", 100.0)

	# ---- 旧档高度迁移(M2a §11.10):用仓库里的真实 format 2 存档走一遍 ----
	# 存档由上一版构建真机保存(背包 / 箱子 / 设置 / 位置 / 世界改动都有值),
	# Python 侧把它复制进工程 res://legacy_save_format2.json。
	var migration_ok := false
	var migration_detail := ""
	if save_script != null and chunk_script != null:
		var fixture := FileAccess.open("res://legacy_save_format2.json",
			FileAccess.READ)
		var fixture_text := ""
		if fixture != null:
			fixture_text = fixture.get_as_text()
			fixture.close()
		if not fixture_text.is_empty():
			DirAccess.make_dir_recursive_absolute(
				ProjectSettings.globalize_path("user://saves"))
			var legacy_path := "user://saves/旧档样本.json"
			var legacy_file := FileAccess.open(legacy_path, FileAccess.WRITE)
			legacy_file.store_string(fixture_text)
			legacy_file.close()
			# 1) 读档:高度不兼容 -> 只弹提示,不应用、不写盘
			var entered: bool = bool(save_script.call("load_game",
				"旧档样本.json"))
			for i in range(3):
				await process_frame
			var prompts: Array = scene.find_children("迁移提示", "Control",
				true, false)
			var prompt_ok: bool = prompts.size() > 0 \
				and (prompts[0] as CanvasItem).visible
			var text_ok := false
			if prompts.size() > 0:
				var label := prompts[0].find_child("迁移说明", true, false) as Label
				text_ok = (label != null and label.text.contains("24")
					and label.text.contains("64"))
			# 2) 取消:不写盘、不动当前世界
			var cancel_buttons: Array = scene.find_children("迁移取消",
				"Button", true, false)
			if cancel_buttons.size() > 0:
				cancel_buttons[0].pressed.emit()
			for i in range(3):
				await process_frame
			var after_text := ""
			var after_file := FileAccess.open(legacy_path, FileAccess.READ)
			if after_file != null:
				after_text = after_file.get_as_text()
				after_file.close()
			var mig_cancel_ok := after_text == fixture_text
			# 记录迁移报告目录当前文件数:确认后必须新增一份(不能靠旧报告蒙混)
			var reports_before := 0
			var count_dir := DirAccess.open(
				"user://.audit/minecraft/migrations")
			if count_dir != null:
				count_dir.list_dir_begin()
				var count_entry := count_dir.get_next()
				while count_entry != "":
					if count_entry.ends_with(".json"):
						reports_before += 1
					count_entry = count_dir.get_next()
				count_dir.list_dir_end()
			# 3) 再读 + 确认:只重置地形,背包 / 箱子 / 设置 / 位置 / 血量保留
			var entered2: bool = bool(save_script.call("load_game",
				"旧档样本.json"))
			for i in range(2):
				await process_frame
			var confirm_buttons: Array = scene.find_children("迁移确认",
				"Button", true, false)
			if confirm_buttons.size() > 0:
				confirm_buttons[0].pressed.emit()
			var pos_now: Vector3 = player.global_position
			for i in range(5):
				await process_frame
			var inv2: Node = null
			for child in player.get_children():
				if child.has_method("export_slots"):
					inv2 = child
			var slots2: Array = inv2.call("export_slots")
			var inv_keep := (slots2.size() == 36
				and slots2[0] is Array and str(slots2[0][0]) == "grass"
				and int(slots2[0][1]) == 64
				and slots2[5] is Array and str(slots2[5][0]) == "apple"
				and int(slots2[5][1]) == 8
				and slots2[6] is Array and str(slots2[6][0]) == "sword")
			var chests2: Dictionary = inv2.call("export_chests")
			var chest_keep := (chests2.has("7,2,7")
				and chests2["7,2,7"][0] is Array
				and str(chests2["7,2,7"][0][0]) == "apple"
				and int(chests2["7,2,7"][0][1]) == 9)
			var settings_keep := (str(player.get("世界名")) == "新的世界"
				and str(player.get("种子")) == ""
				and int(player.get("地形类型")) == 2
				and int(player.get("世界大小")) == 1
				and int(player.get("植被密度")) == 3
				and int(player.get("昼夜时间")) == 1
				and int(player.get("渲染距离")) == 3
				and int(player.get("世界高度")) == 64)
			var pos_keep := (absf(pos_now.x - 1.5) < 0.6
				and absf(pos_now.z - 7.5) < 0.6 and pos_now.y > 0.0)
			var health_keep := absf(float(player.get("生命")) - 42.0) < 0.01
			var edits_reset: bool = chunk_script.get("_removed").is_empty() \
				and chunk_script.get("_placed").is_empty()
			var report_ok := false
			var report_new := false
			var report_dir := DirAccess.open(
				"user://.audit/minecraft/migrations")
			if report_dir != null:
				report_dir.list_dir_begin()
				var entry := report_dir.get_next()
				var report_count := 0
				while entry != "":
					if entry.ends_with(".json"):
						report_count += 1
						var rf := FileAccess.open(
							"user://.audit/minecraft/migrations/" + entry,
							FileAccess.READ)
						if rf != null:
							var rep = JSON.parse_string(rf.get_as_text())
							rf.close()
							if rep is Dictionary \
									and int(rep.get("from_height", 0)) == 24 \
									and int(rep.get("to_height", 0)) == 64 \
									and rep.has("reset_edits") \
									and rep.has("elapsed_ms"):
								report_ok = true
					entry = report_dir.get_next()
				report_dir.list_dir_end()
				report_new = report_count > reports_before
			report_ok = report_ok and report_new
			migration_ok = (not entered and prompt_ok and text_ok and mig_cancel_ok
				and not entered2 and inv_keep and chest_keep and settings_keep
				and pos_keep and health_keep and edits_reset and report_ok)
			migration_detail = ("prompt=%s text=%s cancel=%s inv=%s chest=%s"
				+ " settings=%s pos=%s hp=%s reset=%s report=%s") % [
				prompt_ok, text_ok, mig_cancel_ok, inv_keep, chest_keep,
				settings_keep, pos_keep, health_keep, edits_reset, report_ok]
			print("MINECRAFT_DEBUG migration=", migration_ok, " ",
				migration_detail)

	# ---- M2a 第四段:收口测量(只测量,不调档)----
	# 三个场景单独清空分区统计再采:静止 / 走动 / 开界面(ESC 暂停菜单)。
	var m2a_metrics: Dictionary = {}
	var m2a_entry_metrics: Dictionary = {}
	var m2a_frames: Dictionary = {}
	var rss_mb := -1.0
	if save_node != null and chunk_node != null:
		if paused:
			await press("ui_cancel")
			for i in range(20):
				await process_frame
		# 回出生点等整片铺稳(钉住高度,别让没铺到的脚下把人摔下去)
		for i in range(9000):
			await process_frame
			if i % 5 == 0:
				player.global_position = spawn_point + Vector3(0.0, 1.0, 0.0)
			if bool(chunk_node.call("is_ready")):
				break
		# 刚进世界 / 还没走动时的规模快照(出生圈 5×5 的碰撞还在)
		m2a_entry_metrics = chunk_node.call("metrics")
		save_node.call("perf_reset")
		for i in range(180):
			await process_frame
		m2a_frames["idle"] = save_node.call("perf_report").get("静止", {})
		# 走动:挪两格区块(和走路跨边走同一条重建队列)再按住走,采 240 帧。
		# 出生点被挖过洞,原地按住可能卡住;挪到空地才能采到真正的走动帧。
		# 挪完立刻清统计,把这一跨的列 / 分段重建全部算进来。
		player.global_position = spawn_point + Vector3(32.0, 1.0, 0.0)
		save_node.call("perf_reset")
		Input.action_press("move_right")
		for i in range(240):
			await process_frame
		Input.action_release("move_right")
		var walk_report: Dictionary = save_node.call("perf_report")
		m2a_frames["walk"] = walk_report.get("走动",
			walk_report.get("静止", {}))
		# 走完后原地静止再采一次:这时出生圈碰撞已收敛回 ±1 区块
		for i in range(600):
			await process_frame
			if bool(chunk_node.call("is_ready")):
				break
		save_node.call("perf_reset")
		for i in range(120):
			await process_frame
		m2a_frames["idle2"] = save_node.call("perf_report").get("静止", {})
		# 开界面:走框架自己的暂停入口(等价 ESC,避免残留面板抢按键)
		save_node.call("perf_reset")
		if player.has_method("_gk_apply_pause"):
			player.call("_gk_apply_pause", true, "暂停菜单", true)
		for i in range(10):
			await process_frame
		if not paused:
			paused = true
		for i in range(120):
			await process_frame
		m2a_frames["menu"] = save_node.call("perf_report").get("开界面", {})
		m2a_metrics = chunk_node.call("metrics")
		rss_mb = process_rss_mb()
		var idle: Dictionary = m2a_frames["idle"]
		var walk: Dictionary = m2a_frames["walk"]
		var idle2: Dictionary = m2a_frames["idle2"]
		var menu: Dictionary = m2a_frames["menu"]
		print("MINECRAFT_METRICS world_load_s=%.2f" % world_load_s,
			" sec_build_p50_ms=%.2f" % float(m2a_metrics.get("build_p50_ms", 0.0)),
			" sec_build_p99_ms=%.2f" % float(m2a_metrics.get("build_p99_ms", 0.0)),
			" sec_build_max_ms=%.2f" % float(m2a_metrics.get("build_max_ms", 0.0)),
			" rss_mb=%.1f" % rss_mb,
			" entry_bodies=", m2a_entry_metrics.get("bodies", 0),
			" entry_shapes=", m2a_entry_metrics.get("shapes", 0),
			" bodies=", m2a_metrics.get("bodies", 0),
			" shapes=", m2a_metrics.get("shapes", 0),
			" cells=", m2a_metrics.get("cells", 0),
			" sections=", m2a_metrics.get("sections", 0),
			" far_regions=", m2a_metrics.get("far_regions", 0),
			" section_builds=", m2a_metrics.get("section_builds", 0))
		print("MINECRAFT_FRAMES entry_max_ms=%.1f" % entry_max_ms,
			" idle_avg=%.1f idle_p99=%.1f idle_max=%.1f idle_over33=%d" % [
				float(idle.get("avg_ms", 0.0)), float(idle.get("p99_ms", 0.0)),
				float(idle.get("max_ms", 0.0)), int(idle.get("over30", 0))],
			" walk_avg=%.1f walk_p99=%.1f walk_max=%.1f walk_over33=%d" % [
				float(walk.get("avg_ms", 0.0)), float(walk.get("p99_ms", 0.0)),
				float(walk.get("max_ms", 0.0)), int(walk.get("over30", 0))],
			" idle2_avg=%.1f idle2_p99=%.1f idle2_max=%.1f idle2_over33=%d" % [
				float(idle2.get("avg_ms", 0.0)), float(idle2.get("p99_ms", 0.0)),
				float(idle2.get("max_ms", 0.0)), int(idle2.get("over30", 0))],
			" menu_avg=%.1f menu_p99=%.1f menu_max=%.1f menu_over33=%d" % [
				float(menu.get("avg_ms", 0.0)), float(menu.get("p99_ms", 0.0)),
				float(menu.get("max_ms", 0.0)), int(menu.get("over30", 0))],
			" chunk_build_walk_p99=%.2f" % float(walk.get("build_p99_ms", 0.0)),
			" chunk_build_walk_max=%.2f" % float(walk.get("build_max_ms", 0.0)),
			" chunk_build_idle_p99=%.2f" % float(idle.get("build_p99_ms", 0.0)))

	var ok = (on_floor and moved and jumped and broke and mine_time_ok and placed
		and placed_visible and grass_color_ok and switched and menu_ok and frozen
		and menu_fresh_ok and menu_saved_ok and menu_style_ok and menu_cloud_ok
		and mouse_ok
		and create_ok and cancel_ok and started and terrain_ok
		and look_ok and monsters_still and range_ok and bg_ok and bg_hidden
		and tier_ok and tree_default_ok and radius_keep and fog_ok
		and two_same_ok and two_same_loaded and overlap_ok and placed_mat_ok
		and load_ui_ok
		and settings_open and sens_ok and cull_ok
		and hotbar_ok and icon_ok and view_ok and cam_follow_ok and mode_ok and ate and sword_ok
		and wheel_ok and weather_ok and sheep_moved and infinite_ok and load_ok
		and create_flow
		and inv_ok and inv_pick_ok and inv_spend_ok and inv_open_ok
		and inv_move_ok and inv_hover_ok and inv_close_ok
		and craft_ok and craft_gate_ok and craft_close_ok
		and table_place_ok and table_open_ok
		and chest_place_ok and chest_open_ok and chest_store_ok
		and chest_take_ok and chest_drag_ok and chest_close_ok
		and chest_reload_ok and chest_hover_ok
		and noise_ok and spawn_ok and plant_ok and pig_ok
		and water_ok and swim_ok and sink_ok and drown_ok and water_fog_ok
		and input_ok and soft_ok
		and fall_ok and respawn_ok and place_guard_ok
		and self_block_ok and mine_formula_ok and ui_ops_ok and biome_ok
		and snap_ok and rain_ok and cloud_soft_ok and sheep_ok and skin_ok
		and perf_ok
		and hud_bottom_ok and hud_creative_ok
		and follow_sync
		and cross_ok and terrain_textured and cloud_ok and outline_ok
		and migration_ok
		and section_ok and mid_split_ok and only_section_ok and place_section_ok
		and world_load_ok and coverage_ok and fog_hole_ok and hyst_ok and dig_ok)
	print("MINECRAFT section=", section_ok, " mid_split=", mid_split_ok,
		" only_section=", only_section_ok, " place_section=", place_section_ok,
		section_detail, " splits=", mid_detail, place_section_detail)
	print("MINECRAFT world_load=", world_load_ok, " s=%.2f" % world_load_s,
		" entry_max_ms=%.1f" % entry_max_ms, " entry_frames=", entry_frames,
		" coverage=", coverage_ok, coverage_detail,
		" fog_hole=", fog_hole_ok, fog_hole_detail)
	print("MINECRAFT hysteresis=", hyst_ok, " ", hyst_detail)
	print("MINECRAFT dig10=", dig_ok, " ", dig_detail)
	print("MINECRAFT on_floor=", on_floor, " moved=", moved,
		" jumped=", jumped, " tiles=", tiles_before_mine, "->",
		tiles_after_mine, " broke=", broke, " mine_time=", mine_time_ok,
		" placed=", placed,
		" placed_visible=", placed_visible,
		" grass_color=", grass_color_ok, " stone=", stone_nodes.size(),
		" wood=", wood_nodes.size(), " switched=", switched,
		" cull=", cull_ok,
		" look=", look_ok, look_detail,
		" monsters_still=", monsters_still, " drift=%.2f" % monster_drift,
		" range=", range_ok, " bg=", bg_ok, " bg_hidden=", bg_hidden,
		" tier=", tier_ok, " tree=", tree_default_ok,
		" radius_keep=", radius_keep, " fog=", fog_ok,
		" two_same=", two_same_ok, " two_same_loaded=", two_same_loaded,
		" overlap=", overlap_ok, " placed_mat=", placed_mat_ok,
		" load_ui=", load_ui_ok, " create_seconds=%.2f" % load_seconds,
		" menu=", menu_ok, " menu_fresh=", menu_fresh_ok,
		" menu_saved=", menu_saved_ok, " menu_style=", menu_style_ok,
		" menu_cloud=", menu_cloud_ok, " mouse=", mouse_ok, " frozen=", frozen,
		" create=", create_ok,
		" cancel=", cancel_ok, " started=", started,
		" terrain=", terrain_ok, " hotbar=", hotbar_ok,
		" hud=", hud_text, " view=", view_ok, " cam_follow=", cam_follow_ok,
		cam_follow_detail,
		" mode=", mode_ok, " hp=", hp_surv0, "->", hp_surv1,
		" ate=", ate, " sword=", sword_ok, " wheel=", wheel_ok,
		wheel_detail,
		" slot=", slot_before, "->", slot_after,
		" settings=", settings_open, " sens=", sens_ok,
		" weather=", weather_ok, " sheep=", sheep_moved,
		" follow=", follow_sync, follow_detail,
		" crosshair=", cross_ok, " terrain_texture=", terrain_textured,
		" clouds=", cloud_ok, " outline=", outline_ok,
		" tiles_far=", tiles_far, " unload=", old_tile.size() == 0,
		" infinite=", infinite_ok,
		" model_hidden=", model_hidden, " save_open=", save_open,
		" saved=", saved, " saved_player=", saved_player,
		" saved_world=", saved_world, " saved_settings=", saved_settings,
		" list=", list_ok,
		" load_health=", loaded_health, " gap=%.2f" % flat_gap,
		" create_flow=", create_flow,
		" inv=", inv_ok, " pick=", inv_pick_ok, " spend=", inv_spend_ok,
		" inv_open=", inv_open_ok, " inv_move=", inv_move_ok,
		" inv_hover=", inv_hover_ok, " inv_close=", inv_close_ok, " tiles_loaded=", tiles_loaded,
		" noise=", noise_ok, noise_detail, " fall=", fall_ok, fall_detail,
		" water=", water_ok, water_detail, " swim=", swim_ok, swim_detail,
		" drown=", drown_ok, drown_detail, " water_fog=", water_fog_ok,
		water_fog_detail, " input=", input_ok, input_detail, " soft=", soft_ok,
		soft_detail,
		" spawn=", spawn_ok, spawn_detail, " plants=", plant_ok, plant_detail,
		" pig=", pig_ok, pig_detail,
		" respawn=", respawn_ok, " guard=", place_guard_ok, guard_detail,
		" self_block=", self_block_ok, self_detail,
		" mine_formula=", mine_formula_ok, mine_formula_detail,
		" ui_ops=", ui_ops_ok, ui_ops_detail,
		" biome=", biome_ok, biome_detail,
		" rain=", rain_ok, rain_detail,
		" cloud_soft=", cloud_soft_ok, " sheep_model=", sheep_ok, sheep_detail,
		" skin=", skin_ok, skin_detail, " perf=", perf_ok, perf_detail,
		" hud_bottom=", hud_bottom_ok, " hud_creative=", hud_creative_ok,
		" craft=", craft_ok, " craft_gate=", craft_gate_ok,
		" craft_close=", craft_close_ok, " table=", table_detail,
		" chest=", chest_place_ok, chest_open_ok, chest_store_ok,
		chest_take_ok, chest_close_ok, chest_reload_ok, chest_hover_ok, chest_detail,
		" save=", load_ok,
		" migration=", migration_ok, migration_detail)
	print("MINECRAFT_CHECK=", "OK" if ok else "FAIL")
	# 探针自己的存档清掉,把用户原来的存档目录原样放回
	wipe_dir(saves_abs)
	if had_saves:
		DirAccess.rename_absolute(backup_abs, saves_abs)
	quit(0 if ok else 1)
'''


# M2a 第二段:接缝截图。无头模式没有渲染,单独起一次带窗口的 Godot:
# 相机压在区块边界正上方(俯视)和边界一侧(贴地)各拍一张,人眼确认
# 相邻 section 之间没有漏面 / 重面。
SHOTS = '''extends SceneTree

func _initialize() -> void:
	DisplayServer.window_set_size(Vector2i(1280, 720))
	call_deferred("run")


func find_chunk(scene: Node) -> Node:
	var player := scene.get_node_or_null("Colliders/Player")
	if player == null:
		return null
	for child in player.get_children():
		if child.has_method("section_stats"):
			return child
	return null


func save_shot(name: String) -> void:
	await RenderingServer.frame_post_draw
	var image := root.get_texture().get_image()
	if image == null:
		print("MINECRAFT_SHOT_NULL ", name)
		return
	image.save_png("res://shots/" + name)
	print("MINECRAFT_SHOT ", name, " ", image.get_width(), "x",
		image.get_height())


func shot_percentile(values: Array, ratio: float) -> float:
	if values.is_empty():
		return 0.0
	var sorted := values.duplicate()
	sorted.sort()
	return float(sorted[clampi(int(round(float(sorted.size() - 1) * ratio)),
		0, sorted.size() - 1)])


func shot_over(values: Array, limit: float) -> int:
	var total := 0
	for value in values:
		if float(value) > limit:
			total += 1
	return total


func run() -> void:
	var scene = load("res://scenes/World.tscn").instantiate()
	root.add_child(scene)
	current_scene = scene
	var chunk := find_chunk(scene)
	for i in range(1200):
		await process_frame
		if chunk != null and bool(chunk.call("is_ready")):
			break
	for i in range(300):
		await process_frame
		if chunk != null and int((chunk.call("section_stats") as Dictionary)["queue"]) == 0:
			break
	# ---- M2a 第四段:带窗口渲染量一次 draw call / 三角面 / RSS ----
	# 必须真的进世界(开始菜单是暂停的,渲染的世界帧不可比);
	# 视角保持第一人称出生点朝前,和 2026-11-11 的 1736 draw calls 基线同口径。
	var create_btn: Array = scene.find_children("新建世界", "Button", true, false)
	if create_btn.size() > 0:
		create_btn[0].pressed.emit()
		for i in range(3):
			await process_frame
		var creative_btn: Array = scene.find_children("创造模式", "Button",
			true, false)
		if creative_btn.size() > 0:
			creative_btn[0].pressed.emit()
		var load_panel: Control = scene.find_child("加载面板", true, false)
		for i in range(3600):
			await process_frame
			if load_panel != null and not load_panel.visible and not paused:
				break
	for i in range(60):
		await process_frame
	await RenderingServer.frame_post_draw
	var draw_calls := int(Performance.get_monitor(
		Performance.RENDER_TOTAL_DRAW_CALLS_IN_FRAME))
	var primitives := int(Performance.get_monitor(
		Performance.RENDER_TOTAL_PRIMITIVES_IN_FRAME))
	var out_rss: Array = []
	var rss_code := OS.execute("powershell.exe", ["-NoProfile", "-Command",
		"(Get-Process -Id %d).WorkingSet64" % OS.get_process_id()], out_rss, false)
	var rss_mb := -1.0
	if rss_code == 0 and out_rss.size() > 0:
		var rss_text := str(out_rss[0]).strip_edges()
		if rss_text.is_valid_int():
			rss_mb = float(int(rss_text)) / 1048576.0
	print("MINECRAFT_SHOT_METRICS draw_calls=", draw_calls,
		" primitives=", primitives, " rss_mb=%.1f" % rss_mb)
	# ---- 三场景帧时(带窗口渲染,和 155fps 基线同一口径)----
	# 先等世界稳定(加载屏收起后远端还在补),再解除帧率上限采原始帧时。
	for i in range(9000):
		await process_frame
		if chunk != null and bool(chunk.call("is_ready")):
			break
	Engine.max_fps = 0
	var shot_player := scene.get_node_or_null("Colliders/Player") as Node3D
	var idle_frames: Array = []
	var last_us := Time.get_ticks_usec()
	for i in range(180):
		await process_frame
		var now_us := Time.get_ticks_usec()
		idle_frames.append(float(now_us - last_us) / 1000.0)
		last_us = now_us
	# 挪两格区块再走,避开出生点被挖过的洞
	if shot_player != null:
		var walk_cell := Vector2i(int(shot_player.global_position.x) + 32,
			int(shot_player.global_position.z))
		shot_player.global_position = Vector3(walk_cell.x + 0.5,
			float(chunk.call("_height_at", walk_cell)) + 1.2, walk_cell.y + 0.5)
		for i in range(4):
			await process_frame
	var walk_frames: Array = []
	Input.action_press("move_right")
	last_us = Time.get_ticks_usec()
	for i in range(240):
		await process_frame
		var walk_us := Time.get_ticks_usec()
		walk_frames.append(float(walk_us - last_us) / 1000.0)
		last_us = walk_us
	Input.action_release("move_right")
	# 开界面:走框架自己的暂停入口,暂停时也要能继续出帧
	if shot_player != null and shot_player.has_method("_gk_apply_pause"):
		shot_player.call("_gk_apply_pause", true, "暂停菜单", true)
	if not paused:
		paused = true
	var menu_frames: Array = []
	last_us = Time.get_ticks_usec()
	for i in range(120):
		await process_frame
		var menu_us := Time.get_ticks_usec()
		menu_frames.append(float(menu_us - last_us) / 1000.0)
		last_us = menu_us
	print("MINECRAFT_SHOT_FRAMES idle_avg=%.2f idle_p99=%.2f idle_max=%.2f idle_over33=%d" % [
		float(idle_frames.reduce(func(a, b): return a + b, 0.0) / maxf(1.0, idle_frames.size())),
		shot_percentile(idle_frames, 0.99), shot_percentile(idle_frames, 1.0),
		shot_over(idle_frames, 33.4)],
		" walk_avg=%.2f walk_p99=%.2f walk_max=%.2f walk_over33=%d" % [
		float(walk_frames.reduce(func(a, b): return a + b, 0.0) / maxf(1.0, walk_frames.size())),
		shot_percentile(walk_frames, 0.99), shot_percentile(walk_frames, 1.0),
		shot_over(walk_frames, 33.4)],
		" menu_avg=%.2f menu_p99=%.2f menu_max=%.2f menu_over33=%d" % [
		float(menu_frames.reduce(func(a, b): return a + b, 0.0) / maxf(1.0, menu_frames.size())),
		shot_percentile(menu_frames, 0.99), shot_percentile(menu_frames, 1.0),
		shot_over(menu_frames, 33.4)])
	# 菜单 / 加载屏 / HUD 全藏起来,只留世界
	for child in scene.get_children():
		if child is CanvasItem:
			(child as CanvasItem).visible = false
	for node in scene.find_children("*", "CanvasLayer", true, false):
		(node as CanvasLayer).visible = false
	# 云层在 y=92,俯视会挡住地面;截图时先收起来
	for node in scene.find_children("方块云", "MeshInstance3D", true, false):
		(node as MeshInstance3D).visible = false
	var player := scene.get_node("Colliders/Player") as Node3D
	var spot := player.global_position
	# 优先选最近的一条 x 边界,并在附近找一段"两边都是草地、落差 ≤1"的位置,
	# 这样截图里看到的是同一片地形的接缝,不会被群系边界干扰。
	var bx := roundf(spot.x / 16.0) * 16.0
	var bz := roundf(spot.z / 16.0) * 16.0
	var base_z := int(round(spot.z))
	var found := false
	for i in range(-64, 65):
		if found:
			break
		var z := base_z + i
		for side_x in [floorf(spot.x / 16.0) * 16.0,
				(floorf(spot.x / 16.0) + 1.0) * 16.0]:
			var left := Vector2i(int(side_x) - 1, z)
			var right := Vector2i(int(side_x), z)
			var hl := int(chunk.call("_height_at", left))
			var hr := int(chunk.call("_height_at", right))
			var kl := str(chunk.call("_kind_at", left, hl))
			var kr := str(chunk.call("_kind_at", right, hr))
			if kl == "grass" and kr == "grass" and absi(hl - hr) <= 1:
				bx = side_x
				bz = float(z)
				found = true
				break
	var dir := DirAccess.open("res://")
	if dir != null and not dir.dir_exists("shots"):
		dir.make_dir("shots")
	var cam := Camera3D.new()
	cam.fov = 70.0
	scene.add_child(cam)
	# 俯视:边界正上方 45 格(云已收),接缝在画面正中
	cam.global_position = Vector3(bx, 45.0, bz)
	cam.look_at(Vector3(bx, 0.0, bz), Vector3(0, 0, -1))
	cam.make_current()
	for i in range(10):
		await process_frame
	await save_shot("section_seam_top.png")
	# 贴地:站在边界西侧朝东看,接缝横在视野中间
	var cam_cell := Vector2i(int(floor(bx - 5.5)), int(floor(bz)))
	var eye := float(chunk.call("_height_at", cam_cell)) + 1.7
	cam.global_position = Vector3(bx - 5.5, eye, bz + 0.5)
	cam.look_at(Vector3(bx + 5.5, eye - 0.2, bz + 0.5), Vector3.UP)
	cam.make_current()
	for i in range(10):
		await process_frame
	await save_shot("section_seam_ground.png")
	# 柔和光影开 / 关对比(画质项):同一机位各一张。
	# 机位必须贴着玩家 —— 只有近景分段有面亮度差 + AO;远景是整块合并的
	# 纯色盒,开关看不出差别。朝前下方看,让台阶 / 墙面 / 草侧都进画面。
	var here_cell := Vector2i(int(floor(spot.x)), int(floor(spot.z)))
	var look_cell := here_cell + Vector2i(6, 0)
	var eye2 := float(chunk.call("_height_at", here_cell)) + 2.4
	cam.global_position = Vector3(spot.x, eye2, spot.z)
	cam.look_at(Vector3(float(look_cell.x) + 0.5,
		float(chunk.call("_height_at", look_cell)) + 0.8,
		float(look_cell.y) + 0.5), Vector3.UP)
	cam.make_current()
	for i in range(10):
		await process_frame
	await save_shot("soft_light_on.png")
	chunk.call("set_soft_light", false)
	for i in range(10):
		await process_frame
	await save_shot("soft_light_off.png")
	chunk.call("set_soft_light", true)
	for i in range(10):
		await process_frame
	# 猪(第一批):近景特写,确认粉色身体 / 四条腿 / 鼻头 / 尾巴
	var pig_found: Array = scene.find_children("猪1", "CharacterBody3D",
		true, false)
	if pig_found.size() > 0:
		var pig_at: Vector3 = (pig_found[0] as Node3D).global_position
		cam.global_position = pig_at + Vector3(2.0, 1.4, 2.0)
		cam.look_at(pig_at + Vector3(0.0, 0.55, 0.0), Vector3.UP)
		cam.make_current()
		for i in range(10):
			await process_frame
		await save_shot("pig.png")
		print("MINECRAFT_SHOT_PIG at=", pig_at)
	print("MINECRAFT_SHOTS=OK bx=", bx, " bz=", bz)
	quit()
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


def run_shots(godot, work):
    """带窗口跑一次截图(俯视 / 贴地)。失败只提示,不把主检查判红。"""
    out_dir = os.path.join(ROOT, "_dist", "minecraft_shots")
    os.makedirs(out_dir, exist_ok=True)
    env = dict(os.environ, GODOT_SILENCE_ROOT_WARNING="1")
    try:
        result = subprocess.run(
            [godot, "--path", work, "--script", "res://shots.gd",
             "--resolution", "1280x720"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env, timeout=300)
    except subprocess.TimeoutExpired:
        print("MINECRAFT_SHOTS=SKIP(超时)")
        return False
    text = (result.stdout or "") + (result.stderr or "")
    for line in text.splitlines():
        if "MINECRAFT_SHOT" in line:
            print(line.strip())
    if "MINECRAFT_SHOTS=OK" not in text:
        for line in text.splitlines():
            if "ERROR" in line:
                print("  " + line.strip())
        print("MINECRAFT_SHOTS=SKIP(带窗口渲染失败)")
        return False
    for name in ("section_seam_top.png", "section_seam_ground.png",
                 "soft_light_on.png", "soft_light_off.png", "pig.png"):
        src = os.path.join(work, "shots", name)
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(out_dir, name))
    print("MINECRAFT_SHOTS=", out_dir)
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
    if not os.path.isfile(LEGACY_SAVE):
        print(f"MINECRAFT=FAILED(缺少旧档 fixture:{LEGACY_SAVE})")
        return 1
    shutil.copyfile(LEGACY_SAVE, os.path.join(WORK,
                                              "legacy_save_format2.json"))
    with open(os.path.join(WORK, "probe.gd"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE)
    with open(os.path.join(WORK, "shots.gd"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(SHOTS)

    env = dict(os.environ, GODOT_SILENCE_ROOT_WARNING="1")
    # 第两百一十六轮:近景多了植被节点,无头跑完整条检查更慢;900 秒是
    # "卡死"的上限,不是性能指标(性能看 SAVE_SCRIPT 的分区统计)。
    common = dict(capture_output=True, text=True, encoding="utf-8",
                  errors="replace", env=env, timeout=900)
    subprocess.run([godot, "--headless", "--path", WORK, "--import"],
                   **common)
    result = subprocess.run(
        [godot, "--headless", "--path", WORK, "--script", "res://probe.gd"],
        **common)
    text = (result.stdout or "") + (result.stderr or "")
    for line in text.splitlines():
        if "MINECRAFT" in line or "ERROR" in line:
            print(line.strip())
    # P0 回归:进世界不许再刷这两类引擎错误(挂节点 / 不在树里取全局变换)
    bad = [line for line in text.splitlines()
           if "Parent node is busy setting up children" in line
           or "!is_inside_tree()" in line]
    if bad:
        for line in bad[:6]:
            print("  " + line.strip())
        print("MINECRAFT_ENGINE_ERRORS=", len(bad))
        print("MINECRAFT=FAILED")
        return 1
    if "MINECRAFT_CHECK=OK" in text:
        print("MINECRAFT=OK")
        run_shots(godot, WORK)
        return 0
    print("MINECRAFT=FAILED")
    for line in text.splitlines()[-18:]:
        print("  " + line.strip())
    return 1


if __name__ == "__main__":
    sys.exit(main())


