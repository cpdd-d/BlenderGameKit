"""Validate node-card and socket presentation with real Blender data.

Run from the repository:
    blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender_node_presentation.py

All graphs are temporary in-memory data. No project or preference is saved.
"""

import os
import sys
from types import SimpleNamespace

import bpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import BlenderGameKit as addon
from BlenderGameKit import codegen, properties
from BlenderGameKit.nodes import nodes as node_lib
from BlenderGameKit.nodes.node_presentation import apply_socket_style


OPTIONS = {"base_class": "Node", "export_tunables": False}


class RecordingLayout:
    """Record properties together with their inherited enabled state."""

    def __init__(self, parent=None):
        self.parent = parent
        self.calls = parent.calls if parent else []
        self.enabled = True
        self.active = True
        self.use_property_split = False
        self.use_property_decorate = False

    def effective_enabled(self):
        return (bool(self.enabled) and bool(self.active)
                and (self.parent is None or self.parent.effective_enabled()))

    def _child(self, *args, **kwargs):
        return RecordingLayout(self)

    row = column = column_flow = grid_flow = split = box = _child

    def prop(self, owner, name, **kwargs):
        # Access the actual RNA property; an accidentally renamed UI property
        # must not silently pass because the layout is only a recording stub.
        if not name.startswith('['):
            getattr(owner, name)
        self.calls.append(("prop", owner, name, self.effective_enabled(), kwargs))

    def label(self, **kwargs):
        self.calls.append(("label", kwargs.get("text", ""), self.effective_enabled()))

    def operator(self, name, **kwargs):
        result = SimpleNamespace()
        self.calls.append(("operator", name, result, self.effective_enabled(), kwargs))
        return result

    def panel(self, *args, **kwargs):
        return self._child(), self._child()

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self._child()
        return record


class NodeContext:
    def __init__(self, tree, node):
        self.node = node
        self.active_node = node
        self.space_data = SimpleNamespace(tree_type=tree.bl_idname,
                                          edit_tree=tree, node_tree=tree)

    def __getattr__(self, name):
        return getattr(bpy.context, name)


def new_tree(name, kind="GKGameLogicTree"):
    return bpy.data.node_groups.new(name, kind)


def draw(node):
    node.id_data.nodes.active = node
    layout = RecordingLayout()
    node.draw_buttons(NodeContext(node.id_data, node), layout)
    return layout


def property_calls(layout, node, name):
    return [call for call in layout.calls
            if call[0] == "prop" and call[1] == node and call[2] == name]


def links_snapshot(tree):
    return [(link.from_node.name, link.from_socket.name,
             link.to_node.name, link.to_socket.name) for link in tree.links]


def assert_shapes(node):
    for socket in (*node.inputs, *node.outputs):
        expected = "DIAMOND" if socket.bl_idname == "GKSocketFlow" else "CIRCLE"
        assert socket.display_shape == expected, (node.bl_idname, socket.name,
                                                  socket.display_shape, expected)
    for socket in node.inputs:
        prop = getattr(node, "gk_input_props", {}).get(socket.name)
        if prop and hasattr(node, prop):
            assert socket.hide_value, (node.bl_idname, socket.name, prop)


def test_all_node_cards_create_and_draw():
    trees = {}
    count = 0
    for cls in node_lib.ALL_CLASSES:
        kind = properties.node_tree_kinds(cls)[0]
        if kind not in trees:
            trees[kind] = new_tree("presentation_" + kind, kind)
        tree = trees[kind]
        node = tree.nodes.new(cls.bl_idname)
        assert_shapes(node)
        draw(node)
        node.draw_buttons_ext(NodeContext(tree, node), RecordingLayout())
        for socket in (*node.inputs, *node.outputs):
            if socket.bl_idname in {"GKSocketFlow", "GKSocketAny"}:
                socket.draw(NodeContext(tree, node), RecordingLayout(), node, socket.name)
        count += 1
    assert count == len(node_lib.ALL_CLASSES)
    print(f"[PASS] all {count} node classes create and draw with real RNA properties")
    for tree in trees.values():
        bpy.data.node_groups.remove(tree)


