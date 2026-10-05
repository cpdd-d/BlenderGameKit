"""物理测试场的端到端检查(不用 Blender,要 Godot)。

用法:
    python scripts\\check_physics_demo.py
    python scripts\\check_physics_demo.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

它会:
  1. 把 `examples/demo_physics/物理测试场_godot` 拷到 `_dist/physics_probe`
     (**不碰示例本身**);
  2. 在副本里加一个探针场景(`probe.gd` + `probe.tscn`),把主场景指过去;
  3. 用 Godot 无头跑,读探针打印的 PHYS_* 行,汇总 PASS / FAIL。

探针查的是**物理现象**(不是代码长什么样):把角色依次传送到七个工位前,
按住 W 往 -Z 走(相机 yaw=0,方向是确定的),边走边采样位置:

  * **斜坡**:能爬上 20° 的坡(y 升高 ≥ 0.8),不卡在坡脚;
  * **台阶**:低台阶(0.35,在『上台阶高度』0.5 内)该**迈上去** ——
    y 要升到 0.35 那一档;高台阶(1.05,差 0.7)该**仍然挡住** ——
    不许"骑"上第二级、也不许穿进它;
  * **对角方块**:停在**角**外面(到中心的距离 ≥ 1.75)--
    球碰撞会内切进去(用户报过的"斜对角穿模");
  * **推箱子**:刚体箱子被推动 ≥ 0.5 米,角色没陷进箱子;
  * **多碰撞体**:三条射线分别命中腿/身/头,缝里那条**漏掉**;
  * **窄缝**:能穿过 1.6 米宽的缝(角色 1.0 宽);
  * **薄墙**:全速撞 2.5 秒**不穿透**;
  * **移动平台**(第三十轮):站上去不按键 —— 平台(AnimatableBody,
    时间轴往返推着走)移动时角色要**被带走**(位移对得上);要是
    StaticBody("瞬移"),角色留在原地、还会掉到地上;
  * **载具**(第三十轮):E 上车 → W 开 1.5 秒(角色全程钉在座位上)→
    E 下车 → W 还能走(控制权交还角色)、车不跟着跑。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "examples", "demo_physics", "物理测试场_godot")
OUT = os.path.join(ROOT, "_dist", "physics_probe")

PROBE_GD = '''extends Node

# 物理测试场的探针:把角色依次送到七个工位前,按住 W 走一遍,
# 边走边采样"有没有穿模 / 卡住 / 推不动"。结论行统一 PHYS_ 前缀。

static var run_count := 0

var world: Node = null
var host: CharacterBody3D = null
var box: RigidBody3D = null
var platform: Node3D = null
var vehicle: CharacterBody3D = null
var elapsed := 0.0

# 七个工位:名字 / x / 走多久 / 判据用哪一套
const STATION_DEFS := [
	{"key": "slope", "x": 0.0, "walk": 1.8, "kind": "slope"},
	{"key": "stairs", "x": 7.0, "walk": 2.0, "kind": "stairs"},
	{"key": "diagonal", "x": 14.0, "walk": 1.8, "kind": "diagonal"},
	{"key": "push", "x": 21.0, "walk": 2.6, "kind": "push"},
	{"key": "multi", "x": 28.0, "walk": 1.6, "kind": "multi"},
	{"key": "narrow", "x": 35.0, "walk": 2.6, "kind": "narrow"},
	{"key": "thin", "x": 42.0, "walk": 2.5, "kind": "thin"},
	# 第八/九工位(第三十轮)**不按 W**:平台工位是"站上去看会不会被带走",
	# 载具工位由 _vehicle_step 自己按 E / W(keys = "" 起步)。
	{"key": "platform", "x": 49.0, "walk": 3.4, "kind": "platform", "keys": ""},
	{"key": "vehicle", "x": 56.0, "walk": 0.0, "kind": "vehicle", "keys": ""},
]
const SPAWN_Z := 6.0
const GROUND_Y := 0.9
# ── 传送之后先等相机追上,再按 W(用户 2026-10-06)────────────────────
# 测试场开着『随相机朝向』(按 W = 往镜头前方走)。弹簧臂的相机是
# **每帧 look_at(角色)**、而相机位置带平滑 —— 角色被传送到工位时
# 相机还在后面追,那一瞬间它"回头看着新位置",偏航是斜的(实测 -30°~-74°),
# 按 W 会斜着走过去、蹭着障碍物滑开(斜方块/窄缝/推箱子全都会歪)。
# 等 1.2 秒:平滑是"每秒跟手 8 倍",1.2 秒后残差 ~0.01%,偏航回正。
const SETTLE := 1.2
const DIAMOND_CENTER := Vector3(14.0, 1.0, -3.0)

var station_index := -1
var station_time := 0.0
var settle := 0.0
var min_z := 0.0
var max_y := 0.0
var min_x := 0.0
var max_x := 0.0
var max_coll := 0
var start_z := 0.0
var min_dist := 99.0
var box_start_z := 0.0
var rays_done := false
var first_vel_done := false
var walk_started := false
var min_y := 0.0
var plat_min_x := 0.0
var plat_max_x := 0.0
var vphase := 0
var vtime := 0.0
var vstart := Vector3.ZERO
var vcar_exit := Vector3.ZERO
var vyaw0 := 0.0
var vsteer := false
var vyaw := 0.0
var vgod_car := Vector3.ZERO
var vgod_drift := 0.0
var vgap_max := 0.0
var vdrove := 0.0
var vback_gap := 0.0
var vstand := Vector3.ZERO
var vwalked := 0.0
var vdrift := 0.0
var vmeta := false


func _ready() -> void:
	run_count += 1
	world = load("res://scenes/World.tscn").instantiate()
	add_child(world)
	var players := world.find_children("小玩家", "CharacterBody3D", true, false)
	if not players.is_empty():
		host = players[0] as CharacterBody3D
	var boxes := world.find_children("箱子", "RigidBody3D", true, false)
	if not boxes.is_empty():
		box = boxes[0] as RigidBody3D
	var platforms := world.find_children("移动平台", "AnimatableBody3D", true, false)
	if not platforms.is_empty():
		platform = platforms[0] as Node3D
	var vehicles := world.find_children("载具", "CharacterBody3D", true, false)
	if not vehicles.is_empty():
		vehicle = vehicles[0] as CharacterBody3D
	print("DEMO_GAME run=", run_count, " player=", host != null,
		" box=", box != null, " platform=", platform != null,
		" vehicle=", vehicle != null,
		" bodies=", world.find_children("Colliders", "Node3D", true, false).size())


func _send_key(code: int, pressed: bool) -> void:
	var event := InputEventKey.new()
	event.keycode = code
	event.physical_keycode = code
	event.pressed = pressed
	Input.parse_input_event(event)


## 侧视机位的"偏航"(度)—— 移动方向是**相机相对**的,偏航不对角色就走偏。
func _camera_yaw() -> String:
	var cameras := world.find_children("侧视机位", "Camera3D", true, false)
	if cameras.is_empty():
		return "<no-camera>"
	return "%.2f" % (cameras[0] as Camera3D).rotation_degrees.y


## 从某个高度朝 -Z 打一条射线(打多碰撞体的三块 / 两条缝)。
func _ray_hit(x: float, y: float) -> bool:
	var space := get_tree().root.world_3d.direct_space_state
	var query := PhysicsRayQueryParameters3D.create(
		Vector3(x, y, 0.5), Vector3(x, y, -3.5))
	return not space.intersect_ray(query).is_empty()


func _ray_checks() -> void:
	# 多碰撞体:腿(0.4)/身(1.6)/头(2.45)三条该命中;
	# 腿和身之间的缝(0.95)该**漏掉** —— 漏不掉说明三块被堆在一起了。
	var hits := 0
	for height in [0.4, 1.6, 2.45]:
		if _ray_hit(28.0, height):
			hits += 1
	print("PHYS_RAYS multi_hits=", hits, " gap_hit=", _ray_hit(28.0, 0.95))
	# 斜坡:三个高度朝 -Z 打,看**命中位置和法线** ——
	# 命中点应该随高度向 -Z 退(坡面是斜的),法线该接近朝上(≈20°)
	var space := get_tree().root.world_3d.direct_space_state
	for height in [0.3, 0.9, 1.5]:
		var query := PhysicsRayQueryParameters3D.create(
			Vector3(0.0, height, 4.0), Vector3(0.0, height, -6.0))
		var result := space.intersect_ray(query)
		var point: Vector3 = result.get("position", Vector3.ZERO)
		var normal: Vector3 = result.get("normal", Vector3.ZERO)
		print("PHYS_RAMP h=", height, " hit=", point, " n=", normal)


func _physics_process(delta: float) -> void:
	elapsed += delta
	if host == null:
		return
	if not rays_done and elapsed >= 0.4:
		rays_done = true
		_ray_checks()
	if station_index < 0:
		if elapsed >= 0.6:
			_start_station(0)
		return
	if station_time < 0.0:
		# 结算之后的等待(让物理稳定下来再换下一个工位)
		settle += delta
		if settle >= 0.35:
			if station_index + 1 >= STATION_DEFS.size():
				print("DEMO_DONE")
				set_physics_process(false)
			else:
				_start_station(station_index + 1)
		return
	station_time += delta
	var position := host.global_position
	if not walk_started and String(STATION_DEFS[station_index].get("keys", "W")) == "W":
		if station_time >= SETTLE:
			walk_started = true
			_send_key(KEY_W, true)
	if not first_vel_done and station_time >= SETTLE + 0.15:
		first_vel_done = true
		print("PHYS_VEL key=", STATION_DEFS[station_index]["key"],
			" vel=", host.velocity, " cam_yaw=", _camera_yaw())
	if STATION_DEFS[station_index]["kind"] == "vehicle":
		# 载具工位有自己的时序(等稳 → E 上车 → W 开 → E 下车 → W 走)
		_vehicle_step(delta)
		return
	min_z = minf(min_z, position.z)
	max_y = maxf(max_y, position.y)
	min_y = minf(min_y, position.y)
	min_x = minf(min_x, position.x)
	max_x = maxf(max_x, position.x)
	max_coll = maxi(max_coll, host.get_slide_collision_count())
	if STATION_DEFS[station_index]["kind"] == "diagonal":
		min_dist = minf(min_dist, position.distance_to(DIAMOND_CENTER))
	if STATION_DEFS[station_index]["kind"] == "platform" and platform != null:
		plat_min_x = minf(plat_min_x, platform.global_position.x)
		plat_max_x = maxf(plat_max_x, platform.global_position.x)
	if station_time >= float(STATION_DEFS[station_index]["walk"]) + SETTLE:
		_finish_station()


func _vehicle_step(delta: float) -> void:
	# 时序(每个阶段都记下要断言的东西,最后一起判):
	#   等稳 → E 上车 → W 开 1.5 秒 → E 下车 → W 走 0.7 秒
	# 按键**跨帧**按下/松开(同一帧里按了就松,"刚按下"可能只算一次)。
	if vehicle == null:
		_finish_vehicle()          # 找不到车 -> 打印里 ok=false,一眼看得出
		return
	vtime += delta
	var seat: Vector3 = vehicle.global_transform * (
		vehicle.get_meta("gk_seat", Vector3.ZERO) as Vector3)
	if vphase == 0 and vtime >= 0.4:
		vstart = vehicle.global_position
		vyaw0 = vehicle.rotation.y
		_send_key(KEY_E, true)
		vphase = 1
		vtime = 0.0
	elif vphase == 1 and vtime >= 0.3:
		_send_key(KEY_E, false)
		vmeta = bool(vehicle.get_meta("gk_driving", false))
		vgap_max = maxf(vgap_max, host.global_position.distance_to(seat))
		_send_key(KEY_W, true)
		vphase = 2
		vtime = 0.0
	elif vphase == 2:
		vgap_max = maxf(vgap_max, host.global_position.distance_to(seat))
		# 开一秒直路之后打一下方向盘(按 D)—— 车头该真的转过去
		if vtime >= 1.1 and not vsteer:
			vsteer = true
			_send_key(KEY_D, true)
		if vtime >= 1.5:
			_send_key(KEY_D, false)
			_send_key(KEY_W, false)
			vdrove = vstart.distance_to(vehicle.global_position)
			vyaw = absf(vehicle.rotation.y - vyaw0)
			# ── 上帝视角里不该还能开车(用户 2026-10-06 实测)─────────
			# 按 Tab 切上帝机,再按住 W 半秒:那时 WASD 是**飞相机**的,
			# 车一步都不该动(驾驶节点现在也有上帝守卫)。测完 Tab 切回来。
			vgod_car = vehicle.global_position
			_send_key(KEY_TAB, true)
			vphase = 5
			vtime = 0.0
	elif vphase == 5:
		if vtime >= 0.3:
			_send_key(KEY_TAB, false)
			_send_key(KEY_W, true)
			vphase = 6
			vtime = 0.0
	elif vphase == 6:
		if vtime >= 0.5:
			_send_key(KEY_W, false)
			vgod_drift = vgod_car.distance_to(vehicle.global_position)
			_send_key(KEY_TAB, true)      # 切回玩家相机
			vphase = 7
			vtime = 0.0
	elif vphase == 7 and vtime >= 0.3:
		_send_key(KEY_TAB, false)
		_send_key(KEY_E, true)        # 下车
		vphase = 3
		vtime = 0.0
	elif vphase == 3 and vtime >= 0.4:
		_send_key(KEY_E, false)
		vback_gap = host.global_position.distance_to(vehicle.global_position)
		vstand = host.global_position
		vcar_exit = vehicle.global_position
		_send_key(KEY_W, true)
		vphase = 4
		vtime = 0.0
	elif vphase == 4 and vtime >= 0.7:
		_send_key(KEY_W, false)
		vwalked = vstand.distance_to(host.global_position)
		vdrift = vcar_exit.distance_to(vehicle.global_position)
		_finish_vehicle()


func _finish_vehicle() -> void:
	# 上车了(meta) + 车真开出去了 + 角色一路被钉在座位上(ride_gap)
	# + 下车后角色在车外 + 控制权回到角色(walked)+ 车没被"偷开"(drift)
	# + 上帝视角里按 W **车不能动**(god_drift,用户 2026-10-06 实测的那条)
	var ok := (vmeta and vdrove >= 5.0 and vgap_max <= 0.8
		and vback_gap >= 1.5 and vwalked >= 1.0 and vdrift <= 0.3
		and vyaw >= 0.3 and vyaw <= 1.2 and vgod_drift <= 0.2)
	station_time = -1.0
	print("PHYS_vehicle ok=%s drove=%.2f ride_gap=%.2f back_gap=%.2f "
		% [ok, vdrove, vgap_max, vback_gap]
		+ "walked=%.2f drift=%.2f steer=%.2f god_drift=%.2f meta=%s"
		% [vwalked, vdrift, vyaw, vgod_drift, vmeta])


func _start_station(index: int) -> void:
	station_index = index
	var spec: Dictionary = STATION_DEFS[index]
	station_time = 0.0
	settle = 0.0
	var kind := String(spec["kind"])
	var start := Vector3(spec["x"], GROUND_Y, SPAWN_Z)
	if kind == "platform" and platform != null:
		# 站到平台**当前**位置上(平台正在往返,先读它的实时位置;
		# 上表面 +0.9 = 角色中心,+0.15 让它落一下、贴着平台)
		start = Vector3(platform.global_position.x,
			platform.global_position.y + 1.45, platform.global_position.z)
	if kind == "vehicle" and vehicle != null:
		# 站在车**旁边**(『上车距离』3 米以内),等 _vehicle_step 按 E
		start = Vector3(vehicle.global_position.x - 2.6, GROUND_Y,
			vehicle.global_position.z)
	host.global_position = start
	host.velocity = Vector3.ZERO
	# ── 把视角偏航钉回 0(用户 2026-10-06)─────────────────────────
	# 测试场开了『随相机朝向』(按 W = 往镜头前方走,手感才对);探针要的
	# 是**确定的世界 -Z**,所以每个工位开始时把偏航清零 —— 相机相对的方向
	# 变换之后就退化成世界轴,九个工位的判据全都不变。
	# (弹簧臂读的就是这个端口变量;不在这一帧清,传送后它会带着旧偏航。)
	host.set("输出_视角偏航", 0.0)
	min_z = start.z
	max_y = start.y
	min_y = start.y
	min_x = start.x
	max_x = start.x
	max_coll = 0
	start_z = start.z
	first_vel_done = false
	min_dist = 99.0
	box_start_z = box.global_position.z if box != null else 0.0
	plat_min_x = platform.global_position.x if platform != null else 0.0
	plat_max_x = plat_min_x
	vphase = 0
	vtime = 0.0
	walk_started = false
	vstart = vehicle.global_position if vehicle != null else Vector3.ZERO
	vcar_exit = vstart
	vgap_max = 0.0
	vdrove = 0.0
	vback_gap = 0.0
	vwalked = 0.0
	vdrift = 0.0
	vgod_car = Vector3.ZERO
	vgod_drift = 0.0
	vmeta = false
	# 平台 / 载具工位**不按 W**;其余工位的 W 在 _physics_process 里
	# **等相机追上之后**才按(见 SETTLE 的说明)—— 这里不能提前按,
	# 否则方向还是斜的(实测:斜方块会被蹭着滑过去)。
	print("PHYS_START key=", spec["key"], " t=", "%.2f" % elapsed,
		" at=", start)


func _finish_station() -> void:
	_send_key(KEY_W, false)
	var spec: Dictionary = STATION_DEFS[station_index]
	var kind := String(spec["kind"])
	var position := host.global_position
	var ok := false
	var detail := ""
	if kind == "slope":
		var climb := max_y - GROUND_Y
		var forward := start_z - position.z
		ok = climb >= 0.8 and forward >= 2.5 and position.y > 0.2
		detail = "climb=%.2f forward=%.2f end_y=%.2f" % [climb, forward, position.y]
	elif kind == "stairs":
		# 第二十九轮:开着『上台阶高度』0.5 —— 低台阶(0.35)该**上去**,
		# 高台阶(1.05,差 0.7)该**仍然挡住**:
		#   上去:踩到低台阶面 -> 中心 y = 0.9 + 0.35 = 1.25(抬腿瞬间到 1.4)
		#   挡住:停在第二级前脸(z=-2.1)外 —— 中心 z ≈ -1.6
		#   真上去了的话:y ≥ 1.95(站在 1.05 上)、z 会走到 -3 以外
		# ⚠ GDScript 不支持 `a <= b <= c` 链式比较(会按 (a<=b)<=c 解析)
		ok = (max_y >= 1.15 and max_y <= 1.6
			and position.z >= -2.0 and position.z <= -1.2)
		detail = "climb=%.2f max_y=%.2f min_z=%.2f end_z=%.2f" % [
			max_y - GROUND_Y, max_y, min_z, position.z]
	elif kind == "diagonal":
		ok = min_z >= -1.2 and min_dist >= 1.75
		detail = "min_z=%.2f min_dist=%.2f" % [min_z, min_dist]
	elif kind == "push":
		var moved := 0.0
		var clear := 0.0
		if box != null:
			moved = box_start_z - box.global_position.z
			clear = position.z - box.global_position.z
		ok = box != null and moved >= 0.5 and clear >= 1.0
		detail = "moved=%.2f clear=%.2f" % [moved, clear]
	elif kind == "multi":
		# 腿的前脸在 z=-2.0 -> 正常停在 z≈-1.5
		ok = min_z >= -1.7 and position.z <= -1.0
		detail = "min_z=%.2f end_z=%.2f" % [min_z, position.z]
	elif kind == "narrow":
		# 墙从 z=-1 到 -5:穿过去了才会走到 z ≤ -4.5
		ok = position.z <= -4.5
		detail = "end_z=%.2f min_z=%.2f" % [position.z, min_z]
	elif kind == "thin":
		# 墙前脸在 z=-2.875 -> 正常停在 z≈-2.375;穿透的话会到 -3 以外
		ok = min_z >= -2.6 and position.z <= -1.9
		detail = "min_z=%.2f end_z=%.2f" % [min_z, position.z]
	elif kind == "platform":
		# 第三十轮:平台横着往返,角色站上去要**被带走** ——
		# 两者的位移要对得上(差 ≤ 0.6);要是 StaticBody(平台
		# "瞬移"),角色位移≈0、还会从平台上掉到地面(min_y 掉下去)。
		var plat_span := plat_max_x - plat_min_x
		var carried := max_x - min_x
		ok = (plat_span >= 1.0 and carried >= plat_span - 0.6
			and min_y > GROUND_Y + 0.2)
		detail = "plat_span=%.2f carried=%.2f min_y=%.2f" % [
			plat_span, carried, min_y]
	station_time = -1.0
	var box_at := box.global_position if box != null else Vector3.ZERO
	print("PHYS_%s ok=%s %s t=%.2f at=(%.2f,%.2f,%.2f) x_range=[%.2f,%.2f] coll=%d box_z=%.2f"
		% [spec["key"], ok, detail, elapsed, position.x, position.y, position.z,
			min_x, max_x, max_coll, box_at.z])
'''

PROBE_TSCN = '''; 探针(临时文件,不写回示例)
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://probe.gd" id="1_probe"]

[node name="PhysicsProbe" type="Node"]
script = ExtResource("1_probe")
'''


def prepare():
    if not os.path.isdir(SRC):
        print(f"找不到示例工程 {SRC} —— 先跑 make_physics_demo.py")
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
                          # ⚠ 无头模式下主循环跑得比物理快(~160 fps),而
                          # `--quit-after` 数的是**主循环次数** —— 2400 次
                          # 只够 ~15 秒的物理时间。九个工位现在要 ~34 秒
                          # (每站还先等 1.2 秒让相机追上),给足余量。
                          "--quit-after", "9000"],
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
        if "PHYS" in line or "DEMO" in line or "SCRIPT ERROR" in line \
                or "Parse Error" in line:
            print(line.strip())
    ok = True
    for line in text.splitlines():
        if ("SCRIPT ERROR" in line or "Parse Error" in line
                or "DEMO_FAIL" in line):
            print("✗ Godot 里有脚本错误 / 解析错误:", line.strip())
            ok = False

    # ── 七个工位的现象断言 ──────────────────────────────────────────
    if not re.search(r"DEMO_GAME run=1 player=true box=true", text):
        print("✗ 开场状态不对(角色 / 刚体箱子 / 碰撞容器):",
              [line for line in text.splitlines() if "DEMO_GAME" in line])
        ok = False
    if "PHYS_RAYS multi_hits=3 gap_hit=false" not in text:
        print("✗ 多碰撞体不对(三块该命中、缝里该漏掉):",
              [line for line in text.splitlines() if "PHYS_RAYS" in line])
        ok = False
    for key, why in (
            ("slope", "斜坡走不上去 / 卡在坡脚"),
            ("stairs", "低台阶没迈上去 / 高台阶被骑上去了 / 穿模了"),
            ("diagonal", "斜方块的角被吃掉了(球碰撞会这样)"),
            ("push", "刚体箱子推不动 / 角色陷进箱子"),
            ("multi", "多碰撞体没挡住角色"),
            ("narrow", "窄缝卡住了"),
            ("thin", "薄墙被穿透了"),
            ("platform", "平台没把人带走(平台从脚下溜走 / 人掉下去了)"),
            ("vehicle", "上/下车或驾驶不对(没开出去 / 人没跟着 / 下车后车被偷开)")):
        if not re.search(rf"PHYS_{key} ok=true", text):
            print(f"✗ {why}:",
                  [line for line in text.splitlines()
                   if f"PHYS_{key} " in line or f"PHYS_START" in line])
            ok = False
    if "DEMO_DONE" not in text:
        print("✗ 探针没跑完(可能中途报错)")
        ok = False

    # ── 生成物层面的证据 ───────────────────────────────────────────
    script_path = os.path.join(OUT, "scripts", "game_logic.gd")
    if os.path.isfile(script_path):
        with open(script_path, "r", encoding="utf-8") as handle:
            code = handle.read()
        for needle, why in (
                ("apply_central_impulse(", "推开刚体的冲量"),
                ("get_slide_collision_count()", "碰撞循环"),
                ("is RigidBody3D:", "只推刚体"),
                ("KinematicCollision3D.new()", "上台阶①:挡住的是竖面"),
                ("test_move(", "上台阶②③:抬起来路通 + 落点有地"),
                ('set_meta("gk_driving"', "载具状态写在车自己身上"),
                ("rotation.y -= ", "A/D 转车头"),
                ("_gk_set_collision(", "上/下车开关角色碰撞"),
                ("gk_seat", "座位(钉住角色)"),
                ("_gk_resolve_camera(self)", "移动方向跟着相机朝向")):
            if needle not in code:
                print(f"✗ 生成的脚本里没有{why}: {needle}")
                ok = False
    else:
        print("✗ 没找到生成脚本:", script_path)
        ok = False
    world_tscn = os.path.join(OUT, "scenes", "World.tscn")
    if os.path.isfile(world_tscn):
        with open(world_tscn, "r", encoding="utf-8") as handle:
            scene_text = handle.read()
        for needle, why in (
                ('[node name="箱子" type="RigidBody3D"', "刚体箱子"),
                ('[node name="移动平台" type="AnimatableBody3D" '
                 'parent="Colliders"]', "移动平台是 AnimatableBody"),
                ('[node name="载具" type="CharacterBody3D" '
                 'parent="Colliders"]', "载具是 CharacterBody"),
                ('[node name="Model" parent="Colliders/移动平台" instance=',
                 "平台的网格跟着碰撞体(Model 复用 glb)"),
                ('[node name="Model" parent="Colliders/载具" instance=',
                 "载具的网格跟着碰撞体"),
                ('[node name="Collision2" type="CollisionShape3D" '
                 'parent="Colliders/拼装人"]',
                 "多碰撞体的第二块"),
                ('[node name="Collision3" type="CollisionShape3D" '
                 'parent="Colliders/拼装人"]',
                 "多碰撞体的第三块"),
                ('[node name="Colliders"', "碰撞代理容器")):
            if needle not in scene_text:
                print(f"✗ World.tscn 里没有{why}: {needle}")
                ok = False
        # 静态物体的碰撞代理必须**带上旋转**(斜坡 20° / 斜方块 45°)
        # —— 基写死单位阵的话,斜坡是块平砖、斜方块是轴对齐盒子
        for name, basis, why in (
                ("斜坡", "1.000000, 0.000000, 0.000000, 0.000000, 0.939693, "
                         "-0.342020, 0.000000, 0.342020, 0.939693",
                 "斜坡的碰撞体带上了 20° 旋转"),
                ("斜方块", "0.707107, 0.000000, 0.707107, 0.000000, "
                           "1.000000, 0.000000, -0.707107, 0.000000, 0.707107",
                 "斜方块的碰撞体带上了 45° 旋转")):
            match = re.search(
                rf'\[node name="{name}" type="StaticBody3D" parent="Colliders"\]\n'
                rf'transform = Transform3D\(([^)]*)\)', scene_text)
            if not match or not match.group(1).startswith(basis):
                print(f"✗ {why}:",
                      match.group(1) if match else "没找到那个节点")
                ok = False
    else:
        print("✗ 没找到主场景:", world_tscn)
        ok = False

    print("CHECK_PHYSICS_DEMO=" + ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())