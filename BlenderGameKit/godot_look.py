"""让 Blender 的预览画面贴近 Godot 的渲染。

── 为什么需要 ──────────────────────────────────────────────────
Blender 和 Godot 的"出厂画面"差得很远,在 Blender 里调好一看 Godot
完全不是那个样子。实测出来的差异有三处,每一处都会单独把颜色带偏:

1) **色调映射**
   Godot 默认 `tonemap_mode = Linear`(等于不做色调映射),
   而 Blender 4.x 默认是 **AgX** —— AgX 会把高光压下去、颜色变灰变淡。
   这一条是"Blender 看着浓、Godot 看着淡"的主因。

2) **背景**
   Godot 没挂 WorldEnvironment 时,背景是 project.godot 里的
   `rendering/environment/defaults/default_clear_color`,默认
   **(0.3, 0.3, 0.3) 的平灰**。

3) **环境光**
   Godot 默认**没有环境光**(`ambient_light_source` 用的是那个纯黑背景),
   物体只被场景里的灯照亮。Blender 的世界背景默认会**当光源用**,
   于是多了 Godot 根本没有的环境光 —— 暗部被垫亮,对比度也不对。

所以这个模块做的事:
  * 把视图变换切成 Standard、look 清空(对应 Godot 的 Linear)
  * 建一个平灰世界,但用 Light Path 的 `Is Camera Ray` 把它限制成
    **只看得见、不照亮** —— 精确对应 Godot 的"灰底 + 零环境光"
  * 关掉 EEVEE 的光线追踪和 AO(Godot 默认没有 GI/SSAO)

── 为什么不直接改用户的 World ──────────────────────────────────
用户自己的世界可能有天空贴图、HDRI 之类。所以这里**新建**一个世界,
并把原来的世界名、视图变换、EEVEE 开关都记在场景自定义属性里,
点『还原』时原样放回去。

── 第九十五轮:对齐**你选的设置**,而不是写死 Godot 的出厂值 ────────
上面三条说的是"Godot **出厂画面**"的差异。可一旦你在『环境』设置卡上
改了色调映射 / 背景,导出的 Godot 就按**你的设置**渲染,而预览还是
出厂画面 —— 预览又开始骗人(用户:"预览和运行时画面差很大")。
现在 apply() 读 `scene.gk_settings.env_*`(**和导出用的是同一份**),
逐项对齐;设置卡没开时就退回出厂画面(= 老行为):

| 设置卡上 | 预览里怎么对齐 | 精度 |
|---|---|---|
| 色调映射 Linear | 视图变换 Standard | 精确 |
| 色调映射 AgX | 视图变换 AgX | 精确(同一条曲线) |
| Filmic / ACES / Reinhard | 挑同族的 Filmic / AgX / Filmic | **近似** |
| 背景 纯色 | 世界底色 = 那个颜色 | 精确 |
| 背景 天空 | 用**地平线色**铺平(真实是三段渐变) | **近似** |
| 环境光 | 两边都**不加**:卡通材质是 unshaded,运行时也不吃环境光 | 对卡通口径精确 |
| 雾 / 泛光 / 色调调整 | 做不出来(Godot 的后处理)—— 保持关着 | —— |

sRGB 换算(第九十五轮标定 + 第九十六轮修导出):
  * **写进 `.tscn` 的 Color 数字 = 屏幕上显示的值**(标定:
    `Environment.background_color = Color(0.3,0.3,0.3)` 渲出来就是 0.298
    的像素);
  * 第九十六轮起**导出**会把设置卡上的线性颜色换算成显示值再写
    (`asset_export._env_color_text`)—— 取色器上的色块 = 游戏里的颜色。
    所以**预览直接用设置值**:那是线性世界色,Blender 显示出来正好就是
    那个显示值,两边对上;
  * 只有"设置卡没开"的**出厂灰**要换算 `srgb_to_linear(0.30)` —— 那个
    0.30 是 Godot 给的**显示值**,不换的话背景会比 Godot 亮一大截。
"""

import bpy

# Godot 的默认清屏色(project.godot 里 default_clear_color 的默认值)。
#
# 注意这是 **sRGB** 值 —— 它是直接显示给眼睛的输出颜色。
# Blender 的 Background 节点要的是**线性**值,所以下面要转换,
# 否则背景会比 Godot 亮一大截(踩过这个:直接把 0.3 填进去,背景发白)。
GODOT_CLEAR_COLOR_SRGB = (0.3, 0.3, 0.3)

# 我们建的世界叫这个名。带 GK 前缀,方便识别和清理。
WORLD_NAME = "GK Godot 环境"

