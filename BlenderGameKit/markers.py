"""线框标记物体:玩家出生点 / 区域环境点 / 关卡父级(第一百二十四轮)。

用户原话:"出生点 / 区域环境点 / 关卡父级这种,做成视口里看得见的线框标记物体,
属性挂在物体属性里,别塞进游戏逻辑侧栏。"

── 为什么单独一个模块(而不是塞进 properties.py)─────────────────
标记的"kind / 中文标签 / 图标 / 线框颜色"要同时被四方用:
    * properties.py  —— 物体属性里的下拉(items)
    * persistent_player.py / asset_export.py —— 按 kind 找物体、导出
    * ui.py / operator_io.py —— 画线框、上色、『创建标记』菜单
三个地方各写一份字符串,改一个名字就会漏两处。所以这里放**单一定义源**
(KINDS),别处只准从这里取。

── 为什么模块顶层不 import bpy(踩过一次的边界)─────────────────
`tests/test_codegen.py` 是**离线**跑的(Python 桩,没有 bpy),而
properties.py 在导入期就要引用本模块的 KINDS。顶层 `import bpy` 会让
整个离线用例集收集失败 —— 所以 bpy 只在函数里按需 import;下面这些
函数全部只用 `getattr` 访问传入的对象,离线桩也能跑。
"""

# ── 三种标记的 kind(值会存进 .blend,不要改名)────────────────────
KIND_PLAYER_START = "PLAYER_START"
KIND_ENV_ZONE = "ENV_ZONE"
KIND_LEVEL_ROOT = "LEVEL_ROOT"

# ── 单一定义源:(kind, 中文标签, 图标, 说明, 线框颜色 RGBA)────────
# 颜色是给视口线框 / 面板小色块用的:出生点绿(从这里开始)、
# 区域蓝(氛围)、关卡父级橙(容器)。三种颜色在深色视口里都能看清。
KINDS = (
    (KIND_PLAYER_START, "玩家出生点", "EMPTY_ARROWS",
     "玩家每次进这一关的出生位置(和场景设置里的『玩家出生点』是同一个东西,"
     "在视口里放一个标记更直观)",
     (0.15, 0.85, 0.35, 1.0)),
    (KIND_ENV_ZONE, "区域环境点", "WORLD",
     "玩家走进去就切一整套环境氛围(背景 / 环境光 / 雾 / 泛光 / 色调),"
     "走出来还原;范围 = 标记物体的盒子大小(拖缩放)",
     (0.30, 0.65, 1.00, 1.0)),
    (KIND_LEVEL_ROOT, "关卡父级", "OUTLINER_COLLECTION",
     "把它的直接子物体在 Godot 里归到一个同名 Node3D 下面 —— "
     "整关一起搬动 / 切换 / 显隐(标记物体自己是空的,不放几何)",
     (1.00, 0.65, 0.15, 1.0)),
)

KIND_IDS = tuple(entry[0] for entry in KINDS)
KIND_LABELS = {kind: label for kind, label, _icon, _desc, _color in KINDS}
KIND_ICONS = {kind: icon for kind, _label, icon, _desc, _color in KINDS}
KIND_DESCRIPTIONS = {kind: desc for kind, _label, _icon, desc, _color in KINDS}
KIND_COLORS = {kind: color for kind, _label, _icon, _desc, color in KINDS}

# ── 物体属性上那两个名字 ────────────────────────────────────────────
# `gk_marker_kind` 是**真属性**(ui.py 的面板 / 建标记算子按这个名字读写);
# `marker_kind` 是 task 描述里写的那个名字,同样是真属性,两个靠 update
# 回调互相同步(见 sync_update)—— 这样谁先写都能读对,不会有第二个真值源。
MARKER_PROPERTY = "gk_marker_kind"
MARKER_PROPERTY_ALIAS = "marker_kind"
# ui.py 建标记时还会打一个自定义属性(存 .blend 里不丢);
# 老文件 / 手工打的标记可能**只有**它,所以读取时也要认。
MARKER_CUSTOM_PROPERTY = "gk_marker"

# ── "没有标记"的值为什么不是空字符串(Blender 4.5 实测)──────────────
# 枚举项写成 ("", "无", …) 时,**Blender 会把这一项从标识符表里丢掉**:
#   * 读默认值会刷 `pyrna_enum_to_py: current value '0' matches no enum`
#     (探针实测 1858 条,扫一遍场景就刷屏);
#   * 写回去直接抛 `TypeError: enum "" not found in ('A', 'B')`。
# 所以"无"用一个真实标识符 NONE,`kind_of()` 统一把 NONE 归成 ""。
NONE_VALUE = "NONE"
_PROPERTY_VALUES = KIND_IDS + (NONE_VALUE,)


