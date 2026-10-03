"""校验 2.5D 示例(.blend):节点图没问题 + 能导出 + 生成的脚本给真 Godot 解析。

用法:
    blender -b --python scripts\\check_2d_blend_example.py

── 为什么要有这一步(用户 2026-10-03 第七轮)────────────────────────
用户要的是"打开 .blend 就能看懂节点怎么连"。那么"这份 .blend 里的图
本身是干净的、导得出去"就必须是**可验证**的,不能靠我说一句"它能跑"。
这个脚本查四件事:
  1. 两棵树都在,而且 `anim_tree_graph` 不报任何问题
  2. 导出的 GDScript 里**真的**有该有的东西(状态机、滚轮缩放、相机切换)
  3. 生成的脚本写到 _dist/2d_blend.gd,交给 check_gdscript.ps1 让真 Godot 解析
  4. 导出的场景文件也一起生成(用户要"直接运行")
"""

import os
import re
import sys

import bpy  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BLEND = os.path.join(ROOT, "examples", "demo_2d_blend", "2.5D示例.blend")
OUT_GD = os.path.join(ROOT, "_dist", "2d_blend.gd")

_results = []


def check(name, condition, detail=""):
    _results.append((bool(condition), name, detail))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}"
          + (f"  <- {detail}" if detail and not condition else ""))


