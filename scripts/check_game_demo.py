"""小游戏示例的端到端检查(不用 Blender,要 Godot)。

用法:
    python scripts\\check_game_demo.py
    python scripts\\check_game_demo.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

它会:
  1. 把 `examples/demo_game/小游戏示例_godot` 拷到 `_dist/game_probe`
     (**不碰示例本身**);
  2. 在副本里加一个探针场景(`probe.gd` + `probe.tscn`),把主场景指过去;
  3. 用 Godot 无头跑,读探针打印的 DEMO_* 行,汇总 PASS / FAIL。

探针查的是"这一关该有的现象"(不是代码长什么样):
  * 开场:5 枚金币 / 巡逻敌人 / 追击敌人 / 火花发射器都在,HUD 建出来了;
  * **移动**:按住 W 一会儿,角色真的动了(角色预设那条链路);
  * **巡逻**:敌1 的位置随时间变化(时间轴往返驱动);
  * **追击**:敌2 会朝玩家靠近(『移动_朝向目标』的 velocity + move_and_slide);
  * **冷却/无敌帧**:贴着敌2 走 2.5 秒,扣血**只触发 1~2 次**
    (冷却 2 秒;没有冷却门的话是每帧触发,血量当场清零);
  * **拾取**:逐个走到金币上 -> 金币数 +1、金币从场上消失;
  * **HUD 不带 .0**:血量的 float 要靠『文本_数字』显示成"血量 2";
  * **胜利**:集满 5 枚 -> 屏幕上出现"全部收齐";
  * **受伤**:把角色挪到敌1 身上 -> 触发 1~2 次扣血(『流程_冷却』1.5 秒)
    —— 这一步放在收金币之后:胜负定了以后数值不再受两个敌人同时开打影响;
  * **反馈特效**(第二十六轮):挨打时玩家模型**闪红**(material_override
    是 unshaded 的 StandardMaterial3D)且**过后恢复**;"+1""-1" 的
    **飘字**(Label3D)在吃金币 / 受伤时都出现过;
  * **先放完再重复**(第二十七轮):重开之后连按 E 约 24 次(敌1 的抖动
    动画只 0.83 秒)—— 只该**开播一次**、不打断当前这一轮;等它放完
    再按一下,才接着播下一轮;
  * **重开**:按 R -> 场景重载:金币数回 0、金币回到场上、角色回出生点。
"""

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "examples", "demo_game", "小游戏示例_godot")
OUT = os.path.join(ROOT, "_dist", "game_probe")

