"""Reroute traversal and suspension diagnostics, without Blender."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gk_stub

gk_stub.install()
gk_stub.load_addon(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              "BlenderGameKit"))
from BlenderGameKit import codegen
from BlenderGameKit.codegen.graph import downstream, upstream
from BlenderGameKit.codegen.validation import _check_suspending_flow_fanout
from BlenderGameKit.nodes import nodes as NN
from BlenderGameKit.nodes.node_base import StubSocketCollection


class Reroute:
    bl_idname = "NodeReroute"
    bl_label = "转接点"

    def __init__(self):
        self.inputs = StubSocketCollection(self)
        self.outputs = StubSocketCollection(self, is_output=True)
        self.inputs.new("NodeSocketVirtual", "Input")
        self.outputs.new("NodeSocketVirtual", "Output")


def reroute(tree, source, output):
    node = tree.new_node(Reroute)
    gk_stub.connect(tree, source, output, node, "Input")
    return node


class TestGraphTraversal(unittest.TestCase):
    def setUp(self):
        self.tree = gk_stub.StubNodeTree()
        self.ready = self.tree.new_node(NN.GKNodeOnReady, "准备")

    def connect(self, source, output, target, entry="执行"):
        return gk_stub.connect(self.tree, source, output, target, entry)

    def diagnostics(self):
        warnings = []
        _check_suspending_flow_fanout(self.tree, warnings)
        return [item for item in warnings
                if item.code == "SUSPENDING_FLOW_FANOUT"]

    def test_long_reroute_chain_has_no_python_recursion_limit(self):
        current, output = self.ready, "执行"
        for _ in range(1600):
            current = reroute(self.tree, current, output)
            output = "Output"
        target = self.tree.new_node(NN.GKNodePrint)
        self.connect(current, output, target)
        self.assertEqual(downstream(self.ready.outputs.get("执行")),
                         [(target, "执行")])
        self.assertIs(upstream(target.inputs.get("执行")).from_node, self.ready)
        self.assertIn("print(", codegen.generate(self.tree)[0])

    def test_reroute_cycle_reports_actionable_error(self):
        first = reroute(self.tree, self.ready, "执行")
        second = reroute(self.tree, first, "Output")
        self.connect(second, "Output", first, "Input")
        with self.assertRaisesRegex(ValueError, "转接点形成循环"):
            downstream(self.ready.outputs.get("执行"))
        with self.assertRaisesRegex(codegen.CodegenAbort, "转接点形成循环"):
            codegen.generate(self.tree)

    def test_shared_endpoint_is_not_a_cycle_or_deduplicated(self):
        first = reroute(self.tree, self.ready, "执行")
        second = reroute(self.tree, self.ready, "执行")
        shared = reroute(self.tree, first, "Output")
        self.connect(second, "Output", shared, "Input")
        target = self.tree.new_node(NN.GKNodePrint)
        self.connect(shared, "Output", target)
        self.assertEqual(downstream(self.ready.outputs.get("执行")),
                         [(target, "执行"), (target, "执行")])

    def test_reroutes_preserve_link_order_and_entry_name(self):
        first = reroute(self.tree, self.ready, "执行")
        target = self.tree.new_node(Reroute, "双入口测试")
        target.bl_idname = "TestTarget"
        target.inputs.new("GKSocketFlow", "重播")
        self.connect(first, "Output", target, "重播")
        other = self.tree.new_node(NN.GKNodePrint)
        self.connect(self.ready, "执行", other)
        self.assertEqual(downstream(self.ready.outputs.get("执行")),
                         [(target, "重播"), (other, "执行")])

    def test_fanout_hidden_by_reroute_warns_at_real_output(self):
        branch = reroute(self.tree, self.ready, "执行")
        wait = self.tree.new_node(NN.GKNodeWait)
        printer = self.tree.new_node(NN.GKNodePrint)
        self.connect(branch, "Output", wait)
        self.connect(branch, "Output", printer)
        result = codegen.generate_result(self.tree)
        warnings = [item for item in result.diagnostics
                    if item.code == "SUSPENDING_FLOW_FANOUT"]
        self.assertEqual([item.node_name for item in warnings], ["准备"])

    def test_wait_after_intermediate_reroutes_is_found(self):
        first = self.tree.new_node(NN.GKNodePrint)
        other = self.tree.new_node(NN.GKNodePrint)
        self.connect(self.ready, "执行", first)
        self.connect(self.ready, "执行", other)
        wire = reroute(self.tree, first, "完成")
        wire = reroute(self.tree, wire, "Output")
        wait = self.tree.new_node(NN.GKNodeWait)
        self.connect(wire, "Output", wait)
        self.assertEqual([item.node_name for item in self.diagnostics()], ["准备"])

    def test_sequence_with_waiting_numbered_branch_is_explicit(self):
        sequence = self.tree.new_node(NN.GKNodeSequence, gk_step_count=2)
        self.connect(self.ready, "执行", sequence)
        for output, cls in (("1", NN.GKNodePrint), ("2", NN.GKNodeWait)):
            wire = reroute(self.tree, sequence, output)
            self.connect(wire, "Output", self.tree.new_node(cls))
        self.assertEqual(self.diagnostics(), [])

    def test_non_waiting_fanout_and_unconnected_wait_are_not_reported(self):
        wire = reroute(self.tree, self.ready, "执行")
        for _ in range(2):
            self.connect(wire, "Output", self.tree.new_node(NN.GKNodePrint))
        self.tree.new_node(NN.GKNodeWait)
        self.assertEqual(self.diagnostics(), [])

    def test_flow_cycle_does_not_stall_diagnostic(self):
        first = self.tree.new_node(NN.GKNodePrint, "有环的分支")
        second = self.tree.new_node(NN.GKNodePrint)
        self.connect(first, "完成", second)
        self.connect(second, "完成", first)
        self.connect(first, "完成", self.tree.new_node(NN.GKNodeWait))
        self.assertEqual([item.node_name for item in self.diagnostics()],
                         ["有环的分支"])

    def test_diagnostic_uses_tree_link_index(self):
        wire = reroute(self.tree, self.ready, "执行")
        self.connect(wire, "Output", self.tree.new_node(NN.GKNodeWait))
        self.connect(wire, "Output", self.tree.new_node(NN.GKNodePrint))

        class UnreadableLinks:
            def __iter__(self):
                raise AssertionError("Diagnostic rescanned socket.links")

        for node in self.tree.nodes:
            for socket in node.outputs:
                socket.links = UnreadableLinks()
        self.assertEqual(len(self.diagnostics()), 1)


if __name__ == "__main__":
    unittest.main()
