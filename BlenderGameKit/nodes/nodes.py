"""GK 节点集合(**门面**)。

原来 5280 行的单文件按领域拆到了同目录的 `_*.py`,本文件只做三件事:
  1. 按**原始定义顺序**导入各领域模块 —— 顺序决定 `ALL_CLASSES` 的排列,
     直接影响『添加节点』菜单;拆错了顺序用户就会觉得节点位置变了。
  2. 把各模块命名空间合并回来 —— 保持 `node_lib.GKNodeXxx`、
     `node_lib.action_category` 等**老写法零感知**(和 codegen 包同一套做法)。
  3. 定义导出顺序表(分类顺序 / 二级分类 / 常用短名单)。
"""

from . import _common
from ._common import *  # noqa: F401,F403
# 顺序 = 原文件里的定义顺序,不能按字母排
from . import _events
from . import _flow
from . import _movement
from . import _animation
from . import _instances
from . import _fx
from . import _variables
from . import _input
from . import _expressions
from . import _display
from . import _containers
from . import _functions
from . import _ai
from . import _displayset
from . import _assets
# 音乐设置卡(第八十六轮:『游戏音乐』树)—— 和 _displayset 同一类设置卡
from . import _musicset
# 网络联机(第一百轮:房间 + 自定义消息)—— ENet + @rpc
from . import _network


def _merge_namespaces(*modules):
    """把子模块里所有非 dunder 名字并入本模块(含单下划线私有名)。"""
    for module in modules:
        for key, value in vars(module).items():
            if not key.startswith("__"):
                globals()[key] = value


_merge_namespaces(_common, _events, _flow, _movement, _animation,
                  _instances, _fx, _variables, _input, _expressions,
                  _display, _containers, _functions, _ai, _displayset,
                  _assets, _musicset, _network)
del _merge_namespaces



# ==========================================================================
# 动画树里能用的"取值"节点 —— 一份名单,改这里就够了
# ==========================================================================
# ── 为什么用名单而不是在每个类上写 gk_tree_type ──────────────────────
# 因为要回答的问题只有一个是重要的:"动画树的转场条件里到底能拿什么?"
# 写在 25 个类里的话,这个问题要翻遍 3600 行才答得上来,而且**很容易漏**。
#
# 判据:**纯取值**(没有执行流、什么时候求值都安全)。执行流节点
# (打印/移动/生成实例…)不放进来 —— 动画树是"每帧算一次条件"的图,
# 塞语句进去会让人以为能写逻辑,而且求值时机完全不可控。
#
# 用户 2026-10-03 的要求:"转场条件要能像蓝图那样就地拼"
# (他的实际报错是动画树里加『常量_数值』直接 RuntimeError)。
ANIM_TREE_VALUE_NODES = (
    # 常量 / 数学 / 向量 / 逻辑 / 转换 —— 转场条件的基本材料
    "GKNodeConstFloat", "GKNodeConstText", "GKNodeConstBool",
    "GKNodeConstVector",
    "GKNodeMath", "GKNodeRandomRange",
    "GKNodeVectorMake", "GKNodeVectorSplit", "GKNodeVectorLength",
    "GKNodeVectorNormalize",
    "GKNodeCompare", "GKNodeLogic", "GKNodeConvert",
    # 输入 / 手柄 —— "按住 A 且在地面"这类条件不用回玩法图拼
    "GKNodeActionPressed", "GKNodeActionJustPressed",
    "GKNodeGetAxis", "GKNodeGetVector",
    "GKNodeJoyAxis", "GKNodeJoyVector",
    "GKNodeJoyButton", "GKNodeJoyButtonDown",
    # 动画自身的查询 —— "这段播完了吗""还有多久"
    "GKNodeAnimPlaying", "GKNodeAnimLength",
    # 角色状态 —— 和『动画判断_移动状态』输出的是同一批判断
    "GKNodeIsOnFloor",
)

for _cls in CLASSES:
    if _cls.bl_idname in ANIM_TREE_VALUE_NODES:
        _cls.gk_tree_type = BOTH_TREES


