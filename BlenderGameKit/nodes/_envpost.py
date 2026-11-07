"""游戏环境与后期 —— 节点模块(第一百二十五轮)。

用户原话(2026-10-15):
    "环境预设库(请改名字为游戏环境与后期)添加设置色调,连线-屏幕位置
     (整体?还是局部),添加睁眼效果-连线(持续时间),逻辑节点(是否,变量)...
     这个的开始卡片是默认环境卡片连过去,剩下的按照其他的逻辑节点图来设计"

四张卡(都用现成的执行流习惯,和游戏逻辑图一样连线):

    环境_默认环境    **入口卡**。执行流从这里出发 —— 相当于"这一关的环境 + 后期"
                     这条流水线的开始。后面接什么,就是这一关长什么样。
    环境_设置色调    整屏调色(亮度 / 对比度 / 饱和度 / 色调 + 色调量)。
                     『屏幕位置』是**可选**输入引脚:不接 = 整屏统一调;
                     接了 = 只作用在那块区域里(区域外保持原样)。
    环境_屏幕位置    纯取值卡(没有执行流):描述屏幕上一块区域
                     (圆形 / 矩形 + 中心 + 半径/宽高 + 羽化 + 内外反转)。
                     接到『设置色调』的『屏幕位置』引脚上才生效。
    环境_睁眼效果    上下眼皮张开:先是一条横缝,再向上下张开到全屏。
                     『持续时间』是**引脚**,可以连线(接变量/常量都行)。
                     默认**不随关卡自动播放** —— 只由逻辑图触发
                     (用户明确选的是"只在逻辑图里被触发时才播")。

⚠ 这四张卡只属于『游戏环境与后期』树(`gk_tree_type`),**不进游戏逻辑图**:
  它们描述的是"这一局长什么样",和角色行为是两回事 —— 用户选的就是
  "新建一张独立的图"。想让逻辑在某个时刻播睁眼,用『后期_播放睁眼』
  (那张卡在屏幕效果分类里,属于游戏逻辑树)。

⚠ 门的开关:`gk_input_props` 把引脚和节点上的属性绑起来,没连线时读属性、
  连了线读上游 —— 和『屏幕_闪白』那种卡同一个写法。
"""

from ._common import *  # noqa: F401,F403

# 这一套卡只在这张树里出现
ENV_TREE = "GKEnvLibraryTree"

# `gk_category` 决定它们在"添加节点"菜单里归到哪一栏。单独一栏,
# 免得和『显示设置』(那是场景设置卡)混在一起。
ENV_CATEGORY = "环境与后期"

# 睁眼效果的触发方式(存在卡片属性上)。
# ⚠ 必须是 `(标识, 显示名, 说明)` **三元组**,标识用 ASCII ——
#   Blender 的 EnumProperty 不接受纯字符串列表(实测报
#   "expected a tuple containing (identifier, name, description)"),
#   而且给纯中文标识以后写进 GDScript 会变成中文枚举值。
EYE_TRIGGERS = (
    ("LOGIC", "逻辑触发", "只在逻辑图的『后期_播放睁眼』被调用时才播"),
    ("AUTO", "进关卡自动播", "关卡一开始自动播一次"),)

# ── 作用域(第一百三十七轮,用户要求)────────────────────────────────
# 用户原话:"在环境睁眼这,应该加上作用域是全部关卡还是单个"。
# 这张『游戏环境与后期』图是**文件级**的(所有关卡共用一张),所以
# "只给某一关装"必须显式写出来。默认 ALL = 老行为一字不变。
EYE_SCOPES = (
    ("ALL", "全部关卡", "每一关都装这套后期"),
    ("ONE", "只这一关", "只在下面填的那一关装(别的关卡不装)"),
)


# ==========================================================================
# 入口卡
# ==========================================================================
@N
class GKNodeEnvDefault(B.GKEventNode):
    bl_idname = "GKNodeEnvDefault"
    bl_label = "环境_默认环境"
    bl_icon = "WORLD"
    gk_category = ENV_CATEGORY
    gk_tree_type = ENV_TREE
    gk_doc = ("这一关的**环境 + 后期**入口。执行流从这里出发,后面接"
              "『设置色调』『睁眼效果』这些卡 —— 和游戏逻辑图一样连线。"
              "一张图里放一张就够了(它是流水线的起点)")
    gk_outputs = (S.pin(FLOW, "执行"),)

    def draw_buttons(self, context, layout):
        layout.label(text="这一关的环境 + 后期从这里开始", icon="INFO")


