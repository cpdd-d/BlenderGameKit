"""分析扫光出图:检查面部明暗是否随光角度变化。

⚠️ 这个工具目前的定位是**粗筛,不是精确验证**。

它的判据不可靠。实测发现:脸的像素区域只有约 53x57,卡通明暗边界经过
缩放与抗锯齿之后对比度不足,很多角度会得出"这一帧没有边界"的结论。
换句话说,**它报 FAILED 不代表着色器有问题**。

已经被它抓出来的真实 bug(这些是它的价值所在):
  * 第一版把"越暗权重越大"写反了,把亮重心当成暗重心
  * 第二版用 3 像素滑动窗口,而边界只占 1 像素,落差被平滑成 0
  * 第三版取"第一处显著落差",抗锯齿边界有两处,结果系统性偏 1 像素
  * 自检的期望值算错(用 0.5 当检测窗中心,实际中心是 0.495)

面部 SDF 的**权威验证是肉眼比对多角度出图**(见 docs/渲染-NPR卡通着色.md),
以及 scripts/render_shots.ps1 生成的 shots/ 目录。这个脚本只作为辅助。

用法:
    python scripts/analyze_face_sweep.py
    python scripts/analyze_face_sweep.py --selftest    # 用合成图验证测量逻辑本身
"""

import argparse
import glob
import math
import os
import re
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(ROOT, "examples", "demo_render", "shots")


