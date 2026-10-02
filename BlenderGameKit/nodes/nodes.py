"""GK 节点集合。

命名约定:
    gk_in_* / 引脚属性   未连线时引脚读哪个属性(见 gk_input_props)
    gk_cfg_*            纯配置，只画在节点体或侧栏

四大类，显示在"添加节点"菜单里:
    事件   程序入口(_ready / _process / _physics_process / _input / 自定义函数)
    动作   对节点和游戏状态做的事
    运算   纯取值表达式
    实用   流程与整理
"""

from . import node_base as B
from .node_base import finalize, gd_node_path, gd_string, snake_case, target_pin, TARGET_SELF
from .. import gk_sockets as S


# ==========================================================================
# 动作(Action)归属识别 —— "动画名下拉"的地基
# ==========================================================================
# 背景:用户踩过这个坑 —— 节点里动画名是默认的 `idle`,
# 而模型里的动作叫 `骨骼动作` / `形态键动作`,于是播不了。
#
# ── 为什么不能简单地把 bpy.data.actions 全列出来 ──────────────────
# `bpy.data.actions` 是**全局**的,不区分属于哪个角色。
# 场景里有两个角色时,动作会混在一个列表里,选错是迟早的事。
#
# ── 实测出来的归属依据(用户的胡桃模型)────────────────────────────
#
#     动作             id_root     slots
#     骨骼动作         OBJECT      ['胡桃-海灯节_arm']   <- 槽名就是骨架名
#     形态键动作       KEY         ['Key']
#
# Blender 4.4+ 的 slotted action 会把**绑定的物体名**记在 slot 上,
# 这是最可靠的归属依据。拿不到 slot 时退回名字匹配 + 类型判断。
# ==========================================================================

# id_root -> 这类动作该挂在什么类型的物体上
_ACTION_ROOT_TYPES = {
    "OBJECT": ("ARMATURE", "OBJECT", "MESH"),
    "KEY": ("MESH",),
}

# 用户可读的类别名(画在下拉列表里,帮他分辨)
_ACTION_KIND_LABEL = {
    "OBJECT": "骨骼/物体",
    "KEY": "形态键",
    "MATERIAL": "材质",
    "NODETREE": "节点",
    "PARTICLE": "粒子",
}


def _action_slot_names(action):
    """取一个 Action 的 slot 名字列表(拿不到就返回空)。"""
    slots = getattr(action, "slots", None)
    if not slots:
        return []
    out = []
    for slot in slots:
        name = getattr(slot, "name_display", None) or getattr(slot, "name", "")
        if name:
            out.append(str(name))
    return out


def action_belongs_to(action, obj):
    """这个动作是不是"可能属于"这个物体。用于给下拉列表排序/标注。

    判据按可靠性排序:
      1. slot 名字里含物体名(最强 —— Blender 自己就是这么记的)
      2. 动作的 id_root 和物体类型对得上
    """
    if action is None or obj is None:
        return False
    obj_name = str(getattr(obj, "name", ""))
    for slot_name in _action_slot_names(action):
        if obj_name and obj_name in slot_name:
            return True
    root = str(getattr(action, "id_root", "") or "")
    allowed = _ACTION_ROOT_TYPES.get(root, ())
    return str(getattr(obj, "type", "")) in allowed


def actions_on_object(obj):
    """**确凿**挂在这个物体上的动作(当前激活的 + NLA 里的 + 形态键的)。

    这是"一定对"的那一批,优先列给用户。
    """
    found = []
    if obj is None:
        return found

    def _add(action):
        if action is not None and action not in found:
            found.append(action)

    anim = getattr(obj, "animation_data", None)
    if anim is not None:
        _add(getattr(anim, "action", None))
        for track in getattr(anim, "nla_tracks", ()) or ():
            for strip in getattr(track, "strips", ()) or ():
                _add(getattr(strip, "action", None))

    # 形态键动画挂在 mesh data 的 shape_keys 上,不在物体自己的
    # animation_data 里 —— 漏掉这里用户的"形态键动作"就搜不到。
    data = getattr(obj, "data", None)
    shape_keys = getattr(data, "shape_keys", None)
    if shape_keys is not None:
        key_anim = getattr(shape_keys, "animation_data", None)
        if key_anim is not None:
            _add(getattr(key_anim, "action", None))
    return found


def discover_actions(obj=None):
    """给下拉列表用的动作清单:(标识, 显示名, 说明) 三元组序列。

    obj 给了的话,把"属于它"的动作排在前面并标上 ★。

    ── 标识为什么用 `名字` 而不是别的 ──────────────────────────────
    导出的 GDScript 里最终要的是**动画名**(Godot 的
    `AnimationPlayer.has_animation("骨骼动作")`)。所以这里直接用
    Action 的名字当枚举标识,节点上选什么、生成什么,一一对应,
    中间不做任何转换 —— 少一层映射就少一类 bug。
    """
    try:
        import bpy
    except Exception:
        return []

    # ── 测试桩里的 bpy 是个空壳(没有 .data)────────────────────────
    # 直接用 bpy.data.actions 会 AttributeError。离线测试跑的就是这条路,
    # 所以这里必须先确认 .data 真的存在 —— 否则"动作发现"会让整个
    # 离线测试台崩掉,而它本该只是拿不到数据而已。
    data = getattr(bpy, "data", None)
    if data is None:
        return []
    all_actions = getattr(data, "actions", None)
    if all_actions is None:
        return []

    owned = []
    if obj is not None:
        owned = [a.name for a in actions_on_object(obj)]

    entries = []
    for action in all_actions:
        name = str(action.name)
        root = str(getattr(action, "id_root", "") or "")
        kind = _ACTION_KIND_LABEL.get(root, root or "?")
        slot_names = _action_slot_names(action)
        owned_mark = "★ " if name in owned else ""
        detail = f"{kind}"
        if slot_names:
            detail += f" · {slot_names[0]}"
        entries.append((name, f"{owned_mark}{name}  ({detail})", name))

    # 属于当前物体的排前面(下拉列表长的时候,第一屏就能看到对的)
    entries.sort(key=lambda item: (0 if item[0] in owned else 1, item[1]))
    return entries


def check_action_target(node, obj):
    """检查"这个动作挂在这个物体上对不对"。返回一条警告文本或 None。

    ── 回答用户的问题:"如果应用动画物体错误了会怎么样" ─────────────
    **以前是静默失败** —— 导出的脚本在 Godot 里跑,AnimationPlayer
    找不到那个动画名,只在控制台打一条警告,画面看不出问题,
    用户根本不知道哪里错了。

    现在导出时就能发现。判据:
      * `id_root` 是 `KEY`(形态键动画)-> 必须挂在网格上
      * `id_root` 是 `OBJECT` -> 挂在骨架或物体上
      * 动作的 slot 名字里如果有别的物体名 -> 大概率选错了

    返回 None 表示没问题。
    """
    action = getattr(node, "gk_anim_action", None)
    if action is None or obj is None:
        return None

    obj_type = str(getattr(obj, "type", ""))
    root = str(getattr(action, "id_root", "") or "")
    allowed = _ACTION_ROOT_TYPES.get(root)

    if allowed and obj_type not in allowed:
        want = "/".join(allowed)
        return (f"节点『{node.bl_label}』选的动画『{action.name}』是 "
                f"{_ACTION_KIND_LABEL.get(root, root)} 动画,"
                f"应该挂在 {want} 上,但现在挂的是 "
                f"{obj.name}({obj_type})—— 播放时会找不到动画")

    # slot 名字里出现了**别的**物体名 -> 多半是选串了
    own_name = str(getattr(obj, "name", ""))
    for slot_name in _action_slot_names(action):
        if own_name and own_name in slot_name:
            return None      # 明确属于这个物体,没问题
    others = [n for n in _action_slot_names(action) if n and n != "Key"]
    if others and own_name and own_name not in others[0]:
        return (f"节点『{node.bl_label}』选的动画『{action.name}』"
                f"看起来是属于『{others[0]}』的,不是『{own_name}』"
                f"—— 确认一下是不是选错了")
    return None


def action_enum_items(_self=None, _context=None):
    """动态枚举回调:列出场景里所有动作。

    没绑定到具体物体 —— Blender 的 EnumProperty 回调拿不到节点实例,
    所以这里列**全部**,靠标签上的 ★ 和"(类型 · 归属)"帮用户分辨。
    导出时还会再校验一次(见 gk_anim_name 的检查)。
    """
    entries = discover_actions(None)
    if not entries:
        return [("", "(这个文件里还没有任何动画)", "")]
    return entries
from ..gk_sockets import ANY, BOOL, COLOR, FLOAT, FLOW, INT, OBJECT, TEXT, VECTOR, VECTOR2

CLASSES = []


def N(cls):
    """登记节点类并物化它的声明式属性。"""
    CLASSES.append(finalize(cls))
    return cls


# ==========================================================================
# 事件
# ==========================================================================
@N
class GKNodeOnReady(B.GKEventNode):
    bl_idname = "GKNodeOnReady"
    bl_label = "每当_准备"
    bl_icon = "PLAY"
    gk_category = "事件"
    gk_doc = "游戏开始时执行一次，对应 Godot 的 _ready()"
    gk_outputs = (S.pin(FLOW, "执行"),)


@N
class GKNodeOnProcess(B.GKEventNode):
    bl_idname = "GKNodeOnProcess"
    bl_label = "每当_每帧"
    bl_icon = "TIME"
    gk_category = "事件"
    gk_doc = "每一帧执行，对应 Godot 的 _process(delta)"
    gk_prop_defs = (("gk_order", "int", 0, "排序", "多个每帧事件时的执行顺序"),)
    gk_outputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "帧间隔", None))


@N
class GKNodeOnPhysicsProcess(B.GKEventNode):
    bl_idname = "GKNodeOnPhysicsProcess"
    bl_label = "每当_物理帧"
    bl_icon = "PHYSICS"
    gk_category = "事件"
    gk_doc = "固定频率执行(默认每秒 60 次)，对应 _physics_process(delta)"
    gk_prop_defs = (("gk_order", "int", 0, "排序", "多个物理帧事件时的执行顺序"),)
    gk_outputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "帧间隔", None))


@N
class GKNodeOnInput(B.GKEventNode):
    bl_idname = "GKNodeOnInput"
    bl_label = "每当_输入"
    bl_icon = "HAND"
    gk_category = "事件"
    gk_doc = "按键 / 鼠标事件，对应 Godot 的 _input(event)"
    gk_outputs = (S.pin(FLOW, "执行"), S.pin(ANY, "输入事件", None))


@N
class GKNodeFunction(B.GKEventNode):
    bl_idname = "GKNodeFunction"
    bl_label = "自定义函数"
    bl_icon = "FILE_SCRIPT"
    gk_category = "事件"
    gk_doc = "定义一个可被其它节点调用、也能接信号的函数"
    gk_prop_defs = (("gk_func_name", "string", "my_function", "函数名", "生成的 func 名称"),)
    gk_outputs = (S.pin(FLOW, "执行"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_func_name", text="")


@N
class GKNodeCallFunction(B.GKStatementNode):
    bl_idname = "GKNodeCallFunction"
    bl_label = "调用_函数"
    bl_icon = "FILE_REFRESH"
    gk_category = "事件"
    gk_doc = "调用函数(本脚本的自定义函数，或目标节点上的方法)"
    gk_inputs = (S.pin(FLOW, "执行"), target_pin())
    gk_prop_defs = (
        ("gk_func_name", "string", "my_function", "函数名", "要调用的方法名"),
        ("gk_target", "node", TARGET_SELF, "目标", "留空表示本节点"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_func_name", text="")
        row.prop(self, "gk_target", text="")


# ==========================================================================
# 流程控制
# ==========================================================================
@N
class GKNodeBranch(B.GKStatementNode):
    bl_idname = "GKNodeBranch"
    bl_label = "如果_否则"
    bl_icon = "ARROW_LEFTRIGHT"
    gk_category = "流程"
    gk_doc = "条件分支:条件为真走『是』出口，为假走『否』出口"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(BOOL, "条件", False))
    gk_outputs = (S.pin(FLOW, "是"), S.pin(FLOW, "否"))


@N
class GKNodeDoOnce(B.GKStatementNode):
    bl_idname = "GKNodeDoOnce"
    bl_label = "只执行一次"
    bl_icon = "LOCKED"
    gk_category = "流程"
    gk_doc = "第一次到达时执行，之后自动跳过"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(BOOL, "重置", False))
    gk_outputs = (S.pin(FLOW, "首次"),)


@N
class GKNodeSequence(B.GKStatementNode):
    bl_idname = "GKNodeSequence"
    bl_label = "序列"
    bl_icon = "LINENUMBERS_ON"
    gk_category = "流程"
    gk_doc = "依次执行多个出口(出口数量可调)"
    gk_prop_defs = (("gk_step_count", "int", 3, "出口数量", "1~8 个出口"),)
    # 出口引脚数量由 gk_step_count 决定，在 build_sockets 里动态生成
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_outputs = (S.pin(FLOW, "1"), S.pin(FLOW, "2"), S.pin(FLOW, "3"))

    def build_sockets(self):
        count = max(1, min(8, int(getattr(self, "gk_step_count", 3) or 3)))
        self.inputs.clear()
        for spec in self.gk_inputs:
            socket = self.inputs.new(spec["kind"][0], spec["name"])
            if spec["default"] is not None:
                socket.default_value = spec["default"]
        self.outputs.clear()
        for index in range(count):
            self.outputs.new(S.FLOW[0], str(index + 1))
        self.gk_socket_cache = (len(self.inputs), len(self.outputs))

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_step_count", text="出口")


@N
class GKNodeSwitch(B.GKStatementNode):
    bl_idname = "GKNodeSwitch"
    bl_label = "多路分支"
    bl_icon = "SORT_ASC"
    gk_category = "流程"
    gk_doc = ("按一个整数走不同出口。出口数量可调(1~12),"
              "改数字引脚会立刻长出来 —— 这就是蓝图那种'自定义分支数'的节点。"
              "值不匹配任何一条时走『默认』")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(INT, "值", 0))
    # 出口在 build_sockets 里按 gk_case_count 动态生成,这里只是默认值
    gk_outputs = (S.pin(FLOW, "0"), S.pin(FLOW, "1"),
                  S.pin(FLOW, "默认"))
    gk_prop_defs = (
        # 属性名里带 "count" -> node_base 会自动给它挂上"重建引脚"的回调
        ("gk_case_count", "int", 2, "出口数量", "1~12 条分支",
         None, {"min": 1, "max": 12}),
    )

    def build_sockets(self):
        """按 gk_case_count 重建出口。

        出口名就是**要匹配的整数值**,最后一条固定叫『默认』。
        用名字当匹配值(而不是另开一堆属性)是为了:
          * 用户在节点上直接看到"这条出口对应哪个值"
          * 增减出口时不用同步维护另一个数组属性
        """
        count = max(1, min(12, int(getattr(self, "gk_case_count", 2) or 2)))
        self.inputs.clear()
        for spec in self.gk_inputs:
            socket = self.inputs.new(spec["kind"][0], spec["name"])
            if spec["default"] is not None:
                try:
                    socket.default_value = spec["default"]
                except Exception:
                    pass
        self.outputs.clear()
        for index in range(count):
            self.outputs.new(S.FLOW[0], str(index))
        self.outputs.new(S.FLOW[0], "默认")

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_case_count", text="分支数")


@N
class GKNodeForLoop(B.GKStatementNode):
    bl_idname = "GKNodeForLoop"
    bl_label = "循环_次数"
    bl_icon = "FILE_REFRESH"
    gk_category = "流程"
    gk_doc = "把循环体执行指定次数"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(INT, "次数", 3))
    gk_outputs = (S.pin(FLOW, "循环体"), S.pin(FLOW, "结束后"))
    gk_prop_defs = (("gk_index_name", "string", "i", "索引变量", "循环索引变量名"),)


