"""Blender 端 NPR 材质预览:在 EEVEE 里调卡通参数,不用切到 Godot。

为什么是"内联节点"而不是"节点组"
----------------------------------
最初做的是节点组(GK_NPR_Toon),参数挂在组接口上看起来更干净。
但实测撞了三个 EEVEE 的隐式行为坑,而且每一个都只在**节点组内部**出现:

  1. `ShaderNodeTexImage` 在**没有指定图像**时输出透明黑,会把整条链乘成 0。
     节点组里为了做"可选贴图"必然留几个空贴图节点 —— 结果渲染全黑。
  2. `UVMap` 节点的 UV 输入如果没连线,取值是 (0,0),贴图取样退化成一点。
  3. ColorRamp 的 Fac 如果直接接颜色(而不是先经 RGBToBW 转亮度),
     在亮场景下会整体饱和成全黑或全白。

改成**节点直接内联在材质里**之后,这三个问题都不存在:每个材质都是为具体
模型生成的,空贴图节点根本不会创建。

参数存在**材质的自定义属性**上(id_properties),而不是节点接口上。
这样导出时直接遍历 material.items() 就能拿到,也不需要维护两套读写逻辑。

技术关键
--------
卡通着色必须把连续光照"截断"成色阶:

    Diffuse BSDF -> Shader to RGB -> RGBToBW -> ColorRamp(CONSTANT)

两个必须注意的点:
  * **ColorRamp 的 interpolation 必须是 CONSTANT**。
  * **view transform 必须是 Standard**。默认的 AgX/Filmic 会把离散色阶
    重新平滑成连续渐变,看起来像"色阶没生效"。
    实测:无色阶时球心 37~43 种颜色(连续渐变),有色阶时主色一种就占绝大多数。

已知限制(界面上要说清楚,不能让人以为预览=最终效果)
----------------------------------------------------
  * **面部阴影 SDF 无法预览**:它依赖视图空间的光照方向,Blender 节点里
    没有等效量。请在 Godot 里用 scripts/render_shots.ps1 验证。
  * **描边无法预览**:那是 inverted hull 几何操作。可以自己加 Solidify
    修改器近似,本模块不代劳。
"""

import os

import bpy

GROUP_NAME = "GK_NPR_Toon"

# (属性名, Godot uniform 名, 类型, 默认值, 显示名)
PARAM_MAP = (
    ("albedo_color", "albedo_color", "COLOR", (1.0, 0.932, 0.912), "基础色"),
    ("albedo_strength", "albedo_strength", "VALUE", 1.0, "基础色强度"),
    ("shadow_step", "shadow_step", "VALUE", 0.1, "明暗分界"),
    ("shadow_step_2", "shadow_step_2", "VALUE", -0.45, "第二分界"),
    ("three_tone", "three_tone", "VALUE", 0.0, "三段明暗"),
    ("shadow_smooth", "shadow_smooth", "VALUE", 0.02, "分界软硬"),
    ("lit_tint", "lit_tint", "COLOR", (1.0, 0.976, 0.914), "受光颜色"),
    ("shadow_tint", "shadow_tint", "COLOR", (0.706, 0.627, 0.902), "阴影颜色"),
    ("shadow_tint_2", "shadow_tint_2", "COLOR", (0.478, 0.420, 0.722), "深阴影颜色"),
    ("shadow_strength", "shadow_strength", "VALUE", 1.0, "阴影强度"),
    ("specular_amount", "specular_amount", "VALUE", 0.25, "高光强度"),
    # 第一百零一轮:高光大小 / 边缘软硬 / 颜色本来只在着色器里有默认值,
    # **从来没被导出过**(Blender 侧根本没有这三个参数)—— 这一轮补齐。
    ("specular_size", "specular_size", "VALUE", 0.75, "高光大小"),
    ("specular_smooth", "specular_smooth", "VALUE", 0.05, "高光边缘软硬"),
    ("specular_color", "specular_color", "COLOR", (1.0, 1.0, 1.0), "高光颜色"),
    ("rim_amount", "rim_amount", "VALUE", 0.0, "边缘光强度"),
    ("rim_color", "rim_color", "COLOR", (0.75, 0.85, 1.0), "边缘光颜色"),
    ("rim_width", "rim_width", "VALUE", 0.55, "边缘光宽度"),
    ("emission_amount", "emission_amount", "VALUE", 0.0, "自发光强度"),
    ("alpha_cut", "alpha_cut", "VALUE", 0.5, "透明裁剪"),
    ("light_map_mix", "light_map_mix", "VALUE", 0.0, "光照贴图混合"),
    # ---- 面部阴影 SDF ----
    # 参数**直接对应 Godot 的同名 uniform**,但语义上有一处刻意的差异:
    #   Godot 的 face_sdf_angle_scale 是"光照方向与相机夹角"的放大倍率;
    #   Blender 里的 face_sdf_angle 则直接就是**夹角归一化后的系数**:
    #       0    = 光从正前方(阴影线在贴图中线)
    #       1    = 光在正侧面(阴影线滑到边缘)
    #       -1   = 反方向(滑到另一边)
    #
    # 为什么不照搬 Godot 的算法:Godot 用视图空间叉积
    # `cross(view_dir, light_dir).y` 推水平夹角,而 Blender 节点里
    # 拿不到"视图空间的光照方向"。直接给系数省掉这一串,而且
    # **固定光照方向时两边结果一致** —— 预览的目的不就是这个吗。
    # 想省事就用 update_face_sdf_from_scene(),它能从场景里的太阳光反推系数。
    ("face_sdf_mix", "face_sdf_mix", "VALUE", 0.0, "面部 SDF 混合"),
    ("face_sdf_angle", "face_sdf_angle", "VALUE", 0.0, "面部光照系数"),
    ("face_sdf_neutral", "face_sdf_neutral", "VALUE", 0.5, "面部 SDF 中线"),
    ("face_sdf_angle_scale", "face_sdf_angle_scale", "VALUE", 1.6, "面部角度倍率"),
    ("face_sdf_smooth", "face_sdf_smooth", "VALUE", 0.03, "面部边界软硬"),
    ("face_sdf_split", "face_sdf_split", "VALUE", 0.5, "面部左右分界"),
)

PARAM_LIMITS = {
    "albedo_strength": (0.0, 2.0),
    "shadow_step": (-1.0, 1.0),
    "shadow_step_2": (-1.0, 1.0),
    "three_tone": (0.0, 1.0),
    "shadow_smooth": (0.001, 0.6),
    "shadow_strength": (0.0, 4.0),
    "specular_amount": (0.0, 4.0),
    "specular_size": (0.0, 1.0),
    "specular_smooth": (0.005, 0.5),
    "rim_amount": (0.0, 4.0),
    "rim_width": (0.0, 1.0),
    "emission_amount": (0.0, 8.0),
    "alpha_cut": (0.0, 1.0),
    "light_map_mix": (0.0, 1.0),
    "face_sdf_mix": (0.0, 1.0),
    "face_sdf_angle": (-1.0, 1.0),
    "face_sdf_neutral": (0.0, 1.0),
    "face_sdf_angle_scale": (0.25, 4.0),
    "face_sdf_smooth": (0.001, 0.5),
    "face_sdf_split": (0.05, 0.95),
}

# 不需要导出到 Godot 的参数(纯粹是 Blender 侧的工具)
LOCAL_ONLY_PARAMS = ("face_sdf_auto",)

