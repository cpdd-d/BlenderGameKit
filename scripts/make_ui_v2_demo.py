"""界面新功能示例(第一百二十七 / 一百二十八轮验收屏)。

用法:
    blender -b --python scripts\\make_ui_v2_demo.py

产出:
    examples/demo_ui_v2/界面新功能示例.blend
    examples/demo_ui_v2/界面新功能示例_godot/    (可用 Godot 直接打开,按 F5)

── 这一屏在看什么 ────────────────────────────────────────────────
一张**只用新节点搭出来的界面**,每个新功能都至少用上一次 —— 目的是把
"设计上的缺陷 / 生成上的错误"在这一屏上暴露出来:

| 用到的 | 在游戏里怎么看 |
|---|---|
| **界面_画布** | 整屏的根;子控件都挂在它下面(『竖排框』用**连线**挂) |
| **连线式父子** | 标题条 / 开始按钮 / 得分 都靠『父』引脚连到父控件 —— 卡片上的『父控件』名字全留空 |
| **界面_限定框** | 『竖排框』(竖排):标题条 + 开始按钮 + 得分自动从上往下排,不用填 X / Y |
| **界面_文本限定框** | 『标题条』:框 + 里面一行字(有底色、圆角、内边距) |
| **界面_背景** | 『黑幕』:整屏压暗的那层 |
| **界面_弹入/弹出** | 按 **P** 弹入(从左滑进来 + 淡入 + 黑幕一起亮 + 置顶),按 **O** 弹出去(自动复位 + 藏起来) |
| **界面_按键按下** | P / O 这两个热键(捕获自定键,导出时写进 Godot 输入映射) |
| **界面_鼠标点击** | 左键点『开始按钮』-> 分数 +10;**右键点屏幕任意位置** -> 分数归零 |
| **界面_取控件** | 『得分』的父**不是**填名字,而是拿『界面_取控件』取到的『竖排框』引用当父 |
| **界面_取界面** | 玩法图里把整张界面取出来,记进变量 |
| **变量_设置 / 变量_读取** | 界面图里也能用了:『分数』这个变量就写在界面图里 |
| **取值_物体名称** | 『名字牌』显示的是场景里『舞台』这个物体的**节点名** |

自动检查(不用 Blender,要 Godot):`python scripts\\check_ui_v2_demo.py`
"""

import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "examples", "demo_ui_v2")
BLEND_PATH = os.path.join(OUT_DIR, "界面新功能示例.blend")
STAGE = "舞台"
DESIGN_NAME = "设计_新界面"


def log(message):
    print(f"[界面新功能] {message}")


def clear_scene():
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for group in list(bpy.data.node_groups):
        if getattr(group, "bl_idname", "").startswith("GK"):
            bpy.data.node_groups.remove(group)
    scene.name = "界面新功能示例"
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


def build_scene():
    scene = clear_scene()
    # ⚠ 必须有不止一个网格:整场景只有角色一个网格时插件不会生成主场景 .tscn
    make_box("背景板", (16.0, 9.0, 0.2), (0.0, 0.0, -1.0),
             (0.14, 0.16, 0.22, 1.0))
    stage = make_box(STAGE, (2.0, 2.0, 0.2), (0.0, 0.0, 0.0),
                     (0.20, 0.24, 0.32, 1.0))
    log(f"场景:背景板 + {STAGE}(挂脚本的载体)")
    return scene, stage


