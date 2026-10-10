extends Node3D
## 高光贴图的验证探针(第一百零一轮)。
##
## ── 两个模式(靠环境变量分)────────────────────────────────────────
##   GK_SPEC_CHECK=1  无头:把三份材质读回来,验"接线"
##                    (材质能加载 / 着色器在 / 贴图真的绑上了 / 开关对了)
##   (否则)            真窗口:同一颗球**换三份材质各出一张图**,数亮点
##
## ── 为什么必须分两个模式 ──────────────────────────────────────────
## "资源接上了没有"无头就能验(加载 .tres 拿 get_shader_parameter);
## 但"高光真的从实心变成空心 / 被遮罩抹掉"是**像素级**的事,
## `--headless` 是 dummy 渲染器画不出东西 —— 只能真 GPU 出图。
##
## ── 三份材质差在哪(其它参数完全一样)────────────────────────────
##   Spec_plain    没有任何贴图        -> 一个实心高光斑
##   Spec_ring     形状图 = 左白右黑   -> 高光只剩一圈(空心环)
##   Spec_blocked  遮罩图 = 全黑       -> 高光被完全抹掉
## 亮点数应该是 plain > ring > blocked(blocked 接近 0),这就是
## "高光形状真的由贴图决定"的证据。

const MATERIALS := [
	["plain", "res://materials/Spec_plain.tres"],
	["ring", "res://materials/Spec_ring.tres"],
	["blocked", "res://materials/Spec_blocked.tres"],
]

@export var settle_frames := 5

var _mode := "render"
var _frame := 0
var _index := 0


func _ready() -> void:
	if OS.get_environment("GK_SPEC_CHECK") != "":
		_mode = "check"
		_check()
		get_tree().quit()
		return
	_apply(0)


func _check() -> void:
	# 无头:只验"接线" —— 材质 / 着色器 / 贴图 / 开关
	for entry in MATERIALS:
		var name: String = entry[0]
		var material: ShaderMaterial = load(entry[1])
		if material == null:
			print("GK_SPEC_CHECK name=", name, " loaded=false")
			continue
		var shape = material.get_shader_parameter("specular_shape_texture")
		var mask = material.get_shader_parameter("specular_mask_texture")
		print("GK_SPEC_CHECK name=", name,
			" loaded=true",
			" has_shader=", material.shader != null,
			" amount=", material.get_shader_parameter("specular_amount"),
			" size=", material.get_shader_parameter("specular_size"),
			" use_shape=", material.get_shader_parameter(
				"use_specular_shape_texture"),
			" use_mask=", material.get_shader_parameter(
				"use_specular_mask_texture"),
			" shape_w=", shape.get_width() if shape is Texture2D else -1,
			" mask_w=", mask.get_width() if mask is Texture2D else -1)
	print("GK_SPEC_CHECK=DONE")


func _apply(index: int) -> void:
	var ball := get_node_or_null("SpecBall") as MeshInstance3D
	if ball != null:
		ball.material_override = load(MATERIALS[index][1])
	_frame = 0


func _process(_delta: float) -> void:
	if _mode != "render":
		return
	_frame += 1
	if _frame < settle_frames:
		return
	var image: Image = get_viewport().get_texture().get_image()
	var bright := 0
	var lit := 0
	var total := 0
	var peak := 0.0
	for y in range(0, image.get_height(), 2):
		for x in range(0, image.get_width(), 2):
			total += 1
			var px := image.get_pixel(x, y)
			var v := (px.r + px.g + px.b) / 3.0
			peak = max(peak, v)
			if v > 0.03:
				lit += 1
			if v > 0.80:
				bright += 1
	# lit = 画面里"有东西"的格子数(球本身),peak = 最亮的那个格子。
	# 光有 lit 没有 bright 说明球画出来了但**高光没出来** —— 这两个数
	# 分开报,排查时一眼能看出是哪一种(实测踩过:只报 bright 时
	# "球没画出来"和"高光没出来"长得一模一样)。
	print("GK_SPEC name=", MATERIALS[_index][0], " bright=", bright,
		" lit=", lit, " total=", total, " peak=", "%.3f" % peak,
		" size=", image.get_width(), "x", image.get_height())
	_index += 1
	if _index >= MATERIALS.size():
		print("GK_SPEC=DONE")
		get_tree().quit()
		return
	_apply(_index)