# ==========================================================================
# 屏幕位置(纯取值;接到『设置色调』的『屏幕位置』引脚上才生效)
# ==========================================================================
@N
class GKNodeScreenPos(B.GKExpressionNode):
    bl_idname = "GKNodeScreenPos"
    bl_label = "环境_屏幕位置"
    bl_icon = "IMAGE_ALPHA"
    gk_category = ENV_CATEGORY
    gk_tree_type = ENV_TREE
    gk_doc = ("描述屏幕上一块区域。接到『环境_设置色调』的『屏幕位置』引脚上,"
              "调色就**只作用在这块区域里**(不接 = 整屏)。"
              "圆形用『半径』,矩形用『宽 / 高』;『羽化』让边缘过渡柔和")
    gk_outputs = (S.pin(ANY, "屏幕位置"),)
    gk_prop_defs = (
        ("gk_round", "bool", True, "圆形",
         "勾着 = 圆形(用半径);取消 = 矩形(用宽 / 高)"),
        ("gk_center", "float_vector", (0.5, 0.5, 0.0), "中心",
         "屏幕上的中心位置(0~1,左上角是 0,0;第三个分量不用)"),
        ("gk_radius", "float", 0.35, "半径", "圆形的半径(0~1,按屏幕短边算)"),
        ("gk_size", "float_vector", (0.5, 0.4, 0.0), "宽 / 高",
         "矩形的宽和高(0~1;第三个分量不用)"),
        ("gk_feather", "float", 0.1, "羽化", "边缘过渡宽度,0 = 硬边"),
        ("gk_invert", "bool", False, "内外反转",
         "勾上 = 反过来,**区域外**才调色(做暗角用这个)"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_round", text="圆形", toggle=True)
        row.prop(self, "gk_invert", text="反转", toggle=True)
        row = layout.row(align=True)
        row.prop(self, "gk_center", text="")
        if bool(getattr(self, "gk_round", True)):
            row.prop(self, "gk_radius", text="半径")
        else:
            row.prop(self, "gk_size", text="宽/高")
        layout.prop(self, "gk_feather", text="羽化")


# ==========================================================================
# 设置色调(整屏或局部)
# ==========================================================================
@N
class GKNodeEnvGrade(B.GKStatementNode):
    bl_idname = "GKNodeEnvGrade"
    bl_label = "环境_设置色调"
    bl_icon = "COLOR"
    gk_category = ENV_CATEGORY
    gk_tree_type = ENV_TREE
    gk_doc = ("给画面调色:亮度 / 对比度 / 饱和度 / 色调。"
              "『屏幕位置』**不接 = 整屏**;接了『环境_屏幕位置』就只作用在"
              "那块区域里(区域外保持原样)。"
              "跑在『环境_默认环境』后面")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(ANY, "屏幕位置"),
    )
    gk_prop_defs = (
        ("gk_brightness", "float", 1.0, "亮度", "1 = 不动"),
        ("gk_contrast", "float", 1.0, "对比度", "1 = 不动"),
        ("gk_saturation", "float", 1.0, "饱和度", "1 = 不动;0 = 黑白"),
        ("gk_tint", "color", (1.0, 1.0, 1.0, 1.0), "色调",
         "往这个颜色偏(白 = 不偏)"),
        ("gk_tint_amount", "float", 0.0, "色调量", "0 = 不偏色,1 = 完全染成色调"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_brightness", text="亮")
        row.prop(self, "gk_contrast", text="对比")
        row.prop(self, "gk_saturation", text="饱和")
        row = layout.row(align=True)
        row.prop(self, "gk_tint", text="")
        row.prop(self, "gk_tint_amount", text="色调量")
        if not bool(getattr(self.inputs.get("屏幕位置"), "is_linked", False)):
            layout.label(text="没接屏幕位置 = 整屏", icon="INFO")


# ==========================================================================
# 睁眼效果(上下眼皮张开)
# ==========================================================================
@N
class GKNodeEyeOpen(B.GKStatementNode):
    bl_idname = "GKNodeEyeOpen"
    bl_label = "环境_睁眼效果"
    bl_icon = "HIDE_OFF"
    gk_category = ENV_CATEGORY
    gk_tree_type = ENV_TREE
    gk_doc = ("上下眼皮张开:画面先是中间一条横缝,再向上下张开到全屏。"
              "『持续时间』是引脚,可以连线(接变量 / 常量都行)。"
              "默认**不随关卡自动播放** —— 只由逻辑图的『后期_播放睁眼』触发。"
              "把『触发方式』改成『进关卡自动播』就会在关卡开始时播一次")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "持续时间", 1.2),
    )
    gk_input_props = {"持续时间": "gk_duration"}
    gk_prop_defs = (
        ("gk_trigger", "enum", "LOGIC", "触发方式",
         "逻辑触发 = 只在『后期_播放睁眼』被调用时播;"
         "进关卡自动播 = 关卡一开始就播一次", EYE_TRIGGERS),
        # ── 作用域(第一百三十七轮,用户要求)────────────────────────────
        # 用户原话:"在环境睁眼这,应该加上作用域是全部关卡还是单个"。
        # 这张图是**文件级共享**的(所有关卡共用一张),所以"只给某一关"必须
        # 显式写出来 —— 默认『全部关卡』= 老行为一个字不变。
        ("gk_scope", "enum", "ALL", "作用域",
         "全部关卡 = 每一关都装这套后期;只这一关 = 别的关卡不装"
         "(选它要填下面的『关卡』)", EYE_SCOPES),
        ("gk_scene", "string", "", "关卡",
         "『作用域 = 只这一关』时生效:填 Blender 里的场景名(比如 大堂)"),
        ("gk_duration", "float", 1.2, "持续时间", "从闭到全开用多少秒"),
        ("gk_slit", "float", 0.0, "起始缝高",
         "0 = 从完全闭合开始;留一点(比如 0.04)= 一开始有条缝"),
        ("gk_color", "color", (0.0, 0.0, 0.0, 1.0), "眼皮颜色",
         "盖住画面的颜色,默认黑"),
        ("gk_hold", "float", 0.0, "闭合保持", "张开之前先全闭着停多少秒"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_trigger", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_scope", text="")
        if str(getattr(self, "gk_scope", "ALL")) == "ONE":
            row.prop(self, "gk_scene", text="")
            # 把"能填什么"直接写出来 —— 场景名打错一个字就静默不装,
            # 与其让用户去大纲里翻,不如在这里列一遍(第一百三十八轮)。
            names = []
            try:
                import bpy
                names = [scene.name for scene in bpy.data.scenes]
            except Exception:                              # noqa: BLE001
                names = []
            if names:
                layout.label(text="当前文件的关卡:" + "、".join(names[:6]),
                             icon="SCENE_DATA")
            else:
                layout.label(text="填一个场景名(比如 大堂)", icon="INFO")
        row = layout.row(align=True)
        row.prop(self, "gk_duration", text="时长")
        row.prop(self, "gk_slit", text="起始缝")
        row.prop(self, "gk_hold", text="保持")
        layout.prop(self, "gk_color", text="")
        if str(getattr(self, "gk_trigger", "")) == "LOGIC":
            layout.label(text="由逻辑图的『后期_播放睁眼』触发", icon="INFO")


# ==========================================================================
# 逻辑图里的触发卡(属于游戏逻辑树,不是这张图)
# ==========================================================================
@N
class GKNodeEyeOpenPlay(B.GKStatementNode):
    bl_idname = "GKNodeEyeOpenPlay"
    bl_label = "后期_播放睁眼"
    bl_icon = "PLAY"
    gk_category = "屏幕效果"
    # ⚠ 显式写出来,不吃基类的默认值:离线测试的桩基类**没有** `gk_tree_type`
    #   (真 Blender 里那个默认值定义在 `if _USE_BPY_BASE:` 分支内),
    #   不写就会 AttributeError。顺带也把"它属于游戏逻辑树"写在脸上 ——
    #   上面四张环境卡属于 GKEnvLibraryTree,这张反过来,是最容易搞混的一处。
    gk_tree_type = "GKGameLogicTree"
    gk_doc = ("播一次『睁眼效果』(上下眼皮张开)。参数在『游戏环境与后期』"
              "那张图的『环境_睁眼效果』卡上 —— 这张卡只管**什么时候播**。"
              "典型用法:角色醒来 / 被击倒后爬起来 / 传送之后\n"
              "── 引脚(第一百三十七轮)──\n"
              "『时长』接上/填了就**用这个秒数**(盖过卡上的『持续时间』);"
              "空着 = 用卡上的值。")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "时长"))

    def draw_buttons(self, context, layout):
        layout.label(text="播一次眼皮张开", icon="PLAY")
        layout.label(text="『时长』空着 = 用『环境_睁眼效果』卡上的持续时间",
                     icon="INFO")


