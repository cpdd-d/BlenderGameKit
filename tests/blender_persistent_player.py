"""Default player selection, hierarchy export, spawn transforms and disabling."""
import os
from pathlib import Path
import sys
import tempfile
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import BlenderGameKit as addon
from BlenderGameKit import asset_export as assets, project, ui

addon.register()
scene = bpy.context.scene
scene.name = "出生关"
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, -1))
floor = bpy.context.object
floor.name = "地板"
floor.scale = (10, 10, .1)
floor.gk_physics.collision_shape = "box"
bpy.ops.mesh.primitive_cube_add(size=1, location=(3, 2, 1))
player = bpy.context.object
player.name = "自选角色"
player.gk_physics.body_type = "CharacterBody3D"
player.gk_physics.collision_shape = "capsule"
tree = bpy.data.node_groups.new("角色逻辑", "GKGameLogicTree")
tree.gk_scope = "ROLE"
tree.nodes.new("GKNodeOnReady")
player.gk_physics.logic_tree = tree.name
mat = bpy.data.materials.new("玩家材质")
mat.use_nodes = True
player.data.materials.append(mat)
camera = bpy.data.objects.new("角色相机", bpy.data.cameras.new("镜头"))
scene.collection.objects.link(camera)
camera.parent = player
camera.location = (0, -3, 2)
scene.camera = camera
lamp = bpy.data.objects.new("装备灯", bpy.data.lights.new("灯数据", "POINT"))
scene.collection.objects.link(lamp)
lamp.parent = camera
lamp.data.energy = 1000
empty = bpy.data.objects.new("装备挂点", None)
scene.collection.objects.link(empty)
empty.parent = player
empty.location = (.2, .3, .4)
other = bpy.data.scenes.new("目标关")
spawn = bpy.data.objects.new("到达点", None)
other.collection.objects.link(spawn)
spawn.location = (8, 4, 2)
spawn.rotation_euler.z = 1.0
other.gk_settings.player_spawn = spawn
world = ui.world_settings()
world.default_player = player
world.default_map = scene
scene.gk_settings.toon_follow_light = True

with tempfile.TemporaryDirectory(prefix="gk_persistent_") as temp:
    project.create_project(temp)
    project.set_main_scene(temp, "res://scenes/目标关.tscn")
    result = assets.export_all_scenes(temp)
    assert not result["error"], result
    assert result["main_scene"] == "res://scenes/出生关.tscn"
    role = (Path(temp) / "scenes/GKDefaultPlayer.tscn").read_text(encoding="utf-8")
    assert 'name="自选角色"' in role and 'gk_default_player.gd' in role
    assert 'type="OmniLight3D"' in role
    assert 'gk_environment_GKDefaultPlayer.gd' in role
    env = (Path(temp) / "scripts/gk_environment_GKDefaultPlayer.gd").read_text(encoding="utf-8")
    assert "GKPlayer_" in env
    payload = assets.read_glb_payload(str(Path(temp) / "art/GKDefaultPlayer.glb"))
    names = {n.get("name") for n in payload["nodes"]}
    assert {"自选角色", "角色相机", "装备挂点"}.issubset(names), names
    for name in ("出生关", "目标关"):
        level = (Path(temp) / f"scenes/{name}.tscn").read_text(encoding="utf-8")
        assert "CharacterBody3D" not in level and "Camera3D" not in level
        assert "GKPlayerStart" in level
    level = (Path(temp) / "scenes/目标关.tscn").read_text(encoding="utf-8")
    assert "8.00000000, 2.00000000, -4.00000000" in level
    # ── 第一百二十五轮:开局那一关的"跨关卡角色"必须真的能用 ──────────────
    # 用户实测报的 bug:"运行后不能移动与旋转视角" —— 根因是
    # `scene_changed` 在**引擎启动加载第一个场景时**就发了,而 autoload 的
    # `_ready()` 那时才 connect,于是开局这次没人接;而 `_level_ready()` 是
    # **唯一**把玩家从 `PROCESS_MODE_DISABLED` 解回 `PAUSABLE` 的地方。
    # 后果:开局不能用(不动 / 不转视角 / 不在出生点),换关卡之后一切正常。
    # 这里锁住修法:`_ready()` 末尾必须自己再调一次(而且要 deferred,
    # 那一刻 `current_scene` 还没装好)。
    world_script = (Path(temp) / "scripts/gk_world.gd").read_text(encoding="utf-8")
    assert "_level_ready.call_deferred()" in world_script, world_script
    assert "func _ready() -> void:" in world_script
    # 起手那一帧可能还没装好 -> 允许再试一次(有上限,不是每帧扫)
    assert "_level_retried" in world_script, world_script

    # ── 第 125 轮(真机纠正):角色胶囊必须**包住**模型 ─────────────────
    # `CapsuleShape3D.height` 是**总高**,引擎还强制 `height >= 2 * radius`
    # (写 r=0.5 / h=0.8 会被改成 r=0.4 / h=0.8,外形只剩一个 0.8 的球)。
    # 所以导出的胶囊总高应当**等于网格高**,半径 = 最宽边的一半。
    # 第 124 轮我在这儿写反了(减了 2r),用户实测"下半身陷进地板" ——
    # 1.8 米的角色拿到一个 0.8 米的球。这条断言就是钉住这个回归。
    # ⚠ 期望值从**本用例的**网格尺寸算,不写死 1.8 —— 这个 fixture 里的
    #    角色是 size=1 的立方体(高 1.0),而游戏示例里是 1×1×1.8。
    role_scene = (Path(temp) / "scenes/GKDefaultPlayer.tscn").read_text(encoding="utf-8")
    assert "CapsuleShape3D" in role_scene, role_scene[:400]
    import re as _re
    capsule = _re.search(r'CapsuleShape3D[^\[]*?radius = ([\d.]+)\s*\nheight = ([\d.]+)',
                         role_scene)
    assert capsule, role_scene[:600]
    radius, height = float(capsule.group(1)), float(capsule.group(2))
    mesh_height = max(float(v) for v in player.dimensions)
    assert abs(height - mesh_height) < 0.03, (mesh_height, height)
    # 引擎会把半径夹到 height/2 以内,导出侧不该写出会被夹的值
    assert radius <= height * 0.5 + 1e-4, (radius, height)
    world.default_player = None
    result = assets.export_all_scenes(temp)
    assert 'GkWorld=' not in (Path(temp) / "project.godot").read_text(encoding="utf-8")
    assert not (Path(temp) / "scripts/gk_world.gd").exists()
print("PERSISTENT_EXPORT=OK")
