"""生成一个「看得见、玩得起来」的 2D 平台跳跃示例工程。

和 demo_2d 的区别(这就是"看不到东西"的根治):
  * 有 Camera2D,而且跟着玩家走 —— 玩家跑远也不会跑出画面
  * 地面够大、还有几块浮空平台 —— 一眼能看出这是个平台游戏
  * 有金币可以吃、HUD 显示分数 —— 画面里有东西在变,能确认逻辑真的在跑
  * HUD 里直接印着操作说明 —— 不用去翻文档
  * 关掉游戏时把最高分存到存档,下次打开还在 —— 顺便演示存档节点

用法(不需要 Blender,走插件自带的离线打桩通道):
    python scripts/make_platformer_demo.py
然后用 Godot 打开 examples/demo_platformer 按 F5。
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

OUT_DIR = os.path.join(ROOT, "examples", "demo_platformer")
ART_DIR = os.path.join(OUT_DIR, "art")
SCRIPT_DIR = os.path.join(OUT_DIR, "scripts")


# ==========================================================================
# 占位图:手写最小 PNG，不依赖 Pillow
# ==========================================================================
def write_png(path, width, height, pixels):
    import struct
    import zlib

    raw = bytearray()
    for row in pixels:
        raw.append(0)
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))

    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", header)
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
           + chunk(b"IEND", b""))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(png)


def make_player_sprite(width=32, height=48):
    """一个能一眼看清是"人"的方块小人(二次元配色)。"""
    hair = (92, 62, 148, 255)
    face = (255, 224, 189, 255)
    eye = (40, 40, 60, 255)
    cloth = (74, 124, 220, 255)
    belt = (36, 52, 116, 255)
    leg = (245, 245, 250, 255)
    shoe = (56, 56, 70, 255)
    clear = (0, 0, 0, 0)

    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            c = clear
            if 6 <= x < 26:
                if y < 10:
                    c = hair                       # 头发
                elif y < 22:
                    c = face                       # 脸
                    if 10 <= x < 14 and 14 <= y < 18:
                        c = eye
                    if 18 <= x < 22 and 14 <= y < 18:
                        c = eye
                    if 6 <= x < 9 or 23 <= x < 26:
                        c = hair                   # 两侧鬓发
                elif y < 34:
                    c = cloth                      # 上衣
                elif y < 38:
                    c = belt                       # 腰带
                elif y < 44:
                    c = leg                        # 腿
                    if 14 <= x < 18:
                        c = clear                  # 两腿之间留缝
                else:
                    c = shoe                       # 鞋
            elif 4 <= x < 28 and 22 <= y < 32:
                c = cloth                          # 手臂
            row.append(c)
        rows.append(row)
    return rows


def make_coin_sprite(size=16):
    """金币:外圈深金、内圈亮金、中间一点高光。"""
    rows = []
    cx = cy = (size - 1) / 2.0
    for y in range(size):
        row = []
        for x in range(size):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            if d < size / 2 - 0.5:
                if d < size / 4:
                    row.append((255, 236, 140, 255))
                else:
                    row.append((246, 190, 40, 255))
            else:
                row.append((0, 0, 0, 0))
        rows.append(row)
    return rows


# ==========================================================================
# 节点图 —— 这就是"该怎么连"的活教材
# ==========================================================================
def build_graph():
    tree = gk_stub.StubNodeTree("玩家")

    cmt = tree.new_node(NN.GKNodeComment, "注释A")
    cmt.gk_text = ("【1】准备:读存档取最高分 -> 建 HUD -> 建重开按钮\n"
                   "连线规则:金色执行线决定先后顺序，灰色线只负责传值")

    # ---------------- 事件 1:_ready ----------------
    ready = tree.new_node(NN.GKNodeOnReady, "开始")

    load = tree.new_node(NN.GKNodeSaveLoad, "读存档")
    load.gk_slot = "best"
    best = tree.new_node(NN.GKNodeSaveGet, "取最高分")
    best.gk_key = "best_score"
    best.gk_var = "best_score"
    best.gk_default = 0.0

    # HUD:三行文字 + 一个重开按钮
    hint = tree.new_node(NN.GKNodeUiText, "操作提示")
    hint.gk_text = "A/D 或 左右方向键 移动    空格 跳"
    hint.gk_x, hint.gk_y, hint.gk_size = 16.0, 16.0, 20
    hint.gk_color = (0.85, 0.90, 1.0, 1.0)

    score = tree.new_node(NN.GKNodeUiText, "分数")
    score.gk_text = "分数: 0"
    score.gk_x, score.gk_y, score.gk_size = 16.0, 44.0, 26
    score.gk_color = (1.0, 0.92, 0.45, 1.0)

    best_label = tree.new_node(NN.GKNodeUiText, "最高分")
    best_label.gk_text = "最高分: 0"
    best_label.gk_x, best_label.gk_y, best_label.gk_size = 16.0, 78.0, 20
    best_label.gk_color = (0.70, 0.75, 0.85, 1.0)

    restart = tree.new_node(NN.GKNodeUiButton, "重开按钮")
    restart.gk_text = "重开 (R)"
    restart.gk_x, restart.gk_y = 16.0, 112.0
    restart.gk_width, restart.gk_height = 128.0, 36.0

    set_score_var = tree.new_node(NN.GKNodeSetVar, "分数清零")
    set_score_var.gk_var_name = "score"
    set_score_var.gk_value = 0.0

    # _ready 的链:读档 -> 取最高分 -> 清零分数 -> 依次建 HUD
    gk_stub.connect(tree, ready, "执行", load, "执行")
    gk_stub.connect(tree, load, "完成", best, "执行")
    gk_stub.connect(tree, best, "完成", set_score_var, "执行")
    gk_stub.connect(tree, set_score_var, "完成", hint, "执行")
    gk_stub.connect(tree, hint, "完成", score, "执行")
    gk_stub.connect(tree, score, "完成", best_label, "执行")
    gk_stub.connect(tree, best_label, "完成", restart, "执行")

    # 按钮点一下 -> 调用自定义函数重开
    on_restart = tree.new_node(NN.GKNodeCallFunction, "调用重开")
    on_restart.gk_func_name = "restart_game"
    gk_stub.connect(tree, restart, "点击", on_restart, "执行")

    # ---------------- 自定义函数:重开 ----------------
    restart_fn = tree.new_node(NN.GKNodeFunction, "重开函数")
    restart_fn.gk_func_name = "restart_game"
    reload_scene = tree.new_node(NN.GKNodeReloadScene, "重载场景")
    gk_stub.connect(tree, restart_fn, "执行", reload_scene, "执行")

    # ---------------- 事件 2:_physics_process 移动 ----------------
    cmt2 = tree.new_node(NN.GKNodeComment, "注释B")
    cmt2.gk_text = ("【2】每物理帧:读输入 -> 移动 -> 朝向翻转。\n"
                   "『保留纵向速度』必须勾上，否则重力被清掉、角色悬空不下落")

    physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
    d2 = tree.new_node(NN.GKNodeGetVector, "方向输入")
    d2.gk_left, d2.gk_right = "move_left", "move_right"
    d2.gk_up, d2.gk_down = "move_up", "move_down"

    move = tree.new_node(NN.GKNodeMoveDirection, "移动", gk_speed=260.0)
    move.gk_keep_y = True          # 关键:让重力生效

    flip = tree.new_node(NN.GKNodeFlip, "朝向翻转")
    flip.gk_mode = "AUTO"

    gk_stub.connect(tree, physics, "执行", move, "执行")
    gk_stub.connect(tree, d2, "方向", move, "方向")
    gk_stub.connect(tree, move, "完成", flip, "执行")

    # 跳跃:在地面 且 按下跳跃
    onfloor = tree.new_node(NN.GKNodeIsOnFloor, "在地面")
    jp = tree.new_node(NN.GKNodeActionJustPressed, "跳跃键")
    jp.gk_action = "jump"
    both = tree.new_node(NN.GKNodeLogic, "与")
    both.gk_op = "AND"
    jump_branch = tree.new_node(NN.GKNodeBranch, "能跳吗")
    do_jump = tree.new_node(NN.GKNodeJump, "起跳", gk_jump_force=560.0)

    gk_stub.connect(tree, flip, "完成", jump_branch, "执行")
    gk_stub.connect(tree, onfloor, "在地面", both, "A")
    gk_stub.connect(tree, jp, "刚按下", both, "B")
    gk_stub.connect(tree, both, "结果", jump_branch, "条件")
    gk_stub.connect(tree, jump_branch, "是", do_jump, "执行")

    # ---------------- 事件 3:_process 相机跟随 ----------------
    cmt3 = tree.new_node(NN.GKNodeComment, "注释C")
    cmt3.gk_text = ("【3】每帧:相机跟随角色。\n"
                   "不放这个节点相机就站着不动，角色一跑远就出画面了")

    process = tree.new_node(NN.GKNodeOnProcess, "每帧")
    camera_follow = tree.new_node(NN.GKNodeCameraFollow, "相机跟随")
    camera_follow.gk_smooth = 0.12
    gk_stub.connect(tree, process, "执行", camera_follow, "执行")

    return tree


# ==========================================================================
# Godot 工程文件
# ==========================================================================
# 场景结构一眼能看懂:
#   Main
#   ├── Background   深色底(没有它渲染出来是黑/灰)
#   ├── Camera2D     相机 —— 由『相机_跟随』节点驱动
#   ├── Ground       地面(够大)
#   ├── Platform*    三块浮空平台(有它们才像平台跳跃)
#   ├── Coin*        金币
#   └── Player       玩家(脚本来自节点图)
PROJECT_GODOT = """; 由 BlenderGameKit 生成的 2D 平台跳跃示例
; 用 Godot 打开这个目录，按 F5 就能玩
;
; 玩法: A/D 或左右方向键移动，空格跳，吃掉金币加分。
; 这一局吃到的金币数会存成"最高分"，下次打开还在。

