"""Reusable, idempotent migration of the story demo to one persistent pawn."""
import math
import bpy


def upgrade_player():
    from BlenderGameKit.ui import world_settings
    from BlenderGameKit.asset_export import _activate_scene
    from BlenderGameKit import markers
    player = bpy.data.objects.get("玩家") or bpy.data.objects.get("大堂玩家")
    if player is None:
        raise ValueError("找不到示例玩家")
    old_names = {"大堂玩家": "玩家", "走廊玩家": "玩家", "结局玩家": "玩家",
                 "大堂第一人称机位": "第一人称相机", "大堂第三人称机位": "第三人称相机"}
    for scene_name, old_name, position in (("大堂", "大堂玩家", (0, -8, 1)),
                                         ("走廊", "走廊玩家", (-20, -3, 1)),
                                         ("结局", "结局玩家", (0, -6, 1))):
        scene = bpy.data.scenes[scene_name]
        _activate_scene(scene)
        bpy.context.view_layer.update()
        marker = bpy.data.objects.get(scene_name + "出生点")
        if marker is None:
            marker = bpy.data.objects.new(scene_name + "出生点", None)
            scene.collection.objects.link(marker)
            marker.location = position
            original = bpy.data.objects.get(old_name)
            if original is not None:
                # location is available immediately after loading; evaluated matrices
                # may still be identity until this scene's dependency graph is built.
                matrix = original.matrix_basis.copy() if original.parent is None else original.matrix_world.copy()
                marker.matrix_world = matrix
            marker.empty_display_type = "ARROWS"
            marker.empty_display_size = 1.0
        # ── 也让脚本走**新版**的出生点(第一百二十四轮)─────────────────
        # `scene.gk_settings.player_spawn`(老写法)和"物体上的标记 kind"
        # (新写法)两条路导出侧都认。示例里两个都设上,是为了让
        # `make_story_demo.py` 顺便演示新功能 —— 用户照着示例做时,
        # 在视口里看到的是一个**绿色的『玩家出生点』标记**,而不是一个
        # 没有名字含义的空物体。
        markers.set_kind(marker, markers.KIND_PLAYER_START)
        scene.gk_settings.player_spawn = marker
        if scene_name != "大堂":
            for name in (old_name, scene_name + "第一人称机位", scene_name + "第三人称机位"):
                duplicate = bpy.data.objects.get(name)
                if duplicate:
                    bpy.data.objects.remove(duplicate, do_unlink=True)
    for old, new in old_names.items():
        obj = bpy.data.objects.get(old)
        if obj:
            obj.name = new
    _activate_scene(bpy.data.scenes["大堂"])
    bpy.context.view_layer.update()
    for name in ("第一人称相机", "第三人称相机", "手电筒"):
        obj = bpy.data.objects[name]
        if obj.parent != player:
            matrix = obj.matrix_world.copy()
            obj.parent = player
            obj.matrix_world = matrix
    bpy.data.objects["第一人称相机"].data.lens = 24.0
    torch = bpy.data.objects["手电筒"].data
    torch.color = (1.0, .92, .78)
    torch.energy = 8000
    torch.spot_size = math.radians(54)
    torch.spot_blend = .5
    for tree in bpy.data.node_groups:
        if not hasattr(tree, "nodes"):
            continue
        for node in tree.nodes:
            for prop in node.bl_rna.properties:
                if prop.type == "STRING" and prop.identifier.startswith("gk_"):
                    value = getattr(node, prop.identifier)
                    if value in old_names:
                        setattr(node, prop.identifier, old_names[value])
    world_settings().default_player = player
    world_settings().default_map = bpy.data.scenes["大堂"]
    player.gk_physics.logic_tree = "玩家角色逻辑"
    for binding in player.gk_physics.logic_bindings:
        if binding.binding_name in ("第一人称相机", "第三人称相机"):
            binding.target = bpy.data.objects[binding.binding_name]
    move_flashlight_logic()
    return player


