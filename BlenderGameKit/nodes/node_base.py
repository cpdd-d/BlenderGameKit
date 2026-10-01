"""节点基类与共用工具。

三种节点形态:
    GKEventNode      只有输出执行流(程序入口:准备 / 物理帧 / 输入 …)
    GKStatementNode  有输入+输出执行流(动作:移动 / 播放动画 / 分支 …)
    GKExpressionNode 纯取值,没有执行流(常量 / 数学 / 比较 …)

输入取值统一走 ``input_value()``:
    引脚已连线   -> 交给 codegen 递归求上游表达式
    引脚未连线   -> 读同名属性(gk_prop_defs 声明)或引脚 default_value
``gk_input_props`` 记录"未连线时该读哪个属性"，这样节点可以用一个带单位、
能在节点体上直接调的 Blender 属性(如 速度 300.0)去对应一个输入引脚。

声明式属性
----------
Blender 的 RNA 只在**类注解**里识别 bpy.props，所以节点里写成:
    gk_prop_defs = (("gk_speed", "float", 300.0, "速度", "说明"), ...)
由 finalize() 在类创建后物化成 __annotations__ + 类属性。
同一份声明在离线测试(没有 bpy)下也成立，只是注解变成普通装饰。
"""

try:
    import bpy
    from bpy.props import (
        BoolProperty,
        EnumProperty,
        FloatProperty,
        IntProperty,
        StringProperty,
    )
    _BPY = True
except Exception:  # pragma: no cover - 仅离线测试走到这里
    _BPY = False
    bpy = None

# 测试桩环境(gk_stub.install())会注册一个假的 bpy，此时 _BPY 也是 True，
# 但我们需要的是"能被普通 Python 实例化"的桩基类，而不是 bpy.types.Node。
# 用一个显式标记把两件事分开，避免测试台走到 Blender 分支。
_OFFLINE = False
try:
    import gk_stub as _gk_stub_marker  # 只有测试台会提供这个模块
    _OFFLINE = getattr(_gk_stub_marker, "ACTIVE", False)
except Exception:
    _OFFLINE = False

_USE_BPY_BASE = _BPY and not _OFFLINE

from .. import gk_sockets as S

# bl_idname -> 节点类，codegen 通过它反查节点语义
node_type_registry = {}


# ==========================================================================
# 常量与小工具
# ==========================================================================
TARGET_SELF = "__self__"

_GDSCRIPT_KEYWORDS = {
    "if", "else", "elif", "for", "while", "match", "break", "continue", "pass",
    "return", "class", "class_name", "extends", "func", "var", "const", "signal",
    "static", "enum", "self", "true", "false", "null", "and", "or", "not", "in",
    "is", "as", "void", "await", "yield", "preload", "load", "tool", "breakpoint",
}


def snake_case(text, fallback="param"):
    """把中文/英文标签转成合法的 GDScript 标识符。中文会被压成下划线。"""
    out = []
    for index, ch in enumerate(str(text)):
        if ch.isascii() and (ch.isalnum() or ch == "_"):
            out.append(ch.lower() if (ch.isupper() and index > 0) else ch)
        elif ch.isascii() and ch in " -.":
            out.append("_")
        else:
            out.append("_")
    name = "".join(out).strip("_")
    while "__" in name:
        name = name.replace("__", "_")
    if not name or name[0].isdigit():
        return fallback
    if name in _GDSCRIPT_KEYWORDS:
        return f"{name}_v"
    return name


def gd_string(text):
    """生成 GDScript 字符串字面量(转义引号、反斜杠、换行)。"""
    escaped = str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _is_plain_node_path(text):
    """路径的每一段是否都是合法的 GDScript 裸标识符。

    `$玩家` 这种写法 Godot 直接报错(裸路径只接受 ASCII 标识符)，
    必须退化成 `$"玩家"`。实测确认 `$"主相机"` 是合法语法。
    """
    if not text:
        return False
    for segment in text.split("/"):
        if not segment or not segment.isascii():
            return False
        if not (segment[0].isalpha() or segment[0] == "_"):
            return False
        if not all(ch.isalnum() or ch == "_" for ch in segment):
            return False
    return True


