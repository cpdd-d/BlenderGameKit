"""界面示例(第三十七轮:UI 设计)—— 一屏把界面节点全用一遍。

用法:
    blender -b --gpu-backend vulkan --python scripts\\make_ui_demo.py
产出:
    examples/demo_ui/界面示例.blend
    examples/demo_ui/界面示例_godot/     (可用 Godot 直接打开)

── 这一屏在看什么 ────────────────────────────────────────────────
一块 HUD 底板上叠了**图片**(徽标)+ **文本**(得分)+ **进度条**(血条);
底部居中一行提示;开场弹一个**对话框**(逐字显示,点一下继续);
按 **Esc** 弹出**暂停菜单**(世界全停、鼠标放出来)。

| 用到的 | 在游戏里怎么看 |
|---|---|
| **界面_面板** | 左上角那块半透明圆角底板;暂停菜单的那块大底板 |
| **界面_图片** | 左上角的徽标:Blender 里生成的图,导出时落盘成 art/ui_*.png |
| **界面_文本 / 进度条** | "得分 0" + 血条(0.7 满条) |
| **『父控件』** | 上面这些**挂在 HUD 底板上** —— 底板一藏,整块跟着藏; |
| | 暂停菜单的三个控件同样挂在菜单底板上 |
| **『对齐』** | 底部提示 = 底部居中(窗口变了它跟着走);暂停菜单 = 正中 |
| **界面_对话框** | 开场那句"欢迎!"逐字冒出来;点一下补完,再点一下关掉 |
| **界面_设置** | 每物理帧把血条调成半透明;对话结束后把"得分"改成"对话结束" |
| **界面_暂停游戏** | **Esc** 开关暂停(世界冻住 + 菜单弹出 + 鼠标放出来) |
| **界面_按钮** | 菜单里的『继续游戏』『退出游戏』—— 暂停的时候照样点得动 |
| **对话_选择** | 最后一句话之后弹出**结局选择**(救她 / 自己跑 / 再想想):点第 2 个 -> 走第 2 条链 -> 结局变量 = 2 -> "得分"变成"结局:独自逃出";数字键 1~3 也能选 |

── 第二版(第三十八轮)新加的五样 ──────────────────────────────

| 用到的 | 在游戏里怎么看 |
|---|---|
| **界面_列表** | 暂停菜单里的『存档列表』:两个存档槽**自动从上往下排**(不用手填 X / Y),点哪个读哪档;左下角还有一个『日志列表』 |
| **界面_列表_填充** | 『日志列表』里那三行日志 —— 一个节点灌一个数组,每行一条 |
| **界面_九宫格** | 暂停菜单顶上的**标题条**:一张 64×64 的小图,四边不拉伸、中间拉满 —— 拉多宽边框都不变形(切片 = 14) |
| **界面_输入框** | 左上角那个昵称框:**回车**提交(对话结束后它自动获得焦点,键盘直接能打字),提交后"得分"变成你打的名字 |
| **界面_缓动** | 输入框淡入(透明度 0.15 -> 1,0.2 秒);**界面_设焦点** 紧接着把焦点放到它身上 |
| **界面_设焦点** | 对话结束后焦点落在昵称框上 —— 键盘用户不用摸鼠标 |

── 第四十轮:界面设计图(套用式)─────────────────────────────

| 用到的 | 在游戏里怎么看 |
|---|---|
| **游戏界面(设计图)** | 右上角那块绿色小 HUD —— 它**不在玩法图里摆**,画在一张独立的『游戏界面』节点图里(Blend 里叫 `设计_HUD`) |
| **界面_套用设计** | 玩法图里就这一个节点:运行时把设计图里的 3 个控件(底板 / 标题 / 按钮)一次建出来 |
| **界面_按钮点击** | 点右上角『点我』-> "得分"变成"设计按钮被点了"(设计图只管长什么样,点了做什么写在玩法图,按控件名接) |

── 第一百零四轮:设置面板三件套 + 界面层级 ─────────────────────

| 用到的 | 在游戏里怎么看 |
|---|---|
| **界面_滑块** | 按 **O** 弹出的设置面板里那行『音量 ●———○ -14 dB』—— 右边那串数字**跟着拖动实时变**;拖一次就走一遍『变了』-> 真的改了 Godot 的 Master 总线音量 |
| **界面_下拉(下拉菜单)** | 『画质』那一行:点开小列表选 低/中/高 -> 『选了』-> 多路分支 -> 『画质_设置』(视口缩放 / 抗锯齿真的变) |
| **界面_下拉(左右切换)** | 『难度』那一行:`◀ 困难 ▶` 点箭头换项(到头绕回);选完把『选了第几个』写进"得分"(`界面_设置` 改文字) |
| **界面_复选框** | 『显示背景板』:勾/不勾 -> 『改了』-> 背景板跟着显隐(布尔输出直接接『显示_设置可见』的『可见』) |
| **互斥组 + 置顶**(层级) | 『设置面板』和『背包面板』卡上都填了互斥组『侧栏面板』,打开它们的『界面_隐藏控件』都勾了『置顶』—— 按 **O** 开设置、按 **B** 开背包(互相挤掉,只留一块);按 **C** 都收起来 |

── 界面层级怎么理解(用户问:"设置 UI 和背包 UI 重叠了怎么办")──────
* **置顶**(打开时 `move_to_front`)= 谁后开谁在上 —— 同时按两个键,最后受理的那块盖在上面;
* **互斥组**(控件卡上填组名)= 同组的界面只留一个开着 —— 按 O 时背包自动收起,反之亦然;
* 两块面板**都没有**进组时才需要靠"置顶"判断谁在前面。

── 第一百零五轮:模态弹窗 + 首焦点 ─────────────────────────────

| 用到的 | 在游戏里怎么看 |
|---|---|
| **『挡住点击』** | 『模态框』盖住一个按钮 —— 弹窗开着时点那块地方,按钮**不会**被按到(以前面板是"不挡"的,点空白处会穿透到后面的按钮) |
| **『点外面关掉』** | 点『模态框』外面任意位置,它自己收起来;收了再点原来那个按钮,按钮就正常响应了 |
| **『打开时首焦点』** | 按 **Esc** 弹出暂停菜单时焦点自己落到里面第一个能聚焦的控件上,方向键直接上下走(不用先摸鼠标 / 按 Tab) |
| **界面_皮肤(补齐)** | 皮肤顺便管住了第 104 轮新加的滑块 / 下拉 / 勾选框 —— 以前它们还是默认主题的浅灰轨道 + 白底下拉,和这套深色圆角皮肤摆一起像拼的 |

── 第一百零六轮:能点的列表 ───────────────────────────────────

| 用到的 | 在游戏里怎么看 |
|---|---|
| **『每行可点』** | 暂停菜单里的『道具列表』:每行是一个**按钮**(点得动),点哪行走『点行』那条链(**点第 2 行 -> "得分"变成 `2`**);左下角那个『日志列表』**故意没勾**,它继续是纯文字行 —— 两个版本同屏对照 |
| **选中高亮** | 点过的行**一直亮着**,再点另一行时前一行自动灭(行是 toggle + 同一个按钮组 = 单选,亮的是皮肤给列表行的 `GKListItem` 样式) |
| **『第几行』** | 整数输出(1 起数),和下拉『选了第几个』同一条读法 —— 按列表控件的名字现读,不用自己存变量 |

跑法(Godot):打开 `界面示例_godot` 直接 F5。
自动检查(不用 Blender,要 Godot):`python scripts\\check_ui_demo.py`
"""

