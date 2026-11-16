"""Reopen the saved blueprint source and export twice to a different project."""
import json
from pathlib import Path
import re
import sys
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import BlenderGameKit as addon
from BlenderGameKit import asset_export, project


def main():
    addon.register()
    source = ROOT / "examples" / "demo_blueprint_lab" / "BlueprintLab.blend"
    destination = ROOT / "_dist" / "blueprint_roundtrip"
    bpy.ops.wm.open_mainfile(filepath=str(source))
    project.create_project(str(destination), title="Blueprint roundtrip")
    first = asset_export.export_all_scenes(str(destination), dimension="3")
    assert not first.get("error"), first
    assert first["scenes"] and all(s["ok"] for s in first["scenes"]), first
    assert not first.get("warnings"), first["warnings"]
    audio_refs = set()
    for script in (destination / "scripts").glob("*.gd"):
        audio_refs.update(re.findall(r'res://(audio/[^"\s]+)', script.read_text(encoding="utf-8")))
    assert audio_refs, "saved relative audio references were not compiled"
    for ref in audio_refs:
        assert (destination / ref).is_file(), f"compiled audio does not exist: {ref}"
    paths = [destination / ref for ref in audio_refs]
    paths += list((destination / "scripts").glob("game_logic*.gd"))
    models = list((destination / "art").glob("*.glb"))
    assert models, "no world or role models were exported"
    paths += models
    stamps = {str(path): path.stat().st_mtime_ns for path in paths}
    second = asset_export.export_all_scenes(str(destination), dimension="3")
    assert not second.get("error") and all(s["ok"] for s in second["scenes"]), second
    assert stamps == {str(path): path.stat().st_mtime_ns for path in paths}, "unchanged script/audio/model rewritten"
    report = dict(saved_source_reopened=True, relative_audio_present=True,
                  unchanged_files=len(paths), unchanged_models=len(models), warnings=second.get("warnings", []))
    (destination / "roundtrip_result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("BLUEPRINT_ROUNDTRIP=OK", json.dumps(report))


if __name__ == "__main__":
    main()
