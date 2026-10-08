"""分组框(可折叠)。

Blender 原生的 `NodeFrame` 能把一组节点框起来(带标题 / 颜色 / 拖动整组),
但它**天生不能折叠** —— 图一大,几十个框全摊开,照样看不出骨架。
这里在 Frame 上补一层"可折叠":收起时把框内节点藏起来、框缩成一条标题栏;
展开时原样还原(位置 / 颜色 / 高度都退回收起前的样子)。

── 为什么单独一个模块 ────────────────────────────────────────────
折叠 / 展开全是"对节点读写",半点 `bpy` 也不需要(鸭子类型就够),
所以这段能离线跑单测 —— `operator_io` 做不到(它模块级就摸 `bpy.app`)。
模块里**不 import bpy**,只按属性名操作传进来的对象。

── 生成代码为什么不受影响 ────────────────────────────────────────
`NodeFrame` 从来不是 GK 节点(没有 `gk_is_event` 这些属性),codegen 的
遍历里本来就会跳过它;而 `node.hide` 只是个**显示开关**,连线一条不少。
所以"收起"前后生成的脚本必须**逐字一样** —— 这点由测试锁死,
免得以后有人"顺手"在那里加一句 `if node.hide: continue`。
"""

# 收起后只剩标题栏的高度(Blender 的 Frame 标题栏就 20 出头)
GROUP_BAR_HEIGHT = 30.0
# 没有记录过原高度时的兜底(老 .blend 里收着、属性被清掉这种情况)
DEFAULT_GROUP_HEIGHT = 120.0
# 收起态的框色:比正常(0.16, 0.30, 0.45)更深、更灰,一眼能看出"这里收着东西"
COLLAPSED_COLOR = (0.13, 0.15, 0.18)

_KEY_HEIGHT = "gk_group_height"
_KEY_COLOR = "gk_group_color"
_KEY_COLLAPSED = "gk_collapsed"


def _get(obj, key, default=None):
    """读自定义属性 —— 优先 `.get()`,退回 `[]`,再退回默认值。

    三种环境都得认:真 Blender 的 `bpy_struct.get()`、离线桩的 dict 式
    自定义属性、以及根本没实现自定义属性的替身对象。
    """
    getter = getattr(obj, "get", None)
    if callable(getter):
        try:
            value = getter(key, default)
        except Exception:                                 # noqa: BLE001
            value = default
    else:
        try:
            value = obj[key]
        except Exception:                                 # noqa: BLE001
            value = default
    return default if value is None else value


def _set(obj, key, value):
    obj[key] = value


def is_frame(node):
    """这个节点是不是分组框(Blender 原生 NodeFrame)。"""
    return getattr(node, "bl_idname", "") == "NodeFrame"


def parented_to(node, frame):
    """node 是不是**直接**框在 frame 里。

    ── 只能用 `==`,不能用 `is`(真机冒烟当场抓到)────────────────────
    Blender 每次读 `node.parent` 都会**新造一个 Python 包装对象** ——
    `node.parent is frame` 恒为 False,于是"收起"只把框缩了、一个节点
    都没藏(点开还是一堆东西,而且没有任何报错)。
    `bpy_struct` 的 `==` 比的是底下的 RNA 指针,这才是"同一个节点"。
    """
    parent = getattr(node, "parent", None)
    if parent is None:
        return False
    if parent is frame:
        return True
    try:
        return bool(parent == frame)
    except Exception:                                     # noqa: BLE001
        return False


def frames_in(tree):
    """图里所有的分组框(按出现顺序)。"""
    return [node for node in getattr(tree, "nodes", ()) or () if is_frame(node)]


def direct_children(tree, frame):
    """直接框在 frame 里的节点(不含嵌套框里的)。"""
    return [node for node in getattr(tree, "nodes", ()) or ()
            if parented_to(node, frame)]


