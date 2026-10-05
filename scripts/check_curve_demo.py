"""验证示例工程 `examples/demo_curve` 在**真 Godot** 里跑起来是对的。

用法(不需要 Blender):
    python scripts\\check_curve_demo.py
    python scripts\\check_curve_demo.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

它会:
  1. 把 `examples/demo_curve/曲线时间轴示例_godot` 拷到 `_dist/demo_curve_probe`
     (**不碰示例本身**);
  2. 在副本里加一个探针场景(`probe.gd` + `probe.tscn`),把主场景指过去;
  3. 用 Godot 无头跑 300 帧,读探针打印的 DEMO_* 行,汇总 PASS / FAIL。

探针查的是"这一关该有的现象"(不是代码长什么样):
  * 开场镜头**沿曲线**飞到特写机位(中途离直线很远),最后精确停在机位家;
  * 时间轴的 Alpha / 轨道值走到 1 / 终值,『已结束』触发(门抬起来了);
  * 视野被轨道值推近到 32;
  * 触发区接上了 body_entered;HUD 文本建出来了。
"""

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "examples", "demo_curve", "曲线时间轴示例_godot")
OUT = os.path.join(ROOT, "_dist", "demo_curve_probe")

PROBE_GD = '''extends Node

# 曲线时间轴示例的探针:每到一个时间点就把关键状态打出来,并模拟按键。
# 结论行统一用 DEMO_ 前缀,Python 那边按前缀收集、按内容断言。
#
# 查四件事:
#   1) 开场镜头沿曲线飞到特写机位(离直线很远、最后精确停在机位家);
#   2) 时间轴把门抬起来,**碰撞代理跟着走**(门真的能挡人);
#   3) 按 R 能重播场景动画(走的是时间轴新加的『重播』执行口);
#   4) 进了上帝机之后,相机过渡**暂停**(不再被曲线视角控制);
#   5) X 长一棵树(资产_创建实例)/ B 清掉一棵(实例_销毁)/
#      Z 关门的碰撞、G 打开(物理_启用碰撞)。

var world: Node = null
var host: Node = null
var elapsed := 0.0
var marks := [0.4, 1.4, 3.6, 4.6]
var seen := {}
var start_pos := Vector3(0.0, 3.6, 13.0)     # 侧视机位(Blender (0,-13,3.6))
var end_pos := Vector3(0.0, 4.6, -9.0)       # 特写机位(Blender (0,9,4.6))
var max_off_line := 0.0
var phase := 0
var replay_low := -1.0
var replay_high := -1.0
var god_cam_pos := Vector3.ZERO
var god_check_done := false
var spawn_count := 0
var spawn_solid := false
var spawn_material := false
var kill_done := false
var collision_off := false
var collision_on := false
var fx_torch: GPUParticles3D = null
var fx_smoke: CPUParticles3D = null
var fx_spark: GPUParticles3D = null
var fx_burst := false
var fx_stop := false
var fx_play := false
var dbg_label: Label = null
var dbg_visible := false
var dbg_text_ok := false


func _ready() -> void:
	world = load("res://scenes/World.tscn").instantiate()
	add_child(world)
	for node in world.find_children("*", "", true, false):
		if node.get("_gk_tl_alpha") != null:
			host = node
			break
	if host == null:
		print("DEMO_FAIL 找不到挂脚本的角色节点")
	# 触发区(⚠ 场景里有两个叫『触发区』的节点:网格 + Area3D,按类型找)
	var areas := world.find_children("触发区", "Area3D", true, false)
	if areas.is_empty():
		print("DEMO_FAIL 场景里没有 Area3D 类型的『触发区』")
	else:
		print("DEMO_AREA connections=", (areas[0] as Area3D).body_entered.get_connections().size())
	# HUD 文本
	var labels := 0
	for node in world.find_children("*", "Label", true, false):
		if String((node as Label).text).contains("WASD"):
			labels += 1
	print("DEMO_HUD_labels=", labels)
	if host != null:
		print("DEMO_HOST host=", host.name, " is_character=", host is CharacterBody3D)
	# 碰撞代理配对 / 材质都是环境脚本在 _ready 里 deferred 干的
	# —— 实例内部的节点要等本帧末才进树,这里必须**先等两帧再数**。
	# (踩过:在 _ready 里立刻数,材质永远是 0,看起来像"没挂上"。)
	await get_tree().process_frame
	await get_tree().process_frame
	# 静态资产:三份『树』各自带网格(共用同一份模型)+ 材质**都**挂上了
	var trees := 0
	var trees_with_model := 0
	var trees_with_material := 0
	for node in world.find_children("树*", "Node3D", true, false):
		if not (node is CollisionObject3D):
			continue
		if not String(node.name).begins_with("树"):
			continue
		trees += 1
		for child in node.get_children():
			if child is Node3D and String(child.name) == "Model":
				trees_with_model += 1
				for mesh in child.find_children("*", "MeshInstance3D", true, false):
					var item := mesh as MeshInstance3D
					if item.material_override != null:
						trees_with_material += 1
						break
					if item.get_surface_override_material_count() > 0 \
							and item.get_surface_override_material(0) != null:
						trees_with_material += 1
						break
				break
	print("DEMO_ASSET trees=", trees, " models=", trees_with_model,
		" materials=", trees_with_material)
	# 粒子(需求 5,2026-10-05 第二十一轮):三种发射器各验一条 ——
	#   * 火把:GPU 粒子,材质 + "画什么"都在(缺一个都画不出来);
	#   * 烟雾:空物体发射器 -> 该是 **CPU** 粒子节点;
	#   * 火星:勾了『不显示本体网格』-> 场景里不该有它的网格。
	for node in world.find_children("火把", "GPUParticles3D", true, false):
		fx_torch = node
	for node in world.find_children("烟雾", "CPUParticles3D", true, false):
		fx_smoke = node
	for node in world.find_children("火星", "GPUParticles3D", true, false):
		fx_spark = node
	var spark_mesh := world.find_children("火星", "MeshInstance3D", true,
		false).size()
	print("DEMO_FX torch=", fx_torch != null,
		" proc=", fx_torch != null and fx_torch.process_material != null,
		" draw=", fx_torch != null and fx_torch.draw_pass_1 != null,
		" emitting=", fx_torch != null and fx_torch.emitting,
		" smoke_cpu=", fx_smoke != null,
		" spark=", fx_spark != null,
		" spark_one_shot=", fx_spark != null and fx_spark.one_shot,
		" spark_mesh=", spark_mesh)
	# 调试面板(第二十二轮):环境脚本在 _ready 里现造一块 CanvasLayer + Label
	# (子节点的 _ready 先于本探针),所以这里直接找得到。
	dbg_label = world.find_child("调试面板", true, false)
	print("DEMO_DBG label=", dbg_label != null)
	var env := world.find_child("Environment", false, false)
	if env != null:
		var holders := get_tree().root.find_children("Colliders", "Node", true, false)
		print("DEMO_DIAG colliders_holders=", holders.size())
		for node in world.find_children("门", "", true, false):
			print("DEMO_DIAG named_door=", node.get_path(),
				" class=", node.get_class())
		if env.get("_gk_body_links") != null:
			print("DEMO_LINKS count=", env.get("_gk_body_links").size())


func _send_key(code: int, pressed: bool) -> void:
	var event := InputEventKey.new()
	event.keycode = code
	event.physical_keycode = code
	event.pressed = pressed
	Input.parse_input_event(event)


func _physics_process(delta: float) -> void:
	elapsed += delta
	var camera := get_viewport().get_camera_3d()
	if camera != null:
		max_off_line = maxf(max_off_line, _dist_to_line(camera.global_position, start_pos, end_pos))
	for mark in marks:
		if elapsed >= mark and not seen.has(mark):
			seen[mark] = true
			_report(mark)
	# 爆发是『一次性』发射器:headless(Dummy 渲染器)下它会在**同一帧末**
	# 就把 emitting 归零(实测),所以这里每帧"见一次真就算数"地记下来 ——
	# 不能只在这一帧之后读一次,那样永远读到 false。
	if fx_spark != null and fx_spark.emitting:
		fx_burst = true
	_phases()
	if elapsed >= 11.4:
		_finish()
		set_physics_process(false)


func _phases() -> void:
	if phase == 0 and elapsed >= 4.9:
		phase = 1
		_send_key(KEY_R, true)
	elif phase == 1 and elapsed >= 4.95:
		phase = 2
		_send_key(KEY_R, false)
	elif phase == 2 and elapsed >= 5.2:
		phase = 3
		replay_low = float(host.get("_gk_tl_alpha"))
	elif phase == 3 and elapsed >= 6.6:
		phase = 4
		replay_high = float(host.get("_gk_tl_alpha"))
		_send_key(KEY_TAB, true)          # 进上帝机
	elif phase == 4 and elapsed >= 6.65:
		phase = 5
		_send_key(KEY_TAB, false)         # 只松开,不切回来
		var target := world.find_child("特写机位", true, false)
		if target is Node3D:
			god_cam_pos = (target as Node3D).global_position
	elif phase == 5 and elapsed >= 7.4:
		phase = 6
		god_check_done = true
	# ---- 新功能(2026-10-05 第十五轮):创建 / 销毁 / 启用禁用碰撞 ----
	elif phase == 6 and elapsed >= 7.6:
		phase = 7
		_send_key(KEY_X, true)            # X = 资产_创建实例(长一棵树)
		_send_key(KEY_X, false)
	elif phase == 7 and elapsed >= 7.9:
		phase = 8
		_measure_spawn()
		_send_key(KEY_B, true)            # B = 实例_销毁(清掉『树_实例2』)
		_send_key(KEY_B, false)
	elif phase == 8 and elapsed >= 8.3:
		phase = 9
		kill_done = world.find_children("树_实例2", "", true, false).is_empty()
		_send_key(KEY_Z, true)            # Z = 物理_启用碰撞(关掉门的碰撞)
		_send_key(KEY_Z, false)
	elif phase == 9 and elapsed >= 8.7:
		phase = 10
		# set_deferred 要等帧末 -> 再等一帧才读得到
		await get_tree().physics_frame
		collision_off = _door_collision_disabled()
		_send_key(KEY_G, true)            # G = 再打开
		_send_key(KEY_G, false)
	elif phase == 10 and elapsed >= 9.1:
		phase = 11
		await get_tree().physics_frame
		collision_on = not _door_collision_disabled()
		print("DEMO_NEW spawn=", spawn_count, " spawn_solid=", spawn_solid,
			" spawn_material=", spawn_material, " kill=", kill_done,
			" coll_off=", collision_off, " coll_on=", collision_on)
	# ---- 粒子特效(需求 5,2026-10-05 第二十一轮)----
	# K = 特效_爆发(火星再爆一轮)、L = 特效_停止(灭火把)、
	# J = 特效_播放(再点着)。三个都按"现象"验:直接读 emitting。
	elif phase == 11 and elapsed >= 9.4:
		phase = 12
		_send_key(KEY_K, true)
		_send_key(KEY_K, false)
	elif phase == 12 and elapsed >= 9.7:
		phase = 13
		fx_burst = fx_spark != null and fx_spark.emitting
		_send_key(KEY_L, true)
		_send_key(KEY_L, false)
	elif phase == 13 and elapsed >= 10.0:
		phase = 14
		fx_stop = fx_torch != null and not fx_torch.emitting
		_send_key(KEY_J, true)
		_send_key(KEY_J, false)
	elif phase == 14 and elapsed >= 10.3:
		phase = 15
		fx_play = fx_torch != null and fx_torch.emitting
		print("DEMO_FX2 burst=", fx_burst, " stop=", fx_stop, " play=", fx_play)
	# ---- 调试面板(第二十二轮)----
	# F1 = 弹出 / 收起、F2 = 复制全部。⚠ 面板是**轮询读键**
	# (Input.is_key_pressed),所以按住要跨几帧(和 Tab 上帝机同一个道理),
	# 不能按下立刻松开 —— 那样面板一次都读不到。
	elif phase == 15 and elapsed >= 10.6:
		phase = 16
		_send_key(KEY_F1, true)
	elif phase == 16 and elapsed >= 10.75:
		phase = 17
		_send_key(KEY_F1, false)
		if dbg_label != null:
			dbg_visible = dbg_label.visible
			dbg_text_ok = dbg_label.text.contains("FPS")
			dbg_text_ok = dbg_text_ok and dbg_label.text.contains("测试角色")
			dbg_text_ok = dbg_text_ok and dbg_label.text.contains("长了一棵新树")
		print("DEMO_DBG panel=", dbg_visible, " text_ok=", dbg_text_ok)
	elif phase == 17 and elapsed >= 11.0:
		phase = 18
		_send_key(KEY_F2, true)
	elif phase == 18 and elapsed >= 11.15:
		phase = 19
		_send_key(KEY_F2, false)


## 数一下运行时新长出来的那棵树(节点名 = 资产名『树』)。
## ⚠ 场景里摆好的那些叫『树_本体』『树_实例1』…… 名字不同,
## 所以 `find_children("树")` 只会命中新生成的那一份。
func _measure_spawn() -> void:
	var spawned := get_tree().root.find_children("树", "StaticBody3D", true, false)
	spawn_count = spawned.size()
	spawn_solid = false
	if spawn_count > 0:
		var body := spawned[0] as StaticBody3D
		for shape in body.find_children("*", "CollisionShape3D", true, false):
			spawn_solid = true
			break
		# 材质也要补上(运行时新加的网格,环境脚本靠 node_added 补)
		for mesh in body.find_children("*", "MeshInstance3D", true, false):
			if (mesh as MeshInstance3D).material_override != null:
				spawn_material = true
				break


## 门的碰撞现在是不是被关掉了(拿 Colliders/门 下面那个形状看)。
func _door_collision_disabled() -> bool:
	for body in get_tree().root.find_children("门", "StaticBody3D", true, false):
		for shape in body.find_children("*", "CollisionShape3D", true, false):
			return bool((shape as CollisionShape3D).disabled)
	return false


func _finish() -> void:
	var camera := get_viewport().get_camera_3d()
	var cam_name := "<null>"
	if camera != null:
		cam_name = String(camera.name)
	var target := world.find_child("特写机位", true, false)
	var moved := -1.0
	if target is Node3D:
		moved = god_cam_pos.distance_to((target as Node3D).global_position)
	print("DEMO_RESULT max_off_line=", "%.2f" % max_off_line,
		" replay_low=", "%.3f" % replay_low,
		" replay_high=", "%.3f" % replay_high,
		" god_cam=", cam_name,
		" target_moved=", "%.3f" % moved,
		" god_check=", god_check_done)


func _dist_to_line(point: Vector3, a: Vector3, b: Vector3) -> float:
	var direction := b - a
	if direction.length() < 0.001:
		return point.distance_to(a)
	return ((point - a).cross(direction)).length() / direction.length()


func _report(mark: float) -> void:
	if host == null:
		return
	var camera := get_viewport().get_camera_3d()
	var cam_name := "<null>"
	var cam_pos := Vector3.ZERO
	var fov := 0.0
	if camera != null:
		cam_name = String(camera.name)
		cam_pos = camera.global_position
		fov = (camera as Camera3D).fov
	var doors := []
	for node in world.find_children("门", "", true, false):
		if node is Node3D:
			doors.append("%s=%.2f" % [String((node as Node3D).get_path()), (node as Node3D).global_position.y])
	print("DEMO t=", mark,
		" cam=", cam_name,
		" cam_pos=(", "%.2f" % cam_pos.x, ",", "%.2f" % cam_pos.y, ",", "%.2f" % cam_pos.z, ")",
		" fov=", "%.1f" % fov,
		" alpha=", "%.3f" % float(host.get("_gk_tl_alpha")),
		" track1=", "%.2f" % float(host.get("_gk_tl_tr1")),
		" done=", host.get("_gk_tl_done"),
		" doors=", doors)
'''

