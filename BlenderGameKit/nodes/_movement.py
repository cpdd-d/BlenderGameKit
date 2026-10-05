"""移动节点(速度/滑动/跳跃/旋转) —— 从 nodes/nodes.py 按领域拆出的一块(GPT 审查:5280 行的单文件)。

本模块里的节点类通过 `@N` 登记进 `_common.CLASSES`。门面 `nodes/nodes.py`
按拆分前的**原始顺序**导入各领域模块,保证 `ALL_CLASSES` 的顺序不变。
"""

from ._common import *  # noqa: F401,F403



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
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(VECTOR, "速度", (0.0, 0.0, 0.0)),)
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
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (("gk_target", "node", TARGET_SELF, "目标", "要移动的节点"),)

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)


@N
class GKNodeMoveDirection(B.GKStatementNode):
    bl_idname = "GKNodeMoveDirection"
    bl_label = "移动_按方向"
    bl_icon = "ORIENTATION_GLOBAL"
    gk_category = "移动"
    gk_doc = ("沿指定方向移动。**平台跳跃 / 有重力的游戏务必勾上『重力模式』**："
              "Godot 4 的 CharacterBody 不会自动施加重力，不勾角色会浮在原地不动。\n"
              "『前进轴』『横移轴』决定输入往哪个世界轴走 —— 按着不对就翻一下"
              "(和 UPBGE 里设移动轴是一回事)\n"
              "── 第三人称:方向要跟着相机转 ──\n"
              "把『参考系』接一个相机 / 空节点(按它的水平朝向变换),或者没接线时"
              "勾上『随相机朝向』直接用当前相机 —— 这样转视角后『前』始终是镜头前方,"
              "不会出现『视角转了、按前却往原来的方向走』的错位感(UE 蓝图里 "
              "MoveForward/Right 取 Controller 朝向也是这个道理)\n"
              "── 上台阶 ──\n"
              "Godot 的运动学角色**不会**自动上台阶:0.35 米的台阶在它眼里就是一堵墙。"
              "『上台阶高度』填个数(比如 0.5)就会自动抬腿迈上去;填 0 保持原样 —— "
              "比它高的台阶仍然挡住(0.5 上得了半米以下的台阶/路沿,上不了 1 米的墙)")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(VECTOR, "方向", (0.0, 0.0, 0.0)),
        S.pin(FLOAT, "速度", 300.0),
        S.pin(OBJECT, "参考系", None),
    )
    gk_input_props = {"速度": "gk_speed"}
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "目标", "要移动的节点"),
        ("gk_dir_x", "float", 0.0, "方向 X", ""),
        ("gk_dir_y", "float", 0.0, "方向 Y", ""),
        ("gk_speed", "float", 300.0, "速度", "未连线时的速度"),
        # ── 输入往哪个世界轴走(用户要求:像 UPBGE 那样能设)──────────
        # 默认 XZ 平面、前进走 -Z(Godot / glTF 的"前")。
        # 实测踩过:相机朝向不同时,"按前往后、按左往右"完全可能 ——
        # 这不该逼用户去改脚本,给个选项翻过来就行。
        # 2D 项目用不到(只有 X 轴),这两项会被忽略。
        ("gk_axis_h", "enum", "X", "横移轴",
         "左右输入作用在哪个世界轴上",
         (("X", "X (左右)", ""), ("Z", "Z (前后)", ""), ("Y", "Y (上下)", ""))),
        ("gk_axis_v", "enum", "Z", "前进轴",
         "前后输入作用在哪个世界轴上",
         (("Z", "Z (前后)", ""), ("X", "X (左右)", ""), ("Y", "Y (上下)", ""))),
        ("gk_forward_sign", "enum", "NEG", "前进方向",
         "按『前』时沿前进轴的正向还是负向走。默认负向 = Godot / glTF 的『前』",
         (("NEG", "负 (Godot 的『前』)", ""), ("POS", "正", ""))),
        # ── 为什么需要这个开关 ────────────────────────────────────────
        # Godot 4 的 CharacterBody2D/3D **不会自动施加重力**，它只按你设的
        # velocity 移动。所以平台跳跃游戏必须自己累加重力，否则角色
        # 会永远浮在原地(实测:velocity 恒为 (0,0)、is_on_floor 恒 false，
        # 看起来就像"脚本没生效")。
        #
        # 打开这个开关会同时做两件事:
        #   1) 每帧累加 get_gravity()
        #   2) 只改水平速度，纵向留给重力
        # 俯视角游戏(不需要重力)保持关闭即可。
        # 默认关:保持既有工程行为不变，避免"升级后手感突然变了"。
        ("gk_keep_y", "bool", False, "重力模式",
         "平台跳跃 / 有重力的游戏请勾上:自动施加重力，且只改水平速度"),
        # ── 第三人称:移动方向跟着相机转(用户 2026-10-04)──────────────
        # 勾上后『方向』按**当前相机**的水平朝向变换:转视角之后,
        # 手柄/WASD 的"前"始终是镜头前方 —— 否则视角一转,方向感就错位。
        # 只在 3D 下生效(2D 没有"绕着角色转的视角"这个概念)。
        # 如果『参考系』引脚接了东西,以引脚为准(这个勾选作兜底)。
        ("gk_follow_camera", "bool", False, "随相机朝向",
         "3D 第三人称:移动方向按当前相机的水平朝向变换(转视角后方向感正确)"),
        # ── 推开刚体(用户 2026-10-05 第二十八轮)────────────────────
        # Godot 的 CharacterBody 是**运动学体**:撞到刚体时只会"贴着它滑走",
        # 不会给刚体任何力 —— 表现就是"箱子焊在地上,推不动"。
        # 勾上后在 move_and_slide() 之后遍历碰撞,对撞到的 RigidBody 施加
        # 一个持续推力(冲量 = 推力 × 帧时长 × 质量)—— 轻重箱子手感一致。
        ("gk_push_bodies", "bool", False, "推开刚体",
         "撞到刚体(箱子/桶)时把它推走。Godot 的运动学角色默认推不动刚体 —— "
         "不勾就是'贴着滑走、箱子纹丝不动'"),
        ("gk_push_force", "float", 30.0, "推力",
         "推开刚体用的力(牛顿);箱子越重需要越大(冲量按质量缩放)"),
        # ── 上台阶(用户 2026-10-05 第二十九轮)────────────────────────
        # Godot 的 CharacterBody 是**运动学体**:move_and_slide() 撞到台阶
        # 只会"贴着前脸滑走",一点台阶都上不去(实测:0.35 米的三级台阶
        # 在它眼里就是三堵墙)。填一个高度就开启"抬腿":被墙/台阶前脸挡住
        # 且站在地面时,先探「抬起来再往前」这条路通不通、落点下面有没有地
        # —— 都过了才平移过去(不是硬挤,不会穿模/抖动)。
        # 默认 0 = 关闭:老图产物一个字不变。
        ("gk_step_height", "float", 0.0, "上台阶高度",
         "自动上台阶(0 = 关闭)。填一步最多能抬多高(米),比它高的台阶仍然挡住"
         " —— 0.5 = 上得了半米以下的台阶/路沿"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        B.draw_target_picker(self, row)
        row.prop(self, "gk_speed", text="")
        layout.prop(self, "gk_keep_y", text="重力模式(平台游戏勾上)", toggle=True)
        row = layout.row(align=True)
        row.prop(self, "gk_follow_camera", text="随相机朝向", toggle=True)
        row.prop(self, "gk_push_bodies", text="推开刚体", toggle=True)
        if getattr(self, "gk_push_bodies", False):
            layout.prop(self, "gk_push_force", text="推力")
        layout.prop(self, "gk_step_height", text="上台阶高度 (0=关)")

    def draw_buttons_ext(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_dir_x")
        row.prop(self, "gk_dir_y")


@N
class GKNodeMoveToward(B.GKStatementNode):
    bl_idname = "GKNodeMoveToward"
    bl_label = "移动_朝向目标"
    bl_icon = "TRACKER"
    gk_category = "移动"
    gk_doc = ("朝一个目标走过去 —— 追人的敌人 / 宠物跟随 / 自动靠近的 NPC 都用它。"
              "走的是 CharacterBody 那套(velocity + move_and_slide),所以"
              "『谁走』必须是角色体(3D 角色 / 2D 角色),脚本基类也得是 "
              "CharacterBody。\n"
              "『停在多远』= 离目标这么近就刹住(0 = 一直贴上去);\n"
              "『面向目标』= 走的时候顺便转过去看它(3D 用 look_at,2D 转角度);\n"
              "『加速』= 0 是立刻到速度(瞬起瞬停);填个数就变成平滑加减速 —— "
              "数值 = 每秒最多能改变多少速度(追人的敌人填速度的 2~4 倍手感比较软);\n"
              "『到达』输出 = 已经进了『停在多远』的范围(判断\"追到了没有\""
              "直接用这个,不用再接一遍距离 + 比较两个节点)。"
              "⚠ 『停在多远』填 0 时它几乎永远是假(要贴上才算到),"
              "要判到达就给它填个数")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(OBJECT, "目标", None),
        S.pin(FLOAT, "速度", 300.0),
    )
    gk_input_props = {"速度": "gk_speed"}
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "谁走", "要移动的角色节点"),
        ("gk_goal", "node", "", "目标", "往谁那儿走(『目标』引脚没接线时用它)"),
        ("gk_speed", "float", 300.0, "速度", "未连线时的速度(2D 像素/秒,3D 米/秒)"),
        ("gk_stop_distance", "float", 0.0, "停在多远",
         "离目标这么近就停下,不再往前挤(0 = 贴上去)"),
        ("gk_turn", "bool", False, "面向目标", "移动时顺便转向目标"),
        ("gk_accel", "float", 0.0, "加速",
         "0 = 立刻到速度;大于 0 = 平滑加减速(每秒能改变多少速度)"),
    )
    # 『到达』是**数据输出**(不是执行口):接了线的地方会现算
    # "离目标够近了吗" —— 自包含表达式,见 statements 里那个 multi_output
    gk_outputs = (S.pin(FLOW, "完成"), S.pin(BOOL, "到达", False),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        B.draw_target_picker(self, row, "gk_target")
        row.prop(self, "gk_speed", text="")
        row = layout.row(align=True)
        row.label(text="目标")
        B.draw_target_picker(self, row, "gk_goal")
        row.prop(self, "gk_stop_distance", text="停在多远")
        row = layout.row(align=True)
        row.prop(self, "gk_turn", text="面向目标", toggle=True)
        row.prop(self, "gk_accel", text="加速")


@N
class GKNodeJump(B.GKStatementNode):
    bl_idname = "GKNodeJump"
    bl_label = "移动_跳跃"
    bl_icon = "TRIA_UP"
    gk_category = "移动"
    gk_doc = "设置纵向速度实现跳跃"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "强度", 400.0),)
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
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(VECTOR, "冲量", (0.0, -200.0, 0.0)),)
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
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "角度", 0.0), S.pin(INT, "轴", 1),)
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
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(BOOL, "朝左", False),)
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
# 载具(用户 2026-10-05 第三十轮:可驾驶载具)
# ==========================================================================
#
# ── 状态放在**载具自己身上**(而不是成员变量)─────────────────────
# 『载具_上车』『载具_下车』『载具_驾驶』是三个节点,却必须共享同一份
# "这辆车现在有没有人在开":生成成员变量的话,两辆车的节点会抢同一个
# 名字;按名字区分又怕用户复制节点。所以状态挂在**载具节点自己**身上
# (`set_meta` / `get_meta`)—— 同一辆车的三个节点自动是同一份,
# 场景里摆两辆车也互不串。
#
# ── 载具做成什么类型 ────────────────────────────────────────────
# 载具用 **CharacterBody**(和角色同款):『载具_驾驶』用
# velocity + move_and_slide 开它,撞墙会停、会顺着坡走 —— 不用自己写
# 碰撞。**(想在车上再站人)就要用 AnimatableBody**,那是"移动平台"。


