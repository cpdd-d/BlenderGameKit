"""贴图像素坐标 <-> Blender UV 的换算(全项目只此一份)。

── 为什么单独一个文件 ──────────────────────────────────────────────
游戏贴图(皮肤 / 图集 / 图标)天然按"图片左上角为原点、y 向下"排;
Blender 的 UV 是"左下角为原点、v 向上"。两套原点之间必须且只能翻一次。

这一翻发生在**写 UV 的时候**:
    图片像素行 y(从顶部数) -> Blender v = 1 - y / 高
之后 Blender 的 glTF 导出器按 glTF 规范把 v 再换成 1 - v(原点改到
左上),Godot 导入时**不再翻**。所以最终:

    图片第 y 行  ==  Blender v = 1 - y/高  ==  导出后 v = y/高

── 历史(第一百九十八轮,C7)────────────────────────────────────────
示例线一开始按 y/64 直接写 V,导出后整张皮肤在 Godot 里上下颠倒;
后来在示例里手写 1 - (y+h)/64 绕过去。把换算收在这里之后,下游只传
像素矩形,不用再各自记这条规则;**导出侧绝对不能再翻一次**
(两层叠加 = 又反了,见计划 R35)。
"""


def size_pair(size):
    """把 `(宽, 高)` 或正方形边长统一成 `(宽, 高)`。"""
    try:
        width, height = size
    except TypeError:
        width = height = size
    width = float(width)
    height = float(height)
    if width <= 0.0 or height <= 0.0:
        raise ValueError(f"贴图尺寸必须为正: {size!r}")
    return width, height


def pixel_point_uv(x, y, size):
    """图片像素点(左上角原点、y 向下)-> Blender UV。"""
    width, height = size_pair(size)
    return (float(x) / width, 1.0 - float(y) / height)


def pixel_rect_uv(rect, size):
    """图片像素矩形 `(x, y, 宽, 高)` -> Blender UV `(u0, v0, u1, v1)`。

    `(x, y)` 是矩形左上角,`y` 从图片顶部往下数;返回的 `v0 < v1`
    (Blender 的 v 向上),可以直接当 `(u0, v0, u1, v1)` 用。
    """
    x, y, width, height = rect
    if width < 0 or height < 0:
        raise ValueError(f"贴图矩形不能为负: {rect!r}")
    u0, v0 = pixel_point_uv(x, y + height, size)
    u1, v1 = pixel_point_uv(x + width, y, size)
    return (u0, v0, u1, v1)
