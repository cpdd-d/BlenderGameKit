"""灯光示例的自动检查(第四十八轮:阶段①灯光扩展)。

用法:
    python scripts\\check_lights_demo.py
    python scripts\\check_lights_demo.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

它会:
  1. 把 `examples/demo_lights/灯光示例_godot` 拷到 `_dist/lights_probe`
     (**不碰示例本身**);
  2. 在副本里加一个探针场景(`probe.gd` + `probe.tscn`),把主场景指过去;
  3. 用 Godot 无头跑,读探针打印的 LIGHTS_* 行,汇总 PASS / FAIL。

探针查的是"这一关该有的现象"(不是代码长什么样):
  * **三种灯都在**:月光/台灯/手电筒分别是 DirectionalLight3D /
    OmniLight3D / SpotLight3D;
  * **数值对得上**:台灯 omni_range≈8、手电筒 spot_angle≈20(半角)、
    月光 light_color≈(0.6,0.7,1.0)、手电筒不投影 / 月光投影;
  * **手电筒跟着相机**:跑几帧后它的位置和当前相机几乎重合;
  * **开关灯**:按 4 三盏全灭,再按 1 只有手电筒亮;
  * **卡通材质跟光**:全关时 light_dir≈0(平涂);只开手电筒且相机对着
    角色时,light_dir 正好指向"手电筒 -> 角色"。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "examples", "demo_lights", "灯光示例_godot")
OUT = os.path.join(ROOT, "_dist", "lights_probe")

PROBE_GD = '''extends Node

# 灯光示例的探针:验三种灯 / 手电筒跟随相机 / 开关灯 / 卡通材质跟光。
# 结论行统一用 LIGHTS_ 前缀,Python 那边按前缀收集、按内容断言。

var world: Node = null
var moon: Light3D = null
var lamp: Light3D = null
var torch: Light3D = null
var character: Node3D = null
var toon_mat: ShaderMaterial = null
var elapsed := 0.0
var phase := 0


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	world = load("res://scenes/World.tscn").instantiate()
	add_child(world)
	moon = world.find_child("月光", true, false) as Light3D
	lamp = world.find_child("台灯", true, false) as Light3D
	torch = world.find_child("手电筒", true, false) as Light3D
	character = world.find_child("角色", true, false) as Node3D


## 角色网格上的卡通材质(环境脚本运行时会挂上 material_override)。
## ⚠ 不能在 _ready 里找 —— 那时材质还没挂(见环境脚本的 call_deferred)。
func _find_toon() -> ShaderMaterial:
	if character == null:
		return null
	for mesh in character.find_children("*", "MeshInstance3D", true, false):
		var node := mesh as MeshInstance3D
		var m := node.material_override
		if m is ShaderMaterial:
			return m as ShaderMaterial
		for i in range(node.get_surface_override_material_count()):
			var sm := node.get_surface_override_material(i)
			if sm is ShaderMaterial:
				return sm as ShaderMaterial
	return null


## 第九十四轮:背面剔除的对照报告 —— 地面(Blender 里勾了『背面剔除』)
## 该换成**剔面版**着色器,角色(没勾)仍是双面板。材质是环境脚本在
## call_deferred 里挂的,所以和别的报告一样放在 phase 0。
func _mesh_named(node_name: String) -> MeshInstance3D:
	# ⚠ 不能直接 find_child:同名节点先撞上的是 Colliders 里的碰撞代理
	#   (StaticBody3D),那不是网格。
	for node in world.find_children(node_name, "", true, false):
		if node is MeshInstance3D:
			return node as MeshInstance3D
	return null


func _toon_path_in(root_node: Node) -> String:
	# 一个物体身上第一条卡通材质的着色器路径(整网格覆盖 / 按表面覆盖都认)
	if root_node == null:
		return "<no-node>"
	var meshes: Array = []
	if root_node is MeshInstance3D:
		meshes.append(root_node)
	meshes += root_node.find_children("*", "MeshInstance3D", true, false)
	for item in meshes:
		var node := item as MeshInstance3D
		var whole := node.material_override
		if whole is ShaderMaterial and (whole as ShaderMaterial).shader != null:
			return (whole as ShaderMaterial).shader.resource_path
		for index in range(node.get_surface_override_material_count()):
			var surface := node.get_surface_override_material(index)
			if surface is ShaderMaterial and (surface as ShaderMaterial).shader != null:
				return (surface as ShaderMaterial).shader.resource_path
	return "<no-material>"


func _report_cull() -> void:
	var ground_path := _toon_path_in(_mesh_named("地面"))
	var player_path := _toon_path_in(character)
	var ok := ground_path.ends_with("gk_toon_cull.gdshader") \\
		and player_path.ends_with("gk_toon.gdshader")
	print("LIGHTS_CULL ground=", ground_path.get_file(),
		" player=", player_path.get_file(), " ok=", ok)


## 第九十八轮:可见距离 / 阴影开销 —— 三样都要真的设上。
## 表写在环境脚本里,运行时由 _apply_render_tuning 应用(网格在 glb 实例
## 内部,写 .tscn 会被 Godot 丢掉,所以只能运行时装)。
func _report_perf() -> void:
	var ground := _mesh_named("地面")
	var vis := -1.0
	var shadow := -1
	if ground != null:
		vis = ground.visibility_range_end
		shadow = ground.cast_shadow
	var sun_shadow := -1.0
	if moon is DirectionalLight3D:
		sun_shadow = (moon as DirectionalLight3D).directional_shadow_max_distance
	# cast_shadow 0 = OFF(GeometryInstance3D.SHADOW_CASTING_SETTING_OFF)
	var ok := ground != null and absf(vis - 60.0) <= 0.1 and shadow == 0 \\
		and absf(sun_shadow - 50.0) <= 0.1
	print("LIGHTS_PERF vis=", "%.2f" % vis, " cast_shadow=", shadow,
		" sun_shadow=", "%.2f" % sun_shadow, " ok=", ok)


func _send_key(code: int, pressed: bool) -> void:
	var event := InputEventKey.new()
	event.keycode = code
	event.physical_keycode = code
	event.pressed = pressed
	Input.parse_input_event(event)


func _light_dir() -> Vector3:
	if toon_mat == null:
		return Vector3(999.0, 999.0, 999.0)
	var value = toon_mat.get_shader_parameter("light_dir")
	if value == null:
		return Vector3(999.0, 999.0, 999.0)
	return value as Vector3


func _vis(light: Light3D) -> String:
	if light == null:
		return "<none>"
	return "on" if light.visible else "off"


func _report_types() -> void:
	var ok := moon is DirectionalLight3D and lamp is OmniLight3D \\
		and torch is SpotLight3D
	print("LIGHTS_TYPES moon=", moon.get_class() if moon != null else "<none>",
		" lamp=", lamp.get_class() if lamp != null else "<none>",
		" torch=", torch.get_class() if torch != null else "<none>",
		" ok=", ok)


func _report_params() -> void:
	var lamp_range := -1.0
	if lamp is OmniLight3D:
		lamp_range = (lamp as OmniLight3D).omni_range
	var torch_angle := -1.0
	if torch is SpotLight3D:
		torch_angle = (torch as SpotLight3D).spot_angle
	var moon_col := Color.BLACK
	if moon != null:
		moon_col = moon.light_color
	var torch_shadow := true
	if torch != null:
		torch_shadow = torch.shadow_enabled
	var moon_shadow := false
	if moon != null:
		moon_shadow = moon.shadow_enabled
	var ok := absf(lamp_range - 8.0) <= 0.5 \\
		and absf(torch_angle - 20.0) <= 0.5 \\
		and absf(moon_col.r - 0.6) <= 0.05 \\
		and absf(moon_col.g - 0.7) <= 0.05 \\
		and absf(moon_col.b - 1.0) <= 0.05 \\
		and (not torch_shadow) and moon_shadow
	print("LIGHTS_PARAMS lamp_range=", "%.3f" % lamp_range,
		" torch_angle=", "%.3f" % torch_angle,
		" moon_color=", "%.3f/%.3f/%.3f" % [moon_col.r, moon_col.g, moon_col.b],
		" torch_shadow=", torch_shadow, " moon_shadow=", moon_shadow,
		" ok=", ok)


func _report_follow() -> void:
	var cam := get_viewport().get_camera_3d()
	var dist := -1.0
	var cam_pos := Vector3.ZERO
	var torch_pos := Vector3.ZERO
	if cam != null:
		cam_pos = cam.global_position
	if torch != null:
		torch_pos = torch.global_position
	if cam != null and torch != null:
		dist = torch_pos.distance_to(cam_pos)
	var ok := cam != null and torch != null and dist < 0.05
	print("LIGHTS_FOLLOW dist=", "%.4f" % dist,
		" cam=", cam.name if cam != null else "<none>",
		" cam_pos=", cam_pos, " torch_pos=", torch_pos, " ok=", ok)


func _physics_process(delta: float) -> void:
	elapsed += delta
	if phase == 0 and elapsed >= 0.8:
		phase = 1
		if toon_mat == null:
			toon_mat = _find_toon()
		_report_types()
		_report_params()
		_report_follow()
		_report_cull()
		_report_perf()
		# 按 4 = 三盏全关
		_send_key(KEY_4, true)
	elif phase == 1 and elapsed >= 1.2:
		phase = 2
		_send_key(KEY_4, false)
		var all_off := moon != null and lamp != null and torch != null \\
			and (not moon.visible) and (not lamp.visible) and (not torch.visible)
		print("LIGHTS_TOGGLE_OFF moon=", _vis(moon), " lamp=", _vis(lamp),
			" torch=", _vis(torch), " ok=", all_off)
		var zero_len := _light_dir().length()
		var zero_ok := toon_mat != null and zero_len < 0.001
		print("LIGHTS_TOON_ZERO len=", "%.5f" % zero_len,
			" mat=", toon_mat != null, " ok=", zero_ok)
		# 再按 1 = 只开手电筒
		_send_key(KEY_1, true)
	elif phase == 2 and elapsed >= 1.6:
		phase = 3
		_send_key(KEY_1, false)
		var only_torch := torch != null and torch.visible \\
			and lamp != null and (not lamp.visible) \\
			and moon != null and (not moon.visible)
		print("LIGHTS_TOGGLE moon=", _vis(moon), " lamp=", _vis(lamp),
			" torch=", _vis(torch), " ok=", only_torch)
		var dir := _light_dir()
		var want := Vector3.ZERO
		var dot := -2.0
		if character != null and torch != null:
			want = (character.global_position - torch.global_position).normalized()
			if dir.length() > 0.001:
				dot = dir.normalized().dot(want)
		var pick_ok := absf(dir.length() - 1.0) <= 0.05 and dot > 0.9
		print("LIGHTS_TOON_PICK len=", "%.4f" % dir.length(),
			" dot=", "%.4f" % dot, " dir=", dir, " want=", want,
			" ok=", pick_ok)
		# ── 第五十一轮:屏幕暗角(阶段④屏幕氛围)──────────────────────
		_report_vignette()
		_send_key(KEY_5, true)                 # 按 5 = 暗角加深(带 0.35 秒过渡)
	elif phase == 3 and elapsed >= 1.75:
		phase = 4
		_send_key(KEY_5, false)
	elif phase == 4 and elapsed >= 2.3:
		phase = 5
		_report_vignette_change("LIGHTS_VIGNETTE_DEEP", 0.95, 0.45)
		_send_key(KEY_6, true)                 # 按 6 = 放松
	elif phase == 5 and elapsed >= 2.45:
		phase = 6
		_send_key(KEY_6, false)
	elif phase == 6 and elapsed >= 3.0:
		phase = 7
		_report_vignette_change("LIGHTS_VIGNETTE_RELAX", 0.15, 0.75)
		print("LIGHTS_DONE")
		set_physics_process(false)
		# 查完就自己退:无头模式一帧花多少**真实**时间是机器相关的,
		# 靠 --quit-after 卡帧数容易"还没跑到最后一条就被掐了"(实测)。
		get_tree().quit()


## ── 第五十一轮:屏幕暗角 ─────────────────────────────────────────
## 暗角层:GKVignette(CanvasLayer 92)-> ColorRect(全屏)-> ShaderMaterial。
## ⚠ 名字 / 层号都是**生成物的一部分**,探针按名字找它 —— 改名就会在这里报红。
func _vignette_layer() -> CanvasLayer:
	var found := world.find_child("GKVignette", true, false)
	if found is CanvasLayer:
		return found as CanvasLayer
	return null


func _vignette_rect() -> ColorRect:
	var layer := _vignette_layer()
	if layer == null:
		return null
	for child in layer.get_children():
		if child is ColorRect:
			return child as ColorRect
	return null


func _vignette_mat() -> ShaderMaterial:
	var rect := _vignette_rect()
	if rect == null:
		return null
	var mat := rect.material
	if mat is ShaderMaterial:
		return mat as ShaderMaterial
	return null


func _vparam(name: String) -> float:
	var mat := _vignette_mat()
	if mat == null:
		return -1.0
	var value = mat.get_shader_parameter(name)
	if value == null:
		return -1.0
	return float(value)


## 开场那一层:层号 92、全屏矩形不吃鼠标、四个参数都对(强度 0.5 / 内圈 0.6 /
## 柔和 0.4 / 颜色近黑)。
func _report_vignette() -> void:
	var layer := _vignette_layer()
	var rect := _vignette_rect()
	var mat := _vignette_mat()
	var layer_ok: bool = layer != null and layer.layer == 92
	var rect_ok: bool = rect != null \\
		and rect.mouse_filter == Control.MOUSE_FILTER_IGNORE
	var amount := _vparam("vignette_amount")
	var radius := _vparam("vignette_radius")
	var edge := _vparam("vignette_edge")
	var col := Color(9.0, 9.0, 9.0, 1.0)
	if mat != null:
		var raw = mat.get_shader_parameter("vignette_color")
		if raw is Color:
			col = raw
	var color_ok: bool = col.r < 0.02 and col.g < 0.02 and col.b < 0.02
	var ok: bool = mat != null and layer_ok and rect_ok and color_ok \\
		and absf(amount - 0.5) <= 0.02 and absf(radius - 0.6) <= 0.02 \\
		and absf(edge - 0.4) <= 0.02
	print("LIGHTS_VIGNETTE ok=", ok, " layer=", layer_ok, " rect=", rect_ok,
		" amount=", "%.3f" % amount, " radius=", "%.3f" % radius,
		" edge=", "%.3f" % edge, " color=", col)


## 按 5 / 6 之后:强度**渐变**到新值(过渡 0.35 秒),内圈立刻换。
func _report_vignette_change(tag: String, want_amount: float,
		want_radius: float) -> void:
	var amount := _vparam("vignette_amount")
	var radius := _vparam("vignette_radius")
	var ok: bool = absf(amount - want_amount) <= 0.03 \\
		and absf(radius - want_radius) <= 0.02
	print(tag, " amount=", "%.3f" % amount,
		" radius=", "%.3f" % radius, " ok=", ok)
'''

PROBE_TSCN = '''; 探针(临时文件,不写回示例)
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://probe.gd" id="1_probe"]

[node name="LightsProbe" type="Node"]
script = ExtResource("1_probe")
'''


def prepare():
    if not os.path.isdir(SRC):
        print(f"找不到示例工程 {SRC} —— 先跑 make_lights_demo.py")
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
        if "LIGHTS_" in line or "SCRIPT ERROR" in line or "Parse Error" in line:
            print(line.strip())

    ok = True
    if any(("SCRIPT ERROR" in line) or ("Parse Error" in line)
           for line in text.splitlines()):
        print("✗ Godot 里有脚本错误 / 解析错误")
        ok = False

    if not re.search(r"LIGHTS_TYPES moon=DirectionalLight3D "
                     r"lamp=OmniLight3D torch=SpotLight3D ok=true", text):
        print("✗ 三种灯没都导出(名字 / 类型不对):",
              [line for line in text.splitlines() if "LIGHTS_TYPES" in line])
        ok = False
    if not re.search(r"LIGHTS_PARAMS .*ok=true", text):
        print("✗ 灯光参数对不上(range / spot_angle / 颜色 / 阴影):",
              [line for line in text.splitlines() if "LIGHTS_PARAMS" in line])
        ok = False
    if not re.search(r"LIGHTS_FOLLOW .*ok=true", text):
        print("✗ 手电筒没有跟着相机(位置没贴上去):",
              [line for line in text.splitlines() if "LIGHTS_FOLLOW" in line])
        ok = False
    if not re.search(r"LIGHTS_TOGGLE_OFF .*ok=true", text):
        print("✗ 按 4 没把三盏灯全关掉:",
              [line for line in text.splitlines() if "LIGHTS_TOGGLE_OFF" in line])
        ok = False
    if not re.search(r"LIGHTS_TOGGLE .*ok=true", text):
        print("✗ 按 1 之后不是『只有手电筒亮』:",
              [line for line in text.splitlines() if "LIGHTS_TOGGLE " in line])
        ok = False
    if not re.search(r"LIGHTS_TOON_ZERO len=[\d.]+ mat=true ok=true", text):
        print("✗ 灯全关时卡通材质 light_dir 没被置零(还在画假阴影):",
              [line for line in text.splitlines() if "LIGHTS_TOON_ZERO" in line])
        ok = False
    if not re.search(r"LIGHTS_TOON_PICK len=[\d.]+ dot=[\d.]+ .*ok=true", text):
        print("✗ 只开手电筒时卡通材质的光方向不对(没指向手电筒->角色):",
              [line for line in text.splitlines() if "LIGHTS_TOON_PICK" in line])
        ok = False
    if not re.search(r"LIGHTS_VIGNETTE .*ok=true", text):
        print("✗ 屏幕暗角层不对(名字 / 层号 / 矩形 / 四个参数):",
              [line for line in text.splitlines() if "LIGHTS_VIGNETTE" in line])
        ok = False
    if not re.search(r"LIGHTS_VIGNETTE_DEEP .*ok=true", text):
        print("✗ 按 5 暗角没加深(强度没到 0.95 / 内圈没到 0.45):",
              [line for line in text.splitlines() if "LIGHTS_VIGNETTE_DEEP" in line])
        ok = False
    if not re.search(r"LIGHTS_VIGNETTE_RELAX .*ok=true", text):
        print("✗ 按 6 暗角没放松(强度没到 0.15 / 内圈没到 0.75):",
              [line for line in text.splitlines() if "LIGHTS_VIGNETTE_RELAX" in line])
        ok = False
    # 数值层面再兜一层(别只信探针自己算的 ok)
    zero = re.search(r"LIGHTS_TOON_ZERO len=([\d.]+)", text)
    if zero and float(zero.group(1)) >= 0.001:
        print("✗ light_dir 长度不是 0:", zero.group(1))
        ok = False
    pick = re.search(r"LIGHTS_TOON_PICK len=([\d.]+) dot=([-\d.]+)", text)
    if pick:
        if abs(float(pick.group(1)) - 1.0) > 0.05:
            print("✗ light_dir 没归一化:", pick.group(1))
            ok = False
        if float(pick.group(2)) <= 0.9:
            print("✗ light_dir 和『手电筒->角色』方向不符:", pick.group(2))
            ok = False
    if "LIGHTS_DONE" not in text:
        print("✗ 探针没跑完(可能中途报错)")
        ok = False

    # ── 第九十四轮:背面剔除 ────────────────────────────────────────
    # 运行时:地面(勾了)用剔面版着色器,角色(没勾)保持双面 —— 对照断言
    cull = re.search(r"LIGHTS_CULL ground=(\S+) player=(\S+) ok=(\w+)", text)
    if not cull:
        print("✗ 没跑出背面剔除的结论行(LIGHTS_CULL)")
        ok = False
    elif cull.group(3) != "true":
        print("✗ 背面剔除没走到导出(地面该用剔面版 / 角色保持双面):",
              cull.group(0))
        ok = False
    else:
        print(f"    背面剔除: 地面={cull.group(1)} 角色={cull.group(2)}")

    # ── 第九十八轮:可见距离 / 阴影开销 ─────────────────────────────
    # 三样都由环境脚本在运行时设(网格在 glb 实例内部,.tscn 覆盖会被丢掉)
    perf = re.search(
        r"LIGHTS_PERF vis=([\d.]+) cast_shadow=(-?\d+) "
        r"sun_shadow=([\d.]+) ok=(\w+)", text)
    if not perf:
        print("✗ 没跑出可见距离 / 阴影开销的结论行(LIGHTS_PERF)")
        ok = False
    elif perf.group(4) != "true":
        print("✗ 可见距离 / 阴影开销没生效:", perf.group(0))
        ok = False
    else:
        print(f"    性能: 可见距离={perf.group(1)} 米"
              f" 地面cast_shadow={perf.group(2)}(0=不投)"
              f" 月光阴影={perf.group(3)} 米")

    # ── 生成物层面的证据 ────────────────────────────────────────────
    script_path = os.path.join(OUT, "scripts", "game_logic.gd")
    if os.path.isfile(script_path):
        with open(script_path, "r", encoding="utf-8") as handle:
            code = handle.read()
        for needle, why in (
                ('_gk_set_light(self, "手电筒", (not 手电筒开), 0.0, null)',
                 "手电筒的开关(取反)"),
                ('_gk_set_light(self, "台灯", false, 0.0, null)',
                 "台灯的开关"),
                ('_gk_set_light(self, "月光", false, 0.0, null)',
                 "月光的开关"),
                ("var 手电筒开: bool", "手电筒状态变量"),
                ("func _gk_set_light(", "灯光设置小工具"),
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
                ('[node name="月光" type="DirectionalLight3D"', "月光(平行光)"),
                ('[node name="台灯" type="OmniLight3D"', "台灯(点光)"),
                ('[node name="手电筒" type="SpotLight3D"', "手电筒(聚光)"),
                ("omni_range = 8.000", "台灯范围"),
                ("spot_angle = 20.000", "手电筒锥角(半角)"),
                ("shadow_enabled = false", "手电筒不投影"),
        ):
            if needle not in scene:
                print(f"✗ World.tscn 里没有{why}: 找不到 {needle!r}")
                ok = False
    else:
        print(f"✗ 没找到主场景 {scene_path}")
        ok = False

    env_path = os.path.join(OUT, "scripts", "gk_environment.gd")
    if os.path.isfile(env_path):
        with open(env_path, "r", encoding="utf-8") as handle:
            env = handle.read()
        for needle, why in (
                ('@export var follow_lights: String = "手电筒"',
                 "手电筒写进了 follow_lights"),
                ("@export var follow_scene_light: bool = true",
                 "卡通跟随场景光照开着"),
                # 第九十八轮:三张"少画"表 + 运行时应用函数
                ('var visibility_ranges: String = "地面=60.0"', "可见距离表"),
                ('var no_shadow_nodes: String = "地面"', "不投阴影表"),
                ('var light_shadows: String = "月光=50.0"', "阴影距离表"),
                ("func _apply_render_tuning() -> void:", "运行时应用函数"),
        ):
            if needle not in env:
                print(f"✗ gk_environment.gd 里没有{why}: 找不到 {needle!r}")
                ok = False
    else:
        print(f"✗ 没找到环境脚本 {env_path}")
        ok = False

    print("CHECK_LIGHTS_DEMO=" + ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())