DEFAULT_TUNING = {name: default for name, _u, _k, default, _l in PARAM_MAP}


# ==========================================================================
# 高光贴图(第一百零一轮):『高光形状贴图』+『高光遮罩贴图』
# ==========================================================================
# uniform 名 -> (材质上的键名, 面板上的说明)
#
# 值存的是 **Blender 图片名**(不是路径、不是 res://)——
# 导出时由 asset_export 落盘成 art/ui_xxx.png 再写进 .tres,
# 用的命名函数和界面图片是同一个(curve_assets.image_file_name),
# 同一张图在界面和材质里都被引用时只会落一份。
#
# `use_<uniform>` 开关**不存**在材质上 —— 它由"有没有填图"算出来,
# 免得出现"开关是 1 但图被删了"这种自相矛盾的状态。
TEXTURE_KEYS = {
    "specular_shape_texture": (
        "高光形状贴图",
        "横向渐变:0(左)=高光外缘,1(右)=正中心。"
        "右半边压黑 => 高光变空心环;整条压暗 => 高光变柔和。"
        "⚠ 一旦用了它,『高光边缘软硬』就不起作用了(软硬由渐变决定)"),
    "specular_mask_texture": (
        "高光遮罩贴图",
        "按 UV 决定高光长在哪儿:白=留,黑=去掉(数据图,不是颜色图)"),
}


def texture_key(uniform):
    """uniform 名 -> 材质上那个自定义属性的键名(中文,面板里一眼看得懂)。"""
    return TEXTURE_KEYS[uniform][0]


def texture_hint(uniform):
    return TEXTURE_KEYS[uniform][1]


def get_texture(material, uniform):
    """读材质上填的那张图片名(没填返回 "")。"""
    if material is None:
        return ""
    raw = material.get(texture_key(uniform))
    if raw is None:
        return ""
    return str(raw).strip()


def set_texture(material, uniform, image_name):
    """填/清掉一张高光贴图。image_name 为空 = 清掉。"""
    if material is None:
        return
    name = str(image_name or "").strip()
    if name:
        material[texture_key(uniform)] = name
    elif material.get(texture_key(uniform)) is not None:
        del material[texture_key(uniform)]


def texture_image(material, uniform):
    """读材质上那张图对应的 `bpy.data.images` 数据块(拿不到返回 None)。"""
    name = get_texture(material, uniform)
    if not name:
        return None
    return bpy.data.images.get(name)


class NodeBuilder:
    """把节点/连线的样板代码收在一起,并按名字说明或序号取 socket。

    Blender 4.x 的 ShaderNodeMix 输入名不直观(界面叫 A/B,代码里是
    A_Color/B_Color),所以两种方式都试,取不到就报清楚的错误,
    而不是静默接错线。
    """

    def __init__(self, tree):
        self.tree = tree

    def node(self, kind, name=None, location=(0, 0), **props):
        created = self.tree.nodes.new(kind)
        if name:
            created.name = name
            created.label = name
        created.location = location
        for key, value in props.items():
            setattr(created, key, value)
        # ShaderNodeMath 的 use_clamp **默认是 True**,会把结果钳制到 0..1。
        # 这是个很隐蔽的坑:数学节点里出现负数中间值是常态
        # (比如求 delta = target - neutral 必然是负的),
        # 一旦被静默钳制成 0,整条链路就算错了 —— 而且不报任何错。
        # 实测:delta 本该是 -0.5,被钳成 0 之后阴影线位置永远不动。
        if kind == "ShaderNodeMath" and not props.get("use_clamp", False):
            created.use_clamp = False
        return created

    @staticmethod
    def out(node, name):
        socket = node.outputs.get(name)
        if socket is None:
            raise KeyError(f"{node.bl_idname} 没有输出 {name}，可用 "
                           f"{[s.name for s in node.outputs]}")
        return socket

    @staticmethod
    def inp(node, name, index=None):
        """按名字取输入 socket,若给了 index 则**优先**用序号。

        这里有个必须写清楚的语义:
        ShaderNodeMath 有**三个都叫 "Value" 的输入**。如果名字优先,
        那么 `inp(m, "Value", 0)` 和 `inp(m, "Value", 1)` 会返回**同一个**
        socket —— 两条 link 都接到 0 号,后一条静默覆盖前一条,
        结果是某个输入永远没接上(而且不报错)。
        实测症状:`拆UV.X` 明明算得对,却被丢弃,贴图采样恒为常数。

        所以:**给了 index 就用 index**;只有没给 index 时才按名字找。
        """
        if index is not None and 0 <= index < len(node.inputs):
            return node.inputs[index]
        socket = node.inputs.get(name)
        if socket is None:
            raise KeyError(f"{node.bl_idname} 没有输入 {name}，可用 "
                           f"{[(s.name, s.type) for s in node.inputs]}")
        return socket

    def link(self, from_node, from_socket, to_node, to_socket):
        self.tree.links.new(self.out(from_node, from_socket),
                            self.inp(to_node, to_socket))

    def mix_rgb(self, name, location, blend="MULTIPLY", factor=1.0):
        node = self.node("ShaderNodeMix", name, location)
        try:
            node.data_type = "RGBA"
            node.blend_type = blend
        except Exception:
            pass
        node.inputs[0].default_value = factor
        return node


def to_pos(step):
    """Godot 的光照阈值(-1..1)-> ColorRamp 的位置(0..1)。"""
    return max(0.0, min(1.0, (float(step) + 1.0) * 0.5))


def srgb_to_linear(channel):
    """sRGB 分量 -> 线性。

    Godot 的 `Color(r,g,b)` 字面量是 **sRGB** 显示值,而 Blender 的
    ShaderNodeRGB / ColorRamp 色标预期 **线性** 值。不转换的话,
    预览里的阴影色会比 Godot 里明显偏亮(比如 0.706 直接当线性用,
    实际显示约 0.86),两边调出来的颜色对不上。
    """
    value = max(0.0, min(1.0, float(channel)))
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def linear_to_srgb(channel):
    """线性 -> sRGB(反向转换,用于从预览取色写回 Godot)。"""
    value = max(0.0, min(1.0, float(channel)))
    if value <= 0.0031308:
        return value * 12.92
    return 1.055 * (value ** (1.0 / 2.4)) - 0.055


def to_linear_rgb(value):
    """把 Godot 那边的 sRGB 颜色三元组转成 Blender 用的线性值。"""
    r, g, b = _rgb(value)
    return (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b))


def from_linear_rgb(value):
    """把 Blender 的线性颜色转回 Godot 那边的 sRGB 值。"""
    r, g, b = _rgb(value)
    return (linear_to_srgb(r), linear_to_srgb(g), linear_to_srgb(b))


def _rgb(value):
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        return (float(value[0]), float(value[1]), float(value[2]))
    return (1.0, 1.0, 1.0)


