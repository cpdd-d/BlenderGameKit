"""Blender preview of the exported Godot scene.

LINEAR tone mapping uses Standard plus a display-sRGB compositor bridge. Both
Environment adjustments and the connected post graph are applied, with signed
contrast, luminance saturation, multiplicative tint and screen-space masks.
Bloom, non-linear tone mapping, light falloff and per-light toon shading remain
approximations across renderers.

Lights use temporary per-object datablocks. Their colours follow the exporter's
sRGB convention, and range/shadow overrides follow the same source settings.
Saving/restoring removes preview data; exporting reads the authored data.
"""

import math

import bpy

# Godot 的默认清屏色(project.godot 里 default_clear_color 的默认值)。
#
# 注意这是 **sRGB** 值 —— 它是直接显示给眼睛的输出颜色。
# Blender 的 Background 节点要的是**线性**值,所以下面要转换,
# 否则背景会比 Godot 亮一大截(踩过这个:直接把 0.3 填进去,背景发白)。
GODOT_CLEAR_COLOR_SRGB = (0.3, 0.3, 0.3)

# 我们建的世界叫这个名。带 GK 前缀,方便识别和清理。
WORLD_NAME = "GK Godot 环境"

# ── 预览灯光亮度系数(第 124 轮:用户实测"预览的灯光太亮了")────────────
#
# 真机标定(恐怖示例『走廊』,同一机位 1152x648,帧均值亮度):
#     Godot 运行时                 0.3953   (灯开/关的贡献 0.1520)
#     Blender 应用『Godot 观感』后  0.9130   (灯开/关的贡献 0.6332)
# 而且亮的**几乎全是灯**:把世界强度调到 0 只让 0.9130 动到 0.9123 ——
# 环境光项在预览里几乎不起作用,所以要对症下药调灯,不是调环境光。
#
# ── 为什么必须给一个"系数",而不是把瓦数直接换算 ─────────────────
# 两套引擎的**光根本不是同一种单位**:
#   * Blender 点光按物理瓦数、1/r² 衰减:照度 = P / (4πr²);
#   * Godot 的 `light_energy` 是"按 `omni_range` 做距离衰减的倍率",
#     导出侧的换算约定是 **1000W ≈ 1.0**(见 asset_export._light_energy_for)。
# 解 1000 = 4πr²·衰减 得 r ≈ 9.4 米 —— 也就是说这个约定**只在离灯 9.4 米
# 那一个距离上成立**。灯离墙只有 2~3 米的房间里,预览自然亮十几倍。
# 距离相关的精确修法在这个"一个系数"的框架里做不到(要靠改 shader),
# 所以这里用**实测标定**的常数,并在 describe() 里如实写明是近似。
#
# 0.06 怎么来的:扫 0.04~0.2,取"整帧均值 + 灯的贡献"两项误差最小的一档
# (0.06 -> 均值 0.4070 / 贡献 0.1271,运行值 0.3953 / 0.1520)。
# ⚠ 别的房间(灯到墙的距离不同)最佳值会飘 —— 这是**近似**,不是精确对齐。
PREVIEW_LIGHT_SCALE = 0.06

# 调暗前每个灯自己的瓦数记在**灯物体自己身上**(而不是记在场景上):
# 一盏灯可能被多个场景共用,记在物体上还原才不会串。
_LIGHT_ENERGY_KEY = "gk_look_energy"
_LIGHT_SOURCE_KEY = "gk_look_light_source"
_LIGHT_COPY_TAG = "gk_look_light_copy"
_LIGHT_STATE_KEY = "gk_look_light_state"
_LIGHT_SCALE_KEY = "gk_look_light_scale"

# save_pre / save_post 钩子只装一次
_save_hooks_installed = False

# 预览用的雾盒子(第一百一十一轮)。同一个 GK 前缀规矩:能识别、能清理。
FOG_NAME = "GK Godot 雾"
FOG_MATERIAL_NAME = "GK Godot 雾材质"
# 盒子要把场景**包进去**再往外扩这么多米(相机飞出去就看不到雾了,
# 这是近似的一部分;外扩足够大时正常视角都在里面)
FOG_MARGIN = 40.0

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
TONEMAP_EXACT = ("LINEAR",)

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
    # 第 124 轮:预览用场景光照。这三项是 `scene.display.shading` 上的
    # (对 OpenGL / Workbench 渲染生效),交互视口的对齐走 `preview_shading_types`。
    "scene_lights": "gk_look_scene_lights",
    "show_shadows": "gk_look_show_shadows",
    "scene_world": "gk_look_scene_world",
    # 每个 VIEW_3D 视口切到 RENDERED 之前的原 `type`,存成字符串列表:
    # "窗口序号:区域序号=原type" 的竖线分隔串。无头模式(没有窗口)是空串。
    "preview_shading_types": "gk_look_preview_shading_types",
    # 第一百三十五轮:视口合成器(`space.shading.use_compositor`)——
    # 我们用它把『色调调整 / 泛光』画进预览。原来是什么就还原成什么。
    "use_compositor": "gk_look_use_compositor",
    # 场景合成器原来开没开(`scene.use_nodes`)。我们只在**它本来是关的**
    # (或者树里只有我们自己的节点)时才动手,还原时照原值放回去。
    "use_nodes": "gk_look_use_nodes",
    "curve_mapping": "gk_look_curve_mapping",
}

# ── 第一百三十五轮:预览也画『色调调整』与『泛光』(视口合成器)────────────
# 用户实测原话:"游戏环境与后期的节点图逻辑并未在游戏中生效" +
# "现在预览的画面与环境效果太差了,但运行时的却是不错的灯光与质感"。
# 排查下来是预览**根本没画**这两样 Godot 的后处理:
#   * 设置卡上的 亮度 / 对比度 / 饱和度 / 泛光(WorldEnvironment 的
#     `adjustment_*` / `glow_*`);
#   * 『游戏环境与后期』图里『环境_设置色调』(带『环境_屏幕位置』遮罩)
#     的色调值 —— 导出侧是"图里的卡覆盖设置卡",而预览只读设置卡,
#     于是用户在图里调色 = 预览一点不动 = "这图不生效"。
# 做法:场景合成器里搭一串**GK 前缀**的节点(RLayers → 亮度乘 →
# 对比度 → 饱和度 → 色调混 → [区域遮罩] → Glare(BLOOM) → Composite),
# 并把每个 VIEW_3D 视口的 `shading.use_compositor` 切成 `ALWAYS`。
# 精度写在 describe() 里:泛光用 BLOOM 近似(形状 / 迭代次数不同)、
# 亮度用乘法、对比度 / 饱和度用同族节点 —— 都是**近似**。
POST_PREFIX = "GK 后期"
POST_TREE_TAG = "gk_look_post"


def _view3d_spaces():
    """当前所有 VIEW_3D 视口的 shading(后台模式返回空列表)。

    ⚠ 和 `_apply_viewport_scene_lights` 同一个规矩:**后台模式跳过** ——
    示例重建(`-b`)时切视口状态只会被写进保存的 .blend。
    """
    if getattr(bpy.app, "background", False):
        return []
    windows = getattr(bpy.context, "window_manager", None)
    if windows is None:
        return []
    found = []
    try:
        for window in windows.windows:
            screen = getattr(window, "screen", None)
            if screen is None:
                continue
            for area in screen.areas:
                if getattr(area, "type", "") == "VIEW_3D":
                    found.append(area.spaces.active)
    except Exception:                                      # noqa: BLE001
        return []
    return found


def _env_tree():
    """当前文件的『游戏环境与后期』图(没有就 None)。"""
    try:
        groups = list(bpy.data.node_groups)
    except Exception:                                      # noqa: BLE001
        return None
    for group in groups:
        if getattr(group, "bl_idname", "") == "GKEnvLibraryTree":
            return group
    return None


def _env_card(kind):
    """『游戏环境与后期』图里的某类卡(第一张;没有就 None)。"""
    tree = _env_tree()
    for node in getattr(tree, "nodes", ()) or ():
        if getattr(node, "bl_idname", "") == kind:
            return node
    return None


def _grade_mask(grade_node):
    """『环境_设置色调』的『屏幕位置』引脚接的是哪张卡(没接返回 None)。"""
    inputs = getattr(grade_node, "inputs", None)
    socket = inputs.get("屏幕位置") if hasattr(inputs, "get") else None
    if socket is None or not getattr(socket, "is_linked", False):
        return None
    from .codegen import graph as _graph
    try:
        link = _graph.upstream(socket)
    except ValueError:
        return None
    source = getattr(link, "from_node", None) if link is not None else None
    if getattr(source, "bl_idname", "") == "GKNodeScreenPos":
        return source
    return None


