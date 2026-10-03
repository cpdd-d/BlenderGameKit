"""BlenderGameKit 代码生成回归测试(不需要 Blender)。

跑法:
    python tests/test_codegen.py
或者交给 Blender 的 Python 跑(见 run_tests.bat)，效果一样。
"""

import os
import re
import sys
import types
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
        """生成代码里的标识符必须是 Godot 认的写法(中文**可以**)。

        ── 这个断言的立场变过一次(第 22 轮)──────────────────────────
        以前是"禁止中文标识符",理由是"中文会让 GDScript 编译失败" ——
        那个理由是**错的**:实测 Godot 4.7.2 接受 Unicode 标识符
        (`var 金币数: int = 0` 能过 `--check-only`)。
        真正的问题在**命名**那一步:`snake_case` 把中文压成下划线之后,
        『金币数』和『通关』会**撞成同一个名字**,生成
        `var v: float = false` —— 那才是编译失败的根源。

        所以现在改成:中文允许,但**标识符本身必须合法**
        (字母/下划线/数字/Unicode 字母,不能有空格、横线、点,
        不能以数字开头)。真正的语法问题交给真 Godot 那一层抓。
        """
        bad = []
        pattern = re.compile(r"\b(?:var|const)\s+([^\s:=]+)")
        for number, line in enumerate(code.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            body = re.sub(r'"(?:[^"\\]|\\.)*"', '""',
                          line.split("#", 1)[0])
            for name in pattern.findall(body):
                if not name or name[0].isdigit():
                    bad.append((number, name))
                    continue
                if not all(ch.isalnum() or ch == "_" for ch in name):
                    bad.append((number, name))
        self.assertEqual(bad, [], f"标识符不合法: {bad}")

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
        # 变量名跟着节点标签走(『一次_一次』-> `done_一次`),所以只断言
        # "有一个 bool 变量 + 用 not 判断过",不写死名字
        self.assertRegex(code, r"if not done\w*:")
        self.assertRegex(code, r"var done\w*: bool")


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
    def test_node_reference_has_fallback_lookup(self):
        """节点引用不能写死 `$"名字"` —— 要带兜底查找。

        用户真跑报告:脚本挂在『碰撞代理』物体上,而相机是它的**兄弟节点**,
        `$"侧视机位"` 报 `Node not found` -> 『相机_切换』整段失效。
        现在生成的是 `_gk_ref(self, "名字")`:先按路径找,再全树按名字找。
        """
        for name, script in (
                ("GKNodeCameraSetCurrent", "相机_切换"),
                ("GKNodeSetVelocity", "移动_设置速度")):
            tree = make_tree()
            event = tree.new_node(NN.GKNodeOnReady, "准备")
            node = tree.new_node(getattr(NN, name), script,
                                 gk_target="某物")
            printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="x")
            gk_stub.connect(tree, event, "执行", printer, "执行")
            gk_stub.connect(tree, event, "执行", node, "执行")
            code, _w = gen(tree, V3)
            self.assertIn('_gk_ref(self, "某物")', code)
            self.assertNotIn('$"某物"', code)
            self.assertIn("func _gk_ref(host: Node, wanted: String)", code)

    def test_other_node_becomes_onready(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        velocity = tree.new_node(NN.GKNodeSetVelocity, "设别的节点", gk_target="Player")
        gk_stub.connect(tree, event, "执行", velocity, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("@onready var", code)
        self.assertIn('_gk_ref(self, "Player")', code)

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
        # (`GKNodeAnimTransition` 同 `GKNodeAnimState`:靠条件为真被激活;
        #  `GKNodeAnimEntry` 是**开始口** —— 它只有条件,本身就是源头)
        no_flow = {"GKNodeAnimState", "GKNodeAnimTransition",
                   "GKNodeAnimEntry"}
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
        """没填相机时不该崩，而是自动找第一个相机并**提示**(不算告警)。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeCameraSetCurrent, "切镜头")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        # 这是"正常默认用法",归到提示里 —— 否则"零告警导出"这条验收线
        # 会被一堆正常用法刷屏,真正的告警反而淹掉
        notes = list(getattr(codegen, "last_notes", []) or [])
        self.assertTrue(any("没指定相机" in n for n in notes), notes)
        # 顺序:视口里正在用的 -> 分组 -> 场景里第一个(2.5D 示例抓出来的:
        # 只找分组的话,不在分组里的相机会让整段逻辑被跳过)
        self.assertLess(code.index("get_viewport().get_camera_3d()"),
                        code.index('gk_camera"'))
        self.assertFalse([w for w in warnings if "相机" in w], warnings)
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
        # 现在是 (动作名, 捕获到的键) 的形式 —— 键来自『输入_按键』的
        # "按一下键"捕获,导出时要真的写进 project.godot
        names = [item[0] if isinstance(item, (tuple, list)) else item
                 for item in actions]
        self.assertIn("move_left", names)
        self.assertIn("move_right", names)

    def test_set_var_multiple_slots_with_links(self):
        """一张『变量_设置』卡片设两个变量,两个值引脚各接不同节点。

        用户 2026-10-03:"在设置变量属性时,卡片里可以设置多个并且引脚
        可连接不同节点,如虚幻蓝图的变量节点可重复连接"。

        ── 为什么专门盯第 2 个槽 ────────────────────────────────────
        第一版把槽位号和引脚名算错了(第 2 槽去找不存在的『值 3』),
        于是它**永远读不到连线**,悄悄退回数字框里的值 ——
        示例工程里 `combo_ready = 1.0` 就是这么来的(应该是 `true`)。
        """
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        setter = tree.new_node(NN.GKNodeSetVar, "设两个")
        setter.gk_var_count = 2
        setter.gk_var_name = "is_attacking"
        setter.gk_var_name_2 = "combo_ready"
        setter.gk_value_2 = 99.0
        setter.build_sockets()
        const_a = tree.new_node(NN.GKNodeConstBool, "假", gk_value=False)
        const_b = tree.new_node(NN.GKNodeConstBool, "真", gk_value=True)
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="x")
        gk_stub.connect(tree, event, "执行", setter, "执行")
        gk_stub.connect(tree, setter, "完成", printer, "执行")
        gk_stub.connect(tree, const_a, "值", setter, "值")
        gk_stub.connect(tree, const_b, "值", setter, "值 2")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("is_attacking = false", code)
        # 第 2 个槽必须用**线那头**的值(true),不是数字框里的 99
        self.assertIn("combo_ready = true", code)
        self.assertNotIn("combo_ready = 99", code)
        # 布尔变量不该被数字框带成 float
        self.assertIn("var combo_ready: bool", code)

    def test_bool_var_given_a_number_becomes_true_false(self):
        """布尔变量被填了数字:生成能跑的 true/false(并告警提醒接常量_布尔)。

        数字框只能填数字,所以"变量是 bool 却填了 0"非常常见。
        `is_hurt = 0.0` 在 GDScript 里是类型不匹配(bool 变量收 float),
        **整个脚本加载失败** —— 而用户只是"填了个 1"。
        """
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        first = tree.new_node(NN.GKNodeSetVar, "先声明成布尔")
        first.gk_var_name = "is_hurt"
        const_true = tree.new_node(NN.GKNodeConstBool, "真", gk_value=True)
        second = tree.new_node(NN.GKNodeSetVar, "再用数字设一次")
        second.gk_var_name = "is_hurt"
        second.gk_value = 1.0
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="x")
        gk_stub.connect(tree, event, "执行", first, "执行")
        gk_stub.connect(tree, const_true, "值", first, "值")
        gk_stub.connect(tree, first, "完成", second, "执行")
        gk_stub.connect(tree, second, "完成", printer, "执行")
        code, warnings = gen(tree, V3)
        self.assertIn("var is_hurt: bool", code)
        self.assertIn("is_hurt = true", code)
        self.assertNotIn("is_hurt = 1.0", code)
        # 告警要说人话:告诉用户接『常量_布尔』,而不是让他去改变量名
        self.assertTrue(any("常量_布尔" in w for w in warnings), warnings)
        self.assert_no_chinese_identifiers(code)

    def test_set_var_flow_fans_out_to_several_branches(self):
        """『变量_设置』的『完成』可以扇出到**多个**判断节点。

        用户 2026-10-03:"在判断属性时,可由设置变量节点拉出连接,
        如虚幻蓝图设置变量后就直接连接判断节点"。

        蓝图的执行线天生可以一拖多 —— 我们把『完成』接两个『如果_否则』,
        两个都要生成,而不是"后接的顶掉先接的"。
        """
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        setter = tree.new_node(NN.GKNodeSetVar, "设标志")
        setter.gk_var_name = "is_ready"
        const_true = tree.new_node(NN.GKNodeConstBool, "真", gk_value=True)
        branch_a = tree.new_node(NN.GKNodeBranch, "判断A")
        branch_b = tree.new_node(NN.GKNodeBranch, "判断B")
        status = tree.new_node(NN.GKNodeAnimStatus, "动画状态")
        print_a = tree.new_node(NN.GKNodePrint, "打印A", gk_text="A")
        print_b = tree.new_node(NN.GKNodePrint, "打印B", gk_text="B")
        gk_stub.connect(tree, event, "执行", setter, "执行")
        gk_stub.connect(tree, const_true, "值", setter, "值")
        # 扇出:一个『完成』接两个判断节点
        gk_stub.connect(tree, setter, "完成", branch_a, "执行")
        gk_stub.connect(tree, setter, "完成", branch_b, "执行")
        gk_stub.connect(tree, status, "已结束", branch_a, "条件")
        gk_stub.connect(tree, status, "在跑", branch_b, "条件")
        gk_stub.connect(tree, branch_a, "是", print_a, "执行")
        gk_stub.connect(tree, branch_b, "是", print_b, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("is_ready = true", code)
        self.assertIn('print("A")', code)
        self.assertIn('print("B")', code)
        self.assert_no_chinese_identifiers(code)

    def test_captured_key_is_recorded_for_export(self):
        """用户在『输入_按键』里按下的键要跟到导出那一端。

        否则"在 Blender 里按一下 W"这个操作对 Godot 毫无影响 ——
        按键仍然绑在默认表上(或者根本没绑),用户会以为功能坏了。
        """
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        key = tree.new_node(NN.GKNodeKey, "冲刺", gk_key="LEFT_SHIFT",
                            gk_action="dash")
        branch = tree.new_node(NN.GKNodeBranch, "如果")
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="dash")
        gk_stub.connect(tree, event, "执行", branch, "执行")
        gk_stub.connect(tree, key, "结果", branch, "条件")
        gk_stub.connect(tree, branch, "是", printer, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('const ACTION_DASH := "dash"', code)
        self.assertIn("Input.is_action_pressed(ACTION_DASH)", code)
        actions = dict(
            (item[0], item[1]) if isinstance(item, (tuple, list))
            else (item, "") for item in codegen.last_used_actions)
        self.assertEqual(actions.get("dash"), "LEFT_SHIFT")

    def test_key_trigger_modes(self):
        """一个『输入_按键』顶三个:按住 / 刚按下 / 松开(照 UPBGE 的 Tap)。"""
        for trigger, func in (("PRESSED", "is_action_pressed"),
                              ("JUST", "is_action_just_pressed"),
                              ("RELEASED", "is_action_just_released")):
            tree = make_tree()
            event = tree.new_node(NN.GKNodeOnReady, "准备")
            node = tree.new_node(NN.GKNodeKey, "键", gk_key="W",
                                 gk_action="dash", gk_trigger=trigger)
            printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="x")
            gk_stub.connect(tree, node, "结果", printer, "内容")
            gk_stub.connect(tree, event, "执行", printer, "执行")
            code, _w = gen(tree, V3)
            self.assertIn(f"Input.{func}(ACTION_DASH)", code)

    def test_mouse_nodes(self):
        """鼠标节点(UPBGE 的『输入 ▸ 鼠标』那一栏,以前完全没有)。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        branch = tree.new_node(NN.GKNodeBranch, "如果")
        down = tree.new_node(NN.GKNodeMouseButtonDown, "左键按下",
                             gk_button="LEFT")
        mode = tree.new_node(NN.GKNodeMouseMode, "锁鼠标", gk_mode="CAPTURED")
        gk_stub.connect(tree, event, "执行", branch, "执行")
        gk_stub.connect(tree, down, "刚按下", branch, "条件")
        gk_stub.connect(tree, branch, "是", mode, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("Input.is_mouse_button_pressed(MOUSE_BUTTON_LEFT)", code)
        self.assertIn("Input.mouse_mode = Input.MOUSE_MODE_CAPTURED", code)
        self.assert_no_chinese_identifiers(code)

    def test_mouse_wheel_uses_input_event(self):
        """滚轮没有"当前值"这种属性,只能靠事件累积(并每帧清零)。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        printer = tree.new_node(NN.GKNodePrint, "打印")
        wheel = tree.new_node(NN.GKNodeMouseWheel, "滚轮")
        gk_stub.connect(tree, wheel, "滚轮", printer, "内容")
        gk_stub.connect(tree, event, "执行", printer, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("MOUSE_BUTTON_WHEEL_UP", code)
        self.assertIn("func _input(event: InputEvent)", code)

    def test_menu_has_input_subcategories(self):
        """右键菜单里『输入』要分成 键盘 / 鼠标 / 手柄 / 触屏(照 UPBGE)。"""
        from BlenderGameKit import properties as props
        groups = dict()
        for category, subs in props.node_menu_tree(None):
            groups[category] = subs
        self.assertIn("输入", groups, "没有『输入』这个分类")
        subs = groups["输入"] or []
        names = [name for name, _classes in subs]
        for want in ("键盘", "鼠标", "手柄", "触屏"):
            self.assertIn(want, names, f"『输入』下面缺少『{want}』")
        mouse = dict(subs).get("鼠标", [])
        labels = [cls.bl_label for cls in mouse]
        self.assertTrue(any("鼠标" in label for label in labels), labels)
        # 手柄 / 触屏的节点也收进了『输入』(照 UPBGE 的 输入 ▸ … ▸ 游戏手柄)
        joy = dict(subs).get("手柄", [])
        self.assertTrue(joy, "『输入 ▸ 手柄』是空的")
        # 没有『输入』父分类之外的同名顶层分类(避免两处都能找到、看着重复)
        self.assertNotIn("手柄", [name for name in groups if name != "输入"])

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
        gk_stub.connect(anim, idle, "转到", transition, "上游")
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



class TestAnimTreeAdvanced(Base):
    """这一轮新增的:播放完成 / 状态的进入时·离开时 / 一个状态播多段动画。"""

    def _build(self, anim, extra_nodes=()):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        ref = tree.new_node(NN.GKNodeAnimStateMachineRef, "状态机")
        gk_stub.connect(tree, event, "执行", ref, "执行")
        for node, props in extra_nodes:
            for key, value in props.items():
                setattr(node, key, value)
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[anim]))
        try:
            return gen(tree, V3)
        finally:
            detach_anim_tree(original)

    def test_state_entry_and_exit_hooks_run(self):
        """状态的『进入时』/『离开时』执行流 -> 『动画_设变量』真的被执行。

        用户 2026-10-03 要的正是这个:他要把状态节点的『转到』接到
        『动画_设变量』上("上一个播完了就设个布尔变量"),而『转到』是
        "去下一个状态"的意思 —— 那条线只会报错。现在状态上多了两个
        **做事的**出口:『进入时』『离开时』。
        """
        anim = gk_stub.StubNodeTree("角色动画")
        anim.bl_idname = "GKAnimationTree"
        attack = anim.new_node(NN.GKNodeAnimState, "状态攻击")
        attack.gk_state_name = "attack"
        idle = anim.new_node(NN.GKNodeAnimState, "状态待机")
        idle.gk_state_name = "idle"
        trans = anim.new_node(NN.GKNodeAnimTransition, "T")
        gk_stub.connect(anim, attack, "转到", trans, "上游")
        gk_stub.connect(anim, trans, "转到", idle, "进入")
        trans.gk_cond_source = "FINISH"

        setter = anim.new_node(NN.GKNodeAnimSetVar, "设标志",
                               gk_var_name="is_attacking")
        const = anim.new_node(NN.GKNodeConstBool, "假", gk_value=False)
        gk_stub.connect(anim, attack, "离开时", setter, "执行")
        gk_stub.connect(anim, const, "值", setter, "值")
        anim.gk_variables = [types.SimpleNamespace(var_name="is_attacking",
                                                  var_type="bool",
                                                  default_value="0")]

        code, warnings = self._build(anim)
        self.assertEqual(warnings, [], warnings)
        # 离开时的函数生成了,而且里面真的有赋值
        self.assertIn("func _gk_anim_exit_", code)
        self.assertIn("is_attacking = false", code)
        # 进新状态时会按状态名分派(先跑离开时)
        self.assertIn("match anim_state:", code)
        self.assertIn('"attack":', code)

    def test_dead_set_var_still_warns(self):
        """没接在进入时/离开时上的『动画_设变量』仍然要告警。"""
        anim = gk_stub.StubNodeTree("角色动画")
        anim.bl_idname = "GKAnimationTree"
        idle = anim.new_node(NN.GKNodeAnimState, "状态待机")
        idle.gk_state_name = "idle"
        setter = anim.new_node(NN.GKNodeAnimSetVar, "孤儿设变量",
                               gk_var_name="flag")
        graph = NN.anim_tree_graph(anim)
        self.assertTrue(any("不会被执行" in p for p in graph["problems"]),
                        graph["problems"])
        # 接到『进入时』之后就不该再报
        gk_stub.connect(anim, idle, "进入时", setter, "执行")
        graph = NN.anim_tree_graph(anim)
        self.assertEqual([p for p in graph["problems"] if "不会被执行" in p], [])

    def test_state_plays_multiple_animations(self):
        """一个状态同时播多段动画 —— 骨骼动作 + 形态键动作。

        用户的 MMD 模型就是这样:两段动画分开,要看完整动作必须同时播。
        Godot 的 AnimationPlayer 一次只能播一段(play() 会顶掉上一段),
        所以额外的几段走"层播放器"(复制出来的第二个播放器)。
        """

        class FakeAction:
            def __init__(self, name):
                self.name = name

        anim = gk_stub.StubNodeTree("角色动画")
        anim.bl_idname = "GKAnimationTree"
        idle = anim.new_node(NN.GKNodeAnimState, "状态待机")
        idle.gk_state_name = "idle"
        idle.gk_anim_action_2 = FakeAction("形态键动作")
        code, warnings = self._build(anim)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _gk_anim_enter_idle", code)
        self.assertIn("_gk_layer_player(", code)
        self.assertIn('_gk_layer_player(_ap0, 1)', code)
        self.assertIn('"形态键动作"', code)
        # 切走的时候要把层播放器停掉,否则多播的那段会一直播下去
        self.assertIn(".stop()", code)

    def test_anim_finished_node_in_logic_tree(self):
        """『动画_播放完成』能在**玩法图**里用,并且会补上信号连接。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        finished = tree.new_node(NN.GKNodeAnimFinished, "播完了",
                                 gk_anim_name="攻击")
        branch = tree.new_node(NN.GKNodeBranch, "如果")
        setter = tree.new_node(NN.GKNodeSetVar, "清标志",
                               gk_var_name="is_attacking")
        const = tree.new_node(NN.GKNodeConstBool, "假", gk_value=False)
        gk_stub.connect(tree, event, "执行", branch, "执行")
        gk_stub.connect(tree, finished, "完成", branch, "条件")
        gk_stub.connect(tree, branch, "是", setter, "执行")
        gk_stub.connect(tree, const, "值", setter, "值")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('_gk_anim_finished == "攻击"', code)
        # 玩法图里没人在 _ready 里连过信号 -> 生成器要自己补一个
        self.assertIn("animation_finished.connect(_gk_on_anim_finished)", code)
        self.assertIn("animation_started.connect(_gk_on_anim_started)", code)
        self.assertIn("func _ready()", code)
        self.assert_no_chinese_identifiers(code)

    def test_finished_flag_is_sticky_until_next_play(self):
        """标志"粘住"到下一段动画开播 —— 否则复合条件永远等不到。

        如果标志只活一帧,"动画播完了**并且**在地面上"这种条件在玩家
        还没落地时就永远错过了。
        清理走 `animation_started` 信号:所有开播路径都覆盖得到。
        """
        anim = gk_stub.StubNodeTree("角色动画")
        anim.bl_idname = "GKAnimationTree"
        attack = anim.new_node(NN.GKNodeAnimState, "状态攻击")
        attack.gk_state_name = "attack"
        idle = anim.new_node(NN.GKNodeAnimState, "状态待机")
        idle.gk_state_name = "idle"
        trans = anim.new_node(NN.GKNodeAnimTransition, "T")
        gk_stub.connect(anim, attack, "转到", trans, "上游")
        gk_stub.connect(anim, trans, "转到", idle, "进入")
        trans.gk_cond_source = "FINISH"          # 用上"播完"这套机制
        code, warnings = self._build(anim)
        self.assertEqual(warnings, [], warnings)
        # 进新状态时清一次 + 有新动画开播时清一次
        self.assertIn('_gk_anim_finished = ""', code)
        self.assertIn("func _gk_on_anim_started(", code)
        self.assertIn("animation_started.connect(", code)
        # 连接要落在 _ready 里,不能每帧查一遍
        ready = code.split("func _ready", 1)[1].split("\nfunc ", 1)[0]
        self.assertIn("animation_finished.connect(", ready)


class TestAnimStartExit(Base):
    """『动画_开始』『动画_结束』两个口 + 布尔状态输出(用户 2026-10-03)。

    用户的话:"游戏动画节点图中应有一个开始口和结束口,明确从哪里开始动画,
    哪里结束动画,然后可以输出这一个游戏动画的布尔情况,以便于在游戏逻辑里
    判断执行下一步逻辑";"游戏动画开始时的条件可自定义"。
    """

    def _tree(self, **entry_props):
        import types

        anim = gk_stub.StubNodeTree("角色动画")
        anim.bl_idname = "GKAnimationTree"
        start = anim.new_node(NN.GKNodeAnimEntry, "开始", **entry_props)
        exit_node = anim.new_node(NN.GKNodeAnimExit, "结束")
        playing = anim.new_node(NN.GKNodeAnimState, "状态攻击")
        playing.gk_state_name = "attack"
        idle = anim.new_node(NN.GKNodeAnimState, "状态待机")
        idle.gk_state_name = "idle"
        gk_stub.connect(anim, start, "开始", idle, "进入")
        # 攻击是起始状态的一个出线;攻击打完 -> 结束口
        trans = anim.new_node(NN.GKNodeAnimTransition, "T")
        gk_stub.connect(anim, idle, "转到", trans, "上游")
        gk_stub.connect(anim, trans, "转到", playing, "进入 2")
        trans.gk_cond_source = "ACTION_JUST"
        trans.gk_cond_action = "attack"
        gk_stub.connect(anim, playing, "转到", exit_node, "进入")

        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        ref = tree.new_node(NN.GKNodeAnimStateMachineRef, "状态机")
        gk_stub.connect(tree, event, "执行", ref, "执行")

        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[anim]))
        try:
            code, warnings = gen(tree, V3)
        finally:
            detach_anim_tree(original)
        return code, warnings, anim

    def test_explicit_entry_wins_over_topology(self):
        """有开始口时,起始状态 = 开始口连的那个(不用再靠拓扑猜)。"""
        code, warnings, anim = self._tree(gk_start_mode="NOW")
        self.assertEqual(warnings, [], warnings)
        graph = NN.anim_tree_graph(anim)
        self.assertEqual(NN.anim_state_name(graph["entry"]), "idle")
        # 开始口存在 -> 生成"条件成立才开演"的闸门 + 结束处理
        self.assertIn("_gk_anim_started", code)
        self.assertIn("_gk_anim_done", code)
        self.assertIn("func _gk_anim_finish()", code)
        self.assertIn('_gk_anim_enter("idle")', code)
        # 转场只在"在跑"的时候判
        self.assertIn("if _gk_anim_started:", code)
        self.assert_no_chinese_identifiers(code)

    def test_start_condition_variants(self):
        """开始条件可以选:变量 / 刚按下 / 播完 / 接引脚(不用接线)。"""
        code, warnings, _anim = self._tree(gk_start_mode="VAR",
                                           gk_var_name="battle_on")
        self.assertEqual(warnings, [], warnings)
        self.assertIn("if battle_on:", code)

        code, warnings, _anim = self._tree(gk_start_mode="ACTION_JUST",
                                           gk_cond_action="attack")
        self.assertEqual(warnings, [], warnings)
        self.assertIn("Input.is_action_just_pressed(ACTION_ATTACK)", code)

        code, warnings, _anim = self._tree(gk_start_mode="FINISH",
                                           gk_anim_name="intro")
        self.assertEqual(warnings, [], warnings)
        self.assertIn('_gk_anim_finished == "intro"', code)

    def test_start_condition_without_wire_warns(self):
        """选了『自己写』但条件引脚空着 -> 明确告警(否则这张图永远不动)。"""
        _code, warnings, _anim = self._tree(gk_start_mode="PIN")
        self.assertTrue(any("开始" in w and "条件" in w for w in warnings),
                        warnings)

    def test_status_outputs_are_boolean(self):
        """『动画_运行状态』把布尔情况交给玩法图。"""
        import types

        anim = gk_stub.StubNodeTree("角色动画")
        anim.bl_idname = "GKAnimationTree"
        start = anim.new_node(NN.GKNodeAnimEntry, "开始")
        exit_node = anim.new_node(NN.GKNodeAnimExit, "结束")
        idle = anim.new_node(NN.GKNodeAnimState, "状态待机")
        idle.gk_state_name = "idle"
        gk_stub.connect(anim, start, "开始", idle, "进入")
        gk_stub.connect(anim, idle, "转到", exit_node, "进入")

        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        ref = tree.new_node(NN.GKNodeAnimStateMachineRef, "状态机")
        gk_stub.connect(tree, event, "执行", ref, "执行")
        branch = tree.new_node(NN.GKNodeBranch, "如果结束了")
        status = tree.new_node(NN.GKNodeAnimStatus, "动画状态")
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="done")
        physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        gk_stub.connect(tree, physics, "执行", branch, "执行")
        gk_stub.connect(tree, status, "已结束", branch, "条件")
        gk_stub.connect(tree, branch, "是", printer, "执行")

        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[anim]))
        try:
            code, warnings = gen(tree, V3)
        finally:
            detach_anim_tree(original)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_anim_done", code)
        self.assertIn("if _gk_anim_done:", code)

    def test_no_entry_no_exit_keeps_old_output(self):
        """没放开始口/结束口的**老图**生成结果不变(不带闸门)。"""
        code, warnings = self._build([("idle", "在移动", "run")]) \
            if hasattr(self, "_build") else (None, None)
        # 用最朴素的图直接验证:只有状态 + 转场
        import types

        anim = gk_stub.StubNodeTree("角色动画")
        anim.bl_idname = "GKAnimationTree"
        loco = anim.new_node(NN.GKNodeAnimLocomotion, "移动状态")
        idle = anim.new_node(NN.GKNodeAnimState, "状态idle")
        idle.gk_state_name = "idle"
        run = anim.new_node(NN.GKNodeAnimState, "状态run")
        run.gk_state_name = "run"
        trans = anim.new_node(NN.GKNodeAnimTransition, "T")
        gk_stub.connect(anim, idle, "转到", trans, "上游")
        gk_stub.connect(anim, trans, "转到", run, "进入")
        gk_stub.connect(anim, loco, "在移动", trans, "条件")

        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        ref = tree.new_node(NN.GKNodeAnimStateMachineRef, "状态机")
        gk_stub.connect(tree, event, "执行", ref, "执行")
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[anim]))
        try:
            code, warnings = gen(tree, V3)
        finally:
            detach_anim_tree(original)
        self.assertEqual(warnings, [], warnings)
        self.assertNotIn("_gk_anim_started", code)
        self.assertNotIn("_gk_anim_done", code)
        self.assertIn('if anim_state == "idle":', code)


