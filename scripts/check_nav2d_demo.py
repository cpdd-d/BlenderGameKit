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
      ≥ 0.9(没互相顶住);
    - **不勾避障**的对照对**顶死在中点**(两个都到不了、间距≈1.0 贴在一起)
      —— 这条是"绕开"真的由避障干的证据,不是碰巧走过去。
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
var dev_avoid := 0.0      # 避障对最大横向偏离(诊断用)
const LANE_X := 5.0
const AVOID_Y := 3.0
const PLAIN_Y := -3.0
const LANE_WINDOW := 5.5  # 车道实验窗口(秒)


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
	var regions := world.find_children("Navigation", "NavigationRegion2D", true, false)
	if not regions.is_empty():
		var poly := (regions[0] as NavigationRegion2D).navigation_polygon
		if poly != null:
			polys = poly.get_polygon_count()
	print("NAV2D_GAME chaser=", chaser != null, " player=", player != null,
		" polys=", polys, " lanes=",
		avoid_a != null and avoid_b != null and plain_c != null
		and plain_d != null)
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
	if avoid_a != null and avoid_b != null:
		sep_avoid = minf(sep_avoid, avoid_a.global_position.distance_to(
			avoid_b.global_position))
		dev_avoid = maxf(dev_avoid, maxf(
			absf(avoid_a.global_position.y - AVOID_Y),
			absf(avoid_b.global_position.y - AVOID_Y)))
	if plain_c != null and plain_d != null:
		sep_plain = minf(sep_plain, plain_c.global_position.distance_to(
			plain_d.global_position))
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
	var got_a := _lane_reached(avoid_a, goal_a)
	var got_b := _lane_reached(avoid_b, goal_b)
	var got_c := _lane_reached(plain_c, goal_c)
	var got_d := _lane_reached(plain_d, goal_d)
	# 避障对:两个都到 + 最小间距 ≥ 0.9(没互相顶住)
	var ok_avoid := got_a and got_b and sep_avoid >= 0.9
	# 对照对:两个都**没**到(顶死在中点)+ 间距 ≤ 1.3(确实撞上了)
	var ok_plain := (not got_c) and (not got_d) and sep_plain <= 1.3
	print("NAV2D_AVOID ok=", ok_avoid, " a=", got_a, " b=", got_b,
		" min_sep=", "%.2f" % sep_avoid, " dev=", "%.2f" % dev_avoid)
	print("NAV2D_AVOID_CTRL ok=", ok_plain, " c=", got_c, " d=", got_d,
		" min_sep=", "%.2f" % sep_plain)
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
    run = subprocess.run([godot, "--headless", "--path", OUT,
                          # 12 秒追人 + 5.5 秒避障车道 + Esc 收尾;
                          # 无头下主循环比物理快,给足余量
                          "--quit-after", "26000"],
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
    if not re.search(r"NAV2D ok=true", text):
        print("✗ 追踪者没绕开中间障碍追到人(2D 烘焙没生效 / 绕不过去 / 穿障了):",
              [line for line in text.splitlines() if "NAV2D " in line])
        ok = False
    # ── 第八十二轮:2D 避障(勾避障的对撞两人要互相绕开;对照对顶死)──
    if not re.search(r"NAV2D_AVOID ok=true", text):
        print("✗ 2D 避障没生效(勾了『避障』的对撞两人没互相绕开 / 没都到达 / "
              "最小间距太小——RVO 没工作):",
              [line for line in text.splitlines() if "NAV2D_AVOID" in line]
              + [line for line in text.splitlines()
                 if "NAV2D_LANE" in line])
        ok = False
    if not re.search(r"NAV2D_AVOID_CTRL ok=true", text):
        print("✗ 避障对照组不对(不勾避障那对应该**顶死在中点**——"
              "它们要是也到了,说明这个对照没验出东西):",
              [line for line in text.splitlines() if "NAV2D_AVOID_CTRL" in line])
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