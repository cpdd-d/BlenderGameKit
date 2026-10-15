"""生成一张**数学上精确已知**的面部 SDF 测试图(隔离"算法错"和"素材错")。

── 为什么要它(第 126 轮)────────────────────────────────────────────
面部 SDF 在 Godot 端连试四版都失败,症状都一样:"全脸一个颜色、完全不随光变"。
失败可能是两件完全不同的事:
  ① **算法**写错了(采样坐标算错 / 值域搞反);
  ② **素材**不对(那张 `face_sdf.png` 的布局和我以为的不一样)。
靠"再改一版算法"永远分不清是哪一种 —— 得先拿一张**布局完全已知**的图去测。
这张图按下面的规则程序生成,每一列的灰度都是我算出来的,所以"阴影线应该在哪"
可以直接手算验证:

    水平条带,竖直方向是脸的高度(上=额头,下=下巴)。
    左半 [0, 1/2]:光从**画面左**边来。**离中线越远越白**(受光)。
    右半 [1/2, 1]:镜像:离中线越远越白。
    中线那一列 = 纯黑(阴影线初始位置)。

于是"光在正前方"时应当整片偏暗、"光偏右"时画面左半边转亮 —— 方向可预测。

用法:
    python scripts/make_face_sdf_test.py
产出:
    .audit/face_sdf_probe/art/face_sdf_test.png
"""

import math
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def write_png(path, width, height, pixel_fn):
    """最小 PNG 写入(不依赖 Pillow;和 make_face_sdf.py 同一套)。"""
    raw = bytearray()
    for y in range(height):
        raw.append(0)                                   # filter type 0
        for x in range(width):
            r, g, b, a = pixel_fn(x, y)
            raw += bytes((r, g, b, a))

    def chunk(tag, data):
        payload = tag + data
        return (struct.pack(">I", len(data)) + payload
                + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(png)
    return path


def main():
    size = 256
    half = size // 2

    def pixel(x, y):
        # 到中线的归一化距离:0 = 中线,1 = 最外侧
        if x < half:
            distance = (half - x) / float(half)          # 左半:越往左越大
        else:
            distance = (x - half) / float(half)          # 右半:越往右越大
        # 竖直方向给一点点变化:额头(上)略微更亮,下巴(下)更暗 ——
        # 这样"有没有真的按 V 采到不同行"也能一眼看出来
        vertical = 0.75 + 0.25 * (1.0 - y / float(size - 1))
        value = max(0.0, min(1.0, distance * vertical))
        byte = int(round(value * 255.0))
        return byte, byte, byte, 255

    out = os.path.join(ROOT, ".audit", "face_sdf_probe", "art",
                       "face_sdf_test.png")
    write_png(out, size, size, pixel)
    print(f"已写出 {out}")
    print("布局:左半 0->0.5 由黑到白;右半 0.5->1 由黑到白;中线纯黑")
    print("可手算的预期:")
    print("  采样 u=0.50(中线)      -> 0.00 纯黑")
    print("  采样 u=0.25            -> 1.00 纯白")
    print("  采样 u=0.75            -> 1.00 纯白")
    print("  采样 u=0.375           -> 0.50 中灰")
    return 0


if __name__ == "__main__":
    sys.exit(main())