def sync_update(source, target):
    """返回一个 update 回调:把 `source` 属性的值同步到 `target`。

    两个属性名(gk_marker_kind / marker_kind)必须永远一致 —— 面板改一个、
    导出读另一个就会"面板显示改了、导出没反应"。递归用模块级开关挡掉
    (setattr 会再触发对方的 update,那一层必须立刻返回)。
    """
    def _update(self, _context=None):
        if _SYNCING[0]:
            return
        _SYNCING[0] = True
        try:
            value = str(getattr(self, source, "") or "")
            if value not in _PROPERTY_VALUES:
                value = NONE_VALUE
            if str(getattr(self, target, "") or "") != value:
                setattr(self, target, value)
        except Exception:                                 # noqa: BLE001
            pass
        finally:
            _SYNCING[0] = False
    return _update


_SYNCING = [False]

# ── Godot 侧的命名常量(导出 / 运行时脚本 / 面板提示共用一处)────────
# 出生点节点名必须叫这个:`persistent_player.WORLD_SCRIPT` 里
# `level.get_node_or_null("GKPlayerStart")` 是硬编码的(切换关卡时把角色
# 摆到出生点),改名会让"换关后角色落在原点"。
PLAYER_START_NODE = "GKPlayerStart"
# 区域环境点节点前缀:Godot 里一眼能认出哪些节点是 Blender 的区域标记。
ENV_ZONE_PREFIX = "GKEnvZone_"
# 区域环境点在 Godot 里进的组(AI / 调试想找所有区域时用它)。
ENV_ZONE_GROUP = "gk_env_zone"
# 关卡父级组节点名撞上保留名(Colliders / Particles…)或彼此重名时加的后缀。
LEVEL_ROOT_SUFFIX = "_LevelRoot"

# 区域盒子的最小边长(米):拖成 0 会让 Area3D 收不到任何 body,
# 用户看到的是"区域摆设了但没反应",给个下限比静默失灵好。
MIN_ZONE_SIZE = 0.05
# 拿不到任何尺寸信息时的退化值(1 米见方)。
ZONE_FALLBACK_SIZE = (1.0, 1.0, 1.0)
# 『过渡秒』允许的范围(和 properties.py 上的一致,导出侧再钳一次)。
MAX_ZONE_FADE = 5.0


def enum_items():
    """物体属性里那个下拉的 items:`[("NONE", 无, …)] + 三种标记`。

    NONE = 普通物体(默认)。**不能写成空标识符** —— 见上面 NONE_VALUE
    那段实测说明。
    """
    return [(NONE_VALUE, "无", "普通物体(照常导出几何与碰撞)")] + [
        (kind, label, description)
        for kind, label, _icon, description, _color in KINDS]


def label_of(kind):
    """kind -> 中文标签(不是标记时返回"无")。"""
    return KIND_LABELS.get(str(kind or ""), "无")


def icon_of(kind):
    """kind -> Blender 图标名(不是标记时给一个中性的)。"""
    return KIND_ICONS.get(str(kind or ""), "OBJECT_DATAMODE")


def color_of(kind):
    """kind -> 线框颜色 RGBA(不是标记时给灰色)。"""
    return KIND_COLORS.get(str(kind or ""), (0.6, 0.6, 0.6, 1.0))


def kind_of(obj):
    """物体身上的标记 kind;任何异常 / 不是标记一律返回 ""。

    ── 三个来源都认(为什么不是只读一个)──────────────────────────
    * `gk_physics.gk_marker_kind`  —— 面板里的下拉(真属性,导出以它为准);
    * `gk_physics.marker_kind`     —— 同一个东西的别名(靠 update 同步);
    * `obj["gk_marker"]`           —— 建标记时打的自定义属性,老文件 /
      手工打的标记可能只有它(ui.py 的 marker_kind_of 也认这两个来源)。
    前两个都读不到"有效 kind"时才看第三个 —— 顺序 = 优先级。

    全部 getattr + 兜底的理由:老 .blend 里的物体可能属于**另一个包路径**
    的 GKObjectSettings(见 __init__.register 里"无条件重新挂"那段),
    那种旧对象上没有这些属性;导出 / 面板每帧都问这个函数,
    这里一抛异常,整个导出就断在一半。
    """
    if obj is None:
        return ""
    physics = getattr(obj, "gk_physics", None)
    for attribute in (MARKER_PROPERTY, MARKER_PROPERTY_ALIAS):
        try:
            raw = str(getattr(physics, attribute, "") or "")
        except Exception:                                 # noqa: BLE001
            raw = ""
        if raw in KIND_IDS:
            return raw
    try:
        raw = str(obj.get(MARKER_CUSTOM_PROPERTY, "") or "")
    except Exception:                                     # noqa: BLE001
        raw = ""
    return raw if raw in KIND_IDS else ""


