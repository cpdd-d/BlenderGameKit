"""GPU regression: scene-lit toon must receive local light and real shadows.

Runs an isolated Godot project under _dist. A real graphics device is required;
--headless uses a dummy renderer and is not a valid substitute for this check.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '_dist' / 'scene_light_probe'
PROBE = '''extends SceneTree

var floor_mesh: MeshInstance3D
var box: MeshInstance3D
var lamp: SpotLight3D

func shot(label: String) -> Image:
	for i in range(6):
		await process_frame
	await RenderingServer.frame_post_draw
	var img := root.get_texture().get_image()
	img.save_png("res://" + label + ".png")
	return img

func brightness(img: Image) -> float:
	var total := 0.0
	for y in range(img.get_height()):
		for x in range(img.get_width()):
			total += img.get_pixel(x, y).get_luminance()
	return total

func _initialize() -> void:
	call_deferred("run")

func run() -> void:
	var world := Node3D.new()
	root.add_child(world)
	var env_node := WorldEnvironment.new()
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color.BLACK
	env.ambient_light_source = Environment.AMBIENT_SOURCE_DISABLED
	env.reflected_light_source = Environment.REFLECTION_SOURCE_DISABLED
	env_node.environment = env
	world.add_child(env_node)
	var mat := ShaderMaterial.new()
	mat.shader = load("res://gk_toon_lit.gdshader")
	mat.set_shader_parameter("albedo_color", Color(0.65, 0.65, 0.65))
	mat.set_shader_parameter("lit_tint", Color.WHITE)
	floor_mesh = MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(12, 12)
	floor_mesh.mesh = plane
	floor_mesh.material_override = mat
	world.add_child(floor_mesh)
	box = MeshInstance3D.new()
	var block := BoxMesh.new()
	block.size = Vector3(1.2, 1.8, 1.2)
	box.mesh = block
	box.material_override = mat
	world.add_child(box)
	box.position = Vector3(-0.6, 0.9, 0)
	var camera := Camera3D.new()
	world.add_child(camera)
	camera.position = Vector3(0, 9, 9)
	camera.look_at(Vector3.ZERO)
	camera.current = true
	lamp = SpotLight3D.new()
	world.add_child(lamp)
	lamp.position = Vector3(-2, 5, 2)
	lamp.look_at(Vector3.ZERO)
	lamp.spot_angle = 36
	lamp.spot_range = 16
	lamp.light_energy = 8
	lamp.shadow_enabled = true
	lamp.shadow_bias = 0.03
	var shadow := await shot("shadow")
	lamp.shadow_enabled = false
	var no_shadow := await shot("no_shadow")
	lamp.visible = false
	var off := await shot("off")
	lamp.visible = true
	lamp.position.x = 2
	lamp.look_at(Vector3(2, 0, 0))
	var moved := await shot("moved")
	var changed_shadow := 0
	var changed_position := 0
	var lit_pixels := 0
	var dark_pixels := 0
	for y in range(shadow.get_height()):
		for x in range(shadow.get_width()):
			var a := shadow.get_pixel(x, y).get_luminance()
			var b := no_shadow.get_pixel(x, y).get_luminance()
			var c := moved.get_pixel(x, y).get_luminance()
			if b - a > 0.04:
				changed_shadow += 1
			if absf(b - c) > 0.06:
				changed_position += 1
			if a > 0.04:
				lit_pixels += 1
			else:
				dark_pixels += 1
	var ok := changed_shadow > 100 and changed_position > 500 \\
		and lit_pixels > 1000 and dark_pixels > 1000 \\
		and brightness(off) < brightness(shadow) * 0.01
	print("SCENE_LIGHT shadow_pixels=", changed_shadow, " moved_pixels=", changed_position,
		" lit=", lit_pixels, " dark=", dark_pixels, " off=", brightness(off))
	print("SCENE_LIGHT_RENDER=", "OK" if ok else "FAIL")
	quit(0 if ok else 1)
'''

STORY_PROBE = '''extends SceneTree
func _initialize() -> void:
	call_deferred("run")
func run() -> void:
	change_scene_to_file("res://scenes/大堂.tscn")
	await scene_changed
	var world = current_scene
	for i in range(120):
		await physics_frame
	var pawn = root.get_node("GkWorld").player
	Input.action_press("view_top")
	await physics_frame
	await physics_frame
	Input.action_release("view_top")
	await create_timer(4.0).timeout
	await RenderingServer.frame_post_draw
	var off := root.get_texture().get_image()
	off.save_png("res://story_off.png")
	Input.action_press("key_f")
	await physics_frame
	await physics_frame
	Input.action_release("key_f")
	await create_timer(0.4).timeout
	await RenderingServer.frame_post_draw
	var on := root.get_texture().get_image()
	print("RENDER_CAMERA ", root.get_camera_3d().get_path(), " pos=", root.get_camera_3d().global_position, " rot=", root.get_camera_3d().global_rotation)
	print("RENDER_PLAYER ", root.get_node("GkWorld").player.global_position)
	on.save_png("res://story_on.png")
	var changed := 0
	for y in range(130, on.get_height() - 50):
		for x in range(on.get_width()):
			if on.get_pixel(x, y).get_luminance() - off.get_pixel(x, y).get_luminance() > 0.015:
				changed += 1
	var ok := changed > 100 and root.get_camera_3d().name == "第一人称相机"
	print("STORY_LIGHT changed_pixels=", changed)
	print("SCENE_LIGHT_RENDER=", "OK" if ok else "FAIL")
	world.queue_free()
	for i in range(3):
		await process_frame
	quit(0 if ok else 1)
'''


def main():
    global OUT
    parser = argparse.ArgumentParser()
    parser.add_argument('--godot', default=r'D:\godot\Godot_v4.7.2-stable_win64_console.exe')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--story', action='store_true')
    parser.add_argument('--project', type=Path, default=ROOT / 'examples/demo_story/恐怖闯关示例_godot')
    args = parser.parse_args()
    if args.story:
        OUT = ROOT / '_dist' / 'story_render_probe'
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / 'BlenderGameKit/shaders/gk_toon_lit.gdshader', OUT)
    (OUT / 'project.godot').write_text(
        'config_version=5\n[application]\nconfig/name="GK Scene Light Probe"\n'
        '[display]\nwindow/size/viewport_width=640\nwindow/size/viewport_height=480\n'
        '[rendering]\nrenderer/rendering_method="gl_compatibility"\n', encoding='utf-8')
    (OUT / 'probe.gd').write_text(PROBE, encoding='utf-8')
    if args.story:
        shutil.copytree(args.project, OUT,
                        dirs_exist_ok=True, ignore=shutil.ignore_patterns('.godot', '*.tmp'))
        (OUT / 'probe.gd').write_text(STORY_PROBE, encoding='utf-8')
    if args.prepare_only:
        print(OUT)
        return 0
    env = os.environ.copy()
    profile = OUT / 'profile'
    profile.mkdir(exist_ok=True)
    env.update(APPDATA=str(profile), LOCALAPPDATA=str(profile))
    if args.story:
        imported = subprocess.run([args.godot, '--headless', '--path', str(OUT), '--editor', '--quit'],
                                  capture_output=True, timeout=60, env=env)
        if imported.returncode:
            raise RuntimeError(imported.stderr.decode('utf-8', errors='replace'))
    result = subprocess.run(
        [args.godot, '--path', str(OUT), '--script', 'res://probe.gd',
         '--resolution', '1152x648' if args.story else '640x480', '--audio-driver', 'Dummy'],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=60, env=env)
    output = result.stdout + result.stderr
    (OUT / 'render.log').write_text(output, encoding='utf-8')
    print(output)
    return 0 if result.returncode == 0 and 'SCENE_LIGHT_RENDER=OK' in output \
        and 'SHADER ERROR' not in output and 'SCRIPT ERROR' not in output else 1


if __name__ == '__main__':
    raise SystemExit(main())