def move_flashlight_logic():
    source = bpy.data.node_groups["大堂逻辑"]
    target = bpy.data.node_groups["玩家角色逻辑"]
    if target.nodes.get("随身手电"):
        return
    names = ("电量开局满", "手电关常量", "手电开局关标志", "手电开局关", "按F", "按F了吗",
             "读手电开", "取反", "写手电开", "物理帧(手电)", "读手电开2", "读电量",
             "还有电吗", "灯亮吗", "应用手电", "亮着吗", "每秒耗电", "扣电", "没电了吗",
             "没电分支", "强制关", "电量取整", "电量文字", "刷新电量", "手电每帧顺序")
    frame = target.nodes.new("NodeFrame")
    frame.name = frame.label = "随身手电"
    frame.location = (0, -3000)
    clones = {}
    for name in names:
        node = source.nodes.get(name)
        if not node:
            continue
        clone = target.nodes.new(node.bl_idname)
        clone.name = node.name
        for prop in node.bl_rna.properties:
            if prop.identifier.startswith("gk_") and not prop.is_readonly:
                setattr(clone, prop.identifier, getattr(node, prop.identifier))
        clone.parent = frame
        clone.location = node.location
        for a, b in zip(node.inputs, clone.inputs):
            if hasattr(a, "default_value"):
                b.default_value = a.default_value
        clones[name] = clone
    for link in list(source.links):
        if link.from_node.name in clones and link.to_node.name in clones:
            target.links.new(clones[link.from_node.name].outputs[link.from_socket.name],
                             clones[link.to_node.name].inputs[link.to_socket.name])
    ready = target.nodes.new("GKNodeOnReady")
    ready.name = "随身装备初始化"
    ready.parent = frame
    ready.location = (-1450, 850)
    previous = ready
    for name in ("电量开局满", "手电开局关标志", "手电开局关"):
        target.links.new(previous.outputs[0], clones[name].inputs[0])
        previous = clones[name]
    for name in names + ("没电提示",):
        node = source.nodes.get(name)
        if node:
            source.nodes.remove(node)
    # The battery belongs to the permanent HUD, too.
    widget_name = clones["刷新电量"].gk_widget
    from BlenderGameKit.nodes.nodes import main_ui_design_tree
    main_ui = main_ui_design_tree()
    if main_ui and not main_ui.nodes.get(widget_name):
        for tree in bpy.data.node_groups:
            if tree == main_ui:
                continue
            node = tree.nodes.get(widget_name) if hasattr(tree, "nodes") else None
            if node and node.bl_idname == "GKNodeUiText":
                clone = main_ui.nodes.new(node.bl_idname)
                clone.name = node.name
                for prop in node.bl_rna.properties:
                    if prop.identifier.startswith("gk_") and not prop.is_readonly:
                        setattr(clone, prop.identifier, getattr(node, prop.identifier))
                clone.location = node.location
                tree.nodes.remove(node)
                break


