"""Compiler errors must retain the previous playable export in every host mode."""
import hashlib
import json
from pathlib import Path
import sys
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import BlenderGameKit as addon
from BlenderGameKit import asset_export, exporter, project


def main():
    addon.register()
    bpy.ops.wm.open_mainfile(filepath=str(ROOT / "examples/demo_blueprint_lab/BlueprintLab.blend"))
    scene = bpy.context.scene
    destination = ROOT / "_dist" / "compile_failure_probe"
    project.create_project(str(destination), title="Compile failure preservation")
    baseline = asset_export.export_all_scenes(str(destination), dimension="3")
    assert not baseline.get("error") and all(item["ok"] for item in baseline["scenes"]), baseline
    paths = sorted(path for path in destination.rglob("*")
                   if path.is_file() and path.suffix in {".gd", ".glb", ".tscn", ".wav", ".godot"})

    def snapshot():
        return {str(p.relative_to(destination)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                for p in paths}

    before = snapshot()
    tree = bpy.data.node_groups[scene.gk_settings.logic_tree]
    declaration = tree.gk_funcs.add()
    declaration.func_name = "需要参数"
    declaration.params = "amount:int"
    declaration.return_type = "void"
    define = tree.nodes.new("GKNodeFunctionDefine")
    define.gk_func_name = declaration.func_name
    call = tree.nodes.new("GKNodeCallFunction")
    call.gk_func_name = declaration.func_name
    call.name = "故意省略必需参数"
    ready = next(n for n in tree.nodes if n.bl_idname == "GKNodeOnReady")
    tree.links.new(ready.outputs["执行"], call.inputs["执行"])
    settings = dict(output_dir=str(destination), dimension="3", base_class="Node3D",
                    script_name="game_logic_World_level.gd", per_object_scripts=True)
    direct = exporter.export_tree(tree, settings, write_scene=False)
    assert not direct["ok"] and not direct.get("written"), direct
    assert any(d.code == "INTERFACE_CALL_ARGUMENT_MISMATCH" for d in direct["diagnostics"])
    assert before == snapshot(), "direct failed compilation altered the previous export"
    checks = ["direct_export"]
    hosts = [obj for obj in scene.objects if getattr(obj.gk_physics, "script_target", False)]
    # A valid earlier role would produce new bytes. A later level error must
    # prevent even this earlier script from being published.
    role = bpy.data.node_groups[hosts[0].gk_physics.logic_tree]
    var = role.gk_vars.add()
    var.var_name, var.var_type, var.default_value = "valid_pending_change", "int", "42"
    for mode in ("multi", "single", "level"):
        for i, obj in enumerate(hosts):
            obj.gk_physics.script_target = mode == "multi" or (mode == "single" and i == 0)
        result = asset_export.export_all_scenes(str(destination), dimension="3")
        assert result.get("error") and not result["scenes"][0]["ok"], (mode, result)
        assert not result.get("deleted"), (mode, result.get("deleted"))
        assert any(d.code == "INTERFACE_CALL_ARGUMENT_MISMATCH" for d in result.get("diagnostics", [])), mode
        assert before == snapshot(), f"{mode} failed compilation altered existing export"
        checks.append(mode)
    report = dict(status="OK", checks=checks, preserved_files=len(paths))
    (destination / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("COMPILE_FAILURE=OK", json.dumps(report))


if __name__ == "__main__":
    main()
