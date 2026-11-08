# -*- coding: utf-8 -*-
"""生成"我的世界"风格的第一人称方块示例(第一百九十七轮:改成真 Blender 工程)。

用法:
    blender -b --gpu-backend vulkan --python scripts\\make_minecraft_demo.py

产出:
    examples/demo_minecraft/我的世界.blend          (源文件:场景 + 节点图)
    examples/demo_minecraft/我的世界_godot/          (导出产物,可用 Godot 打开)

玩法:
  开始界面(标题 + 开始游戏 / 创建游戏 / 退出游戏)→ 进世界后
  WASD 移动 / 空格跳 / 鼠标转视角 / 数字键 1~4 选方块(草·石·沙·木)
  / 左键或 E 破坏准星指着的方块 / 右键或 Q 放置当前选中的方块
  / 三只红皮怪会追你并扣血 / ESC 退出。

节点图覆盖:
  * 开始界面:『界面_面板』『界面_文本』『界面_按钮』『界面_输入框』
    『界面_隐藏控件』『界面_暂停游戏』+ 按钮点击链;
  * 第一人称:『相机_旋转』+『移动_按方向(随相机朝向)』;
  * 射线交互:『射线_检测』+『实例_销毁』;
  * 四档方块:侧栏变量『方块序号』+『多路分支』+ 四个『资产_创建实例』;
  * 怪物:『移动_朝向目标』+『流程_冷却』+『变量_增减』;
  * HUD:『界面_文本』+『界面_进度条』+『界面_飘字』。
"""

import math
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for path in (ROOT, HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

OUT_DIR = os.path.join(ROOT, "examples", "demo_minecraft")
BLEND_PATH = os.path.join(OUT_DIR, "我的世界.blend")
GODOT_DIR = os.path.join(OUT_DIR, "我的世界_godot")

SCENE_NAME = "我的世界"
TREE_NAME = "我的世界_逻辑"
PLAYER_NAME = "Player"
CAMERA_NAME = "第一人称相机"

# 10x10 个 1 米方块铺地面;再加沙地 / 石台 / 树 / 门框
GROUND = 10
BLOCK_COLORS = {
    "grass": (0.24, 0.60, 0.22),
    "dirt": (0.42, 0.30, 0.18),
    "stone": (0.46, 0.48, 0.52),
    "wood": (0.42, 0.27, 0.12),
    "leaves": (0.16, 0.52, 0.20),
    "sand": (0.80, 0.72, 0.46),
    "monster": (0.58, 0.12, 0.12),
    "player": (0.25, 0.45, 0.85),
}

# 手上能拿的四档:显示名 / 颜色。数字键 1~4 切的就是这个列表,
# 『多路分支』的出口 0~3 也按这个顺序接。
BLOCK_KINDS = (
    ("草方块", (0.24, 0.60, 0.22)),
    ("石头方块", (0.46, 0.48, 0.52)),
    ("沙子方块", (0.80, 0.72, 0.46)),
    ("木头方块", (0.42, 0.27, 0.12)),
)

# 会追人的小怪(名字 / Godot 坐标里的出生点)
MONSTERS = (
    ("怪物1", (2.0, 1.0, -3.0)),
    ("怪物2", (4.0, 1.0, -5.0)),
    ("怪物3", (-1.5, 1.0, -4.0)),
)
MONSTER_SPEED = 2.6
MONSTER_REACH = 1.3
PLAYER_SPAWN = (0.5, 1.0, 0.5)


def log(message):
    print(f"[我的世界] {message}")


def godot_to_blender(x, y, z):
    """Godot(x, y, z) -> Blender(x, z, -y)。"""
    return (x, -z, y)


# ==========================================================================
# 场景
# ==========================================================================
def clear_all():
    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)
    for group in list(bpy.data.node_groups):
        if getattr(group, "bl_idname", "").startswith("GK"):
            bpy.data.node_groups.remove(group)
    scene.name = SCENE_NAME
    return scene


def make_material(name, color):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    principled = material.node_tree.nodes.get("Principled BSDF")
    if principled is not None:
        principled.inputs["Base Color"].default_value = (*color, 1.0)
        principled.inputs["Roughness"].default_value = 0.9
    material.diffuse_color = (*color, 1.0)
    return material


