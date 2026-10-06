"""关卡示例的自查:打开 .blend -> 导出 -> 检查生成结果(第 22 轮)。

跑法:
    blender -b --python scripts/check_level_example.py

── 查什么 ────────────────────────────────────────────────────────
不是"能不能导出",而是**这个关卡该有的机制有没有真的生成**:
金币拾取、分数、HUD 刷新、掉出去回出生点、通关判定、对外端口,
以及通关演出(相机转场 + 切换第二关)。
生成的脚本落到 `_dist/level.gd`,再由 `scripts/verify_anim_system.ps1`
第 4 层交给真实 Godot 解析(语法错会在那一步被抓住)。

── 第二关从哪来 ──────────────────────────────────────────────────
第二关是 .blend 里的另一个 **Blender 场景**(『关卡2』,由
scripts/make_demo_level.py 建好并指好逻辑图『关卡2逻辑』)。插件的
`asset_export.export_all_scenes()` 会把**每个 Blender 场景**各导出一张
.tscn —— scenes/关卡1.tscn 和 scenes/关卡2.tscn 都是它写出来的;
本脚本**不再**手写补第二关场景文件。
"""

import glob
import os
import re
import shutil
import subprocess
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_GD = os.path.join(ROOT, "_dist", "level.gd")
# 关卡示例的 Godot 工程目录(这个自查脚本自己导出的那份,纯导出、无模型资源)。
# 关卡1 / 关卡2 的 .tscn 都由 asset_export.export_all_scenes() 写到
# 这里的 scenes/ 目录下。
LEVEL_PROJECT_DIR = os.path.join(ROOT, "examples", "demo_level", "关卡_godot")
# ── 运行时探针(第三十一轮)────────────────────────────────────────
# **能玩的那份**工程(带 project.godot / 输入映射,由 make_demo_level.py 导出);
# 坐电梯 / 开车这两件事必须在真 Godot 里跑一遍才算数,所以自查脚本会把它
# 拷到 _dist/level_probe,塞一个探针场景进去,无头跑一遍。
GAME_PROJECT_DIR = os.path.join(ROOT, "examples", "demo_level", "game_godot")
PROBE_DIR = os.path.join(ROOT, "_dist", "level_probe")
GODOT_DEFAULT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name}" + (f"  <- {detail}" if detail and not ok else ""))


PROBE_GD = '''extends Node

# 关卡示例的运行时探针(第三十一轮):坐一次电梯、开一段车。
# 结论行统一 LEVEL_ 前缀,由 check_level_example.py 汇总进 LEVEL_CHECK。

static var run_count := 0

var world: Node = null
var host: CharacterBody3D = null
var elevator: Node3D = null
var vehicle: CharacterBody3D = null
var elapsed := 0.0
var phase := 0
var t := 0.0

var lift_start_y := 0.0
var host_start_y := 0.0
var lift_gap_min := 99.0
var lift_gap_max := 0.0

var veh_start := Vector3.ZERO
var veh_drove := 0.0
var veh_gap := 0.0
var veh_back := 0.0
var veh_exit_seat := 0.0
var veh_walked := 0.0
var veh_stand := Vector3.ZERO
var veh_meta := false


func _ready() -> void:
	run_count += 1
	world = load("res://scenes/关卡1.tscn").instantiate()
	add_child(world)
	var players := world.find_children("示例角色", "CharacterBody3D", true, false)
	if not players.is_empty():
		host = players[0] as CharacterBody3D
	var lifts := world.find_children("电梯", "AnimatableBody3D", true, false)
	if not lifts.is_empty():
		elevator = lifts[0] as Node3D
	var cars := world.find_children("载具", "CharacterBody3D", true, false)
	if not cars.is_empty():
		vehicle = cars[0] as CharacterBody3D
	print("LEVEL_GAME run=", run_count, " player=", host != null,
		" elevator=", elevator != null, " vehicle=", vehicle != null)


func _send_key(code: int, pressed: bool) -> void:
	var event := InputEventKey.new()
	event.keycode = code
	event.physical_keycode = code
	event.pressed = pressed
	Input.parse_input_event(event)


func _physics_process(delta: float) -> void:
	elapsed += delta
	if host == null or elevator == null or vehicle == null:
		return
	t += delta
	if phase == 0:
		# 等电梯落到底(往返时间轴,一轮 5 秒;等不到就兜底硬上)
		if elevator.global_position.y <= 0.25 or t > 6.0:
			host.global_position = Vector3(elevator.global_position.x,
				elevator.global_position.y + 1.15, elevator.global_position.z)
			host.velocity = Vector3.ZERO
			phase = 1
			t = 0.0
	elif phase == 1:
		if t > 0.4:
			lift_start_y = elevator.global_position.y
			host_start_y = host.global_position.y
			phase = 2
			t = 0.0
	elif phase == 2:
		var gap := host.global_position.y - elevator.global_position.y
		lift_gap_min = minf(lift_gap_min, gap)
		lift_gap_max = maxf(lift_gap_max, gap)
		if elevator.global_position.y >= 2.15 or t > 6.0:
			var rise := elevator.global_position.y - lift_start_y
			var host_rise := host.global_position.y - host_start_y
			var ok := rise >= 1.4 and host_rise >= 1.4 and absf(gap - 1.1) <= 0.35
			print("LEVEL_LIFT ok=", ok, " rise=", "%.2f" % rise,
				" host_rise=", "%.2f" % host_rise, " gap=", "%.2f" % gap,
				" gap_min=", "%.2f" % lift_gap_min,
				" gap_max=", "%.2f" % lift_gap_max)
			host.global_position = Vector3(vehicle.global_position.x - 2.0,
				0.9, vehicle.global_position.z)
			host.velocity = Vector3.ZERO
			phase = 3
			t = 0.0
	elif phase == 3:
		if t > 0.4:
			veh_start = vehicle.global_position
			_send_key(KEY_E, true)
			phase = 4
			t = 0.0
	elif phase == 4:
		if t > 0.3:
			_send_key(KEY_E, false)
			veh_meta = bool(vehicle.get_meta("gk_driving", false))
			_send_key(KEY_W, true)
			phase = 5
			t = 0.0
	elif phase == 5:
		var seat: Vector3 = vehicle.global_transform * (
			vehicle.get_meta("gk_seat", Vector3.ZERO) as Vector3)
		veh_gap = maxf(veh_gap, host.global_position.distance_to(seat))
		if t > 1.0:
			_send_key(KEY_W, false)
			veh_drove = veh_start.distance_to(vehicle.global_position)
			_send_key(KEY_E, true)
			phase = 6
			t = 0.0
	elif phase == 6:
		if t > 0.3:
			_send_key(KEY_E, false)
			var seat: Vector3 = vehicle.global_transform * (
				vehicle.get_meta("gk_seat", Vector3.ZERO) as Vector3)
			veh_back = host.global_position.distance_to(vehicle.global_position)
			veh_exit_seat = host.global_position.distance_to(seat)
			veh_stand = host.global_position
			_send_key(KEY_W, true)
			phase = 7
			t = 0.0
	elif phase == 7:
		if t > 0.6:
			_send_key(KEY_W, false)
			veh_walked = veh_stand.distance_to(host.global_position)
			var ok := (veh_meta and veh_drove >= 4.0 and veh_gap <= 0.8
				and veh_back >= 1.0 and veh_exit_seat >= 1.5
				and veh_walked >= 0.8)
			print("LEVEL_CAR ok=", ok, " drove=", "%.2f" % veh_drove,
				" ride_gap=", "%.2f" % veh_gap, " back=", "%.2f" % veh_back,
				" exit_seat=", "%.2f" % veh_exit_seat,
				" walked=", "%.2f" % veh_walked, " meta=", veh_meta)
			print("LEVEL_RIDE_DONE")
			set_physics_process(false)
'''

