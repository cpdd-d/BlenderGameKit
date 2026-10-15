# -*- coding: utf-8 -*-
r"""第 126 轮:把示例产物做成"相机穿模"真机复验用的几个变体。

    python scripts\camclip_accept\setup.py            # 建到 _dist\camclip_accept\
    python scripts\camclip_accept\setup.py --list     # 只列会建哪些

变体(全部从**仓库里最新的示例产物**拷一份,绝不写 examples/):
  projA      = 现状(甲的碰撞:自动尺寸 -> 胶囊 0.5/1.8)
  projB      = 乙:玩家的手动尺寸 (1.2,1.8,1.2) 盒子(用户截图那套)
  projC      = 丙:乙 + 玩家身上真挂一个 1.2×1.8×1.2 的 Area3D 触发区
               (勾『作为触发器』在场景里的等价物 —— 用来证明它对相机射线毫无影响)
  projA_old  = 甲 + **把修复回退**(删掉自撞兜底与竖直硬跟两行)= 修复前孪生体
  projB_old  = 乙 + 同上

── 为什么非要有"孪生体" ────────────────────────────────────────────
只跑修复后,数字好看也不能证明"只动了该动的那一维" —— 示例产物里同时还有
别人(视角 / AI / 碰撞体)的改动。同一份产物、只回退这两行,拿**逐帧表逐字节**
对比,才能说清"水平取景 / 贴墙深度 / 跟随滞后一个都没变"。

── 为什么要动关卡脚本(第 126 轮踩了很久)────────────────────────────
这个示例是 大堂 -> 走廊 -> 结局 自动串关的:探针刚跑 17 帧场景就被换掉,
走廊开场对话还会暂停游戏、AI 贴上来推玩家 —— 测出来就不是"站在墙边"了。
所以拷贝里只做三件事(相机 / 玩家 / 弹簧臂全是原样):
  1. `GkWorld.change_level` 与 `change_scene_to_file` 变成一句 print;
  2. 摘掉别人留在这个项目里的探针 autoload(踩过:上一轮的 P2Probe 一进来
     就 `change_scene_to_file("走廊")`,还会自己 `quit()`);
  3. 挂上我们的 `CamClipProbe`。
"""
import argparse
import os
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PRODUCT = ROOT / "examples" / "demo_story" / "恐怖闯关示例_godot"
OUT = ROOT / "_dist" / "camclip_accept"
PROBE = HERE / "camclip_probe.gd"

# 玩家场景里那份"自动尺寸"结果(第 125 轮真机口径:胶囊就是网格高)
CAPSULE = ('[sub_resource type="CapsuleShape3D" id="shape"]\n'
           'radius = 0.500000\nheight = 1.800000')
BOX = ('[sub_resource type="BoxShape3D" id="shape"]\n'
       'size = Vector3(1.200000, 1.800000, 1.200000)')
TRIGGER = ('[sub_resource type="BoxShape3D" id="trigshape"]\n'
           'size = Vector3(1.200000, 1.800000, 1.200000)')

# ── "修复前"要把这几处回退掉(第 126 / 128 轮的全部改动)─────────────
# 自动尺寸那组数(1.2,1.8,1.2)是**原样**写进 BoxShape3D.size 的 ——
# 手动分支不做 Z-up -> Y-up 换轴(换轴只发生在"自动"分支)。
# 用导出器本体离线复核过,见 check_manual_shape.py。
#
# ── 为什么孪生体必须回退**全部**改动(第 128 轮踩过)────────────────
# 只回退其中一部分,拿它当"修复前"对照就会**把没回退的那部分算进基线**:
# 第 128 轮我第一版只回退了第 126 轮那两行,而天花板兜底还留在里面 ——
# 于是"修复前"的方块顶场景根本不穿,对照失去意义。
# 加了新修复就往这个列表里加一条,别忘。
REVERT = [
    # ① 自撞兜底(第 126 轮)
    r'\n[ \t]*if not _gk_hit\.is_empty\(\) and _gk_hit\["collider"\] == self:'
    r'\n[ \t]*_gk_hit = \{\}',
    # ② 竖直硬跟(第 126 轮:跳跃时相机不再相对角色下沉)
    r'\n[ \t]*_gk_spring_pos\.y = _gk_want\.y',
    # ③ 天花板兜底(第 128 轮:相机不许跑到头顶那块天花板上面)
    r'\n[ \t]*var _gk_up: Vector3 = [^\n]*'
    r'\n[ \t]*var _gk_up_hit := [^\n]*'
    r'\n[ \t]*if not _gk_up_hit\.is_empty\(\):'
    r'\n[ \t]*var _gk_ceil_y: float = [^\n]*'
    r'\n[ \t]*if _gk_want\.y > _gk_ceil_y:'
    r'\n[ \t]*_gk_want\.y = _gk_ceil_y',
]

CHANGE_SCENE = re.compile(
    r'get_tree\(\)\.(?:call_deferred\(\s*)?"change_scene_to_file"\s*,\s*'
    r'"[^"]+"\s*\)?')
CHANGE_SCENE2 = re.compile(r'get_tree\(\)\.change_scene_to_file\([^)]*\)')


