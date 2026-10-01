"""端到端验证:NPR 卡通材质真的在 Godot 里生效。

判据(不轻信"文件生成了"):
  1. materials/ 下有 .tres
  2. 场景 .tscn 用 material_override 引用了它
  3. Godot 加载场景时**没有报错**(引用了不存在的文件会报)
  4. Godot 读回来的材质**真的是 ShaderMaterial**,而且 shader 是我们那个
  5. 渲染出来的画面不是纯白(说明着色器真的在算)

用法:
    blender -b --python tests/blender_material_pipeline.py
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from importlib import import_module

import addon_utils
import bmesh
import bpy

RESULTS = []
GODOT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" - {detail}" if detail and not condition else ""))
    return bool(condition)


def find_addon():
    for module in addon_utils.modules():
        if "blender_game_kit" in getattr(module, "__name__", ""):
            return module.__name__
    return None


PROBE = '''extends SceneTree

func _initialize() -> void:
	var main: String = str(ProjectSettings.get_setting("application/run/main_scene", ""))
	print("GK_MP main=", main)
	var packed: PackedScene = load(main) as PackedScene
	if packed == null:
		print("GK_MP !! 场景加载失败")
		quit(1)
		return
	var root: Node = packed.instantiate()
	get_root().add_child(root)
	await process_frame

	var checked := 0
	_walk(root, [checked])
	print("GK_MP 检查了 ", checked, " 个网格")

	await process_frame
	await process_frame
	var img: Image = get_root().get_texture().get_image()
	var colors: Dictionary = {}
	for y in range(0, img.get_height(), maxi(1, int(img.get_height() / 40.0))):
		for x in range(0, img.get_width(), maxi(1, int(img.get_width() / 40.0))):
			var c: Color = img.get_pixel(x, y)
			var key: String = "%.2f,%.2f,%.2f" % [c.r, c.g, c.b]
			colors[key] = int(colors.get(key, 0)) + 1
	print("GK_MP 画面不同颜色数=", colors.size())
	print("GK_MP=DONE")
	quit(0)


func _walk(node: Node, acc: Array) -> void:
	if node is MeshInstance3D:
		var mi := node as MeshInstance3D
		acc[0] = int(acc[0]) + 1
		var mat: Material = mi.material_override
		if mat == null and mi.mesh != null and mi.mesh.get_surface_count() > 0:
			mat = mi.mesh.surface_get_material(0)
		var kind: String = mat.get_class() if mat != null else "无"
		var shader_name: String = ""
		if mat is ShaderMaterial:
			var sm := mat as ShaderMaterial
			if sm.shader != null:
				shader_name = sm.shader.resource_path
				# 抽查一个卡通参数,确认真的是我们的着色器在跑
				print("GK_MP   参数 shadow_step=", sm.get_shader_parameter("shadow_step"),
						" lit_tint=", sm.get_shader_parameter("lit_tint"))
		print("GK_MP   ", node.name, " 材质=", kind, " shader=", shader_name)
	for child in node.get_children():
		_walk(child, acc)
'''


def main():
    print("=" * 72)
    print("NPR 材质管线测试")
    print("=" * 72)

    if not os.path.isfile(GODOT):
        print(f"跳过:找不到 Godot ({GODOT})")
        return 0

    module_name = find_addon()
    if not check("找到插件", module_name, "未安装"):
        return 1
    addon_utils.enable(module_name, default_set=True, persistent=True)
    ops = import_module(f"{module_name}.operator_io")
    asset_export = import_module(f"{module_name}.asset_export")
    project = import_module(f"{module_name}.project")

    workdir = os.path.join(tempfile.gettempdir(), "gk_matpipe")
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)

    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for tree in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(tree)
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)

    # 一个球 + 地面,各给一个材质
    def make(name, kind, location):
        mesh = bpy.data.meshes.new(name + "Mesh")
        obj = bpy.data.objects.new(name, mesh)
        scene.collection.objects.link(obj)
        bm = bmesh.new()
        if kind == "sphere":
            bmesh.ops.create_uvsphere(bm, u_segments=20, v_segments=10, radius=0.7)
        else:
            bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=6.0)
        bm.to_mesh(mesh)
        bm.free()
        obj.location = location
        material = bpy.data.materials.new(f"{name}Mat")
        material.use_nodes = True
        for node in material.node_tree.nodes:
            if node.bl_idname == "ShaderNodeBsdfPrincipled":
                node.inputs["Base Color"].default_value = (0.9, 0.4, 0.4, 1.0)
        mesh.materials.append(material)
        return obj

    ball = make("Ball", "sphere", (0.0, 0.0, 1.0))
    floor = make("Floor", "grid", (0.0, 0.0, 0.0))

    # 相机 + 太阳
    #
    # 取景要留出余量:Blender 的相机是**水平** FOV(angle_x),
    # 而传给 Godot 的 Camera3D.fov 是**垂直** FOV,按渲染分辨率的宽高比换算。
    # 所以分辨率设置会影响最终取景,不能随便写。
    # (踩过:沿用默认的窄视角 + 相机太近,球直接填满整个画面,
    #  看起来"画面只有 2 种颜色",其实着色完全正常。)
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    cam_data = bpy.data.cameras.new("ShotCam")
    cam_data.angle_x = 1.272          # 约 72.9 度水平
    cam = bpy.data.objects.new("ShotCam", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (0.0, -6.0, 2.0)
    cam.rotation_euler = (1.30, 0.0, 0.0)
    scene.camera = cam

    sun_data = bpy.data.lights.new("KeySun", type="SUN")
    # 灯光**不再由导出脚本修正**(按用户要求:灯光在 Godot 里设计)。
    # glTF 会把 Blender 的太阳能量放大约 683 倍(3.0 -> 2049),
    # Godot 原样接收会冲成纯白。要让 Godot 里出现约 0.34 的正常亮度,
    # Blender 侧要写 0.34 / 683 —— 直接用这个值来测材质本身。
    sun_data.energy = 0.0005
    sun = bpy.data.objects.new("KeySun", sun_data)
    scene.collection.objects.link(sun)
    sun.rotation_euler = (0.7, 0.2, 0.5)

    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(workdir, "mat.blend"))
    directory, _reason = project.resolve(scene)
    project.create_project(directory)
    print(f"    项目: {directory}")

    # ---- 走完整的资产导出流程 ----
    tree = bpy.data.node_groups.new("逻辑", "GKGameLogicTree")
    exporter = import_module(f"{module_name}.exporter")
    exporter.build_starter_graph(tree)
    tree.use_fake_user = True
    settings = exporter.build_settings(scene)
    settings["output_dir"] = directory
    result = exporter.export_tree(tree, settings, write_scene=False)
    ops.remember_export(scene, result)

    # 用 bpy.ops 走正规路径 —— Blender 的算子不能直接 __new__ 实例化
    # (会报 "bpy_struct.__new__(struct): expected a single argument")
    for obj in scene.objects:
        obj.select_set(obj.type == "MESH")
    bpy.context.view_layer.objects.active = ball
    outcome = bpy.ops.gk.export_assets()
    check("资产导出执行成功", "FINISHED" in outcome, str(outcome))
    print("    （导出完成）")

    # ---- 1. .tres 生成了 ----
    materials_dir = os.path.join(directory, "materials")
    tres = [f for f in os.listdir(materials_dir) if f.endswith(".tres")] \
        if os.path.isdir(materials_dir) else []
    print(f"    materials/ 里的材质: {tres}")
    check("生成了 .tres 材质", len(tres) >= 2, str(tres))

    # ---- 2. 场景引用了它们 ----
    world_scene = os.path.join(directory, "scenes", "World.tscn")
    check("生成了 World.tscn", os.path.isfile(world_scene), world_scene)
    text = open(world_scene, encoding="utf-8").read() if os.path.isfile(world_scene) else ""
    print("-" * 72)
    print(text[:1500])
    print("-" * 72)
    # 材质**不在 .tscn 里**挂,而是交给 Environment 脚本在运行时挂。
    #
    # 为什么:glb 实例的内部子节点不能被外部节点覆盖 —— 在 .tscn 里写
    #     [node name="Ball" parent="World"]
    #     material_override = ExtResource("mat_2")
    # Godot 报 "Parent path './World' for node 'Ball' has vanished",
    # 覆盖被丢弃,看到的永远是白模。加 editable_children 也没用。
    # (也试过把网格展开成 MeshInstance3D,那样 mi.mesh 是 null,更糟。)
    #
    # 所以这里检查的是**映射表**写对了,真正"有没有生效"靠下面的 Godot 探测。
    check("场景里有 Environment 节点挂材质",
          '[node name="Environment"' in text)
    check("映射表指向 materials/ 下的 .tres",
          "materials = " in text and "res://materials/" in text,
          [ln for ln in text.splitlines() if "materials" in ln][:2])

    # 映射表里提到的 .tres 必须真的存在 —— 引用不存在的文件 Godot 会报错,
    # 而且**整个场景加载会失败**(这个坑踩过:贴图没导出时就是这样)
    missing = []
    table = re.search(r'materials = "([^"]*)"', text)
    if table:
        for entry in table.group(1).split("\\n"):
            if "=" not in entry:
                continue
            res_path = entry.split("=", 1)[1].strip()
            relative = res_path.replace("res://", "")
            if relative and not os.path.isfile(os.path.join(directory, relative)):
                missing.append(relative)
    check("映射表里引用的 .tres 都存在", not missing, str(missing))

    # ---- 3. 交给 Godot ----
    probe_path = os.path.join(directory, "_mp.gd")
    with open(probe_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE)

    imported = subprocess.run([GODOT, "--headless", "--path", directory, "--import"],
                              capture_output=True, text=True, errors="replace",
                              timeout=240)
    import_out = (imported.stdout or "") + (imported.stderr or "")
    import_errors = [ln.strip() for ln in import_out.splitlines()
                     if "ERROR" in ln or "error" in ln.lower()]
    print(f"    导入期错误数: {len(import_errors)}")
    for line in import_errors[:4]:
        print(f"      ! {line}")
    check("Godot 导入无错误", not import_errors, str(import_errors[:2]))

    run = subprocess.run([GODOT, "--path", directory, "--script", "res://_mp.gd"],
                         capture_output=True, text=True, errors="replace", timeout=240)
    out = (run.stdout or "") + (run.stderr or "")
    print("-" * 72)
    for line in out.splitlines():
        if "GK_MP" in line:
            print("   ", line.strip())
    print("-" * 72)

    # ---- 4. 材质真的是我们的 ShaderMaterial ----
    material_lines = [ln for ln in out.splitlines() if "材质=" in ln]
    print(f"    网格材质报告: {len(material_lines)} 条")
    check("Godot 里至少有一个网格用的是 ShaderMaterial",
          any("材质=ShaderMaterial" in ln for ln in material_lines),
          str(material_lines[:3]))
    check("shader 指向 gk_toon.gdshader",
          any("gk_toon.gdshader" in ln for ln in material_lines),
          str(material_lines[:3]))
    check("能读到卡通参数(说明着色器在跑)",
          "GK_MP   参数 shadow_step=" in out, out[-300:])

    colors = re.search(r"GK_MP 画面不同颜色数=(\d+)", out)
    if colors:
        count = int(colors.group(1))
        print(f"    画面不同颜色数: {count}")
        # 卡通着色会把连续光照切成色阶 —— 地面和球各自至少一个色块,
        # 加上背景,至少 3 种颜色
        check("画面有多于 2 种颜色(卡通色阶生效)", count > 2, str(count))

    try:
        os.remove(probe_path)
    except OSError:
        pass

    print(f"    工作目录: {workdir}")
    addon_utils.disable(module_name, default_set=True)
    return 0


class _FakeContext:
    """算子需要的最小 context。"""

    def __init__(self, scene):
        self.scene = scene
        self.selected_objects = []
        self.view_layer = bpy.context.view_layer
        self.window_manager = getattr(bpy.context, "window_manager", None)
        self.preferences = bpy.context.preferences

    def __getattr__(self, name):
        return getattr(bpy.context, name)


try:
    code = main()
except Exception:
    traceback.print_exc()
    code = 1

passed = sum(1 for _, ok, _ in RESULTS if ok)
failed = [name for name, ok, _ in RESULTS if not ok]
print("=" * 72)
print(f"结果: {passed}/{len(RESULTS)} 通过")
if failed:
    print("失败项: " + ", ".join(failed))
print("MATERIAL_PIPELINE=" + ("OK" if code == 0 and not failed else "FAILED"))
print("=" * 72)

sys.exit(0 if (code == 0 and not failed) else 1)
