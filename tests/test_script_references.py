"""Custom scripts follow Text references, independently of text-list ordering."""

import os
import sys
import types
import unittest
from unittest import mock

import test_codegen as support
from BlenderGameKit import script_assets as sa


class TextBlock:
    def __init__(self, name, body="extends Node\n"):
        self.name = name
        self.body = body

    def as_string(self):
        return self.body


class TestScriptReferences(unittest.TestCase):
    def setUp(self):
        self.tree = support.make_tree()
        self.event = self.tree.new_node(support.NN.GKNodeOnReady, "ready")
        self.directory = os.path.join(support.ROOT, ".audit", "script_reference_export")

    def chain(self, text=None, instance=""):
        previous, output = self.event, "执行"
        nodes = []
        for node_type, props in (
                (support.NN.GKNodeScriptLoad, {"gk_start_method": "start"}),
                (support.NN.GKNodeScriptCall, {"gk_method": "burst"}),
                (support.NN.GKNodeScriptStop, {"gk_stop_method": "stop"})):
            node = self.tree.new_node(node_type, gk_script_text=text,
                                      gk_instance=instance, **props)
            support.gk_stub.connect(self.tree, previous, output, node, "执行")
            previous, output = node, "完成"
            nodes.append(node)
        return nodes

    def files(self, texts=(), groups=None):
        data = types.SimpleNamespace(node_groups=groups or [self.tree], texts=texts)
        with mock.patch.object(sys.modules["bpy"], "data", data, create=True):
            return sa.script_files(self.directory)

    def expected_file(self, text):
        return (sa.instance_key(text.name),
                os.path.join(self.directory, sa.SCRIPT_DIR, sa.file_name(text.name)),
                text.body)

    def test_new_nodes_have_no_implicit_script_selection(self):
        for node_type in (support.NN.GKNodeScriptLoad, support.NN.GKNodeScriptStop,
                          support.NN.GKNodeScriptCall):
            with self.subTest(node=node_type.bl_idname):
                node = self.tree.new_node(node_type)
                self.assertIsNone(node.gk_script_text)

    def test_asset_export_reads_selected_text_without_scanning_text_names(self):
        selected = TextBlock("B_选中", 'extends Node\nconst VALUE = "selected"\n')
        self.chain(selected)

        class UnreadableTexts:
            def __iter__(self):
                raise AssertionError("Export scanned bpy.data.texts instead of the reference")

        self.assertEqual(self.files(UnreadableTexts()), [self.expected_file(selected)])

    def test_text_order_and_unselected_rename_cannot_change_asset(self):
        selected = TextBlock("B_selected", 'extends Node\nconst VALUE = "B"\n')
        other = TextBlock("C_other", 'extends Node\nconst VALUE = "C"\n')
        self.chain(selected)
        baseline = self.files([selected, other])
        inserted = TextBlock("A_inserted")
        self.assertEqual(self.files([inserted, selected, other]), baseline)
        other.name = "A_renamed"
        self.assertEqual(self.files([other, inserted, selected]), baseline)
        self.assertEqual(baseline, [self.expected_file(selected)])

    def test_selected_rename_updates_paths_and_all_runtime_keys_together(self):
        selected = TextBlock("B_original", 'extends Node\nconst VALUE = "kept"\n')
        nodes = self.chain(selected)
        old_path = sa.res_path(selected.name)
        selected.name = "重命名后的脚本"
        code, warnings = support.gen(self.tree, support.V3)
        key = sa.instance_key(selected.name)
        self.assertEqual(warnings, [])
        self.assertNotIn(old_path, code)
        self.assertIn(f'_gk_script_load(self, "{sa.res_path(selected.name)}", "{key}", "start")', code)
        self.assertIn(f'_gk_script_call("{key}", "burst")', code)
        self.assertIn(f'_gk_script_stop("{key}", "stop")', code)
        self.assertEqual(self.files(), [self.expected_file(selected)])
        self.assertTrue(all(node.gk_script_text is selected for node in nodes))

    def test_shared_reference_exports_once_and_unreferenced_text_is_ignored(self):
        selected = TextBlock("shared")
        unused = TextBlock("unused")
        self.chain(selected)
        other_tree = support.make_tree()
        other_tree.new_node(support.NN.GKNodeScriptLoad, gk_script_text=selected)
        self.assertEqual(self.files([selected, unused], [self.tree, other_tree]),
                         [self.expected_file(selected)])

    def test_different_unicode_names_keep_distinct_files(self):
        first, second = TextBlock("脚本甲", "body A"), TextBlock("脚本乙", "body B")
        self.tree.new_node(support.NN.GKNodeScriptLoad, gk_script_text=first)
        self.tree.new_node(support.NN.GKNodeScriptLoad, gk_script_text=second)
        files = self.files([second, first])
        self.assertCountEqual(files, [self.expected_file(first), self.expected_file(second)])
        self.assertEqual(len({entry[1] for entry in files}), 2)

    def test_explicit_instance_overrides_runtime_key_but_not_asset_path(self):
        selected = TextBlock("source_script")
        self.chain(selected, "独立实例")
        code, warnings = support.gen(self.tree, support.V3)
        key = sa.instance_key("独立实例")
        self.assertEqual(warnings, [])
        self.assertIn(f'_gk_script_load(self, "{sa.res_path(selected.name)}", "{key}", "start")', code)
        self.assertIn(f'_gk_script_call("{key}", "burst")', code)
        self.assertIn(f'_gk_script_stop("{key}", "stop")', code)
        self.assertEqual(self.files(), [self.expected_file(selected)])

    def test_stop_and_call_can_address_explicit_instance_without_text(self):
        key = sa.instance_key("正在运行的实例")
        call = self.tree.new_node(support.NN.GKNodeScriptCall,
                                  gk_instance="正在运行的实例", gk_method="burst")
        stop = self.tree.new_node(support.NN.GKNodeScriptStop,
                                  gk_instance="正在运行的实例", gk_stop_method="stop")
        support.gk_stub.connect(self.tree, self.event, "执行", call, "执行")
        support.gk_stub.connect(self.tree, call, "完成", stop, "执行")
        code, warnings = support.gen(self.tree, support.V3)
        self.assertEqual(warnings, [])
        self.assertIn(f'_gk_script_call("{key}", "burst")', code)
        self.assertIn(f'_gk_script_stop("{key}", "stop")', code)
        self.assertEqual(self.files(), [])

    def test_missing_load_reference_does_not_guess_from_instance(self):
        load = self.tree.new_node(support.NN.GKNodeScriptLoad, gk_instance="existing")
        support.gk_stub.connect(self.tree, self.event, "执行", load, "执行")
        code, warnings = support.gen(self.tree, support.V3)
        self.assertTrue(any("没选脚本" in item for item in warnings), warnings)
        self.assertNotIn("_gk_script_load(self,", code)
        self.assertEqual(self.files([TextBlock("existing")]), [])

    def test_legacy_enum_key_does_not_select_a_script(self):
        text = TextBlock("旧脚本")
        load = self.tree.new_node(support.NN.GKNodeScriptLoad,
                                  gk_script_name=sa.instance_key(text.name))
        support.gk_stub.connect(self.tree, self.event, "执行", load, "执行")
        code, warnings = support.gen(self.tree, support.V3)
        self.assertTrue(any("没选脚本" in item for item in warnings), warnings)
        self.assertNotIn("_gk_script_load(self,", code)
        self.assertEqual(self.files([text]), [])


if __name__ == "__main__":
    unittest.main()
