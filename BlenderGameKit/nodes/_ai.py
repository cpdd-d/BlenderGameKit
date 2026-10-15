"""AI 节点(『游戏AI』树) —— 状态机式 AI:巡逻 / 追击 / 逃跑 / 返回…

第四十一轮(用户:"添加游戏AI逻辑编辑器,这样分类开不会后期混乱")。
AI 逻辑以前只能拿玩法图的节点硬拼(取值_距离 + 逻辑_比较 + 移动_朝向目标
+ 触发器…),一多就糊在某一张图里。现在照动画树的做法单开一张图:

    玩法图(游戏逻辑)                AI 图(游戏AI)
    ┌────────────────┐             ┌──────────────────────────────┐
    │ 每当_准备       │             │ [AI_状态: 巡逻] 行为=巡逻      │
    │  AI_启用 ───────┼── 驱动 ────>│   ├─转场:看见玩家──> [AI_状态: 追击]
    └────────────────┘             │   └─转场:丢失玩家──> [AI_状态: 巡逻]
                                   └──────────────────────────────┘

交互和动画树**一模一样**(状态卡 + 转场卡 + 起始状态 = 谁都没指到的那个),
用户学过一次就不用再学第二次。区别只在"状态里做的事":动画状态播动画,
AI 状态**做行为**。
"""

from ._common import *  # noqa: F401,F403

# AI 树的标识 / 行为枚举都定义在 _common.py(地基先加载,解析函数要用)—— 
# 这里只写节点类,枚举直接用 _common 的那一份(AI_TREE_ID / AI_BEHAVIORS /
# AI_FACINGS 由 `from ._common import *` 带进来)。


