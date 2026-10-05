"""曲线资产:把 Blender 里的曲线变成 Godot 的 `Curve` / `Curve3D` 资源(.tres)。

── 为什么要有这一层(用户 2026-10-04 第十二轮)────────────────────
用户在 Blender 里画曲线来编"运镜 / 缓动 / 时间轴"的**形状**,而 glTF
**不支持曲线**,Godot 也不认 Blender 的曲线对象 —— 所以导出时把曲线
**采样**出来,写成 Godot 自己的资源:

    2D 曲线(在 XY 平面上画的"缓动曲线")-> Curve
        `curve.sample(t)` 给 0~1 的浮点 —— 相机过渡的缓动、时间轴的 Alpha
    3D 曲线(当轨道用的空间曲线)        -> Curve3D
        `curve.sample_baked(t * 长度)` 给 Vector3 —— 相机的运镜轨道

── .tres 的格式是**拿真 Godot 核对过的**(不是猜的)────────────────
2026-10-04 用 Godot 4.7.2 现场生成样本再读回来,结论:

  * `Curve`:`_data = [Vector2(x, y), 左切线, 右切线, 左模式, 右模式, ...]`
    模式 1 = 切线由邻居算(分段直线);模式 0 = 用写死的切线。
    我们采样得很密(32 段),所以用模式 1,采样结果就是"忠实的分段折线"。
  * `Curve3D`:`_data = {"points": PackedVector3Array(入, 出, 位置, ...),
    "tilts": PackedFloat32Array(...)}`;手柄写 0 时 Godot 会**自己**按
    平滑样条处理(实测:sample_baked 走的是平滑曲线)。
  * `Curve3D.sample(offset, true)` 实测**不可靠**(对未烘焙的曲线会返回
    错的值),必须用 `sample_baked(t * get_baked_length(), true)` ——
    顺带还得到"按弧长等速"的好处(运镜不会一头快一头慢)。
"""

import os


# Godot 项目里放曲线资源的子目录(按需创建,不参与 project 的子目录检查)
CURVES_DIR = "curves"

# 采样密度:缓动曲线按 x 重采样 32 段,运镜轨道按弧长等距取 48 个点
EASE_SAMPLES = 32
PATH_SAMPLES = 48


# ==========================================================================
# 名字 -> res:// 路径
# ==========================================================================
def _crc(text):
    """稳定的 8 位十六进制摘要(不能用内置 hash —— 它每个进程都不一样)。"""
    import zlib
    return f"{zlib.crc32(str(text).encode('utf-8')) & 0xFFFFFFFF:08x}"


def _safe_base(name):
    """文件名里的 ASCII 部分(非 ASCII 一律换成下划线)。

    这里**不能**调 exporter.safe_file_name:exporter -> codegen ->
    本模块,反着引会成环。规则保持和它一致:先按字符集压干净,
    再判断压完还剩什么。
    """
    out = []
    for ch in str(name or ""):
        out.append(ch if (ch.isascii() and (ch.isalnum() or ch in "_-"))
                   else "_")
    text = "".join(out).strip("_")
    while "__" in text:
        text = text.replace("__", "_")
    return text if len(text) >= 2 else "curve"


def curve_file_name(name):
    """曲线资源的文件名。

    ── 为什么名字后面要挂一段摘要 ──────────────────────────────────
    脚本里写死的是 `res://curves/xxx.tres`,所以同一个曲线名**每次导出都
    必须算出同一个文件名**(不能用"导出时全局去重"那套 —— 材质那次
    就吃过跨场景重名互相覆盖的亏)。而 Blender 的物体名可以是中文、
    可以带点号,压成 ASCII 之后『运镜』和『角色曲线』会撞成同一个名字。
    挂一段名字的摘要既稳定又唯一。
    """
    return f"{_safe_base(name)}_{_crc(name)}.tres"


def curve_res_path(name):
    """脚本里引用的 `res://curves/xxx.tres`。"""
    return f"res://{CURVES_DIR}/{curve_file_name(name)}"


# ==========================================================================
# Blender -> 采样
# ==========================================================================
def _bpy():
    try:
        import bpy
        return bpy
    except Exception:
        return None