# ==========================================================================
# 天空盒(第一百三十三轮:昼夜交替 / 天空盒 / 太阳 / 云层)
# ==========================================================================
# 用户原话(2026-10-15):
#     "天空盒,时间流逝,昼夜交替,太阳,云层等这些还没有设计啊,这些都可以
#      添加到游戏环境与后期的右键菜单里,如随着时间流逝,从卡通日间到
#      卡通夜晚,当然这时间等可以自定义的"
# 四个追问用户全选了推荐项:环境图里配好、进关卡自动流逝;太阳**真的按
# 时刻转**;云层是**天空着色器里的流动程序化噪声**(不依赖贴图);关键帧
# 用**多档**(0点夜晚 / 6点白天 / 18点黄昏),之间插值、循环闭合。
#
# ⚠ 天空是 `shaders/gk_sky.gdshader`(Godot 的 `shader_type sky`):
#   程序化噪声画云,所以**一个文件**就够,不用随包带好几张贴图。
# ⚠ Blender 预览只能按"当前时刻铺个渐变色"近似,画不出逐像素的噪声云 ——
#   这条如实写在 `godot_look.describe()` 里(不装成"预览=游戏")。
SKY_SHADER_PATH = "res://shaders/gk_sky.gdshader"

# 关键帧里能填的"氛围档"(导出侧 `asset_export._DAYNIGHT_PRESETS` 是同一套名字)
DAY_PRESETS = ("夜晚", "清晨", "白天", "黄昏")


