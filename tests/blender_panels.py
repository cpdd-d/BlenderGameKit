"""面板不能崩。

为什么专门测这个:Blender 对面板 `draw()` 里抛的异常是**静默**的 ——
界面只是少一块,控制台刷一行 traceback,用户看到的是"按钮不见了"。

实测踩过:插入新面板时误删了 `_draw_body_type` 函数,
`GK_PT_ObjectPhysics.draw` 抛 NameError,整个『游戏物理』面板消失,
碰撞体可视化和描边预览的按钮全都不见了 —— 而当时 20 多层测试全绿,
因为它们只测"文件生成了没",没有一个真的去调 draw()。

用法:
    blender -b --python tests/blender_panels.py
"""

import sys
import os
import math
import time
import traceback
from importlib import import_module

import addon_utils
import bmesh
import bpy

RESULTS = []
# 造过的每一个假 layout(第六十三轮)。第 7 节拿它们统一验一遍
# "画出来的按钮都是真算子" —— 自动覆盖**所有** draw 调用,不用逐个去记。
LAYOUTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" - {detail}" if detail and not condition else ""))
    return bool(condition)


def find_addon():
    for module in addon_utils.modules():
        if "blender_game_kit" in getattr(module, "__name__", ""):
            return module.__name__
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isfile(os.path.join(root, "BlenderGameKit", "__init__.py")):
        sys.path.insert(0, root)
        return "BlenderGameKit"
    return None


class RecordingLayout:
    """假的 layout,记录所有被调用的方法。

    真正的 UILayout 需要一个 3D 视口/窗口上下文,后台模式下造不出来。
    这个替身唯一的作用就是"让 draw() 跑完,并把异常暴露出来" ——
    而异常正是我们要抓的东西。

    ⚠ 必须返回**同一个**对象,不能每次 __getattr__ 都新建。
    踩过:每次访问都返回新实例,记录就丢了 —— 测试报告"operator 调用了 0 次",
    看起来像按钮没画出来,其实只是替身写错了。
    """

    def __init__(self):
        self.calls = []
        self.operator_names = []      # 画出来的按钮 idname(第一段参数)
        self.texts = []               # 画出来的文字(给"盒子有没有反映状态"用)
        LAYOUTS.append(self)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def recorder(*args, **kwargs):
            self.calls.append(name)
            text = kwargs.get("text")
            if text:
                self.texts.append(str(text))
            elif args and isinstance(args[0], str) and name != "operator":
                self.texts.append(args[0])
            if name == "operator" and args:
                self.operator_names.append(str(args[0]))
            return self
        return recorder


class FakeContext:
    """给 draw() 用的最小上下文。"""

    def __init__(self, obj, scene):
        self.object = obj
        self.scene = scene
        self.selected_objects = [obj] if obj else []
        self.view_layer = bpy.context.view_layer
        self.window_manager = bpy.context.window_manager
        self.preferences = bpy.context.preferences
        self.mode = "OBJECT"
        self.active_object = obj

    def __getattr__(self, name):
        return getattr(bpy.context, name)


class FakeNodeContext(FakeContext):
    """带 space_data 的上下文 —— 给"按当前树画"的面板用。

    『游戏显示』面板(第四十二轮)按 `space_data.tree_type` / `edit_tree`
    取当前树;后台模式没有真节点编辑器,所以这里造一个最小替身。
    """

    def __init__(self, obj, scene, tree):
        super().__init__(obj, scene)
        self.space_data = type("S", (), {
            "tree_type": getattr(tree, "bl_idname", ""),
            "edit_tree": tree,
            "node_tree": tree,
        })()


