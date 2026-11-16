"""Execute export/run operators with stub services; failed exports cannot launch."""

import contextlib
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gk_stub


class TestOperatorExportFailures(unittest.TestCase):
    def setUp(self):
        if "bpy" not in sys.modules:
            gk_stub.install()
        if "BlenderGameKit" not in sys.modules:
            gk_stub.load_addon(str(Path(__file__).resolve().parents[1] / "BlenderGameKit"))
        import bpy
        # Import the actual operators. The only extra Blender facility needed at
        # import time is its persistent-handler decorator, not a UI or event loop.
        with mock.patch.object(bpy, "app", NS(handlers=NS(persistent=lambda fn: fn)), create=True):
            from BlenderGameKit import operator_io
        self.io = operator_io
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(operator_io.LAST_EXPORT, {}, clear=True))
        self.scene = NS(objects=[NS(type="MESH")])
        self.context = NS(scene=self.scene)
        self.operator = NS(report=mock.Mock())
        self.directory = "test_project"
        self.messages = self.patch(operator_io, "_report")
        self.patch(operator_io, "bpy", NS(data=NS(filepath="", is_dirty=False)))
        self.patch(operator_io, "_resolve_project", return_value=(self.directory, True))
        self.patch(operator_io.project, "preferences", return_value=None)
        self.record = self.patch(operator_io.export_report, "record")
        self.assets = self.patch(operator_io.export_report, "note_assets")
        self.preview = self.patch(operator_io, "_open_in_text_editor")
        self.patch(operator_io.exporter, "build_settings", return_value={"write_scene": True})
        self.failed = {"ok": False, "stage": "校验逻辑图", "error": "Required argument is missing",
                       "code": "extends Node3D\n", "script_name": "broken.gd", "warnings": [],
                       "diagnostics": [], "written": [], "directory": self.directory}
        self.failed_scenes = {"error": "Required argument is missing", "scenes": [{"ok": False}],
                              "diagnostics": [NS(level="error", message="Bad call")], "written": []}

    def patch(self, owner, name, *args, **kwargs):
        return self.stack.enter_context(mock.patch.object(owner, name, *args, **kwargs))

    def test_script_failure_records_preview_and_never_writes_wrapper(self):
        io = self.io
        self.patch(io, "_tree_of", return_value=NS(name="Broken"))
        self.patch(io.exporter, "export_tree", return_value=self.failed)
        wrapper = self.patch(io.exporter, "write_scene_file")
        project = self.patch(io.exporter, "write_project_file")
        ok, result = io.run_export_script(self.context, self.operator)
        self.assertFalse(ok)
        self.assertIs(result, self.failed)
        self.record.assert_called_once_with(self.scene, self.failed)
        self.preview.assert_called_once_with(self.context, self.failed["code"], "broken.gd")
        wrapper.assert_not_called()
        project.assert_not_called()
        self.assertEqual(io.LAST_EXPORT["error"], self.failed["error"])
        self.assertTrue(any(call.args[1] == "ERROR" for call in self.messages.call_args_list))

    def test_script_operator_returns_cancelled_on_failed_result(self):
        self.patch(self.io, "_tree_of", return_value=NS(name="Broken"))
        self.patch(self.io.exporter, "export_tree", return_value=self.failed)
        wrapper = self.patch(self.io.exporter, "write_scene_file")
        result = self.io.GK_OT_Export().execute(self.context)
        self.assertEqual(result, {"CANCELLED"})
        wrapper.assert_not_called()

    def test_combined_export_stops_if_script_compilation_failed(self):
        self.patch(self.io, "run_export_script", return_value=(False, self.failed))
        assets = self.patch(self.io, "run_export_assets")
        result = self.io.GK_OT_ExportGodot().execute(self.context)
        self.assertEqual(result, {"CANCELLED"})
        assets.assert_not_called()

    def test_asset_export_rechecks_current_graphs_instead_of_reusing_last_name(self):
        io = self.io
        io.LAST_EXPORT.update(script_name="previous.gd")
        self.patch(io, "_export_progress")
        self.patch(io, "_finish_export_progress")
        self.patch(io.asset_export, "collect_objects", return_value=(self.scene.objects, "scene"))
        export = self.patch(io.asset_export, "export_all_scenes", return_value=self.failed_scenes)
        ensure = self.patch(io.asset_export, "ensure_project_file")
        ok, info = io.run_export_assets(self.context, self.operator)
        self.assertFalse(ok)
        self.assertEqual(info["stage"], "pipeline")
        export.assert_called_once_with(self.directory, dimension=None)
        ensure.assert_not_called()
        self.assets.assert_not_called()
        report = self.record.call_args.args[1]
        self.assertFalse(report["ok"])
        self.assertEqual(report["diagnostics"], self.failed_scenes["diagnostics"])

    def test_auto_prepare_uses_all_scene_preflight_and_records_failure(self):
        export = self.patch(self.io.asset_export, "export_all_scenes", return_value=self.failed_scenes)
        arbitrary_script = self.patch(self.io.exporter, "export_tree")
        self.assertIs(self.io._auto_prepare(self.operator, self.context, self.directory), False)
        export.assert_called_once_with(self.directory, dimension=None)
        arbitrary_script.assert_not_called()
        report = self.record.call_args.args[1]
        self.assertFalse(report["ok"])
        self.assertEqual(report["diagnostics"], self.failed_scenes["diagnostics"])
        self.assets.assert_not_called()

    def test_auto_prepare_exception_is_reported_as_failure(self):
        self.patch(self.io.asset_export, "export_all_scenes", side_effect=OSError("Disk unavailable"))
        self.assertIs(self.io._auto_prepare(self.operator, self.context, self.directory), False)
        report = self.record.call_args.args[1]
        self.assertFalse(report["ok"])
        self.assertIn("Disk unavailable", report["error"])

    def test_failed_auto_export_cancels_run_and_packaging_before_launch(self):
        self.patch(self.io.asset_export, "export_all_scenes", return_value=self.failed_scenes)
        launch = self.patch(self.io.project, "open_in_godot")
        executable = self.patch(self.io.project, "find_godot_executable")
        release = self.patch(self.io.packaging, "run_release")
        for kind in (self.io.GK_OT_OpenGodot, self.io.GK_OT_ExportRelease):
            with self.subTest(operator=kind.__name__):
                operator = kind()
                operator.editor = False
                operator.auto_export = True
                self.assertEqual(operator.execute(self.context), {"CANCELLED"})
        launch.assert_not_called()
        executable.assert_not_called()
        release.assert_not_called()

    def test_successful_auto_export_allows_run(self):
        outcome = {"error": None, "scenes": [{"ok": True, "script_name": "valid.gd"}],
                   "written": ["World.tscn"]}
        self.patch(self.io.asset_export, "export_all_scenes", return_value=outcome)
        launch = self.patch(self.io.project, "open_in_godot", return_value=(True, "Started"))
        operator = self.io.GK_OT_OpenGodot()
        operator.editor = False
        operator.auto_export = True
        self.assertEqual(operator.execute(self.context), {"FINISHED"})
        launch.assert_called_once_with(self.directory, self.context, editor=False)
        self.assets.assert_called_once_with(self.scene, self.directory, ["World.tscn"])
        self.assertTrue(self.record.call_args.args[1]["ok"])

    def test_editor_and_explicitly_disabled_auto_export_keep_existing_behavior(self):
        export = self.patch(self.io.asset_export, "export_all_scenes")
        self.patch(self.io.project, "open_in_godot", return_value=(True, "Opened"))
        for editor, automatic in ((True, True), (False, False)):
            with self.subTest(editor=editor, automatic=automatic):
                operator = self.io.GK_OT_OpenGodot()
                operator.editor = editor
                operator.auto_export = automatic
                self.assertEqual(operator.execute(self.context), {"FINISHED"})
        export.assert_not_called()

    def test_unmarked_scene_result_cannot_be_treated_as_success(self):
        self.patch(self.io.asset_export, "export_all_scenes", return_value={"scenes": [{}]})
        self.assertIs(self.io._auto_prepare(self.operator, self.context, self.directory), False)
        self.assertFalse(self.record.call_args.args[1]["ok"])


if __name__ == "__main__":
    unittest.main()
