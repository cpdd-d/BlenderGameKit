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
# 特殊引用语义(第一百八十八轮):相机引用只能接相机引用,
# 动画引用只能接动画引用,UI控件引用只能接UI控件引用。
# 这样"把动画引用接到相机引用上"会被 link_check 拒绝,
# 而不是等到生成代码时才报一个看不懂的错。
SEMANTIC_CAMERA_REF = "CAMERA_REF"
SEMANTIC_ANIM_REF = "ANIM_REF"
SEMANTIC_UI_REF = "UI_REF"
SEMANTIC_ANIM_REF = "ANIM_REF"
SEMANTIC_UI_REF = "UI_REF"

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
        """返回 True 表示允许连接。

        语义分组规则(第一百八十八轮扩展):
          * FLOW 只能接 FLOW
          * ANY 可以接任何数据引脚
          * CAMERA_REF 只能接 CAMERA_REF(或 ANY)
          * DATA 引脚按 bl_idname 判同类
        """
        src_sem = self.gk_semantic()
        dst_sem = getattr(target, "gk_semantic", lambda: SEMANTIC_DATA)()
        if src_sem == SEMANTIC_FLOW or dst_sem == SEMANTIC_FLOW:
            return src_sem == dst_sem
        if src_sem == SEMANTIC_ANY or dst_sem == SEMANTIC_ANY:
            return True
        if src_sem in (SEMANTIC_CAMERA_REF, SEMANTIC_ANIM_REF, SEMANTIC_UI_REF) or dst_sem in (SEMANTIC_CAMERA_REF, SEMANTIC_ANIM_REF, SEMANTIC_UI_REF):
            return src_sem == dst_sem
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
    "GKSocketCameraRef": ("相机引用", "Camera"),
    "GKSocketAnimRef": ("动画引用", "Animation"),
    "GKSocketUiRef": ("控件引用", "Control"),
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
# 相机引用引脚(第一百八十八轮)—— 只能接相机引用,不能接普通节点
_CAMERA_REF_PIN = ("GKSocketCameraRef", "相机引用")
_ANIM_REF_PIN = ("GKSocketAnimRef", "动画引用")
_UI_REF_PIN = ("GKSocketUiRef", "控件引用")

class GKSocketCameraRef(GKSocketBase, bpy.types.NodeSocket):
    """相机引用引脚:只能接相机引用(第一百八十八轮)。"""

    bl_idname = "GKSocketCameraRef"
    bl_label = "相机引用"

    @classmethod
    def gk_semantic(cls):
        return SEMANTIC_CAMERA_REF

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return _COLOR_SELF


class GKSocketAnimRef(GKSocketBase, bpy.types.NodeSocket):
    """动画引用引脚:只能接动画引用(第一百八十八轮)。"""

    bl_idname = "GKSocketAnimRef"
    bl_label = "动画引用"

    @classmethod
    def gk_semantic(cls):
        return SEMANTIC_ANIM_REF

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return _COLOR_SELF

class GKSocketUiRef(GKSocketBase, bpy.types.NodeSocket):
    """UI 控件引用引脚:只能接 UI 控件引用(第一百八十八轮)。"""

    bl_idname = "GKSocketUiRef"
    bl_label = "控件引用"

    @classmethod
    def gk_semantic(cls):
        return SEMANTIC_UI_REF

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return _COLOR_SELF

GK_CLASSES = (GKSocketFlow, GKSocketAny, GKSocketCameraRef, GKSocketAnimRef, GKSocketUiRef)


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
# Offline/API-unavailable fallback, sampled from Blender 4.5 native sockets.
# In Blender, registration reads the running version's own palette instead.
_COLOR_BY_IDNAME = {
    "NodeSocketFloat": (0.63, 0.63, 0.63, 1.0),
    "NodeSocketInt": (0.35, 0.55, 0.36, 1.0),
    "NodeSocketBool": (0.80, 0.65, 0.84, 1.0),
    "NodeSocketString": (0.44, 0.70, 1.0, 1.0),
    "NodeSocketVector": (0.39, 0.39, 0.78, 1.0),
    "NodeSocketVector2D": (0.39, 0.39, 0.78, 1.0),
    "NodeSocketColor": (0.78, 0.78, 0.16, 1.0),
    "NodeSocketObject": (0.93, 0.62, 0.36, 1.0),
}
_NATIVE_COLORS = {}


