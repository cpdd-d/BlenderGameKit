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
        # 冒烟场景里**没有相机** -> 导出会提示"运行时会造一台自由飞行观察机"。
        # 那是**好消息**(告诉你没相机时也能看见画面),不是错误;
        # 和"基类自愈"一样属于信息类提示,不该让冒烟失败。
        real_warnings = [w for w in result["warnings"]
                         if "自由飞行观察机" not in str(w)]
        check("导出时无告警", not real_warnings, str(real_warnings))

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

    # ---- 动作分类:打标签 -> 导出成常量表 ----
    # 覆盖 ui/operator 那条链路:`gk.tag_anim_category` 真的能写进 Action 的
    # 自定义属性,并且导出时变成 GK_ANIM_BY_CATEGORY 常量表。
    try:
        tagged = bpy.data.actions.new("冒烟动作")
        bpy.ops.gk.tag_anim_category(action_name=tagged.name, category="攻击")
        check("动作分类写进 Action",
              tagged.get("gk_anim_category") == "攻击",
              str(tagged.get("gk_anim_category")))
        code2, _w2 = codegen.generate(tree, {
            "dimension": "2", "base_class": "CharacterBody2D",
            "export_tunables": True,
        })
        check("导出含分类常量表",
              'const GK_ANIM_BY_CATEGORY := {' in code2
              and '"攻击": ["冒烟动作"]' in code2)
        bpy.data.actions.remove(tagged)
    except Exception as exc:
        check("动作分类链路", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 新建动画树不报错 ----
    # 踩过的坑:创建起始状态时给节点赋值 `gk_is_default`(该属性**从未定义**),
    # Blender 直接 AttributeError ——「打开/新建动画树」这条路一走到新建分支就崩。
    try:
        bpy.ops.gk.open_anim_tree()
        trees = [g for g in bpy.data.node_groups
                 if getattr(g, "bl_idname", "") == "GKAnimationTree"]
        check("新建动画树不报错", bool(trees), str(len(trees)))
        # 起始状态由**连线拓扑**决定(codegen._anim_tree_entry),不是勾选框
        entry, entries = codegen._anim_tree_entry(trees[0] if trees else None)
        check("动画树有起始状态", entry is not None and len(entries) >= 1)
    except Exception as exc:
        check("新建动画树不报错", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 动画树自定义变量:数据模型 -> 取/设节点 -> 导出带初值的成员变量 ----
    # 仿虚幻蓝图:变量属于这一"个"动画蓝图(这张树),不是"被谁用到才存在"。
    # 这条路一次覆盖三处:GKAnimVariable 集合、GKNodeAnimGetVar/SetVar 节点、
    # codegen 按类型 + 初值生成成员变量。
    try:
        anim_tree = next((g for g in bpy.data.node_groups
                          if getattr(g, "bl_idname", "") == "GKAnimationTree"), None)
        check("找到动画树", anim_tree is not None)

        # 1) 数据模型:加一个布尔变量并写初值(和侧栏『添加变量』算子同一条路)
        # 名字用英文:GDScript 的标识符只认 ASCII,中文名会被压成 `v`
        # (codegen 会就此告警,见 test_codegen.TestAnimTreeVariables)
        var = anim_tree.gk_variables.add()
        var.var_name = "invincible"
        var.var_type = "bool"
        var.default_value = "真"          # 中文初值也要能解析成 true
        check("动画树变量可增删", len(anim_tree.gk_variables) == 1
              and str(anim_tree.gk_variables[0].var_name) == "invincible")

        # 2) 节点:取变量 / 设变量 都能在这张动画树上建出来
        get_node = anim_tree.nodes.new("GKNodeAnimGetVar")
        get_node.gk_var_name = "invincible"
        set_node = anim_tree.nodes.new("GKNodeAnimSetVar")
        set_node.gk_var_name = "invincible"
        check("取/设变量节点可用",
              get_node.outputs.get("值") is not None
              and set_node.inputs.get("执行") is not None
              and set_node.inputs.get("值") is not None)

        # 2b) 逻辑节点(与/或/非)也要能在动画树里拉出来当转场条件
        check("逻辑节点可用在动画树",
              node_lib.GKNodeLogic.poll(anim_tree)
              and node_lib.GKNodeCompare.poll(anim_tree))

        # 3) codegen:玩法图里挂一个『动画_状态机』驱动这张树,
        #    变量必须落地成 var 无敌: bool = true
        #    (『开始』的执行输出还没接线,正好拿来启动它)
        sm_node = tree.nodes.new("GKNodeAnimStateMachineRef")
        sm_node.name = "状态机"
        sm_node.gk_tree_name = anim_tree.name
        tree.links.new(start.outputs["执行"], sm_node.inputs["执行"])
        code3, _w3 = codegen.generate(tree, {
            "dimension": "2", "base_class": "CharacterBody2D",
            "export_tunables": True,
        })
        check("动画变量导出成带初值的成员变量",
              "var invincible: bool = true" in code3,
              [line for line in code3.splitlines() if "invincible" in line][:3])
    except Exception as exc:
        check("动画树变量链路", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 算子注册 ----
    for op_name in ("gk.export_script", "gk.preview_script", "gk.add_node",
                    "gk.auto_link", "gk.pick_target", "gk.set_target",
                    "gk.tag_anim_category", "gk.pick_anim_name",
                    "gk.pick_anim_tree", "gk.anim_var_add", "gk.anim_var_remove",
                    "gk.anim_var_make_node", "gk.anim_var_pick"):
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

    # ---- 右键菜单的排布(用户 2026-10-03,照 UPBGE)------------------
    props_module = __import__(f"{module_name}.properties", fromlist=["node_menu_tree"])
    menu_tree = list(props_module.node_menu_tree(None))
    groups = {category: subs for category, subs in menu_tree}
    input_subs = [name for name, _classes in (groups.get("输入") or [])]
    check("『输入』分成了 键盘 / 鼠标 / 手柄 / 触屏",
          all(want in input_subs for want in ("键盘", "鼠标", "手柄", "触屏")),
          str(input_subs))
    calc_subs = [name for name, _classes in (groups.get("运算") or [])]
    check("『运算』分成了 常量 / 数学 / 向量 / 逻辑 / 转换",
          all(want in calc_subs for want in ("常量", "数学", "向量", "逻辑", "转换")),
          str(calc_subs))
    mouse_nodes = dict(groups.get("输入") or []).get("鼠标", [])
    check(f"鼠标节点 ({len(mouse_nodes)})", len(mouse_nodes) >= 5,
          ", ".join(cls.bl_label for cls in mouse_nodes))

    # ---- "按一下键盘就绑定"的算子(照 UPBGE 的 Press & Choose)--------
    check("『按一下键』算子已注册",
          "GK_OT_CaptureKey" in [cls.__name__ for cls in bpy.types.Operator.__subclasses__()])
    # 直接调 `apply_captured_key`(模拟按了一下 W)——
    # 算子本身不能直接 new,所以绑键逻辑特意抽成了普通函数
    io_module = __import__(f"{module_name}.operator_io",
                           fromlist=["apply_captured_key"])
    tree = None
    for group in bpy.data.node_groups:
        if getattr(group, "bl_idname", "") == "GKGameLogicTree":
            tree = group
            break
    if tree is None:
        tree = bpy.data.node_groups.new("GK按键测试", "GKGameLogicTree")
    tree.use_fake_user = True
    key_node = tree.nodes.new("GKNodeKey")
    key_node.name = "按一下"
    # 让 props.current_tree(context) 能找到这张树 —— 它按"当前活动的节点树"
    # 找,没有界面时回退到 scene 上的设置。设不上也没关系:
    # apply_captured_key 找不到树会返回失败,我们据实报出来。
    try:
        for attr in ("gk_active_tree", "gk_tree_name"):
            if hasattr(bpy.context.scene, attr):
                setattr(bpy.context.scene, attr, tree.name)
    except Exception:  # noqa: BLE001
        pass
    ok, message = io_module.apply_captured_key(bpy.context, key_node.name,
                                               "W", tree=tree)
    check("捕获到的按键写进了节点", ok and key_node.gk_key == "W",
          f"gk_key = {key_node.gk_key!r} ({message})")
    check("动作名按按键自动生成", key_node.gk_action == "key_w",
          f"gk_action = {key_node.gk_action!r}")
    # 改绑另一个键 -> 自动生成的动作名要跟着变(否则看着像没生效)
    io_module.apply_captured_key(bpy.context, key_node.name, "Q", tree=tree)
    check("改绑按键后动作名跟着变", key_node.gk_action == "key_q",
          f"gk_action = {key_node.gk_action!r}")

    # ---- "等按键"到底认哪些事件(2026-10-03 用户报"未正常工作")--------
    # 病根:`event.type == "KEYDOWN"` —— Blender 里**没有**这个类型,
    # 按字母键时 event.type 就是键名("W")、event.value 是 "PRESS"。
    # 所以模态一直挂着,状态栏那句提示不消失。
    def _event(kind, value="PRESS"):
        return type("E", (), {"type": kind, "value": value})()

    cases = [
        (_event("W"), ("W", False), "按 W"),
        (_event("SPACE"), ("SPACE", False), "按空格"),
        (_event("LEFT_SHIFT"), ("LEFT_SHIFT", False), "按左 Shift"),
        (_event("A", "RELEASE"), (None, False), "松开 A 不算按键"),
        (_event("MOUSEMOVE", "NOTHING"), (None, False), "鼠标移动要忽略"),
        (_event("TIMER", "NOTHING"), (None, False), "计时器要忽略"),
        (_event("LEFTMOUSE"), (None, False), "鼠标键不走键盘节点"),
        (_event("ESC"), (None, True), "Esc = 取消"),
    ]
    bad = []
    for event, want, label in cases:
        got = io_module.key_from_event(event)
        if got != want:
            bad.append(f"{label}: 期望 {want} 得到 {got}")
    check("『等按键』认得对(8 种事件)", not bad, "; ".join(bad))

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
