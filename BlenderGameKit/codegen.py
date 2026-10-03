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

import contextlib

from . import gk_sockets as S
from .nodes import node_base as B
from .nodes.node_base import TARGET_SELF, gd_node_path, gd_string, snake_case
from .nodes.nodes import (ANIM_CATEGORY_NAMES, action_category,
                          parse_transitions, transition_pin_name)
# 节点定义模块本身 —— 动画树的"图"解析(状态 / 转场 / 直连)住在那边,
# 因为面板、节点上的预览、代码生成三处必须用同一份判断。
from .nodes import nodes as node_lib

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
        # 额外缩进层。给"嵌套实例守卫"用:守卫那两行之后,本段所有逻辑
        # 都要再往里缩一层,才能落进 `if ...:` 里面。
        self.extra_indent = 0

    def add(self, indent, text=""):
        if not text:
            self.lines.append("")
            return
        self.lines.append(("    " * (indent + self.extra_indent)) + text)

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
        self.var_initials = {}     # 变量名 -> 初值字面量(动画树变量用)
        self.onready = {}          # 变量名 -> (节点访问表达式, GDScript 类型)
        self._onready_by_path = {}  # 节点访问表达式 -> 变量名(避免重复声明同一个节点)
        self.exports = []          # @export 参数行 (名字, 类型, 初值, 注释)
        # 已经发给 @export 的名字 —— 脚本变量不能和它重名
        # (Godot: Variable "x" has the same name as a previously declared variable)
        self.export_vars = set()
        # 被改过名的变量:原名 -> 实际用的名字(见 declare_var)
        self._var_aliases = {}
        self.input_actions = {}    # 常量名 -> 输入动作名
        # 常量名 -> 用户在『输入_按键』上捕获的那个键(导出时写进 project.godot)
        self.action_keys = {}
        self.warnings = []
        self.notes = []    # 提示(正常用法),不算告警
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
        # 跨函数共享的成员变量(见 member_temp)
        self._member_seq = 0
        self.member_vars = []
        # 播放器成员变量(整个脚本共用一个,见 anim_player_member)
        self._anim_player_var = None
        # 用到『播放完成』/『播完就切』时要在 _ready 里接 animation_finished
        self.need_anim_finished_signal = False
        # 生成的代码**用到了哪些类才有的成员** -> 基类需求
        # (见 require_base_class / check_base_class)
        self.base_class_requirements = {}
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
        # 只有真的用了动画节点才生成 _gk_find_animation_player()
        self.need_anim_finder = False
        # 动画事件出口(Started / Running / On Finish)——
        # 见 _stmt_play_anim。有事件出口就要生成信号回调;
        # 其中"每帧"那个还要在 _process 里插一段检查。
        self.need_anim_signals = False
        self.need_anim_running = False
        # 每次播放登记一条:{player, name, func, node, output, kind, signal}
        self.anim_events = []
        # 动画状态机(『每帧检查』模式)- 整段逻辑挂进 _physics_process
        self.anim_state_machines = []
        self.need_anim_state_process = False
        # 动画树(独立那张图)驱动的状态机 —— 同样挂 _physics_process
        self.anim_trees = []
        self.need_anim_tree_process = False
        # 『动画_开始』的开始条件 —— 要在每物理帧判一次"现在能不能开始"
        self.anim_starts = []
        self.need_anim_start_process = False
        # 状态变量名 -> 已经生成的"_进入状态"函数名。
        # 同一个变量只生成一个函数(见 enter_anim_state_func);
        # 不同的动画树各有各的状态变量,所以是按变量名分开的。
        self.anim_enter_funcs = {}
        # 动画名留空时要生成 _gk_first_animation()(见 _stmt_play_anim)
        self.need_first_animation = False
        # 用了『层』≥1 的播放节点时要生成 _gk_layer_player()(上下半身分离)
        self.need_layer_player = False
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

    @contextlib.contextmanager
    def capture(self):
        """把中途生成的代码收进**独立的一段**,结束后把写入目标还回去。

        ── 为什么必须还回去(踩过的坑,很隐蔽)────────────────────────
        `new_section()` 会把 `self.sink` 换成新的一段,而且**不会自己换回来**。
        在"生成某个节点的代码途中"开新段(状态的『进入时』/『离开时』函数、
        按钮的点击回调),之后的 `add_line` 就全跑到那一段里去了。

        实测表现(动画状态机):`_ready` 里那句"起始状态播一下"
        **整行消失**,它跑到了 `_gk_anim_enter_状态()` 的函数体里 ——
        结果进游戏什么动画都不播,而脚本语法完全正确,特别难查。
        """
        saved = self.sink
        section = self.new_section()
        try:
            yield section
        finally:
            self.sink = saved

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
    def note(self, message):
        """提示(不是问题):导出报告里单独一栏,不算告警。

        ── 为什么要分两档(2.5D 示例)────────────────────────────────
        "没填相机 = 自动用当前相机"这种是**正常的默认用法**。全塞进 warnings
        之后,"零告警"这条验收线就没法用了(正常图也会有几十条),
        真正的告警被淹掉。现在 notes 只展示、不参与验收。
        """
        text = str(message)
        if text not in self.notes:
            self.notes.append(text)

    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    def new_temp(self, prefix="t"):
        self._temp_seq += 1
        return f"_{snake_case(prefix, 't')}{self._temp_seq}"

    def require_base_class(self, name, why=""):
        """声明"生成的代码用到了 X 类才有的成员"。

        -- 为什么需要(踩过的坑)------------------------------------
        用户实测导出后 Godot 报:
            Function "is_on_floor()" not found in base self.

        根因是**两边对不上**:生成的脚本 `extends StaticBody3D`
        (因为他那个物体的物理类型是"静态物体"),但动画状态机生成的
        代码调用了 `is_on_floor()` / `velocity` -- 那是
        `CharacterBody3D` 才有的。

        这种错**不该让用户自己去查**。生成器最清楚它用到了什么,
        所以在这里登记需求,导出时由 `check_base_class()` 统一校验:
        对不上就换掉,并明确告诉用户为什么。
        """
        self.base_class_requirements.setdefault(name, why)

    def member_temp(self, prefix="m"):
        """取一个**成员变量**名。

        -- 为什么需要(踩过的坑)------------------------------------
        生成的代码会跨函数引用同一个值:比如 _ready() 里播动画、
        _physics_process() 里判状态,两处都要用**同一个**
        AnimationPlayer 引用。

        用 
ew_temp() 拿的是局部变量 -- 在 _ready() 里声明,
        _physics_process() 里就是
            Identifier "_ap1" not declared in the current scope
        实测炸了 3 条这种错误。

        所以需要跨函数共享的引用一律用这个,声明成成员变量
        (由 _assemble 挂到类的顶部,见 member_vars)。
        """
        name = f"_{snake_case(prefix, 'm')}{self._member_seq}"
        self._member_seq += 1
        self.member_vars.append(name)
        return name

    def anim_player_member(self):
        """**同一个** AnimationPlayer 成员变量(整个脚本共用一个)。

        ── 为什么需要它(踩过的坑)──────────────────────────────────
        `member_temp()` 每次调用都返回**新名字**。状态机和"动画_播放完成"
        节点都要保存播放器引用,各调一次就会生成 `_ap0` / `_ap1` 两个
        成员变量、两遍查找。而且两处都会去连 `animation_finished` 信号 ——
        同一个回调连两次,动画播完时回调会被调用两次。

        所以播放器引用收成一份,由 `_anim_player_assigned` 标记保证只赋值/连接一次。
        """
        if self._anim_player_var is None:
            self._anim_player_var = self.member_temp("ap")
        return self._anim_player_var

    def anim_finished_flag(self, tree_name="动画"):
        """"刚刚播完的是哪个动画"的成员变量与回调。

        ── 为什么不能直接查 current_animation(实测踩过,而且是用户报的 bug)──
        第一版把"播完"写成:
            _ap0.current_animation == "骨骼动作" and not _ap0.is_playing()
        **它永远不会成立**。在 Godot 4.7 上跑真工程实测:

            t=5.50  current_animation=[骨骼动作] is_playing=true  pos=5.43
            t=6.08  animation_finished(骨骼动作)
                    current_animation=[]         is_playing=false pos=6.02

        一次性动画播完的那一刻,`current_animation` **被清成空字符串** ——
        所以 "等于某个名字" 和 "已经不在播" 这两个条件**不可能同时为真**,
        状态机永远停在第一个状态(用户的形态键动画就是这么"播不出来"的)。

        正解是用 Godot 自己的事件:`animation_finished` 信号带**动画名**,
        回调里把它记下来,用的时候比这个名字。

        ── 标志是"粘住"的,不是只活一帧 ─────────────────────────────
        标志一直保留到**下一段动画开始播**(见下面的
        `_gk_on_anim_started` 回调)。这样"动画播完了**并且**在地面上"
        这种复合条件才写得出来 —— 如果只活一帧,那一刻玩家没落地,
        这条转场就永远等不到了。

        ── 为什么连 `animation_started` 来清(而不是在播的地方各清一次)──
        "开始播"的路径不止一条:`_gk_anim_enter()`(状态机)、
        『动画_播放』节点、用户自己写的 `play()`。挨个去加"清标志"既
        容易漏,又会因为**生成顺序**而漏(先播后判的图里,变量还没声明)。
        信号是唯一的汇聚点:接一次 `animation_started`,所有路径都覆盖到。

        返回 (标志变量名, 播完回调名)。
        """
        finished_var = self.declare_var("_gk_anim_finished", "String",
                                        initial='""')
        self.need_anim_finished_signal = True
        handler = self.declare_func_name("_gk_on_anim_finished",
                                         key=finished_var)
        started = self.declare_func_name("_gk_on_anim_started",
                                        key=finished_var)
        made = getattr(self, "_anim_finished_funcs", None)
        if made is None:
            made = set()
            self._anim_finished_funcs = made
        if handler not in made:
            made.add(handler)
            self.extra_sections.append({
                "signature": f"func {handler}(anim_name: StringName) -> void:",
                "lines": [
                    f"    # {tree_name}:动画播完时记下名字,给『播放完成』/『播完就切』用",
                    f"    {finished_var} = String(anim_name)",
                ],
                "empty_body": "    pass",
                "separator": True,
            })
        if started not in made:
            made.add(started)
            self.extra_sections.append({
                "signature": f"func {started}(_anim_name: StringName) -> void:",
                "lines": [
                    f"    # {tree_name}:有新动画开播 -> 上一次的『播放完成』作废",
                    f'    {finished_var} = ""',
                ],
                "empty_body": "    pass",
                "separator": True,
            })
        return finished_var, handler

    def connect_anim_finished(self, indent=None):
        """登记"要用『播放完成』/『播完就切』"。

        **只声明标志 + 打标记**,真正的信号连接由 `generate()` 统一补进
        `_ready`(见那边的说明)。

        ── 为什么不在调用点连(踩过的坑)────────────────────────────
        这个函数是从**表达式求值**里调的,而表达式可能在 `_physics_process`
        里(比如"每帧检查动画播完了没")。在那里连信号的话,生成的代码会变成

            func _physics_process(delta):
                _ap0 = _gk_find_animation_player(self)
                if _ap0 != null and not _ap0.animation_finished.is_connected(...):
                    _ap0.animation_finished.connect(...)     # 每帧都在检查

        虽然 `is_connected` 挡住了重复连接,但"每帧查一遍连接、每帧赋一次
        播放器"既难看又白费。信号是**一次性**的事,应该在 `_ready` 里做。
        """
        return self.anim_finished_flag()

    def _ensure_finished_connect(self, indent):
        """在 `_ready` 里真的把两个信号接上。"""
        finished_var = self.declare_var("_gk_anim_finished", "String",
                                        initial='""')
        player_temp = self.anim_player_member()
        handler = self.declare_func_name("_gk_on_anim_finished",
                                         key=finished_var)
        started = self.declare_func_name("_gk_on_anim_started",
                                        key=finished_var)
        self.add_line(indent, f"if {player_temp} == null:")
        self.add_line(indent + 1, f"{player_temp} = "
                                 f"_gk_find_animation_player(self)")
        for signal_name, func in (("animation_finished", handler),
                                  ("animation_started", started)):
            self.add_line(indent, f"if {player_temp} != null "
                                 f"and not {player_temp}.{signal_name}"
                                 f".is_connected({func}):")
            self.add_line(indent + 1,
                          f"{player_temp}.{signal_name}.connect({func})")
        self._anim_finished_connected = True
        return finished_var, player_temp

    def anim_finish_func(self, started_var, done_var, state_var, player_temp,
                         graph):
        """生成『动画_结束』的处理函数(每个状态变量一个)。

        走到结束口 = 这段动画演完了:

            func _gk_anim_finish() -> void:
                _gk_anim_done = true          # 玩法图读这个
                _gk_anim_started = false      # 等开始条件再开一次
                anim_state = ""
                # 可选:把播放器停住(不勾就保持最后一帧的姿势)
                if _ap0 != null:
                    _ap0.stop()
        """
        key = ("finish", state_var, started_var, done_var)
        existing = self.anim_enter_funcs.get(key)
        if existing:
            return existing
        func_name = self.declare_func_name("_gk_anim_finish")
        self.anim_enter_funcs[key] = func_name
        exit_node = graph.get("exit") if graph else None
        body = [
            f"    {done_var} = true",
            f"    {started_var} = false",
            f'    {state_var} = ""',
        ]
        if exit_node is not None and getattr(exit_node, "gk_stop_player", False):
            body += [
                f"    if {player_temp} != null:",
                f"        {player_temp}.stop()",
            ]
        self.extra_sections.append({
            "signature": f"func {func_name}() -> void:",
            "lines": body,
            "empty_body": "    pass",
            "separator": True,
        })
        return func_name

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
        # 已经被改过名的(和检查器参数撞了)—— 后续调用要用同一个名字，
        # 否则同一个变量会被声明两次(见下面 export_vars 的说明)
        clean = self._var_aliases.get(clean, clean)
        if clean in self.export_vars and clean not in self.vars:
            # ── 和自动生成的检查器参数重名(实测踩过)──────────────────
            # 动画树里的变量叫 `move_speed`,而『移动_按方向』的『速度』
            # 会生成 `@export var move_speed: float = 6.0` —— 同一个脚本里
            # 两个同名变量,Godot 报
            #     Parse Error: Variable "move_speed" has the same name as
            #     a previously declared variable
            # **整个脚本加载失败**。变量是用户起的名,优先保它,
            # 把自动生成的那个参数加序号(在 Godot 检查器里只是名字不同)。
            renamed = self.unique_name(clean, "var")
            self._var_aliases[clean] = renamed
            self.warn(f"变量『{clean}』和自动生成的检查器参数重名了,"
                      f"已把变量改名为『{renamed}』(Godot 不允许同名)。"
                      f"想用回原名,就把动画树里的变量或那个节点的参数改个名")
            clean = renamed
        if key is None:
            if clean in self.vars:
                existing = self.vars.get(clean)
                if gd_type and existing not in (None, "Variant", gd_type):
                    # ── 布尔变量被喂了数字?给一条**能照着改**的告警 ──────
                    # 最常见的触发方式:动画树的变量面板里声明了 bool,
                    # 玩法图里却用『变量_设置』填了个数字(节点上那个
                    # 数字框只能填数字)。原来只说"请改用不同的变量名",
                    # 用户会去改一个根本没问题的地方(实测被抱怨过)。
                    if existing == "bool" and gd_type in ("float", "int"):
                        self.warn(
                            f"变量『{clean}』是**布尔**变量,但这里给它的是"
                            f"数字 —— 想给真假就接一个『常量_布尔』到『值』引脚上"
                            f"(节点上那个框只能填数字)。这次先生成了 "
                            f"{clean} = false/true 的等价写法")
                        return clean
                    self.warn(f"变量『{clean}』同时被当成 {existing} 和 {gd_type} 使用，"
                              f"已保留 {existing}。请改用不同的变量名。")
                elif gd_type and existing in (None, "Variant"):
                    self.vars[clean] = gd_type
                if initial is not None:
                    # 第一次声明时记下的初值优先 —— 后面再有人声明同一个
                    # 名字不该把初值改掉(那会让生成结果依赖节点顺序)
                    self.var_initials.setdefault(clean, initial)
                return clean
            self.vars[clean] = gd_type
            self._var_owner[clean] = None
            self.var_order.append(clean)
            self._var_names[clean] = self._var_names.get(clean, 0) + 1
            if initial is not None:
                self.var_initials[clean] = initial
            return clean

        # 端口别名优先:声明过端口的名字,一律用端口那个变量
        aliases = getattr(self, "_var_aliases", None)
        if aliases:
            hit = aliases.get(clean) or aliases.get(f"输出_{clean}")
            if hit:
                if gd_type and self.vars.get(hit) in (None, "Variant"):
                    self.vars[hit] = gd_type
                return hit
        owner = self._var_owner.get(clean)
        if clean in self.vars and owner == key:
            # 同一个节点再次引用 —— 复用
            if gd_type and self.vars[clean] in (None, "Variant"):
                self.vars[clean] = gd_type
            if initial is not None:
                self.var_initials.setdefault(clean, initial)
            return clean
        # 首次声明，或该名字已被别的节点占用 -> 加序号另开一个
        # ── 计数要从"名字已被占"算起(2.5D 示例真跑抓出来的坑)────────
        # `key=None` 那条路只写 self.vars、**不动** _var_names,于是:
        #   1) 先有节点按名字声明 anim_state(key=None)
        #   2) 状态机再用 key 声明同名 -> 这里读到 used=0 -> 不加序号
        #      -> var_order 里出现两条一模一样的 `var anim_state: String`
        # Godot 直接拒绝整个脚本:
        #   Parse Error: Variable "anim_state" has the same name as a
        #   previously declared variable.
        used = self._var_names.get(clean, 0)
        if used == 0 and clean in self.vars:
            used = 1
        self._var_names[clean] = used + 1
        if used:
            clean = f"{clean}_{used + 1}"
        self.vars[clean] = gd_type
        self._var_owner[clean] = key
        self.var_order.append(clean)
        if initial is not None:
            self.var_initials[clean] = initial
        return clean

    def declare_export(self, base, gd_type, value_expr, comment=""):
        """生成一个可在检查器里调参的 @export 变量，返回变量名。

        只对"未连线的输入引脚"调用:连线了说明值由节点图算出来，不该暴露。
        """
        name = self.unique_name(base, "param")
        # 和已声明的脚本变量重名 -> 加序号。
        # 两边都可能先出现,所以两个方向都要挡(见 declare_var 里的说明)。
        if name in self.vars:
            renamed = self.unique_name(f"{base}_param", "param")
            self.warn(f"检查器参数『{name}』和脚本变量重名了,已改名为"
                      f"『{renamed}』(Godot 不允许同名)")
            name = renamed
        self.export_vars.add(name)
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

    def register_input_handler(self, func_name, reset=False, body=None):
        """登记一个要挂进 `_input(event)` 的回调。

        ── 为什么要 `body`(踩过的坑,2026-10-03)──────────────────────
        触屏拖动这类输入没有"当前值"这种属性,只能靠事件累积,
        所以必须有地方接事件。原来这张表只记 `(函数名, 是否清零)`,
        函数体在发射时才**写死成触屏拖动那几行** ——
        鼠标滚轮复用这张表之后,同一个函数名被生成了**两份**
        (一份滚轮、一份触屏),Godot 直接拒绝整个脚本:
            Parse Error: Function "_gk_mouse_wheel_event" has the same
            name as a previously declared function.
        现在函数体跟着登记一起带过来,发射时照抄,谁登记的谁的体。
        """
        entry = (func_name, bool(reset), list(body or []))
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
        # 缓存键用**节点身份**而不是节点名:两个不同节点可以同名，
        # 用名字做键会把它们的结果串在一起。
        # 身份走 node_lib.node_key(as_pointer):Blender 里同一个节点的两个
        # Python 包装对象 id() 不同,用 id() 会**撞键**(拿错别人的表达式)
        # 或者漏缓存 —— 详见 nodes.node_key。
        key = f"{node_lib.node_key(node)}::{output_name or ''}"
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

    def add_action(self, action, key=""):
        """登记一个输入动作名，返回脚本顶部常量的名字。

        这样所有输入动作都集中在脚本开头，改名一眼可见，
        也不会和 Godot 内置的 ui_* 动作混淆。

        key: 用户在『输入_按键』上**捕获**的那个键(比如 "W")。
        记下来给导出用 —— 导出时会把它写进 project.godot 的输入映射,
        这样"在 Blender 里按一下 W,Godot 里 W 就真的能用"。
        """
        name = (action or "").strip()
        if not name:
            return gd_string("")
        const = "ACTION_" + snake_case(name, "input").upper()
        if const not in self.input_actions:
            self.input_actions[const] = name
        key = str(key or "").strip()
        if key and const not in self.action_keys:
            self.action_keys[const] = key
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

    def animation_player(self, node, use_target=True):
        """这个节点用哪个动画播放器。

        use_target=False 表示**只看脚本所在物体**(忽略节点上的
        『动画播放器』物体选择)—— 『动画_状态机』用它:那个节点作用在
        自己身上,而老文件里可能还留着已经废弃的物体名,不能让它复活。
        """
        raw = (getattr(node, "gk_player", "") or "").strip() if use_target else ""
        if not raw or raw == TARGET_SELF:
            # ── 不能写成 `$AnimationPlayer`(踩过的坑)──────────────────
            # glTF 导入时,AnimationPlayer 会被放在**模型实例内部**:
            #     Hero [CharacterBody3D]        <- 脚本在这里
            #       Model [Node3D]
            #         Rig / Skeleton3D / 网格
            #         AnimationPlayer           <- 动画在这儿,深了一层
            # 实测:写 `$AnimationPlayer` 会报
            #     Node not found: AnimationPlayer (relative to /root/Hero)
            # 整个脚本连 _ready 都跑不起来。
            #
            # 和相机一样,交给一个**运行时查找**函数:填错只是退回自动模式,
            # 不会把脚本搞崩。
            self.need_anim_finder = True
            return "_gk_find_animation_player(self)"
        return self.target_expr(node, "gk_player", hint="anim")

    def enter_anim_state_func(self, node, var_name, player_temp, debug=False,
                              finished_var=None, enter_hooks=None,
                              exit_hooks=None):
        """生成/复用一个"进入动画状态"的成员函数,返回它的名字。

        ── 为什么抽成一个函数(而不是每处切换内联四行)──────────────
        状态切换在两个地方发生:起始状态(在 _ready 里播)和每条转移。
        老代码把
            if _ap0 != null and _ap0.has_animation("run"): _ap0.play("run")
            else: push_warning(...)
        内联在**每一处**,于是"进入状态时还想做点什么"(打印、触发事件、
        改速度)就得改 N 个地方 —— 而且漏改一处只有运行时才看得出来。

        函数体长这样:

            func _gk_anim_enter(next_state: String) -> void:
                match anim_state:                 # 离开旧状态
                    "攻击":
                        _gk_anim_exit_攻击()
                <停掉上一次"同时播"的层播放器>
                anim_state = next_state
                _gk_anim_finished = ""            # 进新状态 = 上次的"播完"作废
                if _ap0 == null:
                    push_warning(...)
                    return
                if _ap0.has_animation(next_state):
                    _ap0.play(next_state)
                else:
                    push_warning(...)
                match next_state:                 # 进入新状态
                    "攻击":
                        _gk_anim_enter_攻击()     # 进入时语句 + 同时播的动画

        按**状态变量名**缓存:同一张图被两个节点驱动、或者重复导出时
        只生成一份。不同的动画树各有各的状态变量,所以互不干扰。
        """
        enter_hooks = enter_hooks or {}
        exit_hooks = exit_hooks or {}
        key = (var_name, finished_var, tuple(sorted(enter_hooks.items())),
               tuple(sorted(exit_hooks.items())))
        existing = self.anim_enter_funcs.get(key)
        if existing:
            return existing
        func_name = self.declare_func_name("_gk_anim_enter",
                                        key=var_name)
        self.anim_enter_funcs[key] = func_name
        body = []
        # ── 1) 先跑旧状态的『离开时』────────────────────────────────
        # 必须在 anim_state 被改写**之前**跑,否则状态名已经变了,
        # "离开时"里的判断读到的会是新状态。
        if exit_hooks:
            body.append("    match " + var_name + ":")
            for name, hook in sorted(exit_hooks.items()):
                body.append(f"        {gd_string(name)}:")
                body.append(f"            {hook}()")
        # ── 2) 停掉上一次"同时播"的层播放器 ──────────────────────────
        # 新状态没填那几段的话,旧的会一直播下去(层播放器是独立的节点)。
        max_layers = int(getattr(self, "_anim_layer_count", 0) or 0)
        if max_layers:
            layers = ", ".join(str(index) for index in range(1, max_layers + 1))
            body.append(f"    for _gk_layer in [{layers}]:")
            body.append(f"        var _gk_lp = "
                        f"_gk_layer_player({player_temp}, _gk_layer)")
            body.append("        if _gk_lp != null:")
            body.append("            _gk_lp.stop()")
        body.append(f"    {var_name} = next_state")
        if finished_var:
            # 进入新状态 = 上一次的"播完"作废,否则会立刻再切一次
            body.append(f'    {finished_var} = ""')
        body += [
            f"    if {player_temp} == null:",
            # 找不到播放器时也要说一句 —— 否则用户只看到"状态在切、人不动"
            '        push_warning("[GK] 没找到 AnimationPlayer,'
            '动画状态机没法播动画")',
            "        return",
            f"    if {player_temp}.has_animation(next_state):",
            f"        {player_temp}.play(next_state)",
            "    else:",
            # 动画名不存在时把**可用清单**打出来 —— 和『动画_播放』同一个做法,
            # 否则用户只知道"没播",不知道模型里到底叫什么
            '        push_warning("[GK] 状态『%s』没有对应的动画,'
            '这个模型里可用的有: %s" % [next_state, '
            f"{player_temp}.get_animation_list()])",
        ]
        if debug:
            body.append('    print("[GK] 动画状态 -> %s" % next_state)')
        # ── 3) 最后跑新状态的『进入时』+ 同时播的动画 ────────────────
        if enter_hooks:
            body.append("    match next_state:")
            for name, hook in sorted(enter_hooks.items()):
                body.append(f"        {gd_string(name)}:")
                body.append(f"            {hook}()")
        self.extra_sections.append({
            "signature": f"func {func_name}(next_state: String) -> void:",
            "lines": body,
            "empty_body": "    pass",
            "separator": True,
        })
        return func_name

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


