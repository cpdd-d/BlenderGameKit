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


def _surface(obj, tiles=False, scale=6.0):
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
            uv.data[index].uv = (co[axes[0]] / scale, co[axes[1]] / scale)


def upgrade_visuals():
    """给三关补"看得见空间感"的材质 / 倒角 / 阴影参数。

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
    这里只保留**和渲染风格无关**的硬装:材质贴图坐标、倒角、
    阴影参数、画质、上帝机开关。

    ── 第一百四十六轮:灯具不再在这里建 ──────────────────────────────
    灯现在由 `make_story_demo` 的 `build_*` 按"哪一盏该坏 / 该闪 /
    该由逻辑点亮"逐盏建(带底座 / 吊杆 / 灯罩,名字也是逻辑图要引用的)。
    这个函数以前会在每面墙上均匀摆 3 盏、并把所有灯的能量统一改成
    9000W —— 那会把"应急红灯开局 0W"这类设计直接抹掉,所以整段删了。
    同理,天花板 / 墙裙也不再由这里生成(几何在 build_* 里,尺寸从
    ROOM 读,免得两边各写一套数字、改一个忘一个)。
    """
    from make_story_demo import ROOM, LOBBY, CORRIDOR, ENDING
    from BlenderGameKit import godot_look
    for scene_name in (LOBBY, CORRIDOR, ENDING):
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
                    # 地砖按 1.6 米一格(第一百四十八轮:2.6 米在 48 米长的
                    # 走廊里一格还是太大,像"四块大板";1.6 米接近真实地砖,
                    # 透视收缩也才读得出纵深)
                    _surface(obj, True, 1.6)
                elif "墙" in obj.name or "柱" in obj.name:
                    _surface(obj)
                if not obj.modifiers.get("边缘倒角"):
                    # ── 倒角宽度按物体最小边收敛 ─────────────────────
                    # 以前写死 0.045:窗棂 / 文件纸这种 1~2 厘米厚的薄片
                    # 会被倒角吃掉整条边(法线翻转、渲染出黑片)。
                    thinnest = min((abs(v) for v in obj.dimensions), default=1.0)
                    width = max(0.004, min(0.045, thinnest * 0.18))
                    bevel = obj.modifiers.new("边缘倒角", "BEVEL")
                    bevel.width = width
                    bevel.segments = 3
            if obj.type == "LIGHT":
                # ⚠ 不再**强制**打开投影(第一百五十一轮第三轮反馈)。
                # 这里以前对所有灯写 `use_shadow = True`,把烛光 / 上照这些
                # 明确 `shadow=False` 的补光又打开了 —— 一盏 1.45 米高的
                # 烛光会在几米外的地上拉出长黑影,看起来像"低角度射灯"。
                # 投影与否交给每盏灯的创建代码(默认本来就是开)。
                if obj.data.shadow_soft_size <= 0.0:
                    obj.data.shadow_soft_size = .12
                # ⚠ 能量 / 射程一概不动:它们现在是**逐盏设计**的
                #   (主光 / 边光 / 应急红灯 / 烛光 / 月光各不相同),
                #   第 124 轮那种"全部改成 9000W"会把明暗设计抹平。
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
        print(f"[恐怖闯关] {scene_name} 硬装:房间 {ROOM[scene_name]}")


def upgrade():
    upgrade_player()
    upgrade_visuals()