def _curve_object(name):
    """按名字取 Blender 曲线物体(不是曲线物体时返回 None)。"""
    bpy = _bpy()
    if bpy is None:
        return None
    text = str(name or "").strip()
    if not text:
        return None
    try:
        objects = bpy.data.objects
        obj = objects.get(text) if hasattr(objects, "get") else None
    except Exception:
        return None
    if obj is None or getattr(obj, "type", "") != "CURVE":
        return None
    return obj


def curve_dimensions(name):
    """曲线的维度:'2D' / '3D'(找不到时返回空串)。"""
    obj = _curve_object(name)
    data = getattr(obj, "data", None) if obj is not None else None
    return str(getattr(data, "dimensions", "") or "")


def curve_exists(name):
    """场景里有没有这条曲线(给节点面板显示状态用)。"""
    return _curve_object(name) is not None


def _world_matrix(obj):
    """求值后的世界矩阵。

    必须先 `view_layer.update()`:刚 new 出来、或者用户刚在视口里拖过曲线,
    `matrix_world` 还停在旧值(新建时就是单位矩阵)。实测:把曲线挪到
    (1,2,0),采到的点仍然在原点 —— 相机就会走一条"看不见的、没挪动过"
    的轨道。
    """
    bpy = _bpy()
    try:
        if bpy is not None and getattr(bpy, "context", None) is not None:
            bpy.context.view_layer.update()
    except Exception:
        pass
    return getattr(obj, "matrix_world", None)


def _flatten(spline, dense=16):
    """把一条样条采样成**折线点列**(局部坐标,含贝塞尔手柄的形状)。

    ── 为什么用 `spline.evaluate` 而不是只看控制点 ──────────────────
    控制点之间的贝塞尔手柄决定了曲线的实际形状;只看控制点的话,
    用户拉出来的弧线会被当成折线(缓动曲线会变得一格一格)。
    `evaluate(t)` 给的是**曲线上**的点,手柄也算了进去。

    老版本 Blender / 没有 evaluate 时退回"只用控制点"的旧做法。
    """
    evaluate = getattr(spline, "evaluate", None)
    points = []
    if callable(evaluate):
        try:
            for index in range(dense + 1):
                point = evaluate(index / float(dense))
                points.append((float(point[0]), float(point[1]),
                               float(point[2])))
            return points
        except Exception:
            points = []
    # 退回:控制点(poly / NURBS 是四维,贝塞尔是 handle.co)
    try:
        count = len(spline.points)
    except Exception:
        count = 0
    if count >= 2:
        for index in range(count):
            point = spline.points[index]
            points.append((float(point.co[0]), float(point.co[1]),
                           float(point.co[2])))
        return points
    for handle in getattr(spline, "bezier_points", ()) or ():
        points.append((float(handle.co[0]), float(handle.co[1]),
                       float(handle.co[2])))
    return points


def _local_points(name):
    """曲线物体的全部样条 -> 一串局部坐标点。"""
    obj = _curve_object(name)
    if obj is None:
        return []
    data = getattr(obj, "data", None)
    out = []
    for spline in getattr(data, "splines", ()) or ():
        out.extend(_flatten(spline))
    return out


def _to_world(matrix, points):
    """局部点 -> 世界坐标(矩阵手算:mathutils 与普通嵌套列表都认)。"""
    if matrix is None:
        return list(points)
    out = []
    for x, y, z in points:
        try:
            wx = (matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z
                  + matrix[0][3])
            wy = (matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z
                  + matrix[1][3])
            wz = (matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z
                  + matrix[2][3])
        except Exception:
            wx, wy, wz = x, y, z
        out.append((wx, wy, wz))
    return out


def _to_godot_points(points):
    """Blender 世界坐标 -> Godot 坐标:`(x, y, z) -> (x, z, -y)`。

    ── 为什么必须换(『相机_路径』一直潜着的 bug)────────────────────
    glTF 导出用的是 `export_yup=True`,Blender 的 Z-up 到 Godot 变成 Y-up;
    而曲线轨道是**我们自己采样**写进脚本的,不走 glTF —— 不换算的话,
    在视口里画的轨道和游戏里跑的轨道**不是同一条**(高度跑到另一个轴上,
    等于把轨道贴着地面翻了个方向)。换算公式和 `asset_export._godot_position`
    逐字一致,免得两处各一套。
    """
    return [(x, z, -y) for x, y, z in points]