import math
import os
import struct
import sys
import wave

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "examples", "demo_ui")
BLEND_PATH = os.path.join(OUT_DIR, "界面示例.blend")

STAGE = "舞台"
# 生成的徽标图:**程序生成**的 128×128 图(没有磁盘文件),
# 导出时靠 `image.save_render` 落盘 —— 这条路正好一起验了。
LOGO_NAME = "徽标"
LOGO_SIZE = 128
# 九宫格标题条的底图(第三十八轮):64×64、边框 14 像素 —— 『切片』填 14
BAR_NAME = "标题条图"
BAR_SIZE = 64
BAR_BORDER = 14
DIALOG_TEXT = ("欢迎!这是界面示例:图片、血条、对话框和暂停菜单都在这一屏。"
               "点一下继续。")
# 第六十四轮:打字机音效 —— 程序生成一个小 wav 放进导出目录的 audio/
TYPE_SOUND_FILE = "gk_type.wav"
TYPE_SOUND_RES = f"res://audio/{TYPE_SOUND_FILE}"
# 第四十轮:界面设计图(『游戏界面』)—— 右上角那块小 HUD 摆在这张**独立图**里
DESIGN_NAME = "设计_HUD"


def log(message):
    print(f"[界面示例] {message}")


def clear_scene():
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for group in list(bpy.data.node_groups):
        if getattr(group, "bl_idname", "").startswith("GK"):
            bpy.data.node_groups.remove(group)
    scene.name = "界面示例"
    return scene


def make_box(name, size, location, color=None, scene=None):
    scene = scene or bpy.context.scene
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (size[0], size[1], size[2])
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if color is not None:
        material = bpy.data.materials.new(f"{name}_材质")
        material.use_nodes = True
        principled = material.node_tree.nodes.get("Principled BSDF")
        if principled is not None:
            principled.inputs["Base Color"].default_value = color
        obj.data.materials.append(material)
    return obj


def make_logo_image():
    """生成一张 128×128 的徽标图(橙色圆盘 + 深色外圈)。

    ── 为什么要**程序生成**而不是读磁盘上的 png ──────────────────────
    示例工程不该依赖"某个文件恰好在你机器上的某个路径";而且程序生成的
    图没有 filepath,正好逼着导出走 `save_render`(而不是 `image.save()`)——
    这条路以前只被材质贴图间接覆盖过。
    """
    image = bpy.data.images.get(LOGO_NAME)
    if image is not None:
        bpy.data.images.remove(image)
    image = bpy.data.images.new(LOGO_NAME, LOGO_SIZE, LOGO_SIZE, alpha=True)
    pixels = [0.0] * (LOGO_SIZE * LOGO_SIZE * 4)
    center = (LOGO_SIZE - 1) / 2.0
    for y in range(LOGO_SIZE):
        for x in range(LOGO_SIZE):
            dx = (x - center) / center
            dy = (y - center) / center
            distance = (dx * dx + dy * dy) ** 0.5
            index = (y * LOGO_SIZE + x) * 4
            if distance < 0.72:
                pixels[index] = 1.0            # R
                pixels[index + 1] = 0.62       # G
                pixels[index + 2] = 0.22       # B
                pixels[index + 3] = 1.0        # A
            elif distance < 0.96:
                pixels[index] = 0.16
                pixels[index + 1] = 0.17
                pixels[index + 2] = 0.22
                pixels[index + 3] = 1.0
            else:
                pixels[index + 3] = 0.0        # 圆外透明
    image.pixels = pixels
    image.pack()      # 跟着 .blend 走(不留外部依赖)
    log(f"徽标图:{LOGO_NAME} {LOGO_SIZE}×{LOGO_SIZE}(程序生成 + 打包)")
    return image


def make_bar_image():
    """生成一张 64×64 的标题条底图(深色底 + 一圈亮边框)。

    ── 为什么专门做一张,而不是拿徽标凑(第三十八轮)────────────────
    九宫格的用法是"四边不拉伸、中间拉满" —— 图本身得是一圈**边框**
    (四个角有花样、中间是纯色)切片才对得上;拿圆盘图去切,拉出来的
    四条边会是弧线,一眼假。边框宽度 = 14 像素,正好是节点上『切片』的值。
    """
    image = bpy.data.images.get(BAR_NAME)
    if image is not None:
        bpy.data.images.remove(image)
    image = bpy.data.images.new(BAR_NAME, BAR_SIZE, BAR_SIZE, alpha=True)
    pixels = [0.0] * (BAR_SIZE * BAR_SIZE * 4)
    for y in range(BAR_SIZE):
        for x in range(BAR_SIZE):
            # 到最近一条边的距离:0 = 最外圈
            edge = min(x, y, BAR_SIZE - 1 - x, BAR_SIZE - 1 - y)
            if edge < 2:
                rgba = (0.08, 0.08, 0.12, 1.0)        # 外沿描边
            elif edge < 5:
                rgba = (0.55, 0.45, 0.85, 1.0)        # 亮内线(角的"花样")
            elif edge < BAR_BORDER:
                rgba = (0.22, 0.19, 0.34, 1.0)        # 边框填充
            else:
                rgba = (0.13, 0.14, 0.20, 0.95)       # 中间(拉伸区)
            index = (y * BAR_SIZE + x) * 4
            pixels[index:index + 4] = list(rgba)
    image.pixels = pixels
    image.pack()
    log(f"标题条图:{BAR_NAME} {BAR_SIZE}×{BAR_SIZE} 边框 {BAR_BORDER}"
        f"(程序生成 + 打包)")
    return image


def build_scene():
    scene = clear_scene()
    # ⚠ 必须有**不止一个网格**:整场景只有"角色"一个网格时插件不会生成
    # 主场景 .tscn(它以为这一关没东西可看)—— 那探针就没有 World.tscn
    # 可加载了。这块背景板同时也是"世界停没停"的背景参照物。
    make_box("背景板", (16.0, 9.0, 0.2), (0.0, 0.0, -1.0),
             (0.14, 0.16, 0.22, 1.0))
    stage = make_box(STAGE, (2.0, 2.0, 0.2), (0.0, 0.0, 0.0),
                     (0.20, 0.24, 0.32, 1.0))
    make_logo_image()
    make_bar_image()
    log(f"场景:背景板 + {STAGE}(挂脚本的载体;这一屏的重点在界面,不在玩法)")
    return scene, stage


