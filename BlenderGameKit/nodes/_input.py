"""输入节点(键盘/鼠标) —— 从 nodes/nodes.py 按领域拆出的一块(GPT 审查:5280 行的单文件)。

本模块里的节点类通过 `@N` 登记进 `_common.CLASSES`。门面 `nodes/nodes.py`
按拆分前的**原始顺序**导入各领域模块,保证 `ALL_CLASSES` 的顺序不变。
"""

from ._common import *  # noqa: F401,F403



# ==========================================================================
# 输入
# ==========================================================================
# ── 为什么改成"一个节点 + 触发方式"(用户 2026-10-03,对着 UPBGE 截图)──
# 用户原话:"在输入按键时,按下键盘任意键即可设置哪个键盘控制输入等,
# 如 upbge 的 keyboard key 设计逻辑"。
#
# UPBGE 的 Keyboard Key 就一个节点:一个 `Tap` 下拉(按下/按住/松开)+
# 一个 `Press & Choose` 按钮(按哪个键就是哪个)。我们原来是**两个**节点
# (『按键按住』『按键按下』)而且只能**打字**填动作名 ——
# 用户得先记住"jump 绑空格、move_right 绑 D"这套约定。
#
# 现在合成一个 『输入_按键』:
#   * 『触发方式』:按住 / 刚按下 / 松开(一个节点顶三个)
#   * 『按键』:按一下键盘上任意键就绑上(不用记名字)
#   * 『输入动作』:导出到 Godot 的映射名;留空 = 按按键名自动生成
@N
class GKNodeKey(B.GKExpressionNode):
    bl_idname = "GKNodeKey"
    bl_label = "输入_按键"
    bl_icon = "HAND"
    gk_category = "输入"
    gk_tree_type = BOTH_TREES
    gk_doc = ("按一下键盘就知道你按的是哪个键(**不用记动作名**)。"
              "『触发方式』一个节点顶三个:按住 / 刚按下(这一帧)/ 松开。"
              "── 两个填法,任选 ──"
              "(1) 点右边那个『按一下键』按钮,按哪个键就绑哪个;"
              "(2) 『输入动作』填 Godot 输入映射里的动作名 —— 用 move_*/jump "
              "这类名字导出时会自动绑好默认键。"
              "留空 = 按你捕获的那个键自动生成动作名。"
              "动画树的转场条件也能直接用(两张图都能放)")
    gk_prop_defs = (
        ("gk_key", "string", "", "按键",
         "捕获到的键(点右边的按钮,按一下你想用的键)"),
        ("gk_action", "string", "", "输入动作",
         "留空 = 按按键名自动生成(比如 W -> key_w)"),
        ("gk_trigger", "enum", "PRESSED", "触发方式",
         "和 UPBGE 的 Tap 一个意思",
         (("PRESSED", "按住", "按住期间一直为真"),
          ("JUST", "刚按下", "只在这一帧为真 —— 跳跃、攻击用这个"),
          ("RELEASED", "松开", "松手那一刻为真 —— 蓄力、拖拽用这个"))),
    )
    gk_outputs = (S.pin(BOOL, "结果", False),)


    def resolved_action(self):
        """导出用的输入动作名:填了就用填的,没填按按键名生成。"""
        raw = str(getattr(self, "gk_action", "") or "").strip()
        if raw:
            return raw
        key = str(getattr(self, "gk_key", "") or "").strip()
        if not key:
            return "jump"
        # ── 数字键要保住那个数字(第四十八轮:灯光示例踩到)────────────
        # `snake_case("1", "key")` 会因为"以数字开头"当不合法标识符、
        # 直接落到 fallback —— 于是 1~9 全变成同一个动作 `key_key`:
        # **按一下 1 会把所有数字键绑的逻辑一起触发**(实测)。
        # `key_1` 本身是合法标识符,所以数字键单独拼,不走 snake_case。
        if key[:1].isdigit():
            return "key_" + key.lower()
        return "key_" + snake_case(key, "key").lower()

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_trigger", text="")
        # 捕获按钮:按一下键盘上任意键就绑上(和 UPBGE 的 Press & Choose 一样)
        capture = row.operator("gk.capture_key", text="", icon="KEYINGSET")
        capture.node_name = self.name
        row = layout.row(align=True)
        row.prop(self, "gk_key", text="按键", icon="SORTALPHA")
        row.prop(self, "gk_action", text="", icon="DOT")
        if not str(getattr(self, "gk_key", "") or "").strip():
            layout.label(text="点右边的按钮,按一下你要用的键", icon="INFO")




