"""Regression coverage for reversible Godot preview light data.

Run: blender --background --factory-startup --python-exit-code 1
             --python tests/blender_godot_lights.py
"""
import math
from pathlib import Path
import re
import sys

import addon_utils
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def close(actual, expected, tolerance=1e-5):
    assert abs(actual - expected) < tolerance, (actual, expected)


def close_color(actual, expected):
    assert max(abs(a - b) for a, b in zip(actual, expected)) < 1e-6, (tuple(actual), expected)


def main():
    addon_utils.enable('BlenderGameKit', default_set=False, persistent=False)
    from BlenderGameKit import godot_look as look, asset_export as export
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    source = bpy.data.lights.new('Shared authored light', 'POINT')
    source.energy = 2300
    source.color = (.8, .4, .2)
    source.use_shadow = True
    source.use_custom_distance = False
    source.cutoff_distance = 7.5
    source.shadow_soft_size = .23
    a = bpy.data.objects.new('Warm override', source)
    b = bpy.data.objects.new('Blue override', source)
    native = bpy.data.objects.new('Native values', source)
    for obj in (a, b, native):
        scene.collection.objects.link(obj)
    for obj, energy, color, distance, shadow in (
            (a, 4., (1., .45, .13, 1.), 12., False),
            (b, 1.5, (.15, .45, 1., 1.), 4., True)):
        cfg = obj.gk_light
        cfg.override = True
        cfg.energy = energy
        cfg.use_color = True
        cfg.color = color
        cfg.range_m = distance
        cfg.shadow = shadow
    scene.gk_settings.preview_light_scale = .06
    scene.gk_settings.preview_scene_lights = True
    original = dict(energy=source.energy, color=tuple(source.color),
                    use_shadow=source.use_shadow, cutoff_distance=source.cutoff_distance,
                    use_custom_distance=source.use_custom_distance)
    before = {obj.name: export._light_node_text(obj) for obj in (a, b, native)}
    base_count = len(bpy.data.lights)

    look._dim_lights(scene, force=True)
    assert len({obj.data.as_pointer() for obj in (a, b, native)}) == 3
    assert all(obj.data != source and look.authored_light_data(obj) == source
               for obj in (a, b, native))
    assert source.energy == original['energy']
    close_color(source.color, original['color'])
    assert source.use_custom_distance == original['use_custom_distance']
    for obj in (a, b, native):
        text = export._light_node_text(obj)
        assert text == before[obj.name], ('Preview changed export', obj.name)
        srgb = re.search(r'light_color = Color\(([^)]+)\)', text)
        assert srgb
        exported = [float(value) for value in srgb.group(1).split(',')[:3]]
        # Compare against the actual export rather than only against settings.
        close_color(obj.data.color, tuple(look.srgb_to_linear(c) for c in exported))
        assert obj.data.use_custom_distance
        radius = re.search(r'omni_range = ([0-9.]+)', text)
        close(obj.data.cutoff_distance, float(radius.group(1)))
        assert obj.data.use_shadow == ('shadow_enabled = true' in text)
    close(a.data.energy, 240.)
    close(b.data.energy, 90.)
    close(native.data.energy, 138.)
    previews = {obj.name: obj.data for obj in (a, b, native)}
    for _ in range(3):
        look._dim_lights(scene, force=True)
    assert len(bpy.data.lights) == base_count + 3
    assert all(obj.data == previews[obj.name] for obj in (a, b, native))
    close(a.data.energy, 240.)
    print('LIGHTS: independent linked data, colors/ranges/shadows, stable export and repeated apply passed')

    signature = look._env_signature(scene)
    a.gk_light.color = (.9, .5, .2, 1.)
    assert look._env_signature(scene) != signature
    signature = look._env_signature(scene)
    source.energy = 3000.
    assert look._env_signature(scene) != signature
    look._dim_lights(scene, force=True)
    close(native.data.energy, 180.)
    close(export._light_energy_for(native), 3.)
    # Native and override edits must refresh every copy independently.
    source.color = (.2, .5, .7)
    b.gk_light.range_m = 9.
    b.gk_light.shadow = False
    look._dim_lights(scene, force=True)
    close_color(native.data.color, tuple(look.srgb_to_linear(c) for c in source.color))
    close(b.data.cutoff_distance, 9.)
    assert not b.data.use_shadow and native.data.use_shadow
    print('LIGHTS: settings and authored data edits change fingerprint and refresh passed')

    # An override disabled while previewing must fall back to the source.
    a.gk_light.override = False
    look._dim_lights(scene, force=True)
    close(a.data.energy, 180.)
    close_color(a.data.color, tuple(look.srgb_to_linear(c) for c in source.color))
    close(a.data.cutoff_distance, source.cutoff_distance)
    assert a.data.use_shadow == source.use_shadow
    look._undim_lights(scene)
    look._undim_lights(scene)
    assert all(obj.data == source for obj in (a, b, native))
    assert not any(light.get(look._LIGHT_COPY_TAG) for light in bpy.data.lights)
    assert len(bpy.data.lights) == base_count
    print('LIGHTS: override disable and idempotent restore passed')

    # Saving must clean previews in every scene, including shared object links.
    other = bpy.data.scenes.new('Other preview scene')
    other.collection.objects.link(a)
    spot_source = bpy.data.lights.new('Authored spot', 'SPOT')
    spot_source.energy = 800.
    spot_source.spot_size = math.radians(64)
    spot = bpy.data.objects.new('Spot object', spot_source)
    other.collection.objects.link(spot)
    second_before = export._light_node_text(spot)
    look._dim_lights(scene, force=True)
    look._dim_lights(other, force=True)
    assert export._light_node_text(spot) == second_before
    look._undim_all(None, None)
    assert spot.data == spot_source and all(obj.data == source for obj in (a, b, native))
    assert export._light_node_text(spot) == second_before
    assert not any(light.get(look._LIGHT_COPY_TAG) for light in bpy.data.lights)
    assert all(look._LIGHT_SOURCE_KEY not in obj and look._LIGHT_ENERGY_KEY not in obj
               for obj in (a, b, native, spot))
    print('LIGHTS: save-pre restores all authored data and removes copies passed')

    # Full apply/restore must recover an absent World and existing curve mapping.
    scene.world = None
    scene.view_settings.use_curve_mapping = True
    scene.view_settings.curve_mapping.initialize()
    curve = scene.view_settings.curve_mapping.curves[3]
    curve.points.new(.3, .2)
    curve_points = [tuple(point.location) for point in curve.points]
    before = {obj.name: export._light_node_text(obj) for obj in (a, b, native)}
    assert look.apply(scene)[0]
    look._dim_lights(scene, force=True)
    assert scene.world is not None and not scene.view_settings.use_curve_mapping
    scene.gk_settings.preview_scene_lights = False
    assert look.apply(scene)[0]
    assert all(obj.data == source for obj in (a, b, native))
    assert look.restore(scene)[0]
    assert scene.world is None and scene.view_settings.use_curve_mapping
    assert [tuple(point.location) for point in curve.points] == curve_points
    assert {obj.name: export._light_node_text(obj) for obj in (a, b, native)} == before
    assert not any(light.get(look._LIGHT_COPY_TAG) for light in bpy.data.lights)
    print('LIGHTS: preview toggle, absent World, curve and unchanged export passed')

    # Native-panel edits target obj.data, which is currently the preview copy.
    # Capture them before fingerprints/export and invert the *previous* scale.
    edit_source = bpy.data.lights.new('Native panel source', 'SPOT')
    edit_source.energy = 1000.
    edit_source.use_fake_user = True
    edit = bpy.data.objects.new('Native panel edit', edit_source)
    scene.collection.objects.link(edit)
    scene.gk_settings.preview_scene_lights = True
    look._dim_lights(scene, force=True)
    signature = look._env_signature(scene)
    edit.data.energy = 180.
    edit.data.color = (.04, .16, .36)
    edit.data.spot_size = math.radians(80)
    edit.data.shadow_soft_size = .65
    edit.data.cutoff_distance = 13.
    edit.data.use_shadow = False
    assert look._env_signature(scene) != signature
    close(edit_source.energy, 3000., .001)
    close_color(edit_source.color, tuple(look.linear_to_srgb(c) for c in (.04, .16, .36)))
    close(edit_source.spot_size, math.radians(80))
    close(edit_source.shadow_soft_size, .65)
    close(edit_source.cutoff_distance, 13.)
    assert not edit_source.use_shadow
    captured = export._light_node_text(edit)
    assert 'light_energy = 3.000' in captured and 'spot_range = 13.000' in captured
    for _ in range(3):
        look._dim_lights(scene, force=True)
    close(edit.data.energy, 180., .001)
    close(edit_source.energy, 3000., .001)
    assert export._light_node_text(edit) == captured
    edit.data.energy = 240.
    # Export can be invoked before the 0.35s change watcher.
    immediate = export._light_node_text(edit)
    assert 'light_energy = 4.000' in immediate
    close(edit_source.energy, 4000., .001)
    # Edits under an override are retained in source data but the override
    # remains authoritative until explicitly switched off.
    edit.gk_light.override = True
    edit.gk_light.energy = 2.
    edit.gk_light.use_color = True
    edit.gk_light.color = (1., .25, .5, 1.)
    look._dim_lights(scene, force=True)
    edit.data.energy = 90.
    edit.data.color = (.1, .2, .3)
    look._dim_lights(scene, force=True)
    close(edit_source.energy, 1500., .001)
    close(edit.data.energy, 120., .001)
    close_color(edit_source.color, tuple(look.linear_to_srgb(c) for c in (.1, .2, .3)))
    close_color(edit.data.color, tuple(look.srgb_to_linear(c) for c in (1., .25, .5)))
    edit.gk_light.override = False
    look._dim_lights(scene, force=True)
    close(edit.data.energy, 90., .001)
    print('LIGHTS: native-panel energy/color/shape edits, immediate export and override precedence passed')

    # Copying an object while previewing initially shares its temporary data.
    # A subsequent per-object override must split it again, without drift.
    duplicate = edit.copy()
    duplicate.name = 'Copied preview light'
    scene.collection.objects.link(duplicate)
    duplicate.gk_light.override = True
    duplicate.gk_light.energy = 7.
    look._dim_lights(scene, force=True)
    assert edit.data != duplicate.data
    close(edit.data.energy, 90., .001)
    close(duplicate.data.energy, 420., .001)
    look._undim_all(None, None)
    assert edit.data == edit_source and duplicate.data == edit_source
    assert not any(light.get(look._LIGHT_COPY_TAG) for light in bpy.data.lights)
    assert edit_source.use_fake_user
    print('LIGHTS: copies made during preview and fake-user cleanup passed')

    # Preserve a deliberately reassigned datablock instead of restoring the
    # stale source. No detached preview copy should be left after cleanup.
    look._dim_lights(scene, force=True)
    replacement = bpy.data.lights.new('Replacement authored light', 'POINT')
    replacement.energy = 3210.
    edit.data = replacement
    assert look.authored_light_data(edit) == replacement
    look._undim_lights(scene)
    assert edit.data == replacement
    close(edit.data.energy, 3210.)
    assert look._LIGHT_SOURCE_KEY not in edit and look._LIGHT_STATE_KEY not in edit
    assert not any(light.get(look._LIGHT_COPY_TAG) for light in bpy.data.lights)
    print('LIGHTS: deliberate datablock reassignment survives restore passed')

    # Changing the native type to AREA used to evade the supported-light scan.
    look._dim_lights(scene, force=True)
    edit.data.type = 'AREA'
    edit.data.size = 2.75
    look._undim_all(None, None)
    # Blender's old PointLight wrapper is no longer equal after its RNA subtype
    # becomes AreaLight; the ID pointer must still be the authored datablock.
    assert edit.data.as_pointer() == replacement.as_pointer()
    assert edit.data.type == 'AREA'
    close(edit.data.size, 2.75)
    assert look._LIGHT_SOURCE_KEY not in edit and look._LIGHT_STATE_KEY not in edit
    assert not any(light.get(look._LIGHT_COPY_TAG) for light in bpy.data.lights)
    print('LIGHTS: AREA type change is captured and save-pre cleans all copies passed')
    print('LIGHTS: all lifecycle tests passed', flush=True)


if __name__ == '__main__':
    main()
