"""端到端验证:导出后 Godot 里真的能看到东西,而不是一片灰。

判据很直接:把导出场景交给 Godot 渲染,统计画面里的**不同颜色数量**。
一片灰只有 1 种颜色;有任何物体 + 光照,颜色数会明显更多。

用法:
    blender -b --python tests/blender_not_gray.py
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


GRAY_PROBE = '''extends SceneTree

func _initialize() -> void:
	var main: String = str(ProjectSettings.get_setting("application/run/main_scene", ""))
	print("GK_GRAY main_scene=", main)
	var packed: PackedScene = load(main) as PackedScene
	if packed == null:
		print("GK_GRAY 加载失败")
		quit(1)
		return
	var root: Node = packed.instantiate()
	get_root().add_child(root)

	# 计数:用**返回值**而不是传数组进去累加。
	#
	# 踩过:写成 `_count(root, [cams, lights, meshes])`,里面改 acc[i] ——
	# GDScript 的 Array 是值语义(写时才复制),所以外面拿到的永远是 0。
	# 探测脚本因此一直报"相机=0 灯光=0 网格=0",看起来像场景是空的。
	var counts := _count(root)
	print("GK_GRAY 相机=", counts[0], " 灯光=", counts[1], " 网格=", counts[2])

	await process_frame

	# 当前相机是谁、在哪 —— 直接验证"Blender 里改的相机位置同步过来了"
	var cam: Camera3D = get_root().get_camera_3d()
	if cam == null:
		print("GK_GRAY !! 没有当前相机")
	else:
		print("GK_GRAY 当前相机=", cam.name, " 位置=", cam.global_position,
				" fov=", cam.fov)

	await process_frame
	await process_frame

	var img: Image = get_root().get_texture().get_image()
	if img == null:
		print("GK_GRAY 拿不到画面")
		quit(1)
		return
	var colors: Dictionary = {}
	for y in range(0, img.get_height(), maxi(1, int(img.get_height() / 30.0))):
		for x in range(0, img.get_width(), maxi(1, int(img.get_width() / 30.0))):
			var c: Color = img.get_pixel(x, y)
			var key: String = "%.1f,%.1f,%.1f" % [c.r, c.g, c.b]
			colors[key] = int(colors.get(key, 0)) + 1
	print("GK_GRAY 不同颜色数=", colors.size())
	img.save_png(ProjectSettings.globalize_path("res://_shot.png"))
	print("GK_GRAY=DONE")
	quit(0)


func _count(node: Node) -> Array:
	var acc := [0, 0, 0]
	if node is Camera3D:
		acc[0] = 1
	elif node is Light3D:
		acc[1] = 1
	elif node is MeshInstance3D:
		acc[2] = 1
	for child in node.get_children():
		var sub := _count(child)
		acc[0] += sub[0]
		acc[1] += sub[1]
		acc[2] += sub[2]
	return acc
'''


def main():
    print("=" * 72)
    print("验证:导出后 Godot 里能看到东西(不是一片灰)")
    print("=" * 72)

    if not os.path.isfile(GODOT):
        print(f"跳过:找不到 Godot ({GODOT})")
        return 0

    module_name = find_addon()
    if not check("找到插件", module_name, "未安装"):
        return 1
    addon_utils.enable(module_name, default_set=True, persistent=True)
    asset_export = import_module(f"{module_name}.asset_export")
    project = import_module(f"{module_name}.project")

    workdir = os.path.join(tempfile.gettempdir(), "gk_not_gray")
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)

    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    # 一个球 + 一个地面
    ball_mesh = bpy.data.meshes.new("BallMesh")
    ball = bpy.data.objects.new("Ball", ball_mesh)
    scene.collection.objects.link(ball)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=20, v_segments=10, radius=0.6)
    bm.to_mesh(ball_mesh)
    bm.free()
    ball.location = (0.0, 0.0, 1.0)

    floor_mesh = bpy.data.meshes.new("FloorMesh")
    floor = bpy.data.objects.new("Floor", floor_mesh)
    scene.collection.objects.link(floor)
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=6.0)
    bm.to_mesh(floor_mesh)
    bm.free()

    # 相机 + 太阳
    cam_data = bpy.data.cameras.new("ShotCam")
    cam = bpy.data.objects.new("ShotCam", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (0.0, -5.0, 2.0)
    cam.rotation_euler = (1.25, 0.0, 0.0)
    scene.camera = cam

    sun_data = bpy.data.lights.new("KeySun", type="SUN")
    sun_data.energy = 3.0
    sun = bpy.data.objects.new("KeySun", sun_data)
    scene.collection.objects.link(sun)
    sun.rotation_euler = (0.7, 0.2, 0.5)

    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(workdir, "gray.blend"))
    directory, _reason = project.resolve(scene)
    project.create_project(directory)
    print(f"    项目: {directory}")

    # ---- 走"导出模型到 Godot"那条路(用户点的就是它) ----
    # 先整场景导出(和插件的实际流程一致),角色场景复用它的模型。
    # 不这么做的话角色场景只 instance 角色模型 ——
    # 实测画面里只有一个白球,地面不见了。
    world_path, world_ok, world_message = asset_export.export_scene_glb(
        directory, "World", scene=scene)
    check("整场景导出成功", world_ok, world_message)

    # 走**真实流程** —— 用户点"导出模型到 Godot"走的就是这条。
    # 之前这里是手动调 write_character_scene,和实际流程不一样,
    # 所以"主场景没切到 World"这种问题测试根本看不到。
    written_script = os.path.join(directory, "scripts", "game_logic.gd")
    os.makedirs(os.path.dirname(written_script), exist_ok=True)
    with open(written_script, "w", encoding="utf-8") as handle:
        handle.write("extends CharacterBody3D\n")

    outcome = asset_export.export_assets_pipeline(
        scene, directory, dimension="3", script_name="game_logic.gd")
    check("导出流程无错误", not outcome.get("error"), str(outcome.get("error")))
    print(f"    材质 {outcome['materials']} 个、碰撞体 {outcome['bodies']} 个、"
          f"相机 {outcome['cameras']} 个、灯 {outcome['lights']} 盏")

    scene_path = os.path.join(directory, "scenes", "World.tscn")
    check("生成了 World.tscn", os.path.isfile(scene_path), scene_path)
    with open(scene_path, encoding="utf-8") as handle:
        text = handle.read()

    # ── 相机和灯光现在来自 .glb,不是 .tscn 里的节点 ────────────────
    #
    # glTF 本身就能带相机和灯(写进 cameras 数组和 KHR_lights_punctual),
    # Godot 导入后自动生成 Camera3D / DirectionalLight3D。
    # 所以 .tscn 里**不该**再有 Camera3D 节点 —— 有的话场景里就有两个相机。
    # (实测踩过:用户说"打开 Godot 多一个摄像头"。)
    glb_info = asset_export.glb_object_names(directory, "World")
    print(f"    glb 里的相机: {glb_info['cameras']}, 灯: {glb_info['lights']}")
    print(f"    glb 里的对象: {glb_info['nodes']}")
    check("glb 里带了相机", len(glb_info["cameras"]) >= 1, str(glb_info))
    check("glb 里带了灯", len(glb_info["lights"]) >= 1, str(glb_info))

    duplicate_cameras = re.findall(r'\[node name="[^"]+" type="Camera3D"', text)
    check("场景里没有重复的 Camera3D 节点", not duplicate_cameras,
          str(duplicate_cameras))
    check("有 Environment 节点负责设主相机",
          '[node name="Environment"' in text)
    env_script = os.path.join(directory, "scripts", "gk_environment.gd")
    check("Environment 脚本写出来了", os.path.isfile(env_script), env_script)
    check("场景 instance 的是整场景模型", "World.glb" in text,
          [ln for ln in text.splitlines() if "glb" in ln][:1])

    # 主场景必须指向 World —— 否则打开的是旧场景,看不到新相机
    main = project.main_scene(directory)
    check("主场景指向 World.tscn", main == "res://scenes/World.tscn", str(main))

    # 整场景模型里必须有地面 —— 少了他就是"角色悬在空中"
    glb_json = None
    try:
        import json as _json
        import struct as _struct

        with open(os.path.join(directory, "art", "World.glb"), "rb") as handle:
            data = handle.read()
        offset = 12
        while offset < len(data):
            length, kind = _struct.unpack_from("<II", data, offset)
            offset += 8
            if kind == 0x4E4F534A:
                glb_json = _json.loads(data[offset:offset + length].decode("utf-8"))
                break
            offset += length
    except Exception as exc:
        print(f"    读 glb 失败: {exc}")

    if glb_json:
        names = [n.get("name", "") for n in glb_json.get("nodes", [])]
        print(f"    整场景模型里的节点: {names}")
        check("整场景模型含地面", "Floor" in names, str(names))
        check("整场景模型含角色", "Ball" in names, str(names))

    # ---- 交给 Godot 真渲染 ----
    probe = os.path.join(directory, "_grayprobe.gd")
    with open(probe, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(GRAY_PROBE)

    subprocess.run([GODOT, "--headless", "--path", directory, "--import"],
                   capture_output=True, text=True, timeout=180)
    result = subprocess.run([GODOT, "--path", directory, "--script",
                             "res://_grayprobe.gd"],
                            capture_output=True, text=True, timeout=180)
    output = (result.stdout or "") + (result.stderr or "")
    print("-" * 72)
    for line in output.splitlines():
        if "GK_GRAY" in line:
            print("   ", line.strip())
    print("-" * 72)

    color_match = re.search(r"GK_GRAY 不同颜色数=\s*(\d+)", output)
    cam_match = re.search(r"GK_GRAY 相机=\s*(\d+)", output)
    mesh_match = re.search(r"GK_GRAY 网格=\s*(\d+)", output)

    check("Godot 渲染出了画面", color_match is not None,
          output[-400:] if not color_match else "")
    if color_match:
        colors = int(color_match.group(1))
        print(f"    画面不同颜色数: {colors}（1 种 = 一片纯色）")
        # 判据说明:真正要验证的是"**不是一片纯色**",也就是场景里确实有
        # 东西被画出来。颜色数量取决于材质 —— 走 NPR 卡通材质时会多很多,
        # 但那是另一条链路的事,这里只断言"有东西"。
        check("画面不是一片纯色(场景里有东西被画出来)", colors > 1, str(colors))
    if cam_match:
        check("场景里确实有相机", int(cam_match.group(1)) >= 1,
              cam_match.group(1))
    if mesh_match:
        check("场景里有网格", int(mesh_match.group(1)) >= 1, mesh_match.group(1))

    # 当前相机必须存在,而且是 Blender 里那个(位置对得上) ——
    # 这是"在 Blender 里改相机位置,Godot 同步"的直接判据。
    # 注意 Godot 的 print 会在逗号分隔的参数之间**自动加空格**,
    # 所以这里的模式要按实际输出写(踩过一次:模式少了个空格,永远匹配不上)。
    cam_name = re.search(r"GK_GRAY 当前相机=\s*(\S+)\s+位置=", output)
    check("有当前相机(current)", cam_name is not None,
          "输出里没有『当前相机= ... 位置=』这一行;"
          "原始输出尾部: " + repr(output[-500:]))
    if cam_name:
        print(f"    当前相机: {cam_name.group(1)}")
    cam_pos = re.search(
        r"GK_GRAY 当前相机=\s*(\S+)\s+位置=\(([-\d.]+), ([-\d.]+), ([-\d.]+)\)",
        output)
    if cam_pos:
        got = tuple(round(float(cam_pos.group(i)), 2) for i in (2, 3, 4))
        # Blender 里是 (0, -5, 2) -> Godot (0, 2, 5)
        expect = (0.0, 2.0, 5.0)
        print(f"    相机位置 {got}（Blender 里是 (0, -5, 2)，期望 {expect}）")
        check("相机位置和 Blender 一致",
              all(abs(a - b) < 0.05 for a, b in zip(got, expect)),
              f"{got} != {expect}")

    try:
        os.remove(probe)
    except OSError:
        pass

    print(f"    工作目录: {workdir}")
    addon_utils.disable(module_name, default_set=True)
    return 0


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
print("NOT_GRAY=" + ("OK" if code == 0 and not failed else "FAILED"))
print("=" * 72)

sys.exit(0 if (code == 0 and not failed) else 1)
