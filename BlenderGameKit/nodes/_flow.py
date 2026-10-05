"""流程节点(分支/循环/等待/打印) —— 从 nodes/nodes.py 按领域拆出的一块(GPT 审查:5280 行的单文件)。

本模块里的节点类通过 `@N` 登记进 `_common.CLASSES`。门面 `nodes/nodes.py`
按拆分前的**原始顺序**导入各领域模块,保证 `ALL_CLASSES` 的顺序不变。
"""

from ._common import *  # noqa: F401,F403



@N
class GKNodeCallFunction(B.GKStatementNode):
    bl_idname = "GKNodeCallFunction"
    bl_label = "调用_函数"
    bl_icon = "FILE_REFRESH"
    gk_category = "事件"
    gk_doc = "调用函数(本脚本的自定义函数，或目标节点上的方法)"
    gk_inputs = (S.pin(FLOW, "执行"),)
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
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(BOOL, "条件", False),)
    gk_outputs = (S.pin(FLOW, "是"), S.pin(FLOW, "否"),)


@N
class GKNodeDoOnce(B.GKStatementNode):
    bl_idname = "GKNodeDoOnce"
    bl_label = "只执行一次"
    bl_icon = "LOCKED"
    gk_category = "流程"
    gk_doc = "第一次到达时执行，之后自动跳过"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(BOOL, "重置", False),)
    gk_outputs = (S.pin(FLOW, "首次"),)



@N
class GKNodeCooldown(B.GKStatementNode):
    bl_idname = "GKNodeCooldown"
    bl_label = "流程_冷却"
    bl_icon = "TIME"
    gk_category = "流程"
    gk_doc = ("冷却门 / 无敌帧:隔『秒数』才放行一次。时间没到就走"
              "『还在冷却』那条出口。\n"
              "── 什么时候需要它(用户实测)──────────────────────────\n"
              "触发区、按键、子弹这些东西会在窗口里**连续触发几十次**,"
              "后面那条链(播动画、扣血、播音效)就跟着重来一遍 —— "
              "想让它\"先放完 / 过一会儿再重复\"就用这个节点挡住。\n"
              "受伤后接一个 = 无敌帧;按键后接一个 = 防连发;\n"
              "『秒数』留 0.5~1 是手感值,填 0 就是每次都能过。\n"
              "『每几次放行一次』= 和秒数**叠加**的抽稀:填 2 就是"
              "每来 2 次才放行 1 次(默认 1 = 老行为,每次都放)")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "秒数", 1.0),)
    gk_input_props = {"秒数": "gk_seconds"}
    gk_prop_defs = (
        ("gk_seconds", "float", 1.0, "秒数", "两次放行之间至少隔多久"),
        ("gk_every", "int", 1, "每几次放行一次",
         "1 = 每次都能过(默认);填 N = 每来 N 次才放行一次",
         None, {"min": 1, "max": 100}),
    )
    gk_outputs = (S.pin(FLOW, "可以"), S.pin(FLOW, "还在冷却"),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_seconds", text="冷却(秒)")
        row.prop(self, "gk_every", text="每 N 次")


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

    gk_outputs = (S.pin(FLOW, "1"), S.pin(FLOW, "2"), S.pin(FLOW, "3"),)

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
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(INT, "值", 0),)
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
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(INT, "次数", 3),)
    gk_outputs = (S.pin(FLOW, "循环体"), S.pin(FLOW, "结束后"),)
    gk_prop_defs = (("gk_index_name", "string", "i", "索引变量", "循环索引变量名"),)


@N
class GKNodeWait(B.GKStatementNode):
    bl_idname = "GKNodeWait"
    bl_label = "等待_秒"
    bl_icon = "MOD_TIME"
    gk_category = "流程"
    gk_doc = "延迟一段时间再继续(协程等待，不卡住游戏)"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "秒", 1.0),)
    gk_prop_defs = (("gk_use_process", "bool", False, "使用真实时间", "忽略引擎暂停"),)


@N
class GKNodePrint(B.GKStatementNode):
    bl_idname = "GKNodePrint"
    bl_label = "打印_输出"
    bl_icon = "CONSOLE"
    gk_category = "流程"
    gk_doc = "在 Godot 的输出面板打印一行文本"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(TEXT, "内容", "你好，世界"),)
    gk_input_props = {"内容": "gk_text"}
    gk_prop_defs = (("gk_text", "string", "你好，世界", "内容", "未连线时打印的字面量"),)