# ── 三张注册表都放在**最前面** ───────────────────────────────────
# 踩过的坑:注册表原来散在文件各处,新加的处理器只要写在定义之前就会
# `NameError: name 'multi_output' is not defined` —— 为了让"处理器
# 可以随便放",把注册表统一提到所有处理器之前。
MULTI_OUTPUT = {
    "GKNodeVectorSplit": {"X": ".x", "Y": ".y", "Z": ".z"},
}

# bl_idname -> 函数(ctx, node) -> {输出引脚名: 表达式} 或 None
MULTI_OUTPUT_FUNCS = {}


def multi_output(bl_idname):
    """注册一个"自定义多输出"表达式节点。"""
    def wrapper(func):
        MULTI_OUTPUT_FUNCS[bl_idname] = func
        return func
    return wrapper


@multi_output("GKNodeGetBone")
def _expr_get_bone(ctx, node):
    """读骨骼姿势:位置 / 旋转(度) / 缩放 / 世界位置。"""
    reader = ctx.declare_func_name("_gk_read_bone", key="bone")
    ctx.need_bone_reader = True
    call = (f"{reader}({_skeleton_expr(ctx, node)}, "
            f"{gd_string(str(getattr(node, 'gk_bone', '') or ''))})")
    return {
        "位置": f"({call}.get(\"pos\", Vector3.ZERO) as Vector3)",
        "旋转": f"({call}.get(\"rot\", Vector3.ZERO) as Vector3)",
        "缩放": f"({call}.get(\"scale\", Vector3.ONE) as Vector3)",
        "世界位置": f"({call}.get(\"world\", Vector3.ZERO) as Vector3)",
    }


@multi_output("GKNodeInputDirection")
def _expr_input_direction(ctx, node):
    """『输入_方向』:上下左右四个键 -> 二维方向向量 + 左右/前后分量。

    用户 2026-10-03(第四轮):"输入这里,这两个都可以整合到一起的,
    比如输入按键时有 x,y 的属性输出,这样就可以调节向前向量和向右向量
    (这还是虚幻蓝图的设计逻辑),这应该是自带的,不应该要再设置变量"。

    所以这里**一次算全**:方向(向量) / 左右(X) / 前后(Y) / 有输入。
    接『移动_按方向』的『方向』引脚就能走,不用自己拼向量、也不用设变量。
    (多输出节点在多个地方被引用也只求值一次 —— 和『动画判断_移动状态』
    同一个做法,靠 multi_output 注册。)
    """
    expr = {}
    deadzone = float(getattr(node, "gk_deadzone", 0.2) or 0.0)
    for direction in ("up", "down", "left", "right"):
        action = None
        key = ""
        resolver = getattr(node, "action_for", None)
        if callable(resolver):
            action, key = resolver(direction)
        else:
            action, key = f"move_{direction}", ""
        const = ctx.add_action(action, key)
        expr[direction] = f"Input.get_action_strength({const})"
    # ── 先落到局部变量再算(不是图省事)────────────────────────────
    # 直接内联的话每个分量要写两遍(一次判死区、一次取值),生成出来是
    # 一行两百多字符的怪物,而且 `get_action_strength` 会被调用两次。
    # 抽成 `var _gk_in_x := …` 之后:生成结果一眼能读,调用也只发生一次。
    x_var = ctx.new_temp("in_x")
    y_var = ctx.new_temp("in_y")
    len_var = ctx.new_temp("in_len")
    ctx.add_line(ctx._expr_indent,
                 f"var {x_var} := {expr['right']} - {expr['left']}")
    ctx.add_line(ctx._expr_indent,
                 f"var {y_var} := {expr['up']} - {expr['down']}")
    if deadzone > 0.0:
        dz = gd_number(deadzone)
        ctx.add_line(ctx._expr_indent,
                     f"if absf({x_var}) < {dz}: {x_var} = 0.0")
        ctx.add_line(ctx._expr_indent,
                     f"if absf({y_var}) < {dz}: {y_var} = 0.0")
    if getattr(node, "gk_normalize", True):
        # 斜着走不能变成 1.41 倍速(虚幻的 Normalize 就是这个作用)。
        # ── 一定要在**分量上**夹,不能只夹向量(踩过的坑)──────────────
        # 3D 项目里『方向』输出的是 `Vector3(_in_x, 0.0, _in_y)`,
        # 如果只在 `Vector2(...).limit_length(1.0)` 上夹,那个被夹过的值
        # 根本没进输出 —— 斜着走照样比直着走快 41%。
        # 所以这里直接把分量归一化,后面谁用都对。
        ctx.add_line(ctx._expr_indent,
                     f"var {len_var} := sqrt({x_var} * {x_var} + "
                     f"{y_var} * {y_var})")
        ctx.add_line(ctx._expr_indent, f"if {len_var} > 1.0:")
        ctx.add_line(ctx._expr_indent + 1, f"{x_var} /= {len_var}")
        ctx.add_line(ctx._expr_indent + 1, f"{y_var} /= {len_var}")
    # ── 3D 项目里『方向』要给 Vector3 ───────────────────────────────
    # 这个引脚主要就是喂给『移动_按方向』的,而它在 3D 里写的是
    # `velocity.z = -方向.z` —— 给 Vector2 会报
    #     Cannot find member "z" in base "Vector2"
    # **整个脚本编译不过**(实测,两个新脚本都被 Godot 挡下来了)。
    # 平面移动在 3D 里就是 XZ 平面:x -> 世界 X,y -> 世界 Z(朝前是 -Z,
    # 由『移动_按方向』那头取负)。和老的『输入_方向向量』保持同一套约定。
    direction_out = (f"Vector2({x_var}, {y_var})" if ctx.dim == 2
                     else f"Vector3({x_var}, 0.0, {y_var})")
    return {
        "方向": direction_out,
        "左右": x_var,
        "前后": y_var,
        "有输入": f"(({x_var} != 0.0) or ({y_var} != 0.0))",
    }


STMT_HANDLERS = {}


def stmt_node(*bl_idnames):
    def wrapper(func):
        for bl_idname in bl_idnames:
            STMT_HANDLERS[bl_idname] = func
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

    # ── 没连线时,B 的默认值按运算符走(用户 2026-10-03)────────────
    # 用户图里『数学_运算』选了"乘",B 没填 -> 默认 0 -> 乘出来恒为 0 ->
    # 「滚轮缩放」整条链失效,而节点看着完全正常(他报的就是"缩放失效")。
    # 乘法/除法/取模的**中性值是 1**,不是 0 —— 面板上没填就按中性值算。
    neutral = {"MUL": "1.0", "DIV": "1.0", "MOD": "1.0",
               "POW": "1.0", "LERP": "1.0"}.get(op, "0.0")

    def arg(name):
        if name == "B" and neutral == "1.0":
            return ctx.input_number(node, name, neutral)
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
    """与 / 或 / 非 / 异或 —— 输入引脚数量由节点自己长(见 nodes.GKNodeLogic)。

    ── 为什么按 inputs 逐个取,而不是写死 A、B ──────────────────────
    用户在节点上把『输入数量』调到 4,就应该真的按 4 个输入算。
    写死 A、B 的话多出来的引脚是**画着好看**,实际被忽略 ——
    那种"看着连了、其实没生效"的节点最难查。
    """
    op = str(getattr(node, "gk_op", "AND"))
    names = [socket.name for socket in (getattr(node, "inputs", None) or ())]
    if not names:
        names = ["A", "B"]
    parts = []
    for name in names:
        socket = ctx._socket(node, name)
        wired = bool(socket is not None and getattr(socket, "is_linked", False))
        if op == "AND" and not wired:
            # 与运算里一个假就全假 —— 空着的引脚会被当成假,
            # 结果整个节点恒为假。这是最容易"配了却完全没效果"的坑。
            ctx.warn(f"『逻辑_布尔运算』的输入『{name}』没接线,按假(false)算,"
                     f"整个『与』的结果会一直是假。"
                     f"要少用一个输入就把『输入数量』调小")
        parts.append(ctx.input_bool_expr(node, name))
    if op == "NOT":
        return f"(not {parts[0]})"
    if op == "XOR":
        # 布尔 != 就是异或,串起来就是"奇数个为真"(N 输入也对)
        expr = parts[0]
        for part in parts[1:]:
            expr = f"({expr} != {part})"
        return expr
    joined = " and ".join(parts) if op == "AND" else " or ".join(parts)
    return f"({joined})"


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


@multi_output("GKNodeGetPosition")
def _expr_get_position(ctx, node):
    """读物体的位置:输出分开的 X / Y / Z。

    走辅助函数(和『相机_取状态』同一个做法):位置是个 Vector,
    而用户要的是三个浮点 —— 一次算好、三个输出共用,不然表达式会
    重复三遍(那种"500 字符一行"的怪物已经踩过一次)。
    """
    target = ctx.target_expr(node, "gk_target", hint="节点")
    global_scope = bool(getattr(node, "gk_global", True))
    prop = "global_position" if global_scope else "position"
    made = getattr(ctx, "_pos_helpers", None)
    if made is None:
        made = set()
        ctx._pos_helpers = made
    key = f"{prop}_{'2d' if ctx.dim == 2 else '3d'}"
    if key not in made:
        made.add(key)
        if ctx.dim == 2:
            body = [
                f"    if host is Node2D:",
                f"        return (host as Node2D).{prop}",
                "    return Vector2.ZERO",
            ]
            ret = "Vector2"
        else:
            body = [
                f"    if host is Node3D:",
                f"        return (host as Node3D).{prop}",
                "    return Vector3.ZERO",
            ]
            ret = "Vector3"
        ctx.extra_sections.append({
            "signature": f"func _gk_{key}(host: Node) -> {ret}:",
            "lines": body,
            "empty_body": "    pass",
            "separator": True,
        })
    call = f"_gk_{key}({target})"
    return {
        "X": f"({call}).x",
        "Y": f"({call}).y",
        "Z": f"({call}).z" if ctx.dim != 2 else "0.0",
    }


@stmt_node("GKNodeSetPosition")
def _stmt_set_position(ctx, node, indent):
    """直接摆到某个位置(传送 / 回出生点)。"""
    target = ctx.target_expr(node, "gk_target", hint="节点")
    x = ctx.input_number(node, "X", "0.0")
    y = ctx.input_number(node, "Y", "0.0")
    z = ctx.input_number(node, "Z", "0.0")
    relative = bool(getattr(node, "gk_relative", False))
    if ctx.dim == 2:
        point = f"Vector2({x}, {y})"
    else:
        point = f"Vector3({x}, {y}, {z})"
    if relative:
        ctx.add_line(indent, f"{target}.global_position += {point}")
    else:
        ctx.add_line(indent, f"{target}.global_position = {point}")


@expr_node("GKNodeTextJoin")
def _expr_text_join(ctx, node):
    """文本拼接(A + 中间 + B)。数字会自动转文字 —— HUD 拼字符串用。"""
    socket_a = ctx._socket(node, "甲")
    socket_b = ctx._socket(node, "乙")
    first = (ctx.expr_of(socket_a)
             if socket_a is not None and getattr(socket_a, "is_linked", False)
             else gd_string(str(getattr(node, "gk_a", "") or "")))
    second = (ctx.expr_of(socket_b)
              if socket_b is not None and getattr(socket_b, "is_linked", False)
              else gd_string(str(getattr(node, "gk_b", "") or "")))
    middle = str(getattr(node, "gk_middle", "") or "")
    parts = [f"str({first})"]
    if middle:
        parts.append(gd_string(middle))
    parts.append(f"str({second})")
    return " + ".join(parts)


@expr_node("GKNodeDistance")
def _expr_distance(ctx, node):
    """两个物体之间的距离(2D 用 distance_to,3D 也是)。

    『甲』留空 = 脚本自己 —— 这样"离玩家多远"只要填『乙』一边,
    是拾取判定最常见的用法。
    """
    first = ctx.target_expr(node, "gk_a", hint="节点")
    second = ctx.target_expr(node, "gk_b", hint="节点")
    # ── 已销毁的物体要当成"无限远"(用户实测:吃到金币之后游戏卡死)──────
    # 拾取是"距离够 -> 销毁金币";金币被 queue_free() 之后,@onready 变量
    # 还指着那个**已释放的实例**,下一帧读 .global_position 直接抛错 ——
    # 而 `_physics_process` 里一抛错,后面的移动/相机**全都不执行**,
    # 表现就是"吃到金币就动不了了"。
    # 所以两边都加 is_instance_valid():失效 = 离得无穷远 = 不会触发拾取。
    guard = (f"is_instance_valid({first}) and is_instance_valid({second})")
    if ctx.dim == 2:
        return (f"(({first} as Node2D).global_position.distance_to("
                f"({second} as Node2D).global_position)"
                f" if {guard} else INF)")
    return (f"(({first} as Node3D).global_position.distance_to("
            f"({second} as Node3D).global_position)"
            f" if {guard} else INF)")


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


@expr_node("GKNodeKey")
def _expr_key(ctx, node):
    """『输入_按键』:一个节点三种触发方式(照 UPBGE 的 Keyboard Key)。

    捕获到的**按键**只在导出时用来写 project.godot 的输入映射;
    脚本里仍然走"输入动作"(Godot 的输入抽象),这样手柄/触屏
    以后要映射到同一个动作时不用改图。
    """
    action = "jump"
    resolver = getattr(node, "resolved_action", None)
    if callable(resolver):
        action = resolver()
    else:
        action = (str(getattr(node, "gk_action", "") or "").strip()
                  or str(getattr(node, "gk_action", "jump")))
    const = ctx.add_action(action, getattr(node, "gk_key", ""))
    trigger = str(getattr(node, "gk_trigger", "PRESSED"))
    func = {
        "PRESSED": "is_action_pressed",
        "JUST": "is_action_just_pressed",
        "RELEASED": "is_action_just_released",
    }.get(trigger, "is_action_pressed")
    return f"Input.{func}({const})"


def _mouse_button_const(button):
    return {
        "LEFT": "MOUSE_BUTTON_LEFT",
        "RIGHT": "MOUSE_BUTTON_RIGHT",
        "MIDDLE": "MOUSE_BUTTON_MIDDLE",
        "MB4": "MOUSE_BUTTON_XBUTTON1",
        "MB5": "MOUSE_BUTTON_XBUTTON2",
    }.get(str(button or "LEFT"), "MOUSE_BUTTON_LEFT")


@expr_node("GKNodeMouseButton")
def _expr_mouse_button(ctx, node):
    button = _mouse_button_const(getattr(node, "gk_button", "LEFT"))
    return f"Input.is_mouse_button_pressed({button})"


@expr_node("GKNodeMouseButtonDown")
def _expr_mouse_button_down(ctx, node):
    """鼠标键"刚按下" —— Godot **没有**这个 API,只能自己记上一帧。

    和手柄按键同一个做法(见 `_expr_joy_button_down`):
    多输出节点会在多个地方被求值,所以状态用**成员变量**存,
    每一帧比一次、更新一次。

    ── 去重键为什么不能用 `id(node)`(项目里踩过的老坑)─────────────
    本项目明确规定过(见 `node_key` 的说明):`id()` 会在对象被回收后**复用**,
    一次导出里前后创建的两批节点可能拿到同一个 id,于是新的"刚按下"节点
    被误判成"已经登记过",**那一路的按键永远不生效**。
    这里用节点自己的指针当键(拿不到指针时退回名字)。
    """
    button = _mouse_button_const(getattr(node, "gk_button", "LEFT"))
    key = ctx.member_temp("mouse_prev")
    ctx.physics_hooks.setdefault("mouse_button_edge", [])
    already = getattr(ctx, "_mouse_edge_nodes", None)
    if already is None:
        already = set()
        ctx._mouse_edge_nodes = already
    marker = node_lib.node_key(node)
    if marker not in already:
        already.add(marker)
        ctx.physics_hooks["mouse_button_edge"] += [
            f"{key} = Input.is_mouse_button_pressed({button})",
        ]
    # 值本身在"上一帧还没按、这一帧按了"时为真 —— 由上面的钩子维护。
    # 这里返回"当前按着 && 上一帧没按"的即时判断(不依赖顺序)
    return (f"(Input.is_mouse_button_pressed({button}) "
            f"and not {key})")


@expr_node("GKNodeMouseWheel")
def _expr_mouse_wheel(ctx, node):
    """滚轮这一帧滚了多少。

    Godot 的滚轮是**事件**(InputEventMouseButton 的 WHEEL_UP/DOWN),
    没有"当前值"这种属性,所以要自己累积:
    生成器补一个 `_input(event)` 收事件,并每帧清零
    (不清零的话手指/滚轮不动也会一直输出)。
    """
    var = ctx.declare_var("_gk_wheel", "float")
    handler = ctx.declare_func_name("_gk_mouse_wheel_event", key="wheel")
    made = getattr(ctx, "_wheel_handlers", None)
    if made is None:
        made = set()
        ctx._wheel_handlers = made
    if handler not in made:
        made.add(handler)
        # 函数体跟着登记一起带过去 —— 见 register_input_handler 的说明
        ctx.register_input_handler(handler, reset=False, body=[
            "    if event is InputEventMouseButton and event.pressed:",
            "        if event.button_index == MOUSE_BUTTON_WHEEL_UP:",
            f"            {var} += 1.0",
            "        elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN:",
            f"            {var} -= 1.0",
        ])
    # ── 清零的时机(2.5D 示例真跑抓出来的坑)────────────────────────
    # 原来是"在 _process 里清零"。但 Godot 一帧的顺序是
    #     _input(滚轮事件) -> _physics_process -> _process(清零)
    # 于是:滚轮值在**同一个物理帧**里被读之前就先被清掉了 ——
    # 表现是"滚轮完全没反应"(实测:直接给变量赋值都读不到)。
    #
    # 现在改成:
    #   * 物理帧末尾清(那时用户的逻辑已经读过了)—— 放在 physics_hooks 末尾
    #   * 没有物理 tick 的那些帧,在 _process 里清一次,免得值跨帧粘住
    # 两处都清,但由"上一物理帧号"保证同一帧不会被清两次。
    frame_var = ctx.declare_var("_gk_wheel_frame", "int", initial="-1")
    ctx.physics_hooks.setdefault("mouse_wheel_reset", [])
    reset_key = f"_gk_wheel_reset_{handler}"
    if not getattr(ctx, reset_key, False):
        setattr(ctx, reset_key, True)
        ctx.physics_hooks["mouse_wheel_reset"] += [
            f"{var} = 0.0    # 物理帧末尾清零:本帧该读的都已经读到了",
            f"{frame_var} = Engine.get_physics_frames()",
        ]
        ctx.process_hooks.setdefault("mouse_wheel_reset", [])
        ctx.process_hooks["mouse_wheel_reset"] += [
            f"if {frame_var} != Engine.get_physics_frames():",
            f"    {var} = 0.0",
        ]
    return var


@expr_node("GKNodeMousePosition")
def _expr_mouse_position(ctx, node):
    if getattr(node, "gk_relative", False):
        # 相对模式也是**位移**(不是速度):和『鼠标_移动量』统一,
        # 免得用户拿两个不同的量去做同一件事
        return _expr_mouse_motion(ctx, node)
    return "get_viewport().get_mouse_position()"


def ensure_mouse_delta(ctx):
    """保证 `_gk_mouse_delta`(这一帧鼠标走了多少像素)存在并会被累积。

    两处要用:『鼠标_移动量』节点,以及**自由飞行相机**(它用同一个量转视角)。
    抽出来共用,免得各写一份、其中一份忘了登记处理函数。
    """
    var = ctx.declare_var("_gk_mouse_delta", "Vector2")
    key = "_gk_mouse_motion_registered"
    if getattr(ctx, key, False):
        return var
    setattr(ctx, key, True)
    ctx.register_input_handler(
        "_gk_mouse_motion_event",
        reset=False,
        # 第一行必须自带缩进(踩过:顶格会让 Godot 报
        # "Expected indented block after function declaration")
        body=[
            "    if event is InputEventMouseMotion:",
            f"        {var} += event.relative",
        ],
    )
    frame_var = ctx.declare_var("_gk_mouse_frame", "int", initial="-1")
    ctx.physics_hooks.setdefault("mouse_motion_reset", [])
    ctx.physics_hooks["mouse_motion_reset"] += [
        f"{var} = Vector2.ZERO",
        f"{frame_var} = Engine.get_physics_frames()",
    ]
    ctx.process_hooks.setdefault("mouse_motion_reset", [])
    ctx.process_hooks["mouse_motion_reset"] += [
        f"if {frame_var} != Engine.get_physics_frames():",
        f"    {var} = Vector2.ZERO",
    ]
    return var


@expr_node("GKNodeMouseMotion")
def _expr_mouse_motion(ctx, node):
    """鼠标这一帧移动了多少(**像素**)。

    ── 为什么不用 `Input.get_last_mouse_velocity()`(用户 2026-10-03)────
    用户原话:"运行时视角一卡一卡的"。原因:
      * `get_last_mouse_velocity()` 是**按渲染帧**算出来的速度(像素/秒),
        而且是"这一帧到上一帧"的差分 —— 值本身就很抖;
      * 我们的逻辑跑在 `_physics_process` 里,物理帧和渲染帧**不同步**
        (60Hz 物理跑在 144Hz 渲染中间,有时一帧吃两次、有时一次都吃不到),
        于是转视角的增量一会儿大一会儿小 —— 看起来就是一卡一卡。
    正确做法(所有 FPS 游戏都这么做):在 `_input` 里把
    `InputEventMouseMotion.relative` **累加**起来,物理帧里取走并清零 ——
    和滚轮用的是同一套机制。这样"物理帧拿到的是这段时间里鼠标真实走过的
    像素总数",无论帧率怎么变,转过的角度总和都对得上。
    """
    return ensure_mouse_delta(ctx)



@expr_node("GKNodeActionPressed")
def _expr_action_pressed(ctx, node):
    return f"Input.is_action_pressed({ctx.input_action(node, 'gk_action', 'jump')})"


@expr_node("GKNodeActionJustPressed")
def _expr_action_just_pressed(ctx, node):
    return f"Input.is_action_just_pressed({ctx.input_action(node, 'gk_action', 'jump')})"


def _skeleton_expr(ctx, node, prop="gk_skeleton_node"):
    """拿骨架:填了节点路径就用它,留空就自动找(带缓存)。"""
    raw = str(getattr(node, prop, "") or "").strip()
    if raw and raw != TARGET_SELF:
        return ctx.target_expr(node, prop, "骨架")
    cache = ctx.declare_var("_gk_skeleton_cache", "Skeleton3D")
    ctx.need_skeleton_helper = True
    return f"({cache} if {cache} != null and is_instance_valid({cache}) " \
           f"else _gk_find_skeleton(self))"


def _bone_setup(ctx, node, indent, lines):
    """生成"找骨架 + 找骨头 + 找不到就告警"的几行(返回两个变量名)。

    ── 为什么必须判 -1(踩过的坑)──────────────────────────────────
    `Skeleton3D.find_bone()` 找不到骨头时返回 **-1**,而骨骼 API 收到 -1
    会抛错刷屏(Godot 里是 "Index p_bone = -1 is out of bounds")。
    骨骼名是用户手填的字符串,打错很正常 —— 与其让游戏里刷一屏红字,
    不如这里判掉并**指名道姓**地告警。
    """
    bone_expr = gd_string(str(getattr(node, "gk_bone", "") or ""))
    skel_var = ctx.new_temp("skel")
    idx_var = ctx.new_temp("bone")
    lines.append(f"var {skel_var} = {_skeleton_expr(ctx, node)}")
    lines.append(f"var {idx_var}: int = -1")
    lines.append(f"if {skel_var} != null:")
    lines.append(f"    {idx_var} = {skel_var}.find_bone({bone_expr})")
    lines.append(f"if {idx_var} < 0:")
    lines.append(f'    push_warning("[GK] 找不到骨骼 %s —— 检查骨骼名,'
                 f'或者模型还没生成骨架" % {bone_expr})')
    return skel_var, idx_var


@stmt_node("GKNodeSetBone")
def _stmt_set_bone(ctx, node, indent):
    """摆骨骼姿势(ADD = 在现有姿势上叠加)。"""
    lines = []
    skel_var, idx_var = _bone_setup(ctx, node, indent, lines)
    do_rot = bool(getattr(node, "gk_do_rot", True))
    do_pos = bool(getattr(node, "gk_do_pos", False))
    do_scale = bool(getattr(node, "gk_do_scale", False))
    mode = str(getattr(node, "gk_mode", "ADD"))
    lines.append("else:")
    if do_rot:
        rot = ctx.input_vector(node, "旋转")
        # ── 度数转弧度**要按分量转**(踩过的坑)──────────────────────
        # 写成 `deg_to_rad(Vector3(...))` 是错的:Godot 4 的 deg_to_rad
        # 只收 float,而且是**解析期**就报
        #     Invalid argument for "deg_to_rad()" function
        # 整个脚本加载失败。向量乘 `PI / 180.0` 才是对的写法。
        rot_rad = f"(({rot}) * (PI / 180.0))"
        if mode == "ADD":
            lines.append(f"    {skel_var}.set_bone_pose_rotation({idx_var}, "
                         f"{skel_var}.get_bone_pose_rotation({idx_var}) * "
                         f"Quaternion.from_euler({rot_rad}))")
        else:
            lines.append(f"    {skel_var}.set_bone_pose_rotation({idx_var}, "
                         f"Quaternion.from_euler({rot_rad}))")
    if do_pos:
        pos = ctx.input_vector(node, "位置")
        if mode == "ADD":
            lines.append(f"    {skel_var}.set_bone_pose_position({idx_var}, "
                         f"{skel_var}.get_bone_pose_position({idx_var}) "
                         f"+ ({pos}))")
        else:
            lines.append(f"    {skel_var}.set_bone_pose_position({idx_var}, "
                         f"{pos})")
    if do_scale:
        scale = ctx.input_vector(node, "缩放")
        lines.append(f"    {skel_var}.set_bone_pose_scale({idx_var}, "
                     f"{scale})")
    if not (do_rot or do_pos or do_scale):
        lines.append("    pass    # 三个通道都没勾,什么都不改")
    for line in lines:
        ctx.add_line(indent, line)


