"""从节点图生成 GDExtension C++ 热路径(第一百九十一轮)。

── 这个生成器**只做一件事**────────────────────────────────────
把节点图里最典型的"每物理帧移动"链翻译成一个 C++ 节点:

    每当_物理帧 -> 输入_方向 -> 移动_按方向 [-> 移动_水平翻转]
                -> 分支(在地面 且 跳跃键) -> 移动_跳跃

生成的不是"整张图的替代品",而是一个**伴随节点**(`extends Node`):
把它挂在角色下面、`body_path` 指到角色,再把节点图里的移动链拆掉,
移动就由 C++ 每物理帧跑;输入、状态、动画、界面等规则仍然留在
节点图导出的 GDScript 里 —— 和 `docs/Blender与Godot互通.md` 里
"C++ 写热点、GDScript 写规则" 的分工一致。

── 为什么要"伴随节点"而不是替换根脚本 ─────────────────────────
节点图里通常还有 _ready / _process / 动画 / 相机跟随等事件。整份替换
会把这些全丢掉;伴随节点只接管移动,其它事件原样保留。代价是用户要
手动拆掉节点图里的移动链(生成的 README 会把节点名列出来)。

── 为什么只支持这些节点 ────────────────────────────────────
移动链上的"重力 / 轴映射 / 朝向 / 跳跃条件"都有明确的 C++ 对应写法;
再往外(寻路、推开刚体、上台阶、相机相对方向)是另一套大逻辑,搬一半
只会让手感悄悄变掉。遇到不支持的节点直接**拒绝并点名**,不生成半成品。
"""

import string

from .nodes.node_base import TARGET_SELF

# 移动链上允许出现的节点
_MOVE = "GKNodeMoveDirection"
_FLIP = "GKNodeFlip"
_JUMP = "GKNodeJump"
_BRANCH = "GKNodeBranch"
_SEQUENCE = "GKNodeSequence"
_INPUT_DIR = "GKNodeInputDirection"
_KEY = "GKNodeKey"
_ON_FLOOR = "GKNodeIsOnFloor"
_LOGIC = "GKNodeLogic"
_CONST_FLOAT = "GKNodeConstFloat"
_COMMENT = "GKNodeComment"
_PHYSICS = "GKNodeOnPhysicsProcess"

MAX_3D_SPEED = 50.0
DEFAULT_3D_SPEED = 6.0


class HotPathError(ValueError):
    """这张图不能生成热路径(消息直接给用户看)。"""


# ==========================================================================
# 图遍历小工具(同时适用于 Blender 与离线桩)
# ==========================================================================
def _label(node):
    return str(getattr(node, "bl_label", None)
               or getattr(node, "bl_idname", "节点"))


def _node_name(node):
    return str(getattr(node, "name", "") or _label(node))


def _source(node, socket_name):
    """取输入引脚连着的那个节点(没连返回 None)。"""
    socket = node.inputs.get(socket_name)
    if socket is None or not getattr(socket, "is_linked", False):
        return None
    links = list(getattr(socket, "links", ()) or ())
    if not links:
        return None
    if len(links) > 1:
        raise HotPathError(
            f"『{_node_name(node)}』的『{socket_name}』接了多条线 —— "
            f"热路径生成器只认单条连接")
    return links[0].from_node


def _targets(node, socket_name):
    """取输出引脚连出去的所有节点(按连线顺序)。"""
    socket = node.outputs.get(socket_name)
    if socket is None:
        return []
    return [link.to_node for link in (getattr(socket, "links", ()) or ())]


def _linked(node, socket_name):
    socket = node.inputs.get(socket_name)
    return bool(socket is not None and getattr(socket, "is_linked", False))