def set_kind(obj, kind):
    """把物体设成某种标记(kind 为空 / 非法 = 取消标记)。返回最终值。

    给 UI / 算子用:它们可能传 ""(取消标记),而属性上那个值是 NONE ——
    这里统一转一次,免得调用方自己去记 NONE_VALUE 这个内部约定。
    """
    value = str(kind or "")
    if value not in KIND_IDS:
        value = NONE_VALUE
    physics = getattr(obj, "gk_physics", None)
    if physics is not None:
        for attribute in (MARKER_PROPERTY, MARKER_PROPERTY_ALIAS):
            if not hasattr(physics, attribute):
                continue
            try:
                setattr(physics, attribute, value)
            except Exception:                             # noqa: BLE001
                pass
    try:
        if value == NONE_VALUE:
            if MARKER_CUSTOM_PROPERTY in obj:
                del obj[MARKER_CUSTOM_PROPERTY]
        else:
            obj[MARKER_CUSTOM_PROPERTY] = value
    except Exception:                                     # noqa: BLE001
        pass
    return value


def objects(scene, kind):
    """本场景 `scene.objects` 里 kind 匹配的物体(顺序 = 场景顺序)。"""
    if scene is None or not kind:
        return []
    try:
        items = list(scene.objects)
    except Exception:                                     # noqa: BLE001
        return []
    return [obj for obj in items if kind_of(obj) == kind]


def player_start(scene):
    """本关的出生点标记(按名字排序取第一个);没有就 None。

    ── 为什么要排序(而不是"取场景里第一个")───────────────────────
    对象顺序决定了导出结果。用户放两个出生点时总得有一个说了算 ——
    按名字排序是**稳定**的:加一个物体、换个顺序都不会让出生点乱跳。
    名字最靠前的那个赢(Cube < Sphere 之类是名字,不是摆放位置)。
    """
    found = objects(scene, KIND_PLAYER_START)
    if not found:
        return None
    return sorted(found, key=lambda obj: str(getattr(obj, "name", "")))[0]


def env_zones(scene):
    """本关所有区域环境点(按名字排序 —— 导出文本稳定)。"""
    return sorted(objects(scene, KIND_ENV_ZONE),
                  key=lambda obj: str(getattr(obj, "name", "")))


def level_roots(scene):
    """本关所有关卡父级标记(按名字排序 —— 导出文本稳定)。"""
    return sorted(objects(scene, KIND_LEVEL_ROOT),
                  key=lambda obj: str(getattr(obj, "name", "")))


def zone_preset(obj):
    """区域环境点的预设名(没填 = "")。"""
    try:
        return str(getattr(getattr(obj, "gk_physics", None),
                           "env_preset", "") or "").strip()
    except Exception:                                     # noqa: BLE001
        return ""


def zone_restore(obj):
    """离开这片区域要不要还原氛围(默认 True = 还原)。"""
    try:
        return bool(getattr(getattr(obj, "gk_physics", None),
                            "env_restore", True))
    except Exception:                                     # noqa: BLE001
        return True


def zone_fade(obj):
    """过渡秒数(默认 0.35;负值 / 读不到 -> 默认,超大 -> 钳到上限)。

    钳在这里而不是只靠面板的 min/max:老 .blend 里的值不保证合法,
    而 NaN / 负数传给 Godot 的 Tween 会变成"永远过渡不完"。
    """
    try:
        value = float(getattr(getattr(obj, "gk_physics", None),
                              "env_fade", 0.35))
    except Exception:                                     # noqa: BLE001
        return 0.35
    if value != value:                                    # NaN
        return 0.35
    return min(MAX_ZONE_FADE, max(0.0, value))


def zone_missing_preset(obj):
    """是区域环境点、但预设名是空的 —— 导出时要报出来(见 asset_export)。"""
    return kind_of(obj) == KIND_ENV_ZONE and not zone_preset(obj)