PROBE_TSCN = '''; 探针(临时文件,不写回示例)
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://probe.gd" id="1_probe"]

[node name="CurveDemoProbe" type="Node"]
script = ExtResource("1_probe")
'''


def _glb_node_names(path):
    """读出 glb 里的节点名(用来验证"资产网格没混进世界 glb")。"""
    import json
    import struct
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        length = struct.unpack("<I", data[12:16])[0]
        payload = json.loads(data[20:20 + length].decode("utf-8"))
    except Exception:                                     # noqa: BLE001
        return []
    return [str(node.get("name", "")) for node in payload.get("nodes", [])]


def prepare():
    if not os.path.isdir(SRC):
        print(f"找不到示例工程 {SRC} —— 先跑 make_curve_demo.py")
        return False
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    shutil.copytree(SRC, OUT,
                    ignore=shutil.ignore_patterns(".godot", "*.import"))
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
    log = subprocess.run([godot, "--headless", "--path", OUT, "--import"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    if log.returncode not in (0, 1):
        print("导入阶段异常:", (log.stderr or "")[-500:])
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
    failed = False
    for line in text.splitlines():
        if "DEMO_FAIL" in line or "SCRIPT ERROR" in line or "Parse Error" in line:
            failed = True
    # 现象断言(和探针打印的数字对上)
    import re
    ok = True
    result = re.search(r"DEMO_RESULT (.*)", text)
    fields = {}
    if result:
        for item in result.group(1).split():
            if "=" in item:
                key, value = item.split("=", 1)
                fields[key] = value
    if not result or float(fields.get("max_off_line", "0")) < 1.0:
        print("✗ 相机没有明显沿曲线飞(离直线距离太小)")
        ok = False
    last = [line for line in text.splitlines() if "DEMO t=4.6" in line]
    if not last:
        print("✗ 没等到 t=4.6 的状态")
        ok = False
    else:
        line = last[-1]
        if "cam=特写机位" not in line:
            print("✗ 开场运镜没有切到特写机位:", line.strip())
            ok = False
        if "alpha=1.000" not in line or "done=true" not in line:
            print("✗ 时间轴没有走完:", line.strip())
            ok = False
        fov = re.search(r"fov=([0-9.]+)", line)
        if not fov or abs(float(fov.group(1)) - 32.0) > 1.5:
            print("✗ 视野没有被轨道推到 32:", line.strip())
            ok = False
        # ── 门的两个节点都要在 7.0(碰撞代理跟着网格走)───────────────
        pairs = re.findall(r"(\S*?门)=([0-9.]+)", line)
        if len(pairs) < 2:
            print("✗ 场景里找不到『门』的网格和碰撞代理两个节点:", line.strip())
            ok = False
        else:
            for path, value in pairs:
                if abs(float(value) - 7.0) > 0.05:
                    print(f"✗ 门没抬到位(或碰撞代理没跟上):{path}={value}")
                    ok = False
    if not re.search(r"DEMO_LINKS count=\s*[1-9]", text):
        print("✗ 环境脚本没有给碰撞代理配上对(门挡不住人)")
        ok = False
    if not re.search(r"DEMO_AREA connections=\s*[1-9]", text):
        print("✗ 触发区没有接上信号")
        ok = False
    if not re.search(r"DEMO_HUD_labels=\s*1", text):
        print("✗ HUD 操作提示没建出来")
        ok = False
    # ── 按 R 能重播(alpha 掉下去又走满)────────────────────────────
    if float(fields.get("replay_low", "9")) > 0.5:
        print("✗ 按 R 没有重播(5.2 秒时 alpha =", fields.get("replay_low"), ")")
        ok = False
    if float(fields.get("replay_high", "0")) < 0.9:
        print("✗ 按 R 重播后没有走满(6.6 秒时 alpha =", fields.get("replay_high"), ")")
        ok = False
    # ── 上帝机:切过去了,而且曲线过渡**停住**了 ─────────────────────
    if fields.get("god_cam") != "上帝摄像机":
        print("✗ 按 Tab 没有切到上帝摄像机:", fields.get("god_cam"))
        ok = False
    if float(fields.get("target_moved", "9")) > 0.05:
        print("✗ 上帝视角下曲线镜头还在动(target_moved =",
              fields.get("target_moved"), ")")
        ok = False
    if fields.get("god_check") != "true":
        print("✗ 上帝机那一段没跑到")
        ok = False
    # ── 静态资产:两个资产各一份 glb;世界 glb 里不能混进它们的网格 ────
    art = os.path.join(OUT, "art")
    asset_glbs = sorted(name for name in os.listdir(art)
                        if name.startswith("asset_") and name.endswith(".glb"))
    if len(asset_glbs) != 2:
        print("✗ 资产 glb 数量不对(应当 2 份:树 / 石头):", asset_glbs)
        ok = False
    leaked = [name for name in _glb_node_names(os.path.join(art, "World.glb"))
              if name.startswith("树") or name.startswith("石头")]
    if leaked:
        print("✗ 世界 glb 里混进了资产网格(会两份重叠闪烁):", leaked)
        ok = False
    if not re.search(r"DEMO_ASSET trees=\s*3 models=\s*3 materials=\s*3", text):
        print("✗ 三份『树』的网格/材质没全挂上:",
              [line for line in text.splitlines() if "DEMO_ASSET" in line])
        ok = False
    # ── 粒子特效(需求 5,2026-10-05 第二十一轮)──────────────────────
    script_path = os.path.join(OUT, "scripts", "game_logic.gd")
    # ① 现象:三个发射器都在(GPU 火焰 / CPU 烟雾 / 一次性火花),
    #    火星勾了『不显示本体网格』-> 场景里不该有它的网格;
    if not re.search(r"DEMO_FX torch=true proc=true draw=true emitting=true "
                     r"smoke_cpu=true spark=true spark_one_shot=true "
                     r"spark_mesh=0", text):
        print("✗ 粒子发射器没建对:",
              [line for line in text.splitlines() if "DEMO_FX " in line])
        ok = False
    # ② 现象:特效节点真的能控制它 —— L 停止 / J 播放读的是 emitting
    #    (火把是**循环**发射器,状态稳定,headless 下也读得准);
    #    『爆发』打的是**一次性**发射器:headless 的 Dummy 渲染器会在同一帧末
    #    把 emitting 归零(实测),所以它的证据是"按键分支真的跑了"——
    #    也就是下面那条 K 提示打印(生成物里 restart() 那一句另有静态检查)。
    if not re.search(r"DEMO_FX2 burst=\w+ stop=true play=true", text):
        print("✗ 特效节点(播放 / 停止)没全对:",
              [line for line in text.splitlines() if "DEMO_FX2" in line])
        ok = False
    for marker, why in (("K:火花爆了一轮", "『特效_爆发』的按键分支"),
                        ("L:火把灭了", "『特效_停止』的按键分支"),
                        ("J:火把点着了", "『特效_播放』的按键分支")):
        if marker not in text:
            print(f"✗ 运行时没跑到{why}(按键没生效?)")
            ok = False
    # ③ 生成物:粒子节点 + 粒子材质 + 贴图文件都在(缺一个 Godot 就画不出来)
    world_tscn = os.path.join(OUT, "scenes", "World.tscn")
    if os.path.isfile(world_tscn):
        with open(world_tscn, encoding="utf-8") as handle:
            scene_text = handle.read()
        for needle, why in (
                ('[node name="火把" type="GPUParticles3D"', "火把的 GPU 粒子节点"),
                ('[sub_resource type="ParticleProcessMaterial"', "粒子材质"),
                ('type="CPUParticles3D"', "CPU 粒子(空物体发射器)"),
                ("draw_pass_1 = SubResource(", "粒子'画什么'"),
                ('path="res://art/fx_soft.png"', "内置粒子贴图引用")):
            if needle not in scene_text:
                print(f"✗ World.tscn 里没有{why}: {needle}")
                ok = False
    if not os.path.isfile(os.path.join(art, "fx_soft.png")):
        print("✗ 内置粒子贴图没落盘: art/fx_soft.png")
        ok = False
    # ④ 脚本里得有那三句(生成物层面的证据,不只是现象)
    if os.path.isfile(script_path):
        with open(script_path, "r", encoding="utf-8") as handle:
            fx_code = handle.read()
        for needle, why in (("特效_火把.emitting = true", "『特效_播放』那一句"),
                            ("特效_火把.emitting = false", "『特效_停止』那一句"),
                            ("特效_火星.restart()", "『特效_爆发』那一句")):
            if needle not in fx_code:
                print(f"✗ 生成的脚本里没有{why}: {needle}")
                ok = False
    # ── 游戏内调试面板(第二十二轮)──────────────────────────────────
    # ① 现象:F1 弹出后,面板上要有 FPS / 宿主状态 / 变量 / 最近打印
    #    ("长了一棵新树"是 7.6 秒按 X 时『打印_输出』打的那条 —— 它出现在面板里,
    #     就证明"打印节点 -> 面板日志"这条线真的通了);
    # ② F2 复制:内容打到控制台(无头环境没有剪贴板,走"打在下面"那条路)。
    if "DEMO_DBG label=true" not in text:
        print("✗ 调试面板的 Label 没建出来(环境脚本里没生成面板?)")
        ok = False
    if not re.search(r"DEMO_DBG panel=true text_ok=true", text):
        print("✗ 按 F1 后面板内容不对:",
              [line for line in text.splitlines() if "DEMO_DBG" in line])
        ok = False
    copied = "已复制" in text or "没有剪贴板" in text
    if "[GK 调试面板]" not in text or not copied:
        print("✗ 按 F2 没有把面板内容复制 / 打出来")
        ok = False
    # ③ 生成物:环境脚本里有面板、游戏逻辑脚本里有日志缓冲
    env_script = os.path.join(OUT, "scripts", "gk_environment.gd")
    if os.path.isfile(env_script):
        with open(env_script, encoding="utf-8") as handle:
            env_code = handle.read()
        for needle, why in (("var debug_panel: bool = true", "面板开关的默认值"),
                            ("const _GK_DBG_KEY := KEY_F1", "面板键常量"),
                            ("func _gk_dbg_text() -> String:", "面板内容函数"),
                            ("DisplayServer.FEATURE_CLIPBOARD", "复制到剪贴板")):
            if needle not in env_code:
                print(f"✗ 环境脚本里没有{why}: {needle}")
                ok = False
    else:
        print("✗ 没找到环境脚本:", env_script)
        ok = False
    if os.path.isfile(script_path):
        with open(script_path, encoding="utf-8") as handle:
            dbg_code = handle.read()
        for needle, why in (("var _gk_debug_logs: Array = []", "日志缓冲"),
                            ("_gk_debug_log_append(", "往面板推日志的那一句")):
            if needle not in dbg_code:
                print(f"✗ 游戏逻辑脚本里没有{why}: {needle}")
                ok = False
    # ── 创建实例 / 销毁实例 / 启用禁用碰撞(2026-10-05 第十五轮)────────
    # X 长一棵树(资产_创建实例,带碰撞 + 材质);
    # B 清掉『树_实例2』(实例_销毁);
    # Z 关门的碰撞 / G 再打开(物理_启用碰撞,切的是 CollisionShape.disabled)。
    if not re.search(r"DEMO_NEW spawn=\s*1 spawn_solid=true "
                     r"spawn_material=true kill=true "
                     r"coll_off=true coll_on=true", text):
        print("✗ 新节点(创建 / 销毁 / 碰撞开关)没全对:",
              [line for line in text.splitlines() if "DEMO_NEW" in line])
        ok = False
    # 脚本里得有那两个 helper(生成物层面的证据,不只是现象)
    if os.path.isfile(script_path):
        with open(script_path, "r", encoding="utf-8") as handle:
            code = handle.read()
        for needle, why in (("func _gk_spawn_asset(", "运行时创建实例的 helper"),
                            ("func _gk_set_collision(", "碰撞开关的 helper"),
                            ("func 重播开场_", "自定义函数(折叠成函数的形状)")):
            if needle not in code:
                print(f"✗ 生成的脚本里没有{why}: {needle}")
                ok = False
    # ── Esc 退出:静态查一下生成的脚本里有退出调用 ────────────────────
    if os.path.isfile(script_path):
        with open(script_path, "r", encoding="utf-8") as handle:
            code = handle.read()
        if "get_tree().quit()" not in code:
            print("✗ 脚本里没有退出游戏的代码(Esc 退出没连上)")
            ok = False
    if failed:
        ok = False
    print("CHECK_CURVE_DEMO=" + ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())