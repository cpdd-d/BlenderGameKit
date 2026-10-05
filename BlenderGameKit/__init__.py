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
#
# ── 包要展开成"子模块在前、包在后"(GPT 审查后拆分 codegen 踩的坑)──
# `codegen` / `nodes` 现在是**包**。`importlib.reload(pkg)` 只会重跑包的
# `__init__`,`from . import core` 命中 sys.modules 缓存,子模块里的代码
# **不会**重新执行 —— 开发期在 Blender 里重载后还是旧逻辑。
# 所以把子模块按依赖顺序排在包前面,逐个显式 reload。
_SUBMODULES = (
    "gk_log",
    "gk_sockets",
    "models",
    # 纯工具层:paths 不依赖 bpy;blender_adapter 集中 bpy 访问,供
    # project.layout / exporter 复用(必须在它们之前)
    "paths",
    "blender_adapter",
    # project 也是包:子模块按依赖顺序排在包前面,保证重载时逐个刷新
    "project.layout",
    "project.windows",
    "project.godot",
    "project.metadata",
    "project.input_map",
    "project.scene",
    "project.addon_prefs",
    "project",
    "properties",
    # 预设(一键生成第三人称角色):纯节点/连线逻辑,离线也能 import
    "presets",
    "nodes.node_base",
    # 节点按领域拆成 _*.py,门面 nodes/nodes.py 合并命名空间。
    # _common 必须排最前 —— 它里面 `CLASSES = []` 负责把登记表清空,
    # 否则重载时领域模块的 @N 会往旧表里**重复追加**一遍节点类。
    "nodes._common",
    "nodes._events",
    "nodes._flow",
    "nodes._movement",
    "nodes._animation",
    "nodes._instances",
    "nodes._variables",
    "nodes._input",
    "nodes._expressions",
    "nodes._display",
    "nodes._containers",
    "nodes.nodes",
    "nodes",
    # codegen 主体按领域拆成 10 个模块(纯搬运)。顺序 = 依赖顺序:
    # literals/registry 是叶子,发射器依赖它们;context 依赖 registry;
    # generator 依赖所有。必须"被依赖者在前",否则重载后装饰器会把
    # 处理器登记进**上一版**的注册表(注册表在 registry.py 里重新定义)。
    "curve_assets",
    "codegen.literals",
    "codegen.registry",
    "codegen.easing",
    "codegen.statements",
    "codegen.camera",
    "codegen.expressions",
    "codegen.animation",
    "codegen.services",
    "codegen.context",
    "codegen.generator",
    "codegen.validation",
    "codegen.api",
    "codegen",
    # 导出物清单 / 旧文件清理(纯标准库,离线可测)
    "generated_files",
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


def _log_exception(message):
    """记录异常 + 完整 traceback,统一走 gk_log。

    ── 为什么不能只 `print(f"... {exc}")`(GPT 审查 / 用户 2026-10-04)──
    只留最后一句异常文本会丢掉出错位置和调用栈,用户反馈时给不出线索。
    这里优先用 `gk_log.exception()`;万一 gk_log 还没就绪(重载中途),
    退回 print + traceback,保证**任何阶段**都不会静默吞掉错误。
    """
    import traceback
    try:
        from . import gk_log
        gk_log.exception(message)
    except Exception:
        print(f"[BlenderGameKit] {message}\n{traceback.format_exc()}")


def _reload_submodules():
    """支持在 Blender 里直接重载插件(开发期便利)。"""
    for name in _SUBMODULES:
        full = f"{__name__}.{name}"
        if full in sys.modules:
            try:
                importlib.reload(sys.modules[full])
            except Exception:  # pragma: no cover
                # 重载期几乎什么都可能抛(依赖顺序 / 半初始化的模块),
                # 这里**不能**收窄,但必须留下完整 traceback(GPT 审查)
                _log_exception(f"重载 {name} 失败(继续)")


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
    from . import gk_sockets, operator_io, project, properties, ui
    from .nodes import nodes as node_lib

    # 1) 引脚类型
    yield from getattr(gk_sockets, "GK_CLASSES", ())
    # 2) 设置属性组
    yield from getattr(properties, "GK_CLASSES", ())
    # 2.5) 插件偏好设置(AddonPreferences)
    #
    # ── 这一行是补的(用户 2026-10-04)────────────────────────────
    # `project.GK_CLASSES = (GKAddonPreferences,)` 一直存在,但这里从来没有
    # yield 过它 —— 于是偏好设置类从未注册,面板打不开(既有 bug)。
    yield from getattr(project, "GK_CLASSES", ())
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
        except Exception:  # pragma: no cover
            _log_exception("注销上一次失败(继续)")

    _reload_submodules()
    _CLASS_REGISTRY.clear()

    for cls in _iter_classes():
        try:
            bpy.utils.register_class(cls)
            _CLASS_REGISTRY.append(cls)
        except Exception:  # pragma: no cover - 只在 Blender 内触发
            _log_exception(f"注册失败 {getattr(cls, '__name__', cls)}(跳过该类,继续)")
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
    except Exception:  # pragma: no cover
        _log_exception("场景设置挂载失败")

    # 物体级物理设置(参考 UPBGE:碰撞形状属于物体本身)
    try:
        bpy.types.Object.gk_physics = bpy.props.PointerProperty(
            type=_object_settings_type())
    except Exception:  # pragma: no cover
        _log_exception("物体物理设置挂载失败")

    # 物体级粒子发射器设置(用户 2026-10-05 第二十一轮)。
    # 和 gk_physics 一样每次注册都**重新挂**:旧版本存下来的
    # 设置对象可能属于另一个包路径,不重挂的话新属性读不到。
    try:
        bpy.types.Object.gk_fx = bpy.props.PointerProperty(
            type=_object_fx_type())
    except Exception:  # pragma: no cover
        _log_exception("物体粒子设置挂载失败")

    # ── 快捷键(F5 = 直接运行游戏)──────────────────────────────────
    # 必须在类都注册完之后 —— 快捷键绑的是算子,算子没注册就绑不上。
    try:
        from . import ui as _ui
        if _ui.register_keymaps():
            print("[BlenderGameKit] 已注册快捷键: 节点编辑器 F5 = 直接运行游戏")
    except Exception:  # pragma: no cover
        _log_exception("快捷键注册失败")

    for name in _SUBMODULES:
        module = sys.modules.get(f"{__name__}.{name}")
        hook = getattr(module, "on_register", None)
        if callable(hook):
            try:
                hook()
            except Exception:  # pragma: no cover
                _log_exception(f"{name}.on_register 失败")




def _scene_settings_type():
    from .properties import GKSceneSettings

    return GKSceneSettings


def _object_settings_type():
    from .properties import GKObjectSettings

    return GKObjectSettings


def _object_fx_type():
    from .properties import GKObjectFx

    return GKObjectFx


def unregister():
    import bpy

    # ── 先摘快捷键(它绑的是算子,算子注销后会变成悬空引用)────────────
    try:
        from . import ui as _ui
        _ui.unregister_keymaps()
    except Exception:  # pragma: no cover
        _log_exception("快捷键注销失败(继续)")

    for name in reversed(_SUBMODULES):
        module = sys.modules.get(f"{__name__}.{name}")
        hook = getattr(module, "on_unregister", None)
        if callable(hook):
            try:
                hook()
            except Exception:  # pragma: no cover
                _log_exception(f"{name}.on_unregister 失败")

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

    try:
        del bpy.types.Object.gk_fx
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
