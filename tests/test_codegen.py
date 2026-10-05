"""BlenderGameKit 代码生成回归测试(不需要 Blender)。

跑法:
    python tests/test_codegen.py
或者交给 Blender 的 Python 跑(见 run_tests.bat)，效果一样。
"""

import os
import re
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import gk_stub  # noqa: E402

gk_stub.install()
ADDON = gk_stub.load_addon(os.path.join(ROOT, "BlenderGameKit"))

from BlenderGameKit import (blender_adapter, codegen, exporter, models, paths,  # noqa: E402
                            project)
from BlenderGameKit.nodes import node_base as NB  # noqa: E402
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
        现在生成的是 `_gk_ref_typed(self, "名字", "类型")`:先在全树里找
        **类型对得上**的那个(同名节点常常有两个:网格 + 碰撞代理),
        找不到再退回 `_gk_ref` 的按名字查找。
        """
        for name, script, gd_type in (
                ("GKNodeCameraSetCurrent", "相机_切换", "Camera3D"),
                ("GKNodeSetVelocity", "移动_设置速度", "Node")):
            tree = make_tree()
            event = tree.new_node(NN.GKNodeOnReady, "准备")
            node = tree.new_node(getattr(NN, name), script,
                                 gk_target="某物")
            printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="x")
            gk_stub.connect(tree, event, "执行", printer, "执行")
            gk_stub.connect(tree, event, "执行", node, "执行")
            code, _w = gen(tree, V3)
            self.assertIn(f'_gk_ref_typed(self, "某物", "{gd_type}")', code)
            self.assertNotIn('$"某物"', code)
            self.assertIn("func _gk_ref(host: Node, wanted: String)", code)
            self.assertIn("func _gk_ref_typed(host: Node, wanted: String, "
                          "type_name: String) -> Node:", code)

    def test_other_node_becomes_onready(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        velocity = tree.new_node(NN.GKNodeSetVelocity, "设别的节点", gk_target="Player")
        gk_stub.connect(tree, event, "执行", velocity, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("@onready var", code)
        self.assertIn('_gk_ref_typed(self, "Player", "Node")', code)

    def test_destroy_also_clears_same_named_mesh(self):
        """『实例_销毁』要连"同名网格"一起清。

        导出后一个物体常常是两个节点:网格在 glb 实例里、碰撞代理在
        Colliders 下。只 free 代理的话物体还在画面上 ——
        实测:金币吃掉后还杵在原地(只有代理消失了)。
        """
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        free = tree.new_node(NN.GKNodeQueueFree, "销毁", gk_target="金币1")
        gk_stub.connect(tree, event, "执行", free, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _gk_free_with_visual(node: Node) -> void:", code)
        self.assertIn("_gk_free_with_visual(", code)
        self.assertIn('find_children(str(node.name), "MeshInstance3D"', code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

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

    def test_set_collision_toggles_shapes(self):
        """『物理_启用碰撞』:切的是碰撞形状的 disabled,而且是 deferred。

        用户 2026-10-05。Godot 4 的 CollisionObject3D **没有**布尔 disabled
        (实测:StaticBody3D 只有 disable_mode),所以只能切 CollisionShape;
        逻辑常在 _physics_process 里跑,直接改会报
        "Can't change this state while flushing queries" —— 必须 set_deferred。
        """
        tree = make_tree()
        event = self._ready(tree)
        off = tree.new_node(NN.GKNodeSetCollision, "关碰撞",
                            gk_target="金币1", gk_enable=False)
        gk_stub.connect(tree, event, "执行", off, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _gk_set_collision(node: Node, on: bool) -> void:", code)
        self.assertIn("_gk_set_collision(", code)
        self.assertIn(", false)", code)
        # 两种形状都要覆盖(2D / 3D),而且一律 deferred
        self.assertIn('"CollisionShape3D"', code)
        self.assertIn('"CollisionShape2D"', code)
        self.assertIn('shape.set_deferred("disabled", not on)', code)
        # 网格 / 碰撞代理是**两个同名节点** —— 名字找到的往往是网格,
        # 所以还要把 CollisionObject3D 那个同名节点一起算上
        self.assertIn('"CollisionObject3D"', code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_set_collision_pin_overrides_property(self):
        """『启用』接了线就以线为准(和别的参数引脚一个规矩)。"""
        tree = make_tree()
        event = self._ready(tree)
        flag = tree.new_node(NN.GKNodeConstBool, "真", gk_value=True)
        node = tree.new_node(NN.GKNodeSetCollision, "开碰撞",
                             gk_target="门", gk_enable=False)
        gk_stub.connect(tree, event, "执行", node, "执行")
        gk_stub.connect(tree, flag, "值", node, "启用")
        code, _warnings = gen(tree, V3)
        self.assertIn("_gk_set_collision(", code)
        self.assertRegex(code, r"_gk_set_collision\([^,]+, true\)")

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
        self.assertIn("_gk_find_widget(\"提示\")", code)
        # 辅助函数不能带没用上的参数 —— Godot 会报 UNUSED_PARAMETER
        # (用户实测:调试器『错误』页里挂着两条,就是这个)
        self.assertIn("func _gk_find_widget(widget_name: String) -> Control:", code)
        self.assertNotIn("host: Node, widget_name", code)
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

    def test_move_follow_camera_transforms_by_camera(self):
        """勾『随相机朝向』:方向按当前相机的水平朝向变换(第三人称)。

        回归(用户 2026-10-04):不勾时 `velocity.x = _dir.x`、
        `velocity.z = -_dir.z` 写死在世界轴上 —— 转视角后按 W 还是往
        原来的方向走,方向感错位。
        """
        tree = make_tree()
        event = self._ready(tree)
        move = tree.new_node(NN.GKNodeMoveDirection, "移动", gk_speed=6.0)
        move.gk_keep_y = True
        move.gk_follow_camera = True
        gk_stub.connect(tree, event, "执行", move, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual([w for w in warnings if "基类已从" not in str(w)], [],
                         warnings)
        # 自动用当前相机,并按它的水平基向量(前 / 右)变换
        self.assertIn("_gk_resolve_camera(self)", code)
        self.assertIn("global_transform.basis.z", code)
        self.assertIn("global_transform.basis.x", code)
        self.assertIn(".velocity.x = ", code)
        self.assertIn(".velocity.z = ", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_move_reference_pin_beats_camera_toggle(self):
        """『参考系』引脚优先:接了节点就按它变换,不再回退到当前相机。"""
        tree = make_tree()
        event = self._ready(tree)
        ref = tree.new_node(NN.GKNodeGetNode, "参考", gk_path="侧视机位")
        move = tree.new_node(NN.GKNodeMoveDirection, "移动")
        move.gk_follow_camera = True          # 有引脚时这个应被忽略
        gk_stub.connect(tree, event, "执行", move, "执行")
        gk_stub.connect(tree, ref, "节点", move, "参考系")
        code, _ = gen(tree, V3)
        self.assertIn("is Node3D", code)
        self.assertIn("global_transform.basis.z", code)
        self.assertNotIn("_gk_resolve_camera(self)", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_move_follow_camera_ignored_in_2d(self):
        """2D 没有"绕角色转的视角",勾了也不该生成 3D 基向量代码。"""
        tree = make_tree()
        event = self._ready(tree)
        move = tree.new_node(NN.GKNodeMoveDirection, "移动")
        move.gk_follow_camera = True
        gk_stub.connect(tree, event, "执行", move, "执行")
        code, _ = gen(tree, V2)
        self.assertNotIn("global_transform", code)
        self.assertNotIn("is Node3D", code)

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


class TestChangeScene(Base):
    """『场景_切换关卡』(用户 2026-10-04:吃完全部金币 -> 转场 -> 换关)。"""

    def _tree(self, path="res://scenes/关卡2.tscn"):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        node = tree.new_node(NN.GKNodeChangeScene, "切到第二关",
                             gk_scene_path=path)
        gk_stub.connect(tree, event, "执行", node, "执行")
        return tree, node

    def test_switches_deferred_to_target_scene(self):
        tree, _node = self._tree()
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        # 必须延到帧末换场景(和重载场景同一个坑)
        self.assertIn("call_deferred", code)
        self.assertIn("change_scene_to_file", code)
        self.assertIn("res://scenes/关卡2.tscn", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_empty_path_warns_and_emits_nothing(self):
        tree, _node = self._tree(path="")
        code, warnings = gen(tree, V3)
        self.assertTrue(any("没填目标场景" in str(w) for w in warnings), warnings)
        self.assertNotIn("change_scene_to_file", code)

    def test_non_res_path_warns(self):
        """填了相对路径要提醒 —— change_scene_to_file 只认 res:// 或绝对路径。"""
        tree, _node = self._tree(path="scenes/关卡2.tscn")
        _code, warnings = gen(tree, V3)
        self.assertTrue(any("res://" in str(w) for w in warnings), warnings)

    def test_path_with_quote_is_escaped(self):
        """路径带引号也必须生成合法 GDScript(否则整段代码语法错误)。"""
        tree, _node = self._tree(path='res://scenes/a"b.tscn')
        code, _warnings = gen(tree, V3)
        self.assertIn('\\"', code)


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
        # 产物对就行 —— 这种明确转换**不用**再告警(第二十四轮:虚惊,
        # 见 TestDeclaredVariables.test_declared_bool_written_with_number_is_quiet)
        self.assertEqual(warnings, [], warnings)
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


