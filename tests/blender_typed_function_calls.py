"""Real Blender editor + generated Godot typed-call fixture; no example changes."""
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import mock
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import BlenderGameKit as addon
from BlenderGameKit import codegen, properties, ui, gk_sockets as S


def node(tree, kind, **values):
    item = tree.nodes.new(kind)
    for key, value in values.items():
        setattr(item, key, value)
    return item


def link(tree, source, dest, out="完成", inp="执行"):
    tree.links.new(source.outputs[out], dest.inputs[inp])


def main():
    addon.register()
    source = bpy.data.node_groups.new("TypedSource", "GKGameLogicTree")
    source.gk_scope = "ROLE"
    declaration = source.gk_funcs.add()
    declaration.func_name, declaration.params, declaration.return_type = "echo", "amount:float, extra:int", "float"
    definition = node(source, "GKNodeFunctionDefine", gk_func_name="echo")
    read = node(source, "GKNodeGetVar", gk_var_name="amount")
    link(source, read, definition, "值", "返回")
    count = node(source, "GKNodeAddVar", gk_var_name="calls", gk_amount=1)
    link(source, definition, count, "执行")

    caller = bpy.data.node_groups.new("TypedCaller", "GKGameLogicTree")
    ready = node(caller, "GKNodeOnReady")
    call = node(caller, "GKNodeFunctionCall", gk_function_tree=source.name,
                gk_target="@door", gk_func_name="echo")
    call.inputs["extra"].default_value = 7
    number = node(caller, "GKNodeConstFloat", gk_value=2.2)
    link(caller, number, call, "值", "amount")
    link(caller, ready, call, "执行")
    declaration.params = "amount:float, extra:int, enabled:bool"
    declaration.return_type = "bool"
    assert call.inputs["amount"].is_linked
    assert call.inputs["amount"].links[0].from_node == number
    assert call.inputs["extra"].default_value == 7
    assert call.inputs["enabled"].bl_idname == S.idname_for_gd_type("bool")
    assert call.outputs["结果"].bl_idname == S.idname_for_gd_type("bool")
    assert call.outputs["成功"].bl_idname == S.idname_for_gd_type("bool")
    with mock.patch.object(properties, "current_tree", return_value=caller):
        specs = ui._function_picker_specs(SimpleNamespace(node_name=call.name), None)
    assert list(specs) == ["echo"] and len(specs["echo"]["params"]) == 3
    properties.rename_function_refs(source, "echo", "echo_updated")
    declaration.func_name = "echo_updated"
    assert call.gk_func_name == "echo_updated"
    assert call.inputs["amount"].is_linked and call.inputs["extra"].default_value == 7
    properties.rename_function_refs(source, "echo_updated", "echo")
    declaration.func_name = "echo"
    declaration.params, declaration.return_type = "amount:float, extra:int", "float"
    print("TYPED_EDITOR=OK (signature sync, links, defaults, return type, picker, rename)")

    host = bpy.data.objects.new("TypedDoor", bpy.data.meshes.new("TypedDoorMesh"))
    bpy.context.scene.collection.objects.link(host)
    host.gk_physics.enabled = True
    host.gk_physics.body_type = "StaticBody3D"
    host.gk_physics.script_target = True
    host.gk_physics.logic_tree = source.name

    # Two consumers reuse one return value; the method must run only once.
    first = node(caller, "GKNodeSetVar", gk_var_name="received_one")
    second = node(caller, "GKNodeSetVar", gk_var_name="received_two")
    success = node(caller, "GKNodeSetVar", gk_var_name="dispatched")
    link(caller, call, first)
    link(caller, first, second)
    link(caller, second, success)
    link(caller, call, first, "结果", "值")
    link(caller, call, second, "结果", "值")
    link(caller, call, success, "成功", "值")

    last = success
    for name, target in (("missing", "DeletedDoor"), ("no_method", "PlainNode")):
        missing = node(caller, "GKNodeFunctionCall", gk_function_tree=source.name,
                       gk_target=target, gk_func_name="echo")
        fallback = node(caller, "GKNodeSetVar", gk_var_name=name + "_result")
        status = node(caller, "GKNodeSetVar", gk_var_name=name + "_success")
        link(caller, last, missing)
        link(caller, missing, fallback)
        link(caller, fallback, status)
        link(caller, missing, fallback, "结果", "值")
        link(caller, missing, status, "成功", "值")
        last = status

    options = {"base_class": "Node3D", "export_tunables": False, "reference_bindings": {"door": host.name}}
    target_result = codegen.generate_result(source, options)
    caller_result = codegen.generate_result(caller, options)
    for result in (target_result, caller_result):
        assert not [d for d in result.diagnostics if d.level == "error"], result.diagnostics
    assert caller_result.code.count(".echo(2.2, 7)") == 1, caller_result.code
    output = ROOT / ".audit" / "typed_interface_runtime"
    output.mkdir(parents=True, exist_ok=True)
    files = {
        "target.gd": target_result.code,
        "caller.gd": caller_result.code,
        "project.godot": '''config_version=5
[application]
config/name="Typed Blueprint Interface"
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
[node name="TypedDoor" type="Node3D" parent="."]
script = ExtResource("2")
[node name="PlainNode" type="Node3D" parent="."]
[node name="Caller" type="Node3D" parent="."]
script = ExtResource("3")
''',
        "probe.gd": '''extends Node3D
func _ready() -> void:
    await get_tree().process_frame
    var caller := get_node("Caller")
    var checks := [get_node("TypedDoor").get("calls") == 1.0,
        is_equal_approx(caller.get("received_one"), 2.2),
        is_equal_approx(caller.get("received_two"), 2.2),
        caller.get("dispatched") == true,
        caller.get("missing_result") == 0.0, caller.get("missing_success") == false,
        caller.get("no_method_result") == 0.0, caller.get("no_method_success") == false]
    if checks.all(func(value): return value):
        print("TYPED_RUNTIME=OK (8/8)")
        get_tree().quit(0)
    else:
        printerr("TYPED_RUNTIME=FAIL ", checks)
        get_tree().quit(1)
''',
    }
    for name, content in files.items():
        (output / name).write_text(content, encoding="utf-8")
    print("TYPED_FIXTURE=" + str(output))


if __name__ == "__main__":
    main()