# ==========================================================================
# AI 图里能用的"取值"节点 —— 第四十一轮
# ==========================================================================
# AI 转场的条件要能就地拼:"血量低于 30% 就逃跑""视线被墙挡住吗"。
# 判据和动画树一样:**纯取值**(没有执行流、什么时候求值都安全)。
# AI 比动画多要三样:变量读取(看血量)、位置 / 距离(看多远)、
# 射线(看视线挡没挡)。
AI_TREE_VALUE_NODES = ANIM_TREE_VALUE_NODES + (
    "GKNodeGetVar",          # 变量_读取 —— "血量低于 30% 就逃"
    "GKNodeDistance",        # 取值_距离
    "GKNodeGetPosition",     # 取值_位置
    "GKNodeRaycastCheck",    # 射线_有东西吗 —— 视线遮挡
)

# 已经在动画树里开放的节点再加 AI 树时必须**三个都保留** ——
# 直接覆盖成两棵树会把动画树那边的『常量_数值』又关回去(用户 2026-10-03
# 踩过一次一模一样的坑:动画树里加『常量_数值』直接 RuntimeError)。
_AI_ONLY_KINDS = ("GKGameLogicTree", "GKGameAiTree")
_ALL_TREE_KINDS = ("GKGameLogicTree", "GKAnimationTree", "GKGameAiTree")

for _cls in CLASSES:
    if _cls.bl_idname in AI_TREE_VALUE_NODES:
        if getattr(_cls, "gk_tree_type", None) == BOTH_TREES:
            _cls.gk_tree_type = _ALL_TREE_KINDS
        else:
            _cls.gk_tree_type = _AI_ONLY_KINDS


# ==========================================================================
# AI 图里能用的"做事的"节点 —— 第七十九轮(D 组 10)
# ==========================================================================
# 『AI_状态』新增了『进入时』/『离开时』两条执行流出口之后,AI 图里必须
# 有**能接上去的语句节点** —— 否则那两条出口是空的,用户看得见摸不着。
#
# 只开这一小组"通用、和玩法图语义一致"的:
#   * 变量_设置 / 变量_增减  —— 改标志、扣血(血的读写靠『变量_读取』)
#   * 音效_播放              —— 打一下出个声
#   * 信号_发出              —— 通知玩法图"它打人了"
# 故意**不**全开:『移动_按方向』『相机_跟随』这类放进来只会让人以为
# "AI 状态里能让它动",而状态的行为本来就是 AI 在管 —— 两套打架。
AI_TREE_FLOW_NODES = (
    "GKNodeSetVar", "GKNodeAddVar", "GKNodePlaySound", "GKNodeEmitSignal",
)

for _cls in CLASSES:
    if _cls.bl_idname in AI_TREE_FLOW_NODES:
        # 动画树**不能**跟着被带开 —— 用户 2026-10-03 踩过一次
        # "动画树里能放『常量_数值』却一放就 RuntimeError"的坑。
        _cls.gk_tree_type = _AI_ONLY_KINDS


# ==========================================================================
# 设计图里的"系统动作" —— 两棵树都能放(第一百一十二轮)
# ==========================================================================
# 『主界面』的设置页会直接调这几个:音量滑块→『音效_总线音量』、
# 亮度→『屏幕_滤镜』、画质按钮→『画质_设置』、退出按钮→『流程_退出游戏』。
# 它们本身不是"界面"节点(玩法图里一直也在用),所以不搬家 ——
# **两边都能放**。放在门面这里是因为它们散在 _display / _containers /
# _instances 三个模块,各模块里的循环看不到"后导入"的模块(_containers
# 在 _display 之后导入),只有门面这个位置能看到全部 CLASSES。
UI_TREE_SYSTEM_NODES = (
    "GKNodeBusVolume", "GKNodeScreenFilter", "GKNodeQualitySet",
    "GKNodeQuitGame",
)

for _cls in CLASSES:
    if _cls.bl_idname in UI_TREE_SYSTEM_NODES:
        _cls.gk_tree_type = ("GKGameLogicTree", "GKUiDesignTree")


