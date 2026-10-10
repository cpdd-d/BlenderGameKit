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
  * **攻击(第七十九轮)**:继续贴身 -> `ai_state` 变成**攻击**,
    并且**真的打到了**(玩家脚本上的『被打次数』变大了 —— 那是挂在
    状态『进入时』出口上的『变量_增减』干的);
  * 把玩家挪到 11 米外 -> `ai_state` 回到**巡逻**(追丢了自己回去);
  * **听觉(第五十七轮)**:把玩家放到噪音能传到的地方,**按住 K 弄出动静**
    -> `ai_state` 变成**去查看**,而且敌人**真的朝噪音点走过去**
    (距离缩了近、量出"朝噪音方向走"的位移);
  * **松开 K 之后**:敌人走到噪音点**原地环顾 ±70°(2 秒,朝向真的左右摆)**,
    环顾中 `ai_state` 必须仍是**去查看**;看完 + 安静才回**巡逻**;
  * **噪音类型(第八十轮)**:广播『枪声』(敌人只听脚步)状态不变,
    再广播『脚步』立刻切**去查看**;
  * **脚下响度(第八十轮)**:玩家出生踩铁皮 -> 射线读回 **2.0**,
    普通地面读回默认 **1.0**;`.tscn` 里写了 metadata;
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
#
# 第八十轮新增四段:
#   AI_SURFACE_TIN / AI_SURFACE_GROUND —— 脚下响度射线(铁皮 2.0 / 普通 1.0)
#   AI_LOOK_SWING / AI_LOOK_MID       —— 到点环顾:摆头 + 环顾中不许回岗
#   AI_CALM                           —— 看完(2 秒)+ 安静才回巡逻
#   AI_TYPE_WRONG / AI_TYPE_RIGHT     —— 只听『脚步』:枪声不理、脚步惊动

var world: Node = null
var player: Node = null
var enemy: Node = null
var elapsed := 0.0
var phase := 0
var start_pos := Vector3.ZERO
# 听觉那一段:噪音点的位置、以及"开始朝它走"之前的敌人位置
var noise_at := Vector3.ZERO
var noise_start := Vector3.ZERO
# 三点巡逻(第七十八轮):把敌人挪到当前那个点上,索引应该前进 1
var wp_before := -1
var wp_after := -2
# 攻击(第七十九轮):进追击前记一下"被打次数",开打后再记一次,要变大
var hits_before := -1.0
var hits_after := -2.0
# 脚下响度(第八十轮)
var surface_tin_done := false
var surface_ground_done := false
var surface_final_done := false
# 到点环顾(第八十轮)
var look_arrived := false
var look_t := 0.0
var rot_a := 0.0
var rot_b := 0.0
var swing_done := false
var mid_done := false
# 类型过滤(第八十轮)
var type_t := 0.0


func _wp() -> int:
	# ⚠ 成员变量在**玩家脚本**上(代码生成在挂脚本的物体上,见文件头)
	if player == null:
		return -1
	return int(player.get("_gk_ai_wp_index_1"))


