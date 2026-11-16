"""Check report navigation against real Blender trees and editor state.

Run with --background --factory-startup --python-exit-code 1 --python this_file.
No .blend file, add-on preferences, or user installation is changed.
"""

import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import BlenderGameKit as addon
from BlenderGameKit import export_report
from BlenderGameKit.models import Diagnostic


def new_tree(name, kind="GKGameLogicTree", node_kind="GKNodePrint"):
    tree = bpy.data.node_groups.new(name, kind)
    node = tree.nodes.new(node_kind)
    node.name = "同名反馈"
    node.select = False
    return tree, node


def main():
    addon.register()
    first, first_node = new_tree("角色甲")
    second, second_node = new_tree("角色乙")
    other = second.nodes.new("GKNodePrint")
    other.select = True
    ui, ui_node = new_tree("游戏界面", "GKUiDesignTree", "GKNodeUiButton")
    area = bpy.context.screen.areas[0]
    previous_type = area.type
    try:
        area.type = "NODE_EDITOR"
        space = area.spaces.active
        space.tree_type = first.bl_idname
        space.pin = True
        space.node_tree = first
        region = next(region for region in area.regions if region.type == "WINDOW")
        with bpy.context.temp_override(area=area, region=region):
            result = bpy.ops.gk.export_report_focus(tree_name=second.name, node_name=second_node.name)
            assert result == {"FINISHED"}
            assert space.node_tree == second, "Editor must switch away from the same-name node in first tree"
            assert second.nodes.active == second_node and second_node.select
            assert not first_node.select and not other.select
            print("[PASS] exact_graph_switches_editor_and_selects_only_target")

            result = bpy.ops.gk.export_report_focus(tree_name=ui.name, node_name=ui_node.name)
            assert result == {"FINISHED"}
            assert space.tree_type == ui.bl_idname and space.node_tree == ui
            assert ui.nodes.active == ui_node
            print("[PASS] ui_graph_switches_editor_type")

            before = space.node_tree
            result = bpy.ops.gk.export_report_focus(tree_name="已删除的图", node_name=first_node.name)
            assert result == {"CANCELLED"} and space.node_tree == before
            result = bpy.ops.gk.export_report_focus(tree_name=first.name, node_name="已删除的节点")
            assert result == {"CANCELLED"} and space.node_tree == before
            print("[PASS] stale_explicit_location_never_uses_another_graph")

            result = bpy.ops.gk.export_report_focus(node_name=first_node.name)
            assert result == {"CANCELLED"} and space.node_tree == before
            first_node.name = "唯一旧节点"
            result = bpy.ops.gk.export_report_focus(node_name=first_node.name)
            assert result == {"FINISHED"} and space.node_tree == first
            print("[PASS] legacy_location_requires_an_unambiguous_match")

            export_report.record(bpy.context.scene, {"diagnostics": [
                Diagnostic(message="测试定位", node_name=second_node.name, tree_name=second.name)]})
            export_report.clear()
            saved = export_report.load(bpy.context.scene)["items"][0]
            result = bpy.ops.gk.export_report_focus(tree_name=saved["tree_name"], node_name=saved["node_name"])
            assert result == {"FINISHED"} and space.node_tree == second
            print("[PASS] persisted_report_preserves_target_identity")
    finally:
        area.type = previous_type
    print("DIAGNOSTIC_LOCATIONS=OK (5/5)")


if __name__ == "__main__":
    main()
