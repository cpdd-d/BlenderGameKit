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
from ..gk_sockets import ANY, BOOL, FLOAT, FLOW, INT, OBJECT, TEXT, VECTOR

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
    gk_doc = "沿指定方向以指定速度移动(方向向量会自动归一化)"
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
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        B.draw_target_picker(self, row)
        row.prop(self, "gk_speed", text="")

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
    gk_doc = "播放 AnimationPlayer 里的动画片段"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "动画名", "idle"),
        target_pin("动画播放器"),
    )
    gk_input_props = {"动画名": "gk_anim_name", "动画播放器": "gk_player"}
    gk_prop_defs = (
        ("gk_player", "node", "AnimationPlayer", "动画播放器", "留空则用 $AnimationPlayer"),
        ("gk_anim_name", "string", "idle", "动画名", "未连线时的动画片段名"),
        ("gk_speed", "float", 1.0, "播放速度", "1.0 为原速"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_anim_name", text="")
        row.prop(self, "gk_speed", text="")

    def draw_buttons_ext(self, context, layout):
        layout.label(text="动画播放器 @onready 引用:")
        B.draw_target_picker(self, layout, "gk_player")


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
    gk_doc = "动作按键是否按住(对应 Input.is_action_pressed)"
    gk_prop_defs = (("gk_action", "string", "ui_right", "动作名", "Godot 输入映射里的动作"),)
    gk_outputs = (S.pin(BOOL, "按住", False),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_action", text="")


@N
class GKNodeActionJustPressed(B.GKExpressionNode):
    bl_idname = "GKNodeActionJustPressed"
    bl_label = "输入_按键按下"
    bl_icon = "MOUSE_LMB"
    gk_category = "输入"
    gk_doc = "动作键是否刚按下(本帧，对应 is_action_just_pressed)"
    gk_prop_defs = (("gk_action", "string", "ui_accept", "动作名", "Godot 输入映射里的动作"),)
    gk_outputs = (S.pin(BOOL, "刚按下", False),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_action", text="")


@N
class GKNodeGetAxis(B.GKExpressionNode):
    bl_idname = "GKNodeGetAxis"
    bl_label = "输入_轴"
    bl_icon = "ARROW_LEFTRIGHT"
    gk_category = "输入"
    gk_doc = "取输入轴强度(-1 ~ 1)，用两个动作拼出一个轴"
    gk_prop_defs = (
        ("gk_negative", "string", "ui_left", "负方向动作", "例如 ui_left"),
        ("gk_positive", "string", "ui_right", "正方向动作", "例如 ui_right"),
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
    gk_doc = "取方向输入并合成向量(对应 Input.get_vector)"
    gk_prop_defs = (
        ("gk_left", "string", "ui_left", "左", ""),
        ("gk_right", "string", "ui_right", "右", ""),
        ("gk_up", "string", "ui_up", "上", ""),
        ("gk_down", "string", "ui_down", "下", ""),
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
CATEGORY_ORDER = ("事件", "流程", "移动", "动画", "实例", "变量", "信号", "输入", "运算", "实用")
