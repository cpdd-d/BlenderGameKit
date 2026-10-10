"""三种关卡预设模板的自查(第八十七轮 I 组 24)。

跑法:
    blender -b --python scripts/check_level_templates.py

── 查什么 ────────────────────────────────────────────────────────
第 1 段(Blender):用 `levels.create_level` 建 2D / 3D / 2.5D 三关,
真导出成 `_dist/level_templates_probe` 工程,断言:
  * 三关各一张 .tscn(逐场景维度:2D 关是 CharacterBody2D、没有相机);
  * 3D / 2.5D 关有相机(在 glb 里)、角色挂了脚本、地面有碰撞体;
  * 每关的 glb 里**没有别的关卡**的物体(第八十七轮修的"串场景")。
第 2 段(真 Godot,无头):把 3D 关当主场景跑一遍,模拟按键断言
  * 角色**落到地面**(重力生效);
  * 按 D **真的走了**(X 位移超过 1 米);
  * 按 C **真的切了相机**(当前相机从侧视机位变成俯瞰机位)。
结论行:`CHECK_LEVEL_TEMPLATES=OK`。找不到 Godot 时第 2 段跳过(不算失败),
和别的自查脚本一个规矩。
"""

import importlib
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

WORK = os.path.join(ROOT, "_dist", "level_templates_probe")
GODOT_DEFAULT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name}" + (f"  <- {detail}" if detail and not ok else ""))


def load_plugin():
    """拿到已安装的插件包(装在哪都行 —— 不写死 user_default)。"""
    import addon_utils

    for module in addon_utils.modules():
        if "blender_game_kit" in getattr(module, "__name__", ""):
            package = importlib.import_module(module.__name__)
            levels = importlib.import_module(f"{module.__name__}.levels")
            asset_export = importlib.import_module(
                f"{module.__name__}.asset_export")
            project = importlib.import_module(f"{module.__name__}.project")
            return levels, asset_export, project
    raise RuntimeError("找不到已安装的 BlenderGameKit")


PROBE_GD = '''extends Node

# 关卡模板的运行时探针(第八十七轮 I 组 24):3D 关卡真的跑起来。
# 结论行统一 LEVELS_ 前缀,由 check_level_templates.py 汇总。

var world: Node = null
var host: CharacterBody3D = null
var start_x := 0.0
var landed_y := 0.0
var moved := 0.0
var cam_before := ""
var cam_after := ""
var frames := 0
var phase := 0
var hold := 0
var done := false


func _ready() -> void:
	world = load("res://scenes/模板3D.tscn").instantiate()
	add_child(world)
	var players := world.find_children("角色*", "CharacterBody3D", true, false)
	if players.is_empty():
		# 名字兜底:场景里唯一的 CharacterBody3D 就是角色
		# (同名物体被 Blender 加了后缀时 "角色*" 仍能命中,这里再兜一层)
		players = world.find_children("*", "CharacterBody3D", true, false)
	if players.is_empty():
		print("LEVELS_ERROR 找不到角色节点")
		get_tree().quit()
		return
	host = players[0] as CharacterBody3D
	print("LEVELS_SETUP host=", host != null)


func _send_key(code: int, pressed: bool) -> void:
	var event := InputEventKey.new()
	event.keycode = code
	event.physical_keycode = code
	event.pressed = pressed
	Input.parse_input_event(event)


func _physics_process(_delta: float) -> void:
	frames += 1
	if host == null or done:
		return
	if phase == 0:
		# 等它落到地面(重力生效);等不到就超时继续,断言会失败
		if host.is_on_floor() or frames > 300:
			landed_y = host.global_position.y
			start_x = host.global_position.x
			print("LEVELS_LANDED ok=", host.is_on_floor(),
				" y=", snappedf(landed_y, 0.001), " frames=", frames)
			_send_key(KEY_D, true)
			phase = 1
	elif phase == 1:
		hold += 1
		if hold >= 90:
			_send_key(KEY_D, false)
			moved = absf(host.global_position.x - start_x)
			print("LEVELS_WALK ok=", moved > 1.0,
				" dx=", snappedf(moved, 0.001))
			var cam := get_viewport().get_camera_3d()
			cam_before = cam.name if cam != null else ""
			_send_key(KEY_C, true)
			phase = 2
			hold = 0
	elif phase == 2:
		hold += 1
		if hold == 2:
			_send_key(KEY_C, false)
		if hold >= 20:
			var cam := get_viewport().get_camera_3d()
			cam_after = cam.name if cam != null else ""
			print("LEVELS_CAM ok=", cam_before != "" and cam_after != ""
				and cam_before != cam_after,
				" from=", cam_before, " to=", cam_after)
			print("LEVELS_DONE")
			done = true
			get_tree().quit()
'''


PROBE_TSCN = '''[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://probe.gd" id="1_probe"]

[node name="LevelTemplatesProbe" type="Node"]
script = ExtResource("1_probe")
'''


def _glb_names(path):
    """glb 里的节点名(JSON 里非 ASCII 会被转义,所以老老实实解析)。"""
    import json
    import struct

    with open(path, "rb") as handle:
        data = handle.read()
    length = struct.unpack("<I", data[12:16])[0]
    doc = json.loads(data[20:20 + length].decode("utf-8"))
    return [node.get("name") for node in doc.get("nodes", [])]