def write_physics(scene, stage):
    """只有『舞台』要挂脚本:3D + CharacterBody3D。

    ── 为什么是 3D(而不是 2D)──────────────────────────────────────
    界面和维度无关,但这一屏用了**角色预设**(它生成的是 3D 的移动节点:
    `移动_按方向` 会把脚本节点当成 Node3D 用)。场景声明成 2D 的话,
    生成出来的脚本 extends CharacterBody2D,而里面满是 Node3D 的强转 ——
    Godot 直接报 Parse Error(真踩过:`UiDemoPlayer` 不能转 `Node3D`)。
    所以这里老实按 3D 走,预设和场景一致。
    """
    for obj in scene.objects:
        physics = getattr(obj, "gk_physics", None)
        if physics is None:
            continue
        try:
            physics.shape_size = (0.0, 0.0, 0.0)
        except Exception:                                 # noqa: BLE001
            pass
        physics.dimension = "3"
        if obj is stage:
            physics.body_type = "CharacterBody3D"
            physics.script_target = True
        elif getattr(obj, "type", "") == "MESH":
            physics.body_type = "StaticBody3D"


def _add(tree, kind, name, x, y, **props):
    node = tree.nodes.new(kind)
    node.name = name
    node.location = (x, y)
    for key, value in props.items():
        setattr(node, key, value)
    return node


def _link(tree, source, source_pin, target, target_pin):
    tree.links.new(source.outputs[source_pin], target.inputs[target_pin])


def build_design_tree():
    """第四十轮:界面设计图 —— 右上角那块小 HUD 摆在这张**独立图**里。

    ── 这就是用户选的"套用式" ─────────────────────────────────────
    设计图只管"长什么样"(控件、坐标、父子);玩法图里只放一个
    『界面_套用设计』(见 `_build_ui_chain`),运行时把这三个控件一次建出来。
    "点了做什么"写在玩法图:`界面_按钮点击` 按控件名接行为。
    """
    design = bpy.data.node_groups.new(DESIGN_NAME, "GKUiDesignTree")
    panel = _add(design, "GKNodeUiPanel", "设计底板", 0.0, 0.0,
                 gk_width=286.0, gk_height=98.0, gk_radius=12,
                 gk_align="TOP_RIGHT", gk_margin=14.0,
                 gk_color=(0.10, 0.22, 0.17, 0.72))
    text = _add(design, "GKNodeUiText", "设计标题", 300.0, 0.0,
                gk_text="设计图 HUD", gk_size=22,
                gk_color=(0.78, 1.0, 0.86, 1.0),
                gk_x=16.0, gk_y=12.0, gk_parent_widget="设计底板")
    button = _add(design, "GKNodeUiButton", "设计按钮", 600.0, 0.0,
                  gk_text="点我", gk_x=16.0, gk_y=48.0,
                  gk_width=120.0, gk_height=38.0,
                  gk_parent_widget="设计底板")
    log(f"设计图『{design.name}』:{len(design.nodes)} 个控件"
        f"(底板 / 标题 / 按钮,后两个挂在底板上)")
    return design


def build_graph(scene, stage):
    """图:开场把整套界面搭出来 -> 对话框;每物理帧改血条透明度。

    ⚠ 执行流是**一条链**走到头:套用设计图 -> 面板 -> 底板上的控件 ->
    暂停菜单 -> 菜单里的控件 -> 挂 Esc 热键 -> 底部提示 -> 对话框 -> 改文字。
    顺序不能乱:『父控件』是按名字找的,底板必须先建出来;
    而对话框会**阻塞**这条链(等玩家点),所以它必须放在最后。
    """
    from BlenderGameKit import presets

    logic, _anim, notes = presets.create_character_preset(
        scene, character=stage,
        logic_name="界面逻辑",
        class_name="UiDemoPlayer",
        with_animation=False,
        with_godot_camera=False,
    )
    for note in notes:
        log(f"预设: {note}")
    ready = None
    physics = None
    for node in logic.nodes:
        if node.bl_idname == "GKNodeOnReady":
            ready = node
        elif node.bl_idname == "GKNodeOnPhysicsProcess":
            physics = node
    if ready is None or physics is None:
        raise RuntimeError("角色预设里没有『准备』/『每当_物理帧』节点")
    # 预设建的那条移动链对"只看界面"的示例没意义 —— 但留着也不碍事
    # (WASD 会让舞台挪一挪,正好能看出"世界真的停了")。
    design = build_design_tree()
    tail = _build_ui_chain(logic, ready, design)
    _build_frame_chain(logic, physics)
    log(f"界面逻辑:共 {len(logic.nodes)} 个节点 / {len(logic.links)} 条连线"
        f"(界面链尾部: {tail.name})")
    return logic


