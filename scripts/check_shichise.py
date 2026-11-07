"""《七色》· 赤之领域（M1 灰盒）自检。

用法:
    python scripts\\check_shichise.py

做两件事:
  1. 静态检查 —— 生成物在不在、主场景指没指对、三个自定义脚本导没导出去;
  2. 真 Godot 无头跑一遍完整链路 —— 对话拿火把 → 点三座火盆 → 塔门升起
     → 砍敌人 → 上塔顶触发 BOSS → 击杀 → 赤色回归(饱和度 1.5)。

没有装 Godot 时只做静态检查,并打印 SKIP(不判红)。

⚠ 这个探针会**删掉**开发用的 user://gk_shichise.json(开始和结束各一次),
   不会污染你自己的存档。要在 Godot 里手动玩,请先跑 make_shichise.py,
   然后按 F5 —— 不要跑这个自检。
"""

import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GAME_DIR = os.path.join(ROOT, "games", "七色")
GODOT_DIR = os.path.join(GAME_DIR, "七色_godot")
BLEND_PATH = os.path.join(GAME_DIR, "七色.blend")
PROBE_NAME = "_gk_check_gameplay.gd"

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}"
          + (f" - {detail}" if detail and not ok else ""))


def find_godot():
    for candidate in (
            os.environ.get("GODOT", ""),
            shutil.which("godot") or "",
            shutil.which("godot4") or "",
            os.path.join("D:\\godot", "Godot_v4.7.2-stable_win64_console.exe"),
            os.path.join("C:\\godot", "Godot_v4.7.2-stable_win64_console.exe"),
    ):
        if candidate and os.path.isfile(candidate):
            return candidate
    return ""


PROBE = r'''extends SceneTree

var frames := 0
var started := false


func _init() -> void:
	var save_path := ProjectSettings.globalize_path("user://gk_shichise.json")
	if FileAccess.file_exists(save_path):
		DirAccess.remove_absolute(save_path)
	var packed: PackedScene = load("res://scenes/World.tscn")
	if packed == null:
		print("CHECK_DONE=FAIL load")
		quit(1)
		return
	root.add_child(packed.instantiate())


func _process(_delta: float) -> bool:
	frames += 1
	if frames == 180 and not started:
		started = true
		_run()
	return false


func _finish(code: int) -> void:
	var save_path := ProjectSettings.globalize_path("user://gk_shichise.json")
	if FileAccess.file_exists(save_path):
		DirAccess.remove_absolute(save_path)
	quit(code)


func _run() -> void:
	var scene := root.get_node("World")
	var world: Node = null
	var manager: Node = null
	for child in scene.get_children():
		if child.name.begins_with("GKScript_"):
			if child.has_method("restore_red"):
				world = child
			elif child.has_method("hit_enemies_in_cone"):
				manager = child
	var player := scene.get_node_or_null("Colliders/艾尔文") as CharacterBody3D
	var script: Node = null
	if player != null:
		for child in player.get_children():
			if child.name.begins_with("GKScript_"):
				script = child
	print("CHECK_LOADED=" + str(world != null) + "," + str(manager != null)
			+ "," + str(script != null))
	if world == null or manager == null or script == null:
		print("CHECK_DONE=FAIL scripts")
		_finish(2)
		return

	world._talk_lia()
	for i in range(4):
		world._advance_dialogue()
	await process_frame
	print("CHECK_TORCH=" + str(world.has_torch))

	for i in range(1, 4):
		world._light_brazier(i)
	print("CHECK_BRAZIERS=" + str(world.braziers_lit))
	await create_timer(1.6).timeout
	var gate := scene.get_node_or_null("Colliders/赤塔门") as Node3D
	print("CHECK_GATE_Y=" + str(gate.position.y if gate else -1.0))

	player.global_position = Vector3(38.0, 1.1, -15.0)
	await physics_frame
	await physics_frame
	var target: Dictionary = {}
	for data in manager.enemies:
		if data.node.name == "敌人_边境1":
			target = data
			break
	if target.is_empty():
		print("CHECK_ENEMY=FAIL")
	else:
		var before: float = target.hp
		manager.hit_enemies_in_cone(
				target.node.global_position + Vector3(0, 0, -2),
				Vector3(0, 0, 1), 3.0, 90.0, 20.0)
		print("CHECK_ENEMY_HP=" + str(before) + "->" + str(target.hp))

	player.global_position = Vector3(-65.0, 25.5, -82.0)
	await create_timer(0.4).timeout
	print("CHECK_BOSS_ACTIVE=" + str(manager.boss_active))
	manager._damage_enemy(manager.boss_data, 999.0)
	await create_timer(4.8).timeout
	var we := scene.get_node_or_null("WorldEnvironment") as WorldEnvironment
	var sat := -1.0
	if we != null and we.environment != null:
		sat = we.environment.adjustment_saturation
	print("CHECK_RED=" + str(world.red_restored) + " sat=" + str(sat))
	print("CHECK_DONE=OK")
	_finish(0)
'''


