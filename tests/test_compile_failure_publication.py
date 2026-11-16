"""Compile errors must stop publication before replacing a working project."""

import contextlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gk_stub


class TestCompileFailurePublication(unittest.TestCase):
    def setUp(self):
        if "bpy" not in sys.modules:
            gk_stub.install()
        if "BlenderGameKit" not in sys.modules:
            gk_stub.load_addon(str(Path(__file__).resolve().parents[1] / "BlenderGameKit"))
        from BlenderGameKit import asset_export, exporter
        from BlenderGameKit.models import Diagnostic
        self.ae, self.ex, self.Diagnostic = asset_export, exporter, Diagnostic
        self.temp = tempfile.TemporaryDirectory(prefix="gk_compile_guard_")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.tree = NS(name="Level", gk_scope="LEVEL", is_property_set=lambda name: True)
        self.role = NS(name="Door")
        self.scene = NS(name="Lab", objects=[], role_trees=[], level=self.tree)
        self.old = {}
        for relative, content in (
            ("scripts/game_logic.gd", b"# previous valid script\nextends Node3D\n"),
            ("scenes/World.tscn", b"[gd_scene format=3]\n"),
            ("models/World.glb", b"previous valid asset"),
            ("project.godot", b"config_version=5\n[application]\nrun/main_scene=\"res://scenes/World.tscn\"\n"),
            (".gk_generated_files.json", b"[\"scripts/game_logic.gd\"]"),
        ):
            path = self.directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            self.old[path] = (content, path.stat().st_mtime_ns)

    def assert_old_unchanged(self):
        for path, (content, modified) in self.old.items():
            self.assertTrue(path.is_file(), str(path))
            self.assertEqual(path.read_bytes(), content, str(path))
            self.assertEqual(path.stat().st_mtime_ns, modified, str(path))
        self.assertEqual({p for p in self.directory.rglob("*") if p.is_file()}, set(self.old))

    def diagnostic(self, level="error", tree=None):
        return self.Diagnostic(level=level, code="INTERFACE_CALL_ARGUMENT_MISMATCH",
                               message="Required argument is missing", tree_name=(tree or self.tree).name,
                               node_name="CallDoor")

    def generated(self, tree, settings, diagnostic=None):
        return ("extends Node3D\nvar changed = true\n", [diagnostic.render()] if diagnostic else [],
                settings.get("script_name") or "game_logic.gd", [],
                [diagnostic] if diagnostic else [], {})

    def test_error_stops_before_directory_and_resource_planning(self):
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run), contextlib.ExitStack() as stack:
                error = self.diagnostic()
                stack.enter_context(mock.patch.object(self.ex, "_generate_code_full", return_value=
                                                       self.generated(self.tree, {}, error)))
                directory_check = stack.enter_context(mock.patch.object(self.ex, "validate_output_dir"))
                resources = stack.enter_context(mock.patch.object(self.ex.script_assets, "script_files"))
                result = self.ex.export_tree(self.tree, {"output_dir": str(self.directory), "dry_run": dry_run})
                self.assertIs(result["ok"], False)
                self.assertEqual(result["stage"], "校验逻辑图")
                self.assertEqual(result["diagnostics"], [error])
                self.assertEqual(result["written"], [])
                self.assertEqual(result["manifest"], [])
                self.assertIn("var changed", result["code"])
                directory_check.assert_not_called()
                resources.assert_not_called()
                self.assert_old_unchanged()

    def test_warning_remains_publishable(self):
        warning = self.diagnostic("warning")
        with mock.patch.object(self.ex, "_generate_code_full", return_value=self.generated(self.tree, {}, warning)), \
             mock.patch.object(self.ex, "_sound_asset_cards", return_value=[]), \
             mock.patch.object(self.ex.script_assets, "script_files", return_value=[]), \
             mock.patch.object(self.ex.blender_adapter, "scene", return_value=None):
            result = self.ex.export_tree(self.tree, {"output_dir": str(self.directory)}, write_scene=False)
        self.assertIs(result["ok"], True)
        self.assertIn(warning, result["diagnostics"])
        self.assertIn("var changed", (self.directory / "scripts/game_logic.gd").read_text())

    def add_role(self, name="DoorA", scene=None):
        scene = scene or self.scene
        obj = NS(name=name, type="MESH", children=[],
                 gk_physics=NS(enabled=True, body_type="StaticBody3D", is_trigger=False))
        scene.objects.append(obj)
        scene.role_trees.append((obj, self.role, "Explicit"))

    def run_scenes(self, fail_tree=None, *, scenes=None, failure="diagnostic"):
        from BlenderGameKit import persistent_player, packs, render_quality
        scenes = scenes or [self.scene]
        ae, ex = self.ae, self.ex
        calls = []
        error = self.diagnostic(tree=fail_tree) if fail_tree is not None else None

        def generate(tree, settings):
            calls.append((tree, dict(settings)))
            if tree is fail_tree and (failure != "publish" or not settings.get("dry_run")):
                if failure == "exception":
                    raise RuntimeError("Compilation interrupted")
                return self.generated(tree, settings, error)
            return self.generated(tree, settings)

        pipeline = mock.Mock(return_value={"written": [], "error": None})
        cleanup = mock.Mock(return_value=[])
        player = mock.Mock(return_value={})
        main_ui = mock.Mock(return_value={})
        with contextlib.ExitStack() as stack:
            for owner, name, value in (
                (ex, "exportable_scenes", lambda: scenes),
                (ex, "resolve_scene_tree", lambda scene: (scene.level, "Explicit")),
                (ex, "object_logic_trees", lambda scene: scene.role_trees),
                (ex, "object_reference_bindings", lambda obj, scene: {"Door": obj.name}),
                (ex, "resolve_base_class", lambda scene: "StaticBody3D"),
                (ex, "build_settings", lambda scene: {"dimension": "3", "class_name": ""}),
                (ex, "_generate_code_full", generate),
                (ex, "_sound_asset_cards", lambda: []),
                (ex.script_assets, "script_files", lambda directory: []),
                (ex.blender_adapter, "scene", lambda: None),
                (ae, "_activate_scene", mock.Mock()),
                (ae, "_active_scene", lambda: self.scene),
                (ae, "export_assets_pipeline", pipeline),
                (ae, "export_main_ui", main_ui),
                (ae, "export_player_prefs", lambda directory: {}),
                (persistent_player, "export", player),
                (persistent_player, "default_player", lambda: None),
                (packs, "export_packs", lambda directory: {}),
                (render_quality, "apply_startup_quality", lambda *args: None),
                (ae.project, "set_main_scene", lambda *args: None),
                (ae.project, "set_input_actions", lambda *args: None),
                (ae.generated_files, "cleanup_after_export", cleanup),
            ):
                stack.enter_context(mock.patch.object(owner, name, value))
            result = ae.export_all_scenes(str(self.directory))
        return result, calls, pipeline, cleanup, player, main_ui, error

    def assert_preflight_failure(self, result):
        outcome, calls, pipeline, cleanup, player, main_ui, diagnostic = result
        self.assertTrue(outcome["error"], outcome)
        self.assertTrue(all(not scene["ok"] for scene in outcome["scenes"]))
        self.assertEqual(outcome["written"], [])
        self.assertEqual(outcome["deleted"], [])
        self.assertTrue(all(options.get("dry_run") for tree, options in calls))
        self.assertIn(diagnostic, outcome["diagnostics"])
        pipeline.assert_not_called()
        cleanup.assert_not_called()
        player.assert_not_called()
        main_ui.assert_not_called()
        self.assert_old_unchanged()

    def test_level_error_prevents_assets_and_cleanup(self):
        self.assert_preflight_failure(self.run_scenes(self.tree))

    def test_single_role_error_prevents_assets_and_cleanup(self):
        self.add_role()
        self.scene.level = None
        self.assert_preflight_failure(self.run_scenes(self.role))

    def test_mixed_role_error_prevents_level_publication(self):
        self.add_role()
        result = self.run_scenes(self.role)
        self.assert_preflight_failure(result)
        self.assertTrue(all(tree is self.role for tree, _options in result[1]))

    def test_mixed_level_error_preserves_earlier_valid_role(self):
        self.add_role()
        self.assert_preflight_failure(self.run_scenes(self.tree))

    def test_multi_role_level_error_preserves_all_earlier_valid_roles(self):
        self.add_role()
        self.add_role("DoorB")
        self.assert_preflight_failure(self.run_scenes(self.tree))

    def test_later_scene_error_prevents_first_scene_publication(self):
        later = NS(name="Later", objects=[], role_trees=[], level=NS(name="BadLevel"))
        self.assert_preflight_failure(self.run_scenes(later.level, scenes=[self.scene, later]))

    def test_success_reuses_exact_preflight_options_for_publication(self):
        self.add_role()
        self.add_role("DoorB")
        outcome, calls, pipeline, cleanup, player, _main_ui, _diagnostic = self.run_scenes()
        self.assertIsNone(outcome["error"])
        self.assertTrue(outcome["scenes"][0]["ok"])
        previews = [(tree, {k: v for k, v in options.items() if k != "dry_run"})
                    for tree, options in calls if options.get("dry_run")]
        published = [(tree, {k: v for k, v in options.items() if k != "dry_run"})
                     for tree, options in calls if not options.get("dry_run")]
        self.assertEqual(previews, published)
        self.assertEqual(len(previews), 3)
        pipeline.assert_called_once()
        cleanup.assert_called_once()
        player.assert_called_once()

    def test_failure_during_publication_is_still_fatal(self):
        outcome, calls, pipeline, cleanup, _player, _ui, error = self.run_scenes(self.tree, failure="publish")
        self.assertTrue(any(not options.get("dry_run") for _tree, options in calls))
        self.assertTrue(outcome["error"])
        self.assertFalse(outcome["scenes"][0]["ok"])
        self.assertIn(error, outcome["diagnostics"])
        pipeline.assert_not_called()
        cleanup.assert_not_called()
        self.assert_old_unchanged()

    def test_compile_exception_is_failure_in_single_role_path(self):
        self.add_role()
        self.scene.level = None
        outcome, _calls, pipeline, cleanup, player, _ui, _error = self.run_scenes(self.role, failure="exception")
        self.assertIn("Compilation interrupted", outcome["error"])
        self.assertFalse(outcome["scenes"][0]["ok"])
        pipeline.assert_not_called()
        cleanup.assert_not_called()
        player.assert_not_called()
        self.assert_old_unchanged()


if __name__ == "__main__":
    unittest.main()
