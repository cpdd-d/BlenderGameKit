"""环境示例(第四十九轮:阶段②环境与后处理)—— 白天 / 幽暗两关对比。

用法:
    blender -b --python scripts\\make_env_demo.py
产出:
    examples/demo_env/环境示例.blend
    examples/demo_env/环境示例_godot/   (可用 Godot 直接打开)

── 这一屏在看什么 ────────────────────────────────────────────────
同一个几何搭两遍,只换**环境与后处理**,看 WorldEnvironment 到底改了什么:

| 场景 | 背景 | 环境光 | 雾 | 泛光 | 色调 |
|---|---|---|---|---|---|
| **白天** | 程序化天空 | 亮(0.8) | 关 | 关 | AgX |
| **幽暗** | 近黑的纯色 | 暗(0.15) | 开(0.08 冷蓝) | 开(1.2) | ACES |

两关在 Blender 里是**两个场景**,导出时各出一张 .tscn
(`scenes/白天.tscn` / `scenes/幽暗.tscn`,见 asset_export.export_all_scenes)。
『游戏显示』设置图里那张『显示_环境氛围』卡,改的就是这一套值(写透场景设置)。

跑法(Godot):打开 `环境示例_godot`,F5 跑白天;想跑幽暗就在 Godot 里
把主场景改成 `res://scenes/幽暗.tscn`(或直接打开那张 .tscn 按 F6)。
自动检查(不用 Blender,要 Godot):`python scripts\\check_env_demo.py`
"""

import math
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "examples", "demo_env")
BLEND_PATH = os.path.join(OUT_DIR, "环境示例.blend")

DAY = "白天"
NIGHT = "幽暗"


def log(message):
    print(f"[环境示例] {message}")


def apply_settings(scene, pairs):
    """把一批导出设置写进场景的 gk_settings(缺属性 / 只读就跳过)。"""
    settings = getattr(scene, "gk_settings", None)
    if settings is None:
        return
    for attr, value in pairs:
        if hasattr(settings, attr):
            try:
                setattr(settings, attr, value)
            except Exception:                             # noqa: BLE001
                pass


def clear_all():
    """清空所有数据块(不能用 read_factory_settings —— 会把插件一起卸掉)。"""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)
    for light in list(bpy.data.lights):
        bpy.data.lights.remove(light)
    for camera in list(bpy.data.cameras):
        bpy.data.cameras.remove(camera)


