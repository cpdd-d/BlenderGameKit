extends Node
# 审计抓帧：固定机位出一张 PNG，然后退出。
#
# 由 scripts/check_preview_match.py **临时**拷进目标工程并注册成 autoload，
# 跑完就删、project.godot 也会还原。不要把它留在游戏工程里。
#
# 环境变量：
#   GK_AUDIT_OUT          输出 PNG（必填；为空就直接不干活）
#   GK_AUDIT_CAM_POS      "x,y,z"，默认 -38,6,2
#   GK_AUDIT_CAM_TARGET   "x,y,z"，默认 0,3,0
#   GK_AUDIT_FOV          垂直视角（度），默认 33.16644

func _ready() -> void:
	var out := OS.get_environment("GK_AUDIT_OUT")
	if out.is_empty():
		return
	var pos := _vec(OS.get_environment("GK_AUDIT_CAM_POS"), Vector3(-38.0, 6.0, 2.0))
	var target := _vec(OS.get_environment("GK_AUDIT_CAM_TARGET"), Vector3(0.0, 3.0, 0.0))
	var fov := 33.16644
	var fov_text := OS.get_environment("GK_AUDIT_FOV")
	if not fov_text.is_empty():
		fov = fov_text.to_float()

	# 等主场景真正进树（必要时再等几帧让环境脚本把后期层建好）
	for _index in 8:
		await get_tree().process_frame

	# 想换场景时（多数情况用不着：默认会跑工程自己的主场景）
	var scene_path := OS.get_environment("GK_AUDIT_SCENE")
	if not scene_path.is_empty():
		get_tree().change_scene_to_file(scene_path)
		for _index in 8:
			await get_tree().process_frame

	_hide_hud()

	var cam := get_viewport().get_camera_3d()
	if cam == null:
		cam = Camera3D.new()
		get_tree().root.add_child(cam)
	cam.global_position = pos
	cam.look_at(target, Vector3.UP)
	cam.fov = fov
	cam.current = true

	await get_tree().physics_frame
	await RenderingServer.frame_post_draw
	var image: Image = get_viewport().get_texture().get_image()
	if image == null:
		print("GK_AUDIT_FAILED=no_image")
		get_tree().quit(1)
		return
	var err := image.save_png(out)
	print("GK_AUDIT_SAVED=", out, " err=", err)
	get_tree().quit(0 if err == OK else 1)


func _hide_hud() -> void:
	# 只藏"界面层"：layer < 100。
	# 后期那张全屏 ColorRect 在 layer=100（见 asset_export 的 _gk_post_ready），
	# 必须留着，否则比的是"没后期"的画面，指标会假性变好。
	for node in get_tree().root.find_children("*", "CanvasLayer", true, false):
		if (node as CanvasLayer).layer < 100:
			(node as CanvasLayer).visible = false


func _vec(text: String, fallback: Vector3) -> Vector3:
	if text.is_empty():
		return fallback
	var parts := text.split(",")
	if parts.size() < 3:
		return fallback
	return Vector3(parts[0].to_float(), parts[1].to_float(), parts[2].to_float())
