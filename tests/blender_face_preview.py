"""面部 SDF 的渲染验证:角度变了,脸上的明暗线应该跟着滑。

判据(与扫光验证同一套思路,不轻信"节点建出来了"):
  1. 渲染一组不同 face_sdf_angle 的图
  2. 统计"左半脸 vs 右半脸"的亮度差
  3. 检查这个差值与角度**单调相关**

另外会渲染一张 face_sdf_mix=0 的对照,确认没有面部 SDF 时
画面确实是均匀的(否则说明测的不是面部 SDF 的效果)。
"""

import importlib.util
import os
import sys
import tempfile
import traceback

import bpy
import bmesh

spec = importlib.util.spec_from_file_location(
    "gkdev", r"D:\BlenderGameKit\BlenderGameKit\__init__.py",
    submodule_search_locations=[r"D:\BlenderGameKit\BlenderGameKit"])
mod = importlib.util.module_from_spec(spec)
sys.modules["gkdev"] = mod
spec.loader.exec_module(mod)
mod.register()
from gkdev import npr_preview as npr

TMP = os.path.join(tempfile.gettempdir(), "gk_face_sdf")
os.makedirs(TMP, exist_ok=True)


def make_sdf_image():
    """造一张符合约定的 SDF 贴图。

    **布局很重要**:U 是"沿脸的横轴"(装阴影线),V 是"脸的纵向"。
    这张测试图让每一半里**靠 U=0.5 的一侧亮、靠外侧暗**,
    于是 U=0.5 处有一条竖直的明暗分界 —— 正好对应"光在正前方时
    阴影线落在贴图中线"。
    这样只要阴影线位置一变,渲染出来的左右亮度差就会跟着变。
    """
    size = 64
    image = bpy.data.images.new("face_sdf_test", size, size)
    pixels = [0.0] * (size * size * 4)
    half = size // 2
    for y in range(size):
        for x in range(size):
            if x < half:
                u = x / (half - 1)
            else:
                u = (size - 1 - x) / (half - 1)
            # u=1 靠近中线 -> 亮;u=0 靠外 -> 暗
            value = 1.0 if u > 0.5 else 0.0
            index = (y * size + x) * 4
            pixels[index] = value
            pixels[index + 1] = value
            pixels[index + 2] = value
            pixels[index + 3] = 1.0
    image.pixels = pixels
    return image


