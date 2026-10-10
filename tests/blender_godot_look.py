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
    for module in addon_utils.modules():
        if "blender_game_kit" in getattr(module, "__name__", ""):
            return module.__name__
    return None


def main():
    module_name = find_addon()
    if not check("找到插件", module_name, "未安装"):
        return 1
    addon_utils.enable(module_name, default_set=True, persistent=True)
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
          world.name == look.WORLD_NAME,
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
                         "env_background", "env_bg_color")}
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
    finally:
        for name, value in keep.items():
            setattr(settings, name, value)

    addon_utils.disable(module_name, default_set=True)
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
