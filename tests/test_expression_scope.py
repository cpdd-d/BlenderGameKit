"""Generated expression temporaries belong to their execution scope."""

import re
import unittest

import test_codegen as support
from BlenderGameKit.codegen.context import GenContext


class TestExpressionScope(unittest.TestCase):
    def make_tree(self):
        tree = support.make_tree()
        event = tree.new_node(support.NN.GKNodeOnReady, "ready")
        direction = tree.new_node(support.NN.GKNodeInputDirection, "direction")
        return tree, event, direction

    def add_print(self, tree, source, output, direction, name):
        node = tree.new_node(support.NN.GKNodePrint, name)
        support.gk_stub.connect(tree, source, output, node, "执行")
        support.gk_stub.connect(tree, direction, "左右", node, "内容")
        return node

    def assert_local_input(self, body, indent):
        """Each print must read the temporary declared at its own indentation."""
        printed = re.findall(r"(?m)^" + " " * indent + r"print\((_in_x\d+)\)$", body)
        self.assertTrue(printed, body)
        for name in printed:
            self.assertRegex(body, r"(?m)^" + " " * indent + r"var " + name + r" := ")

    def test_shared_direction_in_different_events_declares_in_both(self):
        tree, event, direction = self.make_tree()
        self.add_print(tree, event, "执行", direction, "ready_print")
        physics = tree.new_node(support.NN.GKNodeOnPhysicsProcess, "physics")
        self.add_print(tree, physics, "执行", direction, "physics_print")
        code, warnings = support.gen(tree, support.V3)
        self.assertEqual(warnings, [])
        functions = support.Base.functions(code)
        self.assert_local_input(functions["_ready"], 4)
        self.assert_local_input(functions["_physics_process"], 4)

    def test_nested_branch_expression_stays_inside_branch(self):
        tree, event, direction = self.make_tree()
        branch = tree.new_node(support.NN.GKNodeBranch, "branch")
        support.gk_stub.connect(tree, event, "执行", branch, "执行")
        self.add_print(tree, branch, "是", direction, "yes")
        code, warnings = support.gen(tree, support.V3)
        self.assertEqual(warnings, [])
        self.assertIn("    if false:\n        var _in_x", code)
        self.assert_local_input(code, 8)

    def test_sibling_branches_each_declare_their_input(self):
        tree, event, direction = self.make_tree()
        branch = tree.new_node(support.NN.GKNodeBranch, "branch")
        support.gk_stub.connect(tree, event, "执行", branch, "执行")
        self.add_print(tree, branch, "是", direction, "yes")
        self.add_print(tree, branch, "否", direction, "no")
        code, warnings = support.gen(tree, support.V3)
        self.assertEqual(warnings, [])
        yes, no = code.split("    else:\n", 1)
        self.assert_local_input(yes, 8)
        self.assert_local_input(no, 8)

    def test_nested_raycast_prelude_stays_with_its_condition(self):
        tree, event, _direction = self.make_tree()
        outer = tree.new_node(support.NN.GKNodeBranch, "outer")
        inner = tree.new_node(support.NN.GKNodeBranch, "inner")
        ray = tree.new_node(support.NN.GKNodeRaycastCheck, "ray")
        support.gk_stub.connect(tree, event, "执行", outer, "执行")
        support.gk_stub.connect(tree, outer, "是", inner, "执行")
        support.gk_stub.connect(tree, ray, "有东西", inner, "条件")
        code, warnings = support.gen(tree, support.V3)
        self.assertEqual(warnings, [])
        self.assertIn("    if false:\n        var _space", code)
        self.assertRegex(code, r"(?m)^        if not _hit\d+\.is_empty\(\):$")
        self.assertNotRegex(code, r"(?m)^    var _(space|query|hit)\d+")

    def test_loop_expression_is_evaluated_inside_the_loop(self):
        tree, event, direction = self.make_tree()
        loop = tree.new_node(support.NN.GKNodeForLoop, "loop")
        support.gk_stub.connect(tree, event, "执行", loop, "执行")
        self.add_print(tree, loop, "循环体", direction, "loop_print")
        self.add_print(tree, loop, "结束后", direction, "after_print")
        code, warnings = support.gen(tree, support.V3)
        self.assertEqual(warnings, [])
        self.assertIn("    for i in int(3):\n        var _in_x", code)
        self.assert_local_input(code, 8)
        self.assert_local_input(code, 4)

    def test_multiple_uses_within_one_statement_keep_expression_cache(self):
        tree, event, direction = self.make_tree()
        add = tree.new_node(support.NN.GKNodeMath, "add")
        printer = tree.new_node(support.NN.GKNodePrint, "printer")
        for pin in ("A", "B"):
            support.gk_stub.connect(tree, direction, "左右", add, pin)
        support.gk_stub.connect(tree, add, "结果", printer, "内容")
        support.gk_stub.connect(tree, event, "执行", printer, "执行")
        code, warnings = support.gen(tree, support.V3)
        self.assertEqual(warnings, [])
        self.assertEqual(code.count("Input.get_action_strength(ACTION_MOVE_RIGHT)"), 1)
        self.assertIn("print((_in_x1 + _in_x1))", code)

    def test_capture_and_new_function_do_not_share_expression_temporaries(self):
        tree, _event, direction = self.make_tree()
        ctx = GenContext(tree, support.V3)
        outer = ctx.new_section()
        ctx._current_indent = ctx._expr_indent = 2
        original = ctx.node_expression(direction, "左右")
        with ctx.capture() as captured:
            inner = ctx.node_expression(direction, "左右")
            self.assertNotEqual(inner, original)
        self.assertIs(ctx.sink, outer)
        self.assertEqual(ctx._expr_indent, 2)
        self.assertEqual(ctx.node_expression(direction, "左右"), original)
        self.assertTrue(any(line.startswith("    var " + inner) for line in captured.lines))
        ctx.new_section()
        self.assertNotEqual(ctx.node_expression(direction, "左右"), original)

    def test_button_edge_is_shared_across_statements_and_events(self):
        for node_class, source in (
                (support.NN.GKNodeJoyButtonDown, "Input.is_joy_button_pressed"),
                (support.NN.GKNodeMouseButtonDown, "Input.is_mouse_button_pressed")):
            with self.subTest(node=node_class.bl_idname):
                tree = support.make_tree()
                down = tree.new_node(node_class, "down")
                for event_class in (support.NN.GKNodeOnProcess,
                                    support.NN.GKNodeOnPhysicsProcess):
                    event = tree.new_node(event_class, event_class.bl_idname)
                    sequence = tree.new_node(support.NN.GKNodeSequence,
                                             event.name + "_sequence", gk_step_count=2)
                    support.gk_stub.connect(tree, event, "执行", sequence, "执行")
                    for index in (1, 2):
                        branch = tree.new_node(support.NN.GKNodeBranch,
                                               event.name + str(index))
                        support.gk_stub.connect(tree, sequence, str(index), branch, "执行")
                        support.gk_stub.connect(tree, down, "刚按下", branch, "条件")
                code, warnings = support.gen(tree, support.V3)
                self.assertEqual(warnings, [])
                self.assertEqual(code.count(source), 1)
                self.assertEqual(code.count("[[-1, false, false], [-1, false, false]]"), 1)
                self.assertIn("Engine.get_physics_frames()", code)
                self.assertIn("Engine.get_process_frames()", code)
                self.assertIn("if int(sample[0]) != frame:", code)
                functions = support.Base.functions(code)
                for event in ("_process", "_physics_process"):
                    self.assertNotIn(source, functions[event])
                    self.assertEqual(functions[event].count("_gk_button_down()"), 3)


if __name__ == "__main__":
    unittest.main()
