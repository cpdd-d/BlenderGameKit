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
	var regions := world.find_children("Navigation", "NavigationRegion2D", true, false)
	if not regions.is_empty():
		var poly := (regions[0] as NavigationRegion2D).navigation_polygon
		if poly != null:
			polys = poly.get_polygon_count()
	print("NAV2D_GAME chaser=", chaser != null, " player=", player != null,
		" polys=", polys)
	# 诊断:等地图同步后直接查一条"从追踪者到玩家"的路径 ——
	# 追踪者走直线(= 拿不到路径)时,这行能分清"网格没同步"还是"别的"。
	_path_probe.call_deferred()


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
		done = true
		var ok := polys > 0 and side >= 5.5 and arrived and not cheated
		print("NAV2D ok=", ok, " side=", "%.2f" % side,
			" arrived=", arrived, " cheated=", cheated,
			" t=", "%.2f" % elapsed)
		print("NAV2D_DONE")
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
                          # 12 秒物理;无头下主循环比物理快,给足余量
                          "--quit-after", "16000"],
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
    if not re.search(r"NAV2D ok=true", text):
        print("✗ 追踪者没绕开中间障碍追到人(2D 烘焙没生效 / 绕不过去 / 穿障了):",
              [line for line in text.splitlines() if "NAV2D " in line])
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