@N
class GKNodeWait(B.GKStatementNode):
    bl_idname = "GKNodeWait"
    bl_label = "等待_秒"
    bl_icon = "MOD_TIME"
    gk_category = "流程"
    gk_doc = "延迟一段时间再继续(协程等待，不卡住游戏)"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "秒", 1.0))
    gk_prop_defs = (("gk_use_process", "bool", False, "使用真实时间", "忽略引擎暂停"),)


@N
class GKNodePrint(B.GKStatementNode):
    bl_idname = "GKNodePrint"
    bl_label = "打印_输出"
    bl_icon = "CONSOLE"
    gk_category = "流程"
    gk_doc = "在 Godot 的输出面板打印一行文本"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(TEXT, "内容", "你好，世界"))
    gk_input_props = {"内容": "gk_text"}
    gk_prop_defs = (("gk_text", "string", "你好，世界", "内容", "未连线时打印的字面量"),)


# ==========================================================================
# 移动
# ==========================================================================
@N
class GKNodeSetVelocity(B.GKStatementNode):
    bl_idname = "GKNodeSetVelocity"
    bl_label = "移动_设置速度"
    bl_icon = "FORWARD"
    gk_category = "移动"
    gk_doc = "设置速度向量(只改速度不做位移，通常后面接『移动_滑动』)"
    gk_inputs = (S.pin(FLOW, "执行"), target_pin(), S.pin(VECTOR, "速度", (0.0, 0.0, 0.0)))
    gk_input_props = {"速度": "gk_velocity"}
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "目标", "要移动的节点"),
        ("gk_velocity", "float", 0.0, "速度(Y)", "未连线时写入的速度"),
    )

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)


@N
class GKNodeMoveAndSlide(B.GKStatementNode):
    bl_idname = "GKNodeMoveAndSlide"
    bl_label = "移动_滑动"
    bl_icon = "TRACKER"
    gk_category = "移动"
    gk_doc = "按当前速度移动并处理碰撞，对应 move_and_slide()"
    gk_inputs = (S.pin(FLOW, "执行"), target_pin())
    gk_prop_defs = (("gk_target", "node", TARGET_SELF, "目标", "要移动的节点"),)

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)


@N
class GKNodeMoveDirection(B.GKStatementNode):
    bl_idname = "GKNodeMoveDirection"
    bl_label = "移动_按方向"
    bl_icon = "ORIENTATION_GLOBAL"
    gk_category = "移动"
    gk_doc = ("沿指定方向移动。**平台跳跃 / 有重力的游戏务必勾上『重力模式』**："
              "Godot 4 的 CharacterBody 不会自动施加重力，不勾角色会浮在原地不动。\n"
              "『前进轴』『横移轴』决定输入往哪个世界轴走 —— 按着不对就翻一下"
              "(和 UPBGE 里设移动轴是一回事)")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        target_pin(),
        S.pin(VECTOR, "方向", (0.0, 0.0, 0.0)),
        S.pin(FLOAT, "速度", 300.0),
    )
    gk_input_props = {"速度": "gk_speed"}
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "目标", "要移动的节点"),
        ("gk_dir_x", "float", 0.0, "方向 X", ""),
        ("gk_dir_y", "float", 0.0, "方向 Y", ""),
        ("gk_speed", "float", 300.0, "速度", "未连线时的速度"),
        # ── 输入往哪个世界轴走(用户要求:像 UPBGE 那样能设)──────────
        # 默认 XZ 平面、前进走 -Z(Godot / glTF 的"前")。
        # 实测踩过:相机朝向不同时,"按前往后、按左往右"完全可能 ——
        # 这不该逼用户去改脚本,给个选项翻过来就行。
        # 2D 项目用不到(只有 X 轴),这两项会被忽略。
        ("gk_axis_h", "enum", "X", "横移轴",
         "左右输入作用在哪个世界轴上",
         (("X", "X (左右)", ""), ("Z", "Z (前后)", ""), ("Y", "Y (上下)", ""))),
        ("gk_axis_v", "enum", "Z", "前进轴",
         "前后输入作用在哪个世界轴上",
         (("Z", "Z (前后)", ""), ("X", "X (左右)", ""), ("Y", "Y (上下)", ""))),
        ("gk_forward_sign", "enum", "NEG", "前进方向",
         "按『前』时沿前进轴的正向还是负向走。默认负向 = Godot / glTF 的『前』",
         (("NEG", "负 (Godot 的『前』)", ""), ("POS", "正", ""))),
        # ── 为什么需要这个开关 ────────────────────────────────────────
        # Godot 4 的 CharacterBody2D/3D **不会自动施加重力**，它只按你设的
        # velocity 移动。所以平台跳跃游戏必须自己累加重力，否则角色
        # 会永远浮在原地(实测:velocity 恒为 (0,0)、is_on_floor 恒 false，
        # 看起来就像"脚本没生效")。
        #
        # 打开这个开关会同时做两件事:
        #   1) 每帧累加 get_gravity()
        #   2) 只改水平速度，纵向留给重力
        # 俯视角游戏(不需要重力)保持关闭即可。
        # 默认关:保持既有工程行为不变，避免"升级后手感突然变了"。
        ("gk_keep_y", "bool", False, "重力模式",
         "平台跳跃 / 有重力的游戏请勾上:自动施加重力，且只改水平速度"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        B.draw_target_picker(self, row)
        row.prop(self, "gk_speed", text="")
        layout.prop(self, "gk_keep_y", text="重力模式(平台游戏勾上)", toggle=True)

    def draw_buttons_ext(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_dir_x")
        row.prop(self, "gk_dir_y")


@N
class GKNodeJump(B.GKStatementNode):
    bl_idname = "GKNodeJump"
    bl_label = "移动_跳跃"
    bl_icon = "TRIA_UP"
    gk_category = "移动"
    gk_doc = "设置纵向速度实现跳跃"
    gk_inputs = (S.pin(FLOW, "执行"), target_pin(), S.pin(FLOAT, "强度", 400.0))
    gk_input_props = {"强度": "gk_jump_force"}
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "目标", "要跳跃的节点"),
        ("gk_jump_force", "float", 400.0, "强度", "未连线时的跳跃初速度"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        B.draw_target_picker(self, row)
        row.prop(self, "gk_jump_force", text="")


@N
class GKNodeAddImpulse(B.GKStatementNode):
    bl_idname = "GKNodeAddImpulse"
    bl_label = "移动_施加冲量"
    bl_icon = "FORCE_FORCE"
    gk_category = "移动"
    gk_doc = "对刚体施加冲量(对应 apply_impulse)"
    gk_inputs = (S.pin(FLOW, "执行"), target_pin(), S.pin(VECTOR, "冲量", (0.0, -200.0, 0.0)))
    gk_prop_defs = (("gk_target", "node", TARGET_SELF, "目标", "刚体节点"),)

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)


@N
class GKNodeSetRotation(B.GKStatementNode):
    bl_idname = "GKNodeSetRotation"
    bl_label = "移动_设置朝向"
    bl_icon = "ORIENTATION_LOCAL"
    gk_category = "移动"
    gk_doc = "设置节点朝向(2D 用弧度转 rotation，3D 用 rotation_degrees.y)"
    gk_inputs = (S.pin(FLOW, "执行"), target_pin(), S.pin(FLOAT, "角度", 0.0), S.pin(INT, "轴", 1))
    gk_input_props = {"角度": "gk_angle", "轴": "gk_axis"}
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "目标", "要转向的节点"),
        ("gk_angle", "float", 0.0, "角度", "未连线时的角度"),
        ("gk_axis", "enum", "Y", "轴", "3D 里绕哪根轴旋转", (("X", "X", ""), ("Y", "Y", ""), ("Z", "Z", ""))),
        ("gk_degrees", "bool", True, "使用角度制", "关闭则为弧度制"),
    )

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)


