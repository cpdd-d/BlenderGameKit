"""Regression checks for missed execution wires discovered in game examples."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gk_stub

gk_stub.install()
gk_stub.load_addon(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              "BlenderGameKit"))
from BlenderGameKit import codegen
from BlenderGameKit.codegen.graph_audit import audit_execution_flow
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


class TestExecutionFlowAudit(unittest.TestCase):
    def setUp(self):
        self.tree = gk_stub.StubNodeTree("医务室逻辑")
        self.ready = self.tree.new_node(NN.GKNodeOnReady)

    def connect(self, source, output, target, entry="执行"):
        return gk_stub.connect(self.tree, source, output, target, entry)

    def names(self):
        return [item.node_name for item in audit_execution_flow(self.tree)]

    def test_hud_data_wire_does_not_run_the_update(self):
        text = self.tree.new_node(NN.GKNodeConstText, gk_value="60%")
        hud = self.tree.new_node(NN.GKNodeUiSet, "刷新手电电量")
        self.connect(text, "值", hud, "文字")
        result = codegen.generate_result(self.tree)
        warnings = [d for d in result.diagnostics if d.code == "UNREACHABLE_EXECUTION"]
        self.assertEqual([d.node_name for d in warnings], ["刷新手电电量"])
        self.assertIn("医务室逻辑", warnings[0].message)
        self.assertIn("金色执行", warnings[0].hint)
        self.assertTrue(any("刷新手电电量" in w for w in result.warnings))
        self.connect(self.ready, "执行", hud)
        self.assertEqual(self.names(), [])

    def test_connected_island_is_still_unreachable(self):
        first = self.tree.new_node(NN.GKNodePrint, "孤立链头")
        second = self.tree.new_node(NN.GKNodePrint, "孤立链尾")
        self.connect(first, "完成", second)
        self.assertEqual(self.names(), ["孤立链头", "孤立链尾"])
        self.assertIn("所在的执行链", audit_execution_flow(self.tree)[1].message)
        self.connect(self.ready, "执行", first)
        self.assertEqual(self.names(), [])

    def test_disconnected_cycle_terminates_and_is_reported(self):
        first = self.tree.new_node(NN.GKNodePrint, "循环一")
        second = self.tree.new_node(NN.GKNodePrint, "循环二")
        self.connect(first, "完成", second)
        self.connect(second, "完成", first)
        self.assertEqual(self.names(), ["循环一", "循环二"])

    def test_long_reroute_chain_and_branch_use_iterative_walk(self):
        current, output = self.ready, "执行"
        for _ in range(1600):
            wire = self.tree.new_node(Reroute)
            self.connect(current, output, wire, "Input")
            current, output = wire, "Output"
        for name in ("左支", "右支"):
            self.connect(current, output, self.tree.new_node(NN.GKNodePrint, name))
        self.assertEqual(self.names(), [])

    def test_disconnected_reroute_cycle_cannot_hang(self):
        one = self.tree.new_node(Reroute)
        two = self.tree.new_node(Reroute)
        self.connect(one, "Output", two, "Input")
        self.connect(two, "Output", one, "Input")
        target = self.tree.new_node(NN.GKNodePrint, "未触发")
        self.connect(two, "Output", target)
        self.assertEqual(self.names(), ["未触发"])

    def test_callback_is_reachable_only_after_widget_creation(self):
        button = self.tree.new_node(NN.GKNodeUiButton, "按钮")
        clicked = self.tree.new_node(NN.GKNodePrint, "点击反馈")
        self.connect(button, "点击", clicked)
        self.assertEqual(self.names(), ["按钮", "点击反馈"])
        self.connect(self.ready, "执行", button)
        self.assertEqual(self.names(), [])

    def test_function_entry_does_not_require_an_event_connection(self):
        declaration = self.tree.gk_funcs.add()
        declaration.func_name = "反馈"
        for cls in (NN.GKNodeFunction, NN.GKNodeFunctionDefine):
            function = self.tree.new_node(cls, gk_func_name="反馈")
            body = self.tree.new_node(NN.GKNodePrint)
            self.connect(function, "执行", body)
        self.assertEqual(self.names(), [])

    def test_undeclared_function_does_not_make_its_body_reachable(self):
        entry = self.tree.new_node(NN.GKNodeFunctionDefine, gk_func_name="未声明")
        body = self.tree.new_node(NN.GKNodePrint, "被跳过的函数体")
        self.connect(entry, "执行", body)
        self.assertEqual(self.names(), ["被跳过的函数体"])

    def test_duplicate_function_uses_the_same_position_order_as_compiler(self):
        declaration = self.tree.gk_funcs.add()
        declaration.func_name = "反馈"
        first = self.tree.new_node(NN.GKNodeFunctionDefine,
                                   location=(0, 100), gk_func_name="反馈")
        second = self.tree.new_node(NN.GKNodeFunctionDefine,
                                    location=(0, 0), gk_func_name="反馈")
        self.connect(first, "执行", self.tree.new_node(NN.GKNodePrint, "重复定义的函数体"))
        self.connect(second, "执行", self.tree.new_node(NN.GKNodePrint, "实际编译的函数体"))
        self.assertEqual(self.names(), ["重复定义的函数体"])

    def test_raw_link_flags_match_the_codegen_traversal_policy(self):
        from BlenderGameKit.codegen.graph import downstream
        target = self.tree.new_node(NN.GKNodePrint)
        link = self.connect(self.ready, "执行", target)
        link.is_valid = False
        link.is_muted = True
        self.assertEqual(downstream(self.ready.outputs[0]), [(target, "执行")])
        self.assertEqual(self.names(), [])

    def test_trigger_and_network_events_are_entry_points(self):
        for cls in (NN.GKNodeOnTimer, NN.GKNodeOnAreaEntered, NN.GKNodeNetPeerJoined):
            trigger = self.tree.new_node(cls)
            self.connect(trigger, "执行", self.tree.new_node(NN.GKNodePrint))
        self.assertEqual(self.names(), [])

    def test_data_outputs_do_not_execute_raycast_statements(self):
        raycast = self.tree.new_node(NN.GKNodeRaycast, "需要执行的射线")
        target = self.tree.new_node(NN.GKNodePrint)
        self.connect(self.ready, "执行", target)
        self.connect(raycast, "打到的东西", target, "内容")
        self.assertEqual(self.names(), ["需要执行的射线"])

    def test_comments_and_unused_pure_values_are_not_dead_statements(self):
        self.tree.new_node(NN.GKNodeComment)
        self.tree.new_node(NN.GKNodeConstText)
        self.tree.new_node(NN.GKNodeRaycastCheck)
        self.assertEqual(self.names(), [])

    def test_same_name_does_not_merge_node_identity(self):
        connected = self.tree.new_node(NN.GKNodePrint, "重复名")
        self.tree.new_node(NN.GKNodePrint, "重复名")
        self.connect(self.ready, "执行", connected)
        self.assertEqual(self.names(), ["重复名"])

    def test_design_trees_and_disabled_graphs_have_different_entry_semantics(self):
        self.tree.new_node(NN.GKNodeUiSet)
        for kind in ("GKUiDesignTree", "GKAnimationTree", "GKGameAiTree"):
            self.tree.bl_idname = kind
            self.assertEqual(self.names(), [])
        self.tree.bl_idname = "GKGameLogicTree"
        self.tree.gk_enabled = False
        self.assertEqual(self.names(), [])

    def test_audit_does_not_rescan_socket_links_or_mutate_graph(self):
        target = self.tree.new_node(NN.GKNodePrint)
        self.connect(self.ready, "执行", target)
        snapshot = (list(self.tree.nodes), list(self.tree.links))

        class UnreadableLinks:
            def __iter__(self):
                raise AssertionError("Audit rescanned socket.links")

        for node in self.tree.nodes:
            for socket in node.outputs:
                socket.links = UnreadableLinks()
        self.assertEqual(self.names(), [])
        self.assertEqual(snapshot, (self.tree.nodes, self.tree.links))


if __name__ == "__main__":
    unittest.main()