def on_register():
    """Sample native colors once, without touching any user graph.

    Built-in draw_color_simple is exposed on RNA socket instances, not on
    bpy.types classes. A temporary native group supplies those instances;
    drawing later only reads the small palette and never creates/scans nodes.
    """
    _NATIVE_COLORS.clear()
    groups = getattr(getattr(bpy, "data", None), "node_groups", None)
    if not callable(getattr(groups, "new", None)):
        return
    tree = None
    try:
        tree = groups.new(".GKSocketPalette", "GeometryNodeTree")
        for idname in _COLOR_BY_IDNAME:
            # Interfaces expose the base Vector type; its 2D subtype shares
            # Blender's vector palette entry.
            if idname == "NodeSocketVector2D":
                continue
            try:
                tree.interface.new_socket(name=idname, in_out="INPUT",
                                          socket_type=idname)
            except (AttributeError, RuntimeError, TypeError):
                continue
        node = tree.nodes.new("NodeGroupInput")
        for socket in node.outputs:
            if socket.bl_idname not in _COLOR_BY_IDNAME:
                continue
            simple = getattr(socket, "draw_color_simple", None)
            try:
                color = simple() if callable(simple) else socket.draw_color(bpy.context, node)
                color = tuple(float(component) for component in color)
                if len(color) == 4:
                    _NATIVE_COLORS[socket.bl_idname] = color
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue
        if "NodeSocketVector" in _NATIVE_COLORS:
            _NATIVE_COLORS["NodeSocketVector2D"] = _NATIVE_COLORS["NodeSocketVector"]
    except (AttributeError, RuntimeError, TypeError):
        # A restricted/offline environment can still draw with the fallback.
        pass
    finally:
        if tree is not None:
            groups.remove(tree)


def on_unregister():
    _NATIVE_COLORS.clear()

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


def _source_socket(socket):
    """输入引脚的来源；Blender 数据输入至多有一条连线。"""
    for link in getattr(socket, "links", ()) or ():
        other = getattr(link, "from_socket", None)
        if other is not None:
            return other
    return None


def _is_reroute(socket):
    return getattr(getattr(socket, "node", None), "bl_idname", "") == "NodeReroute"


def _concrete_data_type(socket):
    # Blender 给自定义树里的转接点默认使用 NodeSocketColor。它只是接线，
    # 不能把这个默认 RNA 类型当作传输的数据类型。
    if _is_reroute(socket):
        return ""
    idname = str(getattr(socket, "bl_idname", "") or "")
    if idname in _TYPE_TABLE and idname not in {
            "GKSocketAny", "GKSocketFlow", "NodeSocketVirtual"}:
        return idname
    return ""


def _variable_slot(socket):
    node = getattr(socket, "node", None)
    if (getattr(node, "bl_idname", "") == "GKNodeSetVar"
            and not getattr(socket, "is_output", False)):
        name = str(getattr(socket, "name", "") or "")
        if name.startswith("值 ") and name[2:].isdigit():
            return "_" + name[2:]
    return ""


def _declared_type_idname(socket):
    """逻辑/动画变量声明；多变量设置的各个值口分别对应自己的变量。"""
    node = getattr(socket, "node", None)
    if node is None:
        return ""
    name = str(getattr(node, "gk_var_name" + _variable_slot(socket), "") or "").strip()
    if not name:
        return ""
    tree = getattr(node, "id_data", None)
    for collection in ("gk_vars", "gk_variables"):
        for var in getattr(tree, collection, ()) or ():
            if str(getattr(var, "var_name", "") or "") == name:
                return _VAR_TYPE_TO_IDNAME.get(str(getattr(var, "var_type", "")), "")
    return ""


