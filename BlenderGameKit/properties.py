"""Blender 侧属性(场景级设置)与常用小工具。"""

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from . import codegen
# 线框标记物体(第一百二十四轮):三种标记的 kind / 中文标签 / 说明
# 只在 markers.KINDS 里定义一份,这里的下拉直接引它 —— 免得三处各写一份
from . import markers
from .nodes import nodes as node_lib
from .nodes.node_base import node_candidates

BASE_CLASSES = (
    # 默认必须是"自动"。
    #
    # 踩过一个大坑:以前默认写死 CharacterBody3D,而物体的物理体类型
    # (`body_type`)是另一个设置。两者一旦不一致,导出的脚本就会
    # `extends StaticBody3D` 却挂在 Area3D 节点上,Godot 直接拒绝加载:
    #     Script inherits from native type 'StaticBody3D',
    #     so it can't be assigned to an object of type: 'Area3D'
    # 用户的游戏因此**根本跑不起来**(实测)。
    #
    # 现在默认"跟着物体走":脚本的 extends 由节点的**实际类型**决定,
    # 从源头上不可能对不上。
    ("auto", "自动(跟着物体走)", "按物体在『游戏物理』里选的类型决定脚本 extends —— 不会对不上"),
    ("CharacterBody3D", "3D 角色 CharacterBody3D", "受重力与碰撞控制的 3D 角色"),
    ("CharacterBody2D", "2D 角色 CharacterBody2D", "受重力与碰撞控制的 2D 角色"),
    ("RigidBody3D", "3D 刚体 RigidBody3D", "用物理引擎驱动的 3D 刚体"),
    ("RigidBody2D", "2D 刚体 RigidBody2D", "用物理引擎驱动的 2D 刚体"),
    ("StaticBody3D", "3D 静态碰撞体 StaticBody3D", "不动的地形 / 墙 / 地板"),
    ("StaticBody2D", "2D 静态碰撞体 StaticBody2D", "不动的 2D 地形"),
    # ── 会动的平台 / 载具(用户 2026-10-05 第三十轮)────────────────
    # Godot 里"站上去会被带走"的平台必须是 AnimatableBody:被脚本/时间轴
    # 每帧挪位置时,它的**速度**会传给站在上面的角色;StaticBody 是"瞬移",
    # 角色只会留在原地(平台从脚下溜走)。见探针"移动平台"工位。
    ("AnimatableBody3D", "3D 动画平台 AnimatableBody3D",
     "会动的平台 / 载具:站上去跟着走(时间轴或脚本移动它)"),
    ("AnimatableBody2D", "2D 动画平台 AnimatableBody2D",
     "会动的 2D 平台:站上去跟着走"),
    ("Area3D", "3D 触发区 Area3D", "只检测重叠、不阻挡(拾取物 / 伤害区)"),
    ("Area2D", "2D 触发区 Area2D", "只检测重叠、不阻挡"),
    ("Node3D", "3D 空节点 Node3D", "没有物理的 3D 容器节点"),
    ("Node2D", "2D 空节点 Node2D", "没有物理的 2D 容器节点"),
    ("Node", "纯逻辑节点 Node", "只跑逻辑、不参与渲染与物理"),
)

# "自动"的标识
BASE_CLASS_AUTO = "auto"

# 物理体类型 -> 对应的脚本基类。两边必须一致,否则 Godot 拒绝加载脚本。
BODY_TYPE_TO_BASE_CLASS = {
    "StaticBody3D": "StaticBody3D",
    "StaticBody2D": "StaticBody2D",
    "AnimatableBody3D": "AnimatableBody3D",
    "AnimatableBody2D": "AnimatableBody2D",
    "RigidBody3D": "RigidBody3D",
    "RigidBody2D": "RigidBody2D",
    "CharacterBody3D": "CharacterBody3D",
    "CharacterBody2D": "CharacterBody2D",
    "Area3D": "Area3D",
    "Area2D": "Area2D",
}


def resolve_base_class(scene, obj=None, physics=None, dimension="3"):
    """算出脚本该 extends 什么。

    ── 为什么需要这个函数 ──────────────────────────────────────────
    脚本的 `extends` 必须和它挂的那个节点**完全一致**,否则 Godot 报
        Script inherits from native type 'X',
        so it can't be assigned to an object of type: 'Y'
    然后整个场景加载失败 —— 游戏根本跑不起来。
    (实测踩过:用户勾了『作为触发器』,节点变成 Area3D,
     而脚本还是 StaticBody3D。)

    优先级:
      1. 物体自己的设置(它决定节点类型,所以也决定脚本基类)
      2. 场景设置里手选的基类(用户明确指定就尊重他)
      3. 按维度给个兜底
    """
    settings = getattr(scene, "gk_settings", None)
    chosen = getattr(settings, "base_class", BASE_CLASS_AUTO) if settings else ""

    # 1) 有物体设置 -> 跟着物体走。这样绝不会对不上。
    if physics is not None:
        if getattr(physics, "is_trigger", False):
            return "Area3D" if str(dimension) != "2" else "Area2D"
        body_type = getattr(physics, "body_type", "")
        if body_type in BODY_TYPE_TO_BASE_CLASS:
            # 维度不匹配时纠正一下(比如 2D 项目里选了个 3D 类型)
            is_2d = str(dimension) == "2"
            if body_type.endswith("2D") != is_2d:
                body_type = body_type[:-2] + ("2D" if is_2d else "3D")
            if body_type in BODY_TYPE_TO_BASE_CLASS:
                return BODY_TYPE_TO_BASE_CLASS[body_type]

    # 2) 用户手选的基类
    if chosen and chosen != BASE_CLASS_AUTO:
        return chosen

    # 3) 兜底
    return "CharacterBody3D" if str(dimension) != "2" else "CharacterBody2D"


def base_class_mismatches(scene, dimension="3"):
    """找出"脚本基类会和节点类型对不上"的隐患。返回 [(物体名, 基类, 节点类型)]。

    面板上用它来提前警告 —— 让用户在 Blender 里就看到问题,
    而不是在 Godot 里被一堆英文报错拦住。
    """
    out = []
    if scene is None:
        return out
    settings = getattr(scene, "gk_settings", None)
    chosen = getattr(settings, "base_class", BASE_CLASS_AUTO) if settings else ""
    if not chosen or chosen == BASE_CLASS_AUTO:
        return out        # 自动模式不可能对不上

    for obj in scene.objects:
        if getattr(obj, "type", "") != "MESH":
            continue
        physics = getattr(obj, "gk_physics", None)
        if (physics is None or not getattr(physics, "enabled", True)
                or not getattr(physics, "script_target", False)):
            continue
        actual = resolve_base_class(scene, obj, physics, dimension)
        if actual != chosen:
            out.append((obj.name, chosen, actual))
    return out

# 碰撞形状。auto = 按包围盒自动挑(推荐),其余是强制指定。
COLLISION_SHAPES = (
    ("auto", "自动", "按物体尺寸自动选球 / 胶囊 / 盒"),
    ("none", "无", "不生成碰撞体"),
    ("box", "盒子 Box", "立方体 / 墙壁 / 平台"),
    ("sphere", "球 Sphere", "圆球(道具、球体角色)"),
    ("capsule", "胶囊 Capsule", "人形角色(推荐)"),
    ("cylinder", "圆柱 Cylinder", "柱子、树干"),
    # 凸包(用户本轮选的"手动选凸包"):按网格真实外形生成 ——
    # 盒子会"切角"、球会"漏边"的不规则模型(石头 / 雕像)用它。
    # 自动模式**不会**选它(行为可预期),只在这个下拉里手动选。
    ("convex", "凸包 Convex", "按网格真实外形(只有 3D;不规则模型:石头 / 雕像)"),
)

# 碰撞层:Godot 里 1..32 层,用位掩码表示。这里给几个常用预设。
COLLISION_LAYERS = (
    ("1", "第 1 层", "默认层,几乎所有东西都用"),
    ("2", "第 2 层", "常用作玩家层"),
    ("4", "第 3 层", "常用作敌人层"),
    ("8", "第 4 层", "常用作地形层"),
)


def _on_dimension_changed(self, context):
    """切 2D / 3D 时顺手把基类也切到对应版本，少一步手动操作。"""
    want_2d = getattr(self, "dimension", "3") == "2"
    current = getattr(self, "base_class", "")
    is_2d = current.endswith("2D")
    if want_2d and not is_2d and current:
        swapped = current[:-2] + "2D"
        if any(item[0] == swapped for item in BASE_CLASSES):
            self.base_class = swapped
    elif not want_2d and is_2d and current:
        swapped = current[:-2] + "3D"
        if any(item[0] == swapped for item in BASE_CLASSES):
            self.base_class = swapped


def _on_physics_changed(self, context):
    """物理参数变了就重建碰撞线框(实时预览)。

    真正的实现放在 operator_io 里,这里只做转发 ——
    properties.py 不该直接 import operator_io(会绕成循环导入),
    所以用运行时查找;拿不到就静默跳过(离线测试环境本来就没有 UI)。

    self 是 GKObjectSettings 实例,`self.id_data` 是它所属的物体。
    """
    import sys

    package = __package__ or "BlenderGameKit"
    module = sys.modules.get(f"{package}.operator_io")
    if module is None:
        return
    hook = getattr(module, "_on_physics_changed", None)
    if hook is None:
        return
    try:
        hook(self, context)
    except Exception:
        pass


class GKLogicBinding(bpy.types.PropertyGroup):
    binding_name: StringProperty(name="引用名", default="引用")
    target: PointerProperty(name="场景物体", type=bpy.types.Object)


