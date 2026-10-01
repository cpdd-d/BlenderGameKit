"""生成面部 SDF 预览的对比图,便于人工看"阴影线在滑动"。

用二值测试 SDF,渲染 -0.6 / 0.0 / +0.6 三个角度并排存下来。
"""

import importlib.util
import os
import sys

import bmesh
import bpy

spec = importlib.util.spec_from_file_location(
    "gkdev", r"D:\BlenderGameKit\BlenderGameKit\__init__.py",
    submodule_search_locations=[r"D:\BlenderGameKit\BlenderGameKit"])
mod = importlib.util.module_from_spec(spec)
sys.modules["gkdev"] = mod
spec.loader.exec_module(mod)
mod.register()
from gkdev import npr_preview as npr

OUT = r"D:\BlenderGameKit\examples\demo_render\face_preview_shots"
os.makedirs(OUT, exist_ok=True)


def make_sdf(size=128):
    image = bpy.data.images.new("face_sdf_demo", size, size)
    pixels = [0.0] * (size * size * 4)
    half = size // 2
    for y in range(size):
        for x in range(size):
            u = x / (half - 1) if x < half else (size - 1 - x) / (half - 1)
            value = 1.0 if u > 0.5 else 0.0
            i = (y * size + x) * 4
            pixels[i] = value
            pixels[i + 1] = value
            pixels[i + 2] = value
            pixels[i + 3] = 1.0
    image.pixels = pixels
    return image


def render(angle, tag):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.view_settings.view_transform = "Standard"

    image = make_sdf()

    # 用一个略扁的球体当"脸",比平面更有说服力
    mesh = bpy.data.meshes.new("head")
    obj = bpy.data.objects.new("Head", mesh)
    scene.collection.objects.link(obj)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=24, radius=1.0)
    bmesh.ops.scale(bm, vec=(0.85, 0.7, 1.0), verts=bm.verts)
    uv_layer = bm.loops.layers.uv.new("UVMap")
    # 用**正面投影**当 UV:这是面部 SDF 的典型做法
    for face in bm.faces:
        for loop in face.loops:
            co = loop.vert.co
            loop[uv_layer].uv = ((co.x / 0.85 + 1.0) * 0.5, (co.z + 1.0) * 0.5)
    bm.to_mesh(mesh)
    bm.free()

    material = npr.build_toon_material("FaceDemo", {
        "face_sdf_mix": 1.0,
        "face_sdf_angle": angle,
        "face_sdf_image": image,
        "face_sdf_smooth": 0.02,
        "face_sdf_angle_scale": 1.0,
        "specular_amount": 0.0,
        "rim_amount": 0.0,
        "three_tone": 0.0,
        "shadow_smooth": 0.0,
        "shadow_tint": (0.62, 0.58, 0.78),
    })
    obj.data.materials.append(material)

    world = bpy.data.worlds.new("W")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (0.55, 0.58, 0.66, 1.0)
    bg.inputs["Strength"].default_value = 1.0
    scene.world = world

    cam_data = bpy.data.cameras.new("C")
    cam = bpy.data.objects.new("C", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (0, -3.2, 0)
    cam.rotation_euler = (1.5708, 0, 0)
    scene.camera = cam
    scene.render.resolution_x = 260
    scene.render.resolution_y = 260
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = os.path.join(OUT, tag + ".png")
    bpy.ops.render.render(write_still=True)
    print(f"  已渲染 {tag}.png  (角度 {angle:+.2f})")


print("=" * 60)
for angle, tag in ((-0.6, "angle_left"), (0.0, "angle_front"), (0.6, "angle_right")):
    render(angle, tag)
print(f"输出目录: {OUT}")
print("SHOTS_DONE")