def descendants(tree, frame):
    """frame 里的**全部**节点,含嵌套分组框里的(任意层)。"""
    out = []
    stack = [frame]
    while stack:
        current = stack.pop()
        for node in getattr(tree, "nodes", ()) or ():
            if not parented_to(node, current):
                continue
            out.append(node)
            if is_frame(node):
                stack.append(node)
    return out


def count_nodes(tree, frame):
    """框里有多少个节点(侧栏显示用)。"""
    return len(descendants(tree, frame))


def frame_of(node):
    """顺着 parent 往上找,返回**最近**那个包着 node 的分组框(没有就 None)。

    为什么要往上走:节点可以直接框在框里(`parent` = 那个 Frame),
    也可以框在**嵌套的**框里。用户点的是框里的节点、想收的通常是
    它所在的那一组,所以从节点一路往上找到第一个 Frame 就是答案。
    """
    parent = getattr(node, "parent", None)
    for _ in range(16):                                   # 防环(理论上不该有)
        if parent is None:
            return None
        if is_frame(parent):
            return parent
        parent = getattr(parent, "parent", None)
    return None


def is_collapsed(frame):
    return bool(_get(frame, _KEY_COLLAPSED, False))


def collapse(tree, frame):
    """收起一组:藏起框内节点、框缩成条。已经是收起态则什么都不做。

    返回"这次真的收了吗" —— 侧栏按钮靠它区分"点了但没变化"。
    """
    if is_collapsed(frame):
        return False
    # 先记下原样,展开时要退回去。**必须在改 height 之前记**。
    _set(frame, _KEY_HEIGHT, float(getattr(frame, "height", DEFAULT_GROUP_HEIGHT)
                                   or DEFAULT_GROUP_HEIGHT))
    try:
        _set(frame, _KEY_COLOR, [float(c) for c in getattr(frame, "color", ())])
    except Exception:                                     # noqa: BLE001
        pass
    # 嵌套的内层框也要一起藏 —— 它们同属这一组
    for node in descendants(tree, frame):
        node.hide = True
    frame.height = GROUP_BAR_HEIGHT
    try:
        frame.use_custom_color = True
        frame.color = COLLAPSED_COLOR
    except Exception:                                     # noqa: BLE001
        pass
    _set(frame, _KEY_COLLAPSED, True)
    return True


def expand(tree, frame):
    """展开一组:还原节点可见性、高度和配色。已经展开则什么都不做。"""
    if not is_collapsed(frame):
        return False
    for node in _revive(tree, frame):
        node.hide = False
    frame.height = float(_get(frame, _KEY_HEIGHT, DEFAULT_GROUP_HEIGHT)
                         or DEFAULT_GROUP_HEIGHT)
    stored = _get(frame, _KEY_COLOR, None)
    if stored is not None:
        try:
            if len(stored) >= 3:
                frame.color = tuple(float(c) for c in stored[:3])
        except Exception:                                 # noqa: BLE001
            pass
    _set(frame, _KEY_COLLAPSED, False)
    return True


def _revive(tree, frame):
    """展开时要恢复可见的节点。

    ── 内层自己收着的框**不要动**(踩过就会很烦)──────────────────
    外层展开时如果无脑把子孙全 `hide = False`,里面那个明明收着的组
    也会被一起摊开 —— 用户会以为"我收好的东西怎么自己开了"。
    所以碰到 `gk_collapsed` 的内层框就**不再往下走**。
    """
    out = []
    stack = [frame]
    while stack:
        current = stack.pop()
        for node in getattr(tree, "nodes", ()) or ():
            if not parented_to(node, current):
                continue
            out.append(node)
            if is_frame(node) and not is_collapsed(node):
                stack.append(node)
    return out


def toggle(tree, frame):
    """收起 <-> 展开。返回操作后是不是收起态。"""
    if is_collapsed(frame):
        expand(tree, frame)
        return False
    collapse(tree, frame)
    return True
