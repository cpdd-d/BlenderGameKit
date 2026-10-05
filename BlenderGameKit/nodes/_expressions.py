"""运算节点(常量/数学/向量/逻辑) —— 从 nodes/nodes.py 按领域拆出的一块(GPT 审查:5280 行的单文件)。

本模块里的节点类通过 `@N` 登记进 `_common.CLASSES`。门面 `nodes/nodes.py`
按拆分前的**原始顺序**导入各领域模块,保证 `ALL_CLASSES` 的顺序不变。
"""

from ._common import *  # noqa: F401,F403



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
    # 和『逻辑_布尔运算』一样,动画树的转场条件也需要它(速度 > 阈值)
    gk_tree_type = ("GKGameLogicTree", "GKAnimationTree")
    gk_doc = "比较两个值，输出布尔。A / B 是动态类型，可接数值或文本"
    gk_inputs = (S.pin(ANY, "A", 0.0), S.pin(ANY, "B", 0.0),)
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
    # ── 两张树里都能用(用户要求的"逻辑是否、或者")──────────────────
    # 动画转场条件也常常是"A 与 B"(按住加速 && 在地面)。以前这个节点
    # 只在玩法图的菜单里,用户在动画树里根本拉不出来,只能回到玩法图
    # 拼好再想办法 —— 而两张图之间不能连线,等于做不了。
    gk_tree_type = ("GKGameLogicTree", "GKAnimationTree")
    gk_doc = ("与 / 或 / 非 / 异或。与、或可以按『输入数量』加引脚"
              "(A、B、C…),照蓝图那种『多输入 AND』")
    gk_inputs = (S.pin(BOOL, "A", False), S.pin(BOOL, "B", False),)
    gk_prop_defs = (
        ("gk_op", "enum", "AND", "运算", "", (
            ("AND", "与 and(A 且 B)", ""),
            ("OR", "或 or(A 或 B)", ""),
            ("NOT", "非 not(A)", "只用 A 一个输入"),
            ("XOR", "异或 xor(A、B 不同为真)", ""),
        ), {"update": B._on_pin_count_changed}),
        # 属性名里含 "count" -> node_base 自动给它挂"重建引脚"的回调
        ("gk_input_count", "int", 2, "输入数量",
         "与/或 有几个输入(2~8)", None, {"min": 2, "max": 8}),
    )
    gk_outputs = (S.pin(BOOL, "结果", False),)


    def build_sockets(self):
        """按运算方式重建输入引脚。

        NOT 只需要 A 一个;与/或/异或按『输入数量』长出 A、B、C…。
        『非』把 B 也画出来的话,用户会以为它有用 —— 实际被忽略,
        那是最容易误导人的画法。
        """
        op = str(getattr(self, "gk_op", "AND"))
        if op == "NOT":
            names = ["A"]
        else:
            count = max(2, min(8, int(getattr(self, "gk_input_count", 2) or 2)))
            names = [chr(ord("A") + index) for index in range(count)]
        self.inputs.clear()
        for name in names:
            socket = self.inputs.new(S.BOOL[0], name)
            socket.default_value = False
        self.outputs.clear()
        for spec in self.gk_outputs:
            self.outputs.new(spec["kind"][0], spec["name"])

    def active_inputs(self):
        """当前真正参与运算的输入引脚名(按顺序)。"""
        return [socket.name for socket in self.inputs]

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_op", text="")
        if str(getattr(self, "gk_op", "AND")) != "NOT":
            layout.prop(self, "gk_input_count", text="输入")


@N
class GKNodeRandomRange(B.GKExpressionNode):
    bl_idname = "GKNodeRandomRange"
    bl_label = "数学_随机整数"
    bl_icon = "MOD_PARTICLES"
    gk_category = "运算"
    gk_doc = "取 [最小, 最大) 之间的随机整数(对应 randi_range)"
    gk_inputs = (S.pin(INT, "最小", 0), S.pin(INT, "最大", 10),)
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
    gk_inputs = (S.pin(FLOAT, "X", 0.0), S.pin(FLOAT, "Y", 0.0), S.pin(FLOAT, "Z", 0.0),)
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

    gk_outputs = (S.pin(FLOAT, "X", 0.0), S.pin(FLOAT, "Y", 0.0), S.pin(FLOAT, "Z", 0.0),)


