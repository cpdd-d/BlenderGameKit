"""Expression graph cycles must fail before emitting invalid GDScript."""

import unittest
from unittest import mock

import test_codegen as support
from BlenderGameKit.codegen.context import CodegenAbort, GenContext
from BlenderGameKit.codegen.registry import EXPR_HANDLERS, MULTI_OUTPUT_FUNCS


class TestExpressionCycles(unittest.TestCase):
    def make_print_tree(self):
        tree = support.make_tree()
        event = tree.new_node(support.NN.GKNodeOnReady, "ready")
        printer = tree.new_node(support.NN.GKNodePrint, "printer")
        support.gk_stub.connect(tree, event, "执行", printer, "执行")
        return tree, printer

    def test_math_self_cycle_aborts_instead_of_silently_using_null(self):
        tree, printer = self.make_print_tree()
        node = tree.new_node(support.NN.GKNodeMath, "math_cycle")
        support.gk_stub.connect(tree, node, "结果", node, "A")
        support.gk_stub.connect(tree, node, "结果", printer, "内容")
        with self.assertRaisesRegex(CodegenAbort, "取值连线循环.*math_cycle"):
            support.gen(tree, support.V3)

    def test_vector_split_cycle_aborts_before_recursion_limit(self):
        tree, printer = self.make_print_tree()
        split = tree.new_node(support.NN.GKNodeVectorSplit, "split_cycle")
        vector = tree.new_node(support.NN.GKNodeVectorMake, "vector")
        support.gk_stub.connect(tree, split, "X", vector, "X")
        support.gk_stub.connect(tree, vector, "向量", split, "向量")
        support.gk_stub.connect(tree, split, "X", printer, "内容")
        with self.assertRaisesRegex(CodegenAbort, "取值连线循环.*split_cycle"):
            support.gen(tree, support.V3)

    def test_uncached_expression_cycle_aborts_without_partial_ray_code(self):
        tree = support.make_tree()
        ray = tree.new_node(support.NN.GKNodeRaycastCheck, "ray_cycle")
        support.gk_stub.connect(tree, ray, "有东西", ray, "距离")
        ctx = GenContext(tree, support.V3)
        with self.assertRaisesRegex(CodegenAbort, "取值连线循环.*ray_cycle"):
            ctx.node_expression(ray, "有东西")
        self.assertEqual(ctx.sink.lines, [])
        self.assertEqual(ctx._expr_active, set())

    def test_custom_multi_output_handler_is_also_guarded(self):
        tree = support.make_tree()
        node = tree.new_node(support.NN.GKNodeInputDirection, "custom_cycle")
        ctx = GenContext(tree, support.V3)

        def recursive(context, current):
            return {"左右": context.node_expression(current, "左右")}

        with mock.patch.dict(MULTI_OUTPUT_FUNCS, {node.bl_idname: recursive}):
            with self.assertRaisesRegex(CodegenAbort, "取值连线循环.*custom_cycle"):
                ctx.node_expression(node, "左右")
        self.assertEqual(ctx._expr_active, set())

    def test_fatal_error_propagates_through_ordinary_expression(self):
        tree = support.make_tree()
        outer = tree.new_node(support.NN.GKNodeMath, "outer")
        inner = tree.new_node(support.NN.GKNodeConstFloat, "inner")
        support.gk_stub.connect(tree, inner, "值", outer, "A")
        ctx = GenContext(tree, support.V3)

        def abort(_ctx, _node):
            raise CodegenAbort("转接点形成循环")

        with mock.patch.dict(EXPR_HANDLERS, {inner.bl_idname: abort}):
            with self.assertRaisesRegex(CodegenAbort, "转接点形成循环"):
                ctx.node_expression(outer, "结果")
        self.assertEqual(ctx._expr_active, set())
        # Failed evaluations must not leave placeholder null values behind.
        self.assertEqual(ctx.node_expression(outer, "结果"), "(0.0 + 0.0)")

    def test_shared_acyclic_input_is_not_a_cycle(self):
        tree, printer = self.make_print_tree()
        node = tree.new_node(support.NN.GKNodeMath, "sum")
        value = tree.new_node(support.NN.GKNodeConstFloat, "value", gk_value=2.5)
        for pin in ("A", "B"):
            support.gk_stub.connect(tree, value, "值", node, pin)
        support.gk_stub.connect(tree, node, "结果", printer, "内容")
        code, warnings = support.gen(tree, support.V3)
        self.assertIn("print((2.5 + 2.5))", code)
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
