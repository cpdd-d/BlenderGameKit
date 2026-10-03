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

from BlenderGameKit import codegen, exporter, project  # noqa: E402
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
    # 一张卡片设两个变量(第四轮:仿蓝图 Set 节点,每个值引脚可接不同节点)
    init_health = tree.new_node(NN.GKNodeSetVar, "初始化血量与分数")
    init_health.gk_var_count = 2
    init_health.gk_var_name = "health"
    init_health.gk_value = 3.0
    init_health.gk_var_name_2 = "score"
    init_health.gk_value_2 = 0.0
    init_health.build_sockets()
    print_hint = tree.new_node(NN.GKNodePrint, "打印操作提示", gk_text="AD 移动，空格跳跃，J 攻击")
    tree.new_node(NN.GKNodeComment, "注释", gk_text="准备阶段:初始化数据")
    gk_stub.connect(tree, ready, "执行", init_health, "执行")
    gk_stub.connect(tree, init_health, "完成", print_hint, "执行")

    # ---- 自定义函数:按 J 时执行(在 Main.tscn 里由按钮的 pressed 信号调用) ----
    attack = tree.new_node(NN.GKNodeFunction, "攻击函数", gk_func_name="on_attack_pressed")
    play_attack = tree.new_node(NN.GKNodePlayAnim, "播放攻击动画", gk_anim_name="attack", gk_player="")
    set_attack_state = tree.new_node(NN.GKNodeSetState, "状态=攻击", gk_state="attack")
    # 加分:读出来 +1 再写回去(蓝图里也是这么干的)
    read_score = tree.new_node(NN.GKNodeGetVar, "读分数", gk_var_name="score")
    one = tree.new_node(NN.GKNodeConstFloat, "一", gk_value=1.0)
    add_score = tree.new_node(NN.GKNodeMath, "加一", gk_op="ADD")
    write_score = tree.new_node(NN.GKNodeSetVar, "写分数", gk_var_name="score")
    show_score = tree.new_node(NN.GKNodeUiText, "显示分数")
    show_score.gk_text = "分数: 1"
    show_score.gk_x = 20.0
    show_score.gk_y = 20.0
    flash = tree.new_node(NN.GKNodeScreenFlash, "闪一下")
    spawn_bullet = tree.new_node(NN.GKNodeSpawn, "发射子弹")
    spawn_bullet.gk_scene_path = "res://Bullet.tscn"
    spawn_bullet.gk_parent = ""
    gk_stub.connect(tree, attack, "执行", play_attack, "执行")
    gk_stub.connect(tree, play_attack, "完成", set_attack_state, "执行")
    # 『数学_运算』是**表达式**节点(没有执行流):它只当"值的来源",
    # 执行顺序由『变量_设置』那根线决定 —— 和蓝图里把 Add 拖到 Set 的值引脚一样
    gk_stub.connect(tree, read_score, "值", add_score, "A")
    gk_stub.connect(tree, one, "值", add_score, "B")
    gk_stub.connect(tree, add_score, "结果", write_score, "值")
    gk_stub.connect(tree, set_attack_state, "完成", write_score, "执行")
    gk_stub.connect(tree, read_score, "值", show_score, "文字")
    gk_stub.connect(tree, write_score, "完成", show_score, "执行")
    gk_stub.connect(tree, show_score, "完成", flash, "执行")
    gk_stub.connect(tree, flash, "完成", spawn_bullet, "执行")

    # ---- _physics_process:方向移动 + 翻转 + 跳跃 ----
    physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
    # 第四轮的新节点:『输入_方向』—— 四个键**各点各的『按一下键』**捕获,
    # 直接给出 方向 / 左右 / 前后,不用再自己拼向量、也不用设变量
    input_dir = tree.new_node(NN.GKNodeInputDirection, "方向输入")
    input_dir.gk_key_up = "W"
    input_dir.gk_key_down = "S"
    input_dir.gk_key_left = "A"
    input_dir.gk_key_right = "D"
    move = tree.new_node(NN.GKNodeMoveDirection, "方向移动", gk_speed=240.0)
    # 平台跳跃必须有重力:Godot 4 的 CharacterBody2D 不会自动施加重力，
    # 不勾这个开关角色会浮在原地不动(看起来就像"脚本没生效")。
    move.gk_keep_y = True
    flip = tree.new_node(NN.GKNodeFlip, "翻转贴图", gk_mode="AUTO")
    gk_stub.connect(tree, physics, "执行", move, "执行")
    gk_stub.connect(tree, input_dir, "方向", move, "方向")
    follow_cam = tree.new_node(NN.GKNodeCameraFollow, "相机跟随")
    follow_cam.gk_smooth = 0.12
    follow_cam.gk_offset_y = -40.0
    gk_stub.connect(tree, move, "完成", flip, "执行")
    gk_stub.connect(tree, flip, "完成", follow_cam, "执行")

    # 跳跃分支:在地面 且 刚按下跳跃
    on_floor = tree.new_node(NN.GKNodeIsOnFloor, "在地面")
    jump_pressed = tree.new_node(NN.GKNodeKey, "跳跃键")
    jump_pressed.gk_key = "SPACE"
    jump_pressed.gk_action = "jump"
    jump_pressed.gk_trigger = "JUST"
    both = tree.new_node(NN.GKNodeLogic, "与", gk_op="AND")
    branch = tree.new_node(NN.GKNodeBranch, "能跳吗")
    do_jump = tree.new_node(NN.GKNodeJump, "起跳", gk_jump_force=520.0)
    set_jump_state = tree.new_node(NN.GKNodeSetState, "状态=跳", gk_state="jump")
    gk_stub.connect(tree, on_floor, "在地面", both, "A")
    gk_stub.connect(tree, jump_pressed, "结果", both, "B")
    gk_stub.connect(tree, both, "结果", branch, "条件")
    gk_stub.connect(tree, follow_cam, "完成", branch, "执行")
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