def post_values(scene, values=None):
    """Read both exported post stages, following the exporter's execution chain."""
    from . import asset_export
    from .godot_post import number
    values = values if values is not None else env_display(scene)
    settings = getattr(scene, "gk_settings", None)
    enabled = bool(values.get("enabled", False))
    post = {
        "grade_on": False, "brightness": 1.0, "contrast": 1.0, "saturation": 1.0,
        "tint": (1.0, 1.0, 1.0), "tint_amount": 0.0,
        "mask_on": False, "mask_round": True, "mask_invert": False,
        "mask_center": (.5, .5), "mask_radius": .5, "mask_size": (.5, .4),
        "mask_feather": .25,
        "env_adjust_on": enabled and bool(getattr(settings, "env_adjust", False)),
        "env_brightness": number(settings, "env_brightness", 1.0),
        "env_contrast": number(settings, "env_contrast", 1.0),
        "env_saturation": number(settings, "env_saturation", 1.0),
        "glow_on": enabled and bool(getattr(settings, "env_glow", False)),
        "glow_intensity": number(settings, "env_glow_intensity", .6),
        "glow_bloom": number(settings, "env_glow_bloom", .06),
        "glow_threshold": number(settings, "env_glow_threshold", 1.0),
        "glow_strength": number(settings, "env_glow_strength", 1.0),
        "glow_blend": str(getattr(settings, "env_glow_blend", "SCREEN")),
        "display_space": values.get("tonemap", "LINEAR") == "LINEAR",
    }
    # The generated project uses Godot's default LDR canvas. A manually enabled
    # HDR canvas uses linear values; do not apply the LDR transfer in that case.
    try:
        from . import project
        import os
        import re
        directory, _ = project.resolve(scene, adopt=False)
        if directory:
            with open(os.path.join(directory, "project.godot"), encoding="utf-8") as stream:
                config = stream.read()
            if re.search(r"(?m)^\s*viewport/hdr_2d\s*=\s*true\s*$", config):
                post["display_space"] = False
    except (OSError, AttributeError, TypeError):
        pass
    grade = next((node for node in asset_export._post_chain()
                  if getattr(node, "bl_idname", "") == "GKNodeEnvGrade"), None)
    if grade is not None:
        post["grade_on"] = True
        for name in ("brightness", "contrast", "saturation"):
            post[name] = number(grade, "gk_" + name, 1.0)
        post["tint"] = _rgba(getattr(grade, "gk_tint", None), (1, 1, 1, 1))[:3]
        post["tint_amount"] = number(grade, "gk_tint_amount", 0.0)
        mask = asset_export._post_mask_of(grade)
        if mask is not None:
            post["mask_on"] = True
            post["mask_round"] = bool(getattr(mask, "gk_round", True))
            post["mask_invert"] = bool(getattr(mask, "gk_invert", False))
            post["mask_center"] = tuple(getattr(mask, "gk_center", (.5, .5)))[:2]
            post["mask_size"] = tuple(getattr(mask, "gk_size", (.5, .4)))[:2]
            post["mask_radius"] = number(mask, "gk_radius", .5)
            post["mask_feather"] = number(mask, "gk_feather", .25)
    return post


def _post_nodes(tree):
    return [node for node in getattr(tree, "nodes", ()) or ()
            if str(getattr(node, "name", "")).startswith(POST_PREFIX)]


def _post_node(tree, kind, name):
    node = tree.nodes.new(kind)
    node.name = f"{POST_PREFIX} {name}"
    node.label = name
    return node


def _post_reuse(tree, kind, name):
    """树里已经有这一类**不在我们名下**的节点就复用它(Blender 的默认
    RLayers / Composite 一对就属于这种),没有才新建。

    为什么不直接清空:场景合成器的默认那对节点是 Blender 自己放的,
    清掉会动到"用户以后打开合成器"看到的初始状态 —— 能复用就复用。
    """
    for node in getattr(tree, "nodes", ()) or ():
        if node.bl_idname == kind and node not in _post_nodes(tree):
            return node
    return _post_node(tree, kind, name)


_POST_LINKS_KEY = "gk_look_post_links"


def build_post(scene, post):
    """Install the preview pipeline without taking over a user's compositor."""
    import json
    from .godot_post import pipeline
    if not (post.get("grade_on") or post.get("glow_on") or post.get("env_adjust_on")):
        return "none"
    initial_use_nodes = bool(scene.use_nodes)
    tree = getattr(scene, "node_tree", None)
    if tree is None:
        scene.use_nodes = True
        tree = scene.node_tree
    ours = set(_post_nodes(tree))
    linked = {node for link in tree.links for node in (link.from_node, link.to_node)}
    foreign = [node for node in linked if node not in ours
               and node.bl_idname not in ("CompositorNodeRLayers", "CompositorNodeComposite",
                                           "NodeReroute", "CompositorNodeViewer")]
    renders = [node for node in tree.nodes if node.bl_idname == "CompositorNodeRLayers"]
    composites = [node for node in tree.nodes if node.bl_idname == "CompositorNodeComposite"]
    # Only take over a single scene's Image route. Multiple render layers or
    # alternate passes/scenes are user compositing even without color effects.
    custom_source = (len(renders) > 1 or len(composites) > 1
                     or any(node.scene is not None and node.scene != scene for node in renders)
                     or any(link.from_node not in ours and link.to_node not in ours
                            and ((link.from_node.bl_idname == "CompositorNodeRLayers"
                                  and link.from_socket.name != "Image")
                                 or (link.to_node.bl_idname in ("CompositorNodeComposite", "CompositorNodeViewer")
                                     and link.to_socket.name != "Image")) for link in tree.links))
    if foreign or custom_source:
        # Creating a tree can enable compositing. A rejected preview must leave
        # that switch alone, including on Blender versions with richer defaults.
        scene.use_nodes = initial_use_nodes
        return "busy"
    # Capture the original default links once; repeated refreshes must never
    # replace the original snapshot with our temporary chain.
    if _POST_LINKS_KEY not in scene:
        links = [[link.from_node.name, list(link.from_node.outputs).index(link.from_socket),
                  link.to_node.name, list(link.to_node.inputs).index(link.to_socket)]
                 for link in tree.links if link.from_node not in ours and link.to_node not in ours]
        if not links and ours:
            # Upgrade older saved previews whose original default wire was lost.
            renders = [n for n in tree.nodes if n.bl_idname == "CompositorNodeRLayers" and n not in ours]
            outputs = [n for n in tree.nodes if n.bl_idname == "CompositorNodeComposite" and n not in ours]
            if len(renders) == len(outputs) == 1:
                links = [[renders[0].name, 0, outputs[0].name, 0]]
        scene[_POST_LINKS_KEY] = json.dumps(links)
    scene.use_nodes = True
    for node in list(ours):
        tree.nodes.remove(node)
    render = _post_reuse(tree, "CompositorNodeRLayers", "渲染层")
    tail = pipeline(scene, tree, render.outputs["Image"], post, _post_node)
    composite = _post_reuse(tree, "CompositorNodeComposite", "输出")
    tree.links.new(tail, composite.inputs["Image"])
    for space in _view3d_spaces():
        shading = getattr(space, "shading", None)
        if shading is not None and hasattr(shading, "use_compositor"):
            shading.use_compositor = "ALWAYS"
    scene[POST_TREE_TAG] = True
    return ""


def clear_post(scene):
    """Remove our nodes and reconnect the saved default compositor output."""
    import json
    tree = getattr(scene, "node_tree", None)
    owned = bool(scene.get(POST_TREE_TAG)) or (tree is not None and bool(_post_nodes(tree)))
    if not owned:
        return
    if tree is not None:
        for node in _post_nodes(tree):
            tree.nodes.remove(node)
        try:
            saved_links = json.loads(scene.get(_POST_LINKS_KEY, "[]"))
        except (ValueError, TypeError):
            saved_links = []
        for source_name, output_index, target_name, input_index in saved_links:
            source, target = tree.nodes.get(source_name), tree.nodes.get(target_name)
            if source is None or target is None:
                continue
            if output_index >= len(source.outputs) or input_index >= len(target.inputs):
                continue
            destination = target.inputs[input_index]
            if not destination.is_linked:
                tree.links.new(source.outputs[output_index], destination)
    scene[POST_TREE_TAG] = False
    if _POST_LINKS_KEY in scene:
        del scene[_POST_LINKS_KEY]
    scene.use_nodes = bool(scene.get(_BACKUP["use_nodes"], False))
    saved = scene.get(_BACKUP["use_compositor"])
    for space in _view3d_spaces():
        shading = getattr(space, "shading", None)
        if shading is not None and hasattr(shading, "use_compositor"):
            shading.use_compositor = str(saved) if saved else "DISABLED"



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
        # ── 已经记过 —— 但**新版本可能补了新的备份键**,缺哪个补哪个 ────
        # 第 124 轮踩到的坑:示例 `.blend` 是**上一版插件**保存的,里面
        # `gk_look_active = True`(构建时 apply 过),却没有新增的
        # `gk_look_preview_shading_types`。于是 `apply()` 把视口切成
        # RENDERED 之后,`restore()` 找不到原 type —— 视口**永远回不去**
        # (真机 GUI 实测:restore 之后还是 RENDERED)。
        # 所以老备份不完整时,只补**缺的那些键**,不覆盖已有的(已有的
        # 才是真正的"原始值")。
        _remember_missing(scene)
        return
    view = scene.view_settings
    shading = getattr(getattr(scene, "display", None), "shading", None)
    eevee = getattr(scene, "eevee", None)
    world = scene.world

    scene[_BACKUP["view_transform"]] = str(view.view_transform)
    scene[_BACKUP["look"]] = str(view.look)
    scene[_BACKUP["exposure"]] = float(view.exposure)
    scene[_BACKUP["gamma"]] = float(view.gamma)
    scene[_BACKUP["curve_mapping"]] = bool(view.use_curve_mapping)
    scene[_BACKUP["world"]] = world.name if world is not None else ""
    scene[_BACKUP["background_type"]] = str(
        getattr(shading, "background_type", "THEME"))
    scene[_BACKUP["raytracing"]] = bool(getattr(eevee, "use_raytracing", False))
    scene[_BACKUP["gtao"]] = bool(getattr(eevee, "use_gtao", False))
    scene[_BACKUP["fast_gi"]] = bool(getattr(eevee, "use_fast_gi", False))
    scene[_BACKUP["scene_lights"]] = bool(
        getattr(shading, "use_scene_lights", False))
    scene[_BACKUP["show_shadows"]] = bool(
        getattr(shading, "show_shadows", False))
    scene[_BACKUP["scene_world"]] = bool(
        getattr(shading, "use_scene_world", False))
    scene[_BACKUP["preview_shading_types"]] = _viewport_shading_snapshot()
    # 第一百三十五轮:合成器开关的原值(视口 + 场景)。
    spaces = _view3d_spaces()
    scene[_BACKUP["use_compositor"]] = (
        str(getattr(spaces[0].shading, "use_compositor", "DISABLED"))
        if spaces else "DISABLED")
    scene[_BACKUP["use_nodes"]] = bool(scene.use_nodes)
    scene[_BACKUP["active"]] = True


