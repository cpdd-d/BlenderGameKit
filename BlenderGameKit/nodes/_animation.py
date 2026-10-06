"""动画与动画系统节点 —— 从 nodes/nodes.py 按领域拆出的一块(GPT 审查:5280 行的单文件)。

本模块里的节点类通过 `@N` 登记进 `_common.CLASSES`。门面 `nodes/nodes.py`
按拆分前的**原始顺序**导入各领域模块,保证 `ALL_CLASSES` 的顺序不变。
"""

from ._common import *  # noqa: F401,F403



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
              "只对连了线的出口生成代码,没连就没有开销。\n"
              "── 重复触发时怎么办(用户 2026-10-05)──────────────────\n"
              "触发区、按键会在窗口里**连着触发几十次**,默认每次都从头播 —— "
              "看起来就是\"鬼畜重播\"。把下面的『重复触发』改成"
              "『先放完再说』:正在播同名动画就**跳过**,这一轮放完了"
              "下一次才接上 —— 这就是\"先放完再重复\"。\n"
              "想\"隔固定时间才放一次\"(而不是按动画长度),在前面再挂一个"
              "『流程_冷却』")
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
        # ── 照 UPBGE『Play Animation』补齐的参数 ────────────────────────
        # 用户要的那几项:起始/结束帧、Blend-In、混合模式、层。
        # 全部以 0 / -1 为"不生效",所以**老图的行为一点不变**
        # (起始=0、结束=0、Blend-In=-1、层=0、模式=替换)。
        #
        # 单位说明:Godot 的 seek/play_section 收的是**秒**,
        # 而 Blender 里用户看到的是**帧**,所以导出时按场景帧率换算。
        ("gk_start_frame", "float", 0.0, "起始帧",
         "从这个动作的第几帧开始播(0 = 从头)。导出的代码会换算成秒"),
        ("gk_end_frame", "float", 0.0, "结束帧",
         "播到第几帧就停(0 = 播到动作末尾)。相当于在播放器里裁一段"),
        ("gk_blend_in", "float", -1.0, "Blend-In(秒)",
         "和上一个动画之间的过渡时间(秒)。-1 或 0 = 立即切换"),
        ("gk_layer", "int", 0, "层",
         "0 = 主播放器(默认)。≥1 会**另起一个动画播放器**专放这层 —— "
         "上下半身分离动画用(上半身的动作挂 1 层,下半身留在 0 层)"),
        ("gk_blend_mode", "enum", "REPLACE", "重复触发时",
         "连着触发时怎么办:『从头重播』= 每次都从头播;"
         "『先放完再说』= 正在播同名动画就跳过(不打断当前这一轮)= "
         "'先放完再重复'。想隔固定时间放一次再挂『流程_冷却』",
         (("REPLACE", "从头重播", "每次触发都从头播(默认,老行为)"),
          ("CONTINUE", "先放完再说(在播就跳过)",
           "已经在播同名动画就跳过 —— 重复触发不打断当前这一轮"))),
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
        # ── 照 UPBGE 补的那几项(见 gk_prop_defs)──────────────────────
        # 分两行画,免得节点变太宽。
        row = layout.row(align=True)
        row.prop(self, "gk_start_frame", text="起始帧")
        row.prop(self, "gk_end_frame", text="结束帧")
        row = layout.row(align=True)
        row.prop(self, "gk_blend_in", text="Blend-In")
        row.prop(self, "gk_layer", text="层")
        # ── 重复触发怎么办:一键开关(用户 2026-10-05 第二十七轮)──────
        # 以前这个下拉叫『混合模式』、选项叫『续播(同名不打断)』,用户
        # 完全不知道它就是"先放完再重复"。现在直接写清楚。
        layout.prop(self, "gk_blend_mode", text="重复触发")

    def draw_buttons_ext(self, context, layout):
        layout.label(text="动画播放器:留空 = 自动查找(推荐)")
        B.draw_target_picker(self, layout, "gk_player")


