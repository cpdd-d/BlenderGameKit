# -*- coding: utf-8 -*-
"""生成"我的世界"风格的第一人称方块示例(第一百九十三轮)。

不需要 Blender:走插件自带的离线打桩通道,用**真的节点类**建图,
再用真的 codegen 生成 GDScript,最后写一份可以直接按 F5 的 Godot 工程。

    python scripts\\make_minecraft_demo.py
    godot --path examples\\demo_minecraft

玩法:
  WASD 移动 / 空格跳 / 鼠标转视角 / 左键破坏准星指着的方块 / ESC 退出。

它检查的东西:
  * 3D 第一人称:『相机_旋转』+『移动_按方向(随相机朝向)』;
  * 重力 + 跳跃:『在地面』+『输入_按键』+『逻辑与』+『移动_跳跃』;
  * 射线交互:『射线_检测(当前相机 / 相机前向)』+『实例_销毁』;
  * 流程串联:『序列』把转视角 / 挖方块 / ESC 三条每帧逻辑串起来;
  * 输入映射:move_* + jump 自动写进 project.godot。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import gk_stub  # noqa: E402

gk_stub.install()
ADDON = gk_stub.load_addon(os.path.join(ROOT, "BlenderGameKit"))

from BlenderGameKit import codegen, exporter  # noqa: E402
from BlenderGameKit.nodes import nodes as NN  # noqa: E402

OUT_DIR = os.path.join(ROOT, "examples", "demo_minecraft")
SCRIPT_PATH = os.path.join(OUT_DIR, "scripts", "game_logic.gd")

# 准星指到的方块会被『实例_销毁』移走;世界用 10x10 个 1 米方块铺成,
# 再加一棵树、一个小石台和一片沙地。方块是独立 StaticBody3D,
# 所以射线打中的是"一块",不是整片地面。
GROUND = 10
BLOCK_MATS = ("grass", "dirt", "stone", "wood", "leaves", "sand")


# ==========================================================================
# 节点图
# ==========================================================================
def build_tree():
    tree = gk_stub.StubNodeTree("我的世界")

    def add(cls, name, x, y, **props):
        return tree.new_node(cls, name, location=(x, y), **props)

    def link(src, out_name, dst, in_name):
        gk_stub.connect(tree, src, out_name, dst, in_name)

    # ---- _ready:锁定鼠标 + 操作说明 ----
    ready = add(NN.GKNodeOnReady, "开始", -1600.0, 600.0)
    mouse_mode = add(NN.GKNodeMouseMode, "锁定鼠标", -1300.0, 600.0,
                     gk_mode="CAPTURED")
    hint = add(NN.GKNodePrint, "操作说明", -1000.0, 600.0,
               gk_text=("我的世界示例:WASD 移动 / 空格 跳 / 鼠标 转视角 / "
                        "左键或 E 挖方块 / 右键或 Q 放方块 / ESC 退出"))
    link(ready, "执行", mouse_mode, "执行")
    link(mouse_mode, "完成", hint, "执行")

    # ---- _process:序列(转视角 / 挖方块 / ESC) ----
    process = add(NN.GKNodeOnProcess, "每帧", -1600.0, 200.0)
    sequence = add(NN.GKNodeSequence, "每帧三件事", -1300.0, 200.0,
                   gk_step_count=3)
    link(process, "执行", sequence, "执行")

    # 1) 鼠标移动 -> 相机偏航 / 俯仰
    mouse = add(NN.GKNodeMouseMotion, "鼠标移动", -1600.0, -60.0)
    split = add(NN.GKNodeVectorSplit, "拆 XY", -1350.0, -60.0)
    yaw = add(NN.GKNodeMath, "左右灵敏度", -1100.0, -60.0,
              gk_op="MUL", gk_b=0.12)
    pitch = add(NN.GKNodeMath, "上下灵敏度", -1100.0, -240.0,
                gk_op="MUL", gk_b=0.12)
    camera = add(NN.GKNodeCameraRotation, "第一人称转视角", -850.0, -120.0,
                 gk_target="第一人称相机", gk_mode="ADD",
                 gk_smooth=0.0, gk_clamp_pitch=True)
    link(sequence, "1", camera, "执行")
    link(mouse, "移动量", split, "向量")
    link(split, "X", yaw, "A")
    link(split, "Y", pitch, "A")
    link(yaw, "结果", camera, "偏航")
    link(pitch, "结果", camera, "俯仰")

    # 2) 左键 / E -> 射线 -> 销毁打中的方块;右键 / Q -> 在命中点放一块
    ray = add(NN.GKNodeRaycast, "准星射线", -1100.0, -520.0,
              gk_origin="CAMERA", gk_dir_mode="CAMERA_FORWARD",
              gk_distance=6.0, gk_layer=1)
    hit_seq = add(NN.GKNodeSequence, "命中后两件事", -850.0, -560.0,
                  gk_step_count=2)
    left = add(NN.GKNodeMouseButtonDown, "左键刚按下", -1100.0, -700.0,
               gk_button="LEFT")
    mine_key = add(NN.GKNodeKey, "E 也能挖", -1100.0, -820.0,
                   gk_key="E", gk_action="mine", gk_trigger="JUST")
    mine_or = add(NN.GKNodeLogic, "左键或 E", -850.0, -760.0, gk_op="OR")
    hit_branch = add(NN.GKNodeBranch, "打中了吗", -850.0, -560.0)
    destroy = add(NN.GKNodeQueueFree, "挖掉方块", -600.0, -520.0)
    link(sequence, "2", ray, "执行")
    link(ray, "命中", hit_seq, "执行")
    link(hit_seq, "1", hit_branch, "执行")
    link(left, "刚按下", mine_or, "A")
    link(mine_key, "结果", mine_or, "B")
    link(mine_or, "结果", hit_branch, "条件")
    link(hit_branch, "是", destroy, "执行")
    link(ray, "打到的东西", destroy, "目标")

    right = add(NN.GKNodeMouseButtonDown, "右键刚按下", -1100.0, -1000.0,
                gk_button="RIGHT")
    place_key = add(NN.GKNodeKey, "Q 也能放", -1100.0, -1120.0,
                    gk_key="Q", gk_action="place", gk_trigger="JUST")
    place_or = add(NN.GKNodeLogic, "右键或 Q", -850.0, -1060.0, gk_op="OR")
    place_branch = add(NN.GKNodeBranch, "要放吗", -620.0, -1000.0)
    spawn = add(NN.GKNodeAssetSpawn, "放一块", -360.0, -1000.0,
                gk_asset="方块", gk_solid=True)
    split_hit = add(NN.GKNodeVectorSplit, "拆命中点", -1100.0, -1300.0)
    lift = add(NN.GKNodeMath, "抬半格", -850.0, -1300.0,
               gk_op="ADD", gk_b=0.5)
    combine = add(NN.GKNodeVectorMake, "合成位置", -620.0, -1300.0)
    link(hit_seq, "2", place_branch, "执行")
    link(right, "刚按下", place_or, "A")
    link(place_key, "结果", place_or, "B")
    link(place_or, "结果", place_branch, "条件")
    link(place_branch, "是", spawn, "执行")
    link(ray, "命中点", split_hit, "向量")
    link(split_hit, "X", combine, "X")
    link(split_hit, "Z", combine, "Z")
    link(split_hit, "Y", lift, "A")
    link(lift, "结果", combine, "Y")
    link(combine, "向量", spawn, "位置")

    # 3) ESC -> 退出
    esc = add(NN.GKNodeKey, "ESC", -1100.0, -900.0,
              gk_key="ESC", gk_action="ui_cancel", gk_trigger="JUST")
    esc_branch = add(NN.GKNodeBranch, "要退出吗", -850.0, -900.0)
    quit_game = add(NN.GKNodeQuitGame, "退出", -600.0, -900.0)
    link(sequence, "3", esc_branch, "执行")
    link(esc, "结果", esc_branch, "条件")
    link(esc_branch, "是", quit_game, "执行")

    # ---- _physics_process:移动 + 跳跃 ----
    physics = add(NN.GKNodeOnPhysicsProcess, "物理帧", -1600.0, -1200.0)
    direction = add(NN.GKNodeInputDirection, "WASD", -1600.0, -1450.0)
    move = add(NN.GKNodeMoveDirection, "移动", -1300.0, -1200.0,
               gk_speed=6.0, gk_keep_y=True, gk_follow_camera=True,
               gk_face_move=False)
    on_floor = add(NN.GKNodeIsOnFloor, "在地面", -1300.0, -1500.0)
    jump_key = add(NN.GKNodeKey, "空格", -1300.0, -1680.0,
                   gk_action="jump", gk_trigger="JUST")
    both = add(NN.GKNodeLogic, "能跳吗", -1050.0, -1550.0, gk_op="AND")
    jump_branch = add(NN.GKNodeBranch, "如果", -800.0, -1200.0)
    jump = add(NN.GKNodeJump, "跳跃", -550.0, -1200.0, gk_jump_force=8.5)

    link(physics, "执行", move, "执行")
    link(direction, "方向", move, "方向")
    link(move, "完成", jump_branch, "执行")
    link(on_floor, "在地面", both, "A")
    link(jump_key, "结果", both, "B")
    link(both, "结果", jump_branch, "条件")
    link(jump_branch, "是", jump, "执行")
    return tree


# ==========================================================================
# Godot 工程
# ==========================================================================
PROJECT_GODOT = """; 由 BlenderGameKit 生成的"我的世界"风格示例
; 用 Godot 打开这个目录，按 F5 就能玩