def _configure_ramp(ramp_node, params, soft=False):
    """把参数写进 ColorRamp。

    色标顺序对应 Godot 的三段:深阴影 / 阴影 / 受光。
    three_tone 关闭时把中间色标压到与深阴影同一位置,等于只有两段。
    """
    ramp = ramp_node.color_ramp
    ramp.interpolation = "LINEAR" if soft else "CONSTANT"
    while len(ramp.elements) > 2:
        ramp.elements.remove(ramp.elements[-1])

    pos_a = to_pos(params.get("shadow_step", 0.1))
    pos_b = to_pos(params.get("shadow_step_2", -0.45))
    three = float(params.get("three_tone", 0.0)) > 0.5

    ramp.elements[0].position = min(pos_b, pos_a - 0.01)
    ramp.elements[0].color = to_linear_rgb(
        params.get("shadow_tint_2", (0.478, 0.420, 0.722))) + (1.0,)
    ramp.elements[1].position = pos_a
    ramp.elements[1].color = to_linear_rgb(
        params.get("lit_tint", (1.0, 0.976, 0.914))) + (1.0,)
    mid = ramp.elements.new((pos_a + pos_b) * 0.5)
    mid.color = to_linear_rgb(
        params.get("shadow_tint", (0.706, 0.627, 0.902))) + (1.0,)
    if not three:
        mid.position = ramp.elements[0].position


def build_face_sdf_nodes(b, tree, values, uv_out, shaded):
    """搭出面部阴影 SDF 的节点链,返回新的着色结果。

    这是"在 Blender 里预览面部 SDF"的核心。做四件事:
        1. 由角度算出阴影线在条带里的 U 坐标
        2. 用 UV 的 V 组合成采样坐标,采样式贴图
        3. 重切成硬边
        4. 与几何光照结果按 face_sdf_mix 混合

    与 Godot 版本的**唯一差异**:那边用视图空间叉积算夹角,这边直接给系数
    (0 = 光在正前方,+1 = 光在画面右侧,-1 = 左侧)。
    固定光照方向时两者给出同样的阴影线位置。

    实现上刻意**只用 Math / Combine / Separate 这类行为确定的节点**:
    最初用 Mix(FLOAT) + 多层 Map Range,结果采样恒为黑、角度完全不起作用,
    而查不出是哪一步的隐式类型转换导致。换成显式数学后每一步都能单独验证。

    贴图布局(与 Godot 相同的约定):
        左半 [0, split] 光从画面左侧来 / 右半 [split, 1] 镜像
        白 = 受光, 黑 = 阴影
    """
    mix_amount = float(values.get("face_sdf_mix", 0.0))
    if mix_amount <= 0.001:
        return shaded

    angle = float(values.get("face_sdf_angle", 0.0))
    neutral = float(values.get("face_sdf_neutral", 0.5))
    split = max(0.05, min(0.95, float(values.get("face_sdf_split", 0.5))))
    smooth = max(0.005, float(values.get("face_sdf_smooth", 0.03)))
    scale = float(values.get("face_sdf_angle_scale", 1.6))

    # |angle| * scale,钳制到 0..1,表示"线离中线滑了多远"
    abs_angle = b.node("ShaderNodeMath", "角度绝对值", (-640, -1300), operation="ABSOLUTE")
    abs_angle.inputs[0].default_value = angle
    scaled = b.node("ShaderNodeMath", "角度乘倍率", (-460, -1300), operation="MULTIPLY")
    scaled.inputs[1].default_value = scale
    tree.links.new(b.out(abs_angle, "Value"), b.inp(scaled, "Value", 0))
    amount = b.node("ShaderNodeClamp", "滑多远", (-280, -1300))
    amount.inputs["Min"].default_value = 0.0
    amount.inputs["Max"].default_value = 1.0
    tree.links.new(b.out(scaled, "Value"), b.inp(amount, "Value"))

    # 线在"半边"里的位置 = neutral + amount * (target - neutral)
    #   angle >= 0 -> target = 0   (读右半,线从中线向左滑)
    #   angle <  0 -> target = 1   (读左半,线从中线向右滑)
    # 用三步显式算术,不用 Map Range:
    #   delta = target - neutral
    #   offset = amount * delta
    #   line = neutral + offset
    # target 本身用 GREATER_THAN + 组合得到:angle>=0 -> 0,否则 1
    is_left_light = b.node("ShaderNodeMath", "光在左侧吗", (-640, -1450), operation="LESS_THAN")
    is_left_light.inputs[0].default_value = angle
    is_left_light.inputs[1].default_value = 0.0
    # LESS_THAN 输出 0/1;这正是我们要的 target
    delta = b.node("ShaderNodeMath", "目标减中线", (-100, -1450), operation="SUBTRACT")
    tree.links.new(b.out(is_left_light, "Value"), b.inp(delta, "Value", 0))
    delta.inputs[1].default_value = neutral
    offset = b.node("ShaderNodeMath", "偏移量", (80, -1450), operation="MULTIPLY")
    tree.links.new(b.out(amount, "Result"), b.inp(offset, "Value", 0))
    tree.links.new(b.out(delta, "Value"), b.inp(offset, "Value", 1))
    line = b.node("ShaderNodeMath", "线位置", (260, -1450), operation="ADD")
    tree.links.new(b.out(offset, "Value"), b.inp(line, "Value", 0))
    line.inputs[1].default_value = neutral


    # ---- 采样 UV:在 UV.x 上**加偏移**,V 保持原样 ----
    #
    # 关键:这里是"原 U + 偏移",不是"把 U 整个替换掉"。
    # 一开始写错成替换 U,后果是贴图 V 坐标在整个面片上保持不变,
    # 采样出来是常数 —— 表现跟"角度完全不起作用"一模一样,很难查。
    #
    #   offset = line - neutral      (light 在正前方时为 0)
    #   U      = UV.x + offset
    #
    # 偏移的正负天然给出正确方向,不需要再按左右镜像:
    #   光在右(angle>0)  -> line < neutral -> offset < 0 -> 线向左滑
    #   光在左(angle<0)  -> line > neutral -> offset > 0 -> 线向右滑
    separate = b.node("ShaderNodeSeparateXYZ", "拆UV", (620, -2050))
    tree.links.new(uv_out, b.inp(separate, "Vector"))

    offset = b.node("ShaderNodeMath", "偏移量UI", (620, -1900), operation="SUBTRACT")
    tree.links.new(b.out(line, "Value"), b.inp(offset, "Value", 0))
    offset.inputs[1].default_value = neutral

    u_shifted = b.node("ShaderNodeMath", "U加偏移", (800, -2050), operation="ADD")
    tree.links.new(b.out(separate, "X"), b.inp(u_shifted, "Value", 0))
    tree.links.new(b.out(offset, "Value"), b.inp(u_shifted, "Value", 1))

    combine = b.node("ShaderNodeCombineXYZ", "组合UV", (1180, -1950))
    tree.links.new(b.out(u_shifted, "Value"), b.inp(combine, "X"))
    tree.links.new(b.out(separate, "Y"), b.inp(combine, "Y"))
    combine.inputs[2].default_value = 0.0

    sdf_tex = b.node("ShaderNodeTexImage", "面部SDF贴图", (1380, -1950))
    sdf_tex.extension = "EXTEND"
    sdf_tex.interpolation = "Linear"
    tree.links.new(b.out(combine, "Vector"), b.inp(sdf_tex, "Vector"))

    # ---- 重切成硬边 ----
    # 贴图边缘是软的,用 Map Range 挤压成近乎阶跃(等效 Godot 的 gk_band)。
    # 这里用 Map Range 是安全的:它是"数值到数值"的单向映射,没有类型歧义。
    hard = b.node("ShaderNodeMapRange", "面部硬边", (1600, -1900))
    hard.clamp = True
    hard.inputs["From Min"].default_value = 0.5 - smooth
    hard.inputs["From Max"].default_value = 0.5 + smooth
    hard.inputs["To Min"].default_value = 0.0
    hard.inputs["To Max"].default_value = 1.0
    tree.links.new(b.out(sdf_tex, "Color"), b.inp(hard, "Value", 0))

    # 映射到与几何光照同一空间(-1..1),再喂给已有的色阶,
    # 这样面部与身体配色完全一致,不会出现"脸和身子色差"。
    to_ramp = b.node("ShaderNodeMapRange", "面部转色阶", (1800, -1900))
    to_ramp.clamp = True
    to_ramp.inputs["From Min"].default_value = 0.0
    to_ramp.inputs["From Max"].default_value = 1.0
    to_ramp.inputs["To Min"].default_value = -1.0
    to_ramp.inputs["To Max"].default_value = 1.0
    tree.links.new(b.out(hard, "Result"), b.inp(to_ramp, "Value"))

    face_ramp = b.node("ShaderNodeValToRGB", "面部色阶", (2000, -1900))
    _configure_ramp(face_ramp, values, soft=True)
    tree.links.new(b.out(to_ramp, "Result"), b.inp(face_ramp, "Fac"))

    # ---- 与几何光照结果混合 ----
    blend = b.node("ShaderNodeMix", "面部SDF混合", (2200, 200))
    blend.data_type = "RGBA"
    blend.inputs[0].default_value = mix_amount
    tree.links.new(shaded, b.inp(blend, "A", 6))
    tree.links.new(b.out(face_ramp, "Color"), b.inp(blend, "B", 7))
    return b.out(blend, "Result")