def _remember_missing(scene):
    """补齐缺失的备份键(只补缺的,不碰已有的)。

    给"上一版插件存下来的 .blend"用:它 active=True 但可能没有后来新增的
    备份项。缺视口快照就直接现采一份 —— 此刻视口还没被我们改过,
    采到的就是真原值。

    ⚠ 快照是**空串**也算"缺":那说明它是在**后台模式**下采的
      (`_viewport_shading_snapshot` 在 `-b` 里返回空),而没有窗口时压根采不到
      真实视口。交互会话里第一次 apply 时要把它补上,否则这一次还原不了。
    """
    if _BACKUP["curve_mapping"] not in scene:
        scene[_BACKUP["curve_mapping"]] = bool(scene.view_settings.use_curve_mapping)
    if _BACKUP["use_nodes"] not in scene:
        scene[_BACKUP["use_nodes"]] = bool(scene.use_nodes)
    if not scene.get(_BACKUP["preview_shading_types"], ""):
        fresh = _viewport_shading_snapshot()
        if fresh:
            scene[_BACKUP["preview_shading_types"]] = fresh
    for key, attr, fallback in (
            ("scene_lights", "use_scene_lights", False),
            ("show_shadows", "show_shadows", False),
            ("scene_world", "use_scene_world", False)):
        if _BACKUP[key] in scene:
            continue
        shading = getattr(getattr(scene, "display", None), "shading", None)
        scene[_BACKUP[key]] = bool(getattr(shading, attr, fallback))


def _viewport_shading_snapshot():
    """把所有 VIEW_3D 视口的 `shading.type` 记成一条可还原的字符串。

    ── 为什么单独一个函数(第 124 轮)──────────────────────────────
    交互视口的 shading 挂在 `space.shading`(每个视口一个),和
    `scene.display.shading`(全场景一个、只给 OpenGL/Workbench 渲染用)是**两个
    不同的 datablock**。要"预览用上场景真实光照",只能把视口的 `type` 切成
    RENDERED(MATERIAL 也行,但 RENDERED 连体积雾都能显示,最接近 Godot);
    SOLID 模式下 `use_scene_lights` 那三个开关对交互视口是**无效的**(真机
    截图逐项隔离验证过)。所以:快照记住每个视口的原 type,还原时逐个放回去。

    ⚠ 无头模式的判据是 **`bpy.app.background`**,不是"有没有窗口":
      实测 `blender -b` 下 `window_manager.windows` **仍然是 1**(而且带着一个
      VIEW_3D 区)。原来按"没有窗口就跳过"写,结果示例重建时第一个场景把那个
      文档级视口切成 RENDERED,后面两个场景再采快照,采到的"原值"就变成了
      RENDERED —— 保存进 .blend 之后,还原永远回不到 SOLID(真机踩到)。
      后台模式压根没有"用户正在看的视口",这类对齐就不该做。
    """
    if getattr(bpy.app, "background", False):
        return ""
    windows = getattr(bpy.context, "window_manager", None)
    if windows is None:
        return ""
    parts = []
    try:
        for wi, window in enumerate(windows.windows):
            for ai, area in enumerate(getattr(window, "screen", None).areas
                                      if getattr(window, "screen", None) else ()):
                if getattr(area, "type", "") != "VIEW_3D":
                    continue
                space = area.spaces.active
                parts.append(f"{wi}:{ai}={space.shading.type}")
    except Exception:                                      # noqa: BLE001
        return ""
    return "|".join(parts)


def _apply_viewport_scene_lights():
    """把每个 VIEW_3D 视口切成 RENDERED(预览用真实场景光照)。

    只在 `preview_scene_lights` 开着时调用(由 apply 判断)。
    ⚠ 后台模式(`bpy.app.background`,示例重建走的就是这个)直接跳过:
      见 `_viewport_shading_snapshot` 的说明 —— 后台也有窗口,但那是文档级的,
      没有"用户正在看的视口",切它只会把状态写进保存的 .blend。
    """
    if getattr(bpy.app, "background", False):
        return
    windows = getattr(bpy.context, "window_manager", None)
    if windows is None:
        return
    try:
        for window in windows.windows:
            screen = getattr(window, "screen", None)
            if screen is None:
                continue
            for area in screen.areas:
                if getattr(area, "type", "") != "VIEW_3D":
                    continue
                area.spaces.active.shading.type = "RENDERED"
    except Exception:                                      # noqa: BLE001
        pass


def _restore_viewport_shading(scene):
    """把 _apply_viewport_scene_lights 切过的视口 type 逐个还原。"""
    saved = scene.get(_BACKUP["preview_shading_types"], "")
    if not saved:
        return
    if getattr(bpy.app, "background", False):
        return
    windows = getattr(bpy.context, "window_manager", None)
    if windows is None:
        return
    index = {}
    for item in saved.split("|"):
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        if ":" in key:
            index[key] = value
    try:
        for wi, window in enumerate(windows.windows):
            screen = getattr(window, "screen", None)
            if screen is None:
                continue
            for ai, area in enumerate(screen.areas):
                if getattr(area, "type", "") != "VIEW_3D":
                    continue
                original = index.get(f"{wi}:{ai}")
                if original:
                    area.spaces.active.shading.type = original
    except Exception:                                      # noqa: BLE001
        pass


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


def _exposure_stops(multiplier):
    """Godot 的 `tonemap_exposure`(倍率)-> Blender 的 exposure(档位)。

    ── 标定(第 124 轮,真机两边都跑过渲染对比,不是猜)──────────────
    Godot:`tonemap_exposure` 是**倍率**,输出像素 = srgb(线性值 × 倍率),
      倍率 1.0 时确实 = 不变(实测比例 1.0016~1.0045,量化误差内)。
    Blender:`view_settings.exposure` 是**档位**,输出 = srgb(线性值 × 2^exposure),
      实测 exposure ∈ {-1,0,0.5,1,2} 全部落在理论曲线 ratio=1.0000±2e-4。
    两边都是"线性值乘一个系数再转 sRGB",所以让 2^stops == multiplier,
    即 stops = log2(multiplier)。倍率 1.0 -> 0.0 档,天然"不变"。
    """
    if multiplier <= 0.0:
        return 0.0
    return math.log2(multiplier)


def _preview_uses_scene_lights(scene):
    """是否把视口切到 RENDERED 来对齐场景光照(第 124 轮)。

    读 `gk_settings.preview_scene_lights`(取不到就默认 True)。这个字段是
    Lead 加在 properties.py 的,预览这边只读、不改。
    """
    settings = getattr(scene, "gk_settings", None)
    return bool(getattr(settings, "preview_scene_lights", True))


def _scene_lights(scene):
    """这个场景里所有会被导出的灯(类型 / 能量都合法的)。"""
    out = []
    for obj in getattr(scene, "objects", ()) or ():
        if getattr(obj, "type", "") != "LIGHT":
            continue
        if str(getattr(getattr(obj, "data", None), "type", "")) not in (
                "SUN", "POINT", "SPOT"):
            continue
        out.append(obj)
    return out


def _preview_light_scale(scene):
    """这个场景的预览灯光倍率(读者 = 『Godot 观感』卡上的滑块)。

    默认值就是实测标定出来的 `PREVIEW_LIGHT_SCALE`(0.06)。用户把它拧到
    别的值就按他的来 —— 标定值是"一个场景一个机位"量出来的,不是普适真理,
    所以最终解释权交给用户(他原话:"这样就可以自己来精确的调")。
    """
    settings = getattr(scene, "gk_settings", None)
    try:
        value = float(getattr(settings, "preview_light_scale",
                              PREVIEW_LIGHT_SCALE))
    except (TypeError, ValueError):
        return PREVIEW_LIGHT_SCALE
    # 0 或负数会让灯全灭(预览变全黑),夹到一个有意义的下限
    return min(2.0, max(0.01, value))