def _texture(name, tiles=False):
    import numpy as np
    image = bpy.data.images.get(name)
    if image:
        return image
    size = 512
    rng = np.random.default_rng(1703 if tiles else 931)
    y, x = np.mgrid[0:size, 0:size]
    fine = rng.random((size, size)) * .018
    grain = .008 * np.sin(x * .071) * np.sin(y * .037)
    if tiles:
        tile = .32 + grain + fine + .07 * ((x // 128 + y // 128) % 2)
        grout = (x % 128 < 3) | (y % 128 < 3)
        tile[grout] *= .27
        rgb = np.stack((tile * .86, tile * .93, tile), axis=-1)
    else:
        plaster = .46 + grain + fine
        rgb = np.stack((plaster, plaster * .93, plaster * .82), axis=-1)
    rgba = np.ones((size, size, 4), dtype=np.float32)
    rgba[:, :, :3] = rgb
    image = bpy.data.images.new(name, width=size, height=size)
    image.pixels.foreach_set(rgba.ravel())
    image.pack()
    return image


def _surface(obj, tiles=False):
    name = "旧石砖地面" if tiles else "风化墙面"
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        bsdf.inputs["Roughness"].default_value = .72 if tiles else .9
        tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = _texture(name + "贴图", tiles)
        mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    obj.data.materials.clear()
    obj.data.materials.append(mat)
    uv = obj.data.uv_layers.active or obj.data.uv_layers.new(name="UVMap")
    for face in obj.data.polygons:
        axis = max(range(3), key=lambda a: abs(face.normal[a]))
        axes = [a for a in range(3) if a != axis]
        for index in face.loop_indices:
            co = obj.matrix_world @ obj.data.vertices[obj.data.loops[index].vertex_index].co
            uv.data[index].uv = (co[axes[0]] / 6.0, co[axes[1]] / 6.0)


def upgrade_visuals():
    """给三关补"看得见空间感"的硬装(天花板 / 墙裙 / 壁灯 / 倒角 / 法线)。

    ── 第 123 轮:这里曾经是"中世纪观感"的**真凶** ────────────────────
    用户原话:"现在的环境渲染成了『中世纪』的风格了,但我想要的是卡通二次元"。
    这个函数在 `make_story_demo.main()` 里排在 `_apply_settings(...ENV_*)`
    **之后**跑,却把刚设好的环境**整组按自己的口味重写**了一遍:
        settings.toon_materials = False          ← 关掉卡通材质(用回 PBR!)
        settings.style_enabled  = False          ← 关掉全局卡通风格
        settings.env_ambient_energy = .09        ← 暗部直接死黑
        settings.env_bg_color = (.009,.012,.018) ← 背景近乎纯黑
        settings.env_fog_color = (.055,.065,.085)← 冷灰雾
    叠上"暖色点光 + ACES",画面就正好是**写实奇幻**(中世纪酒馆):
    暗部黑、亮部被 ACES 压灰、颜色没饱和度。
    所以这一版把这些**全部删掉** —— 环境与卡通风格交给
    `make_story_demo.ENV_* / STYLE_*`(那一份是按二次元配方写的,含
    LINEAR 色调映射 + 抬高的环境光 + 冷色暗部 + 三段明暗)。
    这里只保留**和渲染风格无关**的硬装:几何、材质贴图坐标、倒角、
    阴影参数、画质、上帝机开关。
    """
    from make_story_demo import make_box, make_light
    from BlenderGameKit import godot_look
    for scene_name in ("大堂", "走廊", "结局"):
        scene = bpy.data.scenes[scene_name]
        settings = scene.gk_settings
        # ⚠ 下面这几项**不能**再写 env_* / style_* / toon_* —— 见函数头说明:
        #   它们会被这里覆盖掉,而那正是"环境改不动"的原因。
        settings.env_ssao = True
        settings.env_ssil = True
        settings.render_quality = "HIGH"
        # ⚠ 上帝机**不能**在这里关掉(第 124 轮用户实测):
        #    用户原话:"运行后我上帝机为什么用不了了？"
        #    上一轮我为了"减少视觉噪音"把它设成 False —— 而 `god_camera`
        #    是**导出开关**:关掉 = 三张 .tscn 里压根不生成上帝机脚本,
        #    于是游戏里按 Tab 毫无反应。它现在是默认**开**的(见下面那行),
        #    想关的人去『世界设置 → 调试用相机』里关。
        settings.god_camera = True
        for obj in list(scene.objects):
            if obj.type == "MESH" and not obj.get("gk_environment_preview"):
                if "地面" in obj.name:
                    _surface(obj, True)
                elif "墙" in obj.name or "柱" in obj.name:
                    _surface(obj)
                if not obj.modifiers.get("边缘倒角"):
                    bevel = obj.modifiers.new("边缘倒角", "BEVEL")
                    bevel.width = .045
                    bevel.segments = 3
            if obj.type == "LIGHT":
                obj.data.use_shadow = True
                obj.data.shadow_soft_size = .12
                obj.data.use_custom_distance = True
                obj.data.cutoff_distance = 22 if scene_name != "走廊" else 16
                if obj.name != "手电筒":
                    # 第 123 轮:整体再抬一档(6500->7200 / 4000->5200)。
                    # 灯在**后面**才建(天花板壁灯 4200W 那几盏),所以主灯
                    # 亮一点不会过曝 —— 而"提亮"正是二次元高调画面的第一需要。
                    obj.data.energy = 7200 if scene_name != "走廊" else 5200
        # Wall lamps and trim make scale and depth legible without covering gameplay areas.
        width, depth, height = {"大堂": (30, 24, 6), "走廊": (48, 14, 6), "结局": (18, 18, 5)}[scene_name]
        ceiling_name = scene_name + "天花板"
        if ceiling_name not in bpy.data.objects:
            ceiling = make_box(ceiling_name, (width, depth, .3), (0, 0, height + .15),
                               (.065, .075, .085, 1), scene=scene)
            ceiling.gk_physics.collision_shape = "box"
        for side in (-1, 1):
            for z in (.14, 1.3, height - .25):
                name = f"{scene_name}墙裙{side}_{z}"
                if name not in bpy.data.objects:
                    obj = make_box(name, (width, .16, .18), (0, side * (depth / 2 - .3), z),
                                   (.075, .09, .085, 1), scene=scene)
                bpy.data.objects[name].gk_physics.collision_shape = "box"
            for i, x in enumerate((-width * .3, 0, width * .3)):
                name = f"{scene_name}壁灯{side}_{i}"
                if name in bpy.data.objects:
                    continue
                y = side * (depth / 2 - .55)
                make_box(name + "底座", (.48, .2, .75), (x, y, 3.1), (.035, .045, .05, 1), scene=scene)
                make_box(name + "灯罩", (.26, .27, .48), (x, y - side * .12, 3.1),
                         (.7, .55, .3, 1), scene=scene, glow=((1, .65, .26, 1), 3.0))
                light = make_light(name, "POINT", (x, y - side * .4, 3.1), 4200,
                                   (1, .72, .38) if side == 1 else (.38, .62, 1),
                                   scene, cutoff=11, shadow=True)
                light.data.shadow_soft_size = .10
        # Ensure closed box normals face outward, including ceiling undersides.
        import bmesh
        for obj in scene.objects:
            if obj.type == "MESH" and not obj.get("gk_environment_preview"):
                bm = bmesh.new()
                bm.from_mesh(obj.data)
                bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
                bm.to_mesh(obj.data)
                bm.free()
        # Keep the Blender rendered viewport on the same lights and ambient environment.
        godot_look.apply(scene)


def upgrade():
    upgrade_player()
    upgrade_visuals()
