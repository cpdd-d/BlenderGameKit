"""验证碰撞线框预览:能生成、能清除、**不会出现在导出里**。

最后一条最关键 —— 如果预览线框被导进 .glb,那用户会看到一堆多余的
线框模型,而且很难理解是从哪来的。
"""

import json
import os
import shutil
import struct
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


def read_glb_json(path):
    with open(path, "rb") as handle:
        data = handle.read()
    offset = 12
    while offset < len(data):
        length, kind = struct.unpack_from("<II", data, offset)
        offset += 8
        chunk = data[offset:offset + length]
        if kind == 0x4E4F534A:
            return json.loads(chunk.decode("utf-8"))
        offset += length
    raise ValueError("找不到 JSON 块")


def main():
    print("=" * 72)
    print("碰撞体可视化预览测试")
    print("=" * 72)

    module_name = find_addon()
    if not check("找到插件", module_name, "未安装"):
        return 1
    addon_utils.enable(module_name, default_set=True, persistent=True)
    ops = import_module(f"{module_name}.operator_io")
    asset_export = import_module(f"{module_name}.asset_export")
    project = import_module(f"{module_name}.project")

    workdir = os.path.join(tempfile.gettempdir(), "gk_collision_preview")
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)

    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    # 造三个形状不同的物体,验证自动推导
    #
    # 注意 Pillar 用的是**细长**的圆柱(高 > 直径),不是半径=高的那种。
    # 半径=高的圆柱包围盒是立方体,和球在包围盒上**无法区分** ——
    # 那种情况下判成球是几何上的必然,不是 bug。想验证胶囊那条分支,
    # 就得给一个真的细长的物体(人形、柱子都行)。
    specs = (("Ball", "uvsphere", 0.6), ("Pillar", "cone", 1.4), ("Slab", "cube", 0.5))
    made = []
    for name, kind, size in specs:
        mesh = bpy.data.meshes.new(name + "Mesh")
        obj = bpy.data.objects.new(name, mesh)
        scene.collection.objects.link(obj)
        bm = bmesh.new()
        if kind == "uvsphere":
            bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=size)
        elif kind == "cone":
            # 半径 0.4、高 4.0 -> 细长,应该判成胶囊
            bmesh.ops.create_cone(bm, cap_ends=True, segments=12,
                                  radius1=0.4, radius2=0.4, depth=4.0)
        else:
            bmesh.ops.create_cube(bm, size=size)
        bm.to_mesh(mesh)
        bm.free()
        made.append(obj)
    made[2].scale = (3.0, 1.0, 0.2)     # 压扁 -> 应该是盒子
    bpy.context.view_layer.update()

    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(workdir, "prev.blend"))
    directory, _reason = project.resolve(scene)
    project.create_project(directory)

    # ---- 1. 线框几何 ----
    print("  自动推导的形状:")
    expected = {"Ball": "sphere", "Pillar": "capsule", "Slab": "box"}
    for obj in made:
        shape = asset_export.guess_collision_shape(obj, True)
        dots = asset_export.collision_preview_mesh(obj, True, obj.gk_physics)
        has_verts = dots is not None and len(dots["verts"]) > 0
        has_edges = dots is not None and len(dots["edges"]) > 0
        print(f"    {obj.name:8s} -> {shape:8s} 顶点 {len(dots['verts']) if dots else 0}"
              f" 边 {len(dots['edges']) if dots else 0}")
        check(f"{obj.name} 推导为 {expected[obj.name]}", shape == expected[obj.name], shape)
        check(f"{obj.name} 有线框几何", has_verts and has_edges)

    # ---- 2. 生成预览物体 ----
    result = bpy.ops.gk.show_collision(target_all=True)
    check("显示碰撞体 执行成功", "FINISHED" in result, str(result))
    previews = [o for o in scene.objects if o.get(ops.PREVIEW_MARK)]
    print(f"    生成了 {len(previews)} 个预览物体")
    check("生成了 3 个预览物体", len(previews) == 3, str(len(previews)))
    for obj in previews:
        check(f"{obj.name} 是线框显示", obj.display_type == "WIRE", obj.display_type)
        check(f"{obj.name} 穿透显示", obj.show_in_front)
        check(f"{obj.name} 不参与渲染", obj.hide_render)

    # 预览物体应该挂在源物体下并保持零变换
    ball = bpy.data.objects.get("Ball")
    ball_preview = next((o for o in previews if o.parent is ball), None)
    check("预览挂在源物体下", ball_preview is not None)
    if ball_preview is not None:
        check("预览本地变换为零",
              all(abs(v) < 1e-6 for v in ball_preview.location),
              str(tuple(ball_preview.location)))

    # ---- 3. 重复点击不会累积 ----
    bpy.ops.gk.show_collision(target_all=True)
    previews2 = [o for o in scene.objects if o.get(ops.PREVIEW_MARK)]
    check("重复点击不会累积预览物体", len(previews2) == 3, str(len(previews2)))

    # ---- 4. **关键**:预览不能出现在导出里 ----
    path, ok, message = asset_export.export_scene_glb(directory, "World")
    check("整场景导出成功", ok, message)
    if ok:
        gltf = read_glb_json(path)
        node_names = [n.get("name", "") for n in gltf.get("nodes", [])]
        print(f"    glb 里的节点: {node_names}")
        leaked = [n for n in node_names if n.startswith("__GK碰撞预览__")]
        check("预览线框没有被导出", not leaked, str(leaked))
        for want in ("Ball", "Pillar", "Slab"):
            check(f"正常物体 {want} 仍然被导出", want in node_names, str(node_names))

    # ---- 5. 清除 ----
    removed = ops._remove_previews(scene)
    check("清除返回了数量", removed == 3, str(removed))
    left = [o for o in scene.objects if o.get(ops.PREVIEW_MARK)]
    check("清除后没有残留", not left, str(len(left)))

    # ---- 6. 碰撞形状设成 none 时不生成 ----
    ball.gk_physics.collision_shape = "none"
    bpy.ops.gk.show_collision(target_all=True)
    previews3 = [o for o in scene.objects if o.get(ops.PREVIEW_MARK)]
    check("形状为 none 时不生成预览", len(previews3) == 2, str(len(previews3)))
    ops._remove_previews(scene)

    # ---- 7. 所有物体都导出(没有"是否导出"开关了)----
    #
    # 按用户要求去掉了物体上的"导出这个物体"开关 —— 所有网格都导出,
    # 碰撞盒要不要在属性里直接配,不用先开一个总开关。
    ball.gk_physics.collision_shape = "auto"
    bpy.ops.gk.show_collision(target_all=True)
    previews4 = [o for o in scene.objects if o.get(ops.PREVIEW_MARK)]
    check("所有物体都生成预览(没有导出开关了)", len(previews4) == 3,
          str(len(previews4)))
    ops._remove_previews(scene)

    # ---- 7b. 「显示这一个」永远只显示活动的那一个 ----
    #
    # 用户报过两次这个 bug:
    #   1) "点击一个会显示全部"
    #   2) "在什么都没有选择时点击显示一个,会显示全部"
    #
    # 根因是拿 `context.selected_objects` 当"选中",而 Blender 的 selected
    # 包含**所有高亮项** —— 全选一次之后它就一直是一大堆。
    # 后来又多了个"取不到活动对象就退回高亮列表"的退让,把 2) 也带出来了。
    #
    # 所以这里**两个症状都要测死**:三个物体全部高亮时,只允许出现 1 个线框;
    # 没有活动对象时,一个都不许出现(宁可空着也不猜)。
    print("=" * 72)
    print("7. 「显示这一个」只显示活动对象")
    print("=" * 72)
    view_layer = bpy.context.view_layer
    for obj in made:
        obj.select_set(True)
    view_layer.objects.active = made[0]        # 活动的是 Ball
    print(f"    高亮 {len(bpy.context.selected_objects)} 个,"
          f"活动 = {view_layer.objects.active.name}")

    bpy.ops.gk.show_collision(target_all=False)
    single = [o for o in scene.objects if o.get(ops.PREVIEW_MARK)]
    print(f"    生成了 {len(single)} 个线框")
    check("高亮三个 + 活动一个 -> 只生成 1 个线框", len(single) == 1,
          str(len(single)))
    if single:
        parent = getattr(single[0], "parent", None)
        check("这 1 个线框挂在活动对象下面",
              parent is made[0], getattr(parent, "name", "None"))

    # 换一个活动对象,应该跟着换
    view_layer.objects.active = made[1]
    bpy.ops.gk.show_collision(target_all=False)
    single2 = [o for o in scene.objects if o.get(ops.PREVIEW_MARK)]
    check("换活动对象后仍然只有 1 个", len(single2) == 1, str(len(single2)))
    if single2:
        check("挂到了新的活动对象上",
              single2[0].parent is made[1],
              getattr(single2[0].parent, "name", "None"))

    # 没有活动对象:必须什么都不生成,并且清掉旧的
    #
    # ⚠ 这里要接住 RuntimeError:算子 self.report({"ERROR"}, ...) 之后
    # 返回 CANCELLED 时,从 Python 调 bpy.ops 会**抛异常**而不是返回结果集。
    # 界面上表现为一个红色提示(正是我们想要的),但测试里必须接住。
    view_layer.objects.active = None
    try:
        bpy.ops.gk.show_collision(target_all=False)
        raised = False
    except RuntimeError as exc:
        raised = True
        print(f"    报了错(符合预期): {str(exc)[:60]}")
    single3 = [o for o in scene.objects if o.get(ops.PREVIEW_MARK)]
    check("没有活动对象时会明确报错", raised, "没有报错")
    check("没有活动对象时一个都不生成(不许退回高亮列表)", len(single3) == 0,
          f"生成了 {len(single3)} 个")

    ops._remove_previews(scene)

    # ---- 8. 实时预览:改参数时线框应该立刻跟着变 ----
    #
    # 这是"实时调节"的核心:给碰撞参数挂 update 回调,回调里就地更新已有
    # 线框的顶点(不删了重建 —— 重建会让视口闪烁)。
    #
    # 注意这里**先把状态造干净**再测:前面的小节改过 collision_shape 和
    # shape_size,如果直接量很容易把"上一次的残影"当成结果。
    ops._remove_previews(scene)
    ball.gk_physics.collision_shape = "box"
    ball.gk_physics.shape_size = (1.0, 1.0, 1.0)
    bpy.ops.gk.show_collision(target_all=True)

    ball_preview = next((o for o in scene.objects
                         if o.get(ops.PREVIEW_MARK) and o.parent is ball), None)
    check("球有碰撞预览", ball_preview is not None)

    def preview_coords():
        # Blender 4.x 里 MeshVertex 本身不可迭代,要取 .co
        return [tuple(round(c, 4) for c in v.co)
                for v in ball_preview.data.vertices]

    if ball_preview is not None:
        before = preview_coords()
        before_max = max(abs(c) for v in before for c in v)
        print(f"    盒子尺寸 1.0 -> {len(before)} 顶点, 最大坐标 {before_max:.4f}")
        check("1.0 的盒子半边长是 0.5", abs(before_max - 0.5) < 0.01,
              f"{before_max:.4f}")

        # 改尺寸 -> update 回调应该就地更新顶点
        ball.gk_physics.shape_size = (3.0, 3.0, 3.0)
        bpy.context.view_layer.update()
        after = preview_coords()
        after_max = max(abs(c) for v in after for c in v)
        print(f"    改成 3.0 -> 最大坐标 {after_max:.4f}（期望 1.5）")
        check("改尺寸后线框跟着变", abs(after_max - 1.5) < 0.02,
              f"{after_max:.4f}（期望 1.5）")

        # 换形状 -> 顶点数应该变(盒子 8 个 vs 球 100+ 个)
        ball.gk_physics.collision_shape = "sphere"
        bpy.context.view_layer.update()
        sphere_count = len(preview_coords())
        print(f"    换成球 -> {sphere_count} 顶点（盒子是 {len(before)} 个）")
        check("换形状后顶点数变了", sphere_count != len(before),
              f"{sphere_count} == {len(before)}")

        # 设成 none -> 预览应该被删掉
        ball.gk_physics.collision_shape = "none"
        bpy.context.view_layer.update()
        still_there = any(o.get(ops.PREVIEW_MARK) and o.parent is ball
                          for o in scene.objects)
        check("形状设为 none 时预览被清掉", not still_there)

        # 恢复成盒子,尺寸也应该回到当前设置
        ball.gk_physics.collision_shape = "box"
        bpy.context.view_layer.update()
        # 注意:none 时预览被删了,再改形状不会自动重建 ——
        # 这是有意的(不主动创建),要重新点『显示碰撞体』
        check("预览被删后不会凭空重建",
              not any(o.get(ops.PREVIEW_MARK) and o.parent is ball
                      for o in scene.objects))

    # ---- 9. 预览=导出:线框要落在物体身上(本轮大修)----
    #
    # 以前预览线框被画在**世界原点** —— 父级逆矩阵把物体自己的位置
    # 抵消掉了,物体一挪位置就完全对不上。测试里的物体都在原点,
    # 所以一直没暴露。现在线框直接用世界坐标(和导出同一套计算)。
    ops._remove_previews(scene)
    ball.gk_physics.collision_shape = "auto"
    ball.location = (7.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    bpy.ops.gk.show_collision(target_all=True)
    ball_preview = next((o for o in scene.objects
                         if o.get(ops.PREVIEW_MARK) and o.parent is ball), None)
    check("挪过位置的物体也有线框", ball_preview is not None)
    if ball_preview is not None:
        world = [tuple(ball_preview.matrix_world @ vert.co)
                 for vert in ball_preview.data.vertices]
        xs = [point[0] for point in world]
        check("线框落在物体身上(不是世界原点)",
              abs(min(xs) - 6.4) < 0.05 and abs(max(xs) - 7.6) < 0.05,
              f"x 范围 {min(xs):.3f}..{max(xs):.3f}(期望 6.4..7.6)")
    ball.location = (0.0, 0.0, 0.0)
    ops._remove_previews(scene)

    # ---- 10. 子物体碰撞块:预览整组一起画 ----
    #
    # 勾『作为父级的碰撞块』的小方块并入宿主的线框(人形头/身/腿);
    # 它自己**不单独**生成线框,也不单独生成碰撞体。
    ops._remove_previews(scene)
    pillar = bpy.data.objects.get("Pillar")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    piece_mesh = bpy.data.meshes.new("PieceMesh")
    bm.to_mesh(piece_mesh)
    bm.free()
    piece = bpy.data.objects.new("头块", piece_mesh)
    scene.collection.objects.link(piece)
    piece.parent = pillar
    piece.matrix_parent_inverse = pillar.matrix_world.inverted()
    piece.location = (0.0, 1.2, 0.0)          # 摆在柱子上方一点
    piece.gk_physics.collision_piece = True
    bpy.context.view_layer.update()
    view_layer.objects.active = pillar
    bpy.ops.gk.show_collision(target_all=False)
    singles = [o for o in scene.objects if o.get(ops.PREVIEW_MARK)]
    check("只生成 1 个线框(碰撞块跟着宿主)", len(singles) == 1, str(len(singles)))
    if singles:
        ys = [vert.co[1] for vert in singles[0].data.vertices]
        check("宿主线框里带上了碰撞块(整体一起画)", max(ys) > 1.0,
              f"y max={max(ys):.3f}(柱子胶囊只有 ±0.4)")
    # 碰撞块的网格不该进 glb(它在游戏里只是"看不见的碰撞形状")
    piece_glb, piece_ok, _piece_msg = asset_export.export_scene_glb(
        directory, "PieceCheck")
    if piece_ok:
        gltf = read_glb_json(piece_glb)
        node_names = [n.get("name", "") for n in gltf.get("nodes", [])]
        check("碰撞块的网格没进 glb", "头块" not in node_names,
              str(node_names[:10]))
    else:
        check("碰撞块 glb 导出成功", False, str(_piece_msg))
    ops._remove_previews(scene)

    # ---- 11. 凸包:导出认、预览也认(手动选『凸包』)----
    slab = bpy.data.objects.get("Slab")
    slab.gk_physics.collision_shape = "convex"
    bpy.context.view_layer.update()
    built = asset_export.build_collision(slab, True, slab.gk_physics)
    check("凸包:导出用 ConvexPolygonShape3D + 点集",
          built is not None and "ConvexPolygonShape3D" in built["sub_resource"]
          and "PackedVector3Array(" in built["sub_resource"],
          str(built and built.get("shape")))
    wire = asset_export.collision_preview_mesh(slab, True, slab.gk_physics)
    check("凸包:线框有点有边",
          wire is not None and len(wire["verts"]) >= 4
          and len(wire["edges"]) >= 6,
          f"{len(wire['verts']) if wire else 0} 点")
    slab.gk_physics.collision_shape = "auto"
    bpy.context.view_layer.update()

    ball.gk_physics.collision_shape = "auto"
    ops._remove_previews(scene)

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
print("COLLISION_PREVIEW=" + ("OK" if code == 0 and not failed else "FAILED"))
print("=" * 72)

sys.exit(0 if (code == 0 and not failed) else 1)