def _configure_map_range(node, to_min, to_max, from_min=0.0, from_max=1.0):
    """Map Range 节点的输入名比较长,单独抽出来设,免得写错。"""
    node.clamp = True
    try:
        node.inputs["From Min"].default_value = from_min
        node.inputs["From Max"].default_value = from_max
        node.inputs["To Min"].default_value = to_min
        node.inputs["To Max"].default_value = to_max
    except KeyError:
        # 不同版本可能没有命名输入,退回按序号
        node.inputs[1].default_value = from_min
        node.inputs[2].default_value = from_max
        node.inputs[3].default_value = to_min
        node.inputs[4].default_value = to_max
    return node


def _find_face_sdf_image():
    """在场景/数据里找一张像面部 SDF 的贴图。

    优先按文件名匹配(face_sdf / facesdf),找不到就退回"当前在图像编辑器
    里打开的那张"。这样用户在 Blender 里打开 face_sdf.png 之后,
    生成材质就能直接看到面部阴影效果,不用先去属性里指定贴图。
    """
    candidates = []
    for image in bpy.data.images:
        name = (image.name or "").lower()
        if "face_sdf" in name or "facesdf" in name or "sdf" in name:
            candidates.append(image)
    if candidates:
        return candidates[0]

    # 退回:图像编辑器里当前显示的那张
    try:
        for area in bpy.context.screen.areas if bpy.context.screen else []:
            if area.type != "IMAGE_EDITOR":
                continue
            space = area.spaces.active
            if space is not None and space.image is not None:
                return space.image
    except Exception:
        pass
    return None


def compute_face_sdf_angle(scene=None):
    """从场景里的太阳光反推面部 SDF 的系数。

    系数定义(与 Godot 的语义对应):
        0    = 光从正前方(阴影线在贴图中线)
        +1   = 光从画面右侧
        -1   = 光从画面左侧

    做法:取太阳光在世界空间的朝向与相机水平朝向的夹角。
    这是**近似** —— Godot 用的是逐片元的视图空间叉积,但固定光照时
    两者给出同样的阴影线位置,这正是预览要的效果。
    """
    import math

    scene = scene or bpy.context.scene
    if scene is None:
        return None

    # **必须**先刷新依赖图。
    # matrix_world 不会因为你改了 rotation_euler 就自动更新 —— 它要等依赖图
    # 求值。不刷新的后果是读到旧矩阵(新建物体时就是单位矩阵),
    # 于是光方向恒为 (0,0,1)、算出来的系数永远是 0。
    # 这个 bug 很隐蔽:函数不报错,只是静默返回错值。
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass

    camera = scene.camera
    if camera is None:
        return None

    light = None
    for obj in scene.objects:
        if obj.type == "LIGHT" and obj.data.type == "SUN" and not obj.hide_get():
            light = obj
            break
    if light is None:
        return None

    # 太阳光沿自身 -Z 照射;光"来自"的方向是它的 +Z
    from mathutils import Vector

    light_dir = light.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))
    cam_dir = camera.matrix_world.to_3x3() @ Vector((0.0, 0.0, -1.0))

    # 只取水平分量(绕 Z 轴的夹角),因为面部 SDF 只管左右滑动
    light_flat = (light_dir.x, light_dir.y)
    cam_flat = (cam_dir.x, cam_dir.y)
    light_len = math.hypot(*light_flat)
    cam_len = math.hypot(*cam_flat)
    if light_len < 1e-6 or cam_len < 1e-6:
        return 0.0

    # 用叉积的 z 分量判断左右(y 轴朝上是 Blender 的 Z-up)
    cross_z = cam_flat[0] * light_flat[1] - cam_flat[1] * light_flat[0]
    dot = (cam_flat[0] * light_flat[0] + cam_flat[1] * light_flat[1]) / (light_len * cam_len)
    dot = max(-1.0, min(1.0, dot))
    angle_rad = math.atan2(cross_z, dot)      # 相对相机的水平夹角
    # 归一化到 -1..1(±90 度即满值)
    return max(-1.0, min(1.0, angle_rad / (math.pi * 0.5)))


