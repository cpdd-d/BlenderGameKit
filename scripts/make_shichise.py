"""《七色》· 赤之领域（M1 灰盒）—— 生成脚本。

用法:
    blender -b --gpu-backend vulkan --python scripts\\make_shichise.py

产出:
    games/七色/七色.blend
    games/七色/七色_godot/      (可直接用 Godot 打开,按 F5)

── 这一版做什么 ────────────────────────────────────────────────
一张约 300 米见方的「赤之领域」大地图,四个区域放在同一张场景里:

    村庄(东南) → 赤之边境(北) → 军镇(中央) → 赤之塔(西北)

全部用方块 / 圆柱占位,方便后续把模型逐个换掉。场景默认是
**灰白**(环境饱和度 0),赤色回归时由世界脚本把饱和度推到 1。

── 为什么先搭场景再写战斗 ─────────────────────────────────────
这一版的验收只关心一件事:Blender 里的地图、玩家、相机、灯光能
原样出现在 Godot 里,并且按 F5 能走。战斗 / 敌人 / 颜色演出接在
下一版,避免一次改太多、出问题时分不清是场景还是脚本。

── 约定 ────────────────────────────────────────────────────────
一个 Blender 场景 = 一关 = 一张 .tscn。名字里带「赤之领域」。
角色逻辑图挂玩家物体,关卡逻辑图挂场景根(见 gk_scope)。
"""

import math
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import shichise_scripts

OUT_DIR = os.path.join(ROOT, "games", "七色")
BLEND_PATH = os.path.join(OUT_DIR, "七色.blend")
GODOT_DIR = os.path.join(OUT_DIR, "七色_godot")

SCENE_NAME = "赤之领域"
PLAYER_NAME = "艾尔文"
CAM_SIDE = "侧视机位"
CAM_TOP = "俯瞰机位"
LOGIC_NAME = "七色_关卡逻辑"
UI_NAME = "七色界面"

# 敌人 / BOSS / 塔门
BORDER_ENEMIES = (("敌人_边境1", (38.0, 12.0)),
                  ("敌人_边境2", (46.0, 20.0)),
                  ("敌人_边境3", (34.0, 24.0)))
TOWN_ENEMIES = (("敌人_军镇1", (18.0, 14.0)),
                ("敌人_军镇2", (26.0, 26.0)),
                ("敌人_军镇3", (14.0, 34.0)))
TOWER_ENEMIES = (("敌人_塔1", (-60.0, 56.0, 5.1)),
                 ("敌人_塔2", (-58.0, 64.0, 13.1)),
                 ("敌人_塔3", (-70.0, 66.0, 13.1)),
                 ("敌人_塔4", (-65.0, 74.0, 21.1)))
ENEMY_NAMES = tuple(name for name, _ in
                    BORDER_ENEMIES + TOWN_ENEMIES) + tuple(
                        name for name, _ in TOWER_ENEMIES)
BOSS_NAME = "赤之骑士团长"
GATE_NAME = "赤塔门"

# 自定义脚本的文本块名(ASCII;导出路径用它)
SCRIPT_NAMES = ("shichise_world", "shichise_enemy", "shichise_player")

# 地图坐标(Blender:X 向东,Y 向北,Z 向上)
MAP_HALF = 150.0
VILLAGE = (70.0, -70.0)
BORDER = (40.0, 10.0)
TOWN = (20.0, 18.0)
TOWER = (-65.0, 65.0)
PLAYER_SPAWN = (70.0, -82.0, 1.0)

# 赤色世界的主色(灰白时不显示,恢复后是暖红)
C_STONE = (0.42, 0.20, 0.16, 1.0)
C_STONE_DARK = (0.18, 0.08, 0.06, 1.0)
C_ROAD = (0.44, 0.24, 0.18, 1.0)
C_WALL = (0.62, 0.20, 0.11, 1.0)
C_ROOF = (0.88, 0.05, 0.02, 1.0)
C_WOOD = (0.32, 0.15, 0.09, 1.0)
C_METAL = (0.28, 0.20, 0.20, 1.0)
C_RED = (0.98, 0.06, 0.02, 1.0)
C_RED_DARK = (0.30, 0.03, 0.02, 1.0)
C_EMBER = (1.0, 0.36, 0.06, 1.0)
C_GRASS = (0.24, 0.30, 0.16, 1.0)

_MATERIALS = {}


def log(message):
    print(f"[七色] {message}")


def clear_all():
    """清空所有数据块(不能用 read_factory_settings,会把插件一起卸掉)。"""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for group in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(group)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    for curve in list(bpy.data.curves):
        bpy.data.curves.remove(curve)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)
    for text in list(bpy.data.texts):
        bpy.data.texts.remove(text)
    for scene in list(bpy.data.scenes)[1:]:
        bpy.data.scenes.remove(scene)
    scene = bpy.context.scene
    scene.name = SCENE_NAME
    return scene


def material_for(name, color, glow=None):
    """按颜色缓存材质 —— 一百多间房子不该建一百多份材质。"""
    key = (name, tuple(color), tuple(glow[0]) if glow else None,
           float(glow[1]) if glow else 0.0)
    cached = _MATERIALS.get(key)
    if cached is not None:
        return cached
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    principled = material.node_tree.nodes.get("Principled BSDF")
    if principled is not None:
        principled.inputs["Base Color"].default_value = color
        if glow:
            try:
                principled.inputs["Emission Color"].default_value = glow[0]
                principled.inputs["Emission Strength"].default_value = glow[1]
            except Exception:                             # noqa: BLE001
                pass
    _MATERIALS[key] = material
    return material