@N
class GKNodeEnterVehicle(B.GKStatementNode):
    bl_idname = "GKNodeEnterVehicle"
    bl_label = "载具_上车"
    bl_icon = "TRACKER"
    gk_category = "移动"
    gk_doc = ("走上车按一下 → 接管驾驶:隐藏角色(可选)、关掉角色碰撞、"
              "把角色摆到**座位**上;之后由『载具_驾驶』每帧重新钉住座位,"
              "所以镜头、位置判定都跟着车走。\n"
              "『上车距离』= 离车这么近才上得去(0 = 不限)—— 免得在"
              "地球另一头按一下键就瞬移上车。\n"
              "上/下车和驾驶三个节点靠**载具自己身上的状态**互通,"
              "不用你摆变量;同一辆车摆两处也互不干扰")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_vehicle", "node", "", "载具", "上哪辆车(场景里另一个物体,做成 CharacterBody)"),
        ("gk_target", "node", TARGET_SELF, "角色", "谁上车;默认 = 脚本挂载的那个角色"),
        ("gk_seat", "float_vector", (0.0, 1.2, 0.0), "座位",
         "座位在**载具本地空间**的偏移(Y = 坐多高);按车的大小调"),
        ("gk_max_distance", "float", 3.0, "上车距离",
         "离载具这么近才上得去;0 = 不限"),
        ("gk_hide", "bool", True, "隐藏角色", "上车后把角色藏起来(只看得见车)"),
        ("gk_disable_collision", "bool", True, "关掉角色碰撞",
         "免得角色在车里和载具顶来顶去"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.label(text="载具")
        B.draw_target_picker(self, row, "gk_vehicle")
        row = layout.row(align=True)
        row.prop(self, "gk_seat", text="")
        row = layout.row(align=True)
        row.label(text="角色")
        B.draw_target_picker(self, row, "gk_target")
        row.prop(self, "gk_max_distance", text="上车距离")
        row = layout.row(align=True)
        row.prop(self, "gk_hide", text="隐藏角色", toggle=True)
        row.prop(self, "gk_disable_collision", text="关掉碰撞", toggle=True)


@N
class GKNodeExitVehicle(B.GKStatementNode):
    bl_idname = "GKNodeExitVehicle"
    bl_label = "载具_下车"
    bl_icon = "TRACKER"
    gk_category = "移动"
    gk_doc = ("再按一下 → 放人下车:角色摆到**下车位置**(载具本地空间的点,"
              "默认左手边 2 米),恢复显示与碰撞,驾驶权交还角色。\n"
              "摆在车旁边或地上都行 —— 落地交给重力")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_vehicle", "node", "", "载具", "从哪辆车下来"),
        ("gk_target", "node", TARGET_SELF, "角色", "谁下车"),
        ("gk_exit", "float_vector", (2.0, 0.5, 0.0), "下车位置",
         "下车后摆在**载具本地空间**的哪一点(默认左手边 2 米)"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.label(text="载具")
        B.draw_target_picker(self, row, "gk_vehicle")
        row = layout.row(align=True)
        row.prop(self, "gk_exit", text="")
        row = layout.row(align=True)
        row.label(text="角色")
        B.draw_target_picker(self, row, "gk_target")


@N
class GKNodeDriveVehicle(B.GKStatementNode):
    bl_idname = "GKNodeDriveVehicle"
    bl_label = "载具_驾驶"
    bl_icon = "TRACKER"
    gk_category = "移动"
    gk_doc = ("挂在『每当_物理帧』里(要排在角色的『移动_按方向』**后面**,"
              "这样它钉座位的那一下才不会被角色的移动覆盖)。\n"
              "没人在开的时候什么都不做;**上帝视角(Tab)期间也不接管** —— "
              "那时候 WASD 是飞相机的。\n"
              "有人开时:\n"
              "W/S = 前进 / 后退(沿**车头**方向,不是相机方向)、"
              "A/D = 转车头 —— 车感。\n"
              "『驾驶中』输出:接『如果_否则』就能做"
              "'按同一个键上车/下车'(是 = 下车,否 = 上车)")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(VECTOR, "方向", (0.0, 0.0, 0.0)),)
    gk_prop_defs = (
        ("gk_vehicle", "node", "", "载具", "开哪辆车"),
        ("gk_target", "node", TARGET_SELF, "角色", "坐在车上的角色(每帧钉在座位上)"),
        ("gk_speed", "float", 10.0, "速度", "米/秒;W 前进、S 后退"),
        ("gk_turn_speed", "float", 120.0, "转向速度", "度/秒;A / D 转车头"),
    )
    gk_outputs = (S.pin(FLOW, "完成"), S.pin(BOOL, "驾驶中", False),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.label(text="载具")
        B.draw_target_picker(self, row, "gk_vehicle")
        row = layout.row(align=True)
        row.prop(self, "gk_speed", text="")
        row.prop(self, "gk_turn_speed", text="转向")
        row = layout.row(align=True)
        row.label(text="角色")
        B.draw_target_picker(self, row, "gk_target")
