"""AI 示例的自动检查(第四十一轮:游戏AI)。

用法:
    python scripts\\check_ai_demo.py
    python scripts\\check_ai_demo.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

它会:
  1. 把 `examples/demo_ai/AI示例_godot` 拷到 `_dist/ai_probe`(**不碰示例本身**);
  2. 在副本里加一个探针场景(`probe.gd` + `probe.tscn`),把主场景指过去;
  3. 用 Godot 无头跑,读探针打印的 AI_* 行,汇总 PASS / FAIL。

探针查的是"AI 该有的现象"(不是代码长什么样):
  * **起始状态 = 巡逻**(AI 图的起始状态 = 没人指向的那个);
  * 敌人真的**在走**(1 秒里位置变化 > 0.5 米)—— 巡逻行为在跑;
  * 把玩家送到敌人 5 米外 -> `ai_state` 变成**追击**,而且**真的追近了**
    (停下来时距离缩到 3 米以内);
  * 把玩家挪到 11 米外 -> `ai_state` 回到**巡逻**(追丢了自己回去);
  * **听觉(第五十七轮)**:把玩家放到噪音能传到的地方,**按住 K 弄出动静**
    -> `ai_state` 变成**去查看**,而且敌人**真的朝噪音点走过去**
    (距离缩了近、量出"朝噪音方向走"的位移);
  * **松开 K 之后**:等超过『记忆秒数』-> `ai_state` 回到**巡逻**
    (安静下来了,不查了);
  * 生成脚本里:状态变量 / 起始赋值 / 看见-丢失的距离条件 / 巡逻折返 /
    听觉三件套(广播 / 登记进组 / 去查看朝噪音点);
  * .tscn 里:'敌人' 是 CharacterBody3D、两个巡逻点都在(静态体)。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "examples", "demo_ai", "AI示例_godot")
OUT = os.path.join(ROOT, "_dist", "ai_probe")

PROBE_GD = '''extends Node

# AI 示例的探针:看敌人会不会"巡逻 -> 看见追 -> 追丢回巡逻"。
# 结论行统一用 AI_ 前缀,Python 那边按前缀收集、按内容断言。
#
# ⚠ `ai_state` 在**玩家脚本**上(『AI_启用』的『作用在谁』= 敌人,
#   但代码生成在"挂脚本的那个物体" = 玩家)。所以状态从 player 读,
#   位置从 enemy 读。

var world: Node = null
var player: Node = null
var enemy: Node = null
var elapsed := 0.0
var phase := 0
var start_pos := Vector3.ZERO
# 听觉那一段:噪音点的位置、以及"开始朝它走"之前的敌人位置
var noise_at := Vector3.ZERO
var noise_start := Vector3.ZERO


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	world = load("res://scenes/World.tscn").instantiate()
	add_child(world)
	player = world.find_child("玩家", true, false)
	enemy = world.find_child("敌人", true, false)


func _state() -> String:
	if player == null:
		return "<no-player>"
	return str(player.get("ai_state"))


func _physics_process(delta: float) -> void:
	elapsed += delta
	if phase == 0 and elapsed >= 0.15:
		phase = 1
		if enemy != null:
			start_pos = enemy.global_position
		print("AI_START state=", _state(),
			" has_player=", player != null, " has_enemy=", enemy != null)
	elif phase == 1 and elapsed >= 1.05:
		phase = 2
		var moved := 0.0
		if enemy != null:
			moved = enemy.global_position.distance_to(start_pos)
		print("AI_PATROL state=", _state(), " moved=", snappedf(moved, 0.01))
		if player != null and enemy != null:
			# 把玩家放到敌人 5 米外(9 米视野内)-> 应该转追击,
			# 而且因为停距是 1.6,敌人会真的追近一段
			player.global_position = enemy.global_position \\
				+ Vector3(5.0, 0.0, 0.0)
	elif phase == 2 and elapsed >= 1.85:
		phase = 3
		var dist := -1.0
		if player != null and enemy != null:
			dist = enemy.global_position.distance_to(player.global_position)
		print("AI_CHASE state=", _state(), " dist=", snappedf(dist, 0.01))
		if player != null:
			# 挪到 11 米外(超出丢失距离 11)-> 应该回到巡逻
			player.global_position = Vector3(-11.0, 1.0, -11.0)
	elif phase == 3 and elapsed >= 2.75:
		phase = 4
		print("AI_LOST state=", _state())
		# ── 听觉:把玩家放到 13 米外 —— **看不见**(视野 9)但**听得见**
		# (响度 16)。这个窗口是故意留的:看得见的话敌人会直接切『追击』,
		# 听觉那条路就轮不到了(踩过:一开始放 6 米,结果直接追过来)。
		if player != null and enemy != null:
			player.global_position = enemy.global_position \\
				+ Vector3(13.0, 0.0, 0.0)
			noise_start = enemy.global_position
			noise_at = player.global_position
		Input.action_press("key_k")
	elif phase == 4 and elapsed >= 4.15:
		phase = 5
		# 这一秒里敌人应该"听见动静 -> 去查看",并且朝噪音点走过去
		Input.action_release("key_k")
		var came := -1.0
		if enemy != null and noise_at != Vector3.ZERO:
			came = enemy.global_position.distance_to(noise_at)
		var walked := 0.0
		if enemy != null:
			walked = enemy.global_position.distance_to(noise_start)
		print("AI_HEARD state=", _state(), " to_noise=", snappedf(came, 0.01),
			" walked=", snappedf(walked, 0.01))
		# ⚠ 把玩家挪到很远处再等"安静下来" —— 不挪的话,敌人一边往噪音点
		#   走一边就走进 9 米视野了,会切『追击』,测不到『安靜回巡逻』。
		if player != null:
			player.global_position = Vector3(-40.0, 1.0, -40.0)
	elif phase == 5 and elapsed >= 5.75:
		phase = 6
		# 松手 + 超过记忆秒数(1.2 秒)-> 应该"安静下来"回巡逻
		print("AI_CALM state=", _state())
		print("AI_DONE")
		set_physics_process(false)
		get_tree().quit()
'''

PROBE_TSCN = '''; 探针(临时文件,不写回示例)
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://probe.gd" id="1_probe"]

[node name="AiProbe" type="Node"]
script = ExtResource("1_probe")
'''


def prepare():
    if not os.path.isdir(SRC):
        print(f"找不到示例工程 {SRC} —— 先跑 make_ai_demo.py")
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
        if "AI_" in line or "SCRIPT ERROR" in line or "Parse Error" in line:
            print(line.strip())

    ok = True
    if any(("SCRIPT ERROR" in line) or ("Parse Error" in line)
           for line in text.splitlines()):
        print("✗ Godot 里有脚本错误 / 解析错误")
        ok = False

    if not re.search(r"AI_START state=巡逻 has_player=true has_enemy=true", text):
        print("✗ 起始状态不是『巡逻』/ 玩家敌人没找到:",
              [line for line in text.splitlines() if line.startswith("AI_START")])
        ok = False
    if not re.search(r"AI_PATROL state=巡逻", text):
        print("✗ 巡逻状态没保持住:",
              [line for line in text.splitlines() if line.startswith("AI_PATROL")])
        ok = False
    moved = re.search(r"AI_PATROL state=巡逻 moved=([\d.]+)", text)
    if not moved or float(moved.group(1)) < 0.5:
        print("✗ 敌人没有在巡逻走(1 秒里位移 < 0.5 米):",
              moved.group(0) if moved else "<没跑到>")
        ok = False
    if not re.search(r"AI_CHASE state=追击", text):
        print("✗ 玩家靠近后没切到追击:",
              [line for line in text.splitlines() if line.startswith("AI_CHASE")])
        ok = False
    dist = re.search(r"AI_CHASE state=追击 dist=([\d.]+)", text)
    if not dist or float(dist.group(1)) > 3.5:
        print("✗ 切到追击后没有真的追近(停在 3.5 米外):",
              dist.group(0) if dist else "<没跑到>")
        ok = False
    if not re.search(r"AI_LOST state=巡逻", text):
        print("✗ 跟丢后没回到巡逻:",
              [line for line in text.splitlines() if line.startswith("AI_LOST")])
        ok = False
    # ── 听觉:听见动静 -> 去查看 -> 真的走过去 -> 安静回巡逻 ────────────
    heard = re.search(r"AI_HEARD state=(\S+) to_noise=([\d.]+) walked=([\d.]+)",
                      text)
    if not heard:
        print("✗ 听觉那段没跑出来:",
              [line for line in text.splitlines() if line.startswith("AI_HEARD")])
        ok = False
    else:
        if heard.group(1) != "去查看":
            print(f"✗ 弄出动静后没切到『去查看』(状态是 {heard.group(1)}):",
                  heard.group(0))
            ok = False
        # 噪音点在 13 米外,走过去之后应该明显更近了(查看速度 4 米/秒,
        # 1.4 秒足够走 3 米以上;留足余量判 1 米)
        if float(heard.group(3)) < 1.0:
            print(f"✗ 敌人听见动静但没有朝噪音点走(位移只有 "
                  f"{heard.group(3)} 米):", heard.group(0))
            ok = False
    if not re.search(r"AI_CALM state=巡逻", text):
        print("✗ 松手 + 过了『记忆秒数』后没回到巡逻:",
              [line for line in text.splitlines() if line.startswith("AI_CALM")])
        ok = False

    if "AI_DONE" not in text:
        print("✗ 探针没跑完(可能中途报错)")
        ok = False

    # ── 生成物层面的证据 ────────────────────────────────────────────
    script_path = os.path.join(OUT, "scripts", "game_logic.gd")
    if os.path.isfile(script_path):
        with open(script_path, "r", encoding="utf-8") as handle:
            code = handle.read()
        for needle, why in (
                ("var ai_state: String", "状态变量"),
                ("if ai_state.is_empty():", "起始状态只在进游戏时设一次"),
                ('ai_state = "巡逻"', "起始状态赋值"),
                ('if ai_state == "巡逻":', "巡逻行为"),
                ('elif ai_state == "追击":', "追击行为"),
                (".length() <= 9.0", "『看见目标』的距离条件"),
                (".length() > 11.0", "『丢失目标』的距离条件"),
                ("_gk_ai_patrol_1 = not _gk_ai_patrol_1", "巡逻到点折返"),
                ("[AI] 切到状态", "调试打印(『打印状态变化』)"),
                # ── 听觉三件套(第五十七轮)───────────────────────────
                ('get_nodes_in_group("gk_ai_hearer")', "噪音广播到听觉体组"),
                ('has_method("_gk_hear_noise")', "广播前先确认对方能收"),
                ('add_to_group("gk_ai_hearer")', "敌人把自己登记成听觉体"),
                ("func _gk_hear_noise(", "接收噪音的派发器(名字固定)"),
                ("func _gk_heard_", "『听得见吗』判据"),
                ('ai_state == "去查看":', "去查看行为"),
                ("ai_state_hear_pos", "『去查看』朝记住的噪音点走"),
                ("not _gk_heard_", "『安静下来』= 听不见"),
                ("[GK 噪音]", "噪音的调试打印"),
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
                ('[node name="敌人" type="CharacterBody3D"', "敌人在场景里"),
                ('name="巡逻点甲"', "巡逻点甲在场景里"),
                ('name="巡逻点乙"', "巡逻点乙在场景里"),
        ):
            if needle not in scene:
                print(f"✗ World.tscn 里没有{why}: 找不到 {needle!r}")
                ok = False
    else:
        print(f"✗ 没找到主场景 {scene_path}")
        ok = False

    print("CHECK_AI_DEMO=" + ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())