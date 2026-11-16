"""glTF 导出后的 UV 方向回归(计划 C7)。

跑法:
    blender -b --factory-startup --python tests/blender_uv_export.py

模型是一块 2×2 的"方向标记"地板:四个象限各贴一个纯色块
(图片左上红 / 右上绿 / 左下蓝 / 右下白),UV 用 `BlenderGameKit.uv`
按图片像素矩形写。断言:

  1. `uv.pixel_rect_uv` 把"图片顶行"换算成 Blender 的高 v;
  2. 导出后 GLB 里 v = 1 - Blender 的 v —— **只翻一次**;
  3. 每个三角形的 UV 象限和它的世界位置象限一致(左/右、上/下);
  4. 用导出后的 PNG 按 GLB 的 UV 采样,颜色仍是该象限的颜色。

第 3/4 条是"二次翻转"的照妖镜:再翻一次会让颜色和位置上下错位。
"""

import json
import os
import struct
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from BlenderGameKit import asset_export, uv  # noqa: E402

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" - {detail}" if detail and not condition else ""))
    return bool(condition)


# 象限 -> (图片像素矩形(左上角原点), 颜色)
# 颜色只用 0/1 通道,sRGB 往返是不动点,断言不受色彩管理影响。
QUADRANTS = {
    (True, True): ((0, 4, 4, 4), (0.0, 0.0, 1.0, 1.0)),    # 左下:蓝
    (False, True): ((4, 4, 4, 4), (1.0, 1.0, 1.0, 1.0)),   # 右下:白
    (True, False): ((0, 0, 4, 4), (1.0, 0.0, 0.0, 1.0)),   # 左上:红
    (False, False): ((4, 0, 4, 4), (0.0, 1.0, 0.0, 1.0)),  # 右上:绿
}
TEXTURE_SIZE = 8


def make_image():
    """8×8 四色图;Blender 的像素行 0 在图片**底部**,所以按行翻转写入。"""
    image = bpy.data.images.new("UV方向贴图", width=TEXTURE_SIZE,
                                height=TEXTURE_SIZE)
    pixels = [0.0] * (TEXTURE_SIZE * TEXTURE_SIZE * 4)
    for (left, bottom), (rect, color) in QUADRANTS.items():
        x, y, width, height = rect
        for row in range(y, y + height):            # row 从图片顶部数
            for col in range(x, x + width):
                index = ((TEXTURE_SIZE - 1 - row) * TEXTURE_SIZE + col) * 4
                pixels[index:index + 4] = color
    image.pixels[:] = pixels
    return image


