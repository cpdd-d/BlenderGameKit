"""动画混合树示例(第六十一轮)—— 一个会"走↔跑"连续过渡的方块。

用法:
    blender -b --python scripts\\make_blend_demo.py
产出:
    examples/demo_blend/混合示例.blend
    examples/demo_blend/混合示例_godot/   (可用 Godot 直接打开)

── 这一屏在看什么 ────────────────────────────────────────────────
一个方块在**上下轻晃**。两块动画:**『走』晃 0.2 米**、**『跑』晃 2 米**。
按 **1** 切到"走"、按 **2** 切到"跑" —— 注意:它不是"啪"地换过去,
而是**连续混过去**(参数从 0 滑到 1,姿势是两段动画按比例叠的)。
这就是混合树相对状态机(硬切)的意义。

| 用到的 | 在游戏里怎么看 |
|---|---|
| **动画_混合树** | 建一个 AnimationTree + BlendSpace1D,混合点 `走:0\\|跑:1` |
| **混合位置变量** | 『参数』引脚接了 `混合位置`;按 1 / 2 改它(0 / 1) |
| **变量_设置 + 常量_数值** | 按键把它设成 0 或 1 |
| **AnimationTree(真 Godot 资源)** | 运行时建;探针会读它的 blend_position 与方块高度 |

按键:1 = 走(小晃)、2 = 跑(大晃)。

跑法(Godot):打开 `混合示例_godot` 直接 F5。
自动检查(不用 Blender,要 Godot):`python scripts\\check_blend_demo.py`
"""

import math
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "examples", "demo_blend")
BLEND_PATH = os.path.join(OUT_DIR, "混合示例.blend")

BOX = "演示方块"
WALK_ANIM = "走"
RUN_ANIM = "跑"
BLEND_VAR = "混合位置"
KEY_WALK = "1"
KEY_RUN = "2"

# 两段动画的关键帧(帧 -> 相对高度)。同样的时长、不同的幅度 ——
# 参数 0/1 时应该是"小晃/大晃",中间值叠出中间的幅度。
WALK_FRAMES = ((1, 0.0), (12, 0.2), (24, 0.0))
RUN_FRAMES = ((1, 0.0), (12, 2.0), (24, 0.0))


def log(message):
    print(f"[混合示例] {message}")


def clear_scene():
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for group in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(group)
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    scene.name = "混合示例"
    return scene


def make_box(name, size, location, color=None):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if color is not None:
        material = bpy.data.materials.new(f"{name}_材质")
        material.use_nodes = True
        principled = material.node_tree.nodes.get("Principled BSDF")
        if principled is not None:
            principled.inputs["Base Color"].default_value = color
        obj.data.materials.append(material)
    return obj


def make_camera(name, location, rotation):
    data = bpy.data.cameras.new(f"{name}数据")
    camera = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = location
    camera.rotation_euler = rotation
    return camera


def add_anim(obj, name, frames):
    """给对象打一段**物体动画**(帧 -> 高度),并推成 NLA 轨道。

    ⚠ 只留 `animation_data.action` 的话,**只有最后一段**会进 glb ——
    实测导出的 glb 里只剩一个动画("跑")。glTF 导出器按"动作 + NLA 轨道"
    导出多段,所以这里每段都推一条轨道,然后把活动动作清掉。
    """
    if obj.animation_data is None:
        obj.animation_data_create()
    action = bpy.data.actions.new(name)
    action.use_fake_user = True
    obj.animation_data.action = action
    base = obj.location.copy()
    for frame, height in frames:
        obj.location = (base.x, base.y, base.z + height)
        obj.keyframe_insert("location", frame=frame)
    obj.location = base
    track = obj.animation_data.nla_tracks.new()
    track.name = name
    strip = track.strips.new(name, int(action.frame_range[0]), action)
    strip.name = name
    obj.animation_data.action = None
    return action


def build_scene():
    scene = clear_scene()
    make_box("地面", (20.0, 20.0, 0.4), (0.0, 0.0, -0.2),
             (0.16, 0.18, 0.22, 1.0))
    box = make_box(BOX, (1.4, 1.4, 1.4), (0.0, 0.0, 0.9),
                   (0.85, 0.60, 0.25, 1.0))
    add_anim(box, WALK_ANIM, WALK_FRAMES)
    add_anim(box, RUN_ANIM, RUN_FRAMES)
    # 摆回原样:动画只在播的时候改它
    box.location = (0.0, 0.0, 0.9)
    side = make_camera("侧视机位", (0.0, -14.0, 4.0),
                       (math.radians(76.0), 0.0, 0.0))
    make_camera("俯瞰机位", (0.0, -6.0, 13.0), (math.radians(24.0), 0.0, 0.0))
    scene.camera = side
    light_data = bpy.data.lights.new("太阳光数据", type="SUN")
    light_data.energy = 3.0
    light = bpy.data.objects.new("太阳光", light_data)
    scene.collection.objects.link(light)
    light.location = (5.0, -5.0, 10.0)
    light.rotation_euler = (math.radians(52.0), 0.0, math.radians(28.0))
    log(f"场景:地面 + {BOX}(两段动画 {WALK_ANIM} / {RUN_ANIM})+ 两台相机")
    return scene, box


