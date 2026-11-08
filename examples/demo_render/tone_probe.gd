extends Node3D
## 两色调 / 三色调分档探针。
##
## 一颗球、纯色背景、光正对相机;沿画面中线扫一行,数"出现得够多的
## 量化颜色"就是明暗档数。
##   two   -> 应该正好 2 档(受光 / 阴影)
##   three -> 应该 3 档(受光 / 阴影 / 深阴影)
## 三色调如果退化成两档,这里会直接报 bands=2 —— 单张图看不出来,
## 数字能看出来。
##
## 跑法(真 GPU,headless 是 dummy 渲染器):
##     godot --path examples/demo_render res://tone.tscn --resolution 480x480

const MATERIALS := [
	["two", "res://materials/Tone_two.tres"],
	["three", "res://materials/Tone_three.tres"],
]

@export var settle_frames := 5

var _index := 0
var _frame := 0


func _ready() -> void:
	_apply(0)


func _apply(index: int) -> void:
	var ball := get_node_or_null("ToneBall") as MeshInstance3D
	if ball != null:
		ball.material_override = load(MATERIALS[index][1])
	_frame = 0


func _process(_delta: float) -> void:
	_frame += 1
	if _frame < settle_frames:
		return
	var image: Image = get_viewport().get_texture().get_image()
	var y := image.get_height() / 2
	var counts := {}
	var total := 0
	for x in range(0, image.get_width()):
		var px := image.get_pixel(x, y)
		var value := (px.r + px.g + px.b) / 3.0
		if value < 0.06:
			continue
		var key := "%d,%d,%d" % [
			int(round(px.r * 24.0)),
			int(round(px.g * 24.0)),
			int(round(px.b * 24.0)),
		]
		counts[key] = int(counts.get(key, 0)) + 1
		total += 1
	var bands := 0
	var detail: Array[String] = []
	for key in counts.keys():
		if int(counts[key]) >= 8:
			bands += 1
			detail.append("%s:%d" % [key, int(counts[key])])
	print("GK_TONE name=", MATERIALS[_index][0], " bands=", bands,
		" px=", total, " colors=", ",".join(detail))
	_index += 1
	if _index >= MATERIALS.size():
		print("GK_TONE=DONE")
		get_tree().quit()
		return
	_apply(_index)