def gd_node_path(raw):
    """把节点的目标值转换成 GDScript 里的节点访问表达式。"""
    text = (raw or "").strip()
    if not text or text == TARGET_SELF:
        return "self"
    if text.startswith("$") or text.startswith("get_node"):
        return text
    if text.startswith("/root/"):
        return f'get_node("{text}")'
    # 含中文(或其它非 ASCII)的节点名不能写成裸标识符，要用带引号的路径
    if not _is_plain_node_path(text):
        return f'$"{text}"'
    return f"${text}"


def target_pin(name="目标"):
    return S.pin(S.OBJECT, name, TARGET_SELF)


def node_candidates(tree):
    """节点引用下拉列表的候选项。"""
    items = [(TARGET_SELF, "自身(脚本所在节点)", "本脚本挂载的节点", "OBJECT_DATA", 0)]
    if tree is None:
        return items
    index = 1
    for node in tree.nodes:
        if getattr(node, "gk_is_event", False):
            continue
        items.append((node.name, node.name, node.bl_label, "NODE", index))
        index += 1
    return items


# 自动模式的特殊取值 -> 界面上显示的说明文字
AUTO_LABELS = {
    "": "自动(用默认节点)",
    "AnimationPlayer": "自动($AnimationPlayer)",
}


def node_items_for(node):
    """给某个节点生成"节点引用"下拉候选项(含"自身"与"自动")。"""
    tree = getattr(node, "id_data", None)
    items = list(node_candidates(tree))
    current = getattr(node, "gk_target", None)
    if current in AUTO_LABELS:
        items.insert(1, (current, AUTO_LABELS[current], "不指定具体节点，用约定路径", "AUTO", 1))
    return items


def draw_target_picker(node, layout, prop="gk_target"):
    """把节点引用画成"文本框 + 节点下拉菜单"。

    用文本框而不是 EnumProperty，是为了不依赖 Blender 的动态枚举回调;
    右侧的下拉菜单直接列出节点图里真实存在的节点，点一下即写入。
    """
    items = node_items_for(node)
    current = getattr(node, prop, TARGET_SELF) or TARGET_SELF

    row = layout.row(align=True)
    row.prop(node, prop, text="")
    op = row.operator("gk.pick_target", text="", icon="TRIA_DOWN")
    op.node_name = node.name
    op.prop_name = prop


# ==========================================================================
# 声明式属性
# ==========================================================================
class GKProp:
    """离线测试用的属性占位符;在 Blender 内会被 bpy.props 覆盖。"""

    def __init__(self, default=None, name="", description="", items=None, **kwargs):
        self.default = default
        self.name = name
        self.description = description
        self.items = items
        self.kwargs = kwargs

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return obj.__dict__.get(self.name, self.default)

    def __set__(self, obj, value):
        obj.__dict__[self.name] = value


if _BPY:

    def prop_float(default=0.0, name="", description="", **kw):
        return FloatProperty(name=name, description=description, default=default, **kw)

    def prop_int(default=0, name="", description="", **kw):
        return IntProperty(name=name, description=description, default=default, **kw)

    def prop_bool(default=False, name="", description="", **kw):
        return BoolProperty(name=name, description=description, default=default, **kw)

    def prop_text(default="", name="", description="", **kw):
        return StringProperty(name=name, description=description, default=default, **kw)

    def prop_enum(items, default="", name="", description="", **kw):
        return EnumProperty(items=items, default=default, name=name, description=description, **kw)

else:

    def prop_float(default=0.0, name="", description="", **kw):
        return GKProp(default, name=name, description=description, **kw)

    def prop_int(default=0, name="", description="", **kw):
        return GKProp(default, name=name, description=description, **kw)

    def prop_bool(default=False, name="", description="", **kw):
        return GKProp(default, name=name, description=description, **kw)

    def prop_text(default="", name="", description="", **kw):
        return GKProp(default, name=name, description=description, **kw)

    def prop_enum(items, default="", name="", description="", **kw):
        return GKProp(default, name=name, description=description, items=items, **kw)


# 节点引用属性与文本属性在 RNA 层是同一种东西，只是个名字上的区分
def prop_node(default="", name="", description="", **kw):
    return prop_text(default, name=name, description=description, **kw)


_PROP_FACTORY = {
    "float": prop_float,
    "int": prop_int,
    "bool": prop_bool,
    "string": prop_text,
    "node": prop_node,
}