config_version=5

[application]

config/name="BlenderGameKit 我的世界示例"
run/main_scene="res://Main.tscn"
config/features=PackedStringArray("4.4", "Forward Plus")

[display]

window/size/viewport_width=1280
window/size/viewport_height=720

[input]

move_left={
"deadzone": 0.2,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":65,"key_label":0,"unicode":0,"location":0,"echo":false,"script":null)
, Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":4194319,"key_label":0,"unicode":0,"location":0,"echo":false,"script":null)
]
}
move_right={
"deadzone": 0.2,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":68,"key_label":0,"unicode":0,"location":0,"echo":false,"script":null)
, Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":4194321,"key_label":0,"unicode":0,"location":0,"echo":false,"script":null)
]
}
move_up={
"deadzone": 0.2,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":87,"key_label":0,"unicode":0,"location":0,"echo":false,"script":null)
]
}
move_down={
"deadzone": 0.2,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":83,"key_label":0,"unicode":0,"location":0,"echo":false,"script":null)
]
}
jump={
"deadzone": 0.2,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":32,"key_label":0,"unicode":0,"location":0,"echo":false,"script":null)
]
}
mine={
"deadzone": 0.2,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":69,"key_label":0,"unicode":0,"location":0,"echo":false,"script":null)
]
}
place={
"deadzone": 0.2,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":81,"key_label":0,"unicode":0,"location":0,"echo":false,"script":null)
]
}

