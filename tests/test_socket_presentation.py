"""Display-only type inference follows actual sources and stable wiring."""

import types
import unittest
from unittest.mock import Mock, patch

import test_codegen as support
from BlenderGameKit import gk_sockets as S
from BlenderGameKit.nodes.node_base import StubSocketCollection


class ColorReroute:
    # Real Blender uses these default socket types even on bool/float wires.
    bl_idname = "NodeReroute"
    bl_label = "转接点"

    def __init__(self):
        self.inputs = StubSocketCollection(self)
        self.outputs = StubSocketCollection(self, is_output=True)
        self.inputs.new("NodeSocketColor", "Input")
        self.outputs.new("NodeSocketColor", "Output")


class TestSocketPresentation(unittest.TestCase):
    def setUp(self):
        self.tree = support.make_tree()

    def add(self, cls, **props):
        return self.tree.new_node(cls, **props)

    def connect(self, source, output, target, input_name):
        return support.gk_stub.connect(self.tree, source, output, target, input_name)

    def declare(self, name, kind):
        var = self.tree.gk_vars.add()
        var.var_name, var.var_type = name, kind
        return var

    def test_reroute_is_transparent_to_upstream_bool(self):
        source = self.add(support.NN.GKNodeConstBool)
        reroute = self.add(ColorReroute)
        setter = self.add(support.NN.GKNodeSetVar)
        self.connect(source, "值", reroute, "Input")
        self.connect(reroute, "Output", setter, "值")
        for socket in (setter.inputs.get("值"), reroute.inputs[0], reroute.outputs[0]):
            self.assertEqual(S.resolved_type_label(socket), "布尔")
        self.assertEqual(setter.inputs.get("值").bl_idname, "GKSocketAny")
        self.assertEqual(S.resolved_color(setter.inputs.get("值")),
                         S._COLOR_BY_IDNAME["NodeSocketBool"])

    def test_reroute_is_transparent_to_downstream_float(self):
        getter = self.add(support.NN.GKNodeGetVar)
        reroute = self.add(ColorReroute)
        vector = self.add(support.NN.GKNodeVectorMake)
        self.connect(getter, "值", reroute, "Input")
        self.connect(reroute, "Output", vector, "X")
        self.assertEqual(S.resolved_type_label(getter.outputs.get("值")), "数值")

    def test_logic_variable_declaration_is_authoritative_for_output(self):
        self.declare("flag", "bool")
        getter = self.add(support.NN.GKNodeGetVar, gk_var_name="flag")
        socket = getter.outputs.get("值")
        self.assertEqual(S.resolved_type_label(socket), "布尔")
        vector = self.add(support.NN.GKNodeVectorMake)
        self.connect(getter, "值", vector, "X")
        self.assertEqual(S.resolved_type_label(socket), "布尔")

    def test_linked_input_uses_source_instead_of_its_declaration_or_default(self):
        self.declare("flag", "bool")
        setter = self.add(support.NN.GKNodeSetVar, gk_var_name="flag")
        source = self.add(support.NN.GKNodeConstText)
        self.connect(source, "值", setter, "值")
        self.assertEqual(S.resolved_type_label(setter.inputs.get("值")), "文本")

    def test_animation_variable_declaration_is_preserved(self):
        node = self.add(support.NN.GKNodeAnimGetVar, gk_var_name="moving")
        node.id_data = types.SimpleNamespace(gk_variables=[
            types.SimpleNamespace(var_name="moving", var_type="bool")])
        self.assertEqual(S.resolved_type_label(node.outputs.get("值")), "布尔")

    def test_multiple_variable_slots_resolve_their_own_declarations(self):
        self.declare("flag", "bool")
        self.declare("caption", "string")
        setter = self.add(support.NN.GKNodeSetVar, gk_var_count=3,
                          gk_var_name="flag", gk_var_name_2="caption",
                          gk_var_name_3="amount")
        self.assertEqual(S.resolved_type_label(setter.inputs.get("值")), "布尔")
        self.assertEqual(S.resolved_type_label(setter.inputs.get("值 2")), "文本")
        self.assertEqual(S.resolved_type_label(setter.inputs.get("值 3")), "数值")

    def test_fanout_ignores_unknown_consumers_without_order_dependence(self):
        getter = self.add(support.NN.GKNodeGetVar)
        setter = self.add(support.NN.GKNodeSetVar)
        vector = self.add(support.NN.GKNodeVectorMake)
        self.connect(getter, "值", setter, "值")
        self.connect(getter, "值", vector, "X")
        socket = getter.outputs.get("值")
        self.assertEqual(S.resolved_type_label(socket), "数值")
        socket.links.reverse()
        self.assertEqual(S.resolved_type_label(socket), "数值")

    def test_fanout_conflicting_consumers_remains_any_in_both_orders(self):
        getter = self.add(support.NN.GKNodeGetVar)
        printer = self.add(support.NN.GKNodePrint)
        vector = self.add(support.NN.GKNodeVectorMake)
        self.connect(getter, "值", printer, "内容")
        self.connect(getter, "值", vector, "X")
        socket = getter.outputs.get("值")
        for _order in range(2):
            self.assertEqual(S.resolved_type_label(socket), "")
            self.assertEqual(S.resolved_color(socket), S._COLOR_ANY)
            socket.links.reverse()

    def test_all_rerouted_fanout_branches_are_considered(self):
        getter = self.add(support.NN.GKNodeGetVar)
        for target_type, pin in ((support.NN.GKNodeVectorMake, "X"),
                                 (support.NN.GKNodePrint, "内容")):
            reroute = self.add(ColorReroute)
            target = self.add(target_type)
            self.connect(getter, "值", reroute, "Input")
            self.connect(reroute, "Output", target, pin)
        self.assertEqual(S.resolved_type_label(getter.outputs.get("值")), "")

    def test_numeric_default_is_known_but_literal_string_is_not_guessed(self):
        setter = self.add(support.NN.GKNodeSetVar)
        compare = self.add(support.NN.GKNodeCompare, gk_a="false")
        self.assertEqual(S.resolved_type_label(setter.inputs.get("值")), "数值")
        self.assertEqual(S.resolved_type_label(compare.inputs.get("A")), "")

    def test_reroute_cycle_terminates_without_inventing_a_color_type(self):
        first, second = self.add(ColorReroute), self.add(ColorReroute)
        self.connect(first, "Output", second, "Input")
        self.connect(second, "Output", first, "Input")
        self.assertEqual(S.resolved_type_label(first.outputs[0]), "")

    def test_budget_does_not_choose_a_type_from_partial_fanout(self):
        getter = self.add(support.NN.GKNodeGetVar)
        for _index in range(8):
            vector = self.add(support.NN.GKNodeVectorMake)
            self.connect(getter, "值", vector, "X")
        self.assertEqual(S._walk_type_idname(getter.outputs.get("值"), limit=4), "")

    def test_changed_declaration_updates_without_stale_cache(self):
        variable = self.declare("value", "bool")
        getter = self.add(support.NN.GKNodeGetVar, gk_var_name="value")
        socket = getter.outputs.get("值")
        self.assertEqual(S.resolved_type_label(socket), "布尔")
        variable.var_type = "string"
        self.assertEqual(S.resolved_type_label(socket), "文本")