config_version=5

[application]

config/name="BlenderGameKit 2D 平台跳跃示例"
run/main_scene="res://Main.tscn"
config/features=PackedStringArray("4.4", "Forward Plus")

[display]

window/size/viewport_width=1152
window/size/viewport_height=648
window/stretch/mode="canvas_items"

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

[layer_names]

2d_physics/layer_1="world"
2d_physics/layer_2="player"
2d_physics/layer_3="pickup"

[rendering]

textures/canvas_textures/default_texture_filter=0
"""


def _platform(name, x, y, w, h):
    return f"""
[node name="{name}" type="StaticBody2D" parent="."]
position = Vector2({x}, {y})

[node name="Shape" type="CollisionShape2D" parent="{name}"]
shape = SubResource("Box_{name}")

[node name="Visual" type="ColorRect" parent="{name}"]
offset_left = {-w / 2}
offset_top = {-h / 2}
offset_right = {w / 2}
offset_bottom = {h / 2}
color = Color(0.25, 0.45, 0.35, 1)
"""


def build_main_scene():
    plat_subs = []
    plat_nodes = []
    for name, x, y, w, h in (
        ("Ground", 1024, 620, 2400, 48),
        ("Platform1", 520, 500, 220, 28),
        ("Platform2", 900, 400, 200, 28),
        ("Platform3", 1320, 470, 240, 28),
    ):
        plat_subs.append(
            f'[sub_resource type="RectangleShape2D" id="Box_{name}"]\n'
            f"size = Vector2({w}, {h})\n")
        plat_nodes.append(_platform(name, x, y, w, h))

    coins = []
    coin_nodes = []
    for i, (cx, cy) in enumerate(
            ((520, 450), (900, 350), (1320, 420), (700, 560), (1150, 560)), start=1):
        coins.append(
            f'[node name="Coin{i}" parent="." instance=ExtResource("3_coin")]\n'
            f"position = Vector2({cx}, {cy})\n")
        coin_nodes.append(coins[-1])

    total_subs = 4  # 4 个平台碰撞盒
    return (
        f"[gd_scene load_steps={total_subs + 4} format=3]\n\n"
        '[ext_resource type="PackedScene" path="res://Player.tscn" id="2_player"]\n'
        '[ext_resource type="PackedScene" path="res://Coin.tscn" id="3_coin"]\n\n'
        + "\n".join(plat_subs)
        + "\n"
        # Background 必须在最前面:后面的节点画在它上面。
        # 尺寸必须**远大于视口** —— 相机跟着角色跑，背景只做 1152x648 的话
        # 相机一移动就露出 Godot 默认灰底(实测截图里左边和下面全是灰的)。
        # 这个工程视口是 1152x648，地面宽 2400，所以背景取 4000x2000 足够。
        + '[node name="Main" type="Node2D"]\n'
        + "\n"
        + '[node name="Background" type="ColorRect" parent="."]\n'
        + "offset_left = -1200.0\n"
        + "offset_top = -600.0\n"
        + "offset_right = 2800.0\n"
        + "offset_bottom = 1400.0\n"
        + "color = Color(0.10, 0.11, 0.17, 1)\n"
        + "\n"
        + '[node name="Camera2D" type="Camera2D" parent="."]\n'
        + "position = Vector2(300, 500)\n"
        + "zoom = Vector2(1, 1)\n"
        + "\n"
        + "".join(plat_nodes)
        + "\n"
        + "".join(coin_nodes)
        + "\n"
        + '[node name="Player" parent="." instance=ExtResource("2_player")]\n'
        + "position = Vector2(300, 480)\n"
    )


PLAYER_SCENE = """[gd_scene load_steps=4 format=3]