@N
class GKNodeSkyBox(B.GKStatementNode):
    bl_idname = "GKNodeSkyBox"
    bl_label = "环境_天空盒"
    bl_icon = "MAT_SPHERE_SKY"
    gk_category = ENV_CATEGORY
    gk_tree_type = ENV_TREE
    gk_doc = ("用**程序化天空**当背景(不依赖贴图):天顶色 / 地平线色 / 地面"
              "颜色,再加一圈霞光。接在『环境_默认环境』后面。"
              "要云层就再接一张『环境_云层』;要让天色随时间变,接"
              "『环境_昼夜循环』(它会每帧改写这几个颜色)")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_sky_top", "color", (0.25, 0.45, 0.85, 1.0), "天顶色",
         "头顶那一片的颜色"),
        ("gk_sky_horizon", "color", (0.72, 0.82, 0.95, 1.0), "地平线色",
         "贴近地平线那一圈的颜色(天光最亮的地方)"),
        ("gk_ground_color", "color", (0.12, 0.12, 0.14, 1.0), "地面色",
         "地平线**以下**那半边的颜色(免得天空色漏到脚下)"),
        ("gk_sun_size", "float", 0.06, "太阳盘大小",
         "太阳盘的角半径(弧度);0 = 不画太阳盘"),
        ("gk_horizon_glow", "float", 0.0, "霞光强度",
         "地平线那圈橙红边,黄昏 / 清晨拧大它(0 = 不画)"),
        ("gk_glow_color", "color", (1.0, 0.55, 0.28, 1.0), "霞光颜色",
         "霞光往什么颜色偏"),
        # ── 星星(第一百五十一轮第四轮)──────────────────────────────
        # 用户原话:"实际运行下……星星也没有"。天空着色器现在用球面哈希散点
        # 画星星,只在天空够暗时出现(白天 / 黄昏自动隐去)。
        ("gk_star_amount", "float", 0.7, "星星数量",
         "0 = 没有星星,1 = 满天星(只在夜空出现)"),
        ("gk_star_brightness", "float", 1.0, "星星亮度",
         "星星的亮度倍率(1 = 正常,2 = 更亮)"),
        ("gk_star_twinkle", "float", 1.0, "星星闪动",
         "0 = 恒定亮着,1 = 正常闪烁,2 = 很夸张(每颗星相位/频率不同)"),
        # ── 南北半球(第一百五十二轮)────────────────────────────────
        # 用户要"南/北半球天空的切换开关"。两个半球的星空不是同一片天
        # 转个角度:北天有北极星(几乎不动)、南天看不到北极星但有大小
        # 麦哲伦云,星图本身也是镜像的。所以做成一个真的换布局的开关。
        ("gk_sky_hemisphere", "enum", "NORTH", "半球", "",
         (("NORTH", "北半球", "北极星 + 北天星图"),
          ("SOUTH", "南半球", "麦哲伦云 + 南天星图"))),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_sky_top", text="")
        row.prop(self, "gk_sky_horizon", text="")
        row.prop(self, "gk_ground_color", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_sun_size", text="太阳盘")
        row.prop(self, "gk_horizon_glow", text="霞光")
        layout.prop(self, "gk_glow_color", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_star_amount", text="星星")
        row.prop(self, "gk_star_brightness", text="亮度")
        row.prop(self, "gk_star_twinkle", text="闪动")
        layout.prop(self, "gk_sky_hemisphere", text="半球")


@N
class GKNodeClouds(B.GKStatementNode):
    bl_idname = "GKNodeClouds"
    bl_label = "环境_云层"
    # ⚠ Blender 的图标表里**没有** "MAT_SPHERE_SKY" / "MOD_CLOUD"(实测查过:
    #   带 CLOUD 的只有 OUTLINER_DATA_POINTCLOUD 那几个点云图标)。
    #   写错的名字在画菜单那一刻才抛,只往状态栏打一行 —— 所以这一关
    #   (`tests/blender_icons.py`)是全扫的。
    bl_icon = "OUTLINER_DATA_POINTCLOUD"
    gk_category = ENV_CATEGORY
    gk_tree_type = ENV_TREE
    gk_doc = ("天空上的**流动云层**:程序化噪声画的,不依赖贴图,会自己飘。"
              "接在『环境_天空盒』后面(没接天空盒也会自动给这一关打开一个"
              "程序化天空)。『云量』= 遮多少天,『云速』= 飘多快,"
              "『云尺度』= 云团多大")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_cloud_amount", "float", 0.45, "云量",
         "0 = 一丝云都没有,1 = 满天都是"),
        ("gk_cloud_speed", "float", 0.06, "云速", "云飘得多快(0 = 定住)"),
        ("gk_cloud_scale", "float", 3.0, "云尺度",
         "越大云团越小、越密;越小云团越大"),
        ("gk_cloud_height", "float", 0.06, "云层高度",
         "云底压在地平线以上多少(以 sin(仰角) 计;0.06 ≈ 3.4°,"
         "太小云会糊到地平线,太大只剩头顶一片)"),
        ("gk_cloud_softness", "float", 0.22, "云边柔和",
         "云团边缘过渡多宽;卡通风格留 0.15~0.3 比较像手绘"),
        ("gk_cloud_light", "color", (1.0, 0.98, 0.95, 1.0), "云亮面",
         "被太阳照到的那一侧"),
        ("gk_cloud_dark", "color", (0.45, 0.48, 0.58, 1.0), "云暗面",
         "背光那一边(夜里整体压暗)"),
        # ── 云影(第一百五十轮)──────────────────────────────────────
        # 用户原话:"云层上,会缓缓移动而影响月光照射"。
        # 云飘过月亮时月光会一暗一明;这里按云量 / 云速算一层**慢速遮罩**,
        # 乘在月光亮度上。0 = 关(老场景月光不变),0.3~0.6 是"薄云飘过"。
        ("gk_cloud_shadow", "float", 0.0, "云影(遮月光)",
         "云飘过月亮时月光变暗多少;0 = 不影响(老行为),"
         "0.3~0.6 = 薄云缓缓飘过,1 = 可以完全遮黑"),
        # ── 白天云量增加(第一百五十一轮第五轮)──────────────────────
        # 用户:"之后是太阳的出现,和云层的增多"。接昼夜循环后,
        # 白天的云量在这个值上再加一道;0 = 老行为(昼夜不影响云量)。
        ("gk_cloud_day_boost", "float", 0.0, "白天云量增加",
         "0 = 老行为;0.25 = 白天比夜里多两成半的云(昼夜循环才生效)"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_cloud_amount", text="云量")
        row.prop(self, "gk_cloud_speed", text="云速")
        row.prop(self, "gk_cloud_scale", text="尺度")
        row = layout.row(align=True)
        row.prop(self, "gk_cloud_light", text="")
        row.prop(self, "gk_cloud_dark", text="")
        layout.prop(self, "gk_cloud_shadow", text="云影(遮月光)")
        layout.prop(self, "gk_cloud_day_boost", text="白天云量增加")


@N
class GKNodeSun(B.GKStatementNode):
    bl_idname = "GKNodeSun"
    bl_label = "环境_太阳"
    gk_category = ENV_CATEGORY
    gk_tree_type = ENV_TREE
    gk_doc = ("指定**哪盏平行光是太阳**,以及它按不按时刻转。"
              "『按时刻转』= 影子方向真的跟着走(早上从东边、中午在头顶、"
              "傍晚从西边),不是只换颜色。名字留空 = 用场景里第一盏平行光。"
              "接了『环境_昼夜循环』之后,颜色 / 强度由关键帧决定,"
              "这里的『强度倍数』是在那之上再乘一道")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_sun_name", "string", "", "平行光名字",
         "留空 = 用场景里第一盏平行光(不按名字找也行,名字只是让你选中某一盏)"),
        ("gk_sun_orbit", "bool", True, "按时刻转",
         "勾着 = 太阳随时间绕天转(影子方向跟着变);取消 = 方向固定,只换颜色"),
        ("gk_sun_latitude", "float", 45.0, "正午高度角",
         "中午太阳离地平线多少度(35~60 比较自然;90 = 正头顶)"),
        ("gk_sun_tilt", "float", 0.0, "方位偏移",
         "整条日轨绕一圈偏多少度(让影子方向合你场景的朝向)"),
        ("gk_sun_scale", "float", 1.0, "强度倍数",
         "在关键帧给的强度上再乘一道(0.5 = 暗一半)"),
        ("gk_sun_shadow", "bool", True, "投影",
         "关掉 = 这盏太阳不投影子(影子开销大,夜景可以关)"),
        # ── 南北半球(第一百五十三轮)───────────────────────────────
        # 北半球:太阳从东升、**偏南**划过天空、西落(正午影子朝北);
        # 南半球:同一套时刻,整条日轨绕天顶转 180° —— 太阳偏北,影子朝南。
        # 只翻"划过的方向",不改高度角(高度角还是由『正午高度角』决定)。
        ("gk_sun_hemisphere", "enum", "NORTH", "半球", "",
         (("NORTH", "北半球", "太阳偏南划过,影子朝北"),
          ("SOUTH", "南半球", "太阳偏北划过,影子朝南"))),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_sun_name", text="", icon="LIGHT_SUN")
        row.prop(self, "gk_sun_orbit", text="按时刻转", toggle=True)
        row = layout.row(align=True)
        row.prop(self, "gk_sun_latitude", text="高度角")
        row.prop(self, "gk_sun_tilt", text="方位")
        row.prop(self, "gk_sun_scale", text="倍数")
        layout.prop(self, "gk_sun_hemisphere", text="半球")


@N
class GKNodeDayNight(B.GKStatementNode):
    bl_idname = "GKNodeDayNight"
    bl_label = "环境_昼夜循环"
    bl_icon = "TIME"
    gk_category = ENV_CATEGORY
    gk_tree_type = ENV_TREE
    gk_doc = ("时间流逝 + 昼夜交替:**进关卡自动走**,天色 / 太阳颜色与强度 / "
              "环境光 / 雾色都在关键帧之间插值,到点循环回第一帧(闭合)。"
              "关键帧写成『时刻=氛围档』,用 | 隔开 —— 默认 "
              "`0=夜晚|6=清晨|12=白天|18=黄昏`。氛围档可选:"
              "夜晚 / 清晨 / 白天 / 黄昏。"
              "想在游戏里暂停 / 加速 / 跳到某个时刻,用逻辑图的"
              "『环境_昼夜设置』")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_keyframes", "string", "0=夜晚|6=清晨|12=白天|18=黄昏", "关键帧",
         "时刻=氛围档,用 | 隔开;不必排序,导出时会自动排。"
         "氛围档可填 夜晚 / 清晨 / 白天 / 黄昏"),
        ("gk_day_seconds", "float", 120.0, "一天多少秒",
         "游戏里过完一整天要多少**现实秒**(120 = 两分钟一昼夜)"),
        ("gk_start_hour", "float", 8.0, "起始时刻",
         "进关卡时是几点(0~24,可以填小数)"),
        ("gk_auto", "bool", True, "自动流逝",
         "勾着 = 一进关卡时间就往前走;取消 = 停在起始时刻"
         "(留给逻辑图用『环境_昼夜设置』手动推)"),
        # ── 只改天空(第一百五十一轮第五轮)──────────────────────────
        # 恐怖示例的走廊 / 大厅有手工调好的环境光与雾;如果昼夜循环
        # 把这两项也按预设覆盖掉,原氛围会被冲淡。勾上后它只改天色、
        # 太阳 / 月亮、云量,环境光和雾保持场景设置。
        ("gk_sky_only", "bool", False, "只改天空",
         "勾着 = 不动环境光 / 雾,只让天色、太阳、月亮、云量随时间变"),
        ("gk_brightness", "float", 1.0, "整体亮度",
         "把关键帧给的天光 / 太阳强度一起乘一道(0.5 = 整条时间轴压暗一半)"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_keyframes", text="")
        row = layout.row(align=True)
        row.prop(self, "gk_day_seconds", text="一天(秒)")
        row.prop(self, "gk_start_hour", text="起点(时)")
        row = layout.row(align=True)
        row.prop(self, "gk_auto", text="自动流逝", toggle=True)
        row.prop(self, "gk_sky_only", text="只改天空", toggle=True)
        row.prop(self, "gk_brightness", text="亮度")
        layout.label(text="氛围档:夜晚 / 清晨 / 白天 / 黄昏", icon="INFO")


# ==========================================================================
# 月亮 / 天气(第一百四十二轮)
# ==========================================================================
WEATHER_KINDS = (
    ("CLEAR", "晴", "晴天:不留雾、云少"),
    ("CLOUDY", "阴", "阴天:云多、光偏冷偏暗"),
    ("RAINY", "雨", "雨天:浓雾 + 冷色 + 云层压暗"),
    ("FOGGY", "雾", "雾天:雾最浓、泛光压低"),
    ("WINDY", "大风", "大风:云被吹快、空中飘尘、雾被吹散一点"),
    ("SNOWY", "雪", "雪:飘雪 + 中等雾,环境光偏亮(雪面反光)"),
)


@N
class GKNodeMoon(B.GKStatementNode):
    bl_idname = "GKNodeMoon"
    bl_label = "环境_月亮"
    bl_icon = "LIGHT_SUN"
    gk_category = ENV_CATEGORY
    gk_tree_type = ENV_TREE
    gk_doc = ("夜里的一盏平行光(月光)+ 天空里的一轮月盘。"
              "『随昼夜升落』勾着 = 和太阳反着走(太阳落下去它就升上来),"
              "亮度按「夜晚程度」淡入淡出;月相是把圆盘吃掉一块(1=满月)。")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_moon_enabled", "bool", True, "启用", "关掉 = 不建月光、不画月盘"),
        ("gk_moon_color", "color", (0.80, 0.86, 1.0, 1.0), "颜色", "月光颜色"),
        ("gk_moon_energy", "float", 0.35, "强度",
         "月光亮度(相对太阳的出厂强度;夜里 0.2~0.6 比较自然)"),
        ("gk_moon_size", "float", 0.06, "月盘大小",
         "月盘的角半径(弧度):0.06 ≈ 3.4° 半径 / 6.9° 直径(0.02~0.10)"),
        ("gk_moon_phase", "float", 1.0, "月相",
         "1 = 满月,0 = 新月;中间的数是缺掉的多少"),
        # ── 第一百五十一轮:月相动画(用户:"从圆满到弯月的动画")────────
        ("gk_moon_phase_animate", "bool", False, "月相动画",
         "勾着 = 月相随环境时钟在『满月 ↔ 最弯』之间循环(不用逻辑图驱动)"),
        ("gk_moon_phase_seconds", "float", 40.0, "月相周期(秒)",
         "一个完整的 满月 → 弯月 → 满月 循环要多少秒"),
        ("gk_moon_phase_min", "float", 0.15, "最弯月相",
         "循环到最弯时保留多少月面(0.15 = 一弯细月,0 = 全黑)"),
        ("gk_moon_shadow", "bool", False, "月光投影",
         "勾着 = 月光也投影子(夜景开销大,默认关)"),
        ("gk_moon_orbit", "bool", True, "随昼夜升落",
         "勾着 = 走一条夜路:18 点东升 → 半夜在正北最高 → 6 点西落;"
         "取消 = 用下面两个角固定方向"),
        ("gk_moon_azimuth", "float", 0.0, "方位角",
         "固定方向时,月亮从哪个方向来(度,Godot 世界):"
         "0 = 北(-Z),90 = 东(+X),180 = 南,270 = 西"),
        ("gk_moon_elevation", "float", 30.0, "高度角",
         "固定方向时,月亮挂多高(度):0 = 地平线,90 = 正头顶;"
         "『随昼夜升落』勾着时 = 月亮在半夜最高挂多高"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_moon_enabled", text="", icon="LIGHT_SUN")
        row.prop(self, "gk_moon_energy", text="强度")
        row.prop(self, "gk_moon_phase", text="月相")
        row = layout.row(align=True)
        row.prop(self, "gk_moon_phase_animate", text="月相动画", toggle=True)
        if self.gk_moon_phase_animate:
            row.prop(self, "gk_moon_phase_seconds", text="周期(秒)")
            layout.prop(self, "gk_moon_phase_min", text="最弯月相")
        row = layout.row(align=True)
        row.prop(self, "gk_moon_size", text="盘大小")
        row.prop(self, "gk_moon_orbit", text="升落", toggle=True)
        row.prop(self, "gk_moon_shadow", text="投影", toggle=True)
        if not self.gk_moon_orbit:
            row = layout.row(align=True)
            row.prop(self, "gk_moon_azimuth", text="方位")
            row.prop(self, "gk_moon_elevation", text="高度")


@N
class GKNodeWeather(B.GKStatementNode):
    bl_idname = "GKNodeWeather"
    bl_label = "环境_天气"
    bl_icon = "MAT_SPHERE_SKY"
    gk_category = ENV_CATEGORY
    gk_tree_type = ENV_TREE
    gk_doc = ("这一关的天气:晴 / 阴 / 雨 / 雾。"
              "天气是一组运行时参数(雾浓度 / 云量 / 环境光 / 泛光),"
              "逻辑图里用『环境_切换天气』可以在游戏里换。")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_weather", "enum", "CLEAR", "起始天气",
         "进关卡时的天气", WEATHER_KINDS),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_weather", text="")


# ==========================================================================
# 逻辑图里的控制卡(属于游戏逻辑树)
# ==========================================================================
@N
class GKNodeEnvHourEvent(B.GKEventNode):
    bl_idname = "GKNodeEnvHourEvent"
    bl_label = "环境_当时间到"
    bl_icon = "TIME"
    gk_category = ENV_CATEGORY
    gk_tree_type = "GKGameLogicTree"
    gk_doc = ("时间走到某个整点就执行一次(比如 19 点天黑了 -> 熄灯)。"
              "『每天重复』勾着 = 每天这个点都会触发;取消 = 整局只触发一次。"
              "⚠ 只认**整点**(0~23)。")
    gk_inputs = ()
    gk_outputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_hour_target", "float", 19.0, "时刻", "几点触发(整点,0~23)"),
        ("gk_hour_daily", "bool", True, "每天重复",
         "勾着 = 每天这个点都触发;取消 = 整局只触发一次"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_hour_target", text="")
        row.prop(self, "gk_hour_daily", text="每天", toggle=True)


DAYNIGHT_ACTIONS = (
    ("SET_TIME", "跳到时刻", "把这一关的时间跳到某个时刻(0~24,可以填小数)"),
    ("PAUSE", "暂停", "时间停住(天色 / 影子都定在当前时刻)"),
    ("RESUME", "继续", "接着走"),
    ("SPEED", "设流速", "倍速:2 = 一天减半,0 = 停住"),
)


@N
class GKNodeDayNightSet(B.GKStatementNode):
    bl_idname = "GKNodeDayNightSet"
    bl_label = "环境_昼夜设置"
    bl_icon = "TIME"
    gk_category = ENV_CATEGORY
    # ⚠ 和『后期_播放睁眼』同一个道理:这张卡在**玩法图**里,
    #   而昼夜循环跑在环境脚本上 —— 两边靠 `gk_daynight` 组接头。
    gk_tree_type = "GKGameLogicTree"
    gk_doc = ("改这一关的时间:跳到某个时刻 / 暂停 / 继续 / 设流速(倍速)。"
              "参数在『游戏环境与后期』图的『环境_昼夜循环』卡上配;"
              "这张卡只管**什么时候改**。典型用法:进屋暂停时间、"
              "过场时跳一觉到天亮、宝箱开了加速")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(FLOAT, "值", 12.0),
    )
    gk_input_props = {"值": "gk_value"}
    gk_prop_defs = (
        ("gk_action", "enum", "SET_TIME", "动作",
         "跳时刻 / 暂停 / 继续 / 设流速", DAYNIGHT_ACTIONS),
        ("gk_value", "float", 12.0, "值",
         "『跳到时刻』= 几点(0~24);『设流速』= 倍速(1 = 正常)。"
         "暂停 / 继续用不到这个值"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_action", text="")
        action = str(getattr(self, "gk_action", "SET_TIME"))
        if action in ("SET_TIME", "SPEED"):
            if not bool(getattr(self.inputs.get("值"), "is_linked", False)):
                row.prop(self, "gk_value", text="")


@N
class GKNodeEnvAlternate(B.GKStatementNode):
    bl_idname = "GKNodeEnvAlternate"
    bl_label = "环境_昼夜交替"
    bl_icon = "TIME"
    gk_category = ENV_CATEGORY
    gk_tree_type = "GKGameLogicTree"
    gk_doc = ("**让这一关在白天和夜晚之间自动来回过渡。**\n"
              "执行一次后,环境时钟会从当前时刻慢慢走到『白天时刻』,"
              "停留一会儿,再慢慢走到『夜晚时刻』,然后一直循环 —— "
              "不用你自己写定时器 / 变量。\n"
              "『过渡秒数』= 走完一次白天→夜晚要多久;『停留秒数』= "
              "到了之后停多久。\n"
              "⚠ 需要『游戏环境与后期』图里有『环境_昼夜循环』卡"
              "(『自动流逝』可以关掉,交替会接管时间)。想停就用"
              "『环境_停止交替』。")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_outputs = (S.pin(FLOW, "完成"),)
    gk_prop_defs = (
        ("gk_alt_day", "float", 7.0, "白天时刻", "白天停在几点(0~24)"),
        ("gk_alt_night", "float", 21.0, "夜晚时刻", "夜晚停在几点(0~24)"),
        ("gk_alt_fade", "float", 12.0, "过渡秒数", "从白天走到夜晚要多少秒"),
        ("gk_alt_hold", "float", 8.0, "停留秒数", "到了白天 / 夜晚后停多久"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_alt_day", text="白天")
        row.prop(self, "gk_alt_night", text="夜晚")
        row = layout.row(align=True)
        row.prop(self, "gk_alt_fade", text="过渡(秒)")
        row.prop(self, "gk_alt_hold", text="停留(秒)")


@N
class GKNodeEnvAlternateStop(B.GKStatementNode):
    bl_idname = "GKNodeEnvAlternateStop"
    bl_label = "环境_停止交替"
    bl_icon = "PAUSE"
    gk_category = ENV_CATEGORY
    gk_tree_type = "GKGameLogicTree"
    gk_doc = ("停掉『环境_昼夜交替』的自动循环,时间就停在此刻。"
              "之后可以用『环境_昼夜设置』接管(跳时刻 / 暂停 / 加速)。")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_outputs = (S.pin(FLOW, "完成"),)

    def draw_buttons(self, context, layout):
        layout.label(text="停在此刻;时间不再自动来回", icon="INFO")


GK_CLASSES_ENVPOST = ()


# ==========================================================================
# 逻辑图 <-> 环境脚本 的对外接口(第一百四十二轮)
# ==========================================================================
# ── 为什么这些卡在**逻辑图**里、而参数在环境图里 ────────────────────
# 跨节点树不能连线(Blender 的限制),所以两边靠**运行时组**接头:
# 环境脚本在 `_gk_daynight_setup()` 里 `add_to_group("gk_env")`,
# 逻辑图这几张卡按组找它、调它公开的方法 —— 和『后期_播放睁眼』
# 按 `gk_post` 组找后期层是同一个套路。
ENV_READ_KINDS = (
    ("HOUR", "时间", "当前时刻(0~24,小数)"),
    ("STATE", "档位", "当前氛围档:夜晚 / 清晨 / 白天 / 黄昏"),
    ("IS_DAY", "是白天", "1.0 = 白天,0.0 = 夜晚(够做条件判断)"),
    ("NIGHT", "夜晚程度", "0 = 全白天,1 = 全黑夜(拿来做淡入淡出)"),
    ("WEATHER", "天气", "当前天气:晴 / 阴 / 雨 / 雾"),
)


@N
class GKNodeEnvRead(B.GKStatementNode):
    bl_idname = "GKNodeEnvRead"
    bl_label = "环境_读取"
    bl_icon = "INFO"
    gk_category = ENV_CATEGORY
    gk_tree_type = "GKGameLogicTree"
    gk_doc = ("把环境的状态读进一个**变量**里:时间 / 档位 / 是否白天 / "
              "夜晚程度 / 天气。读到的值后面随便接条件、显示、切歌都行。"
              "『档位』和『天气』读到的是**名字**(字符串),其它是数字。")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_read_kind", "enum", "HOUR", "数据", "要读哪一项",
         ENV_READ_KINDS),
        ("gk_var_name", "string", "环境时间", "变量名",
         "读到哪个变量里(不存在会自动建)"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_read_kind", text="")
        row.prop(self, "gk_var_name", text="")


@N
class GKNodeEnvSetTime(B.GKStatementNode):
    bl_idname = "GKNodeEnvSetTime"
    bl_label = "环境_设置时间"
    bl_icon = "TIME"
    gk_category = ENV_CATEGORY
    gk_tree_type = "GKGameLogicTree"
    gk_doc = ("把这一关的时间跳到某个时刻(0~24,可以填小数)。"
              "天色 / 太阳 / 影子会立刻按新时刻重算。")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "时刻", 12.0))
    gk_input_props = {"时刻": "gk_hour"}
    gk_prop_defs = (
        ("gk_hour", "float", 12.0, "时刻", "跳到几点(0~24)"),
    )

    def draw_buttons(self, context, layout):
        if not bool(getattr(self.inputs.get("时刻"), "is_linked", False)):
            layout.prop(self, "gk_hour", text="")


@N
class GKNodeEnvSetSpeed(B.GKStatementNode):
    bl_idname = "GKNodeEnvSetSpeed"
    bl_label = "环境_设置流速"
    bl_icon = "TIME"
    gk_category = ENV_CATEGORY
    gk_tree_type = "GKGameLogicTree"
    gk_doc = ("时间走多快:1 = 正常,2 = 快一倍,0 = 停住。"
              "和『环境_暂停时钟』的区别:这个改的是**倍速**,那个是开关。")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "倍速", 1.0))
    gk_input_props = {"倍速": "gk_speed"}
    gk_prop_defs = (
        ("gk_speed", "float", 1.0, "倍速", "1 = 正常,2 = 快一倍,0 = 停"),
    )

    def draw_buttons(self, context, layout):
        if not bool(getattr(self.inputs.get("倍速"), "is_linked", False)):
            layout.prop(self, "gk_speed", text="")


ENV_CLOCK_KINDS = (
    ("PAUSE", "暂停时钟", "时间停住(天色 / 影子定在当前时刻)"),
    ("RESUME", "继续时钟", "接着走"),
)


@N
class GKNodeEnvPause(B.GKStatementNode):
    bl_idname = "GKNodeEnvClock"
    bl_label = "环境_暂停时钟"
    bl_icon = "PAUSE"
    gk_category = ENV_CATEGORY
    gk_tree_type = "GKGameLogicTree"
    gk_doc = ("暂停 / 继续这一关的时间。暂停后天色、太阳、影子都定住,"
              "但场景照常玩(和 Esc 暂停不是一回事)。")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_clock_action", "enum", "PAUSE", "动作", "暂停 / 继续",
         ENV_CLOCK_KINDS),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_clock_action", text="")


@N
class GKNodeEnvWeather(B.GKStatementNode):
    bl_idname = "GKNodeEnvSetWeather"
    bl_label = "环境_切换天气"
    bl_icon = "MAT_SPHERE_SKY"
    gk_category = ENV_CATEGORY
    gk_tree_type = "GKGameLogicTree"
    gk_doc = ("在游戏里换天气:晴 / 阴 / 雨 / 雾。"
              "天气会改雾浓度 / 云量 / 环境光 / 泛光 —— 换的时候有过渡,不是硬切。")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_weather_set", "enum", "RAINY", "天气", "换成哪种天气",
         WEATHER_KINDS),
        ("gk_weather_fade", "float", 2.0, "过渡秒数",
         "多少秒过渡过去(0 = 立刻换)"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_weather_set", text="")
        row.prop(self, "gk_weather_fade", text="秒")