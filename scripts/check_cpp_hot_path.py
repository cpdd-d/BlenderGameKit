"""C++ 热路径真机验证(第一百九十一轮)。

流程和 `examples/gdextension_cpp` 一致,只是源码由节点图生成:

  1. 用离线桩搭一条标准移动链(输入 -> 移动 -> 反转 -> 跳跃分支);
  2. `cpp_hot_path.generate()` 生成 `native/` 下的 C++ / SConstruct / .gdextension;
  3. 用 SCons + 本仓库的 godot-cpp **真编译**;
  4. 起 Godot(无头)加载扩展:检查类存在、属性可读写,**并真跑一帧**
     看它有没有把 velocity 写出来。

2D / 3D 各跑一遍(两套 C++ 代码不一样,只编一套会漏)。

跑法:
    python scripts\\check_cpp_hot_path.py
    python scripts\\check_cpp_hot_path.py --godot "D:\\godot\\...console.exe"

没有 SCons / godot-cpp / Godot 时**跳过**,不判红(和渲染那层同一个约定)。
"""

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORK_BASE = os.path.join(ROOT, "_dist", "cpp_hot_path_probe")
GODOT_CPP = os.path.join(ROOT, "examples", "godot-cpp")
GODOT_DEFAULT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"

PROJECT_GODOT = """config_version=5

[application]

config/name="GK C++ Hot Path Probe"

[rendering]

renderer/rendering_method="gl_compatibility"
"""

PROBE_TEMPLATE = '''extends SceneTree

func _initialize() -> void:
	call_deferred("run")


func run() -> void:
	var cls := "__CLASS__"
	var exists := ClassDB.class_exists(cls)
	var ok := exists
	var speed := -1.0
	var action := ""
	var moved := false
	if exists:
		var node = ClassDB.instantiate(cls)
		if node == null:
			ok = false
		else:
			speed = float(node.get("speed"))
			node.set("speed", 123.0)
			speed = float(node.get("speed"))
			action = str(node.get("action_jump"))
			ok = ok and is_equal_approx(speed, 123.0) \\
				and action == "jump"
			node.free()

		# 真跑一帧:把 C++ 节点挂到角色下面,按一个方向键,
		# 看 velocity 是否真的被它写出来(只验"类存在"不够)。
		# 生成器默认动作名是 move_left/right/up/down + jump,这里先登记,
		# 免得 Input::get_action_strength 刷 "action doesn't exist"。
		for action_name in ["move_left", "move_right", "move_up",
				"move_down", "jump", "probe_right"]:
			if not InputMap.has_action(action_name):
				InputMap.add_action(action_name)
		var world := __WORLD__.new()
		root.add_child(world)
		var body := __BODY__.new()
		world.add_child(body)
		var hot = ClassDB.instantiate(cls)
		body.add_child(hot)
		hot.set("action_right", "probe_right")
		hot.set("gravity_mode", false)
		hot.set("speed", 100.0)
		Input.action_press("probe_right")
		await physics_frame
		await physics_frame
		moved = is_equal_approx(body.velocity.x, 100.0)
		Input.action_release("probe_right")
		world.free()
		ok = ok and moved
	print("CPP_HOT_PATH exists=", exists, " speed=", speed,
		" action_jump=", action, " moved=", moved)
	print("CPP_HOT_PATH_CHECK=", "OK" if ok else "FAIL")
	quit(0 if ok else 1)
'''


def find_godot(explicit=""):
    candidate = explicit or os.environ.get("GK_GODOT") or GODOT_DEFAULT
    return candidate if os.path.isfile(candidate) else ""


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def build_graph():
    """离线搭一条标准移动链(和平台跳跃示例同款)。

    2D / 3D 的图结构一样,区别只在 `generate(..., dimension)`。
    """
    import gk_stub
    from BlenderGameKit.nodes import nodes as node_lib

    tree = gk_stub.StubNodeTree("热路径探针")
    physics = tree.new_node(node_lib.GKNodeOnPhysicsProcess, "物理帧")
    direction = tree.new_node(node_lib.GKNodeInputDirection, "方向输入")
    move = tree.new_node(node_lib.GKNodeMoveDirection, "移动",
                         gk_speed=260.0)
    move.gk_keep_y = True
    flip = tree.new_node(node_lib.GKNodeFlip, "翻转")
    flip.gk_mode = "AUTO"
    on_floor = tree.new_node(node_lib.GKNodeIsOnFloor, "在地面")
    jump_key = tree.new_node(node_lib.GKNodeKey, "跳跃键", gk_action="jump",
                            gk_trigger="JUST")
    logic = tree.new_node(node_lib.GKNodeLogic, "与", gk_op="AND")
    branch = tree.new_node(node_lib.GKNodeBranch, "能跳吗")
    jump = tree.new_node(node_lib.GKNodeJump, "起跳", gk_jump_force=560.0)

    gk_stub.connect(tree, physics, "执行", move, "执行")
    gk_stub.connect(tree, direction, "方向", move, "方向")
    gk_stub.connect(tree, move, "完成", flip, "执行")
    gk_stub.connect(tree, flip, "完成", branch, "执行")
    gk_stub.connect(tree, on_floor, "在地面", logic, "A")
    gk_stub.connect(tree, jump_key, "结果", logic, "B")
    gk_stub.connect(tree, logic, "结果", branch, "条件")
    gk_stub.connect(tree, branch, "是", jump, "执行")
    return tree