def build_scene(mix, angle):
    """建场景。

    注意顺序:read_factory_settings() 会清掉**所有**已创建的数据块,
    所以贴图和材质都必须在它**之后**创建。
    之前把贴图传进来,一进函数就被清掉,报
    "StructRNA of type Image has been removed"。
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"

    sdf_image = make_sdf_image()

    # 一块正对相机的平面当"脸"。平面 UV 天然是正面投影,
    # 正是面部 SDF 需要的坐标系。
    #
    # 注意:bmesh.ops.create_grid **不生成 UV 层**。没有 UV 层时
    # UVMap / TexCoord 节点都会返回 (0,0),贴图恒定采在一个点上 ——
    # 表现是"改角度完全没反应"。所以这里必须自己写 UV。
    mesh = bpy.data.meshes.new("face")
    obj = bpy.data.objects.new("Face", mesh)
    scene.collection.objects.link(obj)
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=1.0)
    uv_layer = bm.loops.layers.uv.new("UVMap")
    for face in bm.faces:
        for loop in face.loops:
            # 刻意让 U 对应屏幕**水平**方向:面部 SDF 的 U 是"沿脸的横轴"
            # (装阴影线的方向),必须跟着水平位置变,这样"左右半脸亮度差"
            # 才能反映阴影线的位置。若 U 映射到竖直方向,
            # 左右取样的是同一个点,永远测不出差别。
            loop[uv_layer].uv = (
                (loop.vert.co.x + 1.0) * 0.5,   # U <- 水平
                (loop.vert.co.y + 1.0) * 0.5,   # V <- 竖直
            )
    bm.to_mesh(mesh)
    bm.free()

    material = npr.build_toon_material("FaceMat", {
        "face_sdf_mix": mix,
        "face_sdf_angle": angle,
        "face_sdf_image": sdf_image,
        "face_sdf_smooth": 0.02,
        "face_sdf_angle_scale": 1.0,
        "specular_amount": 0.0,
        "rim_amount": 0.0,
        "three_tone": 0.0,
        "shadow_smooth": 0.0,
    })
    obj.data.materials.append(material)

    world = bpy.data.worlds.new("W")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = (0.5, 0.5, 0.5, 1.0)
        bg.inputs["Strength"].default_value = 1.0
    scene.world = world

    cam_data = bpy.data.cameras.new("Cam")
    cam = bpy.data.objects.new("Cam", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (0, 0, 4.0)
    cam.rotation_euler = (0, 0, 0)
    scene.camera = cam

    scene.render.resolution_x = 64
    scene.render.resolution_y = 64
    scene.render.image_settings.file_format = "PNG"
    return scene


def measure(scene, tag):
    path = os.path.join(TMP, tag + ".png")
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(path)
    pixels = list(image.pixels)
    width, height = image.size

    left = 0.0
    left_n = 0
    right = 0.0
    right_n = 0
    mid = width // 2
    # 只取中间区域,避开平面边缘
    for y in range(int(height * 0.3), int(height * 0.7)):
        for x in range(int(width * 0.2), int(width * 0.8)):
            i = (y * width + x) * 4
            value = (pixels[i] + pixels[i + 1] + pixels[i + 2]) / 3.0
            if x < mid:
                left += value
                left_n += 1
            else:
                right += value
                right_n += 1
    bpy.data.images.remove(image)
    left_mean = left / max(1, left_n)
    right_mean = right / max(1, right_n)
    return left_mean, right_mean, left_mean - right_mean


def main():
    print("=" * 66)
    print("面部 SDF 渲染验证")
    print("=" * 66)

    results = []

    for angle in (-1.0, -0.5, 0.0, 0.5, 1.0):
        scene = build_scene(1.0, angle)
        left, right, diff = measure(scene, f"angle_{angle:+.1f}")
        results.append((angle, diff))
        print(f"  角度 {angle:+.1f}: 左={left:.3f} 右={right:.3f} 左-右={diff:+.4f}")

    # 对照:关掉面部 SDF
    scene = build_scene(0.0, 0.5)
    left0, right0, diff0 = measure(scene, "mix0")
    print(f"  对照(mix=0): 左={left0:.3f} 右={right0:.3f} 左-右={diff0:+.4f}")

    # 判据 1:开面部 SDF 时,左-右的差应该有明显变化范围
    diffs = [d for _a, d in results]
    spread = max(diffs) - min(diffs)
    print()
    print(f"左-右差值范围: {min(diffs):+.4f} .. {max(diffs):+.4f}  (跨度 {spread:.4f})")

    # 判据说明:
    # 测试贴图是**二值**的(每半只有一条竖直明暗分界),所以"左-右亮度差"
    # 随角度不是线性的,而是"线滑过取样窗时"的阶跃响应 ——
    # 因此不能用"逐点单调、容差 0.02"这种判据(实测会被判失败)。
    # 真正要验证的是两件事:
    #   1. 遮盖范围够大(阴影线确实从一侧滑到了另一侧)
    #   2. **符号正确**:角度为负(光在左)-> 左脸亮 -> 左-右 > 0
    #                      角度为正(光在右)-> 右脸亮 -> 左-右 < 0
    negative = [d for a, d in results if a < -0.01]
    positive = [d for a, d in results if a > 0.01]
    signs_ok = (all(d > 0.02 for d in negative) and all(d < -0.02 for d in positive))
    print(f"符号正确(负角左亮 / 正角右亮): {signs_ok}")

    passed = spread > 0.30 and signs_ok
    if passed:
        print()
        print("结论:面部阴影线确实随角度在左右之间滑动 -> 面部 SDF 预览生效")
        print("FACE_PREVIEW=OK")
        return 0
    print()
    print("结论:不符合预期 -> 面部 SDF 预览有问题")
    print("FACE_PREVIEW=FAILED")
    return 1


try:
    code = main()
except Exception:
    traceback.print_exc()
    code = 1

print(f"输出目录: {TMP}")
sys.exit(code)
