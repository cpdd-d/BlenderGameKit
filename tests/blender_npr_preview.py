"""验证 Blender 端 NPR 预览材质:能建、参数能读回、渲染真的出色阶、能导出 .tres。

用法:
    blender -b --python tests/blender_npr_preview.py
    (不要加 --factory-startup)

这个测试的重点是**不轻信"节点建出来了"**:
最后会渲染两张图做 A/B 对比,并检查导出的 .tres 内容。
"""

import json
import os
import sys
import tempfile
import traceback
from importlib import import_module

import addon_utils
import bmesh
import bpy

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" - {detail}" if detail and not condition else ""))
    return bool(condition)


def find_addon():
    for module in addon_utils.modules():
        if "blender_game_kit" in getattr(module, "__name__", ""):
            return module.__name__
    return None


def fresh_scene():
    """清空场景并配好 EEVEE + Standard 色彩管理 + 斜射光。

    三个细节都是踩过坑才加的:
      * view_transform 必须是 Standard,否则色彩管理会把色阶重新平滑掉
      * 太阳必须和相机成明显夹角,否则正对相机的部分均匀受光,测不出色阶
      * 要有世界环境光,否则相机对着的可能正好是暗面
    """
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"

    mesh = bpy.data.meshes.new("ball")
    obj = bpy.data.objects.new("Ball", mesh)
    scene.collection.objects.link(obj)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=1.0)
    bm.to_mesh(mesh)
    bm.free()

    light_data = bpy.data.lights.new("Key", type="SUN")
    light_data.energy = 1.0
    light = bpy.data.objects.new("Key", light_data)
    scene.collection.objects.link(light)
    light.rotation_euler = (0.9, 0.0, 0.9)

    world = bpy.data.worlds.new("W")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = (0.20, 0.22, 0.30, 1.0)
    scene.world = world

    cam_data = bpy.data.cameras.new("Cam")
    cam = bpy.data.objects.new("Cam", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (0, -4.5, 0)
    cam.rotation_euler = (1.5708, 0, 0)
    scene.camera = cam

    scene.render.resolution_x = 48
    scene.render.resolution_y = 48
    scene.render.image_settings.file_format = "PNG"
    return scene, obj


def measure(scene, path):
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(path)
    pixels = list(image.pixels)
    width, height = image.size
    colors = {}
    for y in range(int(height * 0.35), int(height * 0.65)):
        for x in range(int(width * 0.35), int(width * 0.65)):
            i = (y * width + x) * 4
            key = tuple(round(v, 2) for v in pixels[i:i + 3])
            colors[key] = colors.get(key, 0) + 1
    bpy.data.images.remove(image)
    return colors


def main():
    print("=" * 70)
    print("BlenderGameKit NPR 预览材质测试")
    print("=" * 70)

    module_name = find_addon()
    if not check("找到插件", module_name, "未安装"):
        return 1
    addon_utils.enable(module_name, default_set=True, persistent=True)

    npr = import_module(f"{module_name}.npr_preview")
    asset_export = import_module(f"{module_name}.asset_export")

    tmp = os.path.join(tempfile.gettempdir(), "gk_npr_preview_test")
    os.makedirs(tmp, exist_ok=True)

    # ---- 1. 参数表与着色器一一对应 ----
    #
    # 注意要查**两个**着色器:
    #   卡通参数  -> gk_toon.gdshader
    #   描边参数  -> gk_outline.gdshader
    # 描边是单独的 next_pass(inverted hull 是几何操作,塞不进同一个 shader),
    # 所以它的参数名在前者里当然找不到。
    shaders_dir = os.path.join(os.path.dirname(os.path.abspath(npr.__file__)),
                               "shaders")
    shader_text = ""
    for name in ("gk_toon.gdshader", "gk_outline.gdshader", "gk_toon_2d.gdshader"):
        try:
            with open(os.path.join(shaders_dir, name), encoding="utf-8") as handle:
                shader_text += "\n" + handle.read()
        except OSError as exc:
            check(f"读到 {name}", False, str(exc))

    if not shader_text:
        return 1
    missing = [uniform for _n, uniform, _k, _d, _l in npr.PARAM_MAP
               if uniform not in shader_text]
    check(f"参数与着色器一一对应({len(npr.PARAM_MAP)} 个)", not missing, str(missing))

    # ---- 2. 材质能建出来,节点齐备 ----
    scene, obj = fresh_scene()
    try:
        material = npr.build_toon_material("GK_Test_Toon")
        node_names = [n.name for n in material.node_tree.nodes]
        print(f"    节点数: {len(node_names)}")
        required = ["漫反射", "取光照", "光照亮度", "色阶(硬)", "色阶(柔)", "输出"]
        absent = [name for name in required if name not in node_names]
        check("关键节点齐备", not absent, str(absent))
        check("没有空贴图节点",
              not any(n.bl_idname == "ShaderNodeTexImage" and n.image is None
                      for n in material.node_tree.nodes))
    except Exception as exc:
        check("创建 NPR 材质", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    # ---- 3. 色阶配置正确 ----
    try:
        ramp = material.node_tree.nodes.get("色阶(硬)")
        check("硬色阶是 CONSTANT", ramp.color_ramp.interpolation == "CONSTANT",
              ramp.color_ramp.interpolation)
        positions = [round(e.position, 3) for e in ramp.color_ramp.elements]
        print(f"    色标位置: {positions}")
        # 默认 shadow_step=0.1 -> (0.1+1)/2 = 0.55
        check("分界按 (step+1)/2 映射", any(abs(p - 0.55) < 0.02 for p in positions),
              str(positions))
    except Exception as exc:
        check("色阶配置", False, f"{type(exc).__name__}: {exc}")

    # ---- 4. 参数往返 ----
    try:
        params = npr.read_params(material)
        check("参数可读回", len(params) == len(npr.PARAM_MAP), str(len(params)))
        print(f"    shadow_step={params.get('shadow_step')} "
              f"lit_tint={tuple(round(v,3) for v in params.get('lit_tint', ()))}")

        changed = npr.build_toon_material(
            "GK_Test_Toon", params={"shadow_step": 0.3, "three_tone": 1.0})
        after = npr.read_params(changed)
        check("改参数后能读回", abs(after["shadow_step"] - 0.3) < 1e-4,
              str(after["shadow_step"]))
        ramp_changed = changed.node_tree.nodes.get("色阶(硬)")
        new_positions = [round(e.position, 3) for e in ramp_changed.color_ramp.elements]
        print(f"    改后色标位置: {new_positions}")
        check("三段布局生效", len(ramp_changed.color_ramp.elements) == 3,
              str(len(ramp_changed.color_ramp.elements)))
        check("分界位置跟着变", any(abs(p - 0.65) < 0.02 for p in new_positions),
              str(new_positions))
    except Exception as exc:
        check("参数往返", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 5. sRGB -> 线性转换 ----
    try:
        # Godot 的 0.706(sRGB)对应线性约 0.453
        got = npr.srgb_to_linear(0.706)
        check("sRGB 转线性正确", abs(got - 0.4529) < 0.01, f"{got:.4f}")
        round_trip = npr.linear_to_srgb(got)
        check("线性转回 sRGB 无损", abs(round_trip - 0.706) < 0.002, f"{round_trip:.4f}")
        # 阴影色标应该存的是线性值,明显小于 sRGB 值
        ramp = material.node_tree.nodes.get("色阶(硬)")
        stored = ramp.color_ramp.elements[0].color[0]
        check("色标存的是线性值", stored < 0.5, f"{stored:.4f}")
    except Exception as exc:
        check("颜色空间转换", False, f"{type(exc).__name__}: {exc}")

    # ---- 6. 渲染 A/B:有色阶 vs 无色阶 ----
    try:
        scene, obj = fresh_scene()
        toon = npr.build_toon_material("GK_AB_Toon", {
            "specular_amount": 0.0, "rim_amount": 0.0,
            "shadow_step": 0.15, "three_tone": 1.0, "shadow_smooth": 0.0})
        obj.data.materials.append(toon)
        colors_toon = measure(scene, os.path.join(tmp, "toon.png"))
        print(f"    卡通材质: 球心离散颜色 {len(colors_toon)} 种, "
              f"主色 {max(colors_toon.values())} 像素 / 共 225")

        scene, obj = fresh_scene()
        reference = bpy.data.materials.new("GK_AB_Ref")
        reference.use_nodes = True
        tree = reference.node_tree
        for node in list(tree.nodes):
            tree.nodes.remove(node)
        out = tree.nodes.new("ShaderNodeOutputMaterial")
        s2rgb = tree.nodes.new("ShaderNodeShaderToRGB")
        diffuse = tree.nodes.new("ShaderNodeBsdfDiffuse")
        emission = tree.nodes.new("ShaderNodeEmission")
        tree.links.new(diffuse.outputs["BSDF"], s2rgb.inputs["Shader"])
        tree.links.new(s2rgb.outputs["Color"], emission.inputs["Color"])
        tree.links.new(emission.outputs["Emission"], out.inputs["Surface"])
        obj.data.materials.append(reference)
        colors_ref = measure(scene, os.path.join(tmp, "smooth.png"))
        print(f"    对照(无色阶): 球心离散颜色 {len(colors_ref)} 种, "
              f"主色 {max(colors_ref.values())} 像素")

        check("卡通材质离散色明显更少",
              len(colors_toon) * 3 < len(colors_ref),
              f"{len(colors_toon)} vs {len(colors_ref)}")
        check("卡通材质存在单一主导色",
              max(colors_toon.values()) > 0.5 * 225,
              f"{max(colors_toon.values())}/225")
        check("对照没有主导色",
              max(colors_ref.values()) < 0.25 * 225,
              f"{max(colors_ref.values())}/225")
    except Exception as exc:
        check("渲染 A/B", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 7. 导出 Godot .tres ----
    try:
        # 重新建一个材质:上面的 A/B 渲染调过 read_factory_settings,
        # 会把之前创建的材质数据块一起清掉(ReferenceError)。
        scene, obj = fresh_scene()
        export_material = npr.build_toon_material("GK_Export_Toon", {
            "shadow_step": 0.2, "three_tone": 1.0, "rim_amount": 0.6})
        obj.data.materials.append(export_material)

        out_dir = os.path.join(tmp, "export")
        os.makedirs(out_dir, exist_ok=True)
        path, count = asset_export.write_npr_material(out_dir, export_material)
        check("导出 .tres", path is not None and os.path.isfile(path), str(path))
        if path:
            text = open(path, encoding="utf-8").read()
            print("-" * 70)
            print(text)
            print("-" * 70)
            check("引用卡通着色器", "gk_toon.gdshader" in text)
            check(f"写入全部参数({count})",
                  text.count("shader_parameter/") >= len(npr.PARAM_MAP),
                  str(text.count("shader_parameter/")))
            check("颜色写成 Color(r,g,b,1)", "shader_parameter/lit_tint = Color(" in text)
            check("数值写成浮点", "shader_parameter/shadow_step = " in text)
            # 关键:导出的必须是 sRGB 值(与 Godot 一致),不能是 Blender 的线性值
            check("导出的是 sRGB 颜色(不是线性值)",
                  "Color(0.706" in text or "Color(0.705" in text,
                  [line for line in text.splitlines() if "shadow_tint " in line][:1])
            # 改过的参数也应该带过去
            check("导出包含改动后的参数", "shadow_step = 0.200000" in text)
    except Exception as exc:
        check("导出 .tres", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    print(f"    输出目录: {tmp}")
    addon_utils.disable(module_name, default_set=True)
    return 0


try:
    code = main()
except Exception:
    traceback.print_exc()
    code = 1

passed = sum(1 for _, ok, _ in RESULTS if ok)
failed = [name for name, ok, _ in RESULTS if not ok]
print("=" * 70)
print(f"结果: {passed}/{len(RESULTS)} 通过")
if failed:
    print("失败项: " + ", ".join(failed))
print("NPR_PREVIEW=" + ("OK" if code == 0 and not failed else "FAILED"))
print("=" * 70)

sys.exit(0 if (code == 0 and not failed) else 1)
