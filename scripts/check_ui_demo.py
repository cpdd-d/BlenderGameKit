"""界面示例的自动检查(第三十七轮:UI 设计)。

用法:
    python scripts\\check_ui_demo.py
    python scripts\\check_ui_demo.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

它会:
  1. 把 `examples/demo_ui/界面示例_godot` 拷到 `_dist/ui_probe`(**不碰示例本身**);
  2. 在副本里加一个探针场景(`probe.gd` + `probe.tscn`),把主场景指过去;
  3. 用 Godot 无头跑,读探针打印的 UI_* 行,汇总 PASS / FAIL。

探针查的是"界面该有的现象"(不是代码长什么样):
  * **控件都建出来了**:HUD 底 / 徽标 / 分数 / 血条 / 底部提示 /
    暂停菜单 / 暂停标题 / 继续按钮 / 退出按钮,一个不少;
  * **图片真的加载了**:徽标的 texture 不为空、尺寸 128×128
    (证明 Blender 那张程序生成的图落盘成了 art/ui_*.png);
  * **父控件生效**:分数挂在 HUD 底下、菜单按钮挂在暂停菜单下
    (底板一藏整块跟着藏);
  * **对齐生效**:底部提示的位置 = "底边居中 + 边距 28"
    (按视口尺寸现算,窗口变了它也变);暂停菜单大致居中;
  * **界面_设置生效**:血条的 modulate.a = 0.6(运行时改透明度);
  * **对话框**:开场是逐字中(visible_characters < 全长),点一下补完、
    再点一下关掉,并且**关掉之后才**改"得分"文字(执行流真的停过);
  * **暂停菜单**:Esc -> 世界冻住 + 菜单弹出;点『继续游戏』-> 解冻 +
    菜单收回去(世界冻着的时候按钮照样点得动)。

第二版(第三十八轮)多看五样:
  * **列表**:暂停菜单里的『存档列表』是 ScrollContainer + VBox 两层,
    两个存档槽**挂在里层容器里自动从上往下排**(没填 X / Y);
    点『存档一』-> "得分"变成"读取存档一"(能点的行各接各的链);
  * **列表_填充**:左下『日志列表』正好三行,文字和图上写的一致
    (每行一个 Label,先清空再建);
  * **九宫格**:菜单顶上『标题条』是 NinePatchRect,四边切片 = 14、
    贴图 64×64,而且是菜单的**第一个子节点**(『垫底』没盖住标题);
  * **缓动 + 焦点**:对话结束后昵称框淡入(透明度 0.15 -> 1.0),
    并且**焦点在它身上**(键盘直接能打字);
  * **输入框**:给框里打"阿飞" + 回车 -> 『界面_取文字』读出来 -> "得分"
    变成"阿飞"(整条 取文字 -> 改文字 的链路)。

第四十轮(界面设计图,套用式)再看两样:
  * **界面_套用设计**:右上角 HUD 的三块(底板 / 标题 / 按钮)**不在玩法图里**,
    是设计图 `设计_HUD` 摆好后由这一个节点建出来的 —— 探针查三个控件都在、
    后两个挂在底板上、底板的位置 = "屏幕右上角 + 边距 14"(按视口现算);
  * **界面_按钮点击**:点『点我』-> "得分"变成"设计按钮被点了"
    (设计图只管长什么样,行为写在玩法图、按控件名接)。

第五十轮(剧情分支,『对话_选择』)再看两样:
  * **选择框弹出来**:整条界面链的尾巴是『对话_选择』—— 问题文字对、
    三个选项按钮都在(选择1 / 选择2 / 选择3);
  * **选了走哪条链**:点第 2 个 -> 面板收起 + 得分变成"结局:独自逃出"
    + 『结局』变量 = 2(『选了第几个』这个整数输出真的写回去了)。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "examples", "demo_ui", "界面示例_godot")
OUT = os.path.join(ROOT, "_dist", "ui_probe")

PROBE_GD = '''extends Node

# 界面示例的探针:把界面的每一条能力都**看一遍**。
# 结论行统一用 UI_ 前缀,Python 那边按前缀收集、按内容断言。
#
# ⚠ 探针自己设 PROCESS_MODE_ALWAYS:暂停菜单会把世界冻住,探针跟着停
#   就没人做检查了(这也是"暂停后按钮还能点"能被观察到的前提)。

var world: Node = null
var ui_root: Node = null
var elapsed := 0.0
var phase := 0
var typing_first := 0          # 点击之前,对话框显示到第几个字
var typing_after := 0          # 第一次点击之后(应该"补完")
var dialog_hidden := false
var score_after := ""
var paused_before := false

func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	# ⚠ 无头模式的默认视口只有 64×64:暂停菜单(440×340)会摆到屏幕外面,
	#   菜单里按钮的点击坐标落在视口之外 —— push_input 打不中(实测:
	#   点了『继续游戏』游戏没解冻)。先把窗口调成正常分辨率**再**实例化
	#   世界,界面就按这块屏幕摆。
	get_window().size = Vector2i(1152, 648)
	world = load("res://scenes/World.tscn").instantiate()
	add_child(world)
	var ui := world.find_child("GKUI", true, false)
	if ui != null:
		ui_root = ui.find_child("Root", true, false)


func _widget(name: String) -> Control:
	if ui_root == null:
		return null
	var found := ui_root.find_child(name, true, false)
	return found as Control


## 第四十六轮:开局"选单态" —— 世界暂停 + 鼠标可见
## (世界停着,移动 / 鼠标转视角全都不跑;CAPTURED=2 / VISIBLE=0)
func _check_startup() -> void:
	print("UI_STARTUP paused=", get_tree().paused, " mouse=", Input.mouse_mode)


## 对话点完 = "选择完成":继续世界 + 鼠标锁回去
func _check_startup_resume() -> void:
	print("UI_STARTUP_RESUME paused=", get_tree().paused,
		" mouse=", Input.mouse_mode)


func _physics_process(delta: float) -> void:
	elapsed += delta
	if phase == 0 and elapsed >= 0.35:
		phase = 1
		_check_widgets()
		_check_startup()
		_start_dialog_sample()
	elif phase == 1 and elapsed >= 0.7:
		phase = 2
		# 第一次点:补完当前这句。
		# ⚠ 不能点完**当场**看结果:dialog 的等待循环在 `_ready` 那条链上
		#   等 `process_frame`,点击只是把标记置真,补完发生在**下一个帧**。
		_click_at(Vector2(8.0, 8.0))
	elif phase == 2 and elapsed >= 1.1:
		phase = 3
		typing_after = _dialog_chars()        # 补完了没有(-1 = 全部显示)
		_click_at(Vector2(8.0, 8.0))          # 第二次点:关掉对话框
	elif phase == 3 and elapsed >= 1.5:
		phase = 4
		_check_dialog_done()
		_check_startup_resume()
		# 对话结束后的那两步:输入框淡入 + 焦点交给它(都在 1.4 秒前跑完)
		_check_focus_and_tween()
		_send_key(KEY_ESCAPE, true)           # 按住 Esc(热键是每帧轮询的)
	elif phase == 4 and elapsed >= 1.9:
		phase = 5
		_send_key(KEY_ESCAPE, false)
		_check_pause()
		_check_menu_list()                     # 菜单开着,正好看列表 / 九宫格
	elif phase == 5 and elapsed >= 2.15:
		phase = 6
		# 暂停中点击列表里的『存档一』(能点的行各接各的链)
		var slot := _widget("存档槽一")
		if slot != null:
			_click_at(slot.get_global_rect().get_center())
	elif phase == 6 and elapsed >= 2.45:
		phase = 7
		_check_slot_click()
		paused_before = get_tree().paused
		var button := _widget("继续按钮")
		if button != null:
			_click_at(button.get_global_rect().get_center())
	elif phase == 7 and elapsed >= 2.9:
		phase = 8
		_check_resume()
		_check_log_list()                      # 『列表_填充』灌的三行
		_submit_nickname()                     # 打字 + 回车,交给下一个阶段看
	elif phase == 8 and elapsed >= 3.4:
		phase = 9
		_check_submitted()
		_design_click()                        # 点设计图里那个按钮(套用式)
	elif phase == 9 and elapsed >= 3.8:
		phase = 10
		_check_design_click()
		# 第五十轮:整条界面链的尾巴是『对话_选择』—— 这时候它应该弹着
		_check_choice_shown()
		var option := _widget("选择2")
		if option != null:
			_click_at(option.get_global_rect().get_center())
	elif phase == 10 and elapsed >= 4.2:
		phase = 11
		_check_choice_picked()
		# 第六十四轮:字幕(不阻塞)+ 对话历史回顾
		_check_narrative()
		# 历史面板是"阻塞"的(等玩家点)—— 点一下关掉,下一阶段查它真关了。
		# 面板本身不吃鼠标(`_input` 采点按),点哪儿都算。
		_click_at(Vector2(400.0, 300.0))
	elif phase == 11 and elapsed >= 4.6:
		phase = 12
		_check_history_closed()
		print("UI_DONE")
		set_physics_process(false)
		# 查完就自己退:无头模式一帧花多少**真实**时间是机器相关的,
		# 靠 `--quit-after` 卡帧数容易"还没跑到最后一条就被掐了"(实测)。
		# --quit-after 留着当安全网(卡死时兜底)。
		get_tree().quit()


## 第六十四轮:字幕 **不阻塞** + 对话历史自动记录 + 打字机音效。
##
## 证据链(三条都是"行为"而不是"代码长什么样"):
##   ① `分数` 已经是『结局:独自逃出』—— 那个节点**排在字幕后面**。
##      字幕要是像对话框那样等玩家点,这句话永远执行不到 —— 这就是
##      "字幕不阻塞执行流"的活证据;
##   ② 『对话_历史』面板弹出来了,里面正好 **2 行**(开场白 + 旁白字幕);
##   ③ 宿主身上多了一个叫 `GKBlip` 的播放器 —— 打字音真的响了
##      (它只在"配了打字音 + 音效文件真的存在"时才被创建)。
func _check_narrative() -> void:
	var score := _widget("分数") as Label
	var score_text := score.text if score != null else "<none>"
	var panel := _widget("对话历史")
	var shown: bool = panel != null and panel.visible
	var rows := 0
	var first := ""
	if panel != null:
		var list := panel.find_child("列表", true, false)
		if list != null:
			rows = list.get_child_count()
			if rows > 0 and list.get_child(0) is Label:
				first = (list.get_child(0) as Label).text
	var blip := world.find_child("GKBlip", true, false)
	var ok: bool = shown and rows == 2 and blip != null \
		and score_text == "结局:独自逃出"
	print("UI_NARRATIVE ok=", ok, " panel=", shown, " rows=", rows,
		" first=", first, " blip=", blip != null, " score=", score_text)


## 点一下之后历史面板要关掉(和对话框同一套:等一次点按)。
func _check_history_closed() -> void:
	var panel := _widget("对话历史")
	var hidden: bool = panel == null or not panel.visible
	print("UI_HISTORY_CLOSED ok=", hidden, " hidden=", hidden)


func _check_widgets() -> void:
	var names := ["HUD底", "徽标", "分数", "血条", "底部提示",
		"暂停菜单", "暂停标题", "继续按钮", "退出按钮",
		"标题条", "存档列表", "存档槽一", "存档槽二", "日志列表", "昵称",
		"设计底板", "设计标题", "设计按钮"]
	var missing: Array = []
	for name in names:
		if _widget(name) == null:
			missing.append(name)
	print("UI_WIDGETS ok=", missing.is_empty(), " missing=", missing)

	# 图片:texture 非空 + 尺寸对(证明 art/ui_*.png 真的导出来了)
	var logo := _widget("徽标") as TextureRect
	var tex_ok := false
	var tex_size := Vector2.ZERO
	if logo != null and logo.texture != null:
		tex_size = logo.texture.get_size()
		tex_ok = int(tex_size.x) == 128 and int(tex_size.y) == 128
	print("UI_IMAGE ok=", tex_ok, " size=", tex_size,
		" shown=", logo != null and logo.size.x > 0.0)

	# 父控件:分数 / 按钮挂在底板下(底板隐藏时整块跟着藏)
	var score := _widget("分数")
	var resume := _widget("继续按钮")
	var parent_ok := score != null and score.get_parent() == _widget("HUD底") \\
		and resume != null and resume.get_parent() == _widget("暂停菜单")
	print("UI_PARENT ok=", parent_ok,
		" score_parent=", score.get_parent().name if score != null and score.get_parent() != null else "<none>",
		" btn_parent=", resume.get_parent().name if resume != null and resume.get_parent() != null else "<none>")

	# 对齐:底部提示 = 底边居中 + 边距 28(按**当前视口尺寸**现算)
	var view := get_viewport().get_visible_rect().size
	var hint := _widget("底部提示")
	var align_ok := false
	var align_note := "no widget"
	if hint != null:
		var size := hint.get_combined_minimum_size()
		var want := Vector2((view.x - size.x) / 2.0, view.y - size.y - 28.0)
		var got := hint.position
		align_ok = absf(got.x - want.x) <= 2.0 and absf(got.y - want.y) <= 2.0
		align_note = "want=" + str(want) + " got=" + str(got) + " view=" + str(view)
	print("UI_ALIGN ok=", align_ok, " ", align_note)

	# 暂停菜单:开局必须是**藏着的**(勾了『开局藏』)
	var menu := _widget("暂停菜单")
	var hidden_at_start: bool = menu != null and not menu.visible
	print("UI_MENU_HIDDEN ok=", hidden_at_start)

	# 对齐第二条:暂停菜单大致居中(居中预设 + 边距可能有偏移,给点余量)
	var center_ok := false
	var center_note := "no menu"
	if menu != null:
		var rect_center := menu.position + menu.size / 2.0
		var view_center := view / 2.0
		center_ok = rect_center.distance_to(view_center) <= 30.0
		center_note = "rect_center=" + str(rect_center) + " view_center=" + str(view_center)
	print("UI_MENU_CENTER ok=", center_ok, " ", center_note)

	# 界面_设置:血条被每物理帧刷成半透明(0.6)
	var bar := _widget("血条")
	var alpha_ok := bar != null and absf(bar.modulate.a - 0.6) < 0.02
	print("UI_SET ok=", alpha_ok, " alpha=", bar.modulate.a if bar != null else -1.0)

	# ── 第四十轮:设计图(套用式)─────────────────────────────────
	# 右上角 HUD 的三个控件**不在玩法图里**,是『界面_套用设计』从设计图
	# 建出来的;后两个要挂在底板上(父子关系照设计图里写的)
	var design_panel := _widget("设计底板")
	var design_title := _widget("设计标题")
	var design_btn := _widget("设计按钮")
	var design_ok := false
	var design_note := "no widget"
	if design_panel != null:
		var dwant := Vector2(view.x - design_panel.size.x - 14.0, 14.0)
		design_ok = design_title != null and design_btn != null \\
			and design_title.get_parent() == design_panel \\
			and design_btn.get_parent() == design_panel \\
			and design_panel.position.distance_to(dwant) <= 2.0
		design_note = "panel=" + str(design_panel.position) + " want=" + str(dwant)
	print("UI_DESIGN ok=", design_ok, " ", design_note)


func _start_dialog_sample() -> void:
	var panel := _widget("对话框")
	var text: Label = null
	if panel != null:
		text = panel.find_child("正文", true, false) as Label
	typing_first = text.visible_characters if text != null else -1
	var total := text.text.length() if text != null else 0
	# 逐字显示:刚开场应该"还没显示完"(除非打字速度是 0 = 直接全显示)
	print("UI_DIALOG_TYPING ok=", panel != null and panel.visible and typing_first < total,
		" chars=", typing_first, " total=", total)


func _dialog_chars() -> int:
	var panel := _widget("对话框")
	if panel == null:
		return -1
	var text := panel.find_child("正文", true, false) as Label
	return text.visible_characters if text != null else -1


func _check_dialog_done() -> void:
	var panel := _widget("对话框")
	dialog_hidden = panel == null or not panel.visible
	var score := _widget("分数") as Label
	score_after = score.text if score != null else "<none>"
	# 点一下 = 补完(visible_characters 变成 -1)；再点一下 = 关掉
	print("UI_DIALOG ok=", typing_after < 0 and dialog_hidden and score_after == "对话结束",
		" after_first_click=", typing_after, " hidden=", dialog_hidden,
		" score=", score_after)


func _check_pause() -> void:
	var menu := _widget("暂停菜单")
	var paused: bool = get_tree().paused
	var menu_on: bool = menu != null and menu.visible
	print("UI_PAUSE ok=", paused and menu_on, " paused=", paused, " menu=", menu_on)


func _check_resume() -> void:
	var menu := _widget("暂停菜单")
	var paused: bool = get_tree().paused
	var hidden: bool = menu == null or not menu.visible
	print("UI_RESUME ok=", paused_before and (not paused) and hidden,
		" was=", paused_before, " now=", paused, " hidden=", hidden)


# ── 第三十八轮:列表 / 九宫格 / 缓动 + 焦点 / 输入框 ────────────────

func _check_focus_and_tween() -> void:
	var field := _widget("昵称") as LineEdit
	var owner := get_viewport().gui_get_focus_owner()
	var focus_ok: bool = field != null and owner == field
	var alpha_ok: bool = field != null and field.modulate.a > 0.95
	print("UI_FOCUS_TWEEN ok=", focus_ok and alpha_ok,
		" focus=", owner.name if owner != null else "<none>",
		" alpha=", field.modulate.a if field != null else -1.0)


func _check_menu_list() -> void:
	# 列表 = 滚动容器 + 里层排版容器;两个槽挂在里层、从上往下排
	var list := _widget("存档列表")
	var body: Node = null
	if list != null and list.get_child_count() > 0:
		body = list.get_child(0)
	var slot_one := _widget("存档槽一")
	var slot_two := _widget("存档槽二")
	var stacked := false
	var note := "no slots"
	if slot_one != null and slot_two != null:
		stacked = body != null \\
			and slot_one.get_parent() == body and slot_two.get_parent() == body \\
			and slot_two.position.y > slot_one.position.y + 10.0 \\
			and absf(slot_two.position.x - slot_one.position.x) < 1.0
		note = "one=" + str(slot_one.position) + " two=" + str(slot_two.position) \\
			+ " body=" + (body.name if body != null else "<none>")
	print("UI_LIST ok=", list != null and body is VBoxContainer and stacked, " ", note)

	# 九宫格:四边切片都 = 14、贴图 64×64,而且是菜单的**第一个子节点**
	var bar := _widget("标题条") as NinePatchRect
	var menu := _widget("暂停菜单")
	var slices_ok: bool = bar != null and bar.patch_margin_left == 14 \\
		and bar.patch_margin_top == 14 and bar.patch_margin_right == 14 \\
		and bar.patch_margin_bottom == 14
	var tex_ok: bool = bar != null and bar.texture != null \\
		and int(bar.texture.get_size().x) == 64
	var behind: bool = menu != null and bar != null and menu.get_child(0) == bar
	print("UI_NINEPATCH ok=", slices_ok and tex_ok and behind,
		" slices=", slices_ok, " tex=", tex_ok, " behind=", behind)


func _check_slot_click() -> void:
	var score := _widget("分数") as Label
	var text := score.text if score != null else "<none>"
	print("UI_LIST_CLICK ok=", text == "读取存档一", " score=", text)


func _check_log_list() -> void:
	var list := _widget("日志列表")
	var body: Node = null
	if list != null and list.get_child_count() > 0:
		body = list.get_child(0)
	var rows: Array = []
	if body != null:
		for child in body.get_children():
			rows.append(child.text if child is Label else str(child))
	print("UI_LIST_FILL ok=", rows == ["拾取了钥匙", "打开了大门", "抵达终点"],
		" rows=", rows)


func _submit_nickname() -> void:
	var field := _widget("昵称") as LineEdit
	if field == null:
		return
	field.grab_focus()          # 玩家点一下文本框
	field.text = "阿飞"
	_send_key(KEY_ENTER, true)  # 回车提交 -> text_submitted -> 取文字 -> 改"得分"
	_send_key(KEY_ENTER, false)


func _check_submitted() -> void:
	var score := _widget("分数") as Label
	var text := score.text if score != null else "<none>"
	print("UI_SUBMIT ok=", text == "阿飞", " score=", text)


# ── 第四十轮:设计图里的按钮(行为是玩法图『界面_按钮点击』接的)──────

func _design_click() -> void:
	var button := _widget("设计按钮")
	if button != null:
		_click_at(button.get_global_rect().get_center())


func _check_design_click() -> void:
	var score := _widget("分数") as Label
	var text := score.text if score != null else "<none>"
	print("UI_DESIGN_CLICK ok=", text == "设计按钮被点了", " score=", text)


# ── 第五十轮:剧情分支(『对话_选择』)───────────────────────────────

## 选择框弹出来了没有:面板可见 + 问题文字对 + 三个选项按钮都在。
func _check_choice_shown() -> void:
	var panel := _widget("选择框")
	var question := _widget("问题") as Label
	var box: Node = null
	if panel != null:
		box = panel.find_child("选项", true, false)
	var one := _widget("选择1")
	var two := _widget("选择2")
	var three := _widget("选择3")
	var shown: bool = panel != null and panel.visible
	var ok: bool = shown and question != null \\
		and question.text == "你怎么做？" \\
		and one != null and two != null and three != null
	print("UI_CHOICE_SHOWN ok=", ok, " panel=", shown,
		" question=", question.text if question != null else "<none>",
		" options=", box.get_child_count() if box != null else -1)


## 读宿主脚本上的『结局』变量(『选了第几个』写进去的那个)。
func _read_ending() -> Variant:
	if world == null:
		return null
	for node in world.find_children("*", "Node", true, false):
		var value = node.get("结局")
		if value != null:
			return value
	return null


## 点完第 2 个选项:面板收起 + 走了第 2 条链(得分变了)+ 结局变量 = 2。
func _check_choice_picked() -> void:
	var panel := _widget("选择框")
	var hidden: bool = panel == null or not panel.visible
	var score := _widget("分数") as Label
	var text := score.text if score != null else "<none>"
	var ended = _read_ending()
	var ended_ok: bool = ended != null and int(ended) == 2
	print("UI_CHOICE ok=", hidden and text == "结局:独自逃出" and ended_ok,
		" hidden=", hidden, " score=", text, " 结局=", ended)


func _send_key(code: int, pressed: bool) -> void:
	var event := InputEventKey.new()
	event.keycode = code
	event.physical_keycode = code
	event.pressed = pressed
	Input.parse_input_event(event)


func _click_at(pos: Vector2) -> void:
	for pressed in [true, false]:
		var event := InputEventMouseButton.new()
		event.button_index = MOUSE_BUTTON_LEFT
		event.pressed = pressed
		event.position = pos
		event.global_position = pos
		get_viewport().push_input(event)
'''

PROBE_TSCN = '''; 探针(临时文件,不写回示例)
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://probe.gd" id="1_probe"]

[node name="UiProbe" type="Node"]
script = ExtResource("1_probe")
'''


def prepare():
    if not os.path.isdir(SRC):
        print(f"找不到示例工程 {SRC} —— 先跑 make_ui_demo.py")
        return False
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    shutil.copytree(SRC, OUT, ignore=shutil.ignore_patterns(".godot", "*.import"))
    with open(os.path.join(OUT, "probe.gd"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE_GD)
    with open(os.path.join(OUT, "probe.tscn"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE_TSCN)
    project_file = os.path.join(OUT, "project.godot")
    with open(project_file, "r", encoding="utf-8") as handle:
        text = handle.read()
    if "run/main_scene" in text:
        text = text.replace('run/main_scene="res://scenes/World.tscn"',
                            'run/main_scene="res://probe.tscn"')
    else:
        text = text.replace("[application]",
                            '[application]\n\nrun/main_scene="res://probe.tscn"')
    with open(project_file, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return True


def run_godot(godot):
    subprocess.run([godot, "--headless", "--path", OUT, "--import"],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
    # --quit-after 只是安全网:探针查完会自己 quit(见 PROBE_GD 末尾)
    run = subprocess.run([godot, "--headless", "--path", OUT,
                          "--quit-after", "1800"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    return (run.stdout or "") + (run.stderr or "")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--godot", default="godot")
    args = parser.parse_args()
    if not prepare():
        return 1
    text = run_godot(args.godot)
    for line in text.splitlines():
        if "UI_" in line or "SCRIPT ERROR" in line or "Parse Error" in line:
            print(line.strip())
    ok = True
    failed = any(("UI_FAIL" in line) or ("SCRIPT ERROR" in line)
                 or ("Parse Error" in line) for line in text.splitlines())
    if failed:
        print("✗ Godot 里有脚本错误 / 解析错误")
        ok = False

    checks = (
        (r"UI_WIDGETS ok=true", "界面控件没建全(面板 / 图片 / 文本 / 进度条 / 按钮)"),
        (r"UI_IMAGE ok=true", "徽标图片没加载出来(Blender 的图没导出成 art/ui_*.png?)"),
        (r"UI_PARENT ok=true", "『父控件』没生效(控件没挂到底板下面)"),
        (r"UI_ALIGN ok=true", "『对齐』位置不对(底部居中 + 边距)"),
        (r"UI_MENU_HIDDEN ok=true", "暂停菜单开局没藏起来(『开局藏』没生效)"),
        (r"UI_MENU_CENTER ok=true", "暂停菜单没有居中(居中锚点)"),
        (r"UI_SET ok=true", "『界面_设置』没生效(运行时改透明度)"),
        (r"UI_DIALOG_TYPING ok=true", "对话框开场不是逐字显示"),
        (r"UI_DIALOG ok=true", "对话框点按流程不对(补完 / 关掉 / 关掉后才改文字)"),
        (r"UI_PAUSE ok=true", "Esc 没有冻住世界 / 菜单没弹出来"),
        (r"UI_RESUME ok=true", "点『继续游戏』没有解冻 / 菜单没收回去"),
        (r"UI_LIST ok=true", "列表没建两层 / 存档槽没挂在里层容器里排队"),
        (r"UI_LIST_CLICK ok=true", "点『存档一』没走进它自己的点击链"),
        (r"UI_LIST_FILL ok=true", "『列表_填充』没灌出三行日志"),
        (r"UI_NINEPATCH ok=true", "九宫格切片 / 贴图 / 垫底 不对"),
        (r"UI_FOCUS_TWEEN ok=true", "输入框没淡入到不透明 / 焦点没落到它身上"),
        (r"UI_SUBMIT ok=true", "输入框回车提交后『取文字 -> 改文字』没生效"),
        # ---- 第四十轮:界面设计图(套用式)----
        (r"UI_DESIGN ok=true", "『界面_套用设计』没把设计图的控件建出来 / 右上角对齐不对"),
        (r"UI_DESIGN_CLICK ok=true", "点设计图里的按钮没走进『界面_按钮点击』接的链"),
        # ---- 第四十六轮:开局"选单态"(世界停 + 鼠标可见 -> 选完锁回去)----
        # ⚠ 鼠标模式在**无头模式**下锁不住(CAPTURED 设了还是 0),所以
        #   "锁回去"这一半用**生成代码**验(见下面 _check_startup_code)。
        (r"UI_STARTUP paused=true mouse=0",
         "开局不是『选单态』(应该:世界暂停 + 鼠标可见,免得一上来就能转视角)"),
        (r"UI_STARTUP_RESUME paused=false",
         "开场对话点完没有恢复世界(应该继续跑)"),
        # ---- 第五十轮:剧情分支(『对话_选择』)----
        (r"UI_CHOICE_SHOWN ok=true",
         "『对话_选择』没弹出来 / 问题文字或三个选项不对"),
        (r"UI_CHOICE ok=true",
         "点第 2 个选项没走第 2 条链(得分没变,或『选了第几个』没写进结局变量)"),
        # ---- 第六十四轮:字幕 / 打字音 / 对话历史 ----
        (r"UI_NARRATIVE ok=true",
         "字幕或对话历史不对(字幕**卡住了执行流** / 历史里不是 2 行 / "
         "打字音播放器没建出来)"),
        (r"UI_HISTORY_CLOSED ok=true", "点一下没把『对话回顾』面板关掉"),
    )
    for pattern, why in checks:
        if not re.search(pattern, text):
            print(f"✗ {why}:",
                  [line for line in text.splitlines()
                   if line.startswith("UI_") and "ok=" in line])
            ok = False
    if "UI_DONE" not in text:
        print("✗ 探针没跑完(可能中途报错)")
        ok = False

    # ── 生成物层面的证据 ────────────────────────────────────────────
    script_path = os.path.join(OUT, "scripts", "game_logic.gd")
    if os.path.isfile(script_path):
        with open(script_path, "r", encoding="utf-8") as handle:
            code = handle.read()
        for needle, why in (
                ("TextureRect.new()", "『界面_图片』"),
                ("_box.corner_radius_top_left", "『界面_面板』的圆角"),
                ("_gk_find_widget(\"血条\")", "『界面_设置』找控件"),
                ("Control.PRESET_CENTER_BOTTOM", "底部居中的锚点"),
                ("_gk_dialog_show(", "对话框辅助函数"),
                # ---- 第六十四轮:字幕 / 打字音 / 对话历史 ----
                ("func _gk_subtitle_show(", "字幕辅助函数"),
                ("func _gk_history_show(", "对话历史面板"),
                ('_gk_dialog_blip = "res://audio/gk_type.wav"', "打字音路径"),
                ("_gk_play_blip(", "打字音播放"),
                ("_gk_apply_pause(", "暂停 / 继续世界"),
                ("process_frame.connect(_gk_pause_watch)", "Esc 热键(暂停也读得到按键)"),
                # ---- 第二版(第三十八轮)----
                ("ScrollContainer.new()", "『界面_列表』的滚动容器"),
                ("VBoxContainer.new()", "『界面_列表』里层排版的容器"),
                ("custom_minimum_size", "列表里的控件交给容器排版"),
                ("_gk_list_fill(", "『界面_列表_填充』"),
                ("NinePatchRect.new()", "『界面_九宫格』"),
                ("LineEdit.new()", "『界面_输入框』"),
                ("text_submitted.connect(", "输入框回车提交"),
                ("\"modulate:a\"", "『界面_缓动』"),
                ("_gk_widget_text(", "『界面_取文字』"),
                ("grab_focus()", "『界面_设焦点』"),
                # ---- 第四十轮:设计图(套用式)----
                ("_gk_ui_设计底板", "『界面_套用设计』把设计图的控件建进脚本"),
                ('_gk_find_widget("设计按钮")', "『界面_按钮点击』按名字找按钮"),
                (".pressed.is_connected(", "按钮点击的幂等接线"),
                # ---- 第五十轮:剧情分支(『对话_选择』)----
                ("_gk_choice_show(", "『对话_选择』的辅助函数"),
                ("match _gk_choice_picked:", "『对话_选择』的分支 match"),
                ('button.name = "选择%d" % (index + 1)', "选项按钮"),
                ("gk_choice_input_event(event)", "数字键 1~6 也能选"),
                ("_gk_choice_picked", "『选了第几个』输出"),
        ):
            if needle not in code:
                print(f"✗ 生成的脚本里没有{why}: 找不到 {needle!r}")
                ok = False
        # ---- 第四十六轮:选完之后"把鼠标锁回去"必须是**显式**的一句 ──
        # ⚠ 无头模式验不了 mouse_mode(设 CAPTURED 仍是 0),所以这一半
        #   用生成代码验;而且**顺序**要对:收起对话 -> 继续世界 -> 锁鼠标。
        if not re.search(
                r"_gk_dialog_hide\(\)\s*\n\s*_gk_apply_pause\(false, \"\", true\)"
                r"\s*\n\s*Input\.mouse_mode = Input\.MOUSE_MODE_CAPTURED", code):
            print("✗ 生成代码里没有『收起对话 -> 继续世界 -> 锁回鼠标』这一串"
                  "(开局选单态的收尾少了 / 顺序不对)")
            ok = False
        art = os.path.join(OUT, "art")
        pngs = [name for name in os.listdir(art)
                if name.startswith("ui_") and name.endswith(".png")] if os.path.isdir(art) else []
        # 两张图:徽标 + 九宫格标题条图(各自的摘要不同,不会互相覆盖)
        if len(pngs) < 2:
            print(f"✗ art/ 里的界面图片不足两张(徽标 + 标题条图): {pngs}")
            ok = False
        else:
            print(f"art/ 界面图片: {pngs}")
    else:
        print(f"✗ 没找到生成的脚本 {script_path}")
        ok = False

    print("CHECK_UI_DEMO=" + ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())