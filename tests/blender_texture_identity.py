"""Chinese texture names must keep their identity in exported Godot materials.

Run with Blender --background --factory-startup --python this_file.
Writes isolated PNG/material fixtures under .audit/preview_match/texture_probe.
"""

from pathlib import Path
import re
import sys

import addon_utils
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
addon_utils.enable("BlenderGameKit", default_set=False, persistent=False)
from BlenderGameKit import asset_export as export, godot_material_preview, npr_preview


def main():
    out = ROOT / ".audit/preview_match/texture_probe"
    out.mkdir(parents=True, exist_ok=True)
    scene = bpy.context.scene
    scene.gk_settings.toon_materials = True
    scene.gk_settings.toon_follow_light = True
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.render.image_settings.file_format = "PNG"
    objects, materials, images = [], [], []
    for index, (name, color) in enumerate((
            ("旧石砖地面贴图", (0.8, 0.05, 0.02, 1.0)),
            ("风化墙面贴图", (0.02, 0.08, 0.8, 1.0)))):
        image = bpy.data.images.new(name, width=4, height=4)
        image.pixels[:] = list(color) * 16
        material = bpy.data.materials.new(name + "材质")
        material.use_nodes = True
        tex = material.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = image
        material.node_tree.links.new(tex.outputs["Color"],
            material.node_tree.nodes.get("Principled BSDF").inputs["Base Color"])
        bpy.ops.mesh.primitive_cube_add(location=(index * 3, 0, 0))
        obj = bpy.context.object
        obj.data.materials.append(material)
        objects.append(obj)
        materials.append(material)
        images.append(image)

    exported = export.export_textures(str(out), materials)
    assert len(exported) == 2
    paths = [Path(path) for _name, path in exported]
    assert len({path.name for path in paths}) == 2, paths
    assert paths[0].read_bytes() != paths[1].read_bytes()
    for index, path in enumerate(paths):
        image = bpy.data.images.load(str(path), check_existing=False)
        pixel = tuple(image.pixels[:4])
        assert pixel[index * 2] > 0.6 and pixel[2 - index * 2] < 0.25, pixel
        bpy.data.images.remove(image)

    refs = []
    for _name, path, _has_tex in export.write_materials(str(out), objects):
        text = Path(path).read_text(encoding="utf-8")
        ref = re.search(r'path="res://art/([^\"]+)"', text).group(1)
        refs.append(ref)
        assert (out / "art" / ref).is_file()
    assert set(refs) == {path.name for path in paths}, refs
    assert export.fx_texture_file(objects[0], str(out)) == paths[0].name
    materials[0][npr_preview.material_key("specular_amount")] = 0.2
    npr_path, _count = export.write_npr_material(str(out), materials[0],
                                               file_name="npr", scene_lit=True)
    assert f'res://art/{paths[0].name}' in Path(npr_path).read_text(encoding="utf-8")
    # Remove the NPR flag so both ordinary source materials receive a preview.
    for key in list(materials[0].keys()):
        del materials[0][key]
    godot_material_preview.apply(scene)
    for obj, authored in zip(objects, images):
        preview = obj.material_slots[0].material
        textures = [n.image for n in preview.node_tree.nodes if n.type == "TEX_IMAGE"]
        assert len(textures) == 1
        assert tuple(textures[0].pixels[:4]) == tuple(authored.pixels[:4])
    godot_material_preview.restore(scene)
    assert all(obj.material_slots[0].material == mat for obj, mat in zip(objects, materials))
    assert export.material_texture_file_name("fire.png") == "fire_png.png"
    assert export.material_texture_file_name("albedo") == "albedo.png"
    assert export.material_texture_file_name("墙 a") != export.material_texture_file_name("墙-a")
    print("TEXTURE_IDENTITY_OK", [path.name for path in paths])


if __name__ == "__main__":
    main()
