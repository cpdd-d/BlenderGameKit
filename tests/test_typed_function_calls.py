"""Typed cross-object calls share declarations, sockets and one cached result."""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import gk_stub


class Groups(dict):
    def __iter__(self):
        return iter(self.values())


class TestTypedFunctionCalls(unittest.TestCase):
    def setUp(self):
        if "bpy" not in sys.modules:
            gk_stub.install()
        if "BlenderGameKit" not in sys.modules:
            gk_stub.load_addon(os.path.join(os.path.dirname(os.path.dirname(__file__)), "BlenderGameKit"))
        from BlenderGameKit import codegen, properties
        from BlenderGameKit.codegen import interface_audit
        from BlenderGameKit.nodes import nodes
        self.codegen, self.props, self.audit, self.N = codegen, properties, interface_audit, nodes
        self.source, self.caller = gk_stub.StubNodeTree("DoorInterface"), gk_stub.StubNodeTree("Caller")
        self.groups = Groups({t.name: t for t in (self.source, self.caller)})
        patch = mock.patch.object(sys.modules["bpy"], "data", types.SimpleNamespace(node_groups=self.groups), create=True)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch.object(self.audit, "_scene_role_trees", return_value={"Door": self.source})
        patch.start()
        self.addCleanup(patch.stop)
        self.decl = self.source.gk_funcs.add()
        self.decl.func_name, self.decl.params, self.decl.return_type = "activate", "radius:float", "bool"
        self.define = self.source.new_node(nodes.GKNodeFunctionDefine, gk_func_name="activate")

    def call(self, **properties):
        values = dict(gk_function_tree=self.source.name, gk_target="@door", gk_func_name="activate")
        values.update(properties)
        ready = self.caller.new_node(self.N.GKNodeOnReady)
        node = self.caller.new_node(self.N.GKNodeFunctionCall, **values)
        gk_stub.connect(self.caller, ready, "执行", node, "执行")
        if node.inputs.get("radius"):
            node.inputs.get("radius").default_value = 2.2
        return node

    def generate(self):
        return self.codegen.generate_result(self.caller, {"base_class": "Node3D", "reference_bindings": {"door": "Door"}})

    def test_cross_call_uses_typed_interface_and_caches_exactly_once(self):
        node = self.call()
        result = self.generate()
        self.assertEqual(node.outputs.get("结果").bl_idname, "NodeSocketBool")
        self.assertEqual(node.outputs.get("成功").bl_idname, "NodeSocketBool")
        self.assertEqual(result.code.count(".activate(2.2)"), 1)
        self.assertIn("is_instance_valid(", result.code)
        self.assertIn('.has_method("activate")', result.code)
        self.assertRegex(result.code, r"var _fres\d+: bool = false")
        self.assertFalse([d for d in result.diagnostics if d.level == "error"])

    def test_self_default_is_compatible_with_old_call_nodes(self):
        self.caller = self.source
        self.decl.params, self.decl.return_type = "", "void"
        self.call(gk_function_tree="", gk_target="__self__")
        result = self.generate()
        self.assertIn("    activate()", result.code)
        self.assertNotIn('.has_method("activate")', result.code)

    def test_unknown_runtime_target_uses_explicit_contract_and_guard(self):
        self.call(gk_target="/root/RuntimeDoor")
        result = self.generate()
        self.assertFalse([d for d in result.diagnostics if d.code.startswith("INTERFACE_")])
        self.assertIn('.has_method("activate")', result.code)

    def test_deleted_interface_and_misspelled_function_are_errors(self):
        node = self.call(gk_function_tree="deleted")
        self.assertIn("INTERFACE_SOURCE_MISSING", [d.code for d in self.generate().diagnostics])
        node.gk_function_tree, node.gk_func_name = self.source.name, "typo"
        self.assertIn("INTERFACE_FUNCTION_MISSING", [d.code for d in self.generate().diagnostics])

    def test_cross_call_requires_explicit_source(self):
        self.call(gk_function_tree="")
        self.assertIn("INTERFACE_SOURCE_REQUIRED", [d.code for d in self.generate().diagnostics])

    def test_known_target_must_match_parameter_types_and_return_type(self):
        other = gk_stub.StubNodeTree("WrongTarget")
        declaration = other.gk_funcs.add()
        declaration.func_name, declaration.params, declaration.return_type = "activate", "radius:string", "int"
        other.new_node(self.N.GKNodeFunctionDefine, gk_func_name="activate")
        self.call()
        with mock.patch.object(self.audit, "_scene_role_trees", return_value={"Door": other}):
            result = self.generate()
        found = next(d for d in result.diagnostics if d.code == "INTERFACE_SIGNATURE_MISMATCH")
        self.assertEqual((found.level, found.tree_name), ("error", self.caller.name))
        self.assertNotIn(".activate(2.2)", result.code)

    def test_async_return_is_rejected_and_keeps_false_fallback(self):
        wait = self.source.new_node(self.N.GKNodeWait)
        gk_stub.connect(self.source, self.define, "执行", wait, "执行")
        self.call()
        result = self.generate()
        self.assertIn("INTERFACE_ASYNC_RETURN", [d.code for d in result.diagnostics])
        self.assertNotIn(".activate(2.2)", result.code)
        self.assertRegex(result.code, r"var _fres\d+: bool = false")

    def test_signature_change_updates_foreign_pins_and_preserves_values(self):
        node = self.call()
        self.decl.params = "radius:float, count:int"
        self.decl.return_type = "int"
        self.props._on_function_decl_changed(types.SimpleNamespace(id_data=self.source), None)
        self.assertEqual(node.inputs.get("radius").default_value, 2.2)
        self.assertEqual(node.inputs.get("count").bl_idname, "NodeSocketInt")
        self.assertEqual(node.outputs.get("结果").bl_idname, "NodeSocketInt")

    def test_animation_and_timeline_callback_waits_do_not_suspend_the_caller(self):
        for cls, output in ((self.N.GKNodePlayAnim, "结束时"), (self.N.GKNodeTimeline, "已结束")):
            with self.subTest(node=cls.bl_idname):
                tree = gk_stub.StubNodeTree("CallbackContract")
                declaration = tree.gk_funcs.add()
                declaration.func_name, declaration.return_type = "launch", "bool"
                definition = tree.new_node(self.N.GKNodeFunctionDefine, gk_func_name="launch")
                animation = tree.new_node(cls)
                wait = tree.new_node(self.N.GKNodeWait)
                gk_stub.connect(tree, definition, "执行", animation, "执行")
                gk_stub.connect(tree, animation, output, wait, "执行")
                self.assertFalse(self.audit.function_suspends(tree, "launch"))
                if animation.outputs.get("完成") is not None:
                    gk_stub.connect(tree, animation, "完成", wait, "执行")
                else:
                    gk_stub.connect(tree, definition, "执行", wait, "执行")
                self.assertTrue(self.audit.function_suspends(tree, "launch"))

    def test_vector_argument_defaults_follow_declared_dimension(self):
        self.decl.params = "direction:vector2"
        node = self.call()
        node.inputs.get("direction").default_value = (1.0, 2.0)
        result = self.generate()
        self.assertIn(".activate(Vector2(1.0, 2.0))", result.code)

    def test_foreign_calls_count_in_interface_sidebar_not_caller_sidebar(self):
        self.call()
        self.assertEqual(self.props.function_usages(self.source)["activate"]["calls"], 1)
        self.assertNotIn("activate", self.props.function_usages(self.caller))

    def test_rename_carries_foreign_calls_without_touching_unrelated_self_call(self):
        node = self.call()
        local = self.caller.new_node(self.N.GKNodeFunctionCall, gk_func_name="activate")
        self.props.rename_function_refs(self.source, "activate", "activate_new")
        self.assertEqual(node.gk_func_name, "activate_new")
        self.assertEqual(local.gk_func_name, "activate")


if __name__ == "__main__":
    unittest.main()