def build_toon_material(name, params=None, base_texture=None, uv_layer="UVMap",
                        textures=None):
    """把 NPR 卡通节点**直接内联**建进一个材质里。

    每个材质都为自己的模型生成,所以不会出现"空贴图节点"——
    那正是节点组方案全黑的原因(见模块开头说明)。

    数据流(两边表示不同,别混):
        材质自定义属性  <- 存 **sRGB** 值,导出时直接透传给 Godot
        节点(RGB/色标) <- 用 **线性** 值,否则 EEVEE 渲染会偏亮

    textures: {uniform: 图片名} —— 高光形状图 / 遮罩图(第一百零一轮)。
    不传就从材质自定义属性上读(所以"改完参数重建预览"不会把贴图弄丢)。
    图片名在 `bpy.data.images` 里找不到时**不建节点**(空 TexImage 会输出
    透明黑,把整条链乘成 0 —— 这个坑见模块开头)。
    """
    values = dict(DEFAULT_TUNING)
    values.update(params or {})

    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)

    # 高光贴图:从参数里拿(或从材质属性读),顺便把属性同步回名字
    spec_images = {}
    for uniform in TEXTURE_KEYS:
        raw = (textures or {}).get(uniform)
        if raw is None:
            raw = get_texture(material, uniform)
        image = raw
        if isinstance(raw, str):
            # ⚠ 这里**不能**写 `import bpy` —— 那会让 bpy 变成这个函数的
            # 局部变量,于是上面那句 `bpy.data.materials.get(name)` 直接
            # UnboundLocalError(实测踩到)。模块级已经 import 过 bpy 了。
            image = bpy.data.images.get(raw)
        if image is not None:
            spec_images[uniform] = image
            set_texture(material, uniform, getattr(image, "name", ""))

    b = NodeBuilder(tree)

    # ---- 输出 ----
    output = b.node("ShaderNodeOutputMaterial", "材质输出", (1500, 0))
    emission = b.node("ShaderNodeEmission", "输出", (1250, 0))
    tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])

    # ---- UV ----
    # 用 TexCoord.UV:直接读网格第一套 UV,永远有效。
    # (未连线的 UVMap 节点会退化成 (0,0),这是踩过的坑之一。)
    uv_source = b.node("ShaderNodeTexCoord", "纹理坐标", (-1200, -300))
    uv_out = uv_source.outputs["UV"]

    # 面部 SDF 需要**第二套 UV**。单独建一个 UVMap 节点,
    # 只在真的用到面部 SDF 时才接进链路。
    uv2_source = b.node("ShaderNodeUVMap", "UV2来源", (-1200, -1150))
    uv2_source.uv_map = "UVMap.001"
    # 面部 SDF 的 V 坐标来源。
    # **默认用 UV1**,不是 UV2 —— 因为 UVMap 节点在找不到指定层时返回 (0,0),
    # V 就变成常量,阴影线滑动完全看不出来(实测:五个角度渲染结果一模一样)。
    # Godot 那边可以用材质的"UV通道"切到 UV2;Blender 预览用 UV1 足够,
    # 而且配合 _find_face_sdf_image 的自动找图,打开即见效果。
    uv2_out = uv_source.outputs["UV"]

    # ---- 基础色 ----
    albedo = b.node("ShaderNodeRGB", "基础色", (-1200, 250))
    r, g, bl = to_linear_rgb(values.get("albedo_color", (1.0, 0.93, 0.91)))
    albedo.outputs["Color"].default_value = (r, g, bl, 1.0)

    color_source = b.out(albedo, "Color")
    if base_texture is not None:
        # 只有真的有贴图时才创建贴图节点 —— 空 TexImage 会输出透明黑,
        # 把整条链乘成 0(节点组方案全黑的真正原因)。
        tex = b.node("ShaderNodeTexImage", "基础色贴图", (-1200, 60))
        tex.image = base_texture
        if base_texture.colorspace_settings is not None:
            try:
                base_texture.colorspace_settings.name = "sRGB"
            except Exception:
                pass
        tree.links.new(uv_out, b.inp(tex, "Vector"))
        tint = b.mix_rgb("贴图×基础色", (-950, 150), "MULTIPLY")
        tree.links.new(b.out(tex, "Color"), b.inp(tint, "A", 6))
        tree.links.new(color_source, b.inp(tint, "B", 7))
        color_source = b.out(tint, "Result")

    strength = b.mix_rgb("×强度", (-750, 150), "MULTIPLY")
    strength_value = float(values.get("albedo_strength", 1.0))
    strength.inputs[0].default_value = strength_value
    tree.links.new(color_source, b.inp(strength, "A", 6))
    tree.links.new(color_source, b.inp(strength, "B", 7))
    albedo_out = b.out(strength, "Result")

    # ---- 光照 -> 亮度 -> 色阶 ----
    diffuse = b.node("ShaderNodeBsdfDiffuse", "漫反射", (-500, 300))
    tree.links.new(albedo_out, b.inp(diffuse, "Color"))

    s2rgb = b.node("ShaderNodeShaderToRGB", "取光照", (-300, 300))
    tree.links.new(b.out(diffuse, "BSDF"), b.inp(s2rgb, "Shader"))

    # 必须转成亮度再喂 ColorRamp。直接把颜色接到 Fac 上,在亮场景里
    # 会整体饱和(踩过的坑之一)。
    to_bw = b.node("ShaderNodeRGBToBW", "光照亮度", (-100, 300))
    tree.links.new(b.out(s2rgb, "Color"), b.inp(to_bw, "Color"))

    ramp_hard = b.node("ShaderNodeValToRGB", "色阶(硬)", (100, 420))
    _configure_ramp(ramp_hard, values, soft=False)
    ramp_soft = b.node("ShaderNodeValToRGB", "色阶(柔)", (100, 120))
    _configure_ramp(ramp_soft, values, soft=True)
    tree.links.new(b.out(to_bw, "Val"), b.inp(ramp_hard, "Fac"))
    tree.links.new(b.out(to_bw, "Val"), b.inp(ramp_soft, "Fac"))

    # 分界软硬:在硬边和渐变之间混合,语义与 Godot 的 shadow_smooth 一致
    smooth = b.node("ShaderNodeMath", "软硬系数", (-100, -80), operation="MULTIPLY")
    smooth.inputs[1].default_value = 8.0
    smooth.inputs[0].default_value = float(values.get("shadow_smooth", 0.02))
    clamp = b.node("ShaderNodeClamp", "限幅", (100, -80))
    tree.links.new(b.out(smooth, "Value"), b.inp(clamp, "Value"))

    blend = b.node("ShaderNodeMix", "按软硬混合", (330, 300))
    try:
        blend.data_type = "RGBA"
    except Exception:
        pass
    tree.links.new(b.out(clamp, "Result"), b.inp(blend, "Factor", 0))
    tree.links.new(b.out(ramp_hard, "Color"), b.inp(blend, "A", 6))
    tree.links.new(b.out(ramp_soft, "Color"), b.inp(blend, "B", 7))
    shaded = b.out(blend, "Result")

    # ---- 面部阴影 SDF(可选)----
    # 放在高光/边缘光之前:面部 SDF 只改"明暗",不该影响高光与轮廓。
    if float(values.get("face_sdf_mix", 0.0)) > 0.001:
        # 贴图优先用参数里给的;没有就退回场景里第一张图像,
        # 这样用户在图像编辑器里打开 face_sdf.png 就能直接看到效果。
        face_image = values.get("face_sdf_image", None)
        if face_image is None:
            face_image = _find_face_sdf_image()
        if face_image is None:
            # 没贴图就**不建节点**。空 TexImage 会输出透明黑、把整条链乘成 0
            # —— 这个坑在节点组方案里踩过,见模块开头的说明。
            pass
        else:
            shaded = build_face_sdf_nodes(b, tree, values, uv_out, shaded)
            sdf_node = tree.nodes.get("面部SDF贴图")
            if sdf_node is not None:
                sdf_node.image = face_image
                # **不要**去改这张贴图的 colorspace_settings。
                # 实测:给"用 bpy.data.images.new() 程序生成、没有磁盘文件"的
                # 图像设成 Non-Color,会让纹理节点输出全黑 —— 切色彩空间时
                # 它的缓冲被清掉了(切回 sRGB 立刻恢复)。
                # 而且没必要:SDF 的键控点是 0.5,程序生成图像的像素本来就是
                # 线性值,正是 SDF 需要的;sRGB 解码是单调的,不改变明暗分界
                # 落在哪一侧。
                # 注意:从磁盘读入的 face_sdf.png 由用户自己在图像属性里设
                # Non-Color,那是安全的(有文件缓冲)。

    # ---- 高光(硬边色带)----
    #
    # ── 预览里这个高光是**近似**,别当成 1:1(第一百零一轮写清)────────
    # Godot 那边用的是"视线与光线的半程向量" N·H;Blender 节点里拿不到
    # 那个量(和面部 SDF 同一个限制),所以这里用菲涅尔 Facing 代替。
    # 因此:
    #   * 『高光大小』按**相对默认值的偏移**挪一下色带位置(方向对得上:
    #     越大 -> 高光越大),精确位置两边不可能一致;
    #   * 『高光边缘软硬』**不参与预览**(色带是 CONSTANT 硬边)——
    #     那个只能在 Godot 里看;
    #   * 『高光颜色』与两张**高光贴图**是 1:1 的(同一套采样与乘法),
    #     所以"贴图把高光改成空心环 / 抹掉"在预览里也看得见。
    # 默认参数下这条链的输出和以前**逐字节一致**(颜色白 = 乘 1)。
    spec_amount = float(values.get("specular_amount", 0.25))
    if spec_amount > 0.001:
        spec_size = float(values.get("specular_size", 0.75))
        layer = b.node("ShaderNodeLayerWeight", "菲涅尔", (100, -420))
        layer.inputs["Blend"].default_value = 0.35
        spec_ramp = b.node("ShaderNodeValToRGB", "高光色带", (330, -420))
        spec_ramp.color_ramp.interpolation = "CONSTANT"
        spec_step = max(0.05, min(0.95, 0.72 - (spec_size - 0.75) * 0.6))
        spec_ramp.color_ramp.elements[0].position = max(0.0, spec_step - 0.05)
        spec_ramp.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
        spec_ramp.color_ramp.elements[1].position = spec_step
        spec_ramp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
        tree.links.new(b.out(layer, "Facing"), b.inp(spec_ramp, "Fac"))
        spec_shape = b.out(spec_ramp, "Color")

        # 高光颜色(默认纯白 —— 乘法恒等,老材质的预览不变)
        spec_rgb = b.node("ShaderNodeRGB", "高光颜色", (330, -620))
        sr, sg, sb = to_linear_rgb(values.get("specular_color", (1.0, 1.0, 1.0)))
        spec_rgb.outputs["Color"].default_value = (sr, sg, sb, 1.0)
        spec_tint = b.mix_rgb("高光上色", (560, -620), "MULTIPLY")
        tree.links.new(spec_shape, b.inp(spec_tint, "A", 6))
        tree.links.new(b.out(spec_rgb, "Color"), b.inp(spec_tint, "B", 7))
        spec_shape = b.out(spec_tint, "Result")

        # 高光形状贴图:用"当前高光强度"当横坐标去采样那条渐变
        # (和着色器里的 vec2(spot, 0.5) 是同一个意思)
        shape_image = spec_images.get("specular_shape_texture")
        if shape_image is not None:
            coords = b.node("ShaderNodeCombineXYZ", "形状采样坐标", (790, -620))
            tree.links.new(spec_shape, b.inp(coords, "X"))
            coords.inputs["Y"].default_value = 0.5
            coords.inputs["Z"].default_value = 0.0
            shape_tex = b.node("ShaderNodeTexImage", "高光形状贴图", (1010, -620))
            shape_tex.image = shape_image
            shape_tex.interpolation = "Linear"
            tree.links.new(b.out(coords, "Vector"), b.inp(shape_tex, "Vector"))
            shape_mul = b.mix_rgb("形状×高光", (1250, -620), "MULTIPLY")
            tree.links.new(spec_shape, b.inp(shape_mul, "A", 6))
            tree.links.new(b.out(shape_tex, "Color"), b.inp(shape_mul, "B", 7))
            spec_shape = b.out(shape_mul, "Result")

        # 高光遮罩贴图:按 UV 决定"这块面片上到底出不出高光"
        mask_image = spec_images.get("specular_mask_texture")
        if mask_image is not None:
            mask_tex = b.node("ShaderNodeTexImage", "高光遮罩贴图", (790, -880))
            mask_tex.image = mask_image
            mask_tex.interpolation = "Linear"
            tree.links.new(uv_out, b.inp(mask_tex, "Vector"))
            mask_mul = b.mix_rgb("遮罩×高光", (1010, -880), "MULTIPLY")
            tree.links.new(spec_shape, b.inp(mask_mul, "A", 6))
            tree.links.new(b.out(mask_tex, "Color"), b.inp(mask_mul, "B", 7))
            spec_shape = b.out(mask_mul, "Result")

        spec_scale = b.mix_rgb("高光强度", (1250, -420), "MULTIPLY", spec_amount)
        tree.links.new(spec_shape, b.inp(spec_scale, "A", 6))
        tree.links.new(spec_shape, b.inp(spec_scale, "B", 7))
        add_spec = b.mix_rgb("加高光", (1480, -200), "ADD")
        tree.links.new(shaded, b.inp(add_spec, "A", 6))
        tree.links.new(b.out(spec_scale, "Result"), b.inp(add_spec, "B", 7))
        shaded = b.out(add_spec, "Result")

    # ---- 边缘光 ----
    rim_amount = float(values.get("rim_amount", 0.0))
    if rim_amount > 0.001:
        layer2 = b.node("ShaderNodeLayerWeight", "菲涅尔2", (100, -700))
        layer2.inputs["Blend"].default_value = float(values.get("rim_width", 0.55))
        invert = b.node("ShaderNodeMath", "边缘反相", (330, -700), operation="SUBTRACT")
        invert.inputs[0].default_value = 1.0
        tree.links.new(b.out(layer2, "Facing"), b.inp(invert, "Value", 1))
        rim_ramp = b.node("ShaderNodeValToRGB", "边缘色带", (560, -700))
        rim_ramp.color_ramp.interpolation = "EASE"
        rim_ramp.color_ramp.elements[0].position = 0.0
        rim_ramp.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
        rim_ramp.color_ramp.elements[1].position = 0.45
        rim_ramp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
        tree.links.new(b.out(invert, "Value"), b.inp(rim_ramp, "Fac"))

        rim_color = b.node("ShaderNodeRGB", "边缘光颜色", (560, -880))
        rr, rg, rb = to_linear_rgb(values.get("rim_color", (0.75, 0.85, 1.0)))
        rim_color.outputs["Color"].default_value = (rr, rg, rb, 1.0)
        rim_tint = b.mix_rgb("边缘光色", (790, -700), "MULTIPLY")
        tree.links.new(b.out(rim_ramp, "Color"), b.inp(rim_tint, "A", 6))
        tree.links.new(b.out(rim_color, "Color"), b.inp(rim_tint, "B", 7))
        rim_scale = b.mix_rgb("边缘光强度", (1010, -700), "MULTIPLY", rim_amount)
        tree.links.new(b.out(rim_tint, "Result"), b.inp(rim_scale, "A", 6))
        tree.links.new(b.out(rim_tint, "Result"), b.inp(rim_scale, "B", 7))

        add_rim = b.mix_rgb("加边缘光", (1250, -400), "ADD")
        tree.links.new(shaded, b.inp(add_rim, "A", 6))
        tree.links.new(b.out(rim_scale, "Result"), b.inp(add_rim, "B", 7))
        shaded = b.out(add_rim, "Result")

    tree.links.new(shaded, b.inp(emission, "Color"))

    # ---- 参数存到材质自定义属性(存 sRGB 原值,导出直接透传)----
    write_params(material, values)
    return material


