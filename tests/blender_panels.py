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

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def recorder(*args, **kwargs):
            self.calls.append(name)
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

    # ---- 3. 面板里的按钮必须画出来 ----
    #
    # 只"不抛异常"不够 —— 如果 draw 在半路 return 了,也不抛异常,
    # 但按钮同样不会出现。所以要确认 operator() 真的被调过。
    print("=" * 72)
    print("3. 关键按钮真的画出来了吗")
    print("=" * 72)
    for class_name, target, wanted in (
            ("GK_PT_ObjectPhysics", cube, ("gk.show_collision",)),
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
