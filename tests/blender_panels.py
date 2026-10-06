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
import traceback
from importlib import import_module

import addon_utils
import bmesh
import bpy

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" - {detail}" if detail and not condition else ""))
    return bool(condition)


def find_addon():
    for module in addon_utils.modules():
        if "blender_game_kit" in getattr(module, "__name__", ""):
            return module.__name__
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

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def recorder(*args, **kwargs):
            self.calls.append(name)
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
    addon_utils.enable(module_name, default_set=True, persistent=True)
    ui = import_module(f"{module_name}.ui")

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
            ("GK_PT_Main", cube, ("gk.open_godot", "gk.export_assets")),
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
