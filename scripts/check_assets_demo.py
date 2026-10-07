"""资产示例的自动检查(第四十三轮:游戏资产)。

用法:
    python scripts\\check_assets_demo.py
    python scripts\\check_assets_demo.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

它会:
  1. 把 `examples/demo_assets/资产示例_godot` 拷到 `_dist/assets_probe`
     (**不碰示例本身**);
  2. 在副本里加一个探针场景(`probe.gd` + `probe.tscn`),把主场景指过去;
  3. 用 Godot 无头跑,读探针打印的 ASSET_* 行,汇总 PASS / FAIL。

探针查的是"资产库该有的现象"(不是代码长什么样):
  * 三棵树 **共用同一个模型**(探针里三个 Model 子的 scene_file_path 相同)
    —— "一个模型导一次、实例复用"落到了真 Godot 里;
  * 音频文件真的在 `res://audio/` 下、能 load 成 AudioStream;
  * 生成物层面的证据(不跑游戏也能查):
      - `audio/` 里那份 .wav 存在,脚本里 `load()` 的就是它;
      - `art/ui_*.png`(图片资产)与 `curves/*.tres`(曲线资产)都在;
      - 每个场景里资产 glb **各一份**(摆三份树也只导一次);
      - 第一关 .tscn:3 棵树 + 1 块石头,三个 Model 引用的 ext_resource 是同一条;
      - 第二关 .tscn:树是从**跨场景**摆过来的(也导出了资产 glb)。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "examples", "demo_assets", "资产示例_godot")
OUT = os.path.join(ROOT, "_dist", "assets_probe")

MAIN_SCENE = "资产第一关"
SECOND_SCENE = "资产第二关"

PROBE_GD = '''extends Node

# 资产示例的探针:看"资产库"这套东西真的落到了游戏里。
# 结论行统一用 ASSET_ 前缀,Python 那边按前缀收集、按内容断言。

var world: Node = null
var elapsed := 0.0
var done := false


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	world = load("res://scenes/{main_scene}.tscn").instantiate()
	add_child(world)


func _physics_process(delta: float) -> void:
	if done:
		return
	elapsed += delta
	if elapsed < 0.4:
		return
	done = true

	var names := ["树_本体", "树_实例1", "树_实例2", "石头_本体"]
	var found := []
	var model_paths := {}
	for wanted in names:
		var node := world.find_child(wanted, true, false)
		found.append(node != null)
		if node != null:
			var model := node.find_child("Model", false, false)
			model_paths[wanted] = "" if model == null else str(model.scene_file_path)
	print("ASSET_NODES found=", found)

	var tree_paths := [str(model_paths.get("树_本体", "")),
		str(model_paths.get("树_实例1", "")),
		str(model_paths.get("树_实例2", ""))]
	var same: bool = tree_paths[0] != "" and tree_paths[0] == tree_paths[1] \\
		and tree_paths[0] == tree_paths[2]
	print("ASSET_MODEL same=", same, " path=", tree_paths[0])

	# 音频:资产库把它复制进 res://audio/ 才算成功
	# (目录里还有 Godot 导入生成的 .import,按扩展名挑)
	var wav_name := ""
	for item in DirAccess.get_files_at("res://audio"):
		var lower := item.to_lower()
		if lower.ends_with(".wav") or lower.ends_with(".ogg") \\
				or lower.ends_with(".mp3"):
			wav_name = item
	var stream: AudioStream = null
	if wav_name != "":
		stream = load("res://audio/" + wav_name) as AudioStream
	print("ASSET_SOUND file=", wav_name, " loaded=", stream != null)

	print("ASSET_DONE")
	set_physics_process(false)
	get_tree().quit()
'''

PROBE_TSCN = '''; 探针(临时文件,不写回示例)
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://probe.gd" id="1_probe"]

[node name="AssetsProbe" type="Node"]
script = ExtResource("1_probe")
'''


def prepare():
    if not os.path.isdir(SRC):
        print(f"找不到示例工程 {SRC} —— 先跑 make_assets_demo.py")
        return False
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    shutil.copytree(SRC, OUT, ignore=shutil.ignore_patterns(".godot", "*.import"))
    with open(os.path.join(OUT, "probe.gd"), "w",
              encoding="utf-8", newline="\n") as handle:
        # 用 replace 而不是 format:GDScript 里有 `{}`(空字典字面量),
        # format 会把它当成占位符报错(踩过)
        handle.write(PROBE_GD.replace("{main_scene}", MAIN_SCENE))
    with open(os.path.join(OUT, "probe.tscn"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE_TSCN)
    project_file = os.path.join(OUT, "project.godot")
    with open(project_file, "r", encoding="utf-8") as handle:
        text = handle.read()
    target = f"res://scenes/{MAIN_SCENE}.tscn"
    if "run/main_scene" in text:
        text = text.replace(f'run/main_scene="{target}"',
                            'run/main_scene="res://probe.tscn"')
    else:
        text = text.replace("[application]",
                            '[application]\n\nrun/main_scene="res://probe.tscn"')
    with open(project_file, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return True


def run_godot(godot):
    import_result = subprocess.run(
        [godot, "--headless", "--path", OUT, "--import"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    run = subprocess.run([godot, "--headless", "--path", OUT,
                          "--quit-after", "1800"],
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    return ((run.stdout or "") + (run.stderr or ""),
            (import_result.stdout or "") + (import_result.stderr or ""))


def read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--godot", default="godot")
    args = parser.parse_args()
    if not prepare():
        return 1
    text, import_text = run_godot(args.godot)
    for line in text.splitlines():
        if "ASSET_" in line or "SCRIPT ERROR" in line or "Parse Error" in line:
            print(line.strip())

    ok = True
    for label, blob in (("运行", text), ("导入", import_text)):
        for bad in ("SCRIPT ERROR", "Parse Error", "Failed loading resource"):
            if bad in blob:
                print(f"✗ {label}期有错误: {bad}")
                ok = False
    if "找不到音频文件" in text:
        print("✗ 游戏里报了『找不到音频文件』—— 音频没被带进项目")
        ok = False

    if not re.search(r"ASSET_NODES found=\[true, true, true, true\]", text):
        print("✗ 三棵树 / 石头没全在场景里:",
              [line for line in text.splitlines()
               if line.startswith("ASSET_NODES")])
        ok = False
    if not re.search(r"ASSET_MODEL same=true path=res://art/", text):
        print("✗ 三棵树的模型不是同一个 glb(实例没有复用):",
              [line for line in text.splitlines()
               if line.startswith("ASSET_MODEL")])
        ok = False
    if not re.search(r"ASSET_SOUND file=\S+ loaded=true", text):
        print("✗ 音频没进 audio/ 或加载不出来:",
              [line for line in text.splitlines()
               if line.startswith("ASSET_SOUND")])
        ok = False
    if "ASSET_DONE" not in text:
        print("✗ 探针没跑完(可能中途报错)")
        ok = False

    # ── 生成物层面的证据 ────────────────────────────────────────────
    audio_dir = os.path.join(OUT, "audio")
    wavs = []
    if os.path.isdir(audio_dir):
        wavs = [name for name in os.listdir(audio_dir)
                if name.lower().endswith((".wav", ".ogg", ".mp3"))]
    if len(wavs) != 1:
        print(f"✗ audio/ 里的音频不是 1 个: {os.listdir(audio_dir) if os.path.isdir(audio_dir) else '缺目录'}")
        ok = False
    script_path = os.path.join(OUT, "scripts", f"game_logic_{MAIN_SCENE}.gd")
    if os.path.isfile(script_path):
        code = read(script_path)
        if wavs and f'load("res://audio/{wavs[0]}")' not in code:
            print(f"✗ 脚本里 load() 的不是 audio/{wavs[0]}")
            ok = False
        if "ui_image_" not in code:
            print("✗ 脚本里没有图片资产(『界面_图片』没生成)")
            ok = False
        # 曲线资产:『相机_路径』是把曲线**采样成点**写进脚本的
        # (不落 curves/*.tres —— 那是『相机_切换』沿曲线运镜那条路)。
        # 判据:采样点真的生成了(而且是很多点,不是两三个)。
        if "_gk_cam_path: PackedVector3Array" not in code:
            print("✗ 脚本里没有曲线资产(相机_路径 的采样点没生成)")
            ok = False
        else:
            baked = re.search(r"_gk_cam_path: PackedVector3Array = "
                              r"PackedVector3Array\(\[(.*?)\]\)",
                              code, re.S)
            points = baked.group(1).count("Vector3(") if baked else 0
            if points < 20:
                print(f"✗ 曲线采样点太少({points} 个)—— 曲线没被用上?")
                ok = False
    else:
        print(f"✗ 没找到生成的脚本 {script_path}")
        ok = False

    import glob
    art_dir = os.path.join(OUT, "art")
    ui_pngs = glob.glob(os.path.join(art_dir, "ui_*.png"))
    if not ui_pngs:
        print("✗ 图片资产没落盘(art/ui_*.png 不存在)")
        ok = False

    # 资产 glb:第一关 2 个(树 + 石头)、第二关 1 个(树)——
    # 摆三份树也只导一次,这是"一个模型一次"的直接证据
    def asset_glbs(scene_name):
        return sorted(glob.glob(os.path.join(art_dir,
                                             f"{scene_name}_asset_*.glb")))
    first_assets = asset_glbs(MAIN_SCENE)
    second_assets = asset_glbs(SECOND_SCENE)
    if len(first_assets) != 2 or len(second_assets) != 1:
        print(f"✗ 资产 glb 数量不对:第一关 {len(first_assets)} 个、"
              f"第二关 {len(second_assets)} 个")
        ok = False

    # 第一关 .tscn:三个 Model 引用同一条 ext_resource(复用)
    scene_path = os.path.join(OUT, "scenes", f"{MAIN_SCENE}.tscn")
    if os.path.isfile(scene_path):
        scene = read(scene_path)
        for needle, why in (
                ('[node name="树_本体" type="StaticBody3D"', "本体在场景里"),
                ('[node name="树_实例1" type="StaticBody3D"', "实例 1 在场景里"),
                ('[node name="树_实例2" type="StaticBody3D"', "实例 2 在场景里"),
                ('[node name="石头_本体" type="StaticBody3D"', "石头在场景里"),
        ):
            if needle not in scene:
                print(f"✗ 第一关 .tscn 里没有{why}")
                ok = False
        # 树的三份 Model 必须引用同一个 ext_resource id
        ids = re.findall(r'parent="Colliders/树_[^"]*" instance=ExtResource\("([^"]+)"\)',
                         scene)
        if len(ids) != 3 or len(set(ids)) != 1:
            print(f"✗ 三棵树的 Model 引用不是同一个: {ids}")
            ok = False
    else:
        print(f"✗ 没找到第一关场景 {scene_path}")
        ok = False

    # 第二关:树是跨场景摆过来的(有自己的场景文件 + 资产 glb)
    second_path = os.path.join(OUT, "scenes", f"{SECOND_SCENE}.tscn")
    if not os.path.isfile(second_path):
        print(f"✗ 没找到第二关场景 {second_path}(跨场景没导出来?)")
        ok = False
    else:
        second = read(second_path)
        if second.count('name="树_') < 2:
            print("✗ 第二关里没有那两份跨场景摆过来的树")
            ok = False

    print("CHECK_ASSETS_DEMO=" + ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())