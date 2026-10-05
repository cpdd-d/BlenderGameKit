"""第十二轮验证:曲线运镜 + 时间轴 —— 真 Blender 建曲线 -> 导出 -> 真 Godot 跑。

用户这一轮要的两件事:
    1. "弹簧臂用一个曲线来从末尾到起点的位置变化……预览不了,想要曲线运动
       还要连一大堆节点"  -> 『相机_切换』的『沿曲线运镜』+『过渡曲线』
    2. "动画的时间轴好像还没设计完成,虚幻蓝图的设计逻辑还不错" -> 『定时_时间轴』

这条探针把整条链真跑一遍(离线测试只能验到"生成的代码长什么样"):
    画一条 3D 轨道 + 一条 2D 缓动曲线
      -> 节点图里用上(相机沿曲线运镜 / 时间轴曲线 / 曲线轨道)
      -> 导出:采样写 curves/*.tres + 生成脚本
      -> Godot 真跑:读 Alpha / 轨道值 / 曲线位置 / 相机实际位置

跑法:
    blender -b --python scripts\\make_curve_probe.py
    godot --headless --path _dist\\curve_probe_godot --quit-after 300

断言(在 Godot 的探针脚本里打印,用下面的标记搜出来):
    CURVE_PROBE ...      每一刻的采样值
    CURVE_PROBE_DONE     全部打完
"""

import math
import os
import shutil
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "_dist", "curve_probe_godot")


def log(message):
    print(f"[曲线探针] {message}")


# ==========================================================================
# 1) 场景:地面 + 角色(挂脚本) + 两台相机 + 两条曲线
# ==========================================================================
def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for group in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(group)
    for curve in list(bpy.data.curves):
        bpy.data.curves.remove(curve)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    return bpy.context.scene