def test_mapped_values_and_linked_controls():
    tree = new_tree("mapped_values")
    event = tree.nodes.new("GKNodeOnReady")
    math = tree.nodes.new("GKNodeMath")
    printer = tree.nodes.new("GKNodePrint")
    tree.links.new(event.outputs["执行"], printer.inputs["执行"])
    tree.links.new(math.outputs["结果"], printer.inputs["内容"])
    math.gk_a, math.gk_b = 7.0, 2.0
    # The native socket value is deliberately different: the visible editor
    # must bind the actual mapped node property used by code generation.
    math.inputs["A"].default_value = 999.0
    layout = draw(math)
    for name in ("gk_a", "gk_b", "gk_op"):
        controls = property_calls(layout, math, name)
        assert len(controls) == 1 and controls[0][3], (name, controls)
    assert not property_calls(layout, math, "gk_c")
    assert math.inputs["C"].hide
    assert math.inputs["A"].hide_value
    code, warnings = codegen.generate(tree, OPTIONS)
    assert not warnings, warnings
    assert "print((7.0 + 2.0))" in code, code
    assert "999" not in code

    constant = tree.nodes.new("GKNodeConstFloat")
    constant.gk_value = 3.0
    tree.links.new(constant.outputs["值"], math.inputs["A"])
    linked_layout = draw(math)
    assert not property_calls(linked_layout, math, "gk_a")[0][3]
    assert property_calls(linked_layout, math, "gk_b")[0][3]
    assert property_calls(linked_layout, math, "gk_op")[0][3]
    assert "连线" in property_calls(linked_layout, math, "gk_a")[0][4].get("text", "")
    assert math.gk_a == 7.0, "Connecting an input changed the stored fallback"
    assert "print((3.0 + 2.0))" in codegen.generate(tree, OPTIONS)[0]
    print_layout = draw(printer)
    controls = property_calls(print_layout, printer, "gk_text")
    assert len(controls) == 1 and not controls[0][3], controls

    math.gk_op = "CLAMP"
    draw(math)
    assert not math.inputs["C"].hide
    tree.links.new(constant.outputs["值"], math.inputs["C"])
    math.gk_op = "ADD"
    before_links = links_snapshot(tree)
    before_code = codegen.generate(tree, OPTIONS)[0]
    apply_socket_style(math)
    inactive_layout = draw(math)
    assert not math.inputs["C"].hide, "An existing wire disappeared with an inactive input"
    assert not property_calls(inactive_layout, math, "gk_c")
    assert links_snapshot(tree) == before_links
    assert codegen.generate(tree, OPTIONS)[0] == before_code
    print("[PASS] mapped values match generated code; only linked fallbacks are disabled")
    bpy.data.node_groups.remove(tree)


def test_random_math_hides_unused_inputs_and_preserves_wires():
    tree = new_tree("random_math")
    event = tree.nodes.new("GKNodeOnReady")
    math = tree.nodes.new("GKNodeMath")
    printer = tree.nodes.new("GKNodePrint")
    tree.links.new(event.outputs["执行"], printer.inputs["执行"])
    tree.links.new(math.outputs["结果"], printer.inputs["内容"])
    math.gk_op = "RANDOM"
    layout = draw(math)
    assert math.active_inputs() == ()
    assert all(socket.hide for socket in math.inputs)
    assert all(not property_calls(layout, math, prop)
               for prop in ("gk_a", "gk_b", "gk_c"))
    code, warnings = codegen.generate(tree, OPTIONS)
    assert not warnings, warnings
    assert "print(randf())" in code, code

    constant = tree.nodes.new("GKNodeConstFloat")
    constant.gk_value = 3.0
    math.gk_op = "CLAMP"
    for socket in math.inputs:
        tree.links.new(constant.outputs["值"], socket)
    before_links = links_snapshot(tree)
    math.gk_op = "RANDOM"
    apply_socket_style(math)
    linked_layout = draw(math)
    assert all(not socket.hide for socket in math.inputs)
    assert all(socket.is_linked for socket in math.inputs)
    assert all(not property_calls(linked_layout, math, prop)
               for prop in ("gk_a", "gk_b", "gk_c"))
    assert links_snapshot(tree) == before_links
    assert codegen.generate(tree, OPTIONS)[0] == code
    math.gk_op = "CLAMP"
    assert links_snapshot(tree) == before_links
    assert "clampf(3.0, 3.0, 3.0)" in codegen.generate(tree, OPTIONS)[0]
    print("[PASS] random math hides every unused input and retains existing wires")
    bpy.data.node_groups.remove(tree)


