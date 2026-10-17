"""GPU and lifecycle checks for temporary scene-lit toon material previews.

Run: blender -b --factory-startup --python tests/blender_godot_material_preview.py
Only writes disposable artifacts under .audit/preview_match/material_probe.
"""

import json
import math
from pathlib import Path
import struct
import sys

import bpy
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from BlenderGameKit import godot_material_preview as preview
from BlenderGameKit import npr_preview


class ProbeSettings(bpy.types.PropertyGroup):
    toon_materials: BoolProperty(default=True)
    toon_follow_light: BoolProperty(default=True)
    style_enabled: BoolProperty(default=False)
    style_rim: FloatProperty(default=0.0)
    env_enabled: BoolProperty(default=False)
    env_ambient_energy: FloatProperty(default=0.0)
    env_ambient_color: FloatVectorProperty(size=4, default=(0.68, 0.78, 0.96, 1))


def material_snapshot(material):
    return (material.name, len(material.node_tree.nodes),
            [(node.name, [(socket.name, str(socket.default_value))
                          for socket in node.inputs if hasattr(socket, "default_value")])
             for node in material.node_tree.nodes],
            [(link.from_node.name, link.from_socket.name,
              link.to_node.name, link.to_socket.name)
             for link in material.node_tree.links])


def glb_material_names(path):
    data = path.read_bytes()
    length, kind = struct.unpack_from('<II', data, 12)
    assert kind == 0x4E4F534A
    doc = json.loads(data[20:20+length].decode('utf-8'))
    return [item.get('name', '') for item in doc.get('materials', [])]