# 不在 PARAM_MAP 里、但会出现在材质上的自定义属性的中文名。
#
# 这些不是我们的参数,是别的插件留下的(比如 MMD Tools 的 mmd_material)。
# 不能改它们的值,但可以把显示名标清楚,免得用户以为是自己该调的。
EXTRA_DISPLAY_NAMES = {
    "mmd_material": "MMD 材质(其它插件,不用管)",
    "prop": "自定义数据(其它插件)",
}


def material_key(name):
    """参数名 -> 存在材质上的**键名**。

    ── 为什么键名用中文 ────────────────────────────────────────────
    自定义属性在『材质属性 > 自定义属性』面板里显示的就是**键名**。
    键叫 albedo_color,面板里就显示 albedo_color —— 英文界面的用户无所谓,
    中文界面的用户完全不知道哪个是哪个。

    试过用 `_RNA_UI` 给键设显示名(这是 Blender 的老办法):
    数据能写进去、也能读回来,但**面板不采纳**(实测 Blender 4.5),
    属性类型是数组时(颜色)尤其不生效。

    所以直接让键名就是中文。实测中文键可以正常读写、存盘、导出。
    """
    labels = {name: label for name, _u, _k, _d, label in PARAM_MAP}
    return labels.get(name) or name


def get_param(material, name, default=None):
    """从材质读一个参数,中文键和旧的英文键都认。

    兼容旧键是必须的:用户项目里的材质是旧版本生成的,键还是英文。
    """
    if material is None:
        return default
    key = material_key(name)
    value = material.get(key)
    if value is None and key != name:
        value = material.get(name)
    return default if value is None else value