def make_box(name, size, location, material, scene, rotation=(0.0, 0.0, 0.0)):
    """长方体(底层 API 建 + 直接链接进目标场景,不用 bpy.ops)。"""
    sx, sy, sz = [v * 0.5 for v in size]
    verts = [(-sx, -sy, -sz), (sx, -sy, -sz), (sx, sy, -sz), (-sx, sy, -sz),
             (-sx, -sy, sz), (sx, -sy, sz), (sx, sy, sz), (-sx, sy, sz)]
    faces = [(3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5),
             (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh = bpy.data.meshes.new(f"{name}网格")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    obj.location = location
    obj.rotation_euler = rotation
    obj.data.materials.append(material)
    return obj


def make_cylinder(name, radius, depth, location, material, scene,
                  segments=20, rotation=(0.0, 0.0, 0.0)):
    verts, faces = [], []
    for index in range(segments):
        angle = 2.0 * math.pi * index / segments
        verts.append((math.cos(angle) * radius, math.sin(angle) * radius,
                      -depth / 2.0))
    for index in range(segments):
        angle = 2.0 * math.pi * index / segments
        verts.append((math.cos(angle) * radius, math.sin(angle) * radius,
                      depth / 2.0))
    for index in range(segments):
        nxt = (index + 1) % segments
        faces.append((index, nxt, segments + nxt, segments + index))
    faces.append(tuple(range(segments - 1, -1, -1)))
    faces.append(tuple(range(segments, segments * 2)))
    mesh = bpy.data.meshes.new(f"{name}网格")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    obj.location = location
    obj.rotation_euler = rotation
    obj.data.materials.append(material)
    return obj


def make_cone(name, radius, depth, location, material, scene, segments=16,
              rotation=(0.0, 0.0, 0.0)):
    """圆锥(树冠 / 远山 / 尖顶)。"""
    verts, faces = [], []
    for index in range(segments):
        angle = 2.0 * math.pi * index / segments
        verts.append((math.cos(angle) * radius, math.sin(angle) * radius,
                      -depth / 2.0))
    verts.append((0.0, 0.0, depth / 2.0))
    for index in range(segments):
        nxt = (index + 1) % segments
        faces.append((index, nxt, segments))
    faces.append(tuple(range(segments - 1, -1, -1)))
    mesh = bpy.data.meshes.new(f"{name}网格")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    obj.location = location
    obj.rotation_euler = rotation
    obj.data.materials.append(material)
    return obj


def make_camera(name, location, rotation, scene, lens=50.0):
    data = bpy.data.cameras.new(f"{name}数据")
    data.lens = lens
    camera = bpy.data.objects.new(name, data)
    scene.collection.objects.link(camera)
    camera.location = location
    camera.rotation_euler = rotation
    return camera


def make_light(name, kind, location, energy, color, scene,
               rotation=(0.0, 0.0, 0.0), cutoff=12.0, spot_size=None,
               shadow=True, blend=0.5):
    data = bpy.data.lights.new(f"{name}数据", type=kind)
    data.energy = energy
    data.color = color
    data.use_shadow = shadow
    if kind == "SPOT" and spot_size is not None:
        data.spot_size = spot_size
        data.spot_blend = blend
    if kind in ("POINT", "SPOT"):
        data.shadow_soft_size = 0.12
        data.use_custom_distance = True
        data.cutoff_distance = cutoff
    light = bpy.data.objects.new(name, data)
    scene.collection.objects.link(light)
    light.location = location
    light.rotation_euler = rotation
    return light


def decor(obj):
    """装饰件:不生成碰撞(细长件会被自动碰撞猜成巨球,把路堵死)。"""
    physics = getattr(obj, "gk_physics", None)
    if physics is not None:
        try:
            physics.collision_shape = "none"
        except Exception:                                 # noqa: BLE001
            pass
    return obj


def solid_box(obj):
    """结构件:显式用盒子碰撞。"""
    physics = getattr(obj, "gk_physics", None)
    if physics is not None:
        try:
            physics.collision_shape = "box"
        except Exception:                                 # noqa: BLE001
            pass
    return obj


def hide_mesh(obj):
    """判定体:网格不进 glb,碰撞照旧生成。"""
    physics = getattr(obj, "gk_physics", None)
    if physics is not None:
        try:
            physics.hide_mesh = True
        except Exception:                                 # noqa: BLE001
            pass
    return obj


def make_road(name, start, end, width, scene, material=None):
    """两点之间的一段路(用旋转的长方体,不用曲线)。"""
    sx, sy = float(start[0]), float(start[1])
    ex, ey = float(end[0]), float(end[1])
    dx, dy = ex - sx, ey - sy
    length = math.hypot(dx, dy)
    if length < 1e-4:
        return None
    angle = math.atan2(dy, dx)
    obj = make_box(name, (length, width, 0.18),
                   ((sx + ex) / 2.0, (sy + ey) / 2.0, 0.09),
                   material or material_for("MAT_赤域_土路", C_ROAD),
                   scene, rotation=(0.0, 0.0, angle))
    decor(obj)
    return obj


def make_ramp(name, center, run, rise, width, scene, material=None,
              direction="y", base_z=0.0):
    """斜坡:沿 +Y 或 +X 往上走。角度控制在 45° 以内(角色能爬)。"""
    run = max(0.5, float(run))
    rise = float(rise)
    angle = math.atan2(rise, run)
    length = math.hypot(run, rise)
    if direction == "y":
        size = (width, length, 0.4)
        rotation = (angle, 0.0, 0.0)
    else:
        size = (length, width, 0.4)
        rotation = (0.0, -angle, 0.0)
    return solid_box(make_box(name, size,
                              (center[0], center[1], base_z + rise / 2.0),
                              material or material_for("MAT_赤域_石阶", C_STONE),
                              scene, rotation=rotation))


def make_tree(name, location, scene, height=7.0):
    trunk = solid_box(make_box(name + "树干", (0.7, 0.7, height * 0.55),
                               (location[0], location[1], height * 0.275),
                               material_for("MAT_赤域_枯木", C_WOOD), scene))
    crown = decor(make_cone(name + "树冠", height * 0.32, height * 0.55,
                           (location[0], location[1], height * 0.78),
                           material_for("MAT_赤域_树冠", C_GRASS), scene))
    return trunk, crown


def make_house(name, center, width, depth, height, scene, facing=0.0):
    """一间村屋:墙 + 屋顶 + 门 + 两扇窗。"""
    wall = material_for("MAT_赤域_土墙", C_WALL)
    roof = material_for("MAT_赤域_瓦顶", C_ROOF)
    dark = material_for("MAT_赤域_木构件", C_STONE_DARK)
    base = solid_box(make_box(name, (width, depth, height),
                              (center[0], center[1], height / 2.0), wall, scene))
    decor(make_box(name + "屋顶", (width * 1.12, depth * 1.12, 0.45),
                   (center[0], center[1], height + 0.22), roof, scene))
    decor(make_box(name + "门", (width * 0.22, 0.12, height * 0.45),
                   (center[0], center[1] - depth * 0.51, height * 0.225),
                   dark, scene))
    for side in (-1, 1):
        decor(make_box(f"{name}窗{side}", (width * 0.18, 0.10, height * 0.22),
                       (center[0] + side * width * 0.28,
                        center[1] - depth * 0.51, height * 0.62),
                       dark, scene))
    return base


def make_fence(name, start, end, scene, height=1.2):
    """一段栅栏(纯装饰)。"""
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    length = math.hypot(dx, dy)
    if length < 1e-4:
        return
    angle = math.atan2(dy, dx)
    decor(make_box(name, (length, 0.12, 0.12),
                   ((sx + ex) / 2.0, (sy + ey) / 2.0, height),
                   material_for("MAT_赤域_木栅栏", C_WOOD), scene,
                   rotation=(0.0, 0.0, angle)))
    steps = max(2, int(length // 2.0))
    for index in range(steps + 1):
        t = index / steps
        decor(make_box(f"{name}柱{index}", (0.16, 0.16, height * 0.9),
                       (sx + dx * t, sy + dy * t, height * 0.45),
                       material_for("MAT_赤域_木栅栏", C_WOOD), scene))


def build_village(scene):
    """灰白之村:屋群 + 教堂 + 水井 + 田地 + 篝火 + 莉娅。"""
    make_box("村口牌坊", (14.0, 0.8, 4.5), (70.0, -96.0, 2.25),
             material_for("MAT_赤域_木牌坊", C_WOOD), scene)
    make_box("村口牌坊右柱", (0.8, 0.8, 5.5), (76.0, -96.0, 2.75),
             material_for("MAT_赤域_木牌坊", C_WOOD), scene)
    make_box("村口牌坊左柱", (0.8, 0.8, 5.5), (64.0, -96.0, 2.75),
             material_for("MAT_赤域_木牌坊", C_WOOD), scene)

    houses = (
        ("村屋一", (52.0, -78.0), 7.0, 9.0, 4.2),
        ("村屋二", (88.0, -78.0), 7.0, 9.0, 4.2),
        ("村屋三", (48.0, -62.0), 8.0, 8.0, 4.6),
        ("村屋四", (92.0, -60.0), 8.0, 8.0, 4.6),
        ("村屋五", (58.0, -48.0), 9.0, 7.0, 4.0),
        ("村屋六", (82.0, -48.0), 9.0, 7.0, 4.0),
        ("村屋七", (44.0, -90.0), 6.0, 7.0, 3.8),
        ("村屋八", (96.0, -90.0), 6.0, 7.0, 3.8),
    )
    for name, center, width, depth, height in houses:
        make_house(name, center, width, depth, height, scene)

    # 教堂:高一点的塔 + 尖顶(远看是村庄的地标)
    solid_box(make_box("教堂", (11.0, 15.0, 7.0), (70.0, -104.0, 3.5),
                       material_for("MAT_赤域_教堂墙", C_WALL), scene))
    decor(make_box("教堂塔", (3.2, 3.2, 12.0), (70.0, -110.0, 6.0),
                   material_for("MAT_赤域_教堂塔", C_STONE), scene))
    decor(make_cone("教堂尖顶", 2.2, 4.0, (70.0, -110.0, 14.0),
                    material_for("MAT_赤域_教堂尖顶", C_ROOF), scene))

    # 水井
    solid_box(make_cylinder("水井", 1.6, 1.4, (70.0, -70.0, 0.7),
                            material_for("MAT_赤域_石井", C_STONE), scene))
    decor(make_box("水井架", (3.6, 0.35, 0.35), (70.0, -70.0, 3.2),
                   material_for("MAT_赤域_木构件", C_WOOD), scene))
    for side in (-1, 1):
        decor(make_box(f"水井柱{side}", (0.3, 0.3, 3.0),
                       (70.0 + side * 1.7, -70.0, 1.5),
                       material_for("MAT_赤域_木构件", C_WOOD), scene))

    # 田地:几条深色垄
    for index in range(5):
        decor(make_box(f"麦田垄{index}", (26.0, 0.6, 0.12),
                       (64.0, -58.0 + index * 1.4, 0.06),
                       material_for("MAT_赤域_田地", C_GRASS), scene))

    # 村口篝火(检查点)
    make_cylinder("篝火_村庄", 0.9, 0.35, (64.0, -88.0, 0.18),
                  material_for("MAT_赤域_篝火", C_EMBER,
                               glow=((1.0, 0.35, 0.08, 1.0), 4.0)), scene)
    make_light("村庄火光", "POINT", (64.0, -88.0, 1.0), 900.0,
               (1.0, 0.55, 0.25), scene, cutoff=10.0)

    # 莉娅:一个带红发带的小人(红发带是全村唯一还能看见的颜色)
    solid_box(make_box("莉娅", (0.55, 0.55, 1.55), (78.0, -88.0, 0.78),
                       material_for("MAT_莉娅_衣", (0.55, 0.48, 0.42, 1.0)), scene))
    decor(make_box("莉娅发带", (0.62, 0.62, 0.16), (78.0, -88.0, 1.48),
                   material_for("MAT_莉娅_红发带", C_RED,
                                glow=((0.9, 0.08, 0.05, 1.0), 2.0)), scene))

    # 村民
    for index, (x, y) in enumerate(((66.0, -68.0), (80.0, -66.0), (56.0, -52.0))):
        solid_box(make_box(f"村民{index + 1}", (0.5, 0.5, 1.5), (x, y, 0.75),
                           material_for("MAT_赤域_村民", (0.42, 0.38, 0.36, 1.0)),
                           scene))

    make_fence("村口栅栏东", (60.0, -94.0), (80.0, -94.0), scene)
    make_fence("村口栅栏西", (48.0, -86.0), (48.0, -70.0), scene)

    # 通往边境的土路(村口 -> 北面)
    make_road("村路一", (70.0, -100.0), (70.0, -50.0), 5.0, scene)
    make_road("村路二", (70.0, -50.0), (40.0, 8.0), 5.0, scene)
    log("村庄:8 间村屋 + 教堂 + 水井 + 田地 + 篝火 + 莉娅 + 7 个村民")


def build_border(scene):
    """赤之边境:灰白荒野、枯树、营地、三个火盆。"""
    # 岩柱
    rocks = (
        (30.0, 24.0, 5.0), (52.0, 28.0, 7.0), (62.0, 6.0, 4.0),
        (26.0, -4.0, 6.0), (56.0, 38.0, 5.0), (18.0, 32.0, 8.0),
    )
    for index, (x, y, height) in enumerate(rocks):
        solid_box(make_cone(f"边境岩柱{index + 1}", height * 0.45,
                            height, (x, y, height / 2.0),
                            material_for("MAT_赤域_岩", C_STONE), scene,
                            segments=9))
    for index, (x, y) in enumerate(((34.0, 4.0), (48.0, 34.0),
                                     (20.0, 12.0), (58.0, 18.0))):
        make_tree(f"边境枯树{index + 1}", (x, y), scene,
                  height=6.0 + index * 0.8)

    # 营地:两顶帐篷 + 篝火
    for index, (x, y) in enumerate(((36.0, -2.0), (46.0, 2.0))):
        decor(make_box(f"边境帐篷{index + 1}", (4.0, 5.0, 2.4),
                       (x, y, 1.2),
                       material_for("MAT_赤域_帐篷", (0.36, 0.30, 0.26, 1.0)),
                       scene))
        decor(make_cone(f"边境帐篷顶{index + 1}", 3.2, 1.6,
                        (x, y, 3.1),
                        material_for("MAT_赤域_帐篷布", (0.30, 0.26, 0.24, 1.0)),
                        scene, segments=4))
    make_cylinder("篝火_边境", 0.85, 0.35, (41.0, 3.0, 0.18),
                  material_for("MAT_赤域_篝火", C_EMBER,
                               glow=((1.0, 0.35, 0.08, 1.0), 4.0)), scene)
    make_light("边境火光", "POINT", (41.0, 3.0, 1.0), 900.0,
               (1.0, 0.55, 0.25), scene, cutoff=10.0)

    # 三个火盆(解谜)
    for index, (x, y) in enumerate(((31.0, 16.0), (49.0, 16.0), (40.0, 26.0))):
        make_cylinder(f"火盆{index + 1}", 0.75, 0.55, (x, y, 0.28),
                      material_for("MAT_赤域_火盆", C_METAL), scene)
        decor(make_box(f"火盆架{index + 1}", (1.4, 0.2, 0.2), (x, y, 0.9),
                       material_for("MAT_赤域_火盆架", C_METAL), scene))

    # 边境的边界石墙(把地图收住,顺便做远景)
    for index in range(7):
        x = 10.0 + index * 8.0
        decor(make_box(f"边境界石{index + 1}", (3.0, 2.0, 3.2),
                       (x, 44.0, 1.6),
                       material_for("MAT_赤域_界石", C_STONE), scene))
    log("边境:6 块岩柱 + 4 棵枯树 + 营地 + 3 个火盆")


def build_town(scene):
    """赤之军镇:城门、主街、铁匠铺、旅馆、民居。"""
    # 城门:两根门柱 + 门楣
    for side in (-1, 1):
        solid_box(make_box(f"军镇门柱{side}", (2.2, 2.2, 7.0),
                           (20.0 + side * 7.0, -6.0, 3.5),
                           material_for("MAT_赤域_门柱", C_STONE), scene))
    decor(make_box("军镇门楣", (16.0, 2.0, 1.4), (20.0, -6.0, 7.2),
                   material_for("MAT_赤域_门楣", C_STONE), scene))
    decor(make_box("军镇门旗", (1.0, 0.1, 3.4), (20.0, -7.1, 9.0),
                   material_for("MAT_赤域_旗帜", C_RED), scene))

    # 主街
    make_road("军镇大街", (20.0, -2.0), (20.0, 42.0), 9.0, scene)
    make_road("军镇横街", (4.0, 20.0), (38.0, 20.0), 6.0, scene)

    # 民居
    houses = (
        ("军镇屋一", (5.0, 6.0), 8.0, 9.0, 4.6),
        ("军镇屋二", (35.0, 6.0), 8.0, 9.0, 4.6),
        ("军镇屋三", (4.0, 28.0), 7.0, 10.0, 4.2),
        ("军镇屋四", (36.0, 28.0), 7.0, 10.0, 4.2),
        ("军镇屋五", (20.0, 46.0), 12.0, 7.0, 4.0),
    )
    for name, center, width, depth, height in houses:
        make_house(name, center, width, depth, height, scene)

    # 铁匠铺:一间大屋 + 炉子 + 烟囱
    solid_box(make_box("铁匠铺", (9.0, 8.0, 4.8), (10.0, 16.0, 2.4),
                       material_for("MAT_赤域_铁匠铺", C_STONE_DARK), scene))
    decor(make_box("铁匠铺烟囱", (1.2, 1.2, 3.4), (7.0, 18.0, 6.5),
                   material_for("MAT_赤域_烟囱", C_STONE), scene))
    make_box("铁匠铺炉子", (2.6, 2.0, 1.6), (12.0, 14.0, 0.8),
             material_for("MAT_赤域_炉子", C_METAL), scene)
    make_light("铁匠铺火光", "POINT", (12.0, 14.0, 1.3), 700.0,
               (1.0, 0.48, 0.20), scene, cutoff=9.0)

    # 旅馆
    solid_box(make_box("军镇旅馆", (10.0, 9.0, 5.2), (30.0, 16.0, 2.6),
                       material_for("MAT_赤域_旅馆", C_WALL), scene))
    decor(make_box("旅馆招牌", (2.6, 0.15, 1.2), (30.0, 11.5, 4.0),
                   material_for("MAT_赤域_招牌", C_WOOD), scene))

    # NPC
    for index, (x, y) in enumerate(((16.0, 4.0), (26.0, 8.0),
                                     (30.0, 30.0), (8.0, 24.0))):
        solid_box(make_box(f"军镇居民{index + 1}", (0.5, 0.5, 1.5),
                           (x, y, 0.75),
                           material_for("MAT_赤域_居民", (0.44, 0.40, 0.36, 1.0)),
                           scene))

    # 军镇入口篝火(检查点)
    make_cylinder("篝火_军镇", 0.85, 0.35, (24.0, 0.0, 0.18),
                  material_for("MAT_赤域_篝火", C_EMBER,
                               glow=((1.0, 0.35, 0.08, 1.0), 4.0)), scene)
    make_light("军镇火光", "POINT", (24.0, 0.0, 1.0), 900.0,
               (1.0, 0.55, 0.25), scene, cutoff=10.0)

    # 通往赤塔的土路
    make_road("塔路一", (20.0, 46.0), (-20.0, 52.0), 5.0, scene)
    make_road("塔路二", (-20.0, 52.0), (-65.0, 44.0), 5.0, scene)
    log("军镇:城门 + 主街 + 5 间民居 + 铁匠铺 + 旅馆 + 4 个居民")


def build_tower(scene):
    """赤之塔:阶梯式金字塔,四段斜坡一路走到塔顶。

    塔身越往上越窄、越往北移,所以南面留出一条连续的坡道 ——
    玩家从塔脚一路走上去,不用找楼梯。
    """
    stone = material_for("MAT_赤域_塔石", C_STONE)
    dark = material_for("MAT_赤域_塔石暗", C_STONE_DARK)
    red = material_for("MAT_赤域_塔纹", C_RED_DARK)

    # 四层塔体(中心逐步向北移)
    tiers = (
        ("塔一层", (-65.0, 65.0), 34.0, 0.0, 4.0),
        ("塔二层", (-65.0, 73.0), 26.0, 4.0, 12.0),
        ("塔三层", (-65.0, 79.0), 18.0, 12.0, 20.0),
        ("塔顶", (-65.0, 85.0), 10.0, 20.0, 24.0),
    )
    for name, center, size, z0, z1 in tiers:
        solid_box(make_box(name, (size, size, z1 - z0),
                           (center[0], center[1], (z0 + z1) / 2.0), stone, scene))
        # 每层南面一道红纹(颜色恢复后是一圈圈暖红)
        decor(make_box(name + "红纹", (size * 0.92, 0.3, 0.45),
                       (center[0], center[1] - size / 2.0,
                        z0 + 0.8), red, scene))

    # 四段斜坡(南面,一路向北向上)
    make_ramp("塔坡一", (-65.0, 45.0), 6.0, 4.0, 7.0, scene, base_z=0.0)
    make_ramp("塔坡二", (-65.0, 54.0), 12.0, 8.0, 6.0, scene, base_z=4.0)
    make_ramp("塔坡三", (-65.0, 65.0), 10.0, 8.0, 5.0, scene, base_z=12.0)
    make_ramp("塔坡四", (-65.0, 73.0), 6.0, 4.0, 4.0, scene, base_z=20.0)
    # 塔坡四到塔顶之间的一小段平台
    solid_box(make_box("塔顶平台", (4.5, 4.5, 0.4), (-65.0, 78.0, 23.8),
                       stone, scene))

    # 塔顶的赤色核心(颜色装置)
    make_cylinder("赤色核心", 1.6, 4.6, (-65.0, 88.0, 26.5),
                  material_for("MAT_赤域_核心", C_EMBER,
                               glow=((1.0, 0.22, 0.08, 1.0), 6.0)),
                  scene, segments=12)
    make_cylinder("赤色核心座", 2.6, 1.0, (-65.0, 88.0, 24.2),
                  dark, scene, segments=16)

    # 塔脚篝火(检查点)
    make_cylinder("篝火_赤塔", 0.85, 0.35, (-55.0, 45.0, 0.18),
                  material_for("MAT_赤域_篝火", C_EMBER,
                               glow=((1.0, 0.35, 0.08, 1.0), 4.0)), scene)
    make_light("赤塔火光", "POINT", (-55.0, 45.0, 1.0), 900.0,
               (1.0, 0.55, 0.25), scene, cutoff=10.0)

    # 塔体周围的红岩
    for index, (x, y) in enumerate(((-96.0, 60.0), (-99.0, 80.0),
                                     (-34.0, 92.0), (-88.0, 100.0))):
        make_tree(f"塔边枯树{index + 1}", (x, y), scene, height=6.5)
    log("赤之塔:四层塔体 + 四段斜坡 + 赤色核心 + 塔脚篝火")


def make_enemy(name, location, scene, is_boss=False):
    """敌人占位:一个深色方块 + 胸口一颗会发光的色核。

    色核是**子物体**:网格跟着敌人走;打碎后由敌人管理器把它隐藏。
    """
    if is_boss:
        size = (1.7, 1.7, 2.8)
        color = (0.16, 0.10, 0.12, 1.0)
        core_size = 0.62
    else:
        size = (1.1, 1.1, 2.2)
        color = (0.22, 0.16, 0.17, 1.0)
        core_size = 0.40
    body = solid_box(make_box(name, size, location,
                              material_for("MAT_赤域_敌人", color), scene))
    core = make_box(name + "色核", (core_size, core_size, core_size),
                    (location[0], location[1], location[2] + 0.2),
                    material_for("MAT_赤域_色核", C_RED,
                                 glow=((0.9, 0.10, 0.06, 1.0), 5.0)),
                    scene)
    core.parent = body
    core.matrix_parent_inverse = body.matrix_world.inverted()
    decor(core)
    return body


def build_gameplay(scene):
    """敌人、BOSS、塔门 —— 战斗与解谜的实体。"""
    for name, location in BORDER_ENEMIES + TOWN_ENEMIES:
        make_enemy(name, (location[0], location[1], 1.1), scene)
    for name, location in TOWER_ENEMIES:
        make_enemy(name, location, scene)
    make_enemy(BOSS_NAME, (-65.0, 85.0, 25.4), scene, is_boss=True)

    # 塔门:挡在塔坡一的尽头,三座火盆点亮后由世界脚本把它抬起来
    gate = solid_box(make_box(GATE_NAME, (8.0, 0.7, 7.0),
                              (-65.0, 47.8, 3.5),
                              material_for("MAT_赤域_塔门", C_RED_DARK),
                              scene))
    decor(make_box("塔门纹", (8.2, 0.1, 0.6), (-65.0, 47.4, 5.2),
                   material_for("MAT_赤域_塔门纹", C_RED), scene))
    log(f"战斗实体:{len(ENEMY_NAMES)} 个敌人 + BOSS + 塔门")


def build_boundary(scene):
    """地图边界:一圈远山,把 300 米的地图围住。"""
    mountain = material_for("MAT_赤域_远山", (0.24, 0.12, 0.11, 1.0))
    count = 26
    for index in range(count):
        angle = 2.0 * math.pi * index / count
        radius = MAP_HALF + 18.0
        x = math.cos(angle) * radius
        y = math.sin(angle) * radius
        height = 24.0 + (index % 5) * 6.0
        decor(make_cone(f"远山{index + 1}", 20.0 + (index % 4) * 5.0,
                        height, (x, y, height / 2.0), mountain, scene,
                        segments=7))
    log("边界:26 座远山")


def build_world(scene):
    """把四个区域 + 玩家 + 相机 + 灯光全部建出来。"""
    make_box("地面", (MAP_HALF * 2.0, MAP_HALF * 2.0, 1.0),
             (0.0, 0.0, -0.5),
             material_for("MAT_赤域_地面", (0.34, 0.16, 0.12, 1.0)), scene)
    build_village(scene)
    build_border(scene)
    build_town(scene)
    build_tower(scene)
    build_gameplay(scene)
    build_boundary(scene)

    # 玩家:一个 1×1×1.8 的占位人形,朝向 +Y
    # 深冷灰蓝 —— 灰白世界里也能从浅灰地面里分辨出来;恢复赤色后是冷色对比。
    player = solid_box(make_box(PLAYER_NAME, (1.0, 1.0, 1.8),
                                PLAYER_SPAWN,
                                material_for("MAT_艾尔文", (0.16, 0.20, 0.28, 1.0)),
                                scene))

    # 两台相机:第三人称(默认) + 俯瞰(调试 / 看地图)
    make_camera(CAM_SIDE, (PLAYER_SPAWN[0], PLAYER_SPAWN[1] - 8.0, 4.8),
                (math.radians(72.0), 0.0, 0.0), scene, lens=50.0)
    make_camera(CAM_TOP, (0.0, 0.0, 120.0),
                (0.0, 0.0, 0.0), scene, lens=18.0)
    scene.camera = bpy.data.objects[CAM_SIDE]

    # 太阳:卡通材质的明暗靠它,能量压在甜点区(0.5~0.7)
    make_light("太阳", "SUN", (0.0, 0.0, 80.0), 0.72,
               (1.0, 0.97, 0.93), scene,
               rotation=(math.radians(48.0), math.radians(12.0),
                         math.radians(28.0))) 
    # 冷色补光:让背光面不至于死黑
    make_light("天光", "SUN", (0.0, 0.0, 40.0), 0.12,
               (0.55, 0.65, 0.95), scene,
               rotation=(math.radians(115.0), 0.0, math.radians(200.0)),
               shadow=False)
    log("世界:地面 + 四区 + 玩家 + 侧视/俯瞰相机 + 太阳 + 天光")
    return player


def write_scripts():
    """把三个自定义 GDScript 写进 Blender 文本块,返回 {名字: Text}。

    在建节点之前创建文本块，供『脚本_加载』直接引用。
    """
    bodies = {
        "shichise_world": shichise_scripts.WORLD_SCRIPT,
        "shichise_enemy": shichise_scripts.ENEMY_SCRIPT,
        "shichise_player": shichise_scripts.PLAYER_SCRIPT,
    }
    script_texts = {}
    for name in SCRIPT_NAMES:
        text = bpy.data.texts.new(name)
        text.use_fake_user = True
        text.write(bodies[name])
        script_texts[name] = text
    log("自定义脚本:" + " / ".join(SCRIPT_NAMES))
    return script_texts


def _add(tree, kind, name, x, y, **props):
    node = tree.nodes.new(kind)
    node.name = name
    node.location = (x, y)
    for key, value in props.items():
        setattr(node, key, value)
    return node


def _link(tree, source, source_pin, target, target_pin):
    tree.links.new(source.outputs[source_pin], target.inputs[target_pin])


def build_character_graph(scene, player, script_texts):
    """角色图:第三人称移动 / 鼠标视角 / 弹簧臂 / C 俯瞰 / V 侧视 + 战斗脚本。"""
    from BlenderGameKit import presets

    logic, _anim, notes = presets.create_character_preset(
        scene, character=player,
        logic_name="七色_玩家逻辑", class_name="ShichisePlayer",
        with_animation=False, with_ports=True,
        camera_side=CAM_SIDE, camera_top=CAM_TOP,
        move_speed=6.0, arm_length=5.0, arm_height=1.5, aim_pitch=12.0,
        top_arm_length=40.0, top_pitch=80.0,
        with_god_camera=False,
    )
    for note in notes:
        log(note)
    # 战斗脚本挂在玩家节点上(和移动脚本同一个宿主)
    ready = _add(logic, "GKNodeOnReady", "加载玩家脚本", 300.0, 900.0)
    load = _add(logic, "GKNodeScriptLoad", "玩家战斗脚本", 560.0, 900.0,
                gk_script_text=script_texts["shichise_player"], gk_instance="",
                gk_start_method="")
    _link(logic, ready, "执行", load, "执行")
    # 关卡脚本要挂场景根(不是角色),所以场景设置这里显式改回 Node3D
    scene.gk_settings.base_class = "Node3D"
    scene.gk_settings.class_name = "ShichiseLevel"
    return logic


def build_level_graph(scene, script_texts):
    """关卡图:套用 HUD + 章节卡 + 世界脚本 / 敌人管理脚本。"""
    design = bpy.data.node_groups.new(UI_NAME, "GKUiDesignTree")
    design.use_fake_user = True
    _add(design, "GKNodeUiText", "目标文本", -200.0, 0.0,
         gk_text="目标:离开村庄,前往赤之塔",
         gk_x=24.0, gk_y=24.0, gk_size=20,
         gk_color=(0.06, 0.06, 0.08, 1.0))
    _add(design, "GKNodeUiText", "操作提示", 220.0, 0.0,
         gk_text="WASD 移动 · 鼠标转视角 · 空格 跳 · J 攻击 · K 闪避 · R 色技 · E 交互 · C 俯瞰",
         gk_align="BOTTOM_CENTER", gk_margin=10.0, gk_size=16,
         gk_color=(0.08, 0.08, 0.10, 0.95))
    card = _add(design, "GKNodeUiText", "章节卡", 640.0, 0.0,
                gk_text="序章 · 灰白之村", gk_align="CENTER", gk_size=42,
                gk_color=(0.95, 0.95, 0.95, 1.0))

    logic = bpy.data.node_groups.new(LOGIC_NAME, "GKGameLogicTree")
    logic.gk_scope = "LEVEL"
    logic.use_fake_user = True
    scene.gk_settings.logic_tree = LOGIC_NAME
    ready = _add(logic, "GKNodeOnReady", "每当_准备", -900.0, 0.0)
    apply = _add(logic, "GKNodeUiApplyDesign", "套用七色界面", -600.0, 0.0,
                 gk_design=design.name)
    hold = _add(logic, "GKNodeWait", "章节停留", -300.0, 0.0)
    hold.inputs["秒"].default_value = 3.5
    fade = _add(logic, "GKNodeUiSet", "章节淡出", 0.0, 0.0,
                gk_widget=card.name, gk_set_alpha=True, gk_alpha=0.0)
    _link(logic, ready, "执行", apply, "执行")
    _link(logic, apply, "完成", hold, "执行")
    _link(logic, hold, "完成", fade, "执行")

    # ⚠ 脚本必须排在章节卡 `await 3.5 秒` **之前**：同一个 _ready 里
    #   一旦 await，后面的语句会跟着挂起 —— 世界 / 敌人脚本会晚 3.5 秒才跑。
    #   节点按 Y 排序（越小越先），所以这两组放在 UI 链的下面。
    ready_world = _add(logic, "GKNodeOnReady", "加载世界脚本", -900.0, -420.0)
    load_world = _add(logic, "GKNodeScriptLoad", "世界脚本", -600.0, -420.0,
                      gk_script_text=script_texts["shichise_world"], gk_instance="",
                      gk_start_method="")
    ready_enemy = _add(logic, "GKNodeOnReady", "加载敌人脚本", -900.0, -220.0)
    load_enemy = _add(logic, "GKNodeScriptLoad", "敌人脚本", -600.0, -220.0,
                      gk_script_text=script_texts["shichise_enemy"], gk_instance="",
                      gk_start_method="")
    _link(logic, ready_world, "执行", load_world, "执行")
    _link(logic, ready_enemy, "执行", load_enemy, "执行")
    log("关卡图:界面 + 世界脚本 + 敌人管理脚本")
    return logic


def apply_scene_settings(scene):
    """灰白世界:环境饱和度 0,赤色回归时由脚本推到 1。"""
    settings = getattr(scene, "gk_settings", None)
    if settings is None:
        return
    pairs = (
        ("dimension", "3"),
        ("toon_follow_light", True),
        ("god_camera", False),
        ("env_enabled", True),
        ("env_background", "SKY"),
        ("env_bg_color", (0.95, 0.30, 0.14, 1.0)),
        ("env_ambient_color", (0.95, 0.62, 0.50, 1.0)),
        ("env_ambient_energy", 0.22),
        ("env_fog", True),
        ("env_fog_color", (0.95, 0.38, 0.22, 1.0)),
        ("env_fog_density", 0.0015),
        ("env_fog_sky_affect", 0.0),
        ("env_glow", True),
        ("env_glow_intensity", 0.30),
        ("env_glow_bloom", 0.06),
        ("env_tonemap", "LINEAR"),
        ("env_adjust", True),
        ("env_brightness", 1.0),
        ("env_contrast", 1.0),
        ("env_saturation", 0.0),
        # 全局卡通风格:阴影侧也留在暖红里(默认的冷紫阴影会把赤色洗成灰褐)
        ("style_enabled", True),
        ("style_shadow_step", 0.28),
        ("style_shadow_smooth", 0.18),
        ("style_shadow_tint", (0.52, 0.18, 0.12, 1.0)),
        ("style_shadow_tint_2", (0.30, 0.08, 0.05, 1.0)),
        ("style_shadow_step_2", -0.35),
        ("style_three_tone", 1.0),
        ("style_rim", 0.0),
        ("style_specular", 0.0),
    )
    for attr, value in pairs:
        if not hasattr(settings, attr):
            continue
        try:
            setattr(settings, attr, value)
        except Exception:                                 # noqa: BLE001
            pass
    log("场景设置:3D + 卡通跟随光 + 天空 + 雾 + 灰白(饱和度 0)")


def write_physics(scene, player):
    """按物体归位:玩家 CharacterBody3D + 挂脚本,其余网格 StaticBody3D。"""
    for obj in scene.objects:
        physics = getattr(obj, "gk_physics", None)
        if physics is None:
            continue
        try:
            physics.shape_size = (0.0, 0.0, 0.0)
        except Exception:                                 # noqa: BLE001
            pass
        if obj is player:
            physics.body_type = "CharacterBody3D"
            physics.dimension = "3"
            physics.script_target = True
        elif obj.name in ENEMY_NAMES or obj.name == BOSS_NAME:
            physics.body_type = "CharacterBody3D"
            physics.dimension = "3"
        elif obj.name == GATE_NAME:
            physics.body_type = "AnimatableBody3D"
            physics.dimension = "3"
        elif getattr(obj, "type", "") == "MESH":
            physics.body_type = "StaticBody3D"
            physics.dimension = "3"
    log(f"{PLAYER_NAME}:CharacterBody3D + 挂脚本;"
        f"{len(ENEMY_NAMES)} 个敌人 + BOSS:CharacterBody3D;{GATE_NAME}:AnimatableBody3D")


def save_blend(path):
    if not bpy.app.background:
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return
    blocks = set(bpy.data.objects) | set(bpy.data.meshes) | \
        set(bpy.data.materials) | set(bpy.data.node_groups) | \
        set(bpy.data.cameras) | set(bpy.data.lights) | set(bpy.data.curves) | \
        set(bpy.data.texts)
    scenes = list(bpy.data.scenes)
    bpy.data.libraries.write(path, blocks | set(scenes), fake_user=True)


def main():
    from BlenderGameKit import asset_export, project, register

    if bpy.types.NodeTree.bl_rna_get_subclass_py("GKUiDesignTree", None) is None:
        register()

    os.makedirs(OUT_DIR, exist_ok=True)
    scene = clear_all()
    _MATERIALS.clear()

    script_texts = write_scripts()
    player = build_world(scene)
    build_character_graph(scene, player, script_texts)
    build_level_graph(scene, script_texts)
    apply_scene_settings(scene)
    write_physics(scene, player)

    directory = GODOT_DIR
    project.create_project(directory, title="七色",
                           blend_file_name=os.path.basename(BLEND_PATH))
    result = asset_export.export_all_scenes(directory, dimension="3")
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

    exported = {f"res://scenes/{e['scene_name']}.tscn"
                for e in result.get("scenes", []) if e.get("ok")}
    for entry in result.get("scenes", []):
        if entry.get("ok"):
            main_scene = f"res://scenes/{entry['scene_name']}.tscn"
            project.set_main_scene(directory, main_scene)
            log(f"主场景: {main_scene}")
            break

    save_blend(BLEND_PATH)
    log(f"已保存 {BLEND_PATH}")
    log(f"工程目录: {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
