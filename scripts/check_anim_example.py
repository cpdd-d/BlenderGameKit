"""校验示例工程(examples/demo_anim_tree/动画状态机示例.blend)真的能用。

跑法:
    blender -b --python scripts\\check_anim_example.py

查三件事:
  1. 示例里的动画树**没有**任何问题(导出时不该出现告警)
  2. 生成的 GDScript 里有"多条出线"和"自定义变量当条件"的证据
  3. 把生成的脚本写到 _dist/anim_example.gd,交给
     scripts/check_gdscript.ps1 让真 Godot 解析
"""

import os
import sys

import bpy  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BLEND = os.path.join(ROOT, "examples", "demo_anim_tree", "动画状态机示例.blend")
OUT_GD = os.path.join(ROOT, "_dist", "anim_example.gd")

# ── 插件自己要先能被 import(踩过的坑)────────────────────────────
# Blender 里跑脚本时 `import BlenderGameKit` 不一定能找到 —— 装成扩展时
# 它是 Blender 内部加载的模块,不走 sys.path。这里补一次仓库根目录,
# 保证脚本里 `from BlenderGameKit import project` 这类用法在两种装法下都行。
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_results = []


def check(name, condition, detail=""):
    _results.append((bool(condition), name, detail))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}"
          + (f"  <- {detail}" if detail and not condition else ""))