def test_conditional_defaults_preserve_card_intent():
    tree = new_tree("conditional_defaults")
    ui_set = tree.nodes.new("GKNodeUiSet")
    gated = (("gk_set_pos", ("gk_x", "gk_y")),
             ("gk_set_alpha", ("gk_alpha",)),
             ("gk_set_text", ("gk_text",)))
    all_props = {prop for _gate, names in gated for prop in names}
    for gate, names in gated:
        layout = draw(ui_set)
        assert all(not property_calls(layout, ui_set, prop) for prop in all_props)
        setattr(ui_set, gate, True)
        layout = draw(ui_set)
        for prop in all_props:
            calls = property_calls(layout, ui_set, prop)
            if prop in names:
                assert len(calls) == 1 and calls[0][3], (gate, prop, calls)
            else:
                assert not calls, (gate, prop, calls)
        setattr(ui_set, gate, False)

    number = tree.nodes.new("GKNodeConstFloat")
    tree.links.new(number.outputs["值"], ui_set.inputs["X"])
    before_links = links_snapshot(tree)
    assert not property_calls(draw(ui_set), ui_set, "gk_x")
    ui_set.gk_set_pos = True
    assert not property_calls(draw(ui_set), ui_set, "gk_x")[0][3]
    assert property_calls(draw(ui_set), ui_set, "gk_y")[0][3]
    ui_set.gk_set_pos = False
    assert not property_calls(draw(ui_set), ui_set, "gk_x")
    assert not ui_set.inputs["X"].hide
    assert links_snapshot(tree) == before_links

    sender = tree.nodes.new("GKNodeNetSend")
    assert property_calls(draw(sender), sender, "gk_text")[0][3]
    assert not property_calls(draw(sender), sender, "gk_peer")
    sender.gk_mode = "ONE"
    assert property_calls(draw(sender), sender, "gk_peer")[0][3]
    peer = tree.nodes.new("GKNodeNetPeerJoined")
    tree.links.new(peer.outputs["谁"], sender.inputs["发给谁"])
    assert not property_calls(draw(sender), sender, "gk_peer")[0][3]
    before_links = links_snapshot(tree)
    sender.gk_mode = "ALL"
    assert not property_calls(draw(sender), sender, "gk_peer")
    assert not sender.inputs["发给谁"].hide
    assert links_snapshot(tree) == before_links

    flip = tree.nodes.new("GKNodeFlip")
    for mode in ("AUTO", "LEFT", "RIGHT"):
        flip.gk_mode = mode
        assert not property_calls(draw(flip), flip, "gk_face_left")
        assert not flip.inputs["朝左"].hide
    boolean = tree.nodes.new("GKNodeConstBool")
    tree.links.new(boolean.outputs["值"], flip.inputs["朝左"])
    before_links = links_snapshot(tree)
    assert not property_calls(draw(flip), flip, "gk_face_left")
    assert not flip.inputs["朝左"].hide
    assert links_snapshot(tree) == before_links
    print("[PASS] conditional cards omit inactive defaults and preserve connected inputs")
    bpy.data.node_groups.remove(tree)


