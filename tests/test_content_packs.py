"""资源包 / 数据包导出侧的离线回归测试(不需要 Blender / Godot)。

跑法:
    python tests/test_content_packs.py
    pytest tests/test_content_packs.py

覆盖:
  * 目录包 / zip 包的发现与清单校验;
  * 坏包跳过、重复 id 后者生效;
  * 有包时安装运行时脚本与 autoload,没包时不动工程;
  * 包被删掉后摘掉 autoload(避免 Godot 起不来);
  * 运行时模板里确实带着切换 / 数据查询 / zip 读取的契约。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import gk_stub  # noqa: E402

gk_stub.install()
gk_stub.load_addon(os.path.join(ROOT, "BlenderGameKit"))

from BlenderGameKit import packs  # noqa: E402

PROJECT = 'config_version=5\n[application]\nconfig/name="packs"\n'


def write(path, text, mode="w"):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if "b" in mode:
        if isinstance(text, str):
            text = text.encode("utf-8")
        with open(path, mode) as handle:
            handle.write(text)
    else:
        with open(path, mode, encoding="utf-8", newline="\n") as handle:
            handle.write(text)


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def make_project(directory):
    write(os.path.join(directory, "project.godot"), PROJECT)


def make_resource_pack(directory, name="classic", pack_id="classic"):
    manifest = {
        "id": pack_id,
        "name": "经典材质",
        "overrides": {
            "res://art/grass.png": "overrides/grass.png",
        },
    }
    write(os.path.join(directory, "resourcepacks", name, "pack.json"),
          json.dumps(manifest, ensure_ascii=False))
    write(os.path.join(directory, "resourcepacks", name, "overrides",
                       "grass.png"), "png", mode="wb")


def make_data_pack(directory, name="ruby", pack_id="ruby"):
    manifest = {
        "id": pack_id,
        "name": "红宝石扩展",
        "blocks": [
            {"id": "ruby_block", "name": "红宝石块",
             "texture": "textures/ruby.png", "hardness": 3.0},
        ],
        "world_blocks": [
            {"block": "ruby_block", "scene": "World", "position": [0, 1, 0]},
        ],
    }
    write(os.path.join(directory, "datapacks", name, "pack.json"),
          json.dumps(manifest, ensure_ascii=False))
    write(os.path.join(directory, "datapacks", name, "textures",
                       "ruby.png"), "png", mode="wb")


class PackDiscoveryTests(unittest.TestCase):
    def test_directory_and_zip_packs(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_resource_pack(tmp)
            with zipfile.ZipFile(os.path.join(tmp, "resourcepacks",
                                              "zipped.zip"), "w") as archive:
                archive.writestr("pack.json", json.dumps({
                    "id": "zipped", "name": "压缩包材质",
                    "overrides": {
                        "res://shaders/gk_toon.gdshader": "toon.gdshader"}}))
            make_data_pack(tmp)
            with zipfile.ZipFile(os.path.join(tmp, "datapacks",
                                              "zipped_data.zip"), "w") as arc:
                arc.writestr("pack.json", json.dumps({
                    "id": "zipped_data", "name": "压缩包数据",
                    "blocks": [{"id": "zip_block", "name": "压缩方块"}]}))

            resource, warnings = packs.list_packs(
                tmp, packs.RESOURCE_PACKS_DIR)
            self.assertEqual(warnings, [])
            self.assertEqual([item["id"] for item in resource],
                             ["classic", "zipped"])
            self.assertEqual(resource[0]["kind"], "dir")
            self.assertEqual(resource[1]["kind"], "zip")
            self.assertEqual(resource[0]["count"], 1)

            data, warnings = packs.list_packs(tmp, packs.DATA_PACKS_DIR)
            self.assertEqual(warnings, [])
            self.assertEqual([item["id"] for item in data],
                             ["ruby", "zipped_data"])
            self.assertEqual(data[0]["blocks"][0]["id"], "ruby_block")
            self.assertEqual(len(data[0]["world_blocks"]), 1)
            self.assertEqual(data[1]["kind"], "zip")
            self.assertEqual(data[1]["blocks"][0]["id"], "zip_block")
            self.assertTrue(packs.has_packs(tmp))

    def test_bad_manifests_are_skipped_with_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(os.path.join(tmp, "resourcepacks", "broken", "pack.json"),
                  "{not json")
            write(os.path.join(tmp, "resourcepacks", "empty", "pack.json"),
                  json.dumps({"id": "empty"}))
            write(os.path.join(tmp, "resourcepacks", "bad_target",
                               "pack.json"),
                  json.dumps({"id": "bad_target", "overrides": {
                      "art/grass.png": "grass.png"}}))
            write(os.path.join(tmp, "datapacks", "bad_blocks", "pack.json"),
                  json.dumps({"id": "bad_blocks", "blocks": "nope"}))

            resource, warnings = packs.list_packs(
                tmp, packs.RESOURCE_PACKS_DIR)
            self.assertEqual(resource, [])
            self.assertEqual(len(warnings), 4, warnings)
            self.assertTrue(any("JSON" in item for item in warnings))
            self.assertTrue(any("res://" in item for item in warnings))

            data, warnings = packs.list_packs(tmp, packs.DATA_PACKS_DIR)
            self.assertEqual(data, [])
            self.assertEqual(len(warnings), 1, warnings)

    def test_duplicate_id_last_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_resource_pack(tmp, "first", "same")
            make_resource_pack(tmp, "second", "same")
            found, warnings = packs.list_packs(
                tmp, packs.RESOURCE_PACKS_DIR)
            self.assertEqual([item["id"] for item in found], ["same"])
            self.assertEqual(found[0]["path"].replace("\\", "/"),
                             os.path.join(tmp, "resourcepacks", "second")
                             .replace("\\", "/"))
            self.assertTrue(any("重复" in item for item in warnings))


class PackInstallTests(unittest.TestCase):
    def test_installs_runtime_only_when_packs_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_project(tmp)
            project_file = os.path.join(tmp, "project.godot")
            before = read_text(project_file)

            empty = packs.export_packs(tmp)
            self.assertEqual(empty["written"], [])
            self.assertEqual(empty["warnings"], [])
            self.assertEqual(read_text(project_file), before)
            self.assertFalse(os.path.isdir(os.path.join(tmp, "scripts")))

            make_resource_pack(tmp)
            make_data_pack(tmp)
            result = packs.export_packs(tmp)
            self.assertEqual(result["warnings"], [])
            written = [os.path.basename(path) for path in result["written"]]
            self.assertIn("gk_packs.gd", written)
            self.assertIn("gk_content.gd", written)

            packed = read_text(project_file)
            self.assertIn(
                'GkPacks="*res://scripts/gk_packs.gd"', packed)
            self.assertIn(
                'GkContent="*res://scripts/gk_content.gd"', packed)

            for name in ("gk_packs.gd", "gk_content.gd"):
                text = read_text(os.path.join(tmp, "scripts", name))
                self.assertTrue(text.startswith("# 由 BlenderGameKit 自动生成"),
                                name)

            # 幂等:再装一次不应重复写 autoload
            again = packs.export_packs(tmp)
            self.assertEqual(again["warnings"], [])
            packed_again = read_text(project_file)
            self.assertEqual(packed_again.count("GkPacks="), 1)
            self.assertEqual(packed_again.count("GkContent="), 1)

    def test_removing_packs_removes_autoload(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_project(tmp)
            make_resource_pack(tmp)
            make_data_pack(tmp)
            packs.export_packs(tmp)

            for folder in ("resourcepacks", "datapacks"):
                shutil.rmtree(os.path.join(tmp, folder))

            result = packs.export_packs(tmp)
            self.assertEqual(result["written"], [])
            packed = read_text(os.path.join(tmp, "project.godot"))
            self.assertNotIn("GkPacks", packed)
            self.assertNotIn("GkContent", packed)

    def test_cleanup_does_not_delete_runtime_scripts(self):
        """导出收尾的旧文件清理不能把刚写的运行时当旧文件删掉。"""
        from BlenderGameKit import generated_files

        with tempfile.TemporaryDirectory() as tmp:
            make_project(tmp)
            make_resource_pack(tmp)
            make_data_pack(tmp)
            result = packs.export_packs(tmp)

            deleted = generated_files.cleanup_after_export(
                tmp, result["written"])
            self.assertEqual(deleted, [])
            self.assertTrue(os.path.isfile(
                os.path.join(tmp, "scripts", "gk_packs.gd")))
            self.assertTrue(os.path.isfile(
                os.path.join(tmp, "scripts", "gk_content.gd")))

            # 第二次导出:仍然不该删
            again = packs.export_packs(tmp)
            deleted = generated_files.cleanup_after_export(
                tmp, again["written"])
            self.assertEqual(deleted, [])

            # 包被移除后,下一次导出应把运行时脚本当旧文件清掉
            for folder in ("resourcepacks", "datapacks"):
                shutil.rmtree(os.path.join(tmp, folder))
            gone = packs.export_packs(tmp)
            deleted = generated_files.cleanup_after_export(
                tmp, gone["written"])
            self.assertIn("scripts/gk_packs.gd", deleted)
            self.assertIn("scripts/gk_content.gd", deleted)
            self.assertFalse(os.path.isfile(
                os.path.join(tmp, "scripts", "gk_packs.gd")))

    def test_runtime_templates_keep_their_contract(self):
        resource = packs._RESOURCE_PACK_GD
        self.assertIn("func activate(", resource)
        self.assertIn("func deactivate(", resource)
        self.assertIn("func cycle(", resource)
        self.assertIn("ZIPReader", resource)
        self.assertIn("auto_activate_single", resource)
        self.assertIn("albedo_texture", resource)
        self.assertIn("shader", resource)

        content = packs._CONTENT_GD
        self.assertIn("func get_block(", content)
        self.assertIn("func block_ids(", content)
        self.assertIn("world_blocks", content)
        self.assertIn("func spawn_world_blocks(", content)
        self.assertIn("ZIPReader", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