def _reset_lights(scene):
    """按当前倍率重算:先还原成原瓦数,再按新倍率乘。

    ⚠ 不能省掉 `_undim_lights` —— 直接在"已乘过"的值上再乘会越调越暗
      (拧三次 0.5 = 1/8)。滑块每次改动都会调到这里。
    """
    _undim_lights(scene)
    if is_active(scene):
        _dim_lights(scene)


def _preview_energy(obj, authored, scale):
    """这盏灯在预览里该用多少瓦(第一百二十六轮)。

    ── 用户第 10 轮的原话与这一行的关系 ────────────────────────────
    "灯光还会受到 blender 的原生灯光设置影响...不行的话,就在开启贴近
     godot 观感后就禁用原生灯光设置,转而在游戏环境与后期的侧边栏添加
     灯光设置。"

    灯打开了『用这里的值导出』之后,亮度的**权威**就是 `gk_light.energy`
    (Godot 的 `light_energy` 原值)—— 原生面板的瓦数**不再参与预览**,
    用户改瓦数不会让预览变样。换算走导出侧那条约定的**逆**:
        点光/聚光:Blender 瓦数 = 能量 × 1000
        太阳      :Blender 辐照度 = 能量 ÷ 1.2
    再乘预览标定系数(见 PREVIEW_LIGHT_SCALE)。
    没打开的就是老行为:原生瓦数 × 系数。

    ── 逐灯补偿(第一百三十八轮,用户要求)──────────────────────────
    用户原话:"添加类似 blender 灯光原生的设置来优化预览与运行的差距"。
    场景级那个倍率是**一个数管所有灯**(0.06 是实测标定),但每盏灯的偏差
    并不一样 —— 所以这里再乘一次 `gk_light.preview_scale`(默认 1.0):
    **只影响预览,导出产物一个字不变**。
    """
    settings = getattr(obj, "gk_light", None)
    per_light = float(getattr(settings, "preview_scale", 1.0) or 0.0) \
        if settings is not None else 1.0
    if settings is None or not bool(getattr(settings, "override", False)):
        return authored * scale * per_light
    energy = float(getattr(settings, "energy", 1.0) or 0.0)
    if str(getattr(getattr(obj, "data", None), "type", "")) == "SUN":
        return (energy / 1.2) * scale * per_light
    return energy * 1000.0 * scale * per_light


_LIGHT_NATIVE_FIELDS = (
    "type", "energy", "color", "use_shadow", "use_custom_distance", "cutoff_distance",
    "spot_size", "spot_blend", "shadow_soft_size", "angle", "diffuse_factor",
    "specular_factor", "volume_factor", "shape", "size", "size_y", "normalize", "exposure",
)


def _light_state(data):
    """Values the native Light panel can edit on our temporary datablock."""
    state = {}
    for name in _LIGHT_NATIVE_FIELDS:
        if hasattr(data, name):
            value = getattr(data, name)
            state[name] = list(value) if name == "color" else value
    return state


def _forget_light_preview(obj):
    for key in (_LIGHT_SOURCE_KEY, _LIGHT_ENERGY_KEY, _LIGHT_STATE_KEY, _LIGHT_SCALE_KEY):
        if key in obj:
            del obj[key]


def _cleanup_light_copies():
    for data in list(bpy.data.lights):
        if data.get(_LIGHT_COPY_TAG) and data.users == 0:
            bpy.data.lights.remove(data)


def _capture_light_edits(obj):
    """Keep native-panel edits before refresh/save/export, without recursion.

    Compare with our last applied values, never with the source: another linked
    object's edit can change the source while this object's preview is unchanged.
    Overrides remain authoritative for rendering, but a deliberate native edit
    is retained in the authored data for when the override is disabled.
    """
    source = obj.get(_LIGHT_SOURCE_KEY) if hasattr(obj, "get") else None
    preview = getattr(obj, "data", None)
    if source is None:
        return preview
    if preview is source or not preview.get(_LIGHT_COPY_TAG):
        # A new datablock chosen by the user is authored data, not an edit to
        # our preview. Never replace it with the old source on restore.
        _forget_light_preview(obj)
        _cleanup_light_copies()
        return preview
    import json
    try:
        previous = json.loads(obj.get(_LIGHT_STATE_KEY, "{}"))
    except (TypeError, ValueError):
        previous = {}
    current = _light_state(preview)
    type_changed = previous.get("type", current["type"]) != current["type"]
    for name, value in current.items():
        changed = name in previous and value != previous[name]
        if not changed and not (type_changed and name not in previous):
            continue
        if name == "energy":
            scale = float(obj.get(_LIGHT_SCALE_KEY, 1.0))
            value = float(value) / scale if scale > 1e-12 else float(value)
        elif name == "color":
            value = tuple(linear_to_srgb(component) for component in value)
        try:
            setattr(source, name, value)
            if name == "type":
                # Changing type replaces the RNA subtype wrapper. Re-fetch it
                # before assigning fields that only exist on the new type.
                source = bpy.data.lights[source.name]
        except (AttributeError, TypeError, ValueError):
            pass
    obj[_LIGHT_ENERGY_KEY] = float(source.energy)
    obj[_LIGHT_STATE_KEY] = json.dumps(current)
    return source


def _capture_scene_light_edits(scene):
    # Include AREA and detached/reassigned data so a type change cannot evade
    # cleanup just because it is no longer a supported exported light.
    for obj in getattr(scene, "objects", ()):
        if getattr(obj, "type", "") == "LIGHT":
            _capture_light_edits(obj)


def authored_light_data(obj):
    """Return authored data, first retaining native edits to a preview copy."""
    return _capture_light_edits(obj)


def _dim_lights(scene, *, force=False):
    """把预览里的灯调暗到和 Godot 观感一致(倍率见 `_preview_light_scale`)。

    三件事每件都不能少:
      1. **只在交互会话里做**(`bpy.app.background` 为真时直接返回)——
         示例是用 `blender -b` 重建的,那时调暗会被存进 .blend,下一次导出
         就把 9000W 写成 540W(灯暗 16 倍),这是**会毁掉产物**的路径。
      2. 原值记在**灯物体自己**身上,第二次 apply 不覆盖(否则记下来的是
         已经调暗过的值,还原会越还原越暗)。
      3. 装 save_pre / save_post / load_post 钩子:Ctrl+S 落盘前先把瓦数放
         回去(存完再调暗回来),重新打开文件后按"预览还开着"补一次调暗 ——
         普通 handler 在文件读取时会被清掉,所以必须 persistent(见 _on_load)。
    """
    if getattr(bpy.app, "background", False) and not force:
        return
    import json
    _capture_scene_light_edits(scene)
    scale = _preview_light_scale(scene)
    for obj in getattr(scene, "objects", ()):
        if getattr(obj, "type", "") != "LIGHT":
            continue
        source = authored_light_data(obj)
        if source.type not in {"SUN", "POINT", "SPOT"}:
            _restore_light_object(obj)
            continue
        if _LIGHT_SOURCE_KEY not in obj and _LIGHT_ENERGY_KEY in obj:
            # Migrate lights dimmed in place by an earlier plugin version.
            source.energy = float(obj[_LIGHT_ENERGY_KEY])
        if _LIGHT_SOURCE_KEY in obj:
            obj[_LIGHT_ENERGY_KEY] = float(source.energy)
        if _LIGHT_ENERGY_KEY not in obj:
            obj[_LIGHT_ENERGY_KEY] = float(source.energy)
        if _LIGHT_SOURCE_KEY not in obj or obj.data.users > 1:
            # Separate linked light data before applying per-object overrides.
            # Save/export still see the original colour, range and shadow state.
            obj[_LIGHT_SOURCE_KEY] = source
            preview = source.copy()
            preview.name = "GK 预览灯 · " + obj.name
            preview[_LIGHT_COPY_TAG] = True
            preview.use_fake_user = False
            obj.data = preview
        obj.data.type = source.type
        override = getattr(obj, "gk_light", None)
        enabled = bool(getattr(override, "override", False))
        colour = (override.color if enabled and override.use_color else source.color)
        # The exporter writes light_color verbatim. Godot interprets that value
        # as sRGB; EEVEE's Light.color is linear (verified with RGB render probes).
        obj.data.color = tuple(srgb_to_linear(float(v)) for v in colour[:3])
        obj.data.use_shadow = bool(override.shadow if enabled else source.use_shadow)
        for name in ("spot_size", "spot_blend", "shadow_soft_size", "angle",
                     "diffuse_factor", "specular_factor", "volume_factor"):
            if hasattr(source, name):
                setattr(obj.data, name, getattr(source, name))
        if source.type in {"POINT", "SPOT"}:
            distance = (float(override.range_m) if enabled and override.range_m > 0
                        else float(source.cutoff_distance))
            obj.data.use_custom_distance = True
            obj.data.cutoff_distance = distance if distance > 0 else 5.0
        obj.data.energy = _preview_energy(obj, float(obj[_LIGHT_ENERGY_KEY]),
                                          scale)
        obj[_LIGHT_SCALE_KEY] = scale * float(getattr(override, "preview_scale", 1.0))
        obj[_LIGHT_STATE_KEY] = json.dumps(_light_state(obj.data))
    _cleanup_light_copies()
    _install_save_hooks()