def has_param(material, name):
    """材质上有没有这个参数(中文键或英文键都算)。"""
    if material is None:
        return False
    return (material.get(material_key(name)) is not None
            or material.get(name) is not None)


def migrate_keys(material):
    """把材质上旧的**英文键**改成中文键。返回迁移了几个。

    在打开文件/注册插件时跑一次,用户不用重新生成材质。
    """
    if material is None:
        return 0
    moved = 0
    for name, _uniform, _kind, _default, label in PARAM_MAP:
        if not label or label == name:
            continue
        if material.get(label) is not None:
            # 已经是中文键了;旧的英文键留着会让人困惑,删掉
            if material.get(name) is not None:
                try:
                    del material[name]
                except (KeyError, TypeError):
                    pass
            continue
        value = material.get(name)
        if value is None:
            continue
        try:
            material[label] = value
            del material[name]
            moved += 1
        except (TypeError, KeyError):
            continue
    return moved


def migrate_all_keys():
    """给所有材质迁移键名。返回迁移过的材质数。"""
    try:
        materials = bpy.data.materials
    except AttributeError:
        # load_post 这类受限上下文里 bpy.data 是 _RestrictData,取不到
        return 0
    count = 0
    for material in materials:
        try:
            if migrate_keys(material) > 0:
                count += 1
        except Exception:
            continue
    return count


def ensure_display_names(material):
    """给材质上**已有**的自定义属性补中文显示名。返回补了几个。

    ── 为什么需要"补"而不是"写" ────────────────────────────────────
    write_params 只在**生成材质**时跑。用户项目里的材质是旧版本生成的,
    里面没有 _RNA_UI —— 装了新版本插件、材质还是旧的,名字就还是英文。
    (实测:用户材质 `_RNA_UI 存在: False`,24 个属性全是英文名。)

    所以这里独立出一个"回填"函数:扫材质上现有的键,能对上 PARAM_MAP
    的就用它的中文标签,对不上的用 EXTRA_DISPLAY_NAMES,
    最后还有没名字的加个兜底 —— 至少不会是一串英文键名。
    """
    if material is None:
        return 0
    labels = {name: label for name, _u, _k, _d, label in PARAM_MAP}
    labels.update(EXTRA_DISPLAY_NAMES)

    try:
        rna_ui = material.get("_RNA_UI")
        if rna_ui is None:
            material["_RNA_UI"] = {}
            rna_ui = material["_RNA_UI"]
    except (TypeError, AttributeError):
        return 0

    added = 0
    for key in material.keys():
        if key.startswith("_"):
            continue        # _RNA_UI 自己不算
        label = labels.get(key)
        if not label:
            # 兜底:至少带上"自定义"字样,让用户知道不是标准参数
            label = f"自定义: {key}"
        entry = rna_ui.get(key)
        if entry is None:
            rna_ui[key] = {}
            entry = rna_ui[key]
        current = None
        try:
            current = entry.get("name")
        except AttributeError:
            current = None
        if current != label:
            entry["name"] = label
            added += 1
    return added


def backfill_display_names():
    """把旧材质上的英文键迁成中文键,并顺手写一次 _RNA_UI。

    返回处理过的材质数。在插件注册时、以及每次打开 .blend 之后调用 ——
    用户装上新版本之后,旧材质立刻显示中文名,不需要重新生成材质。
    """
    count = 0
    try:
        materials = bpy.data.materials
    except AttributeError:
        # 在 load_post 这类**受限上下文**里,bpy.data 会变成 _RestrictData,
        # 访问 materials 直接抛 AttributeError。实测踩过:每次打开文件都刷
        # 一行 "回填材质中文名失败"。这不是致命错误,安静跳过即可。
        return 0
    for material in materials:
        try:
            migrate_keys(material)
            ensure_display_names(material)
            count += 1
        except Exception:
            continue
    return count


def on_register():
    """注册时把已有材质的中文显示名补齐。

    为什么放在这里:用户装上新版本之后,项目里那些**旧材质**是以前生成的,
    里面没有 _RNA_UI,自定义属性面板里显示的还是 albedo_color 这种英文键名。
    在这里回填一次,不用重新生成材质就能看到中文名。
    """
    try:
        count = backfill_display_names()
        if count:
            import bpy as _bpy

            print(f"[BlenderGameKit] 已为 {count} 个材质补齐参数中文名")
    except Exception as exc:
        print(f"[BlenderGameKit] 回填材质中文名失败: {exc}")


def on_unregister():
    """对称的清理钩子(目前没有需要清理的)。"""


def write_params(material, values):
    """把参数写进材质的自定义属性(id_properties)。

    **颜色按 sRGB 原样存**,不做线性转换。
    原因:材质属性是"给导出用的中间表示",而 Godot 的 Color(...)
    就是 sRGB 值 —— 存 sRGB 才能让导出直接透传。
    线性转换只在建节点(RGB 节点 / ColorRamp 色标)时做。
    (一开始这里顺手做了线性转换,结果导出成 Color(1,1,1),
    测试里的"导出的是 sRGB 颜色"那条抓到了。)

    ── 顺便写显示名 ────────────────────────────────────────────────
    自定义属性在 Blender 的『材质属性 > 自定义属性』面板里显示的是**键名**,
    也就是 albedo_color / face_sdf_mix 这种英文。
    用户看中文界面时这很割裂,所以给每个属性写一个 `_RNA_UI` 描述块,
    里面放中文 `name` —— Blender 会用那个名字显示。
    """
    # 一次性拿到(或建好) _RNA_UI 字典,把中文名都写进去
    try:
        rna_ui = material.get("_RNA_UI")
        if rna_ui is None:
            material["_RNA_UI"] = {}
            rna_ui = material["_RNA_UI"]
        for name, _uniform, _kind, _default, label in PARAM_MAP:
            if not label:
                continue
            entry = rna_ui.get(name)
            if entry is None:
                rna_ui[name] = {}
                entry = rna_ui[name]
            entry["name"] = label
    except (TypeError, AttributeError):
        # 老版本 Blender 或特殊 ID 上写不了 _RNA_UI —— 不影响功能,跳过
        pass

    for name, _uniform, kind, default, label in PARAM_MAP:
        value = values.get(name, default)
        # 键名用**中文**:面板『自定义属性』里显示的就是键名,
        # 用英文的话中文界面的用户完全看不懂(见 material_key 的说明)。
        material[material_key(name)] = _rgb(value) if kind == "COLOR" else float(value)
    return material