[ext_resource type="Script" path="res://scripts/player.gd" id="1_script"]
[ext_resource type="Texture2D" path="res://art/player.png" id="2_art"]

[sub_resource type="RectangleShape2D" id="Box_player"]
size = Vector2(20, 46)

[node name="Player" type="CharacterBody2D" groups=["player"]]
script = ExtResource("1_script")

[node name="Sprite" type="Sprite2D" parent="."]
texture = ExtResource("2_art")

[node name="Collision" type="CollisionShape2D" parent="."]
shape = SubResource("Box_player")
"""

COIN_SCENE = """[gd_scene load_steps=4 format=3]

[ext_resource type="Script" path="res://scripts/coin.gd" id="1_script"]
[ext_resource type="Texture2D" path="res://art/coin.png" id="2_art"]

[sub_resource type="CircleShape2D" id="Circle_coin"]
radius = 11.0

[node name="Coin" type="Area2D" groups=["coin"]]
script = ExtResource("1_script")

[node name="Sprite" type="Sprite2D" parent="."]
texture = ExtResource("2_art")

[node name="Collision" type="CollisionShape2D" parent="."]
shape = SubResource("Circle_coin")
"""


# 金币脚本是手写的，不来自节点图 —— 它是"另一个物件自己的逻辑"，
# 正好演示"一个场景一个脚本"的组织方式。
COIN_SCRIPT = '''extends Area2D
## 金币:被玩家碰到就加分并消失。
##
## 分数存在玩家身上(由节点图生成的 player.gd 持有 `score` 变量)。
## 这里用 group 找玩家，而不是把 Player 路径写死 —— 场景里放几份金币
## 都不用改代码。

@export var score_value: int = 10
@export var spin_speed: float = 3.0

var _taken := false


func _ready() -> void:
	body_entered.connect(_on_body_entered)


func _process(delta: float) -> void:
	# 轻轻上下浮动，让金币看起来"活着"
	rotation += spin_speed * delta


func _on_body_entered(body: Node) -> void:
	if _taken:
		return
	if not body.is_in_group("player"):
		return
	_taken = true
	if body.has_method("add_score"):
		body.add_score(score_value)
	queue_free()
'''


README = """# 2D 平台跳跃示例