def write_physics(scene, stage):
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
    """整张界面设计图:每个新节点都用上一次。

    ── 注意"开局藏起来"这件事 ──────────────────────────────────────
    『界面_画布 / 限定框 / 背景 / 文本限定框』这四件**都没有『开局藏』选项**
    (只有『界面_面板』有)。所以"开局藏起来、按 P 弹入"这个最典型的用法,
    必须额外挂两个『界面_隐藏控件』节点(见下面『藏黑幕』『藏面板』)——
    这一条本身就是本屏要暴露的设计缺陷之一。
    """
    design = bpy.data.node_groups.new(DESIGN_NAME, "GKUiDesignTree")

    # ① 画布(整屏的根)
    canvas = _add(design, "GKNodeUiCanvas", "主画布", 0.0, 0.0, gk_full=True)

    # ② 黑幕(整屏压暗)—— 『开局藏』(第一百二十八轮给结构件补上的)
    scrim = _add(design, "GKNodeUiBackdrop", "黑幕", 320.0, 0.0,
                 gk_full=True, gk_color=(0.02, 0.02, 0.04, 0.92),
                 gk_start_hidden=True)

    # ③ 竖排限定框(挂画布下,用**连线**;同样开局藏)
    box = _add(design, "GKNodeUiBox", "竖排框", 640.0, 0.0,
               gk_mode="VBOX", gk_separation=12, gk_pad=16,
               gk_x=60.0, gk_y=60.0, gk_width=340.0, gk_height=260.0,
               gk_start_hidden=True)
    _link(design, canvas, "自己", box, "父")

    # ④ 文本限定框(标题条)—— 挂竖排框
    title = _add(design, "GKNodeUiTextBox", "标题条", 960.0, 0.0,
                 gk_text="界面新功能验收", gk_size=26, gk_pad=12,
                 gk_bg=(0.10, 0.20, 0.16, 0.85))
    _link(design, box, "自己", title, "父")

    # ⑤ 按钮 —— 挂竖排框
    start = _add(design, "GKNodeUiButton", "开始按钮", 1280.0, 0.0,
                 gk_text="开始")
    _link(design, box, "自己", start, "父")

    # ⑥ 得分文本 —— 父用『界面_取控件』的**引用**(不填名字)
    ref = _add(design, "GKNodeUiWidgetRef", "取竖排框", 960.0, -320.0,
               gk_widget="竖排框")
    score = _add(design, "GKNodeUiText", "得分", 1280.0, -320.0,
                 gk_text="分数 0", gk_size=20)
    _link(design, ref, "控件", score, "父")

    # ⑦ 名字牌 —— 文字来自『取值_物体名称』(取场景里『舞台』的节点名)
    object_name = _add(design, "GKNodeObjectName", "取舞台名", 960.0, -640.0,
                       gk_target=STAGE)
    nameplate = _add(design, "GKNodeUiText", "名字牌", 1280.0, -640.0,
                     gk_size=18)
    _link(design, box, "自己", nameplate, "父")
    _link(design, object_name, "名称", nameplate, "文字")

    # ⑧ 变量(第一百二十七轮起界面图里也能用):分数
    set_score = _add(design, "GKNodeSetVar", "记分数", 1600.0, -960.0,
                     gk_var_name="分数", gk_value=0.0)

    # ⑨ 变量_读取:把『分数』读出来,给下面几处『界面_设置』当文字用
    #    (第一百二十七轮起『变量_读取』也能摆在界面图里了)
    #
    #    ⚠ 这里的『刷新得分』**不吃卡片名字**,而是把『界面_取控件』取到的
    #      『得分』引用接到它的『控件』引脚上 —— 验的是第一百二十八轮补的
    #      "吃引用"那条路(以前引用取出来没人吃)。
    read_score = _add(design, "GKNodeGetVar", "读分数", 1600.0, -640.0,
                      gk_var_name="分数")
    score_ref = _add(design, "GKNodeUiWidgetRef", "取得分", 1280.0, -960.0,
                     gk_widget="得分")
    refresh_home = _add(design, "GKNodeUiSet", "刷新得分(开局)", 1920.0, -640.0,
                        gk_set_text=True)
    _link(design, read_score, "值", refresh_home, "文字")
    _link(design, score_ref, "控件", refresh_home, "控件")
    _link(design, set_score, "完成", refresh_home, "执行")

    # ⑩ 事件:左键点『开始按钮』-> 分数 = 10 -> 刷新得分(只看那个控件)
    click_button = _add(design, "GKNodeUiOnMouseClick", "左键点开始按钮",
                        0.0, -1280.0, gk_button="LEFT", gk_widget="开始按钮")
    bump = _add(design, "GKNodeSetVar", "分数加十分", 320.0, -1280.0,
                gk_var_name="分数", gk_value=10.0)
    refresh_button = _add(design, "GKNodeUiSet", "刷新得分(按钮)",
                          640.0, -1280.0, gk_widget="得分", gk_set_text=True)
    _link(design, click_button, "点了", bump, "执行")
    _link(design, bump, "完成", refresh_button, "执行")
    _link(design, read_score, "值", refresh_button, "文字")

    # ⑪ 事件:右键点屏幕**任意位置** -> 分数归零 -> 刷新得分
    click_any = _add(design, "GKNodeUiOnMouseClick", "右键点空白", 0.0, -960.0,
                     gk_button="RIGHT", gk_widget="")
    zero = _add(design, "GKNodeSetVar", "分数归零", 320.0, -960.0,
                gk_var_name="分数", gk_value=0.0)
    refresh_zero = _add(design, "GKNodeUiSet", "刷新得分(归零)",
                        640.0, -960.0, gk_widget="得分", gk_set_text=True)
    _link(design, click_any, "点了", zero, "执行")
    _link(design, zero, "完成", refresh_zero, "执行")
    _link(design, read_score, "值", refresh_zero, "文字")

    # ⑪ 热键:P 弹入 / O 弹出(捕获自定键)
    key_open = _add(design, "GKNodeUiOnKeyPress", "开面板键", 0.0, -1600.0,
                    gk_key="P")
    key_close = _add(design, "GKNodeUiOnKeyPress", "收面板键", 320.0, -1600.0,
                     gk_key="O")

    # ⑫ 弹入 / 弹出(带黑幕 + 置顶)
    pop_in = _add(design, "GKNodeUiPop", "弹面板", 640.0, -1600.0,
                  gk_widget="竖排框", gk_mode="IN", gk_direction="LEFT",
                  gk_time=0.35, gk_trans="TRANS_CUBIC", gk_ease="EASE_OUT",
                  gk_fade=True, gk_top=True, gk_scrim="黑幕", gk_scrim_to=0.6,
                  gk_wait=True)
    pop_out = _add(design, "GKNodeUiPop", "收面板", 960.0, -1600.0,
                   gk_widget="竖排框", gk_mode="OUT", gk_direction="LEFT",
                   gk_time=0.3, gk_fade=True, gk_scrim="黑幕", gk_wait=True)
    _link(design, key_open, "按了", pop_in, "执行")
    _link(design, key_close, "按了", pop_out, "执行")

    log(f"设计图『{design.name}』:{len(design.nodes)} 个节点 / "
        f"{len(design.links)} 条连线")
    # 局部变量只是"摆着好看"没意义,这里统一提一句(防止被当成没用上)
    _ = (start, title, scrim, nameplate)
    return design