def zone_size(obj):
    """区域盒子的世界轴对齐包围盒三边长度 `(dx, dy, dz)`。

    ── 为什么必须乘 scale(用户拖的就是缩放)───────────────────────
    区域范围由用户拖标记物体的缩放决定,所以尺寸**必须**含 scale;
    `bound_box` 是**局部**包围盒(不含 scale),走那条路就得乘
    `matrix_world`。盒子网格走这条路(它精确:8 个角点变换后取 AABB);
    其它形状(球 / 圆环 / 自建网格)没有"正交盒子"的语义,退回
    `dimensions`(它已经含 scale)。两条都拿不到就给 1 米见方。

    每轴取绝对值 + 下限 0.05:负数缩放(镜像)不能让 Godot 收到负尺寸,
    太小的区域收不到任何 body —— 静默失灵最难查。
    """
    size = _box_world_size(obj)
    if size is None:
        size = _dimensions_size(obj)
    if size is None:
        size = ZONE_FALLBACK_SIZE
    out = []
    for value in size:
        try:
            number = abs(float(value))
        except (TypeError, ValueError):
            number = MIN_ZONE_SIZE
        out.append(max(MIN_ZONE_SIZE, number))
    return (out[0], out[1], out[2])


def _is_box_mesh(obj):
    """这个网格是不是"盒子"(8 个顶点,且每个顶点都在局部包围盒的角上)。

    ── 为什么不按名字判断 ──────────────────────────────────────────
    复制出来的盒子叫 `Cube.001`、用户改过名的叫`关卡区域`、中文界面下
    又是别的名字 —— 名字靠不住。按**顶点位置**判断:盒子网格的每个顶点
    必然落在包围盒的 8 个角上;球 / 圆环 / 自建网格会被排除掉。
    """
    try:
        vertices = getattr(getattr(obj, "data", None), "vertices", None)
        if vertices is None or len(vertices) != 8:
            return False
        corners = [(float(c[0]), float(c[1]), float(c[2]))
                   for c in obj.bound_box]
        if len(corners) != 8:
            return False
        mins = [min(corner[axis] for corner in corners) for axis in range(3)]
        maxs = [max(corner[axis] for corner in corners) for axis in range(3)]
        for vertex in vertices:
            co = vertex.co
            for axis in range(3):
                value = float(co[axis])
                if (abs(value - mins[axis]) > 1e-5
                        and abs(value - maxs[axis]) > 1e-5):
                    return False
        return True
    except Exception:                                     # noqa: BLE001
        return False


def _box_world_size(obj):
    """盒子网格的世界 AABB 尺寸;不是盒子 / 拿不到 -> None。

    手写矩阵乘法,不 import mathutils:离线桩里没有它,而这个换算是
    "算错了也看不出来"的那类代码(区域大一圈小一圈),必须能离线测
    (和 asset_export._godot_position 同一个理由)。
    """
    if not _is_box_mesh(obj):
        return None
    try:
        matrix = obj.matrix_world
        rows = [[float(matrix[i][j]) for j in range(4)] for i in range(3)]
        points = []
        for corner in obj.bound_box:
            x, y, z = float(corner[0]), float(corner[1]), float(corner[2])
            points.append((rows[0][0] * x + rows[0][1] * y + rows[0][2] * z
                           + rows[0][3],
                           rows[1][0] * x + rows[1][1] * y + rows[1][2] * z
                           + rows[1][3],
                           rows[2][0] * x + rows[2][1] * y + rows[2][2] * z
                           + rows[2][3]))
        if len(points) < 2:
            return None
        return tuple(max(point[axis] for point in points)
                     - min(point[axis] for point in points)
                     for axis in range(3))
    except Exception:                                     # noqa: BLE001
        return None


def _dimensions_size(obj):
    """`obj.dimensions`(已含 scale);拿不到 / 全 0 -> None。"""
    try:
        size = tuple(float(value) for value in obj.dimensions[:3])
    except Exception:                                     # noqa: BLE001
        return None
    if max(abs(value) for value in size) <= 1e-9:
        return None
    return size


# ── 给 ui.py / operator_io.py 用的别名 ──────────────────────────────
# Lead 在 ui.py 里按这几个名字调用(那边还留了一份同名兜底实现,以防
# 插件被单独拷走)。名字保持和 task 描述一致,语义就是上面那两个函数。
MARKER_KINDS = KINDS
MARKER_KIND_IDS = KIND_IDS
marker_kind_of = kind_of
marker_spawn_in = player_start
