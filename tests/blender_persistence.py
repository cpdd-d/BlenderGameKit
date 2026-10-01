"""验证:关掉 .blend 再打开,状态不丢。

用户报的原话是"关闭后重新打开会变成之前的样子"。这个测试直接模拟
"导出 -> 保存 -> 重开",检查:
  1. 上次导出信息还在(面板上那条"上次导出: xxx.gd")
  2. 『打开导出目录』还指得对
  3. 项目绑定还在
  4. 物体上的碰撞设置还在

用法:
    blender -b --python tests/blender_persistence.py
"""

import os
import shutil
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


def main():
    print("=" * 72)
    print("持久化测试:关掉再打开,状态还在吗")
    print("=" * 72)

    module_name = find_addon()
    if not check("找到插件", module_name, "未安装"):
        return 1
    addon_utils.enable(module_name, default_set=True, persistent=True)
    ops = import_module(f"{module_name}.operator_io")
    exporter = import_module(f"{module_name}.exporter")
    project = import_module(f"{module_name}.project")
    asset_export = import_module(f"{module_name}.asset_export")

    workdir = os.path.join(tempfile.gettempdir(), "gk_persist")
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)

    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for tree in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(tree)

    # ---- 造场景:一个球 + 一棵节点树 ----
    mesh = bpy.data.meshes.new("BallMesh")
    ball = bpy.data.objects.new("Ball", mesh)
    scene.collection.objects.link(ball)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=0.6)
    bm.to_mesh(mesh)
    bm.free()
    ball.location = (1.0, 2.0, 3.0)
    ball.gk_physics.collision_shape = "capsule"
    ball.gk_physics.collision_layer = "4"
    ball.gk_physics.script_target = True

    tree = bpy.data.node_groups.new("持久化逻辑", "GKGameLogicTree")
    exporter.build_starter_graph(tree)
    # **必须给 fake user**。程序化创建的节点树没有用户,Blender 在保存时
    # 会把它当垃圾数据丢掉 —— 表现就是"重开后节点树不见了"。
    # 用户在节点编辑器里新建的树会自动被编辑器引用,所以真实场景不会遇到;
    # 但测试里必须显式加,否则测的是 Blender 的垃圾回收而不是我们的持久化。
    tree.use_fake_user = True

    blend_path = os.path.join(workdir, "persist.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)

    directory, _reason = project.resolve(scene)
    project.create_project(directory)

    # ---- 1. 导出一次 ----
    settings = exporter.build_settings(scene)
    settings["output_dir"] = directory
    result = exporter.export_tree(tree, settings, write_scene=True)
    ops.remember_export(scene, result)
    check("导出后有记录", bool(ops.LAST_EXPORT), str(ops.LAST_EXPORT.keys()))
    exported_dir = ops.LAST_EXPORT.get("directory", "")
    print(f"    导出目录: {exported_dir}")
    print(f"    脚本: {ops.LAST_EXPORT.get('script_name')}")

    # 场景自定义属性里应该有东西了
    raw = scene.get("gk_last_export")
    check("写进了场景自定义属性", bool(raw), str(raw))
    if raw:
        print(f"    存的 JSON: {raw}")

    bpy.ops.wm.save_mainfile()

    # ---- 2. 模拟"关掉再打开" ----
    # 清掉内存里的状态,再重新打开文件 —— 这才是真实的"重开"路径
    ops.LAST_EXPORT.clear()
    check("清空后内存里没了", not ops.LAST_EXPORT)

    bpy.ops.wm.open_mainfile(filepath=blend_path)
    # open_mainfile 会触发 load_post handler,恢复应该自动发生
    restored = dict(ops.LAST_EXPORT)
    print(f"    重开后恢复的内容: {restored}")
    check("重开后上次导出信息回来了", bool(restored), str(restored))
    check("重开后导出目录正确",
          restored.get("directory") == exported_dir,
          f"{restored.get('directory')} != {exported_dir}")
    check("重开后脚本名正确",
          restored.get("script_name") == result["script_name"],
          str(restored.get("script_name")))

    # ---- 3. 项目绑定还在 ----
    project_after, reason_after = project.resolve(bpy.context.scene)
    check("重开后项目绑定还在",
          os.path.normcase(project_after) == os.path.normcase(directory),
          f"{project_after} != {directory}")
    check("重开后项目状态仍然是『就绪』",
          project.project_summary(project_after)[0] == "就绪",
          project.project_summary(project_after)[0])

    # ---- 4. 物体上的碰撞设置还在 ----
    ball_after = bpy.data.objects.get("Ball")
    check("重开后找得到物体", ball_after is not None)
    if ball_after is not None:
        physics = ball_after.gk_physics
        print(f"    Ball 碰撞设置: shape={physics.collision_shape} "
              f"layer={physics.collision_layer} script={physics.script_target}")
        check("碰撞形状还在", physics.collision_shape == "capsule",
              physics.collision_shape)
        check("碰撞层还在", physics.collision_layer == "4",
              physics.collision_layer)
        check("『挂脚本』勾选还在", bool(physics.script_target))
        check("物体位置还在",
              abs(ball_after.location.x - 1.0) < 0.01
              and abs(ball_after.location.y - 2.0) < 0.01
              and abs(ball_after.location.z - 3.0) < 0.01,
              str(tuple(ball_after.location)))

    # ---- 5. 节点树还在且没被"变回原样" ----
    tree_after = bpy.data.node_groups.get("持久化逻辑")
    check("重开后节点树还在", tree_after is not None)
    if tree_after is not None:
        print(f"    节点数: {len(tree_after.nodes)}  连线数: {len(tree_after.links)}")
        check("节点没丢", len(tree_after.nodes) == 2, str(len(tree_after.nodes)))
        check("连线没丢", len(tree_after.links) == 1, str(len(tree_after.links)))

    # ---- 6. 重开后重新导出,仍然落在同一个项目里 ----
    settings2 = exporter.build_settings(bpy.context.scene)
    check("重开后 build_settings 仍指向项目",
          settings2.get("output_dir") == directory,
          str(settings2.get("output_dir")))

    print(f"    工作目录: {workdir}")
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
print("PERSIST=" + ("OK" if code == 0 and not failed else "FAILED"))
print("=" * 72)

sys.exit(0 if (code == 0 and not failed) else 1)
