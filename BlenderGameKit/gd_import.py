"""从 GDScript 反向导入节点图(第一百九十二轮)。

── 支持范围(先做最有用的一条)────────────────────────────────
这个解析器**不是**完整的 GDScript 编译器,它只认 BlenderGameKit 自己
导出的那套形状,以及同一形状的手写脚本:

    extends CharacterBody2D / CharacterBody3D
    const ACTION_* := "..."
    @export var move_speed: float = 260.0
    func _ready() / _process(delta) / _physics_process(delta)
        print("...")
        await get_tree().create_timer(1.5).timeout
        queue_free()
        变量 = 数值
        if <条件>: ...
        (物理帧)输入 -> 移动 -> 翻转 -> 在地面且跳跃键 -> 跳跃

不认识的语句会**带行号列进 warnings**,不会静默丢掉;不会为了"看起来
导入成功"而生成一个行为不对的图。

── 为什么不做通用解析器 ──────────────────────────────────────
GDScript 的完整语法(类型推断 / lambda / match / 信号连接 / 自定义资源)
远超"把导出的脚本改回节点图"这件事;真做完整解析器等于再造一个 Godot。
先把**插件自己的产物**做成可逆的,用户手写的非标准脚本给出明确警告。
"""

import re
import types

from .nodes import nodes as node_lib
from .nodes.node_base import TARGET_SELF

_BY_ID = {cls.bl_idname: cls for cls in node_lib.ALL_CLASSES}

EVENT_NODES = {
    "_ready": ("GKNodeOnReady", "每当_准备"),
    "_process": ("GKNodeOnProcess", "每当_过程"),
    "_physics_process": ("GKNodeOnPhysicsProcess", "每当_物理帧"),
}

_COMPARE_OPS = {
    "==": "EQ", "!=": "NE", ">": "GT",
    ">=": "GE", "<": "LT", "<=": "LE",
}


class GDScriptImportError(ValueError):
    """整份脚本没法导入(消息直接给用户看)。"""


class _Line:
    __slots__ = ("number", "indent", "text")

    def __init__(self, number, indent, text):
        self.number = number
        self.indent = indent
        self.text = text

    def __repr__(self):
        return f"<Line {self.number} {self.indent} {self.text!r}>"


# ==========================================================================
# 文本预处理
# ==========================================================================
def _strip_comment(line):
    """去掉 # 注释(引号里的 # 不算)。"""
    out = []
    quote = ""
    escaped = False
    for char in line:
        if escaped:
            out.append(char)
            escaped = False
            continue
        if char == "\\":
            out.append(char)
            escaped = True
            continue
        if quote:
            out.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
            out.append(char)
            continue
        if char == "#":
            break
        out.append(char)
    return "".join(out).rstrip()


def _parse_lines(text):
    lines = []
    for number, raw in enumerate(str(text or "").splitlines(), 1):
        body = _strip_comment(raw)
        if not body.strip():
            continue
        expanded = body.replace("\t", "    ")
        indent = len(expanded) - len(expanded.lstrip(" "))
        lines.append(_Line(number, indent, expanded.strip()))
    return lines


def _functions(lines):
    """顶层 func -> (名字, 函数体行)。函数体 = 缩进更深的连续行。"""
    out = []
    index = 0
    while index < len(lines):
        line = lines[index]
        match = re.match(r"func\s+(\w+)\s*\(", line.text)
        if match and line.indent == 0:
            body = []
            index += 1
            while index < len(lines) and lines[index].indent > line.indent:
                body.append(lines[index])
                index += 1
            out.append((match.group(1), body))
            continue
        index += 1
    return out