def run():
    out = ROOT / '.audit' / 'preview_match' / 'material_probe'
    out.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.utils.register_class(ProbeSettings)
    bpy.types.Scene.gk_settings = bpy.props.PointerProperty(type=ProbeSettings)
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE_NEXT'
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'
    scene.render.resolution_x = scene.render.resolution_y = 64
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    bpy.ops.mesh.primitive_plane_add(size=2)
    obj = bpy.context.object
    obj.name = 'Authored object'
    material = bpy.data.materials.new('Authored emission')
    material.use_nodes = True
    principled = material.node_tree.nodes.get('Principled BSDF')
    principled.inputs['Base Color'].default_value = (0.25, 0.5, 0.75, 1)
    principled.inputs['Metallic'].default_value = 0.8
    principled.inputs['Roughness'].default_value = 0.05
    principled.inputs['Emission Color'].default_value = (0.8, 0.2, 0.1, 1)
    principled.inputs['Emission Strength'].default_value = 3
    obj.data.materials.append(material)
    dormant = bpy.data.materials.new('Dormant object override')
    obj.material_slots[0].link = 'OBJECT'
    obj.material_slots[0].material = dormant
    obj.material_slots[0].link = 'DATA'
    linked = obj.copy()
    linked.name = 'Linked mesh original'
    linked.data = obj.data
    other_scene = bpy.data.scenes.new('Unadapted scene')
    other_scene.collection.objects.link(linked)
    snapshot = material_snapshot(material)

    camera_data = bpy.data.cameras.new('camera')
    camera = bpy.data.objects.new('camera', camera_data)
    scene.collection.objects.link(camera)
    camera.location = (0, 0, 2)
    scene.camera = camera
    light_data = bpy.data.lights.new('sun', 'SUN')
    light_data.energy = 0
    lamp = bpy.data.objects.new('sun', light_data)
    scene.collection.objects.link(lamp)

    def shot(name):
        scene.render.filepath = str(out / (name + '.png'))
        bpy.ops.render.render(write_still=True)
        image = bpy.data.images.load(scene.render.filepath, check_existing=False)
        values = list(image.pixels[(32*64+32)*4:(32*64+32)*4+3])
        bpy.data.images.remove(image)
        return values

    authored = shot('authored_emission')
    assert preview.apply(scene) == 1
    temporary = obj.material_slots[0].material
    assert preview.resolve_material(temporary) == material
    assert obj.data.materials[0] == material
    assert linked.material_slots[0].material == material
    assert material_snapshot(material) == snapshot
    dark = shot('preview_lights_off')
    assert max(dark) < 0.01 and max(authored) > 0.5, (dark, authored)
    light_data.energy = 1
    lit = shot('preview_lights_on')
    assert min(lit) > 0.1, lit
    light_data.energy = 0
    scene.gk_settings.env_enabled = True
    scene.gk_settings.env_ambient_energy = 0.5
    assert preview.apply(scene) == 1
    ambient = shot('preview_ambient_only')
    # Actual Godot 4.7.2 GPU reference, the same ALBEDO and Environment colour.
    godot_ambient = (0.321569, 0.478431, 0.635294)
    assert max(abs(a - b) for a, b in zip(ambient, godot_ambient)) < 0.008, ambient
    scene.gk_settings.env_enabled = False
    light_data.energy = 1
    assert preview.apply(scene) == 1
    assert len(preview._MATERIALS) == 1

    # Shift+D and linked object copies inherit the temporary OBJECT slot but
    # have no Python backup entry. Both scene restore and save must recover it.
    duplicates = []
    for name, unique_mesh in [('Copied preview object', False),
                              ('Duplicated preview mesh', True)]:
        duplicate = obj.copy()
        duplicate.name = name
        if unique_mesh:
            duplicate.data = obj.data.copy()
        duplicate.location.x = 10 + len(duplicates) * 3
        scene.collection.objects.link(duplicate)
        assert duplicate.material_slots[0].material.get(preview.PREVIEW_KEY)
        duplicates.append(duplicate)
    preview.restore(scene)
    for duplicate in duplicates:
        assert duplicate.material_slots[0].link == 'DATA'
        assert duplicate.material_slots[0].material == material
        assert duplicate.data.materials[0] == material
    assert preview.apply(scene) == 3
    orphan_material = obj.material_slots[0].material.copy()
    orphan_material_name = orphan_material.name

    # Both normal completion and exceptions must resume the previews.
    try:
        with preview.suspend():
            assert obj.material_slots[0].link == 'DATA'
            assert obj.material_slots[0].material == material
            assert bpy.data.materials.get(orphan_material_name) is None
            for duplicate in duplicates:
                assert duplicate.material_slots[0].material == material
                assert duplicate.material_slots[0].link == 'DATA'
            with preview.suspend():
                assert obj.data.materials[0] == material
            bpy.ops.export_scene.gltf(filepath=str(out / 'authored.glb'),
                                      export_format='GLB', export_lights=False,
                                      export_cameras=False)
            bpy.ops.wm.save_as_mainfile(filepath=str(out / 'authored.blend'),
                                      check_existing=False)
            raise ValueError('expected probe exception')
    except ValueError:
        pass
    assert preview.resolve_material(obj.material_slots[0].material) == material
    assert obj.material_slots[0].material != material
    names = glb_material_names(out / 'authored.glb')
    assert 'Authored emission' in names and not any(name.startswith('__GK') for name in names), names
    preview.restore_all()
    assert obj.material_slots[0].link == 'DATA'
    assert obj.material_slots[0].material == material
    obj.material_slots[0].link = 'OBJECT'
    assert obj.material_slots[0].material == dormant
    obj.material_slots[0].link = 'DATA'
    assert material_snapshot(material) == snapshot
    assert not preview._MATERIALS
    for duplicate in duplicates:
        assert duplicate.material_slots[0].material == material
        assert duplicate.material_slots[0].link == 'DATA'
        bpy.data.objects.remove(duplicate, do_unlink=True)

    # Isolate the view-dependent rim on black albedo. It must grow toward the
    # edge, receive the real light and disappear when that light switches off.
    principled.inputs['Base Color'].default_value = (0, 0, 0, 1)
    scene.gk_settings.style_enabled = True
    scene.gk_settings.style_rim = 0.2
    assert preview.apply(scene) == 1
    rim_front = shot('rim_facing_camera')
    assert max(rim_front) < 0.015, rim_front
    for angle in (30, 60):
        radians = math.radians(angle)
        camera.location = (2 * math.sin(radians), 0, 2 * math.cos(radians))
        camera.rotation_euler = (Vector((0, 0, 0)) - camera.location).to_track_quat('-Z', 'Y').to_euler()
        values = shot('rim_angle_' + str(angle))
        if angle == 30:
            rim_30 = values
        else:
            rim_60 = values
    assert rim_60[2] > rim_30[2] * 1.5 > 0.1, (rim_30, rim_60)
    assert rim_60[2] > rim_60[0], rim_60
    light_data.energy = 0
    rim_off = shot('rim_lights_off')
    assert max(rim_off) < 0.01, rim_off
    light_data.energy = 1
    camera.location = (0, 0, 2)
    camera.rotation_euler = (0, 0, 0)
    preview.restore_all()
    principled.inputs['Base Color'].default_value = (0.25, 0.5, 0.75, 1)
    scene.gk_settings.style_enabled = False
    scene.gk_settings.style_rim = 0

    # A native NPR material keeps its authored preview graph.
    material[npr_preview.material_key('shadow_step')] = 0.2
    assert preview.apply(scene) == 0
    assert obj.material_slots[0].material == material
    del material[npr_preview.material_key('shadow_step')]
    preview.restore_all()

    # Source-image colour space and alpha remain authored data. The temporary
    # sampler follows Godot's source_color / UV / 0.5 alpha-cut behaviour.
    image = bpy.data.images.new('Authored data colour texture', width=4, height=1,
                                alpha=True)
    image.colorspace_settings.name = 'Non-Color'
    image.pixels[:] = [1, 0.2, 0.1, 1] * 2 + [0.1, 0.2, 1, 0] * 2
    texture = material.node_tree.nodes.new('ShaderNodeTexImage')
    texture.image = image
    material.node_tree.links.new(texture.outputs['Color'], principled.inputs['Base Color'])
    obj.material_slots[0].link = 'OBJECT'
    obj.material_slots[0].material = material
    assert preview.apply(scene) == 1
    assert image.colorspace_settings.name == 'Non-Color'
    assert len(preview._IMAGES) == 1
    orphan_image = preview._IMAGES[0].copy()
    orphan_image_name = orphan_image.name
    shot('preview_texture_cutout')
    rendered = bpy.data.images.load(scene.render.filepath, check_existing=False)
    left = list(rendered.pixels[(32*64+20)*4:(32*64+20)*4+3])
    right = list(rendered.pixels[(32*64+44)*4:(32*64+44)*4+3])
    bpy.data.images.remove(rendered)
    assert left[0] > 0.15 and left[0] > left[2] * 2, left
    assert max(right) < 0.02, right
    preview.restore_all()
    assert obj.material_slots[0].link == 'OBJECT'
    assert obj.material_slots[0].material == material
    assert not preview._IMAGES
    assert bpy.data.images.get(orphan_image_name) is None
    assert image.colorspace_settings.name == 'Non-Color'

    # Loaded files must contain authored slots, with no temporary materials.
    bpy.ops.wm.open_mainfile(filepath=str(out / 'authored.blend'))
    loaded = bpy.data.objects.get('Authored object')
    assert loaded.material_slots[0].link == 'DATA'
    assert loaded.material_slots[0].material.name == 'Authored emission'
    assert not any(mat.get(preview.PREVIEW_KEY) for mat in bpy.data.materials)
    for name in ('Copied preview object', 'Duplicated preview mesh'):
        duplicate = bpy.data.objects[name]
        assert duplicate.material_slots[0].link == 'DATA'
        assert duplicate.material_slots[0].material.name == 'Authored emission'
    (out / 'result.json').write_text(json.dumps({
        'authored_emission': authored, 'preview_lights_off': dark,
        'preview_lights_on': lit, 'preview_ambient_only': ambient,
        'godot_ambient_only': godot_ambient, 'glb_materials': names,
        'source_nodes_unchanged': True, 'shared_mesh_unchanged': True,
        'dormant_override_restored': True, 'save_reload_clean': True,
        'nested_suspend_exception_resumes': True,
        'duplicated_objects_saved_clean': True,
        'untracked_material_image_copies_removed': True,
        'rim_front': rim_front, 'rim_30_degrees': rim_30,
        'rim_60_degrees': rim_60, 'rim_lights_off': rim_off,
        'texture_source_untouched': True, 'alpha_cutout': [left, right]}, indent=2))
    print('GODOT_MATERIAL_PREVIEW=OK')


if __name__ == '__main__':
    try:
        run()
    finally:
        preview.restore_all()
