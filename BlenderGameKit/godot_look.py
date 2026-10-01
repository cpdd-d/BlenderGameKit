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


def build_world():
    """建/刷新那个"只看得见、不照亮"的平灰世界。返回世界对象。"""
    world = bpy.data.worlds.get(WORLD_NAME)
    if world is None:
        world = bpy.data.worlds.new(WORLD_NAME)
    world.use_nodes = True

    tree = world.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)

    output = tree.nodes.new("ShaderNodeOutputWorld")
    output.location = (420, 0)

    # 看得见的灰底(相机射线走这条)
    backdrop = tree.nodes.new("ShaderNodeBackground")
    backdrop.location = (100, 90)
    grey = srgb_to_linear(GODOT_CLEAR_COLOR_SRGB[0])
    backdrop.inputs["Color"].default_value = (grey, grey, grey, 1.0)
    backdrop.inputs["Strength"].default_value = 1.0

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
    tree.links.new(backdrop.outputs["Background"], mix.inputs[2])
    tree.links.new(mix.outputs["Shader"], output.inputs["Surface"])

    # Solid 模式(实体着色)的背景用的是 world.color,是另一个值
    world.color = (grey, grey, grey)
    return world


def apply(scene):
    """让这个场景的预览贴近 Godot。返回 (成功, 说明)。"""
    if scene is None:
        return False, "没有场景"

    _remember(scene)
    notes = []

    # ---- 1. 色调映射:对齐 Godot 的 Linear ----
    view = scene.view_settings
    try:
        view.view_transform = "Standard"
        notes.append("视图变换 Standard(对应 Godot 的 Linear)")
    except Exception:
        notes.append("⚠ 没能改视图变换,请手动设成 Standard")
    try:
        view.look = "None"
    except Exception:
        pass
    view.exposure = 0.0
    view.gamma = 1.0
    view.use_curve_mapping = False

    # ---- 2. 世界:平灰底 + 零环境光 ----
    world = build_world()
    scene.world = world
    notes.append(f"世界换成平灰底(不参与照明)")

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
    """给面板显示的一行摘要。"""
    grey = GODOT_CLEAR_COLOR_SRGB[0]
    return (f"背景 {grey:.2f} 平灰 · 无声环境光 · "
            f"色调映射 Linear(与 Godot 默认一致)")


def describe():
    """面板上显示的差异说明(每行一条)。"""
    return (
        "Blender 和 Godot 的出厂画面差在三处:",
        "· 色调映射:Godot 默认 Linear,Blender 默认 AgX(颜色会变淡)",
        "· 背景:Godot 是 0.30 平灰",
        "· 环境光:Godot 默认没有,Blender 的世界会当灯用",
    )


def on_unregister():
    """清理:不让我们的世界在禁用插件后变成孤儿。"""