这是 BlenderGameKit 的**可玩**示例:不是"能编译就算过"，而是打开就能玩、
画面里能看见东西在动。

## 怎么跑

用 Godot 4 打开这个目录，按 **F5**。

## 能看到什么

| 画面里 | 由谁负责 |
|---|---|
| 深蓝背景 | `Main.tscn` 里的 `Background`(ColorRect) |
| 绿色地面 + 三块浮空平台 | `Main.tscn` 里的 `StaticBody2D` |
| 小人(能左右跑、能跳) | `Player.tscn` + `scripts/player.gd` |
| 金币(碰到加分、自己消失) | `Coin.tscn` + `scripts/coin.gd` |
| 左上角三行字 + 一个按钮 | 节点图里的『界面_文本』『界面_按钮』 |
| 相机跟着角色走 | 节点图里的『相机_跟随』 |

## 操作

* `A` / `D` 或 `←` / `→` —— 移动
* `空格` —— 跳
* 吃金币 —— 加 10 分
* 关掉游戏再打开 —— 最高分还在(存在 `user://gk_best.json`)

## 节点图怎么读

`scripts/player.gd` 是**生成的**(不要手改)。对应的规则在
`BlenderGameKit` 的节点图里，三条主线:

```
【1】每当_准备 ──> 存档_载入 ──> 存档_读取到变量 ──> 变量_设置
              ──> 界面_文本×3 ──> 界面_按钮 ──(点击)──> 调用_函数

【2】每当_物理帧 ──> 移动_按方向(方向 ← 输入_方向向量)
              ──> 移动_水平翻转 ──> 如果_否则(条件 = 在地面 与 按键按下)
                                  └─是─> 移动_跳跃

【3】每当_每帧 ──> 相机_跟随
```

## 连线规则(和蓝图一样吗?)

**一样**。金色/白色的**执行线**决定"先做哪个"，灰色线只负责**传值**。
两者不能接错:执行线只能接执行线。

三条最容易踩的坑(这个示例里都已经避开了):

1. **平台跳跃游戏,『移动_按方向』必须勾上『保留纵向速度』。**
   不勾的话每一帧都会把 `velocity.y` 写成 0，而 Godot 的重力是加在
   `velocity.y` 上的 —— 结果是角色永远悬空不下落。
2. **必须放『相机_跟随』。** Godot 的相机不会自己跟人，不放的话角色
   一跑远就出画面，看起来就像"画面空了"。
