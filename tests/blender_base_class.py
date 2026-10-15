"""脚本基类必须和节点类型一致。

为什么专门测这个:两者不一致时 Godot 会**拒绝加载脚本** ——
    Script inherits from native type 'StaticBody3D',
    so it can't be assigned to an object of type: 'Area3D'
然后整个场景加载失败,游戏根本跑不起来。用户被一堆英文报错拦住,
根本想不到是 Blender 这边一个下拉框引起的。

实测踩过:物体勾了『作为触发器』-> 节点变成 Area3D,
而脚本还是 StaticBody3D。

用法:
    blender -b --python tests/blender_base_class.py
"""

import os
import re
import sys
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


def main():
    module_name = find_addon()
    if not check("找到插件", module_name, "未安装"):
        return 1
    addon_utils.enable(module_name, default_set=True, persistent=True)
    props = import_module(f"{module_name}.properties")
    exporter = import_module(f"{module_name}.exporter")
    asset_export = import_module(f"{module_name}.asset_export")

    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)

    mesh = bpy.data.meshes.new("BoxMesh")
    box = bpy.data.objects.new("Box", mesh)
    scene.collection.objects.link(box)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(mesh)
    bm.free()
    mesh.materials.append(bpy.data.materials.new("M"))

    settings = scene.gk_settings

    # ---- 1. 默认是"自动" ----
    print("=" * 72)
    print("1. 默认值")
    print("=" * 72)
    print(f"    场景基类 = {settings.base_class!r}")
    check("基类默认是『自动』", settings.base_class == props.BASE_CLASS_AUTO,
          settings.base_class)

    # ---- 2. 各种物理类型 -> 解析出来的基类 ----
    print("=" * 72)
    print("2. 物体类型决定脚本基类")
    print("=" * 72)
    cases = [
        ({"body_type": "StaticBody3D", "is_trigger": False}, "StaticBody3D"),
        ({"body_type": "RigidBody3D", "is_trigger": False}, "RigidBody3D"),
        ({"body_type": "CharacterBody3D", "is_trigger": False}, "CharacterBody3D"),
        # 触发器必须变成 Area —— 这就是踩过的那个坑
        ({"body_type": "StaticBody3D", "is_trigger": True}, "Area3D"),
        ({"body_type": "CharacterBody3D", "is_trigger": True}, "Area3D"),
    ]
    box.gk_physics.enabled = True
    box.gk_physics.script_target = True
    for values, want in cases:
        for key, value in values.items():
            setattr(box.gk_physics, key, value)
        got = props.resolve_base_class(scene, box, box.gk_physics, "3")
        label = f"{values['body_type']}{'+触发器' if values['is_trigger'] else ''}"
        print(f"    {label} -> {got}")
        check(f"{label} 解析成 {want}", got == want, str(got))

    # ---- 3. 自动模式下导出,脚本和节点必须一致 ----
    print("=" * 72)
    print("3. 自动模式:脚本科和节点类型必须一致")
    print("=" * 72)
    box.gk_physics.body_type = "StaticBody3D"
    box.gk_physics.is_trigger = True      # -> 节点会是 Area3D
    settings.base_class = props.BASE_CLASS_AUTO
    check("自动模式下没有隐患", not props.base_class_mismatches(scene, "3"),
          str(props.base_class_mismatches(scene, "3")))

    resolved = exporter.resolve_base_class(scene)
    print(f"    解析出的基类 = {resolved}")
    check("解析成 Area3D", resolved == "Area3D", resolved)

    # ---- 4. 手选基类和节点冲突时,要能报出来 ----
    print("=" * 72)
    print("4. 手选冲突时能报出来")
    print("=" * 72)
    settings.base_class = "StaticBody3D"    # 和 Area3D 节点冲突
    # 只有脚本实际挂载的节点需要匹配 extends。普通静态地形即使启用碰撞,
    # 也没有挂角色脚本,不应产生误报警告。
    box.gk_physics.script_target = False
    check("未挂脚本的对象不会报警",
          not props.base_class_mismatches(scene, "3"),
          str(props.base_class_mismatches(scene, "3")))
    box.gk_physics.script_target = True
    clashes = props.base_class_mismatches(scene, "3")
    print(f"    检测到 {len(clashes)} 个冲突: {clashes}")
    check("检测到冲突", len(clashes) >= 1, str(clashes))
    if clashes:
        name, chosen, actual = clashes[0]
        check("冲突里写明了脚本基类和实际类型",
              chosen == "StaticBody3D" and actual == "Area3D",
              f"{chosen} vs {actual}")

    # ---- 5. 修好之后不再有冲突 ----
    settings.base_class = props.BASE_CLASS_AUTO
    check("改成自动后冲突消失",
          not props.base_class_mismatches(scene, "3"),
          str(props.base_class_mismatches(scene, "3")))

    # ---- 6. 端到端:导出的 .gd 和 .tscn 对得上 ----
    print("=" * 72)
    print("6. 端到端:脚本的 extends 和节点的 type")
    print("=" * 72)
    import shutil
    import tempfile

    workdir = os.path.join(tempfile.gettempdir(), "gk_baseclass")
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(workdir, "bc.blend"))
    directory, _reason = import_module(f"{module_name}.project").resolve(scene)

    tree = bpy.data.node_groups.new("逻辑", "GKGameLogicTree")
    exporter.build_starter_graph(tree)
    tree.use_fake_user = True
    code_settings = exporter.build_settings(scene)
    code_settings["output_dir"] = directory
    result = exporter.export_tree(tree, code_settings, write_scene=False)

    outcome = asset_export.export_assets_pipeline(
        scene, directory, dimension="3",
        script_name=result["script_name"])
    check("导出流程无错误", not outcome.get("error"), str(outcome.get("error")))

    script_path = os.path.join(directory, "scripts", result["script_name"])
    if os.path.isfile(script_path):
        with open(script_path, encoding="utf-8") as handle:
            script_text = handle.read()
        match = re.search(r"^extends (\w+)", script_text, re.M)
        script_base = match.group(1) if match else None
        print(f"    脚本: extends {script_base}")

        world_scene = os.path.join(directory, "scenes", "World.tscn")
        with open(world_scene, encoding="utf-8") as handle:
            scene_text = handle.read()
        # 找到挂了脚本的那个节点
        nodes = re.findall(
            r'\[node name="([^"]+)" type="(\w+)" parent="Colliders"\]\s*\n'
            r'transform[^\n]*\n(?:[^\n]*\n)*?script = ExtResource', scene_text)
        print(f"    挂了脚本的节点: {nodes}")
        check("有节点挂了脚本", len(nodes) >= 1, str(nodes))
        for name, node_type in nodes:
            print(f"      {name}: 节点是 {node_type},脚本 extends {script_base}")
            check(f"{name} 的脚本基类和节点类型一致",
                  node_type == script_base,
                  f"节点 {node_type} != 脚本 {script_base}")
    else:
        check("生成了脚本", False, script_path)

    addon_utils.disable(module_name, default_set=True)
    return 0


try:
    code = main()
except Exception:
    traceback.print_exc()
    code = 1

passed = sum(1 for _, ok, _ in RESULTS if ok)
failed = [name for name, ok, _ in RESULTS if not ok]
print("=" * 72)
print(f"结果: {passed}/{len(RESULTS)} 通过")
if failed:
    print("失败项: " + ", ".join(failed))
print("BASE_CLASS=" + ("OK" if code == 0 and not failed else "FAILED"))
print("=" * 72)

sys.exit(0 if (code == 0 and not failed) else 1)