def test_dynamic_rebuild_keeps_style_links_and_code():
    tree = new_tree("dynamic_presentation")
    event = tree.nodes.new("GKNodeOnReady")
    sequence = tree.nodes.new("GKNodeSequence")
    printer = tree.nodes.new("GKNodePrint")
    printer.gk_text = "preserved"
    tree.links.new(event.outputs["执行"], sequence.inputs["执行"])
    tree.links.new(sequence.outputs["1"], printer.inputs["执行"])
    before_links = links_snapshot(tree)
    before_code = codegen.generate(tree, OPTIONS)[0]
    sequence.gk_step_count = 5
    assert len(sequence.outputs) == 5
    assert_shapes(sequence)
    assert links_snapshot(tree) == before_links
    assert codegen.generate(tree, OPTIONS)[0] == before_code

    switch = tree.nodes.new("GKNodeSwitch")
    switch.gk_case_count = 5
    assert len(switch.outputs) == 6
    assert_shapes(switch)
    vector = tree.nodes.new("GKNodeVectorMake")
    vector.gk_dim = "V2"
    vector.rebuild_sockets()
    assert len(vector.inputs) == 2
    assert_shapes(vector)
    vector.gk_dim = "V3"
    vector.rebuild_sockets()
    assert len(vector.inputs) == 3
    assert_shapes(vector)
    function = tree.gk_funcs.add()
    function.func_name, function.params = "accept_amount", "amount:float"
    call = tree.nodes.new("GKNodeFunctionCall")
    call.gk_func_name = function.func_name
    call.rebuild_sockets()
    assert call.inputs.get("amount") is not None
    assert_shapes(call)
    assert not call.inputs["amount"].hide_value

    setter = tree.nodes.new("GKNodeSetVar")
    setter.gk_var_count = 2
    setter.gk_var_name_2 = "second_value"
    constant = tree.nodes.new("GKNodeConstFloat")
    tree.links.new(constant.outputs["值"], setter.inputs["值 2"])
    setter_layout = draw(setter)
    assert setter.inputs["值 2"].hide_value
    assert property_calls(setter_layout, setter, "gk_value")[0][3]
    assert not property_calls(setter_layout, setter, "gk_value_2")[0][3]
    assert "连线" in property_calls(setter_layout, setter, "gk_value_2")[0][4]["text"]
    second_slot_links = links_snapshot(tree)
    setter.gk_var_count = 3
    draw(setter)
    assert links_snapshot(tree) == second_slot_links
    assert setter.inputs["值 2"].hide_value
    print("[PASS] sequence, switch, vector and function rebuilds preserve presentation")
    bpy.data.node_groups.remove(tree)


def test_refresh_preserves_user_layout_and_data():
    tree = new_tree("manual_layout")
    event = tree.nodes.new("GKNodeOnReady")
    printer = tree.nodes.new("GKNodePrint")
    printer.gk_text = "user configured"
    tree.links.new(event.outputs["执行"], printer.inputs["执行"])
    printer.width = 333.0
    printer.location = (530.0, -210.0)
    printer.label = "User label"
    before_links = links_snapshot(tree)
    before_code = codegen.generate(tree, OPTIONS)[0]
    for _ in range(3):
        apply_socket_style(printer)
        draw(printer)
    assert printer.width == 333.0
    assert tuple(printer.location) == (530.0, -210.0)
    assert printer.label == "User label"
    assert printer.gk_text == "user configured"
    assert links_snapshot(tree) == before_links
    assert codegen.generate(tree, OPTIONS)[0] == before_code
    print("[PASS] display refresh preserves user width, position, values, links and code")
    bpy.data.node_groups.remove(tree)


def main():
    addon.register()
    test_all_node_cards_create_and_draw()
    test_mapped_values_and_linked_controls()
    test_random_math_hides_unused_inputs_and_preserves_wires()
    test_conditional_defaults_preserve_card_intent()
    test_dynamic_rebuild_keeps_style_links_and_code()
    test_refresh_preserves_user_layout_and_data()
    print("NODE_PRESENTATION=OK")


if __name__ == "__main__":
    main()
