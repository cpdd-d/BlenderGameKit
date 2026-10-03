"""关卡示例的自查:打开 .blend -> 导出 -> 检查生成结果(第 22 轮)。

跑法:
    blender -b --python scripts/check_level_example.py

── 查什么 ────────────────────────────────────────────────────────
不是"能不能导出",而是**这个关卡该有的机制有没有真的生成**:
金币拾取、分数、HUD 刷新、掉出去回出生点、通关判定、对外端口。
生成的脚本落到 `_dist/level.gd`,再由 `scripts/verify_anim_system.ps1`
第 4 层交给真实 Godot 解析(语法错会在那一步被抓住)。
"""

import os
import re
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_GD = os.path.join(ROOT, "_dist", "level.gd")

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name}" + (f"  <- {detail}" if detail and not ok else ""))


def main():
    blend = os.path.join(ROOT, "examples", "demo_level", "关卡示例.blend")
    if not os.path.exists(blend):
        print(f"找不到: {blend}\n先跑: blender -b --python scripts/make_demo_level.py")
        return 1
    bpy.ops.wm.open_mainfile(filepath=blend)

    from bl_ext.user_default.blender_game_kit import codegen

    # ---- 场景内容 ----
    names = {obj.name for obj in bpy.data.objects}
    coins = sorted(n for n in names if n.startswith("金币"))
    check("场景里有 5 枚金币", len(coins) == 5, str(coins))
    check("场景里有地面 / 玩家 / 两台相机",
          {"地面", "示例角色", "侧视机位", "俯瞰机位"} <= names,
          str(sorted(names)))

    # ---- 节点图 ----
    logic = next((t for t in bpy.data.node_groups
                  if getattr(t, "bl_idname", "") == "GKGameLogicTree"), None)
    check("找到『玩家逻辑』节点图", logic is not None)
    if logic is None:
        return 1
    kinds = [n.bl_idname for n in logic.nodes]
    check("金币拾取用了『取值_距离』",
          kinds.count("GKNodeDistance") == 5, str(kinds.count("GKNodeDistance")))
    check("掉出去有复位逻辑(如果_否则 + 设置位置)",
          "GKNodeSetPosition" in kinds and "GKNodeGetPosition" in kinds)
    check("HUD 用『界面_文本』+『文本_拼接』",
          "GKNodeUiText" in kinds and "GKNodeTextJoin" in kinds)
    check("通关判定接了对外端口输出",
          "GKNodeLogicOutput" in kinds)

    ports = [p.port_name for p in getattr(logic, "gk_ports", [])]
    # 端口:两个游戏进度 + 两个视角(鼠标环绕用)
    check("对外端口齐全(金币数 / 通关 / 视角偏航 / 视角俯仰)",
          {"金币数", "通关", "视角偏航", "视角俯仰"} <= set(ports),
          str(ports))

    # ---- 导出 ----
    settings = getattr(bpy.context.scene, "gk_settings", None)
    options = {
        "dimension": "3",
        "base_class": "CharacterBody3D",
        "class_name": "LevelPlayer",
    }
    code, warnings = codegen.generate(logic, options)
    real = [w for w in warnings if "基类已从" not in str(w)]
    check("导出零告警", not real, str(real)[:300])

    check("生成的脚本 extends CharacterBody3D",
          "extends CharacterBody3D" in code)
    check("拾取判定生成了距离比较",
          "distance_to" in code and "1.3" in code)
    check("吃掉金币调用了 queue_free",
          "queue_free()" in code)
    # 加分是"读 -> 加一 -> 写回"(『变量_设置』没有自增选项)
    check("分数变量会 +1",
          ("+ 1" in code or "+= 1" in code) and "1.0" in code)
    check("HUD 文本会被刷新(拼接 + 赋值)",
          "text = " in code and "str(" in code)
    check("掉出去会回到出生点(赋值 global_position)",
          "global_position = Vector3(" in code)
    check("通关端口生成了变量与赋值",
          "输出_通关" in code and "bool" in code)
    # 端口变量类型要对(用户声明的是 int / bool)
    check("端口变量带正确类型",
          "输出_金币数: float" in code and "输出_通关: bool" in code)
    check("Esc 退出接上了", "get_tree().quit()" in code)
    check("鼠标转视角用了累积位移",
          "_gk_mouse_delta" in code)
    check("输入动作都进了映射(含 view_top / view_side / jump)",
          all(a in code for a in ("ACTION_VIEW_TOP", "ACTION_VIEW_SIDE",
                                  "ACTION_JUMP")))

    os.makedirs(os.path.dirname(OUT_GD), exist_ok=True)
    with open(OUT_GD, "w", encoding="utf-8") as handle:
        handle.write(code)
    print(f"[INFO] 关卡脚本写到: {OUT_GD}")

    # ---- 顺带导出成能跑的 Godot 工程(和 2.5D 示例一套流程)----
    try:
        from bl_ext.user_default.blender_game_kit import exporter
        result = exporter.export_tree(logic, settings={
            "dimension": "3", "base_class": "CharacterBody3D",
            "output_dir": os.path.join(ROOT, "examples", "demo_level",
                                       "关卡_godot"),
            "write_scene": True, "class_name": "LevelPlayer",
        }, write_scene=True)
        check("导出成 Godot 工程", bool(result.get("ok")),
              str(result.get("warnings"))[:200])
        print(f"[INFO] 导出落点: {result.get('directory')}")
    except Exception as exc:                              # noqa: BLE001
        check("导出成 Godot 工程", False, f"{type(exc).__name__}: {exc}")

    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    print()
    print(f"结果: {passed}/{len(RESULTS)} 通过")
    ok = passed == len(RESULTS)
    print("LEVEL_CHECK=" + ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
