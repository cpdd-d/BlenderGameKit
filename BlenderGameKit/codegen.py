"""节点图 -> GDScript 4 代码生成。

总体流程
--------
1. 扫描节点树，收集:事件节点、要声明的信号、要用到的成员变量、需要 @onready
   的节点引用。
2. 每个事件节点生成一个 func(准备/每帧/物理帧/输入/自定义函数)。同一类事件
   有多个节点时合并成一个 func，按"排序"属性排列。
3. 从事件节点的输出执行流开始，顺着 执行 连线逐条走语句；表达式引脚则递归
   求值成 GDScript 表达式。

关键细节
--------
* **字面量 vs 表达式**:表达式节点(常量/变量读取/数学/比较)自己实现
  ``literal()``，因为它们的值存在 Blender 属性里而不在引脚上。引脚
  ``default_value`` 只对"动作节点的输入"有意义。
* **中文标识符**:中文标签无法直接当变量名，统一走 snake_case()，冲突时加
  数字后缀。
* **求值缓存**:同一个表达式节点被多处引用时只求值一次，并抽出临时变量，
  避免 ``Input.get_vector(...)`` 之类被重复调用。
* **循环保护**:沿执行流前进时记录递归栈，遇到回环就停止并告警，不会挂死
  Blender。
"""

from . import gk_sockets as S
from .nodes import node_base as B
from .nodes.node_base import TARGET_SELF, gd_node_path, gd_string, snake_case

# 事件节点 bl_idname -> (函数名, 是否需要 delta, 排序属性)
EVENT_KINDS = {
    "GKNodeOnReady": ("_ready", False, "gk_order"),
    "GKNodeOnProcess": ("_process", True, "gk_order"),
    "GKNodeOnPhysicsProcess": ("_physics_process", True, "gk_order"),
    "GKNodeOnInput": ("_input", False, None),
}

# 事件节点特殊输出引脚对应的函数参数名
EVENT_PARAM_NAMES = {
    "GKNodeOnProcess": "delta",
    "GKNodeOnPhysicsProcess": "delta",
    "GKNodeOnInput": "event",
}

# 生成代码里用到的中文说明前缀
_BANNER = "#" * 74


class CodegenAbort(Exception):
    """导出流程中的致命错误，由 UI 捕获后显示给用户。"""


# ==========================================================================
# 字面量
# ==========================================================================
def gd_number(value):
    """数值字面量:整数保持整数，浮点强制带小数点。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return "0.0"
    if number.is_integer() and abs(number) < 1e16:
        return f"{number:.1f}"
    text = f"{number:.6f}".rstrip("0").rstrip(".")
    return text if "." in text else f"{text}.0"


def gd_vector(value, dim=3):
    """向量字面量。value 可以是元组/列表，也可以是单个数字。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        parts = [float(value), 0.0] + ([0.0] if dim == 3 else [])
    else:
        parts = [float(v) for v in list(value)[:dim]]
        while len(parts) < dim:
            parts.append(0.0)
    joined = ", ".join(gd_number(p) for p in parts)
    name = "Vector2" if dim == 2 else "Vector3"
    return f"{name}({joined})"


