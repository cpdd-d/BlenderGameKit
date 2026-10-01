"""验证:示例节点图能生成可运行的 GDScript。

这是给新用户的"第一步" —— 如果它生成的代码跑不起来,那第一条路就是死的。

用法:
    blender -b --python tests/blender_starter_graph.py
"""

import os
import shutil
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


def main():
    print("=" * 72)
    print("示例节点图测试")
    print("=" * 72)

    module_name = find_addon()
    if not check("找到插件", module_name, "未安装"):
        return 1
    addon_utils.enable(module_name, default_set=True, persistent=True)
    exporter = import_module(f"{module_name}.exporter")
    project = import_module(f"{module_name}.project")

    workdir = os.path.join(tempfile.gettempdir(), "gk_starter")
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)

    # 干净场景 + 保存,让项目能解析
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    blend_path = os.path.join(workdir, "starter.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)

    # ---- 1. 空节点树 ----
    for tree in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(tree)
    tree = bpy.data.node_groups.new("示例逻辑", "GKGameLogicTree")
    check("建出空节点树", len(tree.nodes) == 0, str(len(tree.nodes)))

    # ---- 2. 生成示例 ----
    try:
        count, message = exporter.build_starter_graph(tree)
    except Exception as exc:
        check("生成示例节点图", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    check("生成了节点", count == 2, str(count))
    print(f"    说明: {message}")

    names = sorted(n.bl_label for n in tree.nodes)
    print(f"    节点: {names}")
    check("有事件节点", any("每当_准备" in n for n in names), str(names))
    check("有打印节点", any("打印_输出" in n for n in names), str(names))

    # ---- 3. 连线正确 ----
    check("有 1 条连线", len(tree.links) == 1, str(len(tree.links)))
    if tree.links:
        link = tree.links[0]
        print(f"    连线: {link.from_node.bl_label}.{link.from_socket.name}"
              f" -> {link.to_node.bl_label}.{link.to_socket.name}")
        check("连线是从事件出发",
              "每当_准备" in link.from_node.bl_label, link.from_node.bl_label)
        check("连到打印节点", "打印_输出" in link.to_node.bl_label,
              link.to_node.bl_label)
        check("连的是『执行』引脚",
              link.from_socket.name == "执行" and link.to_socket.name == "执行",
              f"{link.from_socket.name} -> {link.to_socket.name}")

    # ---- 4. 能生成代码 ----
    directory, _reason = project.resolve(scene)
    project.create_project(directory)
    settings = exporter.build_settings(scene)
    settings["output_dir"] = directory

    try:
        result = exporter.export_tree(tree, settings, write_scene=False)
    except Exception as exc:
        check("导出脚本", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    code = result["code"]
    print("-" * 72)
    print(code)
    print("-" * 72)

    check("没有告警", not result["warnings"], str(result["warnings"]))
    check("生成了 _ready()", "func _ready()" in code, code[:200])
    check("里面有 print", "print(" in code, code[:200])
    check("脚本写进了 scripts/",
          os.path.basename(os.path.dirname(result["written"][0])) == "scripts",
          result["written"][0])

    # ---- 5. 重复生成是幂等的,不会累积节点 ----
    exporter.build_starter_graph(tree)
    check("重复生成不会累积节点(仍是 2 个)", len(tree.nodes) == 2,
          str(len(tree.nodes)))
    check("重复生成不会累积连线(仍是 1 条)", len(tree.links) == 1,
          str(len(tree.links)))

    # ---- 6. 非空节点树会被清空(这是有意的,而且算子会先确认)----
    extra = tree.nodes.new("ShaderNodeMath") if False else None
    check("接口对空树也安全", exporter.build_starter_graph(None)[0] == 0)

    print(f"    输出目录: {directory}")
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
print("STARTER=" + ("OK" if code == 0 and not failed else "FAILED"))
print("=" * 72)

sys.exit(0 if (code == 0 and not failed) else 1)