@N
class GKNodeFlip(B.GKStatementNode):
    bl_idname = "GKNodeFlip"
    bl_label = "移动_水平翻转"
    bl_icon = "ARROW_LEFTRIGHT"
    gk_category = "移动"
    gk_doc = "按朝向翻转贴图，二次元横版角色常用"
    gk_inputs = (S.pin(FLOW, "执行"), target_pin(), S.pin(BOOL, "朝左", False))
    gk_input_props = {"朝左": "gk_face_left"}
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "目标", "要做翻转的精灵节点"),
        ("gk_mode", "enum", "AUTO", "翻转方式", "默认按速度方向自动判断", (
            ("AUTO", "自动(按速度方向)", ""),
            ("LEFT", "始终朝左", ""),
            ("RIGHT", "始终朝右", ""),
        )),
        ("gk_face_left", "bool", False, "朝左", "『朝左』引脚未连线且方式为手动时使用"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        B.draw_target_picker(self, row)
        row.prop(self, "gk_mode", text="")


# ==========================================================================
# 动画
# ==========================================================================
@N
class GKNodePlayAnim(B.GKStatementNode):
    bl_idname = "GKNodePlayAnim"
    bl_label = "动画_播放"
    bl_icon = "PLAY"
    gk_category = "动画"
    gk_doc = ("播放动画片段(对应 AnimationPlayer.play)。"
              "『动画播放器』留空 = 自动找:glTF 导入时 AnimationPlayer 藏在"
              "模型实例内部,自动找会自动把它挖出来,不用你填路径。"
              "── 右侧三个事件出口 ──"
              "『开始时』= 动画刚开始那一刻;"
              "『每帧』= 播放期间每一帧(做持续判定用);"
              "『结束时』= 播完了那一刻(做连招、循环用)。"
              "只对连了线的出口生成代码,没连就没有开销")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "动画名", "idle"),
        target_pin("动画播放器"),
    )
    # ── 三个事件出口(照 UPBGE 的 Play Animation 设计)────────────────
    # UPBGE 那个节点最值钱的就是 Started / Running / On Finish ——
    # 有它们才能做"播完接下一招""循环期间持续判定"。
    # 我们以前只有"完成",接不了任何后续判断。
    #
    # 顺序:"完成"在前(沿用原来的默认出口,老图不受影响),
    # 然后依次是三个事件出口 —— 视觉上从上到下就是 UPBGE 那个排列。
    gk_outputs = (
        S.pin(FLOW, "完成"),
        S.pin(FLOW, "开始时"),
        S.pin(FLOW, "每帧"),
        S.pin(FLOW, "结束时"),
    )
    gk_input_props = {"动画名": "gk_anim_name", "动画播放器": "gk_player"}
    gk_prop_defs = (
        # 默认必须是**空**(=自动查找)。以前默认写的是字面量 "AnimationPlayer",
        # 于是生成 `$AnimationPlayer` —— 那是个相对路径,而 glTF 导入时
        # AnimationPlayer 在 Model/ 下面,路径必然找不到,脚本直接崩。
        ("gk_player", "node", "", "动画播放器",
         "留空 = 自动查找(推荐);也可以填场景里的节点名"),
        # ── 动画名:文本框 + 『选…』按钮,不是下拉 ─────────────────────
        # 想做成 EnumProperty 下拉,但 Blender 不允许:
        #     TypeError: EnumProperty(...): 'default' can only be an
        #                integer when 'items' is a function
        # 动态 items 的枚举**默认值只能是整数下标**,而我们这里要的是
        # 动作名字符串,两边对不上。
        #
        # 所以改成:文本框(名字能直接看到) + 『选…』按钮弹列表。
        # 按钮弹出的列表由动作归属分析生成,属于当前物体的排在前面并带 ★。
        #
        # 默认值留空 —— 以前默认 `idle` 是个**假名字**,用户不动它就会
        # 一直播不出来。空值会让导出的脚本报"动画不存在,可用的是…",
        # 比一个看起来合理但永远错的名字好。
        ("gk_anim_name", "string", "", "动画名",
         "不用管它 —— 上面选了『动作』就会自动同步成动作名"),
        # ── 原生动作选择器(用户要的"自动列出它的动作")────────────────
        # 这是**正解**:`PointerProperty(type=Action)` 拿到的是 Blender
        # 原生的数据块选择器(带放大镜、能搜、能浏览全部动作)。
        # 之前试的 EnumProperty 动态下拉被 Blender 挡了
        # (动态 items 的默认值只能是整数,而我们要字符串),
        # 『选…』按钮又需要用户手点一下。见 node_base.prop_action 的说明。
        #
        # 填了这个就**优先用它**,下面的文本框退化成"手填兜底"。
        ("gk_anim_action", "action", None, "动作",
         "从文件里所有动作中选一个(原生选择器,能搜索)"),
        ("gk_speed", "float", 1.0, "播放速度", "1.0 为原速"),
    )

    def draw_buttons(self, context, layout):
        # ── 主入口:原生动作选择器 ──────────────────────────────────
        # 用户反馈"再在下面的那个 idle 是不是多余了,这个又不能改什么,
        # 为啥不去掉" —— 对,那个文本框以前顶着个假默认值 `idle`,
        # 看着能改其实改了也没用(上面选了动作就以动作为准)。
        #
        # 现在:**选了动作就只显示动作名(只读),不显示文本框**。
        # 只有"没选动作"时才给你一个手填的框 —— 那时它才有意义。
        layout.prop(self, "gk_anim_action", text="")
        if self.gk_anim_action is not None:
            # 选了动作 -> 只**显示**名字,不再给一个能改但改了没用的框
            layout.label(text=f"动画名:{self.gk_anim_action.name}",
                         icon="CHECKMARK")
        else:
            # 没选动作 -> 给手填框 + 『选…』按钮。
            # 按钮那个列表会**按归属排序**(属于当前物体的带 ★ 排前面),
            # 比原生选择器更好找 —— 原生选择器只能列全部。
            row = layout.row(align=True)
            row.prop(self, "gk_anim_name", text="", icon="SORTALPHA")
            picker = row.operator("gk.pick_anim_name", text="", icon="VIEWZOOM")
            picker.node_name = self.name
        layout.prop(self, "gk_speed", text="播放速度")

    def draw_buttons_ext(self, context, layout):
        layout.label(text="动画播放器:留空 = 自动查找(推荐)")
        B.draw_target_picker(self, layout, "gk_player")


# ==========================================================================
# 动画系统节点 —— 属于**独立的动画树**(GKAnimationTree)
# ==========================================================================
# 用户原话:"这个可以按照虚幻5蓝图的来做,或者建立一个动作系统,像虚幻5
# 那样,然后就像 upbge 的逻辑砖调用,而不是直接在这来进行,不然,
# 后面就密密麻麻的(这也是 upbge 的缺点,虚幻5的分开得还好)"
#
# 他说得对。第一版把状态机整个塞进一个节点(状态表多行文本 + 一堆动态
# 引脚),那就是 UPBGE 的老毛病 —— 东西一多节点就糊成一团。
#
# 改成照虚幻 5 的 **Animation Blueprint** 那样:
#
#     游戏逻辑树(写玩法)          动画树(写动作,独立一张图)
#     ┌──────────────┐            ┌──────────────────────┐
#     │ 每当_准备     │            │ [状态: idle]          │
#     │   动画_状态机 ├──引用─────>│ [状态: run]  条件引脚… │
#     └──────────────┘            │ [状态: jump]          │
#                                 └──────────────────────┘
#
# 玩法那边只留**一个**引用节点,动作全在另一张图里,两边都清爽。
#
# ── 条件怎么表达 ────────────────────────────────────────────────
# 每个状态节点上有：
#   * 一个布尔**条件引脚** —— 接表达式节点,为真时切到它
#   * 一个『优先级』整数 —— 同时为真时谁赢(数字小的先判)
#   * 一个『目标』布尔 —— 勾上表示这是**起始状态**
# 转移图就是"哪条条件先为真去哪",不需要单独画线。
_ANIM_TREE_HINT = (
    "这个节点属于**动画树**(不是游戏逻辑树)。"
    "在节点编辑器左上角把类型切到『游戏动画』,或者点『游戏动画』面板里的"
    "『打开动画树』"
)


@N
class GKNodeAnimState(B.GKStatementNode):
    bl_idname = "GKNodeAnimState"
    bl_label = "动画状态"
    bl_icon = "POSE_HLT"
    gk_category = "动画系统"
    gk_tree_type = "GKAnimationTree"
    gk_doc = ("一个动画状态。状态名就是动画名。"
              "把条件接到右边那个布尔引脚上 —— 条件为真就切到这个状态;"
              "都没接或都为假时停在当前状态。"
              "『优先级』小的先判(同时为真时用它决定谁赢)")
    gk_inputs = (
        S.pin(BOOL, "条件", False),
    )
    gk_outputs = ()
    gk_prop_defs = (
        ("gk_state_name", "string", "idle", "状态名", "就是动画名"),
        ("gk_priority", "int", 0, "优先级", "数字小的先判;同时为真时它赢"),
        ("gk_is_default", "bool", False, "起始状态",
         "勾上 = 进游戏时的默认状态(整个树里只该勾一个)"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_state_name", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_priority", text="优先")
        row.prop(self, "gk_is_default", text="起始")


@N
class GKNodeAnimStateMachineRef(B.GKStatementNode):
    bl_idname = "GKNodeAnimStateMachineRef"
    bl_label = "动画_状态机"
    bl_icon = "AUTOMERGE_ON"
    gk_category = "动画"
    gk_doc = ("驱动一张**动画树**(在『游戏动画』面板里建)。"
              "动作的切换规则全写在动画树里,这里只负责启动它 —— "
              "玩法图和动画图分开放,两张图都不会糊成一团。"
              "『树名』留空 = 用文件里第一张动画树")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "树名", ""),
        target_pin("动画播放器"),
    )
    gk_input_props = {"树名": "gk_tree_name", "动画播放器": "gk_player"}
    gk_outputs = (S.pin(FLOW, "完成"),)
    gk_prop_defs = (
        ("gk_tree_name", "string", "", "树名",
         "留空 = 用文件里第一张动画树"),
        ("gk_player", "node", "", "动画播放器", "留空 = 自动查找"),
        ("gk_state_var", "string", "anim_state", "状态变量名",
         "当前状态存在哪个成员变量里"),
        ("gk_check_mode", "enum", "EVERY_FRAME", "检查时机",
         "每个物理帧判一次 / 只在开始时判一次",
         (("EVERY_FRAME", "每帧检查", "动作连贯,适合走跑切换"),
          ("ON_ENTER", "只在开始时判", "省性能,适合一次性判定"))),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_tree_name", text="", icon="NODETREE")
        layout.prop(self, "gk_check_mode", text="")

# 用户要求:"做动画状态机取代手拼分支网" —— 这也是 UPBGE 那套最大的缺陷
# (它一次只能播一个动画,没有状态机)。
#
# ── 为什么转移表用**多行文本**而不是可编辑列表 ────────────────────
# 理想的 UI 是像检查器那样的可增删列表,那需要 `CollectionProperty`
# + 一个 `PropertyGroup` 类 + 一个 UIList。试下来的两个硬障碍:
#
#   1. `PropertyGroup` 必须自己注册成 Blender 类,而节点类是由
#      `finalize()` 统一登记的 —— 两者注册时机和通道不一样,
#      混在一起很容易"节点注册成功但属性组没注册",报一堆难懂的错。
#   2. 在节点体里画可增删列表要用 `UILayout.template_list`,而它需要一个
#      **PointerProperty 指向自身**才能定位列表容器 —— 节点类型上挂
#      PointerProperty 在 Blender 里是受限的。
#
# 所以改用多行文本。一行一条规则,格式故意做得极简:
#
#     idle -> run        当『有输入』为真时,idle 切到 run
#     run -> idle        条件是节点上的布尔引脚
#
# 好处:**看得出全部规则、能复制粘贴、能整块注释掉、diff 干净**。
# 代价:没有下拉选择状态名 —— 但状态名本来就是用户自己的字符串,
# 手打反而更自由(而且打错了导出时会报"目标状态没有对应的动画")。
@N
class GKNodeAnimStateMachine(B.GKStatementNode):
    bl_idname = "GKNodeAnimStateMachine"
    bl_label = "动画_状态机"
    bl_icon = "AUTOMERGE_ON"
    gk_category = "动画"
    gk_doc = (
        "把 idle / run / jump / attack 这些状态的切换收进一个节点,"
        "不用手拼一堆分支。"
        "『状态表』一行一条规则,格式:`起始状态 -> 目标状态`;"
        "节点上会**自动长出一条对应的布尔引脚**,接一个条件上去 —— "
        "条件为真时就切过去。"
        "例:`idle -> run` 会生成一个叫『idle→run』的引脚。"
        "『每帧检查』勾上时每个物理帧都判一次条件,否则只在有输入时判"
    )
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(TEXT, "起始状态", "idle"))
    gk_outputs = (S.pin(FLOW, "完成"),)
    gk_prop_defs = (
        ("gk_states", "string", "idle\nrun", "状态列表",
         "这个角色有哪些状态,一行一个(只是给『选…』按钮和导出的常量用)"),
        ("gk_transitions", "string", "idle -> run\nrun -> idle", "状态表",
         "一行一条规则,格式:`起始状态 -> 目标状态`"),
        ("gk_player", "node", "", "动画播放器", "留空 = 自动查找"),
        ("gk_check_mode", "enum", "EVERY_FRAME", "检查时机",
         "每个物理帧判一次 / 只在切换后判一次",
         (("EVERY_FRAME", "每帧检查", "动作连贯,适合走跑切换"),
          ("ON_ENTER", "只在进入时判", "省性能,适合一次性判定"))),
        ("gk_state_var", "string", "anim_state", "状态变量名",
         "当前状态存在哪个成员变量里"),
    )

    def build_sockets(self):
        """按状态表动态生成条件引脚。

        每条规则一个布尔输入引脚,名字就是 `起始→目标`。
        """
        self.inputs.clear()
        for spec in self.gk_inputs:
            socket = self.inputs.new(spec["kind"][0], spec["name"])
            if spec["default"] is not None:
                try:
                    socket.default_value = spec["default"]
                except Exception:
                    pass
        self.outputs.clear()
        for spec in self.gk_outputs:
            socket = self.outputs.new(spec["kind"][0], spec["name"])
            if spec["default"] is not None:
                try:
                    socket.default_value = spec["default"]
                except Exception:
                    pass
        for rule in parse_transitions(getattr(self, "gk_transitions", "")):
            pin_name = transition_pin_name(rule[0], rule[1])
            if self.inputs.get(pin_name) is not None:
                continue      # 重复规则只建一个引脚
            socket = self.inputs.new(S.BOOL[0], pin_name)
            try:
                socket.default_value = False
            except Exception:
                pass

    def draw_buttons(self, context, layout):
        col = layout.column(align=True)
        col.label(text="状态表(一行:`起始 -> 目标`)")
        col.prop(self, "gk_transitions", text="")
        col.prop(self, "gk_check_mode", text="")
        rules = parse_transitions(getattr(self, "gk_transitions", ""))
        if rules:
            col.label(text=f"已生成 {len(rules)} 条转移", icon="INFO")
        else:
            col.label(text="还没有规则", icon="ERROR")


def parse_transitions(text):
    """把状态表文本解析成 [(起始, 目标), ...]。

    宽容处理:支持 `->` 和 `→`,忽略空行和 `#` 开头的注释行。
    解析不出来就跳过那一行,不抛异常 —— 用户正在打字时半截规则
    (只有 `idle ->`)不该让整个节点构建失败。
    """
    rules = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for arrow in ("->", "→"):
            if arrow in line:
                left, _sep, right = line.partition(arrow)
                start, end = left.strip(), right.strip()
                if start and end:
                    rules.append((start, end))
                break
    return rules


def transition_pin_name(start, end):
    """转移规则对应的引脚名。用箭头连接,用户一眼能对上。"""
    return f"{start}→{end}"