class TestMultipleAnimTrees(Base):
    """一个玩法图里放**多个**『动画_状态机』(用户 2026-10-03 的疑问)。

    用户原话:"一个游戏逻辑里是否可以有多个游戏动画?因为我在动画_状态机
    节点中发现,它并不能指定与自定义选择哪个游戏动画,就是如果创建多个
    游戏动画,但动画_状态机只显示第一个创建的。"

    答案是**能** —— 但以前真的不行:两张树都写进同一个 `anim_state`、
    共用同一个 `_gk_anim_enter`,后一张把前一张整个盖掉(这个测试当时
    跑出来就是"只有一个 anim_state / 一个 _gk_anim_enter")。
    """

    def _two_trees(self):
        import types

        def make_anim(name, states, first, second):
            tree = gk_stub.StubNodeTree(name)
            tree.bl_idname = "GKAnimationTree"
            made = {}
            for state in states:
                node = tree.new_node(NN.GKNodeAnimState, "状态" + state)
                node.gk_state_name = state
                made[state] = node
            trans = tree.new_node(NN.GKNodeAnimTransition, "T")
            gk_stub.connect(tree, made[first], "转到", trans, "上游")
            gk_stub.connect(tree, trans, "转到", made[second], "进入")
            trans.gk_cond_source = "FINISH"
            return tree

        anim_a = make_anim("角色动作", ["idle", "run"], "idle", "run")
        anim_b = make_anim("UI动作", ["normal", "hover"], "normal", "hover")

        logic = make_tree()
        event = logic.new_node(NN.GKNodeOnReady, "准备")
        ref_a = logic.new_node(NN.GKNodeAnimStateMachineRef, "状态机A")
        ref_a.gk_tree_name = "角色动作"
        ref_b = logic.new_node(NN.GKNodeAnimStateMachineRef, "状态机B")
        ref_b.gk_tree_name = "UI动作"
        # 两台机器必须用**不同**的状态变量名 —— 同名会互相覆盖,
        # 生成器会明确告警(而不是偷偷给第二台改名,那会让
        # 『动画_运行状态』读到错的那一份)
        ref_b.gk_state_var = "ui_state"
        gk_stub.connect(logic, event, "执行", ref_a, "执行")
        gk_stub.connect(logic, ref_a, "完成", ref_b, "执行")

        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data",
                types.SimpleNamespace(node_groups=[anim_a, anim_b]))
        try:
            code, warnings = gen(logic, V3)
        finally:
            detach_anim_tree(original)
        return code, warnings

    def test_two_trees_get_their_own_state_and_functions(self):
        """两张动画树 -> 两套状态变量 + 两个进入函数,互不覆盖。"""
        code, warnings = self._two_trees()
        self.assertEqual(warnings, [], warnings)
        # 两套状态变量(各用各的名字)
        self.assertIn("var anim_state: String", code)
        self.assertIn("var ui_state: String", code)
        # 两个进入函数
        self.assertIn("func _gk_anim_enter(", code)
        self.assertIn("func _gk_anim_enter_2(", code)
        # 各自的起始状态都播了(不是只播第一张树的)
        self.assertIn('_gk_anim_enter("idle")', code)
        self.assertIn('_gk_anim_enter_2("normal")', code)
        self.assert_no_chinese_identifiers(code)

    def test_same_state_var_name_warns(self):
        """两台状态机用同一个状态变量名 -> 明确告警(不是偷偷改名)。

        偷偷把第二台改成 anim_state_2 的话,按名字读状态的
        『动画_运行状态』就永远读到错的那一份。
        """
        import types

        anim = gk_stub.StubNodeTree("角色动作")
        anim.bl_idname = "GKAnimationTree"
        state = anim.new_node(NN.GKNodeAnimState, "A")
        state.gk_state_name = "idle"

        logic = make_tree()
        event = logic.new_node(NN.GKNodeOnReady, "准备")
        ref_a = logic.new_node(NN.GKNodeAnimStateMachineRef, "状态机A")
        ref_b = logic.new_node(NN.GKNodeAnimStateMachineRef, "状态机B")
        gk_stub.connect(logic, event, "执行", ref_a, "执行")
        gk_stub.connect(logic, ref_a, "完成", ref_b, "执行")
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[anim]))
        try:
            _code, warnings = gen(logic, V3)
        finally:
            detach_anim_tree(original)
        self.assertTrue(any("状态变量名" in w for w in warnings), warnings)

    def test_tree_name_picks_the_right_tree(self):
        """『树名』真的决定用哪张树 —— 不是永远第一张。"""
        import types

        anim_a = gk_stub.StubNodeTree("角色动作")
        anim_a.bl_idname = "GKAnimationTree"
        state_a = anim_a.new_node(NN.GKNodeAnimState, "A")
        state_a.gk_state_name = "idle"
        anim_b = gk_stub.StubNodeTree("UI动作")
        anim_b.bl_idname = "GKAnimationTree"
        state_b = anim_b.new_node(NN.GKNodeAnimState, "B")
        state_b.gk_state_name = "normal"

        logic = make_tree()
        event = logic.new_node(NN.GKNodeOnReady, "准备")
        ref = logic.new_node(NN.GKNodeAnimStateMachineRef, "状态机")
        ref.gk_tree_name = "UI动作"          # ← 指名要第二张
        gk_stub.connect(logic, event, "执行", ref, "执行")
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data",
                types.SimpleNamespace(node_groups=[anim_a, anim_b]))
        try:
            code, _warnings = gen(logic, V3)
        finally:
            detach_anim_tree(original)
        self.assertIn('_gk_anim_enter("normal")', code)
        self.assertNotIn('_gk_anim_enter("idle")', code)


