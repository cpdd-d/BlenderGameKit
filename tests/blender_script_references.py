"""Exercise script Text references with real Blender and exported Godot code.

Run from the repository:
    blender --background --factory-startup --python-exit-code 1 \
        --python tests/blender_script_references.py

Set GK_GODOT to override the Godot executable. All .blend, export and user-data
files live in a temporary directory; no example or user preferences are saved.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import BlenderGameKit as addon
from BlenderGameKit import codegen, exporter, script_assets


OPTIONS = {"base_class": "Node", "export_tunables": False}
TREE_NAME = "脚本引用测试"
COPY_NAME = "脚本引用副本"
NODE_NAMES = ("加载", "停止", "调用")
BODY = '''extends Node

func _record(value: String) -> void:
    var host := get_parent()
    var trace: Array = host.get_meta("trace", [])
    trace.append(value)
    host.set_meta("trace", trace)

func _ready() -> void:
    _record("ready")

func start() -> void:
    _record("start")

func burst() -> void:
    _record("burst")

func stop() -> void:
    _record("stop")
'''


def script_nodes(tree):
    return [tree.nodes[name] for name in NODE_NAMES]


def make_text(name, body="extends Node\n"):
    text = bpy.data.texts.new(name)
    text.write(body)
    return text


def assert_selected(tree, text):
    assert all(node.gk_script_text == text for node in script_nodes(tree))


def assert_code_and_assets(tree, text, directory):
    code, warnings = codegen.generate(tree, OPTIONS)
    assert not warnings, warnings
    key = script_assets.instance_key(text.name)
    path = script_assets.res_path(text.name)
    assert f'_gk_script_load(self, "{path}", "{key}", "start")' in code, code
    assert f'_gk_script_call("{key}", "burst")' in code, code
    assert f'_gk_script_stop("{key}", "stop")' in code, code
    files = script_assets.script_files(str(directory))
    assert len(files) == 1, files
    assert files[0][0] == key, files
    assert Path(files[0][1]) == directory / script_assets.SCRIPT_DIR / script_assets.file_name(text.name)
    assert files[0][2] == BODY, files
    return code


class LayoutProbe:
    def __init__(self):
        self.props = []
        self.operators = []

    def row(self, **_kwargs):
        return self

    def prop(self, node, name, **_kwargs):
        self.props.append((node, name))

    def template_ID(self, node, name, **_kwargs):
        self.props.append((node, name))

    def operator(self, operator, **_kwargs):
        result = SimpleNamespace()
        self.operators.append((operator, result))
        return result

    def label(self, **_kwargs):
        pass


def check_open_buttons(tree, text):
    for node in script_nodes(tree):
        layout = LayoutProbe()
        node.draw_buttons(bpy.context, layout)
        assert (node, "gk_script_text") in layout.props, layout.props
        buttons = [props for operator, props in layout.operators
                   if operator == "gk.open_text_block"]
        assert len(buttons) == 1, layout.operators
        assert buttons[0].text_name == text.name, buttons[0]


def check_export_and_runtime(tree, text, directory):
    directory.mkdir()
    (directory / "project.godot").write_text(
        'config_version=5\n[application]\nconfig/name="GK script reference regression"\n'
        '[rendering]\nrenderer/rendering_method="gl_compatibility"\n', encoding="utf-8")
    result = exporter.export_tree(
        tree, {**OPTIONS, "output_dir": str(directory), "class_name": ""},
        write_scene=False)
    assert result["ok"], result
    exported = directory / script_assets.SCRIPT_DIR / script_assets.file_name(text.name)
    assert exported.read_text(encoding="utf-8") == BODY
    assert len(list(exported.parent.glob("*.gd"))) == 1
    main_script = directory / "scripts" / result["script_name"]
    assert script_assets.res_path(text.name) in main_script.read_text(encoding="utf-8")
    print("[PASS] export writes the referenced Text body and matching path")

    godot = Path(os.environ.get(
        "GK_GODOT", r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"))
    if not godot.is_file():
        print("[SKIP] Godot executable unavailable; export checks passed")
        return False
    script_path = json.dumps("res://scripts/" + result["script_name"])
    runner = '''extends SceneTree

var failures: int = 0

func _initialize() -> void:
    _run.call_deferred()

func _check(condition: bool, message: String) -> void:
    if not condition:
        failures += 1
        printerr("SCRIPT_REFERENCE_FAIL: " + message)

func _run() -> void:
    var generated := load(SCRIPT_PATH) as Script
    _check(generated != null, "generated script must load")
    if generated == null:
        quit(1)
        return
    var host := Node.new()
    host.set_script(generated)
    root.add_child(host)
    _check(host.get_meta("trace", []) == ["ready", "start", "burst", "stop"],
           "load/start/call/stop must use the same selected script")
    _check(host.get("_gk_custom_scripts").is_empty(), "stop must clear the instance key")
    await process_frame
    await process_frame
    _check(host.get_child_count() == 0, "stop must free the loaded script node")
    host.queue_free()
    if failures == 0:
        print("SCRIPT_REFERENCE_RUNTIME=OK")
    quit(1 if failures else 0)
'''.replace("SCRIPT_PATH", script_path)
    (directory / "runner.gd").write_text(runner, encoding="utf-8")
    env = os.environ.copy()
    for variable in ("APPDATA", "LOCALAPPDATA"):
        target = directory / variable.lower()
        target.mkdir()
        env[variable] = str(target)
    run = subprocess.run(
        [str(godot), "--headless", "--path", str(directory),
         "--log-file", str(directory / "godot.log"), "--script", "res://runner.gd"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, timeout=45, check=False)
    output = run.stdout + run.stderr
    print(output.strip())
    errors = [line for line in output.splitlines()
              if any(marker in line for marker in
                     ("SCRIPT ERROR", "ERROR:", "SCRIPT_REFERENCE_FAIL"))
              and line.strip() != "ERROR: Failed to read the root certificate store."]
    assert run.returncode == 0 and not errors, output
    assert "SCRIPT_REFERENCE_RUNTIME=OK" in output, output
    return True


def main():
    addon.register()
    with tempfile.TemporaryDirectory(prefix="gk_script_references_") as temporary:
        directory = Path(temporary)
        tree = bpy.data.node_groups.new(TREE_NAME, "GKGameLogicTree")
        tree.use_fake_user = True
        ready = tree.nodes.new("GKNodeOnReady")
        for kind, name in zip(script_assets.SCRIPT_NODES, NODE_NAMES):
            node = tree.nodes.new(kind)
            node.name = name
            assert node.gk_script_text is None
            prop = node.bl_rna.properties["gk_script_text"]
            assert prop.type == "POINTER" and prop.fixed_type.identifier == "Text"
            assert "gk_script_name" not in node.bl_rna.properties
        load, stop, call = script_nodes(tree)
        load.gk_start_method, stop.gk_stop_method, call.gk_method = "start", "stop", "burst"
        tree.links.new(ready.outputs["执行"], load.inputs["执行"])
        tree.links.new(load.outputs["完成"], call.inputs["执行"])
        tree.links.new(call.outputs["完成"], stop.inputs["执行"])
        print("[PASS] new script nodes use empty Text pointers")

        selected = make_text("B_selected.gd", BODY)
        unrelated = make_text("C_other.gd")
        for node in script_nodes(tree):
            node.gk_script_text = selected
        before = assert_code_and_assets(tree, selected, directory)
        inserted = make_text("A_inserted.gd")
        unrelated.name = "A_renamed.gd"
        assert_selected(tree, selected)
        assert assert_code_and_assets(tree, selected, directory) == before
        bpy.data.texts.remove(inserted)
        assert_selected(tree, selected)
        print("[PASS] unrelated insertion, rename and removal do not change selection")

        selected.name = "改名后的脚本.gd"
        selected_name = selected.name
        assert_selected(tree, selected)
        renamed_code = assert_code_and_assets(tree, selected, directory)
        assert renamed_code != before
        check_open_buttons(tree, selected)
        print("[PASS] selected Text rename updates all generated keys, paths and open buttons")

        copied = tree.copy()
        copied.name = COPY_NAME
        copied.use_fake_user = True
        assert_selected(copied, selected)
        assert_code_and_assets(copied, selected, directory)
        blend_path = directory / "script_references.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
        bpy.ops.wm.open_mainfile(filepath=str(blend_path))
        tree = bpy.data.node_groups[TREE_NAME]
        copied = bpy.data.node_groups[COPY_NAME]
        selected = bpy.data.texts[selected_name]
        assert_selected(tree, selected)
        assert_selected(copied, selected)
        assert assert_code_and_assets(tree, selected, directory) == renamed_code
        check_open_buttons(tree, selected)
        print("[PASS] copied nodes and save/reload preserve the same Text reference")

        runtime = check_export_and_runtime(tree, selected, directory / "godot")
        bpy.data.texts.remove(selected)
        for current in (tree, copied):
            assert all(node.gk_script_text is None for node in script_nodes(current))
        make_text(selected_name, 'extends Node\n# Different replacement script\n')
        for current in (tree, copied):
            assert all(node.gk_script_text is None for node in script_nodes(current))
        code, warnings = codegen.generate(tree, OPTIONS)
        assert any("没选脚本" in warning for warning in warnings), warnings
        assert "_gk_script_load(self," not in code
        assert script_assets.script_files(str(directory)) == []
        print("[PASS] deleting selected Text clears references and never selects a same-name replacement")
        print("SCRIPT_REFERENCES=OK" + (" (Blender + Godot)" if runtime else " (Blender only)"))


if __name__ == "__main__":
    main()
