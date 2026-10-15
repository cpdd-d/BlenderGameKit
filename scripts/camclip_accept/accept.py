# -*- coding: utf-8 -*-
r"""相机"贴墙跳跃穿模"真机验收(第 126 轮用户问题④:有时跳跃 / 切视角会穿模)。

    python scripts\camclip_accept\accept.py                     # 全跑(无头 + 窗口化)
    python scripts\camclip_accept\accept.py --skip-windowed      # 只无头(快)
    python scripts\camclip_accept\accept.py --levels 走廊
    python scripts\camclip_accept\accept.py --no-baseline        # 不跑"修复前孪生体"

── 验收线(两条,都要过)────────────────────────────────────────────
  ① **进近平面的帧数 = 0**:贴墙跳全程"相机到玩家模型表面的最近距离"必须
     >= 相机自己的 `near`。比 near 更近的面片会被裁掉 + 背面剔除
     -> 看到身体内部,用户说的"穿模"就是这个。
     (示例实测:修复前 0.0539~0.0865 米 / 3~24 帧;修复后 0.54~0.58 米 / 0 帧)
  ② **正常画面不变**:修复前孪生体 vs 修复后,**相机位置的 XZ 分量逐帧相同**
     (只允许 Y 在跳跃那几帧不同),而且贴墙静止的"离模型距离 / 离墙余量"、
     走路稳态臂长都要对得上。这条比"看着对了"强:它证明没顺手改掉水平取景、
     贴墙深度、跟随滞后里的任何一个。

── 为什么要窗口化再跑一遍 ──────────────────────────────────────────
有些相机问题只在窗口化(真渲染)时才现。窗口化那一遍还会在起跳最高点
**拍两张图**(模型开 / 关)做像素差,给出"模型占了屏幕多大一块"这个
肉眼能核对的数字(1/2 picture:相机真贴上去时模型会糊在画面正中)。

跑法细节、真机数字、以及"别把探针目录建到别人的共享目录里"这条规矩,
见同目录 README.md。
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import setup as probe_setup                                       # noqa: E402

WORK = ROOT / "_dist" / "camclip_accept"
GODOT_DEFAULT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"
NEAR_FALLBACK = 0.10

ROW = re.compile(
    r"F (\d+) (\S+) cur=(\S+) py=([-\d.]+) \| "
    r"3:piv=([-\d.]+) ctr=([-\d.]+) aabb=([-\d.]+) in=(-?\d+) body=(-?\d+) dy=([-\d.]+) \| "
    r"1:piv=([-\d.]+) ctr=([-\d.]+) aabb=([-\d.]+) in=(-?\d+) body=(-?\d+) dy=([-\d.]+) \| "
    r"ray=([-\d.]+) (\S+) \| vis=(\d+) ppos=\(([-\d.]+),([-\d.]+),([-\d.]+)\) "
    r"c3=\(([-\d.]+),([-\d.]+),([-\d.]+)\) c1=\(([-\d.]+),([-\d.]+),([-\d.]+)\)"
    r"(?: \| md3=([-\d.]+) md1=([-\d.]+) lp3=\(([-\d.]+),([-\d.]+),([-\d.]+)\))?"
    r"(?: \| pivy=([-\d.]+) camy=([-\d.]+) cl=([-\d.]+) cinC=(-?\d+) cinB=(-?\d+))?")

PROVENANCE = ("LEVEL=", "PLAYER=", "CAM3 ", "CAM1 ", "PLAYER_SHAPE",
              "MODEL_AABB_LOCAL", "MODEL_TRIS", "BOX ", "CEIL ", "CAM_CURRENT")

# ── 第 128 轮的验收线:相机不许跑到"它正上方那个碰撞体"的上面 ──────────
# 只验"进近平面帧数 = 0"会漏掉这一类:站在第三关方块顶上跳时,相机离玩家模型
# 还有 1.43 米(第 1 条全绿),但它整个跑到屋子外面 —— 屏幕上只剩天花板顶面,
# 玩家不见了(用户报的就是这个)。
CEIL_BOTTOM = re.compile(r"CEIL .*?底面 y=([-\d.]+)")


def parse_frames(path: Path):
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line.strip())
        if not m:
            continue
        g = m.groups()
        rows.append({
            "f": int(g[0]), "phase": g[1], "cur": g[2], "py": float(g[3]),
            "piv": float(g[4]), "ctr": float(g[5]), "aabb": float(g[6]),
            "in": int(g[7]), "dy": float(g[9]),
            "piv1": float(g[10]),
            "ray": float(g[16]), "who": g[17], "vis": int(g[18]),
            "ppos": (float(g[19]), float(g[20]), float(g[21])),
            "c3": (float(g[22]), float(g[23]), float(g[24])),
            "md3": float(g[28]) if g[28] is not None else -1.0,
            "camy": float(g[34]) if g[34] is not None else -1.0,
            "cl": float(g[35]) if g[35] is not None else 999.0,
            "cinC": int(g[36]) if g[36] is not None else 0,
        })
    return rows


def run_godot(godot: str, project: Path, scene: str = "", windowed: bool = False,
              shoot: bool = False):
    args = [godot]
    if not windowed:
        args.append("--headless")
    args += ["--path", str(project)]
    if scene:
        args.append(f"res://scenes/{scene}.tscn")
    if shoot:
        args += ["--", "--shoot"]
    done = subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(ROOT))
    return (done.stdout or "") + (done.stderr or "")


def ensure_import(godot: str, project: Path, log) -> None:
    subprocess.run([godot, "--headless", "--path", str(project), "--import"],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace", cwd=str(ROOT))
    log(f"    [import] {project.name}")


def stats_of(project: Path, near: float, ceil_bottom=None):
    rows = parse_frames(project / "camclip_frames.txt")
    if not rows:
        return None
    jump = [r for r in rows if r["phase"] == "E_WALL_JUMP" and r["md3"] > 0]
    wall = [r for r in rows if r["phase"] == "D_WALL_IDLE" and r["md3"] > 0]
    clear = [r["ray"] - r["piv"] for r in wall if r["ray"] > 0]
    walk = [r for r in rows if r["phase"] == "K_WALK_AWAY"]
    steady = []
    for prev, cur in zip(walk, walk[1:]):
        dx = cur["ppos"][0] - prev["ppos"][0]
        dz = cur["ppos"][2] - prev["ppos"][2]
        if (dx * dx + dz * dz) ** 0.5 * 60.0 > 5.0 and cur["ray"] < 0:
            steady.append(cur["piv"])
    # ── 第 128 轮:方块顶场景的两条 ──────────────────────────────────
    # box_rows   = "站方块顶跳"那一段采了多少帧
    # box_above  = 相机跑到**天花板底面以上**的帧数(用户报的穿模就是这个)
    # box_md_min = 那一段相机到模型表面的最近距离(离 near 远不远)
    box = [r for r in rows if r["phase"] == "M_BOX_JUMP"]
    above = 0
    if ceil_bottom is not None:
        above = sum(1 for r in box if r["camy"] > ceil_bottom)
    return {
        "rows": len(rows),
        "jump_min": min(r["md3"] for r in jump) if jump else -1.0,
        "jump_bad": sum(1 for r in jump if 0 < r["md3"] < near and r["vis"] == 1),
        "wall_md": min(r["md3"] for r in wall) if wall else -1.0,
        "wall_clear": min(clear) if clear else -1.0,
        "walk": (sum(steady) / len(steady)) if steady else -1.0,
        "box_rows": len(box),
        "box_min_y": min((r["camy"] for r in box), default=-1.0),
        "box_max_y": max((r["camy"] for r in box), default=-1.0),
        "box_cl_min": min((r["cl"] for r in box), default=999.0),
        "box_above": above,
        "box_md_min": min((r["md3"] for r in box if r["md3"] > 0), default=-1.0),
        # 相机钻进天花板(自己那块实心体)里的帧数
        "cinC": sum(1 for r in box if r["cinC"] == 1),
        "rows_all": rows,
    }


def xz_identical(old: Path, new: Path) -> str:
    a = parse_frames(old / "camclip_frames.txt")
    b = parse_frames(new / "camclip_frames.txt")
    if not a or not b:
        return "缺逐帧表"
    if len(a) != len(b):
        return f"帧数不同 {len(a)} vs {len(b)}"
    bad = sum(1 for ra, rb in zip(a, b)
              if abs(ra["c3"][0] - rb["c3"][0]) > 1e-6
              or abs(ra["c3"][2] - rb["c3"][2]) > 1e-6)
    return f"XZ 不一致的帧 {bad}/{len(a)}"


def pixel_diff(root: Path, label: str, log) -> None:
    """起跳最高点"模型开 / 关"两张图相减 = 模型占了屏幕多大一块。

    `--box` 模式(第 128 轮那一段)没有"模型开/关"这一对(那里看的是**相机
    出没出屋**),所以退一步只报告拍到了哪几张图。
    """
    on = root / "camclip_shots" / "E_apex_model_on.png"
    off = root / "camclip_shots" / "E_apex_model_off.png"
    if not on.exists() or not off.exists():
        shots = sorted(p.name for p in (root / "camclip_shots").glob("*.png")) \
            if (root / "camclip_shots").exists() else []
        log(f"    [{label}] 拍到的图:{shots or '无'}"
            + "(box 场景没有'模型开/关'那一对,不用像素差)")
        return
    try:
        from PIL import Image, ImageChops
    except ImportError:
        log(f"    [{label}] 没装 Pillow,跳过像素差")
        return
    a = Image.open(on).convert("RGB")
    b = Image.open(off).convert("RGB")
    diff = ImageChops.difference(a, b).convert("L")
    w, h = a.size
    data = list(diff.getdata())
    changed = [i for i, v in enumerate(data) if v > 8]
    if not changed:
        log(f"    [{label}] 模型在画面上一点都没画到")
        return
    xs = [i % w for i in changed]
    ys = [i // w for i in changed]
    half = [1 for x, y in zip(xs, ys)
            if abs(x - w // 2) < w // 4 and abs(y - h // 2) < h // 4]
    log(f"    [{label}] 模型占全屏 {100.0 * len(changed) / (w * h):.1f}%,"
        f" 画面正中那一半 {100.0 * len(half) / ((w // 2) * (h // 2)):.1f}%")


def main():
    parser = argparse.ArgumentParser(description="相机穿模真机验收(第 126/128 轮)")
    parser.add_argument("--godot", default=GODOT_DEFAULT)
    parser.add_argument("--levels", default="")
    parser.add_argument("--variants", default="projA,projB")
    parser.add_argument("--skip-windowed", action="store_true",
                        help="只跑无头(快);默认无头 + 窗口化各一遍")
    parser.add_argument("--no-baseline", action="store_true",
                        help="不建/不跑『修复前孪生体』")
    parser.add_argument("--with-trigger", action="store_true",
                        help="连丙(盒子 + Area3D 触发区)一起验")
    parser.add_argument("--box", default="",
                        help="第 128 轮场景:方块节点名(例如 结局箱甲)"
                             "—— 给了它就只跑『站方块顶跳』那一段,默认关卡换 结局")
    parser.add_argument("--ceil", default="",
                        help="第 128 轮场景:天花板节点名(例如 结局天花板)")
    parser.add_argument("--out", default=str(WORK))
    args = parser.parse_args()

    if not os.path.exists(args.godot):
        print(f"跳过:找不到 Godot({args.godot})")
        return 2

    work = Path(args.out)
    box_mode = bool(args.box or args.ceil)
    default_levels = "结局" if box_mode else "大堂,走廊"
    levels = [s.strip() for s in (args.levels or default_levels).split(",")
              if s.strip()]
    variants = [s.strip() for s in args.variants.split(",") if s.strip()]
    lines = []

    def log(text=""):
        print(text)
        lines.append(text)

    log("== 1/3 从最新示例产物重建变体 ==")
    made = probe_setup.build_all(work,
                                 variants=tuple(
                                     v.replace("proj", "") for v in variants)
                                 + (("C",) if args.with_trigger else ()),
                                 baseline=not args.no_baseline)
    for name, path in made.items():
        log(f"    {name} -> {path}")
    if box_mode:
        # 中文节点名走文件传(命令行会被转码,踩过)
        for path in made.values():
            (path / "camclip_args.txt").write_text(
                f"only=box\nbox={args.box}\nceil={args.ceil}\n"
                f"shoot={'1' if not args.skip_windowed else '0'}\n",
                encoding="utf-8")
        log(f"    场景参数:box={args.box} ceil={args.ceil}(写在 camclip_args.txt)")

    near = NEAR_FALLBACK
    stats = {}
    log("== 2/3 无头真机跑(相机逐帧采样)==")
    for name in list(made):
        project = made[name]
        ensure_import(args.godot, project, log)
        for level in levels:
            out = run_godot(args.godot, project, level)
            for probe in PROVENANCE:
                for line in out.splitlines():
                    if line.startswith("CAMCLIP " + probe):
                        log(f"    {line.strip()}")
            m = re.search(r"CAM3 fov=[\d.]+ near=([\d.]+)", out)
            if m:
                near = float(m.group(1))
            ceil_bottom = None
            m2 = CEIL_BOTTOM.search(out)
            if m2:
                ceil_bottom = float(m2.group(1))
            s = stats_of(project, near, ceil_bottom)
            if s is None:
                log(f"    [FAIL] {name}/{level}:没有逐帧表(探针没跑起来?)")
                return 1
            stats[(name, level)] = s
            shutil.copy2(project / "camclip_frames.txt",
                         work / f"frames_{name}_{level}.txt")

    log("")
    log(f"== 验收表(真机 Godot;near = {near})==")
    log(f"{'变体':12s} {'关卡':6s} {'贴墙跳最坏':>10s} {'<near':>6s} "
        f"{'贴墙静止':>9s} {'离墙余量':>8s} {'走路臂长':>8s}")
    for (name, level), s in stats.items():
        log(f"{name:12s} {level:6s} {s['jump_min']:10.4f} {s['jump_bad']:6d} "
            f"{s['wall_md']:9.4f} {s['wall_clear']:8.4f} {s['walk']:8.4f}")

    # ── 第 128 轮那张表(只有 --box 模式才有)─────────────────────────
    if any(s["box_rows"] > 0 for s in stats.values()):
        log("")
        log(f"== 第 128 轮:方块顶跳(near = {near};天花板底面要另行从日志读)==")
        log(f"{'变体':12s} {'帧':>5s} {'相机y min':>10s} {'相机y max':>10s} "
            f"{'在天花板上面':>12s} {'钻天花板':>8s} {'到模型最近':>10s}")
        for (name, level), s in stats.items():
            log(f"{name:12s} {s['box_rows']:5d} {s['box_min_y']:10.4f} "
                f"{s['box_max_y']:10.4f} {s['box_above']:12d} "
                f"{s['cinC']:8d} {s['box_md_min']:10.4f}")

    problems = []
    box_mode = all(stats[key]["box_rows"] == 0 for key in stats) is False
    for name in variants:
        if name not in made:
            continue
        for level in levels:
            s = stats.get((name, level))
            if s is None:
                continue
            # ⚠ 只有跑过"贴墙跳"那条场景时才有这几个数;`--box` 模式下整段
            #   场景不存在,`jump_min` 是 -1 —— 不判空就会**假报 FAIL**
            #   (第 128 轮踩过:方块顶那跑明明全绿,却因为 -1 < near 被判失败)。
            if 0 <= s["jump_min"] < near:
                problems.append(f"{name}/{level}:贴墙跳最近 {s['jump_min']:.4f} "
                                f"< near {near}(会切模型)")
            if s["jump_bad"] > 0:
                problems.append(f"{name}/{level}:{s['jump_bad']} 帧进到近平面里")
            # 第 128 轮的线:相机跑到天花板上面 = 用户说的"站在方块顶上跳穿模"
            if s["box_above"] > 0:
                problems.append(
                    f"{name}/{level}:{s['box_above']} 帧相机在天花板上面"
                    f"(天花板底面被相机越过;camy 最高 {s['box_max_y']:.4f})")
    if not args.no_baseline:
        log("")
        log("== 正常画面变没变:修复前孪生体 vs 修复后 ==")
        for name in variants:
            old, new = f"{name}_old", name
            if old not in made or new not in made:
                continue
            for level in levels:
                if (old, level) not in stats or (new, level) not in stats:
                    continue
                a, b = stats[(old, level)], stats[(new, level)]
                same = xz_identical(made[old], made[new])
                log(f"    {name}/{level}: {same};贴墙静止 "
                    f"{a['wall_md']:.4f}->{b['wall_md']:.4f},离墙余量 "
                    f"{a['wall_clear']:.4f}->{b['wall_clear']:.4f},走路臂长 "
                    f"{a['walk']:.4f}->{b['walk']:.4f}")
                if not same.startswith("XZ 不一致的帧 0/"):
                    problems.append(f"{name}/{level}:水平取景被动到了({same})")
                if abs(a["walk"] - b["walk"]) > 0.05:
                    problems.append(f"{name}/{level}:走路跟随手感变了"
                                    f"({a['walk']:.4f}->{b['walk']:.4f})")

    if not args.skip_windowed:
        log("")
        log("== 3/3 窗口化再跑一遍 + 起跳最高点像素差 ==")
        for name in variants:
            if name not in made:
                continue
            project = made[name]
            shots = project / "camclip_shots"
            if shots.exists():
                shutil.rmtree(shots)
            run_godot(args.godot, project, levels[0], windowed=True, shoot=True)
            shutil.copy2(project / "camclip_frames.txt",
                         work / f"frames_{name}_win_{levels[0]}.txt")
            s = stats_of(project, near)
            if s is not None:
                if s["box_rows"] > 0:
                    log(f"    {name}(窗口化/{levels[0]}):方块顶跳 {s['box_rows']} 帧,"
                        f"相机 y {s['box_min_y']:.4f}~{s['box_max_y']:.4f},"
                        f"在天花板上面 {s['box_above']} 帧")
                else:
                    log(f"    {name}(窗口化/{levels[0]}):贴墙跳最坏 "
                        f"{s['jump_min']:.4f},<near {s['jump_bad']} 帧")
                if s["jump_bad"] > 0 or s["box_above"] > 0:
                    problems.append(f"{name}(窗口化):有帧越界"
                                    f"(近平面 {s['jump_bad']} / 天花板上 "
                                    f"{s['box_above']})")
            pixel_diff(project, name, log)

    log("")
    if problems:
        log("ACCEPT: FAIL")
        for problem in problems:
            log("  - " + problem)
    else:
        log("ACCEPT: PASS(进近平面的帧数 = 0;贴墙静止 / 走路画面未变)")
    report = work / "accept_report.txt"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n报告写到:{report}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