class TestInputDirectionAndBones(Base):
    """第四轮:输入_方向(整合) + 骨骼节点(照 UPBGE 的 Get/Set Bone Data)。"""

    def test_input_direction_gives_vector_and_components(self):
        """一个『输入_方向』直接给 方向 / 左右 / 前后 —— 不用设变量。

        用户原话:"这两个都可以整合到一起的,比如输入按键时有 x,y 的属性
        输出,这样就可以调节向前向量和向右向量……这应该是自带的,
        不应该要再设置变量"。
        """
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        move = tree.new_node(NN.GKNodeMoveDirection, "移动")
        direction = tree.new_node(NN.GKNodeInputDirection, "方向",
                                  gk_key_up="W", gk_key_down="S",
                                  gk_key_left="A", gk_key_right="D")
        printer = tree.new_node(NN.GKNodePrint, "打印")
        gk_stub.connect(tree, event, "执行", move, "执行")
        gk_stub.connect(tree, direction, "方向", move, "方向")
        # 分量也要能单独取出来(向前向量 / 向右向量)
        gk_stub.connect(tree, direction, "前后", printer, "内容")
        code, warnings = gen(tree, V3)
        # 会有"速度 300 在 3D 里不像米/秒"的提醒 —— 这里只关心输入节点
        self.assertFalse([w for w in warnings if "输入" in w], warnings)
        self.assertIn("Input.get_action_strength(ACTION_KEY_D)", code)
        # 分量先落局部变量:不必把同一个调用写两遍
        self.assertIn("var _in_x", code)
        # 3D 项目里『方向』给的是 Vector3(否则 velocity.z 会报 Vector2 没 .z)
        self.assertIn("Vector3(_in_x", code)
        # 斜着走不加速:夹的是**分量**(夹向量的话 3D 那条路根本用不上)
        self.assertIn("_in_x1 /= _in_len3", code)
        self.assertIn("_in_y2 /= _in_len3", code)
        self.assertIn("_in_len", code)                # 斜着走不加速

    def test_input_direction_key_capture_reaches_export(self):
        """四个方向捕获的键都要跟着导出(按键 -> project.godot)。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        direction = tree.new_node(NN.GKNodeInputDirection, "方向",
                                  gk_key_up="W", gk_key_down="S",
                                  gk_key_left="A", gk_key_right="D")
        printer = tree.new_node(NN.GKNodePrint, "打印")
        gk_stub.connect(tree, event, "执行", printer, "执行")
        gk_stub.connect(tree, direction, "方向", printer, "内容")
        _code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        pairs = dict((item[0], item[1]) if isinstance(item, (tuple, list))
                     else (item, "") for item in codegen.last_used_actions)
        for action, key in (("key_w", "W"), ("key_s", "S"),
                            ("key_a", "A"), ("key_d", "D")):
            self.assertEqual(pairs.get(action), key, pairs)

    def test_set_bone_rotation_is_additive(self):
        """骨骼_设置(保留)= 在原姿势上叠乘,不覆盖动画自己的动作。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        bone = tree.new_node(NN.GKNodeSetBone, "点头",
                             gk_bone="head", gk_mode="ADD",
                             gk_do_rot=True, gk_do_pos=False)
        gk_stub.connect(tree, event, "执行", bone, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('find_bone("head")', code)
        self.assertIn("get_bone_pose_rotation", code)
        self.assertIn("Quaternion.from_euler", code)
        # 找不到骨头要**告警**而不是让 Godot 刷一屏 -1 越界
        self.assertIn("找不到骨骼", code)
        self.assertIn("func _gk_find_skeleton", code)

    def test_get_bone_outputs(self):
        """骨骼_取数据:位置 / 旋转 / 缩放 / 世界位置(枪口在哪)。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        bone = tree.new_node(NN.GKNodeGetBone, "枪口", gk_bone="weapon_r")
        printer = tree.new_node(NN.GKNodePrint, "打印")
        gk_stub.connect(tree, event, "执行", printer, "执行")
        gk_stub.connect(tree, bone, "世界位置", printer, "内容")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _gk_read_bone", code)
        self.assertIn('"world"', code)
        self.assertIn("get_bone_global_pose", code)

    def test_bone_follows_camera_every_frame(self):
        """骨骼_跟随视角:每物理帧跟着相机偏,带权重 + 限角 + 平滑。

        这就是回答"第一人称转视角时能不能让角色一部分模型跟着转"。
        """
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        look = tree.new_node(NN.GKNodeBoneLookAtCamera, "脖子跟随",
                             gk_bone="head", gk_camera="Camera",
                             gk_yaw_weight=0.5, gk_pitch_weight=0.35,
                             gk_limit=60.0, gk_smooth=8.0)
        gk_stub.connect(tree, event, "执行", look, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        physics = code.split("func _physics_process", 1)[1]
        self.assertIn("find_bone(\"head\")", physics)
        self.assertIn("set_bone_pose_rotation", physics)
        self.assertIn("clampf", physics)          # 限角
        self.assertIn("lerpf", physics)           # 平滑
        self.assertIn("deg_to_rad(60.0)", physics)
        # 挂 _ready 的那次只做绑定,不该把旋转写在那里
        ready = code.split("func _ready", 1)[1].split("\nfunc ", 1)[0]
        self.assertNotIn("set_bone_pose_rotation", ready)


class TestCameraSuite(Base):
    """摄像机套件(照虚幻的 Camera + SpringArm)。

    用户 2026-10-03 第四轮:"摄像机的设置,切换,启用与禁用,视距,视野,
    分支的场景摄像机的设置,切换,启用与禁用,弹簧臂的设计,摄像机的旋转,
    缩放,位置等向量,浮点,两变量的设计,同样是按照虚幻蓝图进行"。
    """

    def test_camera_enable_disable(self):
        """启用 = make_current;禁用 = **正开着才**摘下来。

        不做"是不是我开着"的判断的话,禁用一台没用着的相机会把
        当前画面弄黑(用户会以为游戏崩了)。
        """
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        on = tree.new_node(NN.GKNodeCameraActive, "开A", gk_target="主相机",
                           gk_active=True)
        off = tree.new_node(NN.GKNodeCameraActive, "关B", gk_target="备用相机",
                           gk_active=False)
        gk_stub.connect(tree, event, "执行", on, "执行")
        gk_stub.connect(tree, on, "完成", off, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn(".make_current()", code)
        self.assertIn(".is_current():", code)
        self.assertIn(".clear_current()", code)
        self.assert_no_chinese_identifiers(code)

    def test_camera_setup_fov_and_distance(self):
        """『相机_设置』:视野 + 视距(沿相机朝向推远),带平滑走 Tween。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        setup = tree.new_node(NN.GKNodeCameraSetup, "设机位",
                              gk_fov=45.0, gk_distance=1.5, gk_smooth=0.4)
        gk_stub.connect(tree, event, "执行", setup, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("tween_property", code)
        self.assertIn('"fov"', code)
        # 视距 = 沿相机自己的 +Z 退(Godot 相机朝 -Z 看,所以退是 +Z)
        self.assertIn("global_transform.basis.z", code)
        self.assertIn("global_position + ", code)

    def test_camera_setup_zero_means_leave_alone(self):
        """『相机_设置』没填的项(0)= **不动它**,不能生成 `camera.fov = 0`。

        用户真跑踩到的:节点上视野没填(默认 0),生成出 fov = 0 ——
        Godot 里那是"镜头废掉",画面直接不对,而节点看着完全正常。
        """
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        setup = tree.new_node(NN.GKNodeCameraSetup, "只推视距",
                              gk_target="主相机", gk_fov=0.0,
                              gk_distance=2.0)
        gk_stub.connect(tree, event, "执行", setup, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertNotIn('"fov"', code)          # 视野没填 -> 不碰
        self.assertNotIn(".fov = ", code)
        self.assertIn("basis.z", code)           # 视距照做

        # 两个都没填 -> 明确告警(而不是静默生成一个什么都不做的节点)
        tree2 = make_tree()
        event2 = tree2.new_node(NN.GKNodeOnReady, "准备")
        idle = tree2.new_node(NN.GKNodeCameraSetup, "啥也没填",
                              gk_target="主相机")
        gk_stub.connect(tree2, event2, "执行", idle, "执行")
        _code2, warnings2 = gen(tree2, V3)
        self.assertTrue(any("什么都没设" in w for w in warnings2), warnings2)

    def test_logic_ports_declared_and_validated(self):
        """逻辑图的『对外端口』:声明了就生成变量,没声明会**明确告警**。

        用户第 21 轮:"一个游戏逻辑最后应该输出布尔变量,以便与其他的
        游戏逻辑联动"。以前是节点上随便填名字 —— 打错一个字母不报错,
        别的图读到空值,你只会以为逻辑没跑到。
        """
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        out = tree.new_node(NN.GKNodeLogicOutput, "输出",
                            gk_output_name="完成", gk_value=True)
        gk_stub.connect(tree, event, "执行", out, "执行")

        # 没声明端口 -> 明确告警(而且说清怎么修)
        _code, warnings = gen(tree, V3)
        self.assertTrue(any("没有在侧栏" in w for w in warnings), warnings)

        # 声明之后 -> 不告警,并且生成带类型的变量
        tree.gk_ports.add()
        tree.gk_ports[0].port_name = "完成"
        tree.gk_ports[0].port_type = "bool"
        tree.gk_ports[0].default_value = "false"
        code, warnings2 = gen(tree, V3)
        self.assertEqual([w for w in warnings2 if "端口" in str(w)], [])
        # 变量名会被转成合法标识符(中文名 -> 英文),所以断言"类型 + 初值 +
        # 赋值",而不是断言那个中文名。
        self.assertIn(": bool = false", code)
        self.assertIn(" = true", code)
        # 导出报告里要能看到"这张图对外给了什么"
        notes = " ".join(getattr(codegen, "last_notes", []) or [])
        self.assertIn("对外端口", notes, notes)

    def test_camera_position_vector_or_floats(self):
        """『相机_位置』:向量引脚优先,没接就用 X/Y/Z 浮点。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        pos = tree.new_node(NN.GKNodeCameraPosition, "挪机位",
                            gk_target="主相机", gk_mode="SET",
                            gk_x=1.0, gk_y=2.0, gk_z=3.0)
        gk_stub.connect(tree, event, "执行", pos, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("Vector3(1.0, 2.0, 3.0)", code)
        # SET = 直接换坐标(不是相加)
        self.assertIn("global_position = Vector3(1.0, 2.0, 3.0)", code)

        # 接了向量就用向量(浮点被忽略)
        tree2 = make_tree()
        event2 = tree2.new_node(NN.GKNodeOnReady, "准备")
        vec = tree2.new_node(NN.GKNodeVectorMake, "向量",
                             gk_x=7.0, gk_y=8.0, gk_z=9.0)
        pos2 = tree2.new_node(NN.GKNodeCameraPosition, "挪机位",
                              gk_target="主相机", gk_mode="ADD")
        gk_stub.connect(tree2, event2, "执行", pos2, "执行")
        gk_stub.connect(tree2, vec, "向量", pos2, "位置")
        code2, warnings2 = gen(tree2, V3)
        self.assertEqual(warnings2, [], warnings2)
        self.assertIn("global_position + (Vector3(7.0, 8.0, 9.0))", code2)

    def test_camera_rotation_clamps_pitch(self):
        """『相机_旋转』:相对加法 + 俯仰限制(第一人称必备)。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        rot = tree.new_node(NN.GKNodeCameraRotation, "转视角",
                            gk_target="主相机", gk_mode="ADD",
                            gk_yaw=1.0, gk_pitch=-0.5, gk_clamp_pitch=True)
        gk_stub.connect(tree, event, "执行", rot, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("rotation_degrees + (Vector3(-0.5, 1.0, 0.0))", code)
        self.assertIn("_gk_rot.x > 89.0", code)
        self.assertIn("_gk_rot.x = -89.0", code)

    def test_camera_spring_arm_runs_every_physics_frame(self):
        """弹簧臂:每物理帧摆相机 + 碰撞缩短 + 平滑,而且**不穿墙**。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        arm = tree.new_node(NN.GKNodeCameraSpringArm, "弹簧臂",
                            gk_target="示例角色", gk_camera="Camera",
                            gk_arm=5.0, gk_height=1.6, gk_pitch=-20.0,
                            gk_collision=True, gk_lag=10.0)
        gk_stub.connect(tree, event, "执行", arm, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        # 每帧逻辑必须在 _physics_process 里(和角色同一个时钟)
        physics = code.split("func _physics_process", 1)[1]
        self.assertIn("_gk_spring_", physics)
        self.assertIn("intersect_ray", physics)
        self.assertIn("PhysicsRayQueryParameters3D.create", physics)
        # 排除自己:不排除的话杆子会被自己顶住,相机会贴到脸上
        self.assertIn("_gk_exclude.append", physics)
        self.assertIn("look_at", physics)
        self.assert_no_chinese_identifiers(code)

    def test_camera_spring_arm_disabled_registers_nothing(self):
        """关掉弹簧臂 = 不注册每帧逻辑(不是"注册了再判断")。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        arm = tree.new_node(NN.GKNodeCameraSpringArm, "弹簧臂",
                            gk_target="示例角色", gk_enabled=False)
        gk_stub.connect(tree, event, "执行", arm, "执行")
        code, _warnings = gen(tree, V3)
        self.assertNotIn("intersect_ray", code)
        self.assertNotIn("_gk_spring_pos", code)

    def test_camera_status_outputs(self):
        """『相机_取状态』:名字 / 位置 / 朝向 / 视野 / 开着吗。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        branch = tree.new_node(NN.GKNodeBranch, "如果")
        status = tree.new_node(NN.GKNodeCameraStatus, "相机状态")
        setter = tree.new_node(NN.GKNodeSetVar, "记下来")
        setter.gk_var_name = "cam_ready"
        printer = tree.new_node(NN.GKNodePrint, "打印")
        gk_stub.connect(tree, event, "执行", branch, "执行")
        gk_stub.connect(tree, status, "开着吗", branch, "条件")
        gk_stub.connect(tree, branch, "是", setter, "执行")
        gk_stub.connect(tree, status, "相机名", printer, "内容")
        gk_stub.connect(tree, setter, "完成", printer, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _gk_current_camera()", code)
        self.assertIn("is_current()", code)
        self.assertIn(".name", code)
        self.assert_no_chinese_identifiers(code)


class TestTimelineAndCamera(Base):
    """时间轴 / 相机:缓动任意属性、视野平滑变化、丝滑切相机。"""

    def test_tween_property_uses_godot_tween(self):
        """『定时_缓动属性』-> Tween 驱动任意节点属性。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        tween = tree.new_node(NN.GKNodeTweenProperty, "推近",
                              gk_property="fov", gk_to=30.0, gk_time=0.8)
        gk_stub.connect(tree, event, "执行", tween, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('tween_property(self, "fov",', code)
        self.assertIn("create_tween()", code)
        self.assertIn("Tween.TRANS_SINE", code)
        self.assert_no_chinese_identifiers(code)

    def test_tween_property_without_name_warns(self):
        """没填属性名 -> 告警而不是生成一句错的。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        tween = tree.new_node(NN.GKNodeTweenProperty, "没填", gk_property="")
        gk_stub.connect(tree, event, "执行", tween, "执行")
        _code, warnings = gen(tree, V3)
        self.assertTrue(any("属性" in w for w in warnings), warnings)

    def test_camera_switch_with_blend(self):
        """相机_切换 填了过渡 -> 生成插值 + 每帧推进的钩子。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        switch = tree.new_node(NN.GKNodeCameraSetCurrent, "切镜头",
                               gk_target="特写机位", gk_blend=0.6)
        gk_stub.connect(tree, event, "执行", switch, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_cam_blend_left", code)
        self.assertIn("interpolate_with(", code)
        self.assertIn("get_viewport().get_camera_3d()", code)
        # 每帧推进必须在 _physics_process 里(和角色同一个时钟)
        physics = code.split("func _physics_process", 1)[1]
        self.assertIn("_gk_cam_blend_left", physics)
        self.assert_no_chinese_identifiers(code)

    def test_camera_switch_without_blend_is_instant(self):
        """没填过渡 = 老行为一字不变(直接 make_current)。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        switch = tree.new_node(NN.GKNodeCameraSetCurrent, "切镜头",
                               gk_target="主相机", gk_blend=0.0)
        gk_stub.connect(tree, event, "执行", switch, "执行")
        code, _warnings = gen(tree, V3)
        self.assertIn(".make_current()", code)
        self.assertNotIn("_gk_cam_blend_left", code)

    def test_camera_zoom_with_duration_uses_tween(self):
        """相机_缩放 填了时长 -> Tween;默认 0 -> 和以前一样。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        zoom = tree.new_node(NN.GKNodeCameraZoom, "推近",
                             gk_value=25.0, gk_time=1.2, gk_target="主相机")
        gk_stub.connect(tree, event, "执行", zoom, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('tween_property(', code)
        self.assertIn('"fov"', code)
        self.assertIn("1.2", code)

        tree2 = make_tree()
        event2 = tree2.new_node(NN.GKNodeOnReady, "准备")
        zoom2 = tree2.new_node(NN.GKNodeCameraZoom, "立刻", gk_value=25.0,
                               gk_time=0.0, gk_target="主相机")
        gk_stub.connect(tree2, event2, "执行", zoom2, "执行")
        code2, _w = gen(tree2, V3)
        # 未连线的数值会变成 @export 检查器参数(这是已有行为),所以检查
        # "走的是立刻赋值那条路"而不是硬编码数字
        self.assertIn(".fov = camera_zoom", code2)
        self.assertNotIn("create_tween", code2)



    """图状状态机在真实用法里踩出来的坑(每一条都是实测过的)。"""

    def _anim_with(self, specs, with_physics=False, **ref_props):
        """搭一张动画树 + 逻辑图。specs: [(状态名, 条件输出名, 目标状态名)]"""
        import types

        tree = make_tree()
        anim = gk_stub.StubNodeTree("角色动画")
        anim.bl_idname = "GKAnimationTree"
        loco = anim.new_node(NN.GKNodeAnimLocomotion, "移动状态")
        states = {}
        for start, _cond, end in specs:
            for name in (start, end):
                if name not in states:
                    node = anim.new_node(NN.GKNodeAnimState, "状态" + name)
                    node.gk_state_name = name
                    states[name] = node
        for index, (start, cond, end) in enumerate(specs):
            trans = anim.new_node(NN.GKNodeAnimTransition, f"T{index}")
            gk_stub.connect(anim, states[start], "转到", trans, "上游")
            gk_stub.connect(anim, trans, "转到", states[end], "进入")
            if cond:
                gk_stub.connect(anim, loco, cond, trans, "条件")

        event = tree.new_node(NN.GKNodeOnReady, "准备")
        ref = tree.new_node(NN.GKNodeAnimStateMachineRef, "状态机", **ref_props)
        gk_stub.connect(tree, event, "执行", ref, "执行")
        if with_physics:
            physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
            printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="跑")
            gk_stub.connect(tree, physics, "执行", printer, "执行")

        import types
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[anim]))
        try:
            return gen(tree, {"dimension": "3",
                              "base_class": "CharacterBody3D"})
        finally:
            detach_anim_tree(original)

    def test_physics_process_is_not_declared_twice(self):
        """『每当_物理帧』+ 动画状态机 -> **只能有一个** _physics_process。

        ── 实测踩过(示例工程生成出来的脚本)────────────────────────
        用户的事件生成的是 `func _physics_process(delta: float)`,
        状态机生成的是 `func _physics_process(_delta: float)` —— 参数名不同,
        按完整签名找永远找不到,于是脚本里出现**两个同名函数**:
            Parse Error: Function "_physics_process()" already exists
        Godot 直接拒绝加载,游戏一点都跑不起来。
        """
        code, warnings = self._anim_with([("idle", "在移动", "run")],
                                         with_physics=True)
        self.assertEqual(warnings, [], warnings)
        self.assertEqual(code.count("func _physics_process("), 1, code)
        # 两边的代码都在,而且都在那一个函数里
        self.assertIn("print(\"跑\")", code)
        self.assertIn('if anim_state == "idle":', code)
        # 状态机的判断必须落在 _physics_process 里(不是 _ready 里)
        tail = code.split("func _physics_process(", 1)[1]
        self.assertIn('if anim_state == "idle":', tail.split("func ", 1)[0])

    def test_wait_does_not_emit_downstream_twice(self):
        """『等待_秒』后面的节点只能生成一次。

        ── 实测踩过(示例工程里 `is_hurt = false` 出现了两行)──────────
        `emit_statement()` 调用完处理器之后**已经**会沿『完成』往下走,
        而 `_stmt_wait` 自己又调了一次 `_chain` —— 下游整段生成两遍。
        接的是『音效_播放』『实例_生成』这类节点时,就是**真的执行两次**。
        """
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        wait = tree.new_node(NN.GKNodeWait, "等一秒")
        wait.inputs.get("秒").default_value = 0.5
        setter = tree.new_node(NN.GKNodeSetVar, "设标志", gk_var_name="flag")
        boolean = tree.new_node(NN.GKNodeConstBool, "真", gk_value=True)
        gk_stub.connect(tree, event, "执行", wait, "执行")
        gk_stub.connect(tree, wait, "完成", setter, "执行")
        gk_stub.connect(tree, boolean, "值", setter, "值")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertEqual(code.count("flag = true"), 1, code)
        self.assertEqual(code.count("create_timer"), 1, code)

    def test_variable_collision_with_export_is_renamed(self):
        """脚本变量和检查器参数重名 -> 自动改名,不能生成两个同名变量。

        ── 实测踩过(示例工程里动画树有个变量叫 move_speed)──────────
        『移动_按方向』的『速度』会生成
            @export var move_speed: float = 6.0
        而动画树里的变量也叫 move_speed -> Godot:
            Parse Error: Variable "move_speed" has the same name as a
            previously declared variable
        整个脚本加载失败。变量是用户起的名字,保住它,把自动生成的参数改名。
        """
        tree = make_tree()
        anim = gk_stub.StubNodeTree("角色动画")
        anim.bl_idname = "GKAnimationTree"
        anim.gk_variables = [types.SimpleNamespace(var_name="move_speed",
                                                  var_type="float",
                                                  default_value="1.5")]
        idle = anim.new_node(NN.GKNodeAnimState, "状态idle")
        idle.gk_state_name = "idle"
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        ref = tree.new_node(NN.GKNodeAnimStateMachineRef, "状态机")
        gk_stub.connect(tree, event, "执行", ref, "执行")

        physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        move = tree.new_node(NN.GKNodeMoveDirection, "移动")
        gk_stub.connect(tree, physics, "执行", move, "执行")

        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data",
                types.SimpleNamespace(node_groups=[anim]))
        try:
            code, warnings = gen(tree, {"dimension": "3",
                                        "base_class": "CharacterBody3D"})
        finally:
            detach_anim_tree(original)
        self.assertTrue(any("重名" in w for w in warnings), warnings)
        # 两个名字都必须只声明一次,而且不能同名
        self.assertEqual(code.count("var move_speed:"), 1, code)
        self.assertEqual(code.count("var move_speed_param:"), 1, code)

    def test_state_has_adjustable_inlets(self):
        """『动画_状态』的入口数量可调。

        ── 为什么需要(Blender 的硬限制)────────────────────────────
        **一个输入引脚只允许一条连线**。多个转场指向同一个状态时,
        后接的会把前一条**顶掉**(画布上箭头会跳走)。
        所以入口要能加:进入 / 进入 2 / 进入 3 …
        """
        node = make_tree().new_node(NN.GKNodeAnimState, "状态")
        self.assertEqual([s.name for s in node.inputs], ["进入", "进入 2"])
        node.gk_in_count = 4
        node.build_sockets()
        self.assertEqual([s.name for s in node.inputs],
                         ["进入", "进入 2", "进入 3", "进入 4"])
        # 老文件里接在『进入』上的线不会因为改名而断
        self.assertEqual(node.inputs[0].name, "进入")


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
            gk_stub.connect(anim, states[start], "转到", transition, "上游")
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
        # 上游流和布尔条件必须是**两个**引脚。
        # 引脚名叫『上游』不叫『进入』—— 用户实测里就是因为两个节点
        # 的引脚同名(都叫『进入』)而接错,链路空掉、状态机没转移。
        self.assertIn("上游", names)
        self.assertIn("条件", names)
        self.assertIn("转到", [s.name for s in node.outputs])

    def test_entry_state_is_the_one_without_incoming(self):
        """没有任何转场指向的状态 = 起始状态(照 UE5 的 Entry 节点)。

        而且它**必须真的被播**。老代码只写 `anim_state = "idle"`,
        从头到尾没有一处 play() —— 用户进游戏后角色一直摆着不动,
        这正是他报的"动画_状态机未能正常工作"的一半原因。
        """
        code, warnings = self._build([("idle", "在移动", "run")])
        self.assertEqual(warnings, [], warnings)
        self.assertIn('_gk_anim_enter("idle")', code)
        # 进入状态那个函数里必须真的调 play(而不是只记名字)
        self.assertIn(".play(next_state)", code)

    def test_chain_generates_two_transitions(self):
        code, warnings = self._build([("idle", "在移动", "run"),
                                      ("run", "已离地", "jump")])
        self.assertEqual(warnings, [], warnings)
        self.assertIn('if anim_state == "idle":', code)
        self.assertIn('_gk_anim_enter("run")', code)
        self.assertIn('elif anim_state == "run":', code)
        self.assertIn('_gk_anim_enter("jump")', code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_cycle_does_not_hang(self):
        """状态机天然有环(idle -> run -> idle),导出不能无限递归。

        ── 这一条和上一版的区别 ────────────────────────────────────
        上一版是"沿着链走一圈,走到走过的状态就停",环里的**第二条转移
        会被丢掉**(run -> idle 不生成)。现在按**图**生成:每个状态
        只看自己的出边,环天然成立,两条转移都在。
        """
        code, warnings = self._build([("idle", "在移动", "run"),
                                      ("run", "速度快", "idle")])
        self.assertEqual(warnings, [], warnings)
        self.assertIn('_gk_anim_enter("run")', code)
        self.assertIn('_gk_anim_enter("idle")', code)
        self.assertIn("func _physics_process", code)

    def test_state_can_have_many_outgoing_transitions(self):
        """一个状态多条出线 —— 真实状态机最常见的形状。

        实测踩过:老实现走链只取第一条线,第二条**静默丢掉**,
        于是 `idle -> run` 和 `idle -> jump` 只能活一个。
        """
        code, warnings = self._build([("idle", "在移动", "run"),
                                      ("idle", "已离地", "jump")])
        self.assertEqual(warnings, [], warnings)
        self.assertIn('if anim_state == "idle":', code)
        self.assertIn('_gk_anim_enter("run")', code)
        self.assertIn('_gk_anim_enter("jump")', code)
        # 状态内部用 elif:一帧只走一步(否则同一帧能连跳好几个状态,
        # 中间的动作一帧都播不出来)
        self.assertIn("elif", code)
        # 两条线都在同一个 if anim_state == "idle" 段里
        segment = code.split('if anim_state == "idle":', 1)[1]
        segment = segment.split("func ", 1)[0]
        self.assertIn("_gk_anim_enter(", segment)

    def test_direct_state_link_means_play_then_switch(self):
        """状态直接连状态(中间不放过转场)= "这段动画播完就切"。

        用户实际的图就是这么连的(他的『转场』上游空着、状态的『转到』
        直接指向下一个状态)。老实现把这种情况判成"链路到此为止",
        导出后 _physics_process 只剩一句 pass。
        """
        tree = make_tree()
        anim = gk_stub.StubNodeTree("角色动画")
        anim.bl_idname = "GKAnimationTree"
        first = anim.new_node(NN.GKNodeAnimState, "状态骨骼动作", (0.0, 0.0))
        first.gk_state_name = "骨骼动作"
        second = anim.new_node(NN.GKNodeAnimState, "状态形态键", (0.0, -200.0))
        second.gk_state_name = "形态键动作"
        gk_stub.connect(anim, first, "转到", second, "进入")
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        ref = tree.new_node(NN.GKNodeAnimStateMachineRef, "状态机")
        gk_stub.connect(tree, event, "执行", ref, "执行")

        import types
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[anim]))
        try:
            code, warnings = gen(tree, {"dimension": "3",
                                        "base_class": "CharacterBody3D"})
        finally:
            detach_anim_tree(original)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('_gk_anim_enter("骨骼动作")', code)
        # 条件是"骨骼动作 播完了" —— 靠 animation_finished 信号记下来的名字。
        # **不能**写成 `current_animation == "骨骼动作" and not is_playing()`:
        # 真 Godot 4.7 实测,一次性动画播完时 current_animation 会被清成 ""
        # (见 codegen._anim_finished_helpers 里的实测数据),那个条件永远不成立。
        self.assertIn('if (_gk_anim_finished == "骨骼动作"):', code)
        self.assertNotIn("current_animation ==", code)
        self.assertIn("animation_finished.is_connected(", code)
        self.assertIn("animation_finished.connect(", code)
        self.assertIn("func _gk_on_anim_finished(anim_name: StringName)", code)
        self.assertIn('_gk_anim_finished = ""', code)
        self.assertIn('_gk_anim_enter("形态键动作")', code)
        self.assert_no_chinese_identifiers(code)

    def test_transition_can_use_state_dropdowns(self):
        """忘了连线时,转场的『从』/『到』下拉也能把图接起来(用户确认要)。"""
        tree = make_tree()
        anim = gk_stub.StubNodeTree("角色动画")
        anim.bl_idname = "GKAnimationTree"
        loco = anim.new_node(NN.GKNodeAnimLocomotion, "移动状态")
        idle = anim.new_node(NN.GKNodeAnimState, "状态idle")
        idle.gk_state_name = "idle"
        run = anim.new_node(NN.GKNodeAnimState, "状态run")
        run.gk_state_name = "run"
        trans = anim.new_node(NN.GKNodeAnimTransition, "转场",
                              gk_from_state="idle", gk_to_state="run")
        gk_stub.connect(anim, loco, "在移动", trans, "条件")
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        ref = tree.new_node(NN.GKNodeAnimStateMachineRef, "状态机")
        gk_stub.connect(tree, event, "执行", ref, "执行")

        import types
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[anim]))
        try:
            code, warnings = gen(tree, {"dimension": "3",
                                        "base_class": "CharacterBody3D"})
        finally:
            detach_anim_tree(original)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('if anim_state == "idle":', code)
        self.assertIn('_gk_anim_enter("run")', code)

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

    def test_wired_condition_ignored_when_source_is_input(self):
        """接了条件线、『条件来源』却选了输入 -> 线被忽略,必须说出来。

        用户实测就是这样:『在地面』接在『条件』上,而『条件来源』是
        "选输入:按住 move_right"。光看图完全看不出哪边生效。
        """
        def extra(anim, states, loco):
            trans = anim.new_node(NN.GKNodeAnimTransition, "T输入",
                                  gk_cond_source="ACTION_PRESSED",
                                  gk_cond_action="move_right")
            gk_stub.connect(anim, states["idle"], "转到", trans, "上游")
            gk_stub.connect(anim, trans, "转到", states["run"], "进入")
            gk_stub.connect(anim, loco, "在移动", trans, "条件")

        code, warnings = self._build([("idle", "在移动", "run")], extra=extra)
        self.assertTrue(any("被忽略" in w for w in warnings), warnings)
        self.assertIn("Input.is_action_pressed(", code)

    def test_duplicate_state_names_warn(self):
        """两个状态同名 -> 必须告警(状态机按名字判断该播谁)。"""
        code, warnings = self._build(
            [("idle", "在移动", "run")],
            extra=lambda anim, states, loco: setattr(
                states["run"], "gk_state_name", "idle"))
        self.assertTrue(any("都叫" in w for w in warnings), warnings)

    def test_only_enter_mode_emits_inline(self):
        """『只在开始时判』= 不挂 _physics_process,就地判一次。

        这条路径改起来最容易漏(状态图有两个生成点),所以钉住:
        它必须也能生成出**语法完整**的 if/elif,而不是半截。
        """
        tree = make_tree()
        anim = gk_stub.StubNodeTree("角色动画")
        anim.bl_idname = "GKAnimationTree"
        loco = anim.new_node(NN.GKNodeAnimLocomotion, "移动状态")
        idle = anim.new_node(NN.GKNodeAnimState, "状态idle")
        idle.gk_state_name = "idle"
        run = anim.new_node(NN.GKNodeAnimState, "状态run")
        run.gk_state_name = "run"
        trans = anim.new_node(NN.GKNodeAnimTransition, "T")
        gk_stub.connect(anim, idle, "转到", trans, "上游")
        gk_stub.connect(anim, trans, "转到", run, "进入")
        gk_stub.connect(anim, loco, "在移动", trans, "条件")

        event = tree.new_node(NN.GKNodeOnReady, "准备")
        ref = tree.new_node(NN.GKNodeAnimStateMachineRef, "状态机",
                            gk_check_mode="ON_ENTER")
        gk_stub.connect(tree, event, "执行", ref, "执行")

        import types
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[anim]))
        try:
            code, warnings = gen(tree, {"dimension": "3",
                                        "base_class": "CharacterBody3D"})
        finally:
            detach_anim_tree(original)
        self.assertEqual(warnings, [], warnings)
        # 就地判一次:没有 _physics_process,但在 _ready 里有转移判断
        self.assertNotIn("func _physics_process", code)
        self.assertIn('if anim_state == "idle":', code)
        self.assertIn('_gk_anim_enter("run")', code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_deprecated_player_property_is_ignored(self):
        """『动画_状态机』上的『动画播放器』物体选择已经废弃。

        用户要求删掉它("在此物体选择动画_状态机时自然是作用于本身")。
        老文件里可能还存着值 —— 不能让它复活成 `$别的物体`,
        否则会把动画播到别的节点上,而且路径还可能根本不存在。
        """
        tree = make_tree()
        anim = gk_stub.StubNodeTree("角色动画")
        anim.bl_idname = "GKAnimationTree"
        idle = anim.new_node(NN.GKNodeAnimState, "状态idle")
        idle.gk_state_name = "idle"
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        ref = tree.new_node(NN.GKNodeAnimStateMachineRef, "状态机",
                            gk_player="场景里的别的物体")
        gk_stub.connect(tree, event, "执行", ref, "执行")

        import types
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[anim]))
        try:
            code, _warnings = gen(tree, {"dimension": "3",
                                         "base_class": "CharacterBody3D"})
        finally:
            detach_anim_tree(original)
        self.assertIn("_gk_find_animation_player(self)", code)
        self.assertNotIn("场景里的别的物体", code)

    def test_no_player_pin_on_state_machine_ref(self):
        """『动画_状态机』不该再有『动画播放器』引脚(用户要求删掉)。"""
        node = make_tree().new_node(NN.GKNodeAnimStateMachineRef, "状态机")
        names = [s.name for s in node.inputs]
        self.assertNotIn("动画播放器", names, names)
        self.assertIn("执行", names)
        self.assertIn("树名", names)

    def test_entry_animation_played_only_once(self):
        """起始状态的动画只在 anim_state 为空时播一次。

        不加这层保护的话,每帧都会重新 play("idle") —— 动画永远停在
        第一帧,看起来就是"角色卡住不动"。
        """
        code, _warnings = self._build([("idle", "在移动", "run")])
        self.assertIn("if anim_state.is_empty():", code)
        ready = code.split("func _ready", 1)[1].split("func ", 1)[0]
        self.assertEqual(ready.count('_gk_anim_enter("idle")'), 1)

    def test_transition_to_wrong_node_warns(self):
        """转场连到了非状态节点 -> 告警而不是崩。

        用『动画_判断_移动状态』当靶子(它没有『进入』引脚)——
        实测这种情况曾经的报错是
            KeyError: '不是状态 没有输入引脚 进入'
        也就是**直接崩掉导出**,所以必须挡住并给人话。
        """
        def bad(anim, states, loco):
            # 靶子用**另一个转场节点**(有『上游』这个执行流输入引脚,
            # 但它显然不是『动画_状态』)—— 模拟"用户连错了对象"
            wrong = anim.new_node(NN.GKNodeAnimTransition, "不是状态")
            t2 = anim.new_node(NN.GKNodeAnimTransition, "T坏")
            gk_stub.connect(anim, states["run"], "转到", t2, "上游")
            gk_stub.connect(anim, t2, "转到", wrong, "上游")

        _code, warnings = self._build([("idle", "在移动", "run")], extra=bad)
        self.assertTrue(any("不是『动画_状态』" in w for w in warnings),
                        warnings)

    def test_transition_from_wrong_node_warns(self):
        """转场的『上游』接到了非状态节点 -> 说清接的是什么。

        用户会觉得"我明明接了线啊" —— 所以不能只说"不知道从哪切过来"。
        """
        def bad(anim, states, loco):
            t2 = anim.new_node(NN.GKNodeAnimTransition, "T坏上游")
            gk_stub.connect(anim, loco, "在地面", t2, "上游")
            gk_stub.connect(anim, t2, "转到", states["run"], "进入")
            gk_stub.connect(anim, loco, "在移动", t2, "条件")

        _code, warnings = self._build([("idle", "在移动", "run")], extra=bad)
        self.assertTrue(any("『上游』接的是" in w for w in warnings), warnings)

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