# ==========================================================================
# 『游戏界面』里能用的"界面自己的判断" —— 第一百一十二轮
# ==========================================================================
# 和动画树那份名单(ANIM_TREE_VALUE_NODES)同一套思路:界面的行为经常要
# **就地判断** —— "滑块拖过阈值就亮红字""下拉选了第三项就换一块面板"。
# 判据同样只认**纯取值 + 分支/切换**(没有副作用,什么时候求值都安全):
#   * 常量 / 数学 / 比较 / 逻辑 / 向量 / 输入查询 —— 拼条件的基本材料;
#   * 『流程_分支』『流程_切换』—— 就地分流,本身就是界面行为的一部分。
# 移动 / 相机 / 生成实例这些"改世界"的**不**开 —— 界面行为不该偷偷动游戏;
# 变量_读取也**不**开 —— 变量是按脚本存的,在主界面脚本里读到的是它自己
# 那份(空的),摆进来只会让人误会"能看到玩法侧的变量"。
UI_TREE_VALUE_NODES = ANIM_TREE_VALUE_NODES + (
    "GKNodeBranch", "GKNodeSwitch",
)

for _cls in CLASSES:
    if _cls.bl_idname in UI_TREE_VALUE_NODES:
        kinds = getattr(_cls, "gk_tree_type", "GKGameLogicTree")
        if isinstance(kinds, str):
            kinds = (kinds,)
        if "GKUiDesignTree" not in kinds:
            _cls.gk_tree_type = tuple(kinds) + ("GKUiDesignTree",)

ALL_CLASSES = tuple(CLASSES)

# "添加节点"菜单里的分类排列顺序
CATEGORY_ORDER = ("事件", "流程", "移动", "物理", "动画", "动画系统", "AI", "资产", "实例", "特效", "变量", "函数", "信号", "网络", "输入",
                  "手柄", "触屏", "运算", "显示", "相机", "灯光", "音效", "存档", "界面",
                  "屏幕效果", "氛围", "显示设置", "音乐设置", "射线", "定时", "列表", "字典", "时间",
                  "实用", "动画(旧)")

# Display groups do not change node IDs or serialized categories.
MENU_GROUPS = {
    "事件": ("事件",),
    "流程控制": ("流程", "定时", "时间"),
    "输入": ("输入", "手柄", "触屏"),
    "角色与物理": ("移动", "物理", "射线", "AI", "动画"),
    "数据与函数": ("变量", "函数", "列表", "字典", "存档"),
    "数学与逻辑": ("运算",),
    "场景与对象": ("实例", "资产", "显示"),
    "画面与声音": ("相机", "灯光", "音效", "特效", "屏幕效果", "氛围", "界面"),
    "通信": ("信号", "网络"),
    "调试与工具": ("实用",),
}

# ── 二级子分类 ──────────────────────────────────────────────────────────
# 用户 2026-10-03(对着 UPBGE 的截图):"输入里分类键盘、鼠标、手柄,
# 其他的排布优化"。UPBGE 的『输入』下面是 键盘 / 鼠标 / 游戏手柄 / VR,
# 我们照这个把『输入』收成一个父分类。
#
# 87 个节点如果只按一级分类铺开，最大的「运算」有 13 项，
# 「事件/流程/移动」也不少，翻起来还是累。
# 这张表给**大类**再切一层，菜单变成两级:
#     运算 ▸ 常量 / 数学 / 向量 / 逻辑 / 转换
#     输入 ▸ 键盘 / 鼠标 / 手柄 / 触屏
# 没列在这里的分类保持一级(节点本来就少，再套一层反而多点一次)。
SUBCATEGORY_ORDER = {
    "运算": ("常量", "数学", "向量", "逻辑", "转换"),
    "输入": ("键盘", "鼠标", "手柄", "触屏"),
    "动画": ("播放", "骨骼"),
}

