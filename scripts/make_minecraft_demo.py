# -*- coding: utf-8 -*-
"""生成"我的世界"风格的第一人称方块示例(第一百九十三轮)。

不需要 Blender:走插件自带的离线打桩通道,用**真的节点类**建图,
再用真的 codegen 生成 GDScript,最后写一份可以直接按 F5 的 Godot 工程。

    python scripts\\make_minecraft_demo.py
    godot --path examples\\demo_minecraft

玩法:
  WASD 移动 / 空格跳 / 鼠标转视角 / 数字键 1~4 选方块(草·石·沙·木)
  / 左键破坏准星指着的方块 / 右键或 Q 放置当前选中的方块 / ESC 退出。

它检查的东西:
  * 3D 第一人称:『相机_旋转』+『移动_按方向(随相机朝向)』;
  * 重力 + 跳跃:『在地面』+『输入_按键』+『逻辑与』+『移动_跳跃』;
  * 射线交互:『射线_检测(当前相机 / 相机前向)』+『实例_销毁』;
  * 流程串联:『序列』把转视角 / 挖 / 放 / 选方块 / ESC 串起来;
  * 带状态的放置:侧栏变量『方块序号』+『多路分支』(Switch on Int)
    + 四个『资产_创建实例』(每种方块一份 .tscn);
  * 输入映射:move_* + jump + mine/place + slot_1~slot_4 写进 project.godot。
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
from BlenderGameKit.nodes.node_base import TARGET_SELF  # noqa: E402

OUT_DIR = os.path.join(ROOT, "examples", "demo_minecraft")
SCRIPT_PATH = os.path.join(OUT_DIR, "scripts", "game_logic.gd")

# 准星指到的方块会被『实例_销毁』移走;世界用 10x10 个 1 米方块铺成,
# 再加一棵树、一个小石台和一片沙地。方块是独立 StaticBody3D,
# 所以射线打中的是"一块",不是整片地面。
GROUND = 10
BLOCK_MATS = ("grass", "dirt", "stone", "wood", "leaves", "sand")

# 会追人的小怪(名字 / 出生位置)。它们没有自己的脚本 —— 追击由玩家脚本里的
# 『移动_朝向目标』驱动,所以"谁走"填的是**相对玩家的路径** ../Monsters/<名字>。
MONSTERS = (
    ("怪物1", (2.0, 1.0, -3.0)),
    ("怪物2", (4.0, 1.0, -5.0)),
    ("怪物3", (-1.5, 1.0, -4.0)),
)
MONSTER_SPEED = 2.6
MONSTER_REACH = 1.3
PLAYER_SPAWN = (0.5, 1.0, 0.5)

# 手上能拿的四档:显示名 / 场景文件 / 颜色。数字键 1~4 切的就是这个列表,
# 『多路分支』的出口 0~3 也按这个顺序接。
BLOCK_KINDS = (
    ("草方块", "GrassBlock", (0.24, 0.60, 0.22)),
    ("石头方块", "StoneBlock", (0.46, 0.48, 0.52)),
    ("沙子方块", "SandBlock", (0.80, 0.72, 0.46)),
    ("木头方块", "WoodBlock", (0.42, 0.27, 0.12)),
)


# ==========================================================================
# 节点图
# ==========================================================================
def build_tree():
    tree = gk_stub.StubNodeTree("我的世界")

    # 侧栏变量:手上是哪一档方块(0 草 / 1 石 / 2 沙 / 3 木)。
    # 声明在这里而不是靠『变量_设置』现推 —— 生成的成员变量带类型和初值,
    # 也顺便演示"蓝图变量"这套。
    held = tree.gk_vars.add()
    held.var_name = "方块序号"
    held.var_type = "float"
    held.default_value = "0"
    held.comment = "0 草 / 1 石 / 2 沙 / 3 木,数字键 1~4 切"

    # 玩家生命:怪物贴上来就扣,掉到 0 送回出生点
    health = tree.gk_vars.add()
    health.var_name = "生命"
    health.var_type = "float"
    health.default_value = "100"
    health.comment = "怪物每口 -8;0 = 送回出生点"

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
                        "1~4 选方块 / 左键或 E 挖 / 右键或 Q 放 / ESC 退出"))
    link(ready, "执行", mouse_mode, "执行")
    link(mouse_mode, "完成", hint, "执行")

    # ---- _process:序列(转视角 / 挖 / 放 / 选 1~4 / ESC) ----
    process = add(NN.GKNodeOnProcess, "每帧", -1600.0, 200.0)
    sequence = add(NN.GKNodeSequence, "每帧八件事", -1300.0, 200.0,
                   gk_step_count=8)
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

    # 放哪种:读侧栏变量 ->『多路分支』(Switch on Int)-> 四个资产各一份。
    # 出口名就是匹配的整数值(0~3),顺序和 BLOCK_KINDS 一致。
    held_read = add(NN.GKNodeGetVar, "手上是哪档", -620.0, -820.0,
                    gk_var_name="方块序号")
    block_switch = add(NN.GKNodeSwitch, "放哪种", -380.0, -1000.0,
                       gk_case_count=len(BLOCK_KINDS))
    split_hit = add(NN.GKNodeVectorSplit, "拆命中点", -1100.0, -1300.0)
    lift = add(NN.GKNodeMath, "抬半格", -850.0, -1300.0,
               gk_op="ADD", gk_b=0.5)
    combine = add(NN.GKNodeVectorMake, "合成位置", -620.0, -1300.0)
    link(hit_seq, "2", place_branch, "执行")
    link(right, "刚按下", place_or, "A")
    link(place_key, "结果", place_or, "B")
    link(place_or, "结果", place_branch, "条件")
    link(place_branch, "是", block_switch, "执行")
    link(held_read, "值", block_switch, "值")
    link(ray, "命中点", split_hit, "向量")
    link(split_hit, "X", combine, "X")
    link(split_hit, "Z", combine, "Z")
    link(split_hit, "Y", lift, "A")
    link(lift, "结果", combine, "Y")
    for index, (label, _file, _color) in enumerate(BLOCK_KINDS):
        spawn = add(NN.GKNodeAssetSpawn, "放" + label, -120.0,
                    -1000.0 - index * 180.0, gk_asset=label, gk_solid=True)
        link(block_switch, str(index), spawn, "执行")
        link(combine, "向量", spawn, "位置")

    # 3) 数字键 1~4 -> 侧栏变量『方块序号』= 0~3
    for index, (label, _file, _color) in enumerate(BLOCK_KINDS):
        key = add(NN.GKNodeKey, f"{index + 1} 选{label}", -1100.0,
                  -1500.0 - index * 200.0, gk_key=str(index + 1),
                  gk_action=f"slot_{index + 1}", gk_trigger="JUST")
        branch = add(NN.GKNodeBranch, f"按了{index + 1}?", -850.0,
                     -1500.0 - index * 200.0)
        setter = add(NN.GKNodeSetVar, f"记下{label}", -600.0,
                     -1500.0 - index * 200.0,
                     gk_var_name="方块序号", gk_value=float(index))
        link(sequence, str(index + 4), branch, "执行")
        link(key, "结果", branch, "条件")
        link(branch, "是", setter, "执行")

    # 4) ESC -> 退出
    esc = add(NN.GKNodeKey, "ESC", -1100.0, -900.0,
              gk_key="ESC", gk_action="ui_cancel", gk_trigger="JUST")
    esc_branch = add(NN.GKNodeBranch, "要退出吗", -850.0, -900.0)
    quit_game = add(NN.GKNodeQuitGame, "退出", -600.0, -900.0)
    link(sequence, "8", esc_branch, "执行")
    link(esc, "结果", esc_branch, "条件")
    link(esc_branch, "是", quit_game, "执行")

    # ---- _physics_process:移动 + 怪物追击 + HUD + 跳跃 ----
    physics = add(NN.GKNodeOnPhysicsProcess, "物理帧", -1600.0, -1200.0)
    direction = add(NN.GKNodeInputDirection, "WASD", -1600.0, -1450.0)
    move = add(NN.GKNodeMoveDirection, "移动", -1300.0, -1200.0,
               gk_speed=6.0, gk_keep_y=True, gk_follow_camera=True,
               gk_face_move=False)
    on_floor = add(NN.GKNodeIsOnFloor, "在地面", -1300.0, -1500.0)
    jump_key = add(NN.GKNodeKey, "空格", -1300.0, -1680.0,
                   gk_action="jump", gk_trigger="JUST")
    both = add(NN.GKNodeLogic, "能跳吗", -1050.0, -1550.0, gk_op="AND")

    link(physics, "执行", move, "执行")
    link(direction, "方向", move, "方向")

    # 怪物:朝向玩家走(脚本在玩家身上,所以"谁走"写相对路径)
    chases = []
    previous = move
    for index, (name, _pos) in enumerate(MONSTERS):
        chase = add(NN.GKNodeMoveToward, "追" + name, -1050.0,
                    -1200.0 + index * 220.0,
                    gk_target=f"../Monsters/{name}", gk_goal=TARGET_SELF,
                    gk_speed=MONSTER_SPEED, gk_stop_distance=MONSTER_REACH,
                    gk_turn=True, gk_accel=6.0)
        link(previous, "完成", chase, "执行")
        chases.append(chase)
        previous = chase

    # HUD:生命文字 + 血条(每帧刷新;控件建过一次之后只改值)
    health_read = add(NN.GKNodeGetVar, "读生命", -1500.0, -1900.0,
                      gk_var_name="生命")
    health_num = add(NN.GKNodeNumberText, "生命数字", -1300.0, -1900.0)
    health_join = add(NN.GKNodeTextJoin, "生命文字", -1100.0, -1900.0,
                      gk_a="生命 ")
    hp_text = add(NN.GKNodeUiText, "生命文本", -850.0, -1850.0,
                  gk_size=20, gk_align="TOP_LEFT", gk_x=20.0, gk_y=44.0)
    hp_bar = add(NN.GKNodeUiBar, "生命条", -560.0, -1850.0,
                 gk_max=100.0, gk_x=20.0, gk_y=76.0,
                 gk_width=220.0, gk_height=16.0,
                 gk_color=(0.85, 0.22, 0.22, 1.0))
    link(health_read, "值", health_num, "数值")
    link(health_read, "值", hp_bar, "数值")
    link(health_num, "文字", health_join, "乙")
    link(health_join, "结果", hp_text, "文字")
    link(previous, "完成", hp_text, "执行")
    link(hp_text, "完成", hp_bar, "执行")

    # 攻击:任意一只怪物贴上来 -> 冷却 1 秒 -> 扣 8 点 -> 掉到 0 送回出生点
    reach_or = add(NN.GKNodeLogic, "有怪物贴上来吗", -1050.0, -2100.0,
                   gk_op="OR", gk_input_count=len(MONSTERS))
    for index, chase in enumerate(chases):
        link(chase, "到达", reach_or, chr(ord("A") + index))
    cooldown = add(NN.GKNodeCooldown, "每口间隔", -800.0, -2100.0,
                   gk_seconds=1.0)
    hurt_float = add(NN.GKNodeFloatText, "伤害飘字", -560.0, -2100.0,
                     gk_target=TARGET_SELF, gk_prefix="生命 -",
                     gk_value=8.0, gk_time=0.7, gk_rise=1.0,
                     gk_color=(1.0, 0.35, 0.35, 1.0))
    hurt = add(NN.GKNodeAddVar, "扣血", -320.0, -2100.0,
               gk_var_name="生命", gk_amount=-8.0)
    dead = add(NN.GKNodeCompare, "血空了吗", -80.0, -2150.0,
               gk_op="LE", gk_b="0")
    dead_branch = add(NN.GKNodeBranch, "倒下了吗", 160.0, -2100.0)
    revive = add(NN.GKNodeSetVar, "补满血", 400.0, -2100.0,
                 gk_var_name="生命", gk_value=100.0)
    back = add(NN.GKNodeSetPosition, "送回出生点", 640.0, -2100.0,
               gk_target=TARGET_SELF, gk_x=PLAYER_SPAWN[0],
               gk_y=PLAYER_SPAWN[1], gk_z=PLAYER_SPAWN[2])
    note = add(NN.GKNodePrint, "提示", 880.0, -2100.0,
               gk_text="你被怪物击倒,已回到出生点")
    link(cooldown, "可以", hurt_float, "执行")
    link(hurt_float, "完成", hurt, "执行")
    link(hurt, "完成", dead_branch, "执行")
    link(hurt, "新值", dead, "A")
    link(dead, "结果", dead_branch, "条件")
    link(dead_branch, "是", revive, "执行")
    link(revive, "完成", back, "执行")
    link(back, "完成", note, "执行")

    # 跳跃放在最后:怪物每帧都要动,HUD 每帧都要刷。
    # 用『序列』把"扣血判定"和"跳跃判定"分成两条独立出口 ——
    # 直接串联的话,共用的下游会被生成器复制两遍(是/否各一份)。
    jump_branch = add(NN.GKNodeBranch, "如果", -800.0, -1200.0)
    jump = add(NN.GKNodeJump, "跳跃", -550.0, -1200.0, gk_jump_force=8.5)
    damage_branch = add(NN.GKNodeBranch, "被贴上了吗", -560.0, -1000.0)
    actions = add(NN.GKNodeSequence, "每物理帧两件事", -800.0, -1000.0,
                  gk_step_count=2)
    link(hp_bar, "完成", actions, "执行")
    link(actions, "1", damage_branch, "执行")
    link(actions, "2", jump_branch, "执行")
    link(reach_or, "结果", damage_branch, "条件")
    link(damage_branch, "是", cooldown, "执行")
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
slot_1={
"deadzone": 0.2,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":49,"key_label":0,"unicode":0,"location":0,"echo":false,"script":null)
]
}
slot_2={
"deadzone": 0.2,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":50,"key_label":0,"unicode":0,"location":0,"echo":false,"script":null)
]
}
slot_3={
"deadzone": 0.2,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":51,"key_label":0,"unicode":0,"location":0,"echo":false,"script":null)
]
}
slot_4={
"deadzone": 0.2,
"events": [Object(InputEventKey,"resource_local_to_scene":false,"resource_name":"","device":-1,"window_id":0,"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,"meta_pressed":false,"pressed":false,"keycode":0,"physical_keycode":52,"key_label":0,"unicode":0,"location":0,"echo":false,"script":null)
]
}

[layer_names]

3d_physics/layer_1="blocks"
3d_physics/layer_2="player"
3d_physics/layer_3="mobs"

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
        _material("monster", (0.58, 0.12, 0.12)),
        '[sub_resource type="BoxMesh" id="monster_mesh"]\n'
        "size = Vector3(0.7, 1.4, 0.7)",
        '[sub_resource type="CapsuleShape3D" id="monster_shape"]\n'
        "radius = 0.35\nheight = 1.5",
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
    # 会追人的小怪:CharacterBody3D + 红盒子,逻辑由玩家脚本驱动
    parts.append('[node name="Monsters" type="Node3D" parent="."]')
    parts.append("")
    for name, (mx, my, mz) in MONSTERS:
        parts.append(f'[node name="{name}" type="CharacterBody3D" parent="Monsters"]')
        parts.append(f"transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, "
                     f"{mx}, {my}, {mz})")
        parts.append("collision_layer = 4")
        parts.append("collision_mask = 1")
        parts.append("")
        parts.append(f'[node name="Shape" type="CollisionShape3D" '
                     f'parent="Monsters/{name}"]')
        parts.append("transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, "
                     "0, 0.75, 0)")
        parts.append('shape = SubResource("monster_shape")')
        parts.append("")
        parts.append(f'[node name="Mesh" type="MeshInstance3D" '
                     f'parent="Monsters/{name}"]')
        parts.append("transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, "
                     "0, 0.75, 0)")
        parts.append('mesh = SubResource("monster_mesh")')
        parts.append('material_override = SubResource("mat_monster")')
        parts.append("")
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
                 'WASD 移动 / 空格 跳 / 鼠标 转视角 / 1 草 2 石 3 沙 4 木 / '
                 '左键或 E 挖 / 右键或 Q 放 / ESC 退出 · 小心红皮怪"')
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
- 数字键 **1~4** 换手上的方块:1 草 / 2 石 / 3 沙 / 4 木;
- 左键或 E 破坏准星指着的方块;
- 右键或 Q 在准星命中的方块上放一块(放的是当前选中的那种);
- 三只红皮怪会追你,贴上来自动攻击(每口 -8 生命,左上角有血条);
  生命掉到 0 会补满并送回出生点;
- ESC 退出。

## 用到的节点

| 功能 | 节点 |
|---|---|
| 第一人称视角 | 『鼠标_移动量』+『向量_拆分』+『数学_运算』+『相机_旋转』 |
| 移动 / 重力 / 跳跃 | 『输入_方向』+『移动_按方向(随相机朝向 / 重力模式)』+『移动_跳跃』 |
| 挖方块 | 『射线_检测(当前相机 / 相机前向)』+『鼠标_按键按下』+『实例_销毁』 |
| 选方块 | 侧栏变量『方块序号』+『输入_按键(1~4)』+『如果_否则』+『变量_设置』 |
| 放方块 | 『变量_读取』+『多路分支』(Switch on Int)+ 四个『资产_创建实例』 |
| 怪物追击 | 『移动_朝向目标』(谁走 = `../Monsters/怪物N`,目标 = 自己) |
| 怪物攻击 | 『到达』x3 ->『逻辑_或』->『流程_冷却』->『变量_增减』 |
| 生命 HUD | 『变量_读取』+『文本_数字』+『文本_拼接』+『界面_文本』+『界面_进度条』 |
| 倒下重来 | 『逻辑_比较』+『如果_否则』+『变量_设置』+『移动_设置位置』+『打印_输出』 |
| 每帧八件事 | 『序列』(转视角 / 挖 / 放 / 选 1~4 / ESC) |
| 鼠标锁定 | 『鼠标_模式(锁定)』 |

## 重新生成

```powershell
python scripts\\make_minecraft_demo.py
```

世界里的方块不是资产,而是场景里独立的 `StaticBody3D` —— 射线打中的是一块,
所以『实例_销毁』只会移除那一块。手上能放的四档方块是 `blocks/*.tscn`
(由同一个生成脚本写出),想加第五种就在 `make_minecraft_demo.py` 的
`BLOCK_KINDS` 里加一行,并给 `project.godot` 补一个 `slot_5` 输入动作。
"""


