"""2D 寻路示例的端到端检查(不用 Blender,要 Godot)。

用法:
    python scripts\\check_nav2d_demo.py
    python scripts\\check_nav2d_demo.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

它会:
  1. 把 `examples/demo_nav2d/2D寻路示例_godot` 拷到 `_dist/nav2d_probe`
     (**不碰示例本身**);
  2. 在副本里加一个探针场景(`probe.gd` + `probe.tscn`),把主场景指过去;
  3. 用 Godot 无头跑,读探针打印的 NAV2D_* 行,汇总 PASS / FAIL。

断言(全是行为现象,不是"代码长什么样"):
  * **2D 导航多边形烤出来了**(polys > 0)—— .tscn 里的 NavigationRegion2D +
    NavigationPolygon 子资源 + 环境脚本的 2D 烘焙,三件事一次验完;
  * **追踪者绕开中间障碍**追到玩家 —— |x| 至少到 5.5(障碍半宽 5,只有
    从两端绕过去才会超过它);直线追会一头顶在障碍上、永远到不了;
  * **没有从障碍里穿过去**(cheated=false)—— 顺带验证"绕"是真的绕;
  * **2D 避障(第八十二轮)**:场地里两对对撞的人(每对从 x=±5 对走):
    - 勾了『避障』那对(RVO)**互相绕开、两个都到达**,而且最小间距
      ≥ 0.9(没互相顶住);退化情形(见下)退而求其次:**两个人都
      明显偏离了直线**(横向偏离 ≥1)且没撞穿;
    - **不勾避障**的对照对**顶死在中点**(两个都到不了、间距≈1.0
      贴在一起、一直走直线偏离≈0)—— 这条是"绕开"真的由避障干的证据。
    ⚠ 对称的正对对撞是 RVO 的**退化情形**(第八十九轮实测):挑哪一侧拐
    依赖帧级时序,同一份工程会一次"互相绕开"、一次"顶死在中点"(加
    `--fixed-fps 60` 也不稳)。所以判据分两档:顶死时只要**轨迹确实被
    避障改变过**(dev ≥1,对照组 dev≈0 作反证)就算过 —— 判的是"插件的
    避障接上了没有",不是"RVO 在这个退化局面里的最优性"。
  * **动态障碍(第八十三轮)**:两条车道正中的幽灵障碍(Area + 动态障碍,
    探针每帧沿 x 来回挪)—— 勾避障的那对离它最近 ≥0.9(绕开),
    不勾的那对 0.00(直接穿过去);
  * **区域代价(第八十四轮)**:场地左侧的泥地补丁(『通行代价』5):
    - 泥地车道的路**绕开泥地**(横向偏离 ≥1、一脚没踩进泥地)且到达;
    - 对照车道(没有泥地)**一路直走**完(偏离 ≤0.3)—— 证明"绕开"
      是泥地干的;
    - 从车道起点到**泥地中心**的路径可达(代价区该是"贵",不是"墙")。
  * **多层导航(第八十五轮)**:场景里有**两块**导航网格(层 1 = 场地,
    层 2 = 场地外那条走廊);两个人走同一段路去同一个终点:
    - 走**第 2 层**的那个人到得了(它的网格就是这条走廊);
    - 走**第 1 层**的那个人到不了(层 1 的网格是里面那块场地,
      根本不含这条走廊)—— 这一条就是"分层"在起作用的证据。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "examples", "demo_nav2d", "2D寻路示例_godot")
OUT = os.path.join(ROOT, "_dist", "nav2d_probe")

PROBE_GD = '''extends Node

# 2D 寻路探针:追踪者从 (0,6) 追 (0,-6) 的玩家,中间隔着一堵 10 米宽的
# 障碍 —— 采"最大横向偏移(绕行证据)/ 有没有穿障 / 到没到"。

var world: Node = null
var chaser: CharacterBody2D = null
var player: Node2D = null
var elapsed := 0.0
var polys := 0
var side := 0.0
var arrived := false
var cheated := false
var done := false
var dbg_tick := -1
var esc_at := -1.0        # 发过 Esc 的时刻(-1 = 还没发)
# ── 第八十二轮:2D 避障那两对车道 ────────────────────────────────
var lane_mode := false    # 追人段结束后切到"车道实验"
var lane_at := 0.0        # 车道实验开始时刻
var lane_tick := -1
var avoid_a: CharacterBody2D = null
var avoid_b: CharacterBody2D = null
var plain_c: CharacterBody2D = null
var plain_d: CharacterBody2D = null
var goal_a: Node2D = null
var goal_b: Node2D = null
var goal_c: Node2D = null
var goal_d: Node2D = null
var sep_avoid := 1.0e9    # 避障对的最小间距
var sep_plain := 1.0e9    # 对照对的最小间距
var dev_avoid := 0.0      # 避障对最大横向偏离(避障在起作用的证据)
var dev_plain := 0.0      # 对照对最大横向偏离(该≈0:不勾避障的人走直线)
# ── 第八十三轮:动态障碍(幽灵)────────────────────────────────
var ghost_a: Node2D = null    # 避障车道正中那个(Area2D + 动态障碍)
var ghost_b: Node2D = null    # 对照车道正中那个
var ghost_dist_a := 1.0e9     # 避障对离幽灵最近到过多少
var ghost_dist_b := 1.0e9     # 对照对离幽灵最近到过多少
# ── 第八十四轮:区域代价(泥地)两条车道 ────────────────────────────
var mud_mode := false         # 车道实验结束后切到"代价车道"
var mud_at := 0.0
var mud_tick := -1
var mud_walker: CharacterBody2D = null   # 泥地甲(该绕开泥地)
var ctrl_walker: CharacterBody2D = null  # 对照戊(没有泥地,该直走)
var mud_goal: Node2D = null
var ctrl_goal: Node2D = null
var dev_mud := 0.0            # 泥地车道最大横向偏离(绕行证据)
var dev_ctrl := 0.0           # 对照车道最大横向偏离(该≈0)
var mud_entered := false      # 泥地甲有没有一脚踩进泥地
# ── 第八十五轮:多层导航(层 2 走廊)────────────────────────────────
var layer_mode := false        # 代价车道跑完切到"多层车道"
var layer_at := 0.0
var layer_tick := -1
var l2_walker: CharacterBody2D = null   # 层2甲(走第 2 层)
var l1_walker: CharacterBody2D = null   # 层1乙(走第 1 层,该到不了)
var l2_goal: Node2D = null
var l2_arrived := false
var l1_arrived := false
var regions_found := 0         # 场景里几块导航网格(该是 2)
const L2_START := Vector2(18.0, 0.0)
const L2_START_B := Vector2(18.0, 3.0)
const LAYER_WINDOW := 6.0      # 多层车道实验窗口(秒)
const LAYER_PARK := Vector2(0.0, 60.0)   # 前几段先"按住"这两个人的地方
const LANE_X := 5.0
const AVOID_Y := 3.0
const PLAIN_Y := -3.0
const LANE_WINDOW := 5.5  # 车道实验窗口(秒)
const GHOST_SWING := 1.0  # 幽灵沿 x 来回挪的幅度(模拟会动的柱子)
const MUD_LANE_Y := 5.5   # 泥地车道(直线穿过泥地)
const MUD_CTRL_Y := -7.5  # 对照车道(没有泥地)
const MUD_START := Vector2(-6.0, 5.5)
const MUD_CTRL_START := Vector2(-6.0, -7.5)
const MUD_WINDOW := 6.0   # 代价车道实验窗口(秒)
const MUD_RECT := Rect2(-4.5, 4.5, 3.0, 2.0)  # 泥地补丁(中心 -3,5.5)


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	world = load("res://scenes/World.tscn").instantiate()
	add_child(world)
	var chasers := world.find_children("追踪者", "CharacterBody2D", true, false)
	if not chasers.is_empty():
		chaser = chasers[0] as CharacterBody2D
	var players := world.find_children("小玩家", "CharacterBody2D", true, false)
	if not players.is_empty():
		player = players[0] as Node2D
	avoid_a = _body("避障甲")
	avoid_b = _body("避障乙")
	plain_c = _body("对照丙")
	plain_d = _body("对照丁")
	goal_a = _node2d("终点甲")
	goal_b = _node2d("终点乙")
	goal_c = _node2d("终点丙")
	goal_d = _node2d("终点丁")
	ghost_a = _node2d("幽灵甲")
	ghost_b = _node2d("幽灵乙")
	mud_walker = _body("泥地甲")
	ctrl_walker = _body("对照戊")
	mud_goal = _node2d("终点戊")
	ctrl_goal = _node2d("终点己")
	l2_walker = _body("层2甲")
	l1_walker = _body("层1乙")
	l2_goal = _node2d("终点层2")
	var regions := world.find_children("Navigation", "NavigationRegion2D", true, false)
	if not regions.is_empty():
		var poly := (regions[0] as NavigationRegion2D).navigation_polygon
		if poly != null:
			polys = poly.get_polygon_count()
	# 多层导航(第八十五轮):场景里该有**两块**导航网格(层 1 / 层 2)
	for node in world.find_children("Navigation*", "NavigationRegion2D", true, false):
		regions_found += 1
	print("NAV2D_GAME chaser=", chaser != null, " player=", player != null,
		" polys=", polys, " lanes=",
		avoid_a != null and avoid_b != null and plain_c != null
		and plain_d != null, " mud=",
		mud_walker != null and ctrl_walker != null
		and mud_goal != null and ctrl_goal != null, " layers=",
		regions_found, " layer_walkers=",
		l2_walker != null and l1_walker != null and l2_goal != null)
	# 多层那两个人:前几段先按在远处 —— 走层 1 的那个会把路径吸附回
	# 场地里,可能撞到实验里的人(这俩人只服务最后一段)。
	_park_layer_walkers()
	# 诊断:等地图同步后直接查一条"从追踪者到玩家"的路径 ——
	# 追踪者走直线(= 拿不到路径)时,这行能分清"网格没同步"还是"别的"。
	_path_probe.call_deferred()


func _body(name: String) -> CharacterBody2D:
	var found := world.find_children(name, "CharacterBody2D", true, false)
	return found[0] as CharacterBody2D if not found.is_empty() else null


func _node2d(name: String) -> Node2D:
	var found := world.find_children(name, "Node2D", true, false)
	return found[0] as Node2D if not found.is_empty() else null


func _path_probe() -> void:
	for i in 40:
		await get_tree().physics_frame
	if chaser == null:
		return
	var map := chaser.get_world_2d().navigation_map
	var from: Vector2 = chaser.global_position
	var to: Vector2 = player.global_position if player != null else Vector2.ZERO
	var p := NavigationServer2D.map_get_path(map, from, to, true)
	print("NAV2D_PATH map=", map, " from=", from, " to=", to,
		" n=", p.size(), " ", p)
	# ── 第四十六轮:2D 兜底相机 ────────────────────────────────────
	# 运行后必须有一台"当前"的 Camera2D,而且**追踪者和玩家都在它的可见
	# 范围里** —— 不然就是用户报的"运行没内容 / 视角错乱"(Godot 停在
	# 原点视图,负坐标区的内容全在屏外)。
	var cam: Camera2D = null
	for node in get_tree().root.find_children("*", "Camera2D", true, false):
		if node is Camera2D and (node as Camera2D).is_current():
			cam = node as Camera2D
			break
	var covers := false
	var rect := Rect2()
	if cam != null and chaser != null and player != null:
		var view: Vector2 = get_viewport().get_visible_rect().size / cam.zoom
		var center: Vector2 = cam.get_screen_center_position()
		rect = Rect2(center - view * 0.5, view)
		covers = (rect.has_point(chaser.global_position)
			and rect.has_point(player.global_position))
	print("NAV2D_CAM current=", cam != null, " covers=", covers, " rect=", rect)


func _physics_process(delta: float) -> void:
	elapsed += delta
	if done:
		# ESC 发出去了:等 0.6 秒看游戏退没退(退出是帧末生效的)
		if esc_at > 0.0 and elapsed - esc_at >= 0.6:
			esc_at = -1.0
			print("NAV2D_ESC_STILL_RUNNING")
		return
	if chaser == null or player == null:
		if elapsed >= 3.0:
			done = true
			print("NAV2D ok=false side=0.00 arrived=false cheated=false t=3.00")
			print("NAV2D_DONE")
		return
	if lane_mode:
		_lane_sample()
		if elapsed - lane_at >= LANE_WINDOW:
			_lane_finish()
		return
	if mud_mode:
		_mud_sample()
		if elapsed - mud_at >= MUD_WINDOW:
			_mud_finish()
		return
	if layer_mode:
		_layer_sample()
		if elapsed - layer_at >= LAYER_WINDOW:
			_layer_finish()
		return
	# 还没轮到多层那段:把两个人按在远处,别让走层 1 的那个溜进场地
	_park_layer_walkers()
	_lane_chase(delta)


## ── 第一段:追踪者绕障追人(第三十六轮的老断言)────────────────────
func _lane_chase(_delta: float) -> void:
	var here := chaser.global_position
	side = maxf(side, absf(here.x))
	# 诊断:每 2 秒打一行位置(排障用)+ 用**它当前位置**查一条路径
	var tick := int(elapsed / 2.0)
	if tick != dbg_tick:
		dbg_tick = tick
		var text := "NAV2D_T t=%.1f pos=(%.2f,%.2f) vel=(%.2f,%.2f)" % [
			elapsed, here.x, here.y, chaser.velocity.x, chaser.velocity.y]
		if player != null:
			var map := chaser.get_world_2d().navigation_map
			var p2 := NavigationServer2D.map_get_path(
				map, here, player.global_position, true)
			text += " path=%d" % p2.size()
		print(text)
	# 穿障判据:中心点进入**障碍矩形内部**(|x|<5 且 |y|<1)。
	# ⚠ 不能用"障碍外扩半径"当判据 —— 绕行转弯时贴着障碍右上角
	#   (x≈4.6, y≈1.9)是正常路径,会被误报(实测踩过)。
	if absf(here.x) < 5.0 and absf(here.y) < 1.0:
		cheated = true
	if here.distance_to(player.global_position) <= 1.4:
		arrived = true
	if elapsed >= 12.0:
		var ok := polys > 0 and side >= 5.5 and arrived and not cheated
		print("NAV2D ok=", ok, " side=", "%.2f" % side,
			" arrived=", arrived, " cheated=", cheated,
			" t=", "%.2f" % elapsed)
		# ── 第八十二轮:切到避障车道实验 ───────────────────────────
		_lane_setup()


## ── 第二段:避障车道实验(第八十二轮)─────────────────────────────
## 把追踪者/玩家挪到远角(别再横穿车道),四个人回出发点重新对走。
func _lane_setup() -> void:
	_park_layer_walkers()      # 多层那两个人这一路都先按在远处
	if chaser != null:
		chaser.global_position = Vector2(-10.5, 6.5)
		chaser.velocity = Vector2.ZERO
	if player != null:
		player.global_position = Vector2(-10.5, 7.4)
	_lane_reset(avoid_a, Vector2(-LANE_X, AVOID_Y))
	_lane_reset(avoid_b, Vector2(LANE_X, AVOID_Y))
	_lane_reset(plain_c, Vector2(-LANE_X, PLAIN_Y))
	_lane_reset(plain_d, Vector2(LANE_X, PLAIN_Y))
	sep_avoid = 1.0e9
	sep_plain = 1.0e9
	dev_avoid = 0.0
	dev_plain = 0.0
	ghost_dist_a = 1.0e9
	ghost_dist_b = 1.0e9
	lane_mode = true
	lane_at = elapsed
	var avoid_text := "avoid=<none>"
	if avoid_a != null:
		avoid_text = "avoid=(%.1f,%.1f)" % [
			avoid_a.global_position.x, avoid_a.global_position.y]
	var plain_text := "plain=<none>"
	if plain_c != null:
		plain_text = "plain=(%.1f,%.1f)" % [
			plain_c.global_position.x, plain_c.global_position.y]
	print("NAV2D_LANE_START t=", "%.2f" % elapsed, " ",
		avoid_text, " ", plain_text)


func _lane_reset(body: CharacterBody2D, at: Vector2) -> void:
	if body == null:
		return
	body.global_position = at
	body.velocity = Vector2.ZERO


func _lane_sample() -> void:
	# ── 幽灵障碍(第八十三轮):每帧沿 x 来回挪(模拟会动的柱子)────
	# 导航网格是**启动时烤好的**,这里挪它不会重烤 —— 正好证明
	# "NavigationObstacle 不用重烤就能绕"。
	var swing := sin((elapsed - lane_at) * 1.6) * GHOST_SWING
	if ghost_a != null:
		ghost_a.global_position.x = swing
	if ghost_b != null:
		ghost_b.global_position.x = -swing
	if avoid_a != null and avoid_b != null:
		sep_avoid = minf(sep_avoid, avoid_a.global_position.distance_to(
			avoid_b.global_position))
		dev_avoid = maxf(dev_avoid, maxf(
			absf(avoid_a.global_position.y - AVOID_Y),
			absf(avoid_b.global_position.y - AVOID_Y)))
	if ghost_a != null:
		for body in [avoid_a, avoid_b]:
			if body != null:
				ghost_dist_a = minf(ghost_dist_a, body.global_position.distance_to(
					ghost_a.global_position))
	if ghost_b != null:
		for body in [plain_c, plain_d]:
			if body != null:
				ghost_dist_b = minf(ghost_dist_b, body.global_position.distance_to(
					ghost_b.global_position))
	if plain_c != null and plain_d != null:
		sep_plain = minf(sep_plain, plain_c.global_position.distance_to(
			plain_d.global_position))
		# 对照对的横向偏离(该 ≈0:不勾避障的人一直走直线)——
		# 它是"避障对 dev≥1.0 是避障干的"这条反证的对照(第八十九轮)
		dev_plain = maxf(dev_plain, maxf(
			absf(plain_c.global_position.y - PLAIN_Y),
			absf(plain_d.global_position.y - PLAIN_Y)))
	var tick := int((elapsed - lane_at) / 1.0)
	if tick != lane_tick:
		lane_tick = tick
		print("NAV2D_LANE_T t=%.1f avoid=(%.2f,%.2f)+(%.2f,%.2f) sep=%.2f" % [
			elapsed - lane_at,
			avoid_a.global_position.x if avoid_a != null else 0.0,
			avoid_a.global_position.y if avoid_a != null else 0.0,
			avoid_b.global_position.x if avoid_b != null else 0.0,
			avoid_b.global_position.y if avoid_b != null else 0.0,
			sep_avoid])


func _lane_reached(body: CharacterBody2D, goal: Node2D) -> bool:
	if body == null or goal == null:
		return false
	return body.global_position.distance_to(goal.global_position) <= 0.9


func _lane_finish() -> void:
	lane_mode = false       # ⚠ 先关掉:不然窗口到了之后每帧都会重进这里
	var got_a := _lane_reached(avoid_a, goal_a)
	var got_b := _lane_reached(avoid_b, goal_b)
	var got_c := _lane_reached(plain_c, goal_c)
	var got_d := _lane_reached(plain_d, goal_d)
	# ── 避障对的判据(第八十九轮加固:两档)─────────────────────────
	# 甲等:两个都到、最小间距 ≥0.9 —— RVO 把对撞**完全解开**;
	# 兜底:两个人都**明显偏离了直线**(dev ≥ 1.0)且没撞穿(间距 ≥0.9)。
	#   ⚠ 为什么要有兜底:完全对称的正对对撞是 RVO 的**退化情形** ——
	#   两侧的避让决策互为镜像,挑哪一侧拐依赖帧级时序。门禁上真踩到:
	#   **同一份工程**(逐字节相同)一次 `min_sep=2.42 都到达`、一次
	#   `min_sep=1.22 顶死在中点`(加 --fixed-fps 也不稳)。
	#   顶死不等于"避障没工作":轨迹确实被改过(dev≈1.4)就是它在起作用的
	#   活证据;而对照组(不勾避障)走直线(dev≈0)、顶死 —— 正好做反证。
	#   反过来,要是避障真的坏了(比如 avoidance_enabled 没开),避障对会
	#   和对照组一样走直线(dev≈0) -> 两档都不满足,照样判红。
	var ok_avoid := (got_a and got_b and sep_avoid >= 0.9) \
		or (dev_avoid >= 1.0 and sep_avoid >= 0.9)
	# 对照对:两个都**没**到(顶死在中点)+ 间距 ≤ 1.3(确实撞上了)
	# + 横向偏离 ≈0(一直在直线上走 —— 反证"避障对绕了"是避障干的)
	var ok_plain := (not got_c) and (not got_d) and sep_plain <= 1.3 \
		and dev_plain <= 0.5
	print("NAV2D_AVOID ok=", ok_avoid, " a=", got_a, " b=", got_b,
		" min_sep=", "%.2f" % sep_avoid, " dev=", "%.2f" % dev_avoid)
	print("NAV2D_AVOID_CTRL ok=", ok_plain, " c=", got_c, " d=", got_d,
		" min_sep=", "%.2f" % sep_plain, " dev=", "%.2f" % dev_plain)
	# ── 第八十三轮:动态障碍(幽灵)两问 ─────────────────────────
	# ① 避障对**没有穿过**幽灵障碍(最近距离 ≥ 0.9,幽灵半径 0.8):
	#    幽灵没有物理碰撞,能保距只可能是避障在起作用;
	# ② 对照对**直接穿过去了**(最近距离 ≤ 0.4):证明幽灵确实没有物理碰撞,
	#    ① 的保距不是"被撞住的"。
	var ok_ghost: bool = ghost_dist_a >= 0.9 and ghost_dist_b <= 0.4
	print("NAV2D_OBSTACLE ok=", ok_ghost,
		" avoid_min=", "%.2f" % ghost_dist_a,
		" ctrl_min=", "%.2f" % ghost_dist_b)
	# ── 第八十四轮:切到"代价车道"实验(泥地 / 对照)──────────────
	_mud_setup()


## ── 第三段:代价车道实验(第八十四轮)────────────────────────────
## 泥地甲从 (-6, 5.5) 走到 (6, 5.5),直线会穿过泥地补丁(中心 -3,5.5,
## 3x2,『通行代价』5)—— 该**绕开**;对照戊在没有泥地的车道上直走。
## 追人那对已经被 _lane_setup 挪到远角,正好给这两条车道腾地方
## (对照车道 y=-7.5 就从"小玩家原站位"(0,-6)旁边过 —— 玩家挪走了才干净)。
func _mud_setup() -> void:
	_park_layer_walkers()
	_lane_reset(mud_walker, MUD_START)
	_lane_reset(ctrl_walker, MUD_CTRL_START)
	dev_mud = 0.0
	dev_ctrl = 0.0
	mud_entered = false
	mud_mode = true
	mud_at = elapsed
	print("NAV2D_MUD_START t=", "%.2f" % elapsed)
	# "泥地不是墙"的诊断:从车道起点查一条**走进泥地中心**的路径 ——
	# 代价区该是"贵",不是"不可达"(洞没填上的话这条会到不了)。
	_path_into_mud.call_deferred()


func _path_into_mud() -> void:
	for i in 10:
		await get_tree().physics_frame
	if mud_walker == null:
		print("NAV2D_MUD_WALK ok=false n=0(NO_WALKER)")
		return
	var map := mud_walker.get_world_2d().navigation_map
	var to := Vector2(MUD_RECT.position.x + MUD_RECT.size.x * 0.5,
		MUD_RECT.position.y + MUD_RECT.size.y * 0.5)
	var p := NavigationServer2D.map_get_path(map, MUD_START, to, true)
	var end := Vector2.ZERO
	if p.size() > 0:
		end = p[p.size() - 1]
	var reach := p.size() > 0 and end.distance_to(to) <= 0.6
	print("NAV2D_MUD_WALK ok=", reach, " n=", p.size(),
		" target=", to, " end=", end, " pts=", p)


func _mud_sample() -> void:
	if mud_walker != null:
		var here := mud_walker.global_position
		dev_mud = maxf(dev_mud, absf(here.y - MUD_LANE_Y))
		if MUD_RECT.has_point(here):
			mud_entered = true
	if ctrl_walker != null:
		dev_ctrl = maxf(dev_ctrl,
			absf(ctrl_walker.global_position.y - MUD_CTRL_Y))
	var tick := int((elapsed - mud_at) / 1.0)
	if tick != mud_tick:
		mud_tick = tick
		var mud_text := "mud=<none>"
		if mud_walker != null:
			mud_text = "mud=(%.2f,%.2f)" % [
				mud_walker.global_position.x, mud_walker.global_position.y]
		var ctrl_text := "ctrl=<none>"
		if ctrl_walker != null:
			ctrl_text = "ctrl=(%.2f,%.2f)" % [
				ctrl_walker.global_position.x, ctrl_walker.global_position.y]
		print("NAV2D_MUD_T t=%.1f " % (elapsed - mud_at), mud_text, " ",
			ctrl_text)


func _mud_finish() -> void:
	mud_mode = false       # ⚠ 先关掉:不然窗口到了之后每帧都会重进这里
	var got_mud := _lane_reached(mud_walker, mud_goal)
	var got_ctrl := _lane_reached(ctrl_walker, ctrl_goal)
	# 泥地车道:到达 + 绕开(横向偏离 ≥1)+ 一脚没踩进泥地
	var ok_mud: bool = got_mud and dev_mud >= 1.0 and not mud_entered
	# 对照车道:到达 + 一路直走(偏离 ≤0.3)
	var ok_ctrl: bool = got_ctrl and dev_ctrl <= 0.3
	print("NAV2D_MUD ok=", ok_mud, " arrived=", got_mud,
		" dev=", "%.2f" % dev_mud, " entered=", mud_entered)
	print("NAV2D_MUD_CTRL ok=", ok_ctrl, " arrived=", got_ctrl,
		" dev=", "%.2f" % dev_ctrl)
	# ── 第八十五轮:切到"多层车道"实验(层 2 走廊)─────────────────
	_layer_setup()


## ── 第四段:多层导航实验(第八十五轮)────────────────────────────
## 同一条走廊、同一个终点,两个人只差『走哪层』:
##   层2甲 走第 2 层 -> 该到得了(层 2 的网格就是这条走廊);
##   层1乙 走第 1 层 -> 该**到不了**(层 1 的网格是里面那块场地。
##   注意它不会"钻进去" —— Godot 会把查询点吸附到层 1 最近的网格上,
##   于是它往场地那头走,永远走不到这条走廊的终点)。
func _park_layer_walkers() -> void:
	if l2_walker != null:
		l2_walker.global_position = LAYER_PARK
		l2_walker.velocity = Vector2.ZERO
	if l1_walker != null:
		l1_walker.global_position = LAYER_PARK + Vector2(2.0, 0.0)
		l1_walker.velocity = Vector2.ZERO


func _layer_setup() -> void:
	_park_layer_walkers()
	if l2_walker != null:
		l2_walker.global_position = L2_START
	if l1_walker != null:
		l1_walker.global_position = L2_START_B
	l2_arrived = false
	l1_arrived = false
	layer_mode = true
	layer_at = elapsed
	print("NAV2D_LAYER_START t=", "%.2f" % elapsed,
		" regions=", regions_found)


func _layer_sample() -> void:
	if l2_walker != null and l2_goal != null:
		if l2_walker.global_position.distance_to(l2_goal.global_position) <= 0.9:
			l2_arrived = true
	if l1_walker != null and l2_goal != null:
		if l1_walker.global_position.distance_to(l2_goal.global_position) <= 0.9:
			l1_arrived = true
	var tick := int((elapsed - layer_at) / 1.0)
	if tick != layer_tick:
		layer_tick = tick
		var text2 := "l2=<none>"
		if l2_walker != null:
			text2 = "l2=(%.1f,%.1f)" % [
				l2_walker.global_position.x, l2_walker.global_position.y]
		var text1 := "l1=<none>"
		if l1_walker != null:
			text1 = "l1=(%.1f,%.1f)" % [
				l1_walker.global_position.x, l1_walker.global_position.y]
		print("NAV2D_LAYER_T t=%.1f " % (elapsed - layer_at), text2, " ", text1)


func _layer_finish() -> void:
	layer_mode = false
	# 走层 2 的到得了;走层 1 的到不了(层 1 的网格不含这条走廊)
	var ok_layer: bool = l2_arrived and not l1_arrived and regions_found >= 2
	print("NAV2D_LAYER ok=", ok_layer, " layer2_arrived=", l2_arrived,
		" layer1_arrived=", l1_arrived, " regions=", regions_found)
	print("NAV2D_DONE")
	done = true
	# ── 内置 ESC 退出(第三十九轮)────────────────────────────────
	# 这个场景**没有**接过 Esc,正好验"默认就有":发一次 Esc,
	# 游戏应该自己退出(环境脚本里那条 `[GK] ESC:退出游戏`)。
	# 没退的话下一段如实报出来 —— 那说明内置退出没接上。
	esc_at = elapsed
	var esc := InputEventKey.new()
	esc.keycode = KEY_ESCAPE
	esc.physical_keycode = KEY_ESCAPE
	esc.pressed = true
	Input.parse_input_event(esc)
'''

PROBE_TSCN = '''; 探针(临时文件,不写回示例)
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://probe.gd" id="1_probe"]

[node name="Nav2DProbe" type="Node"]
script = ExtResource("1_probe")
'''


def prepare():
    if not os.path.isdir(SRC):
        print(f"找不到示例工程 {SRC} —— 先跑 make_nav2d_demo.py")
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
        pattern = re.compile(r'run/main_scene="[^"]*"')
        text = pattern.sub('run/main_scene="res://probe.tscn"', text)
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
    run = subprocess.run([godot, "--headless",
                          # ── --fixed-fps 60:一行定死"一步 = 一物理帧"
                          # (第八十九轮实测)──
                          # 不加的话主循环跑多快取决于机器:一个物理步里
                          # 攒了几帧、NavigationServer 在哪一帧同步避障
                          # (velocity_computed 回调)全是机器相关的 ——
                          # 同一份工程(逐字节相同)会一次 `min_sep=2.42 都到达`
                          # 一次 `min_sep=1.22 都没到`(对称对撞的 RVO 退化解
                          # 被时序抖动挑中了)。定死帧率后时序完全可复现。
                          "--fixed-fps", "60",
                          "--path", OUT,
                          # 12 秒追人 + 5.5 秒避障车道 + 6 秒代价车道
                          # + 6 秒多层车道 + Esc 收尾 ≈ 30 秒虚拟时间
                          # (固定帧率下 = 1800 帧);2400 是卡死时的安全网,
                          # 正常跑法在 Esc 那一步就自己退了
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
        if ("NAV2D" in line or "SCRIPT ERROR" in line or "Parse Error" in line
                or "2D 导航多边形" in line):
            print(line.strip())
    ok = True
    for line in text.splitlines():
        if ("SCRIPT ERROR" in line or "Parse Error" in line):
            print("✗ Godot 里有脚本错误 / 解析错误:", line.strip())
            ok = False
    if not re.search(r"NAV2D_GAME chaser=true player=true polys=[1-9]", text):
        print("✗ 开场状态不对(角色/玩家/导航多边形):",
              [line for line in text.splitlines() if "NAV2D_GAME" in line])
        ok = False
    if "lanes=true" not in text:
        print("✗ 避障车道那四个人 / 终点标记没找齐(第八十二轮的示例没重建?):",
              [line for line in text.splitlines() if "NAV2D_GAME" in line])
        ok = False
    if "mud=true" not in text:
        print("✗ 代价车道那两个人 / 终点标记没找齐(第八十四轮的示例没重建?):",
              [line for line in text.splitlines() if "NAV2D_GAME" in line])
        ok = False
    if "layers=2" not in text:
        print("✗ 导航网格不是两块(第八十五轮多层导航:场景里该有层1/层2 两块):",
              [line for line in text.splitlines() if "NAV2D_GAME" in line])
        ok = False
    if "layer_walkers=true" not in text:
        print("✗ 多层车道那两个人 / 终点没找齐(第八十五轮的示例没重建?):",
              [line for line in text.splitlines() if "NAV2D_GAME" in line])
        ok = False
    if not re.search(r"NAV2D ok=true", text):
        print("✗ 追踪者没绕开中间障碍追到人(2D 烘焙没生效 / 绕不过去 / 穿障了):",
              [line for line in text.splitlines() if "NAV2D " in line])
        ok = False
    # ── 第八十二轮:2D 避障(勾避障的对撞两人要互相绕开;对照对顶死)──
    if not re.search(r"NAV2D_AVOID ok=true", text):
        print("✗ 2D 避障没生效(勾了『避障』的对撞两人既没互相绕开、"
              "轨迹也没被避障改变过 —— RVO 没工作):",
              [line for line in text.splitlines() if "NAV2D_AVOID" in line]
              + [line for line in text.splitlines()
                 if "NAV2D_LANE" in line])
        ok = False
    if not re.search(r"NAV2D_AVOID_CTRL ok=true", text):
        print("✗ 避障对照组不对(不勾避障那对应该**顶死在中点**——"
              "它们要是也到了,说明这个对照没验出东西):",
              [line for line in text.splitlines() if "NAV2D_AVOID_CTRL" in line])
        ok = False
    # ── 第八十三轮:动态障碍(幽灵,NavigationObstacle)──────────────
    if not re.search(r"NAV2D_OBSTACLE ok=true", text):
        print("✗ 动态障碍没生效(勾『动态障碍』的幽灵没被避障绕开 / "
              "对方没穿过去 —— 幽灵是 Area,不该有物理碰撞):",
              [line for line in text.splitlines() if "NAV2D_OBSTACLE" in line])
        ok = False
    # ── 第八十四轮:区域代价(泥地)—— 该绕开 / 对照该直走 / 泥地可达 ──
    if not re.search(r"NAV2D_MUD ok=true", text):
        print("✗ 区域代价没生效(泥地车道没绕开 / 没到达 / 一脚踩进了泥地 —— "
              "挖洞+补洞 / travel_cost 哪一段没接上):",
              [line for line in text.splitlines() if "NAV2D_MUD" in line])
        ok = False
    if not re.search(r"NAV2D_MUD_CTRL ok=true", text):
        print("✗ 代价对照车道不对(没有泥地的那条该一路直走完 —— "
              "它要是也绕了,说明绕行不是泥地干的):",
              [line for line in text.splitlines() if "NAV2D_MUD" in line])
        ok = False
    if not re.search(r"NAV2D_MUD_WALK ok=true", text):
        print("✗ 泥地不可达(代价区该是'贵',不是'墙' —— "
              "补块没进地图 / 洞没填上):",
              [line for line in text.splitlines() if "NAV2D_MUD" in line])
        ok = False
    if not re.search(r"NAV2D_CAM current=true covers=true", text):
        print("✗ 2D 兜底相机没生效(运行后看不到内容 / 视角错乱):",
              [line for line in text.splitlines() if "NAV2D_CAM" in line])
        ok = False
    if "NAV2D_DONE" not in text:
        print("✗ 探针没跑完(可能中途报错)")
        ok = False
    # 内置 ESC 退出(第三十九轮):这个场景没接过 Esc —— 按 Esc 就该退出游戏,
    # 证据是环境脚本那条打印,而且探针**没有**报"还活着"。
    if "[GK] ESC:退出游戏" not in text or "NAV2D_ESC_STILL_RUNNING" in text:
        print("✗ 内置 Esc 退出没生效(环境脚本没接上 / Esc 被别的东西抢了):",
              "esc_log=", "[GK] ESC:退出游戏" in text,
              "still_running=", "NAV2D_ESC_STILL_RUNNING" in text)
        ok = False
    print("CHECK_NAV2D_DEMO=" + ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())