@stmt_node("GKNodeBoneLookAtCamera")
def _stmt_bone_look_at_camera(ctx, node, indent):
    """**让一根骨头跟着相机转**(第一人称的"脖子/上半身跟随")。

    ── 怎么做的 ────────────────────────────────────────────────────
    1. 每物理帧读相机的世界朝向(和相机更新同一个时钟,不然会甩)
    2. 取偏航 / 俯仰,各自乘一个**权重**(只跟一部分才像活人)
    3. 按『最大角度』夹一下(脖子不能拧 180°)
    4. 指数平滑(和弹簧臂同一个写法,和帧率无关)
    5. 写成这根骨头姿势上的**叠加**

    之所以是叠加:骨骼姿势是"相对父骨"的,直接覆盖会把动画本身的头部动作
    (点头、摇头)一起抹掉 —— 而跟随视角要的正是"在动画之上再加一点"。
    """
    enabled_look = True
    bone_expr = gd_string(str(getattr(node, "gk_bone", "") or ""))
    skel_var = ctx.new_temp("skel")
    idx_var = ctx.new_temp("bone")
    camera = _camera_node_expr(ctx, node, "gk_camera")
    yaw_w = float(getattr(node, "gk_yaw_weight", 0.5) or 0.0)
    pitch_w = float(getattr(node, "gk_pitch_weight", 0.35) or 0.0)
    limit = float(getattr(node, "gk_limit", 60.0) or 0.0)
    smooth = float(getattr(node, "gk_smooth", 8.0) or 0.0)
    cam_var = ctx.declare_var("_gk_look_cam", "Node")
    cur_yaw = ctx.declare_var("_gk_look_yaw", "float")
    cur_pitch = ctx.declare_var("_gk_look_pitch", "float")
    ctx.add_line(indent, f"{cam_var} = {camera}")
    if not enabled_look:
        return

    # 每帧的动作挂 _physics_process(和相机/角色同一个时钟)——
    # 写在执行点的话只会转一下,第二帧就被动画覆盖回去了。
    frame = [
        f"if {cam_var} != null and {cam_var} is Node3D:",
        f"    var {skel_var}: Skeleton3D = "
        f"{_skeleton_expr(ctx, node)}",
        f"    if {skel_var} != null:",
        f"        var {idx_var}: int = {skel_var}.find_bone({bone_expr})",
        f"        if {idx_var} >= 0:",
        f"            var _gk_cam_rot: Vector3 = "
        f"({cam_var} as Node3D).global_rotation",
        f"            var _gk_want_yaw: float = clampf("
        f"-_gk_cam_rot.y * {gd_number(yaw_w)}, "
        f"-deg_to_rad({gd_number(limit)}), deg_to_rad({gd_number(limit)}))",
        f"            var _gk_want_pitch: float = clampf("
        f"-_gk_cam_rot.x * {gd_number(pitch_w)}, "
        f"-deg_to_rad({gd_number(limit)}), deg_to_rad({gd_number(limit)}))",
    ]
    if smooth > 0.0:
        t = (f"clampf({gd_number(smooth)} * "
             f"get_physics_process_delta_time(), 0.0, 1.0)")
        frame += [
            f"            {cur_yaw} = lerpf({cur_yaw}, _gk_want_yaw, {t})",
            f"            {cur_pitch} = lerpf({cur_pitch}, _gk_want_pitch, {t})",
        ]
    else:
        frame += [
            f"            {cur_yaw} = _gk_want_yaw",
            f"            {cur_pitch} = _gk_want_pitch",
        ]
    frame += [
        # 叠加:原姿势 × 想要的那点旋转(不覆盖动画自己的头部动作)
        f"            {skel_var}.set_bone_pose_rotation({idx_var}, "
        f"{skel_var}.get_bone_pose_rotation({idx_var}) * "
        f"Quaternion(Vector3.UP, {cur_yaw}) * "
        f"Quaternion(Vector3.RIGHT, {cur_pitch}))",
    ]
    ctx.physics_hooks.setdefault("bone_look_at_camera", []).extend(frame)


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
#
# 两种写法:
#   * 简单映射:输出名 -> 后缀(挂在 `base` 表达式后面)
#   * 自定义函数:自己生成代码并返回 {输出名: 表达式}
#     —— 需要先算中间变量的节点用它(比如"移动状态"要一次算速度、
#       离地、上升,三个输出共用那几个变量,分开算会重复生成三遍)
def expression_for_output(ctx, node, output_name):
    """带多输出的表达式节点。

    先看有没有**自定义函数**(能生成中间变量、一次算多个输出),
    再退回简单映射。
    """
    custom = MULTI_OUTPUT_FUNCS.get(node.bl_idname)
    if custom is not None:
        result = custom(ctx, node)
        if isinstance(result, dict):
            return result.get(output_name)
        return None
    mapping = MULTI_OUTPUT.get(node.bl_idname)
    if not mapping or output_name not in mapping:
        return None
    base = ctx.input_vector(node, "向量")
    return f"({base}){mapping[output_name]}"


# ==========================================================================
# 语句节点处理器
# ==========================================================================





@stmt_node("GKNodeMouseMode")
def _stmt_mouse_mode(ctx, node, indent):
    """鼠标模式:第一人称锁鼠标 / 菜单放出来。"""
    mode = {
        "VISIBLE": "MOUSE_MODE_VISIBLE",
        "HIDDEN": "MOUSE_MODE_HIDDEN",
        "CAPTURED": "MOUSE_MODE_CAPTURED",
        "CONFINED": "MOUSE_MODE_CONFINED",
    }.get(str(getattr(node, "gk_mode", "CAPTURED")), "MOUSE_MODE_CAPTURED")
    ctx.add_line(indent, f"Input.mouse_mode = Input.{mode}")


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
            # -- 同一个节点的同一个输入只声明一次(踩过的坑)------------
            # 多输出表达式节点(移动状态)按输出引脚求值,一个节点会被
            # 调用 8 次,每次都声明一遍 @export ->
            #     @export var move_threshold: float = 0.1
            #     @export var run_threshold: float = 3.0
            #     @export var move_threshold_2: float = 0.1   <- 重复
            #     @export var run_threshold_2: float = 3.0    <- 重复
            # 用户在检查器里改第一个,实际生效的可能是后面那个。
            cache = getattr(ctx, "_tunable_cache", None)
            if cache is None:
                cache = {}
                ctx._tunable_cache = cache
            key = (node_lib.node_key(node), socket_name)
            if key in cache:
                return cache[key]
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
            result = ctx.declare_export(base_name, "float", literal,
                                        f"{label} · {socket_name}")
            cache[key] = result
            return result
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
    """起跳:竖直速度给一个冲量。

    ── 2D 和 3D 的"上"是**反的**(2.5D 示例真跑抓出来的坑)──────────
    2D 里屏幕的上方是 **-Y**,3D 里世界的上方是 **+Y**。
    老代码一律写 `velocity.y = -强度` —— 3D 项目里这是**把角色往地里按**,
    而 `move_and_slide()` 会把它当"撞墙"直接把速度清零:
    表现就是"按了跳跃键完全没反应"(实测:插桩看到起跳那一行执行了、
    速度设成了 -6,下一帧就被地面挡回 0,角色一动不动)。

    2D 的写法不动(老图/2D 示例生成结果一个字符都不变),
    3D 走 `+强度`。
    """
    target = ctx.target_expr(node)
    force = _tunable_number(ctx, node, "强度", "400.0", "jump_force")
    if ctx.dim == 2:
        ctx.add_line(indent, f"{target}.velocity.y = -{force}")
    else:
        ctx.add_line(indent, f"{target}.velocity.y = {force}")


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


def _animation_name_of(ctx, node):
    """这个动画节点要播的名字(已经转成 GDScript 字符串字面量)。

    优先级:
      1. **引脚连线**(上游给了个动态名字)—— 最高,说明是运行时决定的
      2. **原生动作指针** `gk_anim_action` —— 用户在 Blender 里选的数据块
      3. 文本框 `gk_anim_name` —— 手填兜底
      4. 都没有 -> `""`,由调用方转成"随便播一个"
    """
    socket = ctx._socket(node, "动画名")
    if socket is not None and getattr(socket, "is_linked", False):
        return ctx.expr_of(socket)
    action = getattr(node, "gk_anim_action", None)
    if action is not None:
        name = str(getattr(action, "name", "") or "")
        if name:
            return gd_string(name)
    return ctx.input_string(node, "动画名", "")


def _scene_fps():
    """当前场景帧率 —— 把用户填的"帧"换算成 Godot 要的"秒"。

    Blender 里说"第 30 帧"很自然,Godot 的 `seek` / `play_section` 收的
    却是秒。两边差一个帧率,所以导出时换算一次。
    拿不到场景(离线测试)就用 Blender 的默认 24。
    """
    try:
        import bpy
        render = getattr(getattr(bpy.context, "scene", None), "render", None)
        fps = float(getattr(render, "fps", 0.0) or 0.0)
        if fps > 0:
            return fps
    except Exception:
        pass
    return 24.0


def _anim_category_table():
    """{分类: [动作名, ...]} —— 用户打过分类标签的动作。

    分类是用户在『动作分类』面板里给 Action 打的自定义属性
    (见 nodes.ANIM_CATEGORY_KEY)。导出时把它整理成一张表,
    Godot 侧就能按分类挑动作(随机/顺序),不用手写名字。

    测试桩里的 bpy 没有 .data,这里返回空 —— 于是老图、离线测试
    生成的代码**一模一样**(不打标签就一行都不生成)。
    """
    try:
        import bpy
    except Exception:
        return {}
    actions = getattr(getattr(bpy, "data", None), "actions", None)
    if actions is None:
        return {}
    table = {}
    for action in actions:
        category = action_category(action)
        if not category:
            continue
        table.setdefault(category, []).append(str(action.name))
    for names in table.values():
        names.sort()
    return table


@stmt_node("GKNodePlayAnim")
def _stmt_play_anim(ctx, node, indent):
    """播动画 + 三个事件出口(Started / Running / On Finish)。

    ── 为什么要事件出口 ────────────────────────────────────────────
    UPBGE 的 Play Animation 节点最值钱的就是这三个输出 ——
    有它们才能做"播完接下一招""循环期间持续判定"这类逻辑。
    以前我们的节点只有"完成",接不了任何后续判断。

    ── 三个出口分别怎么实现 ────────────────────────────────────────
      * **开始时** -> `AnimationPlayer.animation_started` 信号
      * **结束时** -> `AnimationPlayer.animation_finished` 信号
      * **每帧**   -> Godot **没有**对应信号,只能每个 `_process` 查一次
                     `is_playing()` + `current_animation`

    信号那两个都带 `animation_name` 参数,所以回调里比对一下名字,
    避免"别的代码播了同一个播放器上的另一个动画"时误触发。
    """
    player = ctx.animation_player(node)
    # ── 层(上下半身分离动画)────────────────────────────────────────
    # layer ≥ 1 时**另起一个播放器**:复制原播放器当"上层"。
    # 两个播放器指向同一个骨架,谁**后处理**谁覆盖 —— 复制出来的那个是
    # 后加的子节点,处理顺序在后面,正好是"上半身盖住下半身"。
    # 前提:动作得是**按部位拆开**的(上半身动作只含上半身骨骼),
    # 所以是"用户把上半身动作挂 1 层,下半身动作留 0 层"这种用法。
    layer = int(getattr(node, "gk_layer", 0) or 0)
    if layer >= 1:
        ctx.need_layer_player = True
        player = f"_gk_layer_player({player}, {layer})"
    anim = _animation_name_of(ctx, node)
    speed = gd_number(getattr(node, "gk_speed", 1.0))
    # ── Blend-In / 起始帧 / 结束帧 ──────────────────────────────────
    # 都以 0 / -1 为"不生效",老图生成的代码完全不变。
    blend = float(getattr(node, "gk_blend_in", -1.0) or 0.0)
    blend_expr = gd_number(blend) if blend > 0 else "-1.0"
    start_frame = float(getattr(node, "gk_start_frame", 0.0) or 0.0)
    end_frame = float(getattr(node, "gk_end_frame", 0.0) or 0.0)
    # Godot 收的是**秒**,Blender 里填的是**帧** —— 按场景帧率换算。
    fps = _scene_fps()
    start_sec = start_frame / fps if start_frame > 0 else 0.0
    end_sec = end_frame / fps if end_frame > 0 else 0.0
    # 『续播』= 同名动画正在播就不重新开始(避免连续触发时鬼畜重播)
    keep_playing = str(getattr(node, "gk_blend_mode", "REPLACE")) == "CONTINUE"
    name_temp = ctx.new_temp("anim")
    player_temp = ctx.new_temp("ap")
    ctx.add_line(indent, f"var {player_temp} := {player}")
    # ── 名字留空时**不要**生成 has_animation("")──────────────────────
    # 节点默认值改成空之后(见 nodes.py 的说明),用户不动它就会留下空串。
    # 直接生成 `has_animation("")` 是没意义的 —— 永远 false,
    # 只会打一条"动画不存在: "的空警告,用户还是一头雾水。
    #
    # 改成:留空 = "随便播一个"。很多模型身上只有一个动画
    # (用户的胡桃有俩,但单个动作的模型更常见),这样开箱即用。
    if anim == '""':
        # 让 generate() 记得生成 _gk_first_animation() 这个辅助函数。
        # **只在这条分支里设** —— 无条件设的话,
        # 填了名字的节点也会白白带一个用不到的辅助函数。
        ctx.need_first_animation = True
        ctx.add_line(indent, f"var {name_temp} := _gk_first_animation({player_temp})")
    else:
        ctx.add_line(indent, f"var {name_temp} := {anim}")

    # ── 先探测三个事件出口有没有接线,并登记 ────────────────────────
    # 顺序很重要:`_emit_event_connections` 是遍历 `ctx.anim_events`
    # 来写 connect 行的,登记晚了就一行都不写(实测踩过)。
    wanted = []
    for output_name, suffix in (("开始时", "started"),
                                ("每帧", "running"),
                                ("结束时", "finished")):
        if _next_nodes(node, output_name):
            wanted.append((output_name, suffix))
    # 每个出口一个**独立**编号 —— 曾经两个出口共用一个编号,
    # 生成出两个同名函数(一个覆盖另一个,用户的"结束时"逻辑直接消失)。
    numbered = []
    for output_name, suffix in wanted:
        unique = ctx.new_temp("ae")
        handler = f"_gk_{suffix}_{unique}"
        # 变量名:`_gk_running__ae3` -> `_gk_run__ae3`。
        # 不这么剥的话会拼出 `_gk_run__gk_running__ae3` 这种又长又脏的名字。
        plain = handler[len(f"_gk_{suffix}_"):]
        signal_name = None
        if suffix != "running":
            signal_name = ("animation_started" if suffix == "started"
                           else "animation_finished")
        var_name = f"_gk_run_{plain}"
        numbered.append((suffix, handler, signal_name, var_name))
        ctx.anim_events.append({
            "player": player_temp, "name": name_temp,
            "func": handler, "node": node, "output": output_name,
            "indent": indent,
            "kind": "running" if suffix == "running" else "signal",
            "signal": signal_name,
            "var": var_name,
        })
    if numbered:
        # 有任何事件出口就要生成信号回调函数
        ctx.need_anim_signals = True
        # "每帧"那个还需要在 _process 里插一段检查(Godot 没有对应信号)
        if any(suffix == "running" for suffix, _h, _s, _v in numbered):
            ctx.need_anim_running = True

    # ---- 播放 + 接线 ----
    ctx.add_line(indent, f"if {player_temp} != null and "
                         f"{player_temp}.has_animation({name_temp}):")
    # 『续播』模式:同名动画正在播就整段跳过(连信号都不重接)。
    body = indent + 1
    if keep_playing:
        ctx.add_line(indent + 1,
                     f"if {player_temp}.current_animation != {name_temp} "
                     f"or not {player_temp}.is_playing():")
        body = indent + 2
    if end_sec > 0:
        # 有结束帧 -> 用 play_section 一次把 起始/结束/Blend-In/速度 全给它。
        # (Godot 4.3+ 的 API:`play_section(名, 起, 止, 混合, 速度)`)
        ctx.add_line(body,
                     f"{player_temp}.play_section({name_temp}, "
                     f"{gd_number(start_sec)}, {gd_number(end_sec)}, "
                     f"{blend_expr}, {speed})")
    else:
        ctx.add_line(body,
                     f"{player_temp}.play({name_temp}, {blend_expr}, {speed})")
        if start_sec > 0:
            # 只设了起始帧、没设结束帧 -> play 之后 seek 到起点。
            # 第二个参数 true = 立刻刷新姿势,不然要等下一帧才跳过去。
            ctx.add_line(body,
                         f"{player_temp}.seek({gd_number(start_sec)}, true)")
    # ── 接线必须在**成功分支内**、紧挨着 play()(踩过的坑)──────────
    # 上一版把 connect 写在下面的 `else:` 里 —— 那是"动画不存在"的分支,
    # 于是信号只在**播放失败**时接上,正常播放永远不接。
    # 用 CONNECT_ONE_SHOT:触发一次自动断开,重复播放不会累积连接。
    for _suffix, handler, signal_name, _var in numbered:
        if signal_name is None:
            continue
        ctx.add_line(body,
                     f"{player_temp}.{signal_name}.connect({handler}, "
                     f"CONNECT_ONE_SHOT)")
    ctx.add_line(indent, "else:")

    # 动画不存在时的警告
    ctx.add_line(indent + 1, f"if {player_temp} == null:")
    ctx.add_line(indent + 2,
                 'push_warning("[GK] 没找到 AnimationPlayer,播不了动画: %s" % '
                 f"{name_temp})")
    ctx.add_line(indent + 1, "else:")
    # 名字填错时,把**可用的动画名**打出来。实测踩过:用户从 MMD
    # 导入的模型,动作叫『骨骼动作』,而节点里默认填 "idle" ——
    # 只报一句"动画不存在: idle",用户根本不知道正确的名字是什么。
    ctx.add_line(indent + 2,
                 'push_warning("[GK] 动画不存在: %s。这个模型里可用的动画有: %s" % '
                 f"[{name_temp}, {player_temp}.get_animation_list()])")


@multi_output("GKNodeAnimLocomotion")
def _expr_anim_locomotion(ctx, node):
    """移动相关的一组布尔判断,一次给全。

    -- 为什么要这个节点 --------------------------------------------
    动画切换里 90% 的条件就是那几个:"到地面了吗""在动吗""在往下掉吗"。
    没有它的话用户要为每个条件单独拉一个表达式节点再接引脚,
    状态一多整张图全是线。

    -- 为什么**不生成中间变量**(踩过的坑)--------------------------
    第一版为了不重复算速度,生成了 4 行中间变量。结果导出的代码报:
        Identifier "_lk2_speed" not declared in the current scope
    因为表达式是在 _ready() 的**局部作用域**里求值的,而这些表达式
    被 _physics_process 里的状态判断引用 —— 跨函数了。

    而且按输出引脚求值会被调用 8 次,每次都要一个不同的临时名,
    8 组变量散在代码里也很难看。

    所以改成**全部内联**:每个输出就是一个纯表达式,不落地成变量。
    代价是速度会被算多次,但 Vector2(...).length() 是纳秒级操作,
    每帧几次完全无所谓 —— 换来的是生成的代码干净、且不会作用域串味。
    """
    # -- 声明基类需求(踩过的坑)----------------------------------
    # 这个节点用 `is_on_floor()` 和 `velocity` -- **只有 CharacterBody
    # 才有**。用户实测:物体是静态的 -> 脚本 `extends StaticBody3D`
    # -> Godot 报 `Function "is_on_floor()" not found in base self.`
    #
    # 登记下来,导出时统一校验并纠正(见 check_base_class)。
    ctx.require_base_class(
        "CharacterBody2D" if ctx.dim == 2 else "CharacterBody3D",
        "『动画判断_移动状态』要用 is_on_floor() / velocity")
    target = ctx.target_expr(node)
    # 未连线的数值 -> @export 参数,方便在 Godot 检查器里调手感
    slow = _tunable_number(ctx, node, "速度阈值", "0.1", "move_threshold")
    fast = _tunable_number(ctx, node, "跑的速度阈值", "3.0", "run_threshold")
    if ctx.dim == 2:
        speed = f"Vector2({target}.velocity.x, 0.0).length()"
    else:
        speed = (f"Vector2({target}.velocity.x, "
                 f"{target}.velocity.z).length()")
    ground = f"{target}.is_on_floor()"
    moving = f"({speed} > {slow})"
    up = f"({target}.velocity.y > 0.0)"
    return {
        "在地面": f"({ground})",
        "在移动": f"({moving})",
        "地面且移动": f"(({ground}) and {moving})",
        "已离地": f"(not ({ground}))",
        "在上升": f"({up})",
        "在下落": f"(not {up})",
        "速度快": f"({speed} > {fast})",
        "速度慢": f"({speed} <= {fast})",
    }


# ==========================================================================
# 动画树的自定义变量(侧栏『变量』那一栏 / 取变量 / 设变量节点)
# ==========================================================================
# 变量在 Blender 侧存在动画树上(见 properties.GKAnimVariable),
# 导出后就是脚本里的一个成员变量。这里负责三件事:
#   1. 把侧栏里写的类型/初值翻成 GDScript(`var hp: float = 100.0`)
#   2. 使用这张动画树时,**所有**声明的变量都落地(哪怕还没节点用它)
#   3. 『动画_取变量』求值成变量名,『动画_设变量』生成一行赋值
_ANIM_VAR_TYPES = {
    "bool": ("bool", "false"),
    "int": ("int", "0"),
    "float": ("float", "0.0"),
    "string": ("String", '""'),
    "vector": ("Vector3", "Vector3.ZERO"),
}


def _anim_var_spec(tree, name):
    """在动画树的变量表里按名字找一个变量(找不到返回 None)。"""
    for var in (getattr(tree, "gk_variables", ()) or ()):
        if str(getattr(var, "var_name", "") or "") == name:
            return var
    return None


def _anim_var_initial(kind, text):
    """把侧栏里填的『初值』文本转成合法的 GDScript 字面量。

    用户填的是自由文本(和『比较』节点上的 A/B 一个道理),
    所以这里按类型各自做一次转换;转不动就退回该类型的零值,
    绝不让"文本里多打一个字符"变成 Godot 的语法错误。
    """
    raw = str(text or "").strip()
    if kind == "bool":
        return "true" if raw.lower() in ("1", "true", "真", "是", "yes") else "false"
    if kind == "int":
        try:
            return str(int(float(raw)))
        except Exception:
            return "0"
    if kind == "float":
        try:
            return gd_number(float(raw))
        except Exception:
            return "0.0"
    if kind == "vector":
        return raw or "Vector3.ZERO"
    return gd_string(str(text or ""))


def _anim_var_ref(ctx, node):
    """把一个『动画_取变量 / 设变量』节点解析成成员变量名。"""
    raw = str(getattr(node, "gk_var_name", "") or "").strip()
    if not raw:
        ctx.warn(f"节点『{node.bl_label}』还没选变量 —— "
                 f"先在侧栏『变量』里加一个,再点它那行的『取』/『设』"
                 f"(或者用节点上的放大镜挑)")
        return "null"
    spec = _anim_var_spec(getattr(node, "id_data", None), raw)
    if spec is None:
        # 侧栏里没声明过这个名字 —— 退回"按名字自动声明"的老行为,
        # 让手工打的名字也还能用(只是没有类型和初值)
        return ctx.declare_var(raw)
    kind = str(getattr(spec, "var_type", "float") or "float")
    gd_type, fallback = _ANIM_VAR_TYPES.get(kind, ("Variant", "null"))
    initial = _anim_var_initial(kind, getattr(spec, "default_value", ""))
    return ctx.declare_var(raw, gd_type, initial=initial or fallback)


def _declare_anim_vars(ctx, tree):
    """把一张动画树里声明的变量**全部**落成成员变量。

    和 `_anim_var_ref` 的区别:这里不管有没有节点引用 ——
    侧栏里写下的变量就该在脚本里存在(和蓝图一样,变量属于这一"个"
    动画蓝图,不是"被谁用到才存在")。
    """
    for spec in (getattr(tree, "gk_variables", ()) or ()):
        raw = str(getattr(spec, "var_name", "") or "").strip()
        if not raw:
            continue
        if not raw.isascii():
            # GDScript 的标识符只认 ASCII —— 中文名会被压成下划线。
            # 用户在这套中文界面里几乎一定会填中文名,不提醒的话
            # 导出结果里只会剩个 `var v`,而且多个中文名会挤成同一个变量。
            ctx.warn(f"动画树变量『{raw}』不是英文名 —— GDScript 不支持中文"
                     f"变量名,导出后会写成 `{snake_case(raw, 'v')}`。"
                     f"请在侧栏『变量』里改成英文(比如 hp、is_invincible)")
        kind = str(getattr(spec, "var_type", "float") or "float")
        gd_type, fallback = _ANIM_VAR_TYPES.get(kind, ("Variant", "null"))
        initial = _anim_var_initial(kind, getattr(spec, "default_value", ""))
        ctx.declare_var(raw, gd_type, initial=initial or fallback)


@expr_node("GKNodeAnimFinished")
def _expr_anim_finished(ctx, node):
    """某段动画播完了吗 —— 给转场条件 / 玩法图用。

    值来自 `animation_finished` 信号记下的名字(见 anim_finished_flag):
    动画名留空 = 只要有任意动画播完就算真 —— 这样用户不填也能用。
    """
    finished_var, _player = ctx.connect_anim_finished()
    action = getattr(node, "gk_anim_action", None)
    name = str(getattr(action, "name", "") or "") if action is not None else ""
    if not name:
        name = str(getattr(node, "gk_anim_name", "") or "").strip()
    if not name:
        return f"(not {finished_var}.is_empty())"
    return f"({finished_var} == {gd_string(name)})"


