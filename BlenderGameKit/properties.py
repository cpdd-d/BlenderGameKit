"""Blender 侧属性(场景级设置)与常用小工具。"""

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    PointerProperty,
    StringProperty,
)

from . import codegen
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
        if physics is None or not getattr(physics, "enabled", True):
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
    is_trigger: BoolProperty(
        name="作为触发区",
        description="只检测重叠、不阻挡(碰到就知道,但穿得过去)",
        default=False,
    )
    script_target: BoolProperty(
        name="挂游戏逻辑脚本",
        description="把节点图导出的脚本挂到这个物体上",
        default=False,
    )


def physics_of(obj):
    """安全地取物体上的物理设置(没有就返回 None)。"""
    return getattr(obj, "gk_physics", None)


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
    show_log: BoolProperty(
        name="显示日志",
        description="在侧栏显示最近一次导出的日志",
        default=True,
    )
    # ── 主相机(用户第 25 轮)────────────────────────────────────────
    # 整局游戏**默认用哪台相机**。留空 = 自动:
    #   1) 用 Blender 视口当前那台(scene.camera —— 就是你按小键盘 0 看的那台)
    #   2) 没有就取场景里第一台 Camera
    #   3) 一台都没有 -> 运行时造一台自由飞行观察机(见 codegen)
    # 场景里有多台相机却没明确选时,导出会提示一句"建议指定主相机"。
    main_camera: StringProperty(
        name="主相机",
        description="游戏默认用哪台相机;留空 = 跟着 Blender 视口的当前相机",
        default="",
    )

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


def current_tree_type(context):
    """当前节点编辑器在编辑哪一类树 —— "GKGameLogicTree" / "GKAnimationTree"。

    取不到就返回 None。菜单按这个过滤(见 node_menu_entries)。
    """
    space = getattr(context, "space_data", None)
    kind = getattr(space, "tree_type", "") if space is not None else ""
    if kind in ("GKGameLogicTree", "GKAnimationTree"):
        return kind
    return None


def current_tree(context):
    """取当前正在编辑的 GK 节点树，没有就返回 None。

    ── 为什么两种树都认(踩过的坑)────────────────────────────────
    以前这里写死 `tree_type != "GKGameLogicTree"` 就返回 None ——
    于是打开**动画树**时所有面板都以为"没有树",
    用户看到的是空面板(实测:"游戏动画那里什么都没有")。
    现在两类树都返回,由调用方按需再用 `current_tree_type()` 区分。
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


def node_menu_entries():
    """按分类整理所有节点，供"添加节点"菜单使用。"""
    grouped = {}
    for cls in node_lib.ALL_CLASSES:
        grouped.setdefault(cls.gk_category, []).append(cls)
    ordered = []
    for category in node_lib.CATEGORY_ORDER:
        if category in grouped:
            ordered.append((category, grouped.pop(category)))
    for category, items in grouped.items():
        ordered.append((category, items))
    return ordered


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
        if getattr(cls, "gk_hidden", False):
            continue
        if tree_type is not None:
            # gk_tree_type 允许是**元组**(两张树里都能用的节点,比如
            # 逻辑_布尔运算 —— 动画转场条件也要拼组合判断)。见 node_fits_tree
            if not node_fits_tree(cls, tree_type):
                continue
        grouped.setdefault(cls.gk_category, []).append(cls)
    ordered = []
    for category in node_lib.CATEGORY_ORDER:
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
        order = node_lib.SUBCATEGORY_ORDER.get(category)
        if not order:
            result.append((category, None))
            continue
        buckets = {}
        for cls in classes:
            sub = node_lib.SUBCATEGORY_OF.get(cls.bl_idname, "其它")
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
#
# 注意:这几个 PropertyGroup 只在这里**注册类**;真正挂到
# Scene.gk_settings / Object.gk_physics 上的动作在
# __init__.register() 里做 —— 那边能保证"所有类都注册完了"再挂。
GK_CLASSES = (GKSceneSettings, GKObjectSettings, GKAnimVariable,
              GKLogicPort)