def _build_ui_chain(tree, ready, design):
    """开场链:套用设计图 -> 整套界面 + 对话框。返回链尾(便于探针/日志对照)。

    ── 第四十轮加的"套用式"入口 ────────────────────────────────────
    `ready -> 套用设计HUD -+-> 设计按钮点击`(只接行为,不建控件)
                          `+-> HUD底 ...`(原来那条链照旧)
    一个执行出口接两条线是**故意**的:两条互不依赖(接行为和建界面),
    生成时按节点在画布上的上下位置依次出码(见 registry._next_nodes)。
    """
    # ── 设计图那条线:一个『界面_套用设计』+ 一个『界面_按钮点击』──────
    # 第八十一轮:最前面先摆一个『界面_皮肤』—— 主题挂到 UI 根上之后,
    # 后面建的控件(含套用设计展开出来的)自动继承(按钮三态 / 焦点高亮 /
    # 滚动条 / 输入框底色一次全换)
    skin = _add(tree, "GKNodeUiSkin", "皮肤", -260.0, -300.0,
                gk_font_size=22, gk_radius=10,
                gk_button_color=(0.18, 0.22, 0.34, 0.95),
                gk_focus_color=(1.0, 0.78, 0.35, 0.85),
                gk_scroll_color=(0.75, 0.85, 1.0, 0.35))
    apply_design = _add(tree, "GKNodeUiApplyDesign", "套用设计HUD", 0.0, -300.0,
                        gk_design=design.name)
    design_click = _add(tree, "GKNodeUiOnClick", "设计按钮点击", 260.0, -300.0,
                        gk_widget="设计按钮")
    design_reply = _add(tree, "GKNodeUiSet", "设计按钮回话", 520.0, -300.0,
                        gk_widget="分数", gk_set_text=True,
                        gk_text="设计按钮被点了")
    _link(tree, design_click, "点击", design_reply, "执行")
    hud = _add(tree, "GKNodeUiPanel", "HUD底", 0.0, 0.0,
               gk_width=320.0, gk_height=130.0, gk_radius=14,
               gk_x=12.0, gk_y=12.0,
               gk_color=(0.06, 0.07, 0.10, 0.55))
    logo = _add(tree, "GKNodeUiImage", "徽标", 260.0, 0.0,
                gk_image=LOGO_NAME, gk_width=72.0, gk_height=72.0,
                gk_x=14.0, gk_y=14.0, gk_parent_widget="HUD底")
    score = _add(tree, "GKNodeUiText", "分数", 520.0, 0.0,
                 gk_text="得分 0", gk_size=24, gk_x=100.0, gk_y=26.0,
                 gk_parent_widget="HUD底")
    bar = _add(tree, "GKNodeUiBar", "血条", 780.0, 0.0,
               gk_value=0.7, gk_max=1.0, gk_width=292.0, gk_height=16.0,
               gk_x=14.0, gk_y=96.0, gk_color=(0.35, 0.85, 0.45, 1.0),
               gk_parent_widget="HUD底")
    # ── 第八十一轮『音效_播放』循环:开局开一条"引擎声"(一直响)────────
    # 点『继续游戏』时用『音效_停止』(填同一个名字)把它停掉 ——
    # 探针分别在"暂停前"(在响)和"继续后"(停了)各看一次
    engine = _add(tree, "GKNodePlaySound", "引擎声", 780.0, -300.0,
                  gk_path=TYPE_SOUND_RES, gk_loop=True, gk_name="引擎",
                  gk_2d=False, gk_volume=-14.0)
    stop_engine = _add(tree, "GKNodeStopSound", "停引擎声", 3380.0, -400.0,
                       gk_name="引擎")
    # ── 暂停菜单:底板(正中、开局藏)+ 控件挂在它下面 ──────────
    # 菜单里从上往下:标题条(九宫格底图)→ 存档列表(两个槽)→ 两个按钮
    menu = _add(tree, "GKNodeUiPanel", "暂停菜单", 1040.0, 0.0,
                gk_width=440.0, gk_height=430.0, gk_radius=18,
                gk_align="CENTER", gk_start_hidden=True,
                # 第一百零五轮:一弹出来就把焦点交给里面第一个能聚焦的
                # 控件,并把方向键串成一条链 —— 键盘直接上下选,不用摸鼠标
                gk_focus_first=True,
                gk_color=(0.05, 0.06, 0.09, 0.86))
    title = _add(tree, "GKNodeUiText", "暂停标题", 1300.0, 0.0,
                 gk_text="已暂停", gk_size=30, gk_align="TOP_CENTER",
                 gk_margin=26.0, gk_parent_widget="暂停菜单")
    # 标题条**故意放在标题文字后面建**:勾了『垫底』它会被挪到最下面,
    # 不会盖住先建好的"已暂停"(这就是垫底存在的意义)
    title_bar = _add(tree, "GKNodeUiNinePatch", "标题条", 1300.0, -260.0,
                     gk_image=BAR_NAME, gk_slice=BAR_BORDER,
                     gk_x=20.0, gk_y=14.0, gk_width=400.0, gk_height=54.0,
                     gk_bottom=True, gk_parent_widget="暂停菜单")
    # ── 存档列表:两个槽(按钮)挂进容器,自动从上往下排 ──────────
    save_list = _add(tree, "GKNodeUiList", "存档列表", 1560.0, 0.0,
                     gk_x=130.0, gk_y=84.0, gk_width=180.0, gk_height=112.0,
                     gk_separation=6, gk_parent_widget="暂停菜单")
    slot_one = _add(tree, "GKNodeUiButton", "存档槽一", 1820.0, 0.0,
                    gk_text="存档一", gk_width=170.0, gk_height=40.0,
                    gk_parent_widget="存档列表")
    slot_two = _add(tree, "GKNodeUiButton", "存档槽二", 1820.0, -200.0,
                    gk_text="存档二", gk_width=170.0, gk_height=40.0,
                    gk_parent_widget="存档列表")
    resume = _add(tree, "GKNodeUiButton", "继续按钮", 2080.0, 0.0,
                  gk_text="继续游戏", gk_x=130.0, gk_y=206.0,
                  gk_width=180.0, gk_height=48.0,
                  # 第八十一轮『界面音效』:点了响一声(复用打字音那个 wav)
                  gk_click_sound=TYPE_SOUND_RES, gk_click_volume=-8.0,
                  gk_parent_widget="暂停菜单")
    quit_button = _add(tree, "GKNodeUiButton", "退出按钮", 2080.0, -260.0,
                       gk_text="退出游戏", gk_x=130.0, gk_y=262.0,
                       gk_width=180.0, gk_height=48.0,
                       gk_parent_widget="暂停菜单")
    # ── 第一百零六轮:能点的列表(『每行可点』)──────────────────────
    # 以前要"能点的行"得自己一个个摆『界面_按钮』(父控件填列表名);
    # 现在『列表_填充』勾一个开关就行 —— 行建成按钮,点哪行走『点行』,
    # 『第几行』(1 起数)直接可读,而且**选中的那一行会一直亮着**
    # (行是 toggle + 同一个按钮组 = 单选,亮的是皮肤的列表行样式)。
    # ⚠ 左下角那个『日志列表』**故意不勾**:它继续走"纯文字行"的老路,
    #   两个版本摆在同一屏里,一眼能看出区别。
    tool_list = _add(tree, "GKNodeUiList", "道具列表", 2340.0, -700.0,
                     gk_x=30.0, gk_y=318.0, gk_width=380.0, gk_height=84.0,
                     gk_separation=4, gk_parent_widget="暂停菜单")
    fill_tools = _add(tree, "GKNodeUiListFill", "填道具", 2600.0, -700.0,
                      gk_list="道具列表", gk_clickable=True,
                      gk_text="手电筒|钥匙|绷带", gk_size=20,
                      gk_color=(0.90, 0.93, 1.0, 1.0))
    tool_index = _add(tree, "GKNodeConvert", "道具转文字", 2860.0, -700.0,
                      gk_to="String")
    tool_reply = _add(tree, "GKNodeUiSet", "道具回话", 3120.0, -700.0,
                      gk_widget="分数", gk_set_text=True)
    # ── 左下角:日志列表(只显示的行,用『列表_填充』灌三行)────────
    logs = _add(tree, "GKNodeUiList", "日志列表", 2340.0, 0.0,
                gk_width=260.0, gk_height=120.0, gk_separation=4,
                gk_align="BOTTOM_LEFT", gk_margin=16.0)
    fill = _add(tree, "GKNodeUiListFill", "填日志", 2600.0, 0.0,
                gk_list="日志列表",
                gk_text="拾取了钥匙|打开了大门|抵达终点",
                gk_size=20, gk_color=(0.82, 0.86, 0.95, 1.0))
    # ── 昵称输入框:回车提交,提交后把字写进"得分" ────────────────
    nickname = _add(tree, "GKNodeUiInput", "昵称", 2860.0, 0.0,
                    gk_placeholder="输入昵称,回车确认",
                    gk_x=12.0, gk_y=152.0, gk_width=260.0, gk_height=40.0,
                    gk_max_length=12,
                    # 第八十一轮:吃中文输入法 / 手机软键盘,候选框跟光标走
                    gk_ime=True)
    # ── 第八十一轮:多行聊天框(Ctrl+回车提交)─────────────────────
    # 挂在日志列表下面(左下角);提交后把框里的字**原样**写进"得分"
    # (读聊天 -> 设置 的"文字"引脚这条线)
    chat = _add(tree, "GKNodeUiTextEdit", "聊天框", 2860.0, 240.0,
                gk_placeholder="打一行字,Ctrl+回车发送",
                gk_x=12.0, gk_y=210.0, gk_width=260.0, gk_height=72.0)
    read_chat = _add(tree, "GKNodeUiReadText", "读聊天", 3120.0, 400.0,
                     gk_widget="聊天框")
    send_chat = _add(tree, "GKNodeUiSet", "发出聊天", 3120.0, 240.0,
                     gk_widget="分数", gk_set_text=True)
    read_name = _add(tree, "GKNodeUiReadText", "读昵称", 3120.0, -120.0,
                     gk_widget="昵称")
    write_name = _add(tree, "GKNodeUiSet", "把名字写进得分", 3120.0, 0.0,
                      gk_widget="分数", gk_set_text=True)
    # ── 第一百零四轮:设置面板(滑块 / 下拉 / 复选框)─────────────
    # 两块面板填**同一个互斥组**『侧栏面板』+ 打开它们的节点勾『置顶』:
    # 开一块 -> 另一块自动收起(同时按两个键也只会开出一块)。
    settings = _add(tree, "GKNodeUiPanel", "设置面板", 2860.0, 900.0,
                    gk_width=420.0, gk_height=300.0, gk_radius=18,
                    gk_align="CENTER_RIGHT", gk_margin=24.0,
                    gk_start_hidden=True, gk_mutex_group="侧栏面板",
                    gk_color=(0.05, 0.06, 0.09, 0.88))
    settings_title = _add(tree, "GKNodeUiText", "设置标题", 3120.0, 900.0,
                          gk_text="设置", gk_size=28, gk_align="TOP_CENTER",
                          gk_margin=14.0, gk_parent_widget="设置面板")
    volume = _add(tree, "GKNodeUiSlider", "音量滑块", 3380.0, 900.0,
                  gk_min=-40.0, gk_max=0.0, gk_step=1.0, gk_value=-14.0,
                  gk_label="音量", gk_suffix=" dB",
                  gk_x=28.0, gk_y=70.0, gk_width=364.0, gk_height=36.0,
                  gk_parent_widget="设置面板")
    quality_pick = _add(tree, "GKNodeUiDropdown", "画质选择", 3380.0, 700.0,
                        gk_items="低|中|高", gk_style="NATIVE", gk_index=3,
                        gk_label="画质",
                        gk_x=28.0, gk_y=122.0, gk_width=364.0, gk_height=36.0,
                        gk_parent_widget="设置面板")
    level_pick = _add(tree, "GKNodeUiDropdown", "难度选择", 3380.0, 500.0,
                      gk_items="简单|普通|困难", gk_style="ARROWS", gk_index=1,
                      gk_label="难度",
                      gk_x=28.0, gk_y=174.0, gk_width=364.0, gk_height=36.0,
                      gk_parent_widget="设置面板")
    plate_box = _add(tree, "GKNodeUiCheckbox", "显示背景板", 3380.0, 300.0,
                     gk_text="显示背景板", gk_checked=True, gk_style="BOX",
                     gk_x=28.0, gk_y=232.0, gk_width=220.0, gk_height=36.0,
                     gk_parent_widget="设置面板")
    bag = _add(tree, "GKNodeUiPanel", "背包面板", 2860.0, 1500.0,
               gk_width=260.0, gk_height=170.0, gk_radius=16,
               gk_align="CENTER_LEFT", gk_margin=24.0,
               gk_start_hidden=True, gk_mutex_group="侧栏面板",
               gk_color=(0.09, 0.07, 0.05, 0.88))
    bag_title = _add(tree, "GKNodeUiText", "背包标题", 3120.0, 1500.0,
                     gk_text="背包", gk_size=26, gk_align="TOP_CENTER",
                     gk_margin=14.0, gk_parent_widget="背包面板")
    bag_text = _add(tree, "GKNodeUiText", "背包内容", 3120.0, 1300.0,
                    gk_text="钥匙 ×1 / 手电 ×1", gk_size=20,
                    gk_align="TOP_CENTER", gk_margin=64.0,
                    gk_parent_widget="背包面板")
    # ── 第一百零五轮:模态弹窗(挡住点击 + 点外面关)──────────────────
    # 『穿透按钮』先建、『模态框』后建 —— 后建的画在上面,弹窗才盖得住按钮。
    # 探针的三步对照:
    #   ① 弹窗开着时点按钮那一块 -> 点被弹窗吃掉,按钮**没**响应(挡住点击);
    #   ② 点弹窗外面 -> 弹窗自己收起来(点外面关);
    #   ③ 弹窗收了再点同一个位置 -> 按钮正常响应("真的被挡住了,不是坏了")。
    # ⚠ 『垫底』**不能勾**:勾了会被挪到最下面,就盖不住按钮了。
    pierce = _add(tree, "GKNodeUiButton", "穿透按钮", 2860.0, 1800.0,
                  gk_text="点我(穿透测试)", gk_x=820.0, gk_y=200.0,
                  gk_width=200.0, gk_height=44.0)
    modal = _add(tree, "GKNodeUiPanel", "模态框", 3120.0, 1800.0,
                 gk_width=240.0, gk_height=84.0, gk_radius=14,
                 gk_x=800.0, gk_y=180.0,
                 gk_bottom=False, gk_start_hidden=True,
                 gk_block_click=True, gk_close_outside=True,
                 gk_color=(0.24, 0.09, 0.12, 0.92))
    modal_text = _add(tree, "GKNodeUiText", "模态提示", 3380.0, 1800.0,
                      gk_text="点外面关掉我", gk_size=22, gk_align="CENTER",
                      gk_parent_widget="模态框")
    pierce_reply = _add(tree, "GKNodeUiSet", "穿透回话", 3380.0, 2060.0,
                        gk_widget="分数", gk_set_text=True,
                        gk_text="穿透了!")
    _link(tree, pierce, "点击", pierce_reply, "执行")
    # 三条行为链(挂在三件套的出口上;生成物在独立回调里,不会开场就跑)
    volume_bus = _add(tree, "GKNodeBusVolume", "滑块调音量", 3640.0, 900.0,
                      gk_bus="Master", gk_fade=0.0)
    quality_switch = _add(tree, "GKNodeSwitch", "看画质走哪条", 3640.0, 620.0,
                          gk_case_count=4)
    quality_nodes = []
    for _index, (_label, _level) in enumerate((("低", "LOW"), ("中", "MID"),
                                               ("高", "HIGH"))):
        quality_nodes.append(_add(tree, "GKNodeQualitySet", f"画质{_label}档",
                                  3920.0, 520.0 - _index * 60.0,
                                  gk_level=_level))
    level_text = _add(tree, "GKNodeConvert", "难度转文字", 3640.0, 340.0,
                      gk_to="String")
    write_level = _add(tree, "GKNodeUiSet", "显示难度", 3920.0, 340.0,
                       gk_widget="分数", gk_set_text=True)
    plate_visible = _add(tree, "GKNodeSetVisible", "背景板跟着勾走",
                         3640.0, 120.0, gk_target="背景板")
    hotkey = _add(tree, "GKNodeUiPauseGame", "暂停热键", 3380.0, 0.0,
                  gk_pause=False, gk_menu="暂停菜单", gk_hotkey=True)
    hint = _add(tree, "GKNodeUiText", "底部提示", 3640.0, 0.0,
                gk_text="点一下继续对话 / Esc 暂停 / O 设置 / B 背包 / C 收起 / M 弹窗 / 昵称框回车",
                gk_size=20, gk_align="BOTTOM_CENTER", gk_margin=28.0)
    dialog = _add(tree, "GKNodeUiDialog", "开场白", 3900.0, 0.0,
                  gk_speaker="向导", gk_text=DIALOG_TEXT,
                  gk_speed=28.0, gk_width=640.0, gk_height=150.0,
                  gk_margin=56.0,
                  # 第六十四轮:逐字显示时每 2 个字响一下"嗒"(打字机音效)
                  gk_type_sound=TYPE_SOUND_RES, gk_type_every=2,
                  gk_type_volume=-10.0)
    after = _add(tree, "GKNodeUiSet", "改分数文字", 4160.0, 0.0,
                 gk_widget="分数", gk_set_text=True, gk_text="对话结束")
    # 对话点完 = "选择完成":继续世界 + 把鼠标锁回去(第四十六轮)
    release = _add(tree, "GKNodeUiPauseGame", "选完恢复", 4160.0, -620.0,
                   gk_pause=False, gk_show_mouse=True, gk_menu="",
                   gk_hotkey=False)
    lock_mouse = _add(tree, "GKNodeMouseMode", "锁回鼠标", 4420.0, -620.0,
                      gk_mode="CAPTURED")
    # 对话结束后:输入框淡入(缓动) -> 焦点交给它(键盘直接能打字)
    dim = _add(tree, "GKNodeUiSet", "昵称半透明", 4420.0, 0.0,
               gk_widget="昵称", gk_set_alpha=True, gk_alpha=0.15)
    fade_in = _add(tree, "GKNodeUiTween", "淡入昵称", 4680.0, 0.0,
                   gk_widget="昵称", gk_property="ALPHA", gk_to=1.0,
                   gk_time=0.2, gk_wait=True)
    focus_field = _add(tree, "GKNodeUiFocus", "聚焦昵称", 4940.0, 0.0,
                       gk_widget="昵称")

    _link(tree, ready, "执行", apply_design, "执行")
    # ── 第四十六轮:开局"选单态"(用户:"一开始应该启用鼠标,禁用鼠标旋转,
    #    在选择完成后再启用")──────────────────────────────────────────
    # 做法:开局**暂停世界 + 放出鼠标** —— 世界停着,移动 / 鼠标转视角
    # 全都不跑(角色脚本是"可暂停"的);玩家把开场对话框点完(= 选择完成)
    # 之后,『选完恢复』继续世界,『锁回鼠标』显式把鼠标锁回去。
    # ⚠ 必须挂在『暂停热键』**后面**:那个节点在准备时也会调一次
    #   "取消暂停"(它是"开/关"型),挂在它前面会被它覆盖掉(实测:
    #   探针看到 paused=false)。
    startup = _add(tree, "GKNodeUiPauseGame", "开局选单态", 3120.0, 160.0,
                   gk_pause=True, gk_show_mouse=True, gk_menu="",
                   gk_hotkey=False)
    # 一个出口接两条:先接按钮行为,再把整套界面建出来(两条互不依赖)
    _link(tree, ready, "执行", skin, "执行")
    _link(tree, skin, "完成", apply_design, "执行")
    _link(tree, apply_design, "完成", design_click, "执行")
    _link(tree, apply_design, "完成", hud, "执行")
    _link(tree, hud, "完成", logo, "执行")
    _link(tree, logo, "完成", score, "执行")
    _link(tree, score, "完成", bar, "执行")
    # 血条建好后开"引擎声"(循环音效,一直响到点『继续游戏』)
    _link(tree, bar, "完成", engine, "执行")
    _link(tree, engine, "完成", menu, "执行")
    _link(tree, menu, "完成", title, "执行")
    _link(tree, title, "完成", title_bar, "执行")
    _link(tree, title_bar, "完成", save_list, "执行")
    _link(tree, save_list, "完成", slot_one, "执行")
    _link(tree, slot_one, "完成", slot_two, "执行")
    _link(tree, slot_two, "完成", resume, "执行")
    _link(tree, resume, "完成", quit_button, "执行")
    # ── 第八十八轮:画质三档(点了**真的改渲染设置**)────────────────
    # 三个按钮放**左上角**(不进暂停菜单):菜单是居中弹的,而『对话_选择』
    # 面板也居中 —— 探针点它的时候正好两个都可能开着,放菜单里会被面板盖住。
    # 它们仍然接在**同一条建控件的链上**(界面控件是按名字找父级的),
    # 各自的『点击』接一个『画质_设置』。探针点『画质中』,断言视口缩放 /
    # 抗锯齿真的变了(不是插件自己的开关)。
    quality_buttons = []
    for _index, (_label, _level) in enumerate(
            (("画质低", "LOW"), ("画质中", "MID"), ("画质高", "HIGH"))):
        quality_button = _add(tree, "GKNodeUiButton", _label,
                              2860.0, -1300.0 - _index * 60.0,
                              gk_text=_label, gk_x=1022.0,
                              gk_y=452.0 + _index * 48.0,
                              gk_width=110.0, gk_height=40.0)
        quality_apply = _add(tree, "GKNodeQualitySet", f"{_label}生效",
                             3120.0, -1300.0 - _index * 60.0,
                             gk_level=_level)
        _link(tree, quality_button, "点击", quality_apply, "执行")
        quality_buttons.append(quality_button)
    _link(tree, quit_button, "完成", tool_list, "执行")
    _link(tree, tool_list, "完成", fill_tools, "执行")
    _link(tree, fill_tools, "完成", quality_buttons[0], "执行")
    _link(tree, quality_buttons[0], "完成", quality_buttons[1], "执行")
    _link(tree, quality_buttons[1], "完成", quality_buttons[2], "执行")
    _link(tree, quality_buttons[2], "完成", logs, "执行")
    _link(tree, logs, "完成", fill, "执行")
    _link(tree, fill, "完成", chat, "执行")
    _link(tree, chat, "完成", nickname, "执行")
    # ⚠ 顺序:『暂停热键』在前(它在准备时会调一次"取消暂停"—— 它是开/关型),
    #   我的『开局选单态』在后,才不会被它覆盖(实测踩过:paused=false)。
    # 第一百零四轮把设置面板/背包面板插在中间:它们只"建出来"(开局都藏着),
    # 什么时候开由 O / B 键决定(见 _build_frame_chain)。
    _link(tree, nickname, "完成", settings, "执行")
    _link(tree, settings, "完成", settings_title, "执行")
    _link(tree, settings_title, "完成", volume, "执行")
    _link(tree, volume, "完成", quality_pick, "执行")
    _link(tree, quality_pick, "完成", level_pick, "执行")
    _link(tree, level_pick, "完成", plate_box, "执行")
    _link(tree, plate_box, "完成", bag, "执行")
    _link(tree, bag, "完成", bag_title, "执行")
    _link(tree, bag_title, "完成", bag_text, "执行")
    _link(tree, bag_text, "完成", pierce, "执行")
    _link(tree, pierce, "完成", modal, "执行")
    _link(tree, modal, "完成", modal_text, "执行")
    _link(tree, modal_text, "完成", hotkey, "执行")
    _link(tree, hotkey, "完成", startup, "执行")
    _link(tree, startup, "完成", hint, "执行")
    _link(tree, hint, "完成", dialog, "执行")
    _link(tree, dialog, "完成", release, "执行")
    _link(tree, release, "完成", lock_mouse, "执行")
    _link(tree, lock_mouse, "完成", after, "执行")
    _link(tree, after, "完成", dim, "执行")
    _link(tree, dim, "完成", fade_in, "执行")
    _link(tree, fade_in, "完成", focus_field, "执行")

    # ── 第五十轮:剧情分支(『对话_选择』)────────────────────────────
    # 挂在整条界面链的**最尾**:前面的检查(控件 / 暂停 / 输入框 / 设计图)
    # 都跟"选了哪个"无关 —— 选择节点会**阻塞**在这条链上等玩家选。
    choice = _add(tree, "GKNodeUiChoice", "结局选择", 5200.0, 0.0,
                  gk_question="你怎么做？", gk_option_count=3,
                  gk_options="救她|自己跑|再想想", gk_speed=0.0)
    # 出口 1 / 2 都把『选了第几个』写进『结局』变量(演示那个整数输出),
    # 再把"得分"改成对应结局 —— 探针点第 2 个,查的就是这条链。
    pick_one = _add(tree, "GKNodeSetVar", "记结局", 5460.0, 120.0,
                    gk_var_name="结局")
    one_text = _add(tree, "GKNodeUiSet", "显示救她", 5720.0, 120.0,
                    gk_widget="分数", gk_set_text=True, gk_text="结局:回头救她")
    pick_two = _add(tree, "GKNodeSetVar", "记结局2", 5460.0, -300.0,
                    gk_var_name="结局")
    two_text = _add(tree, "GKNodeUiSet", "显示独自逃出", 5720.0, -300.0,
                    gk_widget="分数", gk_set_text=True, gk_text="结局:独自逃出")
    three_text = _add(tree, "GKNodeUiSet", "显示再想想", 5460.0, -700.0,
                      gk_widget="分数", gk_set_text=True, gk_text="结局:再想想")
    # ── 第六十四轮:字幕(不阻塞)+ 对话历史回顾 ───────────────────────
    # 字幕插在『记结局2』和『显示独自逃出』**中间**:字幕不等玩家点,
    # 所以后面那句改分数会**立刻**执行 —— 探针查"分数变成了『结局:独自
    # 逃出』"其实就顺带证明了**字幕没把执行流卡住**(卡住的话分数永远不变)。
    subtitle = _add(tree, "GKNodeUiSubtitle", "旁白字幕", 5460.0, -560.0,
                    gk_text="你独自跑出了教学楼……", gk_seconds=2.5,
                    gk_type_sound=TYPE_SOUND_RES,
                    # 第八十一轮:说话人立绘(复用徽标那张程序生成的图)
                    gk_speaker="旁白", gk_image=LOGO_NAME)
    history = _add(tree, "GKNodeDialogHistory", "回顾", 5980.0, -300.0,
                   gk_title="对话回顾",
                   # 第八十一轮:导出按钮 + 每句可点(点了像字幕一样重飘一遍)
                   gk_export_path="user://对话历史.txt", gk_replay=True)

    _link(tree, focus_field, "完成", choice, "执行")
    _link(tree, choice, "选项1", pick_one, "执行")
    _link(tree, choice, "选了第几个", pick_one, "值")
    _link(tree, pick_one, "完成", one_text, "执行")
    _link(tree, choice, "选项2", pick_two, "执行")
    _link(tree, choice, "选了第几个", pick_two, "值")
    _link(tree, pick_two, "完成", subtitle, "执行")
    _link(tree, subtitle, "完成", two_text, "执行")
    _link(tree, two_text, "完成", history, "执行")
    _link(tree, choice, "选项3", three_text, "执行")

    # 输入框『提交』(回车):读框里的字 -> 写进"得分"
    _link(tree, read_name, "文字", write_name, "文字")
    _link(tree, nickname, "提交", write_name, "执行")
    # 聊天框『提交』(Ctrl+回车):框里的字原样写进"得分"
    _link(tree, read_chat, "文字", send_chat, "文字")
    _link(tree, chat, "提交", send_chat, "执行")

    # ── 第一百零四轮:三件套的行为链 ──────────────────────────────
    # ① 滑块『变了』-> 『音效_总线音量』(目标音量接『值』)—— 拖一下,
    #    Master 总线当场变(时长 0 = 立刻生效,探针好断言);
    # ② 画质下拉『选了』-> 多路分支(拔『选了第几个』)-> 三档画质;
    # ③ 难度(箭头)『选了』-> 把"第几个"转成文字写进"得分"(整数输出);
    # ④ 复选框『改了』-> 『显示_设置可见』(『可见』接『勾了吗』)——
    #    勾/不勾两个方向都跟着走(不接分支,一个节点管来回)。
    _link(tree, volume, "变了", volume_bus, "执行")
    _link(tree, volume, "值", volume_bus, "目标音量")
    _link(tree, quality_pick, "选了", quality_switch, "执行")
    _link(tree, quality_pick, "选了第几个", quality_switch, "值")
    for _index, _apply in enumerate(quality_nodes):
        _link(tree, quality_switch, str(_index + 1), _apply, "执行")
    _link(tree, level_pick, "选了", write_level, "执行")
    _link(tree, level_pick, "选了第几个", level_text, "输入")
    _link(tree, level_text, "输出", write_level, "文字")
    _link(tree, plate_box, "改了", plate_visible, "执行")
    _link(tree, plate_box, "勾了吗", plate_visible, "可见")

    # ── 第一百零六轮:能点的列表 ──────────────────────────────────
    # 『点行』-> 改"得分";写进去的字来自**『第几行』(1 起数)** ——
    # 探针点第 2 行,断言"得分"正好是 `2`:回调链和整数输出一起验了。
    _link(tree, fill_tools, "点行", tool_reply, "执行")
    _link(tree, fill_tools, "第几行", tool_index, "输入")
    _link(tree, tool_index, "输出", tool_reply, "文字")

    # 菜单里的按钮:先解除暂停,再回到游戏 / 退出
    back = _add(tree, "GKNodeUiPauseGame", "继续游戏", 3380.0, -260.0,
                gk_pause=False, gk_menu="暂停菜单", gk_hotkey=False)
    # 继续之前先把"引擎声"停掉(『音效_停止』填同一个名字)
    _link(tree, resume, "点击", stop_engine, "执行")
    _link(tree, stop_engine, "完成", back, "执行")
    quit_pause = _add(tree, "GKNodeUiPauseGame", "退出前解除暂停", 3380.0, -520.0,
                      gk_pause=False, gk_menu="暂停菜单", gk_hotkey=False)
    quit_game = _add(tree, "GKNodeQuitGame", "退出(菜单)", 3640.0, -520.0)
    _link(tree, quit_button, "点击", quit_pause, "执行")
    _link(tree, quit_pause, "完成", quit_game, "执行")
    # 存档槽(能点的行):各接各的点击链(这是"列表里放按钮"的正式用法)
    load_one = _add(tree, "GKNodeUiSet", "读档一", 3640.0, -780.0,
                    gk_widget="分数", gk_set_text=True, gk_text="读取存档一")
    load_two = _add(tree, "GKNodeUiSet", "读档二", 3640.0, -1040.0,
                    gk_widget="分数", gk_set_text=True, gk_text="读取存档二")
    _link(tree, slot_one, "点击", load_one, "执行")
    _link(tree, slot_two, "点击", load_two, "执行")
    log("界面链:皮肤 -> 套用设计图(右上角 HUD,3 个控件) -> 接设计按钮行为 -> "
        "面板 -> 图片 -> 文本 -> 进度条 -> 引擎声(循环) -> 暂停菜单(九宫格标题条 + "
        "存档列表 + 画质三档) -> 日志列表(填充) -> 聊天框(多行) -> "
        "昵称输入框(输入法) -> 设置面板(滑块 + 下拉 + 左右切换 + 复选框)+ "
        "背包面板(互斥组/置顶,O/B/C 开关) -> 提示 -> 对话框 -> 淡入 + 聚焦 -> "
        "结局选择(3 个选项) -> 字幕(带立绘) -> 对话回顾(导出 + 点句重放)")
    return choice