# ==========================================================================
# 字面量 / 标识符
# ==========================================================================
def _string_value(text):
    text = str(text or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _number_value(text):
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


def _resolve(text, constants):
    """ACTION_JUMP -> "jump"; "jump" -> jump; 其它原样。"""
    raw = str(text or "").strip()
    if not raw:
        return ""
    if raw[0] in "\"'":
        return _string_value(raw)
    if raw in constants:
        return constants[raw]
    return raw


def _resolve_number(text, exports, fallback):
    raw = str(text or "").strip()
    value = _number_value(raw)
    if value is not None:
        return value
    if raw in exports:
        value = _number_value(exports[raw])
        if value is not None:
            return value
    return float(fallback)


# ==========================================================================
# 节点 / 连线(同时支持 Blender 与离线桩)
# ==========================================================================
class _Builder:
    def __init__(self, tree):
        self.tree = tree
        self.counter = 0
        self.y = 0.0

    def new(self, bl_idname, name, x=0.0, **props):
        cls = _BY_ID[bl_idname]
        self.counter += 1
        unique = f"{name}{self.counter}"
        if hasattr(self.tree, "new_node"):
            node = self.tree.new_node(cls, unique, location=(x, self.y))
        else:
            node = self.tree.nodes.new(bl_idname)
            node.name = unique
            node.location = (x, self.y)
        for key, value in props.items():
            if hasattr(node, key):
                setattr(node, key, value)
        builder = getattr(node, "build_sockets", None)
        if callable(builder):
            builder()
        return node

    def connect(self, source, from_name, target, to_name):
        out = source.outputs.get(from_name)
        inp = target.inputs.get(to_name)
        if out is None or inp is None:
            return False
        if hasattr(self.tree.links, "new"):
            self.tree.links.new(out, inp)
        else:
            # 离线桩:StubLink 的结构和 Blender 一样,直接手工登记
            link = types.SimpleNamespace(
                from_socket=out, to_socket=inp,
                from_node=source, to_node=target)
            out.links.append(link)
            inp.links.append(link)
            self.tree.links.append(link)
        return True

    def next_row(self):
        self.y -= 180.0


# ==========================================================================
# 条件表达式
# ==========================================================================
def _strip_outer(expr):
    text = str(expr or "").strip()
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        balanced = True
        for index, char in enumerate(text):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and index != len(text) - 1:
                    balanced = False
                    break
        if not balanced:
            break
        text = text[1:-1].strip()
    return text


def _split_top(expr, token):
    """按顶层 and / or 拆开(引号、括号里的不算)。"""
    parts = []
    depth = 0
    quote = ""
    escaped = False
    start = 0
    index = 0
    while index < len(expr):
        char = expr[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        if quote:
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in "\"'":
            quote = char
            index += 1
            continue
        if char in "([{":
            depth += 1
            index += 1
            continue
        if char in ")]}":
            depth -= 1
            index += 1
            continue
        if depth == 0 and expr.startswith(token, index):
            parts.append(expr[start:index].strip())
            index += len(token)
            start = index
            continue
        index += 1
    if not parts:
        return [expr]
    parts.append(expr[start:].strip())
    return [part for part in parts if part]


def _map_condition(builder, expr, constants, exports, warnings, line_no):
    text = _strip_outer(expr)
    if not text:
        warnings.append(f"第 {line_no} 行:条件为空,分支没有导入")
        return None

    for token, op in ((" and ", "AND"), (" or ", "OR")):
        parts = _split_top(text, token)
        if len(parts) > 1:
            if len(parts) > 8:
                warnings.append(f"第 {line_no} 行:{op} 超过 8 个输入,分支没有导入")
                return None
            logic = builder.new("GKNodeLogic", "逻辑", x=-260.0,
                                gk_op=op, gk_input_count=len(parts))
            for index, part in enumerate(parts):
                source = _map_condition(builder, part, constants, exports,
                                        warnings, line_no)
                if source is None:
                    return None
                builder.connect(source, _bool_output(source),
                                logic, chr(ord("A") + index))
            return logic

    if text.startswith("not "):
        source = _map_condition(builder, text[4:], constants, exports,
                                warnings, line_no)
        if source is None:
            return None
        logic = builder.new("GKNodeLogic", "非", x=-520.0, gk_op="NOT")
        builder.connect(source, _bool_output(source), logic, "A")
        return logic

    match = re.match(
        r"Input\.is_action_(just_pressed|pressed|just_released)\(([^)]*)\)",
        text)
    if match:
        trigger = {"just_pressed": "JUST", "pressed": "PRESSED",
                   "just_released": "RELEASED"}[match.group(1)]
        action = _resolve(match.group(2), constants)
        return builder.new("GKNodeKey", "按键", x=-520.0,
                           gk_action=action, gk_trigger=trigger)

    if re.match(r"(?:self\.)?is_on_floor\(\)", text):
        return builder.new("GKNodeIsOnFloor", "在地面", x=-520.0)

    if text in ("true", "false"):
        return builder.new("GKNodeConstBool", "布尔", x=-520.0,
                           gk_value=(text == "true"))

    match = re.match(r"(.+?)\s*(==|!=|>=|<=|>|<)\s*(.+)", text)
    if match:
        left, op, right = match.group(1).strip(), match.group(2), match.group(3).strip()
        compare = builder.new("GKNodeCompare", "比较", x=-520.0,
                              gk_op=_COMPARE_OPS[op])
        for socket, operand in (("A", left), ("B", right)):
            value = _number_value(operand)
            if value is not None:
                setattr(compare, f"gk_{socket.lower()}", f"{value:g}")
                continue
            if operand in ("true", "false"):
                setattr(compare, f"gk_{socket.lower()}", operand)
                continue
            if len(operand) >= 2 and operand[0] in "\"'":
                setattr(compare, f"gk_{socket.lower()}", _string_value(operand))
                continue
            if re.match(r"^[A-Za-z_]\w*$", operand):
                getter = builder.new("GKNodeGetVar", "读取", x=-760.0,
                                     gk_var_name=operand)
                builder.connect(getter, "值", compare, socket)
                continue
            warnings.append(
                f"第 {line_no} 行:比较里的『{operand}』不是变量或字面量,"
                f"分支没有导入")
            return None
        return compare

    warnings.append(f"第 {line_no} 行:条件『{text}』不认识,分支没有导入")
    return None


def _bool_output(node):
    bl_id = str(getattr(node, "bl_idname", ""))
    if bl_id == "GKNodeIsOnFloor":
        return "在地面"
    if bl_id == "GKNodeConstBool":
        return "值"
    return "结果"


# ==========================================================================
# 物理帧:标准移动链
# ==========================================================================
def _canonical(body, constants, exports):
    """识别插件导出的『输入 -> 移动 -> 翻转 -> 跳跃』链。"""
    x_line = y_line = dir_line = move_line = gravity_line = jump_line = None
    speed_var = ""
    jump_var = ""
    jump_sign = 1.0
    flip = False
    deadzone = 0.2
    normalize = False
    condition_line = ""
    axis_neg = True

    for line in body:
        text = line.text
        match = re.match(
            r"var\s+(\w+)\s*:=\s*Input\.get_action_strength\((\w+)\)"
            r"\s*-\s*Input\.get_action_strength\((\w+)\)", text)
        if match:
            if x_line is None:
                x_line = (line, match.group(2), match.group(3))
            elif y_line is None:
                y_line = (line, match.group(2), match.group(3))
            continue
        match = re.match(
            r"if\s+absf\((\w+)\)\s*<\s*([\d.]+):", text)
        if match:
            deadzone = float(match.group(2))
            continue
        if (text.startswith("var _in_len") or text.startswith("if _in_len")
                or text.startswith("var ") and "sqrt(" in text):
            normalize = True
            continue
        if re.match(r"var\s+\w+\s*:=\s*\(Vector[23]\(.*\)\)\.normalized\(\)",
                    text):
            dir_line = line
            continue
        if "get_gravity()" in text:
            gravity_line = line
            continue
        match = re.match(r"(?:self\.)?velocity\.x\s*=\s*(.+)$", text)
        if match:
            expr = match.group(1).strip()
            speed_var = expr.rsplit("*", 1)[-1].strip()
            continue
        match = re.match(
            r"(?:self\.)?velocity\.z\s*=\s*(\(\s*-?.*?\)|\S+)\s*\*\s*(\w+)",
            text)
        if match:
            if "(-" in text or "= -" in text:
                axis_neg = True
            else:
                axis_neg = False
            speed_var = match.group(2)
            continue
        if "move_and_slide()" in text:
            move_line = line
            continue
        if "scale.x = -abs" in text:
            flip = True
            continue
        if ("is_on_floor()" in text
                and "Input.is_action_just_pressed(" in text):
            condition_line = text
            continue
        match = re.match(r"(?:self\.)?velocity\.y\s*=\s*(-?)\s*(\w+)", text)
        if match:
            jump_line = line
            jump_sign = -1.0 if match.group(1) else 1.0
            jump_var = match.group(2)
            continue

    if not (x_line and y_line and move_line and jump_line):
        return None

    condition_action = ""
    if condition_line:
        match = re.search(
            r"Input\.is_action_just_pressed\((\w+)\)", condition_line)
        if match:
            condition_action = _resolve(match.group(1), constants)

    consumed = set()
    for line in body:
        text = line.text
        if (text.startswith("var _unused_delta")
                or "Input.get_action_strength(" in text
                or text.startswith("if absf(")
                or text.startswith("var _in_len")
                or text.startswith("if _in_len")
                or text.startswith("_in_x") or text.startswith("_in_y")
                or text.startswith("var _dir")
                or "get_gravity()" in text
                or text.startswith("self.velocity.") or text.startswith("velocity.")
                or "move_and_slide()" in text
                or text.startswith("if self.velocity.x")
                or text.startswith("if (self.is_on_floor")
                or "scale.x" in text
                or text.startswith("if ((_dir")
                or "face_yaw" in text
                or "rotation.y" in text
                or text.startswith("else:")):
            consumed.add(line.number)
    # 3D 的 atan2 / lerp_angle 两行
    for line in body:
        if "atan2(" in line.text or "lerp_angle(" in line.text:
            consumed.add(line.number)

    return {
        "x_line": x_line,
        "y_line": y_line,
        "dir_line": dir_line,
        "move_line": move_line,
        "gravity": gravity_line is not None,
        "flip": flip,
        "deadzone": deadzone,
        "normalize": normalize,
        "speed": _resolve_number(speed_var, exports, 300.0),
        "jump_force": abs(_resolve_number(jump_var, exports, 400.0)),
        "jump_positive": jump_sign > 0,
        "condition_action": condition_action,
        "axis_neg": axis_neg,
        "consumed": consumed,
    }


def _build_canonical(builder, spec, event, dimension):
    x_line, x_pos, x_neg = spec["x_line"]
    y_line, y_pos, y_neg = spec["y_line"]
    actions = {
        "left": _resolve(x_neg, builder.constants),
        "right": _resolve(x_pos, builder.constants),
        "up": _resolve(y_pos, builder.constants),
        "down": _resolve(y_neg, builder.constants),
    }
    input_dir = builder.new(
        "GKNodeInputDirection", "方向输入", x=0.0,
        gk_action_left=actions["left"], gk_action_right=actions["right"],
        gk_action_up=actions["up"], gk_action_down=actions["down"],
        gk_deadzone=spec["deadzone"], gk_normalize=spec["normalize"])
    move = builder.new(
        "GKNodeMoveDirection", "移动", x=260.0,
        gk_speed=spec["speed"], gk_keep_y=spec["gravity"],
        gk_axis_h="X", gk_axis_v="Z",
        gk_forward_sign="NEG" if spec["axis_neg"] else "POS")
    builder.connect(event, "执行", move, "执行")
    builder.connect(input_dir, "方向", move, "方向")
    last = move

    if spec["flip"]:
        flip = builder.new("GKNodeFlip", "翻转", x=520.0, gk_mode="AUTO")
        builder.connect(last, "完成", flip, "执行")
        last = flip

    if spec["condition_action"]:
        floor = builder.new("GKNodeIsOnFloor", "在地面", x=260.0)
        key = builder.new("GKNodeKey", "跳跃键", x=260.0,
                          gk_action=spec["condition_action"],
                          gk_trigger="JUST")
        logic = builder.new("GKNodeLogic", "与", x=520.0, gk_op="AND")
        branch = builder.new("GKNodeBranch", "能跳吗", x=780.0)
        jump = builder.new("GKNodeJump", "起跳", x=1040.0,
                           gk_jump_force=spec["jump_force"])
        builder.connect(last, "完成", branch, "执行")
        builder.connect(floor, "在地面", logic, "A")
        builder.connect(key, "结果", logic, "B")
        builder.connect(logic, "结果", branch, "条件")
        builder.connect(branch, "是", jump, "执行")
    else:
        jump = builder.new("GKNodeJump", "起跳", x=780.0,
                           gk_jump_force=spec["jump_force"])
        builder.connect(last, "完成", jump, "执行")


# ==========================================================================
# 普通语句
# ==========================================================================
def _skip_block(lines, index, indent):
    while index < len(lines) and lines[index].indent >= indent:
        index += 1
    return index


def _map_block(builder, lines, index, indent, previous, constants, exports,
               warnings, skipped, depth=0, from_socket="完成"):
    """把一块语句映射成执行链。返回 (下一个下标, 最后的执行节点)。"""
    first = True
    while index < len(lines):
        line = lines[index]
        if line.indent < indent:
            return index, previous
        if line.indent > indent:
            warnings.append(
                f"第 {line.number} 行:缩进不对(前面没有 if / 循环),已跳过")
            skipped.append((line.number, line.text))
            index += 1
            continue
        if line.text.startswith("else:"):
            return index, previous

        if line.text.startswith("if ") and line.text.endswith(":"):
            expr = line.text[3:-1].strip()
            branch = builder.new("GKNodeBranch", "如果", x=260.0 * (depth + 1))
            if previous is not None:
                builder.connect(previous, from_socket if first else "完成",
                                branch, "执行")
                first = False
            condition = _map_condition(builder, expr, constants, exports,
                                       warnings, line.number)
            if condition is None:
                skipped.append((line.number, line.text))
                index = _skip_block(lines, index + 1, indent + 4)
                previous = None
                continue
            builder.connect(condition, _bool_output(condition),
                            branch, "条件")
            body_index, _ = _map_block(
                builder, lines, index + 1, indent + 4, branch, constants,
                exports, warnings, skipped, depth + 1, from_socket="是")
            if (body_index < len(lines) and lines[body_index].indent == indent
                    and lines[body_index].text.startswith("else:")):
                body_index, _ = _map_block(
                    builder, lines, body_index + 1, indent + 4, branch,
                    constants, exports, warnings, skipped, depth + 1,
                    from_socket="否")
            if (body_index < len(lines) and lines[body_index].indent == indent
                    and not lines[body_index].text.startswith("else:")):
                warnings.append(
                    f"第 {lines[body_index].number} 行:if 之后的语句没有导入"
                    f"(节点图没有分支汇合,请手动用序列节点接)")
                return len(lines), None
            previous = None
            index = body_index
            continue

        node = _map_statement(builder, line, constants, exports, warnings,
                              skipped)
        if node is not None:
            if previous is not None:
                builder.connect(previous, from_socket if first else "完成",
                                node, "执行")
                first = False
            previous = node
        index += 1
    return index, previous


def _map_statement(builder, line, constants, exports, warnings, skipped):
    text = line.text
    if re.match(r"^(pass|return|break|continue)$", text):
        return None
    match = re.match(r"print\((.*)\)$", text)
    if match:
        return builder.new("GKNodePrint", "打印", x=260.0,
                           gk_text=_string_value(match.group(1)))
    match = re.match(
        r"await\s+get_tree\(\)\.create_timer\(([^)]*)\)\.timeout", text)
    if match:
        seconds = _resolve_number(match.group(1), exports, 1.0)
        node = builder.new("GKNodeWait", "等待", x=260.0)
        socket = node.inputs.get("秒")
        if socket is not None:
            socket.default_value = seconds
        return node
    if text in ("queue_free()", "self.queue_free()"):
        return builder.new("GKNodeQueueFree", "销毁", x=260.0,
                           gk_target=TARGET_SELF)
    match = re.match(r"^([A-Za-z_]\w*)\s*=\s*(.+)$", text)
    if match:
        name, value = match.group(1), match.group(2).strip()
        number = _number_value(value)
        if number is not None:
            return builder.new("GKNodeSetVar", "设置变量", x=260.0,
                               gk_var_name=name, gk_value=number)
        if value in ("true", "false"):
            node = builder.new("GKNodeSetVar", "设置变量", x=260.0,
                               gk_var_name=name, gk_value=1.0 if value == "true" else 0.0)
            warnings.append(
                f"第 {line.number} 行:布尔变量『{name}』按 1/0 导入,"
                f"重新导出后是数值")
            return node
        warnings.append(
            f"第 {line.number} 行:变量赋值『{name} = {value}』不是数值,"
            f"没有导入(字符串变量请手动接节点)")
        skipped.append((line.number, text))
        return None
    if text.startswith("_gk_"):
        warnings.append(
            f"第 {line.number} 行:『{text}』是插件生成的辅助函数,没有导入")
        skipped.append((line.number, text))
        return None
    warnings.append(f"第 {line.number} 行:不认识『{text}』,已跳过")
    skipped.append((line.number, text))
    return None


# ==========================================================================
# 对外入口
# ==========================================================================
def import_script(tree, text, dimension="3"):
    """把 GDScript 文本导入一棵(通常是新建的)游戏逻辑树。

    返回 {created, base_class, dimension, warnings, skipped, functions}。
    不支持的语句只进 warnings / skipped,不会中断导入。
    """
    lines = _parse_lines(text)
    constants = {}
    exports = {}
    base_class = ""
    for line in lines:
        if line.indent != 0:
            continue
        match = re.match(r"extends\s+(\w+)", line.text)
        if match:
            base_class = match.group(1)
            continue
        match = re.match(r"const\s+(\w+)\s*:=\s*(.+)", line.text)
        if match:
            constants[match.group(1)] = _string_value(match.group(2))
            continue
        match = re.match(
            r"@export\s+var\s+(\w+)\s*:\s*(\w+)\s*=\s*(.+)", line.text)
        if match:
            exports[match.group(1)] = match.group(3).strip()
            continue

    if base_class.endswith("2D"):
        dimension = "2"
    elif base_class.endswith("3D"):
        dimension = "3"

    functions = _functions(lines)
    if not functions:
        raise GDScriptImportError(
            "这份脚本里没有 func —— 没有可导入的事件 / 语句")

    builder = _Builder(tree)
    builder.constants = constants
    warnings = []
    skipped = []
    start_count = len(getattr(tree, "nodes", ()) or ())
    imported_names = []
    for name, body in functions:
        event_info = EVENT_NODES.get(name)
        if event_info is None:
            if name.startswith("_gk_"):
                warnings.append(f"函数 {name}() 是插件辅助函数,已跳过")
            else:
                warnings.append(f"函数 {name}() 没有对应的事件节点,已跳过")
            continue
        bl_id, label = event_info
        event = builder.new(bl_id, label, x=0.0)
        imported_names.append(label)
        rest = body
        if name == "_physics_process":
            spec = _canonical(body, constants, exports)
            if spec is not None:
                _build_canonical(builder, spec, event, dimension)
                rest = [line for line in body
                        if line.number not in spec["consumed"]]
        if rest:
            _map_block(builder, rest, 0, 4, event, constants, exports,
                       warnings, skipped, from_socket="执行")
        builder.next_row()

    created = len(getattr(tree, "nodes", ()) or ()) - start_count
    if created <= len(imported_names):
        # 只有事件、没有任何语句/移动链 —— 仍然是一张有效图(至少能再导出),
        # 但提示用户这份脚本可能主要是辅助函数。
        warnings.append("没有导入任何语句 —— 这张图目前只有事件节点")

    return {
        "created": created,
        "base_class": base_class,
        "dimension": dimension,
        "warnings": warnings,
        "skipped": skipped,
        "functions": [name for name, _body in functions],
    }
