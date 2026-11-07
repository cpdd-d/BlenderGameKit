"""Verify reroute traversal and fanout diagnostics with real Blender sockets.

Run from the repository with:
    blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender_graph_traversal.py

All graphs are temporary in-memory data; no file or preferences are saved.
"""

import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import BlenderGameKit as addon
from BlenderGameKit import codegen
from BlenderGameKit.codegen.graph import downstream, upstream


OPTIONS = {"base_class": "Node", "export_tunables": False}


def new_tree(name):
    return bpy.data.node_groups.new(name, "GKGameLogicTree")


def add(tree, kind, name):
    node = tree.nodes.new(kind)
    node.name = name
    return node


def wire(tree, source, destination, output="完成", input_name="执行"):
    tree.links.new(source.outputs[output], destination.inputs[input_name])


def fanout_diagnostics(tree):
    return [item for item in codegen.generate_result(tree, OPTIONS).diagnostics
            if item.code == "SUSPENDING_FLOW_FANOUT"]


def test_reroute_fanout_reports_source():
    tree = new_tree("转接点扇出")
    event = add(tree, "GKNodeOnReady", "准备事件")
    reroute = add(tree, "NodeReroute", "转接点")
    wait = add(tree, "GKNodeWait", "等待")
    printer = add(tree, "GKNodePrint", "另一支路")
    tree.links.new(event.outputs["执行"], reroute.inputs[0])
    tree.links.new(reroute.outputs[0], wait.inputs["执行"])
    tree.links.new(reroute.outputs[0], printer.inputs["执行"])
    reports = fanout_diagnostics(tree)
    assert len(reports) == 1, reports
    assert reports[0].node_name == event.name, reports


def test_wait_beyond_reroute_is_detected():
    tree = new_tree("支路中途转接")
    event = add(tree, "GKNodeOnReady", "准备事件")
    first = add(tree, "GKNodePrint", "进入等待支路")
    second = add(tree, "GKNodePrint", "普通支路")
    reroute = add(tree, "NodeReroute", "等待前转接")
    wait = add(tree, "GKNodeWait", "支路末尾等待")
    wire(tree, event, first, "执行")
    wire(tree, event, second, "执行")
    tree.links.new(first.outputs["完成"], reroute.inputs[0])
    tree.links.new(reroute.outputs[0], wait.inputs["执行"])
    reports = fanout_diagnostics(tree)
    assert len(reports) == 1, reports
    assert reports[0].node_name == event.name, reports


def test_sequence_outputs_are_explicit():
    tree = new_tree("序列编号分支")
    event = add(tree, "GKNodeOnReady", "准备事件")
    sequence = add(tree, "GKNodeSequence", "明确执行顺序")
    first = add(tree, "GKNodePrint", "第一步")
    wait = add(tree, "GKNodeWait", "第二步等待")
    reroute = add(tree, "NodeReroute", "等待前转接")
    wire(tree, event, sequence, "执行")
    wire(tree, sequence, first, "1")
    tree.links.new(sequence.outputs["2"], reroute.inputs[0])
    tree.links.new(reroute.outputs[0], wait.inputs["执行"])
    assert not fanout_diagnostics(tree)


def test_data_reroute_preserves_code():
    tree = new_tree("数据线转接")
    event = add(tree, "GKNodeOnReady", "准备事件")
    setter = add(tree, "GKNodeSetVar", "设置速度")
    setter.gk_var_name = "速度"
    value = add(tree, "GKNodeConstFloat", "数值")
    value.gk_value = 3.5
    wire(tree, event, setter, "执行")
    direct_link = tree.links.new(value.outputs[0], setter.inputs["值"])
    direct_code = codegen.generate_result(tree, OPTIONS).code
    tree.links.remove(direct_link)
    reroute = add(tree, "NodeReroute", "数据转接点")
    tree.links.new(value.outputs[0], reroute.inputs[0])
    tree.links.new(reroute.outputs[0], setter.inputs["值"])
    assert codegen.generate_result(tree, OPTIONS).code == direct_code
    assert upstream(setter.inputs["值"]).from_node == value


def test_long_chain_does_not_recurse():
    tree = new_tree("1200个转接点")
    event = add(tree, "GKNodeOnReady", "准备事件")
    printer = add(tree, "GKNodePrint", "长链终点")
    printer.gk_text = "long_chain_endpoint"
    previous = event.outputs["执行"]
    for index in range(1200):
        reroute = add(tree, "NodeReroute", f"转接{index}")
        tree.links.new(previous, reroute.inputs[0])
        previous = reroute.outputs[0]
    tree.links.new(previous, printer.inputs["执行"])
    expected = [(printer, "执行")]
    assert downstream(event.outputs["执行"]) == expected
    index = {}
    for link in tree.links:
        index.setdefault(link.from_socket, []).append(link)
    assert downstream(event.outputs["执行"], links_by_socket=index) == expected
    assert upstream(printer.inputs["执行"]).from_node == event
    result = codegen.generate_result(tree, OPTIONS)
    assert result.code.count('print("long_chain_endpoint")') == 1, result.code
    assert not result.warnings, result.warnings


def main():
    addon.register()
    checks = (test_reroute_fanout_reports_source,
              test_wait_beyond_reroute_is_detected,
              test_sequence_outputs_are_explicit,
              test_data_reroute_preserves_code,
              test_long_chain_does_not_recurse)
    for check in checks:
        check()
        print(f"[PASS] {check.__name__}")
    print(f"GRAPH_TRAVERSAL=OK ({len(checks)}/{len(checks)})")


if __name__ == "__main__":
    main()