def _build_frame_chain(tree, physics):
    """每物理帧:把血条调成半透明 + 三个按键开关面板。

    ── 为什么留一个"每帧都设一遍"的节点在图上 ──────────────────────
    探针要断言的是"运行时改控件真的生效"(血条的 modulate.a = 0.6);
    一次性设置也行,但每帧刷一遍才是真实用法(血条/图标会随状态变化)。
    顺带还能看出"暂停之后它停了" —— 暂停时血条透明度不会再被刷新。

    ── 第一百零四轮:O / B / C 三个键 ────────────────────────────
    每个键都是"从『每当_物理帧』拉一条独立分支"(curve 示例同一套写法:
    一个执行出口接多条的,生成时按画布上下位置依次出码)。
      * O = 开设置面板、B = 开背包面板 —— **都勾了『置顶』**,所以
        开一块就自动收起互斥组(『侧栏面板』)里的另一块;
      * C = 两块都收起来。
      * M = 弹『模态框』(第一百零五轮)—— 它勾了『挡住点击』+
        『点外面关掉』,所以点它盖住的那块地方不会"隔山打牛"到按钮,
        点它外面它自己收起来。
    """
    setter = _add(tree, "GKNodeUiSet", "血条半透明", 260.0, -320.0,
                  gk_widget="血条", gk_set_alpha=True, gk_alpha=0.6)
    _link(tree, physics, "执行", setter, "执行")

    def open_panel(key_name, panel_name, y):
        key = _add(tree, "GKNodeKey", f"{key_name} 键", 520.0, y,
                   gk_key=key_name, gk_trigger="JUST")
        branch = _add(tree, "GKNodeBranch", f"按了 {key_name}?", 780.0, y)
        opener = _add(tree, "GKNodeUiHide", f"开{panel_name}", 1040.0, y,
                      gk_widget=panel_name, gk_visible=True, gk_raise=True)
        _link(tree, key, "结果", branch, "条件")
        _link(tree, physics, "执行", branch, "执行")
        _link(tree, branch, "是", opener, "执行")
        return opener

    open_panel("O", "设置面板", -640.0)
    open_panel("B", "背包面板", -900.0)
    # 第一百零五轮:模态框(挡住点击 + 点外面关)
    open_panel("M", "模态框", -1450.0)

    key_c = _add(tree, "GKNodeKey", "C 键", 520.0, -1160.0,
                 gk_key="C", gk_trigger="JUST")
    branch_c = _add(tree, "GKNodeBranch", "按了 C?", 780.0, -1160.0)
    close_settings = _add(tree, "GKNodeUiHide", "关设置面板", 1040.0, -1160.0,
                          gk_widget="设置面板", gk_visible=False)
    close_bag = _add(tree, "GKNodeUiHide", "关背包面板", 1300.0, -1160.0,
                     gk_widget="背包面板", gk_visible=False)
    _link(tree, key_c, "结果", branch_c, "条件")
    _link(tree, physics, "执行", branch_c, "执行")
    _link(tree, branch_c, "是", close_settings, "执行")
    _link(tree, close_settings, "完成", close_bag, "执行")


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
        set(bpy.data.materials) | set(bpy.data.images) | \
        set(bpy.data.curves) | set(bpy.data.node_groups) | \
        set(bpy.data.cameras) | set(bpy.data.lights)
    scenes = list(bpy.data.scenes)
    bpy.data.libraries.write(path, blocks | set(scenes), fake_user=True)


