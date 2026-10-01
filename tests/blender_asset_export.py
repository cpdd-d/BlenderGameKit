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
    check("场景引用了脚本(scripts/)", f'path="res://scripts/{script_name}"' in scene_text,
          [ln for ln in scene_text.splitlines() if ".gd" in ln][:1])
    check("场景含碰撞体", "CollisionShape3D" in scene_text and "CapsuleShape3D" in scene_text)
    check("场景根节点是角色基类", 'type="CharacterBody3D"' in scene_text)

    project = asset_export.ensure_project_file(out_dir)
    check("导出目录有 project.godot",
          project is not None and os.path.isfile(project), str(project))

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
