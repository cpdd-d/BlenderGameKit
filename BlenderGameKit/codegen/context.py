"""生成上下文与生成器状态机。

`CodegenAbort` 是导出流程的致命错误;`_Statements` 是行缓冲;`GenContext` 持有遍历过程中的全部状态(临时变量、成员变量、事件函数、动画变量、输入动作……),并提供各发射器共用的取值助手。"""

import contextlib
from .. import gk_sockets as S
from ..nodes.node_base import TARGET_SELF, gd_node_path, gd_string, snake_case
from ..models import Diagnostic
from ..nodes import nodes as node_lib

from .literals import EVENT_PARAM_NAMES, _coerce_text_to_literal, _gd_vector_ctor, gd_literal, gd_node_type, gd_number, gd_vector
from .registry import EXPR_HANDLERS, STMT_HANDLERS, _chain, expression_for_output




class CodegenAbort(Exception):
    """导出流程中的致命错误，由 UI 捕获后显示给用户。"""


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
        # 这一关有没有烤导航网格(第三十四轮:寻路)。
        # 由 Generator.generate 按 options 覆盖 —— 『移动_寻路』靠它决定
        # "绕路走"还是"退化成直线走"(没烤网格时不该站着不动)。
        self.nav_enabled = bool(self.options.get("nav_enabled", False))

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
        # 「每节点一份」的成员变量后缀表:kind -> {节点: 后缀}(见 node_tag)
        self._node_tags = {}
        # 本次生成用到的曲线资源 {曲线名: {"kind": ..., "points": [...]}}
        # (由 codegen/easing.py 登记,exporter 导出时写成 .tres)
        self.curve_assets = {}
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
        self._claimed = {}         # 节点 -> {入口引脚名: 哪个事件函数已经"拥有"它}
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
        # 『动画_事件』(播到第 N 秒触发一次)也要在 _process 里插一段检查
        # (第七十六轮)—— 同样登记进 anim_events,kind = "time"。
        self.need_anim_time = False
        # 每次播放 / 每个动画事件登记一条:
        #   {player_expr, name_expr, func, node, output, kind, signal}
        # 注意存的是**表达式**不是局部变量名 —— 这些代码要落到别的函数里
        self.anim_events = []
        # 动画状态机(『每帧检查』模式)- 整段逻辑挂进 _physics_process
        self.anim_state_machines = []
        self.need_anim_state_process = False
        # 动画树(独立那张图)驱动的状态机 —— 同样挂 _physics_process
        self.anim_trees = []
        self.need_anim_tree_process = False
        # 动画混合树(第六十一轮):每帧把『参数』推给 AnimationTree 的
        # blend_position。登记 {node, var, param, smooth, debug}
        self.anim_blend_trees = []
        # ── AI 图(『游戏AI』,第四十一轮)────────────────────────────
        # 每台『AI_启用』登记一条:{node, graph, var, self, debug, flip} ——
        # 由 generator 统一发射到 _physics_process(先行为、后转场),
        # 和动画树共用"合并进已有的 _physics_process"那套。
        self.ai_machines = []
        self.need_ai_process = False
        # 『动画_开始』的开始条件 —— 要在每物理帧判一次"现在能不能开始"
        self.anim_starts = []
        self.need_anim_start_process = False
        # 成员变量名 -> 已经生成的"_进入状态"函数名。
        # 同一个变量只生成一个函数(见 enter_anim_state_func);
        # 不同的动画树各有各的状态变量,所以是按变量名分开的。
        self.anim_enter_funcs = {}
        # 已经生成过"启动块"的时间轴节点(见 statements._stmt_timeline)。
        # 时间轴有『执行』/『重播』两个入口,第二个入口只**再调一次**
        # 启动函数 —— 不记的话整段会被生成两遍:每帧钩子跑两次(时间轴
        # 双倍速),分支函数重名(Godot 直接拒绝整个脚本)。
        self._timeline_started = set()
        # 动画名留空时要生成 _gk_first_animation()(见 _stmt_play_anim)
        self.need_first_animation = False
        # 用了『层』≥1 的播放节点时要生成 _gk_layer_player()(上下半身分离)
        self.need_layer_player = False
        # 只有真的用了存档节点才生成那几个读写文件的辅助函数
        self.need_save_helpers = False
        # 存档防改档(第九十三轮):任一『存档_保存』勾了『防改档』才置位。
        # 置位后多生成两样东西:脚本头的盐常量 + 带校验的读写辅助函数
        # (读侧自描述 —— 没有 gk_check 的裸 JSON 老存档照旧读)。
        # 一个都没勾时逐字保持老样子,老图生成物一行都不变。
        self.need_save_verify = False
        # 只有真的用了『物理_启用碰撞』才生成 _gk_set_collision()
        # (用户 2026-10-05)—— 见 generator 里那段说明。
        self.need_collision_helper = False
        # 只有真的用了『资产_创建实例』才生成 _gk_spawn_asset()
        self.need_asset_spawn_helper = False
        # 网络联机(第一百轮):用过联机节点才生成那组 ENet 辅助函数 +
        # `@rpc` 收消息方法(见 codegen/network.py)
        self.need_net = False
        # 『资产_创建实例』节点 -> 它生成的那个**临时变量名**。
        # 节点的『实例』输出就是它(见 statements._expr_asset_spawn_outputs):
        # 运行时的节点引用只能靠变量传,而创建和用它通常在一条执行流上,
        # 局部变量就够 —— 不用为它开一个成员变量。
        self.asset_instances = {}
        # ── 函数(用户 2026-10-05 第十七轮)────────────────────────────
        # 函数**参数名** -> 类型:参数只在函数体里有效,不能按成员变量生成
        # (生成的话每个函数都往脚本顶部塞一个同名成员变量,Godot 还会报
        #  SHADOWED_VARIABLE)。_assemble 据此跳过它们。
        self.func_params = {}
        # 『函数_调用』节点 -> 存返回值的临时变量名(『结果』输出就是它)
        self.function_results = {}
        # 已经生成过的函数名(同名只能有一份 —— Godot 不允许同名函数)
        self.defined_functions = set()
        # 只有真的用了界面节点才生成 UI 容器辅助函数
        self.need_ui_helpers = False
        # ── UI 第三十七轮:暂停菜单 / 对话框 ─────────────────────────
        # 暂停:『界面_暂停游戏』勾了『Esc 也能开关』-> 生成挂
        # `process_frame` 信号的监听(暂停也照跑,否则按 Esc 解不了冻)
        self.need_pause_helpers = False
        # 对话框:生成 _gk_dialog_* 那组辅助函数 + 往 _input 里挂点按采集
        self.need_dialog_helpers = False
        # 字幕(第六十四轮):生成 `_gk_subtitle_show()`(复用对话框面板,
        # 但**不阻塞** —— 飘一行字,过几秒自己消失)
        self.need_subtitle_helpers = False
        # 对话历史回顾(第六十四轮):生成 `_gk_history_show/_hide()`
        self.need_history_helpers = False
        # 对话历史的导出 / 点句重放(第八十一轮):
        # 各自生成 `_gk_history_export()` / `_gk_history_replay()`,
        # 没填的图一个字都不多
        self.need_history_export = False
        self.need_history_replay = False
        # 输入框的输入法跟随(第八十一轮):生成 `_gk_ime_update()`
        self.need_ime_helper = False
        # 对话选择(第四十九轮:阶段③『对话_选择』分支):
        # 生成 _gk_choice_* 那组辅助函数(和对话框共用 UI 容器,不冲突)
        self.need_choice_helpers = False
        # ── UI 第二版(第三十八轮)─────────────────────────────────────
        # 列表:生成 `_gk_list_body()`(找列表里那个**排版的容器** ——
        # 子控件挂在它上面才排队,挂到滚动容器本身会浮着)
        self.need_list_helper = False
        # 列表_填充:生成 `_gk_list_fill()`(清空旧行 + 按数组建 Label)
        self.need_list_fill_helper = False
        # 列表_填充勾了『每行可点』(第一百零六轮):生成 `_gk_list_fill_click()`
        # (每行建成按钮 + 同一个按钮组 = 单选,选中那行走 pressed 态高亮)
        self.need_list_fill_click_helper = False
        # 界面_取文字:生成 `_gk_widget_text()`(按名字取控件上的文字)
        self.need_widget_text_helper = False
        # ── 设置面板三件套(第一百零四轮)────────────────────────────
        # 滑块『值』/ 下拉『选了第几个』/ 复选框『勾了吗』——
        # 三个"按名字读控件状态"的小函数,各用各的才生成
        self.need_widget_value_helper = False
        self.need_widget_index_helper = False
        self.need_widget_checked_helper = False
        # 『界面_隐藏控件』勾了『置顶』:生成 `_gk_ui_raise()`
        # (挪到最上层 + 收起同互斥组的其他面板)
        self.need_raise_helper = False
        # 面板勾了『打开时首焦点』(第一百零五轮):生成 `_gk_ui_focus_first()`
        # (显示时按"上->下、左->右"把能聚焦的控件串一条链,焦点给第一个)
        self.need_focus_first_helper = False
        # 只有真的用了手柄节点才生成摇杆死区辅助函数
        self.need_joy_helpers = False
        # 只有真的用了屏幕滤镜才生成全屏材质辅助函数
        self.need_screen_filter = False
        # 屏幕暗角(第五十一轮:阶段④屏幕氛围)—— 和滤镜**各占一层**:
        # 两个可以叠着用(整屏染色 + 四角压暗),互不改写对方的着色器
        self.need_screen_vignette = False
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
        """记一条告警。`message` 可以是字符串,也可以是 `models.Diagnostic`。

        ── 为什么两种都收(GPT 审查 / 用户 2026-10-04)─────────────────
        把 40 多处节点级告警一次性改成结构化对象,改动面大、容易出错。
        所以分两步走:

          * 校验类告警(`validation.py`)已经用 `Diagnostic` 传进来,带稳定
            的 `code` 与修复建议 `hint`,导出报告能按 code 归类;
          * 节点级告警暂时仍是字符串,以后可以逐条升级,不必一次性全改。

        去重按**摊平后的文本**比较,所以同一个问题无论以字符串还是
        `Diagnostic` 形式重复报告,都只会留一条。
        """
        if isinstance(message, Diagnostic):
            text = message.render()
            incoming = message
        else:
            text = str(message)
            incoming = text
        for existing in self.warnings:
            existing_text = (existing.render()
                             if isinstance(existing, Diagnostic)
                             else str(existing))
            if existing_text == text:
                return
        self.warnings.append(incoming)

    def new_temp(self, prefix="t"):
        self._temp_seq += 1
        return f"_{snake_case(prefix, 't')}{self._temp_seq}"

    def node_tag(self, node, kind):
        """给"每个节点各占一份"的成员变量算一个后缀。

        ── 为什么需要(照『相机_弹簧臂』那个坑)────────────────────────
        同一张图里可以放**多个**同类型节点(两条弹簧臂、两条时间轴),
        而它们的状态(当前时间 / 播放中 / 轨道值)必须各管各的。用同一组
        名字的话,第二个节点每帧把第一个的状态覆盖掉 —— 表现就是
        "两个只有最后一个生效"(弹簧臂当年就踩过)。
        第一个拿到空后缀(老图产物名字不变),后面的 `_2` / `_3`。

        kind 用来分开不同节点类型各自计数(弹簧臂 _1、时间轴 _1 互不干扰)。
        """
        table = self._node_tags.setdefault(kind, {})
        if node not in table:
            table[node] = "" if not table else f"_{len(table) + 1}"
        return table[node]

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
        # ── 函数参数(用户 2026-10-05 第十七轮)──────────────────────────
        # 参数是**函数局部**的:函数体里读同名变量时,读到的就是那个形参。
        # 所以这里**不声明成员变量**(见 generator._assemble 里跳过它们),
        # 也不能被改名 —— 改了名就和函数签名里的形参对不上了。
        if clean in getattr(self, "func_params", {}):
            if gd_type and self.func_params.get(clean) in (None, "Variant"):
                self.func_params[clean] = gd_type
            return clean
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
        # ── 事件节点的"数据输出"(第一百轮:联机)─────────────────────
        # 事件节点(有人进来 / 收到消息)的输出不是一个表达式,而是"回调
        # 先写进成员变量、用户那条链再读它"。节点类用
        # `gk_event_output_vars` 声明"哪个输出对应哪个成员变量"。
        owner = self._socket_source_event(socket)
        if owner is not None:
            mapping = getattr(owner, "gk_event_output_vars", None)
            if mapping and name in mapping:
                return mapping[name]
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
            # ── 类型可以**升级**,但绝不能降级(用户 2026-10-05 第二十四轮)──
            # 同一个物体可能先被『移动_设置位置』引用(它要的是 Node),
            # 再被『进入区域』引用(它要的是 Area3D)。只按路径去重的话,
            # 第二次会直接拿到那个 Node 类型的变量,于是生成
            #     if 节点_敌1 != null and 节点_敌1.body_entered...
            # 而运行时它是 **MeshInstance3D**(网格那个同名节点)——
            #     Invalid access to property or key 'body_entered' on a
            #     base object of type 'MeshInstance3D'
            # 触发器**整个失效**(小游戏示例的探针抓到的)。
            # 反过来(先 Area3D 后 Node)没问题 —— Area3D 就是 Node,
            # 所以只往具体的方向升。
            wanted = gd_node_type(path, hint=hint, dim=self.dim)
            entry = self.onready.get(cached)
            if entry is not None and wanted not in (None, "", "Node", "Variant") \
                    and entry[1] in (None, "", "Node", "Variant"):
                self.onready[cached] = (entry[0], wanted)
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
    def target_expr(self, node, prop="gk_target", hint="节点", node_type=""):
        """节点引用:自身 -> self，其它 -> @onready 变量。

        node_type 给了就用**它**当类型,而不是按名字猜(见
        literals.gd_node_type 的 "type:" 分支)。粒子发射器用这个:
        同一个物体名在 Godot 里可能是 GPUParticles3D,也可能是
        CPUParticles3D(面板上选的 CPU/GPU)——名字里根本看不出来。
        """
        raw = getattr(node, prop, TARGET_SELF) or TARGET_SELF
        if raw == TARGET_SELF:
            return "self"
        if node_type:
            return self.add_onready(f"{hint}_{raw}", gd_node_path(raw),
                                    hint=f"type:{node_type}")
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
        # ── 填了物体名:那个物体**不是**播放器(第二十七轮修)──────────
        # glTF 把 AnimationPlayer 放在**模型实例内部**,用户在『动画播放器』
        # 里能选到的只有场景物体(碰撞代理 / 网格),不是那个播放器本身。
        # 以前直接把物体当播放器用 —— 生成出来的代码运行时报
        #     Invalid call. Nonexistent function 'has_animation' in base 'Area3D'
        # 而且**整条链就此断掉**(后面的节点全不执行)。
        # 现在从那个物体开始查:先看它自己/子树(glb 内部),再退回全树。
        self.need_anim_finder = True
        return (f"_gk_find_animation_player("
                f"{self.target_expr(node, 'gk_player', hint='anim')})")

    def enter_anim_state_func(self, node, var_name, player_temp, debug=False,
                              finished_var=None, enter_hooks=None,
                              exit_hooks=None, speed_func=None,
                              blend_param=False):
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
        # speed_func 也要进缓存键:同一张图被两台状态机驱动时,
        # 一台带速度表一台不带,生成出来的函数体是不一样的
        # (blend_param 同理 —— 一个带过渡时长参数、一个不带)
        key = (var_name, finished_var, tuple(sorted(enter_hooks.items())),
               tuple(sorted(exit_hooks.items())), speed_func, blend_param)
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
        # ── 播放速度(第三十九轮)────────────────────────────────────
        # 状态的『播放速度』填了非 1.0 时,play() 带上它 ——
        # 有速度表就走 `_gk_anim_speed(状态名)`,没有就还是原来的
        # `play(名字)`(老图一字不变)。
        # ── 过渡时长(第七十五轮)────────────────────────────────────────
        # 开了 blend 参数时,play 的第二个参数(custom_blend,单位秒)用传进来的
        # 那个 —— 于是走↔跑这种切换是**交叉淡化**而不是硬切。没开(老图)时
        # 一字不变:还是 `play(next_state)` / `play(next_state, -1.0, 速度)`。
        play_line = f"        {player_temp}.play(next_state)"
        if blend_param and speed_func:
            play_line = (f"        {player_temp}.play(next_state, blend, "
                         f"{speed_func}(next_state))")
        elif blend_param:
            play_line = f"        {player_temp}.play(next_state, blend)"
        elif speed_func:
            play_line = (f"        {player_temp}.play(next_state, -1.0, "
                         f"{speed_func}(next_state))")
        body += [
            f"    if {player_temp} == null:",
            # 找不到播放器时也要说一句 —— 否则用户只看到"状态在切、人不动"
            '        push_warning("[GK] 没找到 AnimationPlayer,'
            '动画状态机没法播动画")',
            "        return",
            f"    if {player_temp}.has_animation(next_state):",
            play_line,
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
        signature = f"func {func_name}(next_state: String) -> void:"
        if blend_param:
            # 过渡时长(秒)由**调用方**给(每条转移可能不一样)——
            # 默认 -1.0 = 用 Godot 自己的默认混合时间,等价于原来的硬切。
            signature = (f"func {func_name}(next_state: String, "
                         f"blend: float = -1.0) -> void:")
        self.extra_sections.append({
            "signature": signature,
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