# ==========================================================================
# 输入 / 鼠标(UPBGE 的『输入 ▸ 鼠标』那一栏,我们以前完全没有)
# ==========================================================================
@N
class GKNodeMouseButton(B.GKExpressionNode):
    bl_idname = "GKNodeMouseButton"
    bl_label = "鼠标_按键按住"
    bl_icon = "MOUSE_LMB"
    gk_category = "输入"
    gk_tree_type = BOTH_TREES
    gk_doc = "鼠标键是否按住(对应 Input.is_mouse_button_pressed)"
    gk_prop_defs = (
        ("gk_button", "enum", "LEFT", "哪个键", "",
         (("LEFT", "左键", ""), ("RIGHT", "右键", ""),
          ("MIDDLE", "中键", ""), ("MB4", "侧键 4", ""), ("MB5", "侧键 5", ""))),
    )
    gk_outputs = (S.pin(BOOL, "按住", False),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_button", text="")


@N
class GKNodeMouseButtonDown(B.GKExpressionNode):
    bl_idname = "GKNodeMouseButtonDown"
    bl_label = "鼠标_按键按下"
    bl_icon = "MOUSE_LMB"
    gk_category = "输入"
    gk_tree_type = BOTH_TREES
    gk_doc = ("鼠标键是否刚按下(这一帧)。"
              "Godot 的鼠标键**没有** just_pressed,所以这里靠记住上一帧实现")
    gk_prop_defs = (
        ("gk_button", "enum", "LEFT", "哪个键", "",
         (("LEFT", "左键", ""), ("RIGHT", "右键", ""),
          ("MIDDLE", "中键", ""), ("MB4", "侧键 4", ""), ("MB5", "侧键 5", ""))),
    )
    gk_outputs = (S.pin(BOOL, "刚按下", False),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_button", text="")


@N
class GKNodeMouseWheel(B.GKExpressionNode):
    bl_idname = "GKNodeMouseWheel"
    bl_label = "鼠标_滚轮"
    bl_icon = "MOUSE_MMB_SCROLL"
    gk_category = "输入"
    gk_doc = ("这一帧滚轮滚了多少(往上正、往下负)。"
              "做缩放 / 切武器用:接『逻辑_比较』判 > 0 或 < 0,"
              "或者直接加到相机 zoom 上")
    gk_outputs = (S.pin(FLOAT, "滚轮", 0.0),)



@N
class GKNodeMousePosition(B.GKExpressionNode):
    bl_idname = "GKNodeMousePosition"
    bl_label = "鼠标_位置"
    bl_icon = "CURSOR"
    gk_category = "输入"
    gk_doc = ("鼠标在屏幕上的位置(像素)。"
              "2D 游戏要换算到世界坐标的话接『实例_获取节点』拿到相机再算,"
              "或者直接用『屏幕』坐标做准星")
    gk_prop_defs = (
        ("gk_relative", "bool", False, "相对上一帧",
         "勾上 = 输出这一帧移动了多少(做第一人称视角用)"),
    )
    gk_outputs = (S.pin(VECTOR2, "位置", (0.0, 0.0)),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_relative", text="相对上一帧")


@N
class GKNodeMouseMotion(B.GKExpressionNode):
    bl_idname = "GKNodeMouseMotion"
    bl_label = "鼠标_移动量"
    bl_icon = "MOUSE_MOVE"
    gk_category = "输入"
    gk_doc = ("鼠标这一帧移动了多少(像素/帧)。"
              "第一人称/越肩视角转镜头用:`rotation.y -= 移动量.x * 灵敏度`。"
              "配合『鼠标_模式 = 锁定』才不会撞到屏幕边缘")
    gk_outputs = (S.pin(VECTOR2, "移动量", (0.0, 0.0)),)



@N
class GKNodeMouseMode(B.GKStatementNode):
    bl_idname = "GKNodeMouseMode"
    bl_label = "鼠标_模式"
    bl_icon = "MOUSE_MOVE"
    gk_category = "输入"
    gk_doc = ("设置鼠标模式。做 3D 视角用『锁定』(鼠标不会跑出窗口、也不显示),"
              "做菜单 / 2D 点选用『可见』。"
              "注意:锁定之后要用别的键才能退出来(比如 Esc)")
    gk_inputs = (S.pin(FLOW, "执行"),)

    gk_prop_defs = (
        ("gk_mode", "enum", "CAPTURED", "模式", "",
         (("VISIBLE", "可见", "正常显示,能点界面"),
          ("HIDDEN", "隐藏", "看不见但还能点(触摸屏/虚拟摇杆用)"),
          ("CAPTURED", "锁定", "藏起来并且锁在窗口里 —— 3D 转视角用"),
          ("CONFINED", "限制在窗口内", "看得见但不许跑出窗口"))),
        ("gk_visible_default", "bool", True, "可见", ""),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_mode", text="")


@N
class GKNodeInputDirection(B.GKExpressionNode):
    bl_idname = "GKNodeInputDirection"
    bl_label = "输入_方向"
    bl_icon = "ORIENTATION_GIMBAL"
    gk_category = "输入"
    gk_tree_type = BOTH_TREES
    gk_doc = ("**一个节点搞定移动方向** —— 上下左右四个键,直接给出"
              "『方向』(二维向量)/『左右』X /『前后』Y。"
              "接『移动_按方向』的『方向』引脚就能走,"
              "**不用再自己设变量拼向量**(虚幻蓝图 MoveForward / MoveRight "
              "那条路子,这里是内建的)。"
              "── 两种填法,混着用也行 ──"
              "(1) 每个键点『按一下键』直接捕获(不用记动作名);"
              "(2) 『动作』填 Godot 动作名 —— 手柄 / 虚拟摇杆走同一套动作,"
              "键盘手柄就自动互通了")
    gk_prop_defs = (
        ("gk_key_up", "string", "", "上键", "点『按一下键』捕获;留空用默认 W"),
        ("gk_action_up", "string", "", "上动作", "留空按按键自动生成"),
        ("gk_key_down", "string", "", "下键", ""),
        ("gk_action_down", "string", "", "下动作", ""),
        ("gk_key_left", "string", "", "左键", ""),
        ("gk_action_left", "string", "", "左动作", ""),
        ("gk_key_right", "string", "", "右键", ""),
        ("gk_action_right", "string", "", "右动作", ""),
        ("gk_deadzone", "float", 0.2, "死区",
         "摇杆回中不准时用;纯键盘可以填 0"),
        ("gk_normalize", "bool", True, "斜着走要归一化",
         "勾上 = 斜向速度不会变成 1.41 倍(和虚幻的 Normalize 一个意思)"),
    )
    gk_outputs = (
        S.pin(VECTOR2, "方向", (0.0, 0.0)),
        S.pin(FLOAT, "左右", 0.0),
        S.pin(FLOAT, "前后", 0.0),
        S.pin(BOOL, "有输入", False),
    )

    _DIRECTIONS = ("up", "down", "left", "right")

    def action_for(self, direction):
        """这个方向的输入动作名:填了用填的,没填按按键生成,再没填用默认。"""
        slot = {"up": ("gk_key_up", "gk_action_up", "move_up"),
                "down": ("gk_key_down", "gk_action_down", "move_down"),
                "left": ("gk_key_left", "gk_action_left", "move_left"),
                "right": ("gk_key_right", "gk_action_right", "move_right")}
        key_prop, action_prop, fallback = slot[direction]
        action = str(getattr(self, action_prop, "") or "").strip()
        if action:
            return action, str(getattr(self, key_prop, "") or "")
        key = str(getattr(self, key_prop, "") or "").strip()
        if key:
            return "key_" + key.lower(), key
        return fallback, ""

    def draw_buttons(self, context, layout):
        for label, direction in (("上", "up"), ("下", "down"),
                                 ("左", "left"), ("右", "right")):
            key_prop = f"gk_key_{direction}"
            action_prop = f"gk_action_{direction}"
            row = layout.row(align=True)
            row.label(text=f"{label}:")
            row.prop(self, key_prop, text="")
            capture = row.operator("gk.capture_key", text="", icon="KEYINGSET")
            capture.node_name = self.name
            capture.key_prop = key_prop
            capture.action_prop = action_prop
            row.prop(self, action_prop, text="", icon="DOT")
        row = layout.row(align=True)
        row.prop(self, "gk_deadzone", text="死区")
        row.prop(self, "gk_normalize", text="归一化")




