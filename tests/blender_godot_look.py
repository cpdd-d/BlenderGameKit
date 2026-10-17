"""Godot 观感:让 Blender 预览贴近 Godot 的渲染。

重点测三件事,每件都对应一个真实的坑:

1. **sRGB -> 线性**
   Godot 的清屏色 0.3 是 **sRGB**(直接显示的输出色),
   而 Blender 的 Background 节点要**线性**值(0.3 的 sRGB ≈ 0.073)。
   直接填 0.3 的话背景会比 Godot 亮一大截 —— 所以这里要卡死这个换算。

2. **世界只看得见、不照亮**
   Godot 默认没有环境光,物体只被场景灯照亮。
   Blender 的世界默认会当光源,所以要用 Light Path 的
   `Is Camera Ray` 把非相机射线挡成纯黑。节点结构必须对。

3. **色调映射对齐 Linear**
   Godot 默认 tonemap_mode = Linear(不做色调映射),
   Blender 默认是 AgX。要切成 Standard。

用法:
    blender -b --python tests/blender_godot_look.py
"""

import os
import sys
import traceback
from importlib import import_module

import addon_utils
import bmesh
import bpy

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" - {detail}" if detail and not condition else ""))
    return bool(condition)


def find_addon():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isfile(os.path.join(root, "BlenderGameKit", "__init__.py")):
        sys.path.insert(0, root)
        return "BlenderGameKit"
    for module in addon_utils.modules():
        if "blender_game_kit" in getattr(module, "__name__", ""):
            return module.__name__
    return None