[layer_names]

3d_physics/layer_1="blocks"
3d_physics/layer_2="player"

[rendering]

renderer/rendering_method="gl_compatibility"
"""


def _material(name, color):
    return (f'[sub_resource type="StandardMaterial3D" id="mat_{name}"]\n'
            f"albedo_color = Color({color[0]}, {color[1]}, {color[2]}, 1)\n"
            f"roughness = 0.9\n")


def _block(name, x, y, z, material):
    return (f'[node name="{name}" type="StaticBody3D" parent="Blocks"]\n'
            f"transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, "
            f"{x}, {y}, {z})\n\n"
            f'[node name="Shape" type="CollisionShape3D" parent="Blocks/{name}"]\n'
            f'shape = SubResource("block_shape")\n\n'
            f'[node name="Mesh" type="MeshInstance3D" parent="Blocks/{name}"]\n'
            f'mesh = SubResource("block_mesh")\n'
            f'material_override = SubResource("mat_{material}")\n')


def build_scene():
    blocks = []
    # 地面:10x10 草方块,中心在 (0,0.5,0)
    half = GROUND // 2
    for gx in range(-half, half):
        for gz in range(-half, half):
            blocks.append(_block(f"Ground_{gx}_{gz}", gx + 0.5, 0.5, gz + 0.5,
                                 "grass"))
    # 一条沙地
    for gx in range(1, 4):
        for gz in range(-4, -1):
            blocks.append(_block(f"Sand_{gx}_{gz}", gx + 0.5, 0.5, gz + 0.5,
                                 "sand"))
    # 小石台
    for gx in range(2, 5):
        for gz in range(2, 5):
            blocks.append(_block(f"Stone_{gx}_{gz}", gx + 0.5, 1.5, gz + 0.5,
                                 "stone"))
    # 一棵树
    blocks.append(_block("TreeTrunk_0", -3.5, 1.5, -3.5, "wood"))
    blocks.append(_block("TreeTrunk_1", -3.5, 2.5, -3.5, "wood"))
    blocks.append(_block("TreeTrunk_2", -3.5, 3.5, -3.5, "wood"))
    for dx in (-1, 0, 1):
        for dz in (-1, 0, 1):
            blocks.append(_block(f"LeafA_{dx}_{dz}", -3.5 + dx, 4.5,
                                 -3.5 + dz, "leaves"))
    for dx in (-1, 0, 1):
        for dz in (-1, 0, 1):
            if dx == 0 and dz == 0:
                continue
            blocks.append(_block(f"LeafB_{dx}_{dz}", -3.5 + dx, 5.5,
                                 -3.5 + dz, "leaves"))
    # 一根柱子 / 一个门框,让画面里有竖线
    blocks.append(_block("Pillar", 4.5, 1.5, -2.5, "stone"))
    blocks.append(_block("Pillar2", 4.5, 2.5, -2.5, "stone"))
    blocks.append(_block("ArchL", -1.5, 1.5, 4.5, "wood"))
    blocks.append(_block("ArchR", 0.5, 1.5, 4.5, "wood"))
    blocks.append(_block("ArchTop", -0.5, 2.5, 4.5, "wood"))

    subs = [
        '[sub_resource type="BoxShape3D" id="block_shape"]\n'
        "size = Vector3(1, 1, 1)",
        '[sub_resource type="BoxMesh" id="block_mesh"]\n'
        "size = Vector3(1, 1, 1)",
        _material("grass", (0.24, 0.60, 0.22)),
        _material("dirt", (0.42, 0.30, 0.18)),
        _material("stone", (0.46, 0.48, 0.52)),
        _material("wood", (0.42, 0.27, 0.12)),
        _material("leaves", (0.16, 0.52, 0.20)),
        _material("sand", (0.80, 0.72, 0.46)),
        '[sub_resource type="CapsuleShape3D" id="player_shape"]\n'
        "radius = 0.35\nheight = 1.7",
        '[sub_resource type="Environment" id="env"]\n'
        "background_mode = 1\n"
        "background_color = Color(0.55, 0.72, 0.95, 1)\n"
        "ambient_light_source = 2\n"
        "ambient_light_color = Color(0.55, 0.62, 0.75, 1)\n"
        "ambient_light_energy = 0.55\n"
        "tonemap_mode = 2",
    ]
    ext = [
        '[ext_resource type="Script" path="res://scripts/game_logic.gd" '
        'id="script"]',
    ]
    steps = len(subs) + len(ext) + 1
    parts = [f"[gd_scene load_steps={steps} format=3]", ""]
    parts += ext
    parts.append("")
    parts += subs
    parts.append("")
    parts.append('[node name="Main" type="Node3D"]')
    parts.append("")
    parts.append('[node name="WorldEnvironment" type="WorldEnvironment" '
                 'parent="."]')
    parts.append('environment = SubResource("env")')
    parts.append("")
    parts.append('[node name="Sun" type="DirectionalLight3D" parent="."]')
    parts.append("transform = Transform3D(0.86, -0.35, 0.37, 0, 0.73, 0.68, "
                 "-0.51, -0.59, 0.63, 4, 8, 4)")
    parts.append("light_energy = 1.2")
    parts.append("shadow_enabled = true")
    parts.append("")
    parts.append('[node name="Blocks" type="Node3D" parent="."]')
    parts.append("")
    parts += blocks
    parts.append('[node name="Player" type="CharacterBody3D" parent="."]')
    parts.append("transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0.5, 1, 0.5)")
    parts.append("collision_layer = 2")
    parts.append("collision_mask = 1")
    parts.append('script = ExtResource("script")')
    parts.append("")
    parts.append('[node name="Shape" type="CollisionShape3D" parent="Player"]')
    parts.append("transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0.85, 0)")
    parts.append('shape = SubResource("player_shape")')
    parts.append("")
    parts.append('[node name="第一人称相机" type="Camera3D" parent="Player"]')
    parts.append("transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1.6, 0)")
    parts.append("fov = 75.0")
    parts.append("near = 0.05")
    parts.append("far = 200.0")
    parts.append("current = true")
    parts.append("")
    parts.append('[node name="UI" type="CanvasLayer" parent="."]')
    parts.append("")
    parts.append('[node name="Info" type="Label" parent="UI"]')
    parts.append("offset_left = 16.0")
    parts.append("offset_top = 12.0")
    parts.append("theme_override_font_sizes/font_size = 16")
    parts.append('text = "我的世界 · BlenderGameKit 示例    '
                 'WASD 移动 / 空格 跳 / 鼠标 转视角 / 左键或 E 挖 / 右键或 Q 放 / ESC 退出"')
    parts.append("")
    parts.append('[node name="Crosshair" type="Label" parent="UI"]')
    parts.append("anchors_preset = 8")
    parts.append("anchor_left = 0.5")
    parts.append("anchor_top = 0.5")
    parts.append("anchor_right = 0.5")
    parts.append("anchor_bottom = 0.5")
    parts.append("offset_left = -8.0")
    parts.append("offset_top = -12.0")
    parts.append("offset_right = 8.0")
    parts.append("offset_bottom = 12.0")
    parts.append("theme_override_font_sizes/font_size = 22")
    parts.append('text = "+"')
    parts.append("horizontal_alignment = 1")
    return "\n".join(parts) + "\n"


README = """\
# 我的世界 · BlenderGameKit 示例