class TestSocketNativeColors(unittest.TestCase):
    def setUp(self):
        self.saved_palette = dict(S._NATIVE_COLORS)
        S._NATIVE_COLORS.clear()

    def tearDown(self):
        S._NATIVE_COLORS.clear()
        S._NATIVE_COLORS.update(self.saved_palette)

    def test_offline_colors_match_native_blender_palette(self):
        expected = {
            "NodeSocketFloat": (0.63, 0.63, 0.63, 1.0),
            "NodeSocketInt": (0.35, 0.55, 0.36, 1.0),
            "NodeSocketBool": (0.80, 0.65, 0.84, 1.0),
            "NodeSocketString": (0.44, 0.70, 1.0, 1.0),
            "NodeSocketVector": (0.39, 0.39, 0.78, 1.0),
            "NodeSocketVector2D": (0.39, 0.39, 0.78, 1.0),
            "NodeSocketColor": (0.78, 0.78, 0.16, 1.0),
            "NodeSocketObject": (0.93, 0.62, 0.36, 1.0),
        }
        with patch.object(S, "bpy", types.SimpleNamespace()):
            S.on_register()
        for idname, color in expected.items():
            with self.subTest(idname=idname):
                self.assertEqual(S.resolved_color(types.SimpleNamespace(bl_idname=idname)), color)

    def test_native_palette_is_read_once_and_removed_before_drawing(self):
        # A different running Blender palette must win over the static fallback.
        bool_color = (0.9, 0.5, 0.8, 1.0)
        vector_color = (0.4, 0.5, 0.7, 1.0)
        boolean = types.SimpleNamespace(bl_idname="NodeSocketBool",
                                        draw_color_simple=Mock(return_value=bool_color))
        vector = types.SimpleNamespace(bl_idname="NodeSocketVector",
                                       draw_color=Mock(return_value=vector_color))
        node = types.SimpleNamespace(outputs=[boolean, vector])
        tree = types.SimpleNamespace(interface=types.SimpleNamespace(new_socket=Mock()),
                                     nodes=types.SimpleNamespace(new=Mock(return_value=node)))
        groups = types.SimpleNamespace(new=Mock(return_value=tree), remove=Mock())
        fake_bpy = types.SimpleNamespace(data=types.SimpleNamespace(node_groups=groups),
                                         context=object())
        with patch.object(S, "bpy", fake_bpy):
            S.on_register()
            groups.remove.assert_called_once_with(tree)
            for _draw in range(3):
                self.assertEqual(S.resolved_color(boolean), bool_color)
                self.assertEqual(S.resolved_color(vector), vector_color)
                vector2 = types.SimpleNamespace(bl_idname="NodeSocketVector2D")
                self.assertEqual(S.resolved_color(vector2), vector_color)
            groups.new.assert_called_once()
            boolean.draw_color_simple.assert_called_once_with()
            vector.draw_color.assert_called_once_with(fake_bpy.context, node)
            S.on_unregister()
            self.assertFalse(S._NATIVE_COLORS)

    def test_failed_palette_probe_does_not_leave_a_temporary_tree(self):
        tree = types.SimpleNamespace(interface=types.SimpleNamespace(new_socket=Mock()),
                                     nodes=types.SimpleNamespace(new=Mock(side_effect=RuntimeError)))
        groups = types.SimpleNamespace(new=Mock(return_value=tree), remove=Mock())
        with patch.object(S, "bpy", types.SimpleNamespace(data=types.SimpleNamespace(node_groups=groups))):
            S.on_register()
        groups.remove.assert_called_once_with(tree)
        self.assertEqual(S.resolved_color(types.SimpleNamespace(bl_idname="NodeSocketBool")),
                         (0.80, 0.65, 0.84, 1.0))


if __name__ == "__main__":
    unittest.main()