PROBE2_GD = '''extends Node

# 第二关的运行时探针(第三十五轮)。用户的实测回归:
#   "在关卡示例中完成任务后打开第二关,移动 / 视角 / 上帝摄像机切换 /
#    ESC 退出等失效"
# 根因那版已经查实:第二关当年是当**通关页**做的 —— 图里只接了标题 +
# 两个按键,角色没配移动 / 跳跃 / 相机,上帝机开关还写着 False。
# 现在它由**角色预设**生成,这一趟就是钉住这件事:
#   * 按 W 要**走得动**(不是站在地上不动);
#   * 按 C / V 要切得动**机位**(第二关有侧视 + 俯瞰两台);
#   * 按 Tab 要出得来**上帝机**(它由环境脚本造,god_camera 开着才有)。
# (Esc 退出不能在这里按 —— 它会把游戏关掉、探针也跟着停;
#  那条只做生成物层面的断言,见 check_level_example 的静态检查。)

var world: Node = null
var host: CharacterBody3D = null
var elapsed := 0.0
var phase := 0
var t := 0.0

var start := Vector3.ZERO
var walked := 0.0
var cam_top := false
var cam_back := false
var god_on := false
var god_off := false


func _ready() -> void:
	world = load("res://scenes/关卡2.tscn").instantiate()
	add_child(world)
	var players := world.find_children("第二关角色", "CharacterBody3D", true, false)
	if not players.is_empty():
		host = players[0] as CharacterBody3D
	print("LEVEL2_GAME player=", host != null,
		" side_cam=", world.find_children("第二关机位", "Camera3D", true, false).size(),
		" top_cam=", world.find_children("第二关俯瞰", "Camera3D", true, false).size())


func _send_key(code: int, pressed: bool) -> void:
	var event := InputEventKey.new()
	event.keycode = code
	event.physical_keycode = code
	event.pressed = pressed
	Input.parse_input_event(event)


func _camera_name() -> String:
	var cam := get_viewport().get_camera_3d()
	return String(cam.name) if cam != null else ""


func _god_current() -> bool:
	var cam := get_viewport().get_camera_3d()
	return cam != null and cam.is_in_group("gk_god_camera")


func _physics_process(delta: float) -> void:
	elapsed += delta
	if host == null:
		return
	t += delta
	if phase == 0:
		# 等场景稳下来(材质 / 上帝机都是 call_deferred 挂的)
		if t > 1.2:
			start = host.global_position
			_send_key(KEY_W, true)
			phase = 1
			t = 0.0
	elif phase == 1:
		if t > 1.0:
			_send_key(KEY_W, false)
			walked = start.distance_to(host.global_position)
			_send_key(KEY_C, true)          # 切俯瞰
			phase = 2
			t = 0.0
	elif phase == 2:
		if t > 0.2:
			_send_key(KEY_C, false)
			phase = 3
			t = 0.0
	elif phase == 3:
		# 等过渡走完再看当前相机是谁(『相机_切换』是带过渡的)
		if t > 1.2:
			cam_top = "俯瞰" in _camera_name()
			_send_key(KEY_V, true)          # 切回侧视
			phase = 4
			t = 0.0
	elif phase == 4:
		if t > 0.2:
			_send_key(KEY_V, false)
			phase = 5
			t = 0.0
	elif phase == 5:
		if t > 1.2:
			cam_back = "机位" in _camera_name()
			_send_key(KEY_TAB, true)        # 上帝机
			phase = 6
			t = 0.0
	elif phase == 6:
		if t > 0.3:
			_send_key(KEY_TAB, false)
			god_on = _god_current()
			phase = 7
			t = 0.0
	elif phase == 7:
		# ⚠ 松开和再按必须**跨帧**(踩过的坑:同一帧里"松了就按",环境脚本
		# 那次"松"看不见 —— `_gk_god_tab_held` 还停在按下状态,第二次 Tab
		# 被当成"按住不放"吞掉,切回玩家相机就没发生)。
		if t > 0.3:
			_send_key(KEY_TAB, true)
			phase = 8
			t = 0.0
	elif phase == 8:
		if t > 0.3:
			_send_key(KEY_TAB, false)
			god_off = not _god_current()
			var ok := (walked >= 1.0 and cam_top and cam_back
				and god_on and god_off)
			print("LEVEL2_WALK ok=", ok, " walked=", "%.2f" % walked,
				" cam_top=", cam_top, " cam_back=", cam_back,
				" god=", god_on, "/", god_off)
			print("LEVEL2_DONE")
			set_physics_process(false)
'''

