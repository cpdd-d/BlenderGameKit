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
        两处**有意为之**的例外(都实测过 Godot 4.7.2 接受):

        * `$"主相机"` —— 带引号的节点路径，属于字符串
        * `_gk_ui_<中文>` —— 界面控件的成员变量名。
          界面控件是"每个节点一个"的，用节点名做后缀才能一眼对上号；
          Godot 的标识符本来就允许 Unicode，中文名对中文用户更好读。
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
            # 界面控件的变量名允许中文(见上面的说明)
            body = body.replace("_gk_ui_", "_GKUI_")
            body = re.sub(r"_GKUI_[\u4e00-\u9fff\w]*", "_GKUI_X", body)
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


class TestDisplayNodes(Base):
    """显示 / 相机 / 时间 三个新分类。"""

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_set_visible_true(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeSetVisible, "显示", gk_visible=True)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("self.visible = true", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_set_visible_false(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeSetVisible, "隐藏", gk_visible=False)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("self.visible = false", code)

    def test_set_visible_warns_on_incompatible_base(self):
        """往纯 Node 基类上写 visible 会被 Godot 拒绝，必须提前告警并跳过。

        这是实测过的坑:
            Identifier "visible" not declared in the current scope.
        """
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeSetVisible, "隐藏", gk_visible=False)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, {"dimension": "3", "base_class": "Node"})
        self.assertTrue(any("visible" in w for w in warnings), warnings)
        self.assertNotIn("self.visible", code)

    def test_set_paused_and_resume(self):
        tree = make_tree()
        event = self._ready(tree)
        pause = tree.new_node(NN.GKNodeSetPaused, "暂停", gk_paused=True)
        resume = tree.new_node(NN.GKNodeSetPaused, "恢复", gk_paused=False)
        gk_stub.connect(tree, event, "执行", pause, "执行")
        gk_stub.connect(tree, pause, "完成", resume, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("self.process_mode = Node.PROCESS_MODE_DISABLED", code)
        self.assertIn("self.process_mode = Node.PROCESS_MODE_INHERIT", code)
        self.assert_indent_sane(code)

    def test_camera_set_current(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeCameraSetCurrent, "切镜头", gk_target="主相机")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn(".make_current()", code)
        # 相机节点名里没有类型关键词，必须靠 hint 推断出 Camera2D，
        # 否则声明成 Node 会被 GDScript 静态类型检查拒绝。
        self.assertIn(": Camera2D = ", code)
        self.assert_no_chinese_identifiers(code)

    def test_camera_zoom_dispatches_by_real_type(self):
        """zoom 是 2D 概念、fov 是 3D 概念，必须按相机真实类型分派。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeCameraZoom, "缩放", gk_target="主相机", gk_value=1.5)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("is Camera2D:", code)
        self.assertIn("is Camera3D:", code)
        self.assertIn(".zoom = Vector2(", code)
        self.assertIn(".fov =", code)
        self.assert_indent_sane(code)

    def test_camera_shake_emits_process_hook(self):
        """震动要跨帧衰减，所以必须真的生成 _process，而不是只写一句。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeCameraShake, "受击抖动",
                             gk_target="主相机", gk_strength=6.0, gk_duration=0.2)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)

        # 触发语句:重置计时器(生成器内部名保留前导下划线)
        self.assertIn("_gk_shake_left = _gk_shake_total", code)
        # 每帧衰减:必须有 _process
        self.assertIn("func _process(delta: float) -> void:", code)
        self.assertIn("randf_range(", code)
        # 结束后必须归零，否则相机会一直歪着
        self.assertIn(".offset = Vector2.ZERO", code)
        self.assertIn(".h_offset = 0.0", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)
        # 衰减逻辑必须在 _process 函数体内部。它在 `if _gk_shake_left > 0.0:`
        # 里面，所以缩进是 8(函数体 4 + 一层 if)。
        lines = code.splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith("func _process"))
        decay = next(i for i, line in enumerate(lines) if "_gk_shake_left -= delta" in line)
        self.assertGreater(decay, index, "衰减逻辑必须在 _process 之后")
        self.assertEqual(len(lines[decay]) - len(lines[decay].lstrip(" ")), 8,
                         f"衰减逻辑缩进应为 8: {lines[decay]!r}")

    def test_camera_shake_reuses_single_hook(self):
        """两个震动节点不该生成两份衰减逻辑(否则会互相打架)。"""
        tree = make_tree()
        event = self._ready(tree)
        first = tree.new_node(NN.GKNodeCameraShake, "抖动1", gk_target="主相机")
        second = tree.new_node(NN.GKNodeCameraShake, "抖动2", gk_target="主相机")
        gk_stub.connect(tree, event, "执行", first, "执行")
        gk_stub.connect(tree, first, "完成", second, "执行")
        code, _ = gen(tree, V2)
        self.assertEqual(code.count("gk_shake_left -= delta"), 1, code)

    def test_same_node_referenced_once(self):
        """同一台相机被多个节点引用时，只应声明一个 @onready 变量。

        否则脚本开头会出现 cam / cam_2 / cam_3 三个指向同一节点的变量:
        能跑，但用户根本看不出它们是同一个东西。
        """
        tree = make_tree()
        event = self._ready(tree)
        first = tree.new_node(NN.GKNodeCameraSetCurrent, "切镜头", gk_target="主相机")
        second = tree.new_node(NN.GKNodeCameraZoom, "缩放", gk_target="主相机", gk_value=1.5)
        third = tree.new_node(NN.GKNodeCameraShake, "抖动", gk_target="主相机")
        gk_stub.connect(tree, event, "执行", first, "执行")
        gk_stub.connect(tree, first, "完成", second, "执行")
        gk_stub.connect(tree, second, "完成", third, "执行")
        code, _ = gen(tree, V2)
        self.assertEqual(code.count("@onready"), 1, code)
        # 三个节点都必须指向同一个变量，不能各用各的
        self.assertEqual(code.count(".make_current()"), 1, code)
        self.assertEqual(code.count(".zoom = Vector2("), 1, code)
        self.assertEqual(code.count("gk_shake_cam = "), 1, code)

    def test_camera_typed_by_dimension(self):
        """相机类型必须跟着项目维度走，3D 项目里声明 Camera2D 同样编译不过。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeCameraSetCurrent, "切镜头", gk_target="主相机")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code_2d, _ = gen(tree, {"dimension": "2", "base_class": "CharacterBody2D"})
        self.assertIn(": Camera2D = ", code_2d)

        tree3 = make_tree()
        event3 = tree3.new_node(NN.GKNodeOnReady, "准备")
        node3 = tree3.new_node(NN.GKNodeCameraSetCurrent, "切镜头", gk_target="主相机")
        gk_stub.connect(tree3, event3, "执行", node3, "执行")
        code_3d, _ = gen(tree3, {"dimension": "3", "base_class": "CharacterBody3D"})
        self.assertIn(": Camera3D = ", code_3d)

    def test_camera_auto_find_when_unset(self):
        """没填相机时不该崩，而是自动找第一个相机并告警说明。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeCameraSetCurrent, "切镜头")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertTrue(any("自动找" in w for w in warnings), warnings)
        self.assertIn('get_tree().get_first_node_in_group("gk_camera")', code)
        self.assert_no_chinese_identifiers(code)

    def test_no_camera_helper_when_unused(self):
        """没用到相机就不该塞一个用不到的辅助函数。"""
        tree = make_tree()
        event = self._ready(tree)
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="hi")
        gk_stub.connect(tree, event, "执行", printer, "执行")
        code, _ = gen(tree, V2)
        self.assertNotIn("func get_first_camera", code)

    def test_set_time_scale(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeSetTimeScale, "子弹时间", gk_scale=0.3)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("Engine.time_scale = ", code)
        self.assert_indent_sane(code)

    def test_camera_zoom_uses_export_when_unlinked(self):
        """未连线的数值应变成 @export，方便在 Godot 检查器里直接调手感。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeCameraZoom, "缩放", gk_target="主相机", gk_value=2.0)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("@export var", code)


class TestAudioNodes(Base):
    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_play_sound_creates_player_at_runtime(self):
        """音效不该要求用户先在场景里摆播放器。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodePlaySound, "音效", gk_path="res://art/jump.wav",
                             gk_volume=-3.0, gk_pitch=1.2, gk_2d=True)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("AudioStreamPlayer2D.new()", code)
        self.assertIn('load("res://art/jump.wav")', code)
        self.assertIn(".volume_db = ", code)
        self.assertIn(".pitch_scale = ", code)
        # 播完自己删，否则每个音效都会在场景树里堆一个节点
        self.assertIn(".finished.connect(", code)
        self.assertIn(".queue_free)", code)
        # 文件缺失时必须给出可读告警，而不是静默什么都不做
        self.assertIn("push_warning(", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_play_sound_3d_switch(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodePlaySound, "音效", gk_path="res://a.wav", gk_2d=False)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, {"dimension": "3", "base_class": "CharacterBody3D"})
        self.assertIn("AudioStreamPlayer.new()", code)
        self.assertNotIn("AudioStreamPlayer2D.new()", code)

    def test_music_does_not_stack(self):
        """重复调用播放音乐不该叠着播 —— 必须先处理旧的播放器。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodePlayMusic, "音乐", gk_path="res://bgm.ogg",
                             gk_volume=-6.0, gk_fade=0.5)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("AudioStreamPlayer.new()", code)
        # 同一首正在播时不要打断
        self.assertIn(".playing:", code)
        self.assertIn("queue_free()", code)
        # 有淡入时分贝应从 -80 起
        self.assertIn("-80.0", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_stop_music(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeStopSound, "停音乐", gk_fade=0.5)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn(".stop", code)
        self.assert_indent_sane(code)


class TestSaveNodes(Base):
    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_save_helpers_only_when_used(self):
        """没用到存档节点就不该生成那几个读写文件的函数。"""
        tree = make_tree()
        event = self._ready(tree)
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="hi")
        gk_stub.connect(tree, event, "执行", printer, "执行")
        code, _ = gen(tree, V2)
        self.assertNotIn("_gk_read_save", code)
        self.assertNotIn("func _gk_save_file", code)

    def test_save_write_generates_helpers(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeSaveWrite, "保存", gk_slot="save1")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        for func in ("_gk_save_file", "_gk_read_save", "_gk_write_save", "_gk_delete_save"):
            self.assertIn(f"func {func}", code)
        self.assertIn('"user://gk_" + slot + ".json"', code)
        self.assertIn("FileAccess.open(", code)
        self.assertIn("JSON.stringify(", code)
        self.assertIn("JSON.parse_string(", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_save_slot_names_the_file(self):
        """不同槽位必须落到不同文件，否则多存档会互相覆盖。"""
        tree = make_tree()
        event = self._ready(tree)
        first = tree.new_node(NN.GKNodeSaveWrite, "存档1", gk_slot="slotA")
        second = tree.new_node(NN.GKNodeSaveWrite, "存档2", gk_slot="slotB")
        gk_stub.connect(tree, event, "执行", first, "执行")
        gk_stub.connect(tree, first, "完成", second, "执行")
        code, _ = gen(tree, V2)
        self.assertIn('_gk_save_file("slotA")', code)
        self.assertIn('_gk_save_file("slotB")', code)

    def test_lazy_load_guard_emitted_once_per_block(self):
        """同一段代码里连续两个存档节点，只该有一段惰性读盘守卫。

        以前每个存档节点都吐一遍守卫，4 个节点就是 4 段一模一样的读盘。
        """
        tree = make_tree()
        event = self._ready(tree)
        setter = tree.new_node(NN.GKNodeSaveSet, "写", gk_key="hp")
        getter = tree.new_node(NN.GKNodeSaveGet, "读", gk_key="hp", gk_var="hp")
        gk_stub.connect(tree, event, "执行", setter, "执行")
        gk_stub.connect(tree, setter, "完成", getter, "执行")
        code, _ = gen(tree, V2)
        self.assertLessEqual(code.count("_gk_read_save(gk_save_path)"), 2,
                             f"惰性读盘守卫重复了:\n{code}")

    def test_explicit_load_does_not_double_read(self):
        """显式『存档_载入』之后不该再自动读一遍盘。"""
        tree = make_tree()
        event = self._ready(tree)
        loader = tree.new_node(NN.GKNodeSaveLoad, "载入", gk_slot="save1")
        setter = tree.new_node(NN.GKNodeSaveSet, "写", gk_key="hp", gk_slot="save1")
        gk_stub.connect(tree, event, "执行", loader, "执行")
        gk_stub.connect(tree, loader, "完成", setter, "执行")
        code, _ = gen(tree, V2)
        # 只数"调用"，不数辅助函数自己的签名 `func _gk_read_save(...)`
        calls = [line for line in code.splitlines()
                 if "_gk_read_save(" in line and not line.strip().startswith("func ")]
        self.assertEqual(len(calls), 1, "\n".join(calls))

    def test_save_has_two_named_branches(self):
        """『存档_是否存在』的两个出口必须各自只生成一次。

        回归:出口曾叫『完成』，而 emit_statement 会自动续接『完成』，
        导致 else 分支被生成两遍(实测『没存档』打印两次)。
        """
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeSaveHas, "有档吗", gk_slot="save1")
        yes = tree.new_node(NN.GKNodePrint, "有档", gk_text="有存档")
        no = tree.new_node(NN.GKNodePrint, "无档", gk_text="没存档")
        gk_stub.connect(tree, event, "执行", node, "执行")
        gk_stub.connect(tree, node, "有档", yes, "执行")
        gk_stub.connect(tree, node, "没档", no, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_save_exists(", code)
        self.assertIn("if ", code)
        self.assertIn("else:", code)
        self.assertEqual(code.count('print("有存档")'), 1, code)
        self.assertEqual(code.count('print("没存档")'), 1, code)
        self.assert_indent_sane(code)

    def test_save_has_shares_name_with_branch_convention(self):
        """两个出口不能叫『完成』，否则会被自动续接机制重复执行。"""
        names = [spec["name"] for spec in NN.GKNodeSaveHas.gk_outputs]
        self.assertNotIn("完成", names, "出口叫『完成』会被 emit_statement 重复续接")

    def test_save_delete_clears_memory(self):
        """删档必须同时清掉内存里那份，否则删了还能读到旧数据。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeSaveDelete, "删档", gk_slot="save1")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("_gk_delete_save(", code)
        self.assertIn(".clear()", code)
        self.assertIn("remove_absolute", code)


