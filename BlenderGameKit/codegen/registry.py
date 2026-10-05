"""节点类型 -> 发射器的注册表与派发。

`expr_node` / `multi_output` / `stmt_node` 三个装饰器把发射器登记进 `EXPR_HANDLERS` / `MULTI_OUTPUT_FUNCS` / `STMT_HANDLERS`;`expression_for_output` / `_emit_body` / `_chain` 负责按注册表派发。

注册表必须**单一实例**——各发射器模块都 `from .registry import ...`,共享同一份 dict,否则登记会分散到不同的表里。"""



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


STMT_HANDLERS = {}


def stmt_node(*bl_idnames):
    def wrapper(func):
        for bl_idname in bl_idnames:
            STMT_HANDLERS[bl_idname] = func
        return func
    return wrapper


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


def _emit_body(ctx, node, indent, output="完成", entry="执行"):
    """沿某个输出执行流继续走。

    entry = 这次是从目标节点的**哪个输入引脚**进来的(『执行』/『重播』…)。
    """
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
    # Blender 的输入引脚只允许一条连线，所以**同一个入口**不可能真的被两个
    # 事件驱动。这里以"先到先得"为准并明确告警，而不是默默丢掉一段逻辑。
    #
    # ── 按「节点 + 入口引脚」记账(用户 2026-10-05)────────────────────
    # 以前一个节点只能被**一个**事件引用。但『定时_时间轴』有两个执行入口
    # (『执行』/『重播』):从两个口各接一条链路是**故意的**,不能按"抢"
    # 处理,否则第二个入口整段被跳过(『重播』永远不生效)。
    owners = ctx._claimed.setdefault(node, {})
    owner = owners.get(entry)
    if owner is not None and owner != ctx._current_event:
        ctx.warn(
            f"节点『{node.bl_label}』的『{entry}』同时被『{owner}』与"
            f"『{ctx._current_event}』引用，但一个输入引脚只能接一条线。"
            f"已只保留『{owner}』这一条;"
            f"如果两个事件都要执行这段逻辑，请复制一份节点。"
        )
        return
    owners[entry] = ctx._current_event
    ctx._active.add(node)
    try:
        ctx.emit_statement(node, indent, output)
    finally:
        ctx._active.discard(node)


def _next_nodes(node, output="完成"):
    """取某个输出引脚连到的所有下游节点(按 x 排序，保证结果稳定)。

    返回 `[(下游节点, 入口引脚名)]` —— **入口名也要带**(用户 2026-10-05):
    『定时_时间轴』这类节点有两个执行入口(『执行』/『重播』),生成时得知道
    这次是从哪个口进来的。以前只返回节点,于是"从哪个口进来"这个信息
    在遍历里丢掉,第二个入口会被当成"同一个节点被两处抢"而整段跳过。
    """
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
            entry = getattr(getattr(link, "to_socket", None), "name", "")
            result.append((target, entry or "执行"))
    result.sort(key=lambda item: (getattr(item[0], "location", (0.0, 0.0))[1],
                                  getattr(item[0], "location", (0.0, 0.0))[0]))
    return result


def _chain(ctx, node, indent, output="完成"):
    """node 的某个输出接到的东西全部按顺序执行。"""
    for target, entry in _next_nodes(node, output):
        _emit_body(ctx, target, indent, entry=entry)