class GKObjectSettings(bpy.types.PropertyGroup):
    """挂在**每个物体**上的物理 / 导出设置。

    为什么放在物体上而不是导出面板里
    ---------------------------------
    参考 UPBGE 的做法:碰撞形状、是不是刚体、质量这些**属于物体本身**,
    在物体属性面板里设最直观 —— 选中谁就改谁,所见即所得。
    如果放在侧栏的导出面板里,就得先选中物体、再去另一个面板填参数,
    两处来回跳,而且没法一眼看出"场景里哪些东西有碰撞"。

    导出时 asset_export.collect_physics() 会遍历场景读取这些设置。
    """

    body_type: EnumProperty(
        name="物理类型",
        description="决定它在 Godot 里是什么节点",
        items=BASE_CLASSES,
        default="StaticBody3D",
    )
    collision_shape: EnumProperty(
        name="碰撞形状",
        description="auto = 按物体尺寸自动挑最合适的",
        items=COLLISION_SHAPES,
        default="auto",
        update=_on_physics_changed,
    )
    # 只有选 auto 之外的形状时才用得上
    shape_size: FloatVectorProperty(
        name="形状尺寸",
        description="覆盖自动计算的尺寸(X=半径/半宽, Y=高度, Z=半径/半深);"
                    "全 0 = 不用手动尺寸,按模型自动贴合",
        size=3,
        # ── 默认必须是全 0 = "没填"(用户 2026-10-03,这是"站不住"的真凶)──
        # 原来是 (0.5, 1.0, 0.5) —— 于是**每个新建物体**都自带一组
        # "0.5 x 1 x 0.5 的手动碰撞尺寸"。用户新建一个地面,它的碰撞体
        # 就是半米见方的一小块,角色当然站不住。
        # 实测他那份 .blend 里 7 个物体**全部**是 (0.5, 1.0, 0.5) ——
        # 就是这个默认值,不是他手填的。
        default=(0.0, 0.0, 0.0),
        subtype="XYZ",
        update=_on_physics_changed,
    )
    collision_layer: EnumProperty(
        name="碰撞层",
        description="Godot 的碰撞层,用位掩码表示",
        items=COLLISION_LAYERS,
        default="1",
        update=_on_physics_changed,
    )
    collision_mask: EnumProperty(
        name="检测层",
        description="它会和哪些层发生碰撞",
        items=COLLISION_LAYERS,
        default="1",
    )
    mass: FloatProperty(
        name="质量",
        description="只有刚体用得上",
        default=1.0,
        min=0.001,
        soft_max=100.0,
    )
    gravity_scale: FloatProperty(
        name="重力倍率",
        description="0 = 不受重力(漂浮平台),1 = 正常",
        default=1.0,
        soft_min=0.0,
        soft_max=4.0,
    )
    hide_mesh: BoolProperty(
        name="不渲染网格",
        default=False,
        description="导出时**跳过这块网格**,但碰撞体 / 触发区照旧生成。\n"
                    "给\"看不见的判定体\"用:区域触发块、音量区、检查点。\n"
                    "以前只能把它涂成近黑色,可实机里仍是一块挡视线 / 挡光的\n"
                    "实体(门洞里的触发盒会把门口堵成一块黑板)",
    )
    is_trigger: BoolProperty(
        name="作为触发区",
        description="只检测重叠、不阻挡(碰到就知道,但穿得过去)",
        default=False,
    )
    # ── 地面响度倍率(第八十轮 D-12②:材质响度)──────────────────────
    # 玩法图节点『声音_脚下响度』往下射线读到的就是它 —— 拿它乘跑步
    # 噪音的响度:地毯 0.3(小声)、铁皮 2.0(整条街听见)。1.0 = 普通
    # 地面,导出时不写任何额外数据。
    surface_loudness: FloatProperty(
        name="地面响度倍率",
        description=("踩在这个物体表面时,传出去的噪音响度乘多少:\n"
                     "1.0 = 普通(默认);0.3 = 地毯,几乎听不到;"
                     "2.0 = 铁皮,半条街都惊动。\n"
                     "和玩法图节点『声音_脚下响度』配合"
                     "(它往下射线读这个值,再去乘『声音_发出噪音』的响度)"),
        default=1.0,
        soft_min=0.0,
        soft_max=4.0,
    )
    # ── 动态障碍(第八十三轮:寻路第三版 —— NavigationObstacle)────────
    # 勾上后导出时给这个物体挂一个 `NavigationObstacle2D / 3D` 子节点
    # (半径 = 『障碍半径』)。它的位置**每帧被避障系统读**,所以物体
    # 挪到哪、勾了『避障』的角色就实时绕开哪 —— **不用重烤导航网格**。
    # 典型用途:会动又不该穿过去的柱子 / 车 / 关着的门;
    # 或者"看不见但挡路"的幽灵障碍(物体是 Area、没有物理碰撞,
    # 只有避障会理它 —— 示例 demo_nav2d 的对照就是这么做的)。
    nav_obstacle: BoolProperty(
        name="动态障碍",
        description=("勾上 = 这个物体是『会动的挡路物』:勾了『避障』的角色\n"
                     "会实时绕开它,而且**不用重烤导航网格**。\n"
                     "物体自己怎么动都行(缓动 / 时间轴 / 移动节点)。"),
        default=False,
    )
    nav_obstacle_radius: FloatProperty(
        name="障碍半径",
        description=("避障系统把它当成多大的圆(米 / 像素)。\n"
                     "一般填『物体半宽再多一点』:太小会被贴脸蹭过去。"),
        default=1.0,
        soft_min=0.1,
        soft_max=20.0,
    )
    # ── 通行代价(第八十四轮:寻路第三版② —— 代价区域)─────────────────
    # 填 != 1 = 这个物体盖住的那片地是"代价区":寻路把走在上面算多贵。
    # > 1 = 泥地 / 危险区(路径尽量绕开);< 1 = 近路(路径会主动走它)。
    #
    # ── 为什么必须是"挖洞 + 补洞"(真 Godot spike 实测)────────────────
    # 试过把代价块**叠**在主导航网格上(两块 region 重叠)—— 路径无视
    # 上面的价格照直穿;只有让主网格在代价区**没有覆盖**(烘焙时当障碍
    # 挖掉、再单独补一块设 `travel_cost`)价格才生效。
    # 所以代价区之间**别互相叠着放**(重叠部分同样会互相无视)。
    nav_travel_cost: FloatProperty(
        name="通行代价",
        description=("1.0 = 普通地面(默认,不导出);\n"
                     "大于 1 = 泥地 / 危险区,寻路尽量绕开(试试 5);\n"
                     "小于 1 = 近路,路径会主动走它。\n"
                     "需要场景勾『寻路』;代价区之间别互相叠着放"),
        default=1.0,
        soft_min=0.05,
        soft_max=20.0,
    )
    # ── 可见距离 / 阴影开销(第九十八轮:性能第二件)────────────────────
    # "离得远就别画":Godot 的 GeometryInstance3D.visibility_range_end ——
    # 相机离它超过这个距离,整个网格不进渲染。省的是**渲染**,不是物理
    # (碰撞体还在,角色照样撞得到它)。
    #
    # ⚠ 为什么必须运行时设,而不是写进 .tscn(踩过的坑,见 asset_export
    #   里 `_apply_materials` 上面那段):物体的网格在 **glb 实例内部**,
    #   而 glb 实例的内部子节点**不能被外部节点覆盖** —— 在 .tscn 里写
    #   `visibility_range_end = 80` 会被 Godot 丢掉。所以导出时改成
    #   写进环境脚本,运行时设。
    # ⚠ 默认 0 = 不导出(o 老场景一字不变)。硬切(到距离直接消失)——
    #   想要淡出得在 Godot 里把 visibility_range_fade_mode 调成 Self。
    render_visibility_distance: FloatProperty(
        name="可见距离",
        description=("相机离得超过这个距离(米)就不画这个网格 —— 省渲染,"
                     "碰撞体还在(照样撞得到)。\n"
                     "0 = 不限制(默认,不导出)。适合远处装饰物"),
        default=0.0,
        soft_min=0.0,
        soft_max=200.0,
    )
    render_no_shadow: BoolProperty(
        name="不投阴影",
        description=("这个网格不往阴影贴图里画 —— 小零件(草 / 碎石 / 挂件)"
                     "省阴影开销。\n"
                     "⚠ 它自己**照样接收**阴影,只是不投"),
        default=False,
    )
    # ── 遮挡体(第九十九轮:性能第三件)───────────────────────────────
    # "挡住了就别画它后面的东西":导出成 Godot 的 OccluderInstance3D
    # (+ 一个按包围盒做的 BoxOccluder3D)。
    #
    # ⚠ 真机标定过(Godot 4.7,一面 20x20 的墙 + 墙后 20 个方块):
    #     * 不挂遮挡体      -> 本帧渲染对象数 **21**(墙 + 20 个,白画)
    #     * 挂上遮挡体      -> **1**(墙后面全被剔除)
    #     * BVH 要几帧才建好:第 10 帧还是 21,第 20 帧起才是 1
    #
    # ⚠ 两条必须知道的:
    #   1. Godot 的 `rendering/occlusion_culling/use_occlusion_culling`
    #      **默认是 false** —— 光有节点一点都不会省(实测 21 vs 21)。
    #      所以勾了它,导出会顺手把那个项目设置打开(见 asset_export
    #      的 ensure_occlusion_culling);
    #   2. **每个遮挡体都有 CPU 开销**,小块物体勾它反而更慢。只给
    #      "大块、结实、真能挡视线"的东西用:墙 / 建筑 / 大地形。
    #
    # ⚠ 风险(候选池里就写着):遮挡体太大会把**本来该看见**的东西剔掉 ——
    #   表现是"物体凭空消失"。我们按物体包围盒生成,别手动往大了摆。
    render_occluder: BoolProperty(
        name="遮挡体",
        description=("勾上 = 这个物体挡住的东西不画(Godot 的遮挡剔除)。\n"
                     "只给大块的封闭物用:**墙 / 建筑 / 大地形**;\n"
                     "⚠ 每个遮挡体都有 CPU 开销,小块物体勾了反而更慢;\n"
                     "⚠ 尺寸按包围盒算,别指望斜放的小物件挡得准。只支持 3D"),
        default=False,
    )
    # ── 寻路层(第八十五轮:寻路第三版③ —— 多层导航)──────────────────
    # "不同的角色走**不同的导航网格**":这个物体属于第几层,导出时就烤进
    # 那一层的网格(按 Godot group 分组,各层只烤自己的几何,实测)。
    # 典型用法:地面 / 墙 = 第 1 层(走路的人),空中平台 = 第 2 层(飞的
    # 人)—— 两组互相看不见对方的路。
    # ⚠ 用了第 2 层之后,『移动_寻路』/『AI_状态』上也要挑『走哪层』,
    #   否则它们还在层 1 上找路(层 1 的网格里没有层 2 的地板)。
    nav_layer: EnumProperty(
        name="寻路层",
        description=("这个物体的碰撞体烤进**第几层**的导航网格。\n"
                     "默认第 1 层(走路的人);另一组角色(飞的 / 钻管的)"
                     "放第 2 层,两套路互不干扰。\n"
                     "⚠ 用了第 2 层后,『移动_寻路』/『AI_状态』也要挑"
                     "『走哪层』"),
        items=(
            ("1", "第1层", "默认:走路的人走的网格"),
            ("2", "第2层", "另一组角色的网格(和层 1 完全分开)"),
            ("3", "第3层", "第三组"),
            ("4", "第4层", "第四组"),
        ),
        default="1",
    )
    # ── 子物体碰撞块(用户本轮选的方案:"头/身/腿各摆一个")────────────
    # 勾上后这个物体**不出现在游戏画面里**(网格不导出),它的形状
    # 合并进**父物体**的碰撞体 —— 人形角色摆三个胶囊就是"头/身/腿"。
    # 为什么做成子物体而不是面板里填数字:视口里直接拖着摆,所见即所得;
    # 碰撞块自己还能用『碰撞形状』下拉 + 实时线框预览微调。
    collision_piece: BoolProperty(
        name="作为父级的碰撞块",
        description="不出现在游戏画面里;形状合并进父物体的碰撞体"
                    "(人形:头/身/腿各摆一个)。需要挂在别的网格物体下面",
        default=False,
    )
    script_target: BoolProperty(
        name="挂游戏逻辑脚本",
        description="把节点图导出的脚本挂到这个物体上",
        default=False,
    )
    # ── 角色逻辑图(第一百一十九轮:区分世界/角色/关卡逻辑)────────────
    # 勾了『挂游戏逻辑脚本』的物体,可以再指定一张**它自己的**逻辑图
    # (角色控制 / 敌人 AI 这类"跟着这个物体走"的逻辑)。留空 = 不单独挂,
    # 逻辑都走场景那张图(老行为)。填了 = 这张图导出成独立脚本挂到这个
    # 物体上,和场景图(关卡/世界逻辑)分开。
    logic_tree: StringProperty(
        name="角色逻辑图",
        description=("这张物体**自己的**逻辑图(角色控制/敌人行为等)。"
                     "留空 = 不单独挂,逻辑走场景那张图。"
                     "填了 = 独立脚本挂到这个物体上"),
        default="",
    )
    logic_bindings: CollectionProperty(type=GKLogicBinding, name="角色引用")
    # ── 静态资产(用户 2026-10-05 第十四轮:"游戏静态资产设计")─────────
    # 一个模型只导一次,场景里可以摆很多份:填同一个『资产名』的物体
    # 属于同一个资产;**本体**(不勾"作为实例")负责定义模型,
    # **实例**在导出的场景里各自带一份网格 + 自己的碰撞体。
    #
    # 为什么用物体属性而不是"另建一个资产库":
    # 用户选的就是这条 —— 选中谁就填谁,不用维护两份对应关系;
    # 复制物体时属性跟着复制,把"作为实例"勾上就是新的一份实例。
    asset_name: StringProperty(
        name="资产名",
        description=("留空 = 普通物体。填了名字就属于这个『资产』:\n"
                     "一个物体当本体(定义模型),其它同名的当实例"),
        default="",
    )
    asset_instance: BoolProperty(
        name="作为实例",
        description=("勾上 = 这份只是引用:导出时复用本体的模型\n"
                     "(本体 = 同一个资产名里没勾的那一个)"),
        default=False,
    )
    # ── 灯光(第四十八轮:阶段①灯光扩展)─────────────────────────────
    # 只对**灯光物体**有意义(其它物体上勾了不影响导出)。
    # 勾上 = 这盏灯在游戏里跟着**当前相机**走 —— 手电筒 / 头灯:
    # 位置和朝向每物理帧对齐相机,Blender 里摆的位置只在预览时有意义。
    light_follow_camera: BoolProperty(
        name="跟随相机(手电筒)",
        description=("这盏灯跟着相机走(手电筒 / 头灯):位置和朝向每帧"
                     "对齐当前相机;颜色 / 亮度 / 阴影照旧用它自己的设置"),
        default=False,
    )
    # ── 躲藏区(第一百零九轮:真潜行)───────────────────────────────
    # 勾上 = 这个触发区(Area)是"躲藏点":玩家站在里面时,**敌人的『看见』
    # 判定直接判看不见**(哪怕正对着、距离够近)。配"钻进柜子 / 蹲进暗处"
    # 那类玩法用 —— 之前没有专门的"潜行"设施,只能靠"把 AI 状态冻住"
    # 近似(第一百零八轮 demo_story 里就是这么糊的),这条把它做成真能力。
    #
    # ⚠ 它只影响 AI 的**视线**(『AI_看得见』/『AI_转场』的"看见"条件),
    #   不影响听觉、也不让玩家免于已经被激活的追击(追击状态要不要停,
    #   由你接"进入躲藏区"触发一条转场自己决定)。
    is_hide_spot: BoolProperty(
        name="躲藏区",
        description=("这个触发区是躲藏点:玩家在里面时敌人的『看见』判定"
                     "判看不见。只对 Area(触发区)有意义,别的物体上勾了不生效"),
        default=False,
    )
    # ── 阴影最大距离(第九十八轮:性能第二件)─────────────────────────
    # Godot 的 DirectionalLight3D.directional_shadow_max_distance —— 平行光
    # (太阳 / 月光)的阴影只在这个距离内算。**默认 100 米**已经很保守,
    # 但开放场景里把 shadow map 铺满几百米会很贵;填小一点既省又往往
    # 看不出区别(远处的影子本来就看不见)。
    #
    # ⚠ 只对**平行光**有意义(点光 / 聚光没有这个属性,Godot 侧不会设)——
    #   在其它灯上填了不影响导出,但也不生效,面板上会提示。
    # 0 = 不导出(老场景一字不变)。
    light_shadow_distance: FloatProperty(
        name="阴影距离",
        description=("平行光(太阳 / 月光)的阴影只算到这个距离(米)。\n"
                     "填小 = 省阴影开销;0 = 不导出(默认)。只对平行光有效"),
        default=0.0,
        soft_min=0.0,
        soft_max=200.0,
    )

    # ── 线框标记物体(第一百二十四轮)────────────────────────────────
    # 用户原话:"出生点 / 区域环境点 / 关卡父级这种,做成视口里看得见的
    # 线框标记物体,属性挂在物体属性里,别塞进游戏逻辑侧栏。"
    #
    # 为什么放在**物体属性**(GKObjectSettings)而不是场景设置里
    # --------------------------------------------------------
    # 标记是"某个物体是什么身份",和碰撞形状 / 刚体质量同一类:选中谁就改谁。
    # 放进侧栏的场景设置就得"先选物体 → 再去另一个面板填",而且一个场景
    # 只能有一个出生点、区域却可以有几十个 —— 场景级的容器装不下。
    #
    # ── 为什么是两个属性名(都是真属性,互相同步)───────────────────
    # `gk_marker_kind` 是 ui.py 的面板 / 建标记算子正在读写的名字;
    # `marker_kind` 是 task 描述里的名字。两个都得可读可写,靠 update
    # 回调互相同步(markers.sync_update),不引入第二个真值源。
    #
    # ⚠ "无" 的标识符是 NONE,不是空字符串:Blender 4.5 会把
    #   ("", "无", …) 这一项从标识符表里丢掉 —— 读默认值每读一次刷一条
    #   `pyrna_enum_to_py: current value '0' matches no enum`,写回空串直接
    #   `TypeError: enum "" not found`(探针实测)。markers.kind_of() 会把
    #   NONE 统一归成 ""。
    #
    # 老 .blend 兼容:这几个属性都是**新加的**,读的时候一律
    # `markers.kind_of(obj)` / `getattr(physics, "...", 默认)`;旧文件里
    # 那些属于另一个包路径设置类的物体上没有它们,不这样写会直接抛异常。
    gk_marker_kind: EnumProperty(
        name="标记类型",
        description=("把这个物体当成一种『标记』:\n"
                     "玩家出生点 / 区域环境点 / 关卡父级。\n"
                     "标记物体本身不进 Godot 的几何(线框只是给你看着摆位),"
                     "它产生的是出生点节点 / 区域触发器 / 分组节点"),
        items=markers.enum_items(),
        default=markers.NONE_VALUE,
        update=markers.sync_update(markers.MARKER_PROPERTY,
                                   markers.MARKER_PROPERTY_ALIAS),
    )
    marker_kind: EnumProperty(
        name="标记类型(别名)",
        description="和『标记类型』是同一个东西(task 描述里的名字)",
        items=markers.enum_items(),
        default=markers.NONE_VALUE,
        update=markers.sync_update(markers.MARKER_PROPERTY_ALIAS,
                                   markers.MARKER_PROPERTY),
        options={"HIDDEN"},
    )
    env_preset: StringProperty(
        name="环境预设",
        description=("『区域环境点』走进来要切的那套氛围的名字。\n"
                     "先在内置名里挑:恐怖夜 / 明亮白天 / 黄昏 / 浓雾夜;\n"
                     "也可以用侧栏环境卡里存过的预设名。留空 = 不生成这片区域"),
        default="",
    )
    env_restore: BoolProperty(
        name="离开还原",
        description=("走出这片区域时把氛围还原成进来之前的样子(默认开)。\n"
                     "关掉 = 这片区域**永久**改掉氛围"
                     "(剧情里『走进雾里就再也散不掉』那种)"),
        default=True,
    )
    env_fade: FloatProperty(
        name="过渡秒",
        description=("切换到这套氛围用多少秒过渡(0 = 立刻切)。\n"
                     "暂停时过渡也会继续跑完(不会卡在半路)"),
        default=0.35,
        min=0.0,
        max=markers.MAX_ZONE_FADE,
        soft_max=2.0,
    )
    zone_fit_axis: EnumProperty(
        name="范围轴",
        description=("『适应所选』按钮按哪几个轴对齐(只影响摆位按钮,\n"
                     "不影响导出 —— 导出用的是世界包围盒)"),
        items=(
            ("XYZ", "XYZ(整盒)", "三个轴都按选中物体对齐 —— 最常用"),
            ("XY", "XY(只按底面)", "高度保持原样,只对齐平面范围"),
            ("XZ", "XZ", "只对齐 X / Z 两个轴"),
            ("YZ", "YZ", "只对齐 Y / Z 两个轴"),
        ),
        default="XYZ",
    )


def physics_of(obj):
    """安全地取物体上的物理设置(没有就返回 None)。"""
    return getattr(obj, "gk_physics", None)


def fx_of(obj):
    """安全地取物体上的粒子发射器设置(没有就返回 None)。"""
    return getattr(obj, "gk_fx", None)


# 调试面板能用的按键(值 = Godot 里的按键常量名,导出时直接用)。
# 只给这几个:"不常用的功能键 + 反引号",免得和游戏自己的操作抢键。
DEBUG_KEYS = (
    ("F1", "F1", ""),
    ("F2", "F2", ""),
    ("F3", "F3", ""),
    ("F4", "F4", ""),
    ("F8", "F8", ""),
    ("`", "`(数字 1 左边那个)", "键盘左上角,游戏里几乎不会用到"),
)

# 粒子类型(用户 2026-10-05 第二十一轮:"面板可切,默认 GPU")。
#
# 为什么两套都要:GPUParticles 效果全、参数多(Godot 默认推荐),
# 但**老显卡 / 兼容渲染器**环境下有风险;CPUParticles 是纯 CPU 计算,
# 在哪都能跑(代价是粒子多了会吃帧)。让用户在物体上自己选,
# 导出侧按选择生成不一样的节点 —— 两边都验过。
FX_TYPES = (
    ("gpu", "GPU 粒子 GPUParticles",
     "GPU 加速、效果全(颜色渐变 / 大小曲线都在材质里);Godot 默认推荐"),
    ("cpu", "CPU 粒子 CPUParticles",
     "纯 CPU 计算,兼容性最好(老显卡 / 兼容渲染器都能跑);参数略少"),
)

# 环境背景模式(第四十九轮:阶段②环境与后处理)。值 = Godot 的
# Environment.background_mode 枚举(BG_COLOR=1 / BG_SKY=2)—— 导出时直接用。
ENV_BACKGROUNDS = (
    ("COLOR", "纯色", "用『背景色』铺满 —— 恐怖片的黑 / 幽暗最常用"),
    ("SKY", "天空", "程序化天空(带地平线渐变,天空色跟着『背景色』走)"),
)

# 色调映射模式。值 = Godot 的 Environment.tonemap_mode 枚举
# (第九十五轮真机核对:hint = Linear,Reinhard,Filmic,ACES,AgX)。
# 顺序 / 取值要和 asset_export.ENV_TONEMAP_MODES 一致(离线测试锁着)。
# AgX 是第九十五轮补的 —— 它和 Blender 默认的 AgX 视图变换同一条曲线,
# 选它"预览↔运行时"能精确对齐(其余几个只能挑同族近似,见 godot_look)。
ENV_TONEMAPS = (
    ("LINEAR", "线性", "不做色调映射(老项目默认,颜色最直白)"),
    ("REINHARD", "Reinhard", "柔和压高光,画面不那么容易过曝"),
    ("FILMIC", "Filmic", "电影感,暗部更沉"),
    ("ACES", "ACES", "高动态范围,亮部过渡最自然"),
    ("AGX", "AgX", "和 Blender 默认视图变换同名同族 —— 预览对齐首选"),
)

