"""导出资产到 Godot:glTF(.glb) + 场景(.tscn) + 材质(.tres)。

为什么需要它
------------
Blender 里的模型不能"自带脚本"。Godot 的脚本只能挂在**节点**上，
而 .glb 导入后只是一个网格节点。所以正确的做法是把三件事分开:

    模型      Blender 网格/骨骼/动画  --glTF-->  .glb          (几何)
    材质      Blender 材质            --生成-->  .tres + .gdshader (外观)
    行为      节点图                  --生成-->  .gd            (逻辑)
    装配      ----  生成的 .tscn 把上面三者拼在一起  ----        (节点树)

这个模块负责"装配":导出 .glb，生成引用它的 .tscn，并把脚本挂到根节点上。
这样在 Godot 里打开就是"模型 + 碰撞体 + 脚本 + 卡通材质"的完整角色。

贴图怎么办
----------
glTF 会把贴图**内嵌**进 .glb(默认)或导出为外部文件。两种都能被 Godot 直接读。
Blender 里材质的 Base Color 贴图会变成 Godot 的 albedo_texture，
所以本插件生成的卡通材质会自动带上它(见 _base_color_texture)。
"""

import math
import os
import re

from . import generated_files
from . import gk_log
from . import godot_look
from . import npr_preview
from . import project
from . import sound_assets
from .nodes.node_base import gd_node_path, snake_case

# 导出的 .tscn 里，脚本与模型各自的 res:// 路径
#
# `{env_resource}` 是 Environment 脚本的 ext_resource 行。
# ── 为什么单独拿一个占位符出来(踩过的坑)──────────────────────────
# 以前这一行是"env_files 非空才加"。但 `build_environment_nodes` 只在
# **需要生成相机/灯节点**时才写 gk_environment.gd;相机和灯都已经在 glb 里
# 的时候它不写文件(文件就在磁盘上,不用重写),于是 env_files 是空的 ——
# 那一行就不加了,可 Environment 节点又确实生成了。
# 结果:节点引用了不存在的 ExtResource,Godot 直接报
#     Parse Error: Can't load resource ... env_script
# 用户实测就是这个:"地面有材质,为什么没渲染上" —— 因为挂材质的
# Environment 脚本根本没能加载。
ENV_RESOURCE_LINE = (
    '[ext_resource type="Script" '
    'path="{path}" id="env_script"]'
)


def env_script_rel(scene_name):
    """环境脚本的相对路径 —— **每个 .tscn 一份**,不再共用一个文件。

    用户 2026-10-04(方案 ②a):以前所有场景共写 `scripts/gk_environment.gd`,
    于是 `@export var materials` 的**默认值**只能反映"最后导出的那个场景",
    其余场景全靠各自 .tscn 里 Environment 节点上的 `materials = "..."` 覆盖
    才对 —— 能跑,但脚本默认值不自包含:节点属性一旦丢了(手改场景 / 合并
    .tscn),材质就全没了。现在按 .tscn 名各写一份,脚本默认值 = 本场景
    自己的材质表,节点属性只是冗余覆盖,谁也不依赖谁。

    ── 为什么 "World" 仍叫 gk_environment.gd ────────────────────────
    主包装场景(single 时的 World)保持历史文件名,这样**单场景项目的产物
    和以前一模一样** —— 文档、已有测试(blender_not_gray / blender_scene_export)
    都指向这个名字。只有其它 .tscn(第二关、角色子场景…)才带后缀。
    """
    stem = re.sub(r'[\\/:*?"<>|\s]+', "_", str(scene_name or "")).strip("._")
    if not stem or stem == "World":
        return os.path.join(project.SCRIPTS_DIR, "gk_environment.gd")
    return os.path.join(project.SCRIPTS_DIR, f"gk_environment_{stem}.gd")


def env_script_res(scene_name):
    """`env_script_rel` 的 res:// 形式(ext_resource 行必须用正斜杠)。"""
    return "res://" + env_script_rel(scene_name).replace(os.sep, "/")


# 开头那行 `;` 注释是"插件生成"的内容标记,给旧文件清理用
# (见 generated_files.is_generated_file;Godot 4.7.2 实测能加载带注释的场景)
SCENE_TEMPLATE = """; 由 BlenderGameKit 自动生成,请勿手动修改
[gd_scene load_steps={steps} format=3]

{script_resource}
[ext_resource type="PackedScene" path="{model_path}" id="2_model"]
{env_resource}
{sub_resources}
[node name="{root_name}" type="{base_class}"]
{script_line}{collision}
[node name="Model" parent="." instance=ExtResource("2_model")]
{environment}
"""

# 相机 + 灯光的节点文本。
#
# 为什么角色场景也要带这些:用户按 F5 跑起来看到的是**一片灰**,
# 因为角色场景里只有网格 —— 没有相机就没有视点,没有灯光就全黑,
# 再加上没有环境就是 Godot 默认的灰底。这不是"渲染坏了",是场景里确实空。
# 从 Blender 场景里把相机和灯光搬过来,打开就能看见东西。
# glb 里没有相机时兜底用的节点。
# 名字用 Blender 里的原名 —— 硬编码叫 "Camera" 的话会和 glb 里的对不上,
# 看起来就像凭空多了一个。
ENVIRONMENT_CAMERA = """
[node name="{name}" type="Camera3D" parent="."]
transform = Transform3D({basis}, {cx}, {cy}, {cz})
fov = {fov:.2f}
current = true
"""

# ── 灯光节点(第四十八轮:阶段①灯光扩展)────────────────────────────
# 以前这里只有太阳一条模板、而且**只取场景里第一盏 SUN**(见 git 历史):
# 颜色 / 阴影 / 点光 / 聚光全都没有 —— 恐怖游戏要的"手电筒照出一小片"
# 根本做不出来。现在三种灯都导:
#   * SUN   -> DirectionalLight3D(平行光 / 太阳)
#   * POINT -> OmniLight3D(灯泡,随距离变暗)
#   * SPOT  -> SpotLight3D(手电筒 / 聚光)
# 颜色 / 亮度 / 阴影开关 / 柔和尺寸都带上;点光与聚光再带范围与锥角。
#
# ── 颜色为什么不转换(说明白,免得下次又有人来"修")──────────────
# Blender 的灯光颜色是**线性**空间的值(和材质 base_color 一个习惯),
# Godot 的 light_color 也按线性参与渲染 —— 1:1 直接写,不做 sRGB 转换。
LIGHT_NODE = """
[node name="{name}" type="{type}" parent="."]
transform = Transform3D({basis}, {lx}, {ly}, {lz})
light_color = Color({r}, {g}, {b}, 1)
light_energy = {energy:.3f}
shadow_enabled = {shadow}
{extra}"""

# 让 glb 里带进来的相机成为主相机。
#
# ── 为什么需要这一步 ────────────────────────────────────────────
# glTF 会带相机(写进 cameras 数组),Godot 导入后自动生成 Camera3D,
# 位置朝向都对的。但**不会**把它设成 current ——
# 实测:没有这一步时 `get_root().get_camera_3d()` 返回 null,
# 按 F5 用的是默认视点,画面还是一片灰。
#
# ── 为什么用脚本而不是再写一个 Camera3D 节点 ────────────────────
# 直接再生成一个节点的话,场景里就有两个相机了。实测踩过:
# 用户在 Godot 里看到"多一个摄像头",而且分不清哪个是 Blender 的。
#
# 用 @tool 脚本挂在唯一的 Environment 节点上:单实例、幂等。
# 而且挂在 `_ready` 上,每次打开编辑器/运行都会重新指向正确的那个 ——
# 用户之后手动改主相机也不会被永久覆盖,下次导出再指回来。
ENVIRONMENT_SCRIPT = '''@tool
extends Node
## BlenderGameKit 生成:把 glb 带进来的相机设成主相机,并挂上导出的材质。
##
## 改这里没有用 —— 下次从 Blender 导出会被覆盖。
##
## ── 为什么需要这个脚本 ──────────────────────────────────────────
## glTF 会带相机过来(位置朝向都对),但**不会**设成 current ——
## 不设的话按 F5 看到的是默认视点,通常一片灰。
##
## 灯光**不再由这里管**:用户决定在 Godot 里自己设计灯光,
## 所以 glb 带进来的灯原样保留,这个脚本不碰它们。
## (以前这里会重算灯光强度、重建灯节点,结果灯光和 Godot 里
##  手动调的互相打架 —— 已按用户要求去掉。)

## Blender 里主相机的名字。
@export var camera_name: String = "{camera_name}"

## Blender 相机的垂直视场角(度)。<= 0 表示不改。
@export var camera_fov: float = {fov}

## 材质映射:"节点名=材质路径",多条之间用 **|** 分隔。
## 形如 "Ball=res://materials/Ballmat.tres|Floor=res://materials/Floor.tres"
## 也可以用 "节点名#序号=路径" 只替换某个表面(MMD 那种一个网格几十个
## 材质槽的模型必须这样,否则整网格覆盖会把所有表面刷成同一个材质)。
##
## ── 为什么不用换行分隔(踩过的坑)────────────────────────────────
## 原来这里用换行分隔,结果 Godot 直接报
##     Parse Error: Expected newline after comment string
## 因为 `@export_multiline` 的**默认值里不能放真的换行符** ——
## 那是 GDScript 字符串字面量的语法错误,整个脚本都加载不了。
## 竖线在路径和节点名里都不会出现,安全。
@export var materials: String = "{materials}"

## ── 卡通材质是否跟着场景灯光画明暗(默认关 = 全平涂)──────────────
## 背景:gk_toon.gdshader 是 `unshaded` 的 —— Godot 不做任何光照,
## 明暗全部由 shader 用 `light_dir` / `light_color` 自己算。
## 好处是色阶不会被环境光冲淡,但代价是**角色感觉不到场景里的灯**。
##
## 这个开关的默认值由 Blender 场景里的『卡通跟随场景光照』决定(默认关)。
## 关掉时除了 _update_light 直接返回,还会调 _apply_flat 把明暗**关死**
## (light_dir 置零)—— 只"不喂光"是不够的:shader 里 light_dir 有内置
## 默认方向,不喂它照样画一片和场景无关的假阴影(用户报的"阴影不正常")。
##
## 打开后:每个物理帧把场景里第一盏 DirectionalLight3D 的方向/颜色/
## 亮度同步给所有卡通材质。色阶仍是 shader 算的(硬边、冷色阴影都在),
## 但方向跟着场景灯走 —— 转太阳,角色身上的阴影跟着转。
@export var follow_scene_light: bool = {follow_scene_light}

## 想指定用哪盏灯就填它的名字;留空 = 自动挑(见 _gk_lights_on)。
@export var light_name: String = ""

## ── 场景开局 BGM(第七十七轮,C 组『场景规则』)──────────────────────
## 在侧栏『导出为 Godot 脚本』里选一首:开局自动播、**循环**。
## 音频走和『资产_声音卡』同一条管线(导出时复制进项目的 audio/),
## 所以这里是一条能直接 load 的 res:// 路径。
##
## 留空 = 不播(照旧:自己接『每当_准备 → 音效_音乐』)。
##
## ── 为什么自己造播放器而不是在 .tscn 里摆一个节点 ────────────────
## 环境脚本是**每场景一份** —— BGM 摆节点的话每个场景都要重复一遍;
## 造在这里:选了就有、清空就没了,只此一处。
@export var bgm_path: String = "{bgm_path}"

## 开局 BGM 的音量(dB)。0 = 原始,负数是调小。
@export var bgm_volume_db: float = {bgm_volume}

## 开局 BGM 是否循环(第八十六轮『游戏音乐』图 —— 默认开 = 老行为)。
@export var bgm_loop: bool = {bgm_loop}

## 开局 BGM 走哪条音频总线(留空 = Master)。
## 想单独压背景音乐:先在 Godot 的『音频』面板建一条 Music 总线再填名字。
@export var bgm_bus: String = "{bgm_bus}"

## ── 音乐总线音量(第八十六轮:『游戏音乐』图的『音乐_总线音量』卡)────
## 勾了『开局应用』才生效(_gk_apply_music_bus);不勾时下面三行不起作用,
## 运行时行为与以前完全一致(老工程导出产物只多这几行字)。
@export var music_bus: String = "{music_bus}"
@export var music_bus_db: float = {music_bus_db}
@export var music_bus_apply: bool = {music_bus_apply}

## ── 手电筒:跟着相机走的灯(第四十八轮:阶段①灯光扩展)──────────────
## 在 Blender 里给灯勾上『跟随相机(手电筒)』(『游戏物理』面板 -> 灯光),
## 导出会把这盏灯的名字写进来(多盏用竖线分隔)。运行时**每物理帧**把
## 它的位置和朝向对齐**当前相机** —— 手电筒 / 头灯的效果。
## 灯的颜色 / 亮度 / 影子照旧用它自己的设置,这里只搬 transform;
## 编辑器里不搬(免得在编辑器里乱动场景)。
@export var follow_lights: String = "{follow_lights}"

## ── 上帝摄像机(关卡测试)──────────────────────────────────────────
## 场景设置里开了『上帝摄像机』时,这里常驻一台自由飞行相机:
## 运行时按 Tab 在"玩家相机 / 上帝摄像机"之间切;上帝机忽略碰撞、
## WASD 平移 / 空格上升 / Shift 下降 / 鼠标转视角;上帝视角里按 T
## 还可以**时停**(冻住世界,只留上帝机飞 —— 第三十五轮)。
##
## ── 第三十九轮补齐的默认功能(用户要求"默认就该有")──────────────
##   * **滚轮缩放**:转滚轮改上帝机的视野(FOV 20~110),看细节 / 拉远看全局;
##   * **F 附身**:接管场景里的角色 —— WASD 直接驱动它走 / 跳,
##     相机跟在身后(角色自己的输入让位,见 _gk_god_camera_active 守卫),
##     再按一下 F 放开;
##   * **ESC 退出游戏**:内置的兜底退出(场景里没把 Esc 用在暂停菜单时
##     才生效,见 esc_quit)。
##
## ── 为什么放在环境脚本里,而不是角色的游戏逻辑里(用户 2026-10-04)──
## 游戏逻辑脚本挂在角色身上 —— 场景里**没有角色**就没有它,上帝机也跟着
## 不存在。用户实测:"在没有角色的测试里动不了,鼠标也无法控制视角旋转"
## —— 而"飞着看看这一关"恰恰经常是空场景 / 纯场景测试。
## 环境脚本每张 .tscn 都有一份、还挂在场景根上,所以把它搬到这里。
@export var god_camera: bool = {god_camera}

## ── 内置 ESC 退出游戏(第三十九轮)────────────────────────────────
## 真 = 按 Esc 直接退出游戏。**场景里把 Esc 用在『界面_暂停游戏』的
## 暂停热键时,导出会把它算成 false**(否则一次按键既暂停又退出,
## 两个功能互相打架)。所以这里不是"永远抢 Esc",而是"没人用才兜底"。
@export var esc_quit: bool = {esc_quit}

## 收集到的卡通材质(第四十八轮起**连宿主节点一起收** —— 多光近似要按
## 每个材质自己的位置算"离哪盏灯近"):[[MeshInstance3D, ShaderMaterial], …]。
var _gk_mats: Array = []

## 上帝摄像机的运行状态(Tab 切换 / **自己**那份鼠标位移)。
var _gk_god_cam: Camera3D = null
var _gk_god_player_cam: Camera3D = null
var _gk_god_tab_held: bool = false
var _gk_time_stop_held: bool = false
var _gk_god_look: Vector2 = Vector2.ZERO
var _gk_mouse_was: int = -1
## 滚轮攒下的缩放量(_input 里攒,物理帧里消费 —— 和鼠标位移同一个套路)。
var _gk_god_wheel: float = 0.0
## 上帝机自己的视野(0 = 还没缩放过,用相机原来的)。
var _gk_god_fov: float = 0.0
## 附身(第三十九轮):接管中的角色 + 按键按住标记。
var _gk_god_possess: Node3D = null
var _gk_god_possess_held: bool = false
## ESC 退出的按住标记。
var _gk_esc_held: bool = false

## ── 游戏内调试面板(用户 2026-10-05 第二十二轮:"调试体验")──────────
## 玩的时候按一个键弹出的一块 HUD:FPS / 相机 / 脚本宿主的位置速度在地面 /
## 变量实时值 / 最近 20 条打印;另一个键把全部内容复制到剪贴板。
##
## ── 变量和日志从哪来(不靠导出侧准备表)────────────────────────
## 面板**运行时按名字读**:遍历场景里的节点,谁的脚本有成员变量谁就是
## "宿主";变量值走 `host.get(名字)`,日志走宿主脚本里的 `_gk_debug_logs`
## (『打印_输出』在面板开着时会往里推一条)。所以"变量_设置"运行时
## 新建出来的变量也会自动出现在面板上。
@export var debug_panel: bool = {debug_panel}
## 只显示这些变量(竖线分隔);留空 = 脚本里所有变量都显示。
@export var debug_watch: String = "{debug_watch}"

## ── 寻路:启动时烤一块导航网格(第三十四轮;第三十六轮加 2D / 重烤)──
## 场景设置里勾了『寻路』时为 true。导航区节点(`Navigation`,
## .tscn 里 3D 是 NavigationRegion3D、2D 是 NavigationRegion2D)的参数
## 由导出写死,这里负责**烤**:
##   * 3D:按场景里的**静态碰撞体**(地面 / 墙 / 高台 / 树 / 石头)生成
##     可行走的多边形 —— 角色、载具这些会动的东西不参与(只解析
##     StaticBody 的碰撞体,实测真 Godot:角色站的地方网格是完整的);
##   * 2D:碰撞体按**障碍**解析,先按所有静态碰撞体的包围盒算一个"外框"
##     再烤(实测:2D 没有外框时烤出来 0 个多边形)。
## 『移动_重烤导航』节点运行时重烤也走这里(rebake_navigation)。
##
## 烤完之前『移动_寻路』的角色会先**直线走**(降级,不是站着不动):
## 少一块网格不该让游戏坏掉。
@export var nav_bake: bool = {nav_bake}

## ── 代价区(第八十四轮:寻路第三版②)──────────────────────────────────
## [{{name, cost, rect|box}}] —— 物体『通行代价』填 != 1 时由导出写进来。
## 空数组 = 没有代价区(那些函数直接 return,老场景行为一字不变)。
## 2D 用 `rect`(XY 平面矩形),3D 用 `box`(世界 AABB)。
var nav_costs: Array = {nav_costs}

const _GK_DBG_KEY := {debug_key}            # 弹出 / 收起面板
const _GK_DBG_COPY_KEY := {debug_copy_key}  # 复制全部内容

## ── 上帝视角里的"时停"(第三十五轮,用户 2026-10-06)────────────────────
## "在上帝摄像机时,可以添加时停的功能" —— 上帝视角里按这个键冻住 / 解冻
## 世界:场景动画、物理、角色逻辑全停,只有上帝机还能飞(环境脚本在
## god_camera 场景里设了 PROCESS_MODE_ALWAYS),方便停住画面看细节。
##
## 解冻**不挑视角**(切回玩家相机才发现还冻着,照样按得开);离开上帝机
## 也会自动解冻(见 _gk_god_toggle)。两者都是"别把自己锁在冻住的世界里"。
const _GK_TIME_STOP_KEY := {time_stop_key}

## ── 附身键(第三十九轮,用户 2026-10-06)──────────────────────────
## "上帝机默认属性功能应有:时停、视角缩放、附身(接管角色控制)" ——
## 按这个键接管最近的**角色**(挂了游戏逻辑脚本的那个),WASD 直接驱动它,
## 相机跟到身后;再按一下放开。缩放用鼠标滚轮,不需要按键。
const _GK_POSSESS_KEY := {possess_key}

## 附身时的移动速度 / 起跳冲量(和『第三人称角色预设』的默认手感对齐:
## 移动 6 米/秒、起跳 7 米/秒;重力不用常量,用角色自己的
## `get_gravity()`,这样工程重力设置改了它也跟得上)。
const _GK_POSSESS_SPEED := 6.0
const _GK_POSSESS_JUMP := 7.0

var _gk_dbg_label: Label = null
var _gk_dbg_visible := false
var _gk_dbg_accum := 0.0
var _gk_dbg_key_held := false
var _gk_dbg_copy_held := false
var _gk_dbg_hosts: Array = []
var _gk_dbg_hosts_ready := false


func _ready() -> void:
	_activate_camera()
	# ── 材质必须等一帧再挂(踩过的坑)─────────────────────────────────
	# 角色的网格现在藏在**嵌套的 glb 实例**里:
	#     Cube [CharacterBody3D]
	#       Model [instance Cube.glb]   <- 网格在这一层实例内部
	# 在 _ready() 这个时刻,实例内部的节点**还没进树**,`find_child`
	# 根本搜不到它 —— 实测报 "[GK] 没找到网格节点: Cube",
	# 角色就只剩 glb 自带的默认材质(用户看到的还是"材质没生效")。
	# 用 call_deferred 推到本帧末,那时整棵树都建好了。
	_apply_materials.call_deferred()
	# 碰撞代理配对同理:glb 实例内部的网格节点现在还没进树,搜不到。
	_gk_link_colliders.call_deferred()
	# ── 运行时**新加**进场景的网格也要补材质(用户 2026-10-05)────────
	# 『资产_创建实例』建出来的那一份,网格是 glb 实例内部的**新**节点,
	# 它进场时 _apply_materials 早就跑完了 —— 不补一次的话它一直是 glb
	# 自带的素白 PBR 材质,和旁边同样资产的树对不上(看起来像"没导出材质")。
	if not Engine.is_editor_hint():
		get_tree().node_added.connect(_gk_on_node_added)
	# 上帝机同理:玩家相机常常藏在 glb 实例内部,现在搜不到它。
	# 编辑器里**不造**(脚本是 @tool,编辑器里也会 _ready)—— 造了会在
	# 编辑器场景里凭空多一台相机,还会抢编辑器视口。
	# ── "冻住世界"必须留一条活口(第三十五轮时停 / 第三十九轮 ESC 退出)──
	# 时停是用 `get_tree().paused` 冻的 —— 整个世界都停了,谁来解冻?
	# 环境脚本(和上帝机)必须**暂停也照跑**,否则按 T 冻住之后
	# 按什么都没反应,只剩强退(实测过这种"把自己锁死"的设计)。
	# ESC 退出同理:世界暂停着也得按得应。
	if (god_camera or esc_quit) and not Engine.is_editor_hint():
		process_mode = Node.PROCESS_MODE_ALWAYS
	if god_camera and not Engine.is_editor_hint():
		_gk_god_setup.call_deferred()
	# 调试面板(第二十二轮):和上帝机一样,编辑器里不造 ——
	# 造了在编辑器场景里会凭空多一块文字面板。面板节点不依赖整棵树建好
	# (它只是 CanvasLayer + Label),所以不用 call_deferred。
	if debug_panel and not Engine.is_editor_hint():
		_gk_dbg_setup()
	# 寻路:烤导航网格(第三十四轮)。**同步**烤 —— 『移动_寻路』第一帧
	# 就要用,异步烤会多出几帧"敌人还在直线走"的窗口(降级本来就有,
	# 但越短越好)。编辑器里不烤(和上面几个开关一致)。
	if nav_bake and not Engine.is_editor_hint():
		_gk_bake_navigation()
	# 开局 BGM(第七十七轮):和上面几个一样 —— @tool 脚本在编辑器里
	# 也会走 _ready,编辑器里放音乐会很烦。
	if not bgm_path.is_empty() and not Engine.is_editor_hint():
		_gk_start_bgm()
	# 音乐总线音量(第八十六轮):勾了『开局应用』才动总线 ——
	# 不勾时这行什么都不做,和以前完全一样。
	if music_bus_apply and not Engine.is_editor_hint():
		_gk_apply_music_bus()


## 音乐总线音量(第八十六轮):把一条总线设到指定 dB。
##
## ── 为什么找不到总线只警告不报错 ────────────────────────────────
## 总线是**工程级**的东西(Godot 的『音频』面板里建的),插件没法替你建。
## 用户还没建时最常见的写法是先填上名字 —— 那时给一条能看懂的警告,
## 而不是让场景加载失败。
func _gk_apply_music_bus() -> void:
	var index := AudioServer.get_bus_index(music_bus)
	if index < 0:
		push_warning("[GK] 找不到音频总线: %s(在 Godot 的『音频』面板里建一条)"
				% music_bus)
		return
	AudioServer.set_bus_volume_db(index, music_bus_db)


## 开局 BGM:造一个常驻播放器,循环播(第八十六轮起循环可在『游戏音乐』
## 图的卡上关掉)。
##
## ── 为什么要自己开循环 ──────────────────────────────────────────
## Godot 的 AudioStreamPlayer 播完就停,而 BGM 是要一直有的。
## 三种格式的循环开关**不在同一个地方**(实测):
##   .ogg / .mp3 -> `loop` 属性
##   .wav        -> `loop_mode` + `loop_end`(单位是**采样帧**,不是秒;
##                  不填 loop_end 的话循环区间是空的,等于没循环)
func _gk_start_bgm() -> void:
	var stream := load(bgm_path) as AudioStream
	if stream == null:
		push_warning("[GK] 找不到开局 BGM 文件: %s" % bgm_path)
		return
	if stream is AudioStreamOggVorbis:
		(stream as AudioStreamOggVorbis).loop = bgm_loop
	elif stream is AudioStreamMP3:
		(stream as AudioStreamMP3).loop = bgm_loop
	elif stream is AudioStreamWAV:
		var wav := stream as AudioStreamWAV
		if bgm_loop:
			wav.loop_mode = AudioStreamWAV.LOOP_FORWARD
			wav.loop_begin = 0
			wav.loop_end = _gk_wav_frames(wav)
		else:
			wav.loop_mode = AudioStreamWAV.LOOP_DISABLED
	var player := AudioStreamPlayer.new()
	player.name = "GKBgm"
	player.stream = stream
	player.volume_db = bgm_volume_db
	# 走哪条总线(第八十六轮):留空 = Master(老行为)
	if not bgm_bus.is_empty():
		player.bus = bgm_bus
	# 暂停(ESC 菜单 / 上帝机时停)时 BGM 不该跟着停 —— 它是"这一关的
	# 背景",不是某个角色的动作。
	player.process_mode = Node.PROCESS_MODE_ALWAYS
	add_child(player)
	player.play()


## .wav 一共多少**采样帧**(AudioStreamWAV.loop_end 要的就是帧数)。
##
## 帧数 = 时长(秒) × 采样率。
##
## ── 为什么**不能**拿 `wav.data.size()` 去推(真机踩过)──────────────
## Godot 导入 wav 时默认会**压缩**(设置里的 `compress/mode`),实测一个
## 8kHz / 单声道 / 16bit 的 0.2 秒 wav 导进来是 `format = 3`(**QOA**)、
## `data.size() = 672`。按"16bit 单声道 = 每帧 2 字节"去推就得到 336 帧,
## 而真实长度是 1600 帧 —— 算出来的 loop_end 只有 21%,听起来就是
## "每次都在半路回头"。时长和采样率是**导入器给的**,不受压缩方式影响。
func _gk_wav_frames(wav: AudioStreamWAV) -> int:
	return int(round(wav.get_length() * float(wav.mix_rate)))


## 把 Blender 里调好的材质挂到对应的网格上。
##
## 为什么要在运行时装,而不是写进 .tscn:
## glb 实例的**内部子节点不能被外部节点覆盖** —— 在 .tscn 里写
##     [node name="Ball" parent="World"]
##     material_override = ExtResource("mat_2")
## Godot 会报 "Parent path './World' for node 'Ball' has vanished",
## 覆盖被丢弃,看到的永远是白模。加 editable_children 也没用。
## 运行时装是有效的(实测)。
func _apply_materials() -> void:
	var root := _scene_root()
	if root != null and not materials.strip_edges().is_empty():
		var applied := _apply_material_table(root)
		if applied > 0:
			print("[GK] 已挂 ", applied, " 个卡通材质")
	# ── 收引用 + 平涂都要做,哪怕映射表是空的 ────────────────────────
	# 表空只代表"这次没有东西要挂",不代表场景里没有卡通材质
	# (比如手改过场景)。所以这两件事挪出上面的 early-return。
	_collect_materials()
	if not follow_scene_light:
		_apply_flat()


## 运行时新加进场景的网格:补挂材质(用户 2026-10-05)。
##
## 『资产_创建实例』生成的实例进树时,_apply_materials 早就跑完了 ——
## 不补一次的话它一直是 glb 自带的素白 PBR 材质。用 node_added 信号
## (唯一的汇聚点:不管是谁把它加进来的都能覆盖到),而且**推迟到帧末** ——
## 信号发出时节点还没进树,立刻 find_children 搜不到它下面那些网格。
func _gk_on_node_added(node: Node) -> void:
	if Engine.is_editor_hint():
		return
	if not (node is Node3D or node is Node2D):
		return
	# 只处理"可能带网格"的:整棵场景树挂进来时(换关卡)也走这里,会刷一遍
	_gk_remat.call_deferred()


func _gk_remat() -> void:
	var root := _scene_root()
	if root == null or materials.strip_edges().is_empty():
		return
	_apply_material_table(root, true)
	_collect_materials()
	if not follow_scene_light:
		_apply_flat()


## 按映射表逐条挂材质。返回挂上的条数。
##
## quiet = 不刷"没找到网格节点"的告警 —— 运行时新加节点时会**重跑**
## 整张表(见 _gk_on_node_added),这时表里那些"这次场景里没有"的条目
## 会重复刷警告,把控制台刷花。
func _apply_material_table(root: Node, quiet: bool = false) -> int:
	# ── 分隔符是竖线,不是换行 ──────────────────────────────────────
	# 见上面 materials 变量处的说明:默认值里放真换行会让整个脚本
	# 解析失败(parse error)。竖线在节点名和 res:// 路径里都不会出现。
	var applied := 0
	for line in materials.split("|", false):
		var text := line.strip_edges()
		if text.is_empty() or not text.contains("="):
			continue
		var parts := text.split("=", true, 1)
		var node_name := parts[0].strip_edges()
		var res_path := parts[1].strip_edges()

		# ── 键可以带 "#序号" 表示"只换第 N 个表面" ──────────────────
		# 一个 MMD 模型常常是**一个网格带几十个材质槽**(实测 33 个表面)。
		# material_override 是整个网格级别的 —— 一条覆盖会把所有表面
		# 改成同一个材质,模型看起来就"只有一种颜色 / 变白"。
		# 带序号的条目改用 surface_override 逐个替换。
		var surface := -1
		if node_name.contains("#"):
			var halves := node_name.split("#", true, 1)
			node_name = halves[0].strip_edges()
			surface = int(halves[1])

		if not ResourceLoader.exists(res_path):
			push_warning("[GK] 材质不存在: " + res_path)
			continue
		var material: Material = load(res_path)
		if material == null:
			continue
		# ── 找网格节点必须**先按类型找**(踩过的坑)──────────────────
		# `find_child("Cube")` 返回的是**第一个**叫 Cube 的节点 ——
		# 而场景里有两个同名节点:
		#     Colliders/Cube [CharacterBody3D]   <- 先被找到,但它不是网格
		#     Colliders/Cube/Model/Cube [MeshInstance3D]  <- 真正要挂材质的
		# 于是 `if node is MeshInstance3D` 判false,材质挂不上,
		# 用户看到的是"角色的材质一直没生效"。
		# 所以先在所有 MeshInstance3D 里按名字找,找不到再退回 find_child。
		#
		# ── 同名节点可能有**多个**(静态资产 / 复制出来的物体)────────
		# 以前只挂第一个匹配 —— 一个资产在场景里摆几份时,只有第一份换成
		# 卡通材质,其余的还留着 glb 自带的 PBR 材质,几份长得不一样。
		# 现在**全部匹配的都挂上**。
		var targets: Array = []
		for candidate in root.find_children(node_name, "MeshInstance3D", true, false):
			targets.append(candidate)
		if targets.is_empty():
			var fallback := root.find_child(node_name, true, false)
			if fallback is MeshInstance3D:
				targets.append(fallback)
		if targets.is_empty():
			if not quiet:
				push_warning("[GK] 没找到网格节点: " + node_name)
		for target in targets:
			var mesh_node := target as MeshInstance3D
			if surface >= 0:
				if surface >= mesh_node.get_surface_override_material_count():
					push_warning("[GK] 表面序号超出范围: " + text)
					continue
				mesh_node.set_surface_override_material(surface, material)
			else:
				mesh_node.material_override = material
			applied += 1
	return applied


## 全平涂:卡通材质不画任何明暗,直接显示贴图本色。
##
## ── 为什么必须**显式**设(踩过的坑,用户 2026-10-04)────────────────
## ①A 最初只做了"follow_scene_light 关了就不更新",以为不喂光就是平涂 ——
## 其实 shader 里 `light_dir` 有**内置默认值**(左上前方),不喂它照样按
## 那个方向画色阶:模型身上留着一片**和场景无关的假阴影**(用户报的
## "阴影不正常":脸上一片紫 = shadow_tint 的色阶带)。真正的平涂:
## light_dir 置零(shader 走"没有光"那条路)+ 环境色拉满 + 阴影色改白,
## 结果就是 ALBEDO = 贴图本色。
func _apply_flat() -> void:
	for pair in _gk_mats:
		var sm := pair[1] as ShaderMaterial
		if sm == null:
			continue
		sm.set_shader_parameter("light_dir", Vector3.ZERO)
		sm.set_shader_parameter("ambient", 1.0)
		sm.set_shader_parameter("shadow_tint", Color(1, 1, 1, 1))


func _collect_materials() -> void:
	_gk_mats.clear()
	var root := _scene_root()
	if root == null:
		return
	for node in root.find_children("*", "MeshInstance3D", true, false):
		var mesh_node := node as MeshInstance3D
		if mesh_node == null:
			continue
		# ⚠ 同一个材质资源可能被多个网格共用(复制出来的道具)。这里**按
		# 宿主逐条**收,不去重 —— 多光近似要按"这个网格在哪"算方向,
		# 共用一份材质时两边的取值会互相覆盖(每帧重设,最后一个赢)。
		# 想各自正确就得各用各的材质,这一点写在 README 里了。
		var found: Array = []
		if mesh_node.material_override is ShaderMaterial:
			found.append(mesh_node.material_override)
		for i in range(mesh_node.get_surface_override_material_count()):
			var sm := mesh_node.get_surface_override_material(i) as ShaderMaterial
			if sm != null and not found.has(sm):
				found.append(sm)
		for mat in found:
			_gk_mats.append([mesh_node, mat])


## ── 手电筒:把标了『跟随相机』的灯每帧贴到当前相机上(第四十八轮)──────
## 为什么在**物理帧**里搬:角色移动在 _physics_process,手电筒晚一帧会
## 看到"人动了、光柱慢半拍"。编辑器里不搬(免得在编辑器里乱动场景)。
func _gk_follow_lights_tick() -> void:
	if follow_lights.strip_edges().is_empty() or Engine.is_editor_hint():
		return
	var cam := get_viewport().get_camera_3d()
	if cam == null:
		return
	var root := _scene_root()
	if root == null:
		return
	for entry in follow_lights.split("|", false):
		var wanted := entry.strip_edges()
		if wanted.is_empty():
			continue
		var found := root.find_child(wanted, true, false)
		if found is Light3D:
			(found as Light3D).global_transform = cam.global_transform


## 场景灯光 -> 卡通材质(第四十八轮:从"只跟一盏平行光"升级成多光近似)。
## 参数名带下划线前缀 = "故意不用":取值走 get_physics_process_delta_time(),
## 而 Godot 会把没用上的参数报成 UNUSED_PARAMETER(调试器里一条错误)。
##
## 主光怎么选:每帧对**每个材质**算一遍"哪盏亮着的灯对它影响最大" ——
##   * 平行光:按 light_energy(打了 0.8 折,见 _gk_pick_light 的说明);
##   * 点光 / 聚光:按距离衰减(聚光还要"照在锥里"),方向 = 灯照向材质。
## 挑中的那盏按"主光"喂给 shader(色阶 / 阴影方向 / 亮度都跟着它)。
##
## ⚠ 这是**近似**,不是真实多光源:shader 的色阶是硬边的,多盏叠加会糊成
## 一片灰,所以"多盏同时照"只取影响最大的那盏。要逐像素一致得改 shader,
## 不值当 —— 这里保证的是"手电筒照到谁,谁就跟着手电筒变化"这个体感。
func _update_light(_delta: float) -> void:
	if not follow_scene_light or _gk_mats.is_empty():
		return
	var lights := _gk_lights_on()
	if lights.is_empty():
		# 一盏亮的灯都没有 -> 告诉 shader "没光",它会退化成只画环境色
		# (见 gk_toon.gdshader 里的零向量特判)。不这么做的话,灯关了
		# 角色还是亮的(实测踩过)。
		for pair in _gk_mats:
			var sm_off := pair[1] as ShaderMaterial
			if sm_off != null:
				sm_off.set_shader_parameter("light_dir", Vector3.ZERO)
		return
	for pair in _gk_mats:
		var sm := pair[1] as ShaderMaterial
		if sm == null:
			continue
		var pick := _gk_pick_light(lights, pair[0] as Node3D)
		if pick.is_empty():
			continue
		sm.set_shader_parameter("light_dir", pick[0])
		sm.set_shader_parameter("light_color", pick[1])
		# 亮度夹到 shader 的 0~4,免得点光一近就过曝成一片白。
		sm.set_shader_parameter("light_energy", pick[2])


## 场景里**亮着**的灯(第四十八轮:三种类型都能当主光)。
## 指定了 light_name 就只认那一盏(老行为);否则全部类型都算候选。
func _gk_lights_on() -> Array:
	var root := _scene_root()
	if root == null:
		return []
	var out: Array = []
	if not light_name.is_empty():
		var named := root.find_child(light_name, true, false)
		if named is Light3D and _light_is_on(named):
			out.append(named)
		return out
	for node in root.find_children("*", "Light3D", true, false):
		if _light_is_on(node):
			out.append(node)
	return out


## 这个材质该用哪盏灯:算"影响"挑最大的那盏(近似,见 _update_light)。
## 返回 [照射方向, 颜色, 亮度];空数组 = 没有可用的灯。
func _gk_pick_light(lights: Array, node: Node3D) -> Array:
	var best: Array = []
	var best_score := -1.0
	var here := Vector3.ZERO
	if node != null:
		here = node.global_position
	for light in lights:
		var l := light as Light3D
		if l == null:
			continue
		var score := 0.0
		var reach := 1.0
		var dir := Vector3.ZERO
		if l is DirectionalLight3D:
			# 平行光照的是全场景 —— 给 0.8 的折扣,让"照到脸上的手电筒"能
			# 盖过月光(不打折的话,恐怖游戏里手电筒永远赢不了太阳)。
			score = l.light_energy * 0.8
			dir = -l.global_transform.basis.z.normalized()
		else:
			var span := 5.0
			if l is OmniLight3D:
				span = (l as OmniLight3D).omni_range
			elif l is SpotLight3D:
				span = (l as SpotLight3D).spot_range
			if span <= 0.0:
				span = 5.0
			var away := here - l.global_position
			var dist := away.length()
			reach = clampf(1.0 - dist / span, 0.0, 1.0)
			score = l.light_energy * reach
			if dist > 0.001:
				dir = away.normalized()
				if l is SpotLight3D:
					# 聚光只在锥内有影响:拿"灯的照射方向"和"灯到材质的方向"
					# 点乘当权重 —— 照不到的方向不该被它染色。
					var aim := -l.global_transform.basis.z.normalized()
					score *= clampf(aim.dot(dir), 0.0, 1.0)
			else:
				dir = -l.global_transform.basis.z.normalized()
		if score > best_score:
			best_score = score
			best = [dir, l.light_color,
					clampf(l.light_energy * reach, 0.0, 4.0)]
	return best


## 这盏灯**现在是否在发光**。
##
## ── 为什么必须判断(踩过的坑)────────────────────────────────────
## 原来只按类型找灯 —— 用户把灯隐藏/关掉之后,脚本**照样**把它的方向
## 和颜色往 shader 里灌,于是"灯全关了角色还是亮的"。
## 用户原话:"godot 里关闭与隐藏灯光,但显示与渲染还是残留一些光照"。
##
## 我们自己算光照,就得自己尊重灯的开关 —— Godot 不会替我们判断。
func _light_is_on(node: Node) -> bool:
	var light := node as Light3D
	if light == null:
		return false
	if not light.visible:
		return false
	if light.light_energy <= 0.0:
		return false
	return true


## 用物理帧而不是渲染帧:角色移动在 _physics_process 里,
## 光照跟着一起更新才不会出现"身体动了阴影还停在上一帧"的晃动。
func _physics_process(delta: float) -> void:
	_update_light(delta)
	# 手电筒(第四十八轮):贴到当前相机上 —— 排在 _update_light 之后,
	# 这一帧喂给 shader 的方向就是手电筒搬完后的位置(不差一帧)。
	_gk_follow_lights_tick()
	_gk_sync_colliders()
	# 内置 ESC 退出(第三十九轮):和上帝机无关,每一帧都要看
	if esc_quit and not Engine.is_editor_hint():
		_gk_esc_tick()
	# 编辑器里 Tab 是切视角的快捷键,不能让它去切上帝机
	if god_camera and not Engine.is_editor_hint():
		_gk_god_tick(delta)
		# 时停(第三十五轮):**冻住之后**这一句还得跑 —— 要不谁来解冻。
		# 它排在 _gk_god_tick 后面:Tab 切走的那一刻先解冻(见 _gk_god_toggle),
		# 这一帧的 T 状态不会再把它冻回去。
		_gk_time_stop_tick()
	# 调试面板(第二十二轮):按键 / 刷新都在这里(和上帝机同一个节奏)
	if debug_panel and not Engine.is_editor_hint():
		_gk_dbg_tick(delta)


## ── 碰撞代理 <-> 网格:谁动了谁说了算(用户 2026-10-05)─────────────
## 为什么需要它:
## 场景里的道具在 Godot 里是**两个**节点 —— 看得见的网格在 glb 实例里
## (`World/门`),碰撞代理在 `Colliders/门`。
## 两边都会动,而且动的那一边不一样:
##   * **网格**动 = Blender 里做的场景动画(门升起、平台移动)—— 碰撞
##     必须跟过去,否则"门看起来在动,却挡不住人"(用户实测反馈);
##   * **碰撞代理**动 = 游戏逻辑在推它(『移动_设置位置』的巡逻、
##     『移动_朝向目标』的 velocity + move_and_slide 追击)—— 网格得
##     跟过来,否则"红方块站在原地、判定却跑过来了"。
##
## 所以每物理帧比上一帧的变换:**谁变了就让另一边跟过去**。两边都没动
## 就什么都不做(地面、墙这些从来不动的完全零开销)。
var _gk_body_links: Array = []      # [网格, 代理, 上帧网格变换, 上帧代理变换]


func _gk_link_colliders() -> void:
	if get_tree() == null:
		return
	var tree_root := get_tree().root
	# ── 为什么要"全树找 Colliders"(踩过的坑)──────────────────────────
	# 第一版用 `_scene_root().find_child("Colliders")` —— 一旦这张场景是
	# **被嵌在别处**的(探针把它挂在自己的根节点下、将来"关卡预制体"也是
	# 这个形状),`_scene_root()` 会跑到外层去,Colliders 就找不到了,
	# 配对为空 = 碰撞代理不跟随。现在直接在全树里找所有叫 Colliders 的容器,
	# 每个容器各自配对 —— 嵌套场景、多个关卡都成立。
	var holders := tree_root.find_children("Colliders", "Node", true, false)
	if holders.is_empty():
		return
	for holder in holders:
		for body in holder.get_children():
			if not (body is CollisionObject3D):
				continue
			# 找**同名**的网格节点:不能在容器子树里(那里只有代理),
			# 也不能是代理自己的祖先 / 后代(角色的网格挂在代理下面,自己会动)。
			var mesh: Node3D = null
			for found in tree_root.find_children(String(body.name), "Node3D", true, false):
				if found == holder or holder.is_ancestor_of(found):
					continue
				if found == body or body.is_ancestor_of(found) \
						or found.is_ancestor_of(body):
					continue
				mesh = found
				break
			if mesh != null:
				# 后两个是"上一帧的变换":第一帧拿来和当前比,判断谁动过
				_gk_body_links.append([mesh, body as Node3D,
					mesh.global_transform, body.global_transform])


func _gk_sync_colliders() -> void:
	for pair in _gk_body_links:
		# ── 先查有效性,再赋给**带类型**的局部变量(用户 2026-10-05 第二十四轮)──
		# 踩过:写成
		#     var mesh: Node3D = pair[0]
		#     if not is_instance_valid(mesh): continue
		# 时,**第一句就先报错**:
		#     Trying to assign invalid previously freed instance.
		# (GDScript 往带类型的变量里赋一个"已释放的对象"直接抛错,
		#  根本轮不到后面那句检查。)场景里只要有**被销毁过的道具**
		# (小游戏示例里吃掉的 5 枚金币),它的配对就一直是"已释放",
		# 于是每物理帧刷一屏红。
		# 现在先对数组元素做 is_instance_valid(不涉及带类型赋值),再落地。
		if not is_instance_valid(pair[0]) or not is_instance_valid(pair[1]):
			continue
		var mesh: Node3D = pair[0]
		var body: Node3D = pair[1]
		var mesh_now := mesh.global_transform
		var body_now := body.global_transform
		if mesh_now != pair[2]:
			# 网格动了(Blender 场景动画 / 时间轴挪网格)-> 碰撞代理跟上
			body.global_transform = mesh_now
		elif body_now != pair[3]:
			# 代理自己动了(游戏逻辑 / 物理在推它)-> 把网格拉过来
			mesh.global_transform = body_now
		# 记下**写完之后的**变换,下一帧才比得准
		pair[2] = mesh.global_transform
		pair[3] = body.global_transform


## 造上帝机(幂等)。位置从玩家相机往后上方退一段,看着玩家相机的正前方。
func _gk_god_setup() -> void:
	if _gk_god_cam != null and is_instance_valid(_gk_god_cam):
		return
	var root := _scene_root()
	if root == null or root.get_tree() == null:
		return
	# ── 先把**已经有的**那台找回来(第三十一轮,用户实测)────────────────
	# 上帝机挂在 `/root` 上,场景重载(R 键重开 / 切关卡回来)**清不掉它** ——
	# 不找回来就会每按一次 R 多造一台;而且旧那台可能还占着"当前相机"。
	var cam: Camera3D = null
	for found in root.get_tree().root.get_children():
		if found is Camera3D and (found as Camera3D).is_in_group("gk_god_camera"):
			cam = found as Camera3D
			break
	if cam == null:
		cam = Camera3D.new()
		cam.name = "上帝摄像机"
		# 分组:角色的游戏逻辑靠它判断"现在是不是上帝视角",
		# 是的话就把移动 / 跳跃让出来(用户:上帝视角不该影响角色移动)。
		# **不能**用名字判断 —— 用户可能改节点名。
		cam.add_to_group("gk_god_camera")
		root.get_tree().root.add_child(cam)
	_gk_god_cam = cam
	var from := _find_camera()
	if from != null and from != cam:
		cam.global_transform = from.global_transform
		cam.global_position += from.global_transform.basis.z * 6.0 + Vector3(0.0, 3.0, 0.0)
		var ahead := from.global_position - from.global_transform.basis.z * 10.0
		if cam.global_position.distance_to(ahead) > 0.01:
			cam.look_at(ahead, Vector3.UP)
	else:
		cam.global_position = Vector3(0.0, 8.0, 12.0)
		cam.look_at(Vector3.ZERO, Vector3.UP)
	# ── 重开/换场景必须回到**普通视角**(第三十一轮,用户实测)──────────
	# 重载那一瞬间"当前相机"是空的,上帝机顺手就接管了画面 —— 角色的移动
	# 守卫看到"当前相机是上帝机"就以为还在上帝视角,**按 W 走不动**
	# (实测:重开后 velocity 恒为 0)。上帝视角是临时的,所以这里明确把
	# 镜头交还给本场景自己的相机;场景里**一台别的相机都没有**时,才让
	# 上帝机顶着(否则按 F5 一片黑)。
	_gk_god_restore_camera()
	if get_viewport().get_camera_3d() == null:
		cam.make_current()


## 把镜头交还给本场景自己的相机 —— **只在当前正用着上帝机时才交还**。
## ── 为什么必须这么保守(第三十一轮实测)────────────────────────────
## 第一版不看状态、上来就把镜头交给"第一台别的相机" —— 结果把开场运镜
## 刚切好的特写机位抢回了侧视机位(曲线示例的探针当场抓住:
## `DEMO ... cam=侧视机位`,运镜白播)。所以:不是上帝机在用,就一律不碰。
func _gk_god_restore_camera() -> void:
	if _gk_god_cam == null or not is_instance_valid(_gk_god_cam):
		return
	if not (_gk_god_cam as Camera3D).is_current():
		return
	var root := _scene_root()
	if root == null:
		return
	for found in root.find_children("*", "Camera3D", true, false):
		if found != _gk_god_cam:
			(found as Camera3D).make_current()
			return


## Tab:在『玩家相机』和『上帝摄像机』之间来回切。
func _gk_god_toggle() -> void:
	if _gk_god_cam == null or not is_instance_valid(_gk_god_cam):
		return
	if _gk_god_cam.is_current():
		# 正在用上帝机 -> 切回玩家相机(优先记下来的那台)
		# 附身也跟着结束(第三十九轮):离开上帝机 = 放开角色 ——
		# 不然角色的 velocity 上还留着"上帝机写的一半速度"。
		if _gk_god_possess != null:
			_gk_god_release()
		# 时停也跟着结束(第三十五轮):离开上帝机就该回到能玩的状态 ——
		# "在上帝机里冻住看细节"这个用法到切走为止,不然玩家会看着一个
		# 冻住的画面按什么都没有反应。
		if get_tree() != null and get_tree().paused:
			get_tree().paused = false
			print("[GK] 时停:关(离开上帝机)")
		if _gk_god_player_cam != null and is_instance_valid(_gk_god_player_cam):
			_gk_god_player_cam.make_current()
		else:
			var root := _scene_root()
			if root != null:
				for found in root.find_children("*", "Camera3D", true, false):
					if found != _gk_god_cam:
						(found as Camera3D).make_current()
						break
		if _gk_mouse_was >= 0:
			Input.mouse_mode = _gk_mouse_was
			_gk_mouse_was = -1
	else:
		# 正在用玩家相机 -> 记住它,再切到上帝机
		_gk_god_player_cam = get_viewport().get_camera_3d()
		# 锁鼠标:不然光标一碰到窗口边,上帝机的视角就转不动了
		if _gk_mouse_was < 0:
			_gk_mouse_was = Input.mouse_mode
		Input.mouse_mode = Input.MOUSE_MODE_CAPTURED
		_gk_god_cam.make_current()


## 上帝机的每个物理帧:Tab 切换 + 飞行。
func _gk_god_tick(delta: float) -> void:
	if _gk_god_cam == null or not is_instance_valid(_gk_god_cam):
		return
	if Input.is_key_pressed(KEY_TAB):
		if not _gk_god_tab_held:
			_gk_god_tab_held = true
			_gk_god_toggle()
	else:
		_gk_god_tab_held = false
	if not _gk_god_cam.is_current():
		# 没在用上帝机:位移归零,不然切过去的第一帧视角会先"跳"一下
		_gk_god_look = Vector2.ZERO
		return
	# ── 附身(第三十九轮):F 接管 / 放开 ────────────────────────────
	if Input.is_key_pressed(_GK_POSSESS_KEY):
		if not _gk_god_possess_held:
			_gk_god_possess_held = true
			if _gk_god_possess == null:
				_gk_god_possess_begin()
			else:
				_gk_god_release()
	else:
		_gk_god_possess_held = false
	# 滚轮缩放(第三十九轮)
	_gk_god_zoom_tick()
	# 鼠标转视角(用**自己**累积的那一份,见 _input)
	if _gk_god_look != Vector2.ZERO:
		var rot := _gk_god_cam.rotation_degrees
		rot.y -= _gk_god_look.x * 0.25
		rot.x = clampf(rot.x - _gk_god_look.y * 0.25, -89.0, 89.0)
		_gk_god_cam.rotation_degrees = rot
		_gk_god_look = Vector2.ZERO
	# ── 附身中:WASD 驱动的是**角色**,相机只负责跟在身后 ────────────
	if _gk_god_possess != null and is_instance_valid(_gk_god_possess):
		_gk_god_possess_tick(delta)
		return
	if _gk_god_possess != null:
		_gk_god_possess = null      # 角色被删了 / 切场景了,自动放手
	# WASD 平移 + 空格/Shift 升降(**不走碰撞**,想穿墙就穿墙)
	var fly := Vector3.ZERO
	# ⚠ 名字不能叫 `basis`:Node3D 自己就有 `basis` 属性,Godot 报
	#   "The local variable "basis" is shadowing an already-declared property"
	#   (第七十轮用户截图里那一串告警之一)。
	var _gk_basis := _gk_god_cam.global_transform.basis
	if Input.is_key_pressed(KEY_W):
		fly -= _gk_basis.z
	if Input.is_key_pressed(KEY_S):
		fly += _gk_basis.z
	if Input.is_key_pressed(KEY_A):
		fly -= _gk_basis.x
	if Input.is_key_pressed(KEY_D):
		fly += _gk_basis.x
	if Input.is_key_pressed(KEY_SPACE):
		fly += Vector3.UP
	if Input.is_key_pressed(KEY_SHIFT):
		fly -= Vector3.UP
	if fly != Vector3.ZERO:
		_gk_god_cam.global_position += fly.normalized() * 8.0 * delta


## ── 时停(第三十五轮,用户 2026-10-06:"在上帝摄像机时可以添加时停")──
## 按 `_GK_TIME_STOP_KEY`(默认 T)把整个世界冻住 —— 物理、场景动画、
## 角色逻辑全停(`get_tree().paused`),只有上帝机还能飞:停住画面绕着看
## 细节,再按一下接着玩。
##
## ── 两条守卫都是"别把自己锁死"────────────────────────────────────
## 1. **开**只认上帝视角:正在用玩家相机时按 T 不该把游戏冻住
##    (那时按键是给角色用的,冻住会让人以为"操作失灵了")。
## 2. **关**不挑视角:切回玩家相机之后发现还冻着,照样按得开 ——
##    否则就只剩"必须切回上帝机才能解冻"这一条死路。
## 另外切回玩家相机时也自动解冻(见 _gk_god_toggle),一个是兜底一个是明路。
##
## ── 为什么用 Input.is_key_pressed 而不是 is_action_just_pressed ──────
## 游戏逻辑那套输入是节点自己的(暂停后不跑);T 是**环境脚本**的键,
## 和 Tab 同一个套路:自己记"上一帧按没按",暂停不暂停都能读。
func _gk_time_stop_tick() -> void:
	var tree := get_tree()
	if tree == null:
		return
	if not Input.is_key_pressed(_GK_TIME_STOP_KEY):
		_gk_time_stop_held = false
		return
	if _gk_time_stop_held:
		return
	_gk_time_stop_held = true
	if tree.paused:
		tree.paused = false
		print("[GK] 时停:关")
		return
	var god_on := (_gk_god_cam != null and is_instance_valid(_gk_god_cam)
		and _gk_god_cam.is_current())
	if god_on:
		tree.paused = true
		print("[GK] 时停:开(再按一下继续)")


## ── 滚轮缩放(第三十九轮:用户要的"上帝机默认带视角缩放")──────────
## 转滚轮改上帝机的**视野**(FOV):往"缩小"方向转 = 拉近看细节,
## 反方向 = 拉远看全局。范围夹在 20~110 度(再大就鱼眼,再小看不清)。
##
## ── 为什么是 FOV 而不是"往前后飞"────────────────────────────────
## 前后飞用 WASD 就能做;滚轮改 FOV 才是"镜头拉近拉远"(运镜里最常用的
## 那种),而且**不会**把相机怼进墙里 —— 上帝机本来就不走碰撞,飞太近
## 会穿模,改 FOV 不会。
func _gk_god_zoom_tick() -> void:
	if _gk_god_wheel == 0.0:
		return
	# 第一次缩放以相机自己的 FOV 为基准(场景里可能就是 40/75 之类的)
	if _gk_god_fov <= 0.0:
		_gk_god_fov = _gk_god_cam.fov
	_gk_god_fov = clampf(_gk_god_fov - _gk_god_wheel * 4.0, 20.0, 110.0)
	_gk_god_cam.fov = _gk_god_fov
	_gk_god_wheel = 0.0


## ── 附身(第三十九轮:用户要的"接管角色控制")────────────────────
## 按 F 接管场景里的角色:WASD 直接驱动它走 / 空格跳,相机跟到身后;
## 再按 F 放开。角色自己的游戏逻辑此时是**让位**的(它的移动 / 跳跃都用
## `_gk_god_camera_active()` 判断"现在是不是上帝视角"把自己停了),
## 所以这里驱动它不会打架。
##
## ── 为什么按"角色"找,而不是按名字 / 按第一个 CharacterBody3D ────────
## 名字用户会改;而"挂了游戏逻辑脚本"这件事有个现成的判据:
## **`_gk_god_camera_active` 这个方法**(每个导出的角色脚本都有)。
## 所以优先找有它的节点 = 一定是玩家角色;找不到时(纯场景测试)
## 退回第一个 CharacterBody3D,好歹能拿它当"可驾驶的东西"推着玩。
func _gk_find_possess_target() -> Node3D:
	var root := _scene_root()
	if root == null:
		return null
	var fallback: Node3D = null
	for found in root.find_children("*", "Node3D", true, false):
		if found == _gk_god_cam:
			continue
		if found.has_method("_gk_god_camera_active"):
			return found as Node3D
		if fallback == null and found is CharacterBody3D:
			fallback = found as Node3D
	return fallback


## 开始附身:把相机挪到角色身后,再锁住鼠标。
func _gk_god_possess_begin() -> void:
	var target := _gk_find_possess_target()
	if target == null:
		push_warning("[GK] 场景里没有可附身的角色(没有 CharacterBody3D)")
		return
	_gk_god_possess = target
	# 相机跟到角色身后(用当前朝向):后面每帧由 _gk_god_possess_tick 维持
	_gk_god_cam.global_position = (target.global_position
		+ Vector3(0.0, 1.6, 0.0)
		+ _gk_god_cam.global_transform.basis.z * 5.0)
	Input.mouse_mode = Input.MOUSE_MODE_CAPTURED
	print("[GK] 附身:%s(再按一下 F 放开)" % target.name)


## 放开附身:角色停在原地,交给它自己的逻辑。
func _gk_god_release() -> void:
	var body := _gk_god_possess
	_gk_god_possess = null
	if body == null or not is_instance_valid(body):
		return
	if body is CharacterBody3D:
		# 松手就刹住:不刹的话角色会带着"上帝机写的速度"自己滑一段
		var cb := body as CharacterBody3D
		cb.velocity.x = 0.0
		cb.velocity.z = 0.0
	print("[GK] 放开附身")


## 附身中:WASD / 空格驱动角色,相机跟在身后。
##
## ── 为什么要 `move_and_slide()` 而不是直接改 global_position ──────
## 直接改位置会**穿过墙和箱子**(角色自己的碰撞白做了);move_and_slide
## 走的是角色的碰撞体,和它自己走路一模一样。所以这里只替它写 velocity。
func _gk_god_possess_tick(delta: float) -> void:
	var body := _gk_god_possess
	var _gk_basis := _gk_god_cam.global_transform.basis
	var move := Vector3.ZERO
	if Input.is_key_pressed(KEY_W):
		move -= _gk_basis.z
	if Input.is_key_pressed(KEY_S):
		move += _gk_basis.z
	if Input.is_key_pressed(KEY_A):
		move -= _gk_basis.x
	if Input.is_key_pressed(KEY_D):
		move += _gk_basis.x
	move.y = 0.0
	if move != Vector3.ZERO:
		move = move.normalized()
	if body is CharacterBody3D:
		var cb := body as CharacterBody3D
		cb.velocity.x = move.x * _GK_POSSESS_SPEED
		cb.velocity.z = move.z * _GK_POSSESS_SPEED
		if cb.is_on_floor():
			if Input.is_key_pressed(KEY_SPACE):
				cb.velocity.y = _GK_POSSESS_JUMP
		else:
			# 重力:附身时角色自己的重力逻辑被守卫停掉了,这里补上 ——
			# 用**角色自己的** get_gravity(),工程重力设置改了也跟着变
			# (和角色脚本里 `velocity += get_gravity() * delta` 同一个写法)
			cb.velocity.y += cb.get_gravity().y * delta
		cb.move_and_slide()
		# 面朝走的方向(只转水平朝向,和角色的移动逻辑一个做法)
		if move != Vector3.ZERO:
			var look := cb.global_position + move
			if cb.global_position.distance_to(look) > 0.01:
				cb.look_at(look, Vector3.UP)
	else:
		body.global_position += move * _GK_POSSESS_SPEED * delta
	# 相机跟在身后上方(位置跟,朝向仍由鼠标控制)
	var behind := _gk_god_cam.global_transform.basis.z
	_gk_god_cam.global_position = (body.global_position
		+ Vector3(0.0, 1.6, 0.0) + behind * 5.0)


## ── 内置 ESC 退出(第三十九轮)─────────────────────────────────────
## 场景里没把 Esc 用在暂停菜单时,按 Esc 直接退出游戏(见 esc_quit)。
## 和 Tab / T 同一个套路:自己记"上一帧按没按",暂停不暂停都读得到。
func _gk_esc_tick() -> void:
	if not Input.is_key_pressed(KEY_ESCAPE):
		_gk_esc_held = false
		return
	if _gk_esc_held:
		return
	_gk_esc_held = true
	print("[GK] ESC:退出游戏")
	var tree := get_tree()
	if tree != null:
		tree.quit()


## 上帝机的鼠标位移**单独**攒一份。
##
## ── 为什么不复用游戏逻辑那份 `_gk_mouse_delta`(踩过的坑)──────────
## 那份在物理帧末尾会清零,而同一帧里"清零"排在"上帝机读它"**前面**
## (游戏逻辑的语句先跑、钩子后跑)—— 于是上帝机永远读到零向量,
## 鼠标怎么拖视角都不动(用户实测:"鼠标无法控制视角旋转")。
## 自己攒一份,读写都在同一处,谁先谁后都不影响。
func _input(event: InputEvent) -> void:
	if not god_camera or Engine.is_editor_hint():
		return
	if _gk_god_cam == null or not is_instance_valid(_gk_god_cam):
		return
	if not _gk_god_cam.is_current():
		return
	if event is InputEventMouseMotion:
		_gk_god_look += event.relative
	elif event is InputEventMouseButton:
		# 滚轮缩放(第三十九轮):和鼠标位移一样,**自己攒一份**,
		# 物理帧里统一消费 —— 一帧里滚好几格也不会只算最后一格。
		var wheel := event as InputEventMouseButton
		if wheel.pressed:
			if wheel.button_index == MOUSE_BUTTON_WHEEL_UP:
				_gk_god_wheel -= 1.0
			elif wheel.button_index == MOUSE_BUTTON_WHEEL_DOWN:
				_gk_god_wheel += 1.0


## ── 调试面板(第二十二轮)─────────────────────────────────────────
## 面板是**运行时造**出来的(CanvasLayer + Label):不进 .tscn 就不挑 Godot 版本,
## 也不用在场景里多两个节点。默认收起,按键才出来。
func _gk_dbg_setup() -> void:
	var layer := CanvasLayer.new()
	layer.layer = 95          # 盖在游戏上,低于闪白 / 转场(100)
	var label := Label.new()
	label.name = "调试面板"
	label.position = Vector2(18.0, 14.0)
	label.add_theme_font_size_override("font_size", 15)
	label.add_theme_color_override("font_color", Color(1, 1, 1, 0.96))
	# 纯游戏画面背景深浅不定,加个黑描边(阴影)保证"白字也能看清"
	label.add_theme_color_override("font_shadow_color", Color(0, 0, 0, 0.9))
	label.add_theme_constant_override("shadow_offset_x", 1)
	label.add_theme_constant_override("shadow_offset_y", 1)
	label.visible = false
	layer.add_child(label)
	add_child(layer)
	_gk_dbg_label = label


## 每物理帧:看两个键、按 0.25 秒刷一次面板。
## 刷新不是每帧:每帧拼一遍字符串没必要(人眼也看不清),读起来还会闪。
func _gk_dbg_tick(delta: float) -> void:
	# 自己记"按住没":一次按键会被好几帧读到,不记就会连开连关
	var down := Input.is_key_pressed(_GK_DBG_KEY)
	if down and not _gk_dbg_key_held:
		_gk_dbg_key_held = true
		_gk_dbg_visible = not _gk_dbg_visible
		if _gk_dbg_label != null:
			_gk_dbg_label.visible = _gk_dbg_visible
			if _gk_dbg_visible:
				_gk_dbg_label.text = _gk_dbg_text()
	elif not down:
		_gk_dbg_key_held = false
	var copy := Input.is_key_pressed(_GK_DBG_COPY_KEY)
	if copy and not _gk_dbg_copy_held:
		_gk_dbg_copy_held = true
		_gk_dbg_copy()
	elif not copy:
		_gk_dbg_copy_held = false
	if not _gk_dbg_visible or _gk_dbg_label == null:
		return
	_gk_dbg_accum += delta
	if _gk_dbg_accum < 0.25:
		return
	_gk_dbg_accum = 0.0
	_gk_dbg_label.text = _gk_dbg_text()


## 找"宿主"—— 谁身上挂着带成员变量的脚本,谁就是。
## **只找一次**(脚本不会中途换);我们自己生成的脚本一定有,
## 因为开着面板时脚本里会声明 `_gk_debug_logs`。
func _gk_dbg_find_hosts() -> void:
	if _gk_dbg_hosts_ready:
		return
	_gk_dbg_hosts_ready = true
	var root := _scene_root()
	if root == null:
		return
	var nodes: Array = [root]
	nodes.append_array(root.find_children("*", "", true, false))
	for node in nodes:
		# 面板自己(环境脚本)不算宿主 —— 它的成员变量是材质表 / 相机名这些,
		# 列上去只会把面板刷花。判据用"有没有面板方法",不靠节点名。
		if node.has_method("_gk_dbg_text"):
			continue
		for info in node.get_property_list():
			if int(info.usage) & PROPERTY_USAGE_SCRIPT_VARIABLE != 0:
				_gk_dbg_hosts.append(node)
				break


func _gk_dbg_text() -> String:
	_gk_dbg_find_hosts()
	var lines := PackedStringArray()
	var cam := get_viewport().get_camera_3d()
	var cam_name := String(cam.name) if cam != null else "(没有相机)"
	var cam_pos := cam.global_position if cam != null else Vector3.ZERO
	lines.append("[GK 调试面板]  %s 弹出/收起 · %s 复制全部" % [
		_gk_dbg_key_name(_GK_DBG_KEY), _gk_dbg_key_name(_GK_DBG_COPY_KEY)])
	lines.append("FPS %d   相机 %s  (%.1f, %.1f, %.1f)" % [
		Engine.get_frames_per_second(), cam_name, cam_pos.x, cam_pos.y, cam_pos.z])
	if god_camera:
		var god_on := false
		if _gk_god_cam != null and is_instance_valid(_gk_god_cam):
			god_on = _gk_god_cam.is_current()
		# 键位提示(第三十九轮补齐:滚轮缩放 / 附身键也一并列出来)
		lines.append("上帝摄像机:%s(Tab 切 · %s 时停 · 滚轮缩放 · %s 附身%s)" % [
			"开" if god_on else "关",
			_gk_dbg_key_name(_GK_TIME_STOP_KEY),
			_gk_dbg_key_name(_GK_POSSESS_KEY),
			"中" if _gk_god_possess != null else ""])
	var watch := debug_watch.strip_edges()
	if not watch.is_empty():
		lines.append("显示这几项:%s" % watch.replace("|", "、"))
	for host in _gk_dbg_hosts:
		lines.append(_gk_dbg_host_text(host, watch))
		var logs = host.get("_gk_debug_logs")
		if logs is Array and not logs.is_empty():
			lines.append("  最近打印(%d 条):" % logs.size())
			for text in logs:
				lines.append("    " + str(text))
	return "\n".join(lines)


## 一个宿主的状态 + 它的变量(内部变量 _gk_ 跳过;勾了监视就只看那几个)。
func _gk_dbg_host_text(host: Node, watch: String) -> String:
	var parts := PackedStringArray()
	if host is CharacterBody3D:
		var body := host as CharacterBody3D
		parts.append("位置 (%.1f, %.1f, %.1f)" % [body.global_position.x, body.global_position.y, body.global_position.z])
		parts.append("速度 (%.1f, %.1f, %.1f)" % [body.velocity.x, body.velocity.y, body.velocity.z])
		parts.append("在地面 " + ("是" if body.is_on_floor() else "否"))
	elif host is Node3D:
		var node3 := host as Node3D
		parts.append("位置 (%.1f, %.1f, %.1f)" % [node3.global_position.x, node3.global_position.y, node3.global_position.z])
	elif host is CharacterBody2D:
		var body2 := host as CharacterBody2D
		parts.append("位置 (%.1f, %.1f)" % [body2.global_position.x, body2.global_position.y])
		parts.append("速度 (%.1f, %.1f)" % [body2.velocity.x, body2.velocity.y])
		parts.append("在地面 " + ("是" if body2.is_on_floor() else "否"))
	elif host is Node2D:
		var node2 := host as Node2D
		parts.append("位置 (%.1f, %.1f)" % [node2.global_position.x, node2.global_position.y])
	var out := PackedStringArray()
	out.append("▌%s  %s" % [String(host.name), "  ".join(parts)])
	for info in host.get_property_list():
		if int(info.usage) & PROPERTY_USAGE_SCRIPT_VARIABLE == 0:
			continue
		var vname := String(info.name)
		if vname.begins_with("_gk_"):
			continue
		if not watch.is_empty() and not watch.split("|").has(vname):
			continue
		out.append("    %s = %s" % [vname, str(host.get(vname))])
	return "\n".join(out)


## 按键名(F1 / 空格 这种)显示给用户 —— 直接问 Godot 要,不自己写表。
func _gk_dbg_key_name(key: int) -> String:
	var text := OS.get_keycode_string(key)
	return text if not text.is_empty() else "那个键"


## 复制全部内容:剪贴板 + 控制台。
## 控制台那份是给"没有剪贴板的环境"(比如无头跑)和"想直接看"用的。
func _gk_dbg_copy() -> void:
	_gk_dbg_find_hosts()
	var text := _gk_dbg_text()
	if DisplayServer.has_feature(DisplayServer.FEATURE_CLIPBOARD):
		DisplayServer.clipboard_set(text)
		print("[GK 调试面板] 已复制 %d 行到剪贴板:" % text.split("\n").size())
	else:
		print("[GK 调试面板] 这个环境没有剪贴板,内容打在下面:")
	print(text)


func _activate_camera() -> void:
	if camera_name.is_empty():
		return
	var found := _find_camera()
	if found == null:
		push_warning("[GK] 没找到相机节点: " + camera_name)
		return
	if camera_fov > 0.0:
		found.fov = camera_fov
	# ── 别人已经选好相机了就别抢(用户 2026-10-04,真 Godot 探针抓到的)──
	# 环境脚本是场景里**最后**一个节点,所以它的 _ready 排在角色脚本之后:
	# 角色在『每当_准备』里切了机位(比如开场沿曲线运镜),这里再
	# make_current() 会把镜头**抢回 Blender 的主相机** —— 表现就是
	# "切了镜头又自己跳回来"。
	# 没有相机在用的时候(单相机项目,老行为)才由这里点亮。
	if get_viewport().get_camera_3d() == null:
		found.make_current()


## 找"场景根节点"。
##
## ── 这里踩了两个坑,都记下来 ────────────────────────────────────
##
## 坑 1:不能用 `get_node_or_null(camera_name)` 找相机 ——
## 那是**相对本节点**的路径,而相机和本节点是兄弟,必然找不到。
## 所以下面用 find_child 从场景根往下搜。
##
## 坑 2:**不能只靠 `get_tree().current_scene`**。
## 用 `godot --script` 跑探测脚本时它返回的是**窗口根节点**(/root),
## 于是灯被挂成了 `/root/Lights` 而不是 `<场景>/Lights` ——
## 看起来"生成了 4 盏灯"却一盏都找不到(实测排查了很久)。
##
## 正确做法:从自己往上走,找到最上面那个**有父节点的**节点,
## 也就是场景实例的根(它挂在 /root 下面)。
func _scene_root() -> Node:
	var node: Node = self
	while node.get_parent() != null and node.get_parent().get_parent() != null:
		node = node.get_parent()
	return node


## 按静态碰撞体烤一块导航网格(第三十四轮:寻路;第三十六轮加 2D / 重烤)。
##
## ── 为什么走 NavigationServer 的底层 API,不用 region.bake_xxx()
## 区域自己的 bake 在"根节点 = 区域"的语义下把**区域自己的子节点**当几何源 ——
## 而本项目的地面/墙都在场景根下面(不在区域底下),实测烤出来 **0 个多边形**。
## 底层 API 能**明确指定根节点**(这里给场景根),烤出来的东西才是对的。
##
## ── 为什么烤完要**赋两次**(第三十六轮实测)────────────────────────
## 进程里"第一个"地图的第一次赋值会被服务器吞掉:烤完等 5 帧查路径
## 还是空;把同一份网格 duplicate 再赋一次,立刻就有绕墙路径。
## 第二个地图起一次就够 —— 但多赋一次的代价约等于零,统一赋两次。
func _gk_bake_navigation() -> void:
	var root := _scene_root()
	if root == null:
		return
	# ── 多层导航(第八十五轮):一层一块导航区,都要烤 ────────────────────
	# 层 1 = `Navigation`、层 n = `Navigation_n`;每块的网格子资源里写了
	# source group(见 NAVMESH_SUBRESOURCE),所以 parse 时**只挑本层的
	# 几何** —— 这里不用自己过滤。
	var regions := _gk_nav_regions(root)
	if regions.is_empty():
		print("[GK] 『寻路』开着,但场景里没有 Navigation 节点 —— 重新导出一次")
		return
	for region in regions:
		if region is NavigationRegion3D:
			_gk_bake_navigation_3d(root, region)
		elif region is NavigationRegion2D:
			_gk_bake_navigation_2d(root, region)


## 场景里的所有导航区(层 1 = `Navigation`,层 n = `Navigation_n`)。
## 按树里的顺序 —— 导出就是按层号写的,所以层 1 在前。
func _gk_nav_regions(root: Node) -> Array:
	var out: Array = []
	for node in root.find_children("Navigation*", "", true, false):
		if node is NavigationRegion3D or node is NavigationRegion2D:
			out.append(node)
	return out


## 重烤(第三十六轮:门 / 平台挪了之后)。
## 『移动_重烤导航』节点经生成脚本转发到这里;启动时 _ready 里烤的
## 那次也走同一条路 —— 一个入口,行为一致。
##
## ⚠ 为什么**等两个物理帧**再烤(真 Godot 实测):
## 『移动_设置位置』挪完门之后,如果**同一帧**就重烤,新位置还没进
## 物理世界 —— 烤出来的还是老网格(门洞照样堵着,角色继续绕墙端)。
## 实测对照:同帧烤出来 12 个多边形(= 没变,路径绕行 5 个点);
## 隔 2 帧烤 24 个(= 门洞通了,路径直穿 9 个点)。门/平台是
## AnimatableBody3D 时尤其明显(位移要等下一个物理步);StaticBody
## 同帧就生效 —— 但统一等两帧,两种体都稳。
func rebake_navigation() -> void:
	await get_tree().physics_frame
	await get_tree().physics_frame
	_gk_bake_navigation()


## 3D:几何(静态碰撞体)是实体,烘焙器自动把"能站的面"烤成可行走区。
func _gk_bake_navigation_3d(root: Node, region: NavigationRegion3D) -> void:
	if region.navigation_mesh == null:
		print("[GK] Navigation 节点上没有导航网格 —— 重新导出一次")
		return
	var navmesh: NavigationMesh = region.navigation_mesh
	var baked: NavigationMesh = navmesh.duplicate()
	var source := NavigationMeshSourceGeometryData3D.new()
	NavigationServer3D.parse_source_geometry_data(baked, source, root)
	# 代价区(第八十四轮):当成"柱形障碍"挖洞 —— 主网格不覆盖它,
	# 价格才有意义(重叠着放的价格会被路径无视,实测)
	for area in nav_costs:
		_gk_add_cost_obstruction(source, area["box"])
	NavigationServer3D.bake_from_source_geometry_data(baked, source)
	region.navigation_mesh = baked
	# ⚠ 防"第一个地图吞第一次赋值"(实测):再赋一份同内容
	region.navigation_mesh = baked.duplicate()
	print("[GK] 导航网格已烤:", baked.get_polygon_count(),
		  " 个多边形(代理半径 ", baked.agent_radius, ")")
	_gk_apply_nav_costs_3d(root, region)


## 2D:碰撞体按**障碍**解析 —— 必须先给一个 outline 当"外框"
## (实测:没有外框时烤出来 0 个多边形)。外框按场景里所有静态碰撞体
## 的包围盒往外扩一点算。所以 2D 场景里的障碍物都该有碰撞体;
## 没有碰撞体 = 没东西可烤(打印一行提示,不报错)。
func _gk_bake_navigation_2d(root: Node, region: NavigationRegion2D) -> void:
	if region.navigation_polygon == null:
		print("[GK] Navigation 节点上没有导航多边形 —— 重新导出一次")
		return
	var old: NavigationPolygon = region.navigation_polygon
	# ── 多层导航(第八十五轮):外框只按**本层**的碰撞体算 ──────────────
	# ⚠ `source_geometry_group_name` 的**默认值不是空串**(是个占位名
	#   `navigation_polygon_source_geometry_group`)—— 光看"名字非空"会把
	#   单层场景也当成"按组烤",然后一个碰撞体都挑不到(实测:层 1 烤出
	#   0 个多边形,整个寻路全哑)。所以必须**同时**看 `source_geometry_mode`
	#   是不是 1(按组)。
	var layer_group := ""
	if int(old.source_geometry_mode) == 1:
		layer_group = str(old.source_geometry_group_name)
	# ── 每次新建一份,不用 duplicate ────────────────────────────────
	# 外框是加进 outline 列表的,duplicate 会把上一轮的旧外框带过来
	# (场景挪过之后旧外框和新外框叠在一起,烤出怪多边形)。
	var poly := NavigationPolygon.new()
	poly.agent_radius = maxf(old.agent_radius, 0.01)
	poly.cell_size = clampf(poly.agent_radius * 0.5, 0.25, 8.0)
	# 把"按组烤"这件事**带过去** —— 不带的话新多边形还是"根下所有几何",
	# 分层就白做了(实测:层 2 的网格会把层 1 的墙也当障碍)
	poly.source_geometry_mode = old.source_geometry_mode
	poly.source_geometry_group_name = old.source_geometry_group_name
	var bounds := Rect2()
	var found := false
	for body in root.find_children("*", "StaticBody2D", true, false):
		if not layer_group.is_empty() and not body.is_in_group(layer_group):
			continue
		for shape_node in body.find_children("*", "CollisionShape2D", true, false):
			var shape: Shape2D = (shape_node as CollisionShape2D).shape
			if shape == null:
				continue
			var rect := shape.get_rect()
			var xform := (shape_node as CollisionShape2D).global_transform
			# ⚠ 四个角必须装进 PackedVector2Array 再循环 —— 写成数组字面量
			# `for corner in [Vector2(...), ...]` 时 corner 是 Variant,
			# `var point := xform * corner` 推断不出类型,整个脚本解析失败
			# (实测:环境脚本一挂,导航没烤 + 上帝机/时停全没了)。
			var corners := PackedVector2Array([
					rect.position, rect.position + Vector2(rect.size.x, 0.0),
					rect.end, Vector2(rect.position.x, rect.end.y)])
			for corner in corners:
				var point := xform * corner
				if not found:
					bounds = Rect2(point, Vector2.ZERO)
					found = true
				else:
					bounds = bounds.expand(point)
	if not found:
		print("[GK] 2D 寻路:场景里没有静态碰撞体 —— 没东西可烤",
			  "(给墙 / 障碍物加碰撞体;整块地板不用加,地板会被当障碍挖掉)")
		return
	bounds = bounds.grow(poly.agent_radius + 1.0)
	poly.add_outline(PackedVector2Array([bounds.position,
			Vector2(bounds.end.x, bounds.position.y),
			bounds.end,
			Vector2(bounds.position.x, bounds.end.y)]))
	poly.parsed_geometry_type = NavigationPolygon.PARSED_GEOMETRY_STATIC_COLLIDERS
	var source := NavigationMeshSourceGeometryData2D.new()
	NavigationServer2D.parse_source_geometry_data(poly, source, root)
	# 代价区(第八十四轮):当**障碍轮廓**加进去 —— 主多边形在这里挖洞
	# (洞 = 代价矩形外扩 1×代理半径,膨胀是烘焙器自己做的)
	for area in nav_costs:
		source.add_obstruction_outline(_gk_cost_points_2d(area))
	NavigationServer2D.bake_from_source_geometry_data(poly, source)
	region.navigation_polygon = poly
	# ⚠ 同上:再赋一份(防"第一个地图吞第一次赋值"的同类问题)
	region.navigation_polygon = poly.duplicate()
	print("[GK] 2D 导航多边形已烤:", poly.get_polygon_count(),
		  " 个多边形(代理半径 ", poly.agent_radius, ")")
	_gk_apply_nav_costs_2d(root, region)


## ── 代价区(第八十四轮:寻路第三版②)—— 泥地 / 危险区 ────────────────
## 物体『游戏物理』的『通行代价』填 != 1 = 这片地"过路费":> 1 是泥地 /
## 危险区(寻路尽量绕开),< 1 是近路(会吸引路径)。
##
## ── 为什么是"挖洞 + 补洞",而不是把代价块叠在主网格上 ─────────────
## 真 Godot spike 实测:**两块 region 重叠时,路径无视上面那块的价格
## 照直穿**;必须让主网格在代价区**没有覆盖**(烘焙时当障碍挖掉,见
## 上面两个烘焙函数),再单独烤一小块**填回洞里**并设 `travel_cost` ——
## A* 这才会在"绕路"和"穿泥地"之间按价格选(实测:代价 2 就绕开,
## 代价 1 直穿;2D / 3D 行为一致)。
##
## 尺寸关系(烘焙器语义:外框内缩 agent_radius、障碍外扩同样多):
##   * 洞 = 代价矩形外扩 1×半径(主网格那边的障碍膨胀出来的);
##   * 补块 = 单独烤,外框取"代价矩形外扩 2×半径" —— 被内缩半径后
##     正好等于那个洞;两边顶点精确重合,跨区域连接实测稳。
var _gk_nav_cost_regions: Dictionary = {{}}


## 补块 region 要进的地图:用**世界自己的**导航地图(root 视口里那张)。
## ⚠ 不能用 `region_get_map(主区域)`:烘焙发生在 _ready 里,那时主区域
##   节点**还没同步进地图**,读出来是空 RID —— 补块会进不了任何地图
##   (真机实测:地图上只有 1 个 region,路径死活进不了代价区)。
func _gk_nav_cost_map(root: Node, is_3d: bool) -> RID:
	var viewport := root.get_viewport()
	if viewport != null:
		if is_3d:
			var world3 := viewport.find_world_3d()
			if world3 != null:
				return world3.navigation_map
		else:
			var world2 := viewport.find_world_2d()
			if world2 != null:
				return world2.navigation_map
	return RID()


## 2D 代价区:逐块烤 + 建/更新服务器 region(用 RID 记住,重烤不堆积)。
func _gk_apply_nav_costs_2d(root: Node, region: NavigationRegion2D) -> void:
	if nav_costs.is_empty() or region.navigation_polygon == null:
		return
	var radius: float = maxf(region.navigation_polygon.agent_radius, 0.01)
	var map := _gk_nav_cost_map(root, false)
	if not map.is_valid():
		map = NavigationServer2D.region_get_map(region.get_rid())
	if not map.is_valid():
		print("[GK] 代价区进不了 2D 导航地图 —— 重新导出一次")
		return
	# 多层导航(第八十五轮):补块**跟着本块的层走** —— 层掩码当 key 前缀,
	# 各层的补块互不覆盖;补块自己也要挂上同样的层,不然层 2 的角色
	# 看不见这滩泥地(它只在层 1)。
	var layers := region.navigation_layers
	var key_base := layers * 100
	for i in nav_costs.size():
		var area: Dictionary = nav_costs[i]
		var rid: RID = _gk_nav_cost_regions.get(key_base + i, RID())
		if not rid.is_valid():
			rid = NavigationServer2D.region_create()
			NavigationServer2D.region_set_map(rid, map)
			_gk_nav_cost_regions[key_base + i] = rid
		var poly := _gk_bake_nav_cost_2d(root, area["rect"], radius)
		NavigationServer2D.region_set_navigation_polygon(rid, poly)
		# ⚠ 同主区域:烤出来的第一份可能被服务器吞掉(真机实测:
		#   路径死活进不了代价区),再赋一份同内容就好
		NavigationServer2D.region_set_navigation_polygon(rid, poly.duplicate())
		NavigationServer2D.region_set_navigation_layers(rid, layers)
		NavigationServer2D.region_set_travel_cost(rid, area["cost"])
		print("[GK] 代价区『", area["name"], "』:补块 ",
			  poly.get_polygon_count(), " 块,代价 ", area["cost"])


## 2D 补块:外框 = 代价矩形外扩 2×半径(内缩半径后 == 主网格的洞)。
## 障碍照常参与(代价区里摆着墙的话,补块也把它挖掉,和主网格一致)。
func _gk_bake_nav_cost_2d(root: Node, rect: Rect2,
		radius: float) -> NavigationPolygon:
	var poly := NavigationPolygon.new()
	poly.agent_radius = maxf(radius, 0.01)
	poly.cell_size = clampf(poly.agent_radius * 0.5, 0.25, 8.0)
	poly.parsed_geometry_type = NavigationPolygon.PARSED_GEOMETRY_STATIC_COLLIDERS
	var grown := rect.grow(poly.agent_radius * 2.0)
	poly.add_outline(PackedVector2Array([grown.position,
			Vector2(grown.end.x, grown.position.y),
			grown.end,
			Vector2(grown.position.x, grown.end.y)]))
	var source := NavigationMeshSourceGeometryData2D.new()
	NavigationServer2D.parse_source_geometry_data(poly, source, root)
	NavigationServer2D.bake_from_source_geometry_data(poly, source)
	return poly


func _gk_cost_points_2d(area: Dictionary) -> PackedVector2Array:
	var rect: Rect2 = area["rect"]
	return PackedVector2Array([rect.position,
			Vector2(rect.end.x, rect.position.y), rect.end,
			Vector2(rect.position.x, rect.end.y)])


## 3D 代价区:手工矩形补块(顶点和洞的边重合)+ 建/更新服务器 region。
func _gk_apply_nav_costs_3d(root: Node, region: NavigationRegion3D) -> void:
	if nav_costs.is_empty() or region.navigation_mesh == null:
		return
	var map := _gk_nav_cost_map(root, true)
	if not map.is_valid():
		map = NavigationServer3D.region_get_map(region.get_rid())
	if not map.is_valid():
		print("[GK] 代价区进不了 3D 导航地图 —— 重新导出一次")
		return
	# 多层导航(第八十五轮):同 2D —— 补块跟着本块的层走
	var layers := region.navigation_layers
	var key_base := layers * 100
	for i in nav_costs.size():
		var area: Dictionary = nav_costs[i]
		var rid: RID = _gk_nav_cost_regions.get(key_base + i, RID())
		if not rid.is_valid():
			rid = NavigationServer3D.region_create()
			NavigationServer3D.region_set_map(rid, map)
			_gk_nav_cost_regions[key_base + i] = rid
		var mesh := _gk_nav_cost_mesh_3d(region.navigation_mesh, area["box"])
		NavigationServer3D.region_set_navigation_mesh(rid, mesh)
		# ⚠ 同主区域:第一份可能被服务器吞掉,再赋一份同内容
		NavigationServer3D.region_set_navigation_mesh(rid, mesh.duplicate())
		NavigationServer3D.region_set_navigation_layers(rid, layers)
		NavigationServer3D.region_set_travel_cost(rid, area["cost"])
		print("[GK] 代价区『", area["name"], "』:补块 1 块(面高 ",
			  mesh.get_vertices()[0].y, "),代价 ", area["cost"])


## 3D 补块:代价矩形(XZ)的四个角 + 面高度。高度**必须**取主网格在
## 那一带的面高 —— 实测 recast 的面不在 y=0(高度量化),高度差一点点
## 跨区域就连接不上。
func _gk_nav_cost_mesh_3d(navmesh: NavigationMesh, box: AABB) -> NavigationMesh:
	var y := _gk_nav_cost_surface_y(navmesh, box)
	var mesh := NavigationMesh.new()
	mesh.agent_radius = navmesh.agent_radius
	mesh.set_vertices(PackedVector3Array([
			Vector3(box.position.x, y, box.position.z),
			Vector3(box.position.x, y, box.end.z),
			Vector3(box.end.x, y, box.end.z),
			Vector3(box.end.x, y, box.position.z)]))
	mesh.add_polygon(PackedInt32Array([0, 1, 2, 3]))
	return mesh


## 面高:矩形里没有顶点(被挖成洞了)就往外扩 1 米找那一圈;再没有
## 就退回物体自己的底面高度(空场景的兜底)。
func _gk_nav_cost_surface_y(navmesh: NavigationMesh, box: AABB) -> float:
	var verts := navmesh.get_vertices()
	for band in [0.0, 1.0]:
		var best := -1.0e9
		for v in verts:
			if v.x < box.position.x - band or v.x > box.end.x + band:
				continue
			if v.z < box.position.z - band or v.z > box.end.z + band:
				continue
			best = maxf(best, v.y)
		if best > -1.0e8:
			return best
	return box.position.y


## 3D 挖洞:投影障碍(柱体)= 矩形的 XZ 四个角 + 从"底面往下 0.5 米"
## 到"顶面往上 0.5 米" —— 贴地的薄泥地也要能碰到脚下的导航面
## (只按物体自己的高度范围会擦着地面切不到)。
func _gk_add_cost_obstruction(source: NavigationMeshSourceGeometryData3D,
		box: AABB) -> void:
	var corners := PackedVector3Array([
			Vector3(box.position.x, box.position.y, box.position.z),
			Vector3(box.end.x, box.position.y, box.position.z),
			Vector3(box.end.x, box.position.y, box.end.z),
			Vector3(box.position.x, box.position.y, box.end.z)])
	source.add_projected_obstruction(corners, box.position.y - 0.5,
			box.size.y + 1.0, true)


func _find_camera() -> Camera3D:
	var root := _scene_root()
	if root == null:
		return null
	if not camera_name.is_empty():
		var direct := root.find_child(camera_name, true, false)
		if direct is Camera3D:
			return direct as Camera3D
	for node in root.find_children("*", "Camera3D", true, false):
		if node is Camera3D:
			return node as Camera3D
	return null
'''

# 环境脚本模板的默认填充值(加新字段时只改这一处)。
# 导出走 _env_fields();测试 / 工具用 render_env_script(**想改的字段) ——
# 以前测试各自硬写一整套字段,模板一加字段就一片 KeyError(刚踩过)。
ENV_SCRIPT_DEFAULTS = dict(
    camera_name="", fov="0.000000", materials="",
    follow_scene_light="false", god_camera="false",
    debug_panel="false", debug_watch="",
    debug_key="KEY_F1", debug_copy_key="KEY_F2",
    time_stop_key="KEY_T",
    possess_key="KEY_F",
    esc_quit="true",
    nav_bake="false",
    # 代价区清单(第八十四轮):空数组 = 没有代价区
    nav_costs="[]",
    follow_lights="",
    # 开局 BGM(第七十七轮)。这两个键和 `_env_fields` 里那两行**必须一一对应**:
    # 只加 `_env_fields` 忘了加这里 -> 模板渲染 KeyError,而且只在
    # `render_env_script()` 那条路(测试 / 工具)上炸 —— 导出反而好好的。
    # 有 `TestRound77SceneBgm.test_defaults_cover_every_placeholder` 锁着。
    bgm_path="", bgm_volume="-6.0",
    # 音乐设置(第八十六轮 E 组 14):循环 / 总线 / 总线音量。
    # 默认值 = 老行为(循环开、总线空、不应用)。
    bgm_loop="true", bgm_bus="",
    music_bus="Music", music_bus_db="-6.0", music_bus_apply="false",
)


def render_env_script(**overrides):
    """渲染环境脚本文本(测试 / 工具用;导出走 build_environment_nodes)。"""
    fields = dict(ENV_SCRIPT_DEFAULTS)
    fields.update(overrides)
    return ENVIRONMENT_SCRIPT.format(**fields)


ENVIRONMENT_NODE = """
[node name="Environment" type="Node" parent="."]
script = ExtResource("env_script")
camera_name = "{camera_name}"
camera_fov = {fov}
materials = "{materials}"
"""

COLLISION_TEMPLATE = """
[sub_resource type="CapsuleShape3D" id="cap3d"]
radius = {radius:.4f}
height = {height:.4f}

[sub_resource type="CapsuleShape2D" id="cap2d"]
radius = {radius:.4f}
height = {height:.4f}
"""

COLLISION_NODE_3D = """
[node name="Collision" type="CollisionShape3D" parent="."]
shape = SubResource("cap3d")
"""

COLLISION_NODE_2D = """
[node name="Collision" type="CollisionShape2D" parent="."]
shape = SubResource("cap2d")
"""

TOON_MATERIAL_TEMPLATE = """[gd_resource type="ShaderMaterial" load_steps={steps} format=3]

[ext_resource type="Shader" path="{shader_path}" id="1_shader"]
{textures}[resource]
shader = ExtResource("1_shader")
shader_parameter/albedo_color = Color({r:.4f}, {g:.4f}, {b:.4f}, 1)
shader_parameter/use_albedo_texture = {use_texture:.1f}
shader_parameter/albedo_strength = 1.0
shader_parameter/albedo_texture = {albedo_ref}
shader_parameter/shadow_step = 0.1
shader_parameter/shadow_smooth = 0.02
shader_parameter/three_tone = 0.0
shader_parameter/lit_tint = Color(1, 0.976, 0.914, 1)
shader_parameter/shadow_tint = Color(0.706, 0.627, 0.902, 1)
shader_parameter/specular_amount = 0.0
shader_parameter/rim_amount = 0.0
shader_parameter/emission_amount = 0.0
"""

# ============================================================================
# TOON_MATERIAL_TEMPLATE 的参数为什么是这些值 —— **注释不能写进 .tres 里面**
# ============================================================================
#
# ── 血的教训:.tres 里不能有任何 `#` 注释 ────────────────────────────────
# 曾经把下面这些说明直接写进上面的模板里(插在 shader_parameter 中间),
# 结果 Godot 对**每一个材质**报:
#
#     Parse Error: Unexpected identifier 'rim_color'.
#     Failed loading resource: res://materials/material.tres
#
# `.tres` 是**资源格式**,不是脚本 —— 解析器只认 `[段]` 和 `键 = 值`,
# 见到 `#` 就当语法错误,整个材质文件加载失败。后果就是模型又变回白模。
# 所以:注释写在这里(Python 里),不要写进模板字符串。
#
# ── 颜色数字的色空间(第九十六轮,别"顺手统一"成 env 那套)─────────────
# 这里的 albedo_color 是**线性**值、1:1 直写 —— shader 里它标了
# `source_color`,Godot 上传 uniform 时会自己把值当 sRGB 转成线性。
# 而 `_env_color_text`(Environment / 天空)那边**要**换算,
# 因为那些 Color 是"显示值"语义(实测标定见那个函数的说明)。
#
# ── rim_amount / specular_amount 为什么默认 0 ──────────────────────────
# 这两个是 **EMISSION 加法项**:
#     EMISSION += rim_color.rgb * rim * rim_amount
#     EMISSION += vec3(spot * specular_amount)
# 加法项不参与任何明暗衰减,直接叠在 albedo 上。实测(33 材质槽的
# MMD 模型,太阳灯 light_energy = 4.0):
#     rim=0.4  specular=0.2  -> 一层没有细节的白壳
#     rim=0.08 specular=0.05 -> 仍然偏白偏灰
#     rim=0.0  specular=0.0  -> 肤色/红衣/黑发全部正确显示
# 想要边缘光/高光的用户自己在 Godot 里调上去。
#
# ── 曾经有、现已删掉的 4 个参数(2026-10-04 清理)──────────────────────
# `toon_exposure` / `light_map_mix` / `shadow_strength` / `backlight_amount`
# 是**老版 `light()`-based shader** 的遗留参数。现在的 gk_toon.gdshader 是
# `unshaded`,自己按 `light_dir` 画色阶,**根本没有这四个 uniform**
# (已核对 shaders/gk_toon.gdshader)。继续写进 .tres 只会让 Godot 每次
# 加载材质都刷一条 "shader parameter not found" 警告,所以全部删掉。
#
# 当初实测 `toon_exposure` / `shadow_tint` 改了渲染 PNG **字节完全相同**,
# 也是同一个结论的旁证:`light()` 里的色阶那段在当前 Godot 4.7 下基本没
# 参与最终着色。真正决定观感的是 ALBEDO + EMISSION 加法项。
#
# (顺带:卡通材质是否跟着场景太阳画明暗,现在由场景属性
#  『卡通跟随场景光照』控制 —— 默认关 = 全平涂;见 ENVIRONMENT_SCRIPT。)


def safe_name(text, fallback="model"):
    name = re.sub(r"[^A-Za-z0-9_\-]", "", snake_case(text or "", ""))
    return name or fallback


def _unique_file_names(names, fallback="material", prefix=""):
    """给一串材质名算出**互不重复**的文件名。返回 {原名: 文件名}。

    ── prefix 是干什么的(用户 2026-10-04,多场景)────────────────────
    每个场景的材质都从 `material` 从头数,而文件名只由**材质名**决定 ——
    于是两个 Blender 场景会写出同名文件:关卡2 的 `material_4.tres`
    (第二关角色,红色)把关卡1 的 `material_4.tres`(东墙,灰色)**盖掉**,
    用户看到的就是"关卡示例的东墙莫名变成红色"。
    多场景导出时按场景名加前缀(`关卡1_material_4.tres`);单场景不加 ——
    老项目的产物一个字符都不变(和 `_char_glb_name` 同一套约定)。

    ── 为什么需要去重(踩过的坑)────────────────────────────────────
    `safe_name()` 会把非 [A-Za-z0-9_-] 的字符**全删掉**,而 Blender 给
    重名材质加的后缀恰好是点号:

        material      -> material
        material.001  -> material001     (点号被删)
        material.002  -> material002

    看着还行,但 MMD 模型(41 个材质槽)导入后材质名经常是
    `material` / `material.001` … 甚至全是同一个基础名 ——
    实测日志里 `写入 material.tres` **连续出现了 41 次**:
    41 个材质全写到同一个文件、互相覆盖,最后只剩最后一个。
    用户在 Godot 里看到的就是"模型变白、材质没导出"。

    这里统一做一次去重:重名的加 `_2`、`_3` 后缀。
    """
    used = {}
    out = {}
    for name in names:
        base = prefix + safe_name(name, fallback)
        candidate = base
        index = 2
        while candidate in used:
            candidate = f"{base}_{index}"
            index += 1
        used[candidate] = True
        out[name] = candidate
    return out


# 碰撞预览物体的标记属性。
# 用自定义属性而不是名字前缀来识别 —— 用户可能给物体起任何名字,
# 靠名字判断会误删。导出时也靠它来排除预览线框。
PREVIEW_MARK = "gk_collision_preview"

# 工具生成的辅助物理体带这些自定义属性(目前只认 MMD 的刚体/关节)。
# 它们既不该当碰撞体,也不该当几何导出 —— 见 is_helper_physics。
HELPER_PHYSICS_KEYS = ("mmd_type", "mmd_rigid", "mmd_joint")

# 工具生成的辅助材质都以此为前缀(描边预览材质等)。
# 导出时要跳过它们 —— 否则用户会看到自己的模型在 Godot 里变成纯黑壳。
TOOL_MATERIAL_PREFIX = "__GK"


def _toon_follow_light(scene):
    """场景设置:卡通材质是否跟着场景灯光画明暗(**默认关 = 全平涂**)。

    用户 2026-10-04 报"导出有光照残留":卡通 shader 是 unshaded,但它会自己
    按 `light_dir` 画色阶,而导出脚本默认每物理帧把场景里的平行光同步进去 ——
    于是场景有太阳时,模型上就出现明暗带。默认关掉 = 只画平涂。
    """
    settings = getattr(scene, "gk_settings", None)
    return bool(getattr(settings, "toon_follow_light", False))


def _god_camera_on(scene):
    """场景设置:要不要给这一关配一台上帝摄像机(**关卡测试用**)。

    用户 2026-10-04:上帝机原来是角色的游戏逻辑生成的 —— 场景里没有角色
    就没有它("没有角色的测试里动不了")。现在改由环境脚本生成,这里读出
    场景开关交给模板(见 ENVIRONMENT_SCRIPT 的 god_camera)。
    """
    settings = getattr(scene, "gk_settings", None)
    return bool(getattr(settings, "god_camera", False))


def _esc_claimed_by_graph(scene):
    """图里已经有人管 Esc 了吗(第三十九轮)?管了就轮不到内置的退出。

    ── 为什么环境脚本要问这个 ──────────────────────────────────────
    环境脚本现在内置了"按 Esc 退出游戏"(用户要的默认功能)。但两种情况下
    Esc **已经有主**了,内置的必须让位:
      * 『界面_暂停游戏』勾了『Esc 热键』-> Esc 是暂停 —— 两个都生效的话
        一次按键既暂停又退出(玩家正在暂停菜单里挑选项,窗口直接没了);
      * 图里接了『按键』(Esc)-> 用户自己把 Esc 派了活(关卡示例 / 曲线示例 /
        物理测试场都是"按 Esc 退出游戏"),内置的再抢一遍就成了
        "抢先退出,把用户接的行为挤掉"。
    所以:**谁都没用,才兜底**(见 ENVIRONMENT_SCRIPT 的 esc_quit)。
    """
    from . import exporter

    tree, _why = exporter.resolve_scene_tree(scene)
    if tree is None:
        return False
    for node in getattr(tree, "nodes", ()) or ():
        kind = getattr(node, "bl_idname", "")
        if kind == "GKNodeUiPauseGame":
            if bool(getattr(node, "gk_hotkey", False)):
                return True
        elif kind == "GKNodeKey":
            if str(getattr(node, "gk_key", "") or "").strip().upper() == "ESC":
                return True
    return False


# ==========================================================================
# 环境与后处理(第四十九轮:阶段②—— WorldEnvironment + Environment)
# ==========================================================================
# 用户在设计文档里选的第二阶段:"导出 WorldEnvironment + Environment 资源:
# 背景(纯色 / 天空)、环境光、雾(开关 / 浓度 / 颜色)、泛光(glow)、
# 暗角 / 色调"。入口在『游戏显示』设置图新增的『显示_环境氛围』卡。
#
# ── 为什么是**子资源**(而不是单独一个 env/*.tres)──────────────────
# 场景本来就一个 .tscn,Environment 只属于这一关 —— 写成 .tscn 里的
# `[sub_resource type="Environment"]`,Godot 打开就自带,少一个文件、
# 少一处"忘了清理"的风险(多场景导出时每关各一份,天然隔离)。
#
# ── 颜色为什么不转换(和灯光同一条约定)──────────────────────────
# Blender 的 FloatVectorProperty(COLOR)是**线性**空间的值,Godot 的
# 环境颜色也按线性参与渲染 —— 1:1 直接写,不做 sRGB 转换。
#
# ── 暗角呢?──────────────────────────────────────────────────────
# Godot 的 Environment **没有**内置暗角(vignette)—— 暗角要靠后处理
# 着色器做。这一版先给 Environment 真有的这几样(背景 / 环境光 / 雾 /
# 泛光 / 色调映射 + 调色),暗角留到氛围套件那一轮(配合全屏 shader)。

# 背景模式 / 色调映射的取值 = Godot 的枚举数值。离线测试会断言这里的
# 顺序和 properties 里枚举项的**顺序一致**(防两处各写一套飘掉)。
# AGX=4 是第九十五轮补的:真机反射核对过 Godot 4.7 的 hint
# ("Linear,Reinhard,Filmic,ACES,AgX")—— 它和 Blender 默认的 AgX 视图
# 变换同族,选它预览和运行时能精确对齐(godot_look 里那张映射表)。
ENV_BACKGROUND_MODES = {"COLOR": 1, "SKY": 2}
ENV_TONEMAP_MODES = {"LINEAR": 0, "REINHARD": 1, "FILMIC": 2, "ACES": 3,
                     "AGX": 4}

# ── 环境光来源(第九十七轮修)────────────────────────────────────────
# Godot 的 Environment.ambient_light_source 枚举(真机反射核对的顺序):
#     Background=0, Disabled=1, **Color=2**, **Sky=3**
#
# ⚠ 这里曾经写死 **3(Sky)** —— 而那时候注释还写着"用颜色当环境光",
#    说明是枚举搞错了。第九十七轮真机标定(纯色深灰背景 + 一盏灯都没有 +
#    一个 StandardMaterial3D 白球,环境光设成纯红 1.0):
#        source=3(Sky)      -> 球心像素 0.0000,0.0000,0.0000(**全黑**)
#        source=1(Disabled) -> 球心像素 0.0000,0.0000,0.0000(一模一样)
#        source=2(Color)    -> 球心像素 1.0000,0.0000,0.0000(**被红光照亮**)
#    结论:**纯色背景下写 Sky 等于把环境光静默关掉** —— 用户在设置卡上
#    填的『环境光颜色 / 强度』一直被 Godot 忽略。
#
# 现在按背景模式选:纯色 -> Color(用用户填的),天空 -> Sky(拿天空当环境光)。
# ⚠ 天空模式下 Godot 会忽略 `ambient_light_color` / `ambient_light_energy`
#    (光源就是天空)—— 这是引擎语义,不是我们丢参数。
# ⚠ 影响面:卡通材质是 `unshaded`,本来就不吃环境光 —— 所以只有
#    **关掉卡通材质(用 PBR 材质)** 的项目观感会变亮,这是修回本意。
ENV_AMBIENT_SOURCES = {"COLOR": 2, "SKY": 3}

ENV_RESOURCE_ID = "gk_environment"
ENV_SKY_MATERIAL_ID = "gk_sky_material"
ENV_SKY_ID = "gk_sky"

ENVIRONMENT_SUBRESOURCE = """[sub_resource type="Environment" id="{res_id}"]
background_mode = {background_mode}
background_color = {bg_color}
ambient_light_source = {ambient_source}
ambient_light_color = {ambient_color}
ambient_light_energy = {ambient_energy}
fog_enabled = {fog}
fog_light_color = {fog_color}
fog_density = {fog_density}
glow_enabled = {glow}
glow_intensity = {glow_intensity}
glow_bloom = {glow_bloom}
tonemap_mode = {tonemap}
adjustment_enabled = {adjust}
adjustment_brightness = {brightness}
adjustment_contrast = {contrast}
adjustment_saturation = {saturation}
{sky_line}"""

# 程序化天空:一个 ProceduralSkyMaterial + 一个 Sky 资源。
# 天空的顶色 = 用户填的背景色,地平线往白里提一点点(纯色天空不好看);
# 地面那半边压暗,免得"地面颜色"漏进天空里。
SKY_MATERIAL_SUBRESOURCE = """[sub_resource type="ProceduralSkyMaterial" id="{mat_id}"]
sky_top_color = {top}
sky_horizon_color = {horizon}
ground_bottom_color = {ground_bottom}
ground_horizon_color = {horizon}"""

SKY_SUBRESOURCE = """[sub_resource type="Sky" id="{sky_id}"]
sky_material = SubResource("{mat_id}")"""

ENVIRONMENT_NODE_TEXT = """[node name="WorldEnvironment" type="WorldEnvironment" parent="."]
environment = SubResource("{res_id}")"""


def _env_number(settings, attr, fallback):
    """读一个数字型场景设置(读不到 / 非法值就回退),并夹到合理范围。"""
    try:
        value = float(getattr(settings, attr, fallback))
    except (TypeError, ValueError):
        return float(fallback)
    if value != value:                                  # NaN
        return float(fallback)
    return value


def _env_color_text(value, fallback=(0.0, 0.0, 0.0, 1.0)):
    """把场景设置里的 RGBA 读成 Godot 的 Color(...) 文本。

    ── 为什么要过一遍 linear -> sRGB(第九十六轮)───────────────────
    Blender 的颜色属性存的是**线性**值(取色器把 sRGB 显示值换算成线性存
    下来),而写进 `.tscn` 的 `Color(...)` 数字在 Godot 里是**显示值** ——
    第九十五轮真机标定:`Environment.background_color = Color(0.3,0.3,0.3)`
    渲出来的像素就是 **0.298**。以前 1:1 直写 -> **Blender 取色器上的色块
    ≠ 游戏里的颜色**(游戏偏暗)。换算之后:取色器看到什么,游戏里就是什么。

    ⚠ **材质**那边的 `albedo_color` **不换算**(见 TOON_MATERIAL_TEMPLATE):
    它是 shader 里标了 `source_color` 的 uniform,Godot 会自己把值当 sRGB
    转成线性 —— 1:1 直写才是对的。两处的规矩不一样,别顺手统一。
    """
    try:
        parts = [float(component) for component in value]
    except (TypeError, ValueError):
        parts = list(fallback)
    while len(parts) < 4:
        parts.append(1.0)
    red, green, blue, alpha = parts[:4]
    return "Color({:.6f}, {:.6f}, {:.6f}, {:.6f})".format(
        godot_look.linear_to_srgb(red),
        godot_look.linear_to_srgb(green),
        godot_look.linear_to_srgb(blue),
        alpha)


def _env_mix(color, other, amount):
    """两个 RGBA 之间线性插值(天空地平线用:顶色往白里提)。"""
    out = []
    for index in range(4):
        a = float(color[index]) if index < len(color) else 1.0
        b = float(other[index]) if index < len(other) else 1.0
        out.append(a + (b - a) * float(amount))
    return tuple(out)


def env_settings(scene):
    """读出这一关的环境设置;**没开**(或读不到)返回 None。

    纯读 `scene.gk_settings` 的属性 —— 离线测试喂一个假对象就能验。

    ⚠ 2D 项目直接返回 None:WorldEnvironment 是 3D 的东西,挂在 2D 场景里
    什么都不会发生(用户会觉得"开关开了没反应")。所以卡上开着也**不导**,
    卡自己会红字提醒"只支持 3D"(和调试面板同一条约定)。
    """
    settings = getattr(scene, "gk_settings", None)
    if settings is None or not bool(getattr(settings, "env_enabled", False)):
        return None
    if str(getattr(settings, "dimension", "3")) == "2":
        return None
    background = str(getattr(settings, "env_background", "COLOR") or "COLOR")
    tonemap = str(getattr(settings, "env_tonemap", "LINEAR") or "LINEAR")
    return {
        "background": background if background in ENV_BACKGROUND_MODES else "COLOR",
        "tonemap": tonemap if tonemap in ENV_TONEMAP_MODES else "LINEAR",
        "bg_color": _env_color_text(getattr(settings, "env_bg_color", None),
                                    (0.05, 0.06, 0.10, 1.0)),
        "ambient_color": _env_color_text(
            getattr(settings, "env_ambient_color", None), (0.55, 0.62, 0.80, 1.0)),
        "ambient_energy": _env_number(settings, "env_ambient_energy", 0.3),
        "fog": bool(getattr(settings, "env_fog", False)),
        "fog_color": _env_color_text(getattr(settings, "env_fog_color", None),
                                     (0.30, 0.34, 0.42, 1.0)),
        "fog_density": _env_number(settings, "env_fog_density", 0.02),
        "glow": bool(getattr(settings, "env_glow", False)),
        "glow_intensity": _env_number(settings, "env_glow_intensity", 0.8),
        "glow_bloom": _env_number(settings, "env_glow_bloom", 0.1),
        "adjust": bool(getattr(settings, "env_adjust", False)),
        "brightness": _env_number(settings, "env_brightness", 1.0),
        "contrast": _env_number(settings, "env_contrast", 1.0),
        "saturation": _env_number(settings, "env_saturation", 1.0),
    }


def environment_scene_blocks(scene):
    """环境与后处理的 (子资源文本列表, 节点文本)。没开 -> ([], "")。

    子资源要**加进 .tscn 的 sub_resources**(load_steps 之前),
    节点文本拼到场景末尾 —— 调用方负责这两件事(见 write_world_scene)。
    """
    values = env_settings(scene)
    if values is None:
        return [], ""

    sub_resources = []
    sky_line = ""
    if values["background"] == "SKY":
        settings = getattr(scene, "gk_settings", None)
        top = _godot_color_tuple(getattr(settings, "env_bg_color", None),
                                 (0.05, 0.06, 0.10, 1.0))
        horizon = _env_mix(top, (1.0, 1.0, 1.0, 1.0), 0.35)
        ground = tuple(component * 0.5 for component in top[:3]) + (1.0,)
        sub_resources.append(SKY_MATERIAL_SUBRESOURCE.format(
            mat_id=ENV_SKY_MATERIAL_ID,
            top=_env_color_text(top), horizon=_env_color_text(horizon),
            ground_bottom=_env_color_text(ground)))
        sub_resources.append(SKY_SUBRESOURCE.format(
            sky_id=ENV_SKY_ID, mat_id=ENV_SKY_MATERIAL_ID))
        sky_line = f'sky = SubResource("{ENV_SKY_ID}")'

    sub_resources.append(ENVIRONMENT_SUBRESOURCE.format(
        res_id=ENV_RESOURCE_ID,
        background_mode=ENV_BACKGROUND_MODES[values["background"]],
        # 纯色 -> Color(用户填的颜色才真的生效)、天空 -> Sky(拿天空当环境光);
        # 见 ENV_AMBIENT_SOURCES 上面那段真机标定
        ambient_source=ENV_AMBIENT_SOURCES[values["background"]],
        bg_color=values["bg_color"],
        ambient_color=values["ambient_color"],
        ambient_energy="%.6f" % values["ambient_energy"],
        fog="true" if values["fog"] else "false",
        fog_color=values["fog_color"],
        fog_density="%.6f" % values["fog_density"],
        glow="true" if values["glow"] else "false",
        glow_intensity="%.6f" % values["glow_intensity"],
        glow_bloom="%.6f" % values["glow_bloom"],
        tonemap=ENV_TONEMAP_MODES[values["tonemap"]],
        adjust="true" if values["adjust"] else "false",
        brightness="%.6f" % values["brightness"],
        contrast="%.6f" % values["contrast"],
        saturation="%.6f" % values["saturation"],
        sky_line=sky_line))
    return sub_resources, ENVIRONMENT_NODE_TEXT.format(res_id=ENV_RESOURCE_ID)


def _godot_color_tuple(value, fallback=(0.0, 0.0, 0.0, 1.0)):
    """场景设置里的 RGBA -> 四元组(给天空材料算颜色用)。"""
    try:
        parts = [float(component) for component in value]
    except (TypeError, ValueError):
        return tuple(fallback)
    while len(parts) < 4:
        parts.append(1.0)
    return tuple(parts[:4])


# ==========================================================================
# 寻路 / 导航网格(第三十四轮:用户选的"敌人会绕墙追你")
# ==========================================================================
# 导出的是一块 **运行时烤的** 导航网格:场景里放一个 NavigationRegion3D
# (名字 `Navigation`),环境脚本启动时按 `NavigationMesh` 的参数把
# **静态碰撞体**烤成可行走的多边形。
#
# ── 为什么按"静态碰撞体"烤(实测三个模式都跑过)───────────────────
#   * 只按网格实例(0):本项目的网格都在 glb 实例里、和碰撞代理是两棵树,
#     而且**纯装饰网格**(没有碰撞的)也会被烤进去 —— 不干净;
#   * 两者都要(2):角色/装饰物都会被烤进网格(实测探针:
#     环境网格从 20 个多边形变成 26 个,路径开始绕着**角色**走);
#   * 只按静态碰撞体(1) ✅:地面/墙/高台/树/石头都在 Colliders/ 下有
#     StaticBody —— 而 CharacterBody(角色/载具)和 RigidBody **不参与**
#     (实测:角色站的地方网格是完整的,没有洞)。
NAV_REGION_NAME = "Navigation"


def _nav_on(scene):
    """场景设置:这一关要不要烤导航网格(3D / 2D 都支持,第三十六轮起)。"""
    settings = getattr(scene, "gk_settings", None)
    return bool(getattr(settings, "nav_enabled", False))


def _music_bus_apply_on(scene):
    """『音乐_总线音量』卡勾了『开局应用』没有(第八十六轮)。

    也算"环境脚本有活要干"的一件事 —— 没勾时返回 False,
    老场景的导出产物里连环境脚本都不多一份(和上帝机 / 调试面板同款)。
    """
    settings = getattr(scene, "gk_settings", None)
    return bool(getattr(settings, "music_bus_apply", False))


def _gd_float(value):
    """GDScript 里的浮点写法(不是 .tscn 那套 6 位小数)。

    `@export var x: float = -6.000000` 看着很脏;整数也补一个 `.0`
    (写 `-6` 也能跑,但和这个文件的其它浮点风格不统一)。
    """
    number = float(value)
    if number == 0.0:
        number = 0.0                     # -0.0 -> 0.0
    text = f"{number:.4f}".rstrip("0").rstrip(".")
    if "." not in text:
        text += ".0"
    return text


def scene_bgm_path(scene):
    """侧栏『开局 BGM』选的**本地文件路径**(没选 / 不是音频 -> `""`)。

    ── 为什么要把"不是音频"也当没选 ────────────────────────────────
    `subtype="FILE_PATH"` 的选择器**不按扩展名过滤**,用户可能挑到一个
    .txt。与其在 Godot 里得到一句"找不到文件",不如干脆不生成 ——
    它本来就不会响。
    """
    settings = getattr(scene, "gk_settings", None)
    path = str(getattr(settings, "scene_bgm", "") or "").strip()
    if not path or not sound_assets.is_sound_file(path):
        return ""
    return path


def scene_bgm_card(scene):
    """开局 BGM 的**声音卡**(形状和『资产_声音卡』一样:`(名字, 本地路径)`)。

    导出时拿它喂 `sound_assets.copy_sounds()`,音频才会真的被复制进项目的
    `audio/` —— 环境脚本里那条 `res://audio/...` 才有东西可 load。
    (『资产_声音卡』那条路只服务**脚本**导出;场景导出不复制音频,
     所以这里单独给场景导出用。)
    """
    path = scene_bgm_path(scene)
    return ("开局BGM", path) if path else None


def _bgm_fields(scene):
    """(res:// 路径, dB) —— 环境脚本那两个字段。没选 -> ("", 默认)。"""
    settings = getattr(scene, "gk_settings", None)
    try:
        volume = float(getattr(settings, "scene_bgm_volume", -6.0))
    except (TypeError, ValueError):
        volume = -6.0
    path = scene_bgm_path(scene)
    return (sound_assets.sound_res_path(path) if path else "", volume)


def _gd_quoted(value):
    """把用户填的字符串安全地塞进 GDScript 双引号字面量。

    总线名这种东西走 `@export var x: String = "{...}"` 的模板替换 ——
    名字里带个引号 / 反斜杠就会把生成的脚本弄坏。转义 + 去掉换行,
    最坏情况只是名字看起来怪一点(总比导出的脚本编译不过强)。
    """
    text = str(value or "").replace("\\", "\\\\").replace('"', '\\"')
    return text.replace("\r", " ").replace("\n", " ")


def _music_fields(scene):
    """环境脚本里的音乐设置字段(第八十六轮『游戏音乐』图)。

    ── 默认值就是老行为 ──────────────────────────────────────────────
    循环开(true)、BGM 总线空(走 Master)、总线音量**不应用**(false)。
    所以没动过音乐设置图的 .blend,运行时行为和以前完全一致
    (产物里只多几行默认值字段,见测试 `test_defaults_are_old_behavior`)。
    """
    settings = getattr(scene, "gk_settings", None)

    def text(attr, fallback=""):
        return _gd_quoted(getattr(settings, attr, fallback))

    def number(attr, fallback):
        try:
            return float(getattr(settings, attr, fallback))
        except (TypeError, ValueError):
            return fallback

    return {
        "loop": "true" if bool(getattr(settings, "scene_bgm_loop", True))
                else "false",
        "bgm_bus": text("scene_bgm_bus"),
        "bus": text("music_bus", "Music"),
        "bus_db": _gd_float(number("music_bus_db", -6.0)),
        "apply": "true" if bool(getattr(settings, "music_bus_apply", False))
                 else "false",
    }


def _nav_settings(scene):
    """导航网格的参数(交给 .tscn 的子资源 + 环境脚本)。

    3D 用半径 / 身高 / 坡度;2D 只用半径(2D 世界没有"高度 / 坡度"这回事),
    半径单位是**场景单位**(像素世界一般 8~20,米制世界 0.3~0.8)。
    """
    settings = getattr(scene, "gk_settings", None)

    def number(attr, fallback):
        try:
            return float(getattr(settings, attr, fallback))
        except (TypeError, ValueError):
            return fallback

    return {
        "radius": max(0.05, number("nav_agent_radius", 0.5)),
        "height": max(0.1, number("nav_agent_height", 1.8)),
        "slope": max(0.0, min(89.0, number("nav_max_slope", 45.0))),
    }


# ── 多层导航(第八十五轮:寻路第三版③)──────────────────────────────
# 物体『寻路层』(1~4):不同层的角色走**不同的导航网格** —— 比如"走路的人"
# 走地面(层 1)、"飞行的人"走空中平台(层 2),互相看不见对方的路。
#
# ── 怎么做到"每层只烤自己的几何"(真 Godot spike 定的)──────────────
# Godot 的 NavigationMesh / NavigationPolygon 有
# `source_geometry_mode = GROUPS_WITH_CHILDREN`:烘焙时**只解析"在这些
# Godot group 里的节点及其子节点"**。于是每层只要:
#   * 该层的静态碰撞体节点多一行 `groups = ["gk_nav_layer_N"]`;
#   * 该层的网格子资源写 `source_geometry_mode = 1` + 组名。
# **不用挪碰撞体节点** —— 挪了会牵连材质挂载 / 动态障碍父路径 /
# 碰撞块合并一大片(所以选 group 模式)。
#
# ⚠ 一旦场景用到层 2,层 1 的物体**也要**写自己的组:不写的话层 1 的
#   网格会按"根下所有静态碰撞体"烤,把层 2 的几何一起烤进去
#   (实测:层 1 的人能走到层 2 的平台上 —— 分层就白做了)。
# ⚠ 没用到多层的老场景:一个字都不写(逐字不变)。
NAV_LAYER_MAX = 4
NAV_LAYER_GROUP_PREFIX = "gk_nav_layer_"


def _clamp_nav_layer(value):
    """层号钳到 1~4(填 0 / 负数 / 超大值都不至于把导出写坏)。

    也容忍界面上那种 `L2` / `layer2` 写法(取里面的数字)。
    """
    try:
        return max(1, min(NAV_LAYER_MAX, int(value)))
    except (TypeError, ValueError):
        pass
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    try:
        return max(1, min(NAV_LAYER_MAX, int(digits)))
    except (TypeError, ValueError):
        return 1


def nav_layer_of(physics):
    """物体『寻路层』(1~4);没设置就当作第 1 层。"""
    if physics is None:
        return 1
    return _clamp_nav_layer(getattr(physics, "nav_layer", 1))


def nav_layer_mask(layer):
    """第 n 层 -> Godot 的层掩码位(1 层 = 1、2 层 = 2、3 层 = 4、4 层 = 8)。

    就是 `1 << (n-1)`。`map_get_path` 的第 5 个参数和导航区的
    `navigation_layers` 都用这个位掩码。
    """
    return 1 << (_clamp_nav_layer(layer) - 1)


def nav_group_name(layer):
    """第 n 层的 Godot group 名(烘焙时按它挑几何)。"""
    return f"{NAV_LAYER_GROUP_PREFIX}{_clamp_nav_layer(layer)}"


def nav_group_header(physics, multi):
    """碰撞体节点**头里**要加的那段 `groups=[...]`;单层场景空串(一字不变)。

    ⚠ group 必须写在**节点头**里(`[node name=... type=... groups=["x"]]`)。
      第一版写成单独一行 `groups = [...]` —— Godot 的场景解析器会**丢掉
      整个节点**(实测:碰撞体全没了,导航烤出 0 个多边形,而且不报错)。
    """
    if not multi:
        return ""
    return f' groups=["{nav_group_name(nav_layer_of(physics))}"]'


def nav_extra_layers(scene):
    """场景里用到的**第 2 层及以后**的层号(升序)。空 = 没用多层。

    只看**网格物体**(空物体 / 相机没有碰撞体可烤);场景没勾『寻路』时
    也不存在"层"这回事(没有网格可分层)。
    """
    if scene is None or not _nav_on(scene):
        return []
    found = set()
    for obj in getattr(scene, "objects", None) or []:
        if getattr(obj, "type", "") != "MESH":
            continue
        layer = nav_layer_of(getattr(obj, "gk_physics", None))
        if layer > 1:
            found.add(layer)
    return sorted(found)


def nav_layers_active(scene):
    """这个场景用没用多层(用了才导出多块网格 / groups)。"""
    return bool(nav_extra_layers(scene))


def nav_region_names(scene):
    """[(层号, 节点名)] —— 层 1 叫 `Navigation`,层 n 叫 `Navigation_n`。

    单层场景返回 `[(1, "Navigation")]`(和以前一模一样)。
    """
    layers = nav_extra_layers(scene)
    top = max(layers) if layers else 1
    return [(layer,
             NAV_REGION_NAME if layer == 1 else f"{NAV_REGION_NAME}_{layer}")
            for layer in range(1, top + 1)]


def nav_subresource_text(layer, is_3d, params):
    """导航网格子资源文本(层 != 1 时多两行"只烤本组几何")。"""
    layer = _clamp_nav_layer(layer)
    if is_3d:
        text = NAVMESH_SUBRESOURCE.format(**params)
    else:
        text = NAVPOLY_SUBRESOURCE.format(radius=params["radius"])
    if layer == 1:
        return text
    group = nav_group_name(layer)
    if is_3d:
        text = text.replace('id="gk_navmesh"', f'id="gk_navmesh_{layer}"')
        return (text + "\ngeometry_source_geometry_mode = 1"
                f'\ngeometry_source_group_name = "{group}"')
    text = text.replace('id="gk_navpoly"', f'id="gk_navpoly_{layer}"')
    return (text + "\nsource_geometry_mode = 1"
            f'\nsource_geometry_group_name = "{group}"')


def nav_region_node_text(layer, is_3d, name):
    """导航区节点文本。层 1 不写 `navigation_layers`(默认就是 1)。"""
    layer = _clamp_nav_layer(layer)
    text = (NAVIGATION_NODE.format(name=name) if is_3d
            else NAVIGATION_NODE_2D.format(name=name))
    if layer == 1:
        return text
    old_id = "gk_navmesh" if is_3d else "gk_navpoly"
    text = text.replace(f'SubResource("{old_id}")',
                        f'SubResource("{old_id}_{layer}")')
    return text + f"\nnavigation_layers = {nav_layer_mask(layer)}"


# NavigationMesh 子资源 + 导航区节点。
#
# `geometry_parsed_geometry_type = 1` = 只解析**静态碰撞体**(见上面的说明)。
# `agent_max_climb`(能迈多高)故意留给默认 0.25 —— 和导出的台阶高度同一量级;
# `cell_size` / `cell_height` 也用默认(0.25 / 0.25),代理高度会被量化到
# 格子高度(真 Godot 里会打一条无害的提示,不影响结果)。
NAVMESH_SUBRESOURCE = """[sub_resource type="NavigationMesh" id="gk_navmesh"]
agent_radius = {radius}
agent_height = {height}
agent_max_slope = {slope}
geometry_parsed_geometry_type = 1"""

NAVIGATION_NODE = """[node name="{name}" type="NavigationRegion3D" parent="."]
navigation_mesh = SubResource("gk_navmesh")"""

# ── 2D(第三十六轮:寻路第二版)──────────────────────────────────────
# 2D 的烘焙语义和 3D **不一样**(官方文档 + 真 Godot 实测):
# 3D 里碰撞体是"实体",烘焙器自动把能站的面烤成可行走区;2D 里按
# **障碍**解析 —— 而且**必须先给一个 outline 当外框**(文档原话:
# "at least one outline needs to be added that defines the outer
# bounds of the baked area";实测不给外框时烤出来 0 个多边形)。
# 外框要按**导出后的碰撞体**算,导出侧没有那些数据 —— 所以外框在
# 运行时由环境脚本按"所有静态碰撞体的包围盒"往外扩一点生成,
# 子资源里只写参数。
NAVPOLY_SUBRESOURCE = """[sub_resource type="NavigationPolygon" id="gk_navpoly"]
agent_radius = {radius}
parsed_geometry_type = 1"""

NAVIGATION_NODE_2D = """[node name="{name}" type="NavigationRegion2D" parent="."]
navigation_polygon = SubResource("gk_navpoly")"""


# ==========================================================================
# 游戏内调试面板(用户 2026-10-05 第二十二轮:"调试体验")
# ==========================================================================
# 『运行日志』只能往控制台打一行行字;调试面板是**玩的时候能看**的一块 HUD:
# 按一个键弹出(FPS / 相机 / 脚本宿主的位置速度 / 变量实时值 / 最近打印),
# 另一个键把全部内容复制到剪贴板。
#
# 面板本体在**环境脚本**里(每张 .tscn 一份)—— 没有角色的场景也能用;
# 变量和日志由面板运行时**按名字从游戏逻辑脚本里读**:
#   * 变量:枚举宿主节点的 `PROPERTY_USAGE_SCRIPT_VARIABLE`(跳过 _gk_ 内部的),
#     所以导出侧不用准备任何"变量表","变量_设置"新建出来的变量也自动出现;
#   * 日志:宿主脚本里的 `_gk_debug_logs`(『打印_输出』在面板开着时往里推)。

# 调试面板按键 -> Godot 的按键常量名(值直接写进生成的脚本)
DEBUG_KEY_CONSTS = {
    "F1": "KEY_F1",
    "F2": "KEY_F2",
    "F3": "KEY_F3",
    "F4": "KEY_F4",
    "F8": "KEY_F8",
    "`": "KEY_QUOTELEFT",
}


def _debug_panel_on(scene):
    """场景设置:要不要在这一关生成调试面板。"""
    settings = getattr(scene, "gk_settings", None)
    return bool(getattr(settings, "debug_panel", False)) if settings else False


def _debug_key_const(scene, attr="debug_key", fallback="F1"):
    """把场景设置里的按键("F1" / "`")翻成 Godot 常量名(KEY_F1 / …)。"""
    settings = getattr(scene, "gk_settings", None)
    raw = str(getattr(settings, attr, fallback) or fallback) if settings else fallback
    return DEBUG_KEY_CONSTS.get(raw, DEBUG_KEY_CONSTS[fallback])


def _debug_watch_text(scene):
    """调试面板显示哪些变量(竖线分隔)。空 = 全都显示。

    ── 为什么查"这张场景选的那张逻辑图"────────────────────────────
    调试面板是**每个场景一张**(环境脚本),而变量属于**这张场景用的逻辑图**
    (场景设置里的 logic_tree)—— 查错图的话面板上会列出别的关卡的变量。

    ── 收哪三类(都收,不然就是漏)────────────────────────────────
    * 『变量』框里声明过、勾了『监视』的(没勾的不上 —— 那是用户的"别看它");
    * 图里**实际用到**的变量名(『变量_设置/读取』填的那些)—— 没声明也能用,
      面板不列它就是漏;
    * 对外端口 `输出_xxx`(别的图读它,调试时最想看)。
    三类都没有(空图 / 没绑图)才返回空串 —— 那时面板显示**所有**成员变量,
    连 @onready 引用一起显示(只在"什么都没有"的兜底路径上,平时不会)。
    """
    try:
        from . import exporter as _exporter

        tree, _why = _exporter.resolve_scene_tree(scene)
    except Exception:                                     # noqa: BLE001
        tree = None
    if tree is None:
        return ""

    names = []
    for var in (getattr(tree, "gk_vars", None) or ()):
        if not bool(getattr(var, "watch", True)):
            continue
        names.append(str(getattr(var, "var_name", "") or ""))
    try:
        from . import properties as _properties

        names.extend(name for name, _sets, _gets
                     in _properties.variable_references(tree))
    except Exception:                                     # noqa: BLE001
        pass
    for port in (getattr(tree, "gk_ports", None) or ()):
        if str(getattr(port, "direction", "out")) == "out":
            names.append("输出_" + str(getattr(port, "port_name", "") or ""))

    cleaned = []
    for name in names:
        name = str(name).strip()
        # 值会写进 GDScript 的字符串字面量:引号和分隔符必须去掉
        name = name.replace('"', "").replace("|", "")
        if name and name not in cleaned:
            cleaned.append(name)
    return "|".join(cleaned)


def _env_fields(scene, camera_name="", fov=0.0, material_files=None,
                is_3d=True):
    """环境脚本模板的填充值(两处分支共用一份)。

    ── 为什么要抽出来(踩过的坑)──────────────────────────────────
    `build_environment_nodes` 里有**两条**分支各自拼 fields,以前是各写一份 ——
    加字段时只改一边,另一条分支就会 NameError / 少字段(这次加调试面板时
    第一版就是这么撞的)。现在两边都调这个函数,加字段只改一处。
    """
    bgm_path, bgm_volume = _bgm_fields(scene)
    dimension = "3" if is_3d else "2"
    music = _music_fields(scene)
    return dict(
        camera_name=camera_name,
        fov=_fmt(fov),
        bgm_path=bgm_path,
        bgm_volume=_gd_float(bgm_volume),
        # 音乐设置(第八十六轮『游戏音乐』图):默认值 = 老行为 ——
        # 循环开、总线空(走 Master)、总线音量不应用。
        bgm_loop=music["loop"],
        bgm_bus=music["bgm_bus"],
        music_bus=music["bus"],
        music_bus_db=music["bus_db"],
        music_bus_apply=music["apply"],
        materials=_material_table(material_files),
        follow_scene_light="true" if _toon_follow_light(scene) else "false",
        god_camera="true" if _god_camera_on(scene) else "false",
        debug_panel="true" if _debug_panel_on(scene) else "false",
        debug_watch=_debug_watch_text(scene),
        debug_key=_debug_key_const(scene, "debug_key", "F1"),
        debug_copy_key=_debug_key_const(scene, "debug_copy_key", "F2"),
        # 时停键(第三十五轮):先给个常量 —— 和调试面板那两个键同一个
        # 套路,以后想变成场景设置只要在这儿读 settings 就行。
        time_stop_key="KEY_T",
        # 附身键(第三十九轮):同样是常量,想改就改这儿。
        possess_key="KEY_F",
        # ESC 退出(第三十九轮):**图里没占着 Esc 才生效** ——
        # 暂停菜单 / 用户自己接的 Esc 都算"有人管了"(见 _esc_claimed_by_graph)。
        esc_quit="false" if _esc_claimed_by_graph(scene) else "true",
        nav_bake="true" if _nav_on(scene) else "false",
        # 代价区(第八十四轮):物体『通行代价』!= 1 的清单 —— 环境脚本
        # 烘焙时把它们挖成洞、各自补一块设 travel_cost(挖洞+补洞见模板)
        nav_costs=nav_costs_literal(nav_cost_areas(scene), dimension),
        # 手电筒(第四十八轮):勾了『跟随相机』的灯名,运行时贴着相机走
        # (见环境脚本里的 _gk_follow_lights_tick)。
        follow_lights=_follow_light_names(_collect_lights(scene)),
    )


def _force_hidden(scene):
    """这个场景要不要"强制导出隐藏物体"?

    -- 为什么这个开关没做进界面(实测后放弃)----------------------
    本来打算加个 `force_hidden_objects` 设置。但在用户的文件里实测发现:
    那个 .blend 存下来的 `gk_settings` 属于**另一个包路径**的类
    (`bl_ext.gk_test...` vs 当前加载的 `bl_ext.user_default...`),
    新加的属性**根本没挂上去**,读它永远是默认值。

    这种"属性没挂上"的坑排查成本很高,而收益只是省掉用户点两下鼠标。
    所以**不做界面开关** —— 改成:检测到隐藏骨架就明确告警,
    告诉用户"把骨架显示出来再导出"。

    这个函数留着当入口,以后真要做得有个地方读(读不到就 False)。
    """
    settings = getattr(scene, "gk_settings", None)
    try:
        return bool(getattr(settings, "force_hidden_objects", False))
    except Exception:
        return False


def _hidden_armature_warning(names):
    """隐藏骨架导致蒙皮/骨骼动画丢失时的告警文本。"""
    if not names:
        return ""
    shown = "、".join(names[:3])
    if len(names) > 3:
        shown += f" 等 {len(names)} 个"
    return (f"骨架『{shown}』在视口里是隐藏的,而 Blender 的 glTF 导出器"
            f"**会跳过隐藏的物体** —— 导出的模型将没有蒙皮,骨骼动画也播不了"
            f"(形态键动画不受影响,所以症状通常是"
            f"『形态键能播、骨骼播不了』)。"
            f"解决办法:把骨架显示出来再导出,"
            f"或者在『游戏逻辑』面板的导出设置里勾上『强制导出隐藏物体』")


def collect_objects(context):
    """要导出的对象:优先用选中的，没选就全部可见网格。

    **排除辅助物理体**(MMD 那种一骨头一个的刚体小球)——
    它们既不该当碰撞体也不该当几何。不排除的话用户的胡桃模型会多出
    848 个 34 顶点的小方块,godot 里一片蓝框,glb 也白白大一圈。
    """
    selected = [obj for obj in context.selected_objects
                if obj.type == "MESH" and not is_helper_physics(obj)]
    if selected:
        return selected, "selected"
    visible = [obj for obj in context.scene.objects
               if obj.type == "MESH" and not obj.hide_get()
               and not is_helper_physics(obj)]
    return visible, "visible"


def object_bounds(objects, local=False):
    """网格包围盒。

    ── local=False(默认):**世界空间** AABB ────────────────────────
    线框预览、诊断这些"要贴着世界看"的地方用它。

    ── local=True:**物体自己局部轴**里的包围盒 ────────────────────
    碰撞形状必须用它(第二十八轮修):形状是"挂在物体局部坐标系里的
    一个盒子",朝向由节点的 transform 带过去。以前一律用世界 AABB ——
    转过角度的物体形状会**大一圈**:45° 方块的碰撞体是 2.83 而不是 2
    (斜对角多出 0.8 米)、20° 斜坡的形状变成一块 2.7 米高的竖板把
    角色挡在坡脚。用户看到的就是"看得见的和判定的对不上"。
    """
    import mathutils

    mins = mathutils.Vector((1e9, 1e9, 1e9))
    maxs = mathutils.Vector((-1e9, -1e9, -1e9))
    found = False
    for obj in objects:
        for corner in obj.bound_box:
            if local:
                # 局部包围盒的角点本身就是局部坐标;物体自己的旋转由
                # 节点的 transform 负责(不再在这里"预旋转")。
                world = mathutils.Vector(corner)
            else:
                world = obj.matrix_world @ mathutils.Vector(corner)
            for axis in range(3):
                mins[axis] = min(mins[axis], world[axis])
                maxs[axis] = max(maxs[axis], world[axis])
            found = True
    if not found:
        return None
    size = maxs - mins
    center = (maxs + mins) * 0.5
    return {"min": tuple(mins), "max": tuple(maxs), "size": tuple(size), "center": tuple(center)}


def export_glb(export_dir, name, use_selection, objects):
    """调用 Blender 的 glTF 导出。返回 (路径, 是否成功, 消息)。"""
    import bpy

    # 模型统一放 art/,和贴图在一起
    target_dir = os.path.join(export_dir, project.ART_DIR)
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, f"{name}.glb")
    kwargs = {
        "filepath": path,
        "export_format": "GLB",
        "use_selection": bool(use_selection),
        "export_apply": True,
        "export_yup": True,
        "export_materials": "EXPORT",
        "export_animations": True,
        "export_skins": True,
        "export_texcoords": True,
        "export_normals": True,
    }
    return _run_gltf_export(kwargs, path)


def export_scene_glb(export_dir, name="World", scene=None, exclude=None):
    """把**整个 Blender 场景**导出成一个 .glb(保持层级与变换)。

    返回 (路径, 是否成功, 消息)。

    ── 为什么需要单独做这个 ────────────────────────────────────────
    之前的导出是"把选中的网格导出成一个模型",适合角色。
    但要"打开 Godot 时看到的场景和 Blender 里一样",就必须导出**整个场景的
    层级结构**:空物体、父子关系、每件东西的位置。

    glTF 本身就支持层级,Blender 的导出器会把:
        * 对象父子关系  -> 节点的父子关系
        * 对象变换      -> 节点变换(并且自动做 Z-up -> Y-up 转换)
        * 灯光 / 相机   -> 灯光节点 / 相机节点
    都带过去。所以这一个文件就能让 Godot 里的场景和 Blender 一致。

    ── 预览物体必须手动排除 ────────────────────────────────────────
    碰撞线框预览设了 hide_render,**但 glTF 导出器不看这个** ——
    实测三个 `__GK碰撞预览__` 物体全都进了 .glb。
    所以这里在导出前显式把它们取消选中(导出器会跳过未选中对象),
    导出后再恢复原来的选中状态。
    """
    import bpy

    scene = scene or bpy.context.scene
    if scene is None:
        return None, False, "没有场景"

    # 记住原来的选中状态,导出后恢复 —— 不要打乱用户的选择
    previous = [obj for obj in scene.objects if obj.select_get()]

    # ── 要排除的物体(角色)────────────────────────────────────────────
    # 角色的网格会单独导成一个 glb,再 instance 到角色节点下面。
    # 如果 World.glb 里**也留着一份**,两份网格就会重叠在同一个位置 ——
    # 深度冲突,画面表现是"模型一闪一闪的"(用户实测反馈)。
    #
    # exclude 里的物体**连同它们的子级**一起排除:角色的子物体已经被
    # 角色 glb 带走了,这里再留一份同样会重叠。
    excluded = set()
    stack = [obj for obj in (exclude or ()) if obj is not None]
    while stack:
        obj = stack.pop()
        if obj in excluded:
            continue
        excluded.add(obj)
        stack.extend(obj.children)

    skipped = []
    # ── 隐藏的骨架会被导出器跳过 ────────────────────────────────────
    # 见 export_subtree_glb 里的详细说明。这里只做两件事:
    # 检测 + 告警;用户明确勾了"强制导出隐藏物体"才临时取消隐藏。
    force = _force_hidden(scene)
    restore_hidden = []
    hidden_armatures = []
    skipped = []
    for obj in scene.objects:
        # ── 预览物体:按**标记**和**名字前缀**双重排除(用户 2026-10-03)──
        # 用户导出后的 Godot 场景里出现了 `__GK碰撞预览__示例角色` ——
        # 说明只认标记是不够的:老 .blend 里存的预览物体是**早期版本**建的,
        # 那时候还没写这个标记,于是它一路进了 .glb(连带一条碰撞体)。
        # 名字前缀是这套预览物体固有的,加上它就堵住了。
        is_preview = bool(obj.get(PREVIEW_MARK)) or \
            str(getattr(obj, "name", "")).startswith("__GK碰撞预览__")
        # 子物体碰撞块是**纯辅助物**:网格不进 glb(它在游戏里只是
        # "看不见的碰撞形状",由宿主的碰撞节点代它出现)。
        if is_preview or obj in excluded or is_collision_piece(obj):
            skipped.append(obj.name)
            obj.select_set(False)
        else:
            is_hidden = False
            try:
                is_hidden = obj.hide_get()
            except Exception:
                pass
            if is_hidden and getattr(obj, "type", "") == "ARMATURE":
                hidden_armatures.append(obj.name)
            if is_hidden and force:
                try:
                    obj.hide_set(False)
                    restore_hidden.append(obj)
                except Exception:
                    pass
            obj.select_set(True)

    target_dir = os.path.join(export_dir, project.ART_DIR)
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, f"{name}.glb")

    kwargs = {
        "filepath": path,
        "export_format": "GLB",
        "use_selection": True,      # 配合上面的取消选中来排除预览物体
        "export_apply": False,
        "export_yup": True,
        "export_materials": "EXPORT",
        "export_animations": True,
        "export_skins": True,
        "export_texcoords": True,
        "export_normals": True,
        "export_cameras": True,
        # ── 不导出灯光(踩过的坑)──────────────────────────────────────
        # Blender 4.5 的 glTF 导出器在**导出太阳灯时会自己崩**:
        #     TypeError: SunLight.area(): was called with invalid keyword
        #     argument(s) (world_matrix), expected (matrix_world)
        # 那是它内部的 API 调用写错了(`area()` 的参数名从 world_matrix
        # 改成了 matrix_world),和我们的参数无关 —— 只要场景里有 Sun
        # 就必崩、整场景导出直接失败。
        #
        # 关掉之后,灯光改由 `build_environment_nodes()` 生成成
        # .tscn 里的 DirectionalLight3D —— 那条路本来就实现了,而且更可控
        # (能按 Blender 的强度算 energy)。实测:导出的场景灯光效果一样。
        "export_lights": False,
        "export_extras": True,
    }
    # ── 只导出**当前这个场景**(第八十七轮:多场景时串场景)────────────
    # 不传这个开关时,导出器会把 **.blend 里所有场景**都算进来,
    # 而 `use_selection` 只筛"选中的物体" —— 别的场景里选中的东西会
    # **混进这个 glb**(实测:『关卡预设』建了三关,3D 关的 glb 里
    # 混进了 2.5D 关的地面和角色)。传 True 就把范围收在活动场景里;
    # 老版本 Blender 没有这个参数时 `_run_gltf_export` 会自动降级重试。
    try:
        if bpy.context.scene == scene:
            kwargs["use_active_scene"] = True
    except Exception:                                     # noqa: BLE001
        pass
    result = _run_gltf_export(kwargs, path)

    # 隐藏骨架 -> 明确告警(同 export_subtree_glb)
    note = _hidden_armature_warning(hidden_armatures)
    if note:
        notes = getattr(_run_gltf_export, "_notes", None)
        if notes is None:
            notes = []
            _run_gltf_export._notes = notes
        notes.append(note)

    # 恢复选中状态
    try:
        for obj in scene.objects:
            obj.select_set(obj in previous)
    except Exception:
        pass
    # 把临时取消的隐藏恢复回去(见上面 restore_hidden 的说明)
    for obj in restore_hidden:
        try:
            obj.hide_set(True)
        except Exception:
            pass

    if result[1] and skipped:
        return result[0], True, (f"已导出 {os.path.basename(path)}"
                                 f"（已排除 {len(skipped)} 个碰撞预览）")
    return result


def _run_gltf_export(kwargs, path):
    """跑 glTF 导出,参数名不兼容时逐个降级重试。"""
    import bpy

    working = dict(kwargs)
    for _ in range(12):
        try:
            bpy.ops.export_scene.gltf(**working)
            if os.path.isfile(path):
                return path, True, f"已导出 {os.path.basename(path)}"
            return path, False, "导出命令执行了，但文件没生成"
        except TypeError as exc:
            message = str(exc)
            dropped = None
            for key in list(working):
                if key in message and key != "filepath":
                    dropped = key
                    break
            if dropped is None:
                return path, False, f"导出参数不被支持: {message}"
            working.pop(dropped, None)
        except Exception as exc:
            # ── 导出器自己崩了:试着把可疑的开关关掉再来一次 ────────────
            # 实测踩过:Blender 4.5 的 glTF 导出器导出太阳灯时内部报
            #     SunLight.area(): invalid keyword argument 'world_matrix'
            # 那是**它自己的 bug**,和我们的参数无关,但会让整场景导出
            # 直接失败(用户看到"模型变白/没材质",其实是导出压根没成功)。
            #
            # 这里做一层兜底:报错信息里提到灯光/相机就把对应的开关关掉重试,
            # 这样下次再遇到同类导出器 bug 也不至于整个流程挂掉。
            message = str(exc)
            lowered = message.lower()
            dropped = None
            if working.get("export_lights") and ("light" in lowered
                                                 or "lamp" in lowered
                                                 or "sunlight" in lowered):
                dropped = "export_lights"
            elif working.get("export_cameras") and "camera" in lowered:
                dropped = "export_cameras"
            elif working.get("export_animations") and "anim" in lowered:
                dropped = "export_animations"
            if dropped is None:
                return path, False, f"导出失败: {message}"
            working.pop(dropped, None)
            notes = getattr(_run_gltf_export, "_notes", None)
            if notes is None:
                notes = []
                _run_gltf_export._notes = notes
            notes.append(f"导出器在 {dropped} 上报错,已关掉该项重试")
    return path, False, "重试次数用尽"


# 脚下响度 metadata 的 key —— codegen 里 get_meta 用的必须和这里**逐字一致**。
SURFACE_LOUDNESS_META = "gk_surface_loudness"


def surface_loudness_of(physics):
    """物体『地面响度倍率』该写成多少;**1.0(默认)返回 None = 不写元数据**。

    绝大多数地面都是普通地面 —— 每个碰撞体都写一行 metadata 纯属噪声,
    所以只在倍率 != 1.0 时导出。0.0(地毯 = 完全静音)是有效值,必须保留,
    不能用 falsy 判断。
    """
    if physics is None:
        return None
    raw = getattr(physics, "surface_loudness", None)
    if raw is None:
        return None
    value = float(raw)
    if abs(value - 1.0) < 1e-6:
        return None
    return value


# ── 动态障碍(第八十三轮:寻路第三版)──────────────────────────────
# 勾了『动态障碍』的物体,导出时多挂一个 NavigationObstacle2D / 3D 子节点。
# 它的半径交给**避障系统**(RVO):勾了『避障』的角色会实时绕开它,
# **不用重烤导航网格** —— 这就是"会动的挡路物"的正确做法
# (重烤是整张网重算,每帧挪一点的柱子扛不住)。
NAV_OBSTACLE_NODE = "NavObstacle"


def nav_obstacle_radius_of(physics):
    """物体『动态障碍』的半径;没勾返回 None(不挂节点)。

    半径钳到 ≥ 0.05:填 0 / 负数时 Godot 那边会把它当成"没有障碍",
    静默不生效 —— 这种"看起来勾了却没用"最难查,所以这里兜住。
    """
    if physics is None:
        return None
    if not getattr(physics, "nav_obstacle", False):
        return None
    try:
        radius = float(getattr(physics, "nav_obstacle_radius", 1.0))
    except (TypeError, ValueError):
        radius = 1.0
    return max(0.05, radius)


def nav_obstacle_node_lines(physics, is_3d, parent_name):
    """动态障碍在 .tscn 里的那几行(没勾 = 空列表,老图一字不变)。"""
    radius = nav_obstacle_radius_of(physics)
    if radius is None:
        return []
    node_type = "NavigationObstacle3D" if is_3d else "NavigationObstacle2D"
    return [
        f'[node name="{NAV_OBSTACLE_NODE}" type="{node_type}" '
        f'parent="Colliders/{parent_name}"]',
        f"radius = {_fmt(radius)}",
        "",
    ]


# ── 区域代价(第八十四轮:寻路第三版②)────────────────────────────────
# 物体『通行代价』填 != 1 = 它盖住的那片地是"代价区"(泥地 / 危险区
# 填 > 1 会绕开;< 1 是近路)。导出侧只做两件事:
#   1. 把代价区的**世界包围盒**写进环境脚本的 `nav_costs`;
#   2. 其余交给环境脚本 —— 烘焙时把代价区当障碍**挖洞**、再单独补一块
#      设 `travel_cost`。
# 为什么不能直接叠一块 region 在主网格上(真 Godot spike 实测):
# **重叠区域的代价会被无视**,路径照直穿;必须拼边(挖洞 + 补洞)。
def nav_travel_cost_of(physics):
    """物体『通行代价』;没填 / 1.0(普通地面)返回 None(不导出)。

    0 / 负数钳到 0.05:代价 0 会让 A* 的代价计算失去意义(路径能
    "免费"在泥地里绕),Godot 那边也不保证处理 —— 导出侧就兜住。
    """
    if physics is None:
        return None
    raw = getattr(physics, "nav_travel_cost", None)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if abs(value - 1.0) < 1e-6:
        return None
    return max(0.05, value)


def nav_cost_areas(scene):
    """场景里所有『代价区』-> [{name, cost, min, size}](世界 AABB)。

    * 只在场景勾了『寻路』时才有意义(没网格可挖,代价区无从谈起);
    * 只认**网格物体**(空物体 / 相机没有面积可挖);
    * `min` / `size` 是 3 轴元组 —— 2D 场景只用 XY,3D 用 XYZ
      (由 `nav_costs_literal` 按维度挑)。
    """
    if scene is None or not _nav_on(scene):
        return []
    out = []
    for obj in scene.objects:
        if getattr(obj, "type", "") != "MESH":
            continue
        cost = nav_travel_cost_of(getattr(obj, "gk_physics", None))
        if cost is None:
            continue
        bounds = object_bounds([obj])
        if bounds is None:
            continue
        out.append({
            "name": obj.name,
            "cost": cost,
            "min": bounds["min"],
            "size": bounds["size"],
        })
    return out


def nav_costs_literal(areas, dimension="3"):
    """代价区清单 -> 环境脚本里的 GDScript 数组字面量(空 -> `[]`)。

    2D(俯视):`rect` 用 XY 平面的矩形(Z 不参与 —— 俯视 2D 没有高度);
    3D:`box` 用世界 AABB(XYZ 都参与,环境脚本再按它挖柱形洞)。
    """
    if not areas:
        return "[]"
    import json

    parts = []
    for area in areas:
        name = json.dumps(str(area.get("name", "")), ensure_ascii=False)
        cost = _gd_float(area.get("cost", 1.0))
        mins = tuple(area.get("min", (0.0, 0.0, 0.0)))
        size = tuple(area.get("size", (0.0, 0.0, 0.0)))
        if str(dimension) == "2":
            rect = (f"Rect2({_gd_float(mins[0])}, {_gd_float(mins[1])}, "
                    f"{_gd_float(size[0])}, {_gd_float(size[1])})")
            parts.append(f'{{"name": {name}, "cost": {cost}, "rect": {rect}}}')
        else:
            box = (f"AABB(Vector3({_gd_float(mins[0])}, {_gd_float(mins[1])}, "
                   f"{_gd_float(mins[2])}), "
                   f"Vector3({_gd_float(size[0])}, {_gd_float(size[1])}, "
                   f"{_gd_float(size[2])}))")
            parts.append(f'{{"name": {name}, "cost": {cost}, "box": {box}}}')
    return "[\n\t" + ",\n\t".join(parts) + ",\n]"


def write_world_scene(export_dir, scene_name="World", scene=None,
                      dimension="3", script_name=None, script_target_name=None,
                      materials_map=None, character_model=None, file_names=None,
                      character_object=None, asset_models=None):
    """生成主场景:模型 + 卡通材质 + 碰撞体 + 相机灯光。返回 (路径, 碰撞体数量)。

    结构(3D):

        Ball      [MeshInstance3D]   mesh = glb 里的 BallMesh
                                     material_override = materials/Ballmat.tres
        Floor     [MeshInstance3D]
        ShotCam   [Camera3D]
        KeySun    [DirectionalLight3D]
        Colliders [Node3D]
          Ball    [StaticBody3D]
            Collision [CollisionShape3D]

    ── 材质怎么挂(这条路踩了很久)────────────────────────────────────
    试过三种写法,只有第三种能工作:

    1) 给 glb 实例内部的子节点写 material_override:
           [node name="World" instance=ExtResource("1_world")]
           [node name="Ball" parent="World"]
           material_override = ExtResource("mat_2")
       **不生效**。Godot 报
           WARNING: Parent path './World' for node 'Ball' has vanished
       覆盖被丢弃,用户看到的永远是白模。加 editable_children = true 也没用。

    2) 把网格展开成 MeshInstance3D,`mesh = ExtResource("mesh_1")`:
       **不生效**。运行时 `mi.mesh` 是 `<Object#null>` ——
       glb 的网格没有按 `文件.glb/网格名` 暴露成可引用资源,画不出东西,
       画面一片纯灰。

    3) **instance 整个 glb + 运行时用脚本挂材质** ← 现在用的
       .tscn 里只 instance glb(几何/层级/变换一定对),
       材质映射交给 Environment 脚本在 `_ready` 里挂。
       实测 StandardMaterial3D 和 ShaderMaterial 都能成功显示。

    materials_map: {对象名: (材质资源名, materials/ 下的文件名)}

    character_model: 角色自己的 glb 名(不含 .glb)。给了的话,脚本挂载目标
        会在场景里生成一个**自带网格**的节点:

            Hero [CharacterBody3D]        ← 脚本挂这里;移动它
              Collision [CollisionShape3D]
              Model [instance Hero.glb]   ← 角色网格,跟着一起动

        ── 为什么必须这样(用户实测反馈)────────────────────────────
        以前只有 `Colliders/Cube [CharacterBody3D]` + 一个碰撞体,
        角色的**网格在世界 glb 实例里**(另一棵子树)。移动 Colliders/Cube
        只动了碰撞体,画面里的角色纹丝不动 ——
        用户原话:"摄像机动了但那个角色正方体没跟着动"。
        而 Godot **不允许**往 glb 实例内部挂节点,所以没法把碰撞体
        挂到 `World/Cube` 下面去。

        正确做法就是反过来:让角色节点自带一份自己的网格。
        实测验证过:移动根节点 -> 网格世界坐标跟着变(位移 6.0),
        地面保持不动。
    """
    path = os.path.join(export_dir, project.SCENES_DIR, f"{scene_name}.tscn")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    is_3d = str(dimension) != "2"

    info = glb_scene_nodes(export_dir, scene_name)
    mesh_info = info.get("meshes", {})
    if not mesh_info:
        return None, 0

    # glb 实例本身
    ext_lines = [
        f'[ext_resource type="PackedScene" '
        f'path="res://{project.ART_DIR}/{scene_name}.glb" id="1_world"]'
    ]

    use_script = bool(script_name and script_target_name)
    script_id = None
    if use_script:
        script_id = "gk_script"
        ext_lines.append(f'[ext_resource type="Script" '
                         f'path="res://{project.SCRIPTS_DIR}/{script_name}" '
                         f'id="{script_id}"]')

    # 角色自己的 glb(让角色节点自带网格,见上面 character_model 的说明)
    use_character_model = bool(character_model) and os.path.isfile(
        os.path.join(export_dir, project.ART_DIR, f"{character_model}.glb"))
    if use_character_model:
        ext_lines.append(f'[ext_resource type="PackedScene" '
                         f'path="res://{project.ART_DIR}/{character_model}.glb" '
                         f'id="1_character"]')

    # ── 静态资产的 glb(用户 2026-10-05)─────────────────────────────
    # 每份资产一个 glb(id = `asset_序号`),本体和它的实例都通过
    # `Model` 子节点引用它 —— 一个模型导一次,场景里摆多份。
    asset_models = dict(asset_models or {})
    asset_ids = {}
    for index, (asset_key, model_name) in enumerate(sorted(asset_models.items())):
        glb = os.path.join(export_dir, project.ART_DIR, f"{model_name}.glb")
        if not os.path.isfile(glb):
            continue
        res_id = f"asset_{index}"
        asset_ids[asset_key] = res_id
        ext_lines.append(f'[ext_resource type="PackedScene" '
                         f'path="res://{project.ART_DIR}/{model_name}.glb" '
                         f'id="{res_id}"]')

    # ---- 碰撞体 ----
    bodies = []
    sub_resources = []
    if scene is not None:
        for index, (obj, physics, enabled) in enumerate(collect_physics(scene)):
            if not enabled or getattr(obj, "type", "") != "MESH":
                continue
            # 子物体碰撞块不单独生成碰撞体 —— 形状合并进宿主(下面收集),
            # 块本身也**不进 glb**(是纯辅助物,不该出现在画面里)。
            if is_collision_piece(obj):
                continue
            built = build_collision(obj, is_3d, physics)
            if built is None:
                continue
            shape_id = f"shape_{index}"
            sub_resources.append(
                built["sub_resource"].replace('id="shape"', f'id="{shape_id}"'))
            # ── 子物体碰撞块:"头/身/腿各摆一个"──────────────────────
            # 每块一份形状(走它自己的『游戏物理』设置)+ 一个相对宿主
            # 原点的偏移;导出后它们都是宿主节点下的 Collision 子节点。
            piece_entries = []
            for order, piece in enumerate(collision_pieces(obj), start=1):
                piece_built = build_collision(
                    piece, is_3d, getattr(piece, "gk_physics", None))
                if piece_built is None:
                    continue
                piece_shape_id = f"shape_{index}_{order}"
                sub_resources.append(piece_built["sub_resource"].replace(
                    'id="shape"', f'id="{piece_shape_id}"'))
                piece_entries.append(
                    (piece, piece_built, piece_shape_id,
                     _piece_offset_godot(piece, obj, is_3d)))
            bodies.append((obj, physics, built, shape_id,
                           _godot_position(obj, is_3d), piece_entries))

    # ---- 粒子发射器(需求 5,2026-10-05 第二十一轮)--------------------
    # 网格 / 参数在 Blender 侧,粒子**节点**在导出时生成(见文件上方那一节)。
    # 落位规则:
    #   * 这个物体在 Colliders 下有物理体 → 粒子挂在**它**下面:
    #     物体的游戏逻辑一移动,粒子就跟着走(角色身上的火花要这个);
    #   * 其余 → 统一挂在根下的 `Particles` 容器里(位置 = 物体世界坐标)。
    # 贴图走现有链路:物体材质里有基础色贴图就用它,没有就用内置的
    # 圆形柔光(导出时现写一张 art/fx_soft.png)。
    #
    # ⚠ ext_lines / sub_resources 必须在下面 `load_steps` 之前填完 ——
    # 那两行之后它们就被写进文件了(节点文本可以慢慢加,资源不行)。
    fx_blocks = []
    fx_needs_container = False
    emitter_entries = collect_emitters(scene) if scene is not None else []
    body_node_names = {_sanitize_node_name(entry[0].name) for entry in bodies}
    for index, (emitter, fx) in enumerate(emitter_entries):
        node_name = _sanitize_node_name(emitter.name)
        texture_id = f"fx_tex{index}"
        attach = node_name in body_node_names
        parent = f"Colliders/{node_name}" if attach else "Particles"
        if not attach:
            fx_needs_container = True
        built = build_emitter(emitter, fx, is_3d, uid=index,
                              texture_id=texture_id, parent=parent,
                              attach_to_body=attach)
        ext_lines.append(
            f'[ext_resource type="Texture2D" '
            f'path="res://{project.ART_DIR}/'
            f'{fx_texture_file(emitter, export_dir)}" '
            f'id="{texture_id}"]')
        sub_resources.extend(built["sub_resources"])
        fx_blocks.append(built["node_lines"])

    # ---- 相机 / 灯光 / Environment ----
    # (见上面"材质怎么挂"里的说明:相机和灯由 glb 带进来,
    #  Environment 脚本负责设主相机、修灯光亮度、挂材质)
    glb_info = glb_object_names(export_dir, scene_name)
    material_files = {}
    for obj_name in sorted(mesh_info):
        entry = (materials_map or {}).get(obj_name)
        if entry:
            material_files[obj_name] = entry[1]

    environment, env_files, _camera_name = build_environment_nodes(
        scene, is_3d, skip=glb_info, scene_name=scene_name,
        material_files=material_files)
    if env_files:
        ext_lines.append(f'[ext_resource type="Script" '
                         f'path="{env_script_res(scene_name)}" '
                         f'id="env_script"]')

    # ---- 导航网格(第三十四轮;第三十六轮加 2D)----
    # 只是把参数写进子资源;真正"烤"的动作在环境脚本启动时做
    # (网格要按**导出的碰撞体**烤,导出侧烤不了 —— 见 _nav_on 的说明)。
    nav = _nav_settings(scene) if _nav_on(scene) else None
    if nav is not None:
        # 多层导航(第八十五轮):一层一份网格子资源(层 1 = `gk_navmesh`,
        # 层 n = `gk_navmesh_n`)—— 层 != 1 的那份多两行"只烤本组几何"。
        # 单层场景就只有层 1 那份,文本和以前**逐字一样**。
        for layer, _name in nav_region_names(scene):
            sub_resources.append(nav_subresource_text(layer, is_3d, nav))

    # ---- 环境与后处理(第四十九轮:阶段②)----
    # WorldEnvironment 只挂在**这张要走的主场景**上:角色子场景是被
    # World 实例化的,它再挂一个 WorldEnvironment 会和主场景的打架
    # (同一个视口只有一个环境生效)。子资源必须**在 load_steps 之前**
    # 加进来,节点文本拼到场景末尾。
    env_blocks, env_node = environment_scene_blocks(scene)
    sub_resources.extend(env_blocks)

    load_steps = 1 + len(ext_lines) + len(sub_resources)

    lines = ["; 由 BlenderGameKit 自动生成,请勿手动修改",
             f"[gd_scene load_steps={load_steps} format=3]", ""]
    lines.extend(ext_lines)
    lines.append("")
    for block in sub_resources:
        lines.append(block)
        lines.append("")

    # 注意:环境脚本**不在这里落盘** —— 上面这一次 build_environment_nodes
    # 只传了 mesh_info 的材质(还没有逐表面 / 角色那份),它生成的脚本
    # `materials` 默认值是**残缺的**。节点属性用的是**后面那次**(完整)调用的
    # 结果,所以脚本必须等那次之后再写,否则脚本默认值和节点属性不一致 ——
    # 这正是 ②a 要消灭的"只靠节点属性"。

    # ---- 根节点下的 glb 实例 ----
    #
    # ── 为什么不把网格展开成一个个 MeshInstance3D ────────────────────
    # 试过那种做法(`mesh = ExtResource("mesh_1")`,指向 glb 里的网格),
    # **Godot 拿不到那个资源** —— 运行时 `mi.mesh` 是 `<Object#null>`,
    # 于是什么都画不出来,画面一片纯灰。
    # glb 的网格没有按 `文件.glb/网格名` 这种形式暴露成可引用的资源。
    #
    # 所以还是 instance 整个 glb:几何、层级、变换都由它负责,一定是对的。
    lines.append(f'[node name="{scene_name}" '
                 f'instance=ExtResource("1_world")]')
    lines.append("")

    # ── 材质:交给运行时脚本挂 ──────────────────────────────────────
    # 不能在 .tscn 里给实例内部的子节点写 material_override ——
    # Godot 报 `Parent path './World' for node 'Ball' has vanished`,
    # 覆盖不生效,用户看到的永远是白模。加 editable_children 也没用。
    #
    # 但**运行时**挂是生效的(实测:StandardMaterial3D 和 ShaderMaterial
    # 都成功显示)。所以把映射表交给 Environment 脚本去挂。
    #
    # ── 按表面还是要整网格?(踩过的坑)────────────────────────────────
    # MMD 那种"一个网格 33 个材质槽"的模型,整网格覆盖会把所有表面改成
    # 同一个材质,看起来"变白 / 只有一种颜色"。所以材质槽多于一个时,
    # 改用 `节点名#序号` 逐表面覆盖(见 surface_material_files)。
    material_files = {}
    blender_objects = [obj for obj in (scene.objects if scene else ())
                       if getattr(obj, "type", "") == "MESH"
                       and obj.name in mesh_info]
    for obj in blender_objects:
        per_surface = surface_material_files(obj, file_names)
        if per_surface:
            material_files.update(per_surface)
        else:
            entry = (materials_map or {}).get(obj.name)
            if entry:
                material_files[_sanitize_node_name(obj.name)] = entry[1]

    # 角色自己的 glb 是**单独**一个实例,它的网格不在 mesh_info 里,
    # 所以要额外把角色的材质也列进映射表 —— 否则角色的卡通材质挂不上,
    # 只剩 glb 自带的默认材质(用户看到的还是"角色材质没生效")。
    if use_character_model and character_object is not None:
        per_surface = surface_material_files(character_object, file_names)
        if per_surface:
            material_files.update(per_surface)
        else:
            char_meshes = glb_scene_nodes(export_dir,
                                          character_model).get("meshes", {})
            for node_name in sorted(char_meshes):
                entry = (materials_map or {}).get(node_name)
                if entry:
                    material_files[node_name] = entry[1]

    # ── 静态资产的材质(用户 2026-10-05;实测漏过一次,别再删)─────────
    # 资产的网格在**自己的 glb** 里(世界 glb 刻意不含它们 —— 否则网格
    # 会出现两份),所以上面那个按 `obj.name in mesh_info` 过滤的循环
    # 扫不到它们:三份『树』的材质全漏掉,只剩 glb 自带的素白 PBR
    # 材质(实测 `materials=` 里没有『树_本体』)。
    material_files.update(asset_material_files(
        export_dir, scene, asset_models, materials_map, file_names))

    # ---- 相机 / 灯光 ----
    #
    # glTF 自带的相机和灯由 glb 实例带进来,不再另外生成节点
    # (生成的话场景里会有两个相机,用户会说"多一个摄像头")。
    # Environment 脚本负责把相机设成 current 并按 Blender 的强度修正灯光。
    environment, env_files, _camera_name = build_environment_nodes(
        scene, is_3d, skip=glb_info, scene_name=scene_name,
        material_files=material_files)
    if env_files:
        ext_lines.append(f'[ext_resource type="Script" '
                         f'path="{env_script_res(scene_name)}" '
                         f'id="env_script"]')
    # 脚本落盘:用**这一次**(完整材质)的结果,让脚本默认值 = 节点属性。
    # 见上面"环境脚本不在这里落盘"的说明。
    for relative, content in (env_files or {}).items():
        target = os.path.join(export_dir, relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)

    # ---- 碰撞体 ----
    if bodies:
        lines.append('[node name="Colliders" type="Node3D" parent="."]')
        lines.append("")
        # 多层导航(第八十五轮):用到层 2+ 时每个静态碰撞体都要写自己的组
        # (层 1 也写 —— 否则层 1 的网格会把层 2 的几何一起烤进去)。
        multi_nav = nav_layers_active(scene)
        for obj, physics, built, shape_id, position, piece_entries in bodies:
            # 节点类型和脚本基类必须由**同一个函数**决定 ——
            # 各算各的就会出现"节点是 CharacterBody3D、脚本却 extends X"
            # 这类错配。而且直接读 physics.body_type 在没有物理设置时
            # 会拿到 "auto" 这种非法类型(实测在 World.tscn 里生成过
            # `type="auto"`,Godot 连场景都解析不了)。
            body_type = _base_class_for(scene, obj, dimension)
            if getattr(physics, "is_trigger", False):
                body_type = "Area3D" if is_3d else "Area2D"

            # 脚本挂载目标且角色 glb 存在时,**给它挂上自己的网格**。
            # 见 write_world_scene 文档里 character_model 那段说明 ——
            # 不这么做的话,移动这个节点只动碰撞体,画面里的角色不动。
            #
            # ── 静态资产同理(用户 2026-10-05)────────────────────────
            # 本体和每一份实例都挂同一个资产 glb 当 `Model` 子节点:
            # 网格跟着节点动(碰撞代理不再是"孤儿"),而且**一个模型只导一次**。
            model_glb = None
            model_id = ""
            if use_script and use_character_model \
                    and getattr(physics, "script_target", False):
                model_glb = character_model
                model_id = "1_character"
            else:
                key = str(getattr(physics, "asset_name", "") or "").strip()
                if key and key in asset_ids:
                    model_glb = asset_models[key]
                    model_id = asset_ids[key]
            attach_model = model_glb is not None
            # 多层导航(第八十五轮):静态碰撞体按『寻路层』写进自己的组(写在
            # **节点头**里)—— 每层的网格"只烤本组几何"就靠它(group 模式,
            # 见 nav_group_header / nav_subresource_text 的说明)。
            # 单层场景 / 非静态体不写(老场景逐字不变)。
            nav_group = ""
            if body_type.startswith("StaticBody"):
                nav_group = nav_group_header(physics, multi_nav)

            lines.append(f'[node name="{_sanitize_node_name(obj.name)}" '
                         f'type="{body_type}" parent="Colliders"'
                         f'{nav_group}]')
            if attach_model:
                # ── 位置由**角色节点**说了算,glb 里那份重复的偏移用逆变换抵消 ──
                # glTF 导出会把物体的**世界变换**写进 glb 的根节点。所以角色
                # 节点必须自己站在物体的世界坐标上 —— 游戏逻辑读的正是**节点**
                # 的 global_position(拾取距离、掉出回出生点),节点留在原点的话
                # 这些判定全错。而 glb 内部那份**同样的**变换得抵消掉,否则网格
                # 会被摆两次:
                #     节点 (0, 0.9, 11) + glb 根节点 (0, 0.9, 11)
                #   -> 网格落在 (0, 1.8, 22),直接跑出场地外(实测)。
                # 抵消写在下面的 Model 节点上(见 _glb_root_inverse_transform)。
                lines.append(f"transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, "
                             f"{_fmt(position[0])}, {_fmt(position[1])}, "
                             f"{_fmt(position[2])})")
                # 脚本只挂在**脚本挂载目标**上(资产实例不挂游戏逻辑脚本)
                if use_script and getattr(physics, "script_target", False):
                    # ── 脚本**必须挂在这里**(血泪教训,别删)────────────────
                    # 角色场景(胡桃_mesh.tscn)的根节点上也挂着同一个脚本,
                    # 看起来像"挂了两遍",所以曾经把这一行删掉过 ——
                    # **结果游戏逻辑整个不跑了**。
                    #
                    # 实测(Godot 4.7):
                    #     /root/World/Colliders/胡桃-海灯节_mesh  script=<null>
                    # 因为 **Godot 实例化一个场景时,不会把被实例化场景根节点的
                    # `script` 属性带过来**。那个脚本的 `_ready()` 确实会执行
                    # (所以动画能播),但**这个节点本身**上没有脚本 ——
                    # 于是 `_physics_process` 不跑、节点自己的成员函数都调不到。
                    #
                    # 所以两边都得挂:
                    #   * 角色场景根节点   -> 单独打开角色场景时能跑
                    #   * World 场景这个节点 -> 在 World 里跑的是**这个**实例
                    #
                    # 代价是那个脚本的 `_ready()` 会执行两次(一次来自被实例化的
                    # 角色场景,一次来自这个节点)。用户看到过"打印两遍"。
                    # 这确实是重复,但**删掉任何一份都会坏**,所以保留,
                    # 并在生成物里说明。要彻底解决得让角色场景不再挂脚本
                    # (那样角色场景单独打开就不能跑了)——
                    # 取舍见 docs/蓝图化总清单.md。
                    lines.append(f'script = ExtResource("{script_id}")')
                # ── 碰撞层/掩码属于 **body**(不是 shape)──────────────────
                # 以前写在 Collision 子节点上 —— CollisionShape3D 根本没这
                # 两个属性,Godot 会忽略:用户设的『第 2 层』从来没生效过。
                # 现在挪到 body(StaticBody3D / CharacterBody3D…)上,真的生效。
                layer = built["layer"]
                mask = built["mask"]
                if layer != "1" or mask != "1":
                    lines.append(f"collision_layer = {int(layer)}")
                    lines.append(f"collision_mask = {int(mask)}")
                # ── 脚下响度倍率(第八十轮):触发器(Area)不写,射线不打 Area ──
                if not getattr(physics, "is_trigger", False):
                    loudness = surface_loudness_of(physics)
                    if loudness is not None:
                        lines.append(
                            f"metadata/{SURFACE_LOUDNESS_META} = "
                            f"{_fmt(loudness)}")
                lines.append("")
                shape_type_name = "CollisionShape3D" if is_3d else "CollisionShape2D"
                lines.append(f'[node name="Collision" type="{shape_type_name}" '
                             f'parent="Colliders/{_sanitize_node_name(obj.name)}"]')
                lines.append(f'shape = SubResource("{shape_id}")')
                # ── 形状落在"包围盒中心"(本轮修复:这句以前整个被漏掉了)──
                # 原点在脚底的模型(大量导入模型如此),不写这句的话形状以
                # 原点为中心 —— 一半埋在地里,就是老早的"站不住"问题。
                # built["offset"] 和线框预览用**同一次计算**,不可能分家。
                position_line = _position_line(built.get("offset"), is_3d)
                if position_line:
                    lines.append(position_line)
                lines.append("")
                # 子物体碰撞块:和主 Collision 同级各来一个(头/身/腿…)
                lines.extend(_collision_piece_node_lines(
                    piece_entries, is_3d, obj.name))
                # 动态障碍(第八十三轮):勾了『动态障碍』就多挂一个
                # NavigationObstacle 子节点 —— 勾了『避障』的角色实时绕开它
                lines.extend(nav_obstacle_node_lines(
                    physics, is_3d, _sanitize_node_name(obj.name)))
                # ── 网格的重复偏移在这里抵消 ──────────────────────────────
                # Model 拿到 glb 根节点变换的**逆**,于是
                #     角色节点变换 × 逆 × glb 根节点变换 = 角色节点变换
                # 网格正好落在角色节点(以及碰撞体)所在的位置上。
                # 不抵消的话网格会被摆两次 —— 实测落在 (0, 1.8, 22),
                # 跑出场地外,相机绕着"空点"转,看起来就是"角色位置不对"。
                lines.append(f'[node name="Model" parent="Colliders/'
                             f'{_sanitize_node_name(obj.name)}" '
                             f'instance=ExtResource("{model_id}")]')
                inverse = _glb_root_inverse_transform(export_dir, model_glb,
                                                      obj, is_3d)
                if inverse:
                    lines.append(f"transform = {inverse}")
                lines.append("")
                continue

            # 非角色物体:碰撞体自己站在物体世界坐标上,节点留在原点。
            # 位置必须写出来,不写就全堆在原点,完全对不上模型
            # (实测踩过,Godot 里读回来全是 0)。
            #
            # ── 旋转也必须带上(第二十八轮修:斜坡/斜方块全歪了)────────
            # 以前这里写死**单位基** —— 于是转过角度的静态物体"网格是斜的、
            # 碰撞体是正的":20° 斜坡的碰撞体变成一块平砖(角色走不上去)、
            # 45° 方块的碰撞体变成轴对齐盒子(斜对角照样穿模)。而用户看到的
            # 是"我明明转过了" —— 这种"看得见的和判定的不是同一个形状"
            # 恰恰是碰撞体那一族问题的根。
            # 基沿用 `_emitter_basis_text`(它做的就是这个 Blender -> Godot
            # 的换轴换算,和粒子/相机同一套;离线可测)。
            basis = _emitter_basis_text(obj) if is_3d else _IDENTITY_BASIS
            lines.append(f"transform = Transform3D({basis}, "
                         f"{_fmt(position[0])}, {_fmt(position[1])}, "
                         f"{_fmt(position[2])})")
            if use_script and getattr(physics, "script_target", False):
                lines.append(f'script = ExtResource("{script_id}")')
            # 碰撞层/掩码属于 **body**(不是 shape,写在 shape 上会被 Godot
            # 忽略 —— 用户设的层从来没生效过;本轮修掉)
            layer = built["layer"]
            mask = built["mask"]
            if layer != "1" or mask != "1":
                lines.append(f"collision_layer = {int(layer)}")
                lines.append(f"collision_mask = {int(mask)}")
            # ── 脚下响度倍率(第八十轮);触发器(Area)不写 ──────────────
            # 玩法图的『声音_脚下响度』往脚下射短射线,读的就是这个 metadata。
            if not getattr(physics, "is_trigger", False):
                loudness = surface_loudness_of(physics)
                if loudness is not None:
                    lines.append(
                        f"metadata/{SURFACE_LOUDNESS_META} = "
                        f"{_fmt(loudness)}")
            lines.append("")
            shape_type = "CollisionShape3D" if is_3d else "CollisionShape2D"
            lines.append(f'[node name="Collision" type="{shape_type}" '
                         f'parent="Colliders/{_sanitize_node_name(obj.name)}"]')
            lines.append(f'shape = SubResource("{shape_id}")')
            # 形状落在"包围盒中心"(本轮修复:这句以前整个被漏掉了,
            # 原点在脚底的模型会一半埋进地里)
            position_line = _position_line(built.get("offset"), is_3d)
            if position_line:
                lines.append(position_line)
            lines.append("")
            # 子物体碰撞块:和主 Collision 同级各来一个
            lines.extend(_collision_piece_node_lines(piece_entries, is_3d,
                                                     obj.name))
            # 动态障碍(第八十三轮):勾了『动态障碍』就多挂一个
            # NavigationObstacle 子节点 —— 勾了『避障』的角色实时绕开它
            lines.extend(nav_obstacle_node_lines(
                physics, is_3d, _sanitize_node_name(obj.name)))

    # ---- 粒子节点落位(容器要先声明,子节点才挂得上)----
    if fx_blocks:
        if fx_needs_container:
            container_type = "Node3D" if is_3d else "Node2D"
            lines.append(f'[node name="Particles" type="{container_type}" '
                         f'parent="."]')
            lines.append("")
        for block in fx_blocks:
            lines.extend(block)
            lines.append("")

    # ---- 导航区(第三十四轮:寻路;第三十六轮加 2D)----
    # 一块空的导航区(3D NavigationRegion3D / 2D NavigationRegion2D):
    # 它的网格在启动时由环境脚本烤出来(见 _gk_bake_navigation)。
    # 放在根下面 = 解析根是场景根。
    if nav is not None:
        # 一层一块导航区(层 1 = `Navigation`,层 n = `Navigation_n`)——
        # 每块的网格在启动时由环境脚本**按自己的组**烤出来
        # (见 _gk_bake_navigation)。单层场景就只有 `Navigation`,和以前一样。
        for layer, region_name in nav_region_names(scene):
            lines.append(nav_region_node_text(layer, is_3d, region_name))
            lines.append("")

    if environment:
        lines.append(environment.strip("\n"))
        lines.append("")

    # 环境与后处理(第四十九轮):WorldEnvironment + Environment 子资源
    if env_node:
        lines.append(env_node.strip("\n"))
        lines.append("")

    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))

    project.set_main_scene(export_dir, f"{project.SCENES_DIR}/{scene_name}.tscn")
    return path, len(bodies)


_IDENTITY = ("1.000000, 0.000000, 0.000000, "
             "0.000000, 1.000000, 0.000000, "
             "0.000000, 0.000000, 1.000000, "
             "0.000000, 0.000000, 0.000000")


def _glb_node_details(export_dir, model_name):
    """从 glb 里取出每个节点(网格/相机/灯)的变换。

    返回 (网格变换, [(相机名, 变换, 垂直FOV)], [(灯名, 变换)])。

    必须算完整的 TRS 矩阵,不能只写平移 —— 相机和太阳的朝向全靠旋转,
    只写平移的话它们会一起朝上/朝前,画面立刻不对。
    """
    import json
    import math
    import struct

    matrices = {}
    cameras = []
    lights = []
    path = os.path.join(export_dir or "", project.ART_DIR, f"{model_name}.glb")
    if not os.path.isfile(path):
        return matrices, cameras, lights
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        offset = 12
        payload = None
        while offset < len(data):
            length, kind = struct.unpack_from("<II", data, offset)
            offset += 8
            if kind == 0x4E4F534A:
                payload = json.loads(data[offset:offset + length].decode("utf-8"))
                break
            offset += length
        if payload is None:
            return matrices, cameras, lights
    except (OSError, ValueError, struct.error):
        return matrices, cameras, lights

    camera_defs = payload.get("cameras", [])
    for node in payload.get("nodes", []):
        name = node.get("name", "")
        matrix = _glb_node_matrix(payload, node)
        if "mesh" in node:
            matrices[name] = matrix
        elif "camera" in node:
            fov = 0.0
            index = node["camera"]
            if index < len(camera_defs):
                perspective = camera_defs[index].get("perspective", {})
                if "yfov" in perspective:
                    fov = math.degrees(float(perspective["yfov"]))
            cameras.append((name, matrix, fov))
        elif node.get("extensions", {}).get("KHR_lights_punctual"):
            lights.append((name, matrix))
    return matrices, cameras, lights

def glb_object_names(export_dir, model_name="World"):
    """读 .glb 里有哪些对象,以及有没有相机/灯。

    为什么要读它:**glTF 本身就能带相机和灯**(Blender 导出时会带上,
    相机走 GLTF 的 cameras 数组,灯走 KHR_lights_punctual 扩展),
    Godot 导入后会**自动**生成对应的 Camera3D / DirectionalLight3D。
    如果我们再额外生成一份,场景里就会出现两个相机、两盏灯。

    实测踩过:World.glb 里有 4 个节点(Plane/Cube/Camera/Sun),
    包装场景里又生成了一个 Camera 和一个 Sun —— 用户在 Godot 里
    看到"多一个摄像头",而且分不清哪个是 Blender 的。

    返回 {"nodes": [...], "cameras": [...], "lights": [...]}。
    """
    result = {"nodes": [], "cameras": [], "lights": []}
    path = os.path.join(export_dir or "", project.ART_DIR, f"{model_name}.glb")
    if not os.path.isfile(path):
        return result
    try:
        import json
        import struct

        with open(path, "rb") as handle:
            data = handle.read()
        offset = 12
        payload = None
        while offset < len(data):
            length, kind = struct.unpack_from("<II", data, offset)
            offset += 8
            if kind == 0x4E4F534A:      # "JSON"
                payload = json.loads(data[offset:offset + length].decode("utf-8"))
                break
            offset += length
        if payload is None:
            return result
    except (OSError, ValueError, struct.error):
        return result

    for node in payload.get("nodes", []):
        name = node.get("name", "")
        result["nodes"].append(name)
        if "camera" in node:
            result["cameras"].append(name)
        if node.get("extensions", {}).get("KHR_lights_punctual"):
            result["lights"].append(name)
    return result


def build_environment_nodes(scene, is_3d=True, skip=None, scene_name="World",
                            material_files=None):
    """决定相机和灯怎么处理。返回 (节点文本, 需要写的文件, 相机名)。

    节点文本里如果需要 ext_resource(id="env_script"),调用方要把它写出来。
    第二个返回值是 {相对路径: 内容} 形式的"需要额外写的文件"。

    ── 分两种情况 ──────────────────────────────────────────────────
    1. **glb 里已经有相机**(glTF 能带相机和灯,Blender 导出时会写进去,
       Godot 导入后自动生成 Camera3D / DirectionalLight3D):
       不再生成相机节点 —— 生成的话场景里会有两个相机,用户会发现
       "打开 Godot 多一个摄像头",而且分不清哪个是 Blender 的。
       只挂一个 Environment 脚本把主相机设成 current。

    2. **glb 里没有相机**(比如只导了网格):
       老老实实生成一个 Camera3D 和 DirectionalLight3D 节点,
       不然按 F5 是一片灰 —— 这个坑踩过。
    """
    if scene is None:
        return "", {}, ""
    if not is_3d:
        # ── 2D(第三十六轮:2D 寻路)────────────────────────────────────
        # 2D 场景也要一份环境脚本 —— **导航烘焙**就在它里面(2D 的碰撞体
        # 是"障碍"、烘焙前要按导出后的碰撞体包围盒算一个"外框",这两件
        # 事都只能在运行时做)。相机 / 灯 / 材质那些 3D 内容在 2D 场景里
        # 没有作用对象;环境脚本里这些分支本来就有"找不到就跳过"的兜底,
        # 不会报错(探针实测)。
        # 只有**有活要干**时(寻路 / 上帝机 / 调试面板 / 开局 BGM /
        # 音乐总线音量)才写这个节点。
        files = {}
        if (_nav_on(scene) or _god_camera_on(scene)
                or _debug_panel_on(scene) or scene_bgm_path(scene)
                or _music_bus_apply_on(scene)):
            fields = _env_fields(scene, camera_name="", fov=_fmt(0.0),
                                 material_files=material_files, is_3d=False)
            files[env_script_rel(scene_name)] = \
                ENVIRONMENT_SCRIPT.format(**fields)
            return ENVIRONMENT_NODE.format(**fields), files, ""
        return "", {}, ""

    skip = skip or {}
    glb_has_camera = bool(skip.get("cameras"))
    # ── 灯光一律自己生成,不再依赖 glb ───────────────────────────────
    # 我们**主动关掉了 glTF 的灯光导出**(见 export_scene_glb 里的
    # export_lights=False):Blender 4.5 的导出器导出太阳灯时会自己崩,
    # 让整场景导出失败。所以 glb 里**不可能**有灯,
    # `skip["lights"]` 不该再影响判断 —— 否则场景里会一盏灯都没有(全黑)。
    glb_has_light = False
    # 第四十八轮:要导的灯**先收集一遍**(三种类型 + 全盏,隐藏/关着的不算),
    # 两条分支共用同一份名单 —— 以前是各写一个"找第一盏 SUN"的循环,
    # 加类型时只改一边就漏(和 _env_fields 踩过的坑同款)。
    lights = _collect_lights(scene)

    parts = []
    files = {}

    # ---- 情况 1:相机来自 glb,只需要把它设成 current ----
    if glb_has_camera:
        active = scene.camera
        name = _sanitize_node_name(active.name) if active is not None else \
            _sanitize_node_name(skip["cameras"][0])
        # FOV 也要一起给:Godot 的 Camera3D.fov 是**垂直**视场角,
        # glTF 的 yfov 也是垂直的,理论上能对上;但用户可能在 Blender 里
        # 改过传感器比例,所以这里按 Blender 的当前值算一遍更稳。
        fov = 0.0
        if active is not None:
            try:
                # Blender 的 angle 是水平角,按传感器比例换算成垂直角
                horizontal = float(active.data.angle_x)
                render = getattr(scene, "render", None)
                width = float(getattr(render, "resolution_x", 1920) or 1920)
                height = float(getattr(render, "resolution_y", 1080) or 1080)
                if width > 0 and height > 0:
                    import math
                    fov = math.degrees(
                        2.0 * math.atan(math.tan(horizontal / 2.0)
                                        * height / width))
            except Exception:
                fov = 0.0

        # 灯光**不在这里处理**:用户决定在 Godot 里自己设计灯光,
        # glb 带进来的灯原样保留。以前这里会重算强度/重建灯节点,
        # 结果和用户在 Godot 里的手动调整互相打架(用户反馈)。

        # ── 灯光:这条分支以前**不生成灯**,靠 glb 带进来 ─────────────
        # 现在 glb 里已经没有灯了(见 export_lights=False 的说明),
        # 所以这里必须自己补 —— 否则场景全黑(实测踩过:包装场景里
        # 一个 Light3D 都没有)。第四十八轮起**全部类型、全部盏**都导。
        for obj in lights:
            parts.append(_light_node_text(obj))

        fields = _env_fields(scene, camera_name=name, fov=fov,
                             material_files=material_files)
        parts.append(ENVIRONMENT_NODE.format(**fields))
        files[env_script_rel(scene_name)] = ENVIRONMENT_SCRIPT.format(**fields)
        return "".join(parts), files, name

    # ---- 情况 2:glb 里没有,得自己补 ----
    camera_name = ""
    fov_for_env = 0.0
    for obj in scene.objects:
        if obj.type != "CAMERA" or obj.hide_get():
            continue
        position = _godot_position(obj, True)
        fov = 70.0
        try:
            fov = float(obj.data.angle_x) * _DEG
        except Exception:
            pass
        camera_name = _sanitize_node_name(obj.name)
        fov_for_env = fov
        parts.append(ENVIRONMENT_CAMERA.format(
            name=camera_name,
            basis=_camera_basis_text(obj),
            cx=_fmt(position[0]), cy=_fmt(position[1]), cz=_fmt(position[2]),
            fov=fov))
        break       # 只补一个

    if not glb_has_light:
        # 第四十八轮:全部类型、全部盏(见上面 _collect_lights 的说明);
        # 能量换算在 _light_energy_for 里(太阳 ×1.2 / 点光聚光 1000W≈1.0)。
        for obj in lights:
            parts.append(_light_node_text(obj))

    # ── Environment 节点(踩过的坑)───────────────────────────────────
    # 以前这条分支**没有**加 Environment 节点 —— 只补了相机和灯就返回了。
    # 于是角色场景里没有任何东西去挂卡通材质:
    #     * 情况 1(相机灯都在 glb 里)会生成 Environment
    #     * 情况 2(自己补相机灯)不生成  ← 用户踩的就是这个
    # 而角色 glb 是刻意不带相机灯光的(见 export_subtree_glb),
    # 所以角色场景**永远**走情况 2 → 卡通材质永远挂不上。
    # 用户看到的就是"地面有材质,为什么没渲染上"。
    #
    # 判据是"有活要干",不是"有相机/灯":只要**有材质要挂**,
    # 就必须生成 Environment —— 哪怕场景里一个相机一盏灯都没有
    # (用户可能自己在 Godot 里加,但材质还是得有人挂)。
    material_table = _material_table(material_files)
    # ── 上帝摄像机也必须有 Environment 节点(用户 2026-10-04)────────
    # 上帝机现在由环境脚本生成(不再依赖角色脚本)—— 如果这里因为
    # "没相机没灯没材质"而不生成 Environment,上帝机就跟着不存在了。
    # 所以开关开着时,即使什么活都没有也要把这个节点写出来。
    # 调试面板(第二十二轮)同理:开着就必须有 Environment 节点,
    # 否则"场景里只有网格"时面板会跟着不存在,而排查恰恰常在这种场景里做。
    # 寻路(第三十四轮)同理:烤导航网格那件事本身就在环境脚本里。
    if (parts or material_table or _god_camera_on(scene)
            or _debug_panel_on(scene) or _nav_on(scene)
            or _music_bus_apply_on(scene)):
        fields = _env_fields(scene, camera_name=camera_name, fov=fov_for_env,
                             material_files=material_files)
        if not parts:
            # 纯为了挂材质 / 上帝机 / 调试面板而生成:没有相机可指,名字留空
            # 让脚本兜底去找
            fields["camera_name"] = ""
            fields["fov"] = _fmt(0.0)
        parts.append(ENVIRONMENT_NODE.format(**fields))
        files[env_script_rel(scene_name)] = ENVIRONMENT_SCRIPT.format(**fields)

    return "".join(parts), files, camera_name


def asset_material_files(export_dir, scene, asset_models, materials_map=None,
                         file_names=None):
    """静态资产的材质映射:{资产 glb 里的网格节点名: 文件名}。

    ── 为什么单独一个函数(用户 2026-10-05,实测漏过一次)──────────────
    资产的网格在**各自的 glb** 里(本体导一份,实例复用它),世界 glb
    刻意不含它们 —— 于是 write_world_scene 里那个"按世界 glb 网格名
    过滤"的循环扫不到,三份『树』的材质全漏掉,只剩 glb 自带的素白
    PBR 材质。用户看到的就是"资产没材质"。

    键用**资产 glb 里的网格节点名**(= 本体物体名):运行时
    `_apply_material_table` 按名字找的是**所有**同名 MeshInstance3D,
    所以表里一条就够,3 份实例会一起挂上。

    本体多材质槽时走逐表面覆盖(和角色同理,见 surface_material_files)。
    """
    out = {}
    for asset_key, model_name in sorted((asset_models or {}).items()):
        source = None
        for candidate in (scene.objects if scene else ()):
            candidate_physics = getattr(candidate, "gk_physics", None)
            if candidate_physics is None \
                    or getattr(candidate, "type", "") != "MESH":
                continue
            if str(getattr(candidate_physics, "asset_name", "")
                   or "").strip() != asset_key:
                continue
            if not getattr(candidate_physics, "asset_instance", False):
                source = candidate
                break
        per_surface = surface_material_files(source, file_names)
        if per_surface:
            out.update(per_surface)
            continue
        asset_meshes = glb_scene_nodes(export_dir, model_name).get("meshes", {})
        for node_name in sorted(asset_meshes):
            entry = (materials_map or {}).get(node_name)
            if entry:
                out[node_name] = entry[1]
    return out


def surface_material_files(obj, file_names):
    """给一个网格对象算出**按表面**的材质映射:{键: 文件名}。

    键的写法:
        "节点名"        -> 整个网格(material_override)
        "节点名#序号"   -> 只换第 N 个表面(surface_override)

    ── 为什么要按表面(踩过的坑)────────────────────────────────────
    MMD 模型常常是**一个网格带几十个材质槽**(实测那个胡桃模型:
    33 个表面 / 41 个材质,身体/脸/衣服/头发各是不同的贴图)。
    而 `material_override` 是**整个网格级别**的:一条覆盖把所有表面
    改成同一个材质 —— 用户看到的就是"模型变白 / 只有一种颜色",
    会以为材质压根没导出。

    只有一个材质槽时(最常见)仍然走整网格覆盖 —— 那条路已经验过很多次。
    """
    if obj is None or getattr(obj, "type", "") != "MESH":
        return {}
    node_name = _sanitize_node_name(getattr(obj, "name", "") or "Mesh")
    slots = [slot.material for slot in getattr(obj, "material_slots", ())]
    real = [(i, m) for i, m in enumerate(slots)
            if m is not None and not m.name.startswith(TOOL_MATERIAL_PREFIX)]
    if not real:
        return {}
    if len(real) == 1:
        entry = (file_names or {}).get(real[0][1].name)
        return {node_name: entry} if entry else {}
    out = {}
    for index, material in real:
        entry = (file_names or {}).get(material.name)
        if entry:
            out[f"{node_name}#{index}"] = entry
    return out


def _material_table(material_files):
    """把材质映射拼成脚本里用的 "键=路径" 多行文本。

    键有两种写法:
        "节点名=路径"        -> 整个网格用它(material_override)
        "节点名#序号=路径"   -> 只替换**第 N 个表面**(surface_override)

    用换行分隔而不是 JSON:GDScript 里解析 JSON 要额外代码,
    而这个映射很简单,一行一个就够了。路径必须是 res:// 开头的正斜杠形式 ——
    Windows 的反斜杠 Godot 认不出来。

    ── 为什么需要"按表面"(踩过的坑)────────────────────────────────
    一个 MMD 模型常常是**一个网格带几十个材质槽**(实测那个胡桃模型:
    33 个表面 / 41 个材质)。而 `material_override` 是**整个网格**级别的 ——
    一条覆盖把所有表面都改成同一个材质,结果模型看起来像"只有一种颜色 /
    变白",用户以为材质没导出。
    所以按表面逐个覆盖,才能还原模型本来的样子。
    """
    if not material_files:
        return ""
    lines = []
    for key in sorted(material_files):
        file_name = material_files[key]
        # 补上 .tres —— 这个映射有两种来源:
        #   * scene_materials():值已经带 .tres
        #   * surface_material_files():值是不带扩展名的文件名
        # 不补的话 Godot 报"材质不存在: res://materials/material"(缺后缀)。
        if not str(file_name).endswith(".tres"):
            file_name = f"{file_name}.tres"
        lines.append(f"{key}=res://{project.MATERIALS_DIR}/{file_name}")
    # ── 用**竖线**连接,不要用换行 ──────────────────────────────────
    # 踩过的坑:以前返回真换行,而这个值会被写进
    #     @export_multiline var materials: String = "..."
    # 的**默认值**里 —— GDScript 的字符串字面量不允许裸换行,
    # Godot 直接报 "Parse Error: Expected newline after comment string",
    # 整个 Environment 脚本加载不了,材质一个都挂不上。
    # 竖线在节点名和 res:// 路径里都不会出现,安全。
    return "|".join(lines)


def _sanitize_node_name(name):
    """Godot 节点名里不能有这些字符,换成下划线。

    点号尤其要紧:Godot 用 `.` 和 `:` 处理节点路径,
    名字里带点会让 `parent="A.B/C"` 这种路径解析出问题。
    """
    out = []
    for char in str(name):
        out.append("_" if char in '.:@/%"\\' else char)
    return "".join(out) or "Node"


# 弧度转角度(reduce 掉依赖 math 的一行常数)
_DEG = 57.29577951308232


def _light_basis_text(obj):
    """灯光的朝向。DirectionalLight3D 靠 -Z 指方向,和相机一样。

    只写位置不写朝向的话,太阳光一律从正上方照下来 ——
    在 Blender 里明明是斜着打的侧光,到 Godot 里就成了顶光,
    明暗完全对不上。所以朝向必须一起导出。
    """
    return _camera_basis_text(obj)


def _light_energy_for(obj):
    """Blender 灯光能量 -> Godot light_energy(第四十八轮)。

    ── 两套单位,两条规则(都写在这,别去猜)──────────────────────
    太阳:Blender 的 energy 是辐照度(新建默认 1.0),Godot 平行光默认也是
          1.0,但实测 1.0 在 Godot 里偏暗 —— 沿用老的 ×1.2、夹 [0.2, 4.0]。
    点光 / 聚光:Blender 是**瓦特**(新建一盏默认 1000W),Godot 是倍数
          (默认 1.0)—— 约定 **1000W ≈ 1.0**,夹 [0.05, 8.0]。
          想微调就直接改 Blender 里的瓦数,或者导出后在 Godot 里改。
    """
    energy = float(getattr(obj.data, "energy", 1.0) or 0.0)
    if str(getattr(obj.data, "type", "")) == "SUN":
        return max(0.2, min(energy * 1.2, 4.0))
    return max(0.05, min(energy / 1000.0, 8.0))


def _light_node_text(obj):
    """一盏 Blender 灯 -> Godot 灯光节点文本(第四十八轮)。

    返回 None = 这盏灯不导(隐藏 / 能量 0 / 类型不支持),调用方跳过它。
    """
    data = getattr(obj, "data", None)
    kind = str(getattr(data, "type", ""))
    if kind not in ("SUN", "POINT", "SPOT"):
        return None
    if float(getattr(data, "energy", 1.0) or 0.0) <= 0.0:
        return None                       # 能量 0 = 关着的灯,不当灯导
    godot_type = {"SUN": "DirectionalLight3D", "POINT": "OmniLight3D",
                  "SPOT": "SpotLight3D"}[kind]
    colour = getattr(data, "color", (1.0, 1.0, 1.0))
    try:
        r, g, b = float(colour[0]), float(colour[1]), float(colour[2])
    except Exception:                                     # noqa: BLE001
        r, g, b = 1.0, 1.0, 1.0

    extras = []
    # 范围:用 Blender 的『自定义距离』(= 灯照到多远);没填就交给 Godot 默认。
    cutoff = float(getattr(data, "cutoff_distance", 0.0) or 0.0)
    if kind in ("POINT", "SPOT") and cutoff > 0.0:
        extras.append(("omni_range" if kind == "POINT" else "spot_range",
                       f"{cutoff:.3f}"))
    if kind == "SPOT":
        # Blender 的 spot_size 是**整锥角**(弧度),Godot 的 spot_angle 是
        # **半角**(度)—— Blender 里 45° 的锥,Godot 里写 22.5 才一样大。
        angle = float(getattr(data, "spot_size", 0.0) or 0.0)
        half = max(1.0, min(angle * _DEG / 2.0, 89.0))
        extras.append(("spot_angle", f"{half:.3f}"))
    # 阴影柔和度:Blender 的"半径"和 Godot 的 light_size 是同一个意思(米)。
    soft = float(getattr(data, "shadow_soft_size", 0.0) or 0.0)
    if kind in ("POINT", "SPOT") and soft > 0.0:
        extras.append(("light_size", f"{soft:.3f}"))
    if kind == "SUN":
        # 太阳的角直径(度)<=> Godot 的 light_angular_distance(同一个东西)。
        sun_angle = float(getattr(data, "angle", 0.0) or 0.0)
        extras.append(("light_angular_distance", f"{sun_angle * _DEG:.4f}"))

    extra_text = "".join(f"{key} = {value}\n" for key, value in extras)
    position = _godot_position(obj, True)
    return LIGHT_NODE.format(
        name=_sanitize_node_name(obj.name),
        type=godot_type,
        basis=_light_basis_text(obj),
        lx=_fmt(position[0]), ly=_fmt(position[1]), lz=_fmt(position[2]),
        r=_fmt(r), g=_fmt(g), b=_fmt(b),
        energy=_light_energy_for(obj),
        shadow="true" if bool(getattr(data, "use_shadow", True)) else "false",
        extra=extra_text)


def _light_follow_camera(obj):
    """这盏灯要不要"跟着相机走"(手电筒;第四十八轮)。

    读物体上的 `gk_physics.light_follow_camera`(『游戏物理』面板 -> 灯光)。
    后台 / 老 .blend 里属性可能没挂上 —— 一律兜底 False,绝不因此报错。
    """
    physics = getattr(obj, "gk_physics", None)
    if physics is None:
        return False
    try:
        return bool(getattr(physics, "light_follow_camera", False))
    except Exception:                                     # noqa: BLE001
        return False


def _collect_lights(scene):
    """场景里所有**要导出的灯**(第四十八轮)。

    过滤规则:隐藏的灯(hide_get)和能量 0 的灯视为"关着",不导;不支持
    的类型(面积光等)也不导 —— 面板上会红字提示换太阳 / 点光 / 聚光。
    """
    lights = []
    for obj in getattr(scene, "objects", ()) or ():
        if getattr(obj, "type", "") != "LIGHT" or obj.hide_get():
            continue
        if _light_node_text(obj) is None:
            continue
        lights.append(obj)
    return lights


def _follow_light_names(lights):
    """勾了『跟随相机(手电筒)』的灯名(竖线分隔,给环境脚本用)。"""
    return "|".join(_sanitize_node_name(obj.name) for obj in lights
                    if _light_follow_camera(obj))


# .tscn 里 Transform3D 的**单位基**(9 个数,列优先其实无所谓 —— 单位矩阵对称)
_IDENTITY_BASIS = ("1.000000, 0.000000, 0.000000, "
                   "0.000000, 1.000000, 0.000000, "
                   "0.000000, 0.000000, 1.000000")


def _camera_basis_text(obj):
    """把 Blender 相机的朝向转成 Godot 的 Transform3D 基(9 个数)。

    ── 这里连着踩了两个坑,都记下来 ────────────────────────────────

    **坑一:.tscn 文本里 Transform3D 的 9 个数是"列优先"。**
    权威依据(用 Godot 自己验的,不是猜的):
        目标 basis.x=(1,2,3) y=(4,5,6) z=(7,8,9)
        官方 var_to_str 输出 = Transform3D(1, 4, 7, 2, 5, 8, 3, 6, 9, ...)
        再解析回来完全还原。
    规律:**三个基向量的第一个分量连续出现**(x.x, y.x, z.x),
    然后第二个分量、第三个分量。按行优先写等于把矩阵转置了 ——
    实测症状:相机朝上后方看,世界原点投到屏幕 y=1084(画面高 648),
    is_position_in_frustum=false,画面一片纯色。

    **坑二:相机看的是 -Z,不是 +Z。**
    直接抄旋转矩阵的列当基,会让 -Z 指向上后方。
    所以要用 right / up / back 三个方向来搭,其中 back = -(前方向)。

    坐标轴转换沿用 glTF 的 Y-up 约定:Godot(x, y, z) = Blender(x, z, -y)。
    """
    identity = ("1.000000, 0.000000, 0.000000, "
                "0.000000, 1.000000, 0.000000, "
                "0.000000, 0.000000, 1.000000")
    try:
        from mathutils import Vector

        basis = obj.matrix_world.to_3x3()
        # Blender 相机:局部 +X = 右,+Y = 上,局部 -Z = 前
        right = basis @ Vector((1.0, 0.0, 0.0))
        up = basis @ Vector((0.0, 1.0, 0.0))
        back = -(basis @ Vector((0.0, 0.0, -1.0)))   # 前方向取反

        def conv(v):
            """Blender 向量 -> Godot 向量(Z-up -> Y-up)。"""
            return (v.x, v.z, -v.y)

        rx, ry, rz = conv(right)
        ux, uy, uz = conv(up)
        bx, by, bz = conv(back)
        # **列优先**:x 轴三个分量、y 轴三个分量、z 轴三个分量
        # 即 x.x, y.x, z.x, x.y, y.y, z.y, x.z, y.z, z.z
        return (f"{_fmt(rx)}, {_fmt(ux)}, {_fmt(bx)}, "
                f"{_fmt(ry)}, {_fmt(uy)}, {_fmt(by)}, "
                f"{_fmt(rz)}, {_fmt(uz)}, {_fmt(bz)}")
    except Exception:
        return identity


def _godot_position(obj, is_3d=True):
    """把 Blender 物体的世界坐标转成 Godot 坐标。

    glTF 导出用 `export_yup=True`,做的是 **Z-up -> Y-up**:
        Blender(x, y, z)  ->  Godot(x, z, -y)

    这个转换必须在这里也做一遍 —— 否则碰撞体会全部堆在原点,
    看起来"有碰撞体"但完全对不上模型(实测踩过)。
    """
    if obj is None:
        return (0.0, 0.0, 0.0)
    try:
        # ⚠ 不要再 import mathutils:这里只是读三个分量,而 mathutils
        # 在离线桩里**没有** —— 有它的话这个函数在单测里永远返回原点,
        # "位置没写进 .tscn"这类 bug 就没法离线测(踩过一次)。
        world = obj.matrix_world.translation
        x, y, z = float(world.x), float(world.y), float(world.z)
    except Exception:
        return (0.0, 0.0, 0.0)
    if not is_3d:
        # 2D:只取 x,y(Blender 的 Z 当成 2D 的 y 更符合直觉,
        # 因为在 2D 里用户通常把物体摆在 XY 平面上)
        return (x, y, 0.0)
    return (x, z, -y)


def _collider_dims(sub_text):
    """从 `[sub_resource]` 文本里读出碰撞体的三边尺寸(长的在前)。

    ── 为什么单独抽出来(踩过的坑,一直在误报)────────────────────
    `re.findall(r"-?\\d+\\.?\\d*", "Vector3(26, 0.4, 26)")` 会把
    **"Vector3" 里的那个 3** 也当成一个数 —— 于是三边读成
    [3, 26, 0.4],诊断报出"碰撞体 (26, 3, 0.4)",和真实尺寸对不上,
    给一堆其实**完全正常**的物体刷假告警(导出结果一直是对的,
    只有这个诊断错了)。所以:先剥掉 `Vector3(...)` 这层壳再取数字。
    """
    numbers = {}
    for key in ("radius", "size", "height"):
        match = re.search(rf"^{key} = (.+)$", sub_text, re.MULTILINE)
        if match:
            numbers[key] = match.group(1)
    if "size" in numbers:
        text = numbers["size"]
        if "(" in text and ")" in text:
            text = text[text.find("(") + 1:text.rfind(")")]
        dims = [float(v) for v in re.findall(r"-?\d+\.?\d*", text)]
        return sorted((abs(v) for v in dims[:3]), reverse=True)
    found = re.findall(r"-?\d+\.?\d*", numbers.get("radius", "0"))
    radius = float(found[0]) if found else 0.0
    found_h = re.findall(r"-?\d+\.?\d*", numbers.get("height", ""))
    height = float(found_h[0]) if found_h else radius * 2.0
    # 胶囊总高 = 圆柱段 + 两端半球;球就是 2r
    return sorted((radius * 2.0, max(height, radius * 2.0), radius * 2.0),
                  reverse=True)


def colliders_that_mismatch_mesh(scene, min_ratio=0.8):
    """找出"碰撞体明显比网格小"的物体名。

    返回 [(物体名, 网格尺寸, 碰撞体尺寸)]。

    ── 为什么需要查这个(用户实测反馈)──────────────────────────────
    『碰撞尺寸』(`shape_size`)是手动尺寸,**一旦填了它就覆盖一切** ——
    不管你把形状选成胶囊还是球,用的都是这组数字。填小了不会有任何报错:
    导出的场景看着完全正常,但运行起来"角色走到模型跟前就**陷进去**"。
    用户把这个现象描述成"渲染错误",其实是碰撞体和可见模型对不上。

    实测踩过:用户给一个 2×2×2 的方块填了 `shape_size=(0.5, 1, 0.5)`,
    导出的球半径只有 0.5 —— 角色能**陷进去一大半**。

    ── 为什么只报"偏小" ────────────────────────────────────────────
    碰撞体比模型大,最坏是"还没碰到就撞上了";比模型小才会**视觉穿模**,
    那才是用户会当成 bug 报的现象。另外用户可能故意把地面做得很大,
    只报偏小就不会拿这种正常配置去刷屏。
    """
    out = []
    if scene is None:
        return out
    for obj, physics, enabled in collect_physics(scene):
        if not enabled or getattr(obj, "type", "") != "MESH":
            continue
        if obj.get(PREVIEW_MARK):
            continue
        built = build_collision(obj, True, physics)
        if built is None:
            continue
        # ⚠ 和 build_collision 用同一份**局部**包围盒(第二十八轮):
        # 拿世界 AABB 去比,转过 45° 的方块会被误报成"碰撞体比网格小"
        # (它的 AABB 是 2.83,而正确的碰撞体是 2.0)。
        bounds = object_bounds([obj], local=True)
        if bounds is None:
            continue
        mesh_size = sorted((abs(v) for v in bounds["size"]), reverse=True)

        # 碰撞体的外接尺寸:box 直接用 size;capsule / sphere 由半径和高算
        collider_size = _collider_dims(built["sub_resource"])

        if len(collider_size) < 3 or not any(mesh_size):
            continue
        # 只看**最大的两条边**:地面这种"又大又薄"的物体本来就该配一个
        # 小盒子(用户常这么干,而且贴合得很好),拿最短边去比会误报。
        ratios = [c / m for m, c in zip(mesh_size[:2], collider_size[:2])
                  if m > 1e-6]
        if len(ratios) >= 2 and min(ratios) < min_ratio:
            out.append((obj.name,
                        tuple(round(v, 3) for v in mesh_size),
                        tuple(round(v, 3) for v in collider_size)))
    return out


def _is_character_body(physics):
    """这个物体在『游戏物理』里选的是不是角色体?"""
    body = str(getattr(physics, "body_type", "") or "")
    return body in ("CharacterBody3D", "CharacterBody2D")


def _sample_points(mesh, limit=2000):
    """从网格上取一批"表面采样点":顶点 + **面中心**。

    ── 为什么面中心不能省(踩过的坑)────────────────────────────────
    第一版只看顶点,结果**Blender 默认的正方体判定失败** ——
    它只有 8 个顶点,全在角上,圆度算出来正好是 1.0(看起来像球)。
    而用户报的正是"与正方体模型对角上重叠会穿模"。
    把 6 个面中心(到中心距离 = 半边长)加进来之后,
    正方体 ≈ 0.58、球 ≈ 1.0,一眼分得开。

    返回 [(x, y, z), ...](物体局部坐标);取不到就返回空表。
    """
    points = []

    def take(item, attribute):
        try:
            co = getattr(item, attribute)
            points.append((float(co[0]), float(co[1]), float(co[2])))
        except (AttributeError, IndexError, TypeError, ValueError):
            pass

    verts = list(getattr(mesh, "vertices", None) or ())
    if verts:
        step = max(1, len(verts) // max(1, int(limit)))
        for index in range(0, len(verts), step):
            take(verts[index], "co")
    polys = list(getattr(mesh, "polygons", None) or ())
    if polys:
        step = max(1, len(polys) // max(1, int(limit)))
        for index in range(0, len(polys), step):
            take(polys[index], "center")
    return points


def mesh_roundness(obj, limit=2000):
    """这个网格"有多圆":表面采样点到包围盒中心的 最近/最远 距离之比。

    ── 为什么要看网格(用户 2026-10-06:"与正方体模型对角上重叠会穿模")──
    球和正方体的**包围盒完全一样**,光看尺寸分不出来。而两者的碰撞体差别
    很大:球是**内切**在立方体里的(半径 = 半边长)——
    正方体的八个角全在球外面,人物从斜对角贴上去就会**穿进角里**。

    实测比值(采样点到中心:最近 / 最远):
        球      ≈ 1.00   顶点和面中心都在同一半径上
        正方体  ≈ 0.58   面中心 0.5a,角 √3/2·a
        圆柱    ≈ 0.71   顶面中心 0.5h,边缘 √2/2·a
    所以 0.85 以上才算"真圆",其余都该给盒子(盒子**包住**整个模型,
    不会在角上漏出去)。

    大模型只抽样 2000 个点 —— 判形状不需要精确,只要不被几万个顶点拖慢。
    """
    points = _sample_points(getattr(obj, "data", None), limit)
    if len(points) < 4:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    zs = [point[2] for point in points]
    center = ((min(xs) + max(xs)) * 0.5,
              (min(ys) + max(ys)) * 0.5,
              (min(zs) + max(zs)) * 0.5)
    nearest = None
    farthest = 0.0
    for x, y, z in points:
        dx, dy, dz = x - center[0], y - center[1], z - center[2]
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        if nearest is None or distance < nearest:
            nearest = distance
        if distance > farthest:
            farthest = distance
    if not farthest or farthest <= 1e-9:
        return None
    return (nearest or 0.0) / farthest


def _convex_hull_geometry(points):
    """点集 -> (凸包顶点, 凸包边)。

    顶点给碰撞体(ConvexPolygonShape3D);边给线框预览 —— 两处用**同一个**
    bmesh 凸包,不会出现"线框和碰撞体形状不一样"。

    ── 为什么要它(用户本轮:"手动选凸包")──────────────────────────
    石头 / 雕像这类不规则模型,盒子会"切角"、球会"漏边",凸包正好
    贴着外形走。只对**采样点**求包(见 convex_points 的说明)——
    碰撞体不需要顶点级精确,几百个点足够,也不会被几万顶点拖慢。
    """
    try:
        import bmesh
    except ImportError:
        return [], []           # 离线测试环境没有 bmesh -> 调用方退回盒子
    bm = bmesh.new()
    for co in points:
        try:
            bm.verts.new((float(co[0]), float(co[1]), float(co[2])))
        except (TypeError, ValueError):
            continue
    bm.verts.ensure_lookup_table()
    if len(bm.verts) < 4:
        bm.free()
        return [], []
    try:
        result = bmesh.ops.convex_hull(bm, input=list(bm.verts),
                                       use_existing_faces=False)
    except (TypeError, ValueError):
        bm.free()
        return [], []
    # 删掉包内 / 没用到的点:剩下的 bm.verts / bm.edges 就是凸包本身。
    # (不删的话线框会把包内的点也连出来,看起来一团乱麻)
    leftovers = (list(result.get("geom_interior", ()))
                 + list(result.get("geom_unused", ())))
    if leftovers:
        try:
            bmesh.ops.delete(bm, geom=leftovers, context="VERTS")
        except (TypeError, ValueError):
            pass
    bm.verts.index_update()
    bm.edges.index_update()
    verts = [(float(vert.co[0]), float(vert.co[1]), float(vert.co[2]))
             for vert in bm.verts]
    edges = [(int(edge.verts[0].index), int(edge.verts[1].index))
             for edge in bm.edges]
    bm.free()
    return verts, edges


def _convex_hull_points(points):
    """只要凸包顶点(碰撞体用;线框预览走 _convex_hull_geometry 拿边)。"""
    return _convex_hull_geometry(points)[0]


def convex_points(obj, limit=400, local=False):
    """这个物体的凸包顶点(算不出来给空表)。

    ── local 参数(第二十八轮)─────────────────────────────────────
    * `local=False`(默认):**世界坐标**凸包 —— 老行为;
    * `local=True`:**物体局部坐标**凸包 —— 碰撞体用这个:物体自己的
      旋转现在由节点的 transform 带过去,点集再预乘世界矩阵就会**转两次**
      (转过角度的物体凸包歪到别处)。

    ── 为什么先采样再求包 ──────────────────────────────────────────
    直接拿全量顶点求包:几万顶点的模型既慢、凸包点也多得没意义。
    `_sample_points` 采顶点 + 面中心(和圆度判据用同一批),再求包 ——
    采样点都在表面,凸包只会"略小一点点",对碰撞体完全够用。

    ── 为什么要乘 matrix_world 而不用 mathutils ────────────────────
    离线桩里的矩阵未必支持 `@` 运算符;手动展开 4x4 乘法最稳,
    失败就把点按局部坐标返回(老行为:至少能用)。
    """
    mesh = getattr(obj, "data", None)
    points = _sample_points(mesh, limit)
    if len(points) < 4:
        return []
    hull = _convex_hull_points(points)
    if len(hull) < 4:
        return []
    if local:
        return hull
    matrix = getattr(obj, "matrix_world", None)
    if matrix is None:
        return hull
    out = []
    for x, y, z in hull:
        try:
            out.append((matrix[0][0] * x + matrix[0][1] * y
                        + matrix[0][2] * z + matrix[0][3],
                        matrix[1][0] * x + matrix[1][1] * y
                        + matrix[1][2] * z + matrix[1][3],
                        matrix[2][0] * x + matrix[2][1] * y
                        + matrix[2][2] * z + matrix[2][3]))
        except (TypeError, IndexError, ValueError):
            return hull
    return out


def guess_collision_shape(obj, is_3d=True, prefer_character=False):
    """按物体尺寸猜一个合适的碰撞形状。

    规则(面板上显示的和导出时用的是**同一份逻辑**):

      把三边从大到小排成 a >= b >= c,看**较小的两边 b/c** 的比值:

        b/c 很小(< 1.05)    -> 球      截面接近正方,两个小边都差不多
        c 很小(b/c 很大)    -> 盒子    **扁平**:有一个方向明显更薄
        b/c 居中(1.25~1.8) -> 胶囊    一个方向长,另两个方向中等且接近

    ── 为什么不是"最长/最短"比例 ──────────────────────────────────
    第一版用的是 longest/shortest,结果两个都错:
      * 圆柱(半径 = 高)的包围盒是**立方体**,比例 1.0,被判成球 ——
        球和圆柱在包围盒上确实分不出来,但那不该判成球
      * **压扁**的立方体比例高达 15,被当成"细长"判成胶囊 ——
        可它是扁的,明显该是盒子

    关键区别是**扁平**(一个方向薄)和**细长**(一个方向长)。
    只看最长/最短会把这两者混为一谈,所以现在看两个较小边的比例。
    """
    if obj is None:
        return "box"
    # ⚠ 判据用**局部**包围盒(第二十八轮):世界 AABB 会把"转过 45° 的
    # 薄板"看成一块厚板,形状就选错了。
    bounds = object_bounds([obj], local=True)
    if bounds is None:
        return "box"
    size = bounds["size"]
    if is_3d:
        dims = sorted((abs(size[0]), abs(size[1]), abs(size[2])), reverse=True)
    else:
        dims = sorted((abs(size[0]), abs(size[1])), reverse=True)
        dims.append(min(dims) * 0.5)      # 2D 里没有第三个轴,补一个"薄"的

    a, b, c = dims
    if c <= 1e-6:
        return "box"

    mid_ratio = b / c          # 两个较小边的比例
    long_ratio = a / b         # 最长边比中间边

    # 扁平:一个方向明显更薄 -> 盒子(墙、地板、平台)
    if mid_ratio > 1.8:
        return "box"
    # 细长:一个方向明显更长,另两个方向接近 -> 胶囊(人形、柱子)
    if long_ratio > 1.35 and mid_ratio <= 1.35:
        return "capsule"
    # ── 角色优先给胶囊(用户 2026-10-03:"示例那里有些还要自己填")────
    # 三边接近时包围盒分不出球和方盒,默认给球。但**角色**给球是错的:
    # 球会滚、而且会"坐在"地面接触点上(看起来悬空/打滑)。
    # 所以物体在『游戏物理』里选的是角色体时,这里直接给胶囊(3D)
    # 或盒子(2D,横版角色就是矩形)。
    if prefer_character:
        return "capsule" if is_3d else "box"
    # ── 三边接近:看**网格是不是真圆**(用户 2026-10-06)───────────────
    # 以前这里无脑给球,于是**正方体也拿到球碰撞** —— 球内切在立方体里,
    # 八个角全在球外面:人物从斜对角贴上去就穿进角里(用户实测报的
    # "与正方体模型对角上重叠会穿模")。
    # 现在按表面采样点(顶点 + 面中心)算"圆度":只有真圆(>= 0.85)才给球,其余给盒子。
    # 算不出来(没有网格数据 / 离线桩)时**退回盒子** ——
    # 盒子包得住整个模型,判错也只是"不那么圆",不会漏出角来。
    roundness = mesh_roundness(obj)
    if roundness is not None and roundness >= 0.85:
        return "sphere"
    return "box"

def collision_preview_mesh(obj, is_3d=True, physics=None):
    """算出碰撞体的可视化线框数据(顶点 + 边)。

    返回 dict 或 None:
        {"verts": [(x,y,z), ...], "edges": [(i,j), ...]}

    为什么要这个:碰撞形状在 Blender 里**看不见** —— 用户只能靠"按尺寸会选:胶囊"
    这行字去猜它到底多大、贴不贴合。UPBGE 是直接把碰撞体画在视口里的,
    这里照做:导出前先给一个线框物体,所见即所得。

    ── 本轮大修:"预览所见 = 导出所得"(用户:"预览没接进导出")────────
    以前预览自己算一套,和导出慢慢分家了(实测三处不一致):
      1. **位置**:线框被建在**世界原点** —— 父级逆矩阵把物体自己的位置
         抵消掉了,物体一挪位置就完全对不上(测试里的物体都在原点,
         所以一直没暴露);
      2. **尺寸**:『自动』模式下导出**忽略**手填尺寸,线框却还按它画;
      3. **轴向**:胶囊/球的半径以前取 x/z(把"高"当成了水平方向)。
    现在:形状与参数**全部来自 build_collision**,形状中心 = 物体原点 +
    包围盒中心偏移(和导出写进 position 的是同一次计算)——
    两边不可能再分家。

    线框是**世界坐标**:调用方(operator_io)把预览物体挂在源物体下、
    归零变换,创建时就摆在正确位置,源物体以后移动也跟着走。

    ── 子物体碰撞块 ────────────────────────────────────────────────
    预览**宿主**时,把它底下所有『作为父级的碰撞块』的形状一起画出来 ——
    人形"头/身/腿"三段要一起看才知道拼得对不对。
    """
    if obj is None:
        return None
    wire = _single_collision_wire(obj, is_3d, physics)
    # 宿主自己的碰撞形状设成了『无』-> 整个没有碰撞(块也不导出),
    # 预览和导出在这条上保持一致(不给"看起来有、实际没有"的错觉)。
    if wire is None:
        return None
    for piece in collision_pieces(obj):
        extra = _single_collision_wire(piece, is_3d,
                                       getattr(piece, "gk_physics", None))
        if extra is None:
            continue
        offset = len(wire["verts"])
        wire = {
            "verts": list(wire["verts"]) + list(extra["verts"]),
            "edges": (list(wire["edges"])
                      + [(start + offset, end + offset)
                         for start, end in extra["edges"]]),
        }
    return wire


def _single_collision_wire(obj, is_3d=True, physics=None):
    """一个物体**自己那一份**碰撞线框(世界坐标;碰撞块只画自己)。"""
    built = build_collision(obj, is_3d, physics)
    if built is None:
        return None
    # 和 build_collision 用**同一份**包围盒(局部):形状是局部的,
    # 旋转由节点 transform 带(第二十八轮)。
    bounds = object_bounds([obj], local=True)
    if bounds is None:
        return None

    shape = built["shape"]
    if shape == "box":
        if is_3d:
            # built["dims"] 是 Godot 轴序(宽, 高, 深)——
            # 换回 Blender 轴序(x, y, z)要变成(宽, 深, 高)
            dims = built["dims"]
            hx, hy, hz = dims[0] * 0.5, dims[2] * 0.5, dims[1] * 0.5
        else:
            size = bounds["size"]
            hx = built["dims"][0] * 0.5
            hy = built["dims"][1] * 0.5
            hz = max(size[2], 0.02) * 0.5      # 2D 的物体在 Blender 里是张板
        verts = [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz),
                 (-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]
        edges = [(0, 1), (1, 2), (2, 3), (3, 0),
                 (4, 5), (5, 6), (6, 7), (7, 4),
                 (0, 4), (1, 5), (2, 6), (3, 7)]
        wire = {"verts": verts, "edges": edges}
    elif shape == "sphere":
        wire = _sphere_wire(built["radius"])
    elif shape == "capsule":
        # 半径/高度和导出同一套:半径 = 水平两边取大,高 = 竖直方向
        wire = _capsule_wire(built["radius"],
                             max(built["height"], built["radius"] * 2.0))
    elif shape == "cylinder":
        wire = _cylinder_wire(built["radius"], max(built["height"], 0.002))
    elif shape == "convex":
        wire = _convex_wire(obj, bounds)
    else:
        return None
    if wire is None:
        return None

    base = _collider_world_base(obj, built)
    # ── 线框也要按物体的**旋转**转过去(第二十八轮)───────────────
    # 形状现在是"局部盒子 + 节点旋转"(见 object_bounds 的说明):
    # 线框不跟着转的话,预览还是正的、游戏里却是斜的 —— 那正是
    # "预览≠导出"的老毛病,只是换了个方向。
    verts = _rotate_wire_verts(obj, wire["verts"])
    return {"verts": [(vx + base[0], vy + base[1], vz + base[2])
                      for vx, vy, vz in verts],
            "edges": list(wire["edges"])}


def _rotate_wire_verts(obj, verts):
    """线框顶点乘上物体的旋转(不含平移)。

    线框在 Blender 轴序里画,所以直接用 matrix_world 的 3x3。
    读不到矩阵 / 桩里没实现乘法就原样返回(至少不崩)。
    """
    matrix = getattr(obj, "matrix_world", None)
    if matrix is None:
        return list(verts)
    out = []
    for x, y, z in verts:
        try:
            out.append((
                matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z,
                matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z,
                matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z))
        except (TypeError, IndexError, ValueError):
            return list(verts)
    return out


def _collider_world_base(obj, built):
    """碰撞形状的世界中心 = 物体原点 + 包围盒中心偏移(Blender 轴序)。

    built["offset"] 是 Godot 轴序 (x, z, -y) —— 换回来正好是
    (dx, dy, dz) = 包围盒中心 − 物体原点,和导出写进碰撞节点 position 的
    是同一个数,所以预览和导出必然落在同一个点上。
    """
    ox, oy, oz = 0.0, 0.0, 0.0
    matrix = getattr(obj, "matrix_world", None)
    if matrix is not None:
        try:
            ox, oy, oz = (float(matrix[0][3]), float(matrix[1][3]),
                          float(matrix[2][3]))
        except (TypeError, IndexError, ValueError):
            pass
    offset = built.get("offset") or (0.0, 0.0, 0.0)
    # Godot 轴序 (x, z, -y) -> Blender 轴序 (dx, dy, dz)
    dx, dy, dz = offset[0], -offset[2], offset[1]
    # ── 偏移也要跟着物体转(第二十八轮)───────────────────────────
    # 形状挂在物体的局部坐标系里,偏移是那个坐标系里的向量 ——
    # 物体转了 20°,形状中心也跟着转 20°。以前偏移是世界空间算的
    # (世界中心 − 原点),所以不用转;现在换成局部偏移就得转。
    if matrix is not None:
        try:
            dx, dy, dz = (
                matrix[0][0] * dx + matrix[0][1] * dy + matrix[0][2] * dz,
                matrix[1][0] * dx + matrix[1][1] * dy + matrix[1][2] * dz,
                matrix[2][0] * dx + matrix[2][1] * dy + matrix[2][2] * dz)
        except (TypeError, IndexError, ValueError):
            pass
    return (ox + dx, oy + dy, oz + dz)


def _convex_wire(obj, bounds):
    """凸包线框:点 + 边都来自**同一个** bmesh 凸包(与导出同一批采样点)。

    返回形状局部坐标(相对包围盒中心)—— 外面的基座会把它加回去,
    等于世界坐标下的凸包外形,和导出的 ConvexPolygonShape3D 一一对应。
    """
    mesh = getattr(obj, "data", None)
    verts, edges = _convex_hull_geometry(_sample_points(mesh, 400))
    if len(verts) < 4 or not edges:
        return None
    # ── 只在**局部**坐标系里减中心(第二十八轮)───────────────────
    # bounds 现在是局部包围盒(和导出同一份);旋转由外面的
    # `_rotate_wire_verts` 统一做 —— 这里再乘一次 matrix_world 就转两遍了。
    center = bounds.get("center") or (0.0, 0.0, 0.0)
    out = [(x - center[0], y - center[1], z - center[2])
           for x, y, z in verts]
    return {"verts": out, "edges": list(edges)}


def _sphere_wire(radius, segments=16, rings=4):
    """球的线框:经线 + 纬线。

    用标准的球坐标:
        x = r * sin(phi) * cos(theta)
        y = r * sin(phi) * sin(theta)
        z = r * cos(phi)
    phi 是极角(0..pi),theta 是方位角(0..2pi)。
    """
    import math

    verts = []
    edges = []

    # 经线:固定 theta,扫 phi
    for i in range(4):
        theta = math.pi * i / 4.0
        base = len(verts)
        for j in range(segments + 1):
            phi = math.pi * j / segments
            verts.append((radius * math.sin(phi) * math.cos(theta),
                          radius * math.sin(phi) * math.sin(theta),
                          radius * math.cos(phi)))
        for j in range(segments):
            edges.append((base + j, base + j + 1))

    # 纬线:固定 phi,扫 theta
    for k in range(1, rings + 1):
        phi = math.pi * k / (rings + 1)
        r = radius * math.sin(phi)
        z = radius * math.cos(phi)
        base = len(verts)
        for j in range(segments):
            theta = 2.0 * math.pi * j / segments
            verts.append((r * math.cos(theta), r * math.sin(theta), z))
        for j in range(segments):
            edges.append((base + j, base + (j + 1) % segments))

    return {"verts": verts, "edges": edges}


def _capsule_wire(radius, height, segments=16):
    """胶囊线框:两圈 + 四条竖边 + 半球弧。"""
    import math

    half = max(height * 0.5 - radius, 0.0)
    verts = []
    edges = []
    for z in (half, -half):
        base = len(verts)
        for j in range(segments):
            theta = 2.0 * math.pi * j / segments
            verts.append((radius * math.cos(theta), radius * math.sin(theta), z))
        for j in range(segments):
            edges.append((base + j, base + (j + 1) % segments))
    top = len(verts) - 2 * segments
    bottom = len(verts) - segments
    for j in range(0, segments, max(1, segments // 4)):
        edges.append((top + j, bottom + j))
    # 上下极点
    apex_top = len(verts)
    verts.append((0.0, 0.0, half + radius))
    apex_bottom = len(verts)
    verts.append((0.0, 0.0, -half - radius))
    for j in range(0, segments, max(1, segments // 4)):
        edges.append((top + j, apex_top))
        edges.append((bottom + j, apex_bottom))
    return {"verts": verts, "edges": edges}


def _cylinder_wire(radius, height, segments=16):
    import math

    half = height * 0.5
    verts = []
    edges = []
    for z in (half, -half):
        base = len(verts)
        for j in range(segments):
            theta = 2.0 * math.pi * j / segments
            verts.append((radius * math.cos(theta), radius * math.sin(theta), z))
        for j in range(segments):
            edges.append((base + j, base + (j + 1) % segments))
    top = len(verts) - 2 * segments
    bottom = len(verts) - segments
    for j in range(0, segments, max(1, segments // 4)):
        edges.append((top + j, bottom + j))
    return {"verts": verts, "edges": edges}


def _fmt(value):
    """Godot 的浮点写法。留 6 位小数,避免科学计数法。

    **负零写成 0**:`_fmt(-0.0)` 原本会给出 "-0.000000" —— 导出文件里
    冒出负零很扎眼,而且"同一份计算在预览和 .tscn 里看起来不一样"
    (碰撞那边修过一次,粒子这里换轴 / 取反一算又是一堆负零)。
    数学上 -0.0 == 0.0,写成 0 完全等价。
    """
    number = float(value)
    if number == 0.0:
        number = 0.0                    # -0.0 -> 0.0
    return f"{number:.6f}"


# 3D 碰撞形状的 sub_resource 模板
SHAPE_TEMPLATES_3D = {
    "box": ('[sub_resource type="BoxShape3D" id="shape"]\n'
            "size = Vector3({x}, {y}, {z})"),
    "sphere": ('[sub_resource type="SphereShape3D" id="shape"]\n'
               "radius = {r}"),
    "capsule": ('[sub_resource type="CapsuleShape3D" id="shape"]\n'
                "radius = {r}\nheight = {h}"),
    "cylinder": ('[sub_resource type="CylinderShape3D" id="shape"]\n'
                 "radius = {r}\nheight = {h}"),
    # 凸包(用户本轮):点集本身就是形状,不用尺寸参数。
    # 点是 **Godot 坐标**(x, z, -y)、已减掉包围盒中心 ——
    # 中心本身由碰撞节点的 position(= built["offset"])补上。
    "convex": ('[sub_resource type="ConvexPolygonShape3D" id="shape"]\n'
               "points = PackedVector3Array({points})"),
}

SHAPE_TEMPLATES_2D = {
    "box": ('[sub_resource type="RectangleShape2D" id="shape"]\n'
            "size = Vector2({x}, {y})"),
    "sphere": ('[sub_resource type="CircleShape2D" id="shape"]\n'
               "radius = {r}"),
    "capsule": ('[sub_resource type="CapsuleShape2D" id="shape"]\n'
                "radius = {r}\nheight = {h}"),
    "cylinder": ('[sub_resource type="CircleShape2D" id="shape"]\n'
                 "radius = {r}"),
}


def build_collision(obj, is_3d=True, physics=None):
    """给一个物体算出碰撞体配置。

    返回 dict 或 None(不需要碰撞时):
        {"shape": "capsule", "sub_resource": "...", "node": "...", "offset": (x,y,z)}
    `node` 是挂在物体下面的 CollisionShape 节点文本。
    """
    if obj is None:
        return None

    shape = "auto"
    layer = "1"
    mask = "1"
    is_trigger = False
    override_size = None

    if physics is not None:
        shape = getattr(physics, "collision_shape", "auto")
        layer = getattr(physics, "collision_layer", "1")
        mask = getattr(physics, "collision_mask", "1")
        is_trigger = bool(getattr(physics, "is_trigger", False))
        size = getattr(physics, "shape_size", None)
        if size is not None and len(size) == 3:
            override_size = (float(size[0]), float(size[1]), float(size[2]))

    if shape == "none":
        return None
    if shape == "auto":
        shape = guess_collision_shape(
            obj, is_3d, prefer_character=_is_character_body(physics))
        # ── "自动"就是自动:手动尺寸只在**明确选了形状**时生效 ──────────
        # 踩过的坑:老版本 shape_size 默认值是 (0.5, 1.0, 0.5),
        # 于是**所有**物体(包括选着"自动"的地面)都被那组数字覆盖成
        # 半米见方的一小块 —— 角色一站上去就穿过去(用户"站不住"的真凶)。
        # 现在就算老 .blend 里还留着那组数字,选"自动"也不会再用它。
        override_size = None

    bounds = object_bounds([obj], local=True)
    if bounds is None:
        return None
    size = bounds["size"]

    # ── 手填尺寸全 0 = "没填",退回自动贴合(用户 2026-10-03)──────────
    # 面板上写的是"填了就用这组数字",用户把三个框清成 0 表达的是
    # "我不要手动尺寸" —— 但 (0,0,0) 会生成一个**零大小**的碰撞体,
    # 表现是"角色直接掉下去"。
    if override_size is not None and max(abs(v) for v in override_size) < 1e-6:
        override_size = None

    # ── 最薄的那一边要给个下限(同一天踩到的坑)────────────────────
    # 地面常常是一张**平面**:包围盒某个方向是 0 -> 盒子碰撞体厚度 0 ->
    # 角色一站上去就穿过去(用户:"运动一两步就掉下去")。
    # 给 5 厘米的下限:薄板 / 地面都能站人,厚物体不受影响。
    MIN_THICKNESS = 0.05

    # 形状参数
    convex_points_bl = []          # 凸包的 Blender 点集(非凸包 = 空)
    if is_3d:
        if override_size is not None:
            # 用户填的是**完整尺寸**,三个轴一视同仁地取一半 ——
            # 以前只把 Y 取了一半,X/Z 原样用,于是"填 0.5 出来 1.0",
            # 用户看到的正是"它还是这样大"(实测)
            radius = max(override_size[0], 0.001) * 0.5
            height = max(override_size[1], 0.002)
            half = (max(override_size[0] * 0.5, MIN_THICKNESS),
                    max(override_size[1] * 0.5, MIN_THICKNESS),
                    max(override_size[2] * 0.5, MIN_THICKNESS))
        else:
            # ── 自动尺寸:**必须做 Z-up -> Y-up 的轴向换算**──────────────
            # 用户截图里的"地面立起来了"就是这个 bug:
            # 地面是 20x20 的平面(Blender 里 Z 方向几乎为 0),
            # 而生成的盒子写的是 Vector3(20, 20, 0.1) ——
            # 在 **Godot** 里 Y 才是"上",这等于一块 20 宽、**20 高**、
            # 0.1 厚的**竖墙**。角色当然站不住(摔下去/穿过去)。
            # 换算规则和 `_godot_position` 一致:Blender(x, y, z) -> Godot(x, z, y)
            # (尺寸没有正负,所以不用取负号)
            radius = max(size[0], size[1], 0.001) * 0.5
            height = max(size[2], 0.002)         # 竖直方向 = Blender 的 Z
            half = (max(size[0] * 0.5, MIN_THICKNESS),
                    max(size[2] * 0.5, MIN_THICKNESS),
                    max(size[1] * 0.5, MIN_THICKNESS))
        params = {
            "x": _fmt(half[0] * 2.0), "y": _fmt(half[1] * 2.0),
            "z": _fmt(half[2] * 2.0),
            "r": _fmt(radius), "h": _fmt(max(height, radius * 2.0)),
        }
        # ── 凸包:按网格真实外形(用户本轮选的"手动选凸包")────────────
        # 尺寸参数对它没用 —— 点集本身就是形状。点取**局部坐标凸包**
        # (第二十八轮:物体自己的旋转现在由节点 transform 带,点集不能再
        # 预乘世界矩阵 —— 否则转过角度的物体凸包会**转两次**),
        # 减掉包围盒中心(和盒/球同一套"以中心为准"的规矩),再换成
        # Godot 轴序(x, z, -y);中心由碰撞节点的 position 补上。
        # 算不出凸包(离线桩 / 空网格)就**退回盒子** —— 盒子包得住
        # 整个模型,判错也只是"不那么贴",不会漏出角来。
        if shape == "convex":
            convex_local = convex_points(obj, local=True)
            if len(convex_local) < 4:
                shape = "box"
            else:
                center = bounds.get("center") or (0.0, 0.0, 0.0)
                convex_points_bl = [(point[0] - center[0],
                                     point[1] - center[1],
                                     point[2] - center[2])
                                    for point in convex_local]
                params["points"] = ", ".join(
                    f"{_fmt(point[0])}, {_fmt(point[2])}, {_fmt(-point[1])}"
                    for point in convex_points_bl)
        template = SHAPE_TEMPLATES_3D.get(shape, SHAPE_TEMPLATES_3D["box"])
        node_type = "CollisionShape3D"
        # ── 形状要落在**模型中心**,不能假设原点就在中心 ────────────────
        # 用户"站不住"的真凶:角色的网格顶点是 z=0..1(原点在**脚底**),
        # 而胶囊/盒子是以**原点为中心**生成的 —— 于是形状有一半在地面
        # 之下,Godot 里角色被物理往下挤,看起来就是"掉下去/站不住"。
        # 这里把"包围盒中心 - 物体原点"算出来,写进碰撞节点的 transform。
        offset = (0.0, 0.0, 0.0)
        try:
            center = bounds.get("center")
            # ── 偏移 = 包围盒中心相对**物体原点**(第二十八轮)──────────
            # bounds 现在是**局部**包围盒(local=True),中心本身就是
            # "相对物体原点"的偏移,不要再减世界平移 —— 形状挂在物体的
            # 局部坐标系里,节点的 position 就是这个坐标系里的偏移,
            # 旋转由 body 的 transform 带(斜坡/斜方块靠它摆正)。
            dx = float(center[0])
            dy = float(center[1])
            dz = float(center[2])
            # 和 _godot_position 同一套:Blender(x,y,z) -> Godot(x,z,-y)
            offset = (dx, dz, -dy)
        except Exception:
            offset = (0.0, 0.0, 0.0)
    else:
        if override_size is not None:
            radius = max(override_size[0], 0.001) * 0.5
            height = max(override_size[1], 0.002)
        else:
            radius = max(size[0], 0.001) * 0.5
            height = max(size[1], 0.002)
        params = {
            "x": _fmt(max(size[0], 0.002)), "y": _fmt(max(size[1], 0.002)),
            "z": "0.0",
            "r": _fmt(radius), "h": _fmt(max(height, radius * 2.0)),
        }
        if shape == "convex":
            shape = "box"          # 凸包只有 3D:2D 退回盒子
        template = SHAPE_TEMPLATES_2D.get(shape, SHAPE_TEMPLATES_2D["box"])
        node_type = "CollisionShape2D"
        offset = (0.0, 0.0, 0.0)

    body_type = "StaticBody3D"
    if physics is not None:
        body_type = getattr(physics, "body_type", "StaticBody3D")
    if is_trigger or "Area" in body_type:
        node_type = "CollisionShape3D" if is_3d else "CollisionShape2D"

    node_lines = [f'[node name="Collision" type="{node_type}" parent="."]',
                  'shape = SubResource("shape")']
    position_line = _position_line(offset, is_3d)
    if position_line:
        node_lines.append(position_line)
    if layer != "1" or mask != "1":
        node_lines.append(f"collision_layer = {int(layer)}")
        node_lines.append(f"collision_mask = {int(mask)}")

    return {
        "shape": shape,
        "sub_resource": template.format(**params),
        "node": "\n".join(node_lines),
        "layer": layer,
        "mask": mask,
        "is_trigger": is_trigger,
        # ── 数字形式的形状参数(用户 2026-10-05)──────────────────────
        # `sub_resource` 是给 .tscn 用的文本;运行时创建实例
        # (『资产_创建实例』)要的是**数字** —— 必须和上面那份文本用
        # 同一组数,所以在这里一起返回,而不是让代码生成那边再算一遍
        # (各算一遍迟早会算出两个大小)。
        "dims": (half[0] * 2.0, half[1] * 2.0, half[2] * 2.0)
        if is_3d else (max(size[0], 0.002), max(size[1], 0.002), 0.0),
        "radius": float(radius),
        "height": float(height),
        "offset": tuple(offset),
        # 凸包点集:Godot 轴序那份给 .tscn / 运行时创建用;
        # points_bl 是 Blender 轴序副本,线框预览用(两边同一份来源)。
        "points": [(point[0], point[2], -point[1])
                   for point in convex_points_bl],
        "points_bl": convex_points_bl,
    }


def is_helper_physics(obj):
    """这个物体是不是**插件/工具生成的辅助物理体**(不该导出碰撞)。

    目前只认 MMD:模之屋的 .pmx 用 mmd_tools 导入后,会额外生成
    **每根骨头一个**的刚体小球/小方块 + 关节(用户的胡桃模型是 848 个)。
    它们身上带着 `mmd_type` / `mmd_rigid` / `mmd_joint` 自定义属性。

    ── 不排除会怎样(实测)────────────────────────────────────────
    用户截图里角色周围一堆蓝条、场景里 2301 个碰撞体,就是这么来的:
    这 848 个刚体每个只有 34 个顶点,导出后全变成 Godot 里的
    StaticBody3D + CollisionShape3D —— 视觉上一片蓝框,性能上也白烧。
    而且它们本来就带 `gk_physics`(应该是插件早期自动加的),
    所以"用户没配就不导出"这条规则拦不住。

    ── 为什么敢直接跳过 ──────────────────────────────────────────
    MMD 的刚体是给 MMD 自己的物理模拟用的(头发/裙子摆动)。
    glTF 不导出这套模拟,Godot 也不认,所以它们到 Godot 里
    只是一堆静止的小方块 —— 没有任何正面作用。
    真需要裙子物理的用户会在 Godot 里用 BoneAttachment3D 自己做。
    """
    for key in HELPER_PHYSICS_KEYS:
        if key in getattr(obj, "keys", lambda: [])():
            return True
    return False


# ==========================================================================
# 子物体碰撞块(用户本轮:"人形头/身/腿各摆一个")
# ==========================================================================
# 勾了『作为父级的碰撞块』的网格完全**不进游戏**:
#   * 网格不导出(不出现在 glb / 画面里);
#   * 不单独生成碰撞体 —— 它的形状合并进**父物体**的碰撞体里;
#   * 形状/尺寸还是用它自己的『游戏物理』设置(每个块可以不一样)。
# 这套做法的好处:视口里直接拖着摆,配合碰撞线框预览所见即所得。


def is_collision_piece(obj):
    """这个物体是不是"子物体碰撞块"?（勾了『作为父级的碰撞块』）"""
    physics = getattr(obj, "gk_physics", None)
    return bool(getattr(physics, "collision_piece", False))


def collision_owner(obj):
    """碰撞块的宿主:往上找最近的、本身不是碰撞块的网格物体。

    找不到(碰撞块被顶在场景根上)返回 None —— 导出时跳过它;
    面板上会给红字提示(见 ui 的『游戏物理』面板)。
    """
    parent = getattr(obj, "parent", None)
    while parent is not None:
        if getattr(parent, "type", "") == "MESH" \
                and not is_collision_piece(parent):
            return parent
        parent = getattr(parent, "parent", None)
    return None


def collision_pieces(owner):
    """owner 底下所有碰撞块(**所有层级的子级**,不是一个层)。

    为什么不是一个层级:用户的层级可能"本体 -> 空物体 -> 碰撞块",
    或者块挂在块下面 —— 只要往上走能走到 owner,就算它的。
    顺序按名字排:导出结果稳定,测试可断言。
    """
    out = []
    stack = list(getattr(owner, "children", ()) or ())
    while stack:
        child = stack.pop()
        stack.extend(getattr(child, "children", ()) or ())
        if getattr(child, "type", "") == "MESH" and is_collision_piece(child):
            out.append(child)
    out.sort(key=lambda item: getattr(item, "name", ""))
    return out


def _piece_offset_godot(piece, owner, is_3d=True):
    """碰撞块的形状中心 -> 宿主原点 的偏移(Godot 轴序)。

    宿主节点的 position = 宿主原点,所以块的 Collision 节点写这个偏移,
    形状就落在它自己该在的地方(视口里拖到哪里,游戏里就在哪里)。
    """
    bounds = object_bounds([piece])
    if bounds is None:
        return (0.0, 0.0, 0.0)
    center = bounds["center"]
    origin = (0.0, 0.0, 0.0)
    matrix = getattr(owner, "matrix_world", None)
    if matrix is not None:
        try:
            origin = (float(matrix[0][3]), float(matrix[1][3]),
                      float(matrix[2][3]))
        except (TypeError, IndexError, ValueError):
            pass
    dx = center[0] - origin[0]
    dy = center[1] - origin[1]
    dz = center[2] - origin[2]
    if is_3d:
        return (dx, dz, -dy)
    return (dx, dy, 0.0)


def _position_line(offset, is_3d=True):
    """碰撞节点的 `position = ...` 行(全零/空 -> None)。

    ── 为什么单抽出来(本轮修复:"站不住"的老修复被漏掉了)──────────
    build_collision 早就算好了「包围盒中心 − 物体原点」,但它只躺在
    返回值里没人用 —— 生成的 .tscn 里 Collision 节点上没有 position,
    形状以**原点**为中心:原点在脚底的模型一半埋进地里。
    现在两条导出分支 + 线框预览都用这一个函数/一个数。
    """
    if not offset:
        return None
    values = [float(value) for value in offset]
    if all(abs(value) <= 1e-9 for value in values):
        return None
    # -0.0 一律写成 0.0:导出文件里冒出 "-0.000000" 很扎眼(同一组数
    # 在预览和 .tscn 里看起来还不一样),顺手归一化。
    values = [0.0 if abs(value) <= 1e-9 else value for value in values]
    if is_3d:
        return (f"position = Vector3({_fmt(values[0])}, {_fmt(values[1])}, "
                f"{_fmt(values[2])})")
    return f"position = Vector2({_fmt(values[0])}, {_fmt(values[1])})"


def _collision_piece_node_lines(entries, is_3d, owner_name):
    """碰撞块的 Collision 节点行 —— 和主 Collision 同级。

    entries: [(块, built, shape_id, offset_godot), ...]
    名字用 Collision2 / Collision3…:Godot 的兄弟节点不许重名,用序号
    最稳(中文 / 特殊字符的块名不能直接当节点名)。
    """
    lines = []
    shape_type = "CollisionShape3D" if is_3d else "CollisionShape2D"
    for order, (_piece, _built, shape_id, offset) in enumerate(entries,
                                                              start=2):
        lines.append(f'[node name="Collision{order}" type="{shape_type}" '
                     f'parent="Colliders/{_sanitize_node_name(owner_name)}"]')
        lines.append(f'shape = SubResource("{shape_id}")')
        position_line = _position_line(offset, is_3d)
        if position_line:
            lines.append(position_line)
        lines.append("")
    return lines


def collect_physics(scene):
    """收集场景里所有物体的物理设置。

    返回 [(物体, 设置对象, 是否启用)] —— 顺序跟场景一致,生成结果稳定。
    不导入 bpy:scene 由调用方传进来,这样离线测试也能跑。

    辅助物理体(MMD 刚体等)会被**跳过** —— 见 is_helper_physics。
    """
    if scene is None:
        return []
    out = []
    for obj in scene.objects:
        if is_helper_physics(obj):
            continue
        physics = getattr(obj, "gk_physics", None)
        # 以前这里有个"是否导出"的开关,后来按用户要求删掉了:
        # 所有网格都导出,碰撞盒要不要在属性里直接配,不用先开一个总开关。
        out.append((obj, physics, True))
    return out


# ==========================================================================
# 粒子发射器(需求 5,2026-10-05 第二十一轮)
# ==========================================================================
#
# ── 为什么粒子只能是"Blender 标记 + Godot 生成"────────────────────
# glTF 规范里**没有粒子**,Blender 的粒子系统一个字都传不过去
# (就算硬导,Godot 也不认)。所以分工只能是:
#
#     Blender:标记 + 参数(物体上的 gk_fx;面板『游戏物理』→『粒子特效』)
#     导出   :生成 Godot 的 GPUParticles3D / CPUParticles3D + 材质 / 贴图
#     运行时 :『特效_播放』『特效_停止』『特效_爆发』三个节点控制它
#
# ── 生成的 .tscn 长什么样(GPU 3D 那一版)──────────────────────────
#     [ext_resource type="Texture2D" path="res://art/fx_soft.png" id="fx_tex0"]
#     [sub_resource type="QuadMesh" id="fx_mesh0"]            ← 画什么(一片方片)
#     [sub_resource type="StandardMaterial3D" id="fx_draw0"]  ← 怎么画(无光照+广告牌)
#     [sub_resource type="Gradient" id="fx_grad0"]            ← 颜色渐变(起→终)
#     [sub_resource type="GradientTexture1D" id="fx_ramp0"]   ← 材质要的是"纹理"
#     [sub_resource type="Curve" id="fx_curve0"]              ← 大小曲线(起→终)
#     [sub_resource type="CurveTexture" id="fx_scale0"]
#     [sub_resource type="ParticleProcessMaterial" id="fx_proc0"]  ← 物理与外观
#     [node name="火把" type="GPUParticles3D" parent="Particles"]
#
# ── 这些模板不是猜的(实测核对过,别再凭印象改)────────────────────
# 开发时用 Godot 4.7 自己存了一份"标准粒子场景"当对照(ref.tscn),
# 属性名 / 枚举值 / Curve 的 `_data` 写法全部照抄。踩过的三个不一样:
#   * `randomness` 在**节点**上(GPUParticles3D),材质上**没有**这个属性;
#   * CPU 粒子的缩放曲线要 `Curve`(直接给曲线),GPU 的要 `CurveTexture`;
#   * CPU 的颜色渐变要 `Gradient`,GPU 的要 `GradientTexture1D`。

FX_DEFAULT_TEXTURE = "fx_soft.png"


def fx_node_type(fx, is_3d=True):
    """发射器生成哪种 Godot 节点(由面板上的『粒子类型』决定)。"""
    cpu = str(getattr(fx, "particle_type", "gpu") or "gpu") == "cpu"
    if is_3d:
        return "CPUParticles3D" if cpu else "GPUParticles3D"
    return "CPUParticles2D" if cpu else "GPUParticles2D"


def collect_emitters(scene):
    """收集场景里勾了『作为粒子发射器』的物体:[(物体, 设置)]。

    和 collect_physics 同一套筛法(跳过预览物 / 辅助物理体),
    顺序跟场景一致,生成结果稳定。**网格和空物体都收** ——
    空物体天然就是"只标位置和朝向"的发射器。
    """
    if scene is None:
        return []
    out = []
    for obj in scene.objects:
        if is_helper_physics(obj) or obj.get(PREVIEW_MARK):
            continue
        fx = getattr(obj, "gk_fx", None)
        if fx is None or not getattr(fx, "enabled", False):
            continue
        out.append((obj, fx))
    return out


def fx_hidden_meshes(scene):
    """勾了『不显示本体网格』的发射器 —— 导出时从世界 glb 里排除。

    和『子物体碰撞块』同一个套路:那个物体只当"标记",网格不进画面。
    """
    return [obj for obj, fx in collect_emitters(scene)
            if getattr(fx, "hide_mesh", False)
            and getattr(obj, "type", "") == "MESH"]


def fx_texture_file(obj, export_dir=None):
    """这个发射器用的粒子贴图(art/ 下的 .png 文件名)。

    优先用**物体材质的基础色贴图**(走现有的材质/贴图导出链路);
    没有就退回内置那张圆形柔光(FX_DEFAULT_TEXTURE)。
    名字算法必须和 export_textures / write_materials 一致 ——
    两边各算一次的话 Godot 会 "Resource file not found"。

    export_dir 给了的话还会**确认那个文件真的在**(导贴图可能失败 /
    卡通材质总开关关掉时整条链路都不跑):不在就退回内置那张 ——
    宁可粒子是白点,也不能让 .tscn 引用一个不存在的资源
    (Godot 加载 .tscn 时一个 ext_resource 缺失,整个场景都起不来)。
    """
    material = first_real_material(obj)
    name = _base_color_texture(material)
    if name:
        try:
            import bpy

            image = bpy.data.images.get(name)
            if image is not None and image.size[0] > 0:
                file_name = safe_name(name, "texture") + ".png"
                if export_dir is None:
                    return file_name
                if os.path.isfile(os.path.join(export_dir,
                                               project.ART_DIR, file_name)):
                    return file_name
        except Exception:                                 # noqa: BLE001
            return FX_DEFAULT_TEXTURE
    return FX_DEFAULT_TEXTURE


def fx_default_png():
    """内置默认粒子贴图:64×64 的圆形柔光(纯 Python 生成)。

    ── 为什么要内置一张,而不是"没贴图就拉倒"──────────────────────
    2D 粒子没有贴图**什么都画不出来**;3D 画出来是一个硬邦邦的白方块。
    用户第一次试粒子时多半还没准备贴图,所以这里兜底生成一张 ——
    软边的白色光点,火苗 / 烟雾 / 星光用它都不难看。

    纯 zlib + struct 手写 PNG:不依赖 PIL,也不需要 bpy ——
    离线单测里也能验(见 tests 里的"PNG 头 + 尺寸"断言)。
    """
    import struct
    import zlib

    size = 64
    raw = bytearray()
    for y in range(size):
        raw.append(0)                        # 每行开头那个 filter 字节
        for x in range(size):
            dx = (x + 0.5) / size * 2.0 - 1.0
            dy = (y + 0.5) / size * 2.0 - 1.0
            distance = min(1.0, (dx * dx + dy * dy) ** 0.5)
            alpha = int(round(255 * max(0.0, (1.0 - distance) ** 2)))
            raw += bytes((255, 255, 255, alpha))

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def ensure_fx_default_texture(export_dir):
    """把内置默认贴图写到 art/fx_soft.png。返回路径(失败给 None)。

    只有**真的有发射器用得上它**时才调用(见 export_assets_pipeline):
    没有粒子就不该在 art/ 里多一张没人用的图。
    """
    target = os.path.join(export_dir, project.ART_DIR, FX_DEFAULT_TEXTURE)
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(fx_default_png())
        return target
    except OSError as exc:
        gk_log.warn(f"默认粒子贴图写入失败: {exc}")
        return None


def _emitter_basis_text(obj):
    """普通物体的朝向 -> .tscn 里 Transform3D 的前 9 个数。

    ── 和相机那套的区别与共同点(见 _camera_basis_text)────────────
    共同点:.tscn 里这 9 个数是**列优先**的 —— 顺序是
        x.x, y.x, z.x, x.y, y.y, z.y, x.z, y.z, z.z
    (三个基向量的**第一个分量**先连续出现,然后第二个、第三个)。
    按行优先写等于把矩阵转置了,Godot 里物体就歪了。
    不同点:普通物体的局部轴就是 X/Y/Z 本身(相机要看 -Z,得用
    right / up / back 拼)。坐标轴沿用 glTF 的 Y-up:Blender(x,y,z)
    -> Godot(x, z, -y),即 Godot 基 = C · R · C⁻¹(C 是那个换轴矩阵)。
    直接代入化简后就是:x 轴 = conv(第 0 列)、y 轴 = conv(第 2 列)、
    z 轴 = -conv(第 1 列)。

    刻意不 import mathutils —— 离线桩里没有它,而这个换算是"悄悄算错
    也没人看得出来"的那类代码(转置错了物体只是歪一点),必须能离线测。
    """
    identity = ("1.000000, 0.000000, 0.000000, "
                "0.000000, 1.000000, 0.000000, "
                "0.000000, 0.000000, 1.000000")
    try:
        matrix = obj.matrix_world
        rows = [[float(matrix[i][j]) for j in range(3)] for i in range(3)]
    except Exception:                                     # noqa: BLE001
        return identity

    def column(index):
        return (rows[0][index], rows[1][index], rows[2][index])

    def conv(vector):
        return (vector[0], vector[2], -vector[1])

    x_axis = conv(column(0))
    y_axis = conv(column(2))
    z_axis = tuple(-value for value in conv(column(1)))
    return (f"{_fmt(x_axis[0])}, {_fmt(y_axis[0])}, {_fmt(z_axis[0])}, "
            f"{_fmt(x_axis[1])}, {_fmt(y_axis[1])}, {_fmt(z_axis[1])}, "
            f"{_fmt(x_axis[2])}, {_fmt(y_axis[2])}, {_fmt(z_axis[2])}")


def build_emitter(obj, fx, is_3d=True, uid=0, texture_id="", parent=".",
                  attach_to_body=False):
    """算出一个粒子发射器要写进 .tscn 的全部文本。

    返回 {"node_lines": [...], "sub_resources": [...]};发射器要用的贴图
    由调用方写 ext_resource(文件名用 fx_texture_file(obj) 算)。

    parent:父节点路径(3D 是 'Particles' 或 'Colliders/名字';
            2D 是 'Particles')
    attach_to_body:挂在物体自己的物理体下面(这样它会**跟着物体的
            游戏逻辑一起动**——角色身上的火花就要这个)。那时位置由
            body 说了算,粒子节点只留朝向。
    """
    node_type = fx_node_type(fx, is_3d)
    cpu = node_type.startswith("CPU")

    def number(name, default=0.0):
        try:
            return float(getattr(fx, name, default))
        except (TypeError, ValueError):
            return float(default)

    amount = max(1, int(getattr(fx, "amount", 16) or 1))
    lifetime = max(0.05, number("lifetime", 1.2))
    one_shot = bool(getattr(fx, "one_shot", False))
    auto_play = bool(getattr(fx, "auto_play", True))
    preprocess = max(0.0, number("preprocess", 0.0))
    explosiveness = min(1.0, max(0.0, number("explosiveness", 0.0)))
    randomness = min(1.0, max(0.0, number("randomness", 0.3)))
    speed = max(0.0, number("speed", 1.0))
    spread = min(180.0, max(0.0, number("spread", 25.0)))
    gravity = number("gravity", 0.0)
    radius = max(0.0, number("radius", 0.0))
    start_size = max(0.0, number("start_size", 0.2))
    end_size = max(0.0, number("end_size", 0.0))
    local_coords = bool(getattr(fx, "local_coords", True))
    additive = bool(getattr(fx, "additive", False))

    direction = tuple(float(v) for v in
                      (getattr(fx, "direction", None) or (0.0, 0.0, 1.0)))
    while len(direction) < 3:
        direction += (0.0,)
    if is_3d:
        # Blender(x,y,z) -> Godot(x, z, -y)(和 _godot_position 同一套)
        dir_x, dir_y, dir_z = direction[0], direction[2], -direction[1]
        gravity_vec = f"Vector3(0, {_fmt(-gravity)}, 0)"   # 正数 = 往下
    else:
        # 2D:Godot 屏幕里 y 向下。Blender 的"上"(+y)对应屏幕上的"上"
        # (-y),所以 y 取反;这正是『方向』在 2D 里的直觉语义。
        dir_x, dir_y, dir_z = direction[0], -direction[1], 0.0
        gravity_vec = f"Vector2(0, {_fmt(gravity)})"

    if abs(dir_x) < 1e-9 and abs(dir_y) < 1e-9 and abs(dir_z) < 1e-9:
        # 方向填成"没有方向"时兜底成**朝上**:
        # 3D 的上是 +Y;2D 的上是屏幕上边 = -Y(Godot 2D 的 y 向下)。
        # ⚠ 2D 项目里默认值 (0,0,1) 正好会走到这里 —— 它的两个平面分量
        # 都是 0,兜底后就是"朝上喷",不会被判成没有方向。
        if is_3d:
            dir_z = 1.0
        else:
            dir_y = -1.0

    start_color = tuple(float(v) for v in
                        (getattr(fx, "start_color", None)
                         or (1.0, 1.0, 1.0, 1.0)))
    end_color = tuple(float(v) for v in
                      (getattr(fx, "end_color", None) or (1.0, 1.0, 1.0, 0.0)))
    while len(start_color) < 4:
        start_color += (1.0,)
    while len(end_color) < 4:
        end_color += (1.0,)

    prefix = f"fx{uid}"
    sub_resources = []
    ramp_id = ""
    curve_id = ""
    mesh_id = ""

    # ---- 颜色渐变(两种引擎写法不同:GPU 要纹理、CPU 直接要渐变)----
    grad_id = f"{prefix}_grad"
    sub_resources.append(
        '[sub_resource type="Gradient" id="%s"]\n'
        "colors = PackedColorArray(%s)" % (
            grad_id,
            ", ".join(_fmt(v) for v in (start_color + end_color))))
    if cpu:
        ramp_id = grad_id
    else:
        ramp_id = f"{prefix}_ramp"
        sub_resources.append(
            '[sub_resource type="GradientTexture1D" id="%s"]\n'
            "gradient = SubResource(\"%s\")" % (ramp_id, grad_id))

    # ---- 大小曲线(起 -> 终)。起终一样时不写,省一组没用的资源 ----
    ratio = (end_size / start_size) if start_size > 1e-9 else 0.0
    scale_curve_line = ""
    if abs(ratio - 1.0) > 1e-6:
        curve_id = f"{prefix}_curve"
        sub_resources.append(
            '[sub_resource type="Curve" id="%s"]\n'
            "_data = [Vector2(0, 1), 0.0, 0.0, 0, 0, "
            "Vector2(1, %s), 0.0, 0.0, 0, 0]\n"
            "point_count = 2" % (curve_id, _fmt(ratio)))
        if cpu:
            scale_curve_line = f"scale_amount_curve = SubResource(\"{curve_id}\")"
        else:
            curve_tex_id = f"{prefix}_scale"
            sub_resources.append(
                '[sub_resource type="CurveTexture" id="%s"]\n'
                "curve = SubResource(\"%s\")" % (curve_tex_id, curve_id))
            scale_curve_line = f"scale_curve = SubResource(\"{curve_tex_id}\")"

    # ---- 画什么 / 怎么画(3D 才有;2D 直接把贴图给节点)----
    draw_lines = []
    if is_3d:
        draw_id = f"{prefix}_draw"
        draw = ['[sub_resource type="StandardMaterial3D" id="%s"]' % draw_id,
                "transparency = 1",              # ALPHA
                "shading_mode = 0",              # UNSHADED(粒子不吃场景光)
                # 粒子颜色走"实例颜色",不开这一项等于全是白的(实测核对过)
                "vertex_color_use_as_albedo = true",
                "billboard_mode = 1"]            # 永远面向相机
        if additive:
            draw.append("blend_mode = 1")        # ADD
        if texture_id:
            draw.append(f'albedo_texture = ExtResource("{texture_id}")')
        sub_resources.append("\n".join(draw))
        mesh_id = f"{prefix}_mesh"
        sub_resources.append(
            '[sub_resource type="QuadMesh" id="%s"]\n'
            'material = SubResource("%s")' % (mesh_id, draw_id))
    elif additive:
        canvas_id = f"{prefix}_canvas"
        sub_resources.append(
            '[sub_resource type="CanvasItemMaterial" id="%s"]\n'
            "blend_mode = 1" % canvas_id)        # ADD
        draw_lines.append(f'material = SubResource("{canvas_id}")')

    if not cpu:
        process_id = f"{prefix}_proc"
        process = ['[sub_resource type="ParticleProcessMaterial" id="%s"]'
                   % process_id]
        if not is_3d:
            # 2D 粒子走同一套材质,但要关掉 Z 轴那一维(实测核对过)
            process.append("particle_flag_disable_z = true")
        process.append("emission_shape = %d" % (1 if radius > 1e-9 else 0))
        if radius > 1e-9:
            process.append(f"emission_sphere_radius = {_fmt(radius)}")
        process.append(f"direction = Vector3({_fmt(dir_x)}, {_fmt(dir_y)}, "
                       f"{_fmt(dir_z)})")
        process.append(f"spread = {_fmt(spread)}")
        process.append(f"initial_velocity_min = {_fmt(speed)}")
        process.append(f"initial_velocity_max = {_fmt(speed)}")
        process.append(f"gravity = {gravity_vec}")
        process.append(f"scale_min = {_fmt(start_size)}")
        process.append(f"scale_max = {_fmt(start_size)}")
        if scale_curve_line:
            process.append(scale_curve_line)
        process.append(f'color_ramp = SubResource("{ramp_id}")')
        sub_resources.append("\n".join(process))

    # ---- 节点本体 ----
    name = _sanitize_node_name(getattr(obj, "name", "") or "FX")
    parent_path = parent or "."
    lines = [f'[node name="{name}" type="{node_type}" parent="{parent_path}"]']
    if is_3d:
        basis = _emitter_basis_text(obj)
        if attach_to_body:
            origin = "0.000000, 0.000000, 0.000000"
        else:
            position = _godot_position(obj, is_3d)
            origin = (f"{_fmt(position[0])}, {_fmt(position[1])}, "
                      f"{_fmt(position[2])}")
        identity = ("1.000000, 0.000000, 0.000000, "
                    "0.000000, 1.000000, 0.000000, "
                    "0.000000, 0.000000, 1.000000, "
                    "0.000000, 0.000000, 0.000000")
        transform = f"Transform3D({basis}, {origin})"
        if transform != f"Transform3D({identity})":
            lines.append(f"transform = {transform}")
    elif not attach_to_body:
        position = _godot_position(obj, is_3d)
        lines.append(f"position = Vector2({_fmt(position[0])}, "
                     f"{_fmt(position[1])})")
    lines.append(f"emitting = {'true' if auto_play else 'false'}")
    lines.append(f"amount = {amount}")
    lines.append(f"lifetime = {_fmt(lifetime)}")
    if one_shot:
        lines.append("one_shot = true")
    if preprocess > 1e-9:
        lines.append(f"preprocess = {_fmt(preprocess)}")
    if explosiveness > 1e-9:
        lines.append(f"explosiveness = {_fmt(explosiveness)}")
    if randomness > 1e-9:
        lines.append(f"randomness = {_fmt(randomness)}")
    if not local_coords:
        lines.append("local_coords = false")
    if not is_3d and texture_id:
        lines.append(f'texture = ExtResource("{texture_id}")')
    if cpu:
        # CPU 粒子:所有参数**直接写在节点上**(没有 process_material)
        if is_3d:
            lines.append(f'mesh = SubResource("{mesh_id}")')
        lines.append(f"emission_shape = {1 if radius > 1e-9 else 0}")
        if radius > 1e-9:
            lines.append(f"emission_sphere_radius = {_fmt(radius)}")
        if is_3d:
            lines.append(f"direction = Vector3({_fmt(dir_x)}, "
                         f"{_fmt(dir_y)}, {_fmt(dir_z)})")
        else:
            lines.append(f"direction = Vector2({_fmt(dir_x)}, {_fmt(dir_y)})")
        lines.append(f"spread = {_fmt(spread)}")
        lines.append(f"gravity = {gravity_vec}")
        lines.append(f"initial_velocity_min = {_fmt(speed)}")
        lines.append(f"initial_velocity_max = {_fmt(speed)}")
        lines.append(f"scale_amount_min = {_fmt(start_size)}")
        lines.append(f"scale_amount_max = {_fmt(start_size)}")
        if scale_curve_line:
            lines.append(scale_curve_line)
        lines.append(f'color_ramp = SubResource("{ramp_id}")')
        if is_3d:
            lines.append(f'material_override = SubResource("{draw_id}")')
        else:
            lines.extend(draw_lines)
    else:
        # GPU 粒子:物理与外观全在 process_material 里
        lines.append(f'process_material = SubResource("{prefix}_proc")')
        if is_3d:
            lines.append(f'draw_pass_1 = SubResource("{mesh_id}")')
            lines.append(f'material_override = SubResource("{draw_id}")')
        else:
            lines.extend(draw_lines)
    return {"node_lines": lines, "sub_resources": sub_resources,
            "node_type": node_type}


def fx_codegen_map(scene, dimension="3"):
    """给**代码生成**准备的发射器表:{物体名: {"node_type": 节点类型}}。

    和静态资产的 asset_codegen_map 一个道理:『特效_播放』这类节点需要
    "这个名字的发射器在 Godot 里是什么节点"才知道该怎么写引用
    (GPUParticles3D 和 CPUParticles3D 是两个类型,写错了脚本编译不过)。
    """
    is_3d = str(dimension) != "2"
    out = {}
    for obj, fx in collect_emitters(scene):
        out[obj.name] = {"node_type": fx_node_type(fx, is_3d)}
    return out


def scene_materials(scene, enabled_only=True, file_names=None):
    """收集"对象名 -> 材质文件"的映射,给 write_world_scene 做材质覆盖用。

    返回 {对象名: (材质资源名, materials/ 下的文件名)}。

    为什么需要:glTF **不携带 NPR 外观**。卡通着色靠色阶切分、边缘光、
    面部 SDF,glTF 的 PBR 材质表达不了,所以 .glb 里全是素白的
    StandardMaterial3D。我们给每个 Blender 材质生成的 .tres 一直没被引用。

    有了这个映射,包装场景就能给每个对象加一条 material_override,
    直接指向 .tres —— glb 负责几何,tres 负责外观。
    """
    out = {}
    if scene is None:
        return out
    for obj in scene.objects:
        if getattr(obj, "type", "") != "MESH":
            continue
        if obj.get(PREVIEW_MARK) or is_helper_physics(obj) or is_collision_piece(obj):
            continue
        # 『不显示本体网格』的粒子发射器:网格**刻意不进**世界 glb,
        # 材质表里留一条只会让运行时刷"没找到网格节点"的告警。
        obj_fx = getattr(obj, "gk_fx", None)
        if obj_fx is not None and getattr(obj_fx, "hide_mesh", False):
            continue
        # ── 静态资产的**实例**不出现在材质表里(静态资产,用户 2026-10-05)──
        # 实例的网格来自**本体的 glb**,节点名也跟着本体走 —— 表里放
        # 『实例物体名』永远找不到网格节点(会刷"没找到网格节点"的告警)。
        # 本体的那一条就够了:它按名字匹配到所有实例内部的网格。
        physics = getattr(obj, "gk_physics", None)
        if physics is not None and getattr(physics, "asset_name", "") \
                and getattr(physics, "asset_instance", False):
            continue
        material = first_real_material(obj)
        if material is None:
            continue
        if file_names and material.name in file_names:
            file_name = file_names[material.name] + ".tres"
        else:
            file_name = safe_name(material.name, "material") + ".tres"
        out[obj.name] = (material.name, file_name)
    return out


def first_real_material(obj):
    """取物体的第一个**正经**材质,跳过工具生成的预览材质。

    为什么要跳:『显示描边预览』会往 obj.data.materials 里 append 一个
    叫 __GK描边预览材质__ 的纯色材质。如果不管它,导出时它会被当成
    物体的材质导出去 —— 用户会看到自己的模型在 Godot 里变成一个纯黑壳。
    """
    for material in (getattr(obj.data, "materials", None) or ()):
        if material is None:
            continue
        if material.name.startswith(TOOL_MATERIAL_PREFIX):
            continue
        return material
    return None


def _base_color_texture(material):
    """从 Blender 材质里找 Base Color 贴图，返回图片名(导出后 Godot 里的文件名)。"""
    if material is None or not material.use_nodes:
        return None
    for node in material.node_tree.nodes:
        if node.type != "BSDF_PRINCIPLED":
            continue
        socket = node.inputs.get("Base Color")
        if socket is None or not socket.is_linked:
            continue
        source = socket.links[0].from_node
        if source.type == "TEX_IMAGE" and source.image is not None:
            return source.image.name
    return None


def _base_color_rgb(material):
    """取材质的基础色，用作卡通材质的主色。"""
    fallback = (0.85, 0.82, 0.88)
    if material is None or not material.use_nodes:
        return fallback
    for node in material.node_tree.nodes:
        if node.type != "BSDF_PRINCIPLED":
            continue
        socket = node.inputs.get("Base Color")
        if socket is None:
            continue
        if socket.is_linked:
            continue
        value = socket.default_value
        return (float(value[0]), float(value[1]), float(value[2]))
    return fallback


# ==========================================================================
# 背面剔除(第九十四轮)
# ==========================================================================
# ── 为什么说"没做背面剔除"其实是个 bug,不是缺功能 ────────────────────
# Blender 的材质设置里一直有『背面剔除』(use_backface_culling),勾了预览
# 就真的剔 —— 但导出链路**根本没读它**:所有卡通材质都写死引用
# `gk_toon.gdshader`,而那份着色器是 `cull_disabled`(双面 —— 为了 MMD
# 的单面片头发/裙摆)。于是"Blender 里勾了剔除,Godot 里照旧双面":
# 预览在骗人,而且白白多画了背面那些面片。
#
# ── 怎么修 ──────────────────────────────────────────────────────────
# 勾了的材质 -> .tres 引用**剔面版**着色器 `gk_toon_cull.gdshader`。
# 变体不是仓库里的第二份源码,而是导出时从 `gk_toon.gdshader`
# **当场生成**的(只换 render_mode 那一行)。这样:
#   * 单一真相:改着色器只改那一份,不存在"两份改漂"的问题;
#   * 没勾(默认)的项目产物**一字不变**,也不会多出没用的文件;
#   * 变体只差一个词(`cull_disabled` -> `cull_back`),语义完全确定。
#
# ⚠ 单面片模型(MMD 头发 / 帽檐 / 裙摆)别勾:那些面片只有一面,
#   剔了会整片消失 —— 这也是当年默认双面的原因。
_TOON_SHADER = "gk_toon.gdshader"
CULL_SHADER_NAME = "gk_toon_cull.gdshader"
_BASE_RENDER_MODE = "render_mode unshaded, depth_draw_opaque, cull_disabled;"
_CULL_RENDER_MODE = "render_mode unshaded, depth_draw_opaque, cull_back;"


def material_culls_backfaces(material):
    """这个 Blender 材质勾了『背面剔除』吗。

    缺属性(老文件 / 假对象)时按**不剔除**处理 —— 和 Blender 的默认一致,
    宁可多画,也不让面片凭空消失。
    """
    return bool(getattr(material, "use_backface_culling", False))


def cull_shader_source(base_text):
    """从 gk_toon.gdshader 的源码生成剔面变体(只换 render_mode 一行)。

    ⚠ 找不到那一行就**报错**,不生成"看起来是变体、其实没剔面"的文件 ——
    那种失败是静默的:画面照旧、性能也没省,谁都不会发现。
    """
    if _BASE_RENDER_MODE not in base_text:
        raise ValueError(
            f"{_TOON_SHADER} 里找不到预期的那行:\n    {_BASE_RENDER_MODE}\n"
            "(改 render_mode 时请同步 asset_export 里的常量)")
    return base_text.replace(_BASE_RENDER_MODE, _CULL_RENDER_MODE, 1)


def write_cull_shader(export_dir, shader_res_dir="shaders"):
    """把剔面变体写进项目(有材质勾了剔除时才调)。返回写出的路径。"""
    source = os.path.join(shader_source_dir(), _TOON_SHADER)
    with open(source, "r", encoding="utf-8") as handle:
        text = handle.read()
    target = os.path.join(export_dir, shader_res_dir, CULL_SHADER_NAME)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(cull_shader_source(text))
    return target


def derived_shader_names():
    """导出时**当场生成**的着色器文件名(不在 shaders/ 目录里)。

    清理 / 清单那条链靠名字认亲(见 generated_files._plugin_shader_names):
    不把派生名并进去的话,项目里这个文件会被当成"用户自己放的东西",
    勾选取消之后也永远删不掉(实测过同类的残留问题)。
    """
    return (CULL_SHADER_NAME,)


def write_npr_material(export_dir, material, shader_res_dir="shaders"):
    """把 Blender 里的 NPR 预览材质导出成 Godot 的 .tres。

    这是"在 Blender 里调参"能成立的关键一步:参数存在材质自定义属性上,
    这里读出来直接写成 Godot 的 shader_parameter,两边不会各调一遍。

    返回 (文件路径, 参数个数);材质不是 NPR 材质时返回 (None, 0)。
    """
    # npr_preview 在模块级导入。
    # 注意:不能在函数内部写 from . import npr_preview —— 在 Blender 扩展
    # 环境里,函数局部的相对导入会因为模块路径解析方式不同而报
    # "'bl_ext' is not a package"。模块级导入没有这个问题。
    params = npr_preview.read_params(material)
    if not params:
        return None, 0

    name = safe_name(material.name, "material")
    texture = _base_color_texture(material)
    # 背面剔除(第九十四轮):勾了的材质换剔面版着色器(顺手把变体写进项目)
    shader_name = _TOON_SHADER
    if material_culls_backfaces(material):
        shader_name = CULL_SHADER_NAME
        write_cull_shader(export_dir, shader_res_dir)

    textures_block = ""
    albedo_ref = "null"
    steps = 2
    if texture:
        tex_name = safe_name(texture, "texture") + ".png"
        textures_block = (
            f'[ext_resource type="Texture2D" path="res://{project.ART_DIR}/{tex_name}" '
            f'id="2_albedo"]\n'
        )
        albedo_ref = 'ExtResource("2_albedo")'
        steps = 3

    lines = [
        f'[gd_resource type="ShaderMaterial" load_steps={steps} format=3]',
        "",
        f'[ext_resource type="Shader" path="res://{shader_res_dir}/{shader_name}" '
        f'id="1_shader"]',
    ]
    if textures_block:
        lines.append(textures_block.rstrip("\n"))
    lines.append("")
    lines.append("[resource]")
    lines.append('shader = ExtResource("1_shader")')
    lines.append(f"shader_parameter/albedo_texture = {albedo_ref}")
    # 必须显式写开关:hint_default_white 让未指定贴图时 texture() 恒返回白色,
    # 运行时区分不出"没贴图"和"贴图是白的"。
    lines.append(f"shader_parameter/use_albedo_texture = "
                 f"{1.0 if texture else 0.0:.1f}")

    for uniform, value in params.items():
        if isinstance(value, (tuple, list)):
            r, g, b = float(value[0]), float(value[1]), float(value[2])
            lines.append(
                f"shader_parameter/{uniform} = Color({r:.6f}, {g:.6f}, {b:.6f}, 1)"
            )
        else:
            lines.append(f"shader_parameter/{uniform} = {float(value):.6f}")

    path = os.path.join(export_dir, "materials", f"{name}.tres")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return path, len(params)


def _scene_prefix(scene_name):
    """多场景导出时,给"按场景各写一份"的资源加的文件名前缀。

    单场景时 scene_name == "World" —— 不加,老项目产物与以前逐字一致。
    见 _unique_file_names / _char_glb_name 的说明。
    """
    return "" if scene_name == "World" else f"{scene_name}_"


def write_materials(export_dir, objects, shader_res_dir="shaders", prefix=""):
    """给每个 Blender 材质生成一份 Godot 卡通材质。返回 [(材质名, 文件路径, 有无贴图)]。

    objects 传 None 表示"整个场景里所有网格的材质" —— 整场景导出时用这个,
    因为场景里每个对象都需要自己的 .tres 才能显示成卡通。

    prefix 见 _scene_prefix:多场景导出必须各写一份,否则两关的材质文件
    同名互相覆盖(实测:关卡2 的红色角色材质盖掉关卡1 的东墙材质)。
    """
    # ── 卡通材质总开关 ──────────────────────────────────────────────
    # 关掉 = 不做任何材质转换(Godot 直接用 glTF 里带过去的 PBR 材质),
    # 用户就能拿运行画面和 Blender 预览逐项对比("想看看细节")。
    try:
        import bpy as _bpy
        _scene = getattr(_bpy.context, "scene", None)
        _settings = getattr(_scene, "gk_settings", None) if _scene else None
        if _settings is not None and not getattr(_settings, "toon_materials",
                                                 True):
            print("[GK] 卡通材质已关闭 —— 按原样导出材质(PBR),"
                  "运行画面会和 Blender 预览一致")
            return []
    except Exception:
        pass

    if objects is None:
        import bpy

        scene = getattr(bpy.context, "scene", None)
        objects = [obj for obj in (scene.objects if scene else [])
                   if getattr(obj, "type", "") == "MESH"
                   and not obj.get(PREVIEW_MARK)
                   and not is_helper_physics(obj)
                   and not is_collision_piece(obj)]

    # ── 文件名先去重,再逐个写 ──────────────────────────────────────
    # 不过滤重名的话,M MD 那种几十个材质槽的模型会全部落到同一个
    # `material.tres` 上互相覆盖(实测日志里连续 41 次"写入 material.tres")。
    wanted = []
    for obj in objects:
        for slot in getattr(obj, "material_slots", ()):
            material = slot.material
            if material is None or material.name in wanted:
                continue
            # 跳过工具生成的辅助材质(描边预览的纯色壳)。
            # 不跳的话它会变成一条 .tres,用户可能在 Godot 里选中它,
            # 把模型改成纯黑。
            if material.name.startswith(TOOL_MATERIAL_PREFIX):
                continue
            wanted.append(material.name)
    file_names = _unique_file_names(wanted, prefix=prefix)

    written = []
    # 剔面变体**整个导出只写一次**(第一个勾了剔除的材质触发,见上)
    cull_written = False
    for obj in objects:
        for slot in getattr(obj, "material_slots", ()):
            material = slot.material
            if material is None or material.name not in file_names:
                continue
            if material.name in {item[0] for item in written}:
                continue
            name = file_names[material.name]
            rgb = _base_color_rgb(material)
            texture = _base_color_texture(material)
            # 背面剔除(第九十四轮):Blender 材质勾了『背面剔除』-> 这个
            # .tres 引用剔面版着色器;没勾的照旧双面(老项目产物一字不变)。
            shader_name = _TOON_SHADER
            if material_culls_backfaces(material):
                shader_name = CULL_SHADER_NAME
                if not cull_written:
                    write_cull_shader(export_dir, shader_res_dir)
                    cull_written = True

            textures_block = ""
            albedo_ref = "null"
            steps = 2
            if texture:
                tex_name = safe_name(texture, "texture") + ".png"
                textures_block = (
                    f'[ext_resource type="Texture2D" '
                    f'path="res://{project.ART_DIR}/{tex_name}" id="2_albedo"]\n'
                )
                albedo_ref = 'ExtResource("2_albedo")'
                steps = 3

            text = TOON_MATERIAL_TEMPLATE.format(
                steps=steps,
                shader_path=f"res://{shader_res_dir}/{shader_name}",
                textures=textures_block,
                r=rgb[0], g=rgb[1], b=rgb[2],
                albedo_ref=albedo_ref,
                use_texture=1.0 if texture else 0.0,
            )
            path = os.path.join(export_dir, project.MATERIALS_DIR, f"{name}.tres")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            written.append((material.name, path, bool(texture)))
    return written


def npr_get(material, name, default=None):
    """读材质参数(中文键/英文键都认)。

    单独包一层是为了 asset_export 不用在模块顶层 import npr_preview ——
    那会绕成循环导入(npr_preview 也 import 别的东西)。
    """
    from . import npr_preview

    return npr_preview.get_param(material, name, default)


def export_textures(export_dir, materials):
    """把材质用到的贴图导出成 PNG 到 art/。

    返回 [(图片名, 路径)]。

    ── 为什么必须有这一步 ──────────────────────────────────────────
    生成的 .tres 里有这么一行:
        [ext_resource type="Texture2D" path="res://art/xxx.png" ...]
    如果那个 PNG 不存在,Godot 加载 .tres 会失败,进而**整个场景实例化失败**
    ——表现是打开游戏一片空白/一片灰,而日志里只有一行
    "Failed loading resource"。这个坑实测踩过,而且是"卡通材质显示不出来"
    的直接原因:普通图片材质直接挂在模型上、不经过 .tres,所以看不出问题。

    glTF 会把贴图**内嵌**进 .glb,但那只对 glb 自己的材质有效;
    我们的 .tres 是独立文件,必须自己把 PNG 落盘。
    """
    import bpy

    out = []
    seen = set()
    target_dir = os.path.join(export_dir, project.ART_DIR)
    os.makedirs(target_dir, exist_ok=True)

    for material in materials or ():
        if material is None:
            continue
        # ── 基础色贴图必须按 .tres 期望的**那个名字**导出 ─────────────
        # 踩过的坑:`.tres` 里写的是 `res://art/{safe_name(图片名)}.png`,
        # 比如图片叫 `hair.png` -> 引用 `hair_png.png`;
        # 而这里原来只按 `_material_image_names()` 导出,名字对不上 ——
        # Godot 报 "Resource file not found: res://art/hair_png.png",
        # 材质加载失败,模型回到白模。
        #
        # 所以基础色这张图先按 .tres 的名字单独导一份,再导其余的。
        base_name = _base_color_texture(material)
        if base_name and base_name not in seen:
            seen.add(base_name)
            base_image = bpy.data.images.get(base_name)
            if base_image is not None and base_image.size[0] > 0:
                # 名字算法**必须和 write_materials 里写进 .tres 的完全一致**:
                # 那边是 `safe_name(texture, "texture") + ".png"`,
                # 这里少一步就会变成两套名字,Godot 照样找不到文件。
                file_name = safe_name(base_name, "texture") + ".png"
                path = os.path.join(target_dir, file_name)
                try:
                    _save_image(base_image, path)
                    out.append((base_name, path))
                except Exception as exc:
                    gk_log.warn(f"贴图 {base_name} 导出失败: {exc}")

        for name in _material_image_names(material):
            if name in seen:
                continue
            seen.add(name)
            image = bpy.data.images.get(name)
            if image is None or image.size[0] == 0:
                continue
            file_name = safe_name(name, "texture") + ".png"
            path = os.path.join(target_dir, file_name)
            try:
                _save_image(image, path)
            except Exception as exc:
                gk_log.warn(f"贴图 {name} 导出失败: {exc}")
                continue
            out.append((name, path))
    return out


def ui_image_names(scene):
    """这个场景的逻辑图里,界面节点引用到的 Blender **图片名**。

    ── 谁在用(第三十七轮:UI 设计)──────────────────────────────────
    『界面_图片』(图标 / logo)和『界面_对话框』的『立绘』填的是
    `bpy.data.images` 里的图片名(`res://` 开头的路径原样用,不在这);
    第三十八轮的『界面_九宫格』(标题条底图)也填同一套图片名
    —— 漏掉它的话图不会落盘,游戏里 load 出来是 null(实测踩过)。

    ── 为什么要按**场景**收 ────────────────────────────────────────
    一个 .blend 里可以有多个场景(关卡1 / 关卡2),各用各的图;
    按场景收 = 每个 .tscn 的 art/ 里只放它真正要用的那几张,
    不会把别的关卡的大图也一起拖进来。

    ── 为什么按 `resolve_scene_tree` 取图 ──────────────────────────
    和"生成脚本"用的是同一个函数 —— 场景设置里选的那棵逻辑图。
    两处各选各的话,会出现"脚本里有图片引用、但 PNG 没导出"的
    Resource not found(材质那次吃过)。
    """
    from . import exporter

    tree, _why = exporter.resolve_scene_tree(scene)
    if tree is None:
        return []
    names = []
    for node in getattr(tree, "nodes", ()) or ():
        bl_idname = getattr(node, "bl_idname", "")
        if bl_idname not in ("GKNodeUiImage", "GKNodeUiDialog",
                             "GKNodeUiNinePatch"):
            continue
        raw = str(getattr(node, "gk_image", "") or "").strip()
        if raw and not raw.startswith("res://") and raw not in names:
            names.append(raw)
    return names


def export_ui_images(directory, scene):
    """把界面节点引用的图片落盘成 `art/ui_<名字>_<摘要>.png`。

    返回 [(图片名, 路径)]。文件名算法在 `curve_assets.image_file_name`
    —— 和生成的脚本里那条 `res://art/...` 引用**共用同一个函数**,
    两边不会各算一套(曲线 / 材质都吃过"名字对不上"的亏)。
    """
    import bpy

    from . import curve_assets

    out = []
    target_dir = os.path.join(directory, project.ART_DIR)
    for name in ui_image_names(scene):
        image = bpy.data.images.get(name)
        if image is None:
            gk_log.warn(f"界面图片『{name}』不在工程里(可能被删了),已跳过")
            continue
        if image.size[0] == 0 and image.size[1] == 0:
            gk_log.warn(f"界面图片『{name}』没有像素数据(没打包 / 没加载),已跳过")
            continue
        path = os.path.join(target_dir, curve_assets.image_file_name(name))
        try:
            os.makedirs(target_dir, exist_ok=True)
            _save_image(image, path)
        except Exception as exc:                          # noqa: BLE001
            gk_log.warn(f"界面图片『{name}』导出失败: {exc}")
            continue
        out.append((name, path))
    return out


def _save_image(image, path):
    """把 Blender 图像存成 PNG。

    优先用 save_render —— 它对**程序生成**的图像也有效
    (bpy.data.images.new() 造的图没有磁盘路径,save() 会失败)。
    失败则退回 save()。
    """
    import bpy

    try:
        image.save_render(path)
        return
    except Exception:
        pass
    scene = getattr(bpy.context, "scene", None)
    settings = scene.render.image_settings if scene is not None else None
    old_format = getattr(settings, "file_format", None)
    if settings is not None:
        settings.file_format = "PNG"
    try:
        image.filepath_raw = path
        image.file_format = "PNG"
        image.save()
    finally:
        if settings is not None and old_format is not None:
            settings.file_format = old_format


def _material_image_names(material):
    """列出材质里用到的所有图像名(包括非 Base Color 的,比如 SDF)。"""
    names = []
    if material is None or not getattr(material, "use_nodes", False):
        return names
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return names
    for node in tree.nodes:
        image = getattr(node, "image", None)
        if image is not None and getattr(image, "name", ""):
            names.append(image.name)
    return names


def glb_scene_nodes(export_dir, model_name="World"):
    """解析 .glb,返回可以直接建成 Godot 节点的信息。

    返回 {"meshes": {物体名: 网格资源名}, "cameras": [...], "lights": [...],
          "nodes": [...]}。

    ── 为什么要解析 glb 而不直接 instance 它 ────────────────────────
    包装场景原本是 `[node name="World" instance=ExtResource("1_world")]`,
    然后另外声明节点给实例内部的子节点加 material_override。
    **这条路在 Godot 里走不通**:

        WARNING: Parent path './World' for node 'Ball' has vanished
                 when instantiating: 'res://scenes/World.tscn'.

    glb 实例的内部子节点不能被外部节点覆盖 —— 材质覆盖**从来没生效过**,
    用户在 Godot 里看到的永远是白模。加 editable_children = true 也没用
    (实测过)。

    所以改成:把 glb 里的每个对象展开成一个 MeshInstance3D 节点,
    网格用 ExtResource 指向 glb 里的 mesh 资源(形如 "1_world/BallMesh"),
    材质直接写在 mesh 上。这样**没有任何覆盖**,Godot 不会抱怨,
    而且 .tscn 里就能看到完整的对象树。
    """
    result = {"meshes": {}, "cameras": [], "lights": [], "nodes": [],
              "materials": {}, "root_matrix": None}
    path = os.path.join(export_dir or "", project.ART_DIR, f"{model_name}.glb")
    if not os.path.isfile(path):
        return result
    try:
        import json
        import struct

        with open(path, "rb") as handle:
            data = handle.read()
        offset = 12
        payload = None
        while offset < len(data):
            length, kind = struct.unpack_from("<II", data, offset)
            offset += 8
            if kind == 0x4E4F534A:      # "JSON"
                payload = json.loads(data[offset:offset + length].decode("utf-8"))
                break
            offset += length
        if payload is None:
            return result
    except (OSError, ValueError, struct.error):
        return result

    meshes = payload.get("meshes", [])
    materials = payload.get("materials", [])

    for index, mesh in enumerate(meshes):
        # glTF 的网格名 = Blender 的 mesh data 名,不一定等于对象名,
        # 所以按**节点**去找更靠得住 —— 下面遍历节点时再填。
        result["materials"][index] = mesh.get("name", f"Mesh{index}")

    for node in payload.get("nodes", []):
        name = node.get("name", "")
        if "mesh" in node:
            mesh_index = node["mesh"]
            mesh_name = (meshes[mesh_index].get("name", "") if
                         mesh_index < len(meshes) else "")
            primitives = (meshes[mesh_index].get("primitives", [])
                          if mesh_index < len(meshes) else [])
            material_index = None
            if primitives:
                material_index = primitives[0].get("material")
            material_name = ""
            if material_index is not None and material_index < len(materials):
                material_name = materials[material_index].get("name", "")
            result["meshes"][name] = {
                "mesh_resource": mesh_name or name,
                "primitives": len(primitives),
                "material": material_name,
            }
        if "camera" in node:
            result["cameras"].append(name)
        if node.get("extensions", {}).get("KHR_lights_punctual"):
            result["lights"].append(name)
        result["nodes"].append(name)

    # glb 的根节点变换(第一个 scene 的第一个 node)。
    # 角色 glb 里它带着"角色在 Blender 里的世界变换",instance 到原点的
    # 角色节点下面会**再偏移一次**,所以调用方要拿它算逆变换抵消掉。
    scenes = payload.get("scenes", [])
    nodes_all = payload.get("nodes", [])
    if scenes:
        roots = scenes[0].get("nodes") or []
        if roots and roots[0] < len(nodes_all):
            result["root_matrix"] = _glb_node_matrix(payload, nodes_all[roots[0]])
    return result


def _glb_node_matrix(payload, node):
    """把 glTF 节点的 TRS 转成 Godot 的 Transform3D 文本。

    glTF 用 TRS(translation / rotation 四元数 / scale)**列优先**矩阵,
    Godot 的 Transform3D 也是列优先 —— 所以顺序一致,可以直接展开。
    但**有旋转/缩放时必须算矩阵**,不能只写平移:
    相机和太阳的朝向全靠这个,只写平移它们就会一起朝上。
    """
    translation = node.get("translation", [0.0, 0.0, 0.0])
    rotation = node.get("rotation", [0.0, 0.0, 0.0, 1.0])     # x, y, z, w
    scale = node.get("scale", [1.0, 1.0, 1.0])

    x, y, z, w = (float(rotation[0]), float(rotation[1]),
                  float(rotation[2]), float(rotation[3]))
    sx, sy, sz = (float(scale[0]), float(scale[1]), float(scale[2]))
    # 四元数 -> 3x3 旋转矩阵(标准公式)
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    m = [
        [1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy)],
        [2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx)],
        [2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)],
    ]
    # 乘上缩放(列缩放)
    for row in range(3):
        m[row][0] *= sx
        m[row][1] *= sy
        m[row][2] *= sz

    # Godot 的 Transform3D 文本里,9 个数依次是 **X 轴、Y 轴、Z 轴**三个向量,
    # 然后才是原点。
    #
    # ── 这个顺序我搞错过,把判别方法记下来 ──────────────────────────
    # 错误经历:拿一个"看起来足够随机"的矩阵 (1,2,3,4,5,6,7,8,9) 去试,
    # 结论是"列优先",于是照此生成 —— 结果相机方向整体反了。
    #
    # **那个测试矩阵没有区分力**:在这个矩阵上,按行读和按列读得到的
    # "前方向"符号恰好一样,所以它证明不了任何事。
    #
    # 可靠的判别方法:用一个真实的不对称矩阵(相机旋转)去比 ——
    # 两种读法给出的前方向符号相反,拿 Godot 实测值一对就知道是哪种。
    values = [m[0][0], m[1][0], m[2][0],
              m[0][1], m[1][1], m[2][1],
              m[0][2], m[1][2], m[2][2],
              float(translation[0]), float(translation[1]), float(translation[2])]
    return ", ".join(_fmt(v) for v in values)


def build_model_nodes(export_dir, model_name, node_names, materials_map,
                      resource_id="1_world", indent=""):
    """把 glb 里的对象展开成 Godot 的 MeshInstance3D 节点文本。

    返回 (节点文本, 用到的网格资源名列表)。

    为什么要展开而不是 instance glb:见 glb_scene_nodes 的说明 ——
    glb 实例的内部子节点没法从外部覆盖材质,加 editable_children 也没用。
    展开之后材质直接挂在 mesh 上,不需要任何覆盖。
    """
    import json
    import struct

    path = os.path.join(export_dir or "", project.ART_DIR, f"{model_name}.glb")
    if not os.path.isfile(path):
        return "", []
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        offset = 12
        payload = None
        while offset < len(data):
            length, kind = struct.unpack_from("<II", data, offset)
            offset += 8
            if kind == 0x4E4F534A:
                payload = json.loads(data[offset:offset + length].decode("utf-8"))
                break
            offset += length
        if payload is None:
            return "", []
    except (OSError, ValueError, struct.error):
        return "", []

    meshes = payload.get("meshes", [])
    lines = []
    used = []
    wanted = set(node_names or ())
    for node in payload.get("nodes", []):
        name = node.get("name", "")
        if "mesh" not in node or (wanted and name not in wanted):
            continue
        mesh_index = node["mesh"]
        if mesh_index >= len(meshes):
            continue
        mesh_resource = meshes[mesh_index].get("name", "") or name
        used.append(mesh_resource)
        lines.append(f'{indent}[node name="{_sanitize_node_name(name)}" '
                     f'type="MeshInstance3D" parent="."]')
        lines.append(f'{indent}transform = Transform3D('
                     f'{_glb_node_matrix(payload, node)})')
        lines.append(f'{indent}mesh = ExtResource("{resource_id}/{mesh_resource}")')
        material_file = (materials_map or {}).get(name, (None, None))[1]
        if material_file:
            lines.append(f'{indent}material_override = '
                         f'ExtResource("res://{project.MATERIALS_DIR}/'
                         f'{material_file}")')
        lines.append("")
    return "\n".join(lines), used
def meshes_without_material(scene):
    """找出"会被导出但一个材质都没有"的网格物体名。

    ── 为什么要专门查这个 ──────────────────────────────────────────
    材质是**按物体上的材质槽**导出的:`scene_materials()` 只收集
    `first_real_material(obj)` 拿得到东西的物体。一个材质槽都没有的网格
    会被整个跳过 —— 于是 .glb 里它就是一个没有材质的网格,
    Godot 用默认材质渲染,**看起来是一片灰**。

    实测踩过:用户建了个 Cube 当角色,忘了给材质,导出的角色场景里
    那个方块就是灰的,他以为"渲染坏了"。这不是渲染问题,
    是那个物体在 Blender 里就没有材质。
    """
    out = []
    if scene is None:
        return out
    for obj in scene.objects:
        if getattr(obj, "type", "") != "MESH":
            continue
        if obj.get(PREVIEW_MARK) or is_helper_physics(obj) or is_collision_piece(obj):
            continue
        if not obj.data.materials:
            out.append(obj.name)
            continue
        if first_real_material(obj) is None:
            out.append(obj.name)
    return out


# ── 已删除:_blender_world_to_godot_transform() ──────────────────────
#
# 曾经写过一个"把 Blender 物体的世界变换手写成 Godot Transform3D"的函数,
# 按 Godot(x,y,z) = Blender(x, z, -y) 置换基向量。**这条路是错的,别再走。**
#
# 实测症状:带旋转/缩放时矩阵对不上 —— 缩放跑到别的轴上、原点也抵消不干净,
# 角色网格因此和碰撞体错开,画面表现为重影。
#
# 原因是 **Godot 的 glTF 导入是跨坐标系的(Y-up),不是简单的轴置换**,
# 靠自己拼矩阵必然会偏。
#
# 正确做法(现在用的):位置由 .tscn 里的**角色节点**说了算 —— 游戏逻辑读的
# 正是节点的 `global_position`(拾取距离、掉出回出生点),节点必须站在物体的
# 世界坐标上。glb 根节点里那份**同样的**变换用 `_glb_root_inverse_transform`
# 的逆变换抵消掉,写在 Model 节点上。
#
# 反过来做(节点留在原点、网格全交给 glb 摆)是错的:节点和网格会分开,
# 碰撞体、拾取距离、掉出判定全部错位(实测)。


def _glb_root_inverse_transform(export_dir, model_name, obj, is_3d=True):
    """算出抵消角色 glb 内部根节点变换的 Transform3D 文本(没有偏移时返回 "")。

    ── 为什么需要它(踩过的坑)────────────────────────────────────────
    glTF 导出会把物体的**世界变换**写进 glb 的根节点。于是角色 glb 的根
    节点带着"角色在 Blender 里的位置",把它 instance 到一个位于原点的
    CharacterBody3D 下面,网格就会**再偏移一次** ——
    和碰撞体(按物体世界坐标生成)错开,表现是
    **重影 + 深度冲突闪烁**(用户实测:"模型一闪一闪的")。

    给它一个逆变换就能把这份内部偏移抵消掉:网格正好落在物体世界坐标上,
    和碰撞体严丝合缝。
    """
    info = glb_scene_nodes(export_dir, model_name)
    raw = info.get("root_matrix")
    if not raw:
        return ""
    # _glb_node_matrix 返回的是 "a, b, c, ..." 这样的文本(12 个数)
    try:
        m = [float(part.strip()) for part in str(raw).split(",")]
    except ValueError:
        return ""
    if len(m) != 12:
        return ""
    identity = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
    if all(abs(m[i] - identity[i]) < 1e-9 for i in range(12)):
        return ""
    # 12 个数按 Godot 的列优先顺序:前 9 个是基向量,后 3 个是平移
    a = [[m[0], m[3], m[6]],
         [m[1], m[4], m[7]],
         [m[2], m[5], m[8]]]
    t = [m[9], m[10], m[11]]

    det = (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
           - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
           + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    if abs(det) < 1e-12:
        return ""
    inv = [
        [(a[1][1] * a[2][2] - a[1][2] * a[2][1]) / det,
         (a[0][2] * a[2][1] - a[0][1] * a[2][2]) / det,
         (a[0][1] * a[1][2] - a[0][2] * a[1][1]) / det],
        [(a[1][2] * a[2][0] - a[1][0] * a[2][2]) / det,
         (a[0][0] * a[2][2] - a[0][2] * a[2][0]) / det,
         (a[0][2] * a[1][0] - a[0][0] * a[1][2]) / det],
        [(a[1][0] * a[2][1] - a[1][1] * a[2][0]) / det,
         (a[0][1] * a[2][0] - a[0][0] * a[2][1]) / det,
         (a[0][0] * a[1][1] - a[0][1] * a[1][0]) / det],
    ]
    # 逆平移 = -inv3x3 * t
    it = [-(inv[r][0] * t[0] + inv[r][1] * t[1] + inv[r][2] * t[2])
          for r in range(3)]
    values = [inv[0][0], inv[1][0], inv[2][0],
              inv[0][1], inv[1][1], inv[2][1],
              inv[0][2], inv[1][2], inv[2][2],
              it[0], it[1], it[2]]
    return "Transform3D(" + ", ".join(_fmt(v) for v in values) + ")"


def collect_assets(scene):
    """把场景里的物体按『资产名』分组(静态资产,用户 2026-10-05)。

    返回 `{资产名: {"source": 本体物体或 None, "members": [全部物体]}}`。

    ── 规则(和面板上写的一致)────────────────────────────────────
    * 『资产名』留空 = 普通物体,不参与;
    * 同名的算同一个资产:没勾『作为实例』的是**本体**(定义模型),
      勾了的是**实例**;
    * 一个资产可以有**多个**不带实例标记的物体(用户复制了本体忘了勾)——
      取第一个当本体,并给一条告警(见 validate_assets)。
    """
    grouped = {}
    if scene is None:
        return grouped
    for obj in scene.objects:
        if getattr(obj, "type", "") != "MESH":
            continue
        physics = getattr(obj, "gk_physics", None)
        if physics is None or not getattr(physics, "enabled", True):
            continue
        name = str(getattr(physics, "asset_name", "") or "").strip()
        if not name:
            continue
        entry = grouped.setdefault(name, {"source": None, "members": [],
                                          "extra_sources": []})
        entry["members"].append(obj)
        if getattr(physics, "asset_instance", False):
            continue
        if entry["source"] is None:
            entry["source"] = obj
        else:
            entry["extra_sources"].append(obj)
    return grouped


def validate_assets(scene):
    """资产设置的告警(返回可读文本列表,给导出报告用)。"""
    warnings = []
    for name, entry in collect_assets(scene).items():
        if entry["source"] is None:
            first = entry["members"][0].name if entry["members"] else "?"
            warnings.append(
                f"资产『{name}』只有实例、没有本体 —— 导出的场景里这些实例"
                f"**没有模型**(只剩碰撞代理)。把其中一份(比如『{first}』)"
                f"的『作为实例』取消勾选,让它当本体")
        if entry["extra_sources"]:
            names = "、".join(obj.name for obj in entry["extra_sources"][:3])
            warnings.append(
                f"资产『{name}』有多个本体({entry['source'].name}、{names})—— "
                f"用『{entry['source'].name}』的模型,其余的要勾上『作为实例』")
    return warnings


def asset_model_name(name, scene_name="World"):
    """资产 glb 的文件名(多场景时带场景前缀,避免两关的同名资产互相覆盖)。

    ── 为什么名字后面挂一段摘要(和曲线资源同一个坑)────────────────
    `safe_name` 把非 ASCII 字符全删掉:『树』『石头』都会落回 "asset" ——
    于是**两个中文名的资产写同一个 glb**,后导的盖掉前一个,
    用户在 Godot 里看到"两份资产长得一模一样"。摘要让一个资产名一个文件。
    """
    import zlib
    suffix = f"{zlib.crc32(str(name).encode('utf-8')) & 0xFFFFFFFF:08x}"
    return _scene_prefix(scene_name) + f"asset_{safe_name(name, 'asset')}_{suffix}"


# 碰撞形状 -> 运行时创建实例用的编号。必须和 codegen 里 `_gk_spawn_asset`
# 的 match 分支一一对应(对不上会退化成盒子 —— 不致命,但会白占地方)。
ASSET_SHAPE_KINDS = {"box": 0, "sphere": 1, "capsule": 2, "cylinder": 3,
                     "convex": 4}


def _shape_entry(built, offset, is_3d):
    """一个碰撞形状 -> 运行时创建用的字典(kind / 尺寸 / 偏移 / 凸包点)。"""
    entry = {
        "kind": ASSET_SHAPE_KINDS.get(str(built.get("shape")), 0),
        "dims": tuple(built.get("dims") or (1.0, 1.0, 1.0)),
        "radius": float(built.get("radius") or 0.5),
        "height": float(built.get("height") or 1.0),
        "offset": tuple(offset),
    }
    # 凸包要带上点集(运行时没有网格可算,只能导出时算好)
    points = list(built.get("points") or [])
    if points and is_3d:
        entry["points"] = points
    return entry


def asset_shape_entries(source, is_3d=True):
    """运行时『资产_创建实例』要造的**形状表**:本体主形状 + 所有碰撞块。

    ── 为什么在导出侧算好 ──────────────────────────────────────────
    运行时摆出来的实例必须和本体"长得一样"(本体能挡人,生成的这份
    也得能挡)—— 形状、尺寸、偏移、凸包点全部走 build_collision /
    _piece_offset_godot,和 .tscn 里那份**同一套数据**,不会两边打架。
    """
    if source is None:
        return []
    entries = []
    main = build_collision(source, is_3d, getattr(source, "gk_physics", None))
    if main is not None:
        entries.append(_shape_entry(main, main.get("offset") or (0.0, 0.0, 0.0),
                                    is_3d))
    for piece in collision_pieces(source):
        built = build_collision(piece, is_3d, getattr(piece, "gk_physics", None))
        if built is None:
            continue
        entries.append(_shape_entry(
            built, _piece_offset_godot(piece, source, is_3d), is_3d))
    return entries


def _export_scene_name(scene):
    """这个场景在这次导出里的名字(见 `_scene_prefix` 的规矩)。

    `.blend` 里只有**一个**场景时是 "World"(不加前缀,老产物不变),
    多个场景才按场景名加前缀 —— 和 export_all_scenes 的判断一致。
    """
    try:
        from . import exporter
        multi = len(exporter.exportable_scenes()) > 1
    except Exception:                                     # noqa: BLE001
        multi = False
    return _safe_scene_name(getattr(scene, "name", "")) if multi else "World"


def asset_codegen_map(scene, dimension="3", asset_models=None, scene_name=""):
    """给**代码生成**准备的资产表:{资产名: {glb 路径 / 碰撞参数}}(用户 2026-10-05)。

    ── 为什么要在导出侧算 ──────────────────────────────────────────
    『资产_创建实例』是**运行时**节点:按资产名生成一份新实例,所以它需要
    "这个名字对应哪份 glb、碰撞生成多大"—— 而资产名是用户在 Blender 里
    随手起的,只有导出侧知道这份对应关系。算好后经 build_settings 塞进
    代码生成选项(见 exporter.build_settings),节点那边只管用。

    返回的每项:
        {"path": "res://art/asset_x.glb", "shape": 形状编号(-1 = 不带碰撞),
         "dims"/"radius"/"height": 碰撞尺寸(和 .tscn 里那份用同一组数),
         "layer"/"mask": 碰撞层}
    """
    out = {}
    if scene is None:
        return out
    is_3d = str(dimension) != "2"
    models = dict(asset_models or {})
    if not scene_name:
        scene_name = _export_scene_name(scene)
    for name, entry in collect_assets(scene).items():
        source = entry.get("source")
        model_name = models.get(name) or asset_model_name(name, scene_name)
        info = {"path": f"res://{project.ART_DIR}/{model_name}.glb"}
        built = (build_collision(source, is_3d,
                                 getattr(source, "gk_physics", None))
                 if source is not None else None)
        if built is None:
            info["shape"] = -1
        else:
            info["shape"] = ASSET_SHAPE_KINDS.get(str(built.get("shape")), 0)
            info["dims"] = tuple(built.get("dims") or (1.0, 1.0, 1.0))
            info["radius"] = float(built.get("radius") or 0.5)
            info["height"] = float(built.get("height") or 1.0)
            info["layer"] = int(built.get("layer") or 1)
            info["mask"] = int(built.get("mask") or 1)
        # ── 运行时创建要造的**形状表**(本轮:多碰撞体 + 凸包)──────────
        # 旧字段(shape/dims/…)留着给老测试 / 老调用方;新代码一律用 shapes。
        info["shapes"] = (asset_shape_entries(source, is_3d)
                          if built is not None else [])
        out[name] = info
    return out


def _base_class_for(scene, obj, dimension="3"):
    """算出某个物体对应的脚本基类。

    脚本的 `extends` 和场景里节点的 `type` 必须是同一个值 —— 否则 Godot
    直接拒绝加载脚本,游戏跑不起来。所以两边都走这一个函数。

    返回的**一定是具体类型**,不会是 "auto":
    `resolve_base_class` 在"物体没设物理、场景又选了自动"时会把 "auto"
    原样返回,那会生成 `type="auto"` 这种非法节点 ——
    实测在 World.tscn 里出现过(Godot 解析这个场景直接失败)。
    """
    from . import properties as props

    physics = getattr(obj, "gk_physics", None) if obj is not None else None
    resolved = props.resolve_base_class(scene, obj, physics, dimension)
    if resolved and resolved != props.BASE_CLASS_AUTO:
        return resolved
    # "auto" 不是合法 Godot 类型,兜底成静态碰撞体(最保守的选择)
    return "StaticBody3D" if str(dimension) != "2" else "StaticBody2D"


def export_assets_pipeline(scene, directory, dimension="3", script_name=None,
                           scene_name="World"):
    """完整的资产导出流程:整场景 glb -> 材质 -> 包装场景 -> 角色场景。

    返回一个结果字典,不抛异常(出错时把错误放进 "error"),
    这样调用方(比如"运行前自动导出")可以简单地警告一下就继续。

    ── 为什么抽成独立函数 ──────────────────────────────────────────
    这段流程以前只存在于 bpy.ops.gk.export_assets 的 execute 里。
    但"直接运行游戏时自动导出"也需要它 —— 在算子里再调另一个算子会卷进
    Blender 的操作符上下文约定,出问题时错误信息很难懂。
    这里的每一步本来就是纯函数,抽出来直接调更清楚。

    ── 顺序很重要 ──────────────────────────────────────────────────
    材质**必须在包装场景之前**生成:包装场景要用 material_override
    引用 materials/*.tres,引用不存在的文件 Godot 会报错。

    模型用整场景那份:只 instance 角色的话,按 F5 只能看到角色一个人
    悬在空中,地面和别的道具都不见了(实测踩过)。
    """
    import bpy

    result = {"written": [], "warnings": [], "error": None,
              "bodies": 0, "materials": 0, "textures": 0,
              "cameras": 0, "lights": 0}
    if scene is None or not directory:
        result["error"] = "没有场景或没有项目目录"
        return result

    is_3d = str(dimension) != "2"

    # ---- 0) 先找脚本挂载目标 ----
    # 必须最先做:它决定"角色是谁",而角色的网格要从 World.glb 里排除掉
    # (否则两份网格重叠 -> 深度冲突 -> 模型一闪一闪)。
    script_target = None
    for obj, physics, enabled in collect_physics(scene):
        if enabled and physics is not None and \
                getattr(physics, "script_target", False):
            script_target = obj
            break

    # ── 没勾「脚本挂载目标」时必须说出来 ────────────────────────────
    # 不勾的话导出的场景**一个脚本都不会挂** —— 用户按 F5 看到角色不动,
    # 会以为插件坏了。实测踩过:导出看起来全成功(文件都写了、无报错),
    # 但 .tscn 里根本没有 script = 那一行。
    if script_name and script_target is None:
        result["warnings"].append(
            "脚本没有挂到任何节点上:请在物体的『游戏物理 (BlenderGameKit)』"
            "面板里勾上『脚本挂载目标』(一般选角色身体那一个),再导出一次。"
            "否则导出的场景里不会有脚本,角色不会动。"
        )

    # ---- 0b) 角色自己的 glb(必须在导出整场景**之前**)----
    #
    # 包装场景要把角色节点做成"自带网格"的(self-contained),
    # 就得先有这个 glb 文件;而且导出整场景时要把它排除掉。
    # 见 write_world_scene 文档里 character_model 那段说明。
    def _char_glb_name():
        """角色 glb 的文件名。

        ── 为什么要带场景名前缀(用户 2026-10-04)────────────────────
        多场景导出时,关卡1 和关卡2 各有一个叫『示例角色』的物体 ——
        不带前缀的话两关的角色 glb 会**互相覆盖**(后导的盖掉先导的),
        表现是"第二关的角色网格跑到第一关去了"。
        单场景时 scene_name == "World",命名与以前完全一致。
        """
        base = _sanitize_node_name(script_target.name) or "Character"
        return _scene_prefix(scene_name) + base

    char_model = None
    if script_target is not None:
        char_model = _char_glb_name()
        char_path, char_ok, char_msg = export_subtree_glb(
            directory, char_model, [script_target], scene=scene)
        if char_ok:
            result["written"].append(char_path)
        else:
            result["warnings"].append(
                f"角色单独导出失败({char_msg}),已退回整场景模型:"
                f"这样角色移动时看不到自己的网格跟着动。")
            char_model = None

    # ---- 0c) 静态资产:本体各导一份 glb,实例在场景里复用它 ---------
    # 用户 2026-10-05:"游戏静态资产设计"。一个模型导一次、场景里摆多份:
    #   * 本体 -> `art/asset_<资产名>.glb`(只导本体这一个物体);
    #   * 本体 + 全部实例都从**主场景 glb 里排除**(它们的网格由 .tscn 里的
    #     `Model` 子节点提供 —— 不排除的话网格会出现两份,重叠闪烁);
    #   * 材质照旧按物体名映射(实例的网格来自本体的 glb,名字就是本体名)。
    assets = collect_assets(scene)
    result["warnings"].extend(validate_assets(scene))
    asset_models = {}
    asset_objects = []
    for name, entry in assets.items():
        model_name = asset_model_name(name, scene_name)
        asset_objects.extend(entry["members"])
        if entry["source"] is None:
            asset_models[name] = model_name
            continue
        model = asset_model_name(name, scene_name)
        path, ok, message = export_subtree_glb(directory, model,
                                               [entry["source"]],
                                               scene=scene)
        if ok:
            result["written"].append(path)
            asset_models[name] = model
            result["assets"] = result.get("assets", 0) + 1
        else:
            result["warnings"].append(
                f"资产『{name}』的模型导出失败({message})—— 它的实例在"
                f"Godot 里会没有网格")
            asset_models.pop(name, None)

    # ---- 1) 整场景 glb(排除角色和资产,避免两份网格重叠)----
    # 排除表 = 角色(有自己的 glb)+ 全部资产物体(本体和实例都有 Model 子节点)
    #          + 勾了『不显示本体网格』的粒子发射器(它只当标记,网格不该出现)
    excluded = []
    if script_target is not None and char_model:
        excluded.append(script_target)
    excluded.extend(asset_objects)
    excluded.extend(fx_hidden_meshes(scene))
    world_path, world_ok, world_message = export_scene_glb(
        directory, scene_name, scene=scene,
        exclude=excluded or None)
    if world_ok and world_path:
        result["written"].append(world_path)
    else:
        # ── 失败时 world_path 是 None,绝不能塞进 written ─────────────
        # 实测踩过:导出失败后 `written` 里混进了 None,调用方
        # (`gk.export_assets`)拿它去 `os.path.basename(path)` 就崩:
        #     TypeError: expected str, bytes or os.PathLike object, not NoneType
        # 结果是"导出失败"这件事本身又把插件搞崩一次,用户看到的是
        # 一堆看不懂的 Python traceback,而不是"导出失败了,原因是…"。
        result["warnings"].append(f"整场景导出失败: {world_message}")

    # ---- 2) 材质(必须在包装场景之前)----
    #
    # 贴图必须在材质**之前或同时**落盘:.tres 里引用
    # `res://art/xxx.png`,文件不存在的话 Godot 加载 .tres 会失败,
    # 进而整个场景实例化失败 —— 表现是打开一片空白,日志里只有一行
    # "Failed loading resource"。这是"卡通材质显示不出来"的直接原因。
    # 材质文件名先由 write_materials 统一算出来,再把同一份传给
    # scene_materials —— 两边各算一次的话,重名材质(M MD 模型动辄几十个
    # 槽)会算出互相对不上的文件名,运行时按名字找不到文件。
    materials_map = scene_materials(scene)
    try:
        # 多场景导出:材质文件名带场景前缀 —— 不带的话两关都写
        # `material_4.tres` 之类,后导的把前一份覆盖掉,用户看到的是
        # "关卡示例的东墙莫名变成红色"(那其实是关卡2 角色的材质)。
        materials = write_materials(directory, None,
                                    prefix=_scene_prefix(scene_name))
        result["written"].extend(item[1] for item in materials)
        result["written"].extend(copy_shaders(directory))
        result["materials"] = len(materials)
        # 用**实际写出的文件名**回填映射,保证两边一致:
        # write_materials 返回 (材质名, 路径, 有无贴图),这里去掉扩展名
        # 得到 {材质名: 文件名},再让 scene_materials 用同一份。
        by_material = {}
        for mat_name, path, _tex in materials:
            base = os.path.basename(path)
            by_material[mat_name] = base[:-5] if base.endswith(".tres") else base
        materials_map = scene_materials(scene, file_names=by_material)
        # 贴图要跟着材质一起落盘。
        #
        # ── 必须喂**全部**材质,不能只喂 materials_map 里那几个 ────────
        # 踩过的坑:`materials_map` 是 {物体名: 第一个材质},一个网格
        # 只有一个条目。MMD 那种一个网格 33 个材质槽的模型,用它去导贴图
        # 只会导出**第一张贴图**,其余材质的贴图全部缺失 ——
        # Godot 报 "Resource file not found: res://art/hair_png.png",
        # 材质加载失败,模型回到白模。
        # 所以这里直接拿 write_materials 实际处理过的那批材质。
        used_materials = []
        seen_materials = set()
        for _mat_name, _path, _tex in materials:
            material = bpy.data.materials.get(_mat_name)
            if material is not None and _mat_name not in seen_materials:
                seen_materials.add(_mat_name)
                used_materials.append(material)
        # ── 粒子发射器的贴图也要落盘(用户 2026-10-05 第二十一轮)──────
        # 粒子贴图优先用**物体材质的基础色贴图** —— 而上面那批材质来自
        # write_materials,卡通材质总开关一关它一个都不返回,粒子贴图
        # 就会缺文件(Godot: "Resource file not found" -> 整个场景加载失败)。
        # 所以这里把发射器用到的材质也补进去(export_textures 自己去重)。
        for emitter, _fx in collect_emitters(scene):
            material = first_real_material(emitter)
            if material is not None and material.name not in seen_materials:
                seen_materials.add(material.name)
                used_materials.append(material)
        textures = export_textures(directory, used_materials)
        result["written"].extend(path for _n, path in textures)
        result["textures"] = len(textures)
        # ── 界面图片也要落盘(第三十七轮)──────────────────────────────
        # 『界面_图片』/ 对话框立绘 引用的图片 -> art/ui_xxx.png。
        # 不落盘的话生成的脚本里 load("res://art/ui_xxx.png") 会拿到 null
        # (Godot 只在控制台丢一行 "Resource file not found",画面上就是空的)。
        # 走**同一个 try**:材质那套失败时(比如 OSError)这里一起跳过,
        # 不额外制造一条崩溃路径。
        ui_textures = export_ui_images(directory, scene)
        if ui_textures:
            result["written"].extend(path for _n, path in ui_textures)
            result["textures"] = result["textures"] + len(ui_textures)
    except OSError as exc:
        result["warnings"].append(f"材质生成失败: {exc}")
        materials_map = {}

    # ── 调试面板只支持 3D(第二十二轮)──────────────────────────────────
    # 面板在**环境脚本**里,而环境脚本目前只给 3D 项目生成(见
    # build_environment_nodes 开头的早退)。不说的话 2D 用户会把开关打开、
    # 进游戏发现什么都没有 —— 这种"静默没反应"最费时间。
    if not is_3d and _debug_panel_on(scene):
        result["warnings"].append(
            "调试面板目前只在 3D 项目里生成(2D 项目还没有环境脚本)—— "
            "这一关不会出现面板。要排查先用『运行日志』开关或『导出诊断信息』。")

    # ---- 2b) 内置默认粒子贴图(有用得上的发射器才写)----
    # 贴在**材质之后、包装场景之前**决定:这时贴图已经落盘,
    # `fx_texture_file(..., directory)` 能如实判断"那个物体的贴图到底在不在",
    # 不在就退回内置那张 —— 和 write_world_scene 里是同一个判据,
    # 两边不会一个引用素材贴图、一个引用内置图。
    if any(fx_texture_file(obj, directory) == FX_DEFAULT_TEXTURE
           for obj, _fx in collect_emitters(scene)):
        default_texture = ensure_fx_default_texture(directory)
        if default_texture:
            result["written"].append(default_texture)

    # ---- 3) 包装场景:整个场景 + 材质覆盖 + 碰撞体 + 相机灯光 ----

    # ---- 2b) 角色自己的 glb(必须在包装场景**之前**)----
    #
    # 包装场景要把角色节点做成"自带网格"的(self-contained),
    # 就得在写 .tscn 之前先有这个 glb 文件。
    # 见 write_world_scene 文档里 character_model 那段说明。
    char_model = None
    if script_target is not None:
        # 多场景时带场景名前缀,免得两关的角色 glb 互相覆盖(用户 2026-10-04)。
        char_model = _char_glb_name()
        char_path, char_ok, char_msg = export_subtree_glb(
            directory, char_model, [script_target], scene=scene)
        if char_ok:
            result["written"].append(char_path)
        else:
            result["warnings"].append(
                f"角色单独导出失败({char_msg}),已退回整场景模型:"
                f"这样角色移动时看不到自己的网格跟着动。")
            char_model = None

    try:
        world_scene, body_count = write_world_scene(
            directory, scene_name,
            scene=scene, dimension=dimension,
            script_name=script_name if script_target else None,
            script_target_name=script_target.name if script_target else None,
            materials_map=materials_map,
            character_model=char_model,
            file_names=by_material,
            character_object=script_target,
            asset_models=asset_models,
        )
        result["written"].append(world_scene)
        # 环境脚本也是这次导出的产物(写在 write_world_scene 里),
        # 记账要给旧文件清理用 —— 不记的话它会被当成旧残留删掉(自毁)。
        env_file = os.path.join(directory, env_script_rel(scene_name))
        if os.path.isfile(env_file) and env_file not in result["written"]:
            result["written"].append(env_file)
        result["bodies"] = body_count
        if world_scene is None:
            # 整场景 glb 里**一个网格都没有**(比如场景里只有一个角色,而角色
            # 自己走单独 glb)时 write_world_scene 会静默跳过 —— 结果是
            # project.godot 的主场景指向一张**不存在**的 .tscn。
            # 这里补一条可读告警,别让用户对着"主场景打不开"猜。
            result["warnings"].append(
                "场景里除了角色没有别的网格 —— 没生成主场景 .tscn"
                "(放个地面 / 场景道具再导出,Godot 里按 F5 才有东西可看)")
    except OSError as exc:
        result["warnings"].append(f"包装场景生成失败: {exc}")
        return result

    # ---- 3b) 开局 BGM 的音频文件(第七十七轮)--------------------------
    # 环境脚本里写的是一条 `res://audio/...`,所以音频必须真的进项目 ——
    # 不复制的话 Godot 里就是一句"找不到文件",而且**只在运行时**才发现。
    # 走和『资产_声音卡』同一个管线(`sound_assets`),名字算得一模一样。
    bgm_card = scene_bgm_card(scene)
    if bgm_card is not None:
        try:
            written_sounds, missing_sounds = sound_assets.copy_sounds(
                directory, [bgm_card])
            for rel in written_sounds:
                result["written"].append(os.path.join(directory, rel))
            for _name, path in missing_sounds:
                result["warnings"].append(
                    f"开局 BGM 的文件找不到了:{path}"
                    f"(在侧栏『导出为 Godot 脚本』里重新挑一次)")
        except OSError as exc:
            result["warnings"].append(f"开局 BGM 复制失败: {exc}")

    # 相机 / 灯光数量(面板上要报告,用户靠这个判断"会不会又是一片灰")
    # 相机 / 灯光数量(面板上要报告,用户靠这个判断"会不会又是一片灰")
    glb_info = glb_object_names(directory, scene_name)
    _env, _files, _cam = build_environment_nodes(
        scene, is_3d, skip=glb_info, scene_name=scene_name)
    result["cameras"] = len(glb_info.get("cameras", ())) or (
        1 if any(obj.type == "CAMERA" for obj in scene.objects) else 0)
    result["lights"] = len(glb_info.get("lights", ())) or (
        1 if any(obj.type == "LIGHT" for obj in scene.objects) else 0)

    # ---- 4) 角色场景(单独调试角色用)----
    #
    # ── 主场景是 World.tscn ─────────────────────────────────────────
    # 用户的反馈很明确:"应该默认为平面为主场景,不然每次打开 Godot
    # 再设置项目主场景太烦人了"。
    #
    # 以前把主场景指向角色场景,是因为 World.tscn 里移动角色时画面不动
    # (角色网格在世界 glb 里,和碰撞体分属两棵子树)。现在 World.tscn 里
    # 角色节点**自带网格**了(见 character_model),这个问题不存在了,
    # 所以主场景回到 World —— 打开就能看到地面和整个场景。
    #
    # 角色场景保留:它的用途是"只调试角色一个人",不再抢主场景。
    if script_name and script_target is not None:
        bounds = object_bounds([script_target])
        try:
            scene_path, _cam, _light = write_character_scene(
                directory,
                root_name=script_target.name,
                # 基类必须和主场景里那个节点的类型**一致** ——
                # 不一致的话 Godot 会拒绝加载脚本
                # (Script inherits from native type 'X', so it can't be
                #  assigned to an object of type: 'Y')。
                # 所以这里调的是同一个解析函数,不另外猜。
                base_class=_base_class_for(scene, script_target, dimension),
                # 上面 0b) 已经导过了,这里直接复用,不重复导一遍
                model_name=char_model or scene_name,
                script_name=script_name,
                bounds=bounds,
                dimension=dimension,
                scene=scene,
                # 材质映射要带过去 —— 不带的话角色场景里没有 materials=,
                # gk_environment.gd 就挂不上卡通材质(踩过)
                materials_map=materials_map,
                file_names=by_material,
                character_object=script_target,
                # 不再抢主场景:World.tscn 现在也是"移动角色时画面会跟着动"的
                set_as_main=False,
            )
            result["written"].append(scene_path)
            result["character_scene"] = scene_path
        except OSError as exc:
            result["warnings"].append(f"角色场景生成失败: {exc}")

    result["directory"] = directory

    # ---- 5) 没有材质的网格会渲染成灰模,提前说清楚 ----
    # 这不是渲染坏了,是那个物体在 Blender 里就没有材质。
    # 不说的话用户只会看到"一片灰",完全想不到要去加材质。
    no_material = meshes_without_material(scene)
    if no_material:
        result["warnings"].append(
            "这些网格没有材质,在 Godot 里会显示成灰模:"
            + "、".join(no_material)
            + "。在 Blender 里给它们指定一个材质(『材质属性』面板 → 新建),"
              "再导出一次就有颜色了。"
        )

    # ---- 6) 碰撞体和网格尺寸差太多 —— 看起来像"陷进去/悬空" ----
    # 导出本身不会失败,但运行起来"走到模型跟前就穿模"或者"没碰到就撞上",
    # 用户很容易把它当成渲染 bug(实测就这么被报过)。
    mismatched = colliders_that_mismatch_mesh(scene)
    if mismatched:
        details = ";".join(
            f"{name} 网格{mesh} 碰撞体{collider}"
            for name, mesh, collider in mismatched[:4])
        result["warnings"].append(
            "这些物体的碰撞体和网格尺寸差得比较多,运行时会看起来"
            "『陷进去』或『悬空』:" + details
            + "。在『游戏物理』面板里把『碰撞尺寸』清成 0,让碰撞体自动贴合模型。"
        )

    return result


def _safe_scene_name(name):
    """Blender 场景名 -> 能当文件名 / 资源名的名字(保留中文)。"""
    cleaned = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff\-]", "_", str(name or ""))
    cleaned = cleaned.strip("_")
    return cleaned or "Scene"


def _project_main_scene(directory):
    """读 project.godot 里的 run/main_scene(没有就返回空串)。"""
    path = os.path.join(directory, "project.godot")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return ""
    match = re.search(r'^run/main_scene="([^"]*)"', text, re.MULTILINE)
    return match.group(1) if match else ""


def _active_scene():
    """当前活动场景(取不到就 None)。bpy 只在需要时导入 —— 离线也能 import 本模块。"""
    try:
        import bpy
    except Exception:                                     # noqa: BLE001
        return None
    return getattr(bpy.context, "scene", None)


def _activate_scene(scene):
    """把 scene 切成**当前活动场景**;做不到就返回 False。

    ── 为什么必须切(用户 2026-10-04,多场景实测)──────────────────
    导出一个场景时要选中它里面的物体(`obj.select_set`),而它只认
    **当前视图层**里的物体,否则直接抛:
        物体 '第二关角色' 不在视图层 '视图' 中,所以无法选中
    —— 因为活动场景还是关卡1。而且 glTF 导出算子也只认活动场景,
    不切的话"导关卡2"实际导出的是关卡1的东西。
    GUI 里改 window.scene;后台没有 window(为 None),退回改 context.scene。
    """
    if scene is None:
        return False
    try:
        import bpy
    except Exception:                                     # noqa: BLE001
        return False
    window = getattr(bpy.context, "window", None)
    if window is not None:
        try:
            window.scene = scene
            return True
        except Exception:                                 # noqa: BLE001
            pass
    try:
        bpy.context.scene = scene
        return True
    except Exception:                                     # noqa: BLE001
        return False


def export_all_scenes(directory, dimension=None, script_name=None):
    """把一个 .blend 里的**每个 Blender 场景**各导出一张 .tscn(用户 2026-10-04)。

    ── 为什么需要它 ────────────────────────────────────────────────
    用户做"第二关"时,是在 Blender 里**新建一个场景**(不是新建节点图)——
    Blender 的"场景"才是他心里的"关卡"。以前导出只认"当前那一个场景 +
    节点编辑器里开着的那棵树",所以第二关根本导不出来(实测:切关卡后
    一片空白)。

    现在:遍历 .blend 里所有 Blender 场景,每个场景用它在场景设置里选的
    逻辑图,各导一份 .tscn。**单场景项目 scene_name 仍是 "World",与以前
    完全一致** —— 只有 .blend 里存在多于一个场景时才走多场景命名。

    ── dimension 参数(第八十七轮起可为 None)────────────────────────
    None = **逐场景**用各自的场景设置(『关卡预设』建的 2D / 3D 关
    可以在同一个 .blend 里各导各的维度);给了值 = 全部按这个值导
    (老调用方 / 修过的角例,行为一字不变)。
    以前只认一个值:一个 .blend 里同时有 2D 和 3D 关卡时,后者的场景
    会被前者的维度导出(2D 关卡里出现 Camera3D 那种错)。

    ── 主场景 ──────────────────────────────────────────────────────
    多场景时主场景指向**第一个**场景(通常就是关卡1),但用户手动在 Godot
    里选过的主场景(指向本次导出的某张图)会被尊重。

    返回:
      {"error", "scenes":[{scene, scene_name, tscn, ok, script_name,
                           tree_reason, warnings}],
       "written", "warnings", "main_scene", "single",
       "materials", "bodies", "cameras", "lights"}
    """
    from . import exporter

    merged = {"error": None, "scenes": [], "written": [], "warnings": [],
              "main_scene": "", "single": True, "deleted": [],
              "materials": 0, "bodies": 0, "cameras": 0, "lights": 0}
    if not directory:
        merged["error"] = "没有项目目录"
        return merged

    scenes = list(exporter.exportable_scenes())
    if not scenes:
        merged["error"] = "没有可导出的 Blender 场景"
        return merged

    multi = len(scenes) > 1
    merged["single"] = not multi
    original_main = _project_main_scene(directory)
    # 导出前记住用户正在看哪个场景 —— 循环里会一个个切过去,结束时还回去
    original_active = _active_scene()

    for scene in scenes:
        # 见 _activate_scene:不切的话第二关的物体会选不中,glTF 算子也
        # 会去导"当前这一关"的东西。
        _activate_scene(scene)
        scene_name = _safe_scene_name(scene.name) if multi else "World"
        # ── 这一关的维度(第八十七轮)────────────────────────────────
        # 调用方给了值就用它(老调用方一字不变);没给(None)= 逐场景
        # 读各自的场景设置 —— 一个 .blend 里 2D 关卡和 3D 关卡混着放时
        # 各导各的,不会把 3D 关当 2D 导。
        if dimension is None:
            scene_dimension = str(
                exporter.build_settings(scene).get("dimension", "3") or "3")
        else:
            scene_dimension = str(dimension)
        entry = {"scene": scene.name, "scene_name": scene_name,
                 "tscn": "", "ok": False, "warnings": [],
                 "script_name": "", "tree_reason": ""}

        # ① 这个场景用哪棵游戏逻辑图
        #    多场景:每个场景各自导出脚本(名字按场景分开,见下);
        #    单场景:调用方通常已经导过脚本、把名字传进来了(插件的 UI 流程
        #    就是这样)——没传就**自己导**。不自己导的话会静默导出一张
        #    "没挂脚本"的场景:代码文件在,可 .tscn 里没有 `script =`,
        #    用户按 F5 角色一动不动(实测踩过)。
        tree_script = script_name
        tree, why = exporter.resolve_scene_tree(scene)
        entry["tree_reason"] = why
        # 调用方给了脚本名、但那个文件其实**不在**(比如它只做了别的步骤):
        # 也要自己导一遍 —— 不然 .tscn 会引用一个不存在的脚本,
        # Godot 打开就报 "Failed loading resource"(实测踩过)。
        script_missing = bool(tree_script) and not os.path.isfile(
            os.path.join(directory, project.SCRIPTS_DIR, str(tree_script)))
        if tree is not None and (multi or not tree_script or script_missing):
            scene_settings = exporter.build_settings(scene)
            scene_settings["output_dir"] = directory
            scene_settings["dimension"] = scene_dimension
            if multi:
                # ── 每个场景的逻辑脚本必须**分开命名**(用户 2026-10-04)────
                # 脚本名默认取节点树名,而中文名会被压成空 -> 回退 game_logic.gd。
                # 于是"关卡1"和"关卡2"两棵树都写成 scripts/game_logic.gd ——
                # 后写的赢,前一关的 .tscn 明明挂着,跑起来却是另一关的逻辑
                # (用户实测:"在第二场景里按 Esc 无法退出"就是这一族问题)。
                # 按**场景名**各起一个:game_logic_关卡1.gd / game_logic_关卡2.gd。
                scene_settings["script_name"] = f"game_logic_{scene_name}.gd"
            try:
                tree_result = exporter.export_tree(tree, scene_settings,
                                                   write_scene=False)
                tree_script = tree_result.get("script_name") or None
                entry["script_name"] = tree_script or ""
                entry["input_actions"] = list(
                    tree_result.get("input_actions", []) or [])
                entry["warnings"].extend(tree_result.get("warnings", []))
                # 这次导出写出来的东西(脚本 / 曲线资源)也要记账:
                # 详见 generated_files —— 脚本漏记的话,下一轮清理会把它
                # 当成"旧文件"删掉(自毁)。
                # ⚠ `entry` 这时还没有 "written" 键(它在下面流水线之后才建),
                # 直接 entry["written"].extend 会 KeyError:'written' ——
                # 而且被外面的 except 吞成一句"逻辑图导出失败"(实测踩过)。
                entry.setdefault("written", []).extend(
                    p for p in tree_result.get("written", []) if p)
            except Exception as exc:                        # noqa: BLE001
                entry["warnings"].append(
                    f"『{scene.name}』的逻辑图导出失败:{type(exc).__name__}: {exc}")
        elif multi and tree is None:
            entry["warnings"].append(
                f"『{scene.name}』没有可用的游戏逻辑图:{why}")

        outcome = export_assets_pipeline(
            scene, directory, dimension=scene_dimension, script_name=tree_script,
            scene_name=scene_name)
        entry["warnings"].extend(outcome.get("warnings", []))
        # 累加,不能覆盖:上面『逻辑图导出』那一步可能已经记了脚本 / 曲线
        entry["written"] = list(entry.get("written", [])) + [
            p for p in outcome.get("written", []) if p]
        # ── 调用方已经导过脚本时,脚本也要记账(第十三轮)─────────────
        # UI 的流程是『导出脚本』→『导出资产』两步。第二步如果只承认
        # 自己流水线写的文件,清理就会把第一步刚写好的脚本当成旧文件删掉
        # (自毁)。这里把它补进"这次导出的产物"里。
        if tree_script:
            script_file = os.path.join(directory, project.SCRIPTS_DIR,
                                       str(tree_script))
            if os.path.isfile(script_file):
                entry["written"].append(script_file)
        if outcome.get("error"):
            entry["warnings"].append(outcome["error"])
        else:
            entry["ok"] = True
            entry["tscn"] = os.path.join(
                directory, project.SCENES_DIR, f"{scene_name}.tscn")

        merged["written"].extend(entry["written"])
        merged["warnings"].extend(entry["warnings"])
        merged["materials"] += int(outcome.get("materials", 0) or 0)
        merged["bodies"] += int(outcome.get("bodies", 0) or 0)
        merged["cameras"] += int(outcome.get("cameras", 0) or 0)
        merged["lights"] += int(outcome.get("lights", 0) or 0)
        merged["scenes"].append(entry)

    # ② 主场景:优先保留用户在 Godot 里手动选的(必须是本次导出的某张),
    #    否则指向第一个场景 —— write_world_scene 每导一个都会把主场景
    #    改成"它自己",导完最后一个就成了关卡2,所以要在这里纠正回来。
    exported_res = [f"res://{project.SCENES_DIR}/{e['scene_name']}.tscn"
                    for e in merged["scenes"] if e["ok"]]
    if exported_res:
        keep = original_main if original_main in exported_res else exported_res[0]
        project.set_main_scene(directory, keep)
        merged["main_scene"] = keep

    # ② 输入映射:**合并**写一次(第三十一轮)────────────────────────────
    # 每个场景的 export_tree 会各写一次 [input] 标记块,而 set_input_actions
    # 是"替换标记块"—— 两个场景就会**后写的赢**:关卡1 的动作(比如新加的
    # key_e)被关卡2 的覆盖掉,表现是"第一关按 E 没反应、第二关却好好的"。
    # 真实观测:关卡1 车都上不去(输入映射里没有 key_e)。
    # 修法:把各场景这次用到的动作并起来,最后统一写一遍。
    all_actions = []
    for entry in merged["scenes"]:
        all_actions.extend(entry.get("input_actions") or [])
    if all_actions:
        try:
            project.set_input_actions(directory, all_actions)
        except Exception as exc:                          # noqa: BLE001
            merged["warnings"].append(
                f"输入映射合并写入失败:{type(exc).__name__}: {exc}")

    # ③ 旧文件清理(用户 2026-10-04 第十三轮)──────────────────────────
    # 把"上一次导出写过、这一次没写"的文件删掉。这一条修的是**旧残留**:
    # 上一版留下的同名材质会盖掉新的("东墙莫名变红"),旧脚本带着同一个
    # class_name 会让 Godot 直接报 `Class "X" already exists`(整个工程
    # 跑不起来)。规则与安全边界见 generated_files 的模块说明。
    #
    # **只有整次导出全部成功才清** —— 一半失败时清理会把"这一版本来该写
    # 但还没写"的文件也当成旧的删掉。
    if merged["scenes"] and all(entry["ok"] for entry in merged["scenes"]):
        try:
            merged["deleted"] = generated_files.cleanup_after_export(
                directory, merged["written"])
        except OSError as exc:
            merged["warnings"].append(f"旧文件清理失败(不影响本次导出): {exc}")
    if merged["deleted"]:
        gk_log.info(f"已清理 {len(merged['deleted'])} 个旧导出文件: "
                    + "、".join(merged["deleted"][:6])
                    + ("…" if len(merged["deleted"]) > 6 else ""))

    # 把用户原来看着的场景还回去(循环把活动场景切到最后导的那一关)
    if original_active is not None:
        _activate_scene(original_active)

    return merged


_ROOT_GLB = None


def export_subtree_glb(export_dir, name, roots, scene=None):
    """把若干物体**及其全部子级**导出成一个独立的 .glb。

    ── 为什么角色必须有自己的 glb ──────────────────────────────────
    以前角色场景里 instance 的是整个 `World.glb`,于是它的节点树长这样:

        Cube [CharacterBody3D]        ← 脚本在这里,移动它
          Model [instance World.glb]  ← **整个世界**都塞在这里
            Plane / Suzanne / Camera …

    结果:一移动角色,**整个世界跟着跑**(地面、别的道具全飞了)。
    而且因为 Godot 不允许往实例内部挂节点,也没法把碰撞体挂到角色网格上
    (实测报 "Parent path './World/Suzanne' has vanished")。

    正确做法:角色自己一个 glb,里面只有角色网格。

    返回 (路径, 是否成功, 消息)。
    """
    import bpy

    scene = scene or bpy.context.scene
    if scene is None or not roots:
        return None, False, "没有可导出的角色物体"

    # 收集:根 + 全部后代 + **蒙皮用的骨架**
    #
    # ── 为什么必须带上骨架(实测踩过)────────────────────────────────
    # 骨架在 Blender 里通常是网格的**兄弟**(两者同级,网格挂一个
    # Armature 修改器指过去),而不是网格的子级。只收"根 + 后代"的话,
    # 骨架会被漏掉 —— 导出的角色 glb 里 `skins: 0`、`animations: []`,
    # 动画全留在了世界 glb 里,角色身上根本播不了。
    #
    # 实测症状:Body.glb 里 animations=[] / skins=0,而 World.glb 里
    # animations=['RigAction'] / skins=1 —— 骨骼和角色分家了。
    wanted = set()
    stack = [obj for obj in roots if obj is not None]
    seen = set()
    # ── 还要往上收**父级**(踩过的坑)──────────────────────────────────
    # 用户实测:"可以播放形态键动作,但骨骼动作播放不了"。
    # 导出后的 glb 里 `skins: 0`,只有一个 mesh 节点,骨架根本不在。
    #
    # 他的层级是:
    #     胡桃-海灯节_arm [ARMATURE]        <- 骨架是**父级**
    #       胡桃-海灯节_mesh [MESH]         <- 挂载目标是它
    #
    # 而这里只做 `stack.extend(obj.children)` —— **只往下走**。
    # 从 mesh 出发永远收不到它的父级骨架,于是:
    #   * 没有骨架 -> `skins: 0`,网格失去蒙皮
    #   * 骨架上的动作(骨骼动作)**导不出去**
    #   * 形态键动作长在 mesh 自己身上,所以它能播 —— 正好对上症状
    #
    # 所以两个方向都要走:向上收父级,向下收子级。
    up_stack = [obj for obj in roots if obj is not None]
    seen_up = set()
    while up_stack:
        obj = up_stack.pop()
        if obj in seen_up:
            continue
        seen_up.add(obj)
        wanted.add(obj)
        parent = getattr(obj, "parent", None)
        if parent is not None:
            up_stack.append(parent)
        # 骨架的**子级**也要收:网格可能不是直接的挂载目标
        if getattr(obj, "type", "") == "ARMATURE":
            stack.append(obj)

    while stack:
        obj = stack.pop()
        if obj in seen:
            continue
        seen.add(obj)
        wanted.add(obj)
        stack.extend(obj.children)
        # 蒙皮骨架:网格身上的 Armature 修改器指向谁,就把谁拉进来
        for mod in getattr(obj, "modifiers", ()) or ():
            if getattr(mod, "type", "") == "ARMATURE" and getattr(mod, "object", None):
                stack.append(mod.object)
        # 骨架本身也要算:如果入口物体就是个骨架
        if getattr(obj, "type", "") == "ARMATURE":
            stack.extend(obj.children)

    previous = [obj for obj in scene.objects if obj.select_get()]
    # ── 隐藏的骨架会被导出器跳过 ────────────────────────────────────
    # 用户实测:"可以播放形态键动作,但骨骼动作播放不了"。
    # 根因:他的 MMD 骨架在视口里是隐藏的,而 **Blender 的 glTF 导出器
    # 会跳过隐藏的物体** —— 导出结果里 `skins: 0`,连网格的
    # `JOINTS_0` / `WEIGHTS_0` 都没有,骨骼动画整个丢失。
    #
    # 试过 `hide_viewport`(显示器图标)也一样会被跳过,所以**没有**
    # "在 Blender 里藏起来还能导出骨架"的办法。
    #
    # 用户明确要求不要把隐藏改掉("因为显示了不该显示的东西"),
    # 所以这里**不改隐藏**,而是:
    #   * 检测到要导出的骨架是隐藏的 -> 明确告警,告诉他后果和做法
    #   * 另外给一个 `force_hidden_objects` 开关让他自己选
    #     (见 properties.GKSceneSettings)
    force = _force_hidden(scene)
    restore_hidden = []
    hidden_armatures = []
    for obj in wanted:
        if getattr(obj, "type", "") != "ARMATURE":
            continue
        try:
            if obj.hide_get():
                hidden_armatures.append(obj.name)
                if force:
                    obj.hide_set(False)
                    restore_hidden.append(obj)
        except Exception:
            pass
    for obj in scene.objects:
        obj.select_set(obj in wanted
                       and not obj.get(PREVIEW_MARK)
                       and not is_collision_piece(obj))

    target_dir = os.path.join(export_dir, project.ART_DIR)
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, f"{name}.glb")

    kwargs = {
        "filepath": path,
        "export_format": "GLB",
        "use_selection": True,
        "export_apply": False,
        "export_yup": True,
        "export_materials": "EXPORT",
        "export_animations": True,
        "export_skins": True,
        "export_texcoords": True,
        "export_normals": True,
        # 相机灯光由 Environment 脚本 / 主场景负责,角色 glb 里不要
        "export_cameras": False,
        "export_lights": False,
        "export_extras": True,
    }
    # ── 只导出**当前这个场景**(第八十七轮:多场景时串场景)────────────
    # 不传这个开关时,导出器会把 **.blend 里所有场景**都算进来,
    # 而 `use_selection` 只筛"选中的物体" —— 别的场景里选中的东西会
    # **混进这个 glb**(实测:『关卡预设』建了三关,3D 关的 glb 里
    # 混进了 2.5D 关的地面和角色)。传 True 就把范围收在活动场景里;
    # 老版本 Blender 没有这个参数时 `_run_gltf_export` 会自动降级重试。
    try:
        if bpy.context.scene == scene:
            kwargs["use_active_scene"] = True
    except Exception:                                     # noqa: BLE001
        pass
    result = _run_gltf_export(kwargs, path)

    # 隐藏骨架 -> 明确告警(见上面那段说明)。
    # 不告警的话症状是"形态键能播、骨骼播不了",很难反推到"因为骨架藏了"。
    note = _hidden_armature_warning(hidden_armatures)
    if note:
        notes = getattr(_run_gltf_export, "_notes", None)
        if notes is None:
            notes = []
            _run_gltf_export._notes = notes
        notes.append(note)

    try:
        for obj in scene.objects:
            obj.select_set(obj in previous)
    except Exception:
        pass
    # 把临时取消的隐藏恢复回去
    for obj in restore_hidden:
        try:
            obj.hide_set(True)
        except Exception:
            pass
    return result


def write_character_scene(export_dir, root_name, base_class, model_name, script_name,
                          bounds, dimension="3", with_collision=True, scene=None,
                          set_as_main=True, model_dir=None, materials_map=None,
                          file_names=None, character_object=None):
    """生成把 模型 + 碰撞体 + 脚本 + 相机灯光 拼在一起的 .tscn。

    ── 结构(这才是能让角色动起来的结构)─────────────────────────────
        Cube [CharacterBody3D]        ← 脚本挂这里;移动它
          Collision [CollisionShape3D]
          Model [instance Cube.glb]   ← 只有角色网格

    碰撞体和网格都是**根节点的子级**,所以根一动,两个一起动。

    为什么要带相机和灯光:以前只放模型,结果按 F5 看到的**是一片灰** ——
    没有相机就没有视点,没有灯光就全黑,再加上 Godot 默认的灰底,
    看起来就像"渲染坏了"。从 Blender 场景里把相机和灯光搬过来就没这个问题。

    set_as_main=False 时不会改主场景 —— 整场景导出会先写 World 场景并设成主场景,
    之后再写角色场景时不能再抢,否则 F5 打开的就只剩角色了。
    """
    dimension_2d = str(dimension) == "2"
    sub_resources = ""
    collision = ""
    if with_collision and bounds is not None:
        size = bounds["size"]
        if dimension_2d:
            radius = max(size[0], 0.01) * 0.5
            height = max(size[1], 0.02)
        else:
            radius = max(size[0], size[2], 0.01) * 0.5
            height = max(size[1], 0.02)
        sub_resources = COLLISION_TEMPLATE.format(radius=radius, height=height)
        collision = COLLISION_NODE_2D if dimension_2d else COLLISION_NODE_3D

    # 相机 / 灯光的处理和包装场景一致:glb 里已经有就不再生成
    # (见 build_environment_nodes 的说明)。角色场景 instance 的也是
    # 那个 glb,所以它同样带着相机和灯。
    glb_info = glb_scene_nodes(export_dir, model_name)

    # ── 材质映射必须一起带过来(踩过的坑)───────────────────────────
    # 以前这里**没传** material_files,于是角色场景里的 Environment 节点
    # 不会写 materials= 那一行,`gk_environment.gd` 也就挂不上卡通材质 ——
    # 角色只剩 glb 自带的 StandardMaterial3D,看起来"材质没生效"
    # (用户实测:"地面有材质,为什么没渲染上")。
    #
    # 键用 **glb 里的网格节点名** —— 因为 `_apply_materials` 是在 Godot
    # 运行时按节点名 find_child 找的。Blender 的物体名在导出时会被
    # sanitize,两者可能不一致,所以优先用 glb 里真实存在的节点名。
    #
    # 材质槽多于一个时改成**逐表面**覆盖(`节点名#序号`),否则 MMD 那种
    # 一个网格几十个材质槽的模型会被一条 material_override 全部刷成同一个
    # 材质,看起来"变白"(见 surface_material_files 的说明)。
    material_files = {}
    per_surface = surface_material_files(character_object, file_names)
    if per_surface:
        material_files.update(per_surface)
    else:
        for node_name in sorted(glb_info.get("meshes", {})):
            entry = (materials_map or {}).get(node_name)
            if entry:
                material_files[node_name] = entry[1]
        if not material_files:
            # glb 里没解析出网格名(或名字对不上)时,退回用 Blender 物体名当键。
            # 单网格角色(最常见)的两种情况名字通常一致。
            for obj_name in sorted(materials_map or {}):
                material_files[obj_name] = materials_map[obj_name][1]

    environment, env_files, _camera_name = build_environment_nodes(
        scene, not dimension_2d, skip=glb_info, scene_name=model_name,
        material_files=material_files)
    camera_count = len(glb_info.get("cameras", ()))
    light_count = len(glb_info.get("lights", ()))

    # Environment 脚本的 ext_resource 行 —— 只要有 Environment 节点就必须有它。
    # 判据用 **environment 文本非空**(和 World 场景一致),而不是 env_files:
    # 少写一行 ext_resource 会让节点引用一个没声明的 ExtResource,Godot 直接报
    #     Parse Error: Can't load resource ... env_script
    #
    # 路径按 .tscn 名取(方案 ②a:每场景一份脚本)—— 角色子场景用
    # `model_name`,于是它的 `materials` 默认值就是角色自己的材质表。
    env_resource = ""
    if environment.strip():
        env_resource = ENV_RESOURCE_LINE.format(
            path=env_script_res(model_name))

    steps = 1 + (1 if sub_resources else 0) + 2 + (1 if env_resource else 0)
    # ── 角色场景**根节点不挂脚本**(踩过两轮的坑,这次想清楚了)──────
    # 世界场景里的角色节点会 `instance` 这个角色场景,而那个节点
    # **自己**也挂着同一个脚本(必须挂 —— Godot 实例化场景时
    # **不继承**根节点的 `script` 属性,实测 `script=<null>`)。
    #
    # 两份都挂的后果:`_ready()` 执行两遍 —— 用户实测打印两次,
    # 而且移动逻辑跑两遍(角色双倍速)。
    #
    # 取舍:角色场景在这个工作流里是**子场景**(被 World 实例化),
    # 不是给人按 F5 直接跑的。所以把逻辑留在 World 那个节点上,
    # 角色场景只负责"几何 + 蒙皮 + 碰撞体"。
    # (单独打开角色场景不会跑逻辑,这是**故意**的。)
    text = SCENE_TEMPLATE.format(
        steps=steps,
        script_resource="",
        script_line="",
        script_path=f"res://{project.SCRIPTS_DIR}/{script_name}",
        model_path=f"res://{model_dir or project.ART_DIR}/{model_name}.glb",
        env_resource=env_resource,
        sub_resources=sub_resources,
        root_name=root_name or "Character",
        base_class=base_class,
        collision=collision,
        environment=environment,
    )
    # .tscn 统一放 scenes/(脚本在 scripts/,模型在 art/)
    path = os.path.join(export_dir, project.SCENES_DIR,
                        f"{root_name or 'Character'}.tscn")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    for relative, content in (env_files or {}).items():
        target = os.path.join(export_dir, relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    if set_as_main:
        # 顺手设成主场景,否则 Godot 里按 F5 会弹"没有设置主场景"
        project.set_main_scene(export_dir,
                               f"{project.SCENES_DIR}/{os.path.basename(path)}")
    return path, camera_count, light_count


def shader_source_dir():
    """着色器所在目录。

    必须放在**插件包内部**(BlenderGameKit/shaders/)，因为它要随扩展一起
    打包;放在仓库根目录的话，插件装进 Blender 之后就找不到了。
    """
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "shaders")


def copy_shaders(export_dir):
    """把 NPR 着色器复制进项目的 shaders/ 目录(Godot 不能引用目录外的文件)。"""
    source = shader_source_dir()
    written = []
    if not os.path.isdir(source):
        gk_log.warn(f"找不到着色器目录: {source}")
        return written
    target = os.path.join(export_dir, project.SHADERS_DIR)
    os.makedirs(target, exist_ok=True)
    for name in sorted(os.listdir(source)):
        if not name.endswith(".gdshader"):
            continue
        with open(os.path.join(source, name), "r", encoding="utf-8") as src:
            text = src.read()
        with open(os.path.join(target, name), "w", encoding="utf-8", newline="\n") as dst:
            dst.write(text)
        written.append(os.path.join(target, name))
    return written


def ensure_project_file(export_dir, project_name="BlenderGameKit Asset Export"):
    """确保目录里有 project.godot,返回它的路径。

    已存在时直接返回路径(不重写、也不返回 None),调用方不用区分
    "新建"和"已存在"两种情况。

    这里**只用** project.create_project() 那一套模板,不另写一份 ——
    早期版本这里自己拼过一个更简陋的 project.godot,和 create_project()
    写的内容不一致,两个地方都能建 project.godot 必然打架。
    """
    project.create_project(export_dir, title=project_name)
    path = os.path.join(export_dir, "project.godot")
    # create_project() 一定会把它建出来,所以这里不需要再兜底拼一份 ——
    # 曾经留着一段"如果不存在就自己写"的老代码,和模板不一致且永远不会执行。
    return path if os.path.exists(path) else None
