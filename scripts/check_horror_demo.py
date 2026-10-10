"""恐怖示例的自动检查(第五十九轮单场景 -> 六十轮拆三关 -> 七十轮补标题页)。

用法:
    python scripts\\check_horror_demo.py
    python scripts\\check_horror_demo.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

── 怎么测四幕(而不是一幕)────────────────────────────────────
一幕一张 `.tscn`。**不能**在一个探针里真的走"切关卡" —— Godot 的
`change_scene_to_file` 会把当前场景整棵换掉,探针(挂在当前场景下)会
一起被释放,后面的断言就没机会跑。所以拆成**四个探针**,各自把对应那幕的
`.tscn` 当成子节点加载进来驱动:

  0. 标题:加载后屏幕上出现标题字 + 『开始游戏』按钮(UI 真建起来了);
  1. 教室:**开场曲线运镜真的在飞**(相机位置在动)、拾 3 张纸条 -> `纸条数` = 3;
  2. 走廊:AI 在巡逻、紧张度远低近高 + 心跳建起来、走过惊吓区 ->
     相机在震 + Master 被压低;
  3. 楼梯间:**先写一份"纸条数 = 3"的存档**(模拟第一关存过),再走到门口
     -> 弹出结局选择 -> 按 2 -> `结局` = 2 且真的落盘
     (落的是**带校验的信封** —— 第九十三轮『防改档』);
  4. 『防改档』两趟(第九十三轮):读回上一趟**生成代码自己写下**的信封
     (往返通);塞一份**被手改过**的档(哈希对不上)-> 当没有存档:
     纸条数回默认 0、门口不放行。

另外还会查脚本里的**跨幕证据**:标题有 `change_scene_to_file` + `教室.tscn`,
教室有运镜曲线(`_gk_cam_fly_curve`)+ 切走廊,走廊切楼梯间,楼梯间有读档 +
`>= 3` 的门槛 + 演完切回标题。主场景必须是**标题页**(按 F5 先看到标题)。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "examples", "demo_horror", "恐怖示例_godot")
OUT = os.path.join(ROOT, "_dist", "horror_probe")
GODOT_DEFAULT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"

TITLE_SCENE = "res://scenes/标题.tscn"
ROOM_SCENE = "res://scenes/教室.tscn"
CORRIDOR_SCENE = "res://scenes/走廊.tscn"
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
	player = world.find_child(player_name, true, false)
	if enemy_name != "":
		enemy = world.find_child(enemy_name, true, false)


func _state() -> String:
	if player == null:
		return "<no-player>"
	return str(player.get("ai_state"))


func _var(name: String):
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
	_boot("%(title)s", "标题管理", "")


func _physics_process(delta: float) -> void:
	elapsed += delta
	if phase == 0 and elapsed >= 0.3:
		phase = 1
		_count_ui(world)
		print("HORROR_TITLE_UI labels=", labels, " buttons=", buttons,
			" cam=", world.find_child("标题机位", true, false) != null)
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
	cam = world.find_child("教室特写", true, false)


func _physics_process(delta: float) -> void:
	elapsed += delta
	match phase:
		0:
			if elapsed >= 0.2:
				phase = 1
				if cam != null:
					cam_start = cam.global_position
				print("HORROR_ROOM_START player=", player != null,
					" spots=", spots.size(), " cam=", cam != null)
				_put_at("纸条1")
		1:
			if elapsed >= 0.8:
				phase = 2
				var flew := 0.0
				if cam != null:
					flew = cam.global_position.distance_to(cam_start)
				print("HORROR_ROOM_OPENING flew=", snappedf(flew, 0.01))
				_put_at("纸条2")
		2:
			if elapsed >= 1.4:
				phase = 3
				_put_at("纸条3")
		3:
			if elapsed >= 2.0:
				phase = 4
				print("HORROR_ROOM_NOTES count=",
					snappedf(float(_var("纸条数")), 0.01))
				# 挪到一块空地(别踩到门口 / 纸条那些触发区),看视角跟不跟人
				_put(Vector3(-8.0, 0.8, 0.0))
		4:
			# 开场运镜 2.5s + 特写停留 1.5s + 切回第三人称 1.0s —— 5.4s 早就切回来了
			if elapsed >= 5.4:
				phase = 5
				var cur := get_viewport().get_camera_3d()
				if cur == null:
					print("HORROR_ROOM_CAM cur=<null>")
				else:
					print("HORROR_ROOM_CAM cur=", cur.name,
						" dist=", snappedf(cur.global_position.distance_to(
							player.global_position), 0.01))
				print("HORROR_ROOM_DONE")
				set_physics_process(false)
				get_tree().quit()
''' % {"room": ROOM_SCENE}

PROBE_CORRIDOR = PROBE_HEAD + '''

var enemy_start := Vector3.ZERO


func _ready() -> void:
	_boot("%(corridor)s", "走廊玩家", "怪物")
	_find_areas(["惊吓区", "去楼梯间"])


func _physics_process(delta: float) -> void:
	elapsed += delta
	match phase:
		0:
			if elapsed >= 0.2:
				phase = 1
				if enemy != null:
					enemy_start = enemy.global_position
				print("HORROR_CORRIDOR_START state=", _state(),
					" has_enemy=", enemy != null, " spots=", spots.size())
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
				if enemy != null:
					_put(enemy.global_position + Vector3(2.0, 1.0, 0.0))
		2:
			if elapsed >= 1.5:
				phase = 3
				print("HORROR_TENSION_NEAR tension=",
					snappedf(float(_var("_gk_heart_tension")), 0.001),
					" heart=", _var("_gk_heart_player") != null)
				_put_at("惊吓区")
		3:
			if elapsed >= 1.95:
				phase = 4
				print("HORROR_SCARE shake=",
					snappedf(float(_var("_gk_shake_left")), 0.01),
					" bus=", snappedf(AudioServer.get_bus_volume_db(0), 0.01))
				print("HORROR_CORRIDOR_DONE")
				set_physics_process(false)
				get_tree().quit()
''' % {"corridor": CORRIDOR_SCENE}

PROBE_STAIRS = PROBE_HEAD + '''

func _ready() -> void:
	# ⚠ 模拟第一关"存过档":先落一份 纸条数 = 3,再加载楼梯间
	#   (楼梯间开局的『存档_载入』就会把 3 读回来,门口才放行)。
	var file := FileAccess.open("user://gk_save1.json", FileAccess.WRITE)
	if file != null:
		file.store_string('{"纸条数": 3}')
		file.close()
	_boot("%(stairs)s", "楼梯间玩家", "")
	_find_areas(["门口"])


func _physics_process(delta: float) -> void:
	elapsed += delta
	match phase:
		0:
			if elapsed >= 0.2:
				phase = 1
				print("HORROR_STAIRS_START player=", player != null,
					" notes=", _var("纸条数"), " spots=", spots.size())
				_put_at("门口")
		1:
			if elapsed >= 0.9:
				phase = 2
				var panel = _var("_gk_choice_panel")
				print("HORROR_DOOR picked=", _var("_gk_choice_picked"),
					" panel=", panel != null and panel.visible)
				_send_key(KEY_2, true)
		2:
			if elapsed >= 1.1:
				phase = 3
				_send_key(KEY_2, false)
		3:
			if elapsed >= 1.6:
				phase = 4
				print("HORROR_ENDING ended=", _var("结局"),
					" saved=", FileAccess.file_exists("user://gk_save1.json"),
					" picked=", _var("_gk_choice_picked"))
				# 第九十三轮:落盘的该是**带校验的信封**(gk_check 在文件里)
				print("HORROR_SAVE_FORMAT check=",
					FileAccess.get_file_as_string("user://gk_save1.json"
						).contains("gk_check"))
				print("HORROR_STAIRS_DONE")
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
	_find_areas(["门口"])


func _physics_process(delta: float) -> void:
	elapsed += delta
	match phase:
		0:
			if elapsed >= 0.2:
				phase = 1
				_put_at("门口")
		1:
			if elapsed >= 1.0:
				phase = 2
				var panel = _var("_gk_choice_panel")
				print("HORROR_TAMPER notes=", _var("纸条数"),
					" panel=", panel != null and panel.visible)
				print("HORROR_TAMPER_DONE")
				set_physics_process(false)
				get_tree().quit()
''' % {"stairs": STAIRS_SCENE}

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
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    shutil.copytree(SRC, OUT, ignore=shutil.ignore_patterns(".godot", "*.import"))
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
    checks = {
        "scripts/game_logic_标题.gd": (
            ("change_scene_to_file", "标题切关卡的调用"),
            ("res://scenes/教室.tscn", "标题切到教室的路径"),
            ("Button.new()", "『开始游戏』按钮"),
            ("Label.new()", "标题文字"),
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
        ),
        "scripts/game_logic_走廊.gd": (
            ("change_scene_to_file", "走廊切关卡的调用"),
            ("res://scenes/楼梯间.tscn", "走廊切到楼梯间的路径"),
            ("_gk_heart_tension", "心跳的紧张度"),
            ("get_physics_process_delta_time()", "心跳按物理帧计时"),
            ("AudioServer.get_bus_index(", "总线音量(受惊压低)"),
            ("_gk_shake_cam", "惊吓的相机震动"),
            ("ColorRect.new()", "惊吓的屏幕闪白"),
            ('get_nodes_in_group("gk_ai_hearer")', "听觉广播"),
            ("func _gk_hear_noise(", "听觉派发器"),
            ('ai_state == "去查看":', "去查看行为"),
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
            ("await", "结局停留(等待_秒)"),
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
    # 楼梯间要有 ">= 3" 的门槛
    path = os.path.join(OUT, "scripts/game_logic_楼梯间.gd")
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as handle:
            code = handle.read()
        if not re.search(r"纸条数\)? >= 3", code):
            problems.append("楼梯间没有『纸条数 >= 3』的门槛")


def _scene_checks(problems):
    checks = {
        "scenes/标题.tscn": (
            ('name="标题管理"', "标题管理(挂脚本)"),
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
        ),
        "scenes/走廊.tscn": (
            ('name="怪物" type="CharacterBody3D"', "怪物"),
            ('name="巡逻点甲"', "巡逻点甲"),
            ('name="惊吓区" type="Area3D"', "惊吓区"),
            ('name="去楼梯间" type="Area3D"', "去楼梯间触发区"),
            ('name="手电筒" type="SpotLight3D"', "手电筒(聚光)"),
        ),
        "scenes/楼梯间.tscn": (
            ('name="门口" type="Area3D"', "门口触发区"),
            ('name="楼梯间玩家" type="CharacterBody3D"', "楼梯间角色"),
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
    title_ui = re.search(r"HORROR_TITLE_UI labels=(\d+) buttons=(\d+) cam=(\w+)",
                         title)
    if not title_ui:
        problems.append("标题页 UI 没跑出来: "
                        + _grab(title, r"HORROR_TITLE_UI[^\n]*"))
    else:
        if int(title_ui.group(1)) < 2:
            problems.append("标题页没出现文字标签: " + title_ui.group(0))
        if int(title_ui.group(2)) < 1:
            problems.append("标题页没出现『开始游戏』按钮: " + title_ui.group(0))
        if title_ui.group(3) != "true":
            problems.append("标题页没有相机: " + title_ui.group(0))
    if "HORROR_TITLE_DONE" not in title:
        problems.append("标题页探针没跑完")

    # ── 第一关 教室 ─────────────────────────────────────────────────
    room = _run_probe(args.godot, "probe_room", PROBE_ROOM, "HorrorRoomProbe", 900)
    _errors(room, problems)
    if not re.search(r"HORROR_ROOM_START player=true spots=4 cam=true", room):
        problems.append("教室开场不对(玩家 / 4 个触发区 / 特写机位没找齐): "
                        + _grab(room, r"HORROR_ROOM_START[^\n]*"))
    flew = re.search(r"HORROR_ROOM_OPENING flew=([\d.]+)", room)
    if not flew or float(flew.group(1)) < 2.0:
        problems.append("开场曲线运镜没在飞(相机没动): "
                        + _grab(room, r"HORROR_ROOM_OPENING[^\n]*"))
    notes = re.search(r"HORROR_ROOM_NOTES count=([\d.]+)", room)
    if not notes or float(notes.group(1)) < 2.5:
        problems.append("教室拾 3 张纸条后『纸条数』不是 3: "
                        + _grab(room, r"HORROR_ROOM_NOTES[^\n]*"))
    # 开场运镜**必须切回第三人称**:不切回来视角钉在特写上,人还能走但看不
    # 见自己 = "卡住"(用户实测报过)。这里断言当前相机是『教室机位』且在跟人。
    back = re.search(r"HORROR_ROOM_CAM cur=(\S+) dist=([\d.]+)", room)
    if not back:
        problems.append("教室没跑出『切回第三人称』的结论行: "
                        + _grab(room, r"HORROR_ROOM_CAM[^\n]*"))
    else:
        if back.group(1) != "教室机位":
            problems.append("开场运镜之后当前相机不是第三人称机位(卡住了): "
                            + back.group(0))
        if float(back.group(2)) > 8.0:
            problems.append("第三人称机位没有跟着角色(视角还钉在特写上): "
                            + back.group(0))
    if "HORROR_ROOM_DONE" not in room:
        problems.append("教室探针没跑完")

    # ── 第二关 走廊 ─────────────────────────────────────────────────
    corridor = _run_probe(args.godot, "probe_corridor", PROBE_CORRIDOR,
                          "HorrorCorridorProbe", 900)
    _errors(corridor, problems)
    if not re.search(r"HORROR_CORRIDOR_START state=巡逻 has_enemy=true spots=2",
                     corridor):
        problems.append("走廊开场不对(怪物 / 两个触发区没找齐): "
                        + _grab(corridor, r"HORROR_CORRIDOR_START[^\n]*"))
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
    if not near or float(near.group(1)) < 0.3:
        problems.append("凑近怪物后紧张度没升起来: "
                        + _grab(corridor, r"HORROR_TENSION_NEAR[^\n]*"))
    elif near.group(2) != "true":
        problems.append("心跳播放器没建起来: " + near.group(0))
    scare = re.search(r"HORROR_SCARE shake=([\-\d.]+) bus=([\-\d.]+)", corridor)
    if not scare:
        problems.append("惊吓那段没跑出来: "
                        + _grab(corridor, r"HORROR_SCARE[^\n]*"))
    else:
        if float(scare.group(1)) <= 0.0:
            problems.append("走过惊吓区但相机没在震: " + scare.group(0))
        if float(scare.group(2)) > -10.0:
            problems.append("『音效_总线音量』没有压低 Master: " + scare.group(0))
    if "HORROR_CORRIDOR_DONE" not in corridor:
        problems.append("走廊探针没跑完")

    # ── 第三关 楼梯间 ───────────────────────────────────────────────
    stairs = _run_probe(args.godot, "probe_stairs", PROBE_STAIRS,
                        "HorrorStairsProbe", 900)
    _errors(stairs, problems)
    if not re.search(r"HORROR_STAIRS_START player=true notes=3", stairs):
        problems.append("楼梯间没把存档里的纸条数(3)读回来: "
                        + _grab(stairs, r"HORROR_STAIRS_START[^\n]*"))
    door = re.search(r"HORROR_DOOR picked=([\-\d]+) panel=(\w+)", stairs)
    if not door or int(door.group(1)) != -1 or door.group(2) != "true":
        problems.append("走到门口没弹出结局选择: "
                        + _grab(stairs, r"HORROR_DOOR[^\n]*"))
    ending = re.search(r"HORROR_ENDING ended=([\d.]+) saved=(\w+)", stairs)
    if not ending or float(ending.group(1)) != 2.0:
        problems.append("按 2 之后『结局』不是 2: "
                        + _grab(stairs, r"HORROR_ENDING[^\n]*"))
    elif ending.group(2) != "true":
        problems.append("结局没有落盘: " + ending.group(0))
    envelope_form = re.search(r"HORROR_SAVE_FORMAT check=(\w+)", stairs)
    if not envelope_form or envelope_form.group(1) != "true":
        problems.append("结局落盘的不是带校验的信封(『防改档』没生效): "
                        + _grab(stairs, r"HORROR_SAVE_FORMAT[^\n]*"))
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
    broke = re.search(r"HORROR_TAMPER notes=([\d.]+) panel=(\w+)", tamper)
    if not broke:
        problems.append("改档那趟没跑出结论行: "
                        + _grab(tamper, r"HORROR_TAMPER[^\n]*"))
    else:
        if float(broke.group(1)) != 0.0:
            problems.append("被改过的存档没被拒(纸条数该回默认 0): "
                            + broke.group(0))
        if broke.group(2) != "false":
            problems.append("被改过的存档还放行了(门口不该弹结局选择): "
                            + broke.group(0))
    if "HORROR_TAMPER_DONE" not in tamper:
        problems.append("改档探针没跑完")

    # ── 生成物层面的证据 ────────────────────────────────────────────
    _code_checks(problems)
    _scene_checks(problems)
    _main_scene_check(problems)

    for item in dict.fromkeys(problems):
        print(f"✗ {item}")
    print("CHECK_HORROR_DEMO=" + ("OK" if not problems else "FAIL"))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())