# ── 动画的其余常用操作 ──────────────────────────────────────────────
# 用户原话:"怎么测试骨骼和动画,这两个比较重要"。
# 实测(建了带骨架的角色,导出到 Godot 真跑)确认整条链路是通的:
#     Body.glb 里 animations=['RigAction'] skins=1
#     Godot 导入后 Hero/Model/AnimationPlayer,骨骼数=2,播放位置在推进,
#     骨骼旋转逐帧变化 —— 动画真的在驱动骨骼。
# 但当时节点图里只有『播放』和『切换状态』两个节点,做个游戏根本不够用,
# 所以把最常用的几个补齐。
@N
class GKNodeAnimPlaying(B.GKExpressionNode):
    bl_idname = "GKNodeAnimPlaying"
    bl_label = "动画_是否在播"
    bl_icon = "TIME"
    gk_category = "动画"
    gk_doc = ("某个动画片段是否正在播放(对应 AnimationPlayer.is_playing 组合"
              "current_animation)。留空动画名 = 只要有动画在播就算真")
    gk_prop_defs = (
        ("gk_player", "node", "", "动画播放器", "留空 = 自动查找"),
        ("gk_anim_name", "string", "", "动画名", "留空 = 任意动画"),
    )
    gk_outputs = (S.pin(BOOL, "在播", False),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_anim_name", text="")


@N
class GKNodeAnimPause(B.GKStatementNode):
    bl_idname = "GKNodeAnimPause"
    bl_label = "动画_暂停/继续"
    bl_icon = "PAUSE"
    gk_category = "动画"
    gk_doc = ("暂停或继续当前动画。暂停用 AnimationPlayer.pause()"
              "(停在当前位置,姿势保持不动);继续会从当前位置接着播。"
              "注:Godot 4.7 的 AnimationPlayer **没有 paused 属性**,"
              "所以这里用的是 pause() / play()")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_player", "node", "", "动画播放器", "留空 = 自动查找"),
        ("gk_paused", "bool", True, "暂停", "勾上=暂停,取消=继续"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


@N
class GKNodeAnimStop(B.GKStatementNode):
    bl_idname = "GKNodeAnimStop"
    bl_label = "动画_停止"
    bl_icon = "CANCEL"
    gk_category = "动画"
    gk_doc = "停止播放(对应 AnimationPlayer.stop),角色会回到静止姿势"
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (("gk_player", "node", "", "动画播放器", "留空 = 自动查找"),)
    gk_outputs = (S.pin(FLOW, "完成"),)


@N
class GKNodeAnimSetSpeed(B.GKStatementNode):
    bl_idname = "GKNodeAnimSetSpeed"
    bl_label = "动画_设播放速度"
    bl_icon = "DRIVER"
    gk_category = "动画"
    gk_doc = ("改当前动画的播放速度(对应 AnimationPlayer.speed_scale)。"
              "0.5 = 慢动作,2.0 = 快放")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "速度", 1.0))
    gk_input_props = {"速度": "gk_speed"}
    gk_prop_defs = (
        ("gk_player", "node", "", "动画播放器", "留空 = 自动查找"),
        ("gk_speed", "float", 1.0, "速度", "1.0 为原速"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_speed", text="")


@N
class GKNodeAnimLength(B.GKExpressionNode):
    bl_idname = "GKNodeAnimLength"
    bl_label = "动画_时长"
    bl_icon = "FULLSCREEN_ENTER"
    gk_category = "动画"
    gk_doc = ("某个动画片段有多长(秒)。做连招/计时的时候要先知道它多久。"
              "动画名不存在时返回 0")
    gk_prop_defs = (
        ("gk_player", "node", "", "动画播放器", "留空 = 自动查找"),
        ("gk_anim_name", "string", "idle", "动画名", ""),
    )
    gk_outputs = (S.pin(FLOAT, "秒", 0.0),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_anim_name", text="")


@N
class GKNodeSetState(B.GKStatementNode):
    bl_idname = "GKNodeSetState"
    bl_label = "动画_切换状态"
    bl_icon = "IMAGE_DATA"
    gk_category = "动画"
    gk_doc = "切换角色状态(idle / run / jump / attack / hurt …)"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(TEXT, "状态", "idle"))
    gk_input_props = {"状态": "gk_state"}
    gk_prop_defs = (
        ("gk_state", "string", "idle", "状态名", "角色状态字符串"),
        ("gk_var", "string", "state", "状态变量名", "要写入的成员变量名"),
    )


# ==========================================================================
# 实例与场景
# ==========================================================================
@N
class GKNodeSpawn(B.GKStatementNode):
    bl_idname = "GKNodeSpawn"
    bl_label = "实例_生成"
    bl_icon = "ADD"
    gk_category = "实例"
    gk_doc = "实例化一个场景并放到指定位置(子弹 / 特效 / 敌人)"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(TEXT, "场景路径", "res://scenes/Bullet.tscn"))
    gk_input_props = {"场景路径": "gk_scene_path"}
    gk_prop_defs = (
        ("gk_scene_path", "string", "res://scenes/Bullet.tscn", "场景路径", "要实例化的 .tscn"),
        ("gk_x", "float", 0.0, "X", "生成位置"),
        ("gk_y", "float", 0.0, "Y", "生成位置"),
        ("gk_z", "float", 0.0, "Z", "生成位置"),
        ("gk_parent", "node", "", "父节点", "留空表示挂到当前场景根节点"),
    )
    gk_outputs = (S.pin(FLOW, "完成"), S.pin(ANY, "实例", None))

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_scene_path", text="")


@N
class GKNodeQueueFree(B.GKStatementNode):
    bl_idname = "GKNodeQueueFree"
    bl_label = "实例_销毁"
    bl_icon = "TRASH"
    gk_category = "实例"
    gk_doc = "销毁节点(延迟到帧末，对应 queue_free)"
    gk_inputs = (S.pin(FLOW, "执行"), target_pin())
    gk_prop_defs = (("gk_target", "node", TARGET_SELF, "目标", "要销毁的节点"),)

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)


@N
class GKNodeReloadScene(B.GKStatementNode):
    bl_idname = "GKNodeReloadScene"
    bl_label = "实例_重载场景"
    bl_icon = "FILE_REFRESH"
    gk_category = "实例"
    gk_doc = "重新加载当前场景(重开一局 / 复活后重置关卡)"
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_slot", "string", "1", "存档槽", "留空则重载当前场景;填了则读取该存档槽"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_slot", text="")


@N
class GKNodeGetNode(B.GKExpressionNode):
    bl_idname = "GKNodeGetNode"
    bl_label = "取值_获取节点"
    bl_icon = "NODE_SEL"
    gk_category = "实例"
    gk_doc = "按路径获取场景里的节点"
    gk_outputs = (S.pin(OBJECT, "节点", None),)
    gk_prop_defs = (("gk_path", "node", TARGET_SELF, "节点", "从当前脚本所在节点开始"),)

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout, "gk_path")


@N
class GKNodeIsOnFloor(B.GKExpressionNode):
    bl_idname = "GKNodeIsOnFloor"
    bl_label = "取值_是否在地面"
    bl_icon = "TRIA_DOWN"
    gk_category = "实例"
    gk_doc = "角色是否踩在地面(对应 is_on_floor)，做跳跃判定用"
    gk_outputs = (S.pin(BOOL, "在地面", True),)
    gk_prop_defs = (("gk_target", "node", TARGET_SELF, "目标", "角色节点"),)

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)