def sample_path_points(name, samples=PATH_SAMPLES):
    """把 3D 曲线采样成**等距**的 Godot 坐标点列(运镜轨道用)。

    贝塞尔的控制点疏密不均,直接拿去插值会出现"该慢的地方飞快"。
    先按折线累计长度,再按等距取点 —— 这样『位置 = 0.5』真的在中间。
    """
    obj = _curve_object(name)
    if obj is None:
        return []
    raw = _to_godot_points(
        _to_world(_world_matrix(obj), _local_points(name)))
    if len(raw) < 2:
        return raw
    dense = []
    for index in range(len(raw) - 1):
        a, b = raw[index], raw[index + 1]
        dense.append(a)
        for step in range(1, 8):
            t = step / 8.0
            dense.append((a[0] + (b[0] - a[0]) * t,
                          a[1] + (b[1] - a[1]) * t,
                          a[2] + (b[2] - a[2]) * t))
    dense.append(raw[-1])
    lengths = [0.0]
    for index in range(1, len(dense)):
        dx = dense[index][0] - dense[index - 1][0]
        dy = dense[index][1] - dense[index - 1][1]
        dz = dense[index][2] - dense[index - 1][2]
        lengths.append(lengths[-1] + (dx * dx + dy * dy + dz * dz) ** 0.5)
    total = lengths[-1]
    if total <= 0.0001:
        return raw
    out = []
    for index in range(samples):
        want = total * index / float(samples - 1)
        cursor = 0
        while cursor < len(lengths) - 1 and lengths[cursor] < want:
            cursor += 1
        prev = max(0, cursor - 1)
        span = lengths[cursor] - lengths[prev]
        t = 0.0 if span <= 0.0001 else (want - lengths[prev]) / span
        a, b = dense[prev], dense[cursor]
        out.append((a[0] + (b[0] - a[0]) * t,
                    a[1] + (b[1] - a[1]) * t,
                    a[2] + (b[2] - a[2]) * t))
    return out


def sample_ease_curve(name, samples=EASE_SAMPLES):
    """把曲线当成"x -> y 的函数"采样,归一化成 0~1 的缓动系数。

    返回 None 表示"采不到"(没选 / 不是曲线 / 点数不够),调用方据此告警。
    否则返回 [(t, v)]:t 是归一化后的横轴,v 是归一化后的纵轴 ——
    **不管用户在 Blender 里画多高,最低点当 0、最高点当 1**,
    所以随便画一条从左到右的曲线就能当缓动用。
    """
    obj = _curve_object(name)
    if obj is None:
        return None
    raw = _to_world(_world_matrix(obj), _local_points(name))
    if len(raw) < 2:
        return None
    # 只认前两个分量(缓动曲线是画在平面上的;3D 曲线的 x/y 一样能读)
    flat = sorted(((x, y) for x, y, _z in raw), key=lambda item: item[0])
    x_min, x_max = flat[0][0], flat[-1][0]
    y_min = min(y for _x, y in flat)
    y_max = max(y for _x, y in flat)
    if x_max - x_min <= 1e-6:
        return None
    if y_max - y_min <= 1e-6:
        return None
    # 按等距的 x 重采样:保证 t 严格递增(重复/回头的点在折线上被压平)
    out = []
    for index in range(samples):
        t = index / float(samples - 1)
        want = x_min + (x_max - x_min) * t
        cursor = 0
        while cursor < len(flat) - 1 and flat[cursor][0] < want:
            cursor += 1
        prev = max(0, cursor - 1)
        span = flat[cursor][0] - flat[prev][0]
        ratio = 0.0 if span <= 1e-9 else (want - flat[prev][0]) / span
        y = flat[prev][1] + (flat[cursor][1] - flat[prev][1]) * ratio
        out.append((t, (y - y_min) / (y_max - y_min)))
    return out


# ==========================================================================
# 采样结果 -> .tres 文本
# ==========================================================================
def _num(value):
    """.tres 里的浮点写法:整数就写整数(与 Godot 自己写出来的一致)。"""
    rounded = round(float(value), 5)
    if abs(rounded - round(rounded)) < 1e-6:
        return str(int(round(rounded)))
    return f"{rounded:g}"


