"""集合层级示例(第一百零二轮)—— Blender 的集合 = Godot 的场景树分组。

用法:
    blender -b --python scripts\\make_groups_demo.py
产出:
    examples/demo_groups/集合层级示例.blend
    examples/demo_groups/集合层级示例_godot/   (可用 Godot 直接打开)

── 这一屏在看什么 ────────────────────────────────────────────────
Blender 右侧『大纲视图』里那几个**集合**,导出后在 Godot 的场景树里
就是**同名节点** —— 东西多的时候不用在一个大平铺列表里找:

    World (glb 实例)
      房间A                 ← Blender 的集合『房间A』
        A_墙 / A_桌子
      房间B
        架子                 ← 集合里还能套集合(嵌套保留)
          架子_板1 / 架子_板2
      地面 / 示例角色         ← 直接放在场景根集合里 -> 直接在 World 下
      Scene Collection       ← 只有"和分组撞名"时才保留这一层
        Colliders            ← 你建的集合就叫 Colliders,和碰撞分组重名
          重名方块
      Colliders              ← 导出生成的碰撞代理(不是集合,是物理体)

| 用到的 | 在游戏里怎么看 |
|---|---|
| Blender 集合 | Godot 场景树里的同名分组节点 |
| 嵌套集合 | 分组里再分组 |
| 空集合 | **不会**出现在 Godot 里(导出时丢掉) |
| 撞名集合 | 保留一层 `Scene Collection/` 前缀,免得和碰撞分组打架 |

按键:WASD 走动 / 空格跳 / 鼠标转视角 / C 俯瞰 V 侧视。

跑法(Godot):打开 `集合层级示例_godot` 直接 F5。
自动检查(不用 Blender,要 Godot):`python scripts\\check_groups_demo.py`
"""

import math
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "examples", "demo_groups")
BLEND_PATH = os.path.join(OUT_DIR, "集合层级示例.blend")

PLAYER = "示例角色"
LOGIC_NAME = "集合层级逻辑"

# (物体名, 尺寸, 位置, 颜色)
# 布局:一条东西向走廊,两侧各一排"房间";角色从中间出发,
# 往右按 D 会撞到 房间A 的墙(x≈5)—— 探针拿这个验"碰撞还挡得住"。
FLOOR = ("地面", (22.0, 14.0, 0.4), (0.0, 0.0, -0.2), (0.16, 0.18, 0.22, 1.0))
ROOM_A = (("A_墙", (0.6, 12.0, 3.0), (5.0, 0.0, 1.5), (0.55, 0.32, 0.24, 1.0)),
          ("A_桌子", (2.0, 1.0, 0.8), (2.5, 3.5, 0.4), (0.62, 0.44, 0.28, 1.0)))
ROOM_B = (("B_墙", (0.6, 12.0, 3.0), (-5.0, 0.0, 1.5), (0.30, 0.42, 0.55, 1.0)),)
SHELF = (("架子_板1", (1.6, 0.8, 0.2), (-3.5, 3.5, 0.6), (0.48, 0.40, 0.30, 1.0)),
         ("架子_板2", (1.6, 0.8, 0.2), (-3.5, 4.2, 1.2), (0.48, 0.40, 0.30, 1.0)))
CLASH = ("重名方块", (1.0, 1.0, 1.0), (0.0, 4.5, 0.5), (0.70, 0.30, 0.30, 1.0))


def log(message):
    print(f"[集合层级] {message}")


def clear_scene():
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for group in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(group)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    # 集合也要清 —— 上一次跑剩的『房间A』还挂着的话,这次就成了重名
    for coll in list(bpy.data.collections):
        bpy.data.collections.remove(coll)
    scene.name = "集合层级示例"
    return scene


def make_collection(name, parent):
    coll = bpy.data.collections.new(name)
    parent.children.link(coll)
    return coll


def make_box(name, size, location, color, coll):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    material = bpy.data.materials.new(f"{name}_材质")
    material.use_nodes = True
    principled = material.node_tree.nodes.get("Principled BSDF")
    if principled is not None:
        principled.inputs["Base Color"].default_value = color
    obj.data.materials.append(material)
    # 一个物体只留在**一个**集合里:导出时它归哪个分组就看这个
    for old in list(obj.users_collection):
        old.objects.unlink(obj)
    coll.objects.link(obj)
    return obj


def make_camera(name, location, rotation):
    data = bpy.data.cameras.new(f"{name}数据")
    camera = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = location
    camera.rotation_euler = rotation
    return camera


