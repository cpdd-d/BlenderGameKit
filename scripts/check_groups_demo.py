"""集合层级示例的自动检查(第一百零二轮)。

用法:
    python scripts\\check_groups_demo.py
    python scripts\\check_groups_demo.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

它会:
  1. 把 `examples/demo_groups/集合层级示例_godot` 拷到 `_dist/groups_probe`
     (**不碰示例本身**);
  2. 在副本里加一个探针场景,把主场景指过去;
  3. 用 Godot 无头跑,读探针打印的 GROUPS_* 行,汇总 PASS / FAIL。

断言(全是"现象",不是"代码长什么样"):
  * **集合就是节点分组**:`房间A/A_墙` 和嵌套的 `房间B/架子/架子_板1`
    要能用**路径**取到(`get_node_or_null("...")`)—— 只"找得到"不算,
    这一关验的就是"层级";
  * 直接放在 Blender 根集合里的物体(地面)在 World 根下;
  * **撞名集合**保留了 `Scene Collection/` 前缀,而导出生成的碰撞分组
    `Colliders` 照常在根下 —— 两个不打架(都要在);
  * **空集合**『暂时不用』在 Godot 里不存在(Blender 导出时丢掉了);
  * 分组没把功能弄坏:角色照样能走(按 D 一路走到墙边)、卡通材质照样
    挂得上(材质覆盖 > 0)、而且**被墙挡住**(2 秒后没穿出场地)。
"""

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "examples", "demo_groups", "集合层级示例_godot")
OUT = os.path.join(ROOT, "_dist", "groups_probe")
GODOT_DEFAULT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"

PROBE_GD = '''extends Node

# 集合层级示例的探针。结论行统一 GROUPS_ 前缀。
# 这一关验的是**结构**:Blender 的集合在 Godot 里就是节点分组 ——
# 所以直接用 get_node_or_null("路径") 查**层级**(只用 find_child 的话
# "随便在哪都能找到",证明不了分组)。
# ⚠ 材质是环境脚本在 _ready 里挂的(deferred),所以要等几帧再统计。

var world: Node = null
var player: CharacterBody3D = null
var start_x := 0.0
var end_x := 0.0
var elapsed := 0.0
var phase := 0


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	world = load("res://scenes/World.tscn").instantiate()
	add_child(world)
	player = world.find_child("示例角色", true, false) as CharacterBody3D
	if player != null:
		start_x = player.global_position.x


func _report() -> void:
	var room_a := world.get_node_or_null("房间A/A_墙") != null
	var shelf := world.get_node_or_null("房间B/架子/架子_板1") != null
	var floor_direct := world.get_node_or_null("地面") != null
	var clash := world.get_node_or_null(
		"Scene Collection/Colliders/重名方块") != null
	var empty_gone := world.get_node_or_null("暂时不用") == null
	var colliders := world.get_node_or_null("Colliders") != null
	print("GROUPS_STRUCT room_a=", room_a, " shelf=", shelf,
		" floor=", floor_direct, " clash=", clash,
		" empty_gone=", empty_gone, " colliders=", colliders)
	var textured := 0
	for node in world.find_children("*", "MeshInstance3D", true, false):
		if node.material_override != null:
			textured += 1
	print("GROUPS_MATERIALS textured=", textured)


func _send_key(code: int, pressed: bool) -> void:
	# 和真人按键走同一条路(事件流)—— 生成脚本里既有
	# `Input.is_action_*` 也有 `is_key_pressed`,parse_input_event 两边都喂到。
	var event := InputEventKey.new()
	event.keycode = code
	event.physical_keycode = code
	event.pressed = pressed
	Input.parse_input_event(event)


func _physics_process(delta: float) -> void:
	elapsed += delta
	match phase:
		0:
			if elapsed >= 0.25:
				phase = 1
				_report()
				# 按 D 往 +x 走 —— 那边有『房间A』的墙(x=5),2 秒足够撞上
				_send_key(KEY_D, true)
		1:
			if elapsed >= 2.4:
				phase = 2
				_send_key(KEY_D, false)
				end_x = player.global_position.x if player != null else 0.0
				print("GROUPS_WALK start_x=", snappedf(start_x, 0.01),
					" end_x=", snappedf(end_x, 0.01))
				print("GROUPS_DONE")
				set_physics_process(false)
				get_tree().quit()
'''

PROBE_TSCN = '''[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://probe.gd" id="1_probe"]

[node name="Probe" type="Node"]
script = ExtResource("1_probe")
'''