def make_box(name, size, location):
    sx, sy, sz = [v * 0.5 for v in size]
    verts = [(-sx, -sy, -sz), (sx, -sy, -sz), (sx, sy, -sz), (-sx, sy, -sz),
             (-sx, -sy, sz), (sx, -sy, sz), (sx, sy, sz), (-sx, sy, sz)]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5),
             (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh = bpy.data.meshes.new(f"{name}网格")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    return obj


def make_camera(name, location, rotation):
    data = bpy.data.cameras.new(f"{name}数据")
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    obj.rotation_euler = rotation
    return obj


def make_curve(name, points, dimensions="3D"):
    """画一条贝塞尔曲线(控制点 + 自动手柄)。"""
    data = bpy.data.curves.new(f"{name}数据", type="CURVE")
    data.dimensions = dimensions
    spline = data.splines.new("BEZIER")
    spline.bezier_points.add(len(points) - 1)
    for handle, co in zip(spline.bezier_points, points):
        handle.co = co
        handle.handle_left_type = "AUTO"
        handle.handle_right_type = "AUTO"
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


# 相机的 Blender 坐标 -> Godot 坐标是 (x, z, -y),这里两边都写清楚,
# 免得后面断言时又要在脑子里换算一遍。
SIDE_CAM_BLENDER = (0.0, -8.0, 3.0)      # 侧视机位(Godot: 0, 3, 8)
CLOSE_CAM_BLENDER = (4.0, 0.0, 2.0)      # 特写机位(Godot: 4, 2, 0)
# 运镜轨道:起点贴着侧视机位,中途**往上鼓一个大弯**,终点贴着特写机位。
# 鼓弯是故意的 —— 直线过渡和沿曲线飞的差别就看这个弯。
TRACK_POINTS = [SIDE_CAM_BLENDER, (0.0, -3.5, 7.5), CLOSE_CAM_BLENDER]
# 缓动曲线:慢起快停(画在 XY 平面上,导出成 Godot 的 Curve 资源)
EASE_POINTS = [(0.0, 0.0, 0.0), (0.5, 0.85, 0.0), (1.0, 1.0, 0.0)]


def build_scene():
    scene = clear_scene()
    scene.name = "World"       # 单场景项目 -> .tscn 叫 World.tscn,和以前一致
    scene.unit_settings.system = "METRIC"

    ground = make_box("地面", (20.0, 20.0, 0.4), (0.0, 0.0, -0.2))
    character = make_box("验证角色", (1.0, 1.0, 1.6), (0.0, 0.0, 0.8))
    side_cam = make_camera("侧视机位", SIDE_CAM_BLENDER,
                           (math.radians(72.0), 0.0, 0.0))
    make_camera("特写机位", CLOSE_CAM_BLENDER,
                (math.radians(75.0), 0.0, math.radians(35.0)))
    scene.camera = side_cam
    make_curve("运镜轨道", TRACK_POINTS, dimensions="3D")
    make_curve("缓动曲线", EASE_POINTS, dimensions="2D")
    return scene, ground, character


def write_physics(scene, character):
    """角色挂脚本(CharacterBody3D),地面静态 —— 和真实项目一样。"""
    for obj in scene.objects:
        physics = getattr(obj, "gk_physics", None)
        if physics is None:
            continue
        try:
            physics.shape_size = (0.0, 0.0, 0.0)
        except Exception:                                 # noqa: BLE001
            pass
        if obj is character:
            physics.enabled = True
            physics.body_type = "CharacterBody3D"
            physics.dimension = "3"
            physics.script_target = True
        elif getattr(obj, "type", "") == "MESH":
            physics.body_type = "StaticBody3D"


# ==========================================================================
# 2) 节点图:相机沿曲线运镜 + 一条时间轴(带曲线轨道)
# ==========================================================================
def build_graph(scene):
    tree = bpy.data.node_groups.new("曲线验证逻辑", "GKGameLogicTree")

    def add(kind, name, x, y, **props):
        node = tree.nodes.new(kind)
        node.name = name
        node.location = (x, y)
        for key, value in props.items():
            setattr(node, key, value)
        return node

    def link(from_node, from_pin, to_node, to_pin):
        tree.links.new(from_node.outputs[from_pin], to_node.inputs[to_pin])

    ready = add("GKNodeOnReady", "每当_准备", -1500.0, 400.0)

    # ① 相机:沿曲线运镜(过渡 1.2 秒,缓出)
    switch = add("GKNodeCameraSetCurrent", "开场运镜", -1100.0, 400.0,
                 gk_target="特写机位", gk_blend=1.2,
                 gk_blend_trans="EASE_OUT", gk_path_mode="CURVE",
                 gk_path_curve="运镜轨道")
    link(ready, "执行", switch, "执行")

    # ② 时间轴:1 秒,Alpha 走『缓动曲线』,一条轨道 0->5,曲线轨道=运镜轨道
    timeline = add("GKNodeTimeline", "开门时间轴", -1100.0, 100.0,
                   gk_time=1.0, gk_mode="ONCE", gk_curve="缓动曲线",
                   gk_track_count=1, gk_track1_from=0.0, gk_track1_to=5.0,
                   gk_path_curve="运镜轨道")
    link(ready, "执行", timeline, "执行")
    # 更新时什么都不用做(探针直接读变量);已结束时打印一行,便于确认触发
    done_print = add("GKNodePrint", "时间轴结束", -800.0, -100.0)
    done_print.gk_text = "时间轴结束"
    link(timeline, "已结束", done_print, "执行")

    scene.gk_settings.logic_tree = tree.name
    for attr, value in (("dimension", "3"),
                        ("base_class", "CharacterBody3D"),
                        ("class_name", "CurveProbePlayer"),
                        ("node_name", "CurveProbePlayer"),
                        ("write_scene", True),
                        ("god_camera", False)):
        if hasattr(scene.gk_settings, attr):
            try:
                setattr(scene.gk_settings, attr, value)
            except Exception:                             # noqa: BLE001
                pass
    return tree


# ==========================================================================
# 3) 导出 + 写探针工程文件
# ==========================================================================
PROBE_GD = '''extends Node

# 曲线探针:整条链真跑一遍,读脚本里的时间轴变量 + 相机实际位置。
# 断言:
#   * Alpha 按『缓动曲线』走(0.5 秒时已经到 0.85,不是线性 0.5)
#   * 轨道值 0->5(没给轨道曲线,所以走**线性**进度)
#   * 曲线位置 = 运镜轨道上的点;**中途离"直线"很远**(证明走的是曲线)
#   * 相机在过渡中沿轨道飞,过渡结束后精确停在特写机位
var world: Node = null
var host: Node = null
var elapsed := 0.0
var marks := [0.15, 0.45, 0.75, 1.35, 1.9]
var seen := {}
var start_pos := Vector3.ZERO
var end_pos := Vector3.ZERO

func _ready() -> void:
	world = load("res://scenes/World.tscn").instantiate()
	add_child(world)
	for node in world.find_children("*", "", true, false):
		if node.get("_gk_tl_alpha") != null:
			host = node
			break
	start_pos = Vector3(0.0, 3.0, 8.0)     # 侧视机位(Blender (0,-8,3))
	end_pos = Vector3(4.0, 2.0, 0.0)       # 特写机位(Blender (4,0,2))

func _physics_process(delta: float) -> void:
	elapsed += delta
	for mark in marks:
		if elapsed >= mark and not seen.has(mark):
			seen[mark] = true
			_report(mark)

func _dist_to_line(point: Vector3, a: Vector3, b: Vector3) -> float:
	var direction := b - a
	if direction.length() < 0.001:
		return point.distance_to(a)
	return ((point - a).cross(direction)).length() / direction.length()

func _report(mark: float) -> void:
	if host == null:
		print("CURVE_PROBE mark=", mark, " host=<null> 找不到挂脚本的节点")
		return
	var alpha = host.get("_gk_tl_alpha")
	var track = host.get("_gk_tl_tr1")
	var finished = host.get("_gk_tl_done")
	var path = host.get("_gk_tl_path")
	var to_var = host.get("_gk_cam_to")
	var to_name := "<null>"
	var to_pos := Vector3.ZERO
	var left_value = host.get("_gk_cam_blend_left")
	if to_var != null:
		to_name = String(to_var.name)
		if to_var is Node3D:
			to_pos = (to_var as Node3D).global_position
	var camera := get_viewport().get_camera_3d()
	var camera_name := "<null>"
	var camera_pos := Vector3.ZERO
	if camera != null:
		camera_name = String(camera.name)
		camera_pos = camera.global_position
	print("CURVE_PROBE t=", mark,
		" alpha=", "%.3f" % float(alpha),
		" track=", "%.3f" % float(track),
		" done=", finished,
		" path=", path,
		" cam=", camera_name,
		" cam_pos=", camera_pos,
		" to=", to_name,
		" to_pos=", to_pos,
		" blend_left=", left_value,
		" off_line=", "%.3f" % _dist_to_line(camera_pos, start_pos, end_pos))
	if mark >= 1.9:
		print("CURVE_PROBE_DONE")
		set_physics_process(false)
'''

PROBE_TSCN = '''[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://probe.gd" id="1_probe"]

[node name="CurveProbe" type="Node"]
script = ExtResource("1_probe")
'''


def write_probe_files(directory):
    """把探针场景/脚本写进工程,并把主场景指过去。"""
    with open(os.path.join(directory, "probe.gd"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE_GD)
    with open(os.path.join(directory, "probe.tscn"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE_TSCN)
    project_file = os.path.join(directory, "project.godot")
    if os.path.isfile(project_file):
        with open(project_file, "r", encoding="utf-8") as handle:
            text = handle.read()
        if "run/main_scene" in text:
            text = text.replace('run/main_scene="res://scenes/World.tscn"',
                                'run/main_scene="res://probe.tscn"')
        else:
            text = text.replace(
                "[application]",
                '[application]\n\nrun/main_scene="res://probe.tscn"')
        with open(project_file, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)


def main():
    from BlenderGameKit import asset_export, project

    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    scene, _ground, character = build_scene()
    write_physics(scene, character)
    build_graph(scene)
    project.create_project(OUT_DIR, title="curve_probe",
                           blend_file_name="曲线探针")
    result = asset_export.export_all_scenes(OUT_DIR, dimension="3")
    if result.get("error"):
        log(f"导出失败: {result['error']}")
        return 1
    for entry in result.get("scenes", []):
        log(f"场景 {entry['scene']} -> {os.path.basename(entry['tscn'])}"
            f" 脚本={entry['script_name']}")
    for warning in result.get("warnings", []):
        log(f"告警: {warning}")
    write_probe_files(OUT_DIR)
    curves_dir = os.path.join(OUT_DIR, "curves")
    log(f"曲线资源: {os.listdir(curves_dir) if os.path.isdir(curves_dir) else '无'}")
    log(f"工程目录: {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())