# 泛光混合模式(第 124 轮)。**值 = Godot 的 Environment.glow_blend_mode
# 枚举**(真机反射核对:hint = Additive,Screen,Softlight,Replace,Mix):
#     0=相加 1=滤色 2=柔光 3=替换 4=混合
# 顺序 / 取值要和 asset_export.ENV_GLOW_BLENDS 一致(离线测试锁着)。
#
# 为什么需要它:以前导出只写 `glow_intensity`(Godot 默认 0.8)和 `glow_bloom`
# —— 而真机实测泛光的**真实扳机**是 `glow_hdr_threshold`(默认 1.0 = 只有
# 超过 1.0 的像素才泛光,画面几乎是死的)和 `glow_strength`(默认 1.0)。
# 混合模式是这两个之外"泛光看起来像什么"的那一半:相加最冲(容易过曝)、
# 滤色(默认)最像常见的辉光、柔光更含蓄。所以这一项也必须够得着。
ENV_GLOW_BLENDS = (
    ("ADDITIVE", "相加", "亮部叠加,最冲;容易过曝"),
    ("SCREEN", "滤色", "默认 —— 柔和的辉光"),
    ("SOFTLIGHT", "柔光", "更暗更含蓄"),
    ("REPLACE", "替换", "只留泛光(会把画面变成一团光,一般不用)"),
    ("MIX", "混合", "按 glow_bloom 和原图混"),
)


def _preview_scale_changed(light_settings, context):
    """某盏灯的『预览补偿』改了 -> 立刻重算那一关的预览亮度(第一百三十八轮)。

    用户原话:"添加类似 blender 灯光原生的设置来优化预览与运行的差距"。
    和场景级『灯光倍率』走同一条路(**先还原再乘**,否则越调越暗),
    只是乘数是**这一盏灯**的 —— 改的只是预览,导出产物一个字不变。
    """
    import bpy

    try:
        from . import godot_look
    except Exception:                                      # noqa: BLE001
        return
    scenes = []
    if context is not None and getattr(context, "scene", None) is not None:
        scenes.append(context.scene)
    for scene in bpy.data.scenes:
        if scene not in scenes:
            scenes.append(scene)
    for scene in scenes:
        try:
            godot_look._reset_lights(scene)
        except Exception:                                  # noqa: BLE001
            pass



class GKObjectLight(bpy.types.PropertyGroup):
    """挂在**每盏灯**上的"导出到 Godot 的值"(第一百二十六轮)。

    ── 为什么需要它(用户第 10 轮的原话)────────────────────────────
    用户:"灯光还会受到 blender 的原生灯光设置影响...不行的话,就在开启
    贴近 godot 观感后就禁用原生灯光设置,转而在游戏环境与后期的侧边栏
    添加灯光设置。"

    病根是**同一个亮度有两处权威**,而且两边语义对不上:
      * Blender 灯是按**物理瓦特**算的,还带 `规格化(normalize)`、
        `半径`、以及 MMD 工具那类插件加的 `曝光度` —— 这些在 Godot 里
        **根本不存在**;
      * Godot 的 `light_energy` 是"按 `omni_range` 做距离衰减的倍率",
        导出侧的换算约定是 1000W ≈ 1.0(只在离灯 9.4 米那一个距离成立)。
    于是用户在 Blender 面板里改 385W,预览会变,但游戏里的观感变化
    完全不是那个意思 —— 他没法精确调。

    ── 这一组字段的规矩 ─────────────────────────────────────────────
    `override` 关着(默认)= **完全按老规矩走**(Blender 瓦数换算),
    产物与改动前**逐字节一致**;打开之后,下面这几个值就是**权威**:
    导出直接写它们,预览也按它们反推该多亮 —— 原生面板不再参与。
    想给一盏灯精确调游戏里的观感,就打开它。
    """

    override: BoolProperty(
        name="用这里的值导出", default=False,
        description=("打开 = 下面这几个值说了算(导出直接写它们,预览也按"
                     "它们反推);关着 = 按老规矩从 Blender 的瓦数换算"))
    energy: FloatProperty(
        name="能量", default=1.0, min=0.0, soft_max=16.0,
        description=("Godot 的 light_energy。平行光 1.0 ≈ 正午;点光/聚光"
                     " 1.0 相当于原来 1000W 那档"))
    range_m: FloatProperty(
        name="射程", default=0.0, min=0.0, soft_max=100.0,
        description="Godot 的 omni_range / spot_range(米)。0 = 用 Blender 的『自定义距离』")
    attenuation: FloatProperty(
        name="衰减", default=1.0, min=0.0, soft_max=4.0,
        description=("Godot 的衰减指数。1 = 出厂;调小 = 远处更亮(雾里"
                     "那盏孤灯),调大 = 光收缩在灯罩附近"))
    color: FloatVectorProperty(
        name="颜色", subtype="COLOR", size=4, min=0.0, max=1.0,
        default=(1.0, 1.0, 1.0, 1.0),
        description="Godot 的灯颜色(覆盖 Blender 灯的颜色)")
    use_color: BoolProperty(
        name="用这里的颜色", default=False,
        description="不勾 = 沿用 Blender 灯自己的颜色")
    shadow: BoolProperty(
        name="投影", default=True,
        description="Godot 的 shadow_enabled(关掉省性能,适合当补光用的灯)")
    # ── 阴影柔和 / 浓度(第一百五十轮)──────────────────────────────────
    # 用户原话:"漫射光线的衰减,照射范围的柔和"。
    # 射程 / 衰减指数已经在上面两项;这里补"阴影边缘"这一半:
    #   shadow_blur    = Godot 的 shadow_blur,越大影子边缘越糊(真实散射光);
    #   shadow_opacity = 影子有多黑,1 = 全黑,0.5 = 半透明(环境光透进来)。
    # 默认值 = 出厂值,导出时**不写**,老工程产物逐字不变。
    shadow_blur: FloatProperty(
        name="阴影柔和", default=0.0, min=0.0, soft_max=10.0,
        description=("Godot 的 shadow_blur:影子边缘的模糊程度。"
                     "0 = 出厂硬边;2~6 = 柔和散射光(漫射灯 / 雾天)。"
                     "⚠ 只对开着投影的灯有效"))
    shadow_opacity: FloatProperty(
        name="阴影浓度", default=1.0, min=0.0, max=1.0,
        description=("Godot 的 shadow_opacity:影子多黑。1 = 全黑;"
                     "0.5 = 半透明,适合想让环境光透进来的漫射灯"))
    # ── 光斑形状(第一百五十一轮)────────────────────────────────────
    # 用户原话:"灯的光照的范围太圆了"。
    # 点光/聚光直接照在墙上就是一个标准圆;窗户、天窗、灯罩本来不该是圆的。
    # 这里给几种内置投影纹理(运行时挂到 Godot 的 `light_projector`):
    #   RECT = 柔边矩形(窗 / 天窗 / 门洞投下来的光)
    #   LAMP = 竖向灯罩纹(壁灯 / 台灯,打破正圆)
    #   SOFT = 柔边不规则光斑(吊灯 / 上照,边缘和亮度都带一点噪声,
    #          避免"一个完美椭圆印在墙上")
    # 0 = 老行为(不挂投影),老工程产物不变。
    projector: EnumProperty(
        name="光斑形状", default="NONE",
        items=(("NONE", "圆形(默认)", "不投影纹理,保持 Godot 默认的圆形光斑"),
               ("RECT", "矩形柔光", "窗 / 天窗 / 门洞形状的柔边光斑"),
               ("LAMP", "灯罩竖纹", "竖向条纹,适合壁灯 / 台灯"),
               ("SOFT", "柔边不规则", "边缘和亮度带一点噪声,避免完美圆形光斑")),
        description="给这盏灯挂一张内置投影纹理,让光斑不再是标准圆")
    # ── 逐灯预览补偿(第一百三十八轮,用户要求)──────────────────────────
    # 用户原话:"添加类似 blender 灯光原生的设置来优化预览与运行的差距"。
    # 场景级那个『灯光倍率』是**一个数管所有灯**(0.06 是实测标定出来的),
    # 但每盏灯的偏差并不一样:主光准了,壁灯可能还是偏亮。所以这里给
    # 每盏灯一个**只影响预览**的乘数 —— 导出产物一个字都不变。
    preview_scale: FloatProperty(
        name="预览补偿", default=1.0, min=0.0, soft_max=3.0,
        description=("只影响『Godot 观感』预览:这盏灯再乘多少(1 = 不动)。"
                     "用来逐盏灯消掉『预览和运行对不上』的那点差距 —— "
                     "**导出产物不受影响**"),
        update=_preview_scale_changed)


class GKObjectFx(bpy.types.PropertyGroup):
    """挂在**每个物体**上的粒子发射器设置(需求 5,2026-10-05 第二十一轮)。

    ── 为什么 Blender 侧只做"标记 + 参数"──────────────────────────
    Blender 的粒子系统**完全导不进 glTF**(规范里根本没有粒子),
    就算硬导 Godot 也不认。所以这一套的正确分工是:

        Blender 侧:这个物体是个发射器 + 参数是多少(就是这里)
        导出时    :生成 Godot 的 GPUParticles3D / CPUParticles3D + 材质/贴图
        运行时    :『特效_播放』『特效_停止』『特效_爆发』三个节点控制它

    物体**是不是网格都行**(空物体也能当发射器:只标位置和方向)。
    """

    enabled: BoolProperty(
        name="作为粒子发射器",
        description="导出成 Godot 的粒子节点(位置 / 旋转跟着这个物体)",
        default=False,
    )
    particle_type: EnumProperty(
        name="粒子类型",
        description="GPU 效果全、CPU 兼容好;不确定就用默认的 GPU",
        items=FX_TYPES,
        default="gpu",
    )
    hide_mesh: BoolProperty(
        name="不显示本体网格",
        description="勾上 = 这个物体只当发射器标记,自己的网格不导出"
                    "(适合在空中摆一个小方块当喷点)",
        default=False,
    )
    auto_play: BoolProperty(
        name="自动播放",
        description="勾上 = 游戏一开始就在喷;不勾 = 要靠『特效_播放』才动",
        default=True,
    )
    amount: IntProperty(
        name="数量",
        description="同时活着的粒子上限(火苗 16~32;烟雾/雨可以几百)",
        default=16, min=1, max=4096,
    )
    lifetime: FloatProperty(
        name="生命周期(秒)",
        description="一个粒子从出生到消失活多久",
        default=1.2, min=0.05, soft_max=10.0,
    )
    one_shot: BoolProperty(
        name="一次性",
        description="勾上 = 喷一轮就停(受击火花 / 爆炸);不勾 = 一直循环喷",
        default=False,
    )
    preprocess: FloatProperty(
        name="预发射(秒)",
        description="开场的『提前量』—— 勾了它,进游戏时火已经烧起来了,"
                    "不用等一圈(循环效果填一整个生命周期左右)",
        default=0.0, min=0.0, soft_max=10.0,
    )
    explosiveness: FloatProperty(
        name="爆发度",
        description="0 = 粒子在生命周期里平均散开;1 = 一出生全喷出来"
                    "(一次性冲击 / 爆炸建议拉到 1)",
        default=0.0, min=0.0, max=1.0,
    )
    randomness: FloatProperty(
        name="随机度",
        description="越大越乱(速度 / 角度各有差异,不然像印刷出来的)",
        default=0.3, min=0.0, max=1.0,
    )
    speed: FloatProperty(
        name="初速(米/秒)",
        description="粒子出生时的速度(火苗 1~2;喷泉 / 冲击可以 5 以上)",
        default=1.0, min=0.0, soft_max=30.0,
    )
    direction: FloatVectorProperty(
        name="方向",
        description="喷射方向(按**这个物体自己的旋转**算:把物体转 45°,"
                    "斜着喷的火就是它)。默认朝上 = Z+",
        size=3, subtype="XYZ", default=(0.0, 0.0, 1.0),
    )
    spread: FloatProperty(
        name="扩散角(度)",
        description="0 = 一条直线喷;90 = 一个半球;180 = 四面八方",
        default=25.0, min=0.0, max=180.0,
    )
    gravity: FloatProperty(
        name="重力",
        description="正数 = 往下坠(雨 / 火花);负数 = 往上飘(烟 / 火苗);0 = 不受重力",
        default=0.0, soft_min=-30.0, soft_max=30.0,
    )
    radius: FloatProperty(
        name="发射半径",
        description="0 = 从一个点喷;大于 0 = 在这个半径的球内随机出生"
                    "(火盆 / 大范围烟雾)",
        default=0.0, min=0.0, soft_max=10.0,
    )
    start_color: FloatVectorProperty(
        name="起始颜色",
        description="粒子出生时的颜色(第 4 个分量是透明度)",
        size=4, subtype="COLOR", default=(1.0, 0.75, 0.35, 1.0),
        min=0.0, max=1.0,
    )
    end_color: FloatVectorProperty(
        name="结束颜色",
        description="消失时的颜色 —— 想『淡出』就把透明度拉到 0",
        size=4, subtype="COLOR", default=(1.0, 0.15, 0.02, 0.0),
        min=0.0, max=1.0,
    )
    start_size: FloatProperty(
        name="起始大小(米)",
        description="粒子出生时多大(用物体材质贴图时,这就是那张贴图的实际尺寸)",
        default=0.2, min=0.0, soft_max=5.0,
    )
    end_size: FloatProperty(
        name="结束大小(米)",
        description="消失时多大;0 = 缩没了(火苗的常见做法)",
        default=0.0, min=0.0, soft_max=5.0,
    )
    local_coords: BoolProperty(
        name="局部坐标",
        description="勾上 = 粒子跟着物体动(角色身上的火花);"
                    "不勾 = 喷出去就留在世界里(地上的烟 / 火)",
        default=True,
    )
    additive: BoolProperty(
        name="叠加发光",
        description="勾上 = 颜色相加、越亮越白(火 / 光 / 魔法);"
                    "不勾 = 正常透明度混合(烟 / 尘 / 水)",
        default=False,
    )


def _env_preset_items(self, context):
    """『预设』下拉的动态选项:文件里存的预设 + 内置那几档(带 ★)。

    ⚠ identifier 必须是 **ASCII**(Blender 枚举的硬性要求)—— 内置预设的
    名字是中文,所以 identifier 用 `builtin_序号`,中文名放 label。之前写成
    `builtin:恐怖夜`(含中文)会导致 EnumProperty 内部 UTF-8 解码崩溃
    (UnicodeDecodeError)。

    第 123 轮:名单**不再写在这里** —— 从 `nodes._common.ENV_PRESET_BUILTINS`
    取(那是唯一真值源)。以前这里、`statements.py` 的报错文案、节点的默认值
    各写一份中文名单,加一档"卡通二次元"就要改三处,漏一处就变成
    "下拉里有、节点说找不到"。
    """
    from .nodes._common import ENV_PRESET_ANIME, ENV_PRESET_HORROR

    items = []
    for index, preset in enumerate(getattr(self, "env_presets", ()) or ()):
        items.append((str(index), str(getattr(preset, "preset_name", "?")),
                      "本文件里存的预设"))
    # 二次元三档排前面(第 123 轮:它们是**推荐**的那一组),
    # 恐怖系跟在后面 —— 顺序 = 常用程度,不是字母序。
    for index, name in enumerate(ENV_PRESET_ANIME + ENV_PRESET_HORROR):
        hint = "内置 · 卡通二次元" if name in ENV_PRESET_ANIME else "内置 · 氛围"
        items.append((f"builtin_{index}", f"★ {name}", hint))
    return items


def builtin_env_preset_names():
    """内置预设名(第 123 轮收口:唯一真值源仍是 `nodes._common`)。

    保留这个**函数**而不是模块级常量:properties 在导入期就会 import
    `nodes`(见文件头的 `from .nodes import nodes as node_lib`),
    模块级再去取一次容易踩循环导入 —— 这里每次现取,零风险。
    """
    from .nodes._common import ENV_PRESET_NAMES
    return ENV_PRESET_NAMES


