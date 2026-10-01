"""在真实 Blender 里跑一遍:注册插件 -> 建节点树 -> 生成代码 -> 导出文件。

用法:
    blender -b --python tests/blender_smoke.py

注意:**不要加 --factory-startup**。工厂启动会忽略用户偏好设置，
从而让 Blender 完全看不到用户安装的扩展仓库
(表现为 addon_utils.modules() 里没有 bl_ext.<repo>.<pkg>)。
这个坑已经踩过一次，见 README。

退出码 0 表示全部通过。
"""

import os
import sys
import tempfile
import traceback

import addon_utils
import bpy

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" —— {detail}" if detail and not condition else ""))
    return bool(condition)


def find_addon_module():
    """在已安装的扩展/插件里找到 BlenderGameKit。"""
    for module in addon_utils.modules():
        name = getattr(module, "__name__", "")
        if "blender_game_kit" in name or getattr(module, "bl_info", {}).get("name", "").startswith("BlenderGameKit"):
            return name
    return None


def main():
    print("=" * 70)
    print("BlenderGameKit 冒烟测试")
    print("=" * 70)

    module_name = find_addon_module()
    if not check("找到插件模块", module_name, "未安装 BlenderGameKit"):
        return 1
    print(f"    模块名: {module_name}")

    try:
        addon_utils.enable(module_name, default_set=True, persistent=True)
    except Exception as exc:
        check("启用插件", False, f"{type(exc).__name__}: {exc}")
        return 1
    check("启用插件", True)

    # ---- 插件内容是否齐全 ----
    mod = sys.modules.get(module_name)
    check("插件已加载到 sys.modules", mod is not None)
    if mod is None:
        return 1

    from importlib import import_module

    codegen = import_module(f"{module_name}.codegen")
    exporter = import_module(f"{module_name}.exporter")
    node_lib = import_module(f"{module_name}.nodes.nodes")

    check(f"节点数量 ({len(node_lib.ALL_CLASSES)})", len(node_lib.ALL_CLASSES) >= 30)

    # 注意:bpy.types 的属性表只包含 RNA(C/C++)类型，动态注册的 Python 类
    # 通过 bpy.types.X 取不到。所以这里一律用"能不能真的用"来验证注册结果。
    check("场景设置已挂载", hasattr(bpy.types.Scene, "gk_settings"))

    # ---- 引脚类型:能不能真的建出 socket ----
    try:
        probe_tree = bpy.data.node_groups.new("__pin_probe__", "GKGameLogicTree")
        probe_node = probe_tree.nodes.new("GKNodeOnReady")
        flow_ok = any(getattr(s, "bl_idname", "") == "GKSocketFlow" for s in probe_node.outputs)
        bpy.data.node_groups.remove(probe_tree)
        check("执行流引脚可用", flow_ok)
    except Exception as exc:
        check("执行流引脚可用", False, f"{type(exc).__name__}: {exc}")

    # ---- 建一棵真实节点树 ----
    try:
        tree = bpy.data.node_groups.new("冒烟测试", "GKGameLogicTree")
        check("创建节点树", tree is not None)
    except Exception as exc:
        check("创建节点树", False, f"{type(exc).__name__}: {exc}")
        return 1

    def new_node(bl_idname, name, **props):
        node = tree.nodes.new(bl_idname)
        node.name = name
        for key, value in props.items():
            setattr(node, key, value)
        return node

    try:
        start = new_node("GKNodeOnReady", "开始")
        physics = new_node("GKNodeOnPhysicsProcess", "物理帧")
        direction = new_node("GKNodeGetVector", "方向输入")
        move = new_node("GKNodeMoveDirection", "移动", gk_speed=240.0)
        jump_key = new_node("GKNodeActionJustPressed", "跳跃键", gk_action="jump")
        branch = new_node("GKNodeBranch", "能跳吗")
        do_jump = new_node("GKNodeJump", "起跳", gk_jump_force=520.0)
        printer = new_node("GKNodePrint", "打印")
        check("创建 8 个节点", len(tree.nodes) == 8, f"实际 {len(tree.nodes)}")
    except Exception as exc:
        check("创建节点", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    # ---- 检查引脚真的建出来了 ----
    inputs = [socket.name for socket in move.inputs]
    outputs = [socket.name for socket in move.outputs]
    check("动作节点有执行输入", "执行" in inputs, str(inputs))
    check("动作节点有完成输出", "完成" in outputs, str(outputs))
    check("移动节点带速度属性", hasattr(move, "gk_speed") and abs(move.gk_speed - 240.0) < 1e-6)

    # ---- 连线(用真实 tree.links.new)，逐条校验确实建立了 ----
    link_problems = []

    def link(from_node, from_socket, to_node, to_socket):
        src = from_node.outputs.get(from_socket)
        dst = to_node.inputs.get(to_socket)
        if src is None or dst is None:
            raise KeyError(f"{from_node.name}.{from_socket} -> {to_node.name}.{to_socket}")
        before = len(tree.links)
        tree.links.new(src, dst)
        if len(tree.links) == before:
            link_problems.append(f"{from_node.name}.{from_socket} -> {to_node.name}.{to_socket}"
                                 f" (引脚类型 {src.bl_idname} -> {dst.bl_idname})")

    try:
        link(physics, "执行", move, "执行")
        link(direction, "方向", move, "方向")
        link(move, "完成", branch, "执行")
        link(jump_key, "刚按下", branch, "条件")
        link(branch, "是", do_jump, "执行")
        link(do_jump, "完成", printer, "执行")
        # 最后一条:**预期会被 Blender 拒绝**
        # 打印节点的"执行"输入已经被物理帧占用，而 Blender 的输入引脚
        # 只允许一条连线。这正是"两个事件不能共用一个下游节点"的原因，
        # 代码生成器会对此给出明确告警(见 test_shared_node_between_events_warns)。
        link(start, "执行", printer, "执行")
    except Exception as exc:
        check("连线", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    check("连线(6 条，第 7 条如预期被拒绝)", len(tree.links) == 6,
          f"实际 {len(tree.links)} 条")
    check("第二条连同一个输入引脚会被拒绝", len(link_problems) == 1, str(link_problems))

    # ---- 生成代码 ----
    try:
        code, warnings = codegen.generate(tree, {
            "dimension": "2", "base_class": "CharacterBody2D", "export_tunables": True,
        })
        check("生成代码", bool(code))
        check("生成代码含 _physics_process", "func _physics_process(delta: float) -> void:" in code)
        check("生成代码含 move_and_slide", "move_and_slide()" in code)
        check("生成代码含 get_vector", "Input.get_vector(ACTION_" in code)
        # 默认动作名是 move_*(WASD 开箱即用),不再是 Godot 内置的 ui_*(方向键)
        check("输入动作已抽成常量", 'const ACTION_MOVE_LEFT := "move_left"' in code
              and 'const ACTION_UI_LEFT := "ui_left"' not in code)
        check("跳跃力度暴露为 @export", "@export var jump_force: float = 520.0" in code)
        check("无告警", not warnings, str(warnings))
        print("-" * 70)
        print(code)
        print("-" * 70)
    except Exception as exc:
        check("生成代码", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    # ---- 真的写到磁盘 ----
    out_dir = os.path.join(tempfile.gettempdir(), "BlenderGameKit_smoke")
    try:
        settings = exporter.build_settings(bpy.context.scene)
        settings["output_dir"] = out_dir
        settings["dimension"] = "2"
        settings["base_class"] = "CharacterBody2D"
        settings["write_scene"] = True
        result = exporter.export_tree(tree, settings, write_scene=True)
        # 脚本落在 scripts/ 子目录里(项目结构:scripts/ scenes/ art/ …),
        # 用 result["written"] 里记的真实路径,别自己拼 ——
        # 以前这里拼的是 out_dir/<名字>,那个文件根本不存在,
        # 读到的空内容自然和生成的代码不相等(误报)。
        script_path = next((p for p in result["written"] if p.endswith(".gd")),
                           os.path.join(out_dir, "scripts", result["script_name"]))
        check("导出脚本文件", os.path.isfile(script_path), script_path)
        check("导出时无告警", not result["warnings"], str(result["warnings"]))

        # 读回来比对，确认磁盘内容与生成内容一致
        with open(script_path, encoding="utf-8") as handle:
            written = handle.read()
        check("文件内容与生成内容一致", written == code)
        check("文件是 UTF-8 且含中文注释", "由 BlenderGameKit 自动生成" in written)
        print(f"    导出目录: {out_dir}")
        print(f"    写入文件: {[os.path.basename(p) for p in result['written']]}")
    except Exception as exc:
        check("导出文件", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    # ---- 算子注册 ----
    for op_name in ("gk.export_script", "gk.preview_script", "gk.add_node",
                    "gk.auto_link", "gk.pick_target", "gk.set_target"):
        check(f"算子已注册 {op_name}", hasattr(bpy.ops, op_name.split(".")[0])
              and op_name.split(".")[1] in dir(getattr(bpy.ops, op_name.split(".")[0])))

    # ---- UI 注册:用"面板能不能被 Blender 找到"来验证 ----
    try:
        panel_names = [cls.__name__ for cls in bpy.types.Panel.__subclasses__()]
        check("侧栏面板已注册", "GK_PT_Main" in panel_names, str(panel_names)[:200])
    except Exception as exc:
        check("侧栏面板已注册", False, f"{type(exc).__name__}: {exc}")
    try:
        menu_names = [cls.__name__ for cls in bpy.types.Menu.__subclasses__()]
        check("添加节点菜单已注册", "GK_MT_AddNodeCategories" in menu_names)
    except Exception as exc:
        check("添加节点菜单已注册", False, f"{type(exc).__name__}: {exc}")
    categories = [cls.bl_label for cls in
                  __import__(f"{module_name}.ui", fromlist=["CATEGORY_MENUS"]).CATEGORY_MENUS]
    check(f"分类子菜单 ({len(categories)})", len(categories) >= 8, str(categories))
    # 动态菜单类很容易静默注册失败，必须逐个确认拿到了 bl_rna
    ui_module = __import__(f"{module_name}.ui", fromlist=["CATEGORY_MENUS"])
    unregistered = [cls.__name__ for cls in ui_module.CATEGORY_MENUS if not hasattr(cls, "bl_rna")]
    check("所有分类子菜单都注册成功", not unregistered, str(unregistered))

    # ---- 卸载不报错 ----
    try:
        addon_utils.disable(module_name, default_set=True)
        check("卸载插件", True)
    except Exception as exc:
        check("卸载插件", False, f"{type(exc).__name__}: {exc}")

    return 0


try:
    code = main()
except Exception:
    traceback.print_exc()
    code = 1

passed = sum(1 for _, ok, _ in RESULTS if ok)
failed = [name for name, ok, _ in RESULTS if not ok]
print("=" * 70)
print(f"结果: {passed}/{len(RESULTS)} 通过")
if failed:
    print("失败项: " + ", ".join(failed))
print("SMOKE_RESULT=" + ("OK" if code == 0 and not failed else "FAILED"))
print("=" * 70)

sys.exit(0 if (code == 0 and not failed) else 1)