@N
class GKNodeAiState(B.GKStatementNode):
    bl_idname = AI_STATE_ID
    bl_label = "AI_状态"
    bl_icon = "TRACKER"
    gk_category = "AI"
    gk_tree_type = AI_TREE_ID
    gk_doc = ("一个 AI 状态:『行为』决定它在这个状态里做什么"
              "(待机 / 巡逻 / 追击 / 逃跑 / 跟随 / 返回 / 去查看 / 攻击)。\n"
              "左边『进入』接上一个『AI_转场』的『转到』;"
              "右边『转到』接一个『AI_转场』写“什么条件下切走”。\n"
              "『进入』没被任何『转到』指到的状态 = **起始状态**(一进游戏就跑它)。\n"
              "── 在这个状态里做点事(进入时 / 离开时)──\n"
              "右边还有两条**执行流**出口:『进入时』= 进这个状态后要做的事"
              "(放音效、改标志),『离开时』= 切走前要做的事。"
              "接『变量_设置』『音效_播放』『信号_发出』这些节点。\n"
              "⚠ 『攻击』状态的『进入时』是**每打一下跑一次**(见下)。\n"
              "── 攻击 ──\n"
              "行为选『攻击』:走进『攻击距离』就停下、面向目标,"
              "每『冷却』秒“打一下” —— 打什么由『进入时』出口决定"
              "(插件**不带**血量系统,扣血那套在玩法图里用『变量_增减』拼)。\n"
              "── 作用在谁 ──\n"
              "AI 控制**『AI_启用』节点上选的那个物体**(默认脚本所在物体)。"
              "所以这个物体必须是角色体(3D 角色 / 2D 角色),"
              "行为都是 velocity + move_and_slide 那一套。\n"
              "── 会绕墙吗 ──\n"
              "**会**。追击 / 跟随 / 去查看 / 巡逻 / 返回 / 攻击 / 逃跑这几个"
              "『走路』的行为,只要**场景设置里勾了『寻路』**,就会自动走导航"
              "网格算出来的方向(绕开墙 / 石头 / 台子)—— 不需要你再拼一个"
              "『移动_寻路』。没勾『寻路』时它们退回**直线走**(和以前一样)。\n"
              "── 逃跑往哪逃(第八十轮)──\n"
              "勾了『寻路』时,逃跑不是瞎跑:从自己位置朝**威胁的反方向**投影"
              "一个『逃跑距离』外的点,沿导航网格绕着障碍逃过去"
              "(投影点不可达时退回直线,不会站着不动)。\n"
              "── 去查看 + 环顾(第八十轮)──\n"
              "『去查看』走到噪音点后会**原地左右环顾**(『环顾角度』= 左右"
              "各转多少、『环顾秒数』= 转多久);环顾期间『安静下来』不成立 ——"
              "看完了才允许回岗。\n"
              "⚠ 没勾『寻路』时,『逃跑』仍然是直线后退(和以前一样)。")
    gk_inputs = (
        S.pin(FLOW, "进入"),
    )
    gk_outputs = (
        S.pin(FLOW, "转到"),
        # ── 状态自己的执行流(第七十九轮,照『动画_状态』的做法)────────
        # 用户要的:AI 的状态里也能"做点事" —— 看见玩家放个音效、进攻击
        # 范围扣一滴血、切走前清个标志。以前 AI 状态只有『转到』(那是
        # "去下一个状态"的意思,接别的只会报错)。
        #
        #   『进入时』-> 进入这个状态之后要执行的语句
        #   『离开时』-> 切走之前要执行的语句
        #
        # ⚠ 『攻击』状态的『进入时』是**每打一下跑一次**(不是进状态跑一次):
        #   否则"看见玩家 -> 切进攻击状态"会在它还在赶路时就先打一下。
        S.pin(FLOW, "进入时"),
        S.pin(FLOW, "离开时"),
    )
    gk_prop_defs = (
        ("gk_state_name", "string", "巡逻", "状态名",
         "状态机按名字记“现在在哪个状态” —— 每个状态取一个自己认得出的名字"),
        ("gk_behavior", "enum", "PATROL", "行为",
         "这个状态里做什么", AI_BEHAVIORS),
        # ── 目标:追谁 / 逃谁 / 跟随谁 ────────────────────────────────
        ("gk_goal", "node", "", "目标",
         "追击 / 逃跑 / 跟随的对象(一般选玩家)。留空 = 不生成这些行为"),
        # ── 巡逻点:来回走的两头 ──────────────────────────────────────
        ("gk_patrol_a", "node", "", "巡逻点 A",
         "巡逻的起点 / 返回的岗位(在场景里摆两个空物体当巡逻点)"),
        ("gk_patrol_b", "node", "", "巡逻点 B",
         "巡逻的另一头(不填 = 只在 A 点附近待着)"),
        # ── 第三 / 第四个巡逻点(第七十八轮)──────────────────────────
        # 只填 A / B 时走的是原来那套"翻面布尔"(生成物一字不变);
        # 填了 C 或 D 就改成**往返**:A→B→C→D→C→B→A。
        # 跳着填也行(比如 A、C 两点,中间 B 空着 = 走 A↔C)。
        ("gk_patrol_c", "node", "", "巡逻点 C",
         "第三个巡逻点(不填 = 只走 A↔B)"),
        ("gk_patrol_d", "node", "", "巡逻点 D",
         "第四个巡逻点(最多四个点,走法都是往返)"),
        ("gk_speed", "float", 3.0, "速度",
         "移动速度(3D 米/秒,一般 2~6;2D 项目的单位是像素/秒,要填大得多)"),
        ("gk_stop_distance", "float", 0.6, "停在多远",
         "巡逻 / 返回:离巡逻点多近算“到了”;"
         "追击 / 跟随:离目标这么近就停下;"
         "逃跑:目标进到这个距离以内才开始跑"),
        # ── 攻击(第七十九轮)────────────────────────────────────────
        # 『攻击距离』留 0 = 用上面的『停在多远』(常见写法:贴到停距就打)。
        # 『冷却』是"两次打之间至少隔多久" —— 免得每帧都打一下。
        ("gk_attack_range", "float", 0.0, "攻击距离",
         "『攻击』行为:离目标多近算进了攻击范围;0 = 用『停在多远』"),
        ("gk_cooldown", "float", 1.5, "冷却(秒)",
         "『攻击』行为:两次“打一下”之间至少隔多久"),
        # ── 逃跑(第八十轮)────────────────────────────────────────
        # 勾『寻路』时:背对威胁投影一个这么远的点,沿导航网格逃过去;
        # 没勾寻路时这个值不用(逃跑仍然是直线后退)。
        ("gk_flee_distance", "float", 8.0, "逃跑距离",
         "『逃跑』+ 勾了『寻路』:朝威胁反方向投影多远的点去导航(米,默认 8)"),
        # ── 去查看到点后环顾(第八十轮)──────────────────────────────
        ("gk_look_angle", "float", 70.0, "环顾角度",
         "『去查看』走到点后原地左右环顾,左右各转多少度(默认 70)"),
        ("gk_look_time", "float", 2.0, "环顾秒数",
         "『去查看』环顾多久;期间『安静下来』不成立,看完才回岗(默认 2 秒)"),
        ("gk_facing", "enum", "GOAL", "朝向", "走路时头朝哪", AI_FACINGS),
        # 走哪层(第八十五轮:多层导航)—— 和『移动_寻路』的『走哪层』
        # 同一个意思:用第几层的导航网格找路(物体『寻路层』分出来的)。
        ("gk_nav_layer", "enum", "1", "走哪层",
         "用第几层的导航网格(和物体『寻路层』对应)", (
             ("1", "第1层", "默认"),
             ("2", "第2层", ""),
             ("3", "第3层", ""),
             ("4", "第4层", ""),
         )),
        # 入口数量:Blender 一个输入引脚只允许一条连线 ——
        # "巡逻"被好几条转场指着时要多开几个入口(和『动画_状态』同一个做法)
        ("gk_in_count", "int", 2, "入口数量",
         "可以接几条转场(Blender 一个输入引脚只允许一条线)", None,
         {"min": 1, "max": 8}),
    )

    def build_sockets(self):
        """按『入口数量』生成进入引脚:进入 / 进入 2 / 进入 3 …。

        第一个仍然叫『进入』—— 老文件里已经接好的线不会断
        (和『动画_状态』同一个做法)。
        """
        count = max(1, min(8, int(getattr(self, "gk_in_count", 2) or 2)))
        self.inputs.clear()
        self.inputs.new(S.FLOW[0], "进入")
        for index in range(1, count):
            self.inputs.new(S.FLOW[0], f"进入 {index + 1}")
        self.outputs.clear()
        for spec in self.gk_outputs:
            socket = self.outputs.new(spec["kind"][0], spec["name"])

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_state_name", text="", icon="SORTALPHA")
        layout.prop(self, "gk_behavior", text="")
        behavior = str(getattr(self, "gk_behavior", "PATROL"))

        # 只画这个行为用得到的参数 —— 七种行为的参数不完全一样,
        # 全画出来每张卡都是七八行,图一多就糊(用户抱怨过的那种)。
        if behavior in ("CHASE", "FOLLOW", "FLEE", "ATTACK"):
            row = layout.row(align=True)
            row.label(text="目标")
            B.draw_target_picker(self, row, "gk_goal")
        if behavior in ("PATROL", "RETURN"):
            row = layout.row(align=True)
            row.label(text="巡逻点 A")
            B.draw_target_picker(self, row, "gk_patrol_a")
            if behavior == "PATROL":
                # A / B / C / D 最多四个点(第七十八轮)——
                # 填了 C 或 D 就从"A↔B 翻面"换成"往返走完全程"
                for prop, label in (("gk_patrol_b", "巡逻点 B"),
                                    ("gk_patrol_c", "巡逻点 C"),
                                    ("gk_patrol_d", "巡逻点 D")):
                    row = layout.row(align=True)
                    row.label(text=label)
                    B.draw_target_picker(self, row, prop)
        if behavior != "STAND":
            row = layout.row(align=True)
            row.prop(self, "gk_speed", text="速度")
            row.prop(self, "gk_stop_distance", text="停距")
            layout.prop(self, "gk_facing", text="")
            # 走哪层(第八十五轮:多层导航)—— 场景把静态几何分成几层网格
            # 时才需要改;默认第 1 层(老图一个字都不变)。
            layout.prop(self, "gk_nav_layer", text="走哪层")
        if behavior == "ATTACK":
            row = layout.row(align=True)
            row.prop(self, "gk_attack_range", text="攻击距离")
            row.prop(self, "gk_cooldown", text="冷却")
        if behavior == "FLEE":
            layout.prop(self, "gk_flee_distance", text="逃跑距离")
        if behavior == "INVESTIGATE":
            row = layout.row(align=True)
            row.prop(self, "gk_look_angle", text="环顾角度")
            row.prop(self, "gk_look_time", text="秒数")

        row = layout.row(align=True)
        row.prop(self, "gk_in_count", text="入口")
        full = all(bool(getattr(socket, "is_linked", False))
                   for socket in (getattr(self, "inputs", ()) or ()))
        if full and int(getattr(self, "gk_in_count", 2) or 2) < 8:
            row.label(text="已满,调大再接", icon="ERROR")
        layout.label(text="转到 -> 接『AI_转场』的『上游』", icon="FORWARD")
        if behavior == "ATTACK":
            layout.label(text="进入时 -> 每打一下跑一次(扣血 / 音效)",
                         icon="PLAY")
        else:
            layout.label(text="进入时 / 离开时 -> 接要执行的语句", icon="PLAY")