def _restore_light_object(obj):
    _capture_light_edits(obj)
    source = obj.get(_LIGHT_SOURCE_KEY)
    if isinstance(source, bpy.types.Light):
        obj.data = source
        _forget_light_preview(obj)
    elif _LIGHT_ENERGY_KEY in obj:
        try:
            obj.data.energy = float(obj[_LIGHT_ENERGY_KEY])
            _forget_light_preview(obj)
        except Exception:                                  # noqa: BLE001
            pass


def _undim_lights(scene):
    """Restore all lights, including those changed to AREA while previewing."""
    for obj in getattr(scene, "objects", ()):
        if getattr(obj, "type", "") == "LIGHT":
            _restore_light_object(obj)
    _cleanup_light_copies()


def _undim_all(*_args):
    """所有场景的灯都放回去(给 save_pre 钩子用)。

    ⚠ 必须收 `*_args`:Blender 调 save_pre / save_post 时会**带参数**
      (实测传了 2 个),签名写成无参就是
      `TypeError: _undim_all() takes 0 positional arguments but 2 were given`
      —— 钩子**静默失败**(只在控制台报一行),于是调暗后的 540W 被真的写进
      .blend,下一次导出灯就暗了 16 倍。这条路径实测踩过,签名别再改回去。
    """
    from . import godot_material_preview
    godot_material_preview.restore_all()
    for scene in bpy.data.scenes:
        _undim_lights(scene)
        # 后期节点(第一百三十五轮)也一起收走:它们同样是**内存里的临时状态**,
        # 存进 .blend 就成了"用户打开示例看见一串 GK 后期节点"
        # (save_post 会把它们再搭回来,见 _repost_all)。
        try:
            clear_post(scene)
        except Exception:                                  # noqa: BLE001
            pass


def _redim_all(*_args):
    """存完再把预览调暗回去(给 save_post 钩子用)。同上:必须收 `*_args`。"""
    for scene in bpy.data.scenes:
        if is_active(scene):
            _dim_lights(scene)
            if not bpy.app.background:
                from . import godot_material_preview
                godot_material_preview.apply(scene)
        # 后期节点也重新搭起来(存盘前收走过,见 _undim_all)
        try:
            if is_active(scene):
                build_post(scene, post_values(scene))
        except Exception:                                  # noqa: BLE001
            pass


def _on_load(*_args):
    """打开文件之后:重新调暗还开着预览的场景(第 124 轮)。

    ── 为什么必须挂 load_post(用户实测)─────────────────────────────
    调暗是**内存里的临时状态**,存盘时会被还原 —— 所以**重新打开 .blend**
    之后灯又变回原瓦数了。这时 `gk_look_active` 还是 True(面板上看起来是
    "已开启:贴近 Godot"),但灯是亮的 —— 用户看到的正是
    "明明开了开关,预览还是太亮"。

    ⚠⚠ 必须用 `bpy.app.handlers.persistent` 标记 ⚠⚠
    真机量到:普通注册的 handler 在 `open_mainfile()` 时会被**整批清掉**,
    而且清在**触发之前** —— 实测 `load_post` 里的自定义探针一次都没被调用,
    打开文件后连 `_redim_all` 都不在列表里了(长度从 5 掉回去)。
    普通 handler 会随文件读取重置,只有标记 persistent 的才会被保留。
    所以这里既标记 persistent,又顺手把三个钩子重装一遍(文件读取会清空,
    模块级标志却还在 —— 只靠标志判断会漏装)。
    """
    _install_save_hooks()
    _redim_all()


def _install_save_hooks():
    """装"存盘前后"和"打开文件后"的钩子(幂等,可重复调用)。"""
    global _save_hooks_installed
    try:
        mark = bpy.app.handlers.persistent
    except AttributeError:                                 # 老版本没有这个 API
        mark = None
    for pool, handler in ((bpy.app.handlers.save_pre, _undim_all),
                          (bpy.app.handlers.save_post, _redim_all),
                          (bpy.app.handlers.load_post, _on_load)):
        try:
            entry = mark(handler) if mark is not None else handler
            if entry not in pool:
                pool.append(entry)
        except Exception:                                  # noqa: BLE001
            pass
    _save_hooks_installed = True


def _remove_save_hooks():
    """卸载时把钩子摘掉(别留一个指向已卸载模块的函数)。"""
    global _save_hooks_installed
    for handler in (_undim_all, _redim_all, _on_load):
        for pool in (bpy.app.handlers.save_pre, bpy.app.handlers.save_post,
                     bpy.app.handlers.load_post):
            try:
                if handler in pool:
                    pool.remove(handler)
            except Exception:                              # noqa: BLE001
                pass
    _save_hooks_installed = False


def on_register():
    """插件注册时就把钩子挂上(第 124 轮)。

    不能等 apply() 再挂:重新打开 .blend 时预览还是"开着的",要有人把
    调暗补回去(见 `_on_load` 的说明)。
    """
    _install_save_hooks()
    _install_env_watch()


def _install_env_watch():
    """挂上"设置一改就重刷预览"的定时器(幂等;第一百三十七轮)。"""
    try:
        if not bpy.app.timers.is_registered(_watch_env_changes):
            # persistent:重新打开文件后还在(和 load_post 钩子同一个理由)
            bpy.app.timers.register(_watch_env_changes, persistent=True,
                                    first_interval=_ENV_WATCH_INTERVAL)
    except Exception:                                      # noqa: BLE001
        pass


def _remove_env_watch():
    try:
        if bpy.app.timers.is_registered(_watch_env_changes):
            bpy.app.timers.unregister(_watch_env_changes)
    except Exception:                                      # noqa: BLE001
        pass


def _daynight_card():
    """『游戏环境与后期』图里的『环境_昼夜循环』卡(没有就 None)。"""
    try:
        groups = list(bpy.data.node_groups)
    except Exception:                                      # noqa: BLE001
        return None
    for group in groups:
        if getattr(group, "bl_idname", "") != "GKEnvLibraryTree":
            continue
        for node in getattr(group, "nodes", ()):
            if getattr(node, "bl_idname", "") == "GKNodeDayNight":
                return node
    return None


def _daynight_preview_backdrop(card):
    """按『起始时刻』插值出的天色(**近似**,只用来铺预览底色)。

    ── 为什么只能是近似(同一句话也写进了 describe)──────────────────
    游戏里这套跑在 Godot 的 sky shader 上:三段渐变 + 程序化云 + 太阳盘,
    每帧都在动。Blender 视口里能对齐的只有"平铺的世界底色",所以这里只把
    **当前时刻的天顶色**换算成线性色铺上去,形状一概不画。
    """
    from . import asset_export as ae
    try:
        hour = float(getattr(card, "gk_start_hour", 8.0) or 0.0) % 24.0
    except (TypeError, ValueError):
        hour = 8.0
    keys = []
    for entry in str(getattr(card, "gk_keyframes", "") or "").split("|"):
        text = entry.strip()
        if "=" not in text:
            continue
        left, right = text.split("=", 1)
        try:
            keys.append((float(left.strip()) % 24.0,
                         ae._daynight_preset_index(right.strip())))
        except (TypeError, ValueError):
            continue
    if not keys:
        keys = [(12.0, ae._daynight_preset_index("白天"))]
    keys.sort()
    if len(keys) == 1:
        top = ae._DAYNIGHT_PRESETS[keys[0][1]][1]
    else:
        prev = keys[-1]
        nxt = keys[0]
        for index, key in enumerate(keys):
            if hour >= key[0]:
                prev = key
                nxt = keys[(index + 1) % len(keys)]
        h0, h1 = prev[0], nxt[0]
        if h1 <= h0:
            h1 += 24.0
        hh = hour if hour >= h0 else hour + 24.0
        t = max(0.0, min(1.0, (hh - h0) / max(h1 - h0, 1e-4)))
        first = ae._DAYNIGHT_PRESETS[prev[1]][1]
        second = ae._DAYNIGHT_PRESETS[nxt[1]][1]
        top = tuple(first[i] + (second[i] - first[i]) * t for i in range(3))
    return tuple(srgb_to_linear(component) for component in top) + (1.0,)


