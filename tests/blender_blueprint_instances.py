"""Verify the saved example shares one role graph across two bound objects."""
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import BlenderGameKit as addon
from BlenderGameKit import exporter, gk_sockets, properties


def main():
    addon.register()
    # Role graph names are string properties, so appending only scenes does not
    # load their referenced node groups. Reopen the actual saved source instead.
    bpy.ops.wm.open_mainfile(filepath=str(ROOT / "examples/demo_blueprint_lab/BlueprintLab.blend"))
    assert bpy.context.scene.name == "BlueprintLab", "Opening the source must show the game, not an empty library scene"
    scene = bpy.data.scenes["BlueprintLab"]
    gates = [scene.objects[name] for name in ("EnergyGateA", "EnergyGateB")]
    names = {gate.gk_physics.logic_tree for gate in gates}
    assert len(names) == 1, "Each gate must reference the exact same source graph"
    tree = bpy.data.node_groups[next(iter(names))]
    assert tree.gk_scope == "ROLE"
    assert all(gate.gk_physics.script_target and gate.gk_physics.collision_shape == "box" for gate in gates)
    bindings = [exporter.object_reference_bindings(gate, scene) for gate in gates]
    assert bindings == [{"玩家": "Player", "控制台": "TerminalA"},
                        {"玩家": "Player", "控制台": "TerminalB"}], bindings
    play = next(n for n in tree.nodes if n.bl_idname == "GKNodePlayAnim")
    assert not play.gk_player, "Playback must use the instance's own AnimationPlayer"
    assert play.gk_anim_action is gates[0].animation_data.action is gates[1].animation_data.action
    first_slot = getattr(gates[0].animation_data, "action_slot", None)
    if first_slot is not None:
        assert gates[1].animation_data.action_slot == first_slot, "Shared Action must use the same OBJECT slot"
    assert len(exporter.object_logic_trees(scene)) == 3, "Two gates plus one player must export separately"
    for n in tree.nodes:
        for prop in ("gk_target", "gk_a", "gk_b", "gk_player"):
            value = getattr(n, prop, "")
            assert value not in {"EnergyGateA", "EnergyGateB", "TerminalA", "TerminalB"}, (n.name, prop)
    design = bpy.data.node_groups["界面·实验室HUD"]
    header = design.nodes["LabHeader"]
    assert header.gk_align == "TOP_CENTER" and header.gk_width == 650
    level = bpy.data.node_groups[scene.gk_settings.logic_tree]
    signature = properties.tree_functions(tree)["try_activate"]
    assert signature["params"] == [("radius", "float")] and signature["return"] == "bool", signature
    for index, gate in enumerate(gates, start=1):
        call = level.nodes["带参调用门实例" + str(index)]
        assert call.bl_idname == "GKNodeFunctionCall"
        assert call.gk_function_tree == tree.name and call.gk_target == gate.name
        assert call.inputs["radius"].bl_idname == gk_sockets.idname_for_gd_type("float")
        assert call.outputs["结果"].bl_idname == gk_sockets.idname_for_gd_type("bool")
        assert call.outputs["成功"].bl_idname == gk_sockets.idname_for_gd_type("bool")
        radius_link = next(link for link in level.links if link.to_socket == call.inputs["radius"])
        assert abs(radius_link.from_node.gk_value - (1.4 if index == 1 else 2.2)) < 1e-5
        assert any(link.from_socket == call.outputs["结果"] and link.to_node.bl_idname == "GKNodeBranch" for link in level.links)
    panel = level.nodes["PauseMenu"]
    assert panel.gk_start_hidden and panel.gk_block_click and panel.gk_focus_first
    assert panel.gk_align == "CENTER"
    arm = level.nodes["注册Esc暂停"]
    assert arm.gk_hotkey and not arm.gk_pause and arm.gk_menu == panel.name
    for name in ("ResumeButton", "RestartButton"):
        assert level.nodes[name].gk_parent_widget == panel.name
    assert any(link.from_node == level.nodes["重开前恢复"] and
               link.to_node == level.nodes["暂停菜单重开"] for link in level.links)
    assert all(graph.use_fake_user for graph in (tree, design, level))
    print("BLUEPRINT_INSTANCES=OK (shared graph, independent bindings, local animation, 3 hosts, native pause UI, typed cross-object calls)")


if __name__ == "__main__":
    main()