@N
class GKNodeAiTransition(B.GKStatementNode):
    bl_idname = AI_TRANSITION_ID
    bl_label = "AI_转场"
    bl_icon = "TRACKING_FORWARDS_SINGLE"
    gk_category = "AI"
    gk_tree_type = AI_TREE_ID
    gk_doc = ("一条 AI 的换状态规则:『条件』为真时,"
              "从左边连过来的状态切到右边连着的状态。\n"
              "── 三件事都要有 ──\n"
              "『上游』接上一个状态的『转到』(忘了接就用下面的『从』下拉选);\n"
              "『条件』接判断节点的输出,或者把『条件来源』改成"
              "『看见目标』/『丢失目标』直接填个视野距离;"
              "『转到』接下一个状态的『进入』(也能用『到』下拉选)。\n"
              "── 最常用的两条 ──\n"
              "巡逻 -> 追击:『看见目标』;追击 -> 巡逻:『丢失目标』")
    gk_inputs = (
        S.pin(FLOW, "上游"),
        S.pin(BOOL, "条件", False),
    )
    gk_outputs = (
        S.pin(FLOW, "转到"),
    )
    gk_prop_defs = (
        # ── 条件来源(和『动画_转场』同一套交互)──────────────────────
        # 蓝图里最常见的 AI 条件就是"看得见 / 看不见目标"。
        # 为了这个去拉『AI_看得见』再连线,状态一多整张图全是线。
        # 所以这两个内置在节点上 —— 填个距离就能用。
        ("gk_cond_source", "enum", "PIN", "条件来源",
         "『自己写』= 用左边的条件引脚;其它选项都在节点上直接填",
         (("PIN", "自己写(接引脚)", "想多复杂都行:视野角度、血量、组合条件"),
          ("SEE", "看见目标", "目标进入『视野距离』就算看见"),
          ("LOST", "丢失目标", "目标离开『视野距离』就算跟丢"),
          ("HEARD", "听见动静", "最近『记忆秒数』内听到过噪音(配『声音_发出噪音』)"),
          ("CALM", "安静下来", "『记忆秒数』内**没有**新噪音 —— 用来"
                              "『去查看』完了回岗位"))),
        ("gk_goal", "node", "", "目标",
         "『看见目标 / 丢失目标』看的是谁(一般选玩家)"),
        ("gk_sight", "float", 12.0, "视野距离",
         "目标和自己的距离小于它 = 看见(米 / 像素)"),
        # ── 听觉参数(第四十一轮:条件来源 = 听见动静 / 安静下来)────────
        ("gk_ear", "float", 0.0, "听觉距离",
         "耳朵最远能听多远;0 = 不限制(只看噪音自己的『响度』)"),
        ("gk_memory", "float", 3.0, "记忆秒数",
         "听到之后记多久 —— 也就是『去查看』能持续多久"),
        # ── 只听哪一种噪音(第八十轮)──────────────────────────────
        # 发声卡可以给自己贴个『类型』(脚步 / 玻璃 / 说话 …)。
        # 这里留空 = 什么类型都听;填了就只被同类型的噪音惊动 ——
        # 这样"玻璃碎了去查窗户、脚步声不影响"这种分工才画得出来。
        ("gk_hear_type", "string", "", "只听类型",
         "只被这个类型的噪音惊动(和『声音_发出噪音』的『类型』一致);"
         "留空 = 什么都听"),
        # ── 备用下拉:连线忘了也能用(照『动画_转场』)────────────────
        ("gk_from_state", "string", "", "从状态",
         "备用:『上游』没接线时用这个(下拉里是这张图的状态)"),
        ("gk_to_state", "string", "", "到状态",
         "**推荐**:点右边的下拉从这张图的状态里挑“切到哪个”。"
         "这样图里不用连回状态的线 —— Blender 不允许环"),
    )

    def draw_buttons(self, context, layout):
        source = str(getattr(self, "gk_cond_source", "PIN"))
        layout.prop(self, "gk_cond_source", text="")
        # 上游 / 转到:连线优先,下拉兜底(和『动画_转场』同款画法)
        wired_from = bool(link_sources(self, "上游"))
        wired_to = bool(link_targets(self, "转到"))
        if wired_from:
            layout.label(text="上游 <- 上一个状态的『转到』", icon="CHECKMARK")
        else:
            row = layout.row(align=True)
            row.label(text="上游空着 -> 用『从』兜底", icon="LOOP_BACK")
            pick = row.operator("gk.pick_ai_state", text="", icon="VIEWZOOM")
            pick.node_name = self.name
            pick.prop_name = "gk_from_state"
            layout.prop(self, "gk_from_state", text="从")
        if source == "PIN":
            layout.label(text="条件 <- 接判断节点的输出", icon="LINKED")
        elif source in ("HEARD", "CALM"):
            # 听觉:不用选目标(噪音不来自某个特定目标),填耳朵参数就行
            row = layout.row(align=True)
            row.prop(self, "gk_ear", text="听觉距离")
            row.prop(self, "gk_memory", text="记忆秒")
            layout.prop(self, "gk_hear_type", text="只听类型")
            layout.label(text="类型留空 = 什么都听;0 = 不限距离", icon="INFO")
        else:
            row = layout.row(align=True)
            row.label(text="目标")
            B.draw_target_picker(self, row, "gk_goal")
            layout.prop(self, "gk_sight", text="视野距离")
            if not str(getattr(self, "gk_goal", "") or "").strip():
                layout.label(text="! 没选目标,这条不会生效", icon="ERROR")
        # "线被忽略了"必须画在节点上(踩过:用户接了线却发现不生效)
        if source != "PIN" and link_sources(self, "条件"):
            layout.label(text="! 条件引脚接的线被忽略了", icon="ERROR")
            layout.label(text="   以『条件来源』为准")
        if wired_to:
            layout.label(text="转到 -> 下一个状态的『进入』", icon="CHECKMARK")
        else:
            row = layout.row(align=True)
            row.label(text="转到空着 -> 用『到』兜底", icon="FORWARD")
            pick = row.operator("gk.pick_ai_state", text="", icon="VIEWZOOM")
            pick.node_name = self.name
            pick.prop_name = "gk_to_state"
            layout.prop(self, "gk_to_state", text="到")


