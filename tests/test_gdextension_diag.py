"""GDExtension 诊断检查的离线测试(不需要 Blender)。

为什么值得单测:`.gdextension` 里库路径写错时 Godot **静默不加载**
(只表现为"GDScript 里找不到那个类")。诊断里那条检查存在的意义
就是把"静默"变成"能看见",所以检查本身不能也错。

跑法:
    python tests/test_gdextension_diag.py
"""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "BlenderGameKit"))

import gdextension_info  # noqa: E402

VALID = """[configuration]
entry_symbol = "test_init"
compatibility_minimum = "4.5"
reloadable = true

[libraries]
windows.debug.x86_64 = "res://bin/test.windows.template_debug.x86_64.dll"
windows.release.x86_64 = "res://bin/test.windows.template_release.x86_64.dll"
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="gk_ext_diag_")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, relative, text):
        path = os.path.join(self.root, relative)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def touch(self, relative):
        path = os.path.join(self.root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"x")
        return path

    def lines(self):
        return "\n".join(gdextension_info.check(self.root))


class TestResolve(Base):
    def test_res_to_disk(self):
        target = gdextension_info.resolve_library_path(self.root, "res://bin/a.dll")
        self.assertEqual(target, os.path.join(self.root, "bin", "a.dll"))

    def test_quotes_are_stripped(self):
        target = gdextension_info.resolve_library_path(self.root, '"res://bin/a.dll"')
        self.assertEqual(target, os.path.join(self.root, "bin", "a.dll"))


class TestCheck(Base):
    def test_no_extension_is_normal(self):
        text = self.lines()
        self.assertIn("没有 .gdextension", text)
        self.assertNotIn("缺", text)

    def test_valid_project_is_quiet(self):
        self.write("test.gdextension", VALID)
        self.touch("bin/test.windows.template_debug.x86_64.dll")
        self.touch("bin/test.windows.template_release.x86_64.dll")
        text = self.lines()
        self.assertIn("[有]", text)
        self.assertNotIn("缺", text)
        self.assertIn("compatibility_minimum=4.5", text)
        self.assertIn("entry_symbol=test_init", text)

    def test_missing_library_is_reported(self):
        self.write("test.gdextension", VALID)
        self.touch("bin/test.windows.template_debug.x86_64.dll")
        text = self.lines()
        self.assertIn("[缺]", text)
        self.assertIn("不报错", text)

    def test_debug_suffix_mismatch_is_reported(self):
        """最常见的错法:写成 .debug,而产物是 .template_debug。"""
        self.write("test.gdextension", VALID.replace(".template_debug.", ".debug."))
        self.touch("bin/test.windows.template_debug.x86_64.dll")
        text = self.lines()
        self.assertIn("[缺]", text)

    def test_nested_addon_is_found_and_cache_is_skipped(self):
        self.write("addons/demo/demo.gdextension", VALID)
        self.touch("addons/demo/bin/test.windows.template_debug.x86_64.dll")
        self.touch("addons/demo/bin/test.windows.template_release.x86_64.dll")
        self.write(".godot/imported/stale.gdextension", VALID)
        found = gdextension_info.find_declarations(self.root)
        self.assertEqual(len(found), 1)
        self.assertIn("addons", found[0])

    def test_unreadable_file_does_not_crash(self):
        self.write("broken.gdextension", "这不是 ini\n")
        text = self.lines()
        self.assertIn("读不出来", text)

    def test_no_libraries_section_is_reported(self):
        self.write("empty.gdextension",
                   "[configuration]\nentry_symbol = \"x\"\n")
        self.assertIn("不会加载任何库", self.lines())


if __name__ == "__main__":
    unittest.main(verbosity=2)