def materialize_props(cls):
    """把 gk_prop_defs 转成真实(Blender 可识别)的属性声明。"""
    prop_map = {}
    annotations = dict(getattr(cls, "__annotations__", {}) or {})
    for entry in getattr(cls, "gk_prop_defs", ()) or ():
        prop_name = entry[0]
        kind = entry[1]
        default = entry[2] if len(entry) > 2 else None
        label = entry[3] if len(entry) > 3 else ""
        desc = entry[4] if len(entry) > 4 else ""
        if kind == "enum":
            items = entry[5] if len(entry) > 5 else ()
            descriptor = prop_enum(
                items,
                default if default is not None else (items[0][0] if items else ""),
                name=label,
                description=desc,
            )
        else:
            descriptor = _PROP_FACTORY.get(kind, prop_text)(default, name=label, description=desc)
        annotations[prop_name] = descriptor
        setattr(cls, prop_name, default)
        prop_map[prop_name] = entry
    cls.__annotations__ = annotations
    cls.gk_prop_map = prop_map
    return cls


def finalize(cls):
    """节点类装饰器:物化声明式属性 + 登记到类型表。"""
    materialize_props(cls)
    node_type_registry[cls.bl_idname] = cls
    return cls


def lookup_node_type(bl_idname):
    return node_type_registry.get(bl_idname)


# ==========================================================================
# 基类
# ==========================================================================
def build_declared_sockets(node):
    """按 gk_inputs / gk_outputs 声明创建引脚。两个分支共用。

    引脚描述是 gk_sockets.pin() 生成的字典 {kind, name, default}。
    """
    for spec in node.gk_inputs:
        socket = node.inputs.new(spec["kind"][0], spec["name"])
        if spec["default"] is not None:
            try:
                socket.default_value = spec["default"]
            except Exception:
                pass
    for spec in node.gk_outputs:
        socket = node.outputs.new(spec["kind"][0], spec["name"])
        if spec["default"] is not None:
            try:
                socket.default_value = spec["default"]
            except Exception:
                pass


if _USE_BPY_BASE:

    class _GKNodeBase(bpy.types.Node):
        """所有 GK 节点的公共基类。"""

        gk_category = "杂项"
        gk_inputs = ()          # pin() 字典序列 {kind, name, default}
        gk_outputs = ()
        gk_input_props = {}     # 引脚名 -> 属性名(未连线时读属性)
        gk_prop_defs = ()       # (属性名, 类型, 默认值, 标签, 说明[, 枚举项])
        gk_is_event = False
        gk_data_only = False

        @classmethod
        def poll(cls, node_tree):
            return node_tree.bl_idname == "GKGameLogicTree"

        # ---- 引脚 ----
        def init(self, context):
            self.build_sockets()

        def build_sockets(self):
            build_declared_sockets(self)

        def rebuild_sockets(self):
            """动态引脚(序列出口数量、向量合并的 2D/3D 切换)用。"""
            self.inputs.clear()
            self.outputs.clear()
            self.build_sockets()

        # ---- 取值 ----
        def get_input_prop(self, socket_name, fallback=None):
            """该输入引脚"未连线"时对应的属性名。"""
            return self.gk_input_props.get(socket_name, fallback)

        def input_value(self, ctx, socket_name, fallback=None):
            """求某个输入引脚的值:已连线交付 codegen 递归,否则读属性/默认值。"""
            socket = self.inputs.get(socket_name)
            if socket is not None and socket.is_linked:
                return ctx.expr_of(socket)
            prop = self.get_input_prop(socket_name)
            if prop and hasattr(self, prop):
                return getattr(self, prop)
            if socket is not None:
                return getattr(socket, "default_value", fallback)
            return fallback

        def input_text(self, ctx, socket_name, fallback=""):
            value = self.input_value(ctx, socket_name, fallback)
            return value if isinstance(value, str) else str(value)

        def input_float(self, ctx, socket_name, fallback=0.0):
            value = self.input_value(ctx, socket_name, fallback)
            return float(value) if isinstance(value, (int, float)) else fallback

        def input_bool(self, ctx, socket_name, fallback=False):
            return bool(self.input_value(ctx, socket_name, fallback))

        # ---- UI ----
        def draw_buttons(self, context, layout):
            pass

        def draw_buttons_ext(self, context, layout):
            box = layout.box()
            box.label(text=f"分类: {self.gk_category}", icon="INFO")
            if self.gk_input_props:
                col = box.column(align=True)
                for socket_name, prop_name in self.gk_input_props.items():
                    col.label(text=f"{socket_name} → {prop_name}", icon="DOT")

