"""生成一个可直接运行的 Godot 4 示例工程(2D 横版)。

这个脚本用同一套节点定义 + 代码生成器，把一张"玩家角色"节点图导出成
一个完整、能用 Godot 打开的工程:

    examples/demo_2d/
        project.godot          工程文件(含输入映射)
        Main.tscn              主场景:玩家 + 地面
        Player.tscn            玩家场景(挂了生成的脚本)
        scripts/game_logic.gd  由节点图生成的脚本
        art/player.png         占位贴图(纯 Python 写出的 PNG)

不需要 Blender:走的是插件自带的离线打桩通道。
用法(用 Blender 自带的 Python 最省事):
    python scripts/make_demo.py
"""

import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import gk_stub  # noqa: E402

gk_stub.install()
ADDON = gk_stub.load_addon(os.path.join(ROOT, "BlenderGameKit"))

from BlenderGameKit import codegen, exporter  # noqa: E402
from BlenderGameKit.nodes import nodes as NN  # noqa: E402

OUT_DIR = os.path.join(ROOT, "examples", "demo_2d")


# ==========================================================================
# 占位贴图:手写一个最小 PNG，避免依赖 Pillow
# ==========================================================================
def write_png(path, width, height, pixels):
    """pixels: 每项是 (r,g,b,a) 的二维列表。"""
    raw = bytearray()
    for row in pixels:
        raw.append(0)  # filter type 0
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))

    def chunk(tag, data):
        payload = tag + data
        return (struct.pack(">I", len(data)) + payload
                + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(png)
    return path


def make_player_sprite(width=32, height=48):
    """画一个简单的"二次元角色"色块:头发 / 脸 / 身体 / 腿，带透明背景。"""
    hair = (90, 60, 140, 255)
    skin = (255, 224, 196, 255)
    cloth = (70, 130, 200, 255)
    skirt = (40, 70, 130, 255)
    leg = (245, 245, 245, 255)
    shoe = (60, 60, 70, 255)
    eye = (40, 40, 60, 255)
    clear = (0, 0, 0, 0)

    pixels = [[clear for _ in range(width)] for _ in range(height)]
    for y in range(height):
        for x in range(width):
            cx = x - width / 2
            # 头发+头
            if 2 <= y < 16 and abs(cx) < 10:
                pixels[y][x] = hair
            if 5 <= y < 22 and abs(cx) < 8:
                pixels[y][x] = skin if y > 9 else hair
            # 眼睛
            if 13 <= y < 16 and (4 <= abs(cx) < 7):
                pixels[y][x] = eye
            # 上身
            if 22 <= y < 34 and abs(cx) < 8:
                pixels[y][x] = cloth
            # 裙子
            if 34 <= y < 39 and abs(cx) < 11:
                pixels[y][x] = skirt
            # 腿
            if 39 <= y < 45 and 2 < abs(cx) < 8:
                pixels[y][x] = leg
            # 鞋
            if 45 <= y < 48 and 2 < abs(cx) < 9:
                pixels[y][x] = shoe
    return pixels


# ==========================================================================
# 节点图
# ==========================================================================
def build_graph():
    tree = gk_stub.StubNodeTree("玩家角色")

    # ---- _ready:走一遍"设置变量" ----
    ready = tree.new_node(NN.GKNodeOnReady, "开始")
    init_health = tree.new_node(NN.GKNodeSetVar, "初始化血量", gk_var_name="health", gk_value=3.0)
    print_hint = tree.new_node(NN.GKNodePrint, "打印操作提示", gk_text="方向键移动，空格跳跃，J 攻击")
    tree.new_node(NN.GKNodeComment, "注释", gk_text="准备阶段:初始化数据")
    gk_stub.connect(tree, ready, "执行", init_health, "执行")
    gk_stub.connect(tree, init_health, "完成", print_hint, "执行")

    # ---- 自定义函数:按 J 时执行(在 Main.tscn 里由按钮的 pressed 信号调用) ----
    attack = tree.new_node(NN.GKNodeFunction, "攻击函数", gk_func_name="on_attack_pressed")
    play_attack = tree.new_node(NN.GKNodePlayAnim, "播放攻击动画", gk_anim_name="attack", gk_player="")
    set_attack_state = tree.new_node(NN.GKNodeSetState, "状态=攻击", gk_state="attack")
    gk_stub.connect(tree, attack, "执行", play_attack, "执行")
    gk_stub.connect(tree, play_attack, "完成", set_attack_state, "执行")

    # ---- _physics_process:方向移动 + 翻转 + 跳跃 ----
    physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
    input_dir = tree.new_node(NN.GKNodeGetVector, "方向输入")
    # 画面里只有左右(横版平台),所以纵向两个动作留空不接
    input_dir.gk_up = "move_up"
    input_dir.gk_down = "move_down"
    input_dir.gk_left = "move_left"
    input_dir.gk_right = "move_right"
    move = tree.new_node(NN.GKNodeMoveDirection, "方向移动", gk_speed=240.0)
    # 平台跳跃必须有重力:Godot 4 的 CharacterBody2D 不会自动施加重力，
    # 不勾这个开关角色会浮在原地不动(看起来就像"脚本没生效")。
    move.gk_keep_y = True
    flip = tree.new_node(NN.GKNodeFlip, "翻转贴图", gk_mode="AUTO")
    gk_stub.connect(tree, physics, "执行", move, "执行")
    gk_stub.connect(tree, input_dir, "方向", move, "方向")
    gk_stub.connect(tree, move, "完成", flip, "执行")

    # 跳跃分支:在地面 且 刚按下跳跃
    on_floor = tree.new_node(NN.GKNodeIsOnFloor, "在地面")
    jump_pressed = tree.new_node(NN.GKNodeActionJustPressed, "跳跃键", gk_action="jump")
    both = tree.new_node(NN.GKNodeLogic, "与", gk_op="AND")
    branch = tree.new_node(NN.GKNodeBranch, "能跳吗")
    do_jump = tree.new_node(NN.GKNodeJump, "起跳", gk_jump_force=520.0)
    set_jump_state = tree.new_node(NN.GKNodeSetState, "状态=跳", gk_state="jump")
    gk_stub.connect(tree, on_floor, "在地面", both, "A")
    gk_stub.connect(tree, jump_pressed, "刚按下", both, "B")
    gk_stub.connect(tree, both, "结果", branch, "条件")
    gk_stub.connect(tree, flip, "完成", branch, "执行")
    gk_stub.connect(tree, branch, "是", do_jump, "执行")
    gk_stub.connect(tree, do_jump, "完成", set_jump_state, "执行")

    # ---- 血量归零就发信号并销毁 ----
    health = tree.new_node(NN.GKNodeGetVar, "读血量", gk_var_name="health")
    zero = tree.new_node(NN.GKNodeConstFloat, "零", gk_value=0.0)
    dead = tree.new_node(NN.GKNodeCompare, "死了吗", gk_op="LE")
    dead_branch = tree.new_node(NN.GKNodeBranch, "死亡判定")
    emit_died = tree.new_node(NN.GKNodeEmitSignal, "发出死亡", gk_signal_name="died")
    free_self = tree.new_node(NN.GKNodeQueueFree, "销毁自己")
    gk_stub.connect(tree, set_jump_state, "完成", dead_branch, "执行")
    gk_stub.connect(tree, health, "值", dead, "A")
    gk_stub.connect(tree, zero, "值", dead, "B")
    gk_stub.connect(tree, dead, "结果", dead_branch, "条件")
    gk_stub.connect(tree, dead_branch, "是", emit_died, "执行")
    gk_stub.connect(tree, emit_died, "完成", free_self, "执行")

    return tree


# ==========================================================================
# Godot 工程文件
# ==========================================================================
PROJECT_GODOT = """; 由 BlenderGameKit 生成的示例工程(Godot 4)
; 用 Godot 打开这个目录即可运行:角色能用方向键移动、空格跳跃

config_version=5

[application]

config/name="BlenderGameKit 2D 示例"
run/main_scene="res://Main.tscn"
config/features=PackedStringArray("4.4", "Forward Plus")
config/icon="res://art/player.png"

[display]

window/size/viewport_width=1152
window/size/viewport_height=648
window/stretch/mode="canvas_items"

[input]

move_left={
"deadzone": 0.2,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":65,"key_label":0,"unicode":0,"location":0,"echo":false,"script":null)
]
}
move_right={
"deadzone": 0.2,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":68,"key_label":0,"unicode":0,"location":0,"echo":false,"script":null)
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
attack={
"deadzone": 0.2,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":74,"key_label":0,"unicode":0,"location":0,"echo":false,"script":null)
]
}

[layer_names]

2d_physics/layer_1="world"
2d_physics/layer_2="player"

[rendering]

textures/canvas_textures/default_texture_filter=0
"""

PLAYER_SCENE = """[gd_scene load_steps=5 format=3]

[ext_resource type="Script" path="res://scripts/game_logic.gd" id="1_script"]
[ext_resource type="Texture2D" path="res://art/player.png" id="2_art"]
[ext_resource type="AnimationLibrary" path="res://player_animations.tres" id="3_anim"]

[sub_resource type="RectangleShape2D" id="RectangleShape2D_1"]
size = Vector2(20, 46)

[node name="Player" type="CharacterBody2D"]
script = ExtResource("1_script")

[node name="Sprite" type="Sprite2D" parent="."]
texture = ExtResource("2_art")

[node name="Collision" type="CollisionShape2D" parent="."]
shape = SubResource("RectangleShape2D_1")

[node name="AnimationPlayer" type="AnimationPlayer" parent="."]
libraries = {
"": ExtResource("3_anim")
}
"""

MAIN_SCENE = """[gd_scene load_steps=3 format=3]

[ext_resource type="PackedScene" path="res://Player.tscn" id="1_player"]

[sub_resource type="RectangleShape2D" id="RectangleShape2D_ground"]
size = Vector2(1400, 40)

[node name="Main" type="Node2D"]

[node name="Background" type="ColorRect" parent="."]
offset_right = 1152.0
offset_bottom = 648.0
color = Color(0.11, 0.12, 0.18, 1)

[node name="Player" parent="." instance=ExtResource("1_player")]
position = Vector2(576, 400)

[node name="Ground" type="StaticBody2D" parent="."]
position = Vector2(576, 600)

[node name="Shape" type="CollisionShape2D" parent="Ground"]
shape = SubResource("RectangleShape2D_ground")

[node name="Visual" type="ColorRect" parent="Ground"]
offset_left = -700.0
offset_top = -20.0
offset_right = 700.0
offset_bottom = 20.0
color = Color(0.25, 0.45, 0.35, 1)
"""

ANIMATIONS = """[gd_resource type="AnimationLibrary" load_steps=4 format=3]

[sub_resource type="Animation" id="idle"]
resource_name = "idle"
length = 1.0
loop_mode = 1
tracks/0/type = "value"
tracks/0/imported = false
tracks/0/enabled = true
tracks/0/path = NodePath("Sprite:scale")
tracks/0/interp = 1
tracks/0/loop_wrap = true
tracks/0/keys = {
"times": PackedFloat32Array(0, 0.5, 1),
"transitions": PackedFloat32Array(1, 1, 1),
"update": 0,
"values": [Vector2(1, 1), Vector2(1, 1.06), Vector2(1, 1)]
}

[sub_resource type="Animation" id="attack"]
resource_name = "attack"
length = 0.4
tracks/0/type = "value"
tracks/0/imported = false
tracks/0/enabled = true
tracks/0/path = NodePath("Sprite:modulate")
tracks/0/interp = 1
tracks/0/loop_wrap = true
tracks/0/keys = {
"times": PackedFloat32Array(0, 0.2, 0.4),
"transitions": PackedFloat32Array(1, 1, 1),
"update": 0,
"values": [Color(1, 1, 1, 1), Color(1, 0.5, 0.5, 1), Color(1, 1, 1, 1)]
}

[resource]
idle = SubResource("idle")
attack = SubResource("attack")
"""


def write_file(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def main():
    tree = build_graph()

    # 生成脚本
    settings = {
        "dimension": "2",
        "base_class": "CharacterBody2D",
        "class_name": "PlayerController",
        "export_tunables": True,
    }
    code, warnings = codegen.generate(tree, settings)
    script_dir = os.path.join(OUT_DIR, "scripts")
    exporter.write_text(os.path.join(script_dir, "game_logic.gd"), code)

    # 工程与场景
    write_file(os.path.join(OUT_DIR, "project.godot"), PROJECT_GODOT)
    write_file(os.path.join(OUT_DIR, "Player.tscn"), PLAYER_SCENE)
    write_file(os.path.join(OUT_DIR, "Main.tscn"), MAIN_SCENE)
    write_file(os.path.join(OUT_DIR, "art", "player.png"), "")  # 占位，下面覆写为二进制
    write_png(os.path.join(OUT_DIR, "art", "player.png"), 32, 48, make_player_sprite())
    write_file(os.path.join(OUT_DIR, "player_animations.tres"), ANIMATIONS)
    write_file(os.path.join(OUT_DIR, "README.md"), DEMO_README)

    print(f"已生成示例工程: {OUT_DIR}")
    print(f"  脚本行数: {len(code.splitlines())}")
    print(f"  告警: {len(warnings)}")
    for item in warnings:
        print(f"    - {item}")
    print()
    print("节点图的构成:")
    print(f"  节点总数: {len(tree.nodes)}")
    for node in tree.nodes:
        print(f"    [{node.gk_category}] {node.bl_label:16s} ({node.name})")
    return 0


DEMO_README = """# BlenderGameKit 2D 示例工程

这个工程里的 `scripts/game_logic.gd` **完全由节点图生成**，不要手改 ——
改逻辑请回到 Blender 里改节点图，然后重新导出。

## 怎么来的

`scripts/make_demo.py` 用插件自带的一套节点定义外加代码生成器，
在**不需要 Blender** 的情况下画出下面这张图并导出:

    事件                        动作
    ────────────────────────────────────────────────
    每当_准备     ──>  信号_连接 ──> 变量_设置(血量=3)
    自定义函数    ──>  动画_播放 ──> 动画_切换状态(attack)
                        ▲
                        └── 由"信号_连接"把 attack 按钮接过来
    每当_物理帧   ──>  移动_按方向 ──> 移动_水平翻转 ──> 如果_否则(在地面 且 跳跃键)
                                                          └─是─> 移动_跳跃 ──> 状态=jump
                                                                        └─> 如果_否则(血量<=0)
                                                                              └─是─> 发出 died ──> 销毁自己

## 操作

| 按键 | 动作 |
|------|------|
| A / D | 左右移动 |
| W / S | 上下(2D 平台跳跃里用于预留) |
| 空格 | 跳跃(只在地面时生效) |
| J | 攻击(触发 `on_attack_pressed`) |

## 在 Godot 里打开

用 Godot 4 打开本目录，按 F5 运行。角色会受重力下落并停在地面上，
可以用方向键左右移动、空格跳跃。

## 生成出来的脚本长什么样

打开 `scripts/game_logic.gd` 就能看到中文注释 + 标准 GDScript 4 代码，
其中速度、跳跃力度这类数值是 `@export` 的，可以在 Godot 检查器里直接调手感。
"""


if __name__ == "__main__":
    sys.exit(main())
