"""列表/字典/手柄/触屏节点 —— 从 nodes/nodes.py 按领域拆出的一块(GPT 审查:5280 行的单文件)。

本模块里的节点类通过 `@N` 登记进 `_common.CLASSES`。门面 `nodes/nodes.py`
按拆分前的**原始顺序**导入各领域模块,保证 `ALL_CLASSES` 的顺序不变。
"""

from ._common import *  # noqa: F401,F403



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
    gk_outputs = (S.pin(FLOW, "有"), S.pin(FLOW, "没有"),)

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
    gk_category = "输入"
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
    gk_category = "输入"
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
    gk_category = "输入"
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
    gk_category = "输入"
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
    gk_category = "输入"
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
    gk_category = "输入"
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
    gk_category = "输入"
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


# ==========================================================================
# 引脚改名迁移
# ==========================================================================
# ── 为什么必须做(实测踩过)──────────────────────────────────────
# `build_sockets()` 只在节点 **init()** 时跑一次。所以改了
# `gk_inputs` 里的引脚名之后,**已经存在于 .blend 里的节点不会更新** ——
# 文件里那个节点的 `inputs` 还是旧名字。
#
# 表现极其隐蔽:
#     * 类的 gk_inputs 显示『上游』
#     * 文件里的节点 inputs 却是 ['进入', '条件']
#     * 用户按新名字连线连不上
#     * 导出时链路是空的,`_physics_process` 一片空白,**没有告警**
#
# 所以注册时主动扫一遍并把旧名字换掉 —— 顺便**恢复被改名丢掉的连线**
# (Blender 在 socket 名字变了之后会把原来的 link 丢掉)。
PIN_RENAMES = {
    "GKNodeAnimTransition": {"进入": "上游"},
}


def migrate_pin_names():
    """把所有 GK 节点树里过期的引脚名换成新的。返回改了几个节点。"""
    try:
        import bpy
    except Exception:
        return 0
    changed = 0
    for tree in getattr(getattr(bpy, "data", None), "node_groups", ()) or ():
        if getattr(tree, "bl_idname", "") not in ("GKGameLogicTree",
                                                  "GKAnimationTree"):
            continue
        for node in getattr(tree, "nodes", ()) or ():
            mapping = PIN_RENAMES.get(getattr(node, "bl_idname", ""), {})
            wanted = [spec.get("name") for spec in (node.gk_inputs or ())]
            # ── 除了改名,还要删掉**已经不存在**的引脚(用户 2026-10-03)──
            # 用户反复问"这些重复为什么删不掉":我在代码里把重复的物体引脚
            # 删了,但他**已经保存的 .blend** 里,那些引脚是当数据存着的 ——
            # 新建的节点没这个问题,老节点会一直顶着旧引脚。
            # 判据:当前引脚名既不在声明里、也不在"动态引脚"的名单里
            # (『进入 2』『出口 3』这种是按数量生成的),就重建。
            dynamic_prefixes = ("进入 ", "出口 ", "分支 ", "值 ", "输入 ")
            stale = []
            for socket in getattr(node, "inputs", ()) or ():
                name = getattr(socket, "name", "")
                if name in wanted:
                    continue
                if any(name.startswith(p) for p in dynamic_prefixes):
                    continue
                stale.append(name)
            if not stale:
                continue
            rebuild = getattr(node, "rebuild_sockets", None)
            if rebuild is None:
                continue
            try:
                rebuild()
                changed += 1
            except Exception:
                pass
    return changed