def gd_literal(value):
    """把 Python 值转成 GDScript 字面量表达式。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return gd_number(value)
    if isinstance(value, str):
        return gd_string(value)
    if isinstance(value, (tuple, list)):
        return gd_vector(value)
    return "null"


def _coerce_text_to_literal(text):
    """比较节点里的 A / B 是文本框，允许用户写 100 / true / "abc" """
    raw = (text or "").strip()
    if not raw:
        return "0"
    lowered = raw.lower()
    if lowered in ("true", "false"):
        return lowered
    if lowered in ("null", "nil"):
        return "null"
    try:
        return gd_number(float(raw)) if ("." in raw or "e" in lowered) else str(int(raw))
    except ValueError:
        pass
    if raw.startswith('"') or raw.startswith("'") or raw.startswith("Vector"):
        return raw
    return gd_string(raw)


def gd_class_name(text):
    """把用户填的类名转成合法 GDScript 标识符。

    GDScript 的 class_name 只接受 ASCII 标识符，中文没法用。
    转不出合法名字时返回空串(那就干脆不写 class_name)，而不是生成
    "Gknodes" 这种没意义的降级结果。
    """
    if not text:
        return ""
    # 只保留 ASCII 字母数字，然后按分隔符切分做驼峰。
    # 注意:不能走 snake_case()，那会把大写字母小写化，
    # 结果是 "PlayerController" -> "Playercontroller"(错误)。
    cleaned = []
    for ch in str(text):
        if ch.isascii() and (ch.isalnum() or ch in " _-."):
            cleaned.append(" " if ch in " _-." else ch)
    parts = [part for part in "".join(cleaned).split(" ") if part.strip()]
    if not parts:
        return ""
    joined = "".join(part[:1].upper() + part[1:] for part in parts)
    if not joined or not joined[0].isalpha():
        return ""
    return joined


# ==========================================================================
# 生成上下文
# ==========================================================================
class _Statements:
    """生成过程中收集代码行。

    用独立收集器而不是"读 ctx.lines 的某个下标区间"，因为每段(每个函数)
    生成时都会换一个新的收集器，段落之间不可能串行。
    """

    def __init__(self):
        self.lines = []

    def add(self, indent, text=""):
        self.lines.append(("    " * indent) + text if text else "")

    def extend(self, other):
        self.lines.extend(other.lines)

    def __len__(self):
        return len(self.lines)


class GenContext:
    def __init__(self, tree, options=None):
        self.tree = tree
        self.options = dict(options or {})
        self.class_name = self.options.get("class_name", "") or ""
        self.base_class = self.options.get("base_class", "CharacterBody3D")
        self.dim = 2 if str(self.options.get("dimension", "3")) == "2" else 3
        # 是否把"未连线的数值输入"暴露成 @export 参数(方便在检查器里调手感)
        self.export_tunables = bool(self.options.get("export_tunables", True))

        self.signals = []          # 要声明的 signal 名
        self.vars = {}             # 变量名 -> 类型
        self.var_order = []
        self.onready = {}          # 变量名 -> (节点访问表达式, GDScript 类型)
        self._onready_by_path = {}  # 节点访问表达式 -> 变量名(避免重复声明同一个节点)
        self.exports = []          # @export 参数行 (名字, 类型, 初值, 注释)
        self.input_actions = {}    # 常量名 -> 输入动作名
        self.warnings = []
        self.read_vars = set()     # 只读未写的变量(仍然要声明)
        self.sink = _Statements()  # 当前正在写入的收集器
        self._export_names = {}
        # 成员变量名的占用计数，用于给"不同节点但同名"的变量加序号
        self._var_names = {}
        # 成员变量 -> 拥有它的节点身份(id)，用于区分"同一节点复用"与"另一节点同名"
        self._var_owner = {}
        # 生成函数名(按钮回调等)的占用计数与归属
        self._func_names = {}
        self._func_owners = {}
        # 当前写入缩进 / 表达式处理器可用的缩进(见 add_line 与 emit_expression)
        self._current_indent = 1
        self._expr_indent = 1
        self._temp_seq = 0
        self._expr_cache = {}      # "节点::输出名" -> 表达式文本
        self._active = set()       # 沿执行流的递归栈，用于断环
        self._claimed = {}         # 节点名 -> 哪个事件函数已经"拥有"它
        self._current_event = ""   # 当前正在生成的事件标签(用于告警文案)
        self._expr_refs = {}       # "节点::输出名" -> 被引用次数(判断要不要抽临时变量)
        # 需要在 _process 里每帧执行的"后台行为"。
        # 相机震动这类效果不是一次性语句:它要跨帧衰减。
        # 我们不生成 lambda(类型推断会炸)，而是把整段代码塞进 _process，
        # 由 (变量名, 代码行列表) 描述。值为 None 表示只登记注释。
        self.process_hooks = {}    # 钩子名 -> [代码行, ...]
        # 物理帧钩子,和 process_hooks 同样的写法,但挂到 _physics_process。
        #
        # ── 为什么需要它(用户实测反馈)──────────────────────────────
        # 角色移动在 _physics_process 里(固定步长),相机跟随原来在
        # _process 里(跟渲染帧率)。**两个时钟不同步**时相机就会抖:
        # 用户把『每秒物理周期数』从 60 改成和刷新率一样,闪烁就没了 ——
        # 正是这个原因。
        #
        # 相机跟着角色走,就必须和角色在**同一个时钟**里更新,
        # 否则两个节拍一差拍,画面就开始抖。
        self.physics_hooks = {}    # 钩子名 -> [代码行, ...]
        # 只有真的用了"自动找相机"才生成 get_first_camera()，
        # 避免给所有脚本都塞一个用不到的辅助函数。
        self.need_camera_finder = False
        # 只有真的用了存档节点才生成那几个读写文件的辅助函数
        self.need_save_helpers = False
        # 只有真的用了界面节点才生成 UI 容器辅助函数
        self.need_ui_helpers = False
        # 只有真的用了手柄节点才生成摇杆死区辅助函数
        self.need_joy_helpers = False
        # 只有真的用了屏幕滤镜才生成全屏材质辅助函数
        self.need_screen_filter = False
        # 触屏拖动需要往 _input 里挂事件累积逻辑
        self.need_touch_drag = False
        self._input_handlers = []   # [(函数名, 松手是否清零)]
        # 节点图自己"生出来"的函数(比如按钮的点击回调)。
        # 它们不属于事件，需要在 generate() 里单独拼进最终脚本。
        self.extra_sections = []

    # ---- 生成段落 ----
    def new_section(self):
        """开一段新的代码块(一个函数)，返回它的收集器。"""
        self.sink = _Statements()
        return self.sink

    # ---- 语句派发 ----
    def emit_statement(self, node, indent, output="完成"):
        """把一个语句节点翻译成代码，并沿它的执行流出口继续。"""
        handler = STMT_HANDLERS.get(node.bl_idname)
        if handler is None:
            if not getattr(node, "gk_is_comment", False):
                self.warn(f"节点『{node.bl_label}』还没有实现代码生成，已跳过")
            _chain(self, node, indent, output)
            return
        handler(self, node, indent)
        _chain(self, node, indent, output)

    def emit_expression(self, socket):
        """求值一个输出引脚。

        记录当前缩进供**表达式处理器**使用:有些表达式必须先"执行"几行代码
        才能得出值(比如射线检测)。那些行必须落在使用它的那个作用域里，
        否则会被写到类体里 —— 实测就是这样炸出
        `Unexpected identifier "_query13" in class body`。
        """
        prev = self._expr_indent
        self._expr_indent = self._current_indent
        try:
            return self.expr_of(socket)
        finally:
            self._expr_indent = prev

    def emit_event_outputs(self, node, indent):
        """事件节点:沿它的"执行"输出往下走。"""
        _chain(self, node, indent, "执行")

    # ---- 记录 ----
    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    def new_temp(self, prefix="t"):
        self._temp_seq += 1
        return f"_{snake_case(prefix, 't')}{self._temp_seq}"

    def unique_name(self, base, scope="p"):
        """在导出参数命名空间里取一个不冲突的名字。

        ── 为什么以下划线开头的名字原样保留 ────────────────────────────
        snake_case 会把前导下划线吃掉(`_gk_ui_layer` -> `gk_ui_layer`)。
        生成器内部用的变量名已经保证合法，被悄悄改名就会导致
        "函数里写 `_gk_ui_layer`、成员变量却声明成 `gk_ui_layer`" —— 
        实测就是这样炸出 `Identifier "_gk_ui_layer" not declared`。
        所以内部名(前导下划线)一律不加工。
        """
        if base.startswith("_"):
            name = base
            if name not in self._export_names:
                self._export_names[name] = 1
                return name
            self._export_names[name] += 1
            return f"{name}_{self._export_names[name]}"
        name = snake_case(base, scope)
        if name not in self._export_names:
            self._export_names[name] = 1
            return name
        self._export_names[name] += 1
        return f"{name}_{self._export_names[name]}"

    def declare_var(self, name, gd_type=None, initial=None, export=False, key=None):
        """声明一个成员变量。

        ── 两种调用方式，对应两种语义 ──────────────────────────────────
        * **按名字共享**(不传 key):同一个变量名就是同一个变量。
          列表 / 字典这类节点天生如此 —— 『列表_新建』和『列表_添加』都写
          `bag`,那就是同一个背包。以前每个节点各占一个名字，结果生成出
          `bag` / `bag_2` / `bag_3` 四个互不相干的数组，逻辑完全是错的。
        * **按节点独占**(传 key):每个节点一个变量，同名的不同节点自动加序号。
          界面控件属于这种 —— 两个都叫『分数』的文本是两个不同的控件。

        名字相同但类型冲突时(比如一个节点当列表、另一个当字典)保留第一个
        类型并告警，而不是生成自相矛盾的脚本。
        """
        clean = name if name.startswith("_") else snake_case(name, "v")
        if key is None:
            if clean in self.vars:
                existing = self.vars.get(clean)
                if gd_type and existing not in (None, "Variant", gd_type):
                    self.warn(f"变量『{clean}』同时被当成 {existing} 和 {gd_type} 使用，"
                              f"已保留 {existing}。请改用不同的变量名。")
                elif gd_type and existing in (None, "Variant"):
                    self.vars[clean] = gd_type
                return clean
            self.vars[clean] = gd_type
            self._var_owner[clean] = None
            self.var_order.append(clean)
            return clean

        owner = self._var_owner.get(clean)
        if clean in self.vars and owner == key:
            # 同一个节点再次引用 —— 复用
            if gd_type and self.vars[clean] in (None, "Variant"):
                self.vars[clean] = gd_type
            return clean
        # 首次声明，或该名字已被别的节点占用 -> 加序号另开一个
        used = self._var_names.get(clean, 0)
        self._var_names[clean] = used + 1
        if used:
            clean = f"{clean}_{used + 1}"
        self.vars[clean] = gd_type
        self._var_owner[clean] = key
        self.var_order.append(clean)
        return clean

    def declare_export(self, base, gd_type, value_expr, comment=""):
        """生成一个可在检查器里调参的 @export 变量，返回变量名。

        只对"未连线的输入引脚"调用:连线了说明值由节点图算出来，不该暴露。
        """
        name = self.unique_name(base, "param")
        self.exports.append((name, gd_type, value_expr, comment))
        return name

    def var_names(self):
        """成员变量名 -> 原始名字的映射(用中文方便阅读时保留注释)。"""
        return dict(self.vars)

    def declare_func_name(self, base, key=None):
        """给"每个节点一个"的生成函数取名字，同名自动加序号。

        同一个节点重复调用会拿到同一个名字(靠 key 判断)，
        不同节点即使 base 相同也会各自拿到 `_2` / `_3`。
        """
        owner = self._func_owners.get(base)
        if owner is not None and key is not None and owner == key:
            return base
        used = self._func_names.get(base, 0)
        self._func_names[base] = used + 1
        name = base if not used else f"{base}_{used + 1}"
        self._func_owners[name] = key
        return name

    def register_input_handler(self, func_name, reset=False):
        """登记一个要挂进 _input(event) 的回调。

        触屏拖动这类输入没有"当前值"这种属性，只能靠事件累积，
        所以必须有地方接事件。这里登记后由 generate() 统一拼一个 _input。
        """
        entry = (func_name, bool(reset))
        if entry not in self._input_handlers:
            self._input_handlers.append(entry)
        return func_name

    def add_line(self, indent, text=""):
        # 记住最近一次写入的缩进:纯表达式处理器(射线检测等)需要知道
        # "现在写到第几层了"，才能把必须执行的几行插到正确的作用域里。
        self._current_indent = indent
        self.sink.add(indent, text)

    # ---- 表达式 ----
    def expr_of(self, socket):
        """把某个引脚求值成 GDScript 表达式。"""
        if socket is None:
            return "null"
        link = None
        if getattr(socket, "is_linked", False):
            link = next(iter(socket.links), None)
        if link is not None:
            # 关键:把**上游输出引脚的名字**传给表达式求值，
            # 多输出节点(向量拆分)才能定位到正确的分量
            source = getattr(link, "from_socket", None)
            source_node = link.from_node
            # 事件节点的数据输出是"直通"的:帧间隔 -> delta、输入事件 -> event
            if getattr(source_node, "gk_is_event", False) and source is not None:
                return self.socket_default(source)
            return self.node_expression(source_node, getattr(source, "name", None))
        # 未连线:直接算出字面量，不写缓存
        # (生成过程中 ctx.dim 等因素会变，缓存会串味)
        return self.socket_default(socket)

    def socket_default(self, socket):
        name = getattr(socket, "name", "")
        # 事件节点输出的特殊字面量:帧间隔 / 输入事件。
        # 必须放在 Any 判断之前，否则 帧间隔 -> "转文本" 这类
        # "Any 引脚接事件参数"的连线会被写成 null。
        if name in ("帧间隔", "输入事件"):
            owner = self._socket_source_event(socket)
            if owner is not None:
                return EVENT_PARAM_NAMES.get(owner.bl_idname, "delta")
            return "delta"
        if S.is_any(socket) or getattr(socket, "bl_idname", "") == "NodeSocketObject":
            return "null"
        gd_type = S.gdscript_type(getattr(socket, "bl_idname", ""))
        if gd_type == "String":
            return gd_string(getattr(socket, "default_value", ""))
        if gd_type in ("Vector3", "Vector2"):
            return gd_vector(getattr(socket, "default_value", (0.0, 0.0, 0.0)), self.dim)
        if gd_type == "bool":
            return "true" if getattr(socket, "default_value", False) else "false"
        return gd_number(getattr(socket, "default_value", 0.0))

    @staticmethod
    def _socket_source_event(socket):
        """引脚挂在哪个节点上(桩与 Blender 两种实现都可)。"""
        owner = getattr(socket, "node", None)
        if owner is not None:
            return owner
        return None

    def node_expression(self, node, output_name=None):
        """求一个表达式节点的输出表达式(带缓存)。

        output_name 用于多输出节点(如 向量拆分 的 X/Y/Z)，同一节点的不同
        输出会分别缓存。

        缓存键用**节点身份**而不是节点名:两个不同节点可以同名，
        用名字做键会把它们的结果串在一起。
        """
        if node is None:
            return "null"
        # 会"产生副作用"的表达式节点(射线检测要先射出去)不能缓存:
        # 缓存之后第二次求值只会复用第一次的表达式，射线就不再重新射了。
        cacheable = not getattr(type(node), "gk_no_cache", False)
        key = f"{id(node)}::{output_name or ''}"
        if cacheable:
            cached = self._expr_cache.get(key)
            if cached is not None:
                return cached

        # 多输出节点优先按输出引脚定位
        special = None
        if output_name:
            special = expression_for_output(self, node, output_name)
            if special is not None:
                if cacheable:
                    self._expr_cache[key] = special
                return special

        handler = EXPR_HANDLERS.get(node.bl_idname)
        # 先登记成占位，避免自引用导致死循环
        if cacheable:
            self._expr_cache[key] = "null"
        if handler is None:
            self.warn(f"节点『{node.bl_label}』不能作为取值使用，已按 null 处理")
            result = "null"
        else:
            try:
                result = handler(self, node)
            except Exception as exc:
                self.warn(f"节点『{node.bl_label}』求值失败: {exc}")
                result = "null"
        result = result if isinstance(result, str) else gd_literal(result)
        if cacheable:
            self._expr_cache[key] = result
        return result

    # ---- 输入动作 ----
    def input_action(self, node, prop, fallback):
        """把一个动作名转成脚本顶部常量引用(集中管理输入映射)。"""
        return self.add_action(getattr(node, prop, "") or fallback)

    def input_expr(self, node, socket_name, literal=None):
        """输入引脚的"原始值"形式。返回 (文本, 是否已是表达式)。

        未连线时优先读节点属性(gk_input_props 指定的那个)，属性不存在才
        退回引脚 default_value。比较 / 逻辑这类节点的 A、B 就是靠这里拿到
        用户在节点上填的文本框内容的。
        """
        socket = self._socket(node, socket_name)
        if socket is not None and getattr(socket, "is_linked", False):
            return self.expr_of(socket), True
        if literal is not None:
            return literal, False
        prop = node.get_input_prop(socket_name) if hasattr(node, "get_input_prop") else None
        if prop and hasattr(node, prop):
            return getattr(node, prop), False
        if socket is not None:
            return getattr(socket, "default_value", None), False
        return None, False

    def input_number(self, node, socket_name, fallback="0.0"):
        """要一个数值表达式，属性里的数字也转成合法字面量。"""
        socket = self._socket(node, socket_name)
        if socket is not None and getattr(socket, "is_linked", False):
            return self.expr_of(socket)
        value = node.input_value(self, socket_name, None)
        if isinstance(value, bool):
            return "1.0" if value else "0.0"
        if isinstance(value, (int, float)):
            return gd_number(value)
        if isinstance(value, str):
            return _coerce_text_to_literal(value)
        return fallback

    def input_string(self, node, socket_name, fallback="", literal=False):
        """要一个字符串表达式(可能是字面量，也可能来自上游节点)。

        只有"输入动作"这类属性才该转成常量引用，而它们是显式调用
        input_action()/add_action() 的。默认这里一律当普通字符串处理，
        否则 print("你好") 会被误注册成 ACTION_你好 这种非法标识符。
        """
        socket = self._socket(node, socket_name)
        if socket is not None and getattr(socket, "is_linked", False):
            return self.expr_of(socket)
        value = node.input_value(self, socket_name, None)
        if value is None:
            value = fallback
        if isinstance(value, str):
            return gd_string(value)
        return gd_literal(value)

    def input_bool_expr(self, node, socket_name, fallback="false"):
        socket = self._socket(node, socket_name)
        if socket is not None and getattr(socket, "is_linked", False):
            return self.expr_of(socket)
        value = node.input_value(self, socket_name, None)
        if isinstance(value, bool):
            return "true" if value else "false"
        if value is None:
            return fallback
        return _coerce_text_to_literal(value)

    def input_vector(self, node, socket_name, dim=None, scalar=None):
        """要一个向量表达式。

        三种情况:
          1. 引脚已连线且上游是向量 -> 直接用
          2. 引脚已连线但上游是标量 -> 包一层 Vector3(x, 0, 0)，并在告警里说明
          3. 未连线 -> 用节点上的 X/Y/Z 属性拼出来
        scalar 用于第 2 种情况的放置方式:"x" 放 X 轴，"y" 放 Y 轴。
        """
        dim = dim or self.dim
        socket = self._socket(node, socket_name)
        if socket is not None and getattr(socket, "is_linked", False):
            if self._upstream_is_scalar(socket):
                inner = self.expr_of(socket)
                zero = "0.0"
                filled = [inner, zero, zero] if scalar != "y" else [zero, inner, zero]
                self.warn(f"『{node.bl_label}』的『{socket_name}』接的是单个数值，"
                          f"已自动包装成向量")
                return f"{_gd_vector_ctor(dim)}({', '.join(filled[:dim])})"
            return self.expr_of(socket)
        parts = []
        for axis in ("x", "y", "z")[:dim]:
            value = getattr(node, f"gk_{axis}", None)
            parts.append(gd_number(value if isinstance(value, (int, float)) else 0.0))
        return f"{_gd_vector_ctor(dim)}({', '.join(parts)})"

    def _upstream_is_scalar(self, socket):
        """判断上游给的是标量还是向量。"""
        if not getattr(socket, "is_linked", False):
            return False
        link = next(iter(socket.links), None)
        if link is None:
            return False
        source = getattr(link, "from_socket", None)
        if source is None:
            return False
        gd_type = S.gdscript_type(getattr(source, "bl_idname", ""))
        return gd_type in ("float", "int", "bool")

    def add_onready(self, base, path, hint=""):
        """登记一个 @onready 节点引用，返回变量名。

        类型按路径推断:声明成 Node 却调用 AnimationPlayer 的方法会被
        GDScript 的静态类型检查拒绝，所以这里必须用具体类型。

        hint 是"调用方知道、但路径看不出来"的类型线索。典型场景:
        节点名叫『主相机』，路径是 $主相机 —— 一个相机类型的关键词都没有，
        光看路径只能猜出 Node，于是 `.make_current()` 会被静态类型拒绝。
        带上 hint='cam' 就能正确推断成 Camera2D / Camera3D。

        **同一个节点路径只声明一次**。否则同一台相机被 3 个节点引用时，
        会生成 cam / cam_2 / cam_3 三个指向同一节点的变量：
        能跑，但脚本开头一片重复，用户看不出它们其实是同一个东西。
        """
        cached = self._onready_by_path.get(path)
        if cached is not None:
            return cached
        var = self.unique_name(base, "node")
        self.onready[var] = (path, gd_node_type(path, hint=hint, dim=self.dim))
        self._onready_by_path[path] = var
        return var

    def add_action(self, action):
        """登记一个输入动作名，返回脚本顶部常量的名字。

        这样所有输入动作都集中在脚本开头，改名一眼可见，
        也不会和 Godot 内置的 ui_* 动作混淆。
        """
        name = (action or "").strip()
        if not name:
            return gd_string("")
        const = "ACTION_" + snake_case(name, "input").upper()
        if const not in self.input_actions:
            self.input_actions[const] = name
        return const

    # ---- 目标节点 ----
    def target_expr(self, node, prop="gk_target", hint="节点"):
        """节点引用:自身 -> self，其它 -> @onready 变量。"""
        raw = getattr(node, prop, TARGET_SELF) or TARGET_SELF
        if raw == TARGET_SELF:
            return "self"
        return self.add_onready(f"{hint}_{raw}", gd_node_path(raw), hint=hint)

    def sprite_target(self, node, prop="gk_target"):
        """精灵类目标:自身时用 $Sprite2D 更符合 Godot 习惯。"""
        raw = getattr(node, prop, TARGET_SELF) or TARGET_SELF
        if raw == TARGET_SELF:
            return "self"
        return self.target_expr(node, prop)

    def animation_player(self, node):
        raw = (getattr(node, "gk_player", "") or "").strip()
        if not raw or raw == TARGET_SELF:
            return self.add_onready("anim_player", "$AnimationPlayer")
        return self.target_expr(node, "gk_player", hint="anim")

    # ---- 内部 ----
    @staticmethod
    def _socket(node, socket_name):
        inputs = getattr(node, "inputs", None)
        if inputs is None:
            return None
        getter = getattr(inputs, "get", None)
        if callable(getter):
            return getter(socket_name)
        for socket in inputs:
            if getattr(socket, "name", None) == socket_name:
                return socket
        return None

    @staticmethod
    def _socket_owner_name(node, socket_name):
        """给 @export 参数起名用:节点标签 + 引脚名。"""
        label = getattr(node, "label", "") or node.bl_label
        return f"{label}_{socket_name}"

    def debug_socket(self, node, socket_name):
        """调试辅助:打印某个输入引脚的连线状态。"""
        socket = self._socket(node, socket_name)
        if socket is None:
            return f"{node.name}.{socket_name}: 引脚不存在"
        links = list(getattr(socket, "links", ()) or ())
        return (f"{node.name}.{socket_name}: is_linked={socket.is_linked} "
                f"links={len(links)} bl_idname={socket.bl_idname}")


def _gd_vector_ctor(dim):
    return "Vector2" if dim == 2 else "Vector3"


# 常见 Godot 节点名 -> 类型。@onready 声明必须用**具体类型**，
# 否则声明成 Node 却调用 has_animation()/play() 会被静态类型检查拒绝。
NODE_TYPE_HINTS = (
    ("AnimationPlayer", "AnimationPlayer"),
    ("AnimatedSprite2D", "AnimatedSprite2D"),
    ("AnimatedSprite3D", "AnimatedSprite3D"),
    ("Sprite2D", "Sprite2D"),
    ("Sprite3D", "Sprite3D"),
    ("CharacterBody2D", "CharacterBody2D"),
    ("CharacterBody3D", "CharacterBody3D"),
    ("RigidBody2D", "RigidBody2D"),
    ("RigidBody3D", "RigidBody3D"),
    ("StaticBody2D", "StaticBody2D"),
    ("StaticBody3D", "StaticBody3D"),
    ("Area2D", "Area2D"),
    ("Area3D", "Area3D"),
    ("CollisionShape2D", "CollisionShape2D"),
    ("CollisionShape3D", "CollisionShape3D"),
    ("Timer", "Timer"),
    ("AudioStreamPlayer2D", "AudioStreamPlayer2D"),
    ("AudioStreamPlayer3D", "AudioStreamPlayer3D"),
    ("AudioStreamPlayer", "AudioStreamPlayer"),
    ("Camera2D", "Camera2D"),
    ("Camera3D", "Camera3D"),
    ("Label", "Label"),
    ("Button", "Button"),
    ("ProgressBar", "ProgressBar"),
    ("Node2D", "Node2D"),
    ("Node3D", "Node3D"),
    ("Control", "Control"),
    ("CanvasLayer", "CanvasLayer"),
    ("Marker2D", "Marker2D"),
    ("Marker3D", "Marker3D"),
)


def gd_node_type(raw, hint="", dim=3):
    """按节点路径猜 Godot 类型，猜不到就退回 Node。

    hint 是调用方给的额外线索。相机节点最常踩这个坑:节点叫『主相机』，
    路径里一个类型关键词都没有，只能猜出 Node，于是 make_current() 会被
    GDScript 的静态类型检查拒绝。所以相机类节点允许"猜不出来时用 hint 兜底"，
    并按项目维度选 Camera2D / Camera3D —— 选错了类型一样会编译失败。
    """
    text = (raw or "").strip().rstrip("/")
    leaf = text.rsplit("/", 1)[-1] if text else ""
    if leaf.startswith("@"):
        leaf = leaf[1:]
    for keyword, gd_type in NODE_TYPE_HINTS:
        if leaf == keyword or leaf.endswith(keyword):
            return gd_type
    if hint == "cam":
        return "Camera2D" if dim == 2 else "Camera3D"
    return "Node"


# ==========================================================================
# 表达式节点处理器
# ==========================================================================
EXPR_HANDLERS = {}


def expr_node(*bl_idnames):
    def wrapper(func):
        for bl_idname in bl_idnames:
            EXPR_HANDLERS[bl_idname] = func
        return func
    return wrapper


@expr_node("GKNodeConstFloat")
def _expr_const_float(ctx, node):
    return gd_number(node.input_value(ctx, None, getattr(node, "gk_value", 0.0)))


@expr_node("GKNodeConstText")
def _expr_const_text(ctx, node):
    return gd_string(getattr(node, "gk_value", ""))


@expr_node("GKNodeConstBool")
def _expr_const_bool(ctx, node):
    return "true" if getattr(node, "gk_value", False) else "false"


@expr_node("GKNodeConstVector")
def _expr_const_vector(ctx, node):
    dim = 2 if getattr(node, "gk_dim", "V3") == "V2" else ctx.dim
    values = [getattr(node, f"gk_{axis}", 0.0) for axis in ("x", "y", "z")]
    return gd_vector(tuple(values), dim)


@expr_node("GKNodeMath")
def _expr_math(ctx, node):
    op = getattr(node, "gk_op", "ADD")
    def arg(name):
        return ctx.input_number(node, name, "0.0")
    if op == "ADD":
        return f"({arg('A')} + {arg('B')})"
    if op == "SUB":
        return f"({arg('A')} - {arg('B')})"
    if op == "MUL":
        return f"({arg('A')} * {arg('B')})"
    if op == "DIV":
        return f"({arg('A')} / {arg('B')})"
    if op == "MOD":
        return f"fmod({arg('A')}, {arg('B')})"
    if op == "POW":
        return f"pow({arg('A')}, {arg('B')})"
    if op == "MIN":
        return f"minf({arg('A')}, {arg('B')})"
    if op == "MAX":
        return f"maxf({arg('A')}, {arg('B')})"
    if op == "CLAMP":
        return f"clampf({arg('A')}, {arg('B')}, {arg('C')})"
    if op == "LERP":
        return f"lerpf({arg('A')}, {arg('B')}, {arg('C')})"
    if op == "ABS":
        return f"absf({arg('A')})"
    if op == "SQRT":
        return f"sqrt({arg('A')})"
    if op == "SIN":
        return f"sin({arg('A')})"
    if op == "COS":
        return f"cos({arg('A')})"
    if op == "FLOOR":
        return f"floorf({arg('A')})"
    if op == "CEIL":
        return f"ceilf({arg('A')})"
    if op == "ROUND":
        return f"roundf({arg('A')})"
    if op == "SIGN":
        return f"signf({arg('A')})"
    if op == "RANDOM":
        return "randf()"
    if op == "DEG2RAD":
        return f"deg_to_rad({arg('A')})"
    if op == "RAD2DEG":
        return f"rad_to_deg({arg('A')})"
    ctx.warn(f"未知的数学运算: {op}")
    return "0.0"


_COMPARE_SYMBOL = {"EQ": "==", "NE": "!=", "GT": ">", "GE": ">=", "LT": "<", "LE": "<="}


@expr_node("GKNodeCompare")
def _expr_compare(ctx, node):
    symbol = _COMPARE_SYMBOL.get(getattr(node, "gk_op", "EQ"), "==")
    left, left_is_expr = ctx.input_expr(node, "A")
    right, right_is_expr = ctx.input_expr(node, "B")
    if not left_is_expr:
        left = _coerce_text_to_literal(left)
    if not right_is_expr:
        right = _coerce_text_to_literal(right)
    return f"({left} {symbol} {right})"


@expr_node("GKNodeLogic")
def _expr_logic(ctx, node):
    op = getattr(node, "gk_op", "AND")
    left = ctx.input_bool_expr(node, "A")
    right = ctx.input_bool_expr(node, "B")
    if op == "AND":
        return f"({left} and {right})"
    if op == "OR":
        return f"({left} or {right})"
    return f"(not {left})"


@expr_node("GKNodeRandomRange")
def _expr_random_range(ctx, node):
    return f"randi_range({ctx.input_number(node, '最小', '0')}, {ctx.input_number(node, '最大', '10')})"


@expr_node("GKNodeVectorMake")
def _expr_vector_make(ctx, node):
    dim = 2 if getattr(node, "gk_dim", "V3") == "V2" else ctx.dim
    parts = [ctx.input_number(node, axis, "0.0") for axis in ("X", "Y", "Z")[:dim]]
    return f"{_gd_vector_ctor(dim)}({', '.join(parts)})"


@expr_node("GKNodeVectorSplit")
def _expr_vector_split(ctx, node):
    # 拆分节点有三个输出，单独由 _expr_of_output 处理
    return "null"


@expr_node("GKNodeVectorLength")
def _expr_vector_length(ctx, node):
    return f"({ctx.input_vector(node, '向量')}).length()"


@expr_node("GKNodeVectorNormalize")
def _expr_vector_normalize(ctx, node):
    return f"({ctx.input_vector(node, '向量')}).normalized()"


@expr_node("GKNodeConvert")
def _expr_convert(ctx, node):
    socket = ctx._socket(node, "输入")
    inner, is_expr = ctx.input_expr(node, "输入")
    if not is_expr:
        inner = _coerce_text_to_literal(inner)
    target = getattr(node, "gk_to", "float")
    if target == "float":
        return f"float({inner})"
    if target == "int":
        return f"int({inner})"
    if target == "bool":
        return f"bool({inner})"
    if target == "String":
        return f"str({inner})"
    if target == "Vector2":
        return f"Vector2({inner})"
    if target == "Vector3":
        return f"Vector3({inner})"
    return inner


@expr_node("GKNodeGetVar")
def _expr_get_var(ctx, node):
    name = ctx.declare_var(getattr(node, "gk_var_name", "value"))
    ctx.read_vars.add(name)
    return name


@expr_node("GKNodeGetNode")
def _expr_get_node(ctx, node):
    raw = getattr(node, "gk_path", TARGET_SELF) or TARGET_SELF
    if raw == TARGET_SELF:
        return "self"
    return ctx.add_onready(f"node_{raw}", gd_node_path(raw))


@expr_node("GKNodeIsOnFloor")
def _expr_is_on_floor(ctx, node):
    return f"{ctx.target_expr(node)}.is_on_floor()"


@expr_node("GKNodeActionPressed")
def _expr_action_pressed(ctx, node):
    return f"Input.is_action_pressed({ctx.input_action(node, 'gk_action', 'jump')})"


@expr_node("GKNodeActionJustPressed")
def _expr_action_just_pressed(ctx, node):
    return f"Input.is_action_just_pressed({ctx.input_action(node, 'gk_action', 'jump')})"


@expr_node("GKNodeGetAxis")
def _expr_get_axis(ctx, node):
    # 兜底同 _expr_get_vector:用 move_* 才会自动绑上键
    negative = ctx.input_action(node, "gk_negative", "move_left")
    positive = ctx.input_action(node, "gk_positive", "move_right")
    # 注意:Godot 的 Input.get_axis 不接受死区参数，死区只在 get_vector 里有效
    return f"Input.get_axis({negative}, {positive})"


@expr_node("GKNodeGetVector")
def _expr_get_vector(ctx, node):
    deadzone = gd_number(getattr(node, "gk_deadzone", 0.2))
    # ── 兜底动作名用 "move_*" 而不是 Godot 内置的 "ui_*"(踩过的坑)────
    # `ui_left/right/up/down` 是 Godot 的**全局界面动作**,绑的是**方向键**,
    # 不是 WASD。用户拿默认设置跑起来按 WASD 完全没反应,按方向键才行 ——
    # 而"按 WASD 走路"是所有人的第一直觉,他只会得出"输入是坏的"这个结论。
    #
    # 用 "move_*" 的好处:导出时会连按键一起写进 project.godot
    # (见 project.set_input_actions / DEFAULT_KEYS),WASD 自动就通了。
    left = ctx.input_action(node, "gk_left", "move_left")
    right = ctx.input_action(node, "gk_right", "move_right")
    up = ctx.input_action(node, "gk_up", "move_up")
    down = ctx.input_action(node, "gk_down", "move_down")
    base = f"Input.get_vector({left}, {right}, {up}, {down}, {deadzone})"
    if ctx.dim == 2:
        return base
    # ── 3D 项目的方向必须包成 Vector3 ────────────────────────────────
    # `Input.get_vector` 返回的是 **Vector2**。3D 项目里直接拿它去写
    # `velocity.z` 会让 Godot 报
    #     Cannot find member "z" in base "Vector2"
    # 于是整个脚本编译不过(实测)。平面移动的意图在 3D 里就是 XZ 平面:
    # get_vector 的 x 对应世界 X,y 对应世界 Z。
    #
    # 先存进一个临时变量再包 Vector3:直接内联会把同一个
    # `Input.get_vector(...)` 写两遍，生成的代码又长又难读。
    temp = ctx.new_temp("move")
    ctx.add_line(ctx._expr_indent, f"var {temp} := {base}")
    return f"Vector3({temp}.x, 0.0, {temp}.y)"


# 多输出表达式的节点:按输出引脚分别求值
MULTI_OUTPUT = {
    "GKNodeVectorSplit": {"X": ".x", "Y": ".y", "Z": ".z"},
}


def expression_for_output(ctx, node, output_name):
    """带多输出的表达式节点(向量拆分)。"""
    mapping = MULTI_OUTPUT.get(node.bl_idname)
    if not mapping or output_name not in mapping:
        return None
    base = ctx.input_vector(node, "向量")
    return f"({base}){mapping[output_name]}"


# ==========================================================================
# 语句节点处理器
# ==========================================================================
STMT_HANDLERS = {}


def stmt_node(*bl_idnames):
    def wrapper(func):
        for bl_idname in bl_idnames:
            STMT_HANDLERS[bl_idname] = func
        return func
    return wrapper


def _emit_body(ctx, node, indent, output="完成"):
    """沿某个输出执行流继续走。"""
    if node is None:
        return
    # ── 断环与归属判断用**节点对象本身**当键，不能用节点名，也不能用 id() ──
    # 1) 不能用节点名:两个**不同**的节点可以同名(复制节点后懒得改名很常见)。
    #    用名字判断时第二个同名节点会被当成"又回到了同一节点"，
    #    被误判成执行流回路直接跳过 —— 实测表现为"血条节点整个没生成"。
    # 2) 不能用 id():id 会在对象被回收后复用。一次导出里前后创建的两批节点
    #    可能拿到同一个 id，于是新节点被误判成"已被别的节点占用"，
    #    冒出一条莫名其妙的"同时被两个事件引用"告警(实测)。
    #    用对象当键还能顺带把它们**保持存活**，id 复用问题自然消失。
    if node in ctx._active:
        ctx.warn(f"检测到执行流回路，已在『{node.bl_label}』处断开(游戏里会无限循环)")
        return
    # Blender 的输入引脚只允许一条连线，所以同一个节点不可能真的被两个事件
    # 驱动。这里以"先到先得"为准并明确告警，而不是默默丢掉一段逻辑。
    owner = ctx._claimed.get(node)
    if owner is not None and owner != ctx._current_event:
        ctx.warn(
            f"节点『{node.bl_label}』同时被『{owner}』与『{ctx._current_event}』引用，"
            f"但一个输入引脚只能接一条线。已只保留『{owner}』这一条;"
            f"如果两个事件都要执行这段逻辑，请复制一份节点。"
        )
        return
    ctx._claimed[node] = ctx._current_event
    ctx._active.add(node)
    try:
        ctx.emit_statement(node, indent, output)
    finally:
        ctx._active.discard(node)


def _next_nodes(node, output="完成"):
    """取某个输出引脚连到的所有下游节点(按 x 排序，保证结果稳定)。"""
    outputs = getattr(node, "outputs", None)
    if outputs is None:
        return []
    socket = None
    getter = getattr(outputs, "get", None)
    if callable(getter):
        socket = getter(output)
    if socket is None:
        for candidate in outputs:
            if getattr(candidate, "name", None) == output:
                socket = candidate
                break
    if socket is None:
        return []
    result = []
    for link in getattr(socket, "links", ()):  # 离线桩里 links 是列表
        target = getattr(link, "to_node", None)
        if target is not None:
            result.append(target)
    result.sort(key=lambda item: (getattr(item, "location", (0.0, 0.0))[1],
                                 getattr(item, "location", (0.0, 0.0))[0]))
    return result


def _chain(ctx, node, indent, output="完成"):
    """node 的某个输出接到的东西全部按顺序执行。"""
    for target in _next_nodes(node, output):
        _emit_body(ctx, target, indent)


def _vector_ctor(dim):
    return "Vector2" if dim == 2 else "Vector3"


def _tunable_number(ctx, node, socket_name, fallback, base_name,
                    clamp_max=None, clamp_hint=""):
    """未连线的数值输入 -> 生成 @export 参数，可在 Godot 检查器里直接调。

    连线了就说明这个值由图算出来，不该暴露成外部参数。

    clamp_max: 超过这个值就夹住，并给一条告警。
    用于"单位在不同维度下含义不同"的参数 —— 见 _stmt_move_direction。
    """
    if ctx.export_tunables:
        socket = ctx._socket(node, socket_name)
        if socket is None or not getattr(socket, "is_linked", False):
            literal = ctx.input_number(node, socket_name, fallback)
            if clamp_max is not None:
                try:
                    value = float(literal)
                except (TypeError, ValueError):
                    value = None
                if value is not None and value > clamp_max:
                    label = getattr(node, "label", "") or node.bl_label
                    ctx.warn(
                        f"『{label}』的『{socket_name}』是 {value:g},"
                        f"已按 {clamp_max:g} 生成。{clamp_hint}")
                    literal = f"{clamp_max:g}"
            label = getattr(node, "label", "") or node.bl_label
            return ctx.declare_export(base_name, "float", literal, f"{label} · {socket_name}")
    return ctx.input_number(node, socket_name, fallback)


def _tunable_vector(ctx, node, socket_name, dim, base_name):
    """未连线的向量输入 -> 生成 @export 参数。"""
    if ctx.export_tunables:
        socket = ctx._socket(node, socket_name)
        if socket is None or not getattr(socket, "is_linked", False):
            literal = ctx.input_vector(node, socket_name, dim=dim)
            label = getattr(node, "label", "") or node.bl_label
            return ctx.declare_export(base_name, _vector_ctor(dim), literal,
                                      f"{label} · {socket_name}")
    return ctx.input_vector(node, socket_name, dim=dim)


@stmt_node("GKNodePrint")
def _stmt_print(ctx, node, indent):
    ctx.add_line(indent, f"print({ctx.input_string(node, '内容', '')})")


@stmt_node("GKNodeSetVelocity")
def _stmt_set_velocity(ctx, node, indent):
    target = ctx.target_expr(node)
    velocity = _tunable_vector(ctx, node, "速度", ctx.dim, "start_velocity")
    ctx.add_line(indent, f"{target}.velocity = {velocity}")


@stmt_node("GKNodeMoveAndSlide")
def _stmt_move_and_slide(ctx, node, indent):
    ctx.add_line(indent, f"{ctx.target_expr(node)}.move_and_slide()")


@stmt_node("GKNodeMoveDirection")
def _stmt_move_direction(ctx, node, indent):
    target = ctx.target_expr(node)
    # 方向先归一化:否则"手柄斜推"会比"直推"快 1.41 倍(独立游戏常见 bug)
    direction = ctx.input_vector(node, "方向")
    # ── 默认速度必须按维度给 ────────────────────────────────────────
    # 节点属性上写的 300 是给 **2D** 的(像素/秒),在 2D 里是正常手感。
    # 但 3D 的世界单位是**米**:300 米/秒 = 一帧跑 5 米 ——
    # 实测:角色 1.25 秒冲出 125 米,画面里瞬间消失,
    # 用户看到的是"按键完全没反应"(其实动得飞快,早出画面了)。
    # 所以 3D 下超过 50 就夹到 6 米/秒(正常走路速度),并告警说明。
    if ctx.dim == 2:
        default_speed = "300.0"
        speed = _tunable_number(ctx, node, "速度", default_speed, "move_speed")
    else:
        default_speed = "6.0"
        speed = _tunable_number(
            ctx, node, "速度", default_speed, "move_speed",
            clamp_max=50.0,
            clamp_hint=("『速度』在 2D 里单位是像素/秒,在 3D 里是**米/秒**,"
                        "同一个数值在 3D 里会快几百倍(角色一眨眼就飞出画面)。"
                        "3D 一般用 3~10。"))
    temp = ctx.new_temp("dir")
    ctx.add_line(indent, f"var {temp} := ({direction}).normalized()")

    # ── 输入往哪个世界轴走(用户要求:像 UPBGE 那样能设)──────────────
    # 默认:横移走 X,前进走 Z 的**负向**(Godot / glTF 的"前"就是 -Z)。
    # 实测踩过:相机朝向一变,"按前往后、按左往右"完全可能 ——
    # 这不该逼用户去改脚本,给两个选项翻过来就行。
    # 2D 只有 X 轴,这两项直接忽略。
    if ctx.dim == 2:
        axis_h, axis_v = "x", None
        horiz_expr, vert_expr = f"{temp}.x", f"{temp}.y"
    else:
        axis_h = str(getattr(node, "gk_axis_h", "X") or "X").lower()
        axis_v = str(getattr(node, "gk_axis_v", "Z") or "Z").lower()
        negate = str(getattr(node, "gk_forward_sign", "NEG")) == "NEG"
        horiz_expr = f"{temp}.{axis_h}"
        vert_expr = (f"(-{temp}.{axis_v})" if negate else f"{temp}.{axis_v}")

    if getattr(node, "gk_keep_y", False):
        # ── 有重力时必须做两件事 ────────────────────────────────────
        # 1) Godot 4 的 CharacterBody2D/3D **不会自动施加重力** ——
        #    它只是"按你设的 velocity 移动"。不自己累加重力，
        #    角色就永远浮在原地(实测:velocity 恒为 (0,0)、
        #    is_on_floor() 恒为 false，画面看起来就像"没反应")。
        # 2) 所以只能写水平分量，纵向留给重力累加。
        #    `velocity = dir * speed` 会把 velocity.y 一起清成 0，
        #    重力刚加上去就被抹掉。
        ctx.add_line(indent, f"{target}.velocity += {target}.get_gravity() * "
                             f"get_physics_process_delta_time()")
        if axis_v is None:
            ctx.add_line(indent, f"{target}.velocity.x = {horiz_expr} * {speed}")
        else:
            # 3D:按用户选的轴写。**Y 轴永远留给重力** ——
            # 用户真把某个轴设成 Y 时也不动它,否则 gravity 刚加上就被抹掉
            # (那正是"重力模式"要解决的问题)。
            written = set()
            for axis, expr in ((axis_h, horiz_expr), (axis_v, vert_expr)):
                if axis == "y":
                    continue
                op = "=" if axis not in written else "+="
                ctx.add_line(indent,
                             f"{target}.velocity.{axis} {op} {expr} * {speed}")
                written.add(axis)
            if not written:
                # 两个轴都选了 Y —— 退回默认的 X,至少能动
                ctx.add_line(indent, f"{target}.velocity.x = {horiz_expr} * {speed}")
    else:
        ctx.add_line(indent, f"{target}.velocity = {temp} * {speed}")
    ctx.add_line(indent, f"{target}.move_and_slide()")


@stmt_node("GKNodeJump")
def _stmt_jump(ctx, node, indent):
    target = ctx.target_expr(node)
    force = _tunable_number(ctx, node, "强度", "400.0", "jump_force")
    ctx.add_line(indent, f"{target}.velocity.y = -{force}")


@stmt_node("GKNodeAddImpulse")
def _stmt_add_impulse(ctx, node, indent):
    target = ctx.target_expr(node)
    impulse = ctx.input_vector(node, "冲量")
    ctx.add_line(indent, f"{target}.apply_impulse({impulse})")


@stmt_node("GKNodeSetRotation")
def _stmt_set_rotation(ctx, node, indent):
    target = ctx.target_expr(node)
    angle = ctx.input_number(node, "角度", "0.0")
    axis = getattr(node, "gk_axis", "Y")
    degrees = bool(getattr(node, "gk_degrees", True))
    if ctx.dim == 2:
        ctx.add_line(indent, f"{target}.rotation = deg_to_rad({angle})")
    else:
        prop = "rotation_degrees" if degrees else "rotation"
        value = angle if degrees else f"deg_to_rad({angle})"
        ctx.add_line(indent, f"{target}.{prop}.{axis.lower()} = {value}")


@stmt_node("GKNodeFlip")
def _stmt_flip(ctx, node, indent):
    target = ctx.target_expr(node)
    socket = ctx._socket(node, "朝左")
    if socket is not None and getattr(socket, "is_linked", False):
        left = ctx.expr_of(socket)
    else:
        mode = getattr(node, "gk_mode", "AUTO")
        if mode == "LEFT":
            left = "true"
        elif mode == "RIGHT":
            left = "false"
        else:
            # 自动:按当前朝速度方向翻转(横版二次元角色最常用的行为)
            left = f"{target}.velocity.x < 0.0"
    ctx.add_line(indent, f"if {left}:")
    ctx.add_line(indent + 1, f"{target}.scale.x = -abs({target}.scale.x)")
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f"{target}.scale.x = abs({target}.scale.x)")


@stmt_node("GKNodePlayAnim")
def _stmt_play_anim(ctx, node, indent):
    player = ctx.animation_player(node)
    anim = ctx.input_string(node, "动画名", "idle")
    speed = gd_number(getattr(node, "gk_speed", 1.0))
    name_temp = ctx.new_temp("anim")
    ctx.add_line(indent, f"var {name_temp} := {anim}")
    ctx.add_line(indent, f"if {player}.has_animation({name_temp}):")
    ctx.add_line(indent + 1, f"{player}.play({name_temp}, -1.0, {speed})")
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f'push_warning("动画不存在: %s" % {name_temp})')


@stmt_node("GKNodeSetState")
def _stmt_set_state(ctx, node, indent):
    var_name = ctx.declare_var(getattr(node, "gk_var", "state"), "String")
    state = ctx.input_string(node, "状态", "idle")
    ctx.add_line(indent, f"{var_name} = {state}")


@stmt_node("GKNodeSpawn")
def _stmt_spawn(ctx, node, indent):
    path = ctx.input_string(node, "场景路径", "res://scenes/Bullet.tscn")
    temp = ctx.new_temp("scene")
    inst = ctx.new_temp("inst")
    # 注意:整段都保持在同一个缩进层级，"实例"输出才能在后续语句里被引用
    ctx.add_line(indent, f"var {temp} := load({path}) as PackedScene")
    ctx.add_line(indent, f"if {temp} == null:")
    ctx.add_line(indent + 1, f'push_error("场景加载失败: %s" % {path})')
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f"var {inst} := {temp}.instantiate()")
    position = ctx.input_vector(node, "位置", dim=ctx.dim)
    ctx.add_line(indent + 1, f"if {inst} is Node3D or {inst} is Node2D:")
    ctx.add_line(indent + 2, f"{inst}.position = {position}")
    parent = (getattr(node, "gk_parent", "") or "").strip()
    if parent and parent != TARGET_SELF:
        parent_expr = ctx.target_expr(node, "gk_parent", hint="parent")
        ctx.add_line(indent + 1, f"{parent_expr}.add_child({inst})")
    else:
        ctx.add_line(indent + 1, f"get_tree().current_scene.add_child({inst})")


# ==========================================================================
# 显示 / 相机 / 时间
# ==========================================================================
# 只有这些基类带 `visible` 属性。往 Node 上写 `visible` 会让 GDScript
# 直接报 "Identifier not declared"，所以必须提前拦下来给告警。
_VISIBLE_BASES = {
    "Node2D", "Node3D", "Control", "CanvasItem",
    "Sprite2D", "Sprite3D", "AnimatedSprite2D", "AnimatedSprite3D",
    "CharacterBody2D", "CharacterBody3D", "RigidBody2D", "RigidBody3D",
    "StaticBody2D", "StaticBody3D", "Area2D", "Area3D",
    "Label", "Button", "ProgressBar", "TextureRect", "NinePatchRect",
    "TileMap", "TileMapLayer", "CollisionShape2D", "CollisionShape3D",
}


def _base_supports_visible(base_class):
    text = str(base_class or "")
    return text in _VISIBLE_BASES or text.endswith(("2D", "3D"))


def _camera_expr(ctx, node, auto="first"):
    """求值相机目标。

    三种情况:
      * 图里指定了某个节点 -> 用它的 @onready 变量
      * 图里两个相机节点 -> 按路径引用
      * 都没填 -> 不报警、不生成 @onready，而是在运行时**自动找第一个相机**。
        对新手来说"我什么都没填但震动生效了"比"必须先在面板里选相机"友好得多。
    """
    raw = (getattr(node, "gk_target", "") or "").strip()
    if raw and raw != TARGET_SELF:
        return ctx.target_expr(node, hint="cam"), True
    if auto == "first":
        ctx.warn(f"『{node.bl_label}』没有指定相机，"
                 f"已改成运行时自动找场景里第一个相机(Camera2D / Camera3D)")
        return 'get_tree().get_first_node_in_group("gk_camera")', False
    return "null", False


@stmt_node("GKNodeSetVisible")
def _stmt_set_visible(ctx, node, indent):
    target = ctx.target_expr(node)
    if target == "self" and not _base_supports_visible(ctx.base_class):
        ctx.warn(f"『{node.bl_label}』的目标是自身，但脚本基类 {ctx.base_class} "
                 f"没有 visible 属性(Godot 会报错)，已跳过这一句")
        return
    socket = ctx._socket(node, "可见")
    if socket is not None and getattr(socket, "is_linked", False):
        value = ctx.expr_of(socket)
    else:
        value = "true" if getattr(node, "gk_visible", True) else "false"
    ctx.add_line(indent, f"{target}.visible = {value}")


@stmt_node("GKNodeSetPaused")
def _stmt_set_paused(ctx, node, indent):
    mode = "PROCESS_MODE_DISABLED" if getattr(node, "gk_paused", True) else "PROCESS_MODE_INHERIT"
    ctx.add_line(indent, f"{ctx.target_expr(node)}.process_mode = Node.{mode}")


@stmt_node("GKNodeCameraSetCurrent")
def _stmt_camera_set_current(ctx, node, indent):
    camera, _auto = _camera_expr(ctx, node)
    ctx.add_line(indent, f"{camera}.make_current()")


@stmt_node("GKNodeCameraZoom")
def _stmt_camera_zoom(ctx, node, indent):
    camera, _auto = _camera_expr(ctx, node)
    value = _tunable_number(ctx, node, "数值", "1.0", "camera_zoom")
    temp = ctx.new_temp("cam")
    # 2D 的 zoom 是"越大越近"的倍数，3D 的 fov 是垂直视场角(度)。
    # 两者语义不同，所以按相机真实类型分派，而不是按项目维度猜。
    ctx.add_line(indent, f"var {temp} = {camera}")
    ctx.add_line(indent, f"if {temp} is Camera2D:")
    ctx.add_line(indent + 1, f"{temp}.zoom = Vector2({value}, {value})")
    ctx.add_line(indent, f"elif {temp} is Camera3D:")
    ctx.add_line(indent + 1, f"{temp}.fov = {value}")


@stmt_node("GKNodeCameraShake")
def _stmt_camera_shake(ctx, node, indent):
    """相机震动。

    ── 为什么不是"一次性语句" ──────────────────────────────────────
    震动要跨帧衰减，所以真正的每帧逻辑必须挂在 _process 里。
    做法:这里只负责"装上"(记下相机 + 重置计时器)，
    衰减逻辑登记进 ctx.process_hooks，由 generate() 保证 _process 存在。
    """
    camera, is_specific = _camera_expr(ctx, node)
    strength = gd_number(getattr(node, "gk_strength", 8.0))
    duration = gd_number(getattr(node, "gk_duration", 0.25))

    cam_var = ctx.declare_var("_gk_shake_cam", "Node")
    time_var = ctx.declare_var("_gk_shake_left", "float")
    power_var = ctx.declare_var("_gk_shake_power", "float")
    total_var = ctx.declare_var("_gk_shake_total", "float")

    if is_specific:
        ctx.add_line(indent, f"{cam_var} = {camera}")
    else:
        # 优先用放进 "gk_camera" 组的相机(用户显式指定)，
        # 没有就退回到场景里第一个摄像机(新手不填也能用)。
        ctx.add_line(indent, f'{cam_var} = get_tree().get_first_node_in_group("gk_camera")')
        ctx.add_line(indent, f"if {cam_var} == null:")
        ctx.add_line(indent + 1, f"{cam_var} = _gk_find_camera(self)")

    ctx.add_line(indent, f"{power_var} = {strength}")
    ctx.add_line(indent, f"{total_var} = maxf({duration}, 0.0001)")
    ctx.add_line(indent, f"{time_var} = {total_var}")

    # ---- 每帧衰减逻辑 ----
    hook_key = "camera_shake"
    if hook_key not in ctx.process_hooks:
        lines = [
            f"if {time_var} > 0.0:",
            f"    {time_var} -= delta",
            f"    var _decay: float = {time_var} / {total_var}",
            f"    var _ox: float = randf_range(-{power_var}, {power_var}) * _decay",
            f"    var _oy: float = randf_range(-{power_var}, {power_var}) * _decay",
            f"    if {cam_var} is Camera2D:",
            f"        ({cam_var} as Camera2D).offset = Vector2(_ox, _oy)",
            f"    elif {cam_var} is Camera3D:",
            f"        ({cam_var} as Camera3D).h_offset = _ox",
            f"        ({cam_var} as Camera3D).v_offset = _oy",
            f"elif {cam_var} != null:",
            "    # 震动结束:偏移归零，否则相机会一直歪着",
            f"    if {cam_var} is Camera2D:",
            f"        ({cam_var} as Camera2D).offset = Vector2.ZERO",
            f"    elif {cam_var} is Camera3D:",
            f"        ({cam_var} as Camera3D).h_offset = 0.0",
            f"        ({cam_var} as Camera3D).v_offset = 0.0",
        ]
        ctx.process_hooks[hook_key] = lines

    # 自动找相机需要一个小工具函数
    ctx.need_camera_finder = True


@stmt_node("GKNodeCameraFollow")
def _stmt_camera_follow(ctx, node, indent):
    """相机跟随。

    ── 为什么这件事必须由生成器负责 ────────────────────────────────
    Godot 的 Camera2D / Camera3D 本身**不跟随**任何东西:它站在哪就拍哪。
    平台跳跃游戏不放跟随，角色一跑远就出画面 —— 这是新手最常见的
    "我角色不见了"来源。做成一个节点，而不是让人自己写脚本。
    """
    raw_cam = (getattr(node, "gk_camera", "") or "").strip()
    # ── 相机引用不能生成 `$名字`(踩过的坑)─────────────────────────
    # 以前填了相机名就生成 `@onready var cam_x: Camera3D = $MyCam` ——
    # 那是个**相对本节点的路径**。名字对不上时 Godot 加载脚本直接失败,
    # 报 "Node not found: MyCam (relative to /root/Cube)",
    # 整个脚本连 _process 都跑不起来(用户实测正是这个现象)。
    #
    # 而节点引用下拉里列的是**节点图里的节点**,场景里几乎不可能有同名
    # 物体 —— 用户"怎么选都没影响"的观感就是这么来的。
    #
    # 改成按**名字在场景里搜**,搜不到就退回"场景里第一个相机":
    # 填错只是退回自动模式,不会把脚本搞崩。
    if raw_cam and raw_cam != TARGET_SELF:
        camera = f"_gk_find_camera_named(self, {gd_string(raw_cam)})"
    else:
        # 交给一个纯查询函数:这个节点每帧都在跑，而相机可能还没进场景。
        # 用函数而不是内联代码，是为了把 null 情况收在一处处理。
        camera = "_gk_find_camera(self)"
    ctx.need_camera_finder = True

    raw_target = (getattr(node, "gk_target", TARGET_SELF) or TARGET_SELF).strip()
    target = "self" if raw_target == TARGET_SELF else ctx.target_expr(
        node, "gk_target", hint="node")

    smooth = float(getattr(node, "gk_smooth", 0.12) or 0.0)
    offset_x = gd_number(getattr(node, "gk_offset_x", 0.0))
    offset_y = gd_number(getattr(node, "gk_offset_y", 0.0))
    vec_ctor = "Vector2" if ctx.dim == 2 else "Vector3"

    cam_var = ctx.declare_var("_gk_follow_cam", "Node")
    look_var = ctx.declare_var("_gk_follow_look", "Node")

    if ctx.dim == 2:
        # 2D:相机就在同一平面上，直接跟到目标位置(+偏移)
        target_vec = (f"{vec_ctor}({look_var}.global_position.x + {offset_x}, "
                      f"{look_var}.global_position.y + {offset_y})")
    else:
        # ── 3D 的相机必须**保持一段距离**，不能贴到角色身上 ──────────
        # 踩过的坑:3D 里把相机直接放到角色位置，相机就进了模型内部 ——
        # 画面上只剩一片模型的背面(用户看到的是"一片色块")。
        # 正确做法是记下"相机相对角色的偏移"，然后保持这个偏移跟随:
        # 相机在哪儿一开始由你在 Blender 里摆好，跟随过程中不改变视角。
        dist = getattr(node, "gk_distance", 0.0) or 0.0
        if dist:
            # 用户指定了距离:相机站在角色背后 dist 处、抬高 offset_y
            target_vec = (f"{look_var}.global_position "
                          f"+ {vec_ctor}({offset_x}, {offset_y}, {gd_number(dist)})")
        else:
            # 没指定距离:沿用"一开始的相对位置"，只跟着目标平移。
            # 这样"相机在 Blender 里摆在哪儿"就决定了看角色的角度。
            target_vec = (f"{look_var}.global_position + _gk_follow_delta "
                          f"+ {vec_ctor}({offset_x}, {offset_y}, 0.0)")

    # ── 跟随逻辑挂到**物理帧**,不挂在 _process(踩过的坑)────────────
    # 角色移动在 _physics_process 里(固定步长),相机跟随原来在 _process
    # (跟渲染帧率)—— 两个时钟不同步时相机就会抖。
    # 用户实测:把『每秒物理周期数』从 60 改成和刷新率一样,闪烁就没了,
    # 正是这个原因。相机跟着角色走,就必须和角色在**同一个时钟**里更新。
    #
    # 顺带一个好处:事件里连没连执行流都不影响 —— 只要图里有『相机_跟随』
    # 节点,相机就会跟着走。以前必须把它接在『每当_每帧』后面才行。
    hook_lines = []
    hook_lines.append(f"if {cam_var} == null:")
    hook_lines.append(f"    {cam_var} = {camera}")
    hook_lines.append(f"{look_var} = {target}")
    hook_lines.append(f"if {cam_var} != null and {look_var} != null:")
    if ctx.dim != 2 and not (getattr(node, "gk_distance", 0.0) or 0.0):
        setup_var = ctx.declare_var("_gk_follow_setup", "bool")
        delta_var = ctx.declare_var("_gk_follow_delta", vec_ctor)
        hook_lines.append(f"    if not {setup_var}:")
        hook_lines.append(f"        {setup_var} = true")
        hook_lines.append(f"        {delta_var} = {cam_var}.global_position "
                          f"- {look_var}.global_position")
    if smooth > 0.0:
        hook_lines.append(f"    {cam_var}.global_position = "
                          f"{cam_var}.global_position.lerp({target_vec}, "
                          f"{gd_number(smooth)})")
    else:
        hook_lines.append(f"    {cam_var}.global_position = {target_vec}")
    ctx.physics_hooks.setdefault("camera_follow", []).extend(hook_lines)
    # 事件体里留一行提示,让人知道逻辑在下面的物理帧里
    ctx.add_line(indent, "# 相机跟随的逻辑在 _physics_process 里(和角色同一个时钟)")


@stmt_node("GKNodeSetTimeScale")
def _stmt_set_time_scale(ctx, node, indent):
    scale = _tunable_number(ctx, node, "倍速", "1.0", "time_scale")
    ctx.add_line(indent, f"Engine.time_scale = {scale}")


# ==========================================================================
# 音效
# ==========================================================================
def _audio_player_type(node):
    """2D 还是普通音频播放器。3D 项目里用户会取消勾选『2D 音效』。"""
    return "AudioStreamPlayer2D" if getattr(node, "gk_2d", True) else "AudioStreamPlayer"


def _audio_host(ctx, node):
    """音效挂到哪个节点上。默认挂当前脚本所在节点。"""
    raw = (getattr(node, "gk_target", "") or "").strip()
    if raw and raw != TARGET_SELF:
        return ctx.target_expr(node, hint="audio")
    return "self"


@stmt_node("GKNodePlaySound")
def _stmt_play_sound(ctx, node, indent):
    """一次性音效:运行时建播放器、播完自己删。

    ── 为什么不要求用户先摆好 AudioStreamPlayer ─────────────────---─
    让用户为每个音效在场景里摆一个播放器节点，是"引擎在给用户添活"。
    这里按需建、播完销毁，节点图里写一条就够了。
    """
    path = ctx.input_string(node, "音频路径", "")
    volume = _tunable_number(ctx, node, "音量", "0.0", "sfx_volume")
    pitch = _tunable_number(ctx, node, "音高", "1.0", "sfx_pitch")
    player_type = _audio_player_type(node)
    host = _audio_host(ctx, node)

    stream_var = ctx.new_temp("sfx_stream")
    player_var = ctx.new_temp("sfx")
    # 音频文件缺失时给出可读的报错，而不是让 load() 静默返回 null 后再炸在别处
    ctx.add_line(indent, f"var {stream_var}: AudioStream = load({path}) as AudioStream")
    ctx.add_line(indent, f"if {stream_var} != null:")
    ctx.add_line(indent + 1, f"var {player_var} := {player_type}.new()")
    ctx.add_line(indent + 1, f"{player_var}.stream = {stream_var}")
    ctx.add_line(indent + 1, f"{player_var}.volume_db = {volume}")
    ctx.add_line(indent + 1, f"{player_var}.pitch_scale = {pitch}")
    ctx.add_line(indent + 1, f"{player_var}.max_polyphony = 4")
    ctx.add_line(indent + 1, f"{host}.add_child({player_var})")
    ctx.add_line(indent + 1, f"{player_var}.play()")
    # 播完自己删:用 finished 信号，比"算时长"可靠(流长度不一定拿得到)
    ctx.add_line(indent + 1, f"{player_var}.finished.connect({player_var}.queue_free)")
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f'push_warning("找不到音频文件: " + str({path}))')


@stmt_node("GKNodePlayMusic")
def _stmt_play_music(ctx, node, indent):
    """背景音乐:只保留一个播放器，切曲目时先停旧的。"""
    path = ctx.input_string(node, "音频路径", "")
    volume = gd_number(getattr(node, "gk_volume", -6.0))
    fade = gd_number(getattr(node, "gk_fade", 0.5))

    music_var = ctx.declare_var("_gk_music", "AudioStreamPlayer")
    stream_var = ctx.new_temp("bgm_stream")

    ctx.add_line(indent, f"var {stream_var}: AudioStream = load({path}) as AudioStream")
    ctx.add_line(indent, f"if {stream_var} != null:")
    # 重复播放同一首时不要打断(主菜单返回游戏很常见)
    ctx.add_line(indent + 1, f"if {music_var} != null and {music_var}.stream == {stream_var} and {music_var}.playing:")
    ctx.add_line(indent + 2, "pass")
    ctx.add_line(indent + 1, "else:")
    ctx.add_line(indent + 2, f"if {music_var} != null:")
    ctx.add_line(indent + 3, f"{music_var}.queue_free()")
    ctx.add_line(indent + 2, f"{music_var} = AudioStreamPlayer.new()")
    ctx.add_line(indent + 2, f"{music_var}.stream = {stream_var}")
    ctx.add_line(indent + 2, f"{music_var}.bus = \"Master\"")
    ctx.add_line(indent + 2, f"self.add_child({music_var})")
    ctx.add_line(indent + 2, f"{music_var}.play()")
    if float(getattr(node, "gk_fade", 0.5) or 0.0) > 0.0:
        ctx.add_line(indent + 2, f"{music_var}.volume_db = -80.0")
        fade_var = ctx.new_temp("fade")
        ctx.add_line(indent + 2, f"var {fade_var} := create_tween()")
        ctx.add_line(indent + 2,
                     f"{fade_var}.tween_property({music_var}, \"volume_db\", {volume}, {fade})")
    else:
        ctx.add_line(indent + 2, f"{music_var}.volume_db = {volume}")
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f'push_warning("找不到音乐文件: " + str({path}))')


@stmt_node("GKNodeStopSound")
def _stmt_stop_sound(ctx, node, indent):
    fade = gd_number(getattr(node, "gk_fade", 0.5))
    music_var = ctx.declare_var("_gk_music", "AudioStreamPlayer")
    ctx.add_line(indent, f"if {music_var} != null:")
    if float(getattr(node, "gk_fade", 0.5) or 0.0) > 0.0:
        fade_var = ctx.new_temp("fade")
        ctx.add_line(indent + 1, f"var {fade_var} := create_tween()")
        ctx.add_line(indent + 1, f"{fade_var}.tween_property({music_var}, \"volume_db\", -80.0, {fade})")
        ctx.add_line(indent + 1, f"{fade_var}.tween_callback({music_var}.stop)")
    else:
        ctx.add_line(indent + 1, f"{music_var}.stop()")


# ==========================================================================
# 存档
# ==========================================================================
# 为什么用"内存字典 + 显式保存"而不是每次写入都落盘:
#   落盘是磁盘 IO，在 _process 里每帧调用会卡帧。分开之后
#   "存档_写入"是廉价的，"存档_保存"才是真正写文件的那一次。
_SAVE_DATA_VAR = "_gk_save_data"


def _save_dict(ctx):
    """声明(不初始化)存档字典成员变量。"""
    return ctx.declare_var(_SAVE_DATA_VAR, "Dictionary")


def _ensure_save_block(ctx, data, path, slot):
    """保证存档字典在本代码块内已从磁盘载入一次。

    ── 为什么不靠"谁先谁后"标记 ────────────────────────────────────
    最初用"显式载入时打个标记，后面的节点就别插守卫"的办法。
    但它对顺序敏感:守卫只能插在**已经吐出的行**里，所以
    "写 -> 载入 -> 写"这种顺序下第二个写仍然会插一段多余的读盘。

    改成直接扫描本函数已经生成的代码:只要里面已经有 `_gk_read_save(`，
    就说明这个函数已经读过盘了，不需要再插守卫。
    这跟顺序无关，也不会漏。
    """
    ctx.need_save_helpers = True
    sink = getattr(ctx, "sink", None)
    lines = getattr(sink, "lines", None)
    if not isinstance(lines, list):
        # 拿不到收集器时退化成"就地守卫一次"。仍然正确，只是啰嗦一点。
        slot_expr = gd_string(slot)
        ctx.add_line(0, f"{path} = _gk_save_file({slot_expr})")
        ctx.add_line(0, f'if {path} == "":')
        ctx.add_line(1, f"{path} = _gk_save_file({slot_expr})")
        ctx.add_line(1, f"{data} = _gk_read_save({path})")
        return

    if any("_gk_read_save(" in text for text in lines):
        return

    slot_expr = gd_string(slot)
    index = len(lines)
    lines.insert(index, f"    {path} = _gk_save_file({slot_expr})")
    lines.insert(index + 1, f'    if {path} == "":')
    lines.insert(index + 2, f"        {path} = _gk_save_file({slot_expr})")
    lines.insert(index + 3, f"        {data} = _gk_read_save({path})")


@stmt_node("GKNodeSaveSet")
def _stmt_save_set(ctx, node, indent):
    data = _save_dict(ctx)
    path = ctx.declare_var("_gk_save_path", "String")
    slot = getattr(node, "gk_slot", "save1")
    key = ctx.input_string(node, "键名", "")
    socket = ctx._socket(node, "值")
    if socket is not None and getattr(socket, "is_linked", False):
        value = ctx.expr_of(socket)
    else:
        value = gd_literal(node.input_value(ctx, "值", 0))
    ctx.add_line(indent, f"{data}[{key}] = {value}")
    _ensure_save_block(ctx, data, path, slot)


@stmt_node("GKNodeSaveGet")
def _stmt_save_get(ctx, node, indent):
    data = _save_dict(ctx)
    path = ctx.declare_var("_gk_save_path", "String")
    slot = getattr(node, "gk_slot", "save1")
    key = ctx.input_string(node, "键名", "")
    var_name = ctx.declare_var(getattr(node, "gk_var", "value"), "Variant")
    default = gd_literal(node.input_value(ctx, "默认值", 0.0))
    ctx.add_line(indent, f"{var_name} = {data}.get({key}, {default})")
    ctx.read_vars.add(var_name)
    _ensure_save_block(ctx, data, path, slot)


@stmt_node("GKNodeSaveWrite")
def _stmt_save_write(ctx, node, indent):
    data = _save_dict(ctx)
    path = ctx.declare_var("_gk_save_path", "String")
    slot = getattr(node, "gk_slot", "save1")
    ctx.add_line(indent, f"{path} = _gk_save_file({gd_string(slot)})")
    ctx.add_line(indent, f"_gk_write_save({path}, {data})")
    _ensure_save_block(ctx, data, path, slot)


@stmt_node("GKNodeSaveLoad")
def _stmt_save_load(ctx, node, indent):
    data = _save_dict(ctx)
    path = ctx.declare_var("_gk_save_path", "String")
    slot = getattr(node, "gk_slot", "save1")
    ctx.need_save_helpers = True
    # 设好路径后直接读。同块内后面的存档节点扫描到本行已有的 _gk_read_save(
    # 就不会再插一段惰性守卫，所以不需要额外的抑制标记。
    ctx.add_line(indent, f"{path} = _gk_save_file({gd_string(slot)})")
    ctx.add_line(indent, f"{data} = _gk_read_save({path})")


@stmt_node("GKNodeSaveHas")
def _stmt_save_has(ctx, node, indent):
    """『存档_是否存在』:两个执行出口(有档 / 没档)。

    ── 为什么出口不叫『完成』 ──────────────────────────────────────
    emit_statement() 在 handler 跑完后会**自动**沿名为『完成』的出口继续。
    如果这里留一个『完成』出口，它就会把我的 else 分支再执行一遍
    (实测:『没存档』被打印两次)。所以两个出口都起具体名字，
    自动续接找不到『完成』自然就停住，分支完全由本函数接管。
    """
    slot = getattr(node, "gk_slot", "save1")
    ctx.need_save_helpers = True
    ctx.add_line(indent, f"if _gk_save_exists({gd_string(slot)}):")
    before = len(ctx.sink)
    _chain(ctx, node, indent + 1, "有档")
    if len(ctx.sink) == before:
        ctx.add_line(indent + 1, "pass")
    has_else = any(True for _ in _next_nodes(node, "没档"))
    if has_else:
        ctx.add_line(indent, "else:")
        before = len(ctx.sink)
        _chain(ctx, node, indent + 1, "没档")
        if len(ctx.sink) == before:
            ctx.add_line(indent + 1, "pass")


@stmt_node("GKNodeSaveDelete")
def _stmt_save_delete(ctx, node, indent):
    slot = getattr(node, "gk_slot", "save1")
    data = _save_dict(ctx)
    ctx.need_save_helpers = True
    ctx.add_line(indent, f"_gk_delete_save({gd_string(slot)})")
    # 删档后内存里那份也要清掉，否则"删了档还能读到旧数据"
    ctx.add_line(indent, f"{data}.clear()")


# ==========================================================================
# 屏幕效果
# ==========================================================================
@stmt_node("GKNodeScreenFlash")
def _stmt_screen_flash(ctx, node, indent):
    color = getattr(node, "gk_color", (1.0, 1.0, 1.0, 1.0))
    alpha = float(getattr(node, "gk_alpha", 0.6) or 0.6)
    duration = _tunable_number(ctx, node, "时长", "0.3", "flash_time")

    rect_var = ctx.new_temp("flash")
    tween_var = ctx.new_temp("flash_tween")
    ctx.add_line(indent, f"var {rect_var} := ColorRect.new()")
    ctx.add_line(indent, f"{rect_var}.color = Color({gd_number(color[0])}, {gd_number(color[1])}, "
                         f"{gd_number(color[2])}, {gd_number(alpha)})")
    # 不挡鼠标，否则闪光期间点不动 UI
    ctx.add_line(indent, f"{rect_var}.mouse_filter = Control.MOUSE_FILTER_IGNORE")
    ctx.add_line(indent, f"{rect_var}.set_anchors_preset(Control.PRESET_FULL_RECT)")
    # 盖在 UI 之上(UI 一般在 layer 0~10)
    ctx.add_line(indent, f"{rect_var}.z_index = 100")
    ctx.add_line(indent, f"add_child({rect_var})")
    ctx.add_line(indent, f"var {tween_var} := create_tween()")
    # 只淡 alpha:`color:a` 是子属性路径，改它不影响 RGB
    ctx.add_line(indent, f"{tween_var}.tween_property({rect_var}, \"color:a\", 0.0, {duration})")
    ctx.add_line(indent, f"{tween_var}.tween_callback({rect_var}.queue_free)")


@stmt_node("GKNodeScreenFade")
def _stmt_screen_fade(ctx, node, indent):
    color = getattr(node, "gk_color", (0.0, 0.0, 0.0, 1.0))
    duration = _tunable_number(ctx, node, "时长", "1.0", "fade_time")
    to_opaque = bool(getattr(node, "gk_to_opaque", True))
    hold = float(getattr(node, "gk_hold", 0.0) or 0.0)

    # 用一个持久的遮罩层，才能做"淡出 -> 换场景 -> 淡入"
    layer_var = ctx.declare_var("_gk_fade_layer", "CanvasLayer")
    rect_var = ctx.declare_var("_gk_fade_rect", "ColorRect")

    ctx.add_line(indent, f"if {layer_var} == null:")
    ctx.add_line(indent + 1, f"{layer_var} = CanvasLayer.new()")
    ctx.add_line(indent + 1, f"{layer_var}.layer = 100")
    ctx.add_line(indent + 1, f"add_child({layer_var})")
    ctx.add_line(indent + 1, f"{rect_var} = ColorRect.new()")
    ctx.add_line(indent + 1, f"{rect_var}.mouse_filter = Control.MOUSE_FILTER_IGNORE")
    ctx.add_line(indent + 1, f"{rect_var}.set_anchors_preset(Control.PRESET_FULL_RECT)")
    ctx.add_line(indent + 1, f"{layer_var}.add_child({rect_var})")

    r, g, b = color[0], color[1], color[2]
    start_alpha = 0.0 if to_opaque else 1.0
    end_alpha = 1.0 if to_opaque else 0.0
    ctx.add_line(indent, f"{rect_var}.color = Color({gd_number(r)}, {gd_number(g)}, "
                         f"{gd_number(b)}, {gd_number(start_alpha)})")
    ctx.add_line(indent, f"{rect_var}.visible = true")

    tween_var = ctx.new_temp("fade")
    ctx.add_line(indent, f"var {tween_var} := create_tween()")
    ctx.add_line(indent, f"{tween_var}.tween_property({rect_var}, \"color:a\", "
                         f"{gd_number(end_alpha)}, {duration})")
    if not to_opaque:
        # 淡回透明后把遮罩收起来，否则它会一直盖着(虽然透明，仍会吃掉性能)
        ctx.add_line(indent, f"{tween_var}.tween_callback(func(): {rect_var}.visible = false)")
    if hold > 0.0:
        ctx.add_line(indent, f"{tween_var}.tween_interval({gd_number(hold)})")
    # 等渐变结束再走『完成』出口，这样"淡出 -> 切场景"的顺序才正确
    ctx.add_line(indent, f"await {tween_var}.finished")


# ==========================================================================
# 界面
# ==========================================================================
# ── 为什么 UI 节点都"运行时自己建"而不是要求用户先摆控件 ──────────────
# 让用户为每一行字、每一条血条在场景树里手摆一个 Control，是"引擎在给
# 用户添活"，而且摆完还要在节点图里选来选去。这里统一按需创建，
# 同一个节点重复调用只是改内容，不会叠出新控件。
def _ui_root(ctx, indent):
    """取(必要时创建)UI 根容器。返回 (layer变量, root变量)。

    Control 必须是 CanvasLayer 的子节点才能浮在游戏画面之上，
    而且要铺满全屏、默认不吃鼠标事件(否则按钮以外的 UI 会挡住点击)。
    """
    layer = ctx.declare_var("_gk_ui_layer", "CanvasLayer")
    root = ctx.declare_var("_gk_ui_root", "Control")
    ctx.need_ui_helpers = True
    ctx.add_line(indent, "_gk_ensure_ui(self)")
    return layer, root


@stmt_node("GKNodeUiText")
def _stmt_ui_text(ctx, node, indent):
    _layer, root = _ui_root(ctx, indent)
    widget = ctx.declare_var(f"_gk_ui_{node.name}", "Label", key=id(node))
    text = ctx.input_string(node, "文字", "")
    x = _tunable_number(ctx, node, "X", "20.0", "ui_x")
    y = _tunable_number(ctx, node, "Y", "20.0", "ui_y")
    size = int(getattr(node, "gk_size", 24) or 24)
    color = getattr(node, "gk_color", (1.0, 1.0, 1.0, 1.0))

    ctx.add_line(indent, f"if {widget} == null:")
    ctx.add_line(indent + 1, f"{widget} = Label.new()")
    ctx.add_line(indent + 1, f"{widget}.name = {gd_string(node.name)}")
    ctx.add_line(indent + 1, f"{widget}.add_theme_font_size_override(\"font_size\", {size})")
    ctx.add_line(indent + 1, f"{widget}.add_theme_color_override(\"font_color\", "
                             f"Color({gd_number(color[0])}, {gd_number(color[1])}, "
                             f"{gd_number(color[2])}, {gd_number(color[3])}))")
    # 文字控件默认不吃鼠标，否则会挡住下面的按钮
    ctx.add_line(indent + 1, f"{widget}.mouse_filter = Control.MOUSE_FILTER_IGNORE")
    ctx.add_line(indent + 1, f"{root}.add_child({widget})")
    ctx.add_line(indent, f"{widget}.position = Vector2({x}, {y})")
    ctx.add_line(indent, f"{widget}.text = str({text})")


@stmt_node("GKNodeUiBar")
def _stmt_ui_bar(ctx, node, indent):
    _layer, root = _ui_root(ctx, indent)
    widget = ctx.declare_var(f"_gk_ui_{node.name}", "ProgressBar", key=id(node))
    value = ctx.input_number(node, "数值", "100.0")
    x = _tunable_number(ctx, node, "X", "20.0", "ui_x")
    y = _tunable_number(ctx, node, "Y", "60.0", "ui_y")
    max_value = gd_number(getattr(node, "gk_max", 100.0))
    width = gd_number(getattr(node, "gk_width", 200.0))
    height = gd_number(getattr(node, "gk_height", 16.0))
    color = getattr(node, "gk_color", (0.85, 0.25, 0.25, 1.0))

    ctx.add_line(indent, f"if {widget} == null:")
    ctx.add_line(indent + 1, f"{widget} = ProgressBar.new()")
    ctx.add_line(indent + 1, f"{widget}.name = {gd_string(node.name)}")
    ctx.add_line(indent + 1, f"{widget}.max_value = {max_value}")
    ctx.add_line(indent + 1, f"{widget}.show_percentage = false")
    # ProgressBar 的填充色靠 theme override，不设就是系统默认灰
    ctx.add_line(indent + 1, "var _fill := StyleBoxFlat.new()")
    ctx.add_line(indent + 1, f"_fill.bg_color = Color({gd_number(color[0])}, "
                             f"{gd_number(color[1])}, {gd_number(color[2])}, "
                             f"{gd_number(color[3])})")
    ctx.add_line(indent + 1, f"{widget}.add_theme_stylebox_override(\"fill\", _fill)")
    ctx.add_line(indent + 1, f"{widget}.mouse_filter = Control.MOUSE_FILTER_IGNORE")
    ctx.add_line(indent + 1, f"{root}.add_child({widget})")
    ctx.add_line(indent, f"{widget}.position = Vector2({x}, {y})")
    ctx.add_line(indent, f"{widget}.size = Vector2({width}, {height})")
    # 钳到合法区间:血量变成 -1 或超过上限时进度条会画出诡异的样子
    ctx.add_line(indent, f"{widget}.value = clampf({value}, 0.0, {max_value})")


@stmt_node("GKNodeUiButton")
def _stmt_ui_button(ctx, node, indent):
    """按钮:点击后沿『点击』出口继续。

    ── 为什么出口不叫『完成』 ──────────────────────────────────────
    emit_statement() 会在 handler 跑完后自动沿『完成』出口继续执行。
    按钮的语义是"点了才往下走"，如果叫『完成』，节点图一连线就会在
    按钮创建的那一刻立刻执行后面的逻辑(比如直接开始游戏)。
    所以出口叫『点击』，自动续接找不到『完成』就停住，
    真正的续接由回调函数 _gk_on_<节点名> 负责。
    """
    _layer, root = _ui_root(ctx, indent)
    widget = ctx.declare_var(f"_gk_ui_{node.name}", "Button", key=id(node))
    # 回调函数名同样要防同名:两个按钮可以都叫『开始按钮』，
    # 若共用 gk_on_button 就会有两个同名函数，Godot 直接报重复定义。
    handler = ctx.declare_func_name(f"gk_on_{snake_case(node.name, 'button')}", key=id(node))
    text = ctx.input_string(node, "文字", "")
    x = _tunable_number(ctx, node, "X", "100.0", "ui_x")
    y = _tunable_number(ctx, node, "Y", "200.0", "ui_y")
    width = gd_number(getattr(node, "gk_width", 160.0))
    height = gd_number(getattr(node, "gk_height", 48.0))

    ctx.add_line(indent, f"if {widget} == null:")
    ctx.add_line(indent + 1, f"{widget} = Button.new()")
    ctx.add_line(indent + 1, f"{widget}.name = {gd_string(node.name)}")
    # 按钮必须吃鼠标事件，否则点不动
    ctx.add_line(indent + 1, f"{widget}.mouse_filter = Control.MOUSE_FILTER_STOP")
    ctx.add_line(indent + 1, f"{widget}.pressed.connect({handler})")
    ctx.add_line(indent + 1, f"{root}.add_child({widget})")
    ctx.add_line(indent, f"{widget}.position = Vector2({x}, {y})")
    ctx.add_line(indent, f"{widget}.size = Vector2({width}, {height})")
    ctx.add_line(indent, f"{widget}.text = str({text})")

    # 把『点击』出口的后续代码放进一个独立函数
    lines = ctx.new_section()
    ctx._active.clear()
    prev_event = ctx._current_event
    ctx._current_event = node.name
    _chain(ctx, node, 1, "点击")
    if len(lines) == 0:
        lines.add(1, "pass")
    ctx._current_event = prev_event
    ctx.extra_sections.append({
        "signature": f"func {handler}() -> void:",
        "lines": list(lines.lines),
        "empty_body": "    pass",
        "separator": True,
    })


@stmt_node("GKNodeUiHide")
def _stmt_ui_hide(ctx, node, indent):
    widget_name = (getattr(node, "gk_widget", "") or "").strip()
    visible = "true" if getattr(node, "gk_visible", False) else "false"
    if not widget_name:
        ctx.warn(f"『{node.bl_label}』没有填控件名，这一句被跳过了")
        return
    # 用 group 查而不是路径查:UI 控件是运行时挂在 CanvasLayer 下的，
    # 路径会随场景变化，按名字找更稳。
    var = ctx.new_temp("widget")
    ctx.add_line(indent, f"var {var} := _gk_find_widget(self, {gd_string(widget_name)})")
    ctx.add_line(indent, f"if {var} != null:")
    ctx.add_line(indent + 1, f"{var}.visible = {visible}")
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f'push_warning("找不到界面控件: " + {gd_string(widget_name)})')
    ctx.need_ui_helpers = True


# ==========================================================================
# 射线
# ==========================================================================
def _ray_origin_and_space(ctx, node, indent):
    """生成射线的查询空间，返回 (space变量, params类型, 起点表达式, 维度前缀)。

    2D / 3D 的 API 名字不同，而且基类上不存在的方法**在编译期**就会被拒绝
    (实测 `Node2D` 上写 get_world_3d() 直接报 Function not found)，
    所以这里按项目维度生成对应的一套，而不是运行时判断。
    """
    if ctx.dim == 2:
        space_type = "PhysicsDirectSpaceState2D"
        params_type = "PhysicsRayQueryParameters2D"
        world_call = "get_world_2d"
        origin = "global_position"
    else:
        space_type = "PhysicsDirectSpaceState3D"
        params_type = "PhysicsRayQueryParameters3D"
        world_call = "get_world_3d"
        # Node3D 用 global_transform.origin:它在非物理节点上也存在
        origin = "global_transform.origin"
    space = ctx.new_temp("space")
    ctx.add_line(indent, f"var {space}: {space_type} = {world_call}().direct_space_state")
    return space, params_type, origin


def _emit_ray_branch(ctx, node, indent, hit_var, origin_expr, params_type, space):
    """把射线射出去，结果写进 hit_var。"""
    direction = ctx.input_vector(node, "方向")
    distance = ctx.input_number(node, "距离", "200.0")
    offset = getattr(node, "gk_offset", (0.0, 0.0, 0.0)) or (0.0, 0.0, 0.0)
    layer = int(getattr(node, "gk_layer", 1) or 1)

    vector_ctor = "Vector2" if ctx.dim == 2 else "Vector3"
    offset_expr = f"{vector_ctor}({gd_number(offset[0])}, {gd_number(offset[1])}" + (
        "" if ctx.dim == 2 else f", {gd_number(offset[2])}") + ")"

    dir_var = ctx.new_temp("dir")
    from_var = ctx.new_temp("from")
    to_var = ctx.new_temp("to")
    query_var = ctx.new_temp("query")

    ctx.add_line(indent, f"var {dir_var} := ({direction}).normalized()")
    ctx.add_line(indent, f"var {from_var} := {origin_expr} + {offset_expr}")
    ctx.add_line(indent, f"var {to_var} := {from_var} + {dir_var} * {distance}")
    ctx.add_line(indent, f"var {query_var} := {params_type}.create({from_var}, {to_var})")
    ctx.add_line(indent, f"{query_var}.collision_mask = {layer}")
    ctx.add_line(indent, f"var {hit_var}: Dictionary = {space}.intersect_ray({query_var})")
    return hit_var


@stmt_node("GKNodeRaycast")
def _stmt_raycast(ctx, node, indent):
    """射线检测:命中 / 没打中 两个出口。

    ── 为什么出口不叫『完成』 ──────────────────────────────────────
    和按钮、存档判断同理:emit_statement 会自动沿『完成』续接，
    那样无论有没有打到东西后面的逻辑都会跑，分支就废了。
    """
    space, params_type, origin = _ray_origin_and_space(ctx, node, indent)
    hit_var = ctx.new_temp("hit")
    _emit_ray_branch(ctx, node, indent, hit_var, origin, params_type, space)

    # 打到的东西 / 命中点存进成员变量，下游可直接引用
    target_var = ctx.declare_var(f"gk_ray_{snake_case(node.name, 'hit')}", "Variant",
                                 key=id(node))
    point_var = ctx.declare_var(f"gk_ray_point_{snake_case(node.name, 'hit')}", "Variant",
                                key=id(node))
    ctx.read_vars.add(target_var)
    ctx.read_vars.add(point_var)

    ctx.add_line(indent, f"if not {hit_var}.is_empty():")
    ctx.add_line(indent + 1, f"{target_var} = {hit_var}[\"collider\"]")
    ctx.add_line(indent + 1, f"{point_var} = {hit_var}[\"position\"]")
    before = len(ctx.sink)
    _chain(ctx, node, indent + 1, "命中")
    if len(ctx.sink) == before:
        ctx.add_line(indent + 1, "pass")
    has_else = any(True for _ in _next_nodes(node, "没打中"))
    if has_else:
        ctx.add_line(indent, "else:")
        before = len(ctx.sink)
        _chain(ctx, node, indent + 1, "没打中")
        if len(ctx.sink) == before:
            ctx.add_line(indent + 1, "pass")


@expr_node("GKNodeRaycastCheck")
def _expr_raycast_check(ctx, node):
    """纯表达式:求值时把射线就地射出去，返回布尔表达式。

    ── 为什么可以"在表达式里写语句" ──────────────────────────────────
    GDScript 的表达式是在**被使用的作用域**里求值的。这里通过 ctx.add_line
    生成的几行代码，会正好落在需要这个布尔值的地方(比如 `if <条件>:` 之前)，
    所以变量一定在作用域内、也一定在使用前就绪。
    """
    if ctx.dim == 2:
        space_type = "PhysicsDirectSpaceState2D"
        params_type = "PhysicsRayQueryParameters2D"
        world_call = "get_world_2d"
        origin = "global_position"
    else:
        space_type = "PhysicsDirectSpaceState3D"
        params_type = "PhysicsRayQueryParameters3D"
        world_call = "get_world_3d"
        origin = "global_transform.origin"

    direction = ctx.input_vector(node, "方向")
    distance = ctx.input_number(node, "距离", "200.0")
    offset = getattr(node, "gk_offset", (0.0, 0.0, 0.0)) or (0.0, 0.0, 0.0)
    layer = int(getattr(node, "gk_layer", 1) or 1)
    vector_ctor = "Vector2" if ctx.dim == 2 else "Vector3"
    parts = [gd_number(offset[0]), gd_number(offset[1])]
    if ctx.dim != 2:
        parts.append(gd_number(offset[2]))

    space = ctx.new_temp("space")
    dir_var = ctx.new_temp("dir")
    from_var = ctx.new_temp("from")
    to_var = ctx.new_temp("to")
    query_var = ctx.new_temp("query")
    hit_var = ctx.new_temp("hit")

    ctx.add_line(ctx._expr_indent, f"var {space}: {space_type} = {world_call}().direct_space_state")
    ctx.add_line(ctx._expr_indent, f"var {dir_var} := ({direction}).normalized()")
    ctx.add_line(ctx._expr_indent, f"var {from_var} := {origin} + {vector_ctor}({', '.join(parts)})")
    ctx.add_line(ctx._expr_indent, f"var {to_var} := {from_var} + {dir_var} * {distance}")
    ctx.add_line(ctx._expr_indent, f"var {query_var} := {params_type}.create({from_var}, {to_var})")
    ctx.add_line(ctx._expr_indent, f"{query_var}.collision_mask = {layer}")
    ctx.add_line(ctx._expr_indent, f"var {hit_var}: Dictionary = {space}.intersect_ray({query_var})")
    return f"not {hit_var}.is_empty()"


# ==========================================================================
# 定时
# ==========================================================================
@stmt_node("GKNodeEverySeconds")
def _stmt_every_seconds(ctx, node, indent):
    """每隔 N 秒触发一次。

    ── 为什么出口不叫『完成』 ──────────────────────────────────────
    按钮同理:叫『完成』会在建好计时器的瞬间就往下执行，
    而不是"等时间到了再执行"。所以出口叫『到点了』，
    真正的续接逻辑放进 timeout 回调。
    """
    interval = _tunable_number(ctx, node, "间隔", "1.0", "interval")
    timer_var = ctx.declare_var(f"gk_timer_{snake_case(node.name, 'every')}", "Timer",
                                key=id(node))
    handler = ctx.declare_func_name(f"gk_on_{snake_case(node.name, 'every')}", key=id(node))
    autostart = "true" if getattr(node, "gk_start_now", False) else "false"

    ctx.add_line(indent, f"if {timer_var} == null:")
    ctx.add_line(indent + 1, f"{timer_var} = Timer.new()")
    ctx.add_line(indent + 1, f"{timer_var}.name = {gd_string(node.name)}")
    ctx.add_line(indent + 1, f"{timer_var}.wait_time = {interval}")
    # one_shot = false 才是"每隔 N 秒"而不是"N 秒后一次"
    ctx.add_line(indent + 1, f"{timer_var}.one_shot = false")
    ctx.add_line(indent + 1, f"{timer_var}.autostart = {autostart}")
    ctx.add_line(indent + 1, f"{timer_var}.timeout.connect({handler})")
    ctx.add_line(indent + 1, "self.add_child(" + timer_var + ")")

    lines = ctx.new_section()
    ctx._active.clear()
    prev_event = ctx._current_event
    ctx._current_event = node.name
    _chain(ctx, node, 1, "到点了")
    if len(lines) == 0:
        lines.add(1, "pass")
    ctx._current_event = prev_event
    ctx.extra_sections.append({
        "signature": f"func {handler}() -> void:",
        "lines": list(lines.lines),
        "empty_body": "    pass",
        "separator": True,
    })


@stmt_node("GKNodeTweenValue")
def _stmt_tween_value(ctx, node, indent):
    raw_var = (getattr(node, "gk_var", "") or "").strip()
    if not raw_var:
        ctx.warn(f"『{node.bl_label}』没有填变量名，这一句被跳过了")
        return
    var_name = ctx.declare_var(raw_var, "float")
    ctx.read_vars.add(var_name)
    to_value = _tunable_number(ctx, node, "目标值", "0.0", "tween_to")
    duration = _tunable_number(ctx, node, "时长", "0.5", "tween_time")
    trans = getattr(node, "gk_trans", "TRANS_LINEAR") or "TRANS_LINEAR"
    ease = getattr(node, "gk_ease", "EASE_IN_OUT") or "EASE_IN_OUT"

    tween_var = ctx.new_temp("tween")
    ctx.add_line(indent, f"var {tween_var} := create_tween()")
    ctx.add_line(indent, f"{tween_var}.set_trans(Tween.{trans})")
    ctx.add_line(indent, f"{tween_var}.set_ease(Tween.{ease})")
    # 用 "变量名" 字符串做属性路径:tween_property 需要 NodePath 形式，
    # 对脚本成员变量来说就是变量名字符串本身。
    ctx.add_line(indent, f"{tween_var}.tween_property(self, {gd_string(var_name)}, "
                         f"{to_value}, {duration})")


# ==========================================================================
# 列表 / 字典
# ==========================================================================
def _list_var(ctx, node, gd_type="Array"):
    """列表 / 字典的目标变量:按名字共享(同一个名字就是同一个容器)。"""
    raw = (getattr(node, "gk_var", "") or "").strip()
    if not raw:
        return None
    return ctx.declare_var(raw, gd_type)


def _any_value_expr(ctx, node, socket_name="值"):
    """取一个"任意值"输入:连线了用上游表达式，否则用字面量。"""
    socket = ctx._socket(node, socket_name)
    if socket is not None and getattr(socket, "is_linked", False):
        return ctx.expr_of(socket)
    return gd_literal(node.input_value(ctx, socket_name, 0))


@stmt_node("GKNodeListNew")
def _stmt_list_new(ctx, node, indent):
    var = _list_var(ctx, node)
    if var is None:
        ctx.warn(f"『{node.bl_label}』没有填变量名，这一句被跳过了")
        return
    # 赋成空数组而不是 clear():这样"新建"是真的重新开始，语义更直白
    ctx.add_line(indent, f"{var} = []")


@stmt_node("GKNodeListAdd")
def _stmt_list_add(ctx, node, indent):
    var = _list_var(ctx, node)
    if var is None:
        ctx.warn(f"『{node.bl_label}』没有填变量名，这一句被跳过了")
        return
    ctx.add_line(indent, f"{var}.append({_any_value_expr(ctx, node)})")


@stmt_node("GKNodeListRemove")
def _stmt_list_remove(ctx, node, indent):
    var = _list_var(ctx, node)
    if var is None:
        ctx.warn(f"『{node.bl_label}』没有填变量名，这一句被跳过了")
        return
    ctx.add_line(indent, f"{var}.erase({_any_value_expr(ctx, node)})")


@stmt_node("GKNodeListGet")
def _stmt_list_get(ctx, node, indent):
    var = _list_var(ctx, node)
    if var is None:
        ctx.warn(f"『{node.bl_label}』没有填变量名，这一句被跳过了")
        return
    target_name = (getattr(node, "gk_target_var", "") or "").strip()
    if not target_name:
        ctx.warn(f"『{node.bl_label}』没有填『写入变量』，这一句被跳过了")
        return
    target = ctx.declare_var(target_name, "Variant")
    ctx.read_vars.add(target)
    index = ctx.input_number(node, "下标", "0")
    default = gd_literal(node.input_value(ctx, "默认值", 0.0))
    index_var = ctx.new_temp("idx")
    ctx.add_line(indent, f"var {index_var} := int({index})")
    # 越界必须给默认值:直接 items[99] 在 Godot 里会报错并中断这一帧
    ctx.add_line(indent, f"if {index_var} >= 0 and {index_var} < {var}.size():")
    ctx.add_line(indent + 1, f"{target} = {var}[{index_var}]")
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f"{target} = {default}")


@stmt_node("GKNodeDictSet")
def _stmt_dict_set(ctx, node, indent):
    var = _list_var(ctx, node, "Dictionary")
    if var is None:
        ctx.warn(f"『{node.bl_label}』没有填变量名，这一句被跳过了")
        return
    key = ctx.input_string(node, "键", "")
    ctx.add_line(indent, f"{var}[{key}] = {_any_value_expr(ctx, node)}")


@stmt_node("GKNodeDictGet")
def _stmt_dict_get(ctx, node, indent):
    var = _list_var(ctx, node, "Dictionary")
    if var is None:
        ctx.warn(f"『{node.bl_label}』没有填变量名，这一句被跳过了")
        return
    target_name = (getattr(node, "gk_target_var", "") or "").strip()
    if not target_name:
        ctx.warn(f"『{node.bl_label}』没有填『写入变量』，这一句被跳过了")
        return
    target = ctx.declare_var(target_name, "Variant")
    ctx.read_vars.add(target)
    key = ctx.input_string(node, "键", "")
    default = gd_literal(node.input_value(ctx, "默认值", 0.0))
    ctx.add_line(indent, f"{target} = {var}.get({key}, {default})")


@stmt_node("GKNodeDictHas")
def _stmt_dict_has(ctx, node, indent):
    var = _list_var(ctx, node, "Dictionary")
    if var is None:
        ctx.warn(f"『{node.bl_label}』没有填变量名，这一句被跳过了")
        return
    key = ctx.input_string(node, "键", "")
    ctx.add_line(indent, f"if {var}.has({key}):")
    before = len(ctx.sink)
    _chain(ctx, node, indent + 1, "有")
    if len(ctx.sink) == before:
        ctx.add_line(indent + 1, "pass")
    has_else = any(True for _ in _next_nodes(node, "没有"))
    if has_else:
        ctx.add_line(indent, "else:")
        before = len(ctx.sink)
        _chain(ctx, node, indent + 1, "没有")
        if len(ctx.sink) == before:
            ctx.add_line(indent + 1, "pass")


# ==========================================================================
# 手柄
# ==========================================================================
_JOY_DEADZONE_FN = "_gk_joy_axis"


@expr_node("GKNodeJoyAxis")
def _expr_joy_axis(ctx, node):
    ctx.need_joy_helpers = True
    axis = getattr(node, "gk_axis", "JOY_AXIS_LEFT_X") or "JOY_AXIS_LEFT_X"
    device = int(getattr(node, "gk_device", 0) or 0)
    deadzone = gd_number(getattr(node, "gk_deadzone", 0.2))
    # 死区不能靠 Input.get_joy_axis 自己处理:它不做死区，
    # 摇杆回中不准的手柄会一直输出 0.05 之类的值，角色就自己走路
    return f"{_JOY_DEADZONE_FN}({device}, JoyAxis.{axis}, {deadzone})"


@expr_node("GKNodeJoyVector")
def _expr_joy_vector(ctx, node):
    ctx.need_joy_helpers = True
    stick = getattr(node, "gk_stick", "LEFT") or "LEFT"
    device = int(getattr(node, "gk_device", 0) or 0)
    deadzone = gd_number(getattr(node, "gk_deadzone", 0.2))
    x_axis = "JOY_AXIS_RIGHT_X" if stick == "RIGHT" else "JOY_AXIS_LEFT_X"
    y_axis = "JOY_AXIS_RIGHT_Y" if stick == "RIGHT" else "JOY_AXIS_LEFT_Y"
    x = f"{_JOY_DEADZONE_FN}({device}, JoyAxis.{x_axis}, {deadzone})"
    y = f"{_JOY_DEADZONE_FN}({device}, JoyAxis.{y_axis}, {deadzone})"
    # 注意:Godot 的摇杆 Y 轴向上为负，和屏幕坐标一致，所以直接用
    return f"Vector2({x}, {y})"


def _joy_button_expr(node):
    button = getattr(node, "gk_button", "JOY_BUTTON_A") or "JOY_BUTTON_A"
    device = int(getattr(node, "gk_device", 0) or 0)
    return device, f"JoyButton.{button}"


@expr_node("GKNodeJoyButton")
def _expr_joy_button(ctx, node):
    device, button = _joy_button_expr(node)
    return f"Input.is_joy_button_pressed({device}, {button})"


@expr_node("GKNodeJoyButtonDown")
def _expr_joy_button_down(ctx, node):
    """刚按下:用 Input.is_action_just_pressed 的思路，但手柄按键没有对应动作名，
    所以自己记"上一帧是否按住"。"""
    device, button = _joy_button_expr(node)
    now_var = ctx.declare_var(f"gk_joy_now_{snake_case(node.name, 'btn')}", "bool",
                              key=id(node))
    prev_var = ctx.declare_var(f"gk_joy_prev_{snake_case(node.name, 'btn')}", "bool",
                               key=id(node))
    result_var = ctx.declare_var(f"gk_joy_down_{snake_case(node.name, 'btn')}", "bool",
                                 key=id(node))
    ctx.read_vars.update({now_var, prev_var, result_var})
    indent = ctx._expr_indent
    ctx.add_line(indent, f"{now_var} = Input.is_joy_button_pressed({device}, {button})")
    ctx.add_line(indent, f"{result_var} = {now_var} and not {prev_var}")
    ctx.add_line(indent, f"{prev_var} = {now_var}")
    return result_var


@stmt_node("GKNodeJoyVibrate")
def _stmt_joy_vibrate(ctx, node, indent):
    weak = gd_number(getattr(node, "gk_weak", 0.6))
    strong = gd_number(getattr(node, "gk_strong", 0.6))
    duration = _tunable_number(ctx, node, "时长", "0.2", "rumble_time")
    device = int(getattr(node, "gk_device", 0) or 0)
    ctx.add_line(indent, f"Input.start_joy_vibration({device}, {weak}, {strong}, {duration})")
    # 到点自动停:不依赖玩家再点一次，也不会一直震
    ctx.add_line(indent, f"get_tree().create_timer({duration}).timeout.connect("
                         f"func(): Input.stop_joy_vibration({device}))")


# ==========================================================================
# 触屏
# ==========================================================================
@expr_node("GKNodeTouchButton")
def _expr_touch_button(ctx, node):
    action = ctx.input_action(node, "gk_action", "ui_accept")
    return f"Input.is_action_pressed({action})"


@expr_node("GKNodeTouchDrag")
def _expr_touch_drag(ctx, node):
    """手指这次拖动方向:本帧内累积的位移。

    ── 为什么要每帧清零 ────────────────────────────────────────────
    Godot 把触摸拖动作为 InputEventScreenDrag 事件推给脚本，没有"当前拖动
    向量"这种现成属性，只能自己累积。但累积值如果不清零，手指不动时它也会
    一直保持上一次的偏移，角色就永远朝那个方向走。所以:
      1. _input 里把本帧的位移累加进去
      2. 每帧结束时清零(在 _process 末尾)
    这样读到的就是"这一帧手指移动了多少"，语义和摇杆一致。
    """
    ctx.need_touch_drag = True
    vec_var = ctx.declare_var("_gk_touch_drag", "Vector2", key=None)
    ctx.read_vars.add(vec_var)
    scale = gd_number(getattr(node, "gk_scale", 0.02))
    limit = gd_number(getattr(node, "gk_clamp", 1.0))
    reset = bool(getattr(node, "gk_reset", True))
    handler = ctx.declare_func_name(f"gk_on_touch_{snake_case(node.name, 'drag')}",
                                    key=id(node))
    ctx.register_input_handler(handler, reset=reset)

    # 每帧用完就清零。放在 process_hooks 里，会追加到 _process 末尾，
    # 保证"本帧已经读过它"之后才清。
    hook_key = "touch_drag_reset"
    if hook_key not in ctx.process_hooks:
        ctx.process_hooks[hook_key] = [f"{vec_var} = Vector2.ZERO"]

    return f"{vec_var}.limit_length({limit}) * {scale}"


# ==========================================================================
# 屏幕效果
# ==========================================================================
# 全屏滤镜着色器。用 SCREEN_TEXTURE / SCREEN_UV 读当前画面再染色 ——
# 这是 Godot 4 画布着色器里读屏的标准做法(实测编译通过)。
_GD_FILTER_SHADER = """shader_type canvas_item;
uniform float amount : hint_range(0.0, 1.0) = 0.0;
uniform vec4 tint : source_color = vec4(1.0, 1.0, 1.0, 1.0);
void fragment() {
    vec4 screen = texture(SCREEN_TEXTURE, SCREEN_UV);
    COLOR = vec4(mix(screen.rgb, tint.rgb, amount), screen.a);
}
"""
_FILTER_SHADER_CONST = "_GK_FILTER_SHADER"


def _screen_filter_mat(ctx):
    """取(必要时创建)全屏滤镜的材质。返回 (材质变量, 根变量, 矩形变量)。"""
    layer = ctx.declare_var("_gk_filter_layer", "CanvasLayer")
    rect = ctx.declare_var("_gk_filter_rect", "ColorRect")
    mat = ctx.declare_var("_gk_filter_mat", "ShaderMaterial")
    ctx.need_screen_filter = True
    return mat, layer, rect


@stmt_node("GKNodeScreenFilter")
def _stmt_screen_filter(ctx, node, indent):
    mat, layer, rect = _screen_filter_mat(ctx)
    amount = _tunable_number(ctx, node, "强度", "0.0", "filter_amount")
    color = getattr(node, "gk_color", (1.0, 0.15, 0.15, 1.0))
    softness = float(getattr(node, "gk_softness", 0.0) or 0.0)

    # 第一次用到时把全屏遮罩建起来(着色器源码作为常量放在脚本开头)
    ctx.add_line(indent, "_gk_ensure_filter(self)")
    ctx.add_line(indent, f"{mat}.set_shader_parameter(\"tint\", "
                         f"Color({gd_number(color[0])}, {gd_number(color[1])}, "
                         f"{gd_number(color[2])}, {gd_number(color[3])}))")
    if softness > 0.0:
        tween_var = ctx.new_temp("filter")
        ctx.add_line(indent, f"var {tween_var} := create_tween()")
        ctx.add_line(indent, f"{tween_var}.tween_method(func(v): "
                             f"{mat}.set_shader_parameter(\"amount\", v), "
                             f"float({mat}.get_shader_parameter(\"amount\")), "
                             f"{amount}, {gd_number(softness)})")
    else:
        ctx.add_line(indent, f"{mat}.set_shader_parameter(\"amount\", {amount})")


@stmt_node("GKNodeReloadScene")
def _stmt_reload_scene(ctx, node, indent):
    # 把 reload 推迟到帧末:这个节点常常是"按钮回调 / 计时器回调"里调的，
    # 在信号回调中间直接把场景树换掉，Godot 会报
    # "Parent node is busy setting up children"。
    ctx.add_line(indent, "get_tree().call_deferred(\"reload_current_scene\")")


@stmt_node("GKNodeQueueFree")
def _stmt_queue_free(ctx, node, indent):
    raw = getattr(node, "gk_target", TARGET_SELF)
    if raw == TARGET_SELF:
        ctx.add_line(indent, "queue_free()")
    else:
        ctx.add_line(indent, f"{ctx.target_expr(node)}.queue_free()")


@stmt_node("GKNodeSetVar")
def _stmt_set_var(ctx, node, indent):
    raw_name = getattr(node, "gk_var_name", "value")
    socket = ctx._socket(node, "值")
    if socket is not None and getattr(socket, "is_linked", False):
        value_expr = ctx.expr_of(socket)
        gd_type = "Variant"
    else:
        value = node.input_value(ctx, "值", 0.0)
        value_expr = gd_literal(value)
        gd_type = _gd_type_of(value)
    var_name = ctx.declare_var(raw_name, gd_type)
    ctx.add_line(indent, f"{var_name} = {value_expr}")


@stmt_node("GKNodeEmitSignal")
def _stmt_emit_signal(ctx, node, indent):
    name = snake_case(getattr(node, "gk_signal_name", "signal_name"), "signal_name")
    if name not in ctx.signals and getattr(node, "gk_signal_declare", True):
        ctx.signals.append(name)
    ctx.add_line(indent, f"{name}.emit()")


@stmt_node("GKNodeConnect")
def _stmt_connect(ctx, node, indent):
    target = ctx.target_expr(node)
    signal_name = snake_case(getattr(node, "gk_signal_name", "pressed"), "pressed")
    handler = snake_case(getattr(node, "gk_handler", "on_signal"), "on_signal")
    ctx.add_line(indent, f"if not {target}.{signal_name}.is_connected({handler}):")
    ctx.add_line(indent + 1, f"{target}.{signal_name}.connect({handler})")


@stmt_node("GKNodeCallFunction")
def _stmt_call_function(ctx, node, indent):
    func_name = snake_case(getattr(node, "gk_func_name", "my_function"), "my_function")
    raw = getattr(node, "gk_target", TARGET_SELF)
    if raw == TARGET_SELF:
        ctx.add_line(indent, f"{func_name}()")
    else:
        ctx.add_line(indent, f"{ctx.target_expr(node)}.{func_name}()")


@stmt_node("GKNodeBranch")
def _stmt_branch(ctx, node, indent):
    condition = ctx.input_bool_expr(node, "条件")
    ctx.add_line(indent, f"if {condition}:")
    body_indent = indent + 1
    before = len(ctx.sink)
    _chain(ctx, node, body_indent, "是")
    if len(ctx.sink) == before:
        ctx.add_line(body_indent, "pass")
    has_else = any(True for _ in _next_nodes(node, "否"))
    if has_else:
        ctx.add_line(indent, "else:")
        before = len(ctx.sink)
        _chain(ctx, node, body_indent, "否")
        if len(ctx.sink) == before:
            ctx.add_line(body_indent, "pass")


@stmt_node("GKNodeDoOnce")
def _stmt_do_once(ctx, node, indent):
    var_name = ctx.declare_var(f"done_{node.name}", "bool")
    ctx.add_line(indent, f"if not {var_name}:")
    ctx.add_line(indent + 1, f"{var_name} = true")
    before = len(ctx.sink)
    _chain(ctx, node, indent + 1, "首次")
    if len(ctx.sink) == before:
        ctx.add_line(indent + 1, "pass")


@stmt_node("GKNodeSequence")
def _stmt_sequence(ctx, node, indent):
    for socket in node.outputs:
        _chain(ctx, node, indent, socket.name)


@stmt_node("GKNodeForLoop")
def _stmt_for_loop(ctx, node, indent):
    count = ctx.input_number(node, "次数", "3")
    index_name = snake_case(getattr(node, "gk_index_name", "i"), "i")
    ctx.add_line(indent, f"for {index_name} in int({count}):")
    before = len(ctx.sink)
    _chain(ctx, node, indent + 1, "循环体")
    if len(ctx.sink) == before:
        ctx.add_line(indent + 1, "pass")
    _chain(ctx, node, indent, "结束后")


@stmt_node("GKNodeWait")
def _stmt_wait(ctx, node, indent):
    seconds = ctx.input_number(node, "秒", "1.0")
    use_process = bool(getattr(node, "gk_use_process", False))
    if use_process:
        ctx.add_line(indent, f"await get_tree().create_timer({seconds}, true, false, true).timeout")
    else:
        ctx.add_line(indent, f"await get_tree().create_timer({seconds}).timeout")
    _chain(ctx, node, indent)


def _gd_type_of(value):
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "String"
    if isinstance(value, (tuple, list)):
        return "Vector3" if len(value) >= 3 else "Vector2"
    return "Variant"


# ==========================================================================
# 生成器主体
# ==========================================================================
class Generator:
    def __init__(self, tree, options=None):
        self.tree = tree
        self.options = dict(options or {})
        self.ctx = GenContext(tree, self.options)
        self.functions = []   # (函数名, delta 参数名, 起始行, 事件节点)

    # ---- 主入口 ----
    def generate(self):
        events = self._collect_events()
        if not events:
            self.ctx.warn("节点树里没有任何事件节点，生成的脚本不会做任何事。"
                          "请从『每当_准备』或『每当_物理帧』开始连线。")

        self._prepass(events)

        # 同类事件合并成一个函数
        grouped = {}
        for node in events:
            kind = EVENT_KINDS.get(node.bl_idname)
            if kind is None:
                continue
            grouped.setdefault(kind[0], []).append(node)

        # 事件顺序:_ready 最前，然后 _process / _physics_process / _input
        def sort_key(item):
            name, nodes = item
            rank = {"_ready": 0, "_process": 1, "_physics_process": 2, "_input": 3}.get(name, 9)
            nodes.sort(key=lambda n: (getattr(n, "gk_order", 0) or 0, n.location[1], n.location[0]))
            return rank

        # 每生成完一段就记下它占了 ctx.lines 的哪几行，供 _assemble 精确拼接
        sections = []

        for func_name, nodes in sorted(grouped.items(), key=sort_key):
            first = nodes[0]
            if first.bl_idname == "GKNodeOnInput":
                signature = f"func {func_name}(event: InputEvent) -> void:"
                delta_name = "event"
            elif EVENT_KINDS[first.bl_idname][1]:
                signature = f"func {func_name}(delta: float) -> void:"
                delta_name = "delta"
            else:
                signature = f"func {func_name}() -> void:"
                delta_name = None

            lines = self.ctx.new_section()
            for index, node in enumerate(nodes):
                if index > 0:
                    lines.add(1, "# ---- 另一个同类事件 ----")
                self.ctx._active.clear()
                self.ctx._current_event = node.name
                if delta_name and self._delta_missing(node):
                    lines.add(1, f"var _unused_{delta_name} := {delta_name}")
                self._emit_event_body(node, 1)
            sections.append({
                "signature": signature,
                "lines": list(lines.lines),
                "empty_body": "    pass",
            })

        # 自定义函数节点
        for node in events:
            if node.bl_idname != "GKNodeFunction":
                continue
            func_name = snake_case(getattr(node, "gk_func_name", "my_function"), "my_function")
            self.ctx._active.clear()
            self.ctx._current_event = node.name
            lines = self.ctx.new_section()
            self._emit_event_body(node, 1)
            sections.append({
                "signature": f"func {func_name}() -> void:",
                "lines": list(lines.lines),
                "empty_body": "    pass",
                "separator": True,
            })

        # ---- 后台每帧行为(相机震动等) ----
        # 挂在 _process 里;已经有 _process 就追加，没有就补一个。
        if self.ctx.process_hooks:
            target_section = None
            for section in sections:
                if section["signature"] == "func _process(delta: float) -> void:":
                    target_section = section
                    break
            if target_section is None:
                target_section = {
                    "signature": "func _process(delta: float) -> void:",
                    "lines": [],
                    "empty_body": "    pass",
                    "separator": True,
                }
                sections.append(target_section)
            for hook_name, hook_lines in self.ctx.process_hooks.items():
                target_section["lines"].append(f"    # ---- {hook_name} ----")
                for text in hook_lines:
                    # hook 行自带相对缩进，这里补上函数体的 4 空格
                    target_section["lines"].append(("    " + text) if text else "")

        # ---- 物理帧行为(相机跟随) ----
        # 和上面 process_hooks 同样的处理,只是挂到 _physics_process。
        # 相机必须和角色在**同一个时钟**里更新,否则两个节拍一差拍画面就抖。
        if self.ctx.physics_hooks:
            target_section = None
            for section in sections:
                if section["signature"] == "func _physics_process(delta: float) -> void:":
                    target_section = section
                    break
            if target_section is None:
                target_section = {
                    "signature": "func _physics_process(delta: float) -> void:",
                    "lines": [],
                    "empty_body": "    pass",
                    "separator": True,
                }
                sections.append(target_section)
            for hook_name, hook_lines in self.ctx.physics_hooks.items():
                target_section["lines"].append(f"    # ---- {hook_name} ----")
                for text in hook_lines:
                    target_section["lines"].append(("    " + text) if text else "")

        # ---- 触屏拖动:需要往 _input(event) 里挂事件累积 ----
        if self.ctx._input_handlers:
            target_section = None
            for section in sections:
                if section["signature"].startswith("func _input("):
                    target_section = section
                    break
            if target_section is None:
                target_section = {
                    "signature": "func _input(event: InputEvent) -> void:",
                    "lines": [],
                    "empty_body": "    pass",
                    "separator": True,
                }
                sections.append(target_section)
            for func_name, reset in self.ctx._input_handlers:
                target_section["lines"].append(f"    {func_name}(event)")

        # ---- 自动找相机的小工具 ----
        if self.ctx.need_camera_finder:
            sections.append({
                "signature": "func _gk_find_camera_named(host: Node, wanted: String) -> Node:",
                "lines": [
                    "    if wanted.is_empty():",
                    "        return null",
                    # 先按名字找(用户在节点里填的那个名字)
                    '    for found in host.get_tree().root.find_children(wanted, "Camera3D", true, false):',
                    "        return found",
                    '    for found in host.get_tree().root.find_children(wanted, "Camera2D", true, false):',
                    "        return found",
                    # 名字对不上就退回自动模式 —— 填错不该让游戏跑不起来
                    "    return _gk_find_camera(host)",
                ],
                "empty_body": "    pass",
                "separator": True,
            })
            sections.append({
                "signature": "func _gk_find_camera(host: Node) -> Node:",
                "lines": [
                    # 优先用放进 "gk_camera" 组的相机(用户显式指定)，
                    # 其次退回到场景里第一个相机(新手不填也能用)
                    '    for child in host.get_tree().get_nodes_in_group("gk_camera"):',
                    "        if child is Camera2D or child is Camera3D:",
                    "            return child",
                    '    for found in host.get_tree().root.find_children("*", "Camera2D", true, false):',
                    "        return found",
                    '    for found in host.get_tree().root.find_children("*", "Camera3D", true, false):',
                    "        return found",
                    "    return null",
                ],
                "empty_body": "    pass",
                "separator": True,
            })

        # ---- 存档读写辅助函数 ----
        if self.ctx.need_save_helpers:
            sections.append({
                "signature": "func _gk_save_file(slot: String) -> String:",
                "lines": [
                    '    return "user://gk_" + slot + ".json"',
                ],
                "empty_body": "    pass",
                "separator": True,
            })
            sections.append({
                "signature": "func _gk_save_exists(slot: String) -> bool:",
                "lines": [
                    "    return FileAccess.file_exists(_gk_save_file(slot))",
                ],
                "empty_body": "    pass",
            })
            sections.append({
                "signature": "func _gk_read_save(path: String) -> Dictionary:",
                "lines": [
                    "    if not FileAccess.file_exists(path):",
                    "        return {}",
                    "    var file := FileAccess.open(path, FileAccess.READ)",
                    "    if file == null:",
                    '        push_warning("存档打不开: " + path)',
                    "        return {}",
                    "    var text := file.get_as_text()",
                    "    file.close()",
                    "    var parsed: Variant = JSON.parse_string(text)",
                    "    if parsed is Dictionary:",
                    "        return parsed",
                    '    push_warning("存档内容不是合法 JSON: " + path)',
                    "    return {}",
                ],
                "empty_body": "    pass",
            })
            sections.append({
                "signature": "func _gk_write_save(path: String, data: Dictionary) -> void:",
                "lines": [
                    "    var file := FileAccess.open(path, FileAccess.WRITE)",
                    "    if file == null:",
                    '        push_warning("存档写不进去: " + path)',
                    "        return",
                    "    file.store_string(JSON.stringify(data))",
                    "    file.close()",
                ],
                "empty_body": "    pass",
            })
            sections.append({
                "signature": "func _gk_delete_save(slot: String) -> void:",
                "lines": [
                    "    var path := _gk_save_file(slot)",
                    "    if not FileAccess.file_exists(path):",
                    "        return",
                    # 用 remove_absolute:path 已经是完整的 user:// 路径，
                    # 再 DirAccess.open("user://") + get_file() 反而容易拼错
                    "    DirAccess.remove_absolute(path)",
                ],
                "empty_body": "    pass",
                "separator": True,
            })

        # ---- 节点图自己生出来的函数(按钮点击回调等) ----
        sections.extend(self.ctx.extra_sections)

        # ---- 界面容器辅助函数 ----
        if self.ctx.need_ui_helpers:
            sections.append({
                "signature": "func _gk_ensure_ui(host: Node) -> void:",
                "lines": [
                    "    if _gk_ui_layer == null:",
                    "        _gk_ui_layer = CanvasLayer.new()",
                    "        _gk_ui_layer.name = \"GKUI\"",
                    # layer 10 = 盖在游戏画面上，但低于屏幕闪白(layer 100)
                    "        _gk_ui_layer.layer = 10",
                    "        host.add_child(_gk_ui_layer)",
                    "        _gk_ui_root = Control.new()",
                    "        _gk_ui_root.name = \"Root\"",
                    "        _gk_ui_root.set_anchors_preset(Control.PRESET_FULL_RECT)",
                    "        _gk_ui_root.mouse_filter = Control.MOUSE_FILTER_IGNORE",
                    "        _gk_ui_layer.add_child(_gk_ui_root)",
                ],
                "empty_body": "    pass",
                "separator": True,
            })
            sections.append({
                "signature": "func _gk_find_widget(host: Node, widget_name: String) -> Control:",
                "lines": [
                    "    if _gk_ui_layer == null:",
                    "        return null",
                    "    var found := _gk_ui_layer.find_child(widget_name, true, false)",
                    "    if found is Control:",
                    "        return found",
                    "    return null",
                ],
                "empty_body": "    pass",
                "separator": True,
            })

        # ---- 手柄摇杆死区 ----
        if self.ctx.need_joy_helpers:
            sections.append({
                "signature": "func _gk_joy_axis(device: int, axis: int, deadzone: float) -> float:",
                "lines": [
                    "    var raw := Input.get_joy_axis(device, axis)",
                    "    if absf(raw) < deadzone:",
                    "        return 0.0",
                    # 死区外重新映射到 0~1，否则推杆起点会"跳"一下
                    "    return clampf(remap(absf(raw), deadzone, 1.0, 0.0, 1.0), 0.0, 1.0) * signf(raw)",
                ],
                "empty_body": "    pass",
                "separator": True,
            })

        # ---- 屏幕滤镜 ----
        if self.ctx.need_screen_filter:
            sections.append({
                "signature": "func _gk_ensure_filter(host: Node) -> void:",
                "lines": [
                    "    if _gk_filter_layer != null:",
                    "        return",
                    "    _gk_filter_layer = CanvasLayer.new()",
                    "    _gk_filter_layer.name = \"GKFilter\"",
                    # layer 90:盖住游戏和普通界面(10)，但低于闪白/转场(100)
                    "    _gk_filter_layer.layer = 90",
                    "    host.add_child(_gk_filter_layer)",
                    "    _gk_filter_rect = ColorRect.new()",
                    "    _gk_filter_rect.set_anchors_preset(Control.PRESET_FULL_RECT)",
                    "    _gk_filter_rect.mouse_filter = Control.MOUSE_FILTER_IGNORE",
                    "    var sh := Shader.new()",
                    "    sh.code = _GK_FILTER_SHADER",
                    "    _gk_filter_mat = ShaderMaterial.new()",
                    "    _gk_filter_mat.shader = sh",
                    "    _gk_filter_mat.set_shader_parameter(\"amount\", 0.0)",
                    "    _gk_filter_mat.set_shader_parameter(\"tint\", Color(1, 1, 1, 1))",
                    "    _gk_filter_rect.material = _gk_filter_mat",
                    "    _gk_filter_layer.add_child(_gk_filter_rect)",
                ],
                "empty_body": "    pass",
                "separator": True,
            })

        # ---- 触屏拖动的事件处理 ----
        for func_name, reset in self.ctx._input_handlers:
            lines = [
                "    if event is InputEventScreenDrag:",
                "        _gk_touch_drag += (event as InputEventScreenDrag).relative",
            ]
            if reset:
                lines += [
                    "    elif event is InputEventScreenTouch:",
                    "        if not (event as InputEventScreenTouch).pressed:",
                    "            _gk_touch_drag = Vector2.ZERO",
                ]
            sections.append({
                "signature": f"func {func_name}(event: InputEvent) -> void:",
                "lines": lines,
                "empty_body": "    pass",
                "separator": True,
            })

        return self._assemble(sections)

    # ---- 事件收集 ----
    def _collect_events(self):
        result = []
        for node in self._nodes():
            if getattr(node, "gk_is_event", False) and not getattr(node, "gk_is_comment", False):
                result.append(node)
        result.sort(key=lambda n: (n.location[1], n.location[0]))
        return result

    def _nodes(self):
        nodes = getattr(self.tree, "nodes", ())
        result = list(nodes)
        # 让输出稳定:按 y 再按 x
        result.sort(key=lambda n: (getattr(n, "location", (0.0, 0.0))[1],
                                   getattr(n, "location", (0.0, 0.0))[0]))
        return result

    def _delta_missing(self, node):
        """帧间隔引脚没连线时才需要 var _unused_delta 消警告。"""
        outputs = getattr(node, "outputs", None)
        if outputs is None:
            return False
        socket = None
        getter = getattr(outputs, "get", None)
        if callable(getter):
            socket = getter("帧间隔")
        if socket is None:
            return False
        return not getattr(socket, "is_linked", False)

    # ---- 预扫描 ----
    def _prepass(self, events):
        """不执行、只收集:信号声明、只读变量。"""
        for node in self._nodes():
            if node.bl_idname == "GKNodeEmitSignal" and getattr(node, "gk_signal_declare", True):
                name = snake_case(getattr(node, "gk_signal_name", "signal_name"), "signal_name")
                if name not in self.ctx.signals:
                    self.ctx.signals.append(name)
            if node.bl_idname == "GKNodeGetVar":
                name = self.ctx.declare_var(getattr(node, "gk_var_name", "value"))
                self.ctx.read_vars.add(name)

    def _emit_event_body(self, node, indent):
        outputs = getattr(node, "outputs", None)
        if outputs is None:
            return
        socket = None
        getter = getattr(outputs, "get", None)
        if callable(getter):
            socket = getter("执行")
        if socket is None:
            return
        for target in _next_nodes(node, "执行"):
            _emit_body(self.ctx, target, indent)

    # ---- 组装 ----
    def _assemble(self, sections):
        """拼出最终脚本。

        sections 由 generate() 逐个函数生成，每段自带签名与语句行，
        所以拼接是精确的，不依赖行数猜测。
        """
        ctx = self.ctx
        out = []
        out.append(_BANNER)
        out.append("# 由 BlenderGameKit 自动生成，请勿手动修改。")
        out.append(f"# 节点树: {getattr(self.tree, 'name', '(未命名)')}")
        out.append("# 修改请回到 Blender 里改节点图，然后重新导出。")
        out.append(_BANNER)
        out.append("")
        resolved_class = gd_class_name(ctx.class_name)
        if resolved_class:
            out.append(f"class_name {resolved_class}")
        elif ctx.class_name:
            ctx.warn(f"类名『{ctx.class_name}』含中文，GDScript 不支持中文类名，已省略 class_name")
        out.append(f"extends {ctx.base_class}")
        out.append("")

        if ctx.signals:
            out.append("# ---- 信号 ----")
            for name in ctx.signals:
                out.append(f"signal {name}")
            out.append("")

        # 屏幕滤镜的着色器源码作为常量放在脚本开头。
        # 用三引号多行字符串:GDScript 支持 """..."""，着色器代码里的
        # 换行和引号都能原样保留，不必逐行拼接转义。
        if ctx.need_screen_filter and _GD_FILTER_SHADER not in out:
            out.append("# ---- 全屏滤镜着色器(由『屏幕_滤镜』节点使用)----")
            out.append(f'const {_FILTER_SHADER_CONST} := """{_GD_FILTER_SHADER}"""')
            out.append("")

        if ctx.input_actions:
            out.append("# ---- 输入动作名(Godot 输入映射里的动作)----")
            for const, action in ctx.input_actions.items():
                out.append(f"const {const} := {gd_string(action)}")
            out.append("")

        if ctx.onready:
            out.append("# ---- 场景节点引用 ----")
            for var, (path, gd_type) in ctx.onready.items():
                out.append(f"@onready var {var}: {gd_type} = {path}")
            out.append("")

        if ctx.exports:
            out.append("# ---- 可在检查器里调的参数 ----")
            for name, gd_type, value_expr, comment in ctx.exports:
                suffix = f"  # {comment}" if comment else ""
                out.append(f"@export var {name}: {gd_type} = {value_expr}{suffix}")
            out.append("")

        if ctx.vars:
            out.append("# ---- 成员变量 ----")
            for name in ctx.var_order:
                gd_type = ctx.vars.get(name) or "Variant"
                out.append(f"var {name}: {gd_type}")
            out.append("")

        for index, section in enumerate(sections):
            if index > 0 and section.get("separator"):
                out.append("")
            out.append(section["signature"])
            lines = [text for text in section["lines"] if text]
            if lines:
                out.extend(lines)
            else:
                out.append(section.get("empty_body", "    pass"))
            out.append("")

        while out and out[-1] == "":
            out.pop()
        out.append("")
        return "\n".join(out)


# ==========================================================================
# 对外接口
# ==========================================================================
# 上一次 generate() 用到的输入动作名。
# 生成的脚本里引用 "move_left" / "jump" 这些动作,而 Godot 的
# project.godot 必须登记它们,否则运行时刷
#     ERROR: The InputMap action "move_left" doesn't exist.
# 调用方(导出流程)拿这份清单去写 [input] 段。
# 用模块级变量是因为 generate() 的返回值已经被多处依赖,不想改签名。
last_used_actions = []


# 只有 CharacterBody 才有 move_and_slide / is_on_floor。
# 把「移动_按方向」用在这些基类上会生成 `self.move_and_slide()`,
# 而 Godot 加载脚本时报
#     Parse Error: Function "move_and_slide()" not found in base self.
# 注意:这个错误 `godot --check-only` **查不出来**(它不做成员级类型检查),
# 只有脚本真的挂到场景上、被加载时才报。所以必须在导出前就提醒。
_CHARACTER_BODIES = {"CharacterBody2D", "CharacterBody3D"}


def _check_base_class_supports_graph(tree, options, warnings):
    """基类和节点图是否匹配 —— 不匹配就给一条看得懂的告警。"""
    base = str(options.get("base_class", "") or "")
    if not base or base in _CHARACTER_BODIES:
        return
    needed = []
    for node in getattr(tree, "nodes", ()) or ():
        bl_idname = getattr(node, "bl_idname", "")
        if bl_idname == "GKNodeMoveDirection":
            needed.append("移动_按方向")
        elif bl_idname == "GKNodeMoveAndSlide":
            needed.append("移动_滑动")
        elif bl_idname == "GKNodeJump":
            needed.append("移动_跳跃")
        elif bl_idname == "GKNodeIsOnFloor":
            needed.append("取值_是否在地面")
    if not needed:
        return
    unique = sorted(set(needed))
    warnings.append(
        f"脚本基类是 {base}，但节点图里用了 {'、'.join(unique)} —— "
        f"这些节点生成的是 move_and_slide()，只有 CharacterBody2D / "
        f"CharacterBody3D 才有这个方法。Godot 会在加载脚本时报 "
        f"『Function \"move_and_slide()\" not found in base self』。\n"
        f"改法(二选一)：\n"
        f"  1) 选中要做角色的那个物体 → 『游戏物理』面板 → 类型改成 "
        f"『3D 角色 CharacterBody3D』(2D 则 CharacterBody2D)；\n"
        f"  2) 或者把侧栏『脚本基类』设成『自动(跟着物体走)』，让它跟着物体走。"
    )


def generate(tree, options=None):
    """生成 GDScript，返回 (代码, 告警列表)。"""
    if tree is None:
        raise CodegenAbort("没有可导出的节点树")
    generator = Generator(tree, options)
    code = generator.generate()
    warnings = list(generator.ctx.warnings)
    _check_base_class_supports_graph(tree, options or {}, warnings)
    # 登记本次用到的输入动作，供导出流程写进 project.godot
    globals()["last_used_actions"] = list(generator.ctx.input_actions.values())
    return code, warnings


def generate_to_dict(tree, options=None):
    code, warnings = generate(tree, options)
    return {"code": code, "warnings": warnings, "lines": code.count("\n") + 1}
