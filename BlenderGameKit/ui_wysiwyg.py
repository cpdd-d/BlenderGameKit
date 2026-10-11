"""界面设计"所见即所得"的纯逻辑(第一百零三轮)。

── 为什么单独一个模块(别合并回 ui.py)──────────────────────────────
这里的每个函数都是**数学**:视口布局缩放 / 命中测试 / 拖动换算 / 圆角
几何。它们**不 import bpy** ——
  * 离线单测直接喂假数据(毫秒级跑完);
  * ui.py 那边只留"把算好的顶点喂给 gpu / blf"和"把鼠标事件转成这里的
    一次调用" —— 那两件事**只有真 GUI 能跑**(无头模式没有 gpu 上下文),
    测不了,所以越薄越好(和 node_groups.py 同一个理由)。

── 预览布局的"唯一真相"────────────────────────────────────────────
`preview_layout()` 算出来的 scale / 原点,**画**和**拖**必须用同一份 ——
各算各的会出现"看到的位置和拖到的位置差一截"(经典的所见即所得事故)。

── 坐标系(两套,别混)──────────────────────────────────────────────
  * **设计坐标**:原点在屏幕框**左上**、y 向下(和 Godot 的 UI 一致);
  * **屏幕坐标**:视口区域坐标、原点在**左下**(Blender 的 draw handler 用的)。
  两者靠 `design_to_screen` / `screen_to_design` 换算。
"""

import math

# 屏幕框四周在视口里留的边(像素)
PREVIEW_PAD = 48.0

# 对齐常量:左上 = "直接读 X / Y",其它对齐走"边距"(和生成代码同一套语义)
ALIGN_TOP_LEFT = "TOP_LEFT"
ALIGN_CENTER = "CENTER"


def preview_layout(region_w, region_h, design_w, design_h, pad=PREVIEW_PAD):
    """在视口区域里给"游戏屏幕"找一个居中的矩形。放不下返回 None。

    返回 {"scale", "x0", "y0", "box_w", "box_h"} 或 None。
    """
    region_w = float(region_w or 0.0)
    region_h = float(region_h or 0.0)
    design_w = float(design_w or 0.0)
    design_h = float(design_h or 0.0)
    if min(region_w, region_h, design_w, design_h) <= 0.0:
        return None
    scale = min((region_w - pad) / design_w, (region_h - pad) / design_h)
    if scale <= 0.0:
        return None
    box_w, box_h = design_w * scale, design_h * scale
    return {"scale": scale,
            "x0": (region_w - box_w) * 0.5,
            "y0": (region_h - box_h) * 0.5,
            "box_w": box_w, "box_h": box_h}


def design_to_screen(rect, layout, design_h):
    """控件矩形(设计坐标,左上原点) -> 屏幕矩形(左下原点)。

    返回 (left, bottom, right, top)。
    """
    x, y, width, height = (float(rect[0]), float(rect[1]),
                           float(rect[2]), float(rect[3]))
    scale = float(layout.get("scale") or 1.0)
    left = float(layout.get("x0") or 0.0) + x * scale
    bottom = float(layout.get("y0") or 0.0) \
        + (float(design_h) - y - height) * scale
    return (left, bottom, left + width * scale, bottom + height * scale)


def screen_to_design(sx, sy, layout, design_h):
    """屏幕坐标(左下原点) -> 设计坐标(左上原点)。"""
    scale = float(layout.get("scale") or 1.0)
    if scale <= 0.0:
        scale = 1.0
    return ((float(sx) - float(layout.get("x0") or 0.0)) / scale,
            float(design_h) - (float(sy) - float(layout.get("y0") or 0.0)) / scale)


def hit_test(rows, design_x, design_y):
    """命中哪个控件(设计坐标)。返回最上层的 row;没命中返回 None。

    `rows` = ui.py 的 `_ui_preview_rects` 输出(前五项是位置尺寸)。
    它是"父先子后"的顺序,而**子控件画在父控件上面** —— 所以从后往前
    找第一个命中的就是"看得见的那个"。
    """
    for row in reversed(list(rows or ())):
        try:
            x, y = float(row[1]), float(row[2])
            width, height = float(row[3]), float(row[4])
        except (IndexError, TypeError, ValueError):
            continue
        if x <= design_x <= x + width and y <= design_y <= y + height:
            return row
    return None