def block_scene(color):
    """手上能放的一种方块:1 米盒子 + 自己的颜色。"""
    return (f'[gd_scene load_steps=3 format=3]\n'
            f'\n'
            f'[sub_resource type="BoxMesh" id="mesh"]\n'
            f'size = Vector3(1, 1, 1)\n'
            f'\n'
            f'[sub_resource type="StandardMaterial3D" id="mat"]\n'
            f'albedo_color = Color({color[0]}, {color[1]}, {color[2]}, 1)\n'
            f'roughness = 0.9\n'
            f'\n'
            f'[node name="Block" type="Node3D"]\n'
            f'\n'
            f'[node name="Mesh" type="MeshInstance3D" parent="."]\n'
            f'mesh = SubResource("mesh")\n'
            f'material_override = SubResource("mat")\n')


def main():
    tree = build_tree()
    assets = {}
    for label, file_name, _color in BLOCK_KINDS:
        assets[label] = {
            "path": f"res://blocks/{file_name}.tscn",
            "shape": 0,
            "shapes": [{"kind": 0, "dims": (1.0, 1.0, 1.0),
                        "offset": (0.0, 0.0, 0.0)}],
            "layer": 1,
            "mask": 1,
        }
    code, warnings = codegen.generate(
        tree, dict(dimension="3", base_class="CharacterBody3D",
                   class_name="MinecraftPlayer", export_tunables=True,
                   assets=assets))
    for warning in warnings:
        print("警告:", warning)
    os.makedirs(os.path.dirname(SCRIPT_PATH), exist_ok=True)
    os.makedirs(os.path.join(OUT_DIR, "blocks"), exist_ok=True)
    exporter.write_text(SCRIPT_PATH, code)
    exporter.write_text(os.path.join(OUT_DIR, "Main.tscn"), build_scene())
    for label, file_name, color in BLOCK_KINDS:
        exporter.write_text(os.path.join(OUT_DIR, "blocks", f"{file_name}.tscn"),
                            block_scene(color))
    exporter.write_text(os.path.join(OUT_DIR, "project.godot"), PROJECT_GODOT)
    exporter.write_text(os.path.join(OUT_DIR, "README.md"), README)
    print(f"已生成 {OUT_DIR}")
    print(f"  脚本 {len(code.splitlines())} 行")
    print("  Main.tscn:10x10 方块地面 + 树 + 石台 + 沙地 + 第一人称玩家")
    print("  blocks/:草 / 石 / 沙 / 木 四种可放置方块")


if __name__ == "__main__":
    main()
