"""界面新功能示例的自动检查(第一百二十七 / 一百二十八轮验收)。

用法(要装 Godot,不用 Blender):
    python scripts\\check_ui_v2_demo.py --godot D:\\godot\\Godot_..._console.exe

做法:把 `examples/demo_ui_v2/界面新功能示例_godot` 拷到 `_dist/ui_v2_probe`,
塞一个探针场景当主场景,无头跑一遍 Godot,读它打印的 `UI_V2_*` 行。

── 查什么 ─────────────────────────────────────────────────────
每一项都对应一个**新功能**,失败就是那个功能有缺陷:

| 断言 | 对应的功能 |
|---|---|
| UI_V2_WIDGETS | 画布 / 限定框 / 背景 / 文本限定框 / 按钮 都建出来了 |
| UI_V2_PARENT | **连线式父子**:标题条挂竖排框、竖排框挂画布、得分挂竖排框(用『界面_取控件』的引用当父) |
| UI_V2_FLOW | 限定框真的在**排版**(子项自上而下依次排开,没被写死坐标) |
| UI_V2_HIDDEN | 开局那两块是**藏着的**(靠『界面_隐藏控件』,因为结构件没有『开局藏』) |
| UI_V2_NAME | 『取值_物体名称』取到的就是场景里的『舞台』 |
| UI_V2_POP_IN | 按 P:**弹入**——滑进来 + 黑幕淡到 0.6 + 置顶 |
| UI_V2_POP_OUT | 按 O:**弹出**——藏起来 + 位置**复位** + 黑幕收掉 |
| UI_V2_CLICK_WIDGET | 左键点『开始按钮』-> 变量 分数=10 -> 得分显示 10 |
| UI_V2_CLICK_ANY | **右键点屏幕任意位置** -> 变量 分数=0 -> 得分显示 0 |
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "examples", "demo_ui_v2", "界面新功能示例_godot")
OUT = os.path.join(ROOT, "_dist", "ui_v2_probe")

PROBE_GD = '''extends Node

var world: Node = null
var ui_root: Node = null
var _fails := []
var _home := Vector2.ZERO      # 弹入前竖排框的原位(弹出后要复位回这里)


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	# ⚠ 无头模式的默认视口只有 64×64:界面会摆到屏幕外面,点击坐标落在
	#   视口之外 —— push_input 打不中。先把窗口调成正常分辨率**再**实例化
	#   世界(和 check_ui_demo 踩出来的同一条经验)。
	get_window().size = Vector2i(1152, 648)
	# ⚠ 探针被设成 run/main_scene,所以世界场景**不会自动加载** ——
	#   必须自己 load 进来 add_child(否则一个控件都找不到)。
	world = load("res://scenes/World.tscn").instantiate()
	add_child(world)
	var ui := world.find_child("GKUI", true, false)
	if ui != null:
		ui_root = ui.find_child("Root", true, false)
	await get_tree().create_timer(0.2).timeout
	# ⚠ 顺序有讲究:面板**开局是藏着的** ——
	#   * "容器排版"和"点按钮"都必须等弹入之后再查(藏着的容器不排版、
	#     藏着的按钮点不到);
	#   * "右键点任意位置"放在弹出之后,正好验"不需要控件可见"。
	await _check_widgets()
	await _check_parent()
	await _check_hidden()
	await _check_name()
	await _check_ref_target()      # 『控件』引脚(吃引用)那条路
	await _check_pop_in()          # 按 P:滑入 + 黑幕 + 之后查容器排版
	await _check_click_widget()    # 面板开着时点『开始按钮』
	await _check_pop_out()         # 按 O:藏起来 + 复位 + 黑幕收
	await _check_click_any()       # 面板收起后,右键点屏幕角落
	if not _fails.is_empty():
		print("UI_V2_FAIL ", _fails)
	# ⚠ **失败也要打印 **DONE**:外层把它当"探针跑完了"的判据 ——
	#   不打印的话失败会同时报成"探针没跑完",看不出到底哪条挂。
	print("UI_V2_DONE")
	get_tree().quit()


func _widget(name: String) -> Control:
	if ui_root == null:
		return null
	return ui_root.find_child(name, true, false) as Control


func _send_key(code: int) -> void:
	for pressed in [true, false]:
		var event := InputEventKey.new()
		event.keycode = code
		event.physical_keycode = code
		event.pressed = pressed
		Input.parse_input_event(event)
	await get_tree().process_frame


func _click_at(pos: Vector2, button: int) -> void:
	# ⚠ 必须用 `Input.parse_input_event`,**不能**用 `Viewport.push_input` ——
	#   后者只走 GUI 命中/控件信号那条路,**不会**触发节点的 `_input(event)`。
	#   『界面_鼠标点击』是写在 `_input` 里的,所以 push_input 下它一次都不跑
	#   (实测:探针自己"看起来点了",插件这边毫无反应)。
	for pressed in [true, false]:
		var event := InputEventMouseButton.new()
		event.button_index = button
		event.pressed = pressed
		event.position = pos
		event.global_position = pos
		Input.parse_input_event(event)
	await get_tree().process_frame


func _widget_names() -> Array:
	return ["主画布", "黑幕", "竖排框", "标题条", "开始按钮", "得分", "名字牌"]


func _check_widgets() -> void:
	var missing := []
	for name in _widget_names():
		if _widget(name) == null:
			missing.append(name)
	print("UI_V2_WIDGETS ok=", missing.is_empty(), " missing=", missing)
	if not missing.is_empty():
		_fails.append("widgets")


func _check_parent() -> void:
	var box := _widget("竖排框")
	var title := _widget("标题条")
	var start := _widget("开始按钮")
	var score := _widget("得分")
	var canvas := _widget("主画布")
	var ok := box != null and canvas != null and box.get_parent() == canvas \\
		and title != null and title.get_parent() == box \\
		and start != null and start.get_parent() == box \\
		and score != null and score.get_parent() == box
	print("UI_V2_PARENT ok=", ok,
		" box_parent=", box.get_parent().name if box != null and box.get_parent() != null else "<none>",
		" score_parent=", score.get_parent().name if score != null and score.get_parent() != null else "<none>")
	if not ok:
		_fails.append("parent")


func _check_hidden() -> void:
	var box := _widget("竖排框")
	var scrim := _widget("黑幕")
	var ok := box != null and scrim != null and not box.visible and not scrim.visible
	print("UI_V2_HIDDEN ok=", ok,
		" box=", box.visible if box != null else "<none>",
		" scrim=", scrim.visible if scrim != null else "<none>")
	if not ok:
		_fails.append("hidden")


func _check_name() -> void:
	var plate := _widget("名字牌")
	var text: String = plate.text if plate != null else "<none>"
	print("UI_V2_NAME ok=", text == "舞台", " text=", text)
	if text != "舞台":
		_fails.append("name")


func _check_ref_target() -> void:
	# 『刷新得分(开局)』那一句**没填控件名**,靠『控件』引脚吃『界面_取控件』
	# 取到的引用。它真跑过的话,建控件时写进去的那句字面量 "分数 0" 会被
	# 变量『分数』的值刷掉。
	# ⚠ 不能断言它就是 "0":变量值是 float,Godot 的 `str(0.0)` 是 `"0.0"`。
	var score := _widget("得分")
	var text: String = score.text if score != null else "<none>"
	var ok: bool = text != "分数 0" and text.contains("0")
	print("UI_V2_REF ok=", ok, " score=", text)
	if not ok:
		_fails.append("ref")


func _check_pop_in() -> void:
	var box := _widget("竖排框")
	var scrim := _widget("黑幕")
	if box == null or scrim == null:
		_fails.append("pop_in")
		return
	# 原位先记下来 —— 弹出后要复位回这里(⚠ 不能写 Vector2.ZERO:
	# 竖排框的 X / Y 是 60/60,home 不是原点)
	_home = box.position
	await _send_key(KEY_P)
	await get_tree().create_timer(0.15).timeout
	var sliding: bool = box.visible and box.position.x < _home.x
	await get_tree().create_timer(0.6).timeout
	var landed: bool = box.visible and absf(box.position.x - _home.x) < 1.0
	var scrim_ok: bool = scrim.visible and absf(scrim.modulate.a - 0.6) < 0.05
	print("UI_V2_POP_IN ok=", landed and scrim_ok, " visible=", box.visible,
		" x=", box.position.x, " home=", _home.x,
		" scrim_a=", scrim.modulate.a, " sliding=", sliding)
	if not (landed and scrim_ok):
		_fails.append("pop_in")

	# 『界面_限定框』的**排版**放在这里查:容器要 visible 之后才计算子项位置
	# (开局那块是藏着的,先查会看到子项全在 0)
	var title := _widget("标题条")
	var start := _widget("开始按钮")
	var flow_ok: bool = title != null and start != null \
		and start.position.y > title.position.y
	print("UI_V2_FLOW ok=", flow_ok, " title_y=", title.position.y,
		" start_y=", start.position.y)
	if not flow_ok:
		_fails.append("flow")


func _check_pop_out() -> void:
	var box := _widget("竖排框")
	var scrim := _widget("黑幕")
	if box == null or scrim == null:
		_fails.append("pop_out")
		return
	await _send_key(KEY_O)
	await get_tree().create_timer(0.7).timeout
	var out_ok: bool = (not box.visible) and absf(box.position.x - _home.x) < 1.0 \
		and (not scrim.visible)
	print("UI_V2_POP_OUT ok=", out_ok, " visible=", box.visible,
		" x=", box.position.x, " home=", _home.x, " scrim=", scrim.visible)
	if not out_ok:
		_fails.append("pop_out")


func _check_click_widget() -> void:
	# 面板这会儿是**开着的**(上一步刚弹入),按钮才点得动
	var start := _widget("开始按钮")
	var score := _widget("得分")
	if start == null or score == null:
		_fails.append("click_widget")
		return
	await _click_at(start.get_global_rect().get_center(), MOUSE_BUTTON_LEFT)
	await get_tree().create_timer(0.2).timeout
	var text_after: String = score.text
	var click_ok: bool = text_after.contains("10")
	print("UI_V2_CLICK_WIDGET ok=", click_ok, " score=", text_after)
	if not click_ok:
		_fails.append("click_widget")


func _check_click_any() -> void:
	# 面板已经收起来 —— 正好验"点屏幕任意位置"这条(不需要控件可见)
	var score := _widget("得分")
	if score == null:
		_fails.append("click_any")
		return
	var view := get_viewport().get_visible_rect().size
	await _click_at(Vector2(view.x - 12.0, view.y - 12.0), MOUSE_BUTTON_RIGHT)
	await get_tree().create_timer(0.2).timeout
	var text_end: String = score.text
	var any_ok: bool = text_end.contains("0") and not text_end.contains("10")
	print("UI_V2_CLICK_ANY ok=", any_ok, " score=", text_end)
	if not any_ok:
		_fails.append("click_any")
'''

PROBE_TSCN = '''; 探针(临时文件,不写回示例)
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://probe.gd" id="1_probe"]

[node name="UiV2Probe" type="Node"]
script = ExtResource("1_probe")
'''


def prepare():
    if not os.path.isdir(SRC):
        print(f"找不到示例工程 {SRC} —— 先跑 make_ui_v2_demo.py")
        return False
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    shutil.copytree(SRC, OUT,
                    ignore=shutil.ignore_patterns(".godot", "*.import"))
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
    run = subprocess.run([godot, "--headless", "--path", OUT,
                          "--quit-after", "1800"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    return (run.stdout or "") + (run.stderr or "")


CHECKS = (
    (r"UI_V2_WIDGETS ok=true", "新界面的控件没建全"),
    (r"UI_V2_PARENT ok=true", "连线式父子没生效(挂载目标不对)"),
    (r"UI_V2_FLOW ok=true", "『界面_限定框』没在排版(子项没自上而下排)"),
    (r"UI_V2_HIDDEN ok=true", "开局那两块没藏起来"),
    (r"UI_V2_NAME ok=true", "『取值_物体名称』取到的名字不对"),
    (r"UI_V2_REF ok=true", "『控件』引脚(吃界面引用)那条路没生效"),
    (r"UI_V2_POP_IN ok=true", "『界面_弹入』没滑到位 / 黑幕没淡到 0.6"),
    (r"UI_V2_POP_OUT ok=true", "『界面_弹出』没藏起来 / 位置没复位 / 黑幕没收"),
    (r"UI_V2_CLICK_WIDGET ok=true", "『界面_鼠标点击』(指定控件)没生效"),
    (r"UI_V2_CLICK_ANY ok=true", "『界面_鼠标点击』(任意位置)没生效"),
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--godot", default="godot")
    args = parser.parse_args()
    if not prepare():
        return 1
    text = run_godot(args.godot)
    for line in text.splitlines():
        if "UI_V2" in line or "SCRIPT ERROR" in line or "Parse Error" in line:
            print("    " + line.rstrip())
    if "找不到界面控件" in text:
        print("    ✗ 生成物里有『找不到界面控件』告警 —— 有控件按名字找不到")
    ok = True
    for needle, why in CHECKS:
        if not re.search(needle, text):
            print(f"✗ {why}")
            ok = False
    if re.search(r"UI_V2_FAIL", text):
        ok = False
    if "SCRIPT ERROR" in text or "Parse Error" in text or "SCRIPT_ERROR" in text:
        print("✗ Godot 里有脚本错误 / 解析错误")
        ok = False
    if "UI_V2_DONE" not in text:
        print("✗ 探针没跑完(可能中途报错)")
        ok = False
    print("CHECK_UI_V2_DEMO=" + ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