# ── 色调映射 -> Blender 视图变换(第九十五轮)────────────────────
# 键 = properties.ENV_TONEMAPS 的 value(即 Godot 的 tonemap_mode 枚举名)。
#   * LINEAR -> Standard、AGX -> AgX:名字 / 曲线同族,对得上;
#   * 其余三个 Blender 没有同名视图变换,挑最近的一族 —— 说明里会写
#     "近似",不糊弄用户。
TONEMAP_VIEW_TRANSFORMS = {
    "LINEAR": "Standard",
    "REINHARD": "Filmic",
    "FILMIC": "Filmic",
    "ACES": "AgX",
    "AGX": "AgX",
}
# 哪些算"精确对应"(面板说明里用它决定措辞)
TONEMAP_EXACT = ("LINEAR", "AGX")

# 备份的键(存在 scene 的自定义属性上)
_BACKUP = {
    "active": "gk_look_active",
    "view_transform": "gk_look_view_transform",
    "look": "gk_look_look",
    "exposure": "gk_look_exposure",
    "gamma": "gk_look_gamma",
    "world": "gk_look_world",
    "background_type": "gk_look_bg_type",
    "raytracing": "gk_look_raytracing",
    "gtao": "gk_look_gtao",
    "fast_gi": "gk_look_fast_gi",
}


def srgb_to_linear(value):
    """sRGB -> 线性。0.3 的 sRGB 约等于 0.073 的线性。"""
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def linear_to_srgb(value):
    """线性 -> sRGB。"""
    if value <= 0.0031308:
        return value * 12.92
    return 1.055 * (value ** (1.0 / 2.4)) - 0.055


def _remember(scene):
    """把当前观感设置记下来,供还原用。已经记过就不覆盖。"""
    if scene.get(_BACKUP["active"]):
        return
    view = scene.view_settings
    shading = getattr(getattr(scene, "display", None), "shading", None)
    eevee = getattr(scene, "eevee", None)
    world = scene.world

    scene[_BACKUP["view_transform"]] = str(view.view_transform)
    scene[_BACKUP["look"]] = str(view.look)
    scene[_BACKUP["exposure"]] = float(view.exposure)
    scene[_BACKUP["gamma"]] = float(view.gamma)
    scene[_BACKUP["world"]] = world.name if world is not None else ""
    scene[_BACKUP["background_type"]] = str(
        getattr(shading, "background_type", "THEME"))
    scene[_BACKUP["raytracing"]] = bool(getattr(eevee, "use_raytracing", False))
    scene[_BACKUP["gtao"]] = bool(getattr(eevee, "use_gtao", False))
    scene[_BACKUP["fast_gi"]] = bool(getattr(eevee, "use_fast_gi", False))
    scene[_BACKUP["active"]] = True


def _rgba(value, fallback):
    """设置卡里的 RGBA -> 四元组(读不动就用 fallback)。"""
    try:
        parts = [float(component) for component in value]
    except (TypeError, ValueError):
        return tuple(fallback)
    while len(parts) < 4:
        parts.append(1.0)
    return tuple(parts[:4])


def _mix_srgb(color, other, factor):
    """两个 sRGB 颜色按比例混(天空的地平线色就是这么来的)。"""
    return tuple(color[index] * (1.0 - factor) + other[index] * factor
                 for index in range(3)) + (1.0,)


def env_display(scene):
    """预览要对齐的那几项 —— 读**导出用的同一份**场景设置。

    没开『环境』(或 2D 关卡 —— 导出器同样不导环境)时返回 Godot 的
    **出厂值**:0.30 平灰、Linear 色调映射 —— 也就是老行为。
    """
    settings = getattr(scene, "gk_settings", None)
    enabled = bool(getattr(settings, "env_enabled", False)) if settings else False
    if enabled and str(getattr(settings, "dimension", "3")) == "2":
        enabled = False                    # 见 asset_export.env_settings
    if not enabled:
        # 出厂灰 0.30 是 Godot 的**显示值**(不是设置卡上的线性值)——
        # 要换算成线性才是 Blender 世界的颜色。
        grey_linear = srgb_to_linear(GODOT_CLEAR_COLOR_SRGB[0])
        return {
            "enabled": False,
            "tonemap": "LINEAR",
            "view_transform": TONEMAP_VIEW_TRANSFORMS["LINEAR"],
            "background": "COLOR",
            "backdrop": (grey_linear, grey_linear, grey_linear, 1.0),
        }
    tonemap = str(getattr(settings, "env_tonemap", "LINEAR") or "LINEAR")
    if tonemap not in TONEMAP_VIEW_TRANSFORMS:
        tonemap = "LINEAR"
    background = str(getattr(settings, "env_background", "COLOR") or "COLOR")
    bg_color = _rgba(getattr(settings, "env_bg_color", None),
                     (0.05, 0.06, 0.10, 1.0))
    # 设置卡上的值是**线性**的(Blender 颜色属性),而导出会把它换算成显示值
    # 写进 .tscn(见 asset_export._env_color_text)—— 所以这里**直接用**:
    # Blender 显示 srgb(它) = Godot 显示那个换算后的值,两边对上。
    # 天空:导出器把 sky_top 设成背景色、地平线往白里提 0.35
    # (见 asset_export.SKY_MATERIAL_SUBRESOURCE)—— 预览拿那圈地平线色铺平。
    backdrop = (_mix_srgb(bg_color, (1.0, 1.0, 1.0), 0.35)
                if background == "SKY" else bg_color)
    return {
        "enabled": True,
        "tonemap": tonemap,
        "view_transform": TONEMAP_VIEW_TRANSFORMS[tonemap],
        "background": background,
        "backdrop": backdrop,
    }