@stmt_node("GKNodeTweenProperty")
def _stmt_tween_property(ctx, node, indent):
    """把某个节点的某个属性在时间里推过去 —— 时间轴的一条轨道。

    ── 用 Godot 的 Tween,而不是自己写每帧插值 ─────────────────────
    `create_tween().tween_property(node, "fov", 30.0, 0.8)` 一句话搞定,
    而且 Tween 是**异步**的:调用完立刻返回,曲线在后台自己跑。
    这意味着"几条缓动同时进行"是**天然的**,不需要线程/协程
    —— 用户要的"不同动画的整体联动"就是这么拼出来的。
    """
    target = ctx.target_expr(node, "gk_target")
    prop = (getattr(node, "gk_property", "") or "").strip()
    if not prop:
        ctx.warn(f"『{node.bl_label}』没填『属性』,这一句被跳过了"
                 f"(要动相机视野就填 fov,动位置填 position 或 position:y)")
        return
    socket = ctx._socket(node, "到")
    if socket is not None and getattr(socket, "is_linked", False):
        to_expr = ctx.expr_of(socket)
    else:
        to_expr = _tunable_number(ctx, node, "到", "0.0", "tween_to")
    duration = _tunable_number(ctx, node, "时长", "0.5", "tween_time")
    trans = getattr(node, "gk_trans", "TRANS_SINE") or "TRANS_SINE"
    ease = getattr(node, "gk_ease", "EASE_IN_OUT") or "EASE_IN_OUT"

    tween_var = ctx.new_temp("tween")
    ctx.add_line(indent, f"var {tween_var} = create_tween()")
    ctx.add_line(indent, f"{tween_var}.set_trans(Tween.{trans})")
    ctx.add_line(indent, f"{tween_var}.set_ease(Tween.{ease})")
    ctx.add_line(indent, f"if {target} != null:")
    ctx.add_line(indent + 1,
                 f"{tween_var}.tween_property({target}, "
                 f"{gd_string(prop)}, {to_expr}, {duration})")
    if getattr(node, "gk_snap", True):
        # 浮点插值结束时可能差 0.0001 —— 做"门完全关上""视野正好 30"这类
        # 判定时会差一点点,所以在下一句补一个精确赋值。
        # `set_index` 让这句**等前面那条缓动跑完**再执行(Tween 的链式用法)。
        ctx.add_line(indent, f"{tween_var}.set_parallel(false)")
        ctx.add_line(indent, f"{tween_var}.chain().tween_callback("
                             f"func(): {target}.set({gd_string(prop)}, "
                             f"{to_expr}))")


@multi_output("GKNodeAnimStatus")
def _expr_anim_status(ctx, node):
    """『动画_运行状态』:把动画图的布尔情况交给玩法图判断。

    三个输出一次给全(用 multi_output,和『动画判断_移动状态』同一个做法)——
    这样同一个节点上取三个值也不会重复求值。
    """
    state_var = ctx.declare_var(
        snake_case(getattr(node, "gk_state_var", "anim_state"), "anim_state"),
        "String")
    # 闸门变量按**状态变量名**取 —— 有两台状态机时,
    # 读 `anim_state_2` 的那台就该看 `_gk_anim_done_2`,不能看错人家的
    done = ctx.declare_var("_gk_anim_done", "bool", initial="false",
                           key=state_var)
    started = ctx.declare_var("_gk_anim_started", "bool", initial="false",
                              key=state_var)
    return {
        "已结束": done,
        "在跑": started,
        "当前状态": state_var,
    }


@expr_node("GKNodeAnimGetVar")
def _expr_anim_get_var(ctx, node):
    return _anim_var_ref(ctx, node)


@stmt_node("GKNodeAnimSetVar")
def _stmt_anim_set_var(ctx, node, indent):
    name = _anim_var_ref(ctx, node)
    socket = ctx._socket(node, "值")
    if socket is not None and getattr(socket, "is_linked", False):
        value_expr = ctx.expr_of(socket)
    else:
        value = node.input_value(ctx, "值", None)
        value_expr = gd_literal(value) if value is not None else "null"
    ctx.add_line(indent, f"{name} = {value_expr}")


def _emit_state_hooks(ctx, graph, player_temp):
    """给每个"有事要做"的状态生成『进入时』『离开时』函数。

    返回 ({状态名: 进入函数名}, {状态名: 离开函数名})。

    一个状态需要函数,只要满足任意一条:
      * 『进入时』引脚接了线(用户要在这个状态里执行语句)
      * 『离开时』引脚接了线
      * 它填了『同时播 2 / 同时播 3』(要额外开播放器放那两段动画)
    """
    enter_hooks = {}
    exit_hooks = {}
    max_layers = 0
    for state in graph["states"]:
        name = node_lib.anim_state_name(state)
        if not name:
            continue
        extras = []
        getter = getattr(state, "extra_animation_names", None)
        if callable(getter):
            extras = [extra for extra in getter() if extra and extra != name]
        has_enter = bool(node_lib.link_targets(state, "进入时"))
        has_exit = bool(node_lib.link_targets(state, "离开时"))
        if not (has_enter or has_exit or extras):
            continue
        safe = snake_case(name, "state")

        if has_enter or extras:
            safe = snake_case(name, "state")
            with ctx.capture() as lines:
                ctx._active.clear()
                ctx._current_event = f"状态{name}进入时"
                for index, extra in enumerate(extras):
                    # 同时播的第 N 段:开一个"层"播放器(Godot 的 AnimationPlayer
                    # 一次只能播一段,play() 会把上一段顶掉)
                    layer = index + 1
                    max_layers = max(max_layers, layer)
                    ctx.need_layer_player = True
                    temp = ctx.new_temp("apx")
                    lines.add(1, f"var {temp} = _gk_layer_player({player_temp}, "
                                 f"{layer})")
                    lines.add(1, f"if {temp} != null and "
                                 f"{temp}.has_animation({gd_string(extra)}):")
                    lines.add(2, f"{temp}.play({gd_string(extra)})")
                if has_enter:
                    _chain(ctx, state, 1, "进入时")
                if len(lines) == 0:
                    lines.add(1, "pass")
            func_name = ctx.declare_func_name(f"_gk_anim_enter_{safe}")
            ctx.extra_sections.append({
                "signature": f"func {func_name}() -> void:",
                "lines": list(lines.lines),
                "empty_body": "    pass",
                "separator": True,
            })
            enter_hooks[name] = func_name

        if has_exit:
            safe = snake_case(name, "state")
            with ctx.capture() as lines:
                ctx._active.clear()
                ctx._current_event = f"状态{name}离开时"
                _chain(ctx, state, 1, "离开时")
                if len(lines) == 0:
                    lines.add(1, "pass")
            func_name = ctx.declare_func_name(f"_gk_anim_exit_{safe}")
            ctx.extra_sections.append({
                "signature": f"func {func_name}() -> void:",
                "lines": list(lines.lines),
                "empty_body": "    pass",
                "separator": True,
            })
            exit_hooks[name] = func_name
    # 记住最多有几层"同时播",_gk_anim_enter 里要按这个数量停旧层
    ctx._anim_layer_count = max_layers
    return enter_hooks, exit_hooks


def _start_condition(ctx, graph, node_lib):
    """『动画_开始』的开始条件表达式(没有开始口就返回 None)。

    ── 为什么开始条件要有"可选"的写法(用户 2026-10-03)──────────────
    用户原话:"游戏动画开始时的条件可自定义,如发生什么,一个变量,
    一个动画结束,或者是自定义开始动画条件"。
    所以这里按『开始方式』分派:立刻 / 接引脚 / 变量为真 / 刚按下 / 按住 /
    上一个动画播完。后四种都不用接线,直接在节点上选。
    """
    start = graph.get("start") if graph else None
    if start is None:
        return None
    mode = str(getattr(start, "gk_start_mode", "NOW"))
    if mode == "NOW":
        return "true"
    if mode == "PIN":
        socket = ctx._socket(start, "条件")
        if socket is None or not getattr(socket, "is_linked", False):
            ctx.warn("『动画_开始』的开始方式是『自己写』,但『条件』引脚"
                     "没接线 —— 这张动画图不会开始。接一个判断节点上去,"
                     "或者把开始方式改成『立刻开始』")
            return None
        return ctx.expr_of(socket)
    if mode == "VAR":
        name = str(getattr(start, "gk_var_name", "") or "").strip()
        if not name:
            ctx.warn("『动画_开始』选了『变量为真』但没填变量名")
            return None
        return ctx.declare_var(name, "bool")
    if mode in ("ACTION_JUST", "ACTION_PRESSED"):
        action = str(getattr(start, "gk_cond_action", "") or "").strip()
        if not action:
            ctx.warn("『动画_开始』选了按键开始但没填输入动作")
            return None
        const = ctx.add_action(action)
        func = ("is_action_just_pressed" if mode == "ACTION_JUST"
                else "is_action_pressed")
        return f"Input.{func}({const})"
    if mode == "FINISH":
        finished_var, _player = ctx.connect_anim_finished()
        name = str(getattr(start, "gk_anim_name", "") or "").strip()
        if not name:
            return f"(not {finished_var}.is_empty())"
        return f"({finished_var} == {gd_string(name)})"
    return "true"


@stmt_node("GKNodeAnimStateMachineRef")
def _stmt_anim_tree_ref(ctx, node, indent):
    """驱动一张**动画树** —— 玩法图和动画图分开放。

    见 nodes.py 里 GKNodeAnimState 那段的说明(用户要求照虚幻 5 的
    Animation Blueprint 那样拆开,别都堆在一个节点里)。

    ── 这一版改了什么(2026-10-03)────────────────────────────────
    老实现是"从入口状态沿一条链走一圈",只有三个能工作的形状:
    一条直链、一个环、或者什么都没有。用户实际的图(状态1 的『转到』
    直接连到状态2 的『进入』)导出后只剩一句 `pass`。现在改成**图**:

        entry --播完--> B                (状态直连,见 nodes.anim_tree_graph)
        A --[转场:条件]--> B             (转场节点)
        A --[转场:按住某键]--> C         (同一个状态多条出线)

    生成的形状:

        func _gk_anim_enter(next_state: String) -> void:
            anim_state = next_state
            ...真的 play()

        func _physics_process(_delta):
            if anim_state == "A":
                if <条件1>: _gk_anim_enter("B")
                elif <条件2>: _gk_anim_enter("C")
            elif anim_state == "B":
                ...

    三个关键点:
      * **入口状态真的会播** —— 老代码只写 `anim_state = "骨骼动作"`,
        从头到尾没有一处 play(),所以角色进游戏后一直摆着不动。
      * **同一个状态多条出线** —— 按画布上下顺序 if/elif,第一条成立就切。
      * **直连 = 播完就切** —— 用户就是这么连的(见 nodes.anim_tree_graph)。
    """
    tree = node_lib.find_animation_tree(getattr(node, "gk_tree_name", ""))
    # 这张动画树上声明的自定义变量全部落地成成员变量 ——
    # 即使暂时还没有『动画_取变量』节点引用它。侧栏里写下的东西
    # 必须在导出的脚本里真实存在,否则用户在 Godot 里根本找不到它。
    if tree is not None:
        _declare_anim_vars(ctx, tree)
    graph = node_lib.anim_tree_graph(tree) if tree is not None else None
    if graph is None or graph["entry"] is None:
        ctx.warn(f"节点『{node.bl_label}』找不到可用的动画状态机"
                 f"{'『' + node.gk_tree_name + '』' if node.gk_tree_name else ''}"
                 f"。先在『游戏动画』面板里建一张动画树,放一个『动画_状态』"
                 f"(没有任何转场指向它 = 起始状态),再把它连到下一个状态"
                 f"(或者中间放一个『动画_转场』写条件)")
        return
    # 树自己解析出来的问题,一条条转成导出告警 ——
    # 用户看的是导出报告,所以这里必须**原样带出去**,而不是只画在面板上。
    for problem in graph["problems"]:
        ctx.warn(problem)

    # ── 播放器:一定是**脚本所在物体**上的那个 ────────────────────────
    # 用户 2026-10-03 要求删掉『动画_状态机』的物体选择("在此物体选择
    # 动画_状态机时自然是作用于本身")。所以这里 use_target=False ——
    # 连老文件里存着的那个已废弃物体名也不认,免得它偷偷复活,
    # 生成一条指向别的物体的路径。
    player = ctx.animation_player(node, use_target=False)
    # ── 状态变量**按节点**独立(用户 2026-10-03:"一个游戏逻辑里是否可以
    #    有多个游戏动画?")────────────────────────────────────────────
    # 以前**不行**:两张动画树都写进同一个 `anim_state`、共用同一个
    # `_gk_anim_enter`,后一张把前一张整个盖掉 ——
    # 实测:两个『动画_状态机』节点只生成了一套状态机。
    # 按节点当 key 声明之后,第二台自动叫 `anim_state_2`,互不干扰。
    # ── 状态变量:按**名字**共享(而不是自动加序号)──────────────────
    # 试过"每台状态机自动 anim_state / anim_state_2":两台机器确实不打架了,
    # 但『动画_运行状态』(按名字读 anim_state)就对不上了 —— 它读到的是
    # 别人那一份,状态永远显示错。
    #
    # 现在:名字就是身份。一台机器占一个名字;第二台还想用同一个名字时
    # **明确告警**,告诉用户去改『状态变量名』(比如 ui_state)。
    # 这样"一台机器 + 一个读取节点"这种 99% 的用法永远是对的,
    # 多台的时候也只需要用户动一次手、而且看得见原因。
    requested = snake_case(getattr(node, "gk_state_var", "anim_state"),
                           "anim_state")
    owners = getattr(ctx, "_state_var_owners", None)
    if owners is None:
        owners = {}
        ctx._state_var_owners = owners
    mine = node_lib.node_key(node)
    if requested in owners and owners[requested] != mine:
        ctx.warn(f"有两台『动画_状态机』都用『状态变量名 = {requested}』—— "
                 f"它们会互相覆盖(动画会乱跳)。把其中一台的『状态变量名』"
                 f"改个名字(比如 ui_state),然后『动画_运行状态』也填同一个")
    owners[requested] = mine
    var_name = ctx.declare_var(requested, "String")
    # -- 播放器引用必须是**成员变量**,不能是局部变量(踩过的坑)------
    # 状态判断跑在 _physics_process 里,而 _ready 里也用它。
    # 用 new_temp()(局部变量)会炸:
    #     Identifier "_ap1" not declared in the current scope
    # 实测用户导出后报了这个错。
    #
    # 而且只在 _ready 里赋值一次就够了(它不会变),
    # 所以赋值语句只在 _ready 那次执行时生成,避免每个物理帧都查一遍。
    player_temp = ctx.anim_player_member()
    if not getattr(ctx, "_anim_player_assigned", False):
        ctx._anim_player_assigned = True
        # 用 `=` 不用 `:=`:成员变量已经在类顶部声明过 `var _ap0 = null`,
        # 再写 `var _ap0 := ...` 就是**同一作用域里重复声明**,
        # Godot 会报 "There is already a variable named ..."。
        #
        # 带 `if == null` 的守卫是为了和 generate() 里补的那次赋值**幂等**:
        # 用户没接『动画_播放完成』时只有这里赋一次;接了的话 _ready 里
        # 还会补一次(那时玩家图里可能先用到它),两次都不该重复赋值。
        ctx.add_line(indent, f"if {player_temp} == null:")
        ctx.add_line(indent + 1, f"{player_temp} = {player}")

    # ── "上游动画播完"要靠信号,不能靠 current_animation(实测踩过)────
    # 详见 anim_finished_flag 里的实测数据。只有图里真的有"播完"边
    # 才生成这套东西(没用到就不占行数)。
    has_finish = any(edge["kind"] == node_lib.ANIM_EDGE_FINISH
                     for edge in graph["edges"])
    finished_var = None
    if has_finish:
        tree_name = getattr(tree, "name", "动画树")
        # 只声明标志;信号连接由 generate() 统一补进 _ready(见那里的说明)
        finished_var = ctx.anim_finished_flag(tree_name)[0]

    # ── 状态自己的执行流 + 同时播多段动画(2026-10-03 新增)──────────
    # 每个"有事要做"的状态生成一个函数:
    #     _gk_anim_enter_攻击()   ← 『进入时』接的语句 + 同时播的第 2/3 段动画
    #     _gk_anim_exit_攻击()    ← 『离开时』接的语句
    # 然后 _gk_anim_enter() 里按状态名分派(match)。
    #
    # ── 为什么不把语句直接内联进 _gk_anim_enter ──────────────────
    # 那几个分支的长度完全由用户的图决定,内联会让"进入状态"这个唯一入口
    # 变成一大坨;拆成函数之后,每个状态自己的逻辑在脚本里也是**分开的一块**。
    enter_hooks, exit_hooks = _emit_state_hooks(ctx, graph, player_temp)

    debug = bool(getattr(node, "gk_debug_print", False))
    # ── 进入状态统一走一个函数 ──────────────────────────────────────
    # 老代码每处切换都内联四行(`if has_animation` / `play` / `else` /
    # `push_warning`),想加一个"进入时打印"要改 N 处。收成一个函数之后,
    # 状态切换只有**一个**生成点,而且"进入状态"这件事有了唯一的定义。
    enter_func = ctx.enter_anim_state_func(node, var_name, player_temp, debug,
                                           finished_var,
                                           enter_hooks=enter_hooks,
                                           exit_hooks=exit_hooks)

    # 起始状态 = 图里**没有任何边指向**的那个状态(照 UE5 的 Entry 节点);
    # 放了『动画_开始』就以它连的那个为准(显式指定,不用猜)。
    entry_name = node_lib.anim_state_name(graph["entry"])
    # ── 开始口 / 结束口(用户 2026-10-03)──────────────────────────
    # 有这两个口之一时,这张图变成"有始有终"的:条件成立才开始,
    # 走到『动画_结束』就停下并把『已结束』置真 —— 玩法图用
    # 『动画_运行状态』读它,接着做下一步逻辑。
    # 两个口都没有的老图**生成结果一字不变**(见下面的 else 分支)。
    start_node = graph.get("start")
    start_expr = _start_condition(ctx, graph, node_lib)
    has_start = start_node is not None and start_expr is not None
    has_exit = any(edge["kind"] == node_lib.ANIM_EDGE_EXIT
                   for edge in graph["edges"])
    started = done = None
    finish_func = None
    if has_start or has_exit:
        # 闸门变量也按状态变量区分 —— 否则两台机器会互相把对方的
        # "开始/结束"标志清掉(一个播完另一个也跟着收工)
        started = ctx.declare_var("_gk_anim_started", "bool",
                                  initial="false", key=var_name)
        done = ctx.declare_var("_gk_anim_done", "bool",
                               initial="false", key=var_name)
        if has_exit:
            finish_func = ctx.anim_finish_func(started, done, var_name,
                                               player_temp, graph)
    if has_start:
        # 条件每物理帧判一次 —— 一开始就判的话,条件当时不成立就再也没机会
        ctx.anim_starts.append({
            "node": node, "expr": start_expr, "var": started, "done": done,
            "enter": enter_func, "entry": entry_name,
        })
        ctx.need_anim_start_process = True
    elif has_exit:
        # 没有开始口但有结束口:第一帧直接开演,但**演完不会自己重来**
        # (要有开始口 + 条件才会重来)
        ctx.add_line(indent, f"if not {started} and not {done}:")
        ctx.add_line(indent + 1, f"{started} = true")
        ctx.add_line(indent + 1, f"{done} = false")
        ctx.add_line(indent + 1, f"{enter_func}({gd_string(entry_name)})")
    else:
        # 两个口都没有(老图):一进游戏就播起始状态,和以前一模一样
        ctx.add_line(indent, f"if {var_name}.is_empty():")
        ctx.add_line(indent + 1, f"{enter_func}({gd_string(entry_name)})")

    if str(getattr(node, "gk_check_mode", "EVERY_FRAME")) == "EVERY_FRAME":
        # 交给 generate() 统一发射到 _physics_process —— 跟移动同步判定,
        # 避免"人走出去了状态还没切"的一帧延迟(和相机跟随同一个道理)。
        ctx.anim_trees.append({
            "node": node, "graph": graph, "var": var_name,
            "player": player_temp, "enter": enter_func, "debug": debug,
            "finished": finished_var, "gate": started,
            "finish": finish_func,
        })
        ctx.need_anim_tree_process = True
        return
    _emit_state_graph(ctx, indent, graph, var_name, player_temp, enter_func,
                      finished_var, finish_func, started)


def _find_animation_tree(name):
    """按名字找一张动画树;名字为空就用文件里第一张。

    判断只有一份,在 `nodes.find_animation_tree` —— 面板、节点上的预览、
    导出三处必须看到同一张树,不然会出现"面板说用 A、导出却用了 B"。
    """
    return node_lib.find_animation_tree(name)


def _state_label(state):
    """状态显示名,给告警用。

    真名判断在 `nodes.anim_state_name`(优先用选中的动作名,否则用手填的)——
    这里保留一个同名的转发,是因为早期写下的诊断脚本/测试都在调它。
    """
    return node_lib.anim_state_name(state) or getattr(state, "bl_label", "?")


def _anim_tree_entry(tree):
    """找状态机的**入口状态** —— 照 UE5 的 Entry 节点。

    -- 判断规则:**没有任何一条转场指到**的状态才是入口 ------------
    (老注释里写的"『进入』引脚没接线就是入口"是错的:环里的状态
    同时满足两个身份,实测 `idle -> run -> idle` 里 run 也被判成了入口。)

    保留这个函数是因为侧栏面板和冒烟测试都在用它 —— 但判断本身已经
    挪进 `nodes.anim_tree_graph`,这里只负责取结果。
    """
    if tree is None:
        return None, []
    graph = node_lib.anim_tree_graph(tree)
    entry = graph["entry"]
    if entry is None:
        return None, []
    # 节点身份一律走 node_key(as_pointer),不能用 id()/is ——
    # 详见 nodes.node_key:同一个节点从连线拿到的是**另一个**包装对象。
    targeted = {node_lib.node_key(edge["to"]) for edge in graph["edges"]}
    named = [s for s in graph["states"] if node_lib.anim_state_name(s)]
    entries = [s for s in named
               if node_lib.node_key(s) not in targeted] or [entry]
    return entry, entries


def _edge_condition(ctx, edge, player_temp, finished_var=None):
    """一条边的条件表达式;算不出来返回 None。

    kind 决定从哪儿取:
      FINISH    -> 上游那段动画播完了(靠 animation_finished 信号,见
                   _anim_finished_helpers:查 current_animation 是不行的)
      ACTION_*  -> 节点上直接选的输入动作
      PIN       -> 接在『条件』引脚上的表达式
    """
    kind = edge.get("kind")
    if kind == node_lib.ANIM_EDGE_FINISH:
        source = node_lib.anim_state_name(edge["from"])
        if not source or not finished_var:
            return None
        # 信号回调里记下的名字 == 上游状态的名字 -> 就是"它播完了"
        return f"({finished_var} == {gd_string(source)})"
    if kind == node_lib.ANIM_EDGE_EXIT:
        # 走到『动画_结束』= 无条件(它就是"演完了"这个动作本身)
        return "true"
    trans = edge.get("transition")
    if trans is None:
        return None
    if kind in node_lib.ANIM_ACTION_SOURCES:
        action = str(getattr(trans, "gk_cond_action", "") or "").strip()
        if not action:
            return None
        const = ctx.add_action(action)
        return f"{node_lib.ANIM_ACTION_SOURCES[kind]}({const})"
    getter = getattr(trans, "condition_expr", None)
    if getter is None:
        return None
    return getter(ctx)


def _emit_state_graph(ctx, indent, graph, var_name, player_temp, enter_func,
                      finished_var=None, finish_func=None, gate=None):
    """把状态图生成成 if/elif 链。

    每个状态一段,状态内部按画布上下顺序判转移:

        if anim_state == "A":
            if <条件1>:
                _gk_anim_enter("B")
            elif <条件2>:
                _gk_anim_enter("C")

    -- 为什么状态内用 elif 而不是各自一个 if ------------------------
    "A 同时满足两条转移"必须有确定答案。虚幻 5 的语义是"从上往下第一个
    成立的赢",而且一帧只走一步 —— 用一串 if 的话同一帧能连跳三四个状态,
    表现是"一按键直接跳到最终状态、中间的动作一帧都没播"。
    """
    emitted = 0
    # ── 开始口 / 结束口的"闸门" ──────────────────────────────────────
    # 有开始口或结束口时,转场只在**"这段动画正在跑"**的时候判:
    # 还没开始 -> 不判;走到结束口了 -> 也不判(否则"结束"这个状态
    # 会立刻被别的转场拉走,用户根本等不到玩法图读到"已结束")。
    body_indent = indent
    if gate:
        ctx.add_line(indent, f"if {gate}:")
        body_indent = indent + 1
    for state in graph["states"]:
        name = node_lib.anim_state_name(state)
        if not name:
            continue
        state_key = node_lib.node_key(state)
        outgoing = [e for e in graph["edges"]
                    if node_lib.node_key(e["from"]) == state_key]
        if not outgoing:
            continue
        # 同一个状态里的边按画布顺序(上面的先判)。
        # 同一个节点驱动两次时顺序也必须稳定,所以排序键里带上目标名。
        outgoing.sort(key=lambda e: (e["order"],
                                     node_lib.anim_state_name(e["to"])))
        ctx.add_line(body_indent, f"{'if' if emitted == 0 else 'elif'} "
                                  f"{var_name} == {gd_string(name)}:")
        first = True
        for edge in outgoing:
            condition = _edge_condition(ctx, edge, player_temp, finished_var)
            if edge["kind"] == node_lib.ANIM_EDGE_EXIT:
                # 走到『动画_结束』:不切状态,而是**收工**
                if not condition or not finish_func:
                    continue
                ctx.add_line(body_indent + 1,
                             f"{'if' if first else 'elif'} {condition}:")
                ctx.add_line(body_indent + 2, f"{finish_func}()")
                first = False
                continue
            target = node_lib.anim_state_name(edge["to"])
            if not condition or not target:
                # 条件算不出来(比如『条件来源=自己写』但引脚没接)——
                # anim_tree_graph 已经报过原因,这里不重复刷告警。
                continue
            ctx.add_line(body_indent + 1,
                         f"{'if' if first else 'elif'} {condition}:")
            ctx.add_line(body_indent + 2, f"{enter_func}({gd_string(target)})")
            first = False
        if first:
            # 这一段一条转移都没生成出来 —— 留个 pass 免得语法错误
            ctx.add_line(body_indent + 1, "pass")
        emitted += 1
    if emitted == 0:
        ctx.add_line(body_indent, "pass")
    # ── 这里**不能**清 `_gk_anim_finished`(改过两次才对)──────────────
    # 第一版每帧判完就清,结果"(某动画播完了) 并且 (在地面上)"这种复合条件
    # 永远等不到:那一刻玩家还在空中,条件为假,标志却被清掉了,
    # 之后落地也不会再触发。现在标志**粘住**到下一段动画开播
    # (由 `_gk_on_anim_started` 信号和 `_gk_anim_enter` 负责清)。