def make_box(name, size, location, color=None, scene=None):
    sx, sy, sz = [v * 0.5 for v in size]
    verts = [(-sx, -sy, -sz), (sx, -sy, -sz), (sx, sy, -sz), (-sx, sy, -sz),
             (-sx, -sy, sz), (sx, -sy, sz), (sx, sy, sz), (-sx, sy, sz)]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5),
             (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh = bpy.data.meshes.new(f"{name}网格")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    (scene or bpy.context.scene).collection.objects.link(obj)
    obj.location = location
    if color is not None:
        material = bpy.data.materials.new(f"{name}材质")
        material.use_nodes = True
        principled = material.node_tree.nodes.get("Principled BSDF")
        if principled is not None:
            principled.inputs["Base Color"].default_value = color
        obj.data.materials.append(material)
    return obj


def make_camera(name, location, rotation, scene=None):
    data = bpy.data.cameras.new(f"{name}数据")
    camera = bpy.data.objects.new(name, data)
    (scene or bpy.context.scene).collection.objects.link(camera)
    camera.location = location
    camera.rotation_euler = rotation
    return camera


def make_light(name, kind, location, energy, color, rotation=None,
               cutoff=0.0, spot_size=0.0, use_shadow=True, scene=None):
    data = bpy.data.lights.new(f"{name}数据", type=kind)
    data.energy = energy
    data.color = color
    if kind in ("POINT", "SPOT") and cutoff > 0.0:
        data.cutoff_distance = cutoff
    if kind == "SPOT":
        data.spot_size = spot_size
    data.use_shadow = use_shadow
    light = bpy.data.objects.new(name, data)
    (scene or bpy.context.scene).collection.objects.link(light)
    light.location = location
    if rotation is not None:
        light.rotation_euler = rotation
    return light


def build_geometry(scene):
    """两关共用的几何:地面 + 几根柱子(好让雾 / 泛光 / 环境光看得出来)。

    ⚠ 名字带场景名后缀 —— 两个场景的物体在 `bpy.data` 里是**同一个命名空间**,
    都叫"地面 / 侧视机位"时 Blender 会给第二关自动加 `.001`,而且
    `scene.camera` 一旦指向"另一个场景里的那台相机",导出侧就找不到相机了。
    所以名字带前缀,并且用 make_camera 的返回值(而不是按名字去 bpy.data 里捞)。
    """
    prefix = scene.name
    make_box(f"{prefix}_地面", (24.0, 24.0, 0.4), (0.0, 0.0, -0.2),
             (0.35, 0.36, 0.40, 1.0), scene=scene)
    positions = [(-4.0, 3.0), (0.0, 6.0), (4.0, 3.0), (-6.0, -2.0), (6.0, -2.0)]
    for index, (x, y) in enumerate(positions):
        make_box(f"{prefix}_柱子{index + 1}", (1.2, 1.2, 4.0), (x, y, 2.0),
                 (0.55, 0.50, 0.45, 1.0), scene=scene)
    camera = make_camera(f"{prefix}_侧视机位", (0.0, -18.0, 5.0),
                         (math.radians(74.0), 0.0, 0.0), scene=scene)
    scene.camera = camera


def build_day(scene):
    """白天:程序化天空 + 亮环境光 + Filmic,没有雾也没有泛光。"""
    build_geometry(scene)
    make_light("太阳", "SUN", (0.0, 0.0, 8.0), 3.0, (1.0, 0.96, 0.88),
               rotation=(math.radians(50.0), 0.0, math.radians(30.0)),
               scene=scene)
    apply_settings(scene, (
        ("env_enabled", True),
        ("env_background", "SKY"),
        ("env_bg_color", (0.35, 0.55, 0.85, 1.0)),
        ("env_ambient_color", (0.70, 0.78, 0.92, 1.0)),
        ("env_ambient_energy", 0.8),
        ("env_fog", False),
        ("env_glow", False),
        ("env_tonemap", "AGX"),
        ("env_adjust", False),
    ))
    log(f"{DAY}:天空背景 + 环境光 0.8 + AgX(第九十五轮:和 Blender 默认"
        f"视图变换同名同族,预览↔运行时能精确对齐;雾 / 泛光都关)")


def build_night(scene):
    """幽暗:近黑的纯色背景 + 冷雾 + 泛光 + ACES,只有一盏弱月光和台灯。"""
    build_geometry(scene)
    make_light("月光", "SUN", (0.0, 0.0, 6.0), 0.25, (0.55, 0.65, 1.0),
               rotation=(math.radians(60.0), 0.0, math.radians(40.0)),
               scene=scene)
    make_light("台灯", "POINT", (5.0, 3.0, 1.1), 900.0, (1.0, 0.65, 0.35),
               cutoff=9.0, scene=scene)
    make_light("手电筒", "SPOT", (0.0, 1.5, 3.0), 1600.0, (1.0, 0.98, 0.9),
               rotation=(math.radians(80.0), 0.0, 0.0), cutoff=16.0,
               spot_size=math.radians(40.0), use_shadow=False, scene=scene)
    apply_settings(scene, (
        ("env_enabled", True),
        ("env_background", "COLOR"),
        ("env_bg_color", (0.03, 0.04, 0.07, 1.0)),
        ("env_ambient_color", (0.45, 0.52, 0.72, 1.0)),
        ("env_ambient_energy", 0.15),
        ("env_fog", True),
        ("env_fog_color", (0.24, 0.29, 0.40, 1.0)),
        ("env_fog_density", 0.08),
        ("env_glow", True),
        ("env_glow_intensity", 1.2),
        ("env_glow_bloom", 0.2),
        ("env_tonemap", "ACES"),
        ("env_adjust", False),
    ))
    physics = getattr(bpy.data.objects.get("手电筒"), "gk_physics", None)
    if physics is not None:
        physics.light_follow_camera = True
    log(f"{NIGHT}:近黑背景 + 冷雾 0.08 + 泛光 1.2 + ACES + 手电筒跟随相机")


def save_blend(path):
    if not bpy.app.background:
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return
    blocks = set(bpy.data.objects) | set(bpy.data.meshes) | \
        set(bpy.data.materials) | set(bpy.data.node_groups) | \
        set(bpy.data.cameras) | set(bpy.data.lights)
    scenes = list(bpy.data.scenes)
    bpy.data.libraries.write(path, blocks | set(scenes), fake_user=True)


def main():
    from BlenderGameKit import asset_export, project

    os.makedirs(OUT_DIR, exist_ok=True)
    clear_all()

    day = bpy.context.scene
    day.name = DAY
    build_day(day)

    night = bpy.data.scenes.new(NIGHT)
    apply_settings(night, (("dimension", "3"),))
    build_night(night)

    directory = os.path.join(OUT_DIR, "环境示例_godot")
    project.create_project(directory, title="环境示例",
                           blend_file_name=os.path.basename(BLEND_PATH))
    result = asset_export.export_all_scenes(directory, dimension="3")
    if result.get("error"):
        log(f"导出失败:{result['error']}")
        return 1
    for entry in result.get("scenes", []):
        log(f"场景 {entry['scene']} -> {entry['scene_name']}.tscn")
    for warning in result.get("warnings", []):
        log(f"告警: {warning}")
    if result.get("deleted"):
        log(f"清理旧文件: {result['deleted']}")

    save_blend(BLEND_PATH)
    log(f"已保存 {BLEND_PATH}")
    log(f"工程目录: {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
