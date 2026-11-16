# -*- coding: utf-8 -*-
"""生成"我的世界"风格的第一人称方块示例(第一百九十七轮:改成真 Blender 工程)。

用法:
    blender -b --gpu-backend vulkan --python scripts\\make_minecraft_demo.py

产出:
    examples/demo_minecraft/我的世界.blend          (源文件:场景 + 节点图)
    examples/demo_minecraft/我的世界_godot/          (导出产物,可用 Godot 打开)

玩法:
  开始界面(像素风景背景图 + 开始游戏 / 读取存档 / 新建世界 / 退出游戏)
  → 进世界后 WASD 移动 / 空格跳 / 鼠标转视角 / 数字键 1~9 或滚轮选快捷栏
  / 左键破坏准星指着的方块(生存按住累积挖掘进度) / 右键放置或使用
  / 右键对着箱子 = 开箱(原版没有单独的开箱键)
  / 放置前先查目标格有没有实体,空着才放
  / I 打开背包(36 格,点『合成』进合成界面,可拾起、放下、合并、交换、丢弃)
  / 摔落超过 3 格扣血,掉出世界 / 生命归零回出生点
  / 生存模式底部显示血条 + 10 格饥饿条(创造模式隐藏)
  / 三只方块僵尸先站着不动(这轮关掉追击和攻击)/ ESC 暂停(可命名保存)。

节点图覆盖:
  * 开始界面:『界面_面板』『界面_文本』『界面_按钮』『界面_暂停游戏』
    + 按钮点击链(新建 / 读档 / 设置面板由文本块脚本运行时搭);
  * 第一人称:『相机_旋转』(物理帧)+『移动_按方向(随相机朝向)』;
  * 射线交互:『射线_检测』+『实例_销毁』;
  * 四档方块:侧栏变量『方块序号』+『多路分支』+ 四个『资产_创建实例』;
  * 命名存档 / 世界设置 / 背包:『脚本_加载』+『脚本_调用』+ 文本块脚本;
  * HUD:『界面_文本』+『界面_进度条』+『界面_飘字』。
"""

import math
import os
import random
import shutil
import struct
import sys
import zlib

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for path in (ROOT, HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

OUT_DIR = os.path.join(ROOT, "examples", "demo_minecraft")
BLEND_PATH = os.path.join(OUT_DIR, "我的世界.blend")
GODOT_DIR = os.path.join(OUT_DIR, "我的世界_godot")

SCENE_NAME = "我的世界"
TREE_NAME = "我的世界_逻辑"
PLAYER_NAME = "Player"
CAMERA_NAME = "第一人称相机"

# 地面 / 丘陵 / 树 / 矿石都由运行时的无限地形脚本生成(见 CHUNK_SCRIPT)
BLOCK_COLORS = {
    "grass": (0.24, 0.60, 0.22),
    "dirt": (0.42, 0.30, 0.18),
    "stone": (0.46, 0.48, 0.52),
    "wood": (0.42, 0.27, 0.12),
    "leaves": (0.16, 0.52, 0.20),
    "sand": (0.80, 0.72, 0.46),
    "player": (0.25, 0.45, 0.85),
    "sheep": (0.92, 0.92, 0.88),
}

# ── 原版风格像素贴图(16×16,程序生成)────────────────────────────────
# 为什么自己画像素图:方块颜色再准,没有"一格格像素"的味道也不像原版。
# 贴图在导出前用纯 Python 写成 PNG,既给 Blender 材质预览用,也直接落到
# Godot 的 art/ 给运行时地形用(见 CHUNK_SCRIPT 的 res://art/mc_*.png)。
TEX_SIZE = 16
TEXTURE_DIR = os.path.join(ROOT, "_dist", "minecraft_textures")

# 方块 -> (贴图名, 六面 UV)。UV 顺序和 make_box_mesh 的面顺序一致:
# 底 / 顶 / -Y / +X / +Y / -X。
BLOCK_TEXTURES = {
    "grass": "mc_grass",
    "dirt": "mc_dirt",
    "stone": "mc_stone",
    "wood": "mc_wood",
    "leaves": "mc_leaves",
    "sand": "mc_sand",
    "coal": "mc_coal",
    "iron": "mc_iron",
    "snow": "mc_snow",
    "table": "mc_table",
    "chest": "mc_chest",
}
_FULL = (0.0, 0.0, 1.0, 1.0)
_GRASS_TOP = (0.0, 0.0, 1.0 / 3.0, 1.0)
_GRASS_SIDE = (1.0 / 3.0, 0.0, 2.0 / 3.0, 1.0)
_GRASS_BOTTOM = (2.0 / 3.0, 0.0, 1.0, 1.0)
_WOOD_SIDE = (0.0, 0.0, 0.5, 1.0)
_WOOD_TOP = (0.5, 0.0, 1.0, 1.0)
BLOCK_UVS = {
    "grass": (_GRASS_BOTTOM, _GRASS_TOP, _GRASS_SIDE, _GRASS_SIDE,
              _GRASS_SIDE, _GRASS_SIDE),
    "dirt": (_FULL, _FULL, _FULL, _FULL, _FULL, _FULL),
    "stone": (_FULL, _FULL, _FULL, _FULL, _FULL, _FULL),
    "wood": (_WOOD_TOP, _WOOD_TOP, _WOOD_SIDE, _WOOD_SIDE,
             _WOOD_SIDE, _WOOD_SIDE),
    "leaves": (_FULL, _FULL, _FULL, _FULL, _FULL, _FULL),
    "sand": (_FULL, _FULL, _FULL, _FULL, _FULL, _FULL),
    "coal": (_FULL, _FULL, _FULL, _FULL, _FULL, _FULL),
    "iron": (_FULL, _FULL, _FULL, _FULL, _FULL, _FULL),
    "snow": (_FULL, _FULL, _FULL, _FULL, _FULL, _FULL),
    "table": (_FULL, _FULL, _FULL, _FULL, _FULL, _FULL),
    "chest": (_FULL, _FULL, _FULL, _FULL, _FULL, _FULL),
}

# 手上能拿的档位:显示名 / 颜色 / 方块类型(None = 不是方块,只有功能)
# 数字键 1~7 切的就是这个列表,『多路分支』的出口 0~6 也按这个顺序接。
BLOCK_KINDS = (
    ("草方块", (0.24, 0.60, 0.22), "grass"),
    ("石头方块", (0.46, 0.48, 0.52), "stone"),
    ("沙子方块", (0.80, 0.72, 0.46), "sand"),
    ("木头方块", (0.42, 0.27, 0.12), "wood"),
    ("树叶方块", (0.16, 0.52, 0.20), "leaves"),
    ("苹果", None, None),
    ("剑", None, None),
)
# 可放置方块(第两百一十三轮):前 5 档开局就有,工作台 / 箱子要合成出来。
# 『多路分支』的出口号 = 背包脚本写的『手持方块』,必须和这个列表顺序一致。
PLACE_BLOCKS = (
    ("草方块", (0.24, 0.60, 0.22), "grass"),
    ("石头方块", (0.46, 0.48, 0.52), "stone"),
    ("沙子方块", (0.80, 0.72, 0.46), "sand"),
    ("木头方块", (0.42, 0.27, 0.12), "wood"),
    ("树叶方块", (0.16, 0.52, 0.20), "leaves"),
    ("工作台", (0.62, 0.44, 0.22), "table"),
    ("箱子", (0.52, 0.35, 0.15), "chest"),
)
PLACE_SLOTS = len(PLACE_BLOCKS)
# 物品栏缩略图(顺序和 BLOCK_KINDS 一致;文件由 ensure_item_icons() 生成)
ITEM_ICONS = ("mc_icon_grass", "mc_icon_stone", "mc_icon_sand", "mc_icon_wood",
              "mc_icon_leaves", "mc_icon_apple", "mc_icon_sword")

# ── 背包(第两百零九轮)────────────────────────────────────────────
# 36 格:0~8 是快捷栏,9~35 是主背包;单格堆叠上限 64(剑不叠加)。
# 物品用字符串类型名表示(和地形贴图名一致):grass / stone / sand /
# wood / leaves / apple / sword。空槽用 null。
HOTBAR_SLOTS = 9
INVENTORY_SLOTS = 36
STACK_LIMIT = 64
# 快捷栏 9 格的初始图标:前 7 格和 ITEM_ICONS 一致,最后两格是空槽占位
HOTBAR_ICONS = tuple(ITEM_ICONS) + ("mc_icon_empty", "mc_icon_empty")
# 节点图里 9 个数字键节点的名字后缀(只有第 8/9 格和原 7 档不同)
HOTBAR_LABELS = tuple(name for name, _color, _kind in BLOCK_KINDS) + (
    "备用八", "备用九")

# 会追人的小怪(名字 / Godot 坐标里的出生点)
MONSTERS = (
    ("怪物1", (2.5, 1.0, -2.5)),
    ("怪物2", (3.5, 1.0, -4.0)),
    ("怪物3", (-2.0, 1.0, -3.5)),
)
MONSTER_SPEED = 2.6
MONSTER_REACH = 1.3
PLAYER_SPAWN = (0.5, 1.0, 0.5)
THIRD_CAMERA = "第三人称相机"

# 被动生物:羊(名字 / 出生点)
SHEEP = (
    ("羊1", (4.0, 1.0, 1.0)),
    ("羊2", (-4.0, 1.0, 1.5)),
)
# 猪(内容加法第一批):这里的坐标只是导出时的初始位置,
# 运行时猪脚本会按出生点保护范围(半径 16)之外重摆。
PIGS = (
    ("猪1", (14.0, 1.0, 12.0)),
    ("猪2", (-14.0, 1.0, 16.0)),
)

# ── 无限区块(自定义脚本,存在 .blend 的文本块里)──────────────────────
# 为什么用『脚本_加载』而不是纯节点:每帧要维护 21x21 块地形的增删,
# 节点图里做这件事会是几百个节点。脚本仍然**写在 .blend 里**(文本块),
# 导出成 res://scripts/custom/,和节点图一起走同一条导出链。
CHUNK_SCRIPT = """extends Node3D

## 原版风格无限地形:以玩家为中心铺 (2R+1)^2 格方块。
## 每格是 1~3 层方块,每块都是独立的 StaticBody3D(能一块一块挖);
## 草方块顶绿 / 侧草 / 底土,远处长树。贴图来自 res://art/mc_*.png。

## 玩家挖掉一块时的通知(背包脚本接这个信号 +1 物品)。
## 只在这块是"被挖掉"时发:区块卸载 / 换世界都会先打 gk_retire 标记,
## 那条路径不发信号,否则走两步就白捡一堆方块。
signal block_mined(kind: String)

const BLOCK := 1.0
## 世界高度(M2a 第两百一十八轮):y 索引 0 ~ WORLD_HEIGHT-1。
## 这些常量是唯一的高度来源,后面 section / 存档 / 迁移都读这里,不许再写死数字。
## M2a 只抬容量、不改噪声外观:地表生成仍压在原观感(TERRAIN_MAX = 24);
## M2b 按 64 重定地形噪声(C-E8)后才抬到 60,海平面仍固定在 32。
const WORLD_HEIGHT := 64
const SEA_LEVEL := 32
## 分段高度:每 16 层一个 section(0-15 / 16-31 / 32-47 / 48-63)
const SECTION_HEIGHT := 16
## 分段合并不再按固定段数(旧版 2 段/帧会叠出 9ms 长帧):改成吃
## BUILD_BUDGET_US 的时间预算,并且和列 / 远景分帧,一帧只干一类重活。
## 地表最高层数(M2b):按世界高度 64 重定后,山地最高到 60 层;
## 海平面 32 以下的低地灌静态水(见 _build_water_surface)。
const TERRAIN_MAX := 60
## 出生点(第两百一十六轮):不改地形,只在出生区域里"挑一块好站的地方"。
## 候选范围 ±SPAWN_SEARCH 格;评分看以候选为中心 (2*SPAWN_NEIGHBORS+1)² 的
## 高度差 —— 落差最小者胜,完全不合适的候选(树干占住上方)直接跳过。
## 原版出生点也不会压平地形,这里恢复噪声原样。
const SPAWN_SEARCH := 64
## 评分范围 7×7:原版出生点周围本来就留得出活动空间;范围太小会挑到
## "一小块平地、两步外就抬高一格"的台地,正对台阶放方块会被挡。
const SPAWN_NEIGHBORS := 3
## 出生点保护:以出生点为心 33×33(半径 16)不放敌对生物。
const SPAWN_SAFE_RADIUS := 16.0
## 植被(第两百一十六轮):短草 / 两种花。只在表层草方块上、按群系密度长,
## 无碰撞(Area3D)、挖一下就没,和原版一样不挡路。
const PLANT_KINDS := ["tall_grass", "flower_yellow", "flower_red"]
## 铺地形的时机与速度(真机实测,别改):
##   * 在 _process(渲染帧)里铺,不要在 _physics_process 里 —— 物理步进中
##     添加 StaticBody3D 会偶发 200~400ms 的长帧,换到渲染帧就完全平滑;
##   * 近景逐方块要碰物理:一帧只给很少的列数,远景整块合并才敢多铺;
##   * 暂停 / 加载时屏幕被盖住,允许一次多铺一些,进世界前就铺满。
const BUILD_BUDGET_US := 3000        # 游戏内一帧铺地形的时间预算
const BUILD_BUDGET_MENU_US := 12000  # 菜单 / 加载时(屏幕盖着)
## 每帧上限只是"别一次做太多"的保险,真正卡量的是下面的时间预算:
## 实测建一列不到 0.1ms,以前一帧只给 3 列,走一格要等上整整一屏 ——
## 用户说的"走一步刷一步"。现在放到 24 列,一帧也就 1~2ms,预算仍兜底。
const NEAR_PER_FRAME := 24
const NEAR_PER_FRAME_MENU := 64
const FAR_REGION_PER_FRAME := 4
const FAR_REGION_PER_FRAME_MENU := 8
## 远景区块边长(格):一整块区域(16x16 列 = 原版一个区块)合并成几个网格,
## 只画不碰。按区块对齐,只有跨区块时才换档 —— 走一步不会触发整片重建。
const FAR_REGION := 16
## 近景 = 玩家所在区块 ±NEAR_REGIONS 个区块,逐方块、能挖能站。
const NEAR_REGIONS := 1
const NEAR_RADIUS := (NEAR_REGIONS + 1) * FAR_REGION
## 渲染距离档位(格):按原版的 8/12/16/24/32 区块换算(1 区块 = 16 格),
## 索引和玩家变量『渲染距离』一一对应(1 起数)。默认 16 区块 = 原版默认档;
## 帧率不够时按 16→12→8 往下降一档。
const RADIUS_STEPS := [128, 192, 256, 384, 512]
const RADIUS_CHUNKS := [8, 12, 16, 24, 32]
## 世界大小档位(半径,格):0 = 无限
const WORLD_SIZES := [0, 128, 256, 512]
## 植被密度档位(百分比):默认 10 = 原来的 %10==0,观感完全一致
const TREE_PERCENTS := [0, 5, 10, 25]

## 玩家放下的方块节点名 -> 本脚本里的方块类型(读档后按同一套网格重建)
const PLACE_KINDS := {
	"草方块": "grass",
	"石头方块": "stone",
	"沙子方块": "sand",
	"木头方块": "wood",
	"树叶方块": "leaves",
	"工作台": "table",
	"箱子": "chest",
}

var _cells: Dictionary = {}
## 近景区块(整块对齐):玩家所在区块 ±NEAR_REGIONS
var _near_regions: Dictionary = {}
## 远景:区块 -> 合并网格的根节点(null = 这块区域在世界边界外,不用建)
var _far_regions: Dictionary = {}
## 近景重建中的过渡:区块 -> 还没卸的远景网格。新近景的列铺完之前先留着
## 远景网格,走起来不会出现"脚下那块地先空一下再长出来"的缺口。
var _handover: Dictionary = {}
var _near_queue: Array = []
var _region_queue: Array = []
var _progress_total := 1
var _player_region := Vector2i(1 << 30, 1 << 30)
var _center := Vector2i(1 << 30, 1 << 30)
## 出生点格子(第两百一十六轮):按当前世界设置算一次,apply_world 时作废
var _spawn_cell := Vector2i(1 << 30, 1 << 30)
var _visuals: Dictionary = {}
var _materials: Dictionary = {}
var _plant_materials: Dictionary = {}
var _plant_meshes: Dictionary = {}
var _leaves_mesh: ArrayMesh
## 静态水(M2b):水面材质 / 一个 1×1 水平面片(近景 section 与远景共用)
var _water_mat: StandardMaterial3D
var _water_mesh: ArrayMesh
var _world: Node
## 世界设置(由玩家变量读进来;默认值 = 原来的观感)
var _radius := 256
var _world_seed := 0
var _terrain_type := 2
## 地表生成上限:单点读 TERRAIN_MAX(世界高度见 WORLD_HEIGHT / SEA_LEVEL)。
var _terrain_ceiling := TERRAIN_MAX
## 画质项(柔和光影):顶面亮 / 侧面暗 + 角落 AO。设置面板可关。
var _soft_light := true
var _world_size := 0
var _tree_percent := 10
## 挖掘(第两百一十五轮):按 Java 版公式
##   时间 = 硬度 × 1.5 ÷ 工具倍率(手里没工具 = 1)
##   方块要求工具、但手里工具类型 / 品质不满足:时间 = 硬度 × 5,且不掉落
## 表里存的是原版硬度,不是秒数;具体秒数由 _break_time() 现算。
const BLOCK_HARDNESS := {
	"grass": 0.6,
	"dirt": 0.5,
	"sand": 0.5,
	"stone": 1.5,
	"wood": 2.0,
	"leaves": 0.2,
	"coal": 3.0,
	"iron": 3.0,
	"snow": 0.2,
	"table": 2.5,
	"chest": 2.5,
	## 植被:硬度 0 = 点一下就碎(和原版一样瞬破,不掉裂纹)
	"tall_grass": 0.0,
	"flower_yellow": 0.0,
	"flower_red": 0.0,
}
## 方块要求的最低工具:类型 + 品质(1 木 / 2 石 / 3 铁)。
## 不满足时仍能挖,但按 5×硬度计时,挖掉也不发掉落物。
const REQUIRES_TOOL := {
	"stone": {"type": "pickaxe", "tier": 1},
	"coal": {"type": "pickaxe", "tier": 1},
	"iron": {"type": "pickaxe", "tier": 2},
}
## 工具品质 -> 倍率 / 等级。工具物品还没做;以后 "wood_pickaxe" 这类
## 物品进背包后,_tool_state() 按前缀 + 后缀自动认出来,不用改挖掘逻辑。
const TOOL_SPEED := {"wood": 2.0, "stone": 4.0, "iron": 6.0}
const TOOL_TIER := {"wood": 1, "stone": 2, "iron": 3}
const TOOL_TYPES := ["pickaxe", "axe", "shovel", "sword"]
const CRACK_STAGES := 10
var _mine_target: Node = null
## 准星指着的层号:合批后同一列里换一格也要重置挖掘进度
var _mine_level := -1
var _mine_kind := ""
var _mine_held := ""
var _mine_elapsed := 0.0
var _mine_cooldown := 0.0
var _crack: MeshInstance3D
var _crack_stage := -1
var _time_index := 1
var _cull_timer := 0.0
## 单块方块被场景收走时要重建的分段,下一帧在 _process 里统一处理
var _section_rebuild_queue: Array = []
## 上一帧铺地形耗时(微秒),只给分区帧时统计读
var _build_us_last := 0
## 上一次预铺的耗时(微秒)。预铺在加载屏里按帧做,
## 和 _build_us_last 分开记:同一帧里 _step_build 会把它覆盖成 0。
var _prebuild_us_last := 0
## ── 先加载后进入 / 卸载迟滞(M2a 第三段)──────────────────────────
## 出生点预铺:加载屏里按帧推进(每帧 spawn_build_step),进度条读
## spawn_build_progress() —— 等的时候看得到进度,不是卡一帧再跳到 100%。
var _spawn_active := false
## 5×5 出生区里近景以外的那几块:只补碰撞列(稳态碰撞仍按已定约束
## 只跟玩家 ±1 区块走,这圈在第一次跨区块时自然卸掉)。
var _spawn_pad_queue: Array = []
## 出生圈里"只铺了碰撞、还没长植被"的格子:变成近景时由 _replan 补上,
## 避免远景合并网格的树 / 草花和近景列重面。
var _bare_cells: Dictionary = {}
## 本次预铺承诺铺到多远:雾起点。更远的缺口只能出现在雾后。
var _spawn_far_limit := 0.0
var _spawn_total := 0
## 远景"重建"计数:同一个区块 卸掉 -> 再建 才 +1。
## 近景降级成远景(拆了近景列、补合并网格)不算重建;迟滞区内往返时
## 区块一直留在 _far_regions,这个数必须不动。
var _far_unloaded: Dictionary = {}
var _chunk_rebuild_count := 0
## 最近一次 _replan 算出的"该加载"远景集合:用来数迟滞区里多留了哪些区块。
var _wanted: Dictionary = {}
## ── 分段网格(M2a 第二段)────────────────────────────────────────
## 近景的视觉不再一格一个 MeshInstance3D,改成"16×16 列 × SECTION_HEIGHT 高"
## 的立方 section 合并网格:每个 section 里按方块类型各合并成一个网格。
## 碰撞仍是每列一个 StaticBody3D + 连续实心段(第两百一十七轮),只是不再
## 给每块挂可视网格。key = Vector3i(区块x, 段号, 区块z);null = 这个 section
## 没有任何方块,不用建(下次也不再排队)。
var _sections: Dictionary = {}
var _section_queue: Array = []
## 逐个 section 的合并耗时(微秒):探针要拿"挖 / 放后只重建所在 section"的证据
var _section_build_count := 0
var _section_build_total_us := 0
var _section_build_last_us := 0
var _section_build_max_us := 0
var _section_rebuild_log: Array = []
## 每一次 section 合成的耗时(微秒),给 M2a 收口的 chunk_build_ms 分位数用
var _section_us_samples: Array = []
## 高度缓存(M2a 第二段):_height_at 是纯函数(只读种子 / 地形类型 / 上限),
## 挖 / 放只改方块不改高度。分段重算要查 18×18 的高度,缓存后第二次重建
## 基本不花时间,否则每挖一格都要重算一遍噪声(实测 324 格约 27ms)。
var _height_cache: Dictionary = {}
## 表层方块类型缓存:同样是纯函数(高度 + 种子 + 群系),挖 / 放不会改它。
## 分段重建时 256 个表层格不用再算 4 邻落差和群系噪声。
var _kind_cache: Dictionary = {}
## 世界改动(存档用): "x,z,level" -> true / 方块类型;"x,z" -> true(整棵树)
var _removed: Dictionary = {}
var _placed: Dictionary = {}
var _removed_trees: Dictionary = {}
## 玩家放下的方块节点(增量登记):_cull_placed 只遍历这一小撮。
## 以前每 0.25 秒对全场景 find_children —— 出生圈 6400 个物理体时那一帧 28ms。
var _placed_nodes: Array = []
var _placed_seeded := false


func _ready() -> void:
	# 开始菜单是暂停状态:设成 ALWAYS 让地形在菜单期间就铺好
	process_mode = Node.PROCESS_MODE_ALWAYS
	_materials["grass"] = _block_material("res://art/mc_grass.png")
	_materials["dirt"] = _block_material("res://art/mc_dirt.png")
	_materials["stone"] = _block_material("res://art/mc_stone.png")
	_materials["sand"] = _block_material("res://art/mc_sand.png")
	_materials["wood"] = _block_material("res://art/mc_wood.png")
	_materials["leaves"] = _block_material("res://art/mc_leaves.png")
	_materials["coal"] = _block_material("res://art/mc_coal.png")
	_materials["iron"] = _block_material("res://art/mc_iron.png")
	_materials["snow"] = _block_material("res://art/mc_snow.png")
	_materials["table"] = _block_material("res://art/mc_table.png")
	_materials["chest"] = _block_material("res://art/mc_chest.png")
	# 植被贴图带透明通道:一张 16×16 的草 / 花,画在十字交叉的两片上
	for plant in PLANT_KINDS:
		_plant_materials[plant] = _plant_material("res://art/mc_%s.png" % plant)
		_plant_meshes[plant] = _build_plant_mesh()
	# 放置方块进树时登记一次,替代每 0.25 秒的全场扫描
	get_tree().node_added.connect(_on_node_added)
	_disable_remat.call_deferred()


## 关掉框架的"新节点进树就重挂一遍材质"(性能关键,真机测过)。
## gk_environment 监听 SceneTree.node_added:每有一个 Node3D 进树,就排一次
## `_gk_remat()`,把整张材质表(20 条 × 全树 find_children)重跑一遍。
## 地形一帧要进几十上百个节点 —— 实测「新建世界→可玩」从 2.3 秒被拖到
## 300 秒以上,玩家看到的就是加载条不动、走一步卡一下。
## 地形方块的颜色/贴图在 _block_material 里自己指定,玩家放下的方块由
## _dress_placed 统一成同一套材质 —— 不需要框架再扫一遍。
func _disable_remat() -> void:
	await get_tree().process_frame
	# 环境脚本不一定挂在场景根上(示例里是根下的『Environment』):扫一次整树,
	# 找到就清掉材质表,让 node_added 里的重挂直接走空表提前返回。
	for node in get_tree().root.find_children("*", "Node", true, false):
		if node.has_method("_gk_remat"):
			node.set("materials", "")
			return


func _block_material(path: String) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	if ResourceLoader.exists(path):
		material.albedo_texture = load(path)
	material.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	material.vertex_color_use_as_albedo = true
	material.vertex_color_is_srgb = true
	return material


## 植被贴图:透明底 + 最近邻过滤 + 双面。原版花草原地是"十字面片",
## 不是方块,所以这里不走方块材质(那个材质会强制按立方体顶点色着色)。
func _plant_material(path: String) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	if ResourceLoader.exists(path):
		material.albedo_texture = load(path)
	material.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA_SCISSOR
	material.alpha_scissor_threshold = 0.5
	return material


## 静态水(M2b):海平面 32 的水面。半透明、双面、不受光照 —— 只做外观,
## 没有碰撞、不进 _removed,也不做流动 / 无限水源(Q13)。
func _water_material() -> StandardMaterial3D:
	if _water_mat != null:
		return _water_mat
	var material := StandardMaterial3D.new()
	material.albedo_color = Color(0.16, 0.44, 0.70, 0.58)
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	material.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
	_water_mat = material
	return material


## 远景水面的 1×1 面片:原点在格子中心、水平铺在 y=0,实例只做平移。
func _water_quad() -> ArrayMesh:
	if _water_mesh != null:
		return _water_mesh
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	_quad(st, Vector3(-0.5, 0.0, -0.5), Vector3(-0.5, 0.0, 0.5),
		Vector3(0.5, 0.0, 0.5), Vector3(0.5, 0.0, -0.5),
		Vector2(0, 0), Vector2(0, 1), Vector2(1, 1), Vector2(1, 0), 1.0)
	_water_mesh = st.commit()
	return _water_mesh


## 两片十字交叉、竖直的方片(底边贴着地面,高 1 格)。
func _build_plant_mesh() -> ArrayMesh:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var h := 0.5
	_add_face(st, Vector3(-h, 0.0, 0.0), Vector3(h, 0.0, 0.0),
		Vector3(h, 1.0, 0.0), Vector3(-h, 1.0, 0.0))
	_add_face(st, Vector3(0.0, 0.0, -h), Vector3(0.0, 0.0, h),
		Vector3(0.0, 1.0, h), Vector3(0.0, 1.0, -h))
	return st.commit()


func _add_face(st: SurfaceTool, a: Vector3, b: Vector3, c: Vector3,
		d: Vector3) -> void:
	var uvs := [Vector2(0, 1), Vector2(1, 1), Vector2(1, 0), Vector2(0, 0)]
	var points := [a, b, c, a, c, d]
	var order := [0, 1, 2, 0, 2, 3]
	for index in order:
		st.set_uv(uvs[index])
		st.add_vertex(points[index])


func _process(delta: float) -> void:
	# 单块方块被场景收走时的分段重建排到下一帧:退出游戏 / 卸载场景那一帧
	# 这里不会再跑,自然不会往正在拆的节点树里 add_child(见 _on_block_exiting)。
	while not _section_rebuild_queue.is_empty():
		var item: Array = _section_rebuild_queue.pop_front()
		_rebuild_sections_at(item[0], item[1], "mine")
	_advance()
	_mine_step(delta)
	# 玩家放下的方块不随区块卸载,单独按渲染距离收 / 放(0.25 秒扫一次)
	_cull_timer += delta
	if _cull_timer >= 0.25:
		_cull_timer = 0.0
		_cull_placed()


func _advance() -> void:
	var host := get_parent()
	if host == null or not (host is Node3D):
		return
	var here: Vector3 = (host as Node3D).global_position
	var cell := Vector2i(int(floor(here.x)), int(floor(here.z)))
	_center = cell
	var region := _region_of(cell)
	if region != _player_region:
		# 只在跨区块时重算范围:走一小步不会触发整片重建(以前每换一格
		# 都要扫一遍全部格子,走起来一顿一顿的)。
		_player_region = region
		_replan(region)
	_step_build()


## 手里拿的工具(现在只有空手):类型 / 品质 / 倍率。
## 工具物品还没进配方,这里先把结构留好 —— 以后背包里出现
## wood_pickaxe / stone_pickaxe 这类名字就能直接用。
func _tool_state() -> Dictionary:
	var kind := ""
	var host := get_parent()
	if host != null:
		kind = str(host.get("手持物品"))
	for material in TOOL_SPEED.keys():
		if kind.begins_with(str(material) + "_") == false:
			continue
		for tool_type in TOOL_TYPES:
			if kind.ends_with(str(tool_type)):
				return {"type": tool_type,
					"tier": int(TOOL_TIER.get(material, 0)),
					"speed": float(TOOL_SPEED.get(material, 1.0))}
	return {"type": "", "tier": 0, "speed": 1.0}


## 原版公式:正确工具 硬度×1.5÷倍率;要求工具但不满足 硬度×5 且不掉落。
func _break_time(kind: String, tool: Dictionary) -> Dictionary:
	var hardness := float(BLOCK_HARDNESS.get(kind, 0.6))
	var need = REQUIRES_TOOL.get(kind)
	if need == null \
			or (str(tool["type"]) == str(need["type"])
				and int(tool["tier"]) >= int(need["tier"])):
		return {"time": hardness * 1.5 / float(tool["speed"]),
			"drops": true}
	return {"time": hardness * 5.0, "drops": false}


## 挖掘(第两百一十五轮):按住左键累积进度,按 _break_time() 出 10 阶段裂纹;
## 松开 / 换目标 / 换手持物品 / 打开界面都会把进度清零。创造模式直接碎。
func _mine_step(delta: float) -> void:
	_mine_cooldown = maxf(0.0, _mine_cooldown - delta)
	if get_tree().paused:
		_reset_mine()
		return
	var host := get_parent()
	if host == null:
		return
	# 换手持物品:原版会立刻打断当前挖掘进度
	var held := str(host.get("手持物品"))
	if held != _mine_held:
		_mine_held = held
		_reset_mine()
	var holding := Input.is_mouse_button_pressed(MOUSE_BUTTON_LEFT)
	var cam := get_viewport().get_camera_3d()
	if not holding or cam == null:
		_reset_mine()
		return
	var from := cam.global_position
	var to := from - cam.global_transform.basis.z * 5.0
	var query := PhysicsRayQueryParameters3D.create(from, to, 1)
	# 花 / 草是没有碰撞的 Area3D(走过去不挡路),但准星要能选中 ——
	# 射线把 Area 也一起算进来,命中后按 gk_kind 走同一套挖掘流程。
	query.collide_with_areas = true
	var hit := cam.get_world_3d().direct_space_state.intersect_ray(query)
	if hit.is_empty():
		_reset_mine()
		return
	var body = hit.get("collider")
	if not (body is Node3D):
		_reset_mine()
		return
	# 地形方块带 gk_kind;现放的资产方块没有 meta,靠模型名 / 资产源认
	var aim := _aim_block(body, hit)
	if aim.is_empty():
		_reset_mine()
		return
	var kind := str(aim["kind"])
	# 创造:点一下即碎(0.12 秒冷却,按住扫过去不会一帧拆一排)
	if bool(host.get("创造模式")):
		if _mine_cooldown <= 0.0:
			_mine_cooldown = 0.12
			_break_aim(aim)
		return
	if body != _mine_target or int(aim["level"]) != _mine_level:
		_mine_target = body
		_mine_level = int(aim["level"])
		_mine_kind = kind
		_mine_elapsed = 0.0
	var info := _break_time(kind, _tool_state())
	var break_time := float(info["time"])
	# 硬度 0(花 / 草):点一下就碎,不进裂纹计时(0 会除出 NaN)
	if break_time <= 0.001:
		_reset_mine()
		_break_aim(aim)
		return
	_mine_elapsed += delta
	var stage := clampi(int(_mine_elapsed / break_time * float(CRACK_STAGES)),
		1, CRACK_STAGES)
	_show_crack(body, hit.get("position"), hit.get("normal"), stage)
	if _mine_elapsed >= break_time:
		# 要求工具但手里不满足:方块照样碎,但不发掉落物(原版徒手挖石)
		_reset_mine()
		_break_aim(aim, bool(info["drops"]) == false)


## 准星命中的是哪一格。
## 合批后的地形列:命中点沿法线回退半格 = 方块中心,中心 y 取整就是层号,
## 再去 gk_kinds 里拿类型 —— 和以前"每块一个节点、直接读 meta"是同一套结果。
## 放置方块 / 花草:节点自带 gk_kind(和以前一样)。
func _aim_block(body: Node3D, hit: Dictionary) -> Dictionary:
	if body.has_meta("gk_kinds"):
		var normal: Vector3 = hit.get("normal", Vector3.UP)
		var center: Vector3 = (hit.get("position") as Vector3) - normal * 0.5
		var cell: Vector2i = body.get_meta("gk_cell")
		var kinds: Array = body.get_meta("gk_kinds")
		var level := int(floor(center.y))
		if level < 0 or level >= kinds.size():
			return {}
		var kind := str(kinds[level])
		if kind.is_empty():
			return {}
		return {"body": body, "cell": cell, "level": level, "kind": kind}
	var kind := str(body.get_meta("gk_kind", ""))
	if kind.is_empty():
		kind = _placed_kind(body)
	if kind.is_empty() or BLOCK_HARDNESS.has(kind) == false:
		return {}
	return {"body": body, "cell": body.get_meta("gk_cell", Vector2i.ZERO),
		"level": int(body.get_meta("gk_level", -1)), "kind": kind}


func _break_aim(aim: Dictionary, no_drop := false) -> void:
	var body = aim["body"]
	if not is_instance_valid(body):
		return
	if body.has_meta("gk_kinds"):
		_remove_block(body, aim["cell"], int(aim["level"]),
			str(aim["kind"]), no_drop)
		return
	# 放置方块 / 花草:还是整节点收掉,tree_exiting 里记存档 + 发掉落
	if no_drop:
		(body as Node3D).set_meta("gk_no_drop", true)
	body.queue_free()


func _reset_mine() -> void:
	_mine_target = null
	_mine_level = -1
	_mine_kind = ""
	_mine_elapsed = 0.0
	_crack_stage = -1
	if _crack != null:
		_crack.visible = false


## 裂纹贴在"被打中的那一格"上:命中点往回退半格就是方块中心
func _show_crack(body: Node3D, point: Vector3, normal: Vector3,
		stage: int) -> void:
	if _crack == null:
		_crack = MeshInstance3D.new()
		_crack.name = "破坏裂纹"
		_crack.top_level = true
		_crack.visible = false
		var material := StandardMaterial3D.new()
		material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		material.cull_mode = BaseMaterial3D.CULL_DISABLED
		material.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
		_crack.material_override = material
		var mesh := BoxMesh.new()
		mesh.size = Vector3(1.006, 1.006, 1.006)
		_crack.mesh = mesh
		add_child(_crack)
	_crack.global_position = point - normal * 0.5
	_crack.visible = true
	if stage == _crack_stage:
		return
	_crack_stage = stage
	var path := "res://art/mc_crack_%d.png" % stage
	if ResourceLoader.exists(path):
		var material := _crack.material_override as StandardMaterial3D
		if material != null:
			material.albedo_texture = load(path)


func _region_of(cell: Vector2i) -> Vector2i:
	return Vector2i(int(floor(float(cell.x) / FAR_REGION)),
		int(floor(float(cell.y) / FAR_REGION)))


func _region_gap(region: Vector2i, cell: Vector2i) -> int:
	var dx := region.x * FAR_REGION + FAR_REGION / 2 - cell.x
	var dz := region.y * FAR_REGION + FAR_REGION / 2 - cell.y
	return dx * dx + dz * dz


func _region_in_range(region: Vector2i, cell: Vector2i,
		extra: float = 0.0) -> bool:
	var cx := float(region.x * FAR_REGION) + FAR_REGION * 0.5
	var cz := float(region.y * FAR_REGION) + FAR_REGION * 0.5
	return Vector2(cx - cell.x, cz - cell.y).length() \
		<= float(_radius) + FAR_REGION * 0.5 + extra


## 世界大小有限时:只铺边界里的地,外面是空的
func _in_world(cell: Vector2i) -> bool:
	if _world_size <= 0:
		return true
	return Vector2(cell.x + 0.5, cell.y + 0.5).length() <= float(_world_size)


## 换区块:重算哪几块近景(能挖能站)/ 哪几块远景(合并网格),
## 缺的按"离玩家近的先建"排队 —— 真正建在 _step_build 里按帧分摊。
func _replan(region: Vector2i) -> void:
	_near_regions = {}
	var near_cells: Array = []
	for rx in range(region.x - NEAR_REGIONS, region.x + NEAR_REGIONS + 1):
		for rz in range(region.y - NEAR_REGIONS, region.y + NEAR_REGIONS + 1):
			var near_region := Vector2i(rx, rz)
			_near_regions[near_region] = true
			for dx in range(FAR_REGION):
				for dz in range(FAR_REGION):
					var cell := Vector2i(rx * FAR_REGION + dx,
						rz * FAR_REGION + dz)
					if _in_world(cell):
						near_cells.append(cell)
	# 出生圈里只铺过碰撞的列,变成近景时把植被补回来(远景网格不再画这块)
	for bare in _bare_cells.keys():
		if _near_regions.has(_region_of(bare)) and _cells.has(bare):
			var bare_root: Node = _cells[bare]
			_add_column_decor(bare_root, bare)
			_bare_cells.erase(bare)
	# 已经不属于近景的列卸掉(它所在区块马上会并回远景)
	var demoted: Array = []
	for cell in _cells.keys():
		if _near_regions.has(_region_of(cell)):
			continue
		var node: Node = _cells[cell]
		if is_instance_valid(node):
			_retire(node)
			node.queue_free()
		_cells.erase(cell)
		_bare_cells.erase(cell)
		var old_region := _region_of(cell)
		if not demoted.has(old_region):
			demoted.append(old_region)
	# 降级成远景的区块:它的分段网格一起收掉(远景有自己的合并网格)
	for key in _sections.keys():
		if _near_regions.has(Vector2i(key.x, key.z)):
			continue
		var holder: Node = _sections[key]
		if is_instance_valid(holder):
			_retire(holder)
			holder.queue_free()
		_sections.erase(key)
	# 刚变成近景的区块:远景网格先别收,等新列铺完再收(见 _finish_handover)。
	# 以前这里立刻收掉,新区块的列又是按帧一点点铺的,走起来脚下会先空一块。
	for far_region in _far_regions.keys():
		if not _near_regions.has(far_region):
			continue
		var old: Node = _far_regions[far_region]
		_far_regions.erase(far_region)
		if is_instance_valid(old):
			_handover[far_region] = old
	# 远景:范围里、且不是近景的区块。已有的不动,只补缺的 / 收越界的。
	var wanted: Dictionary = {}
	var span := int(ceil((float(_radius) + FAR_REGION) / float(FAR_REGION)))
	for rx in range(region.x - span, region.x + span + 1):
		for rz in range(region.y - span, region.y + span + 1):
			var far_region := Vector2i(rx, rz)
			if _near_regions.has(far_region) \
					or not _region_in_range(far_region, _center):
				continue
			wanted[far_region] = true
	# 记住这一轮"该加载"的远景:迟滞区里多留的区块 = _far_regions 里不在
	# _wanted 的那些,探针用 unload_deferred_chunks() 读。
	_wanted = wanted
	# 卸载带迟滞(用户 2026-11-10):走出范围的远景不立刻拆,多留两个区块 ——
	# 在边界上来回走时才不会一边拆一边建,加载条也不至于反复闪。
	for far_region in _far_regions.keys():
		if wanted.has(far_region):
			continue
		if _region_in_range(far_region, _center, float(FAR_REGION) * 2.0):
			continue
		var old: Node = _far_regions[far_region]
		if is_instance_valid(old):
			_retire(old)
			old.queue_free()
		_far_regions.erase(far_region)
		_far_unloaded[far_region] = true
	# 刚被近景卸下来的区块:当场补一个合并网格,别等排队 —— 否则走起来
	# 会看到刚才还完整的一区块先空掉、再慢慢长回来。
	for demoted_region in demoted:
		if wanted.has(demoted_region) \
				and not _far_regions.has(demoted_region):
			_build_region(demoted_region)
	var missing: Array = []
	for far_region in wanted.keys():
		if not _far_regions.has(far_region):
			missing.append(far_region)
	missing.sort_custom(func(a, b):
		return _region_gap(a, _center) < _region_gap(b, _center))
	_region_queue = missing
	var near_missing: Array = []
	for cell in near_cells:
		if not _cells.has(cell):
			near_missing.append(cell)
	near_missing.sort_custom(func(a, b):
		return (a - _center).length_squared() < (b - _center).length_squared())
	_near_queue = near_missing
	# 分段网格:近景每个区块按 16 高拆成若干段,只排还没建的。
	# 只排"可能有余量"的段:段底已经高过地表上限的段直接记 null,不建空网格。
	var section_keys: Array = []
	for near_region in _near_regions.keys():
		for sec in range(int(ceil(float(WORLD_HEIGHT) / float(SECTION_HEIGHT)))):
			var key := Vector3i(near_region.x, sec, near_region.y)
			if _sections.has(key):
				continue
			section_keys.append(key)
	section_keys.sort_custom(func(a, b):
		var ar := Vector2i(a.x, a.z)
		var br := Vector2i(b.x, b.z)
		if ar == br:
			return int(a.y) < int(b.y)
		return _region_gap(ar, _center) < _region_gap(br, _center))
	_section_queue = section_keys
	_progress_total = maxi(1, _near_queue.size() + _region_queue.size() \
		+ _section_queue.size())


func _step_build() -> void:
	var paused := get_tree().paused
	var started := Time.get_ticks_usec()
	# 分区帧时统计用:上一帧铺地形花了多少微秒(0 = 这一帧没铺)
	_build_us_last = 0
	var budget := BUILD_BUDGET_MENU_US if paused else BUILD_BUDGET_US
	var near_left := NEAR_PER_FRAME_MENU if paused else NEAR_PER_FRAME
	# 这一帧铺过列 / 远景就不再合分段:两种重活分帧,不会叠出一帧十几毫秒。
	var did_terrain := false
	while near_left > 0 and not _near_queue.is_empty():
		var cell: Vector2i = _near_queue.pop_front()
		if not _cells.has(cell) and _near_regions.has(_region_of(cell)):
			_build_column(cell)
			did_terrain = true
		near_left -= 1
		if Time.get_ticks_usec() - started > budget:
			break
	var region_left := FAR_REGION_PER_FRAME_MENU if paused \
		else FAR_REGION_PER_FRAME
	while region_left > 0 and not _region_queue.is_empty():
		var far_region: Vector2i = _region_queue.pop_front()
		if not _near_regions.has(far_region):
			_build_region(far_region)
			did_terrain = true
		region_left -= 1
		if Time.get_ticks_usec() - started > budget * 2:
			break
	# 分段网格:只给"列已经铺完"的区块合网格,否则会先把空 section 建出来。
	# 每段花多少时间事先不知道,所以每建完一段再判预算 —— 游戏内预算 3ms,
	# 一段通常 3~5ms,实际就是一帧一段;空段 3µs 时能连着把预算吃完。
	if not did_terrain:
		while not _section_queue.is_empty():
			if Time.get_ticks_usec() - started > budget:
				break
			var key: Vector3i = _section_queue[0]
			var section_region := Vector2i(key.x, key.z)
			if not _near_regions.has(section_region):
				_section_queue.pop_front()
				continue
			if not _region_columns_ready(section_region):
				break
			_section_queue.pop_front()
			if not _sections.has(key):
				_build_section(key)
	_finish_handover()
	_build_us_last = Time.get_ticks_usec() - started


## 这个区块的 256 列碰撞体都铺好了没有(分段网格的建前提)。
func _region_columns_ready(region: Vector2i) -> bool:
	for dx in range(FAR_REGION):
		for dz in range(FAR_REGION):
			var cell := Vector2i(region.x * FAR_REGION + dx,
				region.y * FAR_REGION + dz)
			if _in_world(cell) and not _cells.has(cell):
				return false
	return true


## 这个区块的每一段都有结论了没有(建好网格或确认是空段)。
func _region_sections_done(region: Vector2i) -> bool:
	for sec in range(int(ceil(float(WORLD_HEIGHT) / float(SECTION_HEIGHT)))):
		if not _sections.has(Vector3i(region.x, sec, region.y)):
			return false
	return true


## 新近景区块的逐方块列都铺完了,才收掉过渡用的远景网格 —— 保证任何时刻
## 一块地要么有近景列、要么有远景网格,不会出现"先空一下再长出来"。
func _finish_handover() -> void:
	if _handover.is_empty():
		return
	for far_region in _handover.keys():
		var done := true
		for dx in range(FAR_REGION):
			for dz in range(FAR_REGION):
				var cell := Vector2i(far_region.x * FAR_REGION + dx,
					far_region.y * FAR_REGION + dz)
				if _in_world(cell) and not _cells.has(cell):
					done = false
					break
			if not done:
				break
		if not done:
			continue
		# 近景的分段网格也要全部建好(含"空段"的 null 标记),再收远景过渡网格,
		# 否则会出现 1~2 帧"远景已收、分段还没合出来"的空档。
		if not _region_sections_done(far_region):
			continue
		var node: Node = _handover[far_region]
		if is_instance_valid(node):
			_retire(node)
			node.queue_free()
		_handover.erase(far_region)


## 新建 / 读档后判断"整个可见范围铺好了没有":近景 + 远景 + 分段都建完才算好。
func is_ready() -> bool:
	if _center.x == (1 << 30) or _center.y == (1 << 30):
		return false
	return _near_queue.is_empty() and _region_queue.is_empty() \
		and _section_queue.is_empty() and _spawn_pad_queue.is_empty()


## 加载进度(0~1):给『正在生成世界…』的进度条用。
func load_progress() -> float:
	if is_ready():
		return 1.0
	if _progress_total <= 0:
		return 0.0
	var left := _near_queue.size() + _region_queue.size() \
		+ _section_queue.size()
	return clampf(1.0 - float(left) / float(_progress_total), 0.0, 1.0)


## 进世界之前:把出生点周围 (2*chunks+1)² 个区块铺好(地形 + 网格 + 碰撞)
## 再放开操作。以前是等整个渲染距离(约 4.3 秒)才进,而且进世界后近处还在边长;
## 现在只等这一片,更远的缺口全部留在雾起点之后,进世界后按帧补。
##
## M2a 第三段起改成"先 begin、再按帧 step、最后 finish"三步:
##   * 加载屏每帧读 spawn_build_progress() 画进度条,等待期间看得到进度;
##   * 每帧花的时间有上限(spawn_build_step 的预算),不会出现一帧卡几百毫秒。
func begin_spawn_build(chunks: int = 2) -> void:
	var host := get_parent()
	if not (host is Node3D):
		return
	var here: Vector3 = (host as Node3D).global_position
	_center = Vector2i(int(floor(here.x)), int(floor(here.z)))
	var region := _region_of(_center)
	_player_region = region
	_replan(region)
	# 5×5 里近景以外的那一圈:补碰撞列(地形 / 网格交给远景合并网格)。
	# 稳态碰撞按已定约束只跟玩家 ±1 区块走,这圈会在第一次跨区块时被 _replan
	# 收掉 —— 出生那一刻确实"5×5 地形 + 网格 + 碰撞都就绪"。
	_spawn_pad_queue = []
	for rx in range(region.x - chunks, region.x + chunks + 1):
		for rz in range(region.y - chunks, region.y + chunks + 1):
			var near_region := Vector2i(rx, rz)
			if _near_regions.has(near_region):
				continue
			for dx in range(FAR_REGION):
				for dz in range(FAR_REGION):
					var cell := Vector2i(near_region.x * FAR_REGION + dx,
						near_region.y * FAR_REGION + dz)
					if _in_world(cell) and not _cells.has(cell):
						_spawn_pad_queue.append(cell)
	# 只承诺铺到雾起点(至少铺满 5×5):再远的缺口永远在雾后。
	_spawn_far_limit = maxf(float(FAR_REGION) * float(chunks + 1),
		fog_begin_distance())
	_spawn_total = _near_queue.size() + _spawn_pad_queue.size() \
		+ _section_queue.size() + _spawn_far_pending()
	_spawn_active = true


## 该远景区块是否落在本次预铺的承诺距离内(圆 + 半个区块容差)。
func _region_within(region: Vector2i, limit: float) -> bool:
	var cx := float(region.x * FAR_REGION) + FAR_REGION * 0.5
	var cz := float(region.y * FAR_REGION) + FAR_REGION * 0.5
	return Vector2(cx - _center.x, cz - _center.y).length() \
		<= limit + FAR_REGION * 0.5


## 队列里还有多少个远景落在承诺距离内(队列按距离排序,数前缀即可)。
func _spawn_far_pending() -> int:
	var total := 0
	for far_region in _region_queue:
		if not _region_within(far_region, _spawn_far_limit):
			break
		total += 1
	return total


## 预铺一帧:按预算铺近景列 / 出生圈碰撞列 / 分段网格 / 雾前远景。
## 返回 true = 5×5 与雾前都铺完了,可以放开操作。
func spawn_build_step(budget_us: int = BUILD_BUDGET_MENU_US) -> bool:
	if not _spawn_active:
		return true
	var started := Time.get_ticks_usec()
	var near_left := NEAR_PER_FRAME_MENU
	var did_terrain := false
	while near_left > 0 and not _near_queue.is_empty():
		var cell: Vector2i = _near_queue.pop_front()
		if not _cells.has(cell) and _near_regions.has(_region_of(cell)):
			_build_column(cell)
			did_terrain = true
		near_left -= 1
		if Time.get_ticks_usec() - started > budget_us:
			break
	var pad_left := NEAR_PER_FRAME_MENU
	while pad_left > 0 and not _spawn_pad_queue.is_empty():
		var cell: Vector2i = _spawn_pad_queue.pop_front()
		if not _cells.has(cell):
			# 不做植被:远景合并网格已经画过树 / 草花,这里只要碰撞和 gk_kinds
			_build_column(cell, false)
			_bare_cells[cell] = true
			did_terrain = true
		pad_left -= 1
		if Time.get_ticks_usec() - started > budget_us:
			break
	var region_left := FAR_REGION_PER_FRAME_MENU
	while region_left > 0 and not _region_queue.is_empty():
		var far_region: Vector2i = _region_queue[0]
		if not _region_within(far_region, _spawn_far_limit):
			break
		_region_queue.pop_front()
		if not _near_regions.has(far_region):
			_build_region(far_region)
			did_terrain = true
		region_left -= 1
		if Time.get_ticks_usec() - started > budget_us * 2:
			break
	# 分段:列全部铺好的区块才能合网格,否则会先建出空段。
	# 和列 / 远景分帧,每建一段再看预算(加载期预算 12ms,通常能连做 2~3 段)。
	if not did_terrain:
		while not _section_queue.is_empty():
			if Time.get_ticks_usec() - started > budget_us:
				break
			var key: Vector3i = _section_queue[0]
			var section_region := Vector2i(key.x, key.z)
			if not _near_regions.has(section_region):
				_section_queue.pop_front()
				continue
			if not _region_columns_ready(section_region):
				break
			_section_queue.pop_front()
			if not _sections.has(key):
				_build_section(key)
	_finish_handover()
	_prebuild_us_last += Time.get_ticks_usec() - started
	return spawn_build_done()


func spawn_build_done() -> bool:
	if not _spawn_active:
		return true
	if not _near_queue.is_empty() or not _spawn_pad_queue.is_empty():
		return false
	if not _section_queue.is_empty():
		return false
	return _spawn_far_pending() == 0


func spawn_build_progress() -> float:
	if not _spawn_active or _spawn_total <= 0:
		return 1.0
	var left := _near_queue.size() + _spawn_pad_queue.size() \
		+ _section_queue.size() + _spawn_far_pending()
	return clampf(1.0 - float(left) / float(_spawn_total), 0.0, 1.0)


func finish_spawn_build() -> void:
	_spawn_active = false
	_spawn_pad_queue = []
	_progress_total = maxi(1, _near_queue.size() + _region_queue.size() \
		+ _section_queue.size())


## 兼容入口(没有加载屏的调用方):一次跑完,不刷进度。
func prebuild_spawn(chunks: int = 2) -> void:
	begin_spawn_build(chunks)
	var guard := 0
	while not spawn_build_step() and guard < 100000:
		guard += 1
	finish_spawn_build()


## 5×5 出生区就绪统计(探针用):每一格有没有碰撞列、每个区块有没有网格。
func spawn_coverage(chunks: int = 2) -> Dictionary:
	var cells_total := 0
	var cells_ready := 0
	var regions_total := 0
	var regions_meshed := 0
	var region := _player_region
	for rx in range(region.x - chunks, region.x + chunks + 1):
		for rz in range(region.y - chunks, region.y + chunks + 1):
			var near_region := Vector2i(rx, rz)
			var has_cells := false
			for dx in range(FAR_REGION):
				for dz in range(FAR_REGION):
					var cell := Vector2i(rx * FAR_REGION + dx,
						rz * FAR_REGION + dz)
					if not _in_world(cell):
						continue
					has_cells = true
					cells_total += 1
					if _cells.has(cell):
						cells_ready += 1
			if not has_cells:
				continue
			regions_total += 1
			if _near_regions.has(near_region):
				if _region_sections_done(near_region):
					regions_meshed += 1
			elif _far_regions.has(near_region) \
					and _far_regions[near_region] != null:
				regions_meshed += 1
	return {"cells_total": cells_total, "cells_ready": cells_ready,
		"regions": regions_total, "meshed": regions_meshed}


## 雾起点(和世界环境里的深度雾同一口径:0.6 × 渲染距离)。
func fog_begin_distance() -> float:
	return maxf(20.0, float(_radius) * 0.6)


## 还没铺好的远景离玩家最近有多远(断言"未完成区域只能出现在雾后")。
func incomplete_min_distance() -> float:
	var best := 1.0e9
	for far_region in _region_queue:
		if _near_regions.has(far_region):
			continue
		var cx := float(far_region.x * FAR_REGION) + FAR_REGION * 0.5
		var cz := float(far_region.y * FAR_REGION) + FAR_REGION * 0.5
		best = minf(best, Vector2(cx - _center.x, cz - _center.y).length())
	return best


## 卸载迟滞的对外指标:超过加载边界但还留着的远景区块数。
func unload_deferred_chunks() -> int:
	var count := 0
	for far_region in _far_regions.keys():
		if not _wanted.has(far_region) and _far_regions[far_region] != null:
			count += 1
	return count


## 某个远景区块现在是否还挂着(探针验证"走出迟滞区后释放")。
func far_loaded(region: Vector2i) -> bool:
	return _far_regions.has(region) and _far_regions[region] != null


## 远景重建次数(迟滞区内往返必须为 0)。
func chunk_rebuild_count() -> int:
	return _chunk_rebuild_count


## 近景一列。合批(第两百一十七轮):整列只用一个 StaticBody3D,
## 碰撞按"连续实心段"合并成一个盒子(通常是 1 个)。
## M2a 第二段:可视网格不再挂在列上,改由 16×16×SECTION_HEIGHT 的分段网格
## 合并绘制(见 _build_section);列里只留 gk_kinds 给挖掘判定和碰撞段。
## 为什么:近景 2304 列 × 平均 2.2 块 = 约 5000 个 MeshInstance3D,现在
## 每段每种方块只剩 1 个网格,节点数降一个数量级。
func _build_column(cell: Vector2i, decor := true) -> void:
	var height := _height_at(cell)
	var kind := _kind_at(cell, height)
	var root := Node3D.new()
	root.name = "区块_%d_%d" % [cell.x, cell.y]
	var body := StaticBody3D.new()
	body.name = "地形_%d_%d" % [cell.x, cell.y]
	body.collision_layer = 1
	body.collision_mask = 1
	# 列的原点放在 y=0:能站 / 能挖的盒子按"段"给出,方块中心 = level+0.5
	body.position = Vector3(cell.x + 0.5, 0.0, cell.y + 0.5)
	# 每一格还在不在 + 是什么方块("" = 被玩家挖掉);挖掘判定直接查这张表
	var kinds: Array = []
	for level in range(height):
		# 被玩家挖掉的方块不再长回来(存档里的 _removed)
		if _removed.has(_block_key(cell, level)):
			kinds.append("")
			continue
		var top := level == height - 1
		var block_kind := kind if top else "dirt"
		kinds.append(block_kind)
	body.set_meta("gk_cell", cell)
	body.set_meta("gk_kinds", kinds)
	body.tree_exiting.connect(_on_block_exiting.bind(body))
	_rebuild_column_shapes(body)
	root.add_child(body)
	# 植被:只在能站人的近景里长(远景合并网格不画草花,省 draw call);
	# 被挖掉过的格子不再长回来(和方块共用 _removed 记录)。
	if decor:
		_add_column_decor(root, cell)
	_world_node().add_child(root)
	_cells[cell] = root


## 给一列补植被(草花 + 树)。出生圈先只铺碰撞时跳过这一步(远景网格已经
## 画过树和草花,再长一份就是重面);等它变成近景时由 _replan 再补上。
func _add_column_decor(root: Node, cell: Vector2i) -> void:
	var height := _height_at(cell)
	var kind := _kind_at(cell, height)
	if kind != "grass":
		return
	var plant := _plant_at(cell, kind)
	if not plant.is_empty() \
			and not _removed.has(_block_key(cell, height)):
		root.add_child(_make_plant(cell, height, plant))
	if _has_tree(cell) and not _removed_trees.has(_cell_key(cell)):
		root.add_child(_make_tree(cell, height))


## 某一列当前还存在的方块中心(从低到高)。
## 合批后一列只有一个 StaticBody3D,外面没法再靠"数节点"逐块拿格子;
## 这里从 gk_kinds 还原,给测试探针 / 调试用,坐标和合批前完全一致。
func column_blocks(cell: Vector2i) -> Array:
	var out: Array = []
	var root: Node = _cells.get(cell)
	if root == null or not is_instance_valid(root):
		return out
	for child in root.get_children():
		if not (child is StaticBody3D):
			continue
		var kinds: Array = child.get_meta("gk_kinds", [])
		for level in range(kinds.size()):
			if str(kinds[level]).is_empty():
				continue
			out.append(Vector3(cell.x + 0.5, level + 0.5, cell.y + 0.5))
	return out


## ── 分段网格(M2a 第二段)────────────────────────────────────────
## section = 16×16 列 × SECTION_HEIGHT 高的立方块。每个 section 里按方块
## 类型各合并成一个网格;相邻方块之间的面直接剔除。面剔除查的是世界坐标
## 的邻居(跨 section 边界的邻居也算),所以接缝处不会漏面、也不会重面。
## 碰撞不在这里:仍是每列一个 StaticBody3D + 连续实心段(第两百一十七轮)。

## 一格是不是实心(地形没被挖掉 / 玩家放下的方块)。
## heights 是本次合成的高度缓存:段外一圈邻居也要查,避免重复算噪声。
## gone / extra 是"这一片里被挖过 / 被放过方块"的列集合:绝大多数列都不在
## 里面,直接跳过字符串 key 的拼接(实测每个 section 要查 ~3000 次邻居)。
func _solid_in(cell: Vector2i, level: int, heights: Dictionary,
		gone: Dictionary, extra: Dictionary) -> bool:
	if level < 0 or level >= WORLD_HEIGHT:
		return false
	if not _in_world(cell):
		return false
	if extra.has(cell) and _placed.has(_block_key(cell, level)):
		return true
	var height: int = int(heights.get(cell, -1))
	if height < 0:
		height = _height_at(cell)
		heights[cell] = height
	if level >= height:
		return false
	if gone.has(cell) and _removed.has(_block_key(cell, level)):
		return false
	return true


## 六个方向还能看见的面(位掩码):1 顶 / 2 底 / 4 北(z-)/ 8 南(z+)/
## 16 东(x+)/ 32 西(x-),和 _append_block 的检查顺序一一对应。
func _open_mask(cell: Vector2i, level: int, heights: Dictionary,
		gone: Dictionary, extra: Dictionary) -> int:
	var mask := 0
	if not _solid_in(cell, level + 1, heights, gone, extra):
		mask |= 1
	if not _solid_in(cell, level - 1, heights, gone, extra):
		mask |= 2
	if not _solid_in(cell + Vector2i(0, -1), level, heights, gone, extra):
		mask |= 4
	if not _solid_in(cell + Vector2i(0, 1), level, heights, gone, extra):
		mask |= 8
	if not _solid_in(cell + Vector2i(1, 0), level, heights, gone, extra):
		mask |= 16
	if not _solid_in(cell + Vector2i(-1, 0), level, heights, gone, extra):
		mask |= 32
	return mask


## 从 "x,z,level" 形式的改动记录里取出"哪几列被改过",供 _solid_in 快筛。
func _touched_cells(index: Dictionary) -> Dictionary:
	var cells: Dictionary = {}
	for key in index.keys():
		var parts := str(key).split(",")
		if parts.size() == 3:
			cells[Vector2i(int(parts[0]), int(parts[1]))] = true
	return cells


func _section_key_of(cell: Vector2i, level: int) -> Vector3i:
	var region := _region_of(cell)
	return Vector3i(region.x, int(level) / SECTION_HEIGHT, region.y)


## 挖 / 放之后只重建"这一格碰到"的 section:
## 方块自己 + 水平四邻 + 上下两邻所在的段。站在段边界上的方块最多碰 4 段,
## 通常只有 1~2 段,绝不会整块区块重来。
func _rebuild_sections_at(cell: Vector2i, level: int, reason: String) -> void:
	var keys: Dictionary = {}
	keys[_section_key_of(cell, level)] = true
	for offset in [Vector2i(1, 0), Vector2i(-1, 0),
			Vector2i(0, 1), Vector2i(0, -1)]:
		keys[_section_key_of(cell + offset, level)] = true
	if level > 0:
		keys[_section_key_of(cell, level - 1)] = true
	if level < WORLD_HEIGHT - 1:
		keys[_section_key_of(cell, level + 1)] = true
	for key in keys.keys():
		if not _near_regions.has(Vector2i(key.x, key.z)):
			continue
		_build_section(key, reason)


## 整个区块的 section 全部重建只在换世界这类罕见路径用。
func _rebuild_region_sections(region: Vector2i, reason: String) -> void:
	if not _near_regions.has(region):
		return
	for sec in range(int(ceil(float(WORLD_HEIGHT) / float(SECTION_HEIGHT)))):
		_build_section(Vector3i(region.x, sec, region.y), reason)


func _record_section_build(key: Vector3i, reason: String, started: int) -> void:
	var spent := Time.get_ticks_usec() - started
	_section_build_count += 1
	_section_build_total_us += spent
	_section_build_last_us = spent
	_section_build_max_us = maxi(_section_build_max_us, spent)
	_section_rebuild_log.append({"key": key, "us": spent, "reason": reason})
	while _section_rebuild_log.size() > 96:
		_section_rebuild_log.pop_front()
	_section_us_samples.append(spent)
	while _section_us_samples.size() > 4096:
		_section_us_samples.pop_front()


## 收口测量(M2a 第四段):section 合成耗时分布 + 碰撞体 / 网格规模。
func metrics() -> Dictionary:
	var samples := _section_us_samples.duplicate()
	samples.sort()
	var count := samples.size()
	var p50 := 0.0
	var p99 := 0.0
	if count > 0:
		p50 = float(samples[clampi(int(round(float(count - 1) * 0.5)), 0,
			count - 1)]) / 1000.0
		p99 = float(samples[clampi(int(round(float(count - 1) * 0.99)), 0,
			count - 1)]) / 1000.0
	var bodies := 0
	var shapes := 0
	for cell in _cells.keys():
		var node: Node = _cells[cell]
		if not is_instance_valid(node):
			continue
		bodies += 1
		# 碰撞段挂在列根下面的 StaticBody3D 里(和 column_shapes 同一结构)
		for child in node.get_children():
			if child is StaticBody3D:
				for item in child.get_children():
					if item is CollisionShape3D:
						shapes += 1
	var far_meshes := 0
	for far_region in _far_regions.keys():
		if is_instance_valid(_far_regions[far_region]):
			far_meshes += 1
	return {
		"section_builds": count,
		"build_p50_ms": p50,
		"build_p99_ms": p99,
		"build_max_ms": float(_section_build_max_us) / 1000.0,
		"cells": _cells.size(),
		"bodies": bodies,
		"shapes": shapes,
		"sections": _sections.size(),
		"far_regions": far_meshes,
		"chunk_rebuild_count": _chunk_rebuild_count,
		"deferred": unload_deferred_chunks(),
	}


## 合成一个 section 的可视网格。只给"这一段里真的有方块"的类型建网格 ——
## 空类型不建、整段没方块就连节点都不建(_sections[key] = null 记住)。
func _build_section(key: Vector3i, reason := "load") -> void:
	var started := Time.get_ticks_usec()
	var region := Vector2i(key.x, key.z)
	var sec := int(key.y)
	if sec < 0 or sec * SECTION_HEIGHT >= WORLD_HEIGHT:
		push_warning("忽略非法 section: %s" % [key])
		return
	var base_y := sec * SECTION_HEIGHT
	var old: Node = _sections.get(key)
	if is_instance_valid(old):
		# 立即释放:下一行就要用同一个名字新建节点,延迟释放会让新节点被
		# Godot 改名成 @Node3D@xxx,探针按 "分段_*" 就数不到了。
		_retire(old)
		old.free()
	_sections.erase(key)
	# 段底已经高过地表上限:不可能有地形方块(放置方块由自己的节点画),
	# 直接记 null,别每次排队回来重复扫。
	if base_y >= _terrain_ceiling:
		_sections[key] = null
		_record_section_build(key, reason, started)
		return
	# 高度缓存:16×16 本体 + 外圈一格(面剔除要查段外邻居)
	var heights: Dictionary = {}
	for dx in range(-1, FAR_REGION + 1):
		for dz in range(-1, FAR_REGION + 1):
			var cell := Vector2i(region.x * FAR_REGION + dx,
				region.y * FAR_REGION + dz)
			if not heights.has(cell):
				heights[cell] = _height_at(cell)
	var holder := Node3D.new()
	holder.name = "分段_%d_%d_%d" % [region.x, sec, region.y]
	holder.position = Vector3(region.x * FAR_REGION, base_y,
		region.y * FAR_REGION)
	var gone := _touched_cells(_removed)
	var extra := _touched_cells(_placed)
	# 柔和光影(M2b 画质项):先把本段 + 一格边界的是否实心算成一张表,
	# 面剔除和逐顶点 AO 都读这张表 —— 比每个面再查一次字典便宜,也避免
	# 加 AO 后单段合成时间变长。
	var solid := _section_solid_grid(region, base_y, heights, gone, extra)
	var tools: Dictionary = {}
	var order: Array = []
	for dx in range(FAR_REGION):
		for dz in range(FAR_REGION):
			var cell := Vector2i(region.x * FAR_REGION + dx,
				region.y * FAR_REGION + dz)
			if not _in_world(cell):
				continue
			var height: int = heights[cell]
			var top_kind := ""
			for level in range(base_y, mini(base_y + SECTION_HEIGHT, height)):
				if gone.has(cell) and _removed.has(_block_key(cell, level)):
					continue
				var block_kind := "dirt"
				if level == height - 1:
					if top_kind.is_empty():
						top_kind = _kind_at(cell, height)
					block_kind = top_kind
				var gx := dx + 1
				var gy := level - base_y + 1
				var gz := dz + 1
				var mask := _mask_from_grid(solid, gx, gy, gz)
				if mask == 0:
					continue
				var ao: Dictionary = {}
				if _soft_light:
					ao = _block_ao(solid, gx, gy, gz, mask)
				var st: SurfaceTool = tools.get(block_kind)
				if st == null:
					st = SurfaceTool.new()
					st.begin(Mesh.PRIMITIVE_TRIANGLES)
					tools[block_kind] = st
					order.append(block_kind)
				_append_block(st, block_kind,
					Vector3(dx + 0.5, level - base_y + 0.5, dz + 0.5),
					mask, ao)
	for block_kind in order:
		var st: SurfaceTool = tools[block_kind]
		var mesh := st.commit()
		if mesh == null or mesh.get_surface_count() == 0:
			continue
		var visual := MeshInstance3D.new()
		visual.name = "段块_" + str(block_kind)
		visual.mesh = mesh
		visual.material_override = _materials.get(block_kind)
		holder.add_child(visual)
	# 静态水(M2b):海平面所在的那一段(sec = SEA_LEVEL / SECTION_HEIGHT)
	# 补一张水面 —— 只画 height < SEA_LEVEL 的格子顶面,不画侧壁,
	# 岸边就是地形自己的侧壁,不会出现垂直水墙。
	if sec * SECTION_HEIGHT == SEA_LEVEL:
		var water := _build_water_surface(region)
		if water != null:
			holder.add_child(water)
	if holder.get_child_count() == 0:
		holder.free()
		_sections[key] = null
	else:
		_world_node().add_child(holder)
		_sections[key] = holder
	_record_section_build(key, reason, started)


## 本段 18×18×18 的实心表(含四周 / 上下各一格边界)。
## 下标:x + 18 * (z + 18 * y),x / z / y 都是 0~17。
func _section_solid_grid(region: Vector2i, base_y: int, heights: Dictionary,
		gone: Dictionary, extra: Dictionary) -> PackedByteArray:
	var span := FAR_REGION + 2
	var grid := PackedByteArray()
	grid.resize(span * span * span)
	for dy in range(-1, SECTION_HEIGHT + 1):
		for dz in range(-1, FAR_REGION + 1):
			for dx in range(-1, FAR_REGION + 1):
				var cell := Vector2i(region.x * FAR_REGION + dx,
					region.y * FAR_REGION + dz)
				var level := base_y + dy
				var filled := 0
				if level >= 0 and level < WORLD_HEIGHT \
						and _solid_in(cell, level, heights, gone, extra):
					filled = 1
				grid[(dx + 1) + span * ((dz + 1) + span * (dy + 1))] = filled
	return grid


func _grid_solid(grid: PackedByteArray, x: int, y: int, z: int) -> bool:
	var span := FAR_REGION + 2
	if x < 0 or x >= span or y < 0 or y >= span or z < 0 or z >= span:
		return false
	return grid[x + span * (z + span * y)] != 0


## 和 _open_mask 同一套位:1 顶 / 2 底 / 4 北 / 8 南 / 16 东 / 32 西
func _mask_from_grid(grid: PackedByteArray, x: int, y: int, z: int) -> int:
	var mask := 0
	if not _grid_solid(grid, x, y + 1, z):
		mask |= 1
	if not _grid_solid(grid, x, y - 1, z):
		mask |= 2
	if not _grid_solid(grid, x, y, z - 1):
		mask |= 4
	if not _grid_solid(grid, x, y, z + 1):
		mask |= 8
	if not _grid_solid(grid, x + 1, y, z):
		mask |= 16
	if not _grid_solid(grid, x - 1, y, z):
		mask |= 32
	return mask


## 环境光遮蔽(M2b 画质项):每个顶点看"面外那一层"的侧邻 + 角落,
## 0 级最暗(两边都挡死)、3 级不遮。系数别拉太狠,保持现在的像素观感。
const AO_SHADE := [0.56, 0.71, 0.86, 1.0]


func _ao_at(grid: PackedByteArray, x: int, y: int, z: int,
		ax: int, ay: int, az: int, bx: int, by: int, bz: int) -> float:
	var s1 := 1 if _grid_solid(grid, x + ax, y + ay, z + az) else 0
	var s2 := 1 if _grid_solid(grid, x + bx, y + by, z + bz) else 0
	var corner := 1 if _grid_solid(grid,
		x + ax + bx, y + ay + by, z + az + bz) else 0
	var level := 0
	if not (s1 == 1 and s2 == 1):
		level = 3 - s1 - s2 - corner
	return float(AO_SHADE[clampi(level, 0, 3)])


## 一张面 4 个顶点的 AO 系数,顺序和 _append_block 里 _quad 的顶点顺序一致。
func _face_ao(grid: PackedByteArray, x: int, y: int, z: int,
		bit: int) -> Array:
	var out := [1.0, 1.0, 1.0, 1.0]
	match bit:
		1:  # 顶面:角点 (-x,-z) (-x,+z) (+x,+z) (+x,-z)
			out[0] = _ao_at(grid, x, y + 1, z, -1, 0, 0, 0, 0, -1)
			out[1] = _ao_at(grid, x, y + 1, z, -1, 0, 0, 0, 0, 1)
			out[2] = _ao_at(grid, x, y + 1, z, 1, 0, 0, 0, 0, 1)
			out[3] = _ao_at(grid, x, y + 1, z, 1, 0, 0, 0, 0, -1)
		2:  # 底面:角点 (-x,+z) (+x,+z) (+x,-z) (-x,-z)
			out[0] = _ao_at(grid, x, y - 1, z, -1, 0, 0, 0, 0, 1)
			out[1] = _ao_at(grid, x, y - 1, z, 1, 0, 0, 0, 0, 1)
			out[2] = _ao_at(grid, x, y - 1, z, 1, 0, 0, 0, 0, -1)
			out[3] = _ao_at(grid, x, y - 1, z, -1, 0, 0, 0, 0, -1)
		8:  # 南面:角点 (-x,-y) (-x,+y) (+x,+y) (+x,-y)
			out[0] = _ao_at(grid, x, y, z + 1, -1, 0, 0, 0, -1, 0)
			out[1] = _ao_at(grid, x, y, z + 1, -1, 0, 0, 0, 1, 0)
			out[2] = _ao_at(grid, x, y, z + 1, 1, 0, 0, 0, 1, 0)
			out[3] = _ao_at(grid, x, y, z + 1, 1, 0, 0, 0, -1, 0)
		4:  # 北面:角点 (+x,-y) (+x,+y) (-x,+y) (-x,-y)
			out[0] = _ao_at(grid, x, y, z - 1, 1, 0, 0, 0, -1, 0)
			out[1] = _ao_at(grid, x, y, z - 1, 1, 0, 0, 0, 1, 0)
			out[2] = _ao_at(grid, x, y, z - 1, -1, 0, 0, 0, 1, 0)
			out[3] = _ao_at(grid, x, y, z - 1, -1, 0, 0, 0, -1, 0)
		16:  # 东面:角点 (+z,-y) (+z,+y) (-z,+y) (-z,-y)
			out[0] = _ao_at(grid, x + 1, y, z, 0, 0, 1, 0, -1, 0)
			out[1] = _ao_at(grid, x + 1, y, z, 0, 0, 1, 0, 1, 0)
			out[2] = _ao_at(grid, x + 1, y, z, 0, 0, -1, 0, 1, 0)
			out[3] = _ao_at(grid, x + 1, y, z, 0, 0, -1, 0, -1, 0)
		32:  # 西面:角点 (-z,-y) (-z,+y) (+z,+y) (+z,-y)
			out[0] = _ao_at(grid, x - 1, y, z, 0, 0, -1, 0, -1, 0)
			out[1] = _ao_at(grid, x - 1, y, z, 0, 0, -1, 0, 1, 0)
			out[2] = _ao_at(grid, x - 1, y, z, 0, 0, 1, 0, 1, 0)
			out[3] = _ao_at(grid, x - 1, y, z, 0, 0, 1, 0, -1, 0)
	return out


func _block_ao(grid: PackedByteArray, x: int, y: int, z: int,
		mask: int) -> Dictionary:
	var ao: Dictionary = {}
	for bit in [1, 2, 4, 8, 16, 32]:
		if mask & bit:
			ao[bit] = _face_ao(grid, x, y, z, bit)
	return ao


## 画质项:柔和光影(面亮度差 + 角落 AO)开关。关掉只影响近景分段,
## 远景本来就是整块合并的纯色盒;切换时重建一次近景分段(在暂停菜单里)。
func set_soft_light(enabled: bool) -> void:
	if enabled == _soft_light:
		return
	_soft_light = enabled
	for key in _sections.keys():
		if _sections[key] != null:
			_build_section(key, "soft_light")


func soft_light_enabled() -> bool:
	return _soft_light


## 一个区块的水面网格(局部坐标,段持有者的原点在 y=SEA_LEVEL)。
## 只给 height < SEA_LEVEL 的格子铺顶面:水是"一整片平面",没有侧壁,
## 岸边自然过渡;返回 null = 这块区域没有水。
func _build_water_surface(region: Vector2i) -> MeshInstance3D:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var quads := 0
	for dx in range(FAR_REGION):
		for dz in range(FAR_REGION):
			var cell := Vector2i(region.x * FAR_REGION + dx,
				region.y * FAR_REGION + dz)
			if not _in_world(cell) or _height_at(cell) >= SEA_LEVEL:
				continue
			var x := float(dx) + 0.5
			var z := float(dz) + 0.5
			_quad(st, Vector3(x - 0.5, 0.0, z - 0.5),
				Vector3(x - 0.5, 0.0, z + 0.5),
				Vector3(x + 0.5, 0.0, z + 0.5),
				Vector3(x + 0.5, 0.0, z - 0.5),
				Vector2(0, 0), Vector2(0, 1), Vector2(1, 1), Vector2(1, 0), 1.0)
			quads += 1
	if quads == 0:
		return null
	var visual := MeshInstance3D.new()
	visual.name = "段水"
	visual.mesh = st.commit()
	visual.material_override = _water_material()
	return visual


## 静态水对外的只读出口(游泳 / 天空脚本和探针用)。
func water_surface_y() -> int:
	return SEA_LEVEL


## 某个世界坐标点是不是在水里:低于水面、且这一列地表在海平面以下
## (在陆地里 / 山腹里不算水)。
func is_underwater_point(point: Vector3) -> bool:
	if point.y >= float(SEA_LEVEL):
		return false
	var cell := Vector2i(int(floor(point.x)), int(floor(point.z)))
	if not _in_world(cell):
		return false
	return _height_at(cell) < SEA_LEVEL


## 读档迁移用:这一列不会把人卡进方块里的脚底高度(水下抬到水面)。
func safe_ground_y(cell: Vector2i) -> float:
	return float(maxi(_height_at(cell), SEA_LEVEL))


## 分段网格 / 重建耗时的对外快照(探针用)。
func section_stats() -> Dictionary:
	var meshes := 0
	var holders := 0
	for key in _sections.keys():
		var holder: Node = _sections[key]
		if is_instance_valid(holder):
			holders += 1
			meshes += holder.get_child_count()
	return {
		"sections": holders,
		"known": _sections.size(),
		"meshes": meshes,
		"builds": _section_build_count,
		"last_us": _section_build_last_us,
		"max_us": _section_build_max_us,
		"total_us": _section_build_total_us,
		"queue": _section_queue.size(),
	}


func section_rebuild_log() -> Array:
	return _section_rebuild_log.duplicate(true)


## 一列当前的碰撞段(给探针验证"挖掉中间一格拆成上下两段")。
func column_shapes(cell: Vector2i) -> Array:
	var out: Array = []
	var root: Node = _cells.get(cell)
	if root == null or not is_instance_valid(root):
		return out
	for child in root.get_children():
		if not (child is StaticBody3D):
			continue
		for item in child.get_children():
			if not (item is CollisionShape3D):
				continue
			var shape := (item as CollisionShape3D).shape
			if not (shape is BoxShape3D):
				continue
			var size := (shape as BoxShape3D).size
			var center := (item as Node3D).position.y
			out.append({"start": center - size.y * 0.5,
				"end": center + size.y * 0.5, "size": size.y})
	return out


## 露出一格是不是实心(审计用:不依赖合成时的缓存,重新算一遍)。
func _solid_at(cell: Vector2i, level: int) -> bool:
	if level < 0 or level >= WORLD_HEIGHT:
		return false
	if not _in_world(cell):
		return false
	var key := _block_key(cell, level)
	if _placed.has(key):
		return not str(_placed[key]).is_empty()
	if level >= _height_at(cell):
		return false
	return not _removed.has(key)


func _expected_faces(key: Vector3i) -> Dictionary:
	var out: Dictionary = {}
	var region := Vector2i(key.x, key.z)
	var base_y := int(key.y) * SECTION_HEIGHT
	for dx in range(FAR_REGION):
		for dz in range(FAR_REGION):
			var cell := Vector2i(region.x * FAR_REGION + dx,
				region.y * FAR_REGION + dz)
			if not _in_world(cell):
				continue
			for level in range(base_y, mini(base_y + SECTION_HEIGHT,
					WORLD_HEIGHT)):
				if not _solid_at(cell, level):
					continue
				if not _solid_at(cell, level + 1):
					out["%d,%d,%d,0" % [cell.x, level, cell.y]] = true
				if not _solid_at(cell, level - 1):
					out["%d,%d,%d,1" % [cell.x, level, cell.y]] = true
				if not _solid_at(cell + Vector2i(0, 1), level):
					out["%d,%d,%d,2" % [cell.x, level, cell.y]] = true
				if not _solid_at(cell + Vector2i(0, -1), level):
					out["%d,%d,%d,3" % [cell.x, level, cell.y]] = true
				if not _solid_at(cell + Vector2i(1, 0), level):
					out["%d,%d,%d,4" % [cell.x, level, cell.y]] = true
				if not _solid_at(cell + Vector2i(-1, 0), level):
					out["%d,%d,%d,5" % [cell.x, level, cell.y]] = true
	return out


func _dir_code(axis: int, side: int) -> int:
	if axis == 1:
		return 0 if side > 0 else 1
	if axis == 2:
		return 2 if side > 0 else 3
	return 4 if side > 0 else 5


## 从网格三角形反解"这是哪一格的哪个面"。
## 面的平面坐标 = 方块中心 ± 0.5;两个候选里只有"方块实心、外侧是空气"
## 的那个成立,所以不需要相信三角形绕序(材质本来就是双面)。
func _triangle_face_key(a: Vector3, b: Vector3, c: Vector3,
		origin: Vector3) -> String:
	var axis := -1
	for ax in range(3):
		if absf(a[ax] - b[ax]) < 0.001 and absf(a[ax] - c[ax]) < 0.001:
			axis = ax
			break
	if axis < 0:
		return ""
	var plane := origin[axis] + a[axis]
	var min0 := minf(a[0], minf(b[0], c[0])) + origin[0]
	var min1 := minf(a[1], minf(b[1], c[1])) + origin[1]
	var min2 := minf(a[2], minf(b[2], c[2])) + origin[2]
	var cell := Vector2i.ZERO
	var level := 0
	if axis == 0:
		cell = Vector2i(int(floor(plane + 0.001)), int(floor(min2 + 0.001)))
		level = int(floor(min1 + 0.001))
	elif axis == 1:
		cell = Vector2i(int(floor(min0 + 0.001)), int(floor(min2 + 0.001)))
		level = int(floor(plane + 0.001))
	else:
		cell = Vector2i(int(floor(min0 + 0.001)), int(floor(plane + 0.001)))
		level = int(floor(min1 + 0.001))
	for side in [1, -1]:
		var index := int(round(plane - 0.5 - 0.5 * float(side)))
		var cand_cell := cell
		var cand_level := level
		if axis == 0:
			cand_cell = Vector2i(index, cell.y)
		elif axis == 1:
			cand_level = index
		else:
			cand_cell = Vector2i(cell.x, index)
		if not _solid_at(cand_cell, cand_level):
			continue
		var outside := false
		if axis == 0:
			outside = not _solid_at(cand_cell + Vector2i(side, 0), cand_level)
		elif axis == 1:
			outside = not _solid_at(cand_cell, cand_level + side)
		else:
			outside = not _solid_at(cand_cell + Vector2i(0, side), cand_level)
		if outside:
			return "%d,%d,%d,%d" % [cand_cell.x, cand_level, cand_cell.y,
				_dir_code(axis, side)]
	return ""


func _is_seam_key(fk: String) -> bool:
	var parts := fk.split(",")
	if parts.size() != 4:
		return false
	var cell := Vector3i(int(parts[0]), int(parts[1]), int(parts[2]))
	var axis := 1
	var side := 1
	match int(parts[3]):
		0:
			axis = 1
			side = 1
		1:
			axis = 1
			side = -1
		2:
			axis = 2
			side = 1
		3:
			axis = 2
			side = -1
		4:
			axis = 0
			side = 1
		5:
			axis = 0
			side = -1
	var plane := 0
	if axis == 0:
		plane = cell.x + (1 if side > 0 else 0)
	elif axis == 1:
		plane = cell.y + (1 if side > 0 else 0)
	else:
		plane = cell.z + (1 if side > 0 else 0)
	var step := SECTION_HEIGHT if axis == 1 else FAR_REGION
	return plane % step == 0


## 接缝审计:把已建 section 的网格三角形反解成"哪一格的哪个面",和独立
## 算出的"该露哪些面"逐个对账。missing = 漏面,extra = 多面 / 重面,
## duplicated = 同一个面被画了 3 次以上,seam_* 只看落在 section 边界上的面。
func section_audit() -> Dictionary:
	var built := 0
	var expected_total := 0
	var emitted_total := 0
	var missing := 0
	var extra := 0
	var duplicated := 0
	var malformed := 0
	var seam_missing := 0
	var seam_extra := 0
	for key in _sections.keys():
		var holder: Node3D = _sections[key]
		if not is_instance_valid(holder):
			continue
		built += 1
		var expected := _expected_faces(key)
		var counts: Dictionary = {}
		for child in holder.get_children():
			if not (child is MeshInstance3D):
				continue
			# 水面(M2b)不是方块面,不参与接缝审计
			if String(child.name) == "段水":
				continue
			var mesh := (child as MeshInstance3D).mesh
			if mesh == null:
				continue
			for surf in range(mesh.get_surface_count()):
				var arrays := mesh.surface_get_arrays(surf)
				if arrays.size() == 0:
					continue
				var verts: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
				var index = arrays[Mesh.ARRAY_INDEX]
				if index != null and (index as PackedInt32Array).size() >= 3:
					var ids: PackedInt32Array = index
					var t := 0
					while t + 2 < ids.size():
						var fk := _triangle_face_key(verts[ids[t]],
							verts[ids[t + 1]], verts[ids[t + 2]],
							holder.position)
						if fk.is_empty():
							malformed += 1
						else:
							counts[fk] = int(counts.get(fk, 0)) + 1
						t += 3
				else:
					var t2 := 0
					while t2 + 2 < verts.size():
						var fk2 := _triangle_face_key(verts[t2],
							verts[t2 + 1], verts[t2 + 2], holder.position)
						if fk2.is_empty():
							malformed += 1
						else:
							counts[fk2] = int(counts.get(fk2, 0)) + 1
						t2 += 3
		expected_total += expected.size()
		emitted_total += counts.size()
		for fk in counts.keys():
			if int(counts[fk]) > 2:
				duplicated += 1
			elif int(counts[fk]) < 2:
				malformed += 1
		for fk in expected.keys():
			if not counts.has(fk):
				missing += 1
				if _is_seam_key(str(fk)):
					seam_missing += 1
		for fk in counts.keys():
			if not expected.has(fk):
				extra += 1
				if _is_seam_key(str(fk)):
					seam_extra += 1
	return {
		"sections": built,
		"expected": expected_total,
		"emitted": emitted_total,
		"missing": missing,
		"extra": extra,
		"duplicated": duplicated,
		"malformed": malformed,
		"seam_missing": seam_missing,
		"seam_extra": seam_extra,
	}


## 远景:一个区块(16x16 列)的同类方块用一个 MultiMesh 画 —— 只画,不参与
## 射线 / 站立。比"一列一个节点"少两个数量级的节点,渲染距离才敢往大里开;
## 建的时候只写变换,不逐块复制顶点,加载才快得起来。
func _build_region(region: Vector2i) -> void:
	var old: Node = _far_regions.get(region)
	if old != null and is_instance_valid(old):
		_retire(old)
		old.queue_free()
	var spots: Dictionary = {}
	var leaves: Array = []
	var water: Array = []
	var base_x := region.x * FAR_REGION
	var base_z := region.y * FAR_REGION
	var covered := 0
	var gone := _touched_cells(_removed)
	for dx in range(FAR_REGION):
		for dz in range(FAR_REGION):
			var cell := Vector2i(base_x + dx, base_z + dz)
			if not _in_world(cell):
				continue
			covered += 1
			var height := _height_at(cell)
			var kind := _kind_at(cell, height)
			# 水面(M2b):远景也铺一张半透明面片,否则走远了水面会断掉
			if height < SEA_LEVEL:
				water.append(Transform3D(Basis(),
					Vector3(cell.x + 0.5, float(SEA_LEVEL), cell.y + 0.5)))
			# 远景(M2b):地形抬到 60 层后不能再"每块一个实例"(实例数会翻十几倍)。
			# 改成一列两个盒子:一个拉伸到整列高度的泥土盒子 + 顶上一块表层方块。
			# 被挖过的列很少,退回逐块路径,保证挖出来的洞在远景里也还是洞。
			var body_h := height - 1
			var top_gone := gone.has(cell) \
				and _removed.has(_block_key(cell, height - 1))
			if body_h > 0:
				if gone.has(cell):
					for level in range(body_h):
						if _removed.has(_block_key(cell, level)):
							continue
						_spot_of(spots, "dirt").append(Transform3D(Basis(),
							Vector3(cell.x + 0.5, level + 0.5, cell.y + 0.5)))
				else:
					_spot_of(spots, "dirt").append(Transform3D(
						Basis().scaled(Vector3(1.0, float(body_h), 1.0)),
						Vector3(cell.x + 0.5, float(body_h) * 0.5,
							cell.y + 0.5)))
			if not top_gone:
				_spot_of(spots, kind).append(Transform3D(Basis(),
					Vector3(cell.x + 0.5, height - 0.5, cell.y + 0.5)))
			if kind == "grass" and _has_tree(cell) \
					and not _removed_trees.has(_cell_key(cell)):
				for level in range(4):
					_spot_of(spots, "wood").append(Transform3D(Basis(),
						Vector3(cell.x + 0.5, height + level + 0.5,
							cell.y + 0.5)))
				leaves.append(Transform3D(Basis(),
					Vector3(cell.x + 0.5, float(height), cell.y + 0.5)))
	if covered == 0:
		# 世界边界外:记一个空标记,别每次都重新排队
		_far_regions[region] = null
		return
	# 重建计数:这个区块之前被卸掉过,现在又建回来 —— 才 +1。
	# 近景降级成远景(补合并网格)不算:它没被卸载过。
	if _far_unloaded.has(region):
		_chunk_rebuild_count += 1
		_far_unloaded.erase(region)
	var holder := Node3D.new()
	holder.name = "远景区块_%d_%d" % [region.x, region.y]
	for block_kind in spots.keys():
		holder.add_child(_far_multimesh(_visual(block_kind),
			_materials[block_kind], spots[block_kind], "远景网格"))
	if not leaves.is_empty():
		holder.add_child(_far_multimesh(_build_leaves(),
			_materials["leaves"], leaves, "远景树叶"))
	if not water.is_empty():
		holder.add_child(_far_multimesh(_water_quad(),
			_water_material(), water, "远景水面"))
	_world_node().add_child(holder)
	_far_regions[region] = holder


func _spot_of(spots: Dictionary, kind: String) -> Array:
	if spots.has(kind):
		return spots[kind]
	var list: Array = []
	spots[kind] = list
	return list


## 一批同款方块(只画不碰)合成一个 MultiMesh:几千个方块一次 draw call。
## spots 里可以是 Transform3D(带缩放,远景整列盒子)或 Vector3(纯平移)。
func _far_multimesh(mesh: Mesh, material: Material, spots: Array,
		node_name: String) -> MultiMeshInstance3D:
	var multi := MultiMesh.new()
	multi.transform_format = MultiMesh.TRANSFORM_3D
	multi.mesh = mesh
	multi.instance_count = spots.size()
	for i in range(spots.size()):
		var item = spots[i]
		var xform: Transform3D = item if item is Transform3D \
			else Transform3D(Basis(), item)
		multi.set_instance_transform(i, xform)
	var visual := MultiMeshInstance3D.new()
	visual.name = node_name
	visual.multimesh = multi
	visual.material_override = material
	return visual


## 读档:把存档里"放下的方块"全部重建成独立节点(和节点图放下的那一批一样,
## 挂在场景根上、按渲染距离显隐),不再绑死在某一列上 —— 否则玩家走远一点,
## 那一列降级成远景,方块就跟着消失了。
func _rebuild_placed() -> void:
	var world := _world_node()
	if world == null or _placed.is_empty():
		return
	for key in _placed.keys():
		var parts := str(key).split(",")
		if parts.size() != 3:
			continue
		var cell := Vector2i(int(parts[0]), int(parts[1]))
		world.add_child(_make_block(cell, int(parts[2]),
			str(_placed[key]), false, true))


func _make_block(cell: Vector2i, level: int, kind: String, top: bool,
		placed := false) -> StaticBody3D:
	var body := StaticBody3D.new()
	if top and not placed:
		body.name = "地形_%d_%d" % [cell.x, cell.y]
	else:
		body.name = "地形_%d_%d_%d" % [cell.x, cell.y, level]
	body.collision_layer = 1
	body.collision_mask = 1
	var shape := CollisionShape3D.new()
	var box := BoxShape3D.new()
	box.size = Vector3(BLOCK, BLOCK, BLOCK)
	shape.shape = box
	body.add_child(shape)
	var visual := MeshInstance3D.new()
	visual.mesh = _visual(kind)
	visual.material_override = _materials[kind]
	body.add_child(visual)
	body.position = Vector3(cell.x + 0.5, level + 0.5, cell.y + 0.5)
	# 挖掉时记进 _removed(读档后不再长回来);卸载区块时靠标记跳过
	body.set_meta("gk_cell", cell)
	body.set_meta("gk_level", level)
	body.set_meta("gk_placed", placed)
	body.set_meta("gk_kind", kind)
	body.tree_exiting.connect(_on_block_exiting.bind(body))
	return body


## 按 gk_kinds 重算这一列的碰撞盒:连续实心段合成一个盒子。
## 例:高度 3、都在 → 1 个 1×3×1 盒;挖掉中间那格 → 上下各一个盒,
## 洞的位置真的空出来(和以前"一格一个盒"的碰撞体积完全一致)。
func _rebuild_column_shapes(body: StaticBody3D) -> void:
	for child in body.get_children():
		if child is CollisionShape3D:
			body.remove_child(child)
			child.queue_free()
	var kinds: Array = body.get_meta("gk_kinds", [])
	var start := -1
	for level in range(kinds.size() + 1):
		var solid := level < kinds.size() and not str(kinds[level]).is_empty()
		if solid and start < 0:
			start = level
		elif not solid and start >= 0:
			_add_run_shape(body, start, level - 1)
			start = -1


func _add_run_shape(body: StaticBody3D, start: int, end: int) -> void:
	var shape := CollisionShape3D.new()
	var box := BoxShape3D.new()
	var size := float(end - start + 1)
	box.size = Vector3(BLOCK, size, BLOCK)
	shape.shape = box
	shape.position = Vector3(0.0, float(start) + size * 0.5, 0.0)
	body.add_child(shape)


## 挖掉一列里的一格:改 gk_kinds、重算碰撞段、只重建这一格所在的 section 网格。
## 掉落 / 存档记录和以前"一块一个节点 + tree_exiting"完全等价:
## 一样记 _removed、一样按 gk_no_drop 决定发不发物品。
func _remove_block(body: StaticBody3D, cell: Vector2i, level: int,
		kind: String, no_drop: bool) -> void:
	var kinds: Array = body.get_meta("gk_kinds")
	if level < 0 or level >= kinds.size():
		return
	kinds[level] = ""
	_removed[_block_key(cell, level)] = true
	if not no_drop:
		block_mined.emit(kind)
	var any := false
	for value in kinds:
		if not str(value).is_empty():
			any = true
			break
	if not any:
		# 整列挖空:标记成主动销毁,别让 tree_exiting 再记一遍
		body.set_meta("gk_retire", true)
		body.queue_free()
	else:
		_rebuild_column_shapes(body)
	# 可视网格:只重建这一格（以及跨段邻居）所在的分段，不重做整个区块
	_rebuild_sections_at(cell, level, "mine")


func _make_tree(cell: Vector2i, base_y: int) -> Node3D:
	var root := Node3D.new()
	root.name = "树_%d_%d" % [cell.x, cell.y]
	var body := StaticBody3D.new()
	body.name = "树身_%d_%d" % [cell.x, cell.y]
	body.collision_layer = 1
	body.collision_mask = 1
	var shape := CollisionShape3D.new()
	var box := BoxShape3D.new()
	box.size = Vector3(BLOCK, 4.0, BLOCK)
	shape.shape = box
	shape.position = Vector3(0.0, 2.0, 0.0)
	body.add_child(shape)
	for level in range(4):
		var trunk := MeshInstance3D.new()
		trunk.mesh = _visual("wood")
		trunk.material_override = _materials["wood"]
		trunk.position = Vector3(0.0, level + 0.5, 0.0)
		body.add_child(trunk)
	body.position = Vector3(cell.x + 0.5, base_y, cell.y + 0.5)
	# 树随机转个 90° 的整数倍:同一份叶子网格,看上去不会棵棵一模一样
	var yaw := float(_hash(cell, 31) % 4) * PI * 0.5
	body.rotation.y = yaw
	root.add_child(body)
	body.tree_exiting.connect(_on_tree_exiting.bind(body, cell, root))
	var leaves := MeshInstance3D.new()
	leaves.name = "树叶"
	leaves.mesh = _build_leaves()
	leaves.material_override = _materials["leaves"]
	leaves.position = Vector3(cell.x + 0.5, base_y, cell.y + 0.5)
	leaves.rotation.y = yaw
	root.add_child(leaves)
	return root


func _world_node() -> Node:
	if _world != null and is_instance_valid(_world):
		return _world
	var scene := get_tree().current_scene
	if scene == null:
		scene = get_parent()
	_world = scene
	return _world


## 玩家身上的背包脚本(开箱 / 合成 / 掉落都在它那儿)
func _inventory_node() -> Node:
	var host := get_parent()
	if host == null:
		return null
	for child in host.get_children():
		if child.has_method("try_open_chest"):
			return child
	return null


# ── 世界改动记录(命名存档用)──────────────────────────────────────
func _block_key(cell: Vector2i, level: int) -> String:
	return "%d,%d,%d" % [cell.x, cell.y, level]


func _cell_key(cell: Vector2i) -> String:
	return "%d,%d" % [cell.x, cell.y]


## 卸载 / 换世界前给整棵子树打上"主动销毁"标记:
## 不管哪种销毁都会发 tree_exiting,不打标记读档时会被误记成"被玩家挖掉"。
func _retire(node: Node) -> void:
	node.set_meta("gk_retire", true)
	for child in node.get_children():
		_retire(child)


func _on_block_exiting(body: Node) -> void:
	if body.get_meta("gk_retire", false):
		return
	if body.has_meta("gk_kinds"):
		# 合批后的整列被外力收掉(正常挖掘走 _remove_block,不会到这儿):
		# 把还没挖的格子全记成"已挖",和以前逐块 tree_exiting 的记录一致。
		# 这里不重建分段:这条路径只会在场景卸载时走到,往正在拆的场景里
		# add_child 会触发 "Parent node is busy setting up children"。
		var kinds: Array = body.get_meta("gk_kinds")
		var cell: Vector2i = body.get_meta("gk_cell")
		for level in range(kinds.size()):
			if not str(kinds[level]).is_empty():
				_removed[_block_key(cell, level)] = true
		return
	var cell: Vector2i = body.get_meta("gk_cell")
	var level := int(body.get_meta("gk_level"))
	var key := _block_key(cell, level)
	if bool(body.get_meta("gk_placed", false)) or _placed.has(key):
		# 放下的方块被挖掉:从"放下的方块"里删掉,别再复活
		_placed.erase(key)
	else:
		_removed[key] = true
	# 下沉 / 放置方块被收走:受影响的地形面要重新露出来,只重建附近的分段
	_queue_section_rebuild(cell, level)
	# 徒手挖"要求工具"的方块:照样碎,但不发掉落物
	if body.get_meta("gk_no_drop", false) == false:
		block_mined.emit(str(body.get_meta("gk_kind", "")))


## 单块方块被场景收走(不是挖掉)时要重建的分段,下一帧统一处理:
## 卸载场景 / 退出游戏那一帧 _process 不会再跑,避免往正在拆的树里 add_child。
func _queue_section_rebuild(cell: Vector2i, level: int) -> void:
	_section_rebuild_queue.append([cell, level])


func _on_tree_exiting(body: Node, cell: Vector2i, root: Node) -> void:
	if body.get_meta("gk_retire", false):
		return
	_removed_trees[_cell_key(cell)] = true
	# 树干算木头(叶子和树身是一整棵树,一起收走)
	block_mined.emit("wood")
	# 挖掉的是树干:整棵树(连叶子)一起收走,别留一圈浮空的树叶
	if is_instance_valid(root):
		_retire(root)
		root.queue_free()


## 存:挖掉的方块 / 放下的方块 / 挖掉的树。
## 放下的方块每次现扫场景 —— 它们挂在场景根上,不随区块卸载。
## 放置方块的类型:Godot 会给同名兄弟自动加编号(草方块2 / 草方块3),
## 只按名字精确匹配会漏掉从第二块开始的同类方块(存档丢、距离剔除也漏)。
func _placed_kind(node: Node) -> String:
	var label := String(node.name)
	if PLACE_KINDS.has(label):
		return PLACE_KINDS[label]
	var base := label
	while base.length() > 0 and base[base.length() - 1].is_valid_int():
		base = base.substr(0, base.length() - 1)
	if PLACE_KINDS.has(base):
		return PLACE_KINDS[base]
	# 第二块起名字靠不住:同名兄弟进场景时 Godot 会重新生成一个
	# "@StaticBody3D@123" 之类的内部名,连"草方块2"都保不住(真机抓到的)。
	# 这时退回看它挂的资产模型 —— 每种方块的模型都叫『<方块名>_源』。
	for child in node.find_children("*", "", true, false):
		var child_name := str(child.name)
		for key in PLACE_KINDS.keys():
			if child_name == str(key) + "_源" \
					or child_name.begins_with(str(key) + "_源"):
				return str(PLACE_KINDS[key])
	return ""


func save_state() -> Dictionary:
	var placed := _placed.duplicate()
	var world := _world_node()
	if world != null:
		for node in world.find_children("*", "StaticBody3D", true, false):
			var kind := _placed_kind(node)
			if kind.is_empty():
				continue
			var spot := (node as Node3D).global_position
			var cell := Vector2i(int(round(spot.x - 0.5)),
				int(round(spot.z - 0.5)))
			placed[_block_key(cell, int(round(spot.y)))] = kind
	return {
		"removed": _removed.duplicate(),
		"placed": placed,
		"trees": _removed_trees.duplicate(),
	}


## 读档:卸掉眼前这一片,按存档里的改动重新铺。
func load_state(state: Dictionary) -> void:
	_removed = {}
	_placed = {}
	_removed_trees = {}
	# 放置方块节点会按存档重建:登记表重新来一遍(先靠兜底扫描,之后 node_added)
	_placed_nodes = []
	_placed_seeded = false
	var removed = state.get("removed", {})
	if removed is Dictionary:
		for key in removed.keys():
			if bool(removed[key]):
				_removed[str(key)] = true
	var placed = state.get("placed", {})
	if placed is Dictionary:
		for key in placed.keys():
			var kind := str(placed[key])
			if _materials.has(kind):
				_placed[str(key)] = kind
	var trees = state.get("trees", {})
	if trees is Dictionary:
		for key in trees.keys():
			if bool(trees[key]):
				_removed_trees[str(key)] = true
	_unload_all()
	# 场上原有的"放置方块"已随 _unload_all 收掉:按存档里的记录重建一遍
	_rebuild_placed()


## 按玩家变量重新读一遍世界设置(新建世界 / 读档前调用)。
func _read_params() -> void:
	var host := get_parent()
	if host == null:
		return
	_world_seed = _hash_string(str(host.get("种子")))
	_terrain_type = clampi(int(host.get("地形类型")), 1, 3)
	# 世界高度(M2a):地形生成只读常量 TERRAIN_MAX,玩家变量只用于存档对账
	# 和迁移判断 —— 不允许旧变量值把地表抬高,否则"只抬容量不改外观"就破了。
	var height = host.get("世界高度")
	if height != null and int(height) > 0 and int(height) != WORLD_HEIGHT:
		push_warning("世界高度=%d 与当前版本 %d 不一致,地形仍按 %d 铺" % [
			int(height), WORLD_HEIGHT, WORLD_HEIGHT])
	_terrain_ceiling = TERRAIN_MAX
	var size_index := clampi(int(host.get("世界大小")), 1, WORLD_SIZES.size())
	_world_size = int(WORLD_SIZES[size_index - 1])
	var veg_index := clampi(int(host.get("植被密度")), 1, TREE_PERCENTS.size())
	_tree_percent = int(TREE_PERCENTS[veg_index - 1])
	_time_index = clampi(int(host.get("昼夜时间")), 1, 3)
	var radius_index := clampi(int(host.get("渲染距离")), 1,
		RADIUS_STEPS.size())
	_radius = int(RADIUS_STEPS[radius_index - 1])
	# 种子 / 地形类型 / 上限可能都变了,高度 / 表层缓存整批作废
	_height_cache.clear()
	_kind_cache.clear()


## 高度常量对外的只读出口(存档脚本 / 探针用,避免两边各写一份数字)。
func world_height() -> int:
	return WORLD_HEIGHT


func sea_level() -> int:
	return SEA_LEVEL


func section_height() -> int:
	return SECTION_HEIGHT


func terrain_max() -> int:
	return TERRAIN_MAX


## 新建世界 / 换种子:清空改动,按新参数重铺,并把天空调成对应时间。
func apply_world() -> void:
	_read_params()
	_spawn_cell = Vector2i(1 << 30, 1 << 30)
	_removed = {}
	_placed = {}
	_removed_trees = {}
	_placed_nodes = []
	_placed_seeded = false
	_unload_all()
	var world := _world_node()
	if world != null:
		for node in world.find_children("*", "StaticBody3D", true, false):
			if _placed_kind(node).is_empty():
				continue
			node.queue_free()
	_apply_time()


## 设置里改渲染距离:保留已经铺好的列,只按新半径重算可见范围。
## (以前这里直接 _unload_all,改一档就整屏黑掉重建,要十几秒才长回来。)
func set_radius(value: int) -> void:
	var next := clampi(value, 8, 512)
	if next == _radius:
		return
	_radius = next
	# 让 _advance 下一帧重新 _replan:近处 / 范围内的列原地保留,只补缺口。
	_player_region = Vector2i(1 << 30, 1 << 30)
	_apply_time()


func _unload_all() -> void:
	for cell in _cells.keys():
		var node: Node = _cells[cell]
		if is_instance_valid(node):
			_retire(node)
			node.queue_free()
	_cells.clear()
	for region in _far_regions.keys():
		var far: Node = _far_regions[region]
		if is_instance_valid(far):
			_retire(far)
			far.queue_free()
	_far_regions.clear()
	for region in _handover.keys():
		var pending: Node = _handover[region]
		if is_instance_valid(pending):
			_retire(pending)
			pending.queue_free()
	_handover.clear()
	# 分段网格也一起收掉(换世界 / 读档后按新地形重建)
	for key in _sections.keys():
		var holder: Node = _sections[key]
		if is_instance_valid(holder):
			_retire(holder)
			holder.queue_free()
	_sections.clear()
	_section_queue = []
	_near_regions.clear()
	_near_queue = []
	_region_queue = []
	_progress_total = 1
	_spawn_active = false
	_spawn_pad_queue = []
	_bare_cells = {}
	_spawn_total = 0
	_wanted = {}
	_far_unloaded = {}
	_chunk_rebuild_count = 0
	_player_region = Vector2i(1 << 30, 1 << 30)
	_center = Vector2i(1 << 30, 1 << 30)
	# 玩家放下的方块挂在场景根上,不属于任何一列:换世界 / 读档前单独收掉
	var world := _world_node()
	if world != null:
		for node in world.find_children("*", "StaticBody3D", true, false):
			if bool(node.get_meta("gk_placed", false)) \
					or not _placed_kind(node).is_empty():
				_retire(node)
				node.queue_free()


## 放置方块进树时登记一次。只认 StaticBody3D,地形列也会走到这里,
## 但名字不是 PLACE_KINDS —— 两次字符串判断就返回,代价远低于全场扫描。
func _on_node_added(node: Node) -> void:
	_adopt_placed_node(node)


func _adopt_placed_node(node: Node) -> void:
	if not (node is StaticBody3D) or node.is_queued_for_deletion():
		return
	# 地形列 / 出生圈的物理体自己就叫『地形_x_z』:直接跳过,不进深名字匹配
	# (它们挂在『区块_x_z』下面,判父节点名字是判不到的)
	if String(node.name).begins_with("地形_"):
		return
	var kind := _placed_kind(node)
	if kind.is_empty() and bool(node.get_meta("gk_placed", false)):
		kind = str(node.get_meta("gk_kind", ""))
	if kind.is_empty() or _placed_nodes.has(node):
		return
	_placed_nodes.append(node)


## 放下的方块挂在场景根上,不随区块卸载 —— 离玩家超过渲染距离就藏起来。
## 顺手做两件同源的事:
##   * 放置实例的网格 / 材质统一成地形那一套(最近邻过滤),不然明显更糊;
##   * 贴着玩家放下的方块挪到相邻格(挪不动就收掉),别把人卡进方块里。
## M2a 第四段后只遍历 _placed_nodes(增量登记),不再每 0.25 秒 find_children。
func _cull_placed() -> void:
	var world := _world_node()
	var host := get_parent()
	if world == null or host == null or not (host is Node3D):
		return
	var here: Vector3 = (host as Node3D).global_position
	var reach := float(_radius) + 2.0
	# 兜底扫一次:读档 / 场景初始化时可能早于 node_added 连接
	if not _placed_seeded:
		_placed_seeded = true
		for node in world.find_children("*", "StaticBody3D", true, false):
			_adopt_placed_node(node)
	var alive: Array = []
	for node in _placed_nodes:
		if not is_instance_valid(node) or node.is_queued_for_deletion():
			continue
		alive.append(node)
		var kind := _placed_kind(node)
		if kind.is_empty() and bool(node.get_meta("gk_placed", false)):
			kind = str(node.get_meta("gk_kind", ""))
		if kind.is_empty():
			continue
		_dress_placed(node, kind)
		var spot := (node as Node3D).global_position
		if _overlaps_player(node, spot, here):
			_fix_placed(node, kind, spot, here)
			_register_placed(node, kind)
			continue
		(node as Node3D).visible = Vector2(spot.x - here.x,
			spot.z - here.z).length() <= reach
		_register_placed(node, kind)
	_placed_nodes = alive


## 新放下的方块登记进 _placed,并只重建它碰到的分段网格:
## 被新方块挡住的那些面要剔除,否则和方块自己的面贴在一起(重面)。
## 读档重建的方块本来就是 _placed 里的,不会重复触发。
func _register_placed(node: Node, kind: String) -> void:
	if not (node is Node3D) or node.is_queued_for_deletion():
		return
	var spot: Vector3 = (node as Node3D).global_position
	var level := int(node.get_meta("gk_level", -1))
	if level < 0:
		# 资产实例的原点在方块底面:底面中心 y 就是层号
		level = int(round(spot.y))
	var cell := Vector2i(int(round(spot.x - 0.5)),
		int(round(spot.z - 0.5)))
	var key := _block_key(cell, level)
	if str(_placed.get(key, "")) == kind:
		return
	_placed[key] = kind
	_rebuild_sections_at(cell, level, "place")


## 放置实例的视觉和地形完全同一套网格 + 材质(最近邻、无光照、顶点色)。
## 节点图生成的可放置资产自带线性过滤 + mipmap,近距离看会糊成一片。
func _dress_placed(node: Node, kind: String) -> void:
	if not _materials.has(kind):
		return
	for child in node.find_children("*", "MeshInstance3D", true, false):
		var visual := child as MeshInstance3D
		visual.mesh = _visual(kind)
		visual.material_override = _materials[kind]
		# 地形网格以方块中心为原点,节点图资产以底面为原点:不抬半格,
		# 换完网格就有一半埋在地里(用户实测"方块陷进地面")。
		if not node.has_meta("gk_level"):
			visual.position = Vector3(0.0, 0.5, 0.0)


func _block_span(node: Node, spot: Vector3) -> Vector2:
	if node.has_meta("gk_level"):
		# 本脚本重建的方块:坐标是方块中心
		return Vector2(spot.y - 0.5, spot.y + 0.5)
	# 节点图生成的可放置资产:坐标在方块底面
	return Vector2(spot.y, spot.y + 1.0)


func _overlaps_player(node: Node, spot: Vector3, here: Vector3) -> bool:
	if absf(spot.x - here.x) >= 0.85 or absf(spot.z - here.z) >= 0.85:
		return false
	var span := _block_span(node, spot)
	return span.y > here.y + 0.08 and span.x < here.y + 1.75


## 和玩家重叠的方块:优先挪到相邻的空格,挪不动才收掉。
func _fix_placed(node: Node, _kind: String, spot: Vector3, here: Vector3) -> void:
	if node.has_meta("gk_level"):
		# 读档重建的方块:从改动记录里一起删掉,再收走节点
		var cell: Vector2i = node.get_meta("gk_cell")
		_placed.erase(_block_key(cell, int(node.get_meta("gk_level"))))
		_retire(node)
		node.queue_free()
		return
	var away := Vector2(spot.x - here.x, spot.z - here.z)
	if away.length() < 0.1:
		away = Vector2(1.0, 0.0)
	var from := Vector2i(int(round(spot.x - 0.5)), int(round(spot.z - 0.5)))
	var step := Vector2i(1 if away.x >= 0.0 else -1, 0)
	if absf(away.y) > absf(away.x):
		step = Vector2i(0, 1 if away.y >= 0.0 else -1)
	var target := from + step
	var level := int(round(spot.y))
	if _in_world(target) and _height_at(target) <= level:
		(node as Node3D).global_position = Vector3(target.x + 0.5,
			float(level), target.y + 0.5)
		return
	node.queue_free()


## 昼夜:交给『我的世界天空』脚本(它已经在管天空盒 / 雾);
## 这里只负责在新建世界 / 读档后喊它按当前『昼夜时间』重刷一次。
func _apply_time() -> void:
	var host := get_parent()
	if host == null:
		return
	for child in host.get_children():
		if child.has_method("apply_time"):
			child.call("apply_time")
			return


func _hash_string(text: String) -> int:
	var value := 0
	for index in range(text.length()):
		value = (value * 131 + text.unicode_at(index)) & 0x7fffffff
	return value


# ── 地形形状(确定性哈希:走远了再回来还是同一片地)──────────────────
func _hash(cell: Vector2i, salt: int) -> int:
	var n: int = (cell.x * 73856093) ^ (cell.y * 19349663) \
		^ (salt * 83492791) ^ (_world_seed * 2654435761)
	n = (n ^ (n >> 13)) * 1274126177
	n = n & 0x7fffffff
	return n ^ (n >> 16)


## 平滑值噪声:用**世界坐标**做采样点,双线性插值 + 两倍频。
## 以前的写法是"每 3x3 格取一个哈希高度",整片地形是一块块 3x3 的台阶,
## 远看就像同一个区块被复制粘贴(用户实测反馈)。现在每一列都按自己的
## 世界坐标插值,近景 / 远景共用这一个函数,接缝也一致。
func _noise01(x: float, z: float, period: float, salt: int) -> float:
	var fx := x / period
	var fz := z / period
	var ix := int(floor(fx))
	var iz := int(floor(fz))
	var tx := fx - float(ix)
	var tz := fz - float(iz)
	tx = tx * tx * (3.0 - 2.0 * tx)
	tz = tz * tz * (3.0 - 2.0 * tz)
	var a := float(_hash(Vector2i(ix, iz), salt) % 1000) / 999.0
	var b := float(_hash(Vector2i(ix + 1, iz), salt) % 1000) / 999.0
	var c := float(_hash(Vector2i(ix, iz + 1), salt) % 1000) / 999.0
	var d := float(_hash(Vector2i(ix + 1, iz + 1), salt) % 1000) / 999.0
	return lerpf(lerpf(a, b, tx), lerpf(c, d, tx), tz)


func _height_at(cell: Vector2i) -> int:
	var cached: int = int(_height_cache.get(cell, -1))
	if cached >= 0:
		return cached
	var x := float(cell.x) + 0.5
	var z := float(cell.y) + 0.5
	# 四层不同尺度叠起来(用户实测:只有两层、尺度又接近,整片还是很平):
	#   64 格 = 大陆,决定这一片是低地还是高地;
	#   19 格 = 丘陵,缓坡和山包;
	#    6 格 = 小起伏;
	#    2 格 = 一格一格的毛边(只在近景看得出来)。
	var continent := _noise01(x, z, 64.0, 3)
	var hills := _noise01(x, z, 19.0, 11)
	var detail := _noise01(x, z, 6.0, 23)
	var fine := _noise01(x, z, 2.0, 37)
	# 地形类型:1 平原 / 2 丘陵(默认)/ 3 山地。
	# M2b(第两百一十九轮):按世界高度 64 重定 —— 海平面仍是 32,
	# 陆地主体落在 34~52 层,山地最高到 60 层;低地 / 盆地压到 32 以下灌水。
	# 默认档(2)在 ±320 步长 8 上的目标是 p50≈38 / p90≈48 / max≤60。
	var lo := 26.5
	var span := 27.0
	var basin := 4.0
	var mtn_lift := 4.0
	var mtn_span := 8.0
	var amp := 7.0
	var relief := 1.6
	var flat := 0.65
	var flat_y := 33.0
	match _terrain_type:
		1:
			lo = 25.0
			span = 20.0
			basin = 2.0
			mtn_lift = 2.0
			mtn_span = 4.0
			amp = 2.4
			relief = 0.7
			flat = 0.75
			flat_y = 34.0
		3:
			lo = 25.5
			span = 28.0
			basin = 3.0
			amp = 10.0
			relief = 2.0
			flat = 0.55
	# 第两百一十四轮:大尺度分区 —— 同一张图里能走出成片的平原 / 丘陵 / 山地。
	# zone 是 80 格尺度的低频噪声:mountain 越高越像山地,plain 越高越平。
	var zone := _noise01(x, z, 80.0, 53)
	var mountain := smoothstep(0.58, 0.80, zone)
	var plain := 1.0 - smoothstep(0.28, 0.46, zone)
	# 基准面:大陆噪声决定这一片整体多高(低地 26 层左右,高地 53 层左右)。
	var h := lo + span * continent
	# 大尺度山区再整体抬升;低洼盆地再压低,水面才有"湖 / 海湾"而不是一条线。
	h += mountain * (mtn_lift + continent * mtn_span)
	h -= basin * (1.0 - smoothstep(0.10, 0.42, continent))
	# hill_weight 只管"这一片起伏多大":低地平缓、高原 / 山地起伏大。
	var hill_weight := (0.22 + continent * continent * 0.95) \
		* (0.45 + mountain * 1.35)
	h += (hills - 0.5) * amp * hill_weight \
		+ (detail - 0.5) * relief * (0.6 + mountain) \
		+ (fine - 0.5) * 0.9
	# 平原区压成贴着海平面的平地(略高于 32,不会整片淹掉)
	h = lerpf(h, flat_y + (hills - 0.5) * 2.0, plain * flat)
	var result := clampi(int(round(h)), 1, _terrain_ceiling)
	# 缓存有上限:走得很远也不会无限涨;超了就整批丢掉重新算(结果不变)
	if _height_cache.size() >= 65536:
		_height_cache.clear()
	_height_cache[cell] = result
	return result


## 伪群系(第两百一十五轮):不追 1.18 的 5 参数 spline / 3D 密度,
## 只用温度 / 湿度两个 2D 低频噪声分区,让走过一片能看到地表和树密度的变化。
func _biome_at(cell: Vector2i) -> String:
	var x := float(cell.x) + 0.5
	var z := float(cell.y) + 0.5
	var temp := _noise01(x, z, 96.0, 61)
	var wet := _noise01(x, z, 72.0, 67)
	if temp < 0.34:
		return "snow"
	if wet < 0.42:
		return "dry"
	if wet > 0.62:
		return "forest"
	return "plains"


func _kind_at(cell: Vector2i, height: int) -> String:
	var cached := str(_kind_cache.get(cell, ""))
	if not cached.is_empty():
		return cached
	var result := _kind_uncached(cell, height)
	if height == _height_at(cell):
		if _kind_cache.size() >= 65536:
			_kind_cache.clear()
		_kind_cache[cell] = result
	return result


func _kind_uncached(cell: Vector2i, height: int) -> String:
	# 水下(M2b):水面到 32,低于海平面的地表不能长草 —— 近岸铺沙、
	# 深水铺泥土,和原版海底 / 湖底一致;树和花草也不会长到水里。
	if height < SEA_LEVEL:
		return "sand" if height >= SEA_LEVEL - 4 else "dirt"
	# 海岸:贴着水面的那一圈铺沙,岸边从草到水的过渡不会太突兀
	if height <= SEA_LEVEL + 1:
		return "sand"
	# 原版的丘陵绝大多数是"草地顶 + 泥土侧面",石头只在少数山头露出来;
	# 露头的石头里再掺一点煤 / 铁矿石(挖掉就没了,没有背包)。
	# 以前是"高度 >= 3 的列 30% 随机变石头"—— 平地上撒满单格灰块,远看像碎石地。
	# 现在只在坡面(和四邻落差 >= 2)和山顶露岩,平原保持整片草。
	# M2b:阈值按世界高度 64 重定 —— 旧版的 4/7/8 是按 24 层定的,
	# 照搬会让整片陆地都变成石头。
	if height >= SEA_LEVEL + 6:
		var steep := false
		for offset in [Vector2i(1, 0), Vector2i(-1, 0),
				Vector2i(0, 1), Vector2i(0, -1)]:
			if absi(_height_at(cell + offset) - height) >= 2:
				steep = true
				break
		var rock := _hash(cell, 21) % 100
		if steep and rock < 55:
			return "stone"
		if height >= SEA_LEVEL + 16 and rock < 40:
			return "stone"
		if steep and rock < 62:
			return "coal"
		if height >= SEA_LEVEL + 20 and rock < 48:
			return "iron"
	var biome := _biome_at(cell)
	if biome == "snow":
		return "snow"
	# 沙地按 4×4 一块撒(以前 2×2 一块、10% 的密度,平地上像撒了一层碎纸屑)
	# 干地群系沙斑更多,森林 / 平原保持稀疏。
	var patch_chance := 14 if biome == "dry" else 6
	var patch := Vector2i(int(floor((cell.x + 0.5) / 4.0)),
		int(floor((cell.y + 0.5) / 4.0)))
	if _hash(patch, 13) % 100 < patch_chance:
		return "sand"
	return "grass"


func _has_tree(cell: Vector2i) -> bool:
	if _tree_percent <= 0:
		return false
	# 水下的列不长树(海平面 32 以下全泡在水里)
	if _height_at(cell) < SEA_LEVEL:
		return false
	# 第两百一十四轮:树不再满图均匀散点 —— 先看"这一片是不是林子":
	#   forest 是 48 格尺度的低频噪声,高处成片长树、低处是空地;
	#   glade  是 23 格尺度的另一层,再压出几块明显没树的草坪;
	#   林子里按"中心密、边缘稀"的权重散点,离开林子就完全没有树。
	# 这样走动时能看到成片树林和成片空地,而不是一路都是均匀的树。
	var x := float(cell.x) + 0.5
	var z := float(cell.y) + 0.5
	var forest := _noise01(x, z, 48.0, 41)
	var glade := _noise01(x, z, 23.0, 43)
	if glade > 0.86:
		return false
	var weight := clampf((forest - 0.38) / 0.62, 0.0, 1.0)
	if weight <= 0.01:
		return false
	# 换算比例沿用以前的观感(百分比 10 ≈ 1.1%),再乘林子权重:
	# 林子中心约 5.5% 一格、边缘渐变到 0,空地完全没有树。
	# 伪群系再叠一层:森林更密,干地 / 雪原更稀。
	var biome_weight := 1.0
	match _biome_at(cell):
		"forest":
			biome_weight = 1.5
		"plains":
			biome_weight = 0.8
		"dry":
			biome_weight = 0.35
		"snow":
			biome_weight = 0.25
	# 内容加法第一批(植被加密):整体再乘 1.35,林心和林缘一起变密;
	# 各群系的相对比例(森林 1.5 / 平原 0.8 / 干地 0.35 / 雪原 0.25)不动。
	var density := float(_tree_percent) * 100.0 / 9.0 * biome_weight * 1.35
	return _hash(cell, 5) % 10000 < int(density * weight * 5.0)


## ── 出生点(第两百一十六轮):只挑位置,不改地形 ──────────────────
## 返回脚底站立的点(cell 中心 + 地表高度),并把坐标写进玩家变量,
## 之后『回到出生点』和死亡重生都读这三个变量,不再用固定的 (0.5,1,0.5)。
## 规则:
##   * 候选必须非水非岩浆、上方两格不被树干占住;
##   * 评分 = 候选周围 7×7 的最大高度差,越小越好;同分取离原点更近的;
##   * 和原版一样优先草地(裸石山顶虽然也"平",但不是能玩的出生点),
##     只有出生区域里一格草都没有时才退回"落差最小的任意地表"。
func _find_spawn_cell(grass_only: bool) -> Vector2i:
	var best := Vector2i(1 << 30, 1 << 30)
	var best_score := 1 << 30
	var best_gap := 1 << 30
	var found := false
	for z in range(-SPAWN_SEARCH, SPAWN_SEARCH + 1):
		for x in range(-SPAWN_SEARCH, SPAWN_SEARCH + 1):
			var cell := Vector2i(x, z)
			if not _in_world(cell):
				continue
			var height := _height_at(cell)
			var top_kind := _kind_at(cell, height)
			# 水面(M2b):海平面以下的列是水,不能当出生点
			if height < SEA_LEVEL:
				continue
			if top_kind in ["water", "lava"]:
				continue
			if grass_only and top_kind != "grass":
				continue
			if _has_tree(cell):
				continue
			var gap_height := 0
			for dz in range(-SPAWN_NEIGHBORS, SPAWN_NEIGHBORS + 1):
				for dx in range(-SPAWN_NEIGHBORS, SPAWN_NEIGHBORS + 1):
					gap_height = maxi(gap_height,
						absi(_height_at(cell + Vector2i(dx, dz)) - height))
			# 同分时的偏好:别站在花草里、别贴着树干出生
			var bonus := 0
			if not _plant_at(cell, top_kind).is_empty():
				bonus += 3
			var near_tree := false
			for dz in range(-2, 3):
				for dx in range(-2, 3):
					if _has_tree(cell + Vector2i(dx, dz)):
						near_tree = true
						break
				if near_tree:
					break
			if near_tree:
				bonus += 4
			var score := gap_height * 10 + bonus
			var gap := x * x + z * z
			if not found or score < best_score \
					or (score == best_score and gap < best_gap):
				found = true
				best = cell
				best_score = score
				best_gap = gap
	return best


func choose_spawn() -> Vector3:
	# 出生点只跟世界设置 / 种子有关,算一次就缓存;频繁读档不用重扫。
	if _spawn_cell.x > (1 << 29):
		_spawn_cell = _find_spawn_cell(true)
		if _spawn_cell.x > (1 << 29):
			_spawn_cell = _find_spawn_cell(false)
	var best := _spawn_cell
	var point := Vector3(best.x + 0.5, float(_height_at(best)),
		best.y + 0.5)
	var host := get_parent()
	if host != null:
		# 生成器会把变量名里的 X/Y/Z 统一转成小写(见导出的玩家脚本)
		host.set("出生点x", point.x)
		host.set("出生点y", point.y)
		host.set("出生点z", point.z)
	_keep_spawn_safe(point)
	return point


## 出生点保护:33×33(半径 16)内不许有敌对生物。
## 本示例的僵尸是 Python 侧预摆放的固定节点(没有运行时刷怪器),
## 所以"不生成"落成"把已经落在保护圈里的挪出去" —— 效果和原版一致。
## 羊是被动生物,不受这条影响。
func _keep_spawn_safe(point: Vector3) -> void:
	var scene := get_tree().current_scene
	if scene == null:
		return
	var index := 0
	for found in scene.find_children("怪物*", "CharacterBody3D", true, false):
		var body := found as Node3D
		if body == null:
			continue
		var flat := Vector2(body.global_position.x - point.x,
			body.global_position.z - point.z)
		if flat.length() >= SPAWN_SAFE_RADIUS:
			continue
		var angle := TAU * float(index) / 3.0 + 0.7
		var cell := Vector2i(int(floor(point.x + cos(angle) * 24.0)),
			int(floor(point.z + sin(angle) * 24.0)))
		body.global_position = Vector3(cell.x + 0.5,
			float(_height_at(cell)), cell.y + 0.5)
		if body is CharacterBody3D:
			(body as CharacterBody3D).velocity = Vector3.ZERO
		index += 1


## ── 植被(第两百一十六轮)────────────────────────────────────────
## 表层草方块上按群系密度长短草 / 花;噪声是 9 格尺度,所以是"一小丛一小丛"
## 而不是均匀撒点。树干格不长(那里是树身),雪原 / 干地明显更少。
func _plant_at(cell: Vector2i, top_kind: String) -> String:
	if top_kind != "grass" or _has_tree(cell):
		return ""
	var biome := _biome_at(cell)
	if biome == "snow":
		return ""
	var x := float(cell.x) + 0.5
	var z := float(cell.y) + 0.5
	var clump := _noise01(x, z, 9.0, 83)
	# 内容加法第一批(植被加密):草整体 ×1.5、花 0.035 → 0.05,
	# 各群系仍按 forest > plains > dry 的原有比例;雪原不长草不变。
	var grass_chance := 0.24
	match biome:
		"forest":
			grass_chance = 0.38
		"plains":
			grass_chance = 0.27
		"dry":
			grass_chance = 0.075
	var roll := float(_hash(cell, 71) % 1000) / 1000.0
	if roll < grass_chance * (0.55 + clump):
		return "tall_grass"
	# 花比草稀,而且不往干地长;黄 / 红按格子哈希稳定二选一
	if biome != "dry" \
			and float(_hash(cell, 73) % 1000) / 1000.0 < 0.05:
		return "flower_yellow" if _hash(cell, 79) % 2 == 0 else "flower_red"
	return ""


## 植被节点:Area3D 只给射线 / 准星用,不参与 CharacterBody3D 的碰撞
## (原版花草也是能直接走过去的)。名字 / meta 和方块保持同一套,
## 挖掉时 _on_block_exiting 自动记进存档并发出 block_mined。
func _make_plant(cell: Vector2i, level: int, kind: String) -> Area3D:
	var area := Area3D.new()
	area.name = "植物_%d_%d" % [cell.x, cell.y]
	area.collision_layer = 1
	area.collision_mask = 0
	area.monitoring = false
	area.monitorable = true
	var shape := CollisionShape3D.new()
	var box := BoxShape3D.new()
	box.size = Vector3(0.8, 1.0, 0.8)
	shape.shape = box
	shape.position = Vector3(0.0, 0.5, 0.0)
	area.add_child(shape)
	var visual := MeshInstance3D.new()
	visual.mesh = _plant_meshes[kind]
	visual.material_override = _plant_materials[kind]
	area.add_child(visual)
	area.position = Vector3(cell.x + 0.5, level, cell.y + 0.5)
	area.set_meta("gk_cell", cell)
	area.set_meta("gk_level", level)
	area.set_meta("gk_plant", true)
	area.set_meta("gk_kind", kind)
	area.tree_exiting.connect(_on_block_exiting.bind(area))
	return area


## 放置前的目标格计算 + 占用检查(节点图在『资产_创建实例』之前调这个)。
##
## 目标格永远紧贴"准星打中的那一面":命中格 = floor(命中点 - 法线×0.5),
## 目标格 = 命中格 + 法线,目标中心 = 目标格 + (0.5, 0.5, 0.5)。
## 三根轴一起算,角上不会偏格,也不会出现"点在空气里凭空放"(必须打到面)。
## 方块网格以中心为原点,所以目标中心就是实例的坐标(以前按底面算,
## Y 少了半格,放下去会有一半陷进地面 —— 用户实测)。
## 占用检查同样用 0.9 立方扫目标中心:任何碰撞体都算,不只是地形。
## 原版不能把方块放进玩家 / 生物身体里,所以 mask 要带上玩家(层 2)、
## 怪物(层 4)、羊(层 8),不能只查地形的层 1。
func can_place_here() -> bool:
	var host := get_parent()
	if host == null:
		return false
	# 右键点在箱子 / 工作台上 = 开界面(原版没有单独的开箱 / 合成键),这次不放方块
	var inv := _inventory_node()
	if inv != null and inv.has_method("try_open_chest"):
		if bool(inv.call("try_open_chest")):
			host.set("放置允许", false)
			return false
	if inv != null and inv.has_method("try_open_craft"):
		if bool(inv.call("try_open_craft")):
			host.set("放置允许", false)
			return false
	var hit: Vector3 = host.get("放置命中")
	var normal: Vector3 = host.get("放置法线")
	if normal.length() < 0.5:
		host.set("放置允许", false)
		return false
	var base := Vector3(floor(hit.x - normal.x * 0.5),
		floor(hit.y - normal.y * 0.5), floor(hit.z - normal.z * 0.5))
	var cell := base + normal
	# 目标格必须和命中格相邻(单轴一步);防法线数据异常时放出斜角
	if absf((cell - base).length() - 1.0) > 0.01:
		host.set("放置允许", false)
		return false
	var target := cell + Vector3(0.5, 0.5, 0.5)
	# 节点图生成的可放置资产原点在方块底面:交给『资产_创建实例』的坐标
	# 要再往下半格;占用检查仍然用方块中心。
	host.set("放置目标", target - Vector3(0.0, 0.5, 0.0))
	var box := BoxShape3D.new()
	box.size = Vector3(0.9, 0.9, 0.9)
	var params := PhysicsShapeQueryParameters3D.new()
	params.shape = box
	params.transform = Transform3D(Basis(), target)
	# 1 地形 | 2 玩家 | 4 怪物 | 8 羊:目标格里只要有任何碰撞体就拒绝
	params.collision_mask = 15
	var blocked := false
	for hit_info in get_world_3d().direct_space_state.intersect_shape(params, 8):
		if hit_info.get("collider") is CollisionObject3D:
			blocked = true
			break
	# 原版往花草的位置放方块 = 花草被顶掉;植被没有碰撞,占用检查发现不了,
	# 这里在允许放置时顺手清掉目标格里的花草,别让方块和草叠在一起。
	if not blocked:
		_clear_plant(Vector2i(int(cell.x), int(cell.z)), int(cell.y))
	host.set("放置允许", not blocked)
	return not blocked


func _clear_plant(cell: Vector2i, level: int) -> void:
	var scene := get_tree().current_scene
	if scene == null:
		return
	for node in scene.find_children("植物_*", "Area3D", true, false):
		if node.get_meta("gk_cell", Vector2i(1 << 30, 1 << 30)) == cell \
				and int(node.get_meta("gk_level", -1)) == level:
			node.queue_free()


# ── 网格(按方块类型缓存一份)──────────────────────────────────────
func _visual(kind: String) -> ArrayMesh:
	if _visuals.has(kind):
		return _visuals[kind]
	var mesh := _block_mesh(kind)
	_visuals[kind] = mesh
	return mesh


func _block_mesh(kind: String) -> ArrayMesh:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	# 63 = 六面全露;分段网格走同一个 _append_block,只传露出的面,外观不变
	_append_block(st, kind, Vector3.ZERO, 63)
	st.generate_normals()
	return st.commit()


## 方块六个面的 UV 范围。顺序:顶 t0/t1、侧 s0/s1、底 b0/b1。
func _block_tiles(kind: String) -> Array:
	var span := 1.0
	var top_tile := 0
	var side_tile := 0
	var bottom_tile := 0
	match kind:
		"grass":
			span = 1.0 / 3.0
			top_tile = 0
			side_tile = 1
			bottom_tile = 2
		"wood":
			span = 0.5
			top_tile = 1
			side_tile = 0
			bottom_tile = 1
	var t0 := top_tile * span
	var t1 := t0 + span
	var s0 := side_tile * span
	var s1 := s0 + span
	var b0 := bottom_tile * span
	return [t0, t1, s0, s1, b0, b0 + span]


## 把一个方块(中心 center)按 mask 指定的面追加到 SurfaceTool。
## mask 位:1 顶 / 2 底 / 4 北(z-)/ 8 南(z+)/ 16 东(x+)/ 32 西(x-)。
## 几何 / UV / 顶点色和旧的 _block_mesh 完全一致(逐面抄下来),只是可以
## 只画露在外面的那几面。
func _append_block(st: SurfaceTool, kind: String, center: Vector3,
		mask: int, ao: Dictionary = {}) -> void:
	# 柔和光影关掉 = 回到改动前的观感:每个面同一亮度。
	# 打开才有"顶面亮 / 南北 0.8 / 东西 0.64 / 底面 0.5"的面朝向差 + AO。
	var top_shade := 1.0
	var z_shade := 0.80 if _soft_light else 1.0
	var x_shade := 0.64 if _soft_light else 1.0
	var bottom_shade := 0.5 if _soft_light else 1.0
	var tiles := _block_tiles(kind)
	var t0: float = tiles[0]
	var t1: float = tiles[1]
	var s0: float = tiles[2]
	var s1: float = tiles[3]
	var b0: float = tiles[4]
	var b1: float = tiles[5]
	var h := 0.5
	if mask & 1:
		# 顶面(最亮)
		_quad_ao(st, center + Vector3(-h, h, -h), center + Vector3(-h, h, h),
			center + Vector3(h, h, h), center + Vector3(h, h, -h),
			Vector2(t0, 0), Vector2(t0, 1), Vector2(t1, 1), Vector2(t1, 0),
			top_shade, ao.get(1, []))
	if mask & 8:
		# 南面(z+,稍暗)
		_quad_ao(st, center + Vector3(-h, -h, h), center + Vector3(-h, h, h),
			center + Vector3(h, h, h), center + Vector3(h, -h, h),
			Vector2(s0, 1), Vector2(s0, 0), Vector2(s1, 0), Vector2(s1, 1),
			z_shade, ao.get(8, []))
	if mask & 4:
		# 北面(z-)
		_quad_ao(st, center + Vector3(h, -h, -h), center + Vector3(h, h, -h),
			center + Vector3(-h, h, -h), center + Vector3(-h, -h, -h),
			Vector2(s0, 1), Vector2(s0, 0), Vector2(s1, 0), Vector2(s1, 1),
			z_shade, ao.get(4, []))
	if mask & 16:
		# 东面(x+,更暗)
		_quad_ao(st, center + Vector3(h, -h, h), center + Vector3(h, h, h),
			center + Vector3(h, h, -h), center + Vector3(h, -h, -h),
			Vector2(s0, 1), Vector2(s0, 0), Vector2(s1, 0), Vector2(s1, 1),
			x_shade, ao.get(16, []))
	if mask & 32:
		# 西面(x-)
		_quad_ao(st, center + Vector3(-h, -h, -h), center + Vector3(-h, h, -h),
			center + Vector3(-h, h, h), center + Vector3(-h, -h, h),
			Vector2(s0, 1), Vector2(s0, 0), Vector2(s1, 0), Vector2(s1, 1),
			x_shade, ao.get(32, []))
	if mask & 2:
		# 底面(几乎看不到,但挖掉下面一块时不至于透出天空)
		_quad_ao(st, center + Vector3(-h, -h, h), center + Vector3(h, -h, h),
			center + Vector3(h, -h, -h), center + Vector3(-h, -h, -h),
			Vector2(b0, 0), Vector2(b1, 0), Vector2(b1, 1), Vector2(b0, 1),
			bottom_shade, ao.get(2, []))


func _build_leaves() -> ArrayMesh:
	if _leaves_mesh != null:
		return _leaves_mesh
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	for dx in range(-1, 2):
		for dz in range(-1, 2):
			_add_cube(st, Vector3(dx, 3.5, dz))
			_add_cube(st, Vector3(dx, 4.5, dz))
	for offset in [Vector3(0, 5.5, 0), Vector3(1, 5.5, 0),
			Vector3(-1, 5.5, 0), Vector3(0, 5.5, 1), Vector3(0, 5.5, -1)]:
		_add_cube(st, offset)
	st.generate_normals()
	_leaves_mesh = st.commit()
	return _leaves_mesh


func _add_cube(st: SurfaceTool, center: Vector3) -> void:
	var x := center.x
	var y := center.y
	var z := center.z
	var h := 0.5
	_quad(st, Vector3(x - h, y + h, z - h), Vector3(x - h, y + h, z + h),
		Vector3(x + h, y + h, z + h), Vector3(x + h, y + h, z - h),
		Vector2(0, 0), Vector2(0, 1), Vector2(1, 1), Vector2(1, 0), 1.0)
	_quad(st, Vector3(x - h, y - h, z + h), Vector3(x - h, y + h, z + h),
		Vector3(x + h, y + h, z + h), Vector3(x + h, y - h, z + h),
		Vector2(0, 0), Vector2(0, 1), Vector2(1, 1), Vector2(1, 0), 0.78)
	_quad(st, Vector3(x + h, y - h, z - h), Vector3(x + h, y + h, z - h),
		Vector3(x - h, y + h, z - h), Vector3(x - h, y - h, z - h),
		Vector2(0, 0), Vector2(0, 1), Vector2(1, 1), Vector2(1, 0), 0.78)
	_quad(st, Vector3(x + h, y - h, z + h), Vector3(x + h, y + h, z + h),
		Vector3(x + h, y + h, z - h), Vector3(x + h, y - h, z - h),
		Vector2(0, 0), Vector2(0, 1), Vector2(1, 1), Vector2(1, 0), 0.66)
	_quad(st, Vector3(x - h, y - h, z - h), Vector3(x - h, y + h, z - h),
		Vector3(x - h, y + h, z + h), Vector3(x - h, y - h, z + h),
		Vector2(0, 0), Vector2(0, 1), Vector2(1, 1), Vector2(1, 0), 0.66)


func _quad(st: SurfaceTool, a: Vector3, b: Vector3, c: Vector3, d: Vector3,
		ua: Vector2, ub: Vector2, uc: Vector2, ud: Vector2,
		shade: float) -> void:
	var color := Color(shade, shade, shade)
	st.set_color(color)
	st.set_uv(ua)
	st.add_vertex(a)
	st.set_color(color)
	st.set_uv(ub)
	st.add_vertex(b)
	st.set_color(color)
	st.set_uv(uc)
	st.add_vertex(c)
	st.set_color(color)
	st.set_uv(ua)
	st.add_vertex(a)
	st.set_color(color)
	st.set_uv(uc)
	st.add_vertex(c)
	st.set_color(color)
	st.set_uv(ud)
	st.add_vertex(d)


## 和 _quad 一样,只是 4 个顶点各带一个 AO 系数(柔和光影用)。
func _quad_ao(st: SurfaceTool, a: Vector3, b: Vector3, c: Vector3, d: Vector3,
		ua: Vector2, ub: Vector2, uc: Vector2, ud: Vector2,
		shade: float, ao: Array) -> void:
	var f := [shade, shade, shade, shade]
	if ao.size() >= 4:
		f[0] = shade * float(ao[0])
		f[1] = shade * float(ao[1])
		f[2] = shade * float(ao[2])
		f[3] = shade * float(ao[3])
	st.set_color(Color(f[0], f[0], f[0]))
	st.set_uv(ua)
	st.add_vertex(a)
	st.set_color(Color(f[1], f[1], f[1]))
	st.set_uv(ub)
	st.add_vertex(b)
	st.set_color(Color(f[2], f[2], f[2]))
	st.set_uv(uc)
	st.add_vertex(c)
	st.set_color(Color(f[0], f[0], f[0]))
	st.set_uv(ua)
	st.add_vertex(a)
	st.set_color(Color(f[2], f[2], f[2]))
	st.set_uv(uc)
	st.add_vertex(c)
	st.set_color(Color(f[3], f[3], f[3]))
	st.set_uv(ud)
	st.add_vertex(d)
"""

# ── 天气:晴 / 雨 / 雪(同样存在 .blend 文本块里)──────────────────────
WEATHER_SCRIPT = """extends Node3D

## 天气循环:晴 → 雨 → 雪,每种天气有自己的持续时间(第两百一十四轮)。
## 晴天明显更长,雨雪来一阵就停,不会一直下;set_weather() 可以强制指定。

const NAMES := ["晴", "雨", "雪"]
## 每种天气的基准时长(秒),实际会在 ±15% 里浮动
const DURATIONS := [70.0, 26.0, 20.0]

var index := 0
var _timer := 0.0
var _duration := 70.0
var _env: Environment
var _rain: CPUParticles3D
var _rain_mesh: BoxMesh
var _snow_mesh: BoxMesh


func _ready() -> void:
	_build_rain()
	_apply(0)


func _physics_process(delta: float) -> void:
	_timer += delta
	if _timer >= _duration:
		set_weather(index + 1)


func set_weather(value: int) -> void:
	index = posmod(value, 3)
	_apply(index)


func _apply(value: int) -> void:
	_find_environment()
	_timer = 0.0
	_duration = DURATIONS[value] * randf_range(0.85, 1.15)
	match value:
		0:
			# 晴天也留一层很淡的"渲染距离雾"——原版就是靠它把地图边缘
			# 藏进天色里,不然 21×21 格的边界会硬切在蓝天上。
			if _env != null:
				_env.fog_enabled = true
				_env.fog_light_color = Color(0.66, 0.80, 0.98)
				_env.fog_density = 0.012
				_env.background_energy_multiplier = 1.0
			if _rain != null:
				_rain.emitting = false
		1:
			if _env != null:
				_env.fog_enabled = true
				_env.fog_light_color = Color(0.62, 0.68, 0.78)
				_env.fog_density = 0.03
				_env.background_energy_multiplier = 0.55
			if _rain != null:
				_rain.emitting = true
				_rain.mesh = _rain_mesh
				_rain.amount = 320
				_rain.color = Color(0.65, 0.75, 0.95)
				_set_alpha(0.55)
				_rain.initial_velocity_min = 14.0
				_rain.initial_velocity_max = 20.0
		2:
			if _env != null:
				_env.fog_enabled = true
				_env.fog_light_color = Color(0.88, 0.92, 0.98)
				_env.fog_density = 0.05
				_env.background_energy_multiplier = 0.75
			if _rain != null:
				_rain.emitting = true
				_rain.mesh = _snow_mesh
				_rain.amount = 220
				_rain.color = Color(1.0, 1.0, 1.0)
				_set_alpha(0.95)
				_rain.initial_velocity_min = 2.0
				_rain.initial_velocity_max = 4.0


func _find_environment() -> void:
	if _env != null:
		return
	for found in get_tree().root.find_children("*", "WorldEnvironment", true, false):
		var world := found as WorldEnvironment
		if world != null and world.environment != null:
			_env = world.environment
			return


func _set_alpha(value: float) -> void:
	var material := _rain.material_override as StandardMaterial3D
	if material != null:
		material.albedo_color.a = value


func _build_rain() -> void:
	_rain = CPUParticles3D.new()
	_rain.name = "天气粒子"
	_rain.amount = 320
	_rain.lifetime = 1.2
	_rain.emitting = false
	_rain.local_coords = false
	_rain.emission_shape = CPUParticles3D.EMISSION_SHAPE_BOX
	_rain.emission_box_extents = Vector3(9.0, 0.5, 9.0)
	_rain.direction = Vector3(0.0, -1.0, 0.0)
	_rain.spread = 6.0
	_rain.gravity = Vector3(0.0, -9.8, 0.0)
	_rain.initial_velocity_min = 14.0
	_rain.initial_velocity_max = 20.0
	# 雨滴做成细长条(以前是 5~9cm 的方块,凑近了像一颗颗大白骰子);
	# 雪用很小的方点。两者换天气时只换 mesh,不重建粒子。
	_rain_mesh = BoxMesh.new()
	(_rain_mesh as BoxMesh).size = Vector3(0.015, 0.34, 0.015)
	_snow_mesh = BoxMesh.new()
	(_snow_mesh as BoxMesh).size = Vector3(0.05, 0.05, 0.05)
	_rain.mesh = _rain_mesh
	_rain.scale_amount_min = 1.0
	_rain.scale_amount_max = 1.0
	var material := StandardMaterial3D.new()
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	material.albedo_color = Color(0.65, 0.75, 0.95, 0.55)
	_rain.material_override = material
	_rain.position = Vector3(0.0, 8.0, 0.0)
	add_child(_rain)
"""

# ── 羊:自己溜达的被动生物(同样存在 .blend 文本块里)────────────────
SHEEP_SCRIPT = """extends Node3D

## 羊:每 2~5 秒换个方向慢慢走,不追人、不造成伤害。
## 脚本挂在玩家身上(和无限地形同一套『脚本_加载』),控制场景里的羊节点。

const SPEED := 1.2

var _sheep: CharacterBody3D
var _dir := Vector3.ZERO
var _timer := 0.0


func _ready() -> void:
	_find_sheep()


func _physics_process(delta: float) -> void:
	if not is_instance_valid(_sheep):
		_find_sheep()
		return
	_timer -= delta
	if _timer <= 0.0:
		_timer = randf_range(2.0, 5.0)
		_dir = Vector3(randf_range(-1.0, 1.0), 0.0,
			randf_range(-1.0, 1.0)).normalized()
	_sheep.velocity.x = _dir.x * SPEED
	_sheep.velocity.z = _dir.z * SPEED
	_sheep.velocity.y -= 9.8 * delta
	var before := _sheep.global_position
	_sheep.move_and_slide()
	if _sheep.global_position.distance_to(before) < 0.001:
		# 撞墙 / 被方块挡住:马上换个方向,别顶着墙走好几秒
		_timer = 0.0
	if _dir.length() > 0.01:
		_sheep.look_at(_sheep.global_position + _dir, Vector3.UP)


func _find_sheep() -> void:
	_sheep = null
	var scene := get_tree().current_scene
	if scene == null:
		return
	for found in scene.find_children("羊*", "CharacterBody3D", true, false):
		_sheep = found as CharacterBody3D
		if _sheep != null:
			return
"""


# ── 猪(内容加法第一批):随机漫步 / 受击逃跑 / 打两下掉猪肉 ──────────
# 行为沿用羊那套"自己溜达"的写法(CharacterBody3D + move_and_slide + 定时换方向),
# 不接追人逻辑;被剑打一下掉头往远离玩家的方向跑几秒,血打空掉一块猪肉。
# 出生位置在运行时按出生点保护范围(半径 16)之外重新摆,不写死坐标。
PIG_SCRIPT = """extends Node3D

const SPEED := 1.15
const FLEE_SPEED := 3.1
const FLEE_TIME := 3.2
const MAX_HP := 2
const HIT_RANGE := 4.0
const SPAWN_MIN := 18.0
const SPAWN_MAX := 34.0

## [碰撞体, 血量, 当前方向, 换向倒计时, 逃跑剩余秒数]
var _pigs: Array = []
## 空种子算出来的世界种子就是 0,所以"没摆过"用 -1 当哨兵。
var _placed_seed := -1


func _ready() -> void:
	_collect()


func _physics_process(delta: float) -> void:
	var chunk := _find_chunk()
	if chunk == null:
		return
	_sync(chunk)
	if _pigs.is_empty():
		return
	var player := get_parent() as Node3D
	var here: Vector3 = player.global_position if player != null else Vector3.ZERO
	for entry in _pigs:
		var body = entry[0]
		if not is_instance_valid(body):
			continue
		var speed := SPEED
		var dir: Vector3 = entry[2]
		var flee := float(entry[4])
		if flee > 0.0:
			entry[4] = flee - delta
			speed = FLEE_SPEED
			dir = body.global_position - here
			dir.y = 0.0
			if dir.length() < 0.1:
				dir = Vector3(randf_range(-1.0, 1.0), 0.0,
					randf_range(-1.0, 1.0))
			dir = dir.normalized()
		else:
			entry[3] = float(entry[3]) - delta
			if float(entry[3]) <= 0.0:
				entry[3] = randf_range(2.0, 5.0)
				dir = Vector3(randf_range(-1.0, 1.0), 0.0,
					randf_range(-1.0, 1.0)).normalized()
			entry[2] = dir
		body.velocity.x = dir.x * speed
		body.velocity.z = dir.z * speed
		body.velocity.y -= 9.8 * delta
		var before: Vector3 = body.global_position
		body.move_and_slide()
		if flee <= 0.0 and body.global_position.distance_to(before) < 0.001:
			entry[3] = 0.0
		if dir.length() > 0.01:
			body.look_at(body.global_position + dir, Vector3.UP)


## 剑挥一下时由节点图调用:朝准星打一条 layer 8 的射线,打到猪才扣血。
## 打羊 / 打空气 / 挥空都什么也不做。
func pig_hit() -> void:
	var scene := get_tree().current_scene
	if scene == null:
		return
	var cams: Array = scene.find_children("第一人称相机", "Camera3D", true, false)
	if cams.is_empty():
		return
	var cam := cams[0] as Camera3D
	var from: Vector3 = cam.global_position
	var to: Vector3 = from - cam.global_transform.basis.z * HIT_RANGE
	var query := PhysicsRayQueryParameters3D.create(from, to, 8)
	var host := get_parent()
	if host is CollisionObject3D:
		query.exclude = [host]
	var hit := get_world_3d().direct_space_state.intersect_ray(query)
	if hit.is_empty():
		return
	var body = hit["collider"]
	if body == null or not String(body.name).begins_with("猪"):
		return
	for entry in _pigs:
		if entry[0] != body:
			continue
		entry[1] = int(entry[1]) - 1
		if int(entry[1]) > 0:
			# 受伤但没死:掉头往远离玩家的方向跑
			entry[4] = FLEE_TIME
			return
		_drop_pork()
		body.queue_free()
		_pigs.erase(entry)
		return


func _drop_pork() -> void:
	var host := get_parent()
	if host == null:
		return
	for child in host.get_children():
		if child.has_method("add_item"):
			child.call("add_item", "pork", 1)
			return


func _find_chunk() -> Node:
	var host := get_parent()
	if host == null:
		return null
	for child in host.get_children():
		if child.has_method("is_ready") and child.has_method("_height_at"):
			return child
	return null


func _collect() -> void:
	_pigs = []
	var scene := get_tree().current_scene
	if scene == null:
		return
	for found in scene.find_children("猪*", "CharacterBody3D", true, false):
		var body := found as CharacterBody3D
		if body == null:
			continue
		_pigs.append([body, MAX_HP, Vector3.ZERO, randf_range(0.5, 3.0), 0.0])


## 世界种子变了(新建世界 / 读别的档)或猪跑出近景(没有碰撞会掉下去)
## 就按玩家当前位置重摆一次;正常情况不会动。
func _sync(chunk: Node) -> void:
	if _pigs.is_empty():
		_collect()
	if _pigs.is_empty():
		return
	if not bool(chunk.call("is_ready")):
		return
	var near: Dictionary = chunk.get("_near_regions")
	var replan := int(chunk.get("_world_seed")) != _placed_seed
	for entry in _pigs:
		var body = entry[0]
		if not is_instance_valid(body):
			replan = true
			continue
		var cell := Vector2i(int(floor(body.global_position.x)),
			int(floor(body.global_position.z)))
		if not near.has(chunk.call("_region_of", cell)) \
				or body.global_position.y < -5.0:
			replan = true
	if not replan:
		return
	_placed_seed = int(chunk.get("_world_seed"))
	var spawn: Vector3 = chunk.call("choose_spawn")
	var player := get_parent() as Node3D
	# 落点以玩家为基准:近景碰撞只铺在玩家周围,离玩家 18~28 格,
	# 既在出生点保护圈(16)之外,又一定踩得到有碰撞的地。
	var base := spawn
	if player != null:
		base = player.global_position
	var angles := [0.0, 2.2]
	for index in range(_pigs.size()):
		var body = _pigs[index][0]
		if not is_instance_valid(body):
			continue
		var cell := _find_cell(chunk, base, near, float(angles[index % angles.size()]))
		body.global_position = Vector3(cell.x + 0.5,
			float(chunk.call("_height_at", cell)) + 0.9, cell.y + 0.5)
		body.velocity = Vector3.ZERO


## 玩家周围(近景区域内)找一块陆地:非水、不长树;找不到就换方向 / 退近。
func _find_cell(chunk: Node, base_point: Vector3, near: Dictionary,
		angle: float) -> Vector2i:
	var base := Vector2i(int(floor(base_point.x)), int(floor(base_point.z)))
	var tries := [angle, angle + 0.9, angle - 0.9, angle + 1.8, angle - 1.8,
		angle + 2.7, angle - 2.7]
	var dist := SPAWN_MAX
	while dist >= SPAWN_MIN:
		for offset in tries:
			var cell := base + Vector2i(int(round(cos(offset) * dist)),
				int(round(sin(offset) * dist)))
			if not near.has(chunk.call("_region_of", cell)):
				continue
			if int(chunk.call("_height_at", cell)) < 32:
				continue
			if bool(chunk.call("_has_tree", cell)):
				continue
			return cell
		dist -= 2.0
	return base + Vector2i(int(round(cos(angle) * SPAWN_MIN)),
		int(round(sin(angle) * SPAWN_MIN)))
"""


# ── 生物模型跟随:碰撞体在 Colliders/,看得见的网格在 World.glb 里 ─────
# 导出后"怪物1"这个名字在树里有两份:一份是 Colliders 下的隐形碰撞体,
# 一份是 World/Monsters 下的网格。节点图『移动_朝向目标』推的是碰撞体,
# 所以网格必须每帧跟过去 —— 否则只有隐形碰撞体在追人(用户反馈)。
CREATURE_SCRIPT = """extends Node3D

## 怪物 / 羊的视觉跟随 + 走路起伏(第两百零三轮)。
## 网格和碰撞体在导出后的树里是两个分支(World/Monsters/<名字> 是网格),
## 这里每帧把网格的 global_transform 对齐到碰撞体,再叠一点上下颠 + 左右摇。
## 僵尸是合并成一整块的网格,掰不了腿,起伏是"方块小怪"最像走路的做法。

const BOB_HEIGHT := 0.055
const BOB_SPEED := 11.0
const SWAY := 0.045

var _creatures: Array = []


func _ready() -> void:
	_collect()


func _physics_process(delta: float) -> void:
	if _creatures.is_empty():
		_collect()
		if _creatures.is_empty():
			return
	for entry in _creatures:
		# 这里故意不写类型标注:碰撞体被剑 queue_free 之后,把失效实例赋给
		# `Node3D` 类型变量会刷 "assign invalid previously freed instance"。
		var body = entry[0]
		var visual = entry[1]
		if not is_instance_valid(body):
			# 被剑打掉:网格也别留在原地站着
			if is_instance_valid(visual):
				visual.queue_free()
			_collect()
			return
		if not is_instance_valid(visual):
			_collect()
			return
		_drive(body, visual, entry, delta)


func _collect() -> void:
	_creatures = []
	var scene := get_tree().current_scene
	if scene == null:
		return
	for found in scene.find_children("*", "CharacterBody3D", true, false):
		var body := found as Node3D
		if body == null or body.is_queued_for_deletion():
			continue
		var label := String(body.name)
		if not (label.begins_with("怪物") or label.begins_with("羊")
				or label.begins_with("猪")):
			continue
		var visual := _visual(body, scene)
		if visual == null:
			continue
		# [碰撞体, 网格, 相位, 起伏高度, 上一帧位置]
		_creatures.append([body, visual, 0.0, 0.0, body.global_position])


func _visual(body: Node3D, scene: Node) -> Node3D:
	var holder := scene.get_node_or_null("Monsters")
	if holder != null:
		var found := holder.get_node_or_null(String(body.name))
		if found is Node3D:
			return found as Node3D
	for node in scene.find_children(String(body.name), "MeshInstance3D", true, false):
		return node as Node3D
	return null


func _drive(body: Node3D, visual: Node3D, entry: Array, delta: float) -> void:
	var here := body.global_position
	var last: Vector3 = entry[4]
	var walked := Vector2(here.x - last.x, here.z - last.z).length()
	entry[4] = here
	var phase: float = entry[2]
	var bob: float = entry[3]
	if walked > 0.0005:
		phase = fmod(phase + delta * BOB_SPEED, TAU)
		bob = absf(sin(phase)) * BOB_HEIGHT
	else:
		# 停住时把起伏收回去,别原地像心跳一样抖
		bob = move_toward(bob, 0.0, delta * 0.6)
		phase = 0.0
	entry[2] = phase
	entry[3] = bob
	var xf := body.global_transform
	xf.origin.y += bob
	if walked > 0.0005:
		var forward := xf.basis.z.normalized()
		if forward.length() > 0.5:
			xf.basis = xf.basis.rotated(forward, sin(phase) * SWAY)
	visual.global_transform = xf
"""


# ── 静态水(M2b):游泳 / 缓沉 / 溺水 ─────────────────────────────
SWIM_SCRIPT = """extends Node

## 海平面 32 的静态水:能游泳、会溺水(Q13 只做静态水,不做流动 / 无限水源)。
## 挂在 Player 下面,物理优先级 100 —— 在玩家自己的移动脚本之后跑,
## 改的是下一帧 move_and_slide 用的速度:
##   * 脚底在水里按空格 -> 上浮;
##   * 松手时把下沉速度限制在 SINK_SPEED,掉进水里是"缓缓下沉"不是直坠;
##   * 头(眼睛高度)没在水里累计 6 秒后,每秒扣 2 点生命(创造模式不扣)。

const SWIM_UP := 4.6
const SINK_SPEED := -1.2
const BREATH_SECONDS := 6.0
const DROWN_INTERVAL := 1.0
const DROWN_DAMAGE := 2.0

var _chunk: Node = null
var _breath := 0.0
var _drown_timer := 0.0


func _ready() -> void:
	process_physics_priority = 100


func _physics_process(delta: float) -> void:
	var body := get_parent()
	if not (body is CharacterBody3D):
		return
	var chunk := _find_chunk()
	if chunk == null:
		return
	var pos: Vector3 = (body as Node3D).global_position
	# 只认"脚真的低于水面":原点就是脚底,站在海平面 32 上不算入水,
	# 必须能继续往下沉;脚一进到 32 以下才开始上浮 / 缓沉。
	if not bool(chunk.call("is_underwater_point", pos)):
		_breath = 0.0
		_drown_timer = 0.0
		return
	var velocity: Vector3 = (body as CharacterBody3D).velocity
	if Input.is_action_pressed("jump"):
		velocity.y = SWIM_UP
	elif velocity.y < SINK_SPEED:
		velocity.y = SINK_SPEED
	(body as CharacterBody3D).velocity = velocity
	# 头没入水里才开始憋气
	if not bool(chunk.call("is_underwater_point",
			pos + Vector3(0.0, 1.5, 0.0))):
		_breath = 0.0
		_drown_timer = 0.0
		return
	_breath += delta
	if _breath < BREATH_SECONDS or bool(body.get("创造模式")):
		return
	_drown_timer += delta
	while _drown_timer >= DROWN_INTERVAL:
		_drown_timer -= DROWN_INTERVAL
		_damage(DROWN_DAMAGE)


func _find_chunk() -> Node:
	if _chunk != null and is_instance_valid(_chunk):
		return _chunk
	var host := get_parent()
	if host == null:
		return null
	for child in host.get_children():
		if child.has_method("is_underwater_point"):
			_chunk = child
			return _chunk
	return null


## 走玩家脚本自己的受伤入口:那边会弹提示、归零后回出生点。
func _damage(amount: float) -> void:
	var host := get_parent()
	if host == null:
		return
	for child in host.get_children():
		if child.has_method("_hurt"):
			child.call("_hurt", "溺水", amount)
			return
	host.set("生命", maxf(0.0, float(host.get("生命")) - amount))
"""


# ── 输入保护:失焦 / 暂停 / 背包 / 截图 后清干净按键状态 ─────────────
INPUT_GUARD_SCRIPT = """extends Node

## 窗口失焦(切截图工具 / 切浏览器)时,系统的"松开"事件可能收不到:
## 移动是 Input.get_action_strength 轮询、挖掘是鼠标左键轮询、I 键是
## Input.is_physical_key_pressed 轮询 —— 只要状态还挂着,人物就会自己走、
## 一直挖。这里在失焦 / 暂停 / 打开背包(暂停)时统一把状态清干净。
##
## 做法不是"改判定",而是把缺失的松开事件补进来:
##   * Input.action_release 清 InputMap 里的动作(移动 / 跳跃);
##   * 补发物理键的 released 事件,清 is_physical_key_pressed(I 键);
##   * 补发鼠标键的 released 事件,清 is_mouse_button_pressed(挖掘)。

const KEYS := [KEY_W, KEY_A, KEY_S, KEY_D, KEY_SPACE, KEY_I, KEY_G, KEY_V,
	KEY_ESCAPE, KEY_E, KEY_Q, KEY_F, KEY_R, KEY_TAB, KEY_SHIFT,
	KEY_1, KEY_2, KEY_3, KEY_4, KEY_5, KEY_6, KEY_7, KEY_8, KEY_9]
const BUTTONS := [MOUSE_BUTTON_LEFT, MOUSE_BUTTON_RIGHT, MOUSE_BUTTON_MIDDLE,
	MOUSE_BUTTON_WHEEL_UP, MOUSE_BUTTON_WHEEL_DOWN]

var _was_paused := false


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	_was_paused = get_tree().paused
	var window := get_window()
	if window != null and not window.focus_exited.is_connected(clear_all):
		window.focus_exited.connect(clear_all)


func _notification(what: int) -> void:
	# 应用失焦 / 窗口失焦(切截图工具、Alt+Tab)都当"松开所有键"
	if what == NOTIFICATION_APPLICATION_FOCUS_OUT \
			or what == NOTIFICATION_WM_WINDOW_FOCUS_OUT:
		clear_all()


func _process(_delta: float) -> void:
	# 暂停 / 打开背包 / 开箱 / 合成 都会把树暂停:进入和离开暂停各清一次,
	# 免得"进界面前按住的键"在关界面后继续生效。
	var now := get_tree().paused
	if now != _was_paused:
		_was_paused = now
		clear_all()


## 统一入口:补发所有可能卡住的"松开"事件。
func clear_all() -> void:
	for action in InputMap.get_actions():
		Input.action_release(action)
	for key in KEYS:
		var key_event := InputEventKey.new()
		key_event.physical_keycode = key
		key_event.pressed = false
		Input.parse_input_event(key_event)
	for button in BUTTONS:
		var mouse_event := InputEventMouseButton.new()
		mouse_event.button_index = button
		mouse_event.pressed = false
		Input.parse_input_event(mouse_event)
"""


# ── 命名存档:人物 + 世界改动(同样存在 .blend 文本块里)────────────────
SAVE_SCRIPT = """extends Node

## 命名存档(第两百零五轮):人物 + 世界改动 -> user://saves/<名字>.json。
## 开始菜单的『读取存档』、暂停菜单的『保存游戏』都调这里;
## 列表 / 输入框 / 弹窗全部在运行时用代码搭(节点图做"无限列表"太笨重)。
##
## 存档内容:
##   player: 坐标 / 生命 / 手持序号 / 创造模式 / 第三人称
##   world : 挖掉的方块 / 放下的方块 / 挖掉的树(由『我的世界区块』提供)

const SAVE_DIR := "user://saves"
## 旧档高度迁移(M2a §11.10):旧版地表上限是 24,旧存档没有 world_height 字段。
const LEGACY_WORLD_HEIGHT := 24
## 迁移报告归档目录:旧高度 / 新高度 / 丢弃物品 / 重置改动 / 耗时
const MIGRATION_DIR := "user://.audit/minecraft/migrations"
const ITEM_NAMES := ["草方块", "石头方块", "沙子方块", "木头方块",
	"树叶方块", "苹果", "剑", "空", "空"]

var _layer: CanvasLayer
var _root: Control
var _load_screen: Control
var _save_screen: Control
var _create_screen: Control
var _menu_bg: Control
var _menu_clouds: Array = []
var _list: VBoxContainer
var _name_input: LineEdit
var _world_name_input: LineEdit
var _seed_input: LineEdit
var _terrain_option: OptionButton
var _size_option: OptionButton
var _vegetation_option: OptionButton
var _time_option: OptionButton
var _radius_option: OptionButton
var _toast: Label
var _toast_time := 0.0
var _loading_screen: Control
var _loading_bar: ProgressBar
var _loading_label: Label
var _loading_percent: Label
## 最近一次"点击开始/读档 -> 放开操作"的秒数(M2a 第三段指标 world_load_s)
var _world_load_s := 0.0
var _last_name := "新的世界"
var _world_name := "新的世界"
var _menu_was_visible := false
## 最近一次进入世界时设置的鼠标模式:无头探针读不到真实光标状态,
## 用它回归『进世界必须把鼠标锁回去』(Input.MOUSE_MODE_CAPTURED = 2)。
var _last_mouse_mode := -1
## 生存 HUD(血条 + 10 格饥饿条):原版位置在屏幕底部正中、快捷栏上方。
## 血条 / 饥饿条由这里画,不再用节点图左上角那套。
var _hud: Control
var _hp_fill: ColorRect
var _old_hud_nodes: Array = []
## 摔落 / 掉出世界:记录离地后的最高点,落地时按落差扣血。
var _fall_peak := 0.0
var _airborne := false
var _skin_ready := false
## 分区帧时统计(第两百一十六轮):进世界 / 静止 / 走动 / 开界面四种情况
## 分开记帧间隔和 process+physics 的 CPU 时间。只记录不显示,跑图 / 真机用
## perf_report() 取出来 —— 先有数据再决定调什么参数,不凭感觉改。
var _perf_stats: Dictionary = {}
var _perf_last_us := 0
## 旧档迁移:读档时先解析到 _pending_load,弹独立模态等玩家确认。
## 确认前不动当前世界、不写盘;取消就回到菜单。
var _migration_screen: Control
var _migration_label: Label
var _pending_load: Dictionary = {}
var _pending_file := ""
var _migration_forced := false


func _ready() -> void:
	# 菜单是暂停状态:脚本和它搭的界面都要能在暂停时响应
	process_mode = Node.PROCESS_MODE_ALWAYS
	_build_ui()
	_build_survival_hud()
	_apply_player_skin.call_deferred()


## 玩家皮肤:模型 UV 按原版 64×64 排好,这里只把贴图挂上去。
## 优先读 user://skin/ 下的同名文件 —— 换皮肤 = 往那里放一张 64×64 PNG,
## 不用重新导出、也不用改生成器。
## 返回 true 表示贴图已经挂上。刚进场景时 res:// 的图片可能还没导入完,
## `_refresh_hud` 会每帧重试一次,直到成功为止(只试到成功,不重复建材质)。
func _apply_player_skin() -> bool:
	var host := get_parent()
	if host == null:
		return false
	var model := (host as Node).get_node_or_null("Model")
	if model == null:
		return false
	var applied := false
	for found in (model as Node).find_children("*", "MeshInstance3D", true, false):
		var mesh_node := found as MeshInstance3D
		if mesh_node == null or mesh_node.mesh == null:
			continue
		var skin_path := "res://art/skin/mc_skin.png"
		if FileAccess.file_exists("user://skin/mc_skin.png"):
			skin_path = "user://skin/mc_skin.png"
		if ResourceLoader.exists(skin_path):
			var skin := StandardMaterial3D.new()
			skin.albedo_texture = load(skin_path)
			skin.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
			skin.cull_mode = BaseMaterial3D.CULL_DISABLED
			skin.roughness = 1.0
			mesh_node.set_surface_override_material(0, skin)
			applied = true
		if mesh_node.mesh.get_surface_count() < 2:
			continue
		var outer_path := "res://art/skin/mc_skin_outer.png"
		if FileAccess.file_exists("user://skin/mc_skin_outer.png"):
			outer_path = "user://skin/mc_skin_outer.png"
		if ResourceLoader.exists(outer_path):
			var outer := StandardMaterial3D.new()
			outer.albedo_texture = load(outer_path)
			outer.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
			outer.cull_mode = BaseMaterial3D.CULL_DISABLED
			outer.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
			outer.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
			mesh_node.set_surface_override_material(1, outer)
	return applied


func _process(delta: float) -> void:
	_perf_tick()
	if _toast_time > 0.0:
		_toast_time -= delta
		if _toast_time <= 0.0:
			_toast.visible = false
	# 开始菜单的方块云一直往右飘,出右边界就从左边再进来(设计稿的 drift 动画)
	if _menu_bg != null and _menu_bg.visible:
		var span := float(get_viewport().get_visible_rect().size.x)
		for cloud in _menu_clouds:
			if not (cloud is Control):
				continue
			var control := cloud as Control
			control.position.x += float(control.get_meta("speed", 12.0)) * delta
			if control.position.x > span + 40.0:
				control.position.x = -control.size.x - 40.0
	# 开始菜单开着的时候用背景板盖住 3D 画面(怪物还在动,但看不见);
	# 顺手把游戏内 HUD 收起来,菜单上只留菜单自己的按钮。
	var scene := get_tree().current_scene
	if scene != null and _menu_bg != null:
		var menu := scene.find_child("世界菜单", true, false)
		var menu_visible := menu is CanvasItem and (menu as CanvasItem).visible
		if menu_visible != _menu_was_visible:
			_menu_was_visible = menu_visible
			_menu_bg.visible = menu_visible
			for node_name in ["物品栏", "准星横", "准星竖", "手持文本"]:
				var node := scene.find_child(node_name, true, false)
				if node is CanvasItem:
					(node as CanvasItem).visible = not menu_visible
			# 菜单每次露面都按存档状态收敛一次(有档:『继续游戏』可点;
			# 没档:主按钮置灰并提示先『新建世界』,不落进新建流程)。
			if menu_visible:
				_refresh_menu()
	# 所有窗口都收起、游戏也在跑:鼠标必须锁回去。
	# 暂停菜单 / 读档 / 保存 / 新建 / 确认小窗都走这一条兜底路径 —— 只修
	# 进世界那一处的话,从小窗点「返回」再关暂停菜单,光标还是会留在画面里。
	if not _window_open() and not get_tree().paused:
		if Input.mouse_mode != Input.MOUSE_MODE_CAPTURED:
			Input.mouse_mode = Input.MOUSE_MODE_CAPTURED
		_last_mouse_mode = Input.MOUSE_MODE_CAPTURED
	_refresh_hud()


## 当前处在哪一种情形:加载屏盖着 = 进世界;暂停 / 面板开着 = 开界面;
## 水平速度 > 0.4 = 走动;其余 = 静止。
func _perf_zone() -> String:
	if _loading_screen != null and _loading_screen.visible:
		return "进世界"
	if get_tree().paused:
		return "开界面"
	var host := get_parent() as CharacterBody3D
	if host != null:
		if Vector2(host.velocity.x, host.velocity.z).length() > 0.4:
			return "走动"
	return "静止"


func _perf_tick() -> void:
	var now := Time.get_ticks_usec()
	if _perf_last_us <= 0:
		_perf_last_us = now
		return
	var frame_ms := float(now - _perf_last_us) / 1000.0
	_perf_last_us = now
	# 帧间隔是唯一可信的"卡不卡"指标。引擎的 TIME_PROCESS / TIME_PHYSICS_PROCESS
	# 在这台机器上给出的数值和帧间隔自相矛盾(0.22s vs 28ms),不再记录,免得误读。
	# 铺地形这一项是自己量的,单位明确(微秒 -> 毫秒),比引擎监控可信。
	# 预铺是加载屏里按帧做的(每帧有预算),耗时记在 _prebuild_us_last,
	# 读到就清掉,避免同一帧被 _step_build 的 0 覆盖后看不出来。
	var build_ms := 0.0
	var chunk := _chunk()
	if chunk != null:
		build_ms = float(chunk.get("_build_us_last")) / 1000.0
		var prebuild_us := int(chunk.get("_prebuild_us_last"))
		if prebuild_us > 0:
			build_ms += float(prebuild_us) / 1000.0
			chunk.set("_prebuild_us_last", 0)
	var zone := _perf_zone()
	if not _perf_stats.has(zone):
		_perf_stats[zone] = {"frames": 0, "total": 0.0, "max": 0.0,
			"over": 0, "build": 0.0, "build_max": 0.0, "build_frames": 0,
			"samples": [], "build_samples": []}
	var entry: Dictionary = _perf_stats[zone]
	entry["frames"] = int(entry["frames"]) + 1
	entry["total"] = float(entry["total"]) + frame_ms
	entry["max"] = maxf(float(entry["max"]), frame_ms)
	entry["build"] = float(entry["build"]) + build_ms
	entry["build_max"] = maxf(float(entry["build_max"]), build_ms)
	if (entry["samples"] as Array).size() < 4096:
		(entry["samples"] as Array).append(frame_ms)
		(entry["build_samples"] as Array).append(build_ms)
	if build_ms > 0.2:
		entry["build_frames"] = int(entry["build_frames"]) + 1
	if frame_ms > 33.4:
		entry["over"] = int(entry["over"]) + 1


## 分区统计快照:{情形: {frames, avg_ms, p99_ms, max_ms, over30(>33ms 帧数),
## build_ms, build_p99_ms, build_max_ms, build_frames}}
func perf_report() -> Dictionary:
	var out: Dictionary = {}
	for zone in _perf_stats.keys():
		var entry: Dictionary = _perf_stats[zone]
		var frames := maxi(1, int(entry["frames"]))
		out[zone] = {
			"frames": int(entry["frames"]),
			"avg_ms": float(entry["total"]) / float(frames),
			"p99_ms": _sample_percentile(entry["samples"], 0.99),
			"max_ms": float(entry["max"]),
			"over30": int(entry["over"]),
			"build_ms": float(entry["build"]) / float(frames),
			"build_p99_ms": _sample_percentile(entry["build_samples"], 0.99),
			"build_max_ms": float(entry["build_max"]),
			"build_frames": int(entry["build_frames"]),
		}
	return out


func _sample_percentile(values: Array, ratio: float) -> float:
	if values.is_empty():
		return 0.0
	var sorted := values.duplicate()
	sorted.sort()
	return float(sorted[clampi(int(round(float(sorted.size() - 1) * ratio)),
		0, sorted.size() - 1)])


func perf_reset() -> void:
	_perf_stats = {}
	_perf_last_us = 0


## 生存 HUD 每帧刷一次:创造模式隐藏,生命值决定红条长度。
## 饥饿扣减规则还没做(留到下一阶段),这轮先固定 10 格满。
func _refresh_hud() -> void:
	if _hud == null:
		return
	var host := get_parent()
	if host == null:
		return
	var creative := bool(host.get("创造模式"))
	_hud.visible = not creative and not _menu_was_visible
	var hp := clampf(float(host.get("生命")), 0.0, 100.0)
	_hp_fill.size.x = 206.0 * hp / 100.0
	_hide_graph_health()
	_keep_player_model_hidden()
	# 皮肤贴图晚到(首次导入 / 读档重建场景)时每帧重试一次,直到挂上为止。
	if not _skin_ready:
		_skin_ready = _apply_player_skin()


## 第一人称下玩家模型必须藏起来(用户又看到蓝色模型露在画面里)。
## 不再依赖节点图那三条"切视角 / 读档 / 关菜单"的隐藏路径 —— 直接看
## 当前是哪台相机:只有『第三人称相机』在用时才显示,其余(含加载中
## 相机还没就绪)一律藏。任何一条路径漏了都不会再露脸。
func _keep_player_model_hidden() -> void:
	var host := get_parent()
	if host == null:
		return
	var model := (host as Node).get_node_or_null("Model")
	if model is Node3D:
		var camera := get_viewport().get_camera_3d()
		var close := false
		if camera != null:
			# 第三人称相机被墙 / 树挤到贴近角色时也别露出模型,
			# 否则镜头钻进盒子里就是一片蓝。
			close = camera.global_position.distance_to(
				(host as Node3D).global_position + Vector3(0.0, 1.2, 0.0)) < 0.9
		(model as Node3D).visible = camera != null \
			and camera.name == "第三人称相机" and not close


## 节点图左上角原来那套『生命 100 + 红条』不删连线的做法:只把它藏起来,
## 免得同一份数值在屏幕上出现两次。节点图那套 UI 是世界开始跑之后才建出来的
## (菜单期间 Player 暂停,不跑物理帧),所以这里每帧兜一次、找到后缓存。
func _hide_graph_health() -> void:
	var scene := get_tree().current_scene
	if scene == null:
		return
	var need_scan := _old_hud_nodes.size() < 2
	for node in _old_hud_nodes:
		if not is_instance_valid(node):
			need_scan = true
	if need_scan:
		_old_hud_nodes.clear()
		for node_name in ["生命文本", "生命条"]:
			var found := scene.find_child(node_name, true, false)
			if found is CanvasItem:
				_old_hud_nodes.append(found)
	for node in _old_hud_nodes:
		if is_instance_valid(node):
			(node as CanvasItem).visible = false


## 底部成对的血条 / 饥饿条(原版位置):左血右饿,都在快捷栏上方。
func _build_survival_hud() -> void:
	var layer := CanvasLayer.new()
	layer.name = "生存HUD层"
	layer.layer = 24
	layer.process_mode = Node.PROCESS_MODE_ALWAYS
	add_child(layer)
	_hud = Control.new()
	_hud.name = "生存HUD"
	_hud.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_hud.set_anchors_preset(Control.PRESET_CENTER_BOTTOM)
	_hud.offset_left = -260.0
	_hud.offset_right = 260.0
	_hud.offset_top = -116.0
	_hud.offset_bottom = -90.0
	layer.add_child(_hud)
	var hp_bg := ColorRect.new()
	hp_bg.name = "血条底"
	hp_bg.color = Color(0.04, 0.05, 0.07, 0.72)
	hp_bg.position = Vector2(0.0, 5.0)
	hp_bg.size = Vector2(210.0, 14.0)
	hp_bg.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_hud.add_child(hp_bg)
	_hp_fill = ColorRect.new()
	_hp_fill.name = "血条"
	_hp_fill.color = Color(0.82, 0.14, 0.16)
	_hp_fill.position = Vector2(2.0, 7.0)
	_hp_fill.size = Vector2(206.0, 10.0)
	_hp_fill.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_hud.add_child(_hp_fill)
	# 饥饿条:10 格,右半截(原版是鸡腿图形;这里先做 10 格的表现)
	var food_bg := ColorRect.new()
	food_bg.name = "饥饿条底"
	food_bg.color = Color(0.04, 0.05, 0.07, 0.72)
	food_bg.position = Vector2(310.0, 5.0)
	food_bg.size = Vector2(210.0, 14.0)
	food_bg.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_hud.add_child(food_bg)
	for index in range(10):
		var cell := ColorRect.new()
		cell.name = "饥饿格%d" % (index + 1)
		cell.color = Color(0.86, 0.48, 0.12)
		cell.position = Vector2(312.0 + float(index) * 20.6, 7.0)
		cell.size = Vector2(18.0, 10.0)
		cell.mouse_filter = Control.MOUSE_FILTER_IGNORE
		_hud.add_child(cell)
	_hud.visible = false


## 摔落伤害 / 掉出世界重生(用户实测:"掉下去不扣血也不重生")。
## 落差 = 离地后的最高点 - 落地点:跳起来落回原地不算摔。
## 原版是超过 3 格开始掉血,这里同阈值,每多 1 格 -10 生命。
func _physics_process(_delta: float) -> void:
	var host := get_parent() as CharacterBody3D
	if host == null or get_tree().paused:
		return
	var y := host.global_position.y
	if y < -16.0:
		_respawn("掉出了世界")
		return
	if host.is_on_floor():
		if _airborne:
			var drop := _fall_peak - y
			_airborne = false
			if drop > 3.0:
				_hurt("摔落", (drop - 3.0) * 10.0)
		_fall_peak = y
	else:
		_airborne = true
		_fall_peak = maxf(_fall_peak, y)


func _hurt(cause: String, amount: float) -> void:
	var host := get_parent()
	if host == null or bool(host.get("创造模式")):
		return
	var hp := maxf(0.0, float(host.get("生命")) - amount)
	host.set("生命", hp)
	_show_toast("%s -%d 生命" % [cause, int(round(amount))])
	if hp <= 0.0:
		_respawn("你倒下了")


## 回到出生点:坐标由区块脚本按地形挑好,存在玩家变量里(第两百一十六轮)。
## 暂停菜单的『回到出生点』也调这里,不再用固定的 (0.5,1,0.5)。
func respawn_player() -> void:
	var host := get_parent() as CharacterBody3D
	if host == null:
		return
	host.velocity = Vector3.ZERO
	_move_player_to(Vector3(float(host.get("出生点x")),
		float(host.get("出生点y")), float(host.get("出生点z"))))
	_fall_peak = host.global_position.y
	_airborne = false


## 传送玩家时把相机一起平移同样的距离(第两百一十六轮):
## 『相机_跟随』是"第一次跑物理帧时记住相机相对玩家的偏移",游戏开局是暂停的,
## 出生点又在解除暂停前就被改成挑好的位置 —— 如果只挪玩家不挪相机,
## 记下来的偏移就会变成"出生点 - 旧相机位置",之后相机永远歪在一边。
func _move_player_to(point: Vector3) -> void:
	var host := get_parent() as Node3D
	if host == null:
		return
	var delta := point - host.global_position
	host.global_position = point
	var scene := get_tree().current_scene
	if scene == null:
		return
	for cam_name in ["第一人称相机", "第三人称相机"]:
		for cam in scene.find_children(cam_name, "Camera3D", true, false):
			(cam as Node3D).global_position += delta


func _respawn(reason: String) -> void:
	var host := get_parent() as CharacterBody3D
	if host == null:
		return
	host.set("生命", 100.0)
	respawn_player()
	_show_toast("%s,已回到出生点" % reason)


## 放置被占用时给一句反馈(节点图在『位置空吗』的"否"出口调它)
func toast_blocked() -> void:
	# 右键其实是点在箱子上(已经开箱):不要弹"这里已经有方块了"
	var host := get_parent()
	if host != null:
		for child in host.get_children():
			if child.has_method("try_open_chest") \
					and bool(child.get("_chest_open")):
				return
	_show_toast("这里已经有方块了")


## 有没有任何"要鼠标点"的窗口开着(菜单 / 面板 / 加载页)。
func _window_open() -> bool:
	for screen in [_load_screen, _save_screen, _create_screen, _loading_screen,
			_migration_screen]:
		if screen != null and screen.visible:
			return true
	var scene := get_tree().current_scene
	if scene != null:
		for menu_name in ["世界菜单", "暂停菜单"]:
			var menu := scene.find_child(menu_name, true, false)
			if menu is CanvasItem and (menu as CanvasItem).visible:
				return true
	return false


func _unhandled_input(event: InputEvent) -> void:
	if not event.is_action_pressed("ui_cancel"):
		return
	# 迁移模态是"确认 / 取消"二选一:ESC 等同取消,不写盘、不动世界
	if _migration_screen != null and _migration_screen.visible:
		_on_migration_cancel()
		get_viewport().set_input_as_handled()
		return
	if (_load_screen != null and _load_screen.visible) \
			or (_save_screen != null and _save_screen.visible):
		close_panels()
		get_viewport().set_input_as_handled()


# ── 界面(运行时搭:遮罩 + 居中面板 + 滚动列表)──────────────────────
func _build_ui() -> void:
	_layer = CanvasLayer.new()
	_layer.name = "存档层"
	_layer.layer = 30
	_layer.process_mode = Node.PROCESS_MODE_ALWAYS
	add_child(_layer)
	_root = Control.new()
	_root.name = "存档界面"
	_root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_layer.add_child(_root)
	_build_menu_bg()
	_build_load()
	_build_save()
	_build_create()
	_build_loading()
	_build_migration()
	_toast = Label.new()
	_toast.name = "存档提示"
	_toast.visible = false
	_toast.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_toast.add_theme_font_size_override("font_size", 20)
	_toast.add_theme_color_override("font_color", Color(0.65, 1.0, 0.8))
	_toast.set_anchors_preset(Control.PRESET_CENTER_BOTTOM)
	_toast.offset_left = -280.0
	_toast.offset_right = 280.0
	_toast.offset_top = -120.0
	_toast.offset_bottom = -84.0
	_root.add_child(_toast)


## 开始菜单的背景(设计稿那套):像素风景图 + 暗罩 + 会飘的方块云。
## 全部塞进一个全屏 Control:菜单一露整层显示,一收起整层隐藏。
func _build_menu_bg() -> void:
	var layer := CanvasLayer.new()
	layer.name = "菜单背景层"
	layer.layer = 5
	layer.process_mode = Node.PROCESS_MODE_ALWAYS
	add_child(layer)
	_menu_bg = Control.new()
	_menu_bg.name = "菜单背景"
	_menu_bg.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_menu_bg.mouse_filter = Control.MOUSE_FILTER_IGNORE
	layer.add_child(_menu_bg)
	var picture := TextureRect.new()
	picture.name = "风景"
	picture.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	picture.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	picture.stretch_mode = TextureRect.STRETCH_SCALE
	picture.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	picture.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var path := "res://art/mc_menu_bg.png"
	if ResourceLoader.exists(path):
		picture.texture = load(path)
	_menu_bg.add_child(picture)
	# 暗罩(设计稿 .scene-dim):压住风景,面板和字才浮得起来
	# 注意顺序:风景 → 云 → 暗罩,云和背景图一起被压暗(见下面)
	var dim := ColorRect.new()
	dim.name = "暗罩"
	dim.color = Color(0.024, 0.035, 0.063, 0.44)
	dim.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	dim.mouse_filter = Control.MOUSE_FILTER_IGNORE
	# 会飘的方块云:高度 / 速度错开,出右边就从左边再进来。
	# 必须画在暗罩**下面**:设计稿里云属于风景层,要和背景图一起被压暗;
	# 画在暗罩上面会白得发亮,和背景里原有的云明显两个色。
	var cloud_layer := Control.new()
	cloud_layer.name = "云层"
	cloud_layer.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	cloud_layer.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_menu_bg.add_child(cloud_layer)
	var cloud_path := "res://art/mc_cloud.png"
	var cloud_texture: Texture2D = null
	if ResourceLoader.exists(cloud_path):
		cloud_texture = load(cloud_path)
	var spots := [Vector2(40.0, 46.0), Vector2(430.0, 26.0),
		Vector2(760.0, 74.0), Vector2(1040.0, 40.0), Vector2(1240.0, 96.0)]
	var speeds := [18.0, 11.0, 24.0, 14.0, 8.0]
	for index in range(spots.size()):
		var cloud := TextureRect.new()
		cloud.name = "云%d" % (index + 1)
		cloud.texture = cloud_texture
		cloud.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
		cloud.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		cloud.stretch_mode = TextureRect.STRETCH_SCALE
		cloud.mouse_filter = Control.MOUSE_FILTER_IGNORE
		cloud.size = Vector2(120.0, 36.0)
		cloud.position = spots[index]
		cloud.set_meta("speed", speeds[index])
		cloud_layer.add_child(cloud)
		_menu_clouds.append(cloud)
	_menu_bg.add_child(dim)


func _make_screen(node_name: String) -> Control:
	var screen := Control.new()
	screen.name = node_name
	screen.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	screen.visible = false
	_root.add_child(screen)
	var shade := ColorRect.new()
	shade.color = Color(0.0, 0.0, 0.0, 0.45)
	shade.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	shade.mouse_filter = Control.MOUSE_FILTER_STOP
	screen.add_child(shade)
	return screen


func _center_panel(parent: Control, node_name: String,
		size: Vector2) -> Panel:
	var panel := Panel.new()
	panel.name = node_name
	panel.set_anchors_preset(Control.PRESET_CENTER)
	panel.offset_left = -size.x * 0.5
	panel.offset_right = size.x * 0.5
	panel.offset_top = -size.y * 0.5
	panel.offset_bottom = size.y * 0.5
	var style := StyleBoxFlat.new()
	style.bg_color = Color(0.05, 0.06, 0.09, 0.94)
	style.corner_radius_top_left = 16
	style.corner_radius_top_right = 16
	style.corner_radius_bottom_left = 16
	style.corner_radius_bottom_right = 16
	style.border_width_left = 2
	style.border_width_right = 2
	style.border_width_top = 2
	style.border_width_bottom = 2
	style.border_color = Color(0.55, 1.0, 0.75, 0.30)
	panel.add_theme_stylebox_override("panel", style)
	parent.add_child(panel)
	return panel


func _label(parent: Control, node_name: String, text: String, size: int,
		pos: Vector2, box: Vector2, color := Color(1, 1, 1)) -> Label:
	var label := Label.new()
	label.name = node_name
	label.text = text
	label.position = pos
	label.size = box
	label.add_theme_font_size_override("font_size", size)
	label.add_theme_color_override("font_color", color)
	parent.add_child(label)
	return label


func _button(parent: Control, node_name: String, text: String,
		pos: Vector2, box: Vector2) -> Button:
	var button := Button.new()
	button.name = node_name
	button.text = text
	button.position = pos
	button.size = box
	button.custom_minimum_size = box
	button.add_theme_font_size_override("font_size", 18)
	parent.add_child(button)
	return button


func _build_load() -> void:
	_load_screen = _make_screen("读档面板")
	var panel := _center_panel(_load_screen, "读档框", Vector2(500.0, 500.0))
	_label(panel, "读档标题", "读取存档", 30, Vector2(28.0, 18.0),
		Vector2(300.0, 40.0), Color(0.55, 1.0, 0.75))
	_label(panel, "读档说明", "存档里有人物和地形改动", 14,
		Vector2(28.0, 58.0), Vector2(440.0, 24.0), Color(0.75, 0.8, 0.85))
	var scroll := ScrollContainer.new()
	scroll.name = "存档滚动"
	scroll.position = Vector2(28.0, 92.0)
	scroll.size = Vector2(444.0, 320.0)
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	panel.add_child(scroll)
	_list = VBoxContainer.new()
	_list.name = "存档列表"
	_list.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_list.add_theme_constant_override("separation", 8)
	scroll.add_child(_list)
	var back := _button(panel, "读档取消", "返回", Vector2(28.0, 432.0),
		Vector2(444.0, 44.0))
	back.pressed.connect(close_panels)


func _build_save() -> void:
	_save_screen = _make_screen("保存面板")
	var panel := _center_panel(_save_screen, "保存框", Vector2(460.0, 300.0))
	_label(panel, "保存标题", "保存游戏", 30, Vector2(28.0, 18.0),
		Vector2(300.0, 40.0), Color(0.55, 1.0, 0.75))
	_label(panel, "保存说明", "给存档起个名字(重名会覆盖)", 14,
		Vector2(28.0, 58.0), Vector2(404.0, 24.0), Color(0.75, 0.8, 0.85))
	_name_input = LineEdit.new()
	_name_input.name = "存档名输入"
	_name_input.position = Vector2(28.0, 96.0)
	_name_input.size = Vector2(404.0, 44.0)
	_name_input.custom_minimum_size = Vector2(404.0, 44.0)
	_name_input.placeholder_text = "存档名"
	_name_input.add_theme_font_size_override("font_size", 18)
	panel.add_child(_name_input)
	var confirm := _button(panel, "保存确定", "保存", Vector2(28.0, 168.0),
		Vector2(404.0, 46.0))
	confirm.pressed.connect(_on_save_confirm)
	var cancel := _button(panel, "保存取消", "取消", Vector2(28.0, 226.0),
		Vector2(404.0, 40.0))
	cancel.pressed.connect(close_panels)


## 新建世界:名称 / 种子 / 地形 / 大小 / 植被 / 昼夜 / 渲染距离 / 游玩方式
func _build_create() -> void:
	_create_screen = _make_screen("创建面板")
	var panel := _center_panel(_create_screen, "创建框", Vector2(560.0, 540.0))
	_label(panel, "创建标题", "创建新的世界", 28, Vector2(28.0, 14.0),
		Vector2(400.0, 38.0), Color(0.55, 1.0, 0.75))
	var box := VBoxContainer.new()
	box.name = "创建选项"
	box.position = Vector2(28.0, 62.0)
	box.size = Vector2(504.0, 356.0)
	box.add_theme_constant_override("separation", 6)
	panel.add_child(box)
	_world_name_input = LineEdit.new()
	_world_name_input.name = "新世界名输入"
	_world_name_input.text = "新的世界"
	_world_name_input.placeholder_text = "存档名称"
	box.add_child(_form_row("存档名称", _world_name_input))
	_seed_input = LineEdit.new()
	_seed_input.name = "新种子输入"
	_seed_input.placeholder_text = "留空 = 随机"
	box.add_child(_form_row("种子", _seed_input))
	_terrain_option = _option("新地形选项",
		["平原", "丘陵", "山地"], 1)
	box.add_child(_form_row("地形类型", _terrain_option))
	_size_option = _option("新大小选项",
		["无限", "128 格", "256 格", "512 格"], 0)
	box.add_child(_form_row("世界大小", _size_option))
	_vegetation_option = _option("新植被选项",
		["无", "稀疏", "默认", "茂密"], 2)
	box.add_child(_form_row("植被密度", _vegetation_option))
	_time_option = _option("新时间选项",
		["白天", "黄昏", "夜晚"], 0)
	box.add_child(_form_row("昼夜时间", _time_option))
	_radius_option = _option("新距离选项",
		["8 区块", "12 区块", "16 区块", "24 区块", "32 区块"], 2)
	box.add_child(_form_row("渲染距离", _radius_option))
	box.add_child(_mode_row())
	var back := _button(panel, "创建取消", "取消", Vector2(28.0, 474.0),
		Vector2(504.0, 42.0))
	back.pressed.connect(close_panels)


## 加载页:新建 / 读档后先把出生点 5×5 和雾前远景铺好,再让玩家进世界。
## 进度来自区块脚本的 spawn_build_progress(),不是假的定时动画。
func _build_loading() -> void:
	_loading_screen = Control.new()
	_loading_screen.name = "加载面板"
	_loading_screen.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_loading_screen.visible = false
	_root.add_child(_loading_screen)
	var shade := ColorRect.new()
	shade.color = Color(0.02, 0.03, 0.05, 0.86)
	shade.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	shade.mouse_filter = Control.MOUSE_FILTER_STOP
	_loading_screen.add_child(shade)
	var panel := _center_panel(_loading_screen, "加载框", Vector2(520.0, 190.0))
	_loading_label = _label(panel, "加载文字", "正在生成世界…", 22,
		Vector2(28.0, 30.0), Vector2(464.0, 34.0), Color(0.55, 1.0, 0.75))
	_loading_bar = ProgressBar.new()
	_loading_bar.name = "加载进度"
	_loading_bar.position = Vector2(28.0, 88.0)
	_loading_bar.size = Vector2(464.0, 26.0)
	_loading_bar.min_value = 0.0
	_loading_bar.max_value = 100.0
	_loading_bar.value = 0.0
	_loading_bar.show_percentage = false
	panel.add_child(_loading_bar)
	_loading_percent = _label(panel, "加载百分比", "0%", 16,
		Vector2(28.0, 122.0), Vector2(464.0, 24.0), Color(0.75, 0.8, 0.85))


## 旧档高度迁移(M2a):独立模态,叠加在读档 / 保存面板之上。
## 只做提示 + 两个选择;真正的"重置地形、保留背包"在确认回调里执行。
func _build_migration() -> void:
	_migration_screen = _make_screen("迁移提示")
	var panel := _center_panel(_migration_screen, "迁移框",
		Vector2(560.0, 300.0))
	_label(panel, "迁移标题", "世界高度已升级", 26, Vector2(28.0, 18.0),
		Vector2(504.0, 36.0), Color(1.0, 0.78, 0.42))
	_migration_label = _label(panel, "迁移说明", "", 16,
		Vector2(28.0, 66.0), Vector2(504.0, 108.0),
		Color(0.86, 0.9, 0.94))
	_migration_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	var confirm := _button(panel, "迁移确认", "迁移并进入",
		Vector2(28.0, 188.0), Vector2(504.0, 46.0))
	confirm.pressed.connect(_on_migration_confirm)
	var cancel := _button(panel, "迁移取消", "取消",
		Vector2(28.0, 242.0), Vector2(504.0, 38.0))
	cancel.pressed.connect(_on_migration_cancel)


func _form_row(label_text: String, control: Control) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 10)
	var label := Label.new()
	label.text = label_text
	label.custom_minimum_size = Vector2(100.0, 0.0)
	label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	label.add_theme_font_size_override("font_size", 15)
	label.add_theme_color_override("font_color", Color(0.78, 0.83, 0.9))
	row.add_child(label)
	control.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	control.custom_minimum_size = Vector2(0.0, 34.0)
	row.add_child(control)
	return row


func _option(node_name: String, items: Array, picked: int) -> OptionButton:
	var option := OptionButton.new()
	option.name = node_name
	for item in items:
		option.add_item(str(item))
	option.selected = clampi(picked, 0, items.size() - 1)
	option.add_theme_font_size_override("font_size", 15)
	return option


func _mode_row() -> HBoxContainer:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 10)
	var label := Label.new()
	label.text = "游玩方式"
	label.custom_minimum_size = Vector2(100.0, 0.0)
	label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	label.add_theme_font_size_override("font_size", 15)
	label.add_theme_color_override("font_color", Color(0.78, 0.83, 0.9))
	row.add_child(label)
	var survival := Button.new()
	survival.name = "生存模式"
	survival.text = "生存模式"
	survival.custom_minimum_size = Vector2(0.0, 38.0)
	survival.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	survival.add_theme_font_size_override("font_size", 16)
	survival.pressed.connect(_on_create.bind(false))
	row.add_child(survival)
	var creative := Button.new()
	creative.name = "创造模式"
	creative.text = "创造模式"
	creative.custom_minimum_size = Vector2(0.0, 38.0)
	creative.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	creative.add_theme_font_size_override("font_size", 16)
	creative.pressed.connect(_on_create.bind(true))
	row.add_child(creative)
	return row


func _refresh_list() -> void:
	for child in _list.get_children():
		child.queue_free()
	var saves := list_saves()
	if saves.is_empty():
		var empty := Label.new()
		empty.name = "空列表"
		empty.text = "还没有存档 —— 进游戏按 ESC,点『保存游戏』"
		empty.custom_minimum_size = Vector2(0.0, 30.0)
		empty.add_theme_font_size_override("font_size", 15)
		empty.add_theme_color_override("font_color", Color(0.7, 0.75, 0.8))
		_list.add_child(empty)
		return
	for index in range(saves.size()):
		var entry: Dictionary = saves[index]
		var button := Button.new()
		button.name = "存档项%d" % (index + 1)
		button.text = "%s    %s" % [entry["name"], entry["time"]]
		button.custom_minimum_size = Vector2(0.0, 52.0)
		button.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		button.add_theme_font_size_override("font_size", 16)
		button.pressed.connect(_on_pick.bind(String(entry["file"])))
		_list.add_child(button)


func open_load() -> void:
	_refresh_list()
	_save_screen.visible = false
	_load_screen.visible = true
	Input.mouse_mode = Input.MOUSE_MODE_VISIBLE


func open_save() -> void:
	_name_input.text = _world_name if not _world_name.is_empty() else _last_name
	_load_screen.visible = false
	_save_screen.visible = true
	Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
	_name_input.grab_focus()


func open_create() -> void:
	_load_screen.visible = false
	_save_screen.visible = false
	_create_screen.visible = true
	_world_name_input.text = "新的世界"
	_seed_input.text = ""
	_terrain_option.selected = 1
	_size_option.selected = 0
	_vegetation_option.selected = 2
	_time_option.selected = 0
	var host := get_parent()
	var radius_index := 2
	if host != null:
		radius_index = clampi(int(host.get("渲染距离")), 1, 5)
	_radius_option.selected = radius_index - 1
	Input.mouse_mode = Input.MOUSE_MODE_VISIBLE


## 开始菜单主入口:只做"继续"。有存档直接读最近一档;没有存档就提示先新建,
## 绝不偷偷跳到新建流程 —— 否则和『新建世界』又变成两个一样的入口。
func open_menu_primary() -> void:
	var saves := list_saves()
	if saves.is_empty():
		_show_toast("还没有存档,请先『新建世界』")
		return
	if load_game(String(saves[0]["file"])):
		close_panels()
		_enter_world_when_ready("已继续:%s" % _last_name)


## 菜单按存档状态刷新(单入口口径):
##   主按钮有存档是『继续游戏』可点,没存档是『开始游戏』置灰 + 提示先新建;
##   『新建世界』永远可点;『读取存档』没档时置灰(设计稿的 disabled 态)。
func _refresh_menu() -> void:
	var scene := get_tree().current_scene
	if scene == null:
		return
	var menu := scene.find_child("世界菜单", true, false)
	if menu == null:
		return
	_style_menu(menu)
	var has_saves := not list_saves().is_empty()
	var primary := menu.find_child("继续游戏", true, false)
	if primary is Button:
		(primary as Button).text = "继续游戏" if has_saves else "开始游戏"
		(primary as Button).disabled = not has_saves
	var read_button := menu.find_child("读取存档", true, false)
	if read_button is Button:
		(read_button as Button).disabled = not has_saves


## 按设计稿(index.html 的 .menu-panel / .mc-btn)重排开始菜单:
## 左对齐标题 + 副标题、四个通栏按钮、悬停才出现的箭头、底部一行小字。
## 反复调用只是重设同样的几块样式,不会重复挂节点。
func _style_menu(menu: Node) -> void:
	var panel := menu as Control
	panel.add_theme_stylebox_override("panel", _menu_box(
		Color(0.051, 0.059, 0.09, 0.86), Color(0.55, 1.0, 0.75, 0.30), 18))
	var title := menu.find_child("菜单标题", true, false) as Label
	if title != null:
		title.position = Vector2(60.0, 38.0)
		title.size = Vector2(400.0, 64.0)
		title.add_theme_font_size_override("font_size", 52)
	var subtitle := menu.find_child("菜单副标题", true, false) as Label
	if subtitle == null:
		subtitle = Label.new()
		subtitle.name = "菜单副标题"
		subtitle.mouse_filter = Control.MOUSE_FILTER_IGNORE
		menu.add_child(subtitle)
	subtitle.text = "MINECRAFT · GODOT DEMO"
	subtitle.position = Vector2(60.0, 114.0)
	subtitle.size = Vector2(400.0, 18.0)
	subtitle.add_theme_font_size_override("font_size", 11)
	subtitle.add_theme_color_override("font_color",
		Color(0.655, 0.702, 0.753))
	var rows := {"继续游戏": 160.0, "读取存档": 230.0, "新建世界": 300.0,
		"退出游戏": 370.0}
	for key in rows.keys():
		var button := menu.find_child(str(key), true, false) as Button
		if button == null:
			continue
		button.position = Vector2(60.0, float(rows[key]))
		button.size = Vector2(400.0, 54.0)
		button.custom_minimum_size = Vector2(400.0, 54.0)
		button.alignment = HORIZONTAL_ALIGNMENT_LEFT
		button.add_theme_font_size_override("font_size", 15)
		_style_menu_button(button, str(key) == "新建世界")
	var note := menu.find_child("菜单提示", true, false) as Label
	if note == null:
		note = Label.new()
		note.name = "菜单提示"
		note.mouse_filter = Control.MOUSE_FILTER_IGNORE
		menu.add_child(note)
	note.text = "默认渲染距离 16 区块 · 按 ESC 暂停"
	# 设计稿里这行小字在面板外面(紧贴底边),面板不裁剪子节点,照样能画出来
	note.position = Vector2(0.0, 516.0)
	note.size = Vector2(520.0, 20.0)
	note.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	note.add_theme_font_size_override("font_size", 11)
	note.add_theme_color_override("font_color", Color(1.0, 1.0, 1.0, 0.66))


## 设计稿 .mc-btn 的四态:普通 / 悬停 / 按下 / 禁用。主按钮(创建游戏那条
## 绿键)用 primary 配色。边框 / 内容边距保证文字左对齐留 18px。
func _style_menu_button(button: Button, primary: bool) -> void:
	var base := Color(0.247, 0.490, 0.357) if primary \
		else Color(0.290, 0.314, 0.361)
	var hover := Color(0.290, 0.561, 0.412) if primary \
		else Color(0.337, 0.361, 0.412)
	var press := base.darkened(0.35)
	button.add_theme_stylebox_override("normal",
		_menu_box(base, Color(0.039, 0.047, 0.067, 1.0), 0))
	button.add_theme_stylebox_override("hover",
		_menu_box(hover, Color(0.55, 1.0, 0.75, 0.75), 0))
	button.add_theme_stylebox_override("pressed",
		_menu_box(press, Color(0.55, 1.0, 0.75, 0.55), 0))
	button.add_theme_stylebox_override("disabled",
		_menu_box(base.darkened(0.45), Color(0.16, 0.18, 0.21, 1.0), 0))
	button.add_theme_stylebox_override("focus",
		_menu_box(Color(0.0, 0.0, 0.0, 0.0), Color(0.55, 1.0, 0.75, 0.9), 0))
	for state in ["normal", "hover", "pressed", "disabled"]:
		var style := button.get_theme_stylebox(state) as StyleBoxFlat
		if style != null:
			style.content_margin_left = 18.0
			style.content_margin_right = 16.0
	var arrow := button.get_node_or_null("箭头") as Label
	if arrow == null:
		arrow = Label.new()
		arrow.name = "箭头"
		arrow.text = "›"
		arrow.mouse_filter = Control.MOUSE_FILTER_IGNORE
		button.add_child(arrow)
		button.mouse_entered.connect(_menu_arrow.bind(button, true))
		button.mouse_exited.connect(_menu_arrow.bind(button, false))
		button.focus_entered.connect(_menu_arrow.bind(button, true))
		button.focus_exited.connect(_menu_arrow.bind(button, false))
	arrow.position = Vector2(362.0, 10.0)
	arrow.size = Vector2(24.0, 34.0)
	arrow.add_theme_font_size_override("font_size", 22)
	arrow.add_theme_color_override("font_color", Color(0.55, 1.0, 0.75))
	arrow.visible = false


## 悬停 / 键盘聚焦时才露出按钮右边那个『›』(设计稿 .arrow)
func _menu_arrow(button: Button, shown: bool) -> void:
	var arrow := button.get_node_or_null("箭头") as Label
	if arrow != null:
		arrow.visible = shown and not button.disabled


func _menu_box(fill: Color, border: Color, radius: int) -> StyleBoxFlat:
	var style := StyleBoxFlat.new()
	style.bg_color = fill
	style.corner_radius_top_left = radius
	style.corner_radius_top_right = radius
	style.corner_radius_bottom_left = radius
	style.corner_radius_bottom_right = radius
	style.border_width_left = 2
	style.border_width_right = 2
	style.border_width_top = 2
	style.border_width_bottom = 2
	style.border_color = border
	return style


func close_panels() -> void:
	if _load_screen != null:
		_load_screen.visible = false
	if _save_screen != null:
		_save_screen.visible = false
	if _create_screen != null:
		_create_screen.visible = false


func _show_toast(text: String) -> void:
	_toast.text = text
	_toast.visible = true
	_toast_time = 2.6


# ── 存档读写的本体 ──────────────────────────────────────────────
func _on_pick(file_name: String) -> void:
	if load_game(file_name):
		close_panels()
		_enter_world_when_ready("已读取存档:%s" % _last_name)


func _on_save_confirm() -> void:
	var save_name := _name_input.text.strip_edges()
	if save_name.is_empty():
		save_name = "未命名"
	if save_game(save_name):
		close_panels()
		_enter_world("暂停菜单")
		_show_toast("已保存:%s" % save_name)
	else:
		_show_toast("保存失败:写不进 user://saves")


## 新建世界:把面板上选的参数写进玩家变量,再让区块脚本按新参数重铺。
func _on_create(creative: bool) -> void:
	var host := get_parent()
	if host == null:
		return
	var save_name := _world_name_input.text.strip_edges()
	if save_name.is_empty():
		save_name = "新的世界"
	_world_name = save_name
	_last_name = save_name
	host.set("世界名", save_name)
	host.set("种子", _seed_input.text.strip_edges())
	host.set("地形类型", float(_terrain_option.selected + 1))
	host.set("世界大小", float(_size_option.selected + 1))
	host.set("植被密度", float(_vegetation_option.selected + 1))
	host.set("昼夜时间", float(_time_option.selected + 1))
	host.set("渲染距离", float(_radius_option.selected + 1))
	host.set("创造模式", creative)
	host.set("生命", 100.0)
	host.set("方块序号", 0.0)
	host.set("手持名", ITEM_NAMES[0])
	host.set("第三人称", false)
	var inventory := _inventory()
	if inventory != null:
		# 新世界:背包回到初始 36 格(前 5 格方块 x64 / 苹果 8 / 剑 1)
		inventory.call("reset_slots")
	if host is Node3D:
		(host as Node3D).global_position = Vector3(0.5, 1.0, 0.5)
	if host is CharacterBody3D:
		(host as CharacterBody3D).velocity = Vector3.ZERO
	_apply_view(false)
	var chunk := _chunk()
	if chunk != null:
		chunk.call("apply_world")
	close_panels()
	_enter_world_when_ready("世界已创建:%s" % save_name, true)


## 进世界前先把出生点铺好,再放玩家进去:
##   1) 加载屏先画出来;
##   2) 按帧建完出生点周围 5×5 区块(地形 + 网格 + 碰撞)和雾前远景,进度条实时走;
##   3) 进世界,雾后的远端缺口交给区块脚本按帧继续铺。
## 以前是等整个渲染距离铺完(约 4.3 秒),而且进世界后近处的地形还在慢慢长。
func _enter_world_when_ready(done_text := "", at_spawn := false) -> void:
	var load_started := Time.get_ticks_msec()
	var chunk := _chunk()
	if chunk != null:
		_loading_screen.visible = true
		_loading_bar.value = 0.0
		_loading_percent.text = "正在生成世界… 0%"
		await get_tree().process_frame
		if is_instance_valid(chunk):
			# 先在出生区域挑一块落差最小的位置(不改地形),再按它铺地形。
			# 新建世界要把人物放到这块地上;读档保留存档里的坐标。
			var point: Vector3 = chunk.call("choose_spawn")
			var host := get_parent()
			if at_spawn and host is Node3D:
				_move_player_to(point)
				if host is CharacterBody3D:
					(host as CharacterBody3D).velocity = Vector3.ZERO
			# 先加载后进入:出生点 5×5 的地形 / 网格 / 碰撞 + 雾前远景全部就绪,
			# 才解除暂停放开操作。进度来自区块脚本的真实剩余量,不是定时动画。
			chunk.call("begin_spawn_build", 2)
			var frames := 0
			while true:
				await get_tree().process_frame
				var ratio := float(chunk.call("spawn_build_progress"))
				_loading_bar.value = ratio * 100.0
				_loading_percent.text = "正在生成世界… %d%%" % int(ratio * 100.0)
				frames += 1
				if bool(chunk.call("spawn_build_step")):
					break
				if frames >= 3600:
					break
			chunk.call("finish_spawn_build")
		_loading_bar.value = 100.0
		_loading_percent.text = "完成"
		await get_tree().process_frame
		_loading_screen.visible = false
	_world_load_s = float(Time.get_ticks_msec() - load_started) / 1000.0
	_enter_world("世界菜单")
	if not done_text.is_empty():
		_show_toast(done_text)


## 最近一次进世界的 world_load_s(点击开始/读档 -> 放开操作)。
func world_load_seconds() -> float:
	return _world_load_s


## 读档 / 建世界 / 保存完回到游戏:收起菜单、解除暂停、锁鼠标。
## 走框架自己的 _gk_apply_pause,菜单状态 / 鼠标恢复才和按钮点出来的一致。
func _enter_world(menu_name: String) -> void:
	var host := get_parent()
	if host != null and host.has_method("_gk_apply_pause"):
		host.call("_gk_apply_pause", false, menu_name, true)
		# 框架会把鼠标还原成"暂停前的状态"(菜单里是 VISIBLE),这里必须
		# 显式锁回去 —— 否则新建 / 读档 / 保存后光标留在画面里,转不了视角。
		Input.mouse_mode = Input.MOUSE_MODE_CAPTURED
		_last_mouse_mode = Input.MOUSE_MODE_CAPTURED
		return
	var scene := get_tree().current_scene
	if scene != null:
		for name in ["世界菜单", "暂停菜单"]:
			var menu := scene.find_child(name, true, false)
			if menu is CanvasItem:
				(menu as CanvasItem).visible = false
	get_tree().paused = false
	Input.mouse_mode = Input.MOUSE_MODE_CAPTURED
	_last_mouse_mode = Input.MOUSE_MODE_CAPTURED


func save_game(save_name: String) -> bool:
	DirAccess.make_dir_recursive_absolute(SAVE_DIR)
	var file := FileAccess.open(_path_for(save_name), FileAccess.WRITE)
	if file == null:
		return false
	file.store_string(JSON.stringify(_snapshot(save_name), "\t"))
	file.close()
	_last_name = save_name
	return true


func _snapshot(save_name: String) -> Dictionary:
	var host := get_parent()
	var body := host as Node3D
	var data := {
		"format": 3,
		"name": save_name,
		"time": Time.get_datetime_string_from_system().replace("T", " "),
		"player": {},
		"world_settings": {},
	}
	if host != null:
		data["world_settings"] = {
			"name": str(host.get("世界名")),
			"seed": str(host.get("种子")),
			"terrain": int(host.get("地形类型")),
			"size": int(host.get("世界大小")),
			"vegetation": int(host.get("植被密度")),
			"time": int(host.get("昼夜时间")),
			"radius": int(host.get("渲染距离")),
			# M2a:地形与世界高度绑定;旧档缺这三项就是"高度不兼容"
			"world_height": _world_height(),
			"sea_level": _sea_level(),
			"section_height": _section_height(),
		}
	if body != null:
		data["player"] = {
			"x": body.global_position.x,
			"y": body.global_position.y,
			"z": body.global_position.z,
			"health": float(host.get("生命")),
			"slot": int(host.get("方块序号")),
			"creative": bool(host.get("创造模式")),
			"third": bool(host.get("第三人称")),
		}
	var chunk := _chunk()
	if chunk != null:
		data["world"] = chunk.call("save_state")
	var inventory := _inventory()
	if inventory != null:
		data["inventory"] = inventory.call("export_slots")
		if inventory.has_method("export_chests"):
			data["chests"] = inventory.call("export_chests")
	return data


func load_game(file_name: String, force := false) -> bool:
	var file := FileAccess.open(SAVE_DIR + "/" + file_name, FileAccess.READ)
	if file == null:
		return false
	var text := file.get_as_text()
	file.close()
	var data = JSON.parse_string(text)
	if not (data is Dictionary):
		return false
	var incompatible := not _height_compatible(data)
	if incompatible and not force:
		_open_migration(data, file_name)
		return false
	if incompatible:
		_migration_forced = true
		print("MIGRATION_FORCED file=", file_name)
	_apply_loaded(data, incompatible)
	return true


## 读档应用(正常读档 / 迁移确认共用)。
## reset_terrain = true 时只清"地形相关的世界改动",背包 / 箱子 / 设置 /
## 血量 / 时间 / 位置照常写回 —— 这就是 §11.10 的迁移口径。
func _apply_loaded(data: Dictionary, reset_terrain: bool) -> void:
	var host := get_parent()
	var player = data.get("player", {})
	if host is Node3D and player is Dictionary:
		# 读档也是"传送":相机一起走,免得『相机_跟随』记下错误偏移
		_move_player_to(Vector3(
			float(player.get("x", 0.5)), float(player.get("y", 1.0)),
			float(player.get("z", 0.5))))
		if host is CharacterBody3D:
			(host as CharacterBody3D).velocity = Vector3.ZERO
		host.set("生命", float(player.get("health", 100.0)))
		host.set("创造模式", bool(player.get("creative", true)))
		var slot := clampi(int(player.get("slot", 0)), 0, ITEM_NAMES.size() - 1)
		host.set("方块序号", float(slot))
		var third := bool(player.get("third", false))
		host.set("第三人称", third)
		_apply_view(third)
	# 世界设置:旧存档没有这一块 -> 保持默认值,地形和改动照旧
	var settings = data.get("world_settings", {})
	if host != null and settings is Dictionary:
		host.set("世界名", str(settings.get("name", "新的世界")))
		host.set("种子", str(settings.get("seed", "")))
		host.set("地形类型", float(settings.get("terrain", 2)))
		host.set("世界大小", float(settings.get("size", 1)))
		host.set("植被密度", float(settings.get("vegetation", 3)))
		host.set("昼夜时间", float(settings.get("time", 1)))
		host.set("渲染距离", float(settings.get("radius", 3)))
		_world_name = str(settings.get("name", "新的世界"))
	var world = data.get("world", {})
	var chunk := _chunk()
	if chunk != null:
		# apply_world() 已经清空 _removed/_placed/trees 并按当前设置重铺;
		# 迁移时到这里就结束(旧地形改动全部丢弃),正常读档再叠加 load_state。
		chunk.call("apply_world")
		if world is Dictionary and not reset_terrain:
			chunk.call("load_state", world)
		# M2b:地形内容变了(高度从 24 抬到 60),旧档 / 迁移后的人可能正埋在
		# 方块里。x / z 保持读档值,只在 y 低于新地表时把人抬上来。
		_lift_out_of_terrain(host, chunk)
	# 背包:旧存档没有这一块 -> 保持初始背包
	var inventory := _inventory()
	if inventory != null and data.get("inventory") is Array:
		inventory.call("import_slots", data.get("inventory"))
	if inventory != null and inventory.has_method("import_chests"):
		# 箱子内容:旧存档没有这一块 -> 清空(不用判断类型,脚本自己兜底)
		inventory.call("import_chests", data.get("chests", {}))
	_last_name = str(data.get("name", "新的世界"))


## 读档后如果脚底在新地表以下(埋在方块里),抬到地表上方;
## x / z 保持存档值不变,只有真的低于地表才动(正常站在地面上的档不受影响)。
func _lift_out_of_terrain(host: Node, chunk: Node) -> void:
	if not (host is Node3D) or not chunk.has_method("safe_ground_y"):
		return
	var point: Vector3 = (host as Node3D).global_position
	var cell := Vector2i(int(floor(point.x)), int(floor(point.z)))
	var ground := float(chunk.call("safe_ground_y", cell)) + 0.05
	if point.y >= ground:
		return
	_move_player_to(Vector3(point.x, ground, point.z))
	if host is CharacterBody3D:
		(host as CharacterBody3D).velocity = Vector3.ZERO


## 存档里的地表高度:新档读 world_settings.world_height;旧档没有字段,
## 按旧版地表上限 LEGACY_WORLD_HEIGHT 对待。
func _save_height(data: Dictionary) -> int:
	var settings = data.get("world_settings", {})
	if settings is Dictionary and settings.has("world_height"):
		return int(settings.get("world_height", 0))
	return LEGACY_WORLD_HEIGHT


## 高度兼容判定:字段缺失但世界改动为空的新建档,直接按新高度处理。
func _height_compatible(data: Dictionary) -> bool:
	var settings = data.get("world_settings", {})
	if settings is Dictionary and settings.has("world_height"):
		return int(settings.get("world_height", 0)) == _world_height()
	var world = data.get("world", {})
	if world is Dictionary:
		for key in ["removed", "placed", "trees"]:
			var part = world.get(key, {})
			if part is Dictionary and not part.is_empty():
				return false
	return true


func _open_migration(data: Dictionary, file_name: String) -> void:
	_pending_load = data
	_pending_file = file_name
	var old_height := _save_height(data)
	var text := ("此存档的世界高度为旧版本（%d），地形将按新版本（%d）重新生成；"
		% [old_height, _world_height()])
	text += "\n背包、箱子、设置与玩家位置将保留，旧的世界改动会重置。"
	_migration_label.text = text
	_migration_screen.visible = true
	Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
	print("MIGRATION_PROMPT old=", old_height, " new=", _world_height(),
		" file=", file_name)


func _on_migration_confirm() -> void:
	var data := _pending_load
	var file_name := _pending_file
	_pending_load = {}
	_pending_file = ""
	if data.is_empty():
		_migration_screen.visible = false
		return
	var started := Time.get_ticks_msec()
	var stats := _world_edit_counts(data)
	_migration_screen.visible = false
	_apply_loaded(data, true)
	_write_migration_report(String(data.get("name", file_name)),
		_save_height(data), stats, Time.get_ticks_msec() - started)
	close_panels()
	_enter_world_when_ready("已按新世界高度重建地形:%s" % _last_name)


## 取消:回到菜单,不写盘、不动当前世界(存档文件保持原样)。
func _on_migration_cancel() -> void:
	_pending_load = {}
	_pending_file = ""
	if _migration_screen != null:
		_migration_screen.visible = false
	close_panels()
	print("MIGRATION_CANCELLED")


func _world_edit_counts(data: Dictionary) -> Dictionary:
	var out := {"removed": 0, "placed": 0, "trees": 0}
	var world = data.get("world", {})
	if world is Dictionary:
		for key in out.keys():
			var part = world.get(key, {})
			if part is Dictionary:
				out[key] = part.size()
	return out


func _write_migration_report(save_name: String, old_height: int,
		stats: Dictionary, elapsed_ms: int) -> void:
	DirAccess.make_dir_recursive_absolute(
		ProjectSettings.globalize_path(MIGRATION_DIR))
	var path := "%s/%s_%d.json" % [ProjectSettings.globalize_path(MIGRATION_DIR),
		save_name.validate_filename(), Time.get_ticks_msec()]
	var file := FileAccess.open(path, FileAccess.WRITE)
	if file == null:
		return
	file.store_string(JSON.stringify({
		"time": Time.get_datetime_string_from_system().replace("T", " "),
		"save": save_name,
		"from_height": old_height,
		"to_height": _world_height(),
		"discarded_items": 0,
		"reset_edits": stats,
		"elapsed_ms": elapsed_ms,
		"forced": _migration_forced,
	}, "\t"))
	file.close()
	print("MIGRATION_REPORT ", path)


## 高度常量:从区块脚本读,拿不到就用当前版本的默认值。
func _world_height() -> int:
	var chunk := _chunk()
	if chunk != null and chunk.has_method("world_height"):
		return int(chunk.call("world_height"))
	return 64


func _sea_level() -> int:
	var chunk := _chunk()
	if chunk != null and chunk.has_method("sea_level"):
		return int(chunk.call("sea_level"))
	return 32


func _section_height() -> int:
	var chunk := _chunk()
	if chunk != null and chunk.has_method("section_height"):
		return int(chunk.call("section_height"))
	return 16


## 人物存档里带了视角:读档后把当前相机 / 模型显隐也切回去。
func _apply_view(third: bool) -> void:
	var scene := get_tree().current_scene
	if scene != null:
		var cam_name := "第三人称相机" if third else "第一人称相机"
		var cams := scene.find_children(cam_name, "Camera3D", true, false)
		if cams.size() > 0:
			(cams[0] as Camera3D).make_current()
	var host := get_parent()
	if host != null:
		var model := host.find_child("Model", true, false)
		if model is Node3D:
			(model as Node3D).visible = third


func list_saves() -> Array:
	var out: Array = []
	var dir := DirAccess.open(SAVE_DIR)
	if dir == null:
		return out
	dir.list_dir_begin()
	var entry := dir.get_next()
	while entry != "":
		if not dir.current_is_dir() and entry.ends_with(".json"):
			var file := FileAccess.open(SAVE_DIR + "/" + entry, FileAccess.READ)
			if file != null:
				var parsed = JSON.parse_string(file.get_as_text())
				file.close()
				if parsed is Dictionary:
					out.append({
						"file": entry,
						"name": str(parsed.get("name", entry.get_basename())),
						"time": str(parsed.get("time", "")),
					})
		entry = dir.get_next()
	dir.list_dir_end()
	out.sort_custom(func(a, b): return str(a["time"]) > str(b["time"]))
	return out


func _path_for(save_name: String) -> String:
	var safe := save_name.validate_filename().strip_edges()
	if safe.is_empty():
		safe = "存档"
	return SAVE_DIR + "/" + safe + ".json"


func _chunk() -> Node:
	var host := get_parent()
	if host == null:
		return null
	for child in host.get_children():
		if child == self:
			continue
		if child.has_method("save_state") and child.has_method("load_state"):
			return child
	return null


func _inventory() -> Node:
	var host := get_parent()
	if host == null:
		return null
	for child in host.get_children():
		if child == self:
			continue
		if child.has_method("export_slots") and child.has_method("import_slots"):
			return child
	return null
"""


# ── 设置:画面 / 控制(同样存在 .blend 文本块里)────────────────────
SETTINGS_SCRIPT = """extends Node

## 设置面板(第两百零六轮):画面(渲染距离 / 全屏)+ 控制(灵敏度 / 反转Y)。
## 运行时搭界面,存 user://settings.cfg,改一项立刻生效。

const CONFIG_PATH := "user://settings.cfg"

var _layer: CanvasLayer
var _screen: Control
var _radius_option: OptionButton
var _fullscreen_check: CheckBox
var _sens_slider: HSlider
var _sens_label: Label
var _invert_check: CheckBox
var _fps_option: OptionButton
var _vsync_check: CheckBox
var _quality_option: OptionButton
## 画质项(M2b):面亮度差 + 角落 AO,可关;关掉后回到原来的纯色块
var _soft_check: CheckBox
var _soft_light := true
var _hint: Label
var _fps_timer := 0.0
var _config := ConfigFile.new()
var _radius_index := 3
var _sensitivity := 1.0
var _invert := false
var _fullscreen := false
## 帧率上限 / 垂直同步 / 画质档(第两百一十一轮:用户反馈"卡,怀疑帧率")
const FPS_CAPS := [30, 60, 90, 120, 0]
const FPS_LABELS := ["30 帧", "60 帧", "90 帧", "120 帧", "不限制"]
const QUALITY_LABELS := ["流畅(关阴影)", "均衡", "高清(2x MSAA)"]
var _fps_index := 1
var _vsync := true
var _quality := 1


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	_load_config()
	_build_ui()
	_apply_all()


func _unhandled_input(event: InputEvent) -> void:
	if not event.is_action_pressed("ui_cancel"):
		return
	if _screen != null and _screen.visible:
		_screen.visible = false
		get_viewport().set_input_as_handled()


func _build_ui() -> void:
	_layer = CanvasLayer.new()
	_layer.name = "设置层"
	_layer.layer = 31
	_layer.process_mode = Node.PROCESS_MODE_ALWAYS
	add_child(_layer)
	_screen = Control.new()
	_screen.name = "设置面板"
	_screen.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_screen.visible = false
	_layer.add_child(_screen)
	var shade := ColorRect.new()
	shade.color = Color(0.0, 0.0, 0.0, 0.45)
	shade.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	shade.mouse_filter = Control.MOUSE_FILTER_STOP
	_screen.add_child(shade)
	var panel := Panel.new()
	panel.name = "设置框"
	panel.set_anchors_preset(Control.PRESET_CENTER)
	panel.offset_left = -280.0
	panel.offset_right = 280.0
	panel.offset_top = -290.0
	panel.offset_bottom = 290.0
	var style := StyleBoxFlat.new()
	style.bg_color = Color(0.05, 0.06, 0.09, 0.94)
	style.corner_radius_top_left = 16
	style.corner_radius_top_right = 16
	style.corner_radius_bottom_left = 16
	style.corner_radius_bottom_right = 16
	style.border_width_left = 2
	style.border_width_right = 2
	style.border_width_top = 2
	style.border_width_bottom = 2
	style.border_color = Color(0.55, 1.0, 0.75, 0.30)
	panel.add_theme_stylebox_override("panel", style)
	_screen.add_child(panel)
	var title := Label.new()
	title.name = "设置标题"
	title.text = "设置"
	title.position = Vector2(28.0, 14.0)
	title.size = Vector2(300.0, 38.0)
	title.add_theme_font_size_override("font_size", 28)
	title.add_theme_color_override("font_color", Color(0.55, 1.0, 0.75))
	panel.add_child(title)
	var box := VBoxContainer.new()
	box.name = "设置选项"
	box.position = Vector2(28.0, 62.0)
	box.size = Vector2(504.0, 400.0)
	box.add_theme_constant_override("separation", 6)
	panel.add_child(box)
	box.add_child(_section("画面"))
	_radius_option = _option("设置距离选项",
		["8 区块", "12 区块", "16 区块", "24 区块", "32 区块"], _radius_index - 1)
	_radius_option.item_selected.connect(_on_radius)
	box.add_child(_form_row("渲染距离", _radius_option))
	_fullscreen_check = CheckBox.new()
	_fullscreen_check.name = "全屏勾选"
	_fullscreen_check.text = "全屏"
	_fullscreen_check.button_pressed = _fullscreen
	_fullscreen_check.toggled.connect(_on_fullscreen)
	box.add_child(_form_row("显示模式", _fullscreen_check))
	box.add_child(_section("性能"))
	_fps_option = _option("帧率上限选项", FPS_LABELS, _fps_index)
	_fps_option.item_selected.connect(_on_fps)
	box.add_child(_form_row("帧率上限", _fps_option))
	_vsync_check = CheckBox.new()
	_vsync_check.name = "垂直同步勾选"
	_vsync_check.text = "开启"
	_vsync_check.button_pressed = _vsync
	_vsync_check.toggled.connect(_on_vsync)
	box.add_child(_form_row("垂直同步", _vsync_check))
	_quality_option = _option("画质选项", QUALITY_LABELS, _quality)
	_quality_option.item_selected.connect(_on_quality)
	box.add_child(_form_row("画质", _quality_option))
	_soft_check = CheckBox.new()
	_soft_check.name = "柔和光影勾选"
	_soft_check.text = "开启"
	_soft_check.button_pressed = _soft_light
	_soft_check.toggled.connect(_on_soft_light)
	box.add_child(_form_row("柔和光影", _soft_check))
	box.add_child(_section("控制"))
	var sens_box := HBoxContainer.new()
	sens_box.add_theme_constant_override("separation", 8)
	_sens_slider = HSlider.new()
	_sens_slider.name = "灵敏度滑条"
	_sens_slider.min_value = 0.2
	_sens_slider.max_value = 3.0
	_sens_slider.step = 0.1
	_sens_slider.value = _sensitivity
	_sens_slider.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_sens_slider.mouse_filter = Control.MOUSE_FILTER_STOP
	_sens_slider.value_changed.connect(_on_sensitivity)
	sens_box.add_child(_sens_slider)
	_sens_label = Label.new()
	_sens_label.name = "灵敏度数值"
	_sens_label.custom_minimum_size = Vector2(64.0, 0.0)
	_sens_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	_sens_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	sens_box.add_child(_sens_label)
	box.add_child(_form_row("鼠标灵敏度", sens_box))
	_invert_check = CheckBox.new()
	_invert_check.name = "反转Y勾选"
	_invert_check.text = "反转上下"
	_invert_check.button_pressed = _invert
	_invert_check.toggled.connect(_on_invert)
	box.add_child(_form_row("视角", _invert_check))
	var hint := Label.new()
	hint.name = "设置说明"
	hint.text = "改动立即生效,自动保存"
	hint.position = Vector2(28.0, 466.0)
	hint.size = Vector2(504.0, 22.0)
	hint.add_theme_font_size_override("font_size", 13)
	hint.add_theme_color_override("font_color", Color(0.65, 0.7, 0.78))
	panel.add_child(hint)
	_hint = hint
	var close := Button.new()
	close.name = "设置关闭"
	close.text = "关闭"
	close.position = Vector2(28.0, 494.0)
	close.size = Vector2(504.0, 42.0)
	close.custom_minimum_size = Vector2(504.0, 42.0)
	close.add_theme_font_size_override("font_size", 18)
	close.pressed.connect(_on_close)
	panel.add_child(close)
	_refresh_sens_label()


func _section(text: String) -> Label:
	var label := Label.new()
	label.text = "— " + text + " —"
	label.custom_minimum_size = Vector2(0.0, 24.0)
	label.add_theme_font_size_override("font_size", 15)
	label.add_theme_color_override("font_color", Color(0.55, 1.0, 0.75))
	return label


func _form_row(label_text: String, control: Control) -> HBoxContainer:
	var row := HBoxContainer.new()
	row.add_theme_constant_override("separation", 10)
	var label := Label.new()
	label.text = label_text
	label.custom_minimum_size = Vector2(100.0, 0.0)
	label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	label.add_theme_font_size_override("font_size", 15)
	label.add_theme_color_override("font_color", Color(0.78, 0.83, 0.9))
	row.add_child(label)
	control.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	control.custom_minimum_size = Vector2(0.0, 32.0)
	row.add_child(control)
	return row


func _option(node_name: String, items: Array, picked: int) -> OptionButton:
	var option := OptionButton.new()
	option.name = node_name
	for item in items:
		option.add_item(str(item))
	option.selected = clampi(picked, 0, items.size() - 1)
	option.add_theme_font_size_override("font_size", 15)
	return option


func open_settings() -> void:
	# 渲染距离是"当前世界"的参数:面板打开时按玩家的值显示,
	# 不是按上一次保存的全局设置显示。
	var host := get_parent()
	if host != null:
		_radius_index = clampi(int(host.get("渲染距离")), 1, 5)
	_radius_option.selected = _radius_index - 1
	_fullscreen_check.button_pressed = _fullscreen
	_fps_option.selected = _fps_index
	_vsync_check.button_pressed = _vsync
	_quality_option.selected = _quality
	_sens_slider.value = _sensitivity
	_invert_check.button_pressed = _invert
	_refresh_sens_label()
	_screen.visible = true
	Input.mouse_mode = Input.MOUSE_MODE_VISIBLE


func _process(delta: float) -> void:
	# 设置面板开着时,说明行显示实时帧率 —— "卡不卡"先用数字说话。
	if _hint == null or _screen == null or not _screen.visible:
		return
	_fps_timer += delta
	if _fps_timer < 0.25:
		return
	_fps_timer = 0.0
	_hint.text = "当前 %d 帧 · 改动立即生效,自动保存" \
		% Engine.get_frames_per_second()


func _on_close() -> void:
	_screen.visible = false


func _on_radius(index: int) -> void:
	_radius_index = clampi(index + 1, 1, 5)
	_apply_radius()
	_save_config()


func _on_sensitivity(value: float) -> void:
	_sensitivity = value
	_refresh_sens_label()
	_apply_control()
	_save_config()


func _on_invert(pressed: bool) -> void:
	_invert = pressed
	_apply_control()
	_save_config()


func _on_fullscreen(pressed: bool) -> void:
	_fullscreen = pressed
	_apply_fullscreen()
	_save_config()


func _on_fps(index: int) -> void:
	_fps_index = clampi(index, 0, FPS_CAPS.size() - 1)
	_apply_perf()
	_save_config()


func _on_vsync(pressed: bool) -> void:
	_vsync = pressed
	_apply_perf()
	_save_config()


func _on_quality(index: int) -> void:
	_quality = clampi(index, 0, QUALITY_LABELS.size() - 1)
	_apply_quality()
	_save_config()


func _on_soft_light(pressed: bool) -> void:
	_soft_light = pressed
	_apply_soft_light()
	_save_config()


## 柔和光影:关掉 = 不烘 AO,只保留原来的面亮度差;切换会重建一次近景分段。
func _apply_soft_light() -> void:
	var chunk := _chunk()
	if chunk == null or not chunk.has_method("set_soft_light"):
		return
	if bool(chunk.call("soft_light_enabled")) != _soft_light:
		chunk.call("set_soft_light", _soft_light)


## 帧率上限 / 垂直同步:直接落到引擎和窗口上,下一帧就生效。
func _apply_perf() -> void:
	Engine.max_fps = int(FPS_CAPS[clampi(_fps_index, 0, FPS_CAPS.size() - 1)])
	DisplayServer.window_set_vsync_mode(
		DisplayServer.VSYNC_ENABLED if _vsync else DisplayServer.VSYNC_DISABLED)


## 画质档:流畅 = 关阴影 + 0.8 渲染缩放;均衡 = 阴影 + 原生分辨率;
## 高清 = 阴影 + 2x MSAA。真机卡不卡先看"当前 xx 帧"这行。
func _apply_quality() -> void:
	var scene := get_tree().current_scene
	if scene != null:
		for node in scene.find_children("*", "DirectionalLight3D", true, false):
			(node as DirectionalLight3D).shadow_enabled = _quality > 0
	var viewport := get_viewport()
	if viewport != null:
		viewport.msaa_3d = Viewport.MSAA_2X if _quality >= 2 \
			else Viewport.MSAA_DISABLED
		viewport.scaling_3d_scale = 0.8 if _quality == 0 else 1.0


func _refresh_sens_label() -> void:
	if _sens_label != null:
		_sens_label.text = "%.1fx" % _sensitivity


func _apply_all() -> void:
	_apply_radius()
	_apply_control()
	_apply_fullscreen()
	_apply_perf()
	_apply_quality()
	_apply_soft_light()


func _apply_radius() -> void:
	var host := get_parent()
	if host != null:
		host.set("渲染距离", float(_radius_index))
	var chunk := _chunk()
	if chunk == null:
		return
	var blocks := int([128, 192, 256, 384, 512][_radius_index - 1])
	if int(chunk.get("_radius")) != blocks:
		chunk.call("set_radius", blocks)


func _apply_control() -> void:
	var host := get_parent()
	if host == null:
		return
	host.set("灵敏度x", _sensitivity)
	host.set("灵敏度y", _sensitivity * (-1.0 if _invert else 1.0))


func _apply_fullscreen() -> void:
	var window := get_window()
	if window == null:
		return
	window.mode = Window.MODE_FULLSCREEN if _fullscreen else Window.MODE_WINDOWED


func _load_config() -> void:
	if _config.load(CONFIG_PATH) != OK:
		return
	_radius_index = clampi(int(_config.get_value("video", "radius", 3)), 1, 5)
	_sensitivity = clampf(
		float(_config.get_value("control", "sensitivity", 1.0)), 0.2, 3.0)
	_invert = bool(_config.get_value("control", "invert", false))
	_fullscreen = bool(_config.get_value("video", "fullscreen", false))
	_fps_index = clampi(int(_config.get_value("video", "fps", 1)), 0, 4)
	_vsync = bool(_config.get_value("video", "vsync", true))
	_quality = clampi(int(_config.get_value("video", "quality", 1)), 0, 2)
	_soft_light = bool(_config.get_value("video", "soft_light", true))


func _save_config() -> void:
	_config.set_value("video", "radius", _radius_index)
	_config.set_value("video", "fullscreen", _fullscreen)
	_config.set_value("video", "fps", _fps_index)
	_config.set_value("video", "vsync", _vsync)
	_config.set_value("video", "quality", _quality)
	_config.set_value("video", "soft_light", _soft_light)
	_config.set_value("control", "sensitivity", _sensitivity)
	_config.set_value("control", "invert", _invert)
	_config.save(CONFIG_PATH)


func _chunk() -> Node:
	var host := get_parent()
	if host == null:
		return null
	for child in host.get_children():
		if child == self:
			continue
		if child.has_method("set_radius") and child.has_method("apply_world"):
			return child
	return null
"""


# ── 天空:蓝天 + 方块云(同样存在 .blend 文本块里)────────────────────
SKY_SCRIPT = """extends Node3D

## 原版天空:把天空盒调成晴天的蓝,再在头顶铺一层方块云。
## 云挂在场景根、不参与碰撞,只负责"像原版"。

const CLOUD_Y := 92.0

var _clouds: MeshInstance3D
var _sky_done := false
var _env: Environment
var _sun: DirectionalLight3D
## 水下(M2b):相机在海平面以下的水里时换成近处水色雾
var _underwater := false


func _physics_process(_delta: float) -> void:
	if not _sky_done:
		_tune_sky()
		return
	_update_underwater()


## 相机没入水面后把雾压近(能看清海底),浮出水面立刻恢复按渲染距离的雾。
## 只切环境雾;天空盒 / 太阳保持原样(水面是一层半透明面片,抬头还是天)。
func _update_underwater() -> void:
	if _env == null or not is_instance_valid(_env):
		return
	var camera := get_viewport().get_camera_3d()
	if camera == null:
		return
	var chunk: Node = null
	var host := get_parent()
	if host != null:
		for child in host.get_children():
			if child.has_method("is_underwater_point"):
				chunk = child
				break
	if chunk == null:
		return
	var inside := bool(chunk.call("is_underwater_point",
		camera.global_position))
	if inside == _underwater:
		return
	_underwater = inside
	if inside:
		_env.fog_light_color = Color(0.10, 0.34, 0.48)
		_env.fog_mode = Environment.FOG_MODE_DEPTH
		_env.fog_depth_begin = 1.0
		_env.fog_depth_end = 22.0
		_env.fog_depth_curve = 1.0
		_env.fog_density = 0.0
	else:
		apply_time()


func _tune_sky() -> void:
	var world := get_tree().current_scene
	if world == null:
		return
	if _clouds == null:
		_build_clouds()
	_find_env()
	if _env == null or _env.sky == null:
		return
	_sky_done = true
	apply_time()


func _find_env() -> void:
	var world := get_tree().current_scene
	if world == null:
		return
	if _env == null or not is_instance_valid(_env):
		for found in world.find_children("*", "WorldEnvironment", true, false):
			var holder := found as WorldEnvironment
			if holder != null and holder.environment != null:
				_env = holder.environment
				break
	if _sun == null or not is_instance_valid(_sun):
		for found in world.find_children("*", "DirectionalLight3D", true, false):
			_sun = found as DirectionalLight3D
			break


## 昼夜时间(1 白天 / 2 黄昏 / 3 夜晚)。新建世界 / 读档后由区块脚本调,
## 玩家在设置里改时间也会走这里 —— 默认第一项和原来的蓝天完全一致。
func apply_time() -> void:
	_find_env()
	if _env == null or _env.sky == null:
		return
	var index := 1
	var host := get_parent()
	if host != null:
		index = int(host.get("昼夜时间"))
	var sky_top := Color(0.30, 0.52, 0.98)
	var horizon := Color(0.60, 0.78, 1.0)
	var ground := Color(0.33, 0.45, 0.66)
	var fog := Color(0.66, 0.80, 0.98)
	var energy := 1.44
	var light := Color(1.0, 1.0, 1.0)
	match index:
		2:
			sky_top = Color(0.30, 0.20, 0.42)
			horizon = Color(0.95, 0.52, 0.32)
			ground = Color(0.28, 0.20, 0.28)
			fog = Color(0.86, 0.56, 0.42)
			energy = 0.8
			light = Color(1.0, 0.72, 0.5)
		3:
			sky_top = Color(0.02, 0.04, 0.12)
			horizon = Color(0.07, 0.11, 0.24)
			ground = Color(0.02, 0.03, 0.07)
			fog = Color(0.05, 0.08, 0.16)
			energy = 0.22
			light = Color(0.55, 0.65, 1.0)
	# 原版不是"淡淡一层雾",而是"渲染距离雾":近处完全清晰,越远越淡,
	# 到 render distance 正好整片融进天际线(起点大约 60% 视距)。
	# 用深度雾把起点 / 终点直接对到渲染距离上;指数雾要么中段就糊,要么
	# 边缘硬切在天空上。
	var radius := 192.0
	if host != null:
		for child in host.get_children():
			if child.has_method("set_radius"):
				var found := float(child.get("_radius"))
				if found > 1.0:
					radius = found
				break
	_env.fog_enabled = true
	_env.fog_light_color = fog
	_env.fog_mode = Environment.FOG_MODE_DEPTH
	_env.fog_depth_begin = maxf(20.0, radius * 0.6)
	_env.fog_depth_end = maxf(40.0, radius)
	_env.fog_depth_curve = 1.4
	# 深度雾下 density 不参与计算,但保持 0 免得别的路径读到旧值
	_env.fog_density = 0.0
	# 原版没有光影包的辉光 / 体积雾,明确关掉(别跟着模板默认值走)
	_env.glow_enabled = false
	_env.volumetric_fog_enabled = false
	if _sun != null:
		_sun.light_energy = energy
		_sun.light_color = light
	var material = _env.sky.sky_material
	# 导出默认给的是 Godot 自带的 ProceduralSkyMaterial(偏灰白的浅蓝),
	# 不是 gk_sky.gdshader —— 两条路都要照顾,否则"改天色"根本没生效。
	if material is ProceduralSkyMaterial:
		var proc := material as ProceduralSkyMaterial
		proc.sky_top_color = sky_top
		proc.sky_horizon_color = horizon
		proc.ground_horizon_color = horizon
		proc.ground_bottom_color = ground
		# 原版太阳是一枚很小的方块,别用默认 30° 的大光斑。
		proc.set("sun_angle_max", 11.0)
		proc.set("sun_curve", 0.05)
	elif material is ShaderMaterial:
		var shader := material as ShaderMaterial
		shader.set_shader_parameter("sky_top", sky_top)
		shader.set_shader_parameter("sky_horizon", horizon)
		shader.set_shader_parameter("sun_size", 0.10)
		shader.set_shader_parameter("sun_dir",
			Vector3(0.36, 0.74, -0.30).normalized())
		shader.set_shader_parameter("cloud_on", 1.0)
		shader.set_shader_parameter("cloud_amount", 0.45)
		shader.set_shader_parameter("cloud_scale", 2.2)


func _build_clouds() -> void:
	var material := StandardMaterial3D.new()
	# 云是半透明的:以前是不透明的大白块,边缘硬得像纸板。
	material.albedo_color = Color(1.0, 1.0, 1.0, 0.88)
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	material.depth_draw_mode = BaseMaterial3D.DEPTH_DRAW_DISABLED
	_clouds = MeshInstance3D.new()
	_clouds.name = "方块云"
	_clouds.mesh = _cloud_mesh()
	_clouds.material_override = material
	_clouds.position = Vector3(0.0, CLOUD_Y, 0.0)
	var world := get_tree().current_scene
	if world == null:
		world = get_parent()
	world.add_child(_clouds)


func _cloud_mesh() -> ArrayMesh:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	# 一朵云 = 3~6 块叠起来的小云团,不再是一层薄板。
	# 以前的厚度只有 0.7~1.8、单片 5~14 宽,远看就是天上飘着的白纸条;
	# 现在单片 3~7 宽、2~4 厚,上下错开 0~3.6 叠成有体积的云,
	# 大小 / 间距 / 高低都按哈希散开。
	for index in range(56):
		var cx := float(_hash(index, 1) % 340 - 170)
		var cz := float(_hash(index, 2) % 340 - 170)
		var pieces := 3 + _hash(index, 3) % 4
		var span := 3.0 + float(_hash(index, 4) % 3)
		for piece in range(pieces):
			var px := cx + float(_hash(index * 7 + piece, 11) % 17 - 8)
			var pz := cz + float(_hash(index * 7 + piece, 12) % 17 - 8)
			var sx := span + float(_hash(index * 7 + piece, 13) % 4)
			var sz := span + float(_hash(index * 7 + piece, 14) % 4)
			var sy := 2.0 + float(_hash(index * 7 + piece, 15) % 3)
			var lift := float(_hash(index * 7 + piece, 16) % 4) * 1.2
			_add_box(st, Vector3(px, lift, pz), Vector3(sx, sy, sz))
	st.generate_normals()
	return st.commit()


func _hash(index: int, salt: int) -> int:
	var n: int = (index * 73856093) ^ (salt * 19349663)
	n = (n ^ (n >> 13)) * 1274126177
	n = n & 0x7fffffff
	return n ^ (n >> 16)


func _add_box(st: SurfaceTool, center: Vector3, size: Vector3) -> void:
	var x := center.x
	var y := center.y
	var z := center.z
	var hx := size.x * 0.5
	var hy := size.y * 0.5
	var hz := size.z * 0.5
	var uv := Vector2.ZERO
	# 只有一种纯白材质,UV 全 0 也无所谓。
	_sky_quad(st, Vector3(x - hx, y + hy, z - hz),
		Vector3(x - hx, y + hy, z + hz), Vector3(x + hx, y + hy, z + hz),
		Vector3(x + hx, y + hy, z - hz), uv)
	_sky_quad(st, Vector3(x - hx, y - hy, z + hz),
		Vector3(x + hx, y - hy, z + hz), Vector3(x + hx, y - hy, z - hz),
		Vector3(x - hx, y - hy, z - hz), uv)
	_sky_quad(st, Vector3(x - hx, y - hy, z + hz),
		Vector3(x - hx, y + hy, z + hz), Vector3(x + hx, y + hy, z + hz),
		Vector3(x + hx, y - hy, z + hz), uv)
	_sky_quad(st, Vector3(x + hx, y - hy, z - hz),
		Vector3(x + hx, y + hy, z - hz), Vector3(x - hx, y + hy, z - hz),
		Vector3(x - hx, y - hy, z - hz), uv)
	_sky_quad(st, Vector3(x + hx, y - hy, z + hz),
		Vector3(x + hx, y + hy, z + hz), Vector3(x + hx, y + hy, z - hz),
		Vector3(x + hx, y - hy, z - hz), uv)
	_sky_quad(st, Vector3(x - hx, y - hy, z - hz),
		Vector3(x - hx, y + hy, z - hz), Vector3(x - hx, y + hy, z + hz),
		Vector3(x - hx, y - hy, z + hz), uv)


func _sky_quad(st: SurfaceTool, a: Vector3, b: Vector3, c: Vector3,
		d: Vector3, uv: Vector2) -> void:
	for point in [a, b, c, a, c, d]:
		st.set_uv(uv)
		st.add_vertex(point)
"""


# ── 手持模型:第一人称右下角举着当前物品(存在 .blend 文本块里)────────
HAND_SCRIPT = """extends Node3D

## 第一人称手持物:相机右下角举着当前选中的方块 / 苹果 / 剑。
## 第两百零九轮起物品由背包脚本决定:这里只读玩家变量『手持物品』
## (grass / stone / … / apple / sword,空串 = 空手)。
## 滚轮换物品、快捷栏的选中框和名字提示都挪到背包脚本里了,
## 这个脚本只管 3D 那只手举着什么。

const BLOCKS := {
	"grass": 0,
	"stone": 1,
	"sand": 2,
	"wood": 3,
	"leaves": 4,
	"table": 5,
	"chest": 6,
}

const TEXTURES := {
	0: "res://art/mc_grass.png",
	1: "res://art/mc_stone.png",
	2: "res://art/mc_sand.png",
	3: "res://art/mc_wood.png",
	4: "res://art/mc_leaves.png",
	5: "res://art/mc_table.png",
	6: "res://art/mc_chest.png",
}

var _camera: Camera3D
var _holder: Node3D
var _item: MeshInstance3D
var _last_kind := "__start__"


func _physics_process(delta: float) -> void:
	var host := get_parent()
	if host == null:
		return
	if not is_instance_valid(_camera):
		_find_camera()
		return
	if _holder != null:
		var view := get_viewport()
		var current := view.get_camera_3d() if view != null else null
		_holder.visible = current != null and current.name == "第一人称相机"
	var kind := str(host.get("手持物品"))
	if kind != _last_kind:
		_last_kind = kind
		_refresh(kind)


func _find_camera() -> void:
	if not is_inside_tree():
		return
	var scene := get_tree().current_scene
	if scene == null:
		return
	var cams: Array = scene.find_children("第一人称相机", "Camera3D", true, false)
	if cams.is_empty():
		return
	_camera = cams[0] as Camera3D
	if _holder == null:
		_holder = Node3D.new()
		_holder.name = "手持模型"
		_holder.position = Vector3(0.33, -0.20, -0.60)
		_holder.rotation_degrees = Vector3(10.0, -40.0, -8.0)
		_camera.add_child(_holder)
		var arm := MeshInstance3D.new()
		var arm_mesh := BoxMesh.new()
		arm_mesh.size = Vector3(0.13, 0.13, 0.36)
		arm.mesh = arm_mesh
		var skin := StandardMaterial3D.new()
		skin.albedo_color = Color(0.88, 0.71, 0.55)
		skin.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		arm.material_override = skin
		arm.position = Vector3(0.02, -0.10, 0.16)
		_holder.add_child(arm)
		_item = MeshInstance3D.new()
		_item.name = "手持物品"
		_holder.add_child(_item)
	_refresh(_last_kind)


func _refresh(kind: String) -> void:
	if _item == null:
		return
	_item.mesh = null
	_item.material_override = null
	_item.rotation = Vector3.ZERO
	_item.position = Vector3.ZERO
	_item.scale = Vector3.ONE
	if BLOCKS.has(kind):
		var index := int(BLOCKS[kind])
		var material := StandardMaterial3D.new()
		if TEXTURES.has(index):
			material.albedo_texture = load(TEXTURES[index])
		material.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
		material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		material.cull_mode = BaseMaterial3D.CULL_DISABLED
		material.vertex_color_use_as_albedo = true
		material.vertex_color_is_srgb = true
		_item.mesh = _block_mesh(index)
		_item.material_override = material
		_item.scale = Vector3(0.17, 0.17, 0.17)
	elif kind == "apple":
		var apple := BoxMesh.new()
		apple.size = Vector3(0.16, 0.16, 0.16)
		_item.mesh = apple
		_item.material_override = _flat(Color(0.78, 0.12, 0.12))
		_item.position = Vector3(0.0, 0.02, 0.0)
	elif kind == "sword":
		var blade := BoxMesh.new()
		blade.size = Vector3(0.05, 0.46, 0.05)
		_item.mesh = blade
		_item.material_override = _flat(Color(0.78, 0.80, 0.84))
		_item.rotation_degrees = Vector3(18.0, 0.0, 28.0)
	elif kind == "plank":
		var plank := BoxMesh.new()
		plank.size = Vector3(0.34, 0.06, 0.20)
		_item.mesh = plank
		_item.material_override = _flat(Color(0.66, 0.50, 0.30))
		_item.rotation_degrees = Vector3(-18.0, 24.0, 0.0)
	elif kind == "stick":
		var stick := BoxMesh.new()
		stick.size = Vector3(0.04, 0.34, 0.04)
		_item.mesh = stick
		_item.material_override = _flat(Color(0.52, 0.38, 0.20))
		_item.rotation_degrees = Vector3(24.0, 0.0, 26.0)
	else:
		_item.visible = false
		return
	_item.visible = true


func _flat(color: Color) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = color
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	return material


func _block_mesh(index: int) -> ArrayMesh:
	var span := 1.0
	var top_tile := 0
	var side_tile := 0
	var bottom_tile := 0
	match index:
		0:
			span = 1.0 / 3.0
			top_tile = 0
			side_tile = 1
			bottom_tile = 2
		3:
			span = 0.5
			top_tile = 1
			side_tile = 0
			bottom_tile = 1
	var t0 := top_tile * span
	var t1 := t0 + span
	var s0 := side_tile * span
	var s1 := s0 + span
	var b0 := bottom_tile * span
	var b1 := b0 + span
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var h := 0.5
	_quad(st, Vector3(-h, h, -h), Vector3(-h, h, h), Vector3(h, h, h),
		Vector3(h, h, -h), Vector2(t0, 0), Vector2(t0, 1),
		Vector2(t1, 1), Vector2(t1, 0), 1.0)
	_quad(st, Vector3(-h, -h, h), Vector3(-h, h, h), Vector3(h, h, h),
		Vector3(h, -h, h), Vector2(s0, 1), Vector2(s0, 0),
		Vector2(s1, 0), Vector2(s1, 1), 0.80)
	_quad(st, Vector3(h, -h, -h), Vector3(h, h, -h), Vector3(-h, h, -h),
		Vector3(-h, -h, -h), Vector2(s0, 1), Vector2(s0, 0),
		Vector2(s1, 0), Vector2(s1, 1), 0.80)
	_quad(st, Vector3(h, -h, h), Vector3(h, h, h), Vector3(h, h, -h),
		Vector3(h, -h, -h), Vector2(s0, 1), Vector2(s0, 0),
		Vector2(s1, 0), Vector2(s1, 1), 0.64)
	_quad(st, Vector3(-h, -h, -h), Vector3(-h, h, -h), Vector3(-h, h, h),
		Vector3(-h, -h, h), Vector2(s0, 1), Vector2(s0, 0),
		Vector2(s1, 0), Vector2(s1, 1), 0.64)
	_quad(st, Vector3(-h, -h, h), Vector3(h, -h, h), Vector3(h, -h, -h),
		Vector3(-h, -h, -h), Vector2(b0, 0), Vector2(b1, 0),
		Vector2(b1, 1), Vector2(b0, 1), 0.5)
	st.generate_normals()
	return st.commit()


func _quad(st: SurfaceTool, a: Vector3, b: Vector3, c: Vector3, d: Vector3,
		ua: Vector2, ub: Vector2, uc: Vector2, ud: Vector2,
		shade: float) -> void:
	var color := Color(shade, shade, shade)
	st.set_color(color)
	st.set_uv(ua)
	st.add_vertex(a)
	st.set_color(color)
	st.set_uv(ub)
	st.add_vertex(b)
	st.set_color(color)
	st.set_uv(uc)
	st.add_vertex(c)
	st.set_color(color)
	st.set_uv(ua)
	st.add_vertex(a)
	st.set_color(color)
	st.set_uv(uc)
	st.add_vertex(c)
	st.set_color(color)
	st.set_uv(ud)
	st.add_vertex(d)
"""


# ── 背包(存在 .blend 文本块里)────────────────────────────────────
INVENTORY_SCRIPT = """extends Node

## 背包(第两百零九轮):36 格(0~8 快捷栏 / 9~35 主背包),单格最多 64。
## 数据、HUD 快捷栏、拖拽界面、挖方块拾取、放置消耗都在这一个脚本里。
##
## 界面:art/ui 的 inventory_bg(16x16 平铺)当面板底、slot.png 当格子,
## 图标用 2 倍大的像素图。打开时暂停世界 + 放出鼠标,关掉解除暂停并锁回
## 鼠标(存档脚本的兜底也会补一次)。
##
## 物品类型 = 地形贴图名:grass / stone / sand / wood / leaves / apple / sword。

const SLOT_COUNT := 36
const HOTBAR_COUNT := 9
const STACK_MAX := 64

const NAMES := {
	"grass": "草方块",
	"stone": "石头方块",
	"sand": "沙子方块",
	"wood": "木头方块",
	"leaves": "树叶方块",
	"apple": "苹果",
	"sword": "剑",
	"plank": "木板",
	"stick": "木棍",
	"table": "工作台",
	"chest": "箱子",
	"tall_grass": "短草",
	"flower_yellow": "蒲公英",
	"flower_red": "虞美人",
	"pork": "猪肉",
}

const ICONS := {
	"grass": "res://art/mc_icon_grass.png",
	"stone": "res://art/mc_icon_stone.png",
	"sand": "res://art/mc_icon_sand.png",
	"wood": "res://art/mc_icon_wood.png",
	"leaves": "res://art/mc_icon_leaves.png",
	"apple": "res://art/mc_icon_apple.png",
	"sword": "res://art/mc_icon_sword.png",
	"plank": "res://art/mc_icon_plank.png",
	"stick": "res://art/mc_icon_stick.png",
	"table": "res://art/mc_icon_table.png",
	"chest": "res://art/mc_icon_chest.png",
	"tall_grass": "res://art/mc_tall_grass.png",
	"flower_yellow": "res://art/mc_flower_yellow.png",
	"flower_red": "res://art/mc_flower_red.png",
	"pork": "res://art/mc_icon_pork.png",
}

const BLOCK_INDEX := {
	"grass": 0,
	"stone": 1,
	"sand": 2,
	"wood": 3,
	"leaves": 4,
	"table": 5,
	"chest": 6,
}

## 合成配方(第两百一十三轮):数量 = 一次产出的个数。
## 只做"木头 -> 木板 -> 木棍 / 工作台 / 箱子"这一条主干,够撑起 M1 的闭环。
const RECIPES := [
	{"out": "plank", "count": 4, "need": {"wood": 1}},
	{"out": "stick", "count": 4, "need": {"plank": 2}},
	{"out": "table", "count": 1, "need": {"plank": 4}},
	{"out": "chest", "count": 1, "need": {"plank": 8}},
]
const CRAFT_SLOTS := 9

const SLOT_SIZE := 40.0
const SLOT_PITCH := 44.0
const GRID_X := 16.0
const GRID_Y := 58.0
const PANEL_W := 424.0
const PANEL_H := 286.0
const HINT_TIME := 1.4
const DROP_DELAY := 1.0
const PICKUP_GAP := 1.3

var _slots: Array = []
var _layer: CanvasLayer
var _root: Control
var _panel: Panel
var _slot_icons: Array = []
var _slot_counts: Array = []
var _drag_icon: TextureRect
var _drag_count: Label
var _hover: Label
var _held_kind := ""
var _held_count := 0
var _open := false
var _hotbar: Control
var _hud_icons: Array = []
var _hud_counts: Array = []
var _hud_ready := false
var _selected: Control
var _hint: Label
var _hint_time := 0.0
var _last_index := -2
var _i_down := false
var _chunk: Node = null
var _drops: Array = []
## 合成界面(第两百一十四轮):3×3 格子 + 产物槽,右侧是背包(和箱子同一套排版)
var _craft_root: Control
var _craft_inputs: Array = []
var _craft_cells: Array = []
var _craft_icons: Array = []
var _craft_counts: Array = []
var _craft_result_cell: Panel
var _craft_result_icon: TextureRect
var _craft_result_count: Label
var _craft_match: Dictionary = {}
var _craft_open := false
## 拖拽:按下时记下来源,松手落在别的格子上就直接放过去
var _drag_tag := ""
var _drag_from := -1
## 箱子(第两百一十三轮):"x,y,z" -> 27 格物品;打开时和背包并排显示
const CHEST_SLOTS := 27
var _chests: Dictionary = {}
var _chest_panel: Panel
var _chest_icons: Array = []
var _chest_counts: Array = []
var _chest_cells: Array = []
var _chest_slots: Array = []
var _chest_open := false
var _chest_key := ""


func _ready() -> void:
	# 背包开着时世界是暂停的:脚本本身必须继续跑
	process_mode = Node.PROCESS_MODE_ALWAYS
	reset_slots()
	_build_ui()
	_build_craft_ui()
	_build_chest_ui()
	call_deferred("_grab_hud")
	call_deferred("_connect_chunk")


func _process(delta: float) -> void:
	if _chunk == null:
		_connect_chunk()
	_poll_selection()
	if _hint_time > 0.0:
		_hint_time -= delta
		if _hint_time <= 0.0 and _hint != null:
			_hint.visible = false
	if _open or _chest_open or _craft_open:
		if _held_count > 0:
			_drag_icon.position = get_viewport().get_mouse_position() + Vector2(14.0, 14.0)
	_tick_drops(delta)
	_poll_toggle()


## I 键用**轮询**而不是事件:真机上键盘事件可能被 GUI 焦点 / 场景里的
## 其它节点吃掉(用户实测"背包没看到"),轮询直接读物理键状态,
## 只要游戏在跑就一定能开。正在输入世界名时不响应,免得打字弹背包。
func _poll_toggle() -> void:
	var down := Input.is_physical_key_pressed(KEY_I)
	if down and not _i_down and not _typing():
		toggle()
	_i_down = down


func _typing() -> bool:
	var focus := get_viewport().gui_get_focus_owner()
	return focus is LineEdit


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		_on_wheel(event as InputEventMouseButton)
		return
	if not (event is InputEventKey):
		return
	var key_event := event as InputEventKey
	if not key_event.pressed or key_event.echo:
		return
	var key := int(key_event.physical_keycode)
	if key == 0:
		key = int(key_event.keycode)
	if _craft_open:
		if key == KEY_ESCAPE:
			close_craft()
			get_viewport().set_input_as_handled()
		return
	if _chest_open:
		if key == KEY_ESCAPE:
			close_chest()
			get_viewport().set_input_as_handled()
		return
	if _open:
		if key == KEY_ESCAPE:
			close()
			get_viewport().set_input_as_handled()
		elif key == KEY_Q and _held_count > 0:
			_drop_held()
			get_viewport().set_input_as_handled()
		return
	if _menu_visible():
		return
	var digit := _digit_of(key)
	if digit >= 0:
		_select(digit)
		get_viewport().set_input_as_handled()


func _digit_of(key: int) -> int:
	if key >= KEY_1 and key <= KEY_9:
		return int(key - KEY_1)
	return -1


## 滚轮换快捷栏(0~8 循环;菜单 / 背包开着时不响应)
func _on_wheel(event: InputEventMouseButton) -> void:
	if _open or _chest_open or not event.pressed:
		return
	if event.button_index != MOUSE_BUTTON_WHEEL_UP \\
			and event.button_index != MOUSE_BUTTON_WHEEL_DOWN:
		return
	if _menu_visible():
		return
	var host := get_parent()
	if host == null:
		return
	var step := 1
	if event.button_index == MOUSE_BUTTON_WHEEL_UP:
		step = -1
	var index := wrapi(int(host.get("方块序号")) + step, 0, HOTBAR_COUNT)
	_select(index)


func _menu_visible() -> bool:
	var scene := get_tree().current_scene
	if scene == null:
		return false
	for menu_name in ["世界菜单", "暂停菜单"]:
		var menu := scene.find_child(menu_name, true, false)
		if menu is CanvasItem and (menu as CanvasItem).visible:
			return true
	return false


func toggle() -> void:
	if _open:
		close()
		return
	if _chest_open:
		close_chest()
	if _craft_open:
		close_craft()
	open()


func open() -> void:
	if _open or get_tree().paused or _menu_visible():
		return
	_open = true
	_root.visible = true
	get_tree().paused = true
	Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
	if _hint != null:
		_hint.visible = false
	_refresh_inventory()
	_update_drag()


func close() -> void:
	if not _open:
		return
	# 手里还捏着的那一堆先放回背包,别因为关界面把东西弄丢
	if _held_count > 0:
		add_item(_held_kind, _held_count)
	_held_kind = ""
	_held_count = 0
	_open = false
	_root.visible = false
	_update_drag()
	get_tree().paused = false
	Input.mouse_mode = Input.MOUSE_MODE_CAPTURED


# ── 合成(第两百一十三轮)──────────────────────────────────────────
func toggle_craft() -> void:
	if _craft_open:
		close_craft()
		return
	if _chest_open:
		close_chest()
	if _open:
		close()
	open_craft()


func open_craft() -> void:
	if _craft_open or get_tree().paused or _menu_visible():
		return
	_craft_open = true
	# 和箱子一样:左边界面、右边背包,背包面板往右挪半个身位
	_root.visible = true
	if _craft_root != null:
		_craft_root.visible = true
	_shift_inventory(true)
	get_tree().paused = true
	Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
	_refresh_craft()
	_update_drag()


func close_craft() -> void:
	if not _craft_open:
		return
	# 格子里的材料 / 手里捏着的,全部放回背包,关界面不吞东西
	_return_craft_inputs()
	if _held_count > 0:
		var left := add_item(_held_kind, _held_count)
		if left > 0:
			# 背包塞不下就掉在脚边(和背包界面一样)
			_held_count = left
			_drop_held()
		else:
			_held_kind = ""
			_held_count = 0
	_craft_open = false
	if _craft_root != null:
		_craft_root.visible = false
	_shift_inventory(false)
	_root.visible = false
	_update_drag()
	get_tree().paused = false
	Input.mouse_mode = Input.MOUSE_MODE_CAPTURED
	_refresh_inventory()


func _return_craft_inputs() -> void:
	for index in range(CRAFT_SLOTS):
		var slot = _craft_inputs[index]
		if slot is Dictionary:
			var left := add_item(str(slot["kind"]), int(slot["count"]))
			# 背包满了就留在格子里,下次打开还在,别凭空消失
			if left <= 0:
				_craft_inputs[index] = null
			else:
				slot["count"] = left
	_craft_match = {}


## 背包里某样东西还有多少(配方判定 / 扣料)
func total_of(kind: String) -> int:
	var total := 0
	for slot in _slots:
		if slot is Dictionary and str(slot["kind"]) == kind:
			total += int(slot["count"])
	return total


func _count_in(container: Array, kind: String) -> int:
	var total := 0
	for slot in container:
		if slot is Dictionary and str(slot["kind"]) == kind:
			total += int(slot["count"])
	return total


## 3×3 无序配方:按表里顺序取第一条"材料够"的(木头优先,木板次之)
func _match_recipe() -> Dictionary:
	for recipe in RECIPES:
		var need: Dictionary = recipe["need"]
		var ok := true
		for kind in need.keys():
			if _count_in(_craft_inputs, str(kind)) < int(need[kind]):
				ok = false
				break
		if ok:
			return recipe
	return {}


func _take_craft_need(need: Dictionary) -> void:
	for kind in need.keys():
		var left := int(need[kind])
		for index in range(CRAFT_SLOTS):
			if left <= 0:
				break
			var slot = _craft_inputs[index]
			if not (slot is Dictionary) or str(slot["kind"]) != str(kind):
				continue
			var take := mini(left, int(slot["count"]))
			var rest := int(slot["count"]) - take
			if rest <= 0:
				_craft_inputs[index] = null
			else:
				slot["count"] = rest
			left -= take


## 点产物格:像原版一样先拿到手上,再点背包格放下
func _on_craft_result_input(event: InputEvent) -> void:
	if not (event is InputEventMouseButton):
		return
	var mouse := event as InputEventMouseButton
	if not mouse.pressed or mouse.button_index != MOUSE_BUTTON_LEFT:
		return
	_take_craft_result()


func _take_craft_result() -> void:
	if _craft_match.is_empty():
		return
	var out := str(_craft_match["out"])
	var count := int(_craft_match["count"])
	if _held_count > 0:
		if _held_kind != out or _held_count + count > STACK_MAX:
			return
	else:
		_held_kind = out
	_held_count += count
	_take_craft_need(_craft_match["need"])
	_refresh_all()


func _refresh_craft() -> void:
	_craft_match = _match_recipe()
	if _craft_icons.is_empty():
		return
	for index in range(CRAFT_SLOTS):
		var icon: TextureRect = _craft_icons[index]
		var count: Label = _craft_counts[index]
		var slot = _craft_inputs[index] if index < _craft_inputs.size() else null
		if slot is Dictionary:
			icon.texture = _icon_texture(str(slot["kind"]))
			icon.visible = icon.texture != null
			count.text = str(int(slot["count"])) if int(slot["count"]) > 1 else ""
		else:
			icon.texture = null
			icon.visible = false
			count.text = ""
	if _craft_result_icon == null:
		return
	if _craft_match.is_empty():
		_craft_result_icon.texture = null
		_craft_result_icon.visible = false
		if _craft_result_count != null:
			_craft_result_count.text = ""
	else:
		_craft_result_icon.texture = _icon_texture(str(_craft_match["out"]))
		_craft_result_icon.visible = _craft_result_icon.texture != null
		if _craft_result_count != null:
			_craft_result_count.text = str(int(_craft_match["count"]))


func _refresh_all() -> void:
	_refresh_inventory()
	_refresh_craft()
	_refresh_chest()
	_update_drag()


func _on_craft_slot_input(event: InputEvent, index: int) -> void:
	if not (event is InputEventMouseButton):
		return
	var mouse := event as InputEventMouseButton
	var right := mouse.button_index == MOUSE_BUTTON_RIGHT
	if mouse.button_index != MOUSE_BUTTON_LEFT and not right:
		return
	if not mouse.pressed:
		if not right:
			_finish_drag(_craft_inputs, index, "craft")
		return
	_click_container(_craft_inputs, index, right,
		mouse.shift_pressed, "craft")
	_drag_tag = "craft" if _held_count > 0 and not right else ""
	_drag_from = index if _drag_tag != "" else -1
	_refresh_all()


## 拖拽落点:按下时拿起,松手落在别的格子上就直接放过去
func _finish_drag(container: Array, index: int, tag: String) -> void:
	if _drag_from >= 0 and _held_count > 0 \
			and not (tag == _drag_tag and index == _drag_from):
		_click_container(container, index)
		_refresh_all()
	_drag_from = -1
	_drag_tag = ""


func _hover_craft(index: int, on: bool) -> void:
	if index < 0 or index >= _craft_cells.size():
		return
	var cell: Panel = _craft_cells[index]
	if cell == null or not is_instance_valid(cell):
		return
	cell.add_theme_stylebox_override("panel",
		_slot_hover_style() if on else _slot_style())


# ── 箱子(第两百一十三轮)──────────────────────────────────────────
## 27 格空箱子。按方块位置做 key,同一个箱子的东西读写档都落在这一份上。
func _new_chest() -> Array:
	var slots: Array = []
	for _i in range(CHEST_SLOTS):
		slots.append(null)
	return slots


func chest_key_of(node: Node3D) -> String:
	# 读档重建出来的方块带 gk_cell / gk_level(位置是格子中心);
	# 现放的资产方块没有 meta,位置是格子底。两条路统一成 "x,层,z"。
	if node.has_meta("gk_cell") and node.has_meta("gk_level"):
		var cell: Vector2i = node.get_meta("gk_cell")
		return "%d,%d,%d" % [cell.x, int(node.get_meta("gk_level")), cell.y]
	var p := node.global_position
	return "%d,%d,%d" % [int(round(p.x - 0.5)), int(round(p.y)),
		int(round(p.z - 0.5))]


## 准星指着的方块是不是箱子(资产实例的网格子节点叫『箱子_源』,
## 读档重建的方块没有这个名字,靠 gk_kind 标记认)。
## 注意只能看自己这一层:往上找到场景根再 find_child 会把"资产源"里的
## 箱子源模型也当成箱子(任何方块都会命中)。
func _chest_node_of(node) -> Node3D:
	var current = node
	var depth := 0
	while current != null and depth < 3:
		if current is Node3D:
			var body := current as Node3D
			if str(body.get_meta("gk_kind", "")) == "chest" \
					or str(body.name).begins_with("箱子"):
				return body
			if depth == 0 and body.find_child("箱子_源", true, false) != null:
				return body
		current = current.get_parent()
		depth += 1
	return null


## 右键点在箱子上时由区块脚本调用:开了返回 true(这次不放方块)。
## 原版没有单独的开箱键,右键对着箱子就是开箱。
func try_open_chest() -> bool:
	if _open or _craft_open or _chest_open or get_tree().paused or _menu_visible():
		return false
	var chest := _chest_under_crosshair()
	if chest == null:
		return false
	open_chest(chest_key_of(chest))
	return true


## F/右键共用的射线:相机前方 4.5 格,打中箱子就返回它
func _chest_under_crosshair() -> Node3D:
	var cam := get_viewport().get_camera_3d()
	if cam == null:
		return null
	var from := cam.global_position
	var to := from - cam.global_transform.basis.z * 4.5
	var query := PhysicsRayQueryParameters3D.create(from, to, 1)
	query.collide_with_areas = false
	var hit := cam.get_world_3d().direct_space_state.intersect_ray(query)
	if hit.is_empty():
		return null
	return _chest_node_of(hit.get("collider"))


## 右键点在工作台上时由区块脚本调用:开了返回 true(这次不放方块)。
func try_open_craft() -> bool:
	if _open or _craft_open or _chest_open or get_tree().paused or _menu_visible():
		return false
	var cam := get_viewport().get_camera_3d()
	if cam == null:
		return false
	var from := cam.global_position
	var to := from - cam.global_transform.basis.z * 4.5
	var query := PhysicsRayQueryParameters3D.create(from, to, 1)
	query.collide_with_areas = false
	var hit := cam.get_world_3d().direct_space_state.intersect_ray(query)
	if hit.is_empty():
		return false
	if _block_kind_of(hit.get("collider")) != "table":
		return false
	open_craft()
	return true


## 认准星指着的方块类型(地形带 gk_kind;现放的资产看名字 / 模型名)
func _block_kind_of(node) -> String:
	var current = node
	var depth := 0
	while current != null and depth < 3:
		if current is Node3D:
			var body := current as Node3D
			var meta := str(body.get_meta("gk_kind", ""))
			if not meta.is_empty():
				return meta
			var label := str(body.name)
			if label.begins_with("工作台"):
				return "table"
			if label.begins_with("箱子"):
				return "chest"
			if depth == 0 and body.find_child("工作台_源", true, false) != null:
				return "table"
		current = current.get_parent()
		depth += 1
	return ""


func open_chest(key: String) -> void:
	if _chest_open or get_tree().paused or _menu_visible():
		return
	_chest_key = key
	if not _chests.has(key):
		_chests[key] = _new_chest()
	_chest_slots = _chests[key]
	_chest_open = true
	_root.visible = true
	if _chest_panel != null:
		_chest_panel.visible = true
	_shift_inventory(true)
	get_tree().paused = true
	Input.mouse_mode = Input.MOUSE_MODE_VISIBLE
	_refresh_chest()
	_update_drag()


func close_chest() -> void:
	if not _chest_open:
		return
	# 手里那堆放回背包,别把箱子里的东西弄丢
	if _held_count > 0:
		var left := add_item(_held_kind, _held_count)
		_held_count = left
		if left <= 0:
			_held_kind = ""
	_chest_open = false
	if _chest_panel != null:
		_chest_panel.visible = false
	_shift_inventory(false)
	_root.visible = false
	_update_drag()
	get_tree().paused = false
	Input.mouse_mode = Input.MOUSE_MODE_CAPTURED


## 存 / 读档:箱子内容跟着世界走(旧存档没有这一块 -> 全是空箱子)
func export_chests() -> Dictionary:
	# 对外用 [类型, 数量] 的数组形式,和 export_slots 一样方便 JSON 落盘
	var out: Dictionary = {}
	for key in _chests.keys():
		var slots: Array = _chests[key]
		var packed: Array = []
		for slot in slots:
			if slot is Dictionary:
				packed.append([str(slot["kind"]), int(slot["count"])])
			else:
				packed.append(null)
		out[str(key)] = packed
	return out


func import_chests(data) -> void:
	_chests = {}
	if not (data is Dictionary):
		return
	for key in data.keys():
		var slots = data[key]
		if not (slots is Array):
			continue
		var fixed: Array = []
		for index in range(CHEST_SLOTS):
			var slot = slots[index] if index < slots.size() else null
			if slot is Array and slot.size() >= 2 and NAMES.has(str(slot[0])):
				fixed.append({"kind": str(slot[0]),
					"count": int(slot[1])})
			else:
				fixed.append(null)
		_chests[str(key)] = fixed


func _refresh_chest() -> void:
	if _chest_icons.is_empty():
		return
	for index in range(CHEST_SLOTS):
		var icon: TextureRect = _chest_icons[index]
		var count: Label = _chest_counts[index]
		var slot = _chest_slots[index] if index < _chest_slots.size() else null
		if slot is Dictionary:
			icon.texture = _icon_texture(str(slot["kind"]))
			icon.visible = icon.texture != null
			count.text = str(int(slot["count"])) if int(slot["count"]) > 1 else ""
		else:
			icon.texture = null
			icon.visible = false
			count.text = ""


func _on_chest_slot_input(event: InputEvent, index: int) -> void:
	if not (event is InputEventMouseButton):
		return
	var mouse := event as InputEventMouseButton
	var right := mouse.button_index == MOUSE_BUTTON_RIGHT
	if mouse.button_index != MOUSE_BUTTON_LEFT and not right:
		return
	if not mouse.pressed:
		if not right:
			_finish_drag(_chest_slots, index, "chest")
		return
	_click_container(_chest_slots, index, right,
		mouse.shift_pressed, "chest")
	_drag_tag = "chest" if _held_count > 0 and not right else ""
	_drag_from = index if _drag_tag != "" else -1
	_refresh_all()


## 悬停高亮:和原版一样,鼠标压在哪一格哪一格亮起来
func _hover_chest(index: int, on: bool) -> void:
	if index < 0 or index >= _chest_cells.size():
		return
	var cell: Panel = _chest_cells[index]
	if cell == null or not is_instance_valid(cell):
		return
	cell.add_theme_stylebox_override("panel",
		_slot_hover_style() if on else _slot_style())


func _slot_hover_style() -> StyleBox:
	var flat := StyleBoxFlat.new()
	flat.bg_color = Color(1.0, 1.0, 1.0, 0.20)
	flat.border_color = Color(1.0, 1.0, 1.0, 0.9)
	flat.set_border_width_all(2)
	return flat


## 背包面板和箱子面板并排:开箱子时把背包往右挪半个面板
func _shift_inventory(shifted: bool) -> void:
	if _panel == null:
		return
	var delta := PANEL_W * 0.5 + 8.0 if shifted else 0.0
	_panel.offset_left = -PANEL_W * 0.5 + delta
	_panel.offset_right = PANEL_W * 0.5 + delta


# ── 数据 ───────────────────────────────────────────────────────────
func reset_slots() -> void:
	_slots = []
	for i in range(SLOT_COUNT):
		_slots.append(null)
	var start := ["grass", "stone", "sand", "wood", "leaves"]
	for i in range(start.size()):
		_slots[i] = {"kind": start[i], "count": STACK_MAX}
	_slots[5] = {"kind": "apple", "count": 8}
	_slots[6] = {"kind": "sword", "count": 1}
	_held_kind = ""
	_held_count = 0
	# 新世界:箱子内容一并清空(读档时由 import_chests 覆盖)
	_chests = {}
	_chest_slots = []
	# 合成格子里的材料也清掉,别让上一个世界的木头漏过来
	_craft_inputs = []
	for i in range(CRAFT_SLOTS):
		_craft_inputs.append(null)
	_craft_match = {}
	_drag_tag = ""
	_drag_from = -1
	_refresh_hud()
	_apply_selection(false)


func _kind_at(index: int) -> String:
	if index < 0 or index >= _slots.size():
		return ""
	var slot = _slots[index]
	if slot is Dictionary:
		return str(slot["kind"])
	return ""


func _count_at(index: int) -> int:
	if index < 0 or index >= _slots.size():
		return 0
	var slot = _slots[index]
	if slot is Dictionary:
		return int(slot["count"])
	return 0


## 捡到东西:先补已有堆,再找空位。返回没塞下的数量(0 = 全收下)。
func add_item(kind: String, count: int) -> int:
	if not NAMES.has(kind) or count <= 0:
		return count
	var left := count
	for index in range(SLOT_COUNT):
		if left <= 0:
			break
		var slot = _slots[index]
		if slot is Dictionary and str(slot["kind"]) == kind \\
				and int(slot["count"]) < STACK_MAX:
			var room := STACK_MAX - int(slot["count"])
			var put := mini(room, left)
			slot["count"] = int(slot["count"]) + put
			left -= put
	for index in range(SLOT_COUNT):
		if left <= 0:
			break
		if _slots[index] == null:
			var put := mini(STACK_MAX, left)
			_slots[index] = {"kind": kind, "count": put}
			left -= put
	_refresh_hud()
	_refresh_inventory()
	_apply_selection(false)
	return left


## 用掉手上一个(放置方块 / 吃苹果)。创造模式不消耗。
func consume_current() -> void:
	var host := get_parent()
	if host == null:
		return
	if bool(host.get("创造模式")):
		return
	var index := clampi(int(host.get("方块序号")), 0, HOTBAR_COUNT - 1)
	var slot = _slots[index]
	if not (slot is Dictionary):
		return
	var left := int(slot["count"]) - 1
	if left <= 0:
		_slots[index] = null
	else:
		slot["count"] = left
	_refresh_hud()
	_refresh_inventory()
	_apply_selection(false)


func export_slots() -> Array:
	var out: Array = []
	for slot in _slots:
		if slot is Dictionary:
			out.append([str(slot["kind"]), int(slot["count"])])
		else:
			out.append(null)
	return out


func import_slots(data) -> void:
	if not (data is Array):
		return
	_slots = []
	for index in range(SLOT_COUNT):
		var entry = data[index] if index < data.size() else null
		if entry is Array and entry.size() >= 2 and NAMES.has(str(entry[0])):
			_slots.append({"kind": str(entry[0]),
				"count": clampi(int(entry[1]), 1, STACK_MAX)})
		else:
			_slots.append(null)
	_held_kind = ""
	_held_count = 0
	_refresh_hud()
	_refresh_inventory()
	_apply_selection(false)


func _category(kind: String) -> int:
	if BLOCK_INDEX.has(kind):
		return 1
	if kind == "apple":
		return 2
	if kind == "sword":
		return 3
	return 0


# ── 快捷栏 / 手持(玩家变量是节点图和手持脚本的输入)──────────────────
func _poll_selection() -> void:
	var host := get_parent()
	if host == null:
		return
	if int(host.get("方块序号")) != _last_index:
		_apply_selection(true)


func _select(index: int) -> void:
	var host := get_parent()
	if host == null:
		return
	host.set("方块序号", float(clampi(index, 0, HOTBAR_COUNT - 1)))
	_apply_selection(true)


func _apply_selection(show_hint: bool) -> void:
	var host := get_parent()
	if host == null:
		return
	if not _hud_ready:
		_grab_hud()
	var index := clampi(int(host.get("方块序号")), 0, HOTBAR_COUNT - 1)
	_last_index = index
	var kind := _kind_at(index)
	host.set("手持名", NAMES.get(kind, "空"))
	host.set("手持物品", kind)
	host.set("手持类别", float(_category(kind)))
	host.set("手持方块", float(BLOCK_INDEX.get(kind, 0)))
	if _selected != null and is_instance_valid(_selected):
		_selected.position = Vector2(12.0 + float(index) * 76.0, 11.0)
	if show_hint and _hint != null:
		_hint.text = NAMES.get(kind, "空")
		_hint.visible = true
		_hint_time = HINT_TIME


func _grab_hud() -> void:
	if _hud_ready:
		return
	var scene := get_tree().current_scene
	if scene == null:
		return
	_hotbar = scene.find_child("物品栏", true, false) as Control
	if _hotbar == null:
		return
	for index in range(HOTBAR_COUNT):
		var icon := _hotbar.find_child(
			"槽图标%d" % (index + 1), true, false) as TextureRect
		_hud_icons.append(icon)
		var label := Label.new()
		label.name = "数量%d" % (index + 1)
		label.position = Vector2(12.0 + float(index) * 76.0 + 22.0, 31.0)
		label.size = Vector2(30.0, 16.0)
		label.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
		label.add_theme_font_size_override("font_size", 12)
		label.add_theme_color_override("font_color", Color(1, 1, 1, 0.92))
		label.mouse_filter = Control.MOUSE_FILTER_IGNORE
		_hotbar.add_child(label)
		_hud_counts.append(label)
	var marks: Array = scene.find_children("选中框", "Panel", true, false)
	if not marks.is_empty():
		_selected = marks[0] as Control
	var labels: Array = scene.find_children("物品名提示", "Label", true, false)
	if not labels.is_empty():
		_hint = labels[0] as Label
	_hud_ready = true
	_refresh_hud()


func _refresh_hud() -> void:
	for index in range(_hud_icons.size()):
		var icon = _hud_icons[index]
		if icon == null or not is_instance_valid(icon):
			continue
		var kind := _kind_at(index)
		var texture := _icon_texture(kind)
		icon.texture = texture
		icon.visible = texture != null
		var label = _hud_counts[index]
		if label != null and is_instance_valid(label):
			var count := _count_at(index)
			label.text = str(count) if count > 1 else ""


# ── 挖方块拾取:区块脚本挖掉一块时发信号 ─────────────────────────────
func _connect_chunk() -> void:
	if _chunk != null and is_instance_valid(_chunk):
		return
	var host := get_parent()
	if host == null:
		return
	for child in host.get_children():
		if child.has_signal("block_mined"):
			_chunk = child
			child.block_mined.connect(_on_block_mined)
			return


func _on_block_mined(kind: String) -> void:
	if NAMES.has(kind):
		add_item(kind, 1)


# ── 界面(运行时搭:遮罩 + 九宫格面板 + 4x9 格子)────────────────────
func _icon_texture(kind: String) -> Texture2D:
	if kind == "" or not ICONS.has(kind):
		return null
	var path: String = ICONS[kind]
	if not ResourceLoader.exists(path):
		return null
	return load(path)


func _slot_style() -> StyleBox:
	var path := "res://art/ui/slot.png"
	if ResourceLoader.exists(path):
		var style := StyleBoxTexture.new()
		style.texture = load(path)
		style.region_rect = Rect2(0.0, 0.0, 20.0, 20.0)
		style.texture_margin_left = 4.0
		style.texture_margin_right = 4.0
		style.texture_margin_top = 4.0
		style.texture_margin_bottom = 4.0
		return style
	var flat := StyleBoxFlat.new()
	flat.bg_color = Color(0.55, 0.55, 0.58, 0.35)
	flat.border_width_left = 2
	flat.border_width_right = 2
	flat.border_width_top = 2
	flat.border_width_bottom = 2
	flat.border_color = Color(0.78, 0.78, 0.80, 0.55)
	return flat


func _panel_style() -> StyleBox:
	var path := "res://art/ui/inventory_bg.png"
	if ResourceLoader.exists(path):
		var style := StyleBoxTexture.new()
		style.texture = load(path)
		style.region_rect = Rect2(0.0, 0.0, 16.0, 16.0)
		style.texture_margin_left = 4.0
		style.texture_margin_right = 4.0
		style.texture_margin_top = 4.0
		style.texture_margin_bottom = 4.0
		style.axis_stretch_horizontal = StyleBoxTexture.AXIS_STRETCH_MODE_TILE
		style.axis_stretch_vertical = StyleBoxTexture.AXIS_STRETCH_MODE_TILE
		return style
	var flat := StyleBoxFlat.new()
	flat.bg_color = Color(0.12, 0.10, 0.09, 0.97)
	flat.border_width_left = 3
	flat.border_width_right = 3
	flat.border_width_top = 3
	flat.border_width_bottom = 3
	flat.border_color = Color(0.42, 0.38, 0.34, 1.0)
	return flat


func _build_ui() -> void:
	_layer = CanvasLayer.new()
	_layer.name = "背包层"
	_layer.layer = 26
	_layer.process_mode = Node.PROCESS_MODE_ALWAYS
	add_child(_layer)
	_root = Control.new()
	_root.name = "背包界面"
	_root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_root.visible = false
	_layer.add_child(_root)
	var shade := ColorRect.new()
	shade.name = "背包遮罩"
	shade.color = Color(0.0, 0.0, 0.0, 0.45)
	shade.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	shade.mouse_filter = Control.MOUSE_FILTER_STOP
	shade.gui_input.connect(_on_shade_input)
	_root.add_child(shade)
	var panel := Panel.new()
	panel.name = "背包面板"
	panel.set_anchors_preset(Control.PRESET_CENTER)
	panel.offset_left = -PANEL_W * 0.5
	panel.offset_right = PANEL_W * 0.5
	panel.offset_top = -PANEL_H * 0.5
	panel.offset_bottom = PANEL_H * 0.5
	panel.add_theme_stylebox_override("panel", _panel_style())
	_root.add_child(panel)
	_panel = panel
	var title := Label.new()
	title.name = "背包标题"
	title.text = "背包"
	title.position = Vector2(GRID_X, 12.0)
	title.size = Vector2(200.0, 36.0)
	title.add_theme_font_size_override("font_size", 22)
	title.add_theme_color_override("font_color", Color(0.55, 1.0, 0.75))
	panel.add_child(title)
	for index in range(SLOT_COUNT):
		var row := int(index / 9)
		var col := index % 9
		var cell := Panel.new()
		cell.name = "背包格%d" % (index + 1)
		cell.position = Vector2(GRID_X + float(col) * SLOT_PITCH,
			GRID_Y + float(row) * SLOT_PITCH)
		cell.size = Vector2(SLOT_SIZE, SLOT_SIZE)
		cell.add_theme_stylebox_override("panel", _slot_style())
		cell.mouse_filter = Control.MOUSE_FILTER_STOP
		cell.gui_input.connect(_on_slot_input.bind(index))
		cell.mouse_entered.connect(_on_slot_hover.bind(index))
		panel.add_child(cell)
		var icon := TextureRect.new()
		icon.name = "背包图标%d" % (index + 1)
		icon.position = Vector2(4.0, 4.0)
		icon.size = Vector2(32.0, 32.0)
		icon.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		icon.stretch_mode = TextureRect.STRETCH_SCALE
		icon.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
		icon.mouse_filter = Control.MOUSE_FILTER_IGNORE
		cell.add_child(icon)
		var count := Label.new()
		count.name = "背包数量%d" % (index + 1)
		count.position = Vector2(14.0, 22.0)
		count.size = Vector2(24.0, 16.0)
		count.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
		count.add_theme_font_size_override("font_size", 13)
		count.add_theme_color_override("font_color", Color(1, 1, 1, 0.95))
		count.mouse_filter = Control.MOUSE_FILTER_IGNORE
		cell.add_child(count)
		_slot_icons.append(icon)
		_slot_counts.append(count)
	_hover = Label.new()
	_hover.name = "背包悬停名"
	_hover.text = "鼠标放到格子上看物品名"
	_hover.position = Vector2(GRID_X, 236.0)
	_hover.size = Vector2(392.0, 22.0)
	_hover.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_hover.add_theme_font_size_override("font_size", 15)
	_hover.add_theme_color_override("font_color", Color(0.85, 0.92, 0.86))
	panel.add_child(_hover)
	var hint := Label.new()
	hint.name = "背包说明"
	hint.text = "I 关闭 · 左键拿放 · 右键拆半/放1 · Shift 快移 · Q 丢弃"
	hint.position = Vector2(GRID_X, 260.0)
	hint.size = Vector2(392.0, 18.0)
	hint.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	hint.add_theme_font_size_override("font_size", 11)
	hint.add_theme_color_override("font_color", Color(1, 1, 1, 0.62))
	panel.add_child(hint)
	# 合成不再占独立按键:面板标题栏右边一颗『合成』按钮(原版也是从背包进)
	var craft_button := Button.new()
	craft_button.name = "打开合成"
	craft_button.text = "合成"
	craft_button.position = Vector2(PANEL_W - 100.0, 10.0)
	craft_button.size = Vector2(84.0, 30.0)
	craft_button.focus_mode = Control.FOCUS_NONE
	craft_button.pressed.connect(_open_craft_from_bag)
	panel.add_child(craft_button)
	_drag_icon = TextureRect.new()
	_drag_icon.name = "背包拖动"
	# 拖着的图标要压在所有面板上面,不然经过箱子 / 合成面板时会被盖住
	_drag_icon.z_index = 10
	_drag_icon.size = Vector2(32.0, 32.0)
	_drag_icon.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_drag_icon.stretch_mode = TextureRect.STRETCH_SCALE
	_drag_icon.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	_drag_icon.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_drag_icon.visible = false
	_root.add_child(_drag_icon)
	_drag_count = Label.new()
	_drag_count.name = "拖动数量"
	_drag_count.position = Vector2(14.0, 16.0)
	_drag_count.size = Vector2(26.0, 16.0)
	_drag_count.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	_drag_count.add_theme_font_size_override("font_size", 13)
	_drag_count.add_theme_color_override("font_color", Color(1, 1, 1, 0.95))
	_drag_count.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_drag_icon.add_child(_drag_count)
	_refresh_inventory()


## 合成界面(第两百一十四轮):左边 3×3 材料格 + 产物槽,右边背包并排。
## 和原版一样:点物品拿在手上,放进格子看产物,点产物取回背包。
func _build_craft_ui() -> void:
	var panel := Panel.new()
	panel.name = "合成界面"
	panel.set_anchors_preset(Control.PRESET_CENTER)
	panel.offset_left = -PANEL_W - 8.0
	panel.offset_right = -8.0
	panel.offset_top = -PANEL_H * 0.5
	panel.offset_bottom = PANEL_H * 0.5
	panel.add_theme_stylebox_override("panel", _panel_style())
	panel.visible = false
	_root.add_child(panel)
	_craft_root = panel
	var title := Label.new()
	title.name = "合成标题"
	title.text = "合成"
	title.position = Vector2(GRID_X, 12.0)
	title.size = Vector2(200.0, 36.0)
	title.add_theme_font_size_override("font_size", 22)
	title.add_theme_color_override("font_color", Color(0.55, 1.0, 0.75))
	panel.add_child(title)
	for index in range(CRAFT_SLOTS):
		var row := int(index / 3)
		var col := index % 3
		var cell := Panel.new()
		cell.name = "合成格%d" % (index + 1)
		cell.position = Vector2(GRID_X + float(col) * SLOT_PITCH,
			GRID_Y + float(row) * SLOT_PITCH)
		cell.size = Vector2(SLOT_SIZE, SLOT_SIZE)
		cell.add_theme_stylebox_override("panel", _slot_style())
		cell.mouse_filter = Control.MOUSE_FILTER_STOP
		cell.gui_input.connect(_on_craft_slot_input.bind(index))
		cell.mouse_entered.connect(_hover_craft.bind(index, true))
		cell.mouse_exited.connect(_hover_craft.bind(index, false))
		panel.add_child(cell)
		_craft_cells.append(cell)
		var icon := TextureRect.new()
		icon.name = "合成图标%d" % (index + 1)
		icon.position = Vector2(4.0, 4.0)
		icon.size = Vector2(32.0, 32.0)
		icon.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		icon.stretch_mode = TextureRect.STRETCH_SCALE
		icon.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
		icon.mouse_filter = Control.MOUSE_FILTER_IGNORE
		cell.add_child(icon)
		var count := Label.new()
		count.name = "合成数量%d" % (index + 1)
		count.position = Vector2(14.0, 22.0)
		count.size = Vector2(24.0, 16.0)
		count.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
		count.add_theme_font_size_override("font_size", 13)
		count.add_theme_color_override("font_color", Color(1, 1, 1, 0.95))
		count.mouse_filter = Control.MOUSE_FILTER_IGNORE
		cell.add_child(count)
		_craft_icons.append(icon)
		_craft_counts.append(count)
	var arrow := Label.new()
	arrow.name = "合成箭头"
	arrow.text = "→"
	arrow.position = Vector2(GRID_X + SLOT_PITCH * 3.0 + 2.0,
		GRID_Y + SLOT_PITCH + 2.0)
	arrow.size = Vector2(30.0, 36.0)
	arrow.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	arrow.add_theme_font_size_override("font_size", 24)
	arrow.add_theme_color_override("font_color", Color(1, 1, 1, 0.8))
	arrow.mouse_filter = Control.MOUSE_FILTER_IGNORE
	panel.add_child(arrow)
	var result := Panel.new()
	result.name = "合成产物"
	result.position = Vector2(GRID_X + SLOT_PITCH * 3.0 + 34.0,
		GRID_Y + SLOT_PITCH)
	result.size = Vector2(SLOT_SIZE, SLOT_SIZE)
	result.add_theme_stylebox_override("panel", _slot_style())
	result.mouse_filter = Control.MOUSE_FILTER_STOP
	result.gui_input.connect(_on_craft_result_input)
	result.mouse_entered.connect(_hover_result.bind(true))
	result.mouse_exited.connect(_hover_result.bind(false))
	panel.add_child(result)
	_craft_result_cell = result
	var result_icon := TextureRect.new()
	result_icon.name = "合成产物图标"
	result_icon.position = Vector2(4.0, 4.0)
	result_icon.size = Vector2(32.0, 32.0)
	result_icon.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	result_icon.stretch_mode = TextureRect.STRETCH_SCALE
	result_icon.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
	result_icon.mouse_filter = Control.MOUSE_FILTER_IGNORE
	result.add_child(result_icon)
	_craft_result_icon = result_icon
	var result_count := Label.new()
	result_count.name = "合成产物数量"
	result_count.position = Vector2(14.0, 22.0)
	result_count.size = Vector2(24.0, 16.0)
	result_count.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	result_count.add_theme_font_size_override("font_size", 13)
	result_count.add_theme_color_override("font_color", Color(1, 1, 1, 0.95))
	result_count.mouse_filter = Control.MOUSE_FILTER_IGNORE
	result.add_child(result_count)
	_craft_result_count = result_count
	var note := Label.new()
	note.name = "合成说明"
	note.text = "Esc 关闭 · 点背包物品放进 3×3 格 · 点产物取出"
	note.position = Vector2(GRID_X, 224.0)
	note.size = Vector2(PANEL_W - GRID_X * 2.0, 18.0)
	note.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	note.add_theme_font_size_override("font_size", 11)
	note.add_theme_color_override("font_color", Color(1, 1, 1, 0.62))
	panel.add_child(note)
	_refresh_craft()


func _hover_result(on: bool) -> void:
	if _craft_result_cell == null or not is_instance_valid(_craft_result_cell):
		return
	_craft_result_cell.add_theme_stylebox_override("panel",
		_slot_hover_style() if on else _slot_style())


func _open_craft_from_bag() -> void:
	if _open:
		close()
	open_craft()


## 箱子界面(第两百一十三轮):左边 27 格箱子,右边是背包(打开时背包让位)。
## 格子样式 / 点击规则和背包完全一致,只是数据存在 _chests 里。
func _build_chest_ui() -> void:
	var panel := Panel.new()
	panel.name = "箱子面板"
	panel.set_anchors_preset(Control.PRESET_CENTER)
	panel.offset_left = -PANEL_W - 8.0
	panel.offset_right = -8.0
	panel.offset_top = -PANEL_H * 0.5
	panel.offset_bottom = PANEL_H * 0.5
	panel.add_theme_stylebox_override("panel", _panel_style())
	panel.visible = false
	_root.add_child(panel)
	_chest_panel = panel
	var title := Label.new()
	title.name = "箱子标题"
	title.text = "箱子"
	title.position = Vector2(GRID_X, 12.0)
	title.size = Vector2(200.0, 36.0)
	title.add_theme_font_size_override("font_size", 22)
	title.add_theme_color_override("font_color", Color(0.55, 1.0, 0.75))
	panel.add_child(title)
	for index in range(CHEST_SLOTS):
		var row := int(index / 9)
		var col := index % 9
		var cell := Panel.new()
		cell.name = "箱子格%d" % (index + 1)
		cell.position = Vector2(GRID_X + float(col) * SLOT_PITCH,
			GRID_Y + float(row) * SLOT_PITCH)
		cell.size = Vector2(SLOT_SIZE, SLOT_SIZE)
		cell.add_theme_stylebox_override("panel", _slot_style())
		cell.mouse_filter = Control.MOUSE_FILTER_STOP
		cell.gui_input.connect(_on_chest_slot_input.bind(index))
		cell.mouse_entered.connect(_hover_chest.bind(index, true))
		cell.mouse_exited.connect(_hover_chest.bind(index, false))
		_chest_cells.append(cell)
		panel.add_child(cell)
		var icon := TextureRect.new()
		icon.name = "箱子图标%d" % (index + 1)
		icon.position = Vector2(4.0, 4.0)
		icon.size = Vector2(32.0, 32.0)
		icon.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		icon.stretch_mode = TextureRect.STRETCH_SCALE
		icon.texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST
		icon.mouse_filter = Control.MOUSE_FILTER_IGNORE
		cell.add_child(icon)
		var count := Label.new()
		count.name = "箱子数量%d" % (index + 1)
		count.position = Vector2(14.0, 22.0)
		count.size = Vector2(24.0, 16.0)
		count.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
		count.add_theme_font_size_override("font_size", 13)
		count.add_theme_color_override("font_color", Color(1, 1, 1, 0.95))
		count.mouse_filter = Control.MOUSE_FILTER_IGNORE
		cell.add_child(count)
		_chest_icons.append(icon)
		_chest_counts.append(count)
	var note := Label.new()
	note.name = "箱子说明"
	note.text = "Esc 关闭 · 左键存取 · 背包在右侧"
	note.position = Vector2(GRID_X, 220.0)
	note.size = Vector2(PANEL_W - GRID_X * 2.0, 18.0)
	note.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	note.add_theme_font_size_override("font_size", 11)
	note.add_theme_color_override("font_color", Color(1, 1, 1, 0.62))
	panel.add_child(note)
	_refresh_chest()


func _refresh_inventory() -> void:
	for index in range(SLOT_COUNT):
		if index >= _slot_icons.size():
			break
		var icon: TextureRect = _slot_icons[index]
		var count: Label = _slot_counts[index]
		var slot = _slots[index] if index < _slots.size() else null
		if slot is Dictionary:
			icon.texture = _icon_texture(str(slot["kind"]))
			icon.visible = icon.texture != null
			count.text = str(int(slot["count"])) if int(slot["count"]) > 1 else ""
		else:
			icon.texture = null
			icon.visible = false
			count.text = ""
	_refresh_craft()


func _on_slot_hover(index: int) -> void:
	if _hover == null:
		return
	var slot = _slots[index]
	if slot is Dictionary:
		_hover.text = "%s × %d" % [
			NAMES.get(str(slot["kind"]), "物品"), int(slot["count"])]
	else:
		_hover.text = "空"


func _on_slot_input(event: InputEvent, index: int) -> void:
	if not (event is InputEventMouseButton):
		return
	var mouse := event as InputEventMouseButton
	var right := mouse.button_index == MOUSE_BUTTON_RIGHT
	if mouse.button_index != MOUSE_BUTTON_LEFT and not right:
		return
	if not mouse.pressed:
		if not right:
			_finish_drag(_slots, index, "bag")
		return
	_click_container(_slots, index, right, mouse.shift_pressed, "bag")
	_drag_tag = "bag" if _held_count > 0 and not right else ""
	_drag_from = index if _drag_tag != "" else -1
	_refresh_all()


## 拿起 / 放下 / 同一堆合并 / 不同物品交换(和原版左键一致)
func _click_slot(index: int) -> void:
	if index < 0 or index >= SLOT_COUNT:
		return
	_click_container(_slots, index)
	_refresh_inventory()
	_update_drag()


## 同一套点击规则用在任意容器上(背包 / 箱子 / 合成格)。
## 右键 = 拆半 / 放 1 个;Shift + 左键 = 整堆快捷移动。
func _click_container(container: Array, index: int, right := false,
		shift := false, tag := "bag") -> void:
	if index < 0 or index >= container.size():
		return
	if shift:
		_quick_move(container, index, tag)
		return
	var slot = container[index]
	if right:
		_click_right(container, index, slot)
		return
	if _held_count <= 0:
		if slot is Dictionary:
			_held_kind = str(slot["kind"])
			_held_count = int(slot["count"])
			container[index] = null
	elif slot is Dictionary and str(slot["kind"]) == _held_kind:
		var room := STACK_MAX - int(slot["count"])
		var put := mini(room, _held_count)
		slot["count"] = int(slot["count"]) + put
		_held_count -= put
		if _held_count <= 0:
			_held_kind = ""
	else:
		var old_kind := ""
		var old_count := 0
		if slot is Dictionary:
			old_kind = str(slot["kind"])
			old_count = int(slot["count"])
		container[index] = {"kind": _held_kind, "count": _held_count}
		_held_kind = old_kind
		_held_count = old_count


## 右键:空手拿起一半(奇数取较大一半);手里有堆就放下 1 个。
func _click_right(container: Array, index: int, slot) -> void:
	if _held_count <= 0:
		if not (slot is Dictionary):
			return
		var count := int(slot["count"])
		var take := int(ceil(float(count) / 2.0))
		_held_kind = str(slot["kind"])
		_held_count = take
		var rest := count - take
		if rest <= 0:
			container[index] = null
		else:
			slot["count"] = rest
		return
	if slot == null:
		container[index] = {"kind": _held_kind, "count": 1}
		_held_count -= 1
	elif str(slot["kind"]) == _held_kind \
			and int(slot["count"]) < STACK_MAX:
		slot["count"] = int(slot["count"]) + 1
		_held_count -= 1
	if _held_count <= 0:
		_held_kind = ""


## Shift + 左键:整堆移到"对面容器"。背包右键已打开箱子 / 合成时优先给它们;
## 两个面板都没开时,在快捷栏和主背包之间来回搬(原版行为)。
func _quick_move(container: Array, index: int, tag: String) -> void:
	if _held_count > 0:
		return
	var slot = container[index]
	if not (slot is Dictionary):
		return
	var kind := str(slot["kind"])
	var count := int(slot["count"])
	var left := count
	if tag == "bag":
		if _chest_open:
			left = _push_into(_chest_slots, kind, count)
		elif _craft_open:
			left = _push_into(_craft_inputs, kind, count)
		elif index < HOTBAR_COUNT:
			left = _push_range(_slots, kind, count, HOTBAR_COUNT, SLOT_COUNT)
		else:
			left = _push_range(_slots, kind, count, 0, HOTBAR_COUNT)
	else:
		left = _push_into(_slots, kind, count)
	if left <= 0:
		container[index] = null
	else:
		slot["count"] = left


func _push_into(container: Array, kind: String, count: int) -> int:
	return _push_range(container, kind, count, 0, container.size())


## 先合并同类、再找空槽;放不下的数量原样返回(留在原格)。
func _push_range(container: Array, kind: String, count: int,
		start: int, end: int) -> int:
	var left := count
	var limit := mini(end, container.size())
	for index in range(start, limit):
		if left <= 0:
			break
		var slot = container[index]
		if slot is Dictionary and str(slot["kind"]) == kind \
				and int(slot["count"]) < STACK_MAX:
			var put := mini(STACK_MAX - int(slot["count"]), left)
			slot["count"] = int(slot["count"]) + put
			left -= put
	for index in range(start, limit):
		if left <= 0:
			break
		if container[index] == null:
			var put := mini(STACK_MAX, left)
			container[index] = {"kind": kind, "count": put}
			left -= put
	return left


func _update_drag() -> void:
	if _drag_icon == null:
		return
	if _held_count > 0 and (_open or _chest_open or _craft_open):
		_drag_icon.visible = true
		_drag_icon.texture = _icon_texture(_held_kind)
		_drag_count.text = str(_held_count) if _held_count > 1 else ""
		_drag_icon.position = get_viewport().get_mouse_position() + Vector2(14.0, 14.0)
	else:
		_drag_icon.visible = false


## 点面板外面:把手里那堆丢到世界里(走过去还能捡回来)
func _on_shade_input(event: InputEvent) -> void:
	if not (event is InputEventMouseButton):
		return
	var mouse := event as InputEventMouseButton
	if not mouse.pressed or mouse.button_index != MOUSE_BUTTON_LEFT:
		return
	if _held_count > 0:
		_drop_held()


func _drop_held() -> void:
	if _held_count <= 0:
		return
	var host := get_parent() as Node3D
	var scene := get_tree().current_scene
	if host == null or scene == null:
		return
	var node := Node3D.new()
	node.name = "掉落物"
	node.process_mode = Node.PROCESS_MODE_ALWAYS
	var sprite := Sprite3D.new()
	var texture := _icon_texture(_held_kind)
	if texture != null:
		sprite.texture = texture
	sprite.pixel_size = 0.02
	sprite.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	sprite.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
	sprite.shaded = false
	node.add_child(sprite)
	var forward := -host.global_transform.basis.z
	node.global_position = host.global_position + forward * 2.4 \\
		+ Vector3(0.0, 0.7, 0.0)
	scene.add_child(node)
	_drops.append({"node": node, "kind": _held_kind,
		"count": _held_count, "t": 0.0})
	_held_kind = ""
	_held_count = 0
	_update_drag()


func _tick_drops(delta: float) -> void:
	if _drops.is_empty():
		return
	var host := get_parent() as Node3D
	var alive: Array = []
	for drop in _drops:
		var node = drop.get("node")
		if node == null or not is_instance_valid(node):
			continue
		drop["t"] = float(drop["t"]) + delta
		if node is Node3D:
			(node as Node3D).rotate_y(delta * 1.6)
		if host != null and float(drop["t"]) > DROP_DELAY \
				and not _open and not _chest_open:
			var gap: float = (node as Node3D).global_position.distance_to(
				host.global_position + Vector3(0.0, 0.8, 0.0))
			if gap < PICKUP_GAP:
				var left := add_item(str(drop["kind"]), int(drop["count"]))
				if left <= 0:
					(node as Node3D).queue_free()
					continue
				drop["count"] = left
		alive.append(drop)
	_drops = alive
"""


# ── 选中方块的黑框(存在 .blend 文本块里)────────────────────────────
OUTLINE_SCRIPT = """extends Node3D

## 原版最显眼的一处细节:准星指着的方块会被一圈黑框描出来。
## 做法是每帧从第一人称相机打一条 5 米射线(只看方块层),命中就把
## 一个"12 条细边拼成的线框盒"挪到那一格;没指到东西就藏起来。

const REACH := 5.0
const LINE := 0.02

var _camera: Camera3D
var _box: MeshInstance3D
var _last := Vector3(1e9, 1e9, 1e9)


func _ready() -> void:
	_box = MeshInstance3D.new()
	_box.name = "选中框"
	_box.mesh = _outline_mesh()
	var material := StandardMaterial3D.new()
	material.albedo_color = Color(0.05, 0.05, 0.05)
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	_box.material_override = material
	_box.visible = false
	# 不要往 current_scene 挂:这个脚本是节点图在搭场景的过程中加载的,
	# 父节点还在 busy setting up children,add_child 会被引擎拒绝
	# (实测刷屏:add_child() failed: Parent node is busy setting up children)。
	# 挂在自己下面 + top_level,位置仍然用世界坐标,不跟玩家走。
	_box.top_level = true
	add_child(_box)


func _physics_process(_delta: float) -> void:
	if not is_inside_tree():
		return
	if _box == null or not _box.is_inside_tree():
		return
	if not is_instance_valid(_camera):
		_find_camera()
		return
	var view := get_viewport()
	var current := view.get_camera_3d() if view != null else null
	if current == null or current.name != "第一人称相机":
		_box.visible = false
		return
	var from := _camera.global_position
	var to := from - _camera.global_transform.basis.z * REACH
	var query := PhysicsRayQueryParameters3D.create(from, to, 1)
	# 花草是无碰撞的 Area3D,但准星指着时要和方块一样描边
	query.collide_with_areas = true
	var owner_body := _camera.get_parent()
	if owner_body is CollisionObject3D:
		query.exclude = [(owner_body as CollisionObject3D).get_rid()]
	var hit := get_world_3d().direct_space_state.intersect_ray(query)
	if hit.is_empty():
		_box.visible = false
		return
	var solid = hit["collider"]
	var plant: bool = solid is Area3D and solid.get_meta("gk_plant", false)
	if not (solid is StaticBody3D) and not plant:
		_box.visible = false
		return
	var point: Vector3 = hit["position"]
	var normal: Vector3 = hit["normal"]
	var center := Vector3.ZERO
	if plant:
		# 花草是 1 格高的十字面片:选中框套整格,不按命中面的半格回退
		var cell: Vector2i = solid.get_meta("gk_cell")
		center = Vector3(cell.x + 0.5,
			float(int(solid.get_meta("gk_level", 0))) + 0.5, cell.y + 0.5)
	else:
		var inside := point - normal * 0.5
		center = Vector3(floor(inside.x) + 0.5, floor(inside.y) + 0.5,
			floor(inside.z) + 0.5)
	if center != _last:
		_last = center
		_box.global_position = center
	_box.visible = true


func _find_camera() -> void:
	if not is_inside_tree():
		return
	var scene := get_tree().current_scene
	if scene == null:
		return
	var cams: Array = scene.find_children("第一人称相机", "Camera3D", true, false)
	if cams.is_empty():
		return
	_camera = cams[0] as Camera3D


func _outline_mesh() -> ArrayMesh:
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	var h := 0.5 + LINE * 0.5
	var span := 1.0 + LINE * 2.0
	for y in [-h, h]:
		for z in [-h, h]:
			_bar(st, Vector3(0.0, y, z), Vector3(span, LINE, LINE))
	for x in [-h, h]:
		for z in [-h, h]:
			_bar(st, Vector3(x, 0.0, z), Vector3(LINE, span, LINE))
	for x in [-h, h]:
		for y in [-h, h]:
			_bar(st, Vector3(x, y, 0.0), Vector3(LINE, LINE, span))
	st.generate_normals()
	return st.commit()


func _bar(st: SurfaceTool, center: Vector3, size: Vector3) -> void:
	var hx := size.x * 0.5
	var hy := size.y * 0.5
	var hz := size.z * 0.5
	var x := center.x
	var y := center.y
	var z := center.z
	var p := [
		Vector3(x - hx, y - hy, z - hz), Vector3(x + hx, y - hy, z - hz),
		Vector3(x + hx, y - hy, z + hz), Vector3(x - hx, y - hy, z + hz),
		Vector3(x - hx, y + hy, z - hz), Vector3(x + hx, y + hy, z - hz),
		Vector3(x + hx, y + hy, z + hz), Vector3(x - hx, y + hy, z + hz),
	]
	for face in [[0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 5, 4],
			[2, 3, 7, 6], [1, 2, 6, 5], [3, 0, 4, 7]]:
		for index in [face[0], face[1], face[2],
				face[0], face[2], face[3]]:
			st.set_uv(Vector2.ZERO)
			st.add_vertex(p[index])
"""


def log(message):
    print(f"[我的世界] {message}")


def godot_to_blender(x, y, z):
    """Godot(x, y, z) -> Blender(x, z, -y)。"""
    return (x, -z, y)


# ==========================================================================
# 像素贴图(纯 Python 写 PNG,不依赖 PIL)
# ==========================================================================
def _clamp01(value):
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def _png_chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))


def _write_png(path, rows):
    """把 [[(r, g, b), ...], ...] 写成 RGB PNG(第一行 = 图片最上面一行)。"""
    height = len(rows)
    width = len(rows[0])
    raw = bytearray()
    for row in rows:
        raw.append(0)
        for red, green, blue in row:
            raw.extend((int(round(red * 255)), int(round(green * 255)),
                        int(round(blue * 255))))
    payload = (b"\x89PNG\r\n\x1a\n"
               + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height,
                                                 8, 2, 0, 0, 0))
               + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
               + _png_chunk(b"IEND", b""))
    with open(path, "wb") as handle:
        handle.write(payload)


def _write_png_rgba(path, rows):
    """把 [[(r, g, b, a), ...], ...] 写成带透明的 PNG(物品栏图标用)。"""
    height = len(rows)
    width = len(rows[0])
    raw = bytearray()
    for row in rows:
        raw.append(0)
        for red, green, blue, alpha in row:
            raw.extend((int(round(red * 255)), int(round(green * 255)),
                        int(round(blue * 255)), int(round(alpha * 255))))
    payload = (b"\x89PNG\r\n\x1a\n"
               + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height,
                                                 8, 6, 0, 0, 0))
               + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
               + _png_chunk(b"IEND", b""))
    with open(path, "wb") as handle:
        handle.write(payload)


def _noise_tile(seed, base, spread):
    rng = random.Random(seed)
    return [[tuple(_clamp01(channel + rng.uniform(-spread, spread))
                   for channel in base) for _ in range(TEX_SIZE)]
            for _ in range(TEX_SIZE)]


def _grass_top_tile():
    # 原版草地是"整体均匀的绿 + 很轻的颗粒",不要大块深绿斑。
    rows = _noise_tile(11, (0.36, 0.64, 0.26), 0.055)
    rng = random.Random(12)
    for y in range(TEX_SIZE):
        for x in range(TEX_SIZE):
            if rng.random() < 0.06:
                rows[y][x] = (0.30, 0.58, 0.22)
    return rows


def _dirt_tile():
    rows = _noise_tile(51, (0.47, 0.34, 0.22), 0.07)
    rng = random.Random(52)
    for y in range(TEX_SIZE):
        for x in range(TEX_SIZE):
            if rng.random() < 0.08:
                rows[y][x] = (0.38, 0.27, 0.17)
    return rows


def _grass_side_tile():
    rows = _dirt_tile()
    green = _grass_top_tile()
    for x in range(TEX_SIZE):
        band = 3 + (x * 7 + 3) % 3
        for y in range(band):
            rows[y][x] = green[y][x]
        if (x * 5 + 1) % 4 == 0:
            rows[band][x] = green[band][x]
    return rows


def _stone_tile():
    rows = _noise_tile(31, (0.53, 0.53, 0.56), 0.06)
    rng = random.Random(32)
    for y in range(TEX_SIZE):
        for x in range(TEX_SIZE):
            if rng.random() < 0.12:
                rows[y][x] = (0.44, 0.44, 0.48)
    return rows


def _sand_tile():
    rows = _noise_tile(41, (0.86, 0.80, 0.58), 0.05)
    rng = random.Random(42)
    for y in range(TEX_SIZE):
        for x in range(TEX_SIZE):
            if rng.random() < 0.10:
                rows[y][x] = (0.78, 0.71, 0.50)
    return rows


def _snow_tile():
    rows = _noise_tile(71, (0.93, 0.95, 0.99), 0.03)
    rng = random.Random(72)
    for y in range(TEX_SIZE):
        for x in range(TEX_SIZE):
            if rng.random() < 0.08:
                rows[y][x] = (0.82, 0.88, 0.95)
    return rows


def _ore_tile(seed, spots):
    """石头底 + 几团矿点:煤是近黑的块,铁是偏橙的颗粒。"""
    rows = _stone_tile()
    rng = random.Random(seed)
    for _ in range(6):
        cx = rng.randrange(2, TEX_SIZE - 1)
        cy = rng.randrange(2, TEX_SIZE - 1)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if rng.random() < 0.55:
                    rows[(cy + dy) % TEX_SIZE][(cx + dx) % TEX_SIZE] = spots
    return rows


def _wood_side_tile():
    # 原版橡木侧面是偏灰的棕,不是红木;竖纹只作很轻的深浅,再压 3 道深线。
    rows = _noise_tile(61, (0.56, 0.44, 0.29), 0.025)
    for x in range(TEX_SIZE):
        shade = 0.90 + 0.10 * ((x * 3) % 4) / 3.0
        for y in range(TEX_SIZE):
            rows[y][x] = tuple(_clamp01(channel * shade)
                               for channel in rows[y][x])
    for x in (2, 7, 12):
        for y in range(TEX_SIZE):
            rows[y][x] = (0.44, 0.34, 0.21)
    return rows


def _wood_top_tile():
    rows = _noise_tile(71, (0.64, 0.50, 0.28), 0.03)
    for y in range(TEX_SIZE):
        for x in range(TEX_SIZE):
            ring = max(abs(x - 7), abs(y - 7))
            if ring == 0:
                rows[y][x] = (0.42, 0.31, 0.16)
            elif ring % 2 == 0:
                rows[y][x] = tuple(_clamp01(channel * 0.88)
                                   for channel in rows[y][x])
    return rows


def _plank_tile():
    """木板:四条横板 + 板缝,和原版"木板"一个观感(合成台 / 箱子的底料)。"""
    rows = _noise_tile(81, (0.66, 0.52, 0.31), 0.03)
    for y in range(TEX_SIZE):
        for x in range(TEX_SIZE):
            # 每 4 行一块板,板与板之间压一条深缝
            if y % 4 == 3:
                rows[y][x] = (0.46, 0.34, 0.18)
            elif (x + y * 3) % 13 == 0:
                rows[y][x] = (0.58, 0.44, 0.25)
    return rows


def _table_tile():
    """工作台:木板底 + 深色边框 + 台面上的九宫格。"""
    rows = _plank_tile()
    for y in range(TEX_SIZE):
        for x in range(TEX_SIZE):
            if x in (0, TEX_SIZE - 1) or y in (0, TEX_SIZE - 1):
                rows[y][x] = (0.34, 0.24, 0.12)
            if x % 5 == 2 or y % 5 == 2:
                rows[y][x] = (0.40, 0.29, 0.15)
    return rows


def _chest_tile():
    """箱子:深色木箱 + 四边木框 + 正面的铁扣。"""
    rows = _plank_tile()
    for y in range(TEX_SIZE):
        for x in range(TEX_SIZE):
            rows[y][x] = tuple(_clamp01(channel * 0.82) for channel in rows[y][x])
            if x in (0, 1, TEX_SIZE - 2, TEX_SIZE - 1) \
                    or y in (0, 1, TEX_SIZE - 2, TEX_SIZE - 1):
                rows[y][x] = (0.36, 0.25, 0.12)
    for y in range(6, 10):
        for x in range(6, 10):
            rows[y][x] = (0.78, 0.70, 0.42)
    for y in range(10, 14):
        rows[y][8] = (0.60, 0.52, 0.30)
    return rows


def _crack_tile(stage):
    """16×16 破坏裂纹:stage 1~10 逐级加密,背景透明,贴在方块表面。"""
    rows = [[(0.0, 0.0, 0.0, 0.0) for _ in range(TEX_SIZE)]
            for _ in range(TEX_SIZE)]
    rng = random.Random(900 + stage)
    center = TEX_SIZE // 2
    for _line in range(stage * 2):
        angle = rng.uniform(0.0, math.tau)
        x = float(center) + rng.uniform(-2.0, 2.0)
        y = float(center) + rng.uniform(-2.0, 2.0)
        length = rng.randint(3, 6) + stage // 2
        for step in range(length):
            xi = int(round(x))
            yi = int(round(y))
            if 0 <= xi < TEX_SIZE and 0 <= yi < TEX_SIZE:
                rows[yi][xi] = (0.05, 0.04, 0.04, 0.85)
                if step % 2 == 0 and xi + 1 < TEX_SIZE:
                    rows[yi][xi + 1] = (0.10, 0.09, 0.09, 0.55)
            x += math.cos(angle)
            y += math.sin(angle)
    return rows


def _stick_icon():
    """木棍:两根斜放的棕色细棍(和剑一个斜向)。"""
    rows = [[(0.0, 0.0, 0.0, 0.0) for _ in range(TEX_SIZE)]
            for _ in range(TEX_SIZE)]
    wood = (0.55, 0.39, 0.20, 1.0)
    dark = (0.42, 0.29, 0.14, 1.0)
    for step in range(10):
        rows[12 - step][3 + step] = wood
        rows[13 - step][3 + step] = dark
    for step in range(7):
        rows[6 - step][8 + step] = wood
        rows[7 - step][8 + step] = dark
    return rows


def _leaves_tile():
    rows = _noise_tile(81, (0.23, 0.56, 0.24), 0.10)
    rng = random.Random(82)
    for y in range(TEX_SIZE):
        for x in range(TEX_SIZE):
            if rng.random() < 0.16:
                rows[y][x] = (0.13, 0.38, 0.15)
    return rows


def _atlas_rows(tiles):
    """把几张 16×16 横向拼成一张图(草: 顶 / 侧 / 土;木: 侧 / 顶)。"""
    return [[pixel for tile in tiles for pixel in tile[y]]
            for y in range(TEX_SIZE)]


# ── 物品栏缩略图(16×16 画好,再按 2 倍放大成 32×32)──────────────────
# 原版物品栏是"物品图",不是字。方块直接拿现有的像素贴图当图标;
# 苹果 / 剑没有方块贴图,这里单独画 16×16 的小图(带透明背景)。
ICON_SCALE = 2


def _scale_rows(rows, factor):
    """最近邻放大:每个像素变成 factor×factor 的方块(像素画不糊)。"""
    out = []
    for row in rows:
        wide = [tuple(pixel) for pixel in row for _ in range(factor)]
        for _ in range(factor):
            out.append(list(wide))
    return out


def _rgba_tile(tile):
    return [[(red, green, blue, 1.0) for red, green, blue in row]
            for row in tile]


def _apple_icon():
    """红苹果:圆身 + 高光 + 棕色梗 + 一片叶子,其余透明。"""
    rows = [[(0.0, 0.0, 0.0, 0.0) for _ in range(TEX_SIZE)]
            for _ in range(TEX_SIZE)]
    body = (0.78, 0.11, 0.13, 1.0)
    dark = (0.60, 0.07, 0.09, 1.0)
    light = (0.95, 0.34, 0.32, 1.0)
    stem = (0.34, 0.22, 0.10, 1.0)
    leaf = (0.24, 0.64, 0.26, 1.0)
    for y in range(TEX_SIZE):
        for x in range(TEX_SIZE):
            dx = (x - 7.5) / 5.4
            dy = (y - 9.6) / 4.6
            reach = dx * dx + dy * dy
            if reach <= 1.0:
                rows[y][x] = dark if reach > 0.72 else body
    for x, y in ((5, 7), (6, 7), (5, 8)):
        rows[y][x] = light
    for y in range(3, 6):
        rows[y][7] = stem
    for x, y in ((8, 3), (9, 2), (9, 3)):
        rows[y][x] = leaf
    return rows


def _pork_icon():
    """猪肉(第一批):粉红肉块 + 浅色脂肪边 + 一小截骨头,其余透明。"""
    rows = [[(0.0, 0.0, 0.0, 0.0) for _ in range(TEX_SIZE)]
            for _ in range(TEX_SIZE)]
    meat = (0.92, 0.52, 0.55, 1.0)
    dark = (0.80, 0.36, 0.40, 1.0)
    fat = (0.98, 0.82, 0.80, 1.0)
    bone = (0.96, 0.94, 0.86, 1.0)
    for y in range(4, 12):
        for x in range(3, 12):
            rows[y][x] = meat
    for y in range(4, 12):
        rows[y][3] = fat
    for x in range(3, 12):
        rows[4][x] = fat
    for x, y in ((7, 7), (8, 7), (9, 7), (7, 8), (8, 8), (9, 8),
                 (8, 9), (9, 10)):
        rows[y][x] = dark
    for x, y in ((12, 8), (13, 8), (13, 9), (12, 9), (13, 10), (12, 10)):
        rows[y][x] = bone
    return rows


def _sword_icon():
    """铁剑:斜的灰白剑身 + 剑尖 + 护手 + 深色剑柄,其余透明。"""
    rows = [[(0.0, 0.0, 0.0, 0.0) for _ in range(TEX_SIZE)]
            for _ in range(TEX_SIZE)]
    blade = (0.78, 0.81, 0.86, 1.0)
    edge = (0.96, 0.98, 1.0, 1.0)
    guard = (0.56, 0.42, 0.20, 1.0)
    grip = (0.34, 0.24, 0.14, 1.0)
    for step in range(8):
        x = 5 + step
        y = 10 - step
        rows[y][x] = blade
        rows[y][x + 1] = edge
    rows[2][13] = edge
    for x, y in ((3, 12), (4, 13), (4, 12)):
        rows[y][x] = guard
    for x, y in ((2, 14), (1, 15)):
        rows[y][x] = grip
    return rows


def _plant_tile(kind):
    """花草贴图(16×16,透明底):同一张图既画 3D 十字面片,也当物品图标。"""
    rows = [[(0.0, 0.0, 0.0, 0.0) for _ in range(TEX_SIZE)]
            for _ in range(TEX_SIZE)]
    stem = (0.26, 0.55, 0.20, 1.0)
    leaf = (0.32, 0.64, 0.24, 1.0)
    if kind == "tall_grass":
        # 短草:五根高矮不一的草叶,底部聚在一起
        blades = ((7, 15, 7, 5), (6, 15, 5, 8), (8, 15, 9, 7),
                  (7, 15, 4, 10), (8, 15, 11, 10))
        for x0, y0, x1, y1 in blades:
            steps = max(abs(x1 - x0), abs(y1 - y0))
            for step in range(steps + 1):
                x = x0 + int(round((x1 - x0) * step / max(1, steps)))
                y = y0 + int(round((y1 - y0) * step / max(1, steps)))
                rows[y][x] = leaf if step % 3 else stem
        return rows
    # 花:一根茎 + 两片叶 + 顶上一朵花。
    # 花头要够大才认得出来:16×16 里 4 像素宽放到一格方块上只有 0.25 格,
    # 远看就是一根带色的小棍。改成 6 像素宽、5 行高(≈0.4 格),才像原版的花。
    for y in range(7, 16):
        rows[y][7] = stem
        rows[y][8] = stem
    for x, y in ((5, 12), (6, 12), (9, 11), (10, 11), (5, 13), (10, 12)):
        rows[y][x] = leaf
    if kind == "flower_yellow":
        head = (0.95, 0.84, 0.20, 1.0)
        core = (0.86, 0.62, 0.12, 1.0)
    else:
        head = (0.82, 0.16, 0.18, 1.0)
        core = (0.96, 0.42, 0.38, 1.0)
    for x in (6, 7, 8, 9):
        rows[3][x] = head
    for x in (5, 6, 7, 8, 9, 10):
        rows[4][x] = head
        rows[5][x] = head
    for x in (5, 6, 8, 9, 10):
        rows[6][x] = head
    for x in (6, 7, 8, 9):
        rows[7][x] = head
    for x, y in ((7, 4), (8, 4), (7, 5), (8, 5), (7, 6), (8, 6)):
        rows[y][x] = core
    return rows


def ensure_item_icons():
    """生成物品栏图标(顺序和 BLOCK_KINDS 一致),返回 {名字: 路径}。"""
    os.makedirs(TEXTURE_DIR, exist_ok=True)
    tiles = {
        "mc_icon_grass": _rgba_tile(_grass_side_tile()),
        "mc_icon_stone": _rgba_tile(_stone_tile()),
        "mc_icon_sand": _rgba_tile(_sand_tile()),
        "mc_icon_wood": _rgba_tile(_wood_side_tile()),
        "mc_icon_leaves": _rgba_tile(_leaves_tile()),
        "mc_icon_apple": _apple_icon(),
        "mc_icon_sword": _sword_icon(),
        "mc_icon_pork": _pork_icon(),
        # 第两百一十三轮:合成出来的物品也各有一张 16×16 图标
        "mc_icon_plank": _rgba_tile(_plank_tile()),
        "mc_icon_stick": _stick_icon(),
        "mc_icon_table": _rgba_tile(_table_tile()),
        "mc_icon_chest": _rgba_tile(_chest_tile()),
        # 第两百一十六轮:植被(3D 十字面片和物品图标共用同一张透明 PNG)
        "mc_tall_grass": _plant_tile("tall_grass"),
        "mc_flower_yellow": _plant_tile("flower_yellow"),
        "mc_flower_red": _plant_tile("flower_red"),
        # 快捷栏第 8/9 格默认是空槽:占位图全透明,运行时按背包内容显隐
        "mc_icon_empty": [[(0.0, 0.0, 0.0, 0.0) for _ in range(TEX_SIZE)]
                          for _ in range(TEX_SIZE)],
    }
    paths = {}
    for name, tile in tiles.items():
        path = os.path.join(TEXTURE_DIR, name + ".png")
        _write_png_rgba(path, _scale_rows(tile, ICON_SCALE))
        paths[name] = path
    return paths


def ensure_block_textures():
    """生成方块 PNG,返回 {贴图名: 文件路径}。"""
    os.makedirs(TEXTURE_DIR, exist_ok=True)
    sheets = {
        "mc_grass": [_grass_top_tile(), _grass_side_tile(), _dirt_tile()],
        "mc_wood": [_wood_side_tile(), _wood_top_tile()],
        "mc_dirt": [_dirt_tile()],
        "mc_stone": [_stone_tile()],
        "mc_sand": [_sand_tile()],
        "mc_leaves": [_leaves_tile()],
        "mc_coal": [_ore_tile(91, (0.10, 0.10, 0.11))],
        "mc_iron": [_ore_tile(92, (0.82, 0.62, 0.45))],
        "mc_snow": [_snow_tile()],
        "mc_table": [_table_tile()],
        "mc_chest": [_chest_tile()],
    }
    paths = {}
    for name, tiles in sheets.items():
        path = os.path.join(TEXTURE_DIR, name + ".png")
        _write_png(path, _atlas_rows(tiles))
        paths[name] = path
    # 10 阶段破坏裂纹(带透明通道,运行时按挖掘进度切换)
    for stage in range(1, 11):
        name = "mc_crack_%d" % stage
        path = os.path.join(TEXTURE_DIR, name + ".png")
        _write_png_rgba(path, _crack_tile(stage))
        paths[name] = path
    return paths


def _menu_background_rows(width=320, height=180):
    """开始菜单背景:像素风蓝天 / 云 / 远山 / 草地(纯 Python 画)。"""
    rows = []
    horizon = 104
    for y in range(height):
        if y < horizon:
            t = y / float(horizon - 1)
            color = (0.30 + 0.34 * t, 0.58 + 0.24 * t, 0.95 - 0.02 * t)
        else:
            t = (y - horizon) / float(height - horizon - 1)
            color = (0.26 + 0.10 * t, 0.55 + 0.08 * t, 0.25 + 0.03 * t)
        rows.append([color for _ in range(width)])
    # 方块太阳
    for y in range(20, 34):
        for x in range(250, 264):
            rows[y][x] = (1.0, 0.96, 0.72)
    # 方块云
    for y0, x0, w, h in ((24, 40, 46, 6), (38, 120, 64, 8),
                         (18, 190, 34, 5), (56, 88, 40, 6), (48, 228, 52, 7)):
        for y in range(y0, y0 + h):
            for x in range(x0, x0 + w):
                if 0 <= y < height and 0 <= x < width:
                    rows[y][x] = (0.97, 0.98, 1.0)
    # 两层远山
    for layer, (base_y, color) in enumerate(((horizon - 20, (0.22, 0.42, 0.28)),
                                             (horizon - 10, (0.25, 0.50, 0.29)))):
        x = 0
        while x < width:
            span = 18 + (x * 7 + layer * 11) % 26
            top = base_y - (8 + (x * 13 + layer * 17) % 14)
            for y in range(top, horizon):
                for xx in range(x, min(width, x + span)):
                    rows[y][xx] = color
            x += span
    # 近处几棵方块树
    for tx, base in ((38, horizon + 28), (268, horizon + 20),
                     (118, horizon + 46)):
        for y in range(base - 18, base):
            for x in range(tx, tx + 6):
                rows[y][x] = (0.38, 0.25, 0.12)
        for y in range(base - 34, base - 16):
            for x in range(tx - 10, tx + 16):
                if (x - tx - 3) ** 2 + ((y - (base - 25)) * 1.2) ** 2 < 150:
                    rows[y][x] = (0.18, 0.50, 0.22)
    # 底部压暗一点,菜单文字更清楚
    for y in range(height - 30, height):
        shade = 0.55 + 0.45 * (height - 1 - y) / 30.0
        for x in range(width):
            red, green, blue = rows[y][x]
            rows[y][x] = (red * shade, green * shade, blue * shade)
    return rows


def ensure_menu_background():
    """生成开始菜单背景图(放大 2 倍,最近邻保持像素感)。"""
    os.makedirs(TEXTURE_DIR, exist_ok=True)
    path = os.path.join(TEXTURE_DIR, "mc_menu_bg.png")
    _write_png(path, _scale_rows(_menu_background_rows(), 2))
    return path


def _cloud_rows(width=48, height=16):
    """一朵方块云:白身子 + 浅蓝底影 + 透明底(设计稿里云带 box-shadow)。"""
    clear = (0.0, 0.0, 0.0, 0.0)
    rows = [[clear for _ in range(width)] for _ in range(height)]
    body = (0.97, 0.98, 1.0, 1.0)
    shadow = (0.63, 0.75, 0.92, 1.0)
    # 三块错开的矩形拼出"像素云":左矮、中高、右矮
    for x0, y0, block_w, block_h in ((4, 7, 14, 5), (12, 4, 22, 8),
                                     (30, 6, 14, 6)):
        for y in range(y0, y0 + block_h):
            for x in range(x0, x0 + block_w):
                if 0 <= y < height and 0 <= x < width:
                    rows[y][x] = body
    # 每列最底下两个像素压成浅蓝,像云投在自己身上的影子
    for x in range(width):
        filled = [y for y in range(height) if rows[y][x][3] > 0.0]
        for y in filled[-2:]:
            rows[y][x] = shadow
    return rows


def ensure_cloud_texture():
    os.makedirs(TEXTURE_DIR, exist_ok=True)
    path = os.path.join(TEXTURE_DIR, "mc_cloud.png")
    _write_png_rgba(path, _cloud_rows())
    return path


# ==========================================================================
# 场景
# ==========================================================================
def clear_all():
    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)
    for group in list(bpy.data.node_groups):
        if getattr(group, "bl_idname", "").startswith("GK"):
            bpy.data.node_groups.remove(group)
    scene.name = SCENE_NAME
    return scene


def _principled(material):
    """按类型找原理化 BSDF —— 不能按名字:Blender 用中文界面时它叫
    '原理化 BSDF',按名字找会拿到 None(颜色/贴图就全丢了)。"""
    for node in material.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            return node
    return None


def make_material(name, color):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    principled = _principled(material)
    if principled is not None:
        principled.inputs["Base Color"].default_value = (*color, 1.0)
        principled.inputs["Roughness"].default_value = 0.9
    material.diffuse_color = (*color, 1.0)
    return material


def make_textured_material(name, image_name, color):
    """带像素贴图的方块材质:Base Color 接一张 16×16 的图。"""
    material = make_material(name, color)
    image = bpy.data.images.get(image_name)
    principled = _principled(material)
    if image is None or principled is None:
        return material
    texture = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.interpolation = "Closest"
    texture.location = (-360.0, 120.0)
    material.node_tree.links.new(texture.outputs["Color"],
                                 principled.inputs["Base Color"])
    return material


def make_box_mesh(name, size, material, z0=0.0, face_uvs=None):
    """以 (0,0,z0) 为下边的一只盒子网格;原点在 z0 那一层。

    face_uvs: 六个 (u0, v0, u1, v1),顺序和下面的 faces 一致
              (底 / 顶 / -Y / +X / +Y / -X)。None = 不建 UV。
    """
    sx, sy, sz = size
    hx, hy = sx * 0.5, sy * 0.5
    verts = [(-hx, -hy, z0), (hx, -hy, z0), (hx, hy, z0), (-hx, hy, z0),
             (-hx, -hy, z0 + sz), (hx, -hy, z0 + sz),
             (hx, hy, z0 + sz), (-hx, hy, z0 + sz)]
    faces = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
             (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    if material is not None:
        mesh.materials.append(material)
    if face_uvs is not None:
        layer = mesh.uv_layers.new(name="UVMap")
        corners = ((0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0))
        index = 0
        for u0, v0, u1, v1 in face_uvs:
            for cx, cy in corners:
                layer.data[index].uv = (u0 + (u1 - u0) * cx,
                                        v0 + (v1 - v0) * cy)
                index += 1
    return mesh


def make_voxel_mesh(name, parts):
    """把若干只盒子合成一个网格,每只盒子可以有自己的材质。

    原版的生物就是"一堆方块拼出来的":僵尸是头 + 身子 + 两条腿 + 两条
    前伸的手臂,每块用一个纯色材质。parts 里每个元素是
    (尺寸 (sx, sy, sz), 中心 (x, y, z), 材质)。
    """
    mesh = bpy.data.meshes.new(name)
    verts = []
    faces = []
    face_materials = []
    order = []
    for size, center, material in parts:
        sx, sy, sz = size
        cx, cy, cz = center
        hx, hy, hz = sx * 0.5, sy * 0.5, sz * 0.5
        base = len(verts)
        verts.extend([
            (cx - hx, cy - hy, cz - hz), (cx + hx, cy - hy, cz - hz),
            (cx + hx, cy + hy, cz - hz), (cx - hx, cy + hy, cz - hz),
            (cx - hx, cy - hy, cz + hz), (cx + hx, cy - hy, cz + hz),
            (cx + hx, cy + hy, cz + hz), (cx - hx, cy + hy, cz + hz),
        ])
        faces.extend([
            (base + 0, base + 1, base + 2, base + 3),
            (base + 4, base + 7, base + 6, base + 5),
            (base + 0, base + 4, base + 5, base + 1),
            (base + 1, base + 5, base + 6, base + 2),
            (base + 2, base + 6, base + 7, base + 3),
            (base + 3, base + 7, base + 4, base + 0),
        ])
        if material.name not in order:
            order.append(material.name)
        index = order.index(material.name)
        face_materials.extend([index] * 6)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    for material_name in order:
        mesh.materials.append(bpy.data.materials[material_name])
    for polygon, index in zip(mesh.polygons, face_materials):
        polygon.material_index = index
    return mesh


# ── 玩家皮肤(64×64 原版格式,可替换资源)──────────────────────────
# 为什么按 64×64 排:用户想直接拿原版皮肤替换。模型按原版比例拆成
# 头 / 身子 / 两条手臂 / 两条腿,每个面 UV 对齐到 64×64 里对应的格子,
# 外层再套一圈略大的壳(帽子 + 夹克),贴图另有文件,可在 Godot 的
# art/skin/ 里直接换掉,不用改生成器。
SKIN_SIZE = 64
# 每个部件的六个面在皮肤图里的像素矩形,顺序和 make_box_mesh 的
# faces 一致:底 / 顶 / -Y(后) / +X(右) / +Y(前) / -X(左)。
SKIN_HEAD = ((16, 0, 8, 8), (8, 0, 8, 8), (24, 8, 8, 8),
             (0, 8, 8, 8), (8, 8, 8, 8), (16, 8, 8, 8))
SKIN_BODY = ((28, 16, 8, 4), (20, 16, 8, 4), (32, 20, 8, 12),
             (16, 20, 4, 12), (20, 20, 8, 12), (28, 20, 4, 12))
SKIN_ARM = ((48, 16, 4, 4), (44, 16, 4, 4), (52, 20, 4, 12),
            (40, 20, 4, 12), (44, 20, 4, 12), (48, 20, 4, 12))
SKIN_LEG = ((8, 16, 4, 4), (4, 16, 4, 4), (12, 20, 4, 12),
            (0, 20, 4, 12), (4, 20, 4, 12), (8, 20, 4, 12))

# 第二层(帽子 / 夹克 / 袖子 / 裤腿)的独立 UV:原版 64×64 里 x 32–64
# 那一半专门放外层,不能复用第一层矩形(否则用户放原版皮肤时外套不显示)。
# 面顺序和 SKIN_* 一致:底 / 顶 / -Y(后) / +X(右) / +Y(前) / -X(左)。
SKIN_HEAD_OUTER = ((48, 0, 8, 8), (40, 0, 8, 8), (56, 8, 8, 8),
                   (32, 8, 8, 8), (40, 8, 8, 8), (48, 8, 8, 8))
SKIN_BODY_OUTER = ((28, 32, 8, 4), (20, 32, 8, 4), (32, 36, 8, 12),
                   (16, 36, 4, 12), (20, 36, 8, 12), (28, 36, 4, 12))
SKIN_ARM_OUTER = ((48, 32, 4, 4), (44, 32, 4, 4), (52, 36, 4, 12),
                  (40, 36, 4, 12), (44, 36, 4, 12), (48, 36, 4, 12))
SKIN_LEG_OUTER = ((8, 32, 4, 4), (4, 32, 4, 4), (12, 36, 4, 12),
                  (0, 36, 4, 12), (4, 36, 4, 12), (8, 36, 4, 12))

# 玩家模型:脚在 0,总高 1.65 米(和原版 1.8 接近,第三人称看着正常)
PLAYER_PARTS = (
    ("head", (0.45, 0.45, 0.45), (0.0, 0.0, 1.425)),
    ("body", (0.50, 0.28, 0.55), (0.0, 0.0, 0.875)),
    ("arm", (0.16, 0.16, 0.55), (-0.33, 0.0, 0.875)),
    ("arm", (0.16, 0.16, 0.55), (0.33, 0.0, 0.875)),
    ("leg", (0.19, 0.19, 0.65), (-0.12, 0.0, 0.325)),
    ("leg", (0.19, 0.19, 0.65), (0.12, 0.0, 0.325)),
)
SKIN_PART_RECTS = {
    "head": SKIN_HEAD, "body": SKIN_BODY, "arm": SKIN_ARM, "leg": SKIN_LEG,
}
SKIN_PART_RECTS_OUTER = {
    "head": SKIN_HEAD_OUTER, "body": SKIN_BODY_OUTER,
    "arm": SKIN_ARM_OUTER, "leg": SKIN_LEG_OUTER,
}


def _skin_fill(rows, rect, color):
    x0, y0, width, height = rect
    for y in range(y0, y0 + height):
        for x in range(x0, x0 + width):
            rows[y][x] = color


def _player_skin_rows():
    """默认皮肤(Steve 配色),按 64×64 原版格式排。"""
    clear = (0.0, 0.0, 0.0, 0.0)
    rows = [[clear] * SKIN_SIZE for _ in range(SKIN_SIZE)]
    hair = (0.20, 0.12, 0.06, 1.0)
    skin = (0.87, 0.68, 0.50, 1.0)
    shirt = (0.22, 0.62, 0.66, 1.0)
    shirt_dark = (0.16, 0.48, 0.52, 1.0)
    pants = (0.22, 0.29, 0.56, 1.0)
    shoe = (0.32, 0.32, 0.35, 1.0)
    eye_white = (0.95, 0.95, 0.95, 1.0)
    eye_blue = (0.24, 0.38, 0.62, 1.0)
    mouth = (0.55, 0.35, 0.28, 1.0)
    # 后脑勺整块头发;两侧上半头发、下半露脸(和原版 Steve 一样,
    # 否则从背后看会像"光头"):SKIN_HEAD 顺序 底/顶/后/右/前/左。
    _skin_fill(rows, SKIN_HEAD[2], hair)
    for rect in SKIN_HEAD[3:]:
        _skin_fill(rows, rect, skin)
        _skin_fill(rows, (rect[0], rect[1], rect[2], 5), hair)
    _skin_fill(rows, SKIN_HEAD[1], hair)
    _skin_fill(rows, SKIN_HEAD[0], skin)
    _skin_fill(rows, (9, 9, 2, 1), hair)
    _skin_fill(rows, (13, 9, 2, 1), hair)
    _skin_fill(rows, (9, 11, 3, 1), eye_white)
    _skin_fill(rows, (12, 11, 3, 1), eye_white)
    _skin_fill(rows, (10, 11, 1, 1), eye_blue)
    _skin_fill(rows, (13, 11, 1, 1), eye_blue)
    _skin_fill(rows, (10, 13, 4, 1), mouth)
    for rect in SKIN_BODY:
        _skin_fill(rows, rect, shirt)
    _skin_fill(rows, (20, 20, 8, 1), shirt_dark)
    _skin_fill(rows, (20, 31, 8, 1), pants)
    _skin_fill(rows, (16, 31, 4, 1), pants)
    _skin_fill(rows, (28, 31, 4, 1), pants)
    for rect in SKIN_ARM:
        _skin_fill(rows, rect, skin)
        if rect[3] > 4:
            _skin_fill(rows, (rect[0], rect[1], rect[2], 4), shirt)
        else:
            _skin_fill(rows, rect, shirt)
    for rect in SKIN_LEG:
        _skin_fill(rows, rect, pants)
        if rect[3] > 4:
            _skin_fill(rows, (rect[0], rect[1] + rect[3] - 2, rect[2], 2), shoe)
        else:
            _skin_fill(rows, rect, shoe)
    return rows


def _player_skin_outer_rows():
    """外层(帽子 / 夹克 / 袖子 / 裤腿):写在原版第二层 UV 区域。
    只铺"帽子顶 / 肩膀 / 下摆 / 裤脚"几处,其余全透明 —— 和原版 Steve
    一样默认几乎看不见,换成自定义皮肤时第二层直接生效。
    """
    clear = (0.0, 0.0, 0.0, 0.0)
    rows = [[clear] * SKIN_SIZE for _ in range(SKIN_SIZE)]
    cap = (0.18, 0.10, 0.05, 0.85)
    jacket = (0.12, 0.42, 0.46, 0.55)
    pants = (0.16, 0.22, 0.46, 0.55)
    _skin_fill(rows, SKIN_HEAD_OUTER[1], cap)
    for rect in SKIN_BODY_OUTER[2:]:
        _skin_fill(rows, (rect[0], rect[1] + rect[3] - 3, rect[2], 3), jacket)
    for rect in SKIN_ARM_OUTER[2:]:
        _skin_fill(rows, (rect[0], rect[1], rect[2], 4), jacket)
    for rect in SKIN_LEG_OUTER[2:]:
        _skin_fill(rows, (rect[0], rect[1] + rect[3] - 3, rect[2], 3), pants)
    return rows


def ensure_player_skin():
    """生成默认皮肤 + 外套层(64×64),都落在 _dist/minecraft_textures。"""
    os.makedirs(TEXTURE_DIR, exist_ok=True)
    paths = {}
    for name, rows in (("mc_skin", _player_skin_rows()),
                       ("mc_skin_outer", _player_skin_outer_rows())):
        path = os.path.join(TEXTURE_DIR, name + ".png")
        _write_png_rgba(path, rows)
        paths[name] = path
    return paths


def _skin_uv(rect):
    x, y, width, height = rect
    # glTF 导出会把 V 翻一次(1 - v):这里按"从图片底部往上量"写,
    # 到了 Godot 里 v=0 正好落在图片顶部 —— 直接写 y/64 会把整张皮肤
    # 上下颠倒,模型大部分面会采样到空白区(渲染成黑块)。
    return (x / float(SKIN_SIZE), 1.0 - (y + height) / float(SKIN_SIZE),
            (x + width) / float(SKIN_SIZE), 1.0 - y / float(SKIN_SIZE))


def make_skinned_player_mesh(name, materials):
    """按 64×64 皮肤 UV 拼玩家模型;materials = [本体, 外套层]。"""
    mesh = bpy.data.meshes.new(name)
    verts = []
    faces = []
    face_uvs = []
    face_groups = []
    for part, size, center in PLAYER_PARTS:
        # 第二层比本体略外扩:头 0.5px、其余 0.25px(1px ≈ 1/16m)。
        for group, grown in ((0, 0.0), (1, 0.03 if part == "head" else 0.02)):
            sx, sy, sz = size
            if grown:
                sx += grown
                sy += grown
            cx, cy, cz = center
            hx, hy, hz = sx * 0.5, sy * 0.5, sz * 0.5
            base = len(verts)
            verts.extend([
                (cx - hx, cy - hy, cz - hz), (cx + hx, cy - hy, cz - hz),
                (cx + hx, cy + hy, cz - hz), (cx - hx, cy + hy, cz - hz),
                (cx - hx, cy - hy, cz + hz), (cx + hx, cy - hy, cz + hz),
                (cx + hx, cy + hy, cz + hz), (cx - hx, cy + hy, cz + hz),
            ])
            faces.extend([
                (base + 0, base + 1, base + 2, base + 3),
                (base + 4, base + 7, base + 6, base + 5),
                (base + 0, base + 4, base + 5, base + 1),
                (base + 1, base + 5, base + 6, base + 2),
                (base + 2, base + 6, base + 7, base + 3),
                (base + 3, base + 7, base + 4, base + 0),
            ])
            # 本体用第一层 UV,外套用第二层的独立区域
            face_uvs.extend(SKIN_PART_RECTS[part] if group == 0
                            else SKIN_PART_RECTS_OUTER[part])
            face_groups.extend([group] * 6)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    for material in materials:
        mesh.materials.append(material)
    for polygon, group in zip(mesh.polygons, face_groups):
        polygon.material_index = group
    layer = mesh.uv_layers.new(name="UVMap")
    corners = ((0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0))
    index = 0
    for rect in face_uvs:
        u0, v0, u1, v1 = _skin_uv(rect)
        for cx, cy in corners:
            layer.data[index].uv = (u0 + (u1 - u0) * cx,
                                    v0 + (v1 - v0) * cy)
            index += 1
    return mesh


def add_object(scene, name, mesh, location, parent=None):
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    obj.location = location
    if parent is not None:
        obj.parent = parent
    return obj


def add_empty(scene, name, location=(0.0, 0.0, 0.0)):
    obj = bpy.data.objects.new(name, None)
    scene.collection.objects.link(obj)
    obj.location = location
    obj.empty_display_type = "PLAIN_AXES"
    return obj


def _physics(obj):
    return getattr(obj, "gk_physics", None)


def set_physics(obj, body_type, *, script_target=False, tree="", asset="",
                layer="", mask=""):
    physics = _physics(obj)
    if physics is None:
        return
    try:
        physics.enabled = True
        physics.dimension = "3"
        physics.body_type = body_type
        physics.script_target = bool(script_target)
        if tree:
            physics.logic_tree = tree
        if asset:
            physics.asset_name = asset
        if layer:
            physics.collision_layer = layer
        if mask:
            physics.collision_mask = mask
    except Exception as exc:                                # noqa: BLE001
        log(f"物理设置 {obj.name} 失败: {exc}")


def build_world(scene):
    ensure_block_textures()
    ensure_item_icons()
    ensure_player_skin()
    materials = {}
    for name, color in BLOCK_COLORS.items():
        texture = BLOCK_TEXTURES.get(name)
        if texture is None:
            materials[name] = make_material(f"方块_{name}", color)
            continue
        image = bpy.data.images.get(texture)
        if image is None:
            image = bpy.data.images.load(
                os.path.join(TEXTURE_DIR, texture + ".png"),
                check_existing=False)
            image.name = texture
        image.pack()
        materials[name] = make_textured_material(f"方块_{name}", texture, color)

    # 地面/丘陵/树/矿石全部交给"无限区块"脚本生成(见 CHUNK_SCRIPT)。
    # 早期版本在出生点旁边摆过沙地 / 石台 / 门框做装饰,但它们会和
    # 运行时地形打架(悬空的木板、被埋一半的石头),原版味道也差,已移除。
    blocks_root = add_empty(scene, "Blocks")
    placements = []

    for name, location, mesh in placements:
        obj = add_object(scene, name, mesh, location, parent=blocks_root)
        set_physics(obj, "StaticBody3D", layer="1", mask="1")

    # 玩家:64×64 皮肤贴图 + 外套层(模型高 1.65 米,原点在脚下)。
    # 贴图在 art/skin/ 里是独立文件,换皮肤不用改生成器。
    for skin_name in ("mc_skin", "mc_skin_outer"):
        skin_image = bpy.data.images.get(skin_name)
        if skin_image is None:
            skin_image = bpy.data.images.load(
                os.path.join(TEXTURE_DIR, skin_name + ".png"),
                check_existing=False)
            skin_image.name = skin_name
        skin_image.pack()
    skin_material = make_textured_material("玩家皮肤材质", "mc_skin",
                                           (0.25, 0.45, 0.85))
    outer_material = make_textured_material("玩家外套材质", "mc_skin_outer",
                                            (1.0, 1.0, 1.0))
    try:  # 外套层要透明:4.2+ 是 surface_render_method,老版本是 blend_method
        outer_material.surface_render_method = "DITHERED"
    except Exception:  # noqa: BLE001
        pass
    try:
        outer_material.blend_method = "BLEND"
    except Exception:  # noqa: BLE001
        pass
    for node in outer_material.node_tree.nodes:
        if node.type == "TEX_IMAGE":
            links = outer_material.node_tree.links
            links.new(node.outputs["Alpha"],
                      _principled(outer_material).inputs["Alpha"])
    player_mesh = make_skinned_player_mesh("玩家网格",
                                           [skin_material, outer_material])
    player = add_object(scene, PLAYER_NAME, player_mesh,
                        godot_to_blender(*PLAYER_SPAWN))
    set_physics(player, "CharacterBody3D", script_target=True,
                tree=TREE_NAME, layer="2", mask="1")

    camera_data = bpy.data.cameras.new(f"{CAMERA_NAME}数据")
    camera_data.angle = math.radians(75.0)
    camera_data.clip_start = 0.05
    camera_data.clip_end = 200.0
    camera = bpy.data.objects.new(CAMERA_NAME, camera_data)
    scene.collection.objects.link(camera)
    camera.parent = player
    camera.location = (0.0, 0.0, 1.6)
    scene.camera = camera

    # 第三人称机位:站在玩家身后 3.5 米、高 1.2 米(切换只换当前相机)
    third_data = bpy.data.cameras.new(f"{THIRD_CAMERA}数据")
    third_data.angle = math.radians(75.0)
    third_data.clip_start = 0.05
    third_data.clip_end = 200.0
    third = bpy.data.objects.new(THIRD_CAMERA, third_data)
    scene.collection.objects.link(third)
    # 也挂到玩家下面:导出的世界 glb 就不带相机,导出器会把两台相机
    # 都写成场景里的 Camera3D 节点(glb 带相机时它会反过来跳过相机节点)
    third.parent = player
    third.location = (0.0, -3.5, 1.2)

    # 三只僵尸:按 MC 玩家比例用方块拼(腿 + 身子 + 前伸的手臂 + 头),
    # 脸朝 Blender +Y(= Godot -Z,『移动_朝向目标』就是让 -Z 对着玩家)。
    skin = make_material("僵尸皮肤", (0.36, 0.62, 0.30))
    shirt = make_material("僵尸衣服", (0.16, 0.52, 0.52))
    pants = make_material("僵尸裤子", (0.22, 0.28, 0.58))
    eyes = make_material("僵尸眼睛", (0.07, 0.09, 0.07))
    monster_mesh = make_voxel_mesh("僵尸网格", [
        ((0.20, 0.22, 0.75), (-0.13, 0.0, 0.375), pants),
        ((0.20, 0.22, 0.75), (0.13, 0.0, 0.375), pants),
        ((0.50, 0.26, 0.70), (0.0, 0.0, 1.10), shirt),
        ((0.16, 0.60, 0.16), (-0.33, 0.30, 1.32), skin),
        ((0.16, 0.60, 0.16), (0.33, 0.30, 1.32), skin),
        ((0.44, 0.44, 0.44), (0.0, 0.0, 1.67), skin),
        ((0.09, 0.03, 0.07), (-0.10, 0.225, 1.72), eyes),
        ((0.09, 0.03, 0.07), (0.10, 0.225, 1.72), eyes),
        ((0.16, 0.03, 0.05), (0.0, 0.225, 1.55), eyes),
    ])
    monsters_root = add_empty(scene, "Monsters")
    for name, position in MONSTERS:
        monster = add_object(scene, name, monster_mesh,
                             godot_to_blender(*position),
                             parent=monsters_root)
        set_physics(monster, "CharacterBody3D", layer="4", mask="1")

    # 羊:被动生物。以前只有一个白盒子,用户以为是"两个白色实体";
    # 现在按 MC 比例补上头 / 耳朵 / 四条腿,羊毛和脸/腿分开配色。
    face = make_material("羊脸", (0.88, 0.82, 0.78))
    hoof = make_material("羊蹄", (0.55, 0.50, 0.46))
    sheep_mesh = make_voxel_mesh("羊网格", [
        ((0.80, 1.10, 0.62), (0.0, 0.0, 0.62), materials["sheep"]),
        ((0.46, 0.46, 0.46), (0.0, 0.74, 0.86), face),
        ((0.10, 0.20, 0.12), (-0.27, 0.70, 1.02), face),
        ((0.10, 0.20, 0.12), (0.27, 0.70, 1.02), face),
        ((0.18, 0.18, 0.34), (-0.26, -0.36, 0.17), hoof),
        ((0.18, 0.18, 0.34), (0.26, -0.36, 0.17), hoof),
        ((0.18, 0.18, 0.34), (-0.26, 0.36, 0.17), hoof),
        ((0.18, 0.18, 0.34), (0.26, 0.36, 0.17), hoof),
    ])
    for name, position in SHEEP:
        sheep = add_object(scene, name, sheep_mesh,
                           godot_to_blender(*position),
                           parent=monsters_root)
        set_physics(sheep, "CharacterBody3D", layer="8", mask="1")

    # 猪(第一批):粉色身体 + 四条腿 + 鼻头 + 尾巴;贴地高度和羊一致。
    pig_body = make_material("猪身", (0.94, 0.62, 0.66))
    pig_snout = make_material("猪鼻", (0.86, 0.46, 0.52))
    pig_leg = make_material("猪腿", (0.88, 0.54, 0.58))
    pig_mesh = make_voxel_mesh("猪网格", [
        ((0.74, 1.02, 0.56), (0.0, 0.0, 0.60), pig_body),
        ((0.52, 0.46, 0.44), (0.0, 0.66, 0.66), pig_body),
        ((0.24, 0.14, 0.20), (0.0, 0.93, 0.60), pig_snout),
        ((0.18, 0.18, 0.34), (-0.24, -0.36, 0.17), pig_leg),
        ((0.18, 0.18, 0.34), (0.24, -0.36, 0.17), pig_leg),
        ((0.18, 0.18, 0.34), (-0.24, 0.34, 0.17), pig_leg),
        ((0.18, 0.18, 0.34), (0.24, 0.34, 0.17), pig_leg),
        ((0.10, 0.10, 0.10), (0.0, -0.56, 0.72), pig_leg),
    ])
    for name, position in PIGS:
        pig = add_object(scene, name, pig_mesh,
                         godot_to_blender(*position),
                         parent=monsters_root)
        set_physics(pig, "CharacterBody3D", layer="8", mask="1")

    # 可放置方块(草 / 石 / 沙 / 木 / 叶 / 工作台 / 箱子)各造一份资产源,
    # 摆在远处(不进玩法区域);苹果 / 剑不是方块,没有资产源。
    asset_root = add_empty(scene, "资产源", godot_to_blender(0.0, -40.0, 0.0))
    for index, (label, color, kind) in enumerate(PLACE_BLOCKS):
        mesh = make_box_mesh(f"可放置_{label}网格", (1.0, 1.0, 1.0),
                             make_textured_material(
                                 f"可放置_{label}",
                                 BLOCK_TEXTURES[kind], color), z0=0.0,
                             face_uvs=BLOCK_UVS[kind])
        obj = add_object(scene, f"{label}_源", mesh,
                         (index * 2.0, 0.0, 0.0), parent=asset_root)
        set_physics(obj, "StaticBody3D", asset=label, layer="1", mask="1")

    # 太阳
    sun_data = bpy.data.lights.new("太阳光数据", type="SUN")
    sun_data.energy = 1.2
    sun = bpy.data.objects.new("Sun", sun_data)
    scene.collection.objects.link(sun)
    sun.rotation_euler = tuple(math.radians(value) for value in (48.0, 12.0, 28.0))

    log(f"世界:{len(placements)} 块装饰 + 玩家 + 相机 + {len(MONSTERS)} 只小怪"
        f" + {PLACE_SLOTS} 档可放置方块"
        " (地面由无限地形脚本生成)")


def apply_scene_settings(scene):
    settings = getattr(scene, "gk_settings", None)
    if settings is None:
        return
    pairs = (
        ("dimension", "3"),
        ("base_class", "CharacterBody3D"),
        ("class_name", "MinecraftPlayer"),
        ("logic_tree", TREE_NAME),
        ("god_camera", False),
        ("env_enabled", True),
        ("env_background", "SKY"),
        ("env_bg_color", (0.55, 0.72, 0.95, 1.0)),
        ("env_ambient_color", (0.55, 0.62, 0.75, 1.0)),
        ("env_ambient_energy", 0.55),
        ("env_tonemap", "LINEAR"),
    )
    for attr, value in pairs:
        if not hasattr(settings, attr):
            continue
        try:
            setattr(settings, attr, value)
        except Exception:                                   # noqa: BLE001
            pass


# ==========================================================================
# 节点图
# ==========================================================================
def _add(tree, kind, name, x, y, **props):
    node = tree.nodes.new(kind)
    node.name = name
    node.location = (x, y)
    for key, value in props.items():
        setattr(node, key, value)
    builder = getattr(node, "build_sockets", None)
    if callable(builder):
        builder()
    return node


def _link(tree, source, source_pin, target, target_pin):
    tree.links.new(source.outputs[source_pin], target.inputs[target_pin])


def build_ui(tree):
    """开始界面 + 暂停菜单 + 底部物品栏。返回各控件节点。"""
    panel = _add(tree, "GKNodeUiPanel", "世界菜单", -1450.0, 900.0,
                 gk_width=520.0, gk_height=500.0, gk_radius=18,
                 gk_align="CENTER", gk_bottom=True, gk_block_click=True,
                 gk_color=(0.05, 0.06, 0.09, 0.86))
    title = _add(tree, "GKNodeUiText", "菜单标题", -1200.0, 900.0,
                 gk_text="我的世界", gk_size=52,
                 gk_color=(0.55, 1.0, 0.75, 1.0),
                 gk_x=60.0, gk_y=40.0, gk_parent_widget="世界菜单")
    # 开始菜单只有一个"像开始"的主按钮(第两百零七轮):
    # 有存档时是『继续游戏』(读最近一档),没存档时是置灰的『开始游戏』;
    # 位置 / 显隐由存档脚本的 _refresh_menu 按存档状态收敛。
    start = _add(tree, "GKNodeUiButton", "继续游戏", -950.0, 900.0,
                 gk_text="开始游戏", gk_width=400.0, gk_height=54.0,
                 gk_x=60.0, gk_y=160.0, gk_parent_widget="世界菜单")
    load_button = _add(tree, "GKNodeUiButton", "读取存档", -825.0, 900.0,
                       gk_text="读取存档", gk_width=400.0, gk_height=54.0,
                       gk_x=60.0, gk_y=230.0, gk_parent_widget="世界菜单")
    create = _add(tree, "GKNodeUiButton", "新建世界", -700.0, 900.0,
                  gk_text="新建世界", gk_width=400.0, gk_height=54.0,
                  gk_x=60.0, gk_y=300.0, gk_parent_widget="世界菜单")
    quit_button = _add(tree, "GKNodeUiButton", "退出游戏", -450.0, 900.0,
                       gk_text="退出游戏", gk_width=400.0, gk_height=54.0,
                       gk_x=60.0, gk_y=370.0, gk_parent_widget="世界菜单")

    # 底部物品栏:一块底板 + 9 个格子(格子底板 + 物品缩略图)+ 选中框
    hotbar = _add(tree, "GKNodeUiPanel", "物品栏", 1050.0, 1200.0,
                  gk_width=696.0, gk_height=66.0, gk_radius=6,
                  gk_align="BOTTOM_CENTER", gk_bottom=True,
                  gk_color=(0.10, 0.10, 0.12, 0.62))
    slot_bgs = []
    for index in range(HOTBAR_SLOTS):
        background = _add(tree, "GKNodeUiPanel", f"槽底板{index + 1}",
                          1150.0, 1200.0 - index * 120.0,
                          gk_width=64.0, gk_height=44.0, gk_radius=3,
                          gk_x=12.0 + index * 76.0, gk_y=11.0,
                          gk_color=(0.62, 0.62, 0.66, 0.30),
                          gk_parent_widget="物品栏")
        slot_bgs.append(background)
    icons = []
    for index, icon in enumerate(HOTBAR_ICONS):
        picture = _add(tree, "GKNodeUiImage", f"槽图标{index + 1}", 1300.0,
                       1200.0 - index * 120.0,
                       gk_image=f"res://art/{icon}.png",
                       gk_width=32.0, gk_height=32.0, gk_keep=False,
                       gk_x=12.0 + index * 76.0 + 16.0, gk_y=17.0,
                       gk_parent_widget="物品栏")
        icons.append(picture)
    # 选中框:盖在当前格子上的半透明白块(由『我的世界背包』脚本跟着序号挪)
    selected = _add(tree, "GKNodeUiPanel", "选中框", 1450.0, 1500.0,
                    gk_width=64.0, gk_height=44.0, gk_radius=3,
                    gk_x=12.0, gk_y=11.0,
                    gk_color=(1.0, 1.0, 1.0, 0.22),
                    gk_parent_widget="物品栏")
    # 切换物品时在物品栏上方闪一下名字(原版是这样提示的)
    item_hint = _add(tree, "GKNodeUiText", "物品名提示", 1650.0, 1500.0,
                     gk_text="", gk_size=18,
                     gk_color=(1.0, 1.0, 1.0, 1.0),
                     gk_align="BOTTOM_CENTER", gk_margin=96.0)

    # 准星:屏幕正中一横一竖两道细白线(原版的十字准星)
    cross_h = _add(tree, "GKNodeUiPanel", "准星横", 1450.0, 1200.0,
                   gk_width=22.0, gk_height=2.0, gk_radius=0,
                   gk_align="CENTER", gk_color=(1.0, 1.0, 1.0, 0.82))
    cross_v = _add(tree, "GKNodeUiPanel", "准星竖", 1700.0, 1200.0,
                   gk_width=2.0, gk_height=22.0, gk_radius=0,
                   gk_align="CENTER", gk_color=(1.0, 1.0, 1.0, 0.82))

    # 暂停菜单
    pause_panel = _add(tree, "GKNodeUiPanel", "暂停菜单", 2200.0, 1200.0,
                       gk_width=360.0, gk_height=430.0, gk_radius=16,
                       gk_align="CENTER", gk_bottom=True,
                       gk_block_click=True, gk_start_hidden=True,
                       gk_color=(0.05, 0.06, 0.09, 0.90))
    pause_title = _add(tree, "GKNodeUiText", "暂停标题", 2450.0, 1200.0,
                       gk_text="已暂停", gk_size=32,
                       gk_x=30.0, gk_y=24.0,
                       gk_parent_widget="暂停菜单")
    # 节点名不能和开始菜单的『继续游戏』重名(生成器只给重名加 .001,
    # 那不是合法标识符);显示文案仍然是『继续游戏』。
    resume_button = _add(tree, "GKNodeUiButton", "暂停继续", 2700.0, 1200.0,
                         gk_text="继续游戏", gk_width=300.0, gk_height=50.0,
                         gk_x=30.0, gk_y=90.0,
                         gk_parent_widget="暂停菜单")
    save_pause = _add(tree, "GKNodeUiButton", "保存游戏", 2825.0, 1200.0,
                      gk_text="保存游戏", gk_width=300.0, gk_height=50.0,
                      gk_x=30.0, gk_y=155.0,
                      gk_parent_widget="暂停菜单")
    settings_pause = _add(tree, "GKNodeUiButton", "暂停设置", 2875.0, 1200.0,
                          gk_text="设置", gk_width=300.0, gk_height=50.0,
                          gk_x=30.0, gk_y=220.0,
                          gk_parent_widget="暂停菜单")
    respawn_button = _add(tree, "GKNodeUiButton", "回到出生点", 2950.0, 1200.0,
                          gk_text="回到出生点", gk_width=300.0, gk_height=50.0,
                          gk_x=30.0, gk_y=285.0,
                          gk_parent_widget="暂停菜单")
    quit_pause = _add(tree, "GKNodeUiButton", "退出到桌面", 3200.0, 1200.0,
                      gk_text="退出游戏", gk_width=300.0, gk_height=50.0,
                      gk_x=30.0, gk_y=350.0,
                      gk_parent_widget="暂停菜单")
    # 开始菜单按设计稿固定四个入口:开始游戏 / 读取存档 / 创建游戏 / 退出游戏
    # (设置放进 ESC 暂停菜单;第两百零八轮)。
    return {
        "panel": panel, "title": title, "start": start, "load": load_button,
        "create": create, "quit": quit_button,
        "hotbar": hotbar, "icons": icons, "slot_bgs": slot_bgs,
        "selected": selected, "item_hint": item_hint,
        "cross_h": cross_h, "cross_v": cross_v,
        "pause_panel": pause_panel, "pause_title": pause_title,
        "resume": resume_button, "save": save_pause,
        "settings_pause": settings_pause,
        "respawn": respawn_button,
        "quit_pause": quit_pause,
    }


def build_tree(scene, chunk_text=None, weather_text=None, sheep_text=None,
               sky_text=None, hand_text=None, outline_text=None,
               creature_text=None, save_text=None, settings_text=None,
               inventory_text=None, swim_text=None, input_text=None,
               pig_text=None):
    tree = bpy.data.node_groups.new(TREE_NAME, "GKGameLogicTree")
    tree.gk_scope = "ROLE"
    tree.use_fake_user = True

    held = tree.gk_vars.add()
    held.var_name = "方块序号"
    held.var_type = "float"
    held.default_value = "0"
    held.comment = "快捷栏选中格 0~8(由背包脚本写)"

    held_name = tree.gk_vars.add()
    held_name.var_name = "手持名"
    held_name.var_type = "string"
    held_name.default_value = "草方块"
    held_name.comment = "HUD 上显示的当前物品名(由背包脚本按格子内容写)"

    # 第两百零九轮:背包接管物品后,节点图不再按"序号 = 固定物品"判断,
    # 改成背包脚本每帧写这三种"手上是什么"的变量。
    held_item = tree.gk_vars.add()
    held_item.var_name = "手持物品"
    held_item.var_type = "string"
    held_item.default_value = "grass"
    held_item.comment = "手持物品类型:grass/stone/sand/wood/leaves/apple/sword;空 = 空手"

    held_kind = tree.gk_vars.add()
    held_kind.var_name = "手持类别"
    held_kind.var_type = "float"
    held_kind.default_value = "1"
    held_kind.comment = "0 空 / 1 方块 / 2 苹果 / 3 剑"

    held_block = tree.gk_vars.add()
    held_block.var_name = "手持方块"
    held_block.var_type = "float"
    held_block.default_value = "0"
    held_block.comment = "0 草 / 1 石 / 2 沙 / 3 木 / 4 叶(放置时用)"

    place_target = tree.gk_vars.add()
    place_target.var_name = "放置目标"
    place_target.var_type = "vector"
    place_target.default_value = "Vector3.ZERO"
    place_target.comment = "can_place_here 写:目标格底面中心(资产原点)"

    place_hit = tree.gk_vars.add()
    place_hit.var_name = "放置命中"
    place_hit.var_type = "vector"
    place_hit.default_value = "Vector3.ZERO"
    place_hit.comment = "准星射线打中的世界坐标"

    place_normal = tree.gk_vars.add()
    place_normal.var_name = "放置法线"
    place_normal.var_type = "vector"
    place_normal.default_value = "Vector3.ZERO"
    place_normal.comment = "命中面的法线:目标格 = 命中格 + 法线"

    place_allowed = tree.gk_vars.add()
    place_allowed.var_name = "放置允许"
    place_allowed.var_type = "bool"
    place_allowed.default_value = "false"
    place_allowed.comment = "can_place_here 写:目标格没有被实体占住"

    health = tree.gk_vars.add()
    health.var_name = "生命"
    health.var_type = "float"
    health.default_value = "100"
    health.comment = "怪物每口 -8;0 = 送回出生点"

    creative = tree.gk_vars.add()
    creative.var_name = "创造模式"
    creative.var_type = "bool"
    creative.default_value = "true"
    creative.comment = "创造模式:怪物打不掉血"

    third_view = tree.gk_vars.add()
    third_view.var_name = "第三人称"
    third_view.var_type = "bool"
    third_view.default_value = "false"
    third_view.comment = "V 切换第一 / 第三人称"

    # ---- 世界设置(第两百零六轮):默认值 = 原来的观感 -------------------
    world_name_var = tree.gk_vars.add()
    world_name_var.var_name = "世界名"
    world_name_var.var_type = "string"
    world_name_var.default_value = "新的世界"
    world_name_var.comment = "存档名称;『保存游戏』默认用它"

    seed_var = tree.gk_vars.add()
    seed_var.var_name = "种子"
    seed_var.var_type = "string"
    seed_var.default_value = ""
    seed_var.comment = "留空 = 默认世界(和之前的地形完全一致)"

    terrain_var = tree.gk_vars.add()
    terrain_var.var_name = "地形类型"
    terrain_var.var_type = "float"
    terrain_var.default_value = "2"
    terrain_var.comment = "1 平原 / 2 丘陵(默认)/ 3 山地"

    size_var = tree.gk_vars.add()
    size_var.var_name = "世界大小"
    size_var.var_type = "float"
    size_var.default_value = "1"
    size_var.comment = "1 无限 / 2 128 格 / 3 256 格 / 4 512 格(半径)"

    vegetation_var = tree.gk_vars.add()
    vegetation_var.var_name = "植被密度"
    vegetation_var.var_type = "float"
    vegetation_var.default_value = "3"
    vegetation_var.comment = "1 无 / 2 稀疏 / 3 默认 10%(原来)/ 4 茂密"

    time_var = tree.gk_vars.add()
    time_var.var_name = "昼夜时间"
    time_var.var_type = "float"
    time_var.default_value = "1"
    time_var.comment = "1 白天(默认)/ 2 黄昏 / 3 夜晚"

    radius_var = tree.gk_vars.add()
    radius_var.var_name = "渲染距离"
    radius_var.var_type = "float"
    radius_var.default_value = "3"
    radius_var.comment = "1 8 区块 / 2 12 区块 / 3 16 区块(默认)/ 4 24 区块 / 5 32 区块"

    # 世界高度(M2a):地形生成读区块脚本常量,这个变量只做存档对账与迁移判断。
    height_var = tree.gk_vars.add()
    height_var.var_name = "世界高度"
    height_var.var_type = "float"
    height_var.default_value = "64"
    height_var.comment = "世界高度(方块层数);旧档缺这个字段 = 需要迁移"

    # 出生点(第两百一十六轮):由区块脚本按地形挑出来,死亡 / 『回到出生点』都读它。
    # 默认值只是"还没进世界的占位",进世界时 choose_spawn() 会覆盖。
    for axis, default in (("X", "0.5"), ("Y", "1.0"), ("Z", "0.5")):
        spawn_var = tree.gk_vars.add()
        spawn_var.var_name = "出生点%s" % axis
        spawn_var.var_type = "float"
        spawn_var.default_value = default
        spawn_var.comment = "区块脚本选出的出生点坐标(%s 轴)" % axis

    sens_x = tree.gk_vars.add()
    sens_x.var_name = "灵敏度X"
    sens_x.var_type = "float"
    sens_x.default_value = "1"
    sens_x.comment = "设置里的鼠标横向灵敏度倍率"

    sens_y = tree.gk_vars.add()
    sens_y.var_name = "灵敏度Y"
    sens_y.var_type = "float"
    sens_y.default_value = "1"
    sens_y.comment = "纵向灵敏度(反转上下时是负数)"

    ui = build_ui(tree)

    # ---- _ready:开始界面 + 物品栏 + 暂停菜单 ----
    ready = _add(tree, "GKNodeOnReady", "开始", -2200.0, 900.0)
    mouse_mode = _add(tree, "GKNodeMouseMode", "菜单里放出鼠标", -1950.0, 900.0,
                      gk_mode="VISIBLE")
    hint = _add(tree, "GKNodePrint", "操作说明", -1700.0, 900.0,
                gk_text=("我的世界示例:点『开始游戏』进世界;WASD 移动 / "
                         "空格 跳 / 鼠标 转视角 / 1~9 选物品 / I 背包 / "
                         "V 视角 / G 创造·生存 / 左键 挖 / 右键 用或放 / "
                         "右键点箱子 开箱 / ESC 暂停"))
    pause_menu = _add(tree, "GKNodeUiPauseGame", "菜单期间暂停世界",
                      1050.0, 900.0, gk_pause=True, gk_show_mouse=True,
                      gk_menu="世界菜单", gk_hotkey=False)
    hide_self = _add(tree, "GKNodeSetVisible", "第一人称藏起自己",
                     -1200.0, 700.0, gk_target="Model", gk_visible=False)
    chunk_loader = None
    if chunk_text is not None:
        chunk_loader = _add(tree, "GKNodeScriptLoad", "加载无限地形",
                            -950.0, 700.0, gk_script_text=chunk_text,
                            gk_instance="", gk_start_method="")
    weather_loader = None
    if weather_text is not None:
        weather_loader = _add(tree, "GKNodeScriptLoad", "加载天气",
                              -700.0, 700.0, gk_script_text=weather_text,
                              gk_instance="", gk_start_method="")
    sheep_loader = None
    if sheep_text is not None:
        sheep_loader = _add(tree, "GKNodeScriptLoad", "加载羊",
                            -450.0, 700.0, gk_script_text=sheep_text,
                            gk_instance="", gk_start_method="")
    sky_loader = None
    if sky_text is not None:
        sky_loader = _add(tree, "GKNodeScriptLoad", "加载天空",
                          -200.0, 700.0, gk_script_text=sky_text,
                          gk_instance="", gk_start_method="")
    hand_loader = None
    if hand_text is not None:
        hand_loader = _add(tree, "GKNodeScriptLoad", "加载手持模型",
                           50.0, 700.0, gk_script_text=hand_text,
                           gk_instance="", gk_start_method="")
    outline_loader = None
    if outline_text is not None:
        outline_loader = _add(tree, "GKNodeScriptLoad", "加载选中框",
                              300.0, 700.0, gk_script_text=outline_text,
                              gk_instance="", gk_start_method="")
    creature_loader = None
    if creature_text is not None:
        creature_loader = _add(tree, "GKNodeScriptLoad", "加载生物模型跟随",
                               550.0, 700.0, gk_script_text=creature_text,
                               gk_instance="", gk_start_method="")
    save_loader = None
    if save_text is not None:
        save_loader = _add(tree, "GKNodeScriptLoad", "加载存档脚本",
                           800.0, 700.0, gk_script_text=save_text,
                           gk_instance="", gk_start_method="")
    settings_loader = None
    if settings_text is not None:
        settings_loader = _add(tree, "GKNodeScriptLoad", "加载设置脚本",
                               950.0, 700.0, gk_script_text=settings_text,
                               gk_instance="", gk_start_method="")
    inventory_loader = None
    if inventory_text is not None:
        inventory_loader = _add(tree, "GKNodeScriptLoad", "加载背包",
                                1200.0, 700.0, gk_script_text=inventory_text,
                                gk_instance="", gk_start_method="")
    swim_loader = None
    if swim_text is not None:
        swim_loader = _add(tree, "GKNodeScriptLoad", "加载游泳",
                           1450.0, 700.0, gk_script_text=swim_text,
                           gk_instance="", gk_start_method="")
    input_loader = None
    if input_text is not None:
        input_loader = _add(tree, "GKNodeScriptLoad", "加载输入保护",
                            1700.0, 700.0, gk_script_text=input_text,
                            gk_instance="", gk_start_method="")
    pig_loader = None
    if pig_text is not None:
        pig_loader = _add(tree, "GKNodeScriptLoad", "加载猪",
                          1950.0, 700.0, gk_script_text=pig_text,
                          gk_instance="", gk_start_method="")
    _link(tree, ready, "执行", mouse_mode, "执行")
    _link(tree, mouse_mode, "完成", hint, "执行")
    _link(tree, hint, "完成", ui["panel"], "执行")
    _link(tree, ui["panel"], "完成", ui["title"], "执行")
    _link(tree, ui["title"], "完成", ui["start"], "执行")
    _link(tree, ui["start"], "完成", ui["load"], "执行")
    _link(tree, ui["load"], "完成", ui["create"], "执行")
    _link(tree, ui["create"], "完成", ui["quit"], "执行")
    _link(tree, ui["quit"], "完成", ui["hotbar"], "执行")
    previous_ui = ui["hotbar"]
    for background in ui["slot_bgs"]:
        _link(tree, previous_ui, "完成", background, "执行")
        previous_ui = background
    for icon in ui["icons"]:
        _link(tree, previous_ui, "完成", icon, "执行")
        previous_ui = icon
    _link(tree, previous_ui, "完成", ui["selected"], "执行")
    _link(tree, ui["selected"], "完成", ui["item_hint"], "执行")
    _link(tree, ui["item_hint"], "完成", ui["cross_h"], "执行")
    _link(tree, ui["cross_h"], "完成", ui["cross_v"], "执行")
    _link(tree, ui["cross_v"], "完成", ui["pause_panel"], "执行")
    _link(tree, ui["pause_panel"], "完成", ui["pause_title"], "执行")
    _link(tree, ui["pause_title"], "完成", ui["resume"], "执行")
    _link(tree, ui["resume"], "完成", ui["save"], "执行")
    _link(tree, ui["save"], "完成", ui["settings_pause"], "执行")
    _link(tree, ui["settings_pause"], "完成", ui["respawn"], "执行")
    _link(tree, ui["respawn"], "完成", ui["quit_pause"], "执行")
    _link(tree, ui["quit_pause"], "完成", hide_self, "执行")
    tail = hide_self
    for loader in (chunk_loader, weather_loader, sheep_loader, sky_loader,
                   hand_loader, outline_loader, creature_loader, save_loader,
                   settings_loader, inventory_loader, swim_loader,
                   input_loader, pig_loader):
        if loader is None:
            continue
        _link(tree, tail, "完成", loader, "执行")
        tail = loader
    _link(tree, tail, "完成", pause_menu, "执行")

    # 主按钮:交给存档脚本的 open_menu_primary —— 只做继续(有档读最近一档,
    # 没档提示先新建),新建只走下面的『新建世界』。
    if save_text is not None:
        primary = _add(tree, "GKNodeScriptCall", "菜单主入口", -450.0, 1150.0,
                       gk_script_text=save_text,
                       gk_method="open_menu_primary")
        _link(tree, ui["start"], "点击", primary, "执行")
    else:
        resume = _add(tree, "GKNodeUiPauseGame", "开始游戏", -450.0, 1150.0,
                      gk_pause=False, gk_show_mouse=True,
                      gk_menu="世界菜单", gk_hotkey=False)
        lock_mouse = _add(tree, "GKNodeMouseMode", "锁回鼠标", -200.0, 1150.0,
                          gk_mode="CAPTURED")
        _link(tree, ui["start"], "点击", resume, "执行")
        _link(tree, resume, "完成", lock_mouse, "执行")

    # 『读取存档』:弹脚本搭的列表面板(存 / 读都在『我的世界存档』里)
    if save_text is not None:
        open_load = _add(tree, "GKNodeScriptCall", "打开读档面板", -450.0,
                         1550.0, gk_script_text=save_text,
                         gk_method="open_load")
        _link(tree, ui["load"], "点击", open_load, "执行")
        # 『创建游戏』:参数面板也在脚本里(名称 / 种子 / 地形 / 大小 / 植被 /
        # 昼夜 / 渲染距离 / 生存或创造)
        open_create = _add(tree, "GKNodeScriptCall", "打开创建面板", -450.0,
                           1350.0, gk_script_text=save_text,
                           gk_method="open_create")
        _link(tree, ui["create"], "点击", open_create, "执行")
    if settings_text is not None:
        # 开始菜单不再放『设置』(按设计稿固定四个入口),设置只从暂停菜单进
        open_settings2 = _add(tree, "GKNodeScriptCall", "打开设置面板2",
                              -200.0, 1750.0, gk_script_text=settings_text,
                              gk_method="open_settings")
        _link(tree, ui["settings_pause"], "点击", open_settings2, "执行")

    quit_node = _add(tree, "GKNodeQuitGame", "退出", 50.0, 1150.0)
    _link(tree, ui["quit"], "点击", quit_node, "执行")

    # 暂停菜单的三个按钮
    resume_pause = _add(tree, "GKNodeUiPauseGame", "继续游戏", -450.0, 2000.0,
                        gk_pause=False, gk_show_mouse=True,
                        gk_menu="暂停菜单", gk_hotkey=False)
    lock_pause = _add(tree, "GKNodeMouseMode", "锁回鼠标3", -200.0, 2000.0,
                      gk_mode="CAPTURED")
    _link(tree, ui["resume"], "点击", resume_pause, "执行")
    _link(tree, resume_pause, "完成", lock_pause, "执行")

    # 『保存游戏』:弹脚本搭的命名对话框,存完自动回到游戏
    if save_text is not None:
        open_save = _add(tree, "GKNodeScriptCall", "打开保存面板", -450.0,
                         2400.0, gk_script_text=save_text,
                         gk_method="open_save")
        _link(tree, ui["save"], "点击", open_save, "执行")

    # 出生点是区块脚本按地形挑出来的运行时坐标(玩家变量 出生点X/Y/Z),
    # 所以这里调存档脚本的 respawn_player(),不再写死 PLAYER_SPAWN。
    if save_text is not None:
        respawn_pos = _add(tree, "GKNodeScriptCall", "送回出生点2",
                           -450.0, 2200.0, gk_script_text=save_text,
                           gk_method="respawn_player")
    else:
        respawn_pos = _add(tree, "GKNodeSetPosition", "送回出生点2",
                           -450.0, 2200.0, gk_target="__self__",
                           gk_x=PLAYER_SPAWN[0], gk_y=PLAYER_SPAWN[1],
                           gk_z=PLAYER_SPAWN[2])
    resume_spawn = _add(tree, "GKNodeUiPauseGame", "回出生点后继续",
                        -200.0, 2200.0, gk_pause=False, gk_show_mouse=True,
                        gk_menu="暂停菜单", gk_hotkey=False)
    lock_spawn = _add(tree, "GKNodeMouseMode", "锁回鼠标4", 50.0, 2200.0,
                      gk_mode="CAPTURED")
    _link(tree, ui["respawn"], "点击", respawn_pos, "执行")
    _link(tree, respawn_pos, "完成", resume_spawn, "执行")
    _link(tree, resume_spawn, "完成", lock_spawn, "执行")

    quit_pause_node = _add(tree, "GKNodeQuitGame", "退出2", 300.0, 2000.0)
    _link(tree, ui["quit_pause"], "点击", quit_pause_node, "执行")

    # ---- _process:序列(挖 / 用 / 选物品 / V / G / ESC) ----
    # 视角旋转在 _physics_process 里(和鼠标位移的"每物理帧清零"同一节奏),
    # 以前放在渲染帧会一帧转好几次 / 卡顿帧里又整段丢掉 —— 用户反馈"有时转不动"。
    process = _add(tree, "GKNodeOnProcess", "每帧", -1600.0, 200.0)
    sequence = _add(tree, "GKNodeSequence", "每帧六件事", -1300.0, 200.0,
                    gk_step_count=6)
    _link(tree, process, "执行", sequence, "执行")

    mouse = _add(tree, "GKNodeMouseMotion", "鼠标移动", -1600.0, -60.0)
    split = _add(tree, "GKNodeVectorSplit", "拆 XY", -1350.0, -60.0)
    # 鼠标往右 = 视角往右(rotation_degrees.y 减小),所以两个灵敏度都是负的
    yaw = _add(tree, "GKNodeMath", "左右灵敏度", -1100.0, -60.0,
               gk_op="MUL", gk_b=-0.12)
    pitch = _add(tree, "GKNodeMath", "上下灵敏度", -1100.0, -240.0,
                 gk_op="MUL", gk_b=-0.12)
    # 设置里的灵敏度倍率(默认 1;反转上下时"灵敏度Y"是负数)
    sens_x_read = _add(tree, "GKNodeGetVar", "读灵敏度X", -1100.0, -380.0,
                       gk_var_name="灵敏度X")
    sens_y_read = _add(tree, "GKNodeGetVar", "读灵敏度Y", -1100.0, -500.0,
                       gk_var_name="灵敏度Y")
    yaw_final = _add(tree, "GKNodeMath", "横向倍率", -950.0, -60.0,
                     gk_op="MUL")
    pitch_final = _add(tree, "GKNodeMath", "纵向倍率", -950.0, -240.0,
                       gk_op="MUL")
    camera = _add(tree, "GKNodeCameraRotation", "第一人称转视角", -800.0, -120.0,
                  gk_target=CAMERA_NAME, gk_mode="ADD",
                  gk_smooth=0.0, gk_clamp_pitch=True)
    camera3 = _add(tree, "GKNodeCameraRotation", "第三人称转视角", -600.0, -120.0,
                   gk_target=THIRD_CAMERA, gk_mode="ADD",
                   gk_smooth=0.0, gk_clamp_pitch=True)
    _link(tree, mouse, "移动量", split, "向量")
    _link(tree, split, "X", yaw, "A")
    _link(tree, split, "Y", pitch, "A")
    _link(tree, yaw, "结果", yaw_final, "A")
    _link(tree, sens_x_read, "值", yaw_final, "B")
    _link(tree, pitch, "结果", pitch_final, "A")
    _link(tree, sens_y_read, "值", pitch_final, "B")
    _link(tree, yaw_final, "结果", camera, "偏航")
    _link(tree, pitch_final, "结果", camera, "俯仰")
    _link(tree, yaw_final, "结果", camera3, "偏航")
    _link(tree, pitch_final, "结果", camera3, "俯仰")

    # 挖掘(第两百一十四轮)收进区块脚本:左键按住累积进度、按硬度出 10 阶段
    # 裂纹,创造模式即挖即碎。节点图这边不再"点一下就 QueueFree"。
    ray = _add(tree, "GKNodeRaycast", "准星射线", -1100.0, -520.0,
               gk_origin="CAMERA", gk_dir_mode="CAMERA_FORWARD",
               gk_distance=6.0, gk_layer=1)
    _link(tree, sequence, "1", ray, "执行")

    # 右键:剑攻击 / 苹果回血 / 方块放置 / 开箱子 —— 先按手上物品分岔
    right = _add(tree, "GKNodeMouseButtonDown", "右键刚按下", -1100.0, -1000.0,
                 gk_button="RIGHT")
    use_branch = _add(tree, "GKNodeBranch", "要用手上的东西吗", -620.0,
                      -1000.0)
    held_read = _add(tree, "GKNodeGetVar", "手上是哪类", -900.0, -820.0,
                     gk_var_name="手持类别")
    sword_check = _add(tree, "GKNodeCompare", "拿的是剑吗", -620.0, -1180.0,
                       gk_op="EQ", gk_b="3")
    sword_branch = _add(tree, "GKNodeBranch", "是剑", -400.0, -1100.0)
    apple_check = _add(tree, "GKNodeCompare", "拿的是苹果吗", -400.0, -1300.0,
                       gk_op="EQ", gk_b="2")
    apple_branch = _add(tree, "GKNodeBranch", "是苹果", -180.0, -1200.0)
    block_check = _add(tree, "GKNodeCompare", "拿的是方块吗", -180.0, -1500.0,
                       gk_op="EQ", gk_b="1")
    block_branch = _add(tree, "GKNodeBranch", "是方块", 40.0, -1300.0)
    held_block_read = _add(tree, "GKNodeGetVar", "拿的哪种方块", 40.0, -1480.0,
                           gk_var_name="手持方块")
    hit_check = _add(tree, "GKNodeCompare", "瞄准方块了吗", 40.0, -1650.0,
                     gk_op="NE", gk_b="null")
    place_branch = _add(tree, "GKNodeBranch", "要放吗", 280.0, -1200.0)
    block_switch = _add(tree, "GKNodeSwitch", "放哪种", 520.0, -1100.0,
                        gk_case_count=PLACE_SLOTS)
    # ── 放置位置:交给区块脚本按"命中点 + 法线"算相邻格 ──────────────
    # 旧写法三根轴各自 floor / 贴面,角上会偏到斜角格,而且方块原点在
    # 底面、Y 又要再降半格,放下去就有一半埋进地面(用户实测)。
    # 现在只把命中点和法线记下来,由 can_place_here() 统一算:
    # 命中格 = floor(命中点 - 法线×0.5),目标格 = 命中格 + 法线。
    # 必须打到某个面才有法线 —— 凭空点空气不会出现"凭空放方块"。
    set_hit = _add(tree, "GKNodeSetVar", "记下命中点", -900.0, -1650.0,
                   gk_var_name="放置命中")
    set_normal = _add(tree, "GKNodeSetVar", "记下命中法线", -900.0, -1880.0,
                      gk_var_name="放置法线")
    _link(tree, ray, "命中点", set_hit, "值")
    _link(tree, ray, "法线", set_normal, "值")
    _link(tree, sequence, "2", use_branch, "执行")
    # 右键只留一个主键(Q 已删)
    _link(tree, right, "刚按下", use_branch, "条件")
    _link(tree, use_branch, "是", sword_branch, "执行")
    _link(tree, held_read, "值", sword_check, "A")
    _link(tree, sword_check, "结果", sword_branch, "条件")
    _link(tree, sword_branch, "否", apple_branch, "执行")
    _link(tree, held_read, "值", apple_check, "A")
    _link(tree, apple_check, "结果", apple_branch, "条件")
    _link(tree, apple_branch, "否", block_branch, "执行")
    _link(tree, held_read, "值", block_check, "A")
    _link(tree, block_check, "结果", block_branch, "条件")
    _link(tree, block_branch, "是", place_branch, "执行")
    _link(tree, ray, "打到的东西", hit_check, "A")
    _link(tree, hit_check, "结果", place_branch, "条件")
    # ── 放下之前先查"这一格是不是空的"(用户实测:会跟已有方块叠在一起)──
    # 先记命中点 / 法线,再让区块脚本算目标格 + 形状查询;结果写进
    # 『放置允许』,这里分岔:空着才创建实例,占住了只弹一句提示。
    # (『放置目标』由脚本写回来,节点图只读,不再自己拼坐标。)
    _link(tree, place_branch, "是", set_hit, "执行")
    _link(tree, set_hit, "完成", set_normal, "执行")
    if chunk_text is not None:
        check_place = _add(tree, "GKNodeScriptCall", "查这一格空不空",
                           340.0, -1020.0, gk_script_text=chunk_text,
                           gk_method="can_place_here")
        _link(tree, set_normal, "完成", check_place, "执行")
        allow_read = _add(tree, "GKNodeGetVar", "读放置允许", 340.0, -1220.0,
                          gk_var_name="放置允许")
        allow_branch = _add(tree, "GKNodeBranch", "位置空着吗", 560.0, -1020.0)
        _link(tree, check_place, "完成", allow_branch, "执行")
        _link(tree, allow_read, "值", allow_branch, "条件")
        _link(tree, allow_branch, "是", block_switch, "执行")
        if save_text is not None:
            blocked = _add(tree, "GKNodeScriptCall", "提示位置被占",
                           560.0, -1220.0, gk_script_text=save_text,
                           gk_method="toast_blocked")
            _link(tree, allow_branch, "否", blocked, "执行")
    else:
        _link(tree, set_normal, "完成", block_switch, "执行")
    _link(tree, held_block_read, "值", block_switch, "值")
    target_read = _add(tree, "GKNodeGetVar", "读放置目标", 560.0, -1720.0,
                       gk_var_name="放置目标")
    for index in range(PLACE_SLOTS):
        label = PLACE_BLOCKS[index][0]
        spawn = _add(tree, "GKNodeAssetSpawn", "放" + label, 760.0,
                     -1100.0 - index * 180.0, gk_asset=label, gk_solid=True)
        _link(tree, block_switch, str(index), spawn, "执行")
        _link(tree, target_read, "值", spawn, "位置")
        if inventory_text is not None:
            # 放置消耗:创造模式由背包脚本自己跳过
            spent = _add(tree, "GKNodeScriptCall", "扣" + label, 1000.0,
                         -1100.0 - index * 180.0, gk_script_text=inventory_text,
                         gk_method="consume_current")
            _link(tree, spawn, "完成", spent, "执行")

    # 苹果:回 10 点血(钳在 0~100)
    heal = _add(tree, "GKNodeAddVar", "吃苹果", 540.0, -2050.0,
                gk_var_name="生命", gk_amount=10.0,
                gk_clamp=True, gk_min=0.0, gk_max=100.0)
    heal_float = _add(tree, "GKNodeFloatText", "回血飘字", 780.0, -2050.0,
                      gk_target="__self__", gk_prefix="生命 +",
                      gk_value=10.0, gk_time=0.7, gk_rise=1.0,
                      gk_color=(0.4, 1.0, 0.45, 1.0))
    _link(tree, apple_branch, "是", heal, "执行")
    if inventory_text is not None:
        # 吃苹果同样消耗 1 个(创造模式跳过)
        eaten = _add(tree, "GKNodeScriptCall", "吃掉苹果", 780.0, -1870.0,
                     gk_script_text=inventory_text, gk_method="consume_current")
        _link(tree, heal, "完成", eaten, "执行")
        _link(tree, eaten, "完成", heal_float, "执行")
    else:
        _link(tree, heal, "完成", heal_float, "执行")

    # 剑:打准星指着的怪物(单独一条 layer 4 的射线)
    sword_ray = _add(tree, "GKNodeRaycast", "剑的射线", 540.0, -2250.0,
                     gk_origin="CAMERA", gk_dir_mode="CAMERA_FORWARD",
                     gk_distance=4.0, gk_layer=4)
    sword_kill = _add(tree, "GKNodeQueueFree", "击杀", 780.0, -2250.0)
    sword_note = _add(tree, "GKNodePrint", "命中提示", 1020.0, -2250.0,
                      gk_text="剑命中怪物!")
    if pig_text is not None:
        # 猪在 layer 8(和羊同层),不接上面 layer 4 的"击杀"射线;
        # 挥剑时先让猪脚本自己打一条 layer 8 的射线:命中猪扣血 / 逃跑,
        # 打羊 / 打空气都直接返回,不影响下面原有的怪物击杀链。
        pig_hit = _add(tree, "GKNodeScriptCall", "猪受击", 300.0, -2450.0,
                       gk_script_text=pig_text, gk_method="pig_hit")
        _link(tree, sword_branch, "是", pig_hit, "执行")
        _link(tree, pig_hit, "完成", sword_ray, "执行")
    else:
        _link(tree, sword_branch, "是", sword_ray, "执行")
    _link(tree, sword_ray, "命中", sword_kill, "执行")
    _link(tree, sword_ray, "打到的东西", sword_kill, "目标")
    _link(tree, sword_kill, "完成", sword_note, "执行")

    # 选物品:1~9(第两百零九轮扩到 9 格;名字 / 手持物由背包脚本按格子内容写,
    # 这里只记"选中第几格")。『序列』最多 8 个出口,第 9 格接在第 8 个
    # 分支的『否』后面 —— 同一帧不可能同时按 8 和 9。
    slots_seq = _add(tree, "GKNodeSequence", "选物品", -1050.0, -1700.0,
                     gk_step_count=8)
    _link(tree, sequence, "3", slots_seq, "执行")
    last_branch = None
    for index in range(HOTBAR_SLOTS):
        label = HOTBAR_LABELS[index]
        key = _add(tree, "GKNodeKey", f"{index + 1} 选{label}", -800.0,
                   -1600.0 - index * 190.0, gk_key=str(index + 1),
                   gk_action=f"key_{index + 1}", gk_trigger="JUST")
        branch = _add(tree, "GKNodeBranch", f"按了{index + 1}?", -560.0,
                      -1600.0 - index * 190.0)
        setter = _add(tree, "GKNodeSetVar", f"记下{label}", -320.0,
                      -1600.0 - index * 190.0,
                      gk_var_name="方块序号", gk_value=float(index))
        if index < 8:
            _link(tree, slots_seq, str(index + 1), branch, "执行")
        else:
            _link(tree, last_branch, "否", branch, "执行")
        _link(tree, key, "结果", branch, "条件")
        _link(tree, branch, "是", setter, "执行")
        last_branch = branch

    # V:第一 / 第三人称(先按旧值分岔,再把变量改成新值 ——
    # 表达式是在使用时求值的,先赋值再判断会读到新值、方向就反了)
    view_key = _add(tree, "GKNodeKey", "V 切视角", 60.0, -2900.0,
                    gk_key="V", gk_action="view_toggle", gk_trigger="JUST")
    view_branch = _add(tree, "GKNodeBranch", "按了 V 吗", 300.0, -2900.0)
    view_read = _add(tree, "GKNodeGetVar", "读视角", 60.0, -3050.0,
                     gk_var_name="第三人称")
    view_apply = _add(tree, "GKNodeBranch", "现在是第几人称", 540.0, -2900.0)
    cam_first = _add(tree, "GKNodeCameraSetCurrent", "切第一人称", 780.0,
                     -2800.0, gk_target=CAMERA_NAME, gk_blend=0.2)
    hide_self2 = _add(tree, "GKNodeSetVisible", "藏起自己2", 1020.0, -2800.0,
                      gk_target="Model", gk_visible=False)
    view_false = _add(tree, "GKNodeSetVar", "记第一人称", 1260.0, -2800.0,
                      gk_var_name="第三人称", gk_value=0.0)
    false_value = _add(tree, "GKNodeConstBool", "假", 1260.0, -2920.0,
                       gk_value=False)
    cam_third = _add(tree, "GKNodeCameraSetCurrent", "切第三人称", 780.0,
                     -3050.0, gk_target=THIRD_CAMERA, gk_blend=0.2)
    show_self = _add(tree, "GKNodeSetVisible", "显示自己", 1020.0, -3050.0,
                     gk_target="Model", gk_visible=True)
    view_true = _add(tree, "GKNodeSetVar", "记第三人称", 1260.0, -3050.0,
                     gk_var_name="第三人称", gk_value=1.0)
    true_value = _add(tree, "GKNodeConstBool", "真", 1260.0, -3170.0,
                      gk_value=True)
    _link(tree, sequence, "4", view_branch, "执行")
    _link(tree, view_key, "结果", view_branch, "条件")
    _link(tree, view_branch, "是", view_apply, "执行")
    _link(tree, view_read, "值", view_apply, "条件")
    _link(tree, view_apply, "是", cam_first, "执行")
    _link(tree, cam_first, "完成", hide_self2, "执行")
    _link(tree, hide_self2, "完成", view_false, "执行")
    _link(tree, false_value, "值", view_false, "值")
    _link(tree, view_apply, "否", cam_third, "执行")
    _link(tree, cam_third, "完成", show_self, "执行")
    _link(tree, show_self, "完成", view_true, "执行")
    _link(tree, true_value, "值", view_true, "值")

    # G:创造 / 生存
    mode_key = _add(tree, "GKNodeKey", "G 切模式", 60.0, -3200.0,
                    gk_key="G", gk_action="mode_toggle", gk_trigger="JUST")
    mode_branch = _add(tree, "GKNodeBranch", "按了 G 吗", 300.0, -3200.0)
    mode_read = _add(tree, "GKNodeGetVar", "读模式", 60.0, -3350.0,
                     gk_var_name="创造模式")
    mode_not = _add(tree, "GKNodeLogic", "模式取反", 300.0, -3350.0, gk_op="NOT")
    mode_set = _add(tree, "GKNodeSetVar", "记下模式", 540.0, -3350.0,
                    gk_var_name="创造模式", gk_value=0.0)
    _link(tree, sequence, "5", mode_branch, "执行")
    _link(tree, mode_key, "结果", mode_branch, "条件")
    _link(tree, mode_branch, "是", mode_set, "执行")
    _link(tree, mode_read, "值", mode_not, "A")
    _link(tree, mode_not, "结果", mode_set, "值")

    esc = _add(tree, "GKNodeKey", "ESC", 60.0, -3500.0,
               gk_key="ESC", gk_action="ui_cancel", gk_trigger="JUST")
    esc_branch = _add(tree, "GKNodeBranch", "要暂停吗", 300.0, -3500.0)
    pause_game = _add(tree, "GKNodeUiPauseGame", "暂停", 540.0, -3500.0,
                      gk_pause=True, gk_show_mouse=True,
                      gk_menu="暂停菜单", gk_hotkey=False)
    _link(tree, sequence, "6", esc_branch, "执行")
    _link(tree, esc, "结果", esc_branch, "条件")
    _link(tree, esc_branch, "是", pause_game, "执行")

    # ---- _physics_process:移动 + 怪物追击 + HUD + 跳跃 ----
    physics = _add(tree, "GKNodeOnPhysicsProcess", "物理帧", -1600.0, -1200.0)
    direction = _add(tree, "GKNodeInputDirection", "WASD", -1600.0, -1450.0)
    move = _add(tree, "GKNodeMoveDirection", "移动", -1300.0, -1200.0,
                gk_speed=6.0, gk_keep_y=True, gk_follow_camera=True,
                gk_face_move=False)
    on_floor = _add(tree, "GKNodeIsOnFloor", "在地面", -1300.0, -1500.0)
    jump_key = _add(tree, "GKNodeKey", "空格", -1300.0, -1680.0,
                    gk_action="jump", gk_trigger="JUST")
    both = _add(tree, "GKNodeLogic", "能跳吗", -1050.0, -1550.0, gk_op="AND")
    _link(tree, physics, "执行", camera, "执行")
    _link(tree, camera, "完成", camera3, "执行")
    _link(tree, camera3, "完成", move, "执行")
    _link(tree, direction, "方向", move, "方向")

    # 导出的相机是场景根节点(不挂在角色下面),用『相机_跟随』让它贴着玩家走;
    # 距离留 0 = 保持摆好的相对位置(第一人称 1.6 米眼高 / 第三人称 3.5 米后)。
    follow = _add(tree, "GKNodeCameraFollow", "第一人称跟着玩家",
                  -1050.0, -1800.0, gk_target="__self__",
                  gk_camera=CAMERA_NAME, gk_smooth=0.0, gk_distance=0.0)
    follow3 = _add(tree, "GKNodeCameraFollow", "第三人称跟着玩家",
                   -800.0, -1800.0, gk_target="__self__",
                   gk_camera=THIRD_CAMERA, gk_smooth=0.0, gk_distance=0.0)

    # 怪物这轮先站着不动(用户:"先别让那些怪物动和攻击");模型跟随脚本还在,
    # 以后要恢复追击,把『移动_朝向目标』接回这条链就行。
    previous = follow3
    _link(tree, move, "完成", follow, "执行")
    _link(tree, follow, "完成", follow3, "执行")

    health_read = _add(tree, "GKNodeGetVar", "读生命", -1500.0, -1900.0,
                       gk_var_name="生命")
    health_num = _add(tree, "GKNodeNumberText", "生命数字", -1300.0, -1900.0)
    health_join = _add(tree, "GKNodeTextJoin", "生命文字", -1100.0, -1900.0,
                       gk_a="生命 ")
    hp_text = _add(tree, "GKNodeUiText", "生命文本", -850.0, -1850.0,
                   gk_size=20, gk_align="TOP_LEFT", gk_x=20.0, gk_y=44.0)
    hp_bar = _add(tree, "GKNodeUiBar", "生命条", -560.0, -1850.0,
                  gk_max=100.0, gk_x=20.0, gk_y=76.0,
                  gk_width=220.0, gk_height=16.0,
                  gk_color=(0.85, 0.22, 0.22, 1.0))
    _link(tree, health_read, "值", health_num, "数值")
    _link(tree, health_read, "值", hp_bar, "数值")
    _link(tree, health_num, "文字", health_join, "乙")
    _link(tree, health_join, "结果", hp_text, "文字")
    _link(tree, previous, "完成", hp_text, "执行")
    _link(tree, hp_text, "完成", hp_bar, "执行")

    # HUD 第二行:手持物品 + 创造/生存
    mode_read = _add(tree, "GKNodeGetVar", "读模式2", -1500.0, -2050.0,
                     gk_var_name="创造模式")
    mode_yes = _add(tree, "GKNodeConstText", "创造名", -1300.0, -2100.0,
                    gk_value="创造")
    mode_no = _add(tree, "GKNodeConstText", "生存名", -1300.0, -2200.0,
                   gk_value="生存")
    mode_text = _add(tree, "GKNodeSelect", "模式文字", -1100.0, -2100.0,
                     gk_value_type="TEXT")
    item_join = _add(tree, "GKNodeTextJoin", "手持文字", -1300.0, -1900.0,
                     gk_a="手持:", gk_middle=" ")
    name_read = _add(tree, "GKNodeGetVar", "读手持名", -1500.0, -1950.0,
                     gk_var_name="手持名")
    hud_join = _add(tree, "GKNodeTextJoin", "HUD文字", -1100.0, -1900.0,
                    gk_middle=" · ")
    item_text = _add(tree, "GKNodeUiText", "手持文本", -850.0, -2000.0,
                     gk_size=16, gk_align="TOP_LEFT", gk_x=20.0, gk_y=110.0)
    _link(tree, name_read, "值", item_join, "乙")
    _link(tree, item_join, "结果", hud_join, "甲")
    _link(tree, mode_text, "结果", hud_join, "乙")
    _link(tree, hud_join, "结果", item_text, "文字")
    _link(tree, mode_read, "值", mode_text, "条件")
    _link(tree, mode_yes, "值", mode_text, "A")
    _link(tree, mode_no, "值", mode_text, "B")
    _link(tree, hp_bar, "完成", item_text, "执行")

    jump_branch = _add(tree, "GKNodeBranch", "如果", -800.0, -1200.0)
    # 5.2 的起跳力 + 9.8 重力 = 约 1.3 格高(能跳上一格台阶,又不会飞起来)
    jump = _add(tree, "GKNodeJump", "跳跃", -550.0, -1200.0, gk_jump_force=5.2)
    _link(tree, item_text, "完成", jump_branch, "执行")
    _link(tree, on_floor, "在地面", both, "A")
    _link(tree, jump_key, "结果", both, "B")
    _link(tree, both, "结果", jump_branch, "条件")
    _link(tree, jump_branch, "是", jump, "执行")

    log(f"节点图:{len(tree.nodes)} 个节点 / {len(tree.links)} 条连线")
    return tree


# ==========================================================================
# 导出
# ==========================================================================
def save_blend(path):
    if not bpy.app.background:
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return
    blocks = set(bpy.data.objects) | set(bpy.data.meshes) | \
        set(bpy.data.materials) | set(bpy.data.node_groups) | \
        set(bpy.data.cameras) | set(bpy.data.lights) | \
        set(bpy.data.curves) | set(bpy.data.texts)
    scenes = list(bpy.data.scenes)
    bpy.data.libraries.write(path, blocks | set(scenes), fake_user=True)


def main():
    from BlenderGameKit import asset_export, project, register

    if bpy.types.NodeTree.bl_rna_get_subclass_py("GKUiDesignTree", None) is None:
        register()

    os.makedirs(OUT_DIR, exist_ok=True)
    scene = clear_all()
    # 无限地形 / 天气 / 羊:都存在 .blend 的文本块里,由『脚本_加载』运行
    chunk_text = bpy.data.texts.new("我的世界区块")
    chunk_text.use_fake_user = True
    chunk_text.write(CHUNK_SCRIPT)
    weather_text = bpy.data.texts.new("我的世界天气")
    weather_text.use_fake_user = True
    weather_text.write(WEATHER_SCRIPT)
    sheep_text = bpy.data.texts.new("我的世界羊")
    sheep_text.use_fake_user = True
    sheep_text.write(SHEEP_SCRIPT)
    sky_text = bpy.data.texts.new("我的世界天空")
    sky_text.use_fake_user = True
    sky_text.write(SKY_SCRIPT)
    hand_text = bpy.data.texts.new("我的世界手持")
    hand_text.use_fake_user = True
    hand_text.write(HAND_SCRIPT)
    outline_text = bpy.data.texts.new("我的世界选中框")
    outline_text.use_fake_user = True
    outline_text.write(OUTLINE_SCRIPT)
    creature_text = bpy.data.texts.new("我的世界生物")
    creature_text.use_fake_user = True
    creature_text.write(CREATURE_SCRIPT)
    save_text = bpy.data.texts.new("我的世界存档")
    save_text.use_fake_user = True
    save_text.write(SAVE_SCRIPT)
    settings_text = bpy.data.texts.new("我的世界设置")
    settings_text.use_fake_user = True
    settings_text.write(SETTINGS_SCRIPT)
    inventory_text = bpy.data.texts.new("我的世界背包")
    inventory_text.use_fake_user = True
    inventory_text.write(INVENTORY_SCRIPT)
    swim_text = bpy.data.texts.new("我的世界游泳")
    swim_text.use_fake_user = True
    swim_text.write(SWIM_SCRIPT)
    input_text = bpy.data.texts.new("我的世界输入保护")
    input_text.use_fake_user = True
    input_text.write(INPUT_GUARD_SCRIPT)
    pig_text = bpy.data.texts.new("我的世界猪")
    pig_text.use_fake_user = True
    pig_text.write(PIG_SCRIPT)
    build_world(scene)
    build_tree(scene, chunk_text, weather_text, sheep_text, sky_text, hand_text,
               outline_text, creature_text, save_text, settings_text,
               inventory_text, swim_text, input_text, pig_text)
    apply_scene_settings(scene)

    project.create_project(GODOT_DIR, title="我的世界",
                           blend_file_name=os.path.basename(BLEND_PATH))
    result = asset_export.export_all_scenes(GODOT_DIR, dimension="3")
    if result.get("error"):
        log(f"导出失败:{result['error']}")
        return 1
    for entry in result.get("scenes", []):
        log(f"场景 {entry['scene']} -> {entry['scene_name']}.tscn"
            f"(脚本 {entry['script_name']})")
    for warning in result.get("warnings", []):
        log(f"告警: {warning}")
    for entry in result.get("scenes", []):
        if entry.get("ok"):
            main_scene = f"res://scenes/{entry['scene_name']}.tscn"
            project.set_main_scene(GODOT_DIR, main_scene)
            log(f"主场景: {main_scene}")
            break

    # 贴图用我们自己的 PNG 覆盖导出结果:导出链会走一次 Blender 的色彩管理,
    # 像素图被压过一道就不是原来的颜色了。运行时地形(区块脚本)也读这些文件。
    art_dir = os.path.join(GODOT_DIR, "art")
    os.makedirs(art_dir, exist_ok=True)
    for name in BLOCK_TEXTURES.values():
        source = os.path.join(TEXTURE_DIR, name + ".png")
        if os.path.isfile(source):
            shutil.copyfile(source, os.path.join(art_dir, name + ".png"))
    for name, source in ensure_item_icons().items():
        shutil.copyfile(source, os.path.join(art_dir, name + ".png"))
    # 10 阶段破坏裂纹(叠加在方块表面,需要透明通道)
    for stage in range(1, 11):
        name = "mc_crack_%d" % stage
        source = os.path.join(TEXTURE_DIR, name + ".png")
        if os.path.isfile(source):
            shutil.copyfile(source, os.path.join(art_dir, name + ".png"))
    shutil.copyfile(ensure_menu_background(),
                    os.path.join(art_dir, "mc_menu_bg.png"))
    shutil.copyfile(ensure_cloud_texture(),
                    os.path.join(art_dir, "mc_cloud.png"))
    # 玩家皮肤是独立资源:换皮肤直接替换 art/skin/ 里的 64×64 PNG 即可。
    skin_dir = os.path.join(art_dir, "skin")
    os.makedirs(skin_dir, exist_ok=True)
    for name, source in ensure_player_skin().items():
        shutil.copyfile(source, os.path.join(skin_dir, name + ".png"))
    # 背包用 art/ui 里现成的像素面板(只读源,复制到产物 art/ui/)
    ui_source = os.path.join(ROOT, "art", "ui")
    ui_dir = os.path.join(art_dir, "ui")
    os.makedirs(ui_dir, exist_ok=True)
    for name in ("inventory_bg", "slot", "hotbar", "hotbar_selector"):
        source = os.path.join(ui_source, name + ".png")
        if os.path.isfile(source):
            shutil.copyfile(source, os.path.join(ui_dir, name + ".png"))

    save_blend(BLEND_PATH)
    log(f"已保存 {BLEND_PATH}")
    log(f"工程目录: {GODOT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
