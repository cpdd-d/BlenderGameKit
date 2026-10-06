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

    # ---- 注释框(UE 蓝图的 Comment Box)----
    # 选中两个节点 -> 一键建框 -> 应该框住它们,而且**不影响代码生成**
    # (NodeFrame 不是 GK 节点,codegen 必须当它不存在)。
    try:
        for node in tree.nodes:
            node.select = False
        do_jump.select = True
        printer.select = True
        tree.nodes.active = printer
        result = bpy.ops.gk.add_comment_box()
        frames = [n for n in tree.nodes if n.bl_idname == "NodeFrame"]
        frame = frames[0] if frames else None
        check("注释框已创建", result == {"FINISHED"} and len(frames) == 1,
              f"{result} frames={len(frames)}")
        check("注释框认了两个子节点",
              frame is not None and do_jump.parent == frame and printer.parent == frame)
        check("注释框有默认标题与颜色",
              frame is not None and frame.label == "注释"
              and bool(frame.use_custom_color) and frame.width > 0.0)
    except Exception as exc:
        check("注释框", False, f"{type(exc).__name__}: {exc}")
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
        # 冒烟场景里**没有相机** -> 导出会提示"运行时会造一台自由飞行观察机"。
        # 那是**好消息**(告诉你没相机时也能看见画面),不是错误;
        # 和"基类自愈"一样属于信息类提示,不该让冒烟失败。
        real_warnings = [w for w in result["warnings"]
                         if "自由飞行观察机" not in str(w)]
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

    # ---- 算子注册 ----
    for op_name in ("gk.export_script", "gk.preview_script", "gk.add_node",
                    "gk.auto_link", "gk.pick_target", "gk.set_target",
                    "gk.tag_anim_category", "gk.pick_anim_name",
                    "gk.pick_anim_tree", "gk.anim_var_add", "gk.anim_var_remove",
                    "gk.anim_var_make_node", "gk.anim_var_pick"):
        check(f"算子已注册 {op_name}", hasattr(bpy.ops, op_name.split(".")[0])
              and op_name.split(".")[1] in dir(getattr(bpy.ops, op_name.split(".")[0])))

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