[node name="Camera2D" type="Camera2D" parent="."]
position = Vector2(0, -40)
enabled = true
"""

# ── 子弹:『实例_生成』生成的就是它 ──────────────────────────────
BULLET_SCENE = """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://scripts/bullet.gd" id="1_bullet"]

[node name="Bullet" type="Node2D"]
script = ExtResource("1_bullet")

[node name="Visual" type="ColorRect" parent="."]
offset_left = -6.0
offset_top = -3.0
offset_right = 6.0
offset_bottom = 3.0
color = Color(1, 0.85, 0.3, 1)
"""

BULLET_SCRIPT = """extends Node2D
# 子弹自己往前飞,1 秒后消失 —— 演示『实例_生成』出来的东西真的会动
var life := 0.0
var speed := 520.0

func _process(delta: float) -> void:
	life += delta
	position.x += speed * delta
	if life > 1.0:
		queue_free()
"""

# ── 探针:在真 Godot 里**模拟按键**,验证节点图真的产生了行为 ──────
# 用户 2026-10-03:"你可以在一个平面上创建2D示例,然后用节点图来进行游戏
# 设计……我现在验证不了的节点" —— 这个脚本就是答案:不靠肉眼看,
# 而是 `Input.action_press()` 模拟输入 + 读回游戏状态来断言。
PROBE_SCRIPT = """extends Node
# 由 make_demo.py 生成:在**真 Godot**里模拟按键,验证节点图真的产生了行为。
#
# 用户 2026-10-03:"你可以在一个平面上创建2D示例,然后用节点图来进行游戏设计,
# 要求是尽量包含上面的设计好,但我现在验证不了的节点" —— 这个探针就是答案:
# 不靠肉眼看画面,而是 `Input.action_press()` 模拟输入 + 读回游戏状态来断言。
#
# ── 时间累加写在 _process 里(踩过的坑)────────────────────────────
# 第一版把累加放进了"遍历所有时间点"的循环里,于是每帧加 6 次 delta,
# 时间跑得比现实快好几倍 —— 量出来的"跳跃上升 418 像素"纯属幻觉。

var player: Node2D = null
var start_x := 0.0
var jump_start_y := 0.0
var jump_peak_y := 99999.0
var score_before := -1.0
var score_after := -1.0
var bullets := 0
var label_text := "<none>"
var camera_name := "<none>"
var moved_x := 0.0
var jump_rise := 0.0

var t := 0.0
var marks := [0.3, 0.7, 1.0, 1.35, 1.6, 1.9]
var done := {}

func _ready() -> void:
	player = get_parent().get_node_or_null("Player")
	if player != null:
		start_x = player.position.x

