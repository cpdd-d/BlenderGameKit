"""物理测试场的端到端检查(不用 Blender,要 Godot)。

用法:
    python scripts\\check_physics_demo.py
    python scripts\\check_physics_demo.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

它会:
  1. 把 `examples/demo_physics/物理测试场_godot` 拷到 `_dist/physics_probe`
     (**不碰示例本身**);
  2. 在副本里加一个探针场景(`probe.gd` + `probe.tscn`),把主场景指过去;
  3. 用 Godot 无头跑,读探针打印的 PHYS_* 行,汇总 PASS / FAIL。

探针查的是**物理现象**(不是代码长什么样):把角色依次传送到十个工位前,
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
    E 下车 → W 还能走(控制权交还角色)、车不跟着跑;车头顶着
    『车头箱子』—— 车必须**把它推开**(第三十五轮:用户实测
    "上车后推不动箱子");上帝视角里按 W 车不许动,
    按 T **时停**(世界冻住 / 上帝机照飞 / 再按一下解冻);
  * **绕墙追击**(第三十四轮):玩家站在墙这头不动,墙那头的『追踪者』
    由『移动_寻路』驱动 —— 必须**从墙端绕过来**(横向绕出 ≥ 2.8 米)
    并追上玩家;直线追只会在墙前贴住。顺带验导航网格真的烤出来了。
"""

import argparse
import glob
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

# 物理测试场的探针:把角色依次送到十个工位前,按住 W 走一遍,
# 边走边采样"有没有穿模 / 卡住 / 推不动"。结论行统一 PHYS_ 前缀。

static var run_count := 0

var world: Node = null
var host: CharacterBody3D = null
var box: RigidBody3D = null
var carbox: RigidBody3D = null
var platform: Node3D = null
var vehicle: CharacterBody3D = null
var chaser: CharacterBody3D = null
var elapsed := 0.0

# 十个工位:名字 / x / 走多久 / 判据用哪一套
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
	# 第十工位(第三十四轮):绕墙追击 —— 玩家站墙这头不动,追踪者从墙那头
	# **绕过来**(『移动_寻路』),由 _nav_step 采样。
	{"key": "nav", "x": 63.0, "walk": 6.0, "kind": "nav", "keys": ""},
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
# 绕墙墙工位(第三十四轮):墙在 Godot x=63 / z=0,宽 5 米(和
# make_physics_demo 的 NAV_WALL_SPAN 对齐,两边一起改)
const NAV_WALL_X := 63.0
const NAV_WALL_HALF := 2.5

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
var carbox_start_z := 0.0      # 车头箱子:开工位时的 z(推没推开看它)
var vpushed := 0.0
# 时停采样(第三十五轮)
var vstop_on := false          # 按 T 之后 `tree.paused` 真的变真了吗
var vstop_off := false         # 再按一下真的解冻了吗
var vstop_fly := 0.0           # 冻住期间上帝机飞了多远(必须照飞)
var vstop_world := 0.0         # 冻住期间世界动了多远(必须冻住)
var vstop_plat := Vector3.ZERO
var vstop_cam := Vector3.ZERO
# 第十工位(第三十四轮:寻路)采样
var nav_polys := 0            # 导航网格烤出来多少个多边形(0 = 没烤成)
var nav_arrived := false      # 追踪者最后有没有贴到玩家
var nav_around := 0.0         # 它离墙中线最远横走了多少(绕墙证据)
var nav_path := 0.0           # 追踪者走过的路程(采样累加)
var nav_prev := Vector3.ZERO
var nav_min_dist := 99.0


