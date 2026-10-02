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

_CLASS_REGISTRY = []


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

    _reload_submodules()
    _CLASS_REGISTRY.clear()

    for cls in _iter_classes():
        try:
            bpy.utils.register_class(cls)
            _CLASS_REGISTRY.append(cls)
        except Exception as exc:  # pragma: no cover - 只在 Blender 内触发
            print(f"[BlenderGameKit] 注册失败 {getattr(cls, '__name__', cls)}: {exc}")
    # 场景级设置
    try:
        bpy.types.Scene.gk_settings = bpy.props.PointerProperty(type=_scene_settings_type())
    except Exception as exc:  # pragma: no cover
        print(f"[BlenderGameKit] 场景设置挂载失败: {exc}")

    # 物体级物理设置(参考 UPBGE:碰撞形状属于物体本身)
    try:
        bpy.types.Object.gk_physics = bpy.props.PointerProperty(
            type=_object_settings_type())
    except Exception as exc:  # pragma: no cover
        print(f"[BlenderGameKit] 物体物理设置挂载失败: {exc}")

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
