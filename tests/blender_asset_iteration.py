"""Real Blender PNG export with JPEG scene settings and repeat-export mtimes."""
from pathlib import Path
import json
import os
import sys
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import BlenderGameKit as addon
from BlenderGameKit import asset_export, sound_assets


def main():
    addon.register()
    out = ROOT / "_dist" / "asset_iteration_probe"
    out.mkdir(parents=True, exist_ok=True)
    image = bpy.data.images.new("IterationImage", width=4, height=4, alpha=True)
    image.filepath_raw = "//original.jpg"
    image.file_format = "JPEG"
    image.pixels = [0.2, 0.4, 0.8, 1.0] * 16
    settings = bpy.context.scene.render.image_settings
    settings.file_format = "JPEG"
    target = out / "image.png"
    asset_export._save_image(image, str(target))
    first = target.read_bytes()
    assert first.startswith(b"\x89PNG\r\n\x1a\n"), "JPEG bytes disguised as PNG"
    os.utime(target, ns=(1000000000, 1000000000))
    asset_export._save_image(image, str(target))
    assert target.stat().st_mtime_ns == 1000000000, "unchanged PNG was rewritten"
    image.pixels = [0.8, 0.1, 0.2, 1.0] * 16
    asset_export._save_image(image, str(target))
    assert target.read_bytes() != first, "edited pixels were not exported"
    assert target.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert settings.file_format == "JPEG"
    assert image.file_format == "JPEG" and image.filepath_raw == "//original.jpg"
    source = ROOT / "examples" / "demo_blueprint_lab" / "activation.wav"
    cards = [("确认音", str(source))]
    sounds, missing = sound_assets.copy_sounds(out, cards)
    assert not missing and len(sounds) == 1
    audio = out / sounds[0]
    os.utime(audio, ns=(1000000000, 1000000000))
    assert sound_assets.copy_sounds(out, cards) == (sounds, [])
    assert audio.stat().st_mtime_ns == 1000000000
    assert not list(out.rglob(".gk_tmp_*"))
    report = dict(png_signature=True, unchanged_png_preserved=True,
                  changed_pixels_exported=True, source_settings_restored=True,
                  unchanged_audio_preserved=True, temporary_files_cleaned=True)
    (out / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("ASSET_ITERATION=OK", json.dumps(report))


if __name__ == "__main__":
    main()
