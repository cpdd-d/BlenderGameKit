"""《七色》的自定义 GDScript 文本块。

这些字符串会被 `make_shichise.py` 写进 Blender 文本块,再由『脚本_加载』
导出到 `res://scripts/custom/`。放在单独文件里是为了让脚本好读、好改;
生成脚本仍然只认这里一份(别去手改导出后的 .gd)。

三个脚本:
    WORLD_SCRIPT  —— 关卡世界:灰白 / 赤色状态、火盆、检查点、对话、HUD
    ENEMY_SCRIPT  —— 敌人管理:巡逻 / 追击 / 攻击 / 色核 / BOSS 三阶段
    PLAYER_SCRIPT —— 玩家战斗:三连击 / 闪避 / 色技 / 软锁定 / 受伤

── 坐标约定(容易踩)─────────────────────────────────────────────
Blender (x, y, z) 导出到 Godot 是 (x, z, -y)。所以村庄在 Blender
(70, -82) 到了 Godot 是 (70, y, +82);赤之塔在 Blender (-65, 85)
到了 Godot 是 (-65, y, -85)。脚本里凡是硬编码坐标,都按 Godot 写。
"""

WORLD_SCRIPT = r"""extends Node

# ── 《七色》世界脚本 ─────────────────────────────────────────────
# 挂在关卡根节点上(由『脚本_加载』实例化)。负责:
#   * 灰白 / 赤色的环境饱和度
#   * 火盆解谜、塔门、检查点、存档
#   * 对话面板、交互提示、HUD(生命 / 色力 / BOSS 条)
#   * 玩家死亡后回到最近篝火

const SAVE_PATH := "user://gk_shichise.json"
const VILLAGE_SPAWN := Vector3(70.0, 1.0, 82.0)
const RESTORED_SATURATION := 1.5

var env: Environment
var player: CharacterBody3D
var player_script: Node

var red_restored := false
var has_torch := false
var lia_talked := false
var boss_dead := false
var braziers_lit := 0
var _lit := [false, false, false]

var checkpoint_name := "村口篝火"
var checkpoint_pos := VILLAGE_SPAWN

var dialogue_active := false
var _lines: Array = []
var _line_index := 0
var _done := Callable()

var _near := ""
var _near_label := ""
var _hint_time := 0.0
var _death_active := false
var _echo_tween: Tween
var _flash_tween: Tween

var hud: CanvasLayer
var hp_fill: ColorRect
var gauge_fill: ColorRect
var prompt: Label
var hint: Label
var boss_root: Control
var boss_fill: ColorRect
var boss_name: Label
var dialog_panel: Panel
var dialog_speaker: Label
var dialog_text: Label
var dialog_tip: Label
var cinematic: Label
var flash: ColorRect
var death_root: Control


func _ready() -> void:
	add_to_group("gk_world")
	call_deferred("_setup")


func _setup() -> void:
	await get_tree().process_frame
	var host := get_parent()
	var we := host.get_node_or_null("WorldEnvironment") as WorldEnvironment
	if we != null:
		env = we.environment
	player = get_tree().get_first_node_in_group("gk_player") as CharacterBody3D
	if player == null:
		player = host.get_node_or_null("Colliders/艾尔文") as CharacterBody3D
	if player != null:
		for child in player.get_children():
			if child.name.begins_with("GKScript_") and child.has_method("take_damage"):
				player_script = child
	_build_ui()
	_attach_named_mesh(host, "赤塔门")
	_load_game()
	if red_restored and env != null:
		env.adjustment_enabled = true
		env.adjustment_saturation = RESTORED_SATURATION
	_open_gate_if_ready()
	set_hint("莉娅在村口篝火旁等你")


# ── 小工具 ──────────────────────────────────────────────────────

func _rect(parent: Control, pos: Vector2, size: Vector2, color: Color) -> ColorRect:
	var rect := ColorRect.new()
	rect.position = pos
	rect.size = size
	rect.color = color
	rect.mouse_filter = Control.MOUSE_FILTER_IGNORE
	parent.add_child(rect)
	return rect


func _label(parent: Control, text: String, pos: Vector2, size: Vector2,
		font_size: int, color: Color) -> Label:
	var label := Label.new()
	label.text = text
	label.position = pos
	label.size = size
	label.add_theme_font_size_override("font_size", font_size)
	label.add_theme_color_override("font_color", color)
	label.add_theme_color_override("font_outline_color", Color(0.0, 0.0, 0.0, 0.78))
	label.add_theme_constant_override("outline_size", 3)
	label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	parent.add_child(label)
	return label


func _attach_named_mesh(host: Node, obj_name: String) -> void:
	# 世界的网格在 glb 里、碰撞体在 Colliders 里 —— 两个同名节点。
	# 把 glb 那一个挂到碰撞体下,移动碰撞体时画面才会跟着动。
	var body := host.get_node_or_null("Colliders/" + obj_name) as Node3D
	if body == null:
		return
	for node in host.find_children(obj_name, "", true, false):
		if node == body:
			continue
		if node is Node3D and node.get_parent() != body:
			node.reparent(body, true)
			return


# ── UI ──────────────────────────────────────────────────────────

func _build_ui() -> void:
	hud = CanvasLayer.new()
	hud.layer = 6
	add_child(hud)
	var root := Control.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	hud.add_child(root)

	_label(root, "生命", Vector2(24, 26), Vector2(240, 22), 15, Color(0.92, 0.92, 0.94, 1.0))
	_rect(root, Vector2(24, 50), Vector2(240, 14), Color(0.05, 0.05, 0.07, 0.85))
	hp_fill = _rect(root, Vector2(26, 52), Vector2(236, 10), Color(0.82, 0.18, 0.15, 1.0))
	_label(root, "色力", Vector2(24, 68), Vector2(240, 20), 13, Color(0.96, 0.78, 0.5, 1.0))
	_rect(root, Vector2(24, 90), Vector2(240, 10), Color(0.05, 0.05, 0.07, 0.85))
	gauge_fill = _rect(root, Vector2(26, 91), Vector2(236, 8), Color(0.96, 0.52, 0.18, 1.0))

	boss_root = Control.new()
	boss_root.position = Vector2(340, 66)
	boss_root.size = Vector2(600, 54)
	boss_root.visible = false
	boss_root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	root.add_child(boss_root)
	boss_name = _label(boss_root, "赤之骑士团长", Vector2(0, 0), Vector2(600, 26), 21,
			Color(1.0, 0.86, 0.74, 1.0))
	boss_name.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_rect(boss_root, Vector2(0, 30), Vector2(600, 14), Color(0.06, 0.05, 0.05, 0.9))
	boss_fill = _rect(boss_root, Vector2(2, 32), Vector2(596, 10), Color(0.88, 0.2, 0.14, 1.0))

	var prompt_layer := CanvasLayer.new()
	prompt_layer.layer = 15
	add_child(prompt_layer)
	var proot := Control.new()
	proot.set_anchors_preset(Control.PRESET_FULL_RECT)
	proot.mouse_filter = Control.MOUSE_FILTER_IGNORE
	prompt_layer.add_child(proot)
	prompt = _label(proot, "", Vector2(0, 596), Vector2(1280, 34), 22,
			Color(1.0, 0.9, 0.66, 1.0))
	prompt.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	hint = _label(proot, "", Vector2(0, 632), Vector2(1280, 30), 18,
			Color(0.92, 0.9, 0.86, 1.0))
	hint.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER

	dialog_panel = Panel.new()
	dialog_panel.position = Vector2(190, 470)
	dialog_panel.size = Vector2(900, 196)
	dialog_panel.visible = false
	var style := StyleBoxFlat.new()
	style.bg_color = Color(0.05, 0.05, 0.08, 0.94)
	style.border_color = Color(0.5, 0.18, 0.12, 1.0)
	style.set_border_width_all(2)
	style.set_corner_radius_all(8)
	dialog_panel.add_theme_stylebox_override("panel", style)
	root.add_child(dialog_panel)
	dialog_speaker = _label(dialog_panel, "", Vector2(24, 12), Vector2(850, 28), 23,
			Color(1.0, 0.7, 0.45, 1.0))
	dialog_text = _label(dialog_panel, "", Vector2(24, 50), Vector2(840, 100), 20,
			Color(0.95, 0.95, 0.95, 1.0))
	dialog_text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	dialog_tip = _label(dialog_panel, "[E] 继续", Vector2(760, 154), Vector2(120, 26), 16,
			Color(0.68, 0.68, 0.7, 1.0))

	var overlay := CanvasLayer.new()
	overlay.layer = 25
	add_child(overlay)
	var oroot := Control.new()
	oroot.set_anchors_preset(Control.PRESET_FULL_RECT)
	oroot.mouse_filter = Control.MOUSE_FILTER_IGNORE
	overlay.add_child(oroot)
	cinematic = _label(oroot, "", Vector2(0, 280), Vector2(1280, 100), 58,
			Color(1.0, 0.95, 0.9, 1.0))
	cinematic.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	flash = _rect(oroot, Vector2.ZERO, get_viewport().get_visible_rect().size,
			Color(1.0, 1.0, 1.0, 0.0))
	flash.visible = false
	death_root = Control.new()
	death_root.set_anchors_preset(Control.PRESET_FULL_RECT)
	death_root.visible = false
	death_root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	oroot.add_child(death_root)
	_rect(death_root, Vector2.ZERO, get_viewport().get_visible_rect().size,
			Color(0.02, 0.01, 0.01, 0.72))
	var death_label := _label(death_root, "你倒下了\n正在回到最近的篝火……",
			Vector2(0, 280), Vector2(1280, 120), 34, Color(0.95, 0.9, 0.86, 1.0))
	death_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER


func _update_hud() -> void:
	if player_script == null:
		return
	if hp_fill != null:
		hp_fill.size.x = 236.0 * clamp(
				float(player_script.hp) / max(1.0, float(player_script.max_hp)), 0.0, 1.0)
	if gauge_fill != null:
		gauge_fill.size.x = 236.0 * clamp(
				float(player_script.gauge) / max(1.0, float(player_script.max_gauge)), 0.0, 1.0)


func set_hint(text: String) -> void:
	if hint != null:
		hint.text = text
	_hint_time = 3.6


func set_hp(value: float, max_value: float) -> void:
	if hp_fill == null:
		return
	hp_fill.size.x = 236.0 * clamp(value / max(1.0, max_value), 0.0, 1.0)


func set_gauge(value: float, max_value: float) -> void:
	if gauge_fill == null:
		return
	gauge_fill.size.x = 236.0 * clamp(value / max(1.0, max_value), 0.0, 1.0)


func show_boss(label: String, hp: float, max_hp: float) -> void:
	if boss_root == null:
		return
	boss_root.visible = true
	boss_name.text = label
	update_boss(hp, max_hp)


func update_boss(hp: float, max_hp: float) -> void:
	if boss_fill == null:
		return
	boss_fill.size.x = 596.0 * clamp(hp / max(1.0, max_hp), 0.0, 1.0)


func hide_boss() -> void:
	if boss_root != null:
		boss_root.visible = false


# ── 每帧:交互目标 / HUD ─────────────────────────────────────────

func _physics_process(delta: float) -> void:
	_hint_time = max(0.0, _hint_time - delta)
	if _hint_time <= 0.0 and hint != null:
		hint.text = ""
	_refresh_near()
	_update_hud()


func _refresh_near() -> void:
	if player == null or _death_active:
		return
	var host := get_parent()
	var names := [
		["莉娅", "和莉娅说话"],
		["火盆1", "点燃火盆"], ["火盆2", "点燃火盆"], ["火盆3", "点燃火盆"],
		["赤塔门", "推开塔门"],
	]
	var best := ""
	var best_label := ""
	var best_dist := 3.2
	for pair in names:
		var node := host.find_child(pair[0], true, false) as Node3D
		if node == null:
			continue
		var dist := player.global_position.distance_to(node.global_position)
		if dist < best_dist:
			best_dist = dist
			best = pair[0]
			best_label = pair[1]
	_near = best
	_near_label = best_label
	if best.begins_with("火盆"):
		var index := int(best.right(1))
		if index >= 1 and index <= 3 and _lit[index - 1]:
			best_label = "火盆已经点着了"
	if prompt != null:
		prompt.text = "" if best == "" else "[E] " + best_label
	_check_campfires(host)


func _check_campfires(host: Node) -> void:
	var fires := [
		["篝火_村庄", "村口篝火"],
		["篝火_边境", "边境篝火"],
		["篝火_军镇", "军镇篝火"],
		["篝火_赤塔", "赤塔篝火"],
	]
	for pair in fires:
		var node := host.find_child(pair[0], true, false) as Node3D
		if node == null:
			continue
		if player.global_position.distance_to(node.global_position) < 2.6:
			if checkpoint_name != pair[1]:
				checkpoint_name = pair[1]
				checkpoint_pos = player.global_position
				if player_script != null and player_script.has_method("heal_full"):
					player_script.heal_full()
				save_game()
				set_hint("检查点：" + pair[1])
			return


# ── 输入:E 交互 / 推进对话 ──────────────────────────────────────

func _input(event: InputEvent) -> void:
	if not (event is InputEventKey) or not event.pressed or event.echo:
		return
	if event.keycode == KEY_E:
		if dialogue_active:
			_advance_dialogue()
		elif not _death_active:
			_interact()


func _interact() -> void:
	if _near == "":
		return
	match _near:
		"莉娅":
			_talk_lia()
		"火盆1", "火盆2", "火盆3":
			_light_brazier(int(_near.right(1)))
		"赤塔门":
			_try_gate()


func _talk_lia() -> void:
	if not lia_talked:
		lia_talked = true
		_color_echo()
		start_dialogue([
			{"speaker": "莉娅", "text": "你终于醒了。你从天上掉下来的时候，整个村子都在褪色。"},
			{"speaker": "艾尔文", "text": "……我看不见颜色。只看得见你的发带。"},
			{"speaker": "莉娅", "text": "这条发带？它是村里最后一点红。你要是想弄明白，就去边境看看。"},
			{"speaker": "莉娅", "text": "拿着这个火把。三座火盆都点着，赤之塔的门才会开。"},
		], _lia_done)
	else:
		start_dialogue([
			{"speaker": "莉娅", "text": "火盆在边境。三座都点着，塔门就会开。"},
		])


func _lia_done() -> void:
	has_torch = true
	save_game()
	set_hint("获得火把 · 目标：点燃边境的三座火盆")


func _light_brazier(index: int) -> void:
	if index < 1 or index > 3:
		return
	if not has_torch:
		set_hint("先去村口找莉娅拿火把")
		return
	if _lit[index - 1]:
		set_hint("这个火盆已经点着了")
		return
	_lit[index - 1] = true
	braziers_lit = _lit.count(true)
	if braziers_lit >= 3:
		_open_gate()
		set_hint("三座火盆都亮了 · 赤之塔的门开了")
	else:
		set_hint("火盆点亮了 · 还差 " + str(3 - braziers_lit) + " 座")
	save_game()


func _try_gate() -> void:
	if braziers_lit >= 3:
		_open_gate()
	else:
		set_hint("三座火盆都点着，门才会开（还差 " + str(3 - braziers_lit) + " 座）")


func _open_gate() -> void:
	if braziers_lit < 3:
		return
	var host := get_parent()
	var gate := host.get_node_or_null("Colliders/赤塔门") as Node3D
	if gate == null:
		gate = host.find_child("赤塔门", true, false) as Node3D
	if gate == null or gate.get_meta("opened", false):
		return
	gate.set_meta("opened", true)
	var tween := create_tween()
	tween.tween_property(gate, "position:y", gate.position.y + 9.0, 1.4)


func _open_gate_if_ready() -> void:
	if braziers_lit >= 3:
		_open_gate()


# ── 对话 ────────────────────────────────────────────────────────

func start_dialogue(lines: Array, done: Callable = Callable()) -> void:
	if dialogue_active:
		return
	dialogue_active = true
	_lines = lines
	_line_index = 0
	_done = done
	if dialog_panel != null:
		dialog_panel.visible = true
	_show_line()


func _show_line() -> void:
	if _line_index >= _lines.size():
		_end_dialogue()
		return
	var line: Dictionary = _lines[_line_index]
	dialog_speaker.text = str(line.get("speaker", ""))
	dialog_text.text = str(line.get("text", ""))


func _advance_dialogue() -> void:
	_line_index += 1
	if _line_index >= _lines.size():
		_end_dialogue()
	else:
		_show_line()


func _end_dialogue() -> void:
	dialogue_active = false
	if dialog_panel != null:
		dialog_panel.visible = false
	if _done.is_valid():
		_done.call()


# ── 颜色:灰白 → 赤色 ────────────────────────────────────────────

func _color_echo() -> void:
	if env == null or red_restored:
		return
	if _echo_tween != null and _echo_tween.is_valid():
		_echo_tween.kill()
	_echo_tween = create_tween()
	_echo_tween.tween_property(env, "adjustment_saturation", 1.35, 0.35)
	_echo_tween.tween_interval(1.8)
	_echo_tween.tween_property(env, "adjustment_saturation", 0.0, 0.9)


func restore_red() -> void:
	if red_restored:
		return
	red_restored = true
	boss_dead = true
	save_game()
	var chapter := get_parent().find_child("章节卡", true, false)
	if chapter != null and chapter is CanvasItem:
		chapter.visible = false
	_flash_white()
	if env != null:
		env.adjustment_enabled = true
		var tween := create_tween()
		tween.tween_property(env, "adjustment_saturation", RESTORED_SATURATION, 4.0)
	_show_cinematic("赤")
	await get_tree().create_timer(2.2).timeout
	_show_cinematic("七色的第一格：赤，已经回来了。")
	await get_tree().create_timer(3.2).timeout
	_hide_cinematic()
	set_hint("赤之领域恢复了颜色")


func _show_cinematic(text: String) -> void:
	if cinematic != null:
		cinematic.text = text
		cinematic.visible = true


func _hide_cinematic() -> void:
	if cinematic != null:
		cinematic.visible = false


func _flash_white() -> void:
	if flash == null:
		return
	if _flash_tween != null and _flash_tween.is_valid():
		_flash_tween.kill()
	flash.visible = true
	flash.color = Color(1.0, 1.0, 1.0, 0.95)
	_flash_tween = create_tween()
	_flash_tween.tween_property(flash, "color:a", 0.0, 1.2)
	_flash_tween.tween_callback(func() -> void:
		if flash != null:
			flash.visible = false)


func flash_red() -> void:
	if flash == null:
		return
	flash.visible = true
	flash.color = Color(0.75, 0.08, 0.05, 0.28)
	if _flash_tween != null and _flash_tween.is_valid():
		_flash_tween.kill()
	_flash_tween = create_tween()
	_flash_tween.tween_property(flash, "color:a", 0.0, 0.3)
	_flash_tween.tween_callback(func() -> void:
		if flash != null:
			flash.visible = false)


# ── 死亡 / 复活 ──────────────────────────────────────────────────

func on_player_death() -> void:
	if _death_active:
		return
	_death_active = true
	if death_root != null:
		death_root.visible = true
	await get_tree().create_timer(2.6).timeout
	if death_root != null:
		death_root.visible = false
	_death_active = false
	if player_script != null and player_script.has_method("revive"):
		player_script.revive(checkpoint_pos + Vector3(0, 1.0, 0))
	set_hint("回到了 " + checkpoint_name)


# ── 存档 ────────────────────────────────────────────────────────

func save_game() -> void:
	var data := {
		"red": red_restored,
		"torch": has_torch,
		"lia": lia_talked,
		"boss": boss_dead,
		"braziers": _lit,
		"checkpoint": checkpoint_name,
		"pos": [checkpoint_pos.x, checkpoint_pos.y, checkpoint_pos.z],
	}
	var file := FileAccess.open(SAVE_PATH, FileAccess.WRITE)
	if file == null:
		return
	file.store_string(JSON.stringify(data))
	file.close()


func _load_game() -> void:
	if not FileAccess.file_exists(SAVE_PATH):
		return
	var file := FileAccess.open(SAVE_PATH, FileAccess.READ)
	if file == null:
		return
	var text := file.get_as_text()
	file.close()
	var parsed = JSON.parse_string(text)
	if parsed == null or not (parsed is Dictionary):
		return
	var data: Dictionary = parsed
	red_restored = bool(data.get("red", false))
	has_torch = bool(data.get("torch", false))
	lia_talked = bool(data.get("lia", false))
	boss_dead = bool(data.get("boss", false))
	checkpoint_name = str(data.get("checkpoint", checkpoint_name))
	var saved_pos = data.get("pos", [])
	if saved_pos is Array and saved_pos.size() >= 3:
		checkpoint_pos = Vector3(float(saved_pos[0]), float(saved_pos[1]),
				float(saved_pos[2]))
	var saved_lit = data.get("braziers", [])
	if saved_lit is Array and saved_lit.size() >= 3:
		_lit = [bool(saved_lit[0]), bool(saved_lit[1]), bool(saved_lit[2])]
		braziers_lit = _lit.count(true)
"""


