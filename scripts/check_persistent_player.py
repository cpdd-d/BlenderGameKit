"""Exercise real SceneTree scene changes, preserving the actual player instance."""
import argparse
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PROBE = '''extends SceneTree
var failed := false
func check(value: bool, label: String) -> void:
    print("PLAYER_CHECK ", label, "=", value)
    failed = failed or not value
func _initialize() -> void:
    call_deferred("run")
func run() -> void:
    var manager = root.get_node("GkWorld")
    change_scene_to_file("res://scenes/大堂.tscn")
    await scene_changed
    for i in range(10):
        await physics_frame
    var player: CharacterBody3D = manager.player
    var identity: int = player.get_instance_id()
    var spawn_position: Vector3 = current_scene.get_node("GKPlayerStart").global_position
    for i in range(90):
        await physics_frame
    check(player.global_position.y > 0.3 and player.global_position.y < 2.0 and player.is_on_floor(), "standing_on_floor")
    check(Vector2(player.global_position.x, player.global_position.z).distance_to(Vector2(spawn_position.x, spawn_position.z)) < 0.2, "spawn_not_pushed_by_helpers")
    var cameras := player.find_children("*", "Camera3D", true, false)
    print("CAMERA_PATHS ", root.find_children("*", "Camera3D", true, false))
    check(cameras.size() == 2, "two_owned_cameras")
    var camera_id: int = cameras[0].get_instance_id()
    var lamp := player.find_child("手电筒", true, false) as Light3D
    var lamp_id := lamp.get_instance_id()
    var item := Node3D.new()
    item.name = "RuntimeInventory"
    player.add_child(item)
    item.position = Vector3(0.2, 0.3, 0.4)
    var item_id := item.get_instance_id()
    player.set("手电开", true)
    player.set("手电电量", 63.0)
    cameras[0].make_current()
    for level in ["走廊", "结局", "大堂", "走廊", "大堂"]:
        paused = false
        manager.call_deferred("change_level", "res://scenes/" + level + ".tscn")
        await scene_changed
        check(player.get_instance_id() == identity, level + "_same_player")
        check(player.global_position.distance_to(current_scene.get_node("GKPlayerStart").global_position) < 0.01,
              level + "_spawn")
        check(player.velocity.length() < 0.01, level + "_velocity_reset")
        check(item.get_instance_id() == item_id and item.position.is_equal_approx(Vector3(.2, .3, .4)), level + "_child_state")
        check(lamp.get_instance_id() == lamp_id, level + "_same_flashlight")
        check(root.get_camera_3d().get_instance_id() == camera_id, level + "_same_camera")
        check(get_nodes_in_group("gk_player").size() == 1, level + "_one_player")
        # ── 第 125 轮:这里必须**排除上帝机**,不能硬数 2 ──────────────────
        # 用户原话:"运行后我上帝机为什么用不了了？" —— 上一轮把示例的
        # `god_camera` 打开之后,每张 .tscn 的环境脚本都会在 `/root` 下挂
        # **一台**上帝机(故意挂 root 上,这样切关卡不会被清掉 ——
        # 见 asset_export.ENVIRONMENT_SCRIPT 里 `_gk_god_setup` 的说明)。
        # 于是全树里有 3 台:角色自己的两台 + 上帝机一台。
        # 以前这条硬数 2,是因为那时候示例里上帝机是关着的 —— 换句话说
        # **它测的是"示例的设置",不是"有没有重复"**。
        # 现在按**分组**判断(不能按名字:用户可能改节点名,
        # 见 `_gk_god_setup` 里"不能用名字判断"那段):
        #   非上帝机的相机必须还是 2 台(没被场景重载堆出来),
        #   上帝机必须且只有 1 台(重载能被找回来,不是每切一次多一台)。
        var game_cameras := 0
        var god_cameras := 0
        for found in root.find_children("*", "Camera3D", true, false):
            if (found as Camera3D).is_in_group("gk_god_camera"):
                god_cameras += 1
            else:
                game_cameras += 1
        check(game_cameras == 2, level + "_no_duplicate_cameras")
        check(god_cameras == 1, level + "_one_god_camera")
        for i in range(5):
            await physics_frame
        check(bool(player.get("手电开")) and player.get("手电电量") < 63.0 and player.get("手电电量") > 60.0,
              level + "_battery_preserved")
        check(lamp.visible, level + "_flashlight_works")
        check(player.is_ancestor_of(cameras[0]) and player.is_ancestor_of(lamp), level + "_hierarchy")
    var before := current_scene
    manager.change_level("res://scenes/nonexistent.tscn")
    check(current_scene == before and not manager.changing, "invalid_destination_keeps_level")
    check(root.msaa_3d == Viewport.MSAA_4X, "startup_antialiasing")
    print("PERSISTENT_PLAYER=", "FAIL" if failed else "OK")
    quit(1 if failed else 0)
'''


def run(directory, godot):
    directory = Path(directory).resolve()
    (directory / "persistent_probe.gd").write_text(PROBE, encoding="utf-8")
    env = os.environ.copy()
    env["APPDATA"] = str(ROOT / "_dist/persistent_profile")
    env["LOCALAPPDATA"] = env["APPDATA"]
    imported = subprocess.run([godot, "--headless", "--editor", "--path", str(directory), "--quit"],
                              capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    result = subprocess.run([godot, "--headless", "--path", str(directory), "--script", "res://persistent_probe.gd", "--quit-after", "2000"],
                            capture_output=True, text=True, encoding="utf-8", env=env, timeout=60)
    output = result.stdout + result.stderr
    (ROOT / "_dist/persistent_runtime.log").write_text(imported.stdout + imported.stderr + output, encoding="utf-8")
    print(output)
    assert result.returncode == 0 and "PERSISTENT_PLAYER=OK" in output, "See _dist/persistent_runtime.log"
    assert "SCRIPT ERROR" not in output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=str(ROOT / "examples/demo_story/恐怖闯关示例_godot"))
    parser.add_argument("--godot", default=r"D:\godot\Godot_v4.7.2-stable_win64_console.exe")
    args = parser.parse_args()
    run(args.project, args.godot)
