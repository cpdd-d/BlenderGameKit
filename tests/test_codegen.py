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

from BlenderGameKit import (blender_adapter, codegen, exporter, models,  # noqa: E402
                            node_groups, paths, project)
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

    @staticmethod
    def functions(code):
        """把生成物切成 {函数名: 函数体} —— 用来查"跨函数引用"。

        ── 为什么要按函数切(真机抓到的 bug)────────────────────────
        生成物里同一个名字可能**在 _ready 里声明、却在 _process 里用** ——
        这种"看起来有,其实没在作用域里"的问题,grep 整份文件是抓不到的
        (名字确实出现过),只有按函数切开看才暴露。
        """
        funcs = {}
        name = None
        body = []
        for line in code.splitlines():
            if line.startswith("func "):
                if name is not None:
                    funcs[name] = "\n".join(body)
                name = line.split("(")[0][len("func "):]
                body = []
            elif name is not None:
                body.append(line)
        if name is not None:
            funcs[name] = "\n".join(body)
        return funcs


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

        例外一:动画树里的卡片(`GKNodeAnimState` / `GKNodeAnimTransition` /
        `GKNodeAnimEntry`)—— 它们**动画树**里的节点,不是执行流的一环。
        例外二:『游戏显示』设置图的四张**设置卡**(第四十二轮)——
        它们不是逻辑,是场景显示设置的控制台(卡上的开关直接改场景设置),
        所以一个引脚都没有。『游戏音乐』设置图的两张卡(第八十六轮)
        同理。
        """
        from BlenderGameKit import gk_sockets as S
        from BlenderGameKit.nodes import node_base as NB
        # 动画树里的卡片节点不参与执行流,单独列出来
        # (`GKNodeAnimTransition` 同 `GKNodeAnimState`:靠条件为真被激活;
        #  `GKNodeAnimEntry` 是**开始口** —— 它只有条件,本身就是源头)
        no_flow = {"GKNodeAnimState", "GKNodeAnimTransition",
                   "GKNodeAnimEntry"}
        no_flow |= set(NN.DISPLAY_CARD_IDS)
        no_flow |= set(NN.MUSIC_CARD_IDS)
        no_flow |= set(NN.ASSET_CARD_IDS) | set(NN.LEGACY_ASSET_CARD_IDS)
        # 例外三:AI 图那两件(第六十九轮)——
        #   * 『AI_开始』是**源头**(只有『开始』出口,和动画的『开始口』一个道理);
        #   * 『AI_输出』是**声明**(声明一个对外布尔变量),不参与执行流。
        no_flow |= {"GKNodeAiStart", "GKNodeAiOutput"}
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


class TestLightNodes(Base):
    """第四十八轮(阶段①灯光扩展):『灯光_设置』节点 + 手电筒字段。"""

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_set_light_emits_helper_and_call(self):
        """开关一盏灯:生成 _gk_set_light(...) 调用 + 配套小工具函数。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeSetLight, "关手电",
                             gk_light="手电筒", gk_on=False, gk_energy=0.0)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('_gk_set_light(self, "手电筒", false, 0.0, null)', code)
        # 小工具只出一次(多个『灯光_设置』不重复生成)
        self.assertEqual(code.count("func _gk_set_light("), 1)
        self.assert_indent_sane(code)

    def test_set_light_energy_and_color(self):
        """亮度 > 0 会写进调用;勾了『改颜色』才把 Color(...) 带上。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeSetLight, "开手电",
                             gk_light="手电筒", gk_on=True, gk_energy=2.0,
                             gk_set_color=True,
                             gk_color=(1.0, 0.9, 0.6, 1.0))
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V3)
        self.assertIn("_gk_set_light(self, \"手电筒\", true, 2.0, "
                      "Color(1.0, 0.9, 0.6, 1.0))", code)

    def test_set_light_helper_finds_by_name(self):
        """小工具从 current_scene 按名字找灯(换过关卡也对得上)。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeSetLight, "灯", gk_light="台灯")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V3)
        self.assertIn("host.get_tree().current_scene", code)
        self.assertIn("light.visible = on", code)
        self.assertIn("light.light_energy = energy", code)

    def test_env_script_has_flashlight_field(self):
        """环境脚本带 follow_lights 字段 + 每帧贴相机的实现。"""
        from BlenderGameKit import asset_export
        text = asset_export.render_env_script()
        self.assertIn('follow_lights: String = ""', text)
        self.assertIn("func _gk_follow_lights_tick() -> void:", text)
        self.assertIn("_gk_follow_lights_tick()", text)
        # 多光近似:亮灯收集 + 按影响挑一盏
        self.assertIn("func _gk_lights_on() -> Array:", text)
        self.assertIn("func _gk_pick_light(", text)

    def test_light_energy_mapping(self):
        """能量换算:太阳 ×1.2 夹 [0.2,4];点光 1000W ≈ 1.0,夹 [0.05,8]。"""
        from BlenderGameKit import asset_export
        sun = types.SimpleNamespace(
            data=types.SimpleNamespace(type="SUN", energy=1.0))
        self.assertAlmostEqual(asset_export._light_energy_for(sun), 1.2)
        bright = types.SimpleNamespace(
            data=types.SimpleNamespace(type="SUN", energy=10.0))
        self.assertAlmostEqual(asset_export._light_energy_for(bright), 4.0)
        lamp = types.SimpleNamespace(
            data=types.SimpleNamespace(type="POINT", energy=1000.0))
        self.assertAlmostEqual(asset_export._light_energy_for(lamp), 1.0)
        dim = types.SimpleNamespace(
            data=types.SimpleNamespace(type="POINT", energy=10.0))
        self.assertAlmostEqual(asset_export._light_energy_for(dim), 0.05)


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


class TestRound93SaveVerify(Base):
    """第九十三轮:『存档_保存』的『防改档』(存档校验和)。

    勾上之后落盘是一层"信封"(gk_check + gk_data 文本),读档**自描述** ——
    认得信封就校验,裸 JSON(老存档)原样读;一个都没勾的图生成物逐字不变。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_verify_off_keeps_old_output(self):
        """没勾『防改档』-> 一个字都不多(老图生成物不变)。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeSaveWrite, "保存", gk_slot="save1",
                             gk_save_verify=False)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertNotIn("sha256_text", code)
        self.assertNotIn("gk_check", code)
        self.assertNotIn("_GK_SAVE_SALT", code)
        self.assertIn("func _gk_write_save(path: String, data: Dictionary) -> void:",
                      code)
        self.assertIn("_gk_write_save(_gk_save_path, _gk_save_data)", code)
        self.assert_indent_sane(code)

    def test_verify_on_writes_envelope(self):
        """勾上 -> 盐常量 + 校验读写 + 调用点带 true。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeSaveWrite, "保存", gk_slot="save1",
                             gk_save_verify=True)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('const _GK_SAVE_SALT := "blendergamekit-save-v1"', code)
        self.assertIn('(payload + "|" + _GK_SAVE_SALT).sha256_text()', code)
        self.assertIn('"gk_check"', code)
        self.assertIn('"gk_data"', code)
        self.assertIn("_gk_write_save(_gk_save_path, _gk_save_data, true)", code)
        self.assertIn("func _gk_write_save(path: String, data: Dictionary, "
                      "verify: bool = false) -> void:", code)
        # 校验不过 -> 当没有存档(空字典),并给一条可读的告警
        self.assertIn("存档校验不过", code)
        self.assertIn("return {}", code)
        self.assert_indent_sane(code)

    def test_reader_reads_plain_old_save(self):
        """读档自描述:没有 gk_check 的裸 JSON(老存档)原样读回来。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeSaveWrite, "保存", gk_slot="save1",
                             gk_save_verify=True)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V2)
        self.assertIn('if box.has("gk_check"):', code)
        # 裸 JSON 那条路:原样返回解析出来的字典(老档继续能读)
        self.assertIn("return box", code)

    def test_verify_flag_is_per_node(self):
        """开关逐节点生效:同图里一个防一个不防,调用点各按各的写。"""
        tree = make_tree()
        event = self._ready(tree)
        guarded = tree.new_node(NN.GKNodeSaveWrite, "防改档的", gk_slot="save1",
                                gk_save_verify=True)
        plain = tree.new_node(NN.GKNodeSaveWrite, "不防的", gk_slot="slotB",
                              gk_save_verify=False)
        gk_stub.connect(tree, event, "执行", guarded, "执行")
        gk_stub.connect(tree, guarded, "完成", plain, "执行")
        code, _ = gen(tree, V2)
        calls = [line.strip() for line in code.splitlines()
                 if line.strip().startswith("_gk_write_save(")]
        self.assertIn("_gk_write_save(_gk_save_path, _gk_save_data, true)", calls)
        self.assertIn("_gk_write_save(_gk_save_path, _gk_save_data)", calls)

    def test_load_only_graph_has_no_salt(self):
        """只载入不保存的图不生成盐 / 校验 —— 老图一行都不多。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeSaveLoad, "载入", gk_slot="save1")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V2)
        self.assertNotIn("_GK_SAVE_SALT", code)
        self.assertNotIn("sha256_text", code)
        self.assertIn("func _gk_read_save(path: String) -> Dictionary:", code)
        self.assertNotIn('box.has("gk_check")', code)


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

    def test_ui_button_has_both_created_and_clicked_outputs(self):
        """按钮要有两个出口:『完成』(建好了,继续建下一个)+『点击』(玩家点了)。

        回归(第三十七轮):以前只有『点击』一个出口 —— 于是**链上没法
        接着建第二个按钮**(建暂停菜单时要连建三个,当场报
        KeyError: key "完成" not found)。现在『完成』只表示"建好了",
        "点了才走"由独立回调负责,两件事不会混。
        """
        names = [spec["name"] for spec in NN.GKNodeUiButton.gk_outputs]
        self.assertIn("完成", names)
        self.assertIn("点击", names)

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


class TestUiRound37(Base):
    """UI 第一版(第三十七轮):对齐 / 图片 / 面板 / 设置 / 暂停 / 对话框。

    这一批的共同约束:**老图的行为一字不变** ——
    『对齐』默认是左上(和以前一样填 X / Y),只有显式选了别的对齐
    才改挂锚点。所以每条测试都同时看"新写法在不在"和"老写法有没有被改掉"。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    # ---- 对齐 ----
    def test_text_align_center_uses_anchor_preset(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeUiText, "标题", gk_text="暂停",
                             gk_align="CENTER", gk_margin=0.0)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        # Label 用 MINSIZE:它的"尺寸"就是文字本身,KeepSize 时是 0,会挂偏
        self.assertIn("set_anchors_and_offsets_preset(Control.PRESET_CENTER, "
                      "Control.PRESET_MODE_MINSIZE, 0.0)", code)
        self.assertNotIn("position = Vector2", code)
        self.assert_indent_sane(code)

    def test_default_align_keeps_old_position_behaviour(self):
        """默认(左上)必须还是 `position = Vector2(...)` —— 老图产物不变。"""
        tree = make_tree()
        event = self._ready(tree)
        button = tree.new_node(NN.GKNodeUiButton, "开始", gk_text="开始",
                               gk_x=100.0, gk_y=200.0)
        bar = tree.new_node(NN.GKNodeUiBar, "血条", gk_value=1.0)
        gk_stub.connect(tree, event, "执行", button, "执行")
        gk_stub.connect(tree, button, "点击", bar, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        # 老用法:按 X / Y 摆(数字会变成 @export 参数供检查器调,所以断言写法而不是字面值)
        self.assertIn(".position = Vector2(", code)
        self.assertNotIn("set_anchors_and_offsets_preset", code)

    def test_bottom_right_align_uses_keep_size(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeUiButton, "右下", gk_text="右下",
                             gk_align="BOTTOM_RIGHT", gk_margin=24.0)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("set_anchors_and_offsets_preset(Control.PRESET_BOTTOM_RIGHT, "
                      "Control.PRESET_MODE_KEEP_SIZE, 24.0)", code)

    # ---- 图片 ----
    def test_ui_image_exports_blender_image_name(self):
        """Blender 图片名 -> `res://art/ui_<名字>_<摘要>.png`(和导出侧同一算法)。"""
        from BlenderGameKit import curve_assets

        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeUiImage, "图标", gk_image="头像",
                             gk_width=64.0)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        expected = curve_assets.image_res_path("头像")
        self.assertIn(f'"{expected}"', code)
        self.assertIn("TextureRect.new()", code)
        self.assertIn("EXPAND_IGNORE_SIZE", code)
        # 填了宽、高留 0 -> 运行时按原图比例推出来,不能拉变形
        self.assertIn("STRETCH_KEEP_ASPECT_CENTERED", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_ui_image_name_collision_avoided(self):
        """两个中文图片名不能算出同一个文件名(否则两张图互相覆盖)。"""
        from BlenderGameKit import curve_assets

        first = curve_assets.image_file_name("头像")
        second = curve_assets.image_file_name("立绘")
        self.assertNotEqual(first, second, "中文图片名压成 ASCII 后撞名了")

    def test_ui_image_without_picture_warns(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeUiImage, "图标")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertTrue(any("还没选图片" in w for w in warnings), warnings)

    # ---- 面板 ----
    def test_ui_panel_round_corners_and_bottom(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeUiPanel, "暂停菜单", gk_radius=16,
                             gk_width=360.0, gk_height=220.0,
                             gk_align="CENTER", gk_start_hidden=True)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("Panel.new()", code)
        self.assertIn("StyleBoxFlat.new()", code)
        self.assertIn("corner_radius_top_left = 16", code)
        # 垫底:先加面板后加按钮也不会盖住按钮
        self.assertIn("move_child(", code)
        # 暂停菜单开局必须是藏着的
        self.assertIn(".visible = false", code)
        # 底板不许吃鼠标(否则挡住按钮 / 游戏点击)
        self.assertIn("MOUSE_FILTER_IGNORE", code)

    # ---- 设置 ----
    def test_ui_set_only_touches_checked_items(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeUiSet, "淡出", gk_widget="提示",
                             gk_set_alpha=True, gk_alpha=0.3)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('_gk_find_widget("提示")', code)
        self.assertIn("modulate = Color(", code)
        # 没勾的项一个字都不能出现
        self.assertNotIn(".text = str(", code)
        self.assertNotIn(".size = Vector2(", code)

    def test_ui_set_warns_without_widget_or_flags(self):
        tree = make_tree()
        event = self._ready(tree)
        empty_name = tree.new_node(NN.GKNodeUiSet, "没名字")
        gk_stub.connect(tree, event, "执行", empty_name, "执行")
        _code, warnings = gen(tree, V2)
        self.assertTrue(any("控件名" in w for w in warnings), warnings)

        tree2 = make_tree()
        event2 = self._ready(tree2)
        nothing = tree2.new_node(NN.GKNodeUiSet, "没勾", gk_widget="提示")
        gk_stub.connect(tree2, event2, "执行", nothing, "执行")
        _code2, warnings2 = gen(tree2, V2)
        self.assertTrue(any("一项都没勾" in w for w in warnings2), warnings2)

    # ---- 暂停 ----
    def test_pause_generates_apply_and_esc_watch(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeUiPauseGame, "暂停", gk_pause=True,
                             gk_menu="暂停菜单", gk_hotkey=True)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('_gk_apply_pause(true, "暂停菜单", true)', code)
        self.assertIn("_gk_arm_pause_watch(self)", code)
        # 暂停之后本脚本的每帧处理都停了 —— 唯一能读 Esc 的是信号回调
        self.assertIn("process_frame.connect(_gk_pause_watch)", code)
        self.assertIn("var _gk_pause_armed: bool = false", code)
        self.assertIn("var _gk_pause_menu: String = \"暂停菜单\"", code)
        self.assertIn("tree.paused = paused", code)
        self.assertIn("Input.MOUSE_MODE_VISIBLE", code)
        # 鼠标模式要**还原**(暂停前是锁定的,继续后还得是锁定的)
        self.assertIn("_gk_pause_mouse_was", code)
        self.assert_indent_sane(code)

    def test_pause_without_hotkey_has_no_watch(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeUiPauseGame, "继续", gk_pause=False,
                             gk_hotkey=False)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V2)
        self.assertIn('_gk_apply_pause(false, "暂停菜单", true)', code)
        self.assertNotIn("_gk_arm_pause_watch", code)
        self.assertNotIn("func _gk_pause_watch", code)

    def test_pause_hotkey_notes_esc_conflict(self):
        """勾了 Esc 热键要提示"别再接一条 Esc 输入线"(会被处理两遍)。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeUiPauseGame, "暂停", gk_hotkey=True,
                             gk_menu="菜单")
        gk_stub.connect(tree, event, "执行", node, "执行")
        ctx_notes = []
        original = codegen.context.GenContext.note

        def spy(self, message):
            ctx_notes.append(str(message))
            return original(self, message)

        with mock.patch.object(codegen.context.GenContext, "note", spy):
            gen(tree, V2)
        self.assertTrue(any("Esc" in text for text in ctx_notes), ctx_notes)

    def test_button_done_chain_survives_callback(self):
        """按钮『完成』后面的节点必须落在主流程里(不能被回调段吞掉)。

        回归(第三十七轮):按钮 handler 里 `new_section()` 之后没把写入
        目标换回来,后面那条链会写进回调段、而且在快照之后写入 ——
        整段**静默消失**(现场:暂停菜单的三个按钮只建出来第一个,
        敌人巡逻的时间轴也跟着没启动)。
        """
        tree = make_tree()
        event = self._ready(tree)
        first = tree.new_node(NN.GKNodeUiButton, "按钮一", gk_text="一")
        second = tree.new_node(NN.GKNodeUiButton, "按钮二", gk_text="二")
        gk_stub.connect(tree, event, "执行", first, "执行")
        gk_stub.connect(tree, first, "完成", second, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        lines = code.splitlines()
        ready_at = next(i for i, line in enumerate(lines)
                        if line.startswith("func _ready"))
        callback_at = next(i for i, line in enumerate(lines)
                           if "func gk_on_" in line)
        second_at = next(i for i, line in enumerate(lines)
                         if "_gk_ui_按钮二 = Button.new()" in line)
        self.assertGreater(second_at, ready_at)
        self.assertLess(second_at, callback_at,
                        f"第二个按钮被写进回调段(等于丢了):\n{code}")

    # ---- 对话框 ----
    def test_dialog_blocks_chain_until_clicked(self):
        tree = make_tree()
        event = self._ready(tree)
        dialog = tree.new_node(NN.GKNodeUiDialog, "开场白", gk_speaker="向导",
                               gk_text="你好！")
        printer = tree.new_node(NN.GKNodePrint, "之后", gk_text="对话结束")
        gk_stub.connect(tree, event, "执行", dialog, "执行")
        gk_stub.connect(tree, dialog, "完成", printer, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        lines = code.splitlines()
        show_at = next(i for i, line in enumerate(lines)
                       if "_gk_dialog_show(self," in line)
        wait_at = next(i for i, line in enumerate(lines)
                       if "while not _gk_dialog_clicked:" in line)
        hide_at = next(i for i, line in enumerate(lines)
                       if "_gk_dialog_hide()" in line)
        print_at = next(i for i, line in enumerate(lines)
                        if 'print("对话结束")' in line)
        # 顺序必须是:显示 -> 等点按 -> 关掉 -> 才继续后面的逻辑
        self.assertLess(show_at, wait_at)
        self.assertLess(wait_at, hide_at)
        self.assertLess(hide_at, print_at, f"后面的逻辑不能抢在对话框前面:\n{code}")
        # 打字中点一下 = 先补完,再点一下才关
        self.assertIn("if _gk_dialog_typing():", code)
        self.assertIn("_gk_dialog_finish()", code)
        # 点按采集走 _input(不挑控件:点哪儿、按哪个键都算下一句)
        self.assertIn("func gk_dialog_input_event(event: InputEvent) -> void:", code)
        self.assertIn("gk_dialog_input_event(event)", code)
        self.assertIn("AUTOWRAP_WORD_SMART", code)
        self.assert_indent_sane(code)

    def test_dialog_panel_anchored_bottom_center(self):
        """对话框面板挂"底部居中"锚点 —— 窗口变了对话框跟着走。"""
        tree = make_tree()
        event = self._ready(tree)
        dialog = tree.new_node(NN.GKNodeUiDialog, "开场白", gk_text="你好",
                               gk_width=800.0, gk_height=160.0, gk_margin=40.0)
        gk_stub.connect(tree, event, "执行", dialog, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("Control.PRESET_CENTER_BOTTOM", code)
        self.assertIn("Control.PRESET_MODE_KEEP_SIZE, margin)", code)
        self.assertIn('_gk_dialog_show(self, "", str("你好"), 30.0, "", 800.0, 160.0, 40.0)',
                      code)

    def test_dialog_portrait_reuses_image_export_path(self):
        from BlenderGameKit import curve_assets

        tree = make_tree()
        event = self._ready(tree)
        dialog = tree.new_node(NN.GKNodeUiDialog, "开场白", gk_text="你好",
                               gk_image="立绘")
        gk_stub.connect(tree, event, "执行", dialog, "执行")
        code, _ = gen(tree, V2)
        self.assertIn(f'"{curve_assets.image_res_path("立绘")}"', code)

    def test_ui_helpers_absent_without_ui_nodes(self):
        """老约束:没用界面节点时,一个 UI 辅助函数都不许出现。"""
        tree = make_tree()
        event = self._ready(tree)
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="hi")
        gk_stub.connect(tree, event, "执行", printer, "执行")
        code, _ = gen(tree, V2)
        for helper in ("_gk_ensure_ui", "_gk_dialog_show", "_gk_apply_pause",
                       "_gk_find_widget", "_gk_list_body", "_gk_list_fill",
                       "_gk_widget_text"):
            self.assertNotIn(helper, code)


class TestUiRound38(Base):
    """界面第二版(第三十八轮):列表 / 列表填充 / 缓动 / 九宫格 / 输入框 / 焦点。

    这一批的共同约束(真 Godot 实测):
      * **容器会完全接管子控件的位置和尺寸** —— 挂进『界面_列表』的控件
        必须跳过摆位,尺寸改用 `custom_minimum_size`(写 position 是白写);
      * 列表是**两层**(ScrollContainer + 里面排版的 VBox/HBox),
        子控件得挂里层才排队;
      * 输入框的续接走 `text_submitted` 回调(回车才触发),
        所以『提交』出口的链必须在**独立函数**里、『完成』出口留在主流程。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    # ---- 列表 ----
    def test_list_container_two_layers_and_child_layout(self):
        tree = make_tree()
        event = self._ready(tree)
        lst = tree.new_node(NN.GKNodeUiList, "存档列表", gk_width=300.0,
                            gk_height=240.0, gk_separation=10)
        button = tree.new_node(NN.GKNodeUiButton, "存档1", gk_text="存档1",
                               gk_parent_widget="存档列表",
                               gk_x=99.0, gk_y=99.0)
        gk_stub.connect(tree, event, "执行", lst, "执行")
        gk_stub.connect(tree, lst, "完成", button, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        # 两层:外层滚动容器(名字 = 节点名) + 里层排版的 VBox
        self.assertIn("ScrollContainer.new()", code)
        self.assertIn("VBoxContainer.new()", code)
        self.assertIn('"separation", 10', code)
        # 子控件挂到**里层**(挂外层会浮着不排队)
        self.assertIn('_gk_list_body("存档列表")', code)
        # 列表自己不在容器里 -> 用 size
        self.assertIn(".size = Vector2(300.0, 240.0)", code)
        # 列表里的按钮由容器排版:跳过 X / Y,尺寸走 custom_minimum_size
        self.assertIn(".custom_minimum_size = Vector2(160.0, 48.0)", code)
        self.assertNotIn("Vector2(99.0, 99.0)", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_list_child_skips_anchor_preset_too(self):
        """选了对齐也不行:容器里的控件由容器排版,锚点同样是白写。"""
        tree = make_tree()
        event = self._ready(tree)
        lst = tree.new_node(NN.GKNodeUiList, "列表")
        button = tree.new_node(NN.GKNodeUiButton, "项", gk_text="项",
                               gk_parent_widget="列表", gk_align="CENTER")
        gk_stub.connect(tree, event, "执行", lst, "执行")
        gk_stub.connect(tree, lst, "完成", button, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertNotIn("set_anchors_and_offsets_preset", code)

    def test_horizontal_list_uses_hbox_and_can_drop_scroll(self):
        tree = make_tree()
        event = self._ready(tree)
        toolbar = tree.new_node(NN.GKNodeUiList, "工具栏", gk_vertical=False,
                                gk_scroll=False)
        gk_stub.connect(tree, event, "执行", toolbar, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        # 不滚动 = 只有一层(直接就是 HBox,不需要外面套滚动容器)
        self.assertIn("HBoxContainer.new()", code)
        self.assertNotIn("ScrollContainer.new()", code)
        # 纯排版容器不吃鼠标:点空白应该穿到游戏 / 下面的控件
        self.assertIn("Control.MOUSE_FILTER_IGNORE", code)

    def test_list_alone_does_not_need_body_helper(self):
        """只建一个空列表时不需要 `_gk_list_body`(没子控件要挂里层)。"""
        tree = make_tree()
        event = self._ready(tree)
        lst = tree.new_node(NN.GKNodeUiList, "空列表")
        gk_stub.connect(tree, event, "执行", lst, "执行")
        code, _ = gen(tree, V2)
        self.assertNotIn("_gk_list_body", code)

    # ---- 列表填充 ----
    def test_list_fill_clears_then_rebuilds_rows(self):
        tree = make_tree()
        event = self._ready(tree)
        lst = tree.new_node(NN.GKNodeUiList, "日志列表")
        fill = tree.new_node(NN.GKNodeUiListFill, "填日志", gk_list="日志列表",
                             gk_text="第一名|第二名| 第三名 ")
        gk_stub.connect(tree, event, "执行", lst, "执行")
        gk_stub.connect(tree, lst, "完成", fill, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        # 竖线分隔 + 两头空白去掉
        self.assertIn('["第一名", "第二名", "第三名"]', code)
        self.assertIn('_gk_list_fill("日志列表"', code)
        # 先清空旧行再建(每帧刷不会越堆越多);立刻 remove_child 而不是
        # 只 queue_free(等帧末的话那一帧新旧行同时占位,列表会抖)
        self.assertIn("body.remove_child(child)", code)
        self.assertIn("child.queue_free()", code)
        self.assertIn("label.text = str(item)", code)
        self.assert_indent_sane(code)

    def test_list_fill_reads_array_variable(self):
        tree = make_tree()
        event = self._ready(tree)
        lst = tree.new_node(NN.GKNodeUiList, "排行榜")
        fill = tree.new_node(NN.GKNodeUiListFill, "刷新", gk_list="排行榜",
                             gk_var="分数榜")
        gk_stub.connect(tree, event, "执行", lst, "执行")
        gk_stub.connect(tree, lst, "完成", fill, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("var 分数榜: Array", code)
        self.assertIn('_gk_list_fill("排行榜", 分数榜', code)

    def test_list_fill_warns_on_bad_list_name(self):
        tree = make_tree()
        event = self._ready(tree)
        no_name = tree.new_node(NN.GKNodeUiListFill, "填日志", gk_text="A")
        gk_stub.connect(tree, event, "执行", no_name, "执行")
        _code, warnings = gen(tree, V2)
        self.assertTrue(any("没有填『列表』" in w for w in warnings), warnings)

        tree2 = make_tree()
        event2 = self._ready(tree2)
        wrong = tree2.new_node(NN.GKNodeUiListFill, "填日志", gk_list="没有的列表",
                               gk_text="A")
        gk_stub.connect(tree2, event2, "执行", wrong, "执行")
        _code2, warnings2 = gen(tree2, V2)
        self.assertTrue(any("没有一个叫这个名字" in w for w in warnings2), warnings2)

    # ---- 缓动 ----
    def test_tween_alpha_absolute_and_wait(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeUiTween, "淡入", gk_widget="提示",
                             gk_property="ALPHA", gk_to=1.0, gk_time=0.5,
                             gk_wait=True)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('"modulate:a"', code)
        self.assertIn("Tween.TRANS_SINE", code)
        self.assertIn("Tween.EASE_OUT", code)
        # 勾了"等它播完" -> 后面的节点等动画结束再走
        self.assertIn("await", code)
        self.assertIn(".finished", code)
        self.assert_indent_sane(code)

    def test_tween_position_is_relative(self):
        """位置 X / Y 是**相对**的:写法必须是 `现在的位置 + 位移`。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeUiTween, "滑入", gk_widget="提示",
                             gk_property="POS_X", gk_to=80.0, gk_delay=0.2)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('"position:x"', code)
        self.assertIn(".position.x + (80.0)", code)
        self.assertIn("tween_interval(0.2)", code)
        # 没勾"等播完"就不许有 await(边播边往下走)
        self.assertNotIn("await", code)

    def test_tween_warns_without_widget(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeUiTween, "淡入")
        gk_stub.connect(tree, event, "执行", node, "执行")
        _code, warnings = gen(tree, V2)
        self.assertTrue(any("控件名" in w for w in warnings), warnings)

    # ---- 九宫格 ----
    def test_ninepatch_slices_and_bottom_layer(self):
        from BlenderGameKit import curve_assets

        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeUiNinePatch, "标题条", gk_image="头像",
                             gk_slice=16, gk_bottom=True, gk_width=400.0,
                             gk_height=60.0)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("NinePatchRect.new()", code)
        for side in ("left", "top", "right", "bottom"):
            self.assertIn(f".patch_margin_{side} = 16", code)
        # 垫底:铺到兄弟控件下面
        self.assertIn("move_child(", code)
        # 图片路径和『界面_图片』同一套算法(Blender 图片名 -> art/ui_*.png)
        self.assertIn(f'"{curve_assets.image_res_path("头像")}"', code)
        self.assertIn("ResourceLoader.exists(", code)
        self.assert_indent_sane(code)

    def test_ninepatch_image_collected_for_export(self):
        """九宫格的图片也要进导出清单(漏了它 -> 游戏里标题条是空的)。

        回归(第三十八轮):`ui_image_names` 的白名单只有『界面_图片』和
        『对话框』,新加的『界面_九宫格』没进去 —— PNG 不落盘,
        运行时 load 出 null。探针现场:patch_margin 全对、texture = null。
        """
        from BlenderGameKit import asset_export as ae
        from BlenderGameKit import exporter as ex

        tree = make_tree()
        event = self._ready(tree)
        bar = tree.new_node(NN.GKNodeUiNinePatch, "标题条", gk_image="标题条图")
        logo = tree.new_node(NN.GKNodeUiImage, "徽标", gk_image="徽标")
        dialog = tree.new_node(NN.GKNodeUiDialog, "开场白", gk_text="你好",
                               gk_image="立绘")
        raw = tree.new_node(NN.GKNodeUiImage, "外部图", gk_image="res://art/x.png")
        gk_stub.connect(tree, event, "执行", bar, "执行")
        gk_stub.connect(tree, bar, "完成", logo, "执行")
        gk_stub.connect(tree, logo, "完成", dialog, "执行")
        gk_stub.connect(tree, dialog, "完成", raw, "执行")
        with mock.patch.object(ex, "resolve_scene_tree",
                               return_value=(tree, "测试")):
            names = ae.ui_image_names(object())
        self.assertIn("标题条图", names)
        self.assertIn("徽标", names)
        self.assertIn("立绘", names)
        # `res://` 开头的路径原样用,不在"要导出的图片名"里
        self.assertNotIn("res://art/x.png", names)

    def test_ninepatch_without_image_warns(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeUiNinePatch, "标题条")
        gk_stub.connect(tree, event, "执行", node, "执行")
        _code, warnings = gen(tree, V2)
        self.assertTrue(any("还没选图片" in w for w in warnings), warnings)

    # ---- 输入框 ----
    def test_ui_input_submit_chain_in_callback(self):
        tree = make_tree()
        event = self._ready(tree)
        field = tree.new_node(NN.GKNodeUiInput, "昵称", gk_placeholder="输入昵称")
        submit = tree.new_node(NN.GKNodePrint, "提交后", gk_text="提交了")
        after = tree.new_node(NN.GKNodePrint, "建完继续", gk_text="建完继续")
        gk_stub.connect(tree, event, "执行", field, "执行")
        gk_stub.connect(tree, field, "提交", submit, "执行")
        gk_stub.connect(tree, field, "完成", after, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("LineEdit.new()", code)
        self.assertIn('placeholder_text = "输入昵称"', code)
        self.assertIn("text_submitted.connect(", code)
        # 输入框必须吃鼠标,不然点不进去打不了字
        self.assertIn("Control.MOUSE_FILTER_STOP", code)

        lines = code.splitlines()
        cb_at = next(i for i, line in enumerate(lines)
                     if line.startswith("func gk_on_") and "(_text: String)" in line)
        submit_at = next(i for i, line in enumerate(lines)
                         if 'print("提交了")' in line)
        after_at = next(i for i, line in enumerate(lines)
                        if 'print("建完继续")' in line)
        # 『提交』的链在回调函数里(回车才走),『完成』的链留在主流程
        self.assertGreater(submit_at, cb_at)
        self.assertLess(after_at, cb_at)
        # 回调参数没用上要带下划线,否则 Godot 报 UNUSED_PARAMETER
        self.assertIn("(_text: String)", lines[cb_at])
        self.assert_indent_sane(code)

    # ---- 取文字 / 设焦点 ----
    def test_ui_read_text_uses_helper(self):
        tree = make_tree()
        event = self._ready(tree)
        read = tree.new_node(NN.GKNodeUiReadText, "读昵称", gk_widget="昵称")
        printer = tree.new_node(NN.GKNodePrint, "打印")
        gk_stub.connect(tree, read, "文字", printer, "内容")
        gk_stub.connect(tree, event, "执行", printer, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('_gk_widget_text("昵称")', code)
        self.assertIn("func _gk_widget_text(", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_ui_read_text_warns_without_widget(self):
        tree = make_tree()
        event = self._ready(tree)
        read = tree.new_node(NN.GKNodeUiReadText, "读昵称")
        printer = tree.new_node(NN.GKNodePrint, "打印")
        gk_stub.connect(tree, read, "文字", printer, "内容")
        gk_stub.connect(tree, event, "执行", printer, "执行")
        _code, warnings = gen(tree, V2)
        self.assertTrue(any("控件名" in w for w in warnings), warnings)

    def test_ui_focus_grab_and_release(self):
        tree = make_tree()
        event = self._ready(tree)
        grab = tree.new_node(NN.GKNodeUiFocus, "聚焦继续", gk_widget="继续")
        printer = tree.new_node(NN.GKNodePrint, "后面", gk_text="ok")
        gk_stub.connect(tree, event, "执行", grab, "执行")
        gk_stub.connect(tree, grab, "完成", printer, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('_gk_find_widget("继续")', code)
        self.assertIn(".grab_focus()", code)

        # 留空 = 清掉焦点(回到鼠标操作)
        tree2 = make_tree()
        event2 = self._ready(tree2)
        clear = tree2.new_node(NN.GKNodeUiFocus, "清焦点", gk_widget="")
        gk_stub.connect(tree2, event2, "执行", clear, "执行")
        code2, warnings2 = gen(tree2, V2)
        self.assertEqual(warnings2, [], warnings2)
        self.assertIn("gui_get_focus_owner()", code2)
        self.assertIn("release_focus()", code2)


class TestUiDesignTree(Base):
    """第四十轮:界面设计图(『游戏界面』)+ 套用式那条线。

    用户选的方案:
      * 设计图只管"长什么样",玩法图的『界面_套用设计』一键建出来;
      * 界面控件节点**两棵树都能放**(老图一字不变);
      * 点击行为用『界面_按钮点击』按名字接(控件的名字 = 设计图里的节点名)。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def _design(self, name="主界面"):
        design = gk_stub.StubNodeTree(name)
        design.bl_idname = "GKUiDesignTree"
        return design

    def _gen_with_design(self, design, tree, design_name=""):
        """把 design 挂到 bpy.data 上,再跑一次生成(用完还原)。"""
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[design]))
        try:
            return gen(tree, V2)
        finally:
            if original is None:
                try:
                    delattr(bpy_module(), "data")
                except AttributeError:
                    pass
            else:
                setattr(bpy_module(), "data", original)

    # ---- 树类型 / 菜单归属 ----
    def test_ui_widget_nodes_fit_both_trees(self):
        from BlenderGameKit import properties as props
        for cls in (NN.GKNodeUiText, NN.GKNodeUiBar, NN.GKNodeUiButton,
                    NN.GKNodeUiImage, NN.GKNodeUiPanel, NN.GKNodeUiList,
                    NN.GKNodeUiNinePatch, NN.GKNodeUiInput):
            self.assertTrue(props.node_fits_tree(cls, "GKUiDesignTree"),
                            cls.bl_label + " 应该能放进设计图")
            # 老图不受影响:玩法图照旧能放(用户选的"两棵树都能放")
            self.assertTrue(props.node_fits_tree(cls, "GKGameLogicTree"),
                            cls.bl_label + " 仍然要能放进玩法图")
        # 动作类不进设计图 —— 它们管"运行时改控件",是玩法图的事
        self.assertFalse(props.node_fits_tree(NN.GKNodeUiHide, "GKUiDesignTree"))
        self.assertFalse(props.node_fits_tree(NN.GKNodeUiApplyDesign,
                                              "GKUiDesignTree"))

    def test_design_tree_menu_lists_only_widgets(self):
        from BlenderGameKit import properties as props
        labels = [cls.bl_label
                  for _cat, items in props.node_menu_entries("GKUiDesignTree")
                  for cls in items]
        self.assertIn("界面_文本", labels)
        self.assertIn("界面_面板", labels)
        self.assertNotIn("界面_隐藏控件", labels)

    # ---- 『界面_套用设计』----
    def test_apply_design_builds_widgets_parent_first(self):
        """父控件必须**先建**:子控件是按名字找父亲挂进去的。"""
        design = self._design()
        # 故意把子控件摆在前面 —— 生成时必须自己排回"父先子后"
        design.new_node(NN.GKNodeUiText, "菜单标题", gk_text="暂停",
                        gk_parent_widget="暂停菜单")
        design.new_node(NN.GKNodeUiPanel, "暂停菜单",
                        gk_width=440.0, gk_height=340.0)
        tree = make_tree()
        event = self._ready(tree)
        apply_node = tree.new_node(NN.GKNodeUiApplyDesign, "套用主界面")
        gk_stub.connect(tree, event, "执行", apply_node, "执行")
        code, warnings = self._gen_with_design(design, tree)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_ui_暂停菜单", code)
        self.assertIn("_gk_ui_菜单标题", code)
        panel_at = code.find("_gk_ui_暂停菜单 = ")
        child_at = code.find('_gk_find_widget("暂停菜单")')
        self.assertGreater(panel_at, -1, code)
        self.assertGreater(child_at, -1, code)
        self.assertLess(panel_at, child_at,
                        "父控件必须排在子控件前面:\n" + code)

    def test_apply_design_widget_in_design_tree_list_uses_container(self):
        """设计图里的『界面_列表』也要被认出来 —— 子控件交给容器排版。

        回归点:列表住在**设计图**里,而父控件判断以前只扫玩法图的节点,
        不扫的话会多写一堆 position(容器会覆盖,写了白写)。
        """
        design = self._design()
        design.new_node(NN.GKNodeUiList, "设计列表",
                        gk_width=320.0, gk_height=200.0)
        design.new_node(NN.GKNodeUiButton, "设计按钮", gk_text="槽",
                        gk_parent_widget="设计列表")
        tree = make_tree()
        event = self._ready(tree)
        apply_node = tree.new_node(NN.GKNodeUiApplyDesign, "套用主界面")
        gk_stub.connect(tree, event, "执行", apply_node, "执行")
        code, warnings = self._gen_with_design(design, tree)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('_gk_list_body("设计列表")', code)
        # 挂进容器的控件用最小尺寸,而不是自己设 size
        self.assertIn("custom_minimum_size", code)

    def test_apply_design_skips_action_nodes(self):
        """兜底:设计图里混进动作节点时,『套用设计』不该把它"建出来"。

        真 Blender 里节点基类的 poll 会**直接拒绝**把动作节点放进设计图
        (冒烟测试锁着这条);桩环境不校验,所以这里再锁一道 codegen 过滤。
        """
        design = self._design()
        design.new_node(NN.GKNodeUiText, "标题", gk_text="标题")
        design.new_node(NN.GKNodeUiHide, "藏起来", gk_widget="标题")
        code, warnings = self._gen_with_design(
            design, self._apply_tree(design))
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_ui_标题", code)
        # 『界面_隐藏控件』的产物(.visible = false)不该出现
        self.assertNotIn(".visible = false", code)

    def _apply_tree(self, design):
        tree = make_tree()
        event = self._ready(tree)
        apply_node = tree.new_node(NN.GKNodeUiApplyDesign, "套用主界面")
        gk_stub.connect(tree, event, "执行", apply_node, "执行")
        return tree

    def test_apply_design_without_tree_warns(self):
        tree = self._apply_tree(None)
        _code, warnings = gen(tree, V2)
        self.assertTrue(any("界面设计图" in w for w in warnings), warnings)

    def test_apply_design_empty_tree_warns(self):
        design = self._design("空界面")
        _code, warnings = self._gen_with_design(
            design, self._apply_tree(design))
        self.assertTrue(any("没有控件" in w for w in warnings), warnings)

    # ---- 『界面_按钮点击』----
    def test_on_click_binds_once_and_runs_click_chain(self):
        tree = make_tree()
        event = self._ready(tree)
        click = tree.new_node(NN.GKNodeUiOnClick, "开始按钮点击", gk_widget="开始")
        printer = tree.new_node(NN.GKNodePrint, "开跑", gk_text="go")
        gk_stub.connect(tree, event, "执行", click, "执行")
        gk_stub.connect(tree, click, "点击", printer, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('_gk_find_widget("开始")', code)
        self.assertIn("is BaseButton", code)
        # 幂等:接之前先问 is_connected(放每帧的链上也不会越点越多)
        self.assertIn(".pressed.is_connected(", code)
        # 点击链在独立回调里(不是接上就立刻跑)
        lines = code.splitlines()
        callback_at = next(i for i, line in enumerate(lines) if "func gk_on_" in line)
        print_at = next(i for i, line in enumerate(lines) if 'print("go")' in line)
        self.assertGreater(print_at, callback_at, code)

    def test_on_click_without_widget_name_warns(self):
        tree = make_tree()
        event = self._ready(tree)
        click = tree.new_node(NN.GKNodeUiOnClick, "按钮点击", gk_widget="")
        gk_stub.connect(tree, event, "执行", click, "执行")
        _code, warnings = gen(tree, V2)
        self.assertTrue(any("控件名" in w for w in warnings), warnings)


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
        # ── 读屏必须走 hint_screen_texture 的 uniform(第五十一轮修)──────
        # Godot 4.7 删掉了内置的 SCREEN_TEXTURE,原来那句
        # `texture(SCREEN_TEXTURE, SCREEN_UV)` 让材质**根本编译不过**
        # (真 Godot 编译报 "SCREEN_TEXTURE 已被移除")。
        self.assertIn("hint_screen_texture", code)
        self.assertNotIn("SCREEN_TEXTURE", code)
        self.assertIn("texture(screen_tex, SCREEN_UV)", code)
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


class TestRound51ScreenVignette(Base):
    """第五十一轮:阶段④屏幕氛围 —— 『屏幕_暗角』。

    Godot 的 `Environment` **没有内置 vignette**,所以暗角只能用全屏着色器做
    (和『屏幕_滤镜』同一套实现,但**各占一层**,两个能叠着用)。
    判据:
      * 生成自己的着色器常量 + 全屏 ColorRect,参数(强度/内圈/柔和/颜色)都写进去;
      * 暗角层在**滤镜之上**(92 > 90)、闪白/转场之下(100);
      * 『过渡秒数』> 0 走 tween,否则直接设值;
      * 没用它的图里一个暗角字符都不多(老产物逐字不变)。
    """

    def test_builds_its_own_shader_layer(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        node = tree.new_node(NN.GKNodeScreenVignette, "暗角", gk_amount=0.7,
                             gk_radius=0.55, gk_edge=0.35, gk_fade=0.0)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("const _GK_VIGNETTE_SHADER :=", code)
        self.assertIn("shader_type canvas_item;", code)
        # 和滤镜同一条约定:Godot 4.7 没有内置 SCREEN_TEXTURE,读屏走 uniform
        self.assertIn("hint_screen_texture", code)
        self.assertNotIn("SCREEN_TEXTURE", code)
        self.assertIn("texture(screen_tex, SCREEN_UV)", code)
        # 到中心的距离用屏幕对角线一半归一化(0 = 中心,1 = 角)
        self.assertIn("0.70710678", code)
        self.assertIn("smoothstep(vignette_radius, vignette_radius + vignette_edge", code)
        self.assertIn("func _gk_ensure_vignette(", code)
        self.assertIn('set_shader_parameter("vignette_radius", 0.55)', code)
        self.assertIn('set_shader_parameter("vignette_edge", 0.35)', code)
        self.assertIn('set_shader_parameter("vignette_color", Color(0.0, 0.0, 0.0, 1.0))',
                      code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_layer_sits_above_filter_below_flash(self):
        """暗角 92:盖住滤镜(90)与界面(10),但低于闪白 / 转场(100)。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        node = tree.new_node(NN.GKNodeScreenVignette, "暗角")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V2)
        self.assertIn('_gk_vignette_layer.name = "GKVignette"', code)
        self.assertIn("layer = 92", code)
        self.assertIn("Control.MOUSE_FILTER_IGNORE", code)

    def test_stacks_with_filter(self):
        """两个能叠着用:各有各的着色器常量 / 材质 / 层。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        tint = tree.new_node(NN.GKNodeScreenFilter, "受击红", gk_amount=0.6)
        ghost = tree.new_node(NN.GKNodeScreenVignette, "暗角", gk_amount=0.5)
        gk_stub.connect(tree, event, "执行", tint, "执行")
        gk_stub.connect(tree, tint, "完成", ghost, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("const _GK_FILTER_SHADER :=", code)
        self.assertIn("const _GK_VIGNETTE_SHADER :=", code)
        self.assertIn("layer = 90", code)
        self.assertIn("layer = 92", code)

    def test_fade_uses_tween(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        node = tree.new_node(NN.GKNodeScreenVignette, "暗角", gk_amount=1.0,
                             gk_fade=0.4)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("tween_method(", code)
        self.assertIn('get_shader_parameter("vignette_amount")', code)
        self.assertIn(", 0.4)", code)

    def test_no_vignette_without_the_node(self):
        """没用暗角的图里一个暗角字符都不多(老产物逐字不变)。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        tint = tree.new_node(NN.GKNodeScreenFilter, "受击红")
        gk_stub.connect(tree, event, "执行", tint, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("_GK_FILTER_SHADER", code)
        self.assertNotIn("VIGNETTE", code)

        plain = make_tree()
        plain_event = plain.new_node(NN.GKNodeOnReady, "准备")
        printer = plain.new_node(NN.GKNodePrint, "打印", gk_text="hi")
        gk_stub.connect(plain, plain_event, "执行", printer, "执行")
        plain_code, _ = gen(plain, V2)
        self.assertNotIn("VIGNETTE", plain_code)
        self.assertNotIn("_GK_FILTER_SHADER", plain_code)

    def test_node_props_and_category(self):
        from BlenderGameKit import properties as props
        cls = NN.GKNodeScreenVignette
        self.assertEqual(cls.gk_category, "屏幕效果")
        self.assertTrue(props.node_fits_tree(cls, "GKGameLogicTree"))
        names = [entry[0] for entry in cls.gk_prop_defs]
        for wanted in ("gk_amount", "gk_radius", "gk_edge", "gk_color", "gk_fade"):
            self.assertIn(wanted, names)

    def test_embedded_shaders_follow_the_two_hard_rules(self):
        """内嵌着色器必须守两条硬规矩 —— 两条都是真机踩出来的。

        1. **只能用 ASCII**:Godot 的着色器词法分析器碰到非 ASCII 字符直接
           报 `Tokenizer: Unknown character #22522`,整个着色器编不出来
           (所以着色器里的注释只能写英文)。
        2. **不能含 `\"\"\"`**:源码是塞进 GDScript 的 `const X := \"\"\"…\"\"\"`
           里的,里面再出现三引号会提前把字符串截断 —— 整个脚本解析失败。

        真编译那一步在 `scripts/verify_shaders.py`(门禁 1c 层,要真 Godot);
        这里把"能离线查的两条"钉死,免得改着色器时又踩回去。
        """
        from BlenderGameKit.codegen import services

        sources = {
            "屏幕_滤镜": services._GD_FILTER_SHADER,
            "屏幕_暗角": services._GD_VIGNETTE_SHADER,
        }
        for label, source in sources.items():
            self.assertTrue(source.isascii(), f"{label} 的着色器里有非 ASCII 字符")
            self.assertNotIn('"""', source, f"{label} 的着色器里有三引号")
            self.assertIn("shader_type canvas_item;", source)
            # Godot 4 读屏:必须声明 hint_screen_texture 的 uniform
            self.assertIn("hint_screen_texture", source, label)
            self.assertNotIn("SCREEN_TEXTURE", source, f"{label} 用了已删除的内置变量")


class TestNodeRegistrySanity(Base):
    """节点声明表的体检:**对所有节点**都成立的那几条。

    ── 为什么单独拎出来一个类(第五十一轮)───────────────
    这几条原来挂在 `TestScreenFilterNodes` 里,但它们跟"屏幕滤镜"没有关系 ——
    查的是"整张节点表有没有拼错类型 / 漏登记属性"。加暗角那轮顺手分出来,
    免得下次又有人在滤镜类里找"为什么节点注册失败"。
    """

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

    def test_digit_keys_get_distinct_actions(self):
        """数字键的动作名不能撞成同一个(第四十八轮:灯光示例踩到)。

        `snake_case("1", "key")` 因为"以数字开头"直接落到 fallback ——
        于是 1~9 全变成同一个动作 `key_key`:按一下 1 会把所有数字键绑的
        逻辑一起触发。`key_1` 本身是合法标识符,所以数字键单独拼。
        """
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        key_one = tree.new_node(NN.GKNodeKey, "键1", gk_key="1")
        key_two = tree.new_node(NN.GKNodeKey, "键2", gk_key="2")
        branch_one = tree.new_node(NN.GKNodeBranch, "如果1")
        branch_two = tree.new_node(NN.GKNodeBranch, "如果2")
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="d")
        gk_stub.connect(tree, event, "执行", branch_one, "执行")
        gk_stub.connect(tree, key_one, "结果", branch_one, "条件")
        gk_stub.connect(tree, branch_one, "是", branch_two, "执行")
        gk_stub.connect(tree, key_two, "结果", branch_two, "条件")
        gk_stub.connect(tree, branch_two, "是", printer, "执行")
        self.assertEqual(key_one.resolved_action(), "key_1")
        self.assertEqual(key_two.resolved_action(), "key_2")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("ACTION_KEY_1", code)
        self.assertIn("ACTION_KEY_2", code)

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

        ⚠ 第七十六轮把顺序改了:connect 从 play() **之后**挪到**之前** ——
        见 test_started_listener_is_registered_before_play。判据变成
        "在 『有这段动画』 那条分支里、在警告之前"。
        """
        code, _ = self._build({"结束时": "播完了"})
        lines = code.splitlines()
        has_at = next(i for i, ln in enumerate(lines)
                      if ".has_animation(" in ln)
        connect_at = next(i for i, ln in enumerate(lines)
                          if "animation_finished.connect(" in ln)
        warn_at = next(i for i, ln in enumerate(lines) if "push_warning" in ln)
        self.assertGreater(connect_at, has_at, "接线要在『有这段动画』分支里")
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

    # ── 第七十六轮:顺手修的真机 bug ────────────────────────────────
    # 三个事件出口的代码分别落在**别的函数**里(_process / 信号回调),
    # 而它们引用的 `_ap2`(播放器)/ `_anim1`(动画名)是 **_ready 里的局部
    # 变量** —— 真 Godot 报
    #     Parse Error: Identifier "_ap2" not declared in the current scope
    # 整份脚本加载不了。老测试只 grep 字符串(`is_playing()` 在不在),
    # 名字确实出现过,所以这条一直没被抓到。

    def test_running_block_is_self_contained(self):
        """`_process` 里用到的播放器必须**在 _process 里自己声明**。"""
        code, _ = self._build({"每帧": "播放中"})
        body = self.functions(code)["_process"]
        used = set(re.findall(r"(?<!_gk)_(?:ap|anim)\d+", body))
        self.assertTrue(used, "『每帧』那段应该引用播放器/动画名")
        for name in sorted(used):
            self.assertIn(f"var {name}", body,
                          f"_process 用了 {name},却没在本函数里声明 —— "
                          f"跨函数引用会让 Godot 拒绝整份脚本")

    def test_started_listener_is_registered_before_play(self):
        """『开始时』的回调必须在 `play()` **之前**接上。

        真机验过(第七十六轮):`play()` 会**同步**发 `animation_started` ——
        先 play 再 connect 就漏掉这一次。而『动画_播放』接在 每当_准备 后面
        时那**就是唯一的一次**,于是『开始时』永远不响。
        """
        code, _ = self._build({"开始时": "开始"})
        lines = code.splitlines()
        play_at = next(i for i, ln in enumerate(lines) if ".play(" in ln)
        connect_at = next(i for i, ln in enumerate(lines)
                          if "animation_started.connect(" in ln)
        self.assertLess(connect_at, play_at,
                        "要先接信号再 play,否则第一次播放接不到")

    def test_signal_callbacks_do_not_borrow_locals(self):
        """信号回调里不许引用别的函数的局部变量。"""
        code, _ = self._build({"开始时": "开始", "结束时": "完了"})
        funcs = self.functions(code)
        callbacks = {name: body for name, body in funcs.items()
                     if name.startswith(("_gk_started_", "_gk_finished_"))}
        self.assertEqual(len(callbacks), 2, sorted(funcs))
        for name, body in callbacks.items():
            borrowed = re.findall(r"(?<!_gk)_(?:ap|anim)\d+", body)
            self.assertEqual(borrowed, [],
                             f"{name} 引用了别的函数的局部变量 {borrowed}")


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
        # ⚠ 形参叫 `base` 会被 Godot 报"与内置函数同名"(第七十轮告警修复)
        self.assertIn("func _gk_layer_player(host: Node, layer: int) -> Node:",
                      code)
        self.assertNotIn("_gk_layer_player(base:", code)

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

    def test_per_scene_dimension(self):
        """第八十七轮:dimension=None 时**逐场景**用各自的维度。

        一个 .blend 里 2D 关卡和 3D 关卡混着放(『关卡预设』能建出来)——
        各导各的;给了值的老调用方行为一字不变。
        """
        from BlenderGameKit import asset_export as ae
        from BlenderGameKit import exporter as ex
        scenes = [types.SimpleNamespace(name="2D关", dim="2"),
                  types.SimpleNamespace(name="3D关", dim="3")]
        seen = {}

        def fake_pipeline(scene, directory, dimension="3", script_name=None,
                          scene_name="World"):
            seen[scene.name] = dimension
            return {"written": [], "warnings": [], "materials": 0,
                    "bodies": 0, "cameras": 0, "lights": 0, "error": None}

        patches = [
            mock.patch.object(ex, "exportable_scenes", lambda: scenes),
            mock.patch.object(ex, "resolve_scene_tree",
                              lambda scene: (None, "没有逻辑图")),
            mock.patch.object(ex, "build_settings",
                              lambda scene: {"dimension": scene.dim}),
            mock.patch.object(ae, "_activate_scene", lambda scene: None),
            mock.patch.object(ae, "_active_scene", lambda: None),
            mock.patch.object(ae, "_safe_scene_name", lambda name: name),
            mock.patch.object(ae, "export_assets_pipeline", fake_pipeline),
            mock.patch.object(ae.gk_log, "info"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

        directory = self._project()
        out = ae.export_all_scenes(directory, dimension=None)
        self.assertIsNone(out["error"], out)
        self.assertEqual(seen, {"2D关": "2", "3D关": "3"})

        seen.clear()
        ae.export_all_scenes(directory, dimension="3")
        self.assertEqual(seen, {"2D关": "3", "3D关": "3"})


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

    def test_time_stop_key_freezes_and_unfreezes(self):
        """时停(第三十五轮,用户:"在上帝摄像机时可以添加时停")。

        按 T 把世界冻住(`tree.paused`),再按一下解冻 —— 冻住时场景动画 /
        物理 / 角色逻辑全停,只有上帝机还能飞。两条守卫都要在:
          * 环境脚本自己 PROCESS_MODE_ALWAYS —— 不然冻住之后没人读按键,
            "按 T 解冻"永远不会发生(把自己锁在冻住的世界里);
          * **开**只认上帝视角(玩家相机时按 T 不该冻住游戏),
            **关**不挑视角 + 离开上帝机自动解冻(兜底)。
        """
        text = self._env(god="true")
        self.assertIn("const _GK_TIME_STOP_KEY := KEY_T", text)
        self.assertIn("func _gk_time_stop_tick() -> void:", text)
        self.assertIn("tree.paused = true", text)
        self.assertIn("tree.paused = false", text)
        self.assertIn("process_mode = Node.PROCESS_MODE_ALWAYS", text)
        # 开只认上帝视角
        self.assertIn("and _gk_god_cam.is_current())", text)
        # 离开上帝机就解冻(不然玩家看着一个冻住的画面按什么都没反应)
        self.assertIn("时停:关(离开上帝机)", text)

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


class TestCardPropertiesDeclared(Base):
    """卡片上引用的属性必须真的声明过(第三十九轮抓到的真 bug)。

    ── 为什么值得一条**全局**测试 ──────────────────────────────────
    『动画_状态』的 `gk_anim_action_2`(同时播 2)当年**只在 draw_buttons
    里被引用,从来没进 gk_prop_defs** —— 卡片画到那一行直接 AttributeError,
    整块卡片画一半就报错(用户原话:"一部分节点卡片无作用"),"同时播"
    这个功能也跟着不存在。这种"引用了没声明"的错,盯单个节点的测试抓不到,
    所以这里把**所有节点类**扫一遍。
    """

    IGNORE = {"gk_doc", "gk_category", "gk_tree_type", "gk_hidden",
              "gk_order"}

    def test_every_card_property_is_declared(self):
        import inspect
        import re as _re

        bad = []
        for cls in NN.ALL_CLASSES:
            if not isinstance(cls, type):
                continue
            defined = {spec[0] for spec in
                       (getattr(cls, "gk_prop_defs", ()) or ())}
            referenced = set()
            for func_name in ("draw_buttons", "draw_buttons_ext"):
                func = cls.__dict__.get(func_name)
                if func is None:
                    continue
                try:
                    source = inspect.getsource(func)
                except OSError:
                    continue
                referenced |= set(_re.findall(r"self\.(gk_[a-z0-9_]+)",
                                              source))
            missing = sorted(name for name in referenced - self.IGNORE
                             if name not in defined
                             and not hasattr(cls, name))
            if missing:
                bad.append((cls.bl_idname, missing))
        self.assertEqual(bad, [], f"卡片引用了没声明的属性: {bad}")


class TestGodCameraRound39(Base):
    """第三十九轮:上帝机的默认功能补齐(用户 2026-10-06)。

    用户原话:"上帝机默认属性功能应有:时停、视角缩放、附身(接管角色控制)、
    内置 ESC 退出功能"。时停第三十五轮就有了(这里留一条回归),
    另外三样是这一轮新加/补齐的。
    """

    def _env(self, god="true", esc="true"):
        from BlenderGameKit import asset_export
        return asset_export.render_env_script(
            camera_name="Cam", fov="70.0", god_camera=god, esc_quit=esc)

    def test_time_stop_still_there(self):
        text = self._env()
        self.assertIn("const _GK_TIME_STOP_KEY := KEY_T", text)
        self.assertIn("func _gk_time_stop_tick() -> void:", text)

    def test_zoom_with_mouse_wheel(self):
        """视角缩放 = 滚轮改 FOV(不是往前飞 —— 飞太近会穿模)。"""
        text = self._env()
        self.assertIn("MOUSE_BUTTON_WHEEL_UP", text)
        self.assertIn("MOUSE_BUTTON_WHEEL_DOWN", text)
        self.assertIn("func _gk_god_zoom_tick() -> void:", text)
        # FOV 夹在 20~110(再大就鱼眼、再小看不清),而且只在滚轮真动过才写
        self.assertIn("clampf(_gk_god_fov - _gk_god_wheel * 4.0, 20.0, 110.0)",
                      text)
        self.assertIn("_gk_god_cam.fov = _gk_god_fov", text)

    def test_possess_takes_over_character(self):
        """附身:F 接管角色 —— WASD 驱动**角色**,相机跟在身后。"""
        text = self._env()
        self.assertIn("const _GK_POSSESS_KEY := KEY_F", text)
        self.assertIn("func _gk_god_possess_begin() -> void:", text)
        self.assertIn("func _gk_god_possess_tick(delta: float) -> void:", text)
        self.assertIn("func _gk_god_release() -> void:", text)
        # "角色"的判据 = 挂了游戏逻辑脚本(名字用户会改,不能按名字找)
        self.assertIn('has_method("_gk_god_camera_active")', text)
        # 驱动角色走 move_and_slide(直接改坐标会穿过墙)
        self.assertIn("cb.move_and_slide()", text)
        # 离开上帝机 / 目标被删了都要自动放手
        self.assertIn("if _gk_god_possess != null:", text)
        self.assertIn("放开附身", text)

    def test_esc_quits_and_yields_to_pause_menu(self):
        """内置 ESC 退出 —— 暂停菜单没占着 Esc 时才生效。"""
        text = self._env(esc="true")
        self.assertIn("esc_quit: bool = true", text)
        self.assertIn("func _gk_esc_tick() -> void:", text)
        self.assertIn("Input.is_key_pressed(KEY_ESCAPE)", text)
        self.assertIn("tree.quit()", text)
        # 世界冻住时也得按得应 —— 环境脚本暂停也照跑
        self.assertIn("if (god_camera or esc_quit) and not Engine.is_editor_hint():",
                      text)
        off = self._env(esc="false")
        self.assertIn("esc_quit: bool = false", off)

    def _esc_quit_of(self, build):
        """按给定的图算一遍 esc_quit(环境脚本字段)。"""
        from BlenderGameKit import asset_export as ae
        from BlenderGameKit import exporter as ex

        tree = make_tree()
        build(tree)
        with mock.patch.object(ex, "resolve_scene_tree",
                               return_value=(tree, "测试")):
            return ae._env_fields(object())["esc_quit"]

    def test_esc_quit_yields_when_graph_already_uses_esc(self):
        """图里已经有人管 Esc -> 内置退出必须让位(一个键不能干两件事)。

        两种情况:『界面_暂停游戏』勾了 Esc 热键(那是暂停);
        或者用户自己接了『按键』(Esc)(关卡 / 曲线 / 物理三个示例都是
        "按 Esc 退出游戏",内置的再抢一遍就会把用户接的行为挤掉)。
        """
        def pause_hotkey(tree):
            event = tree.new_node(NN.GKNodeOnReady, "准备")
            node = tree.new_node(NN.GKNodeUiPauseGame, "暂停",
                                 gk_menu="暂停菜单", gk_hotkey=True)
            gk_stub.connect(tree, event, "执行", node, "执行")

        self.assertEqual(self._esc_quit_of(pause_hotkey), "false")

        def key_esc(tree):
            # 『输入_按键』是表达式节点(没有执行口),图上放一个就够了 ——
            # 这里问的是"图里有没有人用 Esc",不看它怎么接
            tree.new_node(NN.GKNodeKey, "Esc 键", gk_key="ESC",
                          gk_action="quit_game", gk_trigger="JUST")

        self.assertEqual(self._esc_quit_of(key_esc), "false")

    def test_esc_quit_kept_when_nobody_uses_esc(self):
        """没人用 Esc -> 内置退出保留(用户要的"默认就有")。"""
        def other_key(tree):
            tree.new_node(NN.GKNodeKey, "E 键", gk_key="E",
                          gk_action="use", gk_trigger="JUST")

        self.assertEqual(self._esc_quit_of(other_key), "true")

        def pause_without_hotkey(tree):
            event = tree.new_node(NN.GKNodeOnReady, "准备")
            node = tree.new_node(NN.GKNodeUiPauseGame, "暂停",
                                 gk_menu="暂停菜单", gk_hotkey=False)
            gk_stub.connect(tree, event, "执行", node, "执行")

        self.assertEqual(self._esc_quit_of(pause_without_hotkey), "true")


class TestAnimRound39(Base):
    """第三十九轮:动画三处修复(用户:"无动画速率 / 同时播放 / 卡片无作用")。"""

    def _anim_code(self, anim):
        """把一张动画树挂进 bpy.data,生成玩法图的代码。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        ref = tree.new_node(NN.GKNodeAnimStateMachineRef, "状态机")
        gk_stub.connect(tree, event, "执行", ref, "执行")
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[anim]))
        try:
            return gen(tree, V3)
        finally:
            detach_anim_tree(original)

    def _anim(self, speeds=None):
        """一张两状态(idle -> run)的动画树;speeds 里给状态填速度。

        链路照 UE5:状态 --转到--> 转场『上游』;转场『转到』--> 下一个
        状态的『进入』;条件接在转场的『条件』上。
        """
        anim = gk_stub.StubNodeTree("角色动画")
        anim.bl_idname = "GKAnimationTree"
        loco = anim.new_node(NN.GKNodeAnimLocomotion, "移动状态")
        idle = anim.new_node(NN.GKNodeAnimState, "状态idle")
        idle.gk_state_name = "idle"
        run = anim.new_node(NN.GKNodeAnimState, "状态run")
        run.gk_state_name = "run"
        trans = anim.new_node(NN.GKNodeAnimTransition, "转场")
        gk_stub.connect(anim, idle, "转到", trans, "上游")
        gk_stub.connect(anim, trans, "转到", run, "进入")
        gk_stub.connect(anim, loco, "地面且移动", trans, "条件")
        for state in (idle, run):
            if speeds and state.gk_state_name in speeds:
                state.gk_speed = speeds[state.gk_state_name]
        return anim

    def test_state_speed_goes_into_play(self):
        """状态的『播放速度』要真的进 play() —— 用户说"游戏动画里无动画速率"。

        以前只有『动画_播放』节点能填速度,动画树里的**状态**没有,
        所以走 / 跑 / 受击无法各用各的速率。
        """
        code, warnings = self._anim_code(self._anim({"run": 1.5}))
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _gk_anim_speed(state_name: String) -> float:", code)
        self.assertIn('"run":', code)
        self.assertIn("return 1.5", code)
        self.assertIn("_ap0.play(next_state, -1.0, _gk_anim_speed(next_state))",
                      code)
        self.assert_indent_sane(code)

    def test_state_speed_default_keeps_old_code(self):
        """全 1.0(老图)-> 速度函数一个字符都不生成。"""
        code, warnings = self._anim_code(self._anim())
        self.assertEqual(warnings, [], warnings)
        self.assertNotIn("_gk_anim_speed", code)
        self.assertIn("_ap0.play(next_state)", code)

    def test_simultaneous_actions_declared_and_played(self):
        """『同时播 2 / 3』:属性必须真的声明过,而且真的按层播。

        ── 这是用户说的"同时播放 + 一部分节点卡片无作用"的正主 ──────────
        那两个属性以前**只在 draw_buttons 里被引用,从来没声明过**:
        卡片画到"同时播"那一行时 `self.gk_anim_action_2` 直接
        AttributeError(整块卡片画一半就报错);就算不报错,对着没注册的
        属性 `row.prop(...)` 也画不出选择器 —— 功能等于不存在。
        """
        node_cls = NN.GKNodeAnimState
        declared = {spec[0] for spec in (node_cls.gk_prop_defs or ())}
        self.assertIn("gk_anim_action_2", declared,
                      "『同时播 2』没在 gk_prop_defs 里声明(卡片会报错)")
        self.assertIn("gk_anim_action_3", declared,
                      "『同时播 3』没在 gk_prop_defs 里声明")

        # 填了两段 -> 生成层播放器,而且跟着本状态的速度走
        anim = gk_stub.StubNodeTree("角色动画")
        anim.bl_idname = "GKAnimationTree"
        idle = anim.new_node(NN.GKNodeAnimState, "状态idle")
        idle.gk_state_name = "idle"
        idle.gk_anim_action_2 = types.SimpleNamespace(name="嘴形动作")
        idle.gk_speed = 1.5
        code, warnings = self._anim_code(anim)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_layer_player(_ap0, 1)", code)
        self.assertIn('"嘴形动作", -1.0, _gk_anim_speed("idle")', code)
        self.assert_indent_sane(code)

    def test_blend_shape_node_is_no_longer_dead(self):
        """『动画_设混合参数』以前是死节点(codegen 里根本没有它)。

        用户说的"一部分节点卡片无作用"就是它:拖进图、连上线,导出后
        一个字符都没有,表情参数永远不动。
        """
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        node = tree.new_node(NN.GKNodeAnimParam, "嘴形", gk_value=0.5,
                             gk_shape_index=2)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("set_blend_shape_value(2, 0.5)", code)
        self.assertIn('"MeshInstance3D"', code)
        # 找不到网格时报一次(静默不动是最难查的那种"没反应")
        self.assertIn("_gk_shape_warned", code)
        self.assert_indent_sane(code)

    def test_locomotion_thresholds_read_from_card(self):
        """『动画判断_移动状态』卡片上的两个阈值必须真的进生成结果。

        以前只在卡片上画了两个数字框,而 codegen 是按**引脚名**读值的 ——
        节点上没有这两个引脚,于是改了卡片一点反应都没有(永远 0.1 / 3.0)。
        """
        tree = make_tree()
        physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        loco = tree.new_node(NN.GKNodeAnimLocomotion, "移动状态",
                             gk_speed_threshold=0.5, gk_fast_threshold=6.0)
        branch = tree.new_node(NN.GKNodeBranch, "如果")
        gk_stub.connect(tree, physics, "执行", branch, "执行")
        gk_stub.connect(tree, loco, "在移动", branch, "条件")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        for pin in ("速度阈值", "跑的速度阈值"):
            self.assertIsNotNone(loco.inputs.get(pin), f"少了引脚 {pin}")
        self.assertIn("@export var move_threshold: float = 0.5", code)
        self.assertIn("@export var run_threshold: float = 6.0", code)


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


class TestRound32VariableGroups(Base):
    """第三十二轮:变量**分组 / 文件夹**(侧栏『变量』框按分组折叠显示)。

    ── 排布规矩(都从"文件夹"的直觉来)──────────────────────────────
      * 有组名的按**第一次出现的顺序**排 —— 不重排(用户摆的顺序就是
        展示顺序,按字母重排会让人找不到刚建的组);
      * 组名留空的归到**最后**的『未分组』(内部用空串表示);
      * 组里没变量就不出现(空组不用手动清理);
      * **只影响显示**:分组不进生成代码(成员变量名一个字不变)。
    """

    @staticmethod
    def _tree(*pairs):
        """(名字, 分组) 列表 -> 假的 logic tree(只要有 gk_vars 就行)。"""
        return types.SimpleNamespace(gk_vars=[
            types.SimpleNamespace(var_name=name, group=group)
            for name, group in pairs])

    def test_named_groups_keep_first_seen_order(self):
        from BlenderGameKit import properties as props
        tree = self._tree(("血量", "战斗"), ("金币", "进度"), ("攻击", "战斗"))
        groups = props.logic_var_groups(tree)
        self.assertEqual([name for name, _ in groups], ["战斗", "进度"])
        self.assertEqual([v.var_name for v in groups[0][1]], ["血量", "攻击"])
        self.assertEqual([v.var_name for v in groups[1][1]], ["金币"])

    def test_ungrouped_goes_last_and_lookup_matches(self):
        from BlenderGameKit import properties as props
        tree = self._tree(("血量", "战斗"), ("临时", ""), ("分数", "战斗"))
        groups = props.logic_var_groups(tree)
        self.assertEqual([name for name, _ in groups], ["战斗", ""])
        self.assertEqual([v.var_name for v in groups[-1][1]], ["临时"])
        self.assertEqual(props.logic_var_group_of(tree),
                         {"血量": "战斗", "临时": "", "分数": "战斗"})

    def test_empty_groups_disappear(self):
        from BlenderGameKit import properties as props
        self.assertEqual(len(props.logic_var_groups(self._tree(("血量", "战斗")))),
                         1)
        self.assertEqual(props.logic_var_groups(self._tree()), [])

    def test_group_does_not_change_generated_code(self):
        """分组只影响面板:换组前后生成的脚本**逐字一样**。"""
        tree = make_tree()
        physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        setter = tree.new_node(NN.GKNodeSetVar, "设置", gk_var_name="血量")
        gk_stub.connect(tree, physics, "执行", setter, "执行")
        decl = types.SimpleNamespace(var_name="血量", var_type="float",
                                     default_value="0", group="")
        tree.gk_vars = [decl]
        before, _ = gen(tree, V3)
        decl.group = "战斗"
        after, _ = gen(tree, V3)
        self.assertEqual(before, after)


class TestRound33ReferenceGroups(Base):
    """第三十三轮(整理现有示例):引用区说得对 —— 谁写谁读、端口不算未声明。

    ── 为什么这两处在做示例时被抓出来 ────────────────────────────────
      * 『变量_增减』以前被算成"读",于是小游戏示例的『血量』显示成
        "设 0 · 读 4" —— 四个『变量_增减』在扣血,面板却说没人写过它;
      * 示例里大量用『变量_设置(视角偏航)』这种**端口同名**的写法
        (端口 = 脚本变量 `输出_视角偏航`),面板却标"未声明" ——
        用户会去"补声明",而声明出来的其实是**第二个**变量。
    """

    def test_add_var_counts_as_writer(self):
        from BlenderGameKit import properties as props
        tree = make_tree()
        tree.new_node(NN.GKNodeAddVar, "扣血", gk_var_name="血量", gk_amount=-1.0)
        tree.new_node(NN.GKNodeGetVar, "读血量", gk_var_name="血量")
        refs = {name: (len(sets), len(gets))
                for name, sets, gets in props.variable_references(tree)}
        self.assertEqual(refs["血量"], (1, 1), refs)

    def test_save_get_counts_as_writer(self):
        """『存档_读取到变量』把值写进 `gk_var` —— 它是写方(属性名不一样)。"""
        from BlenderGameKit import properties as props
        tree = make_tree()
        tree.new_node(NN.GKNodeSaveGet, "取最高分", gk_key="best_score",
                      gk_var="best_score", gk_default=0.0)
        refs = {name: (len(sets), len(gets))
                for name, sets, gets in props.variable_references(tree)}
        self.assertEqual(refs["best_score"], (1, 0), refs)

    def test_ports_go_to_their_own_group(self):
        """端口同名的引用归到『对外端口』组 —— 排在用户分组之后、未分组之前。"""
        from BlenderGameKit import properties as props
        tree = make_tree()
        tree.new_node(NN.GKNodeSetVar, "写偏航", gk_var_name="视角偏航")
        tree.new_node(NN.GKNodeSetVar, "写血量", gk_var_name="血量")
        tree.new_node(NN.GKNodeGetVar, "读临时", gk_var_name="临时")
        port = tree.gk_ports.add()
        port.port_name = "视角偏航"
        port.port_type = "float"
        var = tree.gk_vars.add()
        var.var_name = "血量"
        var.group = "战斗"
        groups = props.reference_groups(tree)
        self.assertEqual([name for name, _ in groups],
                         ["战斗", props.PORT_REF_GROUP, ""], groups)
        self.assertEqual([row[0] for row in groups[1][1]], ["视角偏航"])

    def test_declared_var_keeps_its_own_group_even_if_port_same_name(self):
        """同名又声明了变量:用户填的分组优先(不当成端口那组)。"""
        from BlenderGameKit import properties as props
        tree = make_tree()
        tree.new_node(NN.GKNodeSetVar, "写偏航", gk_var_name="视角偏航")
        port = tree.gk_ports.add()
        port.port_name = "视角偏航"
        var = tree.gk_vars.add()
        var.var_name = "视角偏航"
        var.group = "视角"
        groups = props.reference_groups(tree)
        self.assertEqual([name for name, _ in groups], ["视角"], groups)

    def test_named_groups_follow_declaration_order(self):
        """有组名的按**声明区那套顺序**排(用户摆的顺序),不按引用出现顺序。"""
        from BlenderGameKit import properties as props
        tree = make_tree()
        tree.new_node(NN.GKNodeSetVar, "写血量", gk_var_name="血量")
        tree.new_node(NN.GKNodeSetVar, "写金币", gk_var_name="金币数")
        for name, group in (("金币数", "进度"), ("血量", "战斗")):
            var = tree.gk_vars.add()
            var.var_name = name
            var.group = group
        groups = props.reference_groups(tree)
        # 引用按名字排序时『血量』在前,但展示要跟声明区一致:进度 -> 战斗
        self.assertEqual([name for name, _ in groups], ["进度", "战斗"], groups)

    def test_reference_groups_is_empty_without_usages(self):
        from BlenderGameKit import properties as props
        self.assertEqual(props.reference_groups(make_tree()), [])


class TestRound34Navigation(Base):
    """第三十四轮:寻路 —— 『移动_寻路』让敌人绕开墙追人。

    ── 三层降级(用户视角:少开一个开关不该让游戏坏掉)────────────────
      1. 场景没勾『寻路』-> 直线走(和『移动_朝向目标』一样)+ 一条告警;
      2. 代理没准备好 / 目标不可达 -> 这一帧直线走(脚本里有兜底分支);
      3. 2D -> 直线走(2D 的 NavigationRegion2D 还没做)。
    前三条都必须**照常移动**,只是方向不算绕路 —— 站着不动是最糟的降级。
    """

    @staticmethod
    def _tree(*, goal=""):
        tree = make_tree()
        physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        chaser = tree.new_node(NN.GKNodeNavigate, "追玩家", gk_goal=goal,
                               gk_speed=3.0, gk_stop_distance=1.0)
        gk_stub.connect(tree, physics, "执行", chaser, "执行")
        return tree, chaser

    def test_follows_navigation_path(self):
        """勾了『寻路』:方向来自服务器的**路径第 1 个点**,不是直线。"""
        tree, _ = self._tree(goal="玩家")
        code, _ = gen(tree, dict(V3, nav_enabled=True))
        self.assertIn("NavigationServer3D.map_get_path(", code)
        self.assertIn(".size() > 1:", code)
        self.assertIn(".move_and_slide()", code)
        # 兜底分支:路径只有起点(网格没烤完 / 目标不可达)时改走直线
        self.assertIn("length() < 0.05:", code)
        # 路点高度要压平(网格贴在碰撞体上表面,不压平会上下拱)
        self.assertIn(".y = 0.0", code)

    def test_each_node_queries_its_own_path(self):
        """两个『移动_寻路』节点各查各的路径(不共享任何隐藏状态)。"""
        tree = make_tree()
        physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        first = tree.new_node(NN.GKNodeNavigate, "追A", gk_goal="玩家")
        second = tree.new_node(NN.GKNodeNavigate, "追B", gk_goal="玩家")
        gk_stub.connect(tree, physics, "执行", first, "执行")
        gk_stub.connect(tree, first, "完成", second, "执行")
        code, _ = gen(tree, dict(V3, nav_enabled=True))
        self.assertEqual(code.count("NavigationServer3D.map_get_path("), 2)

    def test_no_scene_switch_falls_back_to_straight(self):
        """没勾『寻路』:照样走(直线),并在导出报告里说清楚怎么开。"""
        tree, _ = self._tree(goal="玩家")
        code, warnings = gen(tree, V3)
        self.assertNotIn("map_get_path", code)
        self.assertIn(".move_and_slide()", code)
        self.assertTrue(any("没勾『寻路』" in text for text in warnings), warnings)

    def test_2d_uses_navigation_server2d(self):
        """2D(第三十六轮):真查 NavigationServer2D 的绕行路径,不再降级直线。"""
        tree, _ = self._tree(goal="玩家")
        code, warnings = gen(tree, dict(V2, nav_enabled=True))
        self.assertIn("NavigationServer2D.map_get_path(", code)
        self.assertIn(".get_world_2d().navigation_map", code)
        self.assertIn(".move_and_slide()", code)
        self.assertFalse(any("只支持 3D" in text for text in warnings), warnings)

    def test_arrival_output_is_distance_based(self):
        """『到达』= 离目标进了『停在多远』(和走的是不是直线无关)。"""
        tree, chaser = self._tree(goal="玩家")
        branch = tree.new_node(NN.GKNodeBranch, "到了吗")
        gk_stub.connect(tree, chaser, "到达", branch, "条件")
        gk_stub.connect(tree, tree.find("物理帧"), "执行", branch, "执行")
        code, _ = gen(tree, dict(V3, nav_enabled=True))
        self.assertIn("length() <= 1.0", code)

    def test_navmesh_subresource_is_static_collider_only(self):
        """导航网格只解析**静态碰撞体**(角色/装饰网格不进网格 —— 实测过)。"""
        from BlenderGameKit import asset_export as export
        text = export.NAVMESH_SUBRESOURCE.format(radius=0.5, height=1.8,
                                                 slope=45.0)
        self.assertIn("geometry_parsed_geometry_type = 1", text)
        self.assertIn("agent_radius = 0.5", text)
        self.assertIn("agent_max_slope = 45.0", text)

    def test_scene_switch_reads_and_normalizes(self):
        """场景设置读出来要夹在合理范围(半径太小 / 坡度 90 度都不行)。"""
        from BlenderGameKit import asset_export as export
        settings = types.SimpleNamespace(
            nav_enabled=True, dimension="3",
            nav_agent_radius=0.0, nav_agent_height=-1.0, nav_max_slope=120.0)
        scene = types.SimpleNamespace(gk_settings=settings)
        self.assertTrue(export._nav_on(scene))
        values = export._nav_settings(scene)
        self.assertGreaterEqual(values["radius"], 0.05)
        self.assertGreaterEqual(values["height"], 0.1)
        self.assertLessEqual(values["slope"], 89.0)
        # 2D(第三十六轮):开着就是开着 —— 2D 现在也烤导航多边形
        settings.dimension = "2"
        self.assertTrue(export._nav_on(scene))


class TestRound36NavV2(Base):
    """第三十六轮:寻路第二版 —— 2D 真寻路 / 避障 / 重烤导航。

    避障为什么用 NavigationAgent:那是引擎的 RVO,手写不出来 ——
    每帧 set_velocity(期望速度),velocity_computed 回调发回安全速度。
    实测(真 Godot):3 个敌人同追一点,各自侧移、最小间距 0.8 米、全部到达。
    """

    @staticmethod
    def _tree(*, goal="", avoid=False):
        tree = make_tree()
        physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        chaser = tree.new_node(NN.GKNodeNavigate, "追玩家", gk_goal=goal,
                               gk_speed=3.0, gk_stop_distance=1.0,
                               gk_avoid=avoid, gk_avoid_radius=0.6)
        gk_stub.connect(tree, physics, "执行", chaser, "执行")
        return tree, chaser

    def test_avoid_injects_agent_helpers(self):
        """勾了『避障』:注入 agent 帮助函数(开避障 / 对齐地图 / 回调移动)。"""
        tree, _ = self._tree(goal="玩家", avoid=True)
        code, _ = gen(tree, dict(V3, nav_enabled=True))
        self.assertIn("_gk_nav_move(", code)
        self.assertIn("func _gk_nav_agent(target: CharacterBody3D", code)
        # 避障默认是关的 —— 不显式打开时 set_velocity 什么都不做(实测)
        self.assertIn("agent.avoidance_enabled = true", code)
        self.assertIn("set_navigation_map(map)", code)
        # ⚠ 不设 target_position 时避障不工作(实测:安全速度恒 0)
        self.assertIn("agent.target_position = goal", code)
        self.assertIn("func _gk_nav_move(target: CharacterBody3D, "
                      "desired: Vector3, goal: Vector3",
                      code)
        # 移动只在回调里做:整份代码里 move_and_slide 恰好一次
        self.assertEqual(code.count("move_and_slide()"), 1)

    def test_no_avoid_keeps_old_code(self):
        """没勾『避障』:老代码一字不变,不注入任何 agent 东西。"""
        tree, _ = self._tree(goal="玩家")
        code, _ = gen(tree, dict(V3, nav_enabled=True))
        self.assertNotIn("_gk_nav_move", code)
        self.assertNotIn("NavigationAgent3D", code)

    def test_avoid_2d_uses_agent2d(self):
        """2D 避障走 NavigationAgent2D(同一套 RVO)。"""
        tree, _ = self._tree(goal="玩家", avoid=True)
        code, _ = gen(tree, dict(V2, nav_enabled=True))
        self.assertIn("_gk_nav_move2d(", code)
        self.assertIn("NavigationAgent2D", code)
        self.assertIn("get_world_2d()", code)

    def test_rebake_node_emits_forwarder(self):
        """『移动_重烤导航』:语句 + 转发到环境脚本的帮助函数。"""
        tree, _ = self._tree(goal="玩家")
        physics = tree.find("物理帧")
        rebake = tree.new_node(NN.GKNodeRebakeNavigation, "重烤")
        gk_stub.connect(tree, physics, "执行", rebake, "执行")
        code, _ = gen(tree, dict(V3, nav_enabled=True))
        self.assertIn("_gk_rebake_navigation()", code)
        self.assertIn('env.has_method("rebake_navigation")', code)

    def test_rebake_helper_only_when_used(self):
        """没用到『移动_重烤导航』的图不注入转发函数。"""
        tree, _ = self._tree(goal="玩家")
        code, _ = gen(tree, dict(V3, nav_enabled=True))
        self.assertNotIn("rebake_navigation", code)

    def test_env_script_has_2d_bake_and_rebake(self):
        """环境脚本:3D / 2D 两条烘焙路径 + 公开的 rebake_navigation。

        两条实测结论锁在这里:
          * 2D 烘焙必须先 add_outline 一个"外框"(没有外框 = 0 个多边形);
          * 烤完要再赋一份 duplicate("第一个地图吞第一次赋值")。
        """
        from BlenderGameKit import asset_export as export
        script = export.render_env_script()
        self.assertIn("func rebake_navigation() -> void:", script)
        self.assertIn("func _gk_bake_navigation_3d(", script)
        self.assertIn("func _gk_bake_navigation_2d(", script)
        self.assertIn("add_outline(PackedVector2Array(", script)
        self.assertIn(
            "NavigationPolygon.PARSED_GEOMETRY_STATIC_COLLIDERS", script)
        self.assertIn("region.navigation_mesh = baked.duplicate()", script)
        self.assertIn("region.navigation_polygon = poly.duplicate()", script)
        # ⚠ 重烤要**等两个物理帧**:门/平台(AnimatableBody3D)的位移
        # 下一物理步才进物理世界,同帧重烤烤出来的还是老网格(实测)
        self.assertIn("await get_tree().physics_frame", script)
        self.assertIn("_gk_bake_navigation()", script)  # _ready 里的启动烘焙

    def test_2d_navpoly_subresource_and_node(self):
        """2D 导出:NavigationPolygon 子资源 + NavigationRegion2D 节点。"""
        from BlenderGameKit import asset_export as export
        text = export.NAVPOLY_SUBRESOURCE.format(radius=8.0)
        self.assertIn('type="NavigationPolygon"', text)
        self.assertIn("agent_radius = 8.0", text)
        self.assertIn("parsed_geometry_type = 1", text)
        node = export.NAVIGATION_NODE_2D.format(name="Navigation")
        self.assertIn('type="NavigationRegion2D"', node)
        self.assertIn('navigation_polygon = SubResource("gk_navpoly")', node)


class TestRound41Ai(Base):
    """第四十一轮:AI 图(『游戏AI』)+ 『AI_启用』那条线。

    用户选的方案(四选一后确认):
      * **状态机式**(照动画树):『AI_状态』+ 『AI_转场』+ 起始状态
        = 没有任何转场指到的那个;
      * 玩法图放一个『AI_启用』引用整张 AI 图(同『动画_状态机』
        『界面_套用设计』);
      * 侧栏清单 + 视口叠加预览(巡逻路线 / 感知圈)。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def _ai_tree(self, name="敌人AI"):
        ai = gk_stub.StubNodeTree(name)
        ai.bl_idname = "GKGameAiTree"
        return ai

    def _gen_with_ai(self, ai, tree, options=None):
        """把 AI 图挂到 bpy.data 上再生成(用完还原,照界面那套)。"""
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[ai]))
        try:
            return gen(tree, options or V3)
        finally:
            if original is None:
                try:
                    delattr(bpy_module(), "data")
                except AttributeError:
                    pass
            else:
                setattr(bpy_module(), "data", original)

    def _patrol_chase(self, ai):
        """在 AI 图里搭"巡逻 -> 看见玩家 -> 追击 -> 丢失玩家 -> 巡逻"。

        环用『从』『到』下拉表达(Blender 不许连线成环,见 _new_ai_tree)。
        """
        patrol = ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                             gk_behavior="PATROL",
                             gk_patrol_a="点A", gk_patrol_b="点B", gk_speed=3.0,
                             gk_stop_distance=0.6)
        chase = ai.new_node(NN.GKNodeAiState, "追击", gk_state_name="追击",
                            gk_behavior="CHASE", gk_goal="玩家", gk_speed=4.0,
                            gk_stop_distance=1.2)
        to_chase = ai.new_node(NN.GKNodeAiTransition, "看见玩家",
                               gk_cond_source="SEE", gk_goal="玩家",
                               gk_sight=10.0,
                               gk_from_state="巡逻", gk_to_state="追击")
        back = ai.new_node(NN.GKNodeAiTransition, "丢失玩家",
                           gk_cond_source="LOST", gk_goal="玩家",
                           gk_sight=10.0,
                           gk_from_state="追击", gk_to_state="巡逻")
        return patrol, chase, to_chase, back

    def _enable_tree(self, **props):
        tree = make_tree()
        event = self._ready(tree)
        enable = tree.new_node(NN.GKNodeAiEnable, "启用AI", **props)
        gk_stub.connect(tree, event, "执行", enable, "执行")
        return tree, enable

    # ---- 树类型 / 菜单归属 ----
    def test_ai_nodes_tree_scope(self):
        from BlenderGameKit import properties as props
        # 状态 / 转场:只属于 AI 图
        for cls in (NN.GKNodeAiState, NN.GKNodeAiTransition):
            self.assertTrue(props.node_fits_tree(cls, "GKGameAiTree"),
                            cls.bl_label + " 应该能放进 AI 图")
            self.assertFalse(props.node_fits_tree(cls, "GKGameLogicTree"),
                             cls.bl_label + " 不该出现在玩法图菜单里")
        # 『AI_启用』:玩法图的引用节点(整张 AI 图的入口)
        self.assertTrue(props.node_fits_tree(NN.GKNodeAiEnable, "GKGameLogicTree"))
        self.assertFalse(props.node_fits_tree(NN.GKNodeAiEnable, "GKGameAiTree"))
        # 『AI_看得见』:两棵树都能放
        self.assertTrue(props.node_fits_tree(NN.GKNodeAiSee, "GKGameAiTree"))
        self.assertTrue(props.node_fits_tree(NN.GKNodeAiSee, "GKGameLogicTree"))
        # 拼条件要用的纯取值节点在 AI 树里也开放
        for cls in (NN.GKNodeConstFloat, NN.GKNodeCompare, NN.GKNodeDistance,
                    NN.GKNodeGetVar):
            self.assertTrue(props.node_fits_tree(cls, "GKGameAiTree"),
                            cls.bl_label + " 应该能放进 AI 图(拼条件)")
        # 动画树不受影响:它那边的『常量_数值』还在(第三轮踩过的坑别再犯)
        self.assertTrue(props.node_fits_tree(NN.GKNodeConstFloat,
                                             "GKAnimationTree"))

    def test_ai_menu_lists_states_only(self):
        from BlenderGameKit import properties as props
        labels = [cls.bl_label
                  for _cat, items in props.node_menu_entries("GKGameAiTree")
                  for cls in items]
        self.assertIn("AI_状态", labels)
        self.assertIn("AI_转场", labels)
        self.assertIn("AI_看得见", labels)
        self.assertNotIn("移动_按方向", labels)

    # ---- 状态图解析 ----
    def test_graph_parses_loop_with_fallbacks(self):
        lib = NN
        ai = self._ai_tree()
        self._patrol_chase(ai)
        graph = lib.ai_tree_graph(ai)
        self.assertEqual(len(graph["states"]), 2)
        self.assertEqual(len(graph["edges"]), 2)
        self.assertEqual(lib.ai_state_name(graph["entry"]), "巡逻",
                         "没人指向的状态才是起始状态")
        self.assertEqual(graph["problems"], [], graph["problems"])

    def test_graph_links_also_count(self):
        """连线优先:转场用连线接状态,不用下拉也能解析。"""
        lib = NN
        ai = self._ai_tree()
        patrol = ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                             gk_behavior="STAND")
        chase = ai.new_node(NN.GKNodeAiState, "追击", gk_state_name="追击",
                            gk_behavior="CHASE", gk_goal="玩家")
        trans = ai.new_node(NN.GKNodeAiTransition, "追",
                            gk_cond_source="SEE", gk_goal="玩家")
        gk_stub.connect(ai, patrol, "转到", trans, "上游")
        gk_stub.connect(ai, trans, "转到", chase, "进入")
        graph = lib.ai_tree_graph(ai)
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(lib.ai_state_name(graph["edges"][0]["from"]), "巡逻")
        self.assertEqual(lib.ai_state_name(graph["edges"][0]["to"]), "追击")
        self.assertEqual(graph["problems"], [], graph["problems"])

    def test_graph_reports_missing_params(self):
        lib = NN
        ai = self._ai_tree()
        ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                    gk_behavior="PATROL")          # 没填巡逻点 A/B
        ai.new_node(NN.GKNodeAiState, "追", gk_state_name="追",
                    gk_behavior="CHASE")           # 没填目标
        graph = lib.ai_tree_graph(ai)
        joined = "\n".join(graph["problems"])
        self.assertIn("巡逻点 A", joined)
        self.assertIn("巡逻点 B", joined)
        self.assertIn("目标", joined)

    def test_graph_reports_direct_link(self):
        """AI 状态直连没有条件 —— 报问题,让用户改用『AI_转场』。"""
        lib = NN
        ai = self._ai_tree()
        patrol = ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                             gk_behavior="STAND")
        chase = ai.new_node(NN.GKNodeAiState, "追击", gk_state_name="追击",
                            gk_behavior="STAND")
        gk_stub.connect(ai, patrol, "转到", chase, "进入")
        graph = lib.ai_tree_graph(ai)
        self.assertEqual(graph["edges"], [])
        self.assertTrue(any("直连" in p for p in graph["problems"]),
                        graph["problems"])

    # ---- 『AI_启用』生成 ----
    def test_enable_generates_state_machine(self):
        ai = self._ai_tree()
        self._patrol_chase(ai)
        tree, _ = self._enable_tree()
        code, warnings = self._gen_with_ai(ai, tree)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("var ai_state: String", code)
        # 起始状态:只在"还没进过状态"时设一次(接在每帧链上也安全)
        self.assertIn("if ai_state.is_empty():", code)
        self.assertIn('ai_state = "巡逻"', code)
        # 每帧先执行当前状态的行为(巡逻 / 追击各一段)
        self.assertIn('if ai_state == "巡逻":', code)
        self.assertIn('elif ai_state == "追击":', code)
        self.assertIn("move_and_slide()", code)
        # 行为写在 _physics_process 里(和移动同拍)
        self.assertIn("func _physics_process(", code)
        # 转场:看见 / 丢失目标 -> 距离比较
        self.assertIn(".length() <= 10.0", code)
        self.assertIn(".length() > 10.0", code)

    def test_patrol_flips_between_points(self):
        ai = self._ai_tree()
        self._patrol_chase(ai)
        tree, _ = self._enable_tree()
        code, warnings = self._gen_with_ai(ai, tree)
        self.assertEqual(warnings, [], warnings)
        # 到了哪头就往另一头走(翻面开关存在成员变量里,要跨帧记)
        self.assertIn("var _gk_ai_patrol_1: bool = false", code)
        self.assertIn("_gk_ai_patrol_1 = not _gk_ai_patrol_1", code)

    def test_flee_runs_away_from_goal(self):
        ai = self._ai_tree()
        ai.new_node(NN.GKNodeAiState, "逃", gk_state_name="逃",
                    gk_behavior="FLEE", gk_goal="玩家", gk_speed=4.0,
                    gk_stop_distance=3.0)
        tree, _ = self._enable_tree()
        code, warnings = self._gen_with_ai(ai, tree)
        self.assertEqual(warnings, [], warnings)
        # 逃跑方向 = 自己 - 目标(和追击相反)
        self.assertIn(" - _gk_global_position_3d(节点_玩家)", code)

    def test_see_node_pin_condition(self):
        """『AI_看得见』接在转场条件引脚上:帮函数 + 表达式都要生成。"""
        ai = self._ai_tree()
        patrol = ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                             gk_behavior="STAND")
        chase = ai.new_node(NN.GKNodeAiState, "追击", gk_state_name="追击",
                            gk_behavior="CHASE", gk_goal="玩家")
        see = ai.new_node(NN.GKNodeAiSee, "看得见吗", gk_goal="玩家",
                          gk_distance=8.0, gk_angle=90.0)
        trans = ai.new_node(NN.GKNodeAiTransition, "看见",
                            gk_from_state="巡逻", gk_to_state="追击")
        gk_stub.connect(ai, see, "看得见", trans, "条件")
        tree, _ = self._enable_tree()
        code, warnings = self._gen_with_ai(ai, tree)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_ai_can_see_3d(self, 节点_玩家, 8.0, 90.0)", code)
        self.assertIn("func _gk_ai_can_see_3d(", code)
        # 3D 用物体前方(-Z)和水平方向比夹角
        self.assertIn("signed_angle_to(flat, Vector3.UP)", code)

    def test_see_node_2d_uses_rotation(self):
        tree = make_tree()
        event = self._ready(tree)
        printer = tree.new_node(NN.GKNodePrint, "说")
        see = tree.new_node(NN.GKNodeAiSee, "看得见吗", gk_goal="玩家",
                            gk_distance=200.0, gk_angle=120.0)
        branch = tree.new_node(NN.GKNodeBranch, "看见?")
        gk_stub.connect(tree, event, "执行", branch, "执行")
        gk_stub.connect(tree, see, "看得见", branch, "条件")
        gk_stub.connect(tree, branch, "是", printer, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("func _gk_ai_can_see_2d(", code)
        self.assertIn("Vector2.RIGHT.rotated(", code)

    def test_enable_missing_tree_warns(self):
        tree, _ = self._enable_tree()
        _code, warnings = gen(tree, V3)
        self.assertTrue(any("AI 图" in w for w in warnings), warnings)

    def test_enable_missing_goal_warns(self):
        ai = self._ai_tree()
        ai.new_node(NN.GKNodeAiState, "追", gk_state_name="追",
                    gk_behavior="CHASE")             # 没选目标
        tree, _ = self._enable_tree()
        _code, warnings = self._gen_with_ai(ai, tree)
        self.assertTrue(any("目标" in w for w in warnings), warnings)

    def test_state_var_conflict_warns(self):
        ai = self._ai_tree()
        self._patrol_chase(ai)
        tree = make_tree()
        event = self._ready(tree)
        one = tree.new_node(NN.GKNodeAiEnable, "AI一")
        two = tree.new_node(NN.GKNodeAiEnable, "AI二")
        gk_stub.connect(tree, event, "执行", one, "执行")
        gk_stub.connect(tree, one, "完成", two, "执行")
        _code, warnings = self._gen_with_ai(ai, tree)
        self.assertTrue(any("互相覆盖" in w for w in warnings), warnings)

    def test_required_base_class_from_ai_tree(self):
        """AI 图里的行为要 CharacterBody —— validation 必须**先扫出来**定基类。"""
        ai = self._ai_tree()
        ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                    gk_behavior="STAND")
        need, why = codegen.required_base_class(ai, {"dimension": "3"})
        self.assertEqual(need, "CharacterBody3D")
        self.assertIn("AI", why + "AI")           # 只说"用到了它才有的成员"即可
        need2, _ = codegen.required_base_class(ai, {"dimension": "2"})
        self.assertEqual(need2, "CharacterBody2D")

    def test_target_typed_as_character_body(self):
        """『作用在谁』的 @onready 必须带 CharacterBody 类型。

        不带的话生成 `Node` 类型,`.velocity` / `.move_and_slide()`
        会被 Godot 静态检查拒绝 —— 整个脚本加载不了(和『移动_朝向目标』
        同一个坑)。
        """
        ai = self._ai_tree()
        self._patrol_chase(ai)
        tree, _ = self._enable_tree(gk_target="敌人")
        code, warnings = self._gen_with_ai(ai, tree)
        self.assertEqual(warnings, [], warnings)
        self.assertIn(": CharacterBody3D", code)
        # 行为作用在那个物体上(不是 self)
        self.assertIn("敌人.velocity", code)
        self.assertIn("敌人.move_and_slide()", code)


class TestRound42Display(Base):
    """第四十二轮:『游戏显示』设置图(物理线框 / 调试面板 / 画面风格 /
    视口预览)。

    用户原话:"添加游戏显示设置编辑器,整合如物理显示等的排列,不然不好找";
    动工前在四个形态里点了**新节点树类型**。这张图不是逻辑图,而是一张
    **设置控制台**:卡上的开关直接读写**当前场景**的 GKSceneSettings
    (写透)——
      * 所以四个节点一个引脚都没有,也不参与代码生成;
      * 所以摘要 / 缺卡这些逻辑必须是纯函数,离线就能验(就是这些用例)。
    """

    def test_card_list_matches_classes(self):
        """DISPLAY_CARD_IDS 和真正登记的四个类对得上(防改名 / 漏登记)。"""
        from BlenderGameKit import properties as props
        self.assertEqual(len(NN.DISPLAY_CARD_IDS), 5)
        for blob in NN.DISPLAY_CARD_IDS:
            cls = getattr(NN, blob, None)
            self.assertIsNotNone(cls, f"DISPLAY_CARD_IDS 里的 {blob} 没定义")
            self.assertEqual(cls.bl_idname, blob)
            # 设置卡不是执行流的一环:一个引脚都没有
            self.assertEqual(list(cls.gk_inputs), [], blob)
            self.assertEqual(list(cls.gk_outputs), [], blob)
            self.assertTrue(props.node_fits_tree(cls, "GKDisplayTree"))

    def test_display_cards_tree_scope(self):
        from BlenderGameKit import properties as props
        # 四张卡只属于『游戏显示』图
        for blob in NN.DISPLAY_CARD_IDS:
            cls = getattr(NN, blob)
            for other in ("GKGameLogicTree", "GKAnimationTree",
                          "GKUiDesignTree", "GKGameAiTree"):
                self.assertFalse(props.node_fits_tree(cls, other),
                                 f"{cls.bl_label} 不该出现在 {other} 里")
        # 玩法图的动作节点进不了显示图(菜单和 poll 两层都不给)
        self.assertFalse(props.node_fits_tree(NN.GKNodeMoveDirection,
                                              "GKDisplayTree"))
        # 动画树 / AI 树的取值节点也不该混进来
        self.assertFalse(props.node_fits_tree(NN.GKNodeConstFloat,
                                              "GKDisplayTree"))

    def test_display_menu_lists_only_cards(self):
        from BlenderGameKit import properties as props
        labels = [cls.bl_label
                  for _cat, items in props.node_menu_entries("GKDisplayTree")
                  for cls in items]
        for wanted in ("显示_碰撞线框", "显示_调试面板", "显示_画面风格",
                       "显示_视口预览", "显示_环境氛围"):
            self.assertIn(wanted, labels)
        self.assertNotIn("移动_按方向", labels)
        # 反过来:玩法图的菜单里不该冒出设置卡
        logic_labels = [cls.bl_label
                        for _cat, items
                        in props.node_menu_entries("GKGameLogicTree")
                        for cls in items]
        self.assertNotIn("显示_碰撞线框", logic_labels)

    def test_missing_display_cards(self):
        """缺卡统计:侧栏『补齐』按它找缺哪张(纯读 nodes 的 bl_idname)。"""
        tree = gk_stub.StubNodeTree("游戏显示")
        self.assertEqual(NN.missing_display_cards(tree),
                         list(NN.DISPLAY_CARD_IDS))
        for blob in NN.DISPLAY_CARD_IDS[:2]:
            tree.new_node(getattr(NN, blob))
        self.assertEqual(NN.missing_display_cards(tree),
                         list(NN.DISPLAY_CARD_IDS[2:]))
        # 补满之后没有缺的
        for blob in NN.DISPLAY_CARD_IDS[2:]:
            tree.new_node(getattr(NN, blob))
        self.assertEqual(NN.missing_display_cards(tree), [])

    def test_display_settings_rows(self):
        """侧栏摘要是**纯读值**的:喂假设置对象就能算,不能抛异常。"""
        import types as _types
        settings = _types.SimpleNamespace(
            debug_panel=True, debug_key="F1", debug_copy_key="F2",
            debug_runtime=False, toon_materials=True, toon_follow_light=False)
        rows = NN.display_settings_rows(settings)
        self.assertEqual([row[0] for row in rows],
                         ["调试面板", "运行日志", "画面风格", "环境氛围"])
        self.assertTrue(rows[0][2])
        self.assertIn("F1", rows[0][1])
        self.assertIn("F2", rows[0][1])
        self.assertFalse(rows[1][2])
        self.assertIn("全平涂", rows[2][1])
        # 老设置对象没有 env_* 属性 -> 环境氛围按"关"兜底
        self.assertFalse(rows[3][2])
        self.assertEqual(rows[3][1], "关")

        # 关掉卡通 = 原样导出 PBR;打开跟随光照 = 明暗跟着太阳
        settings.toon_materials = False
        rows = NN.display_settings_rows(settings)
        self.assertIn("PBR", rows[2][1])
        self.assertFalse(rows[2][2])
        settings.toon_materials = True
        settings.toon_follow_light = True
        rows = NN.display_settings_rows(settings)
        self.assertIn("明暗跟随太阳", rows[2][1])

        # 缺属性的裸对象(老 .blend / 桩环境)也要有兜底,不能抛异常
        rows = NN.display_settings_rows(object())
        self.assertEqual(len(rows), 4)
        self.assertFalse(rows[0][2])
        # 设置对象本身拿不到(None)= 空摘要,面板自己去报错
        self.assertEqual(NN.display_settings_rows(None), [])


class TestRound50UiChoice(Base):
    """第五十轮:阶段③『对话_选择』分支(剧情分支 / 二选一)。

    设计文档的第三阶段:"『对话_选择』节点 —— 最多 N 个选项(选项文字 +
    每个选项一个执行出口 + 『选了第几个』输出)"。
    判据:
      * 出口数量跟着『选项数』走(1~6),名字是 `选项1..选项N`;
      * **没有『完成』出口**(有的话会"没选就往下跑" —— 和『界面_按钮』同一个坑);
      * 生成物是 `match _gk_choice_picked:` + 每个出口一个 case;
      * 『选了第几个』是个整数数据出口,解析成 `_gk_choice_picked`;
      * 选项文字按 `|` 分隔,填不满的自动补『选项 N』。
    """

    def _tree_with_choice(self, **props):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        node = tree.new_node(NN.GKNodeUiChoice, "选一个", **props)
        gk_stub.connect(tree, event, "执行", node, "执行")
        return tree, node

    def test_sockets_follow_option_count(self):
        _tree, node = self._tree_with_choice(gk_option_count=3,
                                             gk_options="救她|自己跑|再想想")
        names = [socket.name for socket in node.outputs]
        self.assertEqual(names, ["选项1", "选项2", "选项3", "选了第几个"])
        # 数据出口是整数,不是执行流
        data = node.outputs.get("选了第几个")
        self.assertNotEqual(data.bl_idname, "NodeSocketVirtual")
        node.gk_option_count = 5
        node.build_sockets()
        self.assertEqual([s.name for s in node.outputs],
                         ["选项1", "选项2", "选项3", "选项4", "选项5",
                          "选了第几个"])
        # 上限 6 / 下限 1(防止用户把它调成 0 条出口)
        node.gk_option_count = 99
        node.build_sockets()
        self.assertEqual(len([s for s in node.outputs
                              if s.name.startswith("选项")]), 6)

    def test_no_done_socket(self):
        """**不能有『完成』出口** —— 有的话选都没选后面的逻辑就跑了。"""
        _tree, node = self._tree_with_choice()
        self.assertIsNone(node.outputs.get("完成"))

    def test_option_texts_pad_and_split(self):
        _tree, node = self._tree_with_choice(gk_option_count=4,
                                             gk_options="救她|自己跑")
        self.assertEqual(node.option_texts(),
                         ["救她", "自己跑", "选项 3", "选项 4"])
        node.gk_options = "  甲 | 乙 | 丙 | 丁 | 多出来的  "
        self.assertEqual(node.option_texts(), ["甲", "乙", "丙", "丁"])

    def test_generates_match_with_one_case_per_option(self):
        tree, node = self._tree_with_choice(gk_option_count=3,
                                            gk_options="救她|自己跑|再想想",
                                            gk_question="怎么办？")
        first = tree.new_node(NN.GKNodePrint, "选了一", gk_text="选了一")
        second = tree.new_node(NN.GKNodePrint, "选了二", gk_text="选了二")
        gk_stub.connect(tree, node, "选项1", first, "执行")
        gk_stub.connect(tree, node, "选项2", second, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_choice_show(self, str(\"怎么办？\"), "
                      "[\"救她\", \"自己跑\", \"再想想\"], 30.0, 640.0, 200.0, 48.0)",
                      code)
        self.assertIn("while _gk_choice_picked < 0:", code)
        self.assertIn("await get_tree().process_frame", code)
        self.assertIn("_gk_choice_hide()", code)
        self.assertIn("match _gk_choice_picked:", code)
        for case in ("        1:", "        2:", "        3:"):
            self.assertIn(case, code)
        # 没连线的『选项3』必须补 pass(match 的空分支是语法错)
        self.assertIn("        3:\n            pass", code)
        # 两条链各自落在自己的 case 里
        self.assertIn('print("选了一")', code)
        self.assertIn('print("选了二")', code)
        self.assert_indent_sane(code)

    def test_hides_panel_before_branch(self):
        """先收起面板、再走分支 —— 不然选完面板还挂在屏幕上。"""
        tree, node = self._tree_with_choice()
        printer = tree.new_node(NN.GKNodePrint, "之后", gk_text="之后")
        gk_stub.connect(tree, node, "选项1", printer, "执行")
        code, _ = gen(tree, V2)
        lines = code.splitlines()
        hide_at = next(i for i, line in enumerate(lines)
                       if line.strip() == "_gk_choice_hide()")
        match_at = next(i for i, line in enumerate(lines)
                        if line.strip() == "match _gk_choice_picked:")
        print_at = next(i for i, line in enumerate(lines)
                        if 'print("之后")' in line)
        self.assertLess(hide_at, match_at)
        self.assertLess(match_at, print_at)

    def test_number_keys_and_buttons(self):
        tree, node = self._tree_with_choice()
        code, _ = gen(tree, V2)
        # 鼠标:每个选项一个按钮,按下回调带序号
        self.assertIn('button.name = "选择%d" % (index + 1)', code)
        self.assertIn("button.pressed.connect(_gk_choice_pick.bind(index + 1))", code)
        # 键盘:数字键 1~6,走 _input(和对话框同一套采集方式)
        self.assertIn("func gk_choice_input_event(event: InputEvent) -> void:", code)
        self.assertIn("gk_choice_input_event(event)", code)
        self.assertIn("KEY_1:", code)
        self.assertIn("KEY_6:", code)
        # 面板自己不吃鼠标(点击要能穿到按钮上)
        self.assertIn("_gk_choice_panel.mouse_filter = Control.MOUSE_FILTER_IGNORE", code)

    def test_picked_index_is_readable_expression(self):
        """『选了第几个』接到『变量_设置』上 → 生成物里是 `_gk_choice_picked`。"""
        tree, node = self._tree_with_choice(gk_option_count=2)
        store = tree.new_node(NN.GKNodeSetVar, "记下选择", gk_var_name="选的")
        gk_stub.connect(tree, node, "选项2", store, "执行")
        source = tree.new_node(NN.GKNodeGetVar, "读变量", gk_var_name="选的")
        self.assertIsNotNone(source)
        # 直接把数据出口接到『变量_设置』的值上
        gk_stub.connect(tree, node, "选了第几个", store, "值")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_choice_picked", code)
        self.assertIn("var 选的: int", code)

    def test_panel_helper_only_when_used(self):
        """没用『对话_选择』时,一个选择辅助函数都不许出现(老产物一字不变)。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="你好")
        gk_stub.connect(tree, event, "执行", printer, "执行")
        code, _ = gen(tree, V2)
        self.assertNotIn("_gk_choice_show", code)
        self.assertNotIn("_gk_choice_panel", code)
        self.assertNotIn("gk_choice_input_event", code)

    def test_choice_only_in_game_logic_tree(self):
        from BlenderGameKit import properties as props
        cls = NN.GKNodeUiChoice
        self.assertTrue(props.node_fits_tree(cls, "GKGameLogicTree"))
        self.assertFalse(props.node_fits_tree(cls, "GKUiDesignTree"))
        self.assertFalse(props.node_fits_tree(cls, "GKAnimationTree"))
        # 不是"控件构建"节点:不该被『界面_套用设计』建出来
        self.assertFalse(getattr(cls, "gk_ui_builder", False))


class TestRound49Environment(Base):
    """第四十九轮:阶段②环境与后处理(WorldEnvironment + Environment)。

    设计文档的第二阶段:导出 WorldEnvironment —— 背景 / 环境光 / 雾 /
    泛光 / 色调。判据(和『游戏显示』那轮一样):
      * **默认关**:关着时 environment_scene_blocks 什么都不给
        (老工程导出产物一字不变);
      * 读设置是**纯函数**(喂假设置对象就能验,不碰 bpy);
      * 枚举数值和 properties 里的枚举**顺序一致**(防两处各写一套飘掉)。
    """

    @staticmethod
    def _scene(**overrides):
        values = dict(
            env_enabled=True, env_background="COLOR",
            env_bg_color=(0.05, 0.06, 0.10, 1.0),
            env_ambient_color=(0.5, 0.6, 0.8, 1.0), env_ambient_energy=0.5,
            env_fog=False, env_fog_color=(0.3, 0.34, 0.42, 1.0),
            env_fog_density=0.02,
            env_glow=False, env_glow_intensity=0.8, env_glow_bloom=0.1,
            env_tonemap="LINEAR", env_adjust=False,
            env_brightness=1.0, env_contrast=1.0, env_saturation=1.0)
        values.update(overrides)
        return types.SimpleNamespace(gk_settings=types.SimpleNamespace(**values))

    def test_off_by_default(self):
        from BlenderGameKit import asset_export as ae
        off = self._scene(env_enabled=False)
        self.assertIsNone(ae.env_settings(off))
        self.assertEqual(ae.environment_scene_blocks(off), ([], ""))
        # 没有 gk_settings / scene 是 None 也不能炸
        self.assertIsNone(ae.env_settings(types.SimpleNamespace()))
        self.assertIsNone(ae.env_settings(None))
        self.assertEqual(ae.environment_scene_blocks(None), ([], ""))

    def test_2d_project_does_not_export(self):
        """2D 项目:开着也不导(WorldEnvironment 只对 3D 有用,免得"开了没反应")。"""
        from BlenderGameKit import asset_export as ae
        scene = self._scene(dimension="2")
        self.assertIsNone(ae.env_settings(scene))
        self.assertEqual(ae.environment_scene_blocks(scene), ([], ""))

    def test_environment_fields(self):
        from BlenderGameKit import asset_export as ae
        scene = self._scene(
            env_background="COLOR", env_ambient_energy=0.42,
            env_fog=True, env_fog_density=0.07,
            env_glow=True, env_glow_intensity=1.5, env_glow_bloom=0.3,
            env_tonemap="ACES", env_adjust=True,
            env_brightness=1.1, env_contrast=1.2, env_saturation=0.8)
        blocks, node = ae.environment_scene_blocks(scene)
        self.assertEqual(len(blocks), 1)
        text = blocks[0]
        self.assertIn('[sub_resource type="Environment"', text)
        self.assertIn("background_mode = 1", text)          # 纯色
        # 第九十七轮:纯色背景必须写 **2(Color)** —— 写 3(Sky) 时没有天空,
        # Godot 会当"没有环境光"(真机标定过:和 Disabled 不可区分)
        self.assertIn("ambient_light_source = 2", text)
        self.assertIn("ambient_light_energy = 0.420000", text)
        self.assertIn("fog_enabled = true", text)
        self.assertIn("fog_density = 0.070000", text)
        self.assertIn("glow_enabled = true", text)
        self.assertIn("glow_intensity = 1.500000", text)
        self.assertIn("tonemap_mode = 3", text)             # ACES
        self.assertIn("adjustment_enabled = true", text)
        self.assertIn("adjustment_saturation = 0.800000", text)
        self.assertNotIn("sky =", text)
        self.assertIn('type="WorldEnvironment"', node)
        self.assertIn('SubResource("gk_environment")', node)

    def test_fog_and_glow_off_writes_false(self):
        from BlenderGameKit import asset_export as ae
        blocks, _node = ae.environment_scene_blocks(self._scene())
        text = blocks[0]
        self.assertIn("fog_enabled = false", text)
        self.assertIn("glow_enabled = false", text)
        self.assertIn("adjustment_enabled = false", text)

    def test_sky_background_adds_resources(self):
        from BlenderGameKit import asset_export as ae
        scene = self._scene(env_background="SKY",
                            env_bg_color=(0.2, 0.3, 0.6, 1.0))
        blocks, _node = ae.environment_scene_blocks(scene)
        joined = "\n".join(blocks)
        self.assertEqual(len(blocks), 3)
        self.assertIn('[sub_resource type="ProceduralSkyMaterial"', joined)
        self.assertIn('[sub_resource type="Sky"', joined)
        self.assertIn('sky = SubResource("gk_sky")', joined)
        self.assertIn("background_mode = 2", joined)

    def test_enum_values_match_properties(self):
        from BlenderGameKit import asset_export as ae
        from BlenderGameKit import properties as props
        bg_order = [item[0] for item in props.ENV_BACKGROUNDS]
        self.assertEqual(bg_order, list(ae.ENV_BACKGROUND_MODES))
        tone_order = [item[0] for item in props.ENV_TONEMAPS]
        self.assertEqual(tone_order, list(ae.ENV_TONEMAP_MODES))
        # 数值必须从 0 连续排(和 Godot 枚举对齐)
        self.assertEqual(list(ae.ENV_TONEMAP_MODES.values()),
                         list(range(len(tone_order))))

    def test_scene_settings_has_env_props(self):
        """GKSceneSettings 上真的挂了这些属性(卡上才读得到)。"""
        from BlenderGameKit import properties as props
        annotations = props.GKSceneSettings.__annotations__ or {}
        for name in ("env_enabled", "env_background", "env_bg_color",
                     "env_ambient_color", "env_ambient_energy", "env_fog",
                     "env_fog_color", "env_fog_density", "env_glow",
                     "env_glow_intensity", "env_glow_bloom", "env_tonemap",
                     "env_adjust", "env_brightness", "env_contrast",
                     "env_saturation"):
            self.assertIn(name, annotations, name)

    def test_env_card_is_a_display_card(self):
        from BlenderGameKit import properties as props
        cls = NN.GKNodeDisplayEnvironment
        self.assertIn("GKNodeDisplayEnvironment", NN.DISPLAY_CARD_IDS)
        self.assertEqual(list(cls.gk_inputs), [])
        self.assertEqual(list(cls.gk_outputs), [])
        self.assertTrue(props.node_fits_tree(cls, "GKDisplayTree"))
        self.assertFalse(props.node_fits_tree(cls, "GKGameLogicTree"))


class TestRound43Assets(Base):
    """第四十三轮:『游戏资产』资产库(3D 道具 / 图片 / 曲线 / 音频)。

    用户原话:"添加游戏资产管理编辑器,便于重复利用与和常规资产隔开";
    动工前在三个形态里点了**新节点树类型**,并选了:收四类、
    **跨场景共用**、资产实例自动收进『资产』集合。

    这一轮的判据(和『游戏显示』那轮一样):
      * 四张资产卡只属于资产库(一个引脚都没有,不参与代码生成);
      * "谁在用" / "收录" 是**纯函数**(喂桩树就能验);
      * 音频管线两端算同一个路径(sound_assets),导出时真的复制。
    """

    def _trees_with_users(self):
        logic = gk_stub.StubNodeTree("玩法")
        logic.bl_idname = "GKGameLogicTree"
        logic.new_node(NN.GKNodeUiImage, "头像", gk_image="头像图")
        logic.new_node(NN.GKNodeUiImage, "背景", gk_image="头像图")
        logic.new_node(NN.GKNodeCameraSetCurrent, "运镜", gk_path_curve="轨道")
        logic.new_node(NN.GKNodePlaySound, "音效", gk_path="res://audio/x.wav")
        anim = gk_stub.StubNodeTree("动画")
        anim.bl_idname = "GKAnimationTree"
        anim.new_node(NN.GKNodeUiText, "文字")
        return [logic, anim]

    # ---- 树归属 / 菜单 ----
    def test_card_list_matches_classes(self):
        from BlenderGameKit import properties as props
        # 第六十六轮:四张分开的卡合成一张『资产卡』(ASSET_CARD_IDS 只剩一个)
        self.assertEqual(len(NN.ASSET_CARD_IDS), 1)
        self.assertIn("GKNodeAssetCard", NN.ASSET_CARD_IDS)
        # 旧四张卡保留兼容(定义还在、还认资产树),但不再进菜单
        self.assertEqual(len(NN.LEGACY_ASSET_CARD_IDS), 4)
        for blob in tuple(NN.ASSET_CARD_IDS) + tuple(NN.LEGACY_ASSET_CARD_IDS):
            cls = getattr(NN, blob, None)
            self.assertIsNotNone(cls, f"资产卡名单里的 {blob} 没定义")
            self.assertEqual(cls.bl_idname, blob)
            self.assertEqual(list(cls.gk_inputs), [], blob)
            self.assertEqual(list(cls.gk_outputs), [], blob)
            self.assertTrue(props.node_fits_tree(cls, "GKAssetTree"))
        for blob in NN.ASSET_CARD_IDS:
            self.assertFalse(getattr(getattr(NN, blob), "gk_hidden", False))
        for blob in NN.LEGACY_ASSET_CARD_IDS:
            self.assertTrue(getattr(getattr(NN, blob), "gk_hidden", False),
                            f"旧卡 {blob} 应该从菜单里藏起来")

    def test_asset_cards_tree_scope(self):
        from BlenderGameKit import properties as props
        for blob in tuple(NN.ASSET_CARD_IDS) + tuple(NN.LEGACY_ASSET_CARD_IDS):
            cls = getattr(NN, blob)
            for other in ("GKGameLogicTree", "GKAnimationTree",
                          "GKUiDesignTree", "GKGameAiTree", "GKDisplayTree"):
                self.assertFalse(props.node_fits_tree(cls, other),
                                 f"{cls.bl_label} 不该出现在 {other} 里")
        # 玩法节点 / 设置卡进不了资产库
        self.assertFalse(props.node_fits_tree(NN.GKNodeMoveDirection,
                                              "GKAssetTree"))
        self.assertFalse(props.node_fits_tree(NN.GKNodeDisplayCollision,
                                              "GKAssetTree"))

    def test_menu_lists_only_asset_cards(self):
        from BlenderGameKit import properties as props
        labels = [cls.bl_label
                  for _cat, items in props.node_menu_entries("GKAssetTree")
                  for cls in items]
        self.assertIn("资产卡", labels)
        # 旧四张卡从菜单里藏起来了(老 .blend 仍能打开)
        for gone in ("资产_模型卡", "资产_图片卡", "资产_曲线卡", "资产_声音卡"):
            self.assertNotIn(gone, labels)
        self.assertNotIn("移动_按方向", labels)

    def test_missing_asset_cards(self):
        tree = gk_stub.StubNodeTree("游戏资产")
        self.assertEqual(NN.missing_asset_cards(tree),
                         list(NN.ASSET_CARD_IDS))
        tree.new_node(NN.GKNodeAssetCard)
        self.assertEqual(NN.missing_asset_cards(tree), [])

    # ---- "谁在用" / "收录"(纯函数)----
    def test_asset_users_finds_references(self):
        trees = self._trees_with_users()
        image_users = NN.asset_users(trees, "image", "头像图")
        self.assertEqual(len(image_users), 2)
        self.assertTrue(all(entry[0] == "玩法" for entry in image_users))
        curve_users = NN.asset_users(trees, "curve", "轨道")
        self.assertEqual([entry[1] for entry in curve_users], ["运镜"])
        sound_users = NN.asset_users(trees, "sound", "res://audio/x.wav")
        self.assertEqual([entry[1] for entry in sound_users], ["音效"])
        # 没被引用的资产 = 空清单;空值不误伤
        self.assertEqual(NN.asset_users(trees, "image", "没人用的图"), [])
        self.assertEqual(NN.asset_users(trees, "image", ""), [])

    def test_collect_used_assets(self):
        used = NN.collect_used_assets(self._trees_with_users())
        self.assertEqual(used["images"], ["头像图"])
        self.assertEqual(used["curves"], ["轨道"])
        self.assertEqual(used["sounds"], ["res://audio/x.wav"])

    # ---- 音频管线:名字 / 复制 ----
    def test_sound_naming_is_stable_and_ascii(self):
        from BlenderGameKit import sound_assets
        first = sound_assets.sound_file_name("D:/音频/跳跃.wav")
        second = sound_assets.sound_file_name("D:/音频/跳跃.wav")
        self.assertEqual(first, second)                    # 稳定
        self.assertTrue(first.endswith(".wav"))
        self.assertTrue(first[:-4].isascii())              # 压成 ASCII
        # 同名的两个文件(不同目录)不能撞成同一个目标名
        other = sound_assets.sound_file_name("E:/备份/跳跃.wav")
        self.assertNotEqual(first, other)
        self.assertTrue(sound_assets.sound_res_path("D:/音频/跳跃.wav")
                        .startswith("res://audio/"))
        self.assertTrue(sound_assets.is_sound_file("a.OGG"))
        self.assertFalse(sound_assets.is_sound_file("a.txt"))

    def test_sound_files_filter_and_dedupe(self):
        from BlenderGameKit import sound_assets
        cards = [("跳跃", "D:/s/jump.wav"),
                 ("没有文件", ""),
                 ("不是音频", "D:/s/note.txt"),
                 ("重复的同一个文件", "D:/s/jump.wav")]
        planned = sound_assets.sound_files("X:/proj", cards)
        self.assertEqual(len(planned), 1)
        self.assertEqual(planned[0][0], "跳跃")

    def test_copy_sounds_reports_missing(self):
        from BlenderGameKit import sound_assets
        folder = tempfile.mkdtemp(prefix="gk_sound_")
        try:
            source = os.path.join(folder, "跳跃.wav")
            with open(source, "wb") as handle:
                handle.write(b"RIFF....WAVEfmt ")
            written, missing = sound_assets.copy_sounds(folder, [
                ("跳跃", source),
                ("丢了", os.path.join(folder, "没有这个.wav")),
            ])
            self.assertEqual(len(written), 1)
            self.assertTrue(written[0].startswith("audio/"))
            self.assertTrue(os.path.isfile(
                os.path.join(folder, written[0].replace("/", os.sep))))
            self.assertEqual([name for name, _path in missing], ["丢了"])
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_export_copies_sound_assets(self):
        """导出时必须真的把声音卡的文件复制进 audio/,并写进清单。"""
        folder = tempfile.mkdtemp(prefix="gk_sound_export_")
        try:
            source = os.path.join(folder, "跳跃.wav")
            with open(source, "wb") as handle:
                handle.write(b"RIFF....WAVEfmt ")
            tree = make_tree()
            event = tree.new_node(NN.GKNodeOnReady, "准备")
            play = tree.new_node(NN.GKNodePlaySound, "音效",
                                 gk_path="res://audio/x.wav")
            gk_stub.connect(tree, event, "执行", play, "执行")
            out = os.path.join(folder, "proj")
            os.makedirs(out, exist_ok=True)
            with mock.patch.object(exporter, "_sound_asset_cards",
                                   return_value=[("跳跃", source)]):
                result = exporter.export_tree(
                    tree, {"output_dir": out, "dimension": "3",
                           "base_class": "CharacterBody3D"}, write_scene=False)
            self.assertTrue(result["ok"], result)
            files = os.listdir(os.path.join(out, "audio"))
            self.assertEqual(len(files), 1, files)
            self.assertIn(f"audio/{files[0]}", result["manifest"])
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_export_missing_sound_is_warning_not_failure(self):
        """声音卡的文件被挪走:导出照常成功,只给一条告警。"""
        folder = tempfile.mkdtemp(prefix="gk_sound_missing_")
        try:
            tree = make_tree()
            tree.new_node(NN.GKNodeOnReady, "准备")
            out = os.path.join(folder, "proj")
            os.makedirs(out, exist_ok=True)
            with mock.patch.object(
                    exporter, "_sound_asset_cards",
                    return_value=[("丢了", os.path.join(folder, "无.wav"))]):
                result = exporter.export_tree(
                    tree, {"output_dir": out, "dimension": "3",
                           "base_class": "CharacterBody3D"}, write_scene=False)
            self.assertTrue(result["ok"], result)
            codes = [item.code for item in result["diagnostics"]]
            self.assertIn("SOUND_FILE_MISSING", codes)
            self.assertFalse(os.path.isdir(os.path.join(out, "audio"))
                             and os.listdir(os.path.join(out, "audio")))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_sound_survives_old_file_cleanup(self):
        """音频刚复制进去,不能被"旧文件清理"当成上一轮的残留删掉。

        实测踩过:整次导出末尾的清理只看"事务写过的文件 + 被引用的文件" ——
        音频是直接拷的(二进制不走文本事务),两边都不占,于是刚写进去
        就被清走了(godot 里表现为"找不到音频文件")。
        修法:复制成功的音频进 `written`(`FileTransaction.mark`),
        而且 `.wav/.ogg/.mp3` 加进 `referenced_files` 认的扩展名表。
        这里两头都验:在用 -> 保住;不用了 -> 才清掉。
        """
        from BlenderGameKit import generated_files
        folder = tempfile.mkdtemp(prefix="gk_sound_cleanup_")
        try:
            source = os.path.join(folder, "叮.wav")
            with open(source, "wb") as handle:
                handle.write(b"RIFF....WAVEfmt ")
            out = os.path.join(folder, "proj")
            os.makedirs(out, exist_ok=True)
            tree = make_tree()
            event = tree.new_node(NN.GKNodeOnReady, "准备")
            play = tree.new_node(NN.GKNodePlaySound, "音效",
                                 gk_path="res://audio/x.wav")
            gk_stub.connect(tree, event, "执行", play, "执行")
            options = {"output_dir": out, "dimension": "3",
                       "base_class": "CharacterBody3D"}
            with mock.patch.object(exporter, "_sound_asset_cards",
                                   return_value=[("叮", source)]):
                first = exporter.export_tree(tree, options, write_scene=False)
            self.assertTrue(first["ok"], first)
            generated_files.cleanup_after_export(out, first["written"])
            audio_dir = os.path.join(out, generated_files.project.AUDIO_DIR)
            self.assertTrue(os.listdir(audio_dir),
                            "第一次导出后音频被清理删掉了")

            # 第二次:资产库里没有这张声音卡了 -> 它才该被清掉
            quiet = make_tree()
            quiet.new_node(NN.GKNodeOnReady, "准备")
            with mock.patch.object(exporter, "_sound_asset_cards",
                                   return_value=[]):
                second = exporter.export_tree(quiet, options,
                                              write_scene=False)
            deleted = generated_files.cleanup_after_export(
                out, second["written"])
            self.assertTrue(any(item.startswith("audio/") for item in deleted),
                            f"不再引用的音频没被清掉: {deleted}")
            self.assertFalse(os.listdir(audio_dir))
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class TestRound52Packaging(Base):
    """打包发布(第五十二轮):一键把 Godot 工程打成可执行文件。

    这是"3D 全流程"里**唯一断掉的一环**:以前导出到工程为止,出 exe 还得
    自己开 Godot 配导出预设、装模板、点导出。这一轮把它接成一条龙。

    这一层能离线测,是因为 `packaging` 刻意做成**纯标准库**(不 import bpy、
    不动本插件其它模块)—— 所以"生成预设 / 拼命令 / 探模板"这些纯函数
    直接喂真实文件就能验,不用起 Blender。
    """

    def test_preset_text_has_required_fields(self):
        from BlenderGameKit import packaging
        text = packaging.export_presets_text(
            title="我的游戏", preset_name="Windows Desktop",
            export_path="dist/我的游戏.exe")
        # Godot 靠这几个键认"这个预设导出什么、怎么导"
        self.assertIn("[preset.0]", text)
        self.assertIn("[preset.0.options]", text)
        self.assertIn('name="Windows Desktop"', text)
        self.assertIn('platform="Windows Desktop"', text)
        self.assertIn('export_path="dist/我的游戏.exe"', text)
        # 单文件分发:资源打进 exe,免得用户漏拷同名的 .pck
        self.assertIn("binary_format/embed_pck=true", text)
        self.assertIn('application/product_name="我的游戏"', text)

    def test_ensure_presets_never_overwrites(self):
        """已有 export_presets.cfg 一律原样保留(最重要的那条约束)。"""
        from BlenderGameKit import packaging
        folder = tempfile.mkdtemp(prefix="gk_presets_")
        try:
            path, created = packaging.ensure_export_presets(
                folder, title="g", export_path="dist/g.exe")
            self.assertTrue(created)
            self.assertTrue(os.path.isfile(path))
            # 用户手工改一下(模拟他在 Godot 编辑器里配过)
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write('[preset.0]\nname="我改过的"\n')
            again, created2 = packaging.ensure_export_presets(
                folder, title="g", export_path="dist/g.exe")
            self.assertFalse(created2)
            self.assertEqual(again, path)
            with open(path, "r", encoding="utf-8") as handle:
                self.assertIn("我改过的", handle.read())   # 没被覆盖
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_read_preset_name_and_export_path(self):
        """沿用用户已有预设的**名字和输出路径**,别硬塞默认值。"""
        from BlenderGameKit import packaging
        folder = tempfile.mkdtemp(prefix="gk_presets_read_")
        try:
            self.assertIsNone(packaging.read_preset_name(folder))
            self.assertIsNone(packaging.read_export_path(folder))
            with open(os.path.join(folder, packaging.EXPORT_PRESETS_NAME),
                      "w", encoding="utf-8", newline="\n") as handle:
                handle.write('[preset.0]\nname="我的预设"\n'
                             'export_path="build/Game.exe"\n')
            self.assertEqual(packaging.read_preset_name(folder), "我的预设")
            self.assertEqual(packaging.read_export_path(folder),
                             "build/Game.exe")
            # 只有名字、没写路径(空串)也算"没配路径"
            with open(os.path.join(folder, packaging.EXPORT_PRESETS_NAME),
                      "w", encoding="utf-8", newline="\n") as handle:
                handle.write('[preset.0]\nname="x"\nexport_path=""\n')
            self.assertIsNone(packaging.read_export_path(folder))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_release_command_shape(self):
        from BlenderGameKit import packaging
        args = packaging.release_command(
            r"D:\godot\godot.exe", r"D:\proj", "Windows Desktop",
            r"D:\proj\dist\g.exe")
        self.assertEqual(args[0], r"D:\godot\godot.exe")
        self.assertIn("--headless", args)
        self.assertEqual(args[args.index("--path") + 1], r"D:\proj")
        self.assertEqual(args[args.index("--export-release") + 1],
                         "Windows Desktop")
        self.assertEqual(args[-1], r"D:\proj\dist\g.exe")

    def test_output_path_and_rel(self):
        from BlenderGameKit import packaging
        out = packaging.default_output_path(r"D:\proj", "我的游戏")
        self.assertTrue(out.endswith(os.path.join("dist", "我的游戏.exe")))
        self.assertEqual(packaging.rel_output_path(r"D:\proj", out),
                         "dist/我的游戏.exe")

    def test_template_detection(self):
        """模板探测:按版本号找目录、按"含 windows 且含 release"认文件。"""
        from BlenderGameKit import packaging
        root = tempfile.mkdtemp(prefix="gk_templates_")
        try:
            version_dir = os.path.join(root, "4.7.2.stable")
            os.makedirs(version_dir, exist_ok=True)
            with open(os.path.join(version_dir, "windows_release_x86_64.exe"),
                      "wb") as handle:
                handle.write(b"MZ")
            with open(os.path.join(version_dir, "README.txt"),
                      "w", encoding="utf-8") as handle:
                handle.write("模板说明")
            # 前缀匹配:4.7 也要能找到 4.7.2.stable
            self.assertEqual(
                packaging.find_template_dir("4.7.2", root), version_dir)
            self.assertEqual(
                packaging.find_template_dir("4.7", root), version_dir)
            self.assertIsNone(packaging.find_template_dir("4.9", root))
            self.assertEqual(
                packaging.windows_release_template(version_dir),
                "windows_release_x86_64.exe")
            with mock.patch.object(packaging, "export_templates_root",
                                   return_value=root):
                ready, found_dir, note = packaging.templates_status("4.7.2")
                self.assertTrue(ready, note)
                self.assertEqual(found_dir, version_dir)
                # 版本对不上 -> 不认,并且说明里要写清"去哪儿装"
                missing, _d, miss_note = packaging.templates_status("4.9")
                self.assertFalse(missing)
                self.assertIn("导出模板", miss_note)
                self.assertIn("管理导出模板", miss_note)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_failure_hint_translates_template_error(self):
        """Godot 那句英文 No export template 要翻成"去哪儿装"。"""
        from BlenderGameKit import packaging
        hint = packaging.failure_hint(
            "ERROR: No export template found at: ...", "4.7.2")
        self.assertIn("导出模板", hint)
        self.assertIn("管理导出模板", hint)
        # ── 中文界面的 Godot 打的是中文(真机实测)────────────────────
        # 「在预期路径处未找到导出模板： ...\windows_release_x86_64.exe」
        # 只认英文的话,中文用户看到的永远是一句没翻译的 Godot 原文。
        zh_log = ("ERROR: Cannot export project with preset \"Windows Desktop\""
                  " due to configuration errors:\n"
                  "在预期路径处未找到导出模板：\n"
                  "  C:/Users/x/AppData/Roaming/Godot/export_templates/"
                  "4.7.2.stable/windows_release_x86_64.exe\n")
        self.assertTrue(packaging.template_missing_in_log(zh_log))
        self.assertTrue(packaging.template_missing_in_log(
            "No export template found"))
        self.assertFalse(packaging.template_missing_in_log("all good"))
        zh_hint = packaging.failure_hint(zh_log, "4.7.2")
        self.assertIn("管理导出模板", zh_hint)
        # 找不到预设 -> 给"删掉重生成"的兜底
        preset_hint = packaging.failure_hint(
            'ERROR: Couldn\'t find preset "Windows Desktop"', "4.7.2")
        self.assertIn("export_presets.cfg", preset_hint)
        # 其它错误 -> 至少把 Godot 的原话带回去,别吞掉
        other = packaging.failure_hint("ERROR: something else went wrong")
        self.assertIn("something else", other)

    def test_failure_hint_explains_android_keystore(self):
        """Android 缺签名密钥库:要给出**能照着做**的引导,不能只丢一句原文。

        第七十一轮清账:以前 Godot 打一句 "keystore" 就原样回面板,用户不
        知道下一步该干什么。现在给三条路 + 一条能直接复制的 keytool 命令。
        """
        from BlenderGameKit import packaging
        hint = packaging.failure_hint(
            "ERROR: No keystore configured, unable to export APK."
            " Please check the Android export settings.", "4.7.2")
        self.assertIn("keystore", hint)
        self.assertIn("keytool", hint)
        self.assertIn("编辑器设置", hint)
        self.assertIn("export_presets.cfg", hint)
        # 中文界面的 Godot:"未找到密钥库"
        zh = packaging.failure_hint("错误:未找到密钥库,无法导出 Android")
        self.assertIn("keytool", zh)
        # 别把"安装导出模板"那条抢走(模板缺失优先)
        tmpl = packaging.failure_hint("No export template found", "4.7.2")
        self.assertIn("管理导出模板", tmpl)

    def test_run_release_uses_returncode(self):
        """退出码判成败:Godot 会打一些非致命的 ERROR 字样,不能拿它当判据。"""
        from BlenderGameKit import packaging
        ok_result = types.SimpleNamespace(returncode=0, stdout="ERROR: harmless",
                                          stderr="")
        with mock.patch("subprocess.run", return_value=ok_result):
            ok, log = packaging.run_release("godot", "proj", "P", "out.exe")
        self.assertTrue(ok, log)
        bad_result = types.SimpleNamespace(returncode=1, stdout="",
                                           stderr="No export template found")
        with mock.patch("subprocess.run", return_value=bad_result):
            ok2, log2 = packaging.run_release("godot", "proj", "P", "out.exe")
        self.assertFalse(ok2)
        self.assertIn("template", log2)

    def test_run_release_creates_output_dir(self):
        """Godot **不会**替你建导出目录:目录不在就报"给定的导出路径不存在"。

        真机踩到:装了导出模板后第一次真打包就撞上这条报错,而报错文案里
        根本没提"目录",很像预设写坏了。修法是把"建目录"放进 run_release ——
        这样算子 / 门禁脚本 / 以后的新入口都不会漏。
        """
        from BlenderGameKit import packaging
        folder = tempfile.mkdtemp(prefix="gk_release_dir_")
        try:
            out = os.path.join(folder, "dist", "sub", "g.exe")
            seen = {}

            def spy(_args, **_kwargs):
                # 断言"调用 Godot 之前目录已经建好"
                seen["dir_exists"] = os.path.isdir(os.path.dirname(out))
                with open(out, "wb") as handle:
                    handle.write(b"MZ" + b"\0" * 2048)
                return types.SimpleNamespace(returncode=0, stdout="", stderr="")

            with mock.patch("subprocess.run", side_effect=spy):
                ok, _log = packaging.run_release("godot", "proj", "P", out)
            self.assertTrue(ok)
            self.assertTrue(seen.get("dir_exists"),
                            "run_release 必须在调 Godot 之前把产物目录建出来")
            self.assertTrue(os.path.isfile(out))
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class TestRound53Advisor(Base):
    """项目顾问(AI)(第五十三轮):把项目诊断交给 OpenAI 兼容接口分析。

    这一层只测**纯函数**(拼地址 / 拼请求 / 解析响应 / 脱敏 / 换行)——
    `advisor` 不 import bpy,所以这些都能离线喂数据验。真联网那一小段用
    mock 把 `urlopen` 换掉,不需要网络也不需要 Key。
    """

    def test_chat_url_variants(self):
        """用户填的地址形态五花八门,都得能补成完整地址。"""
        from BlenderGameKit import advisor
        self.assertEqual(advisor.chat_url(""),
                         "")
        self.assertEqual(advisor.chat_url("https://api.deepseek.com"),
                         "https://api.deepseek.com/v1/chat/completions")
        # 结尾带 / 或多写了 /v1 都不能补重
        self.assertEqual(advisor.chat_url("https://api.deepseek.com/"),
                         "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(advisor.chat_url("http://localhost:11434/v1"),
                         "http://localhost:11434/v1/chat/completions")
        # 已经写全的路径原样用(有的自建服务不是 /v1)
        self.assertEqual(
            advisor.chat_url("http://my.host/openai/chat/completions"),
            "http://my.host/openai/chat/completions")

    def test_compose_and_messages(self):
        from BlenderGameKit import advisor
        text = advisor.compose_text("角色不动", "【5】节点图\n...")
        self.assertIn("角色不动", text)
        self.assertIn("【5】节点图", text)
        messages = advisor.build_messages("角色不动", "快照内容")
        self.assertEqual([item["role"] for item in messages], ["system", "user"])
        # 项目快照进**系统消息**、不进用户消息:多轮对话里就永远只有一份
        # **刚取的**快照,不会越聊越多份过期快照(见 build_messages 的说明)
        self.assertIn("快照内容", messages[0]["content"])
        self.assertNotIn("快照内容", messages[1]["content"])
        self.assertEqual(messages[1]["content"], "角色不动")
        # 系统提示里必须讲清这个插件是什么,否则模型会给 VisualScript 的建议
        self.assertIn("脚本挂载目标", messages[0]["content"])
        # 而且要说"用纯文本" —— 显示环境不渲染 Markdown
        self.assertIn("纯文本", messages[0]["content"])

    def test_build_messages_history_window(self):
        """多轮对话:只带**最近**若干条,老消息不进去(又慢又贵还误导)。"""
        from BlenderGameKit import advisor
        history = []
        for index in range(20):
            history.append({"role": "user", "content": f"问{index}"})
            history.append({"role": "assistant", "content": f"答{index}"})
        messages = advisor.build_messages("新问题", "快照", history)
        # 1 条 system + MAX_HISTORY 条历史 + 1 条当前问题
        self.assertEqual(len(messages), 1 + advisor.MAX_HISTORY + 1)
        self.assertIn("问19", str(messages))
        self.assertNotIn("问13", str(messages))        # 超出窗口的老消息
        self.assertEqual(messages[-1]["content"], "新问题")

        # 形状不对的历史项要跳过而不是炸掉:role 不是 user/assistant、
        # 内容为空(load_history 恢复时会过滤一遍,build_messages 再兜一次)
        dirty = [{"role": "system", "content": "不该带 system"},
                 {"role": "user", "content": "   "},
                 {"role": "user", "content": "好问题"}]
        messages2 = advisor.build_messages("q", "", dirty)
        self.assertEqual([item["role"] for item in messages2],
                         ["system", "user", "user"])
        self.assertEqual(messages2[1]["content"], "好问题")

    def test_plainify(self):
        """模型爱用 Markdown,显示环境不渲染 —— 要抹成纯文本。"""
        from BlenderGameKit import advisor
        out = advisor.plainify("**重点**\n## 小节\n用 `CodegenAbort`\n")
        self.assertNotIn("**", out)
        self.assertNotIn("`", out)
        self.assertIn("重点", out)
        self.assertIn("■ 小节", out)
        self.assertIn("CodegenAbort", out)
        # ⚠ 单个 `**` 可能是代码里的幂运算,抹了就把代码改错了
        self.assertIn("2 ** 3", advisor.plainify("面积是 2 ** 3"))
        # `__` 故意不抹:否则会把代码里的 __init__ 误伤成 init
        self.assertIn("__init__", advisor.plainify("调用 __init__"))

    def test_turn_text(self):
        from BlenderGameKit import advisor
        text = advisor.turn_text("角色不动", "**先看这个**")
        self.assertIn("【我】", text)
        self.assertIn("角色不动", text)
        self.assertIn("【AI 顾问】", text)
        self.assertNotIn("**", text)              # 写进去之前就顺手抹了
        err = advisor.turn_text("角色不动", "", error="连不上 AI 服务")
        self.assertIn("调用失败", err)
        self.assertIn("连不上", err)

    def test_history_cap_and_last_answer(self):
        from BlenderGameKit import advisor
        advisor.clear_history()
        self.assertEqual(advisor.last_answer(), "")
        for index in range(60):
            advisor.remember("user", f"问{index}")
            advisor.remember("assistant", f"答{index}")
        self.assertLessEqual(len(advisor.HISTORY), advisor._HISTORY_CAP)
        self.assertIn("答59", advisor.last_answer())
        # 空内容不记(免得白占窗口额度)
        before = len(advisor.HISTORY)
        advisor.remember("user", "   ")
        advisor.remember("assistant", "")
        self.assertEqual(len(advisor.HISTORY), before)
        advisor.clear_history()
        self.assertEqual(advisor.HISTORY, [])

    def test_background_request_lifecycle(self):
        """异步状态机:发出去 -> 还没好 -> 好了 -> 取走。

        这一层是"界面不卡"的地基 —— 发请求的线程只做网络 IO,结果回主线程
        取(见 advisor 里那段线程铁律)。
        """
        import threading
        import time

        from BlenderGameKit import advisor
        advisor.cancel_background()
        self.assertFalse(advisor.is_busy())
        self.assertIsNone(advisor.poll_background())      # 没在跑 -> None

        gate = threading.Event()

        def slow(*_args, **_kwargs):
            gate.wait(5)
            return True, "慢回答"

        with mock.patch.object(advisor, "call_chat", side_effect=slow):
            self.assertTrue(
                advisor.request_in_background("http://x", "k", "m", []))
            # 同一时间只许有一条在飞(否则界面状态和回复会串)
            self.assertFalse(
                advisor.request_in_background("http://x", "k", "m", []))
            self.assertTrue(advisor.is_busy())
            self.assertIsNone(advisor.poll_background())   # 还没好
            gate.set()
            deadline = time.time() + 5
            result = None
            while result is None and time.time() < deadline:
                result = advisor.poll_background()
                time.sleep(0.01)
        self.assertEqual(result, (True, "慢回答"))
        self.assertFalse(advisor.is_busy())

    def test_background_timeout_gives_up(self):
        """底层卡住时不能永远等:超时后放弃等待,并给一句人话。

        直接改 `_REQUEST` 把"开始时间"往回拨,免得真等 75 秒。
        """
        import time

        from BlenderGameKit import advisor
        advisor.cancel_background()
        advisor._REQUEST.update({"active": True,
                                 "started": time.time() - 999,
                                 "timeout": 60, "result": None})
        result = advisor.poll_background()
        self.assertIsNotNone(result)
        ok, note = result
        self.assertFalse(ok)
        self.assertIn("超时", note)
        self.assertFalse(advisor.is_busy())

    def test_cancel_background(self):
        from BlenderGameKit import advisor
        advisor._REQUEST.update({"active": True, "started": 0.0,
                                 "timeout": 60, "result": None})
        self.assertTrue(advisor.is_busy())
        advisor.cancel_background()
        self.assertFalse(advisor.is_busy())
        self.assertIsNone(advisor.poll_background())

    def test_request_payload(self):
        from BlenderGameKit import advisor
        body = advisor.request_payload("deepseek-chat", [{"role": "user"}])
        self.assertEqual(body["model"], "deepseek-chat")
        self.assertFalse(body["stream"])          # 不流式:一次拿完
        self.assertIn("messages", body)

    def test_parse_response(self):
        from BlenderGameKit import advisor
        ok, text = advisor.parse_response(
            {"choices": [{"message": {"content": "  先看这个  "}}]})
        self.assertTrue(ok)
        self.assertEqual(text, "先看这个")          # 去掉首尾空白
        # 少数实现给 text 字段
        self.assertEqual(advisor.parse_response({"choices": [{"text": "hi"}]}),
                         (True, "hi"))
        # 各种坏形状都要给一句看得懂的话,而不是抛异常
        for bad in (None, [], "字符串", {}, {"choices": []},
                    {"choices": [{}]}, {"choices": [{"message": {}}]}):
            ok2, note = advisor.parse_response(bad)
            self.assertFalse(ok2, bad)
            self.assertTrue(note)
        ok3, note3 = advisor.parse_response({"error": {"message": "额度用完了"}})
        self.assertFalse(ok3)
        self.assertIn("额度用完了", note3)

    def test_call_chat_requires_config(self):
        """没填地址 / 模型名 -> 直接返回,一个字节都不发(不联网)。"""
        from BlenderGameKit import advisor
        with mock.patch("urllib.request.urlopen") as fake:
            ok, text = advisor.call_chat("", "k", "m", [])
            self.assertFalse(ok)
            self.assertIn("AI 地址", text)
            ok2, text2 = advisor.call_chat("http://x", "k", "  ", [])
            self.assertFalse(ok2)
            self.assertIn("模型名", text2)
            fake.assert_not_called()               # 关键:没发出任何请求

    def test_call_chat_success(self):
        import json

        from BlenderGameKit import advisor
        response = mock.MagicMock()
        response.read.return_value = json.dumps(
            {"choices": [{"message": {"content": "把脚本挂载目标勾上"}}]}
        ).encode("utf-8")
        response.__enter__.return_value = response
        with mock.patch("urllib.request.urlopen", return_value=response) as fake:
            ok, text = advisor.call_chat("https://api.x.com", "sk-abc", "m", [])
        self.assertTrue(ok)
        self.assertIn("脚本挂载目标", text)
        request = fake.call_args[0][0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-abc")
        self.assertEqual(request.full_url,
                         "https://api.x.com/v1/chat/completions")

    def test_call_chat_error_branches(self):
        """三种最常见的失败:Key 不对 / 连不上 / 返回的不是 JSON。"""
        import urllib.error

        from BlenderGameKit import advisor
        auth = urllib.error.HTTPError("http://x/v1/chat/completions", 401,
                                      "Unauthorized", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=auth):
            ok, text = advisor.call_chat("http://x", "bad", "m", [])
        self.assertFalse(ok)
        self.assertIn("API Key", text)              # 要能让用户知道去改 Key

        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("拒绝连接")):
            ok2, text2 = advisor.call_chat("http://x", "k", "m", [])
        self.assertFalse(ok2)
        self.assertIn("连不上", text2)
        self.assertIn("http://x/v1/chat/completions", text2)   # 把地址回显出来

        response = mock.MagicMock()
        response.read.return_value = b"<html>502 Bad Gateway</html>"
        response.__enter__.return_value = response
        with mock.patch("urllib.request.urlopen", return_value=response):
            ok3, text3 = advisor.call_chat("http://x", "k", "m", [])
        self.assertFalse(ok3)
        self.assertIn("不是 JSON", text3)

    def test_redact(self):
        """发送前必须抹掉 Key、把绝对路径换成占位符。"""
        from BlenderGameKit import advisor
        out = advisor.redact(
            "key=sk-1234567890abcdef 在 D:\\我的游戏\\角色_godot\\scripts\\a.gd",
            secrets=["sk-1234567890abcdef"],
            replacements=[("D:\\我的游戏\\角色_godot", "<Godot项目>")])
        self.assertNotIn("sk-1234567890abcdef", out)
        self.assertIn("***", out)
        self.assertIn("<Godot项目>", out)
        # 太短的"密钥"不抹 —— 否则会把正文里的普通字全换成 ***
        self.assertEqual(advisor.redact("abc", secrets=["a"]), "abc")
        self.assertEqual(advisor.redact("", secrets=["x"]), "")

    def test_truncate(self):
        from BlenderGameKit import advisor
        self.assertEqual(advisor.truncate("abc", 10), "abc")
        cut = advisor.truncate("abcdefghij", 4)
        self.assertTrue(cut.startswith("abcd"))
        self.assertIn("已截断", cut)
        self.assertEqual(advisor.truncate("abcdefghij", 0), "abcdefghij")

    def test_wrap_by_display_width(self):
        """Blender 的 label 不会自动换行,所以必须自己按**显示宽度**切。

        CJK 占两列,按字符个数切会让中英混排的行忽长忽短(中文行看起来
        是英文行的两倍长)。
        """
        from BlenderGameKit import advisor
        # 全角字符算两列
        self.assertEqual(advisor.display_width("中文"), 4)
        self.assertEqual(advisor.display_width("ab"), 2)
        self.assertEqual(advisor.display_width("中a"), 3)

        lines = advisor.wrap("这是一段中文" * 12, 20)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(advisor.display_width(line), 20, line)
        # 拼回去不能丢字(被切开的位置不能吞掉内容)
        self.assertEqual("".join(lines), "这是一段中文" * 12)

        # 英文/代码优先在空格处断,别把 some_function_name 拦腰截断
        english = advisor.wrap("hello world foo", 10)
        self.assertEqual(english[0], "hello")
        for line in english:
            self.assertLessEqual(advisor.display_width(line), 10, line)

        # 空行要保留(不然代码 / 分段会挤成一坨)
        self.assertIn("", advisor.wrap("第一段\n\n第二段", 20))


class TestRound56AdvisorTools(Base):
    """AI 亲自操作(第五十六轮):只读工具 + 提议动作 + Agent 循环状态机。

    用户原话:"我想让AI来亲自操作可以吗"。选的方案是**只读 + 提议动作**:
    AI 可以自己去查,但**改必须由用户点『执行』**。

    这一层能离线测是因为:`advisor` 里协议/状态机那部分不 import bpy,
    `advisor_tools` 里"动作登记表 + 提议校验"也是纯函数(只有真正 apply
    和读 bpy 的那几个才需要 Blender)。真执行由冒烟测。
    """

    def test_tools_schema_shape(self):
        """工具清单必须是合法的 OpenAI tools 格式,否则请求直接被 400。"""
        from BlenderGameKit import advisor_tools
        tools = advisor_tools.tools_schema()
        self.assertTrue(tools)
        names = set()
        for item in tools:
            self.assertEqual(item["type"], "function")
            function = item["function"]
            self.assertTrue(function["name"])
            self.assertTrue(function["description"])
            names.add(function["name"])
            params = function["parameters"]
            self.assertEqual(params["type"], "object")
            self.assertIn("properties", params)
        # 六个只读 + 两个提议(第九十轮起多了批量提议)
        self.assertEqual(names, {
            "read_settings", "list_trees", "list_nodes", "check_export_errors",
            "get_godot_status", "diagnose", "propose_action",
            "propose_actions"})

    def test_no_modifying_tool_exists(self):
        """**核心保证**:工具清单里除了两个**提议**工具,一个能改数据的都没有。

        这条是"AI 不能擅自改项目"的第一道证据 —— 模型能调的工具里压根
        没有"改"这个能力,它想改只能提议。
        """
        from BlenderGameKit import advisor_tools
        proposal_tools = {"propose_action", "propose_actions"}
        modifying = [item["function"]["name"]
                     for item in advisor_tools.tools_schema()
                     if item["function"]["name"] not in proposal_tools
                     and item["function"]["name"] not in advisor_tools._READERS]
        self.assertEqual(modifying, [], f"出现了非只读工具: {modifying}")
        # 而且 propose_action 的参数就是"动作名 + 参数",没有可执行代码
        propose = [item for item in advisor_tools.tools_schema()
                   if item["function"]["name"] == "propose_action"][0]
        props = propose["function"]["parameters"]["properties"]
        self.assertEqual(set(props), {"action", "target", "value", "why"})
        self.assertIn("action", propose["function"]["parameters"]["required"])
        # 批量那条也一样:只有"动作清单"这一个数组参数(同样没有可执行代码)
        batch = [item for item in advisor_tools.tools_schema()
                 if item["function"]["name"] == "propose_actions"][0]
        self.assertEqual(set(batch["function"]["parameters"]["properties"]),
                         {"actions"})

    def test_action_registry_is_declared(self):
        """动作要登记,名字/标签/所需参数都得齐全。"""
        from BlenderGameKit import advisor_tools
        self.assertIn("enable_script_target", advisor_tools.ACTIONS)
        for name, entry in advisor_tools.ACTIONS.items():
            self.assertEqual(entry["name"], name)
            self.assertTrue(entry["label"], name)
            self.assertTrue(entry["hint"], name)
            self.assertTrue(callable(entry["apply"]), name)
            self.assertIsInstance(entry["needs"], tuple)
        # 手选入口要能列出全部动作(三条一组给 Blender 的 EnumProperty)
        items = advisor_tools.action_menu_items(None)
        self.assertEqual(len(items), len(advisor_tools.ACTIONS))
        for name, label, hint in items:
            self.assertIn(name, advisor_tools.ACTIONS)
            self.assertTrue(label and hint)

    def test_build_proposal_rejects_bad_input(self):
        """提议要当场校验 —— 否则卡片是**假承诺**(看着能用,点了报错)。"""
        from BlenderGameKit import advisor_tools
        note, card = advisor_tools.build_proposal({"action": "编的动作",
                                                   "why": "x"})
        self.assertIsNone(card)
        self.assertIn("没有叫", note)
        self.assertIn("可用的动作", note)            # 要让模型知道有哪些
        # 缺必填参数 -> 也不能出卡片
        note2, card2 = advisor_tools.build_proposal(
            {"action": "set_dimension", "why": "先做 2D"})
        self.assertIsNone(card2)
        self.assertIn("还缺参数", note2)
        self.assertIn("value", note2)
        # 该给 target 的没给
        note3, card3 = advisor_tools.build_proposal(
            {"action": "enable_script_target", "why": "角色不动"})
        self.assertIsNone(card3)
        self.assertIn("target", note3)

    def test_build_proposal_ok(self):
        from BlenderGameKit import advisor_tools
        note, card = advisor_tools.build_proposal(
            {"action": "enable_script_target", "target": "Body",
             "why": "角色不动多半是这个"})
        self.assertIsNotNone(card)
        self.assertEqual(card["action"], "enable_script_target")
        self.assertEqual(card["target"], "Body")
        self.assertEqual(card["label"],
                         advisor_tools.action_label("enable_script_target"))
        self.assertIn("角色不动", card["why"])
        # 喂回模型的话必须提醒它"别说已经改好了" —— 否则它会撒谎
        self.assertIn("不要说已经改好了", note.replace("**", ""))

    def test_extract_tool_calls(self):
        """抠工具调用:arguments 是**字符串**里的 JSON,还得容忍它坏掉。"""
        from BlenderGameKit import advisor
        data = {"choices": [{"message": {"tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "list_nodes",
                          "arguments": '{"tree_name": "玩法"}'}},
            {"id": "c2", "type": "function",
             "function": {"name": "list_trees", "arguments": ""}},
        ]}}]}
        calls = advisor.extract_tool_calls(data)
        self.assertEqual([c["name"] for c in calls], ["list_nodes", "list_trees"])
        self.assertEqual(calls[0]["arguments"], {"tree_name": "玩法"})
        self.assertEqual(calls[1]["arguments"], {})
        # 少数实现直接给对象
        inline = {"choices": [{"message": {"tool_calls": [
            {"id": "c", "function": {"name": "x",
                                     "arguments": {"a": 1}}}]}}]}
        self.assertEqual(advisor.extract_tool_calls(inline)[0]["arguments"],
                         {"a": 1})
        # 坏 JSON -> arguments 是 None(调用方会当成"参数有问题"喂回模型,
        # 而不是整轮报废)
        broken = {"choices": [{"message": {"tool_calls": [
            {"id": "c", "function": {"name": "x", "arguments": "{oops"}}]}}]}
        self.assertIsNone(advisor.extract_tool_calls(broken)[0]["arguments"])
        # 没有 tool_calls / 形状不对 -> 空清单,不抛
        for data2 in ({}, {"choices": []}, {"choices": [{}]},
                      {"choices": [{"message": {}}]}, None, "字符串",
                      {"choices": [{"message": {"tool_calls": "不是列表"}}]}):
            self.assertEqual(advisor.extract_tool_calls(data2), [], data2)

    def test_assistant_tool_message_round_trip(self):
        """assistant 消息要能把工具调用原样带回去(否则下一轮请求 400)。"""
        import json

        from BlenderGameKit import advisor
        data = {"choices": [{"message": {
            "content": "我先看看节点图",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "list_nodes",
                                         "arguments": '{"detail": "full"}'}}]}}]}
        calls = advisor.extract_tool_calls(data)
        message = advisor.assistant_tool_message(data, calls)
        self.assertEqual(message["role"], "assistant")
        self.assertEqual(message["content"], "我先看看节点图")
        self.assertEqual(len(message["tool_calls"]), 1)
        sent = message["tool_calls"][0]
        self.assertEqual(sent["id"], "c1")
        self.assertEqual(sent["type"], "function")
        # arguments 必须**又变回字符串**(协议要求)
        self.assertIsInstance(sent["function"]["arguments"], str)
        self.assertEqual(json.loads(sent["function"]["arguments"]),
                         {"detail": "full"})
        # tool 结果消息的形状
        result = advisor.tool_result_message("c1", "节点清单…")
        self.assertEqual(result["role"], "tool")
        self.assertEqual(result["tool_call_id"], "c1")

    def test_agent_state_machine(self):
        """Agent 循环:**网络在线程、工具在主线程**,两边交替。

        这里把 `call_chat_raw` 换成假的(按脚本依次返回"要工具"和"最终回答"),
        再手动催 `poll_agent_step` —— 断言"主线程真的执行了工具"以及
        工具结果真的被喂回了下一轮。
        """
        import time

        from BlenderGameKit import advisor, advisor_tools
        advisor.cancel_agent()
        executed = []

        def fake_reader(context, params):
            executed.append(params)
            return "节点清单(假)"

        original = advisor_tools._READERS.get("list_nodes")
        advisor_tools._READERS["list_nodes"] = fake_reader

        rounds = [
            # 第 1 轮:模型要先看节点图
            {"choices": [{"message": {
                "content": "",
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "list_nodes",
                                             "arguments": "{}"}}]}}]},
            # 第 2 轮:看完给结论
            {"choices": [{"message": {"content": "角色没勾挂载目标,建议勾上"}}]},
        ]
        sent_rounds = []

        def fake_raw(base_url, api_key, model, messages, timeout=60, tools=None):
            sent_rounds.append([dict(m) for m in messages])
            return True, rounds[min(len(sent_rounds) - 1, len(rounds) - 1)]

        original_raw = advisor.call_chat_raw
        advisor.call_chat_raw = fake_raw
        try:
            self.assertTrue(advisor.start_agent("http://x", "k", "m",
                                                [{"role": "user",
                                                  "content": "角色不动"}]))
            state, result = None, None
            deadline = time.time() + 10
            while time.time() < deadline:
                state, result = advisor.poll_agent_step(None)
                if state == "done":
                    break
                time.sleep(0.01)
        finally:
            advisor.call_chat_raw = original_raw
            if original is not None:
                advisor_tools._READERS["list_nodes"] = original

        self.assertEqual(state, "done", f"{state} {result}")
        self.assertTrue(result[0], result)
        self.assertIn("挂载目标", result[1])
        # 关键断言:工具在**主线程**里被执行了
        self.assertEqual(len(executed), 1, executed)
        self.assertEqual(advisor.agent_steps(), ["读了节点图里的节点"])
        # 关键断言:工具结果作为 role=tool 被喂回了第二轮
        self.assertEqual(len(sent_rounds), 2)
        second = sent_rounds[1]
        tool_messages = [m for m in second if m.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 1, second)
        self.assertEqual(tool_messages[0]["tool_call_id"], "c1")
        self.assertIn("节点清单(假)", tool_messages[0]["content"])
        self.assertFalse(advisor.agent_active())

    def test_agent_proposal_needs_user_confirmation(self):
        """Agent 走到 propose_action 时:卡片出现,但**数据一个字节没改**。"""
        import time

        from BlenderGameKit import advisor
        advisor.cancel_agent()
        advisor.clear_proposals()
        rounds = [
            {"choices": [{"message": {
                "content": "",
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {
                                    "name": "propose_action",
                                    "arguments": '{"action": "enable_god_camera",'
                                                 ' "why": "方便看场景"}'}}]}}]},
            {"choices": [{"message": {"content": "建议开上帝摄像机(点确认才生效)"}}]},
        ]
        sent = []

        def fake_raw(_b, _k, _m, messages, timeout=60, tools=None):
            sent.append([dict(m) for m in messages])
            return True, rounds[min(len(sent) - 1, len(rounds) - 1)]

        original = advisor.call_chat_raw
        advisor.call_chat_raw = fake_raw
        try:
            advisor.start_agent("http://x", "k", "m",
                                [{"role": "user", "content": "看不全场景"}])
            deadline = time.time() + 10
            while time.time() < deadline:
                state, result = advisor.poll_agent_step(None)
                if state == "done":
                    break
                time.sleep(0.01)
        finally:
            advisor.call_chat_raw = original

        self.assertEqual(state, "done", f"{state} {result}")
        cards = advisor.agent_proposals()
        self.assertEqual(len(cards), 1, cards)
        self.assertEqual(cards[0]["action"], "enable_god_camera")
        self.assertEqual(cards[0]["label"], "打开上帝摄像机")
        # 喂回模型的说明必须**明确**它没有改动能力
        tool_messages = [m for m in sent[1] if m.get("role") == "tool"]
        self.assertIn("他点『执行』才会生效", tool_messages[0]["content"])
        # 用户还没点 -> 清了卡片就什么都没发生
        advisor.clear_proposals()
        self.assertEqual(advisor.agent_proposals(), [])

    def test_multiple_proposals_are_kept(self):
        """AI 一口气建议好几处改动时,**每一条都要留下**。

        踩过的设计缺陷:原来只存一张卡片,后一张**覆盖**前一张 ——
        模型说"建议 A,也建议 B",侧栏只显示 B,A **静默消失**,
        用户根本没机会看到。
        """
        import time

        from BlenderGameKit import advisor
        advisor.cancel_agent()
        advisor.clear_proposals()
        # 一轮里同时提议两件事(模型一次返回两个 tool_calls)
        rounds = [
            {"choices": [{"message": {"tool_calls": [
                {"id": "c1", "type": "function", "function": {
                    "name": "propose_action",
                    "arguments": '{"action": "enable_god_camera",'
                                 ' "why": "方便看场景"}'}},
                {"id": "c2", "type": "function", "function": {
                    "name": "propose_action",
                    "arguments": '{"action": "enable_script_target",'
                                 ' "target": "Body", "why": "角色不动"}'}},
            ]}}]},
            {"choices": [{"message": {"content": "我建议这两处"}}]},
        ]
        sent = []

        def fake_raw(_b, _k, _m, messages, timeout=60, tools=None):
            sent.append(1)
            return True, rounds[min(len(sent) - 1, len(rounds) - 1)]

        original = advisor.call_chat_raw
        advisor.call_chat_raw = fake_raw
        try:
            advisor.start_agent("http://x", "k", "m", [])
            deadline = time.time() + 10
            while time.time() < deadline:
                state, result = advisor.poll_agent_step(None)
                if state == "done":
                    break
                time.sleep(0.01)
        finally:
            advisor.call_chat_raw = original

        cards = advisor.agent_proposals()
        self.assertEqual(len(cards), 2, cards)          # 两条都在
        self.assertEqual([c["action"] for c in cards],
                         ["enable_god_camera", "enable_script_target"])
        self.assertEqual(cards[1]["target"], "Body")

        # ── 按下标点名:忽略第 2 张不能动到第 1 张 ────────────────
        self.assertEqual(advisor.proposal_at(1)["action"], "enable_script_target")
        self.assertTrue(advisor.drop_proposal(1))
        left = advisor.agent_proposals()
        self.assertEqual(len(left), 1, left)
        self.assertEqual(left[0]["action"], "enable_god_camera",
                         "忽略第二张时把第一张也弄丢了")
        # 越界/坏下标都给 None,不抛
        self.assertIsNone(advisor.proposal_at(9))
        self.assertIsNone(advisor.proposal_at("不是数字"))
        self.assertFalse(advisor.drop_proposal(9))
        advisor.clear_proposals()

    def test_tools_unsupported_degrades_gracefully(self):
        """模型不支持工具调用时:自动脱掉 tools 重试,并**记住**别每轮都试。

        真会踩:Ollama 上一个小模型收到带 tools 的请求会直接 400,
        用户看到的就是"AI 顾问突然坏了"。
        """
        from BlenderGameKit import advisor
        advisor.reset_tools_disabled()
        self.assertFalse(advisor.tools_disabled())

        # 报错文本识别(中英都要认)
        self.assertTrue(advisor.tools_unsupported_in(
            '{"error": {"message": "tools is not supported by this model"}}'))
        self.assertTrue(advisor.tools_unsupported_in("该模型不支持 tools 参数"))
        self.assertFalse(advisor.tools_unsupported_in("invalid api key"))
        self.assertFalse(advisor.tools_unsupported_in(""))
        # 不含 tool/function 字样的报错不该被误判
        self.assertFalse(advisor.tools_unsupported_in("model not found"))

        calls = []

        def fake_post(url, api_key, payload, timeout):
            calls.append(payload)
            if "tools" in payload:
                return False, ('AI 请求失败(HTTP 400)。\n'
                               '{"error":{"message":"tools is not supported"}}')
            return True, {"choices": [{"message": {"content": "脱掉工具也能答"}}]}

        original = advisor._post_json
        advisor._post_json = fake_post
        try:
            ok, data = advisor.call_chat_raw("http://x", "k", "m", [],
                                             tools=[{"type": "function"}])
        finally:
            advisor._post_json = original

        self.assertTrue(ok, data)
        self.assertEqual(len(calls), 2, "应该重试一次")
        self.assertIn("tools", calls[0])              # 第一次带工具
        self.assertNotIn("tools", calls[1])           # 第二次脱掉了
        self.assertTrue(advisor.tools_disabled(), "要记住,别每轮都白试")
        advisor.reset_tools_disabled()

    def test_tools_unsupported_not_swallowed_for_other_errors(self):
        """别的 400(比如模型名写错)不能被当成"不支持工具"悄悄重试。"""
        from BlenderGameKit import advisor
        advisor.reset_tools_disabled()
        calls = []

        def fake_post(url, api_key, payload, timeout):
            calls.append(payload)
            return False, "AI 请求失败(HTTP 400)。\nmodel not found"

        original = advisor._post_json
        advisor._post_json = fake_post
        try:
            ok, data = advisor.call_chat_raw("http://x", "k", "m", [],
                                             tools=[{"type": "function"}])
        finally:
            advisor._post_json = original
        self.assertFalse(ok)
        self.assertIn("model not found", data)         # 原样报给用户
        self.assertEqual(len(calls), 1, "不该白试第二次")
        self.assertFalse(advisor.tools_disabled())

    def test_system_prompt_lists_actions(self):
        """系统提示里必须**列出**动作清单 —— 否则模型只能编动作名。"""
        from BlenderGameKit import advisor, advisor_tools
        messages = advisor.build_messages("怎么改", "快照")
        system = messages[0]["content"]
        self.assertIn("可用的动作", system)
        for name in ("enable_script_target", "set_dimension",
                     "create_godot_project"):
            self.assertIn(name, system, name)
        # 需要参数的动作要写明要哪些参数
        self.assertIn("target", system)
        self.assertNotEqual(advisor._action_lines(), "")
        self.assertEqual(len(advisor_tools.action_names()) >= 9, True,
                         advisor_tools.action_names())

    def test_agent_bad_arguments_does_not_abort(self):
        """模型给了坏 JSON 参数:不整轮报废,而是把"参数有问题"喂回去。"""
        import time

        from BlenderGameKit import advisor
        advisor.cancel_agent()
        rounds = [
            {"choices": [{"message": {"tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "list_nodes", "arguments": "{坏了"}}]}}]},
            {"choices": [{"message": {"content": "我重新问了"}}]},
        ]
        sent = []

        def fake_raw(_b, _k, _m, messages, timeout=60, tools=None):
            sent.append([dict(m) for m in messages])
            return True, rounds[min(len(sent) - 1, len(rounds) - 1)]

        original = advisor.call_chat_raw
        advisor.call_chat_raw = fake_raw
        try:
            advisor.start_agent("http://x", "k", "m", [])
            deadline = time.time() + 10
            while time.time() < deadline:
                state, result = advisor.poll_agent_step(None)
                if state == "done":
                    break
                time.sleep(0.01)
        finally:
            advisor.call_chat_raw = original
        self.assertEqual(state, "done")
        self.assertTrue(result[0], result)
        tool_messages = [m for m in sent[1] if m.get("role") == "tool"]
        self.assertIn("不是合法的 JSON", tool_messages[0]["content"])
        self.assertIn("参数有问题", advisor.agent_steps()[0])

    def test_agent_stops_after_max_rounds(self):
        """模型一直要工具时要能停下 —— 否则是无底洞(烧钱 + 冻住状态)。"""
        import time

        from BlenderGameKit import advisor
        advisor.cancel_agent()
        sent = []

        def always_tool(_b, _k, _m, messages, timeout=60, tools=None):
            sent.append(1)
            return True, {"choices": [{"message": {"tool_calls": [
                {"id": "c", "type": "function",
                 "function": {"name": "list_trees", "arguments": "{}"}}]}}]}

        original = advisor.call_chat_raw
        advisor.call_chat_raw = fake_raw = always_tool
        try:
            advisor.start_agent("http://x", "k", "m", [])
            deadline = time.time() + 15
            while time.time() < deadline:
                state, result = advisor.poll_agent_step(None)
                if state == "done":
                    break
                time.sleep(0.005)
        finally:
            advisor.call_chat_raw = original
        self.assertEqual(state, "done")
        self.assertFalse(result[0])
        self.assertIn("先停一下", result[1])
        self.assertFalse(advisor.agent_active())
        # 轮数封顶,不是无限循环
        self.assertLessEqual(len(sent), advisor.MAX_TOOL_ROUNDS + 2)

    def test_cancel_agent_stops_loop(self):
        from BlenderGameKit import advisor
        advisor.cancel_agent()
        self.assertFalse(advisor.agent_active())
        advisor._AGENT["active"] = True
        advisor.cancel_agent()
        self.assertFalse(advisor.agent_active())
        # 没在跑的时候 poll 也要给一句人话,而不是抛
        state, result = advisor.poll_agent_step(None)
        self.assertEqual(state, "done")
        self.assertFalse(result[0])

    def test_tool_execute_reports_unknown(self):
        """模型编了一个不存在的工具名 -> 告诉它有哪些,别静默。"""
        from BlenderGameKit import advisor_tools
        text, proposal = advisor_tools.execute("编的工具", {}, None)
        self.assertIn("没有叫", text)
        self.assertIn("read_settings", text)
        self.assertIsNone(proposal)

    def test_clip_truncates(self):
        """喂回模型的输出必须摘要 —— 200 个节点全倒过去会撑爆上下文。"""
        from BlenderGameKit import advisor_tools
        short = advisor_tools._clip("短")
        self.assertEqual(short, "短")
        long_text = "x" * (advisor_tools.MAX_OUTPUT + 500)
        clipped = advisor_tools._clip(long_text)
        self.assertLess(len(clipped), len(long_text))
        self.assertIn("已截断", clipped)
        self.assertIn(str(len(long_text)), clipped)   # 告诉模型"还有多少"


class TestRound57AiHearing(Base):
    """AI 听觉(第五十七轮):『声音_发出噪音』+『AI_听见』+『去查看』。

    用户选的方向:"AI 听觉(恐怖游戏关键能力)" —— 玩家跑步/开门弄出动静,
    敌人听见了就过去查看。

    ── 为什么走"分组广播" ──────────────────────────────────────────
    出声的和听声的通常**不是同一个物体**(玩家 -> 怪物),需要一条跨物体
    通道。三条路里选了**分组广播**(`gk_ai_hearer` 组 + 挨个调
    `_gk_hear_noise`):零配置、跨场景有效、出问题一眼能查(在 Godot 里
    看那只怪在不在组里)。
    """

    def _ai_tree(self, name="敌人AI"):
        ai = gk_stub.StubNodeTree(name)
        ai.bl_idname = "GKGameAiTree"
        return ai

    def _gen_with_ai(self, ai, tree, options=None):
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[ai]))
        try:
            return gen(tree, options or V3)
        finally:
            if original is None:
                try:
                    delattr(bpy_module(), "data")
                except AttributeError:
                    pass
            else:
                setattr(bpy_module(), "data", original)

    def _patrol_investigate(self, ai):
        """巡逻 -> 听见动静 -> 去查看 -> 安静下来 -> 巡逻。

        这正是恐怖游戏里那个最经典的"追声"循环。
        """
        patrol = ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                             gk_behavior="PATROL", gk_patrol_a="点A",
                             gk_patrol_b="点B")
        look = ai.new_node(NN.GKNodeAiState, "去查看", gk_state_name="去查看",
                           gk_behavior="INVESTIGATE", gk_speed=4.0,
                           gk_stop_distance=1.0)
        heard = ai.new_node(NN.GKNodeAiTransition, "听见动静",
                            gk_cond_source="HEARD", gk_ear=0.0,
                            gk_memory=3.0,
                            gk_from_state="巡逻", gk_to_state="去查看")
        calm = ai.new_node(NN.GKNodeAiTransition, "安静下来",
                           gk_cond_source="CALM", gk_ear=0.0,
                           gk_memory=3.0,
                           gk_from_state="去查看", gk_to_state="巡逻")
        return patrol, look, heard, calm

    # ---- 树归属 / 菜单 ----
    def test_hearing_nodes_tree_scope(self):
        from BlenderGameKit import properties as props
        # 『AI_听见』:和『AI_看得见』一样,两棵树都能放
        self.assertTrue(props.node_fits_tree(NN.GKNodeAiHear, "GKGameAiTree"))
        self.assertTrue(props.node_fits_tree(NN.GKNodeAiHear, "GKGameLogicTree"))
        # 『声音_发出噪音』:玩法图的东西(出声的是玩法物体)
        self.assertTrue(props.node_fits_tree(NN.GKNodeNoise, "GKGameLogicTree"))
        self.assertFalse(props.node_fits_tree(NN.GKNodeNoise, "GKGameAiTree"))

    def test_menu_lists_hearing_nodes(self):
        from BlenderGameKit import properties as props
        labels = [cls.bl_label
                  for _cat, items in props.node_menu_entries("GKGameLogicTree")
                  for cls in items]
        self.assertIn("声音_发出噪音", labels)
        self.assertIn("AI_听见", labels)

    def test_behavior_and_condition_enums(self):
        """新行为 / 新条件来源要进枚举,而且标签是人话。"""
        # ⚠ 不要在这里 `from BlenderGameKit import nodes as NN` —— 那会把
        # 模块级的 `NN`(nodes.nodes 门面)覆盖成**包**,读不到这些常量
        # 枚举项是 (标识, 显示名, 说明) 三条一组
        behaviors = {item[0]: item[1] for item in NN.AI_BEHAVIORS}
        self.assertIn("INVESTIGATE", behaviors)
        self.assertIn("去查看", behaviors["INVESTIGATE"])
        self.assertIn("HEARD", NN.AI_COND_SOURCES)
        self.assertIn("CALM", NN.AI_COND_SOURCES)
        # 只有看见/丢失需要目标 —— 听觉不需要
        self.assertEqual(tuple(NN.AI_GOAL_COND_SOURCES), ("SEE", "LOST"))
        self.assertEqual(tuple(NN.AI_HEAR_COND_SOURCES), ("HEARD", "CALM"))
        self.assertIn("听见", NN.ai_edge_label({"kind": "HEARD"}))
        self.assertIn("安静", NN.ai_edge_label({"kind": "CALM"}))

    # ---- 生成物:噪音广播 ----
    def test_noise_node_emits_broadcast(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        noise = tree.new_node(NN.GKNodeNoise, "跑步声", gk_loudness=8.0,
                              gk_interval=0.5)
        gk_stub.connect(tree, event, "执行", noise, "执行")
        code = gen(tree, V3)[0]
        # 广播到听觉体组 + 调用固定名字的方法(跨物体通道的关键)
        self.assertIn('get_nodes_in_group("gk_ai_hearer")', code)
        self.assertIn('has_method("_gk_hear_noise")', code)
        self.assertIn("._gk_hear_noise(", code)
        # 间隔:必须按时间戳判,否则接在物理帧后面会每帧都喊
        self.assertIn("float(Time.get_ticks_msec()) * 0.001", code)
        self.assertIn(">= 0.5", code)
        # 位置被传出去(听方要知道"哪")
        self.assertIn("_gk_noise_at", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_noise_interval_uses_member_var(self):
        """间隔要跨帧记 -> 必须是**成员变量**,不能是局部 var。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        noise = tree.new_node(NN.GKNodeNoise, "跑步声")
        gk_stub.connect(tree, event, "执行", noise, "执行")
        code = gen(tree, V3)[0]
        names = [line.strip() for line in code.splitlines()]
        declared = [line for line in names if line.startswith("var _gk_noise_stamp_")]
        self.assertTrue(declared, "时间戳没有声明成成员变量")
        # 成员变量声明必须带类型(生成器里其他成员变量都这样)
        for line in declared:
            self.assertIn(": float", line)

    def test_noise_debug_print_optional(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        noise = tree.new_node(NN.GKNodeNoise, "跑步声", gk_debug_print=True)
        gk_stub.connect(tree, event, "执行", noise, "执行")
        code = gen(tree, V3)[0]
        self.assertIn("[GK 噪音]", code)

    # ---- 生成物:听觉运行时 ----
    def test_hearing_runtime_generated_for_investigate(self):
        """『去查看』行为要连带生成听觉运行时(否则它没法知道去哪)。"""
        ai = self._ai_tree()
        self._patrol_investigate(ai)
        tree, enable = self._make_enable()
        # ⚠ gen() 返回 (代码, 告警) —— 不拆开的话 assertIn 是在**元组**里找
        code, _warnings = self._gen_with_ai(ai, tree)
        # 派发器名字固定(广播方按这个名字调)
        self.assertIn("func _gk_hear_noise(", code)
        self.assertIn("func _gk_heard_", code)
        self.assertIn('add_to_group("gk_ai_hearer")', code)
        # 去查看:朝记住的噪音点走
        self.assertIn("_hear_pos", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_hearing_conditions_use_helpers(self):
        """HEARD / CALM 生成成 helper 调用,而且 CALM 是 HEARD 的反面。"""
        ai = self._ai_tree()
        self._patrol_investigate(ai)
        tree, enable = self._make_enable()
        code, _warnings = self._gen_with_ai(ai, tree)
        self.assertIn("_gk_heard_", code)
        self.assertIn("not _gk_heard_", code)      # 安静下来 = 听不见
        # 听觉条件不需要目标 —— 生成里不该出现"目标的距离"那套
        self.assertNotIn("ai_hear(点", code)

    def test_ai_hear_expression_node(self):
        """『AI_听见』在玩法图里也能用(不经过 AI 图)。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        hear = tree.new_node(NN.GKNodeAiHear, "听见了吗", gk_ear=0.0,
                             gk_memory=2.0)
        branch = tree.new_node(NN.GKNodeBranch, "分支")
        gk_stub.connect(tree, event, "执行", branch, "执行")
        gk_stub.connect(tree, hear, "听见", branch, "条件")
        code = gen(tree, V3)[0]
        self.assertIn("_gk_heard_", code)
        self.assertIn("func _gk_hear_noise(", code)   # 顺带把运行时生成出来
        self.assert_no_chinese_identifiers(code)

    # ---- 校验:不能把听觉转场误报成"没选目标" ----
    def test_hearing_transition_does_not_require_goal(self):
        """⚠ 听觉转场**不需要**目标 —— 别误报。

        以前"只要条件来源不是『自己写』就必须选目标",加了听觉之后
        会把合法的听觉转场刷一屏假告警。
        """
        ai = self._ai_tree()
        self._patrol_investigate(ai)
        graph = NN.ai_tree_graph(ai)
        goal_problems = [p for p in graph["problems"] if "没选『目标』" in p]
        self.assertEqual(goal_problems, [], goal_problems)
        # 而"看见/丢失"该报还是要报
        ai2 = self._ai_tree("敌人AI2")
        patrol = ai2.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻")
        chase = ai2.new_node(NN.GKNodeAiState, "追击", gk_state_name="追击",
                             gk_behavior="CHASE", gk_goal="玩家")
        ai2.new_node(NN.GKNodeAiTransition, "看见",
                     gk_cond_source="SEE", gk_from_state="巡逻",
                     gk_to_state="追击")
        problems2 = NN.ai_tree_graph(ai2)["problems"]
        self.assertTrue(any("没选『目标』" in p for p in problems2), problems2)

    def test_hearing_edges_labelled(self):
        ai = self._ai_tree()
        self._patrol_investigate(ai)
        graph = NN.ai_tree_graph(ai)
        kinds = sorted(edge["kind"] for edge in graph["edges"])
        self.assertEqual(kinds, ["CALM", "HEARD"])
        labels = [NN.ai_edge_label(edge) for edge in graph["edges"]]
        self.assertTrue(any("听见" in text for text in labels), labels)
        self.assertTrue(any("安静" in text for text in labels), labels)

    # ---- 2D 也要能用(向量维度要对) ----
    def test_hearing_works_in_2d(self):
        """2D 项目里噪音位置得是 Vector2 —— 用 Vector3 会编译不过。"""
        ai = self._ai_tree()
        self._patrol_investigate(ai)
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        enable = tree.new_node(NN.GKNodeAiEnable, "启用AI")
        gk_stub.connect(tree, event, "执行", enable, "执行")
        noise = tree.new_node(NN.GKNodeNoise, "跑步声")
        gk_stub.connect(tree, enable, "完成", noise, "执行")
        options = dict(dimension="2", base_class="CharacterBody2D")
        code, _warnings = self._gen_with_ai(ai, tree, options)
        self.assertIn("Vector2.ZERO", code)
        self.assertNotIn("Vector3.ZERO", code)
        self.assertIn("func _gk_hear_noise(at: Vector2", code)
        self.assert_no_chinese_identifiers(code)

    def test_two_physics_events_only_declare_unused_delta_once(self):
        """⚠ 树里有两个『每当_物理帧』时,`_unused_delta` **只能声明一次**。

        真机踩到(这轮的 AI 示例探针里):两个物理帧节点各往**同一个
        函数体**里塞一句 `var _unused_delta := delta`,Godot 直接
        `Parse Error: There is already a variable named "_unused_delta"
        declared in this scope.` —— **整个脚本编译不过、游戏不跑**,
        而且只报这一句,很难联想到"是因为放了两个物理帧节点"。

        (角色预设自带一个物理帧,再自己加一个就必现 —— 这条路径很常见。)
        """
        tree = make_tree()
        first = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧1")
        second = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧2")
        noise1 = tree.new_node(NN.GKNodeNoise, "动静1")
        noise2 = tree.new_node(NN.GKNodeNoise, "动静2")
        gk_stub.connect(tree, first, "执行", noise1, "执行")
        gk_stub.connect(tree, second, "执行", noise2, "执行")
        code = gen(tree, V3)[0]
        declared = [line for line in code.splitlines()
                    if "_unused_delta" in line and line.strip().startswith("var ")]
        self.assertEqual(len(declared), 1,
                         f"`_unused_delta` 声明了 {len(declared)} 次(Godot 会报"
                         f"重复声明、整个脚本编不过): {declared}")
        # 两段都还在(不能为了去重把第二个事件吞掉)
        self.assertIn("另一个同类事件", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def _make_enable(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        enable = tree.new_node(NN.GKNodeAiEnable, "启用AI")
        gk_stub.connect(tree, event, "执行", enable, "执行")
        return tree, enable


class TestRound59Horror(Base):
    """第五十九轮:恐怖氛围套件(阶段④收尾)。

    四个新节点:
      * 『惊吓_事件』 —— 相机震动 + 闪白 + 音效三连(一个节点全做);
      * 『氛围_紧张度』 —— 离威胁越近越高,输出 0~1(**纯表达式**,不平滑);
      * 『氛围_心跳』 —— 紧张度驱动的心跳(越快越响,低于阈值停);
      * 『音效_总线音量』 —— 设总线音量(可渐变)。
    判据:各生成对的 Godot API;没用到时一个字符都不多。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_scare_does_all_three(self):
        """三连:相机震动 + 全屏 ColorRect 闪白 + 一次性音效。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeScare, "开门吓一跳",
                             gk_strength=14.0, gk_duration=0.4,
                             gk_color=(1.0, 0.0, 0.0, 1.0), gk_alpha=0.7,
                             gk_flash_time=0.25,
                             gk_path="res://art/slam.wav", gk_volume=-2.0)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        # ① 震动(和『相机_震动』同一套成员变量 + 每帧衰减钩子)
        self.assertIn("_gk_shake_cam", code)
        self.assertIn("_gk_shake_power = 14.0", code)
        self.assertIn("randf_range(-_gk_shake_power, _gk_shake_power)", code)
        # ② 闪白(全屏 ColorRect + Tween 淡出)
        self.assertIn("ColorRect.new()", code)
        self.assertIn("Color(1.0, 0.0, 0.0, 0.7)", code)
        self.assertIn('tween_property(', code)
        self.assertIn('"color:a", 0.0, 0.25)', code)
        # ③ 音效(运行时建、播完自删)
        self.assertIn('load("res://art/slam.wav") as AudioStream', code)
        self.assertIn("AudioStreamPlayer.new()", code)
        self.assertIn(".finished.connect(", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_scare_without_sound_still_shakes_and_flashes(self):
        """『音效』留空 = 只震 + 闪,不生成任何音频代码(没素材也能先用)。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeScare, "震一下", gk_path="")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_shake_cam", code)
        self.assertIn("ColorRect.new()", code)
        self.assertNotIn("AudioStreamPlayer", code)
        self.assert_indent_sane(code)

    def test_tension_is_pure_expression_between_near_and_far(self):
        """紧张度是**内联表达式**(不落中间变量),用位置 reader 算距离。"""
        tree = make_tree()
        event = self._ready(tree)
        heart = tree.new_node(NN.GKNodeHeartbeat, "心跳")
        tension = tree.new_node(NN.GKNodeTension, "紧张",
                                gk_threat="怪物", gk_near=3.0, gk_far=15.0)
        gk_stub.connect(tree, event, "执行", heart, "执行")
        gk_stub.connect(tree, tension, "紧张度", heart, "紧张度")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        # 位置 reader(和『取值_位置』共用的那个)
        self.assertIn("func _gk_global_position_3d(host: Node)", code)
        self.assertIn("_gk_global_position_3d(", code)
        # 0~1 夹取 + near/far 线性
        self.assertIn("clampf(", code)
        self.assertIn("maxf(", code)
        self.assertIn("0.0001", code)
        # 是内联的:心跳那行直接读到 clampf,而不是先赋值给某个 _gk_tension_*
        self.assertIn("_gk_heart_tension += (clampf(", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_tension_without_threat_warns_and_returns_zero(self):
        tree = make_tree()
        event = self._ready(tree)
        heart = tree.new_node(NN.GKNodeHeartbeat, "心跳")
        tension = tree.new_node(NN.GKNodeTension, "紧张")   # 没选威胁
        gk_stub.connect(tree, event, "执行", heart, "执行")
        gk_stub.connect(tree, tension, "紧张度", heart, "紧张度")
        code, warnings = gen(tree, V3)
        self.assertTrue(any("没有指定" in w for w in warnings), warnings)
        self.assertIn("_gk_heart_tension += (0.0", code)

    def test_heartbeat_speeds_up_and_stops_when_calm(self):
        """心跳:间隔/音量随紧张度 lerp,低于安静阈值就停。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeHeartbeat, "心跳",
                             gk_path="res://art/heartbeat.wav",
                             gk_slow_interval=1.0, gk_fast_interval=0.3,
                             gk_quiet_db=-20.0, gk_loud_db=-4.0,
                             gk_quiet=0.2, gk_smooth=1.5)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        # 固定步长(不依赖 delta 参数),平滑朝目标靠拢
        self.assertIn("get_physics_process_delta_time()", code)
        self.assertIn("/ maxf(1.5, 0.0001)", code)
        # 越快越响 + 安静阈值
        self.assertIn("lerpf(1.0, 0.3, _gk_heart_tension)", code)
        self.assertIn("lerpf(-20.0, -4.0, _gk_heart_tension)", code)
        self.assertIn("_gk_heart_tension <= 0.2", code)
        self.assertIn("_gk_heart_player.play()", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_heartbeat_without_sound_warns(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeHeartbeat, "心跳", gk_path="")
        gk_stub.connect(tree, event, "执行", node, "执行")
        _code, warnings = gen(tree, V2)
        self.assertTrue(any("没填『心跳音效』" in w for w in warnings), warnings)

    def test_bus_volume_sets_audioserver_with_fade(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeBusVolume, "压低BGM",
                             gk_bus="Music", gk_db=-80.0, gk_fade=1.0)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('AudioServer.get_bus_index("Music")', code)
        self.assertIn("AudioServer.set_bus_volume_db(", code)
        self.assertIn("AudioServer.get_bus_volume_db(", code)
        self.assertIn("tween_method(func(_v: float):", code)
        self.assertIn(", 1.0)", code)          # tween 时长
        self.assertIn("找不到音频总线", code)     # 总线名写错时有可读告警
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_save_guard_keeps_indent_inside_branch(self):
        """真机 bug(第五十九轮):存档节点在 `if` / `match` 里时,惰性读盘守卫
        的缩进必须跟着**当前层**走。

        老实现把守卫行硬插进行列表、缩进写死 4 个空格 —— 只在语句位于
        函数体第一层时才对。放进分支里(恐怖示例的"选了结局再存档")就生成::

            if x:
                _gk_save_data["hp"] = 0
            _gk_save_path = _gk_save_file("save1")     <- 缩进变浅
                _gk_save_data = _gk_read_save(...)      <- 再深回去
        Godot 报 `Parse Error: Expected statement, found "Indent" instead.`,
        **整个脚本编不过**。
        """
        tree = make_tree()
        event = self._ready(tree)
        cond = tree.new_node(NN.GKNodeConstBool, "开关", gk_value=True)
        branch = tree.new_node(NN.GKNodeBranch, "要不要存")
        setter = tree.new_node(NN.GKNodeSaveSet, "写", gk_key="hp", gk_slot="save1")
        gk_stub.connect(tree, cond, "值", branch, "条件")
        gk_stub.connect(tree, event, "执行", branch, "执行")
        gk_stub.connect(tree, branch, "是", setter, "执行")
        code, _ = gen(tree, V2)
        # 守卫在分支体里 = 8 空格缩进(不是写死的 4 空格)
        self.assertIn(
            '\n        _gk_save_path = _gk_save_file("save1")\n'
            '        if _gk_save_path == "":\n'
            '            _gk_save_path = _gk_save_file("save1")\n'
            '            _gk_save_data = _gk_read_save(_gk_save_path)\n',
            code, code)
        self.assertIn('\n        _gk_save_data["hp"] = 3', code, code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_save_guard_emitted_before_the_write(self):
        """守卫要在写入**之前** —— 『存档_写入』往字典里塞值时,那份字典
        必须先被载入(Godot 里对 null Dictionary 赋值会报错)。"""
        tree = make_tree()
        event = self._ready(tree)
        setter = tree.new_node(NN.GKNodeSaveSet, "写", gk_key="hp", gk_slot="save1")
        gk_stub.connect(tree, event, "执行", setter, "执行")
        code, _ = gen(tree, V2)
        self.assertLess(code.index("_gk_read_save(_gk_save_path)"),
                        code.index('_gk_save_data["hp"]'))

    def test_no_horror_code_without_the_nodes(self):
        """没用到氛围套件的图里一个相关字符都不多(老产物逐字不变)。"""
        tree = make_tree()
        event = self._ready(tree)
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="hi")
        gk_stub.connect(tree, event, "执行", printer, "执行")
        code, _ = gen(tree, V2)
        self.assertNotIn("_gk_shake_", code)
        self.assertNotIn("_gk_heart_", code)
        self.assertNotIn("AudioServer", code)
        self.assertNotIn("_gk_global_position_", code)


class TestRound61BlendTree(Base):
    """第六十一轮:动画混合树。

    ── 它和状态机的根本区别 ────────────────────────────────────────
    状态机是**手写 if/elif + AnimationPlayer.play()**(离散切换);
    混合树要的是"按参数连续混合",AnimationPlayer 做不到(它没有权重),
    所以这一块**真的**建一个 Godot `AnimationTree` + `AnimationNodeBlendSpace1D`,
    每物理帧把参数推给 `parameters/blend_position`。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_parse_blend_points(self):
        parse = codegen.animation.parse_blend_points
        # 第三个数是**这个点自己的速度**(第七十三轮),不写 = 1.0
        self.assertEqual(parse("走:0|跑:1"),
                         [("走", 0.0, 1.0), ("跑", 1.0, 1.0)])
        self.assertEqual(parse(" 走 : 0.5 "), [("走", 0.5, 1.0)])
        self.assertEqual(parse("走:0:1.5"), [("走", 0.0, 1.5)])
        # 没写位置 -> 按顺序补 0/1/2…;空段跳过
        self.assertEqual(parse("甲||乙"), [("甲", 0.0, 1.0), ("乙", 1.0, 1.0)])
        self.assertEqual(parse(""), [])
        # 超过 6 个点只取前 6 个(BlendSpace 够用,也免得卡片画不下)
        self.assertEqual(len(parse("|".join(f"a{i}:{i}" for i in range(9)))), 6)

    def test_builds_animation_tree_and_blend_space(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeBlendTree, "走路跑步",
                             gk_points="走:0|慢跑:1|冲刺:2", gk_value=0.0)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        # 真的建 AnimationTree(不是又一段 if/elif)
        self.assertIn("func _gk_ensure_blend_tree(host: Node, player: Node,"
                      " points: Array, tree_name: String, loop: bool)"
                      " -> AnimationTree:", code)
        self.assertIn("AnimationTree.new()", code)
        # 循环:glTF 导进来的动画默认不循环,播一遍就停在最后一帧
        self.assertIn("anim.loop_mode == Animation.LOOP_NONE", code)
        self.assertIn("anim.loop_mode = Animation.LOOP_LINEAR", code)
        self.assertIn('"GKBlendTree", true)', code)
        self.assertIn("AnimationNodeBlendSpace1D.new()", code)
        self.assertIn("AnimationNodeAnimation.new()", code)
        self.assertIn("space.add_blend_point(", code)
        # 三个混合点都带过去(名 + 位置)
        self.assertIn('["走", 0.0]', code)
        self.assertIn('["慢跑", 1.0]', code)
        self.assertIn('["冲刺", 2.0]', code)
        # 接管播放器:root_node / anim_player / active 都要设
        self.assertIn("tree.anim_player = tree.get_path_to(player)", code)
        self.assertIn("tree.active = true", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_pushes_parameter_every_physics_frame(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeBlendTree, "混合", gk_points="走:0|跑:1")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V2)
        # 建树(在 _ready)+ 每帧推参数(在 _physics_process)
        self.assertIn("if blend_tree == null:", code)
        self.assertIn('blend_tree.set("parameters/blend_position", 0.0)', code)
        self.assertIn("func _physics_process(", code)

    def test_parameter_can_come_from_a_pin(self):
        """参数接引脚 -> 用引脚上的表达式,而不是卡片里的固定值。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeBlendTree, "混合", gk_points="走:0|跑:1")
        value = tree.new_node(NN.GKNodeGetVar, "读速度", gk_var_name="速度")
        gk_stub.connect(tree, event, "执行", node, "执行")
        gk_stub.connect(tree, value, "值", node, "参数")
        code, _ = gen(tree, V2)
        self.assertIn('set("parameters/blend_position", 速度)', code)

    def test_smooth_uses_lerp(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeBlendTree, "混合", gk_points="走:0|跑:1",
                             gk_smooth=0.3)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, _ = gen(tree, V2)
        self.assertIn("get_physics_process_delta_time()", code)
        self.assertIn('float(blend_tree.get("parameters/blend_position"))', code)
        self.assertIn("/ maxf(0.3, 0.0001)", code)

    def test_too_few_points_warns(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeBlendTree, "混合", gk_points="走:0")
        gk_stub.connect(tree, event, "执行", node, "执行")
        _code, warnings = gen(tree, V2)
        self.assertTrue(any("至少要有" in w for w in warnings), warnings)
        # 没生成任何混合相关代码
        self.assertNotIn("_gk_ensure_blend_tree(", _code)

    def test_no_blend_code_without_the_node(self):
        """没用到混合树的图里一个相关字符都不多(老产物逐字不变)。"""
        tree = make_tree()
        event = self._ready(tree)
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="hi")
        gk_stub.connect(tree, event, "执行", printer, "执行")
        code, _ = gen(tree, V2)
        self.assertNotIn("AnimationTree", code)
        self.assertNotIn("blend_position", code)
        self.assertNotIn("_gk_ensure_blend_tree", code)


class TestRound62NodeGroups(Base):
    """第六十二轮:分组框(可折叠)。

    ── 这一轮为什么"几乎没有生成逻辑"────────────────────────────────
    用户的原话是"节点图一大就没法整理"——要的是**可读性**,不是复用
    (复用归『折叠成函数』管)。所以做法是在 Blender 原生 `NodeFrame`
    上补一层折叠:收起 = 框内节点 `hide = True` + 框缩成一条标题栏。

    铁律:**生成的代码一个字都不能变**。`NodeFrame` 本来就不是 GK 节点,
    codegen 会跳过它;`node.hide` 也只是显示开关,连线一条不少。
    下面把这条锁死 —— 免得以后有人"顺手"在遍历里加一句
    `if node.hide: continue`,把折叠变成一个会改行为的开关。
    """

    class _Frame:
        """离线替身:只实现 node_groups 真正会碰的那几个属性。"""

        def __init__(self, name="分组", height=200.0, label="分组"):
            self.bl_idname = "NodeFrame"
            self.name = name
            self.label = label
            self.location = (0.0, 0.0)
            self.width = 300.0
            self.height = height
            self.hide = False
            self.parent = None
            self.use_custom_color = False
            self.color = (0.16, 0.30, 0.45)
            self._props = {}

        def __getitem__(self, key):
            return self._props[key]

        def __setitem__(self, key, value):
            self._props[key] = value

        def get(self, key, default=None):
            return self._props.get(key, default)

    def _frame(self, tree, name="分组", height=200.0):
        frame = self._Frame(name=name, height=height)
        tree.nodes.append(frame)
        return frame

    # ---- 折叠 / 展开本身 ----

    def test_collapse_hides_children_and_shrinks(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="hi")
        frame = self._frame(tree, height=180.0)
        event.parent = frame
        printer.parent = frame

        self.assertFalse(node_groups.is_collapsed(frame))
        self.assertTrue(node_groups.collapse(tree, frame))
        self.assertTrue(event.hide and printer.hide)
        self.assertAlmostEqual(frame.height, node_groups.GROUP_BAR_HEIGHT)
        self.assertTrue(node_groups.is_collapsed(frame))
        # 收起态换个颜色(收着东西一眼看得出来)
        self.assertEqual(tuple(frame.color), node_groups.COLLAPSED_COLOR)

        self.assertTrue(node_groups.expand(tree, frame))
        self.assertFalse(event.hide or printer.hide)
        self.assertAlmostEqual(frame.height, 180.0)
        self.assertFalse(node_groups.is_collapsed(frame))
        self.assertEqual(tuple(frame.color), (0.16, 0.30, 0.45))

    def test_collapse_twice_is_a_noop(self):
        tree = make_tree()
        node = tree.new_node(NN.GKNodePrint, "打印")
        frame = self._frame(tree)
        node.parent = frame
        self.assertTrue(node_groups.collapse(tree, frame))
        # 第二次不该"再记一次原高度"(记的会是收起后的 30 -> 展开就回不去)
        self.assertFalse(node_groups.collapse(tree, frame))
        self.assertTrue(node_groups.expand(tree, frame))
        self.assertAlmostEqual(frame.height, 200.0)

    def test_toggle_reports_resulting_state(self):
        tree = make_tree()
        node = tree.new_node(NN.GKNodePrint, "打印")
        frame = self._frame(tree)
        node.parent = frame
        self.assertTrue(node_groups.toggle(tree, frame))     # 收起
        self.assertTrue(node.hide)
        self.assertFalse(node_groups.toggle(tree, frame))    # 展开
        self.assertFalse(node.hide)

    # ---- 嵌套 ----

    def test_descendants_walk_nested_frames(self):
        tree = make_tree()
        outer = self._frame(tree, "外层")
        inner = self._frame(tree, "内层")
        inner.parent = outer
        deep = tree.new_node(NN.GKNodePrint, "深处的打印")
        deep.parent = inner
        near = tree.new_node(NN.GKNodePrint, "外层的打印")
        near.parent = outer

        self.assertEqual([n.name for n in node_groups.direct_children(tree, outer)],
                         ["内层", "外层的打印"])
        self.assertEqual(sorted(n.name for n in node_groups.descendants(tree, outer)),
                         sorted(["内层", "深处的打印", "外层的打印"]))
        self.assertEqual(node_groups.count_nodes(tree, outer), 3)
        # 从最里面的节点往上找,先碰到内层
        self.assertEqual(node_groups.frame_of(deep).name, "内层")
        self.assertEqual(node_groups.frame_of(inner).name, "外层")

    def test_expanding_outer_keeps_inner_collapsed(self):
        """外层展开时,里面那个**自己收着**的组不能被一起摊开。"""
        tree = make_tree()
        outer = self._frame(tree, "外层")
        inner = self._frame(tree, "内层")
        inner.parent = outer
        deep = tree.new_node(NN.GKNodePrint, "深处的打印")
        deep.parent = inner

        node_groups.collapse(tree, inner)
        self.assertTrue(deep.hide)
        node_groups.collapse(tree, outer)
        # 外层收起 -> 里面全藏(包括内层这个框自己)
        self.assertTrue(deep.hide and inner.hide)
        node_groups.expand(tree, outer)
        self.assertFalse(inner.hide)     # 内层框本身回来了
        self.assertTrue(deep.hide)       # 但里面那组还是收着的
        self.assertTrue(node_groups.is_collapsed(inner))

    def test_nested_collapse_hides_whole_subtree(self):
        tree = make_tree()
        outer = self._frame(tree, "外层")
        inner = self._frame(tree, "内层")
        inner.parent = outer
        deep = tree.new_node(NN.GKNodePrint, "深处的打印")
        deep.parent = inner
        node_groups.collapse(tree, outer)
        self.assertTrue(inner.hide and deep.hide)

    # ---- 对代码生成零影响(这一轮最重要的一条)----

    def test_collapsed_group_generates_identical_code(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        printer = tree.new_node(NN.GKNodePrint, "打印", gk_text="你好")
        direction = tree.new_node(NN.GKNodeGetVector, "方向输入")
        move = tree.new_node(NN.GKNodeMoveDirection, "移动", gk_speed=240.0)
        gk_stub.connect(tree, event, "执行", printer, "执行")
        gk_stub.connect(tree, event, "执行", move, "执行")
        gk_stub.connect(tree, direction, "方向", move, "方向")

        frame = self._frame(tree, "开局", height=220.0)
        for node in (event, printer, direction, move):
            node.parent = frame

        before, _ = gen(tree, V3)
        self.assertIn("move_and_slide()", before)

        node_groups.collapse(tree, frame)
        after, _ = gen(tree, V3)
        # 逐字一样:折叠只是显示,连线一条不少、事件照旧一个个都生成
        self.assertEqual(before, after)

        node_groups.expand(tree, frame)
        again, _ = gen(tree, V3)
        self.assertEqual(before, again)

    def test_frame_alone_never_shows_up_in_code(self):
        """一张只有框、没有 GK 节点的图 -> 不生成任何东西(不是"生成一段空壳")。"""
        tree = make_tree()
        frame = self._frame(tree)
        lonely = tree.new_node(NN.GKNodePrint, "打印")
        lonely.parent = frame
        code, _ = gen(tree, V3)
        self.assertNotIn("NodeFrame", code)
        self.assertNotIn("分组", code)


class TestRound63AiPathfinding(Base):
    """第六十三轮:"让 AI 行为直接用寻路"。

    ── 为什么是**自动**接、不再给节点加开关 ─────────────────────────
    用户的话:"寻路应该整合到游戏AI那里去"。追人的敌人一头撞墙是这个插件
    里最刺眼的表现,而『移动_寻路』是个**手动**节点 —— 要用户给每个追击
    状态再拼一套寻路,基本没人会做。

    所以判据收敛到一个开关:**场景设置里的『寻路』**。
      * 没勾 -> 生成的代码**逐字不变**(老图产物不受影响);
      * 勾了 -> 追击 / 跟随 / 去查看 / 巡逻 / 返回 改走
        `NavigationServer.map_get_path` 算出来的方向(绕开墙和石头)。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def _ai_tree(self, name="敌人AI"):
        ai = gk_stub.StubNodeTree(name)
        ai.bl_idname = "GKGameAiTree"
        return ai

    def _gen_with_ai(self, ai, tree, options=None):
        """把 AI 图挂到 bpy.data 上再生成(用完还原,照界面那套)。"""
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[ai]))
        try:
            return gen(tree, options or V3)
        finally:
            if original is None:
                try:
                    delattr(bpy_module(), "data")
                except AttributeError:
                    pass
            else:
                setattr(bpy_module(), "data", original)

    def _with_chase(self, ai, behavior="CHASE"):
        """AI 图:一个『追击』(或指定行为)-> 玩法图里『AI_启用』它。"""
        state = ai.new_node(NN.GKNodeAiState, "追", gk_state_name="追",
                            gk_behavior=behavior, gk_goal="玩家",
                            gk_speed=4.0, gk_stop_distance=1.2,
                            gk_facing="GOAL")
        tree = make_tree()
        event = self._ready(tree)
        enable = tree.new_node(NN.GKNodeAiEnable, "启用AI", gk_target="玩家")
        gk_stub.connect(tree, event, "执行", enable, "执行")
        return tree, state

    # ---- 没勾『寻路』:一行都不能变(老图回归锁)----

    def test_nav_off_keeps_straight_line(self):
        ai = self._ai_tree()
        tree, _state = self._with_chase(ai)
        code, warnings = self._gen_with_ai(ai, tree)
        self.assertEqual(warnings, [], warnings)
        # 直线走(老行为),且**没有**任何导航相关的东西
        self.assertIn("move_and_slide()", code)
        self.assertNotIn("_gk_ai_nav_dir", code)
        self.assertNotIn("NavigationServer", code)
        self.assertIn(".normalized() * 4.0", code)

    def test_nav_off_2d_keeps_straight_line(self):
        ai = self._ai_tree()
        tree, _state = self._with_chase(ai)
        code, _ = self._gen_with_ai(ai, tree, V2)
        self.assertNotIn("NavigationServer", code)

    # ---- 勾了『寻路』:改走导航网格 ----

    def test_nav_on_chase_uses_navigation_server(self):
        ai = self._ai_tree()
        tree, _state = self._with_chase(ai)
        code, warnings = self._gen_with_ai(ai, tree, dict(V3, nav_enabled=True))
        self.assertEqual(warnings, [], warnings)
        # 顶层 helper 只生成一份
        # ⚠ 第八十五轮(多层导航)给签名加了个**默认形参** `layers: int = 1`
        #   (调用点不填就是老行为;见 TestRound85NavLayers)。
        self.assertIn("func _gk_ai_nav_dir(host: Node, goal: Vector3, "
                      "layers: int = 1) -> Vector3:", code)
        self.assertEqual(code.count("func _gk_ai_nav_dir("), 1)
        # 真去问导航服务器要路径,并**跳过退化点**找下一个路点
        # (只看 path[1] 会在"自己正站在拐角上"时退回直线 -> 卡墙角)
        self.assertIn("NavigationServer3D.map_get_path(", code)
        self.assertIn("body.get_world_3d().navigation_map", code)
        self.assertIn("for i in range(1, path.size()):", code)
        self.assertIn("step.y = 0.0", code)
        # 行为里真的用上了那个方向(而不是只剩直线)
        self.assertIn("_gk_ai_nav_dir(", code)
        self.assertIn("velocity = ", code)
        self.assert_indent_sane(code)
        self.assert_no_chinese_identifiers(code)

    def test_nav_on_2d_uses_navigation_server_2d(self):
        ai = self._ai_tree()
        tree, _state = self._with_chase(ai)
        code, _ = self._gen_with_ai(ai, tree, dict(V2, nav_enabled=True))
        self.assertIn("func _gk_ai_nav_dir(host: Node, goal: Vector2, "
                      "layers: int = 1) -> Vector2:", code)
        self.assertIn("NavigationServer2D.map_get_path(", code)
        self.assertIn("body.get_world_2d().navigation_map", code)
        # 2D 没有"高度"这回事,不压平
        self.assertNotIn("step.y = 0.0", code)

    def test_nav_on_patrol_also_pathfinds(self):
        ai = self._ai_tree()
        ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                    gk_behavior="PATROL", gk_patrol_a="点A", gk_patrol_b="点B",
                    gk_speed=3.0, gk_stop_distance=0.6)
        tree = make_tree()
        event = self._ready(tree)
        enable = tree.new_node(NN.GKNodeAiEnable, "启用AI", gk_target="玩家")
        gk_stub.connect(tree, event, "执行", enable, "执行")
        code, _ = self._gen_with_ai(ai, tree, dict(V3, nav_enabled=True))
        self.assertIn("_gk_ai_nav_dir(", code)
        # 巡逻两头各走一次(翻面用的三元表达式还在)
        self.assertIn(" if ", code)

    def test_nav_on_investigate_uses_the_noise_point(self):
        """『去查看』的目标是**坐标**(不是物体),照样走导航网格。"""
        ai = self._ai_tree()
        ai.new_node(NN.GKNodeAiState, "去看看", gk_state_name="去看看",
                    gk_behavior="INVESTIGATE", gk_speed=3.0, gk_stop_distance=0.6)
        tree = make_tree()
        event = self._ready(tree)
        enable = tree.new_node(NN.GKNodeAiEnable, "启用AI", gk_target="玩家")
        gk_stub.connect(tree, event, "执行", enable, "执行")
        code, _ = self._gen_with_ai(ai, tree, dict(V3, nav_enabled=True))
        self.assertIn("_gk_ai_nav_dir(", code)

    def test_nav_on_emits_helper_only_once_for_two_states(self):
        ai = self._ai_tree()
        ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                    gk_behavior="PATROL", gk_patrol_a="点A", gk_speed=3.0,
                    gk_stop_distance=0.6)
        ai.new_node(NN.GKNodeAiState, "追", gk_state_name="追",
                    gk_behavior="CHASE", gk_goal="玩家", gk_speed=4.0,
                    gk_stop_distance=1.0)
        tree = make_tree()
        event = self._ready(tree)
        enable = tree.new_node(NN.GKNodeAiEnable, "启用AI", gk_target="玩家")
        gk_stub.connect(tree, event, "执行", enable, "执行")
        code, _ = self._gen_with_ai(ai, tree, dict(V3, nav_enabled=True))
        self.assertEqual(code.count("func _gk_ai_nav_dir("), 1)
        # 两个行为都在调它
        self.assertGreaterEqual(code.count(":= _gk_ai_nav_dir("), 2)

    def test_nav_on_leaves_flee_alone(self):
        """『逃跑』在第八十轮(D-11)接上了导航。

        第六十三轮它是唯一不接导航的移动行为("没有固定目标点");
        第八十轮的做法:朝**威胁反方向投影一个『逃跑距离』外的点**,
        把这个点交给导航网格 —— 有墙时绕着墙逃,不可达时 helper 自己
        退回直线(见 TestRound80FleeNavAndHearing)。
        """
        ai = self._ai_tree()
        tree, _state = self._with_chase(ai, behavior="FLEE")
        code, _ = self._gen_with_ai(ai, tree, dict(V3, nav_enabled=True))
        self.assertRegex(
            code,
            r"var _ai_flee_goal\d+ := _ai_here\d+ \+ "
            r"_ai_away\d+\.normalized\(\) \* 8\.0")
        self.assertIn("_gk_ai_nav_dir(", code)

    # ---- 顺手抓到的另一个真 bug:晚登记的引用漏 helper ----

    def test_reference_helper_emitted_for_late_references(self):
        """引用**只来自 AI 图**时,`_gk_ref_typed` 也必须生成。

        ── 这个 bug 是怎么暴露的(第六十三轮)────────────────────────
        AI 图 / 动画图 / 界面设计图是『AI_启用』『动画_状态机』
        『界面_套用设计』**引用时现场展开**的 —— 它们在 `generate()` 很靠后
        的位置才跑,而 `_gk_ref` / `_gk_ref_typed` 的生成写在**前半段**。
        于是"引用全部来自 AI 图"的脚本会得到:

            @onready var 节点_玩家: Node = _gk_ref_typed(self, "玩家", "Node")

        却没有 `_gk_ref_typed` 这个函数 —— Godot 直接
            Parse Error: Function "_gk_ref_typed()" not found in base self
        **整个脚本加载不了**(不是少功能,是跑不起来)。

        这个 bug 是新示例 `examples/demo_ai_nav` 在**真 Godot** 里跑出来的:
        离线单测当时全绿,因为它们不检查"声明和定义配没配上"。
        """
        ai = self._ai_tree()
        tree, _state = self._with_chase(ai)
        code, _ = self._gen_with_ai(ai, tree, dict(V3, nav_enabled=True))
        self.assertIn("@onready var 角色_玩家", code)
        self.assertIn("func _gk_ref_typed(", code)
        self.assertIn("func _gk_ref(", code)
        # 前面补一次、末尾再兜一次 —— 但只该落一份
        self.assertEqual(code.count("func _gk_ref_typed("), 1)
        self.assertEqual(code.count("func _gk_ref("), 1)

    def test_reference_helper_not_duplicated_for_normal_nodes(self):
        """普通节点的引用还是只生成一份(老路径不受"末尾兜底"影响)。"""
        tree = make_tree()
        event = self._ready(tree)
        move = tree.new_node(NN.GKNodeMoveToward, "朝向", gk_target="玩家")
        gk_stub.connect(tree, event, "执行", move, "执行")
        code, _ = gen(tree, V3)
        self.assertIn("@onready var 节点_玩家", code)
        self.assertEqual(code.count("func _gk_ref_typed("), 1)
        self.assertEqual(code.count("func _gk_ref("), 1)


class TestRound64Narrative(Base):
    """第六十四轮:音频与叙事 —— 字幕行 / 打字机音效 / 对话历史回顾。

    ── 这一轮验什么 ────────────────────────────────────────────────
      * 『界面_字幕』**不阻塞**(生成里没有 await 循环)—— 后面跟着的节点
        立刻接着跑,所以"一边运镜一边出旁白"才写得出来;
      * 打字音:逐字显示改成 `tween_method` + 每 N 个字响一声;
      * 每显示一句就自动进对话历史,还有上限(免得长流程吃内存);
      * **只用到对话框 / 字幕的图也要能编译**:`_gk_ui_layer` / `_gk_ui_root`
        这两个 UI 根容器变量原来只有界面控件节点才声明 —— 真 Godot
        `--check-only` 跑出 `Identifier "_gk_ui_layer" not declared`,
        整个脚本加载不了。这条锁死它。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    # ---- 字幕:不阻塞 ----

    def test_subtitle_does_not_block(self):
        tree = make_tree()
        event = self._ready(tree)
        sub = tree.new_node(NN.GKNodeUiSubtitle, "旁白", gk_text="脚步声……",
                            gk_seconds=2.5)
        later = tree.new_node(NN.GKNodePrint, "后面的节点", gk_text="hi")
        gk_stub.connect(tree, event, "执行", sub, "执行")
        gk_stub.connect(tree, sub, "完成", later, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        # 飘一句就走(参数齐全)
        self.assertIn("_gk_subtitle_show(self, \"\", str(\"脚步声……\"), 30.0, "
                      "2.5, 0.3, 720.0, 110.0, 48.0)", code)
        # 字幕**不能**有自己的 await 等待环(那就不叫字幕了)
        self.assertNotIn("while not _gk_dialog_clicked:", code)
        # 后面的节点照跑
        self.assertIn('print("hi")', code)
        self.assert_indent_sane(code)

    def test_subtitle_uses_the_dialog_panel_and_cancels_old_timer(self):
        tree = make_tree()
        event = self._ready(tree)
        sub = tree.new_node(NN.GKNodeUiSubtitle, "旁白", gk_text="甲")
        gk_stub.connect(tree, event, "执行", sub, "执行")
        code, _ = gen(tree, V3)
        # 复用对话框那块面板(同一套观感)
        self.assertIn("func _gk_subtitle_show(", code)
        # 第八十一轮起立绘走 `portrait` 形参(默认空 = 老行为一字不变)
        self.assertIn("_gk_dialog_show(host, speaker, body, speed, portrait, "
                      "width, height, margin)", code)
        # 代数守卫:老定时器不许把新的一句关掉
        self.assertIn("_gk_sub_token += 1", code)
        self.assertIn("if token != _gk_sub_token:", code)

    # ---- 打字机音效 ----

    def test_typing_sound_sets_runtime_vars_and_uses_tween_method(self):
        tree = make_tree()
        event = self._ready(tree)
        dialog = tree.new_node(NN.GKNodeUiDialog, "第一句", gk_text="你好",
                               gk_type_sound="res://art/type.wav",
                               gk_type_every=3, gk_type_volume=-8.0)
        gk_stub.connect(tree, event, "执行", dialog, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('_gk_dialog_blip = "res://art/type.wav"', code)
        self.assertIn("_gk_dialog_blip_every = 3", code)
        self.assertIn("_gk_dialog_blip_db = -8.0", code)
        # 有打字音时逐字走 tween_method(属性 tween 插不进回调)
        self.assertIn("_gk_dialog_tween.tween_method(", code)
        self.assertIn("func _gk_dialog_advance(host: Node, shown: int) -> void:",
                      code)
        self.assertIn("func _gk_play_blip(host: Node, path: String, db: float) -> void:",
                      code)
        self.assert_indent_sane(code)

    def test_no_typing_sound_still_clears_the_setting(self):
        """这张卡没填打字音 -> 显式清空(不然会**继承上一张卡**的音效)。"""
        tree = make_tree()
        event = self._ready(tree)
        dialog = tree.new_node(NN.GKNodeUiDialog, "第一句", gk_text="你好")
        gk_stub.connect(tree, event, "执行", dialog, "执行")
        code, _ = gen(tree, V3)
        self.assertIn('_gk_dialog_blip = ""', code)
        # 调用点没有配任何音效(helper 里那条分支是给"配了音效"用的)
        self.assertNotIn('_gk_dialog_blip = "res://', code)
        self.assertIn('if _gk_dialog_blip != "":', code)

    # ---- 对话历史 ----

    def test_dialog_line_goes_into_history_with_a_cap(self):
        tree = make_tree()
        event = self._ready(tree)
        dialog = tree.new_node(NN.GKNodeUiDialog, "第一句", gk_text="你好")
        gk_stub.connect(tree, event, "执行", dialog, "执行")
        code, _ = gen(tree, V3)
        self.assertIn('_gk_dialog_history.append({"who": speaker, "text": body})',
                      code)
        self.assertIn("if _gk_dialog_history.size() > 200:", code)
        self.assertIn("_gk_dialog_history.pop_front()", code)

    def test_history_node_blocks_until_click(self):
        tree = make_tree()
        event = self._ready(tree)
        hist = tree.new_node(NN.GKNodeDialogHistory, "回顾")
        gk_stub.connect(tree, event, "执行", hist, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('_gk_history_show(self, "对话回顾", 720.0, 380.0, 18, '
                      '"还没有说过话")', code)
        self.assertIn("while not _gk_history_clicked:", code)
        self.assertIn("_gk_history_hide()", code)
        # 自动滚动的列表 + 每句一行
        self.assertIn("func _gk_history_show(", code)
        self.assertIn("_gk_history_scroll = ScrollContainer.new()", code)
        self.assertIn("for entry in _gk_dialog_history:", code)
        self.assert_indent_sane(code)

    # ---- 真 Godot 抓到的:UI 根容器变量没声明 ----

    def test_ui_root_vars_declared_for_dialog_only_graph(self):
        """只放一个对话框的图,`_gk_ui_layer` / `_gk_ui_root` 也必须声明。

        ── 这条是怎么来的 ────────────────────────────────────────────
        这两个变量原来**只有**界面控件节点(`_ui_root()`)才声明。于是
        "一张图里只有对话框 / 字幕"的脚本生成出 `_gk_ui_layer` 的使用,
        却没有声明 —— 真 Godot:
            Parse Error: Identifier "_gk_ui_layer" not declared
        **整个脚本加载不了**。离线单测当时全绿(它们不检查"声明和引用
        配没配上"),是拿生成物去 `godot --check-only` 才现形的。
        """
        tree = make_tree()
        event = self._ready(tree)
        dialog = tree.new_node(NN.GKNodeUiDialog, "只有对话框", gk_text="你好")
        gk_stub.connect(tree, event, "执行", dialog, "执行")
        code, _ = gen(tree, V3)
        self.assertIn("var _gk_ui_layer: CanvasLayer", code)
        self.assertIn("var _gk_ui_root: Control", code)
        # 只声明一次(和界面控件节点共存时也不许重复)
        self.assertEqual(code.count("var _gk_ui_layer: CanvasLayer"), 1)

    def test_ui_root_vars_not_duplicated_with_widget_nodes(self):
        """对话框 + 界面控件一起用时,那两个变量还是只声明一次。"""
        tree = make_tree()
        event = self._ready(tree)
        text = tree.new_node(NN.GKNodeUiText, "提示", gk_text="hi")
        dialog = tree.new_node(NN.GKNodeUiDialog, "第一句", gk_text="你好")
        gk_stub.connect(tree, event, "执行", text, "执行")
        gk_stub.connect(tree, text, "完成", dialog, "执行")
        code, _ = gen(tree, V3)
        self.assertEqual(code.count("var _gk_ui_layer: CanvasLayer"), 1)
        self.assertEqual(code.count("var _gk_ui_root: Control"), 1)

    def test_subtitle_only_graph_declares_ui_root(self):
        tree = make_tree()
        event = self._ready(tree)
        sub = tree.new_node(NN.GKNodeUiSubtitle, "旁白", gk_text="甲")
        gk_stub.connect(tree, event, "执行", sub, "执行")
        code, _ = gen(tree, V3)
        self.assertIn("var _gk_ui_layer: CanvasLayer", code)
        self.assertIn("var _gk_ui_root: Control", code)

    # ---- 音频:循环 + 总线(第六十四轮)----

    def test_music_loops_by_stream_type(self):
        """『音效_音乐』默认循环 —— 而且要**按流类型**分别设。

        Godot 没有一个统一点:Ogg / MP3 是 `.loop`,wav 是 `loop_mode`。
        只设一种的话换个格式就"播一遍就没了",而且**不报错**(很难查)。
        """
        tree = make_tree()
        event = self._ready(tree)
        music = tree.new_node(NN.GKNodePlayMusic, "BGM",
                              gk_path="res://art/bgm.ogg")
        gk_stub.connect(tree, event, "执行", music, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("AudioStreamOggVorbis", code)
        self.assertIn("AudioStreamMP3", code)
        self.assertIn("AudioStreamWAV.LOOP_FORWARD", code)
        self.assertIn(".loop = true", code)
        self.assert_indent_sane(code)

    def test_music_loop_can_be_turned_off(self):
        tree = make_tree()
        event = self._ready(tree)
        music = tree.new_node(NN.GKNodePlayMusic, "BGM",
                              gk_path="res://art/once.ogg", gk_loop=False)
        gk_stub.connect(tree, event, "执行", music, "执行")
        code, _ = gen(tree, V3)
        self.assertNotIn("LOOP_FORWARD", code)
        self.assertNotIn(".loop = true", code)

    def test_music_bus_is_configurable(self):
        tree = make_tree()
        event = self._ready(tree)
        music = tree.new_node(NN.GKNodePlayMusic, "BGM",
                              gk_path="res://art/bgm.ogg", gk_bus="Music")
        gk_stub.connect(tree, event, "执行", music, "执行")
        code, _ = gen(tree, V3)
        self.assertIn('.bus = "Music"', code)
        # 没填就是 Master(和以前一样,不写死成别的)
        tree2 = make_tree()
        event2 = self._ready(tree2)
        music2 = tree2.new_node(NN.GKNodePlayMusic, "BGM2",
                                gk_path="res://art/bgm.ogg", gk_bus="")
        gk_stub.connect(tree2, event2, "执行", music2, "执行")
        code2, _ = gen(tree2, V3)
        self.assertIn('.bus = "Master"', code2)


class TestRound66AssetCard(unittest.TestCase):
    """第六十六轮:四张资产卡合成一张『资产卡』+ 卡片缩略图。

    ── 这一轮验什么 ────────────────────────────────────────────────
      * 统一卡读得出类型 / 名称 / 路径 / 勾选(纯函数,喂桩节点);
      * 勾掉『标记为游戏资产』的卡不算资产(统计 / 导出都跳过);
      * 旧四张卡仍然读得懂(老 .blend 不坏)—— 类型按 bl_idname 认;
      * `sound_card_entries` 统一卡里现算 `res://`(和复制进 audio/ 用
        的是同一个函数);
      * `asset_thumbs` 的纯函数(波形读 wav / 曲线投影 / 上下镜像)。
    """

    def _tree(self):
        return gk_stub.StubNodeTree("资产")

    def _card(self, tree, kind="model", path="", name="", is_asset=True):
        return tree.new_node(NN.GKNodeAssetCard, "卡", gk_kind=kind,
                             gk_path=path, gk_name=name, gk_is_asset=is_asset)

    # ---- 读卡(纯函数)----

    def test_card_kind_defaults_and_override(self):
        tree = self._tree()
        self.assertEqual(NN.card_kind(self._card(tree, "image")), "image")
        self.assertEqual(NN.card_kind(self._card(tree, "sound")), "sound")
        # 认不出来的类型 -> 空串(不是资产卡)
        self.assertEqual(NN.card_kind(self._card(tree, "nope")), "")
        # 旧卡按 bl_idname 认
        self.assertEqual(
            NN.card_kind(tree.new_node(NN.GKNodeAssetCurve, "旧")), "curve")
        self.assertEqual(
            NN.card_kind(tree.new_node(NN.GKNodeAssetModel, "旧")), "model")

    def test_card_path_and_name(self):
        tree = self._tree()
        card = self._card(tree, "model", "树")
        self.assertEqual(NN.card_path(card), "树")
        self.assertEqual(NN.card_name(card), "树")
        # 名称留空 = 用路径最后一段
        self.assertEqual(
            NN.card_name(self._card(tree, "sound", "/a/b/叮.wav")), "叮.wav")
        # 名称填了就用名称
        self.assertEqual(
            NN.card_name(self._card(tree, "sound", "/a/b/叮.wav", "开门")),
            "开门")
        # 旧卡走各自的属性
        old = tree.new_node(NN.GKNodeAssetImage, "旧", gk_image="头像")
        self.assertEqual(NN.card_path(old), "头像")

    def test_card_is_asset_honours_the_checkbox(self):
        tree = self._tree()
        self.assertTrue(NN.card_is_asset(self._card(tree, "model", "树")))
        self.assertFalse(
            NN.card_is_asset(self._card(tree, "model", "树", is_asset=False)))
        # 旧卡没有这个开关,一直算数
        self.assertTrue(NN.card_is_asset(
            tree.new_node(NN.GKNodeAssetModel, "旧", gk_asset="树")))

    def test_card_entries_and_counts_skip_unchecked(self):
        tree = self._tree()
        self._card(tree, "model", "树")
        self._card(tree, "image", "头像")
        self._card(tree, "sound", "a.wav", is_asset=False)
        self._card(tree, "curve", "", is_asset=True)      # 没路径 -> 不算
        entries = NN.card_entries(tree)
        self.assertEqual([e[0] for e in entries], ["model", "image"])
        counts = NN.card_counts(tree)
        self.assertEqual(counts["model"], 1)
        self.assertEqual(counts["image"], 1)
        self.assertEqual(counts["curve"], 0)
        self.assertEqual(counts["sound"], 0)
        # 卡节点还在(只是没勾/没路径)
        self.assertEqual(len(NN.card_nodes(tree)), 4)

    def test_legacy_cards_still_count(self):
        tree = self._tree()
        tree.new_node(NN.GKNodeAssetModel, "模型", gk_asset="树")
        tree.new_node(NN.GKNodeAssetSound, "声音",
                      gk_res="res://audio/b.wav")
        counts = NN.card_counts(tree)
        self.assertEqual(counts["model"], 1)
        self.assertEqual(counts["sound"], 1)

    # ---- 声音卡 -> 导出清单 ----

    def test_sound_card_entries_reads_unified_card(self):
        from BlenderGameKit import sound_assets
        tree = self._tree()
        self._card(tree, "sound", "/tmp/x/叮.wav", "开门声")
        entries = NN.sound_card_entries(tree)
        self.assertEqual(entries,
                         [("开门声", "/tmp/x/叮.wav",
                           sound_assets.sound_res_path("/tmp/x/叮.wav"))])

    def test_sound_card_entries_keeps_legacy_and_res_only(self):
        tree = self._tree()
        tree.new_node(NN.GKNodeAssetSound, "旧", gk_name="旧声",
                      gk_file="/tmp/old.wav", gk_res="res://audio/old.wav")
        # 路径本身就是 res:// -> 只登记,不复制
        self._card(tree, "sound", "res://audio/registered.ogg", "只登记")
        # 勾掉的卡不进清单
        self._card(tree, "sound", "/tmp/skip.wav", "勾掉了", is_asset=False)
        entries = NN.sound_card_entries(tree)
        self.assertIn(("旧声", "/tmp/old.wav", "res://audio/old.wav"), entries)
        self.assertIn(("只登记", "", "res://audio/registered.ogg"), entries)
        self.assertEqual(len(entries), 2)

    # ---- 缩略图的纯函数 ----

    def test_wave_preview_reads_a_real_wav(self):
        import wave

        from BlenderGameKit import asset_thumbs
        path = os.path.join(tempfile.gettempdir(), "gk_thumb66.wav")
        with wave.open(path, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            frames = bytearray()
            for index in range(800):
                value = 20000 if (index // 40) % 2 == 0 else 0
                frames += value.to_bytes(2, "little", signed=True)
            handle.writeframes(bytes(frames))
        peaks = asset_thumbs.wave_preview(path, samples=32)
        self.assertEqual(len(peaks), 32)
        self.assertTrue(all(0.0 <= value <= 1.0 for value in peaks))
        # 有响有静 -> 不是一条平线
        self.assertGreater(max(peaks) - min(peaks), 0.3)
        # 读不了的路径 -> 空(调用方退回色块)
        self.assertEqual(asset_thumbs.wave_preview("X:/没有.wav"), [])

    def test_project_points_keeps_the_two_widest_axes(self):
        from BlenderGameKit import asset_thumbs
        # 一条躺在 XY 平面上的线(Z 是常量)—— Z 轴该被丢掉
        points = [(0, 0, 5), (10, 7, 5), (20, 14, 5)]
        result = asset_thumbs.project_points(points)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], (0.0, 0.0))
        self.assertEqual(result[1], (0.5, 0.5))
        self.assertEqual(result[2], (1.0, 1.0))
        # 点太少 -> 空
        self.assertEqual(asset_thumbs.project_points([(0, 0, 0)]), [])

    def test_mirror_wave_is_symmetric(self):
        from BlenderGameKit import asset_thumbs
        mirrored = asset_thumbs.mirror_wave([0.0, 1.0])
        self.assertEqual(len(mirrored), 4)
        # 上下对称:后半段是前半段绕 0.5 的镜像
        self.assertAlmostEqual(mirrored[0][1], 1.0 - mirrored[-1][1], places=6)
        self.assertEqual(asset_thumbs.mirror_wave([]), [])

    def test_preview_for_unknown_kind_is_none(self):
        from BlenderGameKit import asset_thumbs
        self.assertIsNone(asset_thumbs.preview_for("nope", "x"))
        self.assertIsNone(asset_thumbs.preview_for("model", ""))


class TestRound67DisplaySwitch(Base):
    """第六十七轮:『显示_效果开关』—— 每张玩法图一个,运行时开/关本关的显示效果。

    用户原话:"添加开关按钮控制显示节点的开启与关闭,节点需绑定游戏逻辑
    (如不同关卡显示效果),且同一类节点仅允许添加一个"。
    动工前确认的三点:玩法图里的**开关节点**(不是物体显隐)、
    **接执行流**(游戏里可反复切)、**每张玩法图最多一个**(绑定靠"放在哪张图")。

    ── 它和『显示_设置可见』的分工 ───────────────────────────────────
    『显示_设置可见』管**某一个物体**:选目标 + 设 visible,一个个摆。
    这个管**本关的显示效果**(环境氛围 / 画面风格 / 调试面板)——
    一个节点把一关的效果一起开关,所以"每图一个"才有意义。
    """

    def _graph(self, **props):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        node = tree.new_node(NN.GKNodeDisplaySwitch, "显示开关", **props)
        gk_stub.connect(tree, event, "执行", node, "执行")
        return tree, node

    # ---- 节点本身 ----

    def test_node_registered_with_flow_pins(self):
        cls = getattr(NN, "GKNodeDisplaySwitch", None)
        self.assertIsNotNone(cls, "『显示_效果开关』没注册进节点表")
        self.assertEqual(cls.bl_label, "显示_效果开关")
        self.assertEqual(cls.gk_category, "显示")
        ins = [spec["name"] for spec in cls.gk_inputs]
        outs = [spec["name"] for spec in cls.gk_outputs]
        self.assertIn("执行", ins)
        self.assertIn("完成", outs)

    # ---- 三种效果一起开关 ----

    def test_toggles_all_three_effects(self):
        tree, _node = self._graph()
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        # helper 只有一份,调用点带上三个开关
        self.assertEqual(code.count("func _gk_display_effects("), 1)
        self.assertIn("_gk_display_effects(true, true, true)", code)
        # ① 环境氛围:主场景上的 WorldEnvironment
        self.assertIn("as WorldEnvironment", code)
        # ② 调试面板:场景根上的环境脚本(有这一项才动它)
        self.assertIn('set("debug_panel"', code)
        # ③ 画面风格:摘掉 / 还原卡通材质的覆盖
        self.assertIn("material_override", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_members_declared_once(self):
        tree, _node = self._graph()
        code, _ = gen(tree, V3)
        self.assertEqual(code.count("var _gk_env_backup"), 1)
        self.assertEqual(code.count("var _gk_toon_backup"), 1)

    def test_unchecked_effects_pass_false(self):
        tree, _node = self._graph(gk_toon_on=False, gk_debug_on=False)
        code, _ = gen(tree, V3)
        self.assertIn("_gk_display_effects(true, false, false)", code)

    def test_all_off_is_allowed(self):
        """三个都不勾 = 走到这里把本关的显示效果全关掉(不算错)。"""
        tree, _node = self._graph(gk_env_on=False, gk_toon_on=False,
                                  gk_debug_on=False)
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_display_effects(false, false, false)", code)

    # ---- 每张玩法图只允许一个 ----

    def test_two_switches_in_one_graph_warn(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        first = tree.new_node(NN.GKNodeDisplaySwitch, "开关A")
        second = tree.new_node(NN.GKNodeDisplaySwitch, "开关B")
        gk_stub.connect(tree, event, "执行", first, "执行")
        gk_stub.connect(tree, first, "完成", second, "执行")
        _code, warnings = gen(tree, V3)
        self.assertTrue(
            any("显示_效果开关" in w and "一个" in w for w in warnings), warnings)

    def test_one_switch_does_not_warn(self):
        tree, _node = self._graph()
        _code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)

    # ---- 不摆这个节点时,生成物一个字都不变(老图回归锁)----

    def test_graph_without_switch_is_untouched(self):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        printer = tree.new_node(NN.GKNodePrint, "打招呼", gk_text="hi")
        gk_stub.connect(tree, event, "执行", printer, "执行")
        code, _ = gen(tree, V3)
        self.assertNotIn("_gk_display_effects", code)
        self.assertNotIn("_gk_env_backup", code)


class TestRound68PackagingPlatforms(Base):
    """第六十八轮:侧栏『打包设置』—— 一次打出多个平台(.exe / .apk / …)。

    用户确认的三点(原话"1 全上;2 场景;3 追加"):
      * 平台全做:Windows / Linux / Web / Android;macOS **只能在 macOS 主机**
        上导出(Godot 的限制),界面上要明说,不能让人点了白等;
      * 设置存**场景**(和 `dimension` / `logic_tree` 一样随 .blend 走);
      * 项目里已经有 `export_presets.cfg` 时**追加**缺失的平台段(写前先备份),
        只增不改 —— 用户在 Godot 编辑器里调过的东西一个字都不能动。
    """

    # ---- 平台表 ----

    def test_platform_table_covers_five_with_required_fields(self):
        from BlenderGameKit import packaging
        self.assertEqual(list(packaging.PLATFORM_ORDER),
                         ["windows", "linux", "web", "android", "macos"])
        for key in packaging.PLATFORM_ORDER:
            spec = packaging.PLATFORMS[key]
            for field in ("label", "preset_name", "godot_platform", "ext",
                          "template", "template_ext", "template_tokens",
                          "hosts"):
                self.assertIn(field, spec, f"{key} 缺字段 {field}")

    def test_macos_only_buildable_on_macos(self):
        from BlenderGameKit import packaging
        self.assertTrue(packaging.platform_buildable("macos", "darwin"))
        self.assertFalse(packaging.platform_buildable("macos", "win32"))
        self.assertFalse(packaging.platform_buildable("macos", "linux"))
        # 其余平台在 Windows 上都能打
        for key in ("windows", "linux", "web", "android"):
            self.assertTrue(packaging.platform_buildable(key, "win32"))

    def test_selected_platforms_reads_scene_settings(self):
        """勾了哪些平台 = 读场景设置上的 5 个开关(纯 getattr,离线可测)。"""
        from BlenderGameKit import packaging
        settings = types.SimpleNamespace(package_windows=True, package_linux=False,
                                         package_web=True, package_android=True,
                                         package_macos=False)
        self.assertEqual(packaging.selected_platforms(settings),
                         ["windows", "web", "android"])
        # 老工程没这些字段 / 一个都没勾 -> 空表(交给调用方给提示,不瞎猜)
        self.assertEqual(packaging.selected_platforms(types.SimpleNamespace()), [])
        self.assertEqual(packaging.selected_platforms(None), [])

    # ---- 预设文本 ----

    def test_preset_text_per_platform(self):
        from BlenderGameKit import packaging
        for key in packaging.PLATFORM_ORDER:
            spec = packaging.PLATFORMS[key]
            text = packaging.platform_presets_text(
                key, title="我的游戏", preset_name="P", export_path="dist/x")
            self.assertIn("[preset.0]", text)
            self.assertIn(f'platform="{spec["godot_platform"]}"', text)
            self.assertIn('export_path="dist/x"', text)
            self.assertIn('application/product_name="我的游戏"', text)

    def test_android_preset_carries_package_name(self):
        """Android 导出**必须**有包名,否则 Godot 直接拒;版本号也写进去。"""
        from BlenderGameKit import packaging
        text = packaging.platform_presets_text(
            "android", title="我的游戏", preset_name="Android",
            export_path="dist/a.apk", android_id="com.example.mygame",
            version="1.2.3")
        self.assertIn('package/unique_name="com.example.mygame"', text)
        self.assertIn('version/name="1.2.3"', text)
        # 不走 gradle 构建:走它就要 Android SDK / gradle 那套,门槛太高
        self.assertIn("gradle_build/use_gradle_build=false", text)

    def test_windows_preset_still_embeds_pck(self):
        from BlenderGameKit import packaging
        text = packaging.export_presets_text(title="x", preset_name="P")
        self.assertIn("binary_format/embed_pck=true", text)

    # ---- 追加缺失平台(用户选"追加")----

    def _write_presets(self, folder, body):
        from BlenderGameKit import packaging
        path = os.path.join(folder, packaging.EXPORT_PRESETS_NAME)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
        return path

    def _read_text(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_append_adds_missing_platform_and_backs_up(self):
        from BlenderGameKit import packaging
        folder = tempfile.mkdtemp(prefix="gk_append68_")
        try:
            existing = ('[preset.0]\nname="我的预设"\n'
                        'platform="Windows Desktop"\n'
                        'export_path="dist/a.exe"\n\n'
                        '[preset.0.options]\n'
                        'application/product_name="我自己改的名字"\n')
            path = self._write_presets(folder, existing)
            added = packaging.append_missing_presets(
                folder, ["windows", "android"], title="game")
            self.assertEqual(added, ["android"])
            text = self._read_text(path)
            # 老内容一个字没动
            self.assertIn('name="我的预设"', text)
            self.assertIn('application/product_name="我自己改的名字"', text)
            # 缺的那段补在 [preset.1] 上
            self.assertIn("[preset.1]", text)
            self.assertIn('platform="Android"', text)
            # 写之前备份了一份原样
            self.assertTrue(os.path.isfile(path + ".bak"))
            self.assertEqual(self._read_text(path + ".bak"), existing)
            # windows 已经在了 -> 不重复加(全文里还是只有原来那一条)
            self.assertEqual(text.count('platform="Windows Desktop"'), 1)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_append_is_idempotent(self):
        from BlenderGameKit import packaging
        folder = tempfile.mkdtemp(prefix="gk_append68b_")
        try:
            first = packaging.append_missing_presets(
                folder, ["windows", "android"], title="g")
            self.assertEqual(first, ["windows", "android"])
            second = packaging.append_missing_presets(
                folder, ["windows", "android"], title="g")
            self.assertEqual(second, [])
            text = self._read_text(
                os.path.join(folder, packaging.EXPORT_PRESETS_NAME))
            self.assertEqual(text.count('platform="Android"'), 1)
            self.assertEqual(text.count('platform="Windows Desktop"'), 1)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_append_creates_file_without_backup_when_missing(self):
        from BlenderGameKit import packaging
        folder = tempfile.mkdtemp(prefix="gk_append68c_")
        try:
            added = packaging.append_missing_presets(
                folder, ["linux"], title="g")
            self.assertEqual(added, ["linux"])
            path = os.path.join(folder, packaging.EXPORT_PRESETS_NAME)
            self.assertTrue(os.path.isfile(path))
            # 本来就没有文件 -> 没东西可备份,不许留个空的 .bak
            self.assertFalse(os.path.isfile(path + ".bak"))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_read_preset_platforms(self):
        from BlenderGameKit import packaging
        folder = tempfile.mkdtemp(prefix="gk_plat68_")
        try:
            self.assertEqual(packaging.read_preset_platforms(folder), [])
            self._write_presets(folder, '[preset.0]\nname="A"\n'
                                        'platform="Windows Desktop"\n'
                                        '[preset.1]\nname="B"\n'
                                        'platform="Android"\n')
            self.assertEqual(packaging.read_preset_platforms(folder),
                             ["Windows Desktop", "Android"])
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_read_platform_preset_prefers_user_config(self):
        """已有预设里跟这个平台对得上的那条:名字 + 输出路径都听用户的。

        多平台之后不能再"读第一条" —— 文件里可能既有 Windows 又有 Android,
        拿错条就会用别人的预设名去导(命令行会报找不到预设),或者把产物
        丢到用户没预期的地方。
        """
        from BlenderGameKit import packaging
        folder = tempfile.mkdtemp(prefix="gk_plpreset68_")
        try:
            self.assertEqual(packaging.read_platform_preset(folder, "windows"),
                             (None, None))
            self._write_presets(folder,
                                '[preset.0]\nname="我的Windows"\n'
                                'platform="Windows Desktop"\n'
                                'export_path="out/win.exe"\n'
                                '[preset.0.options]\n'
                                'application/product_name="x"\n'
                                '[preset.1]\nname="安卓"\n'
                                'platform="Android"\n'
                                'export_path="out/a.apk"\n'
                                '[preset.1.options]\n'
                                'package/unique_name="com.a.b"\n')
            self.assertEqual(packaging.read_platform_preset(folder, "android"),
                             ("安卓", "out/a.apk"))
            self.assertEqual(packaging.read_platform_preset(folder, "windows"),
                             ("我的Windows", "out/win.exe"))
            # 没配过的平台 -> (None, None),由调用方用默认名 / 默认路径
            self.assertEqual(packaging.read_platform_preset(folder, "web"),
                             (None, None))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    # ---- 产物路径与模板文件 ----

    def test_output_path_per_platform(self):
        from BlenderGameKit import packaging
        self.assertTrue(packaging.platform_output_path(
            r"D:\proj", "我的游戏", "android").endswith(
                os.path.join("dist", "我的游戏.apk")))
        self.assertTrue(packaging.platform_output_path(
            r"D:\proj", "g", "linux").endswith(".x86_64"))
        self.assertTrue(packaging.platform_output_path(
            r"D:\proj", "g", "web").endswith(".zip"))

    def test_template_file_per_platform(self):
        """按平台认发布模板:先精确名,认不出再退回关键词(名字改过)。"""
        from BlenderGameKit import packaging
        root = tempfile.mkdtemp(prefix="gk_tpl68_")
        try:
            for name in ("windows_release_x86_64.exe", "windows_debug_x86_64.exe",
                         "linux_release.x86_64", "web_release.zip",
                         "web_dlink_release.zip", "android_release.apk",
                         "android_debug.apk", "macos.zip", "version.txt"):
                with open(os.path.join(root, name), "wb") as handle:
                    handle.write(b"x")
            self.assertEqual(packaging.platform_template_file("windows", root),
                             "windows_release_x86_64.exe")
            self.assertEqual(packaging.platform_template_file("linux", root),
                             "linux_release.x86_64")
            # web 有好几份(dlink / nothreads),要挑最"朴素"的那个
            self.assertEqual(packaging.platform_template_file("web", root),
                             "web_release.zip")
            self.assertEqual(packaging.platform_template_file("android", root),
                             "android_release.apk")
            self.assertEqual(packaging.platform_template_file("macos", root),
                             "macos.zip")
            # 精确名不在(Godot 换过名字)-> 退回关键词,且不挑 console / debug
            fallback = tempfile.mkdtemp(prefix="gk_tpl68b_")
            try:
                for name in ("windows_release_arm64.exe",
                             "windows_release_arm64_console.exe",
                             "windows_debug_arm64.exe"):
                    with open(os.path.join(fallback, name), "wb") as handle:
                        handle.write(b"x")
                self.assertEqual(
                    packaging.platform_template_file("windows", fallback),
                    "windows_release_arm64.exe")
            finally:
                shutil.rmtree(fallback, ignore_errors=True)
            # 一个都没有 -> None
            empty = tempfile.mkdtemp(prefix="gk_tpl68c_")
            try:
                self.assertIsNone(packaging.platform_template_file("web", empty))
            finally:
                shutil.rmtree(empty, ignore_errors=True)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_templates_status_names_the_platform(self):
        from BlenderGameKit import packaging
        root = tempfile.mkdtemp(prefix="gk_tpl68d_")
        try:
            version_dir = os.path.join(root, "4.7.2.stable")
            os.makedirs(version_dir, exist_ok=True)
            with open(os.path.join(version_dir, "android_release.apk"),
                      "wb") as handle:
                handle.write(b"x")
            with mock.patch.object(packaging, "export_templates_root",
                                   return_value=root):
                ready, _d, note = packaging.templates_status("4.7.2", "android")
                self.assertTrue(ready, note)
                # Windows 那份没装 -> 说明里要点名是哪个平台、缺哪个文件
                missing, _d2, miss = packaging.templates_status("4.7.2", "windows")
                self.assertFalse(missing)
                self.assertIn("Windows", miss)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestRound69AiNodes(Base):
    """第六十九轮:AI 图补三件 —— 『AI_开始』『AI_输出』『AI_逻辑判断』。

    用户原话:"游戏AI 需补充『开始』『输出』『逻辑判断』节点,对外输出布尔变量
    以实现玩法图分支判断"。动工前确认:**都在 AI 图里** + **『AI_输出』先做
    "是否处于某状态"的布尔**(条件布尔等真需要再加)。

    补的三个缺口:
      * 入口原来是"没有任何转场指到它"**猜**出来的 —— 循环图(每个状态都有
        入边)就没有入口。『AI_开始』把它变成显式的;
      * 玩法图原来只能『变量_读取』拿到状态**字符串**再自己比;
        『AI_输出』把"在不在某个状态"直接做成布尔变量;
      * 『AI_转场』只能挂**一个**条件,「看见 **且** 距离近」写不出来;
        『AI_逻辑判断』在 AI 图里把条件拼起来。
    """

    def _ai_tree(self, name="敌人AI"):
        ai = gk_stub.StubNodeTree(name)
        ai.bl_idname = "GKGameAiTree"
        return ai

    def _enable(self, ai_name="敌人AI"):
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        enable = tree.new_node(NN.GKNodeAiEnable, "启用AI", gk_target="玩家")
        gk_stub.connect(tree, event, "执行", enable, "执行")
        return tree

    def _gen_with_ai(self, ai, tree, options=None):
        """把 AI 图挂到 bpy.data 上再生成(用完还原,照界面那套)。"""
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[ai]))
        try:
            return gen(tree, options or V3)
        finally:
            if original is None:
                try:
                    delattr(bpy_module(), "data")
                except AttributeError:
                    pass
            else:
                setattr(bpy_module(), "data", original)

    # ---- 三个节点都注册好了,而且只进 AI 图 ----

    def test_nodes_registered_and_ai_tree_only(self):
        want = ("GKNodeAiStart", "GKNodeAiOutput", "GKNodeAiLogic")
        for blob in want:
            cls = getattr(NN, blob, None)
            self.assertIsNotNone(cls, f"{blob} 没注册")
            self.assertEqual(cls.gk_category, "AI", blob)
            self.assertEqual(str(getattr(cls, "gk_tree_type", "")), "GKGameAiTree",
                             f"{blob} 应该只属于 AI 图")
        self.assertEqual(NN.GKNodeAiStart.bl_label, "AI_开始")
        self.assertEqual(NN.GKNodeAiOutput.bl_label, "AI_输出")
        self.assertEqual(NN.GKNodeAiLogic.bl_label, "AI_逻辑判断")
        outs = [spec["name"] for spec in NN.GKNodeAiLogic.gk_outputs]
        self.assertIn("结果", outs)

    # ---- 『AI_开始』:把"从哪开始"变成显式的 ----

    def test_start_node_decides_the_entry(self):
        """循环图(每个状态都有入边)里,入口也该由『AI_开始』说了算。"""
        ai = self._ai_tree()
        patrol = ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                             gk_behavior="PATROL", gk_patrol_a="点A",
                             gk_patrol_b="点B", gk_speed=3.0,
                             gk_stop_distance=0.6)
        chase = ai.new_node(NN.GKNodeAiState, "追", gk_state_name="追",
                            gk_behavior="CHASE", gk_goal="玩家", gk_speed=4.0,
                            gk_stop_distance=1.0)
        # 两条互为条件的转场 -> 每个状态都有入边,老办法找不到入口
        gk_stub.connect(ai, patrol, "转到", chase, "进入")
        gk_stub.connect(ai, chase, "转到", patrol, "进入")
        start = ai.new_node(NN.GKNodeAiStart, "开始")
        gk_stub.connect(ai, start, "开始", chase, "进入")

        tree = self._enable()
        code, warnings = self._gen_with_ai(ai, tree)
        # 从『AI_开始』指的那个状态起手(不是随便挑一个)
        self.assertIn('ai_state = "追"', code, code[:400])
        self.assertFalse([w for w in warnings if "没有可用的状态" in w], warnings)

    def test_start_without_target_warns(self):
        ai = self._ai_tree()
        ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                    gk_behavior="PATROL", gk_patrol_a="点A", gk_patrol_b="点B",
                    gk_speed=3.0, gk_stop_distance=0.6)
        ai.new_node(NN.GKNodeAiStart, "开始")
        tree = self._enable()
        _code, warnings = self._gen_with_ai(ai, tree)
        self.assertTrue(any("AI_开始" in w and "接" in w for w in warnings), warnings)

    def test_multiple_start_nodes_warn(self):
        ai = self._ai_tree()
        state = ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                            gk_behavior="PATROL", gk_patrol_a="点A",
                            gk_patrol_b="点B", gk_speed=3.0, gk_stop_distance=0.6)
        first = ai.new_node(NN.GKNodeAiStart, "开始1")
        ai.new_node(NN.GKNodeAiStart, "开始2")
        gk_stub.connect(ai, first, "开始", state, "进入")
        tree = self._enable()
        _code, warnings = self._gen_with_ai(ai, tree)
        self.assertTrue(any("AI_开始" in w and "一个" in w for w in warnings),
                        warnings)

    # ---- 『AI_输出』:把"在不在某个状态"做成布尔变量 ----

    def test_output_declares_bool_and_updates_every_frame(self):
        ai = self._ai_tree()
        ai.new_node(NN.GKNodeAiState, "追", gk_state_name="追",
                    gk_behavior="CHASE", gk_goal="玩家", gk_speed=4.0,
                    gk_stop_distance=1.0)
        ai.new_node(NN.GKNodeAiOutput, "追击中", gk_state_name="追",
                    gk_var="is_chasing")
        tree = self._enable()
        code, warnings = self._gen_with_ai(ai, tree)
        self.assertEqual(warnings, [], warnings)
        # 声明成成员布尔(玩法图的『变量_读取』照名字读得到)
        self.assertIn("var is_chasing: bool = false", code)
        # 每个物理帧按当前状态重算
        self.assertIn('is_chasing = ai_state == "追"', code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_output_without_var_name_warns(self):
        ai = self._ai_tree()
        ai.new_node(NN.GKNodeAiState, "追", gk_state_name="追",
                    gk_behavior="CHASE", gk_goal="玩家", gk_speed=4.0,
                    gk_stop_distance=1.0)
        ai.new_node(NN.GKNodeAiOutput, "输出", gk_state_name="追", gk_var="")
        tree = self._enable()
        _code, warnings = self._gen_with_ai(ai, tree)
        self.assertTrue(any("AI_输出" in w and "变量名" in w for w in warnings),
                        warnings)

    def test_output_unknown_state_warns(self):
        ai = self._ai_tree()
        ai.new_node(NN.GKNodeAiState, "追", gk_state_name="追",
                    gk_behavior="CHASE", gk_goal="玩家", gk_speed=4.0,
                    gk_stop_distance=1.0)
        ai.new_node(NN.GKNodeAiOutput, "输出", gk_state_name="没这个状态",
                    gk_var="x_flag")
        tree = self._enable()
        _code, warnings = self._gen_with_ai(ai, tree)
        self.assertTrue(any("AI_输出" in w and "没这个状态" in w for w in warnings),
                        warnings)

    # ---- 『AI_逻辑判断』:在 AI 图里把条件拼起来 ----

    def test_logic_node_combines_two_conditions(self):
        ai = self._ai_tree()
        patrol = ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                             gk_behavior="PATROL", gk_patrol_a="点A",
                             gk_patrol_b="点B", gk_speed=3.0,
                             gk_stop_distance=0.6)
        chase = ai.new_node(NN.GKNodeAiState, "追", gk_state_name="追",
                            gk_behavior="CHASE", gk_goal="玩家", gk_speed=4.0,
                            gk_stop_distance=1.0)
        see = ai.new_node(NN.GKNodeAiSee, "近处看得见", gk_goal="玩家",
                          gk_distance=5.0, gk_angle=0.0)
        far = ai.new_node(NN.GKNodeAiSee, "远处也看得见", gk_goal="玩家",
                          gk_distance=15.0, gk_angle=360.0)
        logic = ai.new_node(NN.GKNodeAiLogic, "看见或听见", gk_op="OR")
        trans = ai.new_node(NN.GKNodeAiTransition, "转场", gk_cond_source="PIN")
        gk_stub.connect(ai, see, "看得见", logic, "A")
        gk_stub.connect(ai, far, "看得见", logic, "B")
        gk_stub.connect(ai, patrol, "转到", trans, "上游")
        gk_stub.connect(ai, logic, "结果", trans, "条件")
        gk_stub.connect(ai, trans, "转到", chase, "进入")

        tree = self._enable()
        code, warnings = self._gen_with_ai(ai, tree)
        self.assertFalse([w for w in warnings if "没接线" in w], warnings)
        # 与/或拼出来的条件出现在转场判定里
        self.assertIn(" or ", code)
        self.assert_indent_sane(code)

    # ---- 不摆新节点的老图:生成物一个字都不变 ----

    def test_old_graph_without_new_nodes_keeps_implicit_entry(self):
        ai = self._ai_tree()
        patrol = ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                             gk_behavior="PATROL", gk_patrol_a="点A",
                             gk_patrol_b="点B", gk_speed=3.0,
                             gk_stop_distance=0.6)
        chase = ai.new_node(NN.GKNodeAiState, "追", gk_state_name="追",
                            gk_behavior="CHASE", gk_goal="玩家", gk_speed=4.0,
                            gk_stop_distance=1.0)
        trans = ai.new_node(NN.GKNodeAiTransition, "转场", gk_cond_source="PIN")
        gk_stub.connect(ai, patrol, "转到", trans, "上游")
        gk_stub.connect(ai, trans, "转到", chase, "进入")
        tree = self._enable()
        code, warnings = self._gen_with_ai(ai, tree)
        # 没有任何『AI_开始』-> 还是老规矩:没人指向的那个状态当起点
        self.assertIn('ai_state = "巡逻"', code)
        self.assertNotIn("AI_开始", code)
        self.assertEqual([w for w in warnings if "AI_开始" in w], [], warnings)


class TestRound70TensionSeen(Base):
    """第七十轮(阶段④/⑤ 收尾)①:紧张度支持"被看见时更紧张"。

    『氛围_紧张度』原来只按"离威胁的距离"算;恐怖游戏里"被它看见了"比
    "离得近"更慌。加一个可选输入『被看见』(布尔)和『被看见下限』:
    为真时紧张度**至少抬到下限**(其余仍按距离)。不接引脚、也没勾 = 老行为
    一个字都不变。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def _heart(self, tree):
        event = self._ready(tree)
        heart = tree.new_node(NN.GKNodeHeartbeat, "心跳")
        gk_stub.connect(tree, event, "执行", heart, "执行")
        return heart

    def test_seen_pin_and_floor_exist(self):
        ins = [spec["name"] for spec in NN.GKNodeTension.gk_inputs]
        self.assertIn("被看见", ins)
        props = {entry[0]: entry for entry in NN.GKNodeTension.gk_prop_defs}
        self.assertIn("gk_seen_floor", props)
        self.assertEqual(props["gk_seen_floor"][2], 0.8)

    def test_seen_linked_picks_the_higher_of_distance_and_floor(self):
        tree = make_tree()
        heart = self._heart(tree)
        tension = tree.new_node(NN.GKNodeTension, "紧张", gk_threat="怪物",
                                gk_near=3.0, gk_far=15.0, gk_seen_floor=0.8)
        spotted = tree.new_node(NN.GKNodeCompare, "被发现了吗")
        gk_stub.connect(tree, spotted, "结果", tension, "被看见")
        gk_stub.connect(tree, tension, "紧张度", heart, "紧张度")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        # 距离算出来的值再和下限取大(下限随『被看见』这个布尔现算)
        self.assertIn("maxf(clampf(", code)
        self.assertIn("0.8", code)
        self.assertIn(" else 0.0", code)      # 三元:看见才抬到下限
        self.assert_indent_sane(code)

    def test_seen_toggled_on_socket_uses_floor(self):
        tree = make_tree()
        heart = self._heart(tree)
        tension = tree.new_node(NN.GKNodeTension, "紧张", gk_threat="怪物",
                                gk_seen_floor=0.9)
        tension.inputs.get("被看见").default_value = True
        gk_stub.connect(tree, tension, "紧张度", heart, "紧张度")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("maxf(clampf(", code)
        self.assertIn("0.9", code)
        self.assertNotIn(" else 0.0", code)   # 固定下限,不需要三元

    def test_floor_zero_is_ignored(self):
        """下限填 0 = 不用这个功能 —— 生成物回到纯距离那一版。"""
        tree = make_tree()
        heart = self._heart(tree)
        tension = tree.new_node(NN.GKNodeTension, "紧张", gk_threat="怪物",
                                gk_seen_floor=0.0)
        tension.inputs.get("被看见").default_value = True
        gk_stub.connect(tree, tension, "紧张度", heart, "紧张度")
        code, _warnings = gen(tree, V3)
        self.assertNotIn("maxf(clampf(", code)
        self.assertIn("_gk_heart_tension += (clampf(", code)

    def test_seen_off_keeps_old_output(self):
        """没接『被看见』、也没勾 -> 生成物和以前一模一样,不多一个字符。"""
        tree = make_tree()
        heart = self._heart(tree)
        tension = tree.new_node(NN.GKNodeTension, "紧张", gk_threat="怪物",
                                gk_near=3.0, gk_far=15.0)
        gk_stub.connect(tree, tension, "紧张度", heart, "紧张度")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_heart_tension += (clampf(", code)
        self.assertNotIn("maxf(clampf(", code)
        self.assertNotIn(" else 0.0", code)


class TestRound70NoGodotWarnings(Base):
    """第七十轮:把用户截图里那几条**生成代码自己招来的 Godot 告警**修掉。

    用户报"放完曲线动画后卡住",顺手截了 Godot 调试器的 10 条记录,其中三条
    是生成器写出来的、Godot 判定为问题的东西:

      * `The local variable "basis" is shadowing an already-declared property
        in the base class "Node3D"` —— 兜底飞行机里 `var basis := ...basis`;
      * `The function parameter "base" has the same name as a built-in
        function` —— 动画层播放器 `_gk_layer_player(base: Node, ...)`;
      * `Integer used when an enum value is expected` —— `_gk_ease` 把 int
        成员直接喂给要枚举的 `Tween.interpolate_value`。
    """

    def test_fly_camera_does_not_shadow_basis(self):
        """兜底飞行机的局部变量不能叫 `basis`(Node3D 有同名属性)。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        branch = tree.new_node(NN.GKNodeBranch, "如果")
        status = tree.new_node(NN.GKNodeCameraStatus, "相机状态")
        gk_stub.connect(tree, event, "执行", branch, "执行")
        gk_stub.connect(tree, status, "开着吗", branch, "条件")
        code, _warnings = gen(tree, V3)
        self.assertIn("_gk_fly_cam", code)
        self.assertIn("var _gk_basis := ", code)
        self.assertNotIn("var basis := ", code)

    def test_ease_casts_int_to_enum(self):
        """`_gk_ease` 把 int 显式转枚举(否则 Godot 报"该给枚举值")。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        switch = tree.new_node(NN.GKNodeCameraSetCurrent, "推镜",
                               gk_target="特写机位", gk_blend=0.8,
                               gk_blend_trans="EASE_OUT")
        gk_stub.connect(tree, event, "执行", switch, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("trans as Tween.TransitionType", code)
        self.assertIn("ease as Tween.EaseType", code)
        self.assert_indent_sane(code)


class TestRound70ReadyAwaitOrder(Base):
    """第七十轮:『每当_准备』链里有『等待_秒』时,**触发器连接不能被它拖后**。

    真机上踩到:示例给开场运镜后面接了个『等待_秒』,生成的 `_ready()` 是

        func _ready() -> void:
            ...开场运镜...
            await get_tree().create_timer(4.0).timeout     # 等特写演完
            ...切回第三人称...
            if area_纸条1 != null ...:                      # ← 排在 await 后面
                area_纸条1.body_entered.connect(_gk_on_area_entered_4)

    于是开局那 4 秒里**所有触发区都不响应**(纸条拾不起来、计数一直 0)。
    修法:信号连接插到 `_ready` 最前面。
    """

    def test_trigger_connect_comes_before_await(self):
        tree = make_tree()
        ready = tree.new_node(NN.GKNodeOnReady, "准备")
        wait = tree.new_node(NN.GKNodeWait, "等一下")
        wait.inputs.get("秒").default_value = 4.0
        gk_stub.connect(tree, ready, "执行", wait, "执行")
        touch = tree.new_node(NN.GKNodeOnAreaEntered, "碰到纸条",
                              gk_area="纸条1", gk_filter="玩家")
        tree.new_node(NN.GKNodePrint, "提示", gk_text="拾到一张纸条……")
        printer = None
        for node in tree.nodes:
            if node.bl_idname == "GKNodePrint":
                printer = node
        gk_stub.connect(tree, touch, "执行", printer, "执行")

        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("await get_tree().create_timer(4.0).timeout", code)
        self.assertIn("body_entered.connect(", code)
        self.assertLess(code.index("body_entered.connect("),
                        code.index("await get_tree().create_timer("),
                        "触发器的信号连接必须排在 `await` 之前")
        self.assert_indent_sane(code)


class TestRound72BlendSpaces(Base):
    """第七十二轮(G 组):2D 混合树 + 混合点解析。

    『动画_混合树』(1D)按一个参数混走↔跑;2D 那版用 X / Y 两个参数当平面
    坐标(UE 的 Blend Space 2D),八个方向的走跑过渡都靠它。
    验收线两条:2D 生成的是 `AnimationNodeBlendSpace2D` + Vector2 参数,
    而**只用 1D 的老图生成物一字不变**(所以 2D 的辅助函数是单独一份)。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_parse_points_2d(self):
        from BlenderGameKit import codegen
        points = codegen.parse_blend_points_2d("待机:0:0|前走:0:1|右走:1:0")
        self.assertEqual(points, [("待机", 0.0, 0.0, 1.0),
                                  ("前走", 0.0, 1.0, 1.0),
                                  ("右走", 1.0, 0.0, 1.0)])
        # 第四个数 = 这个点自己的速度(第七十三轮)
        self.assertEqual(codegen.parse_blend_points_2d("走:0:1:2.5"),
                         [("走", 0.0, 1.0, 2.5)])
        # 坏数据不抛异常:Y 缺了当 0,位置缺了按序号补
        loose = codegen.parse_blend_points_2d("甲|乙:2")
        self.assertEqual(loose, [("甲", 0.0, 0.0, 1.0), ("乙", 2.0, 0.0, 1.0)])
        self.assertEqual(codegen.parse_blend_points_2d(""), [])

    def test_points_round_trip(self):
        """对话框写回用的拼串,和解析**同一套规则**(不然界面一按就变形)。

        界面(`gk.edit_blend_points`)读用 `parse_blend_points*`、写用
        `format_blend_points` —— 这里锁住"读一遍再写一遍还是原样"。
        """
        from BlenderGameKit import codegen
        one_d = "走:0|慢跑:1|冲刺:2"
        self.assertEqual(
            codegen.format_blend_points(
                codegen.parse_blend_points(one_d), dim=1), one_d)
        two_d = "待机:0:0|前走:0:1|右走:1:0"
        self.assertEqual(
            codegen.format_blend_points(
                codegen.parse_blend_points_2d(two_d), dim=2), two_d)
        # 整数位置别写成 0.0 —— 保持读起来干净
        self.assertEqual(codegen.format_blend_points([("走", 0.0)], dim=1), "走:0")

    def test_node_registered_with_two_axes(self):
        cls = getattr(NN, "GKNodeBlendTree2D", None)
        self.assertIsNotNone(cls, "『动画_混合树2D』没注册")
        self.assertEqual(cls.bl_label, "动画_混合树2D")
        self.assertEqual(cls.gk_category, "动画")
        pins = [spec["name"] for spec in cls.gk_inputs]
        self.assertIn("X", pins)
        self.assertIn("Y", pins)

    def test_generates_blend_space_2d(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeBlendTree2D, "八向混合",
                             gk_points="待机:0:0|前走:0:1|右走:1:0")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _gk_ensure_blend_tree_2d(", code)
        self.assertIn("AnimationNodeBlendSpace2D.new()", code)
        self.assertIn("space.add_blend_point(anim_node, Vector2(", code)
        # 每帧推的是 Vector2(BlendSpace2D 的 blend_position 就是二维的)
        self.assertIn('set("parameters/blend_position", Vector2(', code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_two_points_warns(self):
        """少于三个点围不成面 —— 要说得出话,而不是生成一棵怪树。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeBlendTree2D, "两个点",
                             gk_points="待机:0:0|前走:0:1")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V3)
        self.assertTrue(any("至少" in w and "三个" in w for w in warnings), warnings)
        self.assertNotIn("AnimationNodeBlendSpace2D", code)

    def test_smooth_uses_vector_lerp(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeBlendTree2D, "八向混合",
                             gk_points="甲:0:0|乙:1:0|丙:0:1", gk_smooth=0.3)
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn(".lerp(Vector2(", code)
        self.assertIn("/ maxf(0.3, 0.0001)", code)

    def test_1d_output_untouched(self):
        """只用 1D 的老图:生成物里不许出现 2D 那份 helper / BlendSpace2D。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeBlendTree, "走跑", gk_points="走:0|跑:1")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_ensure_blend_tree(self,", code)
        self.assertNotIn("_gk_ensure_blend_tree_2d", code)
        self.assertNotIn("AnimationNodeBlendSpace2D", code)
        self.assertIn("AnimationNodeBlendSpace1D.new()", code)


class TestRound73PointSpeed(Base):
    """第七十三轮(G 组):**每个混合点自己播多快**。

    ⚠ 实现走的是 `AnimationNodeAnimation.use_custom_timeline` +
      `timeline_length = 原长 / 速度`,**不是** `AnimationNodeTimeScale` ——
      后者的 scale 是"参数"而不是属性,而混合点里的子节点没有可寻址的名字
      (真机实测:它的属性列表是空的)。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_speed_field_round_trip(self):
        from BlenderGameKit import codegen
        # 速度 = 1 时不写那一段:老文本改个位置不该平白多出一串 :1
        self.assertEqual(
            codegen.format_blend_points([("走", 0.0, 1.0)], dim=1), "走:0")
        self.assertEqual(
            codegen.format_blend_points([("走", 0.0, 1.6)], dim=1), "走:0:1.6")
        self.assertEqual(
            codegen.format_blend_points([("待机", 0.0, 0.0, 1.0)], dim=2),
            "待机:0:0")
        self.assertEqual(
            codegen.format_blend_points([("待机", 0.0, 0.0, 0.5)], dim=2),
            "待机:0:0:0.5")
        text = "走:0|跑:1:1.5"
        self.assertEqual(
            codegen.format_blend_points(codegen.parse_blend_points(text)), text)

    def test_speed_generates_custom_timeline(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeBlendTree, "走跑", gk_points="走:0|跑:1:1.5")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("use_custom_timeline = true", code)
        self.assertIn("timeline_length = _src.length / float(entry[2])", code)
        self.assertIn("stretch_time_scale = true", code)
        # ⚠ 真机探针:走了自定义时间轴,**循环得看节点自己的 loop_mode** ——
        #   不补这一行,2 倍速那段播到 0.5 秒就冻在最后一帧不动了。
        self.assertIn("anim_node.loop_mode = Animation.LOOP_LINEAR", code)
        # 带速度那一列的点和不带速度的点在同一个字面量里
        self.assertIn('["走", 0.0, 1.0]', code)
        self.assertIn('["跑", 1.0, 1.5]', code)

    def test_without_speed_literal_stays_two_fields(self):
        """没写速度 -> 字面量还是两段式(老图产物逐字不变)。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeBlendTree, "走跑", gk_points="走:0|跑:1")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('[["走", 0.0], ["跑", 1.0]]', code)
        # helper 里那段是"有速度才生效"的守卫,不写速度时是空操作
        self.assertIn("if entry.size() > 2 and float(entry[2]) != 1.0:", code)

    def test_2d_speed_uses_fourth_field(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeBlendTree2D, "八向",
                             gk_points="甲:0:0|乙:1:0|丙:0:1:2.0")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('["丙", 0.0, 1.0, 2.0]', code)
        self.assertIn("timeline_length = _src.length / float(entry[3])", code)


class TestRound73OverlayLayer(Base):
    """第七十三轮(G 组):**受击叠层** —— 一次性动作叠在走动之上。

    受击 / 挥砍这种动作要"叠"在走路之上(走路不中断),播完自己收回去。

    ── 结构(真 Godot 4.7.2 探针实测出来的一版)────────────────────
        BlendTree ─ base    = BlendSpace 1D / 2D(混合点那套)
                    shot    = AnimationNodeAnimation(叠层动画)
                    oneshot = AnimationNodeOneShot
                                 输入 0 "in"   ← base
                                 输入 1 "shot" ← shot
                    output ← oneshot

    ⚠ 两个坑都是探针抓出来的:
      1. `AnimationNodeOneShot` 的两个输入按名字是 `in`(0)/`shot`(1)。
         第一版把叠层动画接到 0 上(以为 0 是"那一段"),触发后画面纹丝
         不动(`during=0.14`,和基准一样)。
      2. `output` 节点**只吃一个输入** —— 官网上"把 oneshot 接到 output 的
         0 / 1 两个输入"那版接不上,所以第一版绕了一圈显式 `Blend2` +
         推 `blend_amount`。其实 `oneshot` 自己就会"没触发透传 in、触发混进
         shot、播完按 fadeout 退回 in",那层管子不需要。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def _blend(self, tree, event, points="走:0|跑:1", overlay=None):
        """放一个混合树并接上执行线;overlay 非空 = 配了叠层动画。"""
        kwargs = {"gk_points": points}
        if overlay is not None:
            kwargs["gk_overlay"] = overlay
        node = tree.new_node(NN.GKNodeBlendTree, "走跑", **kwargs)
        gk_stub.connect(tree, event, "执行", node, "执行")
        return node

    def test_node_registered(self):
        cls = getattr(NN, "GKNodeAnimOverlay", None)
        self.assertIsNotNone(cls, "『动画_叠层』没注册")
        self.assertEqual(cls.bl_label, "动画_叠层")
        self.assertEqual(cls.gk_category, "动画")
        self.assertEqual([spec["name"] for spec in cls.gk_inputs], ["执行"])
        self.assertEqual([spec["name"] for spec in cls.gk_outputs], ["完成"])

    def test_overlay_tree_structure(self):
        tree = make_tree()
        event = self._ready(tree)
        self._blend(tree, event, overlay="受击")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _gk_ensure_blend_tree_layer(", code)
        self.assertIn("AnimationNodeOneShot.new()", code)
        # oneshot 的 0 是 "in"(基准)、1 是 "shot"(叠层);output 只吃一个输入
        self.assertIn('bt.connect_node("oneshot", 0, "base")', code)
        self.assertIn('bt.connect_node("oneshot", 1, "shot")', code)
        self.assertIn('bt.connect_node("output", 0, "oneshot")', code)
        # 不再绕 Blend2 / 推 blend_amount(oneshot 自己会退回去)
        self.assertNotIn("AnimationNodeBlend2", code)
        self.assertNotIn("blend_amount", code)
        # 叠层那段必须"播一次":循环的话 OneShot 永远等不到它结束,
        # 人会一直僵在叠层姿势上(真机实测)
        self.assertIn("shot_anim.loop_mode = Animation.LOOP_NONE", code)
        # 空间成了子节点 -> 参数路径多一层 base/
        self.assertIn('set("parameters/base/blend_position"', code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_fire_plays_overlay(self):
        tree = make_tree()
        event = self._ready(tree)
        self._blend(tree, event, overlay="受击")
        fire = tree.new_node(NN.GKNodeAnimOverlay, "挨打", gk_action="FIRE",
                             gk_hold=0.4, gk_tree_index=0)
        gk_stub.connect(tree, event, "执行", fire, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _gk_overlay_fire(", code)
        self.assertIn("_gk_overlay_fire(blend_tree, 0.4)", code)
        self.assertIn("ONE_SHOT_REQUEST_FIRE", code)
        # 『保持秒数』不是 0 -> 到点强制收起
        self.assertIn("create_timer(hold)", code)
        # 收尾**不是协程**(用 SceneTreeTimer + lambda):不 await 的协程
        # 会让 Godot 打一条"coroutine called without await"的告警。
        self.assertNotIn("await ", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_hold_zero_lets_it_finish(self):
        """『保持秒数』= 0:不排定时器,让那段动画自己播完再退回去。"""
        tree = make_tree()
        event = self._ready(tree)
        self._blend(tree, event, overlay="受击")
        fire = tree.new_node(NN.GKNodeAnimOverlay, "挨打", gk_action="FIRE")
        gk_stub.connect(tree, event, "执行", fire, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_overlay_fire(blend_tree, 0.0)", code)
        self.assertIn("if hold <= 0.0:", code)

    def test_stop_aborts_overlay(self):
        tree = make_tree()
        event = self._ready(tree)
        self._blend(tree, event, overlay="受击")
        stop = tree.new_node(NN.GKNodeAnimOverlay, "收手", gk_action="STOP")
        gk_stub.connect(tree, event, "执行", stop, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_overlay_stop(blend_tree)", code)
        self.assertIn("ONE_SHOT_REQUEST_ABORT", code)
        # 『立刻收起』不该顺手放一遍(比的是**调用**,函数定义本来就一起带上)
        self.assertNotIn("_gk_overlay_fire(blend_tree", code)

    def test_warns_without_tree(self):
        """图里根本没有混合树 —— 要说得出话,而不是生成一处空引用。"""
        tree = make_tree()
        event = self._ready(tree)
        fire = tree.new_node(NN.GKNodeAnimOverlay, "挨打", gk_action="FIRE")
        gk_stub.connect(tree, event, "执行", fire, "执行")
        code, warnings = gen(tree, V3)
        self.assertTrue(any("找不到" in w and "混合树" in w for w in warnings),
                        warnings)
        # 告警了就连那两个小函数都不该生成
        self.assertNotIn("func _gk_overlay_fire(", code)

    def test_warns_when_overlay_not_configured(self):
        """树在,但那张卡片没填『叠层动画』—— 叠层建不起来,要说清楚。"""
        tree = make_tree()
        event = self._ready(tree)
        self._blend(tree, event)  # 没配 overlay
        fire = tree.new_node(NN.GKNodeAnimOverlay, "挨打", gk_action="FIRE")
        gk_stub.connect(tree, event, "执行", fire, "执行")
        code, warnings = gen(tree, V3)
        self.assertTrue(any("叠层动画" in w for w in warnings), warnings)
        self.assertNotIn("func _gk_overlay_fire(", code)

    def test_without_overlay_keeps_old_output(self):
        """没配叠层的老图:用的还是原来那份 helper,参数路径也不多一层。

        这是本项目的验收线 —— 老 .blend 重新导出,生成物要逐字不变。
        """
        tree = make_tree()
        event = self._ready(tree)
        self._blend(tree, event)
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_ensure_blend_tree(self,", code)
        self.assertNotIn("_gk_ensure_blend_tree_layer", code)
        self.assertNotIn("AnimationNodeOneShot", code)
        # 参数路径**不带** base/(带引号比对,免得被 base/ 那串蹭到)
        self.assertIn('set("parameters/blend_position"', code)
        self.assertNotIn('set("parameters/base/', code)


class TestRound74NamedBlendPoints(Base):
    """第七十四轮:给混合点起名字,消掉 Godot 4.7 的弃用警告。

    Godot 4.7 给混合点加了"名字";不传的话每次建树都会在输出面板打一条

        AnimationNodeBlendSpace1D::add_blend_point: No name provided,
        using safe index as reference. In the future, empty names will be
        deprecated, so explicitly passing a name is recommended.

    满屏都是,用户会以为是插件生成的代码有问题。

    `add_blend_point` 的**第 4 个参数就是名字**(真机探针读的方法签名:
    `add_blend_point(node, pos, at_index, name)`,name 是 StringName)——
    所以不用再补一句 `set_blend_point_name`,一次调用就把名字带上了。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def test_1d_points_are_named(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeBlendTree, "走跑", gk_points="走:0|跑:1")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("AnimationNodeBlendSpace1D.new()", code)
        self.assertIn("space.add_blend_point(anim_node, float(entry[1]), "
                      "-1, StringName(entry[0]))", code)

    def test_2d_points_are_named(self):
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeBlendTree2D, "八向",
                             gk_points="甲:0:0|乙:1:0|丙:0:1")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("AnimationNodeBlendSpace2D.new()", code)
        self.assertIn("float(entry[2])), -1, StringName(entry[0]))", code)

    def test_layered_helper_names_points_on_both_axes(self):
        """带叠层那份 helper 里 1D / 2D 两条分支都要带上名字。"""
        tree = make_tree()
        event = self._ready(tree)
        node = tree.new_node(NN.GKNodeBlendTree, "走跑", gk_points="走:0|跑:1",
                             gk_overlay="受击")
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _gk_ensure_blend_tree_layer(", code)
        self.assertEqual(code.count("-1, StringName(entry[0]))"), 2)


class TestRound75TransitionBlend(Base):
    """第七十五轮(G 组 21 的前一半):**切状态时的过渡时长**(交叉淡化)。

    现在切状态是**硬切** —— 统一走 `_gk_anim_enter(next_state)`,里面就一句
    `play(next_state)`,走↔跑之间是瞬间换、没有过渡。

    ── 填法(用户 2026-10-09 拍板:两处都要)────────────────────────
      * **状态上**填『过渡时长』= 兜底(任何方式切进这个状态都用它)
      * **转场上**填『过渡时长』= 覆盖目标状态上的(0 = 用状态的)

    默认 0 = 硬切,所以老图(谁都没填)生成物**一字不变**:进入函数的签名
    还是 `(next_state: String)`,一句 `blend` 都不多。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def _run(self, states, edges, state_blends=None, state_speeds=None,
             edge_blends=None):
        """搭一张最小的动画树:states 第一个当起始;edges 写 (从, 到, 条件)。

        条件填 `None` = 状态直连(播完就切),没有转场节点;
        其它值 = 『条件来源』(比如 ACTION_PRESSED)。
        """
        anim = gk_stub.StubNodeTree("角色动画")
        anim.bl_idname = "GKAnimationTree"
        made = {}
        for name in states:
            node = anim.new_node(NN.GKNodeAnimState, "状态" + name)
            node.gk_state_name = name
            node.gk_blend_time = float((state_blends or {}).get(name, 0.0))
            node.gk_speed = float((state_speeds or {}).get(name, 1.0))
            made[name] = node
        for number, (src, dst, cond) in enumerate(edges):
            if cond is None:
                # 直连 = 播完就切(没有转场节点)
                gk_stub.connect(anim, made[src], "转到", made[dst], "进入")
                continue
            trans = anim.new_node(NN.GKNodeAnimTransition, f"T{number}")
            gk_stub.connect(anim, made[src], "转到", trans, "上游")
            gk_stub.connect(anim, trans, "转到", made[dst], "进入")
            trans.gk_cond_source = cond
            trans.gk_blend_time = float((edge_blends or {}).get((src, dst), 0.0))
        logic = make_tree()
        event = self._ready(logic)
        ref = logic.new_node(NN.GKNodeAnimStateMachineRef, "状态机")
        gk_stub.connect(logic, event, "执行", ref, "执行")
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[anim]))
        try:
            return gen(logic, V3)
        finally:
            detach_anim_tree(original)

    def test_both_nodes_declare_blend_time(self):
        """『过渡时长』必须真的声明在节点上(否则卡片画不出来)。"""
        state_props = {entry[0] for entry in NN.GKNodeAnimState.gk_prop_defs}
        trans_props = {entry[0] for entry in NN.GKNodeAnimTransition.gk_prop_defs}
        self.assertIn("gk_blend_time", state_props)
        self.assertIn("gk_blend_time", trans_props)

    def test_state_blend_adds_param_and_value(self):
        code, warnings = self._run(
            ["idle", "run"], [("idle", "run", "ACTION_PRESSED")],
            state_blends={"run": 0.2})
        self.assertEqual(warnings, [], warnings)
        # 进入函数多一个带默认值的 blend 参数
        self.assertIn("func _gk_anim_enter(next_state: String, "
                      "blend: float = -1.0) -> void:", code)
        # 切过去时把秒数带上
        self.assertIn('_gk_anim_enter("run", 0.2)', code)
        self.assertIn(".play(next_state, blend)", code)
        # 起始状态在 _ready 里播 —— 没有"上一个动作"可混,不塞秒数
        self.assertIn('_gk_anim_enter("idle")', code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_transition_overrides_state(self):
        code, warnings = self._run(
            ["idle", "run"], [("idle", "run", "ACTION_PRESSED")],
            state_blends={"run": 0.2},
            edge_blends={("idle", "run"): 0.05})
        self.assertEqual(warnings, [], warnings)
        self.assertIn('_gk_anim_enter("run", 0.05)', code)
        self.assertNotIn('_gk_anim_enter("run", 0.2)', code)

    def test_direct_edge_uses_target_state(self):
        """直连边(播完就切)没有转场节点 -> 用目标状态上那个兜底值。"""
        code, warnings = self._run(
            ["idle", "run"], [("idle", "run", None)],
            state_blends={"run": 0.3})
        self.assertEqual(warnings, [], warnings)
        self.assertIn('_gk_anim_enter("run", 0.3)', code)

    def test_no_blend_keeps_old_output(self):
        """谁都没填 -> 生成物一字不变(签名不带参数、play 不带 blend)。"""
        code, warnings = self._run(
            ["idle", "run"], [("idle", "run", "ACTION_PRESSED")])
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _gk_anim_enter(next_state: String) -> void:", code)
        self.assertNotIn("blend: float", code)
        self.assertNotIn(".play(next_state, blend", code)
        self.assertIn('_gk_anim_enter("run")', code)
        self.assertIn(".play(next_state)", code)

    def test_blend_and_speed_together(self):
        """过渡时长和播放速度并存时,play 的三段参数都得对。"""
        code, warnings = self._run(
            ["idle", "run"], [("idle", "run", "ACTION_PRESSED")],
            state_blends={"run": 0.2}, state_speeds={"run": 1.5})
        self.assertEqual(warnings, [], warnings)
        self.assertIn(".play(next_state, blend, _gk_anim_speed(next_state))", code)


class TestRound76AnimEvents(Base):
    """第七十六轮(G 组 21b):**动画事件** —— 播到某一秒时触发一次。

    典型用法:攻击动画播到 0.4 秒 -> 生成判定框 / 播音效;脚步动画播到
    0.1 秒 -> 脚步音;开门动画播到 0.8 秒 -> "咔哒"一声。

    ── 机制(用户 2026-10-09 拍板)──────────────────────────────────
      * 一个事件一个**节点**『动画_事件』,填『动画名 + 第几秒』;
      * 接在执行流后面(和『动画_叠层』一样),靠**每帧查**动画进度判定 ——
        Godot 的 AnimationPlayer 没有"播到第 N 秒"这种信号;
      * 一段动画只响**一次**;同一段动画**重播**(进度回到 0 又往前)会再响;
        循环动画**每圈响一次**(脚步音就是这么用的);
      * 出口两个:『触发时』接要做的事,『完成』用来把多个事件串成一条线。
    """

    def _build(self, anim_name="攻击", seconds=0.4, chained=None,
               wire_trigger=True):
        tree = make_tree()
        physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧", gk_order=0)
        nodes = [tree.new_node(NN.GKNodeAnimEvent, f"事件{i}",
                               gk_anim_name=name, gk_event_time=time)
                 for i, (name, time) in enumerate(
                     chained or [(anim_name, seconds)])]
        gk_stub.connect(tree, physics, "执行", nodes[0], "执行")
        for prev, nxt in zip(nodes, nodes[1:]):
            gk_stub.connect(tree, prev, "完成", nxt, "执行")
        for index, node in enumerate(nodes):
            hit = tree.new_node(NN.GKNodePrint, f"触发{index}", gk_text="触发了")
            if wire_trigger:
                gk_stub.connect(tree, node, "触发时", hit, "执行")
        return gen(tree, V3)

    def test_node_registered_in_play_group(self):
        cls = getattr(NN, "GKNodeAnimEvent", None)
        self.assertIsNotNone(cls, "『动画_事件』没注册")
        self.assertEqual(cls.bl_label, "动画_事件")
        self.assertEqual(cls.gk_category, "动画")
        self.assertEqual(NN.SUBCATEGORY_OF.get("GKNodeAnimEvent"), "播放",
                         "要登记进『动画 ▸ 播放』,否则菜单里看不到")
        outputs = [spec["name"] for spec in cls.gk_outputs]
        self.assertIn("触发时", outputs)
        self.assertIn("完成", outputs)
        props = {entry[0] for entry in cls.gk_prop_defs}
        self.assertIn("gk_event_time", props)
        self.assertIn("gk_anim_action", props, "要用动作选择器,别只给手填框")

    def test_generates_time_check_in_process(self):
        code, warnings = self._build()
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _process(", code)
        # 盯着"当前播的是不是这段""播到第几秒了"
        self.assertIn('.current_animation == "攻击"', code)
        self.assertIn(".current_animation_position", code)
        self.assertIn(">= 0.4", code)
        # 两个成员变量:响过没有 / 上一帧的进度
        self.assertIn("var _gk_ev_armed:", code)
        self.assertIn("var _gk_ev_last:", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_trigger_chain_lands_in_process(self):
        """『触发时』接的东西要落在 `_process` 里(每帧查出来的那一刻)。"""
        code, _ = self._build()
        funcs = self.functions(code)
        self.assertIn("触发了", funcs["_process"])
        self.assertNotIn("触发了", funcs.get("_physics_process", ""))

    def test_complete_chain_stays_in_execution_flow(self):
        """『完成』是执行流,接的东西还在原来的地方。"""
        code, _ = self._build()
        funcs = self.functions(code)
        # 上一帧的进度要**在 _process 里**更新(不能留在别处)
        self.assertIn("_gk_ev_last = ", funcs["_process"])

    def test_two_events_get_their_own_vars(self):
        """同一张图放两个事件 -> 各管各的"响过没有",不能互相覆盖。"""
        code, _ = self._build(chained=[("攻击", 0.4), ("攻击", 0.8)])
        self.assertIn("var _gk_ev_armed:", code)
        self.assertIn("var _gk_ev_armed_2:", code)
        self.assertIn(">= 0.4", code)
        self.assertIn(">= 0.8", code)

    def test_empty_anim_name_warns(self):
        code, warnings = self._build(anim_name="")
        self.assertTrue(any("动画" in w for w in warnings), warnings)
        self.assertNotIn("current_animation_position", code)

    def test_zero_seconds_warns(self):
        code, warnings = self._build(seconds=0.0)
        self.assertTrue(any("大于 0" in w for w in warnings), warnings)
        self.assertNotIn("current_animation_position", code)

    def test_unwired_trigger_warns(self):
        _code, warnings = self._build(wire_trigger=False)
        self.assertTrue(any("触发时" in w for w in warnings), warnings)


class TestRound77SceneBgm(Base):
    """第七十七轮(C 组 7):**场景开局 BGM**(『场景规则』)。

    以前要自己接『每当_准备 → 音效_音乐』。现在侧栏选一首就行 ——
    落的点是**环境脚本**(每场景一份,天然就是"场景规则"该待的地方):
    开局自动造一个常驻 AudioStreamPlayer 播着、循环。

    判据:**没选就一行都不多**(老工程导出产物一字不变)。
    """

    @staticmethod
    def _scene(**overrides):
        values = dict(dimension="3", scene_bgm="", scene_bgm_volume=-6.0)
        values.update(overrides)
        return types.SimpleNamespace(gk_settings=types.SimpleNamespace(**values))

    def test_off_by_default(self):
        from BlenderGameKit import asset_export as ae
        self.assertEqual(ae.scene_bgm_path(self._scene()), "")
        # 没有 gk_settings / scene 是 None 都不能炸
        self.assertEqual(ae.scene_bgm_path(types.SimpleNamespace()), "")
        self.assertEqual(ae.scene_bgm_path(None), "")

    def test_non_audio_file_is_ignored(self):
        """选了个不是音频的文件(比如 .txt)-> 当没选,别去 load 它。"""
        from BlenderGameKit import asset_export as ae
        self.assertEqual(ae.scene_bgm_path(self._scene(scene_bgm=r"D:\a.txt")),
                         "")

    def test_env_script_gets_the_bgm_player(self):
        from BlenderGameKit import asset_export as ae
        from BlenderGameKit import sound_assets
        scene = self._scene(scene_bgm=r"D:\snd\主题曲.ogg")
        fields = ae._env_fields(scene, camera_name="", fov=0.0)
        text = ae.ENVIRONMENT_SCRIPT.format(**fields)
        # 音频走和『资产_声音卡』同一条路 —— 路径必须算得一模一样
        self.assertIn(sound_assets.sound_res_path(r"D:\snd\主题曲.ogg"), text)
        self.assertIn("bgm_volume_db: float = -6.0", text)
        self.assertIn("AudioStreamPlayer.new()", text)
        # @tool 脚本在编辑器里也会 _ready —— 编辑器里不许自己放音乐
        self.assertIn("if not bgm_path.is_empty() and not Engine.is_editor_hint()",
                      text)

    def test_empty_bgm_keeps_script_quiet(self):
        from BlenderGameKit import asset_export as ae
        fields = ae._env_fields(self._scene(), camera_name="", fov=0.0)
        text = ae.ENVIRONMENT_SCRIPT.format(**fields)
        self.assertIn('bgm_path: String = ""', text)
        # 空路径也要有守卫,不然会去 load("")
        self.assertIn("if not bgm_path.is_empty()", text)

    def test_volume_is_written_through(self):
        from BlenderGameKit import asset_export as ae
        scene = self._scene(scene_bgm=r"D:\snd\bgm.wav", scene_bgm_volume=-12.5)
        fields = ae._env_fields(scene, camera_name="", fov=0.0)
        self.assertIn("bgm_volume_db: float = -12.5",
                      ae.ENVIRONMENT_SCRIPT.format(**fields))

    def test_2d_scene_with_bgm_still_gets_env_script(self):
        """2D 场景本来只在"有活要干"时才写环境脚本 —— BGM 也算一件活。"""
        from BlenderGameKit import asset_export as ae
        scene = self._scene(dimension="2", scene_bgm=r"D:\snd\bgm.wav")
        _node, files, _cam = ae.build_environment_nodes(
            scene, is_3d=False, scene_name="World")
        self.assertTrue(files, "只开了 BGM 的 2D 场景也该有环境脚本")
        self.assertIn("bgm_path: String = ", list(files.values())[0])

    def test_2d_scene_without_bgm_has_nothing(self):
        """没开任何东西的 2D 场景:还是一份环境脚本都不写(老行为不变)。"""
        from BlenderGameKit import asset_export as ae
        _node, files, _cam = ae.build_environment_nodes(
            self._scene(dimension="2"), is_3d=False, scene_name="World")
        self.assertFalse(files)

    def test_bgm_becomes_a_sound_card(self):
        """选了 BGM 就得进音频管线 —— 导出时它会被复制进 audio/。"""
        from BlenderGameKit import asset_export as ae
        scene = self._scene(scene_bgm=r"D:\snd\主题曲.ogg")
        card = ae.scene_bgm_card(scene)
        self.assertIsNotNone(card, "没生成声音卡的话 BGM 不会被复制进 audio/")
        self.assertEqual(card[1], r"D:\snd\主题曲.ogg")
        self.assertIsNone(ae.scene_bgm_card(self._scene()))

    def test_defaults_cover_every_placeholder(self):
        """模板里的占位符必须都能被 `ENV_SCRIPT_DEFAULTS` 兜住。

        加字段时只改 `_env_fields` 忘了改 DEFAULTS -> `render_env_script()`
        渲染 KeyError,而导出走 `_env_fields` 反而是好的 —— 这种"一边好
        一边炸"最容易漏。
        """
        from BlenderGameKit import asset_export as ae
        placeholders = set(re.findall(r"\{(\w+)\}", ae.ENVIRONMENT_SCRIPT))
        missing = placeholders - set(ae.ENV_SCRIPT_DEFAULTS)
        self.assertEqual(missing, set(), f"模板占位符没有默认值: {missing}")

    def test_copy_target_matches_the_script_path(self):
        """环境脚本里写的 res:// 路径,必须和**真的被复制过去**的文件一致。

        这两处以前在资产卡那条路上就是这么锁的(名字算两遍迟早飘掉)——
        脚本说 `res://audio/主题曲_ab12cd34.ogg`,复制却叫别的名,
        运行时就只是"找不到文件"。
        """
        from BlenderGameKit import asset_export as ae
        from BlenderGameKit import sound_assets
        scene = self._scene(scene_bgm=r"D:\snd\主题曲.ogg")
        with tempfile.TemporaryDirectory() as folder:
            planned = sound_assets.sound_files(folder, [ae.scene_bgm_card(scene)])
        self.assertEqual(len(planned), 1, planned)
        _name, _path, _target, res = planned[0]
        fields = ae._env_fields(scene, camera_name="", fov=0.0)
        self.assertIn(res, ae.ENVIRONMENT_SCRIPT.format(**fields))


class TestRound78PatrolWaypoints(Base):
    """第七十八轮(D 组 8):**巡逻路径点(3 个以上)**。

    以前巡逻只有两点(『巡逻点 A』『巡逻点 B』)+ 一个翻面布尔 ——
    来回一条直线。现在卡片上多了『巡逻点 C』『巡逻点 D』(同样是物体
    选择器,最多四个点),走法是**往返**:A→B→C→D→C→B→A。

    判据:**只填了 A / B 的老图走原来那条路,生成物一字不变**。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def _ai_tree(self, name="敌人AI"):
        ai = gk_stub.StubNodeTree(name)
        ai.bl_idname = "GKGameAiTree"
        return ai

    def _gen_with_ai(self, ai, tree):
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[ai]))
        try:
            return gen(tree, V3)
        finally:
            if original is None:
                try:
                    delattr(bpy_module(), "data")
                except AttributeError:
                    pass
            else:
                setattr(bpy_module(), "data", original)

    def _run(self, **patrol):
        """一张最小 AI 图:只有一个巡逻状态 + 一个『AI_启用』。"""
        ai = self._ai_tree()
        ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                    gk_behavior="PATROL", gk_speed=3.0, gk_stop_distance=0.6,
                    **patrol)
        tree = make_tree()
        event = self._ready(tree)
        enable = tree.new_node(NN.GKNodeAiEnable, "启用AI")
        gk_stub.connect(tree, event, "执行", enable, "执行")
        return self._gen_with_ai(ai, tree)

    def test_waypoint_props_declared(self):
        props = {entry[0] for entry in NN.GKNodeAiState.gk_prop_defs}
        self.assertIn("gk_patrol_c", props)
        self.assertIn("gk_patrol_d", props)

    def test_two_points_keep_the_old_flip(self):
        """只填 A / B:还是原来那个翻面布尔,一个新变量都不多。"""
        code, warnings = self._run(gk_patrol_a="点A", gk_patrol_b="点B")
        self.assertEqual(warnings, [], warnings)
        self.assertIn("var _gk_ai_patrol_1: bool = false", code)
        self.assertIn("_gk_ai_patrol_1 = not _gk_ai_patrol_1", code)
        self.assertNotIn("_gk_ai_wp_index", code)
        self.assertNotIn("_gk_ai_wp_dir", code)

    def test_three_points_ping_pong(self):
        code, warnings = self._run(gk_patrol_a="点A", gk_patrol_b="点B",
                                   gk_patrol_c="点C")
        self.assertEqual(warnings, [], warnings)
        # 当前第几个点 + 往前往后(都要跨帧记 -> 成员变量)
        self.assertIn("var _gk_ai_wp_index_1: int = 0", code)
        self.assertIn("var _gk_ai_wp_dir_1: bool = true", code)
        # 三个点 = 索引 0/1/2,两头的边界各翻一次方向
        self.assertIn("_gk_ai_wp_index_1 >= 2:", code)
        self.assertIn("_gk_ai_wp_index_1 <= 0:", code)
        self.assertIn("_gk_ai_wp_index_1 += 1", code)
        self.assertIn("_gk_ai_wp_index_1 -= 1", code)
        for name in ("点A", "点B", "点C"):
            self.assertIn(name, code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_four_points(self):
        code, _ = self._run(gk_patrol_a="点A", gk_patrol_b="点B",
                            gk_patrol_c="点C", gk_patrol_d="点D")
        self.assertIn("_gk_ai_wp_index_1 >= 3:", code)
        self.assertIn("点D", code)

    def test_gap_between_points_is_skipped(self):
        """跳着填(A + C,B 空)也走:a→c→a,不是站着不动。"""
        code, warnings = self._run(gk_patrol_a="点A", gk_patrol_c="点C")
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_ai_wp_index_1 >= 1:", code)
        self.assertIn("点C", code)

    def test_no_waypoint_path_without_point_a(self):
        """只填了 C 没填 A —— 走不了。要么告警、要么别生成那条路。"""
        code, warnings = self._run(gk_patrol_c="点C")
        self.assertTrue(any("巡逻点 A" in w for w in warnings), warnings)
        self.assertNotIn("_gk_ai_wp_index", code)

    def test_return_behavior_still_goes_to_a_only(self):
        """『返回』还是只回 A 点 —— 巡逻点 C / D 不掺和。"""
        ai = self._ai_tree()
        ai.new_node(NN.GKNodeAiState, "回岗", gk_state_name="回岗",
                    gk_behavior="RETURN", gk_patrol_a="点A", gk_patrol_c="点C")
        tree = make_tree()
        event = self._ready(tree)
        enable = tree.new_node(NN.GKNodeAiEnable, "启用AI")
        gk_stub.connect(tree, event, "执行", enable, "执行")
        code, warnings = self._gen_with_ai(ai, tree)
        self.assertEqual(warnings, [], warnings)
        self.assertNotIn("_gk_ai_wp_index", code)


class TestRound79AiAttack(Base):
    """第七十九轮(D 组 9 + 10):『攻击』行为 + 状态的『进入时』/『离开时』出口。

    D-9:『AI_状态』多一个行为『攻击』—— 走进『攻击距离』就停下、面向目标,
         每『冷却』秒"打一下"。插件**不建血量系统**:打一下具体做什么
         由用户接在『进入时』出口上(设变量 / 播音效 / 发信号)。
    D-10:状态卡多了『进入时』『离开时』两条执行流(照动画状态的做法),
         于是 AI 图里也能接玩法图节点了。

    两条最容易踩的规矩:
      * 『攻击』状态的『进入时』= **每打一下跑一次**(不是进状态跑一次),
        否则"看见玩家 -> 进攻击状态"会在还在赶路时就打一下;
      * 没接任何钩子的老图,生成物**一字不变**。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def _ai_tree(self, name="敌人AI"):
        ai = gk_stub.StubNodeTree(name)
        ai.bl_idname = "GKGameAiTree"
        return ai

    def _gen_with_ai(self, ai, tree):
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[ai]))
        try:
            return gen(tree, V3)
        finally:
            if original is None:
                try:
                    delattr(bpy_module(), "data")
                except AttributeError:
                    pass
            else:
                setattr(bpy_module(), "data", original)

    def _enable(self, tree):
        """玩法图:『每当_准备』->『AI_启用』(AI 图的唯一入口)。"""
        event = self._ready(tree)
        enable = tree.new_node(NN.GKNodeAiEnable, "启用AI")
        gk_stub.connect(tree, event, "执行", enable, "执行")
        return enable

    def _attack_state(self, ai, **props):
        props.setdefault("gk_goal", "玩家")
        return ai.new_node(NN.GKNodeAiState, "攻击", gk_state_name="攻击",
                           gk_behavior="ATTACK", **props)

    # ---- 枚举 / 属性 / 引脚 ----
    def test_attack_in_behavior_table(self):
        behaviors = {item[0]: item[1] for item in NN.AI_BEHAVIORS}
        self.assertEqual(behaviors.get("ATTACK"), "攻击")

    def test_attack_props_declared(self):
        props = {entry[0] for entry in NN.GKNodeAiState.gk_prop_defs}
        self.assertIn("gk_attack_range", props)
        self.assertIn("gk_cooldown", props)

    def test_state_has_enter_exit_outputs(self):
        names = [pin["name"] for pin in NN.GKNodeAiState.gk_outputs]
        self.assertIn("转到", names)
        self.assertIn("进入时", names)
        self.assertIn("离开时", names)

    # ---- 攻击行为的生成 ----
    def test_attack_generates_range_and_cooldown(self):
        ai = self._ai_tree()
        self._attack_state(ai, gk_attack_range=2.0, gk_cooldown=1.5,
                           gk_speed=3.0, gk_stop_distance=0.6)
        tree = make_tree()
        self._enable(tree)
        code, warnings = self._gen_with_ai(ai, tree)
        # 没接『进入时』-> 提醒"打不出效果",但生成物本身要是对的
        self.assertTrue(any("进入时" in w for w in warnings), warnings)
        # 冷却计时器是成员变量(要跨帧记)
        self.assertIn("var _gk_ai_atk_1: float = 0.0", code)
        # 走进攻击距离就停下,超出就朝目标走(临时变量带序号,用正则)
        self.assertRegex(code, r"if _ai_dist\d+ > 2\.0:")
        self.assertRegex(code, r"\.velocity = _ai_diff\d+\.normalized\(\) \* 3\.0")
        self.assertIn(".velocity = Vector3.ZERO", code)
        # 冷却到点才"打一下"
        self.assertIn("_gk_ai_atk_1 > 0.0", code)
        self.assertIn("_gk_ai_atk_1 -= delta", code)
        self.assertIn("_gk_ai_atk_1 = 1.5", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_attack_range_zero_falls_back_to_stop_distance(self):
        ai = self._ai_tree()
        self._attack_state(ai, gk_attack_range=0.0, gk_stop_distance=0.9)
        tree = make_tree()
        self._enable(tree)
        code, _ = self._gen_with_ai(ai, tree)
        self.assertRegex(code, r"if _ai_dist\d+ > 0\.9:")

    def test_attack_without_target_warns(self):
        ai = self._ai_tree()
        ai.new_node(NN.GKNodeAiState, "攻击", gk_state_name="攻击",
                    gk_behavior="ATTACK")           # 没选目标
        graph = NN.ai_tree_graph(ai)
        joined = "\n".join(graph["problems"])
        self.assertIn("目标", joined)

    def test_attack_without_enter_hook_warns(self):
        ai = self._ai_tree()
        self._attack_state(ai)
        graph = NN.ai_tree_graph(ai)
        joined = "\n".join(graph["problems"])
        self.assertIn("进入时", joined)

    def test_strike_runs_enter_hook_every_cooldown(self):
        """『进入时』= 每打一下跑一次(攻击状态),不是进状态跑一次。"""
        ai = self._ai_tree()
        atk = self._attack_state(ai, gk_attack_range=2.0, gk_cooldown=1.5)
        setter = ai.new_node(NN.GKNodeSetVar, "打一下", gk_var_name="攻击中",
                             gk_value=1.0)
        gk_stub.connect(ai, atk, "进入时", setter, "执行")
        tree = make_tree()
        self._enable(tree)
        code, warnings = self._gen_with_ai(ai, tree)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _gk_ai_enter_攻击() -> void:", code)
        self.assertIn("攻击中 = 1.0", code)
        # 触发点在冷却到点那一支里
        strike_line = "_gk_ai_atk_1 = 1.5"
        hook_line = "_gk_ai_enter_攻击()"
        self.assertLess(code.index(strike_line), code.index(hook_line))
        # 起始状态就是攻击状态时,进状态**不**额外打一下(免得赶路时先打空拳)
        self.assertNotIn('ai_state = "攻击"\n        _gk_ai_enter_攻击()', code)

    def test_enter_hook_runs_on_entry_for_normal_states(self):
        ai = self._ai_tree()
        patrol = ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                             gk_behavior="PATROL", gk_patrol_a="点A",
                             gk_patrol_b="点B")
        setter = ai.new_node(NN.GKNodeSetVar, "开工", gk_var_name="巡逻中",
                             gk_value=1.0)
        gk_stub.connect(ai, patrol, "进入时", setter, "执行")
        tree = make_tree()
        self._enable(tree)
        code, warnings = self._gen_with_ai(ai, tree)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _gk_ai_enter_巡逻() -> void:", code)
        # 进起始状态(巡逻)时跑一次
        self.assertIn('ai_state = "巡逻"\n        _gk_ai_enter_巡逻()', code)

    def test_exit_hook_runs_before_switch(self):
        ai = self._ai_tree()
        patrol = ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                             gk_behavior="PATROL", gk_patrol_a="点A",
                             gk_patrol_b="点B")
        atk = self._attack_state(ai, gk_attack_range=2.0)
        leaving = ai.new_node(NN.GKNodeSetVar, "收工", gk_var_name="巡逻中",
                              gk_value=0.0)
        gk_stub.connect(ai, patrol, "离开时", leaving, "执行")
        # 攻击状态接了『进入时』(打一下)-> 免得"没接钩子"的提醒混进来
        hitting = ai.new_node(NN.GKNodeSetVar, "打一下", gk_var_name="攻击中",
                              gk_value=1.0)
        gk_stub.connect(ai, atk, "进入时", hitting, "执行")
        ai.new_node(NN.GKNodeAiTransition, "看见玩家", gk_cond_source="SEE",
                    gk_goal="玩家", gk_sight=10.0,
                    gk_from_state="巡逻", gk_to_state="攻击")
        tree = make_tree()
        self._enable(tree)
        code, warnings = self._gen_with_ai(ai, tree)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("func _gk_ai_exit_巡逻() -> void:", code)
        # 『离开时』必须在状态变量被改写**之前**跑
        self.assertLess(code.index("_gk_ai_exit_巡逻()"),
                        code.index('ai_state = "攻击"'))

    def test_no_hooks_means_no_extra_lines(self):
        """一张没接任何钩子的老图:一个钩子函数都不多。"""
        ai = self._ai_tree()
        ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                    gk_behavior="PATROL", gk_patrol_a="点A", gk_patrol_b="点B")
        tree = make_tree()
        self._enable(tree)
        code, warnings = self._gen_with_ai(ai, tree)
        self.assertEqual(warnings, [], warnings)
        self.assertNotIn("_gk_ai_enter_", code)
        self.assertNotIn("_gk_ai_exit_", code)
        self.assertNotIn("_gk_ai_atk_", code)

    def test_only_attack_states_declare_cooldown_timer(self):
        ai = self._ai_tree()
        ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                    gk_behavior="PATROL", gk_patrol_a="点A", gk_patrol_b="点B")
        tree = make_tree()
        self._enable(tree)
        code, _ = self._gen_with_ai(ai, tree)
        self.assertNotIn("_gk_ai_atk_", code)

    # ---- AI 图里能放"做事的"节点(D-10 的前提)----
    def test_ai_tree_now_allows_flow_nodes(self):
        from BlenderGameKit import properties as props
        for cls in (NN.GKNodeSetVar, NN.GKNodeAddVar, NN.GKNodePlaySound,
                    NN.GKNodeEmitSignal):
            self.assertTrue(props.node_fits_tree(cls, "GKGameAiTree"),
                            cls.bl_label + " 应该能放进 AI 图(状态的钩子里用)")
            self.assertTrue(props.node_fits_tree(cls, "GKGameLogicTree"),
                            cls.bl_label + " 不该从玩法图里消失")
            # 动画树**不能**跟着被带开(不然又是一个"放了用不了"的坑)
            self.assertFalse(props.node_fits_tree(cls, "GKAnimationTree"),
                             cls.bl_label + " 不该出现在动画树里")

    def test_ai_menu_lists_flow_nodes(self):
        from BlenderGameKit import properties as props
        labels = [cls.bl_label
                  for _cat, items in props.node_menu_entries("GKGameAiTree")
                  for cls in items]
        self.assertIn("变量_设置", labels)
        self.assertIn("音效_播放", labels)


class TestRound71SingleChain(Base):
    """第七十一轮:处理器**不许自己再走一遍下游**。

    `emit_statement()` 调完处理器之后**已经**会沿『完成』往下走。
    处理器里再 `_chain(ctx, node, indent)` 就会把下游整段生成**两遍** ——
    『等待_秒』早就踩过(`_stmt_wait` 的注释),但这两处一直没清:

      * `_stmt_ai_enable`(『AI_启用』)—— 小游戏示例里表现为
        『界面_套用设计』的三个控件各建两遍(第四十四轮起就在);
      * `_stmt_noise`(『声音_发出噪音』)。

    不只是白占行数:下游要是『变量_加』『音效_播放』『实例_生成』这种
    **有副作用**的节点,游戏里就是真的执行两次。
    """

    def _ai_tree(self, name="敌人AI"):
        ai = gk_stub.StubNodeTree(name)
        ai.bl_idname = "GKGameAiTree"
        state = ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                            gk_behavior="PATROL", gk_patrol_a="点A",
                            gk_patrol_b="点B", gk_speed=3.0,
                            gk_stop_distance=0.6)
        return ai

    def _gen_with(self, groups, tree):
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=groups))
        try:
            return gen(tree, V3)
        finally:
            if original is None:
                try:
                    delattr(bpy_module(), "data")
                except AttributeError:
                    pass
            else:
                setattr(bpy_module(), "data", original)

    def test_chain_after_ai_enable_runs_once(self):
        ai = self._ai_tree()
        tree = make_tree()
        ready = tree.new_node(NN.GKNodeOnReady, "准备")
        enable = tree.new_node(NN.GKNodeAiEnable, "启用AI", gk_target="玩家")
        # 下游放一个"看得见"的节点:带副作用的节点会被真的执行两次
        mark = tree.new_node(NN.GKNodePrint, "标记", gk_text="唯一标记")
        gk_stub.connect(tree, ready, "执行", enable, "执行")
        gk_stub.connect(tree, enable, "完成", mark, "执行")
        code, _warnings = self._gen_with([ai], tree)
        self.assertEqual(code.count("唯一标记"), 1,
                         "『AI_启用』下游只该走一遍:\n" + code[-600:])

    def test_chain_after_noise_runs_once(self):
        tree = make_tree()
        ready = tree.new_node(NN.GKNodeOnReady, "准备")
        noise = tree.new_node(NN.GKNodeNoise, "弄出动静",
                              gk_loudness=12.0, gk_target="玩家")
        mark = tree.new_node(NN.GKNodePrint, "标记", gk_text="唯一标记")
        gk_stub.connect(tree, ready, "执行", noise, "执行")
        gk_stub.connect(tree, noise, "完成", mark, "执行")
        code, _warnings = gen(tree, V3)
        self.assertEqual(code.count("唯一标记"), 1,
                         "『声音_发出噪音』下游只该走一遍:\n" + code[-600:])


class TestRound80FleeNavAndHearing(Base):
    """第八十轮(D 组 11 + 12):逃跑接导航 / 噪音类型 / 去查看环顾 / 脚下响度。

    D-11:场景勾了『寻路』时,『逃跑』把"威胁反方向投影一个点"交给导航网格
         (`_gk_ai_nav_dir`);没勾时生成物和老图**逐字一致**(直线后退)。
    D-12①:『声音_发出噪音』带『类型』;『AI_听见』和转场的听见/安静条件
          能按类型过滤(留空 = 什么都听)。
    D-12③:『去查看』走到噪音点后原地左右环顾(角度 + 秒数两张卡上配),
          环顾期间『安静下来』不成立,环顾结束才允许回岗。
    D-12②:新节点『声音_脚下响度』向下射线查脚下碰撞体的
          `gk_surface_loudness` 元数据(Blender 物体『游戏物理』里配,
          导出写进 .tscn 的 `metadata/`),拿它乘跑步噪音的响度。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def _ai_tree(self, name="敌人AI"):
        ai = gk_stub.StubNodeTree(name)
        ai.bl_idname = "GKGameAiTree"
        return ai

    def _gen_with_ai(self, ai, tree, options=None):
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[ai]))
        try:
            return gen(tree, options or V3)
        finally:
            if original is None:
                try:
                    delattr(bpy_module(), "data")
                except AttributeError:
                    pass
            else:
                setattr(bpy_module(), "data", original)

    def _enable(self, tree, **props):
        event = self._ready(tree)
        enable = tree.new_node(NN.GKNodeAiEnable, "启用AI", **props)
        gk_stub.connect(tree, event, "执行", enable, "执行")
        return enable

    def _flee_tree(self, **props):
        ai = self._ai_tree()
        props.setdefault("gk_goal", "玩家")
        ai.new_node(NN.GKNodeAiState, "逃跑", gk_state_name="逃跑",
                    gk_behavior="FLEE", gk_speed=5.0,
                    gk_stop_distance=3.0, gk_flee_distance=8.0, **props)
        tree = make_tree()
        self._enable(tree)
        return ai, tree

    # ---- 卡片 / 属性都登记好了 ----
    def test_new_props_declared(self):
        state_props = {entry[0] for entry in NN.GKNodeAiState.gk_prop_defs}
        self.assertIn("gk_flee_distance", state_props)
        self.assertIn("gk_look_angle", state_props)
        self.assertIn("gk_look_time", state_props)
        trans_props = {entry[0] for entry in NN.GKNodeAiTransition.gk_prop_defs}
        self.assertIn("gk_hear_type", trans_props)
        hear_props = {entry[0] for entry in NN.GKNodeAiHear.gk_prop_defs}
        self.assertIn("gk_hear_type", hear_props)
        noise_props = {entry[0] for entry in NN.GKNodeNoise.gk_prop_defs}
        self.assertIn("gk_noise_type", noise_props)

    def test_surface_loudness_node_registered(self):
        cls = getattr(NN, "GKNodeSurfaceLoudness", None)
        self.assertIsNotNone(cls, "声音_脚下响度 没注册")
        self.assertEqual(cls.bl_label, "声音_脚下响度")
        self.assertEqual(str(cls.gk_tree_type), "GKGameLogicTree")
        self.assertTrue(getattr(cls, "gk_no_cache", False),
                        "脚下响度是射线,每次求值都要重新射,必须 gk_no_cache")
        outs = [pin["name"] for pin in cls.gk_outputs]
        self.assertIn("响度倍率", outs)

    # ---- D-11:逃跑接导航 ----
    def test_flee_without_nav_is_byte_compatible(self):
        ai, tree = self._flee_tree()
        code, warnings = self._gen_with_ai(ai, tree)
        self.assertEqual(warnings, [], warnings)
        # 没勾寻路:一个导航字眼都不许有(老图逐字不变)
        self.assertNotIn("_gk_ai_nav_dir", code)
        self.assertNotIn("flee_goal", code)
        self.assertRegex(code, r"if _ai_dist\d+ <= 3\.0:")
        self.assertRegex(code,
                         r"\.velocity = _ai_away\d+\.normalized\(\) \* 5\.0")
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_flee_with_nav_projects_a_point_behind(self):
        ai, tree = self._flee_tree()
        code, warnings = self._gen_with_ai(ai, tree, dict(V3, nav_enabled=True))
        self.assertEqual(warnings, [], warnings)
        # 投影点 = 自己 + (背对威胁方向) × 逃跑距离
        self.assertRegex(
            code,
            r"var _ai_flee_goal\d+ := _ai_here\d+ \+ "
            r"_ai_away\d+\.normalized\(\) \* 8\.0")
        # 这个点交给导航网格,而不是直接 normalized()
        self.assertRegex(
            code,
            r"var _ai_dir\d+ := _gk_ai_nav_dir\(self, _ai_flee_goal\d+\)")
        self.assertRegex(code, r"if _ai_dist\d+ < 0\.001:")
        self.assertRegex(code, r"\.velocity = _ai_dir\d+ \* 5\.0")
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    # ---- D-12①:噪音类型 ----
    def _hearing_graph(self, want_type=""):
        """巡逻 --听见『类型』--> 去查看 --安静(同类型)--> 巡逻 + 一个发声卡。"""
        ai = self._ai_tree()
        patrol = ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                             gk_behavior="PATROL", gk_patrol_a="点A",
                             gk_patrol_b="点B", gk_speed=3.0,
                             gk_stop_distance=0.6)
        look = ai.new_node(NN.GKNodeAiState, "去查看", gk_state_name="去查看",
                           gk_behavior="INVESTIGATE", gk_speed=4.0,
                           gk_stop_distance=1.0, gk_look_angle=70.0,
                           gk_look_time=2.0)
        heard = ai.new_node(NN.GKNodeAiTransition, "听见玻璃",
                            gk_cond_source="HEARD", gk_ear=0.0, gk_memory=1.2,
                            gk_hear_type=want_type)
        calm = ai.new_node(NN.GKNodeAiTransition, "安静了",
                           gk_cond_source="CALM", gk_ear=0.0, gk_memory=1.2,
                           gk_hear_type=want_type)
        gk_stub.connect(ai, patrol, "转到", heard, "上游")
        gk_stub.connect(ai, heard, "转到", look, "进入")
        gk_stub.connect(ai, look, "转到", calm, "上游")
        gk_stub.connect(ai, calm, "转到", patrol, "进入 2")

        tree = make_tree()
        enable = self._enable(tree)
        noise = tree.new_node(NN.GKNodeNoise, "打碎玻璃",
                              gk_loudness=16.0, gk_interval=0.2,
                              gk_target="玩家", gk_noise_type="玻璃")
        gk_stub.connect(tree, enable, "完成", noise, "执行")
        return ai, tree

    def test_dispatcher_carries_noise_type(self):
        ai, tree = self._hearing_graph(want_type="玻璃")
        code, warnings = self._gen_with_ai(ai, tree)
        self.assertEqual(warnings, [], warnings)
        self.assertIn(
            "func _gk_hear_noise(at: Vector3, loudness: float, "
            "noise_type: String) -> void:", code)
        self.assertIn("ai_state_hear_type = noise_type", code)
        # 广播方把类型一起送过去
        self.assertRegex(code, r'\._gk_hear_noise\([^;]*?, 16\.0, "玻璃"\)')
        # 听方函数多了 want_type 参数,并在不是的时候拒绝
        self.assertIn(
            "func _gk_heard_ai_state_hear(host: Node, ear: float, "
            "memory: float, want_type: String) -> bool:", code)
        self.assertIn('if want_type != "":', code)
        self.assertIn("ai_state_hear_type != want_type", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_transition_passes_want_type(self):
        ai, tree = self._hearing_graph(want_type="玻璃")
        code, _ = self._gen_with_ai(ai, tree)
        # 听见 / 安静两条边都把类型带进判据
        self.assertEqual(len(re.findall(r'_gk_heard_ai_state_hear\([^\n]*?"玻璃"\)',
                                        code)), 2)

    def test_ai_hear_expression_filters_by_type(self):
        ai = self._ai_tree()
        patrol = ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                             gk_behavior="PATROL", gk_patrol_a="点A",
                             gk_patrol_b="点B")
        chase = ai.new_node(NN.GKNodeAiState, "追击", gk_state_name="追击",
                            gk_behavior="CHASE", gk_goal="玩家")
        hear = ai.new_node(NN.GKNodeAiHear, "听见脚步", gk_ear=20.0,
                           gk_memory=3.0, gk_hear_type="脚步")
        trans = ai.new_node(NN.GKNodeAiTransition, "听见就追")
        gk_stub.connect(ai, patrol, "转到", trans, "上游")
        gk_stub.connect(ai, hear, "听见", trans, "条件")
        gk_stub.connect(ai, trans, "转到", chase, "进入")
        tree = make_tree()
        self._enable(tree, gk_target="敌人")
        code, warnings = self._gen_with_ai(ai, tree)
        self.assertEqual(warnings, [], warnings)
        self.assertRegex(code, r'_gk_heard_ai_state_hear\([^\n]*?"脚步"\)')

    # ---- D-12③:去查看到位后环顾 ----
    def test_investigate_look_around_runtime(self):
        ai, tree = self._hearing_graph(want_type="玻璃")
        code, _ = self._gen_with_ai(ai, tree)
        # 跨帧记的环顾状态(默认"看完了"—— 没听到声音时安静条件该成立)
        self.assertIn("var _gk_inv_on_1: bool = false", code)
        self.assertIn("var _gk_inv_done_1: bool = true", code)
        self.assertIn("var _gk_inv_start_1: float = -1.0", code)
        self.assertIn("var _gk_inv_base_1: float = 0.0", code)
        self.assertIn("var _gk_inv_noise_1: float = -1000000000.0", code)
        # 新噪音 -> 重新开始(赶路途中就要关门,start 置 -1 = 还没到点);
        # 到点后:70°、2 秒、正弦摆头
        self.assertIn("if _gk_inv_noise_1 != ai_state_hear_at:", code)
        self.assertIn("_gk_inv_start_1 = -1.0", code)
        self.assertIn("if _gk_inv_on_1 and _gk_inv_start_1 < 0.0:", code)
        self.assertIn("(self as Node3D).rotation.y = _gk_inv_base_1", code)
        self.assertRegex(
            code,
            r"deg_to_rad\(70\.0\) \* sin\(TAU \* _ai_look_el\d+ / 2\.0\)")
        self.assertRegex(code, r"if _ai_look_el\d+ >= 2\.0:")
        self.assertIn("_gk_inv_done_1 = true", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_calm_blocked_until_look_done(self):
        ai, tree = self._hearing_graph(want_type="玻璃")
        code, _ = self._gen_with_ai(ai, tree)
        # 『安静下来』= 听不见**且**环顾完了
        self.assertRegex(
            code,
            r"not _gk_heard_ai_state_hear\([^\n]*?"
            r'"玻璃"\) and _gk_inv_done_1')

    def test_calm_without_investigate_is_unchanged(self):
        # 没有『去查看』状态的图:一个环顾变量都不多,CALM 也不被门控
        ai = self._ai_tree()
        patrol = ai.new_node(NN.GKNodeAiState, "巡逻", gk_state_name="巡逻",
                             gk_behavior="PATROL", gk_patrol_a="点A",
                             gk_patrol_b="点B")
        idle = ai.new_node(NN.GKNodeAiState, "待机", gk_state_name="待机",
                           gk_behavior="STAND")
        calm = ai.new_node(NN.GKNodeAiTransition, "安静了",
                           gk_cond_source="CALM", gk_memory=3.0)
        gk_stub.connect(ai, patrol, "转到", calm, "上游")
        gk_stub.connect(ai, calm, "转到", idle, "进入")
        tree = make_tree()
        self._enable(tree)
        code, warnings = self._gen_with_ai(ai, tree)
        self.assertEqual(warnings, [], warnings)
        self.assertNotIn("_gk_inv_", code)
        self.assertRegex(code, r"if not _gk_heard_ai_state_hear\([^\n]*?\):")

    # ---- D-12②:脚下响度 ----
    def _surface_tree(self, target="玩家"):
        tree = make_tree()
        event = self._ready(tree)
        surf = tree.new_node(NN.GKNodeSurfaceLoudness, "脚下响度",
                             gk_target=target, gk_distance=3.0)
        noise = tree.new_node(NN.GKNodeNoise, "跑步声", gk_loudness=16.0,
                              gk_interval=0.2, gk_target=target,
                              gk_noise_type="脚步")
        gk_stub.connect(tree, surf, "响度倍率", noise, "响度")
        gk_stub.connect(tree, event, "执行", noise, "执行")
        return tree

    def test_surface_loudness_helper_3d(self):
        code, warnings = gen(self._surface_tree(), V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn(
            "func _gk_surface_loudness_3d(host: Node, dist: float) -> float:",
            code)
        self.assertIn("get_world_3d().direct_space_state", code)
        self.assertIn("intersect_ray", code)
        self.assertIn('get_meta("gk_surface_loudness", 1.0)', code)
        # 探测不到地面时退回 1.0(别把脚步声弄没了)
        self.assertRegex(code,
                         r"if _gk_hit\d+.is_empty\(\):\s*\n\s*return 1\.0")
        # 发声卡的响度引脚真的接了脚下倍率
        self.assertRegex(code, r"_gk_surface_loudness_3d\([^\n]*?, 3\.0\)")
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_surface_loudness_helper_2d(self):
        code, _ = gen(self._surface_tree(), V2)
        self.assertIn(
            "func _gk_surface_loudness_2d(host: Node, dist: float) -> float:",
            code)
        self.assertIn("get_world_2d().direct_space_state", code)
        self.assertIn('get_meta("gk_surface_loudness", 1.0)', code)

    def test_surface_loudness_export_value_helper(self):
        """导出侧:1.0 = 普通地面(不写元数据);其它值(含 0)才写。"""
        from BlenderGameKit import asset_export as ae
        self.assertIsNone(ae.surface_loudness_of(
            types.SimpleNamespace(surface_loudness=1.0)))
        self.assertIsNone(ae.surface_loudness_of(types.SimpleNamespace()))
        self.assertAlmostEqual(ae.surface_loudness_of(
            types.SimpleNamespace(surface_loudness=1.5)), 1.5)
        self.assertAlmostEqual(ae.surface_loudness_of(
            types.SimpleNamespace(surface_loudness=0.0)), 0.0)


class TestRound83NavObstacle(Base):
    """第八十三轮(寻路第三版第一小件):动态障碍 `NavigationObstacle`。

    物体『游戏物理』勾『动态障碍』-> 导出时给它挂一个
    `NavigationObstacle2D / 3D` 子节点(半径 = 『障碍半径』)——
    勾了『避障』的角色会**实时绕开它,不用重烤导航网格**。
    没勾的老图一个字都不多(纯函数返回空)。
    """

    def _physics(self, **props):
        return types.SimpleNamespace(**props)

    def test_radius_of_default_and_clamp(self):
        from BlenderGameKit import asset_export as ae
        self.assertIsNone(ae.nav_obstacle_radius_of(None))
        self.assertIsNone(ae.nav_obstacle_radius_of(self._physics()))
        self.assertIsNone(ae.nav_obstacle_radius_of(
            self._physics(nav_obstacle=False, nav_obstacle_radius=2.0)))
        # 勾了:默认半径 1.0;值原样透出来
        self.assertAlmostEqual(ae.nav_obstacle_radius_of(
            self._physics(nav_obstacle=True)), 1.0)
        self.assertAlmostEqual(ae.nav_obstacle_radius_of(
            self._physics(nav_obstacle=True, nav_obstacle_radius=2.5)), 2.5)
        # 0 / 负数钳到 0.05:填 0 时 Godot 会当成"没有障碍"静默不生效
        self.assertAlmostEqual(ae.nav_obstacle_radius_of(
            self._physics(nav_obstacle=True, nav_obstacle_radius=0.0)), 0.05)
        self.assertAlmostEqual(ae.nav_obstacle_radius_of(
            self._physics(nav_obstacle=True, nav_obstacle_radius=-3.0)), 0.05)

    def test_node_lines_3d_and_2d(self):
        from BlenderGameKit import asset_export as ae
        self.assertEqual(ae.nav_obstacle_node_lines(
            self._physics(), True, "箱子"), [])
        lines3 = ae.nav_obstacle_node_lines(
            self._physics(nav_obstacle=True, nav_obstacle_radius=1.5),
            True, "箱子")
        self.assertIn('[node name="NavObstacle" type="NavigationObstacle3D" '
                      'parent="Colliders/箱子"]', lines3)
        self.assertIn("radius = 1.500000", lines3)
        lines2 = ae.nav_obstacle_node_lines(
            self._physics(nav_obstacle=True), False, "幽灵")
        self.assertIn('[node name="NavObstacle" type="NavigationObstacle2D" '
                      'parent="Colliders/幽灵"]', lines2)

    def test_property_registered_on_physics(self):
        """真实属性组上有这两个字段(冒烟那层再验真机 + 真导出)。

        ⚠ 这些属性是**类注解**(`nav_obstacle: BoolProperty(...)`),
        Blender 在注册时才把它们变成真属性 —— 所以这里查 `__annotations__`,
        查 `dir()` 是空的(第一版就是这么写错的)。
        """
        from BlenderGameKit import properties
        names = set(getattr(properties.GKObjectSettings, "__annotations__", {}))
        self.assertIn("nav_obstacle", names)
        self.assertIn("nav_obstacle_radius", names)


class TestRound84NavCost(Base):
    """第八十四轮(H 组 22 第二小件):区域代价 —— 泥地 / 危险区。

    物体『游戏物理』新增『通行代价』(默认 1.0 = 普通地面)。填 != 1 时
    导出侧把它的世界包围盒写进环境脚本的 `nav_costs`;环境脚本烘焙时
    把它当障碍**挖洞**、再单独补一块带 `travel_cost` 的代价区域 ——
    A* 会在"绕路"和"穿泥地"之间按代价选。没配代价区的场景 `nav_costs`
    为空,运行时行为不变(环境脚本里那些函数直接 return)。

    真机机制是 spike 定下来的(真 Godot 实测:**两块 region 重叠时代价
    会被无视**,必须"挖洞 + 补洞";拼边/共享边的两块 region 代价才生效)。
    探针在 demo_nav2d 里带对照(有泥地绕开 / 没泥地直穿 / 泥地可达)。
    """

    def _physics(self, **props):
        return types.SimpleNamespace(**props)

    def test_travel_cost_of(self):
        from BlenderGameKit import asset_export as ae
        self.assertIsNone(ae.nav_travel_cost_of(None))
        self.assertIsNone(ae.nav_travel_cost_of(self._physics()))
        # 1.0 = 普通地面 -> 不导出(老图一个字都不多)
        self.assertIsNone(ae.nav_travel_cost_of(
            self._physics(nav_travel_cost=1.0)))
        self.assertAlmostEqual(ae.nav_travel_cost_of(
            self._physics(nav_travel_cost=5.0)), 5.0)
        # 小于 1 = "近路"(会吸引路径)也透出来
        self.assertAlmostEqual(ae.nav_travel_cost_of(
            self._physics(nav_travel_cost=0.5)), 0.5)
        # 0 / 负数钳到 0.05:0 会让 A* 的代价计算失去意义
        self.assertAlmostEqual(ae.nav_travel_cost_of(
            self._physics(nav_travel_cost=0.0)), 0.05)
        self.assertAlmostEqual(ae.nav_travel_cost_of(
            self._physics(nav_travel_cost=-2.0)), 0.05)

    def test_literal_3d_uses_world_box(self):
        from BlenderGameKit import asset_export as ae
        self.assertEqual(ae.nav_costs_literal([], "3"), "[]")
        text = ae.nav_costs_literal([{
            "name": "泥地", "cost": 5.0,
            "min": (-1.5, 0.0, 4.5), "size": (3.0, 0.0, 2.0)}], "3")
        self.assertIn('"name": "泥地"', text)
        self.assertIn('"cost": 5.0', text)
        self.assertIn(
            "AABB(Vector3(-1.5, 0.0, 4.5), Vector3(3.0, 0.0, 2.0))", text)
        self.assertNotIn("Rect2", text)

    def test_literal_2d_uses_xy_rect(self):
        """2D(俯视)里 Z 没有意义 —— 只把 XY 平面的矩形写进去。"""
        from BlenderGameKit import asset_export as ae
        text = ae.nav_costs_literal([{
            "name": "泥地", "cost": 5.0,
            "min": (-1.5, 4.5, 0.0), "size": (3.0, 2.0, 0.0)}], "2")
        self.assertIn("Rect2(-1.5, 4.5, 3.0, 2.0)", text)
        self.assertNotIn("AABB", text)
        self.assertNotIn("Vector3", text)

    def test_literal_escapes_name(self):
        """物体名里带引号也不能把环境脚本写坏。"""
        from BlenderGameKit import asset_export as ae
        text = ae.nav_costs_literal([{
            "name": '泥"地', "cost": 2.0,
            "min": (0.0, 0.0, 0.0), "size": (1.0, 1.0, 1.0)}], "3")
        self.assertIn('\\"', text)

    def test_env_script_has_cost_pipeline(self):
        """环境脚本里有整条管线:空清单 + 挖洞 + 补洞 + 设代价。"""
        from BlenderGameKit import asset_export as ae
        text = ae.render_env_script()
        self.assertIn("var nav_costs: Array = []", text)
        for marker in ("add_obstruction_outline",
                       "add_projected_obstruction",
                       "region_set_travel_cost",
                       "_gk_apply_nav_costs_2d",
                       "_gk_apply_nav_costs_3d",
                       "_gk_nav_cost_surface_y"):
            self.assertIn(marker, text)

    def test_property_registered_on_physics(self):
        """真实属性组上有这个字段(冒烟那层再验真机 + 真导出)。"""
        from BlenderGameKit import properties
        names = set(getattr(properties.GKObjectSettings, "__annotations__", {}))
        self.assertIn("nav_travel_cost", names)


class TestRound85NavLayers(Base):
    """第八十五轮(H 组 22③):多层导航 —— 不同层的角色走**不同的导航网格**。

    ── 机制(真 Godot spike 定下)────────────────────────────────────
    Godot 的 `NavigationMesh` / `NavigationPolygon` 有
    `source_geometry_mode = GROUPS_WITH_CHILDREN`:烘焙时**只解析"在某个
    Godot group 里的节点及其子节点"**。于是导出侧只要:
      * 每个碰撞体按『寻路层』写进 `gk_nav_layer_N` 组;
      * 每层一块 NavigationRegion(层 1 = `Navigation`,层 2 = `Navigation_2`),
        各自网格子资源指向自己的组、`navigation_layers = 1 << (N-1)`;
      * agent 查询带层掩码 `map_get_path(..., true, mask)`。
    **不用挪碰撞体节点** —— 挪了会牵连材质挂载 / 动态障碍父路径 /
    碰撞块合并一大片(所以选 group 模式)。

    ── 老图契约 ────────────────────────────────────────────────────
    没用到多层的老场景:`groups` / `navigation_layers` / `map_get_path`
    的第 5 个参数**一个字都不写**(逐字不变)—— 唯一必然变化的是 AI 那个
    公共 helper 的签名加了默认形参 `layers: int = 1`(行为和以前完全一样)。
    """

    def _physics(self, **props):
        return types.SimpleNamespace(**props)

    def _scene(self, layers, nav=True):
        objs = []
        for index, layer in enumerate(layers):
            objs.append(types.SimpleNamespace(
                type="MESH", name=f"物{index}",
                gk_physics=types.SimpleNamespace(nav_layer=layer)))
        return types.SimpleNamespace(
            objects=objs,
            gk_settings=types.SimpleNamespace(nav_enabled=nav))

    def test_layer_of_default_and_clamp(self):
        from BlenderGameKit import asset_export as ae
        self.assertEqual(ae.nav_layer_of(None), 1)
        self.assertEqual(ae.nav_layer_of(self._physics()), 1)
        self.assertEqual(ae.nav_layer_of(self._physics(nav_layer=2)), 2)
        self.assertEqual(ae.nav_layer_of(self._physics(nav_layer=4)), 4)
        # 越界钳住:0 / 负数 -> 第 1 层;太大 -> 第 4 层
        self.assertEqual(ae.nav_layer_of(self._physics(nav_layer=0)), 1)
        self.assertEqual(ae.nav_layer_of(self._physics(nav_layer=-3)), 1)
        self.assertEqual(ae.nav_layer_of(self._physics(nav_layer=9)), 4)

    def test_layer_mask(self):
        from BlenderGameKit import asset_export as ae
        self.assertEqual(ae.nav_layer_mask(1), 1)
        self.assertEqual(ae.nav_layer_mask(2), 2)
        self.assertEqual(ae.nav_layer_mask(3), 4)
        self.assertEqual(ae.nav_layer_mask(4), 8)
        # 越界的层也钳到 1~4(和 nav_layer_of 同一套口径)
        self.assertEqual(ae.nav_layer_mask(0), 1)
        self.assertEqual(ae.nav_layer_mask(99), 8)

    def test_group_name_and_header(self):
        from BlenderGameKit import asset_export as ae
        self.assertEqual(ae.nav_group_name(2), "gk_nav_layer_2")
        # 单层模式:一个字都不写(老场景逐字不变)
        self.assertEqual(
            ae.nav_group_header(self._physics(nav_layer=2), False), "")
        # 多层模式:层 2 的物体写自己的组;层 1 的**也要写** ——
        # 不写的话层 1 的网格会按"根下所有静态碰撞体"烤,把层 2 的几何
        # 也烤进去(实测:层 1 的人能走到层 2 的平台上)
        # ⚠ 必须写在**节点头**里:写成单独一行属性 Godot 会丢掉整个节点
        self.assertEqual(ae.nav_group_header(self._physics(nav_layer=2), True),
                         ' groups=["gk_nav_layer_2"]')
        self.assertEqual(ae.nav_group_header(self._physics(), True),
                         ' groups=["gk_nav_layer_1"]')

    def test_extra_layers_and_region_names(self):
        from BlenderGameKit import asset_export as ae
        single = self._scene([1, 1])
        self.assertEqual(ae.nav_extra_layers(single), [])
        self.assertFalse(ae.nav_layers_active(single))
        self.assertEqual(ae.nav_region_names(single), [(1, "Navigation")])
        multi = self._scene([1, 3, 2, 3])
        self.assertEqual(ae.nav_extra_layers(multi), [2, 3])
        self.assertTrue(ae.nav_layers_active(multi))
        self.assertEqual(ae.nav_region_names(multi),
                         [(1, "Navigation"), (2, "Navigation_2"),
                          (3, "Navigation_3")])
        # 场景没勾『寻路』-> 没有层可言(也不会导出任何导航东西)
        self.assertEqual(ae.nav_extra_layers(self._scene([1, 2], nav=False)), [])
        # 空场景 / 没有物理设置的物体都不算
        self.assertEqual(ae.nav_extra_layers(types.SimpleNamespace(
            objects=[types.SimpleNamespace(type="CAMERA", name="相机")],
            gk_settings=types.SimpleNamespace(nav_enabled=True))), [])

    def test_subresource_carries_group(self):
        from BlenderGameKit import asset_export as ae
        params = {"radius": "0.5", "height": "1.8", "slope": "45.0"}
        text3 = ae.nav_subresource_text(2, True, params)
        self.assertIn('id="gk_navmesh_2"', text3)
        self.assertIn("geometry_source_geometry_mode = 1", text3)
        self.assertIn('geometry_source_group_name = "gk_nav_layer_2"', text3)
        # 层 1 的老写法:不带组(老场景逐字不变)
        self.assertNotIn("group_name", ae.nav_subresource_text(1, True, params))
        text2 = ae.nav_subresource_text(2, False, params)
        self.assertIn('id="gk_navpoly_2"', text2)
        self.assertIn("source_geometry_mode = 1", text2)
        self.assertIn('source_geometry_group_name = "gk_nav_layer_2"', text2)

    def test_region_node_text(self):
        from BlenderGameKit import asset_export as ae
        text = ae.nav_region_node_text(2, True, "Navigation_2")
        self.assertIn('[node name="Navigation_2" type="NavigationRegion3D"',
                      text)
        self.assertIn("navigation_layers = 2", text)
        # 层 1 照旧:不写 navigation_layers(默认就是 1,老场景逐字不变)
        self.assertNotIn("navigation_layers",
                         ae.nav_region_node_text(1, True, "Navigation"))
        text2 = ae.nav_region_node_text(3, False, "Navigation_3")
        self.assertIn('type="NavigationRegion2D"', text2)
        self.assertIn("navigation_layers = 4", text2)

    def test_env_script_bakes_every_layer(self):
        """环境脚本:一层一块 region 都要烤;2D 的外框按**本层的组**算。"""
        from BlenderGameKit import asset_export as ae
        text = ae.render_env_script()
        for marker in ("_gk_nav_regions", "source_geometry_group_name",
                       "navigation_layers"):
            self.assertIn(marker, text)

    def test_property_registered_on_physics(self):
        from BlenderGameKit import properties
        names = set(getattr(properties.GKObjectSettings, "__annotations__", {}))
        self.assertIn("nav_layer", names)

    def test_navigate_node_layer_argument(self):
        tree = make_tree()
        physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        chaser = tree.new_node(NN.GKNodeNavigate, "追玩家", gk_goal="玩家",
                               gk_nav_layer=2)
        gk_stub.connect(tree, physics, "执行", chaser, "执行")
        code, _ = gen(tree, dict(V3, nav_enabled=True))
        self.assertIn("map_get_path(", code)
        self.assertIn("true, 2)", code)

    def test_navigate_default_layer_has_no_extra_argument(self):
        tree = make_tree()
        physics = tree.new_node(NN.GKNodeOnPhysicsProcess, "物理帧")
        chaser = tree.new_node(NN.GKNodeNavigate, "追玩家", gk_goal="玩家")
        gk_stub.connect(tree, physics, "执行", chaser, "执行")
        code, _ = gen(tree, dict(V3, nav_enabled=True))
        self.assertIn(", true)", code)
        self.assertNotIn("true, 1)", code)

    def _ai_tree(self, name="敌人AI"):
        ai = gk_stub.StubNodeTree(name)
        ai.bl_idname = "GKGameAiTree"
        return ai

    def _gen_with_ai(self, ai, tree, options=None):
        original = getattr(bpy_module(), "data", None)
        setattr(bpy_module(), "data", types.SimpleNamespace(node_groups=[ai]))
        try:
            return gen(tree, options or V3)
        finally:
            if original is None:
                try:
                    delattr(bpy_module(), "data")
                except AttributeError:
                    pass
            else:
                setattr(bpy_module(), "data", original)

    def _chase_tree(self, ai, **props):
        state = ai.new_node(NN.GKNodeAiState, "追", gk_state_name="追",
                            gk_behavior="CHASE", gk_goal="玩家",
                            gk_speed=4.0, gk_stop_distance=1.2,
                            gk_facing="GOAL", **props)
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        enable = tree.new_node(NN.GKNodeAiEnable, "启用AI", gk_target="玩家")
        gk_stub.connect(tree, event, "执行", enable, "执行")
        return tree, state

    def test_ai_state_layer_argument(self):
        """『AI_状态』填了『走哪层』-> 公共 helper 带上层掩码。"""
        ai = self._ai_tree()
        tree, _state = self._chase_tree(ai, gk_nav_layer=2)
        code, _ = self._gen_with_ai(ai, tree, dict(V3, nav_enabled=True))
        self.assertIn("_gk_ai_nav_dir(", code)
        self.assertIn("layers: int = 1", code)
        self.assertIn(", 2)", code)

    def test_ai_state_default_layer_unchanged_behavior(self):
        """没填的 AI 图:helper 多了个默认形参,行为一字不变。"""
        ai = self._ai_tree()
        tree, _state = self._chase_tree(ai)
        code, _ = self._gen_with_ai(ai, tree, dict(V3, nav_enabled=True))
        self.assertIn("layers: int = 1", code)
        self.assertNotIn(", 2)", code)


class TestRound81UiPolish(Base):
    """第八十一轮(F 组 15 + 16):界面收尾 —— 皮肤 / 滚动条 / 选中高亮 /
    软键盘·IME / 多行输入框 / 点击音效 / 字幕立绘 / 历史导出·跳回 /
    音效循环。

    老图契约:**每一个子项都是"填了才有"**。没填的卡片生成物互相不变 ——
    唯一的两处必然变化(helper 签名加默认形参、历史点按处理里多一句
    运行期开关判断)在这一轮的说明里写清楚了。
    """

    def _ready(self, tree, name="准备"):
        return tree.new_node(NN.GKNodeOnReady, name)

    def _chain(self, tree, *nodes):
        """串一条执行链:准备 -> n1 -> n2 -> …(每个节点用『完成』接下一个)。"""
        event = self._ready(tree)
        previous = None
        for node in nodes:
            gk_stub.connect(tree, event if previous is None else previous,
                            "执行" if previous is None else "完成",
                            node, "执行")
            previous = node
        return event

    # ---- 卡片 / 属性登记 ----

    def test_new_nodes_and_props_registered(self):
        skin = getattr(NN, "GKNodeUiSkin", None)
        self.assertIsNotNone(skin, "界面_皮肤 没注册")
        self.assertEqual(skin.bl_label, "界面_皮肤")
        skin_props = {entry[0] for entry in skin.gk_prop_defs}
        for key in ("gk_font_size", "gk_font_color", "gk_panel_color",
                    "gk_button_color", "gk_button_hover", "gk_button_press",
                    "gk_focus_color", "gk_scroll_color", "gk_radius"):
            self.assertIn(key, skin_props, f"皮肤缺属性 {key}")

        edit = getattr(NN, "GKNodeUiTextEdit", None)
        self.assertIsNotNone(edit, "界面_多行输入框 没注册")
        self.assertEqual(edit.bl_label, "界面_多行输入框")
        edit_props = {entry[0] for entry in edit.gk_prop_defs}
        for key in ("gk_text", "gk_placeholder", "gk_wrap", "gk_readonly",
                    "gk_width", "gk_height"):
            self.assertIn(key, edit_props, f"多行输入框缺属性 {key}")
        outs = [pin["name"] for pin in edit.gk_outputs]
        self.assertIn("提交", outs)

        # 老卡片上的新栏位
        input_props = {entry[0] for entry in NN.GKNodeUiInput.gk_prop_defs}
        self.assertIn("gk_ime", input_props)
        button_props = {entry[0] for entry in NN.GKNodeUiButton.gk_prop_defs}
        self.assertIn("gk_click_sound", button_props)
        self.assertIn("gk_click_volume", button_props)
        sub_props = {entry[0] for entry in NN.GKNodeUiSubtitle.gk_prop_defs}
        self.assertIn("gk_image", sub_props)
        hist_props = {entry[0] for entry in NN.GKNodeDialogHistory.gk_prop_defs}
        self.assertIn("gk_export_path", hist_props)
        self.assertIn("gk_replay", hist_props)
        sound_props = {entry[0] for entry in NN.GKNodePlaySound.gk_prop_defs}
        self.assertIn("gk_loop", sound_props)
        self.assertIn("gk_name", sound_props)
        stop_props = {entry[0] for entry in NN.GKNodeStopSound.gk_prop_defs}
        self.assertIn("gk_name", stop_props)

    # ---- 15① 滚动条美化 + 15② 选中高亮 + 15⑥ 界面皮肤(一个节点全给)----

    def test_skin_builds_and_applies_a_theme(self):
        tree = make_tree()
        skin = tree.new_node(NN.GKNodeUiSkin, "皮肤", gk_font_size=22)
        self._chain(tree, skin)
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        # 一套主题 + 默认字号
        self.assertRegex(code, r"_gk_ui_theme\d+ := Theme\.new\(\)")
        self.assertRegex(code, r"_gk_ui_theme\d+\.default_font_size = 22")
        # 15② 选中高亮 = Button 的 focus 样式(键盘 / 手柄焦点看得见)
        self.assertRegex(code, r'set_stylebox\("focus", "Button", ')
        self.assertRegex(code, r'set_stylebox\("normal", "Button", ')
        self.assertRegex(code, r'set_stylebox\("hover", "Button", ')
        self.assertRegex(code, r'set_stylebox\("pressed", "Button", ')
        # 15① 滚动条:V / H 两条都换轨道 + 滑块
        self.assertIn('["VScrollBar", "HScrollBar"]', code)
        self.assertRegex(code, r'set_stylebox\("scroll", ')
        self.assertRegex(code, r'set_stylebox\("grabber", ')
        self.assertRegex(code, r'set_stylebox\("grabber_highlight", ')
        # 面板 / 输入框 / 进度条也一起收进来
        self.assertRegex(code, r'set_stylebox\("panel", "Panel", ')
        self.assertRegex(code, r'set_stylebox\("normal", "LineEdit", ')
        self.assertRegex(code, r'set_stylebox\("normal", "TextEdit", ')
        self.assertRegex(code, r'set_stylebox\("fill", "ProgressBar", ')
        # 文字色给常用类型
        self.assertRegex(code, r'set_color\("font_color", _gk_ui_t\d+')
        # 建根 + 挂到根上(控件后建的也继承)
        self.assertIn("_gk_ensure_ui(self)", code)
        self.assertIn("_gk_ui_root.theme = ", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_without_skin_nothing_changes(self):
        """没放皮肤 -> 一个主题字眼都不许有(老图不变)。"""
        tree = make_tree()
        button = tree.new_node(NN.GKNodeUiButton, "开始", gk_text="开始")
        self._chain(tree, button)
        code, _ = gen(tree, V3)
        self.assertNotIn("Theme.new()", code)
        self.assertNotIn("_gk_ui_root.theme", code)

    # ---- 15③ 输入框软键盘 / IME ----

    def test_input_ime_opt_in(self):
        tree = make_tree()
        field = tree.new_node(NN.GKNodeUiInput, "昵称", gk_ime=True)
        self._chain(tree, field)
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("virtual_keyboard_enabled = true", code)
        self.assertIn("DisplayServer.window_set_ime_active(true)", code)
        self.assertIn("DisplayServer.window_set_ime_active(false)", code)
        self.assertIn("DisplayServer.window_set_ime_position(", code)
        self.assertIn("focus_entered.connect(", code)
        self.assertIn("text_changed.connect(", code)
        self.assertIn("func _gk_ime_update(edit: Control) -> void:", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_input_ime_off_by_default(self):
        tree = make_tree()
        field = tree.new_node(NN.GKNodeUiInput, "昵称")
        self._chain(tree, field)
        code, _ = gen(tree, V3)
        self.assertNotIn("window_set_ime_active", code)
        self.assertNotIn("virtual_keyboard_enabled", code)
        self.assertNotIn("_gk_ime_update", code)

    # ---- 15④ 多行输入框 ----

    def test_multiline_input_submits_on_ctrl_enter(self):
        tree = make_tree()
        field = tree.new_node(NN.GKNodeUiTextEdit, "聊天框", gk_text="默认",
                              gk_placeholder="说点什么…", gk_width=360.0,
                              gk_height=120.0)
        later = tree.new_node(NN.GKNodePrint, "发出去了", gk_text="发送")
        gk_stub.connect(tree, field, "提交", later, "执行")
        self._chain(tree, field)
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("TextEdit.new()", code)
        self.assertIn("TextEdit.LINE_WRAPPING_BOUNDARY", code)
        self.assertIn("gui_input.connect(", code)
        self.assertIn("event.keycode != KEY_ENTER", code)
        self.assertIn("event.keycode != KEY_KP_ENTER", code)
        self.assertIn("if not event.ctrl_pressed:", code)
        # 链死在提交回调里(不是建出来就跑)
        funcs = self.functions(code)
        handler = [name for name in funcs if "submit" in name]
        self.assertTrue(handler, list(funcs))
        self.assertIn('print("发送")', funcs[handler[0]])
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_multiline_input_wrap_and_readonly(self):
        tree = make_tree()
        field = tree.new_node(NN.GKNodeUiTextEdit, "输出窗", gk_wrap=False,
                              gk_readonly=True)
        self._chain(tree, field)
        code, _ = gen(tree, V3)
        self.assertNotIn("LINE_WRAPPING_BOUNDARY", code)
        self.assertIn("editable = false", code)

    # ---- 15⑤ 界面音效(点按钮出声)----

    def test_button_click_sound_opt_in(self):
        tree = make_tree()
        button = tree.new_node(NN.GKNodeUiButton, "开始", gk_text="开始",
                               gk_click_sound="res://art/click.wav",
                               gk_click_volume=-8.0)
        self._chain(tree, button)
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn('load("res://art/click.wav")', code)
        self.assertIn("AudioStreamPlayer.new()", code)
        self.assertIn("volume_db = -8.0", code)
        self.assertRegex(code, r"\.pressed\.connect\(_gk_ui_cb\d+\)")
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_button_without_click_sound_is_old(self):
        tree = make_tree()
        button = tree.new_node(NN.GKNodeUiButton, "开始", gk_text="开始")
        self._chain(tree, button)
        code, _ = gen(tree, V3)
        self.assertNotIn("AudioStreamPlayer", code)

    # ---- 16① 字幕说话人立绘 ----

    def test_subtitle_portrait_opt_in(self):
        tree = make_tree()
        sub = tree.new_node(NN.GKNodeUiSubtitle, "旁白", gk_text="早上好",
                            gk_image="res://art/立绘.png")
        self._chain(tree, sub)
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        # 第 10 个实参就是立绘路径
        self.assertIn('48.0, "res://art/立绘.png")', code)
        # helper 加了带默认值的形参(老调用点 9 个实参照样合法)
        self.assertIn('margin: float, portrait: String = "") -> void:', code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_subtitle_without_portrait_call_unchanged(self):
        tree = make_tree()
        sub = tree.new_node(NN.GKNodeUiSubtitle, "旁白", gk_text="早上好")
        self._chain(tree, sub)
        code, _ = gen(tree, V3)
        self.assertIn('_gk_subtitle_show(self, "", str("早上好"), 30.0, 3.0, '
                      '0.3, 720.0, 110.0, 48.0)', code)

    # ---- 16② 对话历史:导出 / 点句重放 ----

    def test_history_export_and_replay_opt_in(self):
        tree = make_tree()
        hist = tree.new_node(NN.GKNodeDialogHistory, "回顾",
                             gk_export_path="user://对话历史.txt",
                             gk_replay=True)
        self._chain(tree, hist)
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        # 调用点把两个开关传进去
        self.assertIn('"还没有说过话", "user://对话历史.txt", true)', code)
        # 导出:按钮 + 写文件
        self.assertIn("func _gk_history_export(path: String) -> void:", code)
        self.assertIn("FileAccess.open(path, FileAccess.WRITE)", code)
        self.assertIn("file.store_line(", code)
        # 点句重放:行变成按钮,点了那一句像字幕一样再飘一遍
        self.assertIn("func _gk_history_replay(host: Node, who: String, "
                      "text: String) -> void:", code)
        self.assertIn("_gk_subtitle_show(host, who, text,", code)
        funcs = self.functions(code)
        rows = funcs.get("_gk_history_show", "")
        self.assertIn("Button.new()", rows)
        self.assertIn("_gk_history_replay(host,", rows)
        # 运行期开关 + 点面板里不关、点面板外才关
        self.assertIn("var _gk_history_replay_on: bool = false", code)
        self.assertIn("_gk_history_replay_on = true", code)
        self.assertIn("get_global_rect().has_point(event.position)", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_history_defaults_are_old(self):
        tree = make_tree()
        hist = tree.new_node(NN.GKNodeDialogHistory, "回顾")
        self._chain(tree, hist)
        code, _ = gen(tree, V3)
        self.assertIn('_gk_history_show(self, "对话回顾", 720.0, 380.0, 18, '
                      '"还没有说过话")', code)
        self.assertIn("_gk_history_replay_on = false", code)
        self.assertNotIn("FileAccess.open(", code)
        self.assertNotIn("func _gk_history_replay(", code)
        self.assertNotIn("func _gk_history_export(", code)

    # ---- 16③ 音效循环 ----

    def test_loop_sound_registers_and_does_not_self_free(self):
        tree = make_tree()
        sound = tree.new_node(NN.GKNodePlaySound, "引擎声",
                              gk_path="res://art/engine.wav", gk_loop=True,
                              gk_name="引擎")
        self._chain(tree, sound)
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("var _gk_loops: Dictionary = {}", code)
        # 名字进了字典(键先存进一个变量,再 has / 取 / 覆盖)
        self.assertRegex(code, r'var _sfx_key\d+ := "引擎"')
        self.assertRegex(code, r"_gk_loops\[_sfx_key\d+\]")
        self.assertRegex(code, r"_gk_loops\.has\(_sfx_key\d+\)")
        # 三种流格式都要设循环(和 BGM 同一套)
        self.assertIn("AudioStreamOggVorbis", code)
        self.assertIn("AudioStreamMP3", code)
        self.assertIn("AudioStreamWAV", code)
        # wav 只设 loop_mode 的话循环长度是 0,流当场结束(真机抓到的)
        self.assertIn("loop_end = int(", code)
        # 循环音**不许**自己删(那个 finished 永远不会来)
        self.assertNotIn("finished.connect(", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_loop_sound_without_name_uses_node_name(self):
        tree = make_tree()
        sound = tree.new_node(NN.GKNodePlaySound, "风声",
                              gk_path="res://art/wind.wav", gk_loop=True)
        self._chain(tree, sound)
        code, _ = gen(tree, V3)
        self.assertRegex(code, r'var _sfx_key\d+ := "风声"')

    def test_named_stop_stops_loop_sound_only(self):
        tree = make_tree()
        stop = tree.new_node(NN.GKNodeStopSound, "停引擎", gk_name="引擎")
        self._chain(tree, stop)
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertRegex(code, r'var _stop_key\d+ := "引擎"')
        self.assertRegex(code, r"_gk_loops\.has\(_stop_key\d+\)")
        self.assertIn("erase(", code)
        # 带名字 = 只停循环音,不许碰 BGM
        self.assertNotIn("_gk_music", code)

    def test_plain_stop_is_still_music(self):
        tree = make_tree()
        stop = tree.new_node(NN.GKNodeStopSound, "停BGM")
        self._chain(tree, stop)
        code, _ = gen(tree, V3)
        self.assertIn("_gk_music", code)
        self.assertNotIn("_gk_loops", code)

    def test_plain_sound_self_frees_as_before(self):
        tree = make_tree()
        sound = tree.new_node(NN.GKNodePlaySound, "跳跃",
                              gk_path="res://art/jump.wav")
        self._chain(tree, sound)
        code, _ = gen(tree, V3)
        self.assertIn("finished.connect(", code)
        self.assertNotIn("_gk_loops", code)

    # ---- 一张全开的图:格式体检 ----

    def test_all_round81_features_together(self):
        tree = make_tree()
        skin = tree.new_node(NN.GKNodeUiSkin, "皮肤")
        button = tree.new_node(NN.GKNodeUiButton, "开始", gk_text="开始",
                               gk_click_sound="res://art/click.wav")
        field = tree.new_node(NN.GKNodeUiInput, "昵称", gk_ime=True)
        chat = tree.new_node(NN.GKNodeUiTextEdit, "聊天框")
        sub = tree.new_node(NN.GKNodeUiSubtitle, "旁白", gk_text="旁白",
                            gk_image="res://art/立绘.png")
        hist = tree.new_node(NN.GKNodeDialogHistory, "回顾",
                             gk_export_path="user://h.txt", gk_replay=True)
        sound = tree.new_node(NN.GKNodePlaySound, "引擎", gk_loop=True,
                              gk_name="引擎")
        stop = tree.new_node(NN.GKNodeStopSound, "停引擎", gk_name="引擎")
        self._chain(tree, skin, button, field, chat, sub, hist, sound, stop)
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_old_graph_still_clean(self):
        """一张只用老功能的界面图:新功能一个字眼都不该出现。"""
        tree = make_tree()
        button = tree.new_node(NN.GKNodeUiButton, "开始", gk_text="开始")
        field = tree.new_node(NN.GKNodeUiInput, "昵称")
        sub = tree.new_node(NN.GKNodeUiSubtitle, "旁白", gk_text="……")
        hist = tree.new_node(NN.GKNodeDialogHistory, "回顾")
        sound = tree.new_node(NN.GKNodePlaySound, "跳",
                              gk_path="res://art/jump.wav")
        self._chain(tree, button, field, sub, hist, sound)
        code, _ = gen(tree, V3)
        for needle in ("Theme.new()", "window_set_ime_active",
                       "_gk_loops", "FileAccess.open(", "TextEdit.new()",
                       "func _gk_history_replay(", "func _gk_ime_update("):
            self.assertNotIn(needle, code, needle)


class TestRound86LogicInfo(Base):
    """第八十六轮(E 组 13):『游戏逻辑信息』—— 侧栏信息块的判据(纯函数)。

    这块的价值全在**体检**:error / warn / info 三档,把"导出来才发现"
    的问题提前说出来(没事件节点 / 没挂脚本 / 两个物体互相覆盖 /
    用了寻路但导航关着 / 端口声明了没人写)。判据是纯函数,
    这里喂假事实直接断言;侧栏那边只负责收集事实。
    """

    @staticmethod
    def _facts(**overrides):
        values = dict(
            tree_name="玩家逻辑",
            tree_reason="只有一棵游戏逻辑图,自动使用",
            event_count=1,
            carrier_count=1,
            carrier_names=["角色"],
            uses_pathfinding=False,
            nav_enabled=False,
            declared_ports=(),
            written_ports=(),
            disabled_trees=0,
        )
        values.update(overrides)
        return values

    def test_inventory_counts_kinds_and_nodes(self):
        from BlenderGameKit import logic_info
        groups = [
            types.SimpleNamespace(bl_idname="GKGameLogicTree", nodes=[1, 2, 3]),
            types.SimpleNamespace(bl_idname="GKGameLogicTree", nodes=[1]),
            types.SimpleNamespace(bl_idname="GKAnimationTree", nodes=[1, 2]),
            types.SimpleNamespace(bl_idname="GKMusicTree", nodes=[]),
            # 认不出的类型:计进总数,但不进分类统计(不许抛异常)
            types.SimpleNamespace(bl_idname="SomeOtherTree", nodes=[1]),
        ]
        data = logic_info.inventory(groups)
        self.assertEqual(data["total"], 5)
        self.assertEqual(data["nodes"], 7)
        self.assertEqual(data["kinds"], {"逻辑": 2, "动画": 1, "音乐": 1})
        self.assertIn("逻辑 2", logic_info.inventory_text(groups))
        self.assertEqual(logic_info.inventory_text([]), "还没有节点图")

    def test_clean_scene_has_no_rows(self):
        from BlenderGameKit import logic_info
        self.assertEqual(logic_info.checkup(self._facts()), [])
        self.assertEqual(logic_info.checkup_summary([]), "一切正常")

    def test_missing_tree_says_why(self):
        """没解析到图 = 直接把 `resolve_scene_tree` 的说明摆出来。"""
        from BlenderGameKit import logic_info
        rows = logic_info.checkup(self._facts(
            tree_name="",
            tree_reason="有 2 棵游戏逻辑图,但这个场景没选 —— 请在『导出』面板给场景指定一棵"))
        self.assertEqual(rows[0][0], "error")
        self.assertIn("这个场景没选", rows[0][1])
        # 拿不到理由时的兜底
        rows = logic_info.checkup(self._facts(tree_name="", tree_reason=""))
        self.assertTrue(rows[0][1])

    def test_no_event_node_is_error(self):
        from BlenderGameKit import logic_info
        rows = logic_info.checkup(self._facts(event_count=0))
        self.assertEqual([level for level, _ in rows], ["error"])
        self.assertIn("事件节点", rows[0][1])

    def test_no_carrier_is_warn_with_hint(self):
        from BlenderGameKit import logic_info
        rows = logic_info.checkup(self._facts(carrier_count=0,
                                              carrier_names=[]))
        self.assertEqual(rows[0][0], "warn")
        self.assertIn("脚本挂载目标", rows[0][1])

    def test_two_carriers_is_error_with_names(self):
        """多于一个挂脚本 = 会互相覆盖(后写的赢)—— 点名是哪几个。"""
        from BlenderGameKit import logic_info
        rows = logic_info.checkup(self._facts(carrier_count=2,
                                              carrier_names=["角色", "敌人"]))
        self.assertEqual(rows[0][0], "error")
        self.assertIn("角色", rows[0][1])
        self.assertIn("敌人", rows[0][1])

    def test_pathfinding_hint_only_when_nav_off(self):
        from BlenderGameKit import logic_info
        rows = logic_info.checkup(self._facts(uses_pathfinding=True))
        self.assertEqual(rows[0][0], "info")
        self.assertIn("寻路", rows[0][1])
        # 导航开着就不再提这一条
        self.assertEqual(
            logic_info.checkup(self._facts(uses_pathfinding=True,
                                           nav_enabled=True)), [])

    def test_unwritten_port_is_reported(self):
        """端口声明了、但图里没有『逻辑_输出结果』写它 —— 别的图读到空值。"""
        from BlenderGameKit import logic_info
        rows = logic_info.checkup(self._facts(
            declared_ports=["完成", "胜利"], written_ports=["完成"]))
        self.assertEqual(len(rows), 1)
        self.assertIn("胜利", rows[0][1])

    def test_disabled_trees_noted(self):
        from BlenderGameKit import logic_info
        rows = logic_info.checkup(self._facts(disabled_trees=2))
        self.assertEqual(rows[0][0], "info")
        self.assertIn("2 张图停用", rows[0][1])

    def test_summary_counts_levels(self):
        from BlenderGameKit import logic_info
        # 错误 + 提示(没绑图 + 有图停用)
        rows = logic_info.checkup(self._facts(
            tree_name="", tree_reason="没有逻辑图", disabled_trees=1))
        self.assertEqual(logic_info.checkup_summary(rows),
                         "1 个错误 / 1 条提示")
        # 警告 + 提示(有图,但没人挂脚本)
        rows = logic_info.checkup(self._facts(
            carrier_count=0, carrier_names=[], disabled_trees=1))
        self.assertEqual(logic_info.checkup_summary(rows),
                         "1 条警告 / 1 条提示")


class TestRound86MusicSet(Base):
    """第八十六轮(E 组 14):『游戏音乐』设置图 —— 音量 / 循环 / 场景规则。

    形态照『游戏显示』先例(用户点的新节点树):两张设置卡直接读写
    **当前场景**的设置,产物落进环境脚本。

    判据:**默认值 = 老行为** —— 循环开、BGM 走 Master、总线音量不应用,
    这几件事不勾时运行时和以前一模一样(产物里只多几行默认值字段)。
    """

    @staticmethod
    def _scene(**overrides):
        values = dict(dimension="3", scene_bgm="", scene_bgm_volume=-6.0,
                      scene_bgm_loop=True, scene_bgm_bus="",
                      music_bus="Music", music_bus_db=-6.0,
                      music_bus_apply=False)
        values.update(overrides)
        return types.SimpleNamespace(gk_settings=types.SimpleNamespace(**values))

    def test_cards_list_and_missing(self):
        from BlenderGameKit.nodes import nodes as NN
        self.assertEqual(NN.MUSIC_CARD_IDS,
                         ("GKNodeMusicSceneBgm", "GKNodeMusicBus"))
        for blob in NN.MUSIC_CARD_IDS:
            self.assertTrue(any(cls.bl_idname == blob for cls in NN.ALL_CLASSES),
                            blob)
        tree = types.SimpleNamespace(nodes=[
            types.SimpleNamespace(bl_idname="GKNodeMusicSceneBgm")])
        self.assertEqual(NN.missing_music_cards(tree), ["GKNodeMusicBus"])
        self.assertEqual(NN.missing_music_cards(types.SimpleNamespace(nodes=[])),
                         list(NN.MUSIC_CARD_IDS))

    def test_cards_belong_to_music_tree_only(self):
        """两张卡只出现在『游戏音乐』树里(和显示卡一个道理)。"""
        from BlenderGameKit import properties as props
        from BlenderGameKit.nodes import nodes as NN
        for blob in NN.MUSIC_CARD_IDS:
            cls = next(c for c in NN.ALL_CLASSES if c.bl_idname == blob)
            self.assertTrue(props.node_fits_tree(cls, "GKMusicTree"), blob)
            self.assertFalse(props.node_fits_tree(cls, "GKGameLogicTree"), blob)

    def test_rows_summarize_settings(self):
        from BlenderGameKit.nodes import nodes as NN
        # 函数吃的是**场景设置对象**(scene.gk_settings),不是场景
        rows = NN.music_settings_rows(self._scene().gk_settings)
        self.assertEqual(rows[0], ("开局 BGM", "留空(不播)", False))
        self.assertEqual(rows[1][0], "总线音量")
        self.assertFalse(rows[1][2])

        rows = NN.music_settings_rows(self._scene(
            scene_bgm=r"D:\snd\主题曲.ogg", scene_bgm_volume=-12.0,
            scene_bgm_loop=False, scene_bgm_bus="Music",
            music_bus_apply=True, music_bus_db=-3.0).gk_settings)
        self.assertTrue(rows[0][2])
        self.assertIn("-12", rows[0][1])
        self.assertIn("播一遍", rows[0][1])
        self.assertIn("Music", rows[0][1])
        self.assertEqual(rows[1][2], True)
        self.assertIn("-3", rows[1][1])
        # 没有设置对象也不许炸
        self.assertEqual(NN.music_settings_rows(None), [])

    def test_defaults_are_old_behavior(self):
        """不碰音乐设置图的 .blend:三个字段都是老行为。"""
        from BlenderGameKit import asset_export as ae
        text = ae.render_env_script()
        self.assertIn("bgm_loop: bool = true", text)
        self.assertIn('bgm_bus: String = ""', text)
        self.assertIn("music_bus_apply: bool = false", text)

    def test_loop_off_is_written(self):
        from BlenderGameKit import asset_export as ae
        fields = ae._env_fields(self._scene(scene_bgm=r"D:\a.ogg",
                                            scene_bgm_loop=False))
        rendered = ae.ENVIRONMENT_SCRIPT.format(**fields)
        self.assertIn("bgm_loop: bool = false", rendered)
        # wav 那条分支:不循环要写 LOOP_DISABLED(以前写死 LOOP_FORWARD)
        self.assertIn("LOOP_DISABLED", rendered)
        # 老场景(默认)那条分支仍然是 LOOP_FORWARD
        default = ae.ENVIRONMENT_SCRIPT.format(**ae._env_fields(self._scene()))
        self.assertIn("LOOP_FORWARD", default)

    def test_bus_names_are_escaped(self):
        """总线名里的引号不许把生成的脚本弄坏。"""
        from BlenderGameKit import asset_export as ae
        fields = ae._env_fields(self._scene(
            scene_bgm_bus='Mu"sic\\x', music_bus='A"B'))
        rendered = ae.ENVIRONMENT_SCRIPT.format(**fields)
        self.assertIn('bgm_bus: String = "Mu\\"sic\\\\x"', rendered)
        self.assertIn('music_bus: String = "A\\"B"', rendered)

    def test_bus_apply_writes_runtime_call(self):
        from BlenderGameKit import asset_export as ae
        fields = ae._env_fields(self._scene(music_bus="Music",
                                            music_bus_db=-9.5,
                                            music_bus_apply=True))
        rendered = ae.ENVIRONMENT_SCRIPT.format(**fields)
        self.assertIn("music_bus_apply: bool = true", rendered)
        self.assertIn("music_bus_db: float = -9.5", rendered)
        self.assertIn("_gk_apply_music_bus()", rendered)
        self.assertIn("AudioServer.get_bus_index(music_bus)", rendered)
        # 不勾 = 一行都不多(判定函数是同一处真相)
        self.assertFalse(ae._music_bus_apply_on(self._scene()))
        self.assertTrue(ae._music_bus_apply_on(
            self._scene(music_bus_apply=True)))

    def test_bus_apply_counts_as_2d_work(self):
        """2D 场景本来"没活干"就不写环境脚本 —— 勾了总线音量也算一件活。"""
        from BlenderGameKit import asset_export as ae
        _node, files, _cam = ae.build_environment_nodes(
            self._scene(dimension="2", music_bus_apply=True),
            is_3d=False, scene_name="World")
        self.assertTrue(files, "只勾了总线音量的 2D 场景也该有环境脚本")
        _node, files, _cam = ae.build_environment_nodes(
            self._scene(dimension="2"), is_3d=False, scene_name="World")
        self.assertFalse(files, "什么都没开的 2D 场景:还是老行为(不写)")


class TestRound87LevelPresets(Base):
    """第八十七轮(I 组 24):三种关卡预设模板(2D 闯关 / 3D 关卡 / 2.5D)。

    "一键建一关":新场景 + 地面 / 角色 / 相机 + 连好的起步逻辑图
    (见 levels.py)。这里测**图那一半**(离线可跑):三条起步链的
    节点 / 连线 / 生成物;建场景 / 摆物体那一半归真 Blender 的冒烟。
    """

    def _tree(self, template, **params):
        from BlenderGameKit import levels
        tree = _PresetTree(f"{template}逻辑")
        levels._CHAIN_BUILDERS[template](tree, params)
        return tree

    def test_template_items_shape(self):
        from BlenderGameKit import levels
        items = levels.template_items()
        self.assertEqual([entry[0] for entry in items], ["2d", "3d", "25d"])
        for key, label, hint in items:
            self.assertTrue(label and hint, key)
            self.assertIn(key, levels.TEMPLATE_HINTS)
        self.assertEqual([entry[1] for entry in items],
                         [label for _key, label in levels.TEMPLATES])

    def test_2d_chain_runs(self):
        """2D 链:方向键走 + 空格跳 + 相机跟随,生成的脚本要真的能跑。"""
        tree = self._tree("2d")
        kinds = [node.bl_idname for node in tree.tree.nodes]
        for want in ("GKNodeOnReady", "GKNodeOnPhysicsProcess",
                     "GKNodeInputDirection", "GKNodeMoveDirection",
                     "GKNodeCameraFollow", "GKNodeJump", "GKNodeBranch",
                     "GKNodeIsOnFloor", "GKNodeKey"):
            self.assertIn(want, kinds, want)
        code, warnings = codegen.generate(tree.tree, dict(V2))
        self.assertEqual(warnings, [], warnings)
        self.assertIn("move_and_slide()", code)
        self.assertIn("ACTION_JUMP", code)
        self.assertIn("_gk_follow_cam", code)          # 相机跟随
        # 2D 场景没相机时脚本自己造一台 Camera2D(模板不摆相机,靠这条兜底)
        self.assertIn("_gk_ensure_camera2d", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_2d_params_reach_the_graph(self):
        tree = self._tree("2d", move_speed=320.0, jump_force=600.0)
        by_name = {node.name: node for node in tree.tree.nodes}
        self.assertEqual(by_name["移动_按方向"].gk_speed, 320.0)
        self.assertTrue(by_name["移动_按方向"].gk_keep_y)   # 平台跳跃必须有重力
        self.assertEqual(by_name["起跳"].gk_jump_force, 600.0)

    def test_3d_chain_is_the_character_preset(self):
        """3D 链直接复用『角色预设』那张跑通的图(不重抄一份)。"""
        tree = self._tree("3d")
        kinds = [node.bl_idname for node in tree.tree.nodes]
        for want in ("GKNodeMouseMode", "GKNodeMouseMotion",
                     "GKNodeCameraSpringArm", "GKNodeJump"):
            self.assertIn(want, kinds, want)
        code, warnings = codegen.generate(tree.tree, dict(V3))
        self.assertEqual(warnings, [], warnings)
        self.assertIn("Input.mouse_mode = Input.MOUSE_MODE_CAPTURED", code)
        # 两台相机的名字是约定值(关卡模板会摆在场景里)
        self.assertIn("侧视机位", code)
        self.assertIn("俯瞰机位", code)

    def test_25d_chain_switches_cameras(self):
        """2.5D:固定机位 + C/V 切换,不转视角(和 3D 链的关键差别)。"""
        tree = self._tree("25d")
        by_name = {node.name: node for node in tree.tree.nodes}
        kinds = [node.bl_idname for node in tree.tree.nodes]
        self.assertIn("GKNodeCameraActive", kinds)
        self.assertEqual(by_name["启用侧视机位"].gk_target, "侧视机位")
        switches = [node for node in tree.tree.nodes
                    if node.bl_idname == "GKNodeCameraSetCurrent"]
        self.assertEqual([node.gk_target for node in switches],
                         ["俯瞰机位", "侧视机位"])
        code, warnings = codegen.generate(tree.tree, dict(V3))
        self.assertEqual(warnings, [], warnings)
        # 不转视角(没有鼠标锁定 / 鼠标位移那套)
        self.assertNotIn("Input.mouse_mode", code)
        self.assertIn("make_current()", code)
        self.assertIn("move_and_slide()", code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_every_template_generates_clean_code(self):
        """三种模板一张图:生成物不许告警 / 缩进必须整齐。"""
        for template in ("2d", "3d", "25d"):
            tree = self._tree(template)
            options = dict(V2 if template == "2d" else V3)
            code, warnings = codegen.generate(tree.tree, options)
            self.assertEqual(warnings, [], f"{template}: {warnings}")
            self.assertIn("move_and_slide()", code, template)


class TestRound88QualitySet(Base):
    """第八十八轮(I 组 25):『画质_设置』—— 切画质真的改渲染设置。

    档位 -> 数字的映射是纯函数(`nodes.resolved_quality` + 两张表),
    生成物是"一行字面量 + 一个老老实实设值的 helper"。这里两边都断言:
    卡片上选低档、生成的就是 0.6 / 0 抗锯齿 / 关阴影 这套数字。
    """

    def test_presets_and_custom(self):
        from BlenderGameKit.nodes import nodes as NN
        low = types.SimpleNamespace(gk_level="LOW")
        mid = types.SimpleNamespace(gk_level="MID")
        high = types.SimpleNamespace(gk_level="HIGH")
        self.assertEqual(NN.resolved_quality(low), (0.6, 0, 0))
        self.assertEqual(NN.resolved_quality(mid), (0.85, 1, 2))
        self.assertEqual(NN.resolved_quality(high), (1.0, 2, 3))
        custom = types.SimpleNamespace(gk_level="CUSTOM", gk_scale=0.42,
                                       gk_msaa="4X", gk_shadow="LOW")
        self.assertEqual(NN.resolved_quality(custom), (0.42, 2, 1))
        # 缩放越界会夹回来(0.05 -> 0.1;2.0 -> 1.0)
        self.assertEqual(NN.resolved_quality(types.SimpleNamespace(
            gk_level="CUSTOM", gk_scale=0.05, gk_msaa="OFF",
            gk_shadow="OFF")), (0.1, 0, 0))
        self.assertEqual(NN.resolved_quality(types.SimpleNamespace(
            gk_level="CUSTOM", gk_scale=2.0, gk_msaa="OFF",
            gk_shadow="OFF")), (1.0, 0, 0))

    def test_3d_code_sets_render_settings(self):
        tree = make_tree()
        ready = tree.new_node(NN.GKNodeOnReady, "准备")
        quality = tree.new_node(NN.GKNodeQualitySet, "画质=中",
                                gk_level="MID")
        gk_stub.connect(tree, ready, "执行", quality, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_quality_apply(0.85, 1, 2048, 3)", code)
        self.assertIn("_gk_vp.scaling_3d_scale = scale", code)
        self.assertIn("_gk_vp2.msaa_3d = msaa", code)
        self.assertIn("shadow_enabled = _gk_shadow_on", code)
        self.assertIn("directional_shadow_atlas_set_size(atlas, true)", code)
        self.assertIn("directional_soft_shadow_filter_set_quality(quality)",
                      code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)

    def test_2d_uses_window_scale(self):
        """2D 项目改窗口 content_scale_factor + msaa_2d(实测 4.7 没有
        Viewport.scaling_2d_scale)。"""
        tree = make_tree()
        ready = tree.new_node(NN.GKNodeOnReady, "准备")
        quality = tree.new_node(NN.GKNodeQualitySet, "画质=低",
                                gk_level="LOW")
        gk_stub.connect(tree, ready, "执行", quality, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("_gk_quality_apply(0.6, 0, 0, 0)", code)
        self.assertIn("_gk_win.content_scale_factor = scale", code)
        self.assertIn("_gk_vp.msaa_2d = msaa", code)
        # 2D 不许去碰 3D 的那条属性(注释里提到它不算)
        self.assertNotIn("_gk_vp.scaling_3d_scale", code)

    def test_helper_generated_once(self):
        """两个画质节点共用一个 helper(生成物只多一行调用,不重抄)。"""
        tree = make_tree()
        ready = tree.new_node(NN.GKNodeOnReady, "准备")
        low = tree.new_node(NN.GKNodeQualitySet, "画质=低", gk_level="LOW")
        high = tree.new_node(NN.GKNodeQualitySet, "画质=高", gk_level="HIGH")
        gk_stub.connect(tree, ready, "执行", low, "执行")
        gk_stub.connect(tree, low, "完成", high, "执行")
        code, _w = gen(tree, V3)
        self.assertEqual(code.count("func _gk_quality_apply("), 1)
        self.assertIn("_gk_quality_apply(0.6, 0, 0, 0)", code)
        self.assertIn("_gk_quality_apply(1.0, 2, 4096, 4)", code)


# ==========================================================================
# 第八十九轮(I 组 27+28):变量 / 函数两块细看 + 资产改名 + 报告资产清单
# ==========================================================================
class TestRound89BlueprintAudit(Base):
    """侧栏『变量』『函数』两块的判据 + 导出报告的资产清单(全是纯函数)。

    第八十九轮把这两块"细看"了一遍,补的是三个**真缺口**:
      * 『函数』框的调用次数原来是 `1 if 有 else 0` —— 调用几次都显示 1;
      * 改名(函数 / 变量)只改声明,图里的引用会悄悄断成"未声明";
      * 导出报告只报"文件数",资产那份清单要用户自己去翻 art/ 目录。
    """

    class _Scene:
        """假场景:只要 `scene[key] = ...` 和 `scene.get(key)` 两个口。"""

        def __init__(self):
            self.data = {}

        def __setitem__(self, key, value):
            self.data[key] = value

        def get(self, key, default=None):
            return self.data.get(key, default)

    def _declare_func(self, tree, name, return_type="void", params=""):
        tree.gk_funcs.add()
        func = tree.gk_funcs[len(tree.gk_funcs) - 1]
        func.func_name = name
        func.return_type = return_type
        func.params = params
        return func

    def _declare_var(self, tree, name, var_type="float"):
        tree.gk_vars.add()
        var = tree.gk_vars[len(tree.gk_vars) - 1]
        var.var_name = name
        var.var_type = var_type
        return var

    # ---- 『函数』框:真数节点 ----

    def test_function_usages_counts_defines_and_calls(self):
        """调用次数必须**真数节点**(以前恒为 0/1,调用两次也显示 1 次)。"""
        from BlenderGameKit import properties as props
        tree = make_tree()
        tree.new_node(NN.GKNodeFunctionDefine, "定义攻击", gk_func_name="攻击")
        tree.new_node(NN.GKNodeFunctionCall, "调用1", gk_func_name="攻击")
        tree.new_node(NN.GKNodeFunctionCall, "调用2", gk_func_name="攻击")
        tree.new_node(NN.GKNodeFunctionDefine, "定义走路", gk_func_name="走路")
        tree.new_node(NN.GKNodeFunctionCall, "没填", gk_func_name="")
        usages = props.function_usages(tree)
        self.assertEqual(usages["攻击"], {"defines": 1, "calls": 2}, usages)
        self.assertEqual(usages["走路"], {"defines": 1, "calls": 0}, usages)
        self.assertNotIn("", usages)

    def test_function_usages_keeps_undeclared_names(self):
        """改名 / 拼错留下的孤儿引用也要在表里(侧栏靠它列"未声明")。"""
        from BlenderGameKit import properties as props
        tree = make_tree()
        tree.new_node(NN.GKNodeFunctionCall, "孤儿", gk_func_name="攻击2")
        usages = props.function_usages(tree)
        self.assertIn("攻击2", usages)
        self.assertEqual(usages["攻击2"], {"defines": 0, "calls": 1})
        # 声明表里没有它 —— 两部分一减就是"未声明清单"
        self.assertEqual(props.tree_functions(tree), {})

    def test_function_usages_double_define(self):
        """同名定义两个也要数出来(导出只保留第一个,是静默丢逻辑)。"""
        from BlenderGameKit import properties as props
        tree = make_tree()
        tree.new_node(NN.GKNodeFunctionDefine, "定义1", gk_func_name="攻击")
        tree.new_node(NN.GKNodeFunctionDefine, "定义2", gk_func_name="攻击")
        self.assertEqual(props.function_usages(tree)["攻击"],
                         {"defines": 2, "calls": 0})

    # ---- 改名连带引用 ----

    def test_rename_function_refs_carries_nodes_and_bindings(self):
        from BlenderGameKit import properties as props
        tree = make_tree()
        define = tree.new_node(NN.GKNodeFunctionDefine, "定义",
                              gk_func_name="攻击")
        call = tree.new_node(NN.GKNodeFunctionCall, "调用",
                             gk_func_name="攻击")
        other = tree.new_node(NN.GKNodeFunctionCall, "别的",
                              gk_func_name="走路")
        var = self._declare_var(tree, "血量")
        var.getter = "攻击"
        var.setter = "走路"
        carried = props.rename_function_refs(tree, "攻击", "攻击_重击")
        self.assertEqual(carried, 3, carried)      # 定义 + 调用 + getter
        self.assertEqual(str(define.gk_func_name), "攻击_重击")
        self.assertEqual(str(call.gk_func_name), "攻击_重击")
        self.assertEqual(str(other.gk_func_name), "走路")     # 别人的不动
        self.assertEqual(var.getter, "攻击_重击")
        self.assertEqual(var.setter, "走路")

    def test_rename_function_refs_ignores_noop(self):
        from BlenderGameKit import properties as props
        tree = make_tree()
        node = tree.new_node(NN.GKNodeFunctionCall, "调用",
                             gk_func_name="攻击")
        self.assertEqual(props.rename_function_refs(tree, "攻击", "攻击"), 0)
        self.assertEqual(props.rename_function_refs(tree, "", "新"), 0)
        self.assertEqual(props.rename_function_refs(tree, "攻击", ""), 0)
        self.assertEqual(str(node.gk_func_name), "攻击")

    def test_rename_variable_refs_covers_all_slots(self):
        """『变量_设置』的第 2~6 个槽位也要跟着改(共享同一张属性表)。"""
        from BlenderGameKit import properties as props
        tree = make_tree()
        getter = tree.new_node(NN.GKNodeGetVar, "读", gk_var_name="血量")
        setter = tree.new_node(NN.GKNodeSetVar, "写", gk_var_name="血量",
                               gk_var_count=3, gk_var_name_2="血量")
        other = tree.new_node(NN.GKNodeGetVar, "别的", gk_var_name="分数")
        carried = props.rename_variable_refs(tree, "血量", "生命值")
        self.assertEqual(carried, 3, carried)      # 读 1 + 写主槽 + 第 2 槽
        self.assertEqual(str(getter.gk_var_name), "生命值")
        self.assertEqual(str(setter.gk_var_name), "生命值")
        self.assertEqual(str(setter.gk_var_name_2), "生命值")
        self.assertEqual(str(other.gk_var_name), "分数")

    # ---- 导出报告:资产清单 ----

    def test_classify_assets_groups_by_folder(self):
        from BlenderGameKit import export_report as er
        written = [
            "D:/proj/art/World.glb",
            "D:/proj/art/asset_树_7b216a3b.glb",
            "D:/proj/art/tex_c4e80742.png",
            "D:/proj/audio/sound_c4e80742.wav",
            "D:/proj/curves/curve_demo.tres",
            "D:/proj/materials/m_tree.tres",     # 材质不算资产
            "D:/proj/scripts/game_logic.gd",
            "D:/proj/scenes/World.tscn",
            None,                                # written 里混 None 不能崩
            "art/ui_image_861a9d02.png",         # 相对路径也认
        ]
        groups = er.classify_assets("D:/proj", written)
        self.assertEqual(groups["model"],
                         ["art/World.glb", "art/asset_树_7b216a3b.glb"])
        self.assertEqual(groups["image"],
                         ["art/tex_c4e80742.png", "art/ui_image_861a9d02.png"])
        self.assertEqual(groups["sound"], ["audio/sound_c4e80742.wav"])
        self.assertEqual(groups["curve"], ["curves/curve_demo.tres"])

    def test_classify_assets_empty_files_is_zero(self):
        from BlenderGameKit import export_report as er
        self.assertEqual(er.asset_total({}), 0)
        self.assertEqual(er.note_assets(None, "D:/proj", []), 0)
        groups = er.classify_assets("D:/proj", ["D:/proj/scripts/x.gd"])
        self.assertEqual(sum(len(entries) for entries in groups.values()), 0)

    def test_note_assets_into_report_and_text(self):
        """资产清单要能写进报告(.blend)、清掉内存读回来、正文列出来。"""
        from BlenderGameKit import export_report as er
        scene = self._Scene()
        er.clear()
        er.record(scene, {"ok": True, "script_name": "game_logic.gd",
                          "directory": "D:/proj", "lines": 10,
                          "written": ["a"], "warnings": [], "notes": []})
        total = er.note_assets(scene, "D:/proj", ["D:/proj/art/World.glb",
                                                  "D:/proj/audio/a.wav"])
        self.assertEqual(total, 2)
        er.clear()                                     # 只留 .blend 那份
        loaded = er.load(scene)
        self.assertEqual(er.asset_total(loaded), 2)
        text = er.report_text(loaded)
        self.assertIn("资产清单(2 份)", text)
        self.assertIn("模型: art/World.glb", text)
        self.assertIn("音频: audio/a.wav", text)
        er.clear()

    def test_note_assets_makes_minimal_report_without_one(self):
        """用户只导了资产、还没导过脚本 —— 也要有一份能看的清单。"""
        from BlenderGameKit import export_report as er
        er.clear()
        total = er.note_assets(None, "D:/proj", ["D:/proj/art/World.glb"])
        self.assertEqual(total, 1)
        self.assertIn("资产清单(1 份)", er.report_text(er.load(None)))
        er.clear()

    def test_record_carries_assets_forward(self):
        """只导脚本时,上次记的资产清单要跟着留(那些文件还在磁盘上)。"""
        from BlenderGameKit import export_report as er
        scene = self._Scene()
        er.clear()
        er.note_assets(scene, "D:/proj", ["D:/proj/art/World.glb"])
        er.record(scene, {"ok": True, "script_name": "game_logic.gd",
                          "directory": "D:/proj", "lines": 3,
                          "written": ["a"], "warnings": [], "notes": []})
        self.assertEqual(er.asset_total(er.load(scene)), 1)
        er.clear()

    def test_report_text_caps_long_asset_lists(self):
        from BlenderGameKit import export_report as er
        names = [f"D:/proj/art/m{index:02d}.glb" for index in range(14)]
        report = er.build({"ok": True, "script_name": "g.gd",
                           "directory": "D:/proj", "lines": 1,
                           "written": ["a"], "warnings": [], "notes": []})
        report["assets"] = er.classify_assets("D:/proj", names)
        text = er.report_text(report)
        self.assertIn("资产清单(14 份)", text)
        self.assertIn("…(还有 4 个)", text)


# ==========================================================================
# 第九十轮(待办 31):AI 批量提议 + 『全部执行』
# ==========================================================================
class TestRound90AdviceBatch(Base):
    """AI 一次提好几条(待办 31)。

    * `propose_actions` 批量工具:逐条校验,合格的进清单、不合格的点名;
    * 循环里"一张卡片 / 一批卡片"拍平成同一个处理(ai 那层不关心是哪种);
    * 步数标签报出张数(侧栏"它已经:提议了 3 个动作")。
    """

    def _actions(self):
        from BlenderGameKit import advisor_tools
        return advisor_tools

    def test_batch_tool_schema_present(self):
        from BlenderGameKit import advisor_tools
        names = [entry["function"]["name"]
                 for entry in advisor_tools.tools_schema()]
        self.assertIn("propose_action", names)
        self.assertIn("propose_actions", names)
        # 批量那条的动作名枚举要和登记表一致(AI 才编不出不存在的动作)
        spec = next(entry for entry in advisor_tools.tools_schema()
                    if entry["function"]["name"] == "propose_actions")
        enum = (spec["function"]["parameters"]["properties"]["actions"]
                ["items"]["properties"]["action"]["enum"])
        self.assertEqual(enum, advisor_tools.action_names())

    def test_batch_builds_all_valid_cards(self):
        at = self._actions()
        note, cards = at.build_proposals({"actions": [
            {"action": "enable_god_camera", "why": "看场景"},
            {"action": "set_toon_materials", "value": "false", "why": "核对细节"},
        ]})
        self.assertEqual([card["action"] for card in cards],
                         ["enable_god_camera", "set_toon_materials"])
        self.assertIn("2 张卡片", note)
        self.assertIn("全部执行", note)
        # 说明里必须提醒模型**别说"已经改好了"**(那是撒谎)
        self.assertIn("不要", note)
        self.assertIn("改好", note)
        # 卡片形状和单条那条路一致(执行侧不用区分来源)
        self.assertEqual(cards[1]["value"], "false")
        self.assertTrue(cards[0]["label"])

    def test_batch_reports_bad_items_but_keeps_good(self):
        at = self._actions()
        note, cards = at.build_proposals({"actions": [
            {"action": "enable_god_camera", "why": "看场景"},
            {"action": "没这个动作", "why": "编的"},
            {"action": "enable_script_target", "why": "忘了给对象名"},
            "不是对象",
        ]})
        self.assertEqual(len(cards), 1)
        self.assertIn("第 2 条", note)      # 动作名不存在
        self.assertIn("第 3 条", note)      # 缺 target
        self.assertIn("第 4 条", note)      # 不是对象
        self.assertIn("补对", note)

    def test_batch_all_bad_gives_no_cards(self):
        at = self._actions()
        note, cards = at.build_proposals({"actions": [
            {"action": "没这个动作", "why": "编的"}]})
        self.assertEqual(cards, [])
        self.assertIn("一条都不合格", note)

    def test_batch_needs_non_empty_array(self):
        at = self._actions()
        for params in ({}, {"actions": []}, {"actions": "x"},
                       None):
            note, cards = at.build_proposals(params or {})
            self.assertEqual(cards, [], params)
            self.assertIn("数组", note)

    def test_execute_returns_cards_for_batch_tool(self):
        at = self._actions()
        text, cards = at.execute("propose_actions", {"actions": [
            {"action": "enable_god_camera", "why": "看场景"},
            {"action": "set_toon_materials", "value": "false", "why": "核对"}]},
            None)
        self.assertIsInstance(cards, list)
        self.assertEqual(len(cards), 2)
        self.assertIn("2 张卡片", text)
        # 单条那条路还是老样子:一张 dict(不是列表)
        text1, card = at.execute("propose_action",
                                 {"action": "enable_god_camera",
                                  "why": "看场景"}, None)
        self.assertIsInstance(card, dict)
        self.assertEqual(card["action"], "enable_god_camera")
        # 名字拼错时的报错要把两个都列出来
        text2, nothing = at.execute("propose_actions_x", {}, None)
        self.assertIsNone(nothing)
        self.assertIn("propose_actions", text2)

    def test_step_label_reports_count(self):
        from BlenderGameKit import advisor
        # `arguments` 有值 = 正常一次调用(缺了才算"参数有问题")
        call = {"name": "propose_actions", "arguments": {}}
        self.assertEqual(advisor._step_label(call, []),
                         "提议了几个动作(等你确认)")
        self.assertEqual(advisor._step_label(call, [{"action": "x"}]),
                         "提议了一个动作(等你确认)")
        self.assertEqual(
            advisor._step_label(call, [{"action": "x"}, {"action": "y"},
                                       {"action": "z"}]),
            "提议了 3 个动作(等你确认)")

    def test_proposals_keep_batch_appended(self):
        """一批卡片要**追加**进待确认清单(不能被上一批覆盖掉)。"""
        from BlenderGameKit import advisor
        advisor.clear_proposals()
        try:
            advisor.add_proposal({"action": "a"})
            for card in ({"action": "b"}, {"action": "c"}):
                advisor.add_proposal(card)
            self.assertEqual([card["action"]
                              for card in advisor.agent_proposals()],
                             ["a", "b", "c"])
        finally:
            advisor.clear_proposals()


class TestRound94BackfaceCulling(Base):
    """第九十四轮:背面剔除 —— Blender 材质勾了『背面剔除』导出要跟着剔。

    以前导出链路**根本没读那个勾**(所有卡通材质都写死双面的
    `gk_toon.gdshader`):Blender 预览剔了、Godot 里照旧双面 —— 预览在骗人,
    背面那些面片也白画。修法是"按需生成剔面变体"(不是仓库里第二份源码)。
    """

    def test_flag_defaults_to_no_culling(self):
        """缺属性(老文件 / 假对象)按**不剔除** —— 面板片消失最难受。"""
        from BlenderGameKit import asset_export

        class Fake:
            pass

        self.assertFalse(asset_export.material_culls_backfaces(Fake()))
        self.assertFalse(asset_export.material_culls_backfaces(None))
        fake = Fake()
        fake.use_backface_culling = True
        self.assertTrue(asset_export.material_culls_backfaces(fake))

    def test_variant_differs_by_one_line_only(self):
        """变体 = 原文件 + 一行 render_mode 换词(逐行比对,别的都不许动)。"""
        from BlenderGameKit import asset_export
        shader = os.path.join(ROOT, "BlenderGameKit", "shaders",
                             "gk_toon.gdshader")
        with open(shader, "r", encoding="utf-8") as handle:
            base = handle.read()
        variant = asset_export.cull_shader_source(base)
        self.assertIn("cull_back;", variant)
        self.assertNotIn("cull_disabled;", variant)
        base_lines = base.splitlines()
        variant_lines = variant.splitlines()
        self.assertEqual(len(base_lines), len(variant_lines))
        diff = [pair for pair in zip(base_lines, variant_lines)
                if pair[0] != pair[1]]
        self.assertEqual(len(diff), 1, diff)
        self.assertIn("render_mode", diff[0][0])
        self.assertIn("render_mode", diff[0][1])

    def test_variant_generation_fails_loudly(self):
        """源文件那行被改坏时必须**报错** —— 不能静默生成"没剔面"的变体。"""
        from BlenderGameKit import asset_export
        with self.assertRaises(ValueError):
            asset_export.cull_shader_source("shader_type spatial;\n")

    def test_variant_name_is_claimed_by_cleanup(self):
        """派生文件名要在"插件认识的着色器"名单里(否则存量文件永远删不掉)。"""
        from BlenderGameKit import asset_export, generated_files
        self.assertIn(asset_export.CULL_SHADER_NAME,
                      generated_files._plugin_shader_names())
        with tempfile.TemporaryDirectory() as folder:
            shaders = os.path.join(folder, "shaders")
            os.makedirs(shaders)
            mine = os.path.join(shaders, asset_export.CULL_SHADER_NAME)
            with open(mine, "w", encoding="utf-8") as handle:
                handle.write("shader_type spatial;\n")
            self.assertTrue(generated_files.is_generated_file(mine),
                            "派生着色器要被认领,不然后面永远清不掉")
            theirs = os.path.join(shaders, "my_own.gdshader")
            with open(theirs, "w", encoding="utf-8") as handle:
                handle.write("shader_type spatial;\n")
            self.assertFalse(generated_files.is_generated_file(theirs),
                             "用户自己放的着色器不该被认领")


class TestRound95PreviewAlign(Base):
    """第九十五轮:预览跟着『环境』设置卡对齐(不再写死 Godot 出厂值)。

    —— 以前『Godot 观感』把预览钉死在"出厂画面"(Standard / 0.30 平灰),
    用户在设置卡上换成 AgX / 换个背景色之后,预览和运行时又对不上了。
    现在对齐表读的是**导出用的同一份**场景设置。
    """

    def _settings(self, **values):
        class Fake:
            pass

        fake = Fake()
        fake.env_enabled = True
        fake.dimension = "3"
        for name, value in values.items():
            setattr(fake, name, value)
        return fake

    def _scene(self, settings):
        class FakeScene:
            pass

        scene = FakeScene()
        scene.gk_settings = settings
        return scene

    def test_disabled_falls_back_to_factory(self):
        """没开设置卡 -> Godot 出厂画面(老行为,不是空值)。

        ⚠ 出厂灰是 Godot 给的**显示值**,预览世界要的是线性值 ——
        所以要换算(第九十六轮把这条口径写死在这里)。
        """
        from BlenderGameKit import godot_look
        for settings in (None, self._settings(env_enabled=False)):
            values = godot_look.env_display(self._scene(settings))
            self.assertFalse(values["enabled"])
            self.assertEqual(values["view_transform"], "Standard")
            self.assertEqual(values["tonemap"], "LINEAR")
            self.assertAlmostEqual(
                values["backdrop"][0],
                godot_look.srgb_to_linear(godot_look.GODOT_CLEAR_COLOR_SRGB[0]),
                places=5)

    def test_tonemap_picks_view_transform(self):
        """五种色调映射都要有映射,且和 properties 的选项一一对上。"""
        from BlenderGameKit import godot_look
        from BlenderGameKit import properties as props
        cases = {"LINEAR": "Standard", "AGX": "AgX", "ACES": "AgX",
                 "FILMIC": "Filmic", "REINHARD": "Filmic"}
        for tonemap, expected in cases.items():
            values = godot_look.env_display(
                self._scene(self._settings(env_tonemap=tonemap)))
            self.assertEqual(values["view_transform"], expected, tonemap)
        self.assertEqual({item[0] for item in props.ENV_TONEMAPS},
                         set(godot_look.TONEMAP_VIEW_TRANSFORMS),
                         "设置卡上能选的,映射表里都得有")

    def test_background_color_follows_settings(self):
        from BlenderGameKit import godot_look
        values = godot_look.env_display(self._scene(self._settings(
            env_background="COLOR", env_bg_color=(0.1, 0.2, 0.3, 1.0))))
        self.assertEqual(values["background"], "COLOR")
        self.assertAlmostEqual(values["backdrop"][0], 0.1, places=5)
        self.assertAlmostEqual(values["backdrop"][2], 0.3, places=5)

    def test_sky_backdrop_uses_horizon(self):
        """天空:预览用导出器算的那个**地平线色**(顶色往白里提 0.35)。"""
        from BlenderGameKit import godot_look
        values = godot_look.env_display(self._scene(self._settings(
            env_background="SKY", env_bg_color=(0.0, 0.0, 0.0, 1.0))))
        self.assertEqual(values["background"], "SKY")
        self.assertAlmostEqual(values["backdrop"][0], 0.35, places=5)

    def test_2d_scene_keeps_factory(self):
        """2D 关卡不导环境(导出器同样跳过)-> 预览也别对齐它。"""
        from BlenderGameKit import godot_look
        values = godot_look.env_display(self._scene(
            self._settings(dimension="2", env_tonemap="AGX")))
        self.assertFalse(values["enabled"])
        self.assertEqual(values["view_transform"], "Standard")


class TestRound96ColorSpace(Base):
    """第九十六轮:写进 .tscn 的颜色按"显示值"语义换算(线性 -> sRGB)。

    实测标定(第九十五轮):`Environment.background_color = Color(0.3,)`
    渲出来就是 0.298 的像素 —— 那些 Color 是**显示值**;而 Blender 的颜色
    属性存的是**线性**值。以前 1:1 直写 -> 取色器上的色块 ≠ 游戏里的颜色
    (游戏偏暗)。换算之后:取色器看到什么,游戏里就是什么。
    """

    def test_env_colors_are_converted(self):
        from BlenderGameKit import asset_export as ae
        from BlenderGameKit import godot_look
        want = godot_look.linear_to_srgb(0.3)
        text = ae._env_color_text((0.3, 0.3, 0.3, 1.0))
        self.assertIn("Color(%.6f" % want, text)
        self.assertNotIn("0.300000", text, "直写那套已经不对了")

    def test_conversion_endpoints_are_fixed(self):
        """0 和 1 是换算的不动点(黑还是黑、白还是白)。"""
        from BlenderGameKit import asset_export as ae
        self.assertIn("Color(0.000000, 0.000000, 0.000000, 1.000000)",
                      ae._env_color_text((0.0, 0.0, 0.0, 1.0)))
        self.assertIn("Color(1.000000, 1.000000, 1.000000, 1.000000)",
                      ae._env_color_text((1.0, 1.0, 1.0, 1.0)))

    def test_preview_world_color_is_the_setting(self):
        """导出换算之后,预览**直接**用设置值当线性世界色(不再自己换算)。"""
        from BlenderGameKit import godot_look

        class Fake:
            pass

        settings = Fake()
        settings.env_enabled = True
        settings.dimension = "3"
        settings.env_background = "COLOR"
        settings.env_bg_color = (0.2, 0.2, 0.2, 1.0)
        scene = Fake()
        scene.gk_settings = settings
        values = godot_look.env_display(scene)
        self.assertAlmostEqual(values["backdrop"][0], 0.2, places=5)


class TestRound97AmbientSource(Base):
    """第九十七轮:环境光来源跟着背景模式选(纯色 -> Color,天空 -> Sky)。

    —— 原来写死 3(**Sky**),而注释还写着"用颜色当环境光":枚举搞错了。
    纯色背景下没有天空,`ambient_light_source = 3` 等于**把环境光静默关掉**
    (真机标定:和 Disabled 渲出来一模一样),用户在设置卡上填的
    『环境光颜色 / 强度』一直被忽略。
    """

    def _scene(self, **values):
        class Fake:
            pass

        settings = Fake()
        settings.env_enabled = True
        settings.dimension = "3"
        for name, value in values.items():
            setattr(settings, name, value)
        scene = Fake()
        scene.gk_settings = settings
        return scene

    def test_solid_background_uses_color_source(self):
        from BlenderGameKit import asset_export as ae
        blocks, _node = ae.environment_scene_blocks(
            self._scene(env_background="COLOR"))
        text = blocks[-1]
        self.assertIn("background_mode = 1", text)
        self.assertIn("ambient_light_source = 2", text,
                      "纯色背景必须用 Color(2),不然用户填的环境光不生效")
        self.assertNotIn("ambient_light_source = 3", text)
        self.assertNotIn("sky =", text)

    def test_sky_background_uses_sky_source(self):
        from BlenderGameKit import asset_export as ae
        blocks, _node = ae.environment_scene_blocks(
            self._scene(env_background="SKY"))
        text = "".join(blocks)
        self.assertIn("background_mode = 2", text)
        self.assertIn("ambient_light_source = 3", text,
                      "有天空时才该拿天空当环境光")
        self.assertIn("sky = SubResource(", text)

    def test_ambient_source_covers_every_background(self):
        """背景模式每多一种,环境光来源都必须有对应项(不然会 KeyError)。"""
        from BlenderGameKit import asset_export as ae
        self.assertEqual(set(ae.ENV_AMBIENT_SOURCES),
                         set(ae.ENV_BACKGROUND_MODES))
        self.assertEqual(set(ae.ENV_AMBIENT_SOURCES.values()), {2, 3})


class TestRound98RenderTuning(Base):
    """第九十八轮:可见距离 / 阴影开销(三张"少画"表)。

    三项都走**环境脚本**(不是 .tscn):物体的网格在 glb 实例内部,而实例
    的内部子节点不能被外部覆盖 —— 写进 .tscn 会被 Godot 丢掉(材质那条
    踩过的坑,注释写在 asset_export 里)。全空 = 老场景一字不变。
    """

    class _Obj:
        def __init__(self, name, kind="MESH", data_type="", **props):
            self.name = name
            self.type = kind
            self.data = types.SimpleNamespace(type=data_type)
            self.gk_physics = (types.SimpleNamespace(**props) if props else None)

    def _scene(self, *objs):
        return types.SimpleNamespace(objects=list(objs))

    def test_empty_scene_writes_empty_tables(self):
        """什么都没填 -> 三张表都是空串(老场景行为不变)。"""
        from BlenderGameKit import asset_export as ae
        self.assertEqual(ae.render_tuning_tables(self._scene()),
                         ('""', '""', '""'))

    def test_visibility_distance_only_when_positive(self):
        from BlenderGameKit import asset_export as ae
        scene = self._scene(
            self._Obj("树", render_visibility_distance=60.0),
            self._Obj("石头", render_visibility_distance=0.0),
            self._Obj("草", render_visibility_distance=-5.0),
        )
        vis, _shadow, _light = ae.render_tuning_tables(scene)
        self.assertIn("树=60.0", vis)
        self.assertNotIn("石头", vis)
        self.assertNotIn("草", vis)

    def test_no_shadow_lists_mesh_names(self):
        from BlenderGameKit import asset_export as ae
        scene = self._scene(
            self._Obj("碎石", render_no_shadow=True),
            self._Obj("墙", render_no_shadow=False),
        )
        _vis, shadow, _light = ae.render_tuning_tables(scene)
        self.assertIn("碎石", shadow)
        self.assertNotIn("墙", shadow)

    def test_light_shadow_distance_only_for_sun(self):
        """只认平行光(点光 / 聚光在 Godot 里没有这个属性,填了也不生效)。"""
        from BlenderGameKit import asset_export as ae
        scene = self._scene(
            self._Obj("月光", kind="LIGHT", data_type="SUN",
                      light_shadow_distance=50.0),
            self._Obj("台灯", kind="LIGHT", data_type="POINT",
                      light_shadow_distance=50.0),
            self._Obj("手电筒", kind="LIGHT", data_type="SPOT"),
        )
        _vis, _shadow, light = ae.render_tuning_tables(scene)
        self.assertIn("月光=50.0", light)
        self.assertNotIn("台灯", light)
        self.assertNotIn("手电筒", light)

    def test_tables_use_pipe_separator(self):
        """分隔符必须是竖线 —— 默认值里放真换行会让 GDScript 解析失败。"""
        from BlenderGameKit import asset_export as ae
        scene = self._scene(
            self._Obj("树", render_visibility_distance=60.0),
            self._Obj("石头", render_visibility_distance=30.0),
        )
        vis, _shadow, _light = ae.render_tuning_tables(scene)
        self.assertIn("|", vis)
        self.assertNotIn("\\n", vis)

    def test_env_script_gets_the_tables(self):
        """三张表要真的写进环境脚本文本(并且运行时函数在里面)。"""
        from BlenderGameKit import asset_export as ae
        text = ae.render_env_script(
            visibility_ranges='"树=60.0"', no_shadow_nodes='"碎石"',
            light_shadows='"月光=50.0"')
        self.assertIn('var visibility_ranges: String = "树=60.0"', text)
        self.assertIn('var no_shadow_nodes: String = "碎石"', text)
        self.assertIn('var light_shadows: String = "月光=50.0"', text)
        self.assertIn("func _apply_render_tuning() -> void:", text)
        self.assertIn("visibility_range_end", text)
        self.assertIn("cast_shadow = _GK_SHADOW_OFF", text)
        self.assertIn("directional_shadow_max_distance", text)


class TestRound99Occluder(Base):
    """第九十九轮:遮挡体(导出 OccluderInstance3D + 打开项目总开关)。

    ── 两个真机标定出来的硬事实(都在这一层锁住)──────────────────
    1. Godot 的 `rendering/occlusion_culling/use_occlusion_culling`
       **默认 false** —— 光有节点一点都不会省(实测挂着 21 个渲染对象、
       拿掉也是 21 个;打开后 1 个)。所以勾了就必须顺手打开;
    2. 这次**可以**写进 .tscn(不像上一轮的 visibility_range):
       OccluderInstance3D 是场景里的独立节点,不是 glb 实例内部的网格。
    """

    class _Obj:
        def __init__(self, name, kind="MESH", occluder=False, bounds=None):
            self.name = name
            self.type = kind
            self.gk_physics = types.SimpleNamespace(render_occluder=occluder)
            self._bounds = bounds

    def _scene(self, *objs, dimension="3"):
        return types.SimpleNamespace(
            objects=list(objs),
            gk_settings=types.SimpleNamespace(dimension=dimension))

    def test_no_occluder_writes_nothing(self):
        from BlenderGameKit import asset_export as ae
        entries = ae.occluder_entries(self._scene(self._Obj("墙")))
        self.assertEqual(entries, [])
        self.assertEqual(ae.occluder_texts(entries), ([], []))

    def test_entries_use_bounds_and_godot_axis(self):
        """尺寸取包围盒、位置取包围盒中心,并按 Blender -> Godot 换轴序。"""
        from BlenderGameKit import asset_export as ae
        with mock.patch.object(ae, "object_bounds", return_value={
                "min": (0.0, 0.0, 0.0), "size": (4.0, 2.0, 6.0)}):
            entries = ae.occluder_entries(
                self._scene(self._Obj("墙", occluder=True)))
        self.assertEqual(len(entries), 1)
        # Blender 中心 (2, 1, 3) -> Godot (2, 3, -1)
        self.assertEqual(entries[0]["position"], (2.0, 3.0, -1.0))
        # Blender 尺寸 (4, 2, 6) -> Godot (4, 6, 2)
        self.assertEqual(entries[0]["size"], (4.0, 6.0, 2.0))

    def test_texts_are_occluder_node_and_box(self):
        from BlenderGameKit import asset_export as ae
        blocks, nodes = ae.occluder_texts([
            {"name": "墙", "position": (1.0, 2.0, 3.0), "size": (4.0, 5.0, 6.0)},
        ])
        self.assertEqual(len(blocks), 1)
        self.assertIn('[sub_resource type="BoxOccluder3D" id="gk_occluder_0"]',
                      blocks[0])
        self.assertIn("size = Vector3(4.0000, 5.0000, 6.0000)", blocks[0])
        self.assertEqual(len(nodes), 1)
        self.assertIn('type="OccluderInstance3D" parent="."', nodes[0])
        self.assertIn("1.0000, 2.0000, 3.0000", nodes[0])
        self.assertIn('occluder = SubResource("gk_occluder_0")', nodes[0])

    def test_two_occluders_get_distinct_ids(self):
        from BlenderGameKit import asset_export as ae
        blocks, nodes = ae.occluder_texts([
            {"name": "A", "position": (0.0, 0.0, 0.0), "size": (1.0, 1.0, 1.0)},
            {"name": "B", "position": (0.0, 0.0, 0.0), "size": (1.0, 1.0, 1.0)},
        ])
        self.assertIn("gk_occluder_0", blocks[0])
        self.assertIn("gk_occluder_1", blocks[1])
        self.assertIn('type="OccluderInstance3D"', nodes[1])
        # ⚠ 节点名不能重复(Godot 会改名,用户按名字找就找不着了)
        self.assertNotEqual(nodes[0].split('"')[1], nodes[1].split('"')[1])

    def test_2d_scene_skips_occluders(self):
        """遮挡剔除是 3D 的(LightOccluder2D 是给光影的,不是它)。"""
        from BlenderGameKit import asset_export as ae
        entries = ae.occluder_entries(
            self._scene(self._Obj("墙", occluder=True), dimension="2"))
        self.assertEqual(entries, [])

    def test_project_setting_is_enabled(self):
        """勾了遮挡体 -> project.godot 里的总开关被打开(默认是 false)。"""
        from BlenderGameKit import asset_export as ae
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "project.godot")
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write("config_version=5\n\n[application]\n\n"
                             'config/name="x"\n\n[rendering]\n\n'
                             'renderer/rendering_method="forward_plus"\n')
            self.assertTrue(ae.ensure_occlusion_culling(folder))
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("occlusion_culling/use_occlusion_culling = true", text)
            self.assertIn('renderer/rendering_method="forward_plus"', text)
            # 已经开着了 -> 不再改(幂等,别每次导出都重写文件)
            self.assertFalse(ae.ensure_occlusion_culling(folder))

    def test_project_setting_without_rendering_section(self):
        """project.godot 里没有 [rendering] 段时也要能写进去。"""
        from BlenderGameKit import asset_export as ae
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "project.godot")
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write("config_version=5\n\n[application]\n\n"
                             'config/name="x"\n')
            self.assertTrue(ae.ensure_occlusion_culling(folder))
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertIn("[rendering]", text)
            self.assertIn("occlusion_culling/use_occlusion_culling = true", text)


class TestRound100Net(Base):
    """第一百轮:网络联机(ENet 房间 + 自定义消息)。

    ── 这些断言锁的是"真机标定过的形状"(改代码前先看注释)────────
    * 开房 / 加入都走同一个 `_gk_net_start(server, ip, port, max)`;
    * 信号(peer_connected / peer_disconnected)接在 `_ready` **最前面**,
      而且**先连接后开房**(实测:先 connect 再赋 multiplayer_peer 一样收得到);
    * 收到的消息走一个固定的 `@rpc` 通道 `_gk_net_msg(频道, 发送者, 文本)`,
      发送者**当参数传**(不靠 get_remote_sender_id),这样"谁发的"可控;
    * `rpc(...)` = 发给除自己以外的所有人(实测),发的人自己收不到。
    """

    def _host_tree(self):
        tree = make_tree()
        ready = tree.new_node(NN.GKNodeOnReady, "准备")
        host = tree.new_node(NN.GKNodeNetHost, "开房",
                             gk_port=9001, gk_max=4)
        printer = tree.new_node(NN.GKNodePrint, "日志", gk_text="NET_HOST_READY")
        gk_stub.connect(tree, ready, "执行", host, "执行")
        gk_stub.connect(tree, host, "完成", printer, "执行")
        return tree

    # ---- 四个动作节点 ----
    def test_host_opens_room(self):
        code, warnings = gen(self._host_tree(), V3)
        self.assertIn('_gk_net_start(true, "127.0.0.1", 9001, 4)', code)
        self.assertIn("ENetMultiplayerPeer.new()", code)
        self.assertIn("peer.create_server(port, max_clients)", code)
        self.assertIn('print("NET_HOST_READY")', code)
        self.assert_no_chinese_identifiers(code)
        self.assert_indent_sane(code)
        self.assertFalse([w for w in warnings if "端口" in w], warnings)

    def test_join_connects_to_address(self):
        tree = make_tree()
        ready = tree.new_node(NN.GKNodeOnReady, "准备")
        join = tree.new_node(NN.GKNodeNetJoin, "加入",
                             gk_ip="192.168.1.7", gk_port=9001)
        gk_stub.connect(tree, ready, "执行", join, "执行")
        code, _ = gen(tree, V3)
        self.assertIn('_gk_net_start(false, "192.168.1.7", 9001, 1)', code)
        self.assertIn("peer.create_client(ip, port)", code)

    def test_bad_port_falls_back_with_warning(self):
        tree = make_tree()
        ready = tree.new_node(NN.GKNodeOnReady, "准备")
        host = tree.new_node(NN.GKNodeNetHost, "开房", gk_port=70000)
        gk_stub.connect(tree, ready, "执行", host, "执行")
        code, warnings = gen(tree, V3)
        self.assertIn('_gk_net_start(true, "127.0.0.1", 8910, 8)', code)
        self.assertTrue(any("端口" in w and "70000" in w for w in warnings), warnings)

    def test_disconnect(self):
        tree = make_tree()
        ready = tree.new_node(NN.GKNodeOnReady, "准备")
        off = tree.new_node(NN.GKNodeNetDisconnect, "断开")
        gk_stub.connect(tree, ready, "执行", off, "执行")
        code, _ = gen(tree, V3)
        self.assertIn("_gk_net_stop()", code)
        self.assertIn("multiplayer.multiplayer_peer = null", code)

    def test_send_to_all(self):
        tree = make_tree()
        ready = tree.new_node(NN.GKNodeOnReady, "准备")
        send = tree.new_node(NN.GKNodeNetSend, "发消息",
                             gk_channel="聊天", gk_text="大家好啊")
        gk_stub.connect(tree, ready, "执行", send, "执行")
        code, _ = gen(tree, V3)
        self.assertIn('_gk_net_send("聊天", 0, "大家好啊")', code)
        self.assertIn('rpc("_gk_net_msg", channel, _me, text)', code)

    def test_send_to_one_uses_peer_pin(self):
        """『发给谁』接『有人进来』的『谁』-> 直接读那个成员变量。"""
        tree = make_tree()
        joined = tree.new_node(NN.GKNodeNetPeerJoined, "有人进来")
        send = tree.new_node(NN.GKNodeNetSend, "欢迎",
                             gk_mode="ONE", gk_text="欢迎")
        gk_stub.connect(tree, joined, "执行", send, "执行")
        gk_stub.connect(tree, joined, "谁", send, "发给谁")
        code, _ = gen(tree, V3)
        self.assertIn('_gk_net_send("默认", int(_gk_net_peer), "欢迎")', code)
        self.assertIn('rpc_id(to_peer, "_gk_net_msg", channel, _me, text)', code)

    # ---- 事件节点 ----
    def test_peer_joined_is_driven_by_signal(self):
        tree = make_tree()
        joined = tree.new_node(NN.GKNodeNetPeerJoined, "有人进来")
        printer = tree.new_node(NN.GKNodePrint, "日志", gk_text="NET_PEER_JOINED")
        gk_stub.connect(tree, joined, "执行", printer, "执行")
        code, _ = gen(tree, V3)
        # 每个事件节点一条独立执行流
        self.assertIn("func _gk_net_evt_joined() -> void:", code)
        self.assertIn('print("NET_PEER_JOINED")', code)
        # 分发函数把 id 写进变量、再跑那条链
        self.assertIn("func _gk_net_on_peer_connected(id: int) -> void:", code)
        self.assertIn("    _gk_net_peer = id", code)
        self.assertIn("    _gk_net_evt_joined()", code)
        # 信号在 _ready 里接上
        self.assertIn("multiplayer.peer_connected.connect", code)

    def test_peer_left_is_driven_by_signal(self):
        tree = make_tree()
        left = tree.new_node(NN.GKNodeNetPeerLeft, "有人走了")
        printer = tree.new_node(NN.GKNodePrint, "日志", gk_text="NET_PEER_LEFT")
        gk_stub.connect(tree, left, "执行", printer, "执行")
        code, _ = gen(tree, V3)
        self.assertIn("func _gk_net_evt_left() -> void:", code)
        self.assertIn("func _gk_net_on_peer_disconnected(id: int) -> void:", code)
        self.assertIn("multiplayer.peer_disconnected.connect", code)

    def test_message_rpc_channel_filter(self):
        tree = make_tree()
        recv = tree.new_node(NN.GKNodeNetMessage, "收消息", gk_channel="聊天")
        printer = tree.new_node(NN.GKNodePrint, "日志")
        gk_stub.connect(tree, recv, "执行", printer, "执行")
        gk_stub.connect(tree, recv, "内容", printer, "内容")
        code, _ = gen(tree, V3)
        self.assertIn('@rpc("any_peer", "call_remote", "reliable")\n'
                      "func _gk_net_msg(channel: String, sender: int, "
                      "text: String) -> void:", code)
        self.assertIn("    _gk_net_msg_sender = sender", code)
        self.assertIn("    _gk_net_msg_text = text", code)
        self.assertIn('if channel == "聊天":', code)
        self.assertIn("        _gk_net_evt_msg()", code)
        self.assertIn("print(_gk_net_msg_text)", code)

    def test_message_any_channel_skips_filter(self):
        tree = make_tree()
        recv = tree.new_node(NN.GKNodeNetMessage, "收消息", gk_any=True)
        printer = tree.new_node(NN.GKNodePrint, "日志", gk_text="ANY")
        gk_stub.connect(tree, recv, "执行", printer, "执行")
        code, _ = gen(tree, V3)
        self.assertNotIn("if channel ==", code)
        self.assertIn("    _gk_net_evt_msg()", code)

    def test_sender_output_resolves_to_variable(self):
        """『谁发的』接到『发给谁』-> 转回发送者(回声 / 回礼)。"""
        tree = make_tree()
        recv = tree.new_node(NN.GKNodeNetMessage, "收消息")
        send = tree.new_node(NN.GKNodeNetSend, "回礼",
                             gk_mode="ONE", gk_text="收到")
        gk_stub.connect(tree, recv, "执行", send, "执行")
        gk_stub.connect(tree, recv, "谁发的", send, "发给谁")
        code, _ = gen(tree, V3)
        self.assertIn('int(_gk_net_msg_sender)', code)

    def test_two_message_nodes_get_distinct_funcs(self):
        tree = make_tree()
        first = tree.new_node(NN.GKNodeNetMessage, "收A", gk_channel="A")
        second = tree.new_node(NN.GKNodeNetMessage, "收B", gk_channel="B")
        for node in (first, second):
            printer = tree.new_node(NN.GKNodePrint, "日志", gk_text="X")
            gk_stub.connect(tree, node, "执行", printer, "执行")
        code, _ = gen(tree, V3)
        self.assertIn("func _gk_net_evt_msg() -> void:", code)
        self.assertIn("func _gk_net_evt_msg_2() -> void:", code)
        self.assertIn('if channel == "A":', code)
        self.assertIn('if channel == "B":', code)

    # ---- 不影响老图 ----
    def test_no_net_nodes_writes_nothing(self):
        tree = make_tree()
        ready = tree.new_node(NN.GKNodeOnReady, "准备")
        printer = tree.new_node(NN.GKNodePrint, "日志", gk_text="你好")
        gk_stub.connect(tree, ready, "执行", printer, "执行")
        code, _ = gen(tree, V3)
        self.assertNotIn("_gk_net", code)
        self.assertNotIn("@rpc", code)

    def test_helpers_only_once_with_many_nodes(self):
        """一堆联机节点也只生成一份辅助函数(同名函数会拒绝整个脚本)。"""
        code, _ = gen(self._host_tree(), V3)
        self.assertEqual(code.count("func _gk_net_start("), 1)
        self.assertEqual(code.count("func _gk_net_send("), 1)
        self.assertEqual(code.count("func _gk_net_msg("), 1)
        self.assert_indent_sane(code)


class TestRound101SpecTexture(Base):
    """第一百零一轮:高光贴图(形状剖面图 + UV 遮罩图)。

    ── 这一层锁住的三条硬事实(改代码前先看)────────────────────────
    1. **形状图必须用连续坐标采样**。第一版把二值的 `spot`(硬色带 0/1)
       直接当横坐标去采样 —— 那样只会读到渐变的两个端点像素,
       "形状贴图"等于没用(设计时就发现并改掉了);
    2. `read_params` 的契约是"一条 PARAM_MAP 一个值",高光贴图的
       `use_*` 开关**不能**加进去(加了会当场弄红 material_params 那条);
    3. 开关由导出侧按"有没有落盘"写,值永远自洽(不会出现"开着的图没了")。
    """

    @staticmethod
    def _shader_text():
        from BlenderGameKit import npr_preview
        path = os.path.join(os.path.dirname(os.path.abspath(npr_preview.__file__)),
                            "shaders", "gk_toon.gdshader")
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    class FakeMat(dict):
        """最小材质替身:只有导出链真正读的那几个属性。"""

        name = "假材质"
        use_nodes = False
        use_backface_culling = False

    # ---- 着色器 ----
    def test_shader_declares_two_texture_uniforms(self):
        text = self._shader_text()
        for uniform in ("specular_shape_texture", "specular_mask_texture"):
            self.assertIn(f"uniform sampler2D {uniform}", text)
            self.assertIn(f"uniform float use_{uniform}", text)
            self.assertIn(f"if (use_{uniform} > 0.5) {{", text)

    def test_shape_texture_uses_continuous_coordinate(self):
        text = self._shader_text()
        self.assertIn("texture(specular_shape_texture, vec2(u, 0.5))", text)
        self.assertIn("float half_spot = dot(n_view, half_vec);", text)
        # 二值坐标那条老写法必须已经不在了(否则贴图等于摆设)
        self.assertNotIn("texture(specular_shape_texture, vec2(spot, 0.5))", text)

    def test_mask_texture_uses_uv(self):
        self.assertIn("texture(specular_mask_texture, UV).r", self._shader_text())

    def test_highlight_knobs_are_real_uniforms_and_params(self):
        from BlenderGameKit import npr_preview as npr
        names = {entry[0] for entry in npr.PARAM_MAP}
        self.assertLessEqual(
            {"specular_amount", "specular_size", "specular_smooth",
             "specular_color"}, names)
        text = self._shader_text()
        self.assertIn("uniform float specular_size", text)
        self.assertIn("uniform float specular_smooth", text)
        self.assertIn("uniform vec4 specular_color", text)

    # ---- 材质上的读写 ----
    def test_texture_keys_are_chinese_and_distinct(self):
        from BlenderGameKit import npr_preview as npr
        keys = [npr.texture_key(u) for u in npr.TEXTURE_KEYS]
        self.assertEqual(len(keys), len(set(keys)))
        for key in keys:
            self.assertTrue(any("\u4e00" <= ch <= "\u9fff" for ch in key), key)
            self.assertIn(key, [npr.texture_key(u) for u in npr.TEXTURE_KEYS])
            self.assertTrue(npr.texture_hint(
                [u for u in npr.TEXTURE_KEYS if npr.texture_key(u) == key][0]))

    def test_get_set_texture_round_trip(self):
        from BlenderGameKit import npr_preview as npr
        mat = self.FakeMat()
        self.assertEqual(npr.get_texture(mat, "specular_shape_texture"), "")
        npr.set_texture(mat, "specular_shape_texture", "形状图")
        self.assertEqual(npr.get_texture(mat, "specular_shape_texture"), "形状图")
        self.assertEqual(mat[npr.texture_key("specular_shape_texture")], "形状图")
        # 另一槽位不受影响
        self.assertEqual(npr.get_texture(mat, "specular_mask_texture"), "")
        npr.set_texture(mat, "specular_shape_texture", "")
        self.assertEqual(npr.get_texture(mat, "specular_shape_texture"), "")
        self.assertNotIn(npr.texture_key("specular_shape_texture"), mat)

    def test_read_params_keeps_one_entry_per_param(self):
        from BlenderGameKit import npr_preview as npr
        params = npr.read_params(self.FakeMat())
        self.assertEqual(len(params), len(npr.PARAM_MAP))
        self.assertFalse([key for key in params if key.startswith("use_")])

    # ---- 导出 ----
    def test_export_without_textures_writes_switches_off(self):
        from BlenderGameKit import asset_export as ae
        mat = self.FakeMat()
        with tempfile.TemporaryDirectory() as folder:
            path, _count = ae.write_npr_material(folder, mat)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        self.assertIn("shader_parameter/use_specular_shape_texture = 0.0", text)
        self.assertIn("shader_parameter/use_specular_mask_texture = 0.0", text)
        self.assertNotIn("specular_shape_texture = ExtResource", text)

    def test_export_with_texture_writes_resource_and_switch(self):
        from BlenderGameKit import asset_export as ae
        from BlenderGameKit import curve_assets, npr_preview as npr

        class _Image:
            name = "形状图"
            size = (4, 4)

        mat = self.FakeMat()
        npr.set_texture(mat, "specular_shape_texture", "形状图")
        with mock.patch.object(ae, "_find_image", return_value=_Image()), \
                mock.patch.object(ae, "_save_image"):
            with tempfile.TemporaryDirectory() as folder:
                path, _count = ae.write_npr_material(folder, mat)
                with open(path, encoding="utf-8") as handle:
                    text = handle.read()
        file_name = curve_assets.image_file_name("形状图")
        self.assertIn("shader_parameter/use_specular_shape_texture = 1.0", text)
        # sorted(TEXTURE_KEYS):mask 先(0)、shape 后(1)—— id 跟着这个顺序
        self.assertIn(f'path="res://art/{file_name}" id="spec_1"', text)
        self.assertIn('shader_parameter/specular_shape_texture = '
                      'ExtResource("spec_1")', text)
        self.assertIn("shader_parameter/use_specular_mask_texture = 0.0", text)
        # load_steps 必须跟着多出来的 ext_resource 涨(少了 Godot 不认)
        self.assertIn("load_steps=3", text)

    def test_missing_image_warns_and_keeps_switch_off(self):
        from BlenderGameKit import asset_export as ae
        from BlenderGameKit import npr_preview as npr
        mat = self.FakeMat()
        npr.set_texture(mat, "specular_shape_texture", "被删掉的图")
        with mock.patch.object(ae, "_find_image", return_value=None):
            with tempfile.TemporaryDirectory() as folder:
                path, _count = ae.write_npr_material(folder, mat)
                with open(path, encoding="utf-8") as handle:
                    text = handle.read()
        self.assertIn("shader_parameter/use_specular_shape_texture = 0.0", text)
        self.assertNotIn("ExtResource(\"spec_1\")", text)

    def test_scene_export_uses_npr_path_only_when_params_exist(self):
        """整场景导出:有 NPR 参数的材质走完整导出,老材质还是老模板。

        ⚠ 这一条是**必须的**:场景导出原来写死用 TOON_MATERIAL_TEMPLATE
        (`specular_amount = 0.0`)—— 高光参数根本到不了游戏里。
        """
        import inspect
        from BlenderGameKit import asset_export as ae
        source = inspect.getsource(ae.write_materials)
        self.assertIn('has_param(material, "specular_amount")', source)
        self.assertIn("write_npr_material(", source)


class TestRound102Collections(Base):
    """第一百零二轮:保留 Blender 集合层级的场景树。

    ── 这一层锁住的四条事实(改代码前先看)────────────────────────────
    1. glb 里那层"场景根集合"(Scene Collection)导出后要**展平** ——
       Godot 里直接看到用户自己的集合名(`关卡一/装饰/树`);
    2. 展平**只改 `scenes[0].nodes` 和根集合自己的 `children`**,
       `nodes` 数组一个字节都不动(动画通道 / skin 的索引引用
       不可能被弄坏 —— 删节点重排索引是另一条路,别走);
    3. 与 `.tscn` 根下节点重名的顶层集合(碰撞 / 粒子 / 环境 / 导航 /
       遮挡)**保留一层前缀**,否则 Godot 里两个同名兄弟节点会打架;
    4. 只在"唯一一个根 + 名字对得上"时才动手 —— 老版本 Blender 不支持
       集合模式,导出的是扁平的多个根,绝不能误伤。
    """

    @staticmethod
    def _payload(children_names, root_name="Scene Collection", extra_roots=0):
        """模拟 Blender 集合模式的 glb:根(集合)-> 一串子节点。

        前几个子节点是"集合 / 物体"都行 —— 这个函数只关心名字。
        """
        nodes = [{"name": root_name}]
        children = []
        for name in children_names:
            children.append(len(nodes))
            nodes.append({"name": name})
        nodes[0]["children"] = children
        roots = [0]
        for index in range(extra_roots):
            roots.append(len(nodes))
            nodes.append({"name": f"游离物体{index}"})
        return {"nodes": nodes, "scenes": [{"nodes": roots}]}

    @staticmethod
    def _tiny_glb(payload, binary=b"\x01\x02\x03\x04"):
        """把 payload 拼成最小合法 .glb(JSON 块 + BIN 块)。"""
        import json
        import struct

        text = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        text += b" " * ((4 - len(text) % 4) % 4)
        total = 12 + 8 + len(text) + 8 + len(binary)
        blob = struct.pack("<III", 0x46546C67, 2, total)
        blob += struct.pack("<II", len(text), 0x4E4F534A) + text
        blob += struct.pack("<II", len(binary), 0x004E4942) + binary
        return blob

    # ---- 纯逻辑:展平 ----
    def test_flatten_lifts_children_to_root(self):
        from BlenderGameKit import asset_export as ae
        payload = self._payload(["关卡一", "地面"])
        _payload, kept, changed = ae.flatten_collection_root(payload, "Scene Collection")
        self.assertTrue(changed)
        self.assertEqual(kept, [])
        self.assertEqual(payload["scenes"][0]["nodes"], [1, 2])
        # 根集合的 children 已清空(它不再被任何地方引用 -> Godot 直接忽略)
        self.assertNotIn("children", payload["nodes"][0])

    def test_nested_collections_survive(self):
        """嵌套集合是**节点自己的 children**,展平不动它们。"""
        from BlenderGameKit import asset_export as ae
        payload = self._payload(["关卡一"])
        payload["nodes"].append({"name": "装饰", "children": []})
        payload["nodes"].append({"name": "树"})
        payload["nodes"][3]["children"] = [4]
        payload["nodes"][1]["children"] = [3]
        ae.flatten_collection_root(payload, "Scene Collection")
        self.assertEqual(payload["nodes"][1]["children"], [3])
        self.assertEqual(payload["nodes"][3]["children"], [4])

    def test_reserved_collection_keeps_prefix(self):
        from BlenderGameKit import asset_export as ae
        payload = self._payload(["Colliders", "关卡一"])
        _payload, kept, changed = ae.flatten_collection_root(payload, "Scene Collection")
        self.assertTrue(changed)
        self.assertEqual(kept, ["Colliders"])
        # 根集合保留(它挂着撞名那个),其它提升
        self.assertEqual(payload["scenes"][0]["nodes"], [0, 2])
        self.assertEqual(payload["nodes"][0]["children"], [1])

    def test_all_reserved_keeps_everything(self):
        from BlenderGameKit import asset_export as ae
        payload = self._payload(["Colliders", "Particles"])
        _payload, kept, changed = ae.flatten_collection_root(payload, "Scene Collection")
        self.assertFalse(changed)
        self.assertEqual(sorted(kept), ["Colliders", "Particles"])
        self.assertEqual(payload["scenes"][0]["nodes"], [0])
        self.assertEqual(payload["nodes"][0]["children"], [1, 2])

    def test_multiple_roots_untouched(self):
        """老版本 Blender(没有集合模式)导的是"多个平根"—— 不许动。"""
        from BlenderGameKit import asset_export as ae
        payload = self._payload(["关卡一"], extra_roots=1)
        before = list(payload["scenes"][0]["nodes"])
        _payload, kept, changed = ae.flatten_collection_root(payload, "Scene Collection")
        self.assertFalse(changed)
        self.assertEqual(kept, [])
        self.assertEqual(payload["scenes"][0]["nodes"], before)
        self.assertEqual(payload["nodes"][0]["children"], [1])

    def test_wrong_root_name_untouched(self):
        from BlenderGameKit import asset_export as ae
        payload = self._payload(["关卡一"], root_name="别的集合")
        _payload, _kept, changed = ae.flatten_collection_root(
            payload, "Scene Collection")
        self.assertFalse(changed)
        self.assertEqual(payload["scenes"][0]["nodes"], [0])

    def test_empty_root_untouched(self):
        from BlenderGameKit import asset_export as ae
        payload = {"nodes": [{"name": "Scene Collection"}],
                   "scenes": [{"nodes": [0]}]}
        _payload, _kept, changed = ae.flatten_collection_root(
            payload, "Scene Collection")
        self.assertFalse(changed)

    def test_reserved_name_match(self):
        from BlenderGameKit import asset_export as ae
        for name in ("Colliders", "Particles", "Environment",
                     "WorldEnvironment", "Navigation",
                     "Navigation_2", "Navigation_10", "Occluder_0",
                     "Occluder_12"):
            self.assertTrue(ae.is_scene_root_reserved(name), name)
        for name in ("关卡一", "Colliders2", "Navigation_x", "Occluder",
                     "Occluder_", "装饰", "", None, "environment"):
            self.assertFalse(ae.is_scene_root_reserved(name), name)

    # ---- 纯逻辑:glb 读写 ----
    def test_glb_round_trip_keeps_binary(self):
        import struct
        from BlenderGameKit import asset_export as ae
        payload = self._payload(["关卡一", "地面"])
        blob = self._tiny_glb(payload, b"\x0a\x0b\x0c\x0d")
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "test.glb")
            with open(path, "wb") as handle:
                handle.write(blob)
            loaded = ae.read_glb_payload(path)
            self.assertEqual(loaded["scenes"][0]["nodes"], [0])
            ae.flatten_collection_root(loaded, "Scene Collection")
            self.assertTrue(ae.write_glb_payload(path, loaded))
            with open(path, "rb") as handle:
                data = handle.read()
        magic, version, total = struct.unpack_from("<III", data, 0)
        self.assertEqual((magic, version), (0x46546C67, 2))
        self.assertEqual(total, len(data))          # 头里的总长必须对得上
        self.assertEqual(len(data) % 4, 0)          # 规范:4 字节对齐
        text_len, kind = struct.unpack_from("<II", data, 12)
        self.assertEqual(kind, 0x4E4F534A)
        self.assertEqual(text_len % 4, 0)
        bin_len, bin_kind = struct.unpack_from("<II", data, 20 + text_len)
        self.assertEqual(bin_kind, 0x004E4942)
        binary = data[28 + text_len:28 + text_len + bin_len]
        self.assertEqual(binary, b"\x0a\x0b\x0c\x0d")   # 顶点数据原样

    def test_flatten_scene_glb_leaves_flat_file_alone(self):
        from BlenderGameKit import asset_export as ae
        payload = self._payload(["关卡一"], extra_roots=1)
        blob = self._tiny_glb(payload)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "flat.glb")
            with open(path, "wb") as handle:
                handle.write(blob)
            changed, kept, note = ae.flatten_scene_glb(path, "Scene Collection")
            with open(path, "rb") as handle:
                after = handle.read()
        self.assertFalse(changed)
        self.assertEqual(kept, [])
        self.assertEqual(note, "")
        self.assertEqual(after, blob)               # 一个字节都不该动

    def test_flatten_scene_glb_fixes_collection_export(self):
        from BlenderGameKit import asset_export as ae
        payload = self._payload(["关卡一", "Colliders"])
        blob = self._tiny_glb(payload)
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "coll.glb")
            with open(path, "wb") as handle:
                handle.write(blob)
            changed, kept, note = ae.flatten_scene_glb(path, "Scene Collection")
            after = ae.read_glb_payload(path)
        self.assertTrue(changed)
        self.assertEqual(kept, ["Colliders"])
        self.assertEqual(note, "")
        self.assertEqual(after["scenes"][0]["nodes"], [0, 1])

    # ---- 接线:导出流程真的开了集合模式、也真的收了提示 ----
    def test_export_scene_glb_turns_collection_mode_on(self):
        import inspect
        from BlenderGameKit import asset_export as ae
        source = inspect.getsource(ae.export_scene_glb)
        self.assertIn('"export_hierarchy_full_collections": True', source)
        self.assertIn("flatten_scene_glb(", source)
        self.assertIn('scene.collection', source)

    def test_pipeline_collects_collection_notes(self):
        import inspect
        from BlenderGameKit import asset_export as ae
        source = inspect.getsource(ae.export_assets_pipeline)
        self.assertIn('export_scene_glb, "last_notes"', source)

    def test_character_and_asset_glb_stay_flat(self):
        """角色 / 资产 glb 是"一个模型一次"—— 不能开集合模式。

        它们靠 `_glb_root_inverse_transform` 抵消 glb 根节点变换,
        多一层集合节点就会算错。
        """
        import inspect
        from BlenderGameKit import asset_export as ae
        for func in (ae.export_subtree_glb, ae.export_glb):
            self.assertNotIn("export_hierarchy_full_collections",
                             inspect.getsource(func))


if __name__ == "__main__":
    unittest.main(verbosity=2)