@stmt_node("GKNodeAnimStateMachine")
def _stmt_anim_state_machine(ctx, node, indent):
    """动画状态机 —— 取代手拼分支网。

    ── 生成思路 ────────────────────────────────────────────────────
    不做"每个状态一个函数"那种展开(GDScript 里会很啰嗦),
    而是生成一段**紧凑的 if 链**:按状态表顺序逐条判断,

        if anim_state == "idle" and <条件引脚>:
            anim_state = "run"
            <切动画>

    这么做的理由:
      * 生成结果短、可读 —— 用户能直接看出哪条规则在生效
      * 状态表是**有序**的,优先级就是书写顺序(第一条命中的赢),
        这和 UPBGE / 蓝图里"从上往下第一个为真的分支"直觉一致
      * 不需要为每个状态生成函数,省掉一堆样板

    ── 检查时机 ────────────────────────────────────────────────────
    『每帧检查』-> 整段挂在 `_physics_process` 里
    『只在进入时判』-> 只在本次执行时判一次
    """
    rules = parse_transitions(getattr(node, "gk_transitions", ""))
    if not rules:
        ctx.warn(f"节点『{node.bl_label}』的状态表是空的,什么都不会发生。"
                 f"写一行像 `idle -> run` 这样的规则")
        return

    player = ctx.animation_player(node)
    # ── 状态变量**按节点**独立(用户 2026-10-03 的问题)──────────────
    # 用户问"一个游戏逻辑里是否可以有多个游戏动画?"—— 以前**不行**:
    # 两张动画树都写进同一个 `anim_state`、共用同一个 `_gk_anim_enter`,
    # 后一张把前一张整个盖掉(实测:两张树只生成一套状态机)。
    # 现在按节点 key 声明,第二个状态机自动叫 `anim_state_2`。
    var_name = ctx.declare_var(
        snake_case(getattr(node, "gk_state_var", "anim_state"), "anim_state"),
        "String", key=node_lib.node_key(node))
    player_temp = ctx.new_temp("ap")
    ctx.add_line(indent, f"var {player_temp} := {player}")
    # 首次执行时把状态设成"起始状态"
    start_state = ctx.input_string(node, "起始状态", "idle")
    ctx.add_line(indent, f"if {var_name}.is_empty():")
    ctx.add_line(indent + 1, f"{var_name} = {start_state}")

    every_frame = str(getattr(node, "gk_check_mode", "EVERY_FRAME")) == "EVERY_FRAME"
    if every_frame:
        # 整段逻辑要放进 _physics_process —— 由 ctx 收集,generate() 统一发射
        ctx.anim_state_machines.append({
            "node": node, "rules": rules, "var": var_name,
            "player": player_temp,
        })
        ctx.need_anim_state_process = True
        return

    # 只在进入时判:直接在这里展开一次
    _emit_state_transitions(ctx, node, indent, rules, var_name, player_temp)


def _emit_state_transitions(ctx, node, indent, rules, var_name, player_temp):
    """生成转移判断的 if 链。见 _stmt_anim_state_machine 的说明。"""
    for start, end in rules:
        pin_name = transition_pin_name(start, end)
        socket = ctx._socket(node, pin_name)
        if socket is None:
            # 状态表改了但引脚还没重建 —— 提示用户点一下节点
            ctx.warn(f"节点『{node.bl_label}』的规则 `{start} -> {end}` "
                     f"还没有对应的引脚。在节点上把状态表重新编辑一次"
                     f"(或重新加载文件)就会长出来")
            continue
        if not getattr(socket, "is_linked", False):
            # 没接条件 -> 这条规则永远不会触发,明确告诉用户
            ctx.warn(f"节点『{node.bl_label}』的规则 `{start} -> {end}` "
                     f"没有接条件,不会生效")
            continue
        condition = ctx.expr_of(socket)
        ctx.add_line(indent, f'if {var_name} == {gd_string(start)} and {condition}:')
        ctx.add_line(indent + 1, f'{var_name} = {gd_string(end)}')
        # 切状态 = 播目标状态的动画(状态名就是动画名)
        ctx.add_line(indent + 1, f"if {player_temp} != null and "
                                 f"{player_temp}.has_animation({gd_string(end)}):")
        ctx.add_line(indent + 2,
                     f"{player_temp}.play({gd_string(end)}, -1.0, 1.0)")
        ctx.add_line(indent + 1, "else:")
        ctx.add_line(indent + 2,
                     f'push_warning("[GK] 状态『{end}』没有对应的动画: %s" % '
                     f"{gd_string(end)})")


@stmt_node("GKNodeSetState")
def _stmt_set_state(ctx, node, indent):
    var_name = ctx.declare_var(getattr(node, "gk_var", "state"), "String")
    state = ctx.input_string(node, "状态", "idle")
    ctx.add_line(indent, f"{var_name} = {state}")


# ==========================================================================
# 动画:其余常用操作
# ==========================================================================
@expr_node("GKNodeAnimPlaying")
def _expr_anim_playing(ctx, node):
    """某个动画片段是否正在播放。

    Godot 的 AnimationPlayer 只有 `is_playing()`(当前有没有在播)和
    `current_animation`(当前播的是谁),没有"某个片段是否在播"。
    所以这里组合判断:留空动画名 = 只看 is_playing;
    填了名字 = 还要当前播的就是它。
    """
    player = ctx.animation_player(node)
    name = (getattr(node, "gk_anim_name", "") or "").strip()
    temp = ctx.new_temp("ap")
    ctx.add_line(ctx._expr_indent, f"var {temp} := {player}")
    if not name:
        return f"({temp} != null and {temp}.is_playing())"
    return (f"({temp} != null and {temp}.is_playing() "
            f"and {temp}.current_animation == {gd_string(name)})")


@stmt_node("GKNodeAnimPause")
def _stmt_anim_pause(ctx, node, indent):
    """暂停 / 继续当前动画。

    ── API 是实测出来的,不是猜的(踩过的坑)────────────────────────
    Godot 4.7 的 `AnimationPlayer` **没有 `paused` 属性** ——
    写了会报 "Invalid assignment of property 'paused'"。
    真实可用的是:
        pause()                 暂停(停在当前位置)
        play(name)              从当前位置继续(不再传名字就得重播)
        active / set_active()   开关整个播放器

    所以这里:
      * 暂停 -> `pause()`(位置保持不变)
      * 继续 -> 用 `current_animation` 重新 `play()`,从当前位置接着放
        (不能用 stop(),那会回到静止姿势)
    """
    player = ctx.animation_player(node)
    temp = ctx.new_temp("ap")
    pos_var = ctx.declare_var("_gk_anim_pause_pos", "float")
    ctx.add_line(indent, f"var {temp} := {player}")
    ctx.add_line(indent, f"if {temp} != null:")
    if getattr(node, "gk_paused", True):
        # 记下当前位置 —— 恢复时要 seek 回来
        ctx.add_line(indent + 1, f"{pos_var} = {temp}.current_animation_position")
        ctx.add_line(indent + 1, f"{temp}.pause()")
    else:
        # 恢复:`play()` 会把动画**从头开始**,所以要用 seek 回到暂停时的位置。
        # (实测:只调 play() 不 seek,动画会跳回开头)
        ctx.add_line(indent + 1, f"if not {temp}.current_animation.is_empty():")
        ctx.add_line(indent + 2, f"{temp}.play({temp}.current_animation)")
        ctx.add_line(indent + 2, f"{temp}.seek({pos_var}, true)")


@stmt_node("GKNodeAnimStop")
def _stmt_anim_stop(ctx, node, indent):
    player = ctx.animation_player(node)
    temp = ctx.new_temp("ap")
    ctx.add_line(indent, f"var {temp} := {player}")
    ctx.add_line(indent, f"if {temp} != null:")
    ctx.add_line(indent + 1, f"{temp}.stop()")


@stmt_node("GKNodeAnimSetSpeed")
def _stmt_anim_set_speed(ctx, node, indent):
    player = ctx.animation_player(node)
    speed = _tunable_number(ctx, node, "速度", "1.0", "anim_speed")
    temp = ctx.new_temp("ap")
    ctx.add_line(indent, f"var {temp} := {player}")
    ctx.add_line(indent, f"if {temp} != null:")
    ctx.add_line(indent + 1, f"{temp}.speed_scale = {speed}")


@expr_node("GKNodeAnimLength")
def _expr_anim_length(ctx, node):
    """动画片段时长(秒)。名字不存在时返回 0,不是崩溃。"""
    player = ctx.animation_player(node)
    name = ctx.input_string(node, "动画名", "idle")
    ap_temp = ctx.new_temp("ap")
    name_temp = ctx.new_temp("anim")
    ctx.add_line(ctx._expr_indent, f"var {ap_temp} := {player}")
    ctx.add_line(ctx._expr_indent, f"var {name_temp} := {name}")
    return (f"(0.0 if ({ap_temp} == null or not {ap_temp}.has_animation({name_temp})) "
            f"else {ap_temp}.get_animation({name_temp}).length)")


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



def _path_name(path):
    """从生成的节点引用表达式里取出**节点名**。

    `$"侧视机位"` / `$Camera` -> `视视机位` / `Camera`;
    不是这种形式的(比如 `self`)原样返回。
    """
    text = str(path or "").strip()
    if text.startswith("$"):
        text = text[1:].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("\"", "'"):
        text = text[1:-1]
    return text

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
        # ── "没填相机"就找**正在用的那台**(2.5D 示例真跑抓出来的坑)──────
        # 顺序:①节点上 if 指定的 ②视口里正在用的 ③gk_camera 分组
        #      ④场景里第一台
        #
        # ── 为什么走**函数**而不是嵌套三元(第 28 轮,Godot 调试器实证)──
        # 以前生成的是一行嵌套三元:
        #     _gk_cam_auto if _gk_cam_auto != null else
        #       (get_camera_3d() if ... else (get_camera_2d() if ... else
        #         get_first_node_in_group("gk_camera")))
        # 三个分支的类型是 Camera3D / Camera2D / Node —— Godot 报
        #     INCOMPATIBLE_TERNARY: Values of the ternary operator are not
        #     mutually compatible
        # (实测一次报 4 条,分属 130/134/138/141 行),而且那一行**算不出
        # 可用相机** -> 弹簧臂每帧都走"没找到相机"分支 -> 完全不生效。
        # 现在统一成 `_gk_resolve_camera(self)`,内部全是普通 if,返回 Node。
        ctx.need_resolve_camera = True
        ctx.note(f"『{node.bl_label}』没指定相机 —— 运行时自动用当前相机"
                 f"(单相机项目这样就行;想固定某台就在节点上选)")
        return "_gk_resolve_camera(self)", True
def _camera_node_expr(ctx, node, prop="gk_target", hint="相机"):
    """取"这台相机"的表达式(留空 = 运行时找当前相机)。

    ── 为什么区分"指定相机"和"当前相机"(照虚幻的习惯)──────────────
    虚幻里 Activate 是作用在**具体某个组件**上的,而"当前在用哪台"
    则是 ViewTarget。我们两种都给:填了就用填的(@onready 引用),
    留空就用 `get_viewport().get_camera_3d()/2d()` —— 单相机项目
    不用填任何东西就能用。
    """
    raw = str(getattr(node, prop, "") or "").strip()
    if raw and raw != TARGET_SELF:
        return ctx.target_expr(node, prop, hint)
    # ── 同样走**类型统一的函数**,不要嵌套三元(第 28 轮)───────────
    # 嵌套三元里的三个分支是 Camera3D / Camera2D / Node,
    # Godot 报 `INCOMPATIBLE_TERNARY: Values of the ternary operator are
    # not mutually compatible`(用户调试器里 4 条,130/134/138/141 行),
    # 而且那个表达式**算不出可用相机** -> 弹簧臂每帧"没找到相机"。
    ctx.need_resolve_camera = True
    ctx.note(f"『{node.bl_label}』没指定相机 —— 运行时自动用当前相机")
    return "_gk_resolve_camera(self)"


@stmt_node("GKNodeCameraActive")
def _stmt_camera_active(ctx, node, indent):
    """启用 / 禁用相机(照虚幻的 Activate / Deactivate)。"""
    camera = _camera_node_expr(ctx, node)
    temp = ctx.new_temp("cam")
    ctx.add_line(indent, f"var {temp} = {camera}")
    ctx.add_line(indent, f"if {temp} == null:")
    ctx.add_line(indent + 1, 'push_warning("[GK] 相机_启用:没找到相机")')
    if getattr(node, "gk_active", True):
        ctx.add_line(indent, f"else:")
        ctx.add_line(indent + 1, f"{temp}.make_current()")
    else:
        # 禁用 = 正开着才摘下来(不是它开着的话什么都不做,免得把别人的画面弄黑)
        ctx.add_line(indent, f"elif {temp} is Camera3D and "
                             f"({temp} as Camera3D).is_current():")
        ctx.add_line(indent + 1, f"({temp} as Camera3D).clear_current()")
        ctx.add_line(indent, f"elif {temp} is Camera2D and "
                             f"({temp} as Camera2D).is_current():")
        ctx.add_line(indent + 1, f"({temp} as Camera2D).clear_current()")


@stmt_node("GKNodeCameraSetup")
def _stmt_camera_setup(ctx, node, indent):
    """一次设好:视野 + 视距(+ 平滑)。

    ── 0 = **不动它**(用户 2026-10-03 真跑踩出来的坑)────────────────
    文档里写的就是"0 = 不动它",但代码是**无条件**写进去的 ——
    用户在节点上没填视野(默认 0),于是生成 `camera.fov = 0`:
    Godot 里 fov=0 等于镜头废掉,**整个画面就废了**,
    表现是"视角完全不对/看不到东西"。
    现在:没填(0)且没连线的项直接不生成,并且在导出报告里说清楚
    这次到底改了什么。

    平滑走 `create_tween()` —— 和『定时_缓动属性』同一个机制。
    """
    camera = _camera_node_expr(ctx, node)
    fov_socket = ctx._socket(node, "视野")
    dist_socket = ctx._socket(node, "视距")
    fov_linked = bool(fov_socket is not None
                      and getattr(fov_socket, "is_linked", False))
    dist_linked = bool(dist_socket is not None
                       and getattr(dist_socket, "is_linked", False))
    fov_value = float(getattr(node, "gk_fov", 0.0) or 0.0)
    dist_value = float(getattr(node, "gk_distance", 0.0) or 0.0)
    want_fov = fov_linked or abs(fov_value) > 0.0001
    want_dist = dist_linked or abs(dist_value) > 0.0001
    smooth = float(getattr(node, "gk_smooth", 0.0) or 0.0)
    if not want_fov and not want_dist:
        ctx.warn("『相机_设置』什么都没设(视野 0、视距 0)—— 它不会改任何"
                 "东西。想改视野就填一个角度(比如 55),想推远就填视距;"
                 "两个都不填的话,这个节点可以删掉")
        return
    temp = ctx.new_temp("cam")
    ctx.add_line(indent, f"var {temp} = {camera}")
    ctx.add_line(indent, f"if {temp} == null:")
    ctx.add_line(indent + 1, 'push_warning("[GK] 相机_设置:没找到相机")')
    ctx.add_line(indent, f"else:")
    body = indent + 1
    setter = None
    if smooth > 0.0:
        tween = ctx.new_temp("tween")
        ctx.add_line(body, f"var {tween} = create_tween()")
        ctx.add_line(body, f"{tween}.set_trans(Tween.TRANS_SINE)")
        ctx.add_line(body, f"{tween}.set_ease(Tween.EASE_IN_OUT)")
        setter = f"{tween}.tween_property"
    if want_fov:
        fov = ctx.input_number(node, "视野", "0.0")
        ctx.add_line(body, f"if {temp} is Camera2D:")
        if setter:
            ctx.add_line(body + 1,
                         f"{setter}({temp}, \"zoom\", Vector2({fov}, {fov}), "
                         f"{gd_number(smooth)})")
        else:
            ctx.add_line(body + 1, f"{temp}.zoom = Vector2({fov}, {fov})")
        ctx.add_line(body, f"elif {temp} is Camera3D:")
        if setter:
            ctx.add_line(body + 1,
                         f"{setter}({temp}, \"fov\", {fov}, "
                         f"{gd_number(smooth)})")
        else:
            ctx.add_line(body + 1, f"{temp}.fov = {fov}")
    if want_dist:
        # 视距:沿**相机自己的朝向**推远(负 = 拉近)。
        # 用 global_transform 而不是 position,父节点有旋转也照样对。
        distance = ctx.input_number(node, "视距", "0.0")
        ctx.add_line(body, f"if {temp} is Node3D:")
        ctx.add_line(body + 1, f"var _gk_back := "
                               f"({temp} as Node3D).global_transform.basis.z")
        if setter:
            ctx.add_line(body + 1,
                         f"{setter}({temp}, \"global_position\", "
                         f"({temp} as Node3D).global_position + _gk_back * "
                         f"{distance}, {gd_number(smooth)})")
        else:
            ctx.add_line(body + 1, f"({temp} as Node3D).global_position += "
                                   f"_gk_back * {distance}")


@stmt_node("GKNodeCameraPosition")
def _stmt_camera_position(ctx, node, indent):
    """设位置:向量优先,没接就用 X/Y/Z 三个浮点。"""
    camera = _camera_node_expr(ctx, node)
    socket = ctx._socket(node, "位置")
    if socket is not None and getattr(socket, "is_linked", False):
        target = ctx.expr_of(socket)
    else:
        x = ctx.input_number(node, "X", "0.0")
        y = ctx.input_number(node, "Y", "0.0")
        z = ctx.input_number(node, "Z", "0.0")
        target = f"Vector3({x}, {y}, {z})"
    mode = str(getattr(node, "gk_mode", "ADD"))
    smooth = float(getattr(node, "gk_smooth", 0.0) or 0.0)
    temp = ctx.new_temp("cam")
    ctx.add_line(indent, f"var {temp} = {camera}")
    ctx.add_line(indent, f"if {temp} is Node3D:")
    if mode == "SET":
        expr = target
    else:
        expr = f"({temp} as Node3D).global_position + ({target})"
    if smooth > 0.0:
        tween = ctx.new_temp("tween")
        ctx.add_line(indent + 1, f"var {tween} = create_tween()")
        ctx.add_line(indent + 1, f"{tween}.set_trans(Tween.TRANS_SINE)")
        ctx.add_line(indent + 1, f"{tween}.tween_property({temp}, "
                                 f"\"global_position\", {expr}, "
                                 f"{gd_number(smooth)})")
    else:
        ctx.add_line(indent + 1, f"({temp} as Node3D).global_position = {expr}")


@stmt_node("GKNodeCameraRotation")
def _stmt_camera_rotation(ctx, node, indent):
    """设朝向:向量优先(单位度),没接就用偏航/俯仰/翻滚。"""
    camera = _camera_node_expr(ctx, node)
    yaw = ctx.input_number(node, "偏航", "0.0")
    pitch = ctx.input_number(node, "俯仰", "0.0")
    roll = ctx.input_number(node, "翻滚", "0.0")
    # ── 反转开关(用户要求:默认跟随鼠标,但要能自定义方向)──────────
    # 在**生成时**给表达式套个负号:填了常量就直接取负,是表达式就
    # `-(expr)` —— 生成的代码仍然是一句干净的话。
    def flip(expr, invert):
        if not invert or expr in ("0.0", "0"):
            return expr
        if expr.startswith("-"):
            return expr[1:]
        return f"-({expr})"

    yaw = flip(yaw, bool(getattr(node, "gk_invert_x", False)))
    pitch = flip(pitch, bool(getattr(node, "gk_invert_y", False)))
    target = f"Vector3({pitch}, {yaw}, {roll})"
    mode = str(getattr(node, "gk_mode", "ADD"))
    smooth = float(getattr(node, "gk_smooth", 0.0) or 0.0)
    clamp = bool(getattr(node, "gk_clamp_pitch", True))
    temp = ctx.new_temp("cam")
    base = f"({temp} as Node3D).rotation_degrees"
    ctx.add_line(indent, f"var {temp} = {camera}")
    # ── 手动转视角要**打断**正在进行的相机过渡(用户:"视角会卡住")──────
    # 相机切换的过渡是每帧把位姿插值覆盖回去的;用户这时候转视角,
    # 刚转过去下一帧就被覆盖 -> 表现就是"转了没效果 / 视角卡住"。
    # 手动操作 = 打断过渡,并且把"家"更新成用户现在摆的位置。
    left = ctx.declare_var("_gk_cam_blend_left", "float")
    homes = ctx.declare_var("_gk_cam_homes", "Dictionary")
    ctx.add_line(indent, f"if {temp} != null and {left} > 0.0:")
    ctx.add_line(indent + 1, f"{left} = 0.0")
    ctx.add_line(indent + 1, f"if {temp} is Node3D:")
    ctx.add_line(indent + 2, f"{homes}[{temp}] = "
                             f"({temp} as Node3D).global_transform")
    ctx.add_line(indent, f"if {temp} is Node3D:")
    body = indent + 1
    if mode == "SET":
        ctx.add_line(body, f"var _gk_rot: Vector3 = {target}")
    else:
        ctx.add_line(body, f"var _gk_rot: Vector3 = {base} + ({target})")
    if clamp:
        # 俯仰限制在 ±89°:第一人称转视角不加这个会翻过头(画面上下颠倒)
        ctx.add_line(body, "if _gk_rot.x > 89.0:")
        ctx.add_line(body + 1, "_gk_rot.x = 89.0")
        ctx.add_line(body, "elif _gk_rot.x < -89.0:")
        ctx.add_line(body + 1, "_gk_rot.x = -89.0")
    if smooth > 0.0:
        tween = ctx.new_temp("tween")
        ctx.add_line(body, f"var {tween} = create_tween()")
        ctx.add_line(body, f"{tween}.set_trans(Tween.TRANS_SINE)")
        ctx.add_line(body, f"{tween}.tween_property({temp}, "
                           f"\"rotation_degrees\", _gk_rot, "
                           f"{gd_number(smooth)})")
    else:
        ctx.add_line(body, f"({temp} as Node3D).rotation_degrees = _gk_rot")


@multi_output("GKNodeCameraSpringArm")
def _expr_spring_arm_outputs(ctx, node):
    """弹簧臂右侧的三个输出 —— 让别处能读到杆子的状态。

    用户 2026-10-03:"向量右侧输出引脚:便于在运行过程中设置向量"。
    三个都是**成员变量**,由每帧那段逻辑更新 —— 所以在任何地方读都是
    最新值(准星、判断"镜头有没有被墙顶进来"、把相机位置喂给别的节点)。

    注意:这个节点同时是**语句节点**(有执行流)。两边都注册:
    走执行流那支用 STMT_HANDLERS,取数据那支用 MULTI_OUTPUT_FUNCS。
    """
    pivot = ctx.declare_var("_gk_spring_pivot", "Vector3")
    direction = ctx.declare_var("_gk_spring_dir", "Vector3")
    cam = ctx.declare_var("_gk_spring_cam", "Node")
    return {
        "枢轴": pivot,
        "方向": direction,
        "相机位置": (f"(({cam} as Node3D).global_position if {cam} is Node3D "
                     f"else Vector3.ZERO)"),
    }


