"""Real Blender execution-flow audit; only creates in-memory test graphs.

blender --background --factory-startup --python-exit-code 1 \
    --python tests/blender_graph_audit.py

Also loads BlueprintLab's saved node groups, when present, to detect diagnostic
noise in the redesigned example. Does not install the add-on or save preferences.
"""

import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import BlenderGameKit as addon
from BlenderGameKit import codegen
from BlenderGameKit.codegen.graph_audit import audit_execution_flow


def graph(name, kind="GKGameLogicTree"):
    return bpy.data.node_groups.new(name, kind)


def add(tree, kind, name):
    node = tree.nodes.new(kind)
    node.name = name
    return node


def test_hud_data_and_execution_are_distinct():
    tree = graph("医务室手电回归")
    event = add(tree, "GKNodeOnReady", "准备")
    text = add(tree, "GKNodeConstText", "电量")
    hud = add(tree, "GKNodeUiSet", "刷新电量")
    tree.links.new(text.outputs["值"], hud.inputs["文字"])
    result = codegen.generate_result(tree, {"base_class": "Node"})
    assert [d.node_name for d in result.diagnostics
            if d.code == "UNREACHABLE_EXECUTION"] == [hud.name]
    tree.links.new(event.outputs["执行"], hud.inputs["执行"])
    assert not audit_execution_flow(tree)


def test_reroute_and_button_callback_are_reachable():
    tree = graph("回调与转接点")
    event = add(tree, "GKNodeOnReady", "准备")
    wire = add(tree, "NodeReroute", "转接")
    button = add(tree, "GKNodeUiButton", "开始按钮")
    printer = add(tree, "GKNodePrint", "点击反馈")
    tree.links.new(button.outputs["点击"], printer.inputs["执行"])
    assert len(audit_execution_flow(tree)) == 2
    tree.links.new(event.outputs["执行"], wire.inputs[0])
    tree.links.new(wire.outputs[0], button.inputs["执行"])
    assert not audit_execution_flow(tree)
    result = codegen.generate_result(tree, {"base_class": "Node"})
    assert not [d for d in result.diagnostics if d.code == "UNREACHABLE_EXECUTION"]


def test_declared_functions_and_ui_design_have_distinct_roots():
    tree = graph("可复用函数")
    function = add(tree, "GKNodeFunctionDefine", "反馈入口")
    function.gk_func_name = "反馈"
    body = add(tree, "GKNodePrint", "反馈内容")
    tree.links.new(function.outputs["执行"], body.inputs["执行"])
    assert [d.node_name for d in audit_execution_flow(tree)] == [body.name]
    declaration = tree.gk_funcs.add()
    declaration.func_name = "反馈"
    assert not audit_execution_flow(tree)
    design = graph("主界面", "GKUiDesignTree")
    add(design, "GKNodeUiButton", "自动构建的按钮")
    assert not audit_execution_flow(design)


def test_saved_blueprint_example():
    path = os.path.join(ROOT, "examples", "demo_blueprint_lab", "BlueprintLab.blend")
    if not os.path.isfile(path):
        raise AssertionError("Build scripts/make_blueprint_lab.py before running this check")
    with bpy.data.libraries.load(path, link=False) as (source, target):
        target.node_groups = list(source.node_groups)
    logic = [tree for tree in target.node_groups if tree is not None
             and tree.bl_idname == "GKGameLogicTree"]
    assert logic, "Saved example has no logic graphs"
    warnings = [(tree.name, d.render()) for tree in logic
                for d in audit_execution_flow(tree)]
    print(f"BLUEPRINT_EXAMPLE_GRAPH_AUDIT={len(logic)} graphs, {len(warnings)} warnings")
    assert not warnings, warnings


def main():
    addon.register()
    checks = (test_hud_data_and_execution_are_distinct,
              test_reroute_and_button_callback_are_reachable,
              test_declared_functions_and_ui_design_have_distinct_roots,
              test_saved_blueprint_example)
    for check in checks:
        check()
        print(f"[PASS] {check.__name__}")
    print(f"GRAPH_AUDIT=OK ({len(checks)}/{len(checks)})")


if __name__ == "__main__":
    main()