def env_display(scene):
    """预览要对齐的那几项 —— 读**导出用的同一份**场景设置。

    没开『环境』(或 2D 关卡 —— 导出器同样不导环境)时返回 Godot 的
    **出厂值**:0.30 平灰、Linear 色调映射 —— 也就是老行为。
    """
    settings = getattr(scene, "gk_settings", None)
    enabled = bool(getattr(settings, "env_enabled", False)) if settings else False
    # 第一百三十三轮:『环境与后期』图里放了昼夜 / 天空盒卡时,导出**一定会**
    # 建 WorldEnvironment(天空盒得挂在它上面,见 asset_export.env_settings 的
    # 例外说明)—— 预览跟着一起打开,两边才是同一件事。
    daynight = _daynight_card()
    if not enabled and daynight is not None:
        enabled = True
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
            # 设置卡没开 = Godot 出厂画面:雾是**关**的(跟着一起关)
            "fog": False,
            "fog_color": (0.30, 0.34, 0.42, 1.0),
            "fog_density": 0.0,
            # 第 124 轮新增(没开环境 = 全部出厂值)
            "fog_light_energy": 1.0,
            "fog_sky_affect": 1.0,
            "fog_aerial_perspective": 0.0,
            "glow_strength": 1.0,
            "glow_threshold": 1.0,
            "tonemap_exposure": 1.0,
            "tonemap_white": 1.0,
            "brightness": 1.0,
            "contrast": 1.0,
            "saturation": 1.0,
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
    if daynight is not None:
        # 昼夜卡在场:底色按『起始时刻』的天色铺(近似;形状 / 云一概不画,
        # 见 _daynight_preview_backdrop 的说明)
        try:
            backdrop = _daynight_preview_backdrop(daynight)
            background = "SKY"
        except Exception:                                  # noqa: BLE001
            pass
    return {
        "enabled": True,
        "tonemap": tonemap,
        "view_transform": TONEMAP_VIEW_TRANSFORMS[tonemap],
        "background": background,
        "backdrop": backdrop,
        # 雾(第一百一十一轮):颜色和密度**直接用设置值** —— 和背景同一条
        # 换算规矩(导出把线性换算成显示值写进 .tscn,Blender 显示线性色
        # 正好等于那个显示值)。密度两边单位一样(每米)。
        "fog": bool(getattr(settings, "env_fog", False)),
        "fog_color": _rgba(getattr(settings, "env_fog_color", None),
                           (0.30, 0.34, 0.42, 1.0)),
        "fog_density": float(getattr(settings, "env_fog_density", 0.02) or 0.0),
        # ── 第 124 轮:新键(预览要按同一份设置对齐)──────────────────
        # 都是"值 == 出厂值就不落地"的键,预览这边直接读,两边语义一致。
        "fog_light_energy": float(getattr(settings, "env_fog_light_energy", 1.0)
                                  or 1.0),
        "fog_sky_affect": float(getattr(settings, "env_fog_sky_affect", 1.0)
                                or 0.0),
        "fog_aerial_perspective": float(
            getattr(settings, "env_fog_aerial_perspective", 0.0) or 0.0),
        "glow_strength": float(getattr(settings, "env_glow_strength", 1.0)
                               or 1.0),
        "glow_threshold": float(getattr(settings, "env_glow_threshold", 1.0)
                                or 1.0),
        "tonemap_exposure": float(getattr(settings, "env_tonemap_exposure", 1.0)
                                  or 1.0),
        "tonemap_white": float(getattr(settings, "env_tonemap_white", 1.0)
                               or 1.0),
        "brightness": float(getattr(settings, "env_brightness", 1.0) or 1.0),
        "contrast": float(getattr(settings, "env_contrast", 1.0) or 1.0),
        "saturation": float(getattr(settings, "env_saturation", 1.0) or 1.0),
    }


def build_world(backdrop=None, *, scene=None, ambient_color=None, ambient_energy=0.0):
    """建/刷新那个"看得见、不照亮"的世界。返回世界对象。

    backdrop = **线性的世界底色**(env_display 已经算好换算;不给就用
    Godot 出厂灰 0.30 换算出来的线性值)。
    """
    if backdrop is None:
        grey = srgb_to_linear(GODOT_CLEAR_COLOR_SRGB[0])
        backdrop = (grey, grey, grey, 1.0)
    backdrop = _rgba(backdrop, (0.0, 0.0, 0.0, 1.0))
    name = WORLD_NAME if scene is None else WORLD_NAME + " · " + scene.name
    world = bpy.data.worlds.get(name)
    if world is None:
        world = bpy.data.worlds.new(name)
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
    shade.inputs["Color"].default_value = (_rgba(ambient_color, (0.0, 0.0, 0.0, 1.0))
                                           if ambient_energy > 0.0 else (0.0, 0.0, 0.0, 1.0))
    shade.inputs["Strength"].default_value = max(0.0, ambient_energy)

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


def _scene_bounds(scene):
    """场景里所有网格物体的世界包围盒 -> (中心, 尺寸);一个网格都没有 -> None。

    用 `bound_box`(物体本地空间的 8 个角)乘 `matrix_world` —— 那是导出
    也认的那份几何,不含修改器结果,对"给雾盒子定个大小"足够。
    """
    from mathutils import Vector
    low = [1.0e30, 1.0e30, 1.0e30]
    high = [-1.0e30, -1.0e30, -1.0e30]
    found = False
    for obj in scene.objects:
        if getattr(obj, "type", "") != "MESH":
            continue
        if getattr(obj, "name", "").startswith(FOG_NAME):
            continue                       # 我们自己建的雾盒子不算场景
        try:
            corners = [obj.matrix_world @ Vector(corner)
                       for corner in obj.bound_box]
        except Exception:                  # noqa: BLE001 —— 拿不到就跳过这个物体
            continue
        for corner in corners:
            for index in range(3):
                low[index] = min(low[index], corner[index])
                high[index] = max(high[index], corner[index])
        found = True
    if not found:
        return None
    centre = Vector([(low[i] + high[i]) * 0.5 for i in range(3)])
    size = Vector([max(high[i] - low[i], 1.0) for i in range(3)])
    return centre, size


def _camera_bounds(scene):
    """没有网格时的兜底范围:以相机(没有就原点)为中心的一块默认体积。

    空场景 / 只有灯和相机的调试场景也要能看到雾 —— 不然"预览没雾"和
    "设置卡上没开"看起来一模一样,又要来回查。
    """
    from mathutils import Vector
    camera = getattr(scene, "camera", None)
    if camera is not None:
        try:
            return Vector(camera.matrix_world.translation), Vector((1.0, 1.0, 1.0))
        except Exception:                  # noqa: BLE001
            pass
    return Vector((0.0, 0.0, 0.0)), Vector((1.0, 1.0, 1.0))


