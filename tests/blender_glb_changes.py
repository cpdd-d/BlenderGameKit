"""Real Blender: unchanged models stay untouched; mesh/Action edits publish."""
import json
import os
from pathlib import Path
import sys
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import BlenderGameKit as addon
from BlenderGameKit import asset_export


def main():
    addon.register()
    bpy.ops.wm.open_mainfile(filepath=str(ROOT / "examples/demo_blueprint_lab/BlueprintLab.blend"))
    scene = bpy.context.scene
    gate = scene.objects["EnergyGateA"]
    out = ROOT / "_dist" / "glb_changes_probe"

    def export():
        path, ok, message = asset_export.export_subtree_glb(str(out), "gate", [gate], scene)
        assert ok, message
        asset_export._validate_glb_output(path)
        return Path(path)

    path = export()
    original = path.read_bytes()
    os.utime(path, ns=(1000000000, 1000000000))
    export()
    assert path.stat().st_mtime_ns == 1000000000, "unchanged GLB rewritten"
    gate.data.vertices[0].co.x += 0.25
    gate.data.update()
    bpy.context.view_layer.update()
    export()
    geometry = path.read_bytes()
    assert geometry != original, "mesh edit was not published"
    scene.frame_set(24)
    gate.location.z += 0.75
    gate.keyframe_insert("location", index=2, frame=24)
    scene.frame_set(1)
    export()
    animated = path.read_bytes()
    assert animated != geometry, "Action edit was not published"
    payload = asset_export.read_glb_payload(path)
    assert payload.get("animations"), "native animation lost during publication"
    gate.shape_key_add(name="Basis")
    morph = gate.shape_key_add(name="Bent")
    morph.data[0].co.x += 0.2
    export()
    with_morph = path.read_bytes()
    scene.gk_settings.gk_skip_morph = True
    export()
    assert path.read_bytes() != with_morph, "export setting change was not published"
    assert not list(out.rglob(".gk_tmp_*")), "temporary model leaked"
    result = dict(unchanged_preserved=True, mesh_change=True, action_change=True,
                  export_setting_change=True, native_animation_preserved=True,
                  temporary_files_cleaned=True)
    (out / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("GLB_CHANGES=OK", json.dumps(result))


if __name__ == "__main__":
    main()
