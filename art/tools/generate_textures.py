#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minecraft 风格 16×16 像素素材生成器（纯标准库，无第三方依赖）。

产出只写在 art/ 目录内：
  blocks/  方块贴图（16×16，无缝平铺）
  items/   物品与方块图标（16×16 原版规格）
  items/2x 同一图案的 2× 最近邻放大（32×32，手持/大图标用）
  ui/      界面纹理
  preview.html  可放大 / 平铺查看整组贴图的预览页
  README.md     文件名与用途清单

用法：
  python art/tools/generate_textures.py
  python art/tools/generate_textures.py --sheet %TEMP%/mc_sheet.png
"""

from __future__ import annotations

import json
import math
import random
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # art/
BLOCK_DIR = ROOT / "blocks"
ITEM_DIR = ROOT / "items"
ITEM2_DIR = ITEM_DIR / "2x"
MOB_DIR = ROOT / "mobs"
UI_DIR = ROOT / "ui"

RGBA = tuple
Canvas = list

# ══════════════════════════════════════════════════════════════════════
# 基础工具
# ══════════════════════════════════════════════════════════════════════
def C(value: str, alpha: int = 255):
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha)


def shade(color, factor):
    return (min(255, int(round(color[0] * factor))),
            min(255, int(round(color[1] * factor))),
            min(255, int(round(color[2] * factor))),
            color[3] if len(color) > 3 else 255)


def canvas(width=16, height=16, fill=(0, 0, 0, 0)):
    return [[fill for _ in range(width)] for _ in range(height)]


def _png_chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def write_png(path: Path, pixels):
    height = len(pixels)
    width = len(pixels[0])
    raw = bytearray()
    for row in pixels:
        raw.append(0)
        for pixel in row:
            raw.extend(pixel)
    payload = (b"\x89PNG\r\n\x1a\n"
               + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height,
                                                 8, 6, 0, 0, 0))
               + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
               + _png_chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def scale_nn(pixels, factor):
    out = []
    for row in pixels:
        wide = [tuple(pixel) for pixel in row for _ in range(factor)]
        for _ in range(factor):
            out.append([tuple(pixel) for pixel in wide])
    return out


def count_colors(pixels):
    return len({tuple(pixel) for row in pixels for pixel in row})


def is_opaque(pixels):
    return all(pixel[3] == 255 for row in pixels for pixel in row)


def seam_ratio(pixels):
    """接缝与内部相邻像素差的比值；接近 1 说明看不出接缝。"""
    h, w = len(pixels), len(pixels[0])

    def diff(a, b):
        return sum(abs(a[i] - b[i]) for i in range(4))

    edge_v = sum(diff(pixels[y][-1], pixels[y][0]) for y in range(h)) / h
    inner_v = sum(diff(pixels[y][x], pixels[y][x + 1])
                  for y in range(h) for x in range(w - 1)) / (h * (w - 1))
    edge_h = sum(diff(pixels[-1][x], pixels[0][x]) for x in range(w)) / w
    inner_h = sum(diff(pixels[y][x], pixels[y + 1][x])
                  for y in range(h - 1) for x in range(w)) / ((h - 1) * w)
    return edge_v / max(inner_v, 0.001), edge_h / max(inner_h, 0.001)


def smooth(t):
    return t * t * (3.0 - 2.0 * t)


def value_noise(seed, cells, width=16, height=16):
    """周期性 value noise：四边自然衔接，用于无缝贴图。"""
    rng = random.Random(seed)
    grid = [[rng.random() for _ in range(cells)] for _ in range(cells)]
    out = [[0.0] * width for _ in range(height)]
    for y in range(height):
        gy = y * cells / height
        y0 = int(gy) % cells
        y1 = (y0 + 1) % cells
        ty = smooth(gy - int(gy))
        for x in range(width):
            gx = x * cells / width
            x0 = int(gx) % cells
            x1 = (x0 + 1) % cells
            tx = smooth(gx - int(gx))
            top = grid[y0][x0] * (1 - tx) + grid[y0][x1] * tx
            bottom = grid[y1][x0] * (1 - tx) + grid[y1][x1] * tx
            out[y][x] = top * (1 - ty) + bottom * ty
    return out


def fbm(seed, cells=4, width=16, height=16):
    """两层周期噪声，归一化到 0~1。"""
    a = value_noise(seed, cells, width, height)
    b = value_noise(seed + 1, cells * 2, width, height)
    values = [0.68 * a[y][x] + 0.32 * b[y][x]
              for y in range(height) for x in range(width)]
    lo, hi = min(values), max(values)
    span = max(hi - lo, 0.001)
    return [[(0.68 * a[y][x] + 0.32 * b[y][x] - lo) / span
             for x in range(width)] for y in range(height)]


def _weighted(rng, weights):
    total = sum(weights)
    pick = rng.random() * total
    acc = 0.0
    for i, weight in enumerate(weights):
        acc += weight
        if pick <= acc:
            return i
    return len(weights) - 1


def voronoi(seed, cells, jitter=0.42, width=16, height=16):
    """环面 Voronoi：石块/砂砾细胞在四边自动接续。"""
    rng = random.Random(seed)
    step_x = width / cells
    step_y = height / cells
    points = []
    for gy in range(cells):
        for gx in range(cells):
            points.append((((gx + 0.5 + rng.uniform(-jitter, jitter)) * step_x) % width,
                           ((gy + 0.5 + rng.uniform(-jitter, jitter)) * step_y) % height))
    labels = [[0] * width for _ in range(height)]
    edges = [[False] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            best, second, best_id = 1e9, 1e9, 0
            for i, (px, py) in enumerate(points):
                dx = abs(x + 0.5 - px)
                dx = min(dx, width - dx)
                dy = abs(y + 0.5 - py)
                dy = min(dy, height - dy)
                dist = math.hypot(dx, dy)
                if dist < best:
                    second, best, best_id = best, dist, i
                elif dist < second:
                    second = dist
            labels[y][x] = best_id
            edges[y][x] = (second - best) < 0.95
    return labels, edges


# ══════════════════════════════════════════════════════════════════════
# 有限调色板（每个区块 5~6 级明度，同类方块亮度相互协调）
# ══════════════════════════════════════════════════════════════════════
GRASS_TOP = [C(x) for x in ("#62953A", "#6FA344", "#7CB14E", "#89BF58",
                            "#96CD62", "#A3DB6C")]
GRASS_SIDE = [C(x) for x in ("#578432", "#63913C", "#6F9E46", "#7BAB50",
                             "#87B85A", "#93C564")]
DIRT = [C(x) for x in ("#6B4A30", "#785437", "#855E3E", "#926846",
                       "#9F724E", "#AC7C56")]
STONE = [C(x) for x in ("#66666A", "#707074", "#7A7A7E", "#848488",
                        "#8E8E92", "#98989C")]
SAND = [C(x) for x in ("#C8B984", "#D2C38F", "#DCCD9A", "#E6D7A5",
                       "#F0E1B0", "#FAEBBB")]
WOOD_BARK = [C(x) for x in ("#4A3620", "#564027", "#624A2E", "#6E5435",
                            "#7A5E3C", "#866844")]
PLANKS = [C(x) for x in ("#9A7A4C", "#A68554", "#B2905C", "#BE9B64",
                         "#CAA66C", "#D6B174")]
LOG_TOP = [C(x) for x in ("#775B35", "#84663C", "#917145", "#9E7C4D",
                          "#AB8755")]
LEAVES = [C(x) for x in ("#3A6C27", "#467C31", "#528C3B", "#5E9C45",
                         "#6AAC4F")]
LEAF_HOLE = C("#12300E")
WATER = [C(x) for x in ("#2C5CC0", "#3468D2", "#3C74E0", "#4480EA",
                        "#5290F2", "#6AAAFA")]
SNOW = [C(x) for x in ("#DCE9F1", "#E6F0F6", "#EEF5FA", "#F5FAFD",
                       "#FBFDFF")]
GRAVEL = [C(x) for x in ("#696560", "#736E68", "#7F7973", "#8B847D",
                         "#979087", "#A39B91")]
BEDROCK = [C(x) for x in ("#26262A", "#303035", "#3A3A40", "#44444A",
                          "#4E4E54")]

COAL_ORE = [C(x) for x in ("#17171A", "#232327", "#313136")]
IRON_ORE = [C(x) for x in ("#8F6142", "#B07C57", "#D3A077", "#EFC39C")]
GOLD_ORE = [C(x) for x in ("#8F6A12", "#C79A1E", "#F0C93C", "#FFE589")]
DIAMOND_ORE = [C(x) for x in ("#157C79", "#27AAA6", "#54D8D2", "#A9F5F0")]

# 物品配色
IRON_L = C("#EDF2F8")
IRON = C("#C3CBD5")
IRON_D = C("#8C95A3")
IRON_X = C("#5B6472")
WOOD = C("#8A6238")
WOOD_D = C("#5E4224")
APPLE_R = C("#D63A32")
APPLE_D = C("#A3221D")
APPLE_L = C("#F0715F")
STEM = C("#5A3A1C")
LEAF = C("#3E8F35")
BREAD = C("#C08A4A")
BREAD_L = C("#E0B477")
BREAD_D = C("#8A5C2E")
FLAME_Y = C("#FFE98A")
FLAME_O = C("#FFAE3C")
COAL = C("#202024")
COAL_M = C("#3A3A42")
COAL_L = C("#5A5A62")

# UI 配色
GUI_BORDER = C("#2B2B2B")
GUI_LIGHT = C("#FFFFFF")
GUI_SHADOW = C("#555555")
GUI_BODY = C("#C6C6C6")
GUI_SLOT = (139, 139, 139, 255)
GUI_SLOT_HI = (170, 170, 170, 255)
GUI_SLOT_LO = (60, 60, 60, 255)
HEART_O = C("#2A0A06")
HEART_B = C("#D22A1E")
HEART_L = C("#FF7A6A")
HEART_S = C("#8E120B")
HEART_E = C("#3A3434")

# 生物配色（对齐 make_minecraft_demo.py 里的纯色材质：皮肤绿 / 青衫 / 蓝紫裤 / 羊毛白 / 肤色）
Z_SKIN = [C(x) for x in ("#3C6E2E", "#467A36", "#50863E", "#5A9246", "#649E4E")]
Z_SHIRT = [C(x) for x in ("#147070", "#1A7C7C", "#208888", "#269494")]
Z_PANTS = [C(x) for x in ("#2E3270", "#383C84", "#424698", "#4C50AC")]
Z_DARK = C("#0D130D")
Z_TOOTH = C("#B7CFA4")
SKIN = [C(x) for x in ("#9C6C46", "#AC7B52", "#BB8A5E", "#CA996C", "#D6A67A")]
SLEEVE = [C(x) for x in ("#2F58A8", "#3864B8", "#4170C8", "#4A7CD8")]
SLEEVE_HEM = C("#24437F")
WOOL = [C(x) for x in ("#B5B0A2", "#C2BDAE", "#CFCABA", "#DCD7C7",
                       "#E8E3D3", "#F4F0E2")]
SHEEP_HIDE = [C(x) for x in ("#8E7C68", "#9E8C77", "#AE9C86")]
SHEEP_MUZZLE = C("#D8BFA8")
SHEEP_EYE = C("#2A241E")
# 破坏裂纹叠加层：三档都由深灰压暗，避免在亮方块上出现灰雾
CRACK = ((12, 10, 9, 225), (38, 34, 31, 195), (72, 66, 60, 150))


# ══════════════════════════════════════════════════════════════════════
# 方块贴图
# ══════════════════════════════════════════════════════════════════════
def grass_top():
    rng = random.Random(101)
    tile = canvas()
    for y in range(16):
        for x in range(16):
            tile[y][x] = GRASS_TOP[_weighted(rng, (10, 22, 30, 24, 10, 4))]
    for _ in range(4):                       # 少量深色草叶
        tile[rng.randrange(16)][rng.randrange(16)] = GRASS_TOP[0]
    for _ in range(3):                       # 极少量亮尖
        tile[rng.randrange(16)][rng.randrange(16)] = GRASS_TOP[5]
    return tile


def dirt():
    rng = random.Random(111)
    tile = canvas()
    for y in range(16):
        for x in range(16):
            tile[y][x] = DIRT[_weighted(rng, (12, 22, 28, 22, 12, 4))]
    for _ in range(3):                       # 深色土块
        cx, cy = rng.randrange(16), rng.randrange(16)
        for dx in range(2):
            for dy in range(2):
                tile[(cy + dy) % 16][(cx + dx) % 16] = DIRT[0]
    for _ in range(2):                       # 小石粒
        tile[rng.randrange(16)][rng.randrange(16)] = DIRT[5]
    return tile


def stone():
    rng = random.Random(121)
    tile = canvas()
    for y in range(16):
        for x in range(16):
            tile[y][x] = STONE[_weighted(rng, (14, 24, 28, 20, 10, 4))]
    for _ in range(2):
        cx, cy = rng.randrange(16), rng.randrange(16)
        for dx in range(2):
            for dy in range(2):
                tile[(cy + dy) % 16][(cx + dx) % 16] = STONE[0]
    return tile


def sand():
    rng = random.Random(131)
    tile = canvas()
    for y in range(16):
        for x in range(16):
            tile[y][x] = SAND[_weighted(rng, (12, 24, 30, 22, 10, 2))]
    for _ in range(3):
        tile[rng.randrange(16)][rng.randrange(16)] = SAND[0]
    for _ in range(2):
        tile[rng.randrange(16)][rng.randrange(16)] = SAND[5]
    return tile


def leaves():
    rng = random.Random(141)
    holes = fbm(142, 4)
    tile = canvas()
    for y in range(16):
        for x in range(16):
            if holes[y][x] < 0.34:            # 叶簇之间的暗缝
                tile[y][x] = LEAF_HOLE
            else:
                tile[y][x] = LEAVES[_weighted(rng, (8, 18, 30, 28, 16))]
    for _ in range(4):                        # 受光叶尖
        x, y = rng.randrange(16), rng.randrange(16)
        if tile[y][x] != LEAF_HOLE:
            tile[y][x] = LEAVES[4]
    return tile


def leaves_transparent():
    """树叶的半透明版：叶缝完全镂空（alpha=0），供 alpha 裁剪材质使用。"""
    rng = random.Random(143)
    holes = fbm(142, 4)                       # 与不透明版共用同一张噪声场
    tile = canvas()
    for y in range(16):
        for x in range(16):
            if holes[y][x] < 0.32:
                continue                      # 真正镂空，透出后面的天空
            tile[y][x] = LEAVES[_weighted(rng, (8, 18, 30, 28, 16))]
    for _ in range(4):
        x, y = rng.randrange(16), rng.randrange(16)
        if tile[y][x][3]:
            tile[y][x] = LEAVES[4]
    return tile


def grass_side():
    tile = dirt()
    rng = random.Random(151)
    for x in range(16):
        height = 3 + ((x * 7 + 3) % 3)        # 3~5 px 的不规则草缘
        for y in range(height):
            tile[y][x] = GRASS_SIDE[_weighted(rng, (14, 24, 28, 20, 10, 4))]
        if (x * 5 + 1) % 4 == 0:              # 偶尔垂下一格草尖
            tile[height][x] = GRASS_SIDE[0]
    return tile


def snow_grass_side():
    tile = dirt()
    rng = random.Random(161)
    for x in range(16):
        height = 3 + ((x * 11 + 5) % 3)
        for y in range(height):
            tile[y][x] = SNOW[_weighted(rng, (16, 26, 28, 20, 10))]
        if (x * 3 + 2) % 5 == 0:
            tile[height][x] = SNOW[0]
    return tile


def oak_log_side():
    rng = random.Random(171)
    tile = canvas()
    for y in range(16):
        for x in range(16):
            tile[y][x] = WOOD_BARK[_weighted(rng, (12, 22, 28, 22, 12, 4))]
    for x in (3, 8, 13):                      # 竖向树皮沟槽（不贴边，横向可平铺）
        for y in range(16):
            tile[y][x] = WOOD_BARK[0]
            if (y + x) % 5 == 0:
                tile[y][x + 1] = WOOD_BARK[1]
    for _ in range(10):
        tile[rng.randrange(16)][rng.randrange(16)] = WOOD_BARK[1]
    return tile


def oak_log_top():
    tile = canvas()
    wobble = fbm(183, 4)
    for y in range(16):
        for x in range(16):
            ring = (max(abs(x - 7.5), abs(y - 7.5))
                    + (wobble[y][x] - 0.5) * 1.15)   # 年轮轻微起伏，避免死板同心方
            if ring > 6.5:
                color = WOOD_BARK[1]
            elif ring > 5.5:
                color = LOG_TOP[0]
            elif ring > 3.5:
                color = LOG_TOP[3]
            elif ring > 2.5:
                color = LOG_TOP[1]
            elif ring > 0.5:
                color = LOG_TOP[2]
            else:
                color = LOG_TOP[0]
            tile[y][x] = color
    rng = random.Random(181)
    for _ in range(12):                       # 年轮里的轻微噪点
        x, y = rng.randrange(16), rng.randrange(16)
        if tile[y][x] in (LOG_TOP[2], LOG_TOP[3]):
            tile[y][x] = LOG_TOP[1] if rng.random() < 0.5 else LOG_TOP[4]
    return tile


def oak_planks():
    rng = random.Random(191)
    tile = canvas()
    for y in range(16):
        for x in range(16):
            if y % 4 == 0:
                tile[y][x] = PLANKS[0]        # 横向板缝
            else:
                tile[y][x] = PLANKS[_weighted(rng, (10, 22, 30, 24, 10, 4))]
    for band, joint in enumerate((3, 11, 6, 13)):   # 每块板的竖向端缝
        for y in range(band * 4 + 1, band * 4 + 4):
            tile[y][joint] = PLANKS[0]
    for _ in range(14):                       # 木板顺纹
        x, y = rng.randrange(15), rng.randrange(1, 16)
        if y % 4:
            tile[y][x] = PLANKS[1]
            tile[y][x + 1] = PLANKS[1]
    return tile


def water():
    tile = canvas()
    for y in range(16):
        for x in range(16):
            wave = (0.5
                    + 0.30 * math.sin(2 * math.pi * x / 8.0
                                      + 1.3 * math.sin(2 * math.pi * y / 16.0))
                    + 0.12 * math.sin(2 * math.pi * (2 * y) / 16.0))
            idx = max(0, min(5, int(wave * 6)))
            tile[y][x] = WATER[idx]
    for x, y in ((1, 2), (2, 2), (8, 6), (9, 6),
                 (3, 11), (4, 11), (11, 13), (12, 13)):
        tile[y][x] = WATER[5]
    return tile


def snow():
    rng = random.Random(201)
    tile = canvas()
    for y in range(16):
        for x in range(16):
            tile[y][x] = SNOW[_weighted(rng, (14, 24, 28, 22, 12))]
    return tile


def gravel():
    labels, edges = voronoi(211, 5, jitter=0.55)
    rng = random.Random(212)
    base = {i: rng.randrange(1, 6) for i in range(25)}
    tile = canvas()
    for y in range(16):
        for x in range(16):
            if edges[y][x]:
                tile[y][x] = GRAVEL[0]
            else:
                idx = max(1, min(5, base[labels[y][x]] + rng.choice((-1, 0, 1))))
                tile[y][x] = GRAVEL[idx]
    for y in range(16):                       # 小石子上沿高光
        for x in range(16):
            if not edges[y][x] and labels[(y - 1) % 16][x] != labels[y][x]:
                tile[y][x] = GRAVEL[5]
    return tile


def cobblestone():
    labels, edges = voronoi(221, 4, jitter=0.60)
    rng = random.Random(222)
    base = {i: rng.randrange(1, 6) for i in range(16)}
    tile = canvas()
    for y in range(16):
        for x in range(16):
            if edges[y][x]:
                tile[y][x] = STONE[0]
            else:
                idx = max(1, min(5, base[labels[y][x]] + rng.choice((-1, 0, 0, 1))))
                tile[y][x] = STONE[idx]
    for y in range(16):                       # 石块上沿受光 / 下沿压暗，做出体积
        for x in range(16):
            if edges[y][x]:
                continue
            label = labels[y][x]
            if labels[(y - 1) % 16][x] != label:
                tile[y][x] = STONE[5]
            elif labels[(y + 1) % 16][x] != label:
                tile[y][x] = STONE[1]
    return tile


def bedrock():
    labels, edges = voronoi(231, 5)
    rng = random.Random(232)
    base = {i: rng.randrange(0, 4) for i in range(25)}
    tile = canvas()
    for y in range(16):
        for x in range(16):
            if edges[y][x]:
                tile[y][x] = BEDROCK[0]
            else:
                idx = max(0, min(4, base[labels[y][x]] + rng.choice((-1, 0, 0, 0, 1))))
                tile[y][x] = BEDROCK[idx]
    return tile


ORE_SHAPES = [
    ((0, 0), (1, 0), (0, 1), (1, 1), (2, 1)),
    ((0, 0), (1, 0), (1, 1), (2, 2)),
    ((0, 0), (0, 1), (1, 1), (2, 1)),
    ((0, 0), (1, 0), (1, 1)),
]


def ore(clusters, palette, seed):
    tile = stone()
    rng = random.Random(seed)
    for cx, cy in clusters:
        shape = ORE_SHAPES[rng.randrange(len(ORE_SHAPES))]
        for i, (dx, dy) in enumerate(shape):
            x, y = cx + dx, cy + dy
            if 1 <= x <= 14 and 1 <= y <= 14:
                tile[y][x] = palette[2] if i == 0 else palette[1]
                if y + 1 <= 14 and (x, y + 1) not in [(cx + a, cy + b) for a, b in shape]:
                    tile[y + 1][x] = palette[0]
    return tile


def coal_ore():
    return ore(((3, 3), (10, 2), (6, 8), (12, 9), (4, 12), (9, 13)),
               COAL_ORE, 241)


def iron_ore():
    return ore(((3, 3), (10, 2), (6, 8), (12, 9), (4, 12), (9, 13)),
               IRON_ORE, 251)


def gold_ore():
    return ore(((3, 3), (10, 2), (6, 8), (12, 9), (4, 12), (9, 13)),
               GOLD_ORE, 261)


def diamond_ore():
    return ore(((3, 3), (10, 2), (6, 8), (12, 9), (4, 12), (9, 13)),
               DIAMOND_ORE, 271)


# ── 方块破坏：10 阶段裂纹 ───────────────────────────────────────────
# 每项是 (阶段, [(x, y, 色调), ...])；色调 0 深 / 1 中 / 2 浅。
# 所有裂纹都落在 x=3..13、y=2..14 内，四边留透明 —— 叠加到方块上永不露接缝。
CRACK_STEPS = (
    (1, [(8, 7, 0), (8, 8, 0), (8, 9, 0), (7, 7, 1), (9, 10, 1)]),
    (2, [(9, 6, 0), (9, 5, 0), (7, 8, 1), (9, 11, 0)]),
    (3, [(9, 4, 0), (7, 6, 1), (6, 8, 1), (9, 12, 0)]),
    (4, [(8, 3, 0), (8, 2, 0), (7, 5, 1), (7, 4, 1),
         (6, 9, 1), (5, 9, 1), (10, 6, 1)]),
    (5, [(6, 4, 1), (6, 3, 1), (5, 3, 1), (4, 2, 2),
         (10, 5, 1), (11, 5, 1), (11, 4, 1), (10, 9, 1)]),
    (6, [(12, 4, 1), (12, 3, 1), (13, 2, 2), (5, 10, 1), (4, 11, 1),
         (3, 11, 2), (10, 3, 1), (11, 3, 1), (11, 2, 2), (12, 2, 2)]),
    (7, [(12, 5, 1), (13, 6, 2), (11, 10, 1), (12, 10, 1), (11, 11, 1),
         (12, 12, 2), (7, 10, 1), (7, 11, 1), (6, 11, 1)]),
    (8, [(6, 12, 1), (5, 12, 1), (4, 13, 2), (8, 13, 0), (8, 14, 1),
         (7, 14, 2), (10, 12, 1), (10, 13, 1), (11, 14, 2)]),
    (9, [(7, 9, 2), (9, 8, 2), (8, 6, 2), (9, 9, 2), (5, 7, 2), (4, 7, 2),
         (4, 8, 2), (4, 9, 2), (5, 13, 2), (5, 14, 2), (13, 11, 2),
         (12, 11, 2), (13, 5, 2), (11, 13, 2)]),
)


def destroy_stage(step):
    """第 step 阶段（0 = 全透明，1~9 逐级加密）。"""
    tile = canvas()
    if step == 0:
        return tile
    for stage, pixels in CRACK_STEPS:
        if stage > step:
            break
        for x, y, tone in pixels:
            if tile[y][x][3] == 0:            # 先出现的裂纹保持更深的色调
                tile[y][x] = CRACK[tone]
    return tile


# ══════════════════════════════════════════════════════════════════════
# 生物皮肤（按 demo 里的盒子部件拆图）
# ══════════════════════════════════════════════════════════════════════
def mob_skin():
    """僵尸皮肤：可平铺的腐绿噪点，头/手臂共用。"""
    rng = random.Random(301)
    mottle = fbm(302, 4)
    tile = canvas()
    for y in range(16):
        for x in range(16):
            idx = _weighted(rng, (10, 24, 30, 20, 6))
            if mottle[y][x] < 0.30 and idx > 0:
                idx -= 1                      # 低洼处压暗
            elif mottle[y][x] > 0.74 and idx < 4:
                idx += 1                      # 受光处提亮
            tile[y][x] = Z_SKIN[idx]
    for _ in range(3):                        # 腐斑
        cx, cy = rng.randrange(16), rng.randrange(16)
        for dx, dy in ((0, 0), (1, 0), (0, 1)):
            tile[(cy + dy) % 16][(cx + dx) % 16] = Z_SKIN[0]
    return tile


def mob_shirt():
    """僵尸衣服：青绿色布纹 + 破损下摆。"""
    rng = random.Random(311)
    tile = canvas()
    for y in range(16):
        for x in range(16):
            idx = _weighted(rng, (8, 22, 30, 18))
            if x % 4 == 0:
                idx = max(0, idx - 1)         # 竖向布纹
            tile[y][x] = Z_SHIRT[idx]
    for y in (14, 15):                        # 下摆压暗
        for x in range(16):
            tile[y][x] = Z_SHIRT[0]
    for x in range(16):
        if (x * 7 + 3) % 5 == 0:
            tile[13][x] = Z_SHIRT[0]          # 破口
    return tile


def mob_pants():
    """僵尸裤子：蓝紫牛仔布，带裤缝与磨白。"""
    rng = random.Random(321)
    tile = canvas()
    for y in range(16):
        for x in range(16):
            idx = _weighted(rng, (10, 24, 30, 16))
            if x in (0, 8):
                idx = max(0, idx - 1)         # 裤缝
            tile[y][x] = Z_PANTS[idx]
    for y in range(1, 16, 6):                 # 不规则的膝部磨白
        x = rng.randrange(2, 14)
        tile[y][x] = Z_PANTS[3]
        tile[y][(x + 1) % 16] = Z_PANTS[2]
    return tile


def zombie_face():
    """僵尸头正面：腐绿脸 + 凹陷眼窝 + 龇牙。眼睛/嘴已画进贴图。"""
    tile = mob_skin()
    for x in range(2, 14):                    # 眉骨阴影
        tile[4][x] = Z_SKIN[0]
    for ox in (3, 10):                        # 3×3 眼窝
        for y in range(5, 8):
            for x in range(ox, ox + 3):
                tile[y][x] = Z_DARK
        tile[6][ox + 1] = C("#24361F")        # 眼窝里的死绿反光
    for x in range(5, 11):                    # 嘴
        tile[11][x] = Z_DARK
    for x in (6, 9):                          # 两颗牙
        tile[10][x] = Z_TOOTH
    for y in range(6, 12):                    # 两颊压暗
        tile[y][1] = Z_SKIN[0]
        tile[y][14] = Z_SKIN[0]
    return tile


def sheep_wool():
    """羊羊毛：软噪点底 + 散落的卷毛小钩，可平铺。"""
    rng = random.Random(331)
    soft = fbm(333, 4)
    tile = canvas()
    for y in range(16):
        for x in range(16):
            idx = _weighted(rng, (10, 22, 30, 22, 10))
            if soft[y][x] < 0.30 and idx > 0:
                idx -= 1
            elif soft[y][x] > 0.72 and idx < 5:
                idx += 1
            tile[y][x] = WOOL[idx]
    for _ in range(9):                        # 2×2 卷毛：左上受光、右下压暗
        x, y = rng.randrange(16), rng.randrange(16)
        tile[y][x] = WOOL[5]
        tile[y][(x + 1) % 16] = WOOL[4]
        tile[(y + 1) % 16][x] = WOOL[1]
        tile[(y + 1) % 16][(x + 1) % 16] = WOOL[0]
    return tile


def sheep_face():
    """羊脸（模型当前还没有头部盒子）：羊毛边 + 灰褐脸盘 + 浅色口鼻。"""
    tile = canvas(fill=WOOL[4])
    for y in range(16):
        for x in range(16):
            if ((x - 7.5) / 6.2) ** 2 + ((y - 8.5) / 5.8) ** 2 <= 1.0:
                tile[y][x] = SHEEP_HIDE[1]
    for y in range(16):                       # 脸盘右侧压暗，留出体积
        for x in range(16):
            if tile[y][x] == SHEEP_HIDE[1] and x > 11:
                tile[y][x] = SHEEP_HIDE[0]
    for y in range(9, 15):                    # 浅色口鼻
        for x in range(4, 12):
            if ((x - 7.5) / 3.4) ** 2 + ((y - 11.5) / 2.8) ** 2 <= 1.0:
                tile[y][x] = SHEEP_MUZZLE
    for ox in (4, 10):                        # 眼睛
        tile[7][ox] = SHEEP_EYE
        tile[7][ox + 1] = SHEEP_EYE
        tile[6][ox] = SHEEP_EYE
    tile[11][7] = SHEEP_HIDE[0]               # 鼻孔
    tile[11][8] = SHEEP_HIDE[0]
    for x in (7, 8):                          # 嘴
        tile[13][x] = SHEEP_HIDE[0]
    return tile


def player_arm():
    """玩家手臂：V=0（上）是蓝色袖子，V=1（下）是肤色。"""
    rng = random.Random(341)
    tile = canvas()
    for y in range(16):
        for x in range(16):
            if y < 4:
                if y == 3:
                    color = SLEEVE_HEM
                else:
                    idx = _weighted(rng, (8, 22, 30, 18))
                    if x == 0:
                        idx = max(0, idx - 1)
                    color = SLEEVE[idx]
            else:
                idx = _weighted(rng, (10, 24, 30, 18, 6))
                if x == 0:
                    idx = max(0, idx - 1)      # 左右两侧压暗，盒子看起来更圆
                color = SKIN[idx]
            tile[y][x] = color
    for x in range(16):                       # 袖口阴影
        tile[4][x] = SLEEVE[0]
    for x in (5, 8, 11):                      # 手背关节
        tile[14][x] = SKIN[1]
    return tile


# ══════════════════════════════════════════════════════════════════════
# 物品图标
# ══════════════════════════════════════════════════════════════════════
def sprite(rows, palette):
    assert len(rows) == 16, f"图标需要 16 行，实际 {len(rows)} 行"
    tile = canvas()
    for y, row in enumerate(rows):
        assert len(row) == 16, f"第 {y} 行需要 16 列，实际 {len(row)} 列：{row!r}"
        for x, char in enumerate(row):
            if char in palette:
                tile[y][x] = palette[char]
    return tile


APPLE_SPRITE = [
    "................",
    "........SS......",
    ".......SGG......",
    "....RRRRRRRR....",
    "...RRRRRRRRRR...",
    "..RRLLRRRRRRRR..",
    "..RLLRRRRRRRRR..",
    "..RLRRRRRRRRRR..",
    "..RRRRRRRRRRRR..",
    "..RRRRRRRRRRRR..",
    "..DRRRRRRRRRRD..",
    "...DDRRRRRRDD...",
    "....DDDDDDDD....",
    "................",
    "................",
    "................",
]

TORCH_SPRITE = [
    "................",
    "................",
    ".......Y........",
    "......YYY.......",
    "......OYO.......",
    ".....OOYOO......",
    "......OOO.......",
    ".......O........",
    "......WWW.......",
    "......WwW.......",
    "......WwW.......",
    "......WwW.......",
    "......WwW.......",
    "......WwW.......",
    "......WwW.......",
    "................",
]

COAL_SPRITE = [
    "................",
    "................",
    "................",
    "................",
    "....DMMMD.......",
    "...DMMMMMD......",
    "..DMLLMMMMD.....",
    "..DMLMMMMMMD....",
    "..DMMMMMMMMD....",
    "...DMMMMMMD.....",
    "....DMMMD.......",
    "................",
    "................",
    "................",
    "................",
    "................",
]


def icon_apple():
    return sprite(APPLE_SPRITE, {"R": APPLE_R, "D": APPLE_D,
                                 "L": APPLE_L, "S": STEM, "G": LEAF})


def icon_bread():
    tile = canvas()
    for y in range(16):
        for x in range(16):
            if ((x - 7.5) / 6.4) ** 2 + ((y - 7.2) / 3.9) ** 2 <= 1.0:
                tile[y][x] = BREAD
    for x in range(3, 13):                    # 顶部受光
        if tile[5][x] == BREAD:
            tile[5][x] = BREAD_L
    for x in range(3, 13):                    # 底部阴影
        if tile[10][x] == BREAD:
            tile[10][x] = BREAD_D
    for ox in (4, 7, 10):                     # 三道斜割痕
        for i in range(3):
            x, y = ox + i, 6 + i
            if tile[y][x] in (BREAD, BREAD_L):
                tile[y][x] = BREAD_D
    return tile


def icon_torch():
    return sprite(TORCH_SPRITE, {"Y": FLAME_Y, "O": FLAME_O,
                                 "W": WOOD, "w": WOOD_D})


def icon_ingot():
    tile = canvas()
    for x in range(4, 12):
        tile[6][x] = IRON_L
    for x in range(3, 13):
        tile[7][x] = IRON_L if x < 6 else IRON
    for x in range(2, 14):
        tile[8][x] = IRON_L if x == 2 else IRON
    tile[7][12] = IRON_D
    tile[8][13] = IRON_D
    for y in (9, 10):                         # 正面压暗，和顶面拉开层次
        for x in range(2, 14):
            tile[y][x] = IRON_D if x > 2 else IRON
    for x in range(2, 14):
        tile[11][x] = IRON_X
    for x in range(3, 13):
        tile[12][x] = IRON_X
    return tile


def icon_coal():
    return sprite(COAL_SPRITE, {"D": COAL, "M": COAL_M, "L": COAL_L})


def icon_stick():
    tile = canvas()
    for i in range(9):
        x, y = 4 + i, 13 - i
        tile[y][x] = WOOD
        tile[y][x + 1] = WOOD_D
    return tile


def icon_sword():
    tile = canvas()
    for i in range(10):                      # 剑刃：左下 → 右上，2 px 厚 + 深色下缘
        x, y = 5 + i, 10 - i
        tile[y][x] = IRON_L
        tile[y + 1][x] = IRON
        tile[y + 1][x - 1] = IRON_X
    tile[1][15] = IRON_L                     # 剑尖
    tile[2][15] = IRON
    for x, y in ((4, 9), (5, 10), (6, 11)):  # 护手（与剑身垂直）
        tile[y][x] = IRON_X
    for i in range(4):                       # 2 px 宽木柄
        x, y = 4 - i, 11 + i
        tile[y][x] = WOOD
        tile[y][x + 1] = WOOD_D
    tile[15][0] = WOOD_D                     # 柄尾
    return tile


def icon_pickaxe():
    tile = canvas()
    for i in range(10):                      # 木柄
        x, y = 3 + i, 14 - i
        tile[y][x] = WOOD
        tile[y][x + 1] = WOOD_D
    for x, y in ((5, 4), (4, 5), (5, 5), (6, 3), (7, 2),
                 (8, 2), (9, 2), (10, 3), (11, 4), (12, 5),
                 (13, 6)):                   # 镐头弧形
        tile[y][x] = IRON
    for x, y in ((6, 3), (7, 2), (8, 2), (9, 2)):
        tile[y][x] = IRON_L
    for x, y in ((4, 6), (5, 6), (10, 4), (11, 5), (12, 6), (13, 7)):
        if 0 <= y < 16:
            tile[y][x] = IRON_D
    return tile


def icon_axe():
    tile = canvas()
    for i in range(10):                      # 木柄
        x, y = 3 + i, 14 - i
        tile[y][x] = WOOD
        tile[y][x + 1] = WOOD_D
    for y in range(2, 9):                    # 斧头：靠柄顶的楔形
        for x in range(9, 14):
            if abs(y - 5) + (13 - x) <= 4:
                tile[y][x] = IRON
    for x, y in ((9, 3), (9, 4), (10, 2), (11, 2), (10, 3)):
        tile[y][x] = IRON_L
    for x, y in ((12, 3), (13, 4), (13, 5), (12, 6), (11, 7), (10, 7)):
        tile[y][x] = IRON_D
    return tile


def icon_shovel():
    tile = canvas()
    for i in range(10):                      # 木柄
        x, y = 3 + i, 14 - i
        tile[y][x] = WOOD
        tile[y][x + 1] = WOOD_D
    for x, y in ((10, 1), (11, 1), (12, 2), (12, 3),
                 (11, 4), (10, 4), (9, 3), (9, 2)):   # 铲头
        tile[y][x] = IRON
    for x, y in ((10, 1), (11, 1), (9, 2), (10, 2)):
        tile[y][x] = IRON_L
    for x, y in ((12, 3), (11, 4), (10, 4)):
        tile[y][x] = IRON_D
    return tile


def iso_block_icon(top, side):
    """把方块的正/侧贴图采样成 16×16 等距立方体图标（原版物品栏观感）。"""
    tile = canvas()

    def sample(source, u, v):
        x = min(15, max(0, int(u * 16)))
        y = min(15, max(0, int(v * 16)))
        return source[y][x]

    for y in range(16):
        for x in range(16):
            px, py = x + 0.5, y + 0.5
            a = (px - 7.5) / 7.0              # 顶面菱形参数
            b = -(py - 4.5) / 3.5
            if abs(a) + abs(b) <= 1.0:
                u = (a + 1.0) * 0.5
                v = (1.0 - b) * 0.5
                tile[y][x] = shade(sample(top, u, v), 1.0)
                continue
            if px <= 7.5:                     # 左面
                u = (px - 0.5) / 7.0
                v = (py - 4.5 - 3.5 * u) / 7.5
                if 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0:
                    tile[y][x] = shade(sample(side, u, v), 0.80)
                    continue
            else:                             # 右面
                u = (14.5 - px) / 7.0
                v = (py - 4.5 - 3.5 * u) / 7.5
                if 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0:
                    tile[y][x] = shade(sample(side, u, v), 0.62)
    return tile


# ══════════════════════════════════════════════════════════════════════
# 界面纹理
# ══════════════════════════════════════════════════════════════════════
def ui_crosshair():
    tile = canvas()
    white = (255, 255, 255, 235)
    for x in range(2, 14):
        tile[7][x] = white
        tile[8][x] = white
    for y in range(2, 14):
        tile[y][7] = white
        tile[y][8] = white
    return tile


def ui_hotbar():
    width, height = 182, 22
    tile = canvas(width, height, (0, 0, 0, 0))
    for y in range(height):
        for x in range(width):
            tile[y][x] = (0, 0, 0, 110)
    for y in (0, height - 1):                 # 外框
        for x in range(width):
            tile[y][x] = GUI_SLOT
    for x in (0, width - 1):
        for y in range(height):
            tile[y][x] = GUI_SLOT
    for i in range(9):
        x0 = 1 + i * 20
        for y in range(1, 21):
            for x in range(x0, x0 + 20):
                tile[y][x] = (0, 0, 0, 120)
        for x in range(x0, x0 + 20):
            tile[1][x] = GUI_SLOT
            tile[20][x] = GUI_SLOT
        for y in range(1, 21):
            tile[y][x0] = GUI_SLOT
            tile[y][x0 + 19] = GUI_SLOT
        for x in range(x0 + 1, x0 + 19):
            tile[2][x] = GUI_SLOT_HI
        for y in range(2, 20):
            tile[y][x0 + 1] = GUI_SLOT_HI
        for x in range(x0 + 1, x0 + 19):
            tile[19][x] = GUI_SLOT_LO
        for y in range(2, 20):
            tile[y][x0 + 18] = GUI_SLOT_LO
    return tile


def ui_hotbar_selector():
    size = 24
    tile = canvas(size, size, (0, 0, 0, 0))
    white = (255, 255, 255, 240)
    for t in (0, 1):
        for x in range(size):
            tile[t][x] = white
            tile[size - 1 - t][x] = white
        for y in range(size):
            tile[y][t] = white
            tile[y][size - 1 - t] = white
    return tile


def ui_slot():
    tile = canvas(20, 20, (0, 0, 0, 120))
    for x in range(20):
        tile[0][x] = GUI_SLOT
        tile[19][x] = GUI_SLOT
    for y in range(20):
        tile[y][0] = GUI_SLOT
        tile[y][19] = GUI_SLOT
    for x in range(1, 19):
        tile[1][x] = GUI_SLOT_HI
    for y in range(1, 19):
        tile[y][1] = GUI_SLOT_HI
    for x in range(1, 19):
        tile[18][x] = GUI_SLOT_LO
    for y in range(1, 19):
        tile[y][18] = GUI_SLOT_LO
    return tile


def _button(body, top, bottom, outer):
    tile = canvas(64, 20)
    for y in range(20):
        for x in range(64):
            if x in (0, 63) or y in (0, 19):
                tile[y][x] = outer
            elif x == 1 or y == 1:
                tile[y][x] = top
            elif x == 62 or y == 18:
                tile[y][x] = bottom
            else:
                tile[y][x] = body
    return tile


def ui_button():
    return _button(C("#7A7A7A"), C("#B0B0B0"), C("#4E4E4E"), C("#1E1E1E"))


def ui_button_hover():
    return _button(C("#8C8C8C"), C("#C4C4C4"), C("#5C5C5C"), C("#1E1E1E"))


def ui_button_pressed():
    return _button(C("#5A5A5A"), C("#3F3F3F"), C("#8A8A8A"), C("#1E1E1E"))


def ui_panel_light():
    size = 48
    tile = canvas(size, size, GUI_BODY)
    for i in range(size):
        tile[0][i] = GUI_BORDER
        tile[size - 1][i] = GUI_BORDER
        tile[i][0] = GUI_BORDER
        tile[i][size - 1] = GUI_BORDER
    for i in range(1, size - 1):
        tile[1][i] = GUI_LIGHT
        tile[i][1] = GUI_LIGHT
        tile[size - 2][i] = GUI_SHADOW
        tile[i][size - 2] = GUI_SHADOW
    return tile


def ui_panel_dark():
    size = 64
    body = (18, 20, 26, 215)
    outer = (0, 0, 0, 235)
    hi = (255, 255, 255, 40)
    lo = (0, 0, 0, 120)
    tile = canvas(size, size, body)
    for i in range(size):
        tile[0][i] = outer
        tile[size - 1][i] = outer
        tile[i][0] = outer
        tile[i][size - 1] = outer
    for i in range(1, size - 1):
        tile[1][i] = hi
        tile[i][1] = hi
        tile[size - 2][i] = lo
        tile[i][size - 2] = lo
    return tile


def ui_inventory_bg():
    base = dirt()
    tile = canvas()
    for y in range(16):
        for x in range(16):
            r, g, b, _ = base[y][x]
            tile[y][x] = (int(r * 0.45), int(g * 0.42),
                          int(b * 0.40), 255)
    return tile


HEART_FULL = [
    "..OO.OO..",
    ".OOOOOOO.",
    ".OBBBBBO.",
    "OBBBBBBBO",
    "OBBBBBBBO",
    ".OBBBBBO.",
    "..OBBBO..",
    "...OBO...",
    "....O....",
]

HEART_HALF = [
    "..OO.OO..",
    ".OOOOOOO.",
    ".OBBBEEO.",
    "OBBBBEEO.",
    "OBBBBEEO.",
    ".OBBBEEO.",
    "..OBBEO..",
    "...OEO...",
    "....O....",
]

HEART_EMPTY = [
    "..OO.OO..",
    ".OOOOOOO.",
    ".OEEEEEO.",
    "OEEEEEEEO",
    "OEEEEEEEO",
    ".OEEEEEO.",
    "..OEEEO..",
    "...OEO...",
    "....O....",
]


def _heart(rows, body, extra=None):
    """9×9 小心形，不能用 16×16 的 sprite()。"""
    palette = {"O": HEART_O, "B": body, "E": HEART_E}
    tile = canvas(len(rows[0]), len(rows))
    for y, row in enumerate(rows):
        assert len(row) == len(rows[0]), f"第 {y} 行长度不一致：{row!r}"
        for x, char in enumerate(row):
            if char in palette:
                tile[y][x] = palette[char]
    if extra:
        for (x, y, color) in extra:
            tile[y][x] = color
    return tile


def ui_heart_full():
    return _heart(HEART_FULL, HEART_B,
                  [(2, 2, HEART_L), (3, 2, HEART_L), (5, 5, HEART_S)])


def ui_heart_half():
    tile = _heart(HEART_HALF, HEART_B,
                  [(2, 2, HEART_L), (2, 3, HEART_L)])
    return tile


def ui_heart_empty():
    return _heart(HEART_EMPTY, HEART_E,
                  [(3, 5, (74, 68, 68, 255))])


SKY = [C(x) for x in ("#78A7FF", "#84B0FF", "#90B9FF", "#9CC2FF",
                      "#A8CBFF", "#B4D4FF", "#C0DDFF", "#CCE6FF")]
SUN = C("#FFF7D6")
SUN_EDGE = C("#FFE9A8")
CLOUD = C("#F7FBFF")
CLOUD_SHADE = C("#E2EDF7")
HILL_BACK = C("#4A7F35")
HILL_FRONT = C("#5A9440")
TRUNK = C("#4A3320")
CANOPY = C("#3C7A2C")
CANOPY_LIGHT = C("#4A8C36")
GROUND = C("#5E9636")
GROUND_DARK = C("#4A7B2A")


def ui_menu_background():
    width, height = 320, 180
    horizon = 106
    tile = canvas(width, height)
    for y in range(height):
        if y < horizon:
            idx = min(len(SKY) - 1, y * len(SKY) // horizon)
            color = SKY[idx]
        else:
            color = GROUND if y < height - 30 else GROUND_DARK
        for x in range(width):
            tile[y][x] = color
    # 方块太阳
    for y in range(16, 38):
        for x in range(244, 266):
            tile[y][x] = SUN_EDGE if (y in (16, 37) or x in (244, 265)) else SUN
    # 方块云（上亮下暗）
    clouds = ((24, 40, 46, 6), (38, 120, 64, 8), (18, 190, 34, 5),
              (56, 88, 40, 6), (48, 228, 52, 7), (70, 156, 28, 5))
    for y0, x0, w, h in clouds:
        for y in range(y0, y0 + h):
            for x in range(x0, x0 + w):
                if 0 <= y < height and 0 <= x < width:
                    tile[y][x] = CLOUD_SHADE if y >= y0 + h - 2 else CLOUD
    # 两层阶梯远山
    for layer, (base_y, color) in enumerate(((horizon - 24, HILL_BACK),
                                             (horizon - 12, HILL_FRONT))):
        x = 0
        while x < width:
            span = 20 + (x * 7 + layer * 11) % 28
            top = base_y - (6 + (x * 13 + layer * 17) % 16)
            for y in range(top, horizon):
                for xx in range(x, min(width, x + span)):
                    tile[y][xx] = color
            x += span
    # 近处方块树
    for tx, base in ((38, horizon + 34), (268, horizon + 24),
                     (118, horizon + 52)):
        for y in range(base - 20, base):
            for x in range(tx, tx + 7):
                tile[y][x] = TRUNK
        for y in range(base - 36, base - 18):
            for x in range(tx - 11, tx + 18):
                if (x - tx - 3) ** 2 + ((y - (base - 27)) * 1.25) ** 2 < 165:
                    tile[y][x] = CANOPY
        for x in range(tx - 6, tx + 4):
            tile[base - 34][x] = CANOPY_LIGHT
    # 底部压暗（分 4 档，保持有限调色）
    for y in range(height - 34, height):
        band = (height - 1 - y) // 9
        factor = (0.55, 0.66, 0.78, 0.9)[min(3, band)]
        for x in range(width):
            tile[y][x] = shade(tile[y][x], factor)
    return tile


# ══════════════════════════════════════════════════════════════════════
# 资产登记
# ══════════════════════════════════════════════════════════════════════
ASSETS = []
BREAK_COUNTS = []


def save(tile, rel_path, group, usage, tileable=False, allow_alpha=False):
    path = ROOT / rel_path
    height, width = len(tile), len(tile[0])
    write_png(path, tile)
    record = {
        "p": rel_path,
        "g": group,
        "n": path.name,
        "w": width,
        "h": height,
        "s": f"{width}×{height}",
        "c": count_colors(tile),
        "u": usage,
        "t": bool(tileable),
        "opaque": is_opaque(tile),
        "alpha": bool(allow_alpha),
    }
    if tileable:
        record["seam_v"], record["seam_h"] = seam_ratio(tile)
    ASSETS.append(record)
    return record


def build_blocks():
    save(grass_top(), "blocks/grass_top.png", "blocks",
         "草方块顶面（主绿，比侧面草缘更亮）", True)
    save(grass_side(), "blocks/grass_side.png", "blocks",
         "草方块侧面（不规则草缘 + 泥土下沿）；底面复用 dirt.png", True)
    save(dirt(), "blocks/dirt.png", "blocks",
         "泥土方块六面；也是草方块的底面", True)
    save(stone(), "blocks/stone.png", "blocks", "石头方块六面", True)
    save(cobblestone(), "blocks/cobblestone.png", "blocks",
         "圆石方块（墙体 / 路面 / 建造）", True)
    save(sand(), "blocks/sand.png", "blocks",
         "沙子方块（地表、水边、沙丘）", True)
    save(gravel(), "blocks/gravel.png", "blocks",
         "砂砾方块（小路 / 河床）", True)
    save(oak_log_side(), "blocks/oak_log_side.png", "blocks",
         "橡木原木侧面（树皮竖纹）", True)
    save(oak_log_top(), "blocks/oak_log_top.png", "blocks",
         "橡木原木顶/底面（年轮，单面贴图，不做跨块平铺）", False)
    save(oak_planks(), "blocks/oak_planks.png", "blocks",
         "橡木板（建造、地板）", True)
    save(leaves(), "blocks/leaves.png", "blocks",
         "树叶方块（不透明版，带暗缝）", True)
    save(leaves_transparent(), "blocks/leaves_transparent.png", "blocks",
         "树叶方块（半透明版：叶缝 alpha=0，需材质 alpha 裁剪）", True,
         allow_alpha=True)
    save(water(), "blocks/water.png", "blocks",
         "水面（贴图不透明，透明度建议由材质设为 0.7~0.8）", True)
    save(snow(), "blocks/snow.png", "blocks",
         "雪块六面（雪地地形）", True)
    save(snow_grass_side(), "blocks/snow_grass_side.png", "blocks",
         "积雪草方块侧面（雪缘 + 泥土下沿）", True)
    save(bedrock(), "blocks/bedrock.png", "blocks",
         "基岩（世界底部、不可挖）", True)
    save(coal_ore(), "blocks/coal_ore.png", "blocks",
         "煤矿石（石头底 + 黑色矿点）", True)
    save(iron_ore(), "blocks/iron_ore.png", "blocks",
         "铁矿石（石头底 + 米黄矿点）", True)
    save(gold_ore(), "blocks/gold_ore.png", "blocks",
         "金矿石（石头底 + 金黄矿点）", True)
    save(diamond_ore(), "blocks/diamond_ore.png", "blocks",
         "钻石矿石（石头底 + 青色矿点）", True)


def build_break():
    global BREAK_COUNTS
    tiles = [destroy_stage(step) for step in range(10)]
    counts = [sum(1 for row in tile for pixel in row if pixel[3])
              for tile in tiles]
    assert counts[0] == 0, f"第 0 阶段应为全透明：{counts}"
    assert all(b > a for a, b in zip(counts, counts[1:])), \
        f"裂纹必须逐级增加：{counts}"
    for step, tile in enumerate(tiles):
        note = "全透明，无裂纹" if step == 0 else f"裂纹逐级加密（可见 {counts[step]} px）"
        save(tile, f"blocks/destroy_stage_{step}.png", "break",
             f"方块破坏叠加层 · 阶段 {step}/9：{note}", False,
             allow_alpha=True)
    BREAK_COUNTS = counts


def build_mobs():
    save(mob_skin(), "mobs/zombie_skin.png", "mobs",
         "僵尸皮肤（头其余五面 / 手臂共用），可平铺", True)
    save(zombie_face(), "mobs/zombie_face.png", "mobs",
         "僵尸头正面（眼窝 + 龇牙已画进贴图）", False)
    save(mob_shirt(), "mobs/zombie_shirt.png", "mobs",
         "僵尸躯干衣服（青绿布纹，破损下摆）", True)
    save(mob_pants(), "mobs/zombie_pants.png", "mobs",
         "僵尸腿裤子（蓝紫牛仔布）", True)
    save(sheep_wool(), "mobs/sheep_wool.png", "mobs",
         "羊身体羊毛（可平铺）", True)
    save(sheep_face(), "mobs/sheep_face.png", "mobs",
         "羊脸（模型当前只有羊毛盒子，需先补头部）", False)
    save(player_arm(), "mobs/player_arm.png", "mobs",
         "玩家手臂（V=0 肩部袖子 / V=1 手；四周肤色可环绕）", False)


def build_items():
    entries = (
        ("block_grass", iso_block_icon(grass_top(), grass_side()),
         "草方块物品图标（等距立方体）"),
        ("block_dirt", iso_block_icon(dirt(), dirt()),
         "泥土方块物品图标（等距立方体）"),
        ("block_stone", iso_block_icon(stone(), stone()),
         "石头方块物品图标（等距立方体）"),
        ("block_sand", iso_block_icon(sand(), sand()),
         "沙子方块物品图标（等距立方体）"),
        ("block_oak_log", iso_block_icon(oak_log_top(), oak_log_side()),
         "橡木原木物品图标（等距立方体）"),
        ("block_oak_planks", iso_block_icon(oak_planks(), oak_planks()),
         "橡木板物品图标（等距立方体）"),
        ("block_leaves", iso_block_icon(leaves(), leaves()),
         "树叶方块物品图标（等距立方体）"),
        ("block_snow", iso_block_icon(snow(), snow_grass_side()),
         "雪块物品图标（等距立方体）"),
        ("apple", icon_apple(), "苹果（回血食物）"),
        ("bread", icon_bread(), "面包（食物）"),
        ("stick", icon_stick(), "木棍（合成材料）"),
        ("torch", icon_torch(), "火把（照明）"),
        ("iron_ingot", icon_ingot(), "铁锭（合成材料）"),
        ("coal_lump", icon_coal(), "煤炭（燃料 / 合成材料）"),
        ("iron_sword", icon_sword(), "铁剑（近战武器，手持 / 快捷栏）"),
        ("iron_pickaxe", icon_pickaxe(), "铁镐（挖掘石头 / 矿石）"),
        ("iron_axe", icon_axe(), "铁斧（砍树 / 近战）"),
        ("iron_shovel", icon_shovel(), "铁铲（挖土 / 沙）"),
    )
    for name, tile, usage in entries:
        save(tile, f"items/{name}.png", "items", usage, False)
        save(scale_nn(tile, 2), f"items/2x/{name}.png", "items_2x",
             f"{name}.png 的 2× 最近邻放大（第一人称手持 / 大图标）", False)


def build_ui():
    save(ui_crosshair(), "ui/crosshair.png", "ui",
         "第一人称准星（16×16，建议按屏幕比例整数放大）", False)
    save(ui_hotbar(), "ui/hotbar.png", "ui",
         "9 格快捷栏底框（182×22，原版尺寸，含半透明槽位）", False)
    save(ui_hotbar_selector(), "ui/hotbar_selector.png", "ui",
         "快捷栏选中框（24×24，白色 2 px 边框）", False)
    save(ui_slot(), "ui/slot.png", "ui",
         "通用物品槽（20×20，背包 / 箱子界面复用）", False)
    save(ui_button(), "ui/button.png", "ui",
         "按钮常态（64×20，9-slice 中心拉伸，文字由引擎绘制）", False)
    save(ui_button_hover(), "ui/button_hover.png", "ui",
         "按钮悬停态（比常态更亮）", False)
    save(ui_button_pressed(), "ui/button_pressed.png", "ui",
         "按钮按下态（高光与阴影反转）", False)
    save(ui_panel_light(), "ui/panel_light.png", "ui",
         "浅色面板（48×48，9-slice；原版背包 / 菜单底板）", False)
    save(ui_panel_dark(), "ui/panel_dark.png", "ui",
         "深色半透明面板（64×64，9-slice；开始菜单 / 暂停面板）", False)
    save(ui_inventory_bg(), "ui/inventory_bg.png", "ui",
         "背包底色（压暗的泥土，16×16 可平铺）", True)
    save(ui_heart_full(), "ui/heart_full.png", "ui",
         "满心（9×9，生命值 HUD）", False)
    save(ui_heart_half(), "ui/heart_half.png", "ui",
         "半心（9×9，生命值 HUD）", False)
    save(ui_heart_empty(), "ui/heart_empty.png", "ui",
         "空心（9×9，生命值 HUD）", False)
    save(ui_menu_background(), "ui/menu_background.png", "ui",
         "开始菜单背景（320×180 像素画，按 2× 最近邻铺满屏幕）", False)


# ══════════════════════════════════════════════════════════════════════
# 校验 / 清单 / 预览页
# ══════════════════════════════════════════════════════════════════════
def validate():
    problems = []
    for a in ASSETS:
        if a["g"] == "blocks":
            if (a["w"], a["h"]) != (16, 16):
                problems.append(f"{a['p']} 尺寸不是 16×16：{a['s']}")
            if not a["opaque"] and not a["alpha"]:
                problems.append(f"{a['p']} 方块贴图含透明像素")
            if a["c"] > 18:
                problems.append(f"{a['p']} 色数过多：{a['c']}")
        if a["g"] == "break":
            if (a["w"], a["h"]) != (16, 16):
                problems.append(f"{a['p']} 裂纹尺寸不是 16×16：{a['s']}")
            if a["c"] > 6:
                problems.append(f"{a['p']} 裂纹色数过多：{a['c']}")
            if a["opaque"]:
                problems.append(f"{a['p']} 裂纹叠加层不应完全不透明")
        if a["g"] == "mobs":
            if (a["w"], a["h"]) != (16, 16):
                problems.append(f"{a['p']} 皮肤尺寸不是 16×16：{a['s']}")
            if a["c"] > 18:
                problems.append(f"{a['p']} 皮肤色数过多：{a['c']}")
        if a["g"] == "items" and (a["w"], a["h"]) != (16, 16):
            problems.append(f"{a['p']} 图标尺寸不是 16×16：{a['s']}")
        if a["g"] == "items_2x" and (a["w"], a["h"]) != (32, 32):
            problems.append(f"{a['p']} 2× 图标尺寸不是 32×32：{a['s']}")
    if problems:
        raise SystemExit("素材校验失败：\n  " + "\n  ".join(problems))
    print(f"校验通过：{len(ASSETS)} 个文件")


def luminance(color):
    return 0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]


def report():
    blocks = [a for a in ASSETS if a["g"] == "blocks"]
    print("\n方块贴图（亮度 / 色数 / 接缝比）")
    for a in blocks:
        seam = f"{a['seam_v']:.2f}/{a['seam_h']:.2f}" if "seam_v" in a else "单面"
        print(f"  {a['p']:<28} {a['s']:>6}  {a['c']:>2} 色  "
              f"接缝 {seam}")

    top = grass_top()
    side = grass_side()
    top_lum = sum(luminance(top[y][x]) for y in range(16) for x in range(16)) / 256
    fringe_lum = sum(luminance(side[y][x]) for x in range(16) for y in range(3)) / 48
    print(f"\n草地顶面亮度 {top_lum:.1f} / 侧面草缘亮度 {fringe_lum:.1f} "
          f"（差 {top_lum - fringe_lum:.1f}）")

    print("\n生物皮肤（色数 / 接缝比）")
    for a in [item for item in ASSETS if item["g"] == "mobs"]:
        seam = f"{a['seam_v']:.2f}/{a['seam_h']:.2f}" if "seam_v" in a else "单面"
        print(f"  {a['p']:<26} {a['s']:>6}  {a['c']:>2} 色  接缝 {seam}")

    print("\n破坏裂纹可见像素（0 → 9）")
    print("  " + "  ".join(f"{i}:{n}" for i, n in enumerate(BREAK_COUNTS)))


def write_readme():
    groups = (
        ("blocks", "方块贴图", "blocks/"),
        ("break", "方块破坏叠加层（10 阶段，透明）", "blocks/"),
        ("mobs", "生物皮肤（16×16，按部件拆图）", "mobs/"),
        ("items", "物品 / 方块图标（16×16）", "items/"),
        ("items_2x", "物品图标 2× 放大（32×32）", "items/2x/"),
        ("ui", "界面纹理", "ui/"),
    )
    lines = [
        "# Minecraft 像素素材包",
        "",
        "全部素材由 `tools/generate_textures.py` 程序化生成（纯标准库），"
        "可直接重跑覆盖。",
        "",
        "## 统一规格",
        "",
        "- 方块、物品与生物皮肤均为 **16×16** 像素网格，最近邻采样，无抗锯齿。",
        "- 方块贴图每张只用 5~6 级明度（草侧面含草 + 土两组），同类方块亮度相互协调；"
        "等距方块图标由三个面叠加明暗，色数会相应更多。",
        "- 所有方块贴图按环面噪声 / 环绕 Voronoi 生成，四边可无缝平铺；"
        "`oak_log_top.png` 是单面年轮图，不做跨块平铺。",
        "- 草、土、石、木、水、叶、沙与菜单天空按 Java 原版基准的亮度 / 饱和度校准，"
        "不跟随参考截图里的光影偏移。",
        "- 草方块：顶面 `grass_top.png` 比侧面草缘更亮；侧面 `grass_side.png` "
        "是草缘 + 泥土；底面复用 `dirt.png`。",
        "- `leaves_transparent.png` 是树叶的 alpha 镂空版：叶缝完全透明，"
        "材质请用 alpha 裁剪 / alpha test（阈值约 0.5），不要用半透明混合。",
        "- 破坏裂纹 `destroy_stage_1~9` 是半透明叠加层，`destroy_stage_0` 全透明；"
        "裂纹只落在四边以内，叠加到任何方块上都不会露出接缝。",
        "- `water.png` 本身不透明，水面透明感请由材质 alpha（约 0.7~0.8）控制。",
        "- `items/2x/` 是 `items/` 的 2× 最近邻放大，供第一人称手持或大图标使用，"
        "像素边缘不会被插值糊掉。",
        "- UI 按钮 / 面板为 9-slice 友好尺寸，文字由引擎绘制。",
        "",
        "## 预览",
        "",
        "打开 `preview.html`：支持缩放（3×~16×）、3×3 平铺查接缝、"
        "透明棋盘背景和点击单图放大。",
        "",
    ]
    for key, title, prefix in groups:
        rows = [a for a in ASSETS if a["g"] == key]
        lines += [f"## {title}", "",
                  "| 文件 | 尺寸 | 色数 | 平铺 | 用途 |",
                  "| --- | --- | --- | --- | --- |"]
        for a in rows:
            rel = a["p"][len(prefix):] if a["p"].startswith(prefix) else a["p"]
            lines.append(f"| `{rel}` | {a['s']} | {a['c']} | "
                         f"{'可' if a['t'] else '否'} | {a['u']} |")
        lines.append("")
    lines += [
        "## 生物皮肤接法（需要模型侧配合）",
        "",
        "`make_minecraft_demo.py` 里的僵尸、羊、玩家手臂目前都是**纯色盒子材质、没有 UV**。"
        "贴图已按部件拆好，模型侧还需要：",
        "",
        "1. 给对应盒子加 UV（每个面 0~1；手臂的 V 轴沿长度方向）。",
        "2. 材质改用 albedo 贴图，`TEXTURE_FILTER_NEAREST` + `SHADING_MODE_UNSHADED`，"
        "与现有方块材质一致。",
        "3. 僵尸脸上的眼睛 / 嘴已经画进贴图，原来凸出的眼睛、嘴小盒子可以删掉"
        "（保留也能用，只是会略微凸出）。",
        "",
        "| 部件（demo 里的尺寸） | 贴图 | UV / 材质建议 |",
        "| --- | --- | --- |",
        "| 僵尸头 `0.44³` | `zombie_face.png`（正面）+ `zombie_skin.png`（其余五面） "
        "| 正面用整张 0..1，其余面平铺 |",
        "| 僵尸躯干 `0.50×0.26×0.70` | `zombie_shirt.png` | 六面 0..1，破损下摆朝下 |",
        "| 僵尸手臂 `0.16×0.60×0.16` | `zombie_skin.png` | 六面 0..1，或 V 沿手臂平铺 |",
        "| 僵尸腿 `0.20×0.22×0.75` | `zombie_pants.png` | 六面 0..1 |",
        "| 羊身体 `0.8×0.9×1.2` | `sheep_wool.png` | 六面 0..1，可平铺 |",
        "| 羊头（模型暂无） | `sheep_face.png` | 需先在模型侧补头部盒子 |",
        "| 玩家手臂 `0.13×0.13×0.36` | `player_arm.png` "
        "| V=0 肩部（袖子）、V=1 手；端面用肤色段 |",
        "",
        "## 尚未覆盖（下一轮可补）",
        "",
        "- 羊的头部 / 四条腿、生物行走动画与骨骼绑定（模型侧）。",
        "- 火把 / 工作台等异形方块；饥饿值、经验条、Boss 血条、"
        "物品数量数字、字体与标题 Logo。",
        "- 天空盒、日月贴图、洞穴 / 岩浆等特殊方块。",
        "",
        "## 与 demo 的关系",
        "",
        "这些文件是独立素材包，没有改动 `examples/` 下的任何工程。"
        "若要替换现有 demo 的 `art/mc_*.png`，按本清单逐一对应复制即可；"
        "草/木原本是多格 atlas，本包拆成了单张，替换时需要同步调整 UV。"
        "生物皮肤、破坏裂纹、半透明树叶是新增素材，需要模型 / 材质侧"
        "先加 UV 与 alpha 设置。",
        "",
    ]
    (ROOT / "README.md").write_text("\n".join(lines), encoding="utf-8")


PREVIEW_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Minecraft 像素素材 · 预览</title>
<style>
  :root {
    --bg: #14161b; --panel: #1d2027; --panel-2: #232831;
    --line: #2f3540; --text: #e7ecf3; --dim: #96a1b2; --accent: #7fd15a;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; color: var(--text);
    background: radial-gradient(1200px 600px at 50% -10%, #1e2530 0%, var(--bg) 55%) fixed;
    font: 14px/1.55 "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
  }
  body.checker .card .thumb, body.checker .mview, .mview.checker {
    background-color: #2a2f39;
    background-image:
      linear-gradient(45deg, #242832 25%, transparent 25%),
      linear-gradient(-45deg, #242832 25%, transparent 25%),
      linear-gradient(45deg, transparent 75%, #242832 75%),
      linear-gradient(-45deg, transparent 75%, #242832 75%);
    background-size: 16px 16px;
    background-position: 0 0, 0 8px, 8px -8px, -8px 0;
  }
  img { image-rendering: pixelated; image-rendering: crisp-edges; }
  header { max-width: 1280px; margin: 0 auto; padding: 34px 20px 10px; }
  h1 { margin: 0 0 6px; font-size: 26px; letter-spacing: .5px; }
  h1 em { color: var(--accent); font-style: normal; }
  .sub { margin: 0; color: var(--dim); }
  .stats { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
  .stats span {
    padding: 4px 10px; border: 1px solid var(--line); border-radius: 999px;
    color: var(--dim); background: #191c22;
  }
  .stats b { color: var(--text); }
  main { max-width: 1280px; margin: 0 auto; padding: 6px 20px 60px; }
  h2 { font-size: 16px; margin: 26px 0 12px; color: #cfd8e6; }
  .hud-stage {
    position: relative; height: 300px; overflow: hidden; border-radius: 10px;
    border: 1px solid var(--line);
    background: linear-gradient(#5c93d6 0%, #7bb6ed 48%, #a9d5f3 66%, #3c6b22 66%, #2f561a 100%);
  }
  .hud-sun {
    position: absolute; left: 78%; top: 22px; width: 54px; height: 54px;
    background: #fff7d6; box-shadow: 0 0 0 4px #ffe9a8;
  }
  .hud-cloud { position: absolute; background: #f7fbff; height: 18px; opacity: .95; }
  .hud-cloud.b { background: #e2edf7; height: 18px; }
  .hud-ground {
    position: absolute; left: 0; right: 0; bottom: 0; height: 102px;
    background-image: url(blocks/grass_top.png);
    background-size: 51px 51px;
    box-shadow: inset 0 0 0 2000px rgba(0, 0, 0, .06);
  }
  .hud-dirt {
    position: absolute; left: 0; right: 0; bottom: 0; height: 17px;
    background-image: url(blocks/dirt.png);
    background-size: 51px 51px;
    opacity: .95;
  }
  .hud-center { position: absolute; left: 50%; top: 44%; transform: translate(-50%, -50%); }
  .hud-center img { width: 48px; height: 48px; display: block; opacity: .9; }
  .hud-bottom {
    position: absolute; left: 50%; bottom: 12px; transform: translateX(-50%);
    display: flex; flex-direction: column; align-items: center; gap: 4px;
  }
  .hud-hearts { display: flex; gap: 3px; filter: drop-shadow(0 1px 0 rgba(0,0,0,.6)); }
  .hud-hearts img { width: 27px; height: 27px; }
  .hud-bar { position: relative; width: 546px; height: 66px; }
  .hud-bar > img.hotbar { width: 546px; height: 66px; display: block; }
  .hud-bar > img.selector {
    position: absolute; width: 72px; height: 72px;
    left: 168px; top: -3px; pointer-events: none;
  }
  .hud-bar > img.icon {
    position: absolute; width: 48px; height: 48px; top: 9px;
  }
  .controls {
    position: sticky; top: 0; z-index: 5; display: flex; flex-wrap: wrap;
    gap: 14px; align-items: center; padding: 12px; margin: 22px 0 14px;
    border: 1px solid var(--line); border-radius: 10px;
    background: rgba(20, 22, 27, .92); backdrop-filter: blur(6px);
  }
  .filters { display: flex; flex-wrap: wrap; gap: 6px; }
  .filters button {
    padding: 5px 11px; border-radius: 7px; cursor: pointer;
    border: 1px solid var(--line); background: var(--panel-2);
    color: var(--dim); font: inherit;
  }
  .filters button.on { color: #10130f; background: var(--accent); border-color: var(--accent); }
  .controls label { display: flex; align-items: center; gap: 7px; color: var(--dim); }
  input[type=range] { width: 120px; accent-color: var(--accent); }
  input[type=checkbox] { accent-color: var(--accent); }
  #zoomOut { color: var(--text); min-width: 34px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(196px, 1fr)); gap: 14px; }
  .card {
    border: 1px solid var(--line); border-radius: 10px; overflow: hidden;
    background: var(--panel); cursor: zoom-in; transition: transform .12s, border-color .12s;
  }
  .card:hover { transform: translateY(-2px); border-color: #46505f; }
  .thumb {
    display: flex; align-items: center; justify-content: center;
    height: 168px; overflow: hidden; padding: 8px;
    border-bottom: 1px solid var(--line);
  }
  .thumb img { max-width: 100%; max-height: 168px; }
  .thumb.tiling { background-repeat: repeat; background-position: center; }
  .card-body { padding: 9px 11px 12px; }
  .card-title { display: flex; align-items: center; gap: 6px; justify-content: space-between; }
  code { font: 12px/1.4 "Cascadia Code", Consolas, monospace; color: #cfe3ff; }
  .badge {
    font-size: 11px; color: var(--dim); border: 1px solid var(--line);
    border-radius: 5px; padding: 0 5px; white-space: nowrap;
  }
  .usage { margin: 6px 0 4px; color: var(--dim); font-size: 12.5px; min-height: 36px; }
  .facts { font-size: 11.5px; color: #6f7b8d; }
  .facts b { color: #9fb0c7; font-weight: 500; }
  .modal {
    position: fixed; inset: 0; z-index: 20; display: flex; align-items: center;
    justify-content: center; padding: 24px; background: rgba(5, 7, 10, .82);
  }
  .modal[hidden] { display: none; }
  .modal-card {
    width: min(720px, 100%); max-height: 90vh; overflow: auto;
    background: var(--panel); border: 1px solid #3a424f; border-radius: 12px;
    padding: 20px; position: relative;
  }
  .modal-card h3 { margin: 0 40px 4px 0; font-size: 18px; }
  .modal-card .close {
    position: absolute; right: 10px; top: 8px; width: 34px; height: 34px;
    border-radius: 8px; border: 1px solid var(--line); background: var(--panel-2);
    color: var(--text); font-size: 20px; cursor: pointer;
  }
  .mview {
    margin: 14px auto; overflow: auto; max-width: 100%;
    background-repeat: repeat; background-position: center;
    border: 1px solid var(--line); border-radius: 8px;
  }
  .modal-card .mview { box-shadow: inset 0 0 0 1px rgba(255,255,255,.03); }
  .modal-info { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 6px 18px; color: var(--dim); font-size: 13px; }
  .modal-info b { color: var(--text); font-weight: 500; }
  footer { text-align: center; color: #5f6b7d; padding: 0 20px 34px; font-size: 12px; }
</style>
</head>
<body class="checker">
<header>
  <h1>Minecraft <em>16×16</em> 像素素材</h1>
  <p class="sub">有限调色 · 无抗锯齿 · 方块贴图无缝平铺 · 与 demo 工程隔离</p>
  <div class="stats" id="stats"></div>
</header>
<main>
  <h2>HUD 组合预览</h2>
  <div class="hud-stage">
    <div class="hud-sun"></div>
    <div class="hud-cloud" style="left:8%;top:44px;width:120px"></div>
    <div class="hud-cloud b" style="left:8%;top:62px;width:96px;height:9px"></div>
    <div class="hud-cloud" style="left:58%;top:80px;width:150px"></div>
    <div class="hud-cloud b" style="left:58%;top:98px;width:118px;height:9px"></div>
    <div class="hud-ground"></div>
    <div class="hud-dirt"></div>
    <div class="hud-center"><img src="ui/crosshair.png" alt="准星"></div>
    <div class="hud-bottom">
      <div class="hud-hearts" id="hudHearts"></div>
      <div class="hud-bar" id="hudBar">
        <img class="hotbar" src="ui/hotbar.png" alt="快捷栏">
        <img class="selector" src="ui/hotbar_selector.png" alt="选中框">
      </div>
    </div>
  </div>

  <div class="controls">
    <div class="filters" id="filters"></div>
    <label>缩放 <input id="zoom" type="range" min="3" max="16" value="7"><output id="zoomOut">7×</output></label>
    <label><input id="tile" type="checkbox"> 3×3 平铺（看接缝）</label>
    <label><input id="checker" type="checkbox" checked> 透明棋盘</label>
  </div>

  <section class="grid" id="grid"></section>
</main>
<footer>点击任意贴图查看 3×3 平铺大图 · 按 Esc 关闭</footer>

<div class="modal" id="modal" hidden>
  <div class="modal-card">
    <button class="close" id="close" aria-label="关闭">×</button>
    <h3 id="mTitle"></h3>
    <div class="mview" id="mView"></div>
    <div class="modal-info" id="mInfo"></div>
    <p class="usage" id="mUsage" style="min-height:0"></p>
  </div>
</div>

<script>
const ASSETS = __ASSETS__;
const GROUPS = { blocks: '方块贴图', break: '破坏裂纹', mobs: '生物皮肤',
                 items: '物品图标', items_2x: '物品 2×', ui: '界面纹理' };
const HUD_SLOTS = ['block_grass', 'block_stone', 'block_sand', 'block_oak_log',
                   'block_oak_planks', 'block_leaves', 'apple', 'iron_sword', 'torch'];
const SELECTED = 1;
let zoom = 7, group = 'all', tiling = false;

const $ = (id) => document.getElementById(id);

function buildStats() {
  const blocks = ASSETS.filter(a => a.g === 'blocks').length;
  const brk = ASSETS.filter(a => a.g === 'break').length;
  const mobs = ASSETS.filter(a => a.g === 'mobs').length;
  const items = ASSETS.filter(a => a.g === 'items').length;
  const ui = ASSETS.filter(a => a.g === 'ui').length;
  $('stats').innerHTML = [
    `<span><b>${ASSETS.length}</b> 个文件</span>`,
    `<span>方块 <b>${blocks}</b></span>`,
    `<span>裂纹 <b>${brk}</b></span>`,
    `<span>生物 <b>${mobs}</b></span>`,
    `<span>物品 <b>${items}</b>（+ 2×）</span>`,
    `<span>界面 <b>${ui}</b></span>`,
    `<span>主网格 <b>16×16</b></span>`
  ].join('');
}

function buildFilters() {
  const entries = [['all', '全部'], ...Object.entries(GROUPS)];
  $('filters').innerHTML = entries.map(([key, label]) => {
    const n = key === 'all' ? ASSETS.length : ASSETS.filter(a => a.g === key).length;
    return `<button data-g="${key}" class="${key === group ? 'on' : ''}">${label} ${n}</button>`;
  }).join('');
  $('filters').querySelectorAll('button').forEach(btn => {
    btn.onclick = () => { group = btn.dataset.g; buildFilters(); renderGrid(); };
  });
}

function renderGrid() {
  const grid = $('grid');
  grid.innerHTML = '';
  const list = ASSETS.filter(a => group === 'all' || a.g === group);
  for (const a of list) {
    const card = document.createElement('article');
    card.className = 'card';
    const w = a.w * zoom, h = a.h * zoom;
    card.innerHTML = `
      <div class="thumb"><img src="${a.p}" width="${w}" height="${h}" alt="${a.n}" loading="lazy"></div>
      <div class="card-body">
        <div class="card-title"><code>${a.n}</code><span class="badge">${a.s}</span></div>
        <p class="usage">${a.u}</p>
        <div class="facts"><b>${a.c}</b> 色 · ${a.t ? '<b>可平铺</b>' : '单面图'}</div>
      </div>`;
    const thumb = card.querySelector('.thumb');
    if (tiling && a.t) {
      thumb.classList.add('tiling');
      thumb.style.backgroundImage = `url(${a.p})`;
      thumb.style.backgroundSize = `${w}px ${h}px`;
      thumb.querySelector('img').style.visibility = 'hidden';
    }
    card.onclick = () => openModal(a);
    grid.appendChild(card);
  }
  document.body.classList.toggle('checker', $('checker').checked);
}

function openModal(a) {
  const z = a.w > 64 ? 1 : 10;
  const reps = a.t ? 3 : 1;
  $('mTitle').textContent = a.n;
  const view = $('mView');
  view.style.width = (a.w * z * reps) + 'px';
  view.style.height = (a.h * z * reps) + 'px';
  view.style.backgroundImage = `url(${a.p})`;
  view.style.backgroundSize = `${a.w * z}px ${a.h * z}px`;
  view.style.backgroundRepeat = 'repeat';
  view.classList.toggle('checker', $('checker').checked);
  $('mInfo').innerHTML = [
    ['路径', `<b>${a.p}</b>`],
    ['尺寸', `<b>${a.s}</b>`],
    ['色数', `<b>${a.c}</b>`],
    ['平铺', `<b>${a.t ? '可无缝平铺' : '单面 / 控件'}</b>`]
  ].map(([k, v]) => `<div>${k}：${v}</div>`).join('');
  $('mUsage').textContent = a.u;
  $('modal').hidden = false;
}

function buildHud() {
  const bar = $('hudBar');
  HUD_SLOTS.forEach((name, i) => {
    const img = document.createElement('img');
    img.className = 'icon';
    img.src = `items/${name}.png`;
    img.alt = name;
    img.style.left = (9 + i * 60) + 'px';
    bar.appendChild(img);
  });
  const sel = bar.querySelector('.selector');
  sel.style.left = (60 * SELECTED - 3) + 'px';
  const hearts = $('hudHearts');
  for (let i = 0; i < 8; i++) {
    const img = document.createElement('img');
    img.src = i < 6 ? 'ui/heart_full.png' : (i === 6 ? 'ui/heart_half.png' : 'ui/heart_empty.png');
    img.alt = '心';
    hearts.appendChild(img);
  }
}

$('zoom').oninput = (e) => {
  zoom = Number(e.target.value);
  $('zoomOut').value = zoom + '×';
  renderGrid();
};
$('tile').onchange = (e) => { tiling = e.target.checked; renderGrid(); };
$('checker').onchange = () => renderGrid();
$('close').onclick = () => { $('modal').hidden = true; };
$('modal').onclick = (e) => { if (e.target === $('modal')) $('modal').hidden = true; };
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') $('modal').hidden = true; });

buildStats();
buildFilters();
buildHud();
renderGrid();
</script>
</body>
</html>
"""