def prepare():
    if not os.path.isdir(SRC):
        print(f"找不到示例工程 {SRC} —— 先跑 make_groups_demo.py")
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
    text = re.sub(r'run/main_scene="[^"]*"',
                  'run/main_scene="res://probe.tscn"', text)
    with open(project_file, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return True


def run_godot(godot):
    subprocess.run([godot, "--headless", "--path", OUT, "--import"],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")
    run = subprocess.run([godot, "--headless", "--path", OUT,
                          "--quit-after", "1800"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    return (run.stdout or "") + (run.stderr or "")


def _grab(text, pattern):
    match = re.search(pattern, text)
    return match.group(0) if match else "<没跑到>"


def _glb_tree(path):
    """读 .glb 的 JSON 块,返回 {节点名: [子节点名, ...]}(只看有子节点的)。"""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    offset = 12
    payload = None
    while offset + 8 <= len(data):
        length, kind = struct.unpack_from("<II", data, offset)
        offset += 8
        if kind == 0x4E4F534A:
            try:
                payload = json.loads(data[offset:offset + length].decode("utf-8"))
            except ValueError:
                return None
            break
        offset += length
    if payload is None:
        return None
    nodes = payload.get("nodes", [])
    tree = {}
    for node in nodes:
        children = node.get("children") or []
        if children:
            tree[node.get("name", "")] = [nodes[c].get("name", "")
                                          for c in children
                                          if 0 <= c < len(nodes)]
    return tree


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--godot", default=GODOT_DEFAULT)
    args = parser.parse_args()
    if not prepare():
        return 1
    text = run_godot(args.godot)
    for line in text.splitlines():
        if "GROUPS_" in line or "SCRIPT ERROR" in line or "Parse Error" in line:
            print(line.strip())

    problems = []
    for line in text.splitlines():
        if "SCRIPT ERROR" in line or "Parse Error" in line:
            problems.append(f"Godot 脚本 / 解析错误: {line.strip()}")
            break

    struct_line = _grab(text, r"GROUPS_STRUCT[^\n]*")
    expect = ("room_a=true", "shelf=true", "floor=true", "clash=true",
              "empty_gone=true", "colliders=true")
    if not re.search(r"GROUPS_STRUCT " + r" ".join(
            re.escape(part) for part in expect), text):
        problems.append("集合 -> 节点分组不对(房间A / 嵌套架子 / 根下物体 / "
                        "撞名前缀 / 空集合 / 碰撞分组 至少有一个不符):"
                        + struct_line)

    materials = re.search(r"GROUPS_MATERIALS textured=(\d+)", text)
    if not materials:
        problems.append("材质数量没量出来: "
                        + _grab(text, r"GROUPS_MATERIALS[^\n]*"))
    elif int(materials.group(1)) < 6:
        problems.append(f"挂上的材质太少({materials.group(1)} 个)—— "
                        f"分组之后环境脚本按名字找网格那条链断了?")

    walk = re.search(r"GROUPS_WALK start_x=([\-\d.]+) end_x=([\-\d.]+)", text)
    if not walk:
        problems.append("角色位移没量出来: " + _grab(text, r"GROUPS_WALK[^\n]*"))
    else:
        start_x = float(walk.group(1))
        end_x = float(walk.group(2))
        moved = abs(end_x - start_x)
        if moved < 1.2:
            problems.append(f"角色没走动({start_x} -> {end_x})—— "
                            f"玩法图 / 输入映射在分组之后没跑起来?")
        # 两侧都有墙(x=±5,墙厚 0.6、角色半宽 0.5)-> 中心最多到 ±4.2;
        # 走到 4.7 以外 = 穿墙了;走到 6 以外 = 连场地都出去了。
        if abs(end_x) > 4.7:
            problems.append(f"角色穿过了墙(end_x={end_x},墙在 ±4.7 就该停下)"
                            f"—— 碰撞代理没跟着走?")
    if "GROUPS_DONE" not in text:
        problems.append("探针没跑完(可能中途报错)")

    # ── 生成物层面的证据(不需要 Godot 也能看)─────────────────────────
    glb = os.path.join(OUT, "art", "World.glb")
    tree = _glb_tree(glb)
    if tree is None:
        problems.append(f"没读到 glb 的节点树: {glb}")
    else:
        if tree.get("房间B") != ["B_墙", "架子"]:
            problems.append(f"glb 里『房间B』的子节点不对: {tree.get('房间B')}")
        if tree.get("架子") != ["架子_板1", "架子_板2"]:
            problems.append(f"glb 里嵌套集合『架子』的子节点不对: {tree.get('架子')}")
        if tree.get("Scene Collection") != ["Colliders"]:
            problems.append("glb 里撞名集合没保留在 Scene Collection 下: "
                            + str(tree.get("Scene Collection")))
        if "暂时不用" in tree or any(
                "暂时不用" in names for names in tree.values()):
            problems.append("空集合『暂时不用』不该出现在 glb 里")

    scene_path = os.path.join(OUT, "scenes", "World.tscn")
    if os.path.isfile(scene_path):
        with open(scene_path, "r", encoding="utf-8") as handle:
            scene = handle.read()
        if '[node name="World" instance=ExtResource("1_world")]' not in scene:
            problems.append("World.tscn 里没有 glb 实例节点(场景结构变了)")
        if '[node name="Colliders" type="Node3D" parent="."]' not in scene:
            problems.append("World.tscn 里没有碰撞分组(结构变了)")
    else:
        problems.append(f"没找到主场景 {scene_path}")

    for item in dict.fromkeys(problems):
        print(f"✗ {item}")
    print("CHECK_GROUPS_DEMO=" + ("OK" if not problems else "FAIL"))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())