PROBE_GD = '''extends Node

# 小游戏示例的探针:按时间线**驱动**这一关,并把现象打出来。
# 结论行统一用 DEMO_ 前缀,Python 那边按前缀收集、按内容断言。
#
# ── 为什么要 static ──────────────────────────────────────────────
# 按 R 会『重载场景』—— 而主场景就是这个探针场景,所以探针自己也会被
# 重新实例化(状态清零)。static 变量挂在脚本类上,重载后还在,
# 用它判断"这是重载之后的第二次启动",接着做重载后的断言。

static var run_count := 0

var world: Node = null
var host: Node = null              # 挂着游戏逻辑的脚本节点(读它的变量)
var ui_root: Node = null           # GKUI/Root(读 HUD 文本)
var spark: GPUParticles3D = null
var elapsed := 0.0
var phase := 0
# ⚠ 出生点是 **Godot 坐标**(Blender(0,-9,0.9) -> Godot(0, 0.9, 9.0))
var spawn := Vector3(0.0, 0.9, 9.0)
var moved := false
var patrol_moved := false
var hurt_ok := false
var chase := false                 # 受伤测试:贴着**敌1**走(触发区那条链)
var glue_chaser := false           # 冷却测试:贴着**敌2**走(追击那条链)
var picked := 0
var hud_ok := false
var clean_text := false            # HUD 上没有 ".0"(『文本_数字』)
var win_ok := false
var reload_ok := false
var chase_ok := false              # 敌2 真的在靠近
var cooldown_ok := false           # 一波接触里只掉一次血
# 第二十六轮的反馈特效(每帧采样):
var flash_seen := false            # 受击时玩家模型真的闪过红
var flash_restored := false        # 闪完恢复原样(没把卡通材质抹掉)
var float_coin := false            # 吃金币时出现过 "+1" 飘字
var float_hurt := false            # 受伤时出现过 "-1" 飘字
# 第二十七轮:『动画_播放』的"先放完再重复"(按 E 让敌1 抖一下)
var anim_player: AnimationPlayer = null
var anim_starts := 0               # animation_started 一共响了几次
var anim_before := 0
var spam_sent := 0                 # 连按 E 发了几次触发
var spam_plays := 0
var anim_ok := false
var enemy_a_start := Vector3.ZERO
var chase_start := 0.0
var hits_before := 0
var hp_before := 0
var coin_step := 0
var step_time := 0.0

# 收集金币的顺序:**先去四个角**,最后才去中间那个
# (中间那枚离敌1 的巡逻线最近,留到最后免得中途被撞)
# ⚠ 用**名字**而不是下标:金币吃一枚就消失一个,下标会整体前移(踩过:
# 收了 3 枚就再也对不上了)
const COIN_ORDER := ["金币1", "金币2", "金币4", "金币5", "金币3"]


func _ready() -> void:
	run_count += 1
	world = load("res://scenes/World.tscn").instantiate()
	add_child(world)
	for node in world.find_children("*", "", true, false):
		if node.get("金币数") != null:
			host = node
			break
	var ui := world.find_child("GKUI", true, false)
	if ui != null:
		ui_root = ui.find_child("Root", true, false)
	# ⚠ 按**类型**找:空物体发射器在 glb 里会留一个同名的 Node3D 占位节点
	# (glTF 把空物体也导成节点),只按名字找会先撞上那个占位节点。
	var sparks := world.find_children("金币火花", "GPUParticles3D", true, false)
	if not sparks.is_empty():
		spark = sparks[0]
	# ── 敌1 的动画播放器(第二十七轮)────────────────────────────
	# glb 里带动作时,Godot 导入会在**模型实例内部**放一个 AnimationPlayer;
	# 按 E 那条链就是靠它播"敌1抖动"。这里把它找出来数播放次数。
	anim_player = null
	anim_starts = 0
	var players := world.find_children("*", "AnimationPlayer", true, false)
	if not players.is_empty():
		anim_player = players[0] as AnimationPlayer
		anim_player.animation_started.connect(_on_anim_started)
	var fx_names: Array = []
	for node in world.find_children("*", "GPUParticles3D", true, false):
		fx_names.append(String(node.name))
	print("DEMO_GAME run=", run_count,
		" host=", host != null,
		" coins=", _coins().size(),
		" enemies=", _enemies().size(),
		" chaser=", _chaser() != null,
		" spark=", spark != null,
		" fx=", fx_names)


func _coins() -> Array:
	var out: Array = []
	for index in range(1, 6):
		var found := world.find_children("金币" + str(index), "Area3D", true, false)
		if not found.is_empty():
			out.append(found[0])
	return out


## 巡逻敌人(敌1):触发区(Area3D)
func _enemies() -> Array:
	var out: Array = []
	var found := world.find_children("敌1", "Area3D", true, false)
	if not found.is_empty():
		out.append(found[0])
	return out


## 追击敌人(敌2):**角色体**——它自己要 move_and_slide 追人
func _chaser() -> Node3D:
	var found := world.find_children("敌2", "CharacterBody3D", true, false)
	if found.is_empty():
		return null
	return found[0] as Node3D


## 数一次受伤链触发了几次:受伤提示会进**调试面板的日志缓冲**
## (设置里开着调试面板),数最近这些行里有几条是"被 敌X 碰到/追上"。
## 为什么不用血量数:血量掉到 0 以后就再也看不出"又触发了几次"了;
## 而且同一波接触里两个敌人可能同时在扣血,分开数才说得清。
func _hurt_hits(who: String) -> int:
	if host == null:
		return -1
	var logs = host.get("_gk_debug_logs")
	if logs == null:
		return -1
	var count := 0
	for line in logs:
		if String(line).contains(who):
			count += 1
	return count


func _hud(name: String) -> String:
	if ui_root == null:
		return "<no-ui>"
	var label := ui_root.find_child(name, true, false)
	return String(label.text) if label != null else "<none>"


func _hp() -> int:
	return int(host.get("血量")) if host != null else -999


func _coins_count() -> int:
	return int(host.get("金币数")) if host != null else -999


## 玩家的模型现在是不是"闪红中"(『特效_闪红』盖了一层 unshaded 的
## StandardMaterial3D)—— 卡通材质是 ShaderMaterial,两者一眼分得开。
func _flash_active() -> bool:
	if host == null:
		return false
	for mesh in host.find_children("*", "MeshInstance3D", true, false):
		var mat := (mesh as MeshInstance3D).material_override
		if mat is StandardMaterial3D and (mat as StandardMaterial3D).shading_mode == BaseMaterial3D.SHADING_MODE_UNSHADED:
			return true
	return false


## 每帧采样两样"一闪而过"的现象(错过这一帧就永远看不到):
##   * 玩家模型闪红(只亮 0.25 秒);
##   * 世界里的一次性飘字(Label3D,0.8 秒后自删)。
## 飘字挂在 current_scene 上 —— 而主场景就是探针自己,所以从 self 找。
func _sample_fx() -> void:
	if _flash_active():
		flash_seen = true
	for node in find_children("*", "Label3D", true, false):
		var shown := String((node as Label3D).text)
		if shown == "+1":
			float_coin = true
		elif shown == "-1":
			float_hurt = true


## 环境脚本把"碰撞代理"每帧对齐到同名网格(道具动了碰撞跟着走)。
## 追击敌人也是碰撞代理 —— 先把它和谁配了对打出来,排查很方便。
func _env_links() -> String:
	var env := world.find_child("Environment", true, false)
	if env == null:
		return "<no-env>"
	var links = env.get("_gk_body_links")
	if links == null:
		return "<no-links>"
	var parts: Array = []
	for pair in links:
		if not is_instance_valid(pair[0]) or not is_instance_valid(pair[1]):
			continue
		var mesh: Node3D = pair[0]
		var body: Node3D = pair[1]
		if String(body.name) not in ["敌1", "敌2"]:
			continue
		parts.append("%s(path=%s same=%s) <- %s(path=%s)" % [
			String(body.name), String(body.get_path()),
			str(mesh.global_transform == body.global_transform),
			String(mesh.name), String(mesh.get_path())])
	return " | ".join(parts)


func _send_key(code: int, pressed: bool) -> void:
	var event := InputEventKey.new()
	event.keycode = code
	event.physical_keycode = code
	event.pressed = pressed
	Input.parse_input_event(event)


## 离某个点**最远**的那个角落 —— 追击测试用:
## 先把角色放远,才能看出敌2 是不是真的在靠近(而不是已经贴着不动了)
func _far_corner(from: Vector3) -> Vector3:
	var best := Vector3(10.0, 1.0, 10.0)
	var best_dist := -1.0
	for x in [-10.0, 10.0]:
		for z in [-10.0, 10.0]:
			var spot := Vector3(x, 1.0, z)
			var dist := spot.distance_to(from)
			if dist > best_dist:
				best_dist = dist
				best = spot
	return best


func _physics_process(delta: float) -> void:
	elapsed += delta
	if run_count >= 2:
		_after_reload(delta)
		return
	# 受伤测试期间**每帧**把角色贴到敌人身上(敌人一直在动,只传送一次
	# 的话很可能只擦一下;实测"直接传送进去"给的 body_entered 还偏晚)
	if chase:
		var enemies := _enemies()
		if not enemies.is_empty() and host != null:
			(host as Node3D).global_position = (enemies[0] as Node3D).global_position
	if glue_chaser:
		var target := _chaser()
		if target != null and host != null:
			(host as Node3D).global_position = target.global_position
	_sample_fx()
	_phases(delta)


## 敌1 的动画每开播一次就 +1 —— "连按 E 只该开播一次"就是靠它数的。
func _on_anim_started(_name: String) -> void:
	anim_starts += 1


## 重载之后的第二条时间线:① 验"重开真的把状态清干净了"
## ② 验『动画_播放』的"先放完再重复"(按 E 让敌1 抖一下)。
##
## ── 为什么这一步放在重载之后 ────────────────────────────────────
## 前面那条时间线用掉了 11 秒,阶段安排已经排满;重开之后世界是干净的,
## 正好单独测这一段(而且 `elapsed` / `anim_starts` 都从 0 重新开始)。
func _after_reload(delta: float) -> void:
	if phase == 0 and elapsed >= 0.8:
		phase = 1
		var back := _coins().size()
		# ⚠ 必须显式写类型:`:=` 推不出"(bool and Variant)"的结果类型,
		# Godot 直接报 "Cannot infer the type of near variable"
		var near: bool = host != null and host.global_position.distance_to(spawn) < 3.0
		reload_ok = _coins_count() == 0 and _hp() == 3 and back == 5 and near
		print("DEMO_RELOAD ok=", reload_ok, " coins=", _coins_count(),
			" hp=", _hp(), " back_on_field=", back, " near_spawn=", near)
	elif phase == 1 and elapsed >= 1.0:
		phase = 2
		anim_before = anim_starts
		print("DEMO_ANIM_START player=", anim_player != null,
			" list=", anim_player.get_animation_list() if anim_player != null else "<none>")
	elif phase == 2:
		# ── 连按 E(1.0~1.4 秒之间**每帧**一下,约 24 次触发)──────────
		# 动画本体 0.83 秒 —— 这 0.4 秒的连按全都落在"正在播"的窗口里,
		# 所以按『先放完再说』的规矩:只开播**一次**,不打断当前这一轮。
		if elapsed < 1.4:
			spam_sent += 1
			_send_key(KEY_E, true)
			_send_key(KEY_E, false)
		elif elapsed >= 2.6:
			# 早该放完了(1.0 + 0.83 ≈ 1.83 秒)。这时再按一下 ——
			# 应该能接着播**下一轮**("放完可以再重复")。
			phase = 3
			spam_plays = anim_starts - anim_before
			_send_key(KEY_E, true)
			_send_key(KEY_E, false)
	elif phase == 3 and elapsed >= 3.3:
		phase = 4
		var total := anim_starts - anim_before
		anim_ok = (anim_player != null and spam_sent >= 10
			and spam_plays == 1 and total == 2)
		print("DEMO_ANIM ok=", anim_ok, " triggers=", spam_sent,
			" plays_during_spam=", spam_plays, " total_plays=", total,
			" now=", anim_player.current_animation if anim_player != null else "<none>")
		print("DEMO_DONE")
		set_physics_process(false)


func _phases(delta: float) -> void:
	# ① 移动:按住 W 一秒
	if phase == 0 and elapsed >= 0.3:
		phase = 1
		_send_key(KEY_W, true)
		if host != null and host is Node3D:
			print("DEMO_MOVE start=", (host as Node3D).global_position,
				" action=", Input.is_action_pressed("move_up"))
	elif phase == 1 and elapsed >= 1.3:
		phase = 2
		_send_key(KEY_W, false)
		var walk := -1.0
		var where := Vector3.ZERO
		if host != null and host is Node3D:
			where = (host as Node3D).global_position
			walk = where.distance_to(spawn)
		moved = walk > 0.8
		# 判定放最前面:Python 那边按 `DEMO_MOVE ok=true` 抓
		print("DEMO_MOVE ok=", moved, " walked=", "%.2f" % walk, " at=", where,
			" held=", Input.is_action_pressed("move_up"))
		# ② 巡逻:记下敌1 现在的位置,1 秒后再看
		var enemies := _enemies()
		if not enemies.is_empty():
			enemy_a_start = (enemies[0] as Node3D).global_position
	elif phase == 2 and elapsed >= 2.3:
		phase = 3
		var enemies := _enemies()
		var now := Vector3.ZERO
		if not enemies.is_empty():
			now = (enemies[0] as Node3D).global_position
		patrol_moved = now.distance_to(enemy_a_start) > 0.5
		print("DEMO_PATROL ok=", patrol_moved,
			" moved=", "%.2f" % now.distance_to(enemy_a_start))
		# ③ 拾取 + 胜利**先做**:收齐 5 枚之后胜负就定了(变量『结束』),
		# 后面再挨打也不会变成"失败" —— 三个敌人的测试才能各测各的
		var target := _chaser()
		if target != null and host != null:
			# 先把角色丢到离敌2 最远的角落,收金币的这两秒里它追不上来
			(host as Node3D).global_position = _far_corner(target.global_position)
	elif phase == 3:
		if elapsed >= 2.5:
			# 一枚一枚走过去(0.4 秒一枚)
			step_time += delta
			if coin_step < COIN_ORDER.size() and step_time >= 0.4:
				step_time = 0.0
				var found := world.find_children(COIN_ORDER[coin_step], "Area3D", true, false)
				if not found.is_empty() and host != null:
					(host as Node3D).global_position = (found[0] as Node3D).global_position
				coin_step += 1
			if coin_step >= COIN_ORDER.size() and step_time >= 0.4:
				phase = 4
				picked = _coins_count()
				# HUD 要跟着**变量**走(不是写死的数字):显示的就是当前血量
				hud_ok = _hud("血量HUD").contains(str(_hp()))
				# 『文本_数字』的意义:血量是 float,HUD 上不能出现 "2.0"
				clean_text = (not _hud("血量HUD").contains(".0")
					and not _hud("金币HUD").contains(".0"))
				print("DEMO_COINS picked=", picked, " left=", _coins().size(),
					" hud=", _hud("金币HUD"), " hp_hud=", _hud("血量HUD"),
					" hud_ok=", hud_ok, " clean_text=", clean_text)
				win_ok = _hud("胜利提示").contains("全部收齐") and _coins_count() == 5
				print("DEMO_WIN text=", _hud("胜利提示"), " ok=", win_ok)
	elif phase == 4 and elapsed >= 4.9:
		phase = 5
		# ④ 追击:把角色放到**离敌2 最远**的角落,看它会不会自己靠过来
		var target := _chaser()
		if target != null and host != null:
			(host as Node3D).global_position = _far_corner(target.global_position)
			chase_start = (host as Node3D).global_position.distance_to(target.global_position)
		print("DEMO_CHASE_START dist=", "%.2f" % chase_start,
			" chaser_at=", target.global_position if target != null else "<none>",
			" vel=", target.velocity if target is CharacterBody3D else "<no-vel>",
			" links=", _env_links())
	elif phase == 5 and elapsed >= 6.3:
		phase = 6
		var target := _chaser()
		var now := 0.0
		if target != null and host != null:
			now = (host as Node3D).global_position.distance_to(target.global_position)
		chase_ok = target != null and now < chase_start - 0.8
		print("DEMO_CHASE ok=", chase_ok, " from=", "%.2f" % chase_start,
			" to=", "%.2f" % now,
			" chaser_at=", target.global_position if target != null else "<none>",
			" vel=", target.velocity if target is CharacterBody3D else "<no-vel>",
			" links=", _env_links())
		# ⑤ 冷却/无敌帧(追击那条链):贴上去 2.5 秒,数一数触发了几次
		hits_before = _hurt_hits("敌2")
		hp_before = _hp()
		glue_chaser = true
	elif phase == 6 and elapsed >= 8.8:
		phase = 7
		glue_chaser = false
		var hits := _hurt_hits("敌2") - hits_before
		# 冷却 2.0 秒 + 窗口 2.5 秒 -> 一定**至少触发 1 次**,且**至多 2 次**。
		# 没有冷却门的话是每帧触发(几十次),血量当场清零。
		cooldown_ok = hits >= 1 and hits <= 2
		print("DEMO_COOLDOWN ok=", cooldown_ok, " hits=", hits,
			" hp=", _hp(), " hp_before=", hp_before)
		# ⑥ 受伤(触发区那条链):贴着敌1 走 2 秒
		hits_before = _hurt_hits("敌1")
		chase = true
	elif phase == 7 and elapsed >= 10.8:
		phase = 8
		chase = false
		var hits := _hurt_hits("敌1") - hits_before
		var conns := 0
		var enemy_areas := world.find_children("敌1", "Area3D", true, false)
		var over := 0
		var area_pos := Vector3.ZERO
		var player_pos := Vector3.ZERO
		if not enemy_areas.is_empty():
			conns = (enemy_areas[0] as Area3D).body_entered.get_connections().size()
			over = (enemy_areas[0] as Area3D).get_overlapping_bodies().size()
			area_pos = (enemy_areas[0] as Node3D).global_position
		if host != null:
			player_pos = (host as Node3D).global_position
		print("DEMO_HURT_DIAG area=", area_pos, " player=", player_pos,
			" overlapping=", over)
		# 受伤链前面挂着『流程_冷却』(1.5 秒):贴着走 2 秒只该触发 1~2 次
		# (没有冷却门的话是"每帧一次",2 秒能刷上百次)
		hurt_ok = hits >= 1 and hits <= 2 and _hp() < 3
		print("DEMO_HURT ok=", hurt_ok, " hp=", _hp(), " hits=", hits,
			" conns=", conns, " hud=", _hud("血量HUD"))
	elif phase == 8 and elapsed >= 11.4:
		phase = 9
		# 第二十六轮的反馈特效:最后一次挨打在 10.8 之前,闪红 0.25 秒就结束 ——
		# 等到 11.4 再看"恢复原样了没有"(没恢复 = 卡通材质被抹了)
		flash_restored = not _flash_active()
		print("DEMO_FX flash=", flash_seen, " restored=", flash_restored,
			" float_coin=", float_coin, " float_hurt=", float_hurt)
		_send_key(KEY_R, true)          # R = 重开(重载场景)
		_send_key(KEY_R, false)
'''

