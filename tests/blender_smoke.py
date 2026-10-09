"""在真实 Blender 里跑一遍:注册插件 -> 建节点树 -> 生成代码 -> 导出文件。

用法:
    blender -b --python tests/blender_smoke.py

注意:**不要加 --factory-startup**。工厂启动会忽略用户偏好设置，
从而让 Blender 完全看不到用户安装的扩展仓库
(表现为 addon_utils.modules() 里没有 bl_ext.<repo>.<pkg>)。
这个坑已经踩过一次，见 README。

退出码 0 表示全部通过。
"""

import os
import re
import sys
import tempfile
import traceback

import addon_utils
import bpy

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" —— {detail}" if detail and not condition else ""))
    return bool(condition)


def find_addon_module():
    """在已安装的扩展/插件里找到 BlenderGameKit。"""
    for module in addon_utils.modules():
        name = getattr(module, "__name__", "")
        if "blender_game_kit" in name or getattr(module, "bl_info", {}).get("name", "").startswith("BlenderGameKit"):
            return name
    return None


def main():
    print("=" * 70)
    print("BlenderGameKit 冒烟测试")
    print("=" * 70)

    module_name = find_addon_module()
    if not check("找到插件模块", module_name, "未安装 BlenderGameKit"):
        return 1
    print(f"    模块名: {module_name}")

    try:
        addon_utils.enable(module_name, default_set=True, persistent=True)
    except Exception as exc:
        check("启用插件", False, f"{type(exc).__name__}: {exc}")
        return 1
    check("启用插件", True)

    # ---- 插件内容是否齐全 ----
    mod = sys.modules.get(module_name)
    check("插件已加载到 sys.modules", mod is not None)
    if mod is None:
        return 1

    from importlib import import_module

    codegen = import_module(f"{module_name}.codegen")
    exporter = import_module(f"{module_name}.exporter")
    node_lib = import_module(f"{module_name}.nodes.nodes")
    # 环境脚本(上帝机)的渲染入口(第三十九轮:验里面真有三样新功能)
    asset_export = import_module(f"{module_name}.asset_export")
    # 『选图片』算子的枚举函数(第三十七轮):要验证它真能列出工程里的图
    operator_io = import_module(f"{module_name}.operator_io")

    check(f"节点数量 ({len(node_lib.ALL_CLASSES)})", len(node_lib.ALL_CLASSES) >= 30)

    # 注意:bpy.types 的属性表只包含 RNA(C/C++)类型，动态注册的 Python 类
    # 通过 bpy.types.X 取不到。所以这里一律用"能不能真的用"来验证注册结果。
    check("场景设置已挂载", hasattr(bpy.types.Scene, "gk_settings"))

    # ---- 引脚类型:能不能真的建出 socket ----
    try:
        probe_tree = bpy.data.node_groups.new("__pin_probe__", "GKGameLogicTree")
        probe_node = probe_tree.nodes.new("GKNodeOnReady")
        flow_ok = any(getattr(s, "bl_idname", "") == "GKSocketFlow" for s in probe_node.outputs)
        bpy.data.node_groups.remove(probe_tree)
        check("执行流引脚可用", flow_ok)
    except Exception as exc:
        check("执行流引脚可用", False, f"{type(exc).__name__}: {exc}")

    # ---- 建一棵真实节点树 ----
    try:
        tree = bpy.data.node_groups.new("冒烟测试", "GKGameLogicTree")
        # ⚠ 必须给 fake user:没有用户的节点图在**存盘/导出那一步会被丢掉**
        # (实测:后半段"导出报告/函数/变量面板"全部报
        #  `StructRNA of type GKGameLogicTree has been removed`)——
        # 示例脚本里也都写了 use_fake_user,一个道理。
        tree.use_fake_user = True
        check("创建节点树", tree is not None)
    except Exception as exc:
        check("创建节点树", False, f"{type(exc).__name__}: {exc}")
        return 1

    def new_node(bl_idname, name, **props):
        node = tree.nodes.new(bl_idname)
        node.name = name
        for key, value in props.items():
            setattr(node, key, value)
        return node

    try:
        start = new_node("GKNodeOnReady", "开始")
        physics = new_node("GKNodeOnPhysicsProcess", "物理帧")
        direction = new_node("GKNodeGetVector", "方向输入")
        move = new_node("GKNodeMoveDirection", "移动", gk_speed=240.0)
        jump_key = new_node("GKNodeActionJustPressed", "跳跃键", gk_action="jump")
        branch = new_node("GKNodeBranch", "能跳吗")
        do_jump = new_node("GKNodeJump", "起跳", gk_jump_force=520.0)
        printer = new_node("GKNodePrint", "打印")
        check("创建 8 个节点", len(tree.nodes) == 8, f"实际 {len(tree.nodes)}")
    except Exception as exc:
        check("创建节点", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    # ---- 检查引脚真的建出来了 ----
    inputs = [socket.name for socket in move.inputs]
    outputs = [socket.name for socket in move.outputs]
    check("动作节点有执行输入", "执行" in inputs, str(inputs))
    check("动作节点有完成输出", "完成" in outputs, str(outputs))
    check("移动节点带速度属性", hasattr(move, "gk_speed") and abs(move.gk_speed - 240.0) < 1e-6)

    # ---- 连线(用真实 tree.links.new)，逐条校验确实建立了 ----
    link_problems = []

    def link(from_node, from_socket, to_node, to_socket):
        src = from_node.outputs.get(from_socket)
        dst = to_node.inputs.get(to_socket)
        if src is None or dst is None:
            raise KeyError(f"{from_node.name}.{from_socket} -> {to_node.name}.{to_socket}")
        before = len(tree.links)
        tree.links.new(src, dst)
        if len(tree.links) == before:
            link_problems.append(f"{from_node.name}.{from_socket} -> {to_node.name}.{to_socket}"
                                 f" (引脚类型 {src.bl_idname} -> {dst.bl_idname})")

    try:
        link(physics, "执行", move, "执行")
        link(direction, "方向", move, "方向")
        link(move, "完成", branch, "执行")
        link(jump_key, "刚按下", branch, "条件")
        link(branch, "是", do_jump, "执行")
        link(do_jump, "完成", printer, "执行")
        # 最后一条:**预期会被 Blender 拒绝**
        # 打印节点的"执行"输入已经被物理帧占用，而 Blender 的输入引脚
        # 只允许一条连线。这正是"两个事件不能共用一个下游节点"的原因，
        # 代码生成器会对此给出明确告警(见 test_shared_node_between_events_warns)。
        link(start, "执行", printer, "执行")
    except Exception as exc:
        check("连线", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    check("连线(6 条，第 7 条如预期被拒绝)", len(tree.links) == 6,
          f"实际 {len(tree.links)} 条")
    check("第二条连同一个输入引脚会被拒绝", len(link_problems) == 1, str(link_problems))

    # ---- 分组框(UE 蓝图的 Comment Box)+ 折叠(第六十二轮)----
    # 选中两个节点 -> 一键建框 -> 应该框住它们,而且**不影响代码生成**
    # (NodeFrame 不是 GK 节点,codegen 必须当它不存在)。
    # 第六十二轮补了"折叠":收起时框内节点藏起来、框缩成一条;展开原样还原
    # —— 全程生成的脚本必须**逐字一样**(`hide` 只是显示开关,连线一条不少)。
    try:
        for node in tree.nodes:
            node.select = False
        do_jump.select = True
        printer.select = True
        tree.nodes.active = printer
        result = bpy.ops.gk.add_comment_box()
        frames = [n for n in tree.nodes if n.bl_idname == "NodeFrame"]
        frame = frames[0] if frames else None
        check("分组框已创建", result == {"FINISHED"} and len(frames) == 1,
              f"{result} frames={len(frames)}")
        check("分组框认了两个子节点",
              frame is not None and do_jump.parent == frame and printer.parent == frame)
        check("分组框有默认标题与颜色",
              frame is not None and frame.label == "分组"
              and bool(frame.use_custom_color) and frame.width > 0.0)

        options = {"dimension": "2", "base_class": "CharacterBody2D",
                   "export_tunables": True}
        before, _ = codegen.generate(tree, options)
        full_height = float(frame.height)

        # 收起:框内节点藏起来 + 框缩成一条标题栏
        for node in tree.nodes:
            node.select = node.name == frame.name
        tree.nodes.active = frame
        bpy.ops.gk.toggle_group()
        check("折叠:框内节点都藏起来了", do_jump.hide and printer.hide,
              f"{do_jump.hide} {printer.hide}")
        check("折叠:框缩成一条标题栏", 0.0 < float(frame.height) < full_height,
              f"{frame.height} <- {full_height}")
        after, _ = codegen.generate(tree, options)
        check("折叠前后生成的代码逐字一样", before == after)

        # 展开:节点重新可见 + 框高还原
        bpy.ops.gk.toggle_group()
        check("展开:节点重新可见",
              (not do_jump.hide) and (not printer.hide))
        check("展开:框高还原", abs(float(frame.height) - full_height) < 1e-4,
              f"{frame.height} vs {full_height}")
    except Exception as exc:
        check("分组框", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 碰撞形状的"智能推荐"(用户 2026-10-06:"正方体对角穿模")----
    # 真 Blender 里造一个**默认正方体**(8 顶点 + 6 面中心)和一个球,
    # 断言判出来的形状:正方体 -> 盒子(球会内切、八个角漏出来),
    # 球 -> 球。以前这里无脑给球,所以正方体的角上会穿模。
    try:
        asset_export = import_module(f"{module_name}.asset_export")
        bpy.ops.mesh.primitive_cube_add(size=2.0)
        cube = bpy.context.active_object
        cube_shape = asset_export.guess_collision_shape(cube, True)
        cube_ratio = asset_export.mesh_roundness(cube)
        bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0)
        ball = bpy.context.active_object
        ball_shape = asset_export.guess_collision_shape(ball, True)
        ball_ratio = asset_export.mesh_roundness(ball)
        for obj in (cube, ball):
            bpy.data.objects.remove(obj, do_unlink=True)
        check("碰撞形状:正方体给盒子(不是球)", cube_shape == "box",
              f"判成 {cube_shape}(圆度 {cube_ratio:.3f})")
        check("碰撞形状:圆球还是给球", ball_shape == "sphere",
              f"判成 {ball_shape}(圆度 {ball_ratio:.3f})")
        check("圆度判据分得开(正方体 < 0.85 <= 球)",
              cube_ratio is not None and ball_ratio is not None
              and cube_ratio < 0.85 <= ball_ratio,
              f"正方体 {cube_ratio} / 球 {ball_ratio}")
    except Exception as exc:
        check("碰撞形状智能推荐", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 碰撞系统打磨(本轮):偏移 / 子物体碰撞块 / 凸包 ----
    # 三件事的"最终证据"都在同一份生成的 .tscn 里:
    #   ① 偏移:原点在脚底的方块,碰撞形状要抬到包围盒中心
    #      (老修复曾被漏掉:Collision 节点上没有 position -> 一半埋地里);
    #   ② 碰撞块:勾『作为父级的碰撞块』的小方块 -> 合并成 Collision2,
    #      位置 = 相对宿主的偏移;而且**不单独生成 body**;
    #   ③ 凸包:手动选『凸包』-> ConvexPolygonShape3D + 点集;
    #   ④ 碰撞层写在 body 上(以前写在 shape 上会被 Godot 忽略)。
    try:
        import mathutils
        import shutil

        asset_export = import_module(f"{module_name}.asset_export")

        work = os.path.join(tempfile.gettempdir(), "gk_collide_polish")
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work, exist_ok=True)

        # 清掉场景里现有的东西,免得混进断言
        for leftover in list(bpy.context.scene.objects):
            bpy.data.objects.remove(leftover, do_unlink=True)

        bpy.ops.mesh.primitive_cube_add(size=2.0, location=(3.0, 0.0, 0.0))
        host = bpy.context.active_object
        host.name = "脚底方块"
        # 网格整体上移 1 -> 原点在**脚底**(z=0)、包围盒中心在 z=1
        host.data.transform(mathutils.Matrix.Translation((0.0, 0.0, 1.0)))
        host.gk_physics.collision_layer = "2"       # 顺便验层写在 body 上
        bpy.context.view_layer.update()

        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(3.0, 0.0, 3.0))
        piece = bpy.context.active_object
        piece.name = "头块"
        piece.gk_physics.collision_piece = True
        piece.parent = host
        piece.matrix_parent_inverse = host.matrix_world.inverted()
        bpy.context.view_layer.update()

        bpy.ops.mesh.primitive_cube_add(size=1.5, location=(8.0, 0.0, 0.0))
        rock = bpy.context.active_object
        rock.name = "凸包石头"
        rock.gk_physics.collision_shape = "convex"
        bpy.context.view_layer.update()

        # 先出世界 glb(write_world_scene 要从里面读网格名;顺带验证
        # 碰撞块的网格**没进 glb** —— 它是纯辅助物)
        glb_path, glb_ok, _glb_msg = asset_export.export_scene_glb(work, "World")
        check("世界 glb 导出成功(碰撞块不该在里面)", glb_ok, str(_glb_msg))

        path, _count = asset_export.write_world_scene(
            work, "World", scene=bpy.context.scene, dimension="3")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        lines = text.splitlines()

        check("碰撞偏移写进 Collision 节点(抬到包围盒中心)",
              "position = Vector3(0.000000, 1.000000, 0.000000)" in text,
              text[-900:])
        check("碰撞块合并成 Collision2 + 相对偏移",
              '[node name="Collision2"' in text
              and "position = Vector3(0.000000, 3.000000, 0.000000)" in text)
        check("碰撞块不单独生成 body(只是宿主的形状)",
              '[node name="头块"' not in text)

        layer_index = next((i for i, line in enumerate(lines)
                            if line.strip() == "collision_layer = 2"), None)
        owner_line = ""
        if layer_index is not None:
            owner_line = next((line for line in reversed(lines[:layer_index])
                               if line.startswith("[node ")), "")
        check("碰撞层写在 body 节点上(不是 shape 上)",
              layer_index is not None and "脚底方块" in owner_line,
              f"{owner_line} (index={layer_index})")

        check("凸包生成 ConvexPolygonShape3D + 点集",
              "ConvexPolygonShape3D" in text
              and "points = PackedVector3Array(" in text)
    except Exception as exc:
        check("碰撞系统打磨", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 粒子特效(需求 5,用户 2026-10-05 第二十一轮)----
    # Blender 侧只有"标记 + 参数",粒子节点是**导出时**生成的。这一节在
    # 真 Blender 里验五件事:
    #   ① 有物理体的发射器挂在 Colliders/<名字> 下(跟着角色动)、
    #      空物体发射器挂在 Particles 容器里;
    #   ② 方向 / 重力按 glTF 的 Y-up 换轴(不换火苗横着喷);
    #   ③ 勾『不显示本体网格』的发射器 -> 网格**不进世界 glb**;
    #   ④ 贴图链路:材质里有图就用它、没有就用内置那张(现写一张 PNG);
    #   ⑤ 『特效_播放』在真 Blender 里编得出带**具体类型**的引用
    #      (声明成 Node 调 .emitting,GDScript 直接编译不过)。
    try:
        import math
        import shutil

        asset_export = import_module(f"{module_name}.asset_export")

        work = os.path.join(tempfile.gettempdir(), "gk_particle_fx")
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work, exist_ok=True)
        for leftover in list(bpy.context.scene.objects):
            bpy.data.objects.remove(leftover, do_unlink=True)

        # ① 火把:网格 + 静态物理(会生成 body)-> 粒子挂在 Colliders/火把 下
        bpy.ops.mesh.primitive_cube_add(size=0.6, location=(2.0, 0.0, 1.0))
        torch = bpy.context.active_object
        torch.name = "火把"
        torch.rotation_euler = (0.0, math.radians(30.0), 0.0)   # 斜着喷
        torch.gk_physics.body_type = "StaticBody3D"
        torch_fx = torch.gk_fx
        torch_fx.enabled = True
        torch_fx.amount = 24
        torch_fx.speed = 1.6
        torch_fx.gravity = 2.5
        torch_fx.radius = 0.2
        torch_fx.start_size = 0.25
        torch_fx.end_size = 0.0

        # ② 烟雾:空物体 + CPU 粒子 -> 挂在 Particles 容器里
        smoke = bpy.data.objects.new("烟雾", None)
        bpy.context.scene.collection.objects.link(smoke)
        smoke.location = (-2.0, 0.0, 2.0)
        smoke.gk_fx.enabled = True
        smoke.gk_fx.particle_type = "cpu"
        smoke.gk_fx.start_size = 0.9        # 好和火把(0.25)分开认

        # ③ 火星:网格 + 勾『不显示本体网格』-> 只当标记,网格不进 glb
        bpy.ops.mesh.primitive_cube_add(size=0.4, location=(0.0, 3.0, 1.0))
        spark = bpy.context.active_object
        spark.name = "火星"
        spark.gk_fx.enabled = True
        spark.gk_fx.hide_mesh = True

        # ④ 给『火星』挂一张真贴图:粒子贴图该走现有材质/贴图链路
        image = bpy.data.images.new("火光贴图", 32, 32)
        material = bpy.data.materials.new("火材质")
        material.use_nodes = True
        material_nodes = material.node_tree.nodes
        tex_node = material_nodes.new("ShaderNodeTexImage")
        tex_node.image = image
        principled = next(node for node in material_nodes
                          if node.type == "BSDF_PRINCIPLED")
        material.node_tree.links.new(tex_node.outputs["Color"],
                                     principled.inputs["Base Color"])
        spark.data.materials.append(material)
        bpy.context.view_layer.update()

        texture_name = asset_export.fx_texture_file(spark)
        check("粒子:材质有贴图时就用它(不走内置图)",
              texture_name != asset_export.FX_DEFAULT_TEXTURE, texture_name)
        # 文件还没导 -> 判断要退回内置那张(.tscn 不能引用不存在的资源)
        check("粒子:贴图文件不在时退回内置图",
              asset_export.fx_texture_file(spark, work)
              == asset_export.FX_DEFAULT_TEXTURE)
        # 导贴图 + 写内置图,再判断一次:这回该认材质那张了(名字两边一致)
        asset_export.export_textures(work, [material])
        asset_export.ensure_fx_default_texture(work)
        check("粒子:贴图落盘后名字对得上",
              asset_export.fx_texture_file(spark, work) == texture_name,
              asset_export.fx_texture_file(spark, work))
        check("粒子:内置贴图是真 PNG",
              open(os.path.join(work, "art", asset_export.FX_DEFAULT_TEXTURE),
                   "rb").read(8) == b"\x89PNG\r\n\x1a\n")

        # 发射器表(『特效_*』节点靠它知道目标是什么类型)
        table = asset_export.fx_codegen_map(bpy.context.scene, "3")
        check("粒子:发射器表按 GPU / CPU 给节点类型",
              table.get("火把", {}).get("node_type") == "GPUParticles3D"
              and table.get("烟雾", {}).get("node_type") == "CPUParticles3D"
              and table.get("火星", {}).get("node_type") == "GPUParticles3D",
              str(table))

        # 排除表就是导出流程用的那个函数(见 export_assets_pipeline 里
        # `excluded.extend(fx_hidden_meshes(scene))`)——
        # 不然这里验的就不是"导出真的会排除",只是"这个函数看起来对"。
        hidden = asset_export.fx_hidden_meshes(bpy.context.scene)
        check("粒子:『不显示本体网格』的发射器会进排除表",
              [obj.name for obj in hidden] == ["火星"], str(hidden))
        glb_path, glb_ok, _glb_msg = asset_export.export_scene_glb(
            work, "World", exclude=hidden or None)
        check("粒子:世界 glb 导出成功", glb_ok, str(_glb_msg))
        meshes = asset_export.glb_scene_nodes(work, "World").get("meshes", {})
        check("粒子:勾了『不显示本体网格』-> 网格不进世界 glb",
              "火星" not in meshes and "火把" in meshes, str(sorted(meshes)))

        path, _count = asset_export.write_world_scene(
            work, "World", scene=bpy.context.scene, dimension="3")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()

        check("粒子:有物理体的发射器挂在 body 下面(跟着动)",
              '[node name="火把" type="GPUParticles3D" '
              'parent="Colliders/火把"]' in text)
        check("粒子:空物体发射器挂在 Particles 容器里",
              '[node name="Particles" type="Node3D" parent="."]' in text
              and '[node name="烟雾" type="CPUParticles3D" '
                  'parent="Particles"]' in text)
        check("粒子:方向按 Y-up 换轴(Blender +Z -> Godot +Y)",
              "direction = Vector3(0.000000, 1.000000, 0.000000)" in text)
        check("粒子:重力正数往下(Godot 的 -Y)",
              "gravity = Vector3(0, -2.500000, 0)" in text)
        check("粒子:发射半径 -> 球形发射区",
              "emission_shape = 1" in text
              and "emission_sphere_radius = 0.200000" in text)
        check("粒子:CPU 粒子参数直接写在节点上(scale_amount_*)",
              "scale_amount_min = 0.900000" in text
              and "scale_amount_curve = SubResource(" in text)
        check("粒子:两个发射器各引用自己的贴图",
              f'path="res://art/{asset_export.FX_DEFAULT_TEXTURE}"' in text
              and f'path="res://art/{texture_name}"' in text,
              text[:600])
        # 绕 Blender Y 轴转 30° -> Godot 基按 Y-up 换轴后的**精确** 9 个数
        # (列优先:x 轴 3 个、y 轴 3 个、z 轴 3 个)。挂 body 下所以原点是 0。
        check("粒子:斜着的发射器写进了 transform(换轴 + 列优先)",
              "transform = Transform3D(0.866025, 0.500000, 0.000000, "
              "-0.500000, 0.866025, 0.000000, "
              "0.000000, 0.000000, 1.000000, "
              "0.000000, 0.000000, 0.000000)" in text,
              [line for line in text.splitlines()
               if line.startswith("transform = ")][:2])

        # ── 静态物体的碰撞代理也要带旋转(第二十八轮修)────────────────
        # 火把是绕 Y 轴转 30° 的静态物体。以前碰撞代理只写位置、基写死单位阵
        # —— 于是"看得见的网格是斜的、碰撞体是正的":20° 斜坡走不上去、
        # 45° 方块斜对角照样穿模。这里把 body 的 transform 抓出来单独看。
        body_match = re.search(
            r'\[node name="火把" type="StaticBody3D" parent="Colliders"\]\n'
            r'(transform = Transform3D\([^)]*\))', text)
        check("碰撞代理带上了物体的旋转(斜坡/斜方块不再'网格斜、碰撞正')",
              bool(body_match) and body_match.group(1).startswith(
                  "transform = Transform3D(0.866025, 0.500000, 0.000000, "
                  "-0.500000, 0.866025, 0.000000, "
                  "0.000000, 0.000000, 1.000000, "),
              body_match.group(1) if body_match else "没找到火把的 StaticBody3D 块")

        # ⑤ 节点侧:『特效_播放』在真 Blender 里编出带类型的引用
        fx_tree = bpy.data.node_groups.new("粒子冒烟", "GKGameLogicTree")
        ready_node = fx_tree.nodes.new("GKNodeOnReady")
        play_node = fx_tree.nodes.new("GKNodeFxPlay")
        play_node.gk_target = "火把"
        fx_tree.links.new(ready_node.outputs["执行"], play_node.inputs["执行"])
        fx_code, fx_warnings = codegen.generate(fx_tree, {
            "dimension": "3", "base_class": "CharacterBody3D",
            "fx": asset_export.fx_codegen_map(bpy.context.scene, "3"),
        })
        check("粒子:『特效_播放』编出带具体类型的引用",
              ": GPUParticles3D = " in fx_code
              and ".emitting = true" in fx_code
              and not fx_warnings, f"{fx_code} {fx_warnings}")
        bpy.data.node_groups.remove(fx_tree)

        # 收尾:把这一节的物体清掉,别影响后面的步骤(它们默认"场景干净")
        for leftover in (torch, smoke, spark):
            bpy.data.objects.remove(leftover, do_unlink=True)
    except Exception as exc:
        check("粒子特效", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 灯光:三种类型 / 颜色 / 阴影 / 手电筒(第四十八轮:阶段①)----
    # 以前只导"场景里第一盏 SUN",点光聚光全丢(恐怖游戏做不了手电筒)。
    # 真 Blender 里摆三盏灯(太阳 + 台灯 + 手电筒),验导出的 .tscn:
    #   * 三种类型各一个节点;
    #   * 颜色 / 阴影 / 范围 / 锥角都写进去了;
    #   * 勾了『跟随相机』的那盏名字进了环境脚本的 follow_lights。
    try:
        import math
        import shutil

        work = os.path.join(tempfile.gettempdir(), "gk_lights")
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work, exist_ok=True)
        for leftover in list(bpy.context.scene.objects):
            bpy.data.objects.remove(leftover, do_unlink=True)
        bpy.ops.mesh.primitive_cube_add(size=1.0)

        moon = bpy.data.lights.new("月光", type="SUN")
        moon.energy = 0.4
        moon.color = (0.6, 0.7, 1.0)
        moon_obj = bpy.data.objects.new("月光", moon)
        bpy.context.scene.collection.objects.link(moon_obj)

        lamp = bpy.data.lights.new("台灯", type="POINT")
        lamp.energy = 800.0
        lamp.color = (1.0, 0.7, 0.4)
        lamp.cutoff_distance = 8.0
        lamp_obj = bpy.data.objects.new("台灯", lamp)
        lamp_obj.location = (2.0, 2.0, 0.0)
        bpy.context.scene.collection.objects.link(lamp_obj)

        torch = bpy.data.lights.new("手电筒", type="SPOT")
        torch.energy = 1500.0
        torch.spot_size = math.radians(40.0)
        torch.use_shadow = False
        torch_obj = bpy.data.objects.new("手电筒", torch)
        bpy.context.scene.collection.objects.link(torch_obj)
        torch_obj.gk_physics.light_follow_camera = True
        bpy.context.view_layer.update()

        asset_export.export_scene_glb(work, "World")
        path, _count = asset_export.write_world_scene(
            work, "World", scene=bpy.context.scene, dimension="3")
        with open(path, encoding="utf-8") as handle:
            scene_text = handle.read()
        env_file = os.path.join(work, "scripts", "gk_environment.gd")
        env_code = ""
        if os.path.isfile(env_file):
            with open(env_file, encoding="utf-8") as handle:
                env_code = handle.read()
        check("灯光:三种类型都导出来了",
              'type="DirectionalLight3D"' in scene_text
              and 'type="OmniLight3D"' in scene_text
              and 'type="SpotLight3D"' in scene_text,
              [ln for ln in scene_text.splitlines() if "Light3D" in ln][:4])
        check("灯光:颜色 / 阴影 / 范围 / 锥角都写进去了",
              "light_color = Color(0.600000, 0.700000, 1.000000, 1)" in scene_text
              and "shadow_enabled = false" in scene_text
              and "omni_range = 8.000" in scene_text
              and "spot_angle = 20.000" in scene_text,
              [ln for ln in scene_text.splitlines()
               if "light_color" in ln or "range" in ln or "angle" in ln][:6])
        check("手电筒:『跟随相机』的灯名进了环境脚本",
              'follow_lights: String = "手电筒"' in env_code)
        check("手电筒:环境脚本每帧贴相机",
              "func _gk_follow_lights_tick() -> void:" in env_code
              and "global_transform = cam.global_transform" in env_code)
        # 收尾:清掉这一节的灯(网格留给下一节自己清)
        for leftover in (moon_obj, lamp_obj, torch_obj):
            bpy.data.objects.remove(leftover, do_unlink=True)
    except Exception as exc:
        check("灯光导出", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 游戏内调试面板(用户 2026-10-05 第二十二轮:调试体验)----
    # 真 Blender 里验两件事:
    #   ① 场景开关打开 -> 写出的**环境脚本**里带着面板(按键 / 复制 / 变量过滤),
    #      而且按键跟着场景设置走(F8 / 反引号);
    #   ② 代码生成开着面板 -> 脚本里有日志缓冲,『打印_输出』会往面板推一行。
    try:
        import shutil

        asset_export = import_module(f"{module_name}.asset_export")

        work = os.path.join(tempfile.gettempdir(), "gk_debug_panel")
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work, exist_ok=True)
        for leftover in list(bpy.context.scene.objects):
            bpy.data.objects.remove(leftover, do_unlink=True)
        bpy.ops.mesh.primitive_cube_add(size=1.0)
        bpy.context.view_layer.update()

        settings = bpy.context.scene.gk_settings
        settings.debug_panel = True
        settings.debug_key = "F8"
        settings.debug_copy_key = "`"

        asset_export.export_scene_glb(work, "World")
        path, _count = asset_export.write_world_scene(
            work, "World", scene=bpy.context.scene, dimension="3")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        env_file = os.path.join(work, "scripts", "gk_environment.gd")
        env_code = ""
        if os.path.isfile(env_file):
            with open(env_file, encoding="utf-8") as handle:
                env_code = handle.read()
        check("调试面板:环境脚本里有面板本体 + 复制",
              "func _gk_dbg_setup() -> void:" in env_code
              and "func _gk_dbg_text() -> String:" in env_code
              and "DisplayServer.clipboard_set(text)" in env_code)
        check("调试面板:按键跟着场景设置走(F8 / 反引号)",
              "const _GK_DBG_KEY := KEY_F8" in env_code
              and "const _GK_DBG_COPY_KEY := KEY_QUOTELEFT" in env_code)
        check("调试面板:开关默认值 = 场景设置",
              "var debug_panel: bool = true" in env_code)
        check("调试面板:主场景里引用了环境脚本",
              'id="env_script"' in text)

        # ② 代码生成:开着面板时打印节点会往面板推一行
        # ⚠ 变量名别和外层重名(踩过:`printer` 是前面那张图里的节点,
        # 这里再绑一次会把它**换掉**,后面的"折叠成函数"就对着一个
        # 已被删掉的节点操作 -> KeyError)。
        dbg_tree = bpy.data.node_groups.new("调试冒烟", "GKGameLogicTree")
        dbg_ready = dbg_tree.nodes.new("GKNodeOnReady")
        dbg_printer = dbg_tree.nodes.new("GKNodePrint")
        dbg_printer.gk_text = "调试一行"
        dbg_tree.links.new(dbg_ready.outputs["执行"], dbg_printer.inputs["执行"])
        dbg_code, _dbg_warnings = codegen.generate(dbg_tree, {
            "dimension": "3", "base_class": "CharacterBody3D",
            "debug_panel": True,
        })
        check("调试面板:打印节点会往面板推一行",
              "var _gk_debug_logs: Array = []" in dbg_code
              and 'print("调试一行")' in dbg_code
              and '_gk_debug_log_append("调试一行")' in dbg_code)
        bpy.data.node_groups.remove(dbg_tree)

        # 收尾:开关关回去、物体清掉,别影响后面的步骤
        settings.debug_panel = False
        for leftover in list(bpy.context.scene.objects):
            bpy.data.objects.remove(leftover, do_unlink=True)
    except Exception as exc:
        check("调试面板", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 常用件(第二十五轮):追人 / 变量加减 / 冷却 / 数字转文字 ----
    # 四个新节点的**注册与引脚**在真 Blender 里过一遍(bl_icon 写错会让
    # 整个节点注册失败,那条路只有真 Blender 能验),再生成一次代码。
    try:
        gap_tree = bpy.data.node_groups.new("常用件冒烟", "GKGameLogicTree")
        gap_physics = gap_tree.nodes.new("GKNodeOnPhysicsProcess")
        gap_chase = gap_tree.nodes.new("GKNodeMoveToward")
        gap_chase.name = "追人"
        gap_chase.gk_goal = "小玩家"
        gap_chase.gk_speed = 3.0
        gap_add = gap_tree.nodes.new("GKNodeAddVar")
        gap_add.name = "金币加一"
        gap_add.gk_var_name = "金币数"
        # 侧栏声明成 **int** —— 『变量_增减』才会走"浮点表达式补 int(...)"
        # 那条路(没声明的话按 float 处理,生成的是不带 int() 的版本)
        gap_var = gap_tree.gk_vars.add()
        gap_var.var_name = "金币数"
        gap_var.var_type = "int"
        gap_var.default_value = "0"
        gap_cd = gap_tree.nodes.new("GKNodeCooldown")
        gap_cd.name = "受伤冷却"
        gap_num = gap_tree.nodes.new("GKNodeNumberText")
        gap_hud = gap_tree.nodes.new("GKNodeUiText")
        gap_hud.gk_text = "金币"
        for link_pair in (
                (gap_physics, "执行", gap_chase, "执行"),
                (gap_chase, "完成", gap_add, "执行"),
                (gap_add, "完成", gap_cd, "执行"),
                (gap_cd, "可以", gap_hud, "执行"),
                (gap_num, "文字", gap_hud, "文字")):
            gap_tree.links.new(link_pair[0].outputs[link_pair[1]],
                               link_pair[2].inputs[link_pair[3]])
        gap_inputs = [s.name for s in gap_chase.inputs]
        gap_outputs = [s.name for s in gap_cd.outputs]
        check("常用件:『移动_朝向目标』的引脚(执行/目标/速度)",
              gap_inputs == ["执行", "目标", "速度"], str(gap_inputs))
        check("常用件:『流程_冷却』两个出口(可以/还在冷却)",
              gap_outputs == ["可以", "还在冷却"], str(gap_outputs))
        check("常用件:『变量_增减』有『新值』输出",
              any(s.name == "新值" for s in gap_add.outputs),
              str([s.name for s in gap_add.outputs]))
        gap_code, gap_warnings = codegen.generate(gap_tree, {
            "dimension": "3", "base_class": "CharacterBody3D",
            "export_tunables": True,
        })
        check("常用件:生成代码(追人 / 加减 / 冷却 / 数字文字)",
              ".velocity = " in gap_code
              and "move_and_slide()" in gap_code
              and "金币数 = int((金币数 + 1.0))" in gap_code
              and "Time.get_ticks_msec() - _gk_cd_ticks" in gap_code
              and "_gk_num_text(" in gap_code)
        check("常用件:无告警", not gap_warnings, str(gap_warnings))
        bpy.data.node_groups.remove(gap_tree)
    except Exception as exc:
        check("常用件", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 常用件二批(第二十六轮):加速/到达 / 夹范围 / 抽稀 / 闪红 / 飘字 ----
    # 两个**新节点**(特效_闪红、界面_飘字)的注册只能在真 Blender 里验
    # (bl_icon 写错会让整个类注册失败),所以这里建图 + 出一次代码。
    try:
        r26_tree = bpy.data.node_groups.new("常用件二批冒烟", "GKGameLogicTree")
        r26_physics = r26_tree.nodes.new("GKNodeOnPhysicsProcess")
        r26_chase = r26_tree.nodes.new("GKNodeMoveToward")
        r26_chase.name = "追人"
        r26_chase.gk_goal = "小玩家"
        r26_chase.gk_speed = 3.0
        r26_chase.gk_accel = 8.0          # 平滑加减速
        r26_chase.gk_stop_distance = 1.5
        r26_branch = r26_tree.nodes.new("GKNodeBranch")
        r26_branch.name = "到了吗"
        r26_cd = r26_tree.nodes.new("GKNodeCooldown")
        r26_cd.name = "每两次"
        r26_cd.gk_every = 2               # 抽稀:每来 2 次放行 1 次
        r26_add = r26_tree.nodes.new("GKNodeAddVar")
        r26_add.name = "扣血"
        r26_add.gk_var_name = "血量"
        r26_add.gk_amount = -1.0
        r26_add.gk_clamp = True
        r26_add.gk_min = 0.0
        r26_add.gk_max = 3.0
        r26_hp = r26_tree.gk_vars.add()
        r26_hp.var_name = "血量"
        r26_hp.var_type = "float"
        r26_hp.default_value = "3.0"
        r26_flash = r26_tree.nodes.new("GKNodeFlashRed")
        r26_flash.name = "受伤闪红"
        r26_text = r26_tree.nodes.new("GKNodeFloatText")
        r26_text.name = "受伤飘字"
        r26_text.gk_target = "小玩家"
        r26_text.gk_prefix = "-"
        for link_pair in (
                (r26_physics, "执行", r26_chase, "执行"),
                (r26_chase, "到达", r26_branch, "条件"),
                (r26_chase, "完成", r26_branch, "执行"),
                (r26_branch, "是", r26_cd, "执行"),
                (r26_cd, "可以", r26_add, "执行"),
                (r26_add, "完成", r26_flash, "执行"),
                (r26_flash, "完成", r26_text, "执行")):
            r26_tree.links.new(link_pair[0].outputs[link_pair[1]],
                               link_pair[2].inputs[link_pair[3]])
        r26_outputs = [socket.name for socket in r26_chase.outputs]
        r26_inputs = [socket.name for socket in r26_flash.inputs]
        check("常用件二批:『移动_朝向目标』的出口(完成/到达)",
              r26_outputs == ["完成", "到达"], str(r26_outputs))
        check("常用件二批:『特效_闪红』注册成功(执行/秒)",
              r26_inputs == ["执行", "秒"], str(r26_inputs))
        r26_code, r26_warnings = codegen.generate(r26_tree, {
            "dimension": "3", "base_class": "CharacterBody3D",
            "export_tunables": True,
        })
        check("常用件二批:生成代码(加速 / 到达 / 夹取 / 抽稀 / 闪红 / 飘字)",
              "velocity.move_toward(" in r26_code
              and "get_physics_process_delta_time()" in r26_code
              and ".length() <= 1.5" in r26_code
              and "血量 = clampf((血量 + -1.0), 0.0, 3.0)" in r26_code
              and "_gk_cd_count % 2 == 0" in r26_code
              and "func _gk_flash_3d(node: Node, color: Color, "
                  "seconds: float) -> void:" in r26_code
              and "Label3D.new()" in r26_code
              and "_gk_num_text(1.0)" in r26_code)
        check("常用件二批:无告警", not r26_warnings, str(r26_warnings))
        bpy.data.node_groups.remove(r26_tree)
    except Exception as exc:
        check("常用件二批", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 先放完再重复(第二十七轮):动画_播放的『重复触发时』----
    # 选项**键**没改(老 .blend 的值仍然对得上)+ 填物体名时走"从它开始找
    # 播放器"那条路(以前直接把物体当播放器 -> 运行时对着 Area3D 调
    # has_animation 报错,整条链断掉)。
    try:
        r27_tree = bpy.data.node_groups.new("先放完再重复冒烟", "GKGameLogicTree")
        r27_physics = r27_tree.nodes.new("GKNodeOnPhysicsProcess")
        r27_key = r27_tree.nodes.new("GKNodeKey")
        r27_key.name = "E 键"
        r27_key.gk_key = "E"
        r27_key.gk_action = "key_e"
        r27_key.gk_trigger = "JUST"
        r27_branch = r27_tree.nodes.new("GKNodeBranch")
        r27_branch.name = "按了 E?"
        r27_play = r27_tree.nodes.new("GKNodePlayAnim")
        r27_play.name = "敌1受击抖动"
        r27_play.gk_player = "敌1"
        r27_play.gk_anim_name = "敌1抖动"
        r27_play.gk_blend_mode = "CONTINUE"
        for link_pair in ((r27_key, "结果", r27_branch, "条件"),
                          (r27_physics, "执行", r27_branch, "执行"),
                          (r27_branch, "是", r27_play, "执行")):
            r27_tree.links.new(link_pair[0].outputs[link_pair[1]],
                               link_pair[2].inputs[link_pair[3]])
        r27_items = next(entry[5] for entry in r27_play.gk_prop_defs
                         if entry[0] == "gk_blend_mode")
        r27_code, r27_warnings = codegen.generate(r27_tree, {
            "dimension": "3", "base_class": "CharacterBody3D",
        })
        check("先放完再重复:选项键没变(REPLACE/CONTINUE)",
              [item[0] for item in r27_items] == ["REPLACE", "CONTINUE"],
              str([item[0] for item in r27_items]))
        check("先放完再重复:填物体名 -> 从它开始找播放器 + 在播就跳过",
              "_gk_find_animation_player(anim_敌1)" in r27_code
              and "current_animation != _anim" in r27_code
              and '"敌1抖动"' in r27_code)
        check("先放完再重复:无告警", not r27_warnings, str(r27_warnings))
        bpy.data.node_groups.remove(r27_tree)
    except Exception as exc:
        check("先放完再重复", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- UI 第一版(第三十七轮):对齐 / 图片 / 面板 / 设置 / 暂停 / 对话框 ----
    # 六个新节点的注册(bl_icon 写错会让整个类注册失败)+ 建图出一次代码。
    # 选图片的算子在真 Blender 里也要能调起来(枚举里得真有图片)。
    try:
        r37_tree = bpy.data.node_groups.new("界面冒烟", "GKGameLogicTree")
        r37_ready = r37_tree.nodes.new("GKNodeOnReady")
        r37_panel = r37_tree.nodes.new("GKNodeUiPanel")
        r37_panel.name = "暂停菜单"
        r37_panel.gk_align = "CENTER"
        r37_panel.gk_start_hidden = True
        # 用前面粒子那一段建的"火光贴图"当界面图片(工程里确实有这张图)
        r37_image = r37_tree.nodes.new("GKNodeUiImage")
        r37_image.name = "徽标"
        r37_image.gk_image = "火光贴图"
        r37_image.gk_width = 64.0
        r37_image.gk_parent_widget = "暂停菜单"
        r37_text = r37_tree.nodes.new("GKNodeUiText")
        r37_text.name = "提示"
        r37_text.gk_align = "BOTTOM_CENTER"
        r37_button = r37_tree.nodes.new("GKNodeUiButton")
        r37_button.name = "继续按钮"
        r37_button.gk_parent_widget = "暂停菜单"
        r37_pause = r37_tree.nodes.new("GKNodeUiPauseGame")
        r37_pause.name = "暂停热键"
        r37_pause.gk_pause = False
        r37_pause.gk_menu = "暂停菜单"
        r37_pause.gk_hotkey = True
        r37_dialog = r37_tree.nodes.new("GKNodeUiDialog")
        r37_dialog.name = "开场白"
        r37_dialog.gk_text = "你好"
        r37_set = r37_tree.nodes.new("GKNodeUiSet")
        r37_set.name = "改文字"
        r37_set.gk_widget = "提示"
        r37_set.gk_set_text = True
        r37_set.gk_text = "好了"
        for link_pair in (
                (r37_ready, "执行", r37_panel, "执行"),
                (r37_panel, "完成", r37_image, "执行"),
                (r37_image, "完成", r37_text, "执行"),
                (r37_text, "完成", r37_button, "执行"),
                (r37_button, "完成", r37_pause, "执行"),
                (r37_pause, "完成", r37_dialog, "执行"),
                (r37_dialog, "完成", r37_set, "执行")):
            r37_tree.links.new(link_pair[0].outputs[link_pair[1]],
                               link_pair[2].inputs[link_pair[3]])
        r37_button_out = [socket.name for socket in r37_button.outputs]
        check("UI:『界面_按钮』两个出口(完成 = 建好了 / 点击 = 点了)",
              r37_button_out == ["完成", "点击"], str(r37_button_out))
        check("UI:『界面_面板』的对齐 / 开局藏属性在",
              r37_panel.gk_align == "CENTER" and r37_panel.gk_start_hidden)
        r37_code, r37_warnings = codegen.generate(r37_tree, {
            "dimension": "3", "base_class": "CharacterBody3D",
            "export_tunables": True,
        })
        check("UI:生成代码(锚点 / 图片 / 面板 / 暂停 / 对话框 / 改控件)",
              "Control.PRESET_CENTER_BOTTOM" in r37_code
              and "TextureRect.new()" in r37_code
              and "StyleBoxFlat.new()" in r37_code
              and "set_anchors_and_offsets_preset(Control.PRESET_CENTER" in r37_code
              and "_gk_apply_pause(false, \"暂停菜单\", true)" in r37_code
              and "process_frame.connect(_gk_pause_watch)" in r37_code
              and "_gk_dialog_show(self," in r37_code
              and "_gk_find_widget(\"提示\")" in r37_code)
        check("UI:无告警(有选图片 / 有控件名)", not r37_warnings, str(r37_warnings))
        # 选图片算子:枚举里必须列得出工程里的图片
        r37_items = operator_io.image_items(None, None)
        check("UI:『选图片』算子列得出图片",
              any(item[0] == "火光贴图" for item in r37_items), str(r37_items)[:120])
        bpy.data.node_groups.remove(r37_tree)
    except Exception as exc:
        check("UI 第一版", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- UI 第二版(第三十八轮):列表 / 填充 / 缓动 / 九宫格 / 输入框 / 焦点 ----
    # 七个新节点都要能注册出来(bl_icon 写错会让整个类注册失败)。
    # 本轮两条核心约束出码必须看得到:
    #   ① 列表是两层(ScrollContainer + VBox),子控件由容器排版 ——
    #      跳过写死的 X / Y,尺寸走 custom_minimum_size(真 Godot 实测);
    #   ② 输入框的『提交』出口走 text_submitted 独立回调,不在主流程里。
    try:
        r38_tree = bpy.data.node_groups.new("界面二冒烟", "GKGameLogicTree")
        r38_ready = r38_tree.nodes.new("GKNodeOnReady")
        r38_list = r38_tree.nodes.new("GKNodeUiList")
        r38_list.name = "存档列表"
        r38_slot = r38_tree.nodes.new("GKNodeUiButton")
        r38_slot.name = "存档槽一"
        r38_slot.gk_parent_widget = "存档列表"
        r38_slot.gk_x = 99.0
        r38_slot.gk_y = 99.0
        r38_fill = r38_tree.nodes.new("GKNodeUiListFill")
        r38_fill.name = "填日志"
        r38_fill.gk_list = "存档列表"
        r38_fill.gk_text = "第一行|第二行"
        r38_patch = r38_tree.nodes.new("GKNodeUiNinePatch")
        r38_patch.name = "标题条"
        r38_patch.gk_image = "火光贴图"
        r38_patch.gk_slice = 16
        r38_input = r38_tree.nodes.new("GKNodeUiInput")
        r38_input.name = "昵称"
        r38_input.gk_placeholder = "输入昵称"
        r38_tween = r38_tree.nodes.new("GKNodeUiTween")
        r38_tween.name = "淡入"
        r38_tween.gk_widget = "标题条"
        r38_read = r38_tree.nodes.new("GKNodeUiReadText")
        r38_read.name = "读昵称"
        r38_read.gk_widget = "昵称"
        r38_focus = r38_tree.nodes.new("GKNodeUiFocus")
        r38_focus.name = "聚焦"
        r38_focus.gk_widget = "存档槽一"
        r38_print = r38_tree.nodes.new("GKNodePrint")
        r38_print.name = "提交打印"
        r38_print.gk_text = "提交了"
        for link_pair in (
                (r38_ready, "执行", r38_list, "执行"),
                (r38_list, "完成", r38_slot, "执行"),
                (r38_slot, "完成", r38_fill, "执行"),
                (r38_fill, "完成", r38_patch, "执行"),
                (r38_patch, "完成", r38_input, "执行"),
                (r38_input, "提交", r38_print, "执行"),
                (r38_input, "完成", r38_tween, "执行"),
                (r38_tween, "完成", r38_focus, "执行")):
            r38_tree.links.new(link_pair[0].outputs[link_pair[1]],
                               link_pair[2].inputs[link_pair[3]])
        # 取文字是表达式节点(没有执行口),接到打印的『内容』上
        r38_tree.links.new(r38_read.outputs["文字"], r38_print.inputs["内容"])
        check("UI2:『界面_输入框』出口是『完成』+『提交』",
              [socket.name for socket in r38_input.outputs] == ["完成", "提交"],
              str([socket.name for socket in r38_input.outputs]))
        r38_code, r38_warnings = codegen.generate(r38_tree, {
            "dimension": "3", "base_class": "CharacterBody3D",
        })
        check("UI2:列表两层 + 子控件由容器排版(不写死坐标 / 用 custom_minimum_size)",
              "ScrollContainer.new()" in r38_code
              and "VBoxContainer.new()" in r38_code
              and '_gk_list_body("存档列表")' in r38_code
              and ".custom_minimum_size = Vector2(160.0, 48.0)" in r38_code
              and "Vector2(99.0, 99.0)" not in r38_code)
        check("UI2:列表填充 / 九宫格 / 缓动 / 取文字 / 焦点都出了码",
              '_gk_list_fill("存档列表", ["第一行", "第二行"]' in r38_code
              and "NinePatchRect.new()" in r38_code
              and ".patch_margin_top = 16" in r38_code
              and '"modulate:a"' in r38_code
              and '_gk_widget_text("昵称")' in r38_code
              and '_gk_find_widget("存档槽一")' in r38_code
              and ".grab_focus()" in r38_code)
        # 『提交』出口的链(打印『取文字』的结果)必须在独立回调函数里 ——
        # 在 _ready 之后、且落在 gk_on_昵称_submit 的函数体里
        r38_submit_at = r38_code.find("func gk_on_昵称_submit")
        check("UI2:输入框回车走独立回调(提交链不在主流程)",
              "text_submitted.connect(gk_on_昵称_submit)" in r38_code
              and r38_submit_at > r38_code.find("func _ready")
              and 'print(_gk_widget_text("昵称"))' in r38_code[r38_submit_at:])
        check("UI2:无告警", not r38_warnings, str(r38_warnings))
        bpy.data.node_groups.remove(r38_tree)
    except Exception as exc:
        check("UI 第二版", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 上帝机默认功能 + 动画三处修复(第三十九轮)----
    # ① 环境脚本里真的有三样新功能:滚轮缩放 / F 附身 / 内置 ESC 退出;
    # ② 动画状态有了『播放速度』属性(节点侧);
    # ③ 『动画_设混合参数』以前是**死节点**(codegen 里没有它)—— 现在出码;
    # ④ 『动画判断_移动状态』的两个阈值有了引脚(卡片上改才读得到)。
    try:
        r39_env = asset_export.render_env_script(
            camera_name="Cam", fov="70.0", god_camera="true", esc_quit="true")
        check("上帝机:滚轮缩放 / F 附身 / 内置 ESC 退出都进了环境脚本",
              "func _gk_god_zoom_tick() -> void:" in r39_env
              and "MOUSE_BUTTON_WHEEL_UP" in r39_env
              and "const _GK_POSSESS_KEY := KEY_F" in r39_env
              and "func _gk_god_possess_tick(delta: float) -> void:" in r39_env
              and "func _gk_esc_tick() -> void:" in r39_env
              and "tree.quit()" in r39_env)

        r39_tree = bpy.data.node_groups.new("动画冒烟39", "GKGameLogicTree")
        r39_ready = r39_tree.nodes.new("GKNodeOnReady")
        r39_param = r39_tree.nodes.new("GKNodeAnimParam")
        r39_param.gk_value = 0.4
        r39_param.gk_shape_index = 1
        r39_tree.links.new(r39_ready.outputs["执行"], r39_param.inputs["执行"])
        r39_code, r39_warnings = codegen.generate(r39_tree, {
            "dimension": "3", "base_class": "CharacterBody3D",
        })
        check("动画:『设混合参数』不再是死节点(出 set_blend_shape_value)",
              "set_blend_shape_value(1, 0.4)" in r39_code
              and "MeshInstance3D" in r39_code)
        check("动画:『设混合参数』无告警", not r39_warnings, str(r39_warnings))
        bpy.data.node_groups.remove(r39_tree)

        r39_anim = bpy.data.node_groups.new("动画树冒烟39", "GKAnimationTree")
        r39_state = r39_anim.nodes.new("GKNodeAnimState")
        r39_state.gk_state_name = "run"
        check("动画:『动画_状态』有『播放速度』(默认 1.0)",
              hasattr(r39_state, "gk_speed")
              and abs(float(r39_state.gk_speed) - 1.0) < 1e-6,
              str(getattr(r39_state, "gk_speed", "<没有这个属性>")))
        r39_loco = r39_anim.nodes.new("GKNodeAnimLocomotion")
        r39_pins = [socket.name for socket in r39_loco.inputs]
        check("动画:『移动状态』有了两个阈值引脚(卡片值才读得到)",
              "速度阈值" in r39_pins and "跑的速度阈值" in r39_pins,
              str(r39_pins))
        # 『同时播 2 / 3』以前**没声明过** —— 卡片画到那一行就报错
        # (用户说的"一部分节点卡片无作用")。现在必须是真属性。
        check("动画:『同时播 2 / 3』是真属性(卡片不再画一半报错)",
              hasattr(r39_state, "gk_anim_action_2")
              and hasattr(r39_state, "gk_anim_action_3")
              and getattr(r39_state, "gk_anim_action_2", None) is None)
        bpy.data.node_groups.remove(r39_anim)
    except Exception as exc:
        check("第三十九轮", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 界面设计图 + 套用式(第四十轮)----
    # ① 第三种节点树类型真的注册上了(能在编辑器类型里选、属性都在);
    # ② 『界面_套用设计』把设计图里的控件全建出来,**父控件排在子控件前面**
    #    (子控件是按名字找父亲挂进去的 —— 顺序错了会掉到界面根下);
    # ③ 设计图里被人手塞的动作节点不会被"建出来";
    # ④ 『界面_按钮点击』的接线是幂等的(is_connected 守卫)。
    try:
        r40_design = bpy.data.node_groups.new("界面设计冒烟40", "GKUiDesignTree")
        check("界面:设计图类型注册成功(预览开关 / 设计分辨率都在)",
              getattr(r40_design, "bl_idname", "") == "GKUiDesignTree"
              and bool(getattr(r40_design, "gk_preview", False))
              and int(getattr(r40_design, "gk_design_width", 0)) == 1152)
        # 故意先建子控件、后建父控件 —— 生成时必须自己排回"父先子后"
        r40_title = r40_design.nodes.new("GKNodeUiText")
        r40_title.name = "冒烟标题"
        r40_title.gk_text = "标题"
        r40_title.gk_parent_widget = "冒烟菜单"
        r40_menu = r40_design.nodes.new("GKNodeUiPanel")
        r40_menu.name = "冒烟菜单"
        r40_menu.gk_width = 400.0
        r40_menu.gk_height = 300.0
        # 动作节点连**塞**都塞不进设计图 —— 节点基类的 poll 按树类型把关,
        # 菜单过滤之外还有这一层兜底。
        r40_before = len(r40_design.nodes)
        try:
            r40_design.nodes.new("GKNodeUiHide")
            r40_blocked = False
        except RuntimeError:
            r40_blocked = True
        check("界面:动作节点进不了设计图(Blender 侧也把关)",
              r40_blocked and len(r40_design.nodes) == r40_before,
              f"blocked={r40_blocked} nodes={len(r40_design.nodes)}")

        r40_tree = bpy.data.node_groups.new("界面玩法冒烟40", "GKGameLogicTree")
        r40_ready = r40_tree.nodes.new("GKNodeOnReady")
        r40_apply = r40_tree.nodes.new("GKNodeUiApplyDesign")
        r40_apply.gk_design = r40_design.name
        r40_tree.links.new(r40_ready.outputs["执行"], r40_apply.inputs["执行"])
        r40_code, r40_warnings = codegen.generate(r40_tree, {
            "dimension": "3", "base_class": "CharacterBody3D",
        })
        r40_panel_at = r40_code.find("_gk_ui_冒烟菜单 = ")
        r40_child_at = r40_code.find('_gk_find_widget("冒烟菜单")')
        check("界面:『界面_套用设计』把设计图的控件建出来(父先子后)",
              r40_panel_at > -1 and r40_child_at > -1
              and r40_panel_at < r40_child_at)
        check("界面:套用设计无告警", not r40_warnings, str(r40_warnings))

        r40_click = r40_tree.nodes.new("GKNodeUiOnClick")
        r40_click.gk_widget = "冒烟按钮"
        r40_tree.links.new(r40_apply.outputs["完成"], r40_click.inputs["执行"])
        r40_code2, r40_warnings2 = codegen.generate(r40_tree, {
            "dimension": "3", "base_class": "CharacterBody3D",
        })
        check("界面:『界面_按钮点击』幂等接线(is_connected 守卫)",
              ".pressed.is_connected(" in r40_code2
              and "is BaseButton" in r40_code2)
        check("界面:按钮点击无告警", not r40_warnings2, str(r40_warnings2))

        r40_panels = [cls.__name__ for cls in bpy.types.Panel.__subclasses__()]
        check("界面:设计图侧栏面板已注册", "GK_PT_UiDesign" in r40_panels,
              str(r40_panels)[:200])

        # 视口叠加预览:算矩形的那段是纯逻辑,能在这儿直接验
        # (真正的画由 3D 视口的绘制回调负责 —— 无头模式没有视口可画)
        from importlib import import_module
        r40_ui = import_module(f"{module_name}.ui")
        r40_rects = r40_ui._ui_preview_rects(r40_design)
        r40_by_name = {item[0]: item for item in r40_rects}
        # 设计图里就两个控件(标题 + 菜单;动作节点进不来,见上面那条)
        r40_place_ok = (
            len(r40_rects) == 2
            and abs(r40_by_name["冒烟菜单"][3] - 400.0) < 1e-6
            and abs(r40_by_name["冒烟标题"][1]
                    - (r40_by_name["冒烟菜单"][1] + r40_title.gk_x)) < 1e-6
            and abs(r40_by_name["冒烟标题"][2]
                    - (r40_by_name["冒烟菜单"][2] + r40_title.gk_y)) < 1e-6)
        check("界面:视口预览的矩形算法(父先子后 + 子控件相对父偏移)",
              r40_place_ok, str(r40_rects))
        check("界面:预览绘制回调已挂上(注销时会摘掉)",
              len(r40_ui._UI_PREVIEW_HANDLE) >= 1)
        try:
            import blf
            import gpu
            import gpu_extras.batch
            r40_api_ok = (hasattr(gpu, "shader")
                          and hasattr(gpu.state, "blend_set")
                          and hasattr(blf, "draw")
                          and hasattr(gpu_extras.batch, "batch_for_shader"))
        except Exception:                                 # noqa: BLE001
            r40_api_ok = False
        check("界面:预览用的绘制接口都在(gpu / blf / batch_for_shader)",
              r40_api_ok)

        bpy.data.node_groups.remove(r40_tree)
        bpy.data.node_groups.remove(r40_design)
    except Exception as exc:
        check("第四十轮", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- AI 图 + 『AI_启用』(第四十一轮)----
    # ① 第四种节点树类型真的注册上了(预览开关都在);
    # ② 玩法节点进不了 AI 图、AI 节点进不了玩法图(Blender 侧 poll 把关);
    # ③ "巡逻 ->(看见)-> 追击 ->(丢失)-> 巡逻"的**环**用连线搭 ——
    #    第四十一轮实测:Blender 4.5.5 允许节点连线成环(保存 / 读回都正常),
    #    所以默认新建的 AI 图就是连线版;解析走"连线优先";
    # ④ 『AI_启用』生成:起始状态 / 巡逻翻面 / 追击停距 / 看见-丢失的距离条件;
    # ⑤ 基类自动推成 CharacterBody3D(AI 行为要 velocity + move_and_slide);
    # ⑥ 侧栏面板 + 预览回调 + 6 个特效/函数节点在**重载后**还在(见 _SUBMODULES)。
    try:
        r41_ai = bpy.data.node_groups.new("AI冒烟41", "GKGameAiTree")
        check("AI:图类型注册成功(预览开关在)",
              getattr(r41_ai, "bl_idname", "") == "GKGameAiTree"
              and bool(getattr(r41_ai, "gk_preview", False)))

        r41_patrol = r41_ai.nodes.new("GKNodeAiState")
        r41_patrol.name = "巡逻"
        r41_patrol.gk_state_name = "巡逻"
        r41_patrol.gk_behavior = "PATROL"
        r41_patrol.gk_patrol_a = "巡逻点甲"
        r41_patrol.gk_patrol_b = "巡逻点乙"
        r41_chase = r41_ai.nodes.new("GKNodeAiState")
        r41_chase.name = "追击"
        r41_chase.gk_state_name = "追击"
        r41_chase.gk_behavior = "CHASE"
        r41_chase.gk_goal = "玩家"

        r41_see = r41_ai.nodes.new("GKNodeAiTransition")
        r41_see.name = "看见玩家"
        r41_see.gk_cond_source = "SEE"
        r41_see.gk_goal = "玩家"
        r41_see.gk_sight = 9.0
        r41_back = r41_ai.nodes.new("GKNodeAiTransition")
        r41_back.name = "丢失玩家"
        r41_back.gk_cond_source = "LOST"
        r41_back.gk_goal = "玩家"
        r41_back.gk_sight = 9.0

        # 连线搭出完整的环(状态 -> 转场『上游』;转场『转到』-> 状态『进入』)
        try:
            r41_ai.links.new(r41_patrol.outputs["转到"],
                             r41_see.inputs["上游"])
            r41_ai.links.new(r41_see.outputs["转到"],
                             r41_chase.inputs["进入"])
            r41_ai.links.new(r41_chase.outputs["转到"],
                             r41_back.inputs["上游"])
            r41_ai.links.new(r41_back.outputs["转到"],
                             r41_patrol.inputs["进入"])
            r41_loop_ok = True
        except RuntimeError:
            r41_loop_ok = False
        check("AI:状态连线成环在 Blender 里可行(默认新图就是这么搭的)",
              r41_loop_ok and len(r41_ai.links) == 4,
              f"links={len(r41_ai.links)} loop_ok={r41_loop_ok}")

        # 解析(连线优先;下拉一个字没填)
        r41_graph = node_lib.ai_tree_graph(r41_ai)
        check("AI:连线版环图解析正确(2 状态 / 2 转场 / 起始 = 巡逻)",
              len(r41_graph["states"]) == 2
              and len(r41_graph["edges"]) == 2
              and node_lib.ai_state_name(r41_graph["entry"]) == "巡逻"
              and not r41_graph["problems"],
              f"edges={len(r41_graph['edges'])} "
              f"problems={r41_graph['problems'][:2]}")

        # 玩法图:『AI_启用』引用这张图
        r41_tree = bpy.data.node_groups.new("AI玩法冒烟41", "GKGameLogicTree")
        r41_ready = r41_tree.nodes.new("GKNodeOnReady")
        r41_enable = r41_tree.nodes.new("GKNodeAiEnable")
        r41_enable.gk_tree_name = r41_ai.name
        r41_tree.links.new(r41_ready.outputs["执行"], r41_enable.inputs["执行"])

        r41_code, r41_warnings = codegen.generate(r41_tree, {
            "dimension": "3", "base_class": "CharacterBody3D",
        })
        check("AI:『AI_启用』生成状态机 + 行为",
              'ai_state = "巡逻"' in r41_code
              and "if ai_state.is_empty():" in r41_code
              and 'if ai_state == "巡逻":' in r41_code
              and "_gk_ai_patrol_1 = not _gk_ai_patrol_1" in r41_code
              and "move_and_slide()" in r41_code,
              r41_code[-600:])
        check("AI:看见 / 丢失目标 -> 距离比较(视野距离 9.0)",
              r41_code.count(".length() <= 9.0") >= 1
              and r41_code.count(".length() > 9.0") >= 1,
              r41_code[-600:])
        check("AI:生成无告警", not r41_warnings, str(r41_warnings)[:300])

        # 基类:validation 静态扫 AI 树(AI_启用 是入口,同样要角色体)
        r41_need, _r41_why = codegen.required_base_class(
            r41_tree, {"dimension": "3"})
        check("AI:基类自动推成 CharacterBody3D(行为要 velocity)",
              r41_need == "CharacterBody3D", str(r41_need))

        # AI 图里塞不进玩法图的动作节点(poll 把关)
        r41_before = len(r41_ai.nodes)
        try:
            r41_ai.nodes.new("GKNodeMoveDirection")
            r41_ai_blocked = False
        except RuntimeError:
            r41_ai_blocked = True
        check("AI:玩法节点进不了 AI 图(Blender 侧也把关)",
              r41_ai_blocked and len(r41_ai.nodes) == r41_before)

        # 玩法图里也塞不进『AI_状态』(它只属于 AI 图)
        r41_tree_nodes = len(r41_tree.nodes)
        try:
            r41_tree.nodes.new("GKNodeAiState")
            r41_state_blocked = False
        except RuntimeError:
            r41_state_blocked = True
        check("AI:『AI_状态』进不了玩法图",
              r41_state_blocked and len(r41_tree.nodes) == r41_tree_nodes)

        r41_panels = [cls.__name__ for cls in bpy.types.Panel.__subclasses__()]
        check("AI:侧栏面板已注册", "GK_PT_AiDesign" in r41_panels,
              str(r41_panels)[:200])

        # 重载不该丢节点:特效 / 函数两批以前漏在 _SUBMODULES 表外,
        # 重载后从 ALL_CLASSES 里整批消失(第四十一轮抓到,174 -> 168)。
        # 冒烟本身跑在 enable 触发的重载之后 —— 直接断言它们今天还在。
        r41_names = [c.bl_idname for c in node_lib.ALL_CLASSES]
        check("重载后 特效/函数 节点还在(_SUBMODULES 补上了 _fx/_functions)",
              all(name in r41_names for name in
                  ("GKNodeFxPlay", "GKNodeFxStop", "GKNodeFlashRed",
                   "GKNodeFxBurst", "GKNodeFunctionDefine",
                   "GKNodeFunctionCall")),
              f"count={len(r41_names)}")

        from importlib import import_module
        r41_ui = import_module(f"{module_name}.ui")
        check("AI:预览绘制回调已挂上(注销时会摘掉)",
              len(r41_ui._AI_PREVIEW_HANDLE) >= 1)

        bpy.data.node_groups.remove(r41_tree)
        bpy.data.node_groups.remove(r41_ai)
    except Exception as exc:
        check("第四十一轮", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 显示设置图(第四十二轮:『游戏显示』)----
    # ① 第五种节点树类型注册上了,新建时四张设置卡一张不少(fake user 也在);
    # ② 玩法节点进不了显示图、设置卡进不了玩法图(Blender 侧 poll 把关);
    # ③ 卡上的开关是**写透**场景设置:只有一处真相(改场景 -> 摘要跟着变);
    # ④ 四张卡的 draw_buttons 都能画完(第三十九轮"卡片画一半就报错"的坑);
    # ⑤ 老入口真的搬走了:侧栏导出盒 / 物体面板的『视口预览』盒里那几行都不在了。
    try:
        from importlib import import_module
        import inspect
        r42_ui = import_module(f"{module_name}.ui")

        r42_tree = r42_ui._new_display_tree()
        r42_names = [getattr(node, "bl_idname", "") for node in r42_tree.nodes]
        check("显示:第五种图类型注册成功,新建带齐四张设置卡",
              getattr(r42_tree, "bl_idname", "") == "GKDisplayTree"
              and r42_names == list(node_lib.DISPLAY_CARD_IDS)
              and bool(getattr(r42_tree, "use_fake_user", False)),
              str(r42_names))
        check("显示:四张卡补不重复(全都还在时不加)",
              node_lib.missing_display_cards(r42_tree) == []
              and r42_ui._fill_display_cards(r42_tree) == 0)

        # 玩法节点进不了显示图 / 设置卡进不了玩法图(poll 把关)
        r42_before = len(r42_tree.nodes)
        try:
            r42_tree.nodes.new("GKNodeMoveDirection")
            r42_move_blocked = False
        except RuntimeError:
            r42_move_blocked = True
        check("显示:玩法节点进不了显示图(Blender 侧把关)",
              r42_move_blocked and len(r42_tree.nodes) == r42_before)
        r42_logic = bpy.data.node_groups.new("显示玩法冒烟42", "GKGameLogicTree")
        try:
            r42_logic.nodes.new("GKNodeDisplayCollision")
            r42_card_blocked = False
        except RuntimeError:
            r42_card_blocked = True
        check("显示:设置卡进不了玩法图(不会污染代码生成)",
              r42_card_blocked and len(r42_logic.nodes) == 0)

        # 写透:卡上读写的就是场景设置本身(改场景 -> 摘要跟着变,没有第二份值)
        r42_settings = bpy.context.scene.gk_settings
        r42_rows = node_lib.display_settings_rows(r42_settings)
        check("显示:侧栏摘要读的是场景设置(四项都在)",
              [row[0] for row in r42_rows]
              == ["调试面板", "运行日志", "画面风格", "环境氛围"])
        r42_old_toon = bool(getattr(r42_settings, "toon_materials", True))
        r42_old_follow = bool(getattr(r42_settings, "toon_follow_light", False))
        r42_settings.toon_materials = True
        r42_settings.toon_follow_light = True
        r42_rows = node_lib.display_settings_rows(r42_settings)
        check("显示:改场景设置 -> 摘要立刻反映(写透,不用点应用)",
              "明暗跟随太阳" in r42_rows[2][1] and bool(r42_rows[2][2]))
        r42_settings.toon_materials = r42_old_toon
        r42_settings.toon_follow_light = r42_old_follow

        # 卡片 draw_buttons 都要能画完
        class _R42Layout:      # 最小替身:随便怎么调都返回自己
            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AttributeError(name)

                def recorder(*_args, **_kwargs):
                    return self
                return recorder

        r42_draw_ok = True
        r42_draw_error = ""
        for r42_card in r42_tree.nodes:
            try:
                r42_card.draw_buttons(bpy.context, _R42Layout())
            except Exception as exc:                      # noqa: BLE001
                r42_draw_ok = False
                r42_draw_error = (f"{getattr(r42_card, 'bl_label', '?')}: "
                                  f"{type(exc).__name__}: {exc}")
                break
        check("显示:四张卡的 draw_buttons 都能画完", r42_draw_ok,
              r42_draw_error)

        r42_panels = [cls.__name__ for cls in bpy.types.Panel.__subclasses__()]
        check("显示:侧栏面板已注册", "GK_PT_DisplayTree" in r42_panels,
              str(r42_panels)[:200])

        # 老入口真的搬走了(用户选的"完全搬走"):侧栏导出盒不再画
        # 预览碰撞体 / 调试面板 / 卡通材质,物体面板不再画碰撞线框按钮。
        r42_export_src = inspect.getsource(r42_ui.GK_PT_Main.draw)
        r42_obj_src = inspect.getsource(r42_ui.GK_PT_ObjectPhysics.draw)
        check("显示:老入口搬走了(侧栏 / 物体面板都不再画那些开关)",
              "gk.preview_colliders" not in r42_export_src
              and "gk.debug_panel" not in r42_export_src
              and "toon_materials" not in r42_export_src
              and "gk.show_collision" not in r42_obj_src)

        bpy.data.node_groups.remove(r42_logic)
        bpy.data.node_groups.remove(r42_tree)
    except Exception as exc:
        check("第四十二轮", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 环境与后处理(第四十九轮:阶段②环境与后处理)----
    # ① 场景设置里开着『环境与后处理』-> 主场景 .tscn 里多出 WorldEnvironment
    #    节点 + Environment 子资源(背景 / 环境光 / 雾 / 泛光 / 色调都在);
    # ② **关着时一个字符都不多**(老工程产物一字不变);
    # ③ 天空背景多两个子资源(ProceduralSkyMaterial + Sky);
    # ④ 『显示_环境氛围』卡的 draw_buttons 画得完(第五张卡)。
    try:
        import shutil as _r49_shutil

        asset_export = import_module(f"{module_name}.asset_export")
        r49_settings = bpy.context.scene.gk_settings
        r49_saved = {name: getattr(r49_settings, name) for name in (
            "env_enabled", "env_background", "env_fog", "env_glow",
            "env_adjust", "env_tonemap", "env_ambient_energy")}

        r49_work = os.path.join(tempfile.gettempdir(), "gk_env_smoke")
        _r49_shutil.rmtree(r49_work, ignore_errors=True)
        os.makedirs(r49_work, exist_ok=True)
        for r49_leftover in list(bpy.context.scene.objects):
            bpy.data.objects.remove(r49_leftover, do_unlink=True)
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.0, 0.0, 0.0))
        r49_ground = bpy.context.active_object
        r49_ground.name = "环境地面"
        bpy.context.view_layer.update()
        check("环境:世界 glb 导出成功",
              asset_export.export_scene_glb(r49_work, "World")[1])

        # 关着 -> 产物里连节点都没有
        r49_settings.env_enabled = False
        r49_path, _r49_count = asset_export.write_world_scene(
            r49_work, "World", scene=bpy.context.scene, dimension="3")
        with open(r49_path, encoding="utf-8") as handle:
            r49_off = handle.read()
        check("环境:关着时产物里没有 WorldEnvironment",
              "WorldEnvironment" not in r49_off, r49_off[-400:])

        # 开着:纯色背景 + 环境光 + 雾 + 泛光 + 调色
        r49_settings.env_enabled = True
        r49_settings.env_background = "COLOR"
        r49_settings.env_ambient_energy = 0.42
        r49_settings.env_fog = True
        r49_settings.env_fog_density = 0.07
        r49_settings.env_glow = True
        r49_settings.env_adjust = True
        r49_settings.env_tonemap = "ACES"
        r49_path, _r49_count = asset_export.write_world_scene(
            r49_work, "World", scene=bpy.context.scene, dimension="3")
        with open(r49_path, encoding="utf-8") as handle:
            r49_on = handle.read()
        check("环境:.tscn 里有 WorldEnvironment 节点 + Environment 子资源",
              '[node name="WorldEnvironment" type="WorldEnvironment"'
              in r49_on
              and '[sub_resource type="Environment" id="gk_environment"]'
              in r49_on
              and 'environment = SubResource("gk_environment")' in r49_on,
              r49_on[-700:])
        check("环境:背景 / 环境光 / 雾 / 泛光 / 色调字段都写进去了",
              "background_mode = 1" in r49_on
              and "ambient_light_energy = 0.420000" in r49_on
              and "fog_enabled = true" in r49_on
              and "fog_density = 0.070000" in r49_on
              and "glow_enabled = true" in r49_on
              and "adjustment_enabled = true" in r49_on
              and "tonemap_mode = 3" in r49_on)

        # 天空:多两个子资源 + background_mode = 2
        r49_settings.env_background = "SKY"
        r49_path, _r49_count = asset_export.write_world_scene(
            r49_work, "World", scene=bpy.context.scene, dimension="3")
        with open(r49_path, encoding="utf-8") as handle:
            r49_sky = handle.read()
        check("环境:天空背景导出 Sky + 程序化天空材质",
              '[sub_resource type="Sky" id="gk_sky"]' in r49_sky
              and '[sub_resource type="ProceduralSkyMaterial"' in r49_sky
              and "background_mode = 2" in r49_sky
              and 'sky = SubResource("gk_sky")' in r49_sky)

        # 第五张卡的 draw_buttons 也要能画完
        class _R49Layout:
            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AttributeError(name)

                def recorder(*_args, **_kwargs):
                    return self
                return recorder

        try:
            node_lib.GKNodeDisplayEnvironment.draw_buttons(
                None, bpy.context, _R49Layout())
            r49_draw_ok = True
            r49_draw_error = ""
        except Exception as exc:                          # noqa: BLE001
            r49_draw_ok = False
            r49_draw_error = f"{type(exc).__name__}: {exc}"
        check("环境:『显示_环境氛围』卡 draw_buttons 画得完", r49_draw_ok,
              r49_draw_error)

        # 收尾:设置还原 + 清掉临时物体
        for r49_name, r49_value in r49_saved.items():
            setattr(r49_settings, r49_name, r49_value)
        bpy.data.objects.remove(r49_ground, do_unlink=True)
        _r49_shutil.rmtree(r49_work, ignore_errors=True)
    except Exception as exc:
        check("第四十九轮", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 对话选择(第五十轮:阶段③『对话_选择』分支)----
    # ① 节点注册上了,出口跟着『选项数』走(选项1..N + 『选了第几个』);
    # ② **没有『完成』出口**(有的话选都没选后面的逻辑就跑了);
    # ③ 玩法图能放它、『游戏界面』设计图放不进去;
    # ④ 生成物里是 `match _gk_choice_picked:` + 每个出口一个 case。
    try:
        from importlib import import_module as _r50_import

        r50_codegen = _r50_import(f"{module_name}.codegen")
        r50_choice_cls = getattr(node_lib, "GKNodeUiChoice", None)
        check("对话选择:节点注册成功", r50_choice_cls is not None)

        r50_tree = bpy.data.node_groups.new("对话选择冒烟50", "GKGameLogicTree")
        r50_ready = r50_tree.nodes.new("GKNodeOnReady")
        r50_choice = r50_tree.nodes.new("GKNodeUiChoice")
        r50_choice.gk_question = "你怎么做？"
        r50_choice.gk_option_count = 3
        r50_choice.gk_options = "救她|自己跑|再想想"
        r50_choice.build_sockets()
        r50_names = [socket.name for socket in r50_choice.outputs]
        check("对话选择:出口 = 选项1..N + 『选了第几个』",
              r50_names == ["选项1", "选项2", "选项3", "选了第几个"],
              str(r50_names))
        check("对话选择:没有『完成』出口(免得没选就往下跑)",
              r50_choice.outputs.get("完成") is None)
        check("对话选择:选项文字按 | 分隔",
              r50_choice.option_texts() == ["救她", "自己跑", "再想想"],
              str(r50_choice.option_texts()))

        # 改『选项数』-> 出口立刻变(蓝图手感)
        r50_choice.gk_option_count = 2
        r50_choice.build_sockets()
        check("对话选择:改『选项数』出口跟着变",
              len([s for s in r50_choice.outputs
                   if s.name.startswith("选项")]) == 2,
              str([s.name for s in r50_choice.outputs]))

        # 出口接两条链 -> 生成 match + 各自的分支
        r50_choice.gk_option_count = 3
        r50_choice.build_sockets()
        r50_one = r50_tree.nodes.new("GKNodePrint")
        r50_one.gk_text = "选了一"
        r50_two = r50_tree.nodes.new("GKNodePrint")
        r50_two.gk_text = "选了二"
        r50_tree.links.new(r50_ready.outputs["执行"], r50_choice.inputs["执行"])
        r50_tree.links.new(r50_choice.outputs["选项1"], r50_one.inputs["执行"])
        r50_tree.links.new(r50_choice.outputs["选项2"], r50_two.inputs["执行"])
        r50_code, r50_warnings = r50_codegen.generate(r50_tree, {
            "dimension": "3", "base_class": "CharacterBody3D"})
        check("对话选择:生成无告警", not r50_warnings, str(r50_warnings)[:300])
        check("对话选择:生成 match + 各出口的 case",
              "_gk_choice_show(self," in r50_code
              and "while _gk_choice_picked < 0:" in r50_code
              and "match _gk_choice_picked:" in r50_code
              and 'print("选了一")' in r50_code
              and 'print("选了二")' in r50_code,
              r50_code[:400])
        check("对话选择:没连线的出口补 pass",
              "        3:\n            pass" in r50_code)
        check("对话选择:数字键 / 按钮两条路都在",
              "func gk_choice_input_event" in r50_code
              and "button.pressed.connect(_gk_choice_pick.bind(index + 1))"
              in r50_code)

        # 设计图里放不进去(它不是"控件构建"节点)
        r50_design = bpy.data.node_groups.new("对话选择设计冒烟50", "GKUiDesignTree")
        try:
            r50_design.nodes.new("GKNodeUiChoice")
            r50_blocked = False
        except RuntimeError:
            r50_blocked = True
        check("对话选择:『游戏界面』设计图里放不进去",
              r50_blocked and len(r50_design.nodes) == 0)

        # 第五张卡之后又多了一张卡之外的东西 —— draw_buttons 也要画得完
        class _R50Layout:
            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AttributeError(name)

                def recorder(*_args, **_kwargs):
                    return self
                return recorder

        try:
            r50_choice.draw_buttons(bpy.context, _R50Layout())
            check("对话选择:节点卡片能画完(选项逐条列出来)", True)
        except Exception as exc:                          # noqa: BLE001
            check("对话选择:节点卡片能画完(选项逐条列出来)", False,
                  f"{type(exc).__name__}: {exc}")

        bpy.data.node_groups.remove(r50_design)
        bpy.data.node_groups.remove(r50_tree)
    except Exception as exc:
        check("第五十轮", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 屏幕暗角(第五十一轮:阶段④屏幕氛围)----
    # ① 节点注册上了,属性齐(强度 / 内圈 / 柔和 / 颜色 / 过渡);
    # ② 生成物:自己的着色器常量 + 全屏 ColorRect + 层号 92(滤镜 90 之上);
    # ③ 两个屏幕效果能叠着用(滤镜 + 暗角各有各的常量 / 层);
    # ④ 节点卡片画得完。
    try:
        r51_codegen = _r50_import(f"{module_name}.codegen")
        r51_cls = getattr(node_lib, "GKNodeScreenVignette", None)
        check("屏幕暗角:节点注册成功", r51_cls is not None)

        r51_tree = bpy.data.node_groups.new("屏幕暗角冒烟51", "GKGameLogicTree")
        r51_ready = r51_tree.nodes.new("GKNodeOnReady")
        r51_vig = r51_tree.nodes.new("GKNodeScreenVignette")
        r51_vig.gk_amount = 0.7
        r51_vig.gk_radius = 0.55
        r51_vig.gk_edge = 0.35
        r51_vig.gk_fade = 0.0
        r51_tree.links.new(r51_ready.outputs["执行"], r51_vig.inputs["执行"])
        r51_code, r51_warnings = r51_codegen.generate(r51_tree, {
            "dimension": "3", "base_class": "CharacterBody3D"})
        check("屏幕暗角:生成无告警", not r51_warnings, str(r51_warnings)[:300])
        check("屏幕暗角:自带着色器常量 + 全屏遮罩 + 层号 92",
              "const _GK_VIGNETTE_SHADER :=" in r51_code
              and "func _gk_ensure_vignette(" in r51_code
              and "ColorRect.new()" in r51_code
              and "layer = 92" in r51_code,
              r51_code[-500:])
        check("屏幕暗角:读屏走 hint_screen_texture(Godot 4.7 没有 SCREEN_TEXTURE)",
              "hint_screen_texture" in r51_code
              and "SCREEN_TEXTURE" not in r51_code)
        check("屏幕暗角:四个参数都写进去了",
              'set_shader_parameter("vignette_radius", 0.55)' in r51_code
              and 'set_shader_parameter("vignette_edge", 0.35)' in r51_code
              and 'set_shader_parameter("vignette_color"' in r51_code
              and 'set_shader_parameter("vignette_amount"' in r51_code)

        # 和『屏幕_滤镜』叠着用:两个常量、两层都在
        r51_tint = r51_tree.nodes.new("GKNodeScreenFilter")
        r51_tint.gk_amount = 0.6
        r51_tree.links.new(r51_vig.outputs["完成"], r51_tint.inputs["执行"])
        r51_code2, _r51_warn2 = r51_codegen.generate(r51_tree, {
            "dimension": "3", "base_class": "CharacterBody3D"})
        check("屏幕暗角:和『屏幕_滤镜』叠着用(两层各一个着色器)",
              "const _GK_FILTER_SHADER :=" in r51_code2
              and "const _GK_VIGNETTE_SHADER :=" in r51_code2
              and "layer = 90" in r51_code2 and "layer = 92" in r51_code2,
              "缺常量或层号")

        class _R51Layout:
            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AttributeError(name)

                def recorder(*_args, **_kwargs):
                    return self
                return recorder

        try:
            r51_vig.draw_buttons(bpy.context, _R51Layout())
            check("屏幕暗角:节点卡片能画完", True)
        except Exception as exc:                          # noqa: BLE001
            check("屏幕暗角:节点卡片能画完", False,
                  f"{type(exc).__name__}: {exc}")

        bpy.data.node_groups.remove(r51_tree)
    except Exception as exc:
        check("第五十一轮", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 资产库(第四十三轮:『游戏资产』)----
    # ① 第六种图类型注册成功;`_fill_asset_cards` 把 .blend 里的资产收成卡,
    #    再收一遍不重复;
    # ② 玩法节点进不了资产库、资产卡进不了玩法图(Blender 侧 poll 把关);
    # ③ 『资产』集合:真的从原来的集合摘出来(大纲里和普通物体分开);
    # ④ 跨场景:本体在 A 关,往 B 关摆 —— 第一份自动当本体、第二份是实例,
    #    这样 B 关导出的账本上有本体(实例有模型可用);
    # ⑤ 音频:声音卡进导出清单(名字 / 本地文件 / res:// 路径);
    # ⑥ 四张资产卡的 draw_buttons 都能画完 + 侧栏面板已注册。
    try:
        from importlib import import_module
        import bmesh
        r43_ui = import_module(f"{module_name}.ui")
        r43_sound = import_module(f"{module_name}.sound_assets")
        r43_export = import_module(f"{module_name}.asset_export")

        r43_scene = bpy.context.scene
        r43_mesh = bpy.data.meshes.new("冒烟资产43")
        r43_bm = bmesh.new()
        bmesh.ops.create_cube(r43_bm, size=1.0)
        r43_bm.to_mesh(r43_mesh)
        r43_bm.free()
        r43_obj = bpy.data.objects.new("冒烟树43", r43_mesh)
        r43_scene.collection.objects.link(r43_obj)
        r43_obj.gk_physics.asset_name = "冒烟树43"

        r43_tree = r43_ui._asset_tree_or_new()
        r43_counts = r43_ui._fill_asset_cards(r43_tree)
        r43_cards = node_lib.card_entries(r43_tree)
        r43_model_cards = [path for kind, _name, path, _node in r43_cards
                           if kind == "model"]
        check("资产:第六种图类型注册成功 + 收录出统一『资产卡』",
              getattr(r43_tree, "bl_idname", "") == "GKAssetTree"
              and "冒烟树43" in r43_model_cards,
              f"counts={r43_counts} cards={r43_model_cards}")
        check("资产:重复收录不重复加卡",
              sum(r43_ui._fill_asset_cards(r43_tree).values()) == 0)
        # 统一卡:类型 / 名称 / 路径 / 勾选都读得出来
        r43_model_card = next(
            (card for kind, _name, _path, card in r43_cards if kind == "model"),
            None)
        check("资产:统一卡读得出类型/路径/勾选",
              r43_model_card is not None
              and node_lib.card_kind(r43_model_card) == "model"
              and node_lib.card_path(r43_model_card) == "冒烟树43"
              and node_lib.card_is_asset(r43_model_card)
              and bool(getattr(r43_model_card, "gk_is_asset", False)),
              f"card={getattr(r43_model_card, 'name', None)}")
        # 取消勾选 -> 不算资产(统计 / 导出都跳过)
        r43_model_card.gk_is_asset = False
        r43_after = node_lib.card_entries(r43_tree)
        check("资产:取消『标记为游戏资产』后不算资产",
              all(path != "冒烟树43"
                  for kind, _n, path, _c in r43_after),
              f"cards={[(k, p) for k, _n, p, _c in r43_after]} "
              f"counts={node_lib.card_counts(r43_tree)}")
        r43_model_card.gk_is_asset = True

        # 双向拦节点(poll 把关)
        r43_before = len(r43_tree.nodes)
        try:
            r43_tree.nodes.new("GKNodeMoveDirection")
            r43_move_blocked = False
        except RuntimeError:
            r43_move_blocked = True
        check("资产:玩法节点进不了资产库(Blender 侧把关)",
              r43_move_blocked and len(r43_tree.nodes) == r43_before)
        r43_logic = bpy.data.node_groups.new("资产玩法冒烟43", "GKGameLogicTree")
        try:
            r43_logic.nodes.new("GKNodeAssetCard")
            r43_card_blocked = False
        except RuntimeError:
            r43_card_blocked = True
        check("资产:资产卡进不了玩法图(不会污染代码生成)",
              r43_card_blocked and len(r43_logic.nodes) == 0)

        # ③ 收进『资产』集合(用户选的"在 Blender 里就隔开")
        r43_collection = r43_ui.asset_collection(r43_scene)
        r43_moved = r43_ui.move_into_asset_collection(r43_scene, [r43_obj])
        check("资产:收进『资产』集合(从原来的集合摘出来)",
              r43_moved == 1 and r43_collection is not None
              and r43_obj.name in r43_collection.objects
              and r43_obj.name not in r43_scene.collection.objects
              and r43_obj.name in r43_scene.objects,
              f"collections={[c.name for c in r43_obj.users_collection]}")

        # ④ 跨场景摆一份(本体在这一关,摆到新的一关)
        r43_scene2 = bpy.data.scenes.new("资产第二关43")
        r43_ok1, r43_msg1, r43_first = r43_ui.place_asset_into_scene(
            r43_scene2, "冒烟树43")
        r43_ok2, r43_msg2, r43_second = r43_ui.place_asset_into_scene(
            r43_scene2, "冒烟树43")
        check("资产:跨场景摆一份(第一份当本体、第二份当实例)",
              r43_ok1 and r43_ok2
              and r43_first is not None and r43_second is not None
              and not r43_first.gk_physics.asset_instance
              and r43_second.gk_physics.asset_instance
              and r43_first.name in r43_scene2.objects
              and r43_second.name in r43_scene2.objects,
              f"{r43_msg1} / {r43_msg2}")
        r43_entry = r43_export.collect_assets(r43_scene2).get("冒烟树43") or {}
        check("资产:第二关的导出账本上有本体(实例有模型可用)",
              r43_entry.get("source") is not None
              and len(r43_entry.get("members") or []) == 2,
              f"source={getattr(r43_entry.get('source'), 'name', None)} "
              f"members={len(r43_entry.get('members') or [])}")

        # ⑤ 音频:统一『资产卡』(类型=音频)-> 导出清单(名字 / 文件 / res://)
        r43_wav = os.path.join(tempfile.gettempdir(), "gk_smoke43_跳跃.wav")
        r43_sound_ok = True
        try:
            import wave
            with wave.open(r43_wav, "wb") as r43_wav_handle:
                r43_wav_handle.setnchannels(1)
                r43_wav_handle.setsampwidth(2)
                r43_wav_handle.setframerate(8000)
                r43_wav_handle.writeframes(bytes(2000))
        except Exception:                                 # noqa: BLE001
            r43_sound_ok = False
        r43_sound_card = r43_tree.nodes.new("GKNodeAssetCard")
        r43_sound_card.gk_kind = "sound"
        r43_sound_card.gk_name = "冒烟音效43"
        r43_sound_card.gk_path = r43_wav
        r43_sound_card.gk_is_asset = True
        r43_entries = node_lib.sound_card_entries(r43_tree)
        check("资产:统一卡(音频)进了导出清单(名字 / 文件 / res:// 路径)",
              ("冒烟音效43", r43_wav, r43_sound.sound_res_path(r43_wav))
              in r43_entries, str(r43_entries))
        r43_planned = r43_sound.sound_files(
            "X:/proj", [(name, path) for name, path, _res in r43_entries])
        check("资产:声音卡的目标文件落在 audio/ 下",
              any(item[3].startswith("res://audio/") for item in r43_planned))
        # 波形缩略图:真读一个 wav(离线那条单测也是这么造的)
        r43_thumbs = import_module(f"{module_name}.asset_thumbs")
        r43_peaks = r43_thumbs.wave_preview(r43_wav, samples=8) \
            if r43_sound_ok else []
        check("资产:音频缩略图能从 wav 读出波形",
              bool(r43_peaks) and all(0.0 <= value <= 1.0
                                      for value in r43_peaks),
              f"peaks={r43_peaks[:4]}")
        # 旧『资产_声音卡』仍然认(老 .blend 不坏)
        r43_legacy_sound = r43_tree.nodes.new("GKNodeAssetSound")
        r43_legacy_sound.gk_name = "旧声音43"
        r43_legacy_sound.gk_file = r43_wav
        r43_legacy_sound.gk_res = r43_sound.sound_res_path(r43_wav)
        check("资产:旧『资产_声音卡』仍然读得出来(兼容老工程)",
              ("旧声音43", r43_wav, r43_sound.sound_res_path(r43_wav))
              in node_lib.sound_card_entries(r43_tree))

        # ⑥ 卡片 draw + 缩略图叠加 + 面板注册
        # 缩略图绘制回调:没有节点编辑器上下文时应**静默返回**(不是抛异常)
        r43_thumb_error = ""
        try:
            r43_ui._asset_thumb_draw()
        except Exception as exc:                          # noqa: BLE001
            r43_thumb_error = f"{type(exc).__name__}: {exc}"
        check("资产:缩略图绘制回调在没上下文时不抛异常", not r43_thumb_error,
              r43_thumb_error)
        check("资产:缩略图绘制回调挂上了节点编辑器",
              bool(getattr(r43_ui, "_ASSET_THUMB_HANDLE", [])),
              str(getattr(r43_ui, "_ASSET_THUMB_HANDLE", None)))

        class _R43Layout:      # 最小替身:随便怎么调都返回自己
            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AttributeError(name)

                def recorder(*_args, **_kwargs):
                    return self
                return recorder

        r43_draw_ok = True
        r43_draw_error = ""
        for r43_card in r43_tree.nodes:
            try:
                r43_card.draw_buttons(bpy.context, _R43Layout())
            except Exception as exc:                      # noqa: BLE001
                r43_draw_ok = False
                r43_draw_error = (f"{getattr(r43_card, 'bl_label', '?')}: "
                                  f"{type(exc).__name__}: {exc}")
                break
        check("资产:资产卡的 draw_buttons 都能画完", r43_draw_ok,
              r43_draw_error)
        r43_panels = [cls.__name__ for cls in bpy.types.Panel.__subclasses__()]
        check("资产:侧栏面板已注册", "GK_PT_AssetTree" in r43_panels,
              str(r43_panels)[:200])

        # 收尾(第二关的对象 / 集合 / 场景都要清干净)
        try:
            os.remove(r43_wav)
        except OSError:
            pass
        for r43_temp in list(r43_scene2.objects):
            bpy.data.objects.remove(r43_temp, do_unlink=True)
        r43_col2 = r43_ui.asset_collection(r43_scene2, create=False)
        bpy.data.scenes.remove(r43_scene2)
        if r43_col2 is not None:
            bpy.data.collections.remove(r43_col2)
        bpy.data.node_groups.remove(r43_logic)
        bpy.data.node_groups.remove(r43_tree)
        bpy.data.objects.remove(r43_obj, do_unlink=True)
        bpy.data.meshes.remove(r43_mesh)
    except Exception as exc:
        check("第四十三轮", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 上台阶(第二十九轮):『移动_按方向』的『上台阶高度』----
    # Godot 的运动学角色**不会**自动上台阶(0.35 米在它眼里就是堵墙)。
    # 填了高度才生成"三关探测"(被竖面挡住 / 抬起来的路通 / 落点有地),
    # 默认 0 必须**一个字都不生成** —— 老图产物不变。
    try:
        r29_tree = bpy.data.node_groups.new("上台阶冒烟", "GKGameLogicTree")
        r29_physics = r29_tree.nodes.new("GKNodeOnPhysicsProcess")
        r29_move = r29_tree.nodes.new("GKNodeMoveDirection")
        r29_move.name = "移动"
        r29_move.gk_speed = 6.0
        r29_tree.links.new(r29_physics.outputs["执行"], r29_move.inputs["执行"])
        r29_off, _r29_w0 = codegen.generate(r29_tree, {
            "dimension": "3", "base_class": "CharacterBody3D",
        })
        r29_move.gk_step_height = 0.5
        r29_code, r29_warnings = codegen.generate(r29_tree, {
            "dimension": "3", "base_class": "CharacterBody3D",
        })
        check("上台阶:默认 0 -> 不生成探测(老图一字不变)",
              "test_move" not in r29_off)
        check("上台阶:填 0.5 -> 生成三关探测(被挡/路通/有地)",
              "test_move" in r29_code
              and "KinematicCollision3D.new()" in r29_code
              and "absf(" in r29_code
              and "Vector3.UP * 0.5" in r29_code
              and "Vector3.DOWN * 0.45" in r29_code
              and "_step_ahead" in r29_code
              and "get_normal().y >= 0.5" in r29_code
              and "global_position += _step_lift" in r29_code)
        check("上台阶:无告警", not r29_warnings, str(r29_warnings))
        bpy.data.node_groups.remove(r29_tree)
    except Exception as exc:
        check("上台阶", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 移动平台 + 可驾驶载具(第三十轮)----
    # ① **站上去跟着走**:平台必须导出成 AnimatableBody —— 被脚本/时间轴
    #    每帧挪位置时它的**速度**会传给站在上面的角色;StaticBody 是
    #    "瞬移",角色只会原地留在那儿(平台从脚下溜走);
    # ② **可驾驶**:上车/驾驶节点生成的状态写在载具自己身上
    #    (set_meta),驾驶 = 车头方向前进 / A-D 转车头 / 每帧钉座位。
    try:
        import shutil

        asset_export = import_module(f"{module_name}.asset_export")

        work = os.path.join(tempfile.gettempdir(), "gk_platform_vehicle")
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work, exist_ok=True)
        for leftover in list(bpy.context.scene.objects):
            bpy.data.objects.remove(leftover, do_unlink=True)

        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.0))
        platform = bpy.context.active_object
        platform.name = "移动平台"
        platform.scale = (3.0, 0.4, 3.0)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        platform.gk_physics.body_type = "AnimatableBody3D"
        bpy.context.view_layer.update()

        # 世界 glb 要先出(write_world_scene 按 glb 里的网格名过滤)
        asset_export.export_scene_glb(work, "World")
        path, _count = asset_export.write_world_scene(
            work, "World", scene=bpy.context.scene, dimension="3")
        with open(path, encoding="utf-8") as handle:
            world_text = handle.read()
        check("移动平台:导出成 AnimatableBody3D(角色才会被带着走)",
              '[node name="移动平台" type="AnimatableBody3D" '
              'parent="Colliders"]' in world_text,
              world_text[-600:])

        r30_tree = bpy.data.node_groups.new("载具冒烟", "GKGameLogicTree")
        r30_physics = r30_tree.nodes.new("GKNodeOnPhysicsProcess")
        r30_move = r30_tree.nodes.new("GKNodeMoveDirection")
        r30_move.name = "移动"
        r30_move.gk_speed = 6.0
        r30_drive = r30_tree.nodes.new("GKNodeDriveVehicle")
        r30_drive.name = "驾驶"
        r30_drive.gk_vehicle = "移动平台"
        r30_drive.gk_speed = 10.0
        r30_enter = r30_tree.nodes.new("GKNodeEnterVehicle")
        r30_enter.name = "上车"
        r30_enter.gk_vehicle = "移动平台"
        for link_pair in ((r30_physics, "执行", r30_move, "执行"),
                          (r30_move, "完成", r30_drive, "执行"),
                          (r30_drive, "完成", r30_enter, "执行")):
            r30_tree.links.new(link_pair[0].outputs[link_pair[1]],
                               link_pair[2].inputs[link_pair[3]])
        r30_code, r30_warnings = codegen.generate(r30_tree, {
            "dimension": "3", "base_class": "CharacterBody3D",
        })
        check("可驾驶载具:状态互通 + 车头方向 + 转车头 + 钉座位",
              'set_meta("gk_driving", true)' in r30_code
              and 'get_meta("gk_driving", false)' in r30_code
              and 'set_meta("gk_seat"' in r30_code
              and ".rotation.y -= deg_to_rad(" in r30_code
              and "global_transform * (载具_移动平台.get_meta" in r30_code,
              r30_code[-400:])
        check("可驾驶载具:无告警", not r30_warnings, str(r30_warnings))
        bpy.data.node_groups.remove(r30_tree)
    except Exception as exc:
        check("移动平台 / 载具", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 多场景导出的输入映射(第三十一轮)----
    # 两个 Blender 场景各带一个按键动作时,project.godot 的 [input] 里
    # **两个动作都要在** —— 每个场景的脚本导出各重写一次标记块,后写的
    # 会把先写的覆盖掉。真实观测(第三十一轮):关卡1 新加的 `E 上车`
    # 没了(只剩关卡2 的动作),第一关怎么按都上不了车。
    try:
        import shutil

        asset_export = import_module(f"{module_name}.asset_export")
        project_mod = import_module(f"{module_name}.project")

        work = os.path.join(tempfile.gettempdir(), "gk_multi_input")
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work, exist_ok=True)

        original_scene = bpy.context.window.scene
        made_scenes = []
        # ⚠ 变量名一律带 r31_ 前缀:这段和后面几段共用 main() 的局部变量,
        # 起名 `tree` 会把上面那棵"冒烟测试"覆盖掉(实测:后面所有章节
        # 全报 StructRNA ... has been removed)。
        for r31_label, r31_action in (("冒烟关卡甲", "smoke_key_a"),
                                      ("冒烟关卡乙", "smoke_key_b")):
            r31_scene = bpy.data.scenes.new(r31_label)
            made_scenes.append(r31_scene)
            r31_scene.gk_settings.logic_tree = f"{r31_label}逻辑"
            r31_tree = bpy.data.node_groups.new(f"{r31_label}逻辑",
                                                "GKGameLogicTree")
            r31_tree.use_fake_user = True
            r31_event = r31_tree.nodes.new("GKNodeOnPhysicsProcess")
            r31_key = r31_tree.nodes.new("GKNodeKey")
            r31_key.name = f"{r31_label}按键"
            r31_key.gk_key = "K"
            r31_key.gk_action = r31_action
            r31_key.gk_trigger = "JUST"
            r31_branch = r31_tree.nodes.new("GKNodeBranch")
            r31_tree.links.new(r31_key.outputs["结果"], r31_branch.inputs["条件"])
            r31_tree.links.new(r31_event.outputs["执行"],
                               r31_branch.inputs["执行"])
        project_mod.create_project(work, title="输入合并",
                                   blend_file_name="冒烟.blend")
        asset_export.export_all_scenes(work, dimension="3")
        with open(os.path.join(work, "project.godot"),
                  encoding="utf-8") as handle:
            text = handle.read()
        check("多场景导出:两个场景的输入动作都在 [input] 里(不被覆盖)",
              "smoke_key_a=" in text and "smoke_key_b=" in text,
              [line for line in text.splitlines()
               if line.startswith("smoke_key")])

        bpy.context.window.scene = original_scene
        for r31_scene in made_scenes:
            bpy.data.scenes.remove(r31_scene)
        for r31_name in ("冒烟关卡甲逻辑", "冒烟关卡乙逻辑"):
            r31_group = bpy.data.node_groups.get(r31_name)
            if r31_group is not None:
                bpy.data.node_groups.remove(r31_group)
    except Exception as exc:
        check("多场景导出:输入映射合并", False,
              f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 变量分组 / 文件夹(第三十二轮)----
    # 真 Blender 里:给几个变量填『分组』 -> 排布按"第一次出现的顺序"、
    # 未分组排在最后;而且分组**不进生成代码**(同一张图换个组,产物逐字一样)。
    try:
        props_mod = import_module(f"{module_name}.properties")
        r32_tree = bpy.data.node_groups.new("变量分组冒烟", "GKGameLogicTree")
        r32_tree.use_fake_user = True
        for r32_name, r32_group in (("血量", "战斗"), ("金币", "进度"),
                                    ("攻击", "战斗"), ("临时", "")):
            r32_var = r32_tree.gk_vars.add()
            r32_var.var_name = r32_name
            r32_var.group = r32_group
        r32_groups = props_mod.logic_var_groups(r32_tree)
        check("变量分组:第一次出现的顺序 + 未分组排最后",
              [name for name, _ in r32_groups] == ["战斗", "进度", ""]
              and [v.var_name for v in r32_groups[0][1]] == ["血量", "攻击"],
              str([name for name, _ in r32_groups]))
        # 生成物不受分组影响
        r32_event = r32_tree.nodes.new("GKNodeOnPhysicsProcess")
        r32_set = r32_tree.nodes.new("GKNodeSetVar")
        r32_set.name = "写血量"
        r32_set.gk_var_name = "血量"
        r32_tree.links.new(r32_event.outputs["执行"], r32_set.inputs["执行"])
        r32_before, _r32_w1 = codegen.generate(r32_tree, {
            "dimension": "3", "base_class": "CharacterBody3D",
        })
        r32_tree.gk_vars[0].group = "换个组"
        r32_after, _r32_w2 = codegen.generate(r32_tree, {
            "dimension": "3", "base_class": "CharacterBody3D",
        })
        check("变量分组:只影响显示(生成物逐字不变)",
              r32_before == r32_after,
              f"{len(r32_before)} vs {len(r32_after)} 字符")
        bpy.data.node_groups.remove(r32_tree)
    except Exception as exc:
        check("变量分组 / 文件夹", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 导出报告(用户 2026-10-05 第二十三轮:"导出报告可视化")----
    # 真 Blender 里验四件事:
    #   ① remember_export 顺手把**结构化报告**记进 .blend(清掉内存还能读回);
    #   ② 『复制导出报告』能跑:剪贴板 + 文本编辑器 + 控制台;
    #   ③ 『定位到出问题的节点』真的能选中那个节点(报告里带 node_name);
    #   ④ 报告文本里有"怎么修"和节点名(用户要发出来的就是这份)。
    try:
        # ⚠ 在 Blender 里插件是 bl_ext.<仓库>.<包名>,不是 `BlenderGameKit`
        # (直接 import 会 ModuleNotFoundError —— 别的块都用 import_module)
        export_report = import_module(f"{module_name}.export_report")
        models = import_module(f"{module_name}.models")
        ops = import_module(f"{module_name}.operator_io")

        scene = bpy.context.scene
        report_node = new_node("GKNodePrint", "报警节点", gk_text="报告用")
        diagnostic = models.Diagnostic(
            level="warning", code="ANIM_TARGET_MISMATCH",
            message="动画『走路』不在『身体』上",
            hint="检查节点上的『动画播放器』",
            node_name="报警节点")
        export_report.clear()
        ops.remember_export(scene, {
            "ok": True, "script_name": "smoke_report.gd", "directory": "",
            "lines": 12, "elapsed_ms": 4.0, "written": ["a"],
            "diagnostics": [diagnostic],
            "warnings": [diagnostic.render(), "这些网格没有材质: 地面"],
            "notes": ["参考: 无"],
        })
        export_report.clear()                      # 只留 .blend 里那份
        loaded = export_report.load(scene)
        check("导出报告:记进 .blend,清掉内存还能读回",
              loaded.get("script_name") == "smoke_report.gd"
              and len(loaded.get("items") or []) == 3,
              str(loaded)[:200])
        text = export_report.report_text(loaded)
        check("导出报告:文本里有『怎么修』和节点名",
              "怎么修:" in text and "相关节点: 报警节点" in text)

        result = bpy.ops.gk.export_report_copy()
        copied_text = bpy.data.texts.get("导出报告")
        check("导出报告:『复制报告』把文本放进文本编辑器",
              result == {"FINISHED"} and copied_text is not None
              and "导出报告" in copied_text.as_string(),
              f"{result}")

        bpy.ops.gk.export_report_focus(node_name="报警节点")
        check("导出报告:『定位』选中了那个节点", bool(report_node.select),
              f"select={report_node.select}")

        # ── 第五十五轮:整份报告也写进文本块(侧栏只留摘要 + 定位按钮)──
        # 用户:"显示的信息太多了"。正文搬进文本块,用文本编辑器读。
        block = bpy.data.texts.get(export_report.EXPORT_REPORT_TEXT_NAME)
        check("导出报告:整份写进了文本块(可在文本编辑器里滚动着看)",
              block is not None and "怎么修:" in block.as_string(),
              str([t.name for t in bpy.data.texts])[:120])
        if block is not None:
            bpy.data.texts.remove(block)
        export_report.clear()
    except Exception as exc:
        check("导出报告", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 函数:折叠成函数(用户 2026-10-05 第十七轮)----
    # 选**一个节点**(printer —— 上游是别的执行流节点)试折叠,断言五件事:
    #   ① 侧栏多了一个函数声明;② 多了『函数_定义』和『函数_调用』两个节点;
    #   ③ 上游照旧接上(折叠前 printer 的上游 → 现在指向调用节点);
    #   ④ 折叠出来的函数能**真的编出 func**(不是只在图里好看);
    #   ⑤ 跨边界的数据线(外面的常量 → printer 的『内容』)自动变成**参数**。
    try:
        # 先造一根跨边界的数据线:常量 → printer.内容
        amount = new_node("GKNodeConstFloat", "金额", gk_value=7.0)
        link(amount, "值", printer, "内容")
        for node in tree.nodes:
            node.select = False
        printer.select = True
        # 折叠**前**记下 printer 的上游是谁 —— 折叠后调用节点的上游应该一样
        # ⚠ 不写死"跳跃"这个节点名:Blender 的 links.new 对"已经接了线的输入"
        # 是**替换**旧线(不是拒绝),所以 printer 的上游到底是哪个节点得实测。
        before_up = [(l.from_node.name, l.from_socket.name) for l in tree.links
                     if l.to_node == printer and l.to_socket.name == "执行"]
        funcs_before = len(tree.gk_funcs)
        nodes_before = len(tree.nodes)
        result = bpy.ops.gk.collapse_to_function(func_name="smoke_collapse")
        declared = [f for f in tree.gk_funcs if str(f.func_name) == "smoke_collapse"]
        # 新增节点 = 定义 + 调用 + 一个『变量_读取(参数名)』(跨边界数据线)
        check("折叠成函数:已声明 + 加了 3 个节点",
              result == {"FINISHED"} and len(tree.gk_funcs) == funcs_before + 1
              and len(tree.nodes) == nodes_before + 3 and bool(declared),
              f"{result} 声明={len(tree.gk_funcs)} "
              f"新增节点={len(tree.nodes) - nodes_before}")
        define = next((n for n in tree.nodes
                       if n.bl_idname == "GKNodeFunctionDefine"
                       and str(n.gk_func_name) == "smoke_collapse"), None)
        call = next((n for n in tree.nodes
                     if n.bl_idname == "GKNodeFunctionCall"
                     and str(n.gk_func_name) == "smoke_collapse"), None)
        # 上游:折叠前 printer 的**执行**上游 -> 现在指向调用节点
        # ⚠ 只比『执行』那根:调用节点上还多了参数框的线(那是数据的,不是执行流)
        into_call = [(l.from_node.name, l.from_socket.name) for l in tree.links
                     if l.to_node == call and l.to_socket.name == "执行"]
        upstream_ok = sorted(into_call) == sorted(before_up)
        # 定义 -> printer 的『执行』(函数体从这里开始)
        into_entry = any(l.to_node == printer
                         and getattr(l.from_node, "bl_idname", "") == "GKNodeFunctionDefine"
                         for l in tree.links)
        check("折叠成函数:上游接上、函数体接上",
              define is not None and call is not None and upstream_ok and into_entry,
              f"折叠前上游={before_up} 调用节点的执行上游={into_call} "
              f"定义->入口={into_entry}")
        # 跨边界的数据线 -> 参数:声明里有参数、调用点接了外面那个常量、
        # 函数体里插了一个『变量_读取(参数名)』
        params_text = str(declared[0].params) if declared else ""
        param_names = [part.split(":")[0].strip() for part in params_text.split(",")
                       if part.strip()]
        readers = [n for n in tree.nodes
                   if n.bl_idname == "GKNodeGetVar"
                   and str(n.gk_var_name) in param_names]
        call_param_links = [l for l in tree.links
                            if l.to_node == call
                            and l.to_socket.name in param_names]
        check("折叠成函数:跨边界数据线变成了参数",
              bool(params_text) and bool(readers)
              and any(getattr(l.from_node, "bl_idname", "") == "GKNodeConstFloat"
                      for l in call_param_links),
              f"参数=『{params_text}』读取节点={len(readers)} "
              f"调用点参数线={len(call_param_links)}")
        # 折叠出来的函数要能**真的编出代码**(不是只在图里好看)
        # ⚠ 现在它带了参数 -> 签名是 `func smoke_collapse(金额: float) -> void:`
        code_text, _gen_warnings, _name, _actions, _diag = \
            exporter.generate_code_result(tree, exporter.default_settings())
        check("折叠成函数:能编出 func",
              "func smoke_collapse(" in code_text
              and "smoke_collapse(" in code_text
              and "金额: float" in code_text,
              code_text[-400:])
    except Exception as exc:
        check("折叠成函数", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 变量集中面板:查找引用 + 拉取/设节点(用户 2026-10-04)----
    try:
        var_set = new_node("GKNodeSetVar", "写生命", gk_var_name="hp")
        var_get = new_node("GKNodeGetVar", "读生命", gk_var_name="hp")
        result = bpy.ops.gk.var_find_refs(var_name="hp")
        check("查找引用返回完成", result == {"FINISHED"}, str(result))
        check("读/写两个节点都被选中",
              bool(var_set.select) and bool(var_get.select))
        before = len(tree.nodes)
        made = bpy.ops.gk.var_make_node(var_name="hp", make_set=False)
        selected = [n for n in tree.nodes if getattr(n, "select", False)]
        new_obj = selected[0] if selected else None
        check("拉取变量节点已创建",
              made == {"FINISHED"} and len(tree.nodes) == before + 1
              and new_obj is not None and new_obj.bl_idname == "GKNodeGetVar"
              and str(new_obj.gk_var_name) == "hp",
              f"{made} 新增={len(tree.nodes) - before} 选中={new_obj}")
    except Exception as exc:
        check("变量面板", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 通配引脚自动类型提示(真实引脚/连线路径)----
    try:
        gk_sockets = import_module(f"{module_name}.gk_sockets")
        const = new_node("GKNodeConstFloat", "常量")
        setter = new_node("GKNodeSetVar", "设置", gk_var_name="mp")
        source = const.outputs.get("值")
        target = setter.inputs.get("值")
        tree.links.new(source, target)
        label = gk_sockets.resolved_type_label(target)
        check("通配引脚解析出上游类型", label == "数值", label)
        check("通配引脚标签补了类型",
              gk_sockets.socket_caption(target, "值") == "值 (数值)",
              gk_sockets.socket_caption(target, "值"))
    except Exception as exc:
        check("通配引脚类型提示", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 侧栏折叠 + 右键菜单子菜单(用户 2026-10-04)----
    try:
        ui_mod = import_module(f"{module_name}.ui")
        before = ui_mod._OPEN_SECTIONS.get("smoke_key", True)
        result = bpy.ops.gk.toggle_section(key="smoke_key", default_open=True)
        after = ui_mod._OPEN_SECTIONS.get("smoke_key", True)
        check("侧栏折叠开关能翻转",
              result == {"FINISHED"} and before is True and after is False,
              f"{result} {before}->{after}")
        check("右键菜单的两个子菜单已注册",
              hasattr(ui_mod.TOP_MENU, "bl_rna")
              and hasattr(ui_mod.TOOLS_MENU, "bl_rna"))
    except Exception as exc:
        check("侧栏折叠", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 生成代码 ----
    try:
        code, warnings = codegen.generate(tree, {
            "dimension": "2", "base_class": "CharacterBody2D", "export_tunables": True,
        })
        check("生成代码", bool(code))
        check("生成代码含 _physics_process", "func _physics_process(delta: float) -> void:" in code)
        check("生成代码含 move_and_slide", "move_and_slide()" in code)
        check("生成代码含 get_vector", "Input.get_vector(ACTION_" in code)
        # 默认动作名是 move_*(WASD 开箱即用),不再是 Godot 内置的 ui_*(方向键)
        check("输入动作已抽成常量", 'const ACTION_MOVE_LEFT := "move_left"' in code
              and 'const ACTION_UI_LEFT := "ui_left"' not in code)
        check("跳跃力度暴露为 @export", "@export var jump_force: float = 520.0" in code)
        check("无告警", not warnings, str(warnings))
        print("-" * 70)
        print(code)
        print("-" * 70)
    except Exception as exc:
        check("生成代码", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    # ---- 真的写到磁盘 ----
    out_dir = os.path.join(tempfile.gettempdir(), "BlenderGameKit_smoke")
    try:
        settings = exporter.build_settings(bpy.context.scene)
        settings["output_dir"] = out_dir
        settings["dimension"] = "2"
        settings["base_class"] = "CharacterBody2D"
        settings["write_scene"] = True
        result = exporter.export_tree(tree, settings, write_scene=True)
        # 脚本落在 scripts/ 子目录里(项目结构:scripts/ scenes/ art/ …),
        # 用 result["written"] 里记的真实路径,别自己拼 ——
        # 以前这里拼的是 out_dir/<名字>,那个文件根本不存在,
        # 读到的空内容自然和生成的代码不相等(误报)。
        script_path = next((p for p in result["written"] if p.endswith(".gd")),
                           os.path.join(out_dir, "scripts", result["script_name"]))
        check("导出脚本文件", os.path.isfile(script_path), script_path)
        # 冒烟场景里**没有相机** -> 导出会提示"运行时会自动造一台相机"
        # (2D 造 Camera2D 框住全部物体 / 3D 造自由飞行观察机,第四十六轮分流)。
        # 那是**好消息**(告诉你没相机时也能看见画面),不是错误;
        # 和"基类自愈"一样属于信息类提示,不该让冒烟失败 —— 按**共同前缀**
        # 豁免,两种维度的文案都不会顶掉这条断言。
        real_warnings = [w for w in result["warnings"]
                         if "场景里一台相机都没有" not in str(w)]
        check("导出时无告警", not real_warnings, str(real_warnings))

        # 读回来比对，确认磁盘内容与生成内容一致
        with open(script_path, encoding="utf-8") as handle:
            written = handle.read()
        check("文件内容与生成内容一致", written == code)
        check("文件是 UTF-8 且含中文注释", "由 BlenderGameKit 自动生成" in written)
        print(f"    导出目录: {out_dir}")
        print(f"    写入文件: {[os.path.basename(p) for p in result['written']]}")
    except Exception as exc:
        check("导出文件", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    # ---- 动作分类:打标签 -> 导出成常量表 ----
    # 覆盖 ui/operator 那条链路:`gk.tag_anim_category` 真的能写进 Action 的
    # 自定义属性,并且导出时变成 GK_ANIM_BY_CATEGORY 常量表。
    try:
        tagged = bpy.data.actions.new("冒烟动作")
        bpy.ops.gk.tag_anim_category(action_name=tagged.name, category="攻击")
        check("动作分类写进 Action",
              tagged.get("gk_anim_category") == "攻击",
              str(tagged.get("gk_anim_category")))
        code2, _w2 = codegen.generate(tree, {
            "dimension": "2", "base_class": "CharacterBody2D",
            "export_tunables": True,
        })
        check("导出含分类常量表",
              'const GK_ANIM_BY_CATEGORY := {' in code2
              and '"攻击": ["冒烟动作"]' in code2)
        bpy.data.actions.remove(tagged)
    except Exception as exc:
        check("动作分类链路", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 新建动画树不报错 ----
    # 踩过的坑:创建起始状态时给节点赋值 `gk_is_default`(该属性**从未定义**),
    # Blender 直接 AttributeError ——「打开/新建动画树」这条路一走到新建分支就崩。
    try:
        bpy.ops.gk.open_anim_tree()
        trees = [g for g in bpy.data.node_groups
                 if getattr(g, "bl_idname", "") == "GKAnimationTree"]
        check("新建动画树不报错", bool(trees), str(len(trees)))
        # 起始状态由**连线拓扑**决定(codegen._anim_tree_entry),不是勾选框
        entry, entries = codegen._anim_tree_entry(trees[0] if trees else None)
        check("动画树有起始状态", entry is not None and len(entries) >= 1)
    except Exception as exc:
        check("新建动画树不报错", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 动画树自定义变量:数据模型 -> 取/设节点 -> 导出带初值的成员变量 ----
    # 仿虚幻蓝图:变量属于这一"个"动画蓝图(这张树),不是"被谁用到才存在"。
    # 这条路一次覆盖三处:GKAnimVariable 集合、GKNodeAnimGetVar/SetVar 节点、
    # codegen 按类型 + 初值生成成员变量。
    try:
        anim_tree = next((g for g in bpy.data.node_groups
                          if getattr(g, "bl_idname", "") == "GKAnimationTree"), None)
        check("找到动画树", anim_tree is not None)

        # 1) 数据模型:加一个布尔变量并写初值(和侧栏『添加变量』算子同一条路)
        # 名字用英文:GDScript 的标识符只认 ASCII,中文名会被压成 `v`
        # (codegen 会就此告警,见 test_codegen.TestAnimTreeVariables)
        var = anim_tree.gk_variables.add()
        var.var_name = "invincible"
        var.var_type = "bool"
        var.default_value = "真"          # 中文初值也要能解析成 true
        check("动画树变量可增删", len(anim_tree.gk_variables) == 1
              and str(anim_tree.gk_variables[0].var_name) == "invincible")

        # 2) 节点:取变量 / 设变量 都能在这张动画树上建出来
        get_node = anim_tree.nodes.new("GKNodeAnimGetVar")
        get_node.gk_var_name = "invincible"
        set_node = anim_tree.nodes.new("GKNodeAnimSetVar")
        set_node.gk_var_name = "invincible"
        check("取/设变量节点可用",
              get_node.outputs.get("值") is not None
              and set_node.inputs.get("执行") is not None
              and set_node.inputs.get("值") is not None)

        # 2b) 逻辑节点(与/或/非)也要能在动画树里拉出来当转场条件
        check("逻辑节点可用在动画树",
              node_lib.GKNodeLogic.poll(anim_tree)
              and node_lib.GKNodeCompare.poll(anim_tree))

        # 3) codegen:玩法图里挂一个『动画_状态机』驱动这张树,
        #    变量必须落地成 var 无敌: bool = true
        #    (『开始』的执行输出还没接线,正好拿来启动它)
        sm_node = tree.nodes.new("GKNodeAnimStateMachineRef")
        sm_node.name = "状态机"
        sm_node.gk_tree_name = anim_tree.name
        tree.links.new(start.outputs["执行"], sm_node.inputs["执行"])
        code3, _w3 = codegen.generate(tree, {
            "dimension": "2", "base_class": "CharacterBody2D",
            "export_tunables": True,
        })
        check("动画变量导出成带初值的成员变量",
              "var invincible: bool = true" in code3,
              [line for line in code3.splitlines() if "invincible" in line][:3])
    except Exception as exc:
        check("动画树变量链路", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 音频与叙事(第六十四轮):字幕 / 打字机音效 / 对话历史 ----
    # 这一批是界面 + 音频的东西,冒烟查"接线对不对"(节点注册 / 生成物里
    # 那几条关键链在不在 / 卡片画不画得完);**行为**由界面示例的探针在真
    # Godot 里查(字幕不阻塞、历史面板能开、打字音播放器真的被建出来)。
    try:
        r64_tree = bpy.data.node_groups.new("叙事冒烟64", "GKGameLogicTree")
        r64_ready = r64_tree.nodes.new("GKNodeOnReady")
        r64_dialog = r64_tree.nodes.new("GKNodeUiDialog")
        r64_dialog.gk_speaker = "向导"
        r64_dialog.gk_text = "你好"
        r64_dialog.gk_type_sound = "res://audio/type.wav"
        r64_dialog.gk_type_every = 2
        r64_sub = r64_tree.nodes.new("GKNodeUiSubtitle")
        r64_sub.gk_text = "脚步声……"
        r64_sub.gk_type_sound = "res://audio/type.wav"
        r64_hist = r64_tree.nodes.new("GKNodeDialogHistory")
        r64_tree.links.new(r64_ready.outputs["执行"], r64_dialog.inputs["执行"])
        r64_tree.links.new(r64_dialog.outputs["完成"], r64_sub.inputs["执行"])
        r64_tree.links.new(r64_sub.outputs["完成"], r64_hist.inputs["执行"])
        r64_code, r64_warn = codegen.generate(
            r64_tree, {"dimension": "3", "base_class": "CharacterBody3D"})
        check("叙事:字幕 / 历史节点都建出来了(已注册)",
              r64_sub.bl_idname == "GKNodeUiSubtitle"
              and r64_hist.bl_idname == "GKNodeDialogHistory")
        check("叙事:字幕 / 打字音 / 历史三条链都出了码",
              "_gk_subtitle_show(self, " in r64_code
              and '_gk_dialog_blip = "res://audio/type.wav"' in r64_code
              and "func _gk_play_blip(" in r64_code
              and "func _gk_history_show(" in r64_code
              and "_gk_dialog_history.append(" in r64_code,
              r64_code[-400:])
        check("叙事:UI 根容器变量声明了(只用到对话框的图也能编译)",
              "var _gk_ui_layer: CanvasLayer" in r64_code
              and "var _gk_ui_root: Control" in r64_code)
        check("叙事:生成无告警", not r64_warn, str(r64_warn)[:300])

        class _R64Layout:
            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AttributeError(name)

                def recorder(*_args, **_kwargs):
                    return self
                return recorder

        r64_draw_ok = True
        r64_draw_error = ""
        for card in (r64_dialog, r64_sub, r64_hist):
            try:
                card.draw_buttons(bpy.context, _R64Layout())
            except Exception as exc:                      # noqa: BLE001
                r64_draw_ok = False
                r64_draw_error = (f"{card.bl_label}: "
                                  f"{type(exc).__name__}: {exc}")
                break
        check("叙事:三张节点卡片能画完", r64_draw_ok, r64_draw_error)

        bpy.data.node_groups.remove(r64_tree)

        # ---- 音频:BGM 循环 / 总线(第六十四轮)----
        r64b_tree = bpy.data.node_groups.new("音频冒烟64", "GKGameLogicTree")
        r64b_ready = r64b_tree.nodes.new("GKNodeOnReady")
        r64b_music = r64b_tree.nodes.new("GKNodePlayMusic")
        r64b_music.gk_path = "res://audio/bgm.ogg"
        r64b_music.gk_loop = True
        r64b_music.gk_bus = "Music"
        r64b_tree.links.new(r64b_ready.outputs["执行"], r64b_music.inputs["执行"])
        r64b_code, r64b_warn = codegen.generate(
            r64b_tree, {"dimension": "3", "base_class": "CharacterBody3D"})
        # 循环要按流类型分别设(Ogg/MP3 是 .loop,wav 是 loop_mode)——
        # 只设一种的话换个格式就"播一遍就没了",而且不报错
        check("音频:BGM 按流类型设循环 + 总线可配",
              "AudioStreamWAV.LOOP_FORWARD" in r64b_code
              and "AudioStreamOggVorbis" in r64b_code
              and '.bus = "Music"' in r64b_code,
              r64b_code[-300:])
        check("音频:BGM 生成无告警", not r64b_warn, str(r64b_warn)[:200])
        r64b_draw_ok = True
        r64b_draw_error = ""
        try:
            r64b_music.draw_buttons(bpy.context, _R64Layout())
        except Exception as exc:                          # noqa: BLE001
            r64b_draw_ok = False
            r64b_draw_error = f"{type(exc).__name__}: {exc}"
        check("音频:音乐卡片能画完(循环 / 总线两栏)", r64b_draw_ok,
              r64b_draw_error)
        bpy.data.node_groups.remove(r64b_tree)
    except Exception as exc:
        check("音频与叙事", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 算子注册 ----
    for op_name in ("gk.export_script", "gk.preview_script", "gk.add_node",
                    "gk.auto_link", "gk.pick_target", "gk.set_target",
                    "gk.tag_anim_category", "gk.pick_anim_name",
                    "gk.pick_anim_tree", "gk.anim_var_add", "gk.anim_var_remove",
                    "gk.anim_var_make_node", "gk.anim_var_pick",
                    # 打包发布(第五十二轮):一键出可执行文件 + 打开打包目录
                    "gk.export_release", "gk.open_release_folder",
                    # 项目顾问(第五十三轮):把项目诊断交给 AI
                    "gk.ask_advisor", "gk.open_text_block",
                    "gk.copy_advisor_context", "gk.clear_advisor",
                    # AI 亲自操作(第五十六轮):执行/忽略提议的动作
                    "gk.apply_advisor_action", "gk.dismiss_advisor_action",
                    "gk.run_advisor_action",
                    # 分组框折叠(第六十二轮)+ 一键预设角色(第六十三轮补登记)
                    "gk.toggle_group", "gk.character_preset"):
        check(f"算子已注册 {op_name}", hasattr(bpy.ops, op_name.split(".")[0])
              and op_name.split(".")[1] in dir(getattr(bpy.ops, op_name.split(".")[0])))

    # ---- 打包发布(第五十二轮):算子接线 + 绝不覆盖已有预设 ----
    # 这一层只验"接线"(用假 Godot / 假模板 / 假打包),真打包由门禁 5n
    # 那层用真 Godot 跑 —— 免得冒烟测试还要依赖本机装没装导出模板。
    try:
        packaging_mod = __import__(f"{module_name}.packaging",
                                   fromlist=["EXPORT_PRESETS_NAME"])
        ops_mod = __import__(f"{module_name}.operator_io",
                             fromlist=["LAST_RELEASE"])
        proj_mod = __import__(f"{module_name}.project",
                              fromlist=["find_godot_executable"])
        work = tempfile.mkdtemp(prefix="gk_smoke_package_")
        proj_mod.create_project(work, title="打包冒烟")

        saved = (proj_mod.find_godot_executable, proj_mod.godot_version,
                 packaging_mod.templates_status, packaging_mod.run_release)
        made = []

        def fake_run(exe, directory, preset, output, timeout=600):
            os.makedirs(os.path.dirname(output), exist_ok=True)
            with open(output, "wb") as handle:
                handle.write(b"MZ")
            made.append(preset)
            return True, ""

        settings = bpy.context.scene.gk_settings
        saved_dir = settings.project_dir
        try:
            proj_mod.find_godot_executable = lambda *a, **k: "godot-fake"
            proj_mod.godot_version = lambda *a, **k: "4.7.2"
            packaging_mod.templates_status = lambda v, p="windows": (True, work, "ok")
            packaging_mod.run_release = fake_run
            settings.project_dir = work

            # 先放一份"用户自己的"预设 -> 打包**绝不能**覆盖它
            preset_file = os.path.join(work,
                                       packaging_mod.EXPORT_PRESETS_NAME)
            with open(preset_file, "w", encoding="utf-8", newline="\n") as h:
                h.write('[preset.0]\nname="我的预设"\n'
                        'export_path="build/x.exe"\n')

            result = bpy.ops.gk.export_release(auto_export=False)
            check("打包算子跑通", result == {"FINISHED"}, str(result))
            with open(preset_file, "r", encoding="utf-8") as h:
                preset_text = h.read()
            check("打包不覆盖用户已有的预设", "我的预设" in preset_text,
                  preset_text[:80])
            check("打包沿用了用户的预设名", made == ["我的预设"], str(made))
            out = ops_mod.LAST_RELEASE.get("output", "")
            check("打包产物落在用户预设指定的位置",
                  out.replace("\\", "/").endswith("build/x.exe"), out)
        finally:
            (proj_mod.find_godot_executable, proj_mod.godot_version,
             packaging_mod.templates_status,
             packaging_mod.run_release) = saved
            settings.project_dir = saved_dir
    except Exception as exc:
        check("打包发布算子", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 打包设置:多平台(第六十八轮)----
    # 用户原话:"在侧边栏添加打包为 .exe,.apk 等的打包设置";动工前确认过
    # "全上 / 存场景 / 已有预设就追加"。这一层用**假 Godot + 假打包**验接线:
    #   * 场景设置上有 5 个平台开关(存场景,随 .blend 走);
    #   * 勾了 windows/linux/web/macos -> 能打的都打了,预设名各是各的;
    #   * 缺的平台预设被**追加**,老内容一个字不动,旁边留 .bak;
    #   * macOS 在这台主机上被跳过(不报错,只记一笔);
    #   * 某个平台失败**不影响**别的(linux 没模板 -> 另外两个照打)。
    try:
        packaging_mod = __import__(f"{module_name}.packaging",
                                   fromlist=["EXPORT_PRESETS_NAME"])
        ops_mod = __import__(f"{module_name}.operator_io",
                             fromlist=["LAST_RELEASE"])
        proj_mod = __import__(f"{module_name}.project",
                              fromlist=["find_godot_executable"])
        work = tempfile.mkdtemp(prefix="gk_smoke_pkg68_")
        proj_mod.create_project(work, title="多平台冒烟")

        settings = bpy.context.scene.gk_settings
        check("打包设置:场景设置上有 5 个平台开关",
              all(hasattr(settings, f"package_{key}")
                  for key in packaging_mod.PLATFORM_ORDER),
              str([key for key in packaging_mod.PLATFORM_ORDER
                   if not hasattr(settings, f"package_{key}")]))
        check("打包设置:应用名 / 版本 / 安卓包名都在",
              all(hasattr(settings, name) for name in (
                  "package_app_name", "package_version", "package_android_id")))
        check("打包设置:Windows 默认勾上(老工程点打包行为不变)",
              bool(settings.package_windows))

        saved = (proj_mod.find_godot_executable, proj_mod.godot_version,
                 packaging_mod.templates_status, packaging_mod.run_release)
        saved_dir = settings.project_dir
        saved_flags = {f"package_{key}":
                       bool(getattr(settings, f"package_{key}", False))
                       for key in packaging_mod.PLATFORM_ORDER}
        made = []

        def fake_run(exe, directory, preset, output, timeout=600):
            os.makedirs(os.path.dirname(output), exist_ok=True)
            with open(output, "wb") as handle:
                handle.write(b"MZ")
            made.append((preset, os.path.basename(output)))
            return True, ""

        def fake_templates(version, platform="windows"):
            # linux 那份**故意**说没装 -> 验"一个平台失败不影响别的"
            return (platform != "linux", work, f"{platform} 没装")

        try:
            proj_mod.find_godot_executable = lambda *a, **k: "godot-fake"
            proj_mod.godot_version = lambda *a, **k: "4.7.2"
            packaging_mod.templates_status = fake_templates
            packaging_mod.run_release = fake_run
            settings.project_dir = work
            for key in packaging_mod.PLATFORM_ORDER:
                setattr(settings, f"package_{key}",
                        key in ("windows", "linux", "web", "macos"))
            settings.package_app_name = "多平台游戏"
            settings.package_version = "2.0"

            # 先放一份"用户自己的"预设(带 platform= 行)-> 要沿用它、不覆盖它
            preset_file = os.path.join(work,
                                       packaging_mod.EXPORT_PRESETS_NAME)
            with open(preset_file, "w", encoding="utf-8", newline="\n") as h:
                h.write('[preset.0]\nname="我改过的名字"\n'
                        'platform="Windows Desktop"\n\n'
                        '[preset.0.options]\n'
                        'application/product_name="我自己改的"\n')

            result = bpy.ops.gk.export_release(auto_export=False)
            check("打包设置:算子跑通(有一个平台失败也跑到头)",
                  result == {"FINISHED"}, str(result))
            names = [preset for preset, _name in made]
            check("打包设置:能打的平台各打一次(linux 因没模板被跳过)",
                  names == ["我改过的名字", "Web"], str(made))
            check("打包设置:产物扩展名跟着平台走",
                  any(name.endswith(".exe") for _p, name in made)
                  and any(name.endswith(".zip") for _p, name in made),
                  str(made))
            check("打包设置:macOS 在这台主机上被跳过",
                  all(preset != "macOS" for preset, _name in made), str(made))

            with open(preset_file, encoding="utf-8") as h:
                preset_text = h.read()
            check("打包设置:用户那条预设一个字没动",
                  "我改过的名字" in preset_text
                  and "我自己改的" in preset_text
                  and preset_text.count('platform="Windows Desktop"') == 1,
                  preset_text[:200])
            check("打包设置:缺的平台预设被追加",
                  'platform="Web"' in preset_text
                  and 'platform="Linux"' in preset_text, preset_text[:200])
            check("打包设置:追加前留了备份",
                  os.path.isfile(preset_file + ".bak"))
        finally:
            (proj_mod.find_godot_executable, proj_mod.godot_version,
             packaging_mod.templates_status,
             packaging_mod.run_release) = saved
            settings.project_dir = saved_dir
            for name, value in saved_flags.items():
                setattr(settings, name, value)
    except Exception as exc:
        check("打包设置(第六十八轮)", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 项目顾问(AI)(第五十三轮):接线 + 不联网 + 上下文收集 ----
    # ⚠ 这一层**绝不真的联网**:把 advisor.call_chat 换成假的,再验
    # "没配地址就不发"、"没写问题就不发"。真调用由用户手动点。
    try:
        adv_mod = __import__(f"{module_name}.advisor", fromlist=["collect_context"])
        proj_mod2 = __import__(f"{module_name}.project", fromlist=["preferences"])
        settings = bpy.context.scene.gk_settings
        check("场景设置里有『项目顾问提问』字段",
              hasattr(settings, "advisor_question"))
        prefs2 = proj_mod2.preferences(bpy.context)
        check("偏好设置里有项目顾问的四项",
              prefs2 is not None
              and all(hasattr(prefs2, name) for name in (
                  "advisor_base_url", "advisor_model", "advisor_api_key",
                  "advisor_include_code")),
              str(prefs2))

        saved_q = settings.advisor_question
        saved_prefs = {name: getattr(prefs2, name, "") for name in (
            "advisor_base_url", "advisor_model", "advisor_api_key")}
        saved_call = adv_mod.call_chat_raw
        try:
            # 先把 Key 填上 —— 这样下面"快照里不能有明文 Key"才是真的在验
            # 脱敏,而不是因为压根没配
            prefs2.advisor_api_key = "sk-smoke-should-never-appear"
            # ⚠ 这里**不能**直接断言返回值:算子里 `report({"ERROR"})` 在
            # bpy.ops 这一层会变成 RuntimeError(实测踩到)。被挡下时既要
            # "没做事",也要"把原因说清楚" —— 所以抓异常、看消息。
            def blocked_reason(call):
                try:
                    outcome = call()
                except RuntimeError as exc:
                    return str(exc)
                return "" if outcome == {"CANCELLED"} else f"竟然返回了 {outcome}"

            # 0) 还没聊过时『查看对话』要明确拒绝(而不是静默什么都不发生)
            why0 = blocked_reason(lambda: bpy.ops.gk.open_text_block(
                text_name=adv_mod.DIALOG_TEXT_NAME))
            check("还没对话记录时『查看对话』被挡下并说明",
                  "还没有" in why0, why0)
            # 1) 没写问题 -> 明确拒绝
            settings.advisor_question = ""
            why1 = blocked_reason(bpy.ops.gk.ask_advisor)
            check("没写问题时『问 AI』被挡下并说明原因",
                  "写一句你卡在哪" in why1, why1)
            # 2) 写了问题但没配地址 -> 也拒绝(绝不偷偷联网)
            settings.advisor_question = "角色不动"
            prefs2.advisor_base_url = ""
            why2 = blocked_reason(bpy.ops.gk.ask_advisor)
            check("没配 AI 地址时不发请求,并指出去哪配",
                  "还没填 AI 地址" in why2, why2)
            # 3) 『复制诊断』不联网也能跑通,而且快照要真的含"节点图 + 代码"
            result = bpy.ops.gk.copy_advisor_context()
            check("『复制诊断』跑通(不联网)", result == {"FINISHED"}, str(result))
            snapshot = ops_mod.LAST_ADVISOR.get("context", "")
            check("诊断快照含节点图与生成的代码",
                  "节点图" in snapshot and "生成的 GDScript" in snapshot,
                  f"{len(snapshot)} 字")
            check("快照里没有明文 API Key(脱敏生效)",
                  "sk-smoke-should-never-appear" not in snapshot)
            # 4) 配好地址 + 假模型 -> 走完整条路(不联网)。
            #    ⚠ 现在是**异步**的:算子发出去就返回,结果由主线程定时器收。
            #    `-b` 无事件循环,定时器不会自己跑,所以这里手动催 `_poll_advisor()`。
            import time
            prefs2.advisor_base_url = "http://example.invalid"
            prefs2.advisor_model = "smoke-model"
            # ⚠ 第五十六轮起算子走的是 **Agent 循环**(带工具),所以假的是
            #    `call_chat_raw`(返回原始 JSON),不是上一版的 `call_chat`。
            #    这里返回一个"不带 tool_calls 的普通回答",等于模型直接作答。
            adv_mod.call_chat_raw = lambda *a, **k: (
                True, {"choices": [{"message": {
                    "content": "**先把『脚本挂载目标』勾上。**"}}]})
            settings.advisor_question = "角色不动"
            result = bpy.ops.gk.ask_advisor()
            check("『问 AI』发出就返回(异步,界面不卡)",
                  result == {"FINISHED"}, str(result))
            check("发出后处于等待状态", adv_mod.agent_active())
            deadline = time.time() + 10
            while adv_mod.agent_active() and time.time() < deadline:
                ops_mod._poll_advisor()
                time.sleep(0.05)
            check("回复到了会写进对话记录文本块",
                  adv_mod.DIALOG_TEXT_NAME in [t.name for t in bpy.data.texts],
                  str([t.name for t in bpy.data.texts])[:120])
            block = bpy.data.texts.get(adv_mod.DIALOG_TEXT_NAME)
            dialog = block.as_string() if block else ""
            check("对话记录里有我的问题",
                  "角色不动" in dialog, dialog[:60])
            check("对话记录里有 AI 的回答(且已抹掉 Markdown 星号)",
                  "脚本挂载目标" in dialog and "**" not in dialog,
                  dialog[:120])
            check("这一轮进了对话历史(下次问会带上)",
                  len(adv_mod.HISTORY) >= 2, str(len(adv_mod.HISTORY)))
            check("问完把输入框腾空,方便接着问下一句",
                  (settings.advisor_question or "") == "",
                  settings.advisor_question)
            check("清空对话能同时清历史与记录",
                  bpy.ops.gk.clear_advisor() == {"FINISHED"}
                  and not adv_mod.HISTORY
                  and "已清空" in bpy.data.texts[adv_mod.DIALOG_TEXT_NAME].as_string())
        finally:
            adv_mod.call_chat_raw = saved_call
            adv_mod.cancel_agent()
            adv_mod.clear_history()
            for name, value in saved_prefs.items():
                setattr(prefs2, name, value)
            settings.advisor_question = saved_q
            # 测试留下的文本块要清掉,免得污染后面的检查 / 被存进文件
            leftover = bpy.data.texts.get(adv_mod.DIALOG_TEXT_NAME)
            if leftover is not None:
                bpy.data.texts.remove(leftover)
    except Exception as exc:
        check("项目顾问算子", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- AI 亲自操作(第五十六轮):只读工具真能读,改必须用户点 ----
    try:
        at_mod = __import__(f"{module_name}.advisor_tools",
                            fromlist=["tools_schema"])
        adv2 = __import__(f"{module_name}.advisor",
                          fromlist=["start_agent", "poll_agent_step"])
        settings = bpy.context.scene.gk_settings

        # 1) 只读工具在**真 Blender 数据**上都要能跑(读不出东西也要给人话,
        #    而不是抛异常 —— AI 那头会把异常文本当成工具结果继续跑)
        for tool_name in ("read_settings", "list_trees", "list_nodes",
                          "check_export_errors", "get_godot_status"):
            text, _proposal = at_mod.execute(tool_name, {}, bpy.context)
            check(f"只读工具 {tool_name} 能跑通且给了内容",
                  isinstance(text, str) and len(text) > 0,
                  str(text)[:80])

        # 2) **核心保证**:提议**不改数据**
        saved_cam = bool(settings.god_camera)
        settings.god_camera = False
        note, card = at_mod.build_proposal(
            {"action": "enable_god_camera", "why": "方便看场景"})
        check("提议生成了卡片", card is not None and card["action"]
              == "enable_god_camera", str(card))
        check("提议**没有**改数据(勾选还是关的)",
              bool(settings.god_camera) is False, str(settings.god_camera))

        # 3) 用户点『执行』才真改
        adv2.add_proposal(card)
        result = bpy.ops.gk.apply_advisor_action(index=0)
        check("点『执行』才真的改了项目",
              result == {"FINISHED"} and bool(settings.god_camera) is True,
              f"{result} god_camera={settings.god_camera}")
        check("执行完卡片就没了(不会重复执行)",
              adv2.agent_proposals() == [])
        # 没有卡片时点执行要明确拒绝
        try:
            bpy.ops.gk.apply_advisor_action(index=0)
            check("没有待确认动作时『执行』被挡下", False, "竟然返回成功")
        except RuntimeError as exc:
            check("没有待确认动作时『执行』被挡下", "没有待确认的动作" in str(exc),
                  str(exc)[:60])

        # 4) 多张卡片时**按下标点名**:忽略第 2 张不能动到第 1 张
        settings.god_camera = False
        _n1, cam_card = at_mod.build_proposal({"action": "enable_god_camera",
                                               "why": "看场景"})
        _n2, toon_card = at_mod.build_proposal(
            {"action": "set_toon_materials", "value": "false",
             "why": "想核对细节"})
        adv2.add_proposal(cam_card)
        adv2.add_proposal(toon_card)
        check("两张卡片都在(不会互相覆盖)",
              len(adv2.agent_proposals()) == 2,
              str([c["action"] for c in adv2.agent_proposals()]))
        result = bpy.ops.gk.dismiss_advisor_action(index=1)
        left = adv2.agent_proposals()
        check("『忽略』按**下标**点名(忽略第 2 张不误伤第 1 张)",
              result == {"FINISHED"} and len(left) == 1
              and left[0]["action"] == "enable_god_camera"
              and bool(settings.god_camera) is False,
              f"{result} 剩={[c['action'] for c in left]}")
        adv2.clear_proposals()

        # 4b) 新登记的动作也要真能落地(卡通材质开关)
        saved_toon = bool(settings.toon_materials)
        apply_card = {"action": "set_toon_materials",
                      "target": "", "value": "false"}
        ok2, msg2 = at_mod.apply_proposal(bpy.context, apply_card)
        check("新动作 set_toon_materials 能落地",
              ok2 and bool(settings.toon_materials) is False, msg2)
        settings.toon_materials = saved_toon

        # 5) 手动执行入口(不经过 AI)也要能用 —— 枚举得列得出动作
        items = at_mod.action_menu_items(None)
        check(f"手动动作清单列得出({len(items)} 个)",
              len(items) == len(at_mod.ACTIONS) and len(items) > 0, str(items)[:80])

        # 6) Agent 循环:真只读工具在**主线程**里被执行,结果喂回下一轮
        settings.god_camera = False
        saved_raw = adv2.call_chat_raw
        rounds = [
            {"choices": [{"message": {"tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "read_settings", "arguments": "{}"}}]}}]},
            {"choices": [{"message": {"content": "看完了,god_camera 是关的"}}]},
        ]
        sent = []

        def fake_raw(_b, _k, _m, messages, timeout=60, tools=None):
            sent.append([dict(m) for m in messages])
            return True, rounds[min(len(sent) - 1, len(rounds) - 1)]

        adv2.call_chat_raw = fake_raw
        try:
            adv2.start_agent("http://x", "k", "m",
                             [{"role": "user", "content": "现在什么设置"}])
            deadline = time.time() + 15
            while time.time() < deadline:
                state, result = adv2.poll_agent_step(bpy.context)
                if state == "done":
                    break
                time.sleep(0.02)
        finally:
            adv2.call_chat_raw = saved_raw
        check("Agent 循环在真 Blender 里跑完", state == "done", f"{state} {result}")
        check("只读工具在主线程里真的执行了(读到了真设置)",
              any("god_camera" in str(m.get("content", ""))
                  for m in sent[1] if m.get("role") == "tool"),
              str([m for m in sent[1] if m.get("role") == "tool"])[:150])
        check("侧栏能看到它做了什么", adv2.agent_steps() and
              "读了场景设置" in adv2.agent_steps(), str(adv2.agent_steps()))
        check("整局 Agent 走完不擅自改数据",
              bool(settings.god_camera) is False, str(settings.god_camera))

        settings.god_camera = saved_cam
        adv2.cancel_agent()
    except Exception as exc:
        check("AI 亲自操作", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- AI 听觉(第五十七轮):新节点 + 新行为 + 新条件来源 ----
    try:
        # ⚠ 要 `nodes.nodes`(门面),不是 `nodes`(包)—— 常量在门面里
        nodes_mod = __import__(f"{module_name}.nodes.nodes",
                               fromlist=["ALL_CLASSES"])
        all_nodes = {cls.bl_idname for cls in nodes_mod.ALL_CLASSES}
        check("『声音_发出噪音』已注册", "GKNodeNoise" in all_nodes)
        check("『AI_听见』已注册", "GKNodeAiHear" in all_nodes)
        behaviors = {item[0]: item[1] for item in nodes_mod.AI_BEHAVIORS}
        check("AI 行为多了『去查看』",
              behaviors.get("INVESTIGATE") == "去查看", str(behaviors)[:80])
        check("AI 条件来源多了『听见动静 / 安静下来』",
              {"HEARD", "CALM"} <= set(nodes_mod.AI_COND_SOURCES),
              str(nodes_mod.AI_COND_SOURCES))
        # 听觉转场**不该**被要求选目标(踩过的误报)
        check("听觉条件不要求选目标",
              tuple(nodes_mod.AI_GOAL_COND_SOURCES) == ("SEE", "LOST")
              and tuple(nodes_mod.AI_HEAR_COND_SOURCES) == ("HEARD", "CALM"))
        check("边的标签念得出听觉",
              "听见" in nodes_mod.ai_edge_label({"kind": "HEARD"})
              and "安静" in nodes_mod.ai_edge_label({"kind": "CALM"}))
    except Exception as exc:
        check("AI 听觉节点", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 恐怖氛围套件(第五十九轮):惊吓三连 / 紧张度 / 心跳 / 总线音量 ----
    try:
        r59_nodes = __import__(f"{module_name}.nodes.nodes",
                               fromlist=["ALL_CLASSES"])
        r59_all = {cls.bl_idname for cls in r59_nodes.ALL_CLASSES}
        check("『惊吓_事件』已注册", "GKNodeScare" in r59_all)
        check("『氛围_紧张度』已注册", "GKNodeTension" in r59_all)
        check("『氛围_心跳』已注册", "GKNodeHeartbeat" in r59_all)
        check("『音效_总线音量』已注册", "GKNodeBusVolume" in r59_all)
        check("『氛围』进了分类顺序表", "氛围" in r59_nodes.CATEGORY_ORDER,
              str(r59_nodes.CATEGORY_ORDER))

        r59_tree = bpy.data.node_groups.new("氛围冒烟59", "GKGameLogicTree")
        r59_ready = r59_tree.nodes.new("GKNodeOnReady")
        r59_phys = r59_tree.nodes.new("GKNodeOnPhysicsProcess")
        r59_scare = r59_tree.nodes.new("GKNodeScare")
        r59_scare.gk_path = "res://art/slam.wav"
        r59_scare.gk_strength = 14.0
        r59_bus = r59_tree.nodes.new("GKNodeBusVolume")
        r59_bus.gk_bus = "Master"
        r59_bus.gk_db = -80.0
        r59_heart = r59_tree.nodes.new("GKNodeHeartbeat")
        r59_tense = r59_tree.nodes.new("GKNodeTension")
        r59_tense.gk_threat = "冒烟怪"
        r59_tree.links.new(r59_ready.outputs["执行"], r59_scare.inputs["执行"])
        r59_tree.links.new(r59_scare.outputs["完成"], r59_bus.inputs["执行"])
        r59_tree.links.new(r59_phys.outputs["执行"], r59_heart.inputs["执行"])
        r59_tree.links.new(r59_tense.outputs["紧张度"], r59_heart.inputs["紧张度"])

        r59_code, r59_warn = codegen.generate(r59_tree, {
            "dimension": "3", "base_class": "CharacterBody3D",
        })
        check("氛围:惊吓_事件 三连都出码(震/闪/响)",
              "_gk_shake_cam" in r59_code
              and "ColorRect.new()" in r59_code
              and "AudioStreamPlayer.new()" in r59_code
              and 'load("res://art/slam.wav")' in r59_code,
              r59_code[-500:])
        check("氛围:紧张度 -> 心跳(内联 clampf + 越快越响)",
              "clampf(" in r59_code
              and "_gk_heart_tension += (clampf(" in r59_code
              and "lerpf(" in r59_code,
              r59_code[-500:])
        check("氛围:总线音量走 AudioServer",
              'AudioServer.get_bus_index("Master")' in r59_code
              and "AudioServer.set_bus_volume_db(" in r59_code,
              r59_code[-500:])
        check("氛围:生成无告警", not r59_warn, str(r59_warn)[:300])

        class _R59Layout:
            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AttributeError(name)

                def recorder(*_args, **_kwargs):
                    return self
                return recorder

        r59_draw_ok = True
        r59_draw_error = ""
        for r59_card in (r59_scare, r59_bus, r59_heart, r59_tense):
            try:
                r59_card.draw_buttons(bpy.context, _R59Layout())
            except Exception as exc:                       # noqa: BLE001
                r59_draw_ok = False
                r59_draw_error = (f"{r59_card.bl_label}: "
                                  f"{type(exc).__name__}: {exc}")
                break
        check("氛围:四张节点卡片都能画完", r59_draw_ok, r59_draw_error)

        bpy.data.node_groups.remove(r59_tree)
    except Exception as exc:
        check("恐怖氛围套件", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 动画混合树(第六十一轮):AnimationTree + BlendSpace1D ----
    try:
        r61_nodes = __import__(f"{module_name}.nodes.nodes",
                               fromlist=["ALL_CLASSES"])
        r61_all = {cls.bl_idname for cls in r61_nodes.ALL_CLASSES}
        check("『动画_混合树』已注册", "GKNodeBlendTree" in r61_all)
        check("『动画_混合树』归在『动画 ▸ 播放』下",
              r61_nodes.SUBCATEGORY_OF.get("GKNodeBlendTree") == "播放",
              str(r61_nodes.SUBCATEGORY_OF.get("GKNodeBlendTree")))

        r61_tree = bpy.data.node_groups.new("混合树冒烟61", "GKGameLogicTree")
        r61_ready = r61_tree.nodes.new("GKNodeOnReady")
        r61_blend = r61_tree.nodes.new("GKNodeBlendTree")
        r61_blend.gk_points = "走:0|慢跑:1|冲刺:2"
        r61_tree.links.new(r61_ready.outputs["执行"], r61_blend.inputs["执行"])

        r61_code, r61_warn = codegen.generate(r61_tree, {
            "dimension": "3", "base_class": "CharacterBody3D",
        })
        check("混合树:真的建 AnimationTree + BlendSpace1D",
              "func _gk_ensure_blend_tree(" in r61_code
              and "AnimationTree.new()" in r61_code
              and "AnimationNodeBlendSpace1D.new()" in r61_code
              and "space.add_blend_point(" in r61_code,
              r61_code[-500:])
        check("混合树:三个混合点都带过去",
              r61_code.count('["走", 0.0]') >= 1
              and r61_code.count('["慢跑", 1.0]') >= 1
              and r61_code.count('["冲刺", 2.0]') >= 1,
              r61_code[-500:])
        check("混合树:每物理帧推 blend_position",
              "_physics_process(" in r61_code
              and 'set("parameters/blend_position"' in r61_code,
              r61_code[-500:])
        check("混合树:生成无告警", not r61_warn, str(r61_warn)[:300])

        class _R61Layout:
            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AttributeError(name)

                def recorder(*_args, **_kwargs):
                    return self
                return recorder

        r61_draw_ok = True
        r61_draw_error = ""
        try:
            r61_blend.draw_buttons(bpy.context, _R61Layout())
        except Exception as exc:                          # noqa: BLE001
            r61_draw_ok = False
            r61_draw_error = f"{type(exc).__name__}: {exc}"
        check("混合树:节点卡片能画完", r61_draw_ok, r61_draw_error)

        bpy.data.node_groups.remove(r61_tree)
    except Exception as exc:
        check("动画混合树", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- UI 注册:用"面板能不能被 Blender 找到"来验证 ----
    try:
        panel_names = [cls.__name__ for cls in bpy.types.Panel.__subclasses__()]
        check("侧栏面板已注册", "GK_PT_Main" in panel_names, str(panel_names)[:200])
    except Exception as exc:
        check("侧栏面板已注册", False, f"{type(exc).__name__}: {exc}")
    try:
        menu_names = [cls.__name__ for cls in bpy.types.Menu.__subclasses__()]
        check("添加节点菜单已注册", "GK_MT_AddNodeCategories" in menu_names)
    except Exception as exc:
        check("添加节点菜单已注册", False, f"{type(exc).__name__}: {exc}")

    # ---- 插件偏好设置(2026-10-04:以前从未注册,面板打不开)--------
    try:
        prefs_module = __import__(f"{module_name}.project.addon_prefs",
                                  fromlist=["GKAddonPreferences"])
        prefs_cls = prefs_module.GKAddonPreferences
        check("插件偏好设置已注册", hasattr(prefs_cls, "bl_rna"),
              f"bl_idname={getattr(prefs_cls, 'bl_idname', '?')}")
        # bl_idname 必须等于插件根模块名,否则 Blender 找不到归属
        check("偏好设置 bl_idname 指向插件根",
              getattr(prefs_cls, "bl_idname", "") == module_name,
              f"bl_idname={getattr(prefs_cls, 'bl_idname', '?')} "
              f"module={module_name}")
    except Exception as exc:
        check("插件偏好设置已注册", False, f"{type(exc).__name__}: {exc}")

    # 偏好设置**读得到**才算真的生效:godot_path / auto_export_on_run 都靠它。
    # 以前 preferences() 用的是错的插件名,永远返回 None —— 面板里改的设置
    # 全都不起作用(用户 2026-10-04)。
    project_module = __import__(f"{module_name}.project",
                                fromlist=["preferences"])
    prefs_obj = project_module.preferences(bpy.context)
    check("偏好设置可读取(preferences() 拿到对象)", prefs_obj is not None,
          "返回 None -> Godot 路径 / 运行前自动导出等设置不会生效")
    check("偏好设置里有 godot_path / auto_export_on_run",
          prefs_obj is not None and hasattr(prefs_obj, "godot_path")
          and hasattr(prefs_obj, "auto_export_on_run"))

    categories = [cls.bl_label for cls in
                  __import__(f"{module_name}.ui", fromlist=["CATEGORY_MENUS"]).CATEGORY_MENUS]
    check(f"分类子菜单 ({len(categories)})", len(categories) >= 8, str(categories))
    # 动态菜单类很容易静默注册失败，必须逐个确认拿到了 bl_rna
    ui_module = __import__(f"{module_name}.ui", fromlist=["CATEGORY_MENUS"])
    unregistered = [cls.__name__ for cls in ui_module.CATEGORY_MENUS if not hasattr(cls, "bl_rna")]
    check("所有分类子菜单都注册成功", not unregistered, str(unregistered))

    # ---- 右键菜单的排布(用户 2026-10-03,照 UPBGE)------------------
    props_module = __import__(f"{module_name}.properties", fromlist=["node_menu_tree"])
    menu_tree = list(props_module.node_menu_tree(None))
    groups = {category: subs for category, subs in menu_tree}
    input_subs = [name for name, _classes in (groups.get("输入") or [])]
    check("『输入』分成了 键盘 / 鼠标 / 手柄 / 触屏",
          all(want in input_subs for want in ("键盘", "鼠标", "手柄", "触屏")),
          str(input_subs))
    calc_subs = [name for name, _classes in (groups.get("运算") or [])]
    check("『运算』分成了 常量 / 数学 / 向量 / 逻辑 / 转换",
          all(want in calc_subs for want in ("常量", "数学", "向量", "逻辑", "转换")),
          str(calc_subs))
    mouse_nodes = dict(groups.get("输入") or []).get("鼠标", [])
    check(f"鼠标节点 ({len(mouse_nodes)})", len(mouse_nodes) >= 5,
          ", ".join(cls.bl_label for cls in mouse_nodes))

    # ---- "按一下键盘就绑定"的算子(照 UPBGE 的 Press & Choose)--------
    check("『按一下键』算子已注册",
          "GK_OT_CaptureKey" in [cls.__name__ for cls in bpy.types.Operator.__subclasses__()])
    # 直接调 `apply_captured_key`(模拟按了一下 W)——
    # 算子本身不能直接 new,所以绑键逻辑特意抽成了普通函数
    io_module = __import__(f"{module_name}.operator_io",
                           fromlist=["apply_captured_key"])
    tree = None
    for group in bpy.data.node_groups:
        if getattr(group, "bl_idname", "") == "GKGameLogicTree":
            tree = group
            break
    if tree is None:
        tree = bpy.data.node_groups.new("GK按键测试", "GKGameLogicTree")
    tree.use_fake_user = True
    key_node = tree.nodes.new("GKNodeKey")
    key_node.name = "按一下"
    # 让 props.current_tree(context) 能找到这张树 —— 它按"当前活动的节点树"
    # 找,没有界面时回退到 scene 上的设置。设不上也没关系:
    # apply_captured_key 找不到树会返回失败,我们据实报出来。
    try:
        for attr in ("gk_active_tree", "gk_tree_name"):
            if hasattr(bpy.context.scene, attr):
                setattr(bpy.context.scene, attr, tree.name)
    except Exception:  # noqa: BLE001
        pass
    ok, message = io_module.apply_captured_key(bpy.context, key_node.name,
                                               "W", tree=tree)
    check("捕获到的按键写进了节点", ok and key_node.gk_key == "W",
          f"gk_key = {key_node.gk_key!r} ({message})")
    check("动作名按按键自动生成", key_node.gk_action == "key_w",
          f"gk_action = {key_node.gk_action!r}")
    # 改绑另一个键 -> 自动生成的动作名要跟着变(否则看着像没生效)
    io_module.apply_captured_key(bpy.context, key_node.name, "Q", tree=tree)
    check("改绑按键后动作名跟着变", key_node.gk_action == "key_q",
          f"gk_action = {key_node.gk_action!r}")

    # ---- "等按键"到底认哪些事件(2026-10-03 用户报"未正常工作")--------
    # 病根:`event.type == "KEYDOWN"` —— Blender 里**没有**这个类型,
    # 按字母键时 event.type 就是键名("W")、event.value 是 "PRESS"。
    # 所以模态一直挂着,状态栏那句提示不消失。
    def _event(kind, value="PRESS"):
        return type("E", (), {"type": kind, "value": value})()

    cases = [
        (_event("W"), ("W", False), "按 W"),
        (_event("SPACE"), ("SPACE", False), "按空格"),
        (_event("LEFT_SHIFT"), ("LEFT_SHIFT", False), "按左 Shift"),
        (_event("A", "RELEASE"), (None, False), "松开 A 不算按键"),
        (_event("MOUSEMOVE", "NOTHING"), (None, False), "鼠标移动要忽略"),
        (_event("TIMER", "NOTHING"), (None, False), "计时器要忽略"),
        (_event("LEFTMOUSE"), (None, False), "鼠标键不走键盘节点"),
        (_event("ESC"), (None, True), "Esc = 取消"),
    ]
    bad = []
    for event, want, label in cases:
        got = io_module.key_from_event(event)
        if got != want:
            bad.append(f"{label}: 期望 {want} 得到 {got}")
    check("『等按键』认得对(8 种事件)", not bad, "; ".join(bad))

    # ---- 第三人称角色预设(第六十三轮:补上"按钮接没接上算子"这一环)----
    # ── 为什么现在才测(用户报的"这里显示得没意义")──────────────────
    # `presets.create_character_preset` 一直有离线单测、一直是绿的,但
    # **算子从没写进 GK_CLASSES** -> 侧栏『预设』盒子那句
    # `layout.operator("gk.character_preset")` 是个**空按钮**
    # (Blender 对不存在的算子是静默跳过的),盒子里只剩一行说明文字。
    # 这一关真跑一次算子:图建出来了、连好了、脚本也编得出来。
    #
    # 放在最末:预设会写场景设置(dimension / 基类 / 用哪张图 / 类名),
    # 放前面会把后面那些"按设置导出"的断言带歪。
    try:
        bpy.ops.mesh.primitive_cube_add(size=1.0)
        hero = bpy.context.active_object
        hero.name = "预设角色"
        for obj in list(bpy.context.scene.objects):
            obj.select_set(obj is hero)
        bpy.context.view_layer.objects.active = hero
        # 先把场景的『上帝摄像机』关掉 —— 预设**不许**去动它(第六十五轮:
        # 用户说"预设里不需要加入调试用的上帝机")
        settings = getattr(bpy.context.scene, "gk_settings", None)
        if settings is not None and hasattr(settings, "god_camera"):
            settings.god_camera = False
        result = bpy.ops.gk.character_preset()
        logic = bpy.data.node_groups.get("角色逻辑(预设)")
        anim = bpy.data.node_groups.get("角色动作(预设)")
        check("预设:算子跑通并建出两张图",
              result == {"FINISHED"} and logic is not None and anim is not None,
              f"{result} logic={logic} anim={anim}")
        check("预设:逻辑图连好了(节点 / 连线都不少)",
              logic is not None and len(logic.nodes) > 10 and len(logic.links) > 10,
              f"{len(logic.nodes) if logic else 0} 节点 / "
              f"{len(logic.links) if logic else 0} 连线")
        preset_code, preset_warn = codegen.generate(logic, {
            "dimension": "3", "base_class": "CharacterBody3D",
            "export_tunables": True})
        check("预设:生成的脚本能编出角色控制",
              "move_and_slide()" in preset_code,
              str(preset_warn)[:200])
        check("预设:角色被配成『脚本挂载目标』",
              bool(getattr(getattr(hero, "gk_physics", None),
                           "script_target", False)))
        # ---- 第六十五轮:对话框里不该有"调试用的上帝机 / 测试用的机位"----
        # 用户原话:"预设里不需要加入调试用的上帝机的,还有摄像机的视角只是
        # 测试这个功能的,不需要添加预设,预设有的是输入,视角选择,视角缩放"。
        # ⚠ 走 **RNA**(`get_rna_type()`)而不是翻 `Operator.__subclasses__()`:
        #   后者可能翻到同一进程里留下的**旧类副本**(实测:插件在本次运行里
        #   被加载过两次时,按名字找会拿到旧那一份,断言就红了 —— 而按钮实际
        #   调的是 RNA 上这份)。RNA 才是用户点下去真正生效的那个。
        try:
            preset_props = set(
                bpy.ops.gk.character_preset.get_rna_type().properties.keys())
        except Exception as exc:                          # noqa: BLE001
            preset_props = set()
            check("预设:算子 RNA 读得到", False, f"{type(exc).__name__}: {exc}")
        check("预设:对话框里没有机位名 / 上帝机这几栏",
              "camera_side" not in preset_props
              and "camera_top" not in preset_props
              and "with_god_camera" not in preset_props
              and "move_speed" in preset_props,
              str(sorted(preset_props)))
        # ---- 第七十一轮:把 **invoke** 那条路也真跑一遍 ──────────────────
        # 侧栏那个按钮是 `layout.operator("gk.character_preset")` —— 默认走
        # INVOKE,进的是 `invoke()`(弹小对话框)。以前冒烟只跑 `execute`,
        # invoke 里写坏(属性名打错 / `invoke_props_dialog` 用法变了)没人会
        # 发现,用户点按钮就炸。真机实测:`-b` 下没有窗口,Blender 会把
        # `invoke_props_dialog` 回落成"直接执行",所以返回 {'FINISHED'};
        # 有窗口时是 {'RUNNING_MODAL'} —— 两种都算通。
        preset_before = {group.name for group in bpy.data.node_groups}
        invoke_result = None
        invoke_error = ""
        try:
            invoke_result = bpy.ops.gk.character_preset("INVOKE_DEFAULT")
        except Exception as exc:                          # noqa: BLE001
            invoke_error = f"{type(exc).__name__}: {exc}"
        check("预设:invoke(点按钮弹对话框)那条路走得通",
              invoke_result in ({"FINISHED"}, {"RUNNING_MODAL"}),
              invoke_error or str(invoke_result))
        # 这一趟会**再建一套**图(名字带 .001),清掉它,别影响后面的断言
        for name in {g.name for g in bpy.data.node_groups} - preset_before:
            extra = bpy.data.node_groups.get(name)
            if extra is not None:
                bpy.data.node_groups.remove(extra)
        check("预设:没有顺手打开场景的『上帝摄像机』",
              settings is None or not bool(getattr(settings, "god_camera", False)))
    except Exception as exc:
        check("预设", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 『显示_效果开关』(第六十七轮)----
    # 用户在玩法图里放一个开关,按勾选开 / 关本关的显示效果;每张图只许一个。
    # 这一轮验的是:**节点真注册进 Blender**、生成物里 helper 只有一份、
    # 界面与导出两侧都拦得住"放第二个"。
    try:
        r67_tree = bpy.data.node_groups.new("显示开关测试", "GKGameLogicTree")
        r67_ready = r67_tree.nodes.new("GKNodeOnReady")
        r67_switch = r67_tree.nodes.new("GKNodeDisplaySwitch")
        r67_out = r67_ready.outputs.get("执行")
        r67_in = r67_switch.inputs.get("执行")
        r67_tree.links.new(r67_out, r67_in)

        check("显示开关:节点能建出来 + 三个勾选项都在",
              hasattr(r67_switch, "gk_env_on")
              and hasattr(r67_switch, "gk_toon_on")
              and hasattr(r67_switch, "gk_debug_on"))
        check("显示开关:三项默认都是开",
              bool(r67_switch.gk_env_on) and bool(r67_switch.gk_toon_on)
              and bool(r67_switch.gk_debug_on))
        r67_ins = [spec["name"] for spec in r67_switch.gk_inputs]
        r67_outs = [spec["name"] for spec in r67_switch.gk_outputs]
        check("显示开关:执行进 / 完成出",
              "执行" in r67_ins and "完成" in r67_outs, f"{r67_ins} {r67_outs}")

        # 卡面画得完(侧栏瘦身那次的教训:画不出来的卡片没人会发现)
        class _R67Layout:      # 最小替身:随便怎么调都返回自己
            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AttributeError(name)

                def recorder(*_args, **_kwargs):
                    return self
                return recorder

        r67_draw_error = ""
        try:
            r67_switch.draw_buttons(bpy.context, _R67Layout())
        except Exception as exc:                          # noqa: BLE001
            r67_draw_error = f"{type(exc).__name__}: {exc}"
        check("显示开关:节点卡片能画完", not r67_draw_error, r67_draw_error)

        r67_options = {"dimension": "3", "base_class": "CharacterBody3D",
                       "export_tunables": True}
        r67_code, r67_warn = codegen.generate(r67_tree, r67_options)
        check("显示开关:生成无告警", not r67_warn, str(r67_warn)[:200])
        check("显示开关:helper 只生成一份 + 调用点带上三个开关",
              r67_code.count("func _gk_display_effects(") == 1
              and "_gk_display_effects(true, true, true)" in r67_code)
        check("显示开关:三项各生成对应的关闭动作",
              "as WorldEnvironment" in r67_code
              and 'set("debug_panel"' in r67_code
              and "material_override" in r67_code)

        # ① 导出侧兜底:手工再塞一个 -> 生成时报警(绕过界面也要拦得住)
        r67_extra = r67_tree.nodes.new("GKNodeDisplaySwitch")
        r67_extra.name = "多余的开关"
        _r67_dup_code, r67_dup_warn = codegen.generate(r67_tree, r67_options)
        check("显示开关:导出侧拦得住第二个(告警)",
              any("显示_效果开关" in w and "一个" in w for w in r67_dup_warn),
              str(r67_dup_warn)[:200])
        r67_tree.nodes.remove(r67_extra)

        # ② 界面侧:再点『添加』应被拒绝(只留一个)
        r67_block = operator_io.singleton_error(type(r67_switch), r67_tree)
        check("显示开关:界面侧判定得出『已经有一个』", bool(r67_block),
              str(r67_block))
        # 让 _tree_of 认准这棵树:只让这一棵里有选中节点
        for r67_group in bpy.data.node_groups:
            for r67_node in getattr(r67_group, "nodes", ()) or ():
                try:
                    r67_node.select = False
                except Exception:                          # noqa: BLE001
                    pass
        r67_switch.select = True
        r67_tree.nodes.active = r67_switch
        r67_before = len(r67_tree.nodes)
        r67_rejected = False
        try:
            r67_result = bpy.ops.gk.add_node(node_type="GKNodeDisplaySwitch")
            r67_rejected = r67_result == {"CANCELLED"}
        except RuntimeError as exc:
            # ⚠ Blender 对 report({'ERROR'}) 的算子是**抛 RuntimeError**,
            #   不是返回 CANCELLED(和"找不到 Godot"那些算子一个行为)。
            #   抛出来的那句就是给用户看的人话,所以这也算"被拦下"。
            r67_rejected = "显示_效果开关" in str(exc)
        check("显示开关:再点『添加』被拒绝(每图只一个)",
              r67_rejected and len(r67_tree.nodes) == r67_before,
              f"rejected={r67_rejected} {len(r67_tree.nodes)} <- {r67_before}")

        # ③ 不摆这个节点的图 -> 判定为空(不许误报)
        r67_plain = bpy.data.node_groups.new("没开关的图", "GKGameLogicTree")
        check("显示开关:没有它时不拦(不误报)",
              operator_io.singleton_error(type(r67_switch), r67_plain) is None
              and operator_io.singleton_error(node_lib.GKNodePrint, r67_tree) is None)

        bpy.data.node_groups.remove(r67_tree)
        bpy.data.node_groups.remove(r67_plain)
    except Exception as exc:
        check("第六十七轮", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- AI 图补三件(第六十九轮)----
    # 『AI_开始』(显式入口)/『AI_输出』(对外布尔)/『AI_逻辑判断』(条件组合)。
    # 这一层在**真 Blender** 里建图,验:三个节点注册好了、只进 AI 图、
    # 入口真的由『AI_开始』决定、『AI_输出』没填变量名时报得出问题、卡片画得完。
    try:
        props_mod = __import__(f"{module_name}.properties",
                               fromlist=["node_fits_tree"])
        for r69_blob, r69_label in (("GKNodeAiStart", "AI_开始"),
                                    ("GKNodeAiOutput", "AI_输出"),
                                    ("GKNodeAiLogic", "AI_逻辑判断")):
            r69_cls = getattr(node_lib, r69_blob, None)
            check(f"{r69_label}:节点已注册", r69_cls is not None)
            if r69_cls is None:
                continue
            check(f"{r69_label}:只进 AI 图",
                  props_mod.node_fits_tree(r69_cls, "GKGameAiTree")
                  and not props_mod.node_fits_tree(r69_cls, "GKGameLogicTree"))

        r69_ai = bpy.data.node_groups.new("AI冒烟69", "GKGameAiTree")
        r69_patrol = r69_ai.nodes.new("GKNodeAiState")
        r69_patrol.gk_state_name = "巡逻"
        r69_patrol.gk_behavior = "PATROL"
        r69_patrol.gk_patrol_a = "点A"
        r69_patrol.gk_patrol_b = "点B"
        r69_chase = r69_ai.nodes.new("GKNodeAiState")
        r69_chase.gk_state_name = "追"
        r69_chase.gk_behavior = "CHASE"
        r69_chase.gk_goal = "玩家"
        r69_start = r69_ai.nodes.new("GKNodeAiStart")
        r69_ai.links.new(r69_start.outputs["开始"], r69_patrol.inputs["进入"])
        # 巡逻 --(看见玩家)--> 追:少了这条,『追』从入口走不到,图解析会
        # 判它"孤立"(那是**对的**告警,不是 bug)。
        r69_trans = r69_ai.nodes.new("GKNodeAiTransition")
        r69_trans.gk_cond_source = "SEE"
        r69_trans.gk_goal = "玩家"
        r69_ai.links.new(r69_patrol.outputs["转到"], r69_trans.inputs["上游"])
        r69_ai.links.new(r69_trans.outputs["转到"], r69_chase.inputs["进入"])
        r69_out = r69_ai.nodes.new("GKNodeAiOutput")
        r69_out.gk_state_name = "追"
        r69_out.gk_var = "is_chasing_69"
        r69_logic = r69_ai.nodes.new("GKNodeAiLogic")
        r69_logic.gk_op = "OR"

        r69_graph = node_lib.ai_tree_graph(r69_ai)
        check("AI_开始:入口由它决定(不是猜的)",
              r69_graph["entry"] is not None
              and node_lib.ai_state_name(r69_graph["entry"]) == "巡逻",
              str(r69_graph["entry"]))
        check("AI_开始:图解析得出 starts / outputs",
              len(r69_graph.get("starts", [])) == 1
              and len(r69_graph.get("outputs", [])) == 1,
              str(r69_graph.get("starts")))
        check("AI 三件:配好了就不该有问题", not r69_graph["problems"],
              str(r69_graph["problems"])[:200])

        r69_out.gk_var = ""
        r69_bad = node_lib.ai_tree_graph(r69_ai)
        check("AI_输出:没填变量名时报得出问题",
              any("变量名" in text for text in r69_bad["problems"]),
              str(r69_bad["problems"])[:200])
        r69_out.gk_var = "is_chasing_69"

        class _R69Layout:      # 最小替身:随便怎么调都返回自己
            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AttributeError(name)

                def recorder(*_args, **_kwargs):
                    return self
                return recorder

        r69_draw_error = ""
        for r69_node in (r69_start, r69_out, r69_logic):
            try:
                r69_node.draw_buttons(bpy.context, _R69Layout())
            except Exception as exc:                      # noqa: BLE001
                r69_draw_error = (f"{getattr(r69_node, 'bl_label', '?')}: "
                                  f"{type(exc).__name__}: {exc}")
                break
        check("AI 三件:卡片都能画完", not r69_draw_error, r69_draw_error)

        bpy.data.node_groups.remove(r69_ai)
    except Exception as exc:
        check("第六十九轮", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 紧张度『被看见』(第七十轮)----
    # 『氛围_紧张度』多了『被看见』(布尔) + 『被看见下限』:被盯上时把紧张度
    # 至少抬到下限。真 Blender 里验:引脚 / 属性都在、勾上真出 maxf 兜底、
    # 不勾时和以前一样(不多一个字符)、卡片画得完。
    try:
        r70_tree = bpy.data.node_groups.new("紧张度冒烟70", "GKGameLogicTree")
        r70_phys = r70_tree.nodes.new("GKNodeOnPhysicsProcess")
        r70_heart = r70_tree.nodes.new("GKNodeHeartbeat")
        r70_tense = r70_tree.nodes.new("GKNodeTension")
        r70_tense.gk_threat = "冒烟怪"
        r70_tree.links.new(r70_phys.outputs["执行"], r70_heart.inputs["执行"])
        r70_tree.links.new(r70_tense.outputs["紧张度"],
                           r70_heart.inputs["紧张度"])

        r70_ins = [spec["name"] for spec in r70_tense.gk_inputs]
        check("紧张度:多了『被看见』输入引脚", "被看见" in r70_ins, str(r70_ins))
        check("紧张度:『被看见下限』默认 0.8",
              hasattr(r70_tense, "gk_seen_floor")
              and abs(float(r70_tense.gk_seen_floor) - 0.8) < 0.001,
              str(getattr(r70_tense, "gk_seen_floor", None)))

        r70_options = {"dimension": "3", "base_class": "CharacterBody3D"}
        r70_off, _r70_warn = codegen.generate(r70_tree, r70_options)
        check("紧张度:没勾『被看见』= 老行为(没有 maxf 兜底)",
              "maxf(clampf(" not in r70_off)

        r70_tense.inputs.get("被看见").default_value = True
        r70_on, r70_on_warn = codegen.generate(r70_tree, r70_options)
        check("紧张度:勾上『被看见』-> 和下限取大",
              "maxf(clampf(" in r70_on and "0.8" in r70_on,
              r70_on[-400:])
        check("紧张度:勾上后生成无告警", not r70_on_warn, str(r70_on_warn)[:200])

        class _R70Layout:      # 最小替身:随便怎么调都返回自己
            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AttributeError(name)

                def recorder(*_args, **_kwargs):
                    return self
                return recorder

        r70_draw_error = ""
        try:
            r70_tense.draw_buttons(bpy.context, _R70Layout())
        except Exception as exc:                          # noqa: BLE001
            r70_draw_error = f"{type(exc).__name__}: {exc}"
        check("紧张度:卡片能画完", not r70_draw_error, r70_draw_error)

        bpy.data.node_groups.remove(r70_tree)
    except Exception as exc:
        check("第七十轮", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 2D 混合树 + 混合点编辑器(第七十二轮,G 组)----
    # 『动画_混合树2D』(BlendSpace2D)+ 混合点从"手打"变成"下拉挑动画"。
    # 真 Blender 里验:节点/算子/行 PropertyGroup 都注册好了、菜单归类对、
    # 两张卡片画得完、拼串规则和解析一致(读一遍再写一遍还是原样)。
    try:
        r72_nodes = __import__(f"{module_name}.nodes.nodes",
                               fromlist=["ALL_CLASSES"])
        r72_all = {cls.bl_idname for cls in r72_nodes.ALL_CLASSES}
        check("混合树2D:节点已注册", "GKNodeBlendTree2D" in r72_all)
        check("混合树2D:归在『动画 ▸ 播放』下",
              r72_nodes.SUBCATEGORY_OF.get("GKNodeBlendTree2D") == "播放",
              str(r72_nodes.SUBCATEGORY_OF.get("GKNodeBlendTree2D")))
        r72_cls = getattr(node_lib, "GKNodeBlendTree2D", None)
        check("混合树2D:X / Y 两个参数引脚都在",
              r72_cls is not None and
              {spec["name"] for spec in r72_cls.gk_inputs} >= {"执行", "X", "Y"},
              str([spec["name"] for spec in
                   getattr(r72_cls, "gk_inputs", ())]))
        # 『动画_混合树』(1D)这轮加了『动画播放器』(挑动画时分析归属用)
        r72_1d = getattr(node_lib, "GKNodeBlendTree", None)
        check("混合树:多了『动画播放器』栏位",
              r72_1d is not None and
              any(entry[0] == "gk_player" for entry in r72_1d.gk_prop_defs),
              str([entry[0] for entry in getattr(r72_1d, "gk_prop_defs", ())]))

        # 混合点编辑器:算子 + 行结构
        check("混合点:算子已注册", hasattr(bpy.ops.gk, "edit_blend_points"))
        try:
            r72_props = bpy.ops.gk.edit_blend_points.get_rna_type().properties
            r72_is_collection = r72_props["points"].type == "COLLECTION"
            # ⚠ 别查 `dir(bpy.types)`:PropertyGroup 注册后**不一定**出现在那里
            #   (实测没有)。查集合属性的元素类型才是真的"行结构注册好了"。
            r72_row_id = getattr(getattr(r72_props["points"], "fixed_type", None),
                                 "identifier", "")
        except Exception as exc:                          # noqa: BLE001
            r72_is_collection = False
            r72_row_id = ""
            check("混合点:算子 RNA 读得到", False, f"{type(exc).__name__}: {exc}")
        check("混合点:对话框里那一列点是集合属性", r72_is_collection)
        check("混合点:行结构 GK_BlendPointRow 已注册",
              r72_row_id == "GK_BlendPointRow", r72_row_id)

        # 两张卡片画得完(侧栏瘦身那次的教训:画不出来的卡片没人会发现)
        r72_tree = bpy.data.node_groups.new("混合树冒烟72", "GKGameLogicTree")
        r72_ready = r72_tree.nodes.new("GKNodeOnReady")
        r72_one_d = r72_tree.nodes.new("GKNodeBlendTree")
        r72_two_d = r72_tree.nodes.new("GKNodeBlendTree2D")
        for r72_node, r72_pin in ((r72_one_d, "执行"), (r72_two_d, "执行")):
            r72_tree.links.new(r72_ready.outputs.get("执行"),
                               r72_node.inputs.get(r72_pin))

        class _R72Layout:      # 最小替身:随便怎么调都返回自己
            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AttributeError(name)

                def recorder(*_args, **_kwargs):
                    return self
                return recorder

        r72_draw_error = ""
        for r72_card in (r72_one_d, r72_two_d):
            try:
                r72_card.draw_buttons(bpy.context, _R72Layout())
            except Exception as exc:                      # noqa: BLE001
                r72_draw_error = (f"{r72_card.bl_label}: "
                                  f"{type(exc).__name__}: {exc}")
                break
        check("混合点:1D / 2D 两张卡片都画得完", not r72_draw_error,
              r72_draw_error)

        # 读一遍再写一遍 = 原样(界面写回用的就是这对函数)
        r72_codegen = __import__(f"{module_name}.codegen",
                                 fromlist=["format_blend_points"])
        r72_text = "走:0|慢跑:1|冲刺:2"
        check("混合点:1D 拼串和解析一致",
              r72_codegen.format_blend_points(
                  r72_codegen.parse_blend_points(r72_text)) == r72_text)
        r72_text2 = "待机:0:0|前走:0:1|右走:1:0"
        check("混合点:2D 拼串和解析一致",
              r72_codegen.format_blend_points(
                  r72_codegen.parse_blend_points_2d(r72_text2), dim=2)
              == r72_text2)
        bpy.data.node_groups.remove(r72_tree)
    except Exception as exc:
        check("第七十二轮", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 受击叠层 + 混合点倍速(第七十三轮,G 组)----
    # 『动画_混合树』卡片上填『叠层动画』-> 多出一层 one-shot(受击 / 挥砍叠在
    # 走路之上,播完自己收回);『动画_叠层』节点负责触发 / 立刻收起。
    # 真 Blender 里验:注册 / 归类 / 卡片画得完,以及"填没填动画"时生成物
    # 换不换 helper(填了才换成带 Blend2 + OneShot 的那份,参数路径多一层 base/)。
    try:
        r73_nodes = __import__(f"{module_name}.nodes.nodes",
                               fromlist=["ALL_CLASSES"])
        r73_all = {cls.bl_idname for cls in r73_nodes.ALL_CLASSES}
        check("叠层:节点已注册", "GKNodeAnimOverlay" in r73_all)
        check("叠层:归在『动画 ▸ 播放』下",
              r73_nodes.SUBCATEGORY_OF.get("GKNodeAnimOverlay") == "播放",
              str(r73_nodes.SUBCATEGORY_OF.get("GKNodeAnimOverlay")))

        r73_tree = bpy.data.node_groups.new("叠层冒烟73", "GKGameLogicTree")
        r73_ready = r73_tree.nodes.new("GKNodeOnReady")
        r73_blend = r73_tree.nodes.new("GKNodeBlendTree")
        r73_over = r73_tree.nodes.new("GKNodeAnimOverlay")
        r73_tree.links.new(r73_ready.outputs["执行"], r73_blend.inputs["执行"])
        r73_tree.links.new(r73_blend.outputs["完成"], r73_over.inputs["执行"])

        r73_props = [entry[0] for entry in r73_blend.gk_prop_defs]
        check("叠层:混合树卡片多了三个叠层栏位",
              {"gk_overlay", "gk_overlay_in", "gk_overlay_out"} <= set(r73_props),
              str(r73_props))

        r73_options = {"dimension": "3", "base_class": "CharacterBody3D"}
        # ① 没填『叠层动画』:老那份 helper,参数路径也不多一层
        r73_plain, r73_plain_warn = codegen.generate(r73_tree, r73_options)
        check("叠层:没填动画时用老 helper",
              "_gk_ensure_blend_tree(self," in r73_plain and
              "_gk_ensure_blend_tree_layer" not in r73_plain)
        check("叠层:没填动画时『动画_叠层』会告警",
              any("叠层动画" in w for w in r73_plain_warn),
              str(r73_plain_warn)[:200])

        # ② 填上『叠层动画』:换成带 one-shot 的那份 helper
        r73_blend.gk_overlay = "受击"
        r73_layered, r73_layered_warn = codegen.generate(r73_tree, r73_options)
        check("叠层:填了动画后换成带叠层的 helper",
              "_gk_ensure_blend_tree_layer(self," in r73_layered)
        check("叠层:叠层树里有 OneShot 且接在 output 上",
              "AnimationNodeOneShot.new()" in r73_layered and
              'bt.connect_node("oneshot", 0, "base")' in r73_layered and
              'bt.connect_node("oneshot", 1, "shot")' in r73_layered and
              'bt.connect_node("output", 0, "oneshot")' in r73_layered and
              "AnimationNodeBlend2" not in r73_layered)
        check("叠层:参数路径多了一层 base/",
              "parameters/base/blend_position" in r73_layered)
        check("叠层:填了动画后生成无告警", not r73_layered_warn,
              str(r73_layered_warn)[:200])

        # 两张卡片都画得完(侧栏瘦身那次的教训:画不出来的卡片没人会发现)
        class _R73Layout:      # 最小替身:随便怎么调都返回自己
            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AttributeError(name)

                def recorder(*_args, **_kwargs):
                    return self
                return recorder

        r73_draw_error = ""
        for r73_card in (r73_blend, r73_over):
            try:
                r73_card.draw_buttons(bpy.context, _R73Layout())
            except Exception as exc:                      # noqa: BLE001
                r73_draw_error = (f"{r73_card.bl_label}: "
                                  f"{type(exc).__name__}: {exc}")
                break
        check("叠层:两张卡片都画得完", not r73_draw_error, r73_draw_error)

        # 混合点倍速:读一遍写一遍还是原样(速度 = 1 时不写那一段)
        r73_codegen = __import__(f"{module_name}.codegen",
                                 fromlist=["format_blend_points"])
        r73_text = "走:0|跑:1:1.5"
        check("倍速:混合点带速度能原样往返",
              r73_codegen.format_blend_points(
                  r73_codegen.parse_blend_points(r73_text)) == r73_text)
        r73_blend.gk_points = "走:0|跑:1:1.5"
        r73_speed_code, _r73_speed_warn = codegen.generate(r73_tree, r73_options)
        # ⚠ 变量名在"带叠层"那份 helper 里是 src1 / node1(不带叠层那份是
        #   _src),所以只比"共有的那半句",别把变量名写死。
        check("倍速:生成里带上 use_custom_timeline",
              "use_custom_timeline = true" in r73_speed_code and
              ".length / float(entry[2])" in r73_speed_code and
              "stretch_time_scale = true" in r73_speed_code and
              # 自定义时间轴下循环要看节点自己的 loop_mode(真机踩到)
              "loop_mode = Animation.LOOP_LINEAR" in r73_speed_code,
              r73_speed_code[-200:])

        bpy.data.node_groups.remove(r73_tree)
    except Exception as exc:
        check("第七十三轮", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 混合点起名字(第七十四轮)----
    # 不传名字的话 Godot 4.7 每次建树都在输出面板打一条弃用警告
    # (add_blend_point: No name provided ...),用户会以为是我们生成的代码坏了。
    # `add_blend_point` 的第 4 个参数就是名字(真机读过方法签名),一次带上。
    try:
        r74_tree = bpy.data.node_groups.new("混合点起名74", "GKGameLogicTree")
        r74_ready = r74_tree.nodes.new("GKNodeOnReady")
        r74_one = r74_tree.nodes.new("GKNodeBlendTree")
        r74_two = r74_tree.nodes.new("GKNodeBlendTree2D")
        r74_tree.links.new(r74_ready.outputs["执行"], r74_one.inputs["执行"])
        r74_tree.links.new(r74_one.outputs["完成"], r74_two.inputs["执行"])
        r74_code, r74_warn = codegen.generate(
            r74_tree, {"dimension": "3", "base_class": "CharacterBody3D"})
        check("混合点名字:1D 带上第 4 个参数",
              "add_blend_point(anim_node, float(entry[1]), "
              "-1, StringName(entry[0]))" in r74_code)
        check("混合点名字:2D 带上第 4 个参数",
              "float(entry[2])), -1, StringName(entry[0]))" in r74_code)
        check("混合点名字:生成无告警", not r74_warn, str(r74_warn)[:200])
        bpy.data.node_groups.remove(r74_tree)
    except Exception as exc:
        check("第七十四轮", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 切状态的过渡时长(第七十五轮,G 组 21 的一半)----
    # 以前切状态是**硬切**(走↔跑一瞬间换)。现在『动画_状态』和『动画_转场』
    # 上都能填『过渡时长』(秒):转场填了盖过状态上的;都不填 = 硬切(老图不变)。
    try:
        r75_anim = bpy.data.node_groups.new("过渡时长冒烟75", "GKAnimationTree")
        r75_idle = r75_anim.nodes.new("GKNodeAnimState")
        r75_idle.gk_state_name = "idle"
        r75_run = r75_anim.nodes.new("GKNodeAnimState")
        r75_run.gk_state_name = "run"
        r75_trans = r75_anim.nodes.new("GKNodeAnimTransition")
        check("过渡:『动画_状态』有『过渡时长』(默认 0 = 硬切)",
              hasattr(r75_idle, "gk_blend_time")
              and abs(float(r75_idle.gk_blend_time)) < 1e-9,
              str(getattr(r75_idle, "gk_blend_time", "<没有这个属性>")))
        check("过渡:『动画_转场』有『过渡时长』(默认 0 = 用状态上的)",
              hasattr(r75_trans, "gk_blend_time")
              and abs(float(r75_trans.gk_blend_time)) < 1e-9,
              str(getattr(r75_trans, "gk_blend_time", "<没有这个属性>")))
        r75_anim.links.new(r75_idle.outputs["转到"], r75_trans.inputs["上游"])
        r75_anim.links.new(r75_trans.outputs["转到"], r75_run.inputs["进入"])
        r75_trans.gk_cond_source = "ACTION_PRESSED"

        r75_logic = bpy.data.node_groups.new("过渡逻辑75", "GKGameLogicTree")
        r75_ready = r75_logic.nodes.new("GKNodeOnReady")
        r75_ref = r75_logic.nodes.new("GKNodeAnimStateMachineRef")
        r75_ref.gk_tree_name = "过渡时长冒烟75"
        r75_logic.links.new(r75_ready.outputs["执行"], r75_ref.inputs["执行"])
        r75_options = {"dimension": "3", "base_class": "CharacterBody3D"}

        # ① 都不填 -> 老样子(签名不带参数、play 不带 blend)
        r75_plain, r75_plain_warn = codegen.generate(r75_logic, r75_options)
        check("过渡:都不填时保持老签名(硬切)",
              "func _gk_anim_enter(next_state: String) -> void:" in r75_plain
              and "blend: float" not in r75_plain,
              r75_plain[-200:])
        check("过渡:都不填时无告警", not r75_plain_warn, str(r75_plain_warn)[:200])

        # ② 状态上填 -> 多一个 blend 参数,切过去带上秒数
        r75_run.gk_blend_time = 0.2
        r75_state, _r75_w1 = codegen.generate(r75_logic, r75_options)
        check("过渡:状态上填了 -> 进入函数多一个 blend 参数",
              "func _gk_anim_enter(next_state: String, "
              "blend: float = -1.0) -> void:" in r75_state)
        check("过渡:切过去时带上秒数",
              '_gk_anim_enter("run", 0.2)' in r75_state)
        check("过渡:play 用传进来的 blend",
              ".play(next_state, blend)" in r75_state)

        # ③ 转场上填 -> 盖过状态上的
        r75_trans.gk_blend_time = 0.05
        r75_edge, _r75_w2 = codegen.generate(r75_logic, r75_options)
        check("过渡:转场上填了 -> 盖过状态上的",
              '_gk_anim_enter("run", 0.05)' in r75_edge
              and '_gk_anim_enter("run", 0.2)' not in r75_edge)

        bpy.data.node_groups.remove(r75_logic)
        bpy.data.node_groups.remove(r75_anim)
    except Exception as exc:
        check("第七十五轮", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 动画_播放三个事件出口的编译错(第七十六轮,顺手修的老毛病)----
    # 三个出口的代码分别落在 _process / 信号回调里,却引用了 _ready 的
    # **局部变量**(`_ap2` / `_anim1`)—— 真 Godot 直接报
    #     Parse Error: Identifier "_ap2" not declared in the current scope
    # 整份脚本加载不了(离线测试只 grep 字符串,所以一直没抓到)。
    try:
        r76_tree = bpy.data.node_groups.new("动画事件出口76", "GKGameLogicTree")
        r76_ready = r76_tree.nodes.new("GKNodeOnReady")
        r76_play = r76_tree.nodes.new("GKNodePlayAnim")
        r76_play.gk_anim_name = "攻击"
        r76_tree.links.new(r76_ready.outputs["执行"], r76_play.inputs["执行"])
        for r76_socket, r76_text in (("开始时", "开始"), ("每帧", "每帧中"),
                                     ("结束时", "完了")):
            r76_printer = r76_tree.nodes.new("GKNodePrint")
            r76_printer.gk_text = r76_text
            r76_tree.links.new(r76_play.outputs[r76_socket],
                               r76_printer.inputs["执行"])
        r76_code, _r76_warn = codegen.generate(
            r76_tree, {"dimension": "3", "base_class": "CharacterBody3D"})

        # 生成物按函数切开 —— 跨函数引用只有切开才看得见
        # (整份 grep 是抓不到的:名字确实出现过,只是不在这个作用域里)
        r76_funcs, r76_cur, r76_buf = {}, None, []
        for r76_line in r76_code.splitlines():
            if r76_line.startswith("func "):
                if r76_cur:
                    r76_funcs[r76_cur] = "\n".join(r76_buf)
                r76_cur = r76_line.split("(")[0][len("func "):]
                r76_buf = []
            elif r76_cur:
                r76_buf.append(r76_line)
        if r76_cur:
            r76_funcs[r76_cur] = "\n".join(r76_buf)

        r76_borrowed = []
        for r76_fn, r76_src in r76_funcs.items():
            for r76_var in re.findall(r"(?<!_gk)_(?:ap|anim)\d+", r76_src):
                if f"var {r76_var}" not in r76_src:
                    r76_borrowed.append(f"{r76_fn}:{r76_var}")
        check("事件出口:_process/回调不再借用别处的局部变量",
              not r76_borrowed, str(r76_borrowed))

        r76_lines = r76_code.splitlines()
        r76_play_at = next((i for i, ln in enumerate(r76_lines)
                            if ".play(" in ln), -1)
        r76_conn_at = next((i for i, ln in enumerate(r76_lines)
                            if "animation_started.connect(" in ln), -1)
        check("事件出口:先接信号再 play(否则『开始时』漏掉第一次)",
              0 <= r76_conn_at < r76_play_at,
              f"connect@{r76_conn_at} play@{r76_play_at}")
        check("事件出口:三个出口的代码都生成了",
              "每帧中" in r76_code and "开始" in r76_code
              and "完了" in r76_code)
        bpy.data.node_groups.remove(r76_tree)
    except Exception as exc:
        check("第七十六轮(事件出口编译错)", False,
              f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 『动画_事件』(第七十六轮,G 组 21b)----
    # 某段动画播到第 N 秒触发一次。Godot 没有"播到第 N 秒"的信号,
    # 所以是每帧比 `current_animation_position`;靠"进度回退"重新武装,
    # 于是**循环动画每圈响一次**(脚步音就靠这个)。
    try:
        r76b_nodes = __import__(f"{module_name}.nodes.nodes",
                                fromlist=["SUBCATEGORY_OF"])
        check("动画事件:节点已注册并归在『动画 ▸ 播放』下",
              r76b_nodes.SUBCATEGORY_OF.get("GKNodeAnimEvent") == "播放",
              str(r76b_nodes.SUBCATEGORY_OF.get("GKNodeAnimEvent")))

        r76b_tree = bpy.data.node_groups.new("动画事件76", "GKGameLogicTree")
        r76b_physics = r76b_tree.nodes.new("GKNodeOnPhysicsProcess")
        r76b_event = r76b_tree.nodes.new("GKNodeAnimEvent")
        r76b_event.gk_anim_name = "攻击"
        r76b_event.gk_event_time = 0.4
        r76b_tree.links.new(r76b_physics.outputs["执行"],
                            r76b_event.inputs["执行"])
        r76b_printer = r76b_tree.nodes.new("GKNodePrint")
        r76b_printer.gk_text = "打到了"
        r76b_tree.links.new(r76b_event.outputs["触发时"],
                            r76b_printer.inputs["执行"])

        check("动画事件:卡片上两个属性都在(动画名 / 第几秒)",
              hasattr(r76b_event, "gk_anim_name")
              and hasattr(r76b_event, "gk_event_time")
              and hasattr(r76b_event, "gk_anim_action"))

        r76b_code, r76b_warn = codegen.generate(
            r76b_tree, {"dimension": "3", "base_class": "CharacterBody3D"})
        check("动画事件:生成无告警", not r76b_warn, str(r76b_warn)[:200])
        check("动画事件:每帧比播放进度",
              "current_animation_position" in r76b_code
              and '.current_animation == "攻击"' in r76b_code
              and ">= 0.4" in r76b_code,
              r76b_code[-300:])
        check("动画事件:进度回退时重新武装(循环每圈响一次)",
              "var _gk_ev_armed: bool = true" in r76b_code
              and "_gk_ev_armed = true" in r76b_code
              and "_gk_ev_last < 0.4" in r76b_code)
        r76b_at = r76b_code.find("func _process(")
        check("动画事件:『触发时』接的东西落在 _process 里",
              r76b_at >= 0 and "打到了" in r76b_code[r76b_at:],
              r76b_code[-300:])

        # 卡片画得完(侧栏瘦身那次的教训:画不出来的卡片没人会发现)
        class _R76Layout:      # 最小替身:随便怎么调都返回自己
            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AttributeError(name)

                def recorder(*_args, **_kwargs):
                    return self
                return recorder

        r76b_draw_error = ""
        try:
            r76b_event.draw_buttons(bpy.context, _R76Layout())
        except Exception as exc:                      # noqa: BLE001
            r76b_draw_error = f"{type(exc).__name__}: {exc}"
        check("动画事件:卡片画得完", not r76b_draw_error, r76b_draw_error)

        bpy.data.node_groups.remove(r76b_tree)
    except Exception as exc:
        check("第七十六轮(动画_事件)", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 场景开局 BGM(第七十七轮,C 组『场景规则』)----
    # 侧栏选一首 -> 环境脚本开局自己造播放器播着(**循环**);音频走
    # 『资产_声音卡』同一条管线复制进项目的 audio/。
    try:
        r77_ae = __import__(f"{module_name}.asset_export",
                            fromlist=["scene_bgm_path", "scene_bgm_card"])
        r77_scene = bpy.context.scene
        r77_settings = r77_scene.gk_settings
        check("场景BGM:侧栏两个属性都挂上了",
              hasattr(r77_settings, "scene_bgm")
              and hasattr(r77_settings, "scene_bgm_volume"),
              str([n for n in dir(r77_settings) if n.startswith("scene_bgm")]))

        r77_old = (getattr(r77_settings, "scene_bgm", ""),
                   getattr(r77_settings, "scene_bgm_volume", -6.0),
                   getattr(r77_settings, "dimension", "3"))

        r77_settings.scene_bgm = ""
        check("场景BGM:没选就当作没开",
              r77_ae.scene_bgm_path(r77_scene) == ""
              and r77_ae.scene_bgm_card(r77_scene) is None)
        r77_off = r77_ae.ENVIRONMENT_SCRIPT.format(
            **r77_ae._env_fields(r77_scene, camera_name="", fov=0.0))
        check("场景BGM:没选时脚本里是空路径",
              'bgm_path: String = ""' in r77_off)
        check("场景BGM:模板里有播放器和循环那套",
              "AudioStreamPlayer.new()" in r77_off
              and "AudioStreamWAV.LOOP_FORWARD" in r77_off
              and "Engine.is_editor_hint()" in r77_off)

        # 选一首(路径只要以 wav/ogg/mp3 结尾就算音频 —— 这里不必真存在)
        r77_fake = os.path.join(tempfile.gettempdir(), "gk_开局BGM.wav")
        r77_settings.scene_bgm = r77_fake
        r77_settings.scene_bgm_volume = -12.5
        check("场景BGM:选了就认它",
              r77_ae.scene_bgm_path(r77_scene) == r77_fake)
        r77_card = r77_ae.scene_bgm_card(r77_scene)
        check("场景BGM:生成声音卡(才会被复制进 audio/)",
              r77_card is not None and r77_card[1] == r77_fake,
              str(r77_card))
        r77_on = r77_ae.ENVIRONMENT_SCRIPT.format(
            **r77_ae._env_fields(r77_scene, camera_name="", fov=0.0))
        check("场景BGM:脚本里写的是 res://audio/ 下的路径",
              "res://audio/" in r77_on and "-12.5" in r77_on)

        # 2D 场景本来只在"有活要干"时才写环境脚本 —— BGM 也算一件活
        r77_settings.dimension = "2"
        r77_node, r77_files, _r77_cam = r77_ae.build_environment_nodes(
            r77_scene, is_3d=False, scene_name="BGM冒烟")
        check("场景BGM:2D 场景只开了 BGM 也会写环境脚本",
              bool(r77_files) and "bgm_path" in list(r77_files.values())[0],
              str(list(r77_files)))

        (r77_settings.scene_bgm, r77_settings.scene_bgm_volume,
         r77_settings.dimension) = r77_old
    except Exception as exc:
        check("第七十七轮(场景开局 BGM)", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 巡逻路径点 3 个以上(第七十八轮,D 组 8)----
    # 『AI_状态』的巡逻点从两个扩到四个(都是物体选择器)。只填 A / B 时
    # 还是原来那套"翻面布尔"(老图产物一字不变);填了 C / D 就换成
    # "索引 + 方向"的往返:A→B→C→D→C→B→A。
    try:
        ai_nodes = __import__(f"{module_name}.nodes.nodes",
                              fromlist=["GKNodeAiState"])
        r78_props = [entry[0] for entry in ai_nodes.GKNodeAiState.gk_prop_defs]
        check("巡逻点:卡片上多了 C / D 两个选择器",
              "gk_patrol_c" in r78_props and "gk_patrol_d" in r78_props,
              str(r78_props))

        def _r78_code(*patrol):
            ai = bpy.data.node_groups.new("巡逻点冒烟78", "GKGameAiTree")
            state = ai.nodes.new("GKNodeAiState")
            state.gk_state_name = "巡逻"
            state.gk_behavior = "PATROL"
            state.gk_speed = 3.0
            state.gk_stop_distance = 0.7
            for prop, value in patrol:
                setattr(state, prop, value)
            logic = bpy.data.node_groups.new("巡逻逻辑78", "GKGameLogicTree")
            event = logic.nodes.new("GKNodeOnReady")
            enable = logic.nodes.new("GKNodeAiEnable")
            logic.links.new(event.outputs["执行"], enable.inputs["执行"])
            code, warnings = codegen.generate(
                logic, {"dimension": "3", "base_class": "CharacterBody3D"})
            bpy.data.node_groups.remove(logic)
            bpy.data.node_groups.remove(ai)
            return code, warnings

        r78_two, r78_two_warn = _r78_code(("gk_patrol_a", "点A"),
                                          ("gk_patrol_b", "点B"))
        check("巡逻点:只填 A / B 还是老的翻面布尔",
              "var _gk_ai_patrol_1: bool = false" in r78_two
              and "_gk_ai_patrol_1 = not _gk_ai_patrol_1" in r78_two
              and "_gk_ai_wp_index" not in r78_two,
              r78_two[-200:])
        check("巡逻点:两点时无告警", not r78_two_warn, str(r78_two_warn)[:200])

        r78_three, r78_three_warn = _r78_code(("gk_patrol_a", "点A"),
                                              ("gk_patrol_b", "点B"),
                                              ("gk_patrol_c", "点C"))
        check("巡逻点:三点走往返(索引 + 方向)",
              "var _gk_ai_wp_index_1: int = 0" in r78_three
              and "_gk_ai_wp_index_1 >= 2" in r78_three
              and "_gk_ai_wp_index_1 <= 0" in r78_three
              and "_gk_ai_wp_index_1 += 1" in r78_three
              and "_gk_ai_wp_index_1 -= 1" in r78_three,
              r78_three[-300:])
        check("巡逻点:三点时三个点都进了引用表",
              all(name in r78_three for name in ("点A", "点B", "点C")))
        check("巡逻点:三点时无告警", not r78_three_warn,
              str(r78_three_warn)[:200])

        # 卡片画得完(侧栏瘦身那次的教训)
        class _R78Layout:      # 最小替身:随便怎么调都返回自己
            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AttributeError(name)

                def recorder(*_args, **_kwargs):
                    return self
                return recorder

        r78_ai = bpy.data.node_groups.new("巡逻卡片78", "GKGameAiTree")
        r78_state = r78_ai.nodes.new("GKNodeAiState")
        r78_state.gk_behavior = "PATROL"
        r78_draw_error = ""
        try:
            r78_state.draw_buttons(bpy.context, _R78Layout())
        except Exception as exc:                      # noqa: BLE001
            r78_draw_error = f"{type(exc).__name__}: {exc}"
        check("巡逻点:巡逻卡片画得完", not r78_draw_error, r78_draw_error)
        bpy.data.node_groups.remove(r78_ai)
    except Exception as exc:
        check("第七十八轮(巡逻路径点)", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 攻击行为 + 状态的进入时/离开时(第七十九轮,D 组 9 + 10)----
    # D-9:『AI_状态』多一个行为『攻击』—— 走进『攻击距离』停下、面向目标,
    #      每『冷却』秒"打一下"(打什么由『进入时』出口决定,插件不带血量)。
    # D-10:状态卡多了『进入时』/『离开时』两条执行流(照动画状态),
    #      AI 图里也能放『变量_设置』这类"做事的"节点了。
    try:
        ai_nodes = __import__(f"{module_name}.nodes.nodes",
                              fromlist=["GKNodeAiState"])
        r79_props = [entry[0] for entry in ai_nodes.GKNodeAiState.gk_prop_defs]
        check("攻击:卡片上多了『攻击距离』『冷却』两个属性",
              "gk_attack_range" in r79_props and "gk_cooldown" in r79_props,
              str(r79_props))
        r79_behaviors = {item[0] for item in ai_nodes.AI_BEHAVIORS}
        check("攻击:行为表里多了 ATTACK",
              "ATTACK" in r79_behaviors, str(sorted(r79_behaviors)))
        r79_outputs = [pin["name"] for pin in ai_nodes.GKNodeAiState.gk_outputs]
        check("进入时/离开时:状态卡上多了两条执行流出口",
              "进入时" in r79_outputs and "离开时" in r79_outputs,
              str(r79_outputs))

        r79_ai = bpy.data.node_groups.new("攻击冒烟79", "GKGameAiTree")
        r79_patrol = r79_ai.nodes.new("GKNodeAiState")
        r79_patrol.name = "巡逻"
        r79_patrol.gk_state_name = "巡逻"
        r79_patrol.gk_behavior = "PATROL"
        r79_patrol.gk_patrol_a = "点A"
        r79_patrol.gk_patrol_b = "点B"
        r79_attack = r79_ai.nodes.new("GKNodeAiState")
        r79_attack.name = "攻击"
        r79_attack.gk_state_name = "攻击"
        r79_attack.gk_behavior = "ATTACK"
        r79_attack.gk_goal = "玩家"
        r79_attack.gk_attack_range = 2.0
        r79_attack.gk_cooldown = 1.5

        # AI 图里现在能放『变量_设置』(钩子里用),但放不进玩法动作
        r79_flow_ok = True
        try:
            r79_hit = r79_ai.nodes.new("GKNodeSetVar")
            r79_hit.name = "打一下"
            r79_hit.gk_var_name = "攻击中"
            r79_hit.gk_value = 1.0
            r79_ai.links.new(r79_attack.outputs["进入时"],
                             r79_hit.inputs["执行"])
        except Exception:                              # noqa: BLE001
            r79_flow_ok = False
        check("进入时:AI 图里能放『变量_设置』并接上入口", r79_flow_ok)

        r79_trans = r79_ai.nodes.new("GKNodeAiTransition")
        r79_trans.name = "看见玩家"
        r79_trans.gk_cond_source = "SEE"
        r79_trans.gk_goal = "玩家"
        r79_trans.gk_sight = 10.0
        r79_ai.links.new(r79_patrol.outputs["转到"], r79_trans.inputs["上游"])
        r79_ai.links.new(r79_trans.outputs["转到"], r79_attack.inputs["进入"])

        r79_graph = node_lib.ai_tree_graph(r79_ai)
        check("攻击:图解析无问题(目标 + 进入时都齐了)",
              not r79_graph["problems"], str(r79_graph["problems"])[:300])

        r79_logic = bpy.data.node_groups.new("攻击逻辑79", "GKGameLogicTree")
        r79_event = r79_logic.nodes.new("GKNodeOnReady")
        r79_enable = r79_logic.nodes.new("GKNodeAiEnable")
        r79_logic.links.new(r79_event.outputs["执行"], r79_enable.inputs["执行"])
        r79_code, r79_warn = codegen.generate(
            r79_logic, {"dimension": "3", "base_class": "CharacterBody3D"})
        check("攻击:生成冷却计时器 + 走进攻击距离就停",
              "var _gk_ai_atk_1: float = 0.0" in r79_code
              and "_gk_ai_atk_1 -= delta" in r79_code
              and "_gk_ai_atk_1 = 1.5" in r79_code
              and "> 2.0" in r79_code,
              r79_code[-500:])
        check("攻击:『进入时』钩子在每打一下时被调",
              "func _gk_ai_enter_攻击() -> void:" in r79_code
              and "攻击中 = 1.0" in r79_code,
              r79_code[-500:])
        check("攻击:生成无告警", not r79_warn, str(r79_warn)[:300])

        # 卡片画得完(侧栏瘦身那次的教训)
        class _R79Layout:      # 最小替身:随便怎么调都返回自己
            def __getattr__(self, name):
                if name.startswith("_"):
                    raise AttributeError(name)

                def recorder(*_args, **_kwargs):
                    return self
                return recorder

        r79_draw_error = ""
        try:
            r79_attack.draw_buttons(bpy.context, _R79Layout())
        except Exception as exc:                       # noqa: BLE001
            r79_draw_error = f"{type(exc).__name__}: {exc}"
        check("攻击:攻击卡片画得完", not r79_draw_error, r79_draw_error)

        bpy.data.node_groups.remove(r79_logic)
        bpy.data.node_groups.remove(r79_ai)
    except Exception as exc:
        check("第七十九轮(攻击 + 进入时)", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 卸载不报错 ----
    try:
        addon_utils.disable(module_name, default_set=True)
        check("卸载插件", True)
    except Exception as exc:
        check("卸载插件", False, f"{type(exc).__name__}: {exc}")

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
print("SMOKE_RESULT=" + ("OK" if code == 0 and not failed else "FAILED"))
print("=" * 70)

sys.exit(0 if (code == 0 and not failed) else 1)
