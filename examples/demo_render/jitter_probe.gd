extends Node3D
## 手绘描边动画验证:连续抓帧,用像素差证明"线真的在动"。
##
## 为什么需要专门的脚本
## --------------------
## 单张静态图**无法**验证动画。这个脚本:
##   1. 把背景刷成亮色,于是描边成为深色像素,便于统计
##   2. 按环境变量设置描边材质的抖动参数(开 / 关)
##   3. 连续抓帧,统计每帧深色像素数(线宽是否在呼吸)
##      以及相邻帧的像素平均差(线的位置是否在动)
##
## 环境变量:
##   GK_JITTER_OUT     输出目录(仅用于记录,当前不落盘图片)
##   GK_JITTER_FRAMES  抓多少帧(默认 120)
##   GK_JITTER_SAMPLE  每几帧抓一次(默认 1)
##   GK_JITTER_SPEED   时间倍速(默认 1.0;设 0 冻结时间做对照)
##   GK_JITTER_ENABLE  1 = 开抖动,0 = 关抖动
##   GK_JITTER_FPS     抖动帧率(默认 10)

@export var frames_to_capture := 120
@export var sample_every := 3

var _frame := 0
var _samples: Array = []
var _enabled := true
var _jitter_fps := 10.0
var _shots_saved := 0


func _ready() -> void:
	# 扫光是显式请求的任务,优先级更高:让路,别去改材质和背景。
	if OS.get_environment("GK_SWEEP") != "":
		print("GK_JITTER 检测到扫光模式,本脚本让路")
		set_process(false)
		return

	frames_to_capture = int(_env("GK_JITTER_FRAMES", str(frames_to_capture)))
	sample_every = int(_env("GK_JITTER_SAMPLE", str(sample_every)))
	_enabled = _env("GK_JITTER_ENABLE", "1") != "0"
	_jitter_fps = float(_env("GK_JITTER_FPS", "10"))
	Engine.time_scale = float(_env("GK_JITTER_SPEED", "1.0"))

	_make_bright_background()
	_configure_outline()

	print("GK_JITTER start frames=", frames_to_capture, " sample=", sample_every,
			" enabled=", _enabled, " fps=", _jitter_fps,
			" speed=", Engine.time_scale, " render_fps=", Engine.max_fps)


func _env(key: String, fallback: String) -> String:
	var value := OS.get_environment(key)
	return fallback if value == "" else value


func _make_bright_background() -> void:
	# 亮背景是必需的:深色描边在深色背景上统计不出来
	var world := WorldEnvironment.new()
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.92, 0.93, 0.96)
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.75, 0.78, 0.85)
	env.ambient_light_energy = 0.6
	env.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	world.environment = env
	add_child(world)

	# 场景里原本的 WorldEnvironment(深色)会被后加的覆盖,所以把旧的关掉
	for node in get_tree().get_nodes_in_group("gk_old_env"):
		pass
	var root := get_parent()
	if root:
		for child in root.get_children():
			if child is WorldEnvironment and child != world:
				(child as WorldEnvironment).environment = null


func _configure_outline() -> void:
	## 找到 Hero 的基础材质,设置它的 Next Pass(描边)参数
	var root := get_parent()
	if root == null:
		print("GK_JITTER 警告: 找不到根节点")
		return
	var hero := root.get_node_or_null("Hero")
	if hero == null or hero.material_override == null:
		print("GK_JITTER 警告: 找不到 Hero 或它的材质")
		return

	# 用显式类型标注:base 是 Material 基类,`:=` 推断不出类型会解析失败
	var base_material: Material = hero.material_override
	var outline: ShaderMaterial = null
	if base_material is ShaderMaterial:
		var shader_material := base_material as ShaderMaterial
		var next: Material = shader_material.next_pass
		if next is ShaderMaterial:
			outline = next as ShaderMaterial
	if outline == null:
		print("GK_JITTER 警告: Hero 材质没有 Next Pass 描边")
		return

	# 让描边更粗、颜色更深,便于统计。
	# 幅度也给得比较大 —— 这是**验证**用的参数,目的是让抖动肉眼可见;
	# 实际游戏里建议 jitter_width 0.2~0.35、jitter_offset 0.003~0.006
	# (见 docs/渲染-NPR卡通着色.md 的"手绘描边"一节)。
	outline.set_shader_parameter("outline_width", 0.055)
	outline.set_shader_parameter("outline_color", Color(0.05, 0.04, 0.08, 1.0))
	outline.set_shader_parameter("outline_only", 1.0)
	outline.set_shader_parameter("jitter_enable", 1.0 if _enabled else 0.0)
	outline.set_shader_parameter("jitter_fps", _jitter_fps)
	outline.set_shader_parameter("jitter_width", 0.85)
	outline.set_shader_parameter("jitter_offset", 0.030)
	outline.set_shader_parameter("jitter_scale", 5.0)
	outline.set_shader_parameter("jitter_vertex_seed", 0.9)

	# 关掉球的描边干扰:只留胶囊,Hero 之外的材质换成纯色
	for child in root.get_children():
		if child is MeshInstance3D and child.name != "Hero":
			var plain := StandardMaterial3D.new()
			plain.albedo_color = Color(0.88, 0.89, 0.93)
			(child as MeshInstance3D).material_override = plain
	# 地面也刷亮
	var floor_node := root.get_node_or_null("Floor")
	if floor_node is MeshInstance3D:
		var fm := StandardMaterial3D.new()
		fm.albedo_color = Color(0.88, 0.89, 0.93)
		(floor_node as MeshInstance3D).material_override = fm

	print("GK_JITTER outline width=", outline.get_shader_parameter("outline_width"),
			" jitter_enable=", outline.get_shader_parameter("jitter_enable"))


