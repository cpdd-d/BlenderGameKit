"""`Generator` 主体:扫描节点树、组装事件函数、拼出最终 GDScript。"""

from ..nodes.node_base import (TARGET_SELF, gd_node_path, gd_string,
                               safe_function_name, snake_case)
from ..nodes.nodes import (ANIM_CATEGORY_NAMES, MAIN_UI_AUTOLOAD,
                           action_category, main_ui_design_tree, node_key,
                           parse_transitions, transition_pin_name)

from .literals import (EVENT_KINDS, TRIGGER_KINDS, _BANNER, gd_class_name,
                       gd_number)
from .registry import _chain, _emit_body, _next_nodes
from .statements import _emit_logic_ports, _emit_logic_vars
from .animation import (_anim_category_table, _emit_blend_update,
                        _emit_state_graph, _emit_state_transitions)
from .ai import _emit_ai_graph
from .camera import _path_name
from .services import (_FILTER_SHADER_CONST, _GD_FILTER_SHADER,
                       _GD_VIGNETTE_SHADER, _SAVE_SALT, _SAVE_SALT_CONST,
                       _VIGNETTE_SHADER_CONST, _emit_design_into,
                       _ui_looks_transient, _ui_root)
from .network import emit_network
from .context import GenContext
from .graph import position_key




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
        # ── 再声明侧栏钦定的变量(用户 2026-10-04)────────────────────
        # 同样必须在遍历节点之前:这里定了类型和初值,后面第一个
        # 『变量_设置』才会复用这份声明,而不是"按连线现推"。
        _emit_logic_vars(self.ctx)
        # 运行日志用的计时器变量:同样要**提前声明**(遍历节点之后再声明,
        # 变量清单已经写进 out 了 -> Godot 报 not declared。端口那次踩过)
        if bool(self.options.get("debug_runtime", False)):
            self.ctx.declare_var("_gk_debug_timer", "float")
        # 游戏内调试面板(第二十二轮)的日志缓冲:面板从宿主脚本里读它,
        # 所以**必须带初值 `= []`**(不然面板读到一个 null)。同样提前声明。
        if bool(self.options.get("debug_panel", False)):
            self.ctx.declare_var("_gk_debug_logs", "Array", initial="[]")
        self.functions = []   # (函数名, delta 参数名, 起始行, 事件节点)

    # ---- 主入口 ----
    def generate(self):
        # 上帝摄像机(关卡测试)开着时,角色的控制要让位给上帝机 ——
        # 『移动_按方向』『移动_跳跃』和鼠标位移累积都靠这个标志决定
        # 要不要加守卫。**必须在遍历节点之前设好**(语句是按图生成的)。
        # 上帝机本身由环境脚本生成(见 asset_export.ENVIRONMENT_SCRIPT),
        # 不再进角色脚本 —— 这样没有角色的场景也能飞着看。
        self.ctx.god_camera_enabled = bool(self.options.get("god_camera", False))

        # ── 主界面模式(第一百一十二轮)────────────────────────────────
        # 『主界面』不走事件那套:整张设计图(控件 + 自带行为)就是
        # `_ready` 的内容 —— 它由导出的 autoload 在游戏启动时建一次,
        # 之后切关卡也不重建。除"事件收集"之外的全部环节(helper 发射、
        # process/physics 挂钩、组装)与普通生成**完全共用同一份代码**,
        # 所以暂停热键 / 对话框输入这些事情在 autoload 里同样成立。
        ui_main = bool(self.options.get("ui_main", False))
        # ── 主界面脚本要单独说明自己的身份(第一百二十六轮)──────────────
        # 用户报:"ESC 菜单出现时旧的界面还压在上面"(配图:章节卡「第一章:
        # 门厅」糊在暂停面板的"继续游戏"上)。真机量出来的原因之一:
        # 主界面那份 CanvasLayer 是**后加进 /root 的**(autoload 的树位置
        # 排在关卡前面,但层号同为 10)—— 层号相同时由树序决定,于是关卡
        # 自己那份 GKUI 画在主界面**之上**,暂停菜单挪到最前也跨不过去
        # (move_to_front 只管同一个父节点下的兄弟)。所以主界面这份要
        # 单独抬到 layer 20:面板 + 压暗底一起盖住关卡自己的界面。
        # 层号 20 是算过的:必须**大于**普通界面(10),又要**小于**屏幕
        # 滤镜(90)/ 暗角(92)/ 闪白转场(100)—— 那些是"盖住一切"的全屏效果。
        self.ctx.is_main_ui = ui_main
        events = [] if ui_main else self._collect_events()
        if not events and not ui_main:
            self.ctx.warn("节点树里没有任何事件节点，生成的脚本不会做任何事。"
                          "请从『每当_准备』或『每当_物理帧』开始连线。")

        self._prepass(events)

        # 每生成完一段就记下它占了 ctx.lines 的哪几行，供 _assemble 精确拼接
        sections = []
        if ui_main:
            # ⚠ 顺序要紧:`_emit_ui_main_sections()` **里面**就去遍历设计图了,
            #   所以"要不要压暗底"的标志得等它跑完才是最终值 —— 底座的
            #   函数定义在这之后补,而"初始化调用"在那之前就写进 `_ready()`。
            #   主界面没有『界面_暂停游戏』(填了菜单控件)时:底照建(它默认
            #   隐藏,一个 ColorRect 而已),但没人会去点亮它 —— 不额外写
            #   "删掉刚才那行"的逆向代码,少一处会漂的地方。
            sections.extend(self._emit_ui_main_sections())
            if self.ctx.need_pause_dim:
                sections.extend(self._emit_pause_dim())

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
            nodes.sort(key=lambda n: (getattr(n, "gk_order", 0) or 0, *position_key(n)))
            return rank

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
            # ── `var _unused_delta` 一个函数里**只能声明一次** ──────────
            # 真机踩到(第五十七轮,AI 示例的探针里):树里有两个
            # 『每当_物理帧』时,每个都往**同一个函数体**里塞一句
            # `var _unused_delta := delta` -> Godot:
            #     Parse Error: There is already a variable named
            #     "_unused_delta" declared in this scope.
            # **整个脚本编译不过**,游戏直接不跑(而且只报这一句,很难联想到
            # "是因为放了两个物理帧节点")。所以这里记一下声明过没有。
            declared_unused = False
            for index, node in enumerate(nodes):
                if index > 0:
                    lines.add(1, "# ---- 另一个同类事件 ----")
                self.ctx._active.clear()
                self.ctx._current_event = node.name
                if (delta_name and not declared_unused
                        and self._delta_missing(node)):
                    lines.add(1, f"var _unused_{delta_name} := {delta_name}")
                    declared_unused = True
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

        # ---- 函数定义(用户 2026-10-05 第十七轮:"自定义函数")────────────
        # 每个『函数_定义』节点 = 一个 GDScript `func`:
        #   签名按**侧栏『函数』框里的声明**(名字/返回/参数)生成;
        #   函数体 = 从它的『执行』口接出去的那条链。
        #
        # 参数只在函数体里有效 —— 生成函数体期间把它们登记进
        # ctx.func_params:这段时间里『变量_读取(参数名)』会被当成**形参**
        # 直接用(不会去声明一个同名成员变量,Godot 也就不会报
        #  SHADOWED_VARIABLE)。函数体生成完就把这份登记收回去,
        # 别的地方读同名变量仍是普通变量。
        self._emit_functions(sections)

        # ---- 触发器节点(进入/离开区域、碰撞、定时)────────────────────
        # 用户 2026-10-04 的第 6 条。**不能**像事件那样按名字合并 ——
        # 每个触发器是一条独立的信号回调,合并会把两处逻辑串进一个函数。
        # 所以每个节点用 `declare_func_name` 各取一个函数名,函数体就是它
        # 『执行』后面那条链;信号连接统一补进 _ready(和动画的
        # animation_finished 同一个做法:_ready 只做一次性的事)。
        trigger_ready = []
        for node in events:
            spec = TRIGGER_KINDS.get(getattr(node, "bl_idname", ""))
            if spec is None:
                continue
            signal_name, param_name, param_types, func_base = spec

            # ── 区域引用是必须的,先校验再动代码 ────────────────────
            # 留空时**不能**退回 `self.body_entered` —— 脚本 extends 的是
            # CharacterBody3D,静态类型上根本没有 body_entered,生成的脚本
            # 会连解析都过不了(整个游戏跑不起来)。所以这里直接跳过并告警,
            # 让用户去填区域名(在**生成任何代码之前**就跳过,别留下半截)。
            area_expr = ""
            if param_name:
                raw_area = str(getattr(node, "gk_area", "") or "").strip()
                if not raw_area or raw_area == TARGET_SELF:
                    self.ctx.warn(
                        f"触发器『{node.bl_label}』没有指定『区域』节点 —— "
                        f"请在弹出的下拉里选一个 Area(例如金币、传送门)。"
                        f"这个触发器这次没有生成")
                    continue
                area_expr = self.ctx.target_expr(node, "gk_area", hint="area")

            func_name = self.ctx.declare_func_name(func_base, key=node_key(node))
            self.ctx._active.clear()
            self.ctx._current_event = node.name
            lines = self.ctx.new_section()
            self._emit_event_body(node, 1)

            if param_name:
                ptype = (param_types[0] if self.ctx.dim == 2
                         else param_types[1]) if param_types else "Node"
                filter_text = str(getattr(node, "gk_filter", "") or "").strip()
                # 形参没用上时加下划线前缀,Godot 就不会报 UNUSED_PARAMETER
                actual = param_name if filter_text else f"_{param_name}"
                body = list(lines.lines)
                if filter_text:
                    body = [f"    if String({actual}.name) != "
                            f"{gd_string(filter_text)}:",
                            "        return"] + body
                sections.append({
                    "signature": f"func {func_name}({actual}: {ptype}) -> void:",
                    "lines": body,
                    "empty_body": "    pass",
                    "separator": True,
                })
                trigger_ready.append(
                    f"    if {area_expr} != null "
                    f"and not {area_expr}.{signal_name}.is_connected({func_name}):")
                trigger_ready.append(
                    f"        {area_expr}.{signal_name}.connect({func_name})")
            else:
                # 定时触发:在 _ready 里现造一个 Timer 挂到自己身上。
                # 局部变量即可 —— 节点 add_child 之后由场景树持有引用。
                timer_var = self.ctx.new_temp("timer")
                seconds = gd_number(float(getattr(node, "gk_seconds", 1.0) or 1.0))
                one_shot = "false" if bool(getattr(node, "gk_repeat", False)) else "true"
                sections.append({
                    "signature": f"func {func_name}() -> void:",
                    "lines": list(lines.lines),
                    "empty_body": "    pass",
                    "separator": True,
                })
                trigger_ready += [
                    f"    var {timer_var} := Timer.new()",
                    f"    {timer_var}.wait_time = {seconds}",
                    f"    {timer_var}.one_shot = {one_shot}",
                    f"    {timer_var}.autostart = true",
                    f"    add_child({timer_var})",
                    f"    {timer_var}.timeout.connect({func_name})",
                ]

        if trigger_ready:
            # ⚠ 必须插到 `_ready` **最前面**:『每当_准备』链里可能有『等待_秒』
            #   (`await`),排在它后面的 connect 会晚好几秒才挂上 —— 那段时间
            #   触发区完全不响应(第七十轮踩到)。
            self._append_block(sections, "_ready", "func _ready() -> void:",
                               trigger_ready, at_front=True)

        # ---- 网络联机(第一百轮)──────────────────────────────────────
        # 和触发器同一套路:每个事件节点(有人进来 / 有人走了 / 收到消息)
        # 是一条独立执行流,信号 / RPC 连接补进 `_ready`;另外生成一组
        # ENet 辅助函数 + 一个 `@rpc` 收消息方法(见 codegen/network.py)。
        emit_network(self, sections)

        # ---- 自由飞行相机(兜底观察机)──────────────────────────────
        # **必须**放在 process_hooks / physics_hooks 落盘**之前**:
        # 它要往物理帧末尾挂一行"每帧飞一下"。踩过的坑(用户 2026-10-04:
        # "上帝视角没完善好"):原来这段挂在下面(相机解析那块之后),
        # 而 physics_hooks 早就已经拼进 `_physics_process` 了 —— 于是
        # 注册的钩子**永远没被写出去**,相机造出来却一动不动。
        #
        # ── 上帝摄像机为什么**不在这里**了(用户 2026-10-04 第二轮)──────
        # 它原来也走这段 → 变成角色脚本的一部分:场景里没有角色就没有
        # 这个脚本,上帝机跟着消失("在没有角色的测试里动不了、鼠标也转
        # 不了视角")。现在上帝机由**环境脚本**生成(每张 .tscn 都有),
        # 角色脚本这边只留一条守卫:上帝视角期间把移动 / 跳跃 / 鼠标视角
        # 让出去(见 _emit_god_guard_helper)。
        # 角色脚本要用到守卫时**必须**把定义写出去 —— 判断条件除了
        # "这一关导了上帝机"(god_camera_enabled),还有"图里真的调了它"
        # (need_god_guard,第 124 轮:`显示_鼠标模式` 的锁鼠标那两句会包一层
        # `if not _gk_god_camera_active():`)。只调用不定义 = Godot 报
        # "Function _gk_god_camera_active() not found in base self",整个
        # 脚本加载不了。
        # ── ★ 上帝机守卫只在 **3D** 里生成(第 131 轮,队友查边界时挖到)──
        # 上帝机是**3D 独有**的:它整段逻辑围着 `Camera3D` / 上帝摄像机转,
        # 而 2D 工程里根本没有"3D 相机"这个概念。
        # 可这道门原来**不看维度** —— 2D 工程只要在设置里勾了『上帝摄像机』,
        # 生成的脚本就会带上 `get_viewport().get_camera_3d()`(一个 3D-only API)
        # 和一个永远返回 false 的守卫。
        # 证据(队友给的,2D 图里**只放一个『打印』、没有任何 AI 节点**):
        #     `_gk_god_camera_active` 定义 : 出现
        #     用了 get_camera_3d()        : 出现
        # 也就是说这不是"有没有 AI"的问题,是**门本身漏了维度**。
        # ⚠ 只在 3D 里生成之后,2D 工程产物**逐字回到旧行为**(多一个字节都算回归)。
        #   3D 侧一个字不变 —— 队友的 AI 守卫挂在 `god_camera_enabled` 上,
        #   3D 里它仍然为真,不受影响(他还额外加了维度门做双保险)。
        if int(getattr(self.ctx, "dim", 3) or 3) == 3 and (
                getattr(self.ctx, "god_camera_enabled", False)
                or getattr(self.ctx, "need_god_guard", False)):
            self._emit_god_guard_helper(sections)
        # ── 第一人称枢轴夹取(第 129 轮:用户"第一人称还会触发穿模")────────
        # 只在**图里真有第一人称弹簧臂**时才生成 —— 没有第一人称的工程
        # 一个字节都不多(这条规矩这个仓库一直守着)。
        if getattr(self.ctx, "need_pivot_clamp", False):
            self.ctx.declare_var("_gk_half_up_cache", "float", initial="0.0")
            self.ctx.declare_var("_gk_half_up_ready", "bool", initial="false")
            self._emit_body_half_up_helper(sections)
        if getattr(self.ctx, "need_camera_query_helper", False):
            self._emit_fly_camera(sections)
        # ---- 2D 兜底相机(第四十六轮)──────────────────────────────────
        # 2D 场景里**没有相机**时,Godot 停在原点视图(视口左下角是 (0,0),
        # y 向下)—— 关卡摆在负坐标区就是"运行后一片空 / 视角错乱"
        # (用户实测:2D 寻路示例)。3D 那边早有兜底(自由飞行观察机),
        # 2D 这边一直缺 —— 这里补上。有相机的场景一根手指都不动。
        if getattr(self.ctx, "dim", 3) == 2:
            self._emit_camera2d_fallback(sections)
        # ---- 寻路第二版(第三十六轮):避障 / 重烤导航 ----------
        if (getattr(self.ctx, "need_nav_avoid", False)
                or getattr(self.ctx, "need_nav_avoid_2d", False)
                or getattr(self.ctx, "need_nav_rebake", False)):
            self._emit_nav_helpers(sections)

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

        # 对话/选择会把世界暂停,但它们仍需要在宿主脚本的 `_input` 里
        # 接收回车、数字键和鼠标点击。让宿主 ALWAYS 处理输入,再在游戏逻辑
        # 的 process/physics 回调入口主动停住世界行为;这样不会出现
        # "鼠标能看见、文字/选择永远等不到输入"的半暂停状态。
        # 对话 / 选择本身会在暂停树里等待输入。宿主必须保持 ALWAYS，
        # 否则鼠标事件和 process_frame await 都会在暂停后失去接收者。
        if (self.ctx.need_pause_helpers or self.ctx.need_dialog_helpers
                or self.ctx.need_choice_helpers):
            self._append_block(
                sections, "_ready", "func _ready() -> void:",
                ["    process_mode = Node.PROCESS_MODE_ALWAYS"],
                at_front=True)
            for func_name in ("_process", "_physics_process"):
                section = self._section_for(sections, func_name)
                if section is not None:
                    section["lines"][0:0] = [
                        "    if get_tree().paused:",
                        "        return",
                    ]

        # ---- 触屏拖动:需要往 _input(event) 里挂事件累积 ----
        #
        # ── 为什么 `_input` 里要按 `during_pause` 分两组(用户实测 bug,
        #    2026-10-13:"暂停游戏后鼠标还存在控制视角旋转")──────────────
        # 上面那段给 `_ready` 强行设了 `process_mode = PROCESS_MODE_ALWAYS`
        # —— 不这样的话"暂停之后按 Esc 解冻"和"暂停里弹的对白"都收不到
        # 输入。但 ALWAYS 是**整个节点**的属性:`_process` /
        # `_physics_process` 还能在函数入口用 `if get_tree().paused: return`
        # 挡住,`_input` 一直没挡 —— 于是暂停期间鼠标位移照样往
        # `_gk_mouse_delta` 里攒,视角跟着转。
        # 解法:处理器登记时各自声明 `during_pause`(见
        # context.register_input_handler),这里分两组发射:
        #   * `during_pause=True`  -> **无条件调用**(暂停界面自己的交互:
        #     点外面关面板 / 对话框 / 对话历史 / 选择框,以及上帝机);
        #   * `during_pause=False` -> 包进"当前没暂停"的守卫里(角色玩法
        #     输入:鼠标视角 / 滚轮 / 触屏拖动)。
        # ⚠ 两边都为空时**根本不生成 `_input`**(老行为:没有处理器就没有
        #   这个函数);`get_tree() == null` 要算"没暂停"—— 那种时刻节点
        #   还没进树,按老样子调用一次最安全。
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

            def _during_pause(item):
                # 老的三元组(理论上不存在了)按"暂停期间不跑"处理:
                # 宁可多挡一层,也不要漏掉玩法输入。
                return bool(item[3]) if len(item) > 3 else False

            always_calls = [item[0] for item in self.ctx._input_handlers
                            if _during_pause(item)]
            paused_calls = [item[0] for item in self.ctx._input_handlers
                            if not _during_pause(item)]
            if always_calls:
                target_section["lines"].append(
                    "    # 暂停期间照跑:暂停界面自己的交互 / 上帝机")
                for func_name in always_calls:
                    target_section["lines"].append(f"    {func_name}(event)")
            if paused_calls:
                target_section["lines"].append(
                    "    # 暂停期间不跑:角色玩法输入(视角 / 滚轮 / 触屏)")
                target_section["lines"].append(
                    "    if get_tree() == null or not get_tree().paused:")
                for func_name in paused_calls:
                    target_section["lines"].append(f"        {func_name}(event)")

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
        #
        # ⚠ 这里**只是尽量早**地补一次;真正保证不漏的是 `generate()` 末尾
        #   那一次(同一个方法,里面带"只补一次"的守卫):AI / 动画 / 界面
        #   设计这几张图是"被引用时**现场展开**"的,它们里的引用要到很后面
        #   才登记进 ctx.onready —— 只在前面补一次的话,那种脚本就会
        #   "变量声明了、helper 没生成"。详见 _emit_reference_helpers。
        self._emit_reference_helpers(sections)

        # ---- 销毁物体时把"同名网格"一起清掉 ----
        # 见 statements._stmt_queue_free。导出后一个物体常常是两个节点:
        # 网格在 glb 实例里(根下面)、碰撞代理在 Colliders 下(同名)。
        # 只 free 代理的话用户在画面上还看得见它(实测:金币吃掉后还杵在原地)。
        # is_ancestor_of 那一条是防"自毁":目标本身可能就是网格,或者网格
        # 就在它里面(角色的 Model 子节点)—— 那种情况 free 自己就够了。
        if getattr(self.ctx, "need_free_visual_helper", False):
            sections.append({
                "signature": "func _gk_free_with_visual(node: Node) -> void:",
                "lines": [
                    "    if node == null:",
                    "        return",
                    "    var tree := node.get_tree()",
                    "    if tree != null:",
                    "        for mesh in tree.root.find_children("
                    'str(node.name), "MeshInstance3D", true, false):',
                    "            if mesh != node and not node.is_ancestor_of(mesh):",
                    "                mesh.queue_free()",
                    "    node.queue_free()",
                ],
                "empty_body": "    pass",
                "separator": True,
            })

        # ---- 运行时创建资产实例的小工具(用户 2026-10-05)----
        # 见 statements._stmt_asset_spawn。造一份实例有 3 步:建根节点 /
        # 按形状建碰撞 / 挂 glb 并抵消它内部那层世界变换 ——
        # 每个『资产_创建实例』节点都写一遍会越写越乱,而且漏掉第 3 步的
        # 抵消会让新实例**偏移到本体原来在 Blender 里的位置**。
        if getattr(self.ctx, "need_asset_spawn_helper", False):
            sections.append({
                "signature": "func _gk_spawn_asset(path: String, what: String, "
                             "where: %s, shapes: Array, layer: int, "
                             "mask: int) -> %s:" % (
                                 "Vector2" if self.ctx.dim == 2 else "Vector3",
                                 "Node2D" if self.ctx.dim == 2 else "Node3D"),
                "lines": self._asset_spawn_lines(),
                "empty_body": "    return null",
                "separator": True,
            })

        # ---- 开关碰撞的小工具(用户 2026-10-05)----
        # 见 statements._stmt_set_collision:Godot 4 的 CollisionObject3D
        # **没有**布尔 disabled(实测:StaticBody3D 只有 disable_mode),
        # 真正管用的是 CollisionShape 自己的 disabled。所以要把目标 + 它
        # 下面所有碰撞形状一起切,而且一律 set_deferred ——
        # 逻辑常常挂在 _physics_process 里,直接改会踩
        #     Can't change this state while flushing queries
        if getattr(self.ctx, "need_collision_helper", False):
            sections.append({
                "signature": "func _gk_set_collision(node: Node, on: bool) -> void:",
                "lines": [
                    "    if node == null:",
                    "        return",
                    # 导出后一个物体常常是"网格 + 碰撞代理"两个**同名**节点
                    # (见 _gk_ref 的说明)。按名字找到的往往是网格 ——
                    # 那就得把 Colliders 下那个同名节点也一起算上,
                    # 否则"关掉门的碰撞"会静默失效(实测过这一族坑)。
                    "    var holders: Array = [node]",
                    "    if node.get_tree() != null:",
                    "        for found in node.get_tree().root.find_children("
                    'str(node.name), "CollisionObject3D", true, false):',
                    "            if not holders.has(found):",
                    "                holders.append(found)",
                    "    var shapes: Array = []",
                    "    for holder in holders:",
                    "        shapes.append_array(holder.find_children("
                    '"*", "CollisionShape3D", true, false))',
                    "        shapes.append_array(holder.find_children("
                    '"*", "CollisionShape2D", true, false))',
                    "        if holder is CollisionShape3D or holder is CollisionShape2D:",
                    "            shapes.append(holder)",
                    "    if shapes.is_empty():",
                    '        push_warning("[GK] 『%s』身上没有碰撞形状,'
                    '关不掉/开不了" % node.name)',
                    "        return",
                    "    for shape in shapes:",
                    '        shape.set_deferred("disabled", not on)',
                ],
                "empty_body": "    pass",
                "separator": True,
            })

        # ---- 调试面板的日志缓冲(用户 2026-10-05 第二十二轮)----------
        # 『打印_输出』在开着调试面板时会多调一句 `_gk_debug_log_append` ——
        # 面板(环境脚本)再从这里读最近 20 条显示出来。
        #
        # ── 为什么缓冲放在"打印的人"身上,而不是直接推给面板 ──────────
        # 推给面板要在两个脚本之间找节点、缓存引用、还得处理"面板不存在"
        # (面板没开时那句代码根本不该生成)。缓冲留在自己脚本里,面板只读
        # 不写 —— 一个场景有多个脚本时各存一份,互不打架。
        if getattr(self.ctx, "need_debug_helper", False):
            sections.append({
                "signature": "func _gk_debug_log_append(text: String) -> void:",
                "lines": [
                    "    _gk_debug_logs.append(text)",
                    "    while _gk_debug_logs.size() > 20:",
                    "        _gk_debug_logs.pop_front()",
                ],
                "empty_body": "    pass",
                "separator": True,
            })

        # ---- 灯光设置的小工具(第四十八轮:阶段①灯光扩展)------------
        # 见 statements._stmt_set_light 的说明:运行时按名字找灯,
        # 从 current_scene 往下找(换过关卡也对得上),找不到静默跳过。
        if getattr(self.ctx, "need_light_helper", False):
            sections.append({
                "signature": "func _gk_set_light(host: Node, light_name: String, "
                             "on: bool, energy: float, color) -> void:",
                "lines": [
                    "    if light_name.strip_edges().is_empty():",
                    "        return",
                    "    var root: Node = host",
                    "    if host != null and host.is_inside_tree():",
                    "        var scene := host.get_tree().root",
                    "        if scene != null:",
                    "            root = scene",
                    "    if root == null:",
                    "        return",
                    "    var found := root.find_child("
                    'light_name.strip_edges(), true, false)',
                    "    var light := found as Light3D",
                    "    if light == null:",
                    "        return",
                    "    light.visible = on",
                    "    if energy > 0.0:",
                    "        light.light_energy = energy",
                    "    if color != null:",
                    "        light.light_color = color",
                ],
                "empty_body": "    pass",
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
            self._append_block(sections, "_gk_current_camera",
                               "func _gk_current_camera() -> Node:",
                               list(lines.lines))

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
            # ⚠ 形参不能叫 `base` —— Godot 报
            #   "The function parameter "base" has the same name as a
            #    built-in function"(第七十轮用户截图里的告警之一)。
            sections.append({
                "signature": "func _gk_layer_player(host: Node, layer: int) -> Node:",
                "lines": [
                    "    if host == null or host.get_parent() == null:",
                    "        return host",
                    # 缓存:同一个层只复制一次,反复播放不会一直堆节点
                    '    var cache_name := "_gk_layer_%d" % layer',
                    "    var existing: Node = host.get_parent().get_node_or_null(cache_name)",
                    "    if existing is AnimationPlayer:",
                    "        return existing",
                    "    var copy: Node = host.duplicate()",
                    "    copy.name = cache_name",
                    "    host.get_parent().add_child(copy)",
                    "    return copy",
                ],
                "empty_body": "    return host",
                "separator": True,
            })

        # ---- 动画状态机(『每帧检查』模式)的 _physics_process ----
        # 状态切换要跟移动同步判定 —— 放在渲染帧里会出现
        # "人已经走出去了状态还没切"的一帧延迟(和相机跟随同一个道理)。
        if (self.ctx.need_anim_state_process or self.ctx.need_anim_tree_process
                or self.ctx.need_anim_start_process or self.ctx.need_ai_process):
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
                                  machine.get("gate"),
                                  machine.get("blend", False))
            # ---- 动画混合树(第六十一轮)----
            # 每物理帧把『参数』推给 AnimationTree 的 blend_position。
            for blend in self.ctx.anim_blend_trees:
                self.ctx._active.clear()
                self.ctx._current_event = blend["node"].name
                self.ctx._expr_indent = 1
                self.ctx._current_indent = 1
                _emit_blend_update(self.ctx, 1, blend)
            # ---- AI 状态机(『游戏AI』,第四十一轮)----
            # 和动画树同一套:每物理帧先执行当前状态的行为,再判转场。
            # 缩进同样先摆正 —— 转场条件里的表达式节点(『AI_看得见』)
            # 会就地生成几行代码,落到错误的缩进上就"变量没声明"。
            for machine in self.ctx.ai_machines:
                self.ctx._active.clear()
                self.ctx._current_event = machine["node"].name
                self.ctx._expr_indent = 1
                self.ctx._current_indent = 1
                _emit_ai_graph(self.ctx, 1, machine)
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
                # ── 播放器引用在**本函数里**重新取一份(第七十六轮的编译错)──
                # `_stmt_play_anim` 里那个 `_ap2` 是 _ready 的**局部变量**,
                # 拿到 _process 里用就是 "Identifier not declared"。
                # 表达式本身在哪儿都算得出(而且自带缓存,不重复查找)。
                holder = self.ctx.new_temp("ap")
                lines.add(1, f"var {holder} := {event['player_expr']}")
                lines.add(1, f"if {holder} != null "
                             f"and not {event['name_expr']}.is_empty():")
                lines.add(2, f"var {var}: bool = "
                             f"{holder}.current_animation == {event['name_expr']} "
                             f"and {holder}.is_playing()")
                lines.add(2, f"if {var}:")
                self.ctx._active.clear()
                self.ctx._current_event = event["func"]
                _chain(self.ctx, event["node"], 3, event["output"])
            # 同 _physics_process:并进已有的 _process,别另起一个
            self._append_block(sections, "_process",
                               "func _process(_delta: float) -> void:",
                               list(lines.lines))

        # ---- 『动画_事件』:每帧比一下播放进度,跨过那一刻就触发一次 ----
        # 和上面那段同源(都放 _process):AnimationPlayer 没有"播到第 N 秒"
        # 的信号,只能每帧读 current_animation_position 跟目标时刻比。
        #
        # 只响一次靠 `_gk_ev_armed`;进度**回退**说明这段动画从头又播了
        # (重播 / 循环了一圈)-> 重新武装,于是"循环每圈响一次"。
        if self.ctx.need_anim_time:
            lines = self.ctx.new_section()
            for event in self.ctx.anim_events:
                if event.get("kind") != "time":
                    continue
                holder = self.ctx.new_temp("ap")
                armed = event["armed"]
                last = event["last"]
                at = event["time"]
                name = event["name_expr"]
                lines.add(1, f"var {holder} := {event['player_expr']}")
                lines.add(1, f"if {holder} != null "
                             f"and {holder}.current_animation == {name}:")
                lines.add(2, f"if {armed} and "
                             f"{holder}.current_animation_position >= {at} "
                             f"and {last} < {at}:")
                lines.add(3, f"{armed} = false")
                self.ctx._active.clear()
                self.ctx._current_event = event["func"]
                _chain(self.ctx, event["node"], 3, event["output"])
                lines.add(2, f"if {holder}.current_animation_position < {last}:")
                lines.add(3, f"{armed} = true")
                lines.add(2, f"{last} = {holder}.current_animation_position")
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
                # ⚠ 这里同样是**表达式**不是 _ready 的局部变量(第七十六轮)。
                lines.add(1, f"if {event['name_expr']} != _name:")
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
            if self.ctx.need_save_verify:
                # ── 防改档(第九十三轮)────────────────────────────────
                # 勾了『防改档』的存档落盘时多一层"信封":
                #     {"gk_check": "<sha256>", "gk_data": "<原来的 JSON 文本>"}
                # ⚠ 哈希算在 **gk_data 的原文本**上(不是把它当嵌套对象再
                # stringify 一遍):JSON 往返会把 int 变成 float、还可能动
                # 键序,重新序列化后哈希对不上,好档会被误判成"被改过"。
                # 读侧**自描述**:认得信封就校验,没勾的裸 JSON(老存档)
                # 原样读 —— 这行"老档继续能读"就是靠它。
                check_expr = (f'(payload + "|" + {_SAVE_SALT_CONST})'
                              f'.sha256_text()')
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
                        "        var box: Dictionary = parsed",
                        '        if box.has("gk_check"):',
                        '            var payload := str(box.get("gk_data"))',
                        f'            if {check_expr} != str(box.get("gk_check")):',
                        '                push_warning("存档校验不过(文件被改过?): " + path)',
                        "                return {}",
                        "            var inner: Variant = JSON.parse_string(payload)",
                        "            if inner is Dictionary:",
                        "                return inner",
                        '            push_warning("存档内容不是合法 JSON: " + path)',
                        "            return {}",
                        "        return box",
                        '    push_warning("存档内容不是合法 JSON: " + path)',
                        "    return {}",
                    ],
                    "empty_body": "    pass",
                })
                sections.append({
                    "signature": "func _gk_write_save(path: String, data: Dictionary, verify: bool = false) -> void:",
                    "lines": [
                        "    var file := FileAccess.open(path, FileAccess.WRITE)",
                        "    if file == null:",
                        '        push_warning("存档写不进去: " + path)',
                        "        return",
                        "    if verify:",
                        "        var payload := JSON.stringify(data)",
                        f'        var box := {{"gk_check": {check_expr}, "gk_data": payload}}',
                        "        file.store_string(JSON.stringify(box))",
                        "    else:",
                        "        file.store_string(JSON.stringify(data))",
                        "    file.close()",
                    ],
                    "empty_body": "    pass",
                })
            else:
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
            # A role can update a permanent HUD without creating any local widgets.
            self.ctx.declare_var("_gk_ui_layer", "CanvasLayer")
            self.ctx.declare_var("_gk_ui_root", "Control")
            # ── 层号:主界面 20 / 普通界面 10(第一百二十六轮)──────────────
            # 真机量过(`.audit/ui_overlay_probe`,Godot 4.7.2):关卡的 GKUI
            # 和主界面的 GKUI **都是 layer 10**,而 layer 相同时由 /root 里的
            # 树序决定先后 —— 主界面那份排在前面,于是它整个被关卡的界面
            # **画在下面**。这就是"按 Esc 弹出的暂停面板被章节卡压住"的根:
            # `_gk_ui_raise()` 的 `move_to_front()` 只在**同一个父节点**的
            # 兄弟之间挪,跨 CanvasLayer 一步都迈不出去。
            # 主界面是"跨场景常驻的那一层",本来就该盖在关卡界面之上,所以
            # 它单独抬到 20;仍低于屏幕滤镜(90)/ 暗角(92)/ 闪白转场(100)。
            if getattr(self.ctx, "is_main_ui", False):
                ui_layer_expr = "20"
            else:
                ui_layer_expr = "10"
            sections.append({
                "signature": "func _gk_ensure_ui(host: Node) -> void:",
                "lines": [
                    "    if _gk_ui_layer == null:",
                    "        _gk_ui_layer = CanvasLayer.new()",
                    "        _gk_ui_layer.name = \"GKUI\"",
                    # layer 10 = 盖在游戏画面上，但低于屏幕闪白(layer 100);
                    # 主界面那份 20 = 还要盖住关卡自己的界面(见 above)
                    f"        _gk_ui_layer.layer = {ui_layer_expr}",
                    # 对话/选择会在暂停树里显示,而按钮还必须继续收 GUI 输入。
                    # CanvasLayer 本身不参与暂停,但它的 Control 子树默认会
                    # 继承宿主的 PAUSABLE 模式;暂停后文字 tween 和焦点更新就
                    # 会一起停住,出现"鼠标可见、面板空白"。UI 是模态界面,
                    # 明确设为 ALWAYS,不依赖场景树的父节点模式。
                    "        _gk_ui_layer.process_mode = Node.PROCESS_MODE_ALWAYS",
                    "        host.add_child(_gk_ui_layer)",
                    "        _gk_ui_root = Control.new()",
                    "        _gk_ui_root.name = \"Root\"",
                    "        _gk_ui_root.process_mode = Node.PROCESS_MODE_ALWAYS",
                    "        _gk_ui_root.set_anchors_preset(Control.PRESET_FULL_RECT)",
                    "        _gk_ui_root.mouse_filter = Control.MOUSE_FILTER_IGNORE",
                    "        _gk_ui_layer.add_child(_gk_ui_root)",
                ],
                "empty_body": "    pass",
                "separator": True,
            })
            if self.ctx.need_ime_helper:
                sections.append({
                    # 输入法候选框跟着光标走(第八十一轮):只有『输入法 /
                    # 软键盘』勾了的输入框才生成 —— 没勾的图一个字都不多
                    "signature": "func _gk_ime_update(edit: Control) -> void:",
                    "lines": [
                        "    if edit == null or not (edit is LineEdit):",
                        "        return",
                        "    var field := edit as LineEdit",
                        "    var font := field.get_theme_font(\"font\")",
                        "    var size := field.get_theme_font_size(\"font_size\")",
                        "    var caret := 0",
                        "    if font != null:",
                        "        caret = int(font.get_string_size(",
                        "            field.text.substr(0, field.caret_column),",
                        "            HORIZONTAL_ALIGNMENT_LEFT, -1, size).x)",
                        "    var spot := field.global_position + Vector2(float(caret), field.size.y)",
                        "    DisplayServer.window_set_ime_position(Vector2i(int(spot.x), int(spot.y)))",
                    ],
                    "empty_body": "    pass",
                    "separator": True,
                })
            if self.ctx.need_list_helper:
                sections.append({
                    # 列表是两层(滚动容器 + 里面排版的 VBox/HBox);
                    # 子控件得挂**里层**才排队 —— 挂外层会浮着不动
                    "signature": "func _gk_list_body(list_name: String) -> Control:",
                    "lines": [
                        "    var found := _gk_find_widget(list_name)",
                        "    if found == null:",
                        "        return null",
                        "    for child in found.get_children():",
                        "        if child is VBoxContainer or child is HBoxContainer:",
                        "            return child as Control",
                        "    return found",
                    ],
                    "empty_body": "    pass",
                    "separator": True,
                })
            if self.ctx.need_list_fill_helper:
                sections.append({
                    # 先清空旧行再建:每帧刷也不会越堆越多。
                    # remove_child 是**立刻**摘掉(queue_free 要等帧末,
                    # 那一帧里新旧行会同时占位置,列表看起来在抖)。
                    "signature": ("func _gk_list_fill(list_name: String, items: Array, "
                                  "line_height: float, font_size: int, color: Color) -> void:"),
                    "lines": [
                        "    var body := _gk_list_body(list_name)",
                        "    if body == null:",
                        '        push_warning("找不到列表控件: " + list_name)',
                        "        return",
                        "    for child in body.get_children():",
                        "        body.remove_child(child)",
                        "        child.queue_free()",
                        "    for item in items:",
                        "        var label := Label.new()",
                        "        label.text = str(item)",
                        '        label.add_theme_font_size_override("font_size", font_size)',
                        '        label.add_theme_color_override("font_color", color)',
                        "        label.mouse_filter = Control.MOUSE_FILTER_IGNORE",
                        "        if line_height > 0.0:",
                        "            label.custom_minimum_size = Vector2(0.0, line_height)",
                        "        body.add_child(label)",
                    ],
                    "empty_body": "    pass",
                    "separator": True,
                })
            if self.ctx.need_list_fill_click_helper:
                sections.append({
                    # 『每行可点』(第一百零六轮):行建成**按钮**,而且一组
                    # 里只允许一个"按下"—— 那就是"选中的那一行"(pressed 态
                    # 由皮肤给的 `GKListItem` 变体上色,不用自己算高亮)。
                    # 点哪行就把**行号(0 起)**写进**列表控件自己的 meta**,
                    # 再走用户接的『点行』回调;『第几行』输出按名字现读它。
                    "signature": ("func _gk_list_fill_click(list_name: String, items: Array, "
                                  "line_height: float, font_size: int, color: Color, "
                                  "handler: Callable) -> void:"),
                    "lines": [
                        "    var body := _gk_list_body(list_name)",
                        "    if body == null:",
                        '        push_warning("找不到列表控件: " + list_name)',
                        "        return",
                        "    for child in body.get_children():",
                        "        body.remove_child(child)",
                        "        child.queue_free()",
                        "    var group := ButtonGroup.new()",
                        "    for index in items.size():",
                        "        var row := Button.new()",
                        "        row.text = str(items[index])",
                        "        row.toggle_mode = true",
                        "        row.button_group = group",
                        '        row.theme_type_variation = "GKListItem"',
                        '        row.add_theme_font_size_override("font_size", font_size)',
                        '        row.add_theme_color_override("font_color", color)',
                        "        row.mouse_filter = Control.MOUSE_FILTER_STOP",
                        "        if line_height > 0.0:",
                        "            row.custom_minimum_size = Vector2(0.0, line_height)",
                        "        row.pressed.connect(_gk_list_row_pressed.bind(list_name, index, handler))",
                        "        body.add_child(row)",
                    ],
                    "empty_body": "    pass",
                    "separator": True,
                })
                sections.append({
                    "signature": ("func _gk_list_row_pressed(list_name: String, index: int, "
                                  "handler: Callable) -> void:"),
                    "lines": [
                        "    var found := _gk_find_widget(list_name)",
                        "    if found != null:",
                        '        found.set_meta("gk_index", index)',
                        "    handler.call(index)",
                    ],
                    "empty_body": "    pass",
                    "separator": True,
                })
            if self.ctx.need_widget_text_helper:
                sections.append({
                    "signature": "func _gk_widget_text(widget_name: String) -> String:",
                    "lines": [
                        "    var found := _gk_find_widget(widget_name)",
                        "    if found == null:",
                        "        return \"\"",
                        "    var value: Variant = found.get(\"text\")",
                        "    return str(value) if value != null else \"\"",
                    ],
                    "empty_body": "    pass",
                    "separator": True,
                })
            # ── 设置面板三件套的"读值"三个小函数(第一百零四轮)──────────
            # 都是"按名字找控件 -> 取状态":滑块读 value / 下拉读 gk_index
            # (0 起存的 meta,给用户时 +1)/ 复选框读 button_pressed。
            # 控件还没建出来 / 名字填错时给中性值(0 / 0 / false),不炸。
            if self.ctx.need_widget_value_helper:
                sections.append({
                    "signature": ("func _gk_widget_value(widget_name: String) "
                                  "-> float:"),
                    "lines": [
                        "    var found := _gk_find_widget(widget_name)",
                        "    if found == null:",
                        "        return 0.0",
                        "    if found is Range:",
                        "        return (found as Range).value",
                        "    for child in found.get_children():",
                        "        if child is Range:",
                        "            return (child as Range).value",
                        "    return 0.0",
                    ],
                    "empty_body": "    pass",
                    "separator": True,
                })
            if self.ctx.need_widget_index_helper:
                sections.append({
                    "signature": ("func _gk_widget_index(widget_name: String) "
                                  "-> int:"),
                    "lines": [
                        "    # 下拉框现在选的是第几个(**1 起数**):meta 里存的是 0 起",
                        "    var found := _gk_find_widget(widget_name)",
                        "    if found == null:",
                        "        return 0",
                        '    if found.has_meta("gk_index"):',
                        '        return int(found.get_meta("gk_index")) + 1',
                        "    return 0",
                    ],
                    "empty_body": "    pass",
                    "separator": True,
                })
            if self.ctx.need_widget_checked_helper:
                sections.append({
                    "signature": ("func _gk_widget_checked(widget_name: String) "
                                  "-> bool:"),
                    "lines": [
                        "    var found := _gk_find_widget(widget_name)",
                        "    if found == null:",
                        "        return false",
                        "    if found is BaseButton:",
                        "        return (found as BaseButton).button_pressed",
                        "    return false",
                    ],
                    "empty_body": "    pass",
                    "separator": True,
                })
            if self.ctx.need_raise_helper:
                sections.append({
                    # 『界面_隐藏控件』勾了『置顶』才生成:打开一块面板时
                    # 挪到最上层,并把它所在互斥组的其他成员收起来。
                    # 没进组的控件 get_groups() 里没有那个前缀 —— 空转,安全。
                    "signature": "func _gk_ui_raise(widget: Control) -> void:",
                    "lines": [
                        "    # 谁后开谁在上:挪到同层最后(画在最上面)",
                        "    widget.move_to_front()",
                        "    # 互斥组:同组的其他面板自动收起来(填了组名才进组)",
                        "    for key in widget.get_groups():",
                        '        if not str(key).begins_with("gk_ui_mutex_"):',
                        "            continue",
                        "        for other in get_tree().get_nodes_in_group(key):",
                        "            if other != widget and other is CanvasItem:",
                        "                (other as CanvasItem).visible = false",
                    ],
                    "empty_body": "    pass",
                    "separator": True,
                })
            if self.ctx.need_focus_first_helper:
                sections.append({
                    # 面板勾了『打开时首焦点』(第一百零五轮)才生成:
                    # 每次面板显示时,把里面**能聚焦**的控件按"上->下、
                    # 左->右"串成一条链(方向键 / Tab 都走得通),再把焦点
                    # 交给第一个 —— 键盘 / 手柄玩家不用先按 Tab 摸进去。
                    # 位置排序用 global_position:面板里的控件是自由摆放的
                    # (不在容器里),没有容器帮我们定顺序。
                    "signature": "func _gk_ui_focus_first(panel: Control) -> void:",
                    "lines": [
                        "    var items: Array = []",
                        "    var queue: Array = [panel]",
                        "    while not queue.is_empty():",
                        "        var node: Node = queue.pop_front()",
                        "        for child in node.get_children():",
                        "            if child is Control and (child as Control).visible "
                        "and (child as Control).focus_mode != Control.FOCUS_NONE:",
                        "                items.append(child)",
                        "            queue.append(child)",
                        "    items.sort_custom(func(a: Control, b: Control) -> bool:",
                        "        if absf(a.global_position.y - b.global_position.y) > 2.0:",
                        "            return a.global_position.y < b.global_position.y",
                        "        return a.global_position.x < b.global_position.x)",
                        "    for index in items.size():",
                        "        var item: Control = items[index]",
                        "        var up: NodePath = items[index - 1].get_path() "
                        "if index > 0 else NodePath()",
                        "        var down: NodePath = items[index + 1].get_path() "
                        "if index + 1 < items.size() else NodePath()",
                        "        item.focus_neighbor_top = up",
                        "        item.focus_neighbor_bottom = down",
                        "        item.focus_neighbor_left = up",
                        "        item.focus_neighbor_right = down",
                        "        item.focus_previous = up",
                        "        item.focus_next = down",
                        "    if not items.is_empty():",
                        "        (items[0] as Control).grab_focus()",
                    ],
                    "empty_body": "    pass",
                    "separator": True,
                })
            # ── 有『主界面』时,查找多一条兜底(第一百一十二轮)────────
            # 主界面(跨场景常驻的 autoload)里的控件**按名字也能找到** ——
            # 所以『界面_设置』『界面_隐藏控件』这些无论写在哪个场景里,
            # 都能指到主界面里的一块面板(`暂停菜单` 就只有一个了)。
            # 本场景的界面优先(先查自己的 `_gk_ui_layer`)。
            #
            # ⚠ 没有主界面的工程**保持原样逐字不变**(老图生成结果钉着)——
            #   所以这里按"工程里有没有勾主界面"分两条文本。
            if main_ui_design_tree() is not None:
                find_lines = [
                    "    if _gk_ui_layer != null:",
                    "        var found := _gk_ui_layer.find_child(widget_name, true, false)",
                    "        if found is Control:",
                    "            return found",
                    "    # 主界面(跨场景常驻)里的控件也按名字能找",
                    "    var main_ui: Node = get_node_or_null("
                    f'"/root/{MAIN_UI_AUTOLOAD}")',
                    "    if main_ui != null and main_ui != self:",
                    "        var hit := main_ui.find_child(widget_name, true, false)",
                    "        if hit is Control:",
                    "            return hit",
                    "    return null",
                ]
            else:
                find_lines = [
                    "    if _gk_ui_layer == null:",
                    "        return null",
                    "    var found := _gk_ui_layer.find_child(widget_name, true, false)",
                    "    if found is Control:",
                    "        return found",
                    "    return null",
                ]
            sections.append({
                # 不带 host 参数:它从来没被用过,而 Godot 会把
                # "参数没用上" 报成 UNUSED_PARAMETER(调试器里一条错误)。
                "signature": "func _gk_find_widget(widget_name: String) -> Control:",
                "lines": find_lines,
                "empty_body": "    pass",
                "separator": True,
            })

        # ---- 暂停:Esc 监听(第三十七轮)------------------------------
        # 世界暂停之后本脚本的 _input / _physics_process 全都停了 ——
        # 挂在 `get_tree().process_frame` 信号上的回调**不受暂停影响**
        # (信号是直接的方法调用,不走"节点的每帧处理"),这是唯一能在
        # 暂停期间读到 Esc 的活口。真 Godot 探针验过:暂停 -> Esc -> 解冻。
        #
        # ── 第一百二十六轮(P1):暂停时"旧界面让位"(用户原话)──────────
        # 用户:"有时点快了,就会有这样的情况,那么在如ESC菜单出现时就立刻
        # 隐藏旧的界面,或者添加模糊背景" —— 配图里章节卡「第一章:门厅」
        # 糊在暂停面板的"继续游戏"上。两件事一起做:
        #   ① 主界面那一层抬到 layer 20(见 _gk_ensure_ui 里的说明)+
        #      菜单 `move_to_front()`:菜单**永远在最上层**(跨 CanvasLayer
        #      光靠 move_to_front 跨不过去,所以两层都得动);
        #   ② 过场控件按 `gk_ui_transient` 组整批收起、继续游戏时**原样
        #      还原**(`_gk_hide_transient`),再配一块半透明压暗底。
        if self.ctx.need_pause_helpers:
            # Esc 监听只在真的勾了『Esc 也能开关』时才生成 —— 没勾的图里
            # 那两个函数是死代码(『界面_暂停游戏』本体只需要 _gk_apply_pause)
            if getattr(self.ctx, "_pause_hotkey_taken", False):
                sections.append({
                    "signature": "func _gk_arm_pause_watch(host: Node) -> void:",
                    "lines": [
                        "    if _gk_pause_armed:",
                        "        return",
                        "    _gk_pause_armed = true",
                        "    if host.get_tree() != null:",
                        "        host.get_tree().process_frame.connect(_gk_pause_watch)",
                    ],
                    "empty_body": "    pass",
                    "separator": True,
                })
                sections.append({
                    "signature": "func _gk_pause_watch() -> void:",
                    "lines": [
                        "    var down := Input.is_key_pressed(KEY_ESCAPE)",
                        "    if down and not _gk_pause_esc_held:",
                        "        _gk_pause_esc_held = true",
                        "        var tree := get_tree()",
                        "        if tree != null:",
                        "            _gk_apply_pause(not tree.paused, _gk_pause_menu, _gk_pause_show_mouse)",
                        "    elif not down:",
                        "        _gk_pause_esc_held = false",
                    ],
                    "empty_body": "    pass",
                    "separator": True,
                })
            pause_lines = [
                "    var tree := get_tree()",
                "    if tree == null:",
                "        return",
                "    tree.paused = paused",
                "    if show_mouse:",
                "        if paused:",
                "            if _gk_pause_mouse_was < 0:",
                "                _gk_pause_mouse_was = Input.mouse_mode",
                "            Input.mouse_mode = Input.MOUSE_MODE_VISIBLE",
                "        elif _gk_pause_mouse_was >= 0:",
                "            Input.mouse_mode = _gk_pause_mouse_was as Input.MouseMode",
                "            _gk_pause_mouse_was = -1",
            ]
            # ── 过场控件让位 + 压暗底(第一百二十六轮)────────────────────
            # 让位那一段**按"菜单在不在"分两半**,别写成一个 `paused` 三元:
            #   * 暂停菜单是可空的(`_gk_find_widget` 查不到时只 push_warning),
            #     那种情况下压根没弹出过菜单,也就没有"过场控件被收起来"这回事;
            #   * 所以"恢复"要**先确认菜单真的存在**再还 —— 无条件还原会在
            #     "菜单控件名字填错"的工程里把本该藏着的过场控件亮出来
            #     (章节卡淡出之后又冒出来)。收起/还原两边都靠 `paused` 分流,
            #     同一个函数里成对出现,不会有"收了不还"的中间态。
            if getattr(self.ctx, "_pause_transient", False):
                if self.ctx.need_transient_helper:
                    pause_lines += [
                        "    if paused:",
                        "        _gk_hide_transient(true)",
                        "    else:",
                        "        var has_menu := false",
                        "        if menu != \"\":",
                        "            has_menu = _gk_find_widget(menu) != null",
                        "        if has_menu:",
                        "            _gk_hide_transient(false)",
                    ]
                if self.ctx.need_pause_dim:
                    pause_lines += [
                        "    if _gk_pause_dim != null:",
                        "        _gk_pause_dim.visible = paused",
                    ]
            pause_lines += [
                "    var widget: Control = null",
                "    if menu != \"\":",
                "        widget = _gk_find_widget(menu)",
                "        if widget != null:",
                "            widget.visible = paused",
            ]
            if self.ctx.need_raise_helper and getattr(
                    self.ctx, "_pause_menu_raise", False):
                # 第一百零五轮:弹菜单时也走一遍『置顶 + 互斥组』
                # (Esc 之前可能已经开着别的面板,不置顶会被压住)
                pause_lines += [
                    "            if paused:",
                    "                _gk_ui_raise(widget)",
                ]
            pause_lines += [
                "        else:",
                "            push_warning(\"找不到暂停菜单控件: \" + menu)",
                "    print(\"[GK] 暂停: \", \"开\" if paused else \"关\")",
            ]
            sections.append({
                "signature": "func _gk_apply_pause(paused: bool, menu: String, show_mouse: bool) -> void:",
                "lines": pause_lines,
                "empty_body": "    pass",
                "separator": True,
            })
            # ── 过场控件让位(第一百二十六轮,P1)────────────────────────
            # 用户原话:"有时点快了……那么在如ESC菜单出现时就立刻隐藏旧的
            # 界面" —— 配图是章节卡压在暂停面板上。
            # 收起一批、继续时**原样还原**:用 stash 记住"收起之前各自可不
            # 可见" —— 直接 `visible = true` 会把本来该藏着的控件亮出来
            # (比如已经淡出过的章节卡,继续游戏后反而冒出来)。
            if self.ctx.need_transient_helper:
                # 还原账本:[暂停前各自可不可见],按组里同一个遍历顺序存 /
                # 读回去。不存就只能一律 `visible = true`,把"本来就该藏着的
                # 过场控件"亮出来(章节卡已经淡出过,继续游戏后又冒出来)。
                self.ctx.declare_var("_gk_transient_hidden", "Array",
                                     initial="[]")
                sections.append({
                    "signature": "func _gk_hide_transient(hide: bool) -> void:",
                    "lines": [
                        "    # 按 Godot 组整批收 / 放 —— 组名由生成器写进控件创建处",
                        "    # (见 services.UI_TRANSIENT_GROUP),不靠控件的名字猜。",
                        "    var found := get_tree().get_nodes_in_group(\"gk_ui_transient\")",
                        "    if hide:",
                        "        _gk_transient_hidden.clear()",
                        "        for item in found:",
                        "            if not (item is CanvasItem):",
                        "                continue",
                        "            var canvas := item as CanvasItem",
                        "            _gk_transient_hidden.append(canvas.visible)",
                        "            canvas.visible = false",
                        "    else:",
                        "        var index := 0",
                        "        for item in found:",
                        "            if not (item is CanvasItem):",
                        "                continue",
                        "            if index < _gk_transient_hidden.size():",
                        "                (item as CanvasItem).visible = _gk_transient_hidden[index]",
                        "            index += 1",
                        "        _gk_transient_hidden.clear()",
                    ],
                    "empty_body": "    pass",
                    "separator": True,
                })

        # ---- 对话框(第三十七轮)----------------------------------------
        if self.ctx.need_dialog_helpers:
            sections.append({
                "signature": ("func _gk_dialog_show(host: Node, speaker: String, body: String, "
                              "speed: float, portrait: String, width: float, height: float, "
                              "margin: float) -> void:"),
                "lines": [
                    "    _gk_ensure_ui(host)",
                    "    if _gk_dialog_panel == null:",
                    "        _gk_dialog_panel = Panel.new()",
                    "        _gk_dialog_panel.name = \"对话框\"",
                    "        var box := StyleBoxFlat.new()",
                    "        box.bg_color = Color(0.05, 0.06, 0.09, 0.85)",
                    "        box.corner_radius_top_left = 14",
                    "        box.corner_radius_top_right = 14",
                    "        box.corner_radius_bottom_left = 14",
                    "        box.corner_radius_bottom_right = 14",
                    "        box.border_width_top = 2",
                    "        box.border_width_bottom = 2",
                    "        box.border_width_left = 2",
                    "        box.border_width_right = 2",
                    "        box.border_color = Color(1.0, 1.0, 1.0, 0.25)",
                    "        _gk_dialog_panel.add_theme_stylebox_override(\"panel\", box)",
                    # 面板不吃鼠标:点哪里都算"下一句",别被面板本身拦掉
                    # 面板本身接收鼠标点击，保证暂停时点击对白区域也能推进；
                    # 文字和立绘子节点仍忽略鼠标，不会遮住面板事件。
                    "        _gk_dialog_panel.mouse_filter = Control.MOUSE_FILTER_STOP",
                    "        _gk_dialog_panel.gui_input.connect(_gk_dialog_gui_input)",
                    "        _gk_ui_root.add_child(_gk_dialog_panel)",
                    "        _gk_dialog_name = Label.new()",
                    "        _gk_dialog_name.name = \"说话人\"",
                    "        _gk_dialog_name.add_theme_font_size_override(\"font_size\", 22)",
                    "        _gk_dialog_name.add_theme_color_override(\"font_color\", Color(1.0, 0.86, 0.45, 1.0))",
                    "        _gk_dialog_name.mouse_filter = Control.MOUSE_FILTER_IGNORE",
                    "        _gk_dialog_panel.add_child(_gk_dialog_name)",
                    "        _gk_dialog_text = Label.new()",
                    "        _gk_dialog_text.name = \"正文\"",
                    "        _gk_dialog_text.add_theme_font_size_override(\"font_size\", 20)",
                    "        _gk_dialog_text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART",
                    "        _gk_dialog_text.mouse_filter = Control.MOUSE_FILTER_IGNORE",
                    "        _gk_dialog_panel.add_child(_gk_dialog_text)",
                    "        _gk_dialog_portrait = TextureRect.new()",
                    "        _gk_dialog_portrait.name = \"立绘\"",
                    "        _gk_dialog_portrait.expand_mode = TextureRect.EXPAND_IGNORE_SIZE",
                    "        _gk_dialog_portrait.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED",
                    "        _gk_dialog_portrait.mouse_filter = Control.MOUSE_FILTER_IGNORE",
                    "        _gk_dialog_panel.add_child(_gk_dialog_portrait)",
                    # 底边居中:窗口变了对话框也跟着走(锚点预设,和界面节点同一套)
                    "    _gk_dialog_panel.size = Vector2(width, height)",
                    "    _gk_dialog_panel.set_anchors_and_offsets_preset(",
                    "        Control.PRESET_CENTER_BOTTOM, Control.PRESET_MODE_KEEP_SIZE, margin)",
                    "    var pad := 22.0",
                    "    var has_portrait := false",
                    "    if portrait != \"\" and ResourceLoader.exists(portrait):",
                    "        _gk_dialog_portrait.texture = load(portrait)",
                    "        _gk_dialog_portrait.visible = true",
                    "        has_portrait = true",
                    "    else:",
                    "        _gk_dialog_portrait.visible = false",
                    "    if has_portrait:",
                    "        _gk_dialog_portrait.position = Vector2(pad, pad)",
                    "        _gk_dialog_portrait.size = Vector2(height - pad * 2.0, height - pad * 2.0)",
                    "    var text_left := pad + (height - pad * 2.0 + pad if has_portrait else 0.0)",
                    "    _gk_dialog_name.visible = speaker != \"\"",
                    "    _gk_dialog_name.position = Vector2(text_left, pad * 0.5)",
                    "    _gk_dialog_text.position = Vector2(text_left, pad + 26.0)",
                    "    _gk_dialog_text.size = Vector2(width - text_left - pad, height - pad * 2.0 - 26.0)",
                    "    _gk_dialog_name.text = speaker",
                    "    _gk_dialog_text.text = body",
                    "    _gk_dialog_text.visible_characters = 0",
                    "    if _gk_dialog_tween != null and _gk_dialog_tween.is_valid():",
                    "        _gk_dialog_tween.kill()",
                    "    var count := body.length()",
                    "    _gk_dialog_blip_step = 0",
                    "    if speed > 0.0 and count > 0:",
                    "        _gk_dialog_tween = _gk_dialog_panel.create_tween()",
                    # ⚠ 逐字冒出的 tween 必须**暂停时也照跑**(第一百一十轮真机抓到):
                    #   对话/选择是配合『界面_暂停游戏』用的(暂停世界 + 放出鼠标),
                    #   而 tween 默认 `TWEEN_PAUSE_BOUND` —— 绑在暂停树里的节点上
                    #   就一起冻住,`visible_characters` 永远停在 0,玩家看到的是
                    #   **一个空面板**("文字消失了,鼠标还在")。真机截图复现过;
                    #   无头探针看不到,是因为探针自己挂在 PROCESS_MODE_ALWAYS 下,
                    #   把整个关卡也带成了"暂停也跑"。
                    "        _gk_dialog_tween.set_pause_mode(Tween.TWEEN_PAUSE_PROCESS)",
                    "        if _gk_dialog_blip != \"\":",
                    "            # 有打字音:走 tween_method,每帧自己推进『已显示几个字』",
                    "            # 并『每 N 个字』响一声(属性 tween 插不进回调)",
                    "            _gk_dialog_tween.tween_method(",
                    "                func(shown: float) -> void: _gk_dialog_advance(host, int(shown)),",
                    "                0.0, float(count), float(count) / speed)",
                    "        else:",
                    "            _gk_dialog_tween.tween_property(_gk_dialog_text, \"visible_characters\", count, float(count) / speed)",
                    "    else:",
                    "        _gk_dialog_text.visible_characters = -1",
                    "    _gk_dialog_panel.visible = true",
                    "    _gk_dialog_panel.modulate.a = 1.0    # 字幕淡出过之后要能亮回来",
                    "    _gk_dialog_clicked = false",
                    "    # 新的一句顶掉上一句:老字幕的定时器看到『代数』变了就不再关面板",
                    "    _gk_sub_token += 1",
                    "    # 自动记进对话历史(『对话_历史』读它);上限 200 句,免得长流程吃内存",
                    "    _gk_dialog_history.append({\"who\": speaker, \"text\": body})",
                    "    if _gk_dialog_history.size() > 200:",
                    "        _gk_dialog_history.pop_front()",
                ],
                "empty_body": "    pass",
                "separator": True,
            })
            sections.append({
                "signature": "func _gk_dialog_typing() -> bool:",
                "lines": [
                    "    return _gk_dialog_tween != null and _gk_dialog_tween.is_valid() and _gk_dialog_tween.is_running()",
                ],
                "empty_body": "    pass",
            })
            sections.append({
                "signature": "func _gk_dialog_finish() -> void:",
                "lines": [
                    "    if _gk_dialog_tween != null and _gk_dialog_tween.is_valid():",
                    "        _gk_dialog_tween.kill()",
                    "    if _gk_dialog_text != null:",
                    "        _gk_dialog_text.visible_characters = -1",
                ],
                "empty_body": "    pass",
            })
            sections.append({
                "signature": "func _gk_dialog_hide() -> void:",
                "lines": [
                    "    if _gk_dialog_panel != null:",
                    "        _gk_dialog_panel.visible = false",
                ],
                "empty_body": "    pass",
            })
            # ── 打字机音效(第六十四轮)────────────────────────────────
            # 逐字推进 + "每 N 个字"响一声。只在**开了打字音**时才走这条路
            # (见 _gk_dialog_show 里那个 if),所以这两个函数对没配打字音
            # 的图是死代码 —— 但也没别的地方放它们,就一起生成。
            sections.append({
                "signature": "func _gk_dialog_advance(host: Node, shown: int) -> void:",
                "lines": [
                    "    _gk_dialog_text.visible_characters = shown",
                    "    if _gk_dialog_blip == \"\" or _gk_dialog_blip_every <= 0:",
                    "        return",
                    "    # 『每 N 个字响一声』:用『已经响到第几次』记账,不按帧算",
                    "    var step := shown / _gk_dialog_blip_every",
                    "    if step <= _gk_dialog_blip_step:",
                    "        return",
                    "    _gk_dialog_blip_step = step",
                    "    _gk_play_blip(host, _gk_dialog_blip, _gk_dialog_blip_db)",
                ],
                "empty_body": "    pass",
            })
            sections.append({
                "signature": "func _gk_play_blip(host: Node, path: String, db: float) -> void:",
                "lines": [
                    "    if path == \"\" or not ResourceLoader.exists(path):",
                    "        return",
                    "    # 播放器**复用同一个**:每个字新建一个会瞬间堆出几十个节点",
                    "    if _gk_blip_player == null:",
                    "        _gk_blip_player = AudioStreamPlayer.new()",
                    "        _gk_blip_player.name = \"GKBlip\"",
                    "        host.add_child(_gk_blip_player)",
                    "    if _gk_blip_cache != path:",
                    "        _gk_blip_player.stream = load(path) as AudioStream",
                    "        _gk_blip_cache = path",
                    "    _gk_blip_player.volume_db = db",
                    "    _gk_blip_player.play()",
                ],
                "empty_body": "    pass",
                "separator": True,
            })
            sections.append({
                "signature": "func _gk_dialog_gui_input(event: InputEvent) -> void:",
                "lines": [
                    "    if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:",
                    "        _gk_dialog_clicked = true",
                ],
                "empty_body": "    pass",
            })

        # ---- 字幕行(第六十四轮:"字幕行 / 打字机音效")------------------
        # 复用对话框那块面板,但**不等玩家点**:飘出来之后立刻返回,
        # 后面的节点照跑(运镜 + 台词同时进行才写得出来)。到点由一个
        # SceneTreeTimer 把面板淡出关掉;`_gk_sub_token` 是"第几代" ——
        # 新字幕/新对话框一来就 +1,老定时器看到代数变了就**不再关面板**
        # (不然老字幕的定时器会把后面新弹的对话框关掉)。
        if self.ctx.need_subtitle_helpers:
            # 说话人立绘(第八十一轮):`portrait` 是**带默认值的形参** ——
            # 老调用点(9 个实参)照样合法,填了立绘的调用点才多传一个
            sections.append({
                "signature": ("func _gk_subtitle_show(host: Node, speaker: String, "
                              "body: String, speed: float, seconds: float, "
                              "fade: float, width: float, height: float, "
                              "margin: float, portrait: String = \"\") -> void:"),
                "lines": [
                    "    _gk_dialog_show(host, speaker, body, speed, portrait, width, height, margin)",
                    "    _gk_sub_token += 1",
                    "    var token := _gk_sub_token",
                    # ⚠ 第二个实参 `process_always=false`:暂停时字幕定时器也要停。
                    #   默认值 true 会让"暂停中字幕自己消失"(用户第 125 轮
                    #   报的那条类问题:暂停没有真的冻住所有东西)。
                    "    var timer := host.get_tree().create_timer(maxf(seconds, 0.05), false)",
                    "    timer.timeout.connect(func() -> void:",
                    "        if token != _gk_sub_token:",
                    "            return      # 已经有新的一句顶掉了,别关",
                    "        if fade > 0.0:",
                    "            var tween := _gk_dialog_panel.create_tween()",
                    "            tween.tween_property(_gk_dialog_panel, \"modulate:a\", 0.0, fade)",
                    "            tween.tween_callback(_gk_dialog_hide)",
                    "        else:",
                    "            _gk_dialog_hide())",
                ],
                "empty_body": "    pass",
                "separator": True,
            })

        # ---- 对话历史回顾(第六十四轮)----------------------------------
        # 历史本身是 `_gk_dialog_show` 每显示一句就 append 的(在对话框那段),
        # 这里只负责"把记下来的列出来给人看"。滚动容器设成 STOP:
        # 鼠标滚轮滚得动(点一下仍然关 —— 那是 `_input` 管的,不受它影响)。
        if self.ctx.need_history_helpers:
            # 导出路径 / 点句重放(第八十一轮):两个都是**带默认值的形参** ——
            # 老调用点(6 个实参)一字不动,填了开关的调用点才多传尾巴
            sections.append({
                "signature": ("func _gk_history_show(host: Node, title: String, "
                              "width: float, height: float, font_size: int, "
                              "empty_hint: String, export_path: String = \"\", "
                              "replay: bool = false) -> void:"),
                "lines": [
                    "    _gk_ensure_ui(host)",
                    "    if _gk_history_panel == null:",
                    "        _gk_history_panel = Panel.new()",
                    "        _gk_history_panel.name = \"对话历史\"",
                    "        var box := StyleBoxFlat.new()",
                    "        box.bg_color = Color(0.05, 0.06, 0.09, 0.92)",
                    "        box.corner_radius_top_left = 14",
                    "        box.corner_radius_top_right = 14",
                    "        box.corner_radius_bottom_left = 14",
                    "        box.corner_radius_bottom_right = 14",
                    "        box.border_width_top = 2",
                    "        box.border_width_bottom = 2",
                    "        box.border_width_left = 2",
                    "        box.border_width_right = 2",
                    "        box.border_color = Color(1.0, 1.0, 1.0, 0.25)",
                    "        _gk_history_panel.add_theme_stylebox_override(\"panel\", box)",
                    "        # 面板本身不吃鼠标:点哪儿都算『关掉』(见 gk_history_input_event)",
                    "        _gk_history_panel.mouse_filter = Control.MOUSE_FILTER_IGNORE",
                    "        _gk_ui_root.add_child(_gk_history_panel)",
                    "        _gk_history_title = Label.new()",
                    "        _gk_history_title.name = \"标题\"",
                    "        _gk_history_title.mouse_filter = Control.MOUSE_FILTER_IGNORE",
                    "        _gk_history_panel.add_child(_gk_history_title)",
                    "        _gk_history_scroll = ScrollContainer.new()",
                    "        _gk_history_scroll.name = \"滚动\"",
                    "        # STOP = 滚轮能滚它(关面板走 `_input`,不受这里影响)",
                    "        _gk_history_scroll.mouse_filter = Control.MOUSE_FILTER_STOP",
                    "        _gk_history_panel.add_child(_gk_history_scroll)",
                    "        _gk_history_list = VBoxContainer.new()",
                    "        _gk_history_list.name = \"列表\"",
                    "        _gk_history_list.mouse_filter = Control.MOUSE_FILTER_IGNORE",
                    "        _gk_history_scroll.add_child(_gk_history_list)",
                    "    _gk_history_panel.size = Vector2(width, height)",
                    "    _gk_history_panel.set_anchors_and_offsets_preset(",
                    "        Control.PRESET_CENTER, Control.PRESET_MODE_KEEP_SIZE)",
                    "    _gk_history_title.text = title",
                    "    _gk_history_title.add_theme_font_size_override(\"font_size\", font_size + 4)",
                    "    _gk_history_title.position = Vector2(18.0, 12.0)",
                    # ── 导出按钮(第八十一轮)───────────────────────────
                    # 只建一次(成员变量记着),翻几次历史都不会越堆越多;
                    # 这次没填导出路径就把它藏起来(同一张图里两张历史卡
                    # 一个填一个没填时,行为跟最后走过的那张卡)
                    "    if export_path != \"\":",
                    "        if _gk_history_export_btn == null:",
                    "            _gk_history_export_btn = Button.new()",
                    "            _gk_history_export_btn.name = \"导出\"",
                    "            _gk_history_export_btn.text = \"导出\"",
                    "            _gk_history_export_btn.pressed.connect(",
                    "                func() -> void: _gk_history_export(export_path))",
                    "            _gk_history_panel.add_child(_gk_history_export_btn)",
                    "        _gk_history_export_btn.size = Vector2(84.0, 30.0)",
                    "        _gk_history_export_btn.position = Vector2(width - 102.0, 10.0)",
                    "        _gk_history_export_btn.visible = true",
                    "    elif _gk_history_export_btn != null:",
                    "        _gk_history_export_btn.visible = false",
                    "    var top := 12.0 + float(font_size) + 22.0",
                    "    _gk_history_scroll.position = Vector2(18.0, top)",
                    "    _gk_history_scroll.size = Vector2(width - 36.0, height - top - 16.0)",
                    "    # 先摘再有声地删:只用 queue_free 的话这一帧它还挂在树上,",
                    "    # 下面的新行会跟旧行**同时**显示一帧(看起来像重影)",
                    "    for child in _gk_history_list.get_children():",
                    "        _gk_history_list.remove_child(child)",
                    "        child.queue_free()",
                    "    if _gk_dialog_history.is_empty():",
                    "        var hint := Label.new()",
                    "        hint.text = empty_hint",
                    "        hint.add_theme_font_size_override(\"font_size\", font_size)",
                    "        hint.add_theme_color_override(\"font_color\", Color(1.0, 1.0, 1.0, 0.6))",
                    "        _gk_history_list.add_child(hint)",
                    "    else:",
                    "        for entry in _gk_dialog_history:",
                    "            var who := str(entry.get(\"who\", \"\"))",
                    "            var body := str(entry.get(\"text\", \"\"))",
                    "            var row_text := body if who == \"\" else who + \"：\" + body",
                    # ── 点句重放(第八十一轮)───────────────────────────
                    # 开了重放:每行是个**按钮**,点它 -> 面板收起 + 那一句
                    # 像字幕一样再飘一遍(相当于跳回去重看那一句)。
                    # 没开:还是 Label(老行为,一个像素都不变)
                    "            if replay:",
                    "                var row := Button.new()",
                    "                row.flat = true",
                    "                row.alignment = HORIZONTAL_ALIGNMENT_LEFT",
                    "                row.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART",
                    "                row.custom_minimum_size = Vector2(width - 56.0, 0.0)",
                    "                row.add_theme_font_size_override(\"font_size\", font_size)",
                    "                row.text = row_text",
                    "                row.pressed.connect(",
                    "                    func() -> void: _gk_history_replay(host, who, body))",
                    "                _gk_history_list.add_child(row)",
                    "            else:",
                    "                var line := Label.new()",
                    "                line.text = row_text",
                    "                line.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART",
                    "                line.custom_minimum_size = Vector2(width - 56.0, 0.0)",
                    "                line.add_theme_font_size_override(\"font_size\", font_size)",
                    "                _gk_history_list.add_child(line)",
                    "    _gk_history_panel.visible = true",
                    "    _gk_history_clicked = false",
                ],
                "empty_body": "    pass",
            })
            sections.append({
                "signature": "func _gk_history_hide() -> void:",
                "lines": [
                    "    if _gk_history_panel != null:",
                    "        _gk_history_panel.visible = false",
                ],
                "empty_body": "    pass",
                "separator": True,
            })

        # ---- 对话历史:导出 / 点句重放(第八十一轮)----------------------
        # 两个都是**填了才有**的死代码不给老图添:导出 = 把整段历史写成
        # 文本(user:// 建议);重放 = 那一句像字幕一样再飘一遍(speed 0 =
        # 直接全显示,3 秒后淡出 —— 和字幕同一套 helper,观感一致)。
        if self.ctx.need_history_export:
            sections.append({
                "signature": "func _gk_history_export(path: String) -> void:",
                "lines": [
                    "    if path == \"\":",
                    "        return",
                    "    var file := FileAccess.open(path, FileAccess.WRITE)",
                    "    if file == null:",
                    "        push_warning(\"对话历史导出失败(打不开文件): \" + path)",
                    "        return",
                    "    for entry in _gk_dialog_history:",
                    "        var who := str(entry.get(\"who\", \"\"))",
                    "        var text := str(entry.get(\"text\", \"\"))",
                    "        file.store_line(text if who == \"\" else who + \"：\" + text)",
                    "    file.close()",
                    "    print(\"[GK] 对话历史已导出: \", path)",
                ],
                "empty_body": "    pass",
                "separator": True,
            })
        if self.ctx.need_history_replay:
            sections.append({
                "signature": ("func _gk_history_replay(host: Node, who: String, "
                              "text: String) -> void:"),
                "lines": [
                    "    _gk_history_hide()",
                    "    _gk_subtitle_show(host, who, text, 0.0, 3.0, 0.3, "
                    "720.0, 110.0, 48.0)",
                ],
                "empty_body": "    pass",
                "separator": True,
            })

        # ---- 对话选择(第四十九轮:阶段③『对话_选择』分支)--------------
        # 和对话框同一族的深色圆角面板,但里面是:问题 + 一列**可点的按钮**。
        # 面板本身不吃鼠标(点击要能穿到按钮上),按钮各接各的 `_gk_choice_pick(序号)`。
        # 数字键 1~6 走 `_input`(见 gk_choice_input_event),两条路都通。
        if self.ctx.need_choice_helpers:
            sections.append({
                "signature": ("func _gk_choice_show(host: Node, question: String, "
                              "options: Array, speed: float, width: float, "
                              "height: float, margin: float) -> void:"),
                "lines": [
                    "    _gk_ensure_ui(host)",
                    "    _gk_choice_picked = -1",
                    "    if _gk_choice_panel == null:",
                    "        _gk_choice_panel = Panel.new()",
                    "        _gk_choice_panel.name = \"选择框\"",
                    "        var box := StyleBoxFlat.new()",
                    "        box.bg_color = Color(0.05, 0.06, 0.09, 0.88)",
                    "        box.corner_radius_top_left = 14",
                    "        box.corner_radius_top_right = 14",
                    "        box.corner_radius_bottom_left = 14",
                    "        box.corner_radius_bottom_right = 14",
                    "        box.border_width_top = 2",
                    "        box.border_width_bottom = 2",
                    "        box.border_width_left = 2",
                    "        box.border_width_right = 2",
                    "        box.border_color = Color(1.0, 1.0, 1.0, 0.25)",
                    "        _gk_choice_panel.add_theme_stylebox_override(\"panel\", box)",
                    # 面板自己不吃鼠标:点击要能穿到下面的选项按钮上
                    "        _gk_choice_panel.mouse_filter = Control.MOUSE_FILTER_IGNORE",
                    "        _gk_ui_root.add_child(_gk_choice_panel)",
                    "        _gk_choice_question = Label.new()",
                    "        _gk_choice_question.name = \"问题\"",
                    "        _gk_choice_question.add_theme_font_size_override(\"font_size\", 22)",
                    "        _gk_choice_question.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER",
                    "        _gk_choice_question.mouse_filter = Control.MOUSE_FILTER_IGNORE",
                    "        _gk_choice_panel.add_child(_gk_choice_question)",
                    "        _gk_choice_box = VBoxContainer.new()",
                    "        _gk_choice_box.name = \"选项\"",
                    "        _gk_choice_box.add_theme_constant_override(\"separation\", 10)",
                    "        _gk_choice_box.mouse_filter = Control.MOUSE_FILTER_IGNORE",
                    "        _gk_choice_panel.add_child(_gk_choice_box)",
                    # 上一次的按钮清掉再建新的(选项数 / 文字都可能变了)。
                    # ⚠ 先 remove_child 再 queue_free:queue_free 要到帧末才真的摘掉,
                    #   那一帧里新旧按钮会同时占位置(列表那边踩过同样的坑)。
                    "    for child in _gk_choice_box.get_children():",
                    "        _gk_choice_box.remove_child(child)",
                    "        child.queue_free()",
                    "    _gk_choice_panel.size = Vector2(width, height)",
                    "    _gk_choice_panel.set_anchors_and_offsets_preset(",
                    "        Control.PRESET_CENTER_BOTTOM, Control.PRESET_MODE_KEEP_SIZE, margin)",
                    "    var pad := 22.0",
                    # 问题标题也给出键盘入口;只在选项存在时拼接,避免空选项
                    # 的自定义对话出现误导提示。
                    "    _gk_choice_question.text = question + (\" (按 1-%d 选择)\" % options.size() if not options.is_empty() else \"\")",
                    # 问题也逐字冒出来(和对话框同一种手感);按钮**立刻**可点,
                    # 玩家不等它打完就能选 —— 打字只是好看,不挡操作。
                    # ⚠ 这条 tween 必须**暂停时也照跑**(第一百一十轮真机抓到):
                    #   选择框是配合『界面_暂停游戏』用的(暂停世界 + 放出鼠标,
                    #   玩家才能点),而 tween 默认 `TWEEN_PAUSE_BOUND` —— 绑在
                    #   暂停树里的节点上就一起冻住,`visible_characters` 永远停在
                    #   0,玩家看到的是**一个空面板**(文字"消失"了)。
                    #   真机截图复现过;无头探针看不到是因为探针自己挂在
                    #   PROCESS_MODE_ALWAYS 下,把整个关卡也带成了"暂停也跑"。
                    "    _gk_choice_question.visible_characters = 0",
                    "    var chars := _gk_choice_question.text.length()",
                    "    if speed > 0.0 and chars > 0:",
                    "        var _gk_q_tween := _gk_choice_question.create_tween()",
                    "        _gk_q_tween.set_pause_mode(Tween.TWEEN_PAUSE_PROCESS)",
                    "        _gk_q_tween.tween_property(",
                    "            _gk_choice_question, \"visible_characters\", chars, float(chars) / speed)",
                    "    else:",
                    "        _gk_choice_question.visible_characters = -1",
                    "    _gk_choice_question.position = Vector2(pad, pad * 0.5)",
                    "    _gk_choice_question.size = Vector2(width - pad * 2.0, 30.0)",
                    "    _gk_choice_box.position = Vector2(pad, pad + 34.0)",
                    "    _gk_choice_box.size = Vector2(width - pad * 2.0, height - pad * 2.0 - 34.0)",
                    "    for index in range(options.size()):",
                    "        var button := Button.new()",
                    # 名字带序号:探针 / 调试面板能按名字找到第几个选项
                    "        button.name = \"选择%d\" % (index + 1)",
                    # 按钮上把**键盘数字键**写出来(第一百一十轮):
                    # 选项本来就能按 1~6 选(见 _gk_choice_input_event),可卡片上
                    # 只写"往前跑",玩家根本不知道有键盘这条路 —— 用户实测原话
                    # "部分提示没有按键显示…按1,2键进行选择对话"。数字 + 点号
                    # 是"按 1 选第一个"最省字的写法。
                    "        button.text = str(index + 1) + \". \" + str(options[index])",
                    "        button.custom_minimum_size = Vector2(0.0, 38.0)",
                    "        button.mouse_filter = Control.MOUSE_FILTER_STOP",
                    "        button.pressed.connect(_gk_choice_pick.bind(index + 1))",
                    "        _gk_choice_box.add_child(button)",
                    "    _gk_choice_panel.visible = true",
                ],
                "empty_body": "    pass",
                "separator": True,
            })
            sections.append({
                "signature": "func _gk_choice_pick(index: int) -> void:",
                "lines": [
                    "    if _gk_choice_panel == null or not _gk_choice_panel.visible:",
                    "        return",
                    "    _gk_choice_picked = index",
                ],
                "empty_body": "    pass",
            })
            sections.append({
                "signature": "func _gk_choice_hide() -> void:",
                "lines": [
                    "    if _gk_choice_panel != null:",
                    "        _gk_choice_panel.visible = false",
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

        # ---- 屏幕暗角(第五十一轮:阶段④屏幕氛围)------------------------
        # 和滤镜同一个套路,但**各占一层**(layer 92 > 滤镜的 90):
        # 先染色再压暗角,两个节点能叠着用,谁也不改对方的着色器。
        if self.ctx.need_screen_vignette:
            sections.append({
                "signature": "func _gk_ensure_vignette(host: Node) -> void:",
                "lines": [
                    "    if _gk_vignette_layer != null:",
                    "        return",
                    "    _gk_vignette_layer = CanvasLayer.new()",
                    "    _gk_vignette_layer.name = \"GKVignette\"",
                    # layer 92:盖住滤镜(90)和界面(10),但低于闪白/转场(100)
                    "    _gk_vignette_layer.layer = 92",
                    "    host.add_child(_gk_vignette_layer)",
                    "    _gk_vignette_rect = ColorRect.new()",
                    "    _gk_vignette_rect.set_anchors_preset(Control.PRESET_FULL_RECT)",
                    "    _gk_vignette_rect.mouse_filter = Control.MOUSE_FILTER_IGNORE",
                    "    var sh := Shader.new()",
                    "    sh.code = _GK_VIGNETTE_SHADER",
                    "    _gk_vignette_mat = ShaderMaterial.new()",
                    "    _gk_vignette_mat.shader = sh",
                    "    _gk_vignette_mat.set_shader_parameter(\"vignette_amount\", 0.0)",
                    "    _gk_vignette_mat.set_shader_parameter(\"vignette_radius\", 0.55)",
                    "    _gk_vignette_mat.set_shader_parameter(\"vignette_edge\", 0.35)",
                    "    _gk_vignette_mat.set_shader_parameter(\"vignette_color\", Color(0, 0, 0, 1))",
                    "    _gk_vignette_rect.material = _gk_vignette_mat",
                    "    _gk_vignette_layer.add_child(_gk_vignette_rect)",
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

        # ---- 晚来的引用也得有 helper(第六十三轮抓到的真 bug)--------
        # 见 _emit_reference_helpers 的说明:AI / 动画 / 界面设计图是**被引用
        # 时现场展开**的,它们动员出来的 `@onready` 引用比上面那次调用晚。
        # 不在这儿再兜一次,"引用只来自 AI 图"的脚本就会带着一个
        #   Parse Error: Function "_gk_ref_typed()" not found in base self
        # 上线 —— 那不是少个功能,是整个脚本加载不了。
        # (方法里有守卫,重复调用不会生成两份。)
        self._emit_reference_helpers(sections)

        return self._assemble(sections)

    def _emit_reference_helpers(self, sections):
        """生成 `_gk_ref` / `_gk_ref_typed`(带兜底查找的节点引用工具)。

        `@onready var 节点_玩家: Node = _gk_ref_typed(self, "玩家", "Node")`
        里的 helper **必须**一起生成,否则 Godot 直接报
        "Function _gk_ref_typed() not found in base self" —— 整个脚本加载
        不了。而引用是**边生成边登记**的(ctx.onready),谁先谁后不确定:

          * 普通节点(『移动_朝向目标』那种)在事件段里就登记好了 ——
            `generate()` 前半段那次调用就能补上,老图产物**逐字不变**;
          * AI 图 / 动画图 / 界面设计图是『AI_启用』『动画_状态机』
            『界面_套用设计』**引用时现场展开**的,它们在 `generate()`
            很靠后的位置才跑 —— 只有前面那次的话就会漏掉(实测:示例
            `examples/demo_ai_nav` 的敌人脚本就是这么坏的)。

        所以同一个方法在**前面和末尾**各调一次,靠 `_refs_emitted` 保证
        只落一份。
        """
        if not self.ctx.onready or getattr(self, "_refs_emitted", False):
            return
        self._refs_emitted = True
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
        sections.append({
            "signature": ("func _gk_ref_typed(host: Node, wanted: String, "
                          "type_name: String) -> Node:"),
            "lines": [
                "    # ── 为什么按类型找(用户 2026-10-04,实测)────────────────",
                "    # 导出后一个物体常常是**两个节点**:网格在 glb 实例里(根下面),",
                "    # 碰撞代理在 Colliders 下 —— 两个同名。`_gk_ref` 按树的顺序找,",
                "    # 拿到的往往是网格(MeshInstance3D),赋给 `area_金币1: Area3D` 直接报",
                "    #     Trying to assign value of type 'MeshInstance3D' to a",
                "    #     variable of type 'Area3D'",
                "    # 于是触发器的 body_entered 永远接不上(金币吃不了)。",
                "    # 所以先在全树里找**类型对得上**的那个,找不到再退回老逻辑。",
                "    if wanted.is_empty() or host.get_tree() == null:",
                "        return null",
                "    if ClassDB.class_exists(type_name):",
                "        for found in host.get_tree().root.find_children("
                "wanted, type_name, true, false):",
                "            return found",
                "    return _gk_ref(host, wanted)",
            ],
            "empty_body": "    return null",
            "separator": True,
        })

    def _emit_functions(self, sections):
        """把每个『函数_定义』节点编译成一个 `func`(用户 2026-10-05)。

        签名来自**侧栏『函数』框里的声明**(`props.tree_functions`),
        函数体来自节点『执行』口接出去的那条链。

        ── 为什么签名必须走声明,不能就地读节点 ────────────────────
        GDScript 的 `func` 签名在**两个地方**被用到:定义处要写形参,
        调用处要传实参 —— 两处各读各的就会对不上(少了/多了/顺序变了
        都只有运行时才炸)。所以一律查同一张声明表。
        """
        from .. import properties as props
        for node in self._nodes():
            if getattr(node, "bl_idname", "") != "GKNodeFunctionDefine":
                continue
            raw_name = str(getattr(node, "gk_func_name", "") or "").strip()
            if not raw_name:
                self.ctx.warn("『函数_定义』没填『函数』—— 它被跳过了"
                              "(旁边放大镜可以从侧栏声明里挑)")
                continue
            spec = self._function_spec(raw_name)
            if spec is None:
                self.ctx.warn(f"『函数_定义』填的『{raw_name}』没有在侧栏"
                              f"『函数』框里声明过 —— 它被跳过了")
                continue
            name = safe_function_name(raw_name)
            if name in self.ctx.defined_functions:
                self.ctx.warn(f"函数『{raw_name}』被定义了**两次** —— "
                              f"Godot 不允许同名函数(会拒绝整个脚本),"
                              f"只保留第一个。请删掉重复的『函数_定义』节点")
                continue
            self.ctx.defined_functions.add(name)

            params = list(spec.get("params") or ())
            for param_name, param_type in params:
                self._check_param_name(node, param_name)
            # 参数登记:函数体里读同名的『变量_读取』就落在这个形参上
            saved = dict(self.ctx.func_params)
            self.ctx.func_params.update(dict(params))
            self.ctx._active.clear()
            prev_event = self.ctx._current_event
            self.ctx._current_event = node.name
            lines = self.ctx.new_section()
            try:
                self._emit_event_body(node, 1)
                # 返回:『返回』引脚接了东西就 return 它
                return_expr = self._function_return_expr(node, spec)
                if return_expr:
                    lines.add(1, f"return {return_expr}")
            finally:
                self.ctx._current_event = prev_event
                self.ctx.func_params = saved

            signature = self._function_signature(name, params, spec)
            sections.append({
                "signature": signature,
                "lines": list(lines.lines),
                "empty_body": ("    pass" if not spec.get("return")
                               else self._default_return(spec["return"])),
                "separator": True,
            })

    def _function_spec(self, name):
        """查这张图里声明的函数(离线桩环境没有声明时返回 None)。"""
        try:
            from .. import properties as props
            tree = self._tree()
            if tree is None:
                return None
            return props.tree_functions(tree).get(name)
        except Exception:                                     # noqa: BLE001
            return None

    def _tree(self):
        return getattr(self, "tree", None)

    def _check_param_name(self, node, param_name):
        """参数名和已有的成员变量重名 -> 明确告警(不静默生成遮蔽)。"""
        if param_name in getattr(self.ctx, "vars", {}):
            self.ctx.warn(
                f"函数参数『{param_name}』和脚本里的同名变量撞了 —— "
                f"函数体里读到的会是**参数**(Godot 里叫变量遮蔽)。"
                f"建议把参数改个名(比如 {param_name}_in)")

    def _function_signature(self, name, params, spec):
        """`func 名字(a: float, b: bool) -> void:`。"""
        args = ", ".join(f"{param}: {kind}" for param, kind in params)
        ret = str(spec.get("return") or "")
        if ret:
            return f"func {name}({args}) -> {ret}:"
        return f"func {name}({args}) -> void:"

    @staticmethod
    def _default_return(gd_type):
        """有返回类型但图里没接『返回』引脚时的兜底 return。

        不写兜底的话 Godot 会因为"并非所有代码路径都 return"拒绝整个脚本
        (那是**编译错误**,不是警告)。
        """
        return {"bool": "    return false", "int": "    return 0",
                "float": "    return 0.0", "String": '    return ""',
                "Vector3": "    return Vector3.ZERO",
                "Vector2": "    return Vector2.ZERO"}.get(
                    gd_type, "    return null")

    def _function_return_expr(self, node, spec):
        """『返回』引脚接了什么就 return 它(没接返回值时返回 "")。"""
        if not spec.get("return"):
            return ""
        socket = None
        inputs = getattr(node, "inputs", None)
        getter = getattr(inputs, "get", None) if inputs is not None else None
        if callable(getter):
            socket = getter("返回")
        if socket is None:
            return ""
        if getattr(socket, "is_linked", False):
            return self.ctx.expr_of(socket)
        # 没接线但引脚上填了值 -> 用它(和别的参数引脚一个规矩)
        value = self.ctx.socket_default(socket)
        return "" if value in ("null", None) else value

    # ---- 事件收集 ----
    def _asset_spawn_lines(self):
        """`_gk_spawn_asset` 的函数体(3D / 2D 两版)。

        ── 为什么就地把 glb 根变换反过来抵消,而不是导出时算好矩阵 ─────
        glb 的根节点带着"本体在 Blender 里的世界变换",不抵消的话新实例的
        网格会**偏到本体原来的位置**(和 .tscn 里同款处理)。
        就地算的好处:本体挪了位置、改了大小时不用重新导出也能对上。
        """
        two_d = self.ctx.dim == 2
        node_type = "Node2D" if two_d else "Node3D"
        body_type = "StaticBody2D" if two_d else "StaticBody3D"
        shape_type = "CollisionShape2D" if two_d else "CollisionShape3D"
        box_type = "RectangleShape2D" if two_d else "BoxShape3D"
        ball_type = "CircleShape2D" if two_d else "SphereShape3D"
        cap_type = "CapsuleShape2D" if two_d else "CapsuleShape3D"
        ctor = "Vector2" if two_d else "Vector3"
        one = "Vector2.ONE" if two_d else "Vector3.ONE"
        zero = "Vector2.ZERO" if two_d else "Vector3.ZERO"
        lines = [
            "    var packed := load(path) as PackedScene",
            "    if packed == null:",
            '        push_error("[GK] 资产模型加载失败: %s" % path)',
            "        return null",
            f"    var body: {node_type} = null",
            # ── 形状表(本轮:多碰撞体 + 凸包)────────────────────────
            # shapes 是导出侧算好的字典数组:本体一条 + 每个碰撞块一条。
            # 逐条建 CollisionShape、按 offset 摆好 —— 和 .tscn 里那份
            # 完全同源,生成的实例和本体"长得一样"。
            "    if not shapes.is_empty():",
            f"        var solid := {body_type}.new()",
            # 碰撞层跟本体一致 —— 不一样的话"生成的这份"会突然穿人 / 挡人
            "        solid.collision_layer = layer",
            "        solid.collision_mask = mask",
            "        for data in shapes:",
            f"            var shape := {shape_type}.new()",
            '            match int(data.get("kind", 0)):',
            "                1:",
            f"                    var ball := {ball_type}.new()",
            '                    ball.radius = maxf(float(data.get("radius", 0.5)), 0.05)',
            "                    shape.shape = ball",
            "                2:",
            f"                    var cap := {cap_type}.new()",
            '                    cap.radius = maxf(float(data.get("radius", 0.5)), 0.05)',
            '                    cap.height = maxf(float(data.get("height", 1.0)), '
            "cap.radius * 2.0)",
            "                    shape.shape = cap",
        ]
        if not two_d:
            lines += [
                "                3:",
                "                    var cyl := CylinderShape3D.new()",
                '                    cyl.radius = maxf(float(data.get("radius", 0.5)), 0.05)',
                '                    cyl.height = maxf(float(data.get("height", 1.0)), 0.05)',
                "                    shape.shape = cyl",
                "                4:",
                "                    var hull := ConvexPolygonShape3D.new()",
                '                    var pts: PackedVector3Array = '
                'data.get("points", PackedVector3Array())',
                "                    if pts.size() < 4:",
                "                        continue",
                "                    hull.points = pts",
                "                    shape.shape = hull",
            ]
        lines += [
            "                _:",
            f"                    var box := {box_type}.new()",
            f'                    box.size = {ctor}(data.get("dims", {one}))',
            "                    shape.shape = box",
            f'            shape.position = {ctor}(data.get("offset", {zero}))',
            "            solid.add_child(shape)",
            "        body = solid",
            "    else:",
            f"        body = {node_type}.new()",
            "    body.name = what",
            "    var model := packed.instantiate()",
            "    body.add_child(model)",
            f"    if model is {node_type}:",
            # glb 根带着"本体在 Blender 里的世界变换" -> 反过来抵消,
            # 网格才会落在我们要的位置上(跟新实例的碰撞严丝合缝)
            f"        var placed := model as {node_type}",
            "        placed.transform = placed.transform.affine_inverse()",
            "    body.position = where",
            "    return body",
        ]
        return lines

    # ---- 事件收集 ----
    def _collect_events(self):
        result = []
        for node in self._nodes():
            if getattr(node, "gk_is_event", False) and not getattr(node, "gk_is_comment", False):
                result.append(node)
        result.sort(key=position_key)
        return result

    def _nodes(self):
        nodes = getattr(self.tree, "nodes", ())
        result = list(nodes)
        # 让输出稳定:按 y 再按 x
        result.sort(key=position_key)
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
        params = self._declared_param_names()
        # ── 过场控件要在**发射任何代码之前**就数清楚(第一百二十六轮)────
        # 为什么必须提前扫:`_gk_apply_pause()` 里那句 `_gk_hide_transient(...)`
        # 跟"这张图里有没有过场控件"绑定,而它**可能比控件节点先生成**
        # ——『界面_暂停游戏』(Esc 热键)在示例里排在设计图上半部分,控件
        # 排在下半部分,谁先谁后由画布 Y 决定。发射到那儿才发现没有 helper,
        # 那句话就漏了(实测:走廊那关真的漏过,`_gk_apply_pause` 里少一行)。
        for node in self._nodes():
            if _ui_looks_transient(node) or bool(getattr(node, "gk_transient", False)):
                self.ctx.need_transient_helper = True
        # 设计图里建的控件也算(『界面_套用设计』把设计图建进这个场景时,
        # 那些控件的组登记就写在本脚本里)
        for tree in (getattr(self.ctx, "_gk_ui_design_trees", ()) or ()):
            for node in (getattr(tree, "nodes", ()) or ()):
                if (_ui_looks_transient(node)
                        or bool(getattr(node, "gk_transient", False))):
                    self.ctx.need_transient_helper = True
        for node in self._nodes():
            if node.bl_idname == "GKNodeEmitSignal" and getattr(node, "gk_signal_declare", True):
                name = snake_case(getattr(node, "gk_signal_name", "signal_name"), "signal_name")
                if name not in self.ctx.signals:
                    self.ctx.signals.append(name)
            if node.bl_idname == "GKNodeGetVar":
                raw = str(getattr(node, "gk_var_name", "value") or "value")
                # ── 函数参数不能按成员变量声明(用户 2026-10-05)──────────
                # 函数体里『变量_读取(参数名)』读到的是**形参**。这里如果不
                # 跳过,脚本顶部会多一个同名成员变量,函数体里读到的就变成
                # 那个成员(而不是参数)—— 而且 Godot 还会报
                # SHADOWED_VARIABLE(变量遮蔽)。
                if snake_case(raw, "v") in params \
                        and snake_case(raw, "v") not in self.ctx.vars:
                    continue
                name = self.ctx.declare_var(raw)
                self.ctx.read_vars.add(name)

    def _declared_param_names(self):
        """这张图里所有函数声明用到的参数名(集合)。

        `_prepass` 靠它把"函数体里的『变量_读取』"和"真正的成员变量"分开。
        只在树里读一次,避免每遍历一个节点都去解析一遍声明。
        """
        cached = getattr(self, "_param_names_cache", None)
        if cached is not None:
            return cached
        names = set()
        try:
            from .. import properties as props
            tree = self._tree()
            for spec in (props.tree_functions(tree).values() if tree else ()):
                for param_name, _kind in (spec.get("params") or ()):
                    names.add(snake_case(param_name, "v"))
        except Exception:                                     # noqa: BLE001
            pass
        self._param_names_cache = names
        return names

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
        for target, entry in _next_nodes(node, "执行"):
            _emit_body(self.ctx, target, indent, entry=entry)

    # ---- 主界面模式(第一百一十二轮)----
    def _emit_ui_main_sections(self):
        """主界面模式的语句:`_ready` = 建控件 + 界面自带行为 + 一对接口。

        `show_main(on)` 是玩法图『界面_主界面』节点调的那面接口
        (显示 / 收起整层);`main_shown()` 给"现在画着没"的查询。
        界面的行为(按钮回调 / Esc 暂停…)在 `_emit_design_into` 里
        按"设计图的顶层语句"发射 —— 和『界面_套用设计』走同一条路,
        区别只是这份在 autoload 里跑,全局生效。
        """
        self.ctx.need_ui_helpers = True
        lines = self.ctx.new_section()
        _ui_root(self.ctx, 1)
        _emit_design_into(self.ctx, self.tree, 1)
        # ── 压暗底:调用写在**设计图遍历之后**(第一百二十六轮)──────────
        # ⚠ 顺序踩过坑,别挪回前面去:这块底只在"设计图里有『界面_暂停游戏』
        #   且填了菜单控件"时才需要,而那个标志(need_pause_dim)是**遍历设计图
        #   时**才置上的 —— 写在前面的条件判断拿到的永远是 False,函数定义
        #   却照生成(生成到 _gk_apply_pause 时标志已经是 True),真机表现是
        #   `_gk_pause_dim` 永远 null、压暗底一块都看不见(探针 P1_DIM 抓到的)。
        # 放在后面就与控件顺序无关了:`_gk_pause_dim_init()` 自己用
        # `move_child(..., 0)` 把它提到根部第一个孩子 = 同层最底,菜单由
        # `_gk_ui_raise()` 挪到最上层,正好"先压暗、再画面板"。
        self.ctx.declare_var("_gk_pause_dim", "ColorRect")
        lines.add(1, "_gk_pause_dim_init()")
        return [
            {
                "signature": "func _ready() -> void:",
                "lines": list(lines.lines),
                "empty_body": "    pass",
            },
            {
                "signature": "func show_main(on: bool = true) -> void:",
                "lines": [
                    "    # 玩法图『界面_主界面』调的就是它(显示 / 收起整层)",
                    "    if _gk_ui_layer != null:",
                    "        _gk_ui_layer.visible = on",
                ],
                "empty_body": "    pass",
                "separator": True,
            },
            {
                "signature": "func main_shown() -> bool:",
                "lines": [
                    "    return _gk_ui_layer != null and _gk_ui_layer.visible",
                ],
                "empty_body": "    pass",
                "separator": True,
            },
        ]

    # ---- 暂停时的压暗底(第一百二十六轮)----
    def _emit_pause_dim(self):
        """主界面里那块全屏压暗底:暂停菜单弹出来时把游戏画面压暗一层。

        ── 为什么是 `ColorRect` + 半透明黑,不是模糊 ────────────────────
        用户原话:"在如ESC菜单出现时就立刻隐藏旧的界面,**或者添加模糊背景**"。
        Godot 4 里"模糊背景"要 `BackBufferCopy` + 屏幕纹理着色器(采样
        `hint_screen_texture` 再做多次高斯),代价是每帧一次全屏采样 ——
        而这块底**只在暂停时可见**,本质是"把后面的东西压下去",半透明黑
        已经达成目的,也**不会和『屏幕_滤镜』(layer 90)/『屏幕_暗角』
        (layer 92)抢同一张后备缓冲**。所以这里取轻的那条路:一个 ColorRect。
        以后真要做磨砂玻璃,替换这一个节点的材质即可,接口不用动。

        ── 为什么"作为根的第一个孩子"= 在最底层 ────────────────────────
        同一个 CanvasLayer 里的 Control 按**兄弟顺序**画(后加的画在上面),
        所以压暗底必须是 `_gk_ui_root` 的第一个孩子;暂停菜单由
        `_gk_ui_raise()` 的 `move_to_front()` 挪到最后一个孩子,永远压在
        这块底上面 —— "先压暗、再画面板",不会反过来把面板也压暗。
        """
        self.ctx.declare_var("_gk_pause_dim", "ColorRect")
        return [{
            "signature": "func _gk_pause_dim_init() -> void:",
            "lines": [
                "    if _gk_pause_dim != null or _gk_ui_root == null:",
                "        return",
                "    # 半透明黑 = 压暗背景(不是全遮:玩家还要看得见自己停在哪)",
                "    _gk_pause_dim = ColorRect.new()",
                '    _gk_pause_dim.name = "暂停压暗"',
                "    _gk_pause_dim.color = Color(0.0, 0.0, 0.0, 0.45)",
                "    _gk_pause_dim.set_anchors_preset(Control.PRESET_FULL_RECT)",
                "    # 不吃鼠标:菜单照点,也不会挡住游戏里的点击判定",
                "    _gk_pause_dim.mouse_filter = Control.MOUSE_FILTER_IGNORE",
                "    _gk_pause_dim.visible = false",
                "    _gk_ui_root.add_child(_gk_pause_dim)",
                "    # 提到根的第一个孩子 = 同层最底(见函数说明):菜单等会儿还会",
                "    # 被 move_to_front 挪到最后,正好压在它上面",
                "    _gk_ui_root.move_child(_gk_pause_dim, 0)",
            ],
            "empty_body": "    pass",
            "separator": True,
        }]

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

    def _append_block(self, sections, func_name, signature, lines,
                      at_front=False):
        """把我们生成的代码块并进已有的同名函数,没有才新建一段。

        `at_front=True` 插到函数体**最前面**。给"必须在任何 await 之前跑完"
        的代码用 —— 触发器的信号连接就是这种:

        ── 踩过的坑(第七十轮)──────────────────────────────────────
        『每当_准备』后面接一个『等待_秒』,生成的是 `_ready()` 里的
        `await get_tree().create_timer(4.0).timeout`。连接语句原来排在链子
        **后面**,于是要等 4 秒才 connect —— 那几秒里走过触发区**一点反应
        都没有**(示例里表现为"纸条拾不起来,计数一直是 0")。连接是
        "进了场就该挂上"的东西,不能排在用户的等待后面。
        """
        target = self._section_for(sections, func_name)
        if target is None:
            sections.append({
                "signature": signature,
                "lines": list(lines),
                "empty_body": "    pass",
                "separator": True,
            })
        elif at_front:
            target["lines"][0:0] = list(lines)
        else:
            target["lines"].extend(lines)

    def _emit_god_guard_helper(self, sections):
        """上帝摄像机开着时,角色控制要让位的守卫(用户 2026-10-04)。

        ── 为什么要这个 ────────────────────────────────────────────────
        上帝机现在由**环境脚本**生成(见 asset_export.ENVIRONMENT_SCRIPT),
        角色的脚本跟它不在一个文件里 —— 但它还是能判断"现在是不是上帝视角":
        环境脚本造的那台相机进了 `gk_god_camera` 组,而且切换时会把视口
        当前相机换掉。所以只要看"当前相机是不是那一台"就够了。

        守卫本身在 statements / expressions 里加(移动 / 跳跃 / 鼠标位移),
        理由:用户报"上帝视角还会影响角色移动" —— 飞相机时 WASD 只该动相机。
        """
        if getattr(self, "_god_guard_emitted", False):
            return
        self._god_guard_emitted = True
        self._append_block(sections, "_gk_god_camera_active",
                           "func _gk_god_camera_active() -> bool:",
                           [
                               "    # 视口当前相机是上帝摄像机(环境脚本造的,进了 gk_god_camera 组)",
                               "    var cam := get_viewport().get_camera_3d()",
                               "    return cam != null and cam.is_in_group(\"gk_god_camera\")",
                           ])

    def _emit_body_half_up_helper(self, sections):
        """"这个角色的碰撞体从原点往上有多高" —— 第一人称枢轴要用它夹一次。

        ── 为什么需要(第 129 轮:用户"第一人称还会触发穿模（幅度变小了）")──
        真机量到的链条(第三关方块顶跳,113 帧):
            第一人称枢轴 = 玩家原点 + **1.55**,而胶囊 `r=0.5 h=1.8`
            沿 y 只到 **+0.9** -> 眼睛恒定在**头顶上方 0.65 米**:
                相机高出胶囊顶 = **+0.6500 恒定**
                比 near(0.1) 更近 = **5 帧**,最坏 **0.0066 米**
                相机在实心体里的帧 = **10 帧**(撞天花板)
            而玩家胶囊中心到最近表面始终 0.9~1.47 —— **身体在外面,眼睛伸出去**。

        为什么 `1.55` 会错:`1.55` 是**按"原点在脚底"**写的(1.8 米的人,
        眼睛离地 1.55 是标准写法)。这个角色的原点在**身体中心**,同一串
        数字就把眼睛顶到离地 2.45 米 —— 水平方向还有胶囊拦着(相机只在
        轴线外 0.25 米,胶囊半径 0.5),**竖直方向完全裸奔**。

        这里返回"碰撞形状**上沿**在角色本地上方多少"(沿角色自己的竖直轴):
          * 原点在**中心** -> 约 0.9  -> 1.55 被夹回身体里;
          * 原点在**脚底** -> 约 1.8  -> 1.55 合法,不动。
        两种原点约定**同一个属性值都对**,用户不用猜该填多少。

        ⚠ 返回 0.0 = "量不到" —— 调用方**不夹**(保持老行为)。
          拿不到形状就乱夹会把老工程改坏。
        ⚠ 结果缓存在成员变量里:碰撞形状运行时不会变形,每帧扫一遍白费性能。
        """
        if getattr(self, "_half_up_emitted", False):
            return
        self._half_up_emitted = True
        self._append_block(sections, "_gk_body_half_up",
                           "func _gk_body_half_up(body: Node3D) -> float:",
                           [
                               "    # 量不到就返回 0.0(调用方据此跳过夹取,保持老行为)",
                               "    if body == null:",
                               "        return 0.0",
                               "    if _gk_half_up_ready:",
                               "        return _gk_half_up_cache",
                               "    _gk_half_up_ready = true",
                               "    var best := 0.0",
                               "    for found in body.find_children(\"*\", \"CollisionShape3D\", true, false):",
                               "        var cs := found as CollisionShape3D",
                               "        if cs == null or cs.shape == null:",
                               "            continue",
                               "        # 形状在**角色本地**的竖直范围:把形状中心换算成角色局部坐标",
                               "        var centre: Vector3 = body.to_local(cs.global_position)",
                               "        var half := 0.0",
                               "        var span := 0.0",
                               "        if cs.shape is CapsuleShape3D:",
                               "            span = (cs.shape as CapsuleShape3D).height",
                               "            half = span * 0.5",
                               "        elif cs.shape is BoxShape3D:",
                               "            span = (cs.shape as BoxShape3D).size.y",
                               "            half = span * 0.5",
                               "        elif cs.shape is SphereShape3D:",
                               "            span = (cs.shape as SphereShape3D).radius * 2.0",
                               "            half = span * 0.5",
                               "        elif cs.shape is CylinderShape3D:",
                               "            span = (cs.shape as CylinderShape3D).height",
                               "            half = span * 0.5",
                               "        else:",
                               "            continue",
                               "        # ⚠ 用**形状自己的**竖直轴:节点可能被转了 90°",
                               "        #   (躺着的碰撞盒)。投影到形状本地 y 上才算对。",
                               "        var up: Vector3 = cs.global_transform.basis.y",
                               "        if up.length_squared() < 1e-8:",
                               "            up = Vector3.UP",
                               "        var reach := absf(up.normalized().dot(body.global_transform.basis.y))",
                               "        if reach < 0.05:",
                               "            reach = 0.05          # 形状几乎横躺:至少算它自己的厚度",
                               "        var top: float = centre.y + half * reach",
                               "        best = maxf(best, top)",
                               "    _gk_half_up_cache = best",
                               "    return best",
                           ])

    def _emit_nav_helpers(self, sections):
        """『移动_寻路』避障 + 『移动_重烤导航』的运行期帮助(第三十六轮)。

        ── 避障为什么用 NavigationAgent(真 Godot 实测过)──────────────
        避障是 RVO(一群 agent 互相绕开),手写不出来 —— 交给引擎:
        每帧把**期望速度** `set_velocity()` 交上去,它在物理步里和邻居
        一起算,算完通过 `velocity_computed` 信号把**安全速度**送回来。
        实测:3 个敌人同追一点,各自侧移错开、最小间距 0.8 米、全部到达;
        不避障时互相顶着走,挤成一团。

        ── 三个坑(都实测过,别改回去)────────────────────────────────
        1. agent 节点**运行时现造**(幂等复用):导出时不知道谁会用,
           而且角色网格藏在 glb 实例里、塞不进自定义子节点;
        2. `avoidance_enabled` 默认是 **false** —— 不显式打开的话
           `set_velocity` 什么都不做、回调永远不来,角色一动不动(实测);
        3. 不显式 `set_navigation_map()` 时 agent 可能一直不工作
           (实测:手动 map_get_path 正常、agent 原地不动;显式设过
           map 之后一切正常)。这里每帧幂等对齐,贵不了多少。
        """
        if getattr(self, "_nav_helpers_emitted", False):
            return
        self._nav_helpers_emitted = True
        if getattr(self.ctx, "need_nav_avoid", False):
            self._append_block(sections, "_gk_nav_move",
                               "func _gk_nav_move(target: CharacterBody3D, "
                               "desired: Vector3, goal: Vector3, radius: float, "
                               "speed: float) -> void:",
                               [
                                   "    # 期望速度交给 agent;真正移动在 _gk_nav_agent 连的回调里",
                                   "    _gk_nav_agent(target, goal, radius, speed).set_velocity(desired)",
                               ])
            self._append_block(sections, "_gk_nav_agent",
                               "func _gk_nav_agent(target: CharacterBody3D, "
                               "goal: Vector3, radius: float, "
                               "speed: float) -> NavigationAgent3D:",
                               [
                                   "    var agent := target.get_node_or_null(\"GKNavAgent\") as NavigationAgent3D",
                                   "    if agent == null:",
                                   "        agent = NavigationAgent3D.new()",
                                   "        agent.name = \"GKNavAgent\"",
                                   "        target.add_child(agent)",
                                   "        # 回调只接一次:避障算完把安全速度赋值给角色并移动",
                                   "        agent.velocity_computed.connect(func(safe: Vector3) -> void:",
                                   "            target.velocity = safe",
                                   "            target.move_and_slide())",
                                   "    # 避障默认是关的 —— 不开的话 set_velocity 什么都不做(实测)",
                                   "    agent.avoidance_enabled = true",
                                   "    agent.radius = radius",
                                   "    agent.max_speed = maxf(speed, 0.1)",
                                   "    # ⚠ target_position 必须设,哪怕路径是我们自己算的:",
                                   "    #    不设的时候避障**不工作** —— 回调每帧都来、但安全速度",
                                   "    #    恒为 (0,0,0)(服务器认为这个 agent 没有任务)。",
                                   "    #    实测:同一个场景只加这一行,3 个 agent 立刻互相绕开。",
                                   "    agent.target_position = goal",
                                   "    # agent 的导航地图必须显式对齐 —— 不设时它可能一直不工作(实测)",
                                   "    var map := target.get_world_3d().navigation_map",
                                   "    if agent.get_navigation_map() != map:",
                                   "        agent.set_navigation_map(map)",
                                   "    return agent",
                               ])
        if getattr(self.ctx, "need_nav_avoid_2d", False):
            self._append_block(sections, "_gk_nav_move2d",
                               "func _gk_nav_move2d(target: CharacterBody2D, "
                               "desired: Vector2, goal: Vector2, radius: float, "
                               "speed: float) -> void:",
                               [
                                   "    _gk_nav_agent2d(target, goal, radius, speed).set_velocity(desired)",
                               ])
            self._append_block(sections, "_gk_nav_agent2d",
                               "func _gk_nav_agent2d(target: CharacterBody2D, "
                               "goal: Vector2, radius: float, "
                               "speed: float) -> NavigationAgent2D:",
                               [
                                   "    var agent := target.get_node_or_null(\"GKNavAgent\") as NavigationAgent2D",
                                   "    if agent == null:",
                                   "        agent = NavigationAgent2D.new()",
                                   "        agent.name = \"GKNavAgent\"",
                                   "        target.add_child(agent)",
                                   "        agent.velocity_computed.connect(func(safe: Vector2) -> void:",
                                   "            target.velocity = safe",
                                   "            target.move_and_slide())",
                                   "    agent.avoidance_enabled = true",
                                   "    agent.radius = radius",
                                   "    agent.max_speed = maxf(speed, 0.1)",
                                   "    # ⚠ 同 3D:不设 target_position 时避障不工作(安全速度恒 0)",
                                   "    agent.target_position = goal",
                                   "    var map := target.get_world_2d().navigation_map",
                                   "    if agent.get_navigation_map() != map:",
                                   "        agent.set_navigation_map(map)",
                                   "    return agent",
                               ])
        if getattr(self.ctx, "need_nav_rebake", False):
            self._append_block(sections, "_gk_rebake_navigation",
                               "func _gk_rebake_navigation() -> void:",
                               [
                                   "    # 真正干活的在环境脚本(3D 按静态碰撞体烤 / 2D 先算外框)。",
                                   "    # ⚠ 环境脚本挂在**场景根**下面,本脚本可能挂得更深",
                                   "    #    (比如 Colliders/小玩家);整个场景还可能被嵌进别的",
                                   "    #    场景里(探针就是这样,多了一层) —— 直接往上走到顶",
                                   "    #    再找 \"Environment\" 会走过头(实测:重烤静默跳过)。",
                                   "    #    所以**沿祖先链逐层找**,谁先有谁算数;最后兜底全树搜。",
                                   "    var env: Node = null",
                                   "    var node: Node = self",
                                   "    while node != null:",
                                   "        env = node.get_node_or_null(\"Environment\")",
                                   "        if env != null:",
                                   "            break",
                                   "        node = node.get_parent()",
                                   "    if env == null:",
                                   "        for found in get_tree().root.find_children(\"Environment\", \"Node\", true, false):",
                                   "            if found.has_method(\"rebake_navigation\"):",
                                   "                env = found as Node",
                                   "                break",
                                   "    if env != null and env.has_method(\"rebake_navigation\"):",
                                   "        env.rebake_navigation()",
                                   "    else:",
                                   "        push_warning(\"[GK] 没找到环境脚本(Environment)—— 导航重烤跳过\")",
                               ])

    def _emit_camera2d_fallback(self, sections):
        """2D 兜底相机:框住所有 2D 物体的 Camera2D(第四十六轮)。

        ── 为什么必须兜(用户实测)────────────────────────────────────
        2D 场景里没有相机时,Godot 停在**原点视图** —— 关卡摆在负坐标区
        就"运行后一片空"。3D 那边早有兜底(自由飞行观察机),2D 这边一直缺。

        幂等:已经有**当前**的 Camera2D 就直接返回(用户自己放了相机时别抢)。
        """
        if getattr(self, "_camera2d_emitted", False):
            return
        self._camera2d_emitted = True
        ready = self._section_for(sections, "_ready")
        if ready is None:
            ready = {"signature": "func _ready() -> void:",
                     "lines": [], "empty_body": "    pass", "separator": True}
            sections.append(ready)
        # 放在 _ready 最前面:相机先就位,后面那些节点才谈得上"看得见"
        ready["lines"].insert(0, "    _gk_ensure_camera2d(self)")
        self._append_block(
            sections, "_gk_ensure_camera2d",
            "func _gk_ensure_camera2d(host: Node) -> void:",
            [
                "    # 已经有当前 Camera2D 就不动(用户自己放了相机时别抢)",
                "    if host.get_tree() == null:",
                "        return",
                "    for found in host.get_tree().root.find_children(\"*\", \"Camera2D\", true, false):",
                "        if found is Camera2D and (found as Camera2D).is_current():",
                "            return",
                "    # 框住所有 2D 物体:取它们的全局位置算包围盒",
                "    var min_point := Vector2(INF, INF)",
                "    var max_point := Vector2(-INF, -INF)",
                "    var count := 0",
                "    for node in host.get_tree().root.find_children(\"*\", \"Node2D\", true, false):",
                "        if node is Camera2D:",
                "            continue",
                "        var point := (node as Node2D).global_position",
                "        min_point = min_point.min(point)",
                "        max_point = max_point.max(point)",
                "        count += 1",
                "    if count == 0:",
                "        return",
                "    var cam := Camera2D.new()",
                "    cam.name = \"GK兜底相机2D\"",
                "    # ⚠ 还没进树:用 position 摆位(global_position 要有父级才算得准)",
                "    cam.position = (min_point + max_point) * 0.5",
                "    # 跨距留 4 米边;缩放 = 视口尺寸 / 跨距,内容全进画面",
                "    var span := (max_point - min_point) + Vector2(4.0, 4.0)",
                "    var view := Vector2(host.get_viewport().get_visible_rect().size)",
                "    if span.x > 0.01 and span.y > 0.01:",
                "        cam.zoom = (view / span).clamp(Vector2(0.05, 0.05), Vector2(8.0, 8.0))",
                "    # ⚠ 必须**延后**挂到 root:_ready 里 root 还在装配子节点,",
                "    #   当场 add_child 会报 \"Parent node is busy setting up children\"",
                "    #   并且失败 —— 相机没进树,make_current 又跟着报",
                "    #   \"!enabled || !is_inside_tree()\"(实测踩过,就在这个示例里)",
                "    host.get_tree().root.add_child.call_deferred(cam)",
                "    cam.make_current.call_deferred()",
                "    push_warning(\"[GK] 2D 场景里没有相机 —— 造了一台兜底相机框住全部物体;想自己控镜头就在 Blender 里放一台相机\")",
            ])

    def _emit_fly_camera(self, sections):
        """生成"兜底观察机":场景里**一台相机都没有**时现造一台自由飞行相机。

        ── 上帝摄像机不在这里了(用户 2026-10-04 第二轮)────────────────
        它原来跟兜底机共用这段 —— 于是变成角色脚本的一部分:场景里没有
        角色就没有脚本,上帝机也就不存在(用户实测:"在没有角色的测试里
        动不了、鼠标也转不了视角")。现在上帝机由环境脚本生成(每张 .tscn
        都有一份),角色脚本这边只留 `_gk_god_camera_active()` 守卫。

        兜底机仍然留在这儿:它是『相机_取状态』这类**图节点**的兜底,
        本来就只有"图在跑"才有意义 —— 图在跑就说明角色脚本在。
        """
        if getattr(self, "_fly_camera_emitted", False):
            return
        self._fly_camera_emitted = True
        ctx = self.ctx
        # 两个成员变量必须先声明 —— 函数体要用它们,不声明就是
        # `Parse Error: Identifier "_gk_fly_cam" not declared`,
        # 整个脚本加载失败、所有输入都没反应(实测踩过)。
        ctx.declare_var("_gk_fly_cam", "Node")
        ctx.declare_var("_gk_fly_speed", "float")
        # ── 鼠标位移**自己攒一份**(踩过的坑,用户 2026-10-04)──────────
        # 原来复用 `_gk_mouse_delta`(游戏逻辑那份):它在物理帧末尾清零,
        # 而"清零"在同帧里排在"飞行相机读它"**前面**(图语句先跑、钩子后跑)
        # —— 于是永远读到零,鼠标转不了视角。自己攒一份,读写同处,不受顺序影响。
        look_var = ctx.declare_var("_gk_fly_look", "Vector2")
        ctx.register_input_handler(
            "_gk_fly_mouse_event", reset=False,
            # 暂停期间**照跑**(during_pause=True):这台兜底观察机属于
            # 上帝机那一族的"调试 / 观察"机位 —— 它的设计目的就是
            # "停住画面绕着看细节"(见 asset_export 里时停那段说明,
            # 环境脚本的上帝机按 T 冻住世界后还要能转视角)。跟着一起停掉
            # 就等于"冻住之后连看都看不了"。它的消费端 `_gk_fly_update`
            # 自带"不在当前那台就丢弃"的守卫,多攒一点不会串到玩家视角上。
            during_pause=True,
            body=[
                "    if event is InputEventMouseMotion:",
                f"        {look_var} += event.relative",
            ])

        cam_name = "GK观察相机(自由飞行)"
        warning = ("[GK] 场景里没有相机 —— 造了一台 %s。"
                   "WASD 平移 / 空格上升 / Shift 下降 / 鼠标转视角(不受碰撞)。"
                   "想用固定机位:放一台相机,并把它设为视口当前相机(小键盘 0)"
                   % cam_name)

        # 造机:幂等(已经有就复用),兜底要立刻接管画面。
        self._append_block(sections, "_gk_ensure_fly_camera",
                           "func _gk_ensure_fly_camera(host: Node, make_current: bool) -> Node:",
                           [
                               "    # 已经造过就复用(每次取相机都造一台的话,场景里会越堆越多)",
                               "    if _gk_fly_cam != null and is_instance_valid(_gk_fly_cam):",
                               "        if make_current:",
                               "            (_gk_fly_cam as Camera3D).make_current()",
                               "        return _gk_fly_cam",
                               "    if host.get_tree() == null:",
                               "        return null",
                               "    var cam := Camera3D.new()",
                               f'    cam.name = "{cam_name}"',
                               "    host.get_tree().root.add_child(cam)",
                               "    if host is Node3D:",
                               "        var back := (host as Node3D).global_transform.basis.z",
                               "        cam.global_position = (host as Node3D).global_position + back * 6.0 + Vector3(0.0, 3.0, 0.0)",
                               "        cam.look_at((host as Node3D).global_position, Vector3.UP)",
                               "    if make_current:",
                               "        cam.make_current()",
                               "    _gk_fly_cam = cam",
                               "    _gk_fly_speed = 8.0",
                               "    return cam",
                           ])
        # 兜底造机 + 警告:只有『相机_取状态』那类节点会用到
        if getattr(ctx, "need_camera_query_helper", False):
            self._append_block(sections, "_gk_make_fallback_camera",
                               "func _gk_make_fallback_camera(host: Node) -> void:",
                               [
                                   "    # 场景里一台相机都没有 -> 造一台自由飞行观察机",
                                   "    _gk_ensure_fly_camera(host, true)",
                                   "    push_warning(" + gd_string(warning) + ")",
                               ])
        # 飞行控制:每帧一次(挂在角色脚本上,不用额外节点)。
        # WASD 平移 / 空格上升 / Shift 下降 / 鼠标转视角,**直接改坐标、
        # 不过物理**,想穿墙就穿墙。
        fly_lines = [
            "    # 先取走-清零(见 _gk_fly_look 的说明):不在当前那台也不攒着,",
            "    # 否则接管画面后的第一帧视角会先跳一下",
            f"    var look := {look_var}",
            f"    {look_var} = Vector2.ZERO",
            "    if _gk_fly_cam == null or not is_instance_valid(_gk_fly_cam):",
            "        return",
            "    if not (_gk_fly_cam as Camera3D).is_current():",
            "        return",
            "    # 鼠标转视角(自己累积的那份位移,和玩家视角互不抢)",
            "    if look != Vector2.ZERO:",
            "        var rot := (_gk_fly_cam as Node3D).rotation_degrees",
            "        rot.y -= look.x * 0.25",
            "        rot.x = clampf(rot.x - look.y * 0.25, -89.0, 89.0)",
            "        (_gk_fly_cam as Node3D).rotation_degrees = rot",
            "    # WASD 平移 + 空格/Shift 升降(**不走碰撞**)",
            "    var fly := Vector3.ZERO",
            "    # ⚠ 别叫 `basis` —— Node3D 有同名属性,Godot 会报 shadowing",
            "    var _gk_basis := (_gk_fly_cam as Node3D).global_transform.basis",
            "    if Input.is_key_pressed(KEY_W):",
            "        fly -= _gk_basis.z",
            "    if Input.is_key_pressed(KEY_S):",
            "        fly += _gk_basis.z",
            "    if Input.is_key_pressed(KEY_A):",
            "        fly -= _gk_basis.x",
            "    if Input.is_key_pressed(KEY_D):",
            "        fly += _gk_basis.x",
            "    if Input.is_key_pressed(KEY_SPACE):",
            "        fly += Vector3.UP",
            "    if Input.is_key_pressed(KEY_SHIFT):",
            "        fly -= Vector3.UP",
            "    if fly != Vector3.ZERO:",
            "        (_gk_fly_cam as Node3D).global_position += fly.normalized() * _gk_fly_speed * delta",
        ]
        self._append_block(sections, "_gk_fly_update",
                           "func _gk_fly_update(delta: float) -> void:", fly_lines)

        ctx.physics_hooks.setdefault("fly_camera", []).append(
            "_gk_fly_update(delta)")

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

        # 屏幕暗角同理(第五十一轮):自己的着色器常量,不动滤镜那一份
        if ctx.need_screen_vignette and _GD_VIGNETTE_SHADER not in out:
            out.append("# ---- 暗角着色器(由『屏幕_暗角』节点使用)----")
            out.append(f'const {_VIGNETTE_SHADER_CONST} := """{_GD_VIGNETTE_SHADER}"""')
            out.append("")

        # 存档校验用的盐(第九十三轮):只有勾了『防改档』才生成 ——
        # 没勾的老图一个字都不多。⚠ 它是公开常量,挡的是"顺手改记事本",
        # 不是防破解(客户端存档的本质限制,README / 节点手册里写明)。
        if ctx.need_save_verify:
            out.append("# ---- 存档校验用的盐(『存档_保存』勾了『防改档』时使用)----")
            out.append(f"const {_SAVE_SALT_CONST} := {gd_string(_SAVE_SALT)}")
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
                # 引用要**按类型找** —— 同名节点不止一个(网格 + 碰撞代理),
                # 见下面 _gk_ref_typed 的说明。
                out.append(f"@onready var {var}: {gd_type} = "
                           f"_gk_ref_typed(self, {gd_string(name)}, "
                           f"{gd_string(gd_type)})")
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