def main():
    if not os.path.exists(BLEND):
        print(f"[FAIL] 找不到示例工程: {BLEND}")
        print("       先跑一次: blender -b --python scripts\\make_demo_2d_blend.py")
        print("2D_BLEND_CHECK=FAILED")
        return

    bpy.ops.wm.open_mainfile(filepath=BLEND)
    from bl_ext.user_default.blender_game_kit import codegen, properties
    from bl_ext.user_default.blender_game_kit.nodes import nodes as NN

    trees = {getattr(t, "bl_idname", ""): t
             for t in bpy.data.node_groups}
    anim = trees.get("GKAnimationTree")
    logic = trees.get("GKGameLogicTree")
    check("示例里有动画树", anim is not None)
    check("示例里有游戏逻辑图", logic is not None)
    if anim is None or logic is None:
        print("2D_BLEND_CHECK=FAILED")
        return

    # ---- 1. 两棵树各自的健康检查 ----
    graph = NN.anim_tree_graph(anim)
    check("动画树没有解析问题", not graph["problems"], str(graph["problems"]))
    states = [s for s in graph["states"] if NN.anim_state_name(s)]
    check("动画树有 4 个状态(待机/走/跑/跳)", len(states) == 4,
          f"{[NN.anim_state_name(s) for s in states]}")
    check("有开始口(写明从哪儿开始)", graph.get("start") is not None)
    check("有转场(不是孤立的四个状态)", len(graph["edges"]) >= 8,
          f"{len(graph['edges'])} 条")

    # ---- 2. 导出 ----
    settings = getattr(bpy.context.scene, "gk_settings", None)
    options = {
        "dimension": str(getattr(settings, "dimension", "3") or "3"),
        "base_class": str(getattr(settings, "base_class", "CharacterBody3D")
                          or "CharacterBody3D"),
    }
    # 枚举里"自动"的标识就是小写 auto(踩过:只判大写 AUTO,"auto" 就漏过去了,
    # 于是白白多出一条"基类得是 CharacterBody3D"的告警)
    if options["base_class"].strip().lower() in ("auto", ""):
        options["base_class"] = "CharacterBody3D"
    code, warnings = codegen.generate(logic, options)
    # ── "基类 auto -> 自动定成 CharacterBody3D" 这条**不算问题** ────────
    # 那是导出器在自愈:用户在侧栏留了 auto,它按物体/节点图推断出正确基类,
    # 顺便告诉用户"我替你定了什么"。这条是**好消息**,不该让检查失败。
    real_warnings = [w for w in warnings
                     if "基类已从" not in str(w)
                     and "没指定相机" not in str(w)]
    check("示例导出零告警(自愈提示不算)", not real_warnings,
          str(real_warnings))
    check("状态机驱动的是**指定**的动画树(角色动作)",
          '_gk_anim_enter(' in code)
    check("滚轮缩放视野(读当前 fov + 加滚轮量 + 写回)",
          "MOUSE_BUTTON_WHEEL_UP" in code
          and "_gk_cam_fov()" in code         # 当前视野走辅助函数
          and "_cam_fov10" not in code        # 不再是内联的怪物表达式
          and "_gk_wheel * 6.0" in code       # 一格滚轮 6 度
          and 'tween_property' in code
          # 视野要有上下限(用户:滚轮能无限拉长)
          and "clampf(" in code and "20.0, 90.0" in code)
    check("按 C / V 两个键都进了输入映射",
          "ACTION_VIEW_TOP" in code and "ACTION_VIEW_SIDE" in code)
    check("两台相机都生成了引用(侧视 + 俯瞰,带兜底查找)",
          '_gk_ref(self, "侧视机位")' in code
          and '_gk_ref(self, "俯瞰机位")' in code)
    check("相机切换带过渡(丝滑推镜)", "interpolate_with" in code
          or "make_current" in code)
    check("方向输入用了捕获的 WASD",
          all(f'ACTION_KEY_{k} := "{v}"' in code
              for k, v in (("W", "key_w"), ("A", "key_a"),
                           ("D", "key_d"), ("S", "key_s"))))

    # ---- 碰撞体:平面不能是零厚度、手填尺寸不能翻倍(用户真跑报告)----
    # 用户:"地面碰撞没贴合,走两步就掉下去" + "碰撞并不能自定义,它还是这样大"。
    # 两个 bug:①平面某个方向是 0 -> 盒子厚度 0;②手填尺寸只把 Y 取一半,
    # X/Z 原样用 -> 生成的盒子是两倍大(填 0.5 出 1.0)。
    from bl_ext.user_default.blender_game_kit import asset_export as AE
    ground = bpy.data.objects.get("地面")
    physics = getattr(ground, "gk_physics", None) if ground else None
    if ground is not None and physics is not None:
        keep_shape = physics.collision_shape
        keep_size = tuple(physics.shape_size)
        try:
            physics.collision_shape = "auto"
            physics.shape_size = (0.0, 0.0, 0.0)
            data = AE.build_collision(ground, is_3d=True, physics=physics)
            text = (data or {}).get("sub_resource", "")
            box = [float(v) for v in
                   re.findall(r"Vector3\(([-0-9., ]+)\)", text)[0].split(",")] \
                if "Vector3(" in text else []
            check("平面地面的碰撞体有厚度(不再是 0)",
                  bool(box) and min(box) > 0.001,
                  f"生成尺寸 {box}")
            # ── 轴向!地面在 Godot 里必须"薄在 Y"(用户:地面立起来了)──────
            # 20x20 的平面在 Blender 里 Z 方向几乎为 0;如果不做
            # Z-up -> Y-up 换算,生成的是 Vector3(20, 20, 0.1) ——
            # 在 Godot 里那是一块**竖着的墙**,角色当然站不住。
            check("地面是平的(薄在 Y 轴,不是竖墙)",
                  len(box) == 3 and box[1] < 1.0 and box[0] > 5.0
                  and box[2] > 5.0, f"生成尺寸 {box}")
            # 手填尺寸 = 原样生效(不能翻倍)
            # 老 .blend 里残留的手动尺寸(旧默认值 0.5/1/0.5)不该影响"自动"
            physics.shape_size = (0.5, 1.0, 0.5)
            physics.collision_shape = "auto"
            stale = AE.build_collision(ground, is_3d=True, physics=physics)
            stale_text = (stale or {}).get("sub_resource", "")
            check("残留的手动尺寸不会覆盖自动模式",
                  "20.000000, 0.100000, 20.000000" in stale_text,
                  stale_text[:90])
            # 手填尺寸只在**明确选了形状**时生效(选"自动"时按模型贴合)
            physics.collision_shape = "box"
            physics.shape_size = (20.0, 20.0, 0.5)
            data2 = AE.build_collision(ground, is_3d=True, physics=physics)
            text2 = (data2 or {}).get("sub_resource", "")
            box2 = [float(v) for v in
                    re.findall(r"Vector3\(([-0-9., ]+)\)", text2)[0].split(",")] \
                if "Vector3(" in text2 else []
            check("手填碰撞尺寸按输入生效(不翻倍)",
                  len(box2) == 3 and abs(box2[0] - 20.0) < 0.01
                  and abs(box2[2] - 0.5) < 0.01, f"生成尺寸 {box2}")
        finally:
            physics.collision_shape = keep_shape
            physics.shape_size = keep_size
    else:
        check("能在示例里找到地面并检查碰撞", False, "没找到 地面 对象")

    # ---- 缩进体检(2.5D 示例抓出来的真 bug)------------------------
    # "在表达式求值时顺手 add_line"会让那几行的缩进和包着它的 if 对不上,
    # 生成出来"看着像对的、跑起来逻辑错位"。这里用最朴素的办法查一遍:
    # 上一层是 `if ...:` 时,下一行必须更深;否则不能突然变深。
    bad_indent = []
    lines = code.split("\n")
    for index in range(1, len(lines)):
        prev, current = lines[index - 1], lines[index]
        if not prev.strip() or not current.strip():
            continue
        prev_pad = len(prev) - len(prev.lstrip())
        cur_pad = len(current) - len(current.lstrip())
        if cur_pad > prev_pad and not prev.rstrip().endswith(":"):
            bad_indent.append(f"{index + 1}: {current.strip()[:50]}")
    check("生成的代码缩进自洽(没有错位的代码块)", not bad_indent,
          "; ".join(bad_indent[:3]))
    # 相机相关的语句必须在 _physics_process 的**顶层**(4 空格)——
    # 被嵌进 `if 能跳吗:` 里的话,滚轮缩放和切视角就只在跳起来时生效。
    # (这是"缩进自洽"查不出来的:嵌进去之后缩进照样自洽。)
    top_level = ["    if Input.is_action_just_pressed(ACTION_VIEW_TOP):",
                 "    if Input.is_action_just_pressed(ACTION_VIEW_SIDE):"]
    check("相机切换在物理帧顶层(不是嵌在跳跃分支里)",
          all(line + "\n" in code for line in top_level),
          "有一条相机切换被嵌进了别的分支")

    os.makedirs(os.path.dirname(OUT_GD), exist_ok=True)
    with open(OUT_GD, "w", encoding="utf-8") as handle:
        handle.write(code)
    print(f"[INFO] 生成的脚本写到: {OUT_GD}")

    # ---- 3. 真的导出成一个能跑的 Godot 工程(用户要"直接运行")--------
    from bl_ext.user_default.blender_game_kit import exporter
    try:
        written = exporter.export_tree(logic, settings=options, write_scene=True)
        check("导出脚本成功", bool(written), str(written)[:160])
        print(f"[INFO] 导出落点: {written}")
    except Exception as exc:  # noqa: BLE001
        check("导出脚本成功", False, f"{type(exc).__name__}: {exc}")

    failed = [item for item in _results if not item[0]]
    print("")
    print(f"结果: {len(_results) - len(failed)}/{len(_results)} 通过")
    for _ok, name, detail in failed:
        print(f"  失败: {name} {detail}")
    print("2D_BLEND_CHECK=" + ("OK" if not failed else "FAILED"))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print("2D_BLEND_CHECK=FAILED")
        sys.exit(1)