func _hits() -> float:
	# 『被打次数』也是玩家脚本上的成员变量(『变量_增减』在 AI 状态的
	# 『进入时』出口里,AI 代码就生成在玩家脚本里)。
	if player == null:
		return -1.0
	return float(player.get("被打次数"))


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
	# ── 脚下响度(第八十轮)──────────────────────────────────────────
	# ⚠ 必须赶在 1.05 秒的巡逻阶段之前、并且在**同一帧**内测完:
	#   敌人视野 9 米,把玩家低空放到铁皮 (距巡逻点甲 8.25 米) 或原点
	#   (6 米)只要停留超过一帧就会被看见、提前切追击(实测踩过)。
	#   所以在 10 米高空两处各测一次(射线长度放到 12),同帧再停到
	#   11 米外的死角 —— 高空 3D 距离 > 视野,敌人当帧无论如何看不见。
	if not surface_tin_done and elapsed >= 0.30:
		surface_tin_done = true
		surface_ground_done = true
		surface_final_done = true
		if player != null:
			# 铁皮地面 Godot 坐标 (-8, +8),顶面 y=0.1,倍率 2.0
			player.global_position = Vector3(-8.0, 10.0, 8.0)
			var tin: float = player._gk_surface_loudness_3d(player, 12.0)
			print("AI_SURFACE_TIN value=", snappedf(tin, 0.01))
			# 原点正上方是普通『地面』,无 metadata,读回默认 1.0
			player.global_position = Vector3(0.0, 10.0, 0.0)
			var ground: float = player._gk_surface_loudness_3d(player, 12.0)
			print("AI_SURFACE_GROUND value=", snappedf(ground, 0.01))
			# 测完停远:11 米外死角,等 1.05 秒阶段重新摆局
			player.global_position = Vector3(-11.0, 1.0, -11.0)
	if phase == 0 and elapsed >= 0.15:
		phase = 1
		# ── 三点巡逻(第七十八轮)────────────────────────────────────
		# 把敌人**直接挪到当前那个巡逻点**上(甲是列表里的第 0 个)->
		# 下一帧就该"到了 -> 索引 +1"。
		# ⚠ 必须在**玩家还远**的时候挪:同一帧里挪敌人 + 把玩家挪近的话,
		#   状态当帧就切『追击』了,巡逻的"到点"根本轮不到(实测踩过)。
		wp_before = _wp()
		var spot := world.find_child("巡逻点甲", true, false)
		if enemy != null and spot != null:
			enemy.global_position = (spot as Node3D).global_position
		if enemy != null:
			# 位移基准记在**挪完**之后 —— 否则量到的是这一下瞬移
			start_pos = enemy.global_position
		print("AI_START state=", _state(),
			" has_player=", player != null, " has_enemy=", enemy != null)
	elif phase == 1 and elapsed >= 1.05:
		phase = 2
		var moved := 0.0
		if enemy != null:
			moved = enemy.global_position.distance_to(start_pos)
		wp_after = _wp()
		print("AI_PATROL state=", _state(), " moved=", snappedf(moved, 0.01))
		print("AI_WP before=", wp_before, " after=", wp_after)
		if player != null and enemy != null:
			# 把玩家放到敌人 5 米外(9 米视野内)-> 应该转追击,
			# 而且因为停距是 1.6,敌人会真的追近一段
			player.global_position = enemy.global_position \\
				+ Vector3(5.0, 0.0, 0.0)
			# 记下"还没被打到"的次数(攻击要在这个基准上变大)
			hits_before = _hits()
	elif phase == 2 and elapsed >= 1.85:
		phase = 3
		var dist := -1.0
		if player != null and enemy != null:
			dist = enemy.global_position.distance_to(player.global_position)
		# ⚠ 这一帧状态可能是『追击』也可能是**已经贴近切成的『攻击』**
		#   (追击速度 4.6,0.8 秒就够从 5 米走到 2.5 米开打)—— 两个都算过,
		#   关键看"真的追近了"(dist 缩到 3.5 米以内)。
		print("AI_CHASE state=", _state(), " dist=", snappedf(dist, 0.01))
	elif phase == 3 and elapsed >= 2.85:
		phase = 4
		# ── 攻击(第七十九轮)────────────────────────────────────────
		# 这一秒里敌人应该已经贴身开打:状态 = 攻击,『被打次数』变大。
		hits_after = _hits()
		var at_dist := -1.0
		if player != null and enemy != null:
			at_dist = enemy.global_position.distance_to(player.global_position)
		print("AI_ATTACK state=", _state(),
			" hits_before=", snappedf(hits_before, 0.01),
			" hits_after=", snappedf(hits_after, 0.01),
			" dist=", snappedf(at_dist, 0.01))
		if player != null:
			# 挪到 11 米外(超出丢失距离 11)-> 攻击 -> 追击 -> 巡逻
			player.global_position = Vector3(-11.0, 1.0, -11.0)
	elif phase == 4 and elapsed >= 3.75:
		phase = 5
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
	elif phase == 5 and elapsed >= 4.35:
		phase = 6
		# 0.6 秒的脚步声足够它切『去查看』并朝噪音点走出一段
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
	elif phase == 6:
		# ── 等它走到噪音点(停距 1 米;1.6 米内就算到达),再看环顾 ─────
		if not look_arrived and enemy != null and noise_at != Vector3.ZERO \\
				and enemy.global_position.distance_to(noise_at) <= 1.6:
			look_arrived = true
			look_t = elapsed
			rot_a = enemy.rotation.y
		if look_arrived:
			if not swing_done and elapsed - look_t >= 0.55:
				# 环顾是 ±70° 正弦:0.55 秒时该明显偏离到达时的朝向
				swing_done = true
				rot_b = enemy.rotation.y
				print("AI_LOOK_SWING state=", _state(),
					" rot_a=", snappedf(rot_a, 0.001),
					" rot_b=", snappedf(rot_b, 0.001))
			if not mid_done and elapsed - look_t >= 1.0:
				# 环顾要 2 秒:1 秒时『安静下来』必须不成立(还在去查看)
				mid_done = true
				print("AI_LOOK_MID state=", _state())
			if swing_done and mid_done and elapsed - look_t >= 3.6:
				# 环顾 2 秒 + 记忆 1.2 秒 + 余量 -> 该回巡逻了
				print("AI_CALM state=", _state())
				phase = 7
				type_t = elapsed
				# 类型过滤:先广播一声**枪声**(敌人只听脚步,不该动)
				if player != null and enemy != null:
					player._gk_hear_noise(
						enemy.global_position + Vector3(10.0, 0.0, 0.0),
						16.0, "枪声")
		if not look_arrived and elapsed >= 12.5:
			# 超时也把后面的阶段走完,让失败行打全(别静默卡住)
			print("AI_LOOK_SWING state=TIMEOUT rot_a=0 rot_b=0")
			print("AI_LOOK_MID state=TIMEOUT")
			print("AI_CALM state=TIMEOUT")
			phase = 7
			type_t = elapsed
	elif phase == 7 and elapsed - type_t >= 0.5:
		phase = 8
		print("AI_TYPE_WRONG state=", _state())
		# 再广播一声**脚步** —— 这次必须切去查看
		if player != null and enemy != null:
			player._gk_hear_noise(
				enemy.global_position + Vector3(10.0, 0.0, 0.0),
				16.0, "脚步")
		type_t = elapsed
	elif phase == 8 and elapsed - type_t >= 0.4:
		print("AI_TYPE_RIGHT state=", _state())
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
    chase = re.search(r"AI_CHASE state=(\S+) dist=([\d.]+)", text)
    # ⚠ 这一帧状态可能是『追击』,也可能**已经贴近切成了『攻击』**
    #   (追击速度 4.6,0.8 秒就够从 5 米走到 2.5 米开打)—— 两个都算过。
    if not chase or chase.group(1) not in ("追击", "攻击"):
        print("✗ 玩家靠近后没切到追击(或紧跟着的攻击):",
              [line for line in text.splitlines() if line.startswith("AI_CHASE")])
        ok = False
    elif float(chase.group(2)) > 3.5:
        print(f"✗ 切到追击后没有真的追近(停在 3.5 米外):", chase.group(0))
        ok = False
    # ── 攻击:贴身 -> 状态 = 攻击,而且『被打次数』真的变大了(第七十九轮)──
    atk = re.search(r"AI_ATTACK state=(\S+) hits_before=([\d.-]+) "
                    r"hits_after=([\d.-]+) dist=([\d.]+)", text)
    if not atk:
        print("✗ 攻击那段没跑出来:",
              [line for line in text.splitlines() if line.startswith("AI_ATTACK")])
        ok = False
    else:
        if atk.group(1) != "攻击":
            print(f"✗ 贴身之后没切到『攻击』(状态是 {atk.group(1)}):",
                  atk.group(0))
            ok = False
        if float(atk.group(3)) <= float(atk.group(2)):
            print(f"✗ 进了攻击状态却没有真的打一下(『被打次数』没变大):",
                  atk.group(0))
            ok = False
    if not re.search(r"AI_LOST state=巡逻", text):
        print("✗ 跟丢后没回到巡逻:",
              [line for line in text.splitlines() if line.startswith("AI_LOST")])
        ok = False
    # ── 三点巡逻:把敌人挪到当前那个点上,索引要前进 1(第七十八轮)────────
    wp = re.search(r"AI_WP before=(-?\d+) after=(-?\d+)", text)
    if not wp or int(wp.group(2)) != int(wp.group(1)) + 1:
        print("✗ 三点巡逻的索引没有前进(挪到当前巡逻点后应该 +1):",
              wp.group(0) if wp else "<没跑到>")
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
        print("✗ 环顾看完 + 安静后没回到巡逻:",
              [line for line in text.splitlines() if line.startswith("AI_CALM")])
        ok = False

    # ── 脚下响度(第八十轮):铁皮 2.0,普通地面默认 1.0 ───────────────
    tin = re.search(r"AI_SURFACE_TIN value=([-\d.]+)", text)
    if not tin:
        print("✗ 脚下响度(铁皮)那段没跑出来:",
              [line for line in text.splitlines()
               if line.startswith("AI_SURFACE_TIN")])
        ok = False
    elif abs(float(tin.group(1)) - 2.0) > 0.05:
        print(f"✗ 铁皮地面的响度倍率应为 2.0,读到 {tin.group(1)}:", tin.group(0))
        ok = False
    ground = re.search(r"AI_SURFACE_GROUND value=([-\d.]+)", text)
    if not ground:
        print("✗ 脚下响度(普通地面)那段没跑出来:",
              [line for line in text.splitlines()
               if line.startswith("AI_SURFACE_GROUND")])
        ok = False
    elif abs(float(ground.group(1)) - 1.0) > 0.05:
        print(f"✗ 普通地面的响度倍率应退回默认 1.0,读到 {ground.group(1)}:",
              ground.group(0))
        ok = False

    # ── 到点环顾(第八十轮):摆头 + 环顾期间不许回岗 ─────────────────
    swing = re.search(
        r"AI_LOOK_SWING state=(\S+) rot_a=([-\d.]+) rot_b=([-\d.]+)", text)
    if not swing:
        print("✗ 环顾那段没跑出来:",
              [line for line in text.splitlines()
               if line.startswith("AI_LOOK_SWING")])
        ok = False
    else:
        if swing.group(1) != "去查看":
            print(f"✗ 到点环顾时状态应仍是『去查看』(实际 {swing.group(1)}):",
                  swing.group(0))
            ok = False
        if abs(float(swing.group(3)) - float(swing.group(2))) < 0.1:
            print("✗ 环顾时没有真的摆头(两个朝向差 < 0.1 弧度):",
                  swing.group(0))
            ok = False
    mid = re.search(r"AI_LOOK_MID state=(\S+)", text)
    if not mid:
        print("✗ 环顾中段那行没跑出来:",
              [line for line in text.splitlines()
               if line.startswith("AI_LOOK_MID")])
        ok = False
    elif mid.group(1) != "去查看":
        print(f"✗ 环顾才过 1 秒就离开了『去查看』(环顾秒数=2,状态 {mid.group(1)}):"
              "『安静下来』被环顾门控放早了", mid.group(0))
        ok = False

    # ── 噪音类型过滤(第八十轮):枪声不理、脚步惊动 ──────────────────
    wrong = re.search(r"AI_TYPE_WRONG state=(\S+)", text)
    if not wrong:
        print("✗ 类型不匹配那段没跑出来:",
              [line for line in text.splitlines()
               if line.startswith("AI_TYPE_WRONG")])
        ok = False
    elif wrong.group(1) != "巡逻":
        print(f"✗ 敌人只听『脚步』,却被一声『枪声』惊动了(状态 {wrong.group(1)}):",
              wrong.group(0))
        ok = False
    right = re.search(r"AI_TYPE_RIGHT state=(\S+)", text)
    if not right:
        print("✗ 类型匹配那段没跑出来:",
              [line for line in text.splitlines()
               if line.startswith("AI_TYPE_RIGHT")])
        ok = False
    elif right.group(1) != "去查看":
        print(f"✗ 广播了『脚步』声却没切去查看(状态 {right.group(1)}):",
              right.group(0))
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
                # ── 三个点以上的巡逻(第七十八轮)───────────────────────
                # 老的两点翻面(`_gk_ai_patrol_1 = not ...`)在三点图上**不生成**,
                # 换成"索引 + 方向"那套
                ("var _gk_ai_wp_index_1: int = 0", "三点巡逻的索引变量"),
                ("_gk_ai_wp_index_1 >= 2", "走到头上就掉头"),
                ("_gk_ai_wp_index_1 -= 1", "回头时索引往回走"),
                ("巡逻点丙", "第三个巡逻点进了引用表"),
                ("[AI] 切到状态", "调试打印(『打印状态变化』)"),
                # ── 攻击 + 状态的『进入时』出口(第七十九轮)─────────────
                ("var _gk_ai_atk_1: float = 0.0", "攻击的冷却计时器"),
                ("_gk_ai_atk_1 -= delta", "冷却在数秒"),
                ('ai_state == "攻击":', "攻击行为"),
                ("func _gk_ai_enter_攻击() -> void:", "状态的『进入时』钩子函数"),
                ("被打次数", "钩子里给『被打次数』加一(『变量_增减』)"),
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
                # ── 噪音类型(第八十轮 D-12①)────────────────────────
                ('noise_type: String', "派发器带类型参数"),
                ('want_type: String', "听方带『只听类型』参数"),
                ('if want_type != "":', "类型过滤分支"),
                ('"脚步"', "噪音和转场都用『脚步』这个类型"),
                # ── 到点环顾(第八十轮 D-12③)────────────────────────
                ("_gk_inv_done_", "环顾完成门控变量"),
                ("deg_to_rad(70.0)", "环顾角度 70°"),
                ("sin(TAU * ", "正弦摆头"),
                # ── 脚下响度(第八十轮 D-12②)────────────────────────
                ("func _gk_surface_loudness_3d(", "脚下响度 helper"),
                ('get_meta("gk_surface_loudness", 1.0)', "读地面 metadata"),
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
                # ── 地面响度 metadata(第八十轮 D-12②)────────────────
                ('name="铁皮地面"', "铁皮地面在场景里"),
                ('name="地毯地面"', "地毯地面在场景里"),
                ("metadata/gk_surface_loudness = 2.000000",
                 "铁皮倍率 2.0 写进了碰撞体 metadata"),
                ("metadata/gk_surface_loudness = 0.300000",
                 "地毯倍率 0.3 写进了碰撞体 metadata"),
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