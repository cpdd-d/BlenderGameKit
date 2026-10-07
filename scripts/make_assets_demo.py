"""资产示例(第四十三轮:游戏资产)—— "资产库"怎么用起来的最小例子。

用法:
    blender -b --gpu-backend vulkan --python scripts\\make_assets_demo.py
产出:
    examples/demo_assets/资产示例.blend
    examples/demo_assets/资产示例_godot/      (可用 Godot 直接打开)
    examples/demo_assets/叮.wav              (程序生成的小音效,给声音卡用)

── 这一屏在看什么 ────────────────────────────────────────────────
第一关里有三棵树和一块石头 —— 它们**不是**普通物体,是"资产":
同一个模型只导一次,摆几份就复用几次(每份各自带碰撞)。
打开『游戏资产』图能看到资产库:
    资产_模型卡(树 / 石头)、资产_图片卡(资产徽标)、
    资产_曲线卡(资产轨道)、资产_声音卡(叮)
卡上写着"本场景几份、本体在哪个场景、谁在用"。

| 资产 | 在示例里怎么用 |
|---|---|
| 3D 道具『树』×3 份 / 『石头』 | 第一关;第二关的树是从**资产库跨场景**摆过去的 |
| 图片『资产徽标』 | 开场显示在左上角(『界面_图片』) |
| 曲线『资产轨道』 | 相机沿它自动前进(『相机_路径』) |
| 音频『叮』 | 开场播一次(『音效_播放』,路径由资产库写的) |
| 『资产』集合 | 大纲视图里树 / 石头和地面分开摆(两个场景各一个) |

按键(第一关):WASD 移动 / 空格 跳 / 鼠标转视角 / C·V 切机位(角色预设)。
跑法(Godot):打开 `资产示例_godot` 直接 F5(主场景 = 第一关)。
自动检查(不用 Blender,要 Godot):`python scripts\\check_assets_demo.py`
"""

import math
import os
import struct
import sys
import wave

import bmesh
import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "examples", "demo_assets")
BLEND_PATH = os.path.join(OUT_DIR, "资产示例.blend")
WAV_PATH = os.path.join(OUT_DIR, "叮.wav")
PROJECT_DIR = os.path.join(OUT_DIR, "资产示例_godot")

SCENE_ONE = "资产第一关"
SCENE_TWO = "资产第二关"
PLAYER = "玩家"
LOGO_NAME = "资产徽标"
CURVE_NAME = "资产轨道"

TREE_COLOR = (0.22, 0.55, 0.28, 1.0)
ROCK_COLOR = (0.55, 0.55, 0.60, 1.0)


def log(message):
    print(f"[资产示例] {message}")


def clear_scene():
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for group in list(bpy.data.node_groups):
        if getattr(group, "bl_idname", "").startswith("GK"):
            bpy.data.node_groups.remove(group)
    scene.name = SCENE_ONE
    return scene


def make_box(name, size, location, color=None, scene=None):
    """一个立方体(尺寸**烘进网格** —— 不依赖当前场景 / 算子)。"""
    scene = scene or bpy.context.scene
    mesh = bpy.data.meshes.new(f"{name}网格")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    obj.location = location
    if color is not None:
        material = bpy.data.materials.new(f"{name}_材质")
        material.use_nodes = True
        principled = material.node_tree.nodes.get("Principled BSDF")
        if principled is not None:
            principled.inputs["Base Color"].default_value = color
        mesh.materials.append(material)
    return obj


def make_camera(name, location, rotation, scene=None):
    scene = scene or bpy.context.scene
    data = bpy.data.cameras.new(f"{name}数据")
    camera = bpy.data.objects.new(name, data)
    scene.collection.objects.link(camera)
    camera.location = location
    camera.rotation_euler = rotation
    return camera


def make_curve(name, points, scene=None):
    """一条贝塞尔曲线(控制点 + 自动手柄)—— 给『相机_路径』当轨道。"""
    scene = scene or bpy.context.scene
    data = bpy.data.curves.new(f"{name}数据", type="CURVE")
    data.dimensions = "3D"
    spline = data.splines.new("BEZIER")
    spline.bezier_points.add(len(points) - 1)
    for handle, co in zip(spline.bezier_points, points):
        handle.co = co
        handle.handle_left_type = "AUTO"
        handle.handle_right_type = "AUTO"
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    return obj