PROBE_TSCN = '''; 探针(临时文件,不写回示例)
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://{stem}.gd" id="1_probe"]

[node name="{node}" type="Node"]
script = ExtResource("1_probe")
'''


PROBE3_GD = '''extends Node

# 上帝机默认功能的运行时探针(第三十九轮,用户 2026-10-06):
#   滚轮缩放 / F 附身(接管角色) / Tab 切换 / Esc 退出。
# 结论行统一 LEVEL_ 前缀。
#
# ⚠ 最后一步按 Esc 会让**游戏自己退出**(环境脚本内置的)——
#   所以"退出成功"的证据是控制台那条 `[GK] ESC:退出游戏`,
#   外加"没有出现 LEVEL_GOD_ESC_STILL_RUNNING"。

var world: Node = null
var host: CharacterBody3D = null
var god: Camera3D = null
var env: Node = null
var elapsed := 0.0
var phase := 0
var t := 0.0
var fov_before := 0.0
var fov_after := 0.0
var host_before := Vector3.ZERO
var host_after := Vector3.ZERO
var cam_gap := 0.0
var god_on := false
var rounds := 0          # 0 = 还没找齐节点,1 = 报告过失败,2 = 找齐了


func _ready() -> void:
	world = load("res://scenes/关卡1.tscn").instantiate()
	add_child(world)


# ⚠ 上帝机是环境脚本**延迟一帧**造的(call_deferred),_ready 里找它必然
#   是 null(第一版就这么踩的:探针一路 return,只有一行 ok=false)。
#   所以每帧找一次,找到为止。
func _resolve() -> bool:
	if host == null:
		var players := world.find_children("示例角色", "CharacterBody3D", true, false)
		if not players.is_empty():
			host = players[0] as CharacterBody3D
	if god == null:
		var found := get_tree().root.find_children("上帝摄像机", "Camera3D", true, false)
		if not found.is_empty():
			god = found[0] as Camera3D
	if env == null:
		env = world.find_child("Environment", true, false)
	return host != null and god != null


func _send_key(code: int, pressed: bool) -> void:
	var event := InputEventKey.new()
	event.keycode = code
	event.physical_keycode = code
	event.pressed = pressed
	Input.parse_input_event(event)


func _send_wheel(up: bool) -> void:
	var event := InputEventMouseButton.new()
	event.button_index = MOUSE_BUTTON_WHEEL_UP if up else MOUSE_BUTTON_WHEEL_DOWN
	event.pressed = true
	event.position = Vector2(320.0, 240.0)
	event.global_position = Vector2(320.0, 240.0)
	get_viewport().push_input(event)


func _physics_process(delta: float) -> void:
	elapsed += delta
	t += delta
	if not _resolve():
		if rounds == 0:
			rounds = 1
			print("LEVEL_GOD_FOUND ok=false host=", host != null,
				" god=", god != null, " env=", env != null)
		return
	if rounds == 1:
		rounds = 2
		print("LEVEL_GOD_FOUND ok=true env=", env != null)
	if phase == 0:
		if t > 0.4:
			_send_key(KEY_TAB, true)
			phase = 1
			t = 0.0
	elif phase == 1:
		if t > 0.2:
			_send_key(KEY_TAB, false)
			god_on = god.is_current()
			fov_before = god.fov
			# 滚轮往前滚 = 拉近(FOV 变小)
			_send_wheel(true)
			_send_wheel(true)
			_send_wheel(true)
			phase = 2
			t = 0.0
	elif phase == 2:
		if t > 0.3:
			fov_after = god.fov
			print("LEVEL_GOD_ZOOM ok=", god_on and fov_after < fov_before - 1.0,
				" before=", "%.1f" % fov_before, " after=", "%.1f" % fov_after,
				" god=", god_on)
			_send_key(KEY_F, true)
			phase = 3
			t = 0.0
	elif phase == 3:
		if t > 0.2:
			_send_key(KEY_F, false)
			host_before = host.global_position
			_send_key(KEY_W, true)
			phase = 4
			t = 0.0
	elif phase == 4:
		if t > 1.2:
			_send_key(KEY_W, false)
			host_after = host.global_position
			cam_gap = god.global_position.distance_to(host.global_position)
			var moved := host_before.distance_to(host_after)
			var target := ""
			if env != null:
				var who = env.get("_gk_god_possess")
				target = String(who.name) if who != null else "<none>"
			print("LEVEL_GOD_POSSESS ok=", moved >= 1.0 and cam_gap <= 12.0,
				" moved=", "%.2f" % moved, " cam_gap=", "%.2f" % cam_gap,
				" target=", target)
			_send_key(KEY_F, true)          # 放开附身
			phase = 5
			t = 0.0
	elif phase == 5:
		if t > 0.2:
			_send_key(KEY_F, false)
			print("LEVEL_GOD_ESC press")
			_send_key(KEY_ESCAPE, true)
			phase = 6
			t = 0.0
	elif phase == 6:
		if t > 0.8:
			# 走到这里 = Esc 没有退出游戏(环境脚本没接上)
			print("LEVEL_GOD_ESC_STILL_RUNNING")
			set_physics_process(false)
'''