def _default_type_idname(socket):
    """仅识别未连线输入上有明确类型的属性，不猜字符串字面量。"""
    node = getattr(socket, "node", None)
    if node is None or getattr(socket, "is_output", False):
        return ""
    prop = (getattr(node, "gk_input_props", {}) or {}).get(
        getattr(socket, "name", ""))
    slot = _variable_slot(socket)
    if slot:
        prop = "gk_value" + slot
    spec = (getattr(node, "gk_prop_map", {}) or {}).get(prop)
    if not spec:
        return ""
    # Compare.gk_a 等字符串属性保存的是 GDScript 字面量："0" 不是文本值。
    return {"float": "NodeSocketFloat", "int": "NodeSocketInt",
            "bool": "NodeSocketBool", "color": "NodeSocketColor",
            "float_vector": "NodeSocketVector"}.get(spec[1], "")


def _source_type(socket, limit):
    """找真实来源，返回 (确定类型, 尚无类型的源输出)。有环/超限则放弃。"""
    seen = set()
    current = socket
    for _step in range(limit):
        if current is None:
            break
        key = _socket_key(current)
        if key in seen:
            break
        seen.add(key)
        if _is_reroute(current):
            node = current.node
            inputs = getattr(node, "inputs", ())
            outputs = getattr(node, "outputs", ())
            source = _source_socket(inputs[0]) if inputs else None
            if source is None:
                return "", outputs[0] if outputs else None
            current = source
            continue
        if not getattr(current, "is_output", False):
            source = _source_socket(current)
            if source is not None:
                current = source
                continue
        concrete = _concrete_data_type(current)
        if concrete:
            return concrete, None
        declared = _declared_type_idname(current)
        if declared:
            return declared, None
        if getattr(current, "is_output", False):
            return "", current
        return _default_type_idname(current), None
    return "", None


def _consumer_type(socket, limit):
    """来源未知时合并所有消费者；冲突或未遍历完时保持任意类型。"""
    pending = [socket]
    seen = set()
    found = set()
    steps = 0
    while pending:
        current = pending.pop()
        key = _socket_key(current)
        if key in seen:
            continue
        seen.add(key)
        steps += 1
        if steps > limit:
            return ""
        for link in getattr(current, "links", ()) or ():
            steps += 1
            if steps > limit:
                return ""
            target = getattr(link, "to_socket", None)
            if target is None:
                continue
            if _is_reroute(target):
                outputs = getattr(target.node, "outputs", ())
                if outputs:
                    pending.append(outputs[0])
                continue
            hint = _concrete_data_type(target) or _declared_type_idname(target)
            if hint:
                found.add(hint)
                if len(found) > 1:
                    return ""
    return next(iter(found), "")


def _walk_type_idname(socket, limit=64):
    """有限迭代，先找来源再查消费者；不缓存，改线/改声明立即更新。"""
    known, source = _source_type(socket, limit)
    if known or source is None:
        return known
    return _consumer_type(source, limit)


def resolved_type_idname(socket):
    """通配引脚的显示类型；真正来源优先，未知来源才合并下游类型。

    仅影响提示和颜色，不改变真实引脚类型、连线或生成语义。
    """
    if socket is None:
        return ""
    idname = str(getattr(socket, "bl_idname", "") or "")
    if (not _is_reroute(socket) and idname in _TYPE_TABLE
            and idname not in {"GKSocketAny", "NodeSocketVirtual"}):
        return idname
    return _walk_type_idname(socket)


def resolved_type_label(socket):
    """通配引脚实际承载类型的**中文标签**(如『数值』);解析不出来返回空串。"""
    idname = resolved_type_idname(socket)
    return type_label(idname) if idname else ""


def resolved_color(socket):
    """通配引脚按解析出的类型取显示颜色;解析不出来用默认万能灰。"""
    idname = resolved_type_idname(socket)
    return _NATIVE_COLORS.get(idname, _COLOR_BY_IDNAME.get(idname, _COLOR_ANY))


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
