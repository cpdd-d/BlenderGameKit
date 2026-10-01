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
        description="覆盖自动计算的尺寸(X=半径/半宽, Y=高度, Z=半径/半深)",
        size=3,
        default=(0.5, 1.0, 0.5),
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


def current_tree(context):
    """取当前正在编辑的 GK 节点树，没有就返回 None。"""
    space = getattr(context, "space_data", None)
    if space is None or getattr(space, "tree_type", "") != "GKGameLogicTree":
        return None
    return getattr(space, "edit_tree", None) or getattr(space, "node_tree", None)


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
GK_CLASSES = (GKSceneSettings, GKObjectSettings)
