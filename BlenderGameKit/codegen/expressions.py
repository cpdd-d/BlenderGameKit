"""非动画的表达式发射器(数学/比较/逻辑/向量/文本/转换/输入/骨骼)。

每个函数实现一个表达式节点的求值,由 `@expr_node` / `@multi_output` 登记;真正的遍历与派发在 `registry.py`。"""

from ..nodes.node_base import (TARGET_SELF, gd_node_path, gd_string,
                               safe_function_name, snake_case)
from ..nodes import nodes as node_lib

from .literals import _coerce_text_to_literal, _gd_vector_ctor, gd_number, gd_vector
from .registry import expr_node, multi_output, stmt_node
from .camera import _camera_node_expr




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
            ctx.warn(f"『{getattr(node, 'bl_label', '逻辑_布尔运算')}』"
                     f"的输入『{name}』没接线,按假(false)算,"
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


def ensure_position_helper(ctx, prop="global_position"):
    """确保"按世界/局部坐标读位置"的辅助函数已经生成,返回**它的名字**。

    ── 为什么抽成模块级函数 ────────────────────────────────────────
    『取值_位置』(读 X/Y/Z)和『移动_朝向目标』(算"目标 - 自己")都要
    读位置,两边都必须走同一个 helper:
      * 位置是个 Vector,拆成三个输出时**只算一次**(不然生成出 500 字符
        一行的怪物);
      * `host: Node` 的签名让它对 self / 普通 @onready 引用都安全 ——
        直接写 `.global_position` 会在类型是 Node 时报
        "Cannot find member global_position in base Node"。

    同一份 helper 只生成一次(按 维度 + 属性名 去重)。
    """
    made = getattr(ctx, "_pos_helpers", None)
    if made is None:
        made = set()
        ctx._pos_helpers = made
    key = f"{prop}_{'2d' if ctx.dim == 2 else '3d'}"
    if key not in made:
        made.add(key)
        if ctx.dim == 2:
            body = [
                "    if host is Node2D:",
                f"        return (host as Node2D).{prop}",
                "    # ⚠ 兜底(第三十六轮,真 Godot 实测):2D 场景里引用可能命中",
                "    #    glb 内部的 3D 网格(名字和物体一样、排在树前面)—— 它不",
                "    #    是 Node2D,但它的世界位置就是那个物体在 Blender 里摆的位置,",
                "    #    取 (x,y) 总比返回 (0,0) 把角色引到原点去强。",
                "    if host is Node3D:",
                f"        var _p := (host as Node3D).{prop}",
                "        return Vector2(_p.x, _p.y)",
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
    return f"_gk_{key}"


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
    call = f"{ensure_position_helper(ctx, prop)}({target})"
    return {
        "X": f"({call}).x",
        "Y": f"({call}).y",
        "Z": f"({call}).z" if ctx.dim != 2 else "0.0",
    }


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


_NUM_TEXT_HELPER = "_gk_num_text"


def ensure_num_text_helper(ctx):
    """确保 `_gk_num_text` 只生成一次,返回它的名字。

    『文本_数字』和『界面_飘字』都用它 —— 两处各生成一份的话 Godot 报
    函数重名(整个脚本加载不了),所以统一从这里拿。
    """
    made = getattr(ctx, "_num_text_made", None)
    if made is None:
        made = set()
        ctx._num_text_made = made
    if _NUM_TEXT_HELPER not in made:
        made.add(_NUM_TEXT_HELPER)
        ctx.extra_sections.append({
            "signature": f"func {_NUM_TEXT_HELPER}(value: float) -> String:",
            "lines": [
                "    # 整数值就显示成 '3'(不是 '3.0');有小数才保留两位",
                "    if is_equal_approx(value, roundf(value)):",
                "        return str(int(roundf(value)))",
                "    return str(snappedf(value, 0.01))",
            ],
            "empty_body": "    pass",
            "separator": True,
        })
    return _NUM_TEXT_HELPER


@expr_node("GKNodeNumberText")
def _expr_number_text(ctx, node):
    """『文本_数字』:数值 -> 给人看的文字(整数不带 .0)。

    ── 为什么必须走 helper ─────────────────────────────────────────
    Godot 里 `str(3.0)` 是 **"3.0"** —— HUD 上直接拼变量就成了
    『血量 2.0』。要判断"它是不是整数"得算一次 round、再比一次近似,
    内联的话**同一个值表达式会写三遍**(接了线的值可能是个长表达式)。
    所以抽一个 `_gk_num_text(value: float) -> String`,只生成一次。
    """
    helper = ensure_num_text_helper(ctx)
    return f"{helper}({ctx.input_number(node, '数值', '0.0')})"


def ensure_flash_helper(ctx):
    """『特效_闪红』的辅助函数(2D / 3D 各一份),返回函数名。

    ── 为什么走 helper(而不是把十几行内联到每个闪红节点)──────────
    图里放 5 个闪红节点就复制 5 份同样的代码;而且"过一会儿恢复"必须
    挂 tween 回调,内联的话那几行缩进很容易生成歪(整个脚本解析失败)。

    ── 3D 为什么是 material_override ──────────────────────────────
    卡通材质是**逐个表面**挂的(`set_surface_override_material`),
    整网格的 `material_override` 空着 —— 正好借它"整体染色":
    设上 = 盖住原来所有槽,恢复原值 = 卡通材质原样回来。
    恢复前会先比对"现在盖的是不是我们那个材质":连闪两次时
    (受击 + 追击同时打中),先到期的那个**不会**把后一次的红提前抹掉。

    ── 2D 为什么是 modulate ───────────────────────────────────────
    2D 没有材质覆写这一层,用 CanvasItem.modulate 染色,记下原色再还回去。
    """
    made = getattr(ctx, "_flash_helpers", None)
    if made is None:
        made = set()
        ctx._flash_helpers = made
    suffix = "2d" if ctx.dim == 2 else "3d"
    name = f"_gk_flash_{suffix}"
    if name not in made:
        made.add(name)
        if ctx.dim == 2:
            lines = [
                "    var items: Array[Node] = []",
                "    if node is CanvasItem:",
                "        items.append(node)",
                '    items.append_array(node.find_children("*", "CanvasItem", true, false))',
                "    if items.is_empty():",
                "        return",
                "    var olds: Array = []",
                "    for item in items:",
                "        olds.append((item as CanvasItem).modulate)",
                "        (item as CanvasItem).modulate = color",
                "    var tween := node.create_tween()",
                "    tween.tween_interval(seconds)",
                "    tween.tween_callback(func() -> void:",
                "        for index in items.size():",
                "            var item: Node = items[index]",
                "            if is_instance_valid(item) and (item as CanvasItem).modulate == color:",
                "                (item as CanvasItem).modulate = olds[index]",
                "    )",
            ]
        else:
            lines = [
                "    var mat := StandardMaterial3D.new()",
                "    mat.albedo_color = color",
                "    mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED",
                "    var meshes: Array[Node] = []",
                "    if node is MeshInstance3D:",
                "        meshes.append(node)",
                '    meshes.append_array(node.find_children("*", "MeshInstance3D", true, false))',
                "    if meshes.is_empty():",
                "        return",
                "    var olds: Array = []",
                "    for item in meshes:",
                "        olds.append((item as MeshInstance3D).material_override)",
                "        (item as MeshInstance3D).material_override = mat",
                "    var tween := node.create_tween()",
                "    tween.tween_interval(seconds)",
                "    tween.tween_callback(func() -> void:",
                "        for index in meshes.size():",
                "            var item: Node = meshes[index]",
                "            if is_instance_valid(item) and (item as MeshInstance3D).material_override == mat:",
                "                (item as MeshInstance3D).material_override = olds[index]",
                "    )",
            ]
        ctx.extra_sections.append({
            "signature": f"func {name}(node: Node, color: Color, "
                         f"seconds: float) -> void:",
            "lines": lines,
            "empty_body": "    pass",
            "separator": True,
        })
    return name


def ensure_float_text_helper(ctx):
    """『界面_飘字』的辅助函数(2D / 3D 各一份),返回函数名。

    ── 生成的东西 ─────────────────────────────────────────────────
    在锚点物体**头顶**造一个一次性标签,向上飘 + 淡出,完了 queue_free。
    3D 用 Label3D(billboard 朝向相机 + 不吃深度测试,任何角度都看得见);
    2D 用 Label(Control,跟着锚点的屏幕位置走,只在出生时取一次位置)。

    ── 为什么挂在场景根上 ─────────────────────────────────────────
    金币这类"飘完字也没了"的锚点会被 `queue_free()` 销毁 —— 字挂在
    锚点下面的话会跟着一起消失(飘不出来)。所以挂 current_scene。
    """
    made = getattr(ctx, "_float_text_helpers", None)
    if made is None:
        made = set()
        ctx._float_text_helpers = made
    suffix = "2d" if ctx.dim == 2 else "3d"
    name = f"_gk_float_text_{suffix}"
    if name not in made:
        made.add(name)
        prelude = [
            "    if anchor == null or not anchor.is_inside_tree():",
            "        return",
            "    var parent: Node = anchor.get_tree().current_scene",
            "    if parent == null:",
            "        parent = anchor.get_parent()",
            "    if parent == null:",
            "        return",
        ]
        if ctx.dim == 2:
            body = [
                "    if not (anchor is Node2D):",
                "        return",
                "    var label := Label.new()",
                "    label.text = text",
                "    label.modulate = color",
                "    label.z_index = 100",
                "    label.add_theme_font_size_override(\"font_size\", 32)",
                "    var start := (anchor as Node2D).global_position + Vector2(-24.0, -56.0)",
                "    parent.add_child(label)",
                "    label.global_position = start",
                "    var tween := label.create_tween()",
                "    tween.set_parallel(true)",
                "    tween.tween_property(label, \"global_position\", start + Vector2(0.0, -rise), seconds)",
                "    tween.tween_property(label, \"modulate:a\", 0.0, seconds)",
                "    tween.chain().tween_callback(label.queue_free)",
            ]
        else:
            body = [
                "    if not (anchor is Node3D):",
                "        return",
                "    var label := Label3D.new()",
                "    label.text = text",
                "    label.modulate = color",
                "    label.billboard = BaseMaterial3D.BILLBOARD_ENABLED",
                "    label.no_depth_test = true",
                "    label.font_size = 64",
                "    label.pixel_size = 0.01",
                "    label.outline_size = 12",
                "    label.outline_modulate = Color(0.12, 0.12, 0.12, 0.9)",
                "    var start := (anchor as Node3D).global_position + Vector3(0.0, 1.4, 0.0)",
                "    parent.add_child(label)",
                "    label.global_position = start",
                "    var tween := label.create_tween()",
                "    tween.set_parallel(true)",
                "    tween.tween_property(label, \"global_position\", start + Vector3(0.0, rise, 0.0), seconds)",
                "    tween.tween_property(label, \"modulate:a\", 0.0, seconds)",
                "    tween.chain().tween_callback(label.queue_free)",
            ]
        ctx.extra_sections.append({
            "signature": f"func {name}(anchor: Node, text: String, "
                         f"color: Color, seconds: float, rise: float) -> void:",
            "lines": prelude + body,
            "empty_body": "    pass",
            "separator": True,
        })
    return name


@expr_node("GKNodeGetVar")
def _expr_get_var(ctx, node):
    """读变量 —— 绑了 getter 就读"计算出来的值"(用户 2026-10-05)。

    ── 什么是变量的"实现"(照 UE5)─────────────────────────────────
    侧栏『变量』框里能给一个变量绑**读取时调用的函数**:图里照样是
    『变量_读取』,生成出来却是 `计算血量()`。这样"读的时候要算一下"
    就不用每个读取点各连一遍同样的节点。

    绑定的函数没声明过时**不静默**:给一条能照着改的告警,并退回读变量本身
    (退回比生成一个不存在的函数名安全 —— 后者 Godot 直接拒绝整个脚本)。
    """
    raw = str(getattr(node, "gk_var_name", "value") or "value")
    impl = _variable_impl(ctx, raw)
    getter = (impl or {}).get("getter") or ""
    if getter:
        if _function_ok(ctx, getter, kind="getter"):
            return f"{safe_function_name(getter)}()"
    name = ctx.declare_var(raw)
    ctx.read_vars.add(name)
    return name


def _variable_impl(ctx, var_name):
    """这个变量绑了什么实现(没绑 / 读不到树 -> None)。"""
    tree = getattr(ctx, "tree", None)
    if tree is None:
        return None
    try:
        from .. import properties as props
        return props.variable_implementations(tree).get(str(var_name))
    except Exception:                                     # noqa: BLE001
        return None


def _function_ok(ctx, name, kind="getter"):
    """校验绑定用的函数真的声明过(没声明就给告警,返回 False)。"""
    tree = getattr(ctx, "tree", None)
    try:
        from .. import properties as props
        spec = props.tree_functions(tree).get(str(name)) if tree else None
    except Exception:                                     # noqa: BLE001
        spec = None
    if spec is None:
        ctx.warn(f"变量绑定的函数『{name}』没有在侧栏『函数』框里声明过 —— "
                 f"这次改回直接读写变量。请先声明这个函数(或把绑定清空)")
        return False
    return True


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


@expr_node("GKNodeUiReadText")
def _expr_ui_read_text(ctx, node):
    """读界面控件上的文字(第三十八轮:输入框里玩家打的字 / 标签 / 按钮)。

    走一个 `_gk_widget_text(名字)` 辅助函数 —— 不能在表达式里声明
    `_gk_ui_<名字>` 变量:那个成员变量由**创建它的节点**用具体类型
    (Label / Button / LineEdit)声明过了,再按 "Control" 声明一遍会撞类型。
    """
    ctx.need_ui_helpers = True
    ctx.need_widget_text_helper = True
    name = (getattr(node, "gk_widget", "") or "").strip()
    if not name:
        ctx.warn(f"『{node.bl_label}』没有填控件名 —— 会读到空字符串")
        return '""'
    return f"_gk_widget_text({gd_string(name)})"


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
    # ── 上帝视角期间不攒位移(用户 2026-10-04)───────────────────────
    # 那时候鼠标是**转上帝机**用的(它自己攒一份);游戏这边再攒一份的话,
    # 切回玩家相机时视角已经被拖偏了,图里的『相机_旋转』也跟着乱转。
    # 见 statements._open_god_guard。
    if getattr(ctx, "god_camera_enabled", False):
        condition = "    if event is InputEventMouseMotion and not _gk_god_camera_active():"
    else:
        condition = "    if event is InputEventMouseMotion:"
    ctx.register_input_handler(
        "_gk_mouse_motion_event",
        reset=False,
        # 第一行必须自带缩进(踩过:顶格会让 Godot 报
        # "Expected indented block after function declaration")
        body=[
            condition,
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