def neutralise_story(dst: Path) -> None:
    """把拷贝里"切关 / 暂停"那条剧情线掐掉(相机、玩家、弹簧臂原样)。"""
    for gd in sorted((dst / "scripts").glob("game_logic_*.gd")):
        text = gd.read_text(encoding="utf-8")
        before = text
        text = CHANGE_SCENE.sub('print("[CAMCLIP] level change blocked")', text)
        text = CHANGE_SCENE2.sub('print("[CAMCLIP] level change blocked")', text)
        if text != before:
            gd.write_text(text, encoding="utf-8")
    world = dst / "scripts" / "gk_world.gd"
    if world.exists():
        text = world.read_text(encoding="utf-8")
        anchor = "func change_level(path: String) -> void:\n"
        if anchor in text and "camclip" not in text:
            text = text.replace(
                anchor,
                anchor
                + '    print("[CAMCLIP] level change blocked: " + path)\n'
                + "    return\n", 1)
            world.write_text(text, encoding="utf-8")


def patch_project(dst: Path) -> None:
    project = dst / "project.godot"
    text = project.read_text(encoding="utf-8")
    anchor = 'GkWorld="*res://scripts/gk_world.gd"'
    if anchor not in text:
        raise SystemExit(f"project.godot 里找不到 GkWorld autoload:{project}")
    # 摘掉**别人**留在这个项目里的探针 autoload(只删那一行,文件留着)
    text = "\n".join(line for line in text.splitlines()
                     if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*="
                                     r'"\*res://(p2_probe|persistent_probe|.*_probe)\.gd"$',
                                     line))
    if "CamClipProbe=" not in text:
        text = text.replace(
            anchor, anchor + '\nCamClipProbe="*res://camclip_probe.gd"')
    project.write_text(text, encoding="utf-8")


def build(name: str, dst_root: Path, box: bool = False, trigger: bool = False,
          revert: bool = False) -> Path:
    if not PRODUCT.exists():
        raise SystemExit(
            f"找不到示例产物:{PRODUCT}\n"
            "先重建示例(Blender 里导出 / scripts\\make_story_demo.py)再跑本脚本。")
    dst = dst_root / name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(PRODUCT, dst)
    shutil.copy2(PROBE, dst / "camclip_probe.gd")
    # ⚠ 删掉示例产物里可能留着的**旧逐帧表 / 旧截图**(第 128 轮踩了半小时):
    # 探针只在**跑完**时才写 `camclip_frames.txt`;万一这一跑中途挂了(脚本报错、
    # 被 --quit-after 掐掉),目录里留着的就是上一跑的数据 —— 复制回来一分析,
    # 会得出"改动没生效"的错误结论(实际读的是旧数)。
    for stale in ("camclip_frames.txt",):
        if (dst / stale).exists():
            (dst / stale).unlink()
    if (dst / "camclip_shots").exists():
        shutil.rmtree(dst / "camclip_shots")
    scene = dst / "scenes" / "GKDefaultPlayer.tscn"
    text = scene.read_text(encoding="utf-8")
    if box:
        if CAPSULE not in text:
            raise SystemExit(
                "玩家场景里没找到 0.5/1.8 胶囊 —— 示例的玩家碰撞变了?"
                "(变体乙的前提是『现状 = 自动尺寸胶囊』)")
        text = text.replace(CAPSULE, BOX)
        if trigger:
            text = text.replace(BOX, BOX + "\n" + TRIGGER)
            text = re.sub(r"\[gd_scene load_steps=(\d+)",
                          lambda m: f"[gd_scene load_steps={int(m.group(1)) + 1}",
                          text)
            nodes = ('[node name="触发区" type="Area3D" parent="."]\n'
                     'collision_layer = 1\ncollision_mask = 1\n\n'
                     '[node name="Collision" type="CollisionShape3D" '
                     'parent="触发区"]\nshape = SubResource("trigshape")\n')
            marker = '[node name="Model" parent="." instance=ExtResource("model")]'
            if marker not in text:
                raise SystemExit("玩家场景里找不到 Model 节点,加不上触发区")
            text = text.replace(marker, nodes + "\n" + marker)
        scene.write_text(text, encoding="utf-8")
    patch_project(dst)
    neutralise_story(dst)
    if revert:
        player = dst / "scripts" / "gk_default_player.gd"
        src = player.read_text(encoding="utf-8")
        total = 0
        for pattern in REVERT:
            src, hits = re.subn(pattern, "", src)
            total += hits
        if total == 0:
            print(f"[warn] {name}:找不到可回退的修复行 —— 跳过孪生体对照"
                  "(camera.py 又改过?更新 setup.py 里的 REVERT)")
            return dst
        player.write_text(src, encoding="utf-8")
    return dst


def build_all(dst_root: Path = OUT, variants=("A", "B", "C"),
              baseline: bool = True) -> dict:
    dst_root.mkdir(parents=True, exist_ok=True)
    made = {}
    if "A" in variants:
        made["projA"] = build("projA", dst_root)
    if "B" in variants:
        made["projB"] = build("projB", dst_root, box=True)
    if "C" in variants:
        made["projC"] = build("projC", dst_root, box=True, trigger=True)
    if baseline:
        if "A" in variants:
            made["projA_old"] = build("projA_old", dst_root, revert=True)
        if "B" in variants:
            made["projB_old"] = build("projB_old", dst_root, box=True,
                                      revert=True)
    return made


def main():
    parser = argparse.ArgumentParser(description="建相机穿模复验用的真机变体")
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--no-baseline", action="store_true",
                        help="不建『修复前孪生体』")
    parser.add_argument("--with-trigger", action="store_true",
                        help="连丙(盒子+Area3D 触发区)一起建")
    args = parser.parse_args()
    if args.list:
        print(f"示例产物:{PRODUCT}")
        print(f"输出目录:{args.out}")
        return
    variants = ("A", "B", "C") if args.with_trigger else ("A", "B")
    made = build_all(Path(args.out), variants, baseline=not args.no_baseline)
    for name, path in made.items():
        print(f"[OK] {name} -> {path}")


if __name__ == "__main__":
    sys.exit(main())