def make_model(image):
    """2×2 地板,每块的四角 UV 精确覆盖它对应的图片象限。"""
    verts, faces = [], []
    for (left, bottom), (rect, _color) in QUADRANTS.items():
        cx = -0.5 if left else 0.5
        cy = -0.5 if bottom else 0.5
        base = len(verts)
        # 顶点顺序:左下 / 右下 / 右上 / 左上
        verts.extend([(cx - 0.5, cy - 0.5, 0.0), (cx + 0.5, cy - 0.5, 0.0),
                      (cx + 0.5, cy + 0.5, 0.0), (cx - 0.5, cy + 0.5, 0.0)])
        faces.append((base, base + 1, base + 2, base + 3))

    mesh = bpy.data.meshes.new("UV方向网格")
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    layer = mesh.uv_layers.new(name="UVMap")
    index = 0
    for rect, _color in QUADRANTS.values():
        u0, v0, u1, v1 = uv.pixel_rect_uv(rect, TEXTURE_SIZE)
        for corner in ((u0, v0), (u1, v0), (u1, v1), (u0, v1)):
            layer.data[index].uv = corner
            index += 1

    material = bpy.data.materials.new("UV方向材质")
    material.use_nodes = True
    texture = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    material.node_tree.links.new(
        texture.outputs["Color"],
        material.node_tree.nodes.get("Principled BSDF").inputs["Base Color"])
    mesh.materials.append(material)

    obj = bpy.data.objects.new("UV方向地板", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj, mesh, layer, material


def read_glb(path):
    data = Path(path).read_bytes()
    magic, _version, length = struct.unpack_from("<III", data, 0)
    assert magic == 0x46546C67, hex(magic)
    offset, chunks = 12, {}
    while offset < length:
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        chunks[chunk_type] = data[offset + 8: offset + 8 + chunk_length]
        offset += 8 + chunk_length
    return json.loads(chunks[0x4E4F534A].decode("utf-8")), chunks[0x004E4942]


def read_accessor(gltf, binary, index):
    accessor = gltf["accessors"][index]
    view = gltf["bufferViews"][accessor["bufferView"]]
    fmt, size = {5126: ("f", 4), 5125: ("I", 4),
                 5123: ("H", 2), 5121: ("B", 1)}[accessor["componentType"]]
    count = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[accessor["type"]]
    stride = view.get("byteStride", size * count)
    base = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    return [struct.unpack_from("<" + fmt * count, binary, base + i * stride)
            for i in range(accessor["count"])]


def main():
    # 1. 换算函数:图片顶行 -> 高 v,底行 -> 低 v
    check("pixel_point_uv 图片左上角", uv.pixel_point_uv(0, 0, 8) == (0.0, 1.0))
    check("pixel_point_uv 图片左下角", uv.pixel_point_uv(0, 8, 8) == (0.0, 0.0))
    check("pixel_rect_uv 顶行矩形",
          uv.pixel_rect_uv((0, 0, 4, 4), 8) == (0.0, 0.5, 0.5, 1.0))
    check("pixel_rect_uv 底行矩形",
          uv.pixel_rect_uv((0, 4, 4, 4), 8) == (0.0, 0.0, 0.5, 0.5))

    # 2. 清空默认场景
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"

    image = make_image()
    obj, mesh, layer, material = make_model(image)

    # 3. 走插件真实导出路径
    export_dir = tempfile.mkdtemp(prefix="gk_uv_export_")
    path, ok, message = asset_export.export_glb(export_dir, "UVProbe", False, [obj])
    check("导出 .glb", ok and os.path.isfile(path), message)

    gltf, binary = read_glb(path)
    primitives = gltf["meshes"][0]["primitives"]
    check("一个材质一个 primitive", len(primitives) == 1, len(primitives))
    primitive = primitives[0]
    positions = read_accessor(gltf, binary, primitive["attributes"]["POSITION"])
    uv_values = read_accessor(gltf, binary, primitive["attributes"]["TEXCOORD_0"])
    if "indices" in primitive:
        order = [item[0] for item in
                 read_accessor(gltf, binary, primitive["indices"])]
    else:
        order = list(range(len(positions)))
    triangles = [order[i:i + 3] for i in range(0, len(order), 3)]
    check("三角形数量(4 块 x 2)", len(triangles) == 8, len(triangles))

    # 4. GLB 的 v 必须恰好等于 1 - Blender 的 v(只翻一次)
    authored = {}
    for polygon in mesh.polygons:
        for loop_index in polygon.loop_indices:
            vertex = mesh.vertices[mesh.loops[loop_index].vertex_index]
            key = (round(vertex.co.x, 4), round(vertex.co.y, 4))
            authored[key] = tuple(layer.data[loop_index].uv)
    flips_ok = True
    for index, position in enumerate(positions):
        # glTF 是 Y-up:Blender (x, y, z) -> glTF (x, z, -y)
        key = (round(position[0], 4), round(-position[2], 4))
        source = authored.get(key)
        if source is None:
            flips_ok = False
            print("  找不到对应顶点", key)
            continue
        u, v = uv_values[index]
        if abs(u - source[0]) > 1e-4 or abs(v - (1.0 - source[1])) > 1e-4:
            flips_ok = False
            print(f"  顶点 {key}: Blender {source} -> GLB {(u, v)}")
    check("导出只翻一次 V(GLB v = 1 - Blender v)", flips_ok)

    # 5. 方向标记:UV 象限必须和世界位置象限一致
    direction_ok = True
    for number, triangle in enumerate(triangles):
        points = [positions[i] for i in triangle]
        coords = [uv_values[i] for i in triangle]
        x = sum(p[0] for p in points) / 3.0
        z = sum(p[2] for p in points) / 3.0        # glTF z = -Blender y
        u = sum(c[0] for c in coords) / 3.0
        v = sum(c[1] for c in coords) / 3.0        # glTF v 从图片顶部数
        left, bottom = x < 0.0, z > 0.0
        if (u < 0.5) != left or (v > 0.5) != bottom:
            direction_ok = False
            print(f"  三角 {number}: 位置({x:+.2f},{z:+.2f}) 象限"
                  f"({'左' if left else '右'}{'下' if bottom else '上'}),"
                  f" UV({u:.2f},{v:.2f})")
    check("UV 象限与位置象限一致", direction_ok)

    # 6. 导出 PNG 定向:按 GLB 的 UV 采样应得到该象限的颜色
    exported = asset_export.export_textures(export_dir, [material])
    check("贴图导出为 PNG", len(exported) == 1, exported)
    image_path = exported[0][1]
    loaded = bpy.data.images.load(image_path, check_existing=False)
    width, height = loaded.size
    pixels = list(loaded.pixels)

    def sample(u, v):
        col = min(width - 1, max(0, int(u * width)))
        row_top = min(height - 1, max(0, int(v * height)))
        row_bottom = height - 1 - row_top           # bpy 的像素行从底部数
        offset = (row_bottom * width + col) * 4
        return tuple(pixels[offset:offset + 3])

    colors = {(left, bottom): color
              for (left, bottom), (_rect, color) in QUADRANTS.items()}
    color_ok = True
    for number, triangle in enumerate(triangles):
        points = [positions[i] for i in triangle]
        coords = [uv_values[i] for i in triangle]
        x = sum(p[0] for p in points) / 3.0
        z = sum(p[2] for p in points) / 3.0
        u = sum(c[0] for c in coords) / 3.0
        v = sum(c[1] for c in coords) / 3.0
        expected = colors[(x < 0.0, z > 0.0)]
        got = sample(u, v)
        if any(abs(got[i] - expected[i]) > 0.05 for i in range(3)):
            color_ok = False
            print(f"  三角 {number}: UV({u:.2f},{v:.2f}) 采样 {got} != {expected}")
    check("按导出 UV 采样得到正确象限颜色", color_ok)
    bpy.data.images.remove(loaded)

    passed = all(result[1] for result in RESULTS)
    print(f"UV_EXPORT={'OK' if passed else 'FAILED'} "
          f"({sum(1 for r in RESULTS if r[1])}/{len(RESULTS)})")
    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