class GKEnvPreset(bpy.types.PropertyGroup):
    """一个命名环境预设(第一百一十三轮)。

    预设 = 一整套 背景色 / 环境光 / 雾 / 泛光 / 色调 参数的快照:
      * Blender 侧:『环境与后处理』卡里一键应用(所见即所得地调,
        调好了存快照);
      * 游戏里:新节点『环境_应用预设』在玩法图里随时切换。
    不带 tonemap —— 色调映射是"整局观感",不该被一关内切换。
    """

    preset_name: StringProperty(
        name="预设名", default="新预设",
        description="『环境_应用预设』节点里填的就是这个名字")
    p_background: EnumProperty(
        name="背景", items=ENV_BACKGROUNDS, default="COLOR")
    p_bg_color: FloatVectorProperty(
        name="背景色", subtype="COLOR", size=4, min=0.0, max=1.0,
        default=(0.05, 0.06, 0.10, 1.0))
    p_ambient_color: FloatVectorProperty(
        name="环境光颜色", subtype="COLOR", size=4, min=0.0, max=1.0,
        default=(0.55, 0.62, 0.80, 1.0))
    p_ambient_energy: FloatProperty(
        name="环境光强度", default=0.3, min=0.0, soft_max=4.0)
    p_fog: BoolProperty(name="雾", default=False)
    p_fog_color: FloatVectorProperty(
        name="雾颜色", subtype="COLOR", size=4, min=0.0, max=1.0,
        default=(0.30, 0.34, 0.42, 1.0))
    p_fog_density: FloatProperty(
        name="雾浓度", default=0.02, min=0.0, soft_max=1.0)
    p_glow: BoolProperty(name="泛光", default=False)
    p_glow_intensity: FloatProperty(
        name="泛光强度", default=0.8, min=0.0, soft_max=4.0)
    p_glow_bloom: FloatProperty(
        name="泛光溢出", default=0.1, min=0.0, soft_max=1.0)
    p_adjust: BoolProperty(name="色调调整", default=False)
    p_brightness: FloatProperty(name="亮度", default=1.0, min=0.0, soft_max=2.0)
    p_contrast: FloatProperty(name="对比度", default=1.0, min=0.0, soft_max=2.0)
    p_saturation: FloatProperty(name="饱和度", default=1.0, min=0.0, soft_max=2.0)
    # ── 第 124 轮:高影响键的预设镜像(和上面的 env_* 一一对应)────────
    # 命名照现有 p_fog_* / p_glow_* 的风格,统一 p_ 前缀。默认值 = 场景字段
    # 的默认值 = Godot 的默认值 —— 所以老预设(第 123 轮存下来的那些)
    # 读到这些新字段就是"什么都不改",观感不会因为这次升级而变。
    # 映射表在三处必须一致:ui._ENV_PRESET_FIELDS / make_story_demo._ENV_TO_PRESET
    # / asset_export.ENV_ZONE_KEYS。
    p_fog_sky_affect: FloatProperty(
        name="雾盖背景", default=1.0, min=0.0, max=1.0)
    p_fog_light_energy: FloatProperty(
        name="雾亮度", default=1.0, min=0.0, soft_max=4.0)
    p_fog_aerial: FloatProperty(
        name="远景空气感", default=0.0, min=0.0, max=1.0)
    p_glow_strength: FloatProperty(
        name="泛光力度", default=1.0, min=0.0, soft_max=4.0)
    p_glow_threshold: FloatProperty(
        name="泛光门槛", default=1.0, min=0.0, soft_max=2.0)
    p_glow_blend: EnumProperty(
        name="泛光混合", items=ENV_GLOW_BLENDS, default="SCREEN")
    p_tonemap_exposure: FloatProperty(
        name="曝光", default=1.0, min=0.0, soft_max=4.0)
    p_tonemap_white: FloatProperty(
        name="白点", default=1.0, min=0.1, soft_max=8.0)
    p_ambient_sky: FloatProperty(
        name="天空参与环境光", default=1.0, min=0.0, max=1.0)
    # ── 卡通风格(第一百一十三轮:环境系统大蓝图)────────────────────
    # 这些是**全局 shader 风格**参数,运行时对场景里所有 toon 材质生效。
    # 对应 gk_toon.gdshader 的同名 uniform,值语义见 npr_preview.PARAM_MAP。
    p_style_shadow_step: FloatProperty(
        name="明暗分界", default=0.1, min=-1.0, max=1.0,
        description="卡通明暗的分界线(0 = 正对光时一半亮一半暗)")
    p_style_shadow_smooth: FloatProperty(
        name="分界柔和", default=0.02, min=0.0, max=0.5,
        description="色阶边缘的柔和宽度")
    p_style_shadow_tint: FloatVectorProperty(
        name="阴影色", subtype="COLOR", size=4, min=0.0, max=1.0,
        default=(0.706, 0.627, 0.902, 1.0),
        description="暗部染上的颜色(偏冷紫 = 常见二次元暗部)")
    p_style_shadow_tint_2: FloatVectorProperty(
        name="深阴影色", subtype="COLOR", size=4, min=0.0, max=1.0,
        default=(0.50, 0.45, 0.74, 1.0),
        description="最暗那一档的颜色(三段明暗 > 0.5 时生效)")
    p_style_shadow_step_2: FloatProperty(
        name="深阴影分界", default=-0.45, min=-1.0, max=1.0,
        description="第二段阴影的分界位置")
    p_style_three_tone: FloatProperty(
        name="三段明暗", default=0.0, min=0.0, max=1.0,
        description="0 = 两段明暗;往上加第三段(高光/过渡层)")
    p_style_rim: FloatProperty(
        name="边缘光", default=0.0, min=0.0, max=4.0,
        description="物体轮廓的一圈光(0 = 关)")
    p_style_specular: FloatProperty(
        name="高光", default=0.0, min=0.0, max=4.0,
        description="表面高光强度(0 = 关)")


def _preview_light_scale_changed(settings, context):
    """『预览灯光倍率』改了 -> 立刻按新倍率重算预览里的灯(第 124 轮)。

    用户原话:"要不在那添加一个调整灯光倍率值的选项,这样就可以自己来
    精确的调"。所以系数要能实时拧。

    ⚠ 两个坑:
      1. **必须先还原再乘**。调暗的写法是"把原瓦数乘系数",如果在已经乘过
         的结果上再乘,拧三次 0.5 就变成 1/8 —— 越调越暗。所以走
         `godot_look._reset_lights()`,它先 undim 再按新系数 dim。
      2. **延迟导入** `godot_look`。properties 是被 nodes / codegen 反过来
         依赖的地基模块,顶部导入 godot_look 会形成循环。
    """
    import bpy

    try:
        from . import godot_look
    except Exception:                                      # noqa: BLE001
        return
    scenes = []
    if context is not None and getattr(context, "scene", None) is not None:
        scenes.append(context.scene)
    for scene in bpy.data.scenes:
        if scene not in scenes:
            scenes.append(scene)
    for scene in scenes:
        try:
            godot_look._reset_lights(scene)
        except Exception:                                  # noqa: BLE001
            pass