def build_world(backdrop=None):
    """建/刷新那个"看得见、不照亮"的世界。返回世界对象。

    backdrop = **线性的世界底色**(env_display 已经算好换算;不给就用
    Godot 出厂灰 0.30 换算出来的线性值)。
    """
    if backdrop is None:
        grey = srgb_to_linear(GODOT_CLEAR_COLOR_SRGB[0])
        backdrop = (grey, grey, grey, 1.0)
    backdrop = _rgba(backdrop, (0.0, 0.0, 0.0, 1.0))
    world = bpy.data.worlds.get(WORLD_NAME)
    if world is None:
        world = bpy.data.worlds.new(WORLD_NAME)
    world.use_nodes = True

    tree = world.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)

    output = tree.nodes.new("ShaderNodeOutputWorld")
    output.location = (420, 0)

    # 看得见的底色(相机射线走这条)
    backdrop_node = tree.nodes.new("ShaderNodeBackground")
    backdrop_node.location = (100, 90)
    colour = tuple(backdrop[:3])
    backdrop_node.inputs["Color"].default_value = colour + (1.0,)
    backdrop_node.inputs["Strength"].default_value = 1.0

    # 照不亮的黑(GI / 漫反射射线走这条)——
    # Godot 默认没有环境光,所以非相机射线必须看到纯黑。
    shade = tree.nodes.new("ShaderNodeBackground")
    shade.location = (100, -110)
    shade.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    shade.inputs["Strength"].default_value = 1.0

    mix = tree.nodes.new("ShaderNodeMixShader")
    mix.location = (280, 0)

    path = tree.nodes.new("ShaderNodeLightPath")
    path.location = (-120, -220)

    # Fac: 0 -> 第一个输入,1 -> 第二个输入。
    # Is Camera Ray 为 1 时走灰底,否则走黑色(不参与照明)。
    tree.links.new(path.outputs["Is Camera Ray"], mix.inputs["Fac"])
    tree.links.new(shade.outputs["Background"], mix.inputs[1])
    tree.links.new(backdrop_node.outputs["Background"], mix.inputs[2])
    tree.links.new(mix.outputs["Shader"], output.inputs["Surface"])

    # Solid 模式(实体着色)的背景用的是 world.color,是另一个值
    world.color = colour
    return world


def apply(scene):
    """让这个场景的预览贴近 Godot(按『环境』设置卡对齐)。返回 (成功, 说明)。"""
    if scene is None:
        return False, "没有场景"

    _remember(scene)
    values = env_display(scene)
    notes = []

    # ---- 1. 色调映射:按设置卡上选的那个挑视图变换(第九十五轮)----
    view = scene.view_settings
    exact = "" if values["tonemap"] in TONEMAP_EXACT else "(近似)"
    try:
        view.view_transform = values["view_transform"]
        notes.append(f"视图变换 {values['view_transform']}"
                     f"(色调映射 {values['tonemap']}{exact})")
    except Exception:
        notes.append(f"⚠ 没能改视图变换,请手动设成 "
                     f"{values['view_transform']}")
    try:
        view.look = "None"
    except Exception:
        pass
    view.exposure = 0.0
    view.gamma = 1.0
    view.use_curve_mapping = False

    # ---- 2. 世界:设置卡上的背景色 + 零环境光 ----
    # 环境光**不加**:卡通材质是 unshaded,运行时也不吃环境光 —— 两边一致;
    # (关掉卡通材质用 PBR 时,暗部会有点差别,说明里写着)
    world = build_world(values["backdrop"])
    scene.world = world
    if values["background"] == "SKY":
        notes.append("世界用天空的地平线色铺平(真实是三段渐变,近似)")
    elif values["enabled"]:
        notes.append("世界换成设置卡上的背景色")
    else:
        notes.append("世界换成出厂平灰底(不参与照明)")

    # Solid 模式也跟着用世界背景,不然视口还是主题色
    shading = getattr(getattr(scene, "display", None), "shading", None)
    if shading is not None:
        try:
            shading.background_type = "WORLD"
        except Exception:
            pass

    # ---- 3. 关掉 Godot 没有的那些效果 ----
    eevee = getattr(scene, "eevee", None)
    if eevee is not None:
        for attr, label in (("use_raytracing", "光线追踪"),
                            ("use_gtao", "环境光遮蔽")):
            if hasattr(eevee, attr):
                try:
                    setattr(eevee, attr, False)
                    notes.append(f"关掉 {label}")
                except Exception:
                    pass

    # ---- 4. 说清"对不上的那些"(第九十五轮)----
    notes.append("雾 / 泛光 / 色调调整 / 描边 / 逐片元 SDF 预览里对不上")
    return True, "；".join(notes)