def _fnum(value):
    """**必须是浮点**的字段用这个:0 要写成 `0.0`。

    ── 踩过的坑(2026-10-04,.tres 加载实测)─────────────────────────
    Godot 的 `Curve._data` 是 Variant 数组,装载时**逐个检查类型**:
    切线必须是 FLOAT。而 `0` 在 .tres 里是 **INT** —— 平坦段(斜率 0)
    恰好会写出 `0`,于是加载时报
        Condition "p_input[i + 2].get_type() != Variant::FLOAT" is true
    整条曲线回退成"全是 0"的直线(采样恒为 0)。所以切线一律带小数点。
    """
    text = f"{round(float(value), 5):g}"
    if "." not in text and "e" not in text and "E" not in text:
        text += ".0"
    return text


def curve_tres_text(points):
    """`Curve` 资源的文本(点必须是 (t, v) 且 t 递增)。"""
    count = len(points)
    entries = []
    for index, (t, v) in enumerate(points):
        if index == 0:
            slope = ((points[1][1] - v) / max(points[1][0] - t, 1e-6)
                     if count > 1 else 0.0)
            left = right = slope
        elif index == count - 1:
            slope = (v - points[index - 1][1]) / max(
                t - points[index - 1][0], 1e-6)
            left = right = slope
        else:
            left = (v - points[index - 1][1]) / max(
                t - points[index - 1][0], 1e-6)
            right = (points[index + 1][1] - v) / max(
                points[index + 1][0] - t, 1e-6)
        # 切线写"斜率的 2 倍"、模式写 1(切线由邻居算)——
        # 与 Godot 自己保存出来的样本逐字一致(见模块开头的探测结论)
        entries.append(
            f"Vector2({_num(t)}, {_num(v)}), {_fnum(left * 2.0)}, "
            f"{_fnum(right * 2.0)}, 1, 1")
    return ("[gd_resource type=\"Curve\" format=3]\n"
            "\n"
            "[resource]\n"
            f"_data = [{', '.join(entries)}]\n"
            f"point_count = {count}\n")


def curve3d_tres_text(points, bake_interval=0.05):
    """`Curve3D` 资源的文本(点必须是 (x, y, z))。"""
    flat = []
    for x, y, z in points:
        # 每点三组:入柄、出柄、位置。柄写 0 = 让 Godot 自己按平滑样条算
        # (实测 sample_baked 走得又顺又准,见模块开头)
        flat.extend(("0", "0", "0", "0", "0", "0",
                     _num(x), _num(y), _num(z)))
    tilts = ", ".join("0" for _ in points)
    return ("[gd_resource type=\"Curve3D\" format=3]\n"
            "\n"
            "[resource]\n"
            f"bake_interval = {bake_interval:g}\n"
            "_data = {\n"
            f"\"points\": PackedVector3Array({', '.join(flat)}),\n"
            f"\"tilts\": PackedFloat32Array({tilts})\n"
            "}\n"
            f"point_count = {len(points)}\n")


def curve_files(directory, assets):
    """算出要写的曲线资源:**(曲线名, 路径, 文本)** 列表,不落盘。

    返回文本而不是直接写,是给导出流程用的 —— 那边所有文件一起走
    "先备齐、再统一落盘"的事务(见 exporter.FileTransaction),曲线也得
    跟着回滚。内容采不到(点太少)的曲线直接跳过:脚本那边有兜底
    (找不到资源时退回直线过渡 / 线性缓动,不会把游戏搞崩)。
    """
    out = []
    for name, asset in (assets or {}).items():
        points = asset.get("points") or []
        if len(points) < 2:
            continue
        kind = asset.get("kind", "Curve")
        text = (curve3d_tres_text(points) if kind == "Curve3D"
                else curve_tres_text(points))
        path = os.path.join(directory, CURVES_DIR, curve_file_name(name))
        out.append((name, path, text))
    return out


def write_curves(directory, assets):
    """把本次导出用到的曲线全部落盘。返回 [(曲线名, 文件路径), ...]。"""
    written = []
    for name, path, text in curve_files(directory, assets):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        written.append((name, path))
    return written