class GKSceneSettings(bpy.types.PropertyGroup):
    """挂在 Scene 上的导出设置。"""

    dimension: EnumProperty(
        name="维度",
        description="生成的游戏是 2D 还是 3D",
        items=(("3", "3D", "三维游戏"), ("2", "2D", "二维游戏")),
        default="3",
        update=_on_dimension_changed,
    )
    base_class: EnumProperty(
        name="基类",
        description=("脚本要 extends 的 Godot 节点类型。"
                     "默认『自动』= 跟着物体在『游戏物理』里选的类型走 —— "
                     "两者必须一致，否则 Godot 会拒绝加载脚本"),
        items=BASE_CLASSES,
        default=BASE_CLASS_AUTO,
    )
    class_name: StringProperty(
        name="类名",
        description="可选。填了会生成 class_name(只能用英文，中文会被忽略)",
        default="",
    )
    node_name: StringProperty(
        name="节点名",
        description="生成的 .tscn 里根节点的名字",
        default="Player",
    )
    # ── 本场景用哪棵游戏逻辑图(用户 2026-10-04)─────────────────────
    # 一个 .blend 里可以有好几个 Blender 场景(关卡1 / 关卡2 / ...),
    # 每个场景导出成自己的一张 .tscn。但"场景"和"节点图"之间原本没有
    # 绑定关系 —— 导出时只能靠"当前节点编辑器里打开的那棵",多场景时
    # 必然搞不清。这里显式绑定:某个场景用哪棵图。
    #   留空 = 自动:如果整个 .blend 只有一棵游戏逻辑图,就用它
    #          (单场景项目行为完全不变);有多棵又没选 -> 导出时告警跳过。
    logic_tree: StringProperty(
        name="逻辑图",
        description=("这个场景用哪棵『游戏逻辑』节点图。"
                     "留空 = 自动(只有一棵图时就用它)"),
        default="",
    )
    export_tunables: BoolProperty(
        name="暴露可调参数",
        description="把未连线的数值(速度 / 跳跃力度)生成为 @export，可在 Godot 检查器里调手感",
        default=True,
    )
    write_scene: BoolProperty(
        name="同时生成示例场景",
        description="额外生成一个最小 .tscn 与 project.godot，导出目录可直接用 Godot 打开",
        default=True,
    )

    # ── 导出瘦身(第一百零六轮 B)──────────────────────────────────────
    # 用户实测标定(8066 顶点 + 8 个形态键的球,导出 .glb):
    #   * 形态键关掉:8.16 MB -> 2.20 MB(**-73%**,体积最大的一块);
    #   * 切线关掉:  8.16 MB -> 7.66 MB(-6%);
    #   * 自定义属性关掉:8.16 MB -> 8.16 MB(**0 变化**,即使网格带第二层
    #     UV + 顶点色)—— 所以**不做**这个开关,只留上面两个。
    #   * Draco 压缩率很漂亮(412KB -> 42KB),但 **Godot 4.7 读不了**,
    #     所以在导出参数里**恒定关掉**(见 asset_export._slim_gltf_kwargs)。
    #
    # 为什么**默认都不勾**:关形态键 = 表情 / 形变(shape key / morph)没了,
    # 关切线 = 法线贴图会发暗发花。这种"用户自己知道才敢关"的开关,
    # 绝不能悄悄替他们打开 —— 宁可体积大,也不能让人一导出就丢表情。
    gk_skip_morph: BoolProperty(
        name="不导形态键",
        description=("导出时**不写形态键**(morph target)。"
                     "实测能省一大半体积(8.16MB -> 2.20MB)。"
                     "⚠ 用了表情 / 形变(shape key / morph)的模型别关 —— "
                     "关了表情就没了"),
        default=False,
    )
    gk_skip_tangents: BoolProperty(
        name="不导切线",
        description=("导出时**不写切线**(tangent)。省得不多(约 6%)。"
                     "⚠ 用了法线贴图的模型别关 —— 少了切线,法线贴图会发暗 / 发花"),
        default=False,
    )

    # (原 `show_log` 第五十五轮删掉:侧栏那份日志块删了,它没有使用者了。
    #  属性留着会让面板出现一个点了没反应的开关。)

    # ── 打包设置(第六十八轮)────────────────────────────────────────
    # 用户原话:"在侧边栏添加打包为 .exe,.apk 等的打包设置"。
    # 勾哪个 = 这次要打哪几个平台(一个都不勾 -> 打包时明确报错,不瞎猜)。
    #
    # ── 为什么存在**场景**上 ────────────────────────────────────────
    # 和 `dimension` / `logic_tree` 一个道理:随 .blend 走,换台电脑打开还在。
    # 放插件偏好里会变成"全局一份",两个项目互相串。
    # ⚠ 一个 .blend 里有好几个 Scene 时,用的是**当前场景**这一份。
    #
    # Windows 默认勾上:这是绝大多数人的目标平台,也保证老工程点『打包』
    # 的行为和以前**一模一样**(不会因为新增开关就变成"一个平台都没勾")。
    package_windows: BoolProperty(
        name="Windows (.exe)", default=True,
        description="打出 Windows 可执行文件(单个文件,拷走就能跑)")
    package_linux: BoolProperty(
        name="Linux (.x86_64)", default=False,
        description="打出 Linux 可执行文件")
    package_web: BoolProperty(
        name="Web (HTML5)", default=False,
        description="打出 Web 版(.zip,解压后用本地服务器打开)")
    package_android: BoolProperty(
        name="Android (.apk)", default=False,
        description=("打出安卓安装包。⚠ 需要先配好签名(keystore),"
                     "插件不替你生成密钥"))
    package_macos: BoolProperty(
        name="macOS", default=False,
        description=("⚠ Godot **只能在 macOS 主机上**导出 macOS,"
                     "别的系统点了会被跳过"))
    package_app_name: StringProperty(
        name="应用名",
        description="写进导出预设的应用名(留空 = 用项目文件夹名)",
        default="")
    package_version: StringProperty(
        name="版本",
        description="写进导出预设的版本号(主要给 Android 用)",
        default="1.0")
    package_android_id: StringProperty(
        name="Android 包名",
        description=("安卓的应用 ID,形如 com.example.mygame。"
                     "**必须**是唯一的,不填会用一个占位名"),
        default="com.example.game")

    # ── 项目顾问(第五十三轮)────────────────────────────────────────
    # 用户在这里写"我卡在哪",点『问 AI』才把问题 + 项目诊断快照发出去。
    # 存在**场景**上:重开 .blend 问题还在,不用重打一遍(写到一半去改图,
    # 回来继续问是常态)。
    advisor_question: StringProperty(
        name="你卡在哪",
        description=("描述遇到的问题(比如:角色不动 / 导出报错 / 镜头不对 / "
                     "打包失败)。点『问 AI』时会连同项目诊断一起发给模型"),
        default="",
    )
    # ── 上帝摄像机(用户 2026-10-04)────────────────────────────────
    # 原来这里是一个"主相机"下拉 + 拾取按钮。用户的原话:
    # "把现在的那个侧边栏的主摄像机改成其他要用的或者删了"。
    # 选定的方案:改成**上帝摄像机开关** —— 常驻一台自由飞行相机,
    # 运行时按 Tab 在"玩家相机 / 上帝摄像机"之间来回切。
    # 这台相机**独立于玩家相机**,不走物理(WASD 平移 / 空格上升 /
    # Shift 下降 / 鼠标转视角,可穿墙),用来像虚幻 5 的上帝视角那样
    # 满地跑着看这一关做得对不对。
    # ── ⚠ 这是**调试功能**,正式发行时应当关掉(用户 2026-10-15 明确指出)──
    # 设计原因写在 asset_export.ENVIRONMENT_SCRIPT 的 `god_camera` 文档注释里
    # (三条:绕开一切规则 = 内置外挂、是给开发者做关卡用的、键位会和玩法抢),
    # 面板上也照这个说法提示用户。默认 False,已发布的关卡预设才会顺手开
    # (见 presets.py 的 `with_god_camera`)。
    god_camera: BoolProperty(
        name="上帝摄像机", default=False,
        description=("常驻一台自由飞行相机(独立于玩家相机)。"
                     "运行时按 Tab 切换;WASD 平移 / 空格上升 / Shift 下降 / "
                     "鼠标转视角,不受碰撞。"
                     "⚠ 调试功能:正式发行前请关掉 —— 它能穿墙 / 飞出关卡 / "
                     "时停 / 附身敌人,等于内置外挂,会毁掉追逐潜行类玩法的张力"))
    # ── 主相机(留作自动解析用,侧栏不再单列一行)────────────────────
    # 整局游戏默认用哪台相机。留空 = 自动:
    #   1) 用 Blender 视口当前那台(scene.camera —— 就是你按小键盘 0 看的那台)
    #   2) 没有就取场景里第一台 Camera
    #   3) 一台都没有 -> 运行时造一台自由飞行观察机(见 codegen)
    main_camera: StringProperty(
        name="主相机",
        description="游戏默认用哪台相机;留空 = 跟着 Blender 视口的当前相机",
        default="",
    )
    player_spawn: PointerProperty(
        type=bpy.types.Object, name="玩家出生点",
        description="切换到本关时，将默认角色及其子对象移动到此物体的位置和朝向；推荐使用空物体。未设置时沿用默认角色的初始变换",
    )

    # ── 寻路 / 导航网格(第三十四轮)──────────────────────────────────
    # 用户选的方向:"寻路 / 避障 —— 敌人会绕墙追你"。
    # 打开后导出的场景里会多一个 NavigationRegion3D(名字 `Navigation`),
    # 它的**导航网格是游戏启动时烤的**:按场景里的**静态碰撞体**
    # (地面 / 墙 / 高台 / 树 / 石头 …)生成 —— 角色、载具这些会动的东西
    # 不会被烤进去(实测:真 Godot 里查过,CharacterBody 不参与)。
    #
    # ⚠ 关着时『移动_寻路』会**退化成直线走**(和『移动_朝向目标』一样),
    #   而不是站着不动 —— 少开一个开关不该让游戏坏掉。
    nav_enabled: BoolProperty(
        name="寻路", default=False,
        description=("在这个场景里烤一块导航网格:『移动_寻路』的角色会绕开"
                     "墙 / 障碍物走到目标,不再一头撞墙。\n"
                     "游戏启动时按**静态碰撞体**烤(会动的角色/载具不参与);\n"
                     "关着 = 『移动_寻路』退化成直线走(和『移动_朝向目标』一样)"))
    nav_agent_radius: FloatProperty(
        name="代理半径", default=0.5, min=0.05, max=10.0,
        description=("寻路的角色有多粗(米)—— 比这更窄的缝它不会钻过去。"
                     "一般填角色的碰撞半径"))
    nav_agent_height: FloatProperty(
        name="代理高度", default=1.8, min=0.1, max=10.0,
        description="寻路的角色有多高(米)—— 比这更矮的地方(桌子下面)它不走")
    nav_max_slope: FloatProperty(
        name="爬坡上限", default=45.0, min=0.0, max=89.0,
        description=("能爬多陡的坡(度)—— 比这陡的地方不算可走。"
                     "斜坡要接进寻路就把它调到大于斜坡的角度"))

    # ── AI 视线遮挡(第一百二十六轮:用户第 1 条"AI 遮挡射线")────────────
    # 用户原话:"1. AI 遮挡射线"。
    #
    # ── 修的是什么 ──────────────────────────────────────────────────
    # 『AI_看得见』以前只看 **距离 + 躲藏区 + 视野角**,**完全不看中间有没有
    # 东西挡着** —— 真机实测(4.7.2):把玩家放到 6 米高的『走廊墙北』外面,
    # 怪物到玩家的连线上射线命中的就是那堵墙,
    # `_gk_ai_can_see_3d(..., dist=100, angle=0)` 仍然返回 `true`。
    # 也就是**隔着墙能看见**,潜行玩法直接失效。
    #
    # ── 为什么挂在场景上、而不是挂在『AI_看得见』节点上 ──────────────
    # "这一关的墙挡不挡视线"是**关卡级**的事实(同一关里所有敌人共用),
    # 和『寻路』同一个性质 —— 所以跟 `nav_enabled` 并排放,而不是让用户在
    # 每个敌人节点上各勾一遍(勾漏一个就等于那一个还是千里眼)。
    #
    # ── 为什么默认 **开** ───────────────────────────────────────────
    # 用户的诉求就是"要能挡"(潜行是这批需求的玩法目标)。默认关的话,
    # 用户得先自己找到这个开关才有效果,等于没修。
    # ⚠ 影响面(已知,如实写在报告里):
    #   * 生成物变了 —— 勾了的老工程会**多出一段射线代码**。不勾的工程
    #     生成结果**逐字不变**(仓库里有一堆比对历史产物的断言);
    #   * 视线会被**任何**静态碰撞挡住 —— 包括示例里当路标用的实心
    #     `巡逻点甲/乙/丙`(胶囊,y≈1.0,正好在眼高线上)。这类"看不见的
    #     路标"以前不影响任何判定,现在会挡视线;
    #   * 高度按人形角色定(眼 1.4 米 / 胸口 1.0 米,见 codegen/ai.py):
    #     矮怪或巨型怪要自己改生成体里那两个常量。
    ai_sight_occluded: BoolProperty(
        name="AI 视线遮挡", default=True,
        description=("敌人的『AI_看得见』会被墙 / 障碍物挡住:从敌人的眼睛到"
                     "目标打一条射线,中间有静态碰撞就判看不见。\n"
                     "关掉 = 退回旧行为(只看距离 / 视野角 / 躲藏区,隔墙也能看见)\n"
                     "⚠ 关掉后生成结果逐字不变,老工程可以按需退回"))

    # ── 运行日志(用户第 29 轮:"角色在掉落,鼠标没反应,其他大概也有问题")──
    # 打开后,生成的脚本**每秒**在 Godot 控制台打一行:
    #   位置 / 速度 / 是否在地面 / 当前相机 / HUD 文本
    # 用户整段复制就能告诉我"运行时到底发生了什么" ——
    # 和『导出诊断信息』配成一对(那个管编辑期,这个管运行期)。
    debug_runtime: BoolProperty(
        name="运行日志", default=False,
        description="玩游戏时每秒在 Godot 控制台打一行状态(位置/速度/相机),排查用")

    # ── 游戏内调试面板(用户 2026-10-05 第二十二轮:"调试体验")─────────
    # 『运行日志』是往控制台打一行行字;这个是**玩的时候能看**的一块 HUD:
    # 按一个键弹出,显示 FPS / 相机 / 脚本宿主的位置速度在地面 /
    # 变量实时值(『变量』框里勾了『监视』的那些)/ 最近 20 条打印;
    # 另一个键把全部内容复制到剪贴板 —— 直接粘出来就能查。
    #
    # 面板由**环境脚本**(每张 .tscn 一份)生成,所以没有角色的场景也能用;
    # 变量和日志来自各自的游戏逻辑脚本(运行时按名字读)。
    debug_panel: BoolProperty(
        name="调试面板", default=False,
        description="游戏里按一个键弹出调试 HUD:FPS / 状态 / 变量实时值 / 最近日志,"
                    "可一键复制(粘贴出来就能排查)")
    debug_key: EnumProperty(
        name="面板键",
        description="按它弹出 / 收起调试面板",
        items=DEBUG_KEYS,
        default="F1",
    )
    debug_copy_key: EnumProperty(
        name="复制键",
        description="按它把调试面板的全部内容复制到剪贴板(也打到控制台)",
        items=DEBUG_KEYS,
        default="F2",
    )

    # ── 场景开局 BGM(第七十七轮,C 组 7『场景规则』)────────────────────
    # 以前想在开局放背景音乐,得自己接『每当_准备 → 音效_音乐』
    # (还得先把音频拷进 Godot 项目)。现在侧栏选一首就行。
    #
    # ── 为什么落在**环境脚本**(每场景一份)──────────────────────────
    # "开局放哪首歌"本来就是**一关一个**的规则(教室一首、走廊一首),
    # 而环境脚本天然就是每场景一份 —— 挂场景上、写进它,切场景自动跟着切。
    #
    # 选了它 = 导出时把音频**复制进项目的 audio/**(和『资产_声音卡』
    # 同一条管线、同一个命名规则),所以不会出现"Godot 里找不到文件"。
    scene_bgm: StringProperty(
        name="开局 BGM", default="", subtype="FILE_PATH",
        description="场景一开始就播的背景音乐(wav / ogg / mp3),循环播放。\n"
                    "留空 = 不播(想自己控制就照旧接『音效_音乐』节点)")
    scene_bgm_volume: FloatProperty(
        name="BGM 音量", default=-6.0,
        soft_min=-40.0, soft_max=6.0,
        description="开局 BGM 的音量(dB)。0 = 原始,负数是调小")

    # ── 游戏音乐设置(第八十六轮:E 组 14)──────────────────────────────
    # 『游戏音乐』设置图的『音乐_场景 BGM』卡上那几项 —— 和显示设置卡
    # 同一套"写透":卡上改 = 导出立刻按新值走,不存在"忘了点应用"。
    # 值还是挂在**场景**上(每关一套),切场景跟着切。
    scene_bgm_loop: BoolProperty(
        name="BGM 循环", default=True,
        description="开局 BGM 是否循环播放(关掉 = 播一遍就停)")
    scene_bgm_bus: StringProperty(
        name="BGM 总线", default="",
        description="开局 BGM 走哪条音频总线(留空 = Master)。\n"
                    "想在游戏里单独压背景音乐,先在 Godot 的『音频』面板"
                    "建一条 Music 总线,再把名字填这里")

    # 『音乐_总线音量』卡:开局把一条音频总线设到指定 dB。
    # 默认**不应用**(music_bus_apply = False)—— 老工程导出产物一字不变。
    music_bus: StringProperty(
        name="音乐总线", default="Music",
        description="要调音量的音频总线名(Godot『音频』面板里建过的名字)")
    music_bus_db: FloatProperty(
        name="总线音量", default=-6.0,
        soft_min=-60.0, soft_max=6.0,
        description="开局把上面那条总线设成这个音量(dB)")
    music_bus_apply: BoolProperty(
        name="开局应用", default=False,
        description="勾上 = 场景一开始就把总线音量设下去(不勾 = 什么都不做)")

    show_export_settings: BoolProperty(
        name="展开导出设置", default=False,
        description="侧栏『导出设置』默认收起 —— 那几项开一次就不用再动")

    # ── 卡通材质开关(用户第 24 轮:"一般是默认卡通渲染导出,但应该加个开关")──
    # 导出时默认把材质转成卡通风格(卡通描边),好处是"好看、风格统一";
    # 坏处是**运行画面和 Blender 预览差很多**,想核对细节就没法看了。
    # 关掉它 = 按原样导出,颜色/粗糙度/金属度都保持 Blender 里的设置。
    toon_materials: BoolProperty(
        name="卡通材质", default=True,
        description="导出时把材质转成卡通风格(关掉 = 原样导出,便于核对细节)")

    # ── 卡通跟随场景光照(用户 2026-10-04 第八轮:"导出有光照残留")──────
    # 卡通 shader 是 unshaded,但它**自己按 light_dir 画色阶**;导出脚本默认
    # 每物理帧把场景里的平行光同步进去 —— 于是场景有太阳时,模型身上会出现
    # 明暗带状,用户看成"光照残留"。默认**关** = 真正的二次元平涂;
    # 想要"转太阳、阴影跟着转"的效果再打开。
    toon_follow_light: BoolProperty(
        name="卡通跟随场景光照", default=False,
        description="使用场景中的太阳、点光和聚光灯，接收真实阴影和距离衰减；关闭使用固定卡通光照")

    # ── 环境与后处理(第四十九轮:阶段②环境与后处理)────────────────────
    # 给这一关挂一个 WorldEnvironment + Environment 资源:背景 / 环境光 /
    # 雾 / 泛光 / 色调。**默认关** —— 关着时产物里连节点都不多一个,
    # 老工程导出结果一字不变(和『上帝摄像机』同一套"新能力默认不打扰"的做法)。
    #
    # ── 为什么这些值挂在**场景**上(和显示设置图同一个道理)──────────
    # 一个 .blend 里好几个 Blender 场景 = 好几关,各关的场景氛围本就不一样
    # (教室是暖光、走廊是冷雾)。挂场景上,切场景卡上的值跟着切。
    env_enabled: BoolProperty(
        name="环境与后处理", default=False,
        description=("给这一关挂 WorldEnvironment:背景 / 环境光 / 雾 / 泛光 / 色调。"
                     "关着 = 不导出(和以前完全一样)"))
    # ── 预览用场景光照(第 124 轮:预览↔运行时对齐)────────────────────
    # 实测到的差距来源:Blender 的 SOLID 视口默认 `use_scene_lights=False`
    # + `light='STUDIO'` —— 用的是**跟着视角转的固定棚拍灯**,场景里那几盏
    # 灯它一盏都不看。于是"Blender 里调好了灯,进 Godot 完全不是那个样子"
    # (实测:同一关 Blender 渲染 0.76 / Godot 运行时 0.38)。
    # 打开后『Godot 观感』会把视口切成"真用场景灯 + 真用世界 + 画阴影",
    # 预览才和运行时是**同一个光照配方**。
    #
    # 为什么做成开关而不是直接改:视口观感是用户自己的地盘 —— 有人就喜欢
    # 棚拍灯那种干净平光(看得清模型),不该被插件强制改掉。默认开是因为
    # "预览要能代表运行画面"是『Godot 观感』这个功能的**本意**。
    preview_scene_lights: BoolProperty(
        name="预览用场景光照", default=True,
        description=("『Godot 观感』生效时,让视口用场景里的真实灯光画明暗"
                     "(而不是 Blender 默认那盏跟着视角转的棚拍灯)。"
                     "关掉 = 只对齐色调映射 / 背景 / 雾,不动光照方式"))
    # ── 预览灯光倍率(第 124 轮,用户要求"自己来精确的调")──────────────
    # 自动那档(0.06)是**实测标定**出来的:预览 0.9130 vs 运行 0.3953,
    # 而且亮的几乎全是灯(把世界强度调到 0 只让 0.9130 动到 0.9123)。
    # 但那个标定是"一个场景、一个机位"量出来的 —— 用户实测觉得变化不明显,
    # 所以这里把**系数本身**交出去:0.01~2.0 随便拧,改完立刻重算。
    #
    # 为什么要有 update 回调:调暗是"把灯乘一个系数",改了系数必须**先还原
    # 再乘**(不能在上一次乘完的结果上再乘 —— 那会越调越暗)。回调里调
    # godot_look 的 `_reset_lights` 做这件事(延迟导入,避免循环依赖)。
    preview_light_scale: FloatProperty(
        name="预览灯光倍率", default=0.06, min=0.01, max=2.0,
        description=("『Godot 观感』里把场景灯光乘多少倍(1.0 = 不动)。"
                     "默认 0.06 是实测标定值(预览原本比运行亮一倍多)。"
                     "觉得还是太亮就往小拧、太暗往大拧 —— 改完立刻生效"),
        update=_preview_light_scale_changed)
    # ── 侧栏"关卡打勾"(第一百三十七轮,用户要求)───────────────────────
    # 用户原话:"在游戏环境与后期侧边栏添加选项框,选择一个关卡(选项有关卡
    # 名称,全部,多选,可以设置成那种打勾的选择)"。
    # 勾上的关卡会被侧栏里『套给勾选的关卡』/『一键贴近运行画面』一起改 ——
    # 关卡本身仍是**各存一份设置**(切场景卡上的值跟着切),这里只是一份
    # "这次要动哪几关"的清单。
    look_target: BoolProperty(
        name="一起改", default=True,
        description="勾上的关卡会被侧栏『套给勾选的关卡』/『一键贴近运行画面』"
                    "一起改(默认全勾)")
    env_background: EnumProperty(
        name="背景", items=ENV_BACKGROUNDS, default="COLOR",
        description="纯色 = 用下面的背景色铺满;天空 = 程序化天空(天空色跟着背景色走)")
    env_bg_color: FloatVectorProperty(
        name="背景色", subtype="COLOR", size=4, min=0.0, max=1.0,
        default=(0.05, 0.06, 0.10, 1.0),
        description="背景色(天空模式下当作天空的顶色)")
    env_ambient_color: FloatVectorProperty(
        name="环境光颜色", subtype="COLOR", size=4, min=0.0, max=1.0,
        default=(0.55, 0.62, 0.80, 1.0),
        description="全局补光颜色 —— 幽暗场景给一点冷蓝,免得背光面死黑")
    env_ambient_energy: FloatProperty(
        name="环境光强度", default=0.3, min=0.0, soft_max=4.0,
        description="环境光有多亮;0 = 没有环境光(纯靠灯照)")
    env_fog: BoolProperty(
        name="雾", default=False,
        description="体积雾 —— 恐怖片『空气感』的主力(走廊尽头糊掉 / 手电筒打出光柱)")
    env_fog_color: FloatVectorProperty(
        name="雾颜色", subtype="COLOR", size=4, min=0.0, max=1.0,
        default=(0.30, 0.34, 0.42, 1.0))
    env_fog_density: FloatProperty(
        name="雾浓度", default=0.02, min=0.0, soft_max=1.0,
        description="越大越浓(0.02 是『远处发灰』,0.1 以上就伸手不见五指)")
    # ── 高度雾(第一百四十八轮)──────────────────────────────────────
    # Godot 的 `fog_height / fog_height_density` 是一组**独立**于密度的
    # 整形参数:让雾"贴着地面浓、往上淡"。走廊 / 街道 / 地窖这种场景,
    # 平的深度雾只会把远处糊白,而高度雾能把地面压出一层"贴地的雾毯"、
    # 天花板保持干净 —— 纵向层次一下就出来了。
    # 出厂值都是 0.0(= 关),所以不勾这两项时产物一字不变(铁律)。
    env_fog_height: FloatProperty(
        name="雾高度", default=0.0, soft_min=-50.0, soft_max=50.0,
        description="高度雾的基准高度(米,Godot 世界坐标)。和『雾层厚度』"
                    "一起用;两个都 0 = 不启用高度雾")
    env_fog_height_density: FloatProperty(
        name="雾层厚度", default=0.0, min=0.0, soft_max=2.0,
        description="高度雾的浓度:越大越『贴地』,高处越干净。"
                    "0.1~0.4 是走廊 / 街道的常用档(0 = 不启用)")
    env_glow: BoolProperty(
        name="泛光", default=False,
        description="亮的地方往外溢光 —— 灯 / 手电筒 / 火光'毛'起来")
    # ── 体积雾(第一百四十八轮)──────────────────────────────────────
    # 和上面的『雾』是两套东西:
    #   * 雾(fog_*)       = **深度雾**,按距离把远处糊掉,便宜,只影响颜色;
    #   * 体积雾(volumetric)= **有体积的介质**,灯光会在里面真的"打出光柱"
    #     (窗格投影 / 手电筒光锥 / 吊灯周围那团亮雾)—— 这是恐怖场景里
    #     "空气感"最直接的来源,也是 Godot Forward+ 的看家能力。
    # 默认关、所有值等于 Godot 出厂,所以老项目产物一字不变。
    env_volumetric: BoolProperty(
        name="体积雾", default=False,
        description="让灯在空气里打出可见光柱(窗格光、手电筒光锥)。"
                    "比普通雾贵,少量使用即可;需要 Godot Forward+")
    env_volumetric_density: FloatProperty(
        name="体积雾浓度", default=0.05, min=0.0, soft_max=0.5,
        description="介质有多稠(0.02~0.08 是室内常用档;越大光柱越白)")
    env_volumetric_albedo: FloatVectorProperty(
        name="体积雾颜色", subtype="COLOR", size=4, min=0.0, max=1.0,
        default=(0.85, 0.89, 1.0, 1.0),
        description="雾滴反射出来的颜色(夜里偏冷蓝,地窖偏土黄)")
    env_volumetric_anisotropy: FloatProperty(
        name="前向散射", default=0.2, min=-1.0, max=1.0,
        description="0.2~0.6 = 顺着光看更亮(真实雾气),负值 = 逆光更亮")
    env_volumetric_length: FloatProperty(
        name="体积雾距离", default=64.0, min=1.0, soft_max=256.0,
        description="体积雾的有效距离(米)。走廊 48 米填 48~64,"
                    "再远的部分交给深度雾")
    env_volumetric_ambient: FloatProperty(
        name="环境注入", default=0.0, min=0.0, max=1.0,
        description="把环境光灌进体积雾(0 = 只被灯照亮,更戏剧化)")
    env_ssao: BoolProperty(
        name="接触阴影 (SSAO)", default=False,
        description="增强墙角、地面接触处的遮蔽；需要 Godot Forward+ 渲染器")
    env_ssil: BoolProperty(
        name="间接补光 (SSIL)", default=False,
        description="使用屏幕内的光照信息补充暗部层次；需要 Godot Forward+ 渲染器")
    # ── SSAO 的三个真正起作用的旋钮(第一百四十八轮)─────────────────
    # 以前只有一个"接触阴影"开关,强度 / 半径 / 是否影响直射光全是 Godot
    # 出厂值。实机测过:默认真空 —— `ssao_light_affect = 0` 时 SSAO **只
    # 压暗环境光**,而卡通场景的暗部本来就靠环境光,于是"勾了接触阴影,
    # 画面上什么也没发生"(示例走廊实测暗部只差 2/255)。
    # 三个键的出厂值都等于 Godot 默认,所以拧回 0 就不会多写一行。
    env_ssao_intensity: FloatProperty(
        name="接触阴影强度", default=2.0, min=0.0, soft_max=8.0,
        description="SSAO 压暗的力度(Godot 出厂 2.0;4~6 才有明显接触阴影)")
    env_ssao_radius: FloatProperty(
        name="接触阴影半径", default=1.0, min=0.05, soft_max=8.0,
        description="SSAO 采样半径(米)。小 = 只压缝隙,大 = 大块遮蔽"
                    "(Godot 出厂 1.0)")
    env_ssao_light_affect: FloatProperty(
        name="阴影影响直射光", default=0.0, min=0.0, max=1.0,
        description="0 = SSAO 只压环境光(Godot 出厂,常温和看不出效果);"
                    "0.5~0.8 = 连直射光一起压 —— 墙角 / 家具根部才会真的暗下去")
    render_quality: EnumProperty(
        name="启动画质", default="KEEP",
        items=(("KEEP", "沿用工程", "保留 Godot 工程的渲染设置"),
               ("HIGH", "高画质", "原生分辨率、4x 抗锯齿、4096 阴影图和高质量软阴影")),
    )
    env_glow_intensity: FloatProperty(
        name="泛光强度", default=0.8, min=0.0, soft_max=4.0)
    env_glow_bloom: FloatProperty(
        name="泛光溢出", default=0.1, min=0.0, soft_max=1.0,
        description="整体画面的额外柔光(0 = 只让亮点发光)")
    env_tonemap: EnumProperty(
        name="色调映射", items=ENV_TONEMAPS, default="LINEAR")
    env_adjust: BoolProperty(
        name="色调调整", default=False,
        description="手动调亮度 / 对比度 / 饱和度(调色)")
    env_brightness: FloatProperty(
        name="亮度", default=1.0, min=0.0, soft_max=2.0)
    env_contrast: FloatProperty(
        name="对比度", default=1.0, min=0.0, soft_max=2.0)
    env_saturation: FloatProperty(
        name="饱和度", default=1.0, min=0.0, soft_max=2.0)

    # ── 第 124 轮:Godot Environment 的**高影响键**(真机实测后补的)──────
    # 背景:导出器以前写的 19 个键**全是 Godot 默认值的子集** —— 缺的这几个
    # 才是真正改变画面的杠杆。真机在示例走廊上逐项开关测出的帧均值亮度:
    #     出厂 0.3803;`fog_sky_affect=0` -> 0.3460(雾色整个盖掉了背景色);
    #     `fog_light_energy=0.25` -> 0.2599(雾亮度是仅次于密度的杠杆);
    #     `glow_hdr_threshold=0 + glow_strength=3` -> 0.7247(泛光的真扳机)。
    # 所以下面这些不是"锦上添花的旋钮",是"够不着的旋钮"。
    #
    # 向后兼容:这些字段的**默认值全部等于 Godot 出厂值**,而且导出侧按
    # "值 ≠ 出厂值才写"的条件输出 —— 所以老项目、老示例(恐怖示例等)的产物
    # **逐字节不变、观感也不变**。想要新效果就在卡上把值拧离出厂值。
    #
    # ⚠ `fog_sky_affect` 一度曾把默认值定成 0.0(那样一开雾就自动修掉"雾色
    #   盖掉背景色"),但真机实测发现它会把**恐怖示例**整帧压暗 70%
    #   (0.1422 -> 0.0434,那个示例的背景本来就是近黑、靠雾色垫亮的)。
    #   本仓库有一条铁律:"老工程与示例的观感不能因为新功能而变",所以这里
    #   退回出厂值 1.0,改成**在『卡通二次元』那几档内置预设 + 示例里显式写 0**
    #   —— 谁想要谁开,不替所有人做决定。
    #
    # 雾那一组
    env_fog_sky_affect: FloatProperty(
        name="雾盖背景", default=1.0, min=0.0, max=1.0,
        description=("雾色盖掉背景(天空 / 纯色背景色)的程度。1 = Godot 出厂"
                     "行为:雾色把背景整个换掉(远景糊成一片雾色,和 Blender "
                     "预览对不上);**0 = 用户选的背景色真的看得见**(推荐,"
                     "和预览一致)"))
    env_apply_all_keep_post: BoolProperty(
        name="只套基础环境,不动后期", default=False,
        description=("『套用到全部关卡』时,只复制背景 / 环境光 / 雾,"
                     "不覆盖别的关卡已经调过的泛光与色调调整 —— "
                     "免得一关特意调的后期被冲掉"))
    light_push_by_type: BoolProperty(
        name="按类型刷灯", default=False,
        description=("『灯光刷到全部关卡』按**灯的类型**匹配(所有平行光 / 所有"
                     "点光 / 所有聚光),而不是按名字 —— 三关的灯名字通常都"
                     "不一样(『走廊壁灯』vs『大堂吊灯』),按名字基本刷不到。"
                     "⚠ 同一类型有多盏时**都会**被盖成同一个设置(会冲掉某一关"
                     "特意调过的灯),Ctrl+Z 可撤;所以默认关着,要用再勾"))
    env_fog_light_energy: FloatProperty(
        name="雾亮度", default=1.0, min=0.0, soft_max=4.0,
        description=("雾色整体亮度缩放。仅次于雾浓度的雾杠杆:调小让雾变暗"
                     "(恐怖片常用 0.25~0.5),调大雾会发白发光"))
    env_fog_aerial_perspective: FloatProperty(
        name="远景空气感", default=0.0, min=0.0, max=1.0,
        description=("远处的雾跟着天空颜色走(空气透视)。0 = 雾色恒定"
                     "(默认);往大调远景越像隔着空气看 —— 强杠杆,慎用"))
    # 泛光那一组
    env_glow_strength: FloatProperty(
        name="泛光力度", default=1.0, min=0.0, soft_max=4.0,
        description=("泛光真实力度。Godot 默认 1.0,但配合默认门槛 1.0 时泛光"
                     "几乎是死的;要看到辉光通常是『门槛降到 0.8 以下 + "
                     "力度拉到 2~3』这一对一起动"))
    env_glow_threshold: FloatProperty(
        name="泛光门槛", default=1.0, min=0.0, soft_max=2.0,
        description=("多亮的像素才泛光。Godot 默认 1.0 = 只有超过白点的像素"
                     "发光,所以『开了泛光却看不到』多半是这里;降到 0.6~0.9 "
                     "让灯和亮面真的毛起来(真机实测:0 时整帧亮度 0.38->0.72)"))
    env_glow_blend: EnumProperty(
        name="泛光混合", items=ENV_GLOW_BLENDS, default="SCREEN",
        description="泛光和原图的混合方式(默认『滤色』)")
    # 色调那一组
    env_tonemap_exposure: FloatProperty(
        name="曝光", default=1.0, min=0.0, soft_max=4.0,
        description=("全局曝光倍率(1.0 = 不变)。色调映射**之前**乘上去,"
                     "所以暗关拉亮 / 亮关压暗都从这里走"))
    env_tonemap_white: FloatProperty(
        name="白点", default=1.0, min=0.1, soft_max=8.0,
        description=("白点(色调映射映射到最亮的那一个值)。**线性(LINEAR)"
                     "色调映射下 Godot 会忽略它** —— 选 Reinhard / Filmic / "
                     "ACES / AgX 之后才有意义;调小 = 整体更亮更灰"))
    # 背景那一组
    env_ambient_sky_contribution: FloatProperty(
        name="天空参与环境光", default=1.0, min=0.0, max=1.0,
        description=("环境光里『天空』占多少。**只在天空背景模式下生效**"
                     "(纯色模式下真机实测无效);0 = 环境光完全用下面填的"
                     "环境光颜色,1 = 全交给天空"))

    # ── 全局卡通风格(第一百一十五轮:环境系统大蓝图阶段 B)────────────
    # 场景级 shader 风格:导出时作为**默认**写进没调过参数的材质 .tres
    # (等于"整关一键定风格",不用逐材质调)。这些值对应 gk_toon.gdshader
    # 的同名 uniform,语义见 npr_preview.PARAM_MAP。关着 = 老默认值,
    # 老项目产物一字不变。
    style_enabled: BoolProperty(
        name="全局卡通风格", default=False,
        description="给整关定一套卡通风格(色阶 / 阴影色 / 边缘光 / 高光)。"
                    "导出时作为默认写进材质,不用逐材质调")
    style_shadow_step: FloatProperty(
        name="明暗分界", default=0.1, min=-1.0, max=1.0,
        description="色阶分界在 dot(法线, 光向) 的哪个位置。0.1 几乎是"
                    "'全亮',想看到明显的暗面就抬到 0.25~0.4")
    style_shadow_smooth: FloatProperty(
        name="分界柔和", default=0.02, min=0.0, max=0.5,
        description="色阶边缘的柔和宽度。0.01~0.03 = 硬边卡通;"
                    "0.1 以上接近渐变(默认 0.02 = 导出模板的老值)")
    style_shadow_tint: FloatVectorProperty(
        name="阴影色", subtype="COLOR", size=4, min=0.0, max=1.0,
        default=(0.706, 0.627, 0.902, 1.0),
        description="中间阴影(浅黑)的颜色")
    # ── 三色阶阴影(第一百五十一轮)─────────────────────────────────
    # 用户原话:"阴影的渲染上太单调的,不是浅黑,深黑,暗黑这样的过渡"。
    # 原来模板只写 shadow_tint(一档),三段的第二档用的是 shader 内置默认值,
    # 场景设置里根本调不到。现在把"深阴影色 / 深阴影分界"也开放出来:
    #   亮部 -> shadow_tint(浅黑) -> shadow_tint_2(深黑/暗黑)
    # 只有三段明暗 > 0.5 时第二档才参与(和 shader 的判据一致)。
    style_shadow_tint_2: FloatVectorProperty(
        name="深阴影色", subtype="COLOR", size=4, min=0.0, max=1.0,
        default=(0.50, 0.45, 0.74, 1.0),
        description="最暗那一档的颜色(深黑 / 暗黑),只在三段明暗 > 0.5 时生效")
    style_shadow_step_2: FloatProperty(
        name="深阴影分界", default=-0.45, min=-1.0, max=1.0,
        description="第二段阴影从哪开始(-0.45 = 背光面最深处)")
    style_three_tone: FloatProperty(
        name="三段明暗", default=0.0, min=0.0, max=1.0,
        description="0 = 两段明暗;> 0.5 启用第三段(浅黑 / 深黑两档阴影)")
    style_rim: FloatProperty(
        name="边缘光", default=0.0, min=0.0, max=4.0)
    style_specular: FloatProperty(
        name="高光", default=0.0, min=0.0, max=4.0)

    # ── 环境预设(第一百一十三轮)───────────────────────────────────
    # 每个场景一份预设表:侧栏环境卡里存 / 应用;游戏里用
    # 『环境_应用预设』节点按名字切换。
    env_presets: CollectionProperty(type=GKEnvPreset)
    env_preset_pick: EnumProperty(
        name="预设", items=_env_preset_items,
        description="选一个预设(应用后才写进上面的设置)")
    env_preset_new_name: StringProperty(
        name="存为预设", default="我的氛围",
        description="把当前『环境与后处理』的一套值存成这个名字的预设")

    output_dir: StringProperty(
        name="导出目录",
        description="GDScript 输出到哪里。留空 = 跟着 .blend 文件走(推荐)",
        default="",
        subtype="DIR_PATH",
    )
    project_dir: StringProperty(
        name="项目目录",
        description=("手动指定 Godot 项目根目录。留空时跟着 .blend 走:\n"
                     "项目建在 .blend 旁边,文件夹名 = 文件名 + _godot"),
        default="",
        subtype="DIR_PATH",
    )


