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


@N
class GKNodeActionPressed(B.GKExpressionNode):
    bl_idname = "GKNodeActionPressed"
    bl_label = "输入_按键按住(旧)"
    bl_icon = "HAND"
    gk_category = "输入"
    # 被『输入_按键』取代(那个能捕获按键 + 选触发方式)。
    # 隐藏起来但仍然能加载、能生成代码 —— 老图不会坏(见 node_menu_entries 的说明)。
    gk_hidden = True
    gk_doc = ("【旧版,建议改用『输入_按键』】动作按键是否按住。"
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
    bl_label = "输入_按键按下(旧)"
    bl_icon = "MOUSE_LMB"
    gk_category = "输入"
    gk_hidden = True      # 同『输入_按键按住(旧)』
    gk_doc = ("【旧版,建议改用『输入_按键』】动作键是否刚按下(本帧)。"
              "默认 jump(导出时自动绑到空格)")
    # 默认 jump 而不是 Godot 内置的 ui_accept —— 后者绑的是**回车/空格**,
    # 但"跳跃用空格"更符合直觉,而且导出时会自动把空格绑上(见 DEFAULT_KEYS)
    gk_prop_defs = (("gk_action", "string", "jump", "动作名",
                     "Godot 输入映射里的动作名。用 move_*/jump 这类名字"
                     "导出时会自动绑好按键"),)
    gk_outputs = (S.pin(BOOL, "刚按下", False),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_action", text="")


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


@N
class GKNodeGetAxis(B.GKExpressionNode):
    bl_idname = "GKNodeGetAxis"
    bl_label = "输入_轴(旧)"
    gk_hidden = True     # 被『输入_方向』取代(那个一次给 x/y/向量)
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
    bl_label = "输入_方向向量(旧)"
    gk_hidden = True     # 被『输入_方向』取代(那个能捕获按键 + 直接给 x/y)
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
