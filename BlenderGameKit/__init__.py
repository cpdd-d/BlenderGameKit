"""BlenderGameKit —— 在 Blender 里用中文节点图做游戏逻辑，导出 Godot 4 GDScript。

模块结构:
    gk_log.py        日志
    gk_sockets.py    引脚类型(执行流 / 任意值 + 内置类型语义表)
    nodes/           节点定义与基类
    codegen.py       节点图 -> GDScript 4 代码生成
    exporter.py      落盘 / Godot 工程骨架
    properties.py    场景级导出设置
    operator_io.py   算子(添加节点、导出、预览、打开目录)
    ui.py            侧栏面板与菜单

用法:节点编辑器里新建『游戏逻辑』节点树，从『每当_准备』这类事件节点开始
连线，然后按 N 打开侧栏点『导出为 Godot 脚本』。
"""

bl_info = {
    "name": "BlenderGameKit (游戏逻辑节点 -> Godot)",
    "author": "BlenderGameKit",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "节点编辑器 > 侧栏 (N) > 游戏逻辑",
    "description": "在 Blender 中用中文节点图编写游戏逻辑，一键导出 Godot 4 GDScript",
    "category": "Game Engine",
}

import importlib
import sys

# 子模块顺序即注册顺序(依赖在前)
_SUBMODULES = (
    "gk_log",
    "gk_sockets",
    "project",
    "properties",
    "nodes",
    "codegen",
    "exporter",
    "npr_preview",
    "asset_export",
    "godot_look",
    "godot_glossary",
    "operator_io",
    "ui",
)

_CLASS_REGISTRY = globals().get("_CLASS_REGISTRY") or []
# 注意:`importlib.reload()` 是**原地**重新执行本文件(模块对象与 __dict__
# 都复用)。上面若写成无条件 `= []`,重载后上一版的类清单就丢了 ——
# `register()` 里的"先注销"会空转,旧类留在 Blender 里,
# 菜单回调也跟着一份份叠加。所以复用旧列表。


def _reload_submodules():
    """支持在 Blender 里直接重载插件(开发期便利)。"""
    for name in _SUBMODULES:
        full = f"{__name__}.{name}"
        if full in sys.modules:
            try:
                importlib.reload(sys.modules[full])
            except Exception as exc:  # pragma: no cover
                print(f"[BlenderGameKit] 重载 {name} 失败: {exc}")


def _iter_classes():
    """按注册顺序产出所有需要注册的 Blender 类。

    顺序要求:
      1. 引脚类型
      2. PropertyGroup(必须先于引用它的 PointerProperty 与算子)
      3. 具体节点(基类只是 Python 抽象基类，不注册)
      4. 算子 / 菜单 / 面板 / NodeTree

    注意:_GKNodeBase / GKEventNode / GKStatementNode / GKExpressionNode
    都是抽象基类，没有 bl_label，**不能**交给 register_class，
    否则会报 'expected Node, ... to have an "bl_label" attribute'。
    """
    from . import gk_sockets, operator_io, properties, ui
    from .nodes import nodes as node_lib

    # 1) 引脚类型
    yield from getattr(gk_sockets, "GK_CLASSES", ())
    # 2) 设置属性组
    yield from getattr(properties, "GK_CLASSES", ())
    # 3) 具体节点
    yield from node_lib.ALL_CLASSES
    # 4) 算子、菜单、面板(ui 里的 NodeTree 必须晚于节点)
    yield from getattr(operator_io, "GK_CLASSES", ())
    yield from getattr(ui, "GK_CLASSES", ())


