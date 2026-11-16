"""导出迭代：未变化资源不重写，诊断保留所有可定位来源。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import gk_stub

gk_stub.install()
gk_stub.load_addon(str(Path(__file__).resolve().parents[1] / "BlenderGameKit"))
from BlenderGameKit import export_report, exporter, models


class ExportIterationTests(unittest.TestCase):
    def test_identical_utf8_keeps_timestamp_without_replace(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "脚本.gd"
            exporter.write_text(path, "# 中文\nextends Node\n")
            before = path.stat().st_mtime_ns
            with mock.patch.object(exporter.os, "replace", side_effect=AssertionError("rewritten")):
                self.assertEqual(exporter.write_text(path, "# 中文\nextends Node\n"), path)
            self.assertEqual(path.stat().st_mtime_ns, before)

    def test_changed_same_size_payload_is_written(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "script.gd"
            exporter.write_text(path, "var x = 1\n")
            exporter.write_text(path, "var x = 2\n")
            self.assertEqual(path.read_bytes(), b"var x = 2\n")

    def test_line_endings_are_compared_as_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "script.gd"
            path.write_bytes(b"extends Node\r\n")
            exporter.write_text(path, "extends Node\n")
            self.assertEqual(path.read_bytes(), b"extends Node\n")

    def test_unchanged_file_still_belongs_to_export_transaction(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "script.gd")
            exporter.write_text(path, "extends Node\n")
            transaction = exporter.FileTransaction()
            transaction.write(path, "extends Node\n")
            self.assertEqual(transaction.written, [path])
            transaction.write(path, "extends Node3D\n")
            self.assertEqual(transaction.rollback(), [])
            self.assertEqual(Path(path).read_text(), "extends Node\n")

    def test_shader_copy_second_pass_never_replaces_resources(self):
        from BlenderGameKit import asset_export
        with tempfile.TemporaryDirectory() as folder:
            first = asset_export.copy_shaders(folder)
            self.assertGreater(len(first), 0)
            stamps = {p: os.stat(p).st_mtime_ns for p in first}
            with mock.patch.object(exporter.os, "replace", side_effect=AssertionError("shader rewritten")):
                self.assertEqual(asset_export.copy_shaders(folder), first)
            self.assertEqual(stamps, {p: os.stat(p).st_mtime_ns for p in first})

    def test_failed_atomic_replace_preserves_original(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "script.gd"
            exporter.write_text(path, "extends Node\n")
            with mock.patch.object(exporter.os, "replace", side_effect=OSError("disk unavailable")):
                with self.assertRaises(OSError):
                    exporter.write_text(path, "extends Node3D\n")
            self.assertEqual(path.read_text(), "extends Node\n")
            self.assertEqual(list(Path(folder).glob(".gk_tmp_*")), [])

    def test_diagnostic_without_hint_is_not_lost(self):
        diagnostic = models.Diagnostic(level="error", code="GRAPH", message="断开的执行链", node_name="门")
        result = export_report.build({"diagnostics": [diagnostic], "warnings": [diagnostic.render()]})
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["level"], "error")
        self.assertEqual(result["items"][0]["node_name"], "门")

    def test_same_message_different_nodes_remain_locatable(self):
        diagnostics = [models.Diagnostic(code="GRAPH", message="断开的执行链", hint="连接事件", node_name=name)
                       for name in ("门", "灯")]
        result = export_report.build({"diagnostics": diagnostics + diagnostics,
                                      "warnings": [d.render() for d in diagnostics]})
        self.assertEqual([i["node_name"] for i in result["items"]], ["门", "灯"])


if __name__ == "__main__":
    unittest.main()