# ==========================================================================
# 动画树的自定义变量(照虚幻蓝图的 My Blueprint 那一栏)
# ==========================================================================
# 用户原话:"在侧边栏可以添加变量，自定义变量引脚连线"。
#
# ── 为什么存成 PropertyGroup + CollectionProperty ──────────────────
# 变量属于**这一张动画树**(= 虚幻里的一"个"动画蓝图),不是全局的:
# 两张动画树各有一套变量,互不串味。挂在 NodeTree 子类上最贴切。
#
# ── 为什么不能叫 `name`(踩过的坑)────────────────────────────────
# `PropertyGroup` 自带一个 `name` 属性(CollectionProperty 用它当条目名),
# 再声明一个 StringProperty(name="name") 会被 Blender 拒绝注册。
# 所以变量名存在 `var_name` 上。
class GKAnimVariable(bpy.types.PropertyGroup):
    """动画树里的一个自定义变量。"""

    var_name: StringProperty(
        name="变量名",
        description="导出成 GDScript 的成员变量名(中文会被转成合法的英文名)",
        default="新变量",
    )
    var_type: EnumProperty(
        name="类型",
        description="这个变量存什么",
        items=(("bool", "布尔", "真/假 —— 可以直接当转场条件"),
               ("int", "整数", ""),
               ("float", "浮点", ""),
               ("string", "文本", ""),
               ("vector", "向量", "Vector3")),
        default="float",
    )
    default_value: StringProperty(
        name="初值",
        description="游戏开始时这个变量的值",
        default="0",
    )
    comment: StringProperty(
        name="说明",
        description="写给自己看的备注(不影响导出)",
        default="",
    )


class GKLogicPort(bpy.types.PropertyGroup):
    """一张**游戏逻辑图**对外声明的一个端口(用户第 21 轮要的"真接口")。

    ── 为什么要有它(而不是继续用『逻辑_输出结果』随便填名字)──────────
    原来是"节点上填个名字 + 别的图用『变量_读取』读 `输出_名字`" ——
    名字全靠自觉:**打错一个字母不会报任何错**,运行时读到的是空值,
    然后你会以为是逻辑没跑到。
    现在改成:图**先声明端口**(名字 + 类型),节点上从下拉里挑;
    导出时再校验一遍"被读写的端口有没有被声明过",没有就明确告警。
    """
    port_name: StringProperty(
        name="端口名",
        description="导出成脚本变量 `输出_<端口名>`;别的图用同名端口读它",
        default="完成",
    )
    port_type: EnumProperty(
        name="类型",
        description="这个端口是什么类型(别的图按这个类型读)",
        items=(("bool", "布尔", "真/假 —— 最常用:状态、开关、是否完成"),
               ("int", "整数", ""),
               ("float", "浮点", ""),
               ("string", "文本", ""),
               ("vector", "向量", "Vector3")),
        default="bool",
    )
    direction: EnumProperty(
        name="方向",
        description="输出 = 这张图给别人用;输入 = 这张图要别人给",
        items=(("out", "输出", "这张图产生、别的图读"),
               ("in", "输入", "别的图产生、这张图读")),
        default="out",
    )
    default_value: StringProperty(
        name="初值",
        description="游戏开始时这个端口的值",
        default="false",
    )
    comment: StringProperty(
        name="说明",
        description="写给自己看的备注(不影响导出)",
        default="",
    )


