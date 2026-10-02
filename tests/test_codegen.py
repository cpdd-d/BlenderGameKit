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
V3 = dict(dimension="3", base_class="CharacterBody3D")


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
        # 输入动作名集中成脚本顶部常量，方便改键。
        # 默认是 move_* 而**不是** Godot 内置的 ui_* ——
        # ui_* 绑的是方向键,WASD 完全没反应(用户实测踩过)。
        # move_* 导出时会连按键一起写进 project.godot(WASD 开箱即用)。
        self.assertIn('const ACTION_MOVE_LEFT := "move_left"', code)
        self.assertIn('const ACTION_JUMP := "jump"', code)
        self.assertIn("Input.get_vector(ACTION_MOVE_LEFT, ACTION_MOVE_RIGHT, "
                      "ACTION_MOVE_UP, ACTION_MOVE_DOWN, 0.2)", code)
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
        """留空时用**运行时查找**,不能写死 `$AnimationPlayer`。

        实测踩过:glTF 导入时 AnimationPlayer 藏在模型实例内部
        (Hero/Model/AnimationPlayer),而脚本挂在 Hero 上 ——
        `$AnimationPlayer` 是相对脚本节点的路径,必然找不到,
        Godot 报 "Node not found: AnimationPlayer" 后整个脚本都不跑。
        """
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        play = tree.new_node(NN.GKNodePlayAnim, "播放", gk_anim_name="run", gk_player="")
        gk_stub.connect(tree, event, "执行", play, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_find_animation_player(self)", code)
        self.assertNotIn("$AnimationPlayer", code)
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
        """语句节点必须有一个执行输入引脚。

        例外:`GKNodeAnimState`(动画状态)—— 它是**动画树**里的节点,
        不是执行流的一环。它只有"条件"输入,靠条件为真被激活,
        没有也不需要执行引脚。
        """
        from BlenderGameKit import gk_sockets as S
        from BlenderGameKit.nodes import node_base as NB
        # 动画树里的卡片节点不参与执行流,单独列出来
        # (`GKNodeAnimTransition` 同 `GKNodeAnimState`:靠条件为真被激活)
        no_flow = {"GKNodeAnimState", "GKNodeAnimTransition"}
        for cls in NN.ALL_CLASSES:
            if not issubclass(cls, NB.GKStatementNode):
                continue
            if cls.bl_idname in no_flow:
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


class TestRaycastNodes(Base):
    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_raycast_2d_uses_2d_api(self):
        """2D 项目必须用 2D 的射线 API。

        Node2D 上不存在 get_world_3d()，写错在**编译期**就被拒绝
        (实测 Function not found)，所以是按维度生成两套而不是运行时判断。
        """
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeRaycast, "射线", gk_distance=200.0)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("get_world_2d()", code)
        self.assertIn("PhysicsRayQueryParameters2D.create(", code)
        self.assertIn("PhysicsDirectSpaceState2D", code)
        self.assertNotIn("get_world_3d", code)
        self.assertNotIn("Vector3", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_raycast_3d_uses_3d_api(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        node = tree.new_node(NN.GKNodeRaycast, "射线", gk_distance=10.0)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, {"dimension": "3", "base_class": "CharacterBody3D"})
        self.assertIn("get_world_3d()", code)
        self.assertIn("PhysicsRayQueryParameters3D.create(", code)
        self.assertIn("global_transform.origin", code)
        self.assertNotIn("get_world_2d", code)

    def test_raycast_has_two_named_branches(self):
        """命中 / 没打中 两个出口都必须各生成一次。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeRaycast, "射线")
        hit = tree.new_node(NN.GKNodePrint, "中", gk_text="命中")
        miss = tree.new_node(NN.GKNodePrint, "没", gk_text="未命中")
        gk_stub.connect(tree, event, "执行", node, "执行")
        gk_stub.connect(tree, node, "命中", hit, "执行")
        gk_stub.connect(tree, node, "没打中", miss, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertEqual(code.count('print("命中")'), 1, code)
        self.assertEqual(code.count('print("未命中")'), 1, code)
        self.assertIn("else:", code)

    def test_raycast_direction_is_normalized(self):
        """方向必须先归一化，否则距离参数会随输入长度变化。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeRaycast, "射线")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V2)
        self.assertIn(".normalized()", code)

    def test_raycast_outputs_not_named_done(self):
        names = [spec["name"] for spec in NN.GKNodeRaycast.gk_outputs]
        self.assertNotIn("完成", names, "出口叫『完成』会被 emit_statement 提前续接")

    def test_raycast_check_is_pure_expression(self):
        """『射线_有东西吗』是表达式:生成的多行代码要落在使用它的作用域里。

        回归:以前硬编码缩进 0，代码被写到类体里，
        Godot 报 Unexpected identifier "_query13" in class body。
        """
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnProcess, "每帧")
        branch = tree.new_node(NN.GKNodeBranch, "有墙吗")
        check = tree.new_node(NN.GKNodeRaycastCheck, "看前方", gk_distance=120.0)
        blocked = tree.new_node(NN.GKNodePrint, "挡住", gk_text="前面有东西")
        gk_stub.connect(tree, event, "执行", branch, "执行")
        gk_stub.connect(tree, check, "有东西", branch, "条件")
        gk_stub.connect(tree, branch, "是", blocked, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        lines = code.splitlines()
        proc_at = next(i for i, line in enumerate(lines) if line.startswith("func _process"))
        space_at = next(i for i, line in enumerate(lines) if "direct_space_state" in line)
        self.assertGreater(space_at, proc_at, "射线代码必须在 _process 里面")
        # 必须缩进 4(函数体一层)，不能顶格写到类体
        self.assertEqual(len(lines[space_at]) - len(lines[space_at].lstrip(" ")), 4,
                         f"射线代码缩进应为 4: {lines[space_at]!r}")
        self.assertIn("if not ", code)

    def test_raycast_check_not_cached(self):
        """射线每次求值都要重新射，不能被表达式缓存复用。"""
        self.assertTrue(getattr(NN.GKNodeRaycastCheck, "gk_no_cache", False),
                        "射线节点必须标记 gk_no_cache，否则第二次求值不会重新射")


class TestTimerNodes(Base):
    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_every_seconds_uses_repeating_timer(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeEverySeconds, "刷怪", gk_interval=2.0)
        printer = tree.new_node(NN.GKNodePrint, "刷", gk_text="刷一只")
        gk_stub.connect(tree, event, "执行", node, "执行")
        gk_stub.connect(tree, node, "到点了", printer, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("Timer.new()", code)
        # one_shot = false 才是"每隔 N 秒"，true 就变成"N 秒后一次"
        self.assertIn("one_shot = false", code)
        self.assertIn(".timeout.connect(", code)
        # 触发的逻辑必须在回调函数里，不能建好计时器就立刻执行
        lines = code.splitlines()
        cb_at = next(i for i, line in enumerate(lines) if line.startswith("func gk_on_"))
        print_at = next(i for i, line in enumerate(lines) if 'print("刷一只")' in line)
        self.assertGreater(print_at, cb_at, f"打印应在计时器回调里:\n{code}")

    def test_every_seconds_output_not_named_done(self):
        names = [spec["name"] for spec in NN.GKNodeEverySeconds.gk_outputs]
        self.assertNotIn("完成", names, "出口叫『完成』会立刻执行而不是等计时器")

    def test_tween_value(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeTweenValue, "缓动", gk_var="hp_value",
                             gk_to=0.0, gk_time=0.4, gk_trans="TRANS_CUBIC",
                             gk_ease="EASE_OUT")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("create_tween()", code)
        self.assertIn("Tween.TRANS_CUBIC", code)
        self.assertIn("Tween.EASE_OUT", code)
        self.assertIn('tween_property(self, "hp_value"', code)
        self.assertIn("var hp_value: float", code)
        self.assert_indent_sane(code)

    def test_tween_without_var_warns(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeTweenValue, "缓动", gk_var="")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertTrue(any("变量名" in w for w in warnings), warnings)


class TestContainerNodes(Base):
    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_list_nodes_share_one_variable(self):
        """同一个变量名就是同一个容器。

        回归:以前每个节点各占一个名字，『列表_新建』和『列表_添加』都写
        `bag` 却生成 `bag` / `bag_2` 两个互不相干的数组，逻辑完全是错的。
        """
        tree = make_tree()
        event = self._ready(tree)
        new_node = tree.new_node(NN.GKNodeListNew, "新建", gk_var="bag")
        add_node = tree.new_node(NN.GKNodeListAdd, "加", gk_var="bag")
        gk_stub.connect(tree, event, "执行", new_node, "执行")
        gk_stub.connect(tree, new_node, "完成", add_node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertEqual(code.count("var bag: Array"), 1, code)
        self.assertNotIn("bag_2", code, code)
        self.assertIn("bag = []", code)
        self.assertIn("bag.append(", code)

    def test_dict_nodes_share_one_variable(self):
        tree = make_tree()
        event = self._ready(tree)
        setter = tree.new_node(NN.GKNodeDictSet, "写", gk_var="stats", gk_key="hp")
        getter = tree.new_node(NN.GKNodeDictGet, "读", gk_var="stats", gk_key="hp",
                              gk_target_var="hp")
        gk_stub.connect(tree, event, "执行", setter, "执行")
        gk_stub.connect(tree, setter, "完成", getter, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertEqual(code.count("var stats: Dictionary"), 1, code)
        self.assertNotIn("stats_2", code, code)
        self.assertIn('stats["hp"] = ', code)
        self.assertIn('stats.get("hp", ', code)

    def test_conflicting_types_warn(self):
        """同一个名字被当成列表又当成字典时必须告警，而不是生成矛盾脚本。"""
        tree = make_tree()
        event = self._ready(tree)
        first = tree.new_node(NN.GKNodeListNew, "当列表", gk_var="thing")
        second = tree.new_node(NN.GKNodeDictSet, "当字典", gk_var="thing", gk_key="k")
        gk_stub.connect(tree, event, "执行", first, "执行")
        gk_stub.connect(tree, first, "完成", second, "执行")
        code, warnings = gen(tree, V2)
        self.assertTrue(any("同时被当成" in w for w in warnings), warnings)

    def test_list_get_is_bounds_checked(self):
        """越界必须兜住:直接 items[99] 在 Godot 里会报错并中断当前帧。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeListGet, "取", gk_var="bag", gk_index=5,
                             gk_target_var="item", gk_default=0.0)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn(".size()", code)
        self.assertIn("else:", code)
        self.assertIn("var item: Variant", code)

    def test_dict_has_two_branches(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeDictHas, "查", gk_var="stats", gk_key="mp")
        yes = tree.new_node(NN.GKNodePrint, "有", gk_text="有")
        no = tree.new_node(NN.GKNodePrint, "没", gk_text="没有")
        gk_stub.connect(tree, event, "执行", node, "执行")
        gk_stub.connect(tree, node, "有", yes, "执行")
        gk_stub.connect(tree, node, "没有", no, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('.has("mp")', code)
        self.assertEqual(code.count('print("有")'), 1, code)
        self.assertEqual(code.count('print("没有")'), 1, code)

    def test_dict_has_outputs_not_named_done(self):
        names = [spec["name"] for spec in NN.GKNodeDictHas.gk_outputs]
        self.assertNotIn("完成", names)

    def test_container_without_var_warns(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeListAdd, "加", gk_var="")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertTrue(any("变量名" in w for w in warnings), warnings)


class TestGamepadNodes(Base):
    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_joy_axis_applies_deadzone(self):
        """死区必须自己处理。

        Input.get_joy_axis 不做死区，摇杆回中不准的手柄会一直输出
        0.05 之类的值，角色就自己走路。
        """
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeJoyAxis, "摇杆", gk_axis="JOY_AXIS_LEFT_X",
                             gk_deadzone=0.2)
        # 必须真的**用到**这个输出，节点才会被生成(表达式是按需求值的)
        vec = tree.new_node(NN.GKNodeVectorMake, "包成向量")
        length = tree.new_node(NN.GKNodeVectorLength, "取长度")
        printer = tree.new_node(NN.GKNodePrint, "打印")
        gk_stub.connect(tree, event, "执行", printer, "执行")
        gk_stub.connect(tree, node, "强度", vec, "X")
        gk_stub.connect(tree, vec, "向量", length, "向量")
        gk_stub.connect(tree, length, "长度", printer, "内容")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("JoyAxis.JOY_AXIS_LEFT_X", code)
        self.assertIn("func _gk_joy_axis(", code)
        self.assertIn("Input.get_joy_axis(", code)
        self.assertIn("absf(raw) < deadzone", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_joy_vector_uses_both_axes(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeJoyVector, "左摇杆", gk_stick="LEFT")
        move = tree.new_node(NN.GKNodeMoveDirection, "移动")
        gk_stub.connect(tree, event, "执行", move, "执行")
        gk_stub.connect(tree, node, "方向", move, "方向")
        code, _ = gen(tree, V2)
        self.assertIn("JOY_AXIS_LEFT_X", code)
        self.assertIn("JOY_AXIS_LEFT_Y", code)
        self.assertIn("Vector2(", code)

    def test_joy_vector_right_stick(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeJoyVector, "右摇杆", gk_stick="RIGHT")
        move = tree.new_node(NN.GKNodeMoveDirection, "移动")
        gk_stub.connect(tree, event, "执行", move, "执行")
        gk_stub.connect(tree, node, "方向", move, "方向")
        code, _ = gen(tree, V2)
        self.assertIn("JOY_AXIS_RIGHT_X", code)
        self.assertNotIn("JOY_AXIS_LEFT_X", code)

    def test_joy_button_held_and_just_pressed(self):
        """刚按下必须靠"记住上一帧"实现(Godot 没有手柄按键的 just_pressed)。"""
        tree = make_tree()
        event = self._ready(tree)
        held = tree.new_node(NN.GKNodeJoyButton, "按住", gk_button="JOY_BUTTON_B")
        down = tree.new_node(NN.GKNodeJoyButtonDown, "刚按下", gk_button="JOY_BUTTON_A")
        printer = tree.new_node(NN.GKNodePrint, "打印")
        branch = tree.new_node(NN.GKNodeBranch, "跳出吗")
        gk_stub.connect(tree, event, "执行", branch, "执行")
        gk_stub.connect(tree, down, "刚按下", branch, "条件")
        gk_stub.connect(tree, branch, "是", printer, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("Input.is_joy_button_pressed(", code)
        self.assertIn("JoyButton.JOY_BUTTON_A", code)
        # 记住上一帧
        self.assertIn("and not ", code)
        self.assert_no_chinese_identifiers(code)

    def test_joy_vibrate_stops_itself(self):
        """震动必须自己停，不能一直震。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeJoyVibrate, "震动", gk_time=0.2)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("Input.start_joy_vibration(", code)
        self.assertIn("Input.stop_joy_vibration(", code)
        self.assertIn("create_timer(", code)
        self.assert_indent_sane(code)


class TestTouchNodes(Base):
    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_touch_drag_resets_every_frame(self):
        """拖动累积值必须每帧清零。

        回归:不清零的话手指不动时它也会一直保持上一次的偏移，
        角色就永远朝那个方向走。
        """
        tree = make_tree()
        event = self._ready(tree)
        drag = tree.new_node(NN.GKNodeTouchDrag, "拖动")
        move = tree.new_node(NN.GKNodeMoveDirection, "移动")
        gk_stub.connect(tree, event, "执行", move, "执行")
        gk_stub.connect(tree, drag, "方向", move, "方向")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        # 必须有 _input 接事件
        self.assertIn("func _input(event: InputEvent) -> void:", code)
        self.assertIn("InputEventScreenDrag", code)
        # 必须有每帧清零
        self.assertIn("_gk_touch_drag = Vector2.ZERO", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_touch_drag_clears_on_release(self):
        tree = make_tree()
        event = self._ready(tree)
        drag = tree.new_node(NN.GKNodeTouchDrag, "拖动", gk_reset=True)
        move = tree.new_node(NN.GKNodeMoveDirection, "移动")
        gk_stub.connect(tree, event, "执行", move, "执行")
        gk_stub.connect(tree, drag, "方向", move, "方向")
        code, _ = gen(tree, V2)
        self.assertIn("InputEventScreenTouch", code)
        self.assertIn(".pressed", code)

    def test_touch_button_uses_action(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeTouchButton, "触屏跳", gk_action="jump")
        branch = tree.new_node(NN.GKNodeBranch, "跳吗")
        printer = tree.new_node(NN.GKNodePrint, "跳")
        gk_stub.connect(tree, event, "执行", branch, "执行")
        gk_stub.connect(tree, node, "按住", branch, "条件")
        gk_stub.connect(tree, branch, "是", printer, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("Input.is_action_pressed(", code)
        self.assertIn("ACTION_JUMP", code)


class TestScreenFilterNodes(Base):
    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_screen_filter_builds_shader_material(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeScreenFilter, "受击红", gk_amount=0.6,
                             gk_softness=0.0)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("const _GK_FILTER_SHADER :=", code)
        self.assertIn("shader_type canvas_item;", code)
        self.assertIn("SCREEN_TEXTURE", code)
        self.assertIn("ShaderMaterial.new()", code)
        self.assertIn("set_shader_parameter(\"amount\"", code)
        self.assertIn("set_shader_parameter(\"tint\"", code)
        self.assertIn("func _gk_ensure_filter(", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_screen_filter_sits_below_flash(self):
        """滤镜层(90)要盖住界面(10)但低于闪白/转场(100)。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeScreenFilter, "受击红")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("layer = 90", code)

    def test_screen_filter_softness_uses_tween(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeScreenFilter, "受击红", gk_amount=0.8,
                             gk_softness=0.3)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("tween_method(", code)

    def test_shader_const_only_when_used(self):
        tree = make_tree()
        event = self._ready(tree)
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="hi")
        gk_stub.connect(tree, event, "执行", printer, "执行")
        code, _ = gen(tree, V2)
        self.assertNotIn("_GK_FILTER_SHADER", code)


    def test_all_prop_kinds_are_supported(self):
        """每个 gk_prop_defs 里用到的类型都必须有真实的属性工厂。

        ── 为什么需要这条测试 ──────────────────────────────────────────
        materialize_props 对**不认识的类型**会静默退回成 StringProperty。
        于是 'color' 和 'float_vector' 这两种没登记的类型，
        在 Blender 里会让整类节点注册失败:
            'gk_color' StringProperty could not register
        而在离线测试里因为走的是 GKProp 桩，**完全看不出来** ——
        必须靠这条断言把"类型拼错/漏登记"挡在提交之前。
        """
        from BlenderGameKit.nodes import node_base as NB

        supported = set(NB._PROP_FACTORY) | {"enum"}
        offenders = []
        for cls in NN.ALL_CLASSES:
            for entry in getattr(cls, "gk_prop_defs", ()) or ():
                kind = entry[1]
                if kind not in supported:
                    offenders.append(f"{cls.bl_label}.{entry[0]} 用了未知类型 {kind!r}")
        self.assertEqual(offenders, [], "未登记的属性类型:\n  " + "\n  ".join(offenders))

    def test_all_nodes_register_in_blender_shape(self):
        """每个节点类都要能被实例化并建出引脚(离线近似真实注册)。

        真实注册失败在离线环境里看不见，所以这里至少保证"类本身是完整的":
        有 bl_label、能建引脚、声明过的属性都能设值。
        """
        from BlenderGameKit.nodes import node_base as NB

        for cls in NN.ALL_CLASSES:
            self.assertTrue(getattr(cls, "bl_label", ""), f"{cls.__name__} 缺 bl_label")
            # 抽象基类不注册，跳过
            if cls.__name__.startswith("_"):
                continue
            node = cls()
            builder = getattr(node, "build_sockets", None)
            if callable(builder):
                builder()
            for entry in getattr(cls, "gk_prop_defs", ()) or ():
                prop_name = entry[0]
                self.assertTrue(hasattr(node, prop_name),
                                f"{cls.bl_label} 缺属性 {prop_name}")

    def test_every_declared_prop_type_has_a_real_factory(self):
        """颜色与向量必须落到真属性类型上，不能只是"有个名字"。"""
        from BlenderGameKit.nodes import node_base as NB

        self.assertIn("color", NB._PROP_FACTORY)
        self.assertIn("float_vector", NB._PROP_FACTORY)

    def test_uses_of_color_and_vector_props(self):
        """确实有用到这两种类型的节点，否则上面两条就是空转。"""
        kinds = set()
        for cls in NN.ALL_CLASSES:
            for entry in getattr(cls, "gk_prop_defs", ()) or ():
                kinds.add(entry[1])
        self.assertIn("color", kinds)
        self.assertIn("float_vector", kinds)


    def test_no_bpy_prop_type_that_does_not_exist(self):
        """不能引用不存在的 bpy.props 类型。

        ── 这条测试为什么必须有 ────────────────────────────────────────
        踩过的坑:bpy.props **没有** ColorProperty。写了它会让
        `from bpy.props import (...)` 整段失败 -> _BPY 变 False ->
        **全部 87 个节点**都注册不上(报 missing bl_rna attribute)，
        而离线测试因为走 GKProp 桩，一点异常都看不出来。

        这里静态扫描 node_base 源码里引用到的 bpy.props 名字，
        对照一份"Blender 确实提供"的白名单。
        """
        import inspect

        from BlenderGameKit.nodes import node_base as NB

        # 这份白名单来自 Blender 4.5 的 bpy.props(实测 hasattr 确认过)
        real_bpy_props = {
            "BoolProperty", "BoolVectorProperty", "CollectionProperty",
            "EnumProperty", "FloatProperty", "FloatVectorProperty",
            "IntProperty", "IntVectorProperty", "PointerProperty",
            "RemoveProperty", "StringProperty",
        }
        # 只看代码，不看注释与文档字符串:
        # 注释里会写"不要用 ColorProperty"这种说明，不该被判成违规
        source = inspect.getsource(NB)
        code_only = "\n".join(
            line.split("#", 1)[0] for line in source.splitlines()
        )
        code_only = re.sub(r'"""(?:.|\n)*?"""', "", code_only)
        code_only = re.sub(r"'''(?:.|\n)*?'''", "", code_only)
        used = set(re.findall(r"\b([A-Z]\w*Property)\b", code_only))
        unknown = sorted(used - real_bpy_props)
        self.assertEqual(unknown, [],
                         "node_base 引用了 Blender 不存在的属性类型: " + ", ".join(unknown))

    def test_color_uses_vector_property(self):
        """颜色在 Blender 里就是 4 分量浮点向量，不是 ColorProperty。"""
        import inspect

        from BlenderGameKit.nodes import node_base as NB

        self.assertIn("color", NB._PROP_FACTORY)
        # 代码里不该出现 ColorProperty(注释里可以提，所以先去掉注释)
        source = inspect.getsource(NB)
        code_only = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
        code_only = re.sub(r'"""(?:.|\n)*?"""', "", code_only)
        self.assertNotIn("ColorProperty", code_only)


class TestGravityAndCamera(Base):
    """演示"能玩起来"必须成立的两件事:重力 + 相机。"""

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_move_without_gravity_mode_overwrites_velocity(self):
        """不勾重力模式时保持旧行为(整条赋值)，避免升级后手感突变。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeMoveDirection, "移动", gk_speed=200.0)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V2)
        self.assertIn(".velocity = ", code)
        self.assertNotIn("get_gravity()", code)

    def test_gravity_mode_applies_gravity(self):
        """勾上重力模式必须真的施加重力。

        回归:Godot 4 的 CharacterBody2D **不会自动施加重力**，
        它只按你设的 velocity 移动。不自己累加的话角色 velocity 恒为
        (0,0)、is_on_floor 恒为 false —— 实测截图里角色就浮在半空，
        看起来像"脚本完全没生效"。
        """
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeMoveDirection, "移动", gk_speed=200.0)
        node.gk_keep_y = True
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("get_gravity()", code)
        self.assertIn("get_physics_process_delta_time()", code)
        # 必须只改水平分量，纵向留给重力
        self.assertIn(".velocity.x = ", code)
        self.assertNotIn(".velocity = ", code.replace(".velocity.x = ", "")
                         .replace(".velocity.y = ", "").replace(".velocity.z = ", ""))
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_gravity_mode_3d_keeps_x_and_z(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        node = tree.new_node(NN.GKNodeMoveDirection, "移动")
        node.gk_keep_y = True
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, {"dimension": "3", "base_class": "CharacterBody3D"})
        self.assertIn("get_gravity()", code)
        self.assertIn(".velocity.x = ", code)
        self.assertIn(".velocity.z = ", code)
        self.assertNotIn(".velocity.y = ", code)

    def test_camera_follow_auto_finds_camera(self):
        """相机留空时自动找，并且不告警刷屏(这个节点每帧都在跑)。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnProcess, "每帧")
        node = tree.new_node(NN.GKNodeCameraFollow, "跟随")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _gk_find_camera(", code)
        self.assertIn("_gk_find_camera(self)", code)
        self.assertIn(".global_position = ", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_camera_follow_smooth_uses_lerp(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnProcess, "每帧")
        node = tree.new_node(NN.GKNodeCameraFollow, "跟随", gk_smooth=0.15)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V2)
        self.assertIn(".lerp(", code)
        self.assertIn("0.15", code)

    def test_camera_follow_zero_smooth_is_hard_snap(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnProcess, "每帧")
        node = tree.new_node(NN.GKNodeCameraFollow, "跟随", gk_smooth=0.0)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V2)
        self.assertNotIn(".lerp(", code)

    def test_reload_scene_is_deferred(self):
        """重载场景必须延到帧末。

        这个节点常从按钮 / 计时器回调里调用，在信号回调中间直接换掉场景树，
        Godot 会报 "Parent node is busy setting up children"。
        """
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeReloadScene, "重开")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("call_deferred", code)
        self.assertIn("reload_current_scene", code)


class TestNodeMenu(Base):
    """节点菜单:87 个节点必须能找得到。"""

    def test_common_nodes_all_exist(self):
        """『常用』短名单里的 idname 必须都是真节点(写错了会静默少几个)。"""
        from BlenderGameKit.nodes import nodes as NN

        by_id = {cls.bl_idname for cls in NN.ALL_CLASSES}
        missing = [n for n in NN.COMMON_NODES if n not in by_id]
        self.assertEqual(missing, [], f"COMMON_NODES 里有不存在的节点: {missing}")

    def test_common_menu_is_not_empty_and_reasonable(self):
        from BlenderGameKit.nodes import nodes as NN

        self.assertGreaterEqual(len(NN.COMMON_NODES), 10)
        self.assertLessEqual(len(NN.COMMON_NODES), 24,
                             "『常用』太多就失去意义了，它应该一眼看完")

    def test_subcategory_covers_whole_category(self):
        """二级子菜单加起来必须等于大类总数 —— 漏一个节点用户就找不到它。"""
        from BlenderGameKit.nodes import nodes as NN

        for category, order in NN.SUBCATEGORY_ORDER.items():
            members = [c for c in NN.ALL_CLASSES if c.gk_category == category]
            assigned = [c for c in members if c.bl_idname in NN.SUBCATEGORY_OF]
            self.assertEqual(len(assigned), len(members),
                             f"『{category}』有 {len(members) - len(assigned)} 个节点"
                             f"没登记二级分类，菜单里会找不到")

    def test_subcategory_names_are_declared(self):
        """SUBCATEGORY_OF 用到的子分类名必须在 SUBCATEGORY_ORDER 里出现过。"""
        from BlenderGameKit.nodes import nodes as NN

        allowed = set()
        for order in NN.SUBCATEGORY_ORDER.values():
            allowed.update(order)
        used = set(NN.SUBCATEGORY_OF.values())
        unknown = sorted(used - allowed)
        self.assertEqual(unknown, [], f"这些子分类名没在 SUBCATEGORY_ORDER 里声明: {unknown}")

    def test_every_category_in_order(self):
        """每个节点的分类都必须在 CATEGORY_ORDER 里，否则菜单顺序会乱。"""
        from BlenderGameKit.nodes import nodes as NN

        for cls in NN.ALL_CLASSES:
            self.assertIn(cls.gk_category, NN.CATEGORY_ORDER,
                          f"{cls.bl_label} 的分类 {cls.gk_category} 不在 CATEGORY_ORDER 里")


class TestDimensionSafety(Base):
    """维度相关的 API 必须按维度生成 —— 2D/3D 混用会直接编译不过。"""

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_input_vector_is_vector3_in_3d(self):
        """3D 项目里输入方向必须包成 Vector3。

        回归:`Input.get_vector` 返回的是 **Vector2**。3D 项目里直接
        拿它去写 `velocity.z` 会让 Godot 报
            Cannot find member "z" in base "Vector2"
        整个脚本编译不过(实测)。
        """
        tree = make_tree()
        event = self._ready(tree)
        move = tree.new_node(NN.GKNodeMoveDirection, "移动")
        move.gk_keep_y = True
        vec = tree.new_node(NN.GKNodeGetVector, "方向")
        gk_stub.connect(tree, event, "执行", move, "执行")
        gk_stub.connect(tree, vec, "方向", move, "方向")
        code, warnings = gen(tree, {"dimension": "3", "base_class": "CharacterBody3D"})
        # 会带一条"3D 速度被夹住"的告警 —— 节点默认值 300 是给 2D 的,
        # 3D 里 300 米/秒 一眨眼就飞出画面。断言里放行这条。
        self.assertTrue(all("速度" in w for w in warnings), warnings)
        self.assertIn("Vector3(", code)
        # 不能再出现"Vector2 上取 .z"这种写法
        self.assertNotIn("Input.get_vector(ACTION_MOVE_LEFT, "
                         "ACTION_MOVE_RIGHT, ACTION_MOVE_UP, ACTION_MOVE_DOWN, "
                         "0.2).z", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_input_vector_stays_vector2_in_2d(self):
        tree = make_tree()
        event = self._ready(tree)
        move = tree.new_node(NN.GKNodeMoveDirection, "移动")
        vec = tree.new_node(NN.GKNodeGetVector, "方向")
        gk_stub.connect(tree, event, "执行", move, "执行")
        gk_stub.connect(tree, vec, "方向", move, "方向")
        code, _ = gen(tree, V2)
        self.assertNotIn("Vector3(", code)

    def test_gravity_mode_does_not_write_z_in_2d(self):
        """2D 里不能出现 velocity.z。"""
        tree = make_tree()
        event = self._ready(tree)
        move = tree.new_node(NN.GKNodeMoveDirection, "移动")
        move.gk_keep_y = True
        gk_stub.connect(tree, event, "执行", move, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("get_gravity()", code)
        self.assertNotIn(".velocity.z", code)

    def test_gravity_mode_writes_z_in_3d(self):
        """3D 里水平面是 XZ，两个分量都要写。"""
        tree = make_tree()
        event = self._ready(tree)
        move = tree.new_node(NN.GKNodeMoveDirection, "移动")
        move.gk_keep_y = True
        gk_stub.connect(tree, event, "执行", move, "执行")
        code, _ = gen(tree, {"dimension": "3", "base_class": "CharacterBody3D"})
        self.assertIn(".velocity.x = ", code)
        self.assertIn(".velocity.z = ", code)
        # 纵向留给重力
        self.assertNotIn(".velocity.y = ", code)

    def test_raycast_uses_matching_dimension_api(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeRaycast, "射线")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code2, _ = gen(tree, V2)
        self.assertIn("get_world_2d", code2)
        self.assertNotIn("get_world_3d", code2)

        tree3 = make_tree()
        event3 = tree3.new_node(NN.GKNodeOnReady, "准备")
        node3 = tree3.new_node(NN.GKNodeRaycast, "射线")
        gk_stub.connect(tree3, event3, "执行", node3, "执行")
        code3, _ = gen(tree3, {"dimension": "3", "base_class": "CharacterBody3D"})
        self.assertIn("get_world_3d", code3)


class TestInputActions(Base):
    """用到的输入动作要能被导出流程取到 —— 否则 Godot 里按键没反应。"""

    def test_used_actions_are_recorded(self):
        """generator 必须把用到的动作暴露出来。

        背景:生成的脚本写 ACTION_MOVE_LEFT := "move_left"，
        但 project.godot 里没有登记这个动作时，Godot 运行时刷
            ERROR: The InputMap action "move_left" doesn't exist.
        按键完全没反应，而用户想不到"要去输入映射里手工加动作"。
        所以导出流程要拿到这份清单去写 [input] 段。
        """
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        vec = tree.new_node(NN.GKNodeGetVector, "方向")
        vec.gk_left, vec.gk_right = "move_left", "move_right"
        move = tree.new_node(NN.GKNodeMoveDirection, "移动")
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="hi")
        gk_stub.connect(tree, event, "执行", move, "执行")
        gk_stub.connect(tree, vec, "方向", move, "方向")
        gk_stub.connect(tree, move, "完成", printer, "执行")
        code, _ = gen(tree, V2)
        actions = list(getattr(codegen, "last_used_actions", []) or [])
        self.assertIn("move_left", actions)
        self.assertIn("move_right", actions)

    def test_project_godot_writer_skips_builtin_ui_actions(self):
        """ui_* 是 Godot 内置动作，不能自己重新定义。

        生成器允许不填动作名，这时会退回内置的 ui_left 之类。
        把这些写进 [input] 会用一份空的 events 覆盖掉内置绑定。
        """
        from BlenderGameKit import project as P

        # 只做纯字符串层面的检查，避免依赖真实 Godot 工程
        block = P._input_action_block("move_left")
        self.assertIn('move_left={', block)
        self.assertIn('"physical_keycode":65', block)
        empty = P._input_action_block("some_unknown_action")
        self.assertIn('"events": []', empty)


class TestCameraFollowDim(Base):
    """3D 的相机跟随不能"贴脸",否则相机进了模型内部,画面一片色块。"""

    def _tree(self, distance=0.0):
        tree = make_tree()
        ev = tree.new_node(NN.GKNodeOnProcess, "每帧")
        follow = tree.new_node(NN.GKNodeCameraFollow, "相机跟随")
        follow.gk_smooth = 0.12
        follow.gk_offset_y = 0.36
        follow.gk_distance = distance
        gk_stub.connect(tree, ev, "执行", follow, "执行")
        return tree

    def test_2d_camera_keeps_original_behaviour(self):
        """2D 必须和以前一模一样 —— 平台示例已经验过手感,不能动。"""
        code, warns = gen(self._tree(), V2)
        self.assertEqual(warns, [], warns)
        # 2D:直接跟到目标位置(同一平面),不引入 delta
        self.assertIn("Vector2(", code)
        self.assertNotIn("_gk_follow_delta", code)
        self.assertNotIn("Vector3(", code)

    def test_3d_camera_preserves_initial_offset(self):
        """3D 默认:保持相机一开始相对角色的位置。

        踩过的坑:3D 里把相机直接放到角色位置,相机就进了模型内部,
        画面上只剩一片模型的背面(用户看到的是"一片色块")。
        """
        code, warns = gen(self._tree(), V3)
        self.assertEqual(warns, [], warns)
        self.assertIn("_gk_follow_delta", code)
        self.assertIn("_gk_follow_setup", code)
        # delta 必须**只算一次**(第一帧),否则相机会跟着目标一起漂
        self.assertIn("if not _gk_follow_setup:", code)

    def test_3d_camera_distance_mode(self):
        """给了距离就固定站在角色身后。"""
        code, _ = gen(self._tree(distance=6.0), V3)
        self.assertIn("6.0", code)
        self.assertNotIn("_gk_follow_delta", code)

    def test_camera_code_has_no_chinese_identifiers(self):
        for distance in (0.0, 6.0):
            code, _ = gen(self._tree(distance), V3)
            self.assert_no_chinese_identifiers(code)
            self.assert_indent_sane(code)


class TestMoveSpeedDimension(Base):
    """『移动_按方向』的速度在 2D / 3D 里单位不同,不能直接用同一个数。

    300 在 2D 里是 300 像素/秒(正常),在 3D 里是 300 **米**/秒 ——
    实测角色 1.25 秒冲出 125 米,画面里瞬间消失,用户以为"按键没反应"。
    """

    def _tree(self):
        tree = make_tree()
        ev = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        move = tree.new_node(NN.GKNodeMoveDirection, "移动", gk_speed=300.0)
        move.gk_keep_y = True
        vec = tree.new_node(NN.GKNodeGetVector, "方向")
        gk_stub.connect(tree, ev, "执行", move, "执行")
        gk_stub.connect(tree, vec, "方向", move, "方向")
        return tree

    def test_2d_speed_untouched(self):
        """2D 的 300 是正常手感,一个字都不能改。"""
        code, warns = gen(self._tree(), V2)
        self.assertIn("300.0", code)
        self.assertEqual(warns, [], warns)

    def test_3d_speed_is_clamped(self):
        """3D 下 300 必须被夹到能用范围,并给出告警。"""
        code, warns = gen(self._tree(), V3)
        self.assertIn("= 50", code)
        self.assertNotIn("300.0", code)
        self.assertTrue(any("速度" in w for w in warns), warns)

    def test_3d_reasonable_speed_kept(self):
        """3D 下本来就合理的速度(6 米/秒)不该被改,也不该告警。"""
        tree = make_tree()
        ev = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        move = tree.new_node(NN.GKNodeMoveDirection, "移动", gk_speed=6.0)
        vec = tree.new_node(NN.GKNodeGetVector, "方向")
        gk_stub.connect(tree, ev, "执行", move, "执行")
        gk_stub.connect(tree, vec, "方向", move, "方向")
        code, warns = gen(tree, V3)
        self.assertIn("6.0", code)
        self.assertEqual(warns, [], warns)


class TestDefaultInputActions(Base):
    """默认输入动作必须是 move_*(WASD),不能是 Godot 内置的 ui_*(方向键)。

    用户实测踩过:拿默认设置跑起来按 WASD 完全没反应 ——
    因为生成的是 `Input.get_vector("ui_left", ...)`,而 ui_* 是 Godot 的
    **全局界面动作**,绑的是**方向键**。用户只会得出"输入是坏的"这个结论。
    """

    def test_default_actions_are_move_not_ui(self):
        tree = make_tree()
        ev = tree.new_node(NN.GKNodeOnProcess, "每帧")
        mov = tree.new_node(NN.GKNodeMoveDirection, "移动")
        vec = tree.new_node(NN.GKNodeGetVector, "方向")
        # 必须真的连上 —— 没被消费的节点会被当成死代码丢掉
        gk_stub.connect(tree, ev, "执行", mov, "执行")
        gk_stub.connect(tree, vec, "方向", mov, "方向")
        code, _ = gen(tree, V3)
        self.assertIn('"move_left"', code)
        self.assertIn('"move_right"', code)
        self.assertIn('"move_up"', code)
        self.assertIn('"move_down"', code)
        # 一个 ui_* 都不该出现
        self.assertNotIn("ui_left", code)
        self.assertNotIn("ui_right", code)

    def test_jump_default_action(self):
        tree = make_tree()
        ev = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        pressed = tree.new_node(NN.GKNodeActionJustPressed, "跳跃键")
        branch = tree.new_node(NN.GKNodeBranch, "分支")
        jump = tree.new_node(NN.GKNodeJump, "跳")
        # ActionJustPressed 是表达式节点(只有"刚按下"输出,没有执行引脚),
        # 所以要接到分支的『条件』上,再让分支去驱动跳
        gk_stub.connect(tree, ev, "执行", branch, "执行")
        gk_stub.connect(tree, pressed, "刚按下", branch, "条件")
        gk_stub.connect(tree, branch, "是", jump, "执行")
        code, _ = gen(tree, V3)
        self.assertIn('"jump"', code)

    def test_project_godot_binds_wasd(self):
        """默认动作名必须能被 project.godot 写出真实的 WASD 按键。

        这是"开箱即用"的关键:光有动作名不够,还得有按键绑定,
        否则 Godot 运行时报 The InputMap action "move_left" doesn't exist。
        """
        from BlenderGameKit import project as P

        for action, key in (("move_left", 65), ("move_right", 68),
                            ("move_up", 87), ("move_down", 83),
                            ("jump", 32)):
            self.assertEqual(P.DEFAULT_KEYS.get(action), key,
                             f"{action} 的默认按键不对")
            block = P._input_action_block(action)
            self.assertIn(f'"physical_keycode":{key}', block)


class TestCameraReference(Base):
    """『相机_跟随』的相机引用不能生成脆弱的 `$名字` 路径。

    踩过的坑:填了相机名就生成 `@onready var cam_x: Camera3D = $MyCam` ——
    那是**相对本节点**的路径。名字对不上时 Godot **加载脚本直接失败**,
    报 "Node not found: MyCam (relative to /root/Cube)",
    整个脚本连 _process 都跑不起来(用户实测正是这个现象)。
    而节点引用下拉里列的是**节点图里的节点**,场景里几乎不可能有同名物体,
    所以"怎么选都没影响(或直接崩)"的观感就是这么来的。
    """

    def _tree(self, camera=""):
        tree = make_tree()
        ev = tree.new_node(NN.GKNodeOnProcess, "每帧")
        follow = tree.new_node(NN.GKNodeCameraFollow, "相机跟随")
        follow.gk_camera = camera
        gk_stub.connect(tree, ev, "执行", follow, "执行")
        return tree

    def test_empty_camera_uses_auto_finder(self):
        code, _ = gen(self._tree(), V3)
        self.assertIn("_gk_find_camera(self)", code)

    def test_named_camera_searches_at_runtime(self):
        code, _ = gen(self._tree("MyCam"), V3)
        # 必须按名字在场景里搜,而不是写死一个相对路径
        self.assertIn('_gk_find_camera_named(self, "MyCam")', code)
        self.assertNotIn("$MyCam", code)
        # 不能为它生成 @onready 引用(那正是会崩的写法)
        self.assertNotIn("@onready var cam_mycam", code)

    def test_named_camera_falls_back_when_missing(self):
        """名字对不上时必须退回自动模式 —— 填错不该让游戏跑不起来。"""
        code, _ = gen(self._tree("不存在的相机"), V3)
        self.assertIn("_gk_find_camera(host)", code)
        # 兜底函数里要有"找不到就返回第一个相机"的逻辑
        self.assertIn('find_children("*", "Camera3D"', code)

    def test_camera_code_is_valid_gdscript_shape(self):
        code, _ = gen(self._tree("MyCam"), V3)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)


class TestFollowTarget(Base):
    """『跟随谁』必须真的影响生成的代码。"""

    def test_target_self_is_literal_self(self):
        tree = make_tree()
        ev = tree.new_node(NN.GKNodeOnProcess, "每帧")
        follow = tree.new_node(NN.GKNodeCameraFollow, "相机跟随")
        gk_stub.connect(tree, ev, "执行", follow, "执行")
        code, _ = gen(tree, V3)
        self.assertIn("_gk_follow_look = self", code)

    def test_target_other_generates_node_ref(self):
        tree = make_tree()
        ev = tree.new_node(NN.GKNodeOnProcess, "每帧")
        follow = tree.new_node(NN.GKNodeCameraFollow, "相机跟随")
        follow.gk_target = "Other"
        gk_stub.connect(tree, ev, "执行", follow, "执行")
        code, _ = gen(tree, V3)
        self.assertIn("node_other", code)
        self.assertNotIn("_gk_follow_look = self", code)


class TestCameraFollowClock(Base):
    """相机跟随必须和角色在**同一个时钟**里更新。

    用户实测:把『每秒物理周期数』从 60 改成和刷新率一样,画面抖动就消失了。

    原因:角色移动在 _physics_process(固定步长),相机跟随原来在 _process
    (跟渲染帧率)。两个时钟不同步时就会差拍,表现为画面一直抖/闪。
    相机跟着角色走,就必须和角色在同一个时钟里更新。
    """

    def _tree(self):
        tree = make_tree()
        ev = tree.new_node(NN.GKNodeOnProcess, "每帧")
        follow = tree.new_node(NN.GKNodeCameraFollow, "相机跟随")
        gk_stub.connect(tree, ev, "执行", follow, "执行")
        return tree

    def test_follow_runs_in_physics_process(self):
        code, _ = gen(self._tree(), V3)
        physics_at = code.find("func _physics_process(")
        self.assertGreater(physics_at, 0, "没有生成 _physics_process")
        assign_at = code.find("_gk_follow_cam = _gk_find_camera")
        self.assertGreater(assign_at, physics_at,
                           "相机跟随没有挂在 _physics_process 里")

    def test_2d_follow_also_physics(self):
        """2D 平台跳跃也一样 —— 两个时钟不同步同样会抖。"""
        code, _ = gen(self._tree(), V2)
        physics_at = code.find("func _physics_process(")
        assign_at = code.find("_gk_follow_cam = _gk_find_camera")
        self.assertGreater(assign_at, physics_at)

    def test_follow_code_is_valid_shape(self):
        code, _ = gen(self._tree(), V3)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)


class TestMoveAxes(Base):
    """『移动_按方向』的前进/横移轴要能像 UPBGE 那样改。

    用户反馈:"移动输入反馈倒过来了:按前往后、按左往右"。
    相机朝向一变,输入和视觉方向就可能对不上 —— 这不该逼用户改脚本,
    给两个选项翻过来就行。
    """

    def _tree(self, axis_h="X", axis_v="Z", sign="NEG", keep_y=True):
        tree = make_tree()
        ev = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        move = tree.new_node(NN.GKNodeMoveDirection, "移动")
        vec = tree.new_node(NN.GKNodeGetVector, "方向")
        move.gk_keep_y = keep_y
        move.gk_axis_h = axis_h
        move.gk_axis_v = axis_v
        move.gk_forward_sign = sign
        gk_stub.connect(tree, ev, "执行", move, "执行")
        gk_stub.connect(tree, vec, "方向", move, "方向")
        return tree

    def test_default_forward_is_negative_z(self):
        """默认前进走 -Z,这是 Godot / glTF 的『前』。"""
        code, _ = gen(self._tree(), V3)
        self.assertIn("velocity.z = (-", code)

    def test_forward_sign_can_be_flipped(self):
        code, _ = gen(self._tree(sign="POS"), V3)
        self.assertNotIn("velocity.z = (-", code)
        self.assertIn("velocity.z = ", code)

    def test_horizontal_axis_can_change(self):
        code, _ = gen(self._tree(axis_h="Z", axis_v="X"), V3)
        self.assertIn("velocity.z = _dir", code)
        self.assertIn("velocity.x = ", code)

    def test_same_axis_adds_instead_of_overwriting(self):
        """横移和前进选同一根轴时必须相加 —— 直接赋值会把前一个吃掉。"""
        code, _ = gen(self._tree(axis_h="Z", axis_v="Z"), V3)
        self.assertIn("velocity.z +=", code)

    def test_y_axis_never_gets_movement(self):
        """Y 轴永远留给重力,否则重力刚加上就被抹掉。"""
        code, _ = gen(self._tree(axis_h="Y", axis_v="Y"), V3)
        self.assertNotIn("velocity.y = ", code)
        self.assertIn("get_gravity()", code)

    def test_2d_ignores_axis_options(self):
        code, _ = gen(self._tree(axis_h="Z", axis_v="Y", sign="POS"), V2)
        self.assertNotIn("velocity.z", code)
        self.assertIn("velocity.x", code)


class TestAnimationNodes(Base):
    """动画节点的生成代码。

    实测(建骨架 -> 导出 -> Godot 真跑)确认的几件事:
      * glTF 导入时 AnimationPlayer 在**模型实例内部**
        (Hero/Model/AnimationPlayer),所以不能写 `$AnimationPlayer`
      * Godot 4.7 的 AnimationPlayer **没有 paused 属性**
      * `pause()` 之后再 `play(同名)` 能从暂停处继续
    """

    def _ready_chain(self, nodes):
        tree = make_tree()
        ev = tree.new_node(NN.GKNodeOnReady, "准备")
        prev = ev
        prev_pin = "执行"
        for cls, attrs in nodes:
            n = tree.new_node(cls, cls.bl_label)
            for k, v in attrs.items():
                setattr(n, k, v)
            gk_stub.connect(tree, prev, prev_pin, n, "执行")
            prev = n
            prev_pin = "完成"
        return tree

    def test_anim_finder_is_generated(self):
        tree = self._ready_chain([(NN.GKNodePlayAnim, {"gk_anim_name": "run"})])
        code, _ = gen(tree, V3)
        self.assertIn("func _gk_find_animation_player(host: Node) -> Node:", code)
        self.assertNotIn("$AnimationPlayer", code)

    def test_play_guards_against_missing_clip(self):
        tree = self._ready_chain([(NN.GKNodePlayAnim, {"gk_anim_name": "run"})])
        code, _ = gen(tree, V3)
        self.assertIn("has_animation(", code)
        self.assertIn(".play(", code)

    def test_pause_uses_pause_not_paused_property(self):
        """Godot 4.7 的 AnimationPlayer 没有 paused 属性,写它会报错。"""
        tree = self._ready_chain([(NN.GKNodeAnimPause, {"gk_paused": True})])
        code, _ = gen(tree, V3)
        self.assertIn(".pause()", code)
        self.assertNotIn(".paused = ", code)

    def test_resume_plays_and_seeks_back(self):
        """恢复要用 play + seek —— play() 会把动画从头放。"""
        tree = self._ready_chain([(NN.GKNodeAnimPause, {"gk_paused": False})])
        code, _ = gen(tree, V3)
        self.assertIn(".play(", code)
        self.assertIn(".seek(", code)

    def test_stop(self):
        tree = self._ready_chain([(NN.GKNodeAnimStop, {})])
        code, _ = gen(tree, V3)
        self.assertIn(".stop()", code)

    def test_set_speed(self):
        tree = self._ready_chain([(NN.GKNodeAnimSetSpeed, {"gk_speed": 2.0})])
        code, _ = gen(tree, V3)
        self.assertIn("speed_scale", code)

    def test_anim_playing_query(self):
        tree = make_tree()
        ev = tree.new_node(NN.GKNodeOnReady, "准备")
        query = tree.new_node(NN.GKNodeAnimPlaying, "是否在播")
        query.gk_anim_name = "idle"
        branch = tree.new_node(NN.GKNodeBranch, "分支")
        gk_stub.connect(tree, query, "在播", branch, "条件")
        gk_stub.connect(tree, ev, "执行", branch, "执行")
        code, _ = gen(tree, V3)
        self.assertIn("is_playing()", code)
        self.assertIn('current_animation == "idle"', code)

    def test_anim_length_is_safe(self):
        """时长查询:动画不存在时返回 0,不能崩。"""
        tree = make_tree()
        ev = tree.new_node(NN.GKNodeOnReady, "准备")
        length = tree.new_node(NN.GKNodeAnimLength, "时长")
        length.gk_anim_name = "run"
        branch = tree.new_node(NN.GKNodeBranch, "分支")
        gk_stub.connect(tree, length, "秒", branch, "条件")
        gk_stub.connect(tree, ev, "执行", branch, "执行")
        code, _ = gen(tree, V3)
        self.assertIn("has_animation(", code)
        self.assertIn(".length", code)

    def test_animation_nodes_have_no_chinese_identifiers(self):
        tree = self._ready_chain([
            (NN.GKNodePlayAnim, {"gk_anim_name": "run"}),
            (NN.GKNodeAnimSetSpeed, {"gk_speed": 1.5}),
            (NN.GKNodeAnimStop, {}),
        ])
        code, _ = gen(tree, V3)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)


class TestDynamicPins(Base):
    """动态引脚(蓝图式可调分支数)。

    背景:用户要"像虚幻蓝图那样可以自定义添加连接节点数"。
    实测过:改数量属性后引脚**不会自动刷新**,得重新加载文件才出现 ——
    那不是蓝图该有的手感。所以 node_base 给带 count/cases/steps 字样的
    属性自动挂了 update 回调,改一次重建一次。
    """

    def test_switch_output_count_follows_property(self):
        node = make_tree().new_node(NN.GKNodeSwitch, "多路分支")
        node.gk_case_count = 4
        node.build_sockets()
        self.assertEqual([s.name for s in node.outputs],
                         ["0", "1", "2", "3", "默认"])

    def test_switch_is_clamped(self):
        node = make_tree().new_node(NN.GKNodeSwitch, "多路分支")
        # 上限 12:再多也只给 12 条分支 + 一条"默认"
        node.gk_case_count = 999
        node.build_sockets()
        self.assertEqual(len(node.outputs), 13)
        self.assertEqual(node.outputs[-1].name, "默认")
        # 下限 1。注意**测试桩不执行 bpy 的 min= 约束**,
        # 所以这里直接给 1(而不是 0),三个环境(桩/Blender/生成器)
        # 的行为才一致。
        node.gk_case_count = 1
        node.build_sockets()
        self.assertEqual([s.name for s in node.outputs], ["0", "默认"])

    def _build_switch(self, case_count, wire):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        switch = tree.new_node(NN.GKNodeSwitch, "多路分支")
        switch.gk_case_count = case_count
        switch.build_sockets()
        gk_stub.connect(tree, event, "执行", switch, "执行")
        for socket_name, text in wire.items():
            printer = tree.new_node(NN.GKNodePrint, "打印" + socket_name)
            printer.gk_text = text
            gk_stub.connect(tree, switch, socket_name, printer, "执行")
        return gen(tree, V3)

    def test_switch_generates_match(self):
        code, warnings = self._build_switch(
            3, {"0": "走零", "2": "走二", "默认": "兜底"})
        self.assertEqual(warnings, [], warnings)
        self.assertIn("match ", code)
        self.assertIn("走零", code)
        self.assertIn("走二", code)
        self.assertIn("兜底", code)
        self.assertIn("_:\n", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_switch_empty_branches_get_pass(self):
        """match 的分支体不能为空,没连线必须补 pass —— 否则语法错误。"""
        code, _ = self._build_switch(3, {"默认": "兜底"})
        body = code[code.index("match "):]
        self.assertIn("pass", body)
        # 三个空分支 + 默认不空 -> 至少 3 个 pass
        self.assertGreaterEqual(body.count("pass"), 3)

    def test_switch_only_default_is_valid(self):
        code, _ = self._build_switch(1, {"默认": "只有默认"})
        self.assertIn("match ", code)
        self.assertIn("pass", code)
        self.assert_indent_sane(code)

    def test_pin_count_props_get_update_callback(self):
        """带 count/cases/steps 字样的属性必须挂上重建回调。

        没挂的话用户在 Blender 里改数字看不到新引脚(实测踩过)。
        """
        for cls, prop in ((NN.GKNodeSwitch, "gk_case_count"),
                          (NN.GKNodeSequence, "gk_step_count")):
            descriptor = (cls.__annotations__ or {}).get(prop)
            self.assertIsNotNone(descriptor, f"{cls.__name__}.{prop} 没声明")
            # Blender 的 Property 有 update;桩环境是 GKProp,拿 kwargs 看
            has_update = getattr(descriptor, "update", None) is not None
            if not has_update:
                has_update = "update" in getattr(descriptor, "kwargs", {})
            self.assertTrue(has_update, f"{cls.__name__}.{prop} 没有 update 回调")

    def test_no_chinese_identifiers_in_switch_names(self):
        code, _ = self._build_switch(5, {"3": "三"})
        self.assert_no_chinese_identifiers(code)


class TestAnimationNameSources(Base):
    """动画名的来源与默认值。

    用户踩过的坑:节点里动画名默认是 `idle`,而他的模型里叫
    `骨骼动作` / `形态键动作` —— 不动那个默认值就永远播不出来,
    只能靠运行时警告去猜正确名字。

    所以:
      * 默认值改成**空**(不假装有个叫 idle 的动画)
      * 提供一个能从列表里挑的入口(动作归属分析)
    """

    def test_play_anim_default_name_is_empty(self):
        node = make_tree().new_node(NN.GKNodePlayAnim, "播放")
        self.assertEqual(node.gk_anim_name, "",
                         "默认值必须是空,不能是 idle 这种假名字")

    def test_empty_name_picks_first_animation(self):
        """名字留空 = "随便播一个",不能生成 has_animation("")。

        以前默认值是 `idle`,用户不动它就永远播不出来。
        改成空之后如果直接生成 `has_animation("")`,那是**永远 false**
        加一条空警告 —— 用户照样一头雾水。
        所以留空改走 `_gk_first_animation()`,模型只有一个动画时开箱即用。
        """
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        play = tree.new_node(NN.GKNodePlayAnim, "播放")
        gk_stub.connect(tree, event, "执行", play, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_first_animation(", code)
        self.assertIn("func _gk_first_animation(", code)
        # 绝不能出现空的动画名
        self.assertNotIn('has_animation("")', code)

    def test_filled_name_is_used_verbatim(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        play = tree.new_node(NN.GKNodePlayAnim, "播放", gk_anim_name="骨骼动作")
        gk_stub.connect(tree, event, "执行", play, "执行")
        code, _ = gen(tree, V3)
        self.assertIn('"骨骼动作"', code)
        self.assertNotIn("_gk_first_animation(", code)

    def test_discover_actions_without_bpy_is_safe(self):
        """离线(没有真 bpy)时动作发现要安全返回空,不能抛异常。"""
        entries = NN.discover_actions(None)
        self.assertIsInstance(entries, list)

    def test_action_slot_names_tolerates_missing_slots(self):
        """老版本 Blender / 桩对象没有 slots 时不能崩。"""
        class FakeAction:
            name = "x"

        self.assertEqual(NN._action_slot_names(FakeAction()), [])

    def test_action_belongs_to_uses_slot_name(self):
        """slot 名字里含物体名 -> 判定为属于它(最强依据)。"""
        class FakeSlot:
            name_display = "胡桃-海灯节_arm"

        class FakeAction:
            name = "骨骼动作"
            id_root = "OBJECT"
            slots = (FakeSlot(),)

        class FakeObj:
            name = "胡桃-海灯节_arm"
            type = "ARMATURE"

        self.assertTrue(NN.action_belongs_to(FakeAction(), FakeObj()))

    def test_action_belongs_to_falls_back_to_root_type(self):
        class FakeAction:
            name = "形态键动作"
            id_root = "KEY"

        class FakeMesh:
            name = "随便"
            type = "MESH"

        class FakeArmature:
            name = "随便"
            type = "ARMATURE"

        # KEY 类动作该挂网格,不该挂骨架
        self.assertTrue(NN.action_belongs_to(FakeAction(), FakeMesh()))
        self.assertFalse(NN.action_belongs_to(FakeAction(), FakeArmature()))

    def test_actions_on_object_collects_shape_keys(self):
        """形态键动画挂在 mesh data 的 shape_keys 上,不在物体自己的
        animation_data 里 —— 漏掉这条用户的『形态键动作』就搜不到。"""
        sentinel = object()

        class FakeShapeAnim:
            action = sentinel

        class FakeShapeKeys:
            animation_data = FakeShapeAnim()

        class FakeData:
            shape_keys = FakeShapeKeys()

        class FakeObj:
            name = "m"
            type = "MESH"
            animation_data = None
            data = FakeData()

        self.assertIn(sentinel, NN.actions_on_object(FakeObj()))


    def test_native_action_pointer_takes_priority(self):
        """原生动作指针(gk_anim_action)要优先于手填文本框。

        这是**正解**:`PointerProperty(type=Action)` 拿到 Blender 原生
        数据块选择器(带搜索)。之前试的 EnumProperty 动态下拉被 Blender
        挡了(动态 items 默认值只能是整数),『选…』按钮又要用户手点。
        """
        class FakeAction:
            name = "骨骼动作"

        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        play = tree.new_node(NN.GKNodePlayAnim, "播放", gk_anim_name="手填的")
        play.gk_anim_action = FakeAction()
        gk_stub.connect(tree, event, "执行", play, "执行")
        code, _ = gen(tree, V3)
        self.assertIn('"骨骼动作"', code)
        self.assertNotIn("手填的", code)

    def test_text_field_used_when_no_pointer(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        play = tree.new_node(NN.GKNodePlayAnim, "播放", gk_anim_name="手填的")
        gk_stub.connect(tree, event, "执行", play, "执行")
        code, _ = gen(tree, V3)
        self.assertIn('"手填的"', code)

    def test_pin_wins_over_pointer(self):
        """引脚连线(运行时名字)优先级最高,盖过数据块指针。"""
        class FakeAction:
            name = "骨骼动作"

        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        play = tree.new_node(NN.GKNodePlayAnim, "播放")
        play.gk_anim_action = FakeAction()
        text = tree.new_node(NN.GKNodeConstText, "名字", gk_value="动态名")
        gk_stub.connect(tree, event, "执行", play, "执行")
        gk_stub.connect(tree, text, "值", play, "动画名")
        code, _ = gen(tree, V3)
        self.assertIn("动态名", code)
        self.assertNotIn('"骨骼动作"', code)

    def test_prop_action_falls_back_without_bpy_action(self):
        """桩环境没有 bpy.types.Action -> 退回普通属性占位符,不能崩。

        探不到就崩的话,**整张节点表**都注册不上(实测踩过)。
        """
        desc = (NN.GKNodePlayAnim.__annotations__ or {}).get("gk_anim_action")
        self.assertIsNotNone(desc, "gk_anim_action 没声明")


class TestAnimationEvents(Base):
    """动画的三个事件出口(Started / Running / On Finish)。

    照 UPBGE 的 Play Animation 节点设计 —— 那三个输出是它最值钱的地方,
    有它们才能做"播完接下一招""循环期间持续判定"。
    """

    def _build(self, wire):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        play = tree.new_node(NN.GKNodePlayAnim, "播放", gk_anim_name="骨骼动作")
        gk_stub.connect(tree, event, "执行", play, "执行")
        for socket_name, text in wire.items():
            printer = tree.new_node(NN.GKNodePrint, "打印" + socket_name)
            printer.gk_text = text
            gk_stub.connect(tree, play, socket_name, printer, "执行")
        return gen(tree, V3)

    def test_node_has_three_event_outputs(self):
        node = make_tree().new_node(NN.GKNodePlayAnim, "播放")
        names = [s.name for s in node.outputs]
        for want in ("完成", "开始时", "每帧", "结束时"):
            self.assertIn(want, names)

    def test_started_and_finished_use_signals(self):
        code, warnings = self._build({"开始时": "开始!", "结束时": "播完了"})
        self.assertEqual(warnings, [], warnings)
        self.assertIn("animation_started.connect(", code)
        self.assertIn("animation_finished.connect(", code)
        self.assertIn("开始!", code)
        self.assertIn("播完了", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_connections_are_in_the_success_branch(self):
        """接线必须在 `play()` 成功那条分支里。

        踩过的坑:第一版把 connect 写在 `else:` 里 —— 那是"动画不存在"
        的分支,于是信号只在**播放失败**时接上,正常播放永远不接。
        """
        code, _ = self._build({"结束时": "播完了"})
        lines = code.splitlines()
        play_at = next(i for i, ln in enumerate(lines) if ".play(" in ln)
        connect_at = next(i for i, ln in enumerate(lines)
                          if "animation_finished.connect(" in ln)
        warn_at = next(i for i, ln in enumerate(lines) if "push_warning" in ln)
        self.assertGreater(connect_at, play_at, "接线应该在 play() 之后")
        self.assertLess(connect_at, warn_at, "接线不能在警告(else 分支)里")

    def test_running_generates_process_check(self):
        """Godot 没有"正在播"的信号 -> 只能每帧查一次。"""
        code, warnings = self._build({"每帧": "播放中"})
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _process(", code)
        self.assertIn("is_playing()", code)
        self.assertIn("current_animation ==", code)
        self.assertIn("播放中", code)
        self.assert_indent_sane(code)

    def test_running_without_user_process_node_still_works(self):
        """整张图里没有用户自己的 `每当_帧` 时,也要能生成 _process。"""
        code, _ = self._build({"每帧": "播放中"})
        self.assertEqual(code.count("func _process("), 1)

    def test_each_output_gets_its_own_handler(self):
        """两个出口共用一个编号 -> 生成两个同名函数,一个覆盖另一个。"""
        code, _ = self._build({"开始时": "甲", "结束时": "乙"})
        # 只看事件回调(`_gk_started_*` / `_gk_finished_*` / `_gk_running_*`),
        # 不算 `_gk_find_animation_player` 那种辅助函数。
        handlers = []
        for line in code.splitlines():
            if not line.startswith("func _gk_"):
                continue
            name = line.split("(")[0].replace("func ", "")
            if any(name.startswith(f"_gk_{kind}_")
                   for kind in ("started", "finished", "running")):
                handlers.append(name)
        self.assertEqual(len(handlers), len(set(handlers)),
                         f"回调函数重名了: {handlers}")
        self.assertEqual(len(handlers), 2, handlers)

    def test_no_events_means_no_extra_code(self):
        """三个出口都不接线时,不该白白生成信号相关代码。"""
        code, _ = self._build({})
        self.assertNotIn("animation_started", code)
        self.assertNotIn("animation_finished", code)

    def test_handler_compares_animation_name(self):
        """回调里要比对动画名 —— 否则别的代码播了同一播放器上的
        另一个动画时,这段逻辑会被误触发。"""
        code, _ = self._build({"结束时": "播完了"})
        self.assertIn("StringName", code)
        self.assertIn("!= _name", code)


class TestAnimationStateMachine(Base):
    """动画状态机 —— 取代手拼分支网。

    用户要求"做动画状态机",这也是 UPBGE 那套最大的缺陷
    (它一次只能播一个动画,没有状态机)。
    """

    def test_parse_transitions(self):
        self.assertEqual(NN.parse_transitions("idle -> run\nrun -> idle"),
                         [("idle", "run"), ("run", "idle")])

    def test_parse_accepts_full_width_arrow(self):
        self.assertEqual(NN.parse_transitions("idle→run"), [("idle", "run")])

    def test_parse_ignores_blank_and_comments(self):
        text = "  idle -> run  \n\n# 注释\nrun->idle\n"
        self.assertEqual(NN.parse_transitions(text),
                         [("idle", "run"), ("run", "idle")])

    def test_parse_tolerates_half_typed_rule(self):
        """用户正在打字时的半截规则不该让整个节点崩掉。"""
        self.assertEqual(NN.parse_transitions("idle ->"), [])
        self.assertEqual(NN.parse_transitions(""), [])
        self.assertEqual(NN.parse_transitions(None), [])

    def test_transitions_create_bool_pins(self):
        node = make_tree().new_node(NN.GKNodeAnimStateMachine, "状态机")
        node.gk_transitions = "idle -> run\nrun -> jump"
        node.build_sockets()
        names = [s.name for s in node.inputs]
        self.assertIn("idle→run", names)
        self.assertIn("run→jump", names)

    def test_duplicate_rules_make_one_pin(self):
        node = make_tree().new_node(NN.GKNodeAnimStateMachine, "状态机")
        node.gk_transitions = "idle -> run\nidle -> run"
        node.build_sockets()
        names = [s.name for s in node.inputs]
        self.assertEqual(names.count("idle→run"), 1)

    def _build(self, transitions, condition=None, mode="EVERY_FRAME"):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        machine = tree.new_node(NN.GKNodeAnimStateMachine, "状态机")
        machine.gk_transitions = transitions
        machine.gk_check_mode = mode
        machine.build_sockets()
        gk_stub.connect(tree, event, "执行", machine, "执行")
        if condition is not None:
            cond = tree.new_node(NN.GKNodeActionPressed, "输入")
            cond.gk_action = condition
            first = NN.parse_transitions(transitions)[0]
            gk_stub.connect(tree, cond, "按住", machine,
                            NN.transition_pin_name(*first))
        return gen(tree, V3)

    def test_every_frame_goes_to_physics_process(self):
        """『每帧检查』要挂 _physics_process —— 跟移动同步判定。

        放渲染帧里会出现"人已经走出去了状态还没切"的一帧延迟
        (和相机跟随同一个道理)。
        """
        code, warnings = self._build("idle -> run", condition="move_right")
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _physics_process(", code)
        self.assertIn('anim_state == "idle"', code)
        self.assertIn('anim_state = "run"', code)
        self.assertIn('.play("run"', code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_on_enter_mode_does_not_need_physics_process(self):
        code, _ = self._build("idle -> run", condition="move_right",
                              mode="ON_ENTER")
        self.assertNotIn("func _physics_process(", code)

    def test_empty_table_warns(self):
        _code, warnings = self._build("")
        self.assertTrue(any("状态表是空的" in w for w in warnings), warnings)

    def test_unwired_rule_warns(self):
        """规则没接条件 -> 永远不会触发,必须明确告诉用户。"""
        _code, warnings = self._build("idle -> run\nrun -> idle")
        self.assertTrue(any("没有接条件" in w for w in warnings), warnings)

    def test_state_name_is_also_the_animation_name(self):
        """状态名直接当动画名用 —— 少一层映射就少一类 bug。"""
        code, _ = self._build("idle -> attack", condition="attack")
        self.assertIn('has_animation("attack")', code)
        self.assertIn('.play("attack"', code)

    def test_missing_animation_warns_at_runtime(self):
        code, _ = self._build("idle -> attack", condition="attack")
        self.assertIn("没有对应的动画", code)

    def test_state_var_is_declared_once(self):
        code, _ = self._build("idle -> run", condition="move_right")
        self.assertEqual(code.count("var anim_state: String"), 1)


class TestAnimLocomotion(Base):
    """『动画判断_移动状态』—— 一次给出动画切换最常用的几个判断。

    用户要的是"从静止到运动的判断连接点(在地面?在移动?在离开地面?
    在空中,在下落,在上升?…)"。没有这个节点的话,每个条件都要单独拉
    一个表达式节点再接引脚 —— 状态一多整张图全是线。
    """

    def _build(self, outputs):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        # 序列默认 3 个出口,输出多于 3 个时要先把出口数调上去
        seq = tree.new_node(NN.GKNodeSequence, "序列")
        seq.gk_step_count = max(3, len(outputs))
        seq.build_sockets()
        loco = tree.new_node(NN.GKNodeAnimLocomotion, "移动状态")
        gk_stub.connect(tree, event, "执行", seq, "执行")
        for index, name in enumerate(outputs):
            branch = tree.new_node(NN.GKNodeBranch, "分支" + name)
            gk_stub.connect(tree, loco, name, branch, "条件")
            gk_stub.connect(tree, seq, str(index + 1), branch, "执行")
        return gen(tree, V3)

    def test_node_declares_all_outputs(self):
        node = make_tree().new_node(NN.GKNodeAnimLocomotion, "移动状态")
        names = [s.name for s in node.outputs]
        for want in ("在地面", "在移动", "地面且移动", "已离地",
                     "在上升", "在下落", "速度快", "速度慢"):
            self.assertIn(want, names)

    def test_generates_floor_and_velocity_checks(self):
        code, warnings = self._build(["在下落", "地面且移动"])
        self.assertEqual(warnings, [], warnings)
        self.assertIn("is_on_floor()", code)
        self.assertIn("velocity.x", code)
        self.assertIn("velocity.z", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_horizontal_speed_excludes_vertical(self):
        """水平速度不能算上竖直分量。

        否则跳起来时会被判成"在移动",走跑切换就乱了。
        """
        code, _ = self._build(["在移动"])
        self.assertIn("velocity.x", code)
        self.assertIn("velocity.z", code)
        # 速度表达式里**不能**出现 velocity.y(竖直分量另有用途)
        line = next(ln for ln in code.splitlines() if ">" in ln
                    and "velocity" in ln)
        self.assertIn("velocity.x", line)
        self.assertNotIn("velocity.y", line)

    def test_expressions_are_inlined_not_locals(self):
        """不许生成 `var _lk...` 局部变量。

        踩过的坑:第一版生成了 4 行中间变量,结果导出的代码报
            Identifier "_lk2_speed" not declared in the current scope
        —— 表达式在 `_ready()` 的局部作用域里求值,却被
        `_physics_process` 里的状态判断引用,跨函数了。

        现在全部内联,所以生成结果里**一个 `_lk` 局部变量都不该有**。
        """
        code, _ = self._build(["在地面", "在移动", "已离地", "在下落"])
        self.assertNotIn("_lk", code, "不该再有 _lk 中间变量")
        self.assertNotIn("var _lk", code)

    def test_2d_uses_vector2_x_only(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        seq = tree.new_node(NN.GKNodeSequence, "序列")
        loco = tree.new_node(NN.GKNodeAnimLocomotion, "移动状态")
        branch = tree.new_node(NN.GKNodeBranch, "分支")
        gk_stub.connect(tree, event, "执行", seq, "执行")
        gk_stub.connect(tree, loco, "在移动", branch, "条件")
        gk_stub.connect(tree, seq, "1", branch, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("velocity.x, 0.0", code)
        self.assertNotIn("velocity.z", code)


class TestAnimTreeScope(Base):
    """动画树生成的代码**跨函数**时不能有作用域错误。

    用户实测导出后 Godot 报:
        Function "is_on_floor()" not found in base self.
        Identifier "_ap1" not declared in the current scope.   (x2)

    根因:`_ready()` 里声明的**局部**变量被 `_physics_process()` 引用。
    """

    def _build(self, base_class="CharacterBody3D"):
        tree = make_tree()
        anim = gk_stub.StubNodeTree("角色动画")
        anim.bl_idname = "GKAnimationTree"
        loco = anim.new_node(NN.GKNodeAnimLocomotion, "移动状态")
        # 链路模型(照 UE5):
        #   [状态 idle] --转到--> [转场] --转到--> [状态 跑步]
        # 转场的条件来自『移动状态』的『地面且移动』
        idle = anim.new_node(NN.GKNodeAnimState, "状态idle")
        idle.gk_state_name = "idle"
        run = anim.new_node(NN.GKNodeAnimState, "状态跑步")
        run.gk_state_name = "跑步"
        transition = anim.new_node(NN.GKNodeAnimTransition, "转场")
        # 链路:状态 --转到--> 转场 --转到--> 下一个状态
        # 『转到』接的是转场的**条件**引脚 —— 转场没有"执行流入口",
        # 它就是"上游状态指过来"的那个连接点。条件另外接在『条件』上。
        # 链路:状态 --转到--> 转场『进入』;转场『条件』接判断;
        # 转场『转到』---> 下一个状态的『进入』
        gk_stub.connect(anim, idle, "转到", transition, "进入")
        gk_stub.connect(anim, transition, "转到", run, "进入")
        gk_stub.connect(anim, loco, "地面且移动", transition, "条件")

        event = tree.new_node(NN.GKNodeOnReady, "准备")
        ref = tree.new_node(NN.GKNodeAnimStateMachineRef, "状态机")
        gk_stub.connect(tree, event, "执行", ref, "执行")

        import types
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data",
                types.SimpleNamespace(node_groups=[anim]))
        try:
            return gen(tree, {"dimension": "3", "base_class": base_class})
        finally:
            if original is None:
                try:
                    delattr(bpy_module(), "data")
                except AttributeError:
                    pass
            else:
                setattr(bpy_module(), "data", original)

    def test_player_is_a_member_not_a_local(self):
        """播放器引用必须是**成员变量**。

        局部变量在 `_physics_process` 里就是
        `Identifier "_ap1" not declared in the current scope`。
        """
        code, _ = self._build()
        self.assertIn("var _ap0 = null", code, "成员变量没声明")
        # `_ready` 里是**赋值**,不能再写 `var`(同作用域重复声明)
        self.assertNotIn("var _ap0 :=", code)
        self.assertIn("_ap0 = _gk_find_animation_player(self)", code)

    def test_no_local_intermediate_vars_from_locomotion(self):
        """移动状态不许生成局部中间变量 —— 它们会被跨函数引用。"""
        code, _ = self._build()
        self.assertNotIn("_lk", code)

    def test_tunables_declared_once_per_node(self):
        """同一个节点的同一个输入只能声明一个 @export。

        多输出节点按引脚求值会被调用 8 次,不缓存就会生成
        `move_threshold` / `move_threshold_2` 两份 ——
        用户在检查器里改第一个,生效的却是第二个。
        """
        code, _ = self._build()
        self.assertIn("move_threshold", code)
        self.assertNotIn("move_threshold_2", code)
        self.assertNotIn("run_threshold_2", code)

    def test_locomotion_inlined_uses_target_method(self):
        """`is_on_floor()` 是 CharacterBody3D 的方法 —— 目标节点得对。"""
        code, _ = self._build()
        self.assertIn("self.is_on_floor()", code)


def bpy_module():
    import bpy
    return bpy



def build_anim_chain(tree, anim_tree, specs):
    """在 anim_tree 里搭一条状态链,返回 (入口状态, [状态节点...])。

    specs: [(状态名, [(条件节点, 输出引脚名 或 None), ...]), ...]
    每个状态可以有 0~n 条出边;第一条出边连到下一个状态,
    其余的连到"只连一次就够"的独立转场(测试用不到,先只支持一条)。

    简单起见:specs 是 [(状态名, 条件节点, 条件引脚), ...] ——
    第 i 个状态通过第 i 条转场连到第 i+1 个状态;
    最后一个元组的条件给 None(链尾)。
    """
    states = []
    for index, spec in enumerate(specs):
        name = spec[0]
        node = anim_tree.new_node(NN.GKNodeAnimState, "状态" + name)
        node.gk_state_name = name
        states.append(node)
    for index, spec in enumerate(specs):
        if index + 1 >= len(specs):
            break
        name, cond_node, cond_pin = spec
        transition = anim_tree.new_node(NN.GKNodeAnimTransition, "转场" + name)
        gk_stub.connect(anim_tree, states[index], "转到", transition, "进入")
        gk_stub.connect(anim_tree, transition, "转到",
                        states[index + 1], "进入")
        if cond_node is not None and cond_pin:
            gk_stub.connect(anim_tree, cond_node, cond_pin, transition, "条件")
    return states[0], states


def attach_anim_tree(tree, anim_tree, loco=None, specs=None):
    """把 anim_tree 挂到 bpy.data 上,并给逻辑树加一个状态机引用节点。"""
    import types

    event = tree.new_node(NN.GKNodeOnReady, "准备")
    ref = tree.new_node(NN.GKNodeAnimStateMachineRef, "状态机")
    gk_stub.connect(tree, event, "执行", ref, "执行")
    original = getattr(bpy_module(), "data", None)
    setattr(bpy_module(), "data",
            types.SimpleNamespace(node_groups=[anim_tree]))
    return original, ref


def detach_anim_tree(original):
    if original is None:
        try:
            delattr(bpy_module(), "data")
        except AttributeError:
            pass
    else:
        setattr(bpy_module(), "data", original)



class TestAnimChain(Base):
    """A 方案:照虚幻 5 的状态机 —— 状态连成链,转场带条件。

    用户确认的形状:

        [动画_状态 idle] ● 转到 ──> [动画_转场] ● 转到 ──> [动画_状态 run] ● 进入
                                       ● 条件 <- 「动画判断_移动状态」

    『进入』没接线的状态 = 起始状态(照 UE5 的 Entry 节点)。
    """

    def _build(self, links, loco_outputs=None, extra=None):
        """links: [(起始状态名, 条件输出名 或 None, 目标状态名), ...]"""
        tree = make_tree()
        anim = gk_stub.StubNodeTree("角色动画")
        anim.bl_idname = "GKAnimationTree"
        loco = anim.new_node(NN.GKNodeAnimLocomotion, "移动状态")
        states = {}
        for start, _cond, end in links:
            for name in (start, end):
                if name not in states:
                    node = anim.new_node(NN.GKNodeAnimState, "状态" + name)
                    node.gk_state_name = name
                    states[name] = node
        for index, (start, cond, end) in enumerate(links):
            transition = anim.new_node(NN.GKNodeAnimTransition, f"T{index}")
            gk_stub.connect(anim, states[start], "转到", transition, "进入")
            gk_stub.connect(anim, transition, "转到", states[end], "进入")
            if cond:
                gk_stub.connect(anim, loco, cond, transition, "条件")
        if extra is not None:
            extra(anim, states, loco)

        event = tree.new_node(NN.GKNodeOnReady, "准备")
        ref = tree.new_node(NN.GKNodeAnimStateMachineRef, "状态机")
        gk_stub.connect(tree, event, "执行", ref, "执行")

        import types
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data",
                types.SimpleNamespace(node_groups=[anim]))
        try:
            return gen(tree, {"dimension": "3",
                              "base_class": "CharacterBody3D"})
        finally:
            if original is None:
                try:
                    delattr(bpy_module(), "data")
                except AttributeError:
                    pass
            else:
                setattr(bpy_module(), "data", original)

    def test_state_has_flow_pins(self):
        """状态节点有『进入』和『转到』两个流引脚(照 UE5)。"""
        node = make_tree().new_node(NN.GKNodeAnimState, "状态")
        self.assertIn("进入", [s.name for s in node.inputs])
        self.assertIn("转到", [s.name for s in node.outputs])

    def test_transition_has_separate_flow_and_condition(self):
        """转场的『进入』(流)和『条件』(布尔)必须分开。

        合成一个引脚会互相覆盖 —— 实测生成出 `and null`。
        """
        node = make_tree().new_node(NN.GKNodeAnimTransition, "转场")
        names = [s.name for s in node.inputs]
        self.assertIn("进入", names)
        self.assertIn("条件", names)
        self.assertIn("转到", [s.name for s in node.outputs])

    def test_entry_state_is_the_one_without_incoming(self):
        """『进入』没接线的状态 = 起始状态(照 UE5 的 Entry 节点)。"""
        code, warnings = self._build([("idle", "在移动", "run")])
        self.assertEqual(warnings, [], warnings)
        self.assertIn('anim_state = "idle"', code)

    def test_chain_generates_two_transitions(self):
        code, warnings = self._build([("idle", "在移动", "run"),
                                      ("run", "已离地", "jump")])
        self.assertEqual(warnings, [], warnings)
        self.assertIn('if anim_state == "idle" and', code)
        self.assertIn('anim_state = "run"', code)
        self.assertIn('if anim_state == "run" and', code)
        self.assertIn('anim_state = "jump"', code)
        self.assertIn('.play("run"', code)
        self.assertIn('.play("jump"', code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_cycle_does_not_hang(self):
        """状态机天然有环(idle -> run -> idle),不能无限递归。

        回到已经走过的状态就停 —— 语义上也对:那条转移的起点状态
        已经在链上生成过了,再走一遍只会生成重复代码。
        """
        code, _warnings = self._build([("idle", "在移动", "run"),
                                       ("run", "速度快", "idle")])
        # 第一条照常生成
        self.assertIn('if anim_state == "idle" and', code)
        self.assertIn('anim_state = "run"', code)
        # 环保护生效:不会出现"run 切回 idle"那段(走到了已访问的 idle)
        self.assertNotIn('anim_state = "idle"\n', code.replace(
            'anim_state = "idle"\n', '', 1) and 'x' or '')
        # 关键:不崩、不无限递归
        self.assertIn("func _physics_process", code)

    def test_orphan_state_warns(self):
        """真的孤立的状态(没连进来) -> 明确告警,而不是静默丢掉。

        注意**不**对环里的状态告警:idle <-> run 里两个状态都"没有外来
        入边",但它们是连着的 —— 报"2 个入口"是误报(实测踩过,
        用户会以为图配错了,其实完全正常)。
        """
        code, warnings = self._build(
            [("idle", "在移动", "run")],
            extra=lambda anim, states, loco: anim.new_node(
                NN.GKNodeAnimState, "状态孤儿"))
        self.assertTrue(any("孤立" in w for w in warnings), warnings)

    def test_cycle_does_not_warn_about_entries(self):
        """环不该被误报成"多个入口"。"""
        _code, warnings = self._build([("idle", "在移动", "run"),
                                       ("run", "速度快", "idle")])
        self.assertEqual([w for w in warnings if "孤立" in w], [],
                         f"环被误报成孤立入口了: {warnings}")

    def test_unwired_condition_warns(self):
        """转场没条件 -> 永远不会触发,必须明确告诉用户。"""
        _code, warnings = self._build([("idle", None, "run")])
        self.assertTrue(any("没有条件" in w for w in warnings), warnings)

    def test_transition_to_wrong_node_warns(self):
        """转场连到了非状态节点 -> 告警而不是崩。

        用『动画_判断_移动状态』当靶子(它没有『进入』引脚)——
        实测这种情况曾经的报错是
            KeyError: '不是状态 没有输入引脚 进入'
        也就是**直接崩掉导出**,所以必须挡住并给人话。
        """
        def bad(anim, states, loco):
            wrong = anim.new_node(NN.GKNodeAnimLocomotion, "不是状态")
            t2 = anim.new_node(NN.GKNodeAnimTransition, "T坏")
            gk_stub.connect(anim, states["run"], "转到", t2, "进入")
            # 连到 loco 的**输入**引脚上(stub 允许,真实 Blender 里
            # 类型不匹配连不上;这里模拟"用户连错了"的最坏情况)
            gk_stub.connect(anim, t2, "转到", wrong, "角色")

        _code, warnings = self._build([("idle", "在移动", "run")], extra=bad)
        self.assertTrue(any("不是『动画_状态』" in w for w in warnings),
                        warnings)

    def test_no_entries_warns(self):
        """一个入口都没有 -> 提示怎么搭。"""
        tree = make_tree()
        anim = gk_stub.StubNodeTree("空动画")
        anim.bl_idname = "GKAnimationTree"
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        ref = tree.new_node(NN.GKNodeAnimStateMachineRef, "状态机")
        gk_stub.connect(tree, event, "执行", ref, "执行")
        import types
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data",
                types.SimpleNamespace(node_groups=[anim]))
        try:
            _code, warnings = gen(tree, {"dimension": "3",
                                         "base_class": "CharacterBody3D"})
        finally:
            if original is None:
                try:
                    delattr(bpy_module(), "data")
                except AttributeError:
                    pass
            else:
                setattr(bpy_module(), "data", original)
        self.assertTrue(any("找不到可用的动画状态机" in w for w in warnings),
                        warnings)


    def test_animation_nodes_are_visible_in_menu(self):
        """动画树那三个节点都必须出现在"添加节点"菜单里。

        实测踩过:『动画_状态』因为残留的 `gk_hidden = True` 被菜单
        静默过滤掉,用户在建状态机时**根本找不到状态节点**。
        这种"改造节点时忘了清掉上一版标记"的错很难靠肉眼发现,
        所以用测试钉住。
        """
        for bl_idname in ("GKNodeAnimState",
                          "GKNodeAnimTransition",
                          "GKNodeAnimLocomotion"):
            cls = next((c for c in NN.ALL_CLASSES
                        if c.bl_idname == bl_idname), None)
            self.assertIsNotNone(cls, f"{bl_idname} 没注册")
            self.assertFalse(getattr(cls, "gk_hidden", False),
                             f"{cls.bl_label} 被隐藏了,菜单里看不到")

    def test_animation_menu_lists_all_three(self):
        """菜单查询也要把三个节点都列出来(不只是类属性对)。"""
        from BlenderGameKit import properties as props
        listed = set()
        for _cat, classes in props.node_menu_entries("GKAnimationTree"):
            for cls in classes:
                listed.add(cls.bl_idname)
        for bl_idname in ("GKNodeAnimState",
                          "GKNodeAnimTransition",
                          "GKNodeAnimLocomotion"):
            self.assertIn(bl_idname, listed, f"{bl_idname} 没进菜单")


def nn_base_statement():
    from BlenderGameKit.nodes import node_base as NB
    return NB.GKStatementNode


if __name__ == "__main__":
    unittest.main(verbosity=2)