def _write_type_wav(path, seconds=0.05, freq=1200.0, rate=22050):
    """程序生成一个"嘀"的打字音(第六十四轮)。

    示例不该依赖"你机器上恰好有某个音频"(和 demo_assets 的立场一样)。
    0.05 秒 + 两头极短淡入淡出 —— 就是视觉小说里那种打字机"嗒"的一下。

    ⚠ 直接写进导出目录的 `audio/`,不走『资产_声音卡』:这一版只是给
      示例配一个打字音,没必要为它建一整棵资产图。插件清理只删**它自己
      写过**的文件(清单里那些),手工放进去的这个不会被动。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    count = max(1, int(seconds * rate))
    frames = bytearray()
    for index in range(count):
        t = index / rate
        fade_in = min(1.0, index / (rate * 0.005))
        fade_out = min(1.0, (count - index) / (rate * 0.02))
        value = int(16000 * fade_in * fade_out * math.sin(2.0 * math.pi * freq * t))
        frames += struct.pack("<h", value)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return path


def main():
    from BlenderGameKit import asset_export, project

    os.makedirs(OUT_DIR, exist_ok=True)
    scene, stage = build_scene()
    write_physics(scene, stage)
    build_graph(scene, stage)
    _apply_settings(scene, (("dimension", "3"),))

    directory = os.path.join(OUT_DIR, "界面示例_godot")
    project.create_project(directory, title="界面示例",
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

    # ── 界面的图必须真的落盘(不然 Godot 里 load 出来是 null)──────
    from BlenderGameKit import curve_assets

    logo_file = os.path.join(directory, "art",
                             curve_assets.image_file_name(LOGO_NAME))
    log(f"徽标 PNG: {logo_file} 存在={'是' if os.path.isfile(logo_file) else '否'}")

    # 打字机音效(第六十四轮):程序生成后直接放进导出目录的 audio/
    type_wav = _write_type_wav(os.path.join(directory, "audio",
                                            TYPE_SOUND_FILE))
    log(f"打字音: {os.path.relpath(type_wav, directory)} "
        f"存在={'是' if os.path.isfile(type_wav) else '否'}")

    save_blend(BLEND_PATH)
    log(f"已保存 {BLEND_PATH}")
    log(f"工程目录: {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())