class GKLogicVariable(bpy.types.PropertyGroup):
    """一张**游戏逻辑图**里预先声明的一个变量(用户 2026-10-04)。

    ── 为什么要有它(而不是继续隐式声明)────────────────────────────
    玩法图的变量原来是**隐式声明**的:第一次『变量_设置』写它,就自动
    成为成员变量,类型和初值都从那一刻的连线/默认值现推。带来的问题:
      * "这张图里到底有哪些变量、各是什么类型"只能一个个节点翻;
      * 同一个变量在两处被写得类型不一致时,导出只给一条告警,很容易漏;
      * 想给变量一个明确的初始值,得指望"第一个写它的节点"恰好写对。

    现在可以在侧栏**一处声明**(名字 + 类型 + 初值),节点上从下拉里挑。
    没声明过的变量仍然照旧能自动建 —— 老图一个字都不用改。
    """
    var_name: StringProperty(
        name="变量名",
        description="导出成 GDScript 的成员变量名(中文会被转成合法的英文名)",
        default="新变量",
    )
    var_type: EnumProperty(
        name="类型",
        description="这个变量存什么",
        items=(("bool", "布尔", "真/假 —— 可以直接当判断条件"),
               ("int", "整数", ""),
               ("float", "浮点", ""),
               ("string", "文本", ""),
               ("vector", "向量", "Vector3")),
        default="float",
    )
    default_value: StringProperty(
        name="初值",
        description="游戏开始时这个变量的值",
        default="0",
    )
    comment: StringProperty(
        name="说明",
        description="写给自己看的备注(不影响导出)",
        default="",
    )
    # ── 分组 / 文件夹(用户 2026-10-06 第三十二轮)───────────────────
    # 变量一多(几十个)侧栏就是一长条,找东西全靠翻。填同一个组名就归到
    # 一组,侧栏按组折叠显示;留空 = 未分组(排在最后)。
    # **只影响面板显示,不影响导出** —— 生成的成员变量名一个字不变。
    group: StringProperty(
        name="分组",
        description=("把变量归到一个分组里(侧栏『变量』框按分组折叠显示)。\n"
                     "填同一个名字 = 同一组;留空 = 未分组,排在最后。\n"
                     "只影响面板显示,不影响导出"),
        default="",
    )
    # ── 调试面板要看哪个变量(用户 2026-10-05 第二十二轮)─────────────
    # 勾上的变量会出现在**游戏内调试面板**里(实时值)。
    # 一个都不勾 = 面板显示**脚本里所有**变量(新项目先这样看一遍最省事)。
    watch: BoolProperty(
        name="监视",
        description="调试面板里实时显示它的值;不勾就不显示它"
                    "(图里**实际用到**的变量和对外端口总会显示)",
        default=True,
    )
    # ── 变量的"实现"(用户 2026-10-05 第十七轮:需求 1 剩的那半)────────
    # 照 UE5 那种:变量可以绑定 getter / setter。
    # 读这个变量时走 getter(计算出来的值),写它时走 setter(顺带做别的事)。
    # 例:`血量` 的 getter 是"计算血量"、setter 是"受伤扣血" ——
    # 图里照样用『变量_读取』『变量_设置』,生成出来却是函数调用。
    getter: StringProperty(
        name="读取时调用",
        description=("读这个变量时改调这个函数(留空 = 直接读变量)。"
                     "函数要**无参数、有返回值**(类型和变量一致)"),
        default="",
    )
    setter: StringProperty(
        name="写入时调用",
        description=("写这个变量时改调这个函数(留空 = 直接赋值)。"
                     "函数要**正好一个参数**"),
        default="",
    )


def logic_var_groups(tree):
    """把声明区的变量按『分组』排好:返回 [(组名, [变量, ...]), ...]。

    ── 排布规矩(照"文件夹"的直觉,用户 2026-10-06 第三十二轮)─────────
      * 有组名的按**第一次出现的顺序**排 —— 不按字母重排(用户摆的顺序
        就是展示顺序,重新排序会让人找不到刚建的组);
      * 组名留空的归到最后的『未分组』(这里用空串当组名,调用方自己翻译);
      * 组里没有变量就不会出现(空组不用手动清理);
      * **只看显示,不参与导出** —— 返回的就是原来那批变量对象,只是分了堆。
    """
    groups = {}
    for var in (getattr(tree, "gk_vars", None) or ()):
        name = str(getattr(var, "group", "") or "").strip()
        groups.setdefault(name, []).append(var)
    out = [(name, items) for name, items in groups.items() if name]
    loose = groups.get("", [])
    if loose:
        out.append(("", loose))
    return out


def logic_var_group_of(tree):
    """{变量名: 组名} —— 给『引用区』按同一套分组显示用(未分组是空串)。"""
    out = {}
    for var in (getattr(tree, "gk_vars", None) or ()):
        name = str(getattr(var, "var_name", "") or "").strip()
        if name:
            out[name] = str(getattr(var, "group", "") or "").strip()
    return out


def variable_implementations(tree):
    """读出变量的"实现"绑定:{变量名: {"getter": 函数名, "setter": 函数名}}。

    ── 为什么要单独一张表(用户 2026-10-05)──────────────────────────
    『变量_读取』『变量_设置』和代码生成都要查它(一处声明、两处一致)。
    只收非空的绑定 —— 没绑定的变量照旧是普通成员变量,老图产物一个字符不变。
    """
    out = {}
    for var in (getattr(tree, "gk_vars", None) or ()):
        name = str(getattr(var, "var_name", "") or "").strip()
        if not name:
            continue
        getter = str(getattr(var, "getter", "") or "").strip()
        setter = str(getattr(var, "setter", "") or "").strip()
        if getter or setter:
            out[name] = {"getter": getter, "setter": setter,
                         "type": str(getattr(var, "var_type", "") or "")}
    return out


def _on_function_decl_changed(self, _context):
    """函数声明改了 -> 图里引用它的节点要**重长引脚**(用户 2026-10-05)。

    参数从 1 个改成 3 个时,『函数_调用』上的参数框必须跟着变 ——
    不重长的话用户"改了声明却看不到变化",以为没生效(和『定时_时间轴』
    改轨道数要立刻长引脚同一个道理)。

    ⚠ 用 `rebuild_sockets` 而不是 `build_sockets`(第八十九轮):前者
    按**引脚名字**把连线记下来、重建完再接回去 —— 改名字时参数引脚
    名字没变,已经接好的实参线不会断(见 node_base.rebuild_sockets 的说明)。
    """
    tree = getattr(self, "id_data", None)
    for node in getattr(tree, "nodes", ()) or ():
        if getattr(node, "bl_idname", "") not in ("GKNodeFunctionCall",
                                                  "GKNodeFunctionDefine"):
            continue
        builder = (getattr(node, "rebuild_sockets", None)
                   or getattr(node, "build_sockets", None))
        if callable(builder):
            try:
                builder()
            except Exception:                             # noqa: BLE001
                pass


class GKLogicFunction(bpy.types.PropertyGroup):
    """一张**游戏逻辑图**里预先声明的一个函数(用户 2026-10-05 第十七轮)。

    ── 为什么要有它(用户原话)──────────────────────────────────────
    "查看函数,自定义函数,折叠函数等" —— 现在整张图只能生成固定的
    `_ready` / `_physics_process`,想复用一段逻辑只能**复制节点**。

    有了它就能一处声明、多处调用:声明处写名字 / 返回类型 / 参数,
    图里放『函数_定义』接函数体、放『函数_调用』去调用。
    调用点的参数引脚会按这里的声明**自动长出来**。

    ── 参数怎么写 ──────────────────────────────────────────────────
    `参数` 一栏用「名字:类型」写,逗号(中英文都认)或分号分隔,比如:

        hp:float, amount:float

    留空 = 无参数。类型不认识时会退回 float 并给一条导出告警。
    """
    func_name: StringProperty(
        name="函数名",
        description="导出成 GDScript 的函数名(中文会被压成合法英文名)",
        default="新函数",
        update=_on_function_decl_changed,
    )
    return_type: EnumProperty(
        name="返回",
        description="这个函数返回什么;『无』= 只做事不给值",
        items=(("void", "无返回", "只做事,不给值(最常用)"),
               ("bool", "布尔", ""),
               ("int", "整数", ""),
               ("float", "浮点", ""),
               ("string", "文本", ""),
               ("vector", "向量", "Vector3")),
        default="void",
        update=_on_function_decl_changed,
    )
    params: StringProperty(
        name="参数",
        description="『名字:类型』,逗号分隔。例:hp:float, amount:float(留空 = 无参数)",
        default="",
        update=_on_function_decl_changed,
    )
    comment: StringProperty(
        name="说明",
        description="写给自己看的备注(不影响导出)",
        default="",
    )


# 函数参数里能写的类型(写别的会退回 float + 一条告警)
FUNCTION_PARAM_TYPES = {
    "bool": "bool", "int": "int", "float": "float", "string": "String",
    "vector": "Vector3", "vector3": "Vector3", "vector2": "Vector2",
    "node": "Node", "text": "String",
}
# 显示层用:枚举 id -> GDScript 类型(和 GKLogicFunction.return_type 对应)
FUNCTION_RETURN_TYPES = {
    "void": "", "bool": "bool", "int": "int", "float": "float",
    "string": "String", "vector": "Vector3",
}


def parse_function_params(text):
    """把『参数』那一栏的文本解析成 [(名字, GDScript 类型)]。

    认 `,` `;` `，` `；` 四种分隔;每一项形如 `hp:float`。
    名字缺类型 -> float;类型不认识 -> float(由导出那边给告警)。
    返回 [(名字, 类型, 是否认得这个类型)] —— 第三项给告警用。
    """
    out = []
    text = str(text or "")
    for chunk in text.replace("，", ",").replace("；", ";") \
            .replace(";", ",").split(","):
        item = chunk.strip()
        if not item:
            continue
        if ":" in item:
            name, raw_type = item.split(":", 1)
        elif "：" in item:
            name, raw_type = item.split("：", 1)
        else:
            name, raw_type = item, ""
        name = name.strip()
        if not name:
            continue
        kind = raw_type.strip().lower()
        known = True
        if not kind:
            kind = "float"
        if kind not in FUNCTION_PARAM_TYPES:
            known = False
            kind = "float"
        out.append((name, FUNCTION_PARAM_TYPES[kind], known))
    return out


def tree_functions(tree):
    """读出这张图声明的所有函数:{名字: {"return": 类型, "params": [(名, 类型)]}}。

    ── 为什么从这里统一取(用户 2026-10-05 第十七轮)────────────────
    『函数_定义』『函数_调用』两个节点和代码生成都查这一张表 ——
    一处声明、三处一致。名字重复时**只保留第一个**(导出时会另外给告警,
    否则 Godot 会因为同名函数拒绝整个脚本)。
    """
    out = {}
    for item in (getattr(tree, "gk_funcs", None) or ()):
        name = str(getattr(item, "func_name", "") or "").strip()
        if not name or name in out:
            continue
        params = []
        bad_types = []
        for param_name, param_type, known in parse_function_params(
                getattr(item, "params", "")):
            params.append((param_name, param_type))
            if not known:
                bad_types.append(param_name)
        out[name] = {
            "return": FUNCTION_RETURN_TYPES.get(
                str(getattr(item, "return_type", "void") or "void"), ""),
            "params": params,
            "comment": str(getattr(item, "comment", "") or ""),
            "bad_types": bad_types,
        }
    return out


# 引用函数的两种节点(『函数_定义』接函数体 / 『函数_调用』叫它)。
FUNCTION_NODE_IDS = ("GKNodeFunctionDefine", "GKNodeFunctionCall")


def function_usages(tree):
    """图里每个函数名被引用了几次 -> {名字: {"defines": n, "calls": n}}。

    ── 为什么要有它(第八十九轮)────────────────────────────────────
    侧栏『函数』框原来写的是 `被调用 {1 if name in called else 0} 次` ——
    不管调用几次都显示 1(实测截图对不上)。次数得**真数节点**;
    顺带把"同名定义了 2 个"也数出来(导出只保留第一个,是静默丢逻辑)。

    键是**图里出现过的**名字(含没声明的)—— 调用方拿 `tree_functions`
    的名字去比对,就能发现"引用了但没声明"(改名 / 拼错的遗留)。
    """
    table = {}
    for node in getattr(tree, "nodes", ()) or ():
        kind = getattr(node, "bl_idname", "")
        if kind not in FUNCTION_NODE_IDS:
            continue
        name = str(getattr(node, "gk_func_name", "") or "").strip()
        if not name:
            continue
        entry = table.setdefault(name, {"defines": 0, "calls": 0})
        entry["defines" if kind == "GKNodeFunctionDefine" else "calls"] += 1
    return table


def rename_function_refs(tree, old, new):
    """改函数声明名时,把图里指着老名字的地方全带过去(第八十九轮)。

    带两样:
      * 『函数_定义』『函数_调用』节点上的 `gk_func_name`;
      * 变量『读→/写→』里绑的 getter/setter(它们存的是**函数名**)。

    ── 顺序要求(调用方看这里)────────────────────────────────────
    先调本函数,**再**改函数声明的名字 —— 声明一改会触发
    `_on_function_decl_changed`,那时节点名字已经是新的,引脚能按新名
    正确重长,而且走的是**按名还原连线**的 `rebuild_sockets`,已接好的
    参数实参线不会断。反过来先改声明:重长那一刻节点还指着老名字,
    参数引脚会消失,线就真断了。
    返回改了几处。名字没变 / 空名字直接返回 0。
    """
    old = str(old or "").strip()
    new = str(new or "").strip()
    if not old or not new or old == new:
        return 0
    changed = 0
    for node in getattr(tree, "nodes", ()) or ():
        if getattr(node, "bl_idname", "") not in FUNCTION_NODE_IDS:
            continue
        if str(getattr(node, "gk_func_name", "") or "").strip() != old:
            continue
        try:
            node.gk_func_name = new
            changed += 1
        except Exception:                                 # noqa: BLE001
            pass
    for var in (getattr(tree, "gk_vars", None) or ()):
        for attr in ("getter", "setter"):
            if str(getattr(var, attr, "") or "").strip() == old:
                try:
                    setattr(var, attr, new)
                    changed += 1
                except Exception:                         # noqa: BLE001
                    pass
    return changed


def make_node_picker(prop_name):
    """给『目标节点』这类属性生成动态下拉列表。

    返回一个 update 回调，在面板绘制前刷新 EnumProperty 的候选项，
    这样新增/删除节点后不需要重建属性。
    """

    def refresh(self, context):
        try:
            items = node_candidates(getattr(self, "id_data", None))
        except Exception:
            items = node_candidates(None)
        try:
            self.bl_rna.properties[prop_name].enum_items.clear()
        except Exception:
            pass

    return refresh


# ── "哪些图有 GK 菜单"的总名单(第一百二十七轮抽出常量)────────────
# 原来这段名单只写在 `current_tree_type` 里,而**加载迁移**(见
# nodes/_containers.migrate_pin_names)也要扫"所有 GK 树"。两处各写一份
# 名单的话,加新树类型时必漏一处 —— 漏在迁移侧的表现是"老文件里那类
# 树的节点引脚永远补不上"。所以收成一个常量,两边都读它。
GK_TREE_KINDS = ("GKGameLogicTree", "GKAnimationTree", "GKUiDesignTree",
                 "GKGameAiTree", "GKDisplayTree", "GKAssetTree",
                 "GKMusicTree", "GKEnvLibraryTree")


def current_tree_type(context):
    """当前节点编辑器在编辑哪一类树 —— 八种之一:

        "GKGameLogicTree"  玩法图
        "GKAnimationTree"  动画树(『游戏动画』)
        "GKUiDesignTree"   界面设计图(『游戏界面』,第四十轮)
        "GKGameAiTree"     AI 图(『游戏AI』,第四十一轮)
        "GKDisplayTree"    显示设置图(『游戏显示』,第四十二轮)
        "GKAssetTree"      资产库(『游戏资产』,第四十三轮)
        "GKMusicTree"      音乐设置图(『游戏音乐』,第八十六轮)
        "GKEnvLibraryTree" 游戏环境与后期图(第一百二十五轮)

    取不到就返回 None。菜单按这个过滤(见 node_menu_entries)。

    ── ⚠ 这张名单就是"哪些图有 GK 菜单"的总开关(第一百二十五轮踩过)─────
    `GKEnvLibraryTree` 以前**不在这张名单里** —— 于是
    `_draw_context_menu` / `_draw_add_menu` 一开头就 `return`,
    右键和 Shift+A 里**一条 GK 条目都没有**。用户原话:
    "游戏环境与后期的右键菜单那空的"。加一张树类型时,
    **这里、CATEGORY_ORDER、nodes.py 的导入** 三处都要动,少一处就"菜单是空的"。
    """
    space = getattr(context, "space_data", None)
    kind = getattr(space, "tree_type", "") if space is not None else ""
    if kind in GK_TREE_KINDS:
        return kind
    return None


def current_tree(context):
    """取当前正在编辑的 GK 节点树，没有就返回 None。

    ── 为什么两种树都认(踩过的坑)────────────────────────────────
    以前这里写死 `tree_type != "GKGameLogicTree"` 就返回 None ——
    于是打开**动画树**时所有面板都以为"没有树",
    用户看到的是空面板(实测:"游戏动画那里什么都没有")。
    现在六类树都返回,由调用方按需再用 `current_tree_type()` 区分。
    """
    kind = current_tree_type(context)
    if kind is None:
        return None
    space = getattr(context, "space_data", None)
    return getattr(space, "edit_tree", None) or getattr(space, "node_tree", None)


