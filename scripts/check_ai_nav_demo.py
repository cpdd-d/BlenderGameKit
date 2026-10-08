"""AI 绕墙追击示例的自动检查(第六十三轮)。

用法:
    python scripts\\check_ai_nav_demo.py
    python scripts\\check_ai_nav_demo.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

它会:
  1. 把 `examples/demo_ai_nav/AI寻路示例_godot` 拷到 `_dist/ai_nav_probe`
     (**不碰示例本身**);
  2. 在副本里加一个探针场景,把主场景指过去;
  3. 用 Godot 无头跑,读探针打印的 AINAV_* 行,汇总 PASS / FAIL。

── 它验的是"AI 真的绕过墙追到了人"(不是"代码长什么样")────────────
旁边 5 米宽的墙把『敌人』和『玩家』隔开,直线距离只有 4 米。这个示例里
**一个『移动_寻路』节点都没有** —— 敌人的移动全部来自『AI_状态』的追击
行为。所以:

  * `polys > 0`      —— 导航网格真烤出来了(场景设置了『寻路』);
  * `around >= 2.8`  —— 敌人横着走出去了 2.8 米以上 = **从墙端绕过去**的
                        (墙只铺到 ±2.5 米,直线追会在墙前贴住,around≈0);
  * `arrived`        —— 最后贴到了玩家 1.5 米以内;
  * `state = 追击`   —— AI 状态机真的跑起来了;
  * 生成的脚本里有 `_gk_ai_nav_dir` —— 走的是新的"AI + 导航网格"那条链。
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
SRC = os.path.join(ROOT, "examples", "demo_ai_nav", "AI寻路示例_godot")
OUT = os.path.join(ROOT, "_dist", "ai_nav_probe")
GODOT_DEFAULT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"

WINDOW = 8.0          # 采样窗口(秒):绕一圈约 9 米 / 4.5 米每秒 -> 2 秒多
ARRIVED_DIST = 1.5    # 贴到这个距离内算"追上了"

PROBE_GD = '''extends Node

# AI 绕墙追击的探针。结论行统一 AINAV_ 前缀。
# 敌人是 CharacterBody3D(自己挂脚本、自己跑 AI),玩家只是个静态方块。

const WINDOW := %.1f
const ARRIVED_DIST := %.1f

var world: Node = null
var enemy: CharacterBody3D = null
var player: Node3D = null
var elapsed := 0.0
var around := 0.0        # 离墙中线(x=0)最远横走了多少 = 绕墙的证据
var min_dist := 999.0
var arrived := false
var polys := 0
var last_sec := -1


func _ready() -> void:
\tprocess_mode = Node.PROCESS_MODE_ALWAYS
\tworld = load("res://scenes/World.tscn").instantiate()
\tadd_child(world)
\tvar found := world.find_children("敌人", "CharacterBody3D", true, false)
\tif not found.is_empty():
\t\tenemy = found[0] as CharacterBody3D
\tvar targets := world.find_children("玩家", "Node3D", true, false)
\tif not targets.is_empty():
\t\tplayer = targets[0] as Node3D
\t# 导航网格烤出来没有:读区域里那份 mesh 的多边形数(和物理测试场同一招)
\tvar regions := world.find_children("Navigation", "NavigationRegion3D", true, false)
\tif not regions.is_empty():
\t\tvar mesh: NavigationMesh = (regions[0] as NavigationRegion3D).navigation_mesh
\t\tif mesh != null:
\t\t\tpolys = mesh.get_polygon_count()
\tprint("AINAV_START enemy=", enemy != null, " player=", player != null,
\t\t" polys=", polys, " state=", _state())


func _state() -> String:
\tif enemy == null:
\t\treturn "<no-enemy>"
\tvar value = enemy.get("ai_state")
\treturn "<null>" if value == null else str(value)


func _physics_process(delta: float) -> void:
\telapsed += delta
\tif enemy != null and player != null:
\t\tvar here: Vector3 = enemy.global_position
\t\taround = maxf(around, absf(here.x))
\t\tvar dist := here.distance_to(player.global_position)
\t\tmin_dist = minf(min_dist, dist)
\t\tif dist <= ARRIVED_DIST:
\t\t\tarrived = true
\t\tif int(elapsed) != last_sec:
\t\t\tlast_sec = int(elapsed)
\t\t\t# 诊断:顺便把"导航服务器此刻给的路径"打出来 —— 卡住时一眼能看出
\t\t\t# 是"没有路径"还是"有路径但推不动"。
\t\t\tvar map := enemy.get_world_3d().navigation_map
\t\t\tvar path := NavigationServer3D.map_get_path(
\t\t\t\tmap, here, player.global_position, true)
\t\t\tvar next_pt := Vector3.ZERO if path.size() <= 1 else path[1]
\t\t\tprint("AINAV_T t=", last_sec, " x=", snappedf(here.x, 0.01),
\t\t\t\t" z=", snappedf(here.z, 0.01), " dist=", snappedf(dist, 0.01),
\t\t\t\t" vel=", enemy.velocity.snappedf(0.01),
\t\t\t\t" n=", path.size(), " next=", next_pt.snappedf(0.01))
\tif elapsed >= WINDOW:
\t\tprint("AINAV_DONE around=", snappedf(around, 0.01),
\t\t\t" min_dist=", snappedf(min_dist, 0.01),
\t\t\t" arrived=", arrived, " polys=", polys, " state=", _state())
\t\tset_physics_process(false)
\t\tget_tree().quit()
''' % (WINDOW, ARRIVED_DIST)

PROBE_TSCN = '''; 探针(临时文件,不写回示例)
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://probe.gd" id="1_probe"]

[node name="AiNavProbe" type="Node"]
script = ExtResource("1_probe")
'''


def prepare():
    if not os.path.isdir(SRC):
        print(f"找不到示例工程 {SRC} —— 先跑 make_ai_nav_demo.py")
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--godot", default=GODOT_DEFAULT)
    args = parser.parse_args()
    if not prepare():
        return 1
    text = run_godot(args.godot)
    for line in text.splitlines():
        if "AINAV_" in line or "SCRIPT ERROR" in line or "Parse Error" in line:
            print(line.strip())

    problems = []
    for line in text.splitlines():
        if "SCRIPT ERROR" in line or "Parse Error" in line:
            problems.append(f"Godot 脚本 / 解析错误: {line.strip()}")
            break

    start = re.search(r"AINAV_START enemy=true player=true polys=(\d+)", text)
    if not start:
        problems.append("敌人 / 玩家没找齐: " + _grab(text, r"AINAV_START[^\n]*"))
    elif int(start.group(1)) <= 0:
        problems.append("导航网格没烤出来(polys=0)—— 场景设置的『寻路』"
                        "是不是没勾?")

    done = re.search(r"AINAV_DONE around=([\-\d.]+) min_dist=([\-\d.]+) "
                     r"arrived=(\w+) polys=(\d+) state=([^\s]*)", text)
    if not done:
        problems.append("结算行没出来(探针没跑完): "
                        + _grab(text, r"AINAV_DONE[^\n]*"))
    else:
        around = float(done.group(1))
        min_dist = float(done.group(2))
        arrived = done.group(3) == "true"
        state = done.group(5)
        # ── 绕墙的证据 ──────────────────────────────────────────────
        # 墙只铺到 ±2.5 米:直线追会在墙前贴住(around ≈ 0),
        # 绕过去一定得横着走出 2.8 米以上。
        if around < 2.8:
            problems.append(
                f"敌人没绕墙(离墙中线最远只走了 {around} 米)—— "
                f"AI 的移动大概还在走直线")
        if not arrived:
            problems.append(
                f"敌人没追到玩家(最近 {min_dist} 米 > {ARRIVED_DIST})—— "
                f"要么没绕过去,要么卡在墙上")
        if state != "追击":
            problems.append(f"AI 状态机没跑起来(ai_state={state!r})")

    # ── 生成物层面的证据 ────────────────────────────────────────────
    # ⚠ 脚本名跟着**场景名**走(这里叫 Ai.gd),不是 `game_logic.gd` ——
    #   别写死;环境脚本(gk_environment*)不是主逻辑,跳过。
    scripts_dir = os.path.join(OUT, "scripts")
    logic_files = sorted(
        path for path in glob.glob(os.path.join(scripts_dir, "*.gd"))
        if not os.path.basename(path).startswith("gk_environment"))
    if logic_files:
        with open(logic_files[0], "r", encoding="utf-8") as handle:
            code = handle.read()
        for needle, why in (
                ("func _gk_ai_nav_dir(", "AI 走导航网格的辅助函数"),
                ("NavigationServer3D.map_get_path(", "真的向导航服务器要路径"),
                ("for i in range(1, path.size()):", "跳过退化路点、盯下一个拐角"),
                ("_gk_ai_nav_dir(", "追击行为真的用上了它"),
                # 这个示例里**只有 AI**,没有『移动_寻路』节点 —— 追人的
                # 方向来自上面那个 helper,不是另一套内联的寻路代码。
                ("var ai_state: String", "AI 状态机(证明走的是 AI 那条链)"),
        ):
            if needle not in code:
                problems.append(f"生成的脚本里没有{why}: 找不到 {needle!r}")
    else:
        problems.append(f"没找到生成的脚本({scripts_dir}/*.gd)")

    for item in dict.fromkeys(problems):
        print(f"✗ {item}")
    print("CHECK_AI_NAV_DEMO=" + ("OK" if not problems else "FAIL"))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
