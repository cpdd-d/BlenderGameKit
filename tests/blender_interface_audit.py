"""Probe interface diagnostics using a copied BlueprintLab role graph.

Run with --background --factory-startup --python-exit-code 1 --python this_file.
The example is read-only: all new graphs/objects stay in memory, nothing is saved.
"""

from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import BlenderGameKit as addon
from BlenderGameKit import codegen
from BlenderGameKit.nodes.node_base import safe_function_name


def function(tree, name, params="", define=True):
    declaration = tree.gk_funcs.add()
    declaration.func_name, declaration.params = name, params
    if define:
        node = tree.nodes.new("GKNodeFunctionDefine")
        node.gk_func_name = name
        return node


def caller(kind, **properties):
    tree = bpy.data.node_groups.new("接口验证调用方", "GKGameLogicTree")
    ready = tree.nodes.new("GKNodeOnReady")
    node = tree.nodes.new(kind)
    node.name = "接口调用节点"
    for name, value in properties.items():
        setattr(node, name, value)
    tree.links.new(ready.outputs["执行"], node.inputs["执行"])
    return tree, node


def checks(tree, host):
    result = codegen.generate_result(tree, {"base_class": "Node3D",
                                            "reference_bindings": {"测试机关": host.name}})
    return [d for d in result.diagnostics if d.code.startswith("INTERFACE_")]


def runtime_fixture(host):
    """Write a tiny real-Blender-generated Godot probe; never modify the example."""
    def node(tree, kind, **values):
        item = tree.nodes.new(kind)
        for name, value in values.items():
            setattr(item, name, value)
        return item

    def link(tree, source, dest, output="完成"):
        tree.links.new(source.outputs[output], dest.inputs["执行"])

    target = bpy.data.node_groups.new("接口实机·门", "GKGameLogicTree")
    target.gk_scope = "ROLE"
    host.gk_physics.logic_tree = target.name
    modern = function(target, "开门")
    count = node(target, "GKNodeAddVar", gk_var_name="open_calls", gk_amount=1.0)
    emit = node(target, "GKNodeEmitSignal", gk_signal_name="已开门")
    link(target, modern, count, "执行")
    link(target, count, emit)
    legacy = node(target, "GKNodeFunction", gk_func_name="旧开门")
    legacy_count = node(target, "GKNodeAddVar", gk_var_name="legacy_calls", gk_amount=1.0)
    link(target, legacy, legacy_count, "执行")

    source = bpy.data.node_groups.new("接口实机·调用方", "GKGameLogicTree")
    ready = node(source, "GKNodeOnReady")
    connect = node(source, "GKNodeConnect", gk_target="@门", gk_signal_name="已开门", gk_handler="接收回执")
    call = node(source, "GKNodeCallFunction", gk_target="@门", gk_func_name="开门")
    old_call = node(source, "GKNodeCallFunction", gk_target="@门", gk_func_name="旧开门")
    link(source, ready, connect, "执行")
    link(source, connect, call)
    link(source, call, old_call)
    callback = function(source, "接收回执")
    received = node(source, "GKNodeAddVar", gk_var_name="signal_count", gk_amount=1.0)
    link(source, callback, received, "执行")
    options = {"base_class": "Node3D", "reference_bindings": {"门": host.name}, "export_tunables": False}
    target_result = codegen.generate_result(target, options)
    source_result = codegen.generate_result(source, options)
    for result in (target_result, source_result):
        assert not [d for d in result.diagnostics if d.level == "error"], result.diagnostics
    output = ROOT / ".audit" / "interface_runtime"
    output.mkdir(parents=True, exist_ok=True)
    files = {
        "target.gd": target_result.code,
        "caller.gd": source_result.code,
        "project.godot": '''config_version=5
[application]
config/name="Blueprint Interface Runtime"
run/main_scene="res://main.tscn"
[rendering]
renderer/rendering_method="gl_compatibility"
''',
        "main.tscn": '''[gd_scene load_steps=4 format=3]
[ext_resource type="Script" path="res://probe.gd" id="1"]
[ext_resource type="Script" path="res://target.gd" id="2"]
[ext_resource type="Script" path="res://caller.gd" id="3"]
[node name="Main" type="Node3D"]
script = ExtResource("1")
[node name="InterfaceDoor" type="Node3D" parent="."]
script = ExtResource("2")
[node name="Caller" type="Node3D" parent="."]
script = ExtResource("3")
''',
        "probe.gd": '''extends Node3D

func _ready() -> void:
    await get_tree().process_frame
    var door := get_node("InterfaceDoor")
    var caller := get_node("Caller")
    var checks := [door.get("open_calls") == 1.0,
                   door.get("legacy_calls") == 1.0,
                   caller.get("signal_count") == 1.0]
    if checks.all(func(value): return value):
        print("INTERFACE_RUNTIME=OK (modern Chinese call, Chinese signal callback, legacy Chinese call)")
        get_tree().quit(0)
    else:
        printerr("INTERFACE_RUNTIME=FAIL ", checks)
        get_tree().quit(1)
''',
    }
    for name, content in files.items():
        (output / name).write_text(content, encoding="utf-8")
    print("INTERFACE_RUNTIME_FIXTURE=" + str(output))