def restore(scene):
    """把 apply() 改过的东西放回去。返回 (成功, 说明)。"""
    if scene is None:
        return False, "没有场景"
    if not scene.get(_BACKUP["active"]):
        return False, "当前没有开启 Godot 观感,不需要还原"

    view = scene.view_settings
    try:
        saved = scene.get(_BACKUP["view_transform"])
        if saved:
            view.view_transform = saved
    except Exception:
        pass
    try:
        saved = scene.get(_BACKUP["look"])
        if saved:
            view.look = saved
    except Exception:
        pass
    view.exposure = float(scene.get(_BACKUP["exposure"], 0.0) or 0.0)
    view.gamma = float(scene.get(_BACKUP["gamma"], 1.0) or 1.0)

    world_name = scene.get(_BACKUP["world"], "")
    original = bpy.data.worlds.get(world_name) if world_name else None
    if original is not None:
        scene.world = original

    shading = getattr(getattr(scene, "display", None), "shading", None)
    if shading is not None:
        saved = scene.get(_BACKUP["background_type"])
        if saved:
            try:
                shading.background_type = saved
            except Exception:
                pass

    eevee = getattr(scene, "eevee", None)
    if eevee is not None:
        for attr, key in (("use_raytracing", "raytracing"),
                          ("use_gtao", "gtao"),
                          ("use_fast_gi", "fast_gi")):
            if hasattr(eevee, attr):
                try:
                    setattr(eevee, attr,
                            bool(scene.get(_BACKUP[key], False)))
                except Exception:
                    pass

    # 我们建的那个世界不用留着了
    ours = bpy.data.worlds.get(WORLD_NAME)
    if ours is not None and ours is not scene.world:
        try:
            bpy.data.worlds.remove(ours)
        except Exception:
            pass

    scene[_BACKUP["active"]] = False
    return True, "已还原成原来的观感"


def is_active(scene):
    """现在是不是处于"Godot 观感"。"""
    if scene is None:
        return False
    return bool(scene.get(_BACKUP["active"]))


def summary(scene=None):
    """给面板显示的一行摘要(按当前设置;第九十五轮起不是写死的)。"""
    values = env_display(scene) if scene is not None else None
    if values is None or not values["enabled"]:
        grey = GODOT_CLEAR_COLOR_SRGB[0]
        return (f"背景 {grey:.2f} 平灰 · 无声环境光 · "
                f"色调映射 Linear(与 Godot 默认一致)")
    exact = "" if values["tonemap"] in TONEMAP_EXACT else "(近似)"
    where = "天空(地平线色近似)" if values["background"] == "SKY" else "纯色"
    return (f"背景 {where} · 色调映射 {values['tonemap']}{exact} -> "
            f"视图变换 {values['view_transform']}")


def describe():
    """面板上显示的差异说明(每行一条)。"""
    return (
        "预览按『环境』设置卡对齐(没开设置卡 = Godot 出厂画面):",
        "· 色调映射:Linear -> Standard、AgX -> AgX 是精确的;"
        "Filmic / ACES / Reinhard 只能挑同族近似",
        "· 背景:纯色精确;天空用地平线色铺平(真实是三段渐变)",
        "· 环境光:卡通材质是 unshaded,运行时也不吃它(预览同样不加)",
        "· 对不上的:雾 / 泛光 / 色调调整(后处理)、描边、逐片元面部 SDF",
    )


def on_unregister():
    """清理:不让我们的世界在禁用插件后变成孤儿。"""
