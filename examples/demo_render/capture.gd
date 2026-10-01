extends Node3D
## 渲染验证脚本。
##
## 两个用途:
##   1. 出图:把当前画面存成 PNG,并用像素统计判断"真的画出了东西"
##   2. 扫光:让主光绕角色转一圈,在多个角度各出一张图
##      —— 这是验证面部阴影 SDF 是否真的在"滑动"的唯一可靠方式
##
## 用法(见 scripts/render_shots.ps1):
##     godot --path examples/demo_render --resolution 640x640
## 环境变量:
##     GK_RENDER_OUT    单张输出路径
##     GK_SWEEP=1       开启扫光模式,输出到 <工程目录>/shots/light_XXX.png
##     GK_SWEEP_STEP    角度步长(度),默认 30

@export var warmup_frames := 6
@export var settle_frames := 4

var _frame := 0
var _shots_done := false
var _sweep := false
var _sweep_requested := false
var _angles: PackedFloat32Array = PackedFloat32Array()
var _angle_index := 0
var _shots_dir := ""
var _key_light: DirectionalLight3D
var _base_basis := Transform3D()


func _ready() -> void:
	# 场景里同时挂着抖动验证探针(jitter_probe.gd),它要连续抓很多帧。
	# 两个脚本抢着 quit() 会把对方打断 —— 实测抖动探针只走到 "start" 就没了。
	#
	# 优先级:**显式要求的任务优先**。
	#   扫光模式(GK_SWEEP)是显式请求 -> 让抖动探针让路
	#   否则(没人显式要求扫光)-> 本脚本让路给抖动探针
	_sweep_requested = OS.get_environment("GK_SWEEP") != ""
	if not _sweep_requested:
		var jitter_probe := get_node_or_null("JitterProbe")
		if jitter_probe != null:
			print("GK_RENDER 检测到抖动探针,本脚本改为被动模式")
			set_process(false)
			return

	_sweep = _sweep_requested
	var step := 30.0
	if OS.get_environment("GK_SWEEP_STEP") != "":
		step = float(OS.get_environment("GK_SWEEP_STEP"))
	# 也可以直接给一串角度(GK_SWEEP_ANGLES="60,75,90,105,120")
	# —— 用于密集采样"脸上确实有明暗分界"的区间
	var listed := OS.get_environment("GK_SWEEP_ANGLES")
	if listed != "":
		for part in listed.split(","):
			var text := part.strip_edges()
			if text != "":
				_angles.append(float(text))
	else:
		var count := int(round(360.0 / step))
		for i in range(count):
			_angles.append(i * step)

	_shots_dir = OS.get_environment("GK_SHOTS_DIR")
	if _shots_dir == "":
		_shots_dir = ProjectSettings.globalize_path("res://shots")
	_key_light = get_node_or_null("KeyLight")
	if _key_light == null:
		_sweep = false
	else:
		_base_basis = _key_light.transform
	DirAccess.make_dir_recursive_absolute(_shots_dir)
	if _sweep:
		print("GK_SWEEP 共 ", _angles.size(), " 个角度: ", _angles)


func _process(_delta: float) -> void:
	_frame += 1
	if _shots_done:
		return

	if not _sweep:
		if _frame < warmup_frames:
			return
		_shots_done = true
		_capture(OS.get_environment("GK_RENDER_OUT"))
		get_tree().quit()
		return

	# 扫光模式:每个角度先转灯,等几帧让它稳定,再出图
	if _frame < warmup_frames:
		return
	var delta_frames := _frame - warmup_frames
	if delta_frames % settle_frames != 0:
		return

	_angle_index = delta_frames / settle_frames
	if _angle_index >= _angles.size():
		_shots_done = true
		print("GK_SWEEP=DONE 共 ", _angles.size(), " 张")
		get_tree().quit()
		return

	var angle := _angles[_angle_index]
	_apply_light_angle(angle)
	# 等下一帧再抓,确保这一帧已经用新角度渲染过
	await get_tree().process_frame
	var path := _shots_dir.path_join("light_%03d.png" % int(round(angle)))
	_capture(path)


func _apply_light_angle(degrees: float) -> void:
	# 绕 Y 轴旋转主光:角度 0 = 光从正前方来,90 = 从画面右侧来
	var radians := degrees * (3.14159265 / 180.0)
	var rot := Basis(Vector3.UP, radians)
	var basis := _base_basis.basis * rot
	_key_light.transform = Transform3D(basis, _base_basis.origin)
	# 光从背后时把补光关掉,避免面部 SDF 的判断被补光干扰
	var behind := cos(radians) < -0.2
	var fill := get_node_or_null("RimLight")
	if fill:
		fill.light_energy = 0.0 if behind else 0.16


func _capture(path: String) -> void:
	var image: Image = get_viewport().get_texture().get_image()
	if path == "":
		path = "user://npr_demo.png"
	var err := image.save_png(path)

	var non_black := 0
	var total := 0
	var lit := 0
	var dark := 0
	for y in range(0, image.get_height(), 3):
		for x in range(0, image.get_width(), 3):
			total += 1
			var px := image.get_pixel(x, y)
			var v := (px.r + px.g + px.b) / 3.0
			if v > 0.03:
				non_black += 1
			if v > 0.45:
				lit += 1
			elif v > 0.05:
				dark += 1

	print("GK_RENDER size=", image.get_width(), "x", image.get_height())
	print("GK_RENDER saved=", err == OK, " path=", path)
	print("GK_RENDER non_black=", non_black, "/", total)
	print("GK_RENDER lit=", lit, " dark=", dark)
	print("GK_RENDER=DONE")
