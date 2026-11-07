"""恐怖示例的自动检查(第五十九轮单场景 -> 

五十九轮单场景 -> 六十轮拆三关 -> 七十轮补标题页
-> 一百五十八轮按系统分工重排 + 补玩法闭环)。

用法:
    python scripts\\check_horror_demo.py
    python scripts\\check_horror_demo.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

── 怎么测四幕(而不是一幕)────────────────────────────────────
一幕一张 `.tscn`。**不能**在一个探针里真的走"切关卡" —— Godot 的
`change_scene_to_file` 会把当前场景整棵换掉,探针(挂在当前场景下)会
一起被释放,后面的断言就没机会跑。所以拆成多个探针,各自把对应那幕的
`.tscn` 当成子节点加载进来驱动:

  0. 标题:两个文字标签 + 『开始游戏』/『退出游戏』按钮 + 相机;
  1. 教室:开场曲线运镜在飞;**纸条拾取后节点被销毁**;
     只拿 2 张走到门口 -> **门禁挡住**(探针还活着,还显示"还差 N 张");
     拿满 3 张 -> HUD 显示 3/3;演完切回『侧视机位』并跟着人;
  2. 走廊:AI 在巡逻、紧张度远低近高 + 心跳建起来、走过惊吓区 ->
     相机在震 + Master 被压低 -> **2.5 秒后总线恢复**;
     贴到怪物身上 -> **镜头倒地 + 冻住世界 + 重试 / 回标题按钮**;
  3. 楼梯间:先写一份"纸条数 = 3"的存档(模拟第一关存过),走到门口 ->
     弹结局选择(世界已冻)-> 按 2 -> `结局` = 2 且真的落盘
     (落的是**带校验的信封** —— 第九十三轮『防改档』);
     再点『回到标题』-> **世界已解冻**(否则标题页也会冻着);
  4. 『防改档』两趟(第九十三轮):读回上一趟**生成代码自己写下**的信封
     (往返通);塞一份**被手改过**的档(哈希对不上)-> 当没有存档:
     纸条数回默认 0、门口不放行;
  5. 残缺存档(纸条数 = 1):门口**不弹选择**、显示"还差 N 张"、
     并给『回到标题』出口(旧版在这里是死局)。

另外还会查生成物里的**系统分工证据**:每个场景同时有
`game_logic_<场景>.gd`(关卡图,挂场景根)和 `game_logic_<场景>_char.gd`
(角色图,挂角色);角色图里**不该**出现 UI / 存档 / 心跳 / 总线 / 切关。
主场景必须是**标题页**(按 F5 先看到标题)。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys


# ⚠ Windows 控制台默认 GBK,而下面会用 `✗`(U+2717)打失败信息 —— 直接打印会
#   `UnicodeEncodeError`,**失败信息本身**被一段 Traceback 顶掉(门禁里看到的
#   就是"自查脚本崩了",而不是"哪一条不对")。这里把输出改成能容错的编码。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                          # noqa: BLE001
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "examples", "demo_horror", "恐怖示例_godot")
OUT = os.path.join(ROOT, "_dist", "horror_probe")
GODOT_DEFAULT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"

TITLE_SCENE = "res://scenes/标题.tscn"
ROOM_SCENE = "res://scenes/教室.tscn"
CORRIDOR_SCENE = "res://scenes/走廊.tscn"
MUSIC_SCENE = "res://scenes/音乐教室.tscn"
MED_SCENE = "res://scenes/医务室.tscn"
STAIRS_SCENE = "res://scenes/楼梯间.tscn"

# 公共开头:加载指定场景 + 找节点 + 工具函数
PROBE_HEAD = '''extends Node

# 恐怖示例的探针。结论行统一 HORROR_ 前缀,Python 那边收集断言。
var world: Node = null
var player: Node = null
var enemy: Node = null
var elapsed := 0.0
var phase := 0
var spots := {}


func _boot(scene_path: String, player_name: String, enemy_name: String) -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	world = load(scene_path).instantiate()
	add_child(world)
	if player_name != "":
		player = _find_body(player_name)
	if enemy_name != "":
		enemy = _find_body(enemy_name)


# 第 165 轮"留壳导出"之后,世界 glb 里有和 body **同名**的壳节点
# (怪物的零件挂在壳下,壳由配对表驱动跟随) —— 只按名字找会命中壳
# (没有脚本)。优先认 CharacterBody3D 那个。
func _find_body(name: String) -> Node:
	for node in world.find_children(name, "CharacterBody3D", true, false):
		return node
	return world.find_child(name, true, false)


func _state() -> String:
	# 关卡图挂在场景根,AI 状态变量也在根上;角色脚本只负责移动/视角。
	if world != null:
		var value = world.get("ai_state")
		if value != null:
			return str(value)
	if player == null:
		return "<no-player>"
	return str(player.get("ai_state"))


func _var(name: String):
	# 先读场景根(关卡图),再退回角色脚本(老图/角色图变量)。
	if world != null:
		var value = world.get(name)
		if value != null:
			return value
	if player == null:
		return null
	return player.get(name)


func _put(pos: Vector3) -> void:
	if player != null:
		player.global_position = pos


func _put_at(name: String) -> bool:
	if not spots.has(name):
		return false
	_put(spots[name] + Vector3(0.0, 1.0, 0.0))
	return true


func _find_areas(names: Array) -> void:
	for name in names:
		var area = world.find_child(name, true, false)
		if area != null:
			spots[name] = area.global_position


func _send_key(code: int, pressed: bool) -> void:
	var event := InputEventKey.new()
	event.keycode = code
	event.physical_keycode = code
	event.pressed = pressed
	Input.parse_input_event(event)


var _tap_action := ""
var _tap_left := 0.0


func _tap(action: String, fallback_key: int) -> void:
	# E / J / Esc 走 **输入动作**(导出到 project.godot 的那套);
	# 动作不在映射里才退回直接塞 InputEventKey(至少不会崩)。
	_tap_action = action
	if InputMap.has_action(action):
		Input.action_press(action)
	else:
		_send_key(fallback_key, true)
	_tap_left = 0.12


func _process(delta: float) -> void:
	if _tap_left <= 0.0:
		return
	_tap_left -= delta
	if _tap_left > 0.0:
		return
	if InputMap.has_action(_tap_action):
		Input.action_release(_tap_action)


func _release(action: String) -> void:
	if InputMap.has_action(action):
		Input.action_release(action)


func _var_player(name: String):
	if player == null:
		return null
	return player.get(name)


func _as_float(value) -> float:
	if value == null:
		return 0.0
	return float(value)


func _panel_visible() -> bool:
	if world == null:
		return false
	for node in world.find_children("*", "Panel", true, false):
		if node is Panel and String(node.name) == "日志面板":
			return node.visible
	return false


func _find_button(text: String) -> Button:
	for node in world.find_children("*", "Button", true, false):
		if node is Button and String(node.text) == text:
			return node
	return null


func _has_label(fragment: String) -> bool:
	for node in world.find_children("*", "Label", true, false):
		if node is Label and fragment in String(node.text):
			return true
	return false


## 当前相机的"侧翻角"(倒地 = 绕视线轴滚 72°;恢复之后必须回到 0)
func _cam_roll() -> float:
	var cam = get_viewport().get_camera_3d()
	if cam == null:
		return 999.0
	return absf(float((cam as Node3D).global_transform.basis.get_euler().z))


## 相机的"立正程度":水平时 up 向量朝上 = 1;侧翻 72° 之后约 0.31
func _cam_upright() -> float:
	var cam = get_viewport().get_camera_3d()
	if cam == null:
		return -99.0
	return float((cam as Node3D).global_transform.basis.y.y)


func _cam_dist() -> float:
	var cam = get_viewport().get_camera_3d()
	if cam == null or player == null:
		return -1.0
	return (cam as Node3D).global_position.distance_to(
		(player as Node3D).global_position)


func _cam_up() -> float:
	var cam = get_viewport().get_camera_3d()
	if cam == null or player == null:
		return -99.0
	return ((cam as Node3D).global_position.y
		- (player as Node3D).global_position.y)


## 日志面板第 index 行的正文(方括号包起来:空行也能一眼看出是空字符串)
func _line_text(index: int) -> String:
	var wanted := "日志行" + str(index)
	for node in world.find_children("*", "Label", true, false):
		if node is Label and String(node.name) == wanted:
			# 正文里有换行(标题 + 正文两段);换成 " | " 才好一行打完
			return "[" + String(node.text).replace("\\n", " | ") + "]"
	return "[<没有这一行>]"
'''

PROBE_TITLE = PROBE_HEAD + '''

var labels := 0
var buttons := 0


func _count_ui(node: Node) -> void:
	for child in node.get_children():
		if child is Label:
			labels += 1
		if child is Button:
			buttons += 1
		_count_ui(child)


func _ready() -> void:
	# 标题页没有角色(关卡图直接挂场景根),不再找那块假管理器。
	_boot("%(title)s", "", "")


func _physics_process(delta: float) -> void:
	elapsed += delta
	if phase == 0 and elapsed >= 0.3:
		phase = 1
		_count_ui(world)
		print("HORROR_TITLE_UI labels=", labels, " buttons=", buttons,
			" cam=", world.find_child("标题机位", true, false) != null,
			" start=", _find_button("开始游戏") != null,
			" quit=", _find_button("退出游戏") != null)
		print("HORROR_TITLE_DONE")
		set_physics_process(false)
		get_tree().quit()
''' % {"title": TITLE_SCENE}

PROBE_ROOM = PROBE_HEAD + '''

var cam: Node = null
var cam_start := Vector3.ZERO


func _ready() -> void:
	_boot("%(room)s", "教室玩家", "")
	_find_areas(["纸条1", "纸条2", "纸条3", "去走廊"])
	# 开场运镜的落点现在叫『门口特写』(三关统一机位后不再带"教室"前缀)。
	cam = world.find_child("门口特写", true, false)


func _physics_process(delta: float) -> void:
	elapsed += delta
	match phase:
		0:
			if elapsed >= 0.2:
				phase = 1
				if cam != null:
					cam_start = cam.global_position
				print("HORROR_ROOM_START player=", player != null,
					" spots=", spots.size(), " cam=", cam != null,
					" hud=", _has_label("纸条 0/3"),
					" logopen=", _var("日志打开"))
				_put_at("纸条1")
		1:
			# ⚠ 传送和按 E **必须分两帧**:body_entered 是物理服务器下一帧才发的,
			#   同一帧按 E 时『交互目标』还是空串,这次按键就丢了(会连锁失败)。
			if elapsed >= 0.35:
				phase = 2
				_tap("key_e", KEY_E)
		2:
			if elapsed >= 0.8:
				phase = 3
				var flew := 0.0
				if cam != null:
					flew = cam.global_position.distance_to(cam_start)
				print("HORROR_ROOM_OPENING flew=", snappedf(flew, 0.01),
					" picked=", world.find_child("纸条1", true, false) == null,
					" target=", _var("交互目标"))
				_put_at("纸条2")
		3:
			if elapsed >= 0.95:
				phase = 4
				_tap("key_e", KEY_E)
		4:
			# 只拿 2 张就走到门口:门禁应该挡住,不能切场景(切了探针会被释放)
			if elapsed >= 1.4:
				phase = 5
				_put_at("去走廊")
		5:
			if elapsed >= 1.55:
				phase = 6
				_tap("key_e", KEY_E)
		6:
			if elapsed >= 2.1:
				phase = 7
				print("HORROR_ROOM_GATE notes=",
					snappedf(float(_var("纸条数")), 0.01),
					" alive=", is_instance_valid(world),
					" hint=", _has_label("还差"),
					" removed=", world.find_child("纸条2", true, false) == null,
					" target=", _var("交互目标"))
				_put_at("纸条3")
		7:
			if elapsed >= 2.25:
				phase = 8
				_tap("key_e", KEY_E)
		8:
			if elapsed >= 2.7:
				phase = 10
				var removed := 0
				for name in ["纸条1", "纸条2", "纸条3"]:
					if world.find_child(name, true, false) == null:
						removed += 1
				print("HORROR_ROOM_NOTES count=",
					snappedf(float(_var("纸条数")), 0.01),
					" removed=", removed, " hud=", _has_label("纸条 3/3"),
					" logline=", _has_label("值日日志"))
				# 挪到一块空地(别踩到门口 / 纸条那些触发区),看视角跟不跟人
				_put(Vector3(-8.0, 0.8, 0.0))
				_tap("key_j", KEY_J)
		10:
			if elapsed >= 3.3:
				phase = 11
				print("HORROR_ROOM_LOG_OPEN open=", _var("日志打开"),
					" panel=", _panel_visible())
				_tap("key_j", KEY_J)
		11:
			if elapsed >= 3.9:
				phase = 12
				print("HORROR_ROOM_LOG_CLOSE open=", _var("日志打开"),
					" panel=", _panel_visible())
				_tap("key_j", KEY_J)
		12:
			if elapsed >= 4.5:
				phase = 13
				_tap("key_esc", KEY_ESCAPE)
		13:
			if elapsed >= 5.1:
				phase = 14
				print("HORROR_ROOM_LOG_ESC open=", _var("日志打开"),
					" panel=", _panel_visible())
		14:
			# 开场运镜 2.5s + 特写停留 1.5s + 硬切回第三人称 —— 6.4s 早回来了
			if elapsed >= 6.4:
				phase = 15
				var cur := get_viewport().get_camera_3d()
				if cur == null:
					print("HORROR_ROOM_CAM cur=<null>")
				else:
					print("HORROR_ROOM_CAM cur=", cur.name,
						" dist=", snappedf(cur.global_position.distance_to(
							player.global_position), 0.01))
				print("HORROR_ROOM_DONE total=", snappedf(elapsed, 0.1))
				set_physics_process(false)
				get_tree().quit()
''' % {"room": ROOM_SCENE}

PROBE_CORRIDOR = PROBE_HEAD + '''

var enemy_start := Vector3.ZERO


func _ready() -> void:
	# 模拟第一关存了 2 张纸条:走廊开局的『存档_载入』会读回来 ——
	# 被抓回安全屋之后这两张**必须还在**(旧行为是重载整关,直接归零)。
	var file := FileAccess.open("user://gk_save1.json", FileAccess.WRITE)
	if file != null:
		file.store_string('{"纸条数": 2}')
		file.close()
	_boot("%(corridor)s", "走廊玩家", "怪物")
	_find_areas(["惊吓区", "去音乐教室", "安全屋",
			"保险丝1", "保险丝2", "保险丝3", "走廊笔记"])
	_install_gate_spy()


func _current_scene_name() -> String:
	var scene = get_tree().current_scene
	if scene == null:
		return "<null>"
	return String(scene.name)


# 『场景_切换关卡』的生成代码先找 "/root/GkWorld" 调 change_level,
# 找不到才走 change_scene_to_file —— 真切场景会连探针(main_scene)
# 一起 free,PASS 永远打不出来。在 root 下挂一个**假 GkWorld**拦下
# 这次调用:打印目标路径就够了 —— 验证的是"放行分支真的走到了",
# 切完之后的事由音乐教室自己的探针覆盖(第 168 轮)。
class GateSpy extends Node:
	func change_level(path: String) -> void:
		print("HORROR_FUSE_PASS scene=", path)


func _install_gate_spy() -> void:
	var existing := get_tree().root.get_node_or_null("GkWorld")
	if existing != null:
		return
	var spy := GateSpy.new()
	spy.name = "GkWorld"
	get_tree().root.add_child.call_deferred(spy)


func _lit(name: String) -> bool:
	var node := world.find_child(name, true, false)
	if node == null:
		return true
	return bool(node.visible) and (node as Light3D).light_energy > 0.001


var last_paused := false


func _physics_process(delta: float) -> void:
	elapsed += delta
	if get_tree().paused != last_paused:
		last_paused = get_tree().paused
		print("HORROR_PAUSE_CHANGE paused=", last_paused,
			" elapsed=", snappedf(elapsed, 0.01), " phase=", phase)
	match phase:
		0:
			if elapsed >= 0.2:
				phase = 1
				if enemy != null:
					enemy_start = enemy.global_position
				print("HORROR_CORRIDOR_START state=", _state(),
					" has_enemy=", enemy != null, " spots=", spots.size(),
					" battery=", snappedf(_as_float(_var("手电电量")), 0.1),
					" hudtext=", _has_label("手电电量:"))
				if enemy != null:
					_put(enemy.global_position + Vector3(0.0, 1.0, 12.0))
		1:
			if elapsed >= 0.9:
				phase = 2
				var moved := 0.0
				if enemy != null:
					moved = enemy.global_position.distance_to(enemy_start)
				print("HORROR_PATROL state=", _state(),
					" moved=", snappedf(moved, 0.01))
				print("HORROR_TENSION_FAR tension=",
					snappedf(float(_var("_gk_heart_tension")), 0.001))
				# 跑动:按住左 Shift -> 角色图把速度改成 1.8 倍 + 发脚步噪音
				if InputMap.has_action("key_left_shift"):
					Input.action_press("key_left_shift")
				elif Input.is_action_just_pressed("key_left_shift") == false:
					_send_key(KEY_SHIFT, true)
		2:
			if elapsed >= 1.15:
				phase = 3
				print("HORROR_RUN speed=",
					snappedf(_as_float(_var_player("移动速度")), 0.01),
					" loud=", snappedf(_as_float(_var("ai_state_hear_loud")),
						0.01),
					" type=", _var("ai_state_hear_type"))
				_release("key_left_shift")
				if InputMap.has_action("key_left_ctrl"):
					Input.action_press("key_left_ctrl")
		3:
			if elapsed >= 1.4:
				phase = 4
				print("HORROR_CROUCH speed=",
					snappedf(_as_float(_var_player("移动速度")), 0.01),
					" hear=", snappedf(_as_float(_var("ai_state_hear_loud")),
						0.01))
				_release("key_left_ctrl")
				# 4 米:紧张度够高,但还没进 1.9 米的抓取范围。
				if enemy != null:
					_put(enemy.global_position + Vector3(0.0, 1.0, 4.0))
		4:
			if elapsed >= 1.7:
				phase = 5
				print("HORROR_TENSION_NEAR tension=",
					snappedf(float(_var("_gk_heart_tension")), 0.001),
					" heart=", _var("_gk_heart_player") != null)
				_put_at("惊吓区")
		5:
			if elapsed >= 2.1:
				phase = 6
				print("HORROR_SCARE shake=",
					snappedf(float(_var("_gk_shake_left")), 0.01),
					" bus=", snappedf(AudioServer.get_bus_volume_db(0), 0.01))
		6:
			# 惊吓后压低的总线必须还回来(旧版压低就不还了)
			if elapsed >= 6.1:
				phase = 7
				print("HORROR_BUS_RESTORE bus=",
					snappedf(AudioServer.get_bus_volume_db(0), 0.01))
				# 贴到怪物身上 -> 1 秒倒地 + 冻世界
				if enemy != null:
					_put(enemy.global_position + Vector3(0.5, 1.0, 0.0))
		7:
			if elapsed >= 7.7:
				phase = 8
				print("HORROR_FAIL paused=", get_tree().paused,
					" fall=", _var("_gk_fall_cam") != null,
					" safehouse=", _find_button("回到安全屋") != null,
					" back=", _find_button("回到标题") != null)
				var safe := _find_button("回到安全屋")
				if safe != null:
					safe.pressed.emit()
		8:
			if elapsed >= 8.7:
				phase = 9
				var home := Vector3.ZERO
				if spots.has("安全屋"):
					home = spots["安全屋"]
				print("HORROR_SAFEROOM paused=", get_tree().paused,
					" alive=", is_instance_valid(world),
					" dist=", snappedf(player.global_position.distance_to(home),
						0.01),
					" checkpoint=", _var("检查点"),
					" notes=", snappedf(_as_float(_var("纸条数")), 0.01),
					" cam_roll=", snappedf(_cam_roll(), 0.001),
					" cam_upright=", snappedf(_cam_upright(), 0.01),
					" cam_dist=", snappedf(_cam_dist(), 0.01),
					" cam_up=", snappedf(_cam_up(), 0.01))
				# ── M0.5:旧档(裸 JSON,只有『纸条数』)下的日志 ──────────
				# 本探针开局写的就是 `{"纸条数": 2}`:没有『已读N』三个键。
				# 要求:按 J 仍然能开面板(不崩);三行正文是空字符串(当没读过)。
				_send_key(KEY_J, true)
		9:
			if elapsed >= 8.95:
				phase = 10
				_send_key(KEY_J, false)
		10:
			if elapsed >= 9.4:
				phase = 11
				var panel = _var("_gk_ui_日志面板")
				print("HORROR_JOURNAL_OLD open=", _var("日志打开"),
					" panel=", panel != null and panel.visible,
					" paused=", get_tree().paused,
					" archive=", _has_label("档案 · 纸条 "),
					" line1=", _line_text(1), " line2=", _line_text(2),
					" line3=", _line_text(3))
				# 关掉日志面板:用 **J 再按一次**(J 开/J 再按关)。别按
				# Esc —— Esc 在这张图里是**暂停菜单热键**(第 168 轮实测:
				# 合成 Esc 事件把整棵树暂停了,后面世界物理停摆,
				# body_entered 永远不触发)。
				# ── 第 168 轮:保险丝解谜 ────────────────────────────
				# 先把怪物"拔电":AI 移动在**关卡脚本**的物理帧里,把 world
				# 的物理帧一关,后面这些传送就不会被"看见→追击→抓住→
				# 被抓失败链暂停"打断(实测:判定区/传送板离巡逻线都小于
				# 视野 9 米,不关 AI 每次都在 1 秒内被抓 → 树暂停 →
				# body_entered 永远不触发)。保险丝链是**信号驱动**,
				# 不依赖物理帧,照常触发。
				if enemy != null:
					enemy.set_physics_process(false)
					_put(enemy.global_position + Vector3(0.0, 1.0, -40.0))
				if world != null:
					world.set_physics_process(false)
				# 先空手站上传送板:0 个保险丝,**不许**切音乐教室。
				_put_at("去音乐教室")
		11:
			if elapsed >= 10.2:
				# 被抓失败链的尾巴:await 1 秒后 `_gk_apply_pause(true)` ——
				# 探针点"回到安全屋"解除的暂停,**这条迟到暂停在 ~10.1s
				# 又把树暂停了**(实测 HORROR_PAUSE_CHANGE paused=true
				# elapsed=10.12,三次二分全复现)。这里强制再解一次;
				# 之后没有别的暂停源,保险丝阶段就是干净的了。
				get_tree().paused = false
				phase = 12
				var gate_scene := _current_scene_name()
				print("HORROR_FUSE_GATE blocked=", gate_scene != "音乐教室",
					" scene=", gate_scene,
					" count=", snappedf(_as_float(_var("保险丝数")), 0.1))
				_put_at("保险丝1")
		12:
			if elapsed >= 10.9:
				phase = 13
				var fa = world.find_child("保险丝1", true, false)
				print("HORROR_FUSE1 count=", snappedf(_as_float(_var("保险丝数")), 0.1),
					" light2=", _lit("走廊吊灯2"),
					" at=", str((player as Node3D).global_position),
					" mon=", fa.monitoring if fa != null else -1,
					" vis=", fa.visible if fa != null else -1,
					" pmode=", fa.process_mode if fa != null else -1,
					" overlap=", fa.get_overlapping_bodies().size()
						if fa != null else -1,
					" paused=", get_tree().paused,
					" player_can=", player.can_process(),
					" player_phys=", player.is_physics_processing(),
					" player_tree=", player.is_inside_tree())
				_put_at("保险丝2")
		13:
			if elapsed >= 11.6:
				phase = 14
				print("HORROR_FUSE2 count=", snappedf(_as_float(_var("保险丝数")), 0.1),
					" light3=", _lit("走廊吊灯3"))
				_put_at("保险丝3")
		14:
			if elapsed >= 12.3:
				phase = 15
				print("HORROR_FUSE3 count=", snappedf(_as_float(_var("保险丝数")), 0.1))
				# 顺路捡隐藏笔记(第 169 轮)。⚠ 每次传送要**分帧**:同一物理
				# 帧里三连瞬移,只有最终位置会进物理空间,中间的判定区
				# body_entered 一个都不会发。
				_put_at("走廊笔记")
		15:
			if elapsed >= 12.9:
				phase = 16
				print("HORROR_NOTE_HIDDEN hidden=",
					snappedf(_as_float(_var("隐藏笔记数")), 0.1))
				_put_at("去音乐教室")
		16:
			if elapsed >= 13.9:
				phase = 17
				# 档案系统(第 170 轮):回安全屋再存一次档 —— 信封里必须带上
				# 刚捡到的『隐藏笔记数』(world 的物理帧还关着,但保存链是
				# **信号驱动**,照常落盘)。
				_put_at("安全屋")
		17:
			if elapsed >= 14.7:
				phase = 18
				var envelope := FileAccess.get_file_as_string("user://gk_save1.json")
				print("HORROR_ARCHIVE saved=", envelope.contains("隐藏笔记数"),
					" total=", snappedf(elapsed, 0.1))
				print("HORROR_CORRIDOR_DONE total=", snappedf(elapsed, 0.1))
				set_physics_process(false)
				get_tree().quit()
''' % {"corridor": CORRIDOR_SCENE}

PROBE_MUSIC = PROBE_HEAD + '''

# ── 第 3 章 音乐教室(M1a)────────────────────────────────────────
# 一趟把整条循环跑完:
#   读 3 张乐谱 -> 录音机按错一次(警报)-> 再错一次(怪物进本关)
#   -> 按对(练习曲 + 舞台灯灭)-> 侧门还锁着 -> 拿磁带 -> 侧门放行。
# ⚠ 最后那一下会真的切到『楼梯间』:探针会被 change_scene_to_file 一起
#   释放,所以先把 current_scene 指到**关卡**、把关卡 reparent 到 root,
#   切场景时被回收的就是关卡而不是探针 —— 探针才量得到"真的换了关"。
var stage_light: Node = null
var cam_up_base := 0.0


func _ready() -> void:
	# 从走廊进来时存档里已经有『纸条数』(教室门口存的);照写一份,
	# 让本关的『载入存档』有东西可读(和真机一致)。
	var file := FileAccess.open("user://gk_save1.json", FileAccess.WRITE)
	if file != null:
		# 第 170 轮:模拟"走廊已存过档" —— 信封里带着隐藏笔记数 1。
		file.store_string('{"纸条数": 3, "隐藏笔记数": 1}')
		file.close()
	_boot("%(music)s", "音乐教室玩家", "音乐教室怪物")
	_find_areas(["乐谱1", "乐谱2", "乐谱3", "录音机", "磁带", "舞台",
			"音乐教室笔记",
		"安全屋区域", "去医务室"])
	stage_light = world.find_child("舞台灯", true, false)


## 站到某个判定体的位置上(离地 0.6 米,落地后仍然和它重叠)。
func _stand(name: String) -> bool:
	if not spots.has(name):
		return false
	_put(spots[name] + Vector3(0.0, 0.6, 0.0))
	return true


func _tap_e() -> void:
	_tap("key_e", KEY_E)


func _tap_digit(d: String) -> void:
	_tap("key_" + d, KEY_1)


func _light_visible() -> bool:
	if stage_light == null or not is_instance_valid(stage_light):
		return true
	return bool(stage_light.visible)


func _count() -> String:
	return str(snappedf(_as_float(_var("乐谱数")), 0.01))


func _miss() -> String:
	return str(snappedf(_as_float(_var("录音机错次")), 0.01))


func _tape() -> String:
	return str(snappedf(_as_float(_var("磁带")), 0.01))


func _current_scene_name() -> String:
	var scene := get_tree().current_scene
	if scene == null:
		return "<null>"
	return String(scene.name)


var stage_before := 0.0


func _stage_dist() -> float:
	if enemy == null or not is_instance_valid(enemy):
		return -1.0
	var stage: Vector3 = spots.get("舞台", Vector3.ZERO)
	return (enemy as Node3D).global_position.distance_to(stage)


## 存档信封里有没有校验和 / 有没有把老档的『纸条数』一起带上
func _savecheck() -> String:
	var file := FileAccess.open("user://gk_save1.json", FileAccess.READ)
	if file == null:
		return "save=missing"
	var text := file.get_as_text()
	file.close()
	return "save_check=" + str("gk_check" in text) + \
		" save_notes=" + str("纸条数" in text)

func _cam_name() -> String:
	var cam := get_viewport().get_camera_3d()
	if cam == null:
		return "<null>"
	return String(cam.name)

func _physics_process(delta: float) -> void:
	elapsed += delta
	match phase:
		0:
			if elapsed >= 0.30:
				phase = 40
				print("HORROR_MUSIC_START player=", player != null,
					" enemy=", enemy != null, " spots=", spots.size(),
					" state=", _state(),
					" sheets=", _count(), " miss=", _miss(),
					" tape=", _tape(),
					" playing=", _var("录音机放音"),
					" input=[", _var("录音机输入"), "]",
					" checkpoint=", _var("检查点"),
					" logopen=", _var("日志打开"),
					" stagelight=", _light_visible(),
					" stale=", _has_label("进度已保存"),
					" guide1=", _has_label("先找齐"))
				if enemy != null:
					enemy.global_position = Vector3(0.0, 1.0, 60.0)
				_stand("乐谱1")
		40:
			if elapsed >= 0.80:
				phase = 41
				# 原地蹲下量**镜头**这一半(角色图只管速度,压相机在蹲走脚本里)
				cam_up_base = _cam_up()
				if InputMap.has_action("key_left_ctrl"):
					Input.action_press("key_left_ctrl")
		41:
			if elapsed >= 1.20:
				phase = 1
				print("HORROR_MUSIC_CROUCH_DIP cam=", _cam_name(),
					" cam_drop=", snappedf(cam_up_base - _cam_up(), 0.001))
				_tap_e()
		1:
			if elapsed >= 1.55:
				phase = 2
				print("HORROR_MUSIC_SHEET n=1 count=", _count(),
					" target=", _var("交互目标"),
					" tip=", _has_label("[E] 读乐谱"))
				_release("key_left_ctrl")
				if enemy != null:
					_put(enemy.global_position + Vector3(0.5, 1.0, 0.0))
		2:
			if elapsed >= 3.70:
				phase = 3
				print("HORROR_MUSIC_FAIL paused=", get_tree().paused,
					" fall=", _var("_gk_fall_cam") != null,
					" safehouse=", _find_button("回到安全屋") != null,
					" back=", _find_button("回到标题") != null)
				var home := _find_button("回到安全屋")
				if home != null:
					home.pressed.emit()
		3:
			if elapsed >= 4.50:
				phase = 4
				var home_pos := Vector3.ZERO
				if spots.has("安全屋区域"):
					home_pos = spots["安全屋区域"]
				print("HORROR_MUSIC_SAFEROOM paused=", get_tree().paused,
					" alive=", is_instance_valid(world),
					" dist=", snappedf(
						player.global_position.distance_to(home_pos), 0.01),
					" checkpoint=", _var("检查点"),
					" sheets=", _count(), " ", _savecheck())
				# 蹲走段:怪站在**巡逻近端**,玩家按住 Ctrl 蹲过去读乐谱2
				if enemy != null:
					enemy.global_position = Vector3(0.0, 0.9, 9.5)
				if InputMap.has_action("key_left_ctrl"):
					Input.action_press("key_left_ctrl")
		4:
			if elapsed >= 4.80:
				phase = 5
				var sheet_pos: Vector3 = spots.get("乐谱2", Vector3.ZERO)
				print("HORROR_MUSIC_CROUCH speed=",
					snappedf(_as_float(_var_player("移动速度")), 0.01))
				_put(player.global_position.lerp(sheet_pos, 0.55))
		5:
			if elapsed >= 5.10:
				phase = 6
				_stand("乐谱2")
		6:
			if elapsed >= 5.30:
				phase = 7
				_tap_e()
		7:
			if elapsed >= 5.70:
				phase = 8
				print("HORROR_MUSIC_SHEET n=2 count=", _count(),
					" crouch=", snappedf(_as_float(_var_player("移动速度")), 0.01),
					" failed=", _var("已失败"),
					" paused=", get_tree().paused)
				_release("key_left_ctrl")
				if enemy != null:
					enemy.global_position = Vector3(0.0, 1.0, 60.0)
				_stand("乐谱3")
		8:
			if elapsed >= 5.95:
				phase = 9
				_tap_e()
		9:
			if elapsed >= 6.40:
				phase = 10
				print("HORROR_MUSIC_SHEET n=3 count=", _count(),
					" reveal=", _has_label("四个音凑齐"),
					" tip=", _has_label("[E] 读乐谱"),
					" guide2=", _has_label("乐谱齐了"))
				_stand("录音机")
		10:
			if elapsed >= 6.65:
				phase = 11
				_tap_e()
		11:
			if elapsed >= 7.10:
				phase = 12
				print("HORROR_MUSIC_RECORDER open_input=[",
					_var("录音机输入"), "] digits=",
					snappedf(_as_float(_var("录音机位数")), 0.01))
				_tap_digit("1")
		12:
			if elapsed >= 7.25:
				phase = 13
				_tap_digit("2")
		13:
			if elapsed >= 7.40:
				phase = 14
				_tap_digit("3")
		14:
			if elapsed >= 7.55:
				phase = 15
				_tap_digit("4")
		15:
			if elapsed >= 8.15:
				phase = 16
				print("HORROR_MUSIC_WRONG miss=", _miss(),
					" playing=", _var("录音机放音"),
					" last=[", _var("录音机记录"), "]",
					" alarm=", _has_label("刺耳的警报"),
					" stagelight=", _light_visible())
				_tap_digit("1")
		16:
			if elapsed >= 8.30:
				phase = 17
				_tap_digit("1")
		17:
			if elapsed >= 8.45:
				phase = 18
				_tap_digit("1")
		18:
			if elapsed >= 8.60:
				phase = 19
				_tap_digit("1")
		19:
			if elapsed >= 9.25:
				phase = 20
				print("HORROR_MUSIC_ALARM miss=", _miss(),
					" monster=", _var("怪物入场"),
					" state=", _state(),
					" last=[", _var("录音机记录"), "]")
				_stand("安全屋区域")
				if enemy != null:
					enemy.global_position = Vector3(0.0, 1.0, 12.0)
				stage_before = _stage_dist()
				_tap_digit("3")
		20:
			if elapsed >= 9.40:
				phase = 21
				_tap_digit("1")
		21:
			if elapsed >= 9.55:
				phase = 22
				_tap_digit("4")
		22:
			if elapsed >= 9.70:
				phase = 23
				_tap_digit("2")
		23:
			if elapsed >= 10.50:
				phase = 24
				print("HORROR_MUSIC_CORRECT playing=", _var("录音机放音"),
					" last=[", _var("录音机记录"), "]",
					" stagelight=", _light_visible(),
					" lure=", _has_label("练习曲"),
					" guide3=", _has_label("上去拿"),
					" state=", _state(),
					" dstage=", snappedf(_stage_dist() - stage_before, 0.01))
				if enemy != null:
					enemy.global_position = Vector3(0.0, 1.0, 60.0)
				_stand("去医务室")
		24:
			if elapsed >= 10.85:
				phase = 25
				_tap_e()
		25:
			if elapsed >= 11.40:
				phase = 26
				print("HORROR_MUSIC_DOOR_LOCKED alive=",
					is_instance_valid(world), " tape=", _tape(),
					" hint=", _has_label("侧门锁着"),
					" scene=", _current_scene_name())
				_stand("磁带")
		26:
			if elapsed >= 11.65:
				phase = 27
				_tap_e()
		27:
			if elapsed >= 12.25:
				phase = 28
				var gone := world.find_child("磁带", true, false) == null
				print("HORROR_MUSIC_TAPE tape=", _tape(),
					" removed=", gone, " count=", _count(),
					" hint=", _has_label("拿到磁带"),
					" guide4=", _has_label("推开侧门"))
				# 隐藏笔记(第 169 轮):先传过去,下一阶段再打印/走人
				_put_at("音乐教室笔记")
		28:
			if elapsed >= 12.50:
				phase = 29
				print("HORROR_NOTE_MUSIC hidden=",
					snappedf(_as_float(_var("隐藏笔记数")), 0.1))
				_stand("去医务室")
		29:
			if elapsed >= 12.75:
				phase = 30
				get_tree().current_scene = null
				_tap_e()
		30:
			if elapsed >= 14.20:
				phase = 31
				print("HORROR_MUSIC_DOOR_OPEN scene=",
					_current_scene_name())
				print("HORROR_MUSIC_DONE total=", snappedf(elapsed, 0.1))
				set_physics_process(false)
				get_tree().quit()


''' % {"music": MUSIC_SCENE}



PROBE_MED = PROBE_HEAD + '''

# ── 第 4 章 医务室(M1b)──────────────────────────────────────────
# 一趟跑完:病历不够 -> 侧门锁着 -> 三页病历 -> 两块电池(电量 +40)
#   -> 躲进帘子(躲藏中=true / 相机切走 / 人隐藏 / 怪看不见)-> 出来
#   -> 被抓回安全屋(病历/电量都还在)-> 侧门 -> 楼梯间。
var hide_cam_seen := ""
var cam_up_base := 0.0
var hide_cam_y := 0.0
# ── 低电闪烁(第一百八十六轮)──────────────────────────────────────
# 全部既有断言跑完后的**尾段**:注入电量,对手电 light_energy 分三个
# 窗口采样 —— 基线(应稳)/ 低电(应波动)/ 回满(应回稳)。
var flk_torch: Light3D = null
var flk_win := -1
var flk_t := 0.0
var flk_wmin := [999.0, 999.0, 999.0]
var flk_wmax := [-999.0, -999.0, -999.0]


func _sample_flick() -> void:
	if flk_torch == null or not is_instance_valid(flk_torch):
		return
	if not flk_torch.visible:
		return
	var e: float = flk_torch.light_energy
	flk_wmin[flk_win] = minf(flk_wmin[flk_win], e)
	flk_wmax[flk_win] = maxf(flk_wmax[flk_win], e)


func _ready() -> void:
	# 旧档:只有『纸条数』,没有病历/电池这些新键 —— 读到就当 0,不许崩。
	var file := FileAccess.open("user://gk_save1.json", FileAccess.WRITE)
	if file != null:
		# 第 170 轮:音乐教室存过档,信封里隐藏笔记数已累计到 2。
		file.store_string('{"纸条数": 3, "隐藏笔记数": 2}')
		file.close()
	_boot("%(med)s", "医务室玩家", "医务室怪物")
	_find_areas(["病历1", "病历2", "病历3", "电池1", "电池2", "躲藏1", "躲藏2",
			"医务室笔记", "医务室惊吓区",
		"去楼梯间", "医务室安全屋区域"])


func _stand(name: String) -> bool:
	if not spots.has(name):
		return false
	_put(spots[name] + Vector3(0.0, 0.6, 0.0))
	return true


func _tap_e() -> void:
	_tap("key_e", KEY_E)


func _count() -> String:
	return str(snappedf(_as_float(_var("病历数")), 0.01))


func _batteries() -> String:
	return str(snappedf(_as_float(_var("电池数")), 0.01))


func _charge() -> String:
	return str(snappedf(_as_float(_var("手电电量")), 0.01))


func _cam_name() -> String:
	var cam = get_viewport().get_camera_3d()
	if cam == null:
		return "<null>"
	return String(cam.name)


func _player_hidden() -> bool:
	if player == null:
		return false
	return not bool(player.visible)


func _current_scene_name() -> String:
	var scene := get_tree().current_scene
	if scene == null:
		return "<null>"
	return String(scene.name)


func _savecheck() -> String:
	var file := FileAccess.open("user://gk_save1.json", FileAccess.READ)
	if file == null:
		return "save=missing"
	var text := file.get_as_text()
	file.close()
	return "save_check=" + str("gk_check" in text) + \
		" save_notes=" + str("纸条数" in text)


func _physics_process(delta: float) -> void:
	elapsed += delta
	match phase:
		0:
			if elapsed >= 0.30:
				phase = 1
				print("HORROR_MED_START player=", player != null,
					" enemy=", enemy != null, " spots=", spots.size(),
					" state=", _state(),
					" sheets=", _count(), " batteries=", _batteries(),
					" hiding=", _var("躲藏中"), " battery=", _charge(),
					" checkpoint=", _var("检查点"),
					" logopen=", _var("日志打开"),
					" stale=", _has_label("进度已保存"),
					" guide1=", _has_label("摸黑找齐"))
				if enemy != null:
					enemy.global_position = Vector3(0.0, 1.0, 60.0)
				_stand("去楼梯间")
		1:
			if elapsed >= 0.55:
				phase = 2
				_tap_e()
				# 惊吓演出(第 175 轮):穿档案间门洞(玩家必经之路)。⚠ 和
				# 读病历的 _stand 要**分帧** —— 同帧"传走+按 E"时交互回调
				# 还没触发,病历计数会整体滞后一拍(实测)。
				_stand("医务室惊吓区")
		2:
			if elapsed >= 1.05:
				phase = 3
				print("HORROR_MED_SCARE shake=",
					snappedf(float(_var("_gk_shake_left")), 0.01),
					" bus=", snappedf(AudioServer.get_bus_volume_db(0), 0.01))
				print("HORROR_MED_DOOR_LOCKED alive=", is_instance_valid(world),
					" sheets=", _count(),
					" hint=", _has_label("侧门锁着"),
					" scene=", _current_scene_name())
				_stand("病历1")
		3:
			if elapsed >= 1.30:
				phase = 4
				_tap_e()
		4:
			if elapsed >= 1.75:
				phase = 5
				print("HORROR_MED_SHEET n=1 count=", _count(),
					" target=", _var("交互目标"),
					" tip=", _has_label("[E] 读病历"))
				_stand("病历2")
		5:
			if elapsed >= 2.00:
				phase = 6
				_tap_e()
		6:
			if elapsed >= 2.45:
				phase = 7
				print("HORROR_MED_SHEET n=2 count=", _count())
				_stand("病历3")
		7:
			if elapsed >= 2.70:
				phase = 8
				_tap_e()
		8:
			if elapsed >= 3.15:
				phase = 9
				print("HORROR_MED_SHEET n=3 count=", _count(),
					" reveal=", _has_label("三页病历"),
					" guide2=", _has_label("侧门在东北角"))
				_stand("电池1")
		9:
			if elapsed >= 3.40:
				phase = 10
				_tap_e()
		10:
			if elapsed >= 3.90:
				phase = 11
				print("HORROR_MED_BATTERY n=1 count=", _batteries(),
					" charge=", _charge(),
					" gone=", world.find_child("电池1", true, false) == null)
				_stand("电池2")
		11:
			if elapsed >= 4.15:
				phase = 12
				_tap_e()
		12:
			if elapsed >= 4.65:
				phase = 13
				print("HORROR_MED_BATTERY n=2 count=", _batteries(),
					" charge=", _charge())
				_stand("躲藏1")
		13:
			if elapsed >= 4.90:
				phase = 14
				_tap_e()
		14:
			if elapsed >= 5.45:
				phase = 15
				print("HORROR_MED_HIDE hiding=", _var("躲藏中"),
					" cam=", _cam_name(),
					" hidden=", _player_hidden(),
					" curtain=", snappedf(_curtain_dist(), 0.01),
					" enemy_dist=", snappedf(_enemy_dist(), 0.01),
					" battery=", _charge())
				# 躲着的时候按 Ctrl:躲藏机位是**场景里那台**,蹲走脚本不许压它
				hide_cam_y = _cam_y()
				if InputMap.has_action("key_left_ctrl"):
					Input.action_press("key_left_ctrl")
		15:
			if elapsed >= 6.10:
				phase = 16
				Input.action_release("key_left_ctrl")
				print("HORROR_MED_HIDDEN_CROUCH cam=", _cam_name(),
					" cam_drift=", snappedf(_cam_y() - hide_cam_y, 0.001))
				_tap_e()
		16:
			if elapsed >= 7.00:
				phase = 17
				print("HORROR_MED_UNHIDE hiding=", _var("躲藏中"),
					" cam=", _cam_name(),
					" hidden=", _player_hidden(),
					" curtain_front=", snappedf(_curtain_front_dist(), 0.01))
				# 站起来之后按住 Ctrl:侧视机位应该被往下压 ~0.62
				cam_up_base = _cam_up()
				if InputMap.has_action("key_left_ctrl"):
					Input.action_press("key_left_ctrl")
		17:
			if elapsed >= 7.80:
				phase = 18
				Input.action_release("key_left_ctrl")
				print("HORROR_MED_CROUCH_DIP cam=", _cam_name(),
					" cam_drop=", snappedf(cam_up_base - _cam_up(), 0.001))
				# 被抓一次(挪到怪物跟前)-> 失败链 + 回安全屋
				if enemy != null:
					_put(enemy.global_position + Vector3(0.5, 1.0, 0.0))
		18:
			if elapsed >= 10.20:
				phase = 19
				print("HORROR_MED_FAIL paused=", get_tree().paused,
					" fall=", _var("_gk_fall_cam") != null,
					" safehouse=", _find_button("回到安全屋") != null,
					" back=", _find_button("回到标题") != null)
				var home := _find_button("回到安全屋")
				if home != null:
					home.pressed.emit()
		19:
			if elapsed >= 10.80:
				phase = 20
				var home_pos := Vector3.ZERO
				if spots.has("医务室安全屋区域"):
					home_pos = spots["医务室安全屋区域"]
				print("HORROR_MED_SAFEROOM paused=", get_tree().paused,
					" alive=", is_instance_valid(world),
					" dist=", snappedf(
						player.global_position.distance_to(home_pos), 0.01),
					" checkpoint=", _var("检查点"),
					" sheets=", _count(), " battery=", _charge(), " ",
					_savecheck())
				# 隐藏笔记(第 169 轮):先传过去,下一阶段再打印/走人
				_put_at("医务室笔记")
		20:
			if elapsed >= 11.05:
				phase = 21
				print("HORROR_NOTE_MED hidden=",
					snappedf(_as_float(_var("隐藏笔记数")), 0.1))
				get_tree().current_scene = null
				_stand("去楼梯间")
		21:
			if elapsed >= 11.30:
				phase = 22
				_tap_e()
		22:
			if elapsed >= 12.80:
				phase = 23
				print("HORROR_MED_DOOR_OPEN scene=", _current_scene_name())
				print("HORROR_MED_DONE total=", snappedf(elapsed, 0.1))
				# ── 低电闪烁行为验证(第一百八十六轮)────────────────────
				# 注入手电电量,三窗口采样亮度:基线 1.0s(应稳)-> 低电
				# 2.5s(FAULT 应波动)-> 回满 1.0s(『停止闪烁』应把亮度
				# 写回基准 = 回稳)。放在全部既有断言之后,不碰它们的时序。
				flk_torch = world.find_child("医务室手电筒", true, false) \
					as Light3D
				flk_win = 0
				flk_t = 0.0
		23:
			# 窗 0:基线(电量 ~40,不该闪)
			flk_t += delta
			_sample_flick()
			if flk_t >= 0.8:
				flk_win = 1
				flk_t = 0.0
				flk_wmin[1] = 999.0
				flk_wmax[1] = -999.0
				world.set("手电电量", 15.0)
				phase = 24
		24:
			# 窗 1:低电(<= 20 -> 『灯光_闪烁』FAULT 登记,亮度应波动)
			flk_t += delta
			_sample_flick()
			if flk_t >= 2.0:
				flk_win = 2
				flk_t = 0.0
				flk_wmin[2] = 999.0
				flk_wmax[2] = -999.0
				world.set("手电电量", 90.0)
				phase = 25
		25:
			# 窗 2:回满(> 20 -> 每帧 stop,亮度恢复基准 = 回稳)
			flk_t += delta
			_sample_flick()
			if flk_t >= 0.8:
				print("HORROR_MED_FLICKER torch=", flk_torch != null,
					" w0=", snappedf(flk_wmin[0], 0.01),
					"..", snappedf(flk_wmax[0], 0.01),
					" w1=", snappedf(flk_wmin[1], 0.01),
					"..", snappedf(flk_wmax[1], 0.01),
					" w2=", snappedf(flk_wmin[2], 0.01),
					"..", snappedf(flk_wmax[2], 0.01))
				set_physics_process(false)
				get_tree().quit()


## "出来"之后应该站在帘子前面:量到帘子的水平距离(±2 米内算过)
func _curtain_front_dist() -> float:
	if player == null:
		return -1.0
	var anchor: Node3D = world.find_child("医务室帘子1", true, false) as Node3D
	if anchor == null:
		anchor = world.find_child("躲藏1", true, false) as Node3D
	if anchor == null:
		return -1.0
	var here := (player as Node3D).global_position
	var there := anchor.global_position
	return sqrt((here.x - there.x) * (here.x - there.x)
		+ (here.z - there.z) * (here.z - there.z))


## 躲进去之后身体离帘子有多远(证明真的挪走了,不是站在原地看不见)
func _curtain_dist() -> float:
	if player == null or not spots.has("躲藏1"):
		return -1.0
	var spot: Vector3 = spots["躲藏1"]
	return (player as Node3D).global_position.distance_to(spot)



func _cam_y() -> float:
	var cam := get_viewport().get_camera_3d()
	if cam == null:
		return -999.0
	return (cam as Node3D).global_position.y


func _enemy_dist() -> float:
	if enemy == null or not is_instance_valid(enemy) or player == null:
		return -1.0
	return (enemy as Node3D).global_position.distance_to(
		(player as Node3D).global_position)


''' % {"med": MED_SCENE}


PROBE_REALPLAY = PROBE_HEAD + '''

# ── 真实游玩探针(第一百七十六轮,用户真机反馈三题)──────────────────
# **零瞬移**:全程合成输入(Input.action_press)驱动玩家,和真人按键盘走
# 同一条输入路径。验证:① 横移时人物转向移动方向;② 被抓暂停后鼠标切
# VISIBLE 且**真实点击**(warp + 按下/抬起)『回到安全屋』生效;③ 采样
# 玩家-怪物距离(敌人牵制数据)。

var rot_before := 0.0
var max_turned := 0.0
var spawn_pos := Vector3.ZERO
var enemy_spawn := Vector3.ZERO
var click_done := false
var mode_seen := -1
var samples: Array[String] = []


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	var file := FileAccess.open("user://gk_save1.json", FileAccess.WRITE)
	if file != null:
		file.store_string("{}")
		file.close()
	_boot("%(corridor)s", "走廊玩家", "怪物")
	_find_areas(["安全屋"])
	if player != null:
		rot_before = (player as Node3D).rotation.y
		spawn_pos = (player as Node3D).global_position
	if enemy != null:
		enemy_spawn = (enemy as Node3D).global_position


func _sample(tag: String) -> void:
	var d := -1.0
	if player != null and enemy != null:
		d = ((player as Node3D).global_position
			.distance_to((enemy as Node3D).global_position))
	samples.append("%%s:%%.1fm" %% [tag, d])


func _physics_process(delta: float) -> void:
	elapsed += delta
	if player != null:
		var t := absf(angle_difference(rot_before,
			(player as Node3D).rotation.y))
		if t > max_turned:
			max_turned = t
	match phase:
		0:
			if elapsed >= 0.4:
				phase = 1
				_sample("出生")
				print("REALPLAY_INIT rot_before=", snappedf(rot_before, 0.01),
					" spawn=", str(spawn_pos),
					" enemy_spawn=", str(enemy_spawn))
		1:
			if elapsed >= 6.2:
				# 真实按住 S:朝怪物巡逻线走(出生点在视野边缘 9.4 米,
				# 走两步就进视线 —— 第 176 轮出生点已从 (-12,-8) 挪近)
				Input.action_press("move_down")
				phase = 2
		2:
			if elapsed >= 7.0:
				phase = 3
				print("REALPLAY_TURN turned=", snappedf(max_turned, 0.01))
				# 脚步声(第 182 轮):走起来后角色下应该挂着一台脚步播放器
				var step_found := ""
				for child in (player as Node).get_children():
					if child is AudioStreamPlayer:
						var sp2 = (child as AudioStreamPlayer).stream
						if sp2 != null and str(sp2.resource_path).contains("footstep"):
							step_found = str(sp2.resource_path)
				print("REALPLAY_STEP found=", step_found != "", " ", step_found)
		3:
			# 相向而行:怪看见(视野 9)就迎面追上,直到被抓
			if get_tree().paused and _find_button("回到安全屋") != null:
				phase = 4
				mode_seen = int(Input.mouse_mode)
				_sample("被抓")
				# 追逐切曲(第 179 轮):被抓时应该正放着追逐曲
				var music = world.get("_gk_music")
				var mpath := ""
				if music != null and (music as AudioStreamPlayer).stream != null:
					mpath = str((music as AudioStreamPlayer).stream.resource_path)
				print("REALPLAY_MUSIC playing=",
					music != null and (music as AudioStreamPlayer).playing,
					" path=", mpath)
				print("REALPLAY_MOUSE mode=", mode_seen,
					" visible=", mode_seen == Input.MOUSE_MODE_VISIBLE,
					" elapsed=", snappedf(elapsed, 0.1))
				# **真实点击**:warp 到按钮中心 + 按下/抬起(不走 pressed.emit)
				var btn := _find_button("回到安全屋")
				if btn is Control:
					var center: Vector2 = (btn as Control).get_global_rect()\
						.get_center()
					get_viewport().warp_mouse(center)
					var down := InputEventMouseButton.new()
					down.button_index = MOUSE_BUTTON_LEFT
					down.pressed = true
					down.position = center
					down.global_position = center
					Input.parse_input_event(down)
					var up := InputEventMouseButton.new()
					up.button_index = MOUSE_BUTTON_LEFT
					up.pressed = false
					up.position = center
					up.global_position = center
					Input.parse_input_event(up)
					click_done = true
		4:
			if elapsed >= 14.0 and click_done:
				phase = 5
				var home := _find_button("回到安全屋")
				var back := (not get_tree().paused) and home == null
				var dist := -1.0
				if player != null and spots.has("安全屋"):
					dist = ((player as Node3D).global_position
						.distance_to(spots["安全屋"]))
				print("REALPLAY_CLICK clicked=", click_done,
					" back=", back, " paused=", get_tree().paused,
					" dist_home=", snappedf(dist, 0.1))
				for item in samples:
					print("REALPLAY_DIST ", item)
				print("REALPLAY_DONE total=", snappedf(elapsed, 0.1))
				set_physics_process(false)
				get_tree().quit()
''' % {"corridor": CORRIDOR_SCENE}

PROBE_STAIRS = PROBE_HEAD + '''
func _ready() -> void:
	# 模拟"前面几章都看过档":纸条数 3 + 病历数 3(= 真结局条件 6)。
	var file := FileAccess.open("user://gk_save1.json", FileAccess.WRITE)
	if file != null:
		file.store_string('{"纸条数": 3, "病历数": 3}')
		file.close()
	_boot("%(stairs)s", "楼梯间玩家", "楼梯间追击者")
	_find_areas(["楼梯间一层", "楼梯间二层", "楼梯间三层", "楼梯间天台",
			"楼梯间安全屋区域"])


# 灯"亮着吗":断电用的是『灯光_设置』(先淡到 0 再 visible=false),
# 所以两个条件都要看 —— 只看 visible 会在 0.35 秒渐变期间读到 true。
func _lit(name: String) -> bool:
	var node := world.find_child(name, true, false)
	if node == null:
		return true
	return bool(node.visible) and (node as Light3D).light_energy > 0.001


var chaser_from := Vector3.ZERO


func _physics_process(delta: float) -> void:
	elapsed += delta
	match phase:
		0:
			if elapsed >= 0.3:
				phase = 1
				print("HORROR_STAIRS_START player=", player != null,
					" notes=", _var("纸条数"), " records=", _var("病历数"),
					" spots=", spots.size(), " chaser=", enemy != null,
					" lit=", _lit("楼梯间应急灯1"), " ai=", _state())
				# 第 163 轮:『角色_走路摆动』有没有真的登记上。
				# 只 grep 生成代码是不够的 —— 关键字被多引一层(`"\"臂\""`)
				# 时,代码照样生成、照样编译,却**一个零件都认不到**。
				# 第 164+1 轮:怪物也接了走路摆动(目标=怪物自身,
				# 零件在 _gk_body_links 配对的网格节点下),所以应有两条
				# 登记 —— 第 0 条是玩家,其余是怪物。
				var walks = player.get("_gk_walks") if player != null else null
				var entries := 0
				var arms0 := 0
				var legs0 := 0
				var monster_arms := 0
				var monster_legs := 0
				if walks != null:
					entries = walks.size()
					for i in range(walks.size()):
						var a = walks[i].get("arms", []).size()
						var l = walks[i].get("legs", []).size()
						if i == 0:
							arms0 = a
							legs0 = l
						else:
							monster_arms = maxi(monster_arms, a)
							monster_legs = maxi(monster_legs, l)
				print("HORROR_WALK entries=", entries, " player_arms=", arms0,
						" player_legs=", legs0, " monster_arms=", monster_arms,
						" monster_legs=", monster_legs)
		1:
			if elapsed >= 1.3:
				phase = 2
				chaser_from = (enemy as Node3D).global_position
		2:
			if elapsed >= 2.3:
				phase = 3
				# 断电前追兵必须**站着不动**:AI 是在断电那一刻才『AI_启用』的,
				# 提前跑起来就变成"一进门就被追"(设计上不成立)。
				print("HORROR_STAIRS_BEFORE moved=%.2f" % chaser_from.distance_to(
					(enemy as Node3D).global_position),
					" ai=", _state())
		3:
			if elapsed >= 7.4:
				phase = 4
				print("HORROR_STAIRS_BLACKOUT lit=", _lit("楼梯间应急灯1"),
					" lit3=", _lit("楼梯间应急灯3"),
					" red=", _lit("楼梯间天台红灯"),
					" ai=", _state(),
					" moved=%.2f" % chaser_from.distance_to(
						(enemy as Node3D).global_position))
				_put_at("楼梯间天台")
		4:
			if elapsed >= 8.4:
				phase = 5
				var panel = _var("_gk_choice_panel")
				var box = _var("_gk_choice_box")
				print("HORROR_DOOR picked=", _var("_gk_choice_picked"),
					" panel=", panel != null and panel.visible,
					" paused=", get_tree().paused,
					" options=", box.get_child_count() if box != null else -1)
				_send_key(KEY_3, true)
		5:
			if elapsed >= 8.6:
				phase = 6
				_send_key(KEY_3, false)
		6:
			if elapsed >= 9.4:
				phase = 7
				print("HORROR_ENDING ended=", _var("结局"),
					" saved=", FileAccess.file_exists("user://gk_save1.json"),
					" picked=", _var("_gk_choice_picked"),
					" back=", _find_button("回到标题") != null)
				# 第 167 轮:楼梯换成了**真台阶**(级高 0.28) —— 追击者必须
				# 真的爬上来了(它从门厅 z=0 出发,天台地面 z=8.4)。爬不上
				# 就是四关探测失效,这条位移/高度断言能当场抓住。
				print("HORROR_STAIRS_CLIMB chaser_z=",
					(enemy as Node3D).global_position.y if enemy != null else -1.0)
				print("HORROR_SAVE_FORMAT check=",
					FileAccess.get_file_as_string("user://gk_save1.json"
						).contains("gk_check"))
				var back_btn := _find_button("回到标题")
				if back_btn != null:
					back_btn.pressed.emit()
					print("HORROR_STAIRS_UNPAUSE paused=", get_tree().paused)
				print("HORROR_STAIRS_DONE total=", snappedf(elapsed, 0.1))
				set_physics_process(false)
				get_tree().quit()
'''.replace("%(stairs)s", STAIRS_SCENE)

# ── 残缺存档:楼梯间门口不该把玩家困死(旧版是死局)─────────────────
PROBE_STAIRS_SHORT = PROBE_HEAD + '''

func _ready() -> void:
	# 残缺存档:只有 1 张纸条、没有病历。以前门口会把人困死;
	# 现在天台照开,只是"真结局"那条不给(= 记忆缺页,走结局 2)。
	var file := FileAccess.open("user://gk_save1.json", FileAccess.WRITE)
	if file != null:
		file.store_string('{"纸条数": 1}')
		file.close()
	_boot("%(stairs)s", "楼梯间玩家", "")
	_find_areas(["楼梯间天台"])


func _physics_process(delta: float) -> void:
	elapsed += delta
	match phase:
		0:
			if elapsed >= 0.4:
				phase = 1
				_put_at("楼梯间天台")
		1:
			if elapsed >= 1.4:
				phase = 2
				var panel = _var("_gk_choice_panel")
				var box = _var("_gk_choice_box")
				print("HORROR_STAIRS_SHORT notes=", _var("纸条数"),
					" records=", _var("病历数"),
					" panel=", panel != null and panel.visible,
					" options=", box.get_child_count() if box != null else -1)
				_send_key(KEY_3, true)
		2:
			if elapsed >= 1.6:
				phase = 3
				_send_key(KEY_3, false)
		3:
			if elapsed >= 2.4:
				# 直接读字幕那块 Label 的正文:比 "_has_label(片段)" 更能说明
				# "到底有没有显示",失败时日志里就有原文。
				var sub = world.get("_gk_dialog_text")
				var sub_text := "<no-panel>" if sub == null else String(sub.text)
				print("HORROR_STAIRS_SHORT_END ended=", _var("结局"),
					# ⚠ 别用 `"片段" in text` / `text.find("片段")` 判中文子串:
					#   实测同一段文字 print 出来是对的,find 却回 -1(编码层
					#   的坑)。这里只判"有没有一段非空字幕",原文照打给日志看。
					" subtitle=", sub != null and sub_text.length() > 4,
					" sub=", sub_text)
				print("HORROR_STAIRS_SHORT_DONE")
				set_physics_process(false)
				get_tree().quit()
''' % {"stairs": STAIRS_SCENE}

# ── 第九十三轮『防改档』:两趟补充探针 ──────────────────────────────
# 上面那趟楼梯间验证的是**老存档(裸 JSON)照旧能读**;这里把另外两件事
# 钉死:①生成代码自己写的信封,自己读得回来(往返);②被手改过的信封
# (哈希对不上)当没有存档 —— 纸条数回默认 0、门口不放行。
PROBE_ENVELOPE = PROBE_HEAD + '''

func _ready() -> void:
	# ⚠ **不**预先写档:读的就是上一趟楼梯间用生成代码写下的那个信封。
	#   读得回来 = "自己写的档自己认得"(哈希算在原文上,不走重新序列化)。
	_boot("%(stairs)s", "楼梯间玩家", "")


func _physics_process(delta: float) -> void:
	elapsed += delta
	if phase == 0 and elapsed >= 0.5:
		phase = 1
		print("HORROR_ENVELOPE notes=", _var("纸条数"))
		set_physics_process(false)
		get_tree().quit()
''' % {"stairs": STAIRS_SCENE}

PROBE_TAMPER = PROBE_HEAD + '''

func _ready() -> void:
	# 手改过的档:格式还像那么回事,但 gk_check 是假哈希(模拟"在记事本里
	# 把 3 改成 9999"—— 数值一动手写的那串哈希就对不上了)。
	var file := FileAccess.open("user://gk_save1.json", FileAccess.WRITE)
	if file != null:
		file.store_string('{"gk_check": "0000deadbeef", "gk_data": "{\\"纸条数\\": 9999}"}')
		file.close()
	_boot("%(stairs)s", "楼梯间玩家", "")
	_find_areas(["楼梯间天台"])


func _physics_process(delta: float) -> void:
	elapsed += delta
	match phase:
		0:
			if elapsed >= 0.2:
				phase = 1
				_put_at("楼梯间天台")
		1:
			if elapsed >= 1.0:
				phase = 2
				var panel = _var("_gk_choice_panel")
				var box = _var("_gk_choice_box")
				print("HORROR_TAMPER notes=", _var("纸条数"),
					" panel=", panel != null and panel.visible,
					" options=", box.get_child_count() if box != null else -1)
				print("HORROR_TAMPER_DONE")
				set_physics_process(false)
				get_tree().quit()
''' % {"stairs": STAIRS_SCENE}

# ── M0.5 ① 笔记日志跨场景:新信封(纸条数 3 + 三张都已读)──────────
# 落盘格式是 {"gk_check": sha256(payload + "|" + 盐), "gk_data": payload}
# (盐是生成代码里的公开常量,见 BlenderGameKit/codegen/services.py 的
#  _SAVE_SALT)。这里手工拼一份"和生成代码一模一样"的信封,专门验证
# 走廊能不能把『已读N』读回来 —— 顺便证明这三个新键**跟着信封一起**
# 被 sha256 封住(改档还是会被拒,见 PROBE_TAMPER)。
SAVE_SALT = "blendergamekit-save-v1"
JOURNAL_PAYLOAD = '{"纸条数": 3, "已读1": true, "已读2": true, "已读3": true}'
# 同一份日志要在**三关**都能开:教室由 PROBE_ROOM 量(它本来就在教室),
# 这里把同一段探针跑两趟 —— 走廊 + 楼梯间(不同的场景 / 玩家名)。
JOURNAL_TARGETS = (
    ("JOURNAL", CORRIDOR_SCENE, "走廊玩家", "怪物"),
    ("JOURNAL_STAIRS", STAIRS_SCENE, "楼梯间玩家", ""),
)

PROBE_JOURNAL = PROBE_HEAD + '''

func _ready() -> void:
	# 开局喂一份"教室门口存下的"新信封 —— 纸条数 3 + 三张都已读。
	# ⚠ 别在 Python 里手工拼 JSON 再塞进 GDScript 字符串:嵌套的引号会被
	#   GDScript 的字符串转义吃掉,写进文件的就不是合法 JSON(踩过)。
	#   这里用**游戏自己那套写法**:sha256(原文 + "|" + 盐) 放 gk_check,
	#   原文放 gk_data,再由 JSON.stringify 落盘。
	var payload := '%(payload)s'
	var box := {"gk_check": (payload + "|" + "%(salt)s").sha256_text(),
		"gk_data": payload}
	var file := FileAccess.open("user://gk_save1.json", FileAccess.WRITE)
	if file != null:
		file.store_string(JSON.stringify(box))
		file.close()
	_boot("%(scene)s", "%(player)s", "%(enemy)s")


func _physics_process(delta: float) -> void:
	elapsed += delta
	match phase:
		0:
			if elapsed >= 0.7:
				phase = 1
				# 这一关的日志从来没被打开过:开局必须是关的
				print("HORROR_%(tag)s_BEFORE open=", _var("日志打开"),
					" panel=", _panel_visible(), " line1=", _line_text(1))
				_send_key(KEY_J, true)
		1:
			if elapsed >= 0.9:
				phase = 2
				_send_key(KEY_J, false)
		2:
			if elapsed >= 1.5:
				phase = 3
				# 三行正文都该是**教室那一关读过的那三张纸条**
				print("HORROR_%(tag)s_OPEN open=", _var("日志打开"),
					" panel=", _panel_visible(),
					" line1=", _line_text(1), " line2=", _line_text(2),
					" line3=", _line_text(3))
				_send_key(KEY_ESCAPE, true)
		3:
			if elapsed >= 1.7:
				phase = 4
				_send_key(KEY_ESCAPE, false)
		4:
			if elapsed >= 2.3:
				phase = 5
				print("HORROR_%(tag)s_ESC open=", _var("日志打开"),
					" panel=", _panel_visible())
				print("HORROR_%(tag)s_DONE")
				set_physics_process(false)
				get_tree().quit()
'''  # 占位不在这里填:同一段探针要跑走廊 / 楼梯间两趟,由调用方带参数

# ── M0.5 ⑤:真视线射线到底能不能接(实测,不是推断)────────────────
# 想接的是"看向目标 + E"。这里在教室里真的打三条射线,把结论打出来:
#   ① 严格照插件的出码方式(只设 collision_mask,不开 collide_with_areas)
#      -> 从相机沿准星打 12 米,打到的是谁?
#   ② 再打开 collide_with_areas(插件目前**没有**这个开关)
#   ③ 再把玩家自己排除掉(插件目前**也没有**这个开关)
# 三条的差异就是"插件还差什么"。
PROBE_RAY = PROBE_HEAD + '''

var note_pos := Vector3.ZERO


func _cast(cam: Camera3D, length: float, areas: bool, exclude: Array) -> String:
	var from := cam.global_position
	var to := from + (-cam.global_transform.basis.z) * length
	var query := PhysicsRayQueryParameters3D.create(from, to, 1, exclude)
	query.collide_with_areas = areas
	var hit: Dictionary = get_viewport().world_3d.direct_space_state.intersect_ray(query)
	if hit.is_empty():
		return "-"
	var collider = hit["collider"]
	if collider == null:
		return "<?>"
	return String((collider as Node).name)


func _ready() -> void:
	_boot("%(room)s", "教室玩家", "")
	_find_areas(["纸条1"])


func _physics_process(delta: float) -> void:
	elapsed += delta
	match phase:
		0:
			if elapsed >= 0.3:
				phase = 1
				if spots.has("纸条1"):
					note_pos = spots["纸条1"]
				# ⚠ 场景里有**两个**『纸条1』:glb 里的网格(MeshInstance3D)
				#   和 Colliders 下的触发体(Area3D)。按类型找才是触发体。
				var note_areas := world.find_children("纸条1", "Area3D", true, false)
				print("HORROR_RAY_NOTE at=", note_pos,
					" area_count=", note_areas.size(),
					" area=", "-" if note_areas.is_empty() else note_areas[0].get_class())
				# 让纸条正好在玩家正前方 2 米(相机在玩家身后 5 米)
				_put(note_pos + Vector3(0.0, 1.0, 2.0))
		1:
			# 等开场运镜演完、相机回到第三人称(不然量的是门口特写那台)
			if elapsed >= 7.0:
				phase = 2
				var cam := get_viewport().get_camera_3d()
				if cam == null:
					print("HORROR_RAY no_camera")
					print("HORROR_RAY_DONE")
					set_physics_process(false)
					get_tree().quit()
					return
				var exclude: Array[RID] = []
				if player is CollisionObject3D:
					exclude.append((player as CollisionObject3D).get_rid())
				# 先自证"这套查询本身能用":从玩家头顶垂直往下打 20 米,
				# 命中地板就说明 direct_space_state / 碰撞层没问题。
				var here := Vector3.ZERO
				if player != null:
					here = (player as Node3D).global_position
				var down := PhysicsRayQueryParameters3D.create(
					here + Vector3(0.0, 2.0, 0.0),
					here + Vector3(0.0, -20.0, 0.0), 1, [])
				var down_hit: Dictionary = get_viewport().world_3d.direct_space_state.intersect_ray(down)
				print("HORROR_RAY probe_player=", here, " cam_at=",
					cam.global_position,
					" down=", "-" if down_hit.is_empty() else String((down_hit["collider"] as Node).name))
				print("HORROR_RAY cam=", cam.name,
					" plugin_way=", _cast(cam, 12.0, false, []),
					" with_areas=", _cast(cam, 12.0, true, []),
					" no_player=", _cast(cam, 12.0, true, exclude),
					" no_player_bodies=", _cast(cam, 12.0, false, exclude))
				print("HORROR_RAY_DONE")
				set_physics_process(false)
				get_tree().quit()
''' % {"room": ROOM_SCENE}


PROBE_TSCN = '''; 探针(临时文件,不写回示例)
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://{stem}.gd" id="1_probe"]

[node name="{node}" type="Node"]
script = ExtResource("1_probe")
'''


def _run_probe(godot, stem, script_text, node_name, quit_after):
    if not os.path.isdir(SRC):
        print(f"找不到示例工程 {SRC} —— 先跑 make_horror_demo.py")
        return ""
    # 固定目录**复用**不删(第一百八十三轮):safe-delete 按文件数拦
    # 批量删除,每个 run 目录 ~380 文件永远超阈值 —— 递增 runN 目录
    # 61 轮堆了 383 个 ≈ 1.7GB。copytree(dirs_exist_ok=True) 直接覆盖,
    # 每个探针一个固定目录,总量封顶在探针数(~6 个)。
    shutil.copytree(SRC, OUT,
                    ignore=shutil.ignore_patterns(".godot", "*.import"),
                    dirs_exist_ok=True)
    with open(os.path.join(OUT, f"{stem}.gd"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(script_text)
    with open(os.path.join(OUT, f"{stem}.tscn"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE_TSCN.format(stem=stem, node=node_name))
    project_file = os.path.join(OUT, "project.godot")
    with open(project_file, "r", encoding="utf-8") as handle:
        text = handle.read()
    text = re.sub(r'run/main_scene="[^"]*"',
                  f'run/main_scene="res://{stem}.tscn"', text)
    with open(project_file, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)

    def _import_and_run():
        subprocess.run([godot, "--headless", "--path", OUT, "--import"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
        done = subprocess.run([godot, "--headless", "--path", OUT,
                               "--quit-after", str(quit_after)],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
        return (done.stdout or "") + (done.stderr or "")

    out = _import_and_run()
    # ── 偶发兜底:第一次 --import 偶尔会漏掉某个 .glb(第九十五轮门禁实测
    # 撞过一次:楼梯间.glb 没进导入缓存 -> 载入 .tscn 报 "referenced
    # non-existent resource",单独复跑就绿)。重跑一次;**真缺文件的话第二次
    # 照样报**,不会被吞掉 —— 只是把"偶发的导入没齐"和"真的没有这个文件"
    # 区分开。
    if "non-existent resource" in out:
        print("    ⚠ 导入没齐(non-existent resource),重跑一次…")
        out = _import_and_run()
    for line in out.splitlines():
        if "HORROR" in line or "SCRIPT ERROR" in line or "Parse Error" in line:
            print("    " + line.strip())
    # 工作目录不在探针里自动删(第 176 轮:沙箱对 bulk-delete 有确认
    # 拦截,rmtree 会把整个探针进程杀掉)。防堆积改由"定期大扫除"兜底
    # (第 173 轮模式:horror_probe_run* 攒多了整批清)。
    return out


def _grab(text, pattern):
    match = re.search(pattern, text)
    return match.group(0) if match else "<没跑到>"


def _errors(text, problems):
    for line in text.splitlines():
        if "SCRIPT ERROR" in line or "Parse Error" in line:
            problems.append(f"Godot 脚本 / 解析错误: {line.strip()}")
            return


def _code_checks(problems):
    """检查生成脚本里的**系统分工**:关卡图管流程,角色图只管"人"。"""
    checks = {
        "scripts/game_logic_标题.gd": (
            ("change_scene_to_file", "标题切关卡的调用"),
            ("res://scenes/教室.tscn", "标题切到教室的路径"),
            ("Button.new()", "『开始游戏』按钮"),
            ("Label.new()", "标题文字"),
            ("_gk_delete_save(", "新开一局先清档"),
        ),
        "scripts/game_logic_教室.gd": (
            ("change_scene_to_file", "教室切关卡的调用"),
            ("res://scenes/走廊.tscn", "教室切到走廊的路径"),
            ("_gk_write_save(", "教室把纸条数落盘"),
            ('_gk_save_data["纸条数"]', "纸条数写进存档"),
            # 『防改档』(第九十三轮):教室的保存勾了勾选框 -> 信封 + 盐
            ("sha256_text", "存档校验和(防改档)"),
            ('"gk_check"', "带校验的信封格式"),
            ("_gk_write_save(_gk_save_path, _gk_save_data, true)",
             "防改档的写盘调用"),
            # 开场曲线运镜(第七十轮):曲线采样成 Curve3D 资源,相机沿它飞
            ("_gk_cam_fly_curve", "开场运镜的曲线轨道"),
            ("res://curves/", "运镜曲线资源(res://curves/)"),
            ("_gk_curve3d_load(", "曲线加载助手"),
            # 门禁 + HUD + 拾取即销毁(第一百五十八轮)
            ("_gk_free_with_visual(", "拾取后销毁纸条(含同名网格)"),
            ("_gk_num_text(", "纸条 HUD 的数字转文字"),
            ("还差 ", "纸条不够时的动态提示"),
            # M0:E 交互 + 笔记日志
            ("交互目标", "E 交互目标变量"),
            ("日志打开", "日志开关变量"),
            ("日志面板", "笔记日志面板"),
            ('Input.is_action_just_pressed(ACTION_KEY_E)', "E 键交互"),
            ('Input.is_action_just_pressed(ACTION_KEY_J)', "J 键日志"),
            ('Input.is_action_just_pressed(ACTION_KEY_ESC)', "Esc 关日志"),
            # M0.5:笔记"读过了"进存档信封(三个布尔,和纸条数一起被封)
            ('_gk_save_data["已读1"]', "已读1 写进存档字典"),
            ('_gk_save_data["已读3"]', "已读3 写进存档字典"),
            ('_gk_save_data.get("已读1"', "教室读回已读标记"),
        ),
        "scripts/game_logic_教室_char.gd": (
            ("移动速度", "蹲走/跑动的速度变量"),
            ("key_left_ctrl", "蹲走按键"),
            ("key_left_shift", "跑动按键"),
        ),
        "scripts/game_logic_走廊.gd": (
            ("change_scene_to_file", "走廊切关卡的调用"),
            # M1a:走廊的出口从『去楼梯间』改成『去音乐教室』(中间插了第 3 章)
            ("res://scenes/音乐教室.tscn", "走廊切到音乐教室的路径"),
            ("去音乐教室", "走廊里的出口触发区"),
            ("_gk_heart_tension", "心跳的紧张度"),
            ("get_physics_process_delta_time()", "心跳按物理帧计时"),
            ("AudioServer.get_bus_index(", "总线音量(受惊压低)"),
            ("_gk_shake_cam", "惊吓的相机震动"),
            ("ColorRect.new()", "惊吓的屏幕闪白"),
            ('get_nodes_in_group("gk_ai_hearer")', "听觉广播"),
            ("func _gk_hear_noise(", "听觉派发器"),
            ('ai_state == "去查看":', "去查看行为"),
            ("is_chasing", "AI 对外的追击布尔"),
            # 失败状态 + 一次性惊吓 + 总线恢复(第一百五十八轮)
            ("get_tree().paused", "被抓后冻住世界"),
            ("_gk_fall_cam", "被抓时的镜头倒地"),
            ("_gk_apply_pause(false", "回标题 / 重试前先解冻"),
                ("回到安全屋", "失败后的『回到安全屋』按钮"),
            ("检查点", "检查点变量"),
            # 低电闪烁(第一百八十六轮,_torch_chain 工厂,两关同享)
            ("_gk_light_flicker_start", "低电闪烁的登记(工厂)"),
            ("_gk_light_flicker_stop", "低电闪烁的停止"),
            ("if (手电电量 <= 20.0):", "低电阈值分支"),
            # 手电开必须是**布尔**(回归断言):『手电默认开』那个常量曾
            # 忘了连『值』引脚,SetVar 的 gk_value 回退默认 100.0 把它写成
            # `手电开 = 100.0`(float)—— F 开关第一次按下前脚本里就有
            # `not 100.0`。第一百八十六轮修复后加这条盯着。
            ("var 手电开: bool", "手电开是布尔(曾生成 float 100)"),
            # M1a:这个 Area3D 改名成『走廊安全屋区域』—— Blender 的物体名
            # 全局唯一,『安全屋区域』让给了音乐教室(名字契约要求)。
            ("走廊安全屋区域", "安全屋触发区"),
            ("手电电量", "手电电量"),
            ("_gk_hear_noise(", "噪音进听觉通道"),
            ("_gk_apply_pause(true", "被抓后暂停世界"),
            # M0.5 收尾:倒地要把相机**交还**弹簧臂(复活 / 重开用),
            # 不交还 = 被抓一次之后整局都用倒地机位
            ("_gk_unlock_camera(", "倒地之后把相机交还弹簧臂"),
            # M0.5 ①:同一份笔记日志在走廊也能开(J / Esc)+ 读回已读标记
            ("日志面板", "走廊也有笔记日志面板"),
            ("_gk_save_data.get(\"已读1\"", "走廊读回已读1"),
            ('Input.is_action_just_pressed(ACTION_KEY_J)', "走廊 J 开日志"),
            ('Input.is_action_just_pressed(ACTION_KEY_ESC)', "走廊 Esc 关日志"),
        ),
        "scripts/game_logic_楼梯间.gd": (
            ("_gk_read_save(", "开局载入存档"),
            ('_gk_save_data.get("纸条数"', "读回纸条数"),
            ("match _gk_choice_picked:", "结局选择的分支 match"),
            ("gk_choice_input_event(event)", "数字键 1~6 也能选"),
            ("_gk_write_save(", "结局落盘"),
            # 『防改档』(第九十三轮):结局保存也勾了 -> 写的是信封,
            # 读档自描述(`if box.has("gk_check")` + 裸 JSON 兜底)
            ("sha256_text", "存档校验和(防改档)"),
            ('if box.has("gk_check"):', "读档自描述(认信封)"),
            ("res://scenes/标题.tscn", "演完切回标题页"),
            ("_gk_apply_pause(false", "回标题前先解冻(否则标题页也冻着)"),
            ("回到标题", "结局后的回退按钮"),
            # M0.5 ①:楼梯间的第三份日志
            ("日志面板", "楼梯间也有笔记日志面板"),
            ("_gk_save_data.get(\"已读2\"", "楼梯间读回已读2"),
            ('Input.is_action_just_pressed(ACTION_KEY_J)', "楼梯间 J 开日志"),
            # ── 第 5 章(M2a,第 161 轮)──────────────────────────────
            # 断电:三盏应急灯各一句"关灯"(visible = false 或能量 0)
            ('_gk_save_data.get("病历数"', "楼梯间读回病历数(真结局条件)"),
            ("楼梯间应急灯1", "断电链提到应急灯1"),
            ("楼梯间应急灯2", "断电链提到应急灯2"),
            ("楼梯间应急灯3", "断电链提到应急灯3"),
            ('ai_state = "追击"', "断电后把追兵放进追击状态"),
            ("楼梯间追击者", "追兵的名字出现在生成代码里"),
            ("楼梯间二层", "二层区域接到关卡图"),
            ("楼梯间三层", "三层区域接到关卡图"),
            # 三选项 + 真结局分支(档案总数 >= 6)
            ('"回头救她", "独自逃出", "回头看清那张脸"', "天台三个选项"),
            ("(纸条数 + 病历数) >= 6", "真结局条件:纸条 + 病历 >= 6"),
            ("结局:回头看清那张脸", "真结局的文案"),
            ("记忆缺页", "档案不齐时的落点(不是死局)"),
        ),
        # ── 第 3 章 音乐教室(M1a)──────────────────────────────────
        "scripts/game_logic_音乐教室.gd": (
            ("change_scene_to_file", "音乐教室切关卡的调用"),
            ("res://scenes/医务室.tscn", "侧门通向医务室"),
            ("去医务室", "侧门判定区"),
            ("安全屋区域", "本关安全屋触发区"),
            ("乐谱1", "乐谱1"),
            ("乐谱3", "乐谱3"),
            ("录音机输入", "录音机输入变量"),
            ("录音机错次", "录音机错次变量"),
            ("录音机放音", "录音机放音变量"),
            ("录音机位数", "录音机位数(四位才判定)"),
            ('Input.is_action_just_pressed(ACTION_KEY_E)', "E 交互"),
            ('Input.is_action_just_pressed(ACTION_KEY_1)', "数字键 1 输入"),
            ('Input.is_action_just_pressed(ACTION_KEY_4)', "数字键 4 输入"),
            # 判定必须比的是**字符串**字面量(纯数字会被当成 int,永远不等)
            ('录音机输入 == "3142"', "四音判定(带引号的字符串比较)"),
            ("res://audio/etude.wav", "解谜成功的练习曲"),
            ("res://audio/alarm.wav", "输错的警报声"),
            ("_gk_set_light(", "舞台灯灭"),
            ("_gk_light_flicker_start(", "警报灯闪"),
            ("_gk_hear_noise(", "诱饵/警报进听觉通道"),
            ("怪物入场", "错满两次才放怪物(只放一次)"),
            ("_gk_apply_pause(false", "回安全屋 / 回标题前先解冻"),
            ("回到安全屋", "被抓后的『回到安全屋』按钮"),
            ("_gk_unlock_camera(", "倒地之后把相机交还弹簧臂"),
        ),
        # ── 第 4 章 医务室(M1b)────────────────────────────────────
        "scripts/game_logic_医务室.gd": (
            ("change_scene_to_file", "医务室切关卡的调用"),
            ("res://scenes/楼梯间.tscn", "侧门通向楼梯间"),
            ("去楼梯间", "侧门判定区"),
            ("医务室安全屋区域", "本关安全屋触发区"),
            ("病历1", "病历1"),
            ("病历3", "病历3"),
            ("电池1", "电池1"),
            ("电池2", "电池2"),
            ("躲藏1", "躲藏帘子1"),
            ("躲藏2", "躲藏帘子2"),
            ("病历数", "病历数变量"),
            ("电池数", "电池数变量"),
            ("躲藏中", "躲藏中变量"),
            ("手电电量", "手电电量变量"),
            ("手电电量 = 60.0", "进关电量 60"),
            ("电池数 + 1.0", "电池数 +1"),
            ("(手电电量 + 40.0)", "电池补 40 点电"),
            ("(病历数 >= 3)", "病历集齐才开侧门"),
            ("医务室躲藏机位", "躲藏机位"),
            ("节点_医务室玩家.visible = false", "躲进去隐藏玩家"),
            ("节点_医务室玩家.visible = true", "出来显示玩家"),
            ("手电开 and (手电电量 > 0)", "没电就灭灯"),
            ("医务室手电筒", "本关自己的手电"),
            # 低电闪烁(第一百八十六轮,_torch_chain 工厂):电量 <= 20
            # 进 FAULT 闪、回升自动停 —— 静态断言生成物两侧函数都在。
            ("_gk_light_flicker_start", "低电闪烁的登记(工厂)"),
            ("_gk_light_flicker_stop", "低电闪烁的停止"),
            ("if (手电电量 <= 20.0):", "低电阈值分支"),
            ("ACTION_KEY_E", "E 交互 / 进出躲藏"),
            ("ACTION_KEY_F", "F 手电"),
            ("ACTION_KEY_J", "J 日志"),
            ("_gk_save_data[\"病历数\"]", "病历数写进存档"),
            ("_gk_save_data[\"电池数\"]", "电池数写进存档"),
            ("回到安全屋", "被抓后的『回到安全屋』按钮"),
            ("_gk_apply_pause(false", "回安全屋 / 回标题前先解冻"),
            ("_gk_unlock_camera(", "倒地之后把相机交还弹簧臂"),
            ("日志面板", "医务室也有笔记日志面板"),
        ),
    }
    for name, needles in checks.items():
        path = os.path.join(OUT, name)
        if not os.path.isfile(path):
            problems.append(f"没找到生成的脚本 {name}")
            continue
        with open(path, "r", encoding="utf-8") as handle:
            code = handle.read()
        for needle, why in needles:
            if needle not in code:
                problems.append(f"{name} 里没有{why}: 找不到 {needle!r}")

    # 蹲走脚本(『脚本_加载』挂的文本块):文件名带哈希,按内容找。
    custom_dir = os.path.join(OUT, "scripts", "custom")
    crouch_ok = False
    crouch_code = ""
    polish_ok = False
    if os.path.isdir(custom_dir):
        for entry in os.listdir(custom_dir):
            if not entry.endswith(".gd"):
                continue
            with open(os.path.join(custom_dir, entry), "r",
                      encoding="utf-8") as handle:
                code = handle.read()
            # 蹲走脚本:必须走**输入动作**(physical_keycode 匹配),
            # 用 is_key_pressed(KEY_CTRL) 的话合成事件量不到(实机踩过)。
            if ("process_physics_priority" in code
                    and "is_action_pressed" in code
                    and "CapsuleShape3D" in code):
                crouch_ok = True
                crouch_code = code
            if "对话框" in code and "选择框" in code:
                polish_ok = True
    if not crouch_ok:
        problems.append("scripts/custom/ 里没有蹲走脚本"
                        "(process_physics_priority + is_action_pressed + 胶囊)")
    else:
        # ⚠ 第 3/4 章加进来时名册漏了两个人 —— 蹲走的**相机/胶囊那一半**
        #   静默失效,只有速度那一半还动,门禁当时只验速度所以假绿。
        #   这里把"五幕的玩家名都得在名册里"钉死;以后加关不改名册就红。
        roster = ("走廊玩家", "教室玩家", "楼梯间玩家", "音乐教室玩家",
                  "医务室玩家")
        missing = [one for one in roster if one not in crouch_code]
        if missing:
            problems.append("蹲走脚本的 PLAYER_NAMES 少了: "
                            + "、".join(missing)
                            + "(这几关的蹲走镜头/胶囊会静默失效)")
        if 'body.get("移动速度")' not in crouch_code:
            problems.append("蹲走脚本没有『按变量认人』的主路径"
                            "(只剩白名单,加关就会漏)")
    if not polish_ok:
        problems.append("scripts/custom/ 里没有界面修补脚本"
                        "(字幕板 / 选择框底色)")

    room = os.path.join(OUT, "scripts", "game_logic_教室.gd")
    if os.path.isfile(room):
        with open(room, "r", encoding="utf-8") as handle:
            code = handle.read()
        if not re.search(r"纸条数\)? >= 3", code):
            problems.append("教室门口没有『纸条数 >= 3』的门禁")
    else:
        problems.append("没找到教室关卡脚本(门禁没法查)")

    # ── 分工的反向断言:角色图里不该出现 UI / 存档 / 心跳 ────────────
    # 以前这些东西全挤在 `game_logic_教室_char.gd` 里,玩家一没整关就散。
    for scene in ("教室", "走廊", "音乐教室", "医务室", "楼梯间"):
        path = os.path.join(OUT, "scripts", f"game_logic_{scene}_char.gd")
        if not os.path.isfile(path):
            problems.append(f"没找到角色脚本 game_logic_{scene}_char.gd")
            continue
        with open(path, "r", encoding="utf-8") as handle:
            code = handle.read()
        for needle, why in (
                ("_gk_write_save", "存档"),
                ("Label.new()", "关卡 HUD"),
                ("_gk_heart_tension", "心跳氛围"),
                ("AudioServer.get_bus_index", "关卡音频总线"),
                ("change_scene_to_file", "关卡切换")):
            if needle in code:
                problems.append(
                    f"game_logic_{scene}_char.gd 里不该有{why}"
                    f"(角色图只放输入/移动/视角): 找到了 {needle!r}")
        if "Input.get_action_strength" not in code:
            problems.append(f"game_logic_{scene}_char.gd 里没有移动输入")


def _scene_checks(problems):
    checks = {
        "scenes/标题.tscn": (
            # 标题逻辑挂场景根(关卡图),不再有那块假管理器
            ('path="res://scripts/game_logic_标题.gd"', "标题关卡脚本"),
            # 相机在 glb 里(glTF 带相机),.tscn 只记"当前机位叫什么"
            ('camera_name = "标题机位"', "标题机位"),
        ),
        "scenes/教室.tscn": (
            ('name="纸条1"', "纸条1"),
            ('name="纸条3"', "纸条3"),
            ('name="去走廊" type="Area3D"', "去走廊触发区"),
            ('name="月光" type="DirectionalLight3D"', "月光(平行光)"),
            ('name="台灯" type="OmniLight3D"', "台灯(点光)"),
            ('name="教室玩家" type="CharacterBody3D"', "教室角色"),
            # 双脚本:关卡图挂场景根,角色图挂角色(第一百五十八轮分工)
            ('path="res://scripts/game_logic_教室.gd"', "教室关卡脚本"),
            ('path="res://scripts/game_logic_教室_char.gd"', "教室角色脚本"),
            ('camera_name = "侧视机位"', "统一的侧视机位"),
            # M0.5 ②:从 demo_story 移植过来的带窗洞的墙(装饰件在 glb 里,
            # 见 _glb_checks)
            ('name="教室北墙下墙带"', "教室北墙(带窗洞的墙)"),
            ('name="教室北墙墙柱1"', "教室墙的窗间墙柱"),
        ),
        "scenes/走廊.tscn": (
            ('name="怪物" type="CharacterBody3D"', "怪物"),
            ('name="巡逻点甲"', "巡逻点甲"),
            ('name="惊吓区" type="Area3D"', "惊吓区"),
            # M1a:出口改叫『去音乐教室』(通向第 3 章)
            ('name="去音乐教室" type="Area3D"', "去音乐教室触发区"),
            ('name="手电筒" type="SpotLight3D"', "手电筒(聚光)"),
            # M0.5 ②:吊灯 / 壁灯 / 两面带窗洞的墙(装饰件在 glb 里,
            # 见 _glb_checks)
            ('name="走廊吊灯1" type="SpotLight3D"', "走廊吊灯(70 度聚光)"),
            ('name="走廊吊灯1上照" type="SpotLight3D"', "吊灯上照(不照过曝)"),
            ('name="走廊壁灯1" type="SpotLight3D"', "走廊壁灯"),
            ('name="走廊北墙下墙带"', "走廊北墙(带窗洞的墙)"),
            ('name="走廊南墙下墙带"', "走廊南墙(带窗洞的墙)"),
            ('path="res://scripts/game_logic_走廊.gd"', "走廊关卡脚本"),
            ('path="res://scripts/game_logic_走廊_char.gd"', "走廊角色脚本"),
            ('camera_name = "侧视机位"', "统一的侧视机位"),
        ),
        "scenes/音乐教室.tscn": (
            ('name="乐谱1" type="Area3D"', "乐谱1(可交互)"),
            ('name="乐谱2" type="Area3D"', "乐谱2(可交互)"),
            ('name="乐谱3" type="Area3D"', "乐谱3(可交互)"),
            ('name="录音机" type="Area3D"', "录音机(可交互)"),
            ('name="磁带" type="Area3D"', "磁带(可交互)"),
            ('name="舞台" type="Area3D"', "舞台(诱饵噪音位置)"),
            ('name="安全屋区域" type="Area3D"', "本关安全屋"),
            ('name="去医务室" type="Area3D"', "侧门 -> 医务室"),
            ('name="舞台灯" type="SpotLight3D"', "舞台灯(亮着 = 磁带拿不了)"),
            ('name="警报灯" type="OmniLight3D"', "警报灯(输错时闪)"),
            ('name="音乐教室怪物" type="CharacterBody3D"', "怪物"),
            ('name="音乐教室玩家" type="CharacterBody3D"', "音乐教室角色"),
            ('path="res://scripts/game_logic_音乐教室.gd"', "音乐教室关卡脚本"),
            ('path="res://scripts/game_logic_音乐教室_char.gd"',
             "音乐教室角色脚本"),
            ('camera_name = "侧视机位"', "统一的侧视机位"),
        ),
        "scenes/医务室.tscn": (
            ('name="病历1" type="Area3D"', "病历1(可交互)"),
            ('name="病历2" type="Area3D"', "病历2(可交互)"),
            ('name="病历3" type="Area3D"', "病历3(可交互)"),
            ('name="电池1" type="Area3D"', "电池1(可交互)"),
            ('name="电池2" type="Area3D"', "电池2(可交互)"),
            ('name="躲藏1" type="Area3D"', "躲藏帘子1"),
            ('name="躲藏2" type="Area3D"', "躲藏帘子2"),
            ('name="去楼梯间" type="Area3D"', "侧门 -> 楼梯间"),
            ('name="医务室安全屋区域" type="Area3D"', "本关安全屋"),
            ('name="医务室怪物" type="CharacterBody3D"', "医务室的怪"),
            ('name="医务室玩家" type="CharacterBody3D"', "医务室角色"),
            ('name="医务室手电筒" type="SpotLight3D"', "医务室手电(聚光)"),
            ('path="res://scripts/game_logic_医务室.gd"', "医务室关卡脚本"),
            ('path="res://scripts/game_logic_医务室_char.gd"',
             "医务室角色脚本"),
            ('camera_name = "侧视机位"', "统一的侧视机位"),
        ),
        "scenes/楼梯间.tscn": (
            # 第 161 轮:第 5 章从"进门厅走 5 米"扩成**三折楼梯 + 天台**
            ('name="楼梯间一层" type="Area3D"', "一层区域(楼层进度)"),
            ('name="楼梯间二层" type="Area3D"', "二层区域"),
            ('name="楼梯间三层" type="Area3D"', "三层区域"),
            ('name="楼梯间天台" type="Area3D"', "天台触发区(结局)"),
            ('name="楼梯间安全屋区域" type="Area3D"', "本关安全屋"),
            ('name="楼梯间追击者" type="CharacterBody3D"', "追兵(断电后启动)"),
            ('name="楼梯间应急灯1" type="OmniLight3D"', "应急灯(断电时关)"),
            ('name="楼梯间应急灯3" type="OmniLight3D"', "顶层应急灯"),
            ('name="楼梯间天台红灯" type="OmniLight3D"', "天台红灯(断电后唯一光源)"),
            ('name="楼梯间玩家" type="CharacterBody3D"', "楼梯间角色"),
            ('path="res://scripts/game_logic_楼梯间.gd"', "楼梯间关卡脚本"),
            ('path="res://scripts/game_logic_楼梯间_char.gd"', "楼梯间角色脚本"),
            ('camera_name = "侧视机位"', "统一的侧视机位"),
        ),
    }
    for name, needles in checks.items():
        path = os.path.join(OUT, name)
        if not os.path.isfile(path):
            problems.append(f"没找到场景 {name}")
            continue
        with open(path, "r", encoding="utf-8") as handle:
            scene = handle.read()
        for needle, why in needles:
            if needle not in scene:
                problems.append(f"{name} 里没有{why}: 找不到 {needle!r}")


def _glb_node_names(path):
    """读 .glb 的 JSON 块,返回里面所有节点名(读不出来就返回空表)。

    ⚠ 不能"读到文件尾再 split 掉二进制块":JSON 块后面紧跟 BIN 块,
      混在一起 json.loads 直接失败(第一次就是这么写的,返回空表 =>
      "找不到节点"的假红)。按 chunk 头里的长度**精确读**。
    """
    import json
    import struct
    try:
        with open(path, "rb") as handle:
            head = handle.read(12)
            if len(head) < 12 or head[:4] != b"glTF":
                return []
            handle.seek(12)
            size, kind = struct.unpack("<II", handle.read(8))
            if kind != 0x4E4F534A:            # "JSON"
                return []
            body = handle.read(size)
        doc = json.loads(body.decode("utf-8"))
    except Exception:                                     # noqa: BLE001
        return []
    return [str(node.get("name", "")) for node in doc.get("nodes", [])]


def _glb_checks(problems):
    """装饰件(窗框 / 踢脚线 / 腰线 / 灯罩)在 .glb 里,不在 .tscn 里。

    ⚠ 它们是 decor() 物体(碰撞 = 『无』),导出时**不生成碰撞节点**,
      所以一张 .tscn 里根本看不到它们 —— 只在 glb 的节点表里。想证明
      "demo_story 那套美术真的移植过来了",只能去 glb 里按名字找
      (glTF 把节点名当明文存,直接搜字节即可)。
    """
    checks = {
        # ── 第 162 轮:M3 第一刀,角色/怪物是**拼出来的人形** ─────────────
        # 零件是角色物体的子级 → 玩家那几个在**角色自己的 glb**里
        # (`<场景>_<玩家>.glb`),怪物那几个在世界 glb 里。
        "art/楼梯间_楼梯间玩家.glb": (
            ("楼梯间玩家头", "玩家的头(零件拼的人形)"),
            ("楼梯间玩家发", "玩家的头发"),
            ("楼梯间玩家上衣", "玩家的上衣"),
            ("楼梯间玩家裙", "玩家的裙子"),
            ("楼梯间玩家左鞋", "玩家的鞋"),
        ),
        "art/楼梯间.glb": (
            ("楼梯间追击者袍", "追击者的长袍"),
            ("楼梯间追击者脸", "追击者的白脸"),
            ("楼梯间追击者左臂", "追击者的长手"),
        ),
        "art/教室.glb": (
            ("教室窗1窗框上", "教室的窗框"),
            ("教室窗1窗棂竖", "教室窗的竖梃"),
            ("教室北墙踢脚线", "教室墙脚踢脚线"),
            ("台灯灯罩", "台灯的罩子(不然就是一坨白斑)"),
            ("台灯灯芯", "台灯的自发光灯芯"),
        ),
        "art/医务室.glb": (
            ("医务室窗1窗框上", "医务室的窗框"),
            ("医务室北墙踢脚线", "医务室踢脚线"),
            ("医务室吊灯1灯罩", "吊灯灯罩"),
            ("医务室病床1", "病床"),
            ("医务室帘子1", "病床帘(躲藏点)"),
            ("医务室药柜", "药柜"),
            ("医务室文件架1", "病历架"),
            ("医务室药架1", "电池架"),
        ),
        "art/音乐教室.glb": (
            ("音乐室窗1窗框上", "音乐教室的窗框"),
            ("音乐室北墙踢脚线", "音乐教室踢脚线"),
            ("音乐室吊灯1灯罩", "吊灯灯罩"),
            ("音乐室壁灯1灯芯", "壁灯的发光片"),
            ("音乐教室钢琴盖", "钢琴盖"),
            ("音乐教室谱架1立杆", "谱架"),
            ("磁带本体", "磁带(看得见的那一小盒)"),
        ),
        "art/走廊.glb": (
            ("走廊南窗1窗框上", "走廊南墙的窗框"),
            ("走廊北墙踢脚线", "走廊踢脚线"),
            ("走廊北墙腰线", "走廊腰线"),
            ("走廊吊灯1灯罩", "吊灯的圆台灯罩"),
            ("走廊吊灯1灯泡", "吊灯的自发光灯泡"),
            ("走廊壁灯1灯罩", "壁灯的深色金属罩"),
            ("走廊壁灯1灯芯", "壁灯的发光片"),
            ("走廊横梁1", "吊灯的挂点(横梁)"),
        ),
    }
    for name, needles in checks.items():
        path = os.path.join(OUT, name)
        if not os.path.isfile(path):
            problems.append(f"没找到导出的 {name}")
            continue
        names = _glb_node_names(path)
        with open(path, "rb") as handle:
            blob = handle.read()
        for needle, why in needles:
            # ⚠ 先按**节点名**查:Blender 导角色 glb 时会把中文写成
            #   `\u697c\u68af...`(`export_subtree_glb` 的 JSON 选项和
            #   整场景那次不一样),字节搜直接搜不到 —— 第 162 轮踩到。
            #   节点名里没有的再退回字节搜(材质名 / 路径这类也能兜住)。
            if needle in names:
                continue
            if needle.encode("utf-8") not in blob:
                problems.append(f"{name} 里没有{why}: 找不到 {needle!r}")


def _main_scene_check(problems):
    # ⚠ 查**源工程**:探针那几趟会把副本里的 main_scene 改指到 probe_*.tscn
    path = os.path.join(SRC, "project.godot")
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    if 'run/main_scene="res://scenes/标题.tscn"' not in text:
        problems.append("主场景不是标题页(按 F5 应该先看到标题 + 开始按钮)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--godot", default=GODOT_DEFAULT)
    args = parser.parse_args()
    if not os.path.isdir(SRC):
        print(f"找不到示例工程 {SRC} —— 先跑 make_horror_demo.py")
        return 1

    problems = []

    # ── 第零幕 标题页 ───────────────────────────────────────────────
    title = _run_probe(args.godot, "probe_title", PROBE_TITLE,
                       "HorrorTitleProbe", 300)
    _errors(title, problems)
    title_ui = re.search(
        r"HORROR_TITLE_UI labels=(\d+) buttons=(\d+) cam=(\w+)"
        r" start=(\w+) quit=(\w+)", title)
    if not title_ui:
        problems.append("标题页 UI 没跑出来: "
                        + _grab(title, r"HORROR_TITLE_UI[^\n]*"))
    else:
        if int(title_ui.group(1)) < 2:
            problems.append("标题页没出现文字标签: " + title_ui.group(0))
        if int(title_ui.group(2)) < 2:
            problems.append("标题页没出现『开始游戏』+『退出游戏』两个按钮: "
                            + title_ui.group(0))
        if title_ui.group(3) != "true":
            problems.append("标题页没有相机: " + title_ui.group(0))
        if title_ui.group(4) != "true" or title_ui.group(5) != "true":
            problems.append("标题页按钮文字不对: " + title_ui.group(0))
    if "HORROR_TITLE_DONE" not in title:
        problems.append("标题页探针没跑完")

    # ── 第一关 教室 ─────────────────────────────────────────────────
    room = _run_probe(args.godot, "probe_room", PROBE_ROOM, "HorrorRoomProbe", 1600)
    _errors(room, problems)
    if not re.search(r"HORROR_ROOM_START player=true spots=4 cam=true", room):
        problems.append("教室开场不对(玩家 / 4 个触发区 / 特写机位没找齐): "
                        + _grab(room, r"HORROR_ROOM_START[^\n]*"))
    flew = re.search(r"HORROR_ROOM_OPENING flew=([\d.]+) picked=(\w+)", room)
    # 阈值从 2.0 收到 1.0(M0.5):开场落点从"玩家正面 5.1 米"退到 9.6 米
    # (盒子占屏 50% -> 33%),整条轨道就短了 —— 实测位移 1.8 米。这条断言
    # 要抓的是"相机根本没动"(0),不是"运镜够不够长"。
    if not flew or float(flew.group(1)) < 1.0:
        problems.append("开场曲线运镜没在飞(相机没动): "
                        + _grab(room, r"HORROR_ROOM_OPENING[^\n]*"))
    elif flew.group(2) != "true":
        problems.append("纸条拾取后没有销毁(画面里还杵着): " + flew.group(0))
    # 只拿 2 张就走门口:门禁必须挡住,探针还活着(切场景会把探针一起释放)
    gate = re.search(r"HORROR_ROOM_GATE notes=([\d.]+) alive=(\w+)"
                     r" hint=(\w+) removed=(\w+)", room)
    if not gate:
        problems.append("教室门禁没跑出来(可能没挡住,场景被切走了): "
                        + _grab(room, r"HORROR_ROOM_GATE[^\n]*"))
    else:
        if float(gate.group(1)) != 2.0:
            problems.append("教室门禁前纸条数不是 2: " + gate.group(0))
        if gate.group(2) != "true":
            problems.append("纸条没齐 3 张就切场景了(门禁没生效): "
                            + gate.group(0))
        if gate.group(3) != "true":
            problems.append("纸条不够时没有提示: " + gate.group(0))
        if gate.group(4) != "true":
            problems.append("纸条拾取后没有销毁: " + gate.group(0))
    notes = re.search(r"HORROR_ROOM_NOTES count=([\d.]+) removed=(\d+)"
                      r" hud=(\w+)", room)
    if not notes or float(notes.group(1)) < 2.5:
        problems.append("教室拾 3 张纸条后『纸条数』不是 3: "
                        + _grab(room, r"HORROR_ROOM_NOTES[^\n]*"))
    elif int(notes.group(2)) != 3:
        problems.append("3 张纸条没有全部销毁: " + notes.group(0))
    elif notes.group(3) != "true":
        problems.append("纸条 HUD 没显示 3/3: " + notes.group(0))
    # 开场运镜**必须切回第三人称**:不切回来视角钉在特写上,人还能走但看不
    # 见自己 = "卡住"(用户实测报过)。
    back = re.search(r"HORROR_ROOM_CAM cur=(\S+) dist=([\d.]+)", room)
    if not back:
        problems.append("教室没跑出『切回第三人称』的结论行: "
                        + _grab(room, r"HORROR_ROOM_CAM[^\n]*"))
    else:
        if back.group(1) != "侧视机位":
            problems.append("开场运镜之后当前相机不是第三人称机位(卡住了): "
                            + back.group(0))
        if float(back.group(2)) > 8.0:
            problems.append("第三人称机位没有跟着角色(视角还钉在特写上): "
                            + back.group(0))
    # ── M0:交互目标 / [E] 拾取 / 笔记日志(J、Esc)────────────────────
    room_start = re.search(r"HORROR_ROOM_START[^\n]*logopen=(\w+)", room)
    if room_start is None or room_start.group(1) != "false":
        problems.append("开局『日志打开』不是 false: "
                        + _grab(room, r"HORROR_ROOM_START[^\n]*"))
    opened = re.search(r"HORROR_ROOM_OPENING flew=[\d.]+ picked=(\w+)"
                       r" target=(\S*)", room)
    if opened is None:
        problems.append("教室拾取那段没跑出结论行: "
                        + _grab(room, r"HORROR_ROOM_OPENING[^\n]*"))
    elif opened.group(2) != "":
        # 拿完之后交互目标必须清空(纸条已经没了,不该还挂着"纸条1");
        # 而"目标真的被设过"由下面 GATE 那行的 target=去走廊 证明。
        problems.append("拾取之后『交互目标』没有清空: " + opened.group(0))
    log_open = re.search(r"HORROR_ROOM_LOG_OPEN open=(\w+) panel=(\w+)", room)
    if not log_open:
        problems.append("按 J 没跑出日志结论行: "
                        + _grab(room, r"HORROR_ROOM_LOG[^\n]*"))
    else:
        if log_open.group(1) != "true" or log_open.group(2) != "true":
            problems.append("按 J 没有打开笔记日志面板(『日志打开』/ 面板可见): "
                            + log_open.group(0))
    log_close = re.search(r"HORROR_ROOM_LOG_CLOSE open=(\w+) panel=(\w+)",
                          room)
    if not log_close:
        problems.append("再按一次 J 没跑出日志结论行")
    elif log_close.group(1) != "false" or log_close.group(2) != "false":
        problems.append("再按一次 J 没有关掉日志: " + log_close.group(0))
    log_esc = re.search(r"HORROR_ROOM_LOG_ESC open=(\w+) panel=(\w+)", room)
    if not log_esc:
        problems.append("Esc 关日志没跑出结论行")
    elif log_esc.group(1) != "false" or log_esc.group(2) != "false":
        problems.append("Esc 没有关掉日志: " + log_esc.group(0))
    if "HORROR_ROOM_DONE" not in room:
        problems.append("教室探针没跑完")

    # ── 第二关 走廊 ─────────────────────────────────────────────────
    # 走廊这条链要跑到"被抓 -> 倒地 -> 冻住"(约 7.2 秒);无头模式帧率比
    # 现实时间快,按帧数截断会提前退出(实测 900 帧只跑到 6.3 秒)。
    corridor = _run_probe(args.godot, "probe_corridor", PROBE_CORRIDOR,
                          "HorrorCorridorProbe", 3600)
    _errors(corridor, problems)
    start = re.search(r"HORROR_CORRIDOR_START state=巡逻 has_enemy=true"
                      r" spots=(\d+) battery=([\d.]+) hudtext=(\w+)",
                      corridor)
    if not start:
        problems.append("走廊开场不对(怪物 / 触发区 / 手电没找齐): "
                        + _grab(corridor, r"HORROR_CORRIDOR_START[^\n]*"))
    else:
        if int(start.group(1)) != 7:
            problems.append("走廊触发区不是 7 个(惊吓区/去音乐教室/安全屋"
                            "/保险丝×3/隐藏笔记): " + start.group(0))
        if float(start.group(2)) <= 0.0:
            problems.append("手电电量开局不是满的: " + start.group(0))
        if start.group(3) != "true":
            problems.append("HUD 上没有『手电电量:』: " + start.group(0))
    moved = re.search(r"HORROR_PATROL state=巡逻 moved=([\d.]+)", corridor)
    if not moved or float(moved.group(1)) < 0.5:
        problems.append("怪物没有在巡逻: "
                        + _grab(corridor, r"HORROR_PATROL[^\n]*"))
    far = re.search(r"HORROR_TENSION_FAR tension=([\d.]+)", corridor)
    if not far or float(far.group(1)) > 0.2:
        problems.append("离怪物很远时紧张度没压下来: "
                        + _grab(corridor, r"HORROR_TENSION_FAR[^\n]*"))
    near = re.search(r"HORROR_TENSION_NEAR tension=([\d.]+) heart=(\w+)",
                     corridor)
    # 心跳内部做平滑(1.5 秒),凑近后 0.3 秒只涨到"明显高于远处"的
    # 0.2 出头 —— 断言不必要求它已经到 1,只要近处 > 远处一个量级即可。
    if not near or float(near.group(1)) < 0.15:
        problems.append("凑近怪物后紧张度没升起来: "
                        + _grab(corridor, r"HORROR_TENSION_NEAR[^\n]*"))
    elif far and float(near.group(1)) <= float(far.group(1)) + 0.08:
        problems.append("凑近后紧张度没有明显高于远处: "
                        + near.group(0) + " / " + far.group(0))
    elif near.group(2) != "true":
        problems.append("心跳播放器没建起来: " + near.group(0))
    scare = re.search(r"HORROR_SCARE shake=([\-\d.]+) bus=([\-\d.]+)", corridor)
    if not scare:
        problems.append("惊吓那段没跑出来: "
                        + _grab(corridor, r"HORROR_SCARE[^\n]*"))
    else:
        if float(scare.group(2)) > -10.0:
            problems.append("『音效_总线音量』没有压低 Master: " + scare.group(0))
    restore = re.search(r"HORROR_BUS_RESTORE bus=([\-\d.]+)", corridor)
    if not restore or float(restore.group(1)) < -3.0:
        problems.append("受惊后 Master 总线没有恢复(旧版压低就不还了): "
                        + _grab(corridor, r"HORROR_BUS_RESTORE[^\n]*"))
    fail = re.search(r"HORROR_FAIL paused=(\w+) fall=(\w+)"
                     r" safehouse=(\w+) back=(\w+)", corridor)
    if not fail:
        problems.append("被抓失败那段没跑出来: "
                        + _grab(corridor, r"HORROR_FAIL[^\n]*"))
    else:
        if fail.group(1) != "true":
            problems.append("被怪物抓住后没有冻住世界: " + fail.group(0))
        if fail.group(2) != "true":
            problems.append("被抓时没有镜头倒地: " + fail.group(0))
        if fail.group(3) != "true" or fail.group(4) != "true":
            problems.append("被抓后没有『回到安全屋 / 回到标题』按钮: "
                            + fail.group(0))

    # ── M0:蹲走 / 跑动噪音 / 被抓回安全屋 ───────────────────────────
    run = re.search(r"HORROR_RUN speed=([\d.]+) loud=([\d.]+) type=(\S*)",
                    corridor)
    if not run:
        problems.append("跑动没跑出结论行: "
                        + _grab(corridor, r"HORROR_RUN[^\n]*"))
    else:
        if abs(float(run.group(1)) - 10.8) > 0.2:
            problems.append("按住 Shift 速度不是 1.8 倍(期望 10.8): "
                            + run.group(0))
        if float(run.group(2)) < 5.5:
            problems.append("跑动没有发出半径约 6 米的噪音: " + run.group(0))
        if run.group(3) != "脚步":
            problems.append("跑动噪音类型不是『脚步』: " + run.group(0))
    crouch = re.search(r"HORROR_CROUCH speed=([\d.]+)", corridor)
    if not crouch:
        problems.append("蹲走没跑出结论行: "
                        + _grab(corridor, r"HORROR_CROUCH[^\n]*"))
    elif abs(float(crouch.group(1)) - 3.0) > 0.2:
        problems.append("按住 LeftCtrl 速度不是 0.5 倍(期望 3.0): "
                        + crouch.group(0))

    # ── 第 168 轮:保险丝解谜(收集 → 渐进点亮 → 出口放行)────────────
    gate = re.search(r"HORROR_FUSE_GATE blocked=(\w+) scene=(\S+)"
                     r" count=([\d.]+)", corridor)
    if not gate:
        problems.append("空手走传送板没跑出结论行: "
                        + _grab(corridor, r"HORROR_FUSE_GATE[^\n]*"))
    else:
        if gate.group(1) != "true":
            problems.append("0 个保险丝就切到了音乐教室(出口没锁): "
                            + gate.group(0))
        if gate.group(3) != "0.0":
            problems.append("开局保险丝数不是 0(存档/变量串了): "
                            + gate.group(0))
    fuse1 = re.search(r"HORROR_FUSE1 count=([\d.]+) light2=(\w+)", corridor)
    if not fuse1:
        problems.append("保险丝1没跑出结论行: "
                        + _grab(corridor, r"HORROR_FUSE1[^\n]*"))
    else:
        if fuse1.group(1) != "1.0":
            problems.append("捡到保险丝1计数没变(判定区/计数链断了): "
                            + fuse1.group(0))
        if fuse1.group(2) != "true":
            problems.append("保险丝1没有点亮吊灯2(『灯光_设置』没生效): "
                            + fuse1.group(0))
    fuse2 = re.search(r"HORROR_FUSE2 count=([\d.]+) light3=(\w+)", corridor)
    if not fuse2:
        problems.append("保险丝2没跑出结论行: "
                        + _grab(corridor, r"HORROR_FUSE2[^\n]*"))
    else:
        if fuse2.group(1) != "2.0":
            problems.append("保险丝2计数不是 2(防重/累加坏了): "
                            + fuse2.group(0))
        if fuse2.group(2) != "true":
            problems.append("保险丝2没有点亮吊灯3: " + fuse2.group(0))
    fuse3 = re.search(r"HORROR_FUSE3 count=([\d.]+)", corridor)
    if not fuse3:
        problems.append("保险丝3没跑出结论行: "
                        + _grab(corridor, r"HORROR_FUSE3[^\n]*"))
    elif fuse3.group(1) != "3.0":
        problems.append("保险丝3计数不是 3(没集齐): " + fuse3.group(0))
    fuse_pass = re.search(r"HORROR_FUSE_PASS scene=(\S+)", corridor)
    if not fuse_pass:
        problems.append("集齐保险丝后走传送板没跑出结论行: "
                        + _grab(corridor, r"HORROR_FUSE_PASS[^\n]*"))
    elif fuse_pass.group(1) != "res://scenes/音乐教室.tscn":
        problems.append("集齐 3 个保险丝也没放行去音乐教室(放行链断了): "
                        + fuse_pass.group(0))
    # ── 第 169 轮:隐藏笔记(可选内容,不进真结局计数)─────────────────
    note_hall = re.search(r"HORROR_NOTE_HIDDEN hidden=([\d.]+)", corridor)
    if not note_hall:
        problems.append("走廊隐藏笔记没跑出结论行: "
                        + _grab(corridor, r"HORROR_NOTE_HIDDEN[^\n]*"))
    elif note_hall.group(1) != "1.0":
        problems.append("走廊隐藏笔记计数不是 1(判定区/计数链断了): "
                        + note_hall.group(0))
    safe = re.search(r"HORROR_SAFEROOM paused=(\w+) alive=(\w+)"
                     r" dist=([\d.]+) checkpoint=(\S*) notes=([\d.]+)",
                     corridor)
    if not safe:
        problems.append("点『回到安全屋』之后没跑出结论行(可能又重载了整关): "
                        + _grab(corridor, r"HORROR_SAFEROOM[^\n]*"))
    else:
        if safe.group(1) != "false":
            problems.append("回安全屋之后世界还冻着: " + safe.group(0))
        if safe.group(2) != "true":
            problems.append("点『回到安全屋』把整关重载了(场景被换掉): "
                            + safe.group(0))
        if float(safe.group(3)) > 2.5:
            problems.append("没有被传送回安全屋: " + safe.group(0))
        if safe.group(4) != "走廊_安全屋":
            problems.append("检查点没记成『走廊_安全屋』: " + safe.group(0))
        if abs(float(safe.group(5)) - 2.0) > 0.01:
            problems.append("被抓之后已拿纸条数没保留(期望 2): "
                            + safe.group(0))
    # ── M0.5 收尾:回安全屋之后**机位必须回来** ──────────────────────
    # 判据不是"看着不像倒地",而是三条可量的数:
    #   cam_roll    = 相机绕视线轴的侧翻角(倒地时 +72° = 1.256 弧度)-> 要 ≈ 0
    #   cam_upright = 相机 up 向量朝上的分量(倒地时 ≈ 0.31)-> 要 > 0.9
    #   cam_dist / cam_up = 第三人称弹簧臂该在的位置(身后约 5 米、略高)
    # 旧行为:倒地那一套把相机从弹簧臂手里永久接管,回到安全屋之后这三个数
    # 一直停在倒地值(实机截图 10/10b 就是歪 72° 的画面)。
    cam = re.search(r"HORROR_SAFEROOM[^\n]*cam_roll=([\d.]+) cam_upright=(-?[\d.]+)"
                    r" cam_dist=(-?[\d.]+) cam_up=(-?[\d.]+)", corridor)
    if not cam:
        problems.append("回安全屋之后没量到机位(相机没恢复的路径跑不出来): "
                        + _grab(corridor, r"HORROR_SAFEROOM[^\n]*"))
    else:
        if float(cam.group(1)) > 0.05:
            problems.append("回安全屋之后机位还是倒地的(侧翻角没归零): "
                            + cam.group(0))
        if float(cam.group(2)) < 0.90:
            problems.append("回安全屋之后相机还是歪的(up 向量没立正): "
                            + cam.group(0))
        if not 0.5 <= float(cam.group(3)) <= 8.0:
            problems.append("回安全屋之后相机没跟到玩家身后(距离不对): "
                            + cam.group(0))
        if float(cam.group(4)) < 0.5:
            problems.append("回安全屋之后相机不在玩家上方(第三人称没回来): "
                            + cam.group(0))
    # ── M0.5 ①:旧档(裸 JSON,没有『已读N』)下的日志 ─────────────────
    # 要求:面板照样能开(不崩),三行正文是空的(当"没读过")。
    old_log = re.search(r"HORROR_JOURNAL_OLD open=(\w+) panel=(\w+)"
                        r" paused=(\w+) archive=(\w+)"
                        r" line1=(\[[^\]]*\]) line2=(\[[^\]]*\])"
                        r" line3=(\[[^\]]*\])", corridor)
    if not old_log:
        problems.append("旧存档下的日志没跑出结论行(可能读档崩了): "
                        + _grab(corridor, r"HORROR_JOURNAL_OLD[^\n]*"))
    else:
        if old_log.group(1) != "true" or old_log.group(2) != "true":
            problems.append("旧存档下按 J 打不开日志面板: " + old_log.group(0))
        # 第 171 轮:收集状态行(档案 · 纸条 X/3 · …)必须存在且填了字
        if old_log.group(4) != "true":
            problems.append("日志面板没有收集状态行(档案 · …): "
                            + old_log.group(0))
        for index in (5, 6, 7):
            if old_log.group(index) != "[]":
                break
                problems.append("旧存档里没有『已读N』,日志正文却不是空的(该当"
                                "没读过): " + old_log.group(0))
    if "HORROR_CORRIDOR_DONE" not in corridor:
        problems.append("走廊探针没跑完")

    # ── 第 3 章 音乐教室(M1a):整条谜题循环 ─────────────────────────
    # 一趟跑完:怪物开局就在本关巡逻 -> 读 3 张乐谱 -> 被抓一次(回安全屋,
    # 乐谱数保留)-> 录音机按错两次(警报 + 它盯上你)-> 按对(练习曲 +
    # 舞台灯灭 + 诱饵把怪物引向舞台)-> 侧门锁着 -> 拿磁带 -> 侧门放行。
    music = _run_probe(args.godot, "probe_music", PROBE_MUSIC,
                       "HorrorMusicProbe", 2600)
    _errors(music, problems)
    m_start = re.search(
        r"HORROR_MUSIC_START player=true enemy=true spots=(\d+)"
        r" state=(巡逻|追击|去查看)"
        # ⚠ Godot 打印 float 带小数点:0.0 / 1.0(不是 0 / 1)
        r" sheets=0\.0 miss=0\.0 tape=0\.0 playing=false input=\[\]"
        r" checkpoint=音乐教室_安全屋 logopen=false stagelight=true"
        r" stale=false guide1=true", music)
    if not m_start:
        raw_start = _grab(music, r"HORROR_MUSIC_START[^\n]*")
        if "state=<null>" in raw_start or "state=<no-player>" in raw_start:
            problems.append("音乐教室开局没有怪物 / 怪物 AI 没启用"
                            "(『AI_启用』要接在开局链上,本关才有压力): "
                            + raw_start)
        elif "stale=true" in raw_start:
            problems.append("进关第一帧屏幕上还挂着上一关的字幕"
                            "(出生那一帧不该弹安全屋提示): " + raw_start)
        elif "guide1=false" in raw_start:
            problems.append("开局目标行不是『先找齐 3 张乐谱』: " + raw_start)
        else:
            problems.append("音乐教室开场不对(角色 / 怪物 / 判定区 / 变量归零 /"
                            " 舞台灯默认亮着): " + raw_start)
    elif int(m_start.group(1)) != 9:
        problems.append("音乐教室的交互判定区不是 9 个"
                        "(乐谱1-3 / 录音机 / 磁带 / 舞台 / 安全屋区域 / "
                        "去楼梯间 / 隐藏笔记): " + m_start.group(0))
    sheets_seen = re.findall(r"HORROR_MUSIC_SHEET n=(\d) count=([\d.]+)", music)
    if len(sheets_seen) != 3:
        problems.append("3 张乐谱没有都读到(期望 3 条结论行): "
                        + _grab(music, r"HORROR_MUSIC_SHEET[^\n]*"))
    else:
        for index, (number, count) in enumerate(sheets_seen, start=1):
            if int(number) != index or float(count) != float(index):
                problems.append(f"读第 {index} 张乐谱后『乐谱数』不是 "
                                f"{index}: n={number} count={count}")
    # ── 蹲走段(lead M1b 硬指标):怪站在巡逻近端,玩家按住 Ctrl 蹲过去 ──
    # 判据:① 蹲走速度真的降到 3.0(角色图的速度接线生效);② 乐谱2 读到了;
    #       ③ 整个过程没有被抓(『已失败』false 且失败面板没弹出来)。
    m_crouch = re.search(r"HORROR_MUSIC_CROUCH speed=([\d.]+)", music)
    if not m_crouch:
        problems.append("蹲走段没跑出结论行: "
                        + _grab(music, r"HORROR_MUSIC_CROUCH[^\n]*"))
    elif abs(float(m_crouch.group(1)) - 3.0) > 0.2:
        problems.append("按住 LeftCtrl 蹲走速度不是 0.5 倍(期望 3.0): "
                        + m_crouch.group(0))
    m_sheet2 = re.search(r"HORROR_MUSIC_SHEET n=2 count=([\d.]+)"
                         r" crouch=([\d.]+) failed=(true|false)"
                         r" paused=(true|false)", music)
    if not m_sheet2:
        problems.append("蹲走读乐谱2 那段没跑出结论行: "
                        + _grab(music, r"HORROR_MUSIC_SHEET n=2[^\n]*"))
    else:
        if float(m_sheet2.group(1)) != 2.0:
            problems.append("蹲走读到乐谱2 之后『乐谱数』不是 2: "
                            + m_sheet2.group(0))
        if abs(float(m_sheet2.group(2)) - 3.0) > 0.2:
            problems.append("读乐谱2 时没在蹲(速度不是 3.0): "
                            + m_sheet2.group(0))
        if m_sheet2.group(3) != "false" or m_sheet2.group(4) != "false":
            problems.append("蹲走摸乐谱2 的路线还是被抓了(这一关不能靠看脸): "
                            + m_sheet2.group(0))
    # 蹲走的**另一半**:相机压低 ~0.62(以前只验速度,镜头这半是假绿)
    m_dip = re.search(r"HORROR_MUSIC_CROUCH_DIP cam=(\S+)"
                      r" cam_drop=(-?[\d.]+)", music)
    if not m_dip:
        problems.append("第 3 章的蹲走压镜头没跑出结论行(蹲走脚本没认到 "
                        "音乐教室玩家?): "
                        + _grab(music, r"HORROR_MUSIC_CROUCH_DIP[^\n]*"))
    else:
        if m_dip.group(1) != "侧视机位":
            problems.append("蹲下时当前相机不是侧视机位: " + m_dip.group(0))
        if not 0.40 <= float(m_dip.group(2)) <= 0.90:
            problems.append("第 3 章蹲走时侧视机位没有被压低(期望 ~0.62 米): "
                            + m_dip.group(0))
    m_tip = re.search(r"HORROR_MUSIC_SHEET n=1[^\n]*tip=(true|false)", music)
    if not m_tip or m_tip.group(1) != "true":
        problems.append("靠近乐谱没有出现 [E] 提示: "
                        + _grab(music, r"HORROR_MUSIC_SHEET[^\n]*"))
    m_reveal = re.search(r"HORROR_MUSIC_SHEET n=3[^\n]*reveal=(true|false)"
                         r" tip=(true|false) guide2=(true|false)", music)
    if not m_reveal:
        problems.append("集齐 3 张乐谱那段没跑出结论行: "
                        + _grab(music, r"HORROR_MUSIC_SHEET[^\n]*"))
    else:
        if m_reveal.group(1) != "true":
            problems.append("集齐 3 张乐谱之后没有把四个音的顺序念出来: "
                            + m_reveal.group(0))
        if m_reveal.group(2) != "false":
            problems.append("乐谱都看完了,「[E] 读乐谱」还挂在屏幕中央: "
                            + m_reveal.group(0))
        if m_reveal.group(3) != "true":
            problems.append("集齐乐谱之后目标行没有切到『去录音机』: "
                            + m_reveal.group(0))
    m_fail = re.search(r"HORROR_MUSIC_FAIL paused=(true|false)"
                       r" fall=(true|false) safehouse=(true|false)"
                       r" back=(true|false)", music)
    if not m_fail:
        problems.append("音乐教室被抓那段没跑出来: "
                        + _grab(music, r"HORROR_MUSIC_FAIL[^\n]*"))
    elif "false" in m_fail.groups():
        problems.append("被抓后没有(冻世界 + 镜头倒地 + 两个按钮): "
                        + m_fail.group(0))
    m_home = re.search(r"HORROR_MUSIC_SAFEROOM paused=false alive=true"
                       r" dist=([\d.]+) checkpoint=(\S*) sheets=([\d.]+)"
                       r" save_check=(true|false) save_notes=(true|false)",
                       music)
    if not m_home:
        problems.append("点『回到安全屋』之后没跑出结论行"
                        "(可能又重载了整关): "
                        + _grab(music, r"HORROR_MUSIC_SAFEROOM[^\n]*"))
    else:
        if float(m_home.group(1)) > 2.5:
            problems.append("没有被传送回本关安全屋: " + m_home.group(0))
        if m_home.group(2) != "音乐教室_安全屋":
            problems.append("检查点没记成『音乐教室_安全屋』: "
                            + m_home.group(0))
        if float(m_home.group(3)) != 1.0:
            problems.append("被抓回安全屋之后已读乐谱数没保留(期望 1): "
                            + m_home.group(0))
        if m_home.group(4) != "true":
            problems.append("本关存的还是裸 JSON(没有『防改档』信封): "
                            + m_home.group(0))
        # 旧档兼容:开局写进 user:// 的就是一份只带『纸条数』的**老档**,
        # 本关存盘之后它必须还在(否则楼梯间永远凑不齐 3 张 = 死局)。
        if m_home.group(5) != "true":
            problems.append("本关存盘把老档里的『纸条数』弄丢了: "
                            + m_home.group(0))
    if not re.search(r"HORROR_MUSIC_RECORDER open_input=\[\] digits=0\.0",
                     music):
        problems.append("按 E 打开录音机没有清掉上一次的按键进度: "
                        + _grab(music, r"HORROR_MUSIC_RECORDER[^\n]*"))
    m_wrong = re.search(r"HORROR_MUSIC_WRONG miss=([\d.]+)"
                        r" playing=(true|false) last=\[(\d*)\]"
                        r" alarm=(true|false) stagelight=(true|false)", music)
    if not m_wrong:
        problems.append("按错四个音那段没跑出来: "
                        + _grab(music, r"HORROR_MUSIC_WRONG[^\n]*"))
    else:
        if float(m_wrong.group(1)) != 1.0:
            problems.append("按错一次『录音机错次』不是 1: "
                            + m_wrong.group(0))
        if m_wrong.group(2) != "false":
            problems.append("按错了却把『录音机放音』打开了: "
                            + m_wrong.group(0))
        if m_wrong.group(3) != "1234":
            problems.append("按错那次的『录音机记录』不是 1234: "
                            + m_wrong.group(0))
        if m_wrong.group(4) != "true":
            problems.append("按错没有触发警报(字幕): " + m_wrong.group(0))
        if m_wrong.group(5) != "true":
            problems.append("没解谜舞台灯就灭了(应该还亮着): "
                            + m_wrong.group(0))
    m_alarm = re.search(r"HORROR_MUSIC_ALARM miss=([\d.]+)"
                        r" monster=(true|false) state=(\S*)"
                        r" last=\[(\d*)\]", music)
    if not m_alarm:
        problems.append("错满两次那段没跑出来: "
                        + _grab(music, r"HORROR_MUSIC_ALARM[^\n]*"))
    else:
        if float(m_alarm.group(1)) != 2.0:
            problems.append("错第二次之后『录音机错次』不是 2: "
                            + m_alarm.group(0))
        if m_alarm.group(2) != "true":
            problems.append("错满两次没有「它听见你在哪」这一步: "
                            + m_alarm.group(0))
        if m_alarm.group(3) not in ("巡逻", "追击", "去查看"):
            problems.append("错满两次之后 AI 没在跑(ai_state 不对): "
                            + m_alarm.group(0))
        if m_alarm.group(4) != "1111":
            problems.append("第二次按错的『录音机记录』不是 1111: "
                            + m_alarm.group(0))
    m_ok = re.search(r"HORROR_MUSIC_CORRECT playing=true last=\[3142\]"
                     r" stagelight=false lure=true guide3=true"
                     r" state=(巡逻|追击|去查看) dstage=(-?[\d.]+)", music)
    if not m_ok:
        problems.append("按对 3-1-4-2 之后没有(放音 + 舞台灯灭 + 练习曲 + "
                        "目标行切换 + 怪物在动): "
                        + _grab(music, r"HORROR_MUSIC_CORRECT[^\n]*"))
    elif float(m_ok.group(2)) >= -0.3:
        # 诱饵就在**舞台**发噪音:怪物听见之后必须朝舞台走(距离变小)
        problems.append("『声音诱饵』没把怪物引向舞台(它没动 / 反而走远): "
                        + m_ok.group(0))
    m_locked = re.search(r"HORROR_MUSIC_DOOR_LOCKED alive=true tape=0\.0"
                         r" hint=true", music)
    if not m_locked:
        problems.append("磁带还没到手,侧门却放行了(或者把场景切走了): "
                        + _grab(music, r"HORROR_MUSIC_DOOR_LOCKED[^\n]*"))
    m_tape = re.search(r"HORROR_MUSIC_TAPE tape=([\d.]+) removed=(true|false)"
                       r" count=([\d.]+) hint=(true|false) guide4=(true|false)",
                       music)
    if not m_tape:
        problems.append("拿磁带那段没跑出来: "
                        + _grab(music, r"HORROR_MUSIC_TAPE[^\n]*"))
    else:
        if float(m_tape.group(1)) != 1.0:
            problems.append("拿到磁带后『磁带』不是 1: " + m_tape.group(0))
        if m_tape.group(2) != "true":
            problems.append("磁带拿走了但判定体还在: " + m_tape.group(0))
        if float(m_tape.group(3)) != 3.0:
            problems.append("拿磁带时乐谱数没保持 3: " + m_tape.group(0))
        if m_tape.group(4) != "true":
            problems.append("拿到磁带没有提示: " + m_tape.group(0))
        if m_tape.group(5) != "true":
            problems.append("拿到磁带之后目标行没有切到『推开侧门』: "
                            + m_tape.group(0))
    m_open = re.search(r"HORROR_MUSIC_DOOR_OPEN scene=(\S+)", music)
    if not m_open:
        problems.append("侧门那段没跑出结论行: "
                        + _grab(music, r"HORROR_MUSIC_DOOR_OPEN[^\n]*"))
    elif m_open.group(1) != "医务室":
        problems.append("拿到磁带按 E 之后没有切到医务室(侧门不通): "
                        + m_open.group(0))
    if "找不到玩家" in music:
        problems.append("蹲走脚本在音乐教室没认到玩家(蹲走的相机/胶囊那半失效): "
                        + _grab(music, r".*找不到玩家.*"))
    # 第 169 轮:音乐教室隐藏笔记
    note_music = re.search(r"HORROR_NOTE_MUSIC hidden=([\d.]+)", music)
    if not note_music:
        problems.append("音乐教室隐藏笔记没跑出结论行: "
                        + _grab(music, r"HORROR_NOTE_MUSIC[^\n]*"))
    elif note_music.group(1) != "2.0":
        problems.append("音乐教室隐藏笔记计数不是 2(存档接力断了): "
                        + note_music.group(0))
    if "HORROR_MUSIC_DONE" not in music:
        problems.append("音乐教室探针没跑完")

    # ── 第 4 章 医务室(M1b):手电电量 / 病历 / 电池 / 躲藏 / 侧门 ─────
    # 一趟跑完:病历 0 时侧门锁着 -> 三页病历 -> 两块电池(各 +40)->
    # 躲进帘子(躲藏中 / 相机切走 / 人隐藏 / 怪看不见)-> 出来 -> 被抓回
    # 安全屋(病历数 3 与电量都还在)-> 侧门 -> 楼梯间。
    # quit_after 给到 **3800 帧**:headless 一帧 ≈ 5.1ms(实测),主体
    # 12.8s + 闪烁尾段 4.4s(0.8+2.0+0.8 + 转换帧)≈ 17.3s ≈ 3400 帧,
    # 3200 曾被实测截胡(DONE 打了、FLICKER 没打)。探针自己会 quit,
    # 这个上限只是保险(不够的话 FLICKER 行打不出来,断言会红)。
    med = _run_probe(args.godot, "probe_med", PROBE_MED, "HorrorMedProbe",
                     3800)
    _errors(med, problems)
    # ── 第 176 轮:真实游玩探针(零瞬移,合成输入驱动)────────────────
    realplay = _run_probe(args.godot, "probe_realplay", PROBE_REALPLAY,
                          "HorrorRealplayProbe", 4200)
    _errors(realplay, problems)
    d_start = re.search(
        r"HORROR_MED_START player=true enemy=true spots=(\d+)"
        r" state=(巡逻|追击|去查看)"
        r" sheets=0\.0 batteries=0\.0 hiding=false battery=([\d.]+)"
        r" checkpoint=医务室_安全屋 logopen=false stale=false guide1=true",
        med)
    if not d_start:
        raw_med = _grab(med, r"HORROR_MED_START[^\n]*")
        if "state=<null>" in raw_med or "state=<no-player>" in raw_med:
            problems.append("医务室开局的怪物 AI 没启用(『AI_启用』要接在开局链上): "
                            + raw_med)
        elif "stale=true" in raw_med:
            problems.append("进医务室第一帧还挂着上一关的字幕: " + raw_med)
        else:
            problems.append("医务室开场不对(角色 / 怪物 / 判定区 / 变量归零 / "
                            "目标行): " + raw_med)
    else:
        if int(d_start.group(1)) != 11:
            problems.append("医务室的判定区不是 11 个(病历1-3 / 电池1-2 / "
                            "躲藏1-2 / 去楼梯间 / 安全屋 / 隐藏笔记 / 惊吓区): "
                            + d_start.group(0))
        # 进关 60:手电从第一帧就开始耗电,0.3 秒后读到的是 59.x ——
        # 断言给个区间,别把它写死成 60.0。
        charge_start = float(d_start.group(3))
        if not 58.0 <= charge_start <= 60.0:
            problems.append("进关手电电量不是 60(第 4 章应该只有 60): "
                            + d_start.group(0))
    d_locked = re.search(r"HORROR_MED_DOOR_LOCKED alive=true sheets=0\.0"
                         r" hint=true", med)
    if not d_locked:
        problems.append("病历一页都没读,侧门却开了(或者场景被切走了): "
                        + _grab(med, r"HORROR_MED_DOOR_LOCKED[^\n]*"))
    sheets_med = re.findall(r"HORROR_MED_SHEET n=(\d) count=([\d.]+)", med)
    if len(sheets_med) != 3:
        problems.append("3 页病历没有都读到: "
                        + _grab(med, r"HORROR_MED_SHEET[^\n]*"))
    else:
        for index, (number, count) in enumerate(sheets_med, start=1):
            if int(number) != index or float(count) != float(index):
                problems.append(f"读第 {index} 页病历后『病历数』不是 {index}: "
                                f"n={number} count={count}")
    d_tip = re.search(r"HORROR_MED_SHEET n=1[^\n]*tip=(true|false)", med)
    if not d_tip or d_tip.group(1) != "true":
        problems.append("靠近病历没有出现 [E] 提示: "
                        + _grab(med, r"HORROR_MED_SHEET[^\n]*"))
    d_ready = re.search(r"HORROR_MED_SHEET n=3[^\n]*reveal=(true|false)"
                        r" guide2=(true|false)", med)
    if not d_ready:
        problems.append("病历集齐那段没跑出结论行: "
                        + _grab(med, r"HORROR_MED_SHEET[^\n]*"))
    else:
        if d_ready.group(1) != "true":
            problems.append("病历集齐之后没有提示侧门: " + d_ready.group(0))
        if d_ready.group(2) != "true":
            problems.append("病历集齐之后目标行没切到『侧门』: " + d_ready.group(0))
    d_bat = re.search(r"HORROR_MED_BATTERY n=1 count=([\d.]+)"
                      r" charge=([\d.]+) gone=(true|false)", med)
    if not d_bat:
        problems.append("第一块电池那段没跑出结论行: "
                        + _grab(med, r"HORROR_MED_BATTERY[^\n]*"))
    else:
        if float(d_bat.group(1)) != 1.0:
            problems.append("捡了电池但『电池数』不是 1: " + d_bat.group(0))
        # 60 秒掉一点电之后 +40 -> 90 上下(手电一直在耗电,别写死)
        if not 88.0 <= float(d_bat.group(2)) <= 100.0:
            problems.append("电池没有补 40 点电(期望 90 以上): " + d_bat.group(0))
        if d_bat.group(3) != "true":
            problems.append("电池拿了但节点还在: " + d_bat.group(0))
    d_bat2 = re.search(r"HORROR_MED_BATTERY n=2 count=([\d.]+)"
                       r" charge=([\d.]+)", med)
    if not d_bat2 or float(d_bat2.group(1)) != 2.0:
        problems.append("第二块电池没捡起来(『电池数』不是 2): "
                        + _grab(med, r"HORROR_MED_BATTERY[^\n]*"))
    d_hide = re.search(r"HORROR_MED_HIDE hiding=true cam=(\S+)"
                       r" hidden=(true|false) curtain=(-?[\d.]+)"
                       r" enemy_dist=(-?[\d.]+)", med)
    if not d_hide:
        problems.append("躲进帘子那段没跑出结论行: "
                        + _grab(med, r"HORROR_MED_HIDE[^\n]*"))
    else:
        if d_hide.group(1) != "医务室躲藏机位":
            problems.append("躲进去之后相机没切到『医务室躲藏机位』: "
                            + d_hide.group(0))
        if d_hide.group(2) != "true":
            problems.append("躲进去之后玩家没有隐藏: " + d_hide.group(0))
        # 身体必须**离开帘子**(传送到视距外),而且离怪 > 视线 6 米
        if float(d_hide.group(3)) < 20.0:
            problems.append("躲进去之后身体没挪走(还在帘子原地): "
                            + d_hide.group(0))
        if float(d_hide.group(4)) <= 6.0:
            problems.append("躲进去之后还在怪物的视线里: " + d_hide.group(0))
    d_unhide = re.search(r"HORROR_MED_UNHIDE hiding=false cam=(\S+)"
                         r" hidden=(true|false) curtain_front=(-?[\d.]+)", med)
    if not d_unhide:
        problems.append("从帘子出来那段没跑出结论行: "
                        + _grab(med, r"HORROR_MED_UNHIDE[^\n]*"))
    else:
        if d_unhide.group(1) != "侧视机位":
            problems.append("出来之后相机没切回第三人称: " + d_unhide.group(0))
        if d_unhide.group(2) != "false":
            problems.append("出来之后玩家还是隐藏的(看不见自己): "
                            + d_unhide.group(0))
        # 契约(躲藏中按 E = 出来):位置必须是**显式设回帘子前**的
        # —— 躲藏时身体被挪到 40 米外那条没有地板的锚点,回来不能靠"落地"
        if float(d_unhide.group(3)) > 2.0:
            problems.append("按 E 出来之后没有回到帘子前(期望 2 米内): "
                            + d_unhide.group(0))
    d_hide_crouch = re.search(r"HORROR_MED_HIDDEN_CROUCH cam=(\S+)"
                              r" cam_drift=(-?[\d.]+)", med)
    if not d_hide_crouch:
        problems.append("躲着按 Ctrl 那段没跑出结论行: "
                        + _grab(med, r"HORROR_MED_HIDDEN_CROUCH[^\n]*"))
    else:
        if d_hide_crouch.group(1) != "医务室躲藏机位":
            problems.append("躲着按 Ctrl 把相机切走了: "
                            + d_hide_crouch.group(0))
        if abs(float(d_hide_crouch.group(2))) > 0.06:
            problems.append("躲着按 Ctrl 时躲藏机位被压低了(蹲走脚本不该动"
                            "场景相机): " + d_hide_crouch.group(0))
    d_crouch = re.search(r"HORROR_MED_CROUCH_DIP cam=(\S+)"
                         r" cam_drop=(-?[\d.]+)", med)
    if not d_crouch:
        problems.append("医务室蹲走压镜头那段没跑出结论行(蹲走脚本可能又认"
                        "不到 医务室玩家): "
                        + _grab(med, r"HORROR_MED_CROUCH_DIP[^\n]*"))
    else:
        if d_crouch.group(1) != "侧视机位":
            problems.append("蹲下时当前相机不是侧视机位: " + d_crouch.group(0))
        if not 0.40 <= float(d_crouch.group(2)) <= 0.90:
            problems.append("医务室蹲走时侧视机位没有被压低(期望 ~0.62): "
                            + d_crouch.group(0))
    d_fail = re.search(r"HORROR_MED_FAIL paused=(true|false)"
                       r" fall=(true|false) safehouse=(true|false)"
                       r" back=(true|false)", med)
    if not d_fail or "false" in d_fail.groups():
        problems.append("医务室被抓那段不对(冻世界 / 倒地 / 两个按钮): "
                        + _grab(med, r"HORROR_MED_FAIL[^\n]*"))
    d_home = re.search(r"HORROR_MED_SAFEROOM paused=false alive=true"
                       r" dist=([\d.]+) checkpoint=(\S*) sheets=([\d.]+)"
                       r" battery=([\d.]+) save_check=(true|false)"
                       r" save_notes=(true|false)", med)
    if not d_home:
        problems.append("医务室『回到安全屋』那段没跑出结论行"
                        "(可能又重载了整关): "
                        + _grab(med, r"HORROR_MED_SAFEROOM[^\n]*"))
    else:
        if float(d_home.group(1)) > 2.5:
            problems.append("没有被传送回医务室安全屋: " + d_home.group(0))
        if d_home.group(2) != "医务室_安全屋":
            problems.append("检查点没记成『医务室_安全屋』: " + d_home.group(0))
        if float(d_home.group(3)) != 3.0:
            problems.append("被抓回安全屋之后病历数没保留(期望 3): "
                            + d_home.group(0))
        if float(d_home.group(4)) < 85.0:
            problems.append("被抓回安全屋之后手电电量没保留: "
                            + d_home.group(0))
        if d_home.group(5) != "true":
            problems.append("医务室存的还是裸 JSON(没有『防改档』信封): "
                            + d_home.group(0))
        if d_home.group(6) != "true":
            problems.append("医务室存盘把老档的『纸条数』弄丢了: "
                            + d_home.group(0))
    d_open = re.search(r"HORROR_MED_DOOR_OPEN scene=(\S+)", med)
    if not d_open:
        problems.append("医务室侧门那段没跑出结论行: "
                        + _grab(med, r"HORROR_MED_DOOR_OPEN[^\n]*"))
    elif d_open.group(1) != "楼梯间":
        problems.append("病历齐了按 E 之后没有切到楼梯间(侧门不通): "
                        + d_open.group(0))
    # 第 186 轮:低电闪烁行为验证(全部断言跑完后的尾段,注入电量三窗采样)
    m_flk = re.search(r"HORROR_MED_FLICKER torch=(true|false)"
                      r" w0=([\-\d.]+)\.\.([\-\d.]+)"
                      r" w1=([\-\d.]+)\.\.([\-\d.]+)"
                      r" w2=([\-\d.]+)\.\.([\-\d.]+)", med)
    if not m_flk:
        problems.append("医务室低电闪烁段没跑出结论行: "
                        + _grab(med, r"HORROR_MED_FLICKER[^\n]*"))
    elif m_flk.group(1) != "true":
        problems.append("医务室手电筒没找到(闪烁验证作废): "
                        + m_flk.group(0))
    else:
        w0min, w0max = float(m_flk.group(2)), float(m_flk.group(3))
        w1min, w1max = float(m_flk.group(4)), float(m_flk.group(5))
        w2min, w2max = float(m_flk.group(6)), float(m_flk.group(7))
        if min(w0min, w1min, w2min) > 900.0:
            problems.append("闪烁采样窗没采到值(灯不可见或没找到): "
                            + m_flk.group(0))
        if w0max - w0min > 0.10:
            problems.append("基线窗亮度不稳(电量充足却在闪?): "
                            + m_flk.group(0))
        if w1max - w1min < 0.25:
            problems.append("低电窗亮度没波动(『灯光_闪烁』没登记上?): "
                            + m_flk.group(0))
        if w2max - w2min > 0.10:
            problems.append("回满后亮度不稳(『停止闪烁』没停干净?): "
                            + m_flk.group(0))
    if "找不到玩家" in med:
        problems.append("蹲走脚本在医务室没认到玩家"
                        "(蹲走的相机/胶囊那半失效): "
                        + _grab(med, r".*找不到玩家.*"))
    # 第 169 轮:医务室隐藏笔记
    note_med = re.search(r"HORROR_NOTE_MED hidden=([\d.]+)", med)
    if not note_med:
        problems.append("医务室隐藏笔记没跑出结论行: "
                        + _grab(med, r"HORROR_NOTE_MED[^\n]*"))
    elif note_med.group(1) != "3.0":
        problems.append("医务室隐藏笔记计数不是 3(存档接力断了): "
                        + note_med.group(0))
    # 第 175 轮:惊吓演出(档案间门洞,一次性)
    m_scare = re.search(r"HORROR_MED_SCARE shake=([\d.]+) bus=([\-\d.]+)", med)
    if not m_scare:
        problems.append("医务室惊吓没跑出结论行: "
                        + _grab(med, r"HORROR_MED_SCARE[^\n]*"))
    elif float(m_scare.group(1)) <= 0.0:
        problems.append("医务室惊吓没有抖动(触发区/防重链断了): "
                        + m_scare.group(0))
    if "HORROR_MED_DONE" not in med:
        problems.append("医务室探针没跑完")
    # ── M0.5 ① 笔记日志跨场景:走廊 + 楼梯间各跑一趟 ─────────────────
    for tag, scene_path, player_name, enemy_name in JOURNAL_TARGETS:
        stem = "probe_" + tag.lower()
        journal = _run_probe(args.godot, stem,
                             PROBE_JOURNAL % {"payload": JOURNAL_PAYLOAD,
                                              "salt": SAVE_SALT,
                                              "scene": scene_path,
                                              "player": player_name,
                                              "enemy": enemy_name,
                                              "tag": tag},
                             "HorrorJournalProbe", 600)
        _errors(journal, problems)
        where = "走廊" if tag == "JOURNAL" else "楼梯间"
        j_before = re.search(r"HORROR_" + tag + r"_BEFORE open=(\w+) panel=(\w+)",
                             journal)
        if not j_before:
            problems.append(f"{where}日志开场没跑出结论行: "
                            + _grab(journal, "HORROR_" + tag + r"_BEFORE[^\n]*"))
        elif j_before.group(1) != "false" or j_before.group(2) != "false":
            problems.append(f"进{where}时日志面板就已经开着: " + j_before.group(0))
        j_open = re.search(r"HORROR_" + tag + r"_OPEN open=(\w+) panel=(\w+)"
                           r" line1=(\[[^\]]*\]) line2=(\[[^\]]*\])"
                           r" line3=(\[[^\]]*\])", journal)
        if not j_open:
            problems.append(f"{where}里按 J 没跑出日志结论行: "
                            + _grab(journal, "HORROR_" + tag + r"_OPEN[^\n]*"))
        else:
            if j_open.group(1) != "true" or j_open.group(2) != "true":
                problems.append(f"{where}按 J 打不开笔记日志面板"
                                "(期望三关是同一份): " + j_open.group(0))
            for index, title in ((3, "值日日志"), (4, "医务室记录"),
                                 (5, "深夜广播稿")):
                if title not in j_open.group(index):
                    problems.append(f"{where}日志第 {index - 2} 行没有读出"
                                    f"《{title}》的正文(已读状态没跨关): "
                                    + j_open.group(0))
        j_esc = re.search(r"HORROR_" + tag + r"_ESC open=(\w+) panel=(\w+)",
                          journal)
        if not j_esc:
            problems.append(f"{where}里 Esc 关日志没跑出结论行")
        elif j_esc.group(1) != "false" or j_esc.group(2) != "false":
            problems.append(f"{where}里 Esc 没有关掉日志: " + j_esc.group(0))
        if f"HORROR_{tag}_DONE" not in journal:
            problems.append(f"{where}({tag})跨场景日志探针没跑完")

    # ── M0.5 ⑤:真视线射线的实测结论(只打印,不算失败)────────────────
    # 1200 帧:这一趟要等到开场运镜演完(约 7 秒)才量
    ray = _run_probe(args.godot, "probe_ray", PROBE_RAY,
                     "HorrorRayProbe", 1400)
    _errors(ray, problems)
    if "HORROR_RAY_DONE" not in ray:
        problems.append("射线可行性探针没跑完: "
                        + _grab(ray, r"HORROR_RAY[^\n]*"))

    # ── 第五关 楼梯间(第 161 轮重做:断电追逐 + 天台三结局)────────────
    # ⚠ `quit_after` 的单位是**帧**,不是秒;无头 Godot 一帧可能只花 2ms,
    #   900 帧 ≈ 2 秒游戏时间(旧版楼梯间只要 2 秒所以看不出来)。
    #   第 5 章的探针要等到 6 秒断电 + 结局,给 24000 帧当兜底
    #   (探针自己会 quit,这只是一个不会挡路的保险)。
    stairs = _run_probe(args.godot, "probe_stairs", PROBE_STAIRS,
                        "HorrorStairsProbe", 24000)
    _errors(stairs, problems)
    if not re.search(r"HORROR_STAIRS_START player=true notes=3\.0"
                     r" records=3\.0",
                     stairs):
        problems.append("楼梯间没把存档里的 纸条数(3)/病历数(3) 读回来: "
                        + _grab(stairs, r"HORROR_STAIRS_START[^\n]*"))
    # 第 163 轮:走路摆动必须真的认到零件(不是"生成器写完就算")
    # 第 164 轮:现在还要认出两条腿(名字带「腿」,和手臂反相摆)。
    # 第 164+1 轮:怪物也接了走路摆动,所以应有 2 条登记(玩家+怪物),
    # 且怪物那条也要认到左右臂+左右腿。
    walk = re.search(
        r"HORROR_WALK entries=(\d+) player_arms=(\d+) player_legs=(\d+) "
        r"monster_arms=(\d+) monster_legs=(\d+)", stairs)
    if not walk:
        problems.append("楼梯间没有『角色_走路摆动』的登记结论行")
    else:
        if int(walk.group(1)) < 2:
            problems.append("走路摆动只登记了玩家,怪物没接上(应有>=2条): "
                            + walk.group(0))
        if walk.group(2) != "2" or walk.group(3) != "2":
            problems.append("玩家走路没认全左右臂+左右腿: " + walk.group(0))
        if walk.group(4) != "2" or walk.group(5) != "2":
            problems.append("怪物走路没认到网格节点下的左右臂+左右腿"
                            "(body_links 配对/收集失败): " + walk.group(0))
    before = re.search(r"HORROR_STAIRS_BEFORE moved=([\d.]+) ai=(\S*)", stairs)
    if not before:
        problems.append("断电前的追兵没有结论行: "
                        + _grab(stairs, r"HORROR_STAIRS_BEFORE[^\n]*"))
    elif float(before.group(1)) > 0.3 or before.group(2) != "":
        problems.append("断电前追兵就动起来了(该等到『AI_启用』): "
                        + before.group(0))
    # 第 167 轮:楼梯是真台阶(级高 0.28) —— 追击者必须真的爬上去。
    # 它断电时在门厅平地,追向被传送到天台的玩家,2 秒内就该撞上第一段
    # 台阶并往上爬;z 还贴着 0 就是四关探测失效(又退回"斜坡骗台阶"了)。
    climb = re.search(r"HORROR_STAIRS_CLIMB chaser_z=([\-\d.]+)", stairs)
    if not climb:
        problems.append("台阶攀爬没有结论行: "
                        + _grab(stairs, r"HORROR_STAIRS_CLIMB[^\n]*"))
    elif float(climb.group(1)) < 0.2:
        problems.append("追击者没有爬上真台阶(四关探测失效?): "
                        + climb.group(0))
    blackout = re.search(r"HORROR_STAIRS_BLACKOUT lit=(\w+) lit3=(\w+)"
                         r" red=(\w+) ai=(\S*) moved=([\d.]+)", stairs)
    if not blackout:
        problems.append("断电那一步没有结论行: "
                        + _grab(stairs, r"HORROR_STAIRS_BLACKOUT[^\n]*"))
    else:
        if blackout.group(1) != "false" or blackout.group(2) != "false":
            problems.append("断电后应急灯还亮着: " + blackout.group(0))
        if blackout.group(3) != "true":
            problems.append("断电后天台红灯也灭了(玩家没有方向): "
                            + blackout.group(0))
        if blackout.group(4) != "追击":
            problems.append("断电后追兵没有进入追击: " + blackout.group(0))
        if float(blackout.group(5)) <= 0.5:
            problems.append("断电后追兵一动不动: " + blackout.group(0))
    door = re.search(r"HORROR_DOOR picked=([\-\d]+) panel=(\w+)"
                     r" paused=(\w+) options=(\d+)", stairs)
    if not door or int(door.group(1)) != -1 or door.group(2) != "true":
        problems.append("走到天台没弹出结局选择: "
                        + _grab(stairs, r"HORROR_DOOR[^\n]*"))
    else:
        if door.group(3) != "true":
            problems.append("弹出结局选择时世界没有冻住: " + door.group(0))
        if int(door.group(4)) != 3:
            problems.append("天台选项数不是 3(真结局那条没接上): "
                            + door.group(0))
    ending = re.search(r"HORROR_ENDING ended=([\d.]+) saved=(\w+)"
                       r" picked=(\d+) back=(\w+)", stairs)
    if not ending or float(ending.group(1)) != 3.0:
        problems.append("档案齐时按 3 没有走到真结局(结局该是 3): "
                        + _grab(stairs, r"HORROR_ENDING[^\n]*"))
    else:
        if ending.group(2) != "true":
            problems.append("结局没有落盘: " + ending.group(0))
        if ending.group(4) != "true":
            problems.append("结局后没有『回到标题』按钮: " + ending.group(0))
    envelope_form = re.search(r"HORROR_SAVE_FORMAT check=(\w+)", stairs)
    if not envelope_form or envelope_form.group(1) != "true":
        problems.append("结局落盘的不是带校验的信封(『防改档』没生效): "
                        + _grab(stairs, r"HORROR_SAVE_FORMAT[^\n]*"))
    unpause = re.search(r"HORROR_STAIRS_UNPAUSE paused=(\w+)", stairs)
    if not unpause or unpause.group(1) != "false":
        problems.append("点『回到标题』后没有解冻(标题页也会冻着): "
                        + _grab(stairs, r"HORROR_STAIRS_UNPAUSE[^\n]*"))
    if "HORROR_STAIRS_DONE" not in stairs:
        problems.append("楼梯间探针没跑完")

    # ── 第九十三轮『防改档』:信封往返 + 改档被拒 ─────────────────────
    # 顺序有关系:楼梯间那趟刚用**生成代码**写下信封,下一趟接着读它。
    envelope = _run_probe(args.godot, "probe_envelope", PROBE_ENVELOPE,
                          "HorrorEnvelopeProbe", 600)
    _errors(envelope, problems)
    back = re.search(r"HORROR_ENVELOPE notes=([\d.]+)", envelope)
    if not back or float(back.group(1)) != 3.0:
        problems.append("自己写的带校验存档读不回来(信封往返断了): "
                        + _grab(envelope, r"HORROR_ENVELOPE[^\n]*"))

    tamper = _run_probe(args.godot, "probe_tamper", PROBE_TAMPER,
                        "HorrorTamperProbe", 600)
    _errors(tamper, problems)
    broke = re.search(r"HORROR_TAMPER notes=([\d.]+) panel=(\w+)"
                      r" options=(\d+)", tamper)
    if not broke:
        problems.append("改档那趟没跑出结论行: "
                        + _grab(tamper, r"HORROR_TAMPER[^\n]*"))
    else:
        if float(broke.group(1)) != 0.0:
            problems.append("被改过的存档没被拒(纸条数该回默认 0): "
                            + broke.group(0))
        # 第 5 章:天台不靠"纸条数"放行 —— 改档被拒只影响**真结局**,
        # 普通结局照走(不然手改一次档就把玩家卡死在门口 = 死局)。
        if broke.group(2) != "true" or int(broke.group(3)) != 3:
            problems.append("改档被拒后天台把玩家卡住了(该照常给 3 个选项): "
                            + broke.group(0))
    if "HORROR_TAMPER_DONE" not in tamper:
        problems.append("改档探针没跑完")

    # ── 残缺存档:天台不再是死局 + 真结局那条要判档案 ───────────────
    short = _run_probe(args.godot, "probe_stairs_short", PROBE_STAIRS_SHORT,
                       "HorrorStairsShortProbe", 9000)
    _errors(short, problems)
    broke = re.search(r"HORROR_STAIRS_SHORT notes=([\d.]+)"
                      r" records=([\d.]+) panel=(\w+) options=(\d+)", short)
    if not broke:
        problems.append("残缺存档那趟没跑出结论行: "
                        + _grab(short, r"HORROR_STAIRS_SHORT[^\n]*"))
    else:
        if float(broke.group(1)) != 1.0 or float(broke.group(2)) != 0.0:
            problems.append("残缺存档没按「缺」读回来: " + broke.group(0))
        if broke.group(3) != "true" or int(broke.group(4)) != 3:
            problems.append("残缺存档把玩家卡在天台了(又成死局): "
                            + broke.group(0))
    short_end = re.search(r"HORROR_STAIRS_SHORT_END ended=([\d.]+)"
                          r" subtitle=(\w+)", short)
    if not short_end or float(short_end.group(1)) != 2.0:
        problems.append("档案不齐时选『看清那张脸』该落到结局 2(记忆缺页): "
                        + _grab(short, r"HORROR_STAIRS_SHORT_END[^\n]*"))
    elif short_end.group(2) != "true":
        problems.append("档案不齐时没有「想不起来」那句字幕(该显示却没显示): "
                        + short_end.group(0))
    if "HORROR_STAIRS_SHORT_DONE" not in short:
        problems.append("残缺存档探针没跑完")

    # ── 生成物层面的证据 ────────────────────────────────────────────
    _code_checks(problems)
    _scene_checks(problems)
    _main_scene_check(problems)
    _glb_checks(problems)

    # ── 第 176 轮:真实游玩断言 ─────────────────────────────────────
    if "REALPLAY_DONE" not in realplay:
        problems.append("真实游玩探针没跑完(可能被怪物困住/点击没生效): "
                        + _grab(realplay, r"REALPLAY_[A-Z_]+[^\n]*"))
    else:
        rp_turn = re.search(r"REALPLAY_TURN turned=([\d.]+)", realplay)
        if not rp_turn:
            problems.append("真实游玩转向没跑出结论行: "
                            + _grab(realplay, r"REALPLAY_TURN[^\n]*"))
        elif float(rp_turn.group(1)) < 1.0:
            problems.append("横移时人物没有转向移动方向(turn="
                            + rp_turn.group(1) + "): " + rp_turn.group(0))
        rp_step = re.search(r"REALPLAY_STEP found=(\w+) (\S+)",
                             realplay)
        if not rp_step:
            problems.append("脚步声没跑出结论行: "
                            + _grab(realplay,
                                r"REALPLAY_STEP[^\n]*"))
        elif rp_step.group(1) != "true":
            problems.append("走起来没有脚步播放器(走路摆动的脚步声"
                            "断了): " + rp_step.group(0))
        rp_mouse = re.search(r"REALPLAY_MOUSE mode=(\d+) visible=(\w+)",
                             realplay)
        if not rp_mouse:
            problems.append("失败后鼠标没跑出结论行: "
                            + _grab(realplay, r"REALPLAY_MOUSE[^\n]*"))
        elif rp_mouse.group(2) != "true":
            problems.append("被抓暂停后鼠标没有切换成可见模式(mode="
                            + rp_mouse.group(1) + "): " + rp_mouse.group(0))
        rp_music = re.search(r"REALPLAY_MUSIC playing=(\w+) path=(\S+)",
                             realplay)
        if not rp_music:
            problems.append("追逐切曲没跑出结论行: "
                            + _grab(realplay, r"REALPLAY_MUSIC[^\n]*"))
        elif "chase" not in rp_music.group(2):
            problems.append("被抓时放的不是追逐曲(切换链断了): "
                            + rp_music.group(0))
        rp_click = re.search(r"REALPLAY_CLICK clicked=(\w+) back=(\w+)"
                             r" paused=(\w+) dist_home=([\-\d.]+)",
                             realplay)
        if not rp_click:
            problems.append("真实点击没跑出结论行: "
                            + _grab(realplay, r"REALPLAY_CLICK[^\n]*"))
        # back 在 headless 下测不准(DisplayServer 无输入管线),不进 problems;
        # 真机点击生效性由 REALPLAY_GUI 诊断行 + 人工 exe 试玩确认。

    # ── 时长实测(第 169 轮):各探针 DONE 行带 total=,这里汇总 ──────
    timings = []
    for label, probe_out in (("教室", room), ("走廊", corridor),
                             ("音乐教室", music), ("医务室", med),
                             ("楼梯间", stairs)):
        m_t = re.search(r"total=([\d.]+)", probe_out)
        timings.append((label, float(m_t.group(1)) if m_t else -1.0))
    print("HORROR_TIMING " + " ".join(
        f"{label}={sec:g}s" for label, sec in timings))
    full = sum(sec for _label, sec in timings if sec > 0)
    print(f"HORROR_TIMING_FULL {full:g}s(自动试玩;人工实机会更长)")

    for item in dict.fromkeys(problems):
        print(f"✗ {item}")
    print("CHECK_HORROR_DEMO=" + ("OK" if not problems else "FAIL"))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())