def build_scene():
    scene = clear_scene()
    root = scene.collection

    # ── 集合:房间A / 房间B(套一层『架子』)/ 撞名的 Colliders / 空集合 ──
    room_a = make_collection("房间A", root)
    room_b = make_collection("房间B", root)
    shelf = make_collection("架子", room_b)
    clash = make_collection("Colliders", root)
    make_collection("暂时不用", root)          # 空集合:导出时会被丢掉

    # ── 直接在根集合里的:地面 + 角色(不进任何子集合)────────────────
    floor = make_box(*FLOOR, coll=root)
    player = make_box(PLAYER, (1.0, 2.0, 1.0), (0.0, 0.0, 1.0),
                      (0.85, 0.60, 0.25, 1.0), coll=root)
    for name, size, location, color in ROOM_A:
        make_box(name, size, location, color, coll=room_a)
    for name, size, location, color in ROOM_B:
        make_box(name, size, location, color, coll=room_b)
    for name, size, location, color in SHELF:
        make_box(name, size, location, color, coll=shelf)
    make_box(*CLASH, coll=clash)

    side = make_camera("侧视机位", (0.0, -16.0, 4.0),
                       (math.radians(76.0), 0.0, 0.0))
    make_camera("俯瞰机位", (0.0, -8.0, 16.0), (math.radians(24.0), 0.0, 0.0))
    scene.camera = side
    light_data = bpy.data.lights.new("太阳光数据", type="SUN")
    light_data.energy = 3.0
    light = bpy.data.objects.new("太阳光", light_data)
    root.objects.link(light)
    light.location = (5.0, -5.0, 10.0)
    light.rotation_euler = (math.radians(52.0), 0.0, math.radians(28.0))

    log("场景:地面 + 角色 + 房间A(墙/桌子)+ 房间B(墙 + 嵌套『架子』)"
        " + 撞名集合『Colliders』+ 空集合『暂时不用』")
    return scene, player, floor


def write_physics(scene, player):
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
        elif getattr(obj, "type", "") == "MESH":
            physics.body_type = "StaticBody3D"
            physics.dimension = "3"


def build_graph(scene, player):
    """玩法图:直接用『第三人称角色』预设(输入 + 移动 + 相机 + 跳)。"""
    from BlenderGameKit import presets

    logic = bpy.data.node_groups.new(LOGIC_NAME, "GKGameLogicTree")
    logic.use_fake_user = True
    summary = presets.build_logic_tree(logic, {
        "character": player.name,
        "with_animation": False,      # 这一关不演示动画,只演示集合分组
    })
    log(f"玩法图『{logic.name}』:{summary}")
    return logic


def _apply_settings(scene, pairs):
    settings = getattr(scene, "gk_settings", None)
    if settings is None:
        return
    for attr, value in pairs:
        if hasattr(settings, attr):
            try:
                setattr(settings, attr, value)
            except Exception:                             # noqa: BLE001
                pass


def save_blend(path):
    if not bpy.app.background:
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return
    # ⚠ collections 必须在名单里 —— 漏了的话 .blend 里一个集合都没有,
    # 示例就演示不了"集合层级"本身。
    blocks = set(bpy.data.objects) | set(bpy.data.meshes) | \
        set(bpy.data.materials) | set(bpy.data.node_groups) | \
        set(bpy.data.cameras) | set(bpy.data.lights) | \
        set(bpy.data.collections) | set(bpy.data.actions)
    scenes = list(bpy.data.scenes)
    bpy.data.libraries.write(path, blocks | set(scenes), fake_user=True)


def main():
    from BlenderGameKit import asset_export, project

    os.makedirs(OUT_DIR, exist_ok=True)
    scene, player, _floor = build_scene()
    write_physics(scene, player)
    build_graph(scene, player)
    _apply_settings(scene, (("dimension", "3"),
                            ("base_class", "CharacterBody3D"),
                            ("logic_tree", LOGIC_NAME)))

    directory = os.path.join(OUT_DIR, "集合层级示例_godot")
    project.create_project(directory, title="集合层级示例",
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
    for note in result.get("notes", []) or []:
        log(f"提示: {note}")
    if result.get("deleted"):
        log(f"清理旧文件: {result['deleted']}")

    save_blend(BLEND_PATH)
    log(f"已保存 {BLEND_PATH}")
    log(f"工程目录: {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())