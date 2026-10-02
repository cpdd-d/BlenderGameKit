"""资产导出链路测试:Blender 建模型 -> 导出 .glb + 材质 + 装配场景。

用法:
    blender -b --python tests/blender_asset_export.py
    (不要加 --factory-startup，否则看不到用户扩展仓库)

验证的是"模型怎么和脚本搭载"这条链路:
    网格 + 材质  --glTF-->  .glb
    材质参数     --生成-->  .tres 卡通材质
    节点图脚本   --装配-->  .tscn (模型 + 碰撞体 + 脚本)
"""

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


def prepare_empty_scene():
    """清空场景里的网格数据。

    不要用 bpy.ops.wm.read_factory_settings():它会把整个偏好设置(含
    bl_ext 扩展命名空间)一起重置，导致之前拿到的模块名失效、插件类型消失。
    手动删数据更安全。
    """
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def tree_type_available():
    """能不能真的建出 GK 节点树 —— 这比查 bpy.types 可靠。

    bpy.types 的属性表里看不到动态注册的 Python 类，dir()/hasattr 都不准。
    """
    try:
        probe = bpy.data.node_groups.new("__gk_probe__", "GKGameLogicTree")
        bpy.data.node_groups.remove(probe)
        return True
    except Exception:
        return False


def make_test_character():
    """建一个带两种材质的测试角色(躯干 + 头)。"""
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 1.0))

    # 身体:立方体拉伸一下当躯干
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 1.0))
    body = bpy.context.active_object
    body.name = "Body"
    body.scale = (0.35, 0.25, 0.5)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    # 头:球体
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.3, location=(0, 0, 1.75))
    head = bpy.context.active_object
    head.name = "Head"

    # 身体材质
    body_mat = bpy.data.materials.new("SkinCloth")
    body_mat.use_nodes = True
    bsdf = body_mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.95, 0.82, 0.85, 1.0)
    body.data.materials.append(body_mat)

    # 头部材质:带一段纯色,并挂一张程序生成的贴图来验证贴图通道
    head_mat = bpy.data.materials.new("FaceSkin")
    head_mat.use_nodes = True
    bsdf = head_mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (1.0, 0.92, 0.88, 1.0)
    head.data.materials.append(head_mat)

    return [body, head]