@stmt_node("GKNodeCameraSpringArm")
def _stmt_camera_spring_arm(ctx, node, indent):
    """**弹簧臂**(虚幻的 SpringArmComponent)。

    ── 每物理帧做五件事 ───────────────────────────────────────────
    1. 算出"枢轴"(挂在跟随对象身上的支点)
    2. 从枢轴沿杆子方向量出"想到的位置"
    3. 打一条射线,撞到东西就把杆子缩短(这就是不穿墙的全部秘密)
    4. 平滑(位置 / 朝向各自可调)
    5. 把相机摆过去,并让它看向枢轴

    ── 为什么挂在 _physics_process(而不是 _process)────────────────
    和角色移动同一个时钟。一个在物理帧跑、一个在渲染帧跑,两个节拍一差
    画面就抖(相机跟随当年就是踩了这个,见 physics_hooks 的说明)。
    """
    follow = ctx.target_expr(node, "gk_target", "跟随")
    camera = _camera_node_expr(ctx, node, "gk_camera")
    # 相机过渡用的变量(过渡期间弹簧臂不抢相机 —— 用户第 25 轮:
    # "按视角切换会闪一下":过渡每帧插值写位姿,弹簧臂也每帧写,
    # 相机就在两个位置之间来回跳。谁在过渡谁说了算。)
    # 必须在函数**最外层**声明:写在某个 if 分支里的话,别处引用会
    # UnboundLocalError(踩过)。
    blend_left = ctx.declare_var("_gk_cam_blend_left", "float")
    # ── 杆子挂在哪:接了引脚就用引脚,否则用『弹簧臂』属性,再否则跟随对象 ──
    # 用户 2026-10-03:"应该还要加设置哪个为弹簧臂引脚" —— 想让杆子挂在
    # 肩膀 / 摄像机架子 / 空节点上时,填这个(或接线)就行。
    arm_socket = ctx._socket(node, "弹簧臂")
    if arm_socket is not None and getattr(arm_socket, "is_linked", False):
        arm_node = ctx.expr_of(arm_socket)
    else:
        raw_arm = str(getattr(node, "gk_arm_node", "") or "").strip()
        arm_node = (ctx.target_expr(node, "gk_arm_node", "弹簧臂")
                    if raw_arm and raw_arm != TARGET_SELF else follow)
    # ── 运行中可改的三个量:引脚 > 属性 ────────────────────────────
    arm = ctx.input_number(node, "臂长", gd_number(
        float(getattr(node, "gk_arm", 4.0) or 0.0)))
    yaw_in = ctx.input_number(node, "偏航", gd_number(
        float(getattr(node, "gk_yaw", 0.0) or 0.0)))
    pitch = ctx.input_number(node, "俯仰", gd_number(
        float(getattr(node, "gk_pitch", -15.0) or 0.0)))
    off_socket = ctx._socket(node, "偏移")
    height = float(getattr(node, "gk_height", 1.5) or 0.0)
    side = float(getattr(node, "gk_side", 0.0) or 0.0)
    if off_socket is not None and getattr(off_socket, "is_linked", False):
        offset_expr = ctx.expr_of(off_socket)
    else:
        offset_expr = f"Vector3(0.0, {gd_number(height)}, {gd_number(side)})"
    yaw_offset = float(getattr(node, "gk_yaw_offset", 0.0) or 0.0)
    collision = bool(getattr(node, "gk_collision", True))
    radius = float(getattr(node, "gk_radius", 0.3) or 0.0)
    lag = float(getattr(node, "gk_lag", 8.0) or 0.0)
    lag_rot = float(getattr(node, "gk_lag_rot", 0.0) or 0.0)
    enabled = bool(getattr(node, "gk_enabled", True))
    if enabled and str(arm) in ("0.0", "0"):
        # 臂长填 0 = 杆子收到底,相机贴在枢轴上 —— 那是"第一人称"的做法,
        # 但多数人填 0 是想"先不生效"。这里给一句提醒,免得画面变成贴脸。
        ctx.warn("『相机_弹簧臂』的臂长是 0 —— 相机会贴在枢轴上(第一人称"
                 "效果)。想暂时关掉这条杆子,请把『启用』的勾去掉")

    cam_var = ctx.declare_var("_gk_spring_cam", "Node")
    # 绑定写在执行点(通常是 _ready);每帧逻辑进 physics_hooks
    ctx.add_line(indent, f"{cam_var} = {camera}")
    ctx.add_line(indent, f"if {cam_var} == null:")
    ctx.add_line(indent + 1, 'push_warning("[GK] 相机_弹簧臂:没找到相机")')
    if not enabled:
        # 关掉 = 连每帧用的成员变量都不声明,更不注册逻辑
        # (不是"注册了再判断"—— 那样白跑一辈子,还占着名字)
        return
    pos_var = ctx.declare_var("_gk_spring_pos", "Vector3")
    ready_var = ctx.declare_var("_gk_spring_ready", "bool", initial="false")
    # 右侧输出的三个量(枢轴 / 方向 / 相机位置)——
    # 存成员变量,所以**别的地方**随时读都是最新值(不是只在执行点算一次)
    pivot_var = ctx.declare_var("_gk_spring_pivot", "Vector3")
    dir_var = ctx.declare_var("_gk_spring_dir", "Vector3")

    follow3d = f"({follow} as Node3D)"
    arm3d = f"({arm_node} as Node3D)"
    lines = [
        f"if {cam_var} != null and {arm_node} is Node3D:",
        # 枢轴 = 杆子挂点的位置 + 偏移向量(偏移可以接线,运行中改)
        f"    var _gk_pivot: Vector3 = {arm3d}.global_position "
        f"+ ({offset_expr})",
        # 偏航 = 挂点自己的朝向 + 节点上的偏移 + **『偏航』引脚**
        # (引脚给"鼠标环绕"用:每帧累加一点,杆子就绕着角色转)
        f"    var _gk_yaw: float = {arm3d}.rotation.y "
        f"+ deg_to_rad({gd_number(yaw_offset)} + ({yaw_in}))",
        # 杆子的方向 = 先按俯仰压下去、再按偏航绕一圈。
        # 从 Basis 取 .z 是因为 Godot 的相机朝 -Z 看 —— 杆子往 +Z 退,
        # 相机正好回头看向枢轴,不用再手动翻 180°。
        "    var _gk_arm_basis := Basis.from_euler(Vector3("
        f"deg_to_rad({pitch}), _gk_yaw, 0.0))",
        "    var _gk_dir: Vector3 = _gk_arm_basis.z",
        f"    var _gk_want: Vector3 = _gk_pivot + _gk_dir * ({arm})",
        # 右侧输出的三个量:每帧更新,别处随时读都是新的
        f"    {pivot_var} = _gk_pivot",
        f"    {dir_var} = _gk_dir",
    ]
    if collision:
        # 射线从枢轴打到"想到的位置";撞到就把相机拉到撞点**前面**一点。
        # 排除跟随对象和相机自己 —— 不排除的话杆子会被"自己"顶住,
        # 相机会一下贴到脸上(实测最容易踩的就是这个)。
        lines += [
            f"    var _gk_space := {follow3d}.get_world_3d().direct_space_state",
            "    var _gk_exclude: Array[RID] = []",
            f"    if {follow} is CollisionObject3D:",
            f"        _gk_exclude.append({follow3d}.get_rid())",
            f"    if {cam_var} is CollisionObject3D:",
            f"        _gk_exclude.append(({cam_var} as CollisionObject3D)"
            ".get_rid())",
            "    var _gk_query := PhysicsRayQueryParameters3D.create("
            "_gk_pivot, _gk_want, 0xFFFFFFFF, _gk_exclude)",
            "    var _gk_hit := _gk_space.intersect_ray(_gk_query)",
            "    if not _gk_hit.is_empty():",
            "        var _gk_dist: float = _gk_pivot.distance_to("
            "_gk_hit[\"position\"])",
            f"        _gk_dist = maxf(_gk_dist - {gd_number(radius)}, 0.2)",
            "        _gk_want = _gk_pivot + _gk_dir * _gk_dist",
        ]
    if lag > 0.0:
        # 指数平滑:系数按 delta 换算,**和帧率无关**。
        # (固定系数的 lerp 在 30 帧和 144 帧下手感完全不一样。)
        lines += [
            f"    if not {ready_var}:",
            f"        {ready_var} = true",
            f"        {pos_var} = _gk_want",
            f"    {pos_var} = {pos_var}.lerp(_gk_want, clampf("
            f"{gd_number(lag)} * get_physics_process_delta_time(), "
            f"0.0, 1.0))",
        ]
        lines.append(f"    if {blend_left} <= 0.0:")
        lines.append(f"        ({cam_var} as Node3D).global_position = {pos_var}")
    else:
        # ── 过渡进行中就**别抢**相机(用户第 25 轮:"切视角会闪一下")────
        # 相机过渡每帧插值写位姿,弹簧臂也每帧写 —— 两边同时写,
        # 相机就在两个位置之间来回跳。谁在过渡谁说了算。
        lines.append(f"    if {blend_left} <= 0.0:")
        lines.append(f"        ({cam_var} as Node3D).global_position = _gk_want")
    # ── 朝向 ────────────────────────────────────────────────────────
    # 默认 `look_at(枢轴)`:比手算欧拉角稳,也没有万向节问题。
    # 有旋转平滑时改成 slerp 一个 Basis(相机朝 -Z 看,所以目标是 -_gk_dir)。
    if lag_rot > 0.0:
        # 朝向平滑才需要这个临时变量(声明了不用会被 Godot 警告)
        basis_var = ctx.declare_var("_gk_spring_basis", "Basis")
        lines += [
            "    var _gk_want_basis := Basis.looking_at(-_gk_dir, Vector3.UP)",
            f"    if not {ready_var}:",
            f"        {ready_var} = true",
            f"        {basis_var} = _gk_want_basis",
            f"    {basis_var} = {basis_var}.slerp(_gk_want_basis, clampf("
            f"{gd_number(lag_rot)} * get_physics_process_delta_time(), "
            f"0.0, 1.0))",
            f"    var _gk_xform := ({cam_var} as Node3D).global_transform",
            f"    _gk_xform.basis = {basis_var}",
            f"    ({cam_var} as Node3D).global_transform = _gk_xform",
        ]
    else:
        lines.append(f"    ({cam_var} as Node3D).look_at(_gk_pivot, Vector3.UP)")
    ctx.physics_hooks.setdefault("camera_spring_arm", []).extend(lines)


@multi_output("GKNodeCameraStatus")
def _expr_camera_status(ctx, node):
    """『相机_取状态』:当前相机是谁、在哪、朝哪、视野多少、开没开。

    ── 为什么走**辅助函数**而不是"就地落局部变量"(踩过两次)────────────
    第一版把表达式内联在每个输出里 -> 「滚轮改视野」那条链生成出一行
    **500 多字符**的怪物(同样的表达式重复 8 遍)。

    第二版改成"求值时先 add_line 落几个局部变量" —— 更糟:表达式是**在
    语句生成途中**求值的,那时候"当前缩进"是上一条语句的缩进,
    于是那几个 var 和包着它的 `if` **缩进对不上**,滚轮那段被嵌进了
    跳跃分支里(生成的代码看着像对,跑起来逻辑全乱)。

    现在每个值一个小函数:输出引脚就是一句 `_gk_cam_fov()` ——
    短、缩进无关、求值几次都不会有副作用。
    """
    cam = ctx.declare_var("_gk_cam_query", "Node")
    ctx.need_camera_query_helper = True
    helpers = [
        ("_gk_cam_name", "String", [
            "    var cam := _gk_current_camera()",
            "    return String(cam.name) if cam != null else \"\"",
        ]),
        ("_gk_cam_fov", "float", [
            "    var cam := _gk_current_camera()",
            "    if cam is Camera3D:",
            "        return (cam as Camera3D).fov",
            "    if cam is Camera2D:",
            "        return (cam as Camera2D).zoom.x",
            "    return 0.0",
        ]),
        ("_gk_cam_open", "bool", [
            "    var cam := _gk_current_camera()",
            "    if cam is Camera3D:",
            "        return (cam as Camera3D).is_current()",
            "    if cam is Camera2D:",
            "        return (cam as Camera2D).is_current()",
            "    return false",
        ]),
    ]
    if ctx.dim == 2:
        helpers.append(("_gk_cam_pos", "Vector2", [
            "    var cam := _gk_current_camera()",
            "    return (cam as Node2D).global_position if cam is Node2D "
            "else Vector2.ZERO",
        ]))
    else:
        helpers.append(("_gk_cam_pos", "Vector3", [
            "    var cam := _gk_current_camera()",
            "    return (cam as Node3D).global_position if cam is Node3D "
            "else Vector3.ZERO",
        ]))
    helpers.append(("_gk_cam_rot", "Vector3", [
        "    var cam := _gk_current_camera()",
        "    return (cam as Node3D).rotation_degrees if cam is Node3D "
        "else Vector3.ZERO",
    ]))
    made = getattr(ctx, "_cam_status_funcs", None)
    if made is None:
        made = set()
        ctx._cam_status_funcs = made
    for base, ret, body in helpers:
        if base in made:
            continue
        made.add(base)
        ctx.extra_sections.append({
            "signature": f"func {base}() -> {ret}:",
            "lines": body,
            "empty_body": "    pass",
            "separator": True,
        })
    return {
        "相机名": "_gk_cam_name()",
        "位置": "_gk_cam_pos()",
        "朝向": "_gk_cam_rot()",
        "视野": "_gk_cam_fov()",
        "开着吗": "_gk_cam_open()",
    }


@multi_output("GKNodeCameraSpringArm")
def _expr_spring_arm_outputs(ctx, node):
    """弹簧臂右侧的三个输出 —— 让别处能读到杆子的状态。

    用户 2026-10-03:"向量右侧输出引脚:便于在运行过程中设置向量"。
    三个都是**成员变量**,由每帧那段逻辑更新 —— 所以在任何地方读都是
    最新值(准星、判断"镜头有没有被墙顶进来"、把相机位置喂给别的节点)。

    注意:这个节点同时是**语句节点**(有执行流)。两边都注册:
    走执行流那支用 STMT_HANDLERS,取数据那支用 MULTI_OUTPUT_FUNCS。
    """
    pivot = ctx.declare_var("_gk_spring_pivot", "Vector3")
    direction = ctx.declare_var("_gk_spring_dir", "Vector3")
    cam = ctx.declare_var("_gk_spring_cam", "Node")
    return {
        "枢轴": pivot,
        "方向": direction,
        "相机位置": (f"(({cam} as Node3D).global_position if {cam} is Node3D "
                     f"else Vector3.ZERO)"),
    }


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


def _path_name(path):
    """从生成的节点引用表达式里取出**节点名**。

    `$"侧视机位"` / `$Camera` -> `侧视机位` / `Camera`;
    不是这种形式的(比如 `self` 或已经算好的表达式)原样返回。
    """
    text = str(path or "").strip()
    if text.startswith("$"):
        text = text[1:].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("\"", "'"):
        text = text[1:-1]
    return text


def _sample_curve_points(curve_name, samples=48):
    """把一条 Blender 曲线采样成世界坐标点列。

    ── 为什么在**导出时**采样(而不是把曲线导进游戏)────────────────
    用户的想法是"在场景里建模一条曲线,相机挂在上面"。但 glTF **不支持
    曲线**:导出时曲线会被转成网格(那就变成一根有厚度的管子,不是轨道了)。
    所以正确做法是:导出时把曲线的形状**采样成点**,写进生成的脚本 ——
    游戏里只需要一串 Vector3,不依赖任何曲线资源。

    曲线物体的 `matrix_world` 也要乘上,否则在场景里挪动曲线时,
    采到的点还停在原地(用户一定会这么干)。
    """
    try:
        import bpy
    except Exception:
        return []
    name = str(curve_name or "").strip()
    if not name:
        return []
    obj = (getattr(bpy.data, "objects", None) or {}).get(name) \
        if hasattr(getattr(bpy.data, "objects", None), "get") else None
    if obj is None or getattr(obj, "type", "") != "CURVE":
        return []
    # ── 必须先刷新一次求值(踩过的坑)──────────────────────────────
    # `obj.matrix_world` 是**求值后**的属性:刚 new 出来、或者用户刚在视口里
    # 拖过曲线,`matrix_world` 还停在旧值(新建时就是单位矩阵)。
    # 实测:在场景里把曲线挪到 (1,2,0),采到的点仍然是 (0,0,…) ——
    # 也就是说"相机沿轨道走"会走一条**看不见的、没挪动过的**轨道。
    # 先 view_layer.update() 一下,矩阵才跟上。
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass
    matrix = obj.matrix_world
    points = []
    data = getattr(obj, "data", None)
    for spline in getattr(data, "splines", ()) or ():
        try:
            count = len(spline.points)
        except Exception:
            count = 0
        if count >= 2:
            # 多段线 / NURBS:points 是四维(带权重)
            for index in range(count):
                point = spline.points[index]
                local = (point.co[0], point.co[1], point.co[2])
                world = matrix @ _vec3(local)
                points.append((world.x, world.y, world.z))
        else:
            bezier = getattr(spline, "bezier_points", None)
            if not bezier:
                continue
            for handle in bezier:
                local = (handle.co[0], handle.co[1], handle.co[2])
                world = matrix @ _vec3(local)
                points.append((world.x, world.y, world.z))
    if len(points) < 2:
        return points
    # ── 重采样成**等距**的 N 个点 ──────────────────────────────────
    # 贝塞尔的控制点疏密不均,直接拿去插值会出现"该慢的地方飞快"。
    # 先按折线累计长度,再按等距取点 —— 这样『位置 = 0.5』真的在中间。
    dense = []
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        dense.append(a)
        for step in range(1, 8):
            t = step / 8.0
            dense.append((a[0] + (b[0] - a[0]) * t,
                          a[1] + (b[1] - a[1]) * t,
                          a[2] + (b[2] - a[2]) * t))
    dense.append(points[-1])
    lengths = [0.0]
    for i in range(1, len(dense)):
        dx = dense[i][0] - dense[i - 1][0]
        dy = dense[i][1] - dense[i - 1][1]
        dz = dense[i][2] - dense[i - 1][2]
        lengths.append(lengths[-1] + (dx * dx + dy * dy + dz * dz) ** 0.5)
    total = lengths[-1]
    if total <= 0.0001:
        return points
    out = []
    for index in range(samples):
        want = total * index / float(samples - 1)
        # 线性找第一个超过它的段(点很少,不值得二分)
        cursor = 0
        while cursor < len(lengths) - 1 and lengths[cursor] < want:
            cursor += 1
        prev = max(0, cursor - 1)
        span = lengths[cursor] - lengths[prev]
        t = 0.0 if span <= 0.0001 else (want - lengths[prev]) / span
        a, b = dense[prev], dense[cursor]
        out.append((a[0] + (b[0] - a[0]) * t,
                    a[1] + (b[1] - a[1]) * t,
                    a[2] + (b[2] - a[2]) * t))
    return out


def _vec3(values):
    """(x, y, z) -> 一个能乘矩阵的对象(离线时手算)。"""
    try:
        from mathutils import Vector
        return Vector(values)
    except Exception:
        return _PlainVec(values)


class _PlainVec:
    """没有 mathutils 时的最小替代(离线测试用)。"""

    def __init__(self, values):
        self.x, self.y, self.z = values


@stmt_node("GKNodeCameraPath")
def _stmt_camera_path(ctx, node, indent):
    """相机沿曲线走(运镜轨道)。

    曲线在**导出时**被采样成点列写进脚本 —— 游戏里只是一串 Vector3,
    不依赖任何曲线资源(glTF 也不支持曲线,见 _sample_curve_points)。
    """
    curve_name = str(getattr(node, "gk_curve", "") or "").strip()
    points = _sample_curve_points(curve_name)
    if not curve_name:
        ctx.warn("『相机_路径』还没选曲线 —— 点节点上的『选曲线』挑一条"
                 "(场景里没有的话:添加 → 曲线 → 路径)")
        return
    if len(points) < 2:
        ctx.warn(f"『相机_路径』的曲线『{curve_name}』采不到点 —— "
                 f"确认它是曲线物体,而且有至少两个点")
        return
    path_var = ctx.declare_var("_gk_cam_path", "PackedVector3Array")
    ctx.var_initials[path_var] = "PackedVector3Array([" + ", ".join(
        f"Vector3({gd_number(x)}, {gd_number(y)}, {gd_number(z)})"
        for x, y, z in points) + "])"
    camera = _camera_node_expr(ctx, node)
    cam_var = ctx.declare_var("_gk_path_cam", "Node")
    pos_var = ctx.declare_var("_gk_path_pos", "Vector3")
    t_var = ctx.declare_var("_gk_path_t", "float")
    ctx.add_line(indent, f"{cam_var} = {camera}")
    position = ctx.input_number(node, "位置", "0.0")
    speed = float(getattr(node, "gk_speed", 0.0) or 0.0)
    smooth = float(getattr(node, "gk_smooth", 8.0) or 0.0)
    loop = bool(getattr(node, "gk_loop", False))
    mode = str(getattr(node, "gk_look_mode", "FORWARD"))
    target = ""
    if mode == "TARGET":
        raw = str(getattr(node, "gk_look_target", "") or "").strip()
        target = (ctx.target_expr(node, "gk_look_target", "看向") if raw
                  else "null")
    frame = [
        f"if {cam_var} != null and {cam_var} is Node3D:",
        f"    var _gk_count: int = {path_var}.size()",
        f"    if _gk_count >= 2:",
        f"        {t_var} = clampf({t_var}, 0.0, 1.0)",
        f"        var _gk_f: float = {t_var} * float(_gk_count - 1)",
        "        var _gk_i: int = int(floor(_gk_f))",
        "        var _gk_frac: float = _gk_f - float(_gk_i)",
        f"        var _gk_want: Vector3 = {path_var}[_gk_i].lerp("
        f"{path_var}[mini(_gk_i + 1, _gk_count - 1)], _gk_frac)",
    ]
    if smooth > 0.0:
        t = (f"clampf({gd_number(smooth)} * "
             f"get_physics_process_delta_time(), 0.0, 1.0)")
        frame += [
            f"        {pos_var} = {pos_var}.lerp(_gk_want, {t})",
        ]
    else:
        frame += [f"        {pos_var} = _gk_want"]
    frame += [
        f"        ({cam_var} as Node3D).global_position = {pos_var}",
    ]
    if mode == "FORWARD":
        # 朝前 = 用"下一个点 - 当前位置"当方向(算出来才准,
        # 拿曲线切线的话还得再采样一次)
        frame += [
            f"        var _gk_ahead: Vector3 = {path_var}[mini(_gk_i + 1, "
            f"_gk_count - 1)]",
            f"        if ({cam_var} as Node3D).global_position.distance_to("
            f"_gk_ahead) > 0.01:",
            f"            ({cam_var} as Node3D).look_at(_gk_ahead, Vector3.UP)",
        ]
    elif mode == "TARGET" and target:
        frame += [
            f"        if {target} is Node3D:",
            f"            ({cam_var} as Node3D).look_at("
            f"({target} as Node3D).global_position, Vector3.UP)",
        ]
    if speed > 0.0:
        frame += [
            f"        {t_var} += {gd_number(speed / 100.0)} * "
            f"get_physics_process_delta_time()",
        ]
        if loop:
            frame += [
                f"        if {t_var} > 1.0:",
                f"            {t_var} = 0.0",
            ]
        else:
            frame += [f"        {t_var} = minf({t_var}, 1.0)"]
    # 起始位置由执行点的『位置』引脚给(通常是 0)
    ctx.add_line(indent, f"{t_var} = {position}")
    ctx.physics_hooks.setdefault("camera_path", []).extend(frame)


@stmt_node("GKNodeCameraSetCurrent")
def _stmt_camera_set_current(ctx, node, indent):
    """切相机;填了『过渡』就是丝滑推镜。

    ── 丝滑推镜怎么做的(不用 await,也不用挪走旧相机)────────────
    1. 先记住**新相机自己**的位姿(end)
    2. 把新相机**瞬移**到旧相机的位姿(start),然后 make_current()
    3. 之后每帧把它从 start 插值回 end(登记进 physics_hooks)
    4. 到位后精确写回 end

    关键在"动的是**新**相机":它最后停在的正是自己本来的位姿,所以
    过渡结束不需要任何还原;旧相机从头到尾没被碰过,来回切也不会跑位。
    (先动旧相机再切的做法会把它永久挪到新位置 —— 切回去就发现镜头歪了。)

    每帧逻辑挂在 `_physics_process`(和角色移动同一个时钟),避免相机
    在渲染帧里追、角色在物理帧里跑,两个节拍一差拍画面就抖。
    """
    camera, _auto = _camera_expr(ctx, node)
    blend = float(getattr(node, "gk_blend", 0.0) or 0.0)
    # `to_var` 必须是**成员变量**,而且这里**不能写 `var`**:
    # 过渡逻辑跑在 _physics_process 里(另一个函数),用局部变量会
    #   1) `Identifier "_cam_to2" not declared in the current scope`(实测)
    #   2) 就算声明成成员变量,再写一次 `var` 会**遮蔽**它 ——
    #      局部那个出了 if 块就没了,钩子里读到的永远是 null,
    #      相机过渡**静默失效**(测试里就是这么发现的第一版)
    to_var = ctx.declare_var("_gk_cam_to", "Node")
    ctx.add_line(indent, f"{to_var} = {camera}")
    ctx.add_line(indent, f"if {to_var} == null:")
    ctx.add_line(indent + 1, f"push_warning(\"[GK] 相机_切换:没找到相机\")")
    if blend <= 0.0:
        ctx.add_line(indent, f"else:")
        ctx.add_line(indent + 1, f"{to_var}.make_current()")
        return

    # ---- 带过渡 ----
    from_var = ctx.declare_var("_gk_cam_from", "Node")
    left_var = ctx.declare_var("_gk_cam_blend_left", "float")
    total_var = ctx.declare_var("_gk_cam_blend_total", "float")
    start3 = ctx.declare_var("_gk_cam_start3", "Transform3D")
    # ── 目标相机"本来的位姿"要**缓存**,不能每次现读(用户 2026-10-03)──
    # 连续点 C/V 时相机一路往后飘,原因就在这:
    #   第一次切换:end = 相机本来的位姿(对的) -> 过渡到 end
    #   过渡还没走完又按一次:此时相机正停在**半路**,再读一次"它本来的
    #   位姿"读到的就是那个半路位置 -> end 被改成了半路位置 ->
    #   下一次再往后退一点…… 按几次退几次。
    # 现在把每台相机的"家"记在字典里,第一次见到它时存下来,以后一直用它。
    homes = ctx.declare_var("_gk_cam_homes", "Dictionary")
    end3 = ctx.declare_var("_gk_cam_end3", "Transform3D")
    start2 = ctx.declare_var("_gk_cam_start2", "Vector2")
    end2 = ctx.declare_var("_gk_cam_end2", "Vector2")
    zoom2 = ctx.declare_var("_gk_cam_endzoom", "Vector2")
    ctx.add_line(indent, f"else:")
    ctx.add_line(indent + 1, f"{from_var} = get_viewport().get_camera_3d()")
    ctx.add_line(indent + 1, f"if {from_var} == null:")
    ctx.add_line(indent + 2, f"{from_var} = get_viewport().get_camera_2d()")
    ctx.add_line(indent + 1, f"if {to_var} is Camera3D and {from_var} is Camera3D:")
    # 第一次见到这台相机 -> 把它的"家"记下来;以后每次都回到那个家
    ctx.add_line(indent + 2, f"if not {homes}.has({to_var}):")
    ctx.add_line(indent + 3, f"{homes}[{to_var}] = "
                             f"({to_var} as Camera3D).global_transform")
    ctx.add_line(indent + 2, f"{end3} = {homes}[{to_var}]")
    ctx.add_line(indent + 2, f"{start3} = ({from_var} as Camera3D).global_transform")
    ctx.add_line(indent + 2, f"({to_var} as Camera3D).global_transform = {start3}")
    ctx.add_line(indent + 2, f"({to_var} as Camera3D).make_current()")
    ctx.add_line(indent + 2, f"{total_var} = maxf({gd_number(blend)}, 0.0001)")
    ctx.add_line(indent + 2, f"{left_var} = {total_var}")
    ctx.add_line(indent + 1, f"elif {to_var} is Camera2D and {from_var} is Camera2D:")
    ctx.add_line(indent + 2, f"if not {homes}.has({to_var}):")
    ctx.add_line(indent + 3, f"{homes}[{to_var}] = ["
                             f"({to_var} as Camera2D).global_position, "
                             f"({to_var} as Camera2D).zoom]")
    ctx.add_line(indent + 2, f"{end2} = ({homes}[{to_var}] as Array)[0]")
    ctx.add_line(indent + 2, f"{zoom2} = ({homes}[{to_var}] as Array)[1]")
    ctx.add_line(indent + 2, f"{start2} = ({from_var} as Camera2D).global_position")
    ctx.add_line(indent + 2, f"({to_var} as Camera2D).global_position = {start2}")
    ctx.add_line(indent + 2, f"({to_var} as Camera2D).zoom = "
                             f"({from_var} as Camera2D).zoom")
    ctx.add_line(indent + 2, f"({to_var} as Camera2D).make_current()")
    ctx.add_line(indent + 2, f"{total_var} = maxf({gd_number(blend)}, 0.0001)")
    ctx.add_line(indent + 2, f"{left_var} = {total_var}")
    ctx.add_line(indent + 1, f"else:")
    ctx.add_line(indent + 2, f"{to_var}.make_current()")

    # ---- 每帧推进(和相机震动同一个机制)----
    hook_key = "camera_blend"
    if hook_key not in ctx.physics_hooks:
        ctx.physics_hooks[hook_key] = [
            f"if {left_var} > 0.0:",
            f"    {left_var} -= delta",
            f"    var _gk_ct: float = clampf(1.0 - {left_var} / {total_var}, "
            f"0.0, 1.0)",
            f"    if ({to_var} is Camera3D) and ({from_var} is Camera3D):",
            f"        ({to_var} as Camera3D).global_transform = "
            f"{start3}.interpolate_with({end3}, _gk_ct)",
            f"    elif ({to_var} is Camera2D) and ({from_var} is Camera2D):",
            f"        ({to_var} as Camera2D).global_position = "
            f"{start2}.lerp({end2}, _gk_ct)",
            f"        ({to_var} as Camera2D).zoom = "
            f"({from_var} as Camera2D).zoom.lerp({zoom2}, _gk_ct)",
            f"    if {left_var} <= 0.0:",
            # 到位:精确写回,免得浮点插值差一点点(镜头会"差一丝")
            f"        if {to_var} is Camera3D:",
            f"            ({to_var} as Camera3D).global_transform = {end3}",
            f"        elif {to_var} is Camera2D:",
            f"            ({to_var} as Camera2D).global_position = {end2}",
            f"            ({to_var} as Camera2D).zoom = {zoom2}",
        ]