@N
class GKNodeAnimParam(B.GKStatementNode):
    bl_idname = "GKNodeAnimParam"
    bl_label = "动画_设混合参数"
    bl_icon = "DRIVER"
    gk_category = "动画"
    gk_doc = ("给整个模型的形态键统一设一个值。"
              "做口型/表情时有用:播放说话动画的同时,"
              "用这个节点把嘴形参数推到 0~1")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "值", 1.0),)
    gk_input_props = {"值": "gk_value"}
    gk_outputs = (S.pin(FLOW, "完成"),)

    gk_prop_defs = (
        ("gk_value", "float", 1.0, "值", "0~1"),
        ("gk_shape_index", "int", 0, "形态键序号", "第几个形态键(从 0 数)"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_value", text="")
        row.prop(self, "gk_shape_index", text="序号")


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
class GKNodeAnimLocomotion(B.GKExpressionNode):
    bl_idname = "GKNodeAnimLocomotion"
    bl_label = "动画判断_移动状态"
    bl_icon = "ORIENTATION_GLOBAL"
    gk_category = "动画系统"
    gk_tree_type = "GKAnimationTree"
    gk_doc = (
        "把动画切换最常用的判断一次给你 —— 不用为每个条件单独拉节点。"
        "输出全是布尔,直接接到转场节点的『条件』引脚:"
        "『在地面』『在移动』『地面且移动』『已离地』『在上升』"
        "『在下落』『速度快』『速度慢』。"
        "『速度阈值』用来区分走和跑")
    # ── 两个阈值必须是**引脚**(第三十九轮修)───────────────────────────
    # 以前只在卡片上画了两个数字框,而 codegen 是按**引脚名**去读值的
    # (`_tunable_number(ctx, node, "速度阈值", …)`)—— 节点上根本没有这
    # 两个引脚,于是读不到、永远用硬编码的 0.1 / 3.0:
    # 用户在卡片上把阈值从 0.1 改到 5,导出结果**一点变化都没有**
    # (这就是"一部分节点卡片无作用"的第二个现场)。
    # 补上引脚 + `gk_input_props` 之后:卡片上改 = 生成里改;
    # 想由图算(比如按等级变)还能直接接线。
    gk_inputs = (
        S.pin(FLOAT, "速度阈值", 0.1),
        S.pin(FLOAT, "跑的速度阈值", 3.0),
    )
    gk_input_props = {"速度阈值": "gk_speed_threshold",
                      "跑的速度阈值": "gk_fast_threshold"}
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "角色", "要判断的节点"),
        ("gk_speed_threshold", "float", 0.1, "速度阈值",
         "『在移动』和『速度快』用的门槛"),
        ("gk_fast_threshold", "float", 3.0, "跑的速度阈值",
         "『速度快』用的门槛(比上面那个高)"),
    )
    gk_outputs = (
        S.pin(BOOL, "在地面", False),
        S.pin(BOOL, "在移动", False),
        S.pin(BOOL, "地面且移动", False),
        S.pin(BOOL, "已离地", False),
        S.pin(BOOL, "在上升", False),
        S.pin(BOOL, "在下落", False),
        S.pin(BOOL, "速度快", False),
        S.pin(BOOL, "速度慢", False),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_speed_threshold", text="移动")
        row.prop(self, "gk_fast_threshold", text="跑")


def anim_state_items(self, context):
    """『从 / 到』下拉的候选:**这张动画树里的所有状态名**。

    用户 2026-10-03:状态机天然是环,而 Blender 不允许连成环
    ("链接形式的循环不受支持")。所以给他一条不用连线的路 ——
    在转场上直接选"从哪个状态到哪个状态"。
    """
    items = [("", "不指定(看连线)", "想用连线就留空")]
    tree = getattr(self, "id_data", None)
    seen = set()
    for node in getattr(tree, "nodes", ()) or ():
        if getattr(node, "bl_idname", "") != "GKNodeAnimState":
            continue
        name = anim_state_name(node)
        if name and name not in seen:
            seen.add(name)
            items.append((name, name, f"『{name}』"))
    return items



@N
class GKNodeAnimState(B.GKStatementNode):
    bl_idname = "GKNodeAnimState"
    bl_label = "动画_状态"
    bl_icon = "POSE_HLT"
    gk_category = "动画系统"
    gk_tree_type = "GKAnimationTree"
    # ── 这里**曾经**是 `gk_hidden = True`(踩过的坑)────────────────
    # 上一轮(A 方案之前)这个节点是"旧版卡片",标了隐藏。
    # 这一轮把它**重新做成了 A 方案的状态节点**,却忘了把这一行删掉 ——
    # 于是它继续被菜单过滤掉,用户在"添加节点"里**根本看不到它**
    # (实测:菜单里只有『动画_转场』和『动画判断_移动状态』)。
    #
    # 教训:改造一个已有节点时,要把它身上所有"上一版留下的标记"
    # 一起检查一遍,别只改自己新加的部分。
    gk_doc = ("一个动画状态(照虚幻 5 的动画状态机)。"
              "『动画』选这个状态播什么;"
              "左边『进入』接上一个状态的『转到』;"
              "右边『转到』接一个『动画_转场』。"
              "『进入』没被任何『转到』指到的状态 = 起始状态")
    # ── A 方案:照虚幻 5 的动画状态机(用户确认)──────────────────
    # UE5 里状态之间**只能有一条出线**(出线接一个 Transition,
    # Transition 再接下一个状态的入口),所以不需要额外给"完成"引脚 ——
    # 『转到』一条就够了。
    #
    #   [动画_状态 idle] ● 进入   ● 转到 ──> [动画_转场] ● 转到 ──> [动画_状态 run] ● 进入
    #
    # 一条链就是一张状态机图,一眼看出从哪来、到哪去。
    gk_inputs = (
        S.pin(FLOW, "进入"),
    )
    gk_outputs = (
        S.pin(FLOW, "转到"),
        # ── 状态自己的执行流(照虚幻 5 的 State Entry / State Exit)──────
        # 用户 2026-10-03 想做的事:**"上一个动画播完了 -> 设一个布尔变量"**,
        # 他把状态的『转到』直接接到了『动画_设变量』上 —— 但『转到』是
        # "去下一个状态"的意思,那条线只能报错。
        #
        # 所以在状态上开两个**做事的**出口:
        #   『进入时』-> 进入这个状态之后要执行的语句(设变量、播音效…)
        #   『离开时』-> 切走之前要执行的语句(清标志、收尾…)
        # 有了它们,『动画_设变量』在动画树里就是**活**的了。
        S.pin(FLOW, "进入时"),
        S.pin(FLOW, "离开时"),
    )
    gk_prop_defs = (
        # 状态名 = 动画名,用**动作数据块**选,不用手打。
        # 手打 "run" 打成 "rnu" 不会有任何提示,要到 Godot 运行时才发现。
        ("gk_anim_action", "action", None, "动画",
         "这个状态播哪个动作(原生选择器,能搜索)"),
        ("gk_state_name", "string", "idle", "状态名(手填兜底)",
         "没选『动画』时才用它"),
        # ── 播放速度(第三十九轮,用户 2026-10-06)────────────────────────
        # 用户反馈原话:"游戏动画里的无动画速率"。以前只有『动画_播放』
        # 能填速度,动画树里的**状态**没有 —— 走 / 跑 / 受击想快慢不一
        # 只能靠美术把动作做快,运行时调不了。
        # 默认 1.0 = 原速:老图(全 1.0)生成结果一字不变。
        ("gk_speed", "float", 1.0, "播放速度",
         "1.0 为原速;0.5 = 慢放一半,2.0 = 快一倍。"
         "状态切换后按这个速度播(『同时播』的那几段也跟着)"),
        # ── 为什么要好几个『进入』(Blender 的硬限制)────────────────────
        # **一个输入引脚只允许一条连线**(踩过的坑,README 第 9 条)。
        # 于是"待机"被 走/跑/跳/受击/攻击 五条转场指着时,只有**最后接的
        # 那一条**留在引脚上,前面几条的线会被悄悄顶掉 —— 用户在画布上
        # 会看到自己的箭头跳走,导出时则报"不知道切到哪个状态"。
        #
        # 解法:入口引脚数量可调(和『序列』的出口一个做法)。多接几个
        # 转场就把它调大,每个引脚接一条。第一个仍然叫『进入』——
        # 老文件里已经接好的线不会断。
        ("gk_in_count", "int", 2, "入口数量",
         "可以接几条转场(Blender 一个输入引脚只允许一条线)", None,
         {"min": 1, "max": 8}),
        # ── 一个状态**同时**播好几段动画(用户 2026-10-03 要的)────────
        # 他的 MMD 模型就是这样:**骨骼动作**和**形态键动作**是分开的两段,
        # 想看完整动作必须同时播。Godot 的 AnimationPlayer 一次只能播一段
        # (`play()` 会顶掉上一段),所以这里按"层"复制出额外的播放器
        # (见 codegen.need_layer_player),两段各播各的,互不干扰。
        #
        # ⚠ 第三十九轮补声明(用户原话:"同时播放,且一部分节点卡片无作用")
        # ─────────────────────────────────────────────────────────────
        # 这两个属性以前**只在 draw_buttons 里被引用,从来没声明过**:
        #   * `self.gk_anim_action_2` 一读就 AttributeError —— 而卡片画到
        #     "同时播" 那一行时**必然**会读(getattr 兜不住的短路写在
        #     `if extras or self.gk_anim_action_2 is not None`),于是**每个
        #     状态节点的卡片在 Blender 里都是画一半就报错**,看起来就是
        #     "卡片没作用";
        #   * 就算不报错,`row.prop(self, "gk_anim_action_2")` 对着一个
        #     没注册的属性也画不出选择器 —— "同时播" 这个功能等于不存在。
        # 现在补成真的 Blender 属性(原生动作选择器),两层意思都修好了。
        # (tests/test_codegen.py 的 TestCardPropertiesDeclared 会**全局**
        #  扫这类"卡片引用了没声明",以后不会再漏)
        ("gk_anim_action_2", "action", None, "同时播 2",
         "这个状态**同时**播的第二段动画(Godot 一次只能播一段,多出来的"
         "按层复制播放器;MMD 那种骨骼动作 + 形态键动作分两段的模型就用它)"),
        ("gk_anim_action_3", "action", None, "同时播 3",
         "同时播的第三段(一般用不到)"),
    )

    def build_sockets(self):
        """按『入口数量』生成进入引脚:进入 / 进入 2 / 进入 3 …"""
        count = max(1, min(8, int(getattr(self, "gk_in_count", 2) or 2)))
        self.inputs.clear()
        self.inputs.new(S.FLOW[0], "进入")
        for index in range(1, count):
            self.inputs.new(S.FLOW[0], f"进入 {index + 1}")
        self.outputs.clear()
        for spec in self.gk_outputs:
            socket = self.outputs.new(spec["kind"][0], spec["name"])
            if spec["default"] is not None:
                try:
                    socket.default_value = spec["default"]
                except Exception:
                    pass

    def resolved_state_name(self):
        """这个状态叫什么 —— 优先用选中的**动作**,否则用手填的。

        两种来源都支持是为了两件事:
          * 选了动作 -> 名字一定对(动画不存在根本选不出来)
          * 没选     -> 还能手填(比如动画是运行时才加载的)
        """
        action = getattr(self, "gk_anim_action", None)
        if action is not None:
            name = str(getattr(action, "name", "") or "")
            if name:
                return name
        return str(getattr(self, "gk_state_name", "") or "")

    def extra_animation_names(self):
        """这个状态**同时播**的另外几段动画名(没填的空着)。"""
        names = []
        for prop in ("gk_anim_action_2", "gk_anim_action_3"):
            action = getattr(self, prop, None)
            name = str(getattr(action, "name", "") or "") if action else ""
            names.append(name)
        return [name for name in names if name]

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_anim_action", text="")
        if self.gk_anim_action is not None:
            layout.label(text=f"状态:{self.gk_anim_action.name}",
                         icon="CHECKMARK")
        else:
            layout.prop(self, "gk_state_name", text="", icon="SORTALPHA")
        # 同时播的另外几段:填了才画,免得每个状态都顶着两个空框
        # ⚠ 用 getattr 兜一下:这张卡片以前就因为一个**没声明**的属性
        #   在画到这儿时整块报错(第三十九轮修的),别再踩第二次。
        extras = self.extra_animation_names()
        if extras or getattr(self, "gk_anim_action_2", None) is not None:
            layout.label(text=f"同时播:{len(extras)} 段", icon="DUPLICATE")
        row = layout.row(align=True)
        row.prop(self, "gk_anim_action_2", text="")
        row.prop(self, "gk_anim_action_3", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_in_count", text="入口")
        row.prop(self, "gk_speed", text="速度")
        full = all(
            bool(getattr(socket, "is_linked", False))
            for socket in (getattr(self, "inputs", ()) or ()))
        if full and int(getattr(self, "gk_in_count", 2) or 2) < 8:
            # 每个入口都被占了 —— 下一条线会**顶掉**现有的那一条,
            # 所以这里必须提醒(用户会看到自己的箭头"跳走")
            row.label(text="已满,调大再接", icon="ERROR")
        layout.label(text="转到 -> 接『动画_转场』的『上游』", icon="FORWARD")
        layout.label(text="进入时 / 离开时 -> 接要执行的语句", icon="PLAY")


@N
class GKNodeAnimEntry(B.GKStatementNode):
    bl_idname = "GKNodeAnimEntry"
    bl_label = "动画_开始"
    bl_icon = "PLAY"
    gk_category = "动画系统"
    gk_tree_type = "GKAnimationTree"
    gk_doc = ("**这张动画图从哪儿开始、什么时候开始。**"
              "把它的『开始』接到第一个状态的『进入』—— 那个状态就是起始状态"
              "(不用再靠『谁没被指到』去猜)。"
              "── 开始条件可自定义(用户要求)──"
              "『开始方式』可以选:立刻开始 / 变量为真 / 刚按下某键 / "
              "上一个动画播完 / 自己写(接条件引脚)。"
              "条件不成立时整张图**不动**,成立才开始播第一个状态;"
              "走到『动画_结束』之后,条件再次成立就会重新开始")
    gk_inputs = (
        S.pin(BOOL, "条件", False),
    )
    gk_outputs = (
        S.pin(FLOW, "开始"),
    )
    gk_prop_defs = (
        ("gk_start_mode", "enum", "NOW", "开始方式",
         "『变量 / 输入 / 播完』不用接线;『自己写』才用左边的条件引脚",
         (("NOW", "立刻开始", "游戏一开始(或结束之后马上)就跑这张图"),
          ("PIN", "自己写(接条件引脚)", "想怎么判就怎么判 —— 接判断节点"),
          ("VAR", "变量为真", "侧栏『变量』里的布尔变量为真时开始"),
          ("ACTION_JUST", "刚按下某键", "按下按键的那一刻开始"),
          ("ACTION_PRESSED", "按住某键", "按住期间都算满足"),
          ("FINISH", "上一个动画播完", "某段动画播完之后开始"))),
        ("gk_var_name", "string", "", "变量名", "开始方式 = 『变量为真』时用"),
        ("gk_cond_action", "string", "attack", "输入动作",
         "开始方式 = 『刚按下 / 按住』时用"),
        ("gk_anim_name", "string", "", "动画名",
         "开始方式 = 『上一个动画播完』时用(留空 = 任意动画播完)"),
    )

    def draw_buttons(self, context, layout):
        mode = str(getattr(self, "gk_start_mode", "NOW"))
        layout.prop(self, "gk_start_mode", text="")
        if mode == "VAR":
            layout.prop(self, "gk_var_name", text="", icon="SORTALPHA")
        elif mode in ("ACTION_JUST", "ACTION_PRESSED"):
            layout.prop(self, "gk_cond_action", text="", icon="MOUSE_MOVE")
        elif mode == "FINISH":
            layout.prop(self, "gk_anim_name", text="", icon="TIME")
        elif mode == "PIN":
            layout.label(text="条件 <- 接判断节点的输出", icon="LINKED")
        layout.label(text="开始 -> 接第一个状态的『进入』", icon="FORWARD")


@N
class GKNodeAnimExit(B.GKStatementNode):
    bl_idname = "GKNodeAnimExit"
    bl_label = "动画_结束"
    bl_icon = "QUIT"
    gk_category = "动画系统"
    gk_tree_type = "GKAnimationTree"
    gk_doc = ("**这张动画图到哪儿算结束。**"
              "状态的『转到』可以直接连到它的『进入』—— 走到这里 = 这段动画"
              "演完了,状态机会停下来(不再判转场),并把"
              "『已结束』置为真,玩法图里用『动画_运行状态』就能读到,"
              "接着做下一步逻辑(结算伤害、回到玩法状态、播下一段动画…)")
    gk_inputs = (
        S.pin(FLOW, "进入"),
    )
    gk_outputs = (
        S.pin(FLOW, "结束后"),
    )
    gk_prop_defs = (
        ("gk_stop_player", "bool", False, "结束时停住动画",
         "勾上 = 把动画播放器停下(回到静止姿势);不勾 = 保持最后一帧的姿势"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_stop_player", text="结束时停住动画")
        layout.label(text="进入 <- 接状态的『转到』", icon="LOOP_BACK")
        layout.label(text="结束后 -> 接要执行的语句(可选)", icon="FORWARD")


@N
class GKNodeAnimStatus(B.GKExpressionNode):
    bl_idname = "GKNodeAnimStatus"
    bl_label = "动画_运行状态"
    bl_icon = "INFO"
    gk_category = "动画"
    gk_tree_type = BOTH_TREES
    gk_doc = ("这张动画图现在什么情况 —— 给**玩法图**判断下一步用:"
              "『已结束』(走到结束口了)、『在跑』(正在演)、"
              "『当前状态』(状态名,做分支/调试用)。"
              "例:`如果(动画_运行状态.已结束) -> 扣血 / 回到待机`")
    gk_prop_defs = (
        ("gk_state_var", "string", "anim_state", "状态变量名",
         "和『动画_状态机』上填的一致(默认 anim_state)"),
    )
    gk_outputs = (
        S.pin(BOOL, "已结束", False),
        S.pin(BOOL, "在跑", True),
        S.pin(TEXT, "当前状态", ""),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_state_var", text="", icon="SORTALPHA")


@N
class GKNodeAnimTransition(B.GKStatementNode):
    bl_idname = "GKNodeAnimTransition"
    bl_label = "动画_转场"
    bl_icon = "TRACKING_FORWARDS_SINGLE"
    gk_category = "动画系统"
    gk_tree_type = "GKAnimationTree"
    gk_doc = (
        "一条转场规则(对应虚幻 5 的 Transition Rule):"
        "『条件』为真时,从左边连过来的状态切到右边连着的状态。"
        "── 三件事都要有 ──"
        "『上游』接上一个状态的『转到』(忘了接就用下面的『从』下拉选);"
        "『条件』接判断节点的输出,或者把『条件来源』改成"
        "『上游动画播完』/『选输入』直接挑一个按键;"
        "『转到』接下一个状态的『进入』(也能用『到』下拉选)。"
        "── 忘了接上游会怎样 ──"
        "以前这种情况导出后**什么都不生成**,状态机一动不动;"
        "现在导出时会明确告诉你缺哪根线"
    )
    # ── 为什么有两个输入引脚 ────────────────────────────────────────
    # 『进入』= **执行流**入口,接上游状态的『转到』。
    # 『条件』= 布尔判断,接『动画判断_移动状态』之类的输出,
    #          或者用『条件来源』在节点上直接选一个按键。
    #
    # 两者必须分开:一条转移既要知道"从哪来"(流),
    # 又要知道"什么条件下切"(布尔)。合成一个引脚会互相覆盖。
    gk_inputs = (
        # -- 引脚名要能一眼看出该接哪(用户实测踩过)----------------
        # 原来这个引脚叫『进入』,和状态节点的『进入』**同名** ——
        # 用户截图里就是把上游状态的『转到』接到了转场的
        # 『条件』上,而这个『进入』空着,于是链路是空的、
        # 整个状态机没有一条转移(实测诊断出来的)。
        #
        # 改叫『上游』,并说清接上一个状态的『转到』。
        S.pin(FLOW, "上游"),
        S.pin(BOOL, "条件", False),
    )
    gk_outputs = (
        S.pin(FLOW, "转到"),
    )
    gk_prop_defs = (
        # ── 备用下拉:连线忘了也能用(用户 2026-10-03 确认要)──────────
        # 起因:他的树里『上游』空着、『转到』却接了状态,导出后整台状态机
        # 是死的(_physics_process 只有 pass)。连线仍是首选(看得见箭头),
        # 但下拉是兜底 —— 少接一根线不该让状态机静默失效。
        # 两个都填时**以连线为准**(见 nodes.anim_tree_graph)。
        ("gk_from_state", "string", "", "从状态",
         "备用:『上游』没接线时用这个(下拉里是这张树的状态)"),
        ("gk_to_state", "string", "", "到状态",
         "**推荐**:点右边的下拉从这张树的状态里挑'切到哪个'。"
         "这样图里不用连回状态的线 —— Blender 不允许环,"
         "连线连成环会报『链接形式的循环不受支持』"),
        # ── 复合条件:不用额外拉表达式节点 ──────────────────────────
        # 蓝图里最常见的条件就是"某个键按住/松开"。为了这个去拉一个
        # 『输入_按住』节点再连线,状态一多整张图就全是线 —— 那正是
        # 用户抱怨的"密密麻麻"。
        ("gk_cond_source", "enum", "PIN", "条件来源",
         "『自己写』= 用左边的条件引脚;其它选项都在节点上直接挑",
         (("PIN", "自己写(接引脚)", "想多复杂都行:速度、距离、组合条件"),
          ("FINISH", "上游动画播完", "上游状态那段动画播完的那一刻切过去"
                                     "(动画要设成不循环)"),
          ("ACTION_PRESSED", "选输入:按住", "按键按住期间为真"),
          ("ACTION_JUST", "选输入:刚按下", "按键刚按下那一帧为真"),
          ("ACTION_RELEASED", "选输入:松开", "按键松开期间为真"))),
        ("gk_cond_action", "string", "move_right", "输入动作",
         "只有『条件来源』选了输入类才用得上"),
    )

    def condition_expr(self, ctx):
        """这条转场的条件表达式;没有条件就返回 None。

        只有『自己写』和『选输入』两种在这里处理 —— 『上游动画播完』
        要靠"上游状态叫什么名字"才能算,那是**拓扑**信息,所以放在
        codegen 的状态图遍历里(见 codegen._edge_condition)。
        """
        source = str(getattr(self, "gk_cond_source", "PIN"))
        if source == "PIN":
            socket = self.inputs.get("条件")
            if socket is None or not getattr(socket, "is_linked", False):
                return None
            return ctx.expr_of(socket)
        if source == "FINISH":
            return None
        action = str(getattr(self, "gk_cond_action", "move_right") or "")
        if not action:
            return None
        const = ctx.add_action(action)
        func = ANIM_ACTION_SOURCES.get(source)
        return f"{func}({const})" if func else None

    def draw_buttons(self, context, layout):
        source = str(getattr(self, "gk_cond_source", "PIN"))
        layout.prop(self, "gk_cond_source", text="")
        # ── 从 / 到:连线优先,下拉兜底 ────────────────────────────
        # 接好了线就只画一个 ✓(把"这台状态机到底认哪条边"一眼说清),
        # 没接才把下拉和放大镜摆出来 —— 节点本来就小,常驻两个空输入框
        # 只会让人以为"这里必须填"。
        wired_from = bool(link_sources(self, "上游"))
        wired_to = bool(link_targets(self, "转到"))
        if wired_from:
            layout.label(text="上游 <- 上一个状态的『转到』", icon="CHECKMARK")
        else:
            row = layout.row(align=True)
            row.label(text="上游空着 -> 用『从』兜底", icon="LOOP_BACK")
            pick = row.operator("gk.pick_anim_state", text="", icon="VIEWZOOM")
            pick.node_name = self.name
            pick.prop_name = "gk_from_state"
            layout.prop(self, "gk_from_state", text="从")
        if source == "PIN":
            layout.label(text="条件 <- 接判断节点的输出", icon="LINKED")
        elif source == "FINISH":
            layout.label(text="切过去 = 上游那段动画播完", icon="TIME")
        else:
            layout.prop(self, "gk_cond_action", text="")
        # ── "线被忽略了"必须画在节点上(用户 2026-10-03 的疑问)────────
        # 他的图:『在地面』接在『条件』上,而『条件来源』选的是别的 ——
        # 于是那根线**根本没参与判定**,可节点上完全看不出来,
        # 他只会觉得"这个切换条件不能自定义"。
        if source != "PIN" and link_sources(self, "条件"):
            layout.label(text="! 条件引脚接的线被忽略了", icon="ERROR")
            layout.label(text="   以『条件来源』为准")
        if wired_to:
            layout.label(text="转到 -> 下一个状态的『进入』", icon="CHECKMARK")
        else:
            row = layout.row(align=True)
            row.label(text="转到空着 -> 用『到』兜底", icon="FORWARD")
            pick = row.operator("gk.pick_anim_state", text="", icon="VIEWZOOM")
            pick.node_name = self.name
            pick.prop_name = "gk_to_state"
            layout.prop(self, "gk_to_state", text="到")


# ==========================================================================
# 动画树的自定义变量节点(照虚幻蓝图的 Get / Set)
# ==========================================================================
# 用户要求:"在侧边栏可以添加变量，自定义变量引脚连线"。
# 变量本身存在动画树上(见 properties.GKAnimVariable),这里只负责
# 把它**变成图里的引脚**:『取』拉出一个值,『设』写进去。
#
# 用 ANY 引脚而不是按类型分好几种节点:变量可以随时改类型,
# 分成"布尔取变量/浮点取变量…"的话改类型就得换节点、连线全断。
@N
class GKNodeAnimGetVar(B.GKExpressionNode):
    bl_idname = "GKNodeAnimGetVar"
    bl_label = "动画_取变量"
    bl_icon = "DRIVER"
    gk_category = "动画系统"
    gk_tree_type = "GKAnimationTree"
    gk_doc = ("读取这张动画树的自定义变量(在侧栏『变量』里加)。"
              "相当于蓝图里把变量拖进图的那个 Get 节点")
    gk_outputs = (S.pin(ANY, "值", None),)

    gk_prop_defs = (
        ("gk_var_name", "string", "", "变量名",
         "侧栏『变量』里声明的名字(点那行的『取』会自动填好)"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_var_name", text="", icon="SORTALPHA")
        picker = row.operator("gk.anim_var_pick", text="", icon="VIEWZOOM")
        picker.node_name = self.name
        layout.label(text="值 -> 接要用它的引脚", icon="FORWARD")


@N
class GKNodeAnimSetVar(B.GKStatementNode):
    bl_idname = "GKNodeAnimSetVar"
    bl_label = "动画_设变量"
    bl_icon = "CHECKBOX_HLT"
    gk_category = "动画系统"
    gk_tree_type = "GKAnimationTree"
    gk_doc = ("给这张动画树的自定义变量赋值 —— 蓝图里的 Set 节点。"
              "变量一改,引用它的转场条件下一帧就跟着变")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(ANY, "值", None),)
    gk_outputs = (S.pin(FLOW, "完成"),)

    gk_prop_defs = (
        ("gk_var_name", "string", "", "变量名",
         "侧栏『变量』里声明的名字(点那行的『设』会自动填好)"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_var_name", text="", icon="SORTALPHA")
        picker = row.operator("gk.anim_var_pick", text="", icon="VIEWZOOM")
        picker.node_name = self.name
        layout.label(text="执行 -> 完成", icon="FORWARD")


@N
class GKNodeAnimStateMachineRef(B.GKStatementNode):
    bl_idname = "GKNodeAnimStateMachineRef"
    bl_label = "动画_状态机"
    bl_icon = "AUTOMERGE_ON"
    gk_category = "动画"
    gk_doc = ("驱动一张**动画树**(在『游戏动画』面板里建)。"
              "动作的切换规则全写在动画树里,这里只负责启动它 —— "
              "玩法图和动画图分开放,两张图都不会糊成一团。"
              "『树名』留空 = 用文件里第一张动画树。"
              "── 作用在谁身上 ──"
              "**就是脚本挂着的这个物体**(角色的 AnimationPlayer 在"
              "模型内部,导出后自动往下找),所以不需要再选物体")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "树名", ""),
    )
    gk_input_props = {"树名": "gk_tree_name"}
    gk_outputs = (S.pin(FLOW, "完成"),)

    gk_prop_defs = (
        # ── 树名:文本框 + 『选…』按钮(和『动画_播放』同一套)──────────
        # 想做成下拉,但 Blender 不允许动态 items 的枚举配字符串默认值
        # (`'default' can only be an integer when 'items' is a function`)。
        # 所以沿用那个已经验证过的做法:文本框 + 按钮弹列表。
        #
        # 留空 = 用文件里第一张动画树。**只有一张树时根本不用管它** ——
        # 这就是用户问的"一般都是两个就可以了吧"的答案:
        # 是的,大多数情况留空就行。
        ("gk_tree_name", "string", "", "树名",
         "留空 = 用文件里第一张动画树(只有一张时不用管)"),
        # ── 『动画播放器』物体选择已删除(用户 2026-10-03 要求)─────────
        # 用户原话:"删除物体选择,原因:在此物体选择动画_状态机时自然是
        # 作用于本身"。对 —— 这个节点生成的是**脚本所在物体**的代码,
        # 选别的物体只会生成一条指向它的路径,既没必要又容易选错。
        #
        # 属性本身留着(不画、不用):老文件里存过值,删掉声明会让
        # Blender 读文件时报未知属性。见 codegen.animation_player。
        ("gk_player", "node", "", "(已废弃)动画播放器",
         "已经不画也不用了 —— 状态机作用在脚本所在物体上"),
        ("gk_state_var", "string", "anim_state", "状态变量名",
         "当前状态存在哪个成员变量里"),
        ("gk_check_mode", "enum", "EVERY_FRAME", "检查时机",
         "每个物理帧判一次 / 只在开始时判一次",
         (("EVERY_FRAME", "每帧检查", "动作连贯,适合走跑切换"),
          ("ON_ENTER", "只在开始时判", "省性能,适合一次性判定"))),
        # ── 调试开关(用户要的"打印节点")────────────────────────────
        # 与其再加一个『调试_打印状态』节点串在链上(会把链路图搞复杂),
        # 不如在这里加个勾选框 —— 状态切换本来就只有**一处**生成点,
        # 在这儿加打印最省事。
        ("gk_debug_print", "bool", False, "打印状态变化",
         "勾上后每次切换状态都往 Godot 输出面板打一行,"
         "用来确认状态机到底有没有在跑"),
    )

    def draw_buttons(self, context, layout):
        # ── 树名:只有一张树时不问,直接显示会用哪张(用户要求)─────────
        # 用户原话:"在指定物体后,就会自动查找…并不需要输入而自动列出来"。
        # 手输树名是这套流程里唯一还要打字的地方,而大多数文件只有一张树
        # —— 那种情况下这个输入框纯属多余,还容易打错。
        # ── 树名:永远给出可选项(用户 2026-10-03 的疑问)──────────────
        # 用户问"一个游戏逻辑里是否可以有多个游戏动画?"—— 能(见 codegen
        # 里状态变量的按节点隔离),但前提是**你得能选到第二张树**。
        # 老逻辑"只有一张树时不画选择器"让这个入口看起来根本不存在,
        # 所以现在**一直画**:一张时它显示名字,多张时点放大镜就能换。
        trees = animation_tree_names()
        row = layout.row(align=True)
        if len(trees) <= 1:
            row.label(text=f"动画树:{trees[0] if trees else '(还没有)'}",
                      icon="NODETREE")
        else:
            row.prop(self, "gk_tree_name", text="", icon="NODETREE")
        picker = row.operator("gk.pick_anim_tree", text="", icon="VIEWZOOM")
        picker.node_name = self.name
        if len(trees) > 1:
            row.label(text=f"(共 {len(trees)} 张)", icon="INFO")
        layout.prop(self, "gk_check_mode", text="")
        layout.prop(self, "gk_debug_print", text="打印状态变化")
        self._draw_tree_graph(layout)

    def _draw_tree_graph(self, layout):
        """把这张动画树的**状态图**摊在节点上。

        ── 为什么要画在节点上(而不是只放侧栏)────────────────────────
        用户 2026-10-03 报"动画_状态机未能正常工作",而真正的原因是
        他的转场少接了一根线 —— 节点上什么提示都没有,他只能反复导出
        去 Godot 里试。图和问题直接画在节点上,一眼就能对上。
        """
        tree = find_animation_tree(getattr(self, "gk_tree_name", ""))
        if tree is None:
            layout.label(text="还没有动画树 —— 点『打开/新建动画树』",
                         icon="ERROR")
            return
        graph = anim_tree_graph(tree)
        states = [s for s in graph["states"] if anim_state_name(s)]
        column = layout.column(align=True)
        entry = graph["entry"]
        column.label(text=f"{len(states)} 状态 / {len(graph['edges'])} 条转场",
                     icon="ARMATURE_DATA")
        for state in states[:8]:
            name = anim_state_name(state)
            here = "起始 " if state is entry else "     "
            icon = "PLAY" if state is entry else "FORWARD"
            column.label(text=f"{here}{name}", icon=icon)
        if len(states) > 8:
            column.label(text=f"     …还有 {len(states) - 8} 个", icon="DOT")
        for problem in graph["problems"][:3]:
            # 节点上的字要短 —— 完整的话留给侧栏和导出报告
            column.label(text=short_problem(problem), icon="ERROR")
        if len(graph["problems"]) > 3:
            column.label(text=f"…还有 {len(graph['problems']) - 3} 条问题"
                              f"(见侧栏『游戏动画』)", icon="INFO")

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
    # ── 标签必须和新的引用节点区分开(踩过的坑)────────────────────
    # 两个节点的标签都叫『动画_状态机』,菜单里**看起来一模一样**,
    # 用户截图问"这怎么有两个动画状态机"。
    # 这个是把状态表塞进单节点的旧版,用户已经明确否掉了
    # ("这也太简陋了吧...后面就密密麻麻的"),所以标上『旧』。
    bl_label = "动画_状态表(旧)"
    bl_icon = "AUTOMERGE_ON"
    # 挪到单独的『动画(旧)』分类,和新的动画树节点彻底分开,
    # 免得用户在『动画』里又选错。
    gk_category = "动画(旧)"
    gk_doc = ("【旧版,建议改用『动画_状态机』+ 独立动画树】"
              "把状态表直接写在节点里。东西一多节点就糊成一团,"
              "所以新方案改成了独立的动画树。留着是为了兼容已经用了它的图")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(TEXT, "起始状态", "idle"),)
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
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "速度", 1.0),)
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
class GKNodeAnimFinished(B.GKExpressionNode):
    bl_idname = "GKNodeAnimFinished"
    bl_label = "动画_播放完成"
    bl_icon = "CHECKMARK"
    gk_category = "动画"
    # 两张树里都能用:动画树里拼转场条件,玩法图里做"播完了就设变量/换状态"
    gk_tree_type = BOTH_TREES
    gk_doc = ("某段动画**播完了吗** —— 一次性动画播完那一刻起为真。"
              "『动画名』留空 = 只要有任意动画播完就算真。"
              "── 它能用在两处 ──"
              "动画树:和『逻辑_布尔运算』拼出「播完了**并且**在地面」这种复合转场条件;"
              "玩法图:`如果(动画_播放完成) -> 变量_设置`,做「播完就清标志/发奖励」。"
              "注:标志会一直保留到**下一段动画开始播**(进入新状态会自动清掉),"
              "所以不用担心『只在那一帧有效』而错过")
    gk_prop_defs = (
        ("gk_player", "node", "", "动画播放器", "留空 = 自动查找"),
        ("gk_anim_name", "string", "", "动画名", "留空 = 任意动画播完就算"),
        ("gk_anim_action", "action", None, "动作",
         "从文件里所有动作中选一个(原生选择器,能搜索)"),
    )
    gk_outputs = (S.pin(BOOL, "完成", False),)


    def draw_buttons(self, context, layout):
        # 和『动画_播放』同一套:选了动作就只显示名字,没选才给手填框
        layout.prop(self, "gk_anim_action", text="")
        if self.gk_anim_action is not None:
            layout.label(text=f"动画名:{self.gk_anim_action.name}",
                         icon="CHECKMARK")
        else:
            layout.prop(self, "gk_anim_name", text="", icon="SORTALPHA")


@N
class GKNodeSetState(B.GKStatementNode):
    bl_idname = "GKNodeSetState"
    bl_label = "动画_切换状态"
    bl_icon = "IMAGE_DATA"
    gk_category = "动画"
    gk_doc = "切换角色状态(idle / run / jump / attack / hurt …)"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(TEXT, "状态", "idle"),)
    gk_input_props = {"状态": "gk_state"}
    gk_prop_defs = (
        ("gk_state", "string", "idle", "状态名", "角色状态字符串"),
        ("gk_var", "string", "state", "状态变量名", "要写入的成员变量名"),
    )
