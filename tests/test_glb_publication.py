"""GLB publication preserves the last model and compares normalized bytes."""
import json
from pathlib import Path
import os
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


def glb_bytes(label="model"):
    body = json.dumps({"asset": {"version": "2.0"}, "extras": {"label": label}}).encode()
    body += b" " * (-len(body) % 4)
    return struct.pack("<III", 0x46546C67, 2, 20 + len(body)) + struct.pack("<II", len(body), 0x4E4F534A) + body


class GLBPublicationTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).parent))
        import gk_stub
        if "bpy" not in sys.modules:
            gk_stub.install()
        if "BlenderGameKit" not in sys.modules:
            gk_stub.load_addon(str(Path(__file__).resolve().parents[1] / "BlenderGameKit"))
        from BlenderGameKit import asset_export
        self.ae = asset_export
        folder = tempfile.TemporaryDirectory(prefix="gk_glb_publish_")
        self.addCleanup(folder.cleanup)
        self.target = Path(folder.name) / "模型.glb"
        self.original = glb_bytes("previous")
        self.target.write_bytes(self.original)
        os.utime(self.target, ns=(1000000000, 1000000000))

    def publish(self, operator, postprocess=None, **kwargs):
        ops = SimpleNamespace(export_scene=SimpleNamespace(gltf=operator))
        with mock.patch.object(sys.modules["bpy"], "ops", ops, create=True):
            # Material-preview suspension is tested in real Blender; this unit
            # exercises the actual publication/retry boundary with a fake encoder.
            result = self.ae._run_gltf_export.__wrapped__(
                dict(kwargs, filepath=str(self.target)), str(self.target), postprocess)
        self.assertEqual(result[0], str(self.target))
        self.assertEqual(list(self.target.parent.glob(".gk_tmp_*")), [])
        return result

    def writer(self, payload):
        def write(**kwargs):
            self.assertNotEqual(kwargs["filepath"], str(self.target))
            Path(kwargs["filepath"]).write_bytes(payload)
            return {"FINISHED"}
        return write

    def assert_previous(self):
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(self.target.stat().st_mtime_ns, 1000000000)

    def test_unchanged_model_keeps_mtime(self):
        with mock.patch.object(self.ae.file_io.os, "replace", side_effect=AssertionError("rewritten")):
            self.assertTrue(self.publish(self.writer(self.original))[1])
        self.assert_previous()

    def test_changed_model_replaces_complete_bytes(self):
        changed = glb_bytes("modified")
        self.assertTrue(self.publish(self.writer(changed))[1])
        self.assertEqual(self.target.read_bytes(), changed)

    def test_cancelled_operator_cannot_claim_existing_model(self):
        self.assertFalse(self.publish(lambda **_: {"CANCELLED"})[1])
        self.assert_previous()

    def test_corrupt_or_truncated_output_keeps_previous_model(self):
        for data in (b"", b"partial", self.original[:-1], b"BAD!" + self.original[4:]):
            with self.subTest(data=data[:12]):
                self.assertFalse(self.publish(self.writer(data))[1])
                self.assert_previous()

    def test_retry_never_reuses_failed_attempt_output(self):
        calls = []
        def operator(**kwargs):
            calls.append(kwargs)
            if "export_animations" in kwargs:
                Path(kwargs["filepath"]).write_bytes(glb_bytes("partial attempt"))
                raise TypeError("export_animations is unsupported")
            return {"FINISHED"}  # Deliberately produces no file.
        self.assertFalse(self.publish(operator, export_animations=True)[1])
        self.assertEqual(len(calls), 2)
        self.assert_previous()

    def test_unsupported_parameter_retry_can_publish(self):
        changed = glb_bytes("new version")
        def operator(**kwargs):
            if "unsupported" in kwargs:
                raise TypeError("unsupported is not recognized")
            return self.writer(changed)(**kwargs)
        self.assertTrue(self.publish(operator, unsupported=True)[1])
        self.assertEqual(self.target.read_bytes(), changed)

    def test_normalization_happens_before_content_comparison(self):
        def normalize(path):
            self.assertNotEqual(path, str(self.target))
            Path(path).write_bytes(self.original)
        self.assertTrue(self.publish(self.writer(glb_bytes("raw")), normalize)[1])
        self.assert_previous()

    def test_failing_animation_option_is_disabled_not_reset_to_default(self):
        attempts = []
        def operator(**kwargs):
            attempts.append(kwargs.get("export_animations", True))
            if kwargs.get("export_animations", True):
                raise RuntimeError("animation exporter failed")
            return self.writer(self.original)(**kwargs)
        notes = getattr(self.ae._run_gltf_export, "_notes", None)
        try:
            self.ae._run_gltf_export._notes = []
            self.assertTrue(self.publish(operator, export_animations=True)[1])
            self.assertEqual(attempts, [True, False])
            self.assert_previous()
        finally:
            if notes is None:
                delattr(self.ae._run_gltf_export, "_notes")
            else:
                self.ae._run_gltf_export._notes = notes

    def test_postprocess_and_replace_failures_keep_previous(self):
        def fail(_):
            raise RuntimeError("normalization failed")
        self.assertFalse(self.publish(self.writer(glb_bytes("raw")), fail)[1])
        self.assert_previous()
        with mock.patch.object(self.ae.file_io.os, "replace", side_effect=OSError("locked")):
            self.assertFalse(self.publish(self.writer(glb_bytes("raw")))[1])
        self.assert_previous()


if __name__ == "__main__":
    unittest.main()
