"""`Generator` 主体:扫描节点树、组装事件函数、拼出最终 GDScript。"""

from ..nodes.node_base import (TARGET_SELF, gd_node_path, gd_string,
                               safe_function_name, snake_case)
from ..nodes.nodes import (ANIM_CATEGORY_NAMES, action_category, node_key,
                           parse_transitions, transition_pin_name)

from .literals import (EVENT_KINDS, TRIGGER_KINDS, _BANNER, gd_class_name,
                       gd_number)
from .registry import _chain, _emit_body, _next_nodes
from .statements import _emit_logic_ports, _emit_logic_vars
from .animation import _anim_category_table, _emit_state_graph, _emit_state_transitions
from .ai import _emit_ai_graph
from .camera import _path_name
from .services import _FILTER_SHADER_CONST, _GD_FILTER_SHADER
from .context import GenContext




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
            self._append_block(sections, "_ready", "func _ready() -> void:",
                               trigger_ready)

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
        if getattr(self.ctx, "god_camera_enabled", False):
            self._emit_god_guard_helper(sections)
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
                                  machine.get("gate"))
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
            sections.append({
                # 不带 host 参数:它从来没被用过,而 Godot 会把
                # "参数没用上" 报成 UNUSED_PARAMETER(调试器里一条错误)。
                "signature": "func _gk_find_widget(widget_name: String) -> Control:",
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

        # ---- 暂停:Esc 监听(第三十七轮)------------------------------
        # 世界暂停之后本脚本的 _input / _physics_process 全都停了 ——
        # 挂在 `get_tree().process_frame` 信号上的回调**不受暂停影响**
        # (信号是直接的方法调用,不走"节点的每帧处理"),这是唯一能在
        # 暂停期间读到 Esc 的活口。真 Godot 探针验过:暂停 -> Esc -> 解冻。
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
            sections.append({
                "signature": "func _gk_apply_pause(paused: bool, menu: String, show_mouse: bool) -> void:",
                "lines": [
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
                    "    if menu != \"\":",
                    "        var widget := _gk_find_widget(menu)",
                    "        if widget != null:",
                    "            widget.visible = paused",
                    "        else:",
                    "            push_warning(\"找不到暂停菜单控件: \" + menu)",
                    "    print(\"[GK] 暂停: \", \"开\" if paused else \"关\")",
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
                    "        _gk_dialog_panel.mouse_filter = Control.MOUSE_FILTER_IGNORE",
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
                    "    if speed > 0.0 and count > 0:",
                    "        _gk_dialog_tween = _gk_dialog_panel.create_tween()",
                    "        _gk_dialog_tween.tween_property(_gk_dialog_text, \"visible_characters\", count, float(count) / speed)",
                    "    else:",
                    "        _gk_dialog_text.visible_characters = -1",
                    "    _gk_dialog_panel.visible = true",
                    "    _gk_dialog_clicked = false",
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
        params = self._declared_param_names()
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