# 节点 bl_idname -> 二级分类名。只给上面列了 SUBCATEGORY_ORDER 的分类登记。
SUBCATEGORY_OF = {
    # 常量
    "GKNodeConstFloat": "常量",
    "GKNodeConstText": "常量",
    "GKNodeConstBool": "常量",
    "GKNodeConstVector": "常量",
    # 数学
    "GKNodeMath": "数学",
    "GKNodeRandomRange": "数学",
    # 向量
    "GKNodeVectorMake": "向量",
    "GKNodeVectorSplit": "向量",
    "GKNodeVectorLength": "向量",
    # 距离也放"向量"下(它算的就是两个位置之间那个向量有多长)
    "GKNodeDistance": "向量",
    "GKNodeGetPosition": "向量",
    # 文本拼接放"逻辑"旁边(和『逻辑_比较』一样是"加工数据"的)
    "GKNodeTextJoin": "逻辑",
    "GKNodeVectorNormalize": "向量",
    # 逻辑
    "GKNodeCompare": "逻辑",
    "GKNodeLogic": "逻辑",
    # 转换
    "GKNodeConvert": "转换",
    # 把数值收拾成"给人看的文字"(整数不要 .0)—— 和类型转换是一族活
    "GKNodeNumberText": "转换",
    # 输入 / 键盘
    "GKNodeKey": "键盘",
    "GKNodeActionPressed": "键盘",
    "GKNodeActionJustPressed": "键盘",
    "GKNodeGetAxis": "键盘",
    "GKNodeInputDirection": "键盘",
    "GKNodeGetVector": "键盘",
    "GKNodeOnInput": "键盘",
    # 输入 / 鼠标
    "GKNodePlayAnim": "播放",   # 动画_播放
    "GKNodeAnimParam": "播放",   # 动画_设混合参数
    "GKNodeAnimStatus": "播放",   # 动画_运行状态
    "GKNodeAnimStateMachineRef": "播放",   # 动画_状态机
    "GKNodeBlendTree": "播放",   # 动画_混合树
    "GKNodeBlendTree2D": "播放",  # 动画_混合树2D(第七十二轮)
    "GKNodeAnimOverlay": "播放",  # 动画_叠层(第七十三轮)
    "GKNodeAnimEvent": "播放",  # 动画_事件(第七十六轮)
    "GKNodeAnimPlaying": "播放",   # 动画_是否在播
    "GKNodeAnimPause": "播放",   # 动画_暂停/继续
    "GKNodeAnimStop": "播放",   # 动画_停止
    "GKNodeAnimSetSpeed": "播放",   # 动画_设播放速度
    "GKNodeAnimLength": "播放",   # 动画_时长
    "GKNodeAnimFinished": "播放",   # 动画_播放完成
    "GKNodeSetState": "播放",   # 动画_切换状态
    "GKNodeGetBone": "骨骼",   # 骨骼_取数据
    "GKNodeSetBone": "骨骼",   # 骨骼_设置
    "GKNodeBoneLookAtCamera": "骨骼",   # 骨骼_跟随视角
    "GKNodeMouseButton": "鼠标",
    "GKNodeMouseButtonDown": "鼠标",
    "GKNodeMouseWheel": "鼠标",
    "GKNodeMousePosition": "鼠标",
    "GKNodeMouseMotion": "鼠标",
    "GKNodeMouseMode": "鼠标",
    # 输入 / 手柄
    "GKNodeJoyAxis": "手柄",
    "GKNodeJoyVector": "手柄",
    "GKNodeJoyButton": "手柄",
    "GKNodeJoyButtonDown": "手柄",
    "GKNodeJoyVibrate": "手柄",
    # 输入 / 触屏
    "GKNodeTouchButton": "触屏",
    "GKNodeTouchDrag": "触屏",
}

# 「常用」短名单:新手最需要的前 12 个，直接放菜单最上层。
# 目的是让第一次打开菜单的人不用翻 23 个分类就能起步。
COMMON_NODES = (
    "GKNodeOnReady",
    "GKNodeOnProcess",
    "GKNodeOnPhysicsProcess",
    "GKNodeOnInput",
    "GKNodeBranch",
    "GKNodeSequence",
    "GKNodeDoOnce",
    "GKNodeForLoop",
    "GKNodePrint",
    "GKNodeWait",
    "GKNodeSetVar",
    "GKNodeGetVar",
    "GKNodeMoveDirection",
    "GKNodeMoveAndSlide",
    "GKNodeJump",
    "GKNodeActionJustPressed",
    "GKNodeGetVector",
    "GKNodeComment",
)