def make_box_mesh(name, size, material, z0=0.0):
    """以 (0,0,z0) 为下边的一只盒子网格;原点在 z0 那一层。"""
    sx, sy, sz = size
    hx, hy = sx * 0.5, sy * 0.5
    verts = [(-hx, -hy, z0), (hx, -hy, z0), (hx, hy, z0), (-hx, hy, z0),
             (-hx, -hy, z0 + sz), (hx, -hy, z0 + sz),
             (hx, hy, z0 + sz), (-hx, hy, z0 + sz)]
    faces = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
             (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    if material is not None:
        mesh.materials.append(material)
    return mesh


def add_object(scene, name, mesh, location, parent=None):
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    obj.location = location
    if parent is not None:
        obj.parent = parent
    return obj


def add_empty(scene, name, location=(0.0, 0.0, 0.0)):
    obj = bpy.data.objects.new(name, None)
    scene.collection.objects.link(obj)
    obj.location = location
    obj.empty_display_type = "PLAIN_AXES"
    return obj


def _physics(obj):
    return getattr(obj, "gk_physics", None)


def set_physics(obj, body_type, *, script_target=False, tree="", asset="",
                layer="", mask=""):
    physics = _physics(obj)
    if physics is None:
        return
    try:
        physics.enabled = True
        physics.dimension = "3"
        physics.body_type = body_type
        physics.script_target = bool(script_target)
        if tree:
            physics.logic_tree = tree
        if asset:
            physics.asset_name = asset
        if layer:
            physics.collision_layer = layer
        if mask:
            physics.collision_mask = mask
    except Exception as exc:                                # noqa: BLE001
        log(f"物理设置 {obj.name} 失败: {exc}")


def build_world(scene):
    materials = {name: make_material(f"方块_{name}", color)
                 for name, color in BLOCK_COLORS.items()}
    block_mesh = make_box_mesh("方块网格", (1.0, 1.0, 1.0),
                               materials["grass"], z0=-0.5)

    blocks_root = add_empty(scene, "Blocks")
    placements = []

    def block(name, gx, gy, gz, material):
        # 每种颜色一份网格(材质挂在网格上,同色方块共用一个)
        mesh = block_mesh
        if material != "grass":
            mesh = bpy.data.meshes.get(f"方块网格_{material}")
            if mesh is None:
                mesh = make_box_mesh(f"方块网格_{material}",
                                     (1.0, 1.0, 1.0),
                                     materials[material], z0=-0.5)
        placements.append((name, godot_to_blender(gx, gy, gz), mesh))

    half = GROUND // 2
    for gx in range(-half, half):
        for gz in range(-half, half):
            block(f"Ground_{gx}_{gz}", gx + 0.5, 0.5, gz + 0.5, "grass")
    for gx in range(1, 4):
        for gz in range(-4, -1):
            block(f"Sand_{gx}_{gz}", gx + 0.5, 0.5, gz + 0.5, "sand")
    for gx in range(2, 5):
        for gz in range(2, 5):
            block(f"Stone_{gx}_{gz}", gx + 0.5, 1.5, gz + 0.5, "stone")
    for index in range(3):
        block(f"TreeTrunk_{index}", -3.5, 1.5 + index, -3.5, "wood")
    for dx in (-1, 0, 1):
        for dz in (-1, 0, 1):
            block(f"LeafA_{dx}_{dz}", -3.5 + dx, 4.5, -3.5 + dz, "leaves")
    for dx in (-1, 0, 1):
        for dz in (-1, 0, 1):
            if dx == 0 and dz == 0:
                continue
            block(f"LeafB_{dx}_{dz}", -3.5 + dx, 5.5, -3.5 + dz, "leaves")
    block("Pillar", 4.5, 1.5, -2.5, "stone")
    block("Pillar2", 4.5, 2.5, -2.5, "stone")
    block("ArchL", -1.5, 1.5, 4.5, "wood")
    block("ArchR", 0.5, 1.5, 4.5, "wood")
    block("ArchTop", -0.5, 2.5, 4.5, "wood")

    for name, location, mesh in placements:
        obj = add_object(scene, name, mesh, location, parent=blocks_root)
        set_physics(obj, "StaticBody3D", layer="1", mask="1")

    # 玩家:1.7 米高,原点在脚下
    player_mesh = make_box_mesh("玩家网格", (0.7, 1.7, 0.7),
                                materials["player"], z0=0.0)
    player = add_object(scene, PLAYER_NAME, player_mesh,
                        godot_to_blender(*PLAYER_SPAWN))
    set_physics(player, "CharacterBody3D", script_target=True,
                tree=TREE_NAME, layer="2", mask="1")

    camera_data = bpy.data.cameras.new(f"{CAMERA_NAME}数据")
    camera_data.angle = math.radians(75.0)
    camera_data.clip_start = 0.05
    camera_data.clip_end = 200.0
    camera = bpy.data.objects.new(CAMERA_NAME, camera_data)
    scene.collection.objects.link(camera)
    camera.parent = player
    camera.location = (0.0, 0.0, 1.6)
    scene.camera = camera

    # 三只小怪:1.4 米高,原点在脚下
    monster_mesh = make_box_mesh("怪物网格", (0.7, 1.4, 0.7),
                                 materials["monster"], z0=0.0)
    monsters_root = add_empty(scene, "Monsters")
    for name, position in MONSTERS:
        monster = add_object(scene, name, monster_mesh,
                             godot_to_blender(*position),
                             parent=monsters_root)
        set_physics(monster, "CharacterBody3D", layer="4", mask="1")

    # 四档可放置方块:本体摆在远处当"资产源"(不进玩法区域)
    asset_root = add_empty(scene, "资产源", godot_to_blender(0.0, -40.0, 0.0))
    for index, (label, color) in enumerate(BLOCK_KINDS):
        mesh = make_box_mesh(f"可放置_{label}网格", (1.0, 1.0, 1.0),
                             make_material(f"可放置_{label}", color), z0=0.0)
        obj = add_object(scene, f"{label}_源", mesh,
                         (index * 2.0, 0.0, 0.0), parent=asset_root)
        set_physics(obj, "StaticBody3D", asset=label, layer="1", mask="1")

    # 太阳
    sun_data = bpy.data.lights.new("太阳光数据", type="SUN")
    sun_data.energy = 1.2
    sun = bpy.data.objects.new("Sun", sun_data)
    scene.collection.objects.link(sun)
    sun.rotation_euler = tuple(math.radians(value) for value in (48.0, 12.0, 28.0))

    log(f"世界:{len(placements)} 块方块 + 玩家 + 相机 + {len(MONSTERS)} 只小怪"
        f" + {len(BLOCK_KINDS)} 档可放置方块")


def apply_scene_settings(scene):
    settings = getattr(scene, "gk_settings", None)
    if settings is None:
        return
    pairs = (
        ("dimension", "3"),
        ("base_class", "CharacterBody3D"),
        ("class_name", "MinecraftPlayer"),
        ("logic_tree", TREE_NAME),
        ("god_camera", False),
        ("env_enabled", True),
        ("env_background", "SKY"),
        ("env_bg_color", (0.55, 0.72, 0.95, 1.0)),
        ("env_ambient_color", (0.55, 0.62, 0.75, 1.0)),
        ("env_ambient_energy", 0.55),
        ("env_tonemap", "LINEAR"),
    )
    for attr, value in pairs:
        if not hasattr(settings, attr):
            continue
        try:
            setattr(settings, attr, value)
        except Exception:                                   # noqa: BLE001
            pass


# ==========================================================================
# 节点图
# ==========================================================================
def _add(tree, kind, name, x, y, **props):
    node = tree.nodes.new(kind)
    node.name = name
    node.location = (x, y)
    for key, value in props.items():
        setattr(node, key, value)
    builder = getattr(node, "build_sockets", None)
    if callable(builder):
        builder()
    return node


def _link(tree, source, source_pin, target, target_pin):
    tree.links.new(source.outputs[source_pin], target.inputs[target_pin])


def build_ui(tree):
    """开始界面:标题 + 三个按钮 + 『创建世界』子面板。返回各控件节点。"""
    panel = _add(tree, "GKNodeUiPanel", "世界菜单", -1450.0, 900.0,
                 gk_width=520.0, gk_height=420.0, gk_radius=18,
                 gk_align="CENTER", gk_bottom=True, gk_block_click=True,
                 gk_color=(0.05, 0.06, 0.09, 0.86))
    title = _add(tree, "GKNodeUiText", "菜单标题", -1200.0, 900.0,
                 gk_text="我的世界", gk_size=52,
                 gk_color=(0.55, 1.0, 0.75, 1.0),
                 gk_x=60.0, gk_y=40.0, gk_parent_widget="世界菜单")
    start = _add(tree, "GKNodeUiButton", "开始游戏", -950.0, 900.0,
                 gk_text="开始游戏", gk_width=400.0, gk_height=54.0,
                 gk_x=60.0, gk_y=150.0, gk_parent_widget="世界菜单")
    create = _add(tree, "GKNodeUiButton", "创建游戏", -700.0, 900.0,
                  gk_text="创建游戏", gk_width=400.0, gk_height=54.0,
                  gk_x=60.0, gk_y=220.0, gk_parent_widget="世界菜单")
    quit_button = _add(tree, "GKNodeUiButton", "退出游戏", -450.0, 900.0,
                       gk_text="退出游戏", gk_width=400.0, gk_height=54.0,
                       gk_x=60.0, gk_y=290.0, gk_parent_widget="世界菜单")
    create_panel = _add(tree, "GKNodeUiPanel", "创建世界面板", -200.0, 900.0,
                        gk_width=460.0, gk_height=280.0, gk_radius=16,
                        gk_align="CENTER", gk_bottom=True,
                        gk_block_click=True, gk_start_hidden=True,
                        gk_color=(0.05, 0.06, 0.09, 0.92))
    create_title = _add(tree, "GKNodeUiText", "创建标题", 50.0, 900.0,
                        gk_text="创建新的世界", gk_size=28,
                        gk_x=30.0, gk_y=24.0,
                        gk_parent_widget="创建世界面板")
    name_input = _add(tree, "GKNodeUiInput", "世界名", 300.0, 900.0,
                      gk_text="新的世界", gk_placeholder="世界名称",
                      gk_width=400.0, gk_height=44.0,
                      gk_x=30.0, gk_y=86.0,
                      gk_parent_widget="创建世界面板")
    make_button = _add(tree, "GKNodeUiButton", "创建新的世界", 550.0, 900.0,
                       gk_text="创建新的世界", gk_width=190.0,
                       gk_height=48.0, gk_x=30.0, gk_y=170.0,
                       gk_parent_widget="创建世界面板")
    cancel = _add(tree, "GKNodeUiButton", "取消创建", 800.0, 900.0,
                  gk_text="取消", gk_width=190.0, gk_height=48.0,
                  gk_x=240.0, gk_y=170.0,
                  gk_parent_widget="创建世界面板")
    return {
        "panel": panel, "title": title, "start": start, "create": create,
        "quit": quit_button, "create_panel": create_panel,
        "create_title": create_title, "input": name_input,
        "make": make_button, "cancel": cancel,
    }


def build_tree(scene):
    tree = bpy.data.node_groups.new(TREE_NAME, "GKGameLogicTree")
    tree.gk_scope = "ROLE"
    tree.use_fake_user = True

    held = tree.gk_vars.add()
    held.var_name = "方块序号"
    held.var_type = "float"
    held.default_value = "0"
    held.comment = "0 草 / 1 石 / 2 沙 / 3 木,数字键 1~4 切"

    health = tree.gk_vars.add()
    health.var_name = "生命"
    health.var_type = "float"
    health.default_value = "100"
    health.comment = "怪物每口 -8;0 = 送回出生点"

    ui = build_ui(tree)

    # ---- _ready:开始界面 ----
    ready = _add(tree, "GKNodeOnReady", "开始", -2200.0, 900.0)
    mouse_mode = _add(tree, "GKNodeMouseMode", "菜单里放出鼠标", -1950.0, 900.0,
                      gk_mode="VISIBLE")
    hint = _add(tree, "GKNodePrint", "操作说明", -1700.0, 900.0,
                gk_text=("我的世界示例:点『开始游戏』进世界;WASD 移动 / "
                         "空格 跳 / 鼠标 转视角 / 1~4 选方块 / "
                         "左键或 E 挖 / 右键或 Q 放"))
    pause_menu = _add(tree, "GKNodeUiPauseGame", "菜单期间暂停世界",
                      1050.0, 900.0, gk_pause=True, gk_show_mouse=True,
                      gk_menu="世界菜单", gk_hotkey=False)
    _link(tree, ready, "执行", mouse_mode, "执行")
    _link(tree, mouse_mode, "完成", hint, "执行")
    _link(tree, hint, "完成", ui["panel"], "执行")
    _link(tree, ui["panel"], "完成", ui["title"], "执行")
    _link(tree, ui["title"], "完成", ui["start"], "执行")
    _link(tree, ui["start"], "完成", ui["create"], "执行")
    _link(tree, ui["create"], "完成", ui["quit"], "执行")
    _link(tree, ui["quit"], "完成", ui["create_panel"], "执行")
    _link(tree, ui["create_panel"], "完成", ui["create_title"], "执行")
    _link(tree, ui["create_title"], "完成", ui["input"], "执行")
    _link(tree, ui["input"], "完成", ui["make"], "执行")
    _link(tree, ui["make"], "完成", ui["cancel"], "执行")
    _link(tree, ui["cancel"], "完成", pause_menu, "执行")

    resume = _add(tree, "GKNodeUiPauseGame", "开始游戏", -450.0, 1150.0,
                  gk_pause=False, gk_show_mouse=True,
                  gk_menu="世界菜单", gk_hotkey=False)
    lock_mouse = _add(tree, "GKNodeMouseMode", "锁回鼠标", -200.0, 1150.0,
                      gk_mode="CAPTURED")
    _link(tree, ui["start"], "点击", resume, "执行")
    _link(tree, resume, "完成", lock_mouse, "执行")

    open_create = _add(tree, "GKNodeUiHide", "弹创建面板", -450.0, 1350.0,
                       gk_widget="创建世界面板", gk_visible=True, gk_raise=True)
    hide_menu = _add(tree, "GKNodeUiHide", "收起主菜单", -200.0, 1350.0,
                     gk_widget="世界菜单", gk_visible=False)
    _link(tree, ui["create"], "点击", open_create, "执行")
    _link(tree, open_create, "完成", hide_menu, "执行")

    close_create = _add(tree, "GKNodeUiHide", "收起创建面板", -450.0, 1550.0,
                        gk_widget="创建世界面板", gk_visible=False)
    show_menu = _add(tree, "GKNodeUiHide", "放回主菜单", -200.0, 1550.0,
                     gk_widget="世界菜单", gk_visible=True, gk_raise=True)
    _link(tree, ui["cancel"], "点击", close_create, "执行")
    _link(tree, close_create, "完成", show_menu, "执行")

    world_name = _add(tree, "GKNodeUiReadText", "读世界名", -1350.0, 1750.0,
                      gk_widget="世界名")
    world_join = _add(tree, "GKNodeTextJoin", "创建提示", -1100.0, 1750.0,
                      gk_a="正在创建世界:")
    world_print = _add(tree, "GKNodePrint", "打印世界名", -850.0, 1750.0)
    hide_create2 = _add(tree, "GKNodeUiHide", "收起创建面板2", -600.0, 1750.0,
                        gk_widget="创建世界面板", gk_visible=False)
    resume_create = _add(tree, "GKNodeUiPauseGame", "创建后开始", -350.0, 1750.0,
                         gk_pause=False, gk_show_mouse=True,
                         gk_menu="世界菜单", gk_hotkey=False)
    lock_mouse2 = _add(tree, "GKNodeMouseMode", "锁回鼠标2", -100.0, 1750.0,
                       gk_mode="CAPTURED")
    _link(tree, ui["make"], "点击", world_print, "执行")
    _link(tree, world_name, "文字", world_join, "乙")
    _link(tree, world_join, "结果", world_print, "内容")
    _link(tree, world_print, "完成", hide_create2, "执行")
    _link(tree, hide_create2, "完成", resume_create, "执行")
    _link(tree, resume_create, "完成", lock_mouse2, "执行")

    quit_node = _add(tree, "GKNodeQuitGame", "退出", 50.0, 1150.0)
    _link(tree, ui["quit"], "点击", quit_node, "执行")

    # ---- _process:序列(转视角 / 挖 / 放 / 选 1~4 / ESC) ----
    process = _add(tree, "GKNodeOnProcess", "每帧", -1600.0, 200.0)
    sequence = _add(tree, "GKNodeSequence", "每帧八件事", -1300.0, 200.0,
                    gk_step_count=8)
    _link(tree, process, "执行", sequence, "执行")

    mouse = _add(tree, "GKNodeMouseMotion", "鼠标移动", -1600.0, -60.0)
    split = _add(tree, "GKNodeVectorSplit", "拆 XY", -1350.0, -60.0)
    yaw = _add(tree, "GKNodeMath", "左右灵敏度", -1100.0, -60.0,
               gk_op="MUL", gk_b=0.12)
    pitch = _add(tree, "GKNodeMath", "上下灵敏度", -1100.0, -240.0,
                 gk_op="MUL", gk_b=0.12)
    camera = _add(tree, "GKNodeCameraRotation", "第一人称转视角", -850.0, -120.0,
                  gk_target=CAMERA_NAME, gk_mode="ADD",
                  gk_smooth=0.0, gk_clamp_pitch=True)
    _link(tree, sequence, "1", camera, "执行")
    _link(tree, mouse, "移动量", split, "向量")
    _link(tree, split, "X", yaw, "A")
    _link(tree, split, "Y", pitch, "A")
    _link(tree, yaw, "结果", camera, "偏航")
    _link(tree, pitch, "结果", camera, "俯仰")

    ray = _add(tree, "GKNodeRaycast", "准星射线", -1100.0, -520.0,
               gk_origin="CAMERA", gk_dir_mode="CAMERA_FORWARD",
               gk_distance=6.0, gk_layer=1)
    hit_seq = _add(tree, "GKNodeSequence", "命中后两件事", -850.0, -560.0,
                   gk_step_count=2)
    left = _add(tree, "GKNodeMouseButtonDown", "左键刚按下", -1100.0, -700.0,
                gk_button="LEFT")
    mine_key = _add(tree, "GKNodeKey", "E 也能挖", -1100.0, -820.0,
                    gk_key="E", gk_action="mine", gk_trigger="JUST")
    mine_or = _add(tree, "GKNodeLogic", "左键或 E", -850.0, -760.0, gk_op="OR")
    hit_branch = _add(tree, "GKNodeBranch", "打中了吗", -850.0, -560.0)
    destroy = _add(tree, "GKNodeQueueFree", "挖掉方块", -600.0, -520.0)
    _link(tree, sequence, "2", ray, "执行")
    _link(tree, ray, "命中", hit_seq, "执行")
    _link(tree, hit_seq, "1", hit_branch, "执行")
    _link(tree, left, "刚按下", mine_or, "A")
    _link(tree, mine_key, "结果", mine_or, "B")
    _link(tree, mine_or, "结果", hit_branch, "条件")
    _link(tree, hit_branch, "是", destroy, "执行")
    _link(tree, ray, "打到的东西", destroy, "目标")

    right = _add(tree, "GKNodeMouseButtonDown", "右键刚按下", -1100.0, -1000.0,
                 gk_button="RIGHT")
    place_key = _add(tree, "GKNodeKey", "Q 也能放", -1100.0, -1120.0,
                     gk_key="Q", gk_action="place", gk_trigger="JUST")
    place_or = _add(tree, "GKNodeLogic", "右键或 Q", -850.0, -1060.0, gk_op="OR")
    place_branch = _add(tree, "GKNodeBranch", "要放吗", -620.0, -1000.0)
    held_read = _add(tree, "GKNodeGetVar", "手上是哪档", -620.0, -820.0,
                     gk_var_name="方块序号")
    block_switch = _add(tree, "GKNodeSwitch", "放哪种", -380.0, -1000.0,
                        gk_case_count=len(BLOCK_KINDS))
    split_hit = _add(tree, "GKNodeVectorSplit", "拆命中点", -1100.0, -1300.0)
    lift = _add(tree, "GKNodeMath", "抬半格", -850.0, -1300.0,
                gk_op="ADD", gk_b=0.5)
    combine = _add(tree, "GKNodeVectorMake", "合成位置", -620.0, -1300.0)
    _link(tree, hit_seq, "2", place_branch, "执行")
    _link(tree, right, "刚按下", place_or, "A")
    _link(tree, place_key, "结果", place_or, "B")
    _link(tree, place_or, "结果", place_branch, "条件")
    _link(tree, place_branch, "是", block_switch, "执行")
    _link(tree, held_read, "值", block_switch, "值")
    _link(tree, ray, "命中点", split_hit, "向量")
    _link(tree, split_hit, "X", combine, "X")
    _link(tree, split_hit, "Z", combine, "Z")
    _link(tree, split_hit, "Y", lift, "A")
    _link(tree, lift, "结果", combine, "Y")
    for index, (label, _color) in enumerate(BLOCK_KINDS):
        spawn = _add(tree, "GKNodeAssetSpawn", "放" + label, -120.0,
                     -1000.0 - index * 180.0, gk_asset=label, gk_solid=True)
        _link(tree, block_switch, str(index), spawn, "执行")
        _link(tree, combine, "向量", spawn, "位置")

    for index, (label, _color) in enumerate(BLOCK_KINDS):
        key = _add(tree, "GKNodeKey", f"{index + 1} 选{label}", -1100.0,
                   -1500.0 - index * 200.0, gk_key=str(index + 1),
                   gk_action=f"key_{index + 1}", gk_trigger="JUST")
        branch = _add(tree, "GKNodeBranch", f"按了{index + 1}?", -850.0,
                      -1500.0 - index * 200.0)
        setter = _add(tree, "GKNodeSetVar", f"记下{label}", -600.0,
                      -1500.0 - index * 200.0,
                      gk_var_name="方块序号", gk_value=float(index))
        _link(tree, sequence, str(index + 4), branch, "执行")
        _link(tree, key, "结果", branch, "条件")
        _link(tree, branch, "是", setter, "执行")

    esc = _add(tree, "GKNodeKey", "ESC", -1100.0, -900.0,
               gk_key="ESC", gk_action="ui_cancel", gk_trigger="JUST")
    esc_branch = _add(tree, "GKNodeBranch", "要退出吗", -850.0, -900.0)
    quit_game = _add(tree, "GKNodeQuitGame", "退出游戏", -600.0, -900.0)
    _link(tree, sequence, "8", esc_branch, "执行")
    _link(tree, esc, "结果", esc_branch, "条件")
    _link(tree, esc_branch, "是", quit_game, "执行")

    # ---- _physics_process:移动 + 怪物追击 + HUD + 跳跃 ----
    physics = _add(tree, "GKNodeOnPhysicsProcess", "物理帧", -1600.0, -1200.0)
    direction = _add(tree, "GKNodeInputDirection", "WASD", -1600.0, -1450.0)
    move = _add(tree, "GKNodeMoveDirection", "移动", -1300.0, -1200.0,
                gk_speed=6.0, gk_keep_y=True, gk_follow_camera=True,
                gk_face_move=False)
    on_floor = _add(tree, "GKNodeIsOnFloor", "在地面", -1300.0, -1500.0)
    jump_key = _add(tree, "GKNodeKey", "空格", -1300.0, -1680.0,
                    gk_action="jump", gk_trigger="JUST")
    both = _add(tree, "GKNodeLogic", "能跳吗", -1050.0, -1550.0, gk_op="AND")
    _link(tree, physics, "执行", move, "执行")
    _link(tree, direction, "方向", move, "方向")

    # 导出的相机是场景根节点(不挂在角色下面),用『相机_跟随』让它贴着玩家走;
    # 距离留 0 = 保持摆好的相对位置(眼高 1.6 米)。
    follow = _add(tree, "GKNodeCameraFollow", "相机跟着玩家", -1050.0, -1800.0,
                  gk_target="__self__", gk_smooth=0.0, gk_distance=0.0)

    chases = []
    previous = follow
    _link(tree, move, "完成", follow, "执行")
    for index, (name, _position) in enumerate(MONSTERS):
        chase = _add(tree, "GKNodeMoveToward", "追" + name, -1050.0,
                     -1200.0 + index * 220.0,
                     gk_target=name, gk_goal="__self__",
                     gk_speed=MONSTER_SPEED, gk_stop_distance=MONSTER_REACH,
                     gk_turn=True, gk_accel=6.0)
        _link(tree, previous, "完成", chase, "执行")
        chases.append(chase)
        previous = chase

    health_read = _add(tree, "GKNodeGetVar", "读生命", -1500.0, -1900.0,
                       gk_var_name="生命")
    health_num = _add(tree, "GKNodeNumberText", "生命数字", -1300.0, -1900.0)
    health_join = _add(tree, "GKNodeTextJoin", "生命文字", -1100.0, -1900.0,
                       gk_a="生命 ")
    hp_text = _add(tree, "GKNodeUiText", "生命文本", -850.0, -1850.0,
                   gk_size=20, gk_align="TOP_LEFT", gk_x=20.0, gk_y=44.0)
    hp_bar = _add(tree, "GKNodeUiBar", "生命条", -560.0, -1850.0,
                  gk_max=100.0, gk_x=20.0, gk_y=76.0,
                  gk_width=220.0, gk_height=16.0,
                  gk_color=(0.85, 0.22, 0.22, 1.0))
    _link(tree, health_read, "值", health_num, "数值")
    _link(tree, health_read, "值", hp_bar, "数值")
    _link(tree, health_num, "文字", health_join, "乙")
    _link(tree, health_join, "结果", hp_text, "文字")
    _link(tree, previous, "完成", hp_text, "执行")
    _link(tree, hp_text, "完成", hp_bar, "执行")

    reach_or = _add(tree, "GKNodeLogic", "有怪物贴上来吗", -1050.0, -2100.0,
                    gk_op="OR", gk_input_count=len(MONSTERS))
    for index, chase in enumerate(chases):
        _link(tree, chase, "到达", reach_or, chr(ord("A") + index))
    cooldown = _add(tree, "GKNodeCooldown", "每口间隔", -800.0, -2100.0,
                    gk_seconds=1.0)
    hurt_float = _add(tree, "GKNodeFloatText", "伤害飘字", -560.0, -2100.0,
                      gk_target="__self__", gk_prefix="生命 -",
                      gk_value=8.0, gk_time=0.7, gk_rise=1.0,
                      gk_color=(1.0, 0.35, 0.35, 1.0))
    hurt = _add(tree, "GKNodeAddVar", "扣血", -320.0, -2100.0,
                gk_var_name="生命", gk_amount=-8.0)
    dead = _add(tree, "GKNodeCompare", "血空了吗", -80.0, -2150.0,
                gk_op="LE", gk_b="0")
    dead_branch = _add(tree, "GKNodeBranch", "倒下了吗", 160.0, -2100.0)
    revive = _add(tree, "GKNodeSetVar", "补满血", 400.0, -2100.0,
                  gk_var_name="生命", gk_value=100.0)
    back = _add(tree, "GKNodeSetPosition", "送回出生点", 640.0, -2100.0,
                gk_target="__self__", gk_x=PLAYER_SPAWN[0],
                gk_y=PLAYER_SPAWN[1], gk_z=PLAYER_SPAWN[2])
    note = _add(tree, "GKNodePrint", "提示", 880.0, -2100.0,
                gk_text="你被怪物击倒,已回到出生点")
    _link(tree, cooldown, "可以", hurt_float, "执行")
    _link(tree, hurt_float, "完成", hurt, "执行")
    _link(tree, hurt, "完成", dead_branch, "执行")
    _link(tree, hurt, "新值", dead, "A")
    _link(tree, dead, "结果", dead_branch, "条件")
    _link(tree, dead_branch, "是", revive, "执行")
    _link(tree, revive, "完成", back, "执行")
    _link(tree, back, "完成", note, "执行")

    jump_branch = _add(tree, "GKNodeBranch", "如果", -800.0, -1200.0)
    jump = _add(tree, "GKNodeJump", "跳跃", -550.0, -1200.0, gk_jump_force=8.5)
    damage_branch = _add(tree, "GKNodeBranch", "被贴上了吗", -560.0, -1000.0)
    actions = _add(tree, "GKNodeSequence", "每物理帧两件事", -800.0, -1000.0,
                   gk_step_count=2)
    _link(tree, hp_bar, "完成", actions, "执行")
    _link(tree, actions, "1", damage_branch, "执行")
    _link(tree, actions, "2", jump_branch, "执行")
    _link(tree, reach_or, "结果", damage_branch, "条件")
    _link(tree, damage_branch, "是", cooldown, "执行")
    _link(tree, on_floor, "在地面", both, "A")
    _link(tree, jump_key, "结果", both, "B")
    _link(tree, both, "结果", jump_branch, "条件")
    _link(tree, jump_branch, "是", jump, "执行")

    log(f"节点图:{len(tree.nodes)} 个节点 / {len(tree.links)} 条连线")
    return tree


# ==========================================================================
# 导出
# ==========================================================================
def save_blend(path):
    if not bpy.app.background:
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return
    blocks = set(bpy.data.objects) | set(bpy.data.meshes) | \
        set(bpy.data.materials) | set(bpy.data.node_groups) | \
        set(bpy.data.cameras) | set(bpy.data.lights) | \
        set(bpy.data.curves) | set(bpy.data.texts)
    scenes = list(bpy.data.scenes)
    bpy.data.libraries.write(path, blocks | set(scenes), fake_user=True)


def main():
    from BlenderGameKit import asset_export, project, register

    if bpy.types.NodeTree.bl_rna_get_subclass_py("GKUiDesignTree", None) is None:
        register()

    os.makedirs(OUT_DIR, exist_ok=True)
    scene = clear_all()
    build_world(scene)
    build_tree(scene)
    apply_scene_settings(scene)

    project.create_project(GODOT_DIR, title="我的世界",
                           blend_file_name=os.path.basename(BLEND_PATH))
    result = asset_export.export_all_scenes(GODOT_DIR, dimension="3")
    if result.get("error"):
        log(f"导出失败:{result['error']}")
        return 1
    for entry in result.get("scenes", []):
        log(f"场景 {entry['scene']} -> {entry['scene_name']}.tscn"
            f"(脚本 {entry['script_name']})")
    for warning in result.get("warnings", []):
        log(f"告警: {warning}")
    for entry in result.get("scenes", []):
        if entry.get("ok"):
            main_scene = f"res://scenes/{entry['scene_name']}.tscn"
            project.set_main_scene(GODOT_DIR, main_scene)
            log(f"主场景: {main_scene}")
            break

    save_blend(BLEND_PATH)
    log(f"已保存 {BLEND_PATH}")
    log(f"工程目录: {GODOT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