else:

    class StubSocketCollection(list):
        """离线测试用的引脚集合:接口与 Blender 的 NodeInputs/NodeOutputs 对齐。

        放在插件里而不是测试里，是为了让插件本身也能在没有 Blender 的环境中
        被实例化(用于回归测试)，并且只维护一套桩实现。
        """

        def __init__(self, node=None, is_output=False):
            super().__init__()
            self.node = node
            self.is_output = is_output

        def get(self, name, default=None):
            for socket in self:
                if socket.name == name:
                    return socket
            return default

        def new(self, bl_idname, name, default=None):
            socket = StubSocket(self.node, name, bl_idname, is_output=self.is_output)
            socket.default_value = default
            self.append(socket)
            return socket

        def clear(self):
            del self[:]

    class StubSocket:
        """离线测试用的引脚。links 里放 StubLink。"""

        def __init__(self, node, name, bl_idname, is_output=False):
            self.node = node
            self.name = name
            self.bl_idname = bl_idname
            self.bl_label = bl_idname
            self.default_value = None
            self.is_output = is_output
            self.links = []

        @property
        def is_linked(self):
            return bool(self.links)

    class _GKNodeBase:
        gk_category = "杂项"
        gk_inputs = ()
        gk_outputs = ()
        gk_input_props = {}
        gk_prop_defs = ()
        gk_is_event = False
        gk_data_only = False

        def __init__(self):
            self.name = type(self).bl_label
            self.label = ""
            self.location = (0.0, 0.0)
            self.inputs = StubSocketCollection(self, False)
            self.outputs = StubSocketCollection(self, True)
            # 用声明里的默认值填出实例属性，行为与 Blender 一致
            for entry in type(self).gk_prop_defs:
                setattr(self, entry[0], entry[2] if len(entry) > 2 else None)
            self.build_sockets()

        # ---- 引脚(与 Blender 分支保持同样的语义，供 codegen 与测试使用) ----
        def build_sockets(self):
            build_declared_sockets(self)

        def rebuild_sockets(self):
            self.inputs.clear()
            self.outputs.clear()
            self.build_sockets()

        def draw_buttons(self, context, layout):
            pass

        def get_input_prop(self, socket_name, fallback=None):
            return self.gk_input_props.get(socket_name, fallback)

        def input_value(self, ctx, socket_name, fallback=None):
            """求某个输入引脚的值:已连线交付 codegen 递归,否则读属性/默认值。"""
            socket = self.inputs.get(socket_name)
            if socket is not None and socket.is_linked:
                return ctx.expr_of(socket)
            prop = self.get_input_prop(socket_name)
            if prop and hasattr(self, prop):
                return getattr(self, prop)
            if socket is not None:
                return getattr(socket, "default_value", fallback)
            return fallback
            
        def input_text(self, ctx, socket_name, fallback=""):
            value = self.input_value(ctx, socket_name, fallback)
            return value if isinstance(value, str) else str(value)

        def input_float(self, ctx, socket_name, fallback=0.0):
            value = self.input_value(ctx, socket_name, fallback)
            return float(value) if isinstance(value, (int, float)) else fallback

        def input_bool(self, ctx, socket_name, fallback=False):
            return bool(self.input_value(ctx, socket_name, fallback))


class GKEventNode(_GKNodeBase):
    """程序入口节点。"""

    gk_is_event = True
    gk_category = "事件"
    gk_inputs = ()


class GKStatementNode(_GKNodeBase):
    """执行语句节点,默认一个"完成"输出。"""

    gk_is_event = False
    gk_outputs = (S.pin(S.FLOW, "完成"),)


class GKExpressionNode(_GKNodeBase):
    """纯取值节点,没有执行流。"""

    gk_category = "运算"
    gk_outputs = (S.pin(S.ANY, "输出"),)
    gk_data_only = True


GK_CLASSES = ()
