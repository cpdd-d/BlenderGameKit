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

跑法(Godot):打开 `界面示例_godot` 直接 F5。
自动检查(不用 Blender,要 Godot):`python scripts\\check_ui_demo.py`
"""

import os
import sys

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
DIALOG_TEXT = ("欢迎!这是界面示例:图片、血条、对话框和暂停菜单都在这一屏。"
               "点一下继续。")


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


def build_graph(scene, stage):
    """图:开场把整套界面搭出来 -> 对话框;每物理帧改血条透明度。

    ⚠ 执行流是**一条链**走到头:面板 -> 底板上的控件 -> 暂停菜单 ->
    菜单里的控件 -> 挂 Esc 热键 -> 底部提示 -> 对话框 -> 改文字。
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
    tail = _build_ui_chain(logic, ready)
    _build_frame_chain(logic, physics)
    log(f"界面逻辑:共 {len(logic.nodes)} 个节点 / {len(logic.links)} 条连线"
        f"(界面链尾部: {tail.name})")
    return logic


def _build_ui_chain(tree, ready):
    """开场链:整套界面 + 对话框。返回链尾(便于探针/日志对照)。"""
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
    # ── 暂停菜单:底板(正中、开局藏)+ 三个控件挂在它下面 ──────────
    menu = _add(tree, "GKNodeUiPanel", "暂停菜单", 1040.0, 0.0,
                gk_width=440.0, gk_height=300.0, gk_radius=18,
                gk_align="CENTER", gk_start_hidden=True,
                gk_color=(0.05, 0.06, 0.09, 0.86))
    title = _add(tree, "GKNodeUiText", "暂停标题", 1300.0, 0.0,
                 gk_text="已暂停", gk_size=30, gk_align="TOP_CENTER",
                 gk_margin=26.0, gk_parent_widget="暂停菜单")
    resume = _add(tree, "GKNodeUiButton", "继续按钮", 1560.0, 0.0,
                  gk_text="继续游戏", gk_x=130.0, gk_y=104.0,
                  gk_width=180.0, gk_height=48.0,
                  gk_parent_widget="暂停菜单")
    quit_button = _add(tree, "GKNodeUiButton", "退出按钮", 1560.0, -260.0,
                       gk_text="退出游戏", gk_x=130.0, gk_y=176.0,
                       gk_width=180.0, gk_height=48.0,
                       gk_parent_widget="暂停菜单")
    hotkey = _add(tree, "GKNodeUiPauseGame", "暂停热键", 1840.0, 0.0,
                  gk_pause=False, gk_menu="暂停菜单", gk_hotkey=True)
    hint = _add(tree, "GKNodeUiText", "底部提示", 2100.0, 0.0,
                gk_text="点一下继续对话 / Esc 暂停", gk_size=20,
                gk_align="BOTTOM_CENTER", gk_margin=28.0)
    dialog = _add(tree, "GKNodeUiDialog", "开场白", 2360.0, 0.0,
                  gk_speaker="向导", gk_text=DIALOG_TEXT,
                  gk_speed=28.0, gk_width=640.0, gk_height=150.0,
                  gk_margin=56.0)
    after = _add(tree, "GKNodeUiSet", "改分数文字", 2640.0, 0.0,
                 gk_widget="分数", gk_set_text=True, gk_text="对话结束")

    _link(tree, ready, "执行", hud, "执行")
    _link(tree, hud, "完成", logo, "执行")
    _link(tree, logo, "完成", score, "执行")
    _link(tree, score, "完成", bar, "执行")
    _link(tree, bar, "完成", menu, "执行")
    _link(tree, menu, "完成", title, "执行")
    _link(tree, title, "完成", resume, "执行")
    _link(tree, resume, "完成", quit_button, "执行")
    _link(tree, quit_button, "完成", hotkey, "执行")
    _link(tree, hotkey, "完成", hint, "执行")
    _link(tree, hint, "完成", dialog, "执行")
    _link(tree, dialog, "完成", after, "执行")

    # 菜单里的按钮:先解除暂停,再回到游戏 / 退出
    back = _add(tree, "GKNodeUiPauseGame", "继续游戏", 1840.0, -260.0,
                gk_pause=False, gk_menu="暂停菜单", gk_hotkey=False)
    _link(tree, resume, "点击", back, "执行")
    quit_pause = _add(tree, "GKNodeUiPauseGame", "退出前解除暂停", 1840.0, -520.0,
                      gk_pause=False, gk_menu="暂停菜单", gk_hotkey=False)
    quit_game = _add(tree, "GKNodeQuitGame", "退出(菜单)", 2100.0, -520.0)
    _link(tree, quit_button, "点击", quit_pause, "执行")
    _link(tree, quit_pause, "完成", quit_game, "执行")
    log("界面链:面板 -> 图片 -> 文本 -> 进度条 -> 暂停菜单 -> 底部提示 -> 对话框")
    return after


def _build_frame_chain(tree, physics):
    """每物理帧:把血条调成半透明(『界面_设置』的现场)。

    ── 为什么留一个"每帧都设一遍"的节点在图上 ──────────────────────
    探针要断言的是"运行时改控件真的生效"(血条的 modulate.a = 0.6);
    一次性设置也行,但每帧刷一遍才是真实用法(血条/图标会随状态变化)。
    顺带还能看出"暂停之后它停了" —— 暂停时血条透明度不会再被刷新。
    """
    setter = _add(tree, "GKNodeUiSet", "血条半透明", 260.0, -320.0,
                  gk_widget="血条", gk_set_alpha=True, gk_alpha=0.6)
    _link(tree, physics, "执行", setter, "执行")


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

    save_blend(BLEND_PATH)
    log(f"已保存 {BLEND_PATH}")
    log(f"工程目录: {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())