class TestPlayAnimParams(Base):
    """照 UPBGE『Play Animation』补齐的几项:起始/结束帧、Blend-In、混合模式、层。

    全部以 0 / -1 / REPLACE 为"不生效" —— **老图生成的代码必须一字不变**,
    这是这套参数能安全加进来的前提,用测试钉死。
    """

    def _build(self, **props):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        play = tree.new_node(NN.GKNodePlayAnim, "播放",
                             gk_anim_name="run", **props)
        gk_stub.connect(tree, event, "执行", play, "执行")
        return gen(tree, V3)

    def test_defaults_leave_code_unchanged(self):
        code, warnings = self._build()
        self.assertEqual(warnings, [], warnings)
        self.assertIn(':= "run"', code)
        self.assertIn(".play(", code)
        self.assertIn(", -1.0, 1.0)", code)
        self.assertNotIn("play_section(", code)
        self.assertNotIn(".seek(", code)
        self.assertNotIn("_gk_layer_player(", code)

    def test_start_frame_uses_play_then_seek(self):
        """只设起始帧 -> play() 之后 seek() 到起点(帧按场景帧率换算成秒)。

        桩环境没有 scene,`_scene_fps()` 退回 Blender 默认的 24 -> 48/24=2 秒。
        """
        code, _ = self._build(gk_start_frame=48.0)
        self.assertIn(", -1.0, 1.0)", code)
        self.assertIn(".seek(2.0, true)", code)
        self.assertNotIn("play_section(", code)

    def test_end_frame_uses_play_section(self):
        """设了结束帧 -> 起止/混合/速度一次交给 play_section(4.3+ 的 API)。"""
        code, _ = self._build(gk_start_frame=24.0, gk_end_frame=72.0)
        self.assertIn("play_section(", code)
        self.assertIn(", 1.0, 3.0, -1.0, 1.0)", code)
        self.assertNotIn(".seek(", code)

    def test_blend_in_maps_to_custom_blend(self):
        code, _ = self._build(gk_blend_in=0.25)
        self.assertIn(".play(", code)
        self.assertIn(", 0.25, 1.0)", code)

    def test_continue_mode_does_not_restart(self):
        """『续播』= 同名动画正在播就跳过,避免连续触发时鬼畜重播。"""
        code, _ = self._build(gk_blend_mode="CONTINUE")
        self.assertIn("current_animation != ", code)
        self.assertIn(".is_playing()", code)
        plain, _ = self._build()
        self.assertNotIn("current_animation != ", plain)

    def test_layer_uses_second_player(self):
        """层 ≥ 1 -> 另起一个播放器(复制原播放器当"上层")。"""
        code, warnings = self._build(gk_layer=1)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_layer_player(", code)
        self.assertIn("func _gk_layer_player(base: Node, layer: int) -> Node:", code)

    def test_layer_zero_generates_no_helper(self):
        code, _ = self._build()
        self.assertNotIn("func _gk_layer_player(", code)


class TestAnimCategory(Base):
    """动作分类标签:存进 Action 自定义属性 + 导出成常量表。"""

    def test_round_trip_on_dict_like_action(self):
        class FakeAction(dict):
            name = "砍"

        action = FakeAction()
        self.assertEqual(NN.action_category(action), "")
        NN.set_action_category(action, "攻击")
        self.assertEqual(action[NN.ANIM_CATEGORY_KEY], "攻击")
        self.assertEqual(NN.action_category(action), "攻击")
        NN.set_action_category(action, "")
        self.assertNotIn(NN.ANIM_CATEGORY_KEY, action)

    def test_missing_action_is_safe(self):
        NN.set_action_category(None, "攻击")
        self.assertEqual(NN.action_category(None), "")

    def test_table_empty_without_bpy_data(self):
        self.assertEqual(codegen._anim_category_table(), {})

    def test_no_constant_when_nothing_tagged(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        play = tree.new_node(NN.GKNodePlayAnim, "播放", gk_anim_name="run")
        gk_stub.connect(tree, event, "执行", play, "执行")
        code, _ = gen(tree, V3)
        self.assertNotIn("GK_ANIM_BY_CATEGORY", code)

    def _tagged_actions(self):
        class FakeAction(dict):
            def __init__(self, name):
                super().__init__()
                self.name = name

        attack = FakeAction("砍")
        walk = FakeAction("走")
        NN.set_action_category(attack, "攻击")
        NN.set_action_category(walk, "移动")
        return walk, attack

    def test_constant_table_in_generated_code(self):
        """打了标签 -> 导出生成按分类索引的常量表,Godot 侧能按分类挑动作。"""
        import types
        walk, attack = self._tagged_actions()
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        play = tree.new_node(NN.GKNodePlayAnim, "播放", gk_anim_name="砍")
        gk_stub.connect(tree, event, "执行", play, "执行")

        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data",
                types.SimpleNamespace(actions=[walk, attack]))
        try:
            code, warnings = gen(tree, V3)
        finally:
            if original is None:
                try:
                    delattr(bpy_module(), "data")
                except AttributeError:
                    pass
            else:
                setattr(bpy_module(), "data", original)

        self.assertEqual(warnings, [], warnings)
        self.assertIn("const GK_ANIM_BY_CATEGORY := {", code)
        self.assertIn('"攻击": ["砍"],', code)
        # 分类顺序照 ANIM_CATEGORY_NAMES(移动在攻击前面)
        self.assertIn('"移动": ["走"],', code)


class TestLogicNodeMultiInput(Base):
    """『逻辑_布尔运算』的结构优化(用户要求:"逻辑是否、或者等")。

    以前 A、B 两个输入是写死的 —— 用户在节点上把数量调大,
    多出来的引脚只是**画着好看**,生成的代码里被完全忽略。
    """

    def _logic_tree(self, op, count, wired):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        branch = tree.new_node(NN.GKNodeBranch, "分支")
        logic = tree.new_node(NN.GKNodeLogic, "逻辑", gk_op=op,
                              gk_input_count=count)
        for index in range(wired):
            compare = tree.new_node(NN.GKNodeCompare, f"比较{index}",
                                    gk_op="GT", gk_a=str(index + 2), gk_b="1")
            gk_stub.connect(tree, compare, "结果", logic, chr(ord("A") + index))
        gk_stub.connect(tree, logic, "结果", branch, "条件")
        gk_stub.connect(tree, event, "执行", branch, "执行")
        return tree

    def test_and_with_three_inputs_uses_all(self):
        code, warnings = gen(self._logic_tree("AND", 3, 3), V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("(2 > 1) and (3 > 1) and (4 > 1)", code)

    def test_or_with_three_inputs(self):
        code, _ = gen(self._logic_tree("OR", 3, 3), V2)
        self.assertIn("(2 > 1) or (3 > 1) or (4 > 1)", code)

    def test_not_only_has_one_input(self):
        """"非"只用一个输入 —— 画出 B 会让人以为它有用。"""
        node = make_tree().new_node(NN.GKNodeLogic, "逻辑", gk_op="NOT")
        self.assertEqual([socket.name for s in node.inputs for socket in [s]],
                         ["A"])

    def test_xor_two_inputs(self):
        code, _ = gen(self._logic_tree("XOR", 2, 2), V2)
        self.assertIn("((2 > 1) != (3 > 1))", code)

    def test_and_warns_about_unwired_input(self):
        """『与』的空输入按假算 —— 结果恒为假,必须明确告警。

        这是最难查的一类错:图看起来连好了,游戏里条件永远不成立。
        """
        _code, warnings = gen(self._logic_tree("AND", 3, 2), V2)
        self.assertTrue(any("没接线" in w for w in warnings), warnings)

    def test_logic_nodes_are_available_in_animation_tree(self):
        """布尔运算/比较必须能在**动画树**里拉出来(转场条件要用)。

        两张图之间不能连线 —— 只在玩法图里能用 = 动画转场条件做不了组合判断。
        """
        from BlenderGameKit import properties as props
        for tree_type in ("GKGameLogicTree", "GKAnimationTree"):
            listed = {cls.bl_idname
                      for _cat, classes in props.node_menu_entries(tree_type)
                      for cls in classes}
            for bl_idname in ("GKNodeLogic", "GKNodeCompare"):
                self.assertIn(bl_idname, listed, f"{bl_idname} 不在 {tree_type} 菜单里")


class TestAnimTreeVariables(Base):
    """动画树的自定义变量:侧栏声明 -> 取/设节点 -> 生成的成员变量。

    用户要求:"在侧边栏可以添加变量，自定义变量引脚连线"。
    """

    def _anim_with_vars(self, specs):
        """造一张带变量表的动画树(等价于侧栏里加了几个变量)。"""
        import types
        anim = gk_stub.StubNodeTree("角色动画")
        anim.bl_idname = "GKAnimationTree"
        anim.gk_variables = [
            types.SimpleNamespace(var_name=name, var_type=kind,
                                  default_value=value)
            for name, kind, value in specs
        ]
        return anim

    def _with_bpy(self, anim):
        """把动画树挂到假 bpy 上,返回一个"还原"函数。"""
        import types
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[anim]))

        def restore():
            if original is None:
                try:
                    delattr(bpy_module(), "data")
                except AttributeError:
                    pass
            else:
                setattr(bpy_module(), "data", original)
        return restore

    def test_declared_variables_land_in_the_script(self):
        """侧栏里写下的变量必须真的出现在脚本里 —— 带类型和初值。"""
        anim = self._anim_with_vars([("hp", "float", "100"),
                                     ("is_ready", "bool", "true"),
                                     ("score", "int", "3")])
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        ref = tree.new_node(NN.GKNodeAnimStateMachineRef, "状态机")
        gk_stub.connect(tree, event, "执行", ref, "执行")
        idle = anim.new_node(NN.GKNodeAnimState, "状态idle")
        idle.gk_state_name = "idle"

        restore = self._with_bpy(anim)
        try:
            code, warnings = gen(tree, V3)
        finally:
            restore()

        self.assertEqual(warnings, [], warnings)
        self.assertIn("var hp: float = 100.0", code)
        self.assertIn("var is_ready: bool = true", code)
        self.assertIn("var score: int = 3", code)

    def test_get_var_node_works_as_transition_condition(self):
        """把『动画_取变量』接到转场条件上 —— 变量直接当状态切换条件。"""
        anim = self._anim_with_vars([("airspeed", "float", "0")])
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        ref = tree.new_node(NN.GKNodeAnimStateMachineRef, "状态机")
        gk_stub.connect(tree, event, "执行", ref, "执行")
        idle = anim.new_node(NN.GKNodeAnimState, "状态idle")
        idle.gk_state_name = "idle"
        run = anim.new_node(NN.GKNodeAnimState, "状态run")
        run.gk_state_name = "run"
        transition = anim.new_node(NN.GKNodeAnimTransition, "T")
        getter = anim.new_node(NN.GKNodeAnimGetVar, "取变量", gk_var_name="airspeed")
        gk_stub.connect(anim, idle, "转到", transition, "上游")
        gk_stub.connect(anim, transition, "转到", run, "进入")
        gk_stub.connect(anim, getter, "值", transition, "条件")

        restore = self._with_bpy(anim)
        try:
            code, warnings = gen(tree, V3)
        finally:
            restore()

        self.assertEqual(warnings, [], warnings)
        self.assertIn("airspeed", code)

    def test_set_var_node_assigns_with_declared_type(self):
        """『动画_设变量』生成一行赋值,变量类型/初值来自侧栏声明。"""
        anim = self._anim_with_vars([("is_ready", "bool", "1")])
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        setter = tree.new_node(NN.GKNodeAnimSetVar, "设变量", gk_var_name="is_ready")
        setter.id_data = anim
        source = tree.new_node(NN.GKNodeCompare, "比较",
                               gk_op="GT", gk_a="2", gk_b="1")
        gk_stub.connect(tree, source, "结果", setter, "值")
        gk_stub.connect(tree, event, "执行", setter, "执行")

        code, warnings = gen(tree, V2)

        self.assertEqual(warnings, [], warnings)
        self.assertIn("var is_ready: bool = true", code)
        self.assertIn("is_ready = (2 > 1)", code)

    def test_chinese_variable_name_kept(self):
        """中文变量名**原样保留**,不再压成 `v`(第 22 轮实测修正)。

        以前认为"GDScript 不认中文",于是把中文压成下划线 ——
        结果『金币数』和『通关』**撞成同一个名字**,生成
        `var v: float = false`(类型和值都对不上,Godot 直接解析失败)。
        实测 Godot 4.7.2 **接受 Unicode 标识符**,所以现在原样保留。
        """
        anim = self._anim_with_vars([("无敌", "bool", "真")])
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        ref = tree.new_node(NN.GKNodeAnimStateMachineRef, "状态机")
        gk_stub.connect(tree, event, "执行", ref, "执行")
        idle = anim.new_node(NN.GKNodeAnimState, "状态idle")
        idle.gk_state_name = "idle"

        restore = self._with_bpy(anim)
        try:
            code, warnings = gen(tree, V3)
        finally:
            restore()

        self.assertIn("var 无敌: bool = true", code)

    def test_var_node_without_name_warns(self):
        """没选变量的取变量节点要告警,而不是生成一个空名字。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        printer = tree.new_node(NN.GKNodePrint, "打印")
        getter = tree.new_node(NN.GKNodeAnimGetVar, "取变量")
        gk_stub.connect(tree, getter, "值", printer, "内容")
        gk_stub.connect(tree, event, "执行", printer, "执行")
        _code, warnings = gen(tree, V2)
        self.assertTrue(any("还没选变量" in w for w in warnings), warnings)


def nn_base_statement():
    from BlenderGameKit.nodes import node_base as NB
    return NB.GKStatementNode


if __name__ == "__main__":
    unittest.main(verbosity=2)