def read_params(material):
    """从材质自定义属性读出参数,返回 {Godot uniform 名: 值}。

    颜色在材质属性里存的是 **sRGB** 值(Godot 那边的表示),
    这样导出时直接写进 .tres 就是对的,不需要再转换。

    坑:Blender 的自定义属性数组是 `<bpy id property array [3]>`,
    **不是** Python 的 list/tuple,所以不能用 isinstance 判断,
    否则会静默走到 fallback 分支、把所有颜色读成默认白色
    (测试里"导出的是 sRGB 颜色"那条抓到了这个 bug)。
    """
    out = {}
    for name, uniform, kind, default, _label in PARAM_MAP:
        stored = get_param(material, name, None)
        if stored is None:
            stored = default
        if kind == "COLOR":
            values = _to_sequence(stored)
            out[uniform] = (values[0], values[1], values[2], 1.0)
        else:
            try:
                out[uniform] = float(stored)
            except (TypeError, ValueError):
                out[uniform] = float(default)
    # ⚠ 高光贴图的 `use_*` 开关**不在这里**加(第一百零一轮)。
    # 这个函数的契约是"一个 PARAM_MAP 条目 -> 一个返回值",加了别的键
    # 就破坏了它(实测:`blender_material_params.py` 那条"能读出所有参数"
    # 当场判红)。开关由导出侧按"有没有填图"直接写(shader_parameter/use_*),
    # 值也永远是自洽的。
    return out


def _to_sequence(value):
    """把任意"像数组的东西"转成至少 3 个浮点的列表。

    单独抽出来是因为 Blender 的 id property array 既不是 list 也不是 tuple,
    用 isinstance 判断一定会漏。这里直接试着转,失败才退默认值。
    """
    try:
        values = [float(v) for v in value]
    except (TypeError, ValueError):
        values = []
    while len(values) < 3:
        values.append(1.0)
    return values[:3]


def current_base_color(material):
    """读材质节点里**当前**的基础色,返回 **sRGB** 值。

    为什么要单独读节点:用户可能直接在着色器编辑器里改颜色 ——
    那时节点是新的,而自定义属性还是旧的。
    重建材质时如果从属性取值,用户的改动就**被悄悄改回去了**。

    ── 必须转回 sRGB,这是踩过的坑 ──────────────────────────────────
    RGB 节点里存的是**线性**值(见 build_toon_material 的说明),
    而 build_toon_material 收到 sRGB 值后会自己再转一次线性。
    所以这里如果直接把线性值当 sRGB 传回去,就会**多转一次** ——
    实测:用户设的 (0.8, 0.1, 0.05) 重建后变成 (1.0, 0.852, 0.811),
    也就是"材质被洗白/重置了"。
    """
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return None
    for node in tree.nodes:
        if node.bl_idname == "ShaderNodeRGB":
            try:
                color = node.outputs[0].default_value
                return list(from_linear_rgb((float(color[0]), float(color[1]),
                                             float(color[2]))))
            except (IndexError, TypeError, ValueError):
                continue
    return None


def base_color_from_principled(material):
    """从材质的 Principled BSDF 读基础色,返回 **sRGB** 值(拿不到给默认色)。

    第一百零一轮:"生成 / 刷新预览材质"按钮要能在**用户自己的材质**上工作 ——
    他那个材质很可能只是一个带颜色的 Principled BSDF,自定义属性里
    根本没有 albedo_color。这时应该把他的颜色带过来,而不是把材质
    洗成默认的那点粉。
    """
    tree = getattr(material, "node_tree", None)
    if tree is not None:
        for node in tree.nodes:
            if node.type != "BSDF_PRINCIPLED":
                continue
            socket = node.inputs.get("Base Color")
            if socket is None or socket.is_linked:
                continue
            try:
                value = socket.default_value
                return list(from_linear_rgb((float(value[0]), float(value[1]),
                                             float(value[2]))))
            except (IndexError, TypeError, ValueError):
                break
    return list(DEFAULT_TUNING.get("albedo_color", (1.0, 0.932, 0.912)))


def current_base_image(material):
    """读材质节点里当前用的基础色贴图(可能没有)。

    ⚠ 第一百零一轮起材质里**不止一个**图片节点(高光形状图 / 遮罩图),
    所以不能"拿第一个 TexImage"了 —— 那会把高光贴图当成基础色贴图。
    先按**节点名**找"基础色贴图";找不到才退回第一个(兼容老材质)。
    """
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return None
    named = tree.nodes.get("基础色贴图")
    if named is not None and getattr(named, "image", None) is not None:
        return named.image
    for node in tree.nodes:
        if node.bl_idname != "ShaderNodeTexImage" or node.image is None:
            continue
        if node.name in SPECULAR_NODE_NAMES:
            continue
        return node.image
    return None


# 高光贴图那两个节点在预览里的名字(重建时要按名字把它们和别的图片节点分开)
SPECULAR_NODE_NAMES = ("高光形状贴图", "高光遮罩贴图")


def update_material(material, params):
    """改参数后重建材质节点(色阶结构变了,直接重建最省事也最可靠)。

    ── 必须先把用户当前的设置读出来 ──────────────────────────────
    重建 = 删掉所有节点重新连一遍。所以重建前要把用户**在节点里**做的改动
    读出来,重建后再放回去,否则会被默认值覆盖:
      * 基础色贴图(用户加的图片)
      * 基础色(用户在着色器编辑器里调的颜色)

    注意要从**节点**读,不是从自定义属性读 —— 用户直接改节点时,
    属性里的值还是旧的,拿旧值重建等于把用户的改动丢掉。
    """
    base_image = current_base_image(material)
    node_color = current_base_color(material)
    values = read_params(material)
    # **先合并调用方给的新参数,最后再放用户当前的节点颜色** ——
    # 顺序不能反。params 里也可能有 albedo_color 这个键
    # (read_params 返回的键是 Godot 的 uniform 名,这里恰好同名),
    # 放在后面会把用户刚调的颜色覆盖回属性里的旧值。
    # 实测:先放颜色的话,点『取角度』会把用户调的基础色洗回默认色。
    values.update(params or {})
    if node_color is not None:
        values["albedo_color"] = tuple(node_color)
    return build_toon_material(material.name, values, base_texture=base_image)


def ensure_standard_view_transform():
    """把 view transform 设成 Standard。

    默认的 AgX/Filmic 会把离散色阶重新平滑成连续渐变,看起来就像
    "色阶没生效"。这是实测对比出来的,不是猜的。
    """
    scene = bpy.context.scene
    if scene is None:
        return False
    try:
        scene.view_settings.view_transform = "Standard"
    except Exception:
        return False
    try:
        scene.view_settings.look = "None"
    except Exception:
        pass
    return True


def make_preview_material(name, texture=None, group=None, params=None):
    """兼容旧接口:建一个 NPR 卡通材质。group 参数已不再使用。"""
    return build_toon_material(name, params=params, base_texture=texture)