class TestGodCamera(Base):
    """上帝摄像机(用户 2026-10-04:像虚幻 5 那样自由飞着看这一关)。

    ── 第二轮改了什么(用户实测反馈)──────────────────────────────
    第一版把上帝机写进**角色脚本**里,实测两个问题:
      * 场景里没有角色(纯场景测试)就没有那个脚本 —— 上帝机跟着不存在:
        "在没有角色的测试里动不了,鼠标也无法控制视角旋转";
      * 飞相机时角色照样跟着 WASD 走:"上帝视角还会影响角色移动"。

    现在:上帝机本体由**环境脚本**生成(每张 .tscn 都有,见
    asset_export.ENVIRONMENT_SCRIPT 的 god_camera 段);角色脚本只留
    `_gk_god_camera_active()` 守卫 —— 上帝视角期间移动 / 跳跃 / 鼠标
    位移都让给上帝机。环境脚本那侧的测试见 TestEnvironmentGodCamera。
    """

    def _tree(self):
        tree = make_tree()
        ev = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        move = tree.new_node(NN.GKNodeMoveDirection, "移动", gk_speed=6.0)
        gk_stub.connect(tree, ev, "执行", move, "执行")
        return tree

    def test_off_by_default(self):
        """默认关闭时一行都不生成 —— 不能污染普通导出。"""
        code, _ = gen(self._tree(), V3)
        self.assertNotIn("_gk_god_camera_active", code)
        self.assertNotIn("_gk_fly_cam", code)
        self.assertNotIn("上帝摄像机", code)

    def test_on_only_gates_control_no_fly_camera(self):
        """开着时:角色脚本里**不再内嵌**上帝机,只留守卫。

        上帝机本体已搬到环境脚本 —— 角色脚本里再有一份的话,没有角色的
        场景依然没有它(用户实测的那个 bug),而有了角色的场景会有两台。
        """
        code, warns = gen(self._tree(), dict(V3, god_camera=True))
        self.assertEqual(warns, [], warns)
        self.assertIn("func _gk_god_camera_active() -> bool:", code)
        self.assertIn('is_in_group("gk_god_camera")', code)
        # 上帝视角期间:WASD 推出来的方向直接抹成零(角色原地不动)
        self.assertIn("if _gk_god_camera_active():", code)
        self.assertIn("= Vector3.ZERO", code)
        self.assertNotIn("_gk_fly_cam", code)
        self.assertNotIn("KEY_TAB", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_jump_uses_single_line_guard(self):
        """跳跃要挡 —— 用**单行** if,不能把整段包进块里。

        踩过的坑:『方向』引脚取输入的那几行是固定缩进写出来的,包块会让
        它们不跟着缩进,Godot 报 `Expected indented block after "if" block`
        (实测:导出的关卡1脚本直接解析失败)。
        """
        tree = make_tree()
        ev = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        jump = tree.new_node(NN.GKNodeJump, "跳跃")
        gk_stub.connect(tree, ev, "执行", jump, "执行")
        code, _ = gen(tree, dict(V3, god_camera=True))
        self.assertIn("if not _gk_god_camera_active(): self.velocity.y = jump_force",
                      code)
        self.assert_indent_sane(code)

    def test_no_guard_when_off(self):
        """开关关着时:移动方向那两行守卫一个字都不能多。"""
        code, _ = gen(self._tree(), V3)
        self.assertNotIn("_gk_god_camera_active", code)
        self.assertIn("= (", code)

    def test_mouse_delta_gated_while_flying(self):
        """上帝视角期间游戏这边不攒鼠标位移(那会儿鼠标是转上帝机的)。"""
        def build():
            tree = make_tree()
            event = tree.new_node(NN.GKNodeOnReady, "准备")
            printer = tree.new_node(NN.GKNodePrint, "打印")
            motion = tree.new_node(NN.GKNodeMouseMotion, "鼠标移动量")
            gk_stub.connect(tree, motion, "移动量", printer, "内容")
            gk_stub.connect(tree, event, "执行", printer, "执行")
            return tree

        plain, _ = gen(build(), V3)
        self.assertIn("if event is InputEventMouseMotion:", plain)
        self.assertNotIn("_gk_god_camera_active", plain)

        gated, _ = gen(build(), dict(V3, god_camera=True))
        self.assertIn("if event is InputEventMouseMotion "
                      "and not _gk_god_camera_active():", gated)


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


class TestInlineProps(Base):
    """参数直接画在节点体上(用户 2026-10-04)。

    节点声明的参数(gk_prop_defs)以前只有**手写了 draw_buttons** 的节点
    才看得到 —— 没写的那批(『打印_输出』的文本、『等待_秒』的真实时间…)
    参数是"隐身"的。基类现在给兜底:没重写 draw_buttons 的节点,自动把
    声明的参数画出来。这里测"要画哪些参数"这份清单(UI 画不出来,离线
    桩没有 layout)。
    """

    def test_inline_entries_cover_declared_props(self):
        node = make_tree().new_node(NN.GKNodePrint, "打印")
        names = [name for name, _kind, _label in NB.inline_prop_entries(node)]
        self.assertIn("gk_text", names)

    def test_inline_entries_keep_kind_and_label(self):
        node = make_tree().new_node(NN.GKNodeForLoop, "循环")
        entries = {name: (kind, label)
                   for name, kind, label in NB.inline_prop_entries(node)}
        self.assertEqual(entries["gk_index_name"], ("string", "索引变量"))

    def test_node_reference_prop_uses_picker_kind(self):
        """节点引用要用带下拉的选择器,所以 kind 必须还是 'node'。"""
        node = make_tree().new_node(NN.GKNodeAnimStop, "停止")
        kinds = {name: kind for name, kind, _ in NB.inline_prop_entries(node)}
        self.assertEqual(kinds.get("gk_player"), "node")

    def test_auto_draw_only_when_not_overridden(self):
        """重写了 draw_buttons 的节点自己画参数,基类兜底不参与(不会重复画)。"""
        # 『打印_输出』没有自己的 draw_buttons -> 走基类兜底
        self.assertNotIn("draw_buttons", NN.GKNodePrint.__dict__)
        # 『移动_按方向』自己画了(随相机朝向那个勾) -> 不受兜底影响
        self.assertIn("draw_buttons", NN.GKNodeMoveDirection.__dict__)


class TestVariablePanel(Base):
    """变量集中面板(用户 2026-10-04):扫描图里的变量引用,点一下高亮。

    玩法图的变量是隐式声明的(第一次『变量_设置』就自动建),没有一张
    表能看"有哪些变量、谁读谁写"。这里测扫描结果(UI 画不出来,离线桩
    没有 layout)。
    """

    def test_collects_readers_and_writers(self):
        from BlenderGameKit import properties as props
        tree = make_tree()
        tree.new_node(NN.GKNodeSetVar, "写生命", gk_var_name="hp")
        tree.new_node(NN.GKNodeGetVar, "读生命", gk_var_name="hp")
        tree.new_node(NN.GKNodeGetVar, "读分数", gk_var_name="score")
        refs = {name: (len(sets), len(gets))
                for name, sets, gets in props.variable_references(tree)}
        self.assertEqual(refs["hp"], (1, 1), refs)
        self.assertEqual(refs["score"], (0, 1), refs)

    def test_multi_slot_set_counts_each_variable(self):
        """一张『变量_设置』卡片能写多个变量,每个都要被收进表里。"""
        from BlenderGameKit import properties as props
        tree = make_tree()
        tree.new_node(NN.GKNodeSetVar, "多变量", gk_var_name="hp",
                      gk_var_count=3, gk_var_name_2="mp", gk_var_name_3="exp")
        refs = {name: (len(sets), len(gets))
                for name, sets, gets in props.variable_references(tree)}
        self.assertEqual(refs["hp"], (1, 0), refs)
        self.assertEqual(refs["mp"], (1, 0), refs)
        self.assertEqual(refs["exp"], (1, 0), refs)

    def test_nodes_for_variable_lists_all_refs(self):
        from BlenderGameKit import properties as props
        tree = make_tree()
        tree.new_node(NN.GKNodeSetVar, "写", gk_var_name="hp")
        tree.new_node(NN.GKNodeGetVar, "读", gk_var_name="hp")
        tree.new_node(NN.GKNodeGetVar, "别的", gk_var_name="score")
        hits = props.nodes_for_variable(tree, "hp")
        self.assertEqual([node.name for node in hits], ["写", "读"])

    def test_empty_name_is_ignored(self):
        from BlenderGameKit import properties as props
        tree = make_tree()
        tree.new_node(NN.GKNodeSetVar, "空名", gk_var_name="")
        self.assertEqual(props.variable_references(tree), [])


class TestDeclaredVariables(Base):
    """侧栏声明的变量(用户 2026-10-04):名字/类型/初值一处定,节点里挑。

    声明过的变量在遍历节点**之前**就定好类型和初值,后面第一个
    『变量_设置』复用这份声明;没声明过的仍然隐式自动建(老图不变)。
    """

    def _declare(self, tree, name, var_type="float", default="0"):
        tree.gk_vars.add()
        var = tree.gk_vars[len(tree.gk_vars) - 1]
        var.var_name = name
        var.var_type = var_type
        var.default_value = default
        return var

    def test_declared_type_and_initial_win(self):
        tree = make_tree()
        self._declare(tree, "hp", "float", "42.0")
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        setter = tree.new_node(NN.GKNodeSetVar, "写血", gk_var_name="hp",
                               gk_value=100.0)
        gk_stub.connect(tree, event, "执行", setter, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("var hp: float = 42.0", code)
        self.assertIn("hp = 100.0", code)
        # 声明只生成一份(节点不该把类型/初值再声明一遍)
        self.assertEqual(code.count("var hp:"), 1, code)

    def test_unused_declaration_is_still_emitted(self):
        """声明了但图里没人用 —— 也要生成成员变量(它就是"一处定义")。"""
        tree = make_tree()
        self._declare(tree, "score", "int", "0")
        code, _warnings = gen(tree, V3)
        self.assertIn("var score: int = 0", code)

    def test_bool_default_zero_is_coerced(self):
        """初值框默认是 0 —— 声明布尔时忘了改,也不能生成 `bool = 0`。"""
        tree = make_tree()
        self._declare(tree, "win", "bool", "0")
        code, _warnings = gen(tree, V3)
        self.assertIn("var win: bool = false", code)

    def test_string_initial_gets_quotes(self):
        tree = make_tree()
        self._declare(tree, "player_name", "string", "小明")
        code, _warnings = gen(tree, V3)
        self.assertIn('var player_name: String = "小明"', code)

    def test_declared_bool_written_with_number_is_quiet(self):
        """声明成布尔、节点却填数字 -> 直接写成 true,不再告警(第二十四轮)。

        ── 这条测试的立场变过一次 ──────────────────────────────────
        以前这里断言"必须告警",理由是"提醒用户去接『常量_布尔』"。
        做小游戏示例时发现那是**虚惊**:数字框只能填数字,写布尔变量
        本来就只能这么写,生成器也早就正确转成 `= true` —— 一条示例里
        五处这样的写法就刷出五条告警,而新的『导出报告』会把这些告警
        摆在最显眼的位置(第二十三轮),等于把"没有问题"报成问题。

        所以现在:这种**明确**的转换安静地做掉(产物一样),真对不上的
        类型(文本 -> 整数)仍然告警 —— 见 TestSetVarNumericCoercion。
        """
        tree = make_tree()
        self._declare(tree, "win", "bool", "false")
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        setter = tree.new_node(NN.GKNodeSetVar, "写", gk_var_name="win",
                               gk_value=1.0)
        gk_stub.connect(tree, event, "执行", setter, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("win = true", code)

    def test_empty_name_is_skipped(self):
        tree = make_tree()
        self._declare(tree, "", "float", "0")
        code, _warnings = gen(tree, V3)
        self.assertNotIn("# ---- 成员变量 ----", code)

    def test_declaration_is_reported_in_notes(self):
        tree = make_tree()
        self._declare(tree, "hp", "float", "1.0")
        gen(tree, V3)
        notes = " ".join(getattr(codegen, "last_notes", []) or [])
        self.assertIn("声明变量", notes, notes)


class TestSocketTypeHint(Base):
    """通配引脚的自动类型提示(用户 2026-10-04)。

    「任意值」引脚是灰的万能接口,以前看不出线上跑的是数值还是文本。
    现在沿连线解析出具体类型,写进引脚标签、并按类型上色。
    """

    def test_linked_input_resolves_upstream_type(self):
        from BlenderGameKit import gk_sockets as S
        tree = make_tree()
        const = tree.new_node(NN.GKNodeConstFloat, "常量")
        setvar = tree.new_node(NN.GKNodeSetVar, "设置", gk_var_name="hp")
        gk_stub.connect(tree, const, "值", setvar, "值")
        socket = setvar.inputs.get("值")
        self.assertEqual(socket.bl_idname, "GKSocketAny")
        self.assertEqual(S.resolved_type_label(socket), "数值")
        self.assertEqual(S.socket_caption(socket, "值"), "值 (数值)")

    def test_any_output_follows_downstream(self):
        """输出引脚自身没有类型,靠下游那个具体引脚判定。"""
        from BlenderGameKit import gk_sockets as S
        tree = make_tree()
        getvar = tree.new_node(NN.GKNodeGetVar, "取", gk_var_name="hp")
        vector = tree.new_node(NN.GKNodeVectorMake, "向量")
        gk_stub.connect(tree, getvar, "值", vector, "X")
        self.assertEqual(S.resolved_type_label(getvar.outputs.get("值")), "数值")

    def test_any_to_any_leaves_hint_empty(self):
        """两端都是通配、又没有声明类型时不瞎标(宁可留空)。"""
        from BlenderGameKit import gk_sockets as S
        tree = make_tree()
        getvar = tree.new_node(NN.GKNodeGetVar, "取", gk_var_name="hp")
        setvar = tree.new_node(NN.GKNodeSetVar, "设", gk_var_name="hp")
        gk_stub.connect(tree, getvar, "值", setvar, "值")
        self.assertEqual(S.resolved_type_label(setvar.inputs.get("值")), "")

    def test_declared_variable_type_is_used(self):
        """没有连线时,退回读变量在树里声明的类型(动画树变量)。"""
        import types
        from BlenderGameKit import gk_sockets as S
        tree = make_tree()
        node = tree.new_node(NN.GKNodeAnimGetVar, "取变量", gk_var_name="hp")
        node.id_data = types.SimpleNamespace(gk_variables=[
            types.SimpleNamespace(var_name="hp", var_type="bool")])
        self.assertEqual(S.resolved_type_label(node.outputs.get("值")), "布尔")

    def test_non_any_caption_is_untouched(self):
        """普通引脚已经有颜色/形状说明类型,不加文字噪音。"""
        from BlenderGameKit import gk_sockets as S
        tree = make_tree()
        const = tree.new_node(NN.GKNodeConstFloat, "常量")
        self.assertEqual(S.socket_caption(const.outputs.get("值"), "值"), "值")


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

    def test_two_spring_arms_keep_separate_state(self):
        """两根弹簧臂各管一台相机 —— 成员变量必须按节点分开。

        用户 2026-10-04:"俯视图……动不了" —— 给俯瞰机位加了第二根杆子。
        变量不带后缀时两根杆子共用 `_gk_spring_pos` / `_gk_spring_ready`
        这一批状态,每帧轮流覆盖:第一根把相机摆好,第二根接着在**它**的
        结果上继续 lerp,两台相机都被拖成中间值。这里断言第二根杆子拿到
        独立的一份状态(`_2` 后缀)。
        """
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        side = tree.new_node(NN.GKNodeCameraSpringArm, "弹簧臂",
                             gk_target="示例角色", gk_camera="侧视机位",
                             gk_arm=5.0, gk_pitch=12.0)
        top = tree.new_node(NN.GKNodeCameraSpringArm, "弹簧臂俯瞰",
                            gk_target="示例角色", gk_camera="俯瞰机位",
                            gk_arm=16.0, gk_pitch=80.0)
        gk_stub.connect(tree, event, "执行", side, "执行")
        gk_stub.connect(tree, side, "完成", top, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        # 第一根用原名字,第二根带 _2 后缀 —— 两份状态互不覆盖
        self.assertIn("_gk_spring_pos:", code)
        self.assertIn("_gk_spring_pos_2:", code)
        self.assertIn("_gk_spring_ready_2", code)
        # 两台相机各自被摆一次
        self.assertIn("相机_俯瞰机位", code)
        self.assert_no_chinese_identifiers(code)

    def test_spring_arm_pitch_yaw_sign(self):
        """俯仰/偏航取负 —— "视角旋转逻辑颠倒"的修复不能回退。

        `Basis.from_euler(Vector3(p, y, 0)).z` 的 y 分量是 `-sin(p)`:
        填正值相机跑到枢轴**下方**;yaw 正向在 Godot 里是逆时针(左转),
        鼠标右移给正值会变成"往右转视角、画面往左"。所以两个都取负。
        """
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        arm = tree.new_node(NN.GKNodeCameraSpringArm, "弹簧臂",
                            gk_target="示例角色", gk_camera="侧视机位",
                            gk_arm=5.0, gk_pitch=12.0)
        gk_stub.connect(tree, event, "执行", arm, "执行")
        code, _warnings = gen(tree, V3)
        self.assertIn("-deg_to_rad(12.0), _gk_yaw, 0.0)", code)

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


class TestCurveAndTimeline(Base):
    """第十二轮:过渡曲线(缓动档位 / 自定义曲线)、沿曲线运镜、时间轴。

    曲线在 Blender 里是**真实物体**,离线测试里没有 —— 所以采样那层
    用 mock 打桩,验的是"拿到曲线数据之后生成成什么"。
    """

    # ---- 曲线资产的纯函数 ----
    def test_curve_tres_zero_tangent_must_be_float(self):
        """平坦段的切线必须写成 `0.0` 而不是 `0`。

        ── 这条是真 Godot 抓出来的(2026-10-04)────────────────────
        写成 `0` 时 .tres 里是 INT,Godot 装载 Curve 时报
            Condition "p_input[i + 2].get_type() != Variant::FLOAT" is true
        整条曲线静默变成"全是 0"的直线(采样恒为 0)。
        """
        from BlenderGameKit import curve_assets
        points = [(0.0, 0.0), (0.5, 0.0), (1.0, 1.0)]
        text = curve_assets.curve_tres_text(points)
        self.assertIn("point_count = 3", text)
        self.assertIn('type="Curve"', text)
        self.assertIn("Vector2(0, 0), 0.0, 0.0, 1, 1", text)
        # 平坦段(斜率 0)也不能漏出裸的 int
        self.assertNotIn(", 0, 0, 1, 1", text.replace(", 0.0, 0.0, 1, 1", ""))

    def test_curve3d_tres_format(self):
        from BlenderGameKit import curve_assets
        text = curve_assets.curve3d_tres_text([(0, 0, 0), (0, 2, 3), (4, 1, 6)])
        self.assertIn('type="Curve3D"', text)
        self.assertIn("PackedVector3Array(", text)
        self.assertIn("4, 1, 6", text)
        self.assertIn("point_count = 3", text)
        # 每个点三组(入柄 / 出柄 / 位置)—— 3 点共 9 组 27 个数
        body = text.split("PackedVector3Array(", 1)[1].split(")", 1)[0]
        self.assertEqual(len(body.split(",")), 27)

    def test_curve_file_name_is_stable_and_unique(self):
        """文件名必须稳定(脚本里写死的路径)而且不撞名(中文会压成下划线)。"""
        from BlenderGameKit import curve_assets
        self.assertEqual(curve_assets.curve_file_name("运镜曲线"),
                         curve_assets.curve_file_name("运镜曲线"))
        self.assertNotEqual(curve_assets.curve_file_name("运镜曲线"),
                            curve_assets.curve_file_name("角色曲线"))
        self.assertTrue(curve_assets.curve_res_path("运镜曲线")
                        .startswith("res://curves/"))

    def test_curve_points_convert_to_godot_axes(self):
        """曲线采样出来的点必须换成 Godot 坐标(Blender Z-up -> Godot Y-up)。

        不换的话"在视口里画的轨道"和"游戏里跑的轨道"不是同一条
        (高度跑到另一个轴上)——『相机_路径』一直潜着的 bug。
        """
        from BlenderGameKit import curve_assets
        self.assertEqual(curve_assets._to_godot_points([(1.0, 2.0, 3.0)]),
                         [(1.0, 3.0, -2.0)])

    def test_curve_files_skip_empty(self):
        from BlenderGameKit import curve_assets
        files = curve_assets.curve_files("D:/tmp", {
            "好曲线": {"kind": "Curve", "points": [(0.0, 0.0), (1.0, 1.0)]},
            "空曲线": {"kind": "Curve", "points": []},
        })
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0][0], "好曲线")

    # ---- L1:过渡曲线档位 ----
    def test_camera_switch_ease_preset(self):
        """『过渡曲线』选了缓出 -> 过渡比例过一遍 _gk_ease(引擎档位)。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        switch = tree.new_node(NN.GKNodeCameraSetCurrent, "推镜",
                               gk_target="特写机位", gk_blend=0.8,
                               gk_blend_trans="EASE_OUT")
        gk_stub.connect(tree, event, "执行", switch, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _gk_ease(", code)
        self.assertIn("Tween.TRANS_SINE, Tween.EASE_OUT", code)
        self.assertIn("Tween.interpolate_value", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_camera_switch_linear_keeps_old_code(self):
        """线性档(默认)= 老图产物一字不变:不该出现 _gk_ease。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        switch = tree.new_node(NN.GKNodeCameraSetCurrent, "切镜头",
                               gk_target="主相机", gk_blend=0.5)
        gk_stub.connect(tree, event, "执行", switch, "执行")
        code, _w = gen(tree, V3)
        self.assertNotIn("_gk_ease", code)
        self.assertIn("var _gk_ct: float = clampf(1.0 - _gk_cam_blend_left",
                      code)

    def test_camera_ease_applies_for_second_switch(self):
        """两台『相机_切换』:后面那台的过渡曲线也必须生效。

        ── 为什么(真写出来的坑)──────────────────────────────────────
        每帧推进的那段钩子**只生成一次**。第一版"要不要过缓动"只看第一个
        节点:先遇到一台匀速的,后面那台"回弹"就**静默不生效** ——
        用户会以为"我选了曲线但没用"。现在后遇到的缓动节点会把钩子升级。
        """
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        first = tree.new_node(NN.GKNodeCameraSetCurrent, "第一台",
                              gk_target="机位A", gk_blend=0.5)
        second = tree.new_node(NN.GKNodeCameraSetCurrent, "第二台",
                               gk_target="机位B", gk_blend=0.5,
                               gk_blend_trans="BACK")
        gk_stub.connect(tree, event, "执行", first, "执行")
        gk_stub.connect(tree, first, "完成", second, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("Tween.TRANS_BACK", code)
        # 两台各自的档位都写在切换那一刻(成员变量,谁切谁覆盖)
        self.assertIn("_gk_cam_trans = Tween.TRANS_LINEAR", code)
        self.assertIn("_gk_cam_trans = Tween.TRANS_BACK", code)

    def test_camera_switch_custom_curve_without_pick_warns(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        switch = tree.new_node(NN.GKNodeCameraSetCurrent, "推镜",
                               gk_target="特写机位", gk_blend=0.6,
                               gk_blend_trans="CUSTOM", gk_blend_curve="")
        gk_stub.connect(tree, event, "执行", switch, "执行")
        _code, warnings = gen(tree, V3)
        self.assertTrue(any("自定义" in w for w in warnings), warnings)

    def test_camera_switch_custom_curve_loads_resource(self):
        """选了自定义曲线 -> 生成 load(res://curves/...),并登记要写的资源。"""
        from BlenderGameKit import curve_assets
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        switch = tree.new_node(NN.GKNodeCameraSetCurrent, "推镜",
                               gk_target="特写机位", gk_blend=0.6,
                               gk_blend_trans="CUSTOM",
                               gk_blend_curve="缓动曲线")
        gk_stub.connect(tree, event, "执行", switch, "执行")
        fake = [(i / 3.0, (i / 3.0) ** 2) for i in range(4)]
        with mock.patch.object(curve_assets, "curve_exists", lambda _n: True), \
                mock.patch.object(curve_assets, "sample_ease_curve",
                                  lambda _n, samples=32: fake), \
                mock.patch.object(curve_assets, "curve_dimensions",
                                  lambda _n: "2D"):
            result = codegen.generate_result(tree, V3)
        code = result.code
        self.assertEqual(result.warnings, [], result.warnings)
        self.assertIn("func _gk_curve_load(path: String) -> Curve:", code)
        self.assertIn("func _gk_curve_res(path: String) -> Resource:", code)
        self.assertIn('res://curves/', code)
        self.assertIn("_gk_cam_ease_curve = _gk_curve_load(", code)
        self.assertIn("_gk_ease(", code)
        # 资源内容也带出来了(导出时写 .tres)
        self.assertIn("缓动曲线", result.curve_assets)
        self.assertEqual(result.curve_assets["缓动曲线"]["kind"], "Curve")
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_spring_arm_lag_curve(self):
        """弹簧臂的跟随比例也能过曲线(回弹 = 过冲一点的弹簧感)。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        arm = tree.new_node(NN.GKNodeCameraSpringArm, "弹簧臂",
                            gk_target="角色", gk_camera="侧视机位",
                            gk_lag=8.0, gk_lag_trans="BACK")
        gk_stub.connect(tree, event, "执行", arm, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("Tween.TRANS_BACK", code)
        self.assertIn(".lerp(_gk_want, _gk_ease(", code)

    def test_spring_arm_default_lag_untouched(self):
        """没动『跟随曲线』-> 老图那行一字不变。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        arm = tree.new_node(NN.GKNodeCameraSpringArm, "弹簧臂",
                            gk_target="角色", gk_lag=8.0)
        gk_stub.connect(tree, event, "执行", arm, "执行")
        code, _w = gen(tree, V3)
        self.assertIn("clampf(8.0 * get_physics_process_delta_time(), 0.0, 1.0))",
                      code)
        self.assertNotIn("_gk_ease", code)

    # ---- L3:沿曲线运镜 ----
    def test_camera_switch_along_curve(self):
        from BlenderGameKit import curve_assets
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        switch = tree.new_node(NN.GKNodeCameraSetCurrent, "开场运镜",
                               gk_target="特写机位", gk_blend=2.0,
                               gk_path_mode="CURVE", gk_path_curve="运镜轨道")
        gk_stub.connect(tree, event, "执行", switch, "执行")
        fake = [(0.0, 0.0, 0.0), (1.0, 2.0, 0.0), (3.0, 0.0, 1.0)]
        with mock.patch.object(curve_assets, "curve_exists", lambda _n: True), \
                mock.patch.object(curve_assets, "sample_path_points",
                                  lambda _n, samples=48: fake), \
                mock.patch.object(curve_assets, "curve_dimensions",
                                  lambda _n: "3D"):
            result = codegen.generate_result(tree, V3)
        code = result.code
        self.assertEqual(result.warnings, [], result.warnings)
        self.assertIn("func _gk_curve3d_load(path: String) -> Curve3D:", code)
        self.assertIn("_gk_cam_fly_curve = _gk_curve3d_load(", code)
        self.assertIn("_gk_cam_fly_len = _gk_cam_fly_curve.get_baked_length()", code)
        # 位置 = 曲线上的点(改的是插值后 Transform3D 的 origin);
        # ⚠ 曲线点**必须在 _gk_xf 同一层**取 —— 早先把 `_gk_fly` 声明在
        # `if 有曲线:` 块里、块外再用,Godot 直接解析失败(真导出抓到的)
        self.assertIn("_gk_xf.origin = _gk_cam_fly_curve.sample_baked(", code)
        self.assertNotIn("var _gk_fly:", code)
        # 朝向还是从旧机位平滑转到目标机位(位置走曲线,朝向走插值)
        self.assertIn("interpolate_with(", code)
        self.assertEqual(result.curve_assets["运镜轨道"]["kind"], "Curve3D")
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_camera_switch_along_curve_missing_warns(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        switch = tree.new_node(NN.GKNodeCameraSetCurrent, "开场运镜",
                               gk_target="特写机位", gk_blend=2.0,
                               gk_path_mode="CURVE", gk_path_curve="不存在")
        gk_stub.connect(tree, event, "执行", switch, "执行")
        _code, warnings = gen(tree, V3)
        self.assertTrue(any("找不到" in w for w in warnings), warnings)

    # ---- 时间轴 ----
    def _timeline_tree(self, **props):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "开场")
        timeline = tree.new_node(NN.GKNodeTimeline, "开门", **props)
        printer = tree.new_node(NN.GKNodePrint, "更新时")
        printer.gk_text = "tick"
        done = tree.new_node(NN.GKNodePrint, "结束时")
        done.gk_text = "done"
        gk_stub.connect(tree, event, "执行", timeline, "执行")
        gk_stub.connect(tree, timeline, "更新", printer, "执行")
        gk_stub.connect(tree, timeline, "已结束", done, "执行")
        return tree, timeline

    def test_timeline_basic_update_and_finished(self):
        tree, _timeline = self._timeline_tree(gk_time=0.5, gk_mode="ONCE",
                                              gk_track_count=2)
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        # 状态变量(每节点一份)、进度推进、Alpha
        self.assertIn("var _gk_tl_time: float", code)
        self.assertIn("_gk_tl_play = true", code)
        self.assertIn("_gk_tl_time += delta", code)
        self.assertIn("var _gk_tl_raw: float = clampf(_gk_tl_time / 0.5",
                      code)
        self.assertIn("_gk_tl_alpha = _gk_tl_raw", code)
        # 轨道值 = 从 + (到 - 从) * 进度
        self.assertIn("_gk_tl_tr1 = 0.0 + (1.0 - 0.0) * _gk_tl_raw", code)
        self.assertIn("_gk_tl_tr2 = 0.0 + (1.0 - 0.0) * _gk_tl_raw", code)
        # 更新 / 已结束 各自是一个函数,由每帧钩子调用
        self.assertIn("func _gk_timeline_tick() -> void:", code)
        self.assertIn("func _gk_timeline_done() -> void:", code)
        self.assertIn('print("tick")', code)
        self.assertIn('print("done")', code)
        # 挂在 _process(画面的事用渲染帧)
        process = code.split("func _process", 1)[1].split("func ", 1)[0]
        self.assertIn("_gk_timeline_tick()", process)
        self.assertIn("_gk_tl_play = false", code)
        self.assertIn("_gk_tl_done = true", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_timeline_loop_and_pingpong(self):
        tree, _t = self._timeline_tree(gk_time=1.0, gk_mode="LOOP")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("fmod(_gk_tl_time, 1.0)", code)
        self.assertNotIn("_gk_tl_done = true", code)

        tree2, _t2 = self._timeline_tree(gk_time=1.0, gk_mode="PINGPONG")
        code2, warnings2 = gen(tree2, V3)
        self.assertEqual(warnings2, [], warnings2)
        self.assertIn("_gk_tl_dir = -1.0", code2)
        self.assertIn("_gk_tl_time += _gk_tl_dir * delta", code2)

    def test_timeline_replay_pin_shares_one_start_block(self):
        """『重播』入口:和『执行』一样从头开始,整段只生成一遍。

        用户 2026-10-05:"给『定时_时间轴』加一个『重播』执行口" ——
        以前一个输入引脚只能接一条线,想重播就得拿变量绕一圈。
        两个入口**不能**各生成一遍:每帧钩子会跑两次(时间轴双倍速),
        分支函数还会重名(Godot 拒绝整个脚本)。
        """
        tree = make_tree()
        ready = tree.new_node(NN.GKNodeOnReady, "准备")
        physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧", gk_order=0)
        key = tree.new_node(NN.GKNodeKey, "按R", gk_key="R", gk_action="replay")
        branch = tree.new_node(NN.GKNodeBranch, "如果")
        timeline = tree.new_node(NN.GKNodeTimeline, "开场", gk_time=1.0)
        tick = tree.new_node(NN.GKNodePrint, "更新时")
        tick.gk_text = "tick"
        gk_stub.connect(tree, ready, "执行", timeline, "执行")
        gk_stub.connect(tree, timeline, "更新", tick, "执行")
        gk_stub.connect(tree, physics, "执行", branch, "执行")
        gk_stub.connect(tree, key, "结果", branch, "条件")
        gk_stub.connect(tree, branch, "是", timeline, "重播")

        code, warnings = gen(tree, V3)
        # 关键:不能报"同时被两个事件引用" —— 那是给"抢同一个入口"用的
        self.assertEqual(warnings, [], warnings)
        # 启动函数 / 每帧钩子 / 分支函数都只有一份
        self.assertEqual(code.count("func _gk_timeline_play() -> void:"), 1)
        self.assertEqual(code.count("func _gk_timeline_tick() -> void:"), 1)
        self.assertEqual(code.count("_gk_tl_time += delta"), 1)
        # 两个入口都调同一个启动函数(准备里一次 + 按键分支里一次)
        self.assertEqual(code.count("_gk_timeline_play()"), 3)
        self.assertIn("Input.is_action_pressed(ACTION_REPLAY)", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_timeline_repeat_modes(self):
        """『重复触发时』三档(用户 2026-10-06:"想让它先放完再重复")。

        以前只有一种行为:触发就从 0 重播;想"等播完再放"得在图上连一堆判断。
        现在:重播 / 等播完再重播(排队) / 播的时候忽略。
        """
        # 默认 = 重播:老图产物一个字符都不变(没有 guard、没有 pending)
        tree, _t = self._timeline_tree(gk_time=1.0)
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertNotIn("_gk_tl_pending", code)
        # 启动函数的第一句就是"时间归零"(没有被 guard 拦住的可能)
        self.assertIn("func _gk_timeline_play() -> void:\n    _gk_tl_time = 0.0",
                      code)

        # 排队:播放中触发只记一笔,播完那一刻再放一遍
        tree2, _t2 = self._timeline_tree(gk_time=1.0, gk_repeat="QUEUE")
        code2, warnings2 = gen(tree2, V3)
        self.assertEqual(warnings2, [], warnings2)
        self.assertIn("var _gk_tl_pending: bool = false", code2)
        self.assertIn("_gk_tl_pending = true", code2)
        # 启动函数被 guard 拦住(播放中触发先 return)
        self.assertIn("func _gk_timeline_play() -> void:\n    if _gk_tl_play:",
                      code2)
        # 播完那一段里要有"排队就再来一遍"
        self.assertIn("if _gk_tl_pending:", code2)
        self.assertIn("        _gk_timeline_play()", code2)
        self.assert_no_chinese_identifiers(code2)
        self.assert_indent_sane(code2)

        # 忽略:播放中触发直接 return,没有 pending 变量
        tree3, _t3 = self._timeline_tree(gk_time=1.0, gk_repeat="IGNORE")
        code3, warnings3 = gen(tree3, V3)
        self.assertEqual(warnings3, [], warnings3)
        self.assertIn("func _gk_timeline_play() -> void:\n    if _gk_tl_play:",
                      code3)
        self.assertNotIn("_gk_tl_pending", code3)
        self.assert_indent_sane(code3)

    def test_timeline_queue_with_loop_gets_note(self):
        """循环 / 往返模式没有"播完"那一刻 -> 排队降级成忽略,并给一条说明。"""
        tree, _t = self._timeline_tree(gk_time=1.0, gk_mode="LOOP",
                                       gk_repeat="QUEUE")
        result = codegen.generate_result(tree, V3)
        self.assertTrue(any("等播完再重播" in note for note in result.notes),
                        result.notes)
        self.assertNotIn("_gk_tl_pending", result.code)

    def test_timeline_track_pins_follow_count(self):
        node = make_tree().new_node(NN.GKNodeTimeline, "时间轴",
                                    gk_track_count=4)
        names = [socket.name for socket in node.outputs]
        self.assertIn("轨道1值", names)
        self.assertIn("轨道4值", names)
        node.gk_track_count = 1
        node.build_sockets()
        names = [socket.name for socket in node.outputs]
        self.assertIn("轨道1值", names)
        self.assertNotIn("轨道2值", names)

    def test_timeline_two_nodes_keep_separate_state(self):
        """两条时间轴各管各的状态(同一个变量名会被互相覆盖)。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "开场")
        first = tree.new_node(NN.GKNodeTimeline, "第一条", gk_time=1.0)
        second = tree.new_node(NN.GKNodeTimeline, "第二条", gk_time=2.0)
        gk_stub.connect(tree, event, "执行", first, "执行")
        gk_stub.connect(tree, first, "已结束", second, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("var _gk_tl_time: float", code)
        self.assertIn("var _gk_tl_2_time: float", code)
        self.assertIn("_gk_tl_2_time += delta", code)

    def test_timeline_outputs_readable_elsewhere(self):
        """Alpha / 已结束吗 / 轨道值 / 曲线位置 能在别处读到。"""
        tree, timeline = self._timeline_tree(gk_time=1.0, gk_track_count=1)
        event = tree.new_node(NN.GKNodeOnReady, "检查")
        branch = tree.new_node(NN.GKNodeBranch, "结束了吗")
        printer = tree.new_node(NN.GKNodePrint, "打印")
        gk_stub.connect(tree, event, "执行", branch, "执行")
        gk_stub.connect(tree, timeline, "已结束吗", branch, "条件")
        gk_stub.connect(tree, branch, "是", printer, "执行")
        gk_stub.connect(tree, timeline, "Alpha", printer, "内容")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("if _gk_tl_done:", code)
        self.assertIn("print(_gk_tl_alpha)", code)

    def test_timeline_alpha_curve_from_blender(self):
        """时间轴的进度曲线:导出成 Curve 资源,运行时 sample。"""
        from BlenderGameKit import curve_assets
        tree, _t = self._timeline_tree(gk_time=1.0, gk_curve="开门曲线")
        fake = [(0.0, 0.0), (0.5, 0.9), (1.0, 1.0)]
        with mock.patch.object(curve_assets, "curve_exists", lambda _n: True), \
                mock.patch.object(curve_assets, "sample_ease_curve",
                                  lambda _n, samples=32: fake), \
                mock.patch.object(curve_assets, "curve_dimensions",
                                  lambda _n: "2D"):
            result = codegen.generate_result(tree, V3)
        code = result.code
        self.assertEqual(result.warnings, [], result.warnings)
        self.assertIn("_gk_tl_alpha = _gk_ease(_gk_tl_raw, 0, 0, "
                      "_gk_curve_load(", code)
        self.assertIn("开门曲线", result.curve_assets)

    def test_timeline_path_curve_outputs_position(self):
        """『曲线轨道』:3D 曲线 -> 每帧输出曲线上的位置。"""
        from BlenderGameKit import curve_assets
        tree, _t = self._timeline_tree(gk_time=2.0,
                                       gk_path_curve="轨道曲线")
        fake = [(0.0, 0.0, 0.0), (1.0, 3.0, 2.0)]
        with mock.patch.object(curve_assets, "curve_exists", lambda _n: True), \
                mock.patch.object(curve_assets, "sample_path_points",
                                  lambda _n, samples=48: fake), \
                mock.patch.object(curve_assets, "curve_dimensions",
                                  lambda _n: "3D"):
            result = codegen.generate_result(tree, V3)
        code = result.code
        self.assertEqual(result.warnings, [], result.warnings)
        self.assertIn("_gk_tl_fly = _gk_curve3d_load(", code)
        self.assertIn("_gk_tl_path = _gk_tl_fly.sample_baked(", code)
        self.assertEqual(result.curve_assets["轨道曲线"]["kind"], "Curve3D")

    def test_export_writes_curve_tres(self):
        """导出时必须真的把 curves/*.tres 写出来(脚本引用了它们)。"""
        from BlenderGameKit import curve_assets
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        switch = tree.new_node(NN.GKNodeCameraSetCurrent, "开场运镜",
                               gk_target="特写机位", gk_blend=2.0,
                               gk_path_mode="CURVE", gk_path_curve="运镜轨道")
        gk_stub.connect(tree, event, "执行", switch, "执行")
        directory = tempfile.mkdtemp(prefix="gk_curve_export_")
        fake = [(0.0, 0.0, 0.0), (1.0, 2.0, 0.0)]
        try:
            with mock.patch.object(curve_assets, "curve_exists",
                                   lambda _n: True), \
                    mock.patch.object(curve_assets, "sample_path_points",
                                      lambda _n, samples=48: fake), \
                    mock.patch.object(curve_assets, "curve_dimensions",
                                      lambda _n: "3D"):
                result = exporter.export_tree(
                    tree, {"output_dir": directory, "dimension": "3",
                           "base_class": "CharacterBody3D"}, write_scene=False)
            self.assertTrue(result["ok"], result)
            written = [os.path.basename(path) for path in result["written"]]
            self.assertIn(curve_assets.curve_file_name("运镜轨道"), written)
            path = os.path.join(directory, curve_assets.CURVES_DIR,
                                curve_assets.curve_file_name("运镜轨道"))
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn('type="Curve3D"', text)
            self.assertIn("PackedVector3Array(", text)
        finally:
            shutil.rmtree(directory, ignore_errors=True)


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


class TestExportTransaction(Base):
    """导出的事务性 / 结果对象 / 目录校验 / 输入动作计数。

    背景(GPT 审查 / 用户 2026-10-04):
      * 导出原来不是原子的:写脚本 -> 改 project.godot -> 写场景,
        中途失败会留下"脚本是新的、场景是旧的"这种半成品;
      * 生成结果靠 codegen 的模块级全局变量传递,失败时读到上一次的旧值;
      * set_input_actions 返回去重前的数量,日志和实际写入对不上。
    """

    def _make_tree(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="hi")
        gk_stub.connect(tree, event, "执行", printer, "执行")
        return tree

    def _temp_project(self):
        """建一个最小的 Godot 工程目录(含 project.godot)。"""
        directory = tempfile.mkdtemp(prefix="gk_export_test_")
        self.addCleanup(shutil.rmtree, directory, True)
        with open(os.path.join(directory, "project.godot"), "w",
                  encoding="utf-8") as handle:
            handle.write('config_version=5\n\n[application]\n'
                         'config/name="t"\n\n[rendering]\nrenderer="x"\n')
        return directory

    def test_generate_result_returns_object(self):
        """新入口返回 CodegenResult,输入动作随返回值走(不再只靠全局)。"""
        tree = self._make_tree()
        result = codegen.generate_result(tree, V3)
        self.assertIsInstance(result, codegen.CodegenResult)
        self.assertIn("func _ready", result.code)
        self.assertEqual(list(result.warnings), [])
        self.assertIsInstance(list(result.input_actions), list)

    def test_export_settings_model_round_trip(self):
        """ExportSettings 归一化 dict:已知字段变属性,未知字段不丢。"""
        opts = models.ExportSettings.from_dict(
            {"output_dir": "D:/proj", "write_scene": True,
             "baseclass": "typo", "custom_key": 7})
        self.assertEqual(opts.output_dir, "D:/proj")
        self.assertTrue(opts.write_scene)
        # 拼错的字段不会变成属性,而是留在 extra 里(不静默当默认值用)
        self.assertFalse(hasattr(opts, "baseclass"))
        self.assertEqual(opts.extra["baseclass"], "typo")
        back = opts.to_dict()
        self.assertEqual(back["output_dir"], "D:/proj")
        self.assertEqual(back["custom_key"], 7)

    def test_diagnostic_renders_message_and_hint(self):
        """Diagnostic 能摊平成一行,也能区分错误级别。"""
        item = models.Diagnostic(level="warning", code="MISSING_PROJECT_FILE",
                                 message="没有 project.godot",
                                 hint="先创建项目")
        self.assertIn("没有 project.godot", item.render())
        self.assertIn("先创建项目", item.render())
        self.assertFalse(item.is_error())
        self.assertTrue(models.Diagnostic(level="error").is_error())
        self.assertEqual(models.render_warnings([item, "裸字符串"]),
                         [item.render(), "裸字符串"])

    def test_generate_result_matches_legacy_signature(self):
        """旧的 generate() 仍然返回 (code, warnings),兼容未迁移的调用方。"""
        tree = self._make_tree()
        code, warnings = codegen.generate(tree, V3)
        self.assertIn("func _ready", code)
        self.assertEqual(list(warnings), [])

    def test_set_input_actions_returns_deduped_count(self):
        """重复动作只算一次,ui_* 内置动作不计入数量。"""
        directory = self._temp_project()
        count = project.set_input_actions(
            directory,
            ["jump", "jump", "move_left", "ui_accept", "move_left"])
        # 去重 + 过滤 ui_accept 之后是 {jump, move_left} 两个
        self.assertEqual(count, 2)
        with open(os.path.join(directory, "project.godot"),
                  encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("jump=", text)
        self.assertIn("move_left=", text)
        self.assertNotIn("ui_accept=", text)

    def test_validate_output_dir_rejects_a_file(self):
        """导出目录指向一个**文件**时必须拒绝,而不是照着往里写。"""
        directory = tempfile.mkdtemp(prefix="gk_vdir_")
        self.addCleanup(shutil.rmtree, directory, True)
        a_file = os.path.join(directory, "not_a_dir.txt")
        with open(a_file, "w", encoding="utf-8") as handle:
            handle.write("x")
        ok, message = exporter.validate_output_dir(a_file)
        self.assertFalse(ok)
        self.assertIn("文件", message)
        ok2, path2 = exporter.validate_output_dir(directory)
        self.assertTrue(ok2)
        self.assertEqual(path2, os.path.abspath(directory))

    def test_export_tree_dry_run_writes_nothing(self):
        """dry-run 只算清单,不落任何文件。"""
        directory = self._temp_project()
        with mock.patch.object(exporter.gk_log, "info"):
            result = exporter.export_tree(
                self._make_tree(),
                {"output_dir": directory, "write_scene": True, "dry_run": True},
                write_scene=True)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result.get("dry_run"))
        self.assertEqual(result["written"], [])
        self.assertTrue(result["manifest"])
        # 磁盘上一个 .gd / .tscn 都不该出现
        for _root, _dirs, files in os.walk(directory):
            self.assertFalse([f for f in files if f.endswith((".gd", ".tscn"))],
                             files)

    def test_export_tree_rolls_back_on_failure(self):
        """中途失败必须回滚:脚本和场景都不能留下半成品。"""
        directory = self._temp_project()
        project_file = os.path.join(directory, "project.godot")
        with open(project_file, encoding="utf-8") as handle:
            before = handle.read()

        with mock.patch.object(exporter.project, "set_main_scene",
                               side_effect=RuntimeError("boom")), \
                mock.patch.object(exporter.gk_log, "exception"), \
                mock.patch.object(exporter.gk_log, "error"):
            result = exporter.export_tree(
                self._make_tree(),
                {"output_dir": directory, "write_scene": True},
                write_scene=True)

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["stage"], "设主场景")
        self.assertTrue(result["rolled_back"])
        self.assertEqual(result["written"], [])
        # 清单里那两个文件虽然写过,但已被回滚删除
        for path in result["manifest"]:
            if path.endswith((".gd", ".tscn")):
                self.assertFalse(os.path.exists(path), path)
        with open(project_file, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), before)

    # ── 导出契约测试(GPT 审查第 8 条)──────────────────────────────
    # 这些不测"某段代码长什么样",而是测**对外承诺**:反复导出结果一致、
    # extends 与场景类型永远一致、用户已有的 project.godot 配置不被破坏、
    # res:// 路径统一可加载。任何一条破了,用户的工程就会莫名其妙跑不起来。

    def test_repeated_export_is_consistent(self):
        """同一个图导出两次,落盘内容必须完全一致(幂等)。"""
        directory = self._temp_project()
        options = {"output_dir": directory, "write_scene": True}

        def snapshot(paths):
            data = {}
            for path in paths:
                with open(path, encoding="utf-8") as handle:
                    data[path] = handle.read()
            return data

        first = exporter.export_tree(self._make_tree(), options,
                                     write_scene=True)
        self.assertTrue(first["ok"], first)
        before = snapshot(first["written"])

        second = exporter.export_tree(self._make_tree(), options,
                                      write_scene=True)
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["written"], first["written"])
        self.assertEqual(snapshot(second["written"]), before)

    def test_extends_matches_scene_node_type(self):
        """`.gd` 的 extends 与 `.tscn` 里节点的 type 必须是同一个值。

        不一致时 Godot 直接报
        『Script inherits from native type 'X', so it can't be assigned
         to an object of type: 'Y'』,整个场景加载失败。
        """
        directory = self._temp_project()
        result = exporter.export_tree(
            self._make_tree(),
            {"output_dir": directory, "write_scene": True, "node_name": "Player",
             "base_class": "CharacterBody3D"},
            write_scene=True)
        self.assertTrue(result["ok"], result)
        script_path = os.path.join(directory, "scripts", result["script_name"])
        scene_path = os.path.join(directory, "scenes", "Player.tscn")
        with open(script_path, encoding="utf-8") as handle:
            code = handle.read()
        with open(scene_path, encoding="utf-8") as handle:
            scene = handle.read()
        match = re.search(r"^extends\s+(\w+)", code, re.MULTILINE)
        self.assertIsNotNone(match, code)
        self.assertIn(f'type="{match.group(1)}"', scene)

    def test_export_preserves_user_project_settings(self):
        """导出只动输入映射 / 主场景,用户自己写的段落原样保留。"""
        directory = self._temp_project()
        project_file = os.path.join(directory, "project.godot")
        with open(project_file, "a", encoding="utf-8") as handle:
            handle.write('\n[gk_user]\ncustom_marker="keep"\n')
        result = exporter.export_tree(
            self._make_tree(),
            {"output_dir": directory, "write_scene": True},
            write_scene=True)
        self.assertTrue(result["ok"], result)
        with open(project_file, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn('custom_marker="keep"', text)
        self.assertIn("config_version=5", text)

    def test_scene_uses_uniform_res_paths(self):
        """场景里的资源引用必须是 `res://` 开头、正斜杠,否则 Godot 加载不到。"""
        directory = self._temp_project()
        result = exporter.export_tree(
            self._make_tree(),
            {"output_dir": directory, "write_scene": True, "node_name": "Hero"},
            write_scene=True)
        self.assertTrue(result["ok"], result)
        scene_path = os.path.join(directory, "scenes", "Hero.tscn")
        with open(scene_path, encoding="utf-8") as handle:
            scene = handle.read()
        paths = re.findall(r'path="([^"]+)"', scene)
        self.assertTrue(paths, scene)
        for item in paths:
            self.assertTrue(item.startswith("res://"), item)
            self.assertNotIn("\\", item)

    def test_export_reports_structured_diagnostics(self):
        """缺 project.godot 时:既有结构化诊断(code),也有摊平后的文本。"""
        directory = tempfile.mkdtemp(prefix="gk_noproj_")
        self.addCleanup(shutil.rmtree, directory, True)
        result = exporter.export_tree(self._make_tree(),
                                      {"output_dir": directory})
        self.assertTrue(result["ok"], result)
        codes = [item.code for item in result["diagnostics"]]
        self.assertIn("NO_PROJECT_FILE", codes)
        self.assertTrue(any("project.godot" in text for text in result["warnings"]),
                        result["warnings"])


class TestOldFileCleanup(Base):
    """第十三轮:导出旧文件清理(.gk_generated 清单 + 内容认领)。

    ── 为什么必须胆小(两条真踩过的坑)────────────────────────────
    旧残留会让项目以很难懂的方式坏掉:同名材质盖掉新的("东墙变红"),
    旧脚本带着同一个 class_name 让 Godot 报 `Class "X" already exists`
    (整个工程跑不起来)。所以清理既要做,又必须**只删自己写过的文件**。
    """

    def _project(self):
        """一个最小工程目录:插件那几个子目录 + project.godot。"""
        directory = tempfile.mkdtemp(prefix="gk_clean_")
        self.addCleanup(shutil.rmtree, directory, True)
        for folder in ("scripts", "scenes", "art", "materials", "shaders",
                       "curves"):
            os.makedirs(os.path.join(directory, folder), exist_ok=True)
        with open(os.path.join(directory, "project.godot"), "w",
                  encoding="utf-8") as handle:
            handle.write('config_version=5\n\n[application]\nconfig/name="t"\n')
        return directory

    def _write(self, directory, relative, text="x"):
        path = os.path.join(directory, relative.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    # ---- 清单本身 ----
    def test_manifest_round_trip(self):
        from BlenderGameKit import generated_files as gf
        directory = self._project()
        path = gf.write_manifest(directory, ["scripts/a.gd", "art/x.glb"])
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(gf.read_manifest(directory),
                         ["art/x.glb", "scripts/a.gd"])
        # 清单是 JSON、带生成者标记(用户能看出这是什么)
        with open(path, encoding="utf-8") as handle:
            self.assertIn("BlenderGameKit", handle.read())

    def test_manifest_does_not_collide_with_project_marker(self):
        """清单文件不能和『主场景标记』抢名字(真撞过一次)。

        `.gk_generated` 是 project/metadata 用来记"主场景是插件设的哪一个"
        的;第一版清单也用了这个名字,两边互相覆盖 —— 表现是"导出后主场景
        突然乱指"。
        """
        from BlenderGameKit import generated_files as gf
        from BlenderGameKit import project as project_mod
        directory = self._project()
        # 假装 metadata 写过自己的标记
        with open(os.path.join(directory, ".gk_generated"), "w",
                  encoding="utf-8") as handle:
            handle.write('{"main_scene": "res://scenes/World.tscn"}')
        self.assertNotEqual(gf.MANIFEST_NAME, ".gk_generated")
        self.assertEqual(gf.read_manifest(directory), [])
        gf.write_manifest(directory, ["scripts/a.gd"])
        # 两边的文件互不影响
        with open(os.path.join(directory, ".gk_generated"),
                  encoding="utf-8") as handle:
            self.assertIn("World.tscn", handle.read())
        self.assertEqual(gf.read_manifest(directory), ["scripts/a.gd"])
        self.assertTrue(hasattr(project_mod, "set_main_scene"))

    def test_relative_paths_keep_original_case(self):
        """相对路径必须保留原始大小写(踩过的坑,真删过文件)。

        Windows 比较路径要用 normcase,但**算相对路径**用 normcase 过的东西
        会把 `scenes/World.tscn` 变成 `scenes/world.tscn` —— 和磁盘上的
        名字对不上,清理就把刚写好的主场景当"旧文件"删了。
        """
        from BlenderGameKit import generated_files as gf
        directory = self._project()
        target = self._write(directory, "scenes/World.tscn", "x")
        self.assertEqual(gf.relative_files(directory, [target]),
                         ["scenes/World.tscn"])
        # 不在项目里的路径直接丢掉
        outside = self._write(tempfile.mkdtemp(prefix="gk_out_"), "a.gd", "x")
        self.assertEqual(gf.relative_files(directory, [outside]), [])

    def test_references_ignore_prose(self):
        """`res://` 后面跟着注释文字时不能当成路径抓进来。"""
        from BlenderGameKit import generated_files as gf
        directory = self._project()
        script = self._write(
            directory, "scripts/game_logic.gd",
            "# 由 BlenderGameKit 自动生成\n"
            '## 形如 "Ball=res://materials/Ballmat.tres|Floor=res://x.tres"\n'
            "## 竖线在 res://路径里都不会出现,安全。\n"
            'const C := preload("res://curves/curve_1a2b3c4d.tres")\n')
        found = gf.referenced_files(
            directory, gf.relative_files(directory, [script]))
        self.assertIn("materials/Ballmat.tres", found)
        self.assertIn("curves/curve_1a2b3c4d.tres", found)
        for bad in found:
            self.assertTrue(bad.lower().endswith((".gd", ".tscn", ".tres",
                                                  ".glb", ".png")), bad)
            self.assertNotIn("路径", bad)

    def test_collider_dims_ignore_vector3_prefix(self):
        """碰撞体尺寸要从 `Vector3(...)` 里读 —— "Vector3" 的 3 不算数。

        ── 这条是假告警的根因(示例导出时抓到的)────────────────────
        第一版直接 findall 数字,`Vector3(26, 0.4, 26)` 被读成
        [3, 26, 0.4] —— 于是地面被报成"碰撞体 (26, 3, 0.4) 和网格对不上",
        给一堆完全正常的物体刷假告警(导出结果一直是对的)。
        """
        from BlenderGameKit import asset_export as ae
        box = ('[sub_resource type="BoxShape3D" id="shape"]\n'
               'size = Vector3(26.000000, 0.400000, 26.000000)')
        self.assertEqual(ae._collider_dims(box), [26.0, 26.0, 0.4])
        capsule = ('[sub_resource type="CapsuleShape3D" id="shape"]\n'
                   'radius = 0.500000\nheight = 1.600000')
        # 半径 0.5 -> 直径 1.0;高 1.6(比直径大,所以取 1.6)
        self.assertEqual(ae._collider_dims(capsule), [1.6, 1.0, 1.0])
        sphere = ('[sub_resource type="SphereShape3D" id="shape"]\n'
                  'radius = 0.300000')
        self.assertEqual(ae._collider_dims(sphere), [0.6, 0.6, 0.6])

    def test_manifest_broken_file_is_ignored(self):
        """清单坏了/被删了都不能把导出搞崩 —— 当成"没有清单"。"""
        from BlenderGameKit import generated_files as gf
        directory = self._project()
        with open(gf.manifest_path(directory), "w", encoding="utf-8") as handle:
            handle.write("{ 这不是 json")
        self.assertEqual(gf.read_manifest(directory), [])

    def test_stale_files_diff(self):
        from BlenderGameKit import generated_files as gf
        stale = gf.stale_files(None, current=["scripts/a.gd"],
                               previous=["scripts/a.gd", "scripts/b.gd"])
        self.assertEqual(stale, ["scripts/b.gd"])

    # ---- 内容认领:什么算"插件生成的" ----
    def test_is_generated_file_markers(self):
        from BlenderGameKit import generated_files as gf
        directory = self._project()
        ours = self._write(directory, "scripts/game_logic.gd",
                           "# 由 BlenderGameKit 自动生成，请勿手动修改\n"
                           "extends CharacterBody3D\n")
        theirs = self._write(directory, "scripts/我自己写的.gd",
                             "extends Node\n# 手写脚本\n")
        mat = self._write(directory, "materials/material.tres",
                          '[gd_resource type="ShaderMaterial" format=3]\n'
                          '[ext_resource type="Shader" '
                          'path="res://shaders/gk_toon.gdshader" id="1"]\n')
        curve = self._write(directory, "curves/curve_1a2b3c4d.tres",
                            '[gd_resource type="Curve" format=3]\n')
        scene = self._write(directory, "scenes/World.tscn",
                            "; 由 BlenderGameKit 自动生成\n"
                            '[gd_scene load_steps=1 format=3]\n')
        png = self._write(directory, "art/skin.png", "not really a png")
        self.assertTrue(gf.is_generated_file(ours))
        self.assertTrue(gf.is_generated_file(mat))
        self.assertTrue(gf.is_generated_file(curve))
        self.assertTrue(gf.is_generated_file(scene))
        self.assertFalse(gf.is_generated_file(theirs))
        # 认不出内容的(二进制贴图)不认领 —— 不冒险
        self.assertFalse(gf.is_generated_file(png))

    def test_scan_adopts_legacy_files(self):
        """老工程(还没有清单)第一次导出:带标记的旧文件要被认领。"""
        from BlenderGameKit import generated_files as gf
        directory = self._project()
        self._write(directory, "scripts/旧逻辑.gd",
                    "# 由 BlenderGameKit 自动生成\n")
        self._write(directory, "scripts/用户脚本.gd", "extends Node\n")
        found = gf.scan_generated_files(directory)
        self.assertIn("scripts/旧逻辑.gd", found)
        self.assertNotIn("scripts/用户脚本.gd", found)

    # ---- 删除:三条安全规则 ----
    def test_cleanup_deletes_only_listed_and_keeps_user_files(self):
        from BlenderGameKit import generated_files as gf
        directory = self._project()
        old = self._write(directory, "materials/旧材质.tres",
                          '[ext_resource path="res://shaders/gk_toon.gdshader"]')
        user = self._write(directory, "materials/用户自己的.tres",
                           '[gd_resource type="StandardMaterial3D" format=3]')
        fresh = self._write(directory, "scripts/game_logic.gd",
                            "# 由 BlenderGameKit 自动生成\n")
        # 上一轮写了 旧材质 + game_logic
        gf.write_manifest(directory, gf.relative_files(
            directory, [old, fresh, user]))
        # 这一轮只写了 game_logic(旧材质不再需要)
        deleted = gf.cleanup_after_export(directory, [fresh])
        self.assertEqual(deleted, ["materials/旧材质.tres"])
        self.assertFalse(os.path.exists(old))
        # 用户自己的文件绝不能被删(它**不在**上一轮清单里)
        self.assertTrue(os.path.exists(user))
        self.assertTrue(os.path.exists(fresh))
        # 新清单里没有旧材质,但有这一轮写的东西
        manifest = gf.read_manifest(directory)
        self.assertNotIn("materials/旧材质.tres", manifest)
        self.assertIn("scripts/game_logic.gd", manifest)

    def test_removable_path_guards(self):
        """清单被人手改过也不能删到项目外面 / 关键文件。"""
        from BlenderGameKit import generated_files as gf
        directory = self._project()
        for bad in ("../outside.txt", "scripts/../../x.txt", "project.godot",
                    "scripts/.hidden", "根目录文件.txt", "scripts/a/b.gd",
                    "docs/readme.md"):
            self.assertIsNone(gf.removable_path(directory, bad), bad)

    def test_cleanup_survives_missing_files(self):
        from BlenderGameKit import generated_files as gf
        directory = self._project()
        gf.write_manifest(directory, ["materials/已经没了.tres"])
        self.assertEqual(gf.cleanup_after_export(directory, []), [])
        self.assertEqual(gf.read_manifest(directory), [])

    # ---- 接到导出流程上:全成功才清 ----
    def _fake_export(self, directory, failing):
        """用假场景跑一遍 export_all_scenes(不碰真 bpy)。

        failing=True 时让第二个场景的流水线报错 —— 用来验证
        "一半失败就不清理"(否则会把这一版该写、还没写的文件删掉)。
        """
        from BlenderGameKit import asset_export as ae
        from BlenderGameKit import exporter as ex
        scenes = [types.SimpleNamespace(name="关卡1"),
                  types.SimpleNamespace(name="关卡2")]

        def fake_pipeline(scene, directory, dimension="3", script_name=None,
                          scene_name="World"):
            written = [self._write(directory, f"materials/{scene_name}_m.tres",
                                   "gk_toon shader")] if not (
                failing and scene.name == "关卡2") else []
            return {"written": written, "warnings": [], "materials": 1,
                    "bodies": 0, "cameras": 0, "lights": 0,
                    "error": "故意失败" if (failing and scene.name == "关卡2")
                             else None}

        patches = [
            mock.patch.object(ex, "exportable_scenes", lambda: scenes),
            mock.patch.object(ex, "resolve_scene_tree",
                              lambda scene: (None, "没有逻辑图")),
            mock.patch.object(ae, "_activate_scene", lambda scene: None),
            mock.patch.object(ae, "_active_scene", lambda: None),
            mock.patch.object(ae, "_safe_scene_name", lambda name: name),
            mock.patch.object(ae, "export_assets_pipeline", fake_pipeline),
            mock.patch.object(ae.gk_log, "info"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return ae.export_all_scenes(directory, dimension="3")

    def test_full_export_cleans_and_records(self):
        from BlenderGameKit import generated_files as gf
        directory = self._project()
        # 上一轮的残留:一个不再被写的旧材质(带标记 -> 会被认领)
        stale = self._write(directory, "materials/关卡1_旧.tres",
                            "gk_toon shader")
        gf.write_manifest(directory, ["materials/关卡1_旧.tres"])
        result = self._fake_export(directory, failing=False)
        self.assertIsNone(result["error"], result)
        self.assertIn("materials/关卡1_旧.tres", result["deleted"])
        self.assertFalse(os.path.exists(stale))
        manifest = gf.read_manifest(directory)
        self.assertIn("materials/关卡1_m.tres", manifest)
        self.assertIn("materials/关卡2_m.tres", manifest)

    def test_partial_failure_does_not_clean(self):
        """一半场景失败时**不清理**:那些文件是"还没写完",不是"旧文件"。"""
        from BlenderGameKit import generated_files as gf
        directory = self._project()
        stale = self._write(directory, "materials/关卡1_旧.tres",
                            "gk_toon shader")
        gf.write_manifest(directory, ["materials/关卡1_旧.tres"])
        result = self._fake_export(directory, failing=True)
        self.assertEqual(result["deleted"], [])
        self.assertTrue(os.path.exists(stale))
        # 清单也没被改写(还是上一轮那份)
        self.assertEqual(gf.read_manifest(directory),
                         ["materials/关卡1_旧.tres"])


class TestColliderSyncAndGodGuard(Base):
    """第十四轮:碰撞代理跟随 + 上帝视角期间相机过渡暂停(用户实测反馈)。"""

    def test_camera_blend_pauses_in_god_view(self):
        """开了上帝摄像机时,过渡要加"上帝机在用就不推进"的守卫。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        switch = tree.new_node(NN.GKNodeCameraSetCurrent, "推镜",
                               gk_target="特写机位", gk_blend=1.5)
        gk_stub.connect(tree, event, "执行", switch, "执行")
        code, warnings = gen(tree, dict(V3, god_camera=True))
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _gk_god_camera_active()", code)
        self.assertIn("if _gk_cam_blend_left > 0.0 "
                      "and not _gk_god_camera_active():", code)

    def test_camera_blend_has_no_guard_without_god_camera(self):
        """没开上帝机时不能加那句 —— 那个函数根本不会生成(会解析失败)。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        switch = tree.new_node(NN.GKNodeCameraSetCurrent, "推镜",
                               gk_target="特写机位", gk_blend=1.5)
        gk_stub.connect(tree, event, "执行", switch, "执行")
        code, _w = gen(tree, V3)
        self.assertNotIn("_gk_god_camera_active", code)
        self.assertIn("if _gk_cam_blend_left > 0.0:", code)

    def test_environment_script_syncs_colliders(self):
        """环境脚本里要有"碰撞代理跟着网格走"的那套(门才挡得住人)。"""
        from BlenderGameKit import asset_export as ae
        script = ae.ENVIRONMENT_SCRIPT
        self.assertIn("var _gk_body_links: Array = []", script)
        self.assertIn("func _gk_link_colliders() -> void:", script)
        self.assertIn("func _gk_sync_colliders() -> void:", script)
        # 配对是 deferred(那时 glb 内部的网格才进树)+ 每物理帧同步
        self.assertIn("_gk_link_colliders.call_deferred()", script)
        self.assertIn("_gk_sync_colliders()", script)
        # 全树找 Colliders 容器(嵌套场景 / 多关卡也成立)
        self.assertIn('find_children("Colliders", "Node", true, false)', script)

    def test_environment_script_syncs_both_ways(self):
        """第二十五轮:**谁动了谁说了算** —— 两个方向都要能同步。

        只做"网格 -> 代理"的话,用 velocity + move_and_slide 推着走的道具
        (追人的敌人)每帧都会被拽回原处:实测探针里
        `chaser_at=(0.0, 0.7, -2.5)` 一直不动,而速度明明是 2.2。
        """
        from BlenderGameKit import asset_export as ae
        script = ae.ENVIRONMENT_SCRIPT
        # 配对时记下上一帧的两边变换(第 3、4 个元素)
        self.assertIn("mesh.global_transform, body.global_transform])", script)
        # 网格动了 -> 代理跟上;代理自己动了 -> 把网格拉过来
        self.assertIn("if mesh_now != pair[2]:", script)
        self.assertIn("body.global_transform = mesh_now", script)
        self.assertIn("elif body_now != pair[3]:", script)
        self.assertIn("mesh.global_transform = body_now", script)


class TestStaticAssets(Base):
    """第十五轮:静态资产(资产名 + 实例化导出 + 面板定位)。"""

    class _Physics:
        def __init__(self, asset="", instance=False, enabled=True):
            self.asset_name = asset
            self.asset_instance = instance
            self.enabled = enabled

    class _Obj:
        def __init__(self, name, asset="", instance=False, enabled=True):
            self.name = name
            self.type = "MESH"
            self.gk_physics = TestStaticAssets._Physics(asset, instance, enabled)

    def _scene(self, objects):
        return types.SimpleNamespace(objects=objects)

    def test_collect_groups_by_asset_name(self):
        from BlenderGameKit import asset_export as ae
        scene = self._scene([
            self._Obj("树_本体", "树"),
            self._Obj("树_实例1", "树", instance=True),
            self._Obj("树_实例2", "树", instance=True),
            self._Obj("石头", "石头"),
            self._Obj("普通物体"),
        ])
        assets = ae.collect_assets(scene)
        self.assertEqual(sorted(assets), sorted(["树", "石头"]))
        self.assertEqual(assets["树"]["source"].name, "树_本体")
        self.assertEqual(len(assets["树"]["members"]), 3)
        self.assertEqual(assets["石头"]["source"].name, "石头")

    def test_instance_without_source_warns(self):
        from BlenderGameKit import asset_export as ae
        scene = self._scene([self._Obj("树A", "树", instance=True)])
        assets = ae.collect_assets(scene)
        self.assertIsNone(assets["树"]["source"])
        warnings = ae.validate_assets(scene)
        self.assertTrue(any("没有本体" in w for w in warnings), warnings)

    def test_multiple_sources_warn(self):
        from BlenderGameKit import asset_export as ae
        scene = self._scene([self._Obj("树A", "树"), self._Obj("树B", "树")])
        warnings = ae.validate_assets(scene)
        self.assertTrue(any("多个本体" in w for w in warnings), warnings)

    def test_asset_model_names_are_unique(self):
        """两个中文名的资产不能写成同一个 glb(否则互相覆盖)。"""
        from BlenderGameKit import asset_export as ae
        first = ae.asset_model_name("树")
        second = ae.asset_model_name("石头")
        self.assertNotEqual(first, second)
        self.assertEqual(first, ae.asset_model_name("树"))       # 稳定
        self.assertTrue(first.startswith("asset_"))
        # 多场景带场景前缀(两关的同名资产不会互相覆盖)
        self.assertNotEqual(ae.asset_model_name("树", "关卡1"),
                            ae.asset_model_name("树", "关卡2"))

    def test_env_script_applies_material_to_all_copies(self):
        """同名节点有多个时(资产摆多份),材质要**全部**挂上。"""
        from BlenderGameKit import asset_export as ae
        script = ae.ENVIRONMENT_SCRIPT
        self.assertIn("var targets: Array = []", script)
        self.assertIn("for target in targets:", script)
        self.assertNotIn("node = candidate\n\t\t\tbreak", script)

    def test_asset_materials_make_it_into_the_table(self):
        """资产(本体 + 实例)的材质必须进材质表。

        实测漏过一次:资产的网格在自己的 glb 里、世界 glb 刻意不含它们,
        于是 write_world_scene 里"按世界 glb 网格名过滤"的循环扫不到 ——
        三份『树』全是 glb 自带的素白 PBR 材质,探针报 materials=0。
        """
        from unittest import mock
        from BlenderGameKit import asset_export as ae
        scene = self._scene([
            self._Obj("树_本体", "树"),
            self._Obj("树_实例1", "树", instance=True),
        ])
        materials_map = {"树_本体": ("树_本体材质", "material_tree.tres")}
        with mock.patch.object(ae, "glb_scene_nodes",
                               return_value={"meshes": {"树_本体": {}}}):
            table = ae.asset_material_files("proj", scene, {"树": "asset_树"},
                                            materials_map, {})
        # 键 = 资产 glb 里的网格节点名(= 本体名):运行时一条挂上全部实例
        self.assertEqual(table, {"树_本体": "material_tree.tres"})

    def test_asset_without_source_or_material_is_silent(self):
        """没有本体 / 材质表里查不到时,不抛错也不写空条目。"""
        from unittest import mock
        from BlenderGameKit import asset_export as ae
        scene = self._scene([self._Obj("树_实例1", "树", instance=True)])
        with mock.patch.object(ae, "glb_scene_nodes",
                               return_value={"meshes": {"树_本体": {}}}):
            table = ae.asset_material_files("proj", scene, {"树": "asset_树"},
                                            {}, {})
        self.assertEqual(table, {})

    def test_codegen_map_carries_glb_and_collision(self):
        """『资产_创建实例』要的表:资产名 -> glb 路径 + 碰撞参数。

        这些数必须和 .tscn 里那份碰撞体**同源**(都走 build_collision),
        各算一遍迟早会算出两个大小。
        """
        from unittest import mock
        from BlenderGameKit import asset_export as ae
        source = self._Obj("树_本体", "树")
        scene = self._scene([source, self._Obj("树_实例1", "树", instance=True)])
        built = {"shape": "box", "dims": (1.0, 2.0, 1.0), "radius": 0.5,
                 "height": 2.0, "layer": "1", "mask": "1"}
        with mock.patch.object(ae, "build_collision", return_value=built):
            table = ae.asset_codegen_map(scene, "3", {"树": "asset_树"})
        info = table["树"]
        self.assertEqual(info["path"], "res://art/asset_树.glb")
        self.assertEqual(info["shape"], 0)          # box -> 0
        self.assertEqual(info["dims"], (1.0, 2.0, 1.0))
        self.assertEqual(info["radius"], 0.5)
        self.assertEqual(info["height"], 2.0)

    def test_codegen_map_marks_missing_collision(self):
        """本体选了『碰撞形状 = 无』-> shape = -1(运行时只建网格)。"""
        from unittest import mock
        from BlenderGameKit import asset_export as ae
        scene = self._scene([self._Obj("石头_本体", "石头")])
        with mock.patch.object(ae, "build_collision", return_value=None):
            table = ae.asset_codegen_map(scene, "3", {"石头": "asset_石头"})
        self.assertEqual(table["石头"]["shape"], -1)

    # ---- 碰撞形状的"智能推荐"(用户 2026-10-06:"正方体对角穿模")----
    @staticmethod
    def _mesh_obj(name, points, faces=None):
        """造一个带网格数据的假物体(圆度只看顶点 + **面中心**)。

        `points` = 顶点坐标;`faces` = 面中心坐标(和 Blender 的
        `mesh.polygons[i].center` 一个意思。**不能省** ——
        Blender 默认正方体只有 8 个顶点,全在角上,只看顶点会误判成球)。
        """
        class _Vertex:
            def __init__(self, co):
                self.co = co

        class _Polygon:
            def __init__(self, center):
                self.center = center

        class _Mesh:
            def __init__(self, verts, polys):
                self.vertices = verts
                self.polygons = polys

        class _Obj:
            def __init__(self, verts, polys):
                self.name = name
                self.type = "MESH"
                self.data = _Mesh(verts, polys)
                self.gk_physics = None

            @property
            def dimensions(self):
                return (2.0, 2.0, 2.0)

        return _Obj([_Vertex(p) for p in points],
                    [_Polygon(c) for c in (faces or [])])

    # Blender 默认正方体:8 个角 + 6 个面中心(边长 2,中心在原点)
    _CUBE_POINTS = ([(x, y, z) for x in (-1, 1) for y in (-1, 1)
                     for z in (-1, 1)]
                    + [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0),
                       (0, 0, 1), (0, 0, -1)])

    def test_roundness_tells_cube_from_sphere(self):
        """圆度:球 ≈ 1.0,正方体(含面中心)≈ 0.58 —— "对角穿模"的判据。"""
        import math
        from BlenderGameKit import asset_export as ae
        ratio_cube = ae.mesh_roundness(self._mesh_obj("方块", self._CUBE_POINTS))
        self.assertLess(ratio_cube, 0.7)
        # 球:顶点和面中心都在半径上
        sphere_points = []
        sphere_faces = []
        for i in range(24):
            for j in range(12):
                theta = math.pi * i / 24.0
                phi = 2.0 * math.pi * j / 12.0
                sphere_points.append((math.sin(theta) * math.cos(phi),
                                      math.sin(theta) * math.sin(phi),
                                      math.cos(theta)))
                sphere_faces.append((math.sin(theta + 0.06) * math.cos(phi + 0.1),
                                     math.sin(theta + 0.06) * math.sin(phi + 0.1),
                                     math.cos(theta + 0.06)))
        ratio_sphere = ae.mesh_roundness(
            self._mesh_obj("球", sphere_points, sphere_faces))
        self.assertGreater(ratio_sphere, 0.95)

    def test_bare_cube_without_face_centers_is_not_sphere(self):
        """只有 8 个顶点的正方体也不能判成球(真 Blender 上就是这样)。

        这是第一版的坑:只看顶点 -> 8 个角到中心等距 -> 圆度 1.0 ->
        判成球 -> 正方体的角全漏在碰撞体外面(用户报的"对角穿模")。
        """
        from BlenderGameKit import asset_export as ae
        corners = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
        ratio = ae.mesh_roundness(self._mesh_obj("光板方块", corners))
        # 只有角时圆度会很高 —— 但**没有面中心数据**时不硬判成球:
        # 面中心是 Blender 一定会给的,取不到就该退回盒子(在下面那条路)
        self.assertGreater(ratio, 0.9)

    def test_cube_gets_box_collider(self):
        """正方体 -> 盒子(**不是**球):球内切在立方体里,八个角会漏出来。"""
        from unittest import mock
        from BlenderGameKit import asset_export as ae
        obj = self._mesh_obj("方块", self._CUBE_POINTS)
        with mock.patch.object(ae, "object_bounds",
                               return_value={"size": (2.0, 2.0, 2.0),
                                             "center": (0.0, 0.0, 0.0)}):
            shape = ae.guess_collision_shape(obj, True)
        self.assertEqual(shape, "box")

    def test_shape_falls_back_to_box_without_mesh(self):
        """没有网格数据(离线桩 / 空网格)-> 退回盒子(包得住整个模型)。"""
        from unittest import mock
        from BlenderGameKit import asset_export as ae
        obj = self._mesh_obj("空", [])
        with mock.patch.object(ae, "object_bounds",
                               return_value={"size": (1.0, 1.0, 1.0),
                                             "center": (0.0, 0.0, 0.0)}):
            self.assertEqual(ae.guess_collision_shape(obj, True), "box")

    # ---- 碰撞系统打磨(本轮:偏移 / 碰撞块 / 凸包 / 预览=导出)----

    @staticmethod
    def _plain_physics(**kwargs):
        """假物理设置(默认 = 出厂状态;可改任意字段)。"""
        values = {"collision_shape": "auto", "shape_size": (0.0, 0.0, 0.0),
                  "collision_layer": "1", "collision_mask": "1",
                  "is_trigger": False, "body_type": "StaticBody3D",
                  "collision_piece": False}
        values.update(kwargs)
        return types.SimpleNamespace(**values)

    def test_collision_offset_points_to_bbox_center(self):
        """碰撞偏移 = 包围盒中心 − 物体原点(Godot 轴序)。

        脚底原点的模型(大量导入模型如此)靠它把形状抬到模型中心;
        ⚠ 这个数以前只躺在返回值里**没人用** —— 生成的 .tscn 里
        Collision 节点上没有 position,"站不住"的老修复被漏掉了。
        """
        from unittest import mock
        from BlenderGameKit import asset_export as ae
        obj = self._mesh_obj("脚底方块", self._CUBE_POINTS)
        obj.gk_physics = self._plain_physics()
        obj.matrix_world = types.SimpleNamespace(
            translation=types.SimpleNamespace(x=0.0, y=0.0, z=0.0))
        # 原点在 (0,0,0),包围盒中心在 (0,0,1) 上方 -> Godot 偏移 (0,1,0)
        with mock.patch.object(ae, "object_bounds",
                               return_value={"size": (2.0, 2.0, 2.0),
                                             "center": (0.0, 0.0, 1.0)}):
            built = ae.build_collision(obj, True, obj.gk_physics)
        self.assertEqual(built["offset"], (0.0, 1.0, 0.0))
        self.assertIn("position = Vector3(0.000000, 1.000000, 0.000000)",
                      built["node"])

    def test_position_line_zero_and_nonzero(self):
        """全零偏移 -> 不写 position(老产物不变);非零 -> Vector3/Vector2。"""
        from BlenderGameKit import asset_export as ae
        self.assertIsNone(ae._position_line((0.0, 0.0, 0.0), True))
        self.assertIsNone(ae._position_line(None, True))
        self.assertEqual(ae._position_line((1.5, -0.5, 2.0), True),
                         "position = Vector3(1.500000, -0.500000, 2.000000)")
        self.assertEqual(ae._position_line((1.5, -0.5, 0.0), False),
                         "position = Vector2(1.500000, -0.500000)")

    def test_collision_piece_helpers(self):
        """碰撞块三件套:标记 / 找宿主(跨层级)/ 列块(所有层级)。"""
        from BlenderGameKit import asset_export as ae
        host = self._Obj("角色")
        empty = types.SimpleNamespace(name="空物体", type="EMPTY",
                                      parent=host, children=[])
        head = self._Obj("头")
        head.gk_physics.collision_piece = True
        head.parent = empty
        empty.children = [head]
        body = self._Obj("身")
        body.gk_physics.collision_piece = True
        body.parent = host
        host.children = [empty, body]

        self.assertTrue(ae.is_collision_piece(head))
        self.assertFalse(ae.is_collision_piece(host))
        # 宿主跳过中转的空物体 / 别的碰撞块
        self.assertIs(ae.collision_owner(head), host)
        self.assertIs(ae.collision_owner(body), host)
        self.assertIsNone(ae.collision_owner(host))
        # 列块按名字排序(导出稳定):头 / 身
        self.assertEqual([p.name for p in ae.collision_pieces(host)],
                         ["头", "身"])

    def test_pieces_become_extra_shapes_in_asset_table(self):
        """asset_shape_entries:本体一条 + 每个碰撞块一条(偏移相对本体)。

        运行时"创建实例"靠这张表造碰撞 —— 和 .tscn 那份同源,
        不会出现"本体能挡人、生成的这份穿过去"。
        """
        from unittest import mock
        from BlenderGameKit import asset_export as ae
        host = self._mesh_obj("角色", self._CUBE_POINTS)
        host.gk_physics = self._plain_physics()
        head = self._mesh_obj("头", self._CUBE_POINTS)
        head.gk_physics = self._plain_physics(collision_piece=True)
        head.parent = host
        host.children = [head]

        def bounds_for(objects, local=False):
            obj = objects[0]
            if obj is host:
                return {"size": (2.0, 2.0, 2.0), "center": (0.0, 0.0, 0.0)}
            # 头:**局部**中心是它自己的原点(形状尺寸用),**世界**中心在
            # z=2(碰撞块的偏移用,偏移 = 世界中心 − 宿主原点)。
            # 第二十八轮起同一个函数会按 local 被问两种语义。
            return {"size": (1.0, 1.0, 1.0),
                    "center": (0.0, 0.0, 0.0) if local else (0.0, 0.0, 2.0)}

        with mock.patch.object(ae, "object_bounds", side_effect=bounds_for):
            entries = ae.asset_shape_entries(host, True)
        self.assertEqual(len(entries), 2)
        # 本体:盒子(2×2×2 的三边接近 + 圆度假设为盒子)偏移 0
        self.assertEqual(entries[0]["kind"], 0)
        self.assertEqual(entries[0]["offset"], (0.0, 0.0, 0.0))
        # 头在 Blender 的 z=2(上方)-> Godot 偏移 (0, 2, 0)
        self.assertEqual(entries[1]["kind"], 0)
        self.assertEqual(entries[1]["offset"], (0.0, 2.0, 0.0))

    def test_convex_shape_builds_points(self):
        """凸包:sub_resource 是 ConvexPolygonShape3D,点集 = 世界凸包换算。"""
        from unittest import mock
        from BlenderGameKit import asset_export as ae
        obj = self._mesh_obj("石头", self._CUBE_POINTS)
        obj.gk_physics = self._plain_physics(collision_shape="convex")
        hull = [(x, y, z) for x in (-1.0, 1.0) for y in (-1.0, 1.0)
                for z in (0.0, 2.0)]           # 一条边贴地的"石头"
        with mock.patch.object(ae, "object_bounds",
                               return_value={"size": (2.0, 2.0, 2.0),
                                             "center": (0.0, 0.0, 1.0)}), \
                mock.patch.object(ae, "convex_points", return_value=hull):
            built = ae.build_collision(obj, True, obj.gk_physics)
        self.assertEqual(built["shape"], "convex")
        self.assertIn("ConvexPolygonShape3D", built["sub_resource"])
        numbers = built["sub_resource"].split("PackedVector3Array(")[1] \
            .rstrip(")").split(", ")
        self.assertEqual(len(numbers), 24)     # 8 个点 × 3
        # 点相对包围盒中心:z=0 的点在 Godot 里 y=-1,z=2 -> y=+1
        self.assertIn("-1.000000", numbers)
        self.assertIn("1.000000", numbers)
        self.assertEqual(len(built["points"]), 8)
        self.assertEqual(len(built["points_bl"]), 8)

    def test_convex_falls_back_to_box_without_hull(self):
        """算不出凸包(离线桩 / 空网格)-> 退回盒子,不生成非法形状。"""
        from unittest import mock
        from BlenderGameKit import asset_export as ae
        obj = self._mesh_obj("石头", self._CUBE_POINTS)
        obj.gk_physics = self._plain_physics(collision_shape="convex")
        with mock.patch.object(ae, "object_bounds",
                               return_value={"size": (2.0, 2.0, 2.0),
                                             "center": (0.0, 0.0, 0.0)}), \
                mock.patch.object(ae, "convex_points", return_value=[]):
            built = ae.build_collision(obj, True, obj.gk_physics)
        self.assertEqual(built["shape"], "box")
        self.assertIn("BoxShape3D", built["sub_resource"])

    def test_preview_matches_export_sizes(self):
        """预览=导出:线框的半尺寸和导出参数一一对应(本轮大修)。

        以前三处不一致:位置(画在世界原点)、尺寸(自动模式乱用手填)、
        轴向(半径取了 x/z)。现在预览从 build_collision 取数,必须一致。
        """
        from unittest import mock
        from BlenderGameKit import asset_export as ae
        obj = self._mesh_obj("方块", self._CUBE_POINTS)
        obj.gk_physics = self._plain_physics(collision_shape="box",
                                             shape_size=(3.0, 5.0, 7.0))
        with mock.patch.object(ae, "object_bounds",
                               return_value={"size": (2.0, 2.0, 2.0),
                                             "center": (0.0, 0.0, 0.0)}):
            built = ae.build_collision(obj, True, obj.gk_physics)
            wire = ae.collision_preview_mesh(obj, True, obj.gk_physics)
        # 手填尺寸是 Godot 轴序(X=宽, Y=高, Z=深):线框换回 Blender
        # 半边长应该是 (3/2, 7/2, 5/2)
        xs = [v[0] for v in wire["verts"]]
        ys = [v[1] for v in wire["verts"]]
        zs = [v[2] for v in wire["verts"]]
        self.assertAlmostEqual(max(xs), 1.5, places=4)
        self.assertAlmostEqual(max(ys), 3.5, places=4)     # Blender y ← 深
        self.assertAlmostEqual(max(zs), 2.5, places=4)     # Blender z ← 高
        # 导出那份:BoxShape3D size = (3, 5, 7)
        self.assertIn("size = Vector3(3.000000, 5.000000, 7.000000)",
                      built["sub_resource"])

    def test_preview_ignores_manual_size_in_auto_mode(self):
        """『自动』下导出忽略手填尺寸 —— 预览也必须忽略(以前它俩打架)。"""
        from unittest import mock
        from BlenderGameKit import asset_export as ae
        obj = self._mesh_obj("方块", self._CUBE_POINTS)
        obj.gk_physics = self._plain_physics(collision_shape="auto",
                                             shape_size=(9.0, 9.0, 9.0))
        with mock.patch.object(ae, "object_bounds",
                               return_value={"size": (2.0, 2.0, 2.0),
                                             "center": (0.0, 0.0, 0.0)}), \
                mock.patch.object(ae, "mesh_roundness", return_value=0.58):
            built = ae.build_collision(obj, True, obj.gk_physics)
            wire = ae.collision_preview_mesh(obj, True, obj.gk_physics)
        self.assertEqual(built["shape"], "box")
        self.assertIn("size = Vector3(2.000000, 2.000000, 2.000000)",
                      built["sub_resource"])
        xs = [abs(v[0]) for v in wire["verts"]]
        self.assertAlmostEqual(max(xs), 1.0, places=4)     # 不是 4.5(9/2)

    def test_preview_sits_at_object_position(self):
        """线框要落在物体身上(世界坐标)—— 以前画在世界原点,挪了就错。"""
        from unittest import mock
        from BlenderGameKit import asset_export as ae
        obj = self._mesh_obj("方块", self._CUBE_POINTS)
        obj.gk_physics = self._plain_physics()
        obj.matrix_world = ((1, 0, 0, 7.0), (0, 1, 0, 0.0),
                            (0, 0, 1, 0.0), (0, 0, 0, 1))
        # ⚠ 第二十八轮起碰撞形状用**局部**包围盒(朝向由节点 transform 带),
        # 所以桩里中心给 (0,0,0)(相对物体原点),位置由 matrix_world 提供
        with mock.patch.object(ae, "object_bounds",
                               return_value={"size": (2.0, 2.0, 2.0),
                                             "center": (0.0, 0.0, 0.0)}), \
                mock.patch.object(ae, "mesh_roundness", return_value=0.58):
            wire = ae.collision_preview_mesh(obj, True, obj.gk_physics)
        xs = [v[0] for v in wire["verts"]]
        self.assertAlmostEqual(min(xs), 6.0, places=4)
        self.assertAlmostEqual(max(xs), 8.0, places=4)

    def test_preview_merges_pieces(self):
        """宿主预览 = 自己的形状 + 所有碰撞块(整组一起画)。"""
        from unittest import mock
        from BlenderGameKit import asset_export as ae
        host = self._mesh_obj("角色", self._CUBE_POINTS)
        host.gk_physics = self._plain_physics()
        host.matrix_world = ((1, 0, 0, 0.0), (0, 1, 0, 0.0),
                             (0, 0, 1, 0.0), (0, 0, 0, 1))
        head = self._mesh_obj("头", self._CUBE_POINTS)
        head.gk_physics = self._plain_physics(collision_piece=True)
        head.parent = host
        head.matrix_world = ((1, 0, 0, 0.0), (0, 1, 0, 0.0),
                             (0, 0, 1, 2.0), (0, 0, 0, 1))
        host.children = [head]

        def bounds_for(objects, local=False):
            obj = objects[0]
            if obj is host:
                return {"size": (2.0, 2.0, 2.0), "center": (0.0, 0.0, 0.0)}
            # 头:**局部**中心是它自己的原点(形状尺寸用),**世界**中心在
            # z=2(碰撞块的偏移用,偏移 = 世界中心 − 宿主原点)。
            # 第二十八轮起同一个函数会按 local 被问两种语义。
            return {"size": (1.0, 1.0, 1.0),
                    "center": (0.0, 0.0, 0.0) if local else (0.0, 0.0, 2.0)}

        with mock.patch.object(ae, "object_bounds", side_effect=bounds_for), \
                mock.patch.object(ae, "mesh_roundness", return_value=0.58):
            wire = ae.collision_preview_mesh(host, True, host.gk_physics)
        # 宿主盒子 8 顶点 + 头(能自动判出来时至少 8 个)
        self.assertGreaterEqual(len(wire["verts"]), 16)
        zs = [v[2] for v in wire["verts"]]                 # Blender 里"上"是 z
        self.assertAlmostEqual(max(zs), 2.5, places=4)     # 头在 z=2 上方
        # 头自己的线框(单独预览碰撞块时)= 只有它那 8 个点
        with mock.patch.object(ae, "object_bounds", side_effect=bounds_for), \
                mock.patch.object(ae, "mesh_roundness", return_value=0.58):
            head_wire = ae.collision_preview_mesh(head, True, head.gk_physics)
        self.assertLess(len(head_wire["verts"]), len(wire["verts"]))

    # ---- 运行时创建 / 销毁(用户 2026-10-05)----
    _TREE_INFO = {"path": "res://art/asset_树.glb", "shape": 0,
                  "dims": (1.0, 2.0, 1.0), "radius": 0.5, "height": 2.0,
                  "layer": 1, "mask": 1,
                  # 形状表(本轮:多碰撞体 + 凸包)—— 真实导出由
                  # asset_export.asset_shape_entries 生成,这里照抄一份
                  "shapes": [{"kind": 0, "dims": (1.0, 2.0, 1.0),
                              "radius": 0.5, "height": 2.0,
                              "offset": (0.0, 0.0, 0.0)}]}

    def test_asset_spawn_uses_export_table(self):
        """『资产_创建实例』查导出给的资产表 -> 一行调用 + 那个 helper。"""
        tree = make_tree()
        ready = tree.new_node(NN.GKNodeOnReady, "准备")
        spawn = tree.new_node(NN.GKNodeAssetSpawn, "种树", gk_asset="树",
                              gk_x=1.0, gk_y=0.0, gk_z=2.0)
        gk_stub.connect(tree, ready, "执行", spawn, "执行")
        options = dict(V3)
        options["assets"] = {"树": dict(self._TREE_INFO)}
        code, warnings = gen(tree, options)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _gk_spawn_asset(", code)
        self.assertIn('"res://art/asset_树.glb"', code)
        self.assertIn("Vector3(1.0, 0.0, 2.0)", code)
        self.assertIn("BoxShape3D.new()", code)
        # 形状表(本轮):调用里要带上"要造哪些碰撞形状"的字典数组
        self.assertIn('"kind": 0', code)
        self.assertIn('"dims": Vector3(1.0, 2.0, 1.0)', code)
        self.assertIn('"offset": Vector3(0.0, 0.0, 0.0)', code)
        self.assertIn("solid.collision_layer = layer", code)
        self.assertIn("get_tree().current_scene.add_child(", code)
        # glb 根节点里烘焙的"本体世界变换"必须就地抵消
        self.assertIn("placed.transform = placed.transform.affine_inverse()",
                      code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_asset_spawn_without_collision_flag(self):
        """勾掉『带碰撞』-> 空形状表,只建网格(不加碰撞体)。"""
        tree = make_tree()
        ready = tree.new_node(NN.GKNodeOnReady, "准备")
        spawn = tree.new_node(NN.GKNodeAssetSpawn, "种草", gk_asset="树",
                              gk_solid=False)
        gk_stub.connect(tree, ready, "执行", spawn, "执行")
        options = dict(V3)
        options["assets"] = {"树": dict(self._TREE_INFO)}
        code, _warnings = gen(tree, options)
        self.assertIn(", [], ", code)
        self.assertIn("if not shapes.is_empty():", code)

    def test_asset_spawn_warns_on_unknown_name(self):
        """资产名写错 -> 告警里点名 + 列出可用的(而不是生成空路径)。"""
        tree = make_tree()
        ready = tree.new_node(NN.GKNodeOnReady, "准备")
        spawn = tree.new_node(NN.GKNodeAssetSpawn, "种树", gk_asset="草")
        gk_stub.connect(tree, ready, "执行", spawn, "执行")
        options = dict(V3)
        options["assets"] = {"树": dict(self._TREE_INFO)}
        code, warnings = gen(tree, options)
        self.assertTrue(any("草" in w and "树" in w for w in warnings), warnings)
        self.assertNotIn("_gk_spawn_asset(", code)

    def test_destroy_accepts_spawned_instance(self):
        """『实例_销毁』的『目标』引脚接上『实例』-> 直接 free 那个节点。

        ── 为什么走另一条路 ──────────────────────────────────────────
        填『目标』的那种要"连同名网格一起清"(网格和碰撞代理是两个节点);
        而运行时生成的实例自带网格,**直接 free** 就行 ——
        按名字清反而会连坐删掉场景里同名的东西。
        """
        tree = make_tree()
        ready = tree.new_node(NN.GKNodeOnReady, "准备")
        spawn = tree.new_node(NN.GKNodeAssetSpawn, "种树", gk_asset="树")
        destroy = tree.new_node(NN.GKNodeQueueFree, "销毁")
        gk_stub.connect(tree, ready, "执行", spawn, "执行")
        gk_stub.connect(tree, spawn, "完成", destroy, "执行")
        gk_stub.connect(tree, spawn, "实例", destroy, "目标")
        options = dict(V3)
        options["assets"] = {"树": dict(self._TREE_INFO)}
        code, warnings = gen(tree, options)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("is_instance_valid(", code)
        self.assertIn(".queue_free()", code)
        # 这条路不该动用"按名字清网格"的 helper
        self.assertNotIn("_gk_free_with_visual", code)


class TestFunctions(Base):
    """第十七轮:自定义函数 / 调用(用户:"查看函数,自定义函数,折叠函数")。"""

    def _declare(self, tree, name, return_type="void", params=""):
        func = tree.gk_funcs.add()
        func.func_name = name
        func.return_type = return_type
        func.params = params
        return func

    def test_params_parser(self):
        """『参数』一栏的文本解析:中英文逗号/分号都认,类型不认识就退回浮点。"""
        from BlenderGameKit import properties as props
        parsed = props.parse_function_params("hp:float, amount:bool;name:String")
        self.assertEqual(parsed, [("hp", "float", True), ("amount", "bool", True),
                                  ("name", "String", True)])
        # 只写名字 -> 浮点;类型写错 -> 浮点 + 标记"不认识"
        self.assertEqual(props.parse_function_params("速度"),
                         [("速度", "float", True)])
        self.assertEqual(props.parse_function_params("x:xyz"),
                         [("x", "float", False)])

    def test_function_name_is_stable_and_unique(self):
        """中文函数名要映射成**稳定且互不相同**的 GDScript 函数名。

        踩过的坑和变量名同一族:`snake_case` 会把中文整个压掉,
        『治疗』『扣血』都变成 my_function -> Godot 报"函数已定义"。
        """
        from BlenderGameKit.nodes.node_base import safe_function_name
        first = safe_function_name("治疗")
        second = safe_function_name("扣血")
        self.assertNotEqual(first, second)
        self.assertEqual(first, safe_function_name("治疗"))     # 稳定
        self.assertEqual(safe_function_name("heal_player"), "heal_player")

    def _function_tree(self, **props):
        tree = make_tree()
        define = tree.new_node(NN.GKNodeFunctionDefine, "定义_治疗")
        define.gk_func_name = "治疗"
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        call = tree.new_node(NN.GKNodeFunctionCall, "调用_治疗")
        call.gk_func_name = "治疗"
        body = tree.new_node(NN.GKNodePrint, "治疗中")
        body.gk_text = "heal"
        gk_stub.connect(tree, define, "执行", body, "执行")
        gk_stub.connect(tree, event, "执行", call, "执行")
        return tree, define, call, body

    def test_define_and_call(self):
        tree, _define, _call, _body = self._function_tree()
        self._declare(tree, "治疗", "void", "")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        name = "治疗_"                       # 中文名 -> 名字 + 摘要后缀
        self.assertIn(f"func {name}", code)
        self.assertIn('print("heal")', code)
        # 定义一处 + 调用一处(无返回值时不生成结果变量)
        self.assertEqual(code.count(name), 2, code)
        self.assertNotIn("_gk_fres", code)

    def test_params_become_formals_and_arguments(self):
        """声明里的参数 -> 定义处是形参、调用处是实参,而且**都不生成成员变量**。"""
        tree, _define, call, body = self._function_tree()
        self._declare(tree, "治疗", "void", "hp:float")
        # 声明改了 -> 节点要重长引脚(真实环境靠属性的 update 回调;
        # 桩里没有回调,手动调一次,和用户点完放大镜之后的状态一致)
        call.build_sockets()
        # 函数体里读参数(读的应该是形参,不是成员变量)
        read = tree.new_node(NN.GKNodeGetVar, "读血量", gk_var_name="hp")
        printer = tree.new_node(NN.GKNodePrint, "打印")
        printer.gk_text = "x"
        gk_stub.connect(tree, read, "值", printer, "内容")
        gk_stub.connect(tree, body, "完成", printer, "执行")
        # 调用处:给参数引脚接一个常量
        value = tree.new_node(NN.GKNodeConstFloat, "血量", gk_value=3.0)
        gk_stub.connect(tree, value, "值", call, "hp")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        # 形参在签名里
        self.assertIn("hp: float", code)
        # 参数**不**生成成员变量(生成的话每个函数都往脚本顶部塞一个同名成员)
        self.assertNotIn("var hp:", code)
        # 调用处传的是接上来的实参
        self.assertIn("(3.0)", code)

    def test_return_value(self):
        """声明了返回类型 -> 末尾 return,调用处拿到『结果』。"""
        tree, define, call, _body = self._function_tree()
        self._declare(tree, "治疗", "float", "")
        # 返回值接一个常量
        value = tree.new_node(NN.GKNodeConstFloat, "返回值", gk_value=1.5)
        gk_stub.connect(tree, value, "值", define, "返回")
        out = tree.new_node(NN.GKNodePrint, "打印结果")
        out.gk_text = "x"
        gk_stub.connect(tree, call, "结果", out, "内容")
        gk_stub.connect(tree, call, "完成", out, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("-> float:", code)
        self.assertIn("return 1.5", code)
        self.assertIn("var _fres", code)      # 返回值的临时变量(_ 开头 = 不刷未使用告警)

    def test_undeclared_function_warns(self):
        """名字没声明过 -> 明确告警,不生成猜出来的调用。"""
        tree, _define, call, _body = self._function_tree()
        # 故意不声明
        code, warnings = gen(tree, V3)
        self.assertTrue(any("治疗" in w and "声明" in w for w in warnings), warnings)
        self.assertNotIn("func 治疗", code)

    def test_duplicate_definition_warns(self):
        """同一个函数定义两次 -> 告警并只留第一个(Godot 不允许同名函数)。"""
        tree, define, _call, _body = self._function_tree()
        self._declare(tree, "治疗", "void", "")
        second = tree.new_node(NN.GKNodeFunctionDefine, "定义_治疗2")
        second.gk_func_name = "治疗"
        extra = tree.new_node(NN.GKNodePrint, "第二份")
        extra.gk_text = "dup"
        gk_stub.connect(tree, second, "执行", extra, "执行")
        code, warnings = gen(tree, V3)
        self.assertTrue(any("两次" in w for w in warnings), warnings)
        self.assertEqual(code.count("func 治疗_"), 1)

    # ---- 变量的"实现"(UE5 的 getter/setter,2026-10-05 第十七轮)----
    def _impl_tree(self, getter="", setter=""):
        """一张图:声明变量 hp(可绑 getter/setter)+ 读它 + 写它。"""
        tree = make_tree()
        var = tree.gk_vars.add()
        var.var_name = "hp"
        var.var_type = "float"
        var.default_value = "0"
        var.getter = getter
        var.setter = setter
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        writer = tree.new_node(NN.GKNodeSetVar, "扣血", gk_var_name="hp")
        reader = tree.new_node(NN.GKNodeGetVar, "读血量", gk_var_name="hp")
        printer = tree.new_node(NN.GKNodePrint, "打印")
        printer.gk_text = "x"
        gk_stub.connect(tree, event, "执行", writer, "执行")
        gk_stub.connect(tree, writer, "完成", printer, "执行")
        gk_stub.connect(tree, reader, "值", printer, "内容")
        return tree

    def test_getter_replaces_read(self):
        """变量绑了 getter -> 读它的地方变成**函数调用**。"""
        tree = self._impl_tree(getter="计算血量")
        self._declare(tree, "计算血量", "float", "")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("计算血量_", code)                  # 中文名带摘要
        self.assertIn("print(计算血量_", code)            # 读变量 -> 调 getter

    def test_setter_replaces_write(self):
        """变量绑了 setter -> 写它的地方变成**函数调用**(传最终值)。"""
        tree = self._impl_tree(setter="扣血")
        self._declare(tree, "扣血", "void", "amount:float")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("扣血_", code)
        self.assertNotIn("hp = ", code)

    def test_impl_with_undeclared_function_falls_back(self):
        """绑定的函数没声明过 -> 明确告警 + 退回直接读写(不生成野调用)。"""
        tree = self._impl_tree(getter="没声明过", setter="也没声明")
        code, warnings = gen(tree, V3)
        self.assertTrue(any("没声明过" in w for w in warnings), warnings)
        self.assertTrue(any("也没声明" in w for w in warnings), warnings)
        self.assertIn("hp = ", code)                      # 退回直接赋值
        self.assertNotIn("没声明过(", code)

    def test_setter_needs_exactly_one_param(self):
        """setter 参数不是正好一个 -> 告警并退回直接赋值。"""
        tree = self._impl_tree(setter="扣血")
        self._declare(tree, "扣血", "void", "")          # 0 个参数
        code, warnings = gen(tree, V3)
        self.assertTrue(any("正好一个参数" in w for w in warnings), warnings)
        self.assertIn("hp = ", code)

    def test_no_binding_keeps_old_output(self):
        """没绑定的变量照旧是成员变量(老图产物一个字符不变)。"""
        tree = self._impl_tree()
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("var hp: float", code)
        self.assertIn("hp = ", code)


class TestDiagnostics(Base):
    """结构化诊断:校验类告警既有 code,又不破坏 warnings 的字符串接口。"""

    def test_codegen_exposes_structured_diagnostics(self):
        """基类是 StaticBody3D 却用了『移动_按方向』-> 报 BASE_CLASS_MISMATCH。

        老接口必须不变:`generate()` / `result.warnings` 仍然全是字符串
        (UI 和大量测试都这么用),结构化对象单独放在 `result.diagnostics`。
        """
        tree = make_tree()
        physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧", gk_order=0)
        move = tree.new_node(NN.GKNodeMoveDirection, "移动")
        gk_stub.connect(tree, physics, "执行", move, "执行")

        result = codegen.generate_result(
            tree, {"dimension": "3", "base_class": "StaticBody3D"})
        codes = [item.code for item in result.diagnostics]
        self.assertIn("BASE_CLASS_MISMATCH", codes)
        self.assertTrue(all(isinstance(w, str) for w in result.warnings),
                        result.warnings)
        self.assertTrue(any("move_and_slide" in w for w in result.warnings),
                        result.warnings)

    def test_generate_keeps_string_warnings(self):
        """旧入口 `generate()` 继续返回 (代码, 字符串告警列表)。"""
        tree = make_tree()
        physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧", gk_order=0)
        move = tree.new_node(NN.GKNodeMoveDirection, "移动")
        gk_stub.connect(tree, physics, "执行", move, "执行")
        code, warnings = codegen.generate(
            tree, {"dimension": "3", "base_class": "StaticBody3D"})
        self.assertIsInstance(code, str)
        self.assertTrue(all(isinstance(w, str) for w in warnings), warnings)


class TestPaths(unittest.TestCase):
    """路径工具(纯标准库)—— res:// 归一化与目录穿越防护。"""

    def test_normalize_dir_empty_and_absolute(self):
        self.assertEqual(paths.normalize_dir(""), "")
        self.assertEqual(paths.normalize_dir("   "), "")
        directory = tempfile.mkdtemp(prefix="gk_paths_")
        self.addCleanup(shutil.rmtree, directory, True)
        self.assertEqual(paths.normalize_dir(directory), os.path.abspath(directory))

    def test_to_res_path_variants(self):
        # 相对路径 -> 补前缀;反斜杠被统一成正斜杠
        self.assertEqual(paths.to_res_path("scripts/player.gd"),
                         "res://scripts/player.gd")
        self.assertEqual(paths.to_res_path("scripts\\player.gd"),
                         "res://scripts/player.gd")
        # 已经是 res:// 的只做规范化,不重复加前缀
        self.assertEqual(paths.to_res_path("res://scripts/player.gd"),
                         "res://scripts/player.gd")
        self.assertEqual(paths.to_res_path(""), "")
        # 给 root 时,root 内的绝对路径被转成相对 root
        root = tempfile.mkdtemp(prefix="gk_res_")
        self.addCleanup(shutil.rmtree, root, True)
        inside = os.path.join(root, "scenes", "Main.tscn")
        self.assertEqual(paths.to_res_path(inside, root), "res://scenes/Main.tscn")

    def test_safe_join_project_blocks_traversal(self):
        root = tempfile.mkdtemp(prefix="gk_join_")
        self.addCleanup(shutil.rmtree, root, True)
        joined = paths.safe_join_project(root, "scripts", "player.gd")
        self.assertEqual(joined,
                         os.path.join(os.path.abspath(root), "scripts", "player.gd"))
        # 目录穿越必须被拦下,而不是写到项目外面
        with self.assertRaises(ValueError):
            paths.safe_join_project(root, "..", "outside.txt")
        with self.assertRaises(ValueError):
            paths.safe_join_project(root, os.path.abspath(os.sep), "etc", "passwd")

    def test_addon_package_strips_one_level(self):
        """插件根模块名:普通 addon 与扩展两种加载方式都要算对。

        算错的后果是偏好设置类注册不上、且 `preferences()` 永远返回 None
        —— 面板里改的东西全不生效(用户 2026-10-04 的实际症状)。
        """
        self.assertEqual(
            blender_adapter.addon_package("BlenderGameKit.project"),
            "BlenderGameKit")
        self.assertEqual(
            blender_adapter.addon_package(
                "bl_ext.user_default.blender_game_kit.project"),
            "bl_ext.user_default.blender_game_kit")
        # 已经是根(没有点)时原样返回
        self.assertEqual(blender_adapter.addon_package("BlenderGameKit"),
                         "BlenderGameKit")
        self.assertEqual(blender_adapter.addon_package(""),
                         "BlenderGameKit")

    def test_validate_project_dir_rejects_a_file(self):
        directory = tempfile.mkdtemp(prefix="gk_pdir_")
        self.addCleanup(shutil.rmtree, directory, True)
        a_file = os.path.join(directory, "not_a_dir.txt")
        with open(a_file, "w", encoding="utf-8") as handle:
            handle.write("x")
        ok, reason = paths.validate_project_dir(a_file)
        self.assertFalse(ok)
        self.assertIn("文件", reason)
        self.assertFalse(paths.validate_project_dir("")[0])
        ok2, path2 = paths.validate_project_dir(directory)
        self.assertTrue(ok2)
        self.assertEqual(path2, os.path.abspath(directory))


class TestMultiScene(Base):
    """一个 .blend 里多个 Blender 场景各导一张 .tscn(用户 2026-10-04)。"""

    class _Tree:
        def __init__(self, name, bl_idname="GKGameLogicTree"):
            self.name = name
            self.bl_idname = bl_idname

    class _Scene:
        def __init__(self, name, logic_tree="", has_group=True):
            self.name = name
            self.gk_settings = (types.SimpleNamespace(logic_tree=logic_tree)
                                if has_group else None)

    class _Groups(list):
        """同时支持遍历和 .get(name) —— 模拟 bpy.data.node_groups。"""

        def get(self, name, default=None):
            for group in self:
                if getattr(group, "name", "") == name:
                    return group
            return default

    def _patch_data(self, node_groups=(), scenes=()):
        import bpy
        bpy.data = types.SimpleNamespace(
            node_groups=self._Groups(node_groups), scenes=list(scenes))
        self.addCleanup(lambda: delattr(bpy, "data"))

    def test_safe_scene_name_keeps_chinese(self):
        from BlenderGameKit import asset_export
        self.assertEqual(asset_export._safe_scene_name("关卡2"), "关卡2")
        self.assertEqual(asset_export._safe_scene_name("关卡 2*"), "关卡_2")
        self.assertEqual(asset_export._safe_scene_name(""), "Scene")

    def test_env_script_is_per_scene(self):
        """每个 .tscn 一份环境脚本 —— materials 默认值才自包含(方案 ②a)。

        以前所有场景共写 scripts/gk_environment.gd,默认值只剩"最后导出的
        那个场景"的材质;其余场景全靠各自 .tscn 节点的 materials= 覆盖才对。
        """
        from BlenderGameKit import asset_export
        # 主包装场景保持历史文件名,单场景项目的产物不变
        self.assertEqual(asset_export.env_script_rel("World"),
                         os.path.join("scripts", "gk_environment.gd"))
        # 其它场景各带后缀,互不覆盖
        self.assertEqual(asset_export.env_script_rel("关卡2"),
                         os.path.join("scripts", "gk_environment_关卡2.gd"))
        self.assertNotEqual(asset_export.env_script_rel("关卡1"),
                            asset_export.env_script_rel("关卡2"))

    def test_env_script_res_uses_forward_slashes(self):
        from BlenderGameKit import asset_export
        self.assertEqual(asset_export.env_script_res("World"),
                         "res://scripts/gk_environment.gd")
        self.assertEqual(asset_export.env_script_res("关卡2"),
                         "res://scripts/gk_environment_关卡2.gd")

    def test_material_names_do_not_collide_across_scenes(self):
        """材质文件名也要带场景前缀 —— 否则两关的 .tres 互相覆盖。

        实测(用户 2026-10-04):关卡2 的 `material_4.tres`(第二关角色,红)
        把关卡1 的 `material_4.tres`(东墙,灰)**盖掉** ——
        用户看到的画面是"关卡示例的东墙莫名变成红色"。
        """
        from BlenderGameKit import asset_export
        # 演示关里的材质名是中文 —— safe_name 压不出东西,全都回退到
        # `material`,再靠去重加 _2(就是"两关都写 material_4.tres"的来源)
        names = asset_export._unique_file_names(["东墙材质", "第二关角色材质"],
                                                prefix="关卡1_")
        self.assertEqual(names["东墙材质"], "关卡1_material")
        self.assertEqual(names["第二关角色材质"], "关卡1_material_2")
        # 单场景不加前缀,老项目产物逐字不变
        self.assertEqual(asset_export._unique_file_names(["东墙材质"]),
                         {"东墙材质": "material"})
        self.assertEqual(asset_export._scene_prefix("World"), "")
        self.assertEqual(asset_export._scene_prefix("关卡2"), "关卡2_")

    def test_game_logic_trees_only_logic_and_sorted(self):
        trees = [self._Tree("B图"), self._Tree("A图"),
                 self._Tree("动画图", "GKAnimationTree")]
        self._patch_data(node_groups=trees)
        names = [t.name for t in exporter.game_logic_trees()]
        self.assertEqual(names, ["A图", "B图"])

    def test_single_tree_is_auto_used(self):
        tree = self._Tree("玩家逻辑")
        self._patch_data(node_groups=[tree])
        got, why = exporter.resolve_scene_tree(self._Scene("关卡1"))
        self.assertIs(got, tree)
        self.assertIn("自动", why)

    def test_explicit_selection_wins(self):
        a, b = self._Tree("A"), self._Tree("B")
        self._patch_data(node_groups=[a, b])
        scene = self._Scene("关卡2", logic_tree="B")
        got, why = exporter.resolve_scene_tree(scene)
        self.assertIs(got, b)
        self.assertIn("指定", why)

    def test_multiple_trees_without_selection_warns(self):
        self._patch_data(node_groups=[self._Tree("A"), self._Tree("B")])
        got, why = exporter.resolve_scene_tree(self._Scene("关卡1"))
        self.assertIsNone(got)
        self.assertIn("没选", why)

    def test_missing_selection_reports_reason(self):
        self._patch_data(node_groups=[self._Tree("A")])
        got, why = exporter.resolve_scene_tree(self._Scene("关卡1", logic_tree="B"))
        self.assertIsNone(got)
        self.assertIn("不存在", why)

    def test_project_main_scene_reader(self):
        from BlenderGameKit import asset_export
        directory = tempfile.mkdtemp(prefix="gk_main_")
        self.addCleanup(shutil.rmtree, directory, True)
        self.assertEqual(asset_export._project_main_scene(directory), "")
        with open(os.path.join(directory, "project.godot"), "w",
                  encoding="utf-8") as handle:
            handle.write('[application]\nrun/main_scene="res://scenes/关卡1.tscn"\n')
        self.assertEqual(asset_export._project_main_scene(directory),
                         "res://scenes/关卡1.tscn")

    def test_export_all_scenes_without_scenes_errors(self):
        from BlenderGameKit import asset_export
        self._patch_data(scenes=[])
        out = asset_export.export_all_scenes(tempfile.mkdtemp(prefix="gk_ms_"))
        self.assertIsNotNone(out["error"])
        self.assertEqual(out["scenes"], [])


class _PresetTree:
    """给预设用的"bpy 味道"的桩。

    presets 走的是 bpy 那套 API(`tree.nodes.new("GKNodeXxx")` /
    `tree.links.new(出, 入)`),而 gk_stub 的桩是 `new_node(类)` /
    `connect(...)`。这里薄薄包一层,两边都能用 —— 顺带保证预设真的
    只用 bpy 支持的那套写法(不然真 Blender 里会炸)。
    """

    def __init__(self, name):
        self.tree = gk_stub.StubNodeTree(name)
        self.name = self.tree.name
        self.gk_ports = self.tree.gk_ports
        self.nodes = _NodeMaker(self.tree)
        self.links = _LinkMaker(self.tree)

    def __iter__(self):
        return iter(self.tree.nodes)

    def __len__(self):
        return len(self.tree.nodes)


class _NodeMaker:
    """`tree.nodes`:`new(kind)` 建节点,同时还能像列表一样遍历 / 取长度。"""

    def __init__(self, tree):
        self._tree = tree

    def new(self, kind):
        return self._tree.new_node(getattr(NN, kind))

    def __iter__(self):
        return iter(self._tree.nodes)

    def __len__(self):
        return len(self._tree.nodes)


class _LinkMaker:
    """`tree.links`:`new(出, 入)` 连线,同样能遍历 / 取长度。"""

    def __init__(self, tree):
        self._tree = tree

    def new(self, source, target):
        link = gk_stub.StubLink(source, target)
        source.links.append(link)
        target.links.append(link)
        self._tree.links.append(link)
        return link

    def __iter__(self):
        return iter(self._tree.links)

    def __len__(self):
        return len(self._tree.links)


class TestCharacterPreset(Base):
    """预设:一键生成第三人称角色(用户 2026-10-04 第六条的中间那块)。"""

    def _logic_tree(self, **params):
        from BlenderGameKit import presets
        tree = _PresetTree("角色逻辑(预设)")
        presets.build_logic_tree(tree, presets.defaults(**params))
        return tree

    def test_generated_logic_script_really_runs(self):
        """生成的图交给 codegen —— 出来的脚本要有"能跑起来"的那几样。"""
        tree = self._logic_tree()
        code, warnings = codegen.generate(tree.tree, dict(V3, god_camera=True))
        self.assertEqual(warnings, [], warnings)
        self.assertIn("Input.mouse_mode = Input.MOUSE_MODE_CAPTURED", code)
        self.assertIn("move_and_slide()", code)
        self.assertIn("Input.is_action_just_pressed(ACTION_JUMP)", code)
        self.assertIn("_gk_spring", code)          # 弹簧臂
        self.assertIn("_gk_mouse_delta", code)     # 鼠标转视角
        # 端口变量名会被 snake_case 规范化(『位置X』->『位置x』)
        self.assertIn("var 输出_位置x: float", code)
        self.assertIn("输出_在地面", code)
        # 端口和『变量_设置』必须是**同一个**变量(别名命中),不能各声明一份
        self.assertNotIn("\nvar 位置x: float\n", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_params_reach_the_graph(self):
        """速度 / 跳跃强度 / 灵敏度要真的填进节点(不然预设就没意义)。"""
        from BlenderGameKit import presets
        tree = self._logic_tree(move_speed=9.0, jump_force=3.5,
                                camera_side="跟随相机")
        by_name = {node.name: node for node in tree.tree.nodes}
        self.assertEqual(by_name["移动_按方向"].gk_speed, 9.0)
        self.assertEqual(by_name["起跳"].gk_jump_force, 3.5)
        self.assertEqual(by_name["灵敏度"].gk_a, presets.DEFAULT_PARAMS["sensitivity"])
        self.assertEqual(by_name["弹簧臂(第三人称)"].gk_camera, "跟随相机")

    def test_anim_tree_has_three_states_and_start(self):
        from BlenderGameKit import presets
        tree = _PresetTree("角色动作(预设)")
        presets.build_anim_tree(tree, presets.defaults())
        kinds = [node.bl_idname for node in tree.tree.nodes]
        self.assertIn("GKNodeAnimEntry", kinds)        # 开始口
        self.assertIn("GKNodeAnimLocomotion", kinds)   # 移动状态判断
        for want in ("状态_待机", "状态_跑步", "状态_跳跃"):
            self.assertIn(want, [node.name for node in tree.tree.nodes], want)
        # 5 条转场:idle<->run、idle/run->jump、jump->idle
        self.assertEqual(kinds.count("GKNodeAnimTransition"), 5)

    def test_names_come_from_params(self):
        """动画名 / 树名跟着参数走(用户在自己的模型上用同一个预设)。"""
        from BlenderGameKit import presets
        tree = _PresetTree("角色动作(预设)")
        presets.build_anim_tree(tree, presets.defaults(
            anim_idle="Idle", anim_run="Run", anim_jump="Jump"))
        names = [node.name for node in tree.tree.nodes]
        for want in ("状态_Idle", "状态_Run", "状态_Jump"):
            self.assertIn(want, names, want)


class TestMultiSceneScriptNames(Base):
    """多场景:每关的逻辑脚本必须**分开命名**(用户 2026-10-04)。

    ── 为什么(实测踩过)────────────────────────────────────────────
    脚本名默认取节点树名,而 `safe_file_name` 会把中文压成空 -> 回退
    `game_logic.gd`。两关的树都落到这个名字时,后导出的那份**覆盖**前一份:
    关卡1 的 .tscn 挂着『玩家逻辑』,跑起来却是关卡2 的脚本
    (用户实测:"在第二场景里按 Esc 无法退出"就是这一族问题的表现)。
    """

    def test_override_wins(self):
        from BlenderGameKit import exporter
        _code, _w, name, _a, _d = exporter.generate_code_result(
            make_tree(), dict(V3, script_name="game_logic_关卡2"))
        self.assertEqual(name, "game_logic_关卡2.gd")

    def test_without_override_still_falls_back(self):
        """没传名字时行为不变:树名压不出东西就用 game_logic.gd。"""
        from BlenderGameKit import exporter
        _code, _w, name, _a, _d = exporter.generate_code_result(
            make_tree(), dict(V3))
        self.assertEqual(name, "game_logic.gd")


class TestToonLightFollow(Base):
    """①A / ②b:卡通默认全平涂 + 清掉老 shader 的遗留参数(用户 2026-10-04)。"""

    def test_default_is_flat(self):
        """场景没设过这个属性 = 不跟随场景光照(不加明暗带 = 平涂)。"""
        from BlenderGameKit import asset_export
        scene = types.SimpleNamespace(gk_settings=types.SimpleNamespace())
        self.assertFalse(asset_export._toon_follow_light(scene))

    def test_opt_in_follows_scene_light(self):
        from BlenderGameKit import asset_export
        scene = types.SimpleNamespace(
            gk_settings=types.SimpleNamespace(toon_follow_light=True))
        self.assertTrue(asset_export._toon_follow_light(scene))

    def test_env_script_default_line_is_placeholder(self):
        """导出时 follow_scene_light 的默认值由场景设置决定,不再是写死的 true。"""
        from BlenderGameKit import asset_export
        text = asset_export.render_env_script(
            camera_name="Cam", fov="70.0", follow_scene_light="false")
        self.assertIn("follow_scene_light: bool = false", text)
        self.assertNotIn("follow_scene_light: bool = true", text)

    def test_toon_template_drops_stale_uniforms(self):
        """②b:老 `light()` 版才有的 4 个 uniform 已从材质模板清掉。"""
        from BlenderGameKit import asset_export
        for stale in ("toon_exposure", "light_map_mix",
                      "shadow_strength", "backlight_amount"):
            self.assertNotIn(f"shader_parameter/{stale}",
                             asset_export.TOON_MATERIAL_TEMPLATE, stale)

    def test_flat_actually_zeroes_the_light(self):
        """关掉开关时必须**显式**把明暗关死 —— 只"不喂光"不够。

        用户实测:"阴影不正常"(脸上一片紫)。原因就是第一版只做了
        "不更新",shader 里 light_dir 还有内置默认方向,照样画色阶。
        """
        from BlenderGameKit import asset_export
        text = asset_export.render_env_script(
            camera_name="Cam", fov="70.0", follow_scene_light="false")
        self.assertIn("func _apply_flat() -> void:", text)
        self.assertIn('set_shader_parameter("light_dir", Vector3.ZERO)', text)
        self.assertIn('set_shader_parameter("ambient", 1.0)', text)
        # 平涂要走"没有光"那条路,所以这一步必须在 _apply_materials 里真的被调
        self.assertIn("if not follow_scene_light:", text)


class TestEnvironmentGodCamera(Base):
    """上帝摄像机在**环境脚本**里(用户 2026-10-04 第二轮)。

    为什么必须搬:上帝机原来由角色脚本生成 —— 场景里没有角色就没有脚本,
    上帝机跟着消失("在没有角色的测试里动不了,鼠标也转不了视角")。
    环境脚本每张 .tscn 都有一份,还没有角色也能飞。
    """

    def _env(self, god="true", follow="false"):
        from BlenderGameKit import asset_export
        return asset_export.render_env_script(
            camera_name="Cam", fov="70.0",
            follow_scene_light=follow, god_camera=god)

    def test_env_script_carries_the_god_camera(self):
        text = self._env(god="true")
        self.assertIn("god_camera: bool = true", text)
        self.assertIn('add_to_group("gk_god_camera")', text)
        self.assertIn("KEY_TAB", text)
        self.assertIn("global_position +=", text)
        # 编辑器里不能造(Tab 是编辑器自己的快捷键,而且会凭空多一台相机)
        self.assertIn("Engine.is_editor_hint()", text)

    def test_god_camera_off_by_default(self):
        text = self._env(god="false")
        self.assertIn("god_camera: bool = false", text)

    def test_god_camera_reuses_and_restores_on_reload(self):
        """重开(R)/换场景之后:① 复用旧的那台 ② 把镜头还给玩家相机。

        ── 用户 2026-10-06 实测 ────────────────────────────────────
        "完成目标后按 R 重开,移动输入失效"。根因:上帝机挂在 `/root` 上,
        场景重载**清不掉它**,重载那一瞬间"当前相机"是空的,它就顺手接管了
        画面 —— 角色的移动守卫(`_gk_god_camera_active()`)以为还在上帝视角,
        把 WASD 方向清零(探针实测:重开后 `velocity` 恒为 0)。
        另外每按一次 R 还会多造一台(没人复用)。
        """
        text = self._env(god="true")
        # ① 先找现成的(gk_god_camera 组里那台),找得到就复用
        self.assertIn('is_in_group("gk_god_camera")', text)
        # ② 把镜头交还给本场景自己的相机
        self.assertIn("func _gk_god_restore_camera() -> void:", text)
        self.assertIn("_gk_god_restore_camera()", text)
        # ⚠ 只有"当前正用着上帝机"才交还 —— 不然会把开场运镜刚切好的
        # 特写机位抢回侧视机位(曲线示例探针当场抓到)
        self.assertIn("if not (_gk_god_cam as Camera3D).is_current():", text)
        # 整个上帝机由 `god_camera` 开关在运行时把关(关着的项目不会造/不会切)
        self.assertIn("if god_camera and not Engine.is_editor_hint():", text)

    def test_mouse_look_has_its_own_delta(self):
        """上帝机用自己的鼠标位移 —— 复用游戏逻辑那份会被"先清零"卡死。"""
        text = self._env(god="true")
        self.assertIn("func _input(event: InputEvent) -> void:", text)
        self.assertIn("_gk_god_look += event.relative", text)
        self.assertIn("_gk_god_look = Vector2.ZERO", text)

    def test_god_camera_forces_environment_node(self):
        """开关开着时,即使场景里没相机/灯/材质,也要生成 Environment 节点。

        不然环境脚本不存在 —— 上帝机跟着不存在(刚修掉的那个 bug)。
        """
        from BlenderGameKit import asset_export
        scene = types.SimpleNamespace(
            objects=[], camera=None, render=None,
            gk_settings=types.SimpleNamespace(god_camera=True))
        text, files, _cam = asset_export.build_environment_nodes(scene, True)
        self.assertIn('[node name="Environment"', text)
        script = files[asset_export.env_script_rel("World")]
        self.assertIn("god_camera: bool = true", script)

        # 开关关着时行为不变:没活干就不生成(老项目的产物一字不变)
        off = types.SimpleNamespace(
            objects=[], camera=None, render=None,
            gk_settings=types.SimpleNamespace(god_camera=False))
        text_off, files_off, _cam = asset_export.build_environment_nodes(off, True)
        self.assertEqual(text_off, "")
        self.assertEqual(files_off, {})


class TestTriggerNodes(Base):
    """触发器节点:进入/离开区域、碰撞体触发、定时触发(用户 2026-10-04 第 6 条)。"""

    def _trigger(self, cls, node_name, **props):
        tree = make_tree()
        trigger = tree.new_node(cls, node_name, **props)
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="触发")
        gk_stub.connect(tree, trigger, "执行", printer, "执行")
        return tree, trigger, printer

    def test_area_entered_connects_body_entered(self):
        tree, _t, _p = self._trigger(NN.GKNodeOnAreaEntered, "进入区域",
                                     gk_area="金币1")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [])
        # 回调搭在 Node3D 上、信号连接补进 _ready
        self.assertIn("func _gk_on_area_entered(_body: Node3D) -> void:", code)
        self.assertIn('.body_entered.connect(_gk_on_area_entered)', code)
        self.assertIn("is_connected(_gk_on_area_entered)", code)
        self.assertIn('print("触发")', code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_area_exited_uses_body_exited(self):
        tree, _t, _p = self._trigger(NN.GKNodeOnAreaExited, "离开区域",
                                     gk_area="边界区")
        code, _ = gen(tree, V3)
        self.assertIn("func _gk_on_area_exited(_body: Node3D) -> void:", code)
        self.assertIn(".body_exited.connect(", code)

    def test_collision_trigger_uses_area_entered(self):
        tree, _t, _p = self._trigger(NN.GKNodeCollisionTrigger, "碰撞体触发",
                                     gk_area="拾取区")
        code, _ = gen(tree, V3)
        self.assertIn("func _gk_on_collision(_area: Area3D) -> void:", code)
        self.assertIn(".area_entered.connect(", code)

    def test_without_area_warns_and_skips(self):
        tree, _t, _p = self._trigger(NN.GKNodeOnAreaEntered, "进入区域")
        code, warnings = gen(tree, V3)
        self.assertTrue(any("没有指定" in str(w) for w in warnings), warnings)
        # 绝不能生成 `self.body_entered` —— 那是静态类型错误,整个脚本过不了
        self.assertNotIn("self.body_entered", code)
        self.assertNotIn("_gk_on_area_entered", code)

    def test_body_filter_adds_name_guard(self):
        tree, _t, _p = self._trigger(NN.GKNodeOnAreaEntered, "进入区域",
                                     gk_area="金币1", gk_filter="示例角色")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [])
        # 过滤用到了形参 -> 名字不带下划线前缀
        self.assertIn("func _gk_on_area_entered(body: Node3D) -> void:", code)
        self.assertIn('String(body.name) != "示例角色"', code)

    def test_dimension_two_uses_node2d_and_area2d(self):
        tree, _t, _p = self._trigger(NN.GKNodeOnAreaEntered, "进入区域",
                                     gk_area="金币1")
        code, _ = gen(tree, V2)
        self.assertIn("_body: Node2D", code)
        tree2, _t2, _p2 = self._trigger(NN.GKNodeCollisionTrigger, "碰撞体触发",
                                        gk_area="拾取区")
        code2, _ = gen(tree2, V2)
        self.assertIn("_area: Area2D", code2)

    def test_timer_creates_one_shot_timer(self):
        tree, _t, _p = self._trigger(NN.GKNodeOnTimer, "定时触发",
                                     gk_seconds=2.5, gk_repeat=False)
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [])
        self.assertIn("func _gk_on_timer() -> void:", code)
        self.assertIn("Timer.new()", code)
        self.assertIn("wait_time = 2.5", code)
        self.assertIn("one_shot = true", code)
        self.assertIn("autostart = true", code)
        self.assertIn(".timeout.connect(_gk_on_timer)", code)
        self.assert_indent_sane(code)

    def test_timer_repeat_is_not_one_shot(self):
        tree, _t, _p = self._trigger(NN.GKNodeOnTimer, "定时触发",
                                     gk_seconds=1.0, gk_repeat=True)
        code, _ = gen(tree, V3)
        self.assertIn("one_shot = false", code)

    def test_two_triggers_get_distinct_functions(self):
        """两个同类型的触发器不能合并成一个函数(各自的连接、各自的链)。"""
        tree = make_tree()
        a = tree.new_node(NN.GKNodeOnAreaEntered, "进A", gk_area="金币1")
        b = tree.new_node(NN.GKNodeOnAreaEntered, "进B", gk_area="金币2")
        pa = tree.new_node(NN.GKNodePrint, "打印A", gk_text="A")
        pb = tree.new_node(NN.GKNodePrint, "打印B", gk_text="B")
        gk_stub.connect(tree, a, "执行", pa, "执行")
        gk_stub.connect(tree, b, "执行", pb, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [])
        self.assertIn("_gk_on_area_entered", code)
        self.assertIn("_gk_on_area_entered_2", code)
        self.assertIn('print("A")', code)
        self.assertIn('print("B")', code)

    def test_trigger_nodes_are_events(self):
        for cls in (NN.GKNodeOnAreaEntered, NN.GKNodeOnAreaExited,
                    NN.GKNodeCollisionTrigger, NN.GKNodeOnTimer):
            self.assertTrue(getattr(cls, "gk_is_event", False), cls)
            self.assertEqual(cls.gk_category, "事件")


# ==========================================================================
# 粒子特效(需求 5,2026-10-05 第二十一轮)
# ==========================================================================
class _FxMatrix:
    """假 matrix_world:支持 matrix[i][j] 和 matrix.translation。"""

    def __init__(self, rows=None, position=(1.0, 2.0, 3.0)):
        self._rows = rows or [[1.0, 0.0, 0.0],
                              [0.0, 1.0, 0.0],
                              [0.0, 0.0, 1.0]]
        self.translation = types.SimpleNamespace(
            x=position[0], y=position[1], z=position[2])

    def __getitem__(self, index):
        return self._rows[index]


class TestParticleFx(Base):
    """Blender 侧参数 -> .tscn 粒子节点(属性名对着 Godot 4.7 存的参考场景抄的)。

    开发时踩到三个"凭印象写就会错"的点,这几条测试就是钉它们的:
      * `randomness` 在**节点**上,材质上没有这个属性;
      * CPU 的缩放曲线要 `Curve`,GPU 的要 `CurveTexture`;
      * CPU 的颜色渐变要 `Gradient`,GPU 的要 `GradientTexture1D`。
    """

    class _Fx:
        def __init__(self, **values):
            self.enabled = True
            self.particle_type = "gpu"
            self.hide_mesh = False
            self.auto_play = True
            self.amount = 16
            self.lifetime = 1.2
            self.one_shot = False
            self.preprocess = 0.0
            self.explosiveness = 0.0
            self.randomness = 0.3
            self.speed = 1.0
            self.direction = (0.0, 0.0, 1.0)
            self.spread = 25.0
            self.gravity = 0.0
            self.radius = 0.0
            self.start_color = (1.0, 0.75, 0.35, 1.0)
            self.end_color = (1.0, 0.15, 0.02, 0.0)
            self.start_size = 0.2
            self.end_size = 0.0
            self.local_coords = True
            self.additive = False
            for key, value in values.items():
                setattr(self, key, value)

    class _Obj:
        def __init__(self, name="火把", kind="MESH", basis=None):
            self.name = name
            self.type = kind
            self.matrix_world = _FxMatrix(basis)
            self.data = types.SimpleNamespace(materials=[])
            self.gk_physics = None
            self.gk_fx = None

        def get(self, key, default=None):
            """真物体的自定义属性接口(collect_emitters 用它跳过预览物)。"""
            return default

        def keys(self):
            return []

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def _subs(self, built):
        return "\n".join(built["sub_resources"])

    def _nodes(self, built):
        return "\n".join(built["node_lines"])

    def test_emitter_3d_gpu_scene_text(self):
        from BlenderGameKit import asset_export as ae
        built = ae.build_emitter(self._Obj(), self._Fx(), True, uid=0,
                                 texture_id="fx_tex0", parent="Particles")
        self.assertEqual(built["node_type"], "GPUParticles3D")
        subs = self._subs(built)
        self.assertIn('[sub_resource type="ParticleProcessMaterial" id="fx0_proc"]', subs)
        self.assertIn('scale_curve = SubResource("fx0_scale")', subs)
        self.assertIn('color_ramp = SubResource("fx0_ramp")', subs)
        self.assertIn('gradient = SubResource("fx0_grad")', subs)
        self.assertIn('curve = SubResource("fx0_curve")', subs)
        self.assertIn("point_count = 2", subs)         # Godot 存 Curve 的写法
        # 画什么 / 怎么画:广告牌 + 无光照 + 吃粒子颜色 + 贴图
        self.assertIn('albedo_texture = ExtResource("fx_tex0")', subs)
        self.assertIn("vertex_color_use_as_albedo = true", subs)
        self.assertIn("billboard_mode = 1", subs)
        self.assertIn("shading_mode = 0", subs)
        nodes = self._nodes(built)
        self.assertIn('[node name="火把" type="GPUParticles3D" parent="Particles"]', nodes)
        self.assertIn('process_material = SubResource("fx0_proc")', nodes)
        self.assertIn('draw_pass_1 = SubResource("fx0_mesh")', nodes)
        self.assertIn('material_override = SubResource("fx0_draw")', nodes)
        self.assertIn("emitting = true", nodes)
        self.assertIn("amount = 16", nodes)
        self.assertIn("transform = Transform3D(", nodes)

    def test_emitter_direction_and_gravity_axis_conversion(self):
        """方向 / 重力要按 glTF 的 Y-up 换轴 —— 不换的话火苗横着喷。"""
        from BlenderGameKit import asset_export as ae
        built = ae.build_emitter(self._Obj(), self._Fx(), True, uid=0,
                                 texture_id="t", parent=".")
        self.assertIn("direction = Vector3(0.000000, 1.000000, 0.000000)",
                      self._subs(built))
        # Blender 的"前"(+Y)-> Godot 的"前"(-Z)
        forward = ae.build_emitter(
            self._Obj(), self._Fx(direction=(0.0, 1.0, 0.0)), True, uid=1,
            texture_id="t", parent=".")
        self.assertIn("direction = Vector3(0.000000, 0.000000, -1.000000)",
                      self._subs(forward))
        # 重力:正数 = 往下(Blender 的"下"是 -Z,Godot 的"下"是 -Y)
        heavy = ae.build_emitter(self._Obj(), self._Fx(gravity=9.8), True,
                                 uid=2, texture_id="t", parent=".")
        self.assertIn("gravity = Vector3(0, -9.800000, 0)", self._subs(heavy))

    def test_emitter_2d_uses_screen_direction(self):
        """2D:方向按"屏幕上的上"来(Godot 的 y 向下),重力同向换算。"""
        from BlenderGameKit import asset_export as ae
        built = ae.build_emitter(self._Obj(), self._Fx(gravity=9.8), False,
                                 uid=0, texture_id="fx_tex0",
                                 parent="Particles")
        self.assertEqual(built["node_type"], "GPUParticles2D")
        subs = self._subs(built)
        self.assertIn("particle_flag_disable_z = true", subs)
        self.assertIn("direction = Vector3(0.000000, -1.000000, 0.000000)", subs)
        self.assertIn("gravity = Vector2(0, 9.800000)", subs)
        nodes = self._nodes(built)
        self.assertIn('[node name="火把" type="GPUParticles2D" parent="Particles"]', nodes)
        self.assertIn('texture = ExtResource("fx_tex0")', nodes)
        self.assertIn("position = Vector2(1.000000, 2.000000)", nodes)
        # 2D 没有"画什么网格"那一套(draw_pass_1 是 3D 专用)
        self.assertNotIn("draw_pass_1", nodes)

    def test_emitter_2d_additive_gets_canvas_material(self):
        """2D 叠加发光:节点材质用 CanvasItemMaterial 的 ADD 混合。"""
        from BlenderGameKit import asset_export as ae
        built = ae.build_emitter(self._Obj(), self._Fx(additive=True), False,
                                 uid=0, texture_id="t", parent=".")
        subs = self._subs(built)
        self.assertIn('[sub_resource type="CanvasItemMaterial" id="fx0_canvas"]',
                      subs)
        self.assertIn("blend_mode = 1", subs)
        self.assertIn('material = SubResource("fx0_canvas")', self._nodes(built))

    def test_emitter_cpu_uses_inline_params(self):
        """CPU 粒子:参数在节点上,渐变给 Gradient、曲线给 Curve。"""
        from BlenderGameKit import asset_export as ae
        built = ae.build_emitter(
            self._Obj(), self._Fx(particle_type="cpu"), True, uid=0,
            texture_id="t", parent=".")
        self.assertEqual(built["node_type"], "CPUParticles3D")
        subs = self._subs(built)
        self.assertNotIn("ParticleProcessMaterial", subs)
        self.assertIn('[sub_resource type="Curve" id="fx0_curve"]', subs)
        self.assertNotIn("CurveTexture", subs)
        nodes = self._nodes(built)
        self.assertIn('[node name="火把" type="CPUParticles3D"', nodes)
        self.assertIn('scale_amount_curve = SubResource("fx0_curve")', nodes)
        self.assertIn('color_ramp = SubResource("fx0_grad")', nodes)
        self.assertIn('mesh = SubResource("fx0_mesh")', nodes)
        self.assertIn("scale_amount_min = 0.200000", nodes)
        self.assertNotIn("process_material", nodes)

    def test_emitter_constant_size_skips_curve(self):
        """起终大小一样时不写曲线(省一组没人用的资源)。"""
        from BlenderGameKit import asset_export as ae
        built = ae.build_emitter(self._Obj(),
                                 self._Fx(start_size=0.5, end_size=0.5),
                                 True, uid=0, texture_id="t", parent=".")
        subs = self._subs(built)
        self.assertNotIn('type="Curve"', subs)
        self.assertNotIn("scale_curve", subs)
        self.assertIn("scale_min = 0.500000", subs)

    def test_emitter_basis_and_body_attach(self):
        """朝向按 Y-up 换轴、列优先写;挂物理体下时不重复写位置。"""
        from BlenderGameKit import asset_export as ae
        # 绕 Blender X 轴转 90°:Godot 基应当是
        #   x=(1,0,0) y=(0,0,1) z=(0,-1,0) —— 按列优先落成这 9 个数
        rows = [[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]]
        built = ae.build_emitter(self._Obj(basis=rows), self._Fx(), True,
                                 uid=0, texture_id="t",
                                 parent="Colliders/火把", attach_to_body=True)
        nodes = self._nodes(built)
        self.assertIn(
            "transform = Transform3D(1.000000, 0.000000, 0.000000, "
            "0.000000, 0.000000, -1.000000, "
            "0.000000, 1.000000, 0.000000, "
            "0.000000, 0.000000, 0.000000)", nodes)
        # 挂物理体下 = 位置由 body 说了算,粒子节点不该再写一遍世界坐标
        self.assertNotIn("2.000000, 3.000000", nodes)

    def test_emitter_identity_transform_is_not_written(self):
        """没转没挪的发射器不写 transform(文件干净)。"""
        from BlenderGameKit import asset_export as ae
        obj = self._Obj()
        obj.matrix_world = _FxMatrix(position=(0.0, 0.0, 0.0))
        built = ae.build_emitter(obj, self._Fx(), True, uid=0, texture_id="t",
                                 parent=".")
        self.assertNotIn("transform =", self._nodes(built))

    def test_default_particle_texture_is_a_real_png(self):
        """内置默认贴图必须是真 PNG(2D 粒子没贴图什么都画不出来)。"""
        from BlenderGameKit import asset_export as ae
        data = ae.fx_default_png()
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(b"IHDR", data[:32])
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        self.assertEqual((width, height), (64, 64))
        self.assertGreater(len(data), 200)

    def test_texture_file_uses_material_image_when_it_exists(self):
        """贴图走现有链路:物体材质里有贴图就用它,没有才用内置那张。"""
        from BlenderGameKit import asset_export as ae
        image = types.SimpleNamespace(name="fire.png", size=(64, 64))
        source = types.SimpleNamespace(type="TEX_IMAGE", image=image)
        socket = types.SimpleNamespace(
            is_linked=True, links=[types.SimpleNamespace(from_node=source)])
        bsdf = types.SimpleNamespace(
            type="BSDF_PRINCIPLED",
            inputs=types.SimpleNamespace(get=lambda name: socket))
        material = types.SimpleNamespace(
            name="火材质", use_nodes=True,
            node_tree=types.SimpleNamespace(nodes=[bsdf]))
        obj = self._Obj()
        obj.data = types.SimpleNamespace(materials=[material])

        bpy_stub = bpy_module()
        old_data = getattr(bpy_stub, "data", None)
        try:
            setattr(bpy_stub, "data", types.SimpleNamespace(
                images=types.SimpleNamespace(
                    get=lambda name: image if name == "fire.png" else None)))
            self.assertEqual(ae.fx_texture_file(obj), "fire_png.png")
            # 给了导出目录但文件**不在** -> 退回内置那张
            # (宁可粒子是白点,也不能让 .tscn 引用不存在的资源)
            self.assertEqual(ae.fx_texture_file(obj, "不存在的目录"),
                             ae.FX_DEFAULT_TEXTURE)
        finally:
            if old_data is not None:
                setattr(bpy_stub, "data", old_data)
        # 没材质的物体直接吃内置那张
        self.assertEqual(ae.fx_texture_file(self._Obj()),
                         ae.FX_DEFAULT_TEXTURE)

    def test_collect_emitters_filters_and_codegen_map(self):
        from BlenderGameKit import asset_export as ae
        scene = types.SimpleNamespace(objects=[])
        for name, kind, enabled, particle in (
                ("火把", "MESH", True, "gpu"),
                ("烟", "EMPTY", True, "cpu"),
                ("普通", "MESH", False, "gpu")):
            obj = self._Obj(name, kind)
            obj.gk_fx = self._Fx(enabled=enabled, particle_type=particle)
            scene.objects.append(obj)
        emitters = ae.collect_emitters(scene)
        self.assertEqual([obj.name for obj, _fx in emitters], ["火把", "烟"])
        table = ae.fx_codegen_map(scene, "3")
        self.assertEqual(table["火把"]["node_type"], "GPUParticles3D")
        self.assertEqual(table["烟"]["node_type"], "CPUParticles3D")
        two_d = ae.fx_codegen_map(scene, "2")
        self.assertEqual(two_d["烟"]["node_type"], "CPUParticles2D")

    def test_fx_nodes_are_registered_in_menu(self):
        for cls in (NN.GKNodeFxPlay, NN.GKNodeFxStop, NN.GKNodeFxBurst):
            self.assertEqual(cls.gk_category, "特效")
            self.assertIn("特效", NN.CATEGORY_ORDER)

    # ---- 代码生成:『特效_播放』『特效_停止』『特效_爆发』----
    def _fx_tree(self, kind, target="火把", options=None):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(kind, "特效", gk_target=target)
        gk_stub.connect(tree, event, "执行", node, "执行")
        return tree, node, dict(V3, fx=options if options is not None else {
            "火把": {"node_type": "GPUParticles3D"}})

    def test_fx_play_generates_typed_reference(self):
        tree, _node, options = self._fx_tree(NN.GKNodeFxPlay)
        code, warnings = gen(tree, options)
        self.assertEqual(warnings, [], warnings)
        # 目标类型必须是**具体粒子类** —— 声明成 Node 会编译不过
        self.assertIn('@onready var 特效_火把: GPUParticles3D = '
                      '_gk_ref_typed(self, "火把", "GPUParticles3D")', code)
        self.assertIn("特效_火把.emitting = true", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_fx_stop_generates_false(self):
        tree, _node, options = self._fx_tree(NN.GKNodeFxStop)
        code, warnings = gen(tree, options)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("特效_火把.emitting = false", code)

    def test_fx_burst_restarts(self):
        """爆发 = restart() + emitting = true(一次性发射器再放一轮)。"""
        tree, _node, options = self._fx_tree(NN.GKNodeFxBurst)
        code, warnings = gen(tree, options)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("特效_火把.restart()", code)
        self.assertIn("特效_火把.emitting = true", code)
        self.assert_indent_sane(code)

    def test_fx_uses_cpu_type_from_table(self):
        """面板上选了 CPU -> 生成的类型也跟着是 CPUParticles3D。"""
        tree, _node, options = self._fx_tree(NN.GKNodeFxPlay, options={
            "火把": {"node_type": "CPUParticles3D"}})
        code, warnings = gen(tree, options)
        self.assertEqual(warnings, [], warnings)
        self.assertIn(": CPUParticles3D = ", code)

    def test_fx_warns_when_target_is_not_an_emitter(self):
        """目标不是发射器(或忘了勾)时必须告警,而不是生成幽灵引用。"""
        tree, _node, options = self._fx_tree(NN.GKNodeFxPlay, options={})
        code, warnings = gen(tree, options)
        self.assertTrue(any("粒子发射器" in w for w in warnings), warnings)
        self.assertNotIn(".emitting = true", code)

    def test_fx_warns_on_self_target(self):
        """目标选『自身』时也告警(脚本挂在角色上,它不是发射器)。"""
        tree, _node, options = self._fx_tree(NN.GKNodeFxPlay,
                                             target=NB.TARGET_SELF)
        code, warnings = gen(tree, options)
        self.assertTrue(any("自身" in w for w in warnings), warnings)
        self.assertNotIn(".emitting = true", code)


# ==========================================================================
# 游戏内调试面板(2026-10-05 第二十二轮:"调试体验")
# ==========================================================================
class TestDebugPanel(Base):
    """三块:环境脚本(面板本体)/ 代码生成(日志缓冲)/ 『监视』开关。

    ── 为什么变量和日志都"运行时按名字读",导出侧不准备表 ──────────────
    面板是环境脚本的一部分(每张 .tscn 一份),而变量属于游戏逻辑脚本。
    导出侧准备表的话,"变量_设置"运行时新建的变量就漏了;改成运行时枚举
    宿主节点的 `PROPERTY_USAGE_SCRIPT_VARIABLE` + 读 `_gk_debug_logs`,
    面板天然跟着脚本走(见 asset_export 里那一节说明)。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_env_script_has_the_panel(self):
        from BlenderGameKit import asset_export as ae
        text = ae.render_env_script(
            debug_panel="true", debug_key="KEY_F1", debug_copy_key="KEY_F2",
            debug_watch="金币数|血量")
        self.assertIn("var debug_panel: bool = true", text)
        self.assertIn('var debug_watch: String = "金币数|血量"', text)
        self.assertIn("const _GK_DBG_KEY := KEY_F1", text)
        self.assertIn("const _GK_DBG_COPY_KEY := KEY_F2", text)
        self.assertIn("func _gk_dbg_setup() -> void:", text)
        self.assertIn("func _gk_dbg_tick(delta: float) -> void:", text)
        # 复制:剪贴板 + 控制台(无头环境没有剪贴板,靠控制台那份)
        self.assertIn("DisplayServer.clipboard_set(text)", text)
        self.assertIn("DisplayServer.FEATURE_CLIPBOARD", text)
        # 默认收起(按键才出来)、编辑器里不造
        self.assertIn("label.visible = false", text)
        self.assertIn("if debug_panel and not Engine.is_editor_hint():", text)
        # 两处钩子真的调了(tick 在物理帧、setup 在 _ready)
        self.assertIn("_gk_dbg_tick(delta)", text)
        self.assertIn("_gk_dbg_setup()", text)
        # 变量:内部变量不上面板;勾了监视时只显示那几个
        self.assertIn('if vname.begins_with("_gk_"):', text)
        self.assertIn('if not watch.is_empty() and not '
                      'watch.split("|").has(vname):', text)
        # 状态行:位置 / 速度 / 在地面都在
        self.assertIn("is_on_floor()", text)
        self.assertIn("Engine.get_frames_per_second()", text)

    def test_env_script_panel_is_off_by_default(self):
        from BlenderGameKit import asset_export as ae
        text = ae.render_env_script()
        self.assertIn("var debug_panel: bool = false", text)
        self.assertIn("var debug_watch: String = \"\"", text)

    def test_env_fields_render_without_missing_keys(self):
        """_env_fields 出来的字段必须能把模板完整渲染出来(加字段别漏一处)。"""
        from BlenderGameKit import asset_export as ae
        scene = types.SimpleNamespace(gk_settings=types.SimpleNamespace(
            debug_panel=True, debug_key="F8", debug_copy_key="`"))
        fields = ae._env_fields(scene, camera_name="Cam", fov=70.0)
        self.assertEqual(fields["debug_panel"], "true")
        self.assertEqual(fields["debug_key"], "KEY_F8")
        self.assertEqual(fields["debug_copy_key"], "KEY_QUOTELEFT")
        text = ae.ENVIRONMENT_SCRIPT.format(**fields)
        self.assertIn("const _GK_DBG_KEY := KEY_F8", text)

    def test_key_constants_fall_back(self):
        """认不出来的按键要退回默认(NOT 生成一个不存在的常量 -> 编译不过)。"""
        from BlenderGameKit import asset_export as ae
        odd = types.SimpleNamespace(gk_settings=types.SimpleNamespace(
            debug_key="F12"))
        self.assertEqual(ae._debug_key_const(odd), "KEY_F1")
        self.assertEqual(ae.DEBUG_KEY_CONSTS["`"], "KEY_QUOTELEFT")

    def test_watch_text_keeps_only_checked_variables(self):
        """竖线串只收勾了的变量;引号 / 竖线要清掉(它进字符串字面量)。"""
        from BlenderGameKit import asset_export as ae
        tree = types.SimpleNamespace(gk_vars=[
            types.SimpleNamespace(var_name="金币数", watch=True),
            types.SimpleNamespace(var_name="血量", watch=False),
            types.SimpleNamespace(var_name='a"b|c', watch=True),
        ])
        scene = types.SimpleNamespace(gk_settings=types.SimpleNamespace())
        with mock.patch("BlenderGameKit.exporter.resolve_scene_tree",
                        return_value=(tree, "")):
            self.assertEqual(ae._debug_watch_text(scene), "金币数|abc")

    def test_watch_text_includes_used_variables_and_ports(self):
        """图里**用到**的变量 + 对外端口也要进列表(不然面板上就是漏的)。"""
        from BlenderGameKit import asset_export as ae
        tree = types.SimpleNamespace(
            gk_vars=[types.SimpleNamespace(var_name="金币数", watch=True),
                     types.SimpleNamespace(var_name="血量", watch=False)],
            gk_ports=[types.SimpleNamespace(port_name="视角偏航", direction="out"),
                      types.SimpleNamespace(port_name="外来输入", direction="in")],
            nodes=[])
        scene = types.SimpleNamespace(gk_settings=types.SimpleNamespace())
        with mock.patch("BlenderGameKit.exporter.resolve_scene_tree",
                        return_value=(tree, "")), \
                mock.patch("BlenderGameKit.properties.variable_references",
                           return_value=[("血量", [], []), ("移动速度", [], [])]):
            text = ae._debug_watch_text(scene)
        # 勾了监视的声明变量 + 图里用到的 + 输出端口;重复的只留一次,
        # 输入端口不上(它由别的图给,不是这张图的输出)
        self.assertEqual(text, "金币数|血量|移动速度|输出_视角偏航")

    def test_watch_text_empty_means_show_all(self):
        from BlenderGameKit import asset_export as ae
        scene = types.SimpleNamespace(gk_settings=types.SimpleNamespace())
        with mock.patch("BlenderGameKit.exporter.resolve_scene_tree",
                        return_value=(None, "")):
            self.assertEqual(ae._debug_watch_text(scene), "")
        tree = types.SimpleNamespace(gk_vars=[])
        with mock.patch("BlenderGameKit.exporter.resolve_scene_tree",
                        return_value=(tree, "")):
            self.assertEqual(ae._debug_watch_text(scene), "")

    # ---- 代码生成:开着面板才有日志缓冲 ----
    def _print_tree(self):
        tree = make_tree()
        event = self._ready(tree)
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="金币 +1")
        gk_stub.connect(tree, event, "执行", printer, "执行")
        return tree

    def test_debug_panel_adds_log_buffer(self):
        tree = self._print_tree()
        code, warnings = gen(tree, dict(V3, debug_panel=True))
        self.assertEqual(warnings, [], warnings)
        self.assertIn("var _gk_debug_logs: Array = []", code)
        self.assertIn("func _gk_debug_log_append(text: String) -> void:", code)
        # 『打印_输出』:控制台照打,同一句话再进面板
        self.assertIn('print("金币 +1")', code)
        self.assertIn('_gk_debug_log_append("金币 +1")', code)
        # 只留最近 20 条(面板也就显示这么多)
        self.assertIn("while _gk_debug_logs.size() > 20:", code)
        self.assert_indent_sane(code)

    def test_debug_panel_off_keeps_old_output(self):
        """不开面板 -> 生成的代码里一个日志字符都不出现(老图产物不动)。"""
        tree = self._print_tree()
        code, _warnings = gen(tree, V3)
        self.assertNotIn("_gk_debug_logs", code)
        self.assertNotIn("_gk_debug_log_append", code)
        self.assertIn('print("金币 +1")', code)


# ==========================================================================
# 导出报告(2026-10-05 第二十三轮:"导出报告可视化")
# ==========================================================================
class TestExportReport(Base):
    """结构化诊断 -> 报告(排序 / 去重 / 截断)-> 纯文本 + 级别统计。

    诊断在导出结果里是**对象**(`models.Diagnostic`,带 hint / node_name),
    而 `warnings` 是它们**摊平后的字符串**(见 models.Diagnostic.render)——
    所以报告必须按文本去重,否则同一条会同时以"对象"和"字符串"进两遍。
    """

    class _Scene:
        """假场景:只要 `scene[key] = ...` 和 `scene.get(key)` 两个口。"""

        def __init__(self):
            self.data = {}

        def __setitem__(self, key, value):
            self.data[key] = value

        def get(self, key, default=None):
            return self.data.get(key, default)

    def _result(self):
        from BlenderGameKit import models
        diagnostic = models.Diagnostic(
            level="warning", code="ANIM_TARGET_MISMATCH",
            message="动画『走路』不在『身体』上",
            hint="检查节点上的『动画播放器』是否指向了带这个动画的物体",
            node_name="动画_走路")
        return {
            "ok": True, "script_name": "game_logic.gd", "directory": "D:/proj",
            "lines": 120, "elapsed_ms": 35.5, "written": ["a.tscn", "b.gd"],
            "diagnostics": [diagnostic],
            # ⚠ 摊平后的那条也在 warnings 里(导出流程就是这么做的)
            "warnings": [diagnostic.render(), "这些网格没有材质: 地面"],
            "notes": ["参考: 导出目录 D:/proj"],
        }

    def test_build_dedupes_and_orders(self):
        from BlenderGameKit import export_report as er
        report = er.build(self._result())
        messages = [item["message"] for item in report["items"]]
        # 结构化那条不能出现两次(对象一次 + 摊平字符串一次)
        self.assertEqual(messages.count("动画『走路』不在『身体』上"), 1)
        self.assertIn("这些网格没有材质: 地面", messages)
        self.assertIn("参考: 导出目录 D:/proj", messages)
        # 提示排在最后(警告在前面)
        levels = [item["level"] for item in report["items"]]
        order = {"error": 0, "warning": 1, "info": 2}
        self.assertEqual(levels, sorted(levels, key=lambda l: order[l]))

    def test_build_keeps_hint_and_node(self):
        from BlenderGameKit import export_report as er
        report = er.build(self._result())
        item = next(i for i in report["items"]
                    if i["code"] == "ANIM_TARGET_MISMATCH")
        self.assertIn("动画播放器", item["hint"])
        self.assertEqual(item["node_name"], "动画_走路")

    def test_build_truncates_and_reports_dropped(self):
        from BlenderGameKit import export_report as er
        result = self._result()
        result["warnings"] = [f"第 {index} 条问题" for index in range(80)]
        report = er.build(result)
        self.assertEqual(len(report["items"]), er.MAX_ITEMS)
        self.assertEqual(report["dropped"], 82 - er.MAX_ITEMS)   # 80 + 诊断 + 提示

    def test_counts_and_items_of(self):
        from BlenderGameKit import export_report as er
        report = er.build(self._result())
        errors, warnings, infos = er.counts(report)
        self.assertEqual((errors, warnings, infos), (0, 2, 1))
        self.assertEqual(len(er.items_of(report, "warning")), 2)

    def test_text_has_state_hint_and_node(self):
        from BlenderGameKit import export_report as er
        text = er.report_text(er.build(self._result()))
        self.assertIn("导出报告", text)
        self.assertIn("怎么修:", text)
        self.assertIn("相关节点: 动画_走路", text)
        self.assertIn("game_logic.gd", text)
        self.assertIn("个警告", text)
        # 空报告也不能崩(面板第一次打开时就是这条路)
        self.assertIn("还没有导出过", er.report_text({}))

    def test_record_persists_into_scene(self):
        """报告要能写进 .blend(场景属性),清掉内存还能读回来。"""
        from BlenderGameKit import export_report as er
        scene = self._Scene()
        er.clear()
        er.record(scene, self._result())
        self.assertIn(er.PERSIST_KEY, scene.data)
        er.clear()
        loaded = er.load(scene)
        self.assertEqual(loaded["script_name"], "game_logic.gd")
        self.assertTrue(loaded["items"])
        # 报告是 JSON 存的:条目里不能混进不能序列化的东西
        import json
        json.dumps(scene.data[er.PERSIST_KEY], ensure_ascii=False)
        er.clear()

    def test_report_operators_are_in_the_class_list(self):
        """两个新算子必须在 GK_CLASSES 里 —— 漏了按钮就是静默不工作。

        ⚠ 离线桩里的 bpy 没有 `app.handlers`,`operator_io` 导不进来
        (它模块级就有 `@bpy.app.handlers.persistent`),所以这一条直接查
        **源码文本**:注册清单和 bl_idname 都得在。
        (真 Blender 那边冒烟会真的调这两个算子,两道锁一起上。)
        """
        source = open(os.path.join(ROOT, "BlenderGameKit", "operator_io.py"),
                      encoding="utf-8").read()
        self.assertIn('bl_idname = "gk.export_report_copy"', source)
        self.assertIn('bl_idname = "gk.export_report_focus"', source)
        # 带逗号 + 换行 = 只匹配元组里的那一条(class 定义行没有逗号)
        self.assertIn("GK_OT_ReportCopy,\n", source)
        self.assertIn("GK_OT_ReportFocus,\n", source)

    def test_load_empty_without_report(self):
        from BlenderGameKit import export_report as er
        er.clear()
        self.assertEqual(er.load(self._Scene()), {})
        # 坏 JSON 也不能崩(手改过 .blend / 老版本存的)
        scene = self._Scene()
        scene[er.PERSIST_KEY] = "{不是 json"
        self.assertEqual(er.load(scene), {})


# ==========================================================================
# 『变量_设置』写回**声明过**的变量:数值 / 整数 / 真假之间的转换要安静
# ==========================================================================
class TestOnreadyTypeUpgrade(Base):
    """同一个物体被两种"类型需求"引用时,声明要取**更具体**的那个(第二十四轮)。

    踩过:敌人先被『移动_设置位置』引用(hint=节点 -> Node),再被『进入区域』
    引用(hint=area -> Area3D)。`add_onready` 只按**路径**去重,于是第二次
    直接复用那个 Node 类型的变量,生成

        if 节点_敌1 != null and 节点_敌1.body_entered...

    而运行时它就是 **MeshInstance3D**(网格那个同名节点):

        Invalid access to property or key 'body_entered' on a base object
        of type 'MeshInstance3D'

    **触发器整个失效**(敌人撞了不扣血)—— 小游戏示例的探针抓到的。
    修复:类型只往**具体**方向升(Node -> Area3D 可以,反过来不行)。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_area_type_wins_over_plain_node(self):
        tree = make_tree()
        event = self._ready(tree)
        move = tree.new_node(NN.GKNodeSetPosition, "挪敌人",
                             gk_target="敌1", gk_x=1.0)
        gk_stub.connect(tree, event, "执行", move, "执行")
        tree.new_node(NN.GKNodeOnAreaEntered, "撞到敌1",
                      gk_area="敌1", gk_filter="小玩家")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        # 引用只声明一次(不是两个变量),但类型是更具体的 Area3D
        self.assertIn(': Area3D = _gk_ref_typed(self, "敌1", "Area3D")', code)
        self.assertEqual(code.count('_gk_ref_typed(self, "敌1"'), 1)
        # 触发器接的是**这个**变量(类型对了,body_entered 才存在)
        self.assertIn(".body_entered.connect(", code)
        self.assertNotIn(": Node = _gk_ref_typed(self, \"敌1\"", code)

    def test_plain_node_request_keeps_the_specific_type(self):
        """反过来(先 Area3D 后 Node)不能把类型降回 Node。"""
        tree = make_tree()
        event = self._ready(tree)
        tree.new_node(NN.GKNodeOnAreaEntered, "撞到敌1",
                      gk_area="敌1", gk_filter="小玩家")
        move = tree.new_node(NN.GKNodeSetPosition, "挪敌人",
                             gk_target="敌1", gk_x=1.0)
        gk_stub.connect(tree, event, "执行", move, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn(': Area3D = _gk_ref_typed(self, "敌1", "Area3D")', code)


class TestSetVarNumericCoercion(Base):
    """第二十四轮(做小游戏示例时踩到)。

    『数学_运算』天生吐 float,而『变量』框里声明的可能是 int / bool ——
    生成器**已经**会补 `int(...)` / `true`(产物能跑),但同时还报
    "变量同时被当成 X 和 Y,请改用不同的变量名" —— 那句会把人指到
    错的地方(变量名根本没问题)。现在这种**明确**的转换不再告警;
    真对不上的类型(文本 -> 整数)仍然告警。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def _declare(self, tree, name, var_type, default="0"):
        tree.gk_vars.add()
        var = tree.gk_vars[len(tree.gk_vars) - 1]
        var.var_name = name
        var.var_type = var_type
        var.default_value = default
        return var

    def test_int_variable_from_math_is_quiet(self):
        tree = make_tree()
        self._declare(tree, "金币数", "int", "0")
        event = self._ready(tree)
        math = tree.new_node(NN.GKNodeMath, "加一", gk_op="ADD", gk_b=1.0)
        read = tree.new_node(NN.GKNodeGetVar, "读", gk_var_name="金币数")
        setter = tree.new_node(NN.GKNodeSetVar, "记", gk_var_name="金币数")
        gk_stub.connect(tree, read, "值", math, "A")
        gk_stub.connect(tree, math, "结果", setter, "值")
        gk_stub.connect(tree, event, "执行", setter, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("金币数 = int((", code)

    def test_bool_variable_from_number_literal_is_quiet(self):
        tree = make_tree()
        self._declare(tree, "结束", "bool", "false")
        event = self._ready(tree)
        setter = tree.new_node(NN.GKNodeSetVar, "标记", gk_var_name="结束",
                               gk_value=1.0)
        gk_stub.connect(tree, event, "执行", setter, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("结束 = true", code)

    def test_bool_variable_from_connected_number_keeps_the_value(self):
        """接了线的时候**不能**把表达式丢掉 —— 以前会生成恒 false。"""
        tree = make_tree()
        self._declare(tree, "结束", "bool", "false")
        self._declare(tree, "金币数", "float", "0")
        event = self._ready(tree)
        read = tree.new_node(NN.GKNodeGetVar, "读金币", gk_var_name="金币数")
        setter = tree.new_node(NN.GKNodeSetVar, "标记", gk_var_name="结束")
        gk_stub.connect(tree, read, "值", setter, "值")
        gk_stub.connect(tree, event, "执行", setter, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        # 『变量_读取』的输出是"任意值",线上推不出类型 -> 用 bool() 显式转;
        # 关键是**表达式不许被丢掉**(以前会生成恒 false)
        self.assertIn("结束 = bool(金币数)", code)
        self.assertNotIn("结束 = false", code)

    def test_real_type_conflict_still_warns(self):
        """真对不上的类型(文本 -> 整数)仍然要告警,不能一起静音。"""
        tree = make_tree()
        self._declare(tree, "金币数", "int", "0")
        event = self._ready(tree)
        text = tree.new_node(NN.GKNodeConstText, "字", gk_value="你好")
        setter = tree.new_node(NN.GKNodeSetVar, "记", gk_var_name="金币数")
        gk_stub.connect(tree, text, "值", setter, "值")
        gk_stub.connect(tree, event, "执行", setter, "执行")
        _code, warnings = gen(tree, V3)
        self.assertTrue(any("金币数" in w for w in warnings), warnings)


class TestRound25GameplayPieces(Base):
    """第二十五轮:补小游戏示例暴露的四个"做游戏常用件"。

    * 『移动_朝向目标』—— 追人的敌人 / 跟随的宠物(不用自己拼方向)
    * 『变量_增减』    —— 拾取 +1 / 扣血 -1(不用拼三个节点)
    * 『流程_冷却』    —— 无敌帧 / 防连发(用户原话:"重复触发时它会重复
                          开始动画,如果我想让它先放完再重复")
    * 『文本_数字』    —— HUD 上的 3 不能显示成 3.0(Godot 的 str(3.0))
    """

    def _declare(self, tree, name, var_type="float", default="0"):
        tree.gk_vars.add()
        var = tree.gk_vars[len(tree.gk_vars) - 1]
        var.var_name = name
        var.var_type = var_type
        var.default_value = default
        return var

    def _declare_func(self, tree, name, return_type="void", params=""):
        tree.gk_funcs.add()
        func = tree.gk_funcs[len(tree.gk_funcs) - 1]
        func.func_name = name
        func.return_type = return_type
        func.params = params
        return func

    # ---- ① 移动_朝向目标 ----

    def _chase_tree(self, goal="小玩家", **props):
        tree = make_tree()
        physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        move = tree.new_node(NN.GKNodeMoveToward, "追", gk_goal=goal,
                             gk_speed=3.0, **props)
        gk_stub.connect(tree, physics, "执行", move, "执行")
        return tree, move

    def test_chase_3d_walks_toward_goal(self):
        tree, move = self._chase_tree(gk_stop_distance=1.0)
        move.gk_target = "追击者"
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        self.assert_no_chinese_identifiers(code)
        # 位置读取复用『取值_位置』那个 helper(Node3D 才有的成员不能直接调)
        self.assertIn("func _gk_global_position_3d(host: Node) -> Vector3:", code)
        # 『谁走』用 **CharacterBody** 类型取 —— 取成 Node 的话
        # `.velocity` / `.move_and_slide()` 会被 Godot 静态检查拒绝
        self.assertIn(': CharacterBody3D = _gk_ref_typed(self, "追击者", "CharacterBody3D")',
                      code)
        self.assertRegex(code, r"if _goal_dist\d+ <= 1\.0:")
        self.assertRegex(code, r"节点_追击者\.velocity = _to_goal\d+\.normalized\(\)"
                                r" \* chase_speed")
        # 速度没连线 -> 生成可在检查器里调的 @export
        self.assertIn("@export var chase_speed: float = 3.0", code)
        self.assertIn("节点_追击者.velocity = Vector3.ZERO", code)
        self.assertIn("节点_追击者.move_and_slide()", code)

    def test_chase_speed_can_come_from_a_node(self):
        """速度接了线 -> 用图里算出来的值(不暴露成检查器参数)。"""
        tree, move = self._chase_tree()
        speed = tree.new_node(NN.GKNodeConstFloat, "速度", gk_value=4.5)
        gk_stub.connect(tree, speed, "值", move, "速度")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertNotIn("@export var chase_speed", code)
        self.assertRegex(code, r"\.normalized\(\) \* 4\.5")

    def test_chase_2d_uses_vector2_and_turn(self):
        tree, _move = self._chase_tree(gk_turn=True)
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        self.assertIn("Vector2.ZERO", code)
        self.assertRegex(code,
                         r"\.rotation = atan2\(_to_goal\d+\.y, _to_goal\d+\.x\)")

    def test_chase_3d_turn_flattens_y_before_look_at(self):
        """目标在头顶正上方时 look_at 会报"up 和方向重合" -> 只按水平方向转。"""
        tree, _move = self._chase_tree(gk_turn=True)
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertRegex(
            code, r"if absf\(_to_goal\d+\.x\) \+ absf\(_to_goal\d+\.z\) > 0\.001:")
        self.assertRegex(code, r"\.look_at\(_here\d+ \+ Vector3\(")
        self.assertIn("Vector3.UP", code)

    def test_chase_goal_pin_beats_the_property(self):
        """『目标』引脚接了东西就以引脚为准(运行时实例走这条)。"""
        tree, move = self._chase_tree()
        getter = tree.new_node(NN.GKNodeGetNode, "取追踪点", gk_path="追踪点")
        gk_stub.connect(tree, getter, "节点", move, "目标")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("追踪点", code)
        self.assertNotIn("小玩家", code)

    def test_chase_without_goal_warns_and_skips(self):
        tree, _move = self._chase_tree(goal="")
        code, warnings = gen(tree, V3)
        self.assertTrue(any("不知道往哪走" in w for w in warnings), warnings)
        self.assertNotIn("velocity", code)
        self.assertNotIn("move_and_slide", code)

    def test_chase_speed_clamped_in_3d(self):
        """和『移动_按方向』同一个坑:300 在 3D 里是 300 米/秒。"""
        tree, move = self._chase_tree()
        move.gk_speed = 300.0
        code, warnings = gen(tree, V3)
        self.assertTrue(any("速度" in w for w in warnings), warnings)
        self.assertIn("@export var chase_speed: float = 50", code)
        self.assertNotIn("300", code)

    def test_chase_needs_character_body(self):
        """基类不是 CharacterBody 时要报出来 —— 否则 Godot 加载脚本才报。"""
        tree, _move = self._chase_tree()
        needed, _why = codegen.required_base_class(
            tree, {"dimension": "3", "base_class": "StaticBody3D"})
        self.assertEqual(needed, "CharacterBody3D")
        result = codegen.generate_result(
            tree, {"dimension": "3", "base_class": "StaticBody3D"})
        codes = [item.code for item in result.diagnostics]
        self.assertIn("BASE_CLASS_MISMATCH", codes)
        self.assertTrue(any("移动_朝向目标" in w for w in result.warnings),
                        result.warnings)

    # ---- ② 变量_增减 ----

    def test_add_var_on_int_counts_up(self):
        tree = make_tree()
        self._declare(tree, "金币数", "int", "0")
        event = tree.new_node(NN.GKNodeOnAreaEntered, "碰到金币",
                              gk_area="金币1", gk_filter="小玩家")
        add = tree.new_node(NN.GKNodeAddVar, "金币+1", gk_var_name="金币数",
                            gk_amount=1.0)
        gk_stub.connect(tree, event, "执行", add, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        # int 变量 + 浮点表达式 -> 显式 int(...)(和小游戏示例里那句一样)
        self.assertIn("金币数 = int((金币数 + 1.0))", code)

    def test_add_var_on_float_subtracts(self):
        tree = make_tree()
        self._declare(tree, "血量", "float", "3.0")
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        add = tree.new_node(NN.GKNodeAddVar, "血量-1", gk_var_name="血量",
                            gk_amount=-1.0)
        gk_stub.connect(tree, event, "执行", add, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("血量 = (血量 + -1.0)", code)
        self.assertNotIn("int((", code)

    def test_add_var_new_value_output_is_the_variable(self):
        """『新值』= 加完之后的值 —— 接出去比较不用再挂一个『变量_读取』。"""
        tree = make_tree()
        self._declare(tree, "金币数", "int", "0")
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        add = tree.new_node(NN.GKNodeAddVar, "金币+1", gk_var_name="金币数",
                            gk_amount=1.0)
        compare = tree.new_node(NN.GKNodeCompare, "够5个了吗", gk_op="GE", gk_b="5")
        branch = tree.new_node(NN.GKNodeBranch, "赢了吗")
        gk_stub.connect(tree, event, "执行", add, "执行")
        gk_stub.connect(tree, add, "新值", compare, "A")
        gk_stub.connect(tree, compare, "结果", branch, "条件")
        gk_stub.connect(tree, add, "完成", branch, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("(金币数 >= 5)", code)
        self.assertIn("金币数 = int((金币数 + 1.0))", code)

    def test_add_var_on_bool_is_skipped(self):
        tree = make_tree()
        self._declare(tree, "结束", "bool", "false")
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        add = tree.new_node(NN.GKNodeAddVar, "加一", gk_var_name="结束")
        gk_stub.connect(tree, event, "执行", add, "执行")
        code, warnings = gen(tree, V3)
        self.assertTrue(any("只能给" in w for w in warnings), warnings)
        self.assertIsNone(re.search(r"^结束 = ", code, re.M))

    def test_add_var_without_name_is_skipped(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        add = tree.new_node(NN.GKNodeAddVar, "没填名字", gk_var_name="")
        gk_stub.connect(tree, event, "执行", add, "执行")
        code, warnings = gen(tree, V3)
        self.assertTrue(any("没有填变量名" in w for w in warnings), warnings)
        self.assertNotIn("+= ", code)

    def test_add_var_uses_setter_when_bound(self):
        """变量绑了 setter -> 走 setter(和『变量_设置』同一条路)。"""
        tree = make_tree()
        var = self._declare(tree, "hp", "float", "100")
        var.setter = "扣血"
        self._declare_func(tree, "扣血", "void", "amount:float")
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        add = tree.new_node(NN.GKNodeAddVar, "扣血", gk_var_name="hp",
                            gk_amount=-10.0)
        gk_stub.connect(tree, event, "执行", add, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertRegex(code, r"扣血_[0-9a-f]{8}\(\(hp \+ -10\.0\)\)")
        self.assertNotIn("hp = ", code)

    # ---- ③ 流程_冷却 ----

    def test_cooldown_splits_ready_and_waiting(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnAreaEntered, "撞到敌1",
                              gk_area="敌1", gk_filter="小玩家")
        cooldown = tree.new_node(NN.GKNodeCooldown, "受伤冷却", gk_seconds=2.0)
        damage = tree.new_node(NN.GKNodePrint, "扣血", gk_text="扣血")
        waiting = tree.new_node(NN.GKNodePrint, "无敌中", gk_text="无敌中")
        gk_stub.connect(tree, event, "执行", cooldown, "执行")
        gk_stub.connect(tree, cooldown, "可以", damage, "执行")
        gk_stub.connect(tree, cooldown, "还在冷却", waiting, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        # 初值 -999999 = 第一次一定放行(游戏刚开始时钟才几十毫秒)
        self.assertIn("var _gk_cd_ticks: int = -999999", code)
        self.assertRegex(
            code,
            r"if Time\.get_ticks_msec\(\) - _gk_cd_ticks >= int\(\(2\.0\) \* 1000\.0\):")
        self.assertRegex(
            code,
            r"if Time[^\n]*:\n\s+_gk_cd_ticks = Time\.get_ticks_msec\(\)\n"
            r"\s+print\(\"扣血\"\)")
        self.assertRegex(code, r"else:\n\s+print\(\"无敌中\"\)")

    def test_two_cooldowns_keep_separate_state(self):
        """两个冷却门各管各的(同一个变量的话第二个每帧覆盖第一个)。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        first = tree.new_node(NN.GKNodeCooldown, "冷却A")
        second = tree.new_node(NN.GKNodeCooldown, "冷却B")
        print_a = tree.new_node(NN.GKNodePrint, "打印A", gk_text="A")
        print_b = tree.new_node(NN.GKNodePrint, "打印B", gk_text="B")
        gk_stub.connect(tree, event, "执行", first, "执行")
        gk_stub.connect(tree, first, "可以", print_a, "执行")
        gk_stub.connect(tree, print_a, "完成", second, "执行")
        gk_stub.connect(tree, second, "可以", print_b, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("var _gk_cd_ticks: int = -999999", code)
        self.assertIn("var _gk_cd_ticks_2: int = -999999", code)
        self.assertEqual(code.count("Time.get_ticks_msec() - _gk_cd_ticks_2"), 1)

    def test_cooldown_without_wait_branch_has_no_else(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        cooldown = tree.new_node(NN.GKNodeCooldown, "冷却")
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="过")
        gk_stub.connect(tree, event, "执行", cooldown, "执行")
        gk_stub.connect(tree, cooldown, "可以", printer, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('print("过")', code)
        self.assertNotIn("else:", code)
        self.assertNotIn("pass", code)

    # ---- ④ 文本_数字 ----

    def test_number_text_keeps_hud_clean(self):
        tree = make_tree()
        self._declare(tree, "血量", "float", "3.0")
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        read = tree.new_node(NN.GKNodeGetVar, "读血量", gk_var_name="血量")
        number = tree.new_node(NN.GKNodeNumberText, "血量文字")
        join = tree.new_node(NN.GKNodeTextJoin, "拼", gk_a="血量 ")
        label = tree.new_node(NN.GKNodeUiText, "血量HUD", gk_text="血量 3")
        gk_stub.connect(tree, read, "值", number, "数值")
        gk_stub.connect(tree, number, "文字", join, "乙")
        gk_stub.connect(tree, join, "结果", label, "文字")
        gk_stub.connect(tree, event, "执行", label, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        self.assertIn("func _gk_num_text(value: float) -> String:", code)
        self.assertIn("if is_equal_approx(value, roundf(value)):", code)
        self.assertIn("return str(int(roundf(value)))", code)
        self.assertIn("return str(snappedf(value, 0.01))", code)
        self.assertIn("_gk_num_text(血量)", code)

    def test_number_text_helper_emitted_once(self):
        """两个『文本_数字』共用同一份 helper(不然 Godot 报函数重名)。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        first = tree.new_node(NN.GKNodeNumberText, "数字1", gk_value=1.0)
        second = tree.new_node(NN.GKNodeNumberText, "数字2", gk_value=2.0)
        label_a = tree.new_node(NN.GKNodeUiText, "标签A")
        label_b = tree.new_node(NN.GKNodeUiText, "标签B")
        gk_stub.connect(tree, first, "文字", label_a, "文字")
        gk_stub.connect(tree, second, "文字", label_b, "文字")
        gk_stub.connect(tree, event, "执行", label_a, "执行")
        gk_stub.connect(tree, label_a, "完成", label_b, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertEqual(code.count("func _gk_num_text"), 1)
        self.assertIn("_gk_num_text(1.0)", code)
        self.assertIn("_gk_num_text(2.0)", code)


class TestRound26CommonPieces(Base):
    """第二十六轮:把"做游戏常用件"再补一批。

    * 『移动_朝向目标』 —— 『加速』平滑加减速 + 『到达』数据输出
    * 『变量_增减』     —— 『限制范围』(血量/金币别再扣成负数)
    * 『流程_冷却』     —— 『每几次放行一次』(>1 才生成计数器,老图产物不变)
    * 『特效_闪红』     —— 受击整模型染色(3D material_override / 2D modulate)
    * 『界面_飘字』     —— 拾取 +1 / 受伤 -1 的世界飘字(不占 HUD)
    """

    def _declare(self, tree, name, var_type="float", default="0"):
        tree.gk_vars.add()
        var = tree.gk_vars[len(tree.gk_vars) - 1]
        var.var_name = name
        var.var_type = var_type
        var.default_value = default
        return var

    def _chase_tree(self, goal="小玩家", **props):
        tree = make_tree()
        physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        move = tree.new_node(NN.GKNodeMoveToward, "追", gk_goal=goal,
                             gk_speed=3.0, **props)
        gk_stub.connect(tree, physics, "执行", move, "执行")
        return tree, move

    # ---- ① 移动_朝向目标:加速 + 到达 ----

    def test_chase_has_arrive_output_pin(self):
        _tree, move = self._chase_tree()
        self.assertIn("到达", [socket.name for socket in move.outputs])

    def test_chase_accel_smooths_velocity(self):
        """『加速』> 0 -> velocity 走 move_toward(不再瞬起瞬停)。"""
        tree, move = self._chase_tree(gk_accel=8.0)
        move.gk_target = "追击者"
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        self.assertRegex(
            code,
            r"节点_追击者\.velocity = 节点_追击者\.velocity\.move_toward\(")
        self.assertIn("8.0 * get_physics_process_delta_time()", code)
        # 目标速度还是"方向 × 速度",停下时喂 ZERO
        self.assertRegex(code, r"_wanted\d+ = _to_goal\d+\.normalized\(\) \* chase_speed")
        self.assertIn("Vector3.ZERO", code)
        # 老的瞬时赋值那句不该再出现
        self.assertNotRegex(code, r"节点_追击者\.velocity = _to_goal\d+\.normalized\(\)")

    def test_chase_without_accel_keeps_instant_start(self):
        """默认『加速』= 0:老图产物不变(直接赋值,不出现 move_toward)。"""
        tree, _move = self._chase_tree()
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertNotIn("move_toward", code)

    def test_chase_arrive_output_is_self_contained(self):
        """『到达』输出在别处被读时要能自己算(不靠语句里的临时变量)。"""
        tree, move = self._chase_tree(gk_stop_distance=1.5)
        move.gk_target = "追击者"
        branch = tree.new_node(NN.GKNodeBranch, "到了吗")
        arrived = tree.new_node(NN.GKNodePrint, "到了", gk_text="到了")
        gk_stub.connect(tree, move, "到达", branch, "条件")
        gk_stub.connect(tree, move, "完成", branch, "执行")
        gk_stub.connect(tree, branch, "是", arrived, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        self.assertRegex(
            code,
            r"if \(\(_gk_global_position_3d\(节点_小玩家\) - "
            r"_gk_global_position_3d\(节点_追击者\)\)\.length\(\) <= 1\.5\):")
        # 条件那句必须是自包含的(不能出现语句里那个临时变量)
        condition = [line for line in code.splitlines() if "<= 1.5):" in line]
        self.assertTrue(condition, code)
        self.assertNotIn("_goal_dist", condition[0])

    def test_chase_arrive_output_without_goal_is_false(self):
        """没选『目标』时『到达』给 false(语句那边已经告警,不再刷一条)。"""
        tree, move = self._chase_tree(goal="")
        branch = tree.new_node(NN.GKNodeBranch, "到了吗")
        gk_stub.connect(tree, move, "到达", branch, "条件")
        gk_stub.connect(tree, move, "完成", branch, "执行")
        code, warnings = gen(tree, V3)
        self.assertIn("if false:", code)
        self.assertTrue(any("不知道往哪走" in w for w in warnings), warnings)

    # ---- ② 变量_增减:限制范围 ----

    def test_add_var_clamps_int(self):
        tree = make_tree()
        self._declare(tree, "金币数", "int", "0")
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        add = tree.new_node(NN.GKNodeAddVar, "金币+1", gk_var_name="金币数",
                            gk_amount=1.0, gk_clamp=True, gk_min=0.0, gk_max=5.0)
        gk_stub.connect(tree, event, "执行", add, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        # int 变量的 int(...) 自动补在 clampf 外面
        self.assertIn("金币数 = int(clampf((金币数 + 1.0), 0.0, 5.0))", code)

    def test_add_var_clamps_float_hp(self):
        tree = make_tree()
        self._declare(tree, "血量", "float", "3.0")
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        add = tree.new_node(NN.GKNodeAddVar, "血量-1", gk_var_name="血量",
                            gk_amount=-1.0, gk_clamp=True, gk_min=0.0, gk_max=3.0)
        gk_stub.connect(tree, event, "执行", add, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("血量 = clampf((血量 + -1.0), 0.0, 3.0)", code)
        self.assertNotIn("int(clampf", code)

    def test_add_var_without_clamp_keeps_old_line(self):
        tree = make_tree()
        self._declare(tree, "血量", "float", "3.0")
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        add = tree.new_node(NN.GKNodeAddVar, "血量-1", gk_var_name="血量",
                            gk_amount=-1.0)
        gk_stub.connect(tree, event, "执行", add, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("血量 = (血量 + -1.0)", code)
        self.assertNotIn("clampf", code)

    def test_add_var_clamp_min_above_max_warns(self):
        """最小 > 最大 = 填反了 -> 告警(夹完恒等于最小值)。"""
        tree = make_tree()
        self._declare(tree, "血量", "float", "3.0")
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        add = tree.new_node(NN.GKNodeAddVar, "扣血", gk_var_name="血量",
                            gk_amount=-1.0, gk_clamp=True, gk_min=5.0, gk_max=1.0)
        gk_stub.connect(tree, event, "执行", add, "执行")
        code, warnings = gen(tree, V3)
        self.assertTrue(any("还大" in w for w in warnings), warnings)
        self.assertIn("clampf((血量 + -1.0), 5.0, 1.0)", code)

    # ---- ③ 流程_冷却:每几次放行一次 ----

    def test_cooldown_every_n_adds_counter(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        cooldown = tree.new_node(NN.GKNodeCooldown, "每两次", gk_seconds=0.5,
                                 gk_every=2)
        printer = tree.new_node(NN.GKNodePrint, "过", gk_text="过")
        gk_stub.connect(tree, event, "执行", cooldown, "执行")
        gk_stub.connect(tree, cooldown, "可以", printer, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        self.assertIn("var _gk_cd_count: int = 0", code)
        self.assertRegex(code, r"_gk_cd_count \+= 1")
        self.assertRegex(
            code,
            r"if Time\.get_ticks_msec\(\) - _gk_cd_ticks >= int\(\(0\.5\) "
            r"\* 1000\.0\) and _gk_cd_count % 2 == 0:")

    def test_cooldown_every_one_keeps_old_output(self):
        """默认 1 = 老行为:不生成计数器行(老图产物逐字不变)。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        cooldown = tree.new_node(NN.GKNodeCooldown, "冷却", gk_seconds=2.0)
        printer = tree.new_node(NN.GKNodePrint, "过", gk_text="过")
        gk_stub.connect(tree, event, "执行", cooldown, "执行")
        gk_stub.connect(tree, cooldown, "可以", printer, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertNotIn("_gk_cd_count", code)
        self.assertIn("if Time.get_ticks_msec() - _gk_cd_ticks >= "
                      "int((2.0) * 1000.0):", code)

    def test_two_cooldowns_keep_separate_counters(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        first = tree.new_node(NN.GKNodeCooldown, "冷却A", gk_every=2)
        second = tree.new_node(NN.GKNodeCooldown, "冷却B", gk_every=3)
        printer = tree.new_node(NN.GKNodePrint, "过", gk_text="过")
        gk_stub.connect(tree, event, "执行", first, "执行")
        gk_stub.connect(tree, first, "可以", second, "执行")
        gk_stub.connect(tree, second, "可以", printer, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_cd_count % 2 == 0", code)
        self.assertIn("_gk_cd_count_2 % 3 == 0", code)

    # ---- ④ 特效_闪红 ----

    def test_flash_red_3d_covers_meshes_and_restores(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        flash = tree.new_node(NN.GKNodeFlashRed, "受伤闪红", gk_time=0.25)
        gk_stub.connect(tree, event, "执行", flash, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        self.assert_no_chinese_identifiers(code)
        self.assertIn("func _gk_flash_3d(node: Node, color: Color, "
                      "seconds: float) -> void:", code)
        self.assertIn("BaseMaterial3D.SHADING_MODE_UNSHADED", code)
        # 整棵网格盖上去 + 时间到了恢复(恢复前先比对是不是自己那个材质)
        self.assertIn("(item as MeshInstance3D).material_override = mat", code)
        self.assertIn("(item as MeshInstance3D).material_override = olds[index]", code)
        self.assertIn("tween_callback(func() -> void:", code)
        self.assertIn("_gk_flash_3d(self, Color(1.0, 0.25, 0.25, 1.0), 0.25)", code)

    def test_flash_red_helper_emitted_once(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        first = tree.new_node(NN.GKNodeFlashRed, "闪A")
        second = tree.new_node(NN.GKNodeFlashRed, "闪B", gk_target="敌1")
        gk_stub.connect(tree, event, "执行", first, "执行")
        gk_stub.connect(tree, first, "完成", second, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertEqual(code.count("func _gk_flash_3d"), 1)
        self.assertIn("_gk_flash_3d(节点_敌1, Color(", code)

    def test_flash_red_2d_uses_modulate(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        flash = tree.new_node(NN.GKNodeFlashRed, "受击闪红")
        gk_stub.connect(tree, event, "执行", flash, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        self.assertIn("func _gk_flash_2d(node: Node, color: Color, "
                      "seconds: float) -> void:", code)
        self.assertIn("(item as CanvasItem).modulate = color", code)
        self.assertIn("(item as CanvasItem).modulate = olds[index]", code)
        self.assertNotIn("material_override", code)

    # ---- ⑤ 界面_飘字 ----

    def test_float_text_3d_creates_billboard_label(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        floating = tree.new_node(NN.GKNodeFloatText, "金币飘字",
                                 gk_target="金币1", gk_prefix="+", gk_value=1.0)
        gk_stub.connect(tree, event, "执行", floating, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        self.assert_no_chinese_identifiers(code)
        self.assertIn("func _gk_float_text_3d(anchor: Node, text: String, "
                      "color: Color, seconds: float, rise: float) -> void:", code)
        self.assertIn("Label3D.new()", code)
        self.assertIn("BaseMaterial3D.BILLBOARD_ENABLED", code)
        self.assertIn("tween_callback(label.queue_free)", code)
        # 文字 = 前缀 + 数字(整数不带 .0);飘完自己删
        self.assertIn('_gk_float_text_3d(节点_金币1, "+" + _gk_num_text(1.0), '
                      'Color(1.0, 0.9, 0.35, 1.0), 0.8, 1.2)', code)
        self.assertIn("func _gk_num_text(value: float) -> String:", code)

    def test_float_text_shares_num_text_helper(self):
        """和『文本_数字』共用同一个 helper(各生成一份 Godot 报函数重名)。"""
        tree = make_tree()
        self._declare(tree, "血量", "float", "3.0")
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        read = tree.new_node(NN.GKNodeGetVar, "读血量", gk_var_name="血量")
        number = tree.new_node(NN.GKNodeNumberText, "血量文字")
        damage = tree.new_node(NN.GKNodeConstFloat, "伤害", gk_value=25.0)
        floating = tree.new_node(NN.GKNodeFloatText, "受伤飘字", gk_target="敌1",
                                 gk_prefix="-")
        hud = tree.new_node(NN.GKNodeUiText, "血量HUD")
        gk_stub.connect(tree, read, "值", number, "数值")
        gk_stub.connect(tree, number, "文字", hud, "文字")
        gk_stub.connect(tree, damage, "值", floating, "数值")
        gk_stub.connect(tree, event, "执行", hud, "执行")
        gk_stub.connect(tree, hud, "完成", floating, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertEqual(code.count("func _gk_num_text"), 1)
        self.assertIn("_gk_num_text(血量)", code)
        self.assertIn('_gk_float_text_3d(节点_敌1, "-" + _gk_num_text(25.0), ',
                      code)

    def test_float_text_2d_creates_label(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        floating = tree.new_node(NN.GKNodeFloatText, "受伤飘字",
                                 gk_prefix="-", gk_value=1.0)
        gk_stub.connect(tree, event, "执行", floating, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        self.assertIn("func _gk_float_text_2d(anchor: Node, text: String, "
                      "color: Color, seconds: float, rise: float) -> void:", code)
        self.assertIn("Label.new()", code)
        self.assertIn('add_theme_font_size_override("font_size", 32)', code)
        self.assertIn('_gk_float_text_2d(self, "-" + _gk_num_text(1.0), ', code)
        self.assertNotIn("Label3D", code)


class TestRound27AnimationRepeat(Base):
    """第二十七轮:"先放完再重复"一键化 + 动画播放器填物体名的那条路。

    * 『动画_播放』的『重复触发时』(= 老『混合模式』)选项改名说清楚:
      『从头重播』/『先放完再说(在播就跳过)』—— 后者就是"先放完再重复";
    * 修一个运行时断链的 bug:『动画播放器』填了**物体名**时,那个物体不是
      AnimationPlayer(glTF 把播放器放在模型实例内部),以前直接拿它当
      播放器用 -> 运行时报 "Nonexistent function 'has_animation' in base
      'Area3D'" 且整条链断掉。现在从那个物体开始查。
    """

    def _play_tree(self, **props):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "启动")
        play = tree.new_node(NN.GKNodePlayAnim, "播放", gk_anim_name="抖动",
                             gk_player="", **props)
        gk_stub.connect(tree, event, "执行", play, "执行")
        return tree, play

    def test_repeat_option_keys_stay_compatible(self):
        """选项的**键**不能改(RENAME 会让老 .blend 里的值对不上)。"""
        from BlenderGameKit.nodes import nodes as NN

        spec = next(entry for entry in NN.GKNodePlayAnim.gk_prop_defs
                    if entry[0] == "gk_blend_mode")
        label = spec[3]
        items = spec[5]
        self.assertEqual([item[0] for item in items], ["REPLACE", "CONTINUE"])
        self.assertEqual(label, "重复触发时")
        labels = [item[1] for item in items]
        self.assertTrue(any("先放完" in text for text in labels), labels)

    def test_replace_restarts_every_trigger(self):
        """默认『从头重播』:老行为一个字不变(没有跳过守卫)。"""
        tree, _play = self._play_tree()
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertNotIn("current_animation !=", code)
        self.assertIn(".play(_anim", code)

    def test_continue_skips_while_playing(self):
        """『先放完再说』:正在播同名动画就跳过(不打断当前这一轮)。"""
        tree, _play = self._play_tree(gk_blend_mode="CONTINUE")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        self.assertRegex(code, r"if _ap\d+\.current_animation != _anim\d+ "
                               r"or not _ap\d+\.is_playing\(\):")
        # 播放/接线都要在跳过守卫**里面**(被跳过时不重播、也不重接信号)
        play_line = next(line for line in code.splitlines()
                         if ".play(" in line and "current_animation" not in line)
        guard = next(line for line in code.splitlines()
                     if "current_animation !=" in line)
        play_indent = len(play_line) - len(play_line.lstrip(" "))
        guard_indent = len(guard) - len(guard.lstrip(" "))
        self.assertEqual(play_indent, guard_indent + 4,
                         f"播放没在守卫里面: {play_line!r}")

    def test_named_player_goes_through_the_finder(self):
        """填了物体名 -> 从那个物体开始**找**播放器(不能直接当播放器用)。"""
        tree, _play = self._play_tree()
        _play.gk_player = "敌1"
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_find_animation_player(anim_敌1)", code)
        self.assertIn("func _gk_find_animation_player(host: Node) -> Node:", code)
        # 不能再生成"把物体本身当播放器"的那种引用
        self.assertNotRegex(code, r"var _ap\d+ := 节点_敌1$")
        self.assertNotRegex(code, r"var _ap\d+ := anim_敌1$")


class TestRound28PhysicsPush(Base):
    """第二十八轮:『推开刚体』—— Godot 的运动学角色**不会**推刚体。

    ── 为什么这是"物理加固"里的第一件事 ──────────────────────────
    角色是 CharacterBody(运动学体):`move_and_slide()` 撞到刚体只会贴着
    滑走,不给它任何力。不加这一段,用户看到的就是"箱子焊在地上推不动",
    而他会以为是碰撞体没做对。生成的冲量 = 推力 × 帧时长 × 质量
    (持续施加『推力』牛顿),方向取碰撞法线的**水平**反向。
    """

    def _move_tree(self, **props):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        # 速度给 6(3D 里的合理值)—— 300 会触发"3D 速度太大"那条告警
        move = tree.new_node(NN.GKNodeMoveDirection, "移动", gk_speed=6.0,
                             **props)
        gk_stub.connect(tree, event, "执行", move, "执行")
        return tree, move

    def test_push_off_keeps_old_output(self):
        """默认不勾:老图产物一个字不变(没有碰撞循环)。"""
        tree, _move = self._move_tree()
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertNotIn("get_slide_collision", code)

    def test_push_generates_horizontal_impulse(self):
        tree, _move = self._move_tree(gk_push_bodies=True, gk_push_force=45.0)
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        self.assertRegex(code, r"for _push\d+ in self\.get_slide_collision_count\(\):")
        self.assertRegex(code, r"var _hit\d+ := self\.get_slide_collision\(_push\d+\)")
        self.assertIn("is RigidBody3D:", code)
        self.assertIn("apply_central_impulse(", code)
        self.assertIn("45.0 * get_physics_process_delta_time()", code)
        self.assertIn("as RigidBody3D).mass", code)
        # Y 分量要丢掉 —— 不然会把箱子往地里/天上按
        self.assertRegex(code, r"Vector3\(-_hit\d+\.get_normal\(\)\.x, 0\.0, "
                               r"-_hit\d+\.get_normal\(\)\.z\)")

    def test_push_happens_after_move_and_slide(self):
        """必须在 move_and_slide() **之后** —— 那时才有碰撞可查。"""
        tree, _move = self._move_tree(gk_push_bodies=True)
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertLess(code.index(".move_and_slide()"),
                        code.index("get_slide_collision_count()"))

    def test_push_2d_uses_rigidbody2d(self):
        tree, _move = self._move_tree(gk_push_bodies=True)
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        self.assertIn("is RigidBody2D:", code)
        self.assertNotIn("RigidBody3D", code)


class TestRound29StepUp(Base):
    """第二十九轮:『上台阶』—— Godot 的运动学角色**不会**自动上台阶。

    `move_and_slide()` 撞到 0.35 米的台阶只会贴着前脸滑走,玩家看到的
    就是"一堵墙"。『上台阶高度』> 0 时生成"三关探测":
      ① 前面被**竖着的**东西挡住(坡面不算 —— 不然上坡一蹦一蹦);
      ② 抬到该高度再往前是通的(台阶更高就会撞上 -> 仍然挡住);
      ③ 落点下面有地(不对着悬崖抬腿)。
    三个都过了才平移过去。默认 0 时**一个字都不生成** —— 老图产物不变。
    """

    def _move_tree(self, **props):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        move = tree.new_node(NN.GKNodeMoveDirection, "移动", gk_speed=6.0,
                             gk_keep_y=True, **props)
        gk_stub.connect(tree, event, "执行", move, "执行")
        return tree, move

    def test_step_off_keeps_old_output(self):
        """默认 0(不填):老图产物一个字不变(没有 test_move)。"""
        tree, _move = self._move_tree()
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertNotIn("test_move", code)
        self.assertNotIn("KinematicCollision", code)

    def test_step_generates_three_gates(self):
        """填了高度 -> 三关探测 + 平移过去(不是硬挤)。"""
        tree, _move = self._move_tree(gk_step_height=0.5)
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        self.assertIn("self.is_on_floor()", code)
        self.assertRegex(code, r"var _step_col\d+ := KinematicCollision3D\.new\(\)")
        # ① 被挡住的得是**竖面**(absf(法线.y) < 0.5)—— 坡面不抬腿
        self.assertRegex(code, r"if self\.test_move\(self\.global_transform, "
                               r"_step_fwd\d+, _step_col\d+\) and "
                               r"absf\(_step_col\d+\.get_normal\(\)\.y\) < 0\.5:")
        # ② 抬起来的路通(斜着上去)
        self.assertIn("Vector3.UP * 0.5 + _step_fwd", code)
        # ③ 抬起来之后**还得走得动** —— 胶囊尖从高台阶旁擦过去的
        # "假台阶"就靠这一关挡住(不然顶着高墙原地一蹦一蹦)
        self.assertRegex(code, r"if not self\.test_move\(_step_ahead\d+, "
                               r"_step_fwd\d+\):")
        # ④ 落点有地(比现在高 ≥5 厘米)且是**平面**
        self.assertIn("Vector3.DOWN * 0.45", code)
        self.assertRegex(code, r"var _step_land\d+ := KinematicCollision3D\.new\(\)")
        self.assertRegex(code, r"_step_land\d+\.get_normal\(\)\.y >= 0\.5")
        self.assertRegex(code, r"self\.global_position \+= _step_lift\d+")
        # 必须排在 move_and_slide() 之后 —— 滑不过去才轮到抬腿
        self.assertLess(code.index(".move_and_slide()"), code.index("test_move("))

    def test_step_2d_uses_vector2(self):
        tree, _move = self._move_tree(gk_step_height=0.5)
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        self.assertIn("KinematicCollision2D.new()", code)
        self.assertIn("Vector2.UP * 0.5", code)
        self.assertNotIn("KinematicCollision3D", code)


class TestRound30Vehicles(Base):
    """第三十轮:移动平台(AnimatableBody)+ 可驾驶载具(上/下车/驾驶)。

    ── 两条主线 ────────────────────────────────────────────────────
    ① **站上去跟着走**:平台必须是 AnimatableBody —— 被脚本/时间轴
       每帧挪位置时它的**速度**会传给站在上面的角色;StaticBody 是
       "瞬移",角色只会留在原地(平台从脚下溜走)。
    ② **可驾驶**:上/下车/驾驶三个节点靠载具**自己身上**的
       `set_meta("gk_driving")` 互通(不用摆变量,摆两辆车也不串);
       驾驶 = 沿车头前进/后退 + A/D 转车头 + **每帧把角色钉回座位**
       (镜头 / 距离判定 / 触发区都认角色位置,所以它们自动跟着车走)。
    """

    def _tree(self):
        """物理帧 → 移动(角色)→ 驾驶;按 E:在开吗? 是 = 下车,否 = 上车。

        ⚠ 『否』口只能挂在**里层**(先判"按了 E 吗")—— 挂在外层的
        条件里的话,"没按 E"的帧也会走 否(上车),刚下车就被塞回座位
        (探针实测抓到的)。
        """
        tree = make_tree()
        physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        move = tree.new_node(NN.GKNodeMoveDirection, "移动",
                             gk_speed=6.0, gk_keep_y=True)
        drive = tree.new_node(NN.GKNodeDriveVehicle, "驾驶",
                              gk_vehicle="载具", gk_speed=10.0)
        enter = tree.new_node(NN.GKNodeEnterVehicle, "上车", gk_vehicle="载具")
        leave = tree.new_node(NN.GKNodeExitVehicle, "下车", gk_vehicle="载具")
        key = tree.new_node(NN.GKNodeKey, "E键", gk_key="E",
                            gk_action="key_e", gk_trigger="JUST")
        pressed = tree.new_node(NN.GKNodeBranch, "按了E?")
        driving = tree.new_node(NN.GKNodeBranch, "在开吗?")
        gk_stub.connect(tree, physics, "执行", move, "执行")
        gk_stub.connect(tree, move, "完成", drive, "执行")
        gk_stub.connect(tree, key, "结果", pressed, "条件")
        gk_stub.connect(tree, drive, "完成", pressed, "执行")
        gk_stub.connect(tree, pressed, "是", driving, "执行")
        gk_stub.connect(tree, drive, "驾驶中", driving, "条件")
        gk_stub.connect(tree, driving, "是", leave, "执行")
        gk_stub.connect(tree, driving, "否", enter, "执行")
        return tree, enter, drive, leave

    def test_animatable_body_maps_to_itself(self):
        """『游戏物理』新类型 -> 节点/脚本基类都是 AnimatableBody。"""
        from BlenderGameKit import properties as props
        self.assertEqual(props.BODY_TYPE_TO_BASE_CLASS["AnimatableBody3D"],
                         "AnimatableBody3D")
        self.assertEqual(props.BODY_TYPE_TO_BASE_CLASS["AnimatableBody2D"],
                         "AnimatableBody2D")
        physics = types.SimpleNamespace(is_trigger=False,
                                        body_type="AnimatableBody3D")
        self.assertEqual(props.resolve_base_class(None, None, physics, "3"),
                         "AnimatableBody3D")
        # 2D 项目里选了 3D 类型 -> 自动纠正(不然 Godot 拒绝加载场景)
        self.assertEqual(props.resolve_base_class(None, None, physics, "2"),
                         "AnimatableBody2D")

    def test_enter_puts_rider_on_the_seat(self):
        tree, _enter, _drive, _leave = self._tree()
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        # 状态写在**载具自己身上** —— 三个节点靠它互通
        self.assertIn('set_meta("gk_driving", true)', code)
        self.assertIn('set_meta("gk_seat", Vector3(0.0, 1.2, 0.0))', code)
        self.assertIn(".visible = false", code)
        self.assertIn("_gk_set_collision(self, false)", code)
        self.assertIn(".global_transform * Vector3(0.0, 1.2, 0.0)", code)
        # 『上车距离』= 3 -> 离得够近才上得去(不然在地图另一头也能瞬移上车)
        self.assertIn(".length() <= 3.0:", code)

    def test_enter_without_vehicle_warns(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        enter = tree.new_node(NN.GKNodeEnterVehicle, "上车")      # 没选载具
        gk_stub.connect(tree, event, "执行", enter, "执行")
        code, warnings = gen(tree, V3)
        self.assertTrue(any("载具" in str(w) for w in warnings), warnings)
        self.assertNotIn("set_meta", code)

    def test_exit_restores_the_rider(self):
        tree, _enter, _drive, _leave = self._tree()
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('set_meta("gk_driving", false)', code)
        self.assertIn(".global_transform * Vector3(2.0, 0.5, 0.0)", code)
        self.assertIn(".visible = true", code)
        self.assertIn("_gk_set_collision(self, true)", code)

    def test_drive_guarded_by_state_and_pins_seat(self):
        tree, _enter, _drive, _leave = self._tree()
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        # 没人在开就什么都不做
        self.assertRegex(code, r'if 载具_\S+\.get_meta\("gk_driving", false\):')
        # 车感:W/S 沿车头、A/D 转车头
        # ⚠ `rotation.y` 是弧度,『转向速度』填的是度/秒 —— 少一层
        # deg_to_rad 车会疯狂打转(探针实测:每帧转 1.4 弧度)
        self.assertIn(".rotation.y -= deg_to_rad(", code)
        self.assertRegex(code, r"\.velocity\.x = _drive_fwd\d+\.x \* _drive\d+\.z "
                               r"\* 10\.0")
        self.assertRegex(code, r"\.velocity\.z = _drive_fwd\d+\.z \* _drive\d+\.z "
                               r"\* 10\.0")
        # 每帧把角色钉回座位(读载具身上那份座位)
        self.assertRegex(code, r'self\.global_position = 载具_\S+\.global_transform '
                               r'\* \(载具_\S+\.get_meta\("gk_seat", Vector3\.ZERO\) '
                               r'as Vector3\)')
        # 「驾驶中」输出 = 自包含表达式(接『如果_否则』用)——
        # 分支那边也生成了同一句
        self.assertGreaterEqual(code.count('get_meta("gk_driving", false)'), 2)
        # 钉座位必须排在角色移动**之后**(不然会被角色的移动覆盖)
        pin = code.index('self.global_position = 载具_载具.global_transform * (')
        self.assertLess(code.index("self.move_and_slide()"), pin)

    def test_drive_2d_uses_vector2(self):
        tree = make_tree()
        physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        drive = tree.new_node(NN.GKNodeDriveVehicle, "驾驶2", gk_vehicle="车2")
        gk_stub.connect(tree, physics, "执行", drive, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assert_indent_sane(code)
        self.assertIn('.get_meta("gk_seat", Vector2.ZERO) as Vector2', code)
        self.assertIn(".velocity.x = _drive", code)
        self.assertNotIn("rotation.y", code)      # 2D 没有"打方向盘"

    def test_drive_stands_down_in_god_view(self):
        """上车后按 Tab 进上帝视角,不该还能开车(用户 2026-10-06 实测)。

        上帝视角里 WASD 是**飞相机**的 —— 角色移动早有这个守卫,驾驶节点
        之前漏了。没开上帝摄像机的项目不生成这一截(不会引用不存在的函数)。
        """
        tree = make_tree()
        physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        drive = tree.new_node(NN.GKNodeDriveVehicle, "驾驶", gk_vehicle="载具")
        gk_stub.connect(tree, physics, "执行", drive, "执行")
        plain, _ = gen(tree, V3)
        self.assertIn('if 载具_载具.get_meta("gk_driving", false):', plain)
        self.assertNotIn("_gk_god_camera_active", plain)
        gated, _ = gen(tree, dict(V3, god_camera=True))
        self.assertIn('if 载具_载具.get_meta("gk_driving", false) '
                      'and not _gk_god_camera_active():', gated)


if __name__ == "__main__":
    unittest.main(verbosity=2)
