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


func _physics_process(delta: float) -> void:
	elapsed += delta
	if phase == 0 and elapsed >= 0.35:
		phase = 1
		_check_widgets()
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
		_send_key(KEY_ESCAPE, true)           # 按住 Esc(热键是每帧轮询的)
	elif phase == 4 and elapsed >= 2.0:
		phase = 5
		_send_key(KEY_ESCAPE, false)
		_check_pause()
	elif phase == 5 and elapsed >= 2.4:
		phase = 6
		paused_before = get_tree().paused
		var button := _widget("继续按钮")
		if button != null:
			_click_at(button.get_global_rect().get_center())
	elif phase == 6 and elapsed >= 2.9:
		phase = 7
		_check_resume()
		print("UI_DONE")
		set_physics_process(false)


func _check_widgets() -> void:
	var names := ["HUD底", "徽标", "分数", "血条", "底部提示",
		"暂停菜单", "暂停标题", "继续按钮", "退出按钮"]
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
    run = subprocess.run([godot, "--headless", "--path", OUT,
                          "--quit-after", "420"],
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
                ("_gk_apply_pause(", "暂停 / 继续世界"),
                ("process_frame.connect(_gk_pause_watch)", "Esc 热键(暂停也读得到按键)"),
        ):
            if needle not in code:
                print(f"✗ 生成的脚本里没有{why}: 找不到 {needle!r}")
                ok = False
        art = os.path.join(OUT, "art")
        pngs = [name for name in os.listdir(art)
                if name.startswith("ui_") and name.endswith(".png")] if os.path.isdir(art) else []
        if not pngs:
            print("✗ art/ 里没有界面图片(ui_*.png)")
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