第一人称方块世界,逻辑全部来自节点图(`scripts/game_logic.gd` 是生成的)。

## 玩法

- WASD 移动,空格跳;
- 鼠标转视角(鼠标已被锁定);
- 左键或 E 破坏准星指着的方块;
- 右键或 Q 在准星命中的方块上放一块;
- ESC 退出。

## 用到的节点

| 功能 | 节点 |
|---|---|
| 第一人称视角 | 『鼠标_移动量』+『向量_拆分』+『数学_运算』+『相机_旋转』 |
| 移动 / 重力 / 跳跃 | 『输入_方向』+『移动_按方向(随相机朝向 / 重力模式)』+『移动_跳跃』 |
| 挖方块 | 『射线_检测(当前相机 / 相机前向)』+『鼠标_按键按下』+『实例_销毁』 |
| 放方块 | 『射线_检测』的命中点 +『向量_拆分/合并』+『资产_创建实例』(Block.tscn) |
| 每帧三件事 | 『序列』(转视角 / 挖方块 / ESC) |
| 鼠标锁定 | 『鼠标_模式(锁定)』 |

## 重新生成

```powershell
python scripts\\make_minecraft_demo.py
```

方块不是资产,而是场景里独立的 `StaticBody3D` —— 射线打中的是一块,
所以『实例_销毁』只会移除那一块。想加方块就在 `Main.tscn` 的 `Blocks`
下面复制一个,或者改 `make_minecraft_demo.py` 里的 `blocks` 列表。
"""


BLOCK_SCENE = """[gd_scene load_steps=3 format=3]