class TestScreenEffectNodes(Base):
    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_screen_flash(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeScreenFlash, "闪白", gk_time=0.3, gk_alpha=0.6)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("ColorRect.new()", code)
        self.assertIn("MOUSE_FILTER_IGNORE", code)
        self.assertIn("PRESET_FULL_RECT", code)
        # 只淡 alpha，不能用 tween_property(rect,"color",...) 那样会连 RGB 一起改
        self.assertIn('"color:a"', code)
        self.assertIn(".queue_free)", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_screen_fade_creates_persistent_layer(self):
        """转场遮罩必须是持久层，才能做"淡出 -> 换场景 -> 淡入"。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeScreenFade, "黑场", gk_time=1.0, gk_to_opaque=True)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("CanvasLayer.new()", code)
        self.assertIn("_gk_fade_layer == null", code)
        # 等渐变结束再往下走，否则"淡出->切场景"顺序会乱
        self.assertIn("await ", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_screen_fade_out_hides_overlay(self):
        """淡回透明后要把遮罩收起来，不能一直盖着。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeScreenFade, "淡回", gk_time=1.0, gk_to_opaque=False)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V2)
        self.assertIn(".visible = false", code)

    def test_two_fades_share_one_layer(self):
        """两次渐变应复用同一个遮罩层，而不是各建一个。"""
        tree = make_tree()
        event = self._ready(tree)
        first = tree.new_node(NN.GKNodeScreenFade, "黑场", gk_time=1.0, gk_to_opaque=True)
        second = tree.new_node(NN.GKNodeScreenFade, "淡回", gk_time=1.0, gk_to_opaque=False)
        gk_stub.connect(tree, event, "执行", first, "执行")
        gk_stub.connect(tree, first, "完成", second, "执行")
        code, _ = gen(tree, V2)
        # 成员变量只声明一次(生成器内部名保留前导下划线)
        self.assertEqual(code.count("var _gk_fade_layer: CanvasLayer"), 1, code)
        self.assertEqual(code.count("var _gk_fade_rect: ColorRect"), 1, code)


