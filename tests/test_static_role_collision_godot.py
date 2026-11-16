"""Real Godot regression for animated static ROLE Model/collision ownership.

Runs the actual rendered environment script, not a copy of its sync algorithm.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import gk_stub
gk_stub.install()
gk_stub.load_addon(str(ROOT / "BlenderGameKit"))

try:
    import pytest
except ImportError:
    pass
else:
    pytestmark = pytest.mark.godot

GODOT = os.environ.get("GK_GODOT", r"D:\godot\Godot_v4.7.2-stable_win64_console.exe")

PROBE = '''extends SceneTree

var failures := 0
var checks := 0

func check(value: bool, message: String) -> void:
    checks += 1
    if not value:
        failures += 1
        printerr("STATIC_ROLE_FAIL: " + message)

func make_body(kind: String, name: String, animated: bool = true) -> Dictionary:
    var body := ClassDB.instantiate(kind) as CollisionObject3D
    body.name = name
    body.transform = Transform3D(Basis(Vector3.UP, 0.3), Vector3(2, 1, -3))
    if body is RigidBody3D:
        body.freeze = true
    var first := CollisionShape3D.new()
    first.name = "Collision"
    var shape := BoxShape3D.new()
    shape.size = Vector3(2, 1, 0.4)
    first.shape = shape
    first.transform = Transform3D(Basis(Vector3.FORWARD, 0.2), Vector3(0, 0.5, 0))
    body.add_child(first)
    var second := CollisionShape3D.new()
    second.name = "Collision2"
    second.shape = shape
    second.position = Vector3(1, 1.5, 0)
    body.add_child(second)
    var model := Node3D.new()
    model.name = "Model"
    var authored := Transform3D(Basis(Vector3.UP, 0.2).scaled(Vector3(1.5, 1.5, 1.5)), Vector3(3, 1, -2))
    model.transform = authored.affine_inverse()
    body.add_child(model)
    var pivot := Node3D.new()
    pivot.name = "Pivot"
    pivot.position = Vector3(0.2, 0.4, -0.1)
    model.add_child(pivot)
    var source := MeshInstance3D.new()
    source.name = name
    source.mesh = BoxMesh.new()
    source.transform = authored
    pivot.add_child(source)
    var decoration := MeshInstance3D.new()
    decoration.name = "Decoration"
    decoration.mesh = BoxMesh.new()
    decoration.position = Vector3(0, 2, 0)
    source.add_child(decoration)
    if animated:
        var player := AnimationPlayer.new()
        player.name = "AnimationPlayer"
        model.add_child(player)
        var animation := Animation.new()
        animation.length = 1.0
        var position_track := animation.add_track(Animation.TYPE_POSITION_3D)
        animation.track_set_path(position_track, NodePath("Pivot/" + name + ":position"))
        animation.position_track_insert_key(position_track, 0, authored.origin)
        animation.position_track_insert_key(position_track, 1, authored.origin + Vector3(0, 3, 0))
        var rotation_track := animation.add_track(Animation.TYPE_ROTATION_3D)
        animation.track_set_path(rotation_track, NodePath("Pivot/" + name + ":rotation"))
        animation.rotation_track_insert_key(rotation_track, 0, authored.basis.get_rotation_quaternion())
        animation.rotation_track_insert_key(rotation_track, 1, Quaternion(Vector3.UP, 0.8))
        var library := AnimationLibrary.new()
        library.add_animation("Open", animation)
        player.add_animation_library("", library)
    return {"body": body, "source": source, "first": first, "second": second,
        "body_rest": body.transform, "first_rest": first.transform,
        "second_rest": second.transform, "source_rest": source.transform, "model": model}

func _initialize() -> void:
    call_deferred("run_probe")

func run_probe() -> void:
    var world := Node3D.new()
    world.name = "World"
    var holder := Node3D.new()
    holder.name = "Colliders"
    world.add_child(holder)
    var door := make_body("StaticBody3D", "Door")
    var fixed := make_body("StaticBody3D", "Fixed", false)
    var character := make_body("CharacterBody3D", "Character")
    var rigid := make_body("RigidBody3D", "Rigid")
    var animatable := make_body("AnimatableBody3D", "Animatable")
    var auto := make_body("StaticBody3D", "AutoDoor")
    auto.body.set_script(load("res://auto.gd"))
    for item in [door, fixed, character, rigid, animatable, auto]:
        holder.add_child(item.body)
    var environment := Node.new()
    environment.set_script(load("res://environment.gd"))
    world.add_child(environment)
    root.add_child(world)
    current_scene = world
    environment.set_physics_process(false)
    check(environment._gk_model_shape_links.size() == 4, "bind exactly two shapes of each static animated role")
    var source_rest: Transform3D = door.body.global_transform.affine_inverse() * door.source.global_transform
    var auto_rest: Transform3D = auto.body.global_transform.affine_inverse() * auto.source.global_transform
    for i in range(3):
        environment._gk_link_colliders()
        environment._gk_sync_colliders()
    check(environment._gk_model_shape_links.size() == 4, "relink must be idempotent")
    check(door.first.transform.is_equal_approx(door.first_rest), "rest pose moved first offset")
    check(door.second.transform.is_equal_approx(door.second_rest), "rest pose moved second offset")
    check(door.body.transform.is_equal_approx(door.body_rest), "rest pose moved owner body")

    var player: AnimationPlayer = door.model.get_node("AnimationPlayer")
    player.play("Open")
    player.advance(0.6)
    environment._gk_sync_colliders()
    var current: Transform3D = door.body.global_transform.affine_inverse() * door.source.global_transform
    var delta: Transform3D = current * source_rest.affine_inverse()
    check(door.first.transform.is_equal_approx(delta * door.first_rest), "position and rotation missed first shape")
    check(door.second.transform.is_equal_approx(delta * door.second_rest), "position and rotation missed second shape")
    check(not door.first.transform.is_equal_approx(door.first_rest), "animation did not move collision")
    check(door.body.transform.is_equal_approx(door.body_rest), "animation double-moved owner body")
    check(door.first.shape.size.is_equal_approx(Vector3(2, 1, 0.4)), "shape resource dimensions changed")
    check(door.first.shape == door.second.shape, "shared shape resource should remain shared")
    player.pause()

    # A second environment added after motion must use the original bind.
    var environment2 := Node.new()
    environment2.set_script(load("res://environment.gd"))
    world.add_child(environment2)
    environment2.set_physics_process(false)
    var pose: Transform3D = door.first.transform
    for i in range(5):
        environment._gk_sync_colliders()
        environment2._gk_sync_colliders()
    check(door.first.transform.is_equal_approx(pose), "two environments accumulated the delta")
    door.body.position += Vector3(4, 0, 1)
    environment._gk_sync_colliders()
    check(door.first.transform.is_equal_approx(pose), "body motion applied twice to shape")
    player.play("Open")
    player.seek(0, true)
    player.pause()
    environment2._gk_sync_colliders()
    check(door.first.transform.is_equal_approx(door.first_rest), "return to rest missed original offset")
    check(door.second.transform.is_equal_approx(door.second_rest), "return to rest missed second shape")

    for item in [fixed, character, rigid, animatable]:
        item.source.position += Vector3(0, 3, 0)
    environment._gk_sync_colliders()
    for item in [fixed, character, rigid, animatable]:
        check(item.first.transform.is_equal_approx(item.first_rest), "non-static/unanime role changed: " + str(item.body.name))

    await create_timer(0.15).timeout
    environment._gk_sync_colliders()
    var auto_now: Transform3D = auto.body.global_transform.affine_inverse() * auto.source.global_transform
    check(not auto_now.is_equal_approx(auto_rest), "_ready play never advanced")
    check(auto.first.transform.is_equal_approx(auto_now * auto_rest.affine_inverse() * auto.first_rest), "_ready animation captured a shifted rest pose")
    check(auto.body.transform.is_equal_approx(auto.body_rest), "_ready animation moved owner")
    door.source.queue_free()
    await process_frame
    environment._gk_sync_colliders()
    environment2._gk_sync_colliders()
    world.queue_free()
    await process_frame
    if failures == 0:
        print("STATIC_ROLE_COLLISION=OK checks=", checks)
    quit(1 if failures else 0)
'''


@unittest.skipUnless(os.path.isfile(GODOT), "Godot unavailable: " + GODOT)
class StaticRoleCollisionTests(unittest.TestCase):
    def test_relative_animation_and_owner_motion(self):
        from BlenderGameKit import asset_export
        with tempfile.TemporaryDirectory(prefix="gk_static_role_") as directory:
            root = Path(directory)
            (root / "project.godot").write_text('config_version=5\n', encoding="utf-8")
            (root / "environment.gd").write_text(asset_export.render_env_script(), encoding="utf-8")
            (root / "probe.gd").write_text(PROBE, encoding="utf-8")
            (root / "auto.gd").write_text(
                'extends StaticBody3D\nfunc _ready() -> void:\n'
                '    $Model/AnimationPlayer.play("Open")\n', encoding="utf-8")
            env = dict(os.environ)
            for key in ("APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME"):
                folder = root / key.lower()
                folder.mkdir()
                env[key] = str(folder)
            proc = subprocess.run([GODOT, "--headless", "--path", str(root),
                                   "--script", "res://probe.gd"], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace",
                                  env=env, timeout=30)
            output = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 0, output)
            self.assertNotIn("ERROR:", output)
            self.assertIn("STATIC_ROLE_COLLISION=OK", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