def register():
    import bpy

    # ── 先把上一次的注销干净(踩过的坑)──────────────────────────────
    # `_reload_submodules()` 会**重新 import** 模块,函数对象是新的 ——
    # 之后再也拿不到上一版挂在菜单(NODE_MT_add / NODE_MT_context_menu)
    # 上的旧回调,于是重载几次菜单就叠几份。
    # 用户实测:右键菜单出现了三份。
    #
    # 所以顺序必须是"先注销(此时旧模块还拿得到)、再重载"。
    if _CLASS_REGISTRY:
        try:
            unregister()
        except Exception as exc:  # pragma: no cover
            print(f"[BlenderGameKit] 注销上一次失败(继续): {exc}")

    _reload_submodules()
    _CLASS_REGISTRY.clear()

    for cls in _iter_classes():
        try:
            bpy.utils.register_class(cls)
            _CLASS_REGISTRY.append(cls)
        except Exception as exc:  # pragma: no cover - 只在 Blender 内触发
            print(f"[BlenderGameKit] 注册失败 {getattr(cls, '__name__', cls)}: {exc}")
    # 引脚改名迁移**不在这里做** —— 注册时 `bpy.data` 还是空的
    # (Blender 先注册插件再读 .blend)。挪到了 `load_post`,
    # 见 operator_io._on_load_post。

    # 场景级设置
    #
    # ── 无条件重新挂载(踩过的坑)────────────────────────────────────
    # 以前是"已经有 gk_settings 就跳过" —— 但**旧版本存下来的
    # 设置对象可能属于另一个包路径**。实测用户的文件里挂着
    #     bl_ext.gk_test.blender_game_kit.properties.GKSceneSettings
    # 而当前加载的是 `bl_ext.user_default...`,两个是不同的类。
    # 于是新加的属性(比如 `force_hidden_objects`)**根本不存在**,
    # 读它永远是默认值,用户在面板里也看不到这一项。
    #
    # PointerProperty 重新赋值是安全的(数据按名字存取),
    # 所以这里**每次注册都重新挂**,让类定义永远跟当前代码一致。
    try:
        bpy.types.Scene.gk_settings = bpy.props.PointerProperty(
            type=_scene_settings_type())
    except Exception as exc:  # pragma: no cover
        print(f"[BlenderGameKit] 场景设置挂载失败: {exc}")

    # 物体级物理设置(参考 UPBGE:碰撞形状属于物体本身)
    try:
        bpy.types.Object.gk_physics = bpy.props.PointerProperty(
            type=_object_settings_type())
    except Exception as exc:  # pragma: no cover
        print(f"[BlenderGameKit] 物体物理设置挂载失败: {exc}")

    # ── 快捷键(F5 = 直接运行游戏)──────────────────────────────────
    # 必须在类都注册完之后 —— 快捷键绑的是算子,算子没注册就绑不上。
    try:
        from . import ui as _ui
        if _ui.register_keymaps():
            print("[BlenderGameKit] 已注册快捷键: 节点编辑器 F5 = 直接运行游戏")
    except Exception as exc:  # pragma: no cover
        print(f"[BlenderGameKit] 快捷键注册失败: {exc}")

    for name in _SUBMODULES:
        module = sys.modules.get(f"{__name__}.{name}")
        hook = getattr(module, "on_register", None)
        if callable(hook):
            try:
                hook()
            except Exception as exc:  # pragma: no cover
                print(f"[BlenderGameKit] {name}.on_register 失败: {exc}")




def _scene_settings_type():
    from .properties import GKSceneSettings

    return GKSceneSettings


def _object_settings_type():
    from .properties import GKObjectSettings

    return GKObjectSettings


def unregister():
    import bpy

    # ── 先摘快捷键(它绑的是算子,算子注销后会变成悬空引用)────────────
    try:
        from . import ui as _ui
        _ui.unregister_keymaps()
    except Exception:  # pragma: no cover
        pass

    for name in reversed(_SUBMODULES):
        module = sys.modules.get(f"{__name__}.{name}")
        hook = getattr(module, "on_unregister", None)
        if callable(hook):
            try:
                hook()
            except Exception as exc:  # pragma: no cover
                print(f"[BlenderGameKit] {name}.on_unregister 失败: {exc}")

    try:
        del bpy.types.Scene.gk_settings
    except Exception:
        pass

    # 物体属性也要摘掉。不摘的话禁用插件后 UI 会去访问已经不存在的
    # PropertyGroup,Blender 会报一串 "PointerProperty not found"。
    try:
        del bpy.types.Object.gk_physics
    except Exception:
        pass

    for cls in reversed(_CLASS_REGISTRY):
        # 注册失败的类不会有 bl_rna，直接跳过，否则注销阶段会刷一屏报错
        if not hasattr(cls, "bl_rna"):
            continue
        try:
            bpy.utils.unregister_class(cls)
        except Exception as exc:  # pragma: no cover
            print(f"[BlenderGameKit] 注销失败 {getattr(cls, '__name__', cls)}: {exc}")
    _CLASS_REGISTRY.clear()


if __name__ == "__main__":
    register()
