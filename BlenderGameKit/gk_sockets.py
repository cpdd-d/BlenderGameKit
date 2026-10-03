"""GK 引脚类型。

设计取舍:
  * 执行流(白色/金色)和"任意值"(灰色)需要自己的颜色与形状,用自定义
    NodeSocket 子类实现。注意 Blender 要求 NodeSocket 子类必须显式实现
    ``draw`` 和 ``draw_color``，否则注册会抛 AttributeError。
  * 数值 / 文本 / 布尔 / 向量 直接复用 Blender 内置引脚，这样自带输入框、
    拾色器、向量展开等原生 UI，比自定义省事且更好用。

连接规则(见 GKSocketFlow.link_check / GKSocketAny.link_check):
  * 执行流只能接执行流
  * "任意值"可以接任何数据引脚(动态类型)
  * 其它数据引脚颜色不同则拒绝连接，避免把文本接到向量上
"""

import bpy

# ---- 语义分组，供 link_check 判断"是否同类" ----
SEMANTIC_FLOW = "FLOW"
SEMANTIC_ANY = "ANY"
SEMANTIC_DATA = "DATA"

_COLOR_FLOW = (1.0, 0.85, 0.35, 1.0)
_COLOR_ANY = (0.62, 0.62, 0.62, 1.0)
_COLOR_SELF = (0.42, 0.72, 1.0, 1.0)


class GKSocketBase:
    """所有 GK 引脚的共用逻辑(混入类,不直接注册)。"""

    gk_hidden = False

    @classmethod
    def gk_semantic(cls):
        return SEMANTIC_DATA

    def gk_type_name(self):
        return self.bl_label

    def link_check(self, target):
        """返回 True 表示允许连接。"""
        src_sem = self.gk_semantic()
        dst_sem = getattr(target, "gk_semantic", lambda: SEMANTIC_DATA)()
        if src_sem == SEMANTIC_FLOW or dst_sem == SEMANTIC_FLOW:
            return src_sem == dst_sem
        if src_sem == SEMANTIC_ANY or dst_sem == SEMANTIC_ANY:
            return True
        return self.bl_idname == target.bl_idname


class GKSocketFlow(GKSocketBase, bpy.types.NodeSocket):
    """执行流引脚:决定语句的执行顺序。"""

    bl_idname = "GKSocketFlow"
    bl_label = "执行流"

    @classmethod
    def gk_semantic(cls):
        return SEMANTIC_FLOW

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return _COLOR_FLOW


class GKSocketAny(GKSocketBase, bpy.types.NodeSocket):
    """任意值引脚:可接数值 / 文本 / 布尔 / 向量 / 节点，类型由上游决定。"""

    bl_idname = "GKSocketAny"
    bl_label = "任意"

    @classmethod
    def gk_semantic(cls):
        return SEMANTIC_ANY

    def draw(self, context, layout, node, text):
        if self.is_linked or self.is_output:
            layout.label(text=text)
            return
        # ── 通配引脚没有 default_value(用户日志里刷了一屏)──────────────
        # `NodeSocketVirtual`(GKSocketAny 的基类)没有这个属性,
        # 无条件 prop() 会抛
        #     rna_uiItemR: property not found: GKSocketAny.default_value
        # 每个通配引脚一行 —— 用户日志里几十行全是它。
        # 所以先问一句"有没有这个属性",没有就画个标签。
        if not self.bl_rna.properties.get("default_value"):
            layout.label(text=text)
            return
        layout.prop(self, "default_value", text=text)

    def draw_color(self, context, node):
        return _COLOR_ANY


# ---- 内置引脚上的语义标记：靠 bl_idname 反查，无需自定义 ----
_TYPE_TABLE = {
    "NodeSocketFloat": ("数值", "float"),
    "NodeSocketInt": ("整数", "int"),
    "NodeSocketBool": ("布尔", "bool"),
    "NodeSocketString": ("文本", "String"),
    "NodeSocketVector": ("向量", "Vector3"),
    "NodeSocketVector2D": ("二维向量", "Vector2"),
    "NodeSocketColor": ("颜色", "Color"),
    "NodeSocketObject": ("节点", "Node"),
    "NodeSocketVirtual": ("虚", "null"),
    "GKSocketFlow": ("执行流", "void"),
    "GKSocketAny": ("任意", "Variant"),
}

_FLOW_PIN = ("GKSocketFlow", "执行流")
_ANY_PIN = ("GKSocketAny", "任意值")
_FLOAT_PIN = ("NodeSocketFloat", "数值")
_INT_PIN = ("NodeSocketInt", "整数")
_BOOL_PIN = ("NodeSocketBool", "布尔")
_TEXT_PIN = ("NodeSocketString", "文本")
_VECTOR_PIN = ("NodeSocketVector", "向量")
_VECTOR2_PIN = ("NodeSocketVector2D", "二维向量")
_COLOR_PIN = ("NodeSocketColor", "颜色")
_OBJECT_PIN = ("NodeSocketObject", "节点")

GK_CLASSES = (GKSocketFlow, GKSocketAny)


def pin(kind, name, default=None):
    """构造引脚描述:pin(_FLOW_PIN, "完成") / pin(_FLOAT_PIN, "速度", 300.0)"""
    return {"kind": kind, "name": name, "default": default}


def kind_idname(spec):
    return spec[0]


def type_label(idname):
    return _TYPE_TABLE.get(idname, ("未知", "Variant"))[0]


def gdscript_type(idname):
    return _TYPE_TABLE.get(idname, ("未知", "Variant"))[1]


def is_flow(socket):
    return getattr(socket, "gk_semantic", lambda: None)() == SEMANTIC_FLOW


def is_any(socket):
    return getattr(socket, "gk_semantic", lambda: None)() == SEMANTIC_ANY


# 供 node_base 使用的常用引脚速记
FLOW = _FLOW_PIN
ANY = _ANY_PIN
FLOAT = _FLOAT_PIN
INT = _INT_PIN
BOOL = _BOOL_PIN
TEXT = _TEXT_PIN
VECTOR = _VECTOR_PIN
VECTOR2 = _VECTOR2_PIN
COLOR = _COLOR_PIN
OBJECT = _OBJECT_PIN