@stmt_node("GKNodeCameraZoom")
def _stmt_camera_zoom(ctx, node, indent):
    """缩放 / 视野;填了『时长』就用 Tween 平滑过去。"""
    camera, _auto = _camera_expr(ctx, node)
    value = _tunable_number(ctx, node, "数值", "1.0", "camera_zoom")
    duration = float(getattr(node, "gk_time", 0.0) or 0.0)
    # ── 上下限(用户 2026-10-03:"可以无限拉长,应该添加自定义范围")──────
    # 0 = 不限制。填了就在**生成时**夹一次:
    #   zoom 当固定值 -> 直接 clamp 那个常量,生成的代码还是干净的
    #   zoom 是表达式(滚轮链)-> 用 clampf(...) 包一层
    low = float(getattr(node, "gk_min_value", 0.0) or 0.0)
    high = float(getattr(node, "gk_max_value", 0.0) or 0.0)
    if low != 0.0 or high != 0.0:
        lo = gd_number(low) if low != 0.0 else "-INF"
        hi = gd_number(high) if high != 0.0 else "INF"
        value = f"clampf({value}, {lo}, {hi})"
    temp = ctx.new_temp("cam")
    # 2D 的 zoom 是"越大越近"的倍数,3D 的 fov 是垂直视场角(度)。
    # 两者语义不同，所以按相机真实类型分派，而不是按项目维度猜。
    ctx.add_line(indent, f"var {temp} = {camera}")
    if duration > 0.0:
        tween_var = ctx.new_temp("tween")
        ctx.add_line(indent, f"if {temp} != null:")
        ctx.add_line(indent + 1, f"var {tween_var} = create_tween()")
        ctx.add_line(indent + 1, f"{tween_var}.set_trans(Tween.TRANS_SINE)")
        ctx.add_line(indent + 1, f"{tween_var}.set_ease(Tween.EASE_IN_OUT)")
        ctx.add_line(indent + 1, f"if {temp} is Camera2D:")
        ctx.add_line(indent + 2, f"{tween_var}.tween_property({temp}, "
                                 f"\"zoom\", Vector2({value}, {value}), "
                                 f"{gd_number(duration)})")
        ctx.add_line(indent + 1, f"elif {temp} is Camera3D:")
        ctx.add_line(indent + 2, f"{tween_var}.tween_property({temp}, "
                                 f"\"fov\", {value}, {gd_number(duration)})")
        return
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
    widget = ctx.declare_var(f"_gk_ui_{node.name}", "Label", key=node_lib.node_key(node))
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
    widget = ctx.declare_var(f"_gk_ui_{node.name}", "ProgressBar", key=node_lib.node_key(node))
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
    widget = ctx.declare_var(f"_gk_ui_{node.name}", "Button", key=node_lib.node_key(node))
    # 回调函数名同样要防同名:两个按钮可以都叫『开始按钮』，
    # 若共用 gk_on_button 就会有两个同名函数，Godot 直接报重复定义。
    handler = ctx.declare_func_name(f"gk_on_{snake_case(node.name, 'button')}", key=node_lib.node_key(node))
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
                                 key=node_lib.node_key(node))
    point_var = ctx.declare_var(f"gk_ray_point_{snake_case(node.name, 'hit')}", "Variant",
                                key=node_lib.node_key(node))
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
                                key=node_lib.node_key(node))
    handler = ctx.declare_func_name(f"gk_on_{snake_case(node.name, 'every')}", key=node_lib.node_key(node))
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
                              key=node_lib.node_key(node))
    prev_var = ctx.declare_var(f"gk_joy_prev_{snake_case(node.name, 'btn')}", "bool",
                               key=node_lib.node_key(node))
    result_var = ctx.declare_var(f"gk_joy_down_{snake_case(node.name, 'btn')}", "bool",
                                 key=node_lib.node_key(node))
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
                                    key=node_lib.node_key(node))
    # 函数体**跟着登记一起交**(不再是发射时写死的那几行)——
    # 一个 `_input` 表项一份体,见 register_input_handler 的说明
    body = [
        "    if event is InputEventScreenDrag:",
        "        _gk_touch_drag += (event as InputEventScreenDrag).relative",
    ]
    if reset:
        body += [
            "    elif event is InputEventScreenTouch:",
            "        if not (event as InputEventScreenTouch).pressed:",
            "            _gk_touch_drag = Vector2.ZERO",
        ]
    ctx.register_input_handler(handler, reset=reset, body=body)

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




_PORT_GD_TYPES = {"bool": "bool", "int": "int", "float": "float",
                  "string": "String", "vector": "Vector3"}


def _emit_logic_ports(ctx):
    """把侧栏声明的端口变成脚本变量(带类型和初值)。"""
    tree = getattr(ctx, "tree", None)
    ports = getattr(tree, "gk_ports", None) if tree is not None else None
    if not ports:
        return
    try:
        for port in ports:
            raw = str(port.port_name).strip()
            if not raw:
                continue
            gd_type = _PORT_GD_TYPES.get(str(port.port_type), "Variant")
            initial = str(port.default_value or "").strip()
            if not initial:
                initial = {"bool": "false", "int": "0", "float": "0.0",
                           "string": '\"\"', "vector": "Vector3.ZERO"}.get(
                               str(port.port_type), "null")
            name = ctx.declare_var(f"输出_{raw}", gd_type, initial=initial)
            # 让『变量_设置/『变量_读取』用同名时命中**这一个**变量 ——
            # 否则端口那份永远是初值(它俩会各写一份,实测踩到)
            try:
                ctx._var_aliases[raw] = name
            except Exception:
                pass
            ctx.note(f"对外端口『{raw}』({gd_type})-> {name}")
    except Exception:
        pass


def _declared_ports(ctx, direction):
    """当前图在侧栏声明过的端口名集合;读不到就返回 None(离线/老文件)。

    返回 None 表示"没法校验",调用方**不要**因此报警 —— 校验是加分项,
    不能因为拿不到 UI 数据就让导出失败。
    """
    tree = getattr(ctx, "tree", None)
    ports = getattr(tree, "gk_ports", None) if tree is not None else None
    if ports is None:
        return None
    try:
        return {str(p.port_name).strip() for p in ports
                if str(getattr(p, "direction", "out")) == direction
                and str(p.port_name).strip()}
    except Exception:
        return None

@stmt_node("GKNodeQuitGame")
def _stmt_quit_game(ctx, node, indent):
    """退出游戏(桌面版)。

    延迟 > 0 时用 Timer 等一会儿再退 —— 让"退出动画/音效"播完。
    没有延迟就直接 `get_tree().quit()`。
    """
    delay = float(getattr(node, "gk_delay", 0.0) or 0.0)
    if delay > 0.0:
        ctx.add_line(indent, f"await get_tree().create_timer("
                             f"{gd_number(delay)}).timeout")
        ctx.note("『流程_退出游戏』用了延迟 —— 生成的是 `await`,"
                 "别把它接在 _physics_process 的中间(会挂起后面的逻辑)")
    ctx.add_line(indent, "get_tree().quit()")


@stmt_node("GKNodeLogicOutput")
def _stmt_logic_output(ctx, node, indent):
    """『逻辑_输出结果』:把一个布尔发布成脚本变量 `输出_<名字>`。

    用户第七轮的计划:"一个游戏逻辑最后应该输出布尔变量,以便与其他的
    游戏逻辑联动"。所有节点图编译成**同一个脚本**,所以"输出"就是
    "约定一个变量名":这里写,别处用『变量_读取』读。

    导出报告里会列出来(见下面的 note),方便对照谁输出了什么。
    """
    raw = str(getattr(node, "gk_output_name", "") or "").strip()
    if not raw:
        ctx.warn("『逻辑_输出结果』没填输出名 —— 它不会生成任何东西")
        return
    name = ctx.declare_var(f"输出_{raw}", "bool", initial="false")
    socket = ctx._socket(node, "值")
    if socket is not None and getattr(socket, "is_linked", False):
        value_expr = ctx.expr_of(socket)
    else:
        value_expr = "true" if getattr(node, "gk_value", True) else "false"
    # 值可能是个任意表达式(比如『逻辑_比较』的结果)—— bool 变量收别的类型
    # 会报类型不符,这里统一裹一层 bool()。字面量 true/false 就不用裹了。
    if value_expr not in ("true", "false"):
        value_expr = f"bool({value_expr})"
    ctx.add_line(indent, f"{name} = {value_expr}")
    outputs = getattr(ctx, "_logic_outputs", None)
    if outputs is None:
        outputs = []
        ctx._logic_outputs = outputs
    outputs.append((raw, name))
    # ── 端口校验(用户第 21 轮:"真接口")────────────────────────────
    # 图在侧栏声明过端口的话,这里比对一下:节点上填的名字**必须**在
    # 声明表里。以前是随便填 —— 打错一个字母不会报错,别的图读到空值,
    # 你只会以为是逻辑没跑到(这类"静默失败"最费时间)。
    declared = _declared_ports(ctx, "out")
    if declared is not None and raw not in declared:
        ctx.warn(f"『逻辑_输出结果』写的端口『{raw}』没有在侧栏"
                 f"『对外端口』里声明过 —— 别的图读它只会读到空值。"
                 f"要么去声明一个,要么改成已声明的:"
                 f"{'、'.join(sorted(declared)) or '(还没有)'}")


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


def _link_type(socket):
    """从上游输出引脚的**种类**推断 GDScript 类型(推不出来就 None)。

    ── 为什么要这个(用户 2026-10-03)────────────────────────────
    `变量_设置` 的『值』接了线时,原来一律当成 Variant ——
    于是「变量_设置(is_hurt) <- 常量_布尔」生成出
    `var is_hurt: Variant`,再和其它地方按 bool 用它的代码撞上就报
    "同时被当成 bool 和 Variant 使用"。

    线那头是什么引脚其实**是知道的**(常量_布尔 的输出就是 NodeSocketBool),
    照着它声明类型即可,不用猜。
    """
    links = getattr(socket, "links", ()) or ()
    for link in links:
        from_socket = getattr(link, "from_socket", None)
        kind = (getattr(from_socket, "bl_idname", "")
                or getattr(from_socket, "type", ""))
        mapping = {
            "NodeSocketBool": "bool",
            "NodeSocketFloat": "float",
            "NodeSocketInt": "int",
            "NodeSocketString": "String",
        }
        if kind in mapping:
            return mapping[kind]
    return None


@stmt_node("GKNodeSetVar")
def _stmt_set_var(ctx, node, indent):
    """『变量_设置』:一张卡片可以设好几个变量(仿蓝图 Set 节点)。

    ── 为什么第 1 个用老属性名(踩过的坑)────────────────────────────
    第 1 个变量仍然读 `gk_var_name` / `gk_value`、引脚仍然叫『值』——
    老文件里接好的线、填好的名字一个字都不用改。第 2 个开始才是
    `gk_var_name_2` / 『值 2』。
    """
    pairs = []
    slot_getter = getattr(node, "slots", None)
    if callable(slot_getter):
        pairs = list(slot_getter())
    if not pairs:
        # 桩环境/老节点:退回"只有第一个"的老路径
        raw_name = getattr(node, "gk_var_name", "value")
        pairs = [(1, raw_name, getattr(node, "gk_value", 0.0))]

    for index, raw_name, fallback in pairs:
        # 引脚名:第 1 个叫『值』(老图接的就是它),第 2 个起叫『值 2』『值 3』。
        # 这里 index 是**槽位号**(1 起),别再 +1 —— 写成 "值 {index+1}"
        # 会去找一个不存在的『值 3』,于是第 2 个槽永远读不到连线,
        # 悄悄退回数字框里的值(示例里就是这么被抓到的)。
        pin_name = "值" if index == 1 else f"值 {index}"
        raw_value = None
        socket = ctx._socket(node, pin_name)
        if socket is not None and getattr(socket, "is_linked", False):
            value_expr = ctx.expr_of(socket)
            # ── 值接了线就**不要**替它声明类型(踩过的坑)────────────────
            # 这里原来写的是 `gd_type = "Variant"` —— 于是
            # 「变量_设置(is_hurt) <- 常量_布尔」这种最常见的写法会报
            #     变量『is_hurt』同时被当成 bool 和 Variant 使用,请改用不同的变量名
            # 用户会去改一个**根本没问题**的变量名,而真正的类型(布尔)
            # 本来是清楚的:线的那头就是常量_布尔。
            # 传 None = "我没意见",由别处(动画树的变量面板/取变量节点)决定类型。
            # 能从上游引脚的种类看出来就用它 —— 见 _link_type
            gd_type = _link_type(socket)
        else:
            raw_value = node.input_value(ctx, pin_name, fallback)
            # ── 空值要退回属性里的值(踩过的坑)──────────────────────
            # 多槽卡片的第 2、3… 个引脚是 `build_sockets` 现造的,
            # 没设过 default_value -> `input_value` 返回 **None** ->
            # 生成出 `score = null`,而 `var score: float` 收不了 null,
            # Godot **解析期**就报
            #     Parse Error: Cannot assign a value of type "null" as "float"
            # 整个脚本加载失败。这是 2D 示例探针抓出来的。
            if raw_value is None:
                raw_value = fallback
            value_expr = gd_literal(raw_value)
            gd_type = _gd_type_of(raw_value)
        var_name = ctx.declare_var(raw_name, gd_type)
        # ── 布尔变量被喂了数字:转成真假再赋值(不转的话 Godot 报类型不符)──
        # 触发方式很常见:变量是 bool,用户在节点上那个**数字框**里填 0/1
        # (那个框只能填数字)。declare_var 已经给了一条"该接『常量_布尔』"
        # 的告警,这里负责把生成结果弄成能跑的 `= false/true`。
        if gd_type in ("float", "int") and ctx.vars.get(var_name) == "bool":
            try:
                value_expr = "true" if float(raw_value or 0) != 0 else "false"
            except (TypeError, ValueError):
                value_expr = "false"
        # ── 往整数变量里写浮点表达式要**显式转换**(第 22 轮)──────────
        # 『数学_运算』出来的都是 float,而端口/变量声明的可能是 int:
        # 直接写会得到 `var x: int` + `x = (x + 1.0)` —— Godot 报
        # "Cannot assign a value of type float as int",整个脚本加载失败。
        # 蓝图里这种转换是隐式的,这里补上,不用用户自己套『转换_类型』。
        if ctx.vars.get(var_name) == "int" and value_expr is not None:
            text = str(value_expr).strip()
            looks_int = (text.lstrip("-").isdigit()
                         or text.startswith(("int(", "true", "false")))
            if not looks_int:
                value_expr = f"int({text})"
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