def build_graph(scene, stage, design):
    """玩法图:套用设计 + 用『界面_取界面』把整张界面取出来记进变量。"""
    from BlenderGameKit import presets

    logic, _anim, notes = presets.create_character_preset(
        scene, character=stage,
        logic_name="界面逻辑",
        class_name="UiV2Player",
        with_animation=False,
        with_godot_camera=False,
    )
    for note in notes:
        log(f"预设: {note}")
    ready = None
    for node in logic.nodes:
        if node.bl_idname == "GKNodeOnReady":
            ready = node
            break
    if ready is None:
        raise RuntimeError("角色预设里没有『准备』节点")

    apply_design = _add(logic, "GKNodeUiApplyDesign", "套用新界面", 0.0, -320.0,
                        gk_design=design.name)
    _link(logic, ready, "执行", apply_design, "执行")

    design_ref = _add(logic, "GKNodeUiDesignRef", "取整屏界面", 320.0, -320.0)
    remember = _add(logic, "GKNodeSetVar", "记界面", 640.0, -320.0,
                    gk_var_name="当前界面", gk_value=0.0)
    _link(logic, apply_design, "完成", remember, "执行")
    _link(logic, design_ref, "界面", remember, "值")

    log(f"界面逻辑:共 {len(logic.nodes)} 个节点 / {len(logic.links)} 条连线")
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
    design = build_design_tree()
    build_graph(scene, stage, design)
    _apply_settings(scene, (("dimension", "3"),))

    directory = os.path.join(OUT_DIR, "界面新功能示例_godot")
    project.create_project(directory, title="界面新功能示例",
                           blend_file_name=os.path.basename(BLEND_PATH))
    result = asset_export.export_all_scenes(directory, dimension="3")
    if result.get("error"):
        log(f"导出失败:{result['error']}")
        return 1
    for entry in result.get("scenes", []):
        log(f"场景 {entry['scene']} -> {entry['scene_name']}.tscn"
            f"(脚本 {entry['script_name']})")
    # ── 导出告警是"设计缺陷"的第一手信号 ──────────────────────────
    warnings = result.get("warnings", []) or []
    if warnings:
        log(f"----- 导出告警 {len(warnings)} 条 -----")
        for warning in warnings:
            log(f"告警: {warning}")
    else:
        log("导出告警: 0 条")
    if result.get("deleted"):
        log(f"清理旧文件: {result['deleted']}")

    save_blend(BLEND_PATH)
    log(f"已保存 {BLEND_PATH}")
    log(f"工程目录: {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
