"""相机远裁真机验证(第一百九十轮)。

验证环境脚本对 `Camera3D.far` 的两条行为:

  1. 表里的相机真的被设成那个距离;
  2. 填得比 `near` 还小时按 `near + 0.01` 兜底 —— 不会把画面裁成空的。

不需要显卡:`--headless` 的 dummy 渲染器照样有 Camera3D 节点和属性。
跑法:
    python scripts\\check_camera_far.py
"""

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORK = os.path.join(ROOT, "_dist", "camera_far_probe")
GODOT_DEFAULT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"

PROJECT_GODOT = """config_version=5

[application]

config/name="GK Camera Far Probe"

[rendering]

renderer/rendering_method="gl_compatibility"
"""

WORLD_TSCN = """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://gk_environment.gd" id="env"]

[node name="World" type="Node3D"]

[node name="主相机" type="Camera3D" parent="."]
far = 4000.0

[node name="近裁相机" type="Camera3D" parent="."]
far = 4000.0

[node name="路人相机" type="Camera3D" parent="."]
far = 4000.0

[node name="Environment" type="Node" parent="."]
script = ExtResource("env")
"""

PROBE = '''extends SceneTree

func _initialize() -> void:
	call_deferred("run")


func run() -> void:
	var world := (load("res://World.tscn") as PackedScene).instantiate()
	root.add_child(world)
	# 环境脚本用 call_deferred 应用远裁 -> 等两帧让它跑完
	for i in range(3):
		await process_frame
	var main := world.get_node("主相机") as Camera3D
	var close := world.get_node("近裁相机") as Camera3D
	var other := world.get_node("路人相机") as Camera3D
	var ok := is_equal_approx(main.far, 150.0) \\
		and is_equal_approx(close.far, close.near + 0.01) \\
		and is_equal_approx(other.far, 4000.0)
	print("CAMERA_FAR main=", main.far, " near=", main.near,
		" close=", close.far, " other=", other.far)
	print("CAMERA_FAR_CHECK=", "OK" if ok else "FAIL")
	quit(0 if ok else 1)
'''


def find_godot(explicit=""):
    candidate = explicit or os.environ.get("GK_GODOT") or GODOT_DEFAULT
    return candidate if os.path.isfile(candidate) else ""


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def build():
    """写一个最小工程:三台相机 + 带远裁表的环境脚本。"""
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import gk_stub

    gk_stub.install()
    gk_stub.load_addon(os.path.join(ROOT, "BlenderGameKit"))
    from BlenderGameKit import asset_export

    if os.path.isdir(WORK):
        shutil.rmtree(WORK)
    os.makedirs(WORK, exist_ok=True)
    write(os.path.join(WORK, "project.godot"), PROJECT_GODOT)
    write(os.path.join(WORK, "World.tscn"), WORLD_TSCN)
    write(os.path.join(WORK, "probe.gd"), PROBE)
    write(os.path.join(WORK, "gk_environment.gd"),
          asset_export.render_env_script(
              camera_fars='"主相机=150.0|近裁相机=0.01"'))


def run(godot):
    env = dict(os.environ, GODOT_SILENCE_ROOT_WARNING="1")
    common = dict(capture_output=True, text=True, encoding="utf-8",
                  errors="replace", env=env, timeout=120)
    try:
        subprocess.run([godot, "--headless", "--path", WORK, "--import"],
                       **common)
        result = subprocess.run(
            [godot, "--headless", "--path", WORK, "--script",
             "res://probe.gd"], **common)
    except subprocess.TimeoutExpired:
        print("CAMERA_FAR=FAILED(超时)")
        return 1
    output = (result.stdout or "") + (result.stderr or "")
    print(output)
    ok = ("CAMERA_FAR_CHECK=OK" in output
          and "SCRIPT ERROR" not in output
          and "Parse Error" not in output)
    print("CAMERA_FAR=" + ("OK" if ok else "FAILED"))
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description="相机远裁真机验证")
    parser.add_argument("--godot", default="")
    args = parser.parse_args()
    godot = find_godot(args.godot)
    if not godot:
        print(f"跳过:找不到 Godot({args.godot or GODOT_DEFAULT})")
        return 0
    build()
    return run(godot)


if __name__ == "__main__":
    sys.exit(main())
