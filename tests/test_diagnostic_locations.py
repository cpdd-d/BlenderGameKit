"""A report must identify a graph as well as a potentially repeated node name."""

import json
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gk_stub

gk_stub.install()
gk_stub.load_addon(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              "BlenderGameKit"))
from BlenderGameKit import codegen, export_report
from BlenderGameKit.codegen.api import _diagnostics_for_tree
from BlenderGameKit.models import Diagnostic
from BlenderGameKit.nodes import nodes as NN


def named_tree(name, node_name="刷新血条", kind="GKGameLogicTree"):
    node = SimpleNamespace(name=node_name)
    return SimpleNamespace(name=name, bl_idname=kind, nodes={node_name: node})


class TestDiagnosticLocations(unittest.TestCase):
    def setUp(self):
        self.saved_report = dict(export_report.LAST_REPORT)
        export_report.clear()

    def tearDown(self):
        export_report.clear()
        export_report.LAST_REPORT.update(self.saved_report)

    def test_old_positional_diagnostic_constructor_keeps_its_meaning(self):
        diagnostic = Diagnostic("warning", "TEST", "内容", "建议", "节点")
        self.assertEqual(diagnostic.node_name, "节点")
        self.assertEqual(diagnostic.tree_name, "")
        self.assertEqual(diagnostic.render(), "内容 —— 建议")

    def test_same_message_and_node_in_two_graphs_keeps_both_locations(self):
        one = Diagnostic(code="TEST", message="执行未接线", node_name="刷新血条", tree_name="玩家甲")
        two = Diagnostic(code="TEST", message="执行未接线", node_name="刷新血条", tree_name="玩家乙")
        report = export_report.build({"diagnostics": [one, two, one],
                                      "warnings": [one.render(), two.render()]})
        self.assertEqual([item["tree_name"] for item in report["items"]], ["玩家甲", "玩家乙"])
        self.assertEqual([item["node_name"] for item in report["items"]], ["刷新血条"] * 2)

    def test_record_reload_and_text_preserve_source_graph(self):
        diagnostic = Diagnostic(message="缺少执行", node_name="刷新血条", tree_name="角色乙")
        scene = {}
        report = export_report.record(scene, {"diagnostics": [diagnostic]})
        self.assertIn("相关节点图: 角色乙", export_report.report_text(report))
        export_report.clear()
        restored = export_report.load(scene)
        self.assertEqual(restored["items"][0]["tree_name"], "角色乙")
        json.loads(scene[export_report.PERSIST_KEY])

    def test_report_keeps_exact_blender_names_including_spaces(self):
        tree = named_tree(" 玩家乙 ", " 刷新血条 ")
        diagnostic = Diagnostic(message="缺少执行", node_name=" 刷新血条 ", tree_name=" 玩家乙 ")
        item = export_report.build({"diagnostics": [diagnostic]})["items"][0]
        resolved, node, error = export_report.resolve_node_target(
            [tree], item["node_name"], item["tree_name"])
        self.assertIs(resolved, tree)
        self.assertIs(node, tree.nodes[" 刷新血条 "])
        self.assertEqual(error, "")

    def test_legacy_saved_report_without_graph_field_still_loads(self):
        legacy = {"ok": True, "items": [{"node_name": "刷新血条", "message": "旧警告"}]}
        scene = {export_report.PERSIST_KEY: json.dumps(legacy)}
        loaded = export_report.load(scene)
        self.assertEqual(loaded, legacy)
        self.assertIn("相关节点: 刷新血条", export_report.report_text(loaded))

    def test_exact_graph_beats_the_first_same_name_node(self):
        one, two = named_tree("玩家甲"), named_tree("玩家乙")
        tree, node, error = export_report.resolve_node_target([one, two], "刷新血条", "玩家乙")
        self.assertIs(tree, two)
        self.assertIs(node, two.nodes["刷新血条"])
        self.assertEqual(error, "")

    def test_deleted_named_graph_never_falls_back_to_same_name_node(self):
        tree, node, error = export_report.resolve_node_target(
            [named_tree("玩家甲")], "刷新血条", "已删除的玩家乙")
        self.assertIsNone(tree)
        self.assertIsNone(node)
        self.assertIn("已删除的玩家乙", error)

    def test_deleted_node_in_named_graph_never_falls_back_to_other_graph(self):
        one, two = named_tree("玩家甲"), named_tree("玩家乙", "改名后的血条")
        tree, node, error = export_report.resolve_node_target([one, two], "刷新血条", "玩家乙")
        self.assertIsNone(tree)
        self.assertIsNone(node)
        self.assertIn("玩家乙", error)

    def test_legacy_location_only_resolves_a_unique_match(self):
        one = named_tree("玩家甲")
        tree, node, error = export_report.resolve_node_target([one], "刷新血条")
        self.assertIs(tree, one)
        self.assertIs(node, one.nodes["刷新血条"])
        self.assertEqual(error, "")
        two = named_tree("玩家乙")
        tree, node, error = export_report.resolve_node_target([one, two], "刷新血条")
        self.assertIsNone(tree)
        self.assertIsNone(node)
        self.assertIn("旧报告", error)
        self.assertIn("重新导出", error)

    def test_ui_graphs_can_be_located_and_are_not_confused_with_logic_graphs(self):
        logic = named_tree("角色逻辑")
        ui = named_tree("游戏界面", kind="GKUiDesignTree")
        self.assertIs(export_report.resolve_node_target([logic, ui], "刷新血条", "游戏界面")[0], ui)
        self.assertIn("旧报告", export_report.resolve_node_target([logic, ui], "刷新血条")[2])

    def test_legacy_lookup_does_not_use_a_shader_node_with_the_same_name(self):
        logic = named_tree("角色逻辑")
        shader = named_tree("材质", kind="ShaderNodeTree")
        self.assertIs(export_report.resolve_node_target([shader, logic], "刷新血条")[0], logic)

    def test_codegen_tags_both_graph_audit_and_existing_validation_diagnostics(self):
        tree = gk_stub.StubNodeTree("角色乙")
        ready = tree.new_node(NN.GKNodeOnReady)
        move = tree.new_node(NN.GKNodeMoveDirection)
        tree.new_node(NN.GKNodePrint, "断开节点")
        gk_stub.connect(tree, ready, "执行", move, "执行")
        result = codegen.generate_result(tree, {"base_class": "Node", "dimension": "3"})
        codes = {item.code for item in result.diagnostics}
        self.assertIn("UNREACHABLE_EXECUTION", codes)
        self.assertIn("BASE_CLASS_MISMATCH", codes)
        self.assertTrue(all(item.tree_name == "角色乙" for item in result.diagnostics))

    def test_codegen_preserves_explicit_source_and_does_not_mutate_diagnostic(self):
        origin = Diagnostic(message="子图问题", tree_name="子图")
        legacy = Diagnostic(message="宿主问题")
        result = _diagnostics_for_tree([origin, legacy], SimpleNamespace(name="宿主图"))
        self.assertEqual([item.tree_name for item in result], ["子图", "宿主图"])
        self.assertEqual(legacy.tree_name, "")


if __name__ == "__main__":
    unittest.main()