func _process(_delta: float) -> void:
	_frame += 1
	if _frame % sample_every != 0:
		return

	var image: Image = get_viewport().get_texture().get_image()
	_samples.append(_analyze(image))

	# 顺手存几张图:像素差能**证明**线在动,但"动得好不好看"只能靠眼睛。
	var shot_every := int(_env("GK_JITTER_SHOT_EVERY", "0"))
	if shot_every > 0 and _shots_saved < 8 and _samples.size() % shot_every == 0:
		var dir := _env("GK_JITTER_SHOT_DIR", ProjectSettings.globalize_path("res://jitter_shots"))
		DirAccess.make_dir_recursive_absolute(dir)
		var path := dir.path_join("shot_%02d.png" % _shots_saved)
		var err := image.save_png(path)
		print("GK_JITTER shot saved=", err == OK, " -> ", path)
		_shots_saved += 1

	if _samples.size() >= frames_to_capture:
		_report()
		get_tree().quit()


func _analyze(image: Image) -> Dictionary:
	## 在画面中央区域统计深色像素(描边),并留一份缩略像素用于帧间差分
	var w := image.get_width()
	var h := image.get_height()
	var dark := 0
	var total := 0
	var thumb := PackedFloat32Array()

	for y in range(int(h * 0.30), int(h * 0.70), 2):
		for x in range(int(w * 0.35), int(w * 0.65), 2):
			var px := image.get_pixel(x, y)
			var v := (px.r + px.g + px.b) / 3.0
			total += 1
			if v < 0.30:
				dark += 1
			thumb.append(v)

	return {"dark": dark, "total": total, "thumb": thumb}


func _report() -> void:
	var n := _samples.size()
	if n < 3:
		print("GK_JITTER=FAILED 采样帧不足 n=", n)
		return

	var dark_min: int = _samples[0]["dark"]
	var dark_max: int = _samples[0]["dark"]
	for s in _samples:
		dark_min = min(dark_min, int(s["dark"]))
		dark_max = max(dark_max, int(s["dark"]))
	var dark_span := dark_max - dark_min

	var diff_sum := 0.0
	var diff_max := 0.0
	var changed := 0
	for i in range(1, n):
		var a: PackedFloat32Array = _samples[i - 1]["thumb"]
		var b: PackedFloat32Array = _samples[i]["thumb"]
		var count: int = mini(a.size(), b.size())
		var acc := 0.0
		for k in range(count):
			acc += abs(a[k] - b[k])
		var mean := acc / float(maxi(1, count))
		diff_sum += mean
		diff_max = max(diff_max, mean)
		if mean > 0.0006:
			changed += 1

	var diff_mean := diff_sum / float(maxi(1, n - 1))

	print("GK_JITTER frames=", n)
	# 注意键名要互相不包含,否则外部脚本用正则取数会撞车:
	# "diff max=" 会先匹配上 "dark min=23 max=40" 里的 max=,取到 40 而不是
	# 帧间差。所以这里统一用 d_ / line_ 前缀做命名空间。
	print("GK_JITTER line_min=", dark_min, " line_max=", dark_max, " line_span=", dark_span)
	print("GK_JITTER d_mean=", "%.6f" % diff_mean, " d_max=", "%.6f" % diff_max)
	print("GK_JITTER d_changed=", changed, " d_total=", n - 1)
	print("GK_JITTER=DONE")