@N
class GKNodeDistance(B.GKExpressionNode):
    bl_idname = "GKNodeDistance"
    bl_label = "取值_距离"
    bl_icon = "DRIVER_DISTANCE"
    gk_category = "运算"
    gk_doc = ("两个物体之间隔多远(米)。拾取/靠近/远离这类判定全靠它:"
              "『取值_距离』-> 『逻辑_比较』(< 1.2)-> 『如果_否则』。"
              "『甲』留空 = 脚本自己(所以\"离玩家多远\"只要填『乙』)。")
    gk_inputs = (
        S.pin(OBJECT, "甲", TARGET_SELF),
        S.pin(OBJECT, "乙", None),
    )
    gk_prop_defs = (
        ("gk_a", "node", "", "甲", "留空 = 脚本自己"),
        ("gk_b", "node", "", "乙", "另一个物体"),
    )
    gk_outputs = (S.pin(FLOAT, "距离", 0.0),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.label(text="甲")
        B.draw_target_picker(self, row, "gk_a")
        row = layout.row(align=True)
        row.label(text="乙")
        B.draw_target_picker(self, row, "gk_b")


@N
class GKNodeGetPosition(B.GKExpressionNode):
    bl_idname = "GKNodeGetPosition"
    bl_label = "取值_位置"
    bl_icon = "EMPTY_AXIS"
    gk_category = "运算"
    gk_doc = ("读一个物体现在在哪。输出**分开的 X / Y / Z**(要哪个接哪个),"
              "和『逻辑_比较』配合就能做\"掉出地图了吗\"\"走到终点了吗\"这类判定。")
    gk_inputs = (S.pin(OBJECT, "物体", TARGET_SELF),)
    gk_prop_defs = (
        ("gk_target", "node", "", "物体", "留空 = 脚本自己"),
        ("gk_global", "bool", True, "世界坐标",
         "勾上 = 世界坐标(推荐);取消 = 相对父节点"),
    )
    gk_outputs = (
        S.pin(FLOAT, "X", 0.0),
        S.pin(FLOAT, "Y", 0.0),
        S.pin(FLOAT, "Z", 0.0),
    )

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)
        layout.prop(self, "gk_global", text="世界坐标")


@N
class GKNodeSetPosition(B.GKStatementNode):
    bl_idname = "GKNodeSetPosition"
    bl_label = "移动_设置位置"
    # 图标必须是 Blender 认的枚举值:"TRANSFORM_MOVE" 不在列表里,
    # 会让**整个节点注册失败**(排查了半天:导出时报"尚未定义节点类型")
    bl_icon = "TRANSFORM_ORIGINS"
    gk_category = "移动"
    gk_doc = ("把物体**直接摆到**某个位置(不同于『移动_按方向』那种逐帧推)。"
              "掉出地图送回出生点、传送门、重置关卡都用它。"
              "三个数值可以连线,也能在节点上填。")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "X", 0.0),
        S.pin(FLOAT, "Y", 0.0),
        S.pin(FLOAT, "Z", 0.0),
    )
    gk_input_props = {"X": "gk_x", "Y": "gk_y", "Z": "gk_z"}
    gk_prop_defs = (
        ("gk_x", "float", 0.0, "X", ""),
        ("gk_y", "float", 0.0, "Y", ""),
        ("gk_z", "float", 0.0, "Z", "2D 里不用填"),
        ("gk_relative", "bool", False, "相对现在的位置",
         "勾上 = 在当前位置上再加这三个数(位移)"),
        ("gk_target", "node", "", "物体", "留空 = 脚本自己"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_x", text="X")
        row.prop(self, "gk_y", text="Y")
        row.prop(self, "gk_z", text="Z")
        layout.prop(self, "gk_relative", text="相对")
        B.draw_target_picker(self, layout)


@N
class GKNodeTextJoin(B.GKExpressionNode):
    bl_idname = "GKNodeTextJoin"
    bl_label = "文本_拼接"
    bl_icon = "SORT_ASC"
    gk_category = "运算"
    gk_doc = ("把两段文字接起来(A + B)。HUD 上显示\"金币 3/5\"这种做法:"
              "『变量_读取』-> 『文本_拼接』-> 『界面_文本』的『文字』。"
              "数字会自动转成文字,不用手动转换。")
    gk_inputs = (
        S.pin(TEXT, "甲", ""),
        S.pin(TEXT, "乙", ""),
    )
    gk_prop_defs = (
        ("gk_a", "string", "", "甲", "没连线时用这段文字"),
        ("gk_b", "string", "", "乙", ""),
        ("gk_middle", "string", "", "中间加", "比如一个空格或斜杠"),
    )
    gk_outputs = (S.pin(TEXT, "结果", ""),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_a", text="")
        row.prop(self, "gk_middle", text="")
        row.prop(self, "gk_b", text="")


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


@N
class GKNodeNumberText(B.GKExpressionNode):
    bl_idname = "GKNodeNumberText"
    bl_label = "文本_数字"
    bl_icon = "SORTALPHA"
    gk_category = "运算"
    gk_doc = ("把数值变成**给人看的文字**:整数显示成 '3' 而不是 '3.0',"
              "有小数才保留两位。\n"
              "── 为什么需要它(实测)────────────────────────────────\n"
              "Godot 里 `str(3.0)` 是 \"3.0\",所以 HUD 上直接拼变量会显示"
              "『血量 2.0』『金币 5.0』。想显示成『血量 2』就把它接在"
              "『变量_读取』和『文本_拼接』中间。")
    gk_inputs = (S.pin(FLOAT, "数值", 0.0),)
    gk_input_props = {"数值": "gk_value"}
    gk_prop_defs = (("gk_value", "float", 0.0, "数值", "未连线时转换这个数"),)
    gk_outputs = (S.pin(TEXT, "文字", ""),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_value", text="")