@stmt_node("GKNodeSwitch")
def _stmt_switch(ctx, node, indent):
    """多路分支:按一个整数走不同出口。

    生成的 GDScript 用 `match`:

        var _sw1 := 2
        match _sw1:
            0:
                <出口 0 的链>
            1:
                <出口 1 的链>
            _:
                <默认的链>

    ── 两个容易出错的地方 ──────────────────────────────────────────
    1. `match` 的每个分支体**不能为空** —— 没连线时要补 `pass`,
       否则 GDScript 直接语法错误。
    2. 出口名就是匹配的整数值,最后一条固定叫『默认』(生成成 `_`)。
       所以这里必须把"默认"和数字分开处理,不能一视同仁地 int()。
    """
    value = ctx.input_number(node, "值", "0")
    temp = ctx.new_temp("sw")
    ctx.add_line(indent, f"var {temp} := int({value})")
    ctx.add_line(indent, f"match {temp}:")

    # 出口顺序就是 build_sockets 建的顺序(0..n-1,然后"默认")
    default_socket = None
    branches = []
    for socket in node.outputs:
        if socket.name == "默认":
            default_socket = socket
        else:
            branches.append(socket)

    for socket in branches:
        try:
            case_value = int(socket.name)
        except (TypeError, ValueError):
            continue
        ctx.add_line(indent + 1, f"{case_value}:")
        before = len(ctx.sink)
        _chain(ctx, node, indent + 2, socket.name)
        if len(ctx.sink) == before:
            ctx.add_line(indent + 2, "pass")

    if default_socket is not None:
        ctx.add_line(indent + 1, "_:")
        before = len(ctx.sink)
        _chain(ctx, node, indent + 2, "默认")
        if len(ctx.sink) == before:
            ctx.add_line(indent + 2, "pass")
    elif not branches:
        # 一个出口都没有(理论上不会,防御一下)
        ctx.add_line(indent + 1, "_:")
        ctx.add_line(indent + 2, "pass")


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
    """等待 N 秒(await),然后继续往下走。

    ── 这里**不能**自己再 `_chain` 一次(踩过的坑)──────────────────
    `emit_statement()` 在调用完处理器之后**已经**会沿『完成』往下走了。
    处理器里再 `_chain(ctx, node, indent)` 就会把下游整段代码生成**两遍**:

        等待_秒 -> 变量_设置(is_hurt = false)
        生成结果:
            await get_tree().create_timer(0.6).timeout
            is_hurt = false
            is_hurt = false      <- 多出来的一份

    接在等待后面的要是『音效_播放』『实例_生成』这种有副作用的节点,
    就是**真的执行两次**(放两个音效、生成两个实例),不只是难看。
    `_active` 那个断环保护挡不住它 —— 第二次进来时第一次已经出栈了。

    也不需要担心"等待之后没往下走":走不走由 `emit_statement` 统一负责。
    """
    seconds = ctx.input_number(node, "秒", "1.0")
    use_process = bool(getattr(node, "gk_use_process", False))
    if use_process:
        ctx.add_line(indent, f"await get_tree().create_timer({seconds}, true, false, true).timeout")
    else:
        ctx.add_line(indent, f"await get_tree().create_timer({seconds}).timeout")


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
        # ── 先声明对外端口(第 22 轮)──────────────────────────────────
        # 必须在遍历节点**之前**:端口要抢占变量名。反过来的话,
        # 『变量_设置(金币数)』会先声明一个 float 的 `金币数`,
        # 端口再来只能复用它 —— `输出_` 前缀和声明类型(int)全丢,
        # 别的图按文档读 `输出_金币数` 会读到一个不存在的名字。
        _emit_logic_ports(self.ctx)
        # 运行日志用的计时器变量:同样要**提前声明**(遍历节点之后再声明,
        # 变量清单已经写进 out 了 -> Godot 报 not declared。端口那次踩过)
        if bool(self.options.get("debug_runtime", False)):
            self.ctx.declare_var("_gk_debug_timer", "float")
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

            # ── 嵌套实例守卫 ────────────────────────────────────────────
            # 世界场景里那个包着角色的节点挂着本脚本,而那个节点的子节点
            # **实例化的又是角色场景**(角色场景根节点也挂着同一个脚本)。
            # 于是同一个 `_ready()` 会执行两遍 —— 用户实测:
            # 控制台把 "你好,世界" 打印了两次;更糟的是移动逻辑也会跑两遍,
            # 角色以双倍速度移动。
            #
            # 两份都不能删:
            #   * 删 World 那份 -> Godot 实例化场景时**不继承**根节点的
            #     `script` 属性(实测 script=<null>),逻辑整个不跑
            #   * 删角色场景那份 -> 单独打开角色场景就不能跑了
            #
            # 所以改成**运行时守卫**:如果父节点挂着同一个脚本,
            # 说明"我"只是被实例化出来的那一份,外层已经在处理了 —— 直接跳过。
            #
            # 用 `get_script()` 比对而不是 `has_method("_ready")` ——
            # 后者任何脚本都有,区分不出是不是同一个。
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
            target_section = self._section_for(sections, "_process")
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
            target_section = self._section_for(sections, "_physics_process")
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
            for func_name, reset in [(item[0], item[1])
                                     for item in self.ctx._input_handlers]:
                target_section["lines"].append(f"    {func_name}(event)")

        # ---- 相机解析(类型统一,不用嵌套三元)----
        if getattr(self.ctx, "need_resolve_camera", False):
            self._append_block(sections, "_gk_resolve_camera",
                               "func _gk_resolve_camera(host: Node) -> Node:",
                               [
                                   "    # ① 视口里正在用的那台",
                                   "    var cam: Node = host.get_viewport().get_camera_3d()",
                                   "    if cam != null:",
                                   "        return cam",
                                   "    cam = host.get_viewport().get_camera_2d()",
                                   "    if cam != null:",
                                   "        return cam",
                                   "    # ② 打了 gk_camera 分组的",
                                   "    var grouped := host.get_tree().get_first_node_in_group(\"gk_camera\")",
                                   "    if grouped != null:",
                                   "        return grouped",
                                   "    # ③ 场景里第一台相机(顺手把它设成当前 —— 不然画面全黑)",
                                   "    for found in host.get_tree().root.find_children(\"*\", \"Camera3D\", true, false):",
                                   "        (found as Camera3D).make_current()",
                                   "        return found",
                                   "    for found in host.get_tree().root.find_children(\"*\", \"Camera2D\", true, false):",
                                   "        (found as Camera2D).make_current()",
                                   "        return found",
                                   "    return null",
                               ])

        # ---- 节点引用的小工具(带兜底查找)----
        # 只要脚本里出现过 @onready 引用就要生成它。见上面 out 里那段说明:
        # 脚本挂在碰撞代理上时,相对路径找不到兄弟节点。
        if self.ctx.onready:
            sections.append({
                "signature": ("func _gk_ref(host: Node, wanted: String) "
                              "-> Node:"),
                "lines": [
                    "    if wanted.is_empty():",
                    "        return null",
                    "    var direct := host.get_node_or_null(wanted)",
                    "    if direct != null:",
                    "        return direct",
                    "    if host.get_tree() == null:",
                    "        return null",
                    "    # 先找同名子节点(限定在 host 子树里),再全树找 ——",
                    "    # 导出后脚本常在『碰撞代理』物体上,而用户摆的相机/角色",
                    "    # 是它的兄弟节点,相对路径找不到",
                    "    for found in host.find_children(wanted, \"\", true, "
                    "false):",
                    "        return found",
                    "    for found in host.get_tree().root.find_children("
                    "wanted, \"\", true, false):",
                    "        return found",
                    '    push_warning("[GK] 没找到节点:%s —— 检查名字拼写,'
                    '或者它是不是没被导出" % wanted)',
                    "    return null",
                ],
                "empty_body": "    return null",
                "separator": True,
            })

        # ---- 骨骼:找 Skeleton3D 的小工具 ----
        # glTF 导入的模型骨架常常在**好几层子节点**下面,所以递归找。
        # 找到后缓存在成员变量里 —— 每帧找一遍没必要(骨骼节点不会中途换)。
        if getattr(self.ctx, "need_skeleton_helper", False):
            cache = self.ctx.declare_var("_gk_skeleton_cache", "Skeleton3D")
            sections.append({
                "signature": "func _gk_find_skeleton(host: Node) -> Skeleton3D:",
                "lines": [
                    f"    if {cache} != null and is_instance_valid({cache}):",
                    f"        return {cache}",
                    "    if host == null:",
                    "        return null",
                    "    if host is Skeleton3D:",
                    f"        {cache} = host",
                    f"        return {cache}",
                    "    for child in host.find_children(\"*\", \"Skeleton3D\", "
                    "true, false):",
                    f"        {cache} = child",
                    f"        return {cache}",
                    "    if host.get_tree() != null:",
                    "        for child in host.get_tree().root.find_children("
                    "\"*\", \"Skeleton3D\", true, false):",
                    f"            {cache} = child",
                    f"            return {cache}",
                    "    return null",
                ],
                "empty_body": "    return null",
                "separator": True,
            })

        # ---- 骨骼:读一根骨头的姿势(局部 + 世界) ----
        # 返回字典而不是四个函数:『骨骼_取数据』一次要四个值,
        # 一次算完最省(和『动画判断_移动状态』的多输出一个思路)。
        if getattr(self.ctx, "need_bone_reader", False):
            sections.append({
                "signature": ("func _gk_read_bone(skel: Skeleton3D, "
                              "bone_name: String) -> Dictionary:"),
                "lines": [
                    "    if skel == null:",
                    "        return {}",
                    "    var index: int = skel.find_bone(bone_name)",
                    "    if index < 0:",
                    "        return {}",
                    "    var rot := skel.get_bone_pose_rotation(index)",
                    "    var result := {",
                    "        \"pos\": skel.get_bone_pose_position(index),",
                    "        \"rot\": rot.get_euler() * 180.0 / PI,",
                    "        \"scale\": skel.get_bone_pose_scale(index),",
                    "        \"world\": skel.get_bone_global_pose(index).origin,",
                    "    }",
                    "    return result",
                ],
                "empty_body": "    return {}",
                "separator": True,
            })

        # ---- 『相机_取状态』用的小助手 ----
        # 当前相机是 3D 还是 2D 要先问一遍 viewport;写成函数免得每个
        # 取值点都抄一遍三元表达式(那种代码没法读,也没法改)。
        if getattr(self.ctx, "need_camera_query_helper", False):
            ctx = self.ctx
            cam = ctx.declare_var("_gk_cam_query", "Node")
            lines = ctx.new_section()
            ctx.add_line(1, f"if {cam} != null and is_instance_valid({cam}):")
            ctx.add_line(2, f"return {cam}")
            ctx.add_line(1, f"{cam} = get_viewport().get_camera_3d()")
            ctx.add_line(1, f"if {cam} == null:")
            ctx.add_line(2, f"{cam} = get_viewport().get_camera_2d()")
            # ── 一台都没有 -> 造一台"观察相机"(照虚幻的默认视口)────────
            # 用户实测:"在没有指定摄像机时,我就可以观察哪里动不了" ——
            # 场景里没有相机时画面全黑,看起来就像"游戏没跑起来",
            # 完全不知道角色是不是在动。这里当场造一台挂在角色后上方,
            # 起码能看见发生了什么,并在控制台说清这是兜底的。
            # ── 有相机、但没设成"当前" -> 把第一台设为当前(不要造新的)──
            # 踩过的坑:以前只看 `get_viewport().get_camera_3d()`,它是 null
            # 就直接造一台**自由飞行**相机 —— 于是"按 WASD 角色走、相机也飞",
            # 而且手动转视角被飞行逻辑覆盖(用户实测:鼠标转不了、按方向键视角乱动)。
            ctx.add_line(1, f"if {cam} == null:")
            ctx.add_line(2, f"for found in self.get_tree().root.find_children(\"*\", \"Camera3D\", true, false):")
            ctx.add_line(3, "(found as Camera3D).make_current()")
            ctx.add_line(3, f"{cam} = found")
            ctx.add_line(3, "break")
            ctx.add_line(2, f"if {cam} == null:")
            ctx.add_line(3, f"for found in self.get_tree().root.find_children(\"*\", \"Camera2D\", true, false):")
            ctx.add_line(4, "(found as Camera2D).make_current()")
            ctx.add_line(4, f"{cam} = found")
            ctx.add_line(4, "break")
            ctx.add_line(1, f"if {cam} == null:")
            ctx.add_line(2, "_gk_make_fallback_camera(self)")
            ctx.add_line(2, f"{cam} = get_viewport().get_camera_3d()")
            ctx.add_line(1, f"return {cam}")
            # 自由飞行的相机要有"每帧动一下"的地方:挂在物理帧末尾
            ctx.physics_hooks.setdefault("fly_camera", [])
            ctx.physics_hooks["fly_camera"].append("_gk_fly_update(delta)")
            ensure_mouse_delta(ctx)
            self._append_block(sections, "_gk_current_camera",
                               "func _gk_current_camera() -> Node:",
                               list(lines.lines))
            # 造相机的小工具 —— **自由飞行**观察机(用户第 24 轮)
            # "设置默认摄像机应该是可以到处移动的,不受碰撞,可飞行"。
            # 所以不只是"挂在角色后上方不动",而是照虚幻的 Spectator:
            # WASD 平移 / 空格上升 / Shift 下降 / 鼠标转视角,
            # 而且**直接改坐标、不过物理**,想穿墙就穿墙。
            # 两个成员变量必须先声明 —— 下面的函数体要用它们,
            # 不声明就是 `Parse Error: Identifier "_gk_fly_cam" not declared`,
            # 整个脚本加载失败、所有输入都没反应(实测)
            # 相机解析:返回 Node,内部全是 if —— 不用嵌套三元
            # (Godot 的 INCOMPATIBLE_TERNARY 就是它引起的,见上面说明)
            ctx.declare_var("_gk_fly_cam", "Node")
            ctx.declare_var("_gk_fly_speed", "float")
            self._append_block(sections, "_gk_make_fallback_camera",
                               "func _gk_make_fallback_camera(host: Node) -> void:",
                               [
                                   "    # 场景里一台相机都没有 -> 造一台自由飞行观察机",
                                   "    var cam := Camera3D.new()",
                                   '    cam.name = "GK观察相机(自由飞行)"',
                                   "    host.get_tree().root.add_child(cam)",
                                   "    if host is Node3D:",
                                   "        var back := (host as Node3D).global_transform.basis.z",
                                   "        cam.global_position = (host as Node3D).global_position + back * 6.0 + Vector3(0.0, 3.0, 0.0)",
                                   "        cam.look_at((host as Node3D).global_position, Vector3.UP)",
                                   "    cam.make_current()",
                                   "    _gk_fly_cam = cam",
                                   "    _gk_fly_speed = 8.0",
                                   '    push_warning("[GK] 场景里没有相机 —— '
                                   '造了一台 GK观察相机(自由飞行)。'
                                   'WASD 平移 / 空格上升 / Shift 下降 / '
                                   '鼠标转视角(不受碰撞)。'
                                   '想用固定机位:在节点上指定相机")',
                               ])
            # 飞行控制:每帧一次(挂在角色脚本上,不用额外节点)
            self._append_block(sections, "_gk_fly_update",
                               "func _gk_fly_update(delta: float) -> void:",
                               [
                                   "    if _gk_fly_cam == null or not is_instance_valid(_gk_fly_cam):",
                                   "        return",
                                   "    if not (_gk_fly_cam as Camera3D).is_current():",
                                   "        return",
                                   "    # 鼠标转视角(用累积位移,和玩家视角同一个量)",
                                   "    if _gk_mouse_delta != Vector2.ZERO:",
                                   "        var rot := (_gk_fly_cam as Node3D).rotation_degrees",
                                   "        rot.y -= _gk_mouse_delta.x * 0.25",
                                   "        rot.x = clampf(rot.x - _gk_mouse_delta.y * 0.25, -89.0, 89.0)",
                                   "        (_gk_fly_cam as Node3D).rotation_degrees = rot",
                                   "    # WASD 平移 + 空格/Shift 升降(**不走碰撞**)",
                                   "    var fly := Vector3.ZERO",
                                   "    var basis := (_gk_fly_cam as Node3D).global_transform.basis",
                                   "    if Input.is_key_pressed(KEY_W):",
                                   "        fly -= basis.z",
                                   "    if Input.is_key_pressed(KEY_S):",
                                   "        fly += basis.z",
                                   "    if Input.is_key_pressed(KEY_A):",
                                   "        fly -= basis.x",
                                   "    if Input.is_key_pressed(KEY_D):",
                                   "        fly += basis.x",
                                   "    if Input.is_key_pressed(KEY_SPACE):",
                                   "        fly += Vector3.UP",
                                   "    if Input.is_key_pressed(KEY_SHIFT):",
                                   "        fly -= Vector3.UP",
                                   "    if fly != Vector3.ZERO:",
                                   "        (_gk_fly_cam as Node3D).global_position += fly.normalized() * _gk_fly_speed * delta",
                               ])

        # ---- 『动画_播放完成』需要的信号连接 ----
        # 状态机自己会在它的执行点连(那里本来就是 _ready);但如果用户只在
        # **玩法图**里用了『播放完成』(例如"播完了就设个变量"),就必须由
        # 生成器补一个 _ready —— 否则那个条件永远是假。
        if (self.ctx.need_anim_finished_signal
                and not getattr(self.ctx, "_anim_finished_connected", False)):
            lines = self.ctx.new_section()
            self.ctx._ensure_finished_connect(1)
            self._append_block(sections, "_ready",
                               "func _ready() -> void:", list(lines.lines))

        # ---- 自动找动画播放器的小工具 ----
        # glTF 导入时 AnimationPlayer 藏在模型实例内部(见 animation_player
        # 的说明),所以不能写死路径,得运行时找。
        if self.ctx.need_anim_finder:
            # ── 找到之后要**记住**(实测的性能问题)────────────────────
            # 这个函数会被『动画_是否在播』『动画_时长』这类节点**每帧**
            # 调用一次,而它内部是 `find_children("*", "AnimationPlayer")`
            # —— 用户那个 1058 个节点的 MMD 模型,等于每帧遍历上千个节点。
            # 所以第一次找到就缓存进成员变量,后面直接用。
            # `is_instance_valid` 是必须的:场景切换 / 节点被 free 之后
            # 缓存的引用会变成"野指针",不判会报 freed instance 错误。
            cache = self.ctx.member_temp("gk_ap")
            sections.append({
                "signature": "func _gk_find_animation_player(host: Node) -> Node:",
                "lines": [
                    f"    if {cache} != null and is_instance_valid({cache}):",
                    f"        return {cache}",
                    # 先看自己身上有没有(把 AnimationPlayer 直接放在脚本节点下也能用)
                    "    if host is AnimationPlayer:",
                    f"        {cache} = host",
                    f"        return {cache}",
                    '    var direct := host.get_node_or_null("AnimationPlayer")',
                    "    if direct is AnimationPlayer:",
                    f"        {cache} = direct",
                    f"        return {cache}",
                    # 再全场景递归找:glTF 的 AnimationPlayer 在 Model/ 下面
                    '    for found in host.find_children("*", "AnimationPlayer", true, false):',
                    f"        {cache} = found",
                    f"        return {cache}",
                    "    if host.get_tree() != null:",
                    '        for found in host.get_tree().root.find_children("*", "AnimationPlayer", true, false):',
                    f"            {cache} = found",
                    f"            return {cache}",
                    "    return null",
                ],
                "empty_body": "    pass",
                "separator": True,
            })

        # ---- 分层播放(上下半身分离)的第二个播放器 ----
        # 播放节点填了『层』≥1 时会调用它。AnimationPlayer 自己**没有**
        # 层/加法混合的概念(那是 AnimationTree 的能力),所以这里退而求其次:
        # 复制一个同骨架的播放器当"上层",谁**后处理**谁覆盖。
        # 复制出来的节点是后 add_child 的,处理顺序排在原播放器之后,
        # 于是"上层动作盖住下层动作"就成立了。
        #
        # 前提是动作按部位拆开(上半身动作只含上半身骨骼)—— 这属于用户
        # 的用法约定,不是我们能在导出时替他判断的。
        if self.ctx.need_layer_player:
            sections.append({
                "signature": "func _gk_layer_player(base: Node, layer: int) -> Node:",
                "lines": [
                    "    if base == null or base.get_parent() == null:",
                    "        return base",
                    # 缓存:同一个层只复制一次,反复播放不会一直堆节点
                    '    var cache_name := "_gk_layer_%d" % layer',
                    "    var existing: Node = base.get_parent().get_node_or_null(cache_name)",
                    "    if existing is AnimationPlayer:",
                    "        return existing",
                    "    var copy: Node = base.duplicate()",
                    "    copy.name = cache_name",
                    "    base.get_parent().add_child(copy)",
                    "    return copy",
                ],
                "empty_body": "    return base",
                "separator": True,
            })

        # ---- 动画状态机(『每帧检查』模式)的 _physics_process ----
        # 状态切换要跟移动同步判定 —— 放在渲染帧里会出现
        # "人已经走出去了状态还没切"的一帧延迟(和相机跟随同一个道理)。
        if (self.ctx.need_anim_state_process or self.ctx.need_anim_tree_process
                or self.ctx.need_anim_start_process):
            lines = self.ctx.new_section()
            # ── 开始口:条件成立才开演(每物理帧判一次)────────────────
            for starter in self.ctx.anim_starts:
                self.ctx._active.clear()
                self.ctx._current_event = starter["node"].name
                self.ctx._expr_indent = 1
                self.ctx._current_indent = 1
                lines.add(1, f"if not {starter['var']} and "
                             f"not {starter['done']}:")
                lines.add(2, f"if {starter['expr']}:")
                lines.add(3, f"{starter['var']} = true")
                lines.add(3, f"{starter['done']} = false")
                lines.add(3, f"{starter['enter']}("
                             f"{gd_string(starter['entry'])})")
            for machine in self.ctx.anim_state_machines:
                self.ctx._active.clear()
                self.ctx._current_event = machine["node"].name
                _emit_state_transitions(self.ctx, machine["node"], 1,
                                        machine["rules"], machine["var"],
                                        machine["player"])
            for machine in self.ctx.anim_trees:
                self.ctx._active.clear()
                self.ctx._current_event = machine["node"].name
                # ── 表达式求值的缩进要先摆正(踩过的坑)────────────────
                # 条件里可能有"必须先算几行"的表达式节点(射线检测之类),
                # 它们的代码行落在**当前缩进**上。不显式设一次的话,
                # 第一帧的缩进会是上一次生成留下的值(可能是 2),
                # 那几行就被写进 if 里面 —— 变量还没定义就用了。
                self.ctx._expr_indent = 1
                self.ctx._current_indent = 1
                _emit_state_graph(self.ctx, 1, machine["graph"],
                                  machine["var"], machine["player"],
                                  machine["enter"],
                                  machine.get("finished"),
                                  machine.get("finish"),
                                  machine.get("gate"))
            # ── 并进**已有的** _physics_process,不要另起一个(踩过的坑)──
            # 用户的图里既有『每当_物理帧』又有动画状态机时,这里原来会
            # 再 append 一段 `func _physics_process(_delta)`,于是脚本里
            # 出现两个同名函数 -> Godot:
            #     Parse Error: Function "_physics_process()" already exists
            # 整个脚本加载失败(示例工程里实测到)。
            # ── 参数名统一叫 `delta`(用户真跑看到的警告)──────────────
            # 这里原来写 `_delta`(为了消"参数没用"的警告),而用户自己的
            # 『每当_物理帧』生成的是 `delta` + `var _unused_delta := delta`。
            # 两段**合并成一个函数**(按函数名合并),签名保留的是先建的那份:
            # 谁的段先建,另一段就被塞进不匹配的参数名里 ——
            # 轻则 Godot 报一条 "The parameter _delta ..." 的警告(用户截图里
            # 那条),重则直接 `Identifier "delta" not declared` 编译不过。
            # 统一叫 delta,警告顶多一条(而且是"参数没用"这种无害的)。
            self._append_block(sections, "_physics_process",
                               "func _physics_process(delta: float) -> void:",
                               list(lines.lines))

        # ---- "每帧"动画事件出口需要的那段 _process 代码 ----
        # `is_playing()` + `current_animation`。
        #
        # **必须放在事件循环之后** —— `need_anim_running` 是在生成
        # `_ready` 的函数体时(也就是上面那个循环里)才被置上的。
        # 放在循环前面的话条件永远为假,`_process` 一行都不生成
        # (实测踩过:接了「每帧」却没有任何代码)。
        #
        # 而且可能整张图里根本没有用户自己的 `每当_帧` 节点 ——
        # 那时 _process 函数压根不存在,得由我们新建一个。
        if self.ctx.need_anim_running:
            lines = self.ctx.new_section()
            for event in self.ctx.anim_events:
                if event.get("kind") != "running":
                    continue
                var = event.get("var", "_gk_running")
                lines.add(1, f"if {event['player']} != null "
                             f"and not {event['name']}.is_empty():")
                lines.add(2, f"var {var}: bool = "
                             f"{event['player']}.current_animation == {event['name']} "
                             f"and {event['player']}.is_playing()")
                lines.add(2, f"if {var}:")
                self.ctx._active.clear()
                self.ctx._current_event = event["func"]
                _chain(self.ctx, event["node"], 3, event["output"])
            # 同 _physics_process:并进已有的 _process,别另起一个
            self._append_block(sections, "_process",
                               "func _process(_delta: float) -> void:",
                               list(lines.lines))

        # ---- 动画事件出口的回调函数 ----
        # 见 _stmt_play_anim。每个接了线的事件出口生成一个函数,
        # 函数体就是那个出口后面连的节点。
        if self.ctx.need_anim_signals:
            for event in self.ctx.anim_events:
                if event.get("kind") != "signal":
                    continue   # "每帧"那个走 _process,不生成函数
                lines = self.ctx.new_section()
                # Godot 的信号回调可以声明形参接收信号带的参数。
                # `animation_started` / `animation_finished` 都会带一个
                # `animation_name: StringName`,这里收下来用于比对。
                lines.add(1, "var _name: String = String(__name)")
                # 比对名字,避免"别的代码播了同一个播放器上的另一个动画"
                # 时误触发这段逻辑。
                lines.add(1, f"if {event['name']} != _name:")
                lines.add(2, "return")
                self.ctx._active.clear()
                self.ctx._current_event = event["func"]
                _chain(self.ctx, event["node"], 1, event["output"])
                sections.append({
                    "signature": f"func {event['func']}(__name: StringName) -> void:",
                    "lines": list(lines.lines),
                    "empty_body": "    pass",
                    "separator": True,
                })

        # ---- 动画名留空时用的"随便挑一个"工具 ----
        if self.ctx.need_first_animation:
            sections.append({
                "signature": "func _gk_first_animation(player: Node) -> String:",
                "lines": [
                    "    if player == null:",
                    '        return ""',
                    "    var names: PackedStringArray = player.get_animation_list()",
                    "    if names.is_empty():",
                    '        return ""',
                    "    return names[0]",
                ],
                "empty_body": '    return ""',
                "separator": True,
            })

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

        # ---- 事件处理函数(触屏拖动 / 鼠标滚轮 …)------------------
        # 函数体是**登记时带过来的** —— 每张表项各写各的,
        # 不然"同一个名字两份体"会让 Godot 拒绝整个脚本(踩过)。
        for entry in self.ctx._input_handlers:
            func_name, reset = entry[0], entry[1]
            body = list(entry[2]) if len(entry) > 2 else []
            if not body:
                body = [
                    "    if event is InputEventScreenDrag:",
                    "        _gk_touch_drag += "
                    "(event as InputEventScreenDrag).relative",
                ]
                if reset:
                    body += [
                        "    elif event is InputEventScreenTouch:",
                        "        if not (event as InputEventScreenTouch).pressed:",
                        "            _gk_touch_drag = Vector2.ZERO",
                    ]
            sections.append({
                "signature": f"func {func_name}(event: InputEvent) -> void:",
                "lines": body,
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
    @staticmethod
    def _section_for(sections, func_name):
        """找已经存在的那一段函数(按**函数名**找,不看参数名)。

        ── 为什么只比函数名(踩过的坑)────────────────────────────────
        用户自己的『每当_物理帧』生成的签名是
            func _physics_process(delta: float) -> void:
        而状态机生成的是
            func _physics_process(_delta: float) -> void:
        参数名不一样(`delta` 用了就写 `delta`,没用就写 `_delta` 消警告),
        于是"按完整签名找"永远找不到,结果同一个脚本里出现**两个**
        `_physics_process` —— Godot 直接报
            Parse Error: Function "_physics_process()" already exists
        整个游戏跑不起来。实测在示例工程里踩到。
        """
        needle = f"func {func_name}("
        for section in sections:
            if section["signature"].startswith(needle):
                return section
        return None

    def _append_block(self, sections, func_name, signature, lines):
        """把我们生成的代码块并进已有的同名函数,没有才新建一段。"""
        target = self._section_for(sections, func_name)
        if target is None:
            sections.append({
                "signature": signature,
                "lines": list(lines),
                "empty_body": "    pass",
                "separator": True,
            })
        else:
            target["lines"].extend(lines)

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

        # -- 跨函数共享的成员变量 ----------------------------------
        # 见 member_temp 的说明:这些值在 _ready() 里赋值、
        # 在 _physics_process() 里用,必须是成员变量。
        # 先声明成 null,_ready() 里再赋真值 -- 这样即使某个函数
        # 先被调用也只是拿到 null,而不是"标识符未声明"的编译错误。
        if ctx.member_vars:
            out.append("# ---- 跨函数共享的引用(在 _ready 里赋值) ----")
            for name in ctx.member_vars:
                out.append(f"var {name} = null")
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

        # ---- 按分类整理的动作表 ----
        # 用户在『动作分类』面板里打的标签(<Action>[gk_anim_category])
        # 在这里变成一张常量表,Godot 侧可以按分类挑动作:
        #     GK_ANIM_BY_CATEGORY["攻击"].pick_random()
        # 一个标签都没打时**不生成** —— 老图生成的代码一行都不变。
        anim_table = _anim_category_table()
        if anim_table:
            out.append("# ---- 按分类整理的动作表(『动作分类』面板里打的标签)----")
            out.append("const GK_ANIM_BY_CATEGORY := {")
            ordered = [c for c in ANIM_CATEGORY_NAMES if c in anim_table]
            ordered += sorted(c for c in anim_table if c not in ANIM_CATEGORY_NAMES)
            for category in ordered:
                names = ", ".join(gd_string(n) for n in anim_table[category])
                out.append(f"    {gd_string(category)}: [{names}],")
            out.append("}")
            out.append("")

        if ctx.onready:
            out.append("# ---- 场景节点引用 ----")
            # ── 引用用**带兜底的查找**,不写死 `$"节点名"`(用户真跑报告)──
            # 用户的调试信息里是这两行:
            #     ERROR: Node not found: "侧视机位"
            #         (relative to "/root/World/Colliders/示例角色")
            # 原因:导出时脚本挂在**碰撞代理**物体上,而用户在 Blender 里
            # 摆的相机是它的**兄弟节点**(World 下),`$"侧视机位"` 是相对
            # 路径 -> 找不到 -> 『相机_切换』整段失效(表现就是"切相机没用")。
            #
            # 现在生成的是一句带兜底的查找:先按路径找,找不到就在整棵树里
            # **按名字**找一遍,再找不到才告警。这样"物体怎么摆"都不影响
            # 引用能不能解析 —— 顺带也修好了"用户把相机挪到别的父节点下"。
            for var, (path, gd_type) in ctx.onready.items():
                name = _path_name(path)
                out.append(f"@onready var {var}: {gd_type} = "
                           f"_gk_ref(self, {gd_string(name)})")
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
                # 动画树的自定义变量带初值(侧栏『变量』里填的那个);
                # 其它变量没有初值,保持原来的 `var x: T` 写法不变 ——
                # 老图生成的代码必须一个字符都不差(有测试钉着)。
                initial = ctx.var_initials.get(name)
                if initial is None:
                    out.append(f"var {name}: {gd_type}")
                else:
                    out.append(f"var {name}: {gd_type} = {initial}")
            out.append("")

        # ── 运行日志(用户第 29 轮)────────────────────────────────────
        # 开关打开时,每秒在 Godot 控制台打一行:
        #   位置 / 速度 / 是否在地面 / 当前相机 / 刚体状态
        # 用户直接复制控制台就能告诉我"运行时到底怎么回事",
        # 不用靠截图猜 —— 和『导出诊断信息』配成一对(编辑期 / 运行期)。
        if bool(self.options.get("debug_runtime", False)):
            self._append_block(
                sections, "_process",
                "func _gk_debug_tick(delta: float) -> void:",
                [
                    "    _gk_debug_timer += delta",
                    "    if _gk_debug_timer < 1.0:",
                    "        return",
                    "    _gk_debug_timer = 0.0",
                    "    var pos := global_position if self is Node3D else Vector3.ZERO",
                    "    var vel := velocity if self is CharacterBody3D else Vector3.ZERO",
                    "    var floor_ok := is_on_floor() if self is CharacterBody3D else false",
                    "    var cam := get_viewport().get_camera_3d()",
                    "    print(\"[GK-DBG] pos=(%.1f, %.1f, %.1f) vel=(%.1f, %.1f, %.1f) 在地面=%s 相机=%s\" % [pos.x, pos.y, pos.z, vel.x, vel.y, vel.z, str(floor_ok), String(cam.name) if cam != null else \"(无)\"])",
                ])
            self.ctx.process_hooks.setdefault("debug", [])
            self.ctx.process_hooks["debug"].append("_gk_debug_tick(delta)")

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


def _check_animation_targets(tree, warnings):
    """检查动画节点选的动作和它要播的物体对不对得上。

    ── 回答用户的问题:"如果应用动画物体错误了会怎么样" ─────────────
    **以前是静默失败** —— Godot 里 AnimationPlayer 找不到那个动画,
    只在控制台打一条警告,画面看不出问题,用户不知道哪错了。

    现在**导出时**就报出来。判据见 nodes.check_action_target。
    """
    try:
        from .nodes import nodes as _nodes
    except Exception:
        return
    checker = getattr(_nodes, "check_action_target", None)
    if checker is None:
        return
    try:
        import bpy
        objects = getattr(getattr(bpy, "data", None), "objects", None)
    except Exception:
        objects = None
    if objects is None:
        # 离线测试环境:没有真 bpy,跳过这项检查
        return

    for node in getattr(tree, "nodes", ()) or ():
        if node.bl_idname != "GKNodePlayAnim":
            continue
        # 节点上『动画播放器』填了就用它;留空 = 自动查找,
        # 那种情况没法在导出时确定目标,跳过(运行时才找得到)。
        target_name = str(getattr(node, "gk_player", "") or "")
        if not target_name:
            continue
        obj = objects.get(target_name)
        if obj is None:
            continue
        message = checker(node, obj)
        if message:
            warnings.append(message)


def required_base_class(tree, options=None):
    """扫一遍节点图,返回 (需要的基类, 为什么) —— 不需要就 (None, "")。

    ── 为什么要在生成**之前**扫 ────────────────────────────────────
    基类会写进生成的 `extends X` 那一行。等生成完再发现"这个基类
    没有 is_on_floor()"就晚了 —— 得整个重生成。
    所以先静态扫一遍节点类型,提前把基类定下来。

    目前只有动画树的『动画判断_移动状态』会提需求(它用
    `is_on_floor()` / `velocity`,只有 CharacterBody 有)。
    以后有别的节点提需求,在 `_BASE_CLASS_NEEDS` 里加一条就行。
    """
    if tree is None:
        return None, ""
    dim = str((options or {}).get("dimension", "3"))
    # 动画树不在游戏逻辑图里,得单独找出来一起扫
    trees = [tree]
    try:
        import bpy
        for group in getattr(getattr(bpy, "data", None), "node_groups", ()) or ():
            if getattr(group, "bl_idname", "") == "GKAnimationTree":
                trees.append(group)
    except Exception:
        pass

    for candidate in trees:
        for node in getattr(candidate, "nodes", ()) or ():
            need = _BASE_CLASS_NEEDS.get(getattr(node, "bl_idname", ""))
            if need is None:
                continue
            if callable(need):
                result = need(dim)
            else:
                result = need
            if result:
                return result, (f"节点『{getattr(node, 'bl_label', '?')}』"
                                f"用到了它才有的成员")
    return None, ""


# 节点类型 -> 需要什么基类(或一个根据维度返回基类的函数)
_BASE_CLASS_NEEDS = {
    # is_on_floor() / velocity 只有 CharacterBody 有
    "GKNodeAnimLocomotion": lambda dim: ("CharacterBody2D" if dim == "2"
                                         else "CharacterBody3D"),
    # 移动/跳跃这些也是 CharacterBody 的活
    "GKNodeMoveAndSlide": lambda dim: ("CharacterBody2D" if dim == "2"
                                       else "CharacterBody3D"),
    "GKNodeMoveDirection": lambda dim: ("CharacterBody2D" if dim == "2"
                                        else "CharacterBody3D"),
    "GKNodeJump": lambda dim: ("CharacterBody2D" if dim == "2"
                               else "CharacterBody3D"),
}


def generate(tree, options=None):
    """生成 GDScript，返回 (代码, 告警列表)。"""
    if tree is None:
        raise CodegenAbort("没有可导出的节点树")
    generator = Generator(tree, options)
    code = generator.generate()
    warnings = list(generator.ctx.warnings)
    _check_base_class_supports_graph(tree, options or {}, warnings)
    _check_animation_targets(tree, warnings)
    # 登记本次用到的输入动作，供导出流程写进 project.godot
    # 同时把用户在『输入_按键』里**捕获的键**一起带上(动作名, 键)——
    # 他在 Blender 里按 W,Godot 里 W 就该真的能用,而不是又绑回默认的 D。
    # 提示(正常用法)单独给出去 —— 导出报告里展示,但不算告警
    globals()["last_notes"] = list(generator.ctx.notes)
    globals()["last_used_actions"] = [
        (action, generator.ctx.action_keys.get(const, ""))
        for const, action in generator.ctx.input_actions.items()
    ]
    return code, warnings


def generate_to_dict(tree, options=None):
    code, warnings = generate(tree, options)
    return {"code": code, "warnings": warnings, "lines": code.count("\n") + 1}