# ==========================================================================
# "这个节点属于哪种树" —— 只有这一份判断
# ==========================================================================
# ── 为什么必须收成一个函数(实测踩过的坑)────────────────────────────
# `gk_tree_type` 允许是**元组**(两张树里都能用的节点,比如『逻辑_布尔运算』
# 和『逻辑_比较』—— 动画树的转场条件也要拼组合判断)。
#
# 但菜单的四个地方各自写了一遍 `getattr(cls, "gk_tree_type", ...) != kind`,
# 元组和字符串比永远是 True,于是:
#   * 动画树里点『运算 ▸ 常量 ▸ 常量_数值』-> 报
#     `RuntimeError: 『常量_数值』是游戏逻辑树里的节点,不能放进当前的图`
#     (用户 2026-10-03 截图里的那条)
#   * 两级子菜单、『常用』、『最近使用』全都没做过滤
# 判断逻辑收在这里之后,调用方不可能再写错。
def node_tree_kinds(cls):
    """一个节点类允许放进哪些树,归一成元组。"""
    kinds = getattr(cls, "gk_tree_type", "GKGameLogicTree")
    if isinstance(kinds, str):
        return (kinds,)
    return tuple(kinds)


def node_fits_tree(cls, tree_type):
    """这个节点能不能放进 tree_type 那种树?

    tree_type 为 None(拿不到上下文)时一律返回 True —— 菜单宁可多列,
    也不能在上下文缺失时把整张菜单变空。
    """
    if tree_type is None:
        return True
    return tree_type in node_tree_kinds(cls)


def node_menu_allowed(cls, tree_type):
    """这个节点**菜单里**能不能列进 tree_type 那种树(第一百一十二轮)。

    ── 为什么和 `node_fits_tree`(poll)分开 ─────────────────────────
    用户要求"把在游戏逻辑的右键菜单的界面逻辑删了"(界面自己的行为搬进
    『游戏界面』)。如果直接改 `gk_tree_type` 让 poll 也拦下,会把**老脚本**
    打断 —— 示例的生成脚本还在往玩法图里 `nodes.new()` 摆这些节点
    (代码直连、不过菜单),poll 一拦,示例重建直接失败(实测踩过:
    "无法将 'GKNodeUiPauseGame' 类型的节点添加到节点树 '走廊逻辑' 中")。

    所以拆成两层,各管各的:
      * **菜单**(用户看到的,本函数):藏着不列 —— 新图按新架构走;
      * **poll**(能不能放):照旧允许 —— 老图能编辑、脚本能重建。
    和 `gk_hidden`(整张菜单都不列)是同一种"温和引导"的思路。
    """
    if getattr(cls, "gk_category", "") == "动画(旧)":
        return False
    if not node_fits_tree(cls, tree_type):
        return False
    hidden = getattr(cls, "gk_menu_hidden_trees", ())
    if isinstance(hidden, str):
        hidden = (hidden,)
    return tree_type not in hidden


def node_menu_entries(tree_type=None):
    """按分类整理所有节点，供"添加节点"菜单使用。

    tree_type 给了的话**只列出属于那种树的节点** ——
    在动画树里不该看到玩法节点,反过来也一样。
    不筛的话用户会点到一个必然报错的菜单项(实测踩过)。
    """
    grouped = {}
    for cls in node_lib.ALL_CLASSES:
        # 隐藏的节点不出现在"添加节点"菜单里,但仍然能加载/生成代码。
        # 用来做"旧版保留但不推荐"——删掉的话已有的图会打不开。
        if getattr(cls, "gk_hidden", False) or cls.gk_category == "动画(旧)":
            continue
        if tree_type is not None:
            # gk_tree_type 允许是**元组**(两张树里都能用的节点,比如
            # 逻辑_布尔运算 —— 动画转场条件也要拼组合判断)。见 node_fits_tree
            # ⚠ 菜单用 `node_menu_allowed`:它在 "能放" 之上再叠一层
            #   "菜单里不列"(第一百一十二轮,界面节点搬家用的温和引导)。
            if not node_menu_allowed(cls, tree_type):
                continue
        category = next((name for name, categories in node_lib.MENU_GROUPS.items()
                         if cls.gk_category in categories), cls.gk_category)
        grouped.setdefault(category, []).append(cls)
    ordered = []
    for category in tuple(node_lib.MENU_GROUPS) + node_lib.CATEGORY_ORDER:
        if category in grouped:
            ordered.append((category, grouped.pop(category)))
    for category, items in grouped.items():
        ordered.append((category, items))
    return ordered


def node_menu_tree(tree_type=None):
    """两级菜单结构: 分类 -> None(直接列) 或 分类 -> [(子分类, [节点]), ...]。

    87 个节点只按一级分类铺开时，最大的「运算」有 13 项，翻起来很累。
    这里对**大类**再切一层，菜单变成:
        运算 ▸ 常量 / 数学 / 向量 / 逻辑 / 转换
    小分类保持一级 —— 本来就三五个节点，再套一层是让用户多点一次。

    tree_type 透传给 node_menu_entries,用来按节点树类型过滤。
    """
    result = []
    for category, classes in node_menu_entries(tree_type):
        source_categories = node_lib.MENU_GROUPS.get(category, (category,))
        merged = len(source_categories) > 1 and category != "输入"
        order = (source_categories if merged else
                 node_lib.SUBCATEGORY_ORDER.get(source_categories[0]))
        if not order:
            result.append((category, None))
            continue
        buckets = {}
        for cls in classes:
            sub = (cls.gk_category if merged else
                   node_lib.SUBCATEGORY_OF.get(cls.bl_idname, "其它"))
            buckets.setdefault(sub, []).append(cls)
        subs = []
        for sub in order:
            if sub in buckets:
                subs.append((sub, buckets.pop(sub)))
        for sub, items in buckets.items():     # 没登记进顺序表的兜底
            subs.append((sub, items))
        result.append((category, subs))
    return result


def common_node_classes():
    """「常用」短名单里的节点类，顺序按 COMMON_NODES 走。"""
    by_id = {cls.bl_idname: cls for cls in node_lib.ALL_CLASSES}
    out = []
    for bl_idname in node_lib.COMMON_NODES:
        cls = by_id.get(bl_idname)
        if cls is not None:
            out.append(cls)
    return out


def node_count(tree):
    if tree is None:
        return 0
    return len(tree.nodes)


def event_summary(tree):
    """返回 (事件数量, 动作数量)，用于面板上的健康检查。"""
    if tree is None:
        return 0, 0
    events = 0
    statements = 0
    for node in tree.nodes:
        if getattr(node, "gk_is_event", False) and not getattr(node, "gk_is_comment", False):
            events += 1
        elif not getattr(node, "gk_data_only", False):
            statements += 1
    return events, statements


# ==========================================================================
# 变量集中视图(用户 2026-10-04)
# ==========================================================================
# 玩法图里的变量是**隐式声明**的 —— 第一次用『变量_设置』写它就自动成为
# 成员变量(见 codegen.context.declare_var),没有一个"变量表"能看。
# 于是用户问:"图里到底有哪些变量?谁在读、谁在写?"只能一个个节点翻。
# 这两个纯函数把**所有变量节点的引用**汇总出来,给侧栏面板用。
#
# 判"读/写"靠节点的 bl_idname:写节点的类型是固定的,其余当读。
# ── 『变量_增减』也是"写"(第三十三轮整理示例时补的)───────────────────
# 它以前被当成"读",于是小游戏示例的『血量』在引用区显示成"设 0 · 读 4" ——
# 明明是四个『变量_增减』在扣血,面板却说没人写过它。用户看到的是
# "这个变量没人写,怎么还会变?",而答案是面板数错了。
_VAR_WRITER_NODES = {"GKNodeSetVar", "GKNodeAnimSetVar", "GKNodeAddVar"}

# 写变量、但属性名不叫 `gk_var_name` 的节点:{bl_idname: (属性名, 角色)}。
# 『存档_读取到变量』把存档里的值读进 `gk_var` 这个变量 —— 它是**写方**
# (同『变量_设置』),不扫的话它写的变量在引用区里根本不存在。
_VAR_OTHER_PROPS = {"GKNodeSaveGet": (("gk_var", "set"),)}


def _var_name_props(node):
    """一个节点上**哪些属性存着变量名** -> [(属性名, 角色)],角色 'set'/'get'。

    这是"变量名存在哪"的**唯一一份表**:引用汇总(_node_var_names)和
    改名写回(rename_variable_refs)都走它 —— 两处各写一份的话,
    改名时漏掉一个槽位就是"改了名字,那根线悄悄断了"(第八十九轮)。
    """
    kind = getattr(node, "bl_idname", "")
    role = "set" if kind in _VAR_WRITER_NODES else "get"
    props = [("gk_var_name", role)]
    if kind == "GKNodeSetVar":
        props += [(f"gk_var_name_{slot}", "set") for slot in range(2, 7)]
    props += list(_VAR_OTHER_PROPS.get(kind, ()))
    return props


def _node_var_names(node):
    """一个节点引用的变量 -> [(名字, 角色)],角色是 'set' 或 'get'。

    一张『变量_设置』卡片能写多个变量(gk_var_name / gk_var_name_2 …),
    所以要把所有槽位都扫一遍。
    """
    out = []
    for prop, role in _var_name_props(node):
        name = str(getattr(node, prop, "") or "").strip()
        if name:
            out.append((name, role))
    return out


def variable_references(tree):
    """汇总一张图里所有变量的引用:返回 [(名字, 写它的节点, 读它的节点), ...]。

    按变量名排序 —— 面板上一行一个变量,点一下就能选中所有引用它的节点
    (虚幻蓝图 Find References 的那一步)。
    """
    table = {}
    for node in getattr(tree, "nodes", ()) or ():
        for name, role in _node_var_names(node):
            entry = table.setdefault(name, {"set": [], "get": []})
            # 同一节点在两个槽位填了同名变量时只算一次(用身份比较,
            # Blender 的节点包装对象不能拿 `in` / `==` 当可靠判断)
            if not any(existing is node for existing in entry[role]):
                entry[role].append(node)
    return [(name, table[name]["set"], table[name]["get"])
            for name in sorted(table)]


def nodes_for_variable(tree, var_name):
    """图里所有读到/写到 var_name 的节点(按节点顺序去重)。"""
    want = str(var_name or "").strip()
    if not want:
        return []
    hits = []
    for node in getattr(tree, "nodes", ()) or ():
        if any(name == want for name, _role in _node_var_names(node)):
            hits.append(node)
    return hits


def rename_variable_refs(tree, old, new):
    """把图里引用 old 变量的**节点槽位**全改成 new(第八十九轮)。

    声明区改个名字,图里的『变量_读取/设置』还指着老名字 —— 那根引用
    就成了"未声明"(导出时那几处读写会各建一个变量,行为悄悄变了)。
    改名按钮走这里,连带改完,返回改了几处。
    """
    old = str(old or "").strip()
    new = str(new or "").strip()
    if not old or not new or old == new:
        return 0
    changed = 0
    for node in getattr(tree, "nodes", ()) or ():
        for prop, _role in _var_name_props(node):
            if str(getattr(node, prop, "") or "").strip() == old:
                try:
                    setattr(node, prop, new)
                    changed += 1
                except Exception:                         # noqa: BLE001
                    pass
    return changed


# 『引用区』里给"名字和对外端口同名"的那批引用用的固定组名(第三十三轮)。
# 端口本质就是脚本变量(`输出_<名字>`,见 codegen._emit_logic_ports),
# 图里用『变量_读取/设置』填端口名是**正常接法** —— 说它"未声明"是错的。
PORT_REF_GROUP = "对外端口"


def reference_groups(tree):
    """『引用区』按分组排好的行:[(组名, [(名字, 写它的节点, 读它的节点), …]), …]。

    ── 排布规矩(第三十三轮整理现有示例时定的)────────────────────────
      * 用户给变量填的分组 —— 按**声明区那套顺序**(逻辑图里摆放的顺序),
        没出现在声明区的组按第一次引用出现;
      * 『对外端口』—— 引用的名字和声明过的端口同名(它们不是"未声明");
        固定排在用户分组**之后**、未分组之前;
      * 未分组(空串)排最后。

    纯函数:UI 只负责照这个顺序画折叠盒子(离线测试直接调它)。
    """
    usages = variable_references(tree)
    group_of = logic_var_group_of(tree)
    ports = {str(getattr(port, "port_name", "") or "").strip()
             for port in (getattr(tree, "gk_ports", None) or ())}
    buckets = {}
    for name, sets, gets in usages:
        group = str(group_of.get(name, "") or "").strip()
        if not group and name in ports:
            group = PORT_REF_GROUP
        buckets.setdefault(group, []).append((name, sets, gets))
    # 组名顺序:先按声明区(用户摆的顺序),再补上只在此处出现的组
    ordered_names = [name for name, _ in logic_var_groups(tree) if name]
    ordered_names += [name for name in buckets
                      if name and name not in ordered_names
                      and name != PORT_REF_GROUP]
    ordered = [(name, buckets[name]) for name in ordered_names
               if name in buckets and name != PORT_REF_GROUP]
    if PORT_REF_GROUP in buckets:
        ordered.append((PORT_REF_GROUP, buckets[PORT_REF_GROUP]))
    if "" in buckets:
        ordered.append(("", buckets[""]))
    return ordered


def default_output_dir():
    """已废弃的兜底目录。

    保留只是为了不破坏外部引用。**新代码不要用** ——
    导出目录应该由 project.resolve() 根据 .blend 位置推导,
    静默写到家目录会让用户找不到文件。
    """
    import os

    from . import project

    derived = project.derive_project_dir()
    if derived:
        return derived
    return os.path.join(os.path.expanduser("~"), "BlenderGameKit_Export")


# 注册顺序很关键:PointerProperty 引用 PropertyGroup 时，PropertyGroup
# 必须已经注册，否则挂到 Scene / Object 上会报 "missing bl_rna attribute"。
class GKEnvLibraryTree(bpy.types.NodeTree):
    """游戏环境与后期 —— 文件级(跨场景)共享的环境/后期图(第一百一十五轮)。

    预设原来挂在**每个场景**的 gk_settings.env_presets 上,多关卡时每关
    一份、互不相通(关卡1 存的"恐怖夜"在关卡2 里看不到)。现在统一收到
    这张**文件级**的树里 —— 和"动画变量挂动画树"同一个道理:NodeTree 是
    文件里唯一的"跨场景持久数据块"(bpy.data.node_groups)。

    ── 第 124 轮:从"数据容器"升级成一张**能连线的图**(用户要求)──────
    用户原话:"环境预设库(请改名字为游戏环境与后期)添加设置色调,连线-
    屏幕位置(整体?还是局部),添加睁眼效果-连线(持续时间),逻辑节点
    (是否,变量)...这个的开始卡片是默认环境卡片连过去"。

    所以这张树现在有两个身份:
      * `env_presets` 那一列仍然是**命名预设的存放处**(跨场景共享);
      * 它同时是一张**可编辑的节点图**,入口是『环境_默认环境』卡,
        后面按执行流接『环境_设置色调』『环境_屏幕位置』『环境_睁眼效果』
        以及变量/是否这些逻辑节点 —— 和游戏逻辑图同一套连线习惯。

    名字改成『游戏环境与后期』是因为它的职责已经不只是"存预设"了:
    它是"这一局的环境 + 后期"的总入口。
    """

    bl_idname = "GKEnvLibraryTree"
    bl_label = "游戏环境与后期"
    bl_icon = "BOOKMARKS"
    bl_description = ("BlenderGameKit 的游戏环境与后期 —— 跨场景共享的命名预设"
                      "+ 可连线的环境/后期图(入口是『默认环境』卡)")

    env_presets: CollectionProperty(type=GKEnvPreset)
    env_preset_pick: EnumProperty(
        name="预设", items=_env_preset_items,
        description="选一个预设(应用后才写进上面的设置)")
    env_preset_new_name: StringProperty(
        name="存为预设", default="我的氛围",
        description="把当前『环境与后处理』的一套值存成这个名字的预设")


def env_preset_library():
    """当前文件的『游戏环境与后期』图(没有就建一张);跨场景共享。

    ── 为什么用一张树(而不是继续挂场景)────────────────────────────
    预设是"项目的风格资产",本该多关卡共用。NodeTree 是文件里唯一的
    跨场景持久数据块,和动画变量 / 主界面开关同一个存法。fake_user
    保证没人引用它时也不会被 Blender 存盘时丢掉(和 AI 图同一个坑)。
    """
    for group in bpy.data.node_groups:
        if getattr(group, "bl_idname", "") == "GKEnvLibraryTree":
            return group
    tree = bpy.data.node_groups.new("游戏环境与后期", "GKEnvLibraryTree")
    tree.use_fake_user = True
    return tree


#
# 注意:这几个 PropertyGroup 只在这里**注册类**;真正挂到
# Scene.gk_settings / Object.gk_physics 上的动作在
# __init__.register() 里做 —— 那边能保证"所有类都注册完了"再挂。
GK_CLASSES = (GKEnvPreset, GKEnvLibraryTree, GKSceneSettings, GKLogicBinding,
              GKObjectSettings, GKObjectFx, GKObjectLight, GKAnimVariable,
              GKLogicPort, GKLogicVariable, GKLogicFunction)