def make_logo_image():
    """程序生成一张 64×64 的徽标(不依赖磁盘上的图,和界面示例同一套做法)。"""
    image = bpy.data.images.get(LOGO_NAME)
    if image is not None:
        bpy.data.images.remove(image)
    size = 64
    image = bpy.data.images.new(LOGO_NAME, size, size, alpha=True)
    pixels = [0.0] * (size * size * 4)
    center = (size - 1) / 2.0
    for y in range(size):
        for x in range(size):
            dx = (x - center) / center
            dy = (y - center) / center
            distance = (dx * dx + dy * dy) ** 0.5
            index = (y * size + x) * 4
            if distance < 0.62:
                pixels[index] = 0.20
                pixels[index + 1] = 0.85
                pixels[index + 2] = 0.55
                pixels[index + 3] = 1.0
            elif distance < 0.94:
                pixels[index] = 0.10
                pixels[index + 1] = 0.12
                pixels[index + 2] = 0.18
                pixels[index + 3] = 1.0
            else:
                pixels[index + 3] = 0.0
    image.pixels = pixels
    image.pack()          # 跟着 .blend 走(不留外部依赖)
    log(f"图片资产:{LOGO_NAME} 64×64(程序生成 + 打包)")
    return image


def make_wav(path, seconds=0.35, freq=880.0, rate=22050):
    """程序生成一个小音效(wav)—— 资产库要**本地文件**才谈得上"带进 Godot"。

    和图片同一个立场:示例不依赖"你机器上恰好有某个音频"。
    880Hz + 两头淡入淡出,听起来是一声干净的"叮"。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    count = int(seconds * rate)
    frames = bytearray()
    for index in range(count):
        t = index / rate
        fade_in = min(1.0, index / (rate * 0.02))
        fade_out = min(1.0, (count - index) / (rate * 0.10))
        value = int(22000 * fade_in * fade_out * math.sin(2.0 * math.pi * freq * t))
        frames += struct.pack("<h", value)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    log(f"音频资产:{os.path.basename(path)}({seconds:g} 秒,程序生成)")
    return path


def _set_asset(obj, name, instance=False):
    physics = getattr(obj, "gk_physics", None)
    if physics is None:
        return
    physics.asset_name = name
    physics.asset_instance = instance
    physics.body_type = "StaticBody3D"
    physics.dimension = "3"
    try:
        physics.shape_size = (0.0, 0.0, 0.0)
    except Exception:                                     # noqa: BLE001
        pass


def build_level_one():
    """第一关:地面 + 玩家 + 一棵树摆三份 + 一块石头 + 相机 + 曲线轨道。"""
    scene = clear_scene()
    make_box("地面", (26.0, 26.0, 0.4), (0.0, 0.0, -0.2),
             (0.28, 0.38, 0.30, 1.0))
    player = make_box(PLAYER, (1.0, 1.0, 1.6), (0.0, -10.0, 0.8),
                      (0.95, 0.55, 0.25, 1.0))
    # ── 资产:『树』摆三份(本体 + 两个实例)、『石头』一份 ────────────
    trees = [
        make_box("树_本体", (1.2, 1.2, 3.2), (-8.0, -4.0, 1.6), TREE_COLOR),
        make_box("树_实例1", (1.2, 1.2, 3.2), (-10.6, -1.0, 1.6), TREE_COLOR),
        make_box("树_实例2", (1.2, 1.2, 3.2), (-5.4, -1.0, 1.6), TREE_COLOR),
        make_box("石头_本体", (1.8, 1.8, 1.0), (-3.0, -6.5, 0.5), ROCK_COLOR),
    ]
    _set_asset(trees[0], "树")
    _set_asset(trees[1], "树", instance=True)
    _set_asset(trees[2], "树", instance=True)
    _set_asset(trees[3], "石头")

    side = make_camera("侧视机位", (0.0, -16.0, 5.0),
                       (math.radians(74.0), 0.0, 0.0))
    make_camera("俯瞰机位", (0.0, -8.0, 18.0),
                (math.radians(20.0), 0.0, 0.0))
    # 曲线资产:相机沿着它飞一圈(『相机_路径』引用它)
    make_curve(CURVE_NAME, [(-10.0, -12.0, 3.0), (-3.0, -15.0, 5.5),
                            (3.0, -15.0, 5.5), (10.0, -12.0, 3.0)])
    track_cam = make_camera("轨道相机", (-10.0, -12.0, 3.0),
                            (math.radians(80.0), 0.0, math.radians(20.0)))
    scene.camera = side
    light_data = bpy.data.lights.new("太阳光数据", type="SUN")
    light_data.energy = 3.0
    light = bpy.data.objects.new("太阳光", light_data)
    scene.collection.objects.link(light)
    light.rotation_euler = (math.radians(50.0), 0.0, math.radians(30.0))
    log("第一关:地面 + 玩家 + 树×3(本体 + 2 实例)+ 石头 + 两台机位 + 轨道相机")
    return scene, player, trees, track_cam


def build_level_two():
    """第二关:**跨场景**从资产库摆两份树(第一份自动当本体)。"""
    scene = bpy.data.scenes.new(SCENE_TWO)
    settings = getattr(scene, "gk_settings", None)
    if settings is not None:
        settings.dimension = "3"
    make_box("地面", (26.0, 26.0, 0.4), (0.0, 0.0, -0.2),
             (0.30, 0.34, 0.40, 1.0), scene=scene)
    # 这一关自己的角色(挂第二关的脚本)+ 一台相机 —— 不摆的话导出会告警
    player2 = make_box(f"{PLAYER}二", (1.0, 1.0, 1.6), (0.0, -10.0, 0.8),
                       (0.85, 0.55, 0.35, 1.0), scene=scene)
    physics = getattr(player2, "gk_physics", None)
    if physics is not None:
        physics.body_type = "CharacterBody3D"
        physics.dimension = "3"
        physics.script_target = True
        try:
            physics.shape_size = (0.0, 0.0, 0.0)
        except Exception:                                 # noqa: BLE001
            pass
    camera = make_camera("第二关机位", (0.0, -16.0, 5.0),
                         (math.radians(74.0), 0.0, 0.0), scene=scene)
    scene.camera = camera
    light_data = bpy.data.lights.new("太阳光数据二", type="SUN")
    light_data.energy = 3.0
    light = bpy.data.objects.new("太阳光二", light_data)
    scene.collection.objects.link(light)
    light.rotation_euler = (math.radians(50.0), 0.0, math.radians(30.0))
    log("第二关:地面 + 角色 + 机位(树等会儿从资产库摆过来)")
    return scene


def build_level_two_logic(scene):
    """第二关自己的小逻辑图 + 绑定 —— 一关一棵图(多场景的正规做法)。"""
    tree = bpy.data.node_groups.new("第二关逻辑", "GKGameLogicTree")
    ready = tree.nodes.new("GKNodeOnReady")
    ready.name = "第二关准备"
    ready.location = (0.0, 0.0)
    note = tree.nodes.new("GKNodePrint")
    note.name = "打招呼"
    note.location = (320.0, 0.0)
    note.gk_text = "第二关:这几棵树是从资产库跨场景摆过来的"
    tree.links.new(ready.outputs["执行"], note.inputs["执行"])
    settings = getattr(scene, "gk_settings", None)
    if settings is not None:
        settings.logic_tree = tree.name
    log(f"第二关逻辑图『{tree.name}』:准备 -> 打印(已绑到第二关)")
    return tree


def place_trees_into(scene, count=2):
    """用资产库的『再摆一份』逻辑往 scene 里摆树(和面板上是同一个函数)。"""
    from BlenderGameKit import ui as gk_ui
    placed = []
    for _index in range(count):
        ok, message, copy = gk_ui.place_asset_into_scene(scene, "树")
        if not ok:
            log(f"摆树失败: {message}")
            continue
        placed.append(copy)
        log(message)
    return placed


def build_logic(scene, player, track_cam, wav_path):
    """玩法图:角色预设(控制)+ 界面_图片 + 音效_播放 + 相机_路径。"""
    from BlenderGameKit import presets, sound_assets

    logic, _anim, notes = presets.create_character_preset(
        scene, character=player,
        logic_name="玩家逻辑",
        class_name="AssetsDemoPlayer",
        with_animation=False,
        with_ports=False,
        camera_side="侧视机位",
        camera_top="俯瞰机位",
    )
    for note in notes:
        log(f"预设: {note}")
    ready = None
    for node in logic.nodes:
        if node.bl_idname == "GKNodeOnReady":
            ready = node
    if ready is None:
        raise RuntimeError("角色预设里没有『准备』节点")

    # 界面_图片:开场把资产徽标显示在左上角
    image = logic.nodes.new("GKNodeUiImage")
    image.name = "显示徽标"
    image.location = (0.0, -780.0)
    image.gk_image = LOGO_NAME
    image.gk_x = 24.0
    image.gk_y = 24.0
    logic.links.new(ready.outputs["执行"], image.inputs["执行"])

    # 音效_播放:路径 = 资产库里那份音频的 res://(两边同一个函数算的)
    play = logic.nodes.new("GKNodePlaySound")
    play.name = "播放叮"
    play.location = (320.0, -780.0)
    play.gk_path = sound_assets.sound_res_path(wav_path)
    logic.links.new(image.outputs["完成"], play.inputs["执行"])

    # 相机_路径:轨道相机沿『资产轨道』自动飞(曲线资产真的在用)
    path = logic.nodes.new("GKNodeCameraPath")
    path.name = "轨道运镜"
    path.location = (640.0, -780.0)
    path.gk_target = track_cam.name
    path.gk_curve = CURVE_NAME
    path.gk_speed = 0.06
    path.gk_look_mode = "FORWARD"
    path.gk_loop = True
    logic.links.new(play.outputs["完成"], path.inputs["执行"])
    log("玩家逻辑:预设 + 显示徽标 + 播放叮 + 轨道运镜(相机沿曲线自动飞)")
    return logic


def build_asset_tree(wav_path):
    """资产图:收录 + 声音卡(收录收不到"本地文件",得手动加一张)。"""
    from BlenderGameKit import sound_assets
    from BlenderGameKit import ui as gk_ui

    tree = bpy.data.node_groups.new("游戏资产", "GKAssetTree")
    tree.use_fake_user = True
    counts = gk_ui._fill_asset_cards(tree)
    log(f"资产图收录:模型 {counts['model']} / 图片 {counts['image']} / "
        f"曲线 {counts['curve']} / 声音 {counts['sound']}")

    # 声音卡:『音效_播放』里那个路径**已经被收录成卡了**(gk_res 相同),
    # 这里把它补成"有本地文件"的那种(收录只能看到 res:// 路径,
    # 看不到本地文件 —— 文件得自己挑一次),而不是再加一张重复的卡。
    card = gk_ui._ensure_asset_card(
        tree, "GKNodeAssetSound", sound_assets.sound_res_path(wav_path))
    if card is not None:
        card.gk_name = "叮"
        card.gk_file = wav_path
        log(f"资产图:声音卡『叮』-> {card.gk_res}(本地文件 {os.path.basename(wav_path)})")

    cards = [node for node in tree.nodes
             if getattr(node, "bl_idname", "").startswith("GKNodeAsset")]
    log(f"资产图共 {len(cards)} 张卡:{[node.name for node in cards]}")
    return tree


def collect_collections(*scenes):
    """把每个场景里的资产物体收进各自的『资产』集合(用户要的"隔开")。"""
    from BlenderGameKit import ui as gk_ui
    for scene in scenes:
        objects = gk_ui._scene_asset_objects(scene)
        moved = gk_ui.move_into_asset_collection(scene, objects)
        collection = gk_ui.asset_collection(scene, create=False)
        log(f"『{scene.name}』资产集合:{moved} 个物体"
            + (f" -> 『{collection.name}』" if collection else ""))


def save_blend(path):
    """后台保存:显式列出要比 .blend 带上的数据块。

    ⚠ 不能漏 `collections` / `images` / `curves` —— 少了 collections,
    『资产』集合和它的归属关系就没了(物体变成孤儿),这是这一轮新加的
    结构,必须一起写。
    """
    if not bpy.app.background:
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return
    blocks = set(bpy.data.objects) | set(bpy.data.meshes) | \
        set(bpy.data.materials) | set(bpy.data.node_groups) | \
        set(bpy.data.cameras) | set(bpy.data.lights) | \
        set(bpy.data.images) | set(bpy.data.curves) | \
        set(bpy.data.collections)
    scenes = list(bpy.data.scenes)
    bpy.data.libraries.write(path, blocks | set(scenes), fake_user=True)


def main():
    from BlenderGameKit import asset_export, project

    os.makedirs(OUT_DIR, exist_ok=True)
    scene, player, _trees, track_cam = build_level_one()
    scene2 = build_level_two()
    build_level_two_logic(scene2)
    make_logo_image()
    wav_path = make_wav(WAV_PATH)
    place_trees_into(scene2, count=2)
    build_logic(scene, player, track_cam, wav_path)
    build_asset_tree(wav_path)
    collect_collections(scene, scene2)

    project.create_project(PROJECT_DIR, title="资产示例",
                           blend_file_name=os.path.basename(BLEND_PATH))
    result = asset_export.export_all_scenes(PROJECT_DIR, dimension="3")
    if result.get("error"):
        log(f"导出失败:{result['error']}")
        return 1
    for entry in result.get("scenes", []):
        log(f"场景 {entry['scene']} -> {entry['scene_name']}.tscn"
            f"(脚本 {entry['script_name']})")
    for warning in result.get("warnings", []):
        log(f"告警: {warning}")
    if result.get("deleted"):
        log(f"清理旧文件: {result['deleted']}")

    save_blend(BLEND_PATH)
    log(f"已保存 {BLEND_PATH}")
    log(f"工程目录: {PROJECT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())