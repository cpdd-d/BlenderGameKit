"""字面量、类型名与常量工具。

从原 `codegen/core.py` 按领域拆出。这里只做「Python 值 / 类型名 -> GDScript 字面量」的纯转换,不碰节点遍历,也不产生执行流。"""

from ..nodes.node_base import TARGET_SELF, gd_node_path, gd_string, snake_case

# 事件节点 bl_idname -> (函数名, 是否需要 delta, 排序属性)
EVENT_KINDS = {
    "GKNodeOnReady": ("_ready", False, "gk_order"),
    "GKNodeOnProcess": ("_process", True, "gk_order"),
    "GKNodeOnPhysicsProcess": ("_physics_process", True, "gk_order"),
    "GKNodeOnInput": ("_input", False, None),
}

# 触发器节点 bl_idname -> (信号名, 回调形参名, (2D类型, 3D类型), 函数名前缀)
#
# ── 为什么单独一张表(用户 2026-10-04 的第 6 条)──────────────────────
# 事件节点(_ready/_process…)同类会**合并成一个函数**;触发器不是 ——
# "进入区域"每接一处就是一个独立的信号回调,同名合并会把两处逻辑串成
# 一个函数。所以它们走"每个节点一个函数"那条路(和『自定义函数』一样),
# 这里只登记"接哪个信号、回调长什么样"。
TRIGGER_KINDS = {
    "GKNodeOnAreaEntered": ("body_entered", "body", ("Node2D", "Node3D"),
                            "_gk_on_area_entered"),
    "GKNodeOnAreaExited": ("body_exited", "body", ("Node2D", "Node3D"),
                           "_gk_on_area_exited"),
    "GKNodeCollisionTrigger": ("area_entered", "area", ("Area2D", "Area3D"),
                               "_gk_on_collision"),
    "GKNodeOnTimer": ("timeout", "", (), "_gk_on_timer"),
}

# 事件节点特殊输出引脚对应的函数参数名
EVENT_PARAM_NAMES = {
    "GKNodeOnProcess": "delta",
    "GKNodeOnPhysicsProcess": "delta",
    "GKNodeOnInput": "event",
}

# 生成代码里用到的中文说明前缀
_BANNER = "#" * 74


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
    # 调用方**明确知道**节点类型时直接给(写法 "type:GPUParticles3D")。
    # 粒子发射器就靠它:同一个物体名在 Godot 里可能是 GPUParticles3D,
    # 也可能是 CPUParticles3D(面板上选的),名字里根本看不出来 ——
    # 只有导出侧那张发射器表知道,把它当 hint 传进来。
    if hint.startswith("type:"):
        return hint[5:]
    if hint == "cam":
        return "Camera2D" if dim == 2 else "Camera3D"
    # 触发器(进入/离开区域、碰撞)引用的是 Area 节点:节点名常常是
    # 『金币1』这种不含类型关键词的中文名,光看路径只能猜出 Node,
    # 于是 `.body_entered` 会被静态类型检查拒绝。
    if hint == "area":
        return "Area2D" if dim == 2 else "Area3D"
    return "Node"


def _vector_ctor(dim):
    return "Vector2" if dim == 2 else "Vector3"


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