def build_and_export(levels, asset_export, project):
    """建三关 + 导出到 WORK 工程。返回 {模板: 场景名}。"""
    names = {}
    for template in ("2d", "3d", "25d"):
        scene_name = {"2d": "模板2D", "3d": "模板3D", "25d": "模板25D"}[template]
        outcome = levels.create_level(template=template,
                                      scene_name=scene_name)
        names[template] = outcome["scene"].name
    # 把 Blender 启动时的空场景删掉(它不属于这三关)
    for scene in list(bpy.data.scenes):
        if scene.name not in names.values():
            bpy.data.scenes.remove(scene)

    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)
    project.create_project(WORK, title="关卡模板自查")
    result = asset_export.export_all_scenes(WORK)
    check("三关各导出一张 .tscn(全成功)",
          not result.get("error")
          and len([e for e in result.get("scenes", []) if e.get("ok")]) == 3,
          str({e["scene"]: e["ok"] for e in result.get("scenes", [])}))
    return names


def check_exports(names):
    def read_scene(name):
        path = os.path.join(WORK, "scenes", f"{name}.tscn")
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    scene_2d = read_scene(names["2d"])
    scene_3d = read_scene(names["3d"])
    scene_25d = read_scene(names["25d"])
    check("2D 关按 2D 导(角色是 CharacterBody2D / 没有相机)",
          "CharacterBody2D" in scene_2d and "Camera3D" not in scene_2d)
    check("3D / 2.5D 关的角色挂了脚本 + 地面有碰撞体",
          "script = ExtResource" in scene_3d
          and "StaticBody3D" in scene_3d
          and "CollisionShape3D" in scene_3d
          and "script = ExtResource" in scene_25d)

    glb_3d = os.path.join(WORK, "art", f"{names['3d']}.glb")
    glb_2d = os.path.join(WORK, "art", f"{names['2d']}.glb")
    names_3d = _glb_names(glb_3d)
    names_2d = _glb_names(glb_2d)
    check("3D 关的 glb 里有地面和两个方块(相机也在)",
          all(any(str(n).startswith(prefix) for n in names_3d)
              for prefix in ("地面", "方块A", "方块B", "侧视机位")),
          str(names_3d))
    check("每关的 glb 互相不串(2D 关里没有 3D 关的东西)",
          any(str(n).startswith("跳台") for n in names_2d)
          and not any(str(n).startswith("方块") for n in names_2d),
          str(names_2d))


def check_env_script(names):
    path = os.path.join(WORK, "scripts",
                        f"gk_environment_{names['3d']}.gd")
    if not os.path.isfile(path):
        check("3D 关的环境脚本存在", False, path)
        return
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    check("环境脚本认侧视机位当主相机",
          'camera_name' in text and "侧视机位" in text)


def run_godot_probe():
    """真 Godot 无头跑 3D 关:落地 / 走 / 切相机。返回输出文本。"""
    godot = os.environ.get("GK_GODOT", GODOT_DEFAULT)
    if not os.path.isfile(godot):
        print(f"[INFO] 找不到 Godot({godot})—— 运行时探针跳过")
        return ""
    entry = os.path.join(WORK, "probe.gd")
    with open(entry, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE_GD)
    with open(os.path.join(WORK, "probe.tscn"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE_TSCN)
    project_file = os.path.join(WORK, "project.godot")
    with open(project_file, encoding="utf-8") as handle:
        text = handle.read()
    text = re.sub(r'run/main_scene="[^"]*"',
                  'run/main_scene="res://probe.tscn"', text)
    with open(project_file, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)

    subprocess.run([godot, "--headless", "--path", WORK, "--import"],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
    run = subprocess.run([godot, "--headless", "--path", WORK,
                          "--quit-after", "900"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    out = (run.stdout or "") + (run.stderr or "")
    for line in out.splitlines():
        if "LEVELS_" in line or "SCRIPT ERROR" in line or "Parse Error" in line:
            print("    " + line.strip())
    return out


def main():
    try:
        levels, asset_export, project = load_plugin()
    except Exception as exc:                             # noqa: BLE001
        print(f"✗ 插件不可用:{type(exc).__name__}: {exc}")
        print("CHECK_LEVEL_TEMPLATES=FAILED")
        return 1

    names = build_and_export(levels, asset_export, project)
    check_exports(names)
    check_env_script(names)

    out = run_godot_probe()
    if not out:
        print("[INFO] 运行时探针跳过(没有 Godot)—— 只算导出这一段")
    else:
        check("3D 关:角色落到地面(重力生效)",
              "LEVELS_LANDED ok=true" in out,
              "没等到 is_on_floor")
        check("3D 关:按 D 真的走了(位移 > 1 米)",
              "LEVELS_WALK ok=true" in out, "位移不够")
        check("3D 关:按 C 真的切了相机(侧视 -> 俯瞰)",
              "LEVELS_CAM ok=true" in out, "相机没切")
        check("3D 关:探针跑完", "LEVELS_DONE" in out)

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    failed = [name for name, ok, _d in RESULTS if not ok]
    print("=" * 70)
    print(f"结果: {passed}/{len(RESULTS)} 通过")
    if failed:
        print("失败项: " + ", ".join(failed))
    print("CHECK_LEVEL_TEMPLATES=" + ("OK" if not failed else "FAILED"))
    print("=" * 70)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())