def build_fog(scene, values):
    """建/刷新预览用的雾盒子。返回盒子对象;不需要雾时返回 None。

    ── 怎么近似 Godot 的雾(真机渲图标定,见模块头)────────────────────
    `Principled Volume`:Color = 雾色、Density = 密度、
    Emission Color = 雾色、Emission Strength = 密度 —— 于是
        L = L0*exp(-d*depth) + fog_color*(1-exp(-d*depth))
    和 Godot 的 `fog_density / fog_light_color` 那条公式**同形**。
    ⚠ 自发光强度必须**跟着密度走**(不是 1.0):它对应的是"每米放多少
      光",填成常数的话密度一调,雾色就会跟着过曝或发灰(标定时试过)。
    """
    existing = None
    wanted = bool(values.get("fog")) and float(values.get("fog_density", 0.0)) > 0.0
    if not wanted:
        # ⚠ "不需要雾"这条要在**碰 bpy.data 之前**返回:离线单测的桩里
        #   没有 bpy.data,而这条路径也不需要任何数据(纯判断)。
        return None
    fog_name = FOG_NAME + " · " + scene.name
    material_name = FOG_MATERIAL_NAME + " · " + scene.name
    existing = bpy.data.objects.get(fog_name)
    bounds = _scene_bounds(scene) or _camera_bounds(scene)
    centre, size = bounds

    obj = existing
    if obj is None or obj.name not in bpy.data.objects:
        mesh = bpy.data.meshes.new(fog_name)
        # 单位立方体(-1..1),再靠 object.scale 撑到要的大小
        verts = [(-1.0, -1.0, -1.0), (1.0, -1.0, -1.0), (1.0, 1.0, -1.0),
                 (-1.0, 1.0, -1.0), (-1.0, -1.0, 1.0), (1.0, -1.0, 1.0),
                 (1.0, 1.0, 1.0), (-1.0, 1.0, 1.0)]
        faces = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
                 (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        obj = bpy.data.objects.new(fog_name, mesh)
        scene.collection.objects.link(obj)
    obj["gk_environment_preview"] = True

    # 选中/点击不碍事:看得见(线框),但选不中、也不进大纲的选中链
    obj.hide_select = True
    try:
        obj.display_type = "WIRE"
    except Exception:
        pass
    obj.scale = ((size.x * 0.5) + FOG_MARGIN,
                 (size.y * 0.5) + FOG_MARGIN,
                 (size.z * 0.5) + FOG_MARGIN)
    obj.location = centre

    material = bpy.data.materials.get(material_name)
    if material is None:
        material = bpy.data.materials.new(material_name)
    material.use_nodes = True
    # 相机在盒子**里面**:剔背面,免得正/反两层一起算(密度会翻倍)
    material.use_backface_culling = True
    tree = material.node_tree
    for node in list(tree.nodes):
        tree.nodes.remove(node)
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (320, 0)
    volume = tree.nodes.new("ShaderNodeVolumePrincipled")
    volume.location = (60, 0)
    colour = tuple(values.get("fog_color", (0.3, 0.34, 0.42, 1.0))[:3])
    density = float(values.get("fog_density", 0.0))
    # 第 124 轮:雾亮度(fog_light_energy)。语义和 Godot 一样是"雾整体多亮",
    # 所以直接乘在自发光强度上(见模块头那条标定:L 的雾项系数就是
    # density,再乘 fog_light_energy 就是"雾色更亮但密度不变")。
    # 默认 1.0 = 老行为。
    fog_energy = float(values.get("fog_light_energy", 1.0) or 1.0)
    volume.inputs["Color"].default_value = colour + (1.0,)
    volume.inputs["Density"].default_value = density
    volume.inputs["Emission Color"].default_value = colour + (1.0,)
    volume.inputs["Emission Strength"].default_value = density * fog_energy
    tree.links.new(volume.outputs["Volume"], output.inputs["Volume"])

    obj.data.materials.clear()
    obj.data.materials.append(material)
    return obj


def clear_fog(scene=None):
    """把雾盒子连它的网格 / 材质一起清掉(幂等;没有就什么都不做)。"""
    suffix = " · " + scene.name if scene is not None else ""
    obj = bpy.data.objects.get(FOG_NAME + suffix)
    mesh = None
    if obj is not None:
        mesh = obj.data
        try:
            bpy.data.objects.remove(obj, do_unlink=True)
        except Exception:                  # noqa: BLE001
            pass
    if mesh is not None:
        try:
            bpy.data.meshes.remove(mesh)
        except Exception:                  # noqa: BLE001
            pass
    material = bpy.data.materials.get(FOG_MATERIAL_NAME + suffix)
    if material is not None and material.users == 0:
        try:
            bpy.data.materials.remove(material)
        except Exception:                  # noqa: BLE001
            pass


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
    # 第 124 轮:曝光对齐。Godot 的 tonemap_exposure 是倍率,Blender 的
    # exposure 是档位(2^exposure)—— stops = log2(倍率),见 _exposure_stops
    # 的标定注释。默认 1.0 -> 0.0 档,和改动前(写死 0.0)完全一致。
    view.exposure = _exposure_stops(values["tonemap_exposure"])
    view.gamma = 1.0
    view.use_curve_mapping = False

    # 每个场景保留自己的世界，避免切换关卡时覆盖另一关的预览。
    settings = getattr(scene, "gk_settings", None)
    lit = (not getattr(settings, "toon_materials", True)
           or getattr(settings, "toon_follow_light", False))
    ambient_energy = (getattr(settings, "env_ambient_energy", 0.0)
                      if values["enabled"] and lit else 0.0)
    if (getattr(settings, "toon_materials", False)
            and getattr(settings, "toon_follow_light", False)):
        # Godot's uniform ambient is reproduced in the temporary diffuse
        # material. EEVEE world irradiance would otherwise count it twice.
        ambient_energy = 0.0
    world = build_world(values["backdrop"], scene=scene,
                        ambient_color=getattr(settings, "env_ambient_color", None),
                        ambient_energy=ambient_energy)
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

    # ---- 2b. 预览用场景真实光照(第 124 轮)------------------------
    # 分两条腿:
    #   * `scene.display.shading` 那三项(use_scene_lights / show_shadows /
    #     use_scene_world)对 **OpenGL / Workbench 渲染**(bpy.ops.render.opengl、
    #     Workbench 引擎)是真生效的,代价为零,一并设上;
    #   * 交互视口吃的是 `space.shading`,SOLID 下那三个开关**无效**(真机
    #     截图逐项隔离验证过),只能把 `type` 切成 RENDERED 才用真实场景灯。
    # 所以交互视口走 `_apply_viewport_scene_lights()`,由 `preview_scene_lights`
    # 字段决定要不要切(默认切)。
    if shading is not None:
        try:
            shading.use_scene_lights = True
            shading.show_shadows = True
            shading.use_scene_world = True
        except Exception:
            pass
    if _preview_uses_scene_lights(scene):
        _apply_viewport_scene_lights()
        # 灯一起调暗:预览比运行亮一倍多,亮的又几乎全是灯(见 PREVIEW_LIGHT_SCALE
        # 的真机标定)。只在交互会话里做,Ctrl+S 前会自动还原。
        _dim_lights(scene)
        notes.append("视口切成 Rendered 以对齐场景光照、灯光按实测标定调暗"
                     f"(x{PREVIEW_LIGHT_SCALE};可关『预览用场景光照』)")
    else:
        _undim_lights(scene)
        _restore_viewport_shading(scene)

    # Preview the same ordinary-material replacement that export performs.
    # Background builds keep authored slots; GPU verification opts in explicitly.
    if not bpy.app.background:
        from . import godot_material_preview
        if godot_material_preview.apply(scene):
            notes.append("材质按导出的基础色与漫反射预览(逐灯卡通色阶近似)")

    # ---- 3. 雾:按设置卡的密度 / 颜色做一个体积盒子(第一百一十一轮)----
    if values["fog"] and values["fog_density"] > 0.0:
        fog = build_fog(scene, values)
        if fog is not None:
            note = f"雾 密度 {values['fog_density']:.4f}"
            # 雾亮度(fog_light_energy)≠1 时注明(默认 1.0 不写,免得噪音)
            if abs(values["fog_light_energy"] - 1.0) > 1e-9:
                note += f"·亮度 {values['fog_light_energy']:.2f}"
            note += "(近处清楚、远处发白,近似)"
            notes.append(note)
    else:
        # 关掉雾时也要把上一轮的盒子收走,否则"改了设置没生效"
        clear_fog(scene)
        if values["enabled"]:
            notes.append("雾:设置卡上没开,预览也不加")

    # ---- 3b. 后期:色调调整 + 泛光(第一百三十五轮,视口合成器)--------
    # 用户原话:"游戏环境与后期的节点图逻辑并未在游戏中生效" +
    # "预览的画面与环境效果太差了,但运行时的却是不错的灯光与质感"。
    # 预览以前**根本不画**这两样,所以在图里调色 = 视口一点不动。
    post = post_values(scene, values)
    post_state = build_post(scene, post)
    if post_state == "":
        bits = []
        if post.get("env_adjust_on"):
            bits.append("场景调色")
        if post["grade_on"]:
            bits.append(f"色调 亮度{post['brightness']:.2f}/对比"
                        f"{post['contrast']:.2f}/饱和{post['saturation']:.2f}")
            if post["tint_amount"] > 0.0:
                bits.append(f"色调混 {post['tint_amount']:.2f}")
            if post["mask_on"]:
                bits.append("按『屏幕位置』限定区域"
                            + ("(反选)" if post["mask_invert"] else ""))
        if post["glow_on"]:
            bits.append(f"泛光 强度{post['glow_intensity']:.2f}"
                        f"/阈值{post['glow_threshold']:.2f}")
        notes.append("后期(视口合成器近似):" + " · ".join(bits))
    else:
        clear_post(scene)
        if post_state == "busy" and (post["grade_on"] or post["glow_on"]):
            notes.append("后期:场景合成器已被占用,预览不抢(色调 / 泛光没画)")

    # ---- 4. 关掉 Godot 没有的那些效果 ----
    eevee = getattr(scene, "eevee", None)
    if eevee is not None:
        # ── 第一百四十轮:不再无脑关 AO ─────────────────────────────────
        # 用户说"游戏环境与后期的效果有一部分没作用" —— 查下来有一条是我
        # 们自己造成的:预览**总是**把 Blender 的 AO 关掉,而这一关在 Godot
        # 里 `ssao_enabled = true`(设置卡上开了 SSAO/SSIL)。于是"开了也看不见"。
        # 现在按设置卡走:开了就留着(近似 Godot 的 SSAO),没开才关。
        wants_ao = bool(getattr(settings, "env_ssao", False)) if settings else False
        wants_gi = bool(getattr(settings, "env_ssil", False)) if settings else False
        for attr, label, wanted in (("use_raytracing", "光线追踪", False),
                                    ("use_gtao", "环境光遮蔽", wants_ao),
                                    ("use_fast_gi", "快速 GI", wants_gi)):
            if not hasattr(eevee, attr):
                continue
            try:
                # 光追 Godot 没有,永远关;AO / GI 跟设置卡(Godot 的
                # ssao_enabled / ssil_enabled)对齐。
                currently = bool(getattr(eevee, attr, False))
                if wanted and not currently:
                    setattr(eevee, attr, True)
                elif not wanted and currently:
                    setattr(eevee, attr, False)
            except Exception:                              # noqa: BLE001
                pass

    # ---- 5. 说清"对不上的那些"(第九十五/一百二十四/一百三十五轮更新)----
    notes.append("Linear 模式按 Godot 显示色域调色；泛光、光照衰减和卡通色阶仍为近似")
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
    view.use_curve_mapping = bool(scene.get(_BACKUP["curve_mapping"], False))

    world_name = scene.get(_BACKUP["world"], "")
    original = bpy.data.worlds.get(world_name) if world_name else None
    scene.world = original

    shading = getattr(getattr(scene, "display", None), "shading", None)
    if shading is not None:
        saved = scene.get(_BACKUP["background_type"])
        if saved:
            try:
                shading.background_type = saved
            except Exception:
                pass
        # 第 124 轮:还原 apply() 设的三项场景光照开关
        for attr, key in (("use_scene_lights", "scene_lights"),
                          ("show_shadows", "show_shadows"),
                          ("use_scene_world", "scene_world")):
            if hasattr(shading, attr):
                try:
                    setattr(shading, attr,
                            bool(scene.get(_BACKUP[key], False)))
                except Exception:
                    pass

    # 第 124 轮:还原被切成 RENDERED 的交互视口 type(逐个放回)
    _restore_viewport_shading(scene)
    # 第 124 轮:把调暗的灯放回原来的瓦数
    _undim_lights(scene)
    from . import godot_material_preview
    godot_material_preview.restore(scene)

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
    ours = bpy.data.worlds.get(WORLD_NAME + " · " + scene.name)
    if ours is not None and ours is not scene.world and ours.users == 0:
        try:
            bpy.data.worlds.remove(ours)
        except Exception:
            pass

    # 雾盒子(第一百一十一轮)也是我们建的 —— 一起收走
    clear_fog(scene)
    # 后期节点(第一百三十五轮)同样是临时的:拆掉 + 还原合成器开关
    clear_post(scene)

    scene[_BACKUP["active"]] = False
    return True, "已还原成原来的观感"


def refresh(scene):
    """预览开着的话,按**新的**设置卡值重刷一遍(没开就什么都不做)。

    ── 为什么需要它(用户原话:"那几个预设风格预览不到")────────────────
    在『游戏环境与后期』侧栏里点一档预设,算子把值写进了 `gk_settings` ——
    但视口里**什么都没变**,因为预览是在『预览贴近 Godot』那一刻按当时的
    值搭好的:背景世界、雾盒子、曝光、调暗系数都是**搭的时候算的**,
    之后再改设置卡不会自动跟着走。用户看到的就是"点了没反应"。

    所以凡是"改环境设置"的入口(应用预设 / 一键套用内置档)都要在改完
    之后调一下这里 —— 开着才刷,没开不打扰。
    """
    if scene is None or not is_active(scene):
        return False, "预览没开着"
    return apply(scene)


def is_active(scene):
    """现在是不是处于"Godot 观感"。"""
    if scene is None:
        return False
    return bool(scene.get(_BACKUP["active"]))


# ── 实时显示:设置一改,视口跟着变(第一百三十七轮)────────────────────
# 用户原话:"这游戏环境与后期的设置应该是实时显示于视图中的,不然每次运行
# 再看效果太折磨了。"
#
# 为什么用**轮询指纹**而不是给每个属性挂 update 回调:
#   * 同一批设置有三个入口(『游戏环境设置』卡、游戏环境与后期侧栏、
#     『游戏环境与后期』图里的卡 `gk_*` 属性),逐个挂回调要动几十处声明,
#     漏一个就是"改了没反应";
#   * 轮询只读一小撮标量,0.4 秒一次,开销可以忽略;
#   * 顺带覆盖"撤销 / 脚本改值 / 套预设"这些**不触发 update** 的路径。
_ENV_WATCH_INTERVAL = 0.35
_ENV_SIGNATURE = {}


def _env_signature(scene):
    """这一关"环境/预览相关设置"的指纹(含环境图里那几张卡的属性)。"""
    _capture_scene_light_edits(scene)
    values = []
    settings = getattr(scene, "gk_settings", None)
    if settings is not None:
        for name in sorted(dir(settings)):
            if not name.startswith(("env_", "preview_", "toon_", "style_")):
                continue
            try:
                value = getattr(settings, name)
            except Exception:                              # noqa: BLE001
                continue
            values.append(_signature_of(value))
    tree = _env_tree()
    for node in getattr(tree, "nodes", ()) or ():
        values.append((node.name, node.bl_idname))
        prop_map = getattr(node, "gk_prop_map", None) or {}
        for name in sorted(prop_map):
            try:
                value = getattr(node, name)
            except Exception:                              # noqa: BLE001
                continue
            values.append(_signature_of(value))
    values.append(tuple((link.from_node.name, link.from_socket.identifier,
                         link.to_node.name, link.to_socket.identifier)
                        for link in getattr(tree, "links", ())))
    for obj in _scene_lights(scene):
        data = authored_light_data(obj)
        values.append((obj.name, data.name, data.type))
        for name in ("energy", "color", "cutoff_distance", "use_shadow", "spot_size",
                     "spot_blend", "shadow_soft_size", "angle", "diffuse_factor",
                     "specular_factor", "volume_factor"):
            values.append(_signature_of(getattr(data, name, None)))
        override = getattr(obj, "gk_light", None)
        for name in ("override", "energy", "use_color", "color", "range_m",
                     "attenuation", "shadow", "preview_scale"):
            values.append(_signature_of(getattr(override, name, None)))
    return tuple(values)


def _signature_of(value):
    if isinstance(value, (bool, int, float, str)):
        return value if not isinstance(value, float) else round(value, 5)
    try:
        return tuple(round(float(item), 5) for item in value)
    except Exception:                                      # noqa: BLE001
        return str(value)


def _watch_env_changes():
    """定时器:发现设置变了就把预览重刷一遍(= 实时显示)。"""
    if getattr(bpy.app, "background", False):
        return _ENV_WATCH_INTERVAL
    for scene in list(bpy.data.scenes):
        if not is_active(scene):
            _ENV_SIGNATURE.pop(scene.name, None)
            continue
        try:
            signature = _env_signature(scene)
        except Exception:                                  # noqa: BLE001
            continue
        if _ENV_SIGNATURE.get(scene.name) == signature:
            continue
        _ENV_SIGNATURE[scene.name] = signature
        try:
            apply(scene)
        except Exception:                                  # noqa: BLE001
            pass
    return _ENV_WATCH_INTERVAL


def request_refresh(*_args):
    """让所有开着预览的场景立刻重刷(算子 / 预设套用之后调一下)。"""
    for scene in list(bpy.data.scenes):
        if not is_active(scene):
            continue
        try:
            apply(scene)
        except Exception:                                  # noqa: BLE001
            pass


def summary(scene=None):
    """给面板显示的一行摘要(按当前设置;第九十五轮起不是写死的)。"""
    values = env_display(scene) if scene is not None else None
    if values is None or not values["enabled"]:
        grey = GODOT_CLEAR_COLOR_SRGB[0]
        return (f"背景 {grey:.2f} 平灰 · 无声环境光 · "
                f"色调映射 Linear(与 Godot 默认一致)")
    exact = "" if values["tonemap"] in TONEMAP_EXACT else "(近似)"
    where = "天空(地平线色近似)" if values["background"] == "SKY" else "纯色"
    fog = (f" · 雾 {values['fog_density']:.4f}"
           if values.get("fog") else " · 无雾")
    return (f"背景 {where}{fog} · 色调映射 {values['tonemap']}{exact} -> "
            f"视图变换 {values['view_transform']}")


def describe():
    """Short lines remain readable in Blender's narrow sidebar."""
    return (
        "按当前场景与已连接的环境节点预览",
        "· Linear 调色按 Godot 显示色域计算",
        "· 场景调色与节点调色依次叠加",
        "· 圆形/矩形遮罩、羽化与反选同步",
        "· 灯色、射程与投影跟随导出设置",
        "· 灯光倍率在『游戏环境与后期』侧栏",
        "· 参数或连线改变后自动刷新",
        "仍有差异:光照衰减、卡通色阶、泛光",
        "天空、非 Linear 映射、SDF 与描边近似",
        "游戏逻辑触发的演出请在 Godot 中确认",
    )


def on_unregister():
    """清理:不让我们的世界在禁用插件后变成孤儿。

    ── 为什么这里以前是空的(第 124 轮补上)──────────────────────────
    这个函数很久以来**只有一行 docstring、没有函数体** —— 于是禁用插件后
    `GK Godot 环境 · <场景>` 那几张世界、雾盒子、调暗的灯、save 钩子全都
    留在文件里:用户看到"插件都卸了怎么还有 GK 的东西",而且世界会一直在
    `bpy.data.worlds` 里攒(`.blend` 越存越大)。

    清理**必须逐场景**做(世界名带场景名,雾盒子挂在场景上),而且
    **不能调 `restore()`**:那会把用户自己改过的视图变换 / 曝光一起"还原"
    成备份值 —— 插件卸载不该动用户当前的观感,只需要把**我们造的**东西收走。
    """
    _remove_save_hooks()
    _remove_env_watch()
    from . import godot_material_preview
    godot_material_preview.restore_all()
    for scene in list(bpy.data.scenes):
        # 灯:调暗过就放回原瓦数(用户存盘前先卸载插件也不会丢瓦数)
        try:
            _undim_lights(scene)
        except Exception:                                  # noqa: BLE001
            pass
        # 雾盒子(我们自己建的)
        try:
            clear_fog(scene)
        except Exception:                                  # noqa: BLE001
            pass
        # 后期节点(第一百三十五轮,我们自己建的)
        try:
            clear_post(scene)
        except Exception:                                  # noqa: BLE001
            pass
        # 世界:名字带场景名,而且**没人在用**才删(用户可能手动指定过它)
        ours = bpy.data.worlds.get(WORLD_NAME + " · " + scene.name)
        if ours is not None and ours is not scene.world and ours.users == 0:
            try:
                bpy.data.worlds.remove(ours)
            except Exception:                              # noqa: BLE001
                pass
    # 兜底:场景被删过、世界还在的情况(名字带场景名的那批)
    for world in list(bpy.data.worlds):
        if (world.name == WORLD_NAME or world.name.startswith(WORLD_NAME + " · ")) \
                and world.users == 0:
            try:
                bpy.data.worlds.remove(world)
            except Exception:                              # noqa: BLE001
                pass
    # 场景上的备份标记清掉(留着会让下次 apply 以为"已经开过")
    for scene in list(bpy.data.scenes):
        try:
            if scene.get(_BACKUP["active"]):
                scene[_BACKUP["active"]] = False
        except Exception:                                  # noqa: BLE001
            pass
    _remove_save_hooks()
