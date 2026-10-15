# -*- coding: utf-8 -*-
r"""第 126 轮:手动尺寸到底写成什么形状?—— 离线问导出器本身。

    python scripts\camclip_accept\check_manual_shape.py

回答 Lead 那条"顺手确认":手动尺寸 (1.2, 1.8, 1.2) 导出到 Godot 的
`BoxShape3D.size` / `CapsuleShape3D` 是**原样**还是被 Z-up->Y-up 换过轴?

── 为什么要问(本仓库在这里错过一次)──────────────────────────────
`build_collision` 的**自动**分支会做 `Blender(x,y,z) -> Godot(x,z,y)` 换算
(第二十四轮"地面立起来了"就是漏了这一步:20x20 的平面导出成 20 宽 20 高的竖墙)。
手动分支是另一段代码 —— 两段代码写法不同,所以**必须单独量**。
"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))          # scripts/camclip_accept -> 仓库根  # scripts/camclip_accept -> 仓库根
sys.path.insert(0, os.path.join(ROOT, "tests"))

import gk_stub  # noqa: E402

gk_stub.install()
ADDON = gk_stub.load_addon(os.path.join(ROOT, "BlenderGameKit"))


class _Vec:
    """够 `object_bounds(local=True)` 用的一丁点 mathutils.Vector。

    真 Blender 的 mathutils 在这个 python 里没有(它是 Blender 自带的),
    而这段代码在 local=True 分支上只用到:构造、下标、加减、乘标量。
    桩子故意只实现这些 —— 少一个方法就报错,不会悄悄算出别的数。
    """

    def __init__(self, value=(0.0, 0.0, 0.0)):
        self.v = [float(x) for x in value]

    def __getitem__(self, index):
        return self.v[index]

    def __setitem__(self, index, value):
        self.v[index] = float(value)

    def __sub__(self, other):
        return _Vec([a - b for a, b in zip(self.v, other.v)])

    def __add__(self, other):
        return _Vec([a + b for a, b in zip(self.v, other.v)])

    def __mul__(self, scale):
        return _Vec([a * scale for a in self.v])

    __rmul__ = __mul__


_stub_module = types.ModuleType("mathutils")
_stub_module.Vector = _Vec
sys.modules.setdefault("mathutils", _stub_module)

from BlenderGameKit import asset_export as ae  # noqa: E402


class Obj:
    """够 build_collision / object_bounds 用的最小物体桩。"""
    name = "玩家"

    def __init__(self, size, loc=(0.0, 0.0, 0.9), rot=(0.0, 0.0, 0.0)):
        self.type = "MESH"
        # 1x1x1.8 的立方体网格(和示例里的角色同尺寸)
        self.bound_box = [
            (-size[0] / 2, -size[1] / 2, -size[2] / 2),
            (size[0] / 2, -size[1] / 2, -size[2] / 2),
            (size[0] / 2, size[1] / 2, -size[2] / 2),
            (-size[0] / 2, size[1] / 2, -size[2] / 2),
            (-size[0] / 2, -size[1] / 2, size[2] / 2),
            (size[0] / 2, -size[1] / 2, size[2] / 2),
            (size[0] / 2, size[1] / 2, size[2] / 2),
            (-size[0] / 2, size[1] / 2, size[2] / 2),
        ]
        self.location = loc
        self.rotation_euler = rot
        self.scale = (1.0, 1.0, 1.0)
        self.matrix_world = None
        self.children = []
        self.data = types.SimpleNamespace(polygons=[])

    def keys(self):
        return []


def physics(shape, size, trigger=False, body="CharacterBody3D"):
    return types.SimpleNamespace(
        enabled=True, collision_shape=shape, collision_layer="1",
        collision_mask="1", is_trigger=trigger, shape_size=size,
        body_type=body, collision_piece=False, script_target=True)


def show(tag, result):
    if result is None:
        print(f"  {tag}: None(没有碰撞)")
        return
    print(f"  {tag}:")
    print("    " + result["sub_resource"].replace("\n", "\n    "))
    print(f"    node_type={result['node'][:60]!r}")
    print(f"    dims={tuple(round(v, 4) for v in result['dims'])} "
          f"radius={result['radius']:.4f} height={result['height']:.4f} "
          f"offset={tuple(round(v, 4) for v in result['offset'])}")


def main():
    obj = Obj((1.0, 1.0, 1.8))          # Blender x=1 y=1 z=1.8
    print("物体网格 1.0 x 1.0 x 1.8(Blender 轴序 x,y,z)")
    manual = (1.2, 1.8, 1.2)
    show("手动尺寸(1.2,1.8,1.2) + 盒子", ae.build_collision
         (obj, True, physics("box", manual)))
    show("手动尺寸(1.2,1.8,1.2) + 胶囊", ae.build_collision
         (obj, True, physics("capsule", manual)))
    show("自动 + 盒子", ae.build_collision(obj, True, physics("auto", (0, 0, 0))))
    show("自动 + 胶囊", ae.build_collision
         (obj, True, physics("auto", (0, 0, 0))))
    show("自动 + 盒子(手动尺寸全 0 -> 走自动换轴那一支)",
         ae.build_collision(obj, True, physics("box", (0, 0, 0))))
    show("手动尺寸(1.2,1.8,1.2) + 盒子 + 作为触发器",
         ae.build_collision(obj, True, physics("box", manual, trigger=True)))
    print("\n== 期望 ==")
    print("  手动 1.2/1.8/1.2 -> BoxShape3D.size = Vector3(1.2, 1.8, 1.2) 原样")
    print("  自动 1.0x1.0x1.8 -> 盒子应该换轴成 (1.0, 1.8, 1.0)")


if __name__ == "__main__":
    main()
