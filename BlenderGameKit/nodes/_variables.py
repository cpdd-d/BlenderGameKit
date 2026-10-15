"""变量与信号节点 —— 从 nodes/nodes.py 按领域拆出的一块(GPT 审查:5280 行的单文件)。

本模块里的节点类通过 `@N` 登记进 `_common.CLASSES`。门面 `nodes/nodes.py`
按拆分前的**原始顺序**导入各领域模块,保证 `ALL_CLASSES` 的顺序不变。
"""

from ._common import *  # noqa: F401,F403



# ==========================================================================
# 变量与信号
# ==========================================================================
# 一张『变量_设置』卡片最多能写几个变量
SET_VAR_SLOTS = 6


@N
class GKNodeSetVar(B.GKStatementNode):
    bl_idname = "GKNodeSetVar"
    bl_label = "变量_设置"
    bl_icon = "CHECKBOX_DEHLT"
    gk_category = "变量"
    # ── 一张卡片设多个变量(用户 2026-10-03 的要求)──────────────────
    # 用户原话:"在设置变量属性时,卡片里可以设置多个并且引脚可连接不同节点,
    # 如虚幻蓝图的变量节点可重复连接"。
    #
    # 蓝图的 Set 节点展开后就是一串变量,每个有自己的值引脚 ——
    # 我们照这个做成『数量』可调:值 1 / 值 2 / 值 3 …,每个引脚
    # **可以接不同的上游节点**(一个输入引脚只接一条线,所以要多接就加数量)。
    # 第 1 个仍然叫『值』、属性仍然叫 `gk_var_name`/`gk_value` ——
    # 老文件里已经接好的线不会断。
    gk_doc = ("给脚本变量赋值(首次出现会自动声明成成员变量)。"
              "『数量』调大就能**一次设好几个变量**,每个『值』引脚可以接"
              "不同的节点 —— 和虚幻蓝图的 Set 节点一样。"
              "执行流出口可以接判断节点(『如果_否则』),也可以扇出到好几处")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(ANY, "值", 100),)
    gk_input_props = {"值": "gk_value"}
    gk_prop_defs = (
        ("gk_var_count", "int", 1, "数量",
         f"一次设几个变量(1~{SET_VAR_SLOTS})—— 引脚会长出 值 2 / 值 3 …",
         None, {"min": 1, "max": SET_VAR_SLOTS}),
        ("gk_var_name", "string", "health", "变量名", "GDScript 成员变量名"),
        ("gk_value", "float", 100.0, "值", "未连线时的值"),
    )
    # 第 2~N 个变量的属性(数量够了才画/才用)
    for _slot in range(2, SET_VAR_SLOTS + 1):
        gk_prop_defs += (
            (f"gk_var_name_{_slot}", "string", "", f"变量名 {_slot}", ""),
            (f"gk_value_{_slot}", "float", 0.0, f"值 {_slot}", ""),
        )

    def build_sockets(self):
        """按『数量』生成引脚:执行 / 值 / 值 2 / 值 3 …"""
        count = max(1, min(SET_VAR_SLOTS,
                           int(getattr(self, "gk_var_count", 1) or 1)))
        self.inputs.clear()
        self.inputs.new(S.FLOW[0], "执行")
        for index in range(count):
            name = "值" if index == 0 else f"值 {index + 1}"
            socket = self.inputs.new(S.ANY[0], name)
            # 引脚默认值 = 节点上那个数字框 —— 界面上看得见,
            # 生成器没连线时也能拿到正确的值(不给的话是 None -> `= null`)
            suffix = "" if index == 0 else f"_{index + 1}"
            try:
                socket.default_value = getattr(self, f"gk_value{suffix}", 0.0)
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

    def slots(self):
        """(变量名, 属性里的值) 列表,只取填了名字的那些。"""
        out = []
        for index in range(1, SET_VAR_SLOTS + 1):
            suffix = "" if index == 1 else f"_{index}"
            name = str(getattr(self, f"gk_var_name{suffix}", "") or "").strip()
            if not name:
                continue
            out.append((index, name, getattr(self, f"gk_value{suffix}", 0.0)))
        return out

    def draw_buttons(self, context, layout):
        count = max(1, min(SET_VAR_SLOTS,
                           int(getattr(self, "gk_var_count", 1) or 1)))
        row = layout.row(align=True)
        row.prop(self, "gk_var_count", text="变量")
        row.prop(self, "gk_var_name", text="", icon="SORTALPHA")
        # 放大镜 = 从侧栏『变量』里**声明过的**名字里挑(用户 2026-10-04),
        # 省得手打名字打错;没声明过的名字照样能手打进去(兼容老图)。
        pick = row.operator("gk.pick_var", text="", icon="VIEWZOOM")
        pick.node_name = self.name
        pick.slot = 1
        row.prop(self, "gk_value", text="")
        for index in range(2, count + 1):
            row = layout.row(align=True)
            row.label(text=f"{index}.", icon="BLANK1")
            row.prop(self, f"gk_var_name_{index}", text="")
            pick = row.operator("gk.pick_var", text="", icon="VIEWZOOM")
            pick.node_name = self.name
            pick.slot = index
            row.prop(self, f"gk_value_{index}", text="")


