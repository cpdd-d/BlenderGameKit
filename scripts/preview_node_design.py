"""Create a readable game-logic graph for inspecting node presentation.

Background fixture generation:
    blender --background --factory-startup --python-exit-code 1 \
        --python scripts/preview_node_design.py

Can also be run from Blender's Text Editor. It creates a new demonstration
tree, displays it in the largest editor, and saves a copy to the preview path.
Existing objects, node groups, files, and the current project path are retained.
Use Blender's Home key to frame the graph after opening the preview file.
"""

import argparse
from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import BlenderGameKit as addon
from BlenderGameKit import codegen


DEFAULT_OUTPUT = ROOT / ".audit" / "node_design_20261107" / "节点显示预览.blend"


def frame(tree, title, location, color):
    node = tree.nodes.new("NodeFrame")
    node.name = title
    node.label = title
    node.location = location
    node.label_size = 24
    node.use_custom_color = True
    node.color = color
    node.shrink = True
    return node


def add(tree, parent, kind, title, location, **values):
    node = tree.nodes.new(kind)
    node.name = title
    node.label = title
    node.parent = parent
    node.location = location
    for name, value in values.items():
        setattr(node, name, value)
    return node


def connect(tree, source, output, destination, input_name):
    tree.links.new(source.outputs[output], destination.inputs[input_name])


def build_graph():
    tree = bpy.data.node_groups.new("节点显示预览 · 初始化与计分", "GKGameLogicTree")
    tree.use_fake_user = True
    for name, kind, value in (("允许计分", "bool", "false"),
                              ("分数", "float", "0.0")):
        variable = tree.gk_vars.add()
        variable.var_name = name
        variable.var_type = kind
        variable.default_value = value

    setup = frame(tree, "01 · 初始化", (0.0, 440.0), (0.12, 0.15, 0.18))
    ready = add(tree, setup, "GKNodeOnReady", "游戏开始", (0.0, 0.0))
    sequence = add(tree, setup, "GKNodeSequence", "按顺序初始化", (300.0, 0.0),
                   gk_step_count=2)
    startup = add(tree, setup, "GKNodePrint", "输出开始提示", (600.0, 0.0),
                  gk_text="游戏开始")
    allow = add(tree, setup, "GKNodeConstBool", "允许更新", (300.0, -220.0),
                gk_value=True)
    enable = add(tree, setup, "GKNodeSetVar", "启用计分", (600.0, -220.0),
                 gk_var_name="允许计分")
    connect(tree, ready, "执行", sequence, "执行")
    connect(tree, sequence, "1", startup, "执行")
    connect(tree, sequence, "2", enable, "执行")
    connect(tree, allow, "值", enable, "值")

    update = frame(tree, "02 · 条件与数据", (0.0, -110.0), (0.12, 0.17, 0.15))
    physics = add(tree, update, "GKNodeOnPhysicsProcess", "每个物理帧", (0.0, 0.0))
    gate = add(tree, update, "GKNodeGetVar", "读取允许计分", (0.0, -260.0),
               gk_var_name="允许计分")
    reroute = add(tree, update, "NodeReroute", "布尔转接", (450.0, -240.0))
    branch = add(tree, update, "GKNodeBranch", "允许计分？", (600.0, 0.0))
    base = add(tree, update, "GKNodeConstFloat", "基础增量", (300.0, -330.0),
               gk_value=1.0)
    math = add(tree, update, "GKNodeMath", "相加 · 接线与默认值", (600.0, -330.0),
               gk_op="ADD", gk_a=10.0, gk_b=2.0)
    score = add(tree, update, "GKNodeAddVar", "累加分数", (910.0, 0.0),
                gk_var_name="分数", gk_amount=1.0)
    observe = add(tree, update, "GKNodePrint", "观察新分数", (1220.0, 0.0),
                  gk_text="未接线时的内容")
    connect(tree, physics, "执行", branch, "执行")
    tree.links.new(gate.outputs["值"], reroute.inputs[0])
    tree.links.new(reroute.outputs[0], branch.inputs["条件"])
    connect(tree, branch, "是", score, "执行")
    connect(tree, base, "值", math, "A")
    connect(tree, math, "结果", score, "增减")
    connect(tree, score, "完成", observe, "执行")
    connect(tree, score, "新值", observe, "内容")

    for node in tree.nodes:
        node.select = False
    math.select = True
    tree.nodes.active = math
    result = codegen.generate_result(tree, {"base_class": "Node", "export_tunables": False})
    assert not result.warnings, result.warnings
    assert "(1.0 + 2.0)" in result.code, result.code
    assert len([node for node in tree.nodes if node.bl_idname.startswith("GKNode")]) == 12
    assert len(tree.links) == 12
    print("PREVIEW_GRAPH=OK (12 logic nodes, 1 reroute, 2 frames)")
    return tree


def show_graph(tree):
    window = bpy.context.window
    screen = window.screen if window else bpy.context.screen
    if screen is None or not screen.areas:
        return
    area = max(screen.areas, key=lambda item: item.width * item.height)
    area.type = "NODE_EDITOR"
    space = area.spaces.active
    space.tree_type = "GKGameLogicTree"
    space.node_tree = tree
    space.pin = True
    space.show_region_ui = False
    space.show_region_toolbar = False
    region = next((item for item in area.regions if item.type == "WINDOW"), None)
    if window is not None and region is not None:
        with bpy.context.temp_override(window=window, area=area, region=region):
            if bpy.ops.node.view_all.poll():
                bpy.ops.node.view_all()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = parser.parse_args(arguments)
    if bpy.types.NodeTree.bl_rna_get_subclass_py("GKGameLogicTree", None) is None:
        addon.register()
    tree = build_graph()
    show_graph(tree)
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    original_path = bpy.data.filepath
    bpy.ops.wm.save_as_mainfile(filepath=str(output), copy=True)
    assert bpy.data.filepath == original_path, "Saving the preview changed the current project path"
    print("NODE_DESIGN_PREVIEW=" + str(output))


if __name__ == "__main__":
    if bpy.app.background:
        main()
    else:
        # Switching the active console while its paste operator is still running
        # can crash Blender. Build after that operator has returned to the UI.
        bpy.app.timers.register(main, first_interval=0.1)