3. **一个输入引脚只能接一条线。** 想让两个事件都执行同一段逻辑，
   得把那段节点复制一份(Blender 的限制，生成器会明确告警)。

## 想改着玩

* 改手感:`player.gd` 顶部的 `@export var move_speed / jump_force`
  可以在 Godot 检查器里直接拖，不用重新导出。
* 改分数:金币场景上的 `score_value`。
* 改关卡:直接在 `Main.tscn` 里拖平台和金币。
"""


# 手写补充:金币被吃掉时要给玩家加分，而"加分"这件事需要同时
#   1) 累加 score
#   2) 刷新 HUD 上那行字(否则分数变了画面不变)
#   3) 破纪录就立刻更新存档
# 节点图能做，但要七八个节点；这里作为"生成代码 + 手写补充"的示例附加在
# 生成脚本末尾。生成器每次导出会重新写文件，所以这段要留在这里而不是
# 直接改 examples 里的产物。
HANDWRITTEN_TAIL = '''

# ==========================================================================
# 下面是手写补充(不在节点图里)。
#
# ── 为什么这段是手写的 ──────────────────────────────────────────────
# 金币被吃掉时要同时做三件事:加分、刷新 HUD、破纪录就立刻存盘。
# 用节点图要七八个节点才拼得出来，而这个示例的重点是"看得见 + 好读"。
# 真实项目可以两种方式混用:高频简单的逻辑画节点图，
# 个别需要精细控制的地方手写。
#
# score / best_score 这两个变量由节点图声明(『变量_设置』『存档_读取到变量』)，
# _gk_ui_分数 / _gk_ui_最高分 两个控件也由『界面_文本』建好 ——
# 所以这里直接引用就行，**不要重复声明**，否则 Godot 报变量重定义。
# ==========================================================================

## 金币脚本碰到玩家时会调用这个方法: body.add_score(10)
func add_score(amount: int) -> void:
    score += float(amount)
    if score > float(best_score):
        best_score = score
        # 破纪录才写盘:每吃一个金币都写文件没必要，而且写盘会卡帧
        _gk_save_data["best_score"] = best_score
        _gk_write_save(_gk_save_file("best"), _gk_save_data)
        if _gk_ui_最高分 != null:
            _gk_ui_最高分.text = "最高分: %d" % int(best_score)
    if _gk_ui_分数 != null:
        _gk_ui_分数.text = "分数: %d" % int(score)
'''


def main():
    os.makedirs(ART_DIR, exist_ok=True)
    os.makedirs(SCRIPT_DIR, exist_ok=True)

    write_png(os.path.join(ART_DIR, "player.png"), 32, 48, make_player_sprite())
    write_png(os.path.join(ART_DIR, "coin.png"), 16, 16, make_coin_sprite())

    tree = build_graph()
    options = {
        "class_name": "PlayerController",
        "base_class": "CharacterBody2D",
        "dimension": "2",
        "export_tunables": True,
    }
    code, warnings = codegen.generate(tree, options)
    # 生成脚本 + 手写补充(见 HANDWRITTEN_TAIL 的说明)
    code = code.rstrip() + "\n" + HANDWRITTEN_TAIL

    exporter.write_text(os.path.join(SCRIPT_DIR, "player.gd"), code)
    exporter.write_text(os.path.join(SCRIPT_DIR, "coin.gd"), COIN_SCRIPT)
    exporter.write_text(os.path.join(OUT_DIR, "Main.tscn"), build_main_scene())
    exporter.write_text(os.path.join(OUT_DIR, "Player.tscn"), PLAYER_SCENE)
    exporter.write_text(os.path.join(OUT_DIR, "Coin.tscn"), COIN_SCENE)
    exporter.write_text(os.path.join(OUT_DIR, "project.godot"), PROJECT_GODOT)
    exporter.write_text(os.path.join(OUT_DIR, "README.md"), README)

    print("已生成示例工程:", OUT_DIR)
    print("  脚本行数:", code.count("\n"))
    if warnings:
        print("  告警:")
        for w in warnings:
            print("    -", w)
    else:
        print("  无告警")
    return 0


if __name__ == "__main__":
    # 允许把示例生成到别处:platformer_verify.ps1 的门禁跑在
    # _dist\platformer_e2e,免得每跑一次测试就把 examples/demo_platformer
    # 这批**已入库**的文件改一遍。
    if len(sys.argv) > 1:
        OUT_DIR = os.path.abspath(sys.argv[1])
        ART_DIR = os.path.join(OUT_DIR, "art")
        SCRIPT_DIR = os.path.join(OUT_DIR, "scripts")
    sys.exit(main())
