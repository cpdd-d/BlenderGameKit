"""离线 bpy 打桩。

让 BlenderGameKit 的节点定义与代码生成器可以在**没有 Blender** 的普通
Python 里跑起来，这样回归测试是秒级的，不用每次启动 Blender。

打桩范围只覆盖插件真正用到的 API:
    bpy.types.Node / NodeTree / NodeSocket
    bpy.props.*
并且模拟引脚连线(links)与 is_linked，让 codegen 的图遍历逻辑得到真实验证。
"""

import sys
import types

# 标记:插件靠它判断自己跑在测试桩环境里，从而使用可实例化的桩基类
ACTIVE = True


class StubProp:
    """最小可用的属性描述符。"""

    def __init__(self, default=None, **kwargs):
        self.default = default
        self.kwargs = kwargs

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return obj.__dict__.get("__prop_" + str(id(self)), self.default)

    def __set__(self, obj, value):
        obj.__dict__["__prop_" + str(id(self))] = value


class StubSocket:
    """仅用于类型标注的占位;真实桩引脚在插件的 node_base 里。"""

    def __repr__(self):
        return "<StubSocket>"


class StubLink:
    def __init__(self, from_socket, to_socket):
        self.from_socket = from_socket
        self.to_socket = to_socket
        self.from_node = from_socket.node
        self.to_node = to_socket.node


def _stub_classes():
    """从插件里取桩引脚类(只维护一套实现)。"""
    from BlenderGameKit.nodes import node_base as NB
    return NB.StubSocket, NB.StubSocketCollection


class StubNode:
    """离线测试用的节点基类(仅作参考，实际节点继承插件的桩基类)。"""

    def __init__(self):
        stub_socket, stub_collection = _stub_classes()
        self.name = type(self).bl_label
        self.label = ""
        self.location = (0.0, 0.0)
        self.inputs = stub_collection(self, False)
        self.outputs = stub_collection(self, True)
        self.width = 140.0
        self.hide = False
        self.mute = False
        for entry in getattr(type(self), "gk_prop_defs", ()) or ():
            setattr(self, entry[0], entry[2] if len(entry) > 2 else None)
        builder = getattr(self, "build_sockets", None)
        if callable(builder):
            builder()

    @property
    def bl_idname(self):
        return type(self).bl_idname

    @property
    def bl_label(self):
        return type(self).bl_label

    def draw_buttons(self, context, layout):
        pass


class StubNodeTree:
    def __init__(self, name="GameLogic"):
        self.name = name
        self.nodes = []
        self.links = []

    def new_node(self, node_class, name=None, location=(0.0, 0.0), **props):
        node = node_class()
        if name:
            node.name = name
        node.location = location
        for key, value in props.items():
            setattr(node, key, value)
        # 属性设置可能改变引脚(序列出口数量 / 向量维度切换)，需要重建一次
        builder = getattr(node, "build_sockets", None)
        if callable(builder):
            builder()
        self.nodes.append(node)
        return node

    def find(self, name):
        for node in self.nodes:
            if node.name == name:
                return node
        return None


def connect(tree, from_node, from_socket, to_node, to_socket):
    """连线。from_socket / to_socket 用引脚名，支持 "输出名" 形式。"""
    source = _pick(from_node.outputs, from_socket)
    target = _pick(to_node.inputs, to_socket)
    if source is None:
        raise KeyError(f"{from_node.name} 没有输出引脚 {from_socket}")
    if target is None:
        raise KeyError(f"{to_node.name} 没有输入引脚 {to_socket}")
    link = StubLink(source, target)
    source.links.append(link)
    target.links.append(link)
    tree.links.append(link)
    return link


def _pick(collection, spec):
    """引脚选择:支持 "名称" 或 ("名称", 序号) 以区分同名引脚。"""
    if isinstance(spec, tuple):
        name, index = spec
        matches = [s for s in collection if s.name == name]
        return matches[index] if index < len(matches) else None
    return collection.get(spec)


def build_bpy_module():
    """构造一个假的 bpy 模块对象。"""
    bpy = types.ModuleType("bpy")

    bpy_types = types.ModuleType("bpy.types")

    class Node:
        pass

    class NodeTree:
        pass

    class NodeSocket:
        pass

    bpy_types.Node = Node
    bpy_types.NodeTree = NodeTree
    bpy_types.NodeSocket = NodeSocket

    # 插件代码里会用到的其它基类。离线测试只导入模块、不注册 UI,
    # 所以给个空基类就够了 —— 缺了它模块 import 就会在 class 语句上炸掉
    # (project.py 定义 AddonPreferences 时踩过)。
    for name in ("AddonPreferences", "Panel", "Menu", "Operator", "PropertyGroup",
                 "Header", "UIList", "KeyingSetInfo", "RenderEngine"):
        setattr(bpy_types, name, type(name, (), {}))

    for name in (
        "NodeSocketFloat", "NodeSocketInt", "NodeSocketBool", "NodeSocketString",
        "NodeSocketVector", "NodeSocketVector2D", "NodeSocketColor", "NodeSocketObject",
        "NodeSocketVirtual", "NodeTreeInterfaceSocketFloat",
    ):
        setattr(bpy_types, name, type(name, (NodeSocket,), {}))

    bpy.props = types.ModuleType("bpy.props")

    def _make_prop_factory(func_name):
        # 用工厂函数而不是直接 lambda,否则所有类型会共享同一个闭包变量,
        # 桩出来的属性分不清自己是哪种类型(经典 late-binding 陷阱)。
        def factory(*args, **kwargs):
            prop = StubProp(**kwargs)
            prop.stub_kind = func_name
            return prop
        factory.__name__ = func_name
        return factory

    # 这份清单要和 node_base 实际用到的类型保持一致 ——
    # 少一个就会让 properties.py 的 import 失败,离线测试直接崩在导入期。
    for func_name in (
        "FloatProperty",
        "IntProperty",
        "BoolProperty",
        "StringProperty",
        "EnumProperty",
        "FloatVectorProperty",
        "PointerProperty",
        "CollectionProperty",
    ):
        setattr(bpy.props, func_name, _make_prop_factory(func_name))

    bpy.utils = types.ModuleType("bpy.utils")

    def register_class(cls):
        return cls

    def unregister_class(cls):
        return cls

    bpy.utils.register_class = register_class
    bpy.utils.unregister_class = unregister_class
    bpy.types = bpy_types
    return bpy


def install():
    """把假 bpy 装进 sys.modules(必须在导入插件之前调用)。"""
    bpy = build_bpy_module()
    sys.modules["bpy"] = bpy
    sys.modules["bpy.types"] = bpy.types
    sys.modules["bpy.props"] = bpy.props
    sys.modules["bpy.utils"] = bpy.utils
    return bpy


def load_addon(package_root, package_name="BlenderGameKit"):
    """按路径加载插件包，返回包模块。"""
    import importlib.util
    import os

    init_path = os.path.join(package_root, "__init__.py")
    spec = importlib.util.spec_from_file_location(
        package_name, init_path, submodule_search_locations=[package_root]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    spec.loader.exec_module(module)
    return module