# ── 用 _physics_process 而不是 _process(踩过的坑)────────────────
# 无头(headless)模式一帧只花一两毫秒,`_process` 的 delta 是**真实耗时**,
# 180 帧加起来还不到 0.5 秒 —— 探针的时间点一个都到不了,于是
# "全都没输出"。物理帧的 delta 是**固定的 1/60**,180 帧正好 3 秒,
# 和机器快慢无关。
func _physics_process(delta: float) -> void:
	t += delta
	if player != null and t > 1.0 and t < 1.35:
		jump_peak_y = minf(jump_peak_y, player.position.y)
	for mark in marks:
		if t >= mark and not done.has(mark):
			done[mark] = true
			_step(mark)
	if t > 2.0:
		print("PROBE2D_DONE")
		set_physics_process(false)

func _step(mark: float) -> void:
	match mark:
		0.3:
			Input.action_press("key_d")          # 往右走(节点图捕获的键)
		0.7:
			Input.action_release("key_d")
			moved_x = player.position.x - start_x
		1.0:
			jump_start_y = player.position.y
			Input.action_press("jump")           # 跳
		1.35:
			Input.action_release("jump")
			jump_rise = jump_start_y - jump_peak_y
			score_before = _score()
			if player.has_method("on_attack_pressed"):
				player.call("on_attack_pressed")
		1.6:
			score_after = _score()
			bullets = _count_bullets()
		1.9:
			label_text = _find_label(get_parent())
			if label_text == "<none>":
				label_text = _find_label(player)
			camera_name = _camera_name()
			print("PROBE2D moved_x=", "%.1f" % moved_x)
			print("PROBE2D jump_rise=", "%.1f" % jump_rise)
			print("PROBE2D score=", "%.1f" % score_before, " -> ",
				"%.1f" % score_after)
			print("PROBE2D bullets=", bullets)
			print("PROBE2D label=", label_text)
			print("PROBE2D camera=", camera_name)

func _score() -> float:
	var value = player.get("score")
	return 0.0 if value == null else float(value)

func _count_bullets() -> int:
	var count := 0
	for child in get_parent().get_children():
		if String(child.name).begins_with("Bullet"):
			count += 1
	return count

func _find_label(root: Node) -> String:
	# UI 是节点图自己建的(CanvasLayer -> Control -> Label),递归找
	for child in root.get_children():
		if child is Label:
			return String(child.text)
		var found := _find_label(child)
		if found != "<none>":
			return found
	return "<none>"

func _camera_name() -> String:
	var cam := get_viewport().get_camera_2d()
	return String(cam.name) if cam != null else "<none>"
"""



MAIN_SCENE = """[gd_scene load_steps=4 format=3]

[ext_resource type="PackedScene" path="res://Player.tscn" id="1_player"]
[ext_resource type="Script" path="res://scripts/probe.gd" id="2_probe"]

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

[node name="Probe" type="Node" parent="."]
script = ExtResource("2_probe")
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
_data = {
"idle": SubResource("idle"),
"attack": SubResource("attack")
}
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
    # ── 输入映射交给插件自己的写入器(踩过的坑)──────────────────────
    # 这里原来是**手写** [input] 段(move_left/jump/attack 那一坨)。
    # 节点图换成『输入_方向』之后,动作名变成 key_w / key_a / key_d …,
    # 手写那一段里没有,于是真跑时报
    #     The InputMap action "key_d" doesn't exist.
    # 按键完全没反应 —— 而"用户在 Blender 里按一下键、Godot 里就能用"
    # 这条链本来就是靠 set_input_actions 写进 project.godot 的。
    actions = list(getattr(codegen, "last_used_actions", []) or [])
    written = project.set_input_actions(OUT_DIR, actions)
    print(f"  写入输入动作: {written} 个")
    write_file(os.path.join(OUT_DIR, "Player.tscn"), PLAYER_SCENE)
    write_file(os.path.join(OUT_DIR, "Main.tscn"), MAIN_SCENE)
    write_file(os.path.join(OUT_DIR, "art", "player.png"), "")  # 占位，下面覆写为二进制
    write_png(os.path.join(OUT_DIR, "art", "player.png"), 32, 48, make_player_sprite())
    write_file(os.path.join(OUT_DIR, "player_animations.tres"), ANIMATIONS)
    write_file(os.path.join(OUT_DIR, "Bullet.tscn"), BULLET_SCENE)
    write_file(os.path.join(OUT_DIR, "scripts", "bullet.gd"), BULLET_SCRIPT)
    write_file(os.path.join(OUT_DIR, "scripts", "probe.gd"), PROBE_SCRIPT)
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
