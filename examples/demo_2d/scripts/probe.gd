extends Node
# 由 make_demo.py 生成:在**真 Godot**里模拟按键,验证节点图真的产生了行为。
#
# 用户 2026-10-03:"你可以在一个平面上创建2D示例,然后用节点图来进行游戏设计,
# 要求是尽量包含上面的设计好,但我现在验证不了的节点" —— 这个探针就是答案:
# 不靠肉眼看画面,而是 `Input.action_press()` 模拟输入 + 读回游戏状态来断言。
#
# ── 时间累加写在 _process 里(踩过的坑)────────────────────────────
# 第一版把累加放进了"遍历所有时间点"的循环里,于是每帧加 6 次 delta,
# 时间跑得比现实快好几倍 —— 量出来的"跳跃上升 418 像素"纯属幻觉。

var player: Node2D = null
var start_x := 0.0
var jump_start_y := 0.0
var jump_peak_y := 99999.0
var score_before := -1.0
var score_after := -1.0
var bullets := 0
var label_text := "<none>"
var camera_name := "<none>"
var moved_x := 0.0
var jump_rise := 0.0

var t := 0.0
var marks := [0.3, 0.7, 1.0, 1.35, 1.6, 1.9]
var done := {}

func _ready() -> void:
	player = get_parent().get_node_or_null("Player")
	if player != null:
		start_x = player.position.x

# ── 用 _physics_process 而不是 _process(踩过的坑)────────────────
# 无头(headless)模式一帧只花一两毫秒,`_process` 的 delta 是**真实耗时**,
# 180 帧加起来还不到 0.5 秒 —— 探针的时间点一个都到不了,于是
# "全都没输出"。物理帧的 delta 是**固定的 1/60**,180 帧正好 3 秒,
# 和机器快慢无关。
func _physics_process(delta: float) -> void:
	t += delta
	if player != null and t > 1.0 and t < 1.35:
		jump_peak_y = minf(jump_peak_y, player.position.y)
	for mark in marks:
		if t >= mark and not done.has(mark):
			done[mark] = true
			_step(mark)
	if t > 2.0:
		print("PROBE2D_DONE")
		set_physics_process(false)

func _step(mark: float) -> void:
	match mark:
		0.3:
			Input.action_press("key_d")          # 往右走(节点图捕获的键)
		0.7:
			Input.action_release("key_d")
			moved_x = player.position.x - start_x
		1.0:
			jump_start_y = player.position.y
			Input.action_press("jump")           # 跳
		1.35:
			Input.action_release("jump")
			jump_rise = jump_start_y - jump_peak_y
			score_before = _score()
			if player.has_method("on_attack_pressed"):
				player.call("on_attack_pressed")
		1.6:
			score_after = _score()
			bullets = _count_bullets()
		1.9:
			label_text = _find_label(get_parent())
			if label_text == "<none>":
				label_text = _find_label(player)
			camera_name = _camera_name()
			print("PROBE2D moved_x=", "%.1f" % moved_x)
			print("PROBE2D jump_rise=", "%.1f" % jump_rise)
			print("PROBE2D score=", "%.1f" % score_before, " -> ",
				"%.1f" % score_after)
			print("PROBE2D bullets=", bullets)
			print("PROBE2D label=", label_text)
			print("PROBE2D camera=", camera_name)

func _score() -> float:
	var value = player.get("score")
	return 0.0 if value == null else float(value)

func _count_bullets() -> int:
	var count := 0
	for child in get_parent().get_children():
		if String(child.name).begins_with("Bullet"):
			count += 1
	return count

func _find_label(root: Node) -> String:
	# UI 是节点图自己建的(CanvasLayer -> Control -> Label),递归找
	for child in root.get_children():
		if child is Label:
			return String(child.text)
		var found := _find_label(child)
		if found != "<none>":
			return found
	return "<none>"

func _camera_name() -> String:
	var cam := get_viewport().get_camera_2d()
	return String(cam.name) if cam != null else "<none>"