def write_physics(scene, box):
    for obj in scene.objects:
        physics = getattr(obj, "gk_physics", None)
        if physics is None:
            continue
        try:
            physics.shape_size = (0.0, 0.0, 0.0)
        except Exception:                                 # noqa: BLE001
            pass
        if obj is box:
            physics.body_type = "CharacterBody3D"
            physics.dimension = "3"
            physics.script_target = True
        elif getattr(obj, "type", "") == "MESH":
            physics.body_type = "StaticBody3D"
            physics.dimension = "3"


def _add(tree, kind, name, x, y, **props):
    node = tree.nodes.new(kind)
    node.name = name
    node.location = (x, y)
    for key, value in props.items():
        setattr(node, key, value)
    return node


def _link(tree, source, source_pin, target, target_pin):
    tree.links.new(source.outputs[source_pin], target.inputs[target_pin])


def _make_key_branch(tree, key, action_name, value, x, y):
    """一个物理帧 + 按某个键 -> 把『混合位置』设成 value。"""
    physics = _add(tree, "GKNodeOnPhysicsProcess", f"物理帧({action_name})",
                   x, y)
    key_node = _add(tree, "GKNodeKey", f"按{key}", x + 240.0, y,
                    gk_key=key, gk_action=action_name, gk_trigger="PRESSED")
    branch = _add(tree, "GKNodeBranch", f"按了{key}?", x + 440.0, y)
    const = _add(tree, "GKNodeConstFloat", f"值{value}", x + 440.0, y - 200.0,
                 gk_value=value)
    setter = _add(tree, "GKNodeSetVar", f"混合位置={value}", x + 660.0, y,
                  gk_var_name=BLEND_VAR)
    _link(tree, key_node, "结果", branch, "条件")
    _link(tree, physics, "执行", branch, "执行")
    _link(tree, const, "值", setter, "值")
    _link(tree, branch, "是", setter, "执行")


def build_graph(scene, box):
    """玩法图:建混合树 + 两个按键改混合位置。"""
    logic = bpy.data.node_groups.new("混合演示逻辑", "GKGameLogicTree")
    logic.use_fake_user = True
    ready = _add(logic, "GKNodeOnReady", "准备", -400.0, 600.0)
    hint = _add(logic, "GKNodeUiText", "提示", -160.0, 600.0,
                gk_text="按 1 = 走(小晃)  /  按 2 = 跑(大晃)",
                gk_x=18.0, gk_y=18.0, gk_size=20)
    read = _add(logic, "GKNodeGetVar", "读混合位置", -160.0, 400.0,
                gk_var_name=BLEND_VAR)
    blend = _add(logic, "GKNodeBlendTree", "走路跑步", 120.0, 600.0,
                 gk_points=f"{WALK_ANIM}:0|{RUN_ANIM}:1",
                 gk_smooth=0.0, gk_debug_print=True)
    _link(logic, ready, "执行", hint, "执行")
    _link(logic, hint, "完成", blend, "执行")
    _link(logic, read, "值", blend, "参数")

    _make_key_branch(logic, KEY_WALK, "key_1", 0.0, -400.0, 200.0)
    _make_key_branch(logic, KEY_RUN, "key_2", 1.0, -400.0, -200.0)
    log(f"玩法图『{logic.name}』:混合树({WALK_ANIM}:0|{RUN_ANIM}:1)"
        f" + 按 1/2 改『{BLEND_VAR}』")
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
    blocks = set(bpy.data.objects) | set(bpy.data.meshes) | \
        set(bpy.data.materials) | set(bpy.data.node_groups) | \
        set(bpy.data.cameras) | set(bpy.data.lights) | set(bpy.data.actions)
    scenes = list(bpy.data.scenes)
    bpy.data.libraries.write(path, blocks | set(scenes), fake_user=True)


def main():
    from BlenderGameKit import asset_export, project

    os.makedirs(OUT_DIR, exist_ok=True)
    scene, box = build_scene()
    write_physics(scene, box)
    build_graph(scene, box)
    _apply_settings(scene, (("dimension", "3"),
                            ("base_class", "CharacterBody3D"),
                            ("logic_tree", "混合演示逻辑"),
                            ("god_camera", True)))

    directory = os.path.join(OUT_DIR, "混合示例_godot")
    project.create_project(directory, title="混合示例",
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

    save_blend(BLEND_PATH)
    log(f"已保存 {BLEND_PATH}")
    log(f"工程目录: {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())