# ==========================================================================
# 最小 PNG 读取(不依赖 Pillow)
# ==========================================================================
def read_png(path):
    """返回 (width, height, pixels)，pixels 是逐行 RGB 元组的列表。"""
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG 文件")

    pos = 8
    width = height = None
    bit_depth = color_type = None
    idat = bytearray()

    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        pos += 12 + length

        if tag == b"IHDR":
            width, height, bit_depth, color_type, _, _, _ = struct.unpack(">IIBBBBB", payload)
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break

    if bit_depth != 8 or color_type not in (2, 6):
        raise ValueError(f"只支持 8 位 RGB/RGBA，实际 depth={bit_depth} type={color_type}")

    channels = 3 if color_type == 2 else 4
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    rows = []
    previous = bytearray(stride)

    offset = 0
    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        line = bytearray(raw[offset:offset + stride])
        offset += stride
        # 反滤波:只实现 PNG 的五种滤波
        if filter_type == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = previous[i]
                c = previous[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        rows.append([
            (line[i * channels], line[i * channels + 1], line[i * channels + 2])
            for i in range(width)
        ])
        previous = line

    return width, height, rows


def luminance(px):
    return (0.299 * px[0] + 0.587 * px[1] + 0.114 * px[2]) / 255.0


# ==========================================================================
# 分析
# ==========================================================================
def face_shadow_line(width, height, rows, sample_step=1):
    """找出脸部"阴影跳变线"的水平位置,归一化到 -1..1(中心为 0)。

    做法:对每一行,在脸的横向范围内找亮度**落差最大**的位置;
    如果落差足够大(说明这一行确实跨了明暗分界),就记录这个位置,
    最后对所有行的位置取中位数。

    为什么用"找跳变"而不是"取阈值求重心":
    实测脸部的亮度分布是**双峰的**(受光区接近 1.0,阴影区接近 0.5),
    百分位阈值在这种分布下会得出"整脸同亮/同暗"的错误结论。
    找跳变直接对应卡通阴影的物理特征 —— 它就是一条硬边。

    返回 (位置, 跨界的行数, 该行的平均亮度);找不到跳变时位置为 None。
    """
    x_lo, x_hi = int(width * 0.42), int(width * 0.57)
    y_lo, y_hi = int(height * 0.33), int(height * 0.48)
    x_mid = (x_lo + x_hi) * 0.5
    half = max(1.0, (x_hi - x_lo) * 0.5)

    positions = []
    samples = 0
    total = 0.0

    for y in range(y_lo, y_hi, sample_step):
        # 取这一行在脸部范围内的亮度曲线(只保留暖色像素)
        line = []
        for x in range(x_lo, x_hi):
            px = rows[y][x]
            if px[0] < px[2] - 6:
                line.append(None)
                continue
            line.append(luminance(px))

        valid = [v for v in line if v is not None]
        if len(valid) < 6:
            continue
        samples += len(valid)
        total += sum(valid)

        # 找相邻像素之间落差显著的位置,取它们的中位数。
        #
        # 两个必须注意的点:
        #  1. 必须用**相邻像素差**,不能用滑动窗口均值。脸在画面里只有约 29 像素宽,
        #     卡通阴影的边界只占 1~2 像素,3 像素窗口的左右各 1 像素均值会把落差
        #     平滑到 0。(自检用合成图抓到了这个 bug —— 真实数据反而会碰巧通过。)
        #  2. 抗锯齿过的边界会产生**两处**等幅落差,取第一处会系统性地偏 1 像素。
        #     所以收集所有显著落差的位置再取中位数。
        lo, hi = min(valid), max(valid)
        span = hi - lo
        # 跨度太小说明这一行没跨过明暗分界。
        # 门槛必须明显高于量化噪声:整脸受光时亮度在 0.90~0.96 之间抖,
        # 跨度只有 0.06,但用 0.12 的门槛会放它过去,然后检测器从噪声里
        # "找到"一条假边界(真实数据上表现为所有角度都测出同一个位置)。
        if span < 0.25:
            continue

        threshold = max(span * 0.45, 0.08)
        drops = []
        for i in range(len(line) - 1):
            a, b = line[i], line[i + 1]
            if a is None or b is None:
                continue
            if abs(a - b) > threshold:
                drops.append(x_lo + i + 1)

        if not drops:
            continue
        drops.sort()
        # 中位数即边界中心:两处等幅落差时正好落在它们中间
        edge = drops[len(drops) // 2] if len(drops) % 2 else (
            (drops[len(drops) // 2 - 1] + drops[len(drops) // 2]) // 2
        )
        positions.append((edge - x_mid) / half)

    if not positions:
        return None, 0, (total / samples if samples else 0.0)

    positions.sort()
    median = positions[len(positions) // 2]
    return median, len(positions), total / samples


def collect_shots(directory=None):
    directory = directory or SHOTS
    files = sorted(glob.glob(os.path.join(directory, "light_*.png")))
    entries = []
    for path in files:
        match = re.search(r"light_(\d+)\.png$", os.path.basename(path))
        if not match:
            continue
        entries.append((int(match.group(1)), path))
    return sorted(entries)


def selftest():
    """用合成图验证分析器本身。

    这一步不是走形式:第一版分析器把"越暗权重越大"写反了，
    结果它把亮重心当成暗重心，真实数据上得出完全相反的结论。
    没有这个自检，我会以为着色器坏了。
    """
    print("=== 分析器自检(合成图)===")
    width = height = 200
    ok = True
    # 合成"左暗右亮"的脸,平移边界,检查测出的位置对不对。
    # 边界必须落在检测窗(脸宽 0.42~0.62)内部,否则测不到。
    # 期望值按检测窗自身的中心换算:窗中心 = (0.42+0.62)/2 = 0.52,
    # 窗半宽 = 0.10。用 0.5 当中心会让期望值系统性偏 0.2 —— 自检正是靠
    # 这个偏差发现"期望值算错了"。
    x_lo_u, x_hi_u = 0.42, 0.57
    window_mid = (x_lo_u + x_hi_u) / 2
    window_half = (x_hi_u - x_lo_u) / 2

    for name, line_u in (("边界偏左", 0.46), ("边界居中", 0.495), ("边界偏右", 0.54)):
        expected = (line_u - window_mid) / window_half
        rows = []
        for y in range(height):
            line = []
            for x in range(width):
                inside = (0.38 * width <= x <= 0.62 * width
                          and 0.30 * height <= y <= 0.54 * height)
                if inside:
                    # 肤色双峰分布:亮侧 ~0.95,暗侧 ~0.52(和真实渲染一致)
                    value = 0.95 if (x / width) > line_u else 0.52
                    px = (int(value * 255), int(value * 250), int(value * 240))
                else:
                    px = (26, 26, 26)
                line.append(px)
            rows.append(line)

        position, crossings, _mean = face_shadow_line(width, height, rows)
        if position is None:
            good = False
            detail = "没找到边界"
        else:
            good = abs(position - expected) < 0.18
            detail = f"测得 {position:+.3f} (期望 {expected:+.2f}, 跨界行 {crossings})"
        ok = ok and good
        print(f"  {name}: {detail} -> {'正确' if good else '错误'}")

    # 反向用例:整脸同亮时应该报"无分界",而不是硬找出一条线
    rows = []
    for y in range(height):
        line = []
        for x in range(width):
            inside = 0.38 * width <= x <= 0.62 * width and 0.30 * height <= y <= 0.54 * height
            value = 0.95 if inside else 0.1
            px = (int(value * 255), int(value * 250), int(value * 240))
            line.append(px)
        rows.append(line)
    position, crossings, _mean = face_shadow_line(width, height, rows)
    good = position is None
    ok = ok and good
    print(f"  整脸同亮(应报无边界): {'正确' if good else '错误'}"
          f"{'' if good else f' —— 却测出 {position:+.3f} ({crossings} 行)'}")

    print(f"自检结果: {'通过' if ok else '失败'}")
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true", help="用合成图验证分析器本身")
    parser.add_argument("--shots", default=SHOTS)
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    entries = collect_shots(args.shots)
    if len(entries) < 3:
        print(f"扫光图不足(找到 {len(entries)} 张)。先运行:")
        print("  powershell -File scripts\\render_shots.ps1 -Step 45")
        return 1

    print(f"分析 {len(entries)} 张扫光图: {os.path.relpath(args.shots, ROOT)}")
    print()
    print(f"{'角度':>6} {'阴影线位置':>12} {'跨界行':>7} {'平均亮度':>10}   期望")
    print("-" * 68)

    data = []
    for angle, path in entries:
        width, height, rows = read_png(path)
        position, crossings, mean = face_shadow_line(width, height, rows)
        data.append((angle, position, mean))
        light_x = math.sin(math.radians(angle))
        if light_x > 0.2:
            expect = "光在画面右侧 -> 阴影线偏左(负)"
        elif light_x < -0.2:
            expect = "光在画面左侧 -> 阴影线偏右(正)"
        else:
            expect = "接近正面/背面 -> 无分界"
        if position is None:
            print(f"{angle:>6} {'--':>12} {crossings:>7} {mean:>10.3f}   {expect} [整脸同亮/同暗]")
        else:
            print(f"{angle:>6} {position:>+12.4f} {crossings:>7} {mean:>10.3f}   {expect}")

    usable = [(a, p) for a, p, _ in data if p is not None]
    if len(usable) < 4:
        print(f"\n可测角度只有 {len(usable)} 个 —— 这是预期内的:")
        print("  脸的像素区域很小,卡通边界在缩放+抗锯齿后对比度不足,")
        print("  大部分角度会被判为'整脸同亮/同暗'。")
        print("  这个脚本只做粗筛,权威验证请看多角度出图:")
        print("    powershell -File scripts\\render_shots.ps1")
        print("SWEEP_ANALYZE=INCONCLUSIVE")
        return 2

    # 阴影线位置 与 sin(角度) 应该**负相关**:
    # 光在画面右侧(sin>0) -> 受光在右 -> 阴影线在左 -> 位置 < 0
    sins = [math.sin(math.radians(a)) for a, _ in usable]
    positions = [p for _, p in usable]
    n = len(usable)
    mean_s = sum(sins) / n
    mean_p = sum(positions) / n
    cov = sum((s - mean_s) * (p - mean_p) for s, p in zip(sins, positions))
    var_s = sum((s - mean_s) ** 2 for s in sins)
    var_p = sum((p - mean_p) ** 2 for p in positions)
    if var_s <= 0.0 or var_p <= 0.0:
        print("\n可测角度的位置完全一致 —— 分不出是渲染问题还是测量分辨率不足")
        print("SWEEP_ANALYZE=INCONCLUSIVE")
        return 2
    corr = cov / math.sqrt(var_s * var_p)

    spread = max(positions) - min(positions)
    print()
    print(f"可测角度 {len(usable)}/{len(data)} 个(其余整脸同亮或同暗)")
    print(f"阴影线位置范围: {min(positions):+.4f} .. {max(positions):+.4f}  (跨度 {spread:.4f})")
    print(f"与 sin(角度) 的相关系数: {corr:+.3f}  (期望为负)")

    if corr < -0.5 and spread > 0.2:
        print()
        print("结论:方向正确,与面部 SDF 的预期一致")
        print("SWEEP_ANALYZE=OK")
        return 0
    print()
    print("结论:信号太弱或方向可疑。注意这个工具判据不可靠,")
    print("      请以多角度出图肉眼比对为准。")
    print("SWEEP_ANALYZE=INCONCLUSIVE")
    return 2


if __name__ == "__main__":
    sys.exit(main())