def prepare(dimension):
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import gk_stub

    gk_stub.install()
    gk_stub.load_addon(os.path.join(ROOT, "BlenderGameKit"))
    from BlenderGameKit import cpp_hot_path

    work = f"{WORK_BASE}_{dimension}d"
    project = os.path.join(work, "project")
    native = os.path.join(project, "native")
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(native, exist_ok=True)
    files, spec = cpp_hot_path.generate(build_graph(), dimension)
    for relative, text in files.items():
        write(os.path.join(project, relative.replace("/", os.sep)), text)
    cls = "GKHotPath2D" if dimension == "2" else "GKHotPath3D"
    body = "CharacterBody2D" if dimension == "2" else "CharacterBody3D"
    world = "Node2D" if dimension == "2" else "Node3D"
    probe = (PROBE_TEMPLATE.replace("__CLASS__", cls)
             .replace("__BODY__", body).replace("__WORLD__", world))
    write(os.path.join(project, "project.godot"), PROJECT_GODOT)
    write(os.path.join(project, "probe.gd"), probe)
    print(f"  {dimension}D:生成 {len(files)} 个文件,速度={spec['speed']} "
          f"跳跃={spec['jump_force']}")
    return project, native


def has_scons():
    try:
        result = subprocess.run(
            [sys.executable, "-m", "SCons", "--version"],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def compile_native(native):
    """用 SCons 真编译。返回 (成功, 输出)。"""
    env = dict(os.environ)
    env["GODOT_CPP_PATH"] = GODOT_CPP
    result = subprocess.run(
        [sys.executable, "-m", "SCons", "platform=windows",
         "target=template_debug", "-j8"],
        cwd=native, capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=env, timeout=600)
    return result.returncode == 0, (result.stdout or "") + (result.stderr or "")


def run_godot(godot, project):
    env = dict(os.environ, GODOT_SILENCE_ROOT_WARNING="1")
    common = dict(capture_output=True, text=True, encoding="utf-8",
                  errors="replace", env=env, timeout=120)
    subprocess.run([godot, "--headless", "--path", project, "--import"],
                   **common)
    result = subprocess.run(
        [godot, "--headless", "--path", project, "--script",
         "res://probe.gd"], **common)
    return (result.stdout or "") + (result.stderr or ""), result.returncode


def main():
    parser = argparse.ArgumentParser(description="C++ 热路径真机验证")
    parser.add_argument("--godot", default="")
    args = parser.parse_args()

    godot = find_godot(args.godot)
    if not godot:
        print(f"跳过:找不到 Godot({args.godot or GODOT_DEFAULT})")
        return 0
    if not os.path.isdir(GODOT_CPP):
        print(f"跳过:找不到 godot-cpp({GODOT_CPP})")
        return 0
    if not has_scons():
        print("跳过:找不到 SCons(pip install scons)")
        return 0

    for dimension in ("2", "3"):
        project, native = prepare(dimension)
        ok, output = compile_native(native)
        tail = output.strip().splitlines()[-10:]
        for line in tail:
            print("  " + line)
        if not ok:
            print(f"CPP_HOT_PATH=FAILED({dimension}D 编译失败)")
            return 1

        text, code = run_godot(godot, project)
        for line in text.splitlines():
            if "CPP_HOT_PATH" in line or "ERROR" in line:
                print(line.strip())
        if "CPP_HOT_PATH_CHECK=OK" not in text:
            print(f"CPP_HOT_PATH=FAILED({dimension}D 加载 / 属性检查失败)")
            return 1 if code != 0 or text else 1

    print("CPP_HOT_PATH=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