# ==========================================================================
# 变量与信号
# ==========================================================================
@N
class GKNodeSetVar(B.GKStatementNode):
    bl_idname = "GKNodeSetVar"
    bl_label = "变量_设置"
    bl_icon = "CHECKBOX_DEHLT"
    gk_category = "变量"
    gk_doc = "给脚本变量赋值。首次出现会自动声明成成员变量"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(ANY, "值", 100))
    gk_input_props = {"值": "gk_value"}
    gk_prop_defs = (
        ("gk_var_name", "string", "health", "变量名", "GDScript 成员变量名"),
        ("gk_value", "float", 100.0, "值", "未连线时的值"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_var_name", text="")


@N
class GKNodeGetVar(B.GKExpressionNode):
    bl_idname = "GKNodeGetVar"
    bl_label = "变量_读取"
    bl_icon = "CHECKBOX_HLT"
    gk_category = "变量"
    gk_doc = "读取脚本变量"
    gk_outputs = (S.pin(ANY, "值", None),)
    gk_prop_defs = (("gk_var_name", "string", "health", "变量名", "GDScript 成员变量名"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_var_name", text="")


@N
class GKNodeEmitSignal(B.GKStatementNode):
    bl_idname = "GKNodeEmitSignal"
    bl_label = "信号_发出"
    bl_icon = "RADIOBUT_ON"
    gk_category = "信号"
    gk_doc = "发出信号通知其它节点"
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_signal_name", "string", "died", "信号名", "本脚本声明的信号"),
        ("gk_signal_declare", "bool", True, "自动声明", "在脚本顶部自动 signal 声明"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_signal_name", text="")


@N
class GKNodeConnect(B.GKStatementNode):
    bl_idname = "GKNodeConnect"
    bl_label = "信号_连接"
    bl_icon = "LINKED"
    gk_category = "信号"
    gk_doc = "把目标节点的信号连到本脚本的函数上"
    gk_inputs = (S.pin(FLOW, "执行"), target_pin())
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "目标", "发出信号的节点"),
        ("gk_signal_name", "string", "pressed", "信号名", "对方节点上的信号"),
        ("gk_handler", "string", "on_signal", "响应函数", "本脚本里要调用的函数名"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_signal_name", text="")
        row.prop(self, "gk_handler", text="")


# ==========================================================================
# 输入
# ==========================================================================
@N
class GKNodeActionPressed(B.GKExpressionNode):
    bl_idname = "GKNodeActionPressed"
    bl_label = "输入_按键按住"
    bl_icon = "HAND"
    gk_category = "输入"
    gk_doc = ("动作按键是否按住(对应 Input.is_action_pressed)。"
              "默认 move_right(导出时自动绑到 D)")
    gk_prop_defs = (("gk_action", "string", "move_right", "动作名",
                     "Godot 输入映射里的动作名。用 move_*/jump 这类名字"
                     "导出时会自动绑好按键"),)
    gk_outputs = (S.pin(BOOL, "按住", False),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_action", text="")


@N
class GKNodeActionJustPressed(B.GKExpressionNode):
    bl_idname = "GKNodeActionJustPressed"
    bl_label = "输入_按键按下"
    bl_icon = "MOUSE_LMB"
    gk_category = "输入"
    gk_doc = ("动作键是否刚按下(本帧，对应 is_action_just_pressed)。"
              "默认 jump(导出时自动绑到空格)")
    # 默认 jump 而不是 Godot 内置的 ui_accept —— 后者绑的是**回车/空格**,
    # 但"跳跃用空格"更符合直觉,而且导出时会自动把空格绑上(见 DEFAULT_KEYS)
    gk_prop_defs = (("gk_action", "string", "jump", "动作名",
                     "Godot 输入映射里的动作名。用 move_*/jump 这类名字"
                     "导出时会自动绑好按键"),)
    gk_outputs = (S.pin(BOOL, "刚按下", False),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_action", text="")


@N
class GKNodeGetAxis(B.GKExpressionNode):
    bl_idname = "GKNodeGetAxis"
    bl_label = "输入_轴"
    bl_icon = "ARROW_LEFTRIGHT"
    gk_category = "输入"
    gk_doc = ("取输入轴强度(-1 ~ 1)，用两个动作拼出一个轴。"
              "默认 move_left / move_right(导出时自动绑到 A / D)")
    gk_prop_defs = (
        ("gk_negative", "string", "move_left", "负方向动作",
         "负方向的动作名。默认 move_left(自动绑到 A)"),
        ("gk_positive", "string", "move_right", "正方向动作",
         "正方向的动作名。默认 move_right(自动绑到 D)"),
        ("gk_deadzone", "float", 0.2, "死区", "小于该值视为 0"),
    )
    gk_outputs = (S.pin(FLOAT, "强度", 0.0),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_negative", text="")
        row.prop(self, "gk_positive", text="")


@N
class GKNodeGetVector(B.GKExpressionNode):
    bl_idname = "GKNodeGetVector"
    bl_label = "输入_方向向量"
    bl_icon = "ORIENTATION_GIMBAL"
    gk_category = "输入"
    gk_doc = ("取方向输入并合成向量(对应 Input.get_vector)。"
              "默认动作 move_up/down/left/right 导出时会自动绑到 W/S/A/D —— "
              "不用去 Godot 里手工配输入映射")
    # ── 默认值必须是 move_*,不能是 Godot 内置的 ui_*(踩过的坑)────────
    # `ui_left/right/up/down` 是 Godot 的**全局界面动作**,绑的是**方向键**,
    # 不是 WASD。用户按 WASD 完全没反应,只会得出"输入坏了"的结论
    # —— 而"WASD 走路"是所有人的第一直觉。
    # 用 move_* 的话,导出时会把这四个动作连**按键**一起写进 project.godot
    # (见 project.set_input_actions 的 DEFAULT_KEYS),开箱即用。
    # 想换键位就改这里的动作名,或者在 Godot 的输入映射里改。
    gk_prop_defs = (
        ("gk_left", "string", "move_left", "左", "动作名(导出时自动绑到 A)"),
        ("gk_right", "string", "move_right", "右", "动作名(导出时自动绑到 D)"),
        ("gk_up", "string", "move_up", "上", "动作名(导出时自动绑到 W)"),
        ("gk_down", "string", "move_down", "下", "动作名(导出时自动绑到 S)"),
        ("gk_deadzone", "float", 0.2, "死区", ""),
    )
    gk_outputs = (S.pin(VECTOR, "方向", (0.0, 0.0, 0.0)),)

    def draw_buttons(self, context, layout):
        grid = layout.grid_flow(row_major=True, columns=2, even_columns=True)
        grid.prop(self, "gk_left", text="")
        grid.prop(self, "gk_right", text="")
        grid.prop(self, "gk_up", text="")
        grid.prop(self, "gk_down", text="")


# ==========================================================================
# 运算(纯表达式，不需要执行流)
# ==========================================================================
@N
class GKNodeConstFloat(B.GKExpressionNode):
    bl_idname = "GKNodeConstFloat"
    bl_label = "常量_数值"
    bl_icon = "DOT"
    gk_category = "运算"
    gk_doc = "一个固定数值"
    gk_prop_defs = (("gk_value", "float", 0.0, "数值", ""),)
    gk_outputs = (S.pin(FLOAT, "值", 0.0),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_value", text="")


@N
class GKNodeConstText(B.GKExpressionNode):
    bl_idname = "GKNodeConstText"
    bl_label = "常量_文本"
    bl_icon = "FONT_DATA"
    gk_category = "运算"
    gk_doc = "一段固定文本"
    gk_prop_defs = (("gk_value", "string", "文本", "文本", ""),)
    gk_outputs = (S.pin(TEXT, "值", ""),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_value", text="")


@N
class GKNodeConstBool(B.GKExpressionNode):
    bl_idname = "GKNodeConstBool"
    bl_label = "常量_布尔"
    bl_icon = "CHECKBOX_HLT"
    gk_category = "运算"
    gk_doc = "真 / 假"
    gk_prop_defs = (("gk_value", "bool", True, "值", ""),)
    gk_outputs = (S.pin(BOOL, "值", False),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_value", text="")


@N
class GKNodeConstVector(B.GKExpressionNode):
    bl_idname = "GKNodeConstVector"
    bl_label = "常量_向量"
    bl_icon = "EMPTY_ARROWS"
    gk_category = "运算"
    gk_doc = "一个固定向量"
    gk_prop_defs = (
        ("gk_x", "float", 1.0, "X", ""),
        ("gk_y", "float", 0.0, "Y", ""),
        ("gk_z", "float", 0.0, "Z", ""),
        ("gk_dim", "enum", "V3", "维度", "", (("V2", "二维 Vector2", ""), ("V3", "三维 Vector3", ""))),
    )
    gk_outputs = (S.pin(VECTOR, "向量", (0.0, 0.0, 0.0)),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_x", text="X")
        row.prop(self, "gk_y", text="Y")
        row.prop(self, "gk_z", text="Z")


MATH_OPS = (
    ("ADD", "加 +", ""),
    ("SUB", "减 −", ""),
    ("MUL", "乘 ×", ""),
    ("DIV", "除 ÷", ""),
    ("MOD", "取余 %", ""),
    ("POW", "乘方 ^", ""),
    ("MIN", "取较小 min", ""),
    ("MAX", "取较大 max", ""),
    ("ABS", "绝对值 abs", ""),
    ("SQRT", "开方 sqrt", ""),
    ("SIN", "正弦 sin", ""),
    ("COS", "余弦 cos", ""),
    ("FLOOR", "向下取整 floor", ""),
    ("CEIL", "向上取整 ceil", ""),
    ("ROUND", "四舍五入 round", ""),
    ("CLAMP", "钳制 clamp", ""),
    ("LERP", "线性插值 lerp", ""),
    ("SIGN", "符号 sign", ""),
    ("RANDOM", "随机数 randf", ""),
    ("DEG2RAD", "角度转弧度 deg_to_rad", ""),
    ("RAD2DEG", "弧度转角度 rad_to_deg", ""),
)

MATH_TWO_ARG = {"ADD", "SUB", "MUL", "DIV", "MOD", "POW", "MIN", "MAX"}
MATH_THREE_ARG = {"CLAMP", "LERP"}
MATH_ONE_ARG = {"ABS", "SQRT", "SIN", "COS", "FLOOR", "CEIL", "ROUND", "SIGN", "DEG2RAD", "RAD2DEG"}


@N
class GKNodeMath(B.GKExpressionNode):
    bl_idname = "GKNodeMath"
    bl_label = "数学_运算"
    bl_icon = "DRIVER"
    gk_category = "运算"
    gk_doc = "基础数学运算:加减乘除、取整、钳制、插值、三角函数"
    gk_inputs = (
        S.pin(FLOAT, "A", 0.0),
        S.pin(FLOAT, "B", 0.0),
        S.pin(FLOAT, "C", 0.0),
    )
    gk_input_props = {"A": "gk_a", "B": "gk_b", "C": "gk_c"}
    gk_prop_defs = (
        ("gk_op", "enum", "ADD", "运算", "要执行的运算符", MATH_OPS),
        ("gk_a", "float", 0.0, "A", "未连线时使用"),
        ("gk_b", "float", 0.0, "B", "未连线时使用"),
        ("gk_c", "float", 0.0, "C", "未连线时使用(钳制 / 插值)"),
    )
    gk_outputs = (S.pin(FLOAT, "结果", 0.0),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_op", text="")

    def active_inputs(self):
        op = getattr(self, "gk_op", "ADD")
        if op in MATH_THREE_ARG:
            return ("A", "B", "C")
        if op in MATH_TWO_ARG:
            return ("A", "B")
        if op in MATH_ONE_ARG:
            return ("A",)
        return ("A",)


@N
class GKNodeCompare(B.GKExpressionNode):
    bl_idname = "GKNodeCompare"
    bl_label = "逻辑_比较"
    bl_icon = "ARROW_LEFTRIGHT"
    gk_category = "运算"
    gk_doc = "比较两个值，输出布尔。A / B 是动态类型，可接数值或文本"
    gk_inputs = (S.pin(ANY, "A", 0.0), S.pin(ANY, "B", 0.0))
    gk_input_props = {"A": "gk_a", "B": "gk_b"}
    gk_prop_defs = (
        ("gk_op", "enum", "EQ", "比较方式", "", (
            ("EQ", "等于 ==", ""),
            ("NE", "不等于 !=", ""),
            ("GT", "大于 >", ""),
            ("GE", "大于等于 >=", ""),
            ("LT", "小于 <", ""),
            ("LE", "小于等于 <=", ""),
        )),
        ("gk_a", "string", "0", "A", "未连线时的字面量"),
        ("gk_b", "string", "0", "B", "未连线时的字面量"),
    )
    gk_outputs = (S.pin(BOOL, "结果", False),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_op", text="")


@N
class GKNodeLogic(B.GKExpressionNode):
    bl_idname = "GKNodeLogic"
    bl_label = "逻辑_布尔运算"
    bl_icon = "CHECKMARK"
    gk_category = "运算"
    gk_doc = "与 / 或 / 非"
    gk_inputs = (S.pin(BOOL, "A", False), S.pin(BOOL, "B", False))
    gk_prop_defs = (
        ("gk_op", "enum", "AND", "运算", "", (
            ("AND", "与 and", ""),
            ("OR", "或 or", ""),
            ("NOT", "非 not(A)", ""),
        )),
    )
    gk_outputs = (S.pin(BOOL, "结果", False),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_op", text="")


@N
class GKNodeRandomRange(B.GKExpressionNode):
    bl_idname = "GKNodeRandomRange"
    bl_label = "数学_随机整数"
    bl_icon = "MOD_PARTICLES"
    gk_category = "运算"
    gk_doc = "取 [最小, 最大) 之间的随机整数(对应 randi_range)"
    gk_inputs = (S.pin(INT, "最小", 0), S.pin(INT, "最大", 10))
    gk_input_props = {"最小": "gk_min", "最大": "gk_max"}
    gk_prop_defs = (("gk_min", "int", 0, "最小", ""), ("gk_max", "int", 10, "最大", ""))
    gk_outputs = (S.pin(INT, "结果", 0),)


@N
class GKNodeVectorMake(B.GKExpressionNode):
    bl_idname = "GKNodeVectorMake"
    bl_label = "向量_合并"
    bl_icon = "ADD"
    gk_category = "运算"
    gk_doc = "把 X / Y / Z 合成一个向量，可切 2D / 3D"
    gk_prop_defs = (
        ("gk_x", "float", 0.0, "X", ""),
        ("gk_y", "float", 0.0, "Y", ""),
        ("gk_z", "float", 0.0, "Z", ""),
        ("gk_dim", "enum", "V3", "维度", "", (("V2", "二维 Vector2", ""), ("V3", "三维 Vector3", ""))),
    )
    # 引脚按 gk_dim 动态生成，见 build_sockets;这里保留声明供菜单/校验使用
    gk_inputs = (S.pin(FLOAT, "X", 0.0), S.pin(FLOAT, "Y", 0.0), S.pin(FLOAT, "Z", 0.0))
    gk_input_props = {"X": "gk_x", "Y": "gk_y", "Z": "gk_z"}
    gk_outputs = (S.pin(VECTOR, "向量", (0.0, 0.0, 0.0)),)

    def build_sockets(self):
        dim = getattr(self, "gk_dim", "V3")
        wanted = ("X", "Y") if dim == "V2" else ("X", "Y", "Z")
        self.inputs.clear()
        self.outputs.clear()
        for spec in self.gk_inputs:
            if spec["name"] not in wanted:
                continue
            socket = self.inputs.new(spec["kind"][0], spec["name"])
            socket.default_value = 0.0
        self.outputs.new(S.VECTOR[0], "向量")

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_dim", text="")


@N
class GKNodeVectorSplit(B.GKExpressionNode):
    bl_idname = "GKNodeVectorSplit"
    bl_label = "向量_拆分"
    bl_icon = "ARROW_LEFTRIGHT"
    gk_category = "运算"
    gk_doc = "把向量拆成 X / Y / Z 三个数值"
    gk_inputs = (S.pin(VECTOR, "向量", (0.0, 0.0, 0.0)),)
    gk_outputs = (S.pin(FLOAT, "X", 0.0), S.pin(FLOAT, "Y", 0.0), S.pin(FLOAT, "Z", 0.0))


@N
class GKNodeVectorLength(B.GKExpressionNode):
    bl_idname = "GKNodeVectorLength"
    bl_label = "向量_长度"
    bl_icon = "DRIVER_DISTANCE"
    gk_category = "运算"
    gk_doc = "向量长度(模)"
    gk_inputs = (S.pin(VECTOR, "向量", (0.0, 0.0, 0.0)),)
    gk_outputs = (S.pin(FLOAT, "长度", 0.0),)


@N
class GKNodeVectorNormalize(B.GKExpressionNode):
    bl_idname = "GKNodeVectorNormalize"
    bl_label = "向量_归一化"
    bl_icon = "SNAP_ON"
    gk_category = "运算"
    gk_doc = "把向量变成单位长度(保留方向)"
    gk_inputs = (S.pin(VECTOR, "向量", (0.0, 0.0, 0.0)),)
    gk_outputs = (S.pin(VECTOR, "单位向量", (0.0, 0.0, 0.0)),)


@N
class GKNodeConvert(B.GKExpressionNode):
    bl_idname = "GKNodeConvert"
    bl_label = "转换_类型"
    bl_icon = "FILE_REFRESH"
    gk_category = "运算"
    gk_doc = "在 数值 / 整数 / 文本 / 布尔 / 向量 之间转换"
    gk_inputs = (S.pin(ANY, "输入", 0.0),)
    gk_prop_defs = (
        ("gk_to", "enum", "float", "转为", "", (
            ("float", "数值 float", ""),
            ("int", "整数 int", ""),
            ("String", "文本 String", ""),
            ("bool", "布尔 bool", ""),
            ("Vector2", "二维向量 Vector2", ""),
            ("Vector3", "三维向量 Vector3", ""),
        )),
    )
    gk_outputs = (S.pin(ANY, "输出", None),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_to", text="")


# ==========================================================================
# 显示
# ==========================================================================
@N
class GKNodeSetVisible(B.GKStatementNode):
    bl_idname = "GKNodeSetVisible"
    bl_label = "显示_设置可见"
    bl_icon = "HIDE_OFF"
    gk_category = "显示"
    gk_doc = "显示或隐藏一个节点(2D / 3D 通用，对应 visible)"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(BOOL, "可见", True), target_pin())
    gk_input_props = {"可见": "gk_visible"}
    gk_prop_defs = (
        ("gk_visible", "bool", True, "可见", "未连线时用它决定显示还是隐藏"),
        ("gk_target", "node", TARGET_SELF, "目标", "要显示 / 隐藏的节点"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_visible", text="", toggle=True,
                 icon="HIDE_OFF" if getattr(self, "gk_visible", True) else "HIDE_ON")
        B.draw_target_picker(self, layout)


@N
class GKNodeSetPaused(B.GKStatementNode):
    bl_idname = "GKNodeSetPaused"
    bl_label = "显示_暂停节点"
    bl_icon = "PAUSE"
    gk_category = "显示"
    gk_doc = "暂停 / 恢复一个节点的处理(对应 process_mode，不需要自己写计数逻辑)"
    gk_inputs = (S.pin(FLOW, "执行"), target_pin())
    gk_prop_defs = (
        ("gk_paused", "bool", True, "暂停", "勾上=停止该节点的 _process / _physics_process"),
        ("gk_target", "node", TARGET_SELF, "目标", "要暂停 / 恢复的节点"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_paused", text="", toggle=True,
                 icon="PAUSE" if getattr(self, "gk_paused", True) else "PLAY")
        B.draw_target_picker(self, layout)


# ==========================================================================
# 相机
# ==========================================================================
@N
class GKNodeCameraSetCurrent(B.GKStatementNode):
    bl_idname = "GKNodeCameraSetCurrent"
    bl_label = "相机_切换"
    bl_icon = "CAMERA_DATA"
    gk_category = "相机"
    gk_doc = "把某个相机设为当前相机(2D / 3D 通用)——做镜头切换、Boss 特写用"
    gk_inputs = (S.pin(FLOW, "执行"), target_pin("相机"))
    gk_prop_defs = (("gk_target", "node", "", "相机", "要激活的 Camera2D / Camera3D"),)

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)


@N
class GKNodeCameraZoom(B.GKStatementNode):
    bl_idname = "GKNodeCameraZoom"
    bl_label = "相机_缩放"
    bl_icon = "ZOOM_IN"
    gk_category = "相机"
    gk_doc = "设置相机缩放:2D 用 zoom，3D 用视场角 fov(自动按相机类型选，不用管维度)"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "数值", 1.0), target_pin("相机"))
    gk_input_props = {"数值": "gk_value"}
    gk_prop_defs = (
        ("gk_value", "float", 1.0, "数值", "2D: zoom 越大画面越近；3D: 垂直视场角(度)"),
        ("gk_target", "node", "", "相机", "留空则用 $Camera2D / $Camera3D"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_value", text="")
        B.draw_target_picker(self, layout)


@N
class GKNodeCameraShake(B.GKStatementNode):
    bl_idname = "GKNodeCameraShake"
    bl_label = "相机_震动"
    bl_icon = "RNDCURVE"
    gk_category = "相机"
    gk_doc = ("让相机抖动一下(受击 / 爆炸反馈)。用相机的偏移量实现，"
              "不会破坏相机原本的位置——所以可以和『跟随』同时用")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "强度", 8.0),
        S.pin(FLOAT, "持续", 0.25),
        target_pin("相机"),
    )
    gk_input_props = {"强度": "gk_strength", "持续": "gk_duration"}
    gk_prop_defs = (
        ("gk_strength", "float", 8.0, "强度", "最大偏移像素"),
        ("gk_duration", "float", 0.25, "持续", "震动时长(秒)"),
        ("gk_target", "node", "", "相机", "留空则用 $Camera2D / $Camera3D"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_strength", text="")
        row.prop(self, "gk_duration", text="")
        B.draw_target_picker(self, layout)


@N
class GKNodeCameraFollow(B.GKStatementNode):
    bl_idname = "GKNodeCameraFollow"
    bl_label = "相机_跟随"
    bl_icon = "CON_FOLLOWPATH"
    gk_category = "相机"
    gk_doc = ("让相机跟随某个节点(2D / 3D 通用)。"
              "平台跳跃游戏一定要放这个 —— 不放的话角色一跑远就出画面了")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "平滑", 0.12),
        target_pin("跟随谁"),
    )
    gk_input_props = {"平滑": "gk_smooth"}
    gk_prop_defs = (
        ("gk_smooth", "float", 0.12, "平滑",
         "0 = 硬跟随(相机与角色完全同步)；越大越拖，0.1~0.2 手感较好"),
        ("gk_target", "node", TARGET_SELF, "跟随谁", "要跟随的节点，默认自身"),
        ("gk_camera", "node", "", "相机", "留空则自动找场景里第一个相机"),
        ("gk_offset_x", "float", 0.0, "偏移 X", "瞄准角色上方/下方时的偏移"),
        ("gk_offset_y", "float", 0.0, "偏移 Y", ""),
        ("gk_distance", "float", 0.0, "3D 距离",
         "3D 专用:留 0 = 保持相机一开始相对角色的位置(推荐,"
         "在 Blender 里摆好相机角度即可);填了就固定站在角色身后这个距离"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_smooth", text="")
        row.prop(self, "gk_offset_y", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_camera", text="")
        B.draw_target_picker(self, row, "gk_camera")

    def draw_buttons_ext(self, context, layout):
        layout.label(text="相机:留空 = 自动找场景里第一个相机(推荐)")
        B.draw_target_picker(self, layout, "gk_camera")
        layout.label(text="跟随谁:留空 = 跟随脚本所在的那个节点")
        B.draw_target_picker(self, layout, "gk_target")


# ==========================================================================
# 时间
# ==========================================================================
@N
class GKNodeSetTimeScale(B.GKStatementNode):
    bl_idname = "GKNodeSetTimeScale"
    bl_label = "时间_设置倍速"
    bl_icon = "TIME"
    gk_category = "时间"
    gk_doc = ("设置全局时间倍速(对应 Engine.time_scale):0.3 = 子弹时间，"
              "1.0 = 正常，0 = 完全暂停")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "倍速", 1.0))
    gk_input_props = {"倍速": "gk_scale"}
    gk_prop_defs = (("gk_scale", "float", 1.0, "倍速", "1.0 为正常速度"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_scale", text="")


# ==========================================================================
# 音效
# ==========================================================================
@N
class GKNodePlaySound(B.GKStatementNode):
    bl_idname = "GKNodePlaySound"
    bl_label = "音效_播放"
    bl_icon = "SOUND"
    gk_category = "音效"
    gk_doc = ("播放一次音效(跳跃 / 受击 / 拾取)。播放器是运行时自动建的，"
              "播完自动销毁，不需要你在场景里摆节点")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "音频路径", "res://art/jump.wav"),
        S.pin(FLOAT, "音量", 0.0),
        S.pin(FLOAT, "音高", 1.0),
    )
    gk_input_props = {
        "音频路径": "gk_path",
        "音量": "gk_volume",
        "音高": "gk_pitch",
    }
    gk_prop_defs = (
        ("gk_path", "string", "res://art/jump.wav", "音频路径", "res:// 开头的音频文件"),
        ("gk_volume", "float", 0.0, "音量", "分贝，0 为原始音量，-6 约减半"),
        ("gk_pitch", "float", 1.0, "音高", "1.0 原速，>1 更尖更快"),
        ("gk_2d", "bool", True, "2D 音效", "勾上=按距离衰减(3D 项目取消勾选)"),
        ("gk_target", "node", "", "挂到", "留空则挂到当前脚本所在节点"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_path", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_volume", text="")
        row.prop(self, "gk_pitch", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_2d", text="2D", toggle=True)
        B.draw_target_picker(self, layout)


@N
class GKNodePlayMusic(B.GKStatementNode):
    bl_idname = "GKNodePlayMusic"
    bl_label = "音效_音乐"
    bl_icon = "PLAY_SOUND"
    gk_category = "音效"
    gk_doc = "播放背景音乐(BGM)。重复调用会切换曲目并做淡入淡出，不会叠着播"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "音频路径", "res://art/bgm.ogg"),
        S.pin(FLOAT, "音量", -6.0),
    )
    gk_input_props = {"音频路径": "gk_path", "音量": "gk_volume"}
    gk_prop_defs = (
        ("gk_path", "string", "res://art/bgm.ogg", "音频路径", "res:// 开头的音频文件"),
        ("gk_volume", "float", -6.0, "音量", "分贝，0 为原始音量"),
        ("gk_fade", "float", 0.5, "淡入秒数", "0 表示直接开始"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_path", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_volume", text="")
        row.prop(self, "gk_fade", text="")


@N
class GKNodeStopSound(B.GKStatementNode):
    bl_idname = "GKNodeStopSound"
    bl_label = "音效_停止音乐"
    bl_icon = "MUTE_IPO_OFF"
    gk_category = "音效"
    gk_doc = "停止背景音乐(可做淡出)。音效是一次性的，播完自己就没了，不用手动停"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "淡出秒数", 0.5))
    gk_input_props = {"淡出秒数": "gk_fade"}
    gk_prop_defs = (("gk_fade", "float", 0.5, "淡出秒数", "0 表示立刻停"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_fade", text="")


# ==========================================================================
# 存档
# ==========================================================================
@N
class GKNodeSaveSet(B.GKStatementNode):
    bl_idname = "GKNodeSaveSet"
    bl_label = "存档_写入"
    bl_icon = "IMPORT"
    gk_category = "存档"
    gk_doc = ("往存档里写一个值(血量 / 关卡 / 金币)。只改内存，"
              "要真正落盘请接『存档_保存』")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "键名", "hp"),
        S.pin(ANY, "值", 3),
    )
    gk_input_props = {"键名": "gk_key"}
    gk_prop_defs = (("gk_key", "string", "hp", "键名", "存档里这一项的键"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_key", text="")


@N
class GKNodeSaveGet(B.GKStatementNode):
    bl_idname = "GKNodeSaveGet"
    bl_label = "存档_读取到变量"
    bl_icon = "EXPORT"
    gk_category = "存档"
    gk_doc = ("把存档里的值读进一个脚本变量，然后用『变量_读取』节点取用。"
              "键不存在时用『默认值』")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "键名", "hp"),
    )
    gk_input_props = {"键名": "gk_key"}
    gk_prop_defs = (
        ("gk_key", "string", "hp", "键名", "存档里这一项的键"),
        ("gk_var", "string", "hp", "写入变量", "读出来的值放进哪个脚本变量"),
        ("gk_default", "float", 0.0, "默认值", "键不存在时用它"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_key", text="")
        row.prop(self, "gk_var", text="")
        layout.prop(self, "gk_default", text="")


@N
class GKNodeSaveWrite(B.GKStatementNode):
    bl_idname = "GKNodeSaveWrite"
    bl_label = "存档_保存"
    bl_icon = "FILE_TICK"
    gk_category = "存档"
    gk_doc = "把内存里的存档数据写进磁盘文件(user:// 目录下)"
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_slot", "string", "save1", "存档槽", "不同槽位互不影响，做多存档用"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_slot", text="")


@N
class GKNodeSaveLoad(B.GKStatementNode):
    bl_idname = "GKNodeSaveLoad"
    bl_label = "存档_载入"
    bl_icon = "FILE_FOLDER"
    gk_category = "存档"
    gk_doc = "从磁盘把存档读进内存。文件不存在时不会出错，只是读到空存档"
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_slot", "string", "save1", "存档槽", "要和『存档_保存』用同一个槽位"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_slot", text="")


@N
class GKNodeSaveHas(B.GKStatementNode):
    bl_idname = "GKNodeSaveHas"
    bl_label = "存档_是否存在"
    bl_icon = "QUESTION"
    gk_category = "存档"
    gk_doc = "判断某个槽位有没有存档文件(做『继续游戏』按钮的可用状态)"
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_slot", "string", "save1", "存档槽", "要检查的槽位"),
    )
    # 出口故意不叫『完成』:见 codegen 里 _stmt_save_has 的说明 ——
    # emit_statement 会自动续接『完成』，那样 else 分支会跑两遍。
    gk_outputs = (S.pin(FLOW, "有档"), S.pin(FLOW, "没档"))

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_slot", text="")


@N
class GKNodeSaveDelete(B.GKStatementNode):
    bl_idname = "GKNodeSaveDelete"
    bl_label = "存档_删除"
    bl_icon = "TRASH"
    gk_category = "存档"
    gk_doc = "删除某个槽位的存档文件(删档 / 重新开始)"
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_slot", "string", "save1", "存档槽", "要删除的槽位"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_slot", text="")


# ==========================================================================
# 屏幕效果
# ==========================================================================
@N
class GKNodeScreenFlash(B.GKStatementNode):
    bl_idname = "GKNodeScreenFlash"
    bl_label = "屏幕_闪白"
    bl_icon = "IMAGE_ZDEPTH"
    gk_category = "屏幕效果"
    gk_doc = ("整屏闪一下(受击 / 爆炸 / 治疗)。用 ColorRect + Tween 做，"
              "不占渲染管线，也不需要着色器")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(COLOR, "颜色", (1.0, 1.0, 1.0, 1.0)),
        S.pin(FLOAT, "时长", 0.3),
    )
    gk_input_props = {"时长": "gk_time"}
    gk_prop_defs = (
        ("gk_color", "color", (1.0, 1.0, 1.0, 1.0), "颜色", "闪光颜色"),
        ("gk_time", "float", 0.3, "时长", "淡出到透明所需秒数"),
        ("gk_alpha", "float", 0.6, "起始透明度", "1 为全不透明"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_color", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_time", text="")
        row.prop(self, "gk_alpha", text="")


@N
class GKNodeScreenFade(B.GKStatementNode):
    bl_idname = "GKNodeScreenFade"
    bl_label = "屏幕_渐变遮罩"
    bl_icon = "IMAGE_ALPHA"
    gk_category = "屏幕效果"
    gk_doc = ("整屏渐变到某个颜色(黑场转场 / 淡入开场)。"
              "『到不透明』用于转场，『到透明』用于淡回游戏")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "时长", 1.0),
    )
    gk_input_props = {"时长": "gk_time"}
    gk_prop_defs = (
        ("gk_color", "color", (0.0, 0.0, 0.0, 1.0), "颜色", "遮罩颜色，默认黑"),
        ("gk_time", "float", 1.0, "时长", "渐变秒数"),
        ("gk_to_opaque", "bool", True, "渐变到不透明", "取消勾选=淡回透明"),
        ("gk_hold", "float", 0.0, "保持秒数", "转场时全黑停留多久(0 = 一直留着)"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_color", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_time", text="")
        row.prop(self, "gk_hold", text="")
        layout.prop(self, "gk_to_opaque", text="渐变到不透明", toggle=True)


# ==========================================================================
# 界面
# ==========================================================================
@N
class GKNodeUiText(B.GKStatementNode):
    bl_idname = "GKNodeUiText"
    bl_label = "界面_文本"
    bl_icon = "FONT_DATA"
    gk_category = "界面"
    gk_doc = ("在屏幕上显示一行字(分数 / 提示 / 对话)。UI 层是自动建的，"
              "不需要你在场景里摆控件；重复调用同一个节点是改文字，不会叠出新的一行")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "文字", "分数: 0"),
        S.pin(FLOAT, "X", 20.0),
        S.pin(FLOAT, "Y", 20.0),
    )
    gk_input_props = {"文字": "gk_text", "X": "gk_x", "Y": "gk_y"}
    gk_prop_defs = (
        ("gk_text", "string", "分数: 0", "文字", "未连线时显示的内容"),
        ("gk_x", "float", 20.0, "X", "屏幕坐标(左上角为原点)"),
        ("gk_y", "float", 20.0, "Y", "屏幕坐标"),
        ("gk_size", "int", 24, "字号", "像素"),
        ("gk_color", "color", (1.0, 1.0, 1.0, 1.0), "颜色", "文字颜色"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_text", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_x", text="X")
        row.prop(self, "gk_y", text="Y")
        row = layout.row(align=True)
        row.prop(self, "gk_size", text="")
        row.prop(self, "gk_color", text="")


@N
class GKNodeUiBar(B.GKStatementNode):
    bl_idname = "GKNodeUiBar"
    bl_label = "界面_进度条"
    bl_icon = "COLOR"
    gk_category = "界面"
    gk_doc = "在屏幕上显示一条进度条(血条 / 蓝条 / 蓄力条)。数值会自动钳在 0~最大值"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "数值", 100.0),
        S.pin(FLOAT, "X", 20.0),
        S.pin(FLOAT, "Y", 60.0),
    )
    gk_input_props = {"数值": "gk_value", "X": "gk_x", "Y": "gk_y"}
    gk_prop_defs = (
        ("gk_value", "float", 100.0, "数值", "当前值"),
        ("gk_max", "float", 100.0, "最大值", "满值"),
        ("gk_x", "float", 20.0, "X", "屏幕坐标"),
        ("gk_y", "float", 60.0, "Y", "屏幕坐标"),
        ("gk_width", "float", 200.0, "宽", "像素"),
        ("gk_height", "float", 16.0, "高", "像素"),
        ("gk_color", "color", (0.85, 0.25, 0.25, 1.0), "颜色", "填充色"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_value", text="")
        row.prop(self, "gk_max", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_x", text="X")
        row.prop(self, "gk_y", text="Y")
        row = layout.row(align=True)
        row.prop(self, "gk_width", text="")
        row.prop(self, "gk_height", text="")
        layout.prop(self, "gk_color", text="")


@N
class GKNodeUiButton(B.GKStatementNode):
    bl_idname = "GKNodeUiButton"
    bl_label = "界面_按钮"
    bl_icon = "PLAY"
    gk_category = "界面"
    gk_doc = ("在屏幕上放一个按钮，点击后沿『点击』出口继续。"
              "按钮是持久的(不会点一下就消失)，重复调用只改文字")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "文字", "开始游戏"),
        S.pin(FLOAT, "X", 100.0),
        S.pin(FLOAT, "Y", 200.0),
    )
    gk_input_props = {"文字": "gk_text", "X": "gk_x", "Y": "gk_y"}
    gk_prop_defs = (
        ("gk_text", "string", "开始游戏", "文字", "按钮上显示的字"),
        ("gk_x", "float", 100.0, "X", "屏幕坐标"),
        ("gk_y", "float", 200.0, "Y", "屏幕坐标"),
        ("gk_width", "float", 160.0, "宽", "像素"),
        ("gk_height", "float", 48.0, "高", "像素"),
    )
    # 出口故意不叫『完成』:emit_statement 会自动续接『完成』，
    # 那样按钮还没点就会立刻往下跑。见 _stmt_ui_button 的说明。
    gk_outputs = (S.pin(FLOW, "点击"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_text", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_x", text="X")
        row.prop(self, "gk_y", text="Y")
        row = layout.row(align=True)
        row.prop(self, "gk_width", text="")
        row.prop(self, "gk_height", text="")


@N
class GKNodeUiHide(B.GKStatementNode):
    bl_idname = "GKNodeUiHide"
    bl_label = "界面_隐藏控件"
    bl_icon = "HIDE_ON"
    gk_category = "界面"
    gk_doc = "隐藏某个界面控件(比如捡到道具后让提示消失)。控件名就是创建它的节点名"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(TEXT, "控件名", "提示"))
    gk_input_props = {"控件名": "gk_widget"}
    gk_prop_defs = (
        ("gk_widget", "string", "提示", "控件名", "要隐藏的界面节点名"),
        ("gk_visible", "bool", False, "显示", "勾上=显示，取消=隐藏"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_widget", text="")
        row.prop(self, "gk_visible", text="", toggle=True)


# ==========================================================================
# 射线
# ==========================================================================
@N
class GKNodeRaycast(B.GKStatementNode):
    bl_idname = "GKNodeRaycast"
    bl_label = "射线_检测"
    bl_icon = "RESTRICT_SELECT_OFF"
    gk_category = "射线"
    gk_doc = ("从一个点沿某个方向打一条射线，看打到什么(开枪命中 / 视线判定 / "
              "地面检测)。打到东西走『命中』，没打到走『没打中』")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(VECTOR, "方向", (1.0, 0.0, 0.0)),
        S.pin(FLOAT, "距离", 200.0),
    )
    gk_input_props = {"方向": "gk_direction", "距离": "gk_distance"}
    gk_prop_defs = (
        ("gk_direction", "float_vector", (1.0, 0.0, 0.0), "方向", "单位向量，会自动归一化"),
        ("gk_distance", "float", 200.0, "距离", "射线长度(像素 / 米)"),
        ("gk_offset", "float_vector", (0.0, 0.0, 0.0), "起点偏移", "相对自身位置的偏移"),
        ("gk_layer", "int", 1, "碰撞层", "只检测这些层(位掩码，1 表示第 1 层)"),
    )
    # 出口不叫『完成』:见 codegen 里 _stmt_raycast 的说明 ——
    # 叫『完成』会被 emit_statement 自动续接，分支就废了。
    gk_outputs = (
        S.pin(FLOW, "命中"),
        S.pin(FLOW, "没打中"),
        S.pin(OBJECT, "打到的东西", None),
        S.pin(VECTOR, "命中点", (0.0, 0.0, 0.0)),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_distance", text="")
        row.prop(self, "gk_layer", text="")
        layout.prop(self, "gk_direction", text="")


@N
class GKNodeRaycastCheck(B.GKExpressionNode):
    bl_idname = "GKNodeRaycastCheck"
    bl_label = "射线_有东西吗"
    bl_icon = "QUESTION"
    gk_category = "射线"
    gk_doc = ("往某个方向看一眼有没有东西(视线判定 / 撞墙检测)，"
              "结果接到『如果_否则』的条件上")
    gk_inputs = (
        S.pin(VECTOR, "方向", (1.0, 0.0, 0.0)),
        S.pin(FLOAT, "距离", 200.0),
    )
    gk_input_props = {"方向": "gk_direction", "距离": "gk_distance"}
    gk_prop_defs = (
        ("gk_direction", "float_vector", (1.0, 0.0, 0.0), "方向", "单位向量，会自动归一化"),
        ("gk_distance", "float", 200.0, "距离", "射线长度"),
        ("gk_layer", "int", 1, "碰撞层", "只检测这些层"),
    )
    # 纯表达式:取值时把射线"就地"射出去。
    # GDScript 的表达式在被使用的作用域里求值，所以生成的多行代码
    # 正好落在需要这个布尔值的位置(比如 if 的条件上)。
    #
    # gk_no_cache:射线每次求值都必须**重新射**。生成器默认会缓存表达式文本，
    # 缓存之后第二次求值只会复用第一次的结果，射线就永远停在那里了。
    gk_no_cache = True
    gk_outputs = (S.pin(BOOL, "有东西", False),)


# ==========================================================================
# 定时
# ==========================================================================
@N
class GKNodeEverySeconds(B.GKStatementNode):
    bl_idname = "GKNodeEverySeconds"
    bl_label = "定时_每隔几秒"
    bl_icon = "TIME"
    gk_category = "定时"
    gk_doc = ("每隔 N 秒执行一次(刷怪 / 回血 / 自动存档)。"
              "用独立计时器,不会被帧率影响")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "间隔", 1.0),
    )
    gk_input_props = {"间隔": "gk_interval"}
    gk_prop_defs = (
        ("gk_interval", "float", 1.0, "间隔", "秒"),
        ("gk_start_now", "bool", False, "立即开始", "勾上=不等第一个间隔就先执行一次"),
    )
    # 出口不叫『完成』:要等计时器到点才走，见 _stmt_every_seconds 的说明。
    gk_outputs = (S.pin(FLOW, "到点了"),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_interval", text="")
        row.prop(self, "gk_start_now", text="", toggle=True)


@N
class GKNodeTweenValue(B.GKStatementNode):
    bl_idname = "GKNodeTweenValue"
    bl_label = "定时_缓动数值"
    bl_icon = "IPO_EASE_IN_OUT"
    gk_category = "定时"
    gk_doc = ("把一个脚本变量在指定时间内平滑地变到目标值(血条缓动 / 镜头推近 / "
              "UI 滑入)。对应 Godot 的 Tween")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "目标值", 100.0),
        S.pin(FLOAT, "时长", 0.5),
    )
    gk_input_props = {"目标值": "gk_to", "时长": "gk_time"}
    gk_prop_defs = (
        ("gk_var", "string", "value", "变量名", "要在 Blender 侧声明过的脚本变量"),
        ("gk_to", "float", 100.0, "目标值", "缓动到多少"),
        ("gk_time", "float", 0.5, "时长", "秒"),
        ("gk_trans", "enum", "TRANS_LINEAR", "曲线", "速度曲线", (
            ("TRANS_LINEAR", "匀速", ""),
            ("TRANS_SINE", "正弦(平滑)", ""),
            ("TRANS_CUBIC", "三次(先慢后快)", ""),
            ("TRANS_BACK", "回弹(冲过头再回来)", ""),
            ("TRANS_BOUNCE", "弹跳", ""),
            ("TRANS_ELASTIC", "橡皮筋", ""),
        )),
        ("gk_ease", "enum", "EASE_IN_OUT", "缓动方式", "", (
            ("EASE_IN", "慢进", ""),
            ("EASE_OUT", "慢出", ""),
            ("EASE_IN_OUT", "两头慢", ""),
        )),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_var", text="")
        row.prop(self, "gk_to", text="")
        row.prop(self, "gk_time", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_trans", text="")
        row.prop(self, "gk_ease", text="")


# ==========================================================================
# 列表
# ==========================================================================
@N
class GKNodeListNew(B.GKStatementNode):
    bl_idname = "GKNodeListNew"
    bl_label = "列表_新建"
    bl_icon = "OUTLINER_OB_GROUP_INSTANCE"
    gk_category = "列表"
    gk_doc = "新建一个空列表(背包 / 刷怪表 / 任务清单)"
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (("gk_var", "string", "items", "变量名", "列表变量名"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_var", text="")


@N
class GKNodeListAdd(B.GKStatementNode):
    bl_idname = "GKNodeListAdd"
    bl_label = "列表_添加"
    bl_icon = "ADD"
    gk_category = "列表"
    gk_doc = "往列表末尾加一个东西(捡到道具 / 记录敌人)"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(ANY, "值", 0),
    )
    gk_prop_defs = (("gk_var", "string", "items", "变量名", "列表变量名"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_var", text="")


@N
class GKNodeListRemove(B.GKStatementNode):
    bl_idname = "GKNodeListRemove"
    bl_label = "列表_移除"
    bl_icon = "REMOVE"
    gk_category = "列表"
    gk_doc = "从列表里删掉一个值(用掉道具 / 敌人死亡)"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(ANY, "值", 0),
    )
    gk_prop_defs = (("gk_var", "string", "items", "变量名", "列表变量名"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_var", text="")


@N
class GKNodeListGet(B.GKStatementNode):
    bl_idname = "GKNodeListGet"
    bl_label = "列表_取第几个到变量"
    bl_icon = "FULLSCREEN_ENTER"
    gk_category = "列表"
    gk_doc = "把列表里第 N 个取进一个脚本变量。下标从 0 开始；越界时会用『默认值』"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(INT, "下标", 0),
    )
    gk_input_props = {"下标": "gk_index"}
    gk_prop_defs = (
        ("gk_var", "string", "items", "列表变量名", "从哪个列表取"),
        ("gk_index", "int", 0, "下标", "从 0 开始"),
        ("gk_target_var", "string", "item", "写入变量", "取出来的值放进哪个脚本变量"),
        ("gk_default", "float", 0.0, "默认值", "越界时用它"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_var", text="")
        row.prop(self, "gk_index", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_target_var", text="")
        row.prop(self, "gk_default", text="")


# ==========================================================================
# 字典
# ==========================================================================
@N
class GKNodeDictSet(B.GKStatementNode):
    bl_idname = "GKNodeDictSet"
    bl_label = "字典_写入"
    bl_icon = "KEYINGSET"
    gk_category = "字典"
    gk_doc = "往字典里写一个键值对(按名字存数据,比列表好查)"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "键", "hp"),
        S.pin(ANY, "值", 3),
    )
    gk_input_props = {"键": "gk_key"}
    gk_prop_defs = (
        ("gk_var", "string", "stats", "字典变量名", "往哪个字典写"),
        ("gk_key", "string", "hp", "键", "键名"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_var", text="")
        row.prop(self, "gk_key", text="")


@N
class GKNodeDictGet(B.GKStatementNode):
    bl_idname = "GKNodeDictGet"
    bl_label = "字典_读取到变量"
    bl_icon = "EXPORT"
    gk_category = "字典"
    gk_doc = "按键从字典取值放进脚本变量。键不存在时用『默认值』"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "键", "hp"),
    )
    gk_input_props = {"键": "gk_key"}
    gk_prop_defs = (
        ("gk_var", "string", "stats", "字典变量名", "从哪个字典读"),
        ("gk_key", "string", "hp", "键", "键名"),
        ("gk_target_var", "string", "value", "写入变量", "取出来的值放进哪个脚本变量"),
        ("gk_default", "float", 0.0, "默认值", "键不存在时用它"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_var", text="")
        row.prop(self, "gk_key", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_target_var", text="")
        row.prop(self, "gk_default", text="")


@N
class GKNodeDictHas(B.GKStatementNode):
    bl_idname = "GKNodeDictHas"
    bl_label = "字典_有键吗"
    bl_icon = "QUESTION"
    gk_category = "字典"
    gk_doc = "判断字典里有没有某个键"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "键", "hp"),
    )
    gk_input_props = {"键": "gk_key"}
    gk_prop_defs = (
        ("gk_var", "string", "stats", "字典变量名", "查哪个字典"),
        ("gk_key", "string", "hp", "键", "键名"),
    )
    gk_outputs = (S.pin(FLOW, "有"), S.pin(FLOW, "没有"))

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_var", text="")
        row.prop(self, "gk_key", text="")


# ==========================================================================
# 手柄
# ==========================================================================
_JOY_AXES = (
    ("JOY_AXIS_LEFT_X", "左摇杆 左右", ""),
    ("JOY_AXIS_LEFT_Y", "左摇杆 上下", ""),
    ("JOY_AXIS_RIGHT_X", "右摇杆 左右", ""),
    ("JOY_AXIS_RIGHT_Y", "右摇杆 上下", ""),
    ("JOY_AXIS_TRIGGER_LEFT", "左扳机", ""),
    ("JOY_AXIS_TRIGGER_RIGHT", "右扳机", ""),
)

_JOY_BUTTONS = (
    ("JOY_BUTTON_A", "A(×)", ""),
    ("JOY_BUTTON_B", "B(○)", ""),
    ("JOY_BUTTON_X", "X(□)", ""),
    ("JOY_BUTTON_Y", "Y(△)", ""),
    ("JOY_BUTTON_LEFT_SHOULDER", "左肩键 LB", ""),
    ("JOY_BUTTON_RIGHT_SHOULDER", "右肩键 RB", ""),
    ("JOY_BUTTON_BACK", "返回 / Select", ""),
    ("JOY_BUTTON_START", "开始 / Start", ""),
    ("JOY_BUTTON_DPAD_UP", "十字键 上", ""),
    ("JOY_BUTTON_DPAD_DOWN", "十字键 下", ""),
    ("JOY_BUTTON_DPAD_LEFT", "十字键 左", ""),
    ("JOY_BUTTON_DPAD_RIGHT", "十字键 右", ""),
)


@N
class GKNodeJoyAxis(B.GKExpressionNode):
    bl_idname = "GKNodeJoyAxis"
    bl_label = "手柄_摇杆轴"
    bl_icon = "DRIVER"
    gk_category = "手柄"
    gk_doc = "读手柄某个轴/扳机的强度(-1 ~ 1)。自带死区，不会因为摇杆回中不准而漂移"
    gk_prop_defs = (
        ("gk_axis", "enum", "JOY_AXIS_LEFT_X", "轴", "", _JOY_AXES),
        ("gk_deadzone", "float", 0.2, "死区", "小于这个值算 0"),
        ("gk_device", "int", 0, "手柄编号", "0 表示第一个手柄"),
    )
    gk_outputs = (S.pin(FLOAT, "强度", 0.0),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_axis", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_deadzone", text="")
        row.prop(self, "gk_device", text="")


@N
class GKNodeJoyVector(B.GKExpressionNode):
    bl_idname = "GKNodeJoyVector"
    bl_label = "手柄_摇杆方向"
    bl_icon = "ORIENTATION_GIMBAL"
    gk_category = "手柄"
    gk_doc = ("把一根摇杆读成一个方向向量，可以直接接到『移动_按方向』上。"
              "建议配合『逻辑_比较』判断长度是否大于 0")
    gk_prop_defs = (
        ("gk_stick", "enum", "LEFT", "摇杆", "", (
            ("LEFT", "左摇杆", ""),
            ("RIGHT", "右摇杆", ""),
        )),
        ("gk_deadzone", "float", 0.2, "死区", "小于这个值算 0"),
        ("gk_device", "int", 0, "手柄编号", "0 表示第一个手柄"),
    )
    gk_outputs = (S.pin(VECTOR2, "方向", (0.0, 0.0)),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_stick", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_deadzone", text="")
        row.prop(self, "gk_device", text="")


@N
class GKNodeJoyButton(B.GKExpressionNode):
    bl_idname = "GKNodeJoyButton"
    bl_label = "手柄_按键按住"
    bl_icon = "HAND"
    gk_category = "手柄"
    gk_doc = "手柄按键是否按住"
    gk_prop_defs = (
        ("gk_button", "enum", "JOY_BUTTON_A", "按键", "", _JOY_BUTTONS),
        ("gk_device", "int", 0, "手柄编号", "0 表示第一个手柄"),
    )
    gk_outputs = (S.pin(BOOL, "按住", False),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_button", text="")
        row.prop(self, "gk_device", text="")


@N
class GKNodeJoyButtonDown(B.GKExpressionNode):
    bl_idname = "GKNodeJoyButtonDown"
    bl_label = "手柄_按键按下"
    bl_icon = "HAND"
    gk_category = "手柄"
    gk_doc = "手柄按键是否刚按下(本帧)。做跳跃 / 攻击用这个，避免按住连发"
    gk_prop_defs = (
        ("gk_button", "enum", "JOY_BUTTON_A", "按键", "", _JOY_BUTTONS),
        ("gk_device", "int", 0, "手柄编号", "0 表示第一个手柄"),
    )
    gk_outputs = (S.pin(BOOL, "刚按下", False),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_button", text="")
        row.prop(self, "gk_device", text="")


@N
class GKNodeJoyVibrate(B.GKStatementNode):
    bl_idname = "GKNodeJoyVibrate"
    bl_label = "手柄_震动"
    bl_icon = "RNDCURVE"
    gk_category = "手柄"
    gk_doc = "手柄震动一下(受击 / 爆炸反馈)。用场景树计时器自动停，不需要自己收尾"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "强弱", 0.6),
        S.pin(FLOAT, "时长", 0.2),
    )
    gk_input_props = {"强弱": "gk_weak", "时长": "gk_time"}
    gk_prop_defs = (
        ("gk_weak", "float", 0.6, "弱马达", "0~1"),
        ("gk_strong", "float", 0.6, "强马达", "0~1"),
        ("gk_time", "float", 0.2, "时长", "秒"),
        ("gk_device", "int", 0, "手柄编号", "0 表示第一个手柄"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_weak", text="")
        row.prop(self, "gk_strong", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_time", text="")
        row.prop(self, "gk_device", text="")


# ==========================================================================
# 触屏
# ==========================================================================
@N
class GKNodeTouchButton(B.GKExpressionNode):
    bl_idname = "GKNodeTouchButton"
    bl_label = "触屏_按键按住"
    bl_icon = "HAND"
    gk_category = "触屏"
    gk_doc = ("触屏上某个输入动作是否按住。移动端的虚拟按键通常会映射成输入动作，"
              "所以和键盘共用同一套动作名")
    gk_prop_defs = (
        ("gk_action", "string", "jump", "动作名",
         "Godot 输入映射里的动作名。用 move_*/jump 这类名字导出时会自动绑好按键"),
    )
    gk_outputs = (S.pin(BOOL, "按住", False),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_action", text="")


@N
class GKNodeTouchDrag(B.GKExpressionNode):
    bl_idname = "GKNodeTouchDrag"
    bl_label = "触屏_拖动方向"
    bl_icon = "ORIENTATION_GIMBAL"
    gk_category = "触屏"
    gk_doc = ("手指这次拖动产生的方向向量(松开手指归零)。做虚拟摇杆用。"
              "配合『输入_方向向量』那种判定逻辑即可驱动移动")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_scale", "float", 0.02, "灵敏度", "像素转成向量的比例"),
        ("gk_clamp", "float", 1.0, "最大长度", "向量长度上限"),
        ("gk_reset", "bool", True, "松手归零", "手指松开后是否把向量清零"),
    )
    gk_outputs = (S.pin(VECTOR2, "方向", (0.0, 0.0)),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_scale", text="")
        row.prop(self, "gk_clamp", text="")
        layout.prop(self, "gk_reset", text="松手归零", toggle=True)


# ==========================================================================
# 屏幕效果
# ==========================================================================
@N
class GKNodeScreenFilter(B.GKStatementNode):
    bl_idname = "GKNodeScreenFilter"
    bl_label = "屏幕_滤镜"
    bl_icon = "SHADERFX"
    gk_category = "屏幕效果"
    gk_doc = ("给整个画面蒙一层颜色(受击变红 / 中毒变绿 / 回忆泛黄)。"
              "用全屏着色器材质实现，强度可平滑过渡")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "强度", 0.5),
    )
    gk_input_props = {"强度": "gk_amount"}
    gk_prop_defs = (
        ("gk_amount", "float", 0.5, "强度", "0 = 原画面，1 = 完全染色"),
        ("gk_color", "color", (1.0, 0.15, 0.15, 1.0), "颜色", "叠加的颜色"),
        ("gk_softness", "float", 0.0, "过渡秒数", "0 表示立刻生效"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_color", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_amount", text="")
        row.prop(self, "gk_softness", text="")


# ==========================================================================
# 实用
# ==========================================================================
@N
class GKNodeComment(B.GKEventNode):
    bl_idname = "GKNodeComment"
    bl_label = "注释框"
    bl_icon = "INFO"
    gk_category = "实用"
    gk_doc = "在节点图里写备注，不生成任何代码"
    gk_is_comment = True
    gk_outputs = ()
    gk_prop_defs = (("gk_text", "string", "在这里写备注", "备注", ""),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_text", text="")


ALL_CLASSES = tuple(CLASSES)

# "添加节点"菜单里的分类排列顺序
CATEGORY_ORDER = ("事件", "流程", "移动", "动画", "动画系统", "实例", "变量", "信号", "输入",
                  "手柄", "触屏", "运算", "显示", "相机", "音效", "存档", "界面",
                  "屏幕效果", "射线", "定时", "列表", "字典", "时间", "实用")

# ── 二级子分类 ──────────────────────────────────────────────────────────
# 87 个节点如果只按一级分类铺开，最大的「运算」有 13 项，
# 「事件/流程/移动」也不少，翻起来还是累。
# 这张表给**大类**再切一层，菜单变成两级:
#     运算 ▸ 常量 / 数学 / 向量 / 逻辑 / 转换
# 没列在这里的分类保持一级(节点本来就少，再套一层反而多点一次)。
SUBCATEGORY_ORDER = {
    "运算": ("常量", "数学", "向量", "逻辑", "转换"),
}

# 节点 bl_idname -> 二级分类名。只给上面列了 SUBCATEGORY_ORDER 的分类登记。
SUBCATEGORY_OF = {
    # 常量
    "GKNodeConstFloat": "常量",
    "GKNodeConstText": "常量",
    "GKNodeConstBool": "常量",
    "GKNodeConstVector": "常量",
    # 数学
    "GKNodeMath": "数学",
    "GKNodeRandomRange": "数学",
    # 向量
    "GKNodeVectorMake": "向量",
    "GKNodeVectorSplit": "向量",
    "GKNodeVectorLength": "向量",
    "GKNodeVectorNormalize": "向量",
    # 逻辑
    "GKNodeCompare": "逻辑",
    "GKNodeLogic": "逻辑",
    # 转换
    "GKNodeConvert": "转换",
}

# 「常用」短名单:新手最需要的前 12 个，直接放菜单最上层。
# 目的是让第一次打开菜单的人不用翻 23 个分类就能起步。
COMMON_NODES = (
    "GKNodeOnReady",
    "GKNodeOnProcess",
    "GKNodeOnPhysicsProcess",
    "GKNodeOnInput",
    "GKNodeBranch",
    "GKNodeSequence",
    "GKNodeDoOnce",
    "GKNodeForLoop",
    "GKNodePrint",
    "GKNodeWait",
    "GKNodeSetVar",
    "GKNodeGetVar",
    "GKNodeMoveDirection",
    "GKNodeMoveAndSlide",
    "GKNodeJump",
    "GKNodeActionJustPressed",
    "GKNodeGetVector",
    "GKNodeComment",
)