def main():
    print("=" * 70)
    print("BlenderGameKit 资产导出测试")
    print("=" * 70)

    module_name = find_addon()
    if not check("找到插件", module_name, "未安装"):
        return 1

    prepare_empty_scene()
    addon_utils.enable(module_name, default_set=True, persistent=True)
    check("插件已启用且节点树类型可用", tree_type_available())

    asset_export = import_module(f"{module_name}.asset_export")
    node_lib = import_module(f"{module_name}.nodes.nodes")
    codegen = import_module(f"{module_name}.codegen")
    exporter = import_module(f"{module_name}.exporter")

    out_dir = os.path.join(tempfile.gettempdir(), "BlenderGameKit_assets")
    os.makedirs(out_dir, exist_ok=True)

    # ---- 1. 建模型 ----
    try:
        meshes = make_test_character()
        check(f"创建网格 ({len(meshes)})", len(meshes) == 2)
        for mesh in meshes:
            print(f"    {mesh.name}: 材质 {[m.name for m in mesh.data.materials]}")
    except Exception as exc:
        check("创建网格", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    check("建完模型后节点树类型仍可用", tree_type_available())

    # ---- 2. 包围盒(用于生成碰撞体)----
    bounds = asset_export.object_bounds(meshes)
    check("算出包围盒", bounds is not None)
    if bounds:
        size = bounds["size"]
        print(f"    包围盒尺寸: {size[0]:.3f} x {size[1]:.3f} x {size[2]:.3f}")
        # Blender 是 Z 轴向上:躯干半高 0.5 在 z=1.0，头半径 0.3 在 z=1.75，
        # 所以 Z 向总高约 1.3
        check("包围盒高度覆盖躯干+头", 1.2 < size[2] < 1.4, f"{size}")
        check("包围盒宽度合理", 0.5 < size[0] < 0.7, f"{size}")

    # ---- 3. 导出 glTF ----
    path, ok, message = asset_export.export_glb(out_dir, "TestCharacter", False, meshes)
    check("导出 .glb", ok and os.path.isfile(path), message)
    if ok:
        print(f"    {path} ({os.path.getsize(path):,} 字节)")
        check(".glb 不是空文件", os.path.getsize(path) > 1000)

    # ---- 4. 生成卡通材质 ----
    try:
        materials = asset_export.write_materials(out_dir, meshes)
        check(f"生成卡通材质 ({len(materials)})", len(materials) == 2, str(materials))
        for name, mat_path, has_tex in materials:
            print(f"    {name} -> {os.path.basename(mat_path)} (贴图: {has_tex})")
        if materials:
            with open(materials[0][1], encoding="utf-8") as handle:
                text = handle.read()
            check("材质引用了卡通着色器", "gk_toon.gdshader" in text)
            check("材质含主色参数", "albedo_color = Color(" in text)
    except Exception as exc:
        check("生成卡通材质", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    # ---- 5. 复制着色器 ----
    shaders = asset_export.copy_shaders(out_dir)
    check(f"复制 NPR 着色器 ({len(shaders)})", len(shaders) == 3, str(shaders))

    # ---- 6. 先导出一份脚本(模拟真实流程)----
    tree = bpy.data.node_groups.new("角色逻辑", "GKGameLogicTree")
    ready = tree.nodes.new("GKNodeOnReady")
    printer = tree.nodes.new("GKNodePrint")
    tree.links.new(ready.outputs["执行"], printer.inputs["执行"])
    settings = exporter.build_settings(bpy.context.scene)
    settings["output_dir"] = out_dir
    settings["dimension"] = "3"
    settings["base_class"] = "CharacterBody3D"
    result = exporter.export_tree(tree, settings, write_scene=False)
    script_name = result["script_name"]
    check("导出脚本", os.path.isfile(os.path.join(out_dir, script_name)))

    # ---- 7. 装配场景:模型 + 碰撞体 + 脚本 + 相机灯光 ----
    scene_result = asset_export.write_character_scene(
        out_dir,
        root_name="TestHero",
        base_class="CharacterBody3D",
        model_name="TestCharacter",
        script_name=script_name,
        bounds=bounds,
        dimension="3",
        scene=bpy.context.scene,
    )
    check("write_character_scene 返回三元组", len(scene_result) == 3,
          str(scene_result))
    scene_path, cam_count, light_count = scene_result
    check("生成装配场景", os.path.isfile(scene_path))
    # 场景应该在 scenes/ 子目录里(项目结构:scripts/ scenes/ art/ materials/ shaders/)
    check("场景放在 scenes/ 下",
          os.path.basename(os.path.dirname(scene_path)) == "scenes",
          scene_path)
    with open(scene_path, encoding="utf-8") as handle:
        scene_text = handle.read()
    # .tscn 里引用资源必须用 res:// 路径,且模型在 art/、脚本在 scripts/
    check("场景引用了模型(art/)", 'path="res://art/TestCharacter.glb"' in scene_text,
          [ln for ln in scene_text.splitlines() if "glb" in ln][:1])
    # ── 脚本挂在哪:角色场景**故意不挂** ────────────────────────────
    # 历史(踩了两轮才想清楚):
    #   1. 最初角色场景根节点挂脚本,World 场景里包角色的那个节点**也**挂
    #      -> `_ready()` 跑两遍(逻辑双份,角色双倍速移动)
    #   2. 试着只在角色场景挂 -> **逻辑整个不跑**。因为 Godot 实例化场景时
    #      **不继承**被实例化场景根节点的 `script` 属性(实测 script=<null>)
    #   3. 定案:脚本挂 World 那个节点,**角色场景不挂**。
    #      角色场景在这个工作流里是子场景,不是给人按 F5 直接跑的。
    check("角色场景不挂游戏脚本(避免跑两遍)",
          f'path="res://scripts/{script_name}"' not in scene_text,
          [ln for ln in scene_text.splitlines() if ".gd" in ln][:2])
    check("角色场景仍然引用模型(art/)",
          'path="res://art/TestCharacter.glb"' in scene_text,
          [ln for ln in scene_text.splitlines() if "glb" in ln][:1])
    check("场景含碰撞体", "CollisionShape3D" in scene_text and "CapsuleShape3D" in scene_text)
    check("场景根节点是角色基类", 'type="CharacterBody3D"' in scene_text)

    project = asset_export.ensure_project_file(out_dir)
    check("导出目录有 project.godot",
          project is not None and os.path.isfile(project), str(project))

    # ---- 10. 角色场景必须有 Environment 节点 ----
    #
    # 回归(用户实测"材质没生效"的真正原因):
    # `build_environment_nodes` 有两条分支 ——
    #   情况 1:相机灯都在 glb 里 -> 生成 Environment 节点
    #   情况 2:自己补相机灯     -> **只补相机灯,不生成 Environment**
    # 而角色 glb 是刻意不带相机灯光的(export_subtree_glb 里
    # export_cameras/lights=False),所以角色场景**永远**走情况 2 →
    # 卡通材质永远挂不上,用户看到的还是 glb 自带的默认材质。
    #
    # 另外 ext_resource 的判据也错过:以前拿 env_files 非空当判据,
    # 但情况 1 不重写脚本文件,于是节点引用了没声明的 ExtResource。
    print("-" * 70)
    print("检查角色场景的 Environment 节点")
    char_scene, _c2, _l2 = asset_export.write_character_scene(
        out_dir,
        root_name="EnvHero",
        base_class="CharacterBody3D",
        model_name="SoloHero",
        script_name=script_name,
        bounds=bounds,
        dimension="3",
        scene=bpy.context.scene,
        set_as_main=False,
        materials_map={"SoloHero": ("HeroMat", "HeroMat.tres")},
    )
    with open(char_scene, encoding="utf-8") as handle:
        char_text = handle.read()
    print(char_text)

    check("角色场景有 Environment 节点",
          '[node name="Environment"' in char_text,
          [ln for ln in char_text.splitlines() if "Environment" in ln])
    check("Environment 节点引用了 env_script",
          'ExtResource("env_script")' in char_text)
    # 引用了 ExtResource 就必须有对应的 ext_resource 声明,
    # 否则 Godot 报 "Can't load resource ... env_script"
    if 'ExtResource("env_script")' in char_text:
        check("env_script 有对应的 ext_resource 声明",
              'id="env_script"' in char_text,
              [ln for ln in char_text.splitlines() if "env_script" in ln])
    check("Environment 里带上了材质映射",
          "materials = " in char_text and "HeroMat" in char_text,
          [ln for ln in char_text.splitlines() if "materials" in ln])

    # ---- 8. 角色必须导出成**自己的** glb ----
    #
    # 回归:以前角色场景 instance 的是整个 World.glb,导致
    #   * 一移动角色,整个世界跟着跑(地面、道具全飞了)
    #   * Godot 不允许往实例内部挂节点,碰撞体也就挂不到角色网格上
    #     (实测报 "Parent path './World/Suzanne' has vanished")
    # 正确做法:角色(含子级)单独一个 glb,角色场景 instance 它。
    print("-" * 70)
    print("检查角色单独导出")
    hero_path, hero_ok, hero_msg = asset_export.export_subtree_glb(
        out_dir, "SoloHero", list(meshes), scene=bpy.context.scene)
    check("角色单独导出成功", hero_ok, hero_msg)
    check("角色 glb 落在 art/ 下",
          bool(hero_path) and os.path.basename(os.path.dirname(hero_path)) == "art",
          str(hero_path))
    check("角色 glb 不是空文件",
          bool(hero_path) and os.path.getsize(hero_path) > 100,
          f"{os.path.getsize(hero_path) if hero_path else 0} 字节")

    solo_scene, _c, _l = asset_export.write_character_scene(
        out_dir,
        root_name="SoloHero",
        base_class="CharacterBody3D",
        model_name="SoloHero",
        script_name=script_name,
        bounds=bounds,
        dimension="3",
        scene=bpy.context.scene,
        set_as_main=False,
    )
    with open(solo_scene, encoding="utf-8") as handle:
        solo_text = handle.read()
    check("角色场景 instance 角色自己的 glb",
          'path="res://art/SoloHero.glb"' in solo_text)
    # 这条是关键:不能把整个世界塞到角色下面
    check("角色场景没有把整个 World 塞进来",
          "World.glb" not in solo_text,
          [ln for ln in solo_text.splitlines() if "glb" in ln])
    # 碰撞体和模型必须是根的子级(同父级才会一起动)
    check("碰撞体是根节点的子级", 'parent="."' in solo_text and
          "CollisionShape3D" in solo_text)
    check("模型是根节点的子级",
          '[node name="Model" parent="." instance=' in solo_text)

    # ---- 9. 没有材质的网格必须被报出来 ----
    #
    # 回归:用户建了个 Cube 当角色、忘了给材质,导出的角色在 Godot 里
    # **一片灰**,他以为渲染坏了。这不是渲染问题,是那个物体在 Blender 里
    # 就没有材质 —— 而 .glb 里也就没有材质,Godot 用默认材质渲染。
    # 不报出来的话用户完全想不到要去加材质。
    print("-" * 70)
    print("检查『没有材质』的检测")
    no_mat = asset_export.meshes_without_material(bpy.context.scene)
    print(f"    场景里没有材质的网格: {no_mat}")
    check("有材质的网格不被误报", "TestCharacter" not in no_mat, str(no_mat))

    # 专门造一个干净物体来验:先没材质 -> 该被报;加材质 -> 不该被报。
    # (不用场景里现成的物体,因为它们可能本来就没有材质,
    #  断言会分不清"检测对不对"和"场景本来就那样"。)
    probe_mesh = bpy.data.meshes.new("MaterialProbeMesh")
    probe = bpy.data.objects.new("MaterialProbe", probe_mesh)
    bpy.context.scene.collection.objects.link(probe)
    bm2 = bmesh.new()
    bmesh.ops.create_cube(bm2, size=0.5)
    bm2.to_mesh(probe_mesh)
    bm2.free()

    caught = asset_export.meshes_without_material(bpy.context.scene)
    check("无材质的物体被报出来", "MaterialProbe" in caught, str(caught))

    probe_mat = bpy.data.materials.new("ProbeMat")
    probe_mat.use_nodes = True
    probe_mesh.materials.append(probe_mat)
    after = asset_export.meshes_without_material(bpy.context.scene)
    check("加了材质后不再被报", "MaterialProbe" not in after, str(after))

    # ── .tres 里不能有任何 `#` 注释(血的教训)──────────────────────
    # 曾经把说明性注释插进 TOON_MATERIAL_TEMPLATE 的 shader_parameter
    # 中间,结果 Godot 对**每一个材质**报
    #     Parse Error: Unexpected identifier 'rim_color'.
    #     Failed loading resource: res://materials/material.tres
    # `.tres` 是资源格式不是脚本,解析器只认 `[段]` 和 `键 = 值`,
    # 见到 `#` 直接判语法错误 —— 所有材质加载失败,模型变回白模。
    # 这里逐行检查生成的 .tres,把这条钉死。
    tres_files = []
    materials_dir = os.path.join(out_dir, "materials")
    if os.path.isdir(materials_dir):
        for name in sorted(os.listdir(materials_dir)):
            if name.endswith(".tres"):
                tres_files.append(os.path.join(materials_dir, name))
    check(".tres 材质文件已生成", len(tres_files) > 0, str(len(tres_files)))

    bad_lines = []
    for path in tres_files:
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                stripped = line.strip()
                # `[` 开头的段头、空行、`键 = 值` 之外都算可疑
                if not stripped or stripped.startswith("["):
                    continue
                if "#" in stripped or ";" in stripped:
                    bad_lines.append(f"{os.path.basename(path)}:{number} {stripped}")
    check(".tres 里没有 # 注释(会让整个材质加载失败)",
          not bad_lines, "; ".join(bad_lines[:3]))

    # 每一条 shader_parameter 都必须是合法的 `键 = 值`
    malformed = []
    for path in tres_files:
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("["):
                    continue
                if "=" not in stripped:
                    malformed.append(f"{os.path.basename(path)}:{number} {stripped}")
    check(".tres 每行都是 键 = 值", not malformed, "; ".join(malformed[:3]))

    # rim / 高光默认必须是 0(否则加法项会把贴图盖成白模)
    if tres_files:
        with open(tres_files[0], encoding="utf-8") as handle:
            body = handle.read()
        check("rim_amount 默认 0",
              "shader_parameter/rim_amount = 0.0" in body)
        check("specular_amount 默认 0",
              "shader_parameter/specular_amount = 0.0" in body)

    # 清理
    bpy.data.objects.remove(probe, do_unlink=True)
    if probe_mesh.users == 0:
        bpy.data.meshes.remove(probe_mesh)

    print("-" * 70)
    print(scene_text)
    print("-" * 70)
    print("导出目录内容:")
    for root, _dirs, files in os.walk(out_dir):
        for name in sorted(files):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, out_dir)
            print(f"    {rel}  ({os.path.getsize(full):,} 字节)")
    print(f"ASSET_EXPORT_DIR={out_dir}")

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
print("ASSET_RESULT=" + ("OK" if code == 0 and not failed else "FAILED"))
print("=" * 70)

sys.exit(0 if (code == 0 and not failed) else 1)
