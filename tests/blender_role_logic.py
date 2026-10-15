"""Blender integration check for a scene with only a character logic graph."""

import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import bpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import BlenderGameKit as addon
from BlenderGameKit import asset_export, exporter, project, ui


def check_godot(directory):
    executable = os.environ.get("GK_GODOT", r"D:\godot\Godot_v4.7.2-stable_win64_console.exe")
    if not os.path.isfile(executable):
        return
    env = os.environ.copy()
    config_dir = os.path.join(directory, "godot_config")
    os.makedirs(config_dir, exist_ok=True)
    env["APPDATA"] = config_dir
    env["LOCALAPPDATA"] = config_dir
    result = subprocess.run(
        [executable, "--headless", "--editor", "--path", directory, "--quit"],
        capture_output=True, text=True, timeout=45, check=False, env=env)
    output = result.stdout + result.stderr
    errors = [line for line in output.splitlines()
              if line.startswith("ERROR:")
              and "Failed to read the root certificate store" not in line]
    assert result.returncode == 0 and not errors, output


def main():
    addon.register()
    try:
        scene = bpy.context.scene
        scene.name = "角色测试"
        for obj in list(scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

        mesh = bpy.data.meshes.new("角色网格")
        mesh.from_pydata([(-1, 0, 0), (1, 0, 0), (0, 0, 1)], [], [(0, 1, 2)])
        obj = bpy.data.objects.new("玩家", mesh)
        scene.collection.objects.link(obj)
        obj.gk_physics.enabled = True
        obj.gk_physics.script_target = True

        space = SimpleNamespace(tree_type="GKGameLogicTree", pin=False,
                                node_tree=None)
        context = SimpleNamespace(scene=scene, object=obj,
                                  area=SimpleNamespace(type="NODE_EDITOR"),
                                  space_data=space)
        assert ui.GK_OT_NewLogicTree.execute(
            SimpleNamespace(scope="ROLE"), context) == {"FINISHED"}
        role = space.node_tree
        assert role.gk_scope == "ROLE"
        assert obj.gk_physics.logic_tree == role.name
        role.nodes.new("GKNodeOnReady")
        assert exporter.resolve_scene_tree(scene)[0] is None
        assert exporter.object_logic_trees(scene)[0][1] is role

        with tempfile.TemporaryDirectory(prefix="gk_role_logic_") as directory:
            project.create_project(directory)
            result = asset_export.export_all_scenes(directory)
            assert result["error"] is None, result
            assert result["scenes"][0]["ok"], result["scenes"]
            script = result["scenes"][0]["script_name"]
            assert script, result["scenes"]
            assert os.path.isfile(os.path.join(directory, "scripts", script))
            with open(result["scenes"][0]["tscn"], encoding="utf-8") as handle:
                scene_text = handle.read()
            assert f"res://scripts/{script}" in scene_text, scene_text
            check_godot(directory)

        level = bpy.data.node_groups.new("关卡逻辑", "GKGameLogicTree")
        level.gk_scope = "LEVEL"
        level.nodes.new("GKNodeOnReady")
        scene.gk_settings.logic_tree = level.name
        obj.gk_physics.logic_tree = ""
        obj.gk_physics.script_target = False
        with tempfile.TemporaryDirectory(prefix="gk_level_logic_") as directory:
            project.create_project(directory)
            result = asset_export.export_all_scenes(directory)
            assert result["error"] is None, result
            assert result["scenes"][0]["ok"], result["scenes"]
            level_script = result["scenes"][0]["script_name"]
            with open(result["scenes"][0]["tscn"], encoding="utf-8") as handle:
                scene_text = handle.read()
            assert f"res://scripts/{level_script}" in scene_text, scene_text
            with open(os.path.join(directory, "scripts", level_script),
                      encoding="utf-8") as handle:
                assert "extends Node3D" in handle.read()
            check_godot(directory)

        with tempfile.TemporaryDirectory(prefix="gk_chinese_names_") as directory:
            first = os.path.join(directory, "恐怖游戏.blend")
            second = os.path.join(directory, "恐怖游戏续作.blend")
            bpy.ops.wm.save_as_mainfile(filepath=first)
            first_project, _ = project.resolve(scene, adopt=False)
            bpy.ops.wm.save_as_mainfile(filepath=second)
            second_project, _ = project.resolve(scene, adopt=False)
            assert first_project == os.path.join(directory, "恐怖游戏_godot")
            assert second_project == os.path.join(directory, "恐怖游戏续作_godot")
            assert first_project != second_project
        print("ROLE_LOGIC=OK")
    finally:
        addon.unregister()


main()
