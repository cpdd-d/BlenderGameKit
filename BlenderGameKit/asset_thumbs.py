"""资产缩略图 —— 第六十六轮。

── 为什么缩略图要单独一个模块 ──────────────────────────────────────
用户要求:"要有资产缩略图这样可以显示是什么样子的资产"。而 Blender 的
**节点卡片本身画不了图片**(节点 UI 只认 label / prop / button 三种),
所以缩略图走**叠加绘制**:卡片在节点编辑器里预留一块地方,注册的绘制
回调按节点位置把图贴上去(见 `ui._draw_asset_thumbs`)。

本模块只负责回答一个纯问题:**"这个类型 + 这个路径,画什么?"**
    类型        画什么
    image       真图(Blender 图片数据块)
    model       真外观(OpenGL 离屏渲染的小图;渲染不了就退回色块 + 名字)
    curve       曲线形状(采样成一条折线)
    sound       波形(读 wav 头里的采样;读不了就退回色块 + 名字)

── 离线可测 ────────────────────────────────────────────────────────
`wave_preview()` / `project_points()` 是**纯函数**(标准库 + 数学),
不 import bpy,离线单测直接喂数据。
"""

import os
import struct

# 缩略图统一按这个像素尺寸取(叠加绘制再按缩放贴到卡片上)
THUMB_SIZE = 128

# 渲染出来的模型缩略图数据块名字前缀(存在 .blend 里,打包带走)
MODEL_THUMB_PREFIX = "GK缩略_"

# 采样点数(曲线 / 波形都用它)
PREVIEW_SAMPLES = 48