@N
class GKNodeAddVar(B.GKStatementNode):
    bl_idname = "GKNodeAddVar"
    bl_label = "变量_增减"
    bl_icon = "ADD"
    gk_category = "变量"
    gk_doc = ("给变量**加**一个数(填负数就是减)。比"
              "『变量_读取』->『数学_运算』->『变量_设置』三个节点省事 —— "
              "拾取 +1、扣血 -1、扣弹药 -1 都靠它。\n"
              "『新值』引脚给的是**加完之后**的值(接了线就能直接拿去显示/比较,"
              "不用再读一次变量)\n"
              "『限制范围』勾上 = 加完的值会被夹在『最小』~『最大』之间"
              "(血量别掉成负数、金币别溢出都用它)")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "增减", 1.0),)
    gk_input_props = {"增减": "gk_amount"}
    gk_prop_defs = (
        ("gk_var_name", "string", "分数", "变量名", "GDScript 成员变量名"),
        ("gk_amount", "float", 1.0, "增减", "未连线时的数量(负数就是减)"),
        ("gk_clamp", "bool", False, "限制范围",
         "勾上后加完的值夹在『最小』~『最大』之间(不勾 = 老行为,不夹)"),
        ("gk_min", "float", 0.0, "最小", "夹取的下限(血量这类填 0)"),
        ("gk_max", "float", 100.0, "最大", "夹取的上限"),
    )
    gk_outputs = (S.pin(FLOW, "完成"), S.pin(FLOAT, "新值", 0.0),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_var_name", text="", icon="SORTALPHA")
        # 放大镜 = 从侧栏『变量』里声明过的名字里挑(和『变量_设置』一致)
        pick = row.operator("gk.pick_var", text="", icon="VIEWZOOM")
        pick.node_name = self.name
        pick.slot = 1
        row.prop(self, "gk_amount", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_clamp", text="限制范围", toggle=True)
        if getattr(self, "gk_clamp", False):
            row.prop(self, "gk_min", text="")
            row.label(text="~")
            row.prop(self, "gk_max", text="")


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
        row = layout.row(align=True)
        row.prop(self, "gk_var_name", text="")
        pick = row.operator("gk.pick_var", text="", icon="VIEWZOOM")
        pick.node_name = self.name
        pick.slot = 1


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
    gk_inputs = (S.pin(FLOW, "执行"),)
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
# 玩家偏好(跨关卡/跨启动,第一百一十三轮)
# ==========================================================================
# 用户:"离开一个关卡后,我希望能够保存玩家的视角设置等"。
#
# 场景脚本的变量是**每关一份**的 —— C/V 切到第一人称,走进第二关又回
# 第三人称。『偏好_记住/取回』走导出时生成的 autoload `GkPlayerPrefs`:
# 切场景不销毁,还能落盘到 user://player_prefs.cfg(下次启动还在)。
# 视角模式、鼠标灵敏度、画质选择……任何"玩家的选择"都该用它,
# 而不是塞进存档(存档是"进度",偏好是"习惯")。
PREF_KINDS = (
    ("TEXT", "文字", "存一段文字(视角模式'第一'/'第三'这类)"),
    ("NUMBER", "数字", "存一个数(鼠标灵敏度这类)"),
    ("BOOL", "真假", "存一个布尔(是否开启某选项)"),
)


@N
class GKNodePrefSave(B.GKStatementNode):
    bl_idname = "GKNodePrefSave"
    bl_label = "偏好_记住"
    bl_icon = "BOOKMARKS"
    gk_category = "变量"
    gk_doc = ("把一个值记到**玩家偏好**里 —— 跨关卡保留,勾了『写入磁盘』"
              "的话连下次启动游戏都还在。适合存视角模式(C/V 选的第一/第三"
              "人称)、鼠标灵敏度、画质选择这类\"玩家的选择\"。\n"
              "在另一关用『偏好_取回』读回来。想存\"游戏进度\"(钥匙/血量)"
              "请用『存档_保存』—— 进度和偏好是两回事")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(ANY, "值", None),)
    gk_input_props = {"值": "gk_value_text"}
    gk_prop_defs = (
        ("gk_pref_key", "string", "视角", "键", "偏好的名字(取回时填同一个)"),
        ("gk_kind", "enum", "TEXT", "类型", "没连线时的值是什么类型", PREF_KINDS),
        ("gk_value_text", "string", "第三", "值", "没连线时记住的文字"),
        ("gk_value_number", "float", 0.0, "值", "没连线时记住的数字"),
        ("gk_value_bool", "bool", False, "值", "没连线时记住的真假"),
        ("gk_save_disk", "bool", True, "写入磁盘",
         "勾上 = 顺手存进 user://player_prefs.cfg,下次启动游戏还在;"
         "不勾 = 只活到这次退出"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_pref_key", text="")
        row.prop(self, "gk_kind", text="")
        kind = str(getattr(self, "gk_kind", "TEXT") or "TEXT")
        if kind == "NUMBER":
            layout.prop(self, "gk_value_number", text="值")
        elif kind == "BOOL":
            layout.prop(self, "gk_value_bool", text="值")
        else:
            layout.prop(self, "gk_value_text", text="值")
        layout.prop(self, "gk_save_disk", text="写入磁盘")


@N
class GKNodePrefLoad(B.GKExpressionNode):
    bl_idname = "GKNodePrefLoad"
    bl_label = "偏好_取回"
    bl_icon = "BOOKMARKS"
    gk_category = "变量"
    gk_doc = ("读回『偏好_记住』存的值 —— 开局接在『每当_准备』后面就能"
              "恢复玩家的选择(上一关切了第一人称,这一关开局就是第一人称)。"
              "『有没有』= 这个键从来没被记住过(第一次玩),用它走默认分支")
    gk_outputs = (S.pin(ANY, "值", None), S.pin(BOOL, "有没有", False),)

    gk_prop_defs = (
        ("gk_pref_key", "string", "视角", "键", "『偏好_记住』里填的那个名字"),
        ("gk_kind", "enum", "TEXT", "类型", "没记住时给『默认』的类型", PREF_KINDS),
        ("gk_default_text", "string", "第三", "默认", "从没记住过时给什么"),
        ("gk_default_number", "float", 0.0, "默认", "从没记住过时给什么"),
        ("gk_default_bool", "bool", False, "默认", "从没记住过时给什么"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_pref_key", text="")
        row.prop(self, "gk_kind", text="")
