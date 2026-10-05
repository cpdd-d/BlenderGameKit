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

PROBE_TSCN = '''; 探针(临时文件,不写回示例)
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://probe.gd" id="1_probe"]

[node name="LevelRideProbe" type="Node"]
script = ExtResource("1_probe")
'''


def _probe_line(text, prefix):
    """从探针输出里挑一行出来(失败时贴进 detail 方便看数)。"""
    for line in text.splitlines():
        if line.strip().startswith(prefix):
            return line.strip()
    return "没找到那一行"


def run_ride_probe():
    """真 Godot 里跑一遍:坐电梯被抬上去 + 上车开一段再下车。

    ── 为什么还要在**关卡里**再跑一遍 ────────────────────────────────
    物理测试场已经证明"平台带人 / 车能开"本身没问题;这里验的是**接进
    关卡之后**还成立:电梯的时间轴和关卡自己的逻辑(金币/HUD/相机)一起
    跑、驾驶节点接在角色移动后面、按 E 的两层分支没被别的东西抢走。

    找不到 Godot 或没导出过可玩工程时返回空列表(只打一句"跳过")——
    和别的自查脚本一个规矩:没有 Godot 不算失败。
    """
    godot = os.environ.get("GK_GODOT", GODOT_DEFAULT)
    if not os.path.isdir(GAME_PROJECT_DIR):
        print("[INFO] 没有可玩工程(先跑 make_demo_level.py)—— 坐电梯/开车跳过")
        return []
    if not os.path.isfile(godot):
        print(f"[INFO] 找不到 Godot({godot})—— 坐电梯/开车跳过")
        return []
    if os.path.isdir(PROBE_DIR):
        shutil.rmtree(PROBE_DIR)
    shutil.copytree(GAME_PROJECT_DIR, PROBE_DIR,
                    ignore=shutil.ignore_patterns(".godot", "*.import"))
    with open(os.path.join(PROBE_DIR, "probe.gd"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE_GD)
    with open(os.path.join(PROBE_DIR, "probe.tscn"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE_TSCN)
    project_file = os.path.join(PROBE_DIR, "project.godot")
    with open(project_file, "r", encoding="utf-8") as handle:
        text = handle.read()
    text = re.sub(r'run/main_scene="[^"]*"',
                  'run/main_scene="res://probe.tscn"', text)
    if "res://probe.tscn" not in text:
        text = text.replace("[application]",
                            "[application]\n\n"
                            'run/main_scene="res://probe.tscn"')
    with open(project_file, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)

    subprocess.run([godot, "--headless", "--path", PROBE_DIR, "--import"],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
    # ⚠ 无头模式主循环比物理快,`--quit-after` 数的是主循环次数:
    # 这段探针要等电梯、坐上去、再开一段车,给足余量。
    run = subprocess.run([godot, "--headless", "--path", PROBE_DIR,
                          "--quit-after", "4500"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    out = (run.stdout or "") + (run.stderr or "")
    for line in out.splitlines():
        if "LEVEL_" in line or "SCRIPT ERROR" in line or "Parse Error" in line:
            print("    " + line.strip())
    if "SCRIPT ERROR" in out or "Parse Error" in out:
        return [("关卡运行时探针跑起来了(没有脚本/解析错误)", False,
                 "见上面的输出")]
    return [
        ("坐电梯被抬上去(角色升高 ≈ 电梯升高,间距不变)",
         "LEVEL_LIFT ok=true" in out, _probe_line(out, "LEVEL_LIFT")),
        ("上车开一段再下车(角色钉在座位上,下车后能走)",
         "LEVEL_CAR ok=true" in out, _probe_line(out, "LEVEL_CAR")),
        ("探针跑完(电梯 + 载具都测到)", "LEVEL_RIDE_DONE" in out,
         _probe_line(out, "LEVEL_GAME")),
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

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print()
    print(f"结果: {passed}/{len(RESULTS)} 通过")
    ok = passed == len(RESULTS)
    print("LEVEL_CHECK=" + ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
