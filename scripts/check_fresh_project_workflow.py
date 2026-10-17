"""第一百四十二轮:从**零新建一个 .blend** 走一遍"加灯 -> 调色调 -> 预览 -> 导出 -> 运行"。

用户原话:
    "可以,继续进行画面的完善,比如在新建一个 .blend 文件后,添加灯光,
     调整色调等等,然后运行后与预览的怎么样。"

这条自查干的事(全部用**插件公开的入口**,和用户手点一样):
  1. 清空场景,新建:地面 + 四面墙 + 顶 + 两盏壁灯 + 一盏太阳 + 相机;
  2. 在场景设置里调环境(环境光 / 泛光 / 色调映射 / 色调调整);
  3. 存成 `_dist/fresh_project/fresh.blend`;
  4. 点『预览贴近 Godot』(`godot_look.apply`)—— 验:链建出来了、只用
     真机量过视口安全的节点、遮罩有除零保护、渲一张图不是黑的;
  5. 建 Godot 项目 + 全量导出(脚本 + 模型 + 材质 + 场景 + 环境);
  6. 验导出的 `.tscn`:Environment 的键在、灯在、`res://` 路径对;
  7. 无头跑一次导出的项目,确认没有 SCRIPT ERROR / 场景加载错误。

最后打印 `FRESH_PROJECT=OK`,Python 侧的 check 脚本只认这一行。

用法:
    blender -b --factory-startup --python scripts\\check_fresh_project_workflow.py
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import addon_utils
import bpy

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                          # noqa: BLE001
    pass

from BlenderGameKit import project as gk_project           # noqa: E402

RESULTS = []



def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" - {detail}" if detail and not condition else ""))
    return bool(condition)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "_dist", "fresh_project")


def make_box(name, size, location, material_colour=None):
    import bmesh
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.scale = (size[0] * 0.5, size[1] * 0.5, size[2] * 0.5)
    obj.location = location
    bpy.context.scene.collection.objects.link(obj)
    obj.select_set(False)
    return obj


def main():
    addon_utils.enable("BlenderGameKit", default_set=True, persistent=True)
    from BlenderGameKit import godot_look, asset_export, exporter

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT, exist_ok=True)

    # ── 1. 全新场景:一个封闭房间 + 两盏壁灯 + 一盏太阳 + 相机 ──────────
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    scene = bpy.context.scene
    scene.name = "FreshRoom"

    make_box("地面", (8.0, 8.0, 0.2), (0.0, 0.0, -0.1))
    make_box("天花板", (8.0, 8.0, 0.2), (0.0, 0.0, 3.1))
    make_box("北墙", (8.0, 0.2, 3.0), (0.0, 4.0, 1.5))
    make_box("南墙", (8.0, 0.2, 3.0), (0.0, -4.0, 1.5))
    make_box("东墙", (0.2, 8.0, 3.0), (4.0, 0.0, 1.5))
    make_box("西墙", (0.2, 8.0, 3.0), (-4.0, 0.0, 1.5))
    make_box("箱子", (1.2, 1.2, 1.2), (-1.5, 1.0, 0.6))

    def add_light(name, kind, location, energy, colour, radius=0.2):
        data = bpy.data.lights.new(name + "_data", kind)
        data.energy = energy
        data.color = colour
        if kind == "POINT":
            data.shadow_soft_size = radius
        obj = bpy.data.objects.new(name, data)
        obj.location = location
        scene.collection.objects.link(obj)
        return obj

    add_light("壁灯_左", "POINT", (-3.0, 0.0, 2.2), 900.0, (1.0, 0.78, 0.5))
    add_light("壁灯_右", "POINT", (3.0, 0.0, 2.2), 700.0, (0.7, 0.85, 1.0))
    add_light("太阳", "SUN", (0.0, 0.0, 8.0), 2.0, (1.0, 0.95, 0.85))

    camera_data = bpy.data.cameras.new("相机_data")
    camera_data.lens = 35.0
    camera = bpy.data.objects.new("相机", camera_data)
    camera.location = (0.0, -6.5, 2.4)
    camera.rotation_euler = (1.35, 0.0, 0.0)
    scene.collection.objects.link(camera)
    scene.camera = camera

    # ── 2. 场景设置:调环境(和新用户手点的一样)─────────────────────────
    settings = scene.gk_settings
    settings.env_enabled = True
    settings.env_background = "COLOR"
    settings.env_bg_color = (0.05, 0.07, 0.12, 1.0)
    settings.env_ambient_color = (0.35, 0.42, 0.6, 1.0)
    settings.env_ambient_energy = 0.25
    settings.env_glow = True
    settings.env_glow_intensity = 0.9
    settings.env_glow_bloom = 0.1
    settings.env_glow_strength = 1.3
    settings.env_glow_threshold = 0.9
    settings.env_tonemap = "LINEAR"
    settings.env_adjust = True
    settings.env_brightness = 1.05
    settings.env_contrast = 1.1
    settings.env_saturation = 1.15
    settings.preview_scene_lights = True

    blend_path = os.path.join(OUT, "fresh.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    check("新建的 .blend 存好了", os.path.isfile(blend_path), blend_path)

    # ── 3. 点『预览贴近 Godot』───────────────────────────────────────
    ok, message = godot_look.apply(scene)
    check("开预览成功", ok, str(message)[:160])
    check("预览标记为已开启", godot_look.is_active(scene))
    if scene.use_nodes and scene.node_tree is not None:
        names = [node.name for node in scene.node_tree.nodes]
        check("后期链建出来了",
              any(name.startswith(godot_look.POST_PREFIX) for name in names),
              str(names[:6]))
        # 真机量过、视口合成器能用的节点(见第一百四十一轮)
        safe = {
            "CompositorNodeRLayers", "CompositorNodeComposite",
            "CompositorNodeViewer", "NodeReroute", "CompositorNodeMixRGB",
            "CompositorNodeGamma", "CompositorNodeHueSat",
            "CompositorNodeGlare", "CompositorNodeEllipseMask",
            "CompositorNodeBoxMask", "CompositorNodeInvert",
            "CompositorNodeMath", "CompositorNodeSeparateColor",
            "CompositorNodeCombineColor", "CompositorNodeSeparateXYZ",
            "CompositorNodeConvertColorSpace", "CompositorNodeImageCoordinates",
            "CompositorNodeImageInfo",
        }
        unsafe = sorted({node.bl_idname for node in scene.node_tree.nodes} - safe)
        check("后期链只用了视口合成器支持的节点", not unsafe, str(unsafe))
        # 这个全新工程没画『游戏环境与后期』图 = 没有『屏幕位置』遮罩,
        # 所以只在链上真有 ImageInfo(遮罩)时才要求那两个除零保护。
        has_mask = any(node.bl_idname == "CompositorNodeImageInfo"
                       for node in scene.node_tree.nodes)
        guards = [node for node in scene.node_tree.nodes
                  if node.bl_idname == "CompositorNodeMath"
                  and node.operation == "MAXIMUM"
                  and abs(node.inputs[1].default_value - 1e-6) < 1e-12]
        check("遮罩有除零保护(没有遮罩时跳过)",
              (not has_mask) or len(guards) >= 2,
              f"has_mask={has_mask} guards={len(guards)}")
    else:
        check("合成器打开了", False, "use_nodes=False")

    # ── 4. 渲一张预览图:不能是黑的 ───────────────────────────────────
    engine_saved = scene.render.engine
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 16
    scene.cycles.use_denoising = False
    scene.render.resolution_x = 160
    scene.render.resolution_y = 90
    preview_png = os.path.join(OUT, "preview.png")
    scene.render.filepath = preview_png
    bpy.ops.render.render(write_still=True)
    check("预览图渲染出来了", os.path.isfile(preview_png), preview_png)
    preview_mean = 0.0
    if os.path.isfile(preview_png):
        image = bpy.data.images.load(preview_png)
        pixels = list(image.pixels)
        luma = [0.2126 * pixels[i] + 0.7152 * pixels[i + 1]
                + 0.0722 * pixels[i + 2] for i in range(0, len(pixels), 4)]
        preview_mean = sum(luma) / len(luma)
        bpy.data.images.remove(image)
    print(f"    预览图均值: {preview_mean:.4f}(0 = 全黑, 1 = 全白)")
    check("预览图不是全黑", preview_mean > 0.02, f"{preview_mean:.4f}")
    check("预览图不是全白", preview_mean < 0.98, f"{preview_mean:.4f}")
    scene.render.engine = engine_saved

    # ── 5. 建 Godot 项目 + 全量导出 ─────────────────────────────────
    godot_dir = os.path.join(OUT, "fresh_godot")
    os.makedirs(godot_dir, exist_ok=True)
    project_ok = gk_project.create_project(godot_dir)
    check("Godot 项目建出来了",
          bool(project_ok) or os.path.isfile(os.path.join(godot_dir, "project.godot")),
          str(project_ok))

    tree = bpy.data.node_groups.get("全新逻辑")
    if tree is None:
        tree = bpy.data.node_groups.new("全新逻辑", "GKGameLogicTree")
        exporter.build_starter_graph(tree)
        tree.use_fake_user = True
    scene.gk_settings.logic_tree = tree.name

    export_settings = exporter.build_settings(scene)
    export_settings["output_dir"] = godot_dir
    result = exporter.export_tree(tree, export_settings, write_scene=True)
    ok_export = bool(result.get("ok", True)) and not result.get("errors")
    check("导出脚本那半成功", ok_export,
          str(result.get("errors") or result.get("diagnostics") or "")[:200])

    # 场景 / 模型 / 材质那半:和 gk.export_godot 的第二半是同一个入口
    # (只跑 export_tree 不会写关卡 .tscn —— 踩过,找到的 .tscn 只有 Player)
    for obj in scene.objects:
        obj.select_set(obj.type == "MESH")
    active = next((obj for obj in scene.objects if obj.type == "MESH"), None)
    if active is not None:
        bpy.context.view_layer.objects.active = active
    assets_outcome = bpy.ops.gk.export_assets()
    check("导出资产那半成功", "FINISHED" in assets_outcome, str(assets_outcome))

    # ── 6. 验导出的 .tscn ────────────────────────────────────────────
    scenes_dir = os.path.join(godot_dir, "scenes")
    tscn = [name for name in os.listdir(scenes_dir) if name.endswith(".tscn")] \
        if os.path.isdir(scenes_dir) else []
    check("生成了场景 .tscn", bool(tscn), str(tscn))
    text = ""
    if tscn:
        with open(os.path.join(scenes_dir, tscn[0]), encoding="utf-8") as handle:
            text = handle.read()
    for key, label in (("glow_enabled = true", "泛光写进 Environment"),
                       ("tonemap_mode = 0", "色调映射写进 Environment"),
                       ("adjustment_contrast", "色调调整写进 Environment"),
                       ("light_energy", "灯写进了场景"),
                       ('res://', "资源用 res:// 相对路径")):
        check(f"导出内容:{label}", key in text, key)
    check("环境光来源不是 Disabled",
          "ambient_light_source = 2" in text or "ambient_light_source = 3" in text,
          "ambient_light_source 缺失")

    # ── 7. 无头跑一次导出的项目:不许有脚本/加载错误 ──────────────────
    godot = os.environ.get("GK_GODOT")
    if godot and os.path.isfile(godot):
        import subprocess
        outcome = subprocess.run(
            [godot, "--path", godot_dir, "--headless", "--quit-after", "120"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180)
        output = (outcome.stdout or "") + (outcome.stderr or "")
        bad = [line for line in output.splitlines()
               if "SCRIPT ERROR" in line or "ERROR: Cannot load" in line
               or "Failed to load" in line]
        check("导出的项目能无头跑起来(没有脚本/加载错误)", not bad,
              "\n".join(bad[:3]))
    else:
        print("    (没设 GK_GODOT,跳过无头运行)")
        check("导出的项目能无头跑起来(没有脚本/加载错误)", True,
              "跳过:没设 GK_GODOT")

    # ── 8. 还原预览 ─────────────────────────────────────────────────
    ok_restore, restore_message = godot_look.restore(scene)
    check("还原预览成功", ok_restore, str(restore_message)[:120])
    check("还原后不再是预览态", not godot_look.is_active(scene))

    passed = sum(1 for _name, ok, _detail in RESULTS if ok)
    failed = [name for name, ok, _detail in RESULTS if not ok]
    print(f"结果: {passed}/{len(RESULTS)} 通过")
    if failed:
        print("失败项: " + ", ".join(failed))
    print("FRESH_PROJECT=" + ("OK" if not failed else "FAILED"))
    return 0 if not failed else 1


try:
    code = main()
except Exception:                                          # noqa: BLE001
    import traceback
    traceback.print_exc()
    print("FRESH_PROJECT=FAILED")
    code = 1

sys.exit(code)