ENEMY_SCRIPT = r"""extends Node

# ── 《七色》敌人管理 ─────────────────────────────────────────────
# 挂在关卡根节点上。敌人本体是 Blender 里的方块(CharacterBody3D),
# 网格在 glb 里 —— 启动时把网格挂到碰撞体下,移动碰撞体时画面跟着动。
# 行为:巡逻 → 玩家靠近追击 → 近身攻击;半血色核碎了会褪色变弱。
# BOSS:玩家走上塔顶才现身,按血量切三个阶段。

var enemies: Array = []
var player: CharacterBody3D
var player_script: Node
var world: Node
var boss_data: Dictionary = {}
var boss_active := false


func _ready() -> void:
	add_to_group("gk_enemy_manager")
	call_deferred("_setup")


func _setup() -> void:
	await get_tree().process_frame
	player = get_tree().get_first_node_in_group("gk_player") as CharacterBody3D
	if player == null:
		player = get_parent().get_node_or_null("Colliders/艾尔文") as CharacterBody3D
	player_script = get_tree().get_first_node_in_group("gk_player_script")
	world = get_tree().get_first_node_in_group("gk_world")
	var host := get_parent()
	for node in host.find_children("敌人*", "CharacterBody3D", true, false):
		_add_enemy(node, false)
	var boss_node := host.get_node_or_null("Colliders/赤之骑士团长") as CharacterBody3D
	if boss_node != null:
		_add_enemy(boss_node, true)
		boss_node.visible = false
	if world != null and world.boss_dead:
		for data in enemies:
			if data.get("is_boss", false):
				if is_instance_valid(data.node):
					data.node.queue_free()
				data["dead"] = true


func _attach_mesh(host: Node, obj_name: String, body: Node3D) -> void:
	if body == null:
		return
	for node in host.find_children(obj_name, "", true, false):
		if node == body:
			continue
		if node is Node3D and node.get_parent() != body:
			node.reparent(body, true)
			return


func _add_enemy(node: CharacterBody3D, is_boss: bool) -> void:
	node.add_to_group("gk_enemy")
	_attach_mesh(get_parent(), node.name, node)
	var core := node.find_child("*色核*", true, false)
	var data := {
		"node": node,
		"home": node.global_position,
		"hp": 60.0,
		"max_hp": 60.0,
		"speed": 2.0,
		"damage": 9.0,
		"cooldown": 0.0,
		"core": core,
		"core_alive": core != null,
		"is_boss": is_boss,
		"dead": false,
		"phase": 1,
		"patrol_time": 0.0,
	}
	if is_boss:
		data.hp = 360.0
		data.max_hp = 360.0
		data.speed = 2.2
		data.damage = 12.0
		boss_data = data
	enemies.append(data)


func _physics_process(delta: float) -> void:
	if player == null or player_script == null:
		return
	for data in enemies:
		if data.dead:
			continue
		_tick_enemy(data, delta)


func _tick_enemy(data: Dictionary, delta: float) -> void:
	var node := data.node as CharacterBody3D
	if not is_instance_valid(node):
		data["dead"] = true
		return
	var is_boss: bool = data.is_boss
	var pos := node.global_position
	var target_pos := player.global_position
	var dist := pos.distance_to(target_pos)

	if is_boss:
		if not boss_active:
			if target_pos.y > 19.0 and dist < 20.0:
				boss_active = true
				node.visible = true
				if world != null:
					world.show_boss("赤之骑士团长", data.hp, data.max_hp)
					world.set_hint("赤之骑士团长")
			else:
				return
		var ratio: float = data.hp / data.max_hp
		var next_phase := 1
		if ratio < 0.34:
			next_phase = 3
		elif ratio < 0.67:
			next_phase = 2
		if next_phase != data.phase:
			data.phase = next_phase
			data.speed = 2.2 + 0.7 * (next_phase - 1)
			data.damage = 12.0 + 4.0 * (next_phase - 1)
			data.cooldown = 0.0
			if world != null:
				world.set_hint("赤之骑士团长 · 第 " + str(next_phase) + " 阶段")

	var moving := false
	if dist < 14.0:
		target_pos = player.global_position
		moving = true
	elif is_boss:
		# BOSS 不离开塔顶:玩家跑远就回原位
		if pos.distance_to(data.home) > 1.0:
			target_pos = data.home
			moving = true
	else:
		data.patrol_time += delta
		var offset := Vector3(sin(data.patrol_time * 0.6 + float(data.home.x)) * 4.0,
				0.0, cos(data.patrol_time * 0.5 + float(data.home.z)) * 4.0)
		target_pos = data.home + offset
		if pos.distance_to(target_pos) > 1.0:
			moving = true

	var velocity := Vector3.ZERO
	if moving:
		var direction := target_pos - pos
		direction.y = 0.0
		if direction.length() > 0.1:
			direction = direction.normalized()
			velocity = direction * float(data.speed)
			node.look_at(Vector3(pos.x + direction.x, pos.y, pos.z + direction.z),
					Vector3.UP)
	velocity.y = -9.8 * delta
	node.velocity = velocity
	node.move_and_slide()

	data.cooldown = max(0.0, float(data.cooldown) - delta)
	if dist < 2.9 and data.cooldown <= 0.0:
		data.cooldown = 1.5 if not is_boss else 1.3
		if player_script.has_method("take_damage"):
			player_script.take_damage(float(data.damage), node.global_position)


func nearest_enemy(origin: Vector3, forward: Vector3, radius: float,
		max_angle: float) -> Node3D:
	var best: Node3D = null
	var best_dist := radius
	for data in enemies:
		if data.dead or (data.is_boss and not boss_active):
			continue
		var node := data.node as Node3D
		if not is_instance_valid(node):
			continue
		var to := node.global_position - origin
		to.y = 0.0
		var d := to.length()
		if d > best_dist:
			continue
		if d > 0.01 and rad_to_deg(forward.angle_to(to.normalized())) > max_angle:
			continue
		best_dist = d
		best = node
	return best


func hit_enemies_in_cone(origin: Vector3, forward: Vector3, radius: float,
		max_angle: float, damage: float) -> int:
	var hits := 0
	for data in enemies:
		if data.dead or (data.is_boss and not boss_active):
			continue
		var node := data.node as Node3D
		if not is_instance_valid(node):
			continue
		var to := node.global_position - origin
		to.y = 0.0
		if to.length() > radius:
			continue
		if to.length() > 0.01 and rad_to_deg(forward.angle_to(to.normalized())) > max_angle:
			continue
		_damage_enemy(data, damage)
		hits += 1
	return hits


func aoe_damage(origin: Vector3, radius: float, damage: float) -> int:
	var hits := 0
	for data in enemies:
		if data.dead or (data.is_boss and not boss_active):
			continue
		var node := data.node as Node3D
		if not is_instance_valid(node):
			continue
		if node.global_position.distance_to(origin) <= radius:
			_damage_enemy(data, damage)
			hits += 1
	return hits


func _damage_enemy(data: Dictionary, damage: float) -> void:
	data.hp = float(data.hp) - damage
	var node := data.node as Node3D
	if data.core_alive and data.hp < float(data.max_hp) * 0.5:
		data.core_alive = false
		if is_instance_valid(data.core):
			data.core.visible = false
		data.speed = float(data.speed) * 0.7
		data.damage = float(data.damage) * 0.75
		if world != null:
			world.set_hint("色核碎了 · 敌人褪色了")
	if data.is_boss and world != null:
		world.update_boss(max(0.0, data.hp), data.max_hp)
	if data.hp <= 0.0:
		_kill(data)


func _kill(data: Dictionary) -> void:
	data.dead = true
	var node := data.node as Node3D
	if is_instance_valid(node):
		node.queue_free()
	if data.is_boss:
		boss_active = false
		if world != null:
			world.hide_boss()
			world.restore_red()
"""