class TestUiNodes(Base):
    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_ui_text_creates_shared_root(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeUiText, "分数", gk_text="分数: 0")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_ensure_ui(self)", code)
        self.assertIn("CanvasLayer.new()", code)
        self.assertIn("Control.new()", code)
        self.assertIn("Label.new()", code)
        self.assertIn('str("分数: 0")', code)
        # 文字控件不能吃鼠标，否则会挡住下面的按钮
        self.assertIn("MOUSE_FILTER_IGNORE", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_ui_bar_clamps_value(self):
        """血量变成负数或超过上限时进度条会画出诡异的样子，必须钳住。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeUiBar, "血条", gk_value=150.0, gk_max=100.0)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("ProgressBar.new()", code)
        self.assertIn("clampf(", code)
        self.assertIn("max_value = 100.0", code)
        self.assert_indent_sane(code)

    def test_ui_button_click_is_not_executed_at_creation(self):
        """按钮的『点击』出口必须放进独立回调，不能在连线瞬间就执行。

        回归:出口曾叫『完成』，而 emit_statement 会自动续接『完成』，
        结果按钮刚建出来后面的逻辑就直接跑了(比如游戏立刻开始)。
        """
        tree = make_tree()
        event = self._ready(tree)
        button = tree.new_node(NN.GKNodeUiButton, "开始按钮", gk_text="开始游戏")
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="游戏开始")
        gk_stub.connect(tree, event, "执行", button, "执行")
        gk_stub.connect(tree, button, "点击", printer, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)

        lines = code.splitlines()
        ready_at = next(i for i, line in enumerate(lines) if line.startswith("func _ready"))
        callback_at = next(i for i, line in enumerate(lines) if "func gk_on_" in line)
        print_at = next(i for i, line in enumerate(lines) if 'print("游戏开始")' in line)
        # 打印必须在回调函数里，而不是在 _ready 里
        self.assertGreater(print_at, callback_at,
                           f"打印应在按钮回调里:\n{code}")
        self.assertGreater(callback_at, ready_at)
        self.assertIn(".pressed.connect(", code)
        # 按钮必须吃鼠标，否则点不动
        self.assertIn("MOUSE_FILTER_STOP", code)

    def test_ui_button_output_not_named_done(self):
        """出口不能叫『完成』，否则会被自动续接机制提前执行。"""
        names = [spec["name"] for spec in NN.GKNodeUiButton.gk_outputs]
        self.assertNotIn("完成", names, "出口叫『完成』会被 emit_statement 提前续接")

    def test_ui_hide_finds_widget_by_name(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeUiHide, "隐藏提示", gk_widget="提示", gk_visible=False)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_find_widget(self, \"提示\")", code)
        self.assertIn(".visible = false", code)
        # 找不到控件要给告警，而不是静默什么都不做
        self.assertIn("push_warning(", code)

    def test_ui_hide_without_widget_name_warns(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeUiHide, "隐藏提示", gk_widget="")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertTrue(any("控件名" in w for w in warnings), warnings)

    def test_ui_helpers_only_when_used(self):
        tree = make_tree()
        event = self._ready(tree)
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="hi")
        gk_stub.connect(tree, event, "执行", printer, "执行")
        code, _ = gen(tree, V2)
        self.assertNotIn("_gk_ensure_ui", code)

    def test_same_named_widgets_do_not_share_variable(self):
        """两行同名的文本必须各自有变量，否则类型声明会互相覆盖。

        回归:两个节点都叫『分数』时，一个声明成 Label、另一个声明成
        ProgressBar，后声明的把前一个覆盖掉，生成自相矛盾的脚本
        (Godot 报 Value of type "ProgressBar" cannot be assigned to "Label")。
        """
        tree = make_tree()
        event = self._ready(tree)
        first = tree.new_node(NN.GKNodeUiText, "同一个名字", gk_text="A")
        second = tree.new_node(NN.GKNodeUiBar, "同一个名字", gk_value=1.0)
        gk_stub.connect(tree, event, "执行", first, "执行")
        gk_stub.connect(tree, first, "完成", second, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("var _gk_ui_同一个名字: Label", code)
        self.assertIn("var _gk_ui_同一个名字_2: ProgressBar", code)

    def test_internal_variable_names_keep_leading_underscore(self):
        """生成器内部名不能被 snake_case 吃掉前导下划线。

        回归:`_gk_ui_layer` 被改名成 `gk_ui_layer`，导致函数体里写
        `_gk_ui_layer` 而成员变量声明成 `gk_ui_layer`，
        Godot 报 Identifier "_gk_ui_layer" not declared。
        """
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeUiText, "分数", gk_text="A")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("var _gk_ui_layer: CanvasLayer", code)
        self.assertIn("var _gk_ui_root: Control", code)
        self.assertNotIn("var gk_ui_layer", code)


    def test_repeated_generate_has_no_false_shared_node_warning(self):
        """连续多次导出不能互相污染。

        回归:断环/归属判断曾用 id(节点) 当键。id 会在对象被回收后**复用**，
        于是前一次导出里已释放的节点 id 会被这一次的新节点拿到，
        新节点被误判成"已被别的节点占用"，冒出一条莫名其妙的
        "同时被两个事件引用"告警 —— 单独跑没事、在 Blender 里跑就复现。

        修法是用节点对象本身当键(顺带保持它们存活，id 复用问题自然消失)。
        """
        for round_index in range(3):
            tree = make_tree()
            event = tree.new_node(NN.GKNodeOnReady, "开始")
            first = tree.new_node(NN.GKNodePrint, "一", gk_text="1")
            second = tree.new_node(NN.GKNodePrint, "二", gk_text="2")
            gk_stub.connect(tree, event, "执行", first, "执行")
            gk_stub.connect(tree, first, "完成", second, "执行")
            code, warnings = gen(tree, V2)
            self.assertEqual(warnings, [],
                             f"第 {round_index + 1} 次导出出现假告警: {warnings}")
            self.assertIn('print("1")', code)
            self.assertIn('print("2")', code)
            del tree, event, first, second, code

    def test_index_holder_is_not_used_as_node_key(self):
        """生成器里不该再用 id(节点) 当归属键(会用对象本身)。

        这条测试是"意图锁":如果以后有人为了省事改回 id()，
        上面那条测试在压力下才可能复现，而这条会直接失败。
        """
        import inspect

        source = inspect.getsource(codegen._emit_body)
        self.assertNotIn("id(node)", source,
                         "断环/归属判断请用节点对象当键，别用 id(node)")

    def test_shared_node_between_events_still_warns(self):
        """修好假告警之后，真告警必须还在(否则就是矫枉过正)。"""
        tree = make_tree()
        ready = tree.new_node(NN.GKNodeOnReady, "开始")
        process = tree.new_node(NN.GKNodeOnProcess, "物理帧")
        shared = tree.new_node(NN.GKNodePrint, "共用", gk_text="共用")
        import gk_stub as stub

        for source in (ready, process):
            link = stub.StubLink(source.outputs.get("执行"), shared.inputs.get("执行"))
            source.outputs.get("执行").links.append(link)
            shared.inputs.get("执行").links.append(link)
        code, warnings = gen(tree, V2)
        self.assertTrue(any("同时被" in w for w in warnings), warnings)
        self.assertEqual(code.count('print("共用")'), 1, code)


def nn_base_statement():
    from BlenderGameKit.nodes import node_base as NB
    return NB.GKStatementNode


if __name__ == "__main__":
    unittest.main(verbosity=2)