def _probe_line(text, prefix):
    """从探针输出里挑一行出来(失败时贴进 detail 方便看数)。"""
    for line in text.splitlines():
        if line.strip().startswith(prefix):
            return line.strip()
    return "没找到那一行"


def _run_probe(stem, script_text, node_name, quit_after):
    """把一份探针脚本塞进工程副本里无头跑一遍,返回 Godot 的输出。

    ── 两个探针共用这一段(第三十五轮抽出来的)──────────────────────
    拷工程 → 写脚本 + 场景 → 把主场景临时指过去 → `--import` → 无头跑。
    坐电梯/开车那个(L2)和第二关那个(L2 的回归)走的是同一条流水线。
    找不到 Godot / 没导出过可玩工程时返回空串(调用方只打一句"跳过")——
    和别的自查脚本一个规矩:没有 Godot 不算失败。
    """
    godot = os.environ.get("GK_GODOT", GODOT_DEFAULT)
    if not os.path.isdir(GAME_PROJECT_DIR):
        print("[INFO] 没有可玩工程(先跑 make_demo_level.py)—— 运行时探针跳过")
        return ""
    if not os.path.isfile(godot):
        print(f"[INFO] 找不到 Godot({godot})—— 运行时探针跳过")
        return ""
    if os.path.isdir(PROBE_DIR):
        shutil.rmtree(PROBE_DIR)
    shutil.copytree(GAME_PROJECT_DIR, PROBE_DIR,
                    ignore=shutil.ignore_patterns(".godot", "*.import"))
    with open(os.path.join(PROBE_DIR, f"{stem}.gd"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(script_text)
    with open(os.path.join(PROBE_DIR, f"{stem}.tscn"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE_TSCN.format(stem=stem, node=node_name))
    project_file = os.path.join(PROBE_DIR, "project.godot")
    with open(project_file, "r", encoding="utf-8") as handle:
        text = handle.read()
    text = re.sub(r'run/main_scene="[^"]*"',
                  f'run/main_scene="res://{stem}.tscn"', text)
    if f"res://{stem}.tscn" not in text:
        text = text.replace("[application]",
                            "[application]\n\n"
                            f'run/main_scene="res://{stem}.tscn"')
    with open(project_file, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)

    subprocess.run([godot, "--headless", "--path", PROBE_DIR, "--import"],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
    # ⚠ 无头模式主循环比物理快,`--quit-after` 数的是主循环次数:
    # 探针要等电梯 / 走一段路 / 切几次机位,给足余量。
    run = subprocess.run([godot, "--headless", "--path", PROBE_DIR,
                          "--quit-after", str(quit_after)],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    out = (run.stdout or "") + (run.stderr or "")
    for line in out.splitlines():
        # LEVEL_ / LEVEL2_ 都落在这个前缀里
        if "LEVEL" in line or "SCRIPT ERROR" in line or "Parse Error" in line:
            print("    " + line.strip())
    if "SCRIPT ERROR" in out or "Parse Error" in out:
        print("✗ 探针里有脚本 / 解析错误:", [
            line.strip() for line in out.splitlines()
            if "SCRIPT ERROR" in line or "Parse Error" in line][:3])
    return out


def run_ride_probe():
    """真 Godot 里跑一遍:坐电梯被抬上去 + 上车开一段再下车。

    ── 为什么还要在**关卡里**再跑一遍 ────────────────────────────────
    物理测试场已经证明"平台带人 / 车能开"本身没问题;这里验的是**接进
    关卡之后**还成立:电梯的时间轴和关卡自己的逻辑(金币/HUD/相机)一起
    跑、驾驶节点接在角色移动后面、按 E 的两层分支没被别的东西抢走。
    """
    out = _run_probe("probe", PROBE_GD, "LevelRideProbe", 4500)
    if not out:
        return []
    return [
        ("坐电梯被抬上去(角色升高 ≈ 电梯升高,间距不变)",
         "LEVEL_LIFT ok=true" in out, _probe_line(out, "LEVEL_LIFT")),
        ("上车开一段再下车(角色钉在座位上,下车后能走)",
         "LEVEL_CAR ok=true" in out, _probe_line(out, "LEVEL_CAR")),
        ("探针跑完(电梯 + 载具都测到)", "LEVEL_RIDE_DONE" in out,
         _probe_line(out, "LEVEL_GAME")),
    ]


def run_level2_probe():
    """真 Godot 里走一遍**第二关**(第三十五轮:用户实测的回归)。

    那一版的第二关是当"通关页"做的 —— 角色没配移动 / 相机,上帝机开关
    还写着 False;"切到第二关后移动 / 视角 / 上帝摄像机切换 / ESC 全失效"
    就是这么来的。现在它由**角色预设**生成,这一趟在真 Godot 里钉住:
    W 走得动、C / V 切得动机位、Tab 出得来上帝机。
    """
    out = _run_probe("probe2", PROBE2_GD, "Level2Probe", 3000)
    if not out:
        return []
    return [
        ("第二关的角色能走动(按 W 位移 ≥ 1 米)",
         "LEVEL2_WALK ok=true" in out, _probe_line(out, "LEVEL2_WALK")),
        ("第二关的机位切得动(C 切俯瞰 / V 切回)+ 上帝机切得进切得出",
         "LEVEL2_WALK ok=true" in out and "cam_top=true cam_back=true" in out,
         _probe_line(out, "LEVEL2_WALK")),
        ("第二关探针跑完", "LEVEL2_DONE" in out, _probe_line(out, "LEVEL2_GAME")),
    ]


def run_god_probe():
    """真 Godot 里把上帝机的默认功能走一遍(第三十九轮)。

    用户要的:"时停、视角缩放、附身(接管角色控制)、内置 ESC 退出"。
    时停在曲线示例的探针里已经钉住了,这里验剩下三样:
    Tab 切进上帝机 -> 滚轮拉近(FOV 变小)-> F 附身 + W 让角色真的走 ->
    F 放开 -> Esc 退出游戏(靠 `[GK] ESC:退出游戏` 那条打印判定)。
    """
    out = _run_probe("probe3", PROBE3_GD, "LevelGodProbe", 3000)
    if not out:
        return []
    esc_quit = "[GK] ESC:退出游戏" in out
    still_running = "LEVEL_GOD_ESC_STILL_RUNNING" in out
    return [
        ("上帝机:Tab 切得进去 + 滚轮能缩放(FOV 变小)",
         "LEVEL_GOD_ZOOM ok=true" in out, _probe_line(out, "LEVEL_GOD_ZOOM")),
        ("上帝机:F 附身接管角色(W 驱动角色走 + 相机跟在身后)",
         "LEVEL_GOD_POSSESS ok=true" in out,
         _probe_line(out, "LEVEL_GOD_POSSESS")),
        # 关卡示例的图里**已经接了** Esc->退出游戏,所以内置的那条会让位
        # (esc_quit=false,见 TestGodCameraRound39)。这里验的是"按 Esc
        # 真的退出"(走的哪条路不重要):游戏没退才会打出 STILL_RUNNING。
        ("上帝机:Esc 能退出游戏(图里接的那条;内置的让位)",
         (not still_running) and ("LEVEL_GOD_ESC press" in out),
         "still_running=%s esc_log=%s" % (still_running, esc_quit)),
    ]


def main():
    blend = os.path.join(ROOT, "examples", "demo_level", "关卡示例.blend")
    if not os.path.exists(blend):
        print(f"找不到: {blend}\n先跑: blender -b --python scripts/make_demo_level.py")
        return 1
    bpy.ops.wm.open_mainfile(filepath=blend)

    from bl_ext.user_default.blender_game_kit import asset_export, codegen, exporter

    # ---- 场景内容 ----
    names = {obj.name for obj in bpy.data.objects}
    coins = sorted(n for n in names if n.startswith("金币"))
    check("场景里有 5 枚金币", len(coins) == 5, str(coins))
    check("场景里有地面 / 玩家 / 两台相机",
          {"地面", "示例角色", "侧视机位", "俯瞰机位"} <= names,
          str(sorted(names)))
    check("通关运镜用的『转场机位』在场景里",
          "转场机位" in names, str(sorted(names)))
    check("第二关的角色『第二关角色』在场景里",
          "第二关角色" in names, str(sorted(names)))

    # ---- 这个 .blend 里正好两个 Blender 场景,各指一棵逻辑图 ----
    # "另一个关卡"必须是**真的 Blender 场景**(不是第二棵节点图),导出时
    # 才会各出一张 .tscn。每个场景用哪棵逻辑图由它设置里的 logic_tree 决定。
    scene_list = list(bpy.data.scenes)
    check("这个 .blend 里正好两个 Blender 场景(关卡1 / 关卡2)",
          len(scene_list) == 2, str([s.name for s in scene_list]))
    for label, want_tree in (("关卡1", "玩家逻辑"), ("关卡2", "关卡2逻辑")):
        scene_obj = bpy.data.scenes.get(label)
        tree, why = exporter.resolve_scene_tree(scene_obj)
        actual = getattr(tree, "name", None)
        check(f"{label} 场景的逻辑图是『{want_tree}』", actual == want_tree,
              f"{actual}({why})")

    # ---- 节点图:按**名字**选『玩家逻辑』(不再"随便拿第一棵")----
    logic = bpy.data.node_groups.get("玩家逻辑")
    if logic is None:
        # 兜底:万一名字被改过,退回"第一棵游戏逻辑图"(check 会暴露这一点)
        logic = next((t for t in bpy.data.node_groups
                      if getattr(t, "bl_idname", "") == "GKGameLogicTree"), None)
    check("找到『玩家逻辑』节点图", logic is not None)
    if logic is None:
        return 1
    kinds = [n.bl_idname for n in logic.nodes]
    check("金币拾取改用了『进入区域』触发器",
          kinds.count("GKNodeOnAreaEntered") == 5,
          str(kinds.count("GKNodeOnAreaEntered")))
    check("『取值_距离』已从拾取链移除",
          kinds.count("GKNodeDistance") == 0,
          str(kinds.count("GKNodeDistance")))
    check("每枚金币各有一个『实例_销毁』(queue_free)",
          kinds.count("GKNodeQueueFree") == 5,
          str(kinds.count("GKNodeQueueFree")))
    check("掉出去有复位逻辑(如果_否则 + 设置位置)",
          "GKNodeSetPosition" in kinds and "GKNodeGetPosition" in kinds)
    check("HUD 用『界面_文本』+『文本_拼接』",
          "GKNodeUiText" in kinds and "GKNodeTextJoin" in kinds)
    check("通关判定接了对外端口输出",
          "GKNodeLogicOutput" in kinds)
    check("通关演出接了『场景_切换关卡』",
          "GKNodeChangeScene" in kinds, str(sorted(set(kinds))))

    # ── 第三十一轮:电梯(会动的平台)+ 载具(能开的车)────────────────
    check("场景里有『电梯』和『载具』",
          {"电梯", "载具"} <= names, str(sorted(names)))
    check("电梯接了『定时_时间轴』(往返抬人)",
          kinds.count("GKNodeTimeline") == 1, str(kinds.count("GKNodeTimeline")))
    check("载具三件套都在(上车 / 下车 / 驾驶)",
          {"GKNodeEnterVehicle", "GKNodeExitVehicle", "GKNodeDriveVehicle"}
          <= set(kinds), str(sorted(set(kinds))))

    # ── 每枚金币各有一条**独立的**触发器链 ──────────────────────────
    # 踩过的坑(用户 2026-10-04:"吃完金币没有触发场景动画"):原来把 5 枚金币
    # 的『完成』都接到**同一个**执行输入引脚 —— Blender 一个执行输入只能接
    # 一根线,后连的顶掉前连的,只有最后一枚加分,金币数永远到不了 5,通关
    # 演出与切关卡都不触发。光看生成的字符串抓不住(代码照样合法,只是链子
    # 只有一条),所以这里从**图结构**跟着线走一遍:每个『进入区域』的
    # 『执行』必须连到一个『实例_销毁』,而且它销毁的正是那枚金币。
    def _link_from(node, socket_name):
        """顺着线找到接到 node.socket_name 的上游节点(没有就 None)。"""
        socket = node.inputs.get(socket_name) if node is not None else None
        if socket is None:
            return None
        for link in logic.links:
            if link.to_socket == socket:
                return link.from_node
        return None

    triggers = [n for n in logic.nodes if n.bl_idname == "GKNodeOnAreaEntered"]
    kills = [n for n in logic.nodes if n.bl_idname == "GKNodeQueueFree"]
    pairs = set()
    for kill in kills:
        upstream = _link_from(kill, "执行")
        if upstream is not None and upstream.bl_idname == "GKNodeOnAreaEntered":
            pairs.add((str(getattr(upstream, "gk_area", "")),
                       str(getattr(kill, "gk_target", ""))))
    wired = all((name, name) in pairs for name in coins)
    check("每枚金币各有一条『进入区域』触发器链(防只算最后一枚)",
          len(triggers) == len(coins) and len(kills) == len(coins) and wired,
          f"进入区域={len(triggers)} 销毁={len(kills)} 配对={sorted(pairs)}")

    ports = [p.port_name for p in getattr(logic, "gk_ports", [])]
    # 端口:两个游戏进度 + 两个视角(鼠标环绕用)
    check("对外端口齐全(金币数 / 通关 / 视角偏航 / 视角俯仰)",
          {"金币数", "通关", "视角偏航", "视角俯仰"} <= set(ports),
          str(ports))

    # ---- 导出 ----
    settings = getattr(bpy.context.scene, "gk_settings", None)
    options = {
        "dimension": "3",
        "base_class": "CharacterBody3D",
        "class_name": "LevelPlayer",
        # 关卡1 的场景设置里开着『上帝摄像机』(make_demo_level 写的),
        # 这里跟着开 —— 校验的就是**真正导出那份**代码。
        "god_camera": True,
    }
    code, warnings = codegen.generate(logic, options)
    real = [w for w in warnings if "基类已从" not in str(w)]
    check("导出零告警", not real, str(real)[:300])

    check("生成的脚本 extends CharacterBody3D",
          "extends CharacterBody3D" in code)
    # 拾取改成了『进入区域』触发器:生成的是 body_entered 信号回调,
    # 并且带"只认角色"的过滤器(不再是每帧比距离)。
    check("拾取生成了『进入区域』信号回调(body_entered)",
          "body_entered" in code and ".connect(" in code and "示例角色" in code)
    # 销毁金币时要连"同名网格"一起清 —— 网格在 glb 实例里、碰撞代理在
    # Colliders 下,只 free 代理的话金币还杵在画面上(实测过)
    check("吃掉金币会连网格一起清(_gk_free_with_visual)",
          "_gk_free_with_visual(" in code and "queue_free()" in code)
    check("上帝视角期间角色不动(移动 / 跳跃都带守卫)",
          "if _gk_god_camera_active():" in code
          and "if not _gk_god_camera_active():" in code)
    # 加分是"读 -> 加一 -> 写回"(『变量_设置』没有自增选项)
    check("分数变量会 +1",
          ("+ 1" in code or "+= 1" in code) and "1.0" in code)
    check("HUD 文本会被刷新(拼接 + 赋值)",
          "text = " in code and "str(" in code)
    check("掉出去会回到出生点(赋值 global_position)",
          "global_position = Vector3(" in code)
    check("通关端口生成了变量与赋值",
          "输出_通关" in code and "bool" in code)
    # 端口变量类型要对(用户声明的是 int / bool)
    check("端口变量带正确类型",
          "输出_金币数: float" in code and "输出_通关: bool" in code)
    check("Esc 退出接上了", "get_tree().quit()" in code)
    check("鼠标转视角用了累积位移",
          "_gk_mouse_delta" in code)
    check("滚轮缩放接上了(滚轮累积 + 改视野 + 夹住范围)",
          "_gk_wheel" in code and "fov" in code and "clampf(" in code)
    check("第三人称移动方向跟着相机(按相机水平朝向变换)",
          "global_transform.basis.z" in code
          and "global_transform.basis.x" in code
          and "_gk_resolve_camera(self)" in code)
    check("输入动作都进了映射(含 view_top / view_side / jump)",
          all(a in code for a in ("ACTION_VIEW_TOP", "ACTION_VIEW_SIDE",
                                  "ACTION_JUMP")))
    # ---- 通关演出:相机转场 -> 等待 -> 切换关卡 ----
    check("通关后切到第二关(延到帧末换场景)",
          "change_scene_to_file" in code and "关卡2.tscn" in code)
    check("切换用『相机_切换』做了带过渡的运镜",
          "_gk_cam_blend_total" in code)
    check("运镜播完才切场景(等待 + 计时器)",
          "create_timer(1.6)" in code)

    # ── 第三十一轮的生成物:电梯(时间轴抬人)+ 载具(上/下车 + 驾驶)────
    check("电梯每帧被推到轨道值上(X / Z 保持当前坐标)",
          "节点_电梯.global_position = Vector3(" in code
          and "_gk_tl_tr1" in code)
    check("载具:上车/下车会开关角色的碰撞",
          "_gk_set_collision(self, false)" in code
          and "_gk_set_collision(self, true)" in code)
    check("载具:状态写在车自己身上(meta)",
          '载具_载具.set_meta("gk_driving", true)' in code
          and '载具_载具.set_meta("gk_seat"' in code)
    check("载具:沿车头开 + 打方向(度转弧度)+ 每帧钉座位",
          "-载具_载具.global_transform.basis.z" in code
          and "rotation.y -= deg_to_rad(" in code
          and 'get_meta("gk_seat", Vector3.ZERO) as Vector3' in code)

    os.makedirs(os.path.dirname(OUT_GD), exist_ok=True)
    with open(OUT_GD, "w", encoding="utf-8") as handle:
        handle.write(code)
    print(f"[INFO] 关卡脚本写到: {OUT_GD}")

    # ---- 导出:把 .blend 里的**每个 Blender 场景**各导出一张 .tscn ----
    # 关卡1 -> scenes/关卡1.tscn,关卡2 -> scenes/关卡2.tscn;每个场景用
    # 它在设置里选的逻辑图(见 exporter.resolve_scene_tree)。这一步同时把
    # 两个场景的脚本 / glb / 碰撞体都写到 LEVEL_PROJECT_DIR。
    try:
        result = asset_export.export_all_scenes(LEVEL_PROJECT_DIR, dimension="3")
        check("导出成 Godot 工程(每个 Blender 场景各一张 .tscn)",
              not result.get("error"), str(result.get("error"))[:200])
        print(f"[INFO] 导出落点: {LEVEL_PROJECT_DIR}")
        if result.get("warnings"):
            print(f"[INFO] 导出告警: {result['warnings']}")
    except Exception as exc:                              # noqa: BLE001
        check("导出成 Godot 工程(每个 Blender 场景各一张 .tscn)", False,
              f"{type(exc).__name__}: {exc}")

    # 两张 .tscn 必须真的落到磁盘上 —— 切关卡两边都得有
    for level_name in ("关卡1", "关卡2"):
        scene_file = os.path.join(LEVEL_PROJECT_DIR, "scenes",
                                  f"{level_name}.tscn")
        check(f"场景文件已生成(scenes/{level_name}.tscn)",
              os.path.isfile(scene_file), scene_file)

    # ── 第二关(第三十五轮:用户实测"切到第二关后移动 / 视角 / 上帝机 /
    #    ESC 全失效")────────────────────────────────────────────────────
    # 那一版的第二关是当**通关页**做的:图里只有标题 + 两个按键,角色没配
    # 移动 / 跳跃 / 相机,上帝机开关还写着 False —— 用户看到的"全失效"就是
    # 这个。现在它由**角色预设**生成(见 make_demo_level.build_second_logic),
    # 下面这些断言就是钉住这件事:Blender 侧(图 + 设置)→ 生成物 → 真 Godot。
    scene2 = bpy.data.scenes.get("关卡2")
    names2 = {obj.name for obj in scene2.objects} if scene2 is not None else set()
    check("第二关有两台机位(侧视 + 俯瞰)",
          {"第二关机位", "第二关俯瞰"} <= names2, str(sorted(names2)))
    settings2 = getattr(scene2, "gk_settings", None) if scene2 is not None else None
    check("第二关开着『上帝摄像机』(关着的话 Tab 没反应)",
          bool(getattr(settings2, "god_camera", False)),
          str(getattr(settings2, "god_camera", None)))
    check("第二关的类名是 Level2Player(和第一关 LevelPlayer 不能重名)",
          str(getattr(settings2, "class_name", "")) == "Level2Player",
          str(getattr(settings2, "class_name", "")))

    logic2 = bpy.data.node_groups.get("关卡2逻辑")
    kinds2 = [n.bl_idname for n in logic2.nodes] if logic2 is not None else []
    check("第二关逻辑图有移动 + 跳跃节点(角色走得动)",
          {"GKNodeMoveDirection", "GKNodeJump"} <= set(kinds2),
          str(sorted(set(kinds2))))
    check("第二关逻辑图有相机套件(弹簧臂 + C/V 切换机位)",
          {"GKNodeCameraSpringArm", "GKNodeCameraSetCurrent"} <= set(kinds2),
          str(sorted(set(kinds2))))
    check("第二关按 E 回第一关(场景_切换关卡)",
          "GKNodeChangeScene" in kinds2 and "GKNodeQuitGame" in kinds2,
          str(sorted(set(kinds2))))

    # 生成物:第二关的脚本 / 环境脚本里,那些代码真的在
    scripts_dir2 = os.path.join(LEVEL_PROJECT_DIR, "scripts")
    logic2_files = [path for path in glob.glob(os.path.join(scripts_dir2, "*.gd"))
                    if "关卡2" in os.path.basename(path)
                    and not os.path.basename(path).startswith("gk_environment")]
    if not logic2_files:
        check("第二关的脚本已生成(scripts/*关卡2*.gd)", False, scripts_dir2)
    else:
        with open(logic2_files[0], encoding="utf-8") as handle:
            code2 = handle.read()
        check("第二关脚本是角色脚本(extends CharacterBody3D)",
              "extends CharacterBody3D" in code2, logic2_files[0])
        check("第二关的移动方向跟着相机(不是世界轴死方向)",
              "_gk_resolve_camera(self)" in code2)
        check("第二关的上帝机守卫在(上帝视角期间角色让位)",
              "if _gk_god_camera_active():" in code2
              and "if not _gk_god_camera_active():" in code2)
        check("第二关按 E 能回第一关(change_scene + 关卡1.tscn)",
              "change_scene_to_file" in code2 and "关卡1.tscn" in code2)
        check("第二关 Esc 退出接上了", "get_tree().quit()" in code2)
    env2_file = os.path.join(scripts_dir2, "gk_environment_关卡2.gd")
    if os.path.isfile(env2_file):
        with open(env2_file, encoding="utf-8") as handle:
            env2_code = handle.read()
        check("第二关的环境脚本开着上帝机 + 带时停",
              "god_camera: bool = true" in env2_code
              and "const _GK_TIME_STOP_KEY := KEY_T" in env2_code)
    else:
        check("第二关的环境脚本已生成(scripts/gk_environment_关卡2.gd)",
              False, env2_file)

    # ── 关卡1 的 .tscn 里:电梯/载具的体类型 + 网格跟着碰撞体 ──────────
    level_tscn = os.path.join(LEVEL_PROJECT_DIR, "scenes", "关卡1.tscn")
    if os.path.isfile(level_tscn):
        with open(level_tscn, encoding="utf-8") as handle:
            level_text = handle.read()
        check("电梯导出成 AnimatableBody3D(站上去才会被带走)",
              '[node name="电梯" type="AnimatableBody3D" '
              'parent="Colliders"]' in level_text)
        check("载具导出成 CharacterBody3D",
              '[node name="载具" type="CharacterBody3D" '
              'parent="Colliders"]' in level_text)
        check("电梯/载具的网格跟着碰撞体(Model 复用各自的 glb)",
              '[node name="Model" parent="Colliders/电梯" instance=' in level_text
              and '[node name="Model" parent="Colliders/载具" instance='
              in level_text)

    # ---- 运行时探针:真 Godot 里坐一次电梯、开一段车(第三十一轮)----
    probe_results = run_ride_probe()
    for name, ok, detail in probe_results:
        check(name, ok, detail)

    # ---- 运行时探针:真 Godot 里走一遍第二关(第三十五轮)----------
    for name, ok, detail in run_level2_probe():
        check(name, ok, detail)

    # ---- 运行时探针:上帝机默认功能(第三十九轮)--------------------
    for name, ok, detail in run_god_probe():
        check(name, ok, detail)

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print()
    print(f"结果: {passed}/{len(RESULTS)} 通过")
    ok = passed == len(RESULTS)
    print("LEVEL_CHECK=" + ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
