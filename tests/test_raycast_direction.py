"""射线『方向』回退的回归测试(不需要 Blender)。

为什么单独一个文件:
    第 166 轮修的是 `input_vector` 的一条回退路径 —— 改 codegen 默认路径时
    只需要跑这一组(几毫秒),不用拉起 test_codegen.py 的 1300+ 用例。

修的是什么(待办清单 ⑧):
    `input_vector` 未连线时**只**认 `gk_x / gk_y / gk_z`;而两个射线节点
    (`GKNodeRaycast`、`GKNodeRaycastCheck`)只声明了 `gk_direction`。
    于是拼出来是 `Vector3(0.0, 0.0, 0.0)`:射线长度为零、永远走『没打中』,
    而且**不报错**。现在未连线时先查 `gk_input_props` 的映射,但只在节点
    没有 gk_x / gk_y / gk_z 时才用(保证其它节点的出码一位不变)。

跑法:
    python tests/test_raycast_direction.py
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import gk_stub  # noqa: E402

gk_stub.install()
gk_stub.load_addon(os.path.join(ROOT, "BlenderGameKit"))

from BlenderGameKit import codegen  # noqa: E402
from BlenderGameKit.nodes import nodes as NN  # noqa: E402

V2 = {"dimension": "2", "base_class": "CharacterBody2D"}
V3 = {"dimension": "3", "base_class": "CharacterBody3D"}


def make_tree():
    return gk_stub.StubNodeTree("测试图")


def gen(tree, options=None):
    return codegen.generate(tree, options)


class TestRaycastDirectionFallback(unittest.TestCase):
    def test_unlinked_direction_uses_card_vector(self):
        """『方向』引脚不接线时，必须用卡片上填的向量。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        node = tree.new_node(NN.GKNodeRaycast, "射线", gk_direction=(0.0, 0.0, 5.0))
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertRegex(
            code, r"var _dir\d+ := \(Vector3\(0\.0, 0\.0, 5\.0\)\)\.normalized\(\)")

    def test_unlinked_direction_2d_drops_third_axis(self):
        """同一份图在 2D 下要出 Vector2，多出来的第三轴不能带进来。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnReady, "准备")
        node = tree.new_node(NN.GKNodeRaycast, "射线", gk_direction=(0.0, 3.0, 9.0))
        gk_stub.connect(tree, event, "执行", node, "执行")
        code, warnings = gen(tree, V2)
        self.assertEqual(warnings, [], warnings)
        self.assertIn("Vector2(0.0, 3.0)", code)
        self.assertNotIn("9.0", code)

    def test_both_raycast_nodes_map_direction_without_xyz(self):
        """两个射线节点都得把『方向』绑到 gk_direction，且不能有 gk_x/y/z。

        这条锁的是"修法依赖的前提":一旦有人给射线加回三件套,
        新的映射分支就会让位,零向量 bug 会静默复发。
        """
        for cls in (NN.GKNodeRaycast, NN.GKNodeRaycastCheck):
            with self.subTest(node=cls.bl_idname):
                self.assertEqual(cls.gk_input_props["方向"], "gk_direction")
                for axis in ("gk_x", "gk_y", "gk_z"):
                    self.assertNotIn(axis, cls.gk_prop_map)

    def test_raycast_check_uses_card_direction(self):
        """『射线_有东西吗』是表达式节点,同一个毛病一起修。"""
        tree = make_tree()
        event = tree.new_node(NN.GKNodeOnProcess, "每帧")
        branch = tree.new_node(NN.GKNodeBranch, "有墙吗")
        check = tree.new_node(NN.GKNodeRaycastCheck, "看前方",
                              gk_direction=(0.0, 0.0, -1.0))
        gk_stub.connect(tree, event, "执行", branch, "执行")
        gk_stub.connect(tree, check, "有东西", branch, "条件")
        code, warnings = gen(tree, V3)
        self.assertEqual(warnings, [], warnings)
        self.assertRegex(code, r"Vector3\(0\.0, 0\.0, -1\.0\)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
