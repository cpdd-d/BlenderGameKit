"""Blender 侧属性(场景级设置)与常用小工具。"""

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
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

# 色调映射模式。值 = Godot 的 Environment.tonemap_mode 枚举。
ENV_TONEMAPS = (
    ("LINEAR", "线性", "不做色调映射(老项目默认,颜色最直白)"),
    ("REINHARD", "Reinhard", "柔和压高光,画面不那么容易过曝"),
    ("FILMIC", "Filmic", "电影感,暗部更沉"),
    ("ACES", "ACES", "高动态范围,亮部过渡最自然"),
)


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
    show_log: BoolProperty(
        name="显示日志",
        description="在侧栏显示最近一次导出的日志",
        default=True,
    )
    # ── 上帝摄像机(用户 2026-10-04)────────────────────────────────
    # 原来这里是一个"主相机"下拉 + 拾取按钮。用户的原话:
    # "把现在的那个侧边栏的主摄像机改成其他要用的或者删了"。
    # 选定的方案:改成**上帝摄像机开关** —— 常驻一台自由飞行相机,
    # 运行时按 Tab 在"玩家相机 / 上帝摄像机"之间来回切。
    # 这台相机**独立于玩家相机**,不走物理(WASD 平移 / 空格上升 /
    # Shift 下降 / 鼠标转视角,可穿墙),用来像虚幻 5 的上帝视角那样
    # 满地跑着看这一关做得对不对。
    god_camera: BoolProperty(
        name="上帝摄像机", default=False,
        description=("常驻一台自由飞行相机(独立于玩家相机)。"
                     "运行时按 Tab 切换;WASD 平移 / 空格上升 / Shift 下降 / "
                     "鼠标转视角,不受碰撞"))
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
        description="卡通材质是否跟着场景里的平行光画明暗(关掉 = 全平涂)")

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
    env_glow: BoolProperty(
        name="泛光", default=False,
        description="亮的地方往外溢光 —— 灯 / 手电筒 / 火光'毛'起来")
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
    """
    tree = getattr(self, "id_data", None)
    for node in getattr(tree, "nodes", ()) or ():
        if getattr(node, "bl_idname", "") not in ("GKNodeFunctionCall",
                                                  "GKNodeFunctionDefine"):
            continue
        builder = getattr(node, "build_sockets", None)
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
    """当前节点编辑器在编辑哪一类树 —— 六种之一:

        "GKGameLogicTree"  玩法图
        "GKAnimationTree"  动画树(『游戏动画』)
        "GKUiDesignTree"   界面设计图(『游戏界面』,第四十轮)
        "GKGameAiTree"     AI 图(『游戏AI』,第四十一轮)
        "GKDisplayTree"    显示设置图(『游戏显示』,第四十二轮)
        "GKAssetTree"      资产库(『游戏资产』,第四十三轮)

    取不到就返回 None。菜单按这个过滤(见 node_menu_entries)。
    """
    space = getattr(context, "space_data", None)
    kind = getattr(space, "tree_type", "") if space is not None else ""
    if kind in ("GKGameLogicTree", "GKAnimationTree", "GKUiDesignTree",
                "GKGameAiTree", "GKDisplayTree", "GKAssetTree"):
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


def _node_var_names(node):
    """一个节点引用的变量 -> [(名字, 角色)],角色是 'set' 或 'get'。

    一张『变量_设置』卡片能写多个变量(gk_var_name / gk_var_name_2 …),
    所以要把所有槽位都扫一遍。
    """
    kind = getattr(node, "bl_idname", "")
    role = "set" if kind in _VAR_WRITER_NODES else "get"
    out = []
    name = str(getattr(node, "gk_var_name", "") or "").strip()
    if name:
        out.append((name, role))
    if kind == "GKNodeSetVar":
        for slot in range(2, 7):     # SET_VAR_SLOTS
            extra = str(getattr(node, f"gk_var_name_{slot}", "") or "").strip()
            if extra:
                out.append((extra, "set"))
    for prop, prop_role in _VAR_OTHER_PROPS.get(kind, ()):
        extra = str(getattr(node, prop, "") or "").strip()
        if extra:
            out.append((extra, prop_role))
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
#
# 注意:这几个 PropertyGroup 只在这里**注册类**;真正挂到
# Scene.gk_settings / Object.gk_physics 上的动作在
# __init__.register() 里做 —— 那边能保证"所有类都注册完了"再挂。
GK_CLASSES = (GKSceneSettings, GKObjectSettings, GKObjectFx, GKAnimVariable,
              GKLogicPort, GKLogicVariable, GKLogicFunction)
