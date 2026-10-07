"""显示/相机/骨骼/音效/存档/界面/屏幕/射线/定时 —— 从 nodes/nodes.py 按领域拆出的一块(GPT 审查:5280 行的单文件)。

本模块里的节点类通过 `@N` 登记进 `_common.CLASSES`。门面 `nodes/nodes.py`
按拆分前的**原始顺序**导入各领域模块,保证 `ALL_CLASSES` 的顺序不变。
"""

from ._common import *  # noqa: F401,F403



# ==========================================================================
# 显示
# ==========================================================================
@N
class GKNodeSetVisible(B.GKStatementNode):
    bl_idname = "GKNodeSetVisible"
    bl_label = "显示_设置可见"
    bl_icon = "HIDE_OFF"
    gk_category = "显示"
    gk_doc = "显示或隐藏一个节点(2D / 3D 通用，对应 visible)"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(BOOL, "可见", True),)
    gk_input_props = {"可见": "gk_visible"}
    gk_prop_defs = (
        ("gk_visible", "bool", True, "可见", "未连线时用它决定显示还是隐藏"),
        ("gk_target", "node", TARGET_SELF, "目标", "要显示 / 隐藏的节点"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_visible", text="", toggle=True,
                 icon="HIDE_OFF" if getattr(self, "gk_visible", True) else "HIDE_ON")
        B.draw_target_picker(self, layout)


@N
class GKNodeSetPaused(B.GKStatementNode):
    bl_idname = "GKNodeSetPaused"
    bl_label = "显示_暂停节点"
    bl_icon = "PAUSE"
    gk_category = "显示"
    gk_doc = "暂停 / 恢复一个节点的处理(对应 process_mode，不需要自己写计数逻辑)"
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_paused", "bool", True, "暂停", "勾上=停止该节点的 _process / _physics_process"),
        ("gk_target", "node", TARGET_SELF, "目标", "要暂停 / 恢复的节点"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_paused", text="", toggle=True,
                 icon="PAUSE" if getattr(self, "gk_paused", True) else "PLAY")
        B.draw_target_picker(self, layout)


# ==========================================================================
# 相机
# ==========================================================================
# ── 『过渡曲线』的档位(第十二轮:用户要"曲线运动",但不想连一大堆节点)──
# 一个下拉框搞定:内置档位直接用引擎的 TRANS/EASE;想要自己的形状就
# 选『自定义曲线』,在 Blender 里画一条曲线,导出成 Godot 的 Curve 资源。
#
# ⚠ 档位 id 必须和 `codegen/easing.py` 的 `EASE_PRESETS` 对得上
#   (节点侧只管画,代码生成侧管映射;对不上会静默退回匀速)。
EASE_ITEMS = (
    ("LINEAR", "匀速", "从头到尾一样快(以前的行为)"),
    ("EASE_IN", "缓入(慢起)", "起步慢、越走越快"),
    ("EASE_OUT", "缓出(慢停)", "起步快、到点前慢下来(运镜最常用)"),
    ("EASE_IN_OUT", "缓入缓出", "两头慢 —— 电影运镜那种丝滑"),
    ("BACK", "回弹", "冲过头一点再回来(弹性手感)"),
    ("BOUNCE", "弹跳", "到点时弹一下"),
    ("CUSTOM", "自定义曲线(自己画)", "在场景里画一条从左到右的曲线"),
)

# 『相机_切换』的走法(直线 / 沿曲线运镜)
CAMERA_PATH_MODES = (
    ("DIRECT", "直线过渡", "从旧机位直接插值过去(默认)"),
    ("CURVE", "沿曲线运镜", "在场景里画一条轨道,镜头沿着它飞过去"),
)
# ==========================================================================
# 摄像机套件(照虚幻蓝图 / UE 的 Camera + SpringArm 设计)
# ==========================================================================
# ── 为什么要重做一套(用户 2026-10-03 第四轮)──────────────────────
# 用户原话:"进行摄像机的设计:摄像机的设置,切换,启用与禁用,视距,视野,
# 分支的场景摄像机的设置,切换,启用与禁用,弹簧臂的设计,摄像机的旋转,
# 缩放,位置等向量,浮点,两变量的设计,同样是按照虚幻蓝图进行"。
#
# 虚幻里的对应关系(我们照这个来):
#     CameraComponent.FieldOfView            -> 『相机_缩放』(视野 / 缩放)
#     CameraComponent.Activate / Deactivate  -> 『相机_启用』
#     Set View Target with Blend             -> 『相机_切换』(过渡秒数)
#     SpringArm.TargetArmLength              -> 『相机_弹簧臂』臂长(视距)
#     SpringArm.SocketOffset / TargetOffset  -> 『相机_弹簧臂』偏移
#     SpringArm.bDoCollisionTest             -> 『相机_弹簧臂』碰撞检测
#     SpringArm.CameraLag                    -> 『相机_弹簧臂』平滑
#     SetActorLocation / SetActorRotation    -> 『相机_位置』/『相机_旋转』
#     GetCameraComponent 的各种取值           -> 『相机_取状态』
#
# 每个"设"节点都有『保留』(相对)和『直接设』(绝对)两种模式,和虚幻的
# SetActorLocation(Teleport / Sweep)一个思路:相对 = 在现在的基础上加。
@N
class GKNodeCameraActive(B.GKStatementNode):
    bl_idname = "GKNodeCameraActive"
    bl_label = "相机_启用"
    bl_icon = "CAMERA_DATA"
    gk_category = "相机"
    gk_doc = ("启用 / 禁用一个相机(照虚幻的 Activate / Deactivate)。"
              "『启用』= 切到它;『禁用』= 如果它正开着就交还控制权"
              "(画面会黑一下,所以一般接着『相机_切换』到另一台)。"
              "场景里有多台相机时,用『如果_否则』分场景挑一台 —— 这就是"
              "'分支的场景摄像机设置'")
    gk_inputs = (
        S.pin(FLOW, "执行"),
    )
    gk_prop_defs = (
        ("gk_active", "bool", True, "启用", "关掉 = 禁用(把它从当前相机上摘下来)"),
        ("gk_target", "node", "", "相机", "留空 = 用场景里的当前相机"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_active", text="启用" if self.gk_active else "禁用",
                 toggle=True, icon="CHECKBOX_HLT" if self.gk_active
                 else "CHECKBOX_DEHLT")
        B.draw_target_picker(self, row)


@N
class GKNodeCameraSetup(B.GKStatementNode):
    bl_idname = "GKNodeCameraSetup"
    bl_label = "相机_设置"
    bl_icon = "PREFERENCES"
    gk_category = "相机"
    gk_doc = ("一次把一台相机设好:**视野**(或 2D 的缩放)、**视距**"
              "(沿相机朝向推远/拉近多少)、朝向(可选)。"
              "带『平滑』就是渐变过去,0 = 立刻生效。"
              "场景里每台相机各放一个『相机_设置』,用分支挑 —— "
              "这就是多场景机位的做法")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "视野", 0.0),
        S.pin(FLOAT, "视距", 0.0),
    )
    gk_input_props = {"视野": "gk_fov", "视距": "gk_distance"}
    gk_prop_defs = (
        ("gk_fov", "float", 0.0, "视野",
         "3D = 垂直视场角(度);2D = 缩放倍数。0 = 不动它"),
        ("gk_distance", "float", 0.0, "视距",
         "沿相机当前朝向推远(正)/拉近(负)多少米。0 = 不动它"),
        ("gk_smooth", "float", 0.0, "平滑", "秒;0 = 立刻生效"),
        ("gk_target", "node", "", "相机", "留空 = 场景里的当前相机"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_fov", text="视野")
        row.prop(self, "gk_distance", text="视距")
        row.prop(self, "gk_smooth", text="秒")
        B.draw_target_picker(self, layout)


@N
class GKNodeCameraPosition(B.GKStatementNode):
    bl_idname = "GKNodeCameraPosition"
    bl_label = "相机_位置"
    bl_icon = "EMPTY_ARROWS"
    gk_category = "相机"
    gk_doc = ("设相机位置(照虚幻的 SetActorLocation)。"
              "**两种给法,接哪种用哪种**:①『位置』引脚接一个『向量_合并』;"
              "②用 X / Y / Z 三个浮点分别给。"
              "『保留』= 在当前位置上加(相对),『直接设』= 换成这个坐标(绝对)")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(VECTOR, "位置", (0.0, 0.0, 0.0)),
        S.pin(FLOAT, "X", 0.0),
        S.pin(FLOAT, "Y", 0.0),
        S.pin(FLOAT, "Z", 0.0),
    )
    gk_input_props = {"X": "gk_x", "Y": "gk_y", "Z": "gk_z"}
    gk_prop_defs = (
        ("gk_mode", "enum", "ADD", "怎么用", "",
         (("ADD", "保留(在当前位置上加)", "相对 —— 和虚幻的 AddActorLocalOffset 一个意思"),
          ("SET", "直接设(换成这个坐标)", "绝对 —— 和 SetActorLocation 一个意思"))),
        ("gk_smooth", "float", 0.0, "平滑", "秒;0 = 立刻到位"),
        ("gk_x", "float", 0.0, "X", "没接『位置』向量时用它"),
        ("gk_y", "float", 0.0, "Y", ""),
        ("gk_z", "float", 0.0, "Z", ""),
        ("gk_target", "node", "", "相机", "留空 = 场景里的当前相机"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_mode", text="")
        row.prop(self, "gk_smooth", text="秒")
        row = layout.row(align=True)
        row.prop(self, "gk_x", text="X")
        row.prop(self, "gk_y", text="Y")
        row.prop(self, "gk_z", text="Z")
        B.draw_target_picker(self, layout)


@N
class GKNodeCameraRotation(B.GKStatementNode):
    bl_idname = "GKNodeCameraRotation"
    bl_label = "相机_旋转"
    bl_icon = "ORIENTATION_LOCAL"
    gk_category = "相机"
    gk_doc = ("设相机朝向(照虚幻的 SetActorRotation),用偏航 / 俯仰 / 翻滚"
              "三个浮点给值(单位度)。"
              "第一人称转视角就是每帧『保留』+ 给一点增量;"
              "想反转某一轴,勾下面的『反转』,或者把增量填成负数。")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        # 『旋转』向量引脚**删掉**(用户两次提"两套给法冗余"):
        # 偏航/俯仰/翻滚三个浮点已经覆盖全部用法,而且在节点上就能填,
        # 不用先拉一个『向量_合并』。老图里接在这个引脚上的线会断
        # (引脚不存在了),需要改接到三个浮点上。
        S.pin(FLOAT, "偏航", 0.0),
        S.pin(FLOAT, "俯仰", 0.0),
        S.pin(FLOAT, "翻滚", 0.0),
    )
    gk_input_props = {"偏航": "gk_yaw", "俯仰": "gk_pitch", "翻滚": "gk_roll"}
    gk_prop_defs = (
        ("gk_mode", "enum", "ADD", "怎么用", "",
         (("ADD", "保留(在现有朝向上加)", "相对 —— 转视角用这个"),
          ("SET", "直接设(换成这个朝向)", "绝对 —— 摆机位用这个"))),
        ("gk_smooth", "float", 0.0, "平滑", "秒;0 = 立刻转过去"),
        ("gk_yaw", "float", 0.0, "偏航", "左右转(度);接『鼠标_移动量』的 X 就是转视角"),
        ("gk_pitch", "float", 0.0, "俯仰", "上下看(度);正 = 抬头"),
        ("gk_roll", "float", 0.0, "翻滚", "歪头(度)"),
        ("gk_clamp_pitch", "bool", True, "限制俯仰 ±89°",
         "勾上 = 不会翻过头(第一人称一定要勾)"),
        # ── 反转开关(用户 2026-10-03:"可以自定义方向,但默认跟随鼠标")──
        # 默认跟随鼠标(往右移 -> 视角往右)。想反过来(飞行模拟那种
        # "上移 = 低头")就勾上,不用再去改数学节点的符号。
        ("gk_invert_x", "bool", False, "反转左右",
         "勾上 = 鼠标往右移视角往左(默认跟随鼠标)"),
        ("gk_invert_y", "bool", False, "反转上下",
         "勾上 = 鼠标往上移视角往下(默认跟随鼠标)"),
        ("gk_target", "node", "", "相机", "留空 = 场景里的当前相机"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_mode", text="")
        row.prop(self, "gk_smooth", text="秒")
        row = layout.row(align=True)
        row.prop(self, "gk_yaw", text="偏航")
        row.prop(self, "gk_pitch", text="俯仰")
        row.prop(self, "gk_roll", text="翻滚")
        B.draw_target_picker(self, layout)


@N
class GKNodeCameraSpringArm(B.GKStatementNode):
    bl_idname = "GKNodeCameraSpringArm"
    bl_label = "相机_弹簧臂"
    bl_icon = "CON_FOLLOWPATH"
    gk_category = "相机"
    gk_doc = ("**虚幻的弹簧臂(SpringArm)**:相机挂在一条『会缩的杆子』上 —— "
              "平时退到『臂长』那么远,杆子撞到墙就自动缩短,所以镜头**不会"
              "穿墙**。第三人称/越肩视角的标准做法。"
              "参数对照虚幻:臂长 = TargetArmLength,高度/侧向偏移 = "
              "SocketOffset,俯仰 = 杆子往下压的角度,碰撞检测 = "
              "bDoCollisionTest,平滑 = CameraLag。"
              "引擎里【每物理帧】更新一次(和角色同一个时钟,画面才不抖)。"
              "── 左边接引脚就能**运行中改**(不接就用节点上填的值)──"
              "『杆子挂在哪』『臂长』『偏移』『俯仰』四个都能接线;"
              "右边还能**读出来**:枢轴 / 相机位置 / 方向(做准星、判断有没有"
              "被挡住都用得上)")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        # ── 引脚优先于属性(和全项目一致):接了线就用线那头的值 ──────
        S.pin(OBJECT, "弹簧臂"),
        S.pin(FLOAT, "臂长", 4.0),
        S.pin(VECTOR, "偏移", (0.0, 1.5, 0.5)),
        # 『偏航』= 杆子**绕角色**转多少度 —— 鼠标左右移动接这个,
        # 就是"第三人称绕着角色 360 度转"(用户第 29 轮)。
        # 正数 = 视角**右转**(鼠标右移给正值,刚好对上)。
        S.pin(FLOAT, "偏航", 0.0),
        # 俯仰:正数 = 相机抬高**从高处俯视**,负数 = 从低处仰视。
        S.pin(FLOAT, "俯仰", 15.0),
    )
    gk_input_props = {"臂长": "gk_arm", "偏航": "gk_yaw",
                      "俯仰": "gk_pitch"}
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "跟随谁",
         "杆子挂在谁身上(默认脚本所在节点)"),
        ("gk_arm_node", "node", "", "弹簧臂",
         "杆子挂在哪个节点上(留空 = 就用『跟随谁』);"
         "想让杆子挂在肩膀/摄像机架子上就填它"),
        ("gk_camera", "node", "", "相机", "留空 = 场景里的当前相机"),
        ("gk_arm", "float", 4.0, "臂长", "视距:想退到多远(米)"),
        ("gk_height", "float", 1.5, "高度偏移", "枢轴抬高多少(瞄肩膀/头)"),
        ("gk_side", "float", 0.5, "侧向偏移", "越肩视角往右挪一点"),
        ("gk_pitch", "float", 15.0, "俯仰",
         "相机抬高多少(度);正数 = 从高处俯视,负数 = 从低处仰视"),
        ("gk_yaw", "float", 0.0, "偏航",
         "杆子绕角色转多少度(正数 = 视角右转)—— 鼠标左右移动接这个"
         "(第三人称环绕)"),
        ("gk_yaw_offset", "float", 0.0, "偏航偏移", "相对跟随对象的左右偏(度)"),
        ("gk_collision", "bool", True, "碰撞检测",
         "撞到墙就把杆子缩短(关掉会穿墙,除非你确定场景里没东西)"),
        ("gk_radius", "float", 0.3, "碰撞半径", "留一点余量,免得贴脸"),
        ("gk_lag", "float", 8.0, "位置平滑", "越大越跟手;0 = 硬跟(会有点抖)"),
        ("gk_lag_trans", "enum", "LINEAR", "跟随曲线",
         "位置平滑的速度曲线:线性 = 老行为;回弹档会让相机过冲一点(弹簧感)",
         EASE_ITEMS),
        ("gk_lag_curve", "string", "", "自定义曲线",
         "『跟随曲线』选了自定义时用的那条曲线"),
        ("gk_lag_rot", "float", 0.0, "旋转平滑", "0 = 朝向立刻跟;>0 会有一点拖尾"),
        ("gk_enabled", "bool", True, "启用", "关掉 = 这条杆子不工作"),
    )
    gk_outputs = (
        S.pin(FLOW, "完成"),
        # ── 右侧输出:运行中把这两点读出来 ──────────────────────────
        S.pin(VECTOR, "枢轴", (0.0, 0.0, 0.0)),
        S.pin(VECTOR, "相机位置", (0.0, 0.0, 0.0)),
        S.pin(VECTOR, "方向", (0.0, 0.0, 1.0)),
    )

    def draw_buttons(self, context, layout):
        # ── 节点体上的排布(用户 2026-10-03:"先优化一下现在的节点图")──
        # 以前是三行**没有标注**的数字框 + 一个看不懂的图标按钮,
        # 谁也不知道 4.00 / 1.50 / 0.50 分别是什么。现在每行都带标题,
        # 而且按"先摆杆子、再调手感、最后挂相机"的顺序排。
        row = layout.row(align=True)
        row.prop(self, "gk_arm", text="臂长")
        row.prop(self, "gk_height", text="高度")
        row.prop(self, "gk_side", text="侧向")
        row = layout.row(align=True)
        row.prop(self, "gk_pitch", text="俯仰")
        row.prop(self, "gk_lag", text="平滑")
        row = layout.row(align=True)
        row.prop(self, "gk_collision", text="碰墙缩短", toggle=True)
        row.prop(self, "gk_enabled", text="启用", toggle=True)
        row = layout.row(align=True)
        row.label(text="跟随:", icon="CON_FOLLOWPATH")
        B.draw_target_picker(self, row, "gk_target")

    def draw_buttons_ext(self, context, layout):
        layout.label(text="弹簧臂挂在哪:留空 = 用『跟随谁』", icon="EMPTY_ARROWS")
        B.draw_target_picker(self, layout, "gk_arm_node")
        layout.label(text="相机:留空 = 用场景里的当前相机", icon="CAMERA_DATA")
        B.draw_target_picker(self, layout, "gk_camera")
        layout.label(text="跟随谁:留空 = 脚本所在的那个节点", icon="CON_FOLLOWPATH")
        B.draw_target_picker(self, layout, "gk_target")
        row = layout.row(align=True)
        row.prop(self, "gk_yaw_offset", text="偏航偏移")
        row.prop(self, "gk_lag_rot", text="旋转平滑")
        row = layout.row(align=True)
        row.prop(self, "gk_radius", text="碰撞半径")
        layout.label(text="位置平滑的曲线(线性 = 和以前一样):",
                     icon="IPO_EASE_IN_OUT")
        row = layout.row(align=True)
        row.prop(self, "gk_lag_trans", text="")
        if str(getattr(self, "gk_lag_trans", "")) == "CUSTOM":
            _draw_curve_row(layout, self, "gk_lag_curve", "曲线")


@N
class GKNodeCameraStatus(B.GKExpressionNode):
    bl_idname = "GKNodeCameraStatus"
    bl_label = "相机_取状态"
    bl_icon = "INFO"
    gk_category = "相机"
    gk_tree_type = BOTH_TREES
    gk_doc = ("读当前相机的情况:名字 / 位置 / 朝向(度) / 视野 / 是不是开着。"
              "做逻辑判断用:比如『如果(当前相机 == 特写机位)』才允许开枪")
    gk_outputs = (
        S.pin(TEXT, "相机名", ""),
        S.pin(VECTOR, "位置", (0.0, 0.0, 0.0)),
        S.pin(VECTOR, "朝向", (0.0, 0.0, 0.0)),
        S.pin(FLOAT, "视野", 0.0),
        S.pin(BOOL, "开着吗", False),
    )


@N
class GKNodeCameraPath(B.GKStatementNode):
    bl_idname = "GKNodeCameraPath"
    bl_label = "相机_路径"
    bl_icon = "CURVE_PATH"
    gk_category = "相机"
    gk_doc = ("**让相机沿一条曲线走**(运镜轨道)。"
              "在 Blender 里画一条**曲线**当轨道,点『选曲线』挑它 —— "
              "导出时会把曲线**采样成一条路径**写进脚本(曲线本身不进游戏,"
              "只留形状),相机就沿它移动。"
              "── 两种用法 ──"
              "(1) 『位置』给 0~1:直接摆到轨道上的某一点"
              "(从一处切到另一处 = 把『位置』缓动过去);"
              "(2) 『速度』> 0:自动沿轨道前进(开场运镜 / 过场动画)。"
              "这就是『弹簧臂其实是一条曲线,相机挂在上面,曲线变长变短"
              "相机跟着动』那条路子 —— 直杆用『相机_弹簧臂』,走曲线用这个")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "位置", 0.0),
        S.pin(FLOAT, "速度", 0.0),
    )
    gk_input_props = {"位置": "gk_position", "速度": "gk_speed"}
    gk_prop_defs = (
        ("gk_target", "node", "", "相机", "留空 = 场景里的当前相机"),
        ("gk_curve", "string", "", "曲线",
         "Blender 里的曲线物体(点右边的『选曲线』挑一个)"),
        ("gk_position", "float", 0.0, "位置", "0 = 起点,1 = 终点"),
        ("gk_speed", "float", 0.0, "速度",
         "每秒走轨道的百分之几(0 = 不自动走,只按『位置』摆着)"),
        ("gk_look_mode", "enum", "FORWARD", "朝向", "",
         (("FORWARD", "看向前方(沿轨道方向)", "运镜最常用"),
          ("TARGET", "看向某个节点", "绕着一个东西转圈"),
          ("KEEP", "不改朝向", "自己另外用『相机_旋转』控"))),
        ("gk_look_target", "node", "", "看向谁", "朝向 = 『看向某个节点』时用"),
        ("gk_smooth", "float", 8.0, "平滑", "位置平滑;0 = 硬跟"),
        ("gk_loop", "bool", False, "到终点后从头再来", ""),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_position", text="位置")
        row.prop(self, "gk_speed", text="速度")
        row.prop(self, "gk_loop", text="循环", toggle=True)
        row = layout.row(align=True)
        row.prop(self, "gk_look_mode", text="")
        B.draw_target_picker(self, row)
        _draw_curve_row(layout, self, "gk_curve", "", preview=True)


def _draw_curve_row(layout, node, prop, label, preview=False):
    """一行:曲线名 + 『选曲线』+ (可选)『预览运镜』。

    三个节点都有"填一条曲线"的栏位(相机_切换 的过渡/轨道、相机_路径、
    时间轴),画法统一 —— 免得三处各画一套、按钮位置还不一样。
    """
    row = layout.row(align=True)
    row.prop(node, prop, text=label, icon="CURVE_DATA")
    pick = row.operator("gk.pick_curve", text="", icon="VIEWZOOM")
    pick.node_name = node.name
    pick.curve_prop = prop
    if preview:
        play = row.operator("gk.preview_camera_path", text="", icon="PLAY")
        play.node_name = node.name


@N
class GKNodeCameraSetCurrent(B.GKStatementNode):
    bl_idname = "GKNodeCameraSetCurrent"
    bl_label = "相机_切换"
    bl_icon = "CAMERA_DATA"
    gk_category = "相机"
    gk_doc = ("把某个相机设为当前相机(2D / 3D 通用)——做镜头切换、Boss 特写用。"
              "『过渡(秒)』填了就是**丝滑推镜**:新相机从旧相机的视角平滑"
              "推到它自己的位置,而不是硬切。"
              "做法:让新相机先站到旧相机的位姿上,再插值回它自己 —— "
              "两台相机都不用被挪走,来回切也不会跑位。"
              "── 第十二轮加了两栏(想要曲线运动,不用再连一堆节点)──"
              "『过渡曲线』:过渡的速度曲线(线性 / 缓入 / 缓出 / 缓入缓出 / "
              "回弹 / 自己画一条)。点『预览运镜』可以在 Blender 里先看一遍。"
              "『走法 ▸ 沿曲线运镜』:过渡期间镜头不在直线上走,而是沿着"
              "场景里画的一条曲线飞过去(导出成 Godot 的 Curve3D 资源)")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_target", "node", "", "相机", "要激活的 Camera2D / Camera3D"),
        ("gk_blend", "float", 0.0, "过渡(秒)",
         "0 = 立刻切;填 0.5 就是半秒推过去"),
        ("gk_blend_trans", "enum", "LINEAR", "过渡曲线",
         "过渡的速度曲线(照虚幻的 Set View Target with Blend)", EASE_ITEMS),
        ("gk_blend_curve", "string", "", "自定义曲线",
         "『过渡曲线』选了自定义时用的那条曲线"),
        ("gk_path_mode", "enum", "DIRECT", "走法", "",
         CAMERA_PATH_MODES),
        ("gk_path_curve", "string", "", "运镜轨道",
         "『走法』选了沿曲线运镜时用的 3D 曲线(场景里画一条)"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        B.draw_target_picker(self, row)
        row.prop(self, "gk_blend", text="过渡")
        row = layout.row(align=True)
        row.prop(self, "gk_blend_trans", text="")
        if str(getattr(self, "gk_blend_trans", "")) == "CUSTOM":
            _draw_curve_row(layout, self, "gk_blend_curve", "曲线", preview=True)
        row = layout.row(align=True)
        row.prop(self, "gk_path_mode", text="")
        if str(getattr(self, "gk_path_mode", "DIRECT")) == "CURVE":
            _draw_curve_row(layout, self, "gk_path_curve", "轨道", preview=True)


@N
class GKNodeCameraZoom(B.GKStatementNode):
    bl_idname = "GKNodeCameraZoom"
    bl_label = "相机_缩放"
    bl_icon = "ZOOM_IN"
    gk_category = "相机"
    gk_doc = ("设置相机缩放:2D 用 zoom,3D 用视场角 fov(自动按相机类型选)。"
              "『时长』填了就是**平滑变化**(视野慢慢推近 / 拉远);"
              "默认 0 = 立刻变 —— 这样**老图生成的结果一字不变**")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "数值", 1.0),)
    gk_input_props = {"数值": "gk_value"}
    gk_prop_defs = (
        ("gk_value", "float", 1.0, "数值", "2D: zoom 越大画面越近;3D: 垂直视场角(度)"),
        ("gk_time", "float", 0.0, "时长", "秒;0 = 立刻变(默认,和以前一样)"),
        # ── 范围限制(用户 2026-10-03:"可以无限拉长,应该添加自定义范围")──
        # 滚轮缩放那条链是"当前视野 + 滚轮量",没有上下限就会一路推下去:
        # 视野拉到 170 度画面变形、拉到负数直接废掉。这里给一对上下限,
        # **0 = 不限制**(老图生成的结果一字不变)。
        ("gk_min_value", "float", 0.0, "最小",
         "视野/缩放的下限;0 = 不限制(推荐:3D 视野填 20)"),
        ("gk_max_value", "float", 0.0, "最大",
         "视野/缩放的上限;0 = 不限制(推荐:3D 视野填 90)"),
        ("gk_target", "node", "", "相机", "留空则用当前相机"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_value", text="")
        row.prop(self, "gk_time", text="秒")
        row = layout.row(align=True)
        row.prop(self, "gk_min_value", text="最小")
        row.prop(self, "gk_max_value", text="最大")
        B.draw_target_picker(self, layout)


@N
class GKNodeCameraShake(B.GKStatementNode):
    bl_idname = "GKNodeCameraShake"
    bl_label = "相机_震动"
    bl_icon = "RNDCURVE"
    gk_category = "相机"
    gk_doc = ("让相机抖动一下(受击 / 爆炸反馈)。用相机的偏移量实现，"
              "不会破坏相机原本的位置——所以可以和『跟随』同时用")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "强度", 8.0),
        S.pin(FLOAT, "持续", 0.25),
    )
    gk_input_props = {"强度": "gk_strength", "持续": "gk_duration"}
    gk_prop_defs = (
        ("gk_strength", "float", 8.0, "强度", "最大偏移像素"),
        ("gk_duration", "float", 0.25, "持续", "震动时长(秒)"),
        ("gk_target", "node", "", "相机", "留空则用 $Camera2D / $Camera3D"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_strength", text="")
        row.prop(self, "gk_duration", text="")
        B.draw_target_picker(self, layout)


@N
class GKNodeCameraFollow(B.GKStatementNode):
    bl_idname = "GKNodeCameraFollow"
    bl_label = "相机_跟随"
    bl_icon = "CON_FOLLOWPATH"
    gk_category = "相机"
    gk_doc = ("让相机跟随某个节点(2D / 3D 通用)。"
              "平台跳跃游戏一定要放这个 —— 不放的话角色一跑远就出画面了")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "平滑", 0.12),
    )
    gk_input_props = {"平滑": "gk_smooth"}
    gk_prop_defs = (
        ("gk_smooth", "float", 0.12, "平滑",
         "0 = 硬跟随(相机与角色完全同步)；越大越拖，0.1~0.2 手感较好"),
        ("gk_target", "node", TARGET_SELF, "跟随谁", "要跟随的节点，默认自身"),
        ("gk_camera", "node", "", "相机", "留空则自动找场景里第一个相机"),
        ("gk_offset_x", "float", 0.0, "偏移 X", "瞄准角色上方/下方时的偏移"),
        ("gk_offset_y", "float", 0.0, "偏移 Y", ""),
        ("gk_distance", "float", 0.0, "3D 距离",
         "3D 专用:留 0 = 保持相机一开始相对角色的位置(推荐,"
         "在 Blender 里摆好相机角度即可);填了就固定站在角色身后这个距离"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_smooth", text="")
        row.prop(self, "gk_offset_y", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_camera", text="")
        B.draw_target_picker(self, row, "gk_camera")

    def draw_buttons_ext(self, context, layout):
        layout.label(text="相机:留空 = 自动找场景里第一个相机(推荐)")
        B.draw_target_picker(self, layout, "gk_camera")
        layout.label(text="跟随谁:留空 = 跟随脚本所在的那个节点")
        B.draw_target_picker(self, layout, "gk_target")


# ==========================================================================
# 骨骼(照 UPBGE 的『动画 ▸ Get/Set Bone Data』那一栏)
# ==========================================================================
# ── 为什么要加这一栏(用户 2026-10-03 第四轮)──────────────────────
# 用户贴了 UPBGE 的截图:"相机与动画的节点都可以整合成类似 upbge 一样" ——
# UPBGE 的『动画』下面除了播放/停止,还有
#     Get Bone Data ▸(Get Bone Name / Location / Euler Rotation / Head / …)
#     Set Bone Data ▸(Set Bone Location / Euler Rotation / Scale / …)
# 我们一个都没有。而有骨骼的模型(用户的胡桃就是 MMD 骨架)最常见的需求
# 恰恰是"让某根骨头动一下":头跟着视角转、手举武器、尾巴甩。
#
# Godot 那边对应 Skeleton3D 的 `get/set_bone_pose_*`(局部姿势,相对父骨),
# 以及 `get_bone_global_pose`(世界姿势,算枪口位置要用)。
BONE_HELP = ("填骨骼名(和 Godot 里 Skeleton3D 的骨头名一致)。"
             "Blender 里的骨头名导出后一般不变;拿不准就在 Godot 里"
             "点开模型看一眼")


@N
class GKNodeGetBone(B.GKExpressionNode):
    bl_idname = "GKNodeGetBone"
    bl_label = "骨骼_取数据"
    bl_icon = "BONE_DATA"
    gk_category = "动画"
    gk_doc = ("读一根骨头的姿势:位置 / 旋转(度) / 缩放,"
              "还能算它的**世界位置**(比如枪口在哪 —— 生成特效要用)。"
              "照 UPBGE 的 Get Bone Data 那一栏做的")
    gk_prop_defs = (
        ("gk_bone", "string", "head", "骨骼名", BONE_HELP),
        ("gk_skeleton_node", "node", "", "骨架节点",
         "留空 = 从脚本所在节点往下自动找 Skeleton3D"),
    )
    gk_outputs = (
        S.pin(VECTOR, "位置", (0.0, 0.0, 0.0)),
        S.pin(VECTOR, "旋转", (0.0, 0.0, 0.0)),
        S.pin(VECTOR, "缩放", (1.0, 1.0, 1.0)),
        S.pin(VECTOR, "世界位置", (0.0, 0.0, 0.0)),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_bone", text="", icon="BONE_DATA")
        B.draw_target_picker(self, layout, "gk_skeleton_node")


@N
class GKNodeSetBone(B.GKStatementNode):
    bl_idname = "GKNodeSetBone"
    bl_label = "骨骼_设置"
    bl_icon = "BONE_DATA"
    gk_category = "动画"
    gk_doc = ("摆一根骨头的姿势(照 UPBGE 的 Set Bone Data)。"
              "『保留』= 在现在的姿势上**加**(甩头、点头用这个);"
              "『直接设』= 换成给的姿势。"
              "注意是**相对父骨**的姿势 —— 和 Godot 里的 bone pose 一致")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(VECTOR, "旋转", (0.0, 0.0, 0.0)),
        S.pin(VECTOR, "位置", (0.0, 0.0, 0.0)),
        S.pin(VECTOR, "缩放", (1.0, 1.0, 1.0)),
    )
    gk_prop_defs = (
        ("gk_mode", "enum", "ADD", "怎么用", "",
         (("ADD", "保留(在现在的姿势上加)", "相对 —— 点个头、甩个头"),
          ("SET", "直接设(换成这个姿势)", "绝对"))),
        ("gk_bone", "string", "head", "骨骼名", BONE_HELP),
        ("gk_skeleton_node", "node", "", "骨架节点", "留空 = 自动找"),
        ("gk_do_rot", "bool", True, "改旋转", ""),
        ("gk_do_pos", "bool", False, "改位置", ""),
        ("gk_do_scale", "bool", False, "改缩放", ""),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_mode", text="")
        row.prop(self, "gk_bone", text="", icon="BONE_DATA")
        row = layout.row(align=True)
        row.prop(self, "gk_do_rot", text="旋转", toggle=True)
        row.prop(self, "gk_do_pos", text="位置", toggle=True)
        row.prop(self, "gk_do_scale", text="缩放", toggle=True)
        B.draw_target_picker(self, layout, "gk_skeleton_node")


@N
class GKNodeBoneLookAtCamera(B.GKStatementNode):
    bl_idname = "GKNodeBoneLookAtCamera"
    bl_label = "骨骼_跟随视角"
    bl_icon = "CAMERA_DATA"
    gk_category = "动画"
    gk_doc = ("**让一根骨头跟着相机转** —— 回答第四轮那个问题"
              "『第一人称转视角时能不能让角色一部分模型跟着转』:**能**。"
              "典型用法:头 / 上半身跟着镜头偏,但只跟一部分"
              "(权重 0.3~0.6)、并且限制角度(脖子不能拧 180°)。"
              "每物理帧更新,和相机同一个时钟")
    gk_inputs = (
        S.pin(FLOW, "执行"),
    )
    gk_prop_defs = (
        ("gk_bone", "string", "head", "骨骼名", BONE_HELP),
        ("gk_skeleton_node", "node", "", "骨架节点", "留空 = 自动找"),
        ("gk_camera", "node", "", "相机", "留空 = 当前相机"),
        ("gk_yaw_weight", "float", 0.5, "左右权重",
         "跟着转多少:0 = 不跟,1 = 完全跟。0.3~0.6 看着最自然"),
        ("gk_pitch_weight", "float", 0.35, "上下权重", "同上(上下跟得少一点更像人)"),
        ("gk_limit", "float", 60.0, "最大角度",
         "最多偏多少度(超过就不再跟了,免得脖子拧断)"),
        ("gk_smooth", "float", 8.0, "平滑", "越大越跟手;0 = 硬跟"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_bone", text="", icon="BONE_DATA")
        row.prop(self, "gk_yaw_weight", text="左右")
        row.prop(self, "gk_pitch_weight", text="上下")
        B.draw_target_picker(self, layout, "gk_skeleton_node")

    def draw_buttons_ext(self, context, layout):
        layout.label(text="相机:留空 = 用当前相机")
        B.draw_target_picker(self, layout, "gk_camera")
        layout.label(text="骨架:留空 = 从脚本节点往下自动找")
        B.draw_target_picker(self, layout, "gk_skeleton_node")


# ==========================================================================
# 时间
# ==========================================================================
@N
class GKNodeSetTimeScale(B.GKStatementNode):
    bl_idname = "GKNodeSetTimeScale"
    bl_label = "时间_设置倍速"
    bl_icon = "TIME"
    gk_category = "时间"
    gk_doc = ("设置全局时间倍速(对应 Engine.time_scale):0.3 = 子弹时间，"
              "1.0 = 正常，0 = 完全暂停")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "倍速", 1.0),)
    gk_input_props = {"倍速": "gk_scale"}
    gk_prop_defs = (("gk_scale", "float", 1.0, "倍速", "1.0 为正常速度"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_scale", text="")


# ==========================================================================
# 音效
# ==========================================================================
@N
class GKNodePlaySound(B.GKStatementNode):
    bl_idname = "GKNodePlaySound"
    bl_label = "音效_播放"
    bl_icon = "SOUND"
    gk_category = "音效"
    gk_doc = ("播放一次音效(跳跃 / 受击 / 拾取)。播放器是运行时自动建的，"
              "播完自动销毁，不需要你在场景里摆节点")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "音频路径", "res://art/jump.wav"),
        S.pin(FLOAT, "音量", 0.0),
        S.pin(FLOAT, "音高", 1.0),
    )
    gk_input_props = {
        "音频路径": "gk_path",
        "音量": "gk_volume",
        "音高": "gk_pitch",
    }
    gk_prop_defs = (
        ("gk_path", "string", "res://art/jump.wav", "音频路径", "res:// 开头的音频文件"),
        ("gk_volume", "float", 0.0, "音量", "分贝，0 为原始音量，-6 约减半"),
        ("gk_pitch", "float", 1.0, "音高", "1.0 原速，>1 更尖更快"),
        ("gk_2d", "bool", True, "2D 音效", "勾上=按距离衰减(3D 项目取消勾选)"),
        ("gk_target", "node", "", "挂到", "留空则挂到当前脚本所在节点"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_path", text="")
        # 从资产库挑(第四十三轮):挑中的是『资产_声音卡』,导出时
        # 那个文件会被复制进 audio/ —— 不用自己拷、也不会手敲错路径
        pick = row.operator("gk.pick_sound", text="", icon="SOUND")
        pick.node_name = self.name
        row = layout.row(align=True)
        row.prop(self, "gk_volume", text="")
        row.prop(self, "gk_pitch", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_2d", text="2D", toggle=True)
        B.draw_target_picker(self, layout)


@N
class GKNodePlayMusic(B.GKStatementNode):
    bl_idname = "GKNodePlayMusic"
    bl_label = "音效_音乐"
    bl_icon = "PLAY_SOUND"
    gk_category = "音效"
    gk_doc = "播放背景音乐(BGM)。重复调用会切换曲目并做淡入淡出，不会叠着播"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "音频路径", "res://art/bgm.ogg"),
        S.pin(FLOAT, "音量", -6.0),
    )
    gk_input_props = {"音频路径": "gk_path", "音量": "gk_volume"}
    gk_prop_defs = (
        ("gk_path", "string", "res://art/bgm.ogg", "音频路径", "res:// 开头的音频文件"),
        ("gk_volume", "float", -6.0, "音量", "分贝，0 为原始音量"),
        ("gk_fade", "float", 0.5, "淡入秒数", "0 表示直接开始"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_path", text="")
        # 从资产库挑(第四十三轮,同『音效_播放』)
        pick = row.operator("gk.pick_sound", text="", icon="SOUND")
        pick.node_name = self.name
        row = layout.row(align=True)
        row.prop(self, "gk_volume", text="")
        row.prop(self, "gk_fade", text="")


@N
class GKNodeStopSound(B.GKStatementNode):
    bl_idname = "GKNodeStopSound"
    bl_label = "音效_停止音乐"
    bl_icon = "MUTE_IPO_OFF"
    gk_category = "音效"
    gk_doc = "停止背景音乐(可做淡出)。音效是一次性的，播完自己就没了，不用手动停"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "淡出秒数", 0.5),)
    gk_input_props = {"淡出秒数": "gk_fade"}
    gk_prop_defs = (("gk_fade", "float", 0.5, "淡出秒数", "0 表示立刻停"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_fade", text="")


# ==========================================================================
# 存档
# ==========================================================================
@N
class GKNodeSaveSet(B.GKStatementNode):
    bl_idname = "GKNodeSaveSet"
    bl_label = "存档_写入"
    bl_icon = "IMPORT"
    gk_category = "存档"
    gk_doc = ("往存档里写一个值(血量 / 关卡 / 金币)。只改内存，"
              "要真正落盘请接『存档_保存』")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "键名", "hp"),
        S.pin(ANY, "值", 3),
    )
    gk_input_props = {"键名": "gk_key"}
    gk_prop_defs = (("gk_key", "string", "hp", "键名", "存档里这一项的键"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_key", text="")


@N
class GKNodeSaveGet(B.GKStatementNode):
    bl_idname = "GKNodeSaveGet"
    bl_label = "存档_读取到变量"
    bl_icon = "EXPORT"
    gk_category = "存档"
    gk_doc = ("把存档里的值读进一个脚本变量，然后用『变量_读取』节点取用。"
              "键不存在时用『默认值』")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "键名", "hp"),
    )
    gk_input_props = {"键名": "gk_key"}
    gk_prop_defs = (
        ("gk_key", "string", "hp", "键名", "存档里这一项的键"),
        ("gk_var", "string", "hp", "写入变量", "读出来的值放进哪个脚本变量"),
        ("gk_default", "float", 0.0, "默认值", "键不存在时用它"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_key", text="")
        row.prop(self, "gk_var", text="")
        layout.prop(self, "gk_default", text="")


@N
class GKNodeSaveWrite(B.GKStatementNode):
    bl_idname = "GKNodeSaveWrite"
    bl_label = "存档_保存"
    bl_icon = "FILE_TICK"
    gk_category = "存档"
    gk_doc = "把内存里的存档数据写进磁盘文件(user:// 目录下)"
    gk_inputs = (S.pin(FLOW, "执行"),)

    gk_prop_defs = (
        ("gk_slot", "string", "save1", "存档槽", "不同槽位互不影响，做多存档用"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_slot", text="")


@N
class GKNodeSaveLoad(B.GKStatementNode):
    bl_idname = "GKNodeSaveLoad"
    bl_label = "存档_载入"
    bl_icon = "FILE_FOLDER"
    gk_category = "存档"
    gk_doc = "从磁盘把存档读进内存。文件不存在时不会出错，只是读到空存档"
    gk_inputs = (S.pin(FLOW, "执行"),)

    gk_prop_defs = (
        ("gk_slot", "string", "save1", "存档槽", "要和『存档_保存』用同一个槽位"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_slot", text="")


@N
class GKNodeSaveHas(B.GKStatementNode):
    bl_idname = "GKNodeSaveHas"
    bl_label = "存档_是否存在"
    bl_icon = "QUESTION"
    gk_category = "存档"
    gk_doc = "判断某个槽位有没有存档文件(做『继续游戏』按钮的可用状态)"
    gk_inputs = (S.pin(FLOW, "执行"),)

    gk_prop_defs = (
        ("gk_slot", "string", "save1", "存档槽", "要检查的槽位"),
    )
    # 出口故意不叫『完成』:见 codegen 里 _stmt_save_has 的说明 ——
    # emit_statement 会自动续接『完成』，那样 else 分支会跑两遍。
    gk_outputs = (S.pin(FLOW, "有档"), S.pin(FLOW, "没档"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_slot", text="")


@N
class GKNodeSaveDelete(B.GKStatementNode):
    bl_idname = "GKNodeSaveDelete"
    bl_label = "存档_删除"
    bl_icon = "TRASH"
    gk_category = "存档"
    gk_doc = "删除某个槽位的存档文件(删档 / 重新开始)"
    gk_inputs = (S.pin(FLOW, "执行"),)

    gk_prop_defs = (
        ("gk_slot", "string", "save1", "存档槽", "要删除的槽位"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_slot", text="")


# ==========================================================================
# 屏幕效果
# ==========================================================================
@N
class GKNodeScreenFlash(B.GKStatementNode):
    bl_idname = "GKNodeScreenFlash"
    bl_label = "屏幕_闪白"
    bl_icon = "IMAGE_ZDEPTH"
    gk_category = "屏幕效果"
    gk_doc = ("整屏闪一下(受击 / 爆炸 / 治疗)。用 ColorRect + Tween 做，"
              "不占渲染管线，也不需要着色器")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(COLOR, "颜色", (1.0, 1.0, 1.0, 1.0)),
        S.pin(FLOAT, "时长", 0.3),
    )
    gk_input_props = {"时长": "gk_time"}
    gk_prop_defs = (
        ("gk_color", "color", (1.0, 1.0, 1.0, 1.0), "颜色", "闪光颜色"),
        ("gk_time", "float", 0.3, "时长", "淡出到透明所需秒数"),
        ("gk_alpha", "float", 0.6, "起始透明度", "1 为全不透明"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_color", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_time", text="")
        row.prop(self, "gk_alpha", text="")


@N
class GKNodeScreenFade(B.GKStatementNode):
    bl_idname = "GKNodeScreenFade"
    bl_label = "屏幕_渐变遮罩"
    bl_icon = "IMAGE_ALPHA"
    gk_category = "屏幕效果"
    gk_doc = ("整屏渐变到某个颜色(黑场转场 / 淡入开场)。"
              "『到不透明』用于转场，『到透明』用于淡回游戏")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "时长", 1.0),
    )
    gk_input_props = {"时长": "gk_time"}
    gk_prop_defs = (
        ("gk_color", "color", (0.0, 0.0, 0.0, 1.0), "颜色", "遮罩颜色，默认黑"),
        ("gk_time", "float", 1.0, "时长", "渐变秒数"),
        ("gk_to_opaque", "bool", True, "渐变到不透明", "取消勾选=淡回透明"),
        ("gk_hold", "float", 0.0, "保持秒数", "转场时全黑停留多久(0 = 一直留着)"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_color", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_time", text="")
        row.prop(self, "gk_hold", text="")
        layout.prop(self, "gk_to_opaque", text="渐变到不透明", toggle=True)


# ==========================================================================
# 界面
# ==========================================================================
# ── 『对齐』:九宫格(第三十七轮,用户 2026-10-06:"进行 UI 的设计")────
# 以前所有控件都是**左上角绝对像素**(X / Y),窗口一变(全屏 / 换分辨率)
# 控件就跑到别人家去了。这一栏是屏幕对齐:控件跟着屏幕的角 / 边 / 中心走。
#
# 默认还是『左上(填 X / Y)』—— 老图生成的结果**一字不变**;
# 选了对齐之后 X / Y 就不用了,改用『边距』(贴着屏幕边留多少像素)。
#
# ⚠ 枚举 id 必须和 codegen/services.py 的 `UI_ALIGN_PRESETS` 对得上
#   (对不上会静默退回左上角)。
UI_ALIGN_ITEMS = (
    ("TOP_LEFT", "左上(按 X / Y 摆)", "老行为:填 X / Y 的绝对像素,窗口变了不跟着走"),
    ("TOP_CENTER", "顶部居中", "横向居中、贴着上边"),
    ("TOP_RIGHT", "右上", "贴右上角"),
    ("CENTER_LEFT", "左侧居中", "贴着左边、纵向居中"),
    ("CENTER", "正中", "屏幕正中央"),
    ("CENTER_RIGHT", "右侧居中", "贴着右边、纵向居中"),
    ("BOTTOM_LEFT", "左下", "贴左下角"),
    ("BOTTOM_CENTER", "底部居中", "横向居中、贴着下边(对话框 / 字幕常用)"),
    ("BOTTOM_RIGHT", "右下", "贴右下角"),
)
UI_ALIGN_HELP = "控件挂在屏幕的哪个位置;选了之后 X / Y 就不用了,用『边距』"


@N
class GKNodeUiText(B.GKStatementNode):
    bl_idname = "GKNodeUiText"
    bl_label = "界面_文本"
    bl_icon = "FONT_DATA"
    gk_category = "界面"
    gk_doc = ("在屏幕上显示一行字(分数 / 提示 / 对话)。UI 层是自动建的，"
              "不需要你在场景里摆控件；重复调用同一个节点是改文字，不会叠出新的一行。"
              "『对齐』可以把它挂到屏幕的角 / 边 / 正中上 —— 全屏也不跑偏")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "文字", "分数: 0"),
        S.pin(FLOAT, "X", 20.0),
        S.pin(FLOAT, "Y", 20.0),
    )
    gk_input_props = {"文字": "gk_text", "X": "gk_x", "Y": "gk_y"}
    gk_prop_defs = (
        ("gk_text", "string", "分数: 0", "文字", "未连线时显示的内容"),
        ("gk_x", "float", 20.0, "X", "屏幕坐标(左上角为原点)；对齐 = 左上时才用它"),
        ("gk_y", "float", 20.0, "Y", "屏幕坐标"),
        ("gk_size", "int", 24, "字号", "像素"),
        ("gk_color", "color", (1.0, 1.0, 1.0, 1.0), "颜色", "文字颜色"),
        ("gk_align", "enum", "TOP_LEFT", "对齐", UI_ALIGN_HELP, UI_ALIGN_ITEMS),
        ("gk_margin", "float", 24.0, "边距", "对齐到屏幕边 / 角时留多少像素"),
        ("gk_parent_widget", "string", "", "父控件",
         "挂到哪个界面控件下面(填那个控件的名字)。留空 = 直接铺在屏幕上;"
         "挂到面板下面时,面板一藏它就跟着藏,对齐也变成相对面板"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_text", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_size", text="")
        row.prop(self, "gk_color", text="")
        row.prop(self, "gk_parent_widget", text="", icon="NODE")
        B.draw_ui_align_row(self, layout)


@N
class GKNodeUiBar(B.GKStatementNode):
    bl_idname = "GKNodeUiBar"
    bl_label = "界面_进度条"
    bl_icon = "COLOR"
    gk_category = "界面"
    gk_doc = "在屏幕上显示一条进度条(血条 / 蓝条 / 蓄力条)。数值会自动钳在 0~最大值"
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "数值", 100.0),
        S.pin(FLOAT, "X", 20.0),
        S.pin(FLOAT, "Y", 60.0),
    )
    gk_input_props = {"数值": "gk_value", "X": "gk_x", "Y": "gk_y"}
    gk_prop_defs = (
        ("gk_value", "float", 100.0, "数值", "当前值"),
        ("gk_max", "float", 100.0, "最大值", "满值"),
        ("gk_x", "float", 20.0, "X", "屏幕坐标"),
        ("gk_y", "float", 60.0, "Y", "屏幕坐标"),
        ("gk_width", "float", 200.0, "宽", "像素"),
        ("gk_height", "float", 16.0, "高", "像素"),
        ("gk_color", "color", (0.85, 0.25, 0.25, 1.0), "颜色", "填充色"),
        ("gk_align", "enum", "TOP_LEFT", "对齐", UI_ALIGN_HELP, UI_ALIGN_ITEMS),
        ("gk_margin", "float", 24.0, "边距", "对齐到屏幕边 / 角时留多少像素"),
        ("gk_parent_widget", "string", "", "父控件",
         "挂到哪个界面控件下面(填那个控件的名字)。留空 = 直接铺在屏幕上;"
         "挂到面板下面时,面板一藏它就跟着藏,对齐也变成相对面板"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_value", text="")
        row.prop(self, "gk_max", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_width", text="")
        row.prop(self, "gk_height", text="")
        row.prop(self, "gk_color", text="")
        row.prop(self, "gk_parent_widget", text="", icon="NODE")
        B.draw_ui_align_row(self, layout)


@N
class GKNodeUiButton(B.GKStatementNode):
    bl_idname = "GKNodeUiButton"
    bl_label = "界面_按钮"
    bl_icon = "PLAY"
    gk_category = "界面"
    gk_doc = ("在屏幕上放一个按钮，点击后沿『点击』出口继续。"
              "按钮是持久的(不会点一下就消失)，重复调用只改文字。"
              "『对齐』= 正中 + 『边距』0 就是屏幕正中央的大按钮(主菜单常用);"
              "暂停菜单里的『继续 / 重开 / 退出』也是它")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "文字", "开始游戏"),
        S.pin(FLOAT, "X", 100.0),
        S.pin(FLOAT, "Y", 200.0),
    )
    gk_input_props = {"文字": "gk_text", "X": "gk_x", "Y": "gk_y"}
    gk_prop_defs = (
        ("gk_text", "string", "开始游戏", "文字", "按钮上显示的字"),
        ("gk_x", "float", 100.0, "X", "屏幕坐标；对齐 = 左上时才用它"),
        ("gk_y", "float", 200.0, "Y", "屏幕坐标"),
        ("gk_width", "float", 160.0, "宽", "像素"),
        ("gk_height", "float", 48.0, "高", "像素"),
        ("gk_align", "enum", "TOP_LEFT", "对齐", UI_ALIGN_HELP, UI_ALIGN_ITEMS),
        ("gk_margin", "float", 24.0, "边距", "对齐到屏幕边 / 角时留多少像素"),
        ("gk_parent_widget", "string", "", "父控件",
         "挂到哪个界面控件下面(填那个控件的名字)。留空 = 直接铺在屏幕上;"
         "挂到面板下面时,面板一藏它就跟着藏,对齐也变成相对面板"),
    )
    # ── 两个出口,各管一段(第三十七轮补的 『完成』)─────────────────
    # 『完成』= **按钮建好了**,接着往下建 / 往下做(和别的语句节点一样);
    # 『点击』= **玩家点了**才走的那条链(放进独立回调函数)。
    #
    # ⚠ 为什么最初只有『点击』:那时 emit_statement 会自动续接名叫『完成』
    #   的出口,而按旧语义『完成』= "点了" —— 于是按钮刚建出来后面的逻辑
    #   就直接跑了(比如游戏立刻开始)。现在两个出口分开:
    #   建按钮 -> 『完成』继续建下一个(菜单要连着建三个按钮就是靠它);
    #   点击 -> 『点击』走回调。老图接在『点击』上的线完全不受影响。
    gk_outputs = (S.pin(FLOW, "完成"), S.pin(FLOW, "点击"),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_text", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_width", text="")
        row.prop(self, "gk_height", text="")
        row.prop(self, "gk_parent_widget", text="", icon="NODE")
        B.draw_ui_align_row(self, layout)


@N
class GKNodeUiHide(B.GKStatementNode):
    bl_idname = "GKNodeUiHide"
    bl_label = "界面_隐藏控件"
    bl_icon = "HIDE_ON"
    gk_category = "界面"
    gk_doc = "隐藏某个界面控件(比如捡到道具后让提示消失)。控件名就是创建它的节点名"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(TEXT, "控件名", "提示"),)
    gk_input_props = {"控件名": "gk_widget"}
    gk_prop_defs = (
        ("gk_widget", "string", "提示", "控件名", "要隐藏的界面节点名"),
        ("gk_visible", "bool", False, "显示", "勾上=显示，取消=隐藏"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_widget", text="")
        row.prop(self, "gk_visible", text="", toggle=True)


@N
class GKNodeFloatText(B.GKStatementNode):
    bl_idname = "GKNodeFloatText"
    bl_label = "界面_飘字"
    bl_icon = "FONT_DATA"
    gk_category = "界面"
    gk_doc = ("在『位置』那个物体的**头顶**冒一行字,往上飘一段 + 淡出后消失 —— "
              "吃金币 \"+1\"、受伤 \"-1\"、伤害数字都用它。"
              "生成的是一次性标签(Label3D / 2D 的 Label),不占界面容器,"
              "飘完自己删掉。\n"
              "── 『数值』怎么变成文字 ──\n"
              "走『文本_数字』那套规则:整数不显示 .0(飘的是 \"+1\" 不是 \"+1.0\");"
              "想飘别的数字(伤害 25)就把『数值』接到变量/运算上。\n"
              "── 和『界面_文本』的区别 ──\n"
              "『界面_文本』是**固定 HUD**(屏幕角落、一直在);"
              "这个是**世界里的一次性提示**(贴着物体、飘完就没)。")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "数值", 1.0),)
    gk_input_props = {"数值": "gk_value"}
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "位置", "字从谁身上冒出来"),
        ("gk_prefix", "string", "+", "前缀", "数字前面的字:+/-,也可以写『伤害 』这种"),
        ("gk_value", "float", 1.0, "数值", "未连线时飘这个数(整数不带 .0)"),
        ("gk_color", "color", (1.0, 0.9, 0.35, 1.0), "颜色", "文字颜色"),
        ("gk_time", "float", 0.8, "秒", "飘多久后消失"),
        ("gk_rise", "float", 1.2, "上飘", "往上飘多远(3D 是米;2D 是像素,填 40~80)"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        B.draw_target_picker(self, row, "gk_target")
        row.prop(self, "gk_prefix", text="")
        row.prop(self, "gk_value", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_time", text="秒")
        row.prop(self, "gk_rise", text="上飘")
        layout.prop(self, "gk_color", text="")


@N
class GKNodeUiImage(B.GKStatementNode):
    bl_idname = "GKNodeUiImage"
    bl_label = "界面_图片"
    bl_icon = "IMAGE_DATA"
    gk_category = "界面"
    gk_doc = ("在屏幕上放一张图片(图标 / 立绘 / logo / 按键提示)。"
              "点『选图片』从 Blender 里的图片挑一张 —— 导出时会把它存成 "
              "art/xxx.png 一起带进 Godot(不用你自己拷文件)。"
              "宽 / 高填 0 = 用原图尺寸;勾着『比例』就按原图比例缩放(不变形)")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "图片", ""),
    )
    gk_input_props = {"图片": "gk_image"}
    gk_prop_defs = (
        ("gk_image", "string", "", "图片",
         "Blender 里的图片名(点右边『选图片』挑),或 res:// 路径"),
        ("gk_x", "float", 20.0, "X", "屏幕坐标；对齐 = 左上时才用它"),
        ("gk_y", "float", 20.0, "Y", "屏幕坐标"),
        ("gk_width", "float", 0.0, "宽", "像素；0 = 用原图宽度"),
        ("gk_height", "float", 0.0, "高", "像素；0 = 用原图高度"),
        ("gk_keep", "bool", True, "保持比例",
         "勾上 = 按原图比例缩放(只填宽 / 只填高时另一项自动算)"),
        ("gk_tint", "color", (1.0, 1.0, 1.0, 1.0), "着色",
         "给图乘一个颜色(白 = 原色);第四位是透明度"),
        ("gk_align", "enum", "TOP_LEFT", "对齐", UI_ALIGN_HELP, UI_ALIGN_ITEMS),
        ("gk_margin", "float", 24.0, "边距", "对齐到屏幕边 / 角时留多少像素"),
        ("gk_parent_widget", "string", "", "父控件",
         "挂到哪个界面控件下面(填那个控件的名字)。留空 = 直接铺在屏幕上;"
         "挂到面板下面时,面板一藏它就跟着藏,对齐也变成相对面板"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_image", text="", icon="IMAGE_DATA")
        pick = row.operator("gk.pick_image", text="", icon="VIEWZOOM")
        pick.node_name = self.name
        pick.image_prop = "gk_image"
        row = layout.row(align=True)
        row.prop(self, "gk_width", text="宽")
        row.prop(self, "gk_height", text="高")
        row.prop(self, "gk_keep", text="比例", toggle=True)
        row = layout.row(align=True)
        row.prop(self, "gk_tint", text="")
        row.prop(self, "gk_parent_widget", text="", icon="NODE")
        B.draw_ui_align_row(self, layout)


@N
class GKNodeUiPanel(B.GKStatementNode):
    bl_idname = "GKNodeUiPanel"
    bl_label = "界面_面板"
    bl_icon = "MOD_OPACITY"
    gk_category = "界面"
    gk_doc = ("一块**半透明底**(菜单背景 / 提示条 / 血条底槽)。"
              "颜色第四位是透明度，『圆角』0 = 直角。"
              "『垫底』勾着时它永远铺在别的控件下面 —— 先加面板、"
              "后加按钮也不会把按钮盖住(菜单都这么搭)")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_color", "color", (0.06, 0.07, 0.10, 0.72), "颜色",
         "底色；第四位是透明度(0.7 左右做菜单底刚好)"),
        ("gk_radius", "int", 12, "圆角", "像素；0 = 直角"),
        ("gk_x", "float", 20.0, "X", "屏幕坐标；对齐 = 左上时才用它"),
        ("gk_y", "float", 20.0, "Y", "屏幕坐标"),
        ("gk_width", "float", 480.0, "宽", "像素"),
        ("gk_height", "float", 240.0, "高", "像素"),
        ("gk_bottom", "bool", True, "垫底",
         "勾上 = 永远垫在别的控件下面(菜单底板用这个)"),
        ("gk_start_hidden", "bool", False, "一开始就藏起来",
         "勾上 = 建出来是隐藏的(暂停菜单底板必勾 —— 开局不该显示菜单)"),
        ("gk_align", "enum", "TOP_LEFT", "对齐", UI_ALIGN_HELP, UI_ALIGN_ITEMS),
        ("gk_margin", "float", 24.0, "边距", "对齐到屏幕边 / 角时留多少像素"),
        ("gk_parent_widget", "string", "", "父控件",
         "挂到哪个界面控件下面(留空 = 铺在屏幕上)。嵌套面板用得上"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_color", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_width", text="宽")
        row.prop(self, "gk_height", text="高")
        row.prop(self, "gk_radius", text="圆角")
        row = layout.row(align=True)
        row.prop(self, "gk_bottom", text="垫底", toggle=True)
        row.prop(self, "gk_start_hidden", text="开局藏", toggle=True)
        row.prop(self, "gk_parent_widget", text="", icon="NODE")
        B.draw_ui_align_row(self, layout)


@N
class GKNodeUiSet(B.GKStatementNode):
    bl_idname = "GKNodeUiSet"
    bl_label = "界面_设置"
    bl_icon = "PREFERENCES"
    gk_category = "界面"
    gk_doc = ("运行时改一个**已经建出来的**界面控件:位置 / 大小 / 透明度 / "
              "着色 / 文字。控件名 = 创建它的那个节点名(和『界面_隐藏控件』"
              "同一个规则)。\n"
              "── 每项前面有勾 ──\n"
              "『改哪几项就勾哪几项』,没勾的一律不碰 —— 所以同一个节点"
              "可以只改透明度,也可以一次改好几样。\n"
              "颜色 / 透明度走 `modulate`(整块控件一起染色),"
              "文字走 Label / 按钮的显示文字。")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "X", 0.0),
        S.pin(FLOAT, "Y", 0.0),
        S.pin(FLOAT, "透明度", 1.0),
        S.pin(TEXT, "文字", ""),
    )
    gk_input_props = {"X": "gk_x", "Y": "gk_y",
                      "透明度": "gk_alpha", "文字": "gk_text"}
    gk_prop_defs = (
        ("gk_widget", "string", "", "控件名", "要改哪个控件(填创建它的节点名)"),
        ("gk_set_pos", "bool", False, "改位置", "挪到下面的 X / Y"),
        ("gk_x", "float", 0.0, "X", "相对它的对齐点(没对齐就是屏幕左上角)"),
        ("gk_y", "float", 0.0, "Y", ""),
        ("gk_set_size", "bool", False, "改大小", ""),
        ("gk_width", "float", 0.0, "宽", "像素"),
        ("gk_height", "float", 0.0, "高", "像素"),
        ("gk_set_alpha", "bool", False, "改透明度", "1 = 不透明;0 = 全透明"),
        ("gk_alpha", "float", 1.0, "透明度", ""),
        ("gk_set_color", "bool", False, "改颜色", "整块控件乘这个颜色"),
        ("gk_color", "color", (1.0, 1.0, 1.0, 1.0), "颜色", ""),
        ("gk_set_text", "bool", False, "改文字", "只对文字类控件(文本 / 按钮)有效"),
        ("gk_text", "string", "", "文字", ""),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_widget", text="", icon="RESTRICT_SELECT_OFF")
        row = layout.row(align=True)
        row.prop(self, "gk_set_pos", text="位置", toggle=True)
        if self.gk_set_pos:
            row.prop(self, "gk_x", text="X")
            row.prop(self, "gk_y", text="Y")
        row = layout.row(align=True)
        row.prop(self, "gk_set_size", text="大小", toggle=True)
        if self.gk_set_size:
            row.prop(self, "gk_width", text="宽")
            row.prop(self, "gk_height", text="高")
        row = layout.row(align=True)
        row.prop(self, "gk_set_alpha", text="透明度", toggle=True)
        if self.gk_set_alpha:
            row.prop(self, "gk_alpha", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_set_color", text="颜色", toggle=True)
        if self.gk_set_color:
            row.prop(self, "gk_color", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_set_text", text="文字", toggle=True)
        if self.gk_set_text:
            row.prop(self, "gk_text", text="")


@N
class GKNodeUiPauseGame(B.GKStatementNode):
    bl_idname = "GKNodeUiPauseGame"
    bl_label = "界面_暂停游戏"
    bl_icon = "PAUSE"
    gk_category = "界面"
    gk_doc = ("暂停 / 继续**整个世界**(物理、场景动画、角色逻辑全停 —— "
              "对应 `get_tree().paused`)。暂停时可以把鼠标放出来、"
              "把一块『暂停菜单』面板弹出来;继续时自动还原(鼠标收回去、"
              "菜单藏起来)。\n"
              "── 暂停菜单怎么搭 ──\n"
              "①『输入_按键』(Esc、刚按下)→ 『界面_暂停游戏』(勾『暂停』,"
              "『菜单控件』填 `暂停菜单`);"
              "② 场景里放一块『界面_面板』(命名成 `暂停菜单`) + 几个"
              "『界面_按钮』;"
              "③ 菜单里『继续』按钮 → 再来一个『界面_暂停游戏』(取消勾"
              "『暂停』)。重开 / 退出就接『实例_重载场景』/『流程_退出游戏』。\n"
              "── 为什么暂停时按钮还点得动 ──\n"
              "真 Godot 实测过:世界停了,界面控件的点击**不受影响**"
              "(和『显示_暂停节点』那种停单个节点不是一回事)。")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_pause", "bool", True, "暂停", "勾上 = 暂停世界;取消 = 继续"),
        ("gk_show_mouse", "bool", True, "放出鼠标",
         "暂停时显示鼠标(继续时还原成原来的模式 —— 锁定的还是锁定)"),
        ("gk_menu", "string", "暂停菜单", "菜单控件",
         "暂停时显示 / 继续时隐藏的控件名(留空 = 不自动显隐)"),
        ("gk_hotkey", "bool", True, "Esc 也能开关",
         "按 Esc 直接开关暂停(不用经过按钮);一遍图里只需要一个节点勾它"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_pause", text="暂停世界" if self.gk_pause else "继续游戏",
                 toggle=True, icon="PAUSE" if self.gk_pause else "PLAY")
        row.prop(self, "gk_show_mouse", text="鼠标", toggle=True)
        row.prop(self, "gk_hotkey", text="Esc", toggle=True)
        row = layout.row(align=True)
        row.prop(self, "gk_menu", text="", icon="NODE")


@N
class GKNodeUiDialog(B.GKStatementNode):
    bl_idname = "GKNodeUiDialog"
    bl_label = "界面_对话框"
    bl_icon = "SORTALPHA"
    gk_category = "界面"
    gk_doc = ("弹一句话(剧情 / 引导 / NPC 对话):屏幕下方一块面板 + "
              "说话人 + 正文,**逐字显示**。\n"
              "── 怎么继续 ──\n"
              "点一下鼠标 / 按任意键:正在打字就先**补完这一句**;"
              "打完了再点一下**关掉面板**,沿『完成』继续后面的逻辑"
              "(所以执行流会在对话框这里**停住**,等玩家看完)。\n"
              "── 立绘 ──\n"
              "『立绘』可选(点『选图片』挑一张 Blender 图片,"
              "导出时跟着带进 Godot);留空就是纯文字对话框。")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "文字", "你好，冒险者！"),
    )
    gk_input_props = {"文字": "gk_text"}
    gk_prop_defs = (
        ("gk_speaker", "string", "", "说话人", "留空 = 不显示名字"),
        ("gk_text", "string", "你好，冒险者！", "文字", "没接『文字』引脚时显示这句"),
        ("gk_image", "string", "", "立绘",
         "可选的半身像 / 头像(Blender 图片名或 res:// 路径)"),
        ("gk_speed", "float", 30.0, "打字速度", "每秒显示几个字;0 = 直接全显示"),
        ("gk_width", "float", 720.0, "宽", "面板宽(像素)"),
        ("gk_height", "float", 150.0, "高", "面板高(像素)"),
        ("gk_margin", "float", 48.0, "距底边", "面板离屏幕下边多少像素"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_speaker", text="", icon="USER")
        row.prop(self, "gk_speed", text="字/秒")
        layout.prop(self, "gk_text", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_image", text="", icon="IMAGE_DATA")
        pick = row.operator("gk.pick_image", text="", icon="VIEWZOOM")
        pick.node_name = self.name
        pick.image_prop = "gk_image"
        row = layout.row(align=True)
        row.prop(self, "gk_width", text="宽")
        row.prop(self, "gk_height", text="高")
        row.prop(self, "gk_margin", text="距底边")


# ==========================================================================
# 界面第二版(第三十八轮):列表 / 缓动 / 九宫格 / 输入框 / 焦点
# ==========================================================================
# ── 这一批解决什么(用户 2026-10-06:"UI 继续做深")──────────────────
# 第一版能摆静态控件了,但"一列东西"要靠手填 X / Y 一个个错开,
# 存档槽 / 设置项 / 背包都做不整齐;而且没有输入框、没有焦点导航、
# 没有"淡入淡出"这种小动效。这一批补齐这五样。
#
# ⚠ 容器会**完全接管**子控件的位置和尺寸(真 Godot 实测:子控件自己设的
#   position / 锚点会被容器覆盖)—— 所以挂进『界面_列表』的控件由生成器
#   自动**跳过摆位**,尺寸改用 `custom_minimum_size`。
@N
class GKNodeUiList(B.GKStatementNode):
    bl_idname = "GKNodeUiList"
    bl_label = "界面_列表"
    bl_icon = "LINENUMBERS_ON"
    gk_category = "界面"
    gk_doc = ("一列(或一行)会自动排队的容器 —— 存档槽 / 设置项 / 背包都靠它。"
              "把别的界面控件的 **『父控件』填成它的名字**,那些控件就自动"
              "从上往下排(不用自己填 X / Y 一个个错开)。\n"
              "── 两种排法 ──\n"
              "『竖排』勾着 = 从上往下(列表);取消 = 从左往右(工具栏)。\n"
              "『可滚动』勾着 = 内容超了会出滚动条(鼠标滚轮能滚)。\n"
              "── 和『界面_列表_填充』的分工 ──\n"
              "要**能点**的行(存档槽 / 菜单项):自己放『界面_按钮』,"
              "『父控件』填列表名 —— 每个按钮各接各的点击链;"
              "只是**显示**的行(排行榜 / 日志):用『界面_列表_填充』"
              "喂一个文字数组,它按数组建行。")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_scroll", "bool", True, "可滚动", "内容超了出滚动条(滚轮能滚)"),
        ("gk_vertical", "bool", True, "竖排", "取消勾选 = 横排(工具栏那种)"),
        ("gk_separation", "int", 8, "间距", "行与行之间留多少像素"),
        ("gk_x", "float", 12.0, "X", "屏幕坐标；对齐 = 左上时才用它"),
        ("gk_y", "float", 60.0, "Y", "屏幕坐标"),
        ("gk_width", "float", 320.0, "宽", "像素"),
        ("gk_height", "float", 200.0, "高", "像素"),
        ("gk_align", "enum", "TOP_LEFT", "对齐", UI_ALIGN_HELP, UI_ALIGN_ITEMS),
        ("gk_margin", "float", 24.0, "边距", "对齐到屏幕边 / 角时留多少像素"),
        ("gk_parent_widget", "string", "", "父控件", "挂到哪个界面控件下面"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_width", text="宽")
        row.prop(self, "gk_height", text="高")
        row.prop(self, "gk_separation", text="间距")
        row = layout.row(align=True)
        row.prop(self, "gk_scroll", text="滚动", toggle=True)
        row.prop(self, "gk_vertical", text="竖排", toggle=True)
        row.prop(self, "gk_parent_widget", text="", icon="NODE")
        B.draw_ui_align_row(self, layout)


@N
class GKNodeUiListFill(B.GKStatementNode):
    bl_idname = "GKNodeUiListFill"
    bl_label = "界面_列表_填充"
    gk_category = "界面"
    bl_icon = "LINENUMBERS_ON"
    gk_doc = ("把一串文字**灌进列表**(排行榜 / 日志 / 道具名)。"
              "『列表』填『界面_列表』的名字;文字从『内容』引脚接一个"
              "列表变量(『列表_新建』/『列表_添加』那套)或者写死的"
              "『文字』(每行一条,用竖线 `|` 分隔)。\n"
              "每次调用会**先清空旧行再建**,所以每物理帧刷也不会越堆越多。"
              "注意:这样建出来的行是**文字**(显示用);要能点的行请自己放"
              "『界面_按钮』(父控件填列表名)。")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(ANY, "内容", []),
    )
    gk_prop_defs = (
        ("gk_list", "string", "", "列表", "『界面_列表』那个节点的名字"),
        ("gk_text", "string", "", "文字",
         "没接『内容』时用它:每行一条,用竖线分隔(如 `第一名|第二名`)"),
        ("gk_var", "string", "", "变量名",
         "没接『内容』时也可以填一个**列表变量**的名字(优先于『文字』)"),
        ("gk_line_height", "float", 0.0, "行高", "像素;0 = 按字体自动"),
        ("gk_size", "int", 20, "字号", "像素"),
        ("gk_color", "color", (1.0, 1.0, 1.0, 1.0), "颜色", "行的文字颜色"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_list", text="", icon="LINENUMBERS_ON")
        row = layout.row(align=True)
        row.prop(self, "gk_var", text="", icon="OUTLINER_DATA_GP_LAYER")
        row.prop(self, "gk_line_height", text="行高")
        row.prop(self, "gk_size", text="字号")
        layout.prop(self, "gk_text", text="")
        layout.prop(self, "gk_color", text="")


@N
class GKNodeUiTween(B.GKStatementNode):
    bl_idname = "GKNodeUiTween"
    bl_label = "界面_缓动"
    bl_icon = "IPO_EASE_IN_OUT"
    gk_category = "界面"
    gk_doc = ("把一个界面控件的某个属性**平滑地**推过去 —— 菜单淡入 / "
              "提示条滑进来 / 按钮弹一下。\n"
              "『改什么』有:透明度、位置 X / 位置 Y、缩放、宽 / 高。"
              "勾『等它播完』的话,后面的节点会等动画结束再继续"
              "(不勾就是**边播边往下走**,适合同时动好几个控件)。\n"
              "目标控件按**名字**找(创建它的节点名 / 面板名)。")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "到", 1.0),
    )
    gk_input_props = {"到": "gk_to"}
    gk_prop_defs = (
        ("gk_widget", "string", "", "控件名", "要动哪个控件(创建它的节点名)"),
        ("gk_property", "enum", "ALPHA", "改什么", "", (
            ("ALPHA", "透明度", "0 = 全透明,1 = 不透明(淡入淡出)"),
            ("POS_X", "位置 X", "横向滑动;相对**现在的位置**(填 +80 往右 80)"),
            ("POS_Y", "位置 Y", "纵向滑动;同上"),
            ("SCALE", "缩放", "1 = 原样,1.1 = 放大一点(按钮弹一下)"),
            ("WIDTH", "宽", "像素"),
            ("HEIGHT", "高", "像素"),
        )),
        ("gk_to", "float", 1.0, "到", "缓动到多少(没接引脚时用)"),
        ("gk_time", "float", 0.4, "时长", "秒"),
        ("gk_delay", "float", 0.0, "延迟", "秒;等一会儿再开始"),
        ("gk_wait", "bool", False, "等它播完", "勾上 = 后面的节点等动画结束再走"),
        ("gk_trans", "enum", "TRANS_SINE", "曲线", "速度曲线", (
            ("TRANS_LINEAR", "匀速", ""),
            ("TRANS_SINE", "正弦(平滑)", ""),
            ("TRANS_CUBIC", "三次(先慢后快)", ""),
            ("TRANS_QUAD", "二次", ""),
            ("TRANS_BACK", "回弹(冲过头再回来)", ""),
            ("TRANS_BOUNCE", "弹跳", ""),
            ("TRANS_ELASTIC", "橡皮筋", ""),
        )),
        ("gk_ease", "enum", "EASE_OUT", "缓动方式", "", (
            ("EASE_IN", "慢进", ""),
            ("EASE_OUT", "慢出", "UI 最常用:起步快、到点前慢下来"),
            ("EASE_IN_OUT", "两头慢", ""),
        )),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_widget", text="", icon="RESTRICT_SELECT_OFF")
        row.prop(self, "gk_property", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_to", text="到")
        row.prop(self, "gk_time", text="秒")
        row.prop(self, "gk_delay", text="延迟")
        row = layout.row(align=True)
        row.prop(self, "gk_trans", text="")
        row.prop(self, "gk_ease", text="")
        row.prop(self, "gk_wait", text="等播完", toggle=True)


@N
class GKNodeUiNinePatch(B.GKStatementNode):
    bl_idname = "GKNodeUiNinePatch"
    bl_label = "界面_九宫格"
    bl_icon = "MESH_GRID"
    gk_category = "界面"
    gk_doc = ("**九个切片的底图**:一张小图(比如一圈花边)按四边切好,"
              "拉多大都不会把边角拉变形 —— 面板 / 按钮 / 标题条的正式做法。\n"
              "『切片』四个数字 = 上 / 右 / 下 / 左各留多少像素**不拉伸**"
              "(中间部分拉伸填满)。比如 12 表示四边各 12 像素的花边。\n"
              "图片和『界面_图片』同一套:点『选图片』挑 Blender 图片,"
              "导出时自动落盘成 `art/ui_*.png`。")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_image", "string", "", "图片", "Blender 图片名(点『选图片』挑)或 res:// 路径"),
        ("gk_slice", "int", 12, "切片", "四边各留多少像素不拉伸(花边宽度)"),
        ("gk_x", "float", 12.0, "X", "屏幕坐标；对齐 = 左上时才用它"),
        ("gk_y", "float", 12.0, "Y", "屏幕坐标"),
        ("gk_width", "float", 360.0, "宽", "像素"),
        ("gk_height", "float", 120.0, "高", "像素"),
        ("gk_tint", "color", (1.0, 1.0, 1.0, 1.0), "着色", "给图乘一个颜色"),
        ("gk_bottom", "bool", False, "垫底", "勾上 = 铺在别的控件下面(当底图)"),
        ("gk_align", "enum", "TOP_LEFT", "对齐", UI_ALIGN_HELP, UI_ALIGN_ITEMS),
        ("gk_margin", "float", 24.0, "边距", "对齐到屏幕边 / 角时留多少像素"),
        ("gk_parent_widget", "string", "", "父控件", "挂到哪个界面控件下面"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_image", text="", icon="IMAGE_DATA")
        pick = row.operator("gk.pick_image", text="", icon="VIEWZOOM")
        pick.node_name = self.name
        pick.image_prop = "gk_image"
        row = layout.row(align=True)
        row.prop(self, "gk_width", text="宽")
        row.prop(self, "gk_height", text="高")
        row.prop(self, "gk_slice", text="切片")
        row = layout.row(align=True)
        row.prop(self, "gk_tint", text="")
        row.prop(self, "gk_bottom", text="垫底", toggle=True)
        row.prop(self, "gk_parent_widget", text="", icon="NODE")
        B.draw_ui_align_row(self, layout)


@N
class GKNodeUiInput(B.GKStatementNode):
    bl_idname = "GKNodeUiInput"
    bl_label = "界面_输入框"
    bl_icon = "GREASEPENCIL"        # 找不到"输入"图标,用铅笔(它就是个写字框)
    gk_category = "界面"
    gk_doc = ("一个输入框(昵称 / 聊天 / 存档名)。玩家**按回车**时沿"
              "『提交』出口继续(真 Godot 实测:回车才触发,点别处失焦"
              "**不会**误触发);框里的字随时可以用『界面_取文字』读。\n"
              "『自动聚焦』勾上 = 建出来就选中它(键盘直接能打字);"
              "否则要用『界面_设焦点』或点一下。")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "默认文字", ""),
    )
    gk_input_props = {"默认文字": "gk_text"}
    gk_prop_defs = (
        ("gk_text", "string", "", "默认文字", "一建出来框里的字(留空 = 只有占位提示)"),
        ("gk_placeholder", "string", "输入昵称…", "占位提示",
         "空着的时候显示的灰字"),
        ("gk_max_length", "int", 24, "最多几个字", "0 = 不限"),
        ("gk_password", "bool", False, "密码模式", "打进去的字显示成圆点"),
        ("gk_focus", "bool", False, "自动聚焦", "建出来就选中(键盘直接能打字)"),
        ("gk_x", "float", 20.0, "X", "屏幕坐标；对齐 = 左上时才用它"),
        ("gk_y", "float", 20.0, "Y", "屏幕坐标"),
        ("gk_width", "float", 240.0, "宽", "像素"),
        ("gk_height", "float", 40.0, "高", "像素"),
        ("gk_align", "enum", "TOP_LEFT", "对齐", UI_ALIGN_HELP, UI_ALIGN_ITEMS),
        ("gk_margin", "float", 24.0, "边距", "对齐到屏幕边 / 角时留多少像素"),
        ("gk_parent_widget", "string", "", "父控件", "挂到哪个界面控件下面"),
    )
    # 出口叫『提交』(不叫『完成』):emit_statement 会自动续接『完成』,
    # 那样输入框刚建出来后面的逻辑就跑了。真正的续接在 text_submitted 回调里。
    gk_outputs = (S.pin(FLOW, "完成"), S.pin(FLOW, "提交"),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_placeholder", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_text", text="")
        row.prop(self, "gk_max_length", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_width", text="宽")
        row.prop(self, "gk_height", text="高")
        row.prop(self, "gk_password", text="密码", toggle=True)
        row.prop(self, "gk_focus", text="聚焦", toggle=True)
        row.prop(self, "gk_parent_widget", text="", icon="NODE")
        B.draw_ui_align_row(self, layout)


@N
class GKNodeUiReadText(B.GKExpressionNode):
    bl_idname = "GKNodeUiReadText"
    bl_label = "界面_取文字"
    gk_category = "界面"
    bl_icon = "FONT_DATA"
    gk_doc = ("读一个界面控件上的文字(输入框里玩家打的字 / 标签 / 按钮)。"
              "接到判断或『变量_设置』上就能用,比如"
              "『如果 昵称 == \"\" 就提示先起名』。")
    gk_outputs = (S.pin(TEXT, "文字", ""),)
    gk_prop_defs = (
        ("gk_widget", "string", "", "控件名", "要读哪个控件(创建它的节点名)"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_widget", text="", icon="RESTRICT_SELECT_OFF")


@N
class GKNodeUiFocus(B.GKStatementNode):
    bl_idname = "GKNodeUiFocus"
    bl_label = "界面_设焦点"
    bl_icon = "TRIA_DOWN_BAR"
    gk_category = "界面"
    gk_doc = ("把**键盘 / 手柄焦点**放到某个控件上(通常是按钮或输入框)。"
              "焦点在谁身上:方向键 / 手柄摇杆就能在按钮之间选(真 Godot 实测:"
              "按钮默认就能被选,自动按**位置**找邻居,不用手接),回车 / A 键"
              "等于点它。\n"
              "菜单打开时把它设到『继续游戏』上,手柄用户一进来就能操作;"
              "框里填空 = 清掉焦点(回到鼠标操作)。")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "控件名", ""),
    )
    gk_input_props = {"控件名": "gk_widget"}
    gk_prop_defs = (
        ("gk_widget", "string", "", "控件名",
         "聚焦哪个控件;留空 = 清掉焦点"),
        ("gk_focus", "bool", True, "聚焦", "取消勾选 = 清掉焦点"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_widget", text="", icon="RESTRICT_SELECT_OFF")
        row.prop(self, "gk_focus", text="", toggle=True)


# ==========================================================================
# 界面设计图(第四十轮,用户选的"套用式")
# ==========================================================================
# 用户原话:"添加游戏界面设计编辑器,方便对UI设计"。
#
# ── 做法 ─────────────────────────────────────────────────────────────
# 照动画树那一套:界面"长什么样"画在**独立一张图**(GKUiDesignTree)里,
# 玩法图只放一个『界面_套用设计』节点把那张图里摆好的控件**一次性建出来**。
# 试出来的分工依据是"这个节点会不会**建出**一个控件":
#     设计图 = 摆控件(文本/按钮/面板/列表/图片…);
#     玩法图 = 什么时候显示、点了做什么(隐藏/设置/飘字/缓动/对话框…)。
# 点击行为用『界面_按钮点击』按名字接到设计图里那个按钮上。
#
# ── 为什么按钮不给"直接连到设计图里的按钮节点"─────────────────────
# 设计图的节点在**另一棵树**里,玩法图的执行流够不着它;而把两张图
# 的执行流接起来(蓝图式)是下一轮的事。所以这一版给出的是
# "按名字找控件 + 接行为" —— 和『界面_隐藏控件』『界面_设置』
# 按控件名找控件是同一套做法,一个概念通吃。
@N
class GKNodeUiApplyDesign(B.GKStatementNode):
    bl_idname = "GKNodeUiApplyDesign"
    bl_label = "界面_套用设计"
    bl_icon = "NODETREE"
    gk_category = "界面"
    gk_doc = ("把一张『游戏界面』设计图里摆好的控件**全部建出来** —— "
              "玩法图里放这一个节点就够了,不用把十几个控件节点都拖过来。\n"
              "『设计图』留空 = 用文件里第一张设计图(只有一张时不用管它)。\n"
              "建出来之后,控件就用它的**节点名**来引用:"
              "『界面_隐藏控件』『界面_设置』『界面_按钮点击』填那个名字即可。")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "设计图", ""),
    )
    gk_input_props = {"设计图": "gk_design"}
    gk_prop_defs = (
        ("gk_design", "string", "", "设计图",
         "留空 = 用文件里第一张『游戏界面』设计图"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)

    def draw_buttons(self, context, layout):
        trees = ui_design_tree_names()
        row = layout.row(align=True)
        if len(trees) <= 1:
            row.label(text=f"设计图:{trees[0] if trees else '(还没有)'}",
                      icon="NODETREE")
        else:
            row.prop(self, "gk_design", text="", icon="NODETREE")
        picker = row.operator("gk.pick_ui_design", text="", icon="VIEWZOOM")
        picker.node_name = self.name
        if len(trees) > 1:
            row.label(text=f"(共 {len(trees)} 张)", icon="INFO")
        # 顺带把"这张图里有什么"报出来 —— 省得切过去数
        tree = find_ui_design_tree(getattr(self, "gk_design", ""))
        if tree is None:
            icon = "ERROR" if trees else "INFO"
            layout.label(text="还没有界面设计图(侧栏『游戏界面』里建一张)",
                         icon=icon)
            return
        widgets = [str(getattr(n, "name", "?")) for n in tree.nodes
                   if getattr(n, "gk_ui_builder", False)]
        if not widgets:
            layout.label(text=f"『{tree.name}』里还没有控件", icon="ERROR")
            return
        head = "、".join(widgets[:4]) + ("…" if len(widgets) > 4 else "")
        layout.label(text=f"会建出 {len(widgets)} 个控件:{head}", icon="CHECKMARK")


@N
class GKNodeUiOnClick(B.GKStatementNode):
    bl_idname = "GKNodeUiOnClick"
    bl_label = "界面_按钮点击"
    bl_icon = "PLAY"
    gk_category = "界面"
    gk_doc = ("按名字找到按钮,给它接上『点击』出口后面这段逻辑 —— "
              "用来给**设计图**里摆好的按钮接行为(设计图只管长什么样,"
              "点了做什么写在玩法图里)。\n"
              "── 和『界面_按钮』的区别 ──\n"
              "『界面_按钮』是**建按钮 + 点击行为**一把抓(按钮节点在玩法图里);"
              "这个只**接行为**,不建也不改按钮(按钮来自『界面_套用设计』)。\n"
              "重复执行也只接一次(不会越点越多次),所以放在每帧的链上也没事。")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "控件名", ""),
    )
    gk_input_props = {"控件名": "gk_widget"}
    gk_prop_defs = (
        ("gk_widget", "string", "", "控件名",
         "设计图里那个按钮节点的名字(建出来的控件就叫这个名字)"),
    )
    # 出口叫『点击』不叫『完成』:和『界面_按钮』同一个道理 ——
    # 『完成』会被自动续接(接上就立刻跑后面的逻辑),而这里的语义是"点了才走"。
    gk_outputs = (S.pin(FLOW, "点击"),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_widget", text="", icon="RESTRICT_SELECT_OFF")
        layout.label(text="点了就走『点击』出口", icon="FORWARD")


# ── 哪些节点算"控件构建"(设计图里该建什么由它决定)──────────────────
# 判据:**这个节点会建出一个控件**。动作类(隐藏/设置/飘字/缓动/对话框/
# 列表_填充/暂停游戏/设焦点/取文字)只管"运行时改已有控件",不进设计图 ——
# 它们是玩法图的事。
# 名单写在这里(而不是散在 8 个类上)是因为『界面_套用设计』要按它挑节点:
# 一个问题只有一处答案,以后加新控件节点只改这一张表。
UI_DESIGN_BUILDER_NODES = (
    "GKNodeUiText", "GKNodeUiBar", "GKNodeUiButton", "GKNodeUiImage",
    "GKNodeUiPanel", "GKNodeUiList", "GKNodeUiNinePatch", "GKNodeUiInput",
)

for _cls in CLASSES:
    if _cls.bl_idname in UI_DESIGN_BUILDER_NODES:
        # 两棵树都能放(老图一字不变,设计图也能摆)—— 用户第 40 轮选的
        _cls.gk_tree_type = UI_TREES
        # 『界面_套用设计』按这个标记挑节点,见 codegen.services._ui_design_nodes
        _cls.gk_ui_builder = True


# ==========================================================================
# 射线
# ==========================================================================
@N
class GKNodeRaycast(B.GKStatementNode):
    bl_idname = "GKNodeRaycast"
    bl_label = "射线_检测"
    bl_icon = "RESTRICT_SELECT_OFF"
    gk_category = "射线"
    gk_doc = ("从一个点沿某个方向打一条射线，看打到什么(开枪命中 / 视线判定 / "
              "地面检测)。打到东西走『命中』，没打到走『没打中』")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(VECTOR, "方向", (1.0, 0.0, 0.0)),
        S.pin(FLOAT, "距离", 200.0),
    )
    gk_input_props = {"方向": "gk_direction", "距离": "gk_distance"}
    gk_prop_defs = (
        ("gk_direction", "float_vector", (1.0, 0.0, 0.0), "方向", "单位向量，会自动归一化"),
        ("gk_distance", "float", 200.0, "距离", "射线长度(像素 / 米)"),
        ("gk_offset", "float_vector", (0.0, 0.0, 0.0), "起点偏移", "相对自身位置的偏移"),
        ("gk_layer", "int", 1, "碰撞层", "只检测这些层(位掩码，1 表示第 1 层)"),
    )
    # 出口不叫『完成』:见 codegen 里 _stmt_raycast 的说明 ——
    # 叫『完成』会被 emit_statement 自动续接，分支就废了。
    gk_outputs = (
        S.pin(FLOW, "命中"),
        S.pin(FLOW, "没打中"),
        S.pin(OBJECT, "打到的东西", None),
        S.pin(VECTOR, "命中点", (0.0, 0.0, 0.0)),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_distance", text="")
        row.prop(self, "gk_layer", text="")
        layout.prop(self, "gk_direction", text="")


@N
class GKNodeRaycastCheck(B.GKExpressionNode):
    bl_idname = "GKNodeRaycastCheck"
    bl_label = "射线_有东西吗"
    bl_icon = "QUESTION"
    gk_category = "射线"
    gk_doc = ("往某个方向看一眼有没有东西(视线判定 / 撞墙检测)，"
              "结果接到『如果_否则』的条件上")
    gk_inputs = (
        S.pin(VECTOR, "方向", (1.0, 0.0, 0.0)),
        S.pin(FLOAT, "距离", 200.0),
    )
    gk_input_props = {"方向": "gk_direction", "距离": "gk_distance"}
    gk_prop_defs = (
        ("gk_direction", "float_vector", (1.0, 0.0, 0.0), "方向", "单位向量，会自动归一化"),
        ("gk_distance", "float", 200.0, "距离", "射线长度"),
        ("gk_layer", "int", 1, "碰撞层", "只检测这些层"),
    )
    # 纯表达式:取值时把射线"就地"射出去。
    # GDScript 的表达式在被使用的作用域里求值，所以生成的多行代码
    # 正好落在需要这个布尔值的位置(比如 if 的条件上)。
    #
    # gk_no_cache:射线每次求值都必须**重新射**。生成器默认会缓存表达式文本，
    # 缓存之后第二次求值只会复用第一次的结果，射线就永远停在那里了。
    gk_no_cache = True
    gk_outputs = (S.pin(BOOL, "有东西", False),)



# ==========================================================================
# 定时
# ==========================================================================
@N
class GKNodeEverySeconds(B.GKStatementNode):
    bl_idname = "GKNodeEverySeconds"
    bl_label = "定时_每隔几秒"
    bl_icon = "TIME"
    gk_category = "定时"
    gk_doc = ("每隔 N 秒执行一次(刷怪 / 回血 / 自动存档)。"
              "用独立计时器,不会被帧率影响")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "间隔", 1.0),
    )
    gk_input_props = {"间隔": "gk_interval"}
    gk_prop_defs = (
        ("gk_interval", "float", 1.0, "间隔", "秒"),
        ("gk_start_now", "bool", False, "立即开始", "勾上=不等第一个间隔就先执行一次"),
    )
    # 出口不叫『完成』:要等计时器到点才走，见 _stmt_every_seconds 的说明。
    gk_outputs = (S.pin(FLOW, "到点了"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_interval", text="")
        row.prop(self, "gk_start_now", text="", toggle=True)


@N
class GKNodeTweenValue(B.GKStatementNode):
    bl_idname = "GKNodeTweenValue"
    bl_label = "定时_缓动数值"
    bl_icon = "IPO_EASE_IN_OUT"
    gk_category = "定时"
    gk_doc = ("把一个脚本变量在指定时间内平滑地变到目标值(血条缓动 / 镜头推近 / "
              "UI 滑入)。对应 Godot 的 Tween")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "目标值", 100.0),
        S.pin(FLOAT, "时长", 0.5),
    )
    gk_input_props = {"目标值": "gk_to", "时长": "gk_time"}
    gk_prop_defs = (
        ("gk_var", "string", "value", "变量名", "要在 Blender 侧声明过的脚本变量"),
        ("gk_to", "float", 100.0, "目标值", "缓动到多少"),
        ("gk_time", "float", 0.5, "时长", "秒"),
        ("gk_trans", "enum", "TRANS_LINEAR", "曲线", "速度曲线", (
            ("TRANS_LINEAR", "匀速", ""),
            ("TRANS_SINE", "正弦(平滑)", ""),
            ("TRANS_CUBIC", "三次(先慢后快)", ""),
            ("TRANS_BACK", "回弹(冲过头再回来)", ""),
            ("TRANS_BOUNCE", "弹跳", ""),
            ("TRANS_ELASTIC", "橡皮筋", ""),
        )),
        ("gk_ease", "enum", "EASE_IN_OUT", "缓动方式", "", (
            ("EASE_IN", "慢进", ""),
            ("EASE_OUT", "慢出", ""),
            ("EASE_IN_OUT", "两头慢", ""),
        )),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_var", text="")
        row.prop(self, "gk_to", text="")
        row.prop(self, "gk_time", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_trans", text="")
        row.prop(self, "gk_ease", text="")


# ==========================================================================
# 时间轴 —— 把任意节点的任意属性在时间里推过去
# ==========================================================================
# 用户 2026-10-03 要的"时间轴":
#   * 摄像机视野变化        -> 缓动相机的 fov
#   * 场景动画(门/平台…)   -> 缓动它们的位置 / 旋转 / 颜色
#   * 不同动画的整体联动    -> 几条缓动 + 几段动画一起启动(配『序列』)
#
# ── 为什么不做成"曲线编辑器"那种时间轴 ────────────────────────────
# 那种 UI 在 Blender 节点里画不出来(要自己造一个编辑器),而效果完全可以用
# "一个节点管一条轨道"拼出来 —— 而且**能和其他节点混着摆**:
# 要几条轨道就放几个节点,再串在『序列』里,顺序/同时都看得见。
# Godot 的 Tween 本身是**异步**的:tween_property 一调用就返回,曲线在
# 后台自己跑,所以一个节点接着另一个 = 天然并行。
@N
class GKNodeTweenProperty(B.GKStatementNode):
    bl_idname = "GKNodeTweenProperty"
    bl_label = "定时_缓动属性"
    bl_icon = "IPO_EASE_IN_OUT"
    gk_category = "定时"
    gk_doc = ("把**某个节点的某个属性**在指定时间里平滑地推到目标值 —— "
              "时间轴的一条轨道。"
              "『属性』填 Godot 的属性名,支持带子项:`fov`、`position:y`、"
              "`rotation:y`、`modulate:a`、`scale`、`volume_db` …"
              "常用组合:相机 fov 拉近、门 position:y 升起、"
              "灯 light_energy 变暗、角色 modulate:a 淡出。"
              "Tween 是异步的:调用完立刻往下走,动画在后台自己跑 —— "
              "所以几条缓动**天生就是同时**的")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(ANY, "到", 0.0),
    )
    gk_input_props = {"到": "gk_to"}
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "目标", "要动哪个节点(默认自己)"),
        ("gk_property", "string", "fov", "属性",
         "Godot 的属性名,可用 `position:y` / `modulate:a` 这种子路径"),
        ("gk_to", "float", 0.0, "到",
         "缓动到多少(想给向量/颜色就把『到』引脚连一个『常量_向量』上来)"),
        ("gk_time", "float", 0.5, "时长", "秒"),
        ("gk_trans", "enum", "TRANS_SINE", "曲线", "速度曲线", (
            ("TRANS_LINEAR", "匀速", ""),
            ("TRANS_SINE", "正弦(平滑)", ""),
            ("TRANS_CUBIC", "三次(先慢后快)", ""),
            ("TRANS_QUAD", "二次", ""),
            ("TRANS_BACK", "回弹(冲过头再回来)", ""),
            ("TRANS_BOUNCE", "弹跳", ""),
            ("TRANS_ELASTIC", "橡皮筋", ""),
        )),
        ("gk_ease", "enum", "EASE_IN_OUT", "缓动方式", "", (
            ("EASE_IN", "慢进", ""),
            ("EASE_OUT", "慢出", ""),
            ("EASE_IN_OUT", "两头慢", ""),
        )),
        # 缓动结束后要不要补一句"精确定位"?
        # 浮点插值最后一帧可能差 0.0001,做"门完全关上"这种判定时会差一点。
        ("gk_snap", "bool", True, "结束时对齐",
         "缓动结束时把值精确设成目标值(避免浮点误差差一点点)"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        B.draw_target_picker(self, row)
        row.prop(self, "gk_property", text="", icon="PROPERTIES")
        row = layout.row(align=True)
        row.prop(self, "gk_to", text="到")
        row.prop(self, "gk_time", text="秒")
        row = layout.row(align=True)
        row.prop(self, "gk_trans", text="")
        row.prop(self, "gk_ease", text="")


# ==========================================================================
# 时间轴(照虚幻蓝图的 Timeline)
# ==========================================================================
# 用户 2026-10-04(第十二轮):"动画的时间轴好像还没设计完成,虚幻蓝图的
# 设计逻辑还不错,但这里可以实现吗?"
#
# 虚幻的 Timeline 长这样:一个节点 = 一条时间轴,启动之后每帧给你
#   * Update 执行口 —— 每帧走一次,里面用轨道值 / Alpha 布置画面
#   * Finished 执行口 —— 走完那一次(循环 / 往返不触发)
#   * 若干条 Track(浮点轨道),每条轨道可以有自己的曲线
#   * Alpha 0~1 —— 整条时间轴的进度
#
# 这里照搬这套接口,区别在**曲线怎么编**:节点 UI 里画不出曲线编辑器
# (见上面『为什么不做曲线编辑器』那段,结论仍然成立),但**能选一条
# 已经在 Blender 里画好的曲线** —— 导出时采样成 Godot 的 Curve 资源
# (.tres),运行时 `curve.sample(进度)` 拿值。所以"在 Blender 里编曲线"
# 这条需求是满足的,只是编辑器用 Blender 自带的曲线编辑器。
@N
class GKNodeTimeline(B.GKStatementNode):
    bl_idname = "GKNodeTimeline"
    bl_label = "定时_时间轴"
    bl_icon = "TIME"
    gk_category = "定时"
    gk_doc = ("**一条时间轴**(照虚幻的 Timeline):『执行』之后,每帧从"
              "『更新』口往外走一次,还能读 Alpha(0~1)和各条轨道的当前值;"
              "走完了从『已结束』口走一次。"
              "── 两个入口 ──"
              "『执行』= 从头开始播;『重播』= 一样是从头开始,专给"
              "'走进去再放一遍 / 按 R 再看一次'这种接法用。两个口各接一条线,"
              "互不打架(用户 2026-10-05:以前一个输入引脚只能接一条线,"
              "想重播只能在变量上绕一圈)。"
              "── 用法 ──"
              "把轨道值接给『定时_缓动属性』/『相机_缩放』这类节点,就得到"
              "'开门 / 镜头推进 / 灯光渐暗'那种联动;"
              "『曲线轨道』选一条 3D 曲线,输出就是**曲线上的位置** —— "
              "接『相机_位置』= 相机沿轨道运镜(你之前想的'弹簧臂挂曲线上'"
              "就是这条)。"
              "── 曲线在 Blender 里编 ──"
              "『时间轴曲线』管 Alpha 的形状(慢起 / 快停 / 自己画);"
              "每条轨道还能单独选一条曲线(管这条轨道自己的快慢)。"
              "曲线用『选曲线』挑场景里的曲线物体,导出时变成 Godot 的"
              "Curve 资源")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOW, "重播"))
    gk_prop_defs = (
        ("gk_time", "float", 1.0, "时长", "秒;时间轴从 0 走到头要多久"),
        ("gk_mode", "enum", "ONCE", "播放模式", "", (
            ("ONCE", "一次", "走完停下,并触发『已结束』"),
            ("LOOP", "循环", "走完从头再来(不触发『已结束』)"),
            ("PINGPONG", "往返", "到头再倒着走回来(不触发『已结束』)"),
        )),
        # ── 重复触发时怎么办(用户 2026-10-06)─────────────────────────
        # 用户原话:"重复触发时,它会重复开始动画,如果我想让它先放完再重复
        # ……(节点图看着有点复杂)"。
        # 以前只有一种行为:触发就从 0 重播 —— 想"等播完再放"就得在图上
        # 连一堆判断节点。现在一个下拉搞定。
        ("gk_repeat", "enum", "RESTART", "重复触发时", "", (
            ("RESTART", "重播(从头来)", "再触发就从 0 重新播 —— 以前一直是这个"),
            ("QUEUE", "等播完再重播", "正在播的时候触发只是记一笔,播完**立刻再放一遍**"),
            ("IGNORE", "播的时候忽略", "正在播的时候不理它,播完了才接受下一次"),
        )),
        ("gk_curve", "string", "", "时间轴曲线",
         "整条时间轴的进度曲线(画成从左到右;留空 = 匀速)"),
        # 属性名里含 "count" -> node_base 自动挂"改了就重建引脚"的回调
        ("gk_track_count", "int", 2, "轨道数量",
         "几条浮点轨道(0~4);每条都能填 从 / 到,还能单独选一条曲线",
         None, {"min": 0, "max": 4}),
        ("gk_track1_from", "float", 0.0, "轨道1 从", "进度 0 时的值"),
        ("gk_track1_to", "float", 1.0, "轨道1 到", "进度 1 时的值"),
        ("gk_track1_curve", "string", "", "轨道1 曲线",
         "这条轨道自己的曲线(留空 = 跟时间轴曲线)"),
        ("gk_track2_from", "float", 0.0, "轨道2 从", ""),
        ("gk_track2_to", "float", 1.0, "轨道2 到", ""),
        ("gk_track2_curve", "string", "", "轨道2 曲线", ""),
        ("gk_track3_from", "float", 0.0, "轨道3 从", ""),
        ("gk_track3_to", "float", 1.0, "轨道3 到", ""),
        ("gk_track3_curve", "string", "", "轨道3 曲线", ""),
        ("gk_track4_from", "float", 0.0, "轨道4 从", ""),
        ("gk_track4_to", "float", 1.0, "轨道4 到", ""),
        ("gk_track4_curve", "string", "", "轨道4 曲线", ""),
        ("gk_path_curve", "string", "", "曲线轨道",
         "一条 3D 曲线:『曲线位置』输出就是曲线上的点(相机沿轨道走)"),
    )
    gk_outputs = (
        S.pin(FLOW, "更新"),
        S.pin(FLOW, "已结束"),
        S.pin(FLOAT, "Alpha", 0.0),
        S.pin(BOOL, "已结束吗", False),
        S.pin(VECTOR, "曲线位置", (0.0, 0.0, 0.0)),
    )


    def track_count(self):
        """当前轨道数量(夹在 0~4)。"""
        return max(0, min(4, int(getattr(self, "gk_track_count", 2) or 0)))


    def build_sockets(self):
        """基础引脚 + 按『轨道数量』长出 `轨道N值` 输出。

        必须先 clear:这个函数会在"改数量"和"新建"时被调多次,
        不清的话引脚会**一层层叠上去**(离线测试里直接抓到重复引脚)。
        """
        self.inputs.clear()
        self.outputs.clear()
        B.build_declared_sockets(self)
        for index in range(1, self.track_count() + 1):
            socket = self.outputs.new(S.FLOAT[0], f"轨道{index}值")
            try:
                socket.default_value = 0.0
            except Exception:
                pass


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_time", text="时长")
        row.prop(self, "gk_mode", text="")
        # 重复触发时怎么办(用户 2026-10-06:"想让它先放完再重复")
        row = layout.row(align=True)
        row.prop(self, "gk_repeat", text="再触发")
        _draw_curve_row(layout, self, "gk_curve", "曲线")
        row = layout.row(align=True)
        row.prop(self, "gk_track_count", text="轨道")
        # 只画"当前用得到"的那几条轨道(数量调小了就不画,免得节点上留一堆
        # 改了没效果的输入框)
        for index in range(1, self.track_count() + 1):
            row = layout.row(align=True)
            row.prop(self, f"gk_track{index}_from", text=f"{index} 从")
            row.prop(self, f"gk_track{index}_to", text="到")
            pick = row.operator("gk.pick_curve", text="", icon="CURVE_DATA")
            pick.node_name = self.name
            pick.curve_prop = f"gk_track{index}_curve"
        _draw_curve_row(layout, self, "gk_path_curve", "轨道")