def static_checks():
    check("游戏目录存在", os.path.isdir(GAME_DIR), GAME_DIR)
    check("七色.blend 存在", os.path.isfile(BLEND_PATH), BLEND_PATH)
    check("Godot 工程存在", os.path.isfile(os.path.join(GODOT_DIR, "project.godot")))
    scene_path = os.path.join(GODOT_DIR, "scenes", "World.tscn")
    check("主场景 World.tscn 存在", os.path.isfile(scene_path))
    if os.path.isfile(scene_path):
        with open(scene_path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        check("场景里有玩家", "name=\"艾尔文\"" in text)
        check("场景里有 10 个敌人", text.count("敌人_") >= 10, str(text.count("敌人_")))
        check("场景里有 BOSS", "赤之骑士团长" in text)
        check("场景里有塔门", "赤塔门" in text)
        check("场景里有环境(饱和度可调)", "adjustment_saturation" in text)
    project_path = os.path.join(GODOT_DIR, "project.godot")
    if os.path.isfile(project_path):
        with open(project_path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        check("主场景已指到 World", "res://scenes/World.tscn" in text)
    custom = os.path.join(GODOT_DIR, "scripts", "custom")
    world_files = glob.glob(os.path.join(custom, "shichise_world_*.gd"))
    enemy_files = glob.glob(os.path.join(custom, "shichise_enemy_*.gd"))
    player_files = glob.glob(os.path.join(custom, "shichise_player_*.gd"))
    check("世界脚本已导出", bool(world_files), str(world_files))
    check("敌人脚本已导出", bool(enemy_files), str(enemy_files))
    check("玩家脚本已导出", bool(player_files), str(player_files))
    if world_files:
        text = open(world_files[0], "r", encoding="utf-8", errors="replace").read()
        check("世界脚本有 restore_red", "func restore_red" in text)
        check("世界脚本有火盆逻辑", "func _light_brazier" in text)
        check("世界脚本有检查点", "func _check_campfires" in text)
        check("世界脚本有存档", "func save_game" in text)
    if enemy_files:
        text = open(enemy_files[0], "r", encoding="utf-8", errors="replace").read()
        check("敌人脚本有追击/攻击", "func _tick_enemy" in text)
        check("敌人脚本有色核", "core_alive" in text)
        check("敌人脚本有 BOSS 阶段", "next_phase" in text)
    if player_files:
        text = open(player_files[0], "r", encoding="utf-8", errors="replace").read()
        check("玩家脚本有三连击", "func _start_attack" in text)
        check("玩家脚本有闪避", "func _start_dash" in text)
        check("玩家脚本有色技", "func _use_skill" in text)


def run_godot_check(godot):
    probe_path = os.path.join(GODOT_DIR, PROBE_NAME)
    with open(probe_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE)
    try:
        result = subprocess.run(
            [godot, "--headless", "--path", GODOT_DIR,
             "--script", f"res://{PROBE_NAME}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300,
        )
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        for line in output.splitlines():
            if line.startswith("CHECK_"):
                print("    " + line.strip())
        check("Godot 进程正常退出", result.returncode == 0,
              f"exit={result.returncode}")
        check("三个脚本都加载", "CHECK_LOADED=true,true,true" in output)
        check("对话后拿到火把", "CHECK_TORCH=true" in output)
        check("三座火盆点亮", "CHECK_BRAZIERS=3" in output)
        gate_ok = False
        for line in output.splitlines():
            if line.startswith("CHECK_GATE_Y="):
                try:
                    gate_ok = float(line.split("=", 1)[1]) > 10.0
                except ValueError:
                    gate_ok = False
        check("塔门升起", gate_ok)
        check("敌人掉血", "CHECK_ENEMY_HP=60.0->40.0" in output)
        check("BOSS 现身", "CHECK_BOSS_ACTIVE=true" in output)
        check("赤色回归", "CHECK_RED=true" in output)
        sat_ok = False
        for line in output.splitlines():
            if line.startswith("CHECK_RED=") and " sat=" in line:
                try:
                    sat_ok = float(line.rsplit(" sat=", 1)[1]) >= 1.4
                except ValueError:
                    sat_ok = False
        check("饱和度推到 1.5", sat_ok)
        check("探针跑完", "CHECK_DONE=OK" in output)
    finally:
        if os.path.isfile(probe_path):
            os.remove(probe_path)


def main():
    static_checks()
    godot = find_godot()
    if not godot:
        print("[SKIP] 没找到 Godot —— 只做了静态检查(不判红)")
    else:
        print(f"[INFO] Godot: {godot}")
        run_godot_check(godot)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [name for name, ok, _ in RESULTS if not ok]
    print("-" * 72)
    print(f"结果: {passed}/{len(RESULTS)} 通过")
    if failed:
        print("失败项: " + ", ".join(failed))
    print("CHECK_SHICHISE=" + ("OK" if not failed else "FAILED"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