def _number_input(node, socket_name, fallback, label):
    """数值输入:连线只认『常量_数值』,否则用节点自己的属性。"""
    source = _source(node, socket_name)
    if source is None:
        try:
            return float(fallback)
        except (TypeError, ValueError):
            return 0.0
    if str(getattr(source, "bl_idname", "")) != _CONST_FLOAT:
        raise HotPathError(
            f"『{_node_name(node)}』的『{label}』连的是『{_label(source)}』——"
            f"热路径生成器只认『常量_数值』或节点上直接填的数")
    try:
        return float(getattr(source, "gk_value", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


# ==========================================================================
# 条件解析:在地面 且 跳跃键(支持嵌套 AND)
# ==========================================================================
def _parse_condition(node, polarity=True):
    """返回 (require_floor, action, has_floor, has_key)。

    polarity=False 表示这个条件挂在分支的『否』出口上 —— 语义反过来,
    生成器不支持(直接拒绝,免得"按跳跃反而起跳"这种镜像逻辑被搬错)。
    """
    if node is None:
        raise HotPathError("跳跃分支没有接条件 —— 请把『在地面』和『跳跃键』"
                           "接进一个『运算_逻辑(与)』再接到分支")
    if not polarity:
        raise HotPathError("『移动_跳跃』挂在分支的『否』出口 —— "
                           "热路径生成器只认『是』出口")
    bl_id = str(getattr(node, "bl_idname", ""))
    if bl_id == _ON_FLOOR:
        return True, "", True, False
    if bl_id == _KEY:
        trigger = str(getattr(node, "gk_trigger", "PRESSED"))
        if trigger != "JUST":
            raise HotPathError(
                f"跳跃键『{_node_name(node)}』的触发方式是『{trigger}』——"
                f"热路径只认『刚按下(JUST)』")
        action = ""
        resolver = getattr(node, "resolved_action", None)
        if callable(resolver):
            action = str(resolver() or "")
        if not action:
            action = str(getattr(node, "gk_action", "") or "jump")
        return False, action, False, True
    if bl_id == _LOGIC and str(getattr(node, "gk_op", "AND")) == "AND":
        left = _parse_condition(_source(node, "A"), polarity)
        right = _parse_condition(_source(node, "B"), polarity)
        floor = left[0] or right[0]
        has_floor = left[2] or right[2]
        has_key = left[3] or right[3]
        actions = {item[1] for item in (left, right) if item[3]}
        if len(actions) > 1:
            raise HotPathError("跳跃条件里出现了两个不同的按键 —— "
                               "热路径只能生成一个跳跃动作")
        return floor, (actions.pop() if actions else ""), has_floor, has_key
    raise HotPathError(
        f"跳跃条件里出现了『{_label(node)}』—— 热路径只认"
        f"『在地面』『输入_按键(刚按下)』和『运算_逻辑(与)』的组合")


# ==========================================================================
# 移动链遍历
# ==========================================================================
def _new_spec():
    return {
        "event": None,
        "move": None,
        "flip": None,
        "jump": None,
        "condition": None,
        "visited": set(),
        "warnings": [],
    }


def _walk(node, spec, polarity=True):
    if node is None:
        return
    key = id(node)
    if key in spec["visited"]:
        return
    spec["visited"].add(key)
    bl_id = str(getattr(node, "bl_idname", ""))

    if bl_id == _COMMENT:
        return
    if bl_id == _MOVE:
        if spec["move"] is not None:
            raise HotPathError("热路径里出现了两个『移动_按方向』—— "
                               "请只保留一条移动链")
        spec["move"] = node
        for nxt in _targets(node, "完成"):
            _walk(nxt, spec, polarity)
        return
    if bl_id == _FLIP:
        spec["flip"] = node
        for nxt in _targets(node, "完成"):
            _walk(nxt, spec, polarity)
        return
    if bl_id == _JUMP:
        if not polarity:
            raise HotPathError(
                f"『{_node_name(node)}』挂在分支的『否』出口 —— "
                f"热路径生成器只认『是』出口")
        if spec["jump"] is not None:
            raise HotPathError("热路径里出现了两个『移动_跳跃』—— "
                               "请只保留一处起跳")
        spec["jump"] = node
        for nxt in _targets(node, "完成"):
            _walk(nxt, spec, polarity)
        return
    if bl_id == _BRANCH:
        if spec["condition"] is not None:
            raise HotPathError("热路径里出现了两个分支 —— 请把移动和起跳"
                               "放在同一条链上")
        spec["condition"] = _parse_condition(_source(node, "条件"), polarity)
        for nxt in _targets(node, "是"):
            _walk(nxt, spec, True)
        for nxt in _targets(node, "否"):
            _walk(nxt, spec, False)
        return
    if bl_id == _SEQUENCE:
        count = max(1, min(8, int(getattr(node, "gk_step_count", 3) or 3)))
        for index in range(1, count + 1):
            for nxt in _targets(node, str(index)):
                _walk(nxt, spec, polarity)
        return
    raise HotPathError(
        f"移动链里出现了『{_label(node)}』—— 热路径生成器只支持"
        f"『移动_按方向 / 移动_水平翻转 / 移动_跳跃 / 分支 / 序列』。"
        f"请把这个节点拆到另一条链(它仍然由 GDScript 负责)")


# ==========================================================================
# 参数提取
# ==========================================================================
def _extract_move(spec, dimension):
    move = spec["move"]
    if str(getattr(move, "gk_target", TARGET_SELF) or TARGET_SELF) != TARGET_SELF:
        raise HotPathError(
            f"『{_node_name(move)}』的移动目标是别的物体 —— "
            f"热路径节点只驱动它自己的 body")
    if bool(getattr(move, "gk_follow_camera", False)):
        raise HotPathError("『移动_按方向』勾了『随相机朝向』—— "
                           "热路径生成器暂不支持相机相对方向")
    if _linked(move, "参考系"):
        raise HotPathError("『移动_按方向』的『参考系』接了节点 —— "
                           "热路径生成器暂不支持相机相对方向")
    if bool(getattr(move, "gk_push_bodies", False)):
        raise HotPathError("『移动_按方向』勾了『推开刚体』—— "
                           "热路径生成器暂不支持,请先关掉")
    try:
        step_height = float(getattr(move, "gk_step_height", 0.0) or 0.0)
    except (TypeError, ValueError):
        step_height = 0.0
    if step_height > 0.0:
        raise HotPathError("『移动_按方向』填了『上台阶高度』—— "
                           "热路径生成器暂不支持,请先填 0")

    direction = _source(move, "方向")
    if direction is None or str(getattr(direction, "bl_idname", "")) != _INPUT_DIR:
        raise HotPathError(
            f"『{_node_name(move)}』的『方向』没有接『输入_方向』—— "
            f"热路径生成器只认这一种方向来源")

    actions = {}
    for part in ("up", "down", "left", "right"):
        action = ""
        resolver = getattr(direction, "action_for", None)
        if callable(resolver):
            try:
                action, _key = resolver(part)
            except Exception:                                # noqa: BLE001
                action = ""
        if not action:
            action = f"move_{part}"
        actions[part] = action

    try:
        deadzone = float(getattr(direction, "gk_deadzone", 0.2) or 0.0)
    except (TypeError, ValueError):
        deadzone = 0.0
    normalize = bool(getattr(direction, "gk_normalize", True))

    speed = _number_input(move, "速度", getattr(move, "gk_speed", 300.0), "速度")
    if dimension == "3" and speed > MAX_3D_SPEED:
        spec["warnings"].append(
            f"3D 速度 {speed:g} 米/秒太快,已按 {DEFAULT_3D_SPEED:g} 生成"
            f"(和 GDScript 那条路的 3D 夹取一致)")
        speed = DEFAULT_3D_SPEED

    spec.update({
        "dimension": dimension,
        "speed": speed,
        "gravity_mode": bool(getattr(move, "gk_keep_y", False)),
        "axis_h": str(getattr(move, "gk_axis_h", "X") or "X").upper(),
        "axis_v": str(getattr(move, "gk_axis_v", "Z") or "Z").upper(),
        "forward_neg": str(getattr(move, "gk_forward_sign", "NEG")) == "NEG",
        "actions": actions,
        "deadzone": deadzone,
        "normalize": normalize,
    })


def _extract_jump(spec):
    jump = spec["jump"]
    if jump is None:
        spec["jump_force"] = 0.0
        spec["jump_action"] = ""
        spec["jump_requires_floor"] = False
        return
    if str(getattr(jump, "gk_target", TARGET_SELF) or TARGET_SELF) != TARGET_SELF:
        raise HotPathError(
            f"『{_node_name(jump)}』的跳跃目标是别的物体 —— "
            f"热路径节点只驱动它自己的 body")
    condition = spec["condition"]
    if condition is None:
        raise HotPathError("『移动_跳跃』没有接分支条件 —— "
                           "热路径生成器只认『在地面 且 跳跃键』这种分支")
    require_floor, action, _has_floor, has_key = condition
    if not has_key or not action:
        raise HotPathError("跳跃条件里没有『输入_按键』—— "
                           "热路径生成器需要一个跳跃动作")
    spec["jump_force"] = _number_input(
        jump, "强度", getattr(jump, "gk_jump_force", 400.0), "强度")
    spec["jump_action"] = action
    spec["jump_requires_floor"] = bool(require_floor)


def _extract_flip(spec):
    flip = spec["flip"]
    if flip is None:
        return
    mode = str(getattr(flip, "gk_mode", "AUTO"))
    if mode != "AUTO":
        raise HotPathError(
            f"『{_node_name(flip)}』的翻转方式是『{mode}』—— "
            f"热路径只认『自动(按速度方向)』")


# ==========================================================================
# 对外入口
# ==========================================================================
def detect(tree, dimension="3"):
    """检测移动热路径,返回 spec;不支持时抛 `HotPathError`。"""
    events = [node for node in (getattr(tree, "nodes", ()) or ())
              if str(getattr(node, "bl_idname", "")) == _PHYSICS]
    if not events:
        raise HotPathError("这张图里没有『每当_物理帧』—— "
                           "热路径必须从物理帧事件开始")

    errors = []
    meaningful = ""
    chosen = None
    for event in events:
        spec = _new_spec()
        spec["event"] = event
        try:
            for nxt in _targets(event, "执行"):
                _walk(nxt, spec)
            if spec["move"] is None:
                raise HotPathError("这条物理帧链里没有『移动_按方向』")
        except HotPathError as exc:
            text = str(exc)
            # 优先报"已经找到移动链、但里面有不支持节点"的那条 ——
            # 比"另一条空事件没有移动节点"有用得多。
            if "没有『移动_按方向』" not in text:
                meaningful = text
            errors.append(text)
            continue
        chosen = spec
        break
    if chosen is None:
        detail = meaningful or (errors[0] if errors else "没有可用的移动链")
        raise HotPathError(detail)

    _extract_move(chosen, str(dimension or "3"))
    _extract_jump(chosen)
    _extract_flip(chosen)

    # 其它事件仍由 GDScript 负责 —— 生成器只接管移动,不需要拒绝它们,
    # 但要把"拆掉移动链"说清楚。
    other_events = [node for node in (getattr(tree, "nodes", ()) or ())
                    if bool(getattr(node, "gk_is_event", False))
                    and node is not chosen["event"]]
    if other_events:
        names = "、".join(_node_name(node) for node in other_events[:4])
        chosen["warnings"].append(
            f"还有其它事件({names})—— 它们仍由导出的 GDScript 负责;"
            f"热路径节点只接管移动")
    return chosen


def _cpp_string(text):
    """C++ 字符串字面量(项目里没有需要转义的奇怪字符,但别赌)。"""
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _num(value):
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    if "." not in text:
        text += ".0"
    return text


def _movement_lines(spec):
    """生成 2D/3D 的输入映射 + 速度写入片段。"""
    dim = spec["dimension"]
    if dim == "2":
        dir_decl = "Vector2 dir(x, y);"
        if spec["gravity_mode"]:
            velocity = (
                "    if (gravity_mode) {\n"
                "        velocity += body->get_gravity() * delta;\n"
                "        velocity.x = dir.x * speed;\n"
                "    } else {\n"
                "        velocity = dir * speed;\n"
                "    }")
        else:
            velocity = "    velocity = dir * speed;"
        return dir_decl, velocity, ""

    # ── 3D:按『横移轴 / 前进轴 / 前进方向』拼 Vector3 ──────────────
    axis_h = spec["axis_h"] if spec["axis_h"] in ("X", "Z") else "X"
    axis_v = spec["axis_v"] if spec["axis_v"] in ("X", "Z", "Y") else "Z"
    sign_op = "-" if spec["forward_neg"] else "+"
    # 同一根轴被横移和前进同时占用时,先相加再乘速度 —— 和 GDScript
    # 那条路(先写一次、再 += 一次)数值一致。
    components = {axis_h.lower(): "x"}
    if axis_v != "Y":
        key = axis_v.lower()
        if key in components:
            components[key] = f"({components[key]} {sign_op} y)"
        else:
            components[key] = f"{sign_op} y" if sign_op == "-" else "y"
    dir_lines = ["Vector3 dir(0.0, 0.0, 0.0);"]
    for axis, expr in components.items():
        dir_lines.append(f"dir.{axis} = {expr};")
    if axis_v == "Y":
        dir_lines.append("// 前进轴选了 Y:Y 留给重力,这一项不写入")
    dir_decl = "\n    ".join(dir_lines)

    if spec["gravity_mode"]:
        lines = [
            "    if (gravity_mode) {",
            "        velocity += body->get_gravity() * delta;",
        ]
        for axis in components:
            if axis == "y":
                continue
            lines.append(f"        velocity.{axis} = dir.{axis} * speed;")
        lines += ["    } else {", "        velocity = dir * speed;", "    }"]
        velocity = "\n".join(lines)
    else:
        velocity = "    velocity = dir * speed;"

    rotate = (
        "    if (dir.length_squared() > 0.0001) {\n"
        "        real_t face_yaw = Math::atan2(-dir.x, -dir.z);\n"
        "        Vector3 rot = body->get_rotation();\n"
        "        rot.y = Math::lerp_angle(rot.y, face_yaw,\n"
        "                (real_t)Math::min(1.0, 12.0 * delta));\n"
        "        body->set_rotation(rot);\n"
        "    }")
    return dir_decl, velocity, rotate


HEADER_TEMPLATE = string.Template("""\
#ifndef $GUARD
#define $GUARD

#include <godot_cpp/classes/$BASE_INCLUDE>
#include <godot_cpp/classes/node.hpp>
#include <godot_cpp/classes/$FLIP_INCLUDE>
#include <godot_cpp/variant/node_path.hpp>
#include <godot_cpp/variant/string.hpp>

namespace godot {

// 由 BlenderGameKit 从节点图生成的**热路径伴随节点**。
//
// 它不替代你的 GDScript:挂在角色下面,让 body_path 指向
// $BODY_TYPE,再把节点图里的移动链拆掉 —— 移动由这份 C++ 每物理帧跑,
// 其它事件(状态 / 动画 / 界面)仍然由节点图导出的 GDScript 负责。
class $CLASS : public Node {
\tGDCLASS($CLASS, Node)

private:
\tNodePath body_path = NodePath("..");
\tNodePath flip_path;
\t$BODY_TYPE *body = nullptr;
\t$FLIP_TYPE *flip_target = nullptr;

\tdouble speed = $SPEED;
\tdouble jump_force = $JUMP_FORCE;
\tbool gravity_mode = $GRAVITY;
\tbool jump_requires_floor = $JUMP_FLOOR;
\tbool normalize_input = $NORMALIZE;
\tdouble deadzone = $DEADZONE;

\tString action_left = $ACTION_LEFT;
\tString action_right = $ACTION_RIGHT;
\tString action_up = $ACTION_UP;
\tString action_down = $ACTION_DOWN;
\tString action_jump = $ACTION_JUMP;

protected:
\tstatic void _bind_methods();

public:
\t$CLASS();
\t~$CLASS();

\tvoid _ready() override;
\tvoid _physics_process(double delta) override;

\tvoid set_body_path(const NodePath &p_value);
\tNodePath get_body_path() const;
\tvoid set_flip_path(const NodePath &p_value);
\tNodePath get_flip_path() const;
\tvoid set_speed(double p_value);
\tdouble get_speed() const;
\tvoid set_jump_force(double p_value);
\tdouble get_jump_force() const;
\tvoid set_gravity_mode(bool p_value);
\tbool get_gravity_mode() const;
\tvoid set_jump_requires_floor(bool p_value);
\tbool get_jump_requires_floor() const;
\tvoid set_normalize_input(bool p_value);
\tbool get_normalize_input() const;
\tvoid set_deadzone(double p_value);
\tdouble get_deadzone() const;
\tvoid set_action_left(const String &p_value);
\tString get_action_left() const;
\tvoid set_action_right(const String &p_value);
\tString get_action_right() const;
\tvoid set_action_up(const String &p_value);
\tString get_action_up() const;
\tvoid set_action_down(const String &p_value);
\tString get_action_down() const;
\tvoid set_action_jump(const String &p_value);
\tString get_action_jump() const;
};

} // namespace godot

#endif // $GUARD
""")


CPP_TEMPLATE = string.Template("""\
#include "gk_hot_path.h"

#include <godot_cpp/classes/input.hpp>
#include <godot_cpp/core/class_db.hpp>
#include <godot_cpp/core/math.hpp>
#include <godot_cpp/core/object.hpp>
#include <godot_cpp/variant/utility_functions.hpp>

using namespace godot;

$CLASS::$CLASS() = default;
$CLASS::~$CLASS() = default;

void $CLASS::_bind_methods() {
\tClassDB::bind_method(D_METHOD("set_body_path", "value"),
\t\t&$CLASS::set_body_path);
\tClassDB::bind_method(D_METHOD("get_body_path"), &$CLASS::get_body_path);
\tClassDB::bind_method(D_METHOD("set_flip_path", "value"),
\t\t&$CLASS::set_flip_path);
\tClassDB::bind_method(D_METHOD("get_flip_path"), &$CLASS::get_flip_path);
\tClassDB::bind_method(D_METHOD("set_speed", "value"), &$CLASS::set_speed);
\tClassDB::bind_method(D_METHOD("get_speed"), &$CLASS::get_speed);
\tClassDB::bind_method(D_METHOD("set_jump_force", "value"),
\t\t&$CLASS::set_jump_force);
\tClassDB::bind_method(D_METHOD("get_jump_force"), &$CLASS::get_jump_force);
\tClassDB::bind_method(D_METHOD("set_gravity_mode", "value"),
\t\t&$CLASS::set_gravity_mode);
\tClassDB::bind_method(D_METHOD("get_gravity_mode"), &$CLASS::get_gravity_mode);
\tClassDB::bind_method(D_METHOD("set_jump_requires_floor", "value"),
\t\t&$CLASS::set_jump_requires_floor);
\tClassDB::bind_method(D_METHOD("get_jump_requires_floor"),
\t\t&$CLASS::get_jump_requires_floor);
\tClassDB::bind_method(D_METHOD("set_normalize_input", "value"),
\t\t&$CLASS::set_normalize_input);
\tClassDB::bind_method(D_METHOD("get_normalize_input"),
\t\t&$CLASS::get_normalize_input);
\tClassDB::bind_method(D_METHOD("set_deadzone", "value"),
\t\t&$CLASS::set_deadzone);
\tClassDB::bind_method(D_METHOD("get_deadzone"), &$CLASS::get_deadzone);
\tClassDB::bind_method(D_METHOD("set_action_left", "value"),
\t\t&$CLASS::set_action_left);
\tClassDB::bind_method(D_METHOD("get_action_left"), &$CLASS::get_action_left);
\tClassDB::bind_method(D_METHOD("set_action_right", "value"),
\t\t&$CLASS::set_action_right);
\tClassDB::bind_method(D_METHOD("get_action_right"), &$CLASS::get_action_right);
\tClassDB::bind_method(D_METHOD("set_action_up", "value"),
\t\t&$CLASS::set_action_up);
\tClassDB::bind_method(D_METHOD("get_action_up"), &$CLASS::get_action_up);
\tClassDB::bind_method(D_METHOD("set_action_down", "value"),
\t\t&$CLASS::set_action_down);
\tClassDB::bind_method(D_METHOD("get_action_down"), &$CLASS::get_action_down);
\tClassDB::bind_method(D_METHOD("set_action_jump", "value"),
\t\t&$CLASS::set_action_jump);
\tClassDB::bind_method(D_METHOD("get_action_jump"), &$CLASS::get_action_jump);

\tADD_PROPERTY(PropertyInfo(Variant::NODE_PATH, "body_path"),
\t\t"set_body_path", "get_body_path");
\tADD_PROPERTY(PropertyInfo(Variant::NODE_PATH, "flip_path"),
\t\t"set_flip_path", "get_flip_path");
\tADD_PROPERTY(PropertyInfo(Variant::FLOAT, "speed"),
\t\t"set_speed", "get_speed");
\tADD_PROPERTY(PropertyInfo(Variant::FLOAT, "jump_force"),
\t\t"set_jump_force", "get_jump_force");
\tADD_PROPERTY(PropertyInfo(Variant::BOOL, "gravity_mode"),
\t\t"set_gravity_mode", "get_gravity_mode");
\tADD_PROPERTY(PropertyInfo(Variant::BOOL, "jump_requires_floor"),
\t\t"set_jump_requires_floor", "get_jump_requires_floor");
\tADD_PROPERTY(PropertyInfo(Variant::BOOL, "normalize_input"),
\t\t"set_normalize_input", "get_normalize_input");
\tADD_PROPERTY(PropertyInfo(Variant::FLOAT, "deadzone"),
\t\t"set_deadzone", "get_deadzone");
\tADD_PROPERTY(PropertyInfo(Variant::STRING, "action_left"),
\t\t"set_action_left", "get_action_left");
\tADD_PROPERTY(PropertyInfo(Variant::STRING, "action_right"),
\t\t"set_action_right", "get_action_right");
\tADD_PROPERTY(PropertyInfo(Variant::STRING, "action_up"),
\t\t"set_action_up", "get_action_up");
\tADD_PROPERTY(PropertyInfo(Variant::STRING, "action_down"),
\t\t"set_action_down", "get_action_down");
\tADD_PROPERTY(PropertyInfo(Variant::STRING, "action_jump"),
\t\t"set_action_jump", "get_action_jump");
}

void $CLASS::_ready() {
\tbody = Object::cast_to<$BODY_TYPE>(get_node_or_null(body_path));
\tif (body == nullptr) {
\t\tUtilityFunctions::push_warning(
\t\t\t"$CLASS: body_path 没有指向 $BODY_TYPE");
\t\tset_physics_process(false);
\t\treturn;
\t}
\tif (!flip_path.is_empty()) {
\t\tflip_target = Object::cast_to<$FLIP_TYPE>(
\t\t\tbody->get_node_or_null(flip_path));
\t}
}

void $CLASS::_physics_process(double delta) {
\tif (body == nullptr) {
\t\treturn;
\t}
\tInput *input = Input::get_singleton();
\treal_t x = input->get_action_strength(action_right)
\t\t- input->get_action_strength(action_left);
\treal_t y = input->get_action_strength(action_up)
\t\t- input->get_action_strength(action_down);
\tif (deadzone > 0.0) {
\t\tif (Math::abs(x) < deadzone) {
\t\t\tx = 0.0;
\t\t}
\t\tif (Math::abs(y) < deadzone) {
\t\t\ty = 0.0;
\t\t}
\t}
\tif (normalize_input) {
\t\treal_t length = Math::sqrt(x * x + y * y);
\t\tif (length > 1.0) {
\t\t\tx /= length;
\t\t\ty /= length;
\t\t}
\t}

\t$DIR_DECL
\t$VEC_TYPE velocity = body->get_velocity();
$VELOCITY
\tbody->set_velocity(velocity);
$ROTATE
\tbody->move_and_slide();

\tif (flip_target != nullptr) {
\t\treal_t sx = Math::abs(flip_target->get_scale().x);
\t\tif (body->get_velocity().x < 0.0) {
\t\t\tsx = -sx;
\t\t}
\t\tflip_target->set_scale($FLIP_SCALE);
\t}

\tif (!action_jump.is_empty()
\t\t\t&& input->is_action_just_pressed(action_jump)) {
\t\tif (!jump_requires_floor || body->is_on_floor()) {
\t\t\t$VEC_TYPE jump_velocity = body->get_velocity();
\t\t\tjump_velocity.y = ${JUMP_SIGN}jump_force;
\t\t\tbody->set_velocity(jump_velocity);
\t\t}
\t}
}

void $CLASS::set_body_path(const NodePath &p_value) { body_path = p_value; }
NodePath $CLASS::get_body_path() const { return body_path; }
void $CLASS::set_flip_path(const NodePath &p_value) { flip_path = p_value; }
NodePath $CLASS::get_flip_path() const { return flip_path; }
void $CLASS::set_speed(double p_value) { speed = p_value; }
double $CLASS::get_speed() const { return speed; }
void $CLASS::set_jump_force(double p_value) { jump_force = p_value; }
double $CLASS::get_jump_force() const { return jump_force; }
void $CLASS::set_gravity_mode(bool p_value) { gravity_mode = p_value; }
bool $CLASS::get_gravity_mode() const { return gravity_mode; }
void $CLASS::set_jump_requires_floor(bool p_value) { jump_requires_floor = p_value; }
bool $CLASS::get_jump_requires_floor() const { return jump_requires_floor; }
void $CLASS::set_normalize_input(bool p_value) { normalize_input = p_value; }
bool $CLASS::get_normalize_input() const { return normalize_input; }
void $CLASS::set_deadzone(double p_value) { deadzone = p_value; }
double $CLASS::get_deadzone() const { return deadzone; }
void $CLASS::set_action_left(const String &p_value) { action_left = p_value; }
String $CLASS::get_action_left() const { return action_left; }
void $CLASS::set_action_right(const String &p_value) { action_right = p_value; }
String $CLASS::get_action_right() const { return action_right; }
void $CLASS::set_action_up(const String &p_value) { action_up = p_value; }
String $CLASS::get_action_up() const { return action_up; }
void $CLASS::set_action_down(const String &p_value) { action_down = p_value; }
String $CLASS::get_action_down() const { return action_down; }
void $CLASS::set_action_jump(const String &p_value) { action_jump = p_value; }
String $CLASS::get_action_jump() const { return action_jump; }
""")


REGISTER_TEMPLATE = string.Template("""\
#include "gk_hot_path.h"

#include <godot_cpp/core/defs.hpp>
#include <godot_cpp/godot.hpp>

using namespace godot;

static void initialize_gk_hot_path_module(
\t\tModuleInitializationLevel p_level) {
\tif (p_level != MODULE_INITIALIZATION_LEVEL_SCENE) {
\t\treturn;
\t}
\tGDREGISTER_CLASS($CLASS);
}

static void uninitialize_gk_hot_path_module(
\t\tModuleInitializationLevel p_level) {
\tif (p_level != MODULE_INITIALIZATION_LEVEL_SCENE) {
\t\treturn;
\t}
}

extern "C" {

// 必须和 gk_hot_path.gdextension 里的 entry_symbol 一致
GDExtensionBool GDE_EXPORT gk_hot_path_init(
\t\tGDExtensionInterfaceGetProcAddress p_get_proc_address,
\t\tconst GDExtensionClassLibraryPtr p_library,
\t\tGDExtensionInitialization *r_initialization) {
\tgodot::GDExtensionBinding::InitObject init_obj(
\t\tp_get_proc_address, p_library, r_initialization);
\tinit_obj.register_initializer(initialize_gk_hot_path_module);
\tinit_obj.register_terminator(uninitialize_gk_hot_path_module);
\tinit_obj.set_minimum_library_initialization_level(
\t\tMODULE_INITIALIZATION_LEVEL_SCENE);
\treturn init_obj.init();
}
}
""")

GODOT_EXTENSION = """\
; 由 BlenderGameKit 从节点图生成。
; 改完 C++ 必须重新编译,并**重启 Godot 编辑器**才会加载新库。

[configuration]

entry_symbol = "gk_hot_path_init"
compatibility_minimum = "4.5"
reloadable = true

[libraries]

; target=template_debug 的产物后缀是 .template_debug,不是 .debug
windows.debug.x86_64 = "res://native/bin/gk_hot_path.windows.template_debug.x86_64.dll"
windows.release.x86_64 = "res://native/bin/gk_hot_path.windows.template_release.x86_64.dll"
linux.debug.x86_64 = "res://native/bin/libgk_hot_path.linux.template_debug.x86_64.so"
linux.release.x86_64 = "res://native/bin/libgk_hot_path.linux.template_release.x86_64.so"
macos.debug = "res://native/bin/libgk_hot_path.macos.template_debug.framework"
macos.release = "res://native/bin/libgk_hot_path.macos.template_release.framework"
"""

SCONSTRUCT = """\
#!/usr/bin/env python
\"\"\"SCons 构建脚本(和 examples/gdextension_cpp 同一套做法)。

用法:
    $env:GODOT_CPP_PATH = "D:/godot-cpp"
    python -m SCons platform=windows target=template_debug -j8
\"\"\"

import os

GODOT_CPP_PATH = os.environ.get("GODOT_CPP_PATH", "../godot-cpp")
env = SConscript(os.path.join(GODOT_CPP_PATH, "SConstruct"))  # noqa: F821

sources = Glob("*.cpp")
library = env.SharedLibrary(
    target=os.path.join("bin", "gk_hot_path{}{}".format(
        env["suffix"], env["SHLIBSUFFIX"])),
    source=sources,
)
Default(library)
"""

CMAKE = """\
cmake_minimum_required(VERSION 3.20)
project(gk_hot_path LANGUAGES CXX)

set(GODOT_CPP_PATH "$ENV{GODOT_CPP_PATH}" CACHE PATH "godot-cpp 目录")
if(NOT GODOT_CPP_PATH)
    set(GODOT_CPP_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../godot-cpp")
endif()
if(NOT EXISTS "${GODOT_CPP_PATH}/CMakeLists.txt")
    message(FATAL_ERROR "找不到 godot-cpp: ${GODOT_CPP_PATH}")
endif()

add_subdirectory(${GODOT_CPP_PATH} godot-cpp)

add_library(gk_hot_path SHARED
    gk_hot_path.cpp
    register_types.cpp
)
target_link_libraries(gk_hot_path PRIVATE godot-cpp)

set_target_properties(gk_hot_path PROPERTIES
    PREFIX ""
    OUTPUT_NAME "gk_hot_path.windows.template_debug.x86_64"
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin"
)
set_target_properties(gk_hot_path PROPERTIES
    OUTPUT_NAME_RELEASE "gk_hot_path.windows.template_release.x86_64"
)
"""

BUILD_PS1 = """\
# 由 BlenderGameKit 生成的一键构建(Windows)
#   powershell -ExecutionPolicy Bypass -File build.ps1
param([string]$GodotCpp = "", [switch]$Release)

$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $GodotCpp) { $GodotCpp = $env:GODOT_CPP_PATH }
if (-not $GodotCpp) {
    Write-Output "找不到 godot-cpp。请先设置环境变量 GODOT_CPP_PATH,或用 -GodotCpp 指定"
    exit 1
}
New-Item -ItemType Directory -Force -Path (Join-Path $here "bin") | Out-Null
$target = if ($Release) { "template_release" } else { "template_debug" }
$env:GODOT_CPP_PATH = $GodotCpp
Push-Location $here
& python -m SCons platform=windows target=$target -j8
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { exit 1 }
Write-Output "完成:重启 Godot 编辑器后使用生成的热路径类。"
"""


def _render_readme(spec):
    names = " → ".join(_node_name(spec[key])
                       for key in ("event", "move", "flip", "jump")
                       if spec.get(key) is not None)
    removed = []
    for key in ("move", "flip", "jump"):
        node = spec.get(key)
        if node is not None:
            removed.append(f"- `{_node_name(node)}`({_label(node)})")
    if spec.get("condition") is not None:
        removed.append("- 跳跃分支及其条件(在地面 / 跳跃键 / 逻辑与)")
    if not removed:
        removed.append("- (没有需要拆除的节点)")
    action_rows = "\n".join(
        f"| {part} | `{action}` |"
        for part, action in spec["actions"].items())
    jump_action = spec["jump_action"] or "(没有跳跃节点)"
    return f"""\
# GDExtension 热路径(由 BlenderGameKit 从节点图生成)

生成来源:`{names}`

这是一个**伴随节点**,不是整份脚本的替代品:

- C++ 负责每物理帧的输入 → 移动 → 翻转 → 起跳;
- 节点图导出的 GDScript 继续负责其它事件(状态 / 动画 / 界面 / 相机跟随)。

## 1. 编译

前置:godot-cpp(官方最高分支是 `4.5`)+ SCons。

```powershell
# 只装一次
pip install scons
# godot-cpp 可以放在任何位置,用环境变量指过去
$env:GODOT_CPP_PATH = "D:\\godot-cpp"

powershell -ExecutionPolicy Bypass -File build.ps1
```

产物在 `native/bin/`。改完 C++ 必须重新编译,并**重启 Godot 编辑器**。

## 2. 在 Godot 里挂上

1. 在角色节点下面加一个 `{'GKHotPath2D' if spec['dimension'] == '2' else 'GKHotPath3D'}` 子节点;
2. `body_path` 保持 `..`(指向父节点 = 角色)或改成角色的路径;
3. 要翻转贴图就把 `flip_path` 填成精灵相对角色的路径(例如 `Model`);
4. 参数(速度 / 跳跃力 / 动作名)都可以在检查器里改。

## 3. 拆掉节点图里的移动链

否则 GDScript 和 C++ 会同时移动,速度翻倍 / 跳跃被覆盖。请从节点图里拆掉:

{chr(10).join(removed)}

拆完重新导出一次 GDScript,让热路径接管移动。

## 4. 这张图提取到的参数

| 参数 | 值 |
|---|---|
| 速度 | `{_num(spec['speed'])}` |
| 跳跃力 | `{_num(spec['jump_force'])}` |
| 重力模式 | `{'是' if spec['gravity_mode'] else '否'}` |
| 跳跃需要在地面 | `{'是' if spec['jump_requires_floor'] else '否'}` |
| 跳跃动作 | `{jump_action}` |

| 方向键 | 动作名 |
|---|---|
{action_rows}

> ⚠ 改节点图里的移动参数后,要重新生成一次 C++,再重新编译 ——
> 这份 C++ 不会自动跟着节点图变。
>
> ⚠ 一个 Godot 工程只放一份热路径:重新生成会覆盖 `native/` 下的同名文件。
"""


def generate(tree, dimension="3"):
    """生成热路径文件。返回 ({相对路径: 文本}, spec)。"""
    spec = detect(tree, dimension)
    dim = spec["dimension"]
    cls = "GKHotPath2D" if dim == "2" else "GKHotPath3D"
    body = "CharacterBody2D" if dim == "2" else "CharacterBody3D"
    vec = "Vector2" if dim == "2" else "Vector3"
    flip_type = "Node2D" if dim == "2" else "Node3D"
    dir_decl, velocity, rotate = _movement_lines(spec)
    if dim == "2":
        flip_scale = "Vector2(sx, flip_target->get_scale().y)"
    else:
        flip_scale = ("Vector3(sx, flip_target->get_scale().y, "
                      "flip_target->get_scale().z)")

    values = {
        "CLASS": cls,
        "GUARD": f"GK_HOT_PATH_{dim}D_H",
        "BASE_INCLUDE": "character_body2d.hpp" if dim == "2"
                        else "character_body3d.hpp",
        "FLIP_INCLUDE": "node2d.hpp" if dim == "2" else "node3d.hpp",
        "BODY_TYPE": body,
        "FLIP_TYPE": flip_type,
        "VEC_TYPE": vec,
        "SPEED": _num(spec["speed"]),
        "JUMP_FORCE": _num(spec["jump_force"]),
        "GRAVITY": "true" if spec["gravity_mode"] else "false",
        "JUMP_FLOOR": "true" if spec["jump_requires_floor"] else "false",
        "NORMALIZE": "true" if spec["normalize"] else "false",
        "DEADZONE": _num(spec["deadzone"]),
        "ACTION_LEFT": _cpp_string(spec["actions"]["left"]),
        "ACTION_RIGHT": _cpp_string(spec["actions"]["right"]),
        "ACTION_UP": _cpp_string(spec["actions"]["up"]),
        "ACTION_DOWN": _cpp_string(spec["actions"]["down"]),
        "ACTION_JUMP": _cpp_string(spec["jump_action"]),
        "DIR_DECL": dir_decl,
        "VELOCITY": velocity,
        "ROTATE": rotate,
        "FLIP_SCALE": flip_scale,
        "JUMP_SIGN": "-" if dim == "2" else "",
    }
    # C++ 这类模板里有大量花括号 —— 用 $ 占位,避免 .format 的转义地狱
    return {
        "native/gk_hot_path.h": HEADER_TEMPLATE.substitute(values),
        "native/gk_hot_path.cpp": CPP_TEMPLATE.substitute(values),
        "native/register_types.cpp": REGISTER_TEMPLATE.substitute(values),
        "native/gk_hot_path.gdextension": GODOT_EXTENSION,
        "native/SConstruct": SCONSTRUCT,
        "native/CMakeLists.txt": CMAKE,
        "native/build.ps1": BUILD_PS1,
        "native/README.md": _render_readme(spec),
    }, spec