def main():
    module_name = find_addon()
    if not check("找到插件", module_name, "未安装"):
        return 1
    if module_name == "BlenderGameKit":
        import_module(module_name).register()
    else:
        addon_utils.enable(module_name, default_set=True, persistent=True)
    ui = import_module(f"{module_name}.ui")
    for key in ("more_tools", "project", "tree_list", "logic_ports",
                "variables", "functions", "groups", "preset_box",
                "script_carrier", "scene_assets", "world",
                # 第 122 轮:两块导出盒子并成一块『导出到 Godot』,
                # 侧栏的『打包设置』也搬进了原生菜单 —— 键跟着换。
                "export_godot", "export_report",
                "advisor", "vargroup:", "varrefs:"):
        ui._OPEN_SECTIONS[key] = True

    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)

    # 造一个带材质的网格,让物体面板能画
    mesh = bpy.data.meshes.new("CubeMesh")
    cube = bpy.data.objects.new("Cube", mesh)
    scene.collection.objects.link(cube)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(mesh)
    bm.free()
    material = bpy.data.materials.new("TestMat")
    mesh.materials.append(material)

    # ---- 1. draw 引用的辅助函数必须都存在 ----
    print("=" * 72)
    print("1. 面板用到的模块级函数")
    print("=" * 72)
    # 这些是 draw() 里直接调用的模块级函数。少一个,面板就整块消失。
    needed = ("_draw_body_type", "auto_shape_for",
              "_recent_classes", "SHAPE_LABELS")
    missing = [name for name in needed if not hasattr(ui, name)]
    print(f"    检查了 {len(needed)} 个: {', '.join(needed)}")
    check("面板依赖的辅助函数/常量都在", not missing, str(missing))

    # godot_look 的 describe() 是「Godot 观感」面板直接调的 ——
    # 它要是没了,面板会静默消失(Blender 对 draw 里的异常不吭声)。
    look = import_module(f"{module_name}.godot_look")
    check("godot_look.describe() 存在", callable(getattr(look, "describe", None)))
    check("describe() 返回非空", bool(look.describe()), str(look.describe()))

    # ---- 2. 每个面板的 draw 都要能跑完 ----
    print("=" * 72)
    print("2. 面板 draw() 不能抛异常")
    print("=" * 72)
    cases = [
        ("GK_PT_Main", cube),
        ("GK_PT_ObjectPhysics", cube),
        # 视口侧栏的「Godot 观感」面板 —— 它读 context.scene,
        # 而且会去调 godot_look 的 describe(),少一个函数就整块消失。
        ("GK_PT_GodotLook", cube),
        # 『游戏显示』设置图的侧栏面板(第四十二轮)—— 没有节点编辑器上下文时
        # 它应该走"没有显示设置图"的早退分支,同样不许抛异常。
        ("GK_PT_DisplayTree", cube),
        # 『游戏资产』资产库的侧栏面板(第四十三轮)同理。
        ("GK_PT_AssetTree", cube),
        # 『游戏音乐』设置图的侧栏面板(第八十六轮)同理。
        ("GK_PT_MusicTree", cube),
    ]
    for class_name, target in cases:
        panel = getattr(ui, class_name, None)
        if panel is None:
            check(f"{class_name} 存在", False, "类找不到")
            continue
        layout = RecordingLayout()
        holder = type("P", (), {"layout": layout})()
        context = FakeContext(target, scene)
        try:
            panel.draw(holder, context)
            label = (f"{class_name}.draw() 跑通"
                     f"（调用了 {len(set(layout.calls))} 种方法）")
            check(label, True)
            print(f"    {class_name}: {sorted(set(layout.calls))}")
        except Exception as exc:
            check(f"{class_name}.draw() 跑通", False,
                  f"{type(exc).__name__}: {exc}")
            traceback.print_exc()

    # ---- 2b. 带**导出报告**时面板也要画得出来(第二十三轮)----
    # 报告盒子是这次新加的:它画消息 / "怎么修" / 定位按钮,
    # 还自己折行(Blender 的 label 不换行)。空报告那条路上面已经跑过了,
    # 这里塞一份**有错误 + 长消息 + 节点名**的报告再画一遍。
    print("=" * 72)
    print("2b. 有导出报告时的面板 draw()")
    print("=" * 72)
    try:
        # 插件在 Blender 里是 bl_ext.<仓库>.<包名>,直接 import 会 ModuleNotFoundError
        export_report = import_module(f"{module_name}.export_report")
        models = import_module(f"{module_name}.models")
        export_report.record(scene, {
            "ok": False, "error": "写 .tscn 失败: 权限不足(这是一个很长的" 
                                  "错误消息,用来验证面板里的手动折行)",
            "stage": "写场景", "script_name": "game_logic.gd",
            "directory": "D:/proj", "lines": 120, "elapsed_ms": 35.5,
            "written": ["a"],
            "diagnostics": [models.Diagnostic(
                level="warning", code="ANIM_TARGET_MISMATCH",
                message="动画『走路』不在『身体』上 —— 这个模型里没有这个动作",
                hint="检查节点上的『动画播放器』是否指向了带这个动画的物体",
                node_name="动画_走路")],
            "warnings": ["这些网格没有材质: 地面"],
            "notes": ["参考: 无"],
        })
        panel = getattr(ui, "GK_PT_Main", None)
        layout = RecordingLayout()
        holder = type("P", (), {"layout": layout})()
        panel.draw(holder, FakeContext(cube, scene))
        check("有报告时 GK_PT_Main.draw() 跑通", True)
        # 复制 / 定位两个按钮都要画出来
        names = [call for call in layout.operator_names]
        check("报告盒子画出了『复制报告』和『定位』按钮",
              "gk.export_report_copy" in names
              and "gk.export_report_focus" in names,
              str(sorted(set(names))))

        # ── 资产清单(第八十九轮):摘要里要报"资产 N 份" ────────────────
        # 清单正文在报告文本里(冒烟会验),侧栏这一行只有面板看得见。
        export_report.note_assets(scene, "D:/proj", ["D:/proj/art/World.glb"])
        layout = RecordingLayout()
        holder = type("P", (), {"layout": layout})()
        panel.draw(holder, FakeContext(cube, scene))
        texts = " ".join(str(item) for item in layout.texts)
        check("报告摘要报出了资产份数", "资产 1 份" in texts, texts[:200])
        export_report.clear()
    except Exception as exc:
        check("有报告时 GK_PT_Main.draw() 跑通", False,
              f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 3. 面板里的按钮必须画出来 ----
    #
    # 只"不抛异常"不够 —— 如果 draw 在半路 return 了,也不抛异常,
    # 但按钮同样不会出现。所以要确认 operator() 真的被调过。
    print("=" * 72)
    print("3. 关键按钮真的画出来了吗")
    print("=" * 72)
    for class_name, target, wanted in (
            # 碰撞线框那几个按钮第四十二轮搬到『游戏显示』图里了,
            # 物体面板现在常驻的是"再加一份实例"(静态资产)。
            ("GK_PT_ObjectPhysics", cube, ("gk.add_asset_instance",)),
            ("GK_PT_Main", cube, ("gk.open_godot", "gk.export_godot")),
            # 观感面板要有开关和还原两个按钮
            ("GK_PT_GodotLook", cube,
             ("gk.match_godot_look", "gk.restore_look", "gk.open_glossary")),
    ):
        panel = getattr(ui, class_name, None)
        if panel is None:
            continue
        layout = RecordingLayout()
        holder = type("P", (), {"layout": layout})()
        try:
            panel.draw(holder, FakeContext(target, scene))
        except Exception as exc:
            check(f"{class_name} 按钮", False, f"draw 抛异常: {exc}")
            continue
        button_count = layout.calls.count("operator")
        print(f"    {class_name} 调用了 operator {button_count} 次")
        # 每个面板都该有按钮 —— 用户的抱怨就是"按钮不见了"。
        # 只测"不抛异常"不够:draw 半路 return 也不抛异常,但按钮同样没有。
        check(f"{class_name} 画出了按钮", button_count >= len(wanted),
              f"operator 调用次数={button_count},期望至少 {len(wanted)}")
        # ── 第一百三十九轮:预设盒删了(用户:"预设:全删了,计划在右键
        #    菜单中添加")—— 这里反过来钉"侧栏里没有它",免得哪天又长回来。
        if class_name == "GK_PT_Main":
            check("『预设』按钮不在侧栏了(第一百三十九轮删的)",
                  "gk.character_preset" not in layout.operator_names,
                  str(sorted(set(layout.operator_names))))

    # ---- 4. 『游戏显示』设置图的面板(第四十二轮)----
    # 带真树时才画按钮:缺卡 -> 『补齐』;补齐后 -> 『回到游戏逻辑』。
    # 顺便把"卡片的 draw_buttons 也要能画完"一起验了 —— 设置卡就是这张图的
    # 全部内容,卡片画一半报错的话(第三十九轮那种),用户看到的是"没作用"。
    print("=" * 72)
    print("4. 『游戏显示』面板 + 四张设置卡")
    print("=" * 72)
    try:
        tree = bpy.data.node_groups.new("面板冒烟显示42", "GKDisplayTree")
        tree.nodes.new("GKNodeDisplayCollision")
        panel = getattr(ui, "GK_PT_DisplayTree", None)
        if panel is None:
            check("GK_PT_DisplayTree 存在", False, "类找不到")
        else:
            layout = RecordingLayout()
            holder = type("P", (), {"layout": layout})()
            panel.draw(holder, FakeNodeContext(cube, scene, tree))
            names = list(layout.operator_names)
            check("『游戏显示』面板缺卡时画出『补齐』按钮",
                  "gk.display_fill_cards" in names, str(sorted(set(names))))

            ui._fill_display_cards(tree)
            layout = RecordingLayout()
            holder = type("P", (), {"layout": layout})()
            panel.draw(holder, FakeNodeContext(cube, scene, tree))
            names = list(layout.operator_names)
            check("『游戏显示』面板补齐后不再提示,回到游戏逻辑一直在",
                  "gk.display_fill_cards" not in names
                  and "gk.switch_tree" in names, str(sorted(set(names))))

        # 四张卡的 draw_buttons 都要能画完(画一半崩了 = 卡片"没作用")
        card_error = ""
        for card in tree.nodes:
            try:
                card.draw_buttons(bpy.context, RecordingLayout())
            except Exception as exc:                      # noqa: BLE001
                card_error = (f"{getattr(card, 'bl_label', '?')}: "
                              f"{type(exc).__name__}: {exc}")
                break
        check("四张设置卡的 draw_buttons 都能画完", not card_error, card_error)
        bpy.data.node_groups.remove(tree)
    except Exception as exc:
        check("『游戏显示』面板", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 4b. 『游戏音乐』设置图的面板 + 两张卡(第八十六轮)----
    # 和显示设置图同一套:缺卡 -> 『补齐』;补齐后 -> 『回到游戏逻辑』。
    print("=" * 72)
    print("4b. 『游戏音乐』面板 + 两张设置卡")
    print("=" * 72)
    try:
        music = bpy.data.node_groups.new("面板冒烟音乐86", "GKMusicTree")
        music.nodes.new("GKNodeMusicSceneBgm")
        panel = getattr(ui, "GK_PT_MusicTree", None)
        if panel is None:
            check("GK_PT_MusicTree 存在", False, "类找不到")
        else:
            layout = RecordingLayout()
            holder = type("P", (), {"layout": layout})()
            panel.draw(holder, FakeNodeContext(cube, scene, music))
            names = list(layout.operator_names)
            check("『游戏音乐』面板缺卡时画出『补齐』按钮",
                  "gk.music_fill_cards" in names, str(sorted(set(names))))

            ui._fill_music_cards(music)
            layout = RecordingLayout()
            holder = type("P", (), {"layout": layout})()
            panel.draw(holder, FakeNodeContext(cube, scene, music))
            names = list(layout.operator_names)
            check("『游戏音乐』面板补齐后回到游戏逻辑一直在",
                  "gk.music_fill_cards" not in names
                  and "gk.switch_tree" in names, str(sorted(set(names))))
            # 摘要行真的画出来了(两行:开局 BGM / 总线音量)
            texts = " ".join(layout.texts)
            check("『游戏音乐』面板画出设置摘要",
                  "开局 BGM" in texts and "总线音量" in texts, texts[:200])

        # 两张卡的 draw_buttons 都要能画完
        card_error = ""
        for card in music.nodes:
            try:
                card.draw_buttons(bpy.context, RecordingLayout())
            except Exception as exc:                      # noqa: BLE001
                card_error = (f"{getattr(card, 'bl_label', '?')}: "
                              f"{type(exc).__name__}: {exc}")
                break
        check("两张音乐设置卡的 draw_buttons 都能画完", not card_error,
              card_error)
    except Exception as exc:                              # noqa: BLE001
        check("『游戏音乐』面板", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        bpy.data.node_groups.remove(music)

    # ---- 4c. 『游戏环境与后期』侧栏:关卡打勾 / 灯光倍率 / 一键贴近(第 137 轮)----
    print("=" * 72)
    print("4c. 『游戏环境与后期』侧栏(关卡打勾 + 灯光倍率 + 一键贴近运行)")
    print("=" * 72)
    panel = getattr(ui, "GK_PT_EnvLibrary", None)
    env_tree = bpy.data.node_groups.new("面板冒烟环境137", "GKEnvLibraryTree")
    try:
        if panel is None:
            check("GK_PT_EnvLibrary 存在", False, "类找不到")
        else:
            layout = RecordingLayout()
            holder = type("P", (), {"layout": layout})()
            panel.draw(holder, FakeNodeContext(cube, scene, env_tree))
            names = sorted(set(layout.operator_names))
            texts = " ".join(layout.texts)
            # 用户第 ①③④ 条要的三样:关卡打勾、灯光倍率(搬家)、一键贴近
            check("侧栏画出『要一起改的关卡(打勾)』",
                  "要一起改的关卡(打勾)" in texts, texts[:300])
            check("侧栏画出全选 / 全不选",
                  "gk.look_pick_all" in names and "gk.look_pick_none" in names,
                  str(names))
            check("侧栏画出『套给勾选的关卡』",
                  "gk.env_apply_to_checked" in names, str(names))
            check("侧栏画出『一键贴近运行画面』",
                  "gk.look_match_runtime" in names, str(names))
            check("灯光倍率搬到这个侧栏了(倍率滑块在)",
                  "倍率" in texts, texts[:300])
            # 第一百三十八轮:快速调色 / 曝光组(用户第 ④ 条)
            check("侧栏画出『快速调色 / 曝光』盒",
                  "快速调色" in texts, texts[:400])
            check("快速调色里有 色调映射 / 曝光 / 雾 / 泛光",
                  all(word in texts for word in ("曝光", "雾", "泛光")),
                  texts[:400])
            settings = getattr(scene, "gk_settings", None)
            check("场景设置里有 look_target(打勾用的字段)",
                  settings is not None and hasattr(settings, "look_target"), "")
            # 第一百三十八轮:逐灯『预览补偿』滑块(给灯光盒用)
            lamp = bpy.data.objects.new(
                "面板冒烟灯138", bpy.data.lights.new("面板冒烟灯数据138", "POINT"))
            scene.collection.objects.link(lamp)
            try:
                layout2 = RecordingLayout()
                holder2 = type("P", (), {"layout": layout2})()
                panel.draw(holder2, FakeNodeContext(cube, scene, env_tree))
                check("灯光盒里画出了『预览』补偿那一列",
                      "预览" in layout2.texts, str(layout2.texts)[:400])
                check("每盏灯都有 preview_scale 字段",
                      hasattr(lamp.gk_light, "preview_scale"), "")
            finally:
                bpy.data.objects.remove(lamp, do_unlink=True)
            # 新算子都注册上了
            for name in ("look_pick_all", "look_pick_none",
                         "env_apply_to_checked", "look_match_runtime",
                         "light_preview_scale_reset",
                         # 第一百五十一轮第四轮:右键工具 + 天空预览开关
                         "env_fill_cards", "env_auto_chain", "env_tidy",
                         "env_unlink_selected", "env_preview_toggle",
                         "env_preview_drag"):
                check(f"算子 gk.{name} 注册上了",
                      hasattr(bpy.ops.gk, name), "")
            # ── 第一百五十一轮第四轮:右键工具真的能补齐 / 连线 / 整理 ──
            # 这三个算子在侧栏不画按钮(右键菜单里),所以在这里直接执行 ——
            # 它们改的是树本身,不依赖窗口。
            tools = bpy.data.node_groups.new("面板冒烟环境工具151",
                                             "GKEnvLibraryTree")
            try:
                tool_ctx = FakeNodeContext(cube, scene, tools)
                # ⚠ 不能直接 `ui.GK_OT_EnvFillCards()` —— bpy 的算子类
                #   只能由 Blender 自己 new(bpy_struct.__new__ 需要参数)。
                #   这里用假 self 调**未绑定**的 execute,只验函数体。
                class FakeOperator:
                    def report(self, level, text):
                        pass

                def run(operator_class):
                    return operator_class.execute(FakeOperator(), tool_ctx)

                run(ui.GK_OT_EnvFillCards)
                kinds = {node.bl_idname for node in tools.nodes}
                check("补齐标准环境卡:9 张都补上了",
                      all(kind in kinds for kind, _label
                          in ui._ENV_CARD_ORDER),
                      str(sorted(kinds)))
                run(ui.GK_OT_EnvAutoChain)
                check("自动连线:串起 8 条执行线",
                      len(tools.links) == 8, str(len(tools.links)))
                # 断线:只选中第一张卡 -> 只删它那一条出线
                for item in tools.nodes:
                    item.select = False
                first = next(item for item in tools.nodes
                             if item.bl_idname == ui._ENV_CARD_ORDER[0][0])
                first.select = True
                run(ui.GK_OT_EnvUnlinkSelected)
                check("断开选中节点的连线:只删它自己的线",
                      len(tools.links) == 7, str(len(tools.links)))
                first.select = False
                run(ui.GK_OT_EnvTidy)
                columns = {round(node.location.x, 1) for node in tools.nodes}
                check("整理成一列:横坐标一致", len(columns) == 1,
                      str(sorted(columns)))
                values = ui._env_preview_values(tools)
                check("天空预览读得到太阳 / 月亮 / 云 / 天空",
                      "moon_on" in values and "sun_on" in values
                      and "top" in values
                      and "cloud_amount" in values,
                      str(sorted(values))[:200])
                # 第一百五十一轮第四轮:星星 / 角半径月盘 / 不在环境图里也能取到树
                check("天空预览读得到星星参数",
                      "star_amount" in values
                      and "star_brightness" in values, str(sorted(values)))
                check("天空预览也读『环境_设置色调』",
                      values["grade_on"]
                      and "grade_tint_amount" in values, str(sorted(values)))
                check("月盘默认按角半径 0.06 读",
                      abs(values["moon_size"] - 0.06) < 1e-6,
                      str(values["moon_size"]))
                check("没在编辑环境图时也能找到预览树",
                      ui._env_preview_tree() is not None, "")
                cover = ui._env_preview_cloud_cover(
                    (0.0, 0.5, -0.866), values, 0.0)
                check("预览云量算出来是 0~1 的数",
                      0.0 <= cover <= 1.0, str(cover))
                yaw, pitch = ui._env_preview_camera(values)
                if (values.get("sun_on")
                        and values.get("day_amount", 1.0) > 0.45
                        and values.get("sun_elevation", -90.0) > 2.0):
                    target_az = values["sun_azimuth"]
                    target_el = max(-12.0, min(78.0, values["sun_elevation"]))
                    target_name = "太阳"
                elif (values.get("moon_on")
                      and values.get("moon_elevation", -90.0) > 2.0):
                    target_az = values["moon_azimuth"]
                    target_el = max(-12.0, min(78.0, values["moon_elevation"]))
                    target_name = "月亮"
                else:
                    target_az, target_el, target_name = 0.0, 20.0, "地平线"
                check("预览默认朝向太阳 / 月亮 / 地平线",
                      abs(math.degrees(yaw) - target_az) < 1e-6
                      and abs(math.degrees(pitch) - target_el) < 1e-6,
                      "%s %.2f / %.2f" % (target_name, math.degrees(yaw),
                                          math.degrees(pitch)))
                # 第一百五十一轮第七轮:白天预览(拨时刻 + 播一天)
                check("预览时刻 / 播一天算子已注册",
                      hasattr(bpy.ops.gk, "env_preview_time")
                      and hasattr(bpy.ops.gk, "env_preview_play"), "")
                ui._ENV_PREVIEW_HOUR[0] = None
                ui._ENV_PREVIEW_PLAY[0] = False
                daynight_node = next(item for item in tools.nodes
                                     if item.bl_idname == "GKNodeDayNight")
                daynight_node.gk_auto = False
                daynight_node.gk_start_hour = 0.0
                night_vals = ui._env_preview_daynight(daynight_node)
                check("昼夜预览:0 点取到夜晚档",
                      night_vals is not None and night_vals[2] < 0.1,
                      str(night_vals))
                night_values = ui._env_preview_values(tools)
                check("昼夜预览:夜里云亮度被压暗",
                      night_values["cloud_light"][0] < 0.5,
                      str(night_values["cloud_light"]))
                ui._ENV_PREVIEW_HOUR[0] = 12.0
                noon_values = ui._env_preview_values(tools)
                check("预览能拨到白天(太阳出来、云变亮)",
                      noon_values["day_amount"] > 0.9
                      and noon_values["sun_on"]
                      and noon_values["sun_elevation"] > 10.0
                      and noon_values["cloud_light"][0]
                      > night_values["cloud_light"][0],
                      "day=%.2f sun=%.2f/el=%.1f"
                      % (noon_values["day_amount"],
                         noon_values["sun_opacity"],
                         noon_values["sun_elevation"]))
                check("白天预览的太阳在正北",
                      abs(noon_values["sun_azimuth"]) < 1e-6,
                      str(noon_values["sun_azimuth"]))
                ui._ENV_PREVIEW_PLAY_FROM[0] = 0.0
                ui._ENV_PREVIEW_PLAY_START[0] = time.time() - 12.0
                ui._ENV_PREVIEW_PLAY[0] = True
                check("预览一天循环在走(12 秒 = 6 点)",
                      abs(ui._env_preview_hour(None) - 6.0) < 0.2,
                      "%.2f" % ui._env_preview_hour(None))
                ui._ENV_PREVIEW_PLAY[0] = False
                ui._ENV_PREVIEW_HOUR[0] = None
                daynight_node.gk_start_hour = 12.0
                day_vals = ui._env_preview_daynight(daynight_node)
                check("昼夜预览:12 点取到白天档",
                      day_vals is not None and day_vals[2] > 0.9,
                      str(day_vals))
                check("预览定时器在没有节点编辑器时也不报错",
                      ui._env_preview_tick() > 0.0, "")
            finally:
                bpy.data.node_groups.remove(tools)
    except Exception as exc:                              # noqa: BLE001
        check("『游戏环境与后期』侧栏", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        bpy.data.node_groups.remove(env_tree)

    # ---- 4c. 『逻辑信息』盒子(第八十六轮:E 组 13)----
    # 默认收起 —— 强制展开,把"有事实 + 有体检"那条路真画一遍:
    # 它读 resolve_scene_tree / 挂脚本 / 上次导出报告 / logic_info 判据,
    # 任何一环拿不到都不许把侧栏整块画没。
    print("=" * 72)
    print("4c. 『逻辑信息』盒子(展开)")
    print("=" * 72)
    try:
        ui._OPEN_SECTIONS["logic_info"] = True
        layout = RecordingLayout()
        holder = type("P", (), {"layout": layout})()
        panel = getattr(ui, "GK_PT_Main", None)
        panel.draw(holder, FakeContext(cube, scene))
        texts = " ".join(layout.texts)
        check("『逻辑信息』展开后画出 体检 / 规模 / 挂脚本",
              "体检" in texts and "挂脚本" in texts
              and ("张图" in texts or "还没有节点图" in texts),
              texts[:300])
        check("『逻辑信息』盒子没把侧栏画崩(其它按钮还在)",
              "gk.export_godot" in layout.operator_names,
              str(sorted(set(layout.operator_names)))[:200])
        ui._OPEN_SECTIONS.pop("logic_info", None)
    except Exception as exc:
        ui._OPEN_SECTIONS.pop("logic_info", None)
        check("『逻辑信息』盒子", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 5. 『游戏资产』资产库的面板 + 统一『资产卡』(第四十三/六十六轮)----
    print("=" * 72)
    print("5. 『游戏资产』面板 + 统一『资产卡』")
    print("=" * 72)
    try:
        tree = bpy.data.node_groups.new("面板冒烟资产43", "GKAssetTree")
        cube.gk_physics.asset_name = cube.name
        # 统一卡:一份 3D 道具 + 一份图片 + 一份音频
        model = tree.nodes.new("GKNodeAssetCard")
        model.gk_kind = "model"
        model.gk_path = cube.name
        image = tree.nodes.new("GKNodeAssetCard")
        image.gk_kind = "image"
        image.gk_path = "不存在的图"
        sound = tree.nodes.new("GKNodeAssetCard")
        sound.gk_kind = "sound"
        sound.gk_path = "res://audio/registered.ogg"
        sound.gk_name = "只登记"
        # 旧卡也要能画(老 .blend 不坏)
        tree.nodes.new("GKNodeAssetImage")
        tree.nodes.new("GKNodeAssetSound")

        panel = getattr(ui, "GK_PT_AssetTree", None)
        if panel is None:
            check("GK_PT_AssetTree 存在", False, "类找不到")
        else:
            layout = RecordingLayout()
            holder = type("P", (), {"layout": layout})()
            panel.draw(holder, FakeNodeContext(cube, scene, tree))
            names = list(layout.operator_names)
            for wanted in ("gk.asset_add_card", "gk.asset_fill_cards",
                           "gk.asset_register", "gk.asset_refresh_thumbs",
                           "gk.switch_tree"):
                check(f"『游戏资产』面板画出 {wanted}", wanted in names,
                      str(sorted(set(names))))

        # 每张卡(统一卡 + 旧卡)的 draw_buttons 都要能画完
        card_error = ""
        for card in tree.nodes:
            try:
                card.draw_buttons(bpy.context, RecordingLayout())
            except Exception as exc:                      # noqa: BLE001
                card_error = (f"{getattr(card, 'bl_label', '?')}: "
                              f"{type(exc).__name__}: {exc}")
                break
        check("资产卡(统一卡 + 旧卡)的 draw_buttons 都能画完",
              not card_error, card_error)
        # 3D 道具卡上的『改名』按钮(第八十九轮:连带改物体 / 卡 / 节点)
        model_layout = RecordingLayout()
        model.draw_buttons(bpy.context, model_layout)
        check("资产卡(3D 道具)画出『改名』按钮",
              "gk.asset_rename" in model_layout.operator_names,
              str(sorted(set(model_layout.operator_names))))
        bpy.data.node_groups.remove(tree)
    except Exception as exc:
        check("『游戏资产』面板", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 6. 『分组框』侧栏盒子(第六十二轮)----
    # 收起后的框只剩一条细标题栏,想再展开得先在图里找到那条细杠 ——
    # 侧栏这一盒就是"按名字点一下就地开合"的入口,画不出来(或者计数
    # 不跟着状态走)整块功能就白做了。
    print("=" * 72)
    print("6. 『分组框』侧栏盒子")
    print("=" * 72)
    try:
        node_groups = import_module(f"{module_name}.node_groups")
        tree = bpy.data.node_groups.new("面板冒烟分组62", "GKGameLogicTree")
        first = tree.nodes.new("GKNodeOnReady")
        second = tree.nodes.new("GKNodePrint")
        frame = tree.nodes.new("NodeFrame")
        frame.label = "开场"
        frame.location = (0.0, 80.0)
        for node in (first, second):
            node.parent = frame

        panel = getattr(ui, "GK_PT_Main", None)
        if panel is None:
            check("GK_PT_Main 存在", False, "类找不到")
        else:
            layout = RecordingLayout()
            holder = type("P", (), {"layout": layout})()
            panel.draw(holder, FakeNodeContext(cube, scene, tree))
            names = list(layout.operator_names)
            # 一个组一个开合按钮 + 『全部收起』『全部展开』= 3 个
            check("『分组框』盒子画出了开合按钮",
                  names.count("gk.toggle_group") >= 3, str(sorted(set(names))))
            check("盒子标题报了组数与收起数",
                  any("1 组" in text and "0 收起" in text for text in layout.texts),
                  str(layout.texts[-6:]))

            node_groups.collapse(tree, frame)
            layout = RecordingLayout()
            holder = type("P", (), {"layout": layout})()
            panel.draw(holder, FakeNodeContext(cube, scene, tree))
            check("收起后盒子计数跟着变(读的是真状态)",
                  any("1 收起" in text for text in layout.texts),
                  str(layout.texts[-6:]))
            node_groups.expand(tree, frame)
        bpy.data.node_groups.remove(tree)
    except Exception as exc:
        check("『分组框』侧栏盒子", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 6b. 『显示_效果开关』的卡片(第六十七轮)----
    #
    # ── 为什么单独画一次 ────────────────────────────────────────────
    # 这张卡的 draw_buttons 里是三行 `layout.prop(...)` + 两行 label。
    # 属性名打错 / 参数写错,在 Blender 里是**当场抛异常、卡片整块画不出来**,
    # 而冒烟那层只验"节点在不在、生成对不对",发现不了画的事
    # (第五十五轮侧栏就是这么漏的:冒烟全绿,面板整块是空的)。
    print("=" * 72)
    print("6b. 『显示_效果开关』卡片")
    print("=" * 72)
    try:
        tree = bpy.data.node_groups.new("面板冒烟显示开关67", "GKGameLogicTree")
        switch = tree.nodes.new("GKNodeDisplaySwitch")
        card_error = ""
        try:
            switch.draw_buttons(bpy.context, RecordingLayout())
        except Exception as exc:                          # noqa: BLE001
            card_error = f"{type(exc).__name__}: {exc}"
        check("『显示_效果开关』卡片的 draw_buttons 能画完", not card_error,
              card_error)
        bpy.data.node_groups.remove(tree)
    except Exception as exc:
        check("『显示_效果开关』卡片", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 6c. 『打包游戏(GK)』菜单(第六十八轮建盒 / 第 122 轮搬进原生菜单)----
    #
    # ── 为什么单列 ──────────────────────────────────────────────────
    # 这段是 5 个平台开关 + 3 个文本框 + 一个打包按钮,全靠
    # `layout.prop(settings, ...)` 画出来。属性名写错(或者场景设置拿不到)
    # 就是**整块画不出来** —— 冒烟那层只验"打包算子跑得通",看不见画的事。
    #
    # ── 第 122 轮为什么从"侧栏盒子"改成"菜单"────────────────────────
    # 用户原话:"打包设置移动到 blender 原生的导出里"。盒子删了、
    # 断言的对象换成 `GK_MT_ExportPackage.draw` —— 判据一个字没松:
    # 平台标签、应用名/版本/包名、打包按钮都必须在**真画一次**里出现。
    print("=" * 72)
    print("6c. 『打包游戏(GK)』菜单(文件 → 导出)")
    print("=" * 72)
    try:
        menu_class = getattr(ui, "GK_MT_ExportPackage", None)
        if not check("GK_MT_ExportPackage 存在", menu_class is not None):
            raise RuntimeError("菜单类找不到")
        check("GK_MT_ExportPackage 已注册(Blender 认得它)",
              hasattr(menu_class, "bl_rna"))
        layout = RecordingLayout()
        holder = type("P", (), {"layout": layout})()
        menu_class.draw(holder, FakeContext(cube, scene))
        names = list(layout.operator_names)
        check("『打包游戏(GK)』菜单画出了『开始打包』",
              "gk.export_release" in names, str(sorted(set(names))))
        texts = " ".join(str(item) for item in layout.texts)
        labels = [ui.packaging.PLATFORMS[key]["label"]
                  for key in ui.packaging.PLATFORM_ORDER]
        check("『打包游戏(GK)』菜单列出了 5 个平台",
              all(label in texts for label in labels),
              str([label for label in labels if label not in texts]))
        # 应用名 / 版本 / 安卓包名三个文本框:draw 是 `prop(settings, 名字)`,
        # 名字写错在 Blender 里是**静默**跳过(那一行直接不见)。
        prop_names = [call for call in layout.calls if call == "prop"]
        check("『打包游戏(GK)』菜单画了参数行(应用名/版本/包名)",
              len(prop_names) >= 8,
              f"prop 调用 {len(prop_names)} 次(5 平台 + 3 参数 = 8)")
        # 侧栏那一块必须**真的没了** —— 否则用户会在两处看到打包设置
        check("侧栏不再出现『打包设置』盒子",
              not hasattr(ui, "_draw_packaging_box"))
        # 钩子挂在原生的 文件 → 导出 上(append,不能把官方导出项顶掉)
        hooked = [func for func in (ui._menu_draw_funcs(
            __import__("bpy").types.TOPBAR_MT_file_export) or [])
            if getattr(func, ui._GK_MENU_HOOK, False)]
        check("文件 → 导出 挂上了本插件的子菜单", len(hooked) == 1,
              f"带标记的回调 {len(hooked)} 个")
    except Exception as exc:
        check("『打包游戏(GK)』菜单", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 6d. 『变量』『函数』两块的新判据(第八十九轮:I 组 27)----
    # 细看这两块补的东西:调用次数**真数**、未声明清单(带『补声明』)、
    # 死变量标『没用上』、重名告警、改名按钮。全是**画**出来的 ——
    # 属性名 / 参数写错当场整块消失,冒烟那层看不见(它只验算子行为)。
    print("=" * 72)
    print("6d. 『变量』『函数』盒子(第八十九轮细看)")
    print("=" * 72)
    try:
        tree = bpy.data.node_groups.new("面板冒烟变量函数89",
                                        "GKGameLogicTree")
        func = tree.gk_funcs.add()
        func.func_name = "攻击"
        tree.nodes.new("GKNodeFunctionDefine").gk_func_name = "攻击"
        tree.nodes.new("GKNodeFunctionCall").gk_func_name = "攻击"
        tree.nodes.new("GKNodeFunctionCall").gk_func_name = "攻击"
        # 引用了但没声明(改名 / 拼错的遗留)
        tree.nodes.new("GKNodeFunctionCall").gk_func_name = "走路"
        used = tree.gk_vars.add()
        used.var_name = "血量"
        used.var_type = "float"
        tree.nodes.new("GKNodeGetVar").gk_var_name = "血量"
        dead = tree.gk_vars.add()
        dead.var_name = "没人用"

        ui._OPEN_SECTIONS["functions"] = True
        ui._OPEN_SECTIONS["variables"] = True
        layout = RecordingLayout()
        holder = type("P", (), {"layout": layout})()
        panel = getattr(ui, "GK_PT_Main", None)
        panel.draw(holder, FakeNodeContext(cube, scene, tree))
        names = list(layout.operator_names)
        texts = " ".join(str(item) for item in layout.texts)
        check("『函数』框画出『改名』按钮",
              "gk.func_rename" in names, str(sorted(set(names)))[:200])
        check("『变量』框画出『改名』按钮",
              "gk.logic_var_rename" in names, str(sorted(set(names)))[:200])
        check("『函数』框:调用次数真数 + 未声明清单(带补声明)",
              "定义 1 个 · 调用 2 次" in texts
              and "走路 · 未声明" in texts
              and names.count("gk.func_add") >= 2,       # 一个声明 + 一个补声明
              texts[-300:])
        check("『变量』框:没用上的变量标出来了",
              "没用上" in texts and "设 0 · 读 1" in texts, texts[-300:])

        # 同名再声明一遍 -> 当场告警(导出只保留第一处,是静默失效)
        dup = tree.gk_vars.add()
        dup.var_name = "血量"
        layout = RecordingLayout()
        holder = type("P", (), {"layout": layout})()
        panel.draw(holder, FakeNodeContext(cube, scene, tree))
        texts = " ".join(str(item) for item in layout.texts)
        check("『变量』框:同名声明两遍 -> 告警",
              "和上面的重名" in texts, texts[-200:])
        ui._OPEN_SECTIONS.pop("functions", None)
        ui._OPEN_SECTIONS.pop("variables", None)
        bpy.data.node_groups.remove(tree)
    except Exception as exc:
        ui._OPEN_SECTIONS.pop("functions", None)
        ui._OPEN_SECTIONS.pop("variables", None)
        check("『变量』『函数』盒子", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 6e. AI 建议卡片:多条时画出『全部执行』(第九十轮:待办 31)----
    # 侧栏那一排"全部执行(N) / 全部忽略"只有**画**出来才点得到 ——
    # 属性名写错 / 参数配错就是整块消失,冒烟那层看不见(它只验算子行为)。
    print("=" * 72)
    print("6e. AI 建议卡片的批量按钮")
    print("=" * 72)
    try:
        advisor = import_module(f"{module_name}.advisor")
        advisor.clear_proposals()
        panel = getattr(ui, "GK_PT_Main", None)

        def _draw_advisor():
            # ⚠ 第一百三十九轮:『出问题了? / 项目顾问』**不再挂进侧栏**
            #   (用户:"这个存在位置不太好,且 AI 的操作项目自由度差,全删了")。
            #   所以这里直接调那个函数 —— 卡片本身还在,以后换个位置挂回去
            #   时这段覆盖仍然有效。
            layout = RecordingLayout()
            # _draw_advisor_box 收的是 **layout**(不是面板 holder)
            ui._draw_advisor_box(layout, FakeContext(cube, scene),
                                 getattr(scene, "gk_settings", None))
            return layout

        # 一条建议:不该出现批量按钮(单条点它自己的『执行』就够了)
        advisor.add_proposal({"action": "enable_god_camera",
                              "label": "打开上帝摄像机", "why": "看场景"})
        names = list(_draw_advisor().operator_names)
        check("单条建议时**不**画『全部执行』",
              "gk.apply_all_advisor_actions" not in names
              and "gk.apply_advisor_action" in names,
              str(sorted(set(names)))[:200])

        # 两条建议:批量按钮出现,且带张数
        advisor.add_proposal({"action": "enable_nav",
                              "label": "打开寻路导航网格", "why": "绕开墙"})
        layout = _draw_advisor()
        names = list(layout.operator_names)
        texts = " ".join(str(item) for item in layout.texts)
        check("两条建议时画出『全部执行(2)』/『全部忽略』",
              "gk.apply_all_advisor_actions" in names
              and "gk.dismiss_all_advisor_actions" in names
              and "全部执行(2)" in texts,
              f"{str(sorted(set(names)))[:200]} texts={texts[:120]}")
        advisor.clear_proposals()
    except Exception as exc:
        check("AI 建议卡片的批量按钮", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 6e-2. AI 建议卡片带 args 的动作(第一百零七轮)----
    # 改节点图的动作要的参数不止 target / value(哪张图、哪种节点、接在谁
    # 后面),这些全在 `args` 里。卡片**必须把它们画出来** —— 否则用户点
    # 『执行』前根本不知道它要改哪儿,那就不是"待确认"而是"盲签"。
    # 参数名 / 值写错在 Blender 里是当场抛异常、整块卡片消失(侧栏一漏,
    # 冒烟那层只验算子行为,看不见画的事)。
    print("=" * 72)
    print("6e-2. AI 建议卡片的 args(改节点图)")
    print("=" * 72)
    try:
        advisor = import_module(f"{module_name}.advisor")
        advisor.clear_proposals()
        panel = getattr(ui, "GK_PT_Main", None)
        advisor.add_proposal({
            "action": "add_node", "label": "新建节点并连线",
            "target": "", "value": "",
            "args": {"tree": "游戏逻辑", "type": "打印_输出",
                     "after": "开场"},
            "why": "给你接一个打印,方便看变量值"})
        layout = None
        draw_error = ""
        try:
            layout = RecordingLayout()
            ui._draw_advisor_box(layout, FakeContext(cube, scene),
                                 getattr(scene, "gk_settings", None))
        except Exception as exc:                          # noqa: BLE001
            draw_error = f"{type(exc).__name__}: {exc}"
        texts = " ".join(str(item) for item in (layout.texts if layout else []))
        check("带 args 的建议卡片画得完", not draw_error, draw_error)
        check("卡片把 args 的键值都画出来了",
              "tree: 游戏逻辑" in texts and "type: 打印_输出" in texts
              and "after: 开场" in texts,
              texts[:260])
        check("带 args 的卡片仍画出了『执行』按钮",
              "gk.apply_advisor_action" in
              [str(name) for name in (layout.operator_names if layout else [])],
              str(layout.operator_names if layout else [])[:160])
    except Exception as exc:
        check("带 args 的建议卡片", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        # 画完一定要把这张卡片清掉 —— 残留的提议会影响后面小节的断言
        try:
            import_module(f"{module_name}.advisor").clear_proposals()
        except Exception:                                  # noqa: BLE001
            pass

    # ---- 6f. 『存档_保存』的卡片(第九十三轮:多了一个『防改档』勾选框)----
    # 存档卡片上原来只有『存档槽』一个输入框,现在多一行勾选框 ——
    # 属性名写错 / 参数配错在 Blender 里是**当场抛异常、卡片整块消失**,
    # 冒烟那层看到的是"节点在、代码也对",唯独看不见画的事。
    print("=" * 72)
    print("6f. 『存档_保存』卡片(防改档勾选框)")
    print("=" * 72)
    try:
        tree = bpy.data.node_groups.new("面板冒烟存档93", "GKGameLogicTree")
        save = tree.nodes.new("GKNodeSaveWrite")
        card_error = ""
        try:
            layout = RecordingLayout()
            save.draw_buttons(bpy.context, layout)
        except Exception as exc:                          # noqa: BLE001
            card_error = f"{type(exc).__name__}: {exc}"
        check("『存档_保存』卡片的 draw_buttons 能画完", not card_error,
              card_error)
        check("『存档_保存』卡片画出了『防改档』勾选框",
              not card_error and "防改档(存校验和)" in layout.texts,
              str(layout.texts)[:200])
        bpy.data.node_groups.remove(tree)
    except Exception as exc:
        check("『存档_保存』卡片(防改档勾选框)", False,
              f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 6g. 七个联机节点的卡片(第一百轮)----
    # 卡片上有输入框 / 下拉 / 勾选框 + 一行"发给谁"的联动 —— 属性名写错
    # 或参数配错在 Blender 里是**当场抛异常、整块卡片画不出来**,
    # 冒烟那层只验"节点在、代码对",看不见画的事(第五十五轮就是这么漏的)。
    print("=" * 72)
    print("6g. 联机节点卡片(第一百轮)")
    print("=" * 72)
    try:
        tree = bpy.data.node_groups.new("面板冒烟联机100", "GKGameLogicTree")
        cards = {
            "开房": tree.nodes.new("GKNodeNetHost"),
            "加入": tree.nodes.new("GKNodeNetJoin"),
            "断开": tree.nodes.new("GKNodeNetDisconnect"),
            "发消息": tree.nodes.new("GKNodeNetSend"),
            "有人进来": tree.nodes.new("GKNodeNetPeerJoined"),
            "有人走了": tree.nodes.new("GKNodeNetPeerLeft"),
            "收到消息": tree.nodes.new("GKNodeNetMessage"),
        }
        card_error = ""
        for label, card in cards.items():
            try:
                card.draw_buttons(bpy.context, RecordingLayout())
            except Exception as exc:                      # noqa: BLE001
                card_error = f"{label}: {type(exc).__name__}: {exc}"
                break
        check("七个联机节点的 draw_buttons 都能画完", not card_error,
              card_error)

        host_layout = RecordingLayout()
        cards["开房"].draw_buttons(bpy.context, host_layout)
        check("『联机_开房』卡片画出端口 / 上限",
              "端口" in host_layout.texts and "上限" in host_layout.texts,
              str(host_layout.texts)[:200])

        # 『发给谁』只在选了"某个玩家"时才画出来(联动)
        send_card = cards["发消息"]
        all_layout = RecordingLayout()
        send_card.draw_buttons(bpy.context, all_layout)
        check("『联机_发消息』选『所有人』时不画『谁』",
              "谁" not in all_layout.texts, str(all_layout.texts)[:200])
        send_card.gk_mode = "ONE"
        one_layout = RecordingLayout()
        send_card.draw_buttons(bpy.context, one_layout)
        check("『联机_发消息』选了『某个玩家』才画出『谁』",
              "谁" in one_layout.texts, str(one_layout.texts)[:200])

        recv_card = cards["收到消息"]
        recv_layout = RecordingLayout()
        recv_card.draw_buttons(bpy.context, recv_layout)
        check("『联机_收到消息』画出『任何频道』勾选框",
              "任何频道" in recv_layout.texts, str(recv_layout.texts)[:200])
        bpy.data.node_groups.remove(tree)
    except Exception as exc:
        check("联机节点卡片(第一百轮)", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 6h. 『卡通材质』(高光)盒的测试已删除(第一百二十六轮)----
    # 那一盒的**界面**被用户要求删掉了(原话:"物体属性的粒子与高光
    # 都是之前做的但几乎没什么用,删了吧"),盒子没了,这 7 条自然
    # 一起走。⚠ 只删了界面:材质上的高光属性、`npr_preview` 的贴图
    # 读写、导出路径都还在 —— 所以这一节**不是**在测一个已删除的功能,
    # 而是没有界面可测了。想恢复界面就从 git 历史取回那一盒。

    # ---- 第一百零三轮:界面设计图面板(拖动按钮 + 从 0 到 1 引导)------
    # 拖动按钮必须画出来(第 7 节还会自动验它是个真算子);引导那五行要
    # 真的出现在面板上 —— 用户问的"从什么到什么"就是靠它回答的。
    print("=" * 72)
    print("6.5 界面设计图面板(第一百零三轮)")
    print("=" * 72)
    try:
        design = bpy.data.node_groups.new("面板冒烟界面103", "GKUiDesignTree")
        widget = design.nodes.new("GKNodeUiText")
        widget.name = "面板冒烟标题"
        widget.gk_text = "标题"
        panel = getattr(ui, "GK_PT_UiDesign", None)
        if panel is None:
            check("GK_PT_UiDesign 存在", False, "类找不到")
        else:
            layout = RecordingLayout()
            holder = type("P", (), {"layout": layout})()
            panel.draw(holder, FakeNodeContext(cube, scene, design))
            ops_drawn = [str(name) for name in layout.operator_names]
            texts = " ".join(str(item) for item in layout.texts)
            check("界面面板:画出了『拖动摆放控件』按钮",
                  "gk.ui_drag_widget" in ops_drawn, str(ops_drawn))
            check("界面面板:『从 0 到 1』引导都在",
                  "从 0 到 1" in texts and "① 定屏幕" in texts
                  and "⑥ 验收" in texts, texts[:260])
            check("界面面板:控件清单还在(父 → 子)",
                  "面板冒烟标题" in texts, texts[:260])
        bpy.data.node_groups.remove(design)
    except Exception as exc:
        check("界面设计图面板(第一百零三轮)", False,
              f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 6i. 『界面_面板』卡片(第一百零五轮:模态三件套勾选框)----
    # 卡片上原来只有『垫底 / 开局藏』,现在多一行三个勾选框 —— 属性名写错 /
    # 参数配错在 Blender 里是**当场抛异常、卡片整块消失**,冒烟那层只验
    # "节点在、代码对",看不见画的事。
    print("=" * 72)
    print("6i. 『界面_面板』卡片(挡住点击 / 点外面关 / 首焦点)")
    print("=" * 72)
    try:
        # 第一百一十二轮:控件构建节点只属于『游戏界面』设计图(一直如此),
        # 以前这里用 GKGameLogicTree 建,节点根本放不进去(这条断言是坏的);
        # 顺手改对成设计图。
        tree = bpy.data.node_groups.new("面板冒烟界面105", "GKUiDesignTree")
        panel_card = tree.nodes.new("GKNodeUiPanel")
        card_error = ""
        try:
            card_layout = RecordingLayout()
            panel_card.draw_buttons(bpy.context, card_layout)
        except Exception as exc:                          # noqa: BLE001
            card_error = f"{type(exc).__name__}: {exc}"
        check("『界面_面板』卡片的 draw_buttons 能画完", not card_error,
              card_error)
        texts = " ".join(str(item) for item in card_layout.texts)
        check("『界面_面板』卡片画出了三个新勾选框",
              "挡住点击" in texts and "点外面关" in texts and "首焦点" in texts,
              texts[:200])
        # 属性得是**真的** Blender 属性(写进 tuple 但没物化 -> 卡片一样崩)
        annotations = getattr(type(panel_card), "__annotations__", {})
        missing = [name for name in ("gk_block_click", "gk_close_outside",
                                     "gk_focus_first") if name not in annotations]
        check("『界面_面板』三个新属性都物化过", not missing, str(missing))
        bpy.data.node_groups.remove(tree)
    except Exception as exc:
        check("『界面_面板』卡片(第一百零五轮)", False,
              f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 6j. 『导出设置』盒**已删**(第一百三十九轮)----
    # 用户原话:"导出设置:全删,理由同上(这些是默认行为)"。
    # 这条改成"反过来钉":侧栏里不该再出现这两个开关与那句说明 ——
    # 哪天有人把盒子加回来,这里会先红。
    print("=" * 72)
    print("6j. 『导出设置』盒已删(不该再画出来)")
    print("=" * 72)
    try:
        panel = getattr(ui, "GK_PT_Main", None)
        gk_settings = getattr(scene, "gk_settings", None)
        previous = getattr(gk_settings, "show_export_settings", False)
        gk_settings.show_export_settings = True
        layout = None
        draw_error = ""
        try:
            layout = RecordingLayout()
            holder = type("P", (), {"layout": layout})()
            panel.draw(holder, FakeContext(cube, scene))
        except Exception as exc:                          # noqa: BLE001
            draw_error = f"{type(exc).__name__}: {exc}"
        finally:
            gk_settings.show_export_settings = previous
        check("精简后的侧栏仍能画完(GK_PT_Main 不抛异常)", not draw_error,
              draw_error)
        texts = " ".join(str(item) for item in (layout.texts if layout else []))
        check("侧栏里没有『不导形态键』了", "不导形态键" not in texts,
              texts[:200])
        check("侧栏里没有『不导切线』了", "不导切线" not in texts,
              texts[:200])
        check("侧栏里没有『导出设置』盒了", "导出设置" not in texts,
              texts[:200])
    except Exception as exc:
        check("『导出设置』盒子(第一百零六轮 B)", False,
              f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 6k. 『界面_列表_填充』卡片(第一百零六轮 A:每行可点)----
    # 卡片上多了一行『每行可点』勾选框 —— 属性名 / 参数配错在 Blender 里是
    # **当场抛异常、卡片整块消失**,冒烟那层只验"节点在、代码对"。
    print("=" * 72)
    print("6k. 『界面_列表_填充』卡片(每行可点)")
    print("=" * 72)
    try:
        # 第一百一十二轮:『界面_列表_填充』是界面行为节点,只属于设计图;
        # 这里对应用设计树建(以前用 GKGameLogicTree,节点放不进去)。
        tree = bpy.data.node_groups.new("面板冒烟列表106", "GKUiDesignTree")
        fill_card = tree.nodes.new("GKNodeUiListFill")
        card_error = ""
        try:
            card_layout = RecordingLayout()
            fill_card.draw_buttons(bpy.context, card_layout)
        except Exception as exc:                          # noqa: BLE001
            card_error = f"{type(exc).__name__}: {exc}"
        check("『界面_列表_填充』卡片的 draw_buttons 能画完", not card_error,
              card_error)
        texts = " ".join(str(item) for item in card_layout.texts)
        check("『界面_列表_填充』卡片画出了『每行可点』勾选框",
              "每行可点" in texts, texts[:200])
        # 三个出口 + 那个新属性都得是**真的**(写进 tuple 没物化 -> 卡片一样崩)
        outputs = [pin.name for pin in getattr(fill_card, "outputs", ())]
        check("『界面_列表_填充』有三个出口(完成 / 点行 / 第几行)",
              outputs == ["完成", "点行", "第几行"], str(outputs))
        check("『界面_列表_填充』的『每行可点』真物化过",
              "gk_clickable" in getattr(type(fill_card), "__annotations__", {}))
        bpy.data.node_groups.remove(tree)
    except Exception as exc:
        check("『界面_列表_填充』卡片(第一百零六轮 A)", False,
              f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 6l. 第一百二十七轮:结构件 / 引用 / 事件卡片 + 清单缩进 ----
    # 这批节点的 draw_buttons 全是新写的 —— "画一半崩"在 Blender 里表现为
    # **卡片整块消失**,冒烟那层只验"节点在、代码对",看不见画的事。
    # 顺带在真 Blender 里钉住"连线式父子"的清单缩进。
    print("=" * 72)
    print("6l. 界面结构件 / 引用 / 事件卡片(第一百二十七轮)")
    print("=" * 72)
    try:
        _nb127 = import_module(f"{module_name}.nodes.node_base")
        tree = bpy.data.node_groups.new("面板冒烟界面127", "GKUiDesignTree")
        cards = []
        for idname, name in (
                ("GKNodeUiCanvas", "画布"),
                ("GKNodeUiBox", "竖排框"),
                ("GKNodeUiBackdrop", "底色"),
                ("GKNodeUiTextBox", "标题条"),
                ("GKNodeUiOnMouseClick", "点空白"),
                ("GKNodeUiOnKeyPress", "开背包"),
                ("GKNodeUiWidgetRef", "取按钮"),
                ("GKNodeUiDesignRef", "取界面"),
                ("GKNodeObjectName", "取名字"),
                # 第一百二十八轮:『界面_弹入/弹出』组合卡
                ("GKNodeUiPop", "弹菜单")):
            node = tree.nodes.new(idname)
            node.name = name
            cards.append(node)

        bad_card = ""
        for card in cards:
            try:
                card.draw_buttons(bpy.context, RecordingLayout())
            except Exception as exc:                      # noqa: BLE001
                bad_card = f"{card.bl_label}: {type(exc).__name__}: {exc}"
                break
        check("第一百二十七轮:十张新卡片都画得完", not bad_card, bad_card)

        # 新属性必须**真物化过**(只写进 gk_prop_defs 而没 materialize ->
        # Blender 读这个属性当场 AttributeError,卡片就画不出来)
        check("第一百二十七轮:结构件属性都物化了",
              "gk_mode" in getattr(type(cards[1]), "__annotations__", {})
              and "gk_full" in getattr(type(cards[0]), "__annotations__", {})
              and "gk_bg" in getattr(type(cards[3]), "__annotations__", {}))
        check("第一百二十七轮:事件卡片属性都物化了",
              "gk_button" in getattr(type(cards[4]), "__annotations__", {})
              and "gk_pause" in getattr(type(cards[4]), "__annotations__", {})
              and "gk_key" in getattr(type(cards[5]), "__annotations__", {}))
        check("第一百二十八轮:弹入/弹出卡片属性都物化了",
              "gk_mode" in getattr(type(cards[9]), "__annotations__", {})
              and "gk_direction" in getattr(type(cards[9]), "__annotations__", {})
              and "gk_scrim" in getattr(type(cards[9]), "__annotations__", {}))

        # 连线式父子:控件清单的缩进要认连线
        panel = tree.nodes.new("GKNodeUiPanel")
        panel.name = "底板"
        title = tree.nodes.new("GKNodeUiText")
        title.name = "标题"
        tree.links.new(panel.outputs["自己"], title.inputs["父"])
        widgets = [n for n in tree.nodes if getattr(n, "gk_ui_builder", False)]
        depth_of = {str(n.name): depth
                    for n, depth in ui._ui_design_widget_rows(widgets)}
        check("第一百二十七轮:控件清单按连线缩进(子比父深一级)",
              depth_of.get("标题", 0) == depth_of.get("底板", 0) + 1,
              str(depth_of))
        check("第一百二十七轮:解析器在真 Blender 里认连线",
              _nb127.ui_parent_name(title) == "底板",
              _nb127.ui_parent_name(title))
        check("第一百二十七轮:控件节点都有『父』/『自己』引脚",
              all("父" in [s.name for s in n.inputs]
                  and "自己" in [s.name for s in n.outputs]
                  for n in (panel, title)))

        bpy.data.node_groups.remove(tree)
    except Exception as exc:
        check("界面结构件 / 引用 / 事件卡片(第一百二十七轮)", False,
              f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 7. 面板画出来的按钮必须都是**真算子**(第六十三轮)----
    #
    # ── 为什么补这一关(用户报的"这里显示得没意义")────────────────────
    # 『预设』盒子里那句 `layout.operator("gk.character_preset")` 写了大半年,
    # 一直是**空按钮**:算子 `GK_OT_CharacterPreset` 从没写进 `GK_CLASSES`,
    # 于是根本没注册 —— Blender 对"不存在的算子"是**静默跳过**的,
    # 盒子里只剩一行说明文字。它的**逻辑**(presets.py)倒是有离线单测、
    # 一直是绿的,缺的就是"按钮和算子接上了没有"这一环。
    #
    # 所以这里做一条**通用**断言:凡是面板画出来的 idname,都必须能拿到
    # RNA(`get_rna_type()` 只有真注册过的算子才给),以后再加面板 / 再加
    # 算子,忘了登记当场就红。
    print("=" * 72)
    print("7. 面板画出来的按钮都得是真算子")
    print("=" * 72)
    drawn = sorted({name for layout in LAYOUTS
                    for name in layout.operator_names if name.startswith("gk.")})
    bad_ops = []
    for idname in drawn:
        try:
            group, name = idname.split(".", 1)
            getattr(getattr(bpy.ops, group), name).get_rna_type()
        except Exception as exc:                          # noqa: BLE001
            bad_ops.append(f"{idname}({type(exc).__name__})")
    print(f"    面板一共画了 {len(drawn)} 个 gk.* 按钮")
    check(f"面板画的 {len(drawn)} 个按钮都是已注册算子", not bad_ops,
          ", ".join(bad_ops))

    if module_name == "BlenderGameKit":
        import_module(module_name).unregister()
    else:
        addon_utils.disable(module_name, default_set=True)
    return 0

try:
    code = main()
except Exception:
    traceback.print_exc()
    code = 1

passed = sum(1 for _, ok, _ in RESULTS if ok)
failed = [name for name, ok, _ in RESULTS if not ok]
print("=" * 72)
print(f"结果: {passed}/{len(RESULTS)} 通过")
if failed:
    print("失败项: " + ", ".join(failed))
print("PANELS=" + ("OK" if code == 0 and not failed else "FAILED"))
print("=" * 72)

sys.exit(0 if (code == 0 and not failed) else 1)
