"""节点声明自检(离线)。

这些约定坏了不会让插件加载失败,只会让某张卡的值被静默忽略 / 菜单里
看不到节点 —— 所以用离线单测锁住。
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import gk_stub  # noqa: E402

gk_stub.install()
ADDON = gk_stub.load_addon(os.path.join(ROOT, "BlenderGameKit"))

from BlenderGameKit.nodes import node_schema  # noqa: E402
from BlenderGameKit.nodes import nodes as NN  # noqa: E402
from BlenderGameKit.nodes.node_base import node_type_registry  # noqa: E402


class TestNodeSchema(unittest.TestCase):
    def test_all_registered_nodes_pass(self):
        issues, count = node_schema.validate_all()
        self.assertEqual(issues, [], "节点声明有问题:\n" + "\n".join(issues))
        self.assertEqual(count, 267,
                         "节点总数变了 —— 新增/删除节点时请同步更新这条断言")

    def test_broken_input_mapping_is_reported(self):
        class Broken(NN.GKNodePrint):
            bl_label = "自检_坏映射"
            gk_input_props = {"不存在的引脚": "gk_text"}

        issues = node_schema.validate_node(Broken)
        self.assertTrue(any("不存在的引脚" in issue for issue in issues), issues)

    def test_duplicate_label_is_reported(self):
        class First(NN.GKNodePrint):
            bl_label = "自检_重名"

        class Second(NN.GKNodePrint):
            bl_label = "自检_重名"

        issues, _count = node_schema.validate_all([First, Second])
        self.assertTrue(any("重复" in issue for issue in issues), issues)


if __name__ == "__main__":
    unittest.main()
