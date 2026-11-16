"""Atomic binary export, unchanged assets, and Blender image state restoration."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import gk_stub
gk_stub.install()
gk_stub.load_addon(str(Path(__file__).resolve().parents[1] / "BlenderGameKit"))
from BlenderGameKit import file_io, sound_assets


class AssetIOTests(unittest.TestCase):
    def test_same_content_ignores_source_timestamp(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "source.wav", Path(folder) / "target.wav"
            source.write_bytes(b"sample")
            target.write_bytes(b"sample")
            os.utime(target, ns=(1000000000, 1000000000))
            with mock.patch.object(file_io.os, "replace", side_effect=AssertionError("rewritten")):
                file_io.copy_if_changed(source, target)
            self.assertEqual(target.stat().st_mtime_ns, 1000000000)

    def test_same_size_and_mtime_do_not_hide_content_change(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "source.wav", Path(folder) / "target.wav"
            source.write_bytes(b"new")
            target.write_bytes(b"old")
            for path in (source, target):
                os.utime(path, ns=(1000000000, 1000000000))
            file_io.copy_if_changed(source, target)
            self.assertEqual(target.read_bytes(), b"new")

    def test_failed_encoder_or_replace_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "texture.png"
            target.write_bytes(b"complete")
            with self.assertRaises(RuntimeError):
                with file_io.staged_output(target) as stage:
                    Path(stage).write_bytes(b"partial")
                    raise RuntimeError("encoder failed")
            with mock.patch.object(file_io.os, "replace", side_effect=OSError("locked")):
                with self.assertRaises(OSError):
                    with file_io.staged_output(target) as stage:
                        Path(stage).write_bytes(b"new")
            self.assertEqual(target.read_bytes(), b"complete")
            self.assertEqual(list(Path(folder).glob(".gk_tmp_*")), [])

    def test_missing_source_cannot_replace_existing_target(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "texture.png"
            target.write_bytes(b"complete")
            with self.assertRaises(FileNotFoundError):
                file_io.copy_if_changed(Path(folder) / "missing", target)
            self.assertEqual(target.read_bytes(), b"complete")

    def test_unchanged_sound_is_still_in_export_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sound.wav"
            source.write_bytes(b"clip")
            project = Path(folder) / "project"
            written, missing = sound_assets.copy_sounds(project, [("音效", str(source))])
            self.assertFalse(missing)
            stamp = (project / written[0]).stat().st_mtime_ns
            self.assertEqual(sound_assets.copy_sounds(project, [("音效", str(source))]), (written, []))
            self.assertEqual((project / written[0]).stat().st_mtime_ns, stamp)

    def test_blend_relative_sound_uses_same_identity_for_script_and_copy(self):
        from BlenderGameKit import blender_adapter
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "sound.wav"
            source.write_bytes(b"clip")
            with mock.patch.object(blender_adapter, "abs_path", return_value=str(source)):
                resource, local = sound_assets.resolve_clip("//sound.wav")
                self.assertEqual(local, str(source))
                self.assertEqual(resource, sound_assets.sound_res_path(str(source)))
                written, missing = sound_assets.copy_sounds(folder, [("相对音效", "//sound.wav")])
            self.assertFalse(missing)
            self.assertEqual(resource, "res://" + written[0])
            self.assertEqual((Path(folder) / written[0]).read_bytes(), b"clip")

    def test_image_png_encoding_and_fallback_restore_source_settings(self):
        from BlenderGameKit import asset_export
        for fallback, fail in ((False, False), (True, False), (True, True)):
            with self.subTest(fallback=fallback, fail=fail), tempfile.TemporaryDirectory() as folder:
                settings = SimpleNamespace(file_format="JPEG")
                context = SimpleNamespace(scene=SimpleNamespace(render=SimpleNamespace(image_settings=settings)))
                image = SimpleNamespace(filepath_raw="//source.jpg", file_format="JPEG")
                target = Path(folder) / "texture.png"
                target.write_bytes(b"previous")

                def render(stage):
                    self.assertEqual(settings.file_format, "PNG")
                    Path(stage).write_bytes(b"partial" if fallback else b"png")
                    if fallback:
                        raise RuntimeError("render unavailable")

                def save():
                    self.assertEqual(image.file_format, "PNG")
                    if fail:
                        raise RuntimeError("save failed")
                    Path(image.filepath_raw).write_bytes(b"png")

                image.save_render, image.save = render, save
                with mock.patch.object(sys.modules["bpy"], "context", context, create=True):
                    if fail:
                        with self.assertRaises(RuntimeError):
                            asset_export._save_image(image, str(target))
                    else:
                        asset_export._save_image(image, str(target))
                self.assertEqual(image.filepath_raw, "//source.jpg")
                self.assertEqual(image.file_format, "JPEG")
                self.assertEqual(settings.file_format, "JPEG")
                self.assertEqual(target.read_bytes(), b"previous" if fail else b"png")
                self.assertFalse(list(Path(folder).glob(".gk_tmp_*")))


if __name__ == "__main__":
    unittest.main()
