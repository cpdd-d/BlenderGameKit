"""GPU regression: scene-lit toon receives real shadows and more than two lights.

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
var metrics := {}

func brighter_pixels(before: Image, after: Image, threshold: float) -> int:
	var count := 0
	for y in range(before.get_height()):
		for x in range(before.get_width()):
			if after.get_pixel(x, y).get_luminance() - before.get_pixel(x, y).get_luminance() > threshold:
				count += 1
	return count

func patch_energy(img: Image, center: Vector2) -> float:
	var total := 0.0
	for y in range(-3, 4):
		for x in range(-3, 4):
			total += img.get_pixel(int(center.x) + x, int(center.y) + y).get_luminance()
	return total / 49.0

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

func check_three_tone(mat: ShaderMaterial) -> bool:
	mat.set_shader_parameter("three_tone", 1.0)
	lamp.position = Vector3(-2, 5, 2)
	lamp.look_at(Vector3.ZERO)
	lamp.shadow_enabled = true
	var shadow := await shot("three_tone_shadow")
	lamp.shadow_enabled = false
	var clear := await shot("three_tone_no_shadow")
	lamp.visible = false
	var off := await shot("three_tone_off")
	var changed := brighter_pixels(shadow, clear, 0.04)
	var energy := brightness(shadow)
	var off_energy := brightness(off)
	metrics["three_tone"] = {"shadow_pixels": changed, "on_energy": energy, "off_energy": off_energy}
	print("SCENE_LIGHT_THREE_TONE shadow_pixels=", changed, " on=", energy, " off=", off_energy)
	return changed > 100 and energy > 1000.0 and off_energy < energy * 0.01

func check_multiple_lights(world: Node3D, mat: ShaderMaterial, camera: Camera3D) -> bool:
	# The large floor must receive all four lights, so a per-mesh two-light
	# cap cannot accidentally pass. A small second mesh shares its material.
	lamp.visible = false
	box.visible = false
	var right := MeshInstance3D.new()
	var patch := PlaneMesh.new()
	patch.size = Vector2(2, 2)
	right.mesh = patch
	right.material_override = mat
	right.position = Vector3(3, 0.01, 3)
	world.add_child(right)
	camera.projection = Camera3D.PROJECTION_ORTHOGONAL
	camera.size = 13
	camera.position = Vector3(0, 10, 0.01)
	camera.look_at(Vector3.ZERO)
	mat.set_shader_parameter("albedo_color", Color(0.5, 0.5, 0.5))
	var positions := [Vector3(-3, 4, 3), Vector3(3, 4, 3), Vector3(-3, 4, -3), Vector3(3, 4, -3)]
	var colors := [Color(1, 0.3, 0.3), Color(0.3, 1, 0.3), Color(0.3, 0.3, 1), Color(1, 1, 1)]
	var lights: Array[SpotLight3D] = []
	for index in range(positions.size()):
		var light := SpotLight3D.new()
		world.add_child(light)
		light.position = positions[index]
		# Offset target avoids look_at's collinear-up degeneracy.
		light.look_at(Vector3(positions[index].x, 0, positions[index].z + 0.01))
		light.spot_angle = 25
		light.spot_range = 9
		light.light_energy = 2.0
		light.light_color = colors[index]
		light.visible = false
		lights.append(light)
	var ok := true
	for tone in [0, 1]:
		mat.set_shader_parameter("three_tone", float(tone))
		for light in lights:
			light.visible = false
		var before := await shot("multi_" + str(tone) + "_0")
		var base_energy := brightness(before)
		var counts: Array[int] = []
		var energies: Array[float] = []
		for index in range(lights.size()):
			lights[index].visible = true
			var after := await shot("multi_" + str(tone) + "_" + str(index + 1))
			var changed := brighter_pixels(before, after, 0.02)
			counts.append(changed)
			energies.append(brightness(after))
			ok = ok and changed > 500
			before = after
		# Turn off the third light while retaining first, second and fourth.
		# Every other light's pool must stay unchanged on the shared material.
		lights[2].visible = false
		var removed := await shot("multi_" + str(tone) + "_third_off")
		var third_removed := brighter_pixels(removed, before, 0.02)
		var unrelated_change := brighter_pixels(before, removed, 0.02)
		var other_pool_delta := 0.0
		for index in [0, 1, 3]:
			var center := camera.unproject_position(Vector3(positions[index].x, 0, positions[index].z))
			other_pool_delta = maxf(other_pool_delta, absf(patch_energy(before, center) - patch_energy(removed, center)))
		ok = ok and third_removed > 500 and unrelated_change == 0 and base_energy < 1.0 and other_pool_delta < 0.005
		metrics["multi_tone_" + str(tone)] = {"added_light_pixels": counts,
			"energy": energies, "off_energy": base_energy,
			"third_removed_pixels": third_removed, "unrelated_brighter_pixels": unrelated_change,
			"other_pool_max_delta": other_pool_delta}
		print("SCENE_LIGHT_MULTI tone=", tone, " added=", counts,
			" third_removed=", third_removed, " unrelated=", unrelated_change,
			" other_pool_delta=", other_pool_delta, " energy=", energies)
	return ok

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
	metrics["single_light"] = {"shadow_pixels": changed_shadow, "moved_pixels": changed_position,
		"lit_pixels": lit_pixels, "dark_pixels": dark_pixels, "off_energy": brightness(off)}
	var three_tone_ok := await check_three_tone(mat)
	var multi_ok := await check_multiple_lights(world, mat, camera)
	ok = ok and three_tone_ok and multi_ok
	metrics["ok"] = ok
	metrics["engine"] = Engine.get_version_info()["string"]
	metrics["renderer"] = ProjectSettings.get_setting("rendering/renderer/rendering_method")
	var report := FileAccess.open("res://metrics.json", FileAccess.WRITE)
	report.store_string(JSON.stringify(metrics, "  "))
	report.close()
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
    parser.add_argument('--renderer', choices=('gl_compatibility', 'forward_plus', 'mobile'),
                        default='gl_compatibility', help='Actual GPU rendering backend to verify')
    parser.add_argument('--story', action='store_true')
    parser.add_argument('--project', type=Path, default=ROOT / 'examples/demo_story/恐怖闯关示例_godot')
    args = parser.parse_args()
    if args.story:
        OUT = ROOT / '_dist' / 'story_render_probe'
    elif args.renderer != 'gl_compatibility':
        OUT = ROOT / '_dist' / ('scene_light_probe_' + args.renderer)
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / 'BlenderGameKit/shaders/gk_toon_lit.gdshader', OUT)
    (OUT / 'project.godot').write_text(
        'config_version=5\n[application]\nconfig/name="GK Scene Light Probe"\n'
        '[display]\nwindow/size/viewport_width=640\nwindow/size/viewport_height=480\n'
        f'[rendering]\nrenderer/rendering_method="{args.renderer}"\n', encoding='utf-8')
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
         '--rendering-method', args.renderer,
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