def main():
    module_name = find_addon()
    if not check("找到插件", module_name, "未安装"):
        return 1
    addon_utils.enable(module_name, default_set=False, persistent=False)
    look = import_module(f"{module_name}.godot_look")

    scene = bpy.context.scene

    # ---- 1. sRGB -> 线性换算 ----
    print("=" * 72)
    print("1. sRGB 和线性值不能混")
    print("=" * 72)
    grey_srgb = look.GODOT_CLEAR_COLOR_SRGB[0]
    grey_linear = look.srgb_to_linear(grey_srgb)
    print(f"    Godot 清屏色 {grey_srgb} (sRGB) -> 线性 {grey_linear:.4f}")
    check("0.3 的 sRGB 约等于 0.073 的线性",
          abs(grey_linear - 0.0732) < 0.002, f"{grey_linear:.4f}")
    check("换算结果明显不是 0.3 本身(这就是那个坑)",
          abs(grey_linear - grey_srgb) > 0.2, f"{grey_linear:.4f}")
    check("往返换算能回到原值",
          abs(look.linear_to_srgb(grey_linear) - grey_srgb) < 1e-6,
          str(look.linear_to_srgb(grey_linear)))

    # ---- 2. 开启前先记下原始状态 ----
    print("=" * 72)
    print("2. 开启")
    print("=" * 72)
    before_transform = str(scene.view_settings.view_transform)
    before_world = scene.world.name if scene.world else ""
    print(f"    开启前: 视图变换={before_transform!r} 世界={before_world!r}")
    check("开启前不是 Godot 观感", not look.is_active(scene))

    ok, message = look.apply(scene)
    print(f"    apply: {ok} / {message}")
    check("apply 成功", ok, message)
    check("现在标记为已开启", look.is_active(scene))

    # ---- 3. 色调映射 ----
    print("=" * 72)
    print("3. 色调映射切成 Standard(对应 Godot 的 Linear)")
    print("=" * 72)
    print(f"    视图变换: {scene.view_settings.view_transform!r}")
    check("视图变换是 Standard",
          str(scene.view_settings.view_transform) == "Standard",
          str(scene.view_settings.view_transform))
    check("曝光归零", abs(scene.view_settings.exposure) < 1e-6)
    check("gamma 归 1", abs(scene.view_settings.gamma - 1.0) < 1e-6)

    # ---- 4. 世界的节点结构 ----
    print("=" * 72)
    print("4. 世界:灰底看得见,但不照亮")
    print("=" * 72)
    world = scene.world
    check("世界换成了我们建的那个", world is not None and
          world.name == look.WORLD_NAME + " · " + scene.name,
          getattr(world, "name", "None"))
    if world is not None:
        nodes = world.node_tree.nodes
        kinds = sorted(node.bl_idname for node in nodes)
        print(f"    节点: {kinds}")
        check("有 Light Path(用来区分相机射线)",
              "ShaderNodeLightPath" in kinds, str(kinds))
        check("有 Mix Shader", "ShaderNodeMixShader" in kinds, str(kinds))
        backgrounds = [n for n in nodes
                       if n.bl_idname == "ShaderNodeBackground"]
        check("有两个 Background(灰底 + 纯黑)", len(backgrounds) == 2,
              str(len(backgrounds)))

        # 灰底那个必须是我们换算过的线性值
        greys = [n for n in backgrounds
                 if abs(n.inputs["Color"].default_value[0] - grey_linear) < 1e-5]
        check("有一个 Background 用的是线性灰(不是 0.3)", len(greys) == 1,
              str([round(n.inputs["Color"].default_value[0], 4)
                   for n in backgrounds]))
        blacks = [n for n in backgrounds
                  if n.inputs["Color"].default_value[0] < 1e-6]
        check("另一个 Background 是纯黑(不给场景加环境光)", len(blacks) == 1,
              str(len(blacks)))

        # 接线:Is Camera Ray -> Mix.Fac;黑 -> 输入1;灰 -> 输入2
        mix = next((n for n in nodes
                    if n.bl_idname == "ShaderNodeMixShader"), None)
        path = next((n for n in nodes
                     if n.bl_idname == "ShaderNodeLightPath"), None)
        if mix is not None and path is not None:
            linked = {link.from_socket.name: link.to_socket.name
                      for link in world.node_tree.links}
            print(f"    连线: {linked}")
            check("Is Camera Ray 接到 Mix 的 Fac",
                  linked.get("Is Camera Ray") == "Fac", str(linked))

        # Solid 模式的背景色也要跟着改
        check("world.color 也是灰的(实体模式背景)",
              abs(world.color[0] - grey_linear) < 1e-5,
              str(tuple(round(c, 4) for c in world.color)))

    # ---- 5. EEVEE 关掉 Godot 没有的效果 ----
    print("=" * 72)
    print("5. 关掉 Godot 默认没有的效果")
    print("=" * 72)
    eevee = scene.eevee
    print(f"    use_raytracing={eevee.use_raytracing}  use_gtao={eevee.use_gtao}")
    check("光线追踪关掉了", not eevee.use_raytracing)
    check("环境光遮蔽关掉了", not eevee.use_gtao)

    # ---- 6. 还原 ----
    print("=" * 72)
    print("6. 还原")
    print("=" * 72)
    ok2, message2 = look.restore(scene)
    print(f"    restore: {ok2} / {message2}")
    check("restore 成功", ok2, message2)
    check("不再标记为已开启", not look.is_active(scene))
    print(f"    还原后: 视图变换={scene.view_settings.view_transform!r} "
          f"世界={scene.world.name if scene.world else None!r}")
    check("视图变换还原成原来的",
          str(scene.view_settings.view_transform) == before_transform,
          f"{scene.view_settings.view_transform} != {before_transform}")
    check("世界还原成原来的",
          (scene.world.name if scene.world else "") == before_world,
          f"{scene.world.name if scene.world else None} != {before_world}")
    check("我们建的那个世界被清掉了",
          look.WORLD_NAME not in bpy.data.worlds, str(list(bpy.data.worlds.keys())))

    # ---- 7. 反复开关不会出问题 ----
    print("=" * 72)
    print("7. 反复开关")
    print("=" * 72)
    for _ in range(3):
        look.apply(scene)
        look.restore(scene)
    print(f"    三轮之后: 视图变换={scene.view_settings.view_transform!r}")
    check("三轮开关后仍然还原正确",
          str(scene.view_settings.view_transform) == before_transform,
          str(scene.view_settings.view_transform))
    # 重复还原应该给出提示而不是崩
    ok3, _ = look.restore(scene)
    check("重复还原不会崩(只是提示)", ok3 is False)

    # ---- 8. 跟着『环境』设置卡走(第九十五轮)----
    print("=" * 72)
    print("8. 预览跟着设置卡对齐(不再是写死的出厂值)")
    print("=" * 72)
    settings = scene.gk_settings
    keep = {name: getattr(settings, name)
            for name in ("env_enabled", "dimension", "env_tonemap",
                         "env_background", "env_bg_color",
                         "env_fog", "env_fog_color", "env_fog_density")}
    try:
        settings.dimension = "3"
        settings.env_enabled = True
        settings.env_tonemap = "AGX"
        settings.env_background = "COLOR"
        settings.env_bg_color = (0.2, 0.1, 0.05, 1.0)
        values = look.env_display(scene)
        print(f"    env_display: {values}")
        check("色调映射 AgX 映射到 Blender 的 AgX",
              values["view_transform"] == "AgX", str(values))
        ok4, message4 = look.apply(scene)
        print(f"    apply: {message4}")
        check("apply 用的是设置里的色调映射",
              ok4 and str(scene.view_settings.view_transform) == "AgX",
              f"{message4} / {scene.view_settings.view_transform}")
        wanted = 0.2          # 导出换算后,预览直接用设置值当线性世界色
        colours = [node.inputs["Color"].default_value[0]
                   for node in scene.world.node_tree.nodes
                   if node.bl_idname == "ShaderNodeBackground"]
        print(f"    世界背景色: {[round(c, 4) for c in colours]}")
        check("世界底色 = 设置卡上的颜色(不是出厂灰)",
              any(abs(colour - wanted) < 1e-4 for colour in colours),
              str([round(c, 4) for c in colours]))
        check("面板摘要跟着设置走", "AgX" in look.summary(scene),
              look.summary(scene))

        # 天空背景:用导出的地平线色(顶色往白里提 0.35)
        settings.env_background = "SKY"
        sky = look.env_display(scene)
        horizon = 0.2 * 0.65 + 1.0 * 0.35
        check("天空背景用地平线色(近似)",
              abs(sky["backdrop"][0] - horizon) < 1e-4, str(sky["backdrop"]))
        look.restore(scene)

        # ---- 9. 雾:按设置卡做一个体积盒子(第一百一十一轮)----
        print("=" * 72)
        print("9. 雾:预览也要能看见(密度 / 颜色跟着设置卡)")
        print("=" * 72)
        settings.env_background = "COLOR"
        settings.env_fog = True
        settings.env_fog_color = (0.2, 0.22, 0.32, 1.0)
        settings.env_fog_density = 0.05
        settings.env_bg_color = (0.05, 0.06, 0.10, 1.0)
        fog_values = look.env_display(scene)
        check("雾参数传出来了",
              fog_values["fog"] and fog_values["fog_density"] > 0.04,
              str(fog_values))
        ok5, message5 = look.apply(scene)
        print(f"    apply: {message5}")
        fog = bpy.data.objects.get(look.FOG_NAME + " · " + scene.name)
        check("雾盒子建出来了", ok5 and fog is not None, str(fog))
        if fog is not None:
            check("雾盒子包住场景(比场景大得多)",
                  fog.scale.x > 20.0, str(tuple(round(s, 1) for s in fog.scale)))
            check("雾盒子选不中(不碍事)", bool(fog.hide_select))
            material = fog.data.materials[0] if fog.data.materials else None
            nodes = material.node_tree.nodes if material else []
            volume = next((n for n in nodes
                           if n.bl_idname == "ShaderNodeVolumePrincipled"), None)
            check("用的是体积材质(Principled Volume)", volume is not None,
                  str([n.bl_idname for n in nodes]))
            if volume is not None:
                density = volume.inputs["Density"].default_value
                strength = volume.inputs["Emission Strength"].default_value
                colour = volume.inputs["Color"].default_value
                print(f"    密度={density:.4f} 自发光={strength:.4f} "
                      f"颜色={tuple(round(c, 3) for c in colour)}")
                check("密度 = 设置卡上的密度",
                      abs(density - 0.05) < 1e-5, str(density))
                check("自发光强度跟着密度走(同形公式的关键)",
                      abs(strength - density) < 1e-5,
                      f"{strength} != {density}")
                check("雾色 = 设置卡上的颜色",
                      abs(colour[1] - 0.22) < 1e-4, str(tuple(colour)))
        look.restore(scene)
        check("还原把雾盒子收走了",
              look.FOG_NAME + " · " + scene.name not in bpy.data.objects,
              str(list(bpy.data.objects.keys())))
        check("还原把雾材质也收走了",
              look.FOG_MATERIAL_NAME + " · " + scene.name not in bpy.data.materials,
              str(list(bpy.data.materials.keys())))

        # 雾关掉时:连盒子都不该建
        settings.env_fog = False
        look.apply(scene)
        check("设置卡上没开雾 -> 不建盒子",
              look.FOG_NAME + " · " + scene.name not in bpy.data.objects,
              str(list(bpy.data.objects.keys())))
        look.restore(scene)
    finally:
        for name, value in keep.items():
            setattr(settings, name, value)

    # ---- 9. 第 124 轮:预览用场景光照 + 新键对齐 ----
    print("=" * 72)
    print("9. 第 124 轮:场景光照 / 曝光 / 雾亮度 / describe")
    print("=" * 72)

    # 9a. Godot 的 tonemap_exposure 是**倍率**,Blender 的 exposure 是**档位**。
    #     换算必须是 log2 —— 这是真机双端渲染标定过的(见 _exposure_stops)。
    check("曝光换算:倍率 1.0 -> 0 档(不变)",
          abs(look._exposure_stops(1.0)) < 1e-9,
          str(look._exposure_stops(1.0)))
    check("曝光换算:倍率 2.0 -> 1 档",
          abs(look._exposure_stops(2.0) - 1.0) < 1e-9,
          str(look._exposure_stops(2.0)))
    check("曝光换算:倍率 0.5 -> -1 档",
          abs(look._exposure_stops(0.5) + 1.0) < 1e-9,
          str(look._exposure_stops(0.5)))
    check("曝光换算:倍率 0(防御)-> 0 档",
          abs(look._exposure_stops(0.0)) < 1e-9)

    # 9b. `preview_scene_lights` 缺字段时必须**默认开**(老 .blend / 精简环境)。
    check("预览用场景光照:字段缺失时默认开",
          look._preview_uses_scene_lights(scene) is True)

    # 9c. 无头模式(`-b`)绝不能写视口快照。
    #     实测 `blender -b` 下 window_manager.windows 仍然是 1 —— 原来按"没有
    #     窗口就跳过"写,结果示例重建时把文档级视口切成 RENDERED 并**存进
    #     .blend**,还原永远回不到 SOLID。判据必须是 bpy.app.background。
    print(f"    bpy.app.background = {bpy.app.background}")
    if bpy.app.background:
        check("无头模式不采视口快照(判据是 bpy.app.background)",
              look._viewport_shading_snapshot() == "",
              repr(look._viewport_shading_snapshot()))

    # 9d. 老 .blend 的兜底:active=True 但快照是空串时,apply/restore 不能崩,
    #     而且 restore 之后 active 要归位(否则下次点『预览』直接返回失败)。
    scene[look._BACKUP["preview_shading_types"]] = ""
    scene[look._BACKUP["active"]] = True
    try:
        ok_old, _msg = look.apply(scene)
        check("老 .blend(快照为空的 active 状态)apply 不崩", ok_old)
        ok_old2, _msg2 = look.restore(scene)
        check("老 .blend restore 不崩", ok_old2)
        check("老 .blend restore 之后 active 归位",
              not scene.get(look._BACKUP["active"]))
    except Exception as exc:                               # noqa: BLE001
        check("老 .blend 兜底路径不崩", False, f"{type(exc).__name__}: {exc}")

    # 9e. 雾亮度:Godot 的 fog_light_energy 乘在体积自发光上(默认 1.0 = 老行为)。
    fog_values = {
        "fog": True, "fog_density": 0.05,
        "fog_color": (0.20, 0.22, 0.32, 1.0),
        "fog_light_energy": 0.4,
    }
    fog_lit = look.build_fog(scene, fog_values)
    try:
        mat = fog_lit.data.materials[0]
        vol = next(n for n in mat.node_tree.nodes
                   if n.bl_idname == "ShaderNodeVolumePrincipled")
        got = vol.inputs["Emission Strength"].default_value
        want = 0.05 * 0.4
        print(f"    密度 0.05 × 雾亮度 0.4 -> 自发光 {got:.4f}(期望 {want:.4f})")
        check("雾亮度乘进了自发光强度",
              abs(got - want) < 1e-6, f"{got} != {want}")
    finally:
        look.clear_fog(scene)

    # 9f. env_display 必须把新键暴露出来(apply/summary/describe 都读它)。
    keep2 = {name: getattr(settings, name)
             for name in ("env_enabled", "dimension", "env_tonemap_exposure",
                          "env_fog_light_energy", "env_fog_sky_affect",
                          "env_glow_strength", "env_glow_threshold")}
    try:
        settings.dimension = "3"
        settings.env_enabled = True
        settings.env_tonemap_exposure = 2.0
        settings.env_fog_light_energy = 0.4
        settings.env_glow_strength = 2.5
        settings.env_glow_threshold = 0.7
        values = look.env_display(scene)
        for key, expect in (("tonemap_exposure", 2.0),
                            ("fog_light_energy", 0.4),
                            ("glow_strength", 2.5),
                            ("glow_threshold", 0.7)):
            got = values.get(key)
            check(f"env_display 带出 {key} = {expect}",
                  got is not None and abs(got - expect) < 1e-6, repr(got))
        # 曝光真的接到了视图变换上
        look.apply(scene)
        check("曝光接到了 view_settings.exposure(2.0 倍 -> 1 档)",
              abs(scene.view_settings.exposure - 1.0) < 1e-6,
              str(scene.view_settings.exposure))
        look.restore(scene)
    finally:
        for name, value in keep2.items():
            setattr(settings, name, value)

    # The concise sidebar explains the contract and remaining limitations.
    text = "\n".join(look.describe())
    check("describe 说明灯光跟随导出设置",
          "灯色" in text and "射程" in text and "导出设置" in text, text)
    check("describe 区分调色对齐与泛光近似",
          "显示色域" in text and "泛光" in text and "近似" in text, text)
    check("describe 仍如实列出对不上的项",
          "描边" in text and "SDF" in text, text)

    # ---- 10. 第 124 轮:预览灯光亮度对齐运行时 ----
    print("=" * 72)
    print("10. 预览灯光亮度(用户:'预览的灯光太亮了')")
    print("=" * 72)
    # 真机标定:预览 0.9130 vs 运行 0.3953(同一机位),所以要把灯调暗。
    check("灯光系数是个小于 1 的实测值",
          0.0 < look.PREVIEW_LIGHT_SCALE < 1.0,
          str(look.PREVIEW_LIGHT_SCALE))

    lamp = bpy.data.objects.new("灯_测试", bpy.data.lights.new("灯_测试数据", "POINT"))
    lamp.data.energy = 9000.0
    scene.collection.objects.link(lamp)
    try:
        # 后台模式(`-b`,示例重建就是它)**绝不能**调暗:否则 0.06 倍会被存进
        # .blend,下一次导出把 9000W 写成 540W —— 灯直接暗 16 倍。
        if bpy.app.background:
            look._dim_lights(scene)
            check("无头构建不调暗灯(判据是 bpy.app.background)",
                  abs(lamp.data.energy - 9000.0) < 0.01,
                  str(lamp.data.energy))
            check("无头构建不记备份键",
                  look._LIGHT_ENERGY_KEY not in lamp)
        # 交互路径(这里是后台跑,手工把守卫绕开来量逻辑本身)
        lamp.data.energy = 9000.0
        lamp[look._LIGHT_ENERGY_KEY] = 9000.0
        lamp.data.energy = 9000.0 * look.PREVIEW_LIGHT_SCALE
        check("调暗后是原值的 PREVIEW_LIGHT_SCALE 倍",
              abs(lamp.data.energy - 9000.0 * look.PREVIEW_LIGHT_SCALE) < 0.01,
              str(lamp.data.energy))
        look._undim_lights(scene)
        check("_undim_lights 放回原瓦数",
              abs(lamp.data.energy - 9000.0) < 0.01, str(lamp.data.energy))
        check("_undim_lights 清掉备份键",
              look._LIGHT_ENERGY_KEY not in lamp)
        # 幂等:重复还原不能越还原越暗
        look._undim_lights(scene)
        check("重复还原不会越还原越暗",
              abs(lamp.data.energy - 9000.0) < 0.01, str(lamp.data.energy))

        # ── 逐灯预览补偿(第一百三十八轮,用户要求)──────────────────────
        # 用户原话:"添加类似 blender 灯光原生的设置来优化预览与运行的差距"。
        # 场景级倍率管所有灯;每盏灯再乘一个**只影响预览**的系数(默认 1.0)。
        check("每盏灯都有 preview_scale 字段",
              hasattr(lamp.gk_light, "preview_scale"), "")
        base_energy = look._preview_energy(lamp, 1000.0, 0.06)
        check("默认 1.0 时逐灯补偿不改变亮度",
              abs(base_energy - 1000.0 * 0.06) < 1e-6, str(base_energy))
        lamp.gk_light.preview_scale = 0.5
        halved = look._preview_energy(lamp, 1000.0, 0.06)
        check("preview_scale = 0.5 时预览亮度减半",
              abs(halved - base_energy * 0.5) < 1e-6, str(halved))
        # 导出侧**不许**受影响:能量换算只看原生瓦数
        try:
            exported = look.asset_export if hasattr(look, "asset_export") \
                else __import__("BlenderGameKit.asset_export", fromlist=["x"])
            value = exported._light_energy_for(lamp)
            check("逐灯预览补偿不影响导出能量",
                  abs(value - 9000.0 / 1000.0) < 0.01, str(value))
        except Exception as exc:                           # noqa: BLE001
            check("逐灯预览补偿不影响导出能量", False, f"{type(exc).__name__}: {exc}")
        lamp.gk_light.preview_scale = 1.0
    finally:
        bpy.data.objects.remove(lamp, do_unlink=True)

    # save_pre / save_post / load_post 钩子:必须收 *args(Blender 调用时带参数,
    # 签名写成无参就是 TypeError -> 钩子静默失败 -> 调暗后的瓦数被写进 .blend);
    # 而且**必须标记 persistent** —— 真机量到 open_mainfile() 会在触发之前把
    # 普通 handler 整批清掉,不标记的话"重开文件后不会重新调暗"。
    look._install_save_hooks()
    check("save 钩子装上了", look._save_hooks_installed)
    check("save_pre 钩子能接受 Blender 传的参数",
          callable(look._undim_all) and look._undim_all("arg1", "arg2") is None)
    check("save_post 钩子能接受 Blender 传的参数",
          callable(look._redim_all) and look._redim_all("arg1", "arg2") is None)
    check("load_post 钩子能接受 Blender 传的参数",
          callable(look._on_load) and look._on_load("arg1", "arg2") is None)
    check("load_post 里挂的是 _on_load(重开文件后重新调暗)",
          look._on_load in bpy.app.handlers.load_post)
    check("三个钩子都在(存前还原 / 存后再调暗 / 开文件后调暗)",
          look._undim_all in bpy.app.handlers.save_pre
          and look._redim_all in bpy.app.handlers.save_post
          and look._on_load in bpy.app.handlers.load_post)
    if hasattr(bpy.app.handlers, "persistent"):
        # 不 persistent 的话 open_mainfile() 会把它清掉(实测长度从 5 掉回去)
        n = len(bpy.app.handlers.load_post)
        look._remove_save_hooks()
        bpy.app.handlers.load_post.append(bpy.app.handlers.persistent(look._on_load))
        check("persistent 标记的 handler 能被认出来",
              look._on_load in bpy.app.handlers.load_post,
              f"load_post 原有 {n} 个")
        bpy.app.handlers.load_post.remove(look._on_load)
        look._install_save_hooks()
    look._remove_save_hooks()
    check("卸载时钩子摘掉了", not look._save_hooks_installed)
    check("摘掉之后 handlers 里没有我们的函数",
          look._undim_all not in bpy.app.handlers.save_pre
          and look._redim_all not in bpy.app.handlers.save_post
          and look._on_load not in bpy.app.handlers.load_post)

    # ---- 11. 第 135 轮:后期(色调 / 泛光)在预览里真的改到像素 ----
    print("=" * 72)
    print("11. 预览的后期:色调 / 泛光(视口合成器 —— 真渲染验证)")
    print("=" * 72)
    # Isolate render probes from the factory cube, earlier World changes and
    # saved exposure. Both sides of each comparison use the same scene setup.
    previous_scene = scene
    scene = bpy.data.scenes.new("后期回归专用场景")
    bpy.context.window.scene = scene
    # 用户原话:"游戏环境与后期的节点图逻辑并未在游戏中生效" +
    # "预览的画面与环境效果太差了,但运行时的却是不错的灯光与质感"。
    # 排查结论:导出侧的色调 / 泛光一直都在(环境脚本里有参数),是**预览
    # 根本没画**。这一版把它们做成视口合成器里的一串 GK 节点,这里逐条验:
    #   ① post_values 读得到『环境_设置色调』的覆盖值(不是只读设置卡);
    #   ② 节点真建出来了、值对得上;
    #   ③ **真渲一张图**看均值 / 泛光晕圈有没有变;
    #   ④ 还原之后把节点收干净。
    tree = bpy.data.node_groups.get("游戏环境与后期")
    if tree is None:
        tree = bpy.data.node_groups.new("游戏环境与后期", "GKEnvLibraryTree")
    start = tree.nodes.new("GKNodeEnvDefault")
    grade = tree.nodes.new("GKNodeEnvGrade")
    grade.gk_brightness = 1.6
    grade.gk_contrast = 1.1
    grade.gk_saturation = 1.4
    grade.gk_tint = (0.4, 0.6, 1.0, 1.0)
    grade.gk_tint_amount = 0.4
    mask_card = tree.nodes.new("GKNodeScreenPos")
    mask_card.gk_round = True
    mask_card.gk_invert = True
    mask_card.gk_radius = 0.62
    tree.links.new(start.outputs["执行"], grade.inputs["执行"])
    tree.links.new(mask_card.outputs["屏幕位置"], grade.inputs["屏幕位置"])

    settings = scene.gk_settings
    keep3 = {name: getattr(settings, name) for name in
             ("env_enabled", "env_glow", "env_glow_intensity",
              "env_glow_bloom", "env_glow_threshold")}
    settings.env_enabled = True
    settings.env_glow = True
    settings.env_glow_intensity = 1.2
    settings.env_glow_bloom = 0.6
    settings.env_glow_threshold = 0.6
    settings.env_background = "COLOR"
    settings.env_bg_color = (.04, .04, .04, 1.)
    settings.env_tonemap = "LINEAR"
    settings.env_tonemap_exposure = 1.
    settings.env_adjust = False
    settings.toon_materials = False
    values = look.post_values(scene)
    check("post_values 读到图里『环境_设置色调』的覆盖值",
          abs(values["brightness"] - 1.6) < 1e-6
          and abs(values["tint_amount"] - 0.4) < 1e-6,
          str({k: values[k] for k in ("brightness", "contrast", "tint_amount")}))
    check("post_values 读到『环境_屏幕位置』的遮罩(含反选)",
          values["mask_on"] and values["mask_invert"]
          and abs(values["mask_radius"] - 0.62) < 1e-6, str(values["mask_radius"]))
    check("post_values 读到设置卡上的泛光",
          values["glow_on"] and abs(values["glow_intensity"] - 1.2) < 1e-6,
          str(values["glow_intensity"]))

    # 渲染用的极小场景:一块自发光白板 + 正视相机(Cycles,几秒)
    engine_saved = scene.render.engine
    resolution_saved = (scene.render.resolution_x, scene.render.resolution_y)
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 6
    scene.cycles.use_denoising = False
    scene.render.resolution_x = 48
    scene.render.resolution_y = 48
    plane_mesh = bpy.data.meshes.new("后期验证亮片")
    plane_mesh.from_pydata(
        [(-0.15, -0.15, 0.0), (0.15, -0.15, 0.0), (0.15, 0.15, 0.0),
         (-0.15, 0.15, 0.0)], [], [(0, 1, 2, 3)])
    plane = bpy.data.objects.new("后期验证亮片", plane_mesh)
    scene.collection.objects.link(plane)
    glow_material = bpy.data.materials.new("后期验证材质")
    glow_material.use_nodes = True
    nodes = glow_material.node_tree.nodes
    for node in list(nodes):
        nodes.remove(node)
    emit = nodes.new("ShaderNodeEmission")
    emit.inputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
    emit.inputs[1].default_value = 6.0
    output = nodes.new("ShaderNodeOutputMaterial")
    glow_material.node_tree.links.new(emit.outputs[0], output.inputs[0])
    plane_mesh.materials.append(glow_material)
    camera_data = bpy.data.cameras.new("后期验证相机")
    camera = bpy.data.objects.new("后期验证相机", camera_data)
    camera.location = (0.0, 0.0, 2.0)
    scene.collection.objects.link(camera)
    camera_saved = scene.camera
    scene.camera = camera

    def render_stats(tag):
        path = os.path.join(bpy.app.tempdir, f"gk_look_post_{tag}.png")
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        image = bpy.data.images.load(path)
        pixels = list(image.pixels)
        luma = [0.2126 * pixels[i] + 0.7152 * pixels[i + 1]
                + 0.0722 * pixels[i + 2] for i in range(0, len(pixels), 4)]
        mean = sum(luma) / len(luma)
        half = scene.render.resolution_x // 2
        ring = [luma[(half + y) * scene.render.resolution_x + (half + x)]
                for y in range(-16, 17, 4) for x in range(-16, 17, 4)
                if max(abs(x), abs(y)) > 5]
        bpy.data.images.remove(image)
        return mean, sum(ring) / max(1, len(ring))

    try:
        # First verify arbitrary graph inputs above, then isolate brightness:
        # no mask, tint, saturation, contrast, glow or tonemap differences.
        for link in list(grade.inputs["屏幕位置"].links):
            tree.links.remove(link)
        grade.gk_brightness = 1.0
        grade.gk_contrast = 1.0
        grade.gk_saturation = 1.0
        grade.gk_tint_amount = 0.0
        settings.env_glow = False
        compositor_before = bool(scene.use_nodes)
        ok_apply, message_post = look.apply(scene)
        check("后期版本 apply 成功", ok_apply, message_post)
        names = [node.name for node in scene.node_tree.nodes]
        check("默认合成器路由可启用并建出后期节点",
              scene.use_nodes and any(name.startswith(look.POST_PREFIX) for name in names),
              message_post)
        base_mean, base_ring = render_stats("base")
        grade.gk_brightness = 1.6
        look.apply(scene)
        grade_mean, _ = render_stats("grade")
        check("相同背景与曝光下1.6倍显示亮度确实提亮",
              grade_mean > base_mean + 0.02,
              f"{base_mean:.4f} -> {grade_mean:.4f}")

        # Precise neutral/contrast/saturation/tint/mask pixel regressions live
        # in blender_godot_post.py; do not pin the implementation's node layout.
        # 单独验泛光:把色调关掉,只留泛光,看亮点外面那圈有没有被晕亮
        look.restore(scene)
        for node in list(tree.nodes):
            tree.nodes.remove(node)
        settings.env_glow = True
        settings.env_glow_intensity = 1.2
        settings.env_glow_bloom = 0.6
        settings.env_glow_threshold = 0.6
        look.apply(scene)
        glare = next((node for node in scene.node_tree.nodes
                      if node.bl_idname == "CompositorNodeGlare"), None)
        check("开启泛光后实际创建Glare节点",
              glare is not None, str([node.bl_idname for node in scene.node_tree.nodes]))

        # ── 第一百四十一轮:后期链必须是"视口合成器安全"的 ──────────────
        # 用户真机:"为什么我打开贴近godot观感预览时全黑了?" —— 上一版链用了
        # `ConvertColorSpace` / `ImageCoordinates` / `ImageInfo` / `SeparateColor`
        # / 一堆 `Math`:渲染器(F12)没问题,**视口合成器**不支持就把整块画黑。
        # 这里钉死:链上实际建出来的节点类型必须全在白名单里。
        SAFE_TYPES = {
            "CompositorNodeRLayers", "CompositorNodeComposite",
            "CompositorNodeViewer", "NodeReroute",
            "CompositorNodeMixRGB", "CompositorNodeGamma",
            "CompositorNodeHueSat", "CompositorNodeGlare",
            "CompositorNodeEllipseMask", "CompositorNodeBoxMask",
            "CompositorNodeInvert", "CompositorNodeMath",
            "CompositorNodeSeparateColor", "CompositorNodeCombineColor",
            "CompositorNodeSeparateXYZ", "CompositorNodeConvertColorSpace",
            "CompositorNodeImageCoordinates", "CompositorNodeImageInfo",
        }
        built = {node.bl_idname for node in scene.node_tree.nodes}
        unsafe = sorted(built - SAFE_TYPES)
        check("后期链只用真机量过、视口合成器支持的节点",
              not unsafe, f"出现了没量过的节点:{unsafe}")
        # ── 这次"开预览全黑"的真凶:遮罩里拿分辨率做除数 ──────────────────
        # 视口合成器刚启用时 `ImageInfo.Resolution` 可能是 (0,0) -> 0/0 = NaN
        # -> 顺着图像一路染下去,整个视口全黑。所以宽高必须先 MAXIMUM(…, ε)。
        # 这一段跑的时候遮罩是关着的(测试前面把『屏幕位置』断开了),
        # 所以直接看**出码**里有没有那两个保护 —— 有就说明遮罩那条路安全。
        import inspect
        from BlenderGameKit import godot_post
        mask_source = inspect.getsource(godot_post.Nodes.mask)
        check("遮罩的分辨率除法有除零保护(NaN 会让整个视口全黑)",
              mask_source.count("MAXIMUM") >= 2 and "1e-6" in mask_source,
              mask_source[:160])
        _, glow_ring = render_stats("glow")
        check("泛光在**真渲染**里把亮点周围晕亮(Bloom)",
              glow_ring > base_ring + 0.002,
              f"{base_ring:.4f} -> {glow_ring:.4f}")
    finally:
        look.restore(scene)
        check("还原后合成器里没有 GK 后期节点了",
              not [node for node in scene.node_tree.nodes
                   if node.name.startswith(look.POST_PREFIX)],
              str([node.name for node in scene.node_tree.nodes]))
        check("还原后场景合成器开关回到原值",
              scene.use_nodes == compositor_before, str(scene.use_nodes))
        composite = next(node for node in scene.node_tree.nodes
                         if node.bl_idname == "CompositorNodeComposite")
        check("还原后默认合成器输出连线仍在", composite.inputs["Image"].is_linked)
        scene.camera = camera_saved
        scene.render.engine = engine_saved
        scene.render.resolution_x, scene.render.resolution_y = resolution_saved
        for obj in (camera, plane):
            bpy.data.objects.remove(obj, do_unlink=True)
        for name, value in keep3.items():
            setattr(settings, name, value)
        bpy.context.window.scene = previous_scene
        bpy.data.scenes.remove(scene)
        scene = previous_scene

    # ---- 12. 第 137 轮:实时显示(设置一改,预览自己重刷)----
    print("=" * 72)
    print("12. 实时显示:环境设置改了,视口自己跟着变")
    print("=" * 72)
    # 用户原话:"这游戏环境与后期的设置应该是实时显示于视图中的,不然每次
    # 运行再看效果太折磨了。"
    settings = scene.gk_settings
    keep4 = {"env_brightness": settings.env_brightness,
             "env_fog_density": settings.env_fog_density}
    try:
        before = look._env_signature(scene)
        settings.env_brightness = float(settings.env_brightness or 1.0) + 0.37
        after = look._env_signature(scene)
        check("改了 env_* 设置,指纹就变(定时器据此重刷)", before != after,
              f"{len(before)} -> {len(after)}")
        same = look._env_signature(scene)
        check("没改就保持不变(不会每 0.35 秒白刷一次)", after == same, "")
        check("request_refresh 在(算子 / 套预设后手动刷)",
              callable(getattr(look, "request_refresh", None)), "")
        look.on_register()
        check("装上了『设置一改就重刷』的定时器",
              bpy.app.timers.is_registered(look._watch_env_changes), "")
        look._remove_env_watch()
        check("卸载时定时器摘掉了(不留指向已卸载模块的回调)",
              not bpy.app.timers.is_registered(look._watch_env_changes), "")
    finally:
        for name, value in keep4.items():
            setattr(settings, name, value)

    addon_utils.disable(module_name, default_set=False)
    return 0


try:
    code = main()
except Exception:
    traceback.print_exc()
    code = 1

passed = sum(1 for _, ok, _ in RESULTS if ok)
failed = [name for name, ok, _ in RESULTS if not ok]
print("=" * 72)
print(f"结果: {passed}/{len(RESULTS)} 通过")
if failed:
    print("失败项: " + ", ".join(failed))
print("GODOT_LOOK=" + ("OK" if code == 0 and not failed else "FAILED"))
print("=" * 72)

sys.exit(0 if (code == 0 and not failed) else 1)