[sub_resource type="BoxMesh" id="mesh"]
size = Vector3(1, 1, 1)

[sub_resource type="StandardMaterial3D" id="mat"]
albedo_color = Color(0.55, 0.42, 0.22, 1)
roughness = 0.9

[node name="Block" type="Node3D"]

[node name="Mesh" type="MeshInstance3D" parent="."]
mesh = SubResource("mesh")
material_override = SubResource("mat")
"""


def main():
    tree = build_tree()
    assets = {
        "方块": {
            "path": "res://Block.tscn",
            "shape": 0,
            "shapes": [{"kind": 0, "dims": (1.0, 1.0, 1.0),
                        "offset": (0.0, 0.0, 0.0)}],
            "layer": 1,
            "mask": 1,
        },
    }
    code, warnings = codegen.generate(
        tree, dict(dimension="3", base_class="CharacterBody3D",
                   class_name="MinecraftPlayer", export_tunables=True,
                   assets=assets))
    for warning in warnings:
        print("警告:", warning)
    os.makedirs(os.path.dirname(SCRIPT_PATH), exist_ok=True)
    exporter.write_text(SCRIPT_PATH, code)
    exporter.write_text(os.path.join(OUT_DIR, "Main.tscn"), build_scene())
    exporter.write_text(os.path.join(OUT_DIR, "Block.tscn"), BLOCK_SCENE)
    exporter.write_text(os.path.join(OUT_DIR, "project.godot"), PROJECT_GODOT)
    exporter.write_text(os.path.join(OUT_DIR, "README.md"), README)
    print(f"已生成 {OUT_DIR}")
    print(f"  脚本 {len(code.splitlines())} 行")
    print("  Main.tscn:10x10 方块地面 + 树 + 石台 + 沙地 + 第一人称玩家")
    print("  Block.tscn:右键 / Q 放置时用的方块资产")


if __name__ == "__main__":
    main()
