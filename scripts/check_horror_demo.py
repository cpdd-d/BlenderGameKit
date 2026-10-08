"""恐怖示例的自动检查(第五十九轮:阶段④收尾)。

用法:
    python scripts\\check_horror_demo.py
    python scripts\\check_horror_demo.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

它会:
  1. 把 `examples/demo_horror/恐怖示例_godot` 拷到 `_dist/horror_probe`(**不碰示例本身**);
  2. 在副本里加一个探针场景(`probe.gd` + `probe.tscn`),把主场景指过去;
  3. 用 Godot 无头跑,读探针打印的 HORROR_* 行,汇总 PASS / FAIL。

探针查的是"恐怖关该有的现象"(不是代码长什么样):
  * 怪物活着且在**巡逻**(1 秒里位移 > 0.5 米);
  * **紧张度**:玩家离怪物很远时 ≈ 0,凑到 2 米时明显升高(> 0.3),
    而且心跳播放器跟着建起来 —— 『氛围_紧张度』+『氛围_心跳』真的联动;
  * 走过 3 张纸条 -> `纸条数` = 3;
  * 走过『惊吓区』-> 相机**正在震**(`_gk_shake_left` > 0)且 Master **被压低**
    (总线音量 < -10)—— 『惊吓_事件』三连 + 『音效_总线音量』;
  * 走到门口 -> 弹出结局选择(还没选时 `_gk_choice_picked` = -1),
    按 **2** -> `结局` = 2,而且 `user://gk_save1.json` **真的落盘**;
  * 生成脚本里:新节点 / AI 听觉 / 结局分支都出了码;
  * World.tscn 里:怪物 / 巡逻点 / 3 张纸条 / 惊吓区 / 门口 都在。
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

PROBE_GD = '''extends Node

# 恐怖示例的探针:按 HORROR_ 前缀打印现象,Python 那边收集断言。
# ⚠ 结局 / 纸条数 / 心跳相关变量都在**玩家脚本**上(脚本挂在玩家身上)。

var world: Node = null
var player: Node = null
var enemy: Node = null
var elapsed := 0.0
var phase := 0
var enemy_start := Vector3.ZERO
var spots := {}          # 区域名 -> 它自己的世界坐标(避开手算坐标轴)


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	world = load("res://scenes/World.tscn").instantiate()
	add_child(world)
	player = world.find_child("玩家", true, false)
	enemy = world.find_child("怪物", true, false)
	for name in ["纸条1", "纸条2", "纸条3", "惊吓区", "门口"]:
		var area = world.find_child(name, true, false)
		if area != null:
			spots[name] = area.global_position


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
	# 站到区域正上方 1 米(角色有重力,几帧后落到地面 = 正好压住触发板)
	if not spots.has(name):
		return false
	_put(spots[name] + Vector3(0.0, 1.0, 0.0))
	return true


func _send_key(code: int, pressed: bool) -> void:
	var event := InputEventKey.new()
	event.keycode = code
	event.physical_keycode = code
	event.pressed = pressed
	Input.parse_input_event(event)


func _physics_process(delta: float) -> void:
	elapsed += delta
	match phase:
		0:
			if elapsed >= 0.2:
				phase = 1
				if enemy != null:
					enemy_start = enemy.global_position
				print("HORROR_START state=", _state(),
					" has_player=", player != null, " has_enemy=", enemy != null,
					" spots=", spots.size())
				# 离怪物远一点(但仍在场内)—— 紧张度该接近 0
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
				_put_at("纸条1")
		3:
			if elapsed >= 2.1:
				phase = 4
				_put_at("纸条2")
		4:
			if elapsed >= 2.7:
				phase = 5
				_put_at("纸条3")
		5:
			if elapsed >= 3.2:
				phase = 6
				print("HORROR_NOTES count=",
					snappedf(float(_var("纸条数")), 0.01))
				_put_at("惊吓区")
		6:
			if elapsed >= 3.55:
				phase = 7
				print("HORROR_SCARE shake=",
					snappedf(float(_var("_gk_shake_left")), 0.01),
					" bus=", snappedf(AudioServer.get_bus_volume_db(0), 0.01))
				_put_at("门口")
		7:
			if elapsed >= 4.2:
				phase = 8
				var panel = _var("_gk_choice_panel")
				print("HORROR_DOOR picked=", _var("_gk_choice_picked"),
					" panel=", panel != null and panel.visible)
				_send_key(KEY_2, true)
		8:
			if elapsed >= 4.4:
				phase = 9
				_send_key(KEY_2, false)
		9:
			if elapsed >= 4.9:
				phase = 10
				print("HORROR_ENDING ended=", _var("结局"),
					" saved=", FileAccess.file_exists("user://gk_save1.json"),
					" final_picked=", _var("_gk_choice_picked"))
				print("HORROR_DONE")
				set_physics_process(false)
				get_tree().quit()
'''

PROBE_TSCN = '''; 探针(临时文件,不写回示例)
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://probe.gd" id="1_probe"]

[node name="HorrorProbe" type="Node"]
script = ExtResource("1_probe")
'''


def prepare():
    if not os.path.isdir(SRC):
        print(f"找不到示例工程 {SRC} —— 先跑 make_horror_demo.py")
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
                          "--quit-after", "1800"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    return (run.stdout or "") + (run.stderr or "")


def _grab(text, pattern):
    match = re.search(pattern, text)
    return match.group(0) if match else "<没跑到>"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--godot", default="godot")
    args = parser.parse_args()
    if not prepare():
        return 1
    text = run_godot(args.godot)
    for line in text.splitlines():
        if "HORROR_" in line or "SCRIPT ERROR" in line or "Parse Error" in line:
            print(line.strip())

    ok = True
    if any(("SCRIPT ERROR" in line) or ("Parse Error" in line)
           for line in text.splitlines()):
        print("✗ Godot 里有脚本错误 / 解析错误")
        ok = False

    if not re.search(r"HORROR_START state=巡逻 has_player=true has_enemy=true"
                     r" spots=5", text):
        print("✗ 开场不对(状态不是巡逻 / 玩家 / 敌人 / 5 个触发区没找齐):",
              _grab(text, r"HORROR_START[^\n]*"))
        ok = False
    moved = re.search(r"HORROR_PATROL state=巡逻 moved=([\d.]+)", text)
    if not moved or float(moved.group(1)) < 0.5:
        print("✗ 怪物没有在巡逻(1 秒里位移 < 0.5 米):",
              _grab(text, r"HORROR_PATROL[^\n]*"))
        ok = False

    # ── 紧张度:远 ≈ 0,近 > 0.3,心跳播放器跟着建起来 ──────────────────
    far = re.search(r"HORROR_TENSION_FAR tension=([\d.]+)", text)
    if not far or float(far.group(1)) > 0.2:
        print("✗ 离怪物很远时紧张度没压下来:",
              _grab(text, r"HORROR_TENSION_FAR[^\n]*"))
        ok = False
    near = re.search(r"HORROR_TENSION_NEAR tension=([\d.]+) heart=(\w+)", text)
    if not near or float(near.group(1)) < 0.3:
        print("✗ 凑近怪物后紧张度没升起来:",
              _grab(text, r"HORROR_TENSION_NEAR[^\n]*"))
        ok = False
    elif near.group(2) != "true":
        print("✗ 心跳播放器没建起来:", near.group(0))
        ok = False

    notes = re.search(r"HORROR_NOTES count=([\d.]+)", text)
    if not notes or float(notes.group(1)) < 2.5:
        print("✗ 走过 3 张纸条后『纸条数』不是 3:",
              _grab(text, r"HORROR_NOTES[^\n]*"))
        ok = False

    # ── 惊吓:相机在震 + Master 被压低 ────────────────────────────────
    scare = re.search(r"HORROR_SCARE shake=([\-\d.]+) bus=([\-\d.]+)", text)
    if not scare:
        print("✗ 惊吓那段没跑出来:", _grab(text, r"HORROR_SCARE[^\n]*"))
        ok = False
    else:
        if float(scare.group(1)) <= 0.0:
            print("✗ 走过惊吓区但相机没在震:", scare.group(0))
            ok = False
        if float(scare.group(2)) > -10.0:
            print("✗ 『音效_总线音量』没有压低 Master:", scare.group(0))
            ok = False

    # ── 结局:门口弹选择(-1 = 还没选),按 2 -> 结局 2 + 真落盘 ────────
    door = re.search(r"HORROR_DOOR picked=([\-\d]+) panel=(\w+)", text)
    if not door or int(door.group(1)) != -1 or door.group(2) != "true":
        print("✗ 走到门口没有弹出结局选择(应该面板可见、还没选):",
              _grab(text, r"HORROR_DOOR[^\n]*"))
        ok = False
    ending = re.search(r"HORROR_ENDING ended=([\d.]+) saved=(\w+)",
                       text)
    if not ending or float(ending.group(1)) != 2.0:
        print("✗ 按 2 之后『结局』不是 2:",
              _grab(text, r"HORROR_ENDING[^\n]*"))
        ok = False
    elif ending.group(2) != "true":
        print("✗ 结局没有落盘(user://gk_save1.json 不存在):",
              ending.group(0))
        ok = False

    if "HORROR_DONE" not in text:
        print("✗ 探针没跑完(可能中途报错)")
        ok = False

    # ── 生成物层面的证据 ────────────────────────────────────────────
    script_path = os.path.join(OUT, "scripts", "game_logic.gd")
    if os.path.isfile(script_path):
        with open(script_path, "r", encoding="utf-8") as handle:
            code = handle.read()
        for needle, why in (
                ("var _gk_heart_tension: float", "心跳的紧张度成员变量"),
                ("get_physics_process_delta_time()", "心跳按物理帧计时"),
                ("lerpf(", "心跳越快越响(lerpf 插值)"),
                ("clampf(((", "紧张度的 0~1 夹取"),
                ("_gk_global_position_3d(", "紧张度用位置 reader 算距离"),
                ("_gk_shake_cam", "惊吓的相机震动"),
                ("ColorRect.new()", "惊吓的屏幕闪白"),
                ('load("res://audio/sting.wav")', "惊吓音效"),
                ("AudioServer.get_bus_index(", "总线音量(受惊压低)"),
                ("AudioServer.set_bus_volume_db(", "总线音量真的写了"),
                ("_gk_vignette", "屏幕暗角"),
                ('get_nodes_in_group("gk_ai_hearer")', "听觉广播"),
                ("func _gk_hear_noise(", "听觉派发器"),
                ('ai_state == "去查看":', "去查看行为"),
                ("match _gk_choice_picked:", "结局选择的分支 match"),
                ("gk_choice_input_event(event)", "数字键 1~6 也能选"),
                ("_gk_write_save(", "结局落盘"),
        ):
            if needle not in code:
                print(f"✗ 生成的脚本里没有{why}: 找不到 {needle!r}")
                ok = False
    else:
        print(f"✗ 没找到生成的脚本 {script_path}")
        ok = False

    scene_path = os.path.join(OUT, "scenes", "World.tscn")
    if os.path.isfile(scene_path):
        with open(scene_path, "r", encoding="utf-8") as handle:
            scene = handle.read()
        for needle, why in (
                ('[node name="怪物" type="CharacterBody3D"', "怪物在场景里"),
                ('name="巡逻点甲"', "巡逻点甲"),
                ('name="纸条1"', "纸条1"),
                ('name="纸条3"', "纸条3"),
                ('name="惊吓区"', "惊吓区"),
                ('name="门口"', "门口"),
                ('name="手电筒" type="SpotLight3D"', "手电筒(聚光)"),
        ):
            if needle not in scene:
                print(f"✗ World.tscn 里没有{why}: 找不到 {needle!r}")
                ok = False
    else:
        print(f"✗ 没找到主场景 {scene_path}")
        ok = False

    print("CHECK_HORROR_DEMO=" + ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())