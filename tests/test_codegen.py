"""BlenderGameKit 代码生成回归测试(不需要 Blender)。

跑法:
    python tests/test_codegen.py
或者交给 Blender 的 Python 跑(见 run_tests.bat)，效果一样。
"""

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import gk_stub  # noqa: E402

gk_stub.install()
ADDON = gk_stub.load_addon(os.path.join(ROOT, "BlenderGameKit"))

from BlenderGameKit import codegen  # noqa: E402
from BlenderGameKit.nodes import nodes as NN  # noqa: E402

V2 = dict(dimension="2", base_class="CharacterBody2D")


def make_tree():
    return gk_stub.StubNodeTree("测试图")


def gen(tree, options=None):
    return codegen.generate(tree, options)


class Base(unittest.TestCase):
    def assert_no_chinese_identifiers(self, code):
        """生成代码里不应出现中文标识符(注释与字符串字面量除外)。

        中文字符串是合法的(print("你好"))，但中文标识符会让 GDScript 编译失败。
        """
        offenders = []
        for number, line in enumerate(code.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            body = line.split("#", 1)[0]
            # 去掉字符串字面量后再检查
            body = re.sub(r'"(?:[^"\\]|\\.)*"', '""', body)
            body = re.sub(r"'(?:[^'\\]|\\.)*'", "''", body)
            if re.search(r"[\u4e00-\u9fff]", body):
                offenders.append((number, stripped))
        self.assertEqual(offenders, [], f"代码里出现中文标识符: {offenders}")

    def assert_indent_sane(self, code):
        """缩进必须是 4 的倍数，且不能混用制表符。"""
        for number, line in enumerate(code.splitlines(), 1):
            if "\t" in line:
                self.fail(f"第 {number} 行含制表符")
            if not line.strip():
                continue
            spaces = len(line) - len(line.lstrip(" "))
            self.assertEqual(spaces % 4, 0, f"第 {number} 行缩进不是 4 的倍数: {line!r}")


class TestBasic(Base):
    def test_empty_tree_warns(self):
        tree = make_tree()
        code, warnings = gen(tree)
        self.assertIn("extends CharacterBody3D", code)
        self.assertTrue(any("事件节点" in w for w in warnings), warnings)

    def test_ready_print(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        printer = tree.new_node(NN.GKNodePrint, "打招呼", gk_text="游戏开始")
        gk_stub.connect(tree, event, "执行", printer, "执行")
        code, warnings = gen(tree)
        self.assertEqual(warnings, [])
        self.assertIn('print("游戏开始")', code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_print_value_from_constant(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        printer = tree.new_node(NN.GKNodePrint, "打印")
        const = tree.new_node(NN.GKNodeConstText, "文本", gk_value="连上了")
        gk_stub.connect(tree, const, "值", printer, "内容")
        gk_stub.connect(tree, event, "执行", printer, "执行")
        code, _ = gen(tree)
        self.assertIn('print("连上了")', code)


class TestCharacter(Base):
    def build_2d_character(self):
        """一个完整可跑的 2D 角色图:方向移动 + 翻转 + 在地面时跳跃。"""
        tree = make_tree()
        physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧", gk_order=0)
        direction = tree.new_node(NN.GKNodeGetVector, "方向")
        speed = tree.new_node(NN.GKNodeConstFloat, "速度", gk_value=220.0)
        move_direction = tree.new_node(NN.GKNodeMoveDirection, "移动")
        flipping = tree.new_node(NN.GKNodeFlip, "翻转")
        on_floor = tree.new_node(NN.GKNodeIsOnFloor, "在地面")
        jump_key = tree.new_node(NN.GKNodeActionJustPressed, "跳跃键", gk_action="jump")
        jump = tree.new_node(NN.GKNodeJump, "跳跃", gk_jump_force=520.0)
        branch = tree.new_node(NN.GKNodeBranch, "如果能跳")
        set_state = tree.new_node(NN.GKNodeSetState, "状态", gk_state="jump")

        gk_stub.connect(tree, physics, "执行", move_direction, "执行")
        gk_stub.connect(tree, direction, "方向", move_direction, "方向")
        gk_stub.connect(tree, speed, "值", move_direction, "速度")
        gk_stub.connect(tree, move_direction, "完成", flipping, "执行")
        gk_stub.connect(tree, flipping, "完成", branch, "执行")

        # 条件:在地面 and 按下跳跃键
        logic = tree.new_node(NN.GKNodeLogic, "与", gk_op="AND")
        gk_stub.connect(tree, on_floor, "在地面", logic, "A")
        gk_stub.connect(tree, jump_key, "刚按下", logic, "B")
        gk_stub.connect(tree, logic, "结果", branch, "条件")
        gk_stub.connect(tree, branch, "是", jump, "执行")
        gk_stub.connect(tree, jump, "完成", set_state, "执行")
        return tree

    def test_character_code(self):
        tree = self.build_2d_character()
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

        self.assertIn("extends CharacterBody2D", code)
        self.assertIn("func _physics_process(delta: float) -> void:", code)
        # 输入动作名集中成脚本顶部常量，方便改键
        self.assertIn('const ACTION_UI_LEFT := "ui_left"', code)
        self.assertIn('const ACTION_JUMP := "jump"', code)
        self.assertIn("Input.get_vector(ACTION_UI_LEFT, ACTION_UI_RIGHT, "
                      "ACTION_UI_UP, ACTION_UI_DOWN, 0.2)", code)
        self.assertIn(".normalized()", code)
        self.assertIn(".move_and_slide()", code)
        self.assertIn("Input.is_action_just_pressed(ACTION_JUMP)", code)
        self.assertIn("is_on_floor()", code)
        self.assertIn(" and ", code)
        self.assertIn("if (", code)
        # 跳跃强度是"未连线的数值输入"，会被暴露成可在检查器里调的 @export
        self.assertIn("@export var jump_force: float = 520.0", code)
        self.assertIn("velocity.y = -jump_force", code)
        # 移动速度接了常量节点(已连线)，所以不该被暴露成参数
        self.assertNotIn("move_speed", code)
        self.assertIn("* 220.0", code)
        # 状态变量自动声明成 String
        self.assertIn("var state: String", code)
        self.assertIn('state = "jump"', code)

    def test_delta_unused_silenced(self):
        """帧间隔没连线时应该生成一行占位，避免 UNUSED_PARAMETER 警告。"""
        tree = self.build_2d_character()
        code, _ = gen(tree, V2)
        self.assertIn("var _unused_delta := delta", code)

    def test_delta_used_when_connected(self):
        tree = make_tree()
        physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        printer = tree.new_node(NN.GKNodePrint, "打印")
        to_text = tree.new_node(NN.GKNodeConvert, "转文本", gk_to="String")
        gk_stub.connect(tree, physics, "帧间隔", to_text, "输入")
        gk_stub.connect(tree, to_text, "输出", printer, "内容")
        gk_stub.connect(tree, physics, "执行", printer, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("str(delta)", code)
        self.assertNotIn("_unused_delta", code)


class TestExpressions(Base):
    def test_vector_split_multi_output(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        printer_x = tree.new_node(NN.GKNodePrint, "打印X")
        printer_y = tree.new_node(NN.GKNodePrint, "打印Y")
        split = tree.new_node(NN.GKNodeVectorSplit, "拆分")
        const = tree.new_node(NN.GKNodeConstVector, "向量", gk_x=1.0, gk_y=2.0, gk_z=3.0)
        gk_stub.connect(tree, const, "向量", split, "向量")
        gk_stub.connect(tree, split, "X", printer_x, "内容")
        gk_stub.connect(tree, split, "Y", printer_y, "内容")
        gk_stub.connect(tree, event, "执行", printer_x, "执行")
        gk_stub.connect(tree, printer_x, "完成", printer_y, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        # 向量拆分走多输出路径，X 和 Y 必须分别取 .x / .y
        self.assertIn(".x", code)
        self.assertIn(".y", code)

    def test_math_operators(self):
        cases = {
            "ADD": " + ", "SUB": " - ", "MUL": " * ", "DIV": " / ",
            "MIN": "minf(", "MAX": "maxf(", "ABS": "absf(",
            "CLAMP": "clampf(", "LERP": "lerpf(", "DEG2RAD": "deg_to_rad(",
        }
        for op, expected in cases.items():
            with self.subTest(op=op):
                tree = make_tree()
                event = tree.new_node(NN.GKNodeOnReady, "启动")
                printer = tree.new_node(NN.GKNodePrint, "打印")
                math = tree.new_node(NN.GKNodeMath, "算", gk_op=op, gk_a=2.0, gk_b=3.0, gk_c=4.0)
                gk_stub.connect(tree, math, "结果", printer, "内容")
                gk_stub.connect(tree, event, "执行", printer, "执行")
                code, warnings = gen(tree, V2)
                self.assertEqual(warnings, [], f"{op}: {warnings}")
                self.assertIn(expected, code)

    def test_compare_text_and_number(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        branch = tree.new_node(NN.GKNodeBranch, "分支")
        compare = tree.new_node(NN.GKNodeCompare, "比较", gk_op="GT", gk_a="10", gk_b="3.5")
        gk_stub.connect(tree, compare, "结果", branch, "条件")
        gk_stub.connect(tree, event, "执行", branch, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("(10 > 3.5)", code)

    def test_scalar_into_vector_socket_warns_and_wraps(self):
        """把单个数值接到向量引脚:应该包装成向量并告警，而不是生成非法代码。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        velocity = tree.new_node(NN.GKNodeSetVelocity, "设速度")
        axis = tree.new_node(NN.GKNodeGetAxis, "轴")
        gk_stub.connect(tree, axis, "强度", velocity, "速度")
        gk_stub.connect(tree, event, "执行", velocity, "执行")
        code, warnings = gen(tree, V2)
        self.assertTrue(any("包装成向量" in w for w in warnings), warnings)
        self.assertIn("Vector2(Input.get_axis", code)
        self.assert_no_chinese_identifiers(code)


class TestFlow(Base):
    def test_sequence_and_loop(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        sequence = tree.new_node(NN.GKNodeSequence, "序列", gk_step_count=3)
        p1 = tree.new_node(NN.GKNodePrint, "一", gk_text="1")
        p2 = tree.new_node(NN.GKNodePrint, "二", gk_text="2")
        p3 = tree.new_node(NN.GKNodePrint, "三", gk_text="3")
        loop = tree.new_node(NN.GKNodeForLoop, "循环", gk_index_name="i")
        body = tree.new_node(NN.GKNodePrint, "循环体", gk_text="滴")
        gk_stub.connect(tree, event, "执行", sequence, "执行")
        gk_stub.connect(tree, sequence, "1", p1, "执行")
        gk_stub.connect(tree, sequence, "2", p2, "执行")
        gk_stub.connect(tree, sequence, "3", p3, "执行")
        gk_stub.connect(tree, p3, "完成", loop, "执行")
        gk_stub.connect(tree, loop, "循环体", body, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        for text in ("1", "2", "3", "滴"):
            self.assertIn(f'print("{text}")', code)
        self.assertIn("for i in int(3):", code)

    def test_branch_without_else(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        branch = tree.new_node(NN.GKNodeBranch, "分支")
        yes = tree.new_node(NN.GKNodePrint, "是", gk_text="yes")
        gk_stub.connect(tree, event, "执行", branch, "执行")
        gk_stub.connect(tree, branch, "是", yes, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("if false:", code)
        self.assertNotIn("else:", code)

    def test_branch_empty_gets_pass(self):
        """空的分支体必须补 pass，否则生成的是语法错误代码。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        branch = tree.new_node(NN.GKNodeBranch, "空分支")
        gk_stub.connect(tree, event, "执行", branch, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("if false:", code)
        self.assertIn("pass", code)
        self.assert_indent_sane(code)

    def test_cycle_is_caught(self):
        """执行流成环时必须停下来并告警，不能挂死。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        p1 = tree.new_node(NN.GKNodePrint, "一", gk_text="1")
        p2 = tree.new_node(NN.GKNodePrint, "二", gk_text="2")
        gk_stub.connect(tree, event, "执行", p1, "执行")
        gk_stub.connect(tree, p1, "完成", p2, "执行")
        gk_stub.connect(tree, p2, "完成", p1, "执行")
        code, warnings = gen(tree, V2)
        self.assertTrue(any("回路" in w for w in warnings), warnings)

    def test_do_once(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        once = tree.new_node(NN.GKNodeDoOnce, "一次")
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="只一次")
        gk_stub.connect(tree, event, "执行", once, "执行")
        gk_stub.connect(tree, once, "首次", printer, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("if not done", code)
        self.assertIn("var done: bool", code)


class TestVariablesAndSignals(Base):
    def test_set_get_var_lazy_declare(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        setter = tree.new_node(NN.GKNodeSetVar, "设血量", gk_var_name="health", gk_value=100.0)
        getter = tree.new_node(NN.GKNodeGetVar, "读血量", gk_var_name="health")
        printer = tree.new_node(NN.GKNodePrint, "打印")
        gk_stub.connect(tree, event, "执行", setter, "执行")
        gk_stub.connect(tree, setter, "完成", printer, "执行")
        gk_stub.connect(tree, getter, "值", printer, "内容")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("var health: float", code)
        self.assertIn("health = 100.0", code)
        self.assertIn("print(health)", code)

    def test_signal_declared_and_emitted(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        emitter = tree.new_node(NN.GKNodeEmitSignal, "发出", gk_signal_name="died")
        gk_stub.connect(tree, event, "执行", emitter, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("signal died", code)
        self.assertIn("died.emit()", code)

    def test_connect_guarded(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        connect_node = tree.new_node(
            NN.GKNodeConnect, "连接", gk_signal_name="pressed", gk_handler="on_pressed"
        )
        gk_stub.connect(tree, event, "执行", connect_node, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("self.pressed.connect(on_pressed)", code)
        self.assertIn("is_connected", code)


class TestTargets(Base):
    def test_other_node_becomes_onready(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        velocity = tree.new_node(NN.GKNodeSetVelocity, "设别的节点", gk_target="Player")
        gk_stub.connect(tree, event, "执行", velocity, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("@onready var", code)
        self.assertIn("$Player", code)

    def test_animation_player_default(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        play = tree.new_node(NN.GKNodePlayAnim, "播放", gk_anim_name="run", gk_player="")
        gk_stub.connect(tree, event, "执行", play, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("$AnimationPlayer", code)
        self.assertIn("has_animation(", code)
        self.assertIn('"run"', code)

    def test_spawn_scope_is_flat(self):
        """实例生成节点生成的变量必须和 if 同级，不能被关在 else 分支里。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        spawn = tree.new_node(NN.GKNodeSpawn, "生成")
        gk_stub.connect(tree, event, "执行", spawn, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("as PackedScene", code)
        self.assertIn(".instantiate()", code)
        self.assert_indent_sane(code)

        lines = code.splitlines()
        # 找出 var xxx_inst := ... 所在行:它必须在 else 分支内部(缩进 8)，
        # 因为外层是 `if 场景 == null: 报错  else: 实例化`
        inst_lines = [i for i, line in enumerate(lines) if ":= " in line and ".instantiate()" in line]
        self.assertTrue(inst_lines, "没有生成实例变量")
        for index in inst_lines:
            indent = len(lines[index]) - len(lines[index].lstrip(" "))
            self.assertEqual(indent, 8, f"实例变量缩进不对: {lines[index]!r}")
            # 往上应能找到 else: 与加载失败的判断
            above = [line.strip() for line in lines[:index] if line.strip()]
            self.assertIn("else:", above[-3:])
            self.assertTrue(any(line.startswith("if ") and "== null" in line for line in above[-6:]),
                            "实例变量上方应有加载失败的兜底判断")


class TestSharedNodes(Base):
    def test_shared_node_between_events_warns(self):
        """同一节点被两个事件引用时必须告警，且只生成一次。

        背景:Blender 的输入引脚只允许一条连线，第二条连线会被静默拒绝。
        所以"两个事件都连到同一个动作节点"实际上不可能成立，生成器必须
        明确告诉用户，而不是默默丢掉一段逻辑。
        """
        tree = make_tree()
        ready = tree.new_node(NN.GKNodeOnReady, "准备")
        process = tree.new_node(NN.GKNodeOnProcess, "每帧")
        printer = tree.new_node(NN.GKNodePrint, "共用打印", gk_text="共用")
        # 手动让两个事件都指向同一个节点(模拟用户的真实意图)
        import gk_stub as stub

        for source in (ready, process):
            link = stub.StubLink(source.outputs.get("执行"), printer.inputs.get("执行"))
            source.outputs.get("执行").links.append(link)
            printer.inputs.get("执行").links.append(link)

        code, warnings = gen(tree, V2)
        self.assertTrue(any("同时被" in w for w in warnings), warnings)
        self.assertEqual(code.count('print("共用")'), 1, code)
        self.assert_indent_sane(code)


class TestOptions(Base):
    def test_class_name_and_base(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        tree.new_node(NN.GKNodePrint, "打印", gk_text="hi")
        code, _ = gen(tree, {"class_name": "player", "base_class": "CharacterBody3D"})
        self.assertIn("class_name Player", code)
        self.assertIn("extends CharacterBody3D", code)

    def test_header_has_reference(self):
        tree = make_tree()
        tree.new_node(NN.GKNodeOnReady, "启动")
        code, _ = gen(tree, V2)
        self.assertIn("BlenderGameKit", code)
        self.assertIn("测试图", code)


class TestNodeLibrary(unittest.TestCase):
    def test_all_nodes_have_unique_idnames(self):
        seen = {}
        for cls in NN.ALL_CLASSES:
            self.assertNotIn(cls.bl_idname, seen, f"bl_idname 重复: {cls.bl_idname}")
            seen[cls.bl_idname] = cls

    def test_every_node_has_doc_and_category(self):
        for cls in NN.ALL_CLASSES:
            self.assertTrue(getattr(cls, "gk_doc", ""), f"{cls.bl_label} 缺少 gk_doc")
            self.assertIn(cls.gk_category, NN.CATEGORY_ORDER,
                          f"{cls.bl_label} 的分类 {cls.gk_category} 不在 CATEGORY_ORDER 里")

    def test_statement_nodes_have_flow_in(self):
        """语句节点必须有一个执行输入引脚。"""
        from BlenderGameKit import gk_sockets as S
        from BlenderGameKit.nodes import node_base as NB
        for cls in NN.ALL_CLASSES:
            if not issubclass(cls, NB.GKStatementNode):
                continue
            inputs = [spec for spec in cls.gk_inputs if spec["kind"] is S.FLOW]
            self.assertTrue(inputs, f"{cls.bl_label} 缺少执行输入引脚")

    def test_prop_defs_reference_existing_props(self):
        """gk_input_props 指向的属性必须真的声明过。"""
        for cls in NN.ALL_CLASSES:
            declared = {entry[0] for entry in cls.gk_prop_defs}
            socket_names = [spec["name"] for spec in cls.gk_inputs]
            for socket_name, prop in cls.gk_input_props.items():
                self.assertIn(prop, declared,
                              f"{cls.bl_label}: gk_input_props['{socket_name}'] -> "
                              f"{prop} 没有在 gk_prop_defs 里声明")
                self.assertIn(socket_name, socket_names,
                              f"{cls.bl_label}: gk_input_props 里的 {socket_name} 不是输入引脚")

    def test_input_socket_names_unique(self):
        for cls in NN.ALL_CLASSES:
            names = [spec["name"] for spec in cls.gk_inputs]
            self.assertEqual(len(names), len(set(names)),
                             f"{cls.bl_label} 有重名输入引脚: {names}")


def nn_base_statement():
    from BlenderGameKit.nodes import node_base as NB
    return NB.GKStatementNode


if __name__ == "__main__":
    unittest.main(verbosity=2)
