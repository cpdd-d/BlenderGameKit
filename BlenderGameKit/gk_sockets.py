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

    def gk_type_name(self):
        """通配引脚解析出类型后,返回具体类型名(否则仍是『任意』)。"""
        return resolved_type_label(self) or self.bl_label

    def draw(self, context, layout, node, text):
        # ── 通配引脚没有类型,按连线对面 / 变量声明把类型补在名字后面 ───────
        # 用户 2026-10-04 要的"自动类型提示":一根灰色万能线到底传的是
        # 数值还是文本,以前完全看不出来。现在解析出来就写成『值 (数值)』。
        caption = socket_caption(self, text)
        if self.is_linked or self.is_output:
            layout.label(text=caption)
            return
        # ── 通配引脚没有 default_value(用户日志里刷了一屏)──────────────
        # `NodeSocketVirtual`(GKSocketAny 的基类)没有这个属性,
        # 无条件 prop() 会抛
        #     rna_uiItemR: property not found: GKSocketAny.default_value
        # 每个通配引脚一行 —— 用户日志里几十行全是它。
        # 所以先问一句"有没有这个属性",没有就画个标签。
        if not self.bl_rna.properties.get("default_value"):
            layout.label(text=caption)
            return
        layout.prop(self, "default_value", text=caption)

    def draw_color(self, context, node):
        # 解析出类型就按那个类型上色,和普通引脚一致;解析不出来才用万能灰。
        return resolved_color(self)


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


# ==========================================================================
# 通配引脚的自动类型提示(用户 2026-10-04)
# ==========================================================================
# 「任意值」引脚是灰的万能接口,接什么都能通 —— 但也因此**看不出**
# 这根线上跑的是数值还是文本。这里做两件事:
#   1. 沿连线(输入看上游 / 输出看下游)找出对面的**具体**引脚类型;
#   2. 找不到就看节点填的『变量名』在树里有没有声明类型(动画树变量)。
# 解析出的类型用来给引脚**上色**、把类型名**写在引脚标签后面**。
_COLOR_BY_IDNAME = {
    "NodeSocketFloat": (0.63, 0.63, 0.63, 1.0),
    "NodeSocketInt": (0.25, 0.55, 0.25, 1.0),
    "NodeSocketBool": (0.75, 0.45, 0.30, 1.0),
    "NodeSocketString": (0.42, 0.60, 0.80, 1.0),
    "NodeSocketVector": (0.30, 0.32, 0.72, 1.0),
    "NodeSocketVector2D": (0.36, 0.30, 0.55, 1.0),
    "NodeSocketColor": (0.75, 0.75, 0.20, 1.0),
    "NodeSocketObject": (0.20, 0.60, 0.20, 1.0),
}

# 动画树变量的声明类型 -> GK 引脚类型 idname
_VAR_TYPE_TO_IDNAME = {
    "bool": "NodeSocketBool",
    "int": "NodeSocketInt",
    "float": "NodeSocketFloat",
    "string": "NodeSocketString",
    "vector": "NodeSocketVector",
}


# GDScript 类型名 -> GK 引脚类型 idname。函数声明的『参数:类型』要用它
# 给『函数_调用』长出对应颜色的参数引脚(用户 2026-10-05 第十七轮)。
_GD_TYPE_TO_IDNAME = {
    "bool": "NodeSocketBool",
    "int": "NodeSocketInt",
    "float": "NodeSocketFloat",
    "String": "NodeSocketString",
    "Vector2": "NodeSocketVector2D",
    "Vector3": "NodeSocketVector",
    "Node": "NodeSocketObject",
}


def idname_for_gd_type(gd_type):
    """GDScript 类型 -> 引脚类型 idname(不认识就给『任意值』引脚)。"""
    return _GD_TYPE_TO_IDNAME.get(str(gd_type or ""), "GKSocketAny")


def _socket_key(socket):
    """给引脚一个可比较的身份。

    Blender 里同一个引脚每次取到的是**不同的 Python 包装对象**,`id()`
    会变 —— 用 `as_pointer()` 才能认出"还是那根引脚",避免在环里绕死。
    """
    pointer = getattr(socket, "as_pointer", None)
    if callable(pointer):
        try:
            return ("p", pointer())
        except Exception:
            pass
    return ("i", id(socket))


def _linked_socket(socket):
    """沿连线走一步:输入引脚看上游,输出引脚看下游(取第一条连线)。"""
    is_output = bool(getattr(socket, "is_output", False))
    for link in getattr(socket, "links", ()) or ():
        other = (getattr(link, "to_socket", None) if is_output
                 else getattr(link, "from_socket", None))
        if other is not None:
            return other
    return None


def _walk_type_idname(socket, limit=64):
    """沿连线找对面的**具体**引脚类型(通配引脚要一路走下去)。"""
    pending = [socket]
    seen = set()
    steps = 0
    while pending and steps < limit:
        current = pending.pop(0)
        steps += 1
        key = _socket_key(current)
        if key in seen:
            continue
        seen.add(key)
        idname = str(getattr(current, "bl_idname", "") or "")
        if idname and idname != "GKSocketAny" and idname in _TYPE_TABLE:
            return idname
        nxt = _linked_socket(current)
        if nxt is not None:
            pending.append(nxt)
    return ""


def _declared_type_idname(socket):
    """节点上填的『变量名』在树里声明过类型时,返回那个类型(动画树变量)。"""
    node = getattr(socket, "node", None)
    if node is None:
        return ""
    name = str(getattr(node, "gk_var_name", "") or "").strip()
    if not name:
        return ""
    tree = getattr(node, "id_data", None)
    variables = getattr(tree, "gk_variables", None) if tree is not None else None
    if not variables:
        return ""
    for var in variables:
        if str(getattr(var, "var_name", "") or "") == name:
            return _VAR_TYPE_TO_IDNAME.get(str(getattr(var, "var_type", "")), "")
    return ""


def resolved_type_idname(socket):
    """通配引脚实际承载的 GK 引脚类型 idname;解析不出来返回空串。

    优先级:**连线对面**(上游/下游)的具体类型 > 节点声明的变量类型。
    """
    if socket is None:
        return ""
    idname = str(getattr(socket, "bl_idname", "") or "")
    if idname and idname != "GKSocketAny" and idname in _TYPE_TABLE:
        return idname
    found = _walk_type_idname(socket)
    if found:
        return found
    return _declared_type_idname(socket)


def resolved_type_label(socket):
    """通配引脚实际承载类型的**中文标签**(如『数值』);解析不出来返回空串。"""
    idname = resolved_type_idname(socket)
    return type_label(idname) if idname else ""


def resolved_color(socket):
    """通配引脚按解析出的类型取显示颜色;解析不出来用默认万能灰。"""
    return _COLOR_BY_IDNAME.get(resolved_type_idname(socket), _COLOR_ANY)


def socket_caption(socket, text):
    """引脚的显示文字:通配引脚解析出类型后补一句『值 (数值)』。

    非通配引脚原样返回 —— 它们本身的颜色/形状已经说明了类型,
    再加文字反而是噪音。
    """
    # 直接看 bl_idname,不走 gk_semantic —— 离线桩引脚没有 gk_semantic,
    # 靠它判断会在测试台里静默失效。
    if str(getattr(socket, "bl_idname", "")) != "GKSocketAny":
        return text
    label = resolved_type_label(socket)
    return f"{text} ({label})" if label else text


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
