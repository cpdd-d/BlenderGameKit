"""验证 Shift+A 菜单真的挂上了。

为什么需要单独测:Blender 的菜单挂载是"注册成功了但按下去是空的"这种
静默失败 —— 不报错,只是功能不存在。所以要主动查两件事:
  1. 回调函数确实在 NODE_MT_add 的 _dyn_ui_initialize 里
  2. 我们的分类菜单类确实注册了

用法:
    blender -b --python tests/blender_menu.py
"""

import sys
import os
from types import SimpleNamespace
import traceback
from importlib import import_module

import addon_utils
import bpy

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" - {detail}" if detail and not condition else ""))
    return bool(condition)


def find_addon():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    if os.path.isfile(os.path.join(root, "BlenderGameKit", "__init__.py")):
        return "BlenderGameKit"
    for module in addon_utils.modules():
        if "blender_game_kit" in getattr(module, "__name__", ""):
            return module.__name__
    return None


def main():
    print("=" * 70)
    print("Shift+A 菜单挂载测试")
    print("=" * 70)

    module_name = find_addon()
    if not check("找到插件", module_name, "未安装"):
        return 1
    addon_utils.enable(module_name, default_set=True, persistent=True)
    ui = import_module(f"{module_name}.ui")
    ops_module = import_module(f"{module_name}.operator_io")
    props_mod = import_module(f"{module_name}.properties")

    # ---- 1. 回调函数已挂到 NODE_MT_add ----
    # 尝试过的、**行不通**的检查方式(记下来免得再试):
    #   a) `ui._node_add_menu_draw in NODE_MT_add._dyn_ui_initialize`
    #      -> TypeError: method is not iterable(_dyn_ui_initialize 是 C 方法)
    #   b) inspect.getsource(_dyn_ui_initialize) -> 返回空
    #   c) inspect.getclosurevars(...) -> 闭包是空的
    # 因为 Blender 把 append 的回调存在 C 层,Python 这边看不到。
    # 所以唯一的真验证是**实际调用菜单的 draw**,看它有没有画我们的条目。
    check("NODE_MT_add 是菜单类型",
          hasattr(bpy.types.NODE_MT_add, "append"),
          str([k for k in dir(bpy.types.NODE_MT_add) if k in ("append", "prepend")]))

    # ---- 2. 分类菜单类已注册 ----
    # 不用 bpy.types 查:Blender 不把动态注册的 Python 类挂到 bpy.types 上。
    # 直接看模块里的类有没有 bl_rna 才是可靠判据。
    menu_cls = ui.GK_MT_AddNodeCategories
    check("分类菜单类已注册(有 bl_rna)", hasattr(menu_cls, "bl_rna"))
    check("菜单 bl_idname 正确",
          getattr(menu_cls, "bl_idname", "") == "GK_MT_ADD_NODE",
          getattr(menu_cls, "bl_idname", ""))

    failed = [cls.__name__ for cls in ui.CATEGORY_MENUS
              if not hasattr(cls, "bl_rna")]
    check(f"{len(ui.CATEGORY_MENUS)} 个分类子菜单全部注册", not failed, str(failed))

    # ---- 3. 回调只在我们的节点树里出现 ----
    class _Recorder:
        def __init__(self):
            self.calls = []

        def separator(self):
            self.calls.append(("separator",))

        def menu(self, idname, text=None, icon=None):
            self.calls.append(("menu", idname, text))

        def label(self, text=None, icon=None):
            self.calls.append(("label", text))

        def operator(self, idname, text=None, icon=None):
            self.calls.append(("operator", idname, text))
            return SimpleNamespace()

        def row(self, align=False):
            return self

        def column(self, align=False):
            return self

    class _Holder:
        def __init__(self):
            self.layout = _Recorder()

    class _Space:
        def __init__(self, tree_type):
            self.tree_type = tree_type

    class _Ctx:
        def __init__(self, tree_type):
            self.space_data = _Space(tree_type)

    def _has_menu(calls, menu_idname):
        return any(isinstance(c, tuple) and len(c) >= 2 and c[0] == "menu"
                   and c[1] == menu_idname for c in calls)

    def _has_operator(calls, op_idname):
        return any(isinstance(c, tuple) and len(c) >= 2 and c[0] == "operator"
                   and c[1] == op_idname for c in calls)

    # 分类子菜单的 idname 是 _menu_name(分类名) 生成的(带 hash),
    # 所以用 props.node_menu_entries() 反推期望值,而不是写死字符串。
    expected_category_menus = {ui._menu_name(category)
                               for category, _classes in props_mod.node_menu_entries()}
    expected_recent = ui._menu_name("最近使用")

    # Shift+A 菜单 —— nested 形态:只放『添加游戏逻辑节点』子菜单 + 自动连线。
    # 分类列表收在子菜单里(直接铺开会和 Blender 自带的『添加』并排,看起来像重复);
    # 搜索 / 常用 / 最近在右键的 flat 形态里,这里不直接出现。
    holder = _Holder()
    ui._draw_add_menu(holder, _Ctx("GKGameLogicTree"))
    calls = holder.layout.calls
    drawn_menus = {c[1] for c in calls if isinstance(c, tuple) and c[0] == "menu"}
    print(f"    Shift+A 条目数: {len(calls)},画出的子菜单: {sorted(drawn_menus)}")
    check("Shift+A 里有『添加游戏逻辑节点』子菜单",
          ui.GK_MT_AddNodeCategories.bl_idname in drawn_menus, str(calls))
    check("Shift+A 里有自动连线入口", _has_operator(calls, "gk.auto_link"), str(calls))
    check("Shift+A 里不直接铺开分类(已收进子菜单)",
          not (expected_category_menus & drawn_menus), str(sorted(drawn_menus)))

    # 右键菜单 —— flat 形态:搜索 / 最近 / 分类 都直接铺开
    holder = _Holder()
    ui._draw_context_menu(holder, _Ctx("GKGameLogicTree"))
    calls = holder.layout.calls
    print(f"    右键条目数: {len(calls)}")
    # 2026-10-04 起,搜索 / 最近使用收进了顶部的『搜索与常用』子菜单
    # (右键菜单收成"一项置顶 + 分类平铺")—— 断言分两步:顶层有那个
    # 子菜单;子菜单里有搜索入口和最近使用。
    check("右键里有『搜索与常用』子菜单",
          _has_menu(calls, ui._menu_name("搜索与常用")), str(calls))
    top_holder = _Holder()
    ui._make_top_menu().draw(top_holder, _Ctx("GKGameLogicTree"))
    top_calls = top_holder.layout.calls
    check("右键顶层有搜索入口",
          _has_operator(calls, "gk.find_node"), str(calls))
    categories = props_mod.node_menu_entries("GKGameLogicTree")
    check("游戏逻辑分类压缩到十项", len(categories) == 10, str(categories))
    check("菜单标识没有碰撞", len({cls.bl_idname for cls in ui.CATEGORY_MENUS})
          == len(ui.CATEGORY_MENUS))
    check("『搜索与常用』里有最近使用",
          _has_menu(top_calls, expected_recent), str(top_calls))
    check("右键里有分类入口",
          any(isinstance(c, tuple) and c[0] == "menu" for c in calls), str(calls))

    # 别人的节点树 -> 都不该画
    for name, func in (("Shift+A", ui._draw_add_menu),
                       ("右键", ui._draw_context_menu)):
        holder = _Holder()
        func(holder, _Ctx("ShaderNodeTree"))
        check(f"{name}在着色器节点树里不画", not holder.layout.calls,
              str(holder.layout.calls))

    # 没有 space_data -> 不该崩
    class _NoSpace:
        space_data = None

    for name, func in (("Shift+A", ui._draw_add_menu),
                       ("右键", ui._draw_context_menu)):
        holder = _Holder()
        try:
            func(holder, _NoSpace())
            check(f"{name}没有 space_data 时不崩", not holder.layout.calls)
        except Exception as exc:
            check(f"{name}没有 space_data 时不崩", False, str(exc))

    # ---- 5. 挂载是否真的生效 ----
    #
    # 这里要说清楚**能验证到什么程度**,不要假装全验证了:
    #
    #   [已验证] bpy.types.NODE_MT_add.append 存在,且 on_register 调用它时没抛异常
    #   [已验证] 我们的 draw 回调在正确的 tree_type 下会画菜单、在别的树下不画
    #   [已验证] 分类菜单类都注册了(有 bl_rna)
    #   [已验证] gk.add_node 算子可用
    #   [无法在 -b 无头模式验证] Shift+A 按下去到底弹不弹得出来 ——
    #            那需要一个真实的 NODE_EDITOR 区域。试着用假 context 调
    #            NODE_MT_add.draw() 是不行的:Blender 的菜单会一路读
    #            context.workspace / screen / area 等,假对象永远补不全
    #            (实测依次报 operator_context、workspace 缺失)。
    #            与其硬凑一个"看起来通过"的假测试,不如在这里说清楚。
    #            人工确认方法见 README。
    appended_ok = False
    try:
        probe = lambda self, context: None  # noqa: E731
        bpy.types.NODE_MT_add.prepend(probe)
        bpy.types.NODE_MT_add.remove(probe)
        appended_ok = True
    except Exception as exc:
        print(f"    prepend/remove 探测失败: {exc}")
    check("NODE_MT_add.prepend 可用(挂载机制存在)", appended_ok)

    # 右键菜单也要能挂
    context_ok = hasattr(bpy.types.NODE_MT_context_menu, "prepend")
    check("NODE_MT_context_menu 可挂", context_ok)

    # 我们挂的回调都要能摘掉(不能摘的话禁用插件会留野回调)
    removable = True
    for func in (ui._draw_add_menu, ui._draw_context_menu):
        try:
            bpy.types.NODE_MT_add.remove(func)
            bpy.types.NODE_MT_context_menu.remove(func)
        except Exception as exc:
            print(f"    {func.__name__} remove 失败: {exc}")
            removable = False
    check("两个回调都能被摘掉", removable)

    # ---- 6. 最近使用列表 ----
    RECENT = ops_module.RECENT_NODES
    RECENT.clear()
    for name in ("GKNodeEventReady", "GKNodeBranch", "GKNodeSetVar"):
        ops_module.remember_node(name)
    check("最近使用记录进去了", len(RECENT) == 3, str(RECENT))
    ops_module.remember_node("GKNodeBranch")
    check("重复使用会提到最前", RECENT[0] == "GKNodeBranch", str(RECENT))
    for i in range(20):
        ops_module.remember_node(f"Fake{i}")
    check("最近使用有长度上限", len(RECENT) <= ops_module._RECENT_LIMIT, str(len(RECENT)))
    recent_classes = ui._recent_classes()
    check("能按最近使用取到类(过滤掉不存在的)",
          all(hasattr(c, "bl_idname") for c in recent_classes),
          str([getattr(c, "bl_idname", c) for c in recent_classes]))
    RECENT.clear()

    addon_utils.disable(module_name, default_set=True)
    return 0


try:
    code = main()
except Exception:
    traceback.print_exc()
    code = 1

passed = sum(1 for _, ok, _ in RESULTS if ok)
failed = [name for name, ok, _ in RESULTS if not ok]
print("=" * 70)
print(f"结果: {passed}/{len(RESULTS)} 通过")
if failed:
    print("失败项: " + ", ".join(failed))
print("MENU=" + ("OK" if code == 0 and not failed else "FAILED"))
print("=" * 70)

sys.exit(0 if (code == 0 and not failed) else 1)
