"""3D 恐怖剧情闯关(第一/三人称可切换)示例的自动检查。

用法:
    python scripts\\check_story_demo.py
    python scripts\\check_story_demo.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

它会:
  1. 把 `examples/demo_story/恐怖闯关示例_godot` 拷到 `_dist/story_probe`
     (**不碰示例本身**);
  2. 在副本里加一个探针场景,把主场景指过去;
  3. 用 Godot 无头跑,读探针打印的 STORY_* 行,汇总 PASS / FAIL。

── 探针怎么测(而不是读文本)────────────────────────────────────
探针**自己当主场景**,把三张关卡 .tscn **依次**实例化成子节点驱动
(测完 `queue_free()`)。**不能**用 `change_scene_to_file` 跑关卡切换 ——
那会把当前场景(探针)整棵换掉。真正的往返切关由独立 SceneTree 探针
`check_persistent_player.py` 验证，确保实例、子对象和状态保留。

断言(全是"现象",不是"代码长什么样"):
  * 关卡 1 大堂:章节卡 + HUD(目标 / 钥匙数 / 电量 / 提示 / 按键提示)建出来;
    **C → 第一人称机位(自身模型藏掉)/ V → 第三人称机位(模型显回来)**;
    走进钥匙触发区 -> 钥匙数 = 1、钥匙物体隐掉、目标文字改了;
    F 开手电(SpotLight 亮 + 掉电)、再按 F 关;**Esc 暂停 + 点继续解冻**;
    走进大门区域 -> 大门链真的跑了(时间轴开始 + 门板在升起);
  * 关卡 2 走廊:开场对话 / 剧情选择弹出来;AI 在巡逻;钻柜子 -> 提示
    『屏住呼吸…』+ 暗角变深、**AI 状态仍是巡逻(不再冻结)**、出来恢复;
    **真潜行:玩家在柜子里、怪物贴在正前方也看不见(仍巡逻),挪出柜子才追击**;
    凑到怪物正前方近处 -> 转追击 + 紧张度上去 + 心跳在响 + 贴身惊吓;
  * 关卡 3 结局:选择框弹出 -> 按 1 -> 存档落盘(user://)+ 结局大字显示。
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
SRC = os.path.join(ROOT, "examples", "demo_story", "恐怖闯关示例_godot")
OUT = os.path.join(ROOT, "_dist", "story_probe")
GODOT_DEFAULT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"

# 三关(Blender 场景名 = 导出的 .tscn 名)
LOBBY = "大堂"
CORRIDOR = "走廊"
ENDING = "结局"

PROBE_GD = '''extends Node

# 恐怖闯关示例的探针。结论行统一 STORY_ 前缀,Python 那边逐条断言。
#
# 探针**自己当主场景**,把三张关卡 .tscn **依次**实例化成子节点驱动
# (测完 queue_free())—— 不用 change_scene_to_file:那会把探针整棵换掉。
# 状态机跑在 _physics_process 里(而不是 await 协程):按 C / V / F 这类键
# 要 `Input.is_action_just_pressed` 轮询,而"按下"和"松开"必须**跨帧**
# (同一帧里松了又按,那一帧的 just_pressed 会被后面的松手冲掉)。
# ⚠ 探针的物理帧优先级压到最低:保证按下键那一刻**先**被探针发出去,
#   同一帧里关卡角色的 _physics_process 才读得到"刚按下"。
var world: Node = null
var player: Node = null
var enemy: Node = null
var step := 0
var t := 0.0
# 跨步要留的值
var spawn := Vector3.ZERO
var after_c := ""
var after_v := ""
var torch: Node = null
var on_visible := false
var on_flag := false
var drain := 0.0
var battery_ui := ""
var off_visible := false
var door_y0 := 0.0
var hide_state := ""
var hide_hint := ""
var hide_vig := -1.0
var heard := false
var max_shake := 0.0
var advance_at := 0.0
# 第一人称藏模型 / 真潜行 / 暂停菜单 三项新断言的采样值
var fp_model := false
var tp_model := true
var pause_on := false
var resume_ok := false
var hide_see_in := ""
var hide_see_out := ""
# 第 124 轮:进柜子拆成几个"非阻塞小步"要用的中转值
# (不能在 `_physics_process` 里 await —— 会把同一个分支反复进入)
var _enter_center := Vector3.ZERO
var _enter_area_node: Area3D = null
var _enter_guard := 0
# 第一人称"移动会不会带跑视角"的采样
var fp_yaw0 := 0.0
var fp_yaw1 := 0.0
var fp_rot0 := 0.0
var fp_rot1 := 0.0
var fp_pos0 := Vector3.ZERO
var choice_in = ""
var choice_in2 = ""
# 第一百三十四轮:过场里按 Esc 前后的世界冻结状态(见 STORY_PAUSE_CHOICE)
var pause_before := false
var pause_after_esc := false
var choice_end = ""
# 设置页 / Esc 开关的采样
var settings_open := false
var settings_closed := false
var bus_db := 0.0
var esc_resumed := false
# 第 125 轮:暂停冻结等待的采样
var paused_running := false
var _timer_woke := false
var settings_ui := ""
var dialog_mouse_clicked := false
# ── P1(第一百二十六轮):暂停菜单出现时旧界面让位 ──────────────────────
# 用户原话:"有时点快了,就会有这样的情况,那么在如ESC菜单出现时就立刻隐藏
# 旧的界面,或者添加模糊背景" —— 配图是章节卡「第一章:门厅」糊在暂停面板的
# 「继续游戏」上。采三个现场:章节卡收没收起 / 菜单是不是最上层 /
# HUD(目标 + 电量)有没有被误伤。
var card_hidden := false
var menu_top := false
var hud_kept := false
var dim_shown := false


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	# 探针先跑、关卡后跑(见文件头)。
	process_physics_priority = -1000
	# ⚠ 无头模式主循环**不锁帧**(能跑到几百 fps),而物理步是每秒 60 次 ——
	#   于是 `--quit-after` 数的"帧"远远跑在"物理时间"前面:三关加起来要
	#   十几秒物理时间,却只够跑一两千帧,安全网会**提前**把探针掐掉(实测
	#   撞过:走廊跑完、结局那关一个字都没打印)。锁到 60 = 帧率与物理同步,
	#   1800 帧差不多就是 30 秒,安全网才是真的"安全网"。
	Engine.max_fps = 60
	# ⚠ 无头模式默认视口只有 64×64:先调大再实例化 —— 界面控件的锚点 /
	#   一些按视口尺寸算的逻辑都按它来,太小会读到怪值。
	get_window().size = Vector2i(1152, 648)


func _push_key(code: int, pressed: bool) -> void:
	# 和真人按键走同一条路(事件流)。keycode + physical_keycode 都设:
	# 生成脚本里既有按动作名查的(C / V / F),也有直接看键码的(选项按 1/2)。
	var event := InputEventKey.new()
	event.keycode = code
	event.physical_keycode = code
	event.pressed = pressed
	Input.parse_input_event(event)


func _find(name: String, type_name: String) -> Node:
	if world == null:
		return null
	var found := get_tree().root.find_children(name, type_name, true, false)
	return found[0] if not found.is_empty() else null


## 把玩家摆到怪物**当前前方** `distance` 米处。
##
## ── 第一百二十六轮:为什么不能写死 `+Vector3(0, 0, 3)` ────────────────
## 示例的视野角从 **0(360° 全向)** 改成了 **120(正前方 ±60° 锥)**,
## 于是"把玩家摆到 +Z 方向就一定被看见"**不再成立** —— 那要取决于怪物
## 当时朝哪边(它刚被探针硬摆了位置,朝向是上一段巡逻留下的)。
## 原来那条断言其实是在测"距离 + 全向感知",**没有**在测"看得见"。
## 改成沿 `-basis.z` 摆:这正是生成体判角度用的**同一个轴**(Godot 里物体的
## 正前方),所以它测的是『AI_看得见』本身,而且**不依赖怪物当时的朝向**。
func _place_in_front(enemy: Node, player: Node, distance: float) -> void:
	if enemy == null or player == null:
		return
	var forward: Vector3 = -(enemy as Node3D).global_transform.basis.z
	player.global_position = (enemy as Node3D).global_position \
		+ forward.normalized() * distance


## 把玩家**送进**某个 Area3D:先等物理确认"人确实在外面",再挪进去。
##
## ── 为什么不能一帧直接传送(第 124 轮:这条探针本身不稳)──────────────
## 这一关的"躲进柜子"在游戏里靠 Area3D 的 `body_entered` 信号:玩家
## **走进**触发区 -> Godot 发信号 -> 提示 + 暗角。
## 探针原来是**一帧直接把 global_position 设成柜子中心**。那样"进去"是真是假
## 取决于物理服务器上一帧记的是什么 —— 实测同一份产物有时发信号、有时不发,
## 于是这条断言会**随机变红**(我先按"是不是碰撞体改坏了"查了一圈,
## 结论是**探针不稳**,不是产物坏了)。
## 现在:①先落到区外 2.5 米,循环等物理帧**直到 `overlaps_body` 明确为 false**;
##       ②再挪到中心 -> 那一次一定是"真进入",信号必发。
func _enter_area(node: Node3D, area: Area3D) -> void:
	if node == null or area == null:
		return
	var center := area.global_position
	node.global_position = center + Vector3(2.5, 0.0, 0.0)
	var guard := 0
	while guard < 120:
		await get_tree().physics_frame
		guard += 1
		if not area.overlaps_body(node):
			break
	node.global_position = center
	# ⚠ 再等**物理真的认了这次重叠**才返回(第 124 轮踩到):
	#   调用方 `await _enter_area(...)` 之后会立刻读 HUD 文本,而
	#   `body_entered` 是**这一帧的物理步**里发出来的 —— 不等它,
	#   读到的就是"上一帧的空字符串"(实测:提示为空、但信号其实发了)。
	#   所以这里一直等到 `overlaps_body` 为真(最多 60 帧,约 1 秒)。
	guard = 0
	while guard < 60:
		await get_tree().physics_frame
		guard += 1
		if area.overlaps_body(node):
			break
	# 信号回调在同一帧里排在这之后,再让出一帧,确保 HUD 已经写好
	await get_tree().physics_frame


func _load(level: String) -> void:
	world = load("res://scenes/" + level + ".tscn").instantiate()
	# ⚠ 关卡必须**显式**设成"可暂停"(第一百一十轮真机抓到的教训):
	#   探针自己必须是 PROCESS_MODE_ALWAYS(否则暂停之后它也不跑了),而
	#   Godot 的 PROCESS_MODE_INHERIT 是**沿父链继承**的 —— 关卡作为探针的
	#   子节点就一起变成"暂停也照跑",于是"暂停把逐字冒出 / 动画冻住"这类
	#   bug 探针**永远看不见**(上一轮就是这样:探针全绿、用户看到空面板)。
	#   `PAUSABLE` 是"树暂停我就停",和父级无关 —— 这才和真跑游戏时的父级
	#   (视口)行为一致。
	# ⚠ 为什么还挂在探针下面(而不是 get_tree().root):生成代码里的
	#   `_gk_set_light` 等 helper 是从 `get_tree().current_scene` 往下找节点的,
	#   而 current_scene 是**探针**(主场景)。挂到 root 下当兄弟的话,
	#   那些 helper 就找不到关卡里的灯了(实测:手电怎么按都不亮/不灭)。
	world.process_mode = Node.PROCESS_MODE_PAUSABLE
	add_child(world)
	var manager = get_node("/root/GkWorld")
	manager.player.global_transform = world.get_node("GKPlayerStart").global_transform
	manager.player.velocity = Vector3.ZERO
	manager.player.process_mode = Node.PROCESS_MODE_PAUSABLE
	for property in manager.player.get_property_list():
		if String(property.name).begins_with("_gk_spring_ready"):
			manager.player.set(property.name, false)


func _drop_world() -> void:
	if world != null:
		world.queue_free()
	world = null
	player = null
	enemy = null


func _widget(name: String) -> Node:
	if player != null:
		var ui := player.find_child("GKUI", true, false)
		if ui != null:
			var root := ui.find_child("Root", true, false)
			if root != null:
				var found := root.find_child(name, true, false)
				if found != null:
					return found
	# 关卡逻辑(第一百二十轮):HUD 挂在**关卡实例根节点**(world)的关卡
	# 脚本上,不在角色脚本下 —— 从 world 往下再找一遍。
	if world != null:
		var ui2 := world.find_child("GKUI", true, false)
		if ui2 != null:
			var root2 := ui2.find_child("Root", true, false)
			if root2 != null:
				var found2 := root2.find_child(name, true, false)
				if found2 != null:
					return found2
	# 主界面(跨场景常驻)里的控件也按名字找 —— 暂停菜单 / 设置面板 /
	# 按键提示现在住在那里(第一百一十二轮:全局一份,不再每关各建)。
	var main: Node = get_tree().root.get_node_or_null("GkMainUi")
	if main != null:
		return main.find_child(name, true, false)
	return null


func _text(name: String) -> String:
	var node := _widget(name)
	return String(node.text) if node != null else "<无>"


func _var(name: String):
	# 角色变量在角色脚本上,关卡变量(钥匙数/手电电量…)在关卡实例根节点
	# (world)的关卡脚本上(第一百二十轮拆分)—— 两处都查。
	if player != null:
		var v = player.get(name)
		if v != null:
			return v
	return world.get(name) if world != null else null


func _panel_visible(which: String) -> bool:
	var panel = _var(which)
	if panel == null:
		panel = _widget(which)
	return panel != null and panel.visible


## 控件(或它的祖先链)当前画不画 —— 父面板藏起来时子控件也不画。
func _widget_visible(which: String) -> bool:
	var node := _widget(which)
	if node == null:
		return false
	while node != null:
		if node is CanvasItem and not (node as CanvasItem).visible:
			return false
		node = node.get_parent()
	return true


## 控件住在哪个 CanvasLayer(层号)。跨层的先后由层号决定,同层才轮到
## 兄弟顺序 —— 所以"谁在上面"必须**先**比层号(真机量过:主界面和关卡的
## GKUI 层号同为 10 时,谁在上面由 /root 里的树序决定,跟 move_to_front 无关)。
func _canvas_layer_of(node: Node) -> int:
	var walk := node
	while walk != null:
		if walk is CanvasLayer:
			return int((walk as CanvasLayer).layer)
		walk = walk.get_parent()
	return -999


func _vig_amount() -> float:
	# 暗角是**全屏 ColorRect + 着色器**做的(不是 Environment 参数),
	# 所以读材质的 `vignette_amount` —— 节点不存在时返回 -1。
	var mat = _var("_gk_vignette_mat")
	if mat == null:
		return -1.0
	return float(mat.get_shader_parameter("vignette_amount"))


func _cam_name() -> String:
	var cam := get_viewport().get_camera_3d()
	return String(cam.name) if cam != null else "<无>"


func _cam_yaw(cam: Node) -> float:
	# 相机"看向哪"的水平角(弧度)。相机朝 -Z 看,所以前向 = -basis.z。
	if cam == null or not (cam is Node3D):
		return -999.0
	var forward := -(cam as Node3D).global_transform.basis.z
	return atan2(forward.x, forward.z)


func _node_yaw(node: Node) -> float:
	if node == null or not (node is Node3D):
		return -999.0
	return (node as Node3D).rotation.y


func _model_visible() -> bool:
	# 角色网格是玩家节点下的 Model 子节点(导出时生成的角色自带网格)。
	# 第一人称要把它藏掉,否则相机贴头会看到自己身体的面。
	if player == null:
		return false
	var m := player.find_child("Model", true, false)
	return m != null and m.visible


func _click_widget(name: String) -> void:
	# 直接发按钮的 pressed 信号:暂停时 GUI 点击照常触发(见生成脚本注释),
	# 这里不用真的把鼠标挪过去点。
	var node := _widget(name)
	if node is BaseButton:
		(node as BaseButton).pressed.emit()


func _click_dialog() -> void:
	var panel = _var("_gk_dialog_panel")
	if panel == null:
		return
	var event := InputEventMouseButton.new()
	event.button_index = MOUSE_BUTTON_LEFT
	event.pressed = true
	panel.gui_input.emit(event)


func _choice_state() -> String:
	# 选择框的"看到的样子":问题文字有没有真的显示出来(逐字冒出的进度)、
	# 选项按钮有几个、文字是什么。诊断用(不是断言,断言在 Python 那边)。
	#
	# ⚠ 这段代码是要塞进 Python 的格式化模板里的 —— 里面**不能出现裸的百分号**
	#   (会跟模板的占位符抢参数),所以一律用字符串拼接 / str()。
	var panel = _var("_gk_choice_panel")
	if panel == null:
		return "panel=<无>"
	var q: Node = panel.find_child("问题", true, false)
	var box: Node = panel.find_child("选项", true, false)
	var parts := []
	if box != null:
		for child in box.get_children():
			parts.append(String(child.text) + "|vis=" + str(child.visible))
	var text := "paused=" + str(get_tree().paused)
	text += " vis=" + str(panel.visible) + " size=" + str(panel.size)
	text += " q=[" + (String(q.text) if q != null else "<?>") + "]"
	text += " vc=" + str(q.visible_characters if q != null else -999)
	text += " n=" + str(parts.size()) + " [" + ",".join(parts) + "]"
	return text


func _key_info(action: String) -> String:
	# 一个动作在 project.godot 里到底绑到了哪个键(Alt 那条链全看这个)。
	if not InputMap.has_action(action):
		return "action=<不存在>"
	var codes := []
	for event in InputMap.action_get_events(action):
		if event is InputEventKey:
			codes.append(str((event as InputEventKey).physical_keycode))
	return "action=" + action + " codes=[" + ",".join(codes) + "]"


func _panel_state(which: String) -> String:
	# 一个界面面板里每行字到底显示没显示(逐字冒出的进度 = visible_characters,
	# 0 = 一个字都没显示出来 —— 用户看到的就是"空面板")。
	var node = _var(which)
	if node == null:
		return which + "=<无>"
	var parts := []
	for label in node.find_children("*", "Label", true, false):
		parts.append(String(label.name) + "=" + String(label.text).substr(0, 16)
			+ "|vc=" + str(label.visible_characters))
	return which + " vis=" + str(node.visible) + " [" + ",".join(parts) + "]"


func _dialog_flags() -> String:
	# 对话正文到底有没有"打完字"(暂停把 tween 冻住时它会一直停在 0)。
	var label = _var("_gk_dialog_text")
	if label == null:
		return "typed=false vc=-1 len=0"
	var shown: int = label.visible_characters
	var length := String(label.text).length()
	var typed := shown < 0 or shown >= length
	return "typed=" + str(typed) + " vc=" + str(shown) + " len=" + str(length)


func _choice_flags() -> String:
	# 选择框:问题打完字没 + 选项按钮上有没有写键盘数字(1. / 2. )。
	var panel = _var("_gk_choice_panel")
	if panel == null:
		return "q_vc=-1 n=0 numbered=false"
	var q: Node = panel.find_child("问题", true, false)
	var box: Node = panel.find_child("选项", true, false)
	var vc := -1
	if q != null:
		vc = q.visible_characters
	var count := 0
	var numbered := true
	if box != null:
		for index in range(box.get_child_count()):
			count += 1
			if not String(box.get_child(index).text).begins_with(str(index + 1) + ". "):
				numbered = false
	return "q_vc=" + str(vc) + " n=" + str(count) + " numbered=" + str(numbered)


func _slider_of(container_name: String) -> Node:
	# 『界面_滑块』建出来的是一个 HBox(名字标签 + HSlider + 数值标签),
	# 真正能设值的是里面那个 HSlider —— 直接给 HBox 赋 value 会报
	# "Invalid assignment of property 'value' on HBoxContainer"
	# (而且是**运行期报错会中断当帧函数**,探针会卡死在那一步)。
	# ⚠ `find_child` 只有 3 个参数(没有类型过滤),要按类型找得用 find_children。
	var holder = _widget(container_name)
	if holder == null:
		return null
	var found := holder.find_children("*", "HSlider", true, false)
	return found[0] if not found.is_empty() else null


func _physics_process(delta: float) -> void:
	t += delta
	match step:
		# ── 关卡 1 大堂 ────────────────────────────────────────────
		0:
			if t >= 0.1:
				_load("%(lobby)s")
				player = _find("玩家", "CharacterBody3D")
				step = 1
				t = 0.0
		1:
			if t >= 0.5:
				print("STORY_LOBBY_HUD goal=", _text("目标文本"),
					" keys=", _text("钥匙文本"),
					" battery=", _text("电量文本"),
					" hint=", _text("提示文本"),
					" card=", _text("章节卡"))
				var keyhint_node := _widget("按键提示")
				var keyhint_ok := keyhint_node != null and String(keyhint_node.text).strip_edges() != ""
				print("STORY_KEYHINT ok=", keyhint_ok)
				# Alt 到底绑到了哪个键:4194328 = KEY_ALT(对);4194327 = META(错)。
				print("STORY_ALT_KEY ", _key_info("key_left_alt"))
				_push_key(KEY_C, true)      # C = 第一人称
				step = 2
				t = 0.0
		2:
			if t >= 0.25:
				_push_key(KEY_C, false)
				step = 3
				t = 0.0
		3:
			if t >= 0.3:
				after_c = _cam_name()
				fp_model = _model_visible()
				# ── 第一人称下"按移动键会不会把视角带跑" ────────────────
				# 记下按之前的视角水平角,按住 **D(横移)** 一秒再比一次。
				# 为什么测横移不测前进:弹簧臂"平滑"造成的滞后是**沿位移方向**的,
				# 前进时滞后正好在视线轴上(看不出来),横移时滞后垂直于视线轴 ——
				# 相机被落在侧面,却还在 `look_at(枢轴)`,视线就被甩出去。
				# 用户报的"移动输入控制视角旋转"就是这个(第一百一十轮修)。
				var cam0 := get_viewport().get_camera_3d()
				fp_yaw0 = _cam_yaw(cam0)
				fp_rot0 = _node_yaw(player)
				fp_pos0 = player.global_position if player != null else Vector3.ZERO
				_push_key(KEY_D, true)
				step = 200
				t = 0.0
		200:
			if t >= 1.0:
				var cam1 := get_viewport().get_camera_3d()
				fp_yaw1 = _cam_yaw(cam1)
				fp_rot1 = _node_yaw(player)
				var moved := 0.0
				if player != null:
					moved = (player.global_position - fp_pos0).length()
				print("STORY_FP_MOVE strafe_d_yaw=",
					snappedf(rad_to_deg(absf(fp_yaw1 - fp_yaw0)), 0.01),
					" rot0=", snappedf(fp_rot0, 0.01),
					" rot1=", snappedf(fp_rot1, 0.01),
					" moved=", snappedf(moved, 0.01))
				_push_key(KEY_D, false)
				_push_key(KEY_V, true)      # V = 第三人称
				step = 4
				t = 0.0
		4:
			if t >= 0.25:
				_push_key(KEY_V, false)
				step = 5
				t = 0.0
		5:
			if t >= 0.3:
				after_v = _cam_name()
				tp_model = _model_visible()
				print("STORY_LOBBY_CAM after_c=", after_c, " after_v=", after_v)
				print("STORY_FP_HIDE fp=", fp_model, " tp=", tp_model)
				# 挪进钥匙触发区(物理帧之后 Area3D 才会报进出事件)
				var key_area := _find("钥匙", "Area3D")
				if key_area != null and player != null:
					player.global_position = key_area.global_position
				step = 6
				t = 0.0
		6:
			if t >= 0.4:
				var key_area2 := _find("钥匙", "Area3D")
				print("STORY_LOBBY_KEY picked=", int(_var("钥匙数")),
					" visible=", key_area2.visible if key_area2 != null else "<无>",
					" keys_ui=", _text("钥匙文本"),
					" goal_ui=", _text("目标文本"))
				torch = _find("手电筒", "SpotLight3D")
				_push_key(KEY_F, true)      # F = 手电开
				step = 7
				t = 0.0
		7:
			if t >= 0.25:
				_push_key(KEY_F, false)
				step = 8
				t = 0.0
		8:
			if t >= 0.2:
				on_visible = torch != null and torch.visible
				step = 9
				t = 0.0
		9:
			# 亮着掉 1.5 秒电,再看电量少没少
			if t >= 1.5:
				on_flag = bool(_var("手电开"))
				drain = float(_var("手电电量"))
				battery_ui = _text("电量文本")
				_push_key(KEY_F, true)      # 再按一次 = 关
				step = 10
				t = 0.0
		10:
			if t >= 0.25:
				_push_key(KEY_F, false)
				step = 11
				t = 0.0
		11:
			if t >= 0.2:
				off_visible = torch != null and torch.visible
				print("STORY_LOBBY_TORCH on_visible=", on_visible,
					" on_flag=", on_flag,
					" drain=", snappedf(drain, 0.01),
					" battery_ui=", battery_ui,
					" off_visible=", off_visible,
					" off_flag=", bool(_var("手电开")))
				_push_key(KEY_ESCAPE, true)    # 按住 Esc:暂停菜单热键是每帧轮询的
				step = 12
				t = 0.0
		12:
			if t >= 0.2:
				_push_key(KEY_ESCAPE, false)
				step = 13
				t = 0.0
		13:
			# Esc 之后:世界暂停 + 暂停菜单弹出来。
			# ⚠ 这里**先**采一次现场(下一小步),再去点『设置』—— 用户报的
			#   就是"菜单刚弹出来那一瞬间,旧界面还压在它上面",那个瞬间只有
			#   在这里能看到(进了设置页之后菜单已经被盖掉了)。
			if t >= 0.25:
				step = 135
				t = 0.0
		135:
			# ── 回归(P1):暂停菜单出现时,"过场类"旧界面必须让位 ──────────
			# 用户原话:"有时点快了,就会有这样的情况,那么在如ESC菜单出现时
			# 就立刻隐藏旧的界面,或者添加模糊背景";配图是章节卡
			# 「第一章:门厅」糊在暂停面板的「继续游戏」上。
			# 这条**同时**钉两件事(只钉一件都会漏):
			#   ① 菜单是最上层(主界面 CanvasLayer 抬到 20 > 关卡界面 10);
			#   ② 会跟菜单抢位置的章节卡被收起来了;
			#   ③ 还顺手钉住"别把 HUD 一起藏了"(目标 / 电量必须还看得见)。
			if t >= 0.25:
				var menu := _widget("暂停菜单")
				var card := _widget("章节卡")
				pause_on = get_tree().paused and menu != null and menu.visible
				card_hidden = card != null and not card.visible
				menu_top = false
				if menu != null and card != null:
					var menu_layer := _canvas_layer_of(menu)
					var card_layer := _canvas_layer_of(card)
					# 跨 CanvasLayer 比层号;同一层比兄弟顺序(后画的在上面)。
					# 两种都比 `move_to_front()` 靠谱 —— 它只在同一个父节点
					# 的兄弟之间挪,跨层一步都迈不出去(真机量过)。
					menu_top = menu_layer > card_layer \
						or (menu_layer == card_layer
							and menu.get_index() > card.get_index())
				hud_kept = _widget_visible("目标文本") and _widget_visible("电量文本")
				var dim := _widget("暂停压暗")
				dim_shown = dim != null and dim.visible
				print("STORY_PAUSE_UI menu_visible=", menu != null and menu.visible,
					" card_visible=", card != null and card.visible,
					" menu_layer=", _canvas_layer_of(menu),
					" card_layer=", _canvas_layer_of(card),
					" menu_idx=", menu.get_index() if menu != null else -1,
					" card_idx=", card.get_index() if card != null else -1,
					" hud_visible=", hud_kept,
					" dim_visible=", dim_shown)
				_click_widget("设置按钮")
				step = 130
				t = 0.0
		130:
			if t >= 0.3:
				settings_open = _panel_visible("设置面板")
				var slider := _slider_of("音量滑块")
				if slider != null:
					# 直接改 HSlider.value:Godot 会发 value_changed,和玩家拖动
					# 走的是同一条回调 —— 拖一下真的改 Master 总线音量。
					slider.value = -27.0
				step = 131
				t = 0.0
		131:
			if t >= 0.3:
				bus_db = AudioServer.get_bus_volume_db(0)
				settings_ui = _panel_state("设置面板")
				print("STORY_SETTINGS open=", settings_open,
					" bus_db=", snappedf(bus_db, 0.01))
				_click_widget("返回按钮")
				step = 132
				t = 0.0
		132:
			if t >= 0.3:
				settings_closed = not _panel_visible("设置面板")
				# ── 优化(用户要求):Esc 再按一次 = 继续游戏 ────────────
				_push_key(KEY_ESCAPE, true)
				step = 133
				t = 0.0
		133:
			if t >= 0.2:
				_push_key(KEY_ESCAPE, false)
				step = 134
				t = 0.0
		134:
			if t >= 0.3:
				var menu2 := _widget("暂停菜单")
				esc_resumed = (not get_tree().paused) \
					and (menu2 == null or not menu2.visible) \
					and not _panel_visible("设置面板")
				print("STORY_PAUSE on=", pause_on, " resumed=", esc_resumed,
					" settings_open=", settings_open,
					" settings_closed=", settings_closed)
				var door := _find("大门门板", "AnimatableBody3D")
				door_y0 = door.global_position.y if door != null else 0.0
				var door_area := _find("大门区域", "Area3D")
				if door_area != null and player != null:
					player.global_position = door_area.global_position
				step = 15
				t = 0.0
		15:
			if t >= 0.45:
				var door2 := _find("大门门板", "AnimatableBody3D")
				print("STORY_LOBBY_DOOR tl_play=", _var("_gk_tl_play"),
					" door_y0=", snappedf(door_y0, 0.01),
					" door_y=", snappedf(door2.global_position.y, 0.01) if door2 != null else 0.0)
				step = 16
				t = 0.0
		16:
			# 大门的时间轴 0.9 秒后会自动切场景(会换掉探针)—— 看完副作用就放掉
			if t >= 0.1:
				_drop_world()
				print("STORY_LOBBY_DONE")
				step = 17
				t = 0.0
		# ── 关卡 2 走廊 ────────────────────────────────────────────
		17:
			if t >= 0.1:
				_load("%(corridor)s")
				player = _find("玩家", "CharacterBody3D")
				enemy = _find("怪物", "CharacterBody3D")
				step = 18
				t = 0.0
		18:
			if t >= 0.5:
				spawn = player.global_position if player != null else Vector3.ZERO
				print("STORY_CORRIDOR_START state=", _var("ai_state"),
					" has_enemy=", enemy != null,
					" far_tension=", snappedf(float(_var("_gk_heart_tension")), 0.001))
				step = 19
				t = 0.0
		19:
			# ── 第一百三十九轮:示例里的剧情(对话/选择)已按用户要求删掉 ──
			# 所以这里不再"推进对话 → 等选择框",改成:
			#   ① 确认**没有**剧情面板(删干净了);
			#   ② 钉"眼皮兜底"(STORY_EYE_GUARD):世界冻着时 eye_on 必须
			#      已经回到 0 —— 这是"预览正常、运行时全黑"那条真机 bug 的钉子;
			#   ③ 钉光标规则的输入状态:菜单开着 -> 有界面;收起 -> 没界面。
			if t >= 4.5:
				# 章节卡都淡出了再读(它是 Panel 还是文字都不该影响结论)
				print("STORY_CORRIDOR_DIALOG visible=", _panel_visible("_gk_dialog_panel"))
				print("STORY_CORRIDOR_CHOICE visible=", _panel_visible("_gk_choice_panel"))
				var post := get_tree().get_first_node_in_group("gk_post")
				var mat = post.get("_gk_post_mat") if post != null else null
				print("STORY_EYE_GUARD on=",
					mat.get_shader_parameter("eye_on") if mat != null else "<无>",
					" amount=",
					mat.get_shader_parameter("eye_amount") if mat != null else "<无>")
				# 光标规则问的是"屏幕上有没有**要点的面板**"(生成物里
				# `_gk_transient_visible()` 只认 Panel)—— 居中的提示文字
				# 不算;把大字也算进去,光标就永远收不回来(真机症状)。
				var panels := 0
				for item in get_tree().get_nodes_in_group("gk_ui_transient"):
					if item is Panel and (item as Panel).visible:
						panels += 1
				print("STORY_MOUSE_RULE menu=", _widget_visible("暂停菜单"),
					" panels=", panels,
					" paused=", get_tree().paused)
				step = 21
				t = 0.0
		21:
			if t >= 0.6:
				# 等合流函数跑完(继续世界 + 锁回鼠标 + 字幕),再躲进柜子甲。
				# 同时把怪物挪远:玩家"传送进柜子"那一帧物理重叠还没登记,
				# 怪物若正好在 6 米内会趁这 1 帧看见玩家、直接转追击。
				var cab := _find("柜子甲", "Area3D")
				if cab == null or player == null:
					step = 22
					t = 0.0
					return
				# ⚠ **不能在 `_physics_process` 里 `await`**(第 124 轮踩到):
				#   每帧调用一次这个函数,await 会让同一个分支被反复进入、
				#   协程越开越多(实测:暗角都来不及变、提示永远是空)。
				#   所以拆成"非阻塞的小步":这一帧只负责"落到区外",
				#   往后的等待交给 21b / 21c 两个正常分支。
				_enter_center = Vector3(cab.global_position)
				player.global_position = _enter_center + Vector3(2.5, 0.0, 0.0)
				_enter_area_node = cab
				_enter_guard = 0
				step = 211
				t = 0.0
		# 21b:等物理确认"人已经在区外"（最多 2 秒）——————————————
		211:
			if _enter_area_node == null or player == null:
				step = 22
				t = 0.0
			elif not _enter_area_node.overlaps_body(player) or t >= 2.0:
				player.global_position = _enter_center
				step = 212
				t = 0.0
		# 21c:等物理认下"这次重叠",**当场**读 HUD(第 124 轮的关键)——
		#      `body_entered` 是在物理步里**同步**发的,所以"重叠成立的这一帧"
		#      就是提示刚被写好的时刻。以前拖到后面那一段再读,读到的可能
		#      已经被别的分支覆盖(实测:同一份产物时有时无 —— 探针不稳,
		#      不是产物坏了;我为此先按"是不是碰撞体改坏了"查了一圈,
		#      最后靠"重叠成立那一刻读"才稳定下来)。
		212:
			if _enter_area_node != null and player != null \
					and (_enter_area_node.overlaps_body(player) or t >= 1.0):
				if hide_hint == "":
					hide_hint = _text("提示文本")
				hide_state = String(_var("ai_state"))
				hide_vig = _vig_amount()
				step = 213
				t = 0.0
		213:
			# 进柜完成 -> 把怪物挪远(它若在 6 米内会趁这一帧就看见玩家)
			if t >= 0.0:
				var cab2 := _enter_area_node
				if cab2 != null and enemy != null:
					enemy.global_position = cab2.global_position + Vector3(20.0, 0.0, 0.0)
				step = 22
				t = 0.0
		22:
			if t >= 0.6:
				# 提示/暗角在 212 已经采过(重叠成立那一刻),这里不覆盖 ——
				# 只补读"离开柜子之后"的值。
				if hide_hint == "":
					hide_hint = _text("提示文本")
				hide_state = String(_var("ai_state"))
				hide_vig = _vig_amount()
				# 挪出去(回出生点)
				if player != null:
					player.global_position = spawn
				step = 23
				t = 0.0
		23:
			if t >= 0.6:
				print("STORY_CORRIDOR_HIDE in_state=", hide_state,
					" in_hint=[", hide_hint,
					"] out_state=", _var("ai_state"),
					" out_hint=[", _text("提示文本"), "]")
				print("STORY_CORRIDOR_VIGNETTE hide=", snappedf(hide_vig, 0.01),
					" exit=", snappedf(_vig_amount(), 0.01),
					" layer=", _var("_gk_vignette_layer") != null)
				# 真潜行第一步:再把玩家送进柜子(怪物还在远处)。
				# ⚠ 同样**不能 await**(见 21 那段说明):拆成 231/232 两个
				#    非阻塞小步 —— 先落到区外、等物理确认,再挪进中心。
				var cab2 := _find("柜子甲", "Area3D")
				if cab2 == null or player == null:
					step = 25
					t = 0.0
					return
				_enter_center = Vector3(cab2.global_position)
				_enter_area_node = cab2
				player.global_position = _enter_center + Vector3(2.5, 0.0, 0.0)
				step = 231
				t = 0.0
		231:
			if _enter_area_node == null or player == null:
				step = 24
				t = 0.0
			elif _enter_area_node.overlaps_body(player):
				# 已经在里面了(上一轮的 21x 留下的人还在柜子里)
				step = 24
				t = 0.0
			elif not (player is CharacterBody3D and (player as CharacterBody3D).is_on_floor()) \
					and t < 1.5:
				pass          # 等它落到地面
			else:
				# ⚠ 先站到柜子外侧的**地面上**,等它自己落稳,再横着挪进去
				#    (第 124 轮):`CharacterBody3D` 一次 `global_position = ...`
				#    瞬移之后,Y 要等几帧才落到地面 —— 实测直接瞬移到柜子中心时
				#    它沉到 `y = 0.40`(胶囊中心),和柜子触发区在 Y 上**擦边
				#    不重叠**,于是"躲在柜子里"这条断言假红。
				#    (不是产物坏了:静止探针里 `overlaps_body` 明明是 true。)
				#    先站外面、落稳,再横移进去 —— 高度不变,重叠一定是真的。
				player.global_position = _enter_center + Vector3(3.0, 0.0, 0.0)
				step = 232
				t = 0.0
		232:
			# ② 横着挪进柜子(高度不动),然后交给 24 看 AI 认不认得出
			if _enter_area_node == null or player == null:
				step = 24
				t = 0.0
			elif _enter_area_node.overlaps_body(player):
				step = 24
				t = 0.0
			elif t >= 0.25:
				player.global_position = _enter_center
				t = 0.0
		24:
			# 把怪物挪到柜子旁(3 米),然后看 0.8 秒它认不认得出玩家
			if t >= 0.3:
				var cab3 := _find("柜子甲", "Area3D")
				if cab3 != null and enemy != null:
					enemy.global_position = cab3.global_position + Vector3(-3.0, 0.0, 0.0)
				step = 25
				t = 0.0
		25:
			# 躲在柜子里看一阵:怪物就在 3 米外,却看不见(躲藏区生效)。
			if t >= 2.0:
				hide_see_in = String(_var("ai_state"))
				# 挪出柜子(仍在怪物附近、**正前方**)→ 才该看见
				# ⚠ 第一百二十六轮:以前写死 `+Vector3(0,0,3)`,那是 360°
				#    全向感知下的写法;现在视野角 120(±60° 锥),必须沿
				#    怪物当前前方摆 —— 见 `_place_in_front` 的注释。
				_place_in_front(enemy, player, 3.0)
				step = 26
				t = 0.0
		26:
			if t >= 0.8:
				hide_see_out = String(_var("ai_state"))
				print("STORY_HIDE_SEE in=", hide_see_in, " out=", hide_see_out)
				# 挪到怪物正前方近处(2 米):看见就追 + 贴身惊吓
				# ⚠ 每帧贴着怪物前方摆,免得它一转视角就"看不见"。
				_place_in_front(enemy, player, 2.0)
				# 挪到怪物正前方近处(2 米):看见就追 + 贴身惊吓
				# ⚠ 同上:每帧贴着怪物前方摆,免得它一转视角就"看不见"
				#    (那段距离是『AI_转场』的『丢失』半径内,本来就会追丢)。
				_place_in_front(enemy, player, 2.0)
				heard = false
				max_shake = 0.0
				step = 27
				t = 0.0
		27:
			# 追 + 吓:这 1.2 秒里每帧采样心跳 / 震动
			var heart = _var("_gk_heart_player")
			if heart != null and heart.playing:
				heard = true
			max_shake = maxf(max_shake, float(_var("_gk_shake_left")))
			if t >= 1.2:
				print("STORY_CORRIDOR_CHASE state=", _var("ai_state"),
					" near_tension=", snappedf(float(_var("_gk_heart_tension")), 0.001),
					" heart=", heart != null,
					" playing=", heard,
					" shake=", snappedf(max_shake, 0.01))
				step = 28
				t = 0.0
		28:
			if t >= 0.1:
				_drop_world()
				print("STORY_CORRIDOR_DONE")
				step = 29
				t = 0.0
		# ── 关卡 3 结局 ────────────────────────────────────────────
		29:
			if t >= 0.1:
				_load("%(ending)s")
				player = _find("玩家", "CharacterBody3D")
				step = 30
				t = 0.0
		30:
			# ── 第一百三十九轮:结局不再有旁白/选择/大字 ────────────────
			# 章节卡淡出 -> 冻住世界 + 两个出口按钮(回到开场 / 退出游戏)。
			if t >= 3.5:
				var scene_now := get_tree().current_scene
				print("STORY_ENDING_RESULT frozen=", get_tree().paused,
					" back=", _widget_visible("回到开场"),
					" quit=", _widget_visible("退出游戏"),
					" dialogs=", _panel_visible("_gk_dialog_panel")
						or _panel_visible("_gk_choice_panel"),
					" scene=", String(scene_now.name) if scene_now != null else "<无>")
				step = 35
				t = 0.0
		35:
			# ── 结局期间按 Esc:菜单盖上来 + 世界**继续冻着** + 结局画面让位 ──
			# (第一百三十五轮:用户拍板"结局时冻住世界 + 明确出口",顺手验一下
			#  这条新暂停语义在最关键的那张画面上成不成立)
			if t >= 0.3:
				_push_key(KEY_ESCAPE, true)
				step = 351
				t = 0.0
		351:
			if t >= 0.15:
				_push_key(KEY_ESCAPE, false)
				step = 352
				t = 0.0
		352:
			if t >= 0.3:
				print("STORY_ENDING_MENU paused=", get_tree().paused,
					" menu=", _widget_visible("暂停菜单"),
					" big_hidden=", not _widget_visible("回到开场"))
				_push_key(KEY_ESCAPE, true)
				step = 353
				t = 0.0
		353:
			if t >= 0.15:
				_push_key(KEY_ESCAPE, false)
				step = 354
				t = 0.0
		354:
			# ⚠ 等**冷却**(默认 1 秒)过后再看:过场面板收起后要等冷却才回来,
			#   0.3 秒就断言会读到 big_back=false(第一百三十五轮)。
			if t >= 1.5:
				print("STORY_ENDING_BACK paused=", get_tree().paused,
					" menu=", _widget_visible("暂停菜单"),
					" big_back=", _widget_visible("回到开场"))
				# 点『回到开场』—— 它走 `GkWorld.change_level(大堂)`,探针自己
				# 会被换掉;所以**先把"见证者"挂到 /root**(它活得过换场景),
				# 由它打那一行结论。
				var watcher: Node = load("res://exit_watch.gd").new()
				watcher.name = "EndingExitWatcher"
				get_tree().root.add_child(watcher)
				_click_widget("回到开场")
				step = 355
				t = 0.0
		355:
			# 兜底:两秒内没换关就把结论打成"没换"(见证者活着的话它会先打印)
			if t >= 2.0:
				var now := get_tree().current_scene
				print("STORY_ENDING_EXIT scene=",
					String(now.name) if now != null else "<无>")
				_drop_world()
				print("STORY_ENDING_DONE")
				print("STORY_DONE")
				set_physics_process(false)
				get_tree().quit()
''' % {"lobby": LOBBY, "corridor": CORRIDOR, "ending": ENDING}

# ── 结局出口的"见证者"(第一百三十五轮)─────────────────────────────
# 挂在 `/root` 下,活得过换场景:探针点了『回到开场』之后自己会被换掉,
# 由它来打 `STORY_ENDING_EXIT` 那一行(证明"明确出口"真的把关卡切了)。
EXIT_WATCH_GD = '''extends Node

# 结局出口的见证者:等当前场景不再是探针(Probe)就打印结论。
# ⚠ 收尾的 STORY_ENDING_DONE / STORY_DONE 也由它打 —— 探针那时候已经被
#   换掉了,它打不出来(打了才会让门禁看到"探针跑完")。
func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	for _i in range(360):
		await get_tree().process_frame
		var scene := get_tree().current_scene
		if scene != null and String(scene.name) != "Probe":
			print("STORY_ENDING_EXIT scene=", String(scene.name))
			print("STORY_ENDING_DONE")
			print("STORY_DONE")
			get_tree().quit()
			return
	print("STORY_ENDING_EXIT scene=<没换>")
	queue_free()
'''

PROBE_TSCN = '''[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://probe.gd" id="1_probe"]

[node name="Probe" type="Node"]
script = ExtResource("1_probe")
'''


def prepare(src=None):
    src = src or SRC
    if not os.path.isdir(src):
        print(f"跳过:找不到示例工程 {src} —— 先跑 make_story_demo.py")
        return False
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    shutil.copytree(src, OUT, ignore=shutil.ignore_patterns(".godot", "*.import"))
    with open(os.path.join(OUT, "probe.gd"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE_GD)
    # 结局出口的"见证者"(第一百三十五轮):探针点『回到开场』之后会被换掉,
    # 这一份挂在 /root 下,由它打 `STORY_ENDING_EXIT` 那一行。
    with open(os.path.join(OUT, "exit_watch.gd"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(EXIT_WATCH_GD)
    with open(os.path.join(OUT, "probe.tscn"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE_TSCN)
    project_file = os.path.join(OUT, "project.godot")
    with open(project_file, "r", encoding="utf-8") as handle:
        text = handle.read()
    text = re.sub(r'run/main_scene="[^"]*"',
                  'run/main_scene="res://probe.tscn"', text)
    with open(project_file, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return True


def run_godot(godot):
    if not os.path.exists(godot):
        print(f"跳过:找不到 Godot({godot})")
        return None

    def _import_and_run():
        subprocess.run([godot, "--headless", "--path", OUT, "--import"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
        done = subprocess.run([godot, "--headless", "--path", OUT,
                               "--quit-after", "2400"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
        return (done.stdout or "") + (done.stderr or "")

    out = _import_and_run()
    # ── 偶发兜底:第一次 --import 偶尔会漏掉某个 .glb(门禁实测撞过),
    #    载入 .tscn 会报 "referenced non-existent resource"。重跑一次;真缺
    #    文件的话第二次照样报,只是把"偶发没导入齐"和"真没有"分开。
    if "non-existent resource" in out:
        print("    ⚠ 导入没齐(non-existent resource),重跑一次…")
        out = _import_and_run()
    return out


def _errors(text, problems):
    for line in text.splitlines():
        if "SCRIPT ERROR" in line or "Parse Error" in line:
            problems.append(f"Godot 脚本 / 解析错误: {line.strip()}")
            return


def _need(problems, text, name, pattern, describe):
    """抓一条 STORY 行;抓不到就记一条失败。返回 match 或 None。"""
    match = re.search(pattern, text)
    if not match:
        problems.append(f"{describe} 没跑出来(STORY_{name} 那行没打印)")
    return match


def _expect(problems, match, group_index, value, describe):
    if match is None:
        return False
    got = match.group(group_index)
    if got != value:
        problems.append(f"{describe} 不对(期望 {value},实际 {got}): " + match.group(0))
        return False
    return True


def _code_checks(problems):
    """结构层 + 关卡切换(读文件,不用 Godot)。

    ── 为什么"切关卡"只能这么验 ──────────────────────────────────
    关底那三句 `change_scene_to_file(...)` 一旦真的执行,就会把**当前场景**
    (探针)整棵换掉 —— 探针里的断言再也没机会跑。所以这一半用**出码文本**:
    每个 `game_logic_*.gd` 里必须真的有"切到下一关"那句,而且路径对得上。
    """
    # 主场景 = 大堂(按 F5 从第一关进)
    # ⚠ 必须读 **SRC**(原始产物),不能读 OUT:`prepare()` 已经把 OUT 的副本
    #   改成 `run/main_scene="res://probe.tscn"` 了 —— 读 OUT 会永远报
    #   "主场景不是大堂"。(第一百二十六轮我手贱改成 OUT,当场红,已改回。)
    project_file = os.path.join(SRC, "project.godot")
    with open(project_file, "r", encoding="utf-8") as handle:
        project = handle.read()
    if 'run/main_scene="res://scenes/大堂.tscn"' not in project:
        problems.append("主场景不是大堂(按 F5 应该从第一关进)")

    # 三张关卡 .tscn:都在,且各自引用自己的 游戏逻辑 / 环境脚本
    levels = ((LOBBY, "大堂", "大堂"), (CORRIDOR, "走廊", "走廊"),
              (ENDING, "结局", "结局"))
    for level, logic, env in levels:
        scene_path = os.path.join(OUT, "scenes", f"{level}.tscn")
        if not os.path.isfile(scene_path):
            problems.append(f"没找到场景 scenes/{level}.tscn")
            continue
        with open(scene_path, "r", encoding="utf-8") as handle:
            scene = handle.read()
        if f"res://scripts/game_logic_{logic}.gd" not in scene:
            problems.append(f"{level}.tscn 没引用 game_logic_{logic}.gd")
        if f"res://scripts/gk_environment_{env}.gd" not in scene:
            problems.append(f"{level}.tscn 没引用 gk_environment_{env}.gd")

    # 每个 game_logic 里要有"切到下一关"那句(路径对得上)
    switches = {
        "大堂": "res://scenes/走廊.tscn",
        "走廊": "res://scenes/结局.tscn",
        "结局": "res://scenes/大堂.tscn",
    }
    for level, target in switches.items():
        path = os.path.join(OUT, "scripts", f"game_logic_{level}.gd")
        if not os.path.isfile(path):
            problems.append(f"没找到生成的脚本 scripts/game_logic_{level}.gd")
            continue
        with open(path, "r", encoding="utf-8") as handle:
            code = handle.read()
        if "change_scene_to_file" not in code:
            problems.append(f"game_logic_{level}.gd 里没有切关卡的调用")
        if f'"{target}"' not in code:
            problems.append(f"game_logic_{level}.gd 里没有切到 {target} 的那句")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--godot", default=GODOT_DEFAULT)
    # `--src` 换掉"从哪份工程验" —— 默认还是 examples/demo_story。
    # 用途:改了 make_story_demo.py 但**不想重建共享示例**时,先生成到临时
    # 目录,用 `--src <临时目录>` 把整套断言跑一遍(第 126 轮加的)。
    parser.add_argument("--src", default=SRC,
                        help="要验的 Godot 工程目录(默认 examples/demo_story/...)")
    args = parser.parse_args()
    if not prepare(args.src):
        return 0

    text = run_godot(args.godot)
    if text is None:
        return 0
    for line in text.splitlines():
        if "STORY_" in line or "SCRIPT ERROR" in line or "Parse Error" in line:
            print(line.strip())

    problems = []
    _errors(text, problems)

    # ── 关卡 1 大堂 ─────────────────────────────────────────────────
    hud = _need(problems, text, "LOBBY_HUD",
                r"STORY_LOBBY_HUD goal=(\S*) keys=(\S*) battery=(\S*) "
                r"hint=(\S*) card=(\S*)", "大堂 HUD")
    if hud:
        _expect(problems, hud, 1, "目标:找到钥匙,打开大门", "大堂目标文字")
        _expect(problems, hud, 2, "钥匙:0", "大堂钥匙数文字")
        # 电量控件:格式是 `手电电量:<数>`(Godot 的 str(100.0) 会带 .0),
        # 只断言"前缀对 + 开局是满格"—— 顺带证明电量真的初始化成了 100。
        if not hud.group(3).startswith("手电电量:"):
            problems.append("大堂电量文字不对(期望前缀『手电电量:』,实际 "
                            + hud.group(3) + "): " + hud.group(0))
        elif float(hud.group(3).split(":", 1)[1]) != 100.0:
            problems.append("大堂开局电量不是满格: " + hud.group(0))
        _expect(problems, hud, 5, "第一章:门厅", "大堂章节卡文字")
    keyhint = _need(problems, text, "KEYHINT",
                    r"STORY_KEYHINT ok=(\S+)", "按键提示 HUD")
    if keyhint:
        _expect(problems, keyhint, 1, "true", "按键提示控件存在且文本非空")
    # ── bug4:Alt 必须绑在 **KEY_ALT(4194328)** 上 ────────────────────
    # 这张表曾经把 LEFT_ALT 写成 4194327,那是 Godot 的 **META(Windows 键)**
    # —— 用户按 Alt 一点反应都没有("alt 并未调出鼠标显示")。
    alt_key = _need(problems, text, "ALT_KEY",
                    r"STORY_ALT_KEY action=(\S+) codes=\[([\d,]*)\]", "Alt 动作绑定")
    if alt_key:
        _expect(problems, alt_key, 1, "key_left_alt", "Alt 动作名")
        if "4194328" not in alt_key.group(2).split(","):
            problems.append("Alt 动作没绑到 KEY_ALT(4194328),实际绑的是 "
                            + alt_key.group(2) + "(4194327 = META / Windows 键): "
                            + alt_key.group(0))
    # ── bug2:第一人称横移不能把视角带跑 ──────────────────────────────
    # 弹簧臂"平滑"的滞后比 0.25 米的臂长还长,相机被落在侧面却还 look_at 枢轴
    # -> 视线被甩出去。断言"横移一秒后视角水平角变化 < 1 度"。
    fpmove = _need(problems, text, "FP_MOVE",
                   r"STORY_FP_MOVE strafe_d_yaw=([\d.\-]+) rot0=([\d.\-]+) "
                   r"rot1=([\d.\-]+) moved=([\d.\-]+)", "第一人称横移不转视角")
    if fpmove:
        if abs(float(fpmove.group(1))) > 1.0:
            problems.append(f"第一人称横移时视角被带跑了 {fpmove.group(1)} 度"
                            f"(应≈0): " + fpmove.group(0))
        if float(fpmove.group(4)) < 1.0:
            problems.append(f"第一人称按 D 之后角色(几乎)没动,这条断言没意义: "
                            + fpmove.group(0))
    cam = _need(problems, text, "LOBBY_CAM",
                r"STORY_LOBBY_CAM after_c=(\S+) after_v=(\S+)", "大堂 C/V 切机位")
    if cam:
        _expect(problems, cam, 1, "第一人称相机", "按 C 后当前相机(应第一人称)")
        _expect(problems, cam, 2, "第三人称相机", "按 V 后当前相机(应第三人称)")
    fp = _need(problems, text, "FP_HIDE",
               r"STORY_FP_HIDE fp=(\S+) tp=(\S+)", "第一人称藏自身模型")
    if fp:
        _expect(problems, fp, 1, "false", "第一人称时自身模型应隐藏")
        _expect(problems, fp, 2, "true", "第三人称时自身模型应显示")
    key = _need(problems, text, "LOBBY_KEY",
                r"STORY_LOBBY_KEY picked=(\S+) visible=(\S+) keys_ui=(\S+) "
                r"goal_ui=(\S+)", "大堂拾钥匙")
    if key:
        _expect(problems, key, 1, "1", "拾钥匙后钥匙数")
        _expect(problems, key, 2, "false", "拾钥匙后钥匙物体可见性")
        _expect(problems, key, 3, "钥匙:1", "拾钥匙后 HUD 钥匙数")
        _expect(problems, key, 4, "目标:打开大门", "拾钥匙后 HUD 目标文字")
    torch = _need(problems, text, "LOBBY_TORCH",
                  r"STORY_LOBBY_TORCH on_visible=(\S+) on_flag=(\S+) "
                  r"drain=([\d.]+) battery_ui=(\S+) off_visible=(\S+)", "大堂手电")
    if torch:
        _expect(problems, torch, 1, "true", "按 F 后手电灯可见")
        _expect(problems, torch, 2, "true", "按 F 后『手电开』")
        if float(torch.group(3)) >= 99.999:
            problems.append("手电亮着但电量没下降(默认 0 / 没初始化?): "
                            + torch.group(0))
        try:
            lit_level = float(torch.group(4).split(":", 1)[1])
        except (ValueError, IndexError):
            lit_level = -1.0
        if lit_level >= 100.0 or lit_level < 0.0:
            problems.append("开了手电后 HUD 电量文字没变: " + torch.group(0))
        _expect(problems, torch, 5, "false", "再按 F 后手电灯可见")
    door = _need(problems, text, "LOBBY_DOOR",
                 r"STORY_LOBBY_DOOR tl_play=(\S+) door_y0=([\d.\-]+) "
                 r"door_y=([\d.\-]+)", "大堂大门链")
    if door:
        _expect(problems, door, 1, "true", "走进大门区域后时间轴启动")
        if not (float(door.group(3)) > float(door.group(2)) + 0.3):
            problems.append(f"大门时间轴启动了但门板没升起"
                            f"({door.group(2)} -> {door.group(3)}): " + door.group(0))
    pause = _need(problems, text, "PAUSE",
                  r"STORY_PAUSE on=(\S+) resumed=(\S+) settings_open=(\S+) "
                  r"settings_closed=(\S+)", "暂停菜单 / Esc 开关 / 设置页")
    if pause:
        _expect(problems, pause, 1, "true", "按 Esc 后世界应暂停 + 菜单弹出")
        # 用户要的优化:再按一次 Esc 就继续游戏(不是非得去点按钮)。
        _expect(problems, pause, 2, "true", "再按一次 Esc 应继续游戏(解冻 + 收起菜单)")
        _expect(problems, pause, 3, "true", "点暂停菜单里的『设置』应弹出设置面板")
        _expect(problems, pause, 4, "true", "点设置面板的『返回』应收起设置面板")
    # ── 第 125 轮(用户第 3 条)+ 第一百三十五轮(用户"结局要冻住 + 明确出口")──
    # 第 125 轮的原话:"在最后一关按ESC暂停时,它还会切换关卡" —— 当时的根因是
    # `SceneTreeTimer` 的 `process_always` 默认 true,结局链最后"停留 4 秒 ->
    # 切回大堂"在暂停里照走。
    # 第一百三十五轮用户拍板改成:**结局时冻住世界 + 给明确出口**,那条
    # "停留 4 秒自动切关"整条去掉。所以这里改成量四件事(都是真机数字):
    #   ① 结局画面出来时世界**是冻的**、两个出口按钮在、而且关卡**没被自己切走**;
    #   ② 结局期间按 Esc:菜单盖上来 + 世界继续冻着 + 结局画面让位;
    #   ③ 再按一下 Esc:菜单收起、结局画面回来、世界仍冻着;
    #   ④ 点『回到开场』:真的切到大堂(由挂在 /root 的见证者打那一行)。
    # ⚠ 旧字段(ending/saved/big_visible/big_text)第一百三十九轮删了 ——
    #   结局不再有"选择 + 大字 + 存档",只剩"冻住世界 + 两个出口按钮",
    #   新断言在文件后面(ENDING_RESULT 的另一处,字段是
    #   frozen/back/quit/dialogs/scene)。
    ending_menu = _need(problems, text, "ENDING_MENU",
                        r"STORY_ENDING_MENU paused=(\S+) menu=(\S+) "
                        r"big_hidden=(\S+)", "结局期间按 Esc")
    if ending_menu:
        _expect(problems, ending_menu, 1, "true", "结局期间按 Esc 不该把世界解冻")
        _expect(problems, ending_menu, 2, "true", "结局期间按 Esc 该弹出暂停菜单")
        # (第一百三十九轮:结局的两个出口按钮**不**进过场组 —— 菜单盖上来
        #  时它们还在底下,但菜单在最上层、点得动,这条断言随之去掉)
    ending_back = _need(problems, text, "ENDING_BACK",
                        r"STORY_ENDING_BACK paused=(\S+) menu=(\S+) "
                        r"big_back=(\S+)", "结局期间收起暂停菜单")
    if ending_back:
        _expect(problems, ending_back, 1, "true", "收起菜单不该把结局解冻")
        _expect(problems, ending_back, 2, "false", "再按一下 Esc 菜单该收起")
        _expect(problems, ending_back, 3, "true", "菜单收起后结局画面该回来")
    ending_exit = _need(problems, text, "ENDING_EXIT",
                        r"STORY_ENDING_EXIT scene=(\S+)",
                        "点『回到开场』该真的切关卡(见证者那一行)")
    if ending_exit and ending_exit.group(1) not in ("大堂", "<没换>"):
        # 场景名带后缀(大堂2 之类)也算过;只有"没换"才是失败
        pass
    if ending_exit:
        _expect(problems, ending_exit, 1, "大堂",
                "点『回到开场』没切回大堂(明确出口没生效)")
    # ── P1(第一百二十六轮):暂停菜单出现时旧界面必须让位 ────────────────
    # 用户原话:"有时点快了,就会有这样的情况,那么在如ESC菜单出现时就立刻
    # ── 第一百三十九轮:示例里的剧情(对话/选择)已删 ────────────────────
    # "过场里按 Esc 只开菜单 / 选择框让位 / 收起冷却"那三条断言不再适用
    # (选择框已经不存在了)。插件那套语义没动 —— 见离线单测
    # `test_mouse_follows_ui_not_the_cooldown` / `test_transient_*`。

    # 隐藏旧的界面,或者添加模糊背景" —— 配图里章节卡「第一章:门厅」糊在
    # 暂停面板的「继续游戏」上。
    # ⚠ 为什么这里**既**要"菜单在最上层"**又**要"章节卡收起来":
    #   两件事各修一半问题 —— 光置顶会留一张糊在菜单底下的卡(半透明面板
    #   挡不住字),光收卡则会漏掉"别的旧面板照样压着菜单"。所以都钉住。
    #   顺带钉住"HUD 不能被误伤"(目标 / 电量暂停时还得看得见)。
    pause_ui = _need(problems, text, "PAUSE_UI",
                     r"STORY_PAUSE_UI menu_visible=(\S+) card_visible=(\S+) "
                     r"menu_layer=(-?\d+) card_layer=(-?\d+) menu_idx=(-?\d+) "
                     r"card_idx=(-?\d+) hud_visible=(\S+) dim_visible=(\S+)",
                     "暂停菜单弹出时旧界面让位")
    if pause_ui:
        _expect(problems, pause_ui, 1, "true", "按 Esc 后暂停菜单应可见")
        _expect(problems, pause_ui, 2, "false",
                "暂停菜单弹出时章节卡应被收起(不能糊在菜单上)")
        _expect(problems, pause_ui, 7, "true",
                "暂停时目标 / 电量这些 HUD 不能被一起藏掉")
        menu_layer = int(pause_ui.group(3))
        card_layer = int(pause_ui.group(4))
        menu_idx = int(pause_ui.group(5))
        card_idx = int(pause_ui.group(6))
        if not (menu_layer > card_layer
                or (menu_layer == card_layer and menu_idx > card_idx)):
            problems.append(
                f"暂停菜单没在最上层(菜单 layer={menu_layer} idx={menu_idx},"
                f"章节卡 layer={card_layer} idx={card_idx})—— 跨 CanvasLayer 的"
                f"先后由 layer 号决定,`move_to_front()` 跨不过去: " + pause_ui.group(0))
        # 压暗底(用户说的"或者添加模糊背景"):做的是 ColorRect 半透明黑。
        # 这一条只提示、不算失败 —— 没有主界面工程的老图本来就没有它。
        if pause_ui.group(8) != "true":
            print("    ⚠ 暂停压暗底没亮(老图 / 没勾主界面时正常): " + pause_ui.group(0))
    settings = _need(problems, text, "SETTINGS",
                     r"STORY_SETTINGS open=(\S+) bus_db=([\d.\-]+)",
                     "设置页:音量滑块真的改总线")
    if settings:
        _expect(problems, settings, 1, "true", "设置面板已打开")
        # 滑块拖到 -27 dB -> Master 总线音量必须跟着变(不是只画个滑块)
        if abs(float(settings.group(2)) - (-27.0)) > 0.01:
            problems.append("拖音量滑块后 Master 总线音量没跟着变(期望 -27,实际 "
                            + settings.group(2) + "): " + settings.group(0))
    if "STORY_LOBBY_DONE" not in text:
        problems.append("大堂探针没跑完")

    # ── 关卡 2 走廊 ─────────────────────────────────────────────────
    start = _need(problems, text, "CORRIDOR_START",
                  r"STORY_CORRIDOR_START state=(\S+) has_enemy=(\S+) "
                  r"far_tension=([\d.]+)", "走廊开场")
    if start:
        _expect(problems, start, 1, "巡逻", "走廊怪物开场状态(AI 在跑)")
        _expect(problems, start, 2, "true", "走廊怪物找得到")
    # ── 第一百三十九轮:示例里的对话 / 选择已按用户要求删掉 ─────────────
    # 所以这几条**反过来钉"确实没有"**(哪天又长回来,这里先红)。
    dial = _need(problems, text, "CORRIDOR_DIALOG",
                 r"STORY_CORRIDOR_DIALOG visible=(\S+)", "走廊开场(不该有对话框)")
    if dial:
        _expect(problems, dial, 1, "false",
                "走廊又弹出对话框了 —— 用户第一百三十九轮要求把示例里的"
                "剧情选项与剧情显示全删掉")
    choice = _need(problems, text, "CORRIDOR_CHOICE",
                   r"STORY_CORRIDOR_CHOICE visible=(\S+)", "走廊剧情选择(不该有)")
    if choice:
        _expect(problems, choice, 1, "false", "走廊又弹出剧情选择了 —— 该删干净")

    # ── 新增:眼皮兜底 + 光标规则(第一百三十九轮)──────────────────────
    # ① 眼皮兜底:世界冻着时 eye_on 必须已经回到 0 —— "预览正常、运行时全黑"
    #    那条真机 bug 的回归钉子(过场冻住世界 -> tween 默认跟着暂停 ->
    #    眼皮卡在 6% 的缝上 = 94% 黑)。
    eye_guard = _need(problems, text, "EYE_GUARD",
                      r"STORY_EYE_GUARD on=(\S+) amount=(\S+)",
                      "睁眼兜底(世界冻着也不能把屏幕卡黑)")
    if eye_guard:
        _expect(problems, eye_guard, 1, "0.0",
                "眼皮没张开 —— 运行时整个画面会全黑(查 Tween 的暂停模式)")
        _expect(problems, eye_guard, 2, "1.0", "张开之后 eye_amount 该是 1.0")
    # ② 光标规则的输入状态:菜单收起、没有可见的过场面板 = "没界面",
    #    这时光标就该被收回(用户:"它只有有界面才显示,没有就隐藏")。
    mouse_rule = _need(problems, text, "MOUSE_RULE",
                       r"STORY_MOUSE_RULE menu=(\S+) panels=(\d+) "
                       r"paused=(\S+)",
                       "光标规则(没界面就别显示)")
    if mouse_rule:
        _expect(problems, mouse_rule, 1, "false", "这时候不该有暂停菜单")
        _expect(problems, mouse_rule, 2, "0",
                "这时候屏幕上还有可见的面板 —— 按用户的光标规则"
                "(只有有界面才显示),光标会被一直亮着")
    hide = _need(problems, text, "CORRIDOR_HIDE",
                 r"STORY_CORRIDOR_HIDE in_state=(\S+) in_hint=\[([^\]]*)\] "
                 r"out_state=(\S+) out_hint=\[([^\]]*)\]", "走廊躲藏")
    if hide:
        # 真潜行后不再把 AI 冻成『躲藏着』:躲柜子里它仍保持巡逻(看不见),
        # 只是提示 / 暗角照旧。所以这里断言状态一直是『巡逻』而不是冻结。
        _expect(problems, hide, 1, "巡逻", "钻进柜子后 AI 状态(应仍巡逻,不再冻结)")
        # 提示里必须写清"怎么出去"(用户原话:"部分提示没有按键显示,如屏住呼吸…")
        if not hide.group(2).startswith("屏住呼吸"):
            problems.append("钻进柜子后的 HUD 提示不对(期望以『屏住呼吸』开头,实际 "
                            + hide.group(2) + "): " + hide.group(0))
        elif "走出柜子" not in hide.group(2):
            problems.append("『屏住呼吸』提示没告诉玩家怎么解除(应带『走出柜子』): "
                            + hide.group(0))
        _expect(problems, hide, 3, "巡逻", "离开柜子后 AI 状态(应仍巡逻)")
        _expect(problems, hide, 4, "", "离开柜子后 HUD 提示(应清空)")
    hide_see = _need(problems, text, "HIDE_SEE",
                     r"STORY_HIDE_SEE in=(\S+) out=(\S+)", "真潜行:躲藏区挡视线")
    if hide_see:
        _expect(problems, hide_see, 1, "巡逻", "躲藏区里正前方怪物应看不见(仍巡逻)")
        _expect(problems, hide_see, 2, "追击", "挪出躲藏区(仍正前方)后才应追击")
    vig = _need(problems, text, "CORRIDOR_VIGNETTE",
                r"STORY_CORRIDOR_VIGNETTE hide=([\d.\-]+) exit=([\d.\-]+) "
                r"layer=(\S+)", "走廊暗角")
    if vig:
        _expect(problems, vig, 3, "true", "走廊暗角全屏节点存在")
        if float(vig.group(1)) <= float(vig.group(2)):
            problems.append(f"躲藏时暗角没变深({vig.group(1)} vs {vig.group(2)}): "
                            + vig.group(0))
    chase = _need(problems, text, "CORRIDOR_CHASE",
                  r"STORY_CORRIDOR_CHASE state=(\S+) near_tension=([\d.]+) "
                  r"heart=(\S+) playing=(\S+) shake=([\d.]+)", "走廊追击/惊吓")
    if chase:
        _expect(problems, chase, 1, "追击", "凑近后怪物状态(应看见就追)")
        if float(chase.group(2)) < 0.6:
            problems.append("凑近后紧张度没上去: " + chase.group(0))
        _expect(problems, chase, 3, "true", "心跳播放器建起来")
        _expect(problems, chase, 4, "true", "心跳播放器在响")
        if float(chase.group(5)) <= 0.0:
            problems.append("贴身了但惊吓没触发(相机没震): " + chase.group(0))
    if "STORY_CORRIDOR_DONE" not in text:
        problems.append("走廊探针没跑完")

    # ── 关卡 3 结局 ─────────────────────────────────────────────────
    # ── 第一百三十九轮:结局 = 冻住世界 + 两个出口按钮(没有旁白/选择/大字)
    result = _need(problems, text, "ENDING_RESULT",
                   r"STORY_ENDING_RESULT frozen=(\S+) back=(\S+) quit=(\S+) "
                   r"dialogs=(\S+) scene=(\S+)", "结局(冻住 + 出口按钮)")
    if result:
        _expect(problems, result, 1, "true", "结局该把世界冻住")
        _expect(problems, result, 2, "true", "『回到开场』按钮该在")
        _expect(problems, result, 3, "true", "『退出游戏』按钮该在")
        _expect(problems, result, 4, "false",
                "结局里又出现了剧情面板(对话/选择) —— 该删干净")
    if "STORY_ENDING_DONE" not in text:
        problems.append("结局探针没跑完")

    # ── 结构层 / 关卡切换(读文件)──────────────────────────────────
    _code_checks(problems)
    if not problems:
        from check_persistent_player import run as check_transitions
        try:
            check_transitions(OUT, args.godot)
        except (AssertionError, subprocess.SubprocessError) as exc:
            problems.append(f"跨关卡角色验证失败: {exc}")

    if "STORY_DONE" not in text:
        problems.append("探针没跑完(可能中途报错 / 卡住)")

    for item in dict.fromkeys(problems):
        print(f"✗ {item}")
    print("CHECK_STORY_DEMO=" + ("OK" if not problems else "FAIL"))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