PROBE_TSCN = '''; 探针(临时文件,不写回示例)
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://probe.gd" id="1_probe"]

[node name="MiniGameProbe" type="Node"]
script = ExtResource("1_probe")
'''


def prepare():
    if not os.path.isdir(SRC):
        print(f"找不到示例工程 {SRC} —— 先跑 make_game_demo.py")
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
                          "--quit-after", "2400"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    return (run.stdout or "") + (run.stderr or "")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--godot", default=(
        r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"))
    args = parser.parse_args()
    if not prepare():
        return 1
    if not os.path.isfile(args.godot):
        print(f"跳过:找不到 Godot({args.godot})")
        return 0
    text = run_godot(args.godot)
    for line in text.splitlines():
        if "DEMO" in line or "SCRIPT ERROR" in line or "Parse Error" in line:
            print(line.strip())
    ok = True
    failed = any(("DEMO_FAIL" in line) or ("SCRIPT ERROR" in line)
                 or ("Parse Error" in line) for line in text.splitlines())
    if failed:
        print("✗ Godot 里有脚本错误 / 解析错误")
        ok = False

    # ── 现象断言 ────────────────────────────────────────────────────
    if not re.search(r"DEMO_GAME run=1 host=true coins=5 enemies=1 "
                     r"chaser=true spark=true", text):
        print("✗ 开场状态不对(金币 / 敌人 / 追击敌人 / 火花 / 脚本宿主):",
              [line for line in text.splitlines() if "DEMO_GAME" in line])
        ok = False
    if not re.search(r"DEMO_MOVE ok=true", text):
        print("✗ 按 W 之后角色没动(角色预设链路):",
              [line for line in text.splitlines() if "DEMO_MOVE" in line])
        ok = False
    if not re.search(r"DEMO_PATROL ok=true", text):
        print("✗ 敌1 没有在巡逻(时间轴往返链路):",
              [line for line in text.splitlines() if "DEMO_PATROL" in line])
        ok = False
    if not re.search(r"DEMO_HURT ok=true", text):
        print("✗ 碰到敌1 没有扣血 / 一波接触掉了不止一次(HUD 没跟着变):",
              [line for line in text.splitlines() if "DEMO_HURT" in line])
        ok = False
    if "hud_ok=true" not in text:
        print("✗ HUD 没有跟着变量走:",
              [line for line in text.splitlines() if "hud_ok=" in line])
        ok = False
    if "clean_text=true" not in text:
        print("✗ HUD 里出现了 .0(『文本_数字』没生效):",
              [line for line in text.splitlines() if "clean_text=" in line])
        ok = False
    if not re.search(r"DEMO_COINS picked=5 left=0 hud=金币 5", text):
        print("✗ 金币没有全部收掉 / 计数不对 / HUD 没跟上:",
              [line for line in text.splitlines() if "DEMO_COINS" in line])
        ok = False
    if not re.search(r"DEMO_WIN text=全部收齐!按 R 再来一次 ok=true", text):
        print("✗ 集满 5 枚没有出现胜利提示:",
              [line for line in text.splitlines() if "DEMO_WIN" in line])
        ok = False
    if not re.search(r"DEMO_CHASE ok=true", text):
        print("✗ 敌2 没有朝玩家靠近(『移动_朝向目标』):",
              [line for line in text.splitlines()
               if "DEMO_CHASE" in line or "DEMO_CHASE_START" in line])
        ok = False
    if not re.search(r"DEMO_COOLDOWN ok=true", text):
        print("✗ 一波接触里扣血触发次数不对(『流程_冷却』没生效):",
              [line for line in text.splitlines() if "DEMO_COOLDOWN" in line])
        ok = False
    if not re.search(r"DEMO_RELOAD ok=true", text):
        print("✗ 按 R 没有把这一关重开干净:",
              [line for line in text.splitlines() if "DEMO_RELOAD" in line])
        ok = False
    if not re.search(r"DEMO_FX flash=true restored=true "
                     r"float_coin=true float_hurt=true", text):
        print("✗ 受击 / 拾取的反馈特效不对(『特效_闪红』/『界面_飘字』):",
              [line for line in text.splitlines() if "DEMO_FX" in line])
        ok = False
    if not re.search(r"DEMO_ANIM ok=true", text):
        print("✗ 连按 E 打断了敌1 当前这一轮的动画(『动画_播放』→『先放完再说』):",
              [line for line in text.splitlines() if "DEMO_ANIM" in line])
        ok = False
    if "DEMO_DONE" not in text:
        print("✗ 探针没跑完(可能中途报错)")
        ok = False

    # ── 生成物层面的证据(不只是现象)────────────────────────────────
    script_path = os.path.join(OUT, "scripts", "game_logic.gd")
    if os.path.isfile(script_path):
        with open(script_path, "r", encoding="utf-8") as handle:
            code = handle.read()
        for needle, why in (
                # ⚠ 第二十六轮:血量 / 金币都勾了『限制范围』,所以这两句
                # 从 (…) 变成了 clampf((…), 下限, 上限)
                ("金币数 = int(clampf((金币数 + 1.0), 0.0, 5.0))",
                 "吃金币的计数(『变量_增减』+ 夹范围)"),
                # ⚠ 粒子那句的变量名是**复用的**(和『移动_设置位置』同一个
                # 目标节点 -> @onready 只声明一次),所以查 `…金币火花.restart()`
                ("金币火花.restart()", "吃金币时的粒子爆发"),
                ("reload_current_scene", "R 重开"),
                ("_gk_ui_金币HUD", "金币 HUD"),
                ("flash_time", "受伤闪白"),
                ("_gk_tl_tr1", "敌人巡逻用的时间轴轨道"),
                # 第二十五轮的四件套
                ("Time.get_ticks_msec() - _gk_cd_ticks", "无敌帧(『流程_冷却』)"),
                ("血量 = clampf((血量 + -1.0), 0.0, 3.0)",
                 "扣血 + 夹范围(『变量_增减』)"),
                ("_gk_num_text(血量)", "HUD 的数字转文字(『文本_数字』)"),
                ("节点_敌2.move_and_slide()", "追击敌人(『移动_朝向目标』)"),
                # 第二十六轮
                ("velocity.move_toward(", "追击的平滑加减速(『加速』)"),
                (".length() <= 1.2", "追击的『到达』输出(判'贴上了')"),
                ("func _gk_flash_3d(node: Node, color: Color, "
                 "seconds: float) -> void:", "受击闪红(『特效_闪红』)"),
                ("Label3D.new()", "飘字(『界面_飘字』)"),
                # 第二十七轮:先放完再重复
                # ⚠ 这里复用的是**别的节点已经声明过**的 `节点_敌1` 引用
                # (引用按名字去重),不是新造的 `anim_敌1` —— 所以查它。
                ("_gk_find_animation_player(节点_敌1)",
                 "从敌1 找到它的动画播放器(填物体名的那条路)"),
                ("current_animation != _anim", "先放完再说(在播就跳过)")):
            if needle not in code:
                print(f"✗ 生成的脚本里没有{why}: {needle}")
                ok = False
        # 回归:不能再出现"把物体本身当播放器"的那种写法(运行时会报
        # Nonexistent function 'has_animation' in base 'Area3D' 并断链)
        if re.search(r"var _ap\d+ := (节点_敌1|anim_敌1)\b", code):
            print("✗ 又把物体本身当动画播放器了(应该走 _gk_find_animation_player)")
            ok = False
    else:
        print("✗ 没找到生成脚本:", script_path)
        ok = False
    world_tscn = os.path.join(OUT, "scenes", "World.tscn")
    if os.path.isfile(world_tscn):
        with open(world_tscn, "r", encoding="utf-8") as handle:
            scene_text = handle.read()
        for needle, why in (
                ('[node name="金币火花" type="GPUParticles3D"', "金币火花发射器"),
                ('[node name="Colliders"', "碰撞代理容器"),
                ('id="env_script"', "环境脚本(上帝机 / 调试面板)"),
                # 追击敌人是**角色体**(它要自己 move_and_slide),不是触发区
                ('[node name="敌2" type="CharacterBody3D"', "追击敌人(角色体)")):
            if needle not in scene_text:
                print(f"✗ World.tscn 里没有{why}: {needle}")
                ok = False
    else:
        print("✗ 没找到主场景:", world_tscn)
        ok = False

    # ── 敌1 的对象动画真的导出来了(第二十七轮)────────────────────
    # Blender 里在敌1 上打的 keyframe 要进 World.glb,Godot 导入后才有
    # AnimationPlayer —— 这条链路以前**没有任何示例验证过**。
    glb_path = os.path.join(OUT, "art", "World.glb")
    if os.path.isfile(glb_path):
        with open(glb_path, "rb") as handle:
            raw = handle.read()
        json_length = struct.unpack("<I", raw[12:16])[0]
        payload = json.loads(raw[20:20 + json_length])
        names = [item.get("name") for item in payload.get("animations", [])]
        if "敌1抖动" not in names:
            print("✗ World.glb 里没有敌1 的动画(Blender 的对象动作没导出):",
                  names)
            ok = False
    else:
        print("✗ 没找到世界模型:", glb_path)
        ok = False

    print("CHECK_GAME_DEMO=" + ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())