PLAYER_SCRIPT = r"""extends Node

# ── 《七色》玩家战斗 ─────────────────────────────────────────────
# 挂在玩家节点上(由『脚本_加载』实例化)。移动 / 视角 / 跳跃仍由
# BlenderGameKit 的角色预设负责;这里只管战斗与色力。
#   J 轻击三连 / K 闪避 / R 色技 / E 交互(交互在世界脚本里)

var host: CharacterBody3D
var world: Node
var manager: Node

var hp := 100.0
var max_hp := 100.0
var gauge := 2.0
var max_gauge := 2.0

var dead := false
var invuln := 0.0
var attack_time := 0.0
var attack_duration := 0.0
var attack_cd := 0.0
var attack_done := false
var combo := 0
var combo_timer := 0.0
var damage := 12.0
var dash_time := 0.0
var dash_dir := Vector3.ZERO
var dash_cd := 0.0


func _ready() -> void:
	host = get_parent() as CharacterBody3D
	add_to_group("gk_player_script")
	process_physics_priority = 100
	call_deferred("_setup")


func _setup() -> void:
	await get_tree().process_frame
	world = get_tree().get_first_node_in_group("gk_world")
	manager = get_tree().get_first_node_in_group("gk_enemy_manager")
	if world != null:
		world.set_hp(hp, max_hp)
		world.set_gauge(gauge, max_gauge)


func _input(event: InputEvent) -> void:
	if dead or not (event is InputEventKey) or not event.pressed or event.echo:
		return
	match event.keycode:
		KEY_J:
			_start_attack()
		KEY_K:
			_start_dash()
		KEY_R:
			_use_skill()


func _physics_process(delta: float) -> void:
	if dead:
		return
	invuln = max(0.0, invuln - delta)
	attack_cd = max(0.0, attack_cd - delta)
	dash_cd = max(0.0, dash_cd - delta)
	combo_timer = max(0.0, combo_timer - delta)
	if combo_timer <= 0.0:
		combo = 0
	gauge = min(max_gauge, gauge + delta * 0.06)

	if attack_time > 0.0:
		attack_time -= delta
		if not attack_done and attack_duration - attack_time >= 0.12:
			attack_done = true
			_strike()

	if dash_time > 0.0:
		dash_time -= delta
		host.velocity = dash_dir * 16.0
		host.move_and_slide()

	if world != null:
		world.set_hp(hp, max_hp)
		world.set_gauge(gauge, max_gauge)


func _start_attack() -> void:
	if attack_time > 0.0 or attack_cd > 0.0:
		return
	combo = combo + 1 if combo < 3 else 1
	attack_duration = 0.34 if combo < 3 else 0.46
	attack_time = attack_duration
	attack_done = false
	attack_cd = 0.1
	combo_timer = 0.75
	damage = 12.0 if combo == 1 else (14.0 if combo == 2 else 22.0)
	_face_target()


func _strike() -> void:
	if manager == null:
		return
	var forward := -host.global_transform.basis.z
	forward.y = 0.0
	if forward.length() < 0.01:
		return
	manager.hit_enemies_in_cone(host.global_position, forward.normalized(),
			2.8, 75.0, damage)


func _face_target() -> void:
	var camera := get_viewport().get_camera_3d()
	if camera == null:
		return
	var forward := -camera.global_transform.basis.z
	forward.y = 0.0
	if forward.length() < 0.01:
		return
	# 软锁定:镜头前方 8 米、60° 内最近的那个敌人
	if manager != null:
		var target = manager.nearest_enemy(host.global_position, forward.normalized(),
				8.0, 60.0)
		if target != null:
			_turn_to(target)


func _turn_to(target: Node3D) -> void:
	var to := target.global_position - host.global_position
	to.y = 0.0
	if to.length() > 0.01:
		host.look_at(host.global_position + to.normalized(), Vector3.UP)


func _start_dash() -> void:
	if dash_cd > 0.0 or dash_time > 0.0:
		return
	var up := Input.get_action_strength("move_up")
	var down := Input.get_action_strength("move_down")
	var left := Input.get_action_strength("move_left")
	var right_input := Input.get_action_strength("move_right")
	var camera := get_viewport().get_camera_3d()
	var direction := Vector3.ZERO
	if camera != null:
		var forward := -camera.global_transform.basis.z
		forward.y = 0.0
		forward = forward.normalized()
		var right := camera.global_transform.basis.x
		right.y = 0.0
		right = right.normalized()
		direction = forward * (up - down) + right * (right_input - left)
	if direction.length() < 0.01:
		direction = -host.global_transform.basis.z
		direction.y = 0.0
	dash_dir = direction.normalized()
	dash_time = 0.22
	dash_cd = 0.9
	invuln = 0.4


func _use_skill() -> void:
	if world == null or not world.red_restored:
		if world != null:
			world.set_hint("还没有颜色，用不了色技")
		return
	if gauge < 1.0:
		if world != null:
			world.set_hint("色力不够")
		return
	gauge -= 1.0
	if manager != null:
		manager.aoe_damage(host.global_position, 5.0, 28.0)
	if world != null:
		world.flash_red()
		world.set_hint("赤 · 烈焰爆发")


func take_damage(amount: float, source: Vector3) -> void:
	if dead or invuln > 0.0:
		return
	hp = max(0.0, hp - amount)
	invuln = 0.55
	if world != null:
		world.flash_red()
		world.set_hp(hp, max_hp)
	var push := host.global_position - source
	push.y = 0.0
	if push.length() > 0.01:
		host.velocity += push.normalized() * 3.0
	if hp <= 0.0:
		_die()


func _die() -> void:
	if dead:
		return
	dead = true
	host.velocity = Vector3.ZERO
	if world != null:
		world.on_player_death()


func revive(pos: Vector3) -> void:
	dead = false
	hp = max_hp
	gauge = max_gauge
	invuln = 2.0
	host.global_position = pos
	host.velocity = Vector3.ZERO
	if world != null:
		world.set_hp(hp, max_hp)
		world.set_gauge(gauge, max_gauge)


func heal_full() -> void:
	hp = max_hp
	gauge = max_gauge
	if world != null:
		world.set_hp(hp, max_hp)
		world.set_gauge(gauge, max_gauge)
"""
