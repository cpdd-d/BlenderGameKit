extends Node3D
## 主光 + 补光验证探针。
##
## 主光从正面照,补光从左侧照。关掉补光(Fill_off)时球的左侧是主光阴影;
## 打开补光(Fill_on)后左侧应明显变亮。沿球左侧取一块小区域算平均亮度,
## 两个材质的差值就是 `light_dir_2` 真的参与着色的证据。
##
## 跑法(真 GPU,headless 是 dummy 渲染器):
##     godot --path examples/demo_render res://fill.tscn --resolution 480x480

const MATERIALS := [
	["off", "res://materials/Fill_off.tres"],
	["on", "res://materials/Fill_on.tres"],
]

@export var settle_frames := 5

var _index := 0
var _frame := 0


func _ready() -> void:
	_apply(0)


func _apply(index: int) -> void:
	var ball := get_node_or_null("FillBall") as MeshInstance3D
	if ball != null:
		ball.material_override = load(MATERIALS[index][1])
	_frame = 0


func _process(_delta: float) -> void:
	_frame += 1
	if _frame < settle_frames:
		return
	var image: Image = get_viewport().get_texture().get_image()
	var total := 0.0
	var count := 0
	for x in range(90, 131, 2):
		for y in range(210, 271, 2):
			var px := image.get_pixel(x, y)
			total += (px.r + px.g + px.b) / 3.0
			count += 1
	var average := total / maxf(float(count), 1.0)
	print("GK_FILL name=", MATERIALS[_index][0], " left=", "%.4f" % average)
	_index += 1
	if _index >= MATERIALS.size():
		print("GK_FILL=DONE")
		get_tree().quit()
		return
	_apply(_index)
