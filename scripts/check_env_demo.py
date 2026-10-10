"""环境示例的自动检查(第四十九轮:阶段②环境与后处理)。

用法:
    python scripts\\check_env_demo.py
    python scripts\\check_env_demo.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

它会:
  1. 把 `examples/demo_env/环境示例_godot` 拷到 `_dist/env_probe`(**不碰示例本身**);
  2. 在副本里加一个探针场景(`probe.gd` / `probe.tscn`),把主场景指过去;
  3. 用 Godot 无头跑,读探针打印的 ENV_* 行,汇总 PASS / FAIL。

探针查的是"这一关该有的现象"(不是代码长什么样):
  * **白天**:背景 = 程序化天空(Sky 不为空)、环境光 0.8、**没有雾**;
  * **幽暗**:背景 = 近黑纯色、雾开着(浓度 0.08)、泛光开着、色调 = ACES、
    环境光 0.15;
  * **两关确实不一样**(对照才说明环境真的在起作用)。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "examples", "demo_env", "环境示例_godot")
OUT = os.path.join(ROOT, "_dist", "env_probe")

PROBE_GD = '''extends Node

# 环境示例的探针:验 WorldEnvironment 的背景 / 环境光 / 雾 / 泛光 / 色调。
# 结论行统一用 ENV_ 前缀,Python 那边按前缀收集、按内容断言。

var day_env: Environment = null
var night_env: Environment = null


func _ready() -> void:
	var day: Node = load("res://scenes/白天.tscn").instantiate()
	add_child(day)
	var day_we := day.find_child("WorldEnvironment", true, false) as WorldEnvironment
	if day_we != null:
		day_env = day_we.environment
	var night: Node = load("res://scenes/幽暗.tscn").instantiate()
	add_child(night)
	var night_we := night.find_child("WorldEnvironment", true, false) as WorldEnvironment
	if night_we != null:
		night_env = night_we.environment
	_report()
	set_process(false)
	get_tree().quit()


func _report() -> void:
	# 白天:天空背景 + 亮环境光 + 无雾
	var day_bg := -1
	var day_sky := false
	var day_ambient := -1.0
	var day_tone := -1
	var day_fog := true
	if day_env != null:
		day_bg = day_env.background_mode
		day_sky = day_env.sky != null
		day_ambient = day_env.ambient_light_energy
		day_tone = day_env.tonemap_mode
		day_fog = day_env.fog_enabled
	var day_ok := day_bg == 2 and day_sky and (not day_fog) \\
		and absf(day_ambient - 0.8) <= 0.05 and day_tone == 4
	print("ENV_DAY bg=", day_bg, " sky=", day_sky,
		" ambient=", "%.3f" % day_ambient, " fog=", day_fog,
		" tonemap=", day_tone, " ok=", day_ok)

	# 幽暗:纯色背景 + 雾 + 泛光 + ACES
	var night_bg := -1
	var night_fog := false
	var night_density := -1.0
	var night_glow := false
	var night_tone := -1
	var night_ambient := -1.0
	if night_env != null:
		night_bg = night_env.background_mode
		night_fog = night_env.fog_enabled
		night_density = night_env.fog_density
		night_glow = night_env.glow_enabled
		night_tone = night_env.tonemap_mode
		night_ambient = night_env.ambient_light_energy
	var night_ok := night_bg == 1 and night_fog \\
		and absf(night_density - 0.08) <= 0.005 and night_glow \\
		and night_tone == 3 and absf(night_ambient - 0.15) <= 0.02
	print("ENV_NIGHT bg=", night_bg, " fog=", night_fog,
		" density=", "%.4f" % night_density, " glow=", night_glow,
		" tonemap=", night_tone, " ambient=", "%.3f" % night_ambient,
		" ok=", night_ok)

	# 两关确实不一样(对照)
	var differ := day_env != null and night_env != null and day_bg != night_bg
	print("ENV_DIFF day_bg=", day_bg, " night_bg=", night_bg, " ok=", differ)
	# 第九十七轮:环境光来源 —— 纯色背景必须是 Color(2),写 Sky(3) 的话
	# 没有天空可用,Godot 会当"没有环境光"(真机标定:和 Disabled 一模一样),
	# 用户填的『环境光颜色 / 强度』等于白填。天空背景才该用 Sky(3)。
	var day_source := -1
	var night_source := -1
	if day_env != null:
		day_source = day_env.ambient_light_source
	if night_env != null:
		night_source = night_env.ambient_light_source
	print("ENV_SOURCE day=", day_source, " night=", night_source)

	# 第九十六轮:颜色数字的色空间 —— .tscn 里的 Color 是"显示值"语义,
	# 导出会把 Blender 的线性颜色换算成显示值(取色器上的色块 = 游戏里的颜色)
	var night_bg_r := -1.0
	var day_top_r := -1.0
	if night_env != null:
		night_bg_r = night_env.background_color.r
	if day_env != null and day_env.sky != null:
		var sky_mat := day_env.sky.sky_material as ProceduralSkyMaterial
		if sky_mat != null:
			day_top_r = sky_mat.sky_top_color.r
	print("ENV_COLOR night_bg_r=", "%.4f" % night_bg_r,
		" day_top_r=", "%.4f" % day_top_r)
	print("ENV_DONE")
'''

PROBE_TSCN = '''; 探针(临时文件,不写回示例)
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://probe.gd" id="1_probe"]

[node name="EnvProbe" type="Node"]
script = ExtResource("1_probe")
'''


def prepare():
    if not os.path.isdir(SRC):
        print(f"找不到示例工程 {SRC} —— 先跑 make_env_demo.py")
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
        text = re.sub(r'run/main_scene="[^"]*"',
                      'run/main_scene="res://probe.tscn"', text)
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
                          "--quit-after", "900"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    return (run.stdout or "") + (run.stderr or "")


def _linear_to_srgb(value):
    """线性 -> sRGB(第九十六轮:导出写 .tscn 时用的那条换算)。

    和 asset_export 里走的是同一个公式(那边借的是 godot_look 的实现)——
    这里抄一份是为了**独立算期望值**,免得"用被测代码验被测代码"。
    """
    if value <= 0.0031308:
        return value * 12.92
    return 1.055 * (value ** (1.0 / 2.4)) - 0.055


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
        if "ENV_" in line or "SCRIPT ERROR" in line or "Parse Error" in line:
            print(line.strip())

    ok = True
    if any(("SCRIPT ERROR" in line) or ("Parse Error" in line)
           for line in text.splitlines()):
        print("✗ Godot 里有脚本错误 / 解析错误")
        ok = False

    if not re.search(r"ENV_DAY .*ok=true", text):
        print("✗ 白天环境不对(应是天空背景 + 环境光 0.8 + 无雾):",
              [line for line in text.splitlines() if "ENV_DAY" in line])
        ok = False
    if not re.search(r"ENV_NIGHT .*ok=true", text):
        print("✗ 幽暗环境不对(应是纯色背景 + 雾 + 泛光 + ACES):",
              [line for line in text.splitlines() if "ENV_NIGHT" in line])
        ok = False
    if not re.search(r"ENV_DIFF .*ok=true", text):
        print("✗ 两关环境没拉开差距:",
              [line for line in text.splitlines() if "ENV_DIFF" in line])
        ok = False

    # ── 第九十七轮:环境光来源 ──────────────────────────────────────
    source = re.search(r"ENV_SOURCE day=(-?\d+) night=(-?\d+)", text)
    if not source:
        print("✗ 没跑出环境光来源的结论行(ENV_SOURCE)")
        ok = False
    else:
        day_source = int(source.group(1))
        night_source = int(source.group(2))
        print(f"    环境光来源: 白天={day_source}(期望 3=Sky)"
              f" 幽暗={night_source}(期望 2=Color)")
        if day_source != 3:
            print("✗ 天空背景的环境光来源该是 3(Sky):", day_source)
            ok = False
        if night_source != 2:
            print("✗ 纯色背景的环境光来源该是 2(Color)—— 写 Sky 等于关掉:",
                  night_source)
            ok = False

    # ── 第九十六轮:颜色数字过没过 linear->sRGB ──────────────────────
    # 幽暗的背景色设置是线性 (0.004,...) -> 写进 .tscn 应该是显示值 ≈0.05;
    # 白天的天空顶色是线性 0.35 -> 显示值 ≈0.62。差一个数量级,很好认。
    colour = re.search(r"ENV_COLOR night_bg_r=([\d.]+) day_top_r=([\d.]+)", text)
    if not colour:
        print("✗ 没跑出颜色色空间的结论行(ENV_COLOR)")
        ok = False
    else:
        night_r = float(colour.group(1))
        day_r = float(colour.group(2))
        expect_night = _linear_to_srgb(0.004)
        expect_day = _linear_to_srgb(0.35)
        print(f"    颜色: 幽暗背景 r={night_r:.4f}(期望 {expect_night:.4f}) "
              f"白天天空顶 r={day_r:.4f}(期望 {expect_day:.4f})")
        if abs(night_r - expect_night) > 0.02:
            print("✗ 幽暗背景没换算成显示值(还是线性的?):",
                  night_r, "!=", round(expect_night, 4))
            ok = False
        if abs(day_r - expect_day) > 0.02:
            print("✗ 天空顶色没换算成显示值(还是线性的?):",
                  day_r, "!=", round(expect_day, 4))
            ok = False
    if "ENV_DONE" not in text:
        print("✗ 探针没跑完(可能中途报错)")
        ok = False

    # ── 生成物层面的证据(直接读 .tscn)────────────────────────────
    day_path = os.path.join(OUT, "scenes", "白天.tscn")
    night_path = os.path.join(OUT, "scenes", "幽暗.tscn")
    if os.path.isfile(day_path):
        with open(day_path, "r", encoding="utf-8") as handle:
            day = handle.read()
        for needle, why in (
                ('[node name="WorldEnvironment" type="WorldEnvironment"',
                 "WorldEnvironment 节点"),
                ('[sub_resource type="Environment" id="gk_environment"]',
                 "Environment 子资源"),
                ('[sub_resource type="Sky" id="gk_sky"]', "Sky 子资源"),
                ("background_mode = 2", "天空背景"),
                ("ambient_light_energy = 0.800000", "环境光 0.8"),
                ("fog_enabled = false", "雾关着"),
                ("tonemap_mode = 4", "AgX"),
        ):
            if needle not in day:
                print(f"✗ 白天.tscn 里没有{why}: 找不到 {needle!r}")
                ok = False
    else:
        print(f"✗ 没找到主场景 {day_path}")
        ok = False

    if os.path.isfile(night_path):
        with open(night_path, "r", encoding="utf-8") as handle:
            night = handle.read()
        for needle, why in (
                ("background_mode = 1", "纯色背景"),
                ("fog_enabled = true", "雾开着"),
                ("fog_density = 0.080000", "雾浓度 0.08"),
                ("glow_enabled = true", "泛光开着"),
                ("glow_intensity = 1.200000", "泛光强度 1.2"),
                ("tonemap_mode = 3", "ACES"),
                ("ambient_light_energy = 0.150000", "环境光 0.15"),
        ):
            if needle not in night:
                print(f"✗ 幽暗.tscn 里没有{why}: 找不到 {needle!r}")
                ok = False
    else:
        print(f"✗ 没找到第二关场景 {night_path}")
        ok = False

    print("CHECK_ENV_DEMO=" + ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
