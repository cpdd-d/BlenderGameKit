"""生成面部阴影 SDF 贴图 + 一张简单的二次元脸(纯 Python 写 PNG)。

为什么要有这个脚本
------------------
面部阴影 SDF 的"形状"必须由美术画。为了能自动验证着色器真的能正常工作,
这里用程序画一张**符合约定布局**的贴图,顺便当模板给美术参考:
    水平条带,中间劈开成两半:
      左半 [0, 0.5]  光从画面左侧来 -> 阴影线随光角度向右滑
      右半 [0.5, 1]  镜像副本,光从右侧来时用
      白 = 受光, 黑 = 阴影

垂直方向对应脸的高度:上 1/3 是额头(常受光),中间是眼睛/鼻子(会形成鼻影),
下面是下巴(通常偏暗)。

用法:
    python scripts/make_face_sdf.py
"""

import math
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ==========================================================================
# 最小 PNG 写入(不依赖 Pillow)
# ==========================================================================
def write_png(path, width, height, pixel_fn):
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0
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


def smoothstep(edge0, edge1, x):
    if edge1 == edge0:
        return 0.0 if x < edge0 else 1.0
    t = max(0.0, min(1.0, (x - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


# ==========================================================================
# 面部阴影贴图
# ==========================================================================
def make_face_sdf(width=256, height=256):
    """画一条会"滑动"的阴影线。

    设计要点(这些是二次元面部阴影的惯例):
      * 阴影线不是直线,额头处会有个弯折(刘海投影的形状)
      * 眼睛高度附近阴影会切进来更多(鼻梁投影)
      * 下半张脸天然偏暗
      * 左右两半互为镜像,保证光从两边来时行为对称
    """
    half_w = width // 2

    def sample(u, v):
        """u: 0..1 沿条带, v: 0..1 从下到上。返回 0(阴影)~1(受光)。"""
        # 基础:阴影从左边入侵,u 越小越暗
        # 在 u=0.5 处为分界:左半边全暗,右半边全亮 —— 这样 line=0.5 时正好对半分
        base = smoothstep(0.42, 0.58, u)

        # 额头(上 1/3):阴影线往右多推一点,模拟刘海投影
        if v > 0.66:
            base = smoothstep(0.50, 0.68, u)
        # 鼻子高度(0.42~0.66):阴影切进来更多
        elif v > 0.42:
            nose = 1.0 - 0.35 * math.sin((v - 0.42) / 0.24 * math.pi)
            base = smoothstep(0.42 - 0.10 * (1.0 - nose), 0.58, u)
        # 嘴/下巴(下 0.42):整体压暗
        else:
            base = smoothstep(0.38, 0.60, u) * 0.82 + 0.06

        # 边缘留一点柔和过渡,避免完全硬边;着色器里还会再切一次
        return max(0.0, min(1.0, base))

    def pixel(x, y):
        v = 1.0 - (y / (height - 1))  # 从下到上
        if x < half_w:
            # 左半:光从画面左侧来
            u = x / (half_w - 1) if half_w > 1 else 0.0
            value = sample(u, v)
        else:
            # 右半:镜像。用 1-u 保证两半关于中心线对称
            u = (width - 1 - x) / (half_w - 1) if half_w > 1 else 0.0
            value = sample(u, v)
        grey = int(max(0.0, min(1.0, value)) * 255)
        return (grey, grey, grey, 255)

    return pixel


# ==========================================================================
# 一张简单的二次元脸(用于渲染验证)
# ==========================================================================
def make_anime_face(width=256, height=256):
    """画一张正面脸:肤色 + 头发 + 眼睛 + 嘴。是贴图,不是模型。"""

    def pixel(x, y):
        u = x / (width - 1)
        v = 1.0 - (y / (height - 1))  # 0 下 1 上
        cx = u - 0.5
        cy = v - 0.5

        skin = (252, 226, 205, 255)
        skin_shadow = (236, 206, 200, 255)
        hair = (74, 62, 108, 255)
        hair_light = (108, 92, 148, 255)
        eye_white = (250, 250, 255, 255)
        iris = (86, 128, 196, 255)
        iris_dark = (44, 74, 132, 255)
        pupil = (26, 32, 48, 255)
        mouth = (188, 108, 108, 255)
        blush = (250, 178, 178, 255)
        line = (60, 48, 66, 255)

        # --- 脸型:椭圆,去掉下巴太尖的部分 ---
        in_face = (cx / 0.36) ** 2 + (cy / 0.45) ** 2 < 1.0
        if not in_face:
            return (0, 0, 0, 0)

        color = skin
        # 下半张脸稍暗(和面部 SDF 的做法一致)
        if v < 0.42:
            color = skin_shadow

        # --- 头发:顶部 + 两侧鬓发 ---
        if v > 0.78:
            color = hair
        elif v > 0.70 and abs(cx) > 0.30:
            color = hair
        elif abs(cx) > 0.33 and v > 0.30:
            color = hair
        # 头发高光
        if v > 0.86 and abs(cx) < 0.22:
            color = hair_light

        # --- 刘海:几缕垂下来 ---
        for k in (-0.22, -0.04, 0.14, 0.30):
            if 0.62 < v < 0.78 and abs(cx - k) < 0.045:
                color = hair

        # --- 眼睛 ---
        eye_y = 0.52
        for sign in (-1, 1):
            ex = cx - sign * 0.16
            ey = v - eye_y
            if (ex / 0.11) ** 2 + (ey / 0.075) ** 2 < 1.0:
                color = eye_white
            if (ex / 0.085) ** 2 + (ey / 0.065) ** 2 < 1.0:
                color = iris
            if (ex / 0.055) ** 2 + (ey / 0.055) ** 2 < 1.0:
                color = iris_dark
            if (ex / 0.032) ** 2 + (ey / 0.038) ** 2 < 1.0:
                color = pupil
            # 高光小点(二次元眼睛的灵魂)
            if (ex + 0.035) ** 2 + (ey - 0.028) ** 2 < 0.00055:
                color = (255, 255, 255, 255)
            # 上眼线
            if abs(ex) < 0.115 and 0.005 < (ey - 0.062) < 0.022:
                color = line
            # 睫毛
            if abs(ex) < 0.125 and -0.02 < (ey - 0.078) < 0.012 and abs(ex) > 0.06:
                color = line

        # --- 眉毛 ---
        if 0.66 < v < 0.70 and 0.06 < abs(cx) < 0.26:
            if abs(abs(cx) - 0.16) < 0.075:
                color = hair

        # --- 腮红 ---
        for sign in (-1, 1):
            bx = cx - sign * 0.24
            by = v - 0.40
            if (bx / 0.07) ** 2 + (by / 0.035) ** 2 < 1.0:
                color = blush

        # --- 嘴 ---
        mx = cx
        my = v - 0.30
        if (mx / 0.045) ** 2 + (my / 0.018) ** 2 < 1.0:
            color = mouth

        return color

    return pixel


# ==========================================================================
# 输出
# ==========================================================================
TARGETS = (
    (os.path.join(ROOT, "examples", "demo_render", "art", "face_sdf.png"),
     make_face_sdf(256, 256), 256, 256),
    (os.path.join(ROOT, "examples", "demo_render", "art", "face_albedo.png"),
     make_anime_face(256, 256), 256, 256),
)


def main():
    for path, pixel, width, height in TARGETS:
        write_png(path, width, height, pixel)
        print(f"已生成 {os.path.relpath(path, ROOT)}  ({width}x{height})")
    print()
    print("face_sdf.png 的布局约定(改贴图时务必保持):")
    print("  水平条带中间劈开:左半 = 光从画面左侧来,右半 = 镜像")
    print("  白 = 受光,黑 = 阴影")
    print("  垂直方向 = 脸的高度(上=额头,中=眼睛,下=下巴)")
    print("  阴影线在每一半的 0.5 处居中(对应 face_sdf_neutral=0.5)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
