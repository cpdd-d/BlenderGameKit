"""Multi-host export must preserve each object's script, references and outputs."""

import contextlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gk_stub

class TestMultiRoleExport(unittest.TestCase):
    def setUp(self):
        # unittest imports all test modules before running any case. Importing
        # asset_export at collection time binds its preview helper to that
        # moment's minimal bpy stub, before older tests install their fixtures.
        # Keep product imports in execution and reuse the installed test stub.
        if "bpy" not in sys.modules:
            gk_stub.install()
        if "BlenderGameKit" not in sys.modules:
            gk_stub.load_addon(os.path.join(os.path.dirname(os.path.dirname(__file__)), "BlenderGameKit"))
        from BlenderGameKit import asset_export, exporter
        self.ae, self.ex = asset_export, exporter
        self.temp = tempfile.TemporaryDirectory(prefix="gk_multi_roles_")
        self.addCleanup(self.temp.cleanup)
        self.directory = self.temp.name
        self.shared = NS(name="共享门逻辑")
        self.player_tree = NS(name="玩家逻辑")
        self.level = NS(name="关卡逻辑")
        self.objects = [NS(name=name, type="MESH", children=[],
                           gk_physics=NS(body_type=body, is_trigger=False, enabled=True))
                        for name, body in (("门甲", "StaticBody3D"), ("门乙", "StaticBody3D"),
                                           ("Player", "CharacterBody3D"))]
        self.scene = NS(name="实验室", objects=self.objects)
        self.exports = []

    def run_export(self, fail_name=None, fail_exception=False, pipeline_error=None, reverse=False):
        from BlenderGameKit import persistent_player, packs, render_quality
        from BlenderGameKit.models import Diagnostic
        ae, ex = self.ae, self.ex
        roles = [(obj, self.player_tree if obj.name == "Player" else self.shared, "绑定")
                 for obj in self.objects]
        if reverse:
            roles.reverse()

        def export(tree, settings, write_scene=False):
            settings = dict(settings)
            self.exports.append((tree, settings))
            if fail_name is not None and settings.get("node_name") == fail_name:
                if fail_exception:
                    raise RuntimeError("失败探针")
                return {"ok": False, "error": "失败探针", "script_name": settings["script_name"]}
            if settings.get("dry_run"):
                return {"ok": True, "script_name": settings["script_name"], "written": []}
            path = Path(self.directory) / "scripts" / settings["script_name"]
            path.parent.mkdir(exist_ok=True)
            path.write_text("extends " + settings["base_class"], encoding="utf-8")
            return {"ok": True, "script_name": settings["script_name"], "written": [str(path)],
                    "input_actions": [(settings["script_name"], "E")],
                    "diagnostics": [Diagnostic(message="定位", tree_name=tree.name, node_name="同名节点")]}

        pipeline = mock.Mock(return_value={"error": pipeline_error, "written": ["world.tscn"],
                                          "role_models": {obj.name: obj.name + ".model" for obj in self.objects}})
        cleanup = mock.Mock(return_value=[])
        inputs = mock.Mock()
        with contextlib.ExitStack() as stack:
            for owner, name, value in (
                (ex, "exportable_scenes", lambda: [self.scene]),
                (ex, "resolve_scene_tree", lambda scene: (self.level, "指定")),
                (ex, "object_logic_trees", lambda scene: roles),
                (ex, "object_reference_bindings", lambda obj, scene: {"目标门": obj.name}),
                (ex, "build_settings", lambda scene: {"dimension": "3", "class_name": "SharedClass"}),
                (ex, "export_tree", export),
                (ae, "_activate_scene", lambda scene: None),
                (ae, "_active_scene", lambda: None),
                (ae, "_project_main_scene", lambda directory: ""),
                (ae, "export_assets_pipeline", pipeline),
                (ae, "export_main_ui", lambda directory: {}),
                (ae, "export_player_prefs", lambda directory: {}),
                (persistent_player, "export", lambda directory: {}),
                (persistent_player, "default_player", lambda: None),
                (packs, "export_packs", lambda directory: {}),
                (render_quality, "apply_startup_quality", lambda *args: None),
                (ae.project, "set_main_scene", lambda *args: None),
                (ae.project, "set_input_actions", inputs),
                (ae.generated_files, "cleanup_after_export", cleanup),
            ):
                stack.enter_context(mock.patch.object(owner, name, value))
            result = ae.export_all_scenes(self.directory)
        return result, pipeline, cleanup, inputs

    def test_shared_role_graph_exports_per_object_bindings_and_base_classes(self):
        result, pipeline, cleanup, inputs = self.run_export()
        self.assertIsNone(result["error"], result)
        self.assertTrue(result["scenes"][0]["ok"])
        previews = [settings for _tree, settings in self.exports if settings.get("dry_run")]
        published = [settings for _tree, settings in self.exports if not settings.get("dry_run")]
        self.assertEqual(len(previews), 4)
        self.assertEqual(published, [{k: v for k, v in s.items() if k != "dry_run"} for s in previews])
        role_settings = {settings["node_name"]: settings for tree, settings in self.exports if tree is not self.level}
        self.assertEqual(set(role_settings), {obj.name for obj in self.objects})
        for obj in self.objects:
            self.assertEqual(role_settings[obj.name]["reference_bindings"], {"目标门": obj.name})
            self.assertEqual(role_settings[obj.name]["base_class"], obj.gk_physics.body_type)
            self.assertEqual(role_settings[obj.name]["class_name"], "")
        level = next(settings for tree, settings in self.exports if tree is self.level)
        self.assertEqual(level["base_class"], "Node3D")
        self.assertEqual(level["reference_bindings"], {})
        kwargs = pipeline.call_args.kwargs
        self.assertEqual(set(kwargs["role_scripts"]), set(role_settings))
        self.assertIsNone(kwargs["script_name"])
        self.assertEqual(kwargs["world_script_name"], level["script_name"])
        self.assertEqual(len(inputs.call_args.args[1]), 4)
        self.assertEqual(len(result["diagnostics"]), 4)
        self.assertEqual(len(result["written"]), 5)
        cleanup.assert_called_once()

    def test_unicode_file_names_are_distinct_and_order_independent(self):
        first, _, _, _ = self.run_export()
        first_names = first["scenes"][0]["role_scripts"]
        self.assertNotEqual(first_names["门甲"], first_names["门乙"])
        self.exports.clear()
        second, _, _, _ = self.run_export(reverse=True)
        self.assertEqual(first_names, second["scenes"][0]["role_scripts"])

    def test_failed_script_result_does_not_assemble_or_cleanup(self):
        result, pipeline, cleanup, _ = self.run_export(fail_name="门乙")
        self.assertIn("失败探针", result["error"])
        self.assertFalse(result["scenes"][0]["ok"])
        pipeline.assert_not_called()
        cleanup.assert_not_called()
        self.assertEqual(result["written"], [], "Preflight failures must not publish earlier valid roles")
        self.assertTrue(all(settings.get("dry_run") for _tree, settings in self.exports))

    def test_failed_script_exception_does_not_assemble_or_cleanup(self):
        result, pipeline, cleanup, _ = self.run_export(fail_name="Player", fail_exception=True)
        self.assertIn("失败探针", result["error"])
        self.assertFalse(result["scenes"][0]["ok"])
        pipeline.assert_not_called()
        cleanup.assert_not_called()

    def test_failed_asset_export_does_not_cleanup_any_roles(self):
        result, pipeline, cleanup, _ = self.run_export(pipeline_error="角色模型失败")
        self.assertEqual(result["error"], "角色模型失败")
        self.assertFalse(result["scenes"][0]["ok"])
        cleanup.assert_not_called()

    def test_required_character_capabilities_cannot_change_a_static_hosts_type(self):
        from BlenderGameKit import codegen
        from BlenderGameKit.nodes import nodes as NN
        tree = gk_stub.StubNodeTree("角色图")
        ready = tree.new_node(NN.GKNodeOnReady)
        move = tree.new_node(NN.GKNodeMoveDirection)
        gk_stub.connect(tree, ready, "执行", move, "执行")
        with self.assertRaisesRegex(codegen.CodegenAbort, "类型不匹配"):
            self.ex.generate_code_result(tree, {"base_class": "StaticBody3D", "per_object_scripts": True})

    def test_subtree_export_failure_is_fatal_before_world_export(self):
        ae = self.ae
        obj = self.objects[0]
        scripts = {obj.name: "role.gd"}
        raw_pipeline = ae.export_assets_pipeline.__wrapped__
        with mock.patch.object(ae, "build_collision", return_value={"shape": "BOX"}), \
             mock.patch.object(ae, "collect_physics", return_value=[]), \
             mock.patch.object(ae, "export_subtree_glb", return_value=(None, False, "模型损坏")), \
             mock.patch.object(ae, "export_scene_glb") as world:
            result = raw_pipeline(self.scene, self.directory, role_scripts=scripts)
        self.assertIn("模型损坏", result["error"])
        world.assert_not_called()


if __name__ == "__main__":
    unittest.main()