def write_preview():
    payload = json.dumps(ASSETS, ensure_ascii=False, separators=(",", ":"))
    html = PREVIEW_TEMPLATE.replace("__ASSETS__", payload)
    (ROOT / "preview.html").write_text(html, encoding="utf-8")


def write_contact_sheet(path: Path, scale=10):
    picks = [a for a in ASSETS
             if a["g"] != "items_2x" and a["w"] <= 24 and a["h"] <= 24]
    cols = 6
    cell = 24 * scale
    gap = 6
    rows = (len(picks) + cols - 1) // cols
    width = cols * cell + (cols + 1) * gap
    height = rows * cell + (rows + 1) * gap
    sheet = canvas(width, height, (24, 26, 32, 255))
    for index, asset in enumerate(picks):
        source = [[tuple(pixel) for pixel in row]
                  for row in _load_png(ROOT / asset["p"])]
        big = scale_nn(source, scale)
        cx = gap + (index % cols) * (cell + gap)
        cy = gap + (index // cols) * (cell + gap)
        ox = cx + (cell - len(big[0])) // 2
        oy = cy + (cell - len(big)) // 2
        for y in range(cell):
            for x in range(cell):
                sheet[cy + y][cx + x] = (32, 35, 42, 255)
        for y, row in enumerate(big):
            for x, pixel in enumerate(row):
                if pixel[3]:
                    sheet[oy + y][ox + x] = pixel
    write_png(path, sheet)
    print(f"contact sheet -> {path}")


def _load_png(path: Path):
    """只解析本脚本写出的 RGBA PNG（8-bit、filter 0）。"""
    data = path.read_bytes()
    pos = 8
    width = height = 0
    idat = b""
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            width, height = struct.unpack(">II", chunk[:8])
        elif tag == b"IDAT":
            idat += chunk
        pos += 12 + length
    raw = zlib.decompress(idat)
    rows = []
    stride = width * 4 + 1
    for y in range(height):
        line = raw[y * stride + 1:(y + 1) * stride]
        rows.append([tuple(line[x * 4:x * 4 + 4]) for x in range(width)])
    return rows


def main():
    build_blocks()
    build_break()
    build_mobs()
    build_items()
    build_ui()
    validate()
    report()
    write_readme()
    write_preview()
    if "--sheet" in sys.argv:
        index = sys.argv.index("--sheet")
        target = Path(sys.argv[index + 1]) if index + 1 < len(sys.argv) else None
        if target:
            write_contact_sheet(target)
    print(f"\n完成：{ROOT}")


if __name__ == "__main__":
    main()
