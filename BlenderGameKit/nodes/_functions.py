"""函数节点(用户 2026-10-05 第十七轮):查看函数 / 自定义函数 / 折叠函数。

用户原话:"可查看函数,自定义函数,折叠函数等"。

── 为什么这是目前最大的结构性缺口 ────────────────────────────────
在此之前整张图只能生成固定的 `_ready` / `_physics_process`:
想复用一段逻辑**只能复制节点**,复制出来的几份以后还得各改一遍。

现在:
    『函数_定义』  一个函数的**起点** —— 从这里接出去的节点编译成
                   一个 `func 名字(参数) -> 返回:`(函数体就是那条执行流)
    『函数_调用』  在别处调用它 —— 参数引脚按**侧栏『函数』框里的声明**
                   自动长出来,有返回值时还多一个『结果』输出

函数声明(名字 / 返回类型 / 参数)在侧栏一处写,和变量声明同一套做法:
节点上从下拉里挑,不存在"两处名字对不上"的问题。
"""

from ._common import *  # noqa: F401,F403


# ==========================================================================
# 函数
# ==========================================================================
@N
class GKNodeFunctionDefine(B.GKStatementNode):
    bl_idname = "GKNodeFunctionDefine"
    bl_label = "函数_定义"
    bl_icon = "FILE_SCRIPT"
    gk_category = "函数"
    gk_doc = ("**一个函数的起点**(照虚幻蓝图的 Function):从这里接出去的节点"
              "会被编译成 `func 名字(参数):` —— 函数体就是那条执行流。"
              "── 用法 ──"
              "① 侧栏『函数』框里加一个函数,写好名字 / 返回 / 参数;"
              "② 拖一个本节点出来,从下拉里挑那个函数;"
              "③ 从它的『执行』口开始连函数体(和平时连线一样);"
              "④ 想用参数就在函数体里用『变量_读取』填参数名 —— "
              "导出的函数签名里就有这个名字,读它就是读参数。"
              "── 返回值 ──"
              "声明了返回类型的函数,把要返回的值接在『返回』引脚上,"
              "函数走到头会 `return` 它。"
              "── 折叠 ──"
              "选好一串节点,右键 → 『折叠成函数』可以一键把这段搬进函数、"
              "在原位置留一个调用节点")
    # 它自己就是"函数的入口",不需要别人喂执行流 —— 但为了让『添加节点』
    # 菜单里的分类检查一致,还是留一个执行输入(接进来会被明确拒绝,
    # 见 codegen 里 GKNodeFunctionDefine 的处理器)。
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(ANY, "返回", None),
    )
    gk_prop_defs = (
        ("gk_func_name", "string", "", "函数",
         "侧栏『函数』框里声明的名字"),
    )
    gk_outputs = (S.pin(FLOW, "执行"),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_func_name", text="", icon="FILE_SCRIPT")
        pick = row.operator("gk.pick_function", text="", icon="VIEWZOOM")
        pick.node_name = self.name
        pick.func_prop = "gk_func_name"


@N
class GKNodeFunctionCall(B.GKStatementNode):
    bl_idname = "GKNodeFunctionCall"
    bl_label = "函数_调用"
    bl_icon = "PLAY"
    gk_category = "函数"
    gk_doc = ("**调用一个函数**(在侧栏『函数』框里声明的那些)。"
              "参数引脚按声明自动长出来 —— 声明里写几个参数,这里就有几个框;"
              "有返回值时右侧多一个『结果』输出,可以直接接进别的节点。"
              "── 注意 ──"
              "『函数_定义』那个节点只是函数的起点,**不产生调用** —— "
              "要让函数跑起来,得在某个执行流上接这个『函数_调用』节点")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_func_name", "string", "", "函数",
         "侧栏『函数』框里声明的名字"),
    )
    gk_outputs = (S.pin(FLOW, "完成"), S.pin(ANY, "结果", None),)

    def function_spec(self):
        """从**所属的节点图**里取这个函数的声明(名字 / 参数 / 返回值)。

        侧栏『函数』框在树(`id_data`)上,所以这里要顺着节点往上找树 ——
        离线测试的桩里没有 id_data,那就当作"没声明过"
        (调用点会有一条明确的告警,而不是生成一个猜出来的调用)。
        """
        from .. import properties as props
        tree = getattr(self, "id_data", None)
        name = str(getattr(self, "gk_func_name", "") or "").strip()
        if tree is None or not name:
            return None
        return props.tree_functions(tree).get(name)

    def build_sockets(self):
        """基础引脚 + 按函数声明长参数引脚(用户 2026-10-05)。

        ⚠ 必须先 clear:`build_sockets` 会在"新建"和"改了『函数』"时被叫多次,
        不清的话参数引脚会**一层层叠上去**(和『定时_时间轴』改轨道数同一个坑)。
        """
        self.inputs.clear()
        self.outputs.clear()
        B.build_declared_sockets(self)
        spec = self.function_spec()
        if not spec:
            return
        for param_name, param_type in (spec.get("params") or ()):
            self.inputs.new(S.idname_for_gd_type(param_type), param_name)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_func_name", text="", icon="FILE_SCRIPT")
        pick = row.operator("gk.pick_function", text="", icon="VIEWZOOM")
        pick.node_name = self.name
        pick.func_prop = "gk_func_name"
        spec = self.function_spec()
        if spec is None:
            layout.label(text="先在侧栏『函数』框里声明它", icon="INFO")
            return
        params = spec.get("params") or []
        if params:
            layout.label(text="参数: " + "、".join(
                f"{name}({kind})" for name, kind in params), icon="INFO")
        if spec.get("return"):
            layout.label(text=f"返回: {spec['return']}", icon="INFO")