def drag_writeback(node, dx, dy, free=False, current=None):
    """把一次拖动换算成"要写回卡片的属性"(纯函数)。

    ── 两种手感(用户选的 A 方案)─────────────────────────────────────
    * **自由拖**(按住 Shift,或本来就是"左上"对齐):写 `gk_x / gk_y`,
      位置 = 原来的位置 + 拖动增量 —— "拖到哪就是哪";
    * **约束拖**(默认):**保持对齐语义** —— 只改 `gk_margin`(离那条边的
      距离)。规则:
        - 对齐全含 LEFT / RIGHT -> 横向能拖(LEFT 是"离左边",RIGHT 是
          "离右边",换算时方向相反);
        - 含 TOP / BOTTOM -> 纵向能拖,同理;
        - 两个方向都含(比如"右上")-> 按**位移大的那个方向**折算 margin
          (margin 只有一个值,两个方向本来共用);
        - 纯『居中』-> 没有可调的量,返回 {} (调用方给一句提示);
      margin 不许小于 0(否则控件会越过那条边)。

    ── current(踩过的坑,别省)──────────────────────────────────────
    自由拖 / 左上对齐时,基准必须用**控件当前实际位置(相对父控件左上)**
    —— 也就是 `current`。不能拿卡片上的旧 `gk_x` 加增量:"贴底居中"这类
    对齐下卡片上的 X/Y **根本不参与布局**(位置是算出来的),拿旧值加
    会让控件**跳位**(实测:拖 30 像素、控件蹦到另一处)。
    `current=None` 时才退回"卡片旧值"(老调用方 / 左上控件两值本就相等)。

    返回 {"属性名": 新值};只在需要改的键 —— 拖不动时是空 dict。
    """
    align = str(getattr(node, "gk_align", "") or "").upper() or ALIGN_TOP_LEFT
    if free or align == ALIGN_TOP_LEFT:
        if current is not None:
            base_x, base_y = float(current[0]), float(current[1])
        else:
            base_x = float(getattr(node, "gk_x", 0.0) or 0.0)
            base_y = float(getattr(node, "gk_y", 0.0) or 0.0)
        return {"gk_align": ALIGN_TOP_LEFT,
                "gk_x": base_x + float(dx),
                "gk_y": base_y + float(dy)}
    if align == ALIGN_CENTER:
        return {}
    can_h = ("LEFT" in align) or ("RIGHT" in align)
    can_v = ("TOP" in align) or ("BOTTOM" in align)
    if not can_h and not can_v:
        return {}
    if can_h and not can_v:
        use_h = True
    elif can_v and not can_h:
        use_h = False
    else:
        use_h = abs(float(dx)) >= abs(float(dy))
    if use_h:
        step = float(dx) if "LEFT" in align else -float(dx)
    else:
        step = float(dy) if "TOP" in align else -float(dy)
    margin = float(getattr(node, "gk_margin", 24.0) or 0.0)
    return {"gk_margin": max(0.0, margin + step)}


def drag_hint(node, free=False):
    """拖动时给用户的一句话(没话说返回空串)。"""
    align = str(getattr(node, "gk_align", "") or "").upper() or ALIGN_TOP_LEFT
    if free or align == ALIGN_TOP_LEFT:
        return ""
    if align == ALIGN_CENTER:
        return "『居中』对齐没有边距可调 —— 按住 Shift 拖动可以自由摆放"
    return f"『{align}』对齐:拖动改的是**边距**;按住 Shift 拖动 = 自由摆放"


def clamp_design_point(x, y, design_w, design_h):
    """把设计坐标夹进屏幕框里(拖出边框时不至于算出离谱的值)。"""
    return (max(0.0, min(float(design_w), float(x))),
            max(0.0, min(float(design_h), float(y))))


def rounded_rect_fan(x, y, width, height, radius, segments=4):
    """圆角矩形的三角扇顶点(屏幕坐标,原点左下)。

    返回 [中心, 周边点..., 首点] —— 直接喂给
    `batch_for_shader(shader, "TRI_FAN", {"pos": verts})`
    (⚠ 原语名是 `TRI_FAN`,不是 "TRIANGLE_FAN" —— 真 GUI 校验抓到的)。
    radius 太小时退化成直角矩形(4 个角,没有圆弧)。
    """
    width = max(0.0, float(width))
    height = max(0.0, float(height))
    radius = max(0.0, min(float(radius or 0.0), width * 0.5, height * 0.5))
    center = (x + width * 0.5, y + height * 0.5)
    if radius <= 0.01:
        points = [(x, y), (x + width, y),
                  (x + width, y + height), (x, y + height)]
    else:
        segments = max(1, int(segments))
        arcs = (
            (x + radius, y + height - radius, 90.0, 180.0),     # 左上
            (x + radius, y + radius, 180.0, 270.0),             # 左下
            (x + width - radius, y + radius, 270.0, 360.0),     # 右下
            (x + width - radius, y + height - radius, 0.0, 90.0),  # 右上
        )
        points = []
        for ccx, ccy, start, end in arcs:
            for index in range(segments + 1):
                angle = math.radians(start + (end - start) * index / segments)
                points.append((ccx + radius * math.cos(angle),
                               ccy + radius * math.sin(angle)))
    return [center] + points + [points[0]]