@N
class GKNodeAiSee(B.GKExpressionNode):
    bl_idname = "GKNodeAiSee"
    bl_label = "AI_看得见"
    bl_icon = "HIDE_OFF"
    gk_category = "AI"
    # 玩法图里也能用(比如"玩家走近 NPC 时提示"),所以两张树都开放
    gk_tree_type = ("GKGameLogicTree", AI_TREE_ID)
    gk_doc = ("自己'看得见'目标吗 —— 距离 + 视野角(前方扇区)一起算,"
              "输出布尔。\n"
              "『视野距离』= 多远以内算看得见;"
              "『视野角』= 前方多少度的扇区(0 = 不看角度、360° 全向;"
              "120 = 正前方 ±60°)。角度用的是'自己'的朝向"
              "(3D 是物体的前方 -Z;2D 是本地 X 轴)。\n"
              "和『AI_转场』的『看见目标』快捷的区别:这个多了**视野角**,"
              "适合'背后看不见、绕到面前才会被发现'的潜行玩法 —— "
              "把它的输出接到转场的『条件』引脚,条件来源保持『自己写』。")
    gk_inputs = (
        S.pin(OBJECT, "目标", None),
        S.pin(FLOAT, "视野距离", 12.0),
        S.pin(FLOAT, "视野角", 120.0),
    )
    gk_input_props = {"视野距离": "gk_distance", "视野角": "gk_angle"}
    gk_outputs = (S.pin(BOOL, "看得见", False),)
    gk_prop_defs = (
        ("gk_goal", "node", "", "目标",
         "看谁(『目标』引脚没接线时用它,一般选玩家)"),
        ("gk_distance", "float", 12.0, "视野距离",
         "多远以内算看得见(米 / 像素)"),
        ("gk_angle", "float", 120.0, "视野角",
         "前方多少度算看得见;0 = 全向(360°,和只看距离一样)"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.label(text="目标")
        B.draw_target_picker(self, row, "gk_goal")
        row = layout.row(align=True)
        row.prop(self, "gk_distance", text="距离")
        row.prop(self, "gk_angle", text="角度")
        if not str(getattr(self, "gk_goal", "") or "").strip():
            socket = getattr(self, "inputs", None)
            socket = socket.get("目标") if socket is not None else None
            if socket is None or not getattr(socket, "is_linked", False):
                layout.label(text="! 没选目标", icon="ERROR")


@N
class GKNodeAiHear(B.GKExpressionNode):
    bl_idname = "GKNodeAiHear"
    bl_label = "AI_听见"
    bl_icon = "SOUND"
    gk_category = "AI"
    # 和『AI_看得见』一样:玩法图里也能用(比如"玩家在附近弄出动静就开门")
    gk_tree_type = ("GKGameLogicTree", AI_TREE_ID)
    gk_doc = ("'听见动静'了吗 —— 输出布尔。\n"
              "── 得先有人出声 ──\n"
              "在玩法图里放**『声音_发出噪音』**(跑步 / 开门 / 摔碎东西时调它),"
              "它会把噪音的**位置和响度**广播给所有'听觉体'。\n"
              "── 两个距离都要满足 ──\n"
              "* 噪音自己的『响度』= 这个声音能传多远(在『声音_发出噪音』上填);\n"
              "* 这里的『听觉距离』= 这个敌人的耳朵最远能听多远"
              "(**0 = 不限制**,只看噪音响度)。\n"
              "『记忆秒数』= 听到之后记多久 —— 声音过去了还'算听见'。\n"
              "── 想让它过去看 ──\n"
              "接『AI_转场』的『条件』引脚即可;或者更快:把转场的"
              "『条件来源』直接选成『听见动静』,再接『AI_状态』的一个"
              "『去查看』行为。")
    gk_inputs = (
        S.pin(FLOAT, "听觉距离", 0.0),
        S.pin(FLOAT, "记忆秒数", 3.0),
    )
    gk_input_props = {"听觉距离": "gk_ear", "记忆秒数": "gk_memory"}
    gk_outputs = (S.pin(BOOL, "听见", False),)
    gk_prop_defs = (
        ("gk_ear", "float", 0.0, "听觉距离",
         "耳朵最远能听多远;0 = 不限制(只看噪音自己的『响度』)"),
        ("gk_memory", "float", 3.0, "记忆秒数",
         "听到之后记多久(秒)—— 用来做'过去看看'的持续时间"),
        ("gk_hear_type", "string", "", "只听类型",
         "只对这个类型的噪音算'听见'(和『声音_发出噪音』的『类型』配对);"
         "留空 = 什么类型都听"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_ear", text="听觉距离")
        row.prop(self, "gk_memory", text="记忆秒")
        layout.prop(self, "gk_hear_type", text="只听类型")
        if float(getattr(self, "gk_ear", 0.0) or 0.0) <= 0.0:
            layout.label(text="0 = 不限距离(看噪音响度)", icon="INFO")


@N
class GKNodeNoise(B.GKStatementNode):
    """『声音_发出噪音』:游戏里"弄出动静",让听得见的 AI 过去查看。

    ── 和『音效_播放』的分工(要说清楚,否则用户会以为重复了)────────
    『音效_播放』= **给玩家听的**(扬声器出声);
    『声音_发出噪音』= **给 AI 听的**(一个玩法事件:谁在哪弄出多大声)。
    两者互不影响 —— 想"跑步又有脚步音效、又能被怪物听见",
    两个节点都放(间隔填成一样就不会错拍)。
    """

    bl_idname = "GKNodeNoise"
    bl_label = "声音_发出噪音"
    bl_icon = "SOUND"
    gk_category = "AI"
    gk_tree_type = "GKGameLogicTree"
    gk_doc = ("弄出一点动静 —— 听得见的 AI 会**记下这个位置**"
              "(用『AI_转场』的『听见动静』+『AI_状态』的『去查看』行为"
              "就能让它过来找)。\n"
              "── 三个参数 ──\n"
              "* 『响度』= 这声音能传多远(米 / 像素)。跑步 8、开门 12、"
              "摔碎东西 20 —— 越吵引来的范围越大;\n"
              "* 『间隔秒』= 最快多久发一次。**脚步声接在『每当_物理帧』"
              "后面必须填**(不填就每帧都喊,敌人永远'刚听到');\n"
              "* 『从哪发出』= 噪音的位置,默认脚本所在物体"
              "(一般就是玩家 / 那个道具)。\n"
              "── 注意 ──\n"
              "它**不出声**(玩家听不到),要出声请另接一个『音效_播放』。")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "响度", 8.0),
    )
    gk_input_props = {"响度": "gk_loudness"}
    gk_outputs = (S.pin(FLOW, "完成"),)
    gk_prop_defs = (
        ("gk_loudness", "float", 8.0, "响度",
         "这声音能传多远(米 / 像素):跑步 8 / 开门 12 / 摔碎 20"),
        ("gk_interval", "float", 0.5, "间隔秒",
         "最快多久发一次;接在『每当_物理帧』后面时**必须填**"
         "(0 = 每帧都发)"),
        ("gk_target", "node", TARGET_SELF, "从哪发出",
         "噪音的位置(默认脚本所在物体)"),
        ("gk_noise_type", "string", "", "类型",
         "给噪音贴的标签(脚步 / 玻璃 / 说话 …)。听方在『AI_听见』或"
         "『AI_转场』的『只听类型』里填一样的词才会被惊动;留空 = 普通噪音,"
         "谁都能听见"),
        ("gk_debug_print", "bool", False, "打印",
         "每次发声往输出面板打一行(含位置和通知了几个听觉体),调试听觉时用"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_loudness", text="响度")
        row.prop(self, "gk_interval", text="间隔秒")
        layout.prop(self, "gk_noise_type", text="类型")
        row = layout.row(align=True)
        row.label(text="从哪发出")
        B.draw_target_picker(self, row, "gk_target")
        row.prop(self, "gk_debug_print", text="", icon="CONSOLE")
        if float(getattr(self, "gk_interval", 0.5) or 0.0) <= 0.0:
            layout.label(text="间隔 0 = 每帧都发(接物理帧会刷爆)", icon="ERROR")


@N
class GKNodeSurfaceLoudness(B.GKExpressionNode):
    """『声音_脚下响度』(第八十轮 D-12②):脚下地面的**响度倍率**。

    潜行游戏的老规矩:地毯上跑步和铁皮上跑步,传出去的声音不该一样响。
    艺术家在 Blender 里给地面物体的『游戏物理』面板填『地面响度倍率』,
    导出时写进碰撞体的 metadata;这个节点运行时从角色脚下往下射一根短射线,
    读命中碰撞体上那个倍率,拿它去乘『声音_发出噪音』的『响度』引脚
    (中间接一个『数学_运算』MUL)。射不到地面时退回 1.0(原样响)。
    """

    bl_idname = "GKNodeSurfaceLoudness"
    bl_label = "声音_脚下响度"
    bl_icon = "NORMALS_FACE"
    gk_category = "AI"
    gk_tree_type = "GKGameLogicTree"
    gk_doc = ("脚下踩的地面有多“响” —— 输出**响度倍率**(float)。\n"
              "── 怎么用 ──\n"
              "把它接到一个『数学_运算』(乘)上:**噪音基础响度 × 脚下响度**,"
              "结果再接到『声音_发出噪音』的『响度』引脚。"
              "这样在地毯上(0.3)跑步几乎听不到,踩铁皮(2.0)半条街都惊动。\n"
              "── 倍率在哪配 ──\n"
              "选中地面物体,右侧『游戏物理』面板里填『地面响度倍率』"
              "(默认 1.0 = 普通地面,不写任何额外数据)。\n"
              "节点从『从谁脚下』物体的位置往下射一根短射线"
              "(『检测距离』默认 3 米),读命中碰撞体上的倍率;"
              "脚下没地面时返回 1.0,不会把脚步声弄没。")
    gk_inputs = (
        S.pin(FLOAT, "检测距离", 3.0),
    )
    gk_input_props = {"检测距离": "gk_distance"}
    gk_outputs = (S.pin(FLOAT, "响度倍率", 1.0),)
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "从谁脚下",
         "从谁的脚下往下查(默认脚本所在物体,一般就是玩家)"),
        ("gk_distance", "float", 3.0, "检测距离",
         "往下射多远(米)。有台阶 / 跳跃离地时调大一点"),
    )
    # 射线每次求值都要重新射,不能缓存表达式(同『射线_有东西吗』)。
    gk_no_cache = True

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.label(text="从谁脚下")
        B.draw_target_picker(self, row, "gk_target")
        layout.prop(self, "gk_distance", text="检测距离")


# ==========================================================================
# 第六十九轮:AI 图补三件 —— 显式入口 / 对外布尔输出 / 条件组合
#
# 用户原话:"游戏AI 需补充『开始』『输出』『逻辑判断』节点,对外输出布尔变量
# 以实现玩法图分支判断"。动工前确认:三件都在 AI 图里;『AI_输出』先做
# "在不在某个状态"的布尔(条件布尔等真需要再加)。
# ==========================================================================
@N
class GKNodeAiStart(B.GKStatementNode):
    """AI 图的**显式入口**:一根『开始』线指向起始状态(第六十九轮)。"""

    bl_idname = "GKNodeAiStart"
    bl_label = "AI_开始"
    bl_icon = "PLAY"
    gk_category = "AI"
    gk_tree_type = AI_TREE_ID
    gk_doc = ("这张 AI 图**从哪个状态开始**。\n"
              "不摆它也能用 —— 那时按老规矩『没有任何转场指向的那个状态』"
              "当起点。可**循环图**(每个状态都有人指)猜不出来,只能随便挑一个,"
              "\"我想从追击开始\"就没法表达。\n"
              "摆一个它,把『开始』连到第一个状态的『进入』上,起点就定死了。\n"
              "⚠ 一张图只放一个。")
    gk_inputs = ()
    gk_outputs = (S.pin(FLOW, "开始"),)

    def draw_buttons(self, context, layout):
        layout.label(text="把『开始』连到第一个状态", icon="INFO")


@N
class GKNodeAiOutput(B.GKStatementNode):
    """把一个**状态**暴露成对外**布尔变量**(第六十九轮)。"""

    bl_idname = "GKNodeAiOutput"
    bl_label = "AI_输出"
    bl_icon = "EXPORT"
    gk_category = "AI"
    gk_tree_type = AI_TREE_ID
    gk_doc = ("把\"AI 现在在不在某个状态\"做成一个**布尔变量**,给玩法图分支用。\n"
              "例:填『状态 = 追击』『变量名 = is_chasing』,玩法图里就能用"
              "『变量_读取』读 is_chasing,接『如果_否则』决定放不放紧张音乐。\n"
              "以前只能读**状态字符串**再自己比较(还得自己拼字符串),这个"
              "把它变成现成的真假。\n"
              "⚠ 变量名用英文 / 数字 / 下划线(中文名会被压成下划线,可能和别的"
              "变量撞车)。图里每帧自动跟着状态更新,不用手动设。")
    gk_inputs = ()
    gk_outputs = ()
    gk_prop_defs = (
        ("gk_state_name", "string", "", "状态",
         "要输出的那个状态(填它卡片上的『状态名』)"),
        ("gk_var", "string", "", "变量名",
         "玩法图用『变量_读取』照这个名字读(如 is_chasing)"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_state_name", text="状态")
        row = layout.row(align=True)
        row.prop(self, "gk_var", text="变量名")
        tree = getattr(getattr(context, "space_data", None), "edit_tree", None)
        names = [ai_state_name(node) for node in ai_state_nodes(tree)] \
            if tree is not None else []
        names = [name for name in names if name]
        if names:
            layout.label(text="图里的状态:" + " / ".join(names[:4]), icon="INFO")
        if not str(getattr(self, "gk_var", "") or "").strip():
            layout.label(text="! 还没填变量名,玩法图读不到", icon="ERROR")


@N
class GKNodeAiLogic(B.GKExpressionNode):
    """在 AI 图里把条件拼成布尔(第六十九轮)。

    和玩法图的『逻辑_布尔运算』同一套,只是**它能在 AI 图里用** ——
    转场只能挂一个条件,「看见 **且** 在射程内」得先把条件拼起来。
    """

    bl_idname = "GKNodeAiLogic"
    bl_label = "AI_逻辑判断"
    bl_icon = "CHECKMARK"
    gk_category = "AI"
    gk_tree_type = AI_TREE_ID
    gk_doc = ("与 / 或 / 非。把几个条件拼成一个布尔,接到『AI_转场』的"
              "『条件』引脚上(条件来源保持『自己写』)。\n"
              "例:A 接『AI_看得见』、B 接『AI_听见』,运算选『或』——"
              "\"看见**或**听见\"就切追击。")
    gk_inputs = (S.pin(BOOL, "A", False), S.pin(BOOL, "B", False),)
    gk_prop_defs = (
        ("gk_op", "enum", "AND", "运算", "", (
            ("AND", "与 and(都成立)", ""),
            ("OR", "或 or(任一成立)", ""),
            ("NOT", "非 not(A)", "只用 A 一个输入"),
        )),
    )
    gk_outputs = (S.pin(BOOL, "结果", False),)


@N
class GKNodeAiEnable(B.GKStatementNode):
    bl_idname = "GKNodeAiEnable"
    bl_label = "AI_启用"
    bl_icon = "AUTOMERGE_ON"
    gk_category = "AI"
    gk_doc = ("驱动一张 **AI 图**(在『游戏AI』面板里建)。"
              "巡逻 / 追击 / 逃跑这些规则全写在 AI 图里,这里只负责启动它 —— "
              "和『动画_状态机』『界面_套用设计』同一套路,玩法图不会糊。\n"
              "『树名』留空 = 用文件里第一张 AI 图。\n"
              "『作用在谁』= AI 控制哪个物体(默认脚本所在物体)。"
              "**它必须是角色体**(3D 角色 / 2D 角色)—— 行为都是 "
              "velocity + move_and_slide 那一套。\n"
              "接在『每当_准备』后面即可;一启动就进入 AI 图的**起始状态**"
              "(没有任何转场指到的那个状态)。")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "树名", ""),
    )
    gk_input_props = {"树名": "gk_tree_name"}
    gk_outputs = (S.pin(FLOW, "完成"),)
    gk_prop_defs = (
        ("gk_tree_name", "string", "", "树名",
         "留空 = 用文件里第一张 AI 图(只有一张时不用管)"),
        ("gk_target", "node", TARGET_SELF, "作用在谁",
         "AI 控制哪个物体(默认脚本所在物体,一般就是敌人自己)"),
        ("gk_state_var", "string", "ai_state", "状态变量名",
         "当前状态存在哪个成员变量里(同一张图多处启用时各取一个名字)"),
        ("gk_debug_print", "bool", False, "打印状态变化",
         "勾上后每次切换状态都往 Godot 输出面板打一行,"
         "用来确认状态机到底有没有在跑"),
        # ── 被上帝机暂停(第一百三十一轮:用户第 13 轮)──────────────────
        # 用户原话:"第二关,在上帝机启用中,游戏AI还可以攻击我,应该设置
        # 上帝机启用时全局暂停(冻结全局时间流速),按某个键如enter解除,
        # 且此时靠近游戏AI按F键确实可以接管,但AI的移动逻辑等 和我的在
        # 抢夺控制权(可以在游戏AI的侧边栏添加是否被上帝机暂停,默认启用,
        # 不启用的话这可以在上帝视角观察世界运行)"。
        #
        # ── 为什么默认 **True** ────────────────────────────────────────
        # 用户明确说了"默认启用"。上帝机是"停下来看看这一关做得对不对"的
        # 工具 —— 飞着看的时候敌人还在追你、还在打你,就没法安静地检查。
        # ⚠ 正因为默认是 True,**读属性时必须兜底成 True**:
        #   老的 .blend 里这个节点是这轮之前建的,根本没有这个属性,
        #   用 `getattr(node, "gk_pause_with_god", False)` 会让老工程悄悄
        #   退回"上帝机里 AI 照跑"(用户报的就是这个)。见 codegen/ai.py。
        #
        # ── 什么时候该关掉它 ──────────────────────────────────────────
        # 用户自己给了用途:"不启用的话这可以在上帝视角观察世界运行" ——
        # 想从天上看着巡逻路线跑得对不对 / 敌人会不会撞墙,就取消勾选。
        ("gk_pause_with_god", "bool", True, "被上帝机暂停",
         "上帝机开着时这条 AI 停摆(默认);取消勾选 = 上帝视角里继续"
         "观察它自己跑"),
    )

    def draw_buttons(self, context, layout):
        trees = ai_tree_names()
        row = layout.row(align=True)
        if len(trees) <= 1:
            row.label(text=f"AI 图:{trees[0] if trees else '(还没有)'}",
                      icon="NODETREE")
        else:
            row.prop(self, "gk_tree_name", text="", icon="NODETREE")
        picker = row.operator("gk.pick_ai_tree", text="", icon="VIEWZOOM")
        picker.node_name = self.name
        row = layout.row(align=True)
        row.label(text="作用在谁")
        B.draw_target_picker(self, row, "gk_target")
        layout.prop(self, "gk_debug_print", text="打印状态变化")
        layout.prop(self, "gk_pause_with_god", text="被上帝机暂停")
        self._draw_tree_graph(layout)

    def _draw_tree_graph(self, layout):
        """把这张 AI 图的**状态表**摊在节点上(照『动画_状态机』)。

        用户看不见自己图里的问题就会反复导出试 —— 图和问题直接画在
        节点上,一眼就能对上。
        """
        tree = find_ai_tree(getattr(self, "gk_tree_name", ""))
        if tree is None:
            layout.label(text="还没有 AI 图 —— 点『打开/新建 AI 图』",
                         icon="ERROR")
            return
        graph = ai_tree_graph(tree)
        states = [s for s in graph["states"] if ai_state_name(s)]
        column = layout.column(align=True)
        entry = graph["entry"]
        column.label(text=f"{len(states)} 个状态 / {len(graph['edges'])} 条转场",
                     icon="TRACKER")
        for state in states[:8]:
            name = ai_state_name(state)
            behavior = ai_behavior_label(state)
            here = "起始 " if state is entry else "     "
            icon = "PLAY" if state is entry else "FORWARD"
            column.label(text=f"{here}{name} · {behavior}", icon=icon)
        if len(states) > 8:
            column.label(text=f"     …还有 {len(states) - 8} 个", icon="DOT")
        for problem in graph["problems"][:3]:
            column.label(text=short_problem(problem), icon="ERROR")
        if len(graph["problems"]) > 3:
            column.label(text=f"…还有 {len(graph['problems']) - 3} 条问题"
                              f"(见侧栏『游戏AI』)", icon="INFO")