def main():
    addon.register()
    path = ROOT / "examples" / "demo_blueprint_lab" / "BlueprintLab.blend"
    with bpy.data.libraries.load(str(path), link=False) as (source, target):
        target.node_groups = list(source.node_groups)
    original = next(tree for tree in target.node_groups if tree is not None
                    and tree.bl_idname == "GKGameLogicTree" and "组件" in tree.name)
    original_state = (len(original.nodes), len(original.gk_funcs))
    target_tree = original.copy()
    target_tree.name = "接口测试·示例组件副本"
    target_tree.gk_scope = "ROLE"
    function(target_tree, "receive_amount", "amount:int")
    function(target_tree, "开门")
    function(target_tree, "declared_only", define=False)
    signal = target_tree.nodes.new("GKNodeEmitSignal")
    signal.gk_signal_name = "powered"
    host = bpy.data.objects.new("InterfaceDoor", bpy.data.meshes.new("InterfaceDoorMesh"))
    bpy.context.scene.collection.objects.link(host)
    host.gk_physics.enabled = True
    host.gk_physics.body_type = "StaticBody3D"
    host.gk_physics.script_target = True
    host.gk_physics.logic_tree = target_tree.name

    tree, node = caller("GKNodeCallFunction", gk_target="@测试机关", gk_func_name="receive_amount")
    found = checks(tree, host)
    assert [d.code for d in found] == ["INTERFACE_CALL_ARGUMENT_MISMATCH"], found
    assert (found[0].tree_name, found[0].node_name, found[0].level) == (tree.name, node.name, "error")
    print("[PASS] role_reference_required_argument_mismatch")

    tree, node = caller("GKNodeConnect", gk_target=host.name, gk_signal_name="powered", gk_handler="on_powered")
    function(tree, "on_powered", "amount:int")
    found = checks(tree, host)
    assert [d.code for d in found] == ["INTERFACE_SIGNAL_ARGUMENT_MISMATCH"], found
    print("[PASS] custom_signal_required_callback_argument_mismatch")

    tree, node = caller("GKNodeCallFunction", gk_target=host.name, gk_func_name="开门")
    found = checks(tree, host)
    assert not found, found
    result = codegen.generate_result(tree, {"base_class": "Node3D"})
    assert f".{safe_function_name('开门')}()" in result.code, result.code
    print("[PASS] chinese_function_call_matches_exported_definition")

    tree, node = caller("GKNodeCallFunction", gk_target=host.name, gk_func_name="declared_only")
    found = checks(tree, host)
    assert [(d.code, d.level) for d in found] == [("INTERFACE_FUNCTION_NOT_DEFINED", "warning")], found
    print("[PASS] declaration_without_implementation_warning")

    tree, node = caller("GKNodeCallFunction", gk_target=host.name, gk_func_name="queue_free")
    assert not checks(tree, host)
    node.gk_target = "RuntimeOnly/Child"
    node.gk_func_name = "receive_amount"
    assert not checks(tree, host)
    assert original_state == (len(original.nodes), len(original.gk_funcs))
    print("[PASS] native_and_dynamic_interfaces_are_not_guessed")
    print("INTERFACE_AUDIT=OK (5/5)")
    runtime_fixture(host)


if __name__ == "__main__":
    main()
