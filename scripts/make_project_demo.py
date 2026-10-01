"""演示:一个 .blend 如何对应出一个完整的 Godot 项目。

用法(必须带一个输出目录参数):
    blender -b --python scripts/make_project_demo.py -- D:\\demo

会在指定目录下建:
    demo.blend                <- 假装是你的 Blender 工程
    demo_godot/               <- 配套的 Godot 项目

然后打印整个项目树和关键文件内容,让你看清"一个 .blend 一个项目"
到底产出什么。
"""

import os
import sys
import traceback
from importlib import import_module

import addon_utils
import bmesh
import bpy


def find_addon():
    for module in addon_utils.modules():
        if "blender_game_kit" in getattr(module, "__name__", ""):
            return module.__name__
    return None


def argv_after_dashes():
    if "--" in sys.argv:
        return sys.argv[sys.argv.index("--") + 1:]
    return []


def print_tree(root, prefix=""):
    """打印目录树。"""
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return
    # 目录在前
    entries.sort(key=lambda name: (not os.path.isdir(os.path.join(root, name)), name))
    for index, name in enumerate(entries):
        path = os.path.join(root, name)
        last = index == len(entries) - 1
        branch = "└── " if last else "├── "
        if os.path.isdir(path):
            print(f"{prefix}{branch}{name}/")
            print_tree(path, prefix + ("    " if last else "│   "))
        else:
            size = os.path.getsize(path)
            print(f"{prefix}{branch}{name}  ({size} 字节)")


def main():
    args = argv_after_dashes()
    if not args:
        print("用法: blender -b --python scripts/make_project_demo.py -- <输出目录>")
        return 1
    outdir = os.path.abspath(args[0])
    os.makedirs(outdir, exist_ok=True)

    module_name = find_addon()
    if not module_name:
        print("找不到插件,请先安装")
        return 1
    addon_utils.enable(module_name, default_set=True, persistent=True)
    project = import_module(f"{module_name}.project")
    exporter = import_module(f"{module_name}.exporter")

    print("=" * 74)
    print("BlenderGameKit 项目演示")
    print("=" * 74)

    # ---- 1. 造一个像模像样的场景:一个角色网格 ----
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    mesh = bpy.data.meshes.new("HeroMesh")
    hero = bpy.data.objects.new("Hero", mesh)
    scene.collection.objects.link(hero)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=0.8)
    uv_layer = bm.loops.layers.uv.new("UVMap")
    for face in bm.faces:
        for loop in face.loops:
            co = loop.vert.co
            loop[uv_layer].uv = ((co.x + 1) * 0.5, (co.z + 1) * 0.5)
    bm.to_mesh(mesh)
    bm.free()
    print(f"\n[1] 建好角色网格: {hero.name}（{len(mesh.vertices)} 顶点）")

    # ---- 2. 造一棵游戏逻辑节点树 ----
    tree = bpy.data.node_groups.new("玩家逻辑", "GKGameLogicTree")
    print(f"[2] 建好逻辑节点树: {tree.name}")

    # ---- 3. 保存 .blend ----
    blend_path = os.path.join(outdir, "demo.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    print(f"[3] 保存 .blend -> {blend_path}")

    # ---- 4. 看它推导出什么项目目录 ----
    directory, reason = project.resolve(scene)
    print(f"[4] 推导出的项目目录: {directory}")
    print(f"    （来源: {reason}）")

    # ---- 5. 创建项目 ----
    created = project.create_project(
        directory,
        title=project.safe_project_name(project.blend_name() or "", "game"),
        blend_file_name=os.path.basename(blend_path),
    )
    print(f"[5] 创建项目完成,新增 {len(created)} 项")

    # ---- 6. 导出脚本与模型,确认都落进项目里 ----
    settings = exporter.build_settings(scene)
    print(f"[6] 导出设置里的目录: {settings.get('output_dir')}")
    result = exporter.export_tree(tree, settings, write_scene=True)
    print(f"    脚本: {result['script_name']}（{result['lines']} 行）")
    print(f"    实际写入目录: {result['directory']}")
    same = os.path.normcase(result["directory"]) == os.path.normcase(directory)
    print(f"    和项目目录一致: {same}")

    # ---- 7. 一个自检:确认所有产出都在项目目录**里面** ----
    print("\n[7] 自检:所有产出是否都在项目目录内")
    outside = []
    for path in result.get("written", []):
        try:
            inside = os.path.commonpath([os.path.abspath(path),
                                         os.path.abspath(directory)]) == \
                os.path.abspath(directory)
        except ValueError:
            inside = False
        mark = "OK  " if inside else "越界"
        print(f"    [{mark}] {path}")
        if not inside:
            outside.append(path)
    print(f"    结论: {'全部在项目内' if not outside else f'{len(outside)} 项跑到外面了'}")

    # ---- 8. 打印项目树 ----
    print("\n[8] 项目目录结构")
    print(f"{os.path.basename(directory)}/")
    print_tree(directory)

    # ---- 9. 打印 project.godot ----
    project_file = os.path.join(directory, "project.godot")
    if os.path.isfile(project_file):
        print("\n[9] project.godot 内容")
        print("-" * 74)
        print(open(project_file, encoding="utf-8").read())
        print("-" * 74)

    print(f"\n演示完成。目录: {outdir}")
    print("DEMO_DONE")
    return 0


try:
    code = main()
except Exception:
    traceback.print_exc()
    code = 1
sys.exit(code)