func _ready() -> void:
	run_count += 1
	# ── 探针自己必须"暂停也照跑"(第三十五轮)──────────────────────────
	# 时停那一段会把整个场景树冻住(tree.paused)—— 探针要是跟着停,
	# 就永远看不到"解冻"了(卡在 phase 8 里,后面全不打印)。
	# 它不是游戏的一部分,是观察者,所以设成 ALWAYS。
	process_mode = Node.PROCESS_MODE_ALWAYS
	world = load("res://scenes/World.tscn").instantiate()
	add_child(world)
	var players := world.find_children("小玩家", "CharacterBody3D", true, false)
	if not players.is_empty():
		host = players[0] as CharacterBody3D
	var boxes := world.find_children("箱子", "RigidBody3D", true, false)
	if not boxes.is_empty():
		box = boxes[0] as RigidBody3D
	var car_boxes := world.find_children("车头箱子", "RigidBody3D", true, false)
	if not car_boxes.is_empty():
		carbox = car_boxes[0] as RigidBody3D
	var platforms := world.find_children("移动平台", "AnimatableBody3D", true, false)
	if not platforms.is_empty():
		platform = platforms[0] as Node3D
	var vehicles := world.find_children("载具", "CharacterBody3D", true, false)
	if not vehicles.is_empty():
		vehicle = vehicles[0] as CharacterBody3D
	var chasers := world.find_children("追踪者", "CharacterBody3D", true, false)
	if not chasers.is_empty():
		chaser = chasers[0] as CharacterBody3D
	# 导航网格烤出来没有(第三十四轮):读区域里那份 mesh 的多边形数 ——
	# 「.tscn 里写了子资源 + 环境脚本烤成功」两件事一次验完。
	var regions := world.find_children("Navigation", "NavigationRegion3D", true, false)
	if not regions.is_empty():
		var mesh: NavigationMesh = (regions[0] as NavigationRegion3D).navigation_mesh
		if mesh != null:
			nav_polys = mesh.get_polygon_count()
	print("DEMO_GAME run=", run_count, " player=", host != null,
		" box=", box != null, " carbox=", carbox != null,
		" platform=", platform != null,
		" vehicle=", vehicle != null, " chaser=", chaser != null,
		" nav_polys=", nav_polys,
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


## 上帝机在哪(时停那段要看它"冻着也照飞")—— 按分组找,不认名字。
func _god_camera_position() -> Vector3:
	for node in get_tree().root.find_children("*", "Camera3D", true, false):
		if node.is_in_group("gk_god_camera"):
			return (node as Camera3D).global_position
	return Vector3.ZERO


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
	if STATION_DEFS[station_index]["kind"] == "nav":
		# 绕墙追击工位(第三十四轮):玩家站着不动,采样的对象是**追踪者**
		_nav_step(delta)
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
			# ── 车头箱子(第三十五轮,用户实测:"上车后推不动箱子")──────
			# 车头正前方摆着『车头箱子』—— 车开出去必须先把它顶开;
			# 推不动的话车会被它挡在路上(vdrove 也够不到 5)。
			if carbox != null:
				vpushed = carbox_start_z - carbox.global_position.z
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
			# ── 时停(第三十五轮,用户:"在上帝摄像机时可以添加时停")────
			# 上帝视角里按 T = 冻住世界。冻住之后**上帝机还得能飞**
			# (环境脚本设了 PROCESS_MODE_ALWAYS —— 探针自己也设了,
			# 见 _ready);世界那边(平台由时间轴推着走)必须**一动不动**。
			# 这一段的顺序:T 冻 → 按 W 飞 0.4 秒 → 量三件事 → T 解冻。
			vstop_plat = platform.global_position if platform != null else Vector3.ZERO
			_send_key(KEY_T, true)
			vphase = 8
			vtime = 0.0
	elif vphase == 8:
		if vtime >= 0.15:
			_send_key(KEY_T, false)
			vstop_on = get_tree().paused
			vstop_cam = _god_camera_position()
			_send_key(KEY_W, true)        # 冻着也要能飞
			vphase = 9
			vtime = 0.0
	elif vphase == 9:
		if vtime >= 0.4:
			_send_key(KEY_W, false)
			vstop_fly = vstop_cam.distance_to(_god_camera_position())
			if platform != null:
				vstop_world = vstop_plat.distance_to(platform.global_position)
			_send_key(KEY_T, true)        # 解冻
			vphase = 10
			vtime = 0.0
	elif vphase == 10:
		if vtime >= 0.15:
			_send_key(KEY_T, false)
			vstop_off = not get_tree().paused
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


func _nav_step(delta: float) -> void:
	# 绕墙追击(第三十四轮):每隔一帧采一次追踪者的位置 ——
	#   走了多少路(累加)、离墙中线最远横走多少(绕墙的证据)、
	#   有没有贴到玩家(直线追会在墙前贴住,永远到不了)。
	if chaser == null:
		return
	var here: Vector3 = chaser.global_position
	nav_path += nav_prev.distance_to(here)
	nav_prev = here
	nav_around = maxf(nav_around, absf(here.x - NAV_WALL_X))
	var dist := here.distance_to(host.global_position)
	nav_min_dist = minf(nav_min_dist, dist)
	if dist <= 1.4:
		nav_arrived = true
	# ── 时间到就结算(踩过的坑)─────────────────────────────────────
	# 通用那条结算写在 _physics_process 的**末尾**,而这个工位在中间
	# `return` 了(和载具工位一样)—— 不在这里自己判一次,这一站永远不会
	# 结束(实测:探针跑到 96 秒还在采样,D 都没打印出来)。
	if station_time >= float(STATION_DEFS[station_index]["walk"]) + SETTLE:
		_finish_station()


func _finish_vehicle() -> void:
	# 上车了(meta) + 车真开出去了 + 角色一路被钉在座位上(ride_gap)
	# + 下车后角色在车外 + 控制权回到角色(walked)+ 车没被"偷开"(drift)
	# + 上帝视角里按 W **车不能动**(god_drift,用户 2026-10-06 实测的那条)
	# + 车头箱子**被推开**(pushed,用户实测:"上车后推不动箱子")
	# + 时停三件事:按 T 真的冻上 / 再按真的解冻 / 冻着上帝机照飞、
	#   世界一动不动(第三十五轮)
	var ok := (vmeta and vdrove >= 5.0 and vgap_max <= 0.8
		and vback_gap >= 1.5 and vwalked >= 1.0 and vdrift <= 0.3
		and vyaw >= 0.3 and vyaw <= 1.2 and vgod_drift <= 0.2
		and vpushed >= 0.5
		and vstop_on and vstop_off and vstop_fly >= 1.0
		and vstop_world <= 0.15)
	station_time = -1.0
	print("PHYS_vehicle ok=%s drove=%.2f ride_gap=%.2f back_gap=%.2f "
		% [ok, vdrove, vgap_max, vback_gap]
		+ "walked=%.2f drift=%.2f steer=%.2f god_drift=%.2f meta=%s"
		% [vwalked, vdrift, vyaw, vgod_drift, vmeta]
		+ " pushed=%.2f stop=%s/%s fly=%.2f frozen=%.2f"
		% [vpushed, vstop_on, vstop_off, vstop_fly, vstop_world])


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
	if kind == "nav":
		# 站到墙的**这一侧**(Godot z=+2;墙在 z=0、追踪者在 z=-2)——
		# 玩家不动,就看追踪者绕不绕得过来。
		start = Vector3(spec["x"], GROUND_Y, 2.0)
	host.global_position = start
	host.velocity = Vector3.ZERO
	if kind == "nav":
		# 追击怎么开:图里那句话是"走进『追击触发区』就置真"(人玩也生效)
		# —— 探针把玩家**传送进触发区**里,Area3D 的 body_entered 下一帧
		# 就会把『追击开关』打开,和玩家自己走进去走的是同一条链。
		# (第三十四轮这里是探针直接 `host.set("追击开关", true)` 的 ——
		#  那是"只有探针能开追"的漏洞,用户实测:自己玩追踪者一动不动。)
		if chaser != null:
			nav_prev = chaser.global_position
		nav_around = 0.0
		nav_path = 0.0
		nav_min_dist = 99.0
		nav_arrived = false
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
	carbox_start_z = carbox.global_position.z if carbox != null else 0.0
	vpushed = 0.0
	vstop_on = false
	vstop_off = false
	vstop_fly = 0.0
	vstop_world = 0.0
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
	elif kind == "nav":
		# 第三十四轮:追踪者必须**从墙端绕过去**才算过 ——
		#   around ≥ 2.8:墙半宽只有 2.5,超过它 = 真的绕了(直线追最多
		#     走到墙前 z≈±0.9,横向一步都不会多走);
		#   arrived:绕完还要**贴上玩家**(直线追在墙前贴住、永远到不了);
		#   polys > 0:导航网格真的烤出来了(子资源 + 环境脚本两件事)。
		ok = (nav_polys > 0 and nav_arrived and nav_around >= 2.8)
		detail = "polys=%d around=%.2f arrived=%s path=%.2f min_dist=%.2f" % [
			nav_polys, nav_around, nav_arrived, nav_path, nav_min_dist]
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
                          # ⚠ 无头模式下主循环跑得比物理快,而 `--quit-after`
                          # 数的是**主循环次数** —— 2400 次只够 ~15 秒的物理
                          # 时间。十个工位现在要 ~44 秒(每站还先等 1.2 秒让
                          # 相机追上,⑩ 追人自己跑 ~6 秒;开着寻路后每帧还要
                          # 喂导航服务器,帧率比原来低)—— 给到 4 倍余量。
                          "--quit-after", "20000"],
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

    # ── 十个工位的现象断言 ──────────────────────────────────────────
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
            ("vehicle", "上/下车或驾驶不对(没开出去 / 人没跟着 / 下车后车被偷开"
                        " / 车撞箱子推不动 / 时停开不了或解不开)"),
            ("nav", "追踪者没绕墙追到人(导航网格没烤出来 / 绕不过去 / 贴不上)")):
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
                ("_gk_resolve_camera(self)", "移动方向跟着相机朝向"),
                ("NavigationServer3D.map_get_path(", "寻路:走的是服务器算的绕墙路径")):
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
                ('[node name="车头箱子" type="RigidBody3D"', "车头箱子(车推箱子)"),
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
                ('[node name="追踪者" type="CharacterBody3D" '
                 'parent="Colliders"]', "追踪者是 CharacterBody"),
                # ── 寻路(第三十四轮):子资源 + 区域节点 + 只解析静态碰撞体 ──
                ('[sub_resource type="NavigationMesh" id="gk_navmesh"]',
                 "导航网格子资源"),
                ("geometry_parsed_geometry_type = 1",
                 "导航网格只解析静态碰撞体(角色/装饰网格不进网格)"),
                ('[node name="Navigation" type="NavigationRegion3D" parent="."]',
                 "导航区节点"),
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

    # ── 时停(第三十五轮):环境脚本里真的生成出来了 ─────────────────
    # 时停 / 上帝机都长在**环境脚本**里(不是角色的游戏逻辑)—— 上面那些
    # PHYS_vehicle 的断言验的是"现象",这里顺手锁一下"那一截代码真的在",
    # 免得哪天模板改了字段名、探针却因为降级路径静默放过。
    env_files = glob.glob(os.path.join(OUT, "scripts", "gk_environment*.gd"))
    if not env_files:
        print("✗ 没找到环境脚本(上帝机 / 时停都在里面)")
        ok = False
    else:
        with open(env_files[0], "r", encoding="utf-8") as handle:
            env_code = handle.read()
        for needle, why in (
                ("const _GK_TIME_STOP_KEY := KEY_T", "时停键(默认 T)"),
                ("func _gk_time_stop_tick()", "时停逻辑"),
                ("process_mode = Node.PROCESS_MODE_ALWAYS",
                 "环境脚本暂停也照跑(不设的话冻住就解不开了)")):
            if needle not in env_code:
                print(f"✗ 环境脚本里没有{why}: {needle}")
                ok = False

    print("CHECK_PHYSICS_DEMO=" + ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())