def main():
    if not os.path.exists(BLEND):
        print(f"[FAIL] 找不到示例工程: {BLEND}")
        print("       先跑一次: blender -b --python scripts\\make_anim_example.py")
        print("ANIM_EXAMPLE_CHECK=FAILED")
        return

    bpy.ops.wm.open_mainfile(filepath=BLEND)

    from bl_ext.user_default.blender_game_kit import codegen, properties
    from bl_ext.user_default.blender_game_kit.nodes import nodes as NN

    anim = next((g for g in bpy.data.node_groups
                 if getattr(g, "bl_idname", "") == "GKAnimationTree"), None)
    logic = next((g for g in bpy.data.node_groups
                  if getattr(g, "bl_idname", "") == "GKGameLogicTree"), None)
    check("示例里有动画树", anim is not None)
    check("示例里有游戏逻辑图", logic is not None)
    if anim is None or logic is None:
        print("ANIM_EXAMPLE_CHECK=FAILED")
        return

    graph = NN.anim_tree_graph(anim)
    named = [s for s in graph["states"] if NN.anim_state_name(s)]
    check("状态数 >= 5", len(named) >= 5, f"{len(named)} 个")
    check("图里没有问题(导出时不会有告警)", not graph["problems"],
          str(graph["problems"]))

    # 一个状态多条出线
    from collections import Counter
    counts = Counter(NN.anim_state_name(e["from"]) for e in graph["edges"])
    multi = [name for name, count in counts.items() if count > 1]
    check("有状态接了多条转场", bool(multi), f"多条出线的状态: {multi}")
    check("有多条出线的状态 >= 3 个", len(multi) >= 3, f"{multi}")

    # 直连(播完就切)
    direct = [e for e in graph["edges"] if e.get("transition") is None]
    check("有状态直连(播完就切)", bool(direct), f"{len(direct)} 条")

    # 自定义变量当条件
    getters = [n for n in anim.nodes if n.bl_idname == "GKNodeAnimGetVar"]
    used = [e for e in graph["edges"]
            if e.get("transition") is not None
            and NN.link_sources(e["transition"], "条件")
            and any(getattr(src, "bl_idname", "") in (
                "GKNodeAnimGetVar", "GKNodeLogic", "GKNodeCompare")
                for src in [NN.link_sources(e["transition"], "条件")[0]])]
    check("变量/逻辑节点被接进了『条件』引脚", bool(used),
          f"取变量节点 {len(getters)} 个")
    check("动画树里声明了布尔变量",
          any(str(v.var_type) == "bool" for v in anim.gk_variables),
          str([(v.var_name, v.var_type) for v in anim.gk_variables]))

    options = {"dimension": "3", "base_class": "CharacterBody3D"}
    settings = getattr(bpy.context.scene, "gk_settings", None)
    if settings is not None:
        options = {"dimension": str(getattr(settings, "dimension", "3")),
                   "base_class": str(getattr(settings, "base_class",
                                             "CharacterBody3D"))}
        if options["base_class"] in ("AUTO", ""):
            options["base_class"] = "CharacterBody3D"
    code, warnings = codegen.generate(logic, options)
    check("示例导出零告警", not warnings, str(warnings))
    check("生成了多出线的 if/elif", code.count("elif anim_state ==") >= 2)
    check("变量真的在条件里用上", "is_hurt" in code or "is_attacking" in code)
    check("生成了播完就切的信号机制",
          "animation_finished.connect(" in code)
    # ---- 第三轮新功能 ----
    check("开始口生成了『开始 / 结束』闸门",
          "_gk_anim_started" in code and "_gk_anim_done" in code)
    check("结束口生成了收工函数", "func _gk_anim_finish()" in code)
    check("玩法图读到了动画的布尔状态",
          "if _gk_anim_done:" in code)
    # ---- 第四轮:摄像机套件 ----
    check("弹簧臂每帧打射线(不穿墙)",
          "intersect_ray" in code
          and "PhysicsRayQueryParameters3D.create" in code)
    check("相机启用/禁用都在(启用 = make_current,禁用 = clear_current)",
          ".make_current()" in code and ".clear_current()" in code
          and ".is_current()" in code)
    check("相机_设置(视野 / 视距 至少改一样)生成了",
          ('"fov"' in code or ".fov = " in code
           or "global_transform.basis.z" in code))

    # ── "在 Blender 里按一下键 -> Godot 里真的绑上"整条链 ──────────────
    # 『输入_按键』捕获的是键名(比如 C),导出时要翻成 Godot 的 keycode
    # 写进 project.godot。这里用**真的写入器**跑一遍,直接查 C 的键值。
    used = list(getattr(codegen, "last_used_actions", []) or [])
    pairs = dict((item[0], item[1]) if isinstance(item, (tuple, list))
                 else (item, "") for item in used)
    check("捕获到的按键跟着导出走(switch_camera -> C)",
          pairs.get("switch_camera") == "C", str(pairs.get("switch_camera")))
    import tempfile
    # 用**同一个模块对象**里的 project(它和 codegen 是一套),
    # 不要再 `from BlenderGameKit import ...` —— 扩展装法下那是另一个
    # 模块实例,两边状态不共享(踩过一次:ModuleNotFoundError)
    from bl_ext.user_default.blender_game_kit import project as gk_project
    probe_dir = tempfile.mkdtemp(prefix="gk_keys_")
    with open(os.path.join(probe_dir, "project.godot"), "w",
              encoding="utf-8") as handle:
        handle.write("config_version=5\n")
    written = gk_project.set_input_actions(probe_dir, used)
    with open(os.path.join(probe_dir, "project.godot"), encoding="utf-8") as h:
        manifest = h.read()
    check(f"输入映射写进去了({written} 个动作)",
          "switch_camera={" in manifest)
    check("C 键真的绑上了(physical_keycode = 67)",
          '"physical_keycode":67' in manifest)

    os.makedirs(os.path.dirname(OUT_GD), exist_ok=True)
    with open(OUT_GD, "w", encoding="utf-8") as handle:
        handle.write(code)
    print(f"[INFO] 生成的脚本写到: {OUT_GD}")

    failed = [item for item in _results if not item[0]]
    print("")
    print(f"结果: {len(_results) - len(failed)}/{len(_results)} 通过")
    for _ok, name, detail in failed:
        print(f"  失败: {name} {detail}")
    print("ANIM_EXAMPLE_CHECK=" + ("OK" if not failed else "FAILED"))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"ANIM_EXAMPLE_CHECK=FAILED ({type(exc).__name__}: {exc})")
        sys.exit(1)
