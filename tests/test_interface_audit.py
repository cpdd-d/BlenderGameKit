"""Static interface checks only use contracts that blueprints actually generate."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gk_stub


class TestInterfaceAudit(unittest.TestCase):
    def setUp(self):
        if "bpy" not in sys.modules:
            gk_stub.install()
        if "BlenderGameKit" not in sys.modules:
            gk_stub.load_addon(os.path.join(os.path.dirname(os.path.dirname(__file__)), "BlenderGameKit"))
        from BlenderGameKit.nodes import nodes
        from BlenderGameKit.codegen import interface_audit
        from BlenderGameKit import codegen
        self.NN, self.audit, self.codegen = nodes, interface_audit, codegen
        self.tree = gk_stub.StubNodeTree("调用方")
        self.target = gk_stub.StubNodeTree("共享门")

    def define(self, tree, name="receive", params="", body=True):
        declaration = tree.gk_funcs.add()
        declaration.func_name, declaration.params = name, params
        if body:
            tree.new_node(self.NN.GKNodeFunctionDefine, gk_func_name=name)

    def call(self, target="__self__", name="receive"):
        return self.tree.new_node(self.NN.GKNodeCallFunction, "调用门", gk_target=target, gk_func_name=name)

    def check(self, **kwargs):
        return self.audit.audit_interfaces(self.tree, target_trees={"DoorA": self.target}, **kwargs)

    def test_self_call_with_required_arguments_is_a_located_error(self):
        self.define(self.tree, params="amount:float, tag:string")
        node = self.call()
        diagnostics = self.check()
        self.assertEqual([d.code for d in diagnostics], ["INTERFACE_CALL_ARGUMENT_MISMATCH"])
        self.assertEqual((diagnostics[0].level, diagnostics[0].tree_name, diagnostics[0].node_name),
                         ("error", "调用方", node.name))
        self.assertIn("2 个参数", diagnostics[0].message)

    def test_exact_object_and_role_reference_share_the_same_signature_check(self):
        self.define(self.target, params="amount:int")
        self.call("DoorA")
        self.call("@机关")
        found = self.check(options={"reference_bindings": {"机关": "DoorA"}})
        self.assertEqual([d.code for d in found], ["INTERFACE_CALL_ARGUMENT_MISMATCH"] * 2)

    def test_zero_parameter_legacy_and_modern_functions_are_valid(self):
        self.define(self.target)
        self.target.new_node(self.NN.GKNodeFunction, gk_func_name="legacy")
        self.call("DoorA")
        self.call("DoorA", "legacy")
        self.assertEqual(self.check(), [])

    def test_missing_definition_is_warning_only_for_an_explicit_declaration(self):
        self.define(self.target, body=False)
        self.call("DoorA")
        self.call("DoorA", "unknown_extension_method")
        found = self.check()
        self.assertEqual([(d.code, d.level) for d in found], [("INTERFACE_FUNCTION_NOT_DEFINED", "warning")])

    def test_native_methods_external_paths_and_unknown_targets_are_not_guessed(self):
        self.define(self.target, params="amount:int")
        self.call("DoorA", "queue_free")
        self.call("DoorA/RuntimeChild")
        self.call("/root/ExternalService")
        self.call("SpawnedAtRuntime")
        self.call("@unbound")
        self.assertEqual(self.check(), [])

    def test_chinese_declared_call_matches_definition_and_accepts_exported_name(self):
        from BlenderGameKit.nodes.node_base import safe_function_name
        self.define(self.target, name="开门")
        node = self.call("DoorA", "开门")
        ready = self.tree.new_node(self.NN.GKNodeOnReady)
        gk_stub.connect(self.tree, ready, "执行", node, "执行")
        expected = safe_function_name("开门")
        for spelling in ("开门", expected):
            node.gk_func_name = spelling
            with mock.patch.object(self.audit, "_scene_role_trees", return_value={"DoorA": self.target}):
                result = self.codegen.generate_result(self.tree)
            self.assertIn(f".{expected}()", result.code)
            self.assertEqual(self.check(), [])
        self.assertIn(f"func {expected}()", self.codegen.generate_result(self.target).code)

    def connect(self, signal="powered", handler="on_powered"):
        return self.tree.new_node(self.NN.GKNodeConnect, "连接反馈", gk_target="DoorA",
                                  gk_signal_name=signal, gk_handler=handler)

    def test_custom_signal_cannot_supply_required_callback_arguments(self):
        self.target.new_node(self.NN.GKNodeEmitSignal, gk_signal_name="powered")
        self.define(self.tree, name="on_powered", params="amount:int")
        self.connect()
        found = self.check()
        self.assertEqual([d.code for d in found], ["INTERFACE_SIGNAL_ARGUMENT_MISMATCH"])
        self.assertEqual(found[0].node_name, "连接反馈")

    def test_custom_signal_to_zero_argument_callback_is_valid(self):
        self.target.new_node(self.NN.GKNodeEmitSignal, gk_signal_name="powered")
        self.define(self.tree, name="on_powered")
        self.connect()
        self.assertEqual(self.check(), [])

    def test_native_or_externally_declared_signal_argument_count_is_unknown(self):
        self.target.new_node(self.NN.GKNodeEmitSignal, gk_signal_name="powered", gk_signal_declare=False)
        self.define(self.tree, name="on_powered", params="body:node")
        self.connect()
        self.connect(signal="body_entered")
        self.assertEqual(self.check(), [])

    def test_signal_handler_name_uses_the_real_generated_function_name(self):
        from BlenderGameKit.nodes.node_base import safe_function_name
        self.define(self.tree, name="响应")
        node = self.connect(handler="响应")
        ready = self.tree.new_node(self.NN.GKNodeOnReady)
        gk_stub.connect(self.tree, ready, "执行", node, "执行")
        result = self.codegen.generate_result(self.tree)
        expected = safe_function_name("响应")
        self.assertIn(f".connect({expected})", result.code)
        self.assertIn(f"func {expected}()", result.code)
        self.assertEqual(self.check(), [])

    def test_chinese_role_reference_still_reports_required_argument_mismatch(self):
        self.define(self.target, name="开门", params="key:int")
        self.call("@机关", "开门")
        found = self.check(options={"reference_bindings": {"机关": "DoorA"}})
        self.assertEqual([d.code for d in found], ["INTERFACE_CALL_ARGUMENT_MISMATCH"])

    def test_legacy_chinese_function_calls_and_callbacks_keep_their_names(self):
        from BlenderGameKit.nodes.node_base import snake_case
        self.tree.new_node(self.NN.GKNodeFunction, gk_func_name="旧响应")
        self.target.new_node(self.NN.GKNodeFunction, gk_func_name="旧开门")
        ready = self.tree.new_node(self.NN.GKNodeOnReady)
        callback = self.connect(handler="旧响应")
        node = self.call("DoorA", "旧开门")
        gk_stub.connect(self.tree, ready, "执行", callback, "执行")
        gk_stub.connect(self.tree, callback, "完成", node, "执行")
        with mock.patch.object(self.audit, "_scene_role_trees", return_value={"DoorA": self.target}):
            result = self.codegen.generate_result(self.tree)
        self.assertIn(f".connect({snake_case('旧响应')})", result.code)
        self.assertIn(f".{snake_case('旧开门')}()", result.code)
        self.assertIn(f"func {snake_case('旧响应')}()", result.code)
        self.assertEqual(self.check(), [])

    def test_dynamic_chinese_method_name_is_not_assumed_to_be_a_blueprint(self):
        self.assertEqual(self.audit.emitted_function_name(None, "外部方法"), "外部方法")

    def test_dormant_statements_are_not_reported_by_codegen_api(self):
        self.define(self.tree, params="amount:int")
        self.call()
        result = self.codegen.generate_result(self.tree)
        self.assertFalse([d for d in result.diagnostics if d.code.startswith("INTERFACE_")])

    def test_codegen_api_reports_live_cross_object_call_without_rewriting_it(self):
        self.define(self.target, params="amount:int")
        ready = self.tree.new_node(self.NN.GKNodeOnReady)
        node = self.call("@机关")
        gk_stub.connect(self.tree, ready, "执行", node, "执行")
        with mock.patch.object(self.audit, "_scene_role_trees", return_value={"DoorA": self.target}):
            result = self.codegen.generate_result(self.tree, {"reference_bindings": {"机关": "DoorA"}})
        found = [d for d in result.diagnostics if d.code.startswith("INTERFACE_")]
        self.assertEqual([d.code for d in found], ["INTERFACE_CALL_ARGUMENT_MISMATCH"])
        self.assertIn(".receive()", result.code)


if __name__ == "__main__":
    unittest.main()