# ==========================================================================
# 纯函数(离线可测)
# ==========================================================================
def wave_preview(path, samples=PREVIEW_SAMPLES):
    """把一个 wav 文件读成一条归一化的波形(0..1 的浮点列表)。

    只认 **wav**(标准库 `wave` 能读);ogg / mp3 读不了就返回 []。
    读不了不该让缩略图罢工 —— 调用方会退回"色块 + 名字"。
    """
    try:
        import wave
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            width = handle.getsampwidth()
            channels = max(1, handle.getnchannels())
            if frames <= 0 or width not in (1, 2, 3, 4):
                return []
            raw = handle.readframes(min(frames, 4_000_000))
    except Exception:                                     # noqa: BLE001
        return []
    if not raw:
        return []
    per_sample = width * channels
    total = len(raw) // per_sample
    if total <= 0:
        return []
    buckets = max(1, int(samples))
    step = max(1, total // buckets)
    peaks = []
    for index in range(0, total, step):
        offset = index * per_sample
        chunk = raw[offset:offset + per_sample * step]
        peak = 0
        for position in range(0, len(chunk) - width + 1, per_sample):
            piece = chunk[position:position + width]
            if len(piece) < width:
                break
            if width == 1:
                value = piece[0] - 128
            elif width == 2:
                value = struct.unpack_from("<h", piece)[0]
            elif width == 3:
                value = int.from_bytes(piece + (b"\x00"
                                                if piece[2] < 128 else b"\xff"),
                                       "little", signed=True)
            else:
                value = struct.unpack_from("<i", piece)[0]
            peak = max(peak, abs(value))
        peaks.append(peak)
    if not peaks:
        return []
    biggest = max(peaks) or 1
    return [min(1.0, value / biggest) for value in peaks]


def project_points(points):
    """把一串 3D 点投成 2D 归一化坐标(0..1),给曲线预览用。

    ── 为什么这么投 ────────────────────────────────────────────────
    运镜轨道是空间曲线,而缩略图是个小方块。简单可靠的做法:丢掉
    **变化最小的那个轴**,把剩下两轴铺满方块 —— 不管这条曲线是
    平躺的还是立起来的,都能看出形状。
    """
    pts = [(float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0)
           for p in (points or [])]
    if len(pts) < 2:
        return []
    spans = []
    for axis in range(3):
        values = [p[axis] for p in pts]
        spans.append(max(values) - min(values))
    keep = sorted(range(3), key=lambda axis: spans[axis], reverse=True)[:2]
    keep.sort()
    ranges = {axis: (min(p[axis] for p in pts), max(p[axis] for p in pts))
              for axis in keep}
    out = []
    for point in pts:
        pair = []
        for axis in keep:
            low, high = ranges[axis]
            span = high - low
            pair.append(0.5 if span <= 1e-9 else (point[axis] - low) / span)
        out.append((pair[0], pair[1]))
    return out


def mirror_wave(peaks):
    """波形采样 -> 一条上下对称的折线(画起来像个声波)。"""
    if not peaks:
        return []
    out = []
    count = len(peaks)
    for index, value in enumerate(peaks):
        x = index / max(1, count - 1)
        out.append((x, 0.5 + 0.5 * value))
    for index in range(count - 1, -1, -1):
        x = index / max(1, count - 1)
        out.append((x, 0.5 - 0.5 * peaks[index]))
    return out


# ==========================================================================
# 需要 bpy 的部分
# ==========================================================================
def image_for(name):
    """按名字取一张 Blender 图片(拿不到返回 None)。"""
    try:
        import bpy
        return bpy.data.images.get(str(name or ""))
    except Exception:                                     # noqa: BLE001
        return None


def curve_points(name):
    """取一条曲线物体的控制点(世界坐标)。拿不到返回 []。"""
    try:
        import bpy
        obj = bpy.data.objects.get(str(name or ""))
        if obj is None or getattr(obj, "type", "") != "CURVE":
            return []
        matrix = obj.matrix_world
        data = getattr(obj, "data", None)
        out = []
        for spline in getattr(data, "splines", ()) or ():
            kind = str(getattr(spline, "type", ""))
            if kind == "BEZIER":
                source = [point.co for point in spline.bezier_points]
            elif kind == "NURBS":
                source = [point.co for point in spline.points]
            else:
                source = [point.co for point in getattr(spline, "points", ())]
            for co in source:
                world = matrix @ co
                out.append((world.x, world.y, world.z))
        return out
    except Exception:                                     # noqa: BLE001
        return []


def model_average_color(name):
    """一个资产模型的主色(取第一份材质的 Base Color;拿不到给中性灰)。"""
    fallback = (0.42, 0.48, 0.56, 1.0)
    try:
        import bpy
        from . import asset_export
        source = None
        for scene in getattr(bpy.data, "scenes", ()) or ():
            entry = asset_export.collect_assets(scene).get(str(name or ""))
            if entry and entry.get("source") is not None:
                source = entry["source"]
                break
        if source is None:
            return fallback
        data = getattr(source, "data", None)
        for material in getattr(data, "materials", ()) or ():
            if material is None or not getattr(material, "use_nodes", False):
                continue
            for node in getattr(material.node_tree, "nodes", ()) or ():
                if str(getattr(node, "type", "")) != "BSDF_PRINCIPLED":
                    continue
                socket = getattr(node, "inputs", {}).get("Base Color")
                if socket is None:
                    continue
                value = getattr(socket, "default_value", None)
                if value is not None and len(value) >= 3:
                    return (float(value[0]), float(value[1]),
                            float(value[2]), 1.0)
    except Exception:                                     # noqa: BLE001
        return fallback
    return fallback


def model_thumbnail_image(name):
    """模型缩略图(OpenGL 离屏渲染 cache 在 bpy.data.images 里)。

    渲染不了(没有 GPU / 后台模式)就返回 None —— 调用方退回色块。
    """
    existing = _existing_model_image(name)
    if existing is not None:
        return existing
    try:
        import bpy
    except Exception:                                     # noqa: BLE001
        return None
    return _render_model(bpy, name, MODEL_THUMB_PREFIX + str(name or ""))


def _existing_model_image(name):
    """已经渲染好的模型缩略图(没有就 None,**不会触发渲染**)。"""
    try:
        import bpy
        image = bpy.data.images.get(MODEL_THUMB_PREFIX + str(name or ""))
    except Exception:                                     # noqa: BLE001
        return None
    if image is not None and getattr(image, "size", (0, 0))[0]:
        return image
    return None


def refresh_missing_models(tree):
    """把资产图里**还没缩略图**的 3D 道具卡各拍一张。返回拍成的张数。

    在收录 / 登记这类**算子**里调(那里能跑 `bpy.ops`);绘制回调里
    **绝对不要**调 —— 每帧渲染会把界面拖死。

    ── 不重置"渲染不可用"的记号 ────────────────────────────────────
    这台机器渲染不了时,第一次会撞一次(知道结果),后面直接跳过 ——
    Blender 每次失败都会往控制台打一条红字,不能每张卡都打一遍。
    用户主动点『刷新缩略图』(`refresh_model_thumbnail`)才会重新试。
    """
    done = 0
    from .nodes import nodes as node_lib
    for kind, _name, path, _node in node_lib.card_entries(tree):
        if kind != "model" or _existing_model_image(path) is not None:
            continue
        if _RENDER_BLOCKED:
            break
        if model_thumbnail_image(path) is not None:
            done += 1
    return done


def refresh_model_thumbnail(name):
    """强制重拍一张模型缩略图(用户点『刷新缩略图』时用)。"""
    global _RENDER_BLOCKED
    _RENDER_BLOCKED = False       # 用户主动刷新 -> 再试一次
    try:
        import bpy
        existing = bpy.data.images.get(MODEL_THUMB_PREFIX + str(name or ""))
        if existing is not None:
            bpy.data.images.remove(existing)
    except Exception:                                     # noqa: BLE001
        pass
    return model_thumbnail_image(name)


# 这台机器上"OpenGL 离屏渲染"用不了(实测:后台模式没有 GL 环境)。
# 记住它,别每张卡都去撞一次 —— Blender 每次都会往控制台打一条红字。
_RENDER_BLOCKED = False


def rendering_unavailable():
    """这台机器上模型缩略图渲染是不是确定用不了(给界面提示用)。"""
    return _RENDER_BLOCKED


def _render_model(bpy, name, image_name):
    """OpenGL 离屏渲染:临时相机 + 小分辨率,拍完**原样还原**场景。

    失败(后台模式没有 GPU、物体不在场景里…)一律返回 None。
    """
    import tempfile

    from mathutils import Vector
    global _RENDER_BLOCKED
    if _RENDER_BLOCKED:
        return None                   # 已经知道这台机器渲染不了,别再撞
    scene = getattr(bpy.context, "scene", None)
    if scene is None:
        return None
    try:
        from . import asset_export
    except Exception:                                     # noqa: BLE001
        return None
    source = None
    for candidate in getattr(bpy.data, "scenes", ()) or ():
        entry = asset_export.collect_assets(candidate).get(str(name or ""))
        if entry and entry.get("source") is not None:
            source = entry["source"]
            break
    if source is None:
        return None

    render = scene.render
    saved = {
        "camera": scene.camera,
        "x": render.resolution_x, "y": render.resolution_y,
        "percent": render.resolution_percentage,
        "filepath": render.filepath,
        "format": getattr(render.image_settings, "file_format", "PNG"),
        "transparent": getattr(render, "film_transparent", False),
    }
    camera_data = bpy.data.cameras.new("GK缩略相机")
    camera = bpy.data.objects.new("GK缩略相机", camera_data)
    path = os.path.join(tempfile.gettempdir(),
                        f"gk_thumb_{abs(hash(str(name))) & 0xFFFFFFF}.png")
    try:
        scene.collection.objects.link(camera)
        scene.camera = camera
        render.resolution_x = THUMB_SIZE
        render.resolution_y = THUMB_SIZE
        render.resolution_percentage = 100
        render.film_transparent = True
        try:
            render.image_settings.file_format = "PNG"
        except Exception:                                 # noqa: BLE001
            pass
        corners = [source.matrix_world @ Vector(corner)
                   for corner in getattr(source, "bound_box", ())]
        if corners:
            low = Vector((min(c.x for c in corners), min(c.y for c in corners),
                          min(c.z for c in corners)))
            high = Vector((max(c.x for c in corners), max(c.y for c in corners),
                           max(c.z for c in corners)))
        else:
            low = source.location - Vector((1, 1, 1))
            high = source.location + Vector((1, 1, 1))
        center = (low + high) * 0.5
        radius = max(0.001, (high - low).length * 0.5)
        direction = Vector((1.0, -1.2, 0.8)).normalized()
        camera.location = center + direction * (radius * 2.6)
        look = center - camera.location
        camera.rotation_euler = look.to_track_quat("-Z", "Y").to_euler()
        render.filepath = path
        try:
            bpy.ops.render.opengl(write_still=True, view_context=False)
        except Exception:                                 # noqa: BLE001
            # 后台模式 / 没有 GL 环境 —— 记下来,后面不再重试(免得刷屏)
            _RENDER_BLOCKED = True
            return None
        if not os.path.isfile(path):
            return None
        image = bpy.data.images.load(path)
        image.name = image_name
        try:
            image.pack()          # 打包进 .blend,不再依赖那个临时文件
        except Exception:                                 # noqa: BLE001
            pass
        return image
    except Exception:                                     # noqa: BLE001
        return None
    finally:
        try:
            bpy.data.objects.remove(camera, do_unlink=True)
        except Exception:                                 # noqa: BLE001
            pass
        try:
            bpy.data.cameras.remove(camera_data, do_unlink=True)
        except Exception:                                 # noqa: BLE001
            pass
        scene.camera = saved["camera"]
        render.resolution_x = saved["x"]
        render.resolution_y = saved["y"]
        render.resolution_percentage = saved["percent"]
        render.filepath = saved["filepath"]
        try:
            render.image_settings.file_format = saved["format"]
        except Exception:                                 # noqa: BLE001
            pass
        render.film_transparent = saved["transparent"]
        try:
            if os.path.isfile(path):
                os.remove(path)
        except Exception:                                 # noqa: BLE001
            pass


def preview_for(kind, path):
    """这个类型 + 路径 -> 一份"能画"的缩略图数据。

    返回字典,`mode` 决定怎么画:
        {"mode": "image", "image": <Blender 图片>}    真图
        {"mode": "curve", "points": [(u, v), ...]}     折线
        {"mode": "wave",  "points": [(u, v), ...]}     波形
        {"mode": "swatch","color": (r, g, b, a), "text": "..."}  色块兜底
    拿不到任何东西时返回 None(什么都不画)。

    **本函数不会触发渲染** —— 它在绘制回调里被每帧调用。模型真图由
    `refresh_missing_models()` / `refresh_model_thumbnail()` 在算子里拍好,
    这里只查有没有现成的。
    """
    kind = str(kind or "")
    path = str(path or "").strip()
    if not path:
        return None
    if kind == "image":
        image = image_for(path)
        if image is not None and getattr(image, "size", (0, 0))[0]:
            return {"mode": "image", "image": image}
        return {"mode": "swatch", "color": (0.30, 0.34, 0.42, 1.0),
                "text": "图丢了"}
    if kind == "model":
        image = _existing_model_image(path)
        if image is not None:
            return {"mode": "image", "image": image}
        return {"mode": "swatch", "color": model_average_color(path),
                "text": path}
    if kind == "curve":
        points = curve_points(path)
        if len(points) >= 2:
            projected = project_points(points)
            if len(projected) >= 2:
                return {"mode": "curve", "points": projected}
        return {"mode": "swatch", "color": (0.30, 0.34, 0.42, 1.0),
                "text": path}
    if kind == "sound":
        peaks = wave_preview(path)
        if peaks:
            return {"mode": "wave", "points": mirror_wave(peaks)}
        return {"mode": "swatch", "color": (0.36, 0.30, 0.46, 1.0),
                "text": os.path.basename(path) or path}
    return None


# 缩略图缓存:绘制回调每帧都会问一次,波形的读盘 / 模型主色这两种
# **贵**的算法必须缓存。图像与曲线便宜,不缓存(换了要立刻看到)。
_PREVIEW_CACHE = {}


def _preview_stamp(kind, path):
    """缓存该不该失效 —— 变了就重算。"""
    if kind == "sound":
        try:
            return os.path.getmtime(path)
        except Exception:                                 # noqa: BLE001
            return 0
    if kind == "model":
        # 缩略图是**后来**才渲染出来的:图一出现就作废缓存
        return _existing_model_image(path) is not None
    return 0


def cached_preview(kind, path):
    """`preview_for` 的缓存版(给绘制回调用)。"""
    kind = str(kind or "")
    path = str(path or "").strip()
    if not path:
        return None
    if kind in ("image", "curve"):
        return preview_for(kind, path)
    key = (kind, path)
    stamp = _preview_stamp(kind, path)
    hit = _PREVIEW_CACHE.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    data = preview_for(kind, path)
    _PREVIEW_CACHE[key] = (stamp, data)
    return data
