"""节点基类与共用工具。

三种节点形态:
    GKEventNode      只有输出执行流(程序入口:准备 / 物理帧 / 输入 …)
    GKStatementNode  有输入+输出执行流(动作:移动 / 播放动画 / 分支 …)
    GKExpressionNode 纯取值,没有执行流(常量 / 数学 / 比较 …)

输入取值统一走 ``input_value()``:
    引脚已连线   -> 交给 codegen 递归求上游表达式
    引脚未连线   -> 读同名属性(gk_prop_defs 声明)或引脚 default_value
``gk_input_props`` 记录"未连线时该读哪个属性"，这样节点可以用一个带单位、
能在节点体上直接调的 Blender 属性(如 速度 300.0)去对应一个输入引脚。

声明式属性
----------
Blender 的 RNA 只在**类注解**里识别 bpy.props，所以节点里写成:
    gk_prop_defs = (("gk_speed", "float", 300.0, "速度", "说明"), ...)
由 finalize() 在类创建后物化成 __annotations__ + 类属性。
同一份声明在离线测试(没有 bpy)下也成立，只是注解变成普通装饰。
"""

try:
    import bpy
    from bpy.props import (
        BoolProperty,
        EnumProperty,
        FloatProperty,
        FloatVectorProperty,
        IntProperty,
        PointerProperty,
        StringProperty,
    )
    _BPY = True
except Exception:  # pragma: no cover - 仅离线测试走到这里
    _BPY = False
    bpy = None

# 测试桩环境(gk_stub.install())会注册一个假的 bpy，此时 _BPY 也是 True，
# 但我们需要的是"能被普通 Python 实例化"的桩基类，而不是 bpy.types.Node。
# 用一个显式标记把两件事分开，避免测试台走到 Blender 分支。
_OFFLINE = False
try:
    import gk_stub as _gk_stub_marker  # 只有测试台会提供这个模块
    _OFFLINE = getattr(_gk_stub_marker, "ACTIVE", False)
except Exception:
    _OFFLINE = False

_USE_BPY_BASE = _BPY and not _OFFLINE

from .. import gk_sockets as S

# bl_idname -> 节点类，codegen 通过它反查节点语义
node_type_registry = {}


# ==========================================================================
# 常量与小工具
# ==========================================================================
TARGET_SELF = "__self__"

_GDSCRIPT_KEYWORDS = {
    "if", "else", "elif", "for", "while", "match", "break", "continue", "pass",
    "return", "class", "class_name", "extends", "func", "var", "const", "signal",
    "static", "enum", "self", "true", "false", "null", "and", "or", "not", "in",
    "is", "as", "void", "await", "yield", "preload", "load", "tool", "breakpoint",
}


def snake_case(text, fallback="param"):
    """把中文/英文标签转成合法的 GDScript 标识符。

    ── 中文**保留**,不再压成下划线(第 22 轮改,踩过的坑)──────────
    以前的写法是"非 ASCII 一律换成下划线",于是:
        『金币数』-> ""  -> 落到 fallback "v"
        『通关』  -> ""  -> 也落到 "v"
    两个**不同的变量**撞成同一个名字,生成 `var v: float = false`
    —— 类型和值都对不上,Godot 直接解析失败(实测)。

    实测确认 **Godot 4 支持 Unicode 标识符**(`var 金币数: int = 0` 能过
    `--check-only`),所以现在中文原样保留:变量名在生成的代码里
    仍然读得懂,也不会再撞名。
    """
    out = []
    for index, ch in enumerate(str(text)):
        if ch == "_" or ch.isalnum():
            # 字母数字(含中文/日文等 Unicode 字母)一律保留
            out.append(ch.lower() if (ch.isascii() and ch.isupper()
                                      and index > 0) else ch)
        else:
            out.append("_")
    name = "".join(out).strip("_")
    while "__" in name:
        name = name.replace("__", "_")
    if not name or name[0].isdigit():
        return fallback
    if name in _GDSCRIPT_KEYWORDS:
        return f"{name}_v"
    return name


def gd_string(text):
    """生成 GDScript 字符串字面量(转义引号、反斜杠、换行)。"""
    escaped = str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _is_plain_node_path(text):
    """路径的每一段是否都是合法的 GDScript 裸标识符。

    `$玩家` 这种写法 Godot 直接报错(裸路径只接受 ASCII 标识符)，
    必须退化成 `$"玩家"`。实测确认 `$"主相机"` 是合法语法。
    """
    if not text:
        return False
    for segment in text.split("/"):
        if not segment or not segment.isascii():
            return False
        if not (segment[0].isalpha() or segment[0] == "_"):
            return False
        if not all(ch.isalnum() or ch == "_" for ch in segment):
            return False
    return True


def safe_function_name(text, fallback="my_function"):
    """函数声明里的名字 -> 合法的 GDScript 函数名(用户 2026-10-05)。

    ── 为什么必须转一道(踩过的坑,和变量名同一族)────────────────────
    `snake_case` 会把**非 ASCII 字符整个压掉**:『治疗』『扣血』都会变成
    空 -> 落回 fallback —— 于是**两个中文函数名生成同一个 `func`**,
    Godot 直接报
        Parse Error: Function "my_function()" has already been defined
    整个脚本加载不了。所以这里压完之后按需挂一段**稳定摘要**,
    保证「不同声明名 -> 不同函数名」。
    """
    import zlib
    base = snake_case(str(text or ""), "")
    if not base:
        base = fallback
    # 名字里本来就有非 ASCII 的,压完可能撞车 -> 挂 8 位摘要区分
    raw = str(text or "")
    if any(ord(char) > 127 for char in raw):
        digest = zlib.crc32(raw.encode("utf-8")) & 0xFFFFFFFF
        return f"{base}_{digest:08x}"
    return base


def gd_node_path(raw):
    """把节点的目标值转换成 GDScript 里的节点访问表达式。"""
    text = (raw or "").strip()
    if not text or text == TARGET_SELF:
        return "self"
    if text.startswith("$") or text.startswith("get_node"):
        return text
    if text.startswith("/root/"):
        return f'get_node("{text}")'
    # 含中文(或其它非 ASCII)的节点名不能写成裸标识符，要用带引号的路径
    if not _is_plain_node_path(text):
        return f'$"{text}"'
    return f"${text}"


def target_pin(name="目标"):
    return S.pin(S.OBJECT, name, TARGET_SELF)


def node_candidates(tree, prop=None):
    """节点引用下拉列表的候选项。

    ── 列**场景物体**,不列节点图里的节点(用户 2026-10-03 的疑问)──────
    用户原话:"那个节点是你测试用的吗?……这选择节点上显示多的不知道选哪个"。
    截图里的下拉列的是『锁定鼠标』『设定初始视野』这些 **节点图里的节点名** ——
    而这里的引用指的是**导出后场景里的物体**(相机 / 角色 / 空节点),
    两者根本不是一回事:选了节点名,生成的代码会去找一个不存在的物体。
    现在列 `bpy.data.objects` 里的物体(带类型图标),这才是能选的东西。
    """
    items = [(TARGET_SELF, "自身(脚本所在节点)", "本脚本挂载的节点",
              "OBJECT_DATA", 0)]
    objects = None
    try:
        import bpy
        objects = list(getattr(bpy.data, "objects", ()) or ())
    except Exception:
        objects = None
    if objects is None:
        # 离线桩环境:退回旧行为(测试里只关心生成的代码,不关心下拉内容)
        if tree is None:
            return items
        for index, node in enumerate(getattr(tree, "nodes", ()) or (), start=1):
            if getattr(node, "gk_is_event", False):
                continue
            items.append((node.name, node.name, node.bl_label, "NODE", index))
        return items
    icons = {"CAMERA": "CAMERA_DATA", "MESH": "MESH_DATA", "LIGHT": "LIGHT",
             "EMPTY": "EMPTY_DATA", "ARMATURE": "ARMATURE_DATA",
             "CURVE": "CURVE_DATA"}
    index = 1
    for obj in objects:
        kind = str(getattr(obj, "type", "") or "")
        label = obj.name
        if kind == "CAMERA":
            label = f"{obj.name}   (相机)"
        elif kind == "MESH":
            label = f"{obj.name}   (网格)"
        elif kind == "EMPTY":
            label = f"{obj.name}   (空物体)"
        items.append((obj.name, label, f"场景物体:{kind}",
                      icons.get(kind, "OBJECT_DATA"), index))
        index += 1
    return items


# 自动模式的特殊取值 -> 界面上显示的说明文字
AUTO_LABELS = {
    "": "自动(用默认节点)",
    "AnimationPlayer": "自动($AnimationPlayer)",
}


def node_items_for(node, prop="gk_target"):
    """给某个节点生成"节点引用"下拉候选项(含"自身"与"自动")。

    相机类的引用(如『相机_跟随』的『相机』)语义和其他引用不同:
    它指的是**场景里的相机对象**,而节点图里的节点都不是相机。
    用户实测踩过:在下拉里选了『移动_按方向』,生成的代码去找一个叫
    "移动_按方向" 的场景节点 —— 找不到,相机就一直不动。
    所以这里给个说明文字,别让人以为"列表里随便挑一个就行"。
    """
    tree = getattr(node, "id_data", None)
    items = list(node_candidates(tree, prop))
    current = getattr(node, prop, None)
    if current in AUTO_LABELS:
        items.insert(1, (current, AUTO_LABELS[current], "不指定具体节点，用约定路径", "AUTO", 1))
    if prop == "gk_camera":
        items.insert(
            0, ("", "自动(找场景里第一个相机)", "推荐:导出的场景里相机是自动接上的", "AUTO", 0))
    return items


def draw_target_picker(node, layout, prop="gk_target"):
    """把节点引用画成"文本框 + 节点下拉菜单"。

    用文本框而不是 EnumProperty，是为了不依赖 Blender 的动态枚举回调;
    右侧的下拉菜单直接列出节点图里真实存在的节点，点一下即写入。
    """
    items = node_items_for(node, prop)
    current = getattr(node, prop, TARGET_SELF) or TARGET_SELF

    # ── 空值不画成一个"空框"(用户 2026-10-03:"这选择节点上显示多的
    #    不知道选哪个"、"看着像没填完")────────────────────────────────
    # 留空是有明确含义的:**自动** —— 相机就是"当前正在用的那台",
    # 角色就是"脚本自己"。所以空的时候直接把含义写出来,
    # 只在真的选了具体物体时才显示那个名字。
    row = layout.row(align=True)
    # 注意:没选过的时候值是 TARGET_SELF("__self__"),**不是**空字符串 ——
    # 所以两种都要认,否则画出来还是那个 `__self__` 文本框(用户看到的
    # "空框 / 不知道选哪个"就是它)
    if (not current) or current == TARGET_SELF:
        row.label(text="自动(自身 / 当前相机)", icon="AUTO")
    else:
        row.prop(node, prop, text="")
    op = row.operator("gk.pick_target", text="", icon="TRIA_DOWN")
    op.node_name = node.name
    op.prop_name = prop


def draw_ui_align_row(node, layout):
    """界面控件的『对齐』一行:对齐下拉 + 边距。

    ── 为什么单独抽一个(第三十七轮)──────────────────────────────────
    文本 / 进度条 / 按钮 / 图片 / 面板 五个节点的对齐栏长得一模一样,
    各自画一套的话改一处要改五处(前面相机工具栏已经吃过这个亏)。
    选了『左上(按 X / Y 摆)』时把 X / Y 也画出来 —— 那是老行为,
    用户不用去别处找;选了别的对齐就只画边距(那时 X / Y 不起作用)。
    """
    align = str(getattr(node, "gk_align", "TOP_LEFT") or "TOP_LEFT")
    row = layout.row(align=True)
    row.prop(node, "gk_align", text="")
    if align == "TOP_LEFT":
        row.prop(node, "gk_x", text="X")
        row.prop(node, "gk_y", text="Y")
    else:
        row.prop(node, "gk_margin", text="边距")


def inline_prop_entries(node):
    """节点体上要**自动画出来**的参数:返回 (属性名, 类型, 标签) 列表。

    ── 用户 2026-10-04:「参数直接画在节点体上」────────────────────
    节点声明的参数(gk_prop_defs)以前只有**手写了 draw_buttons** 的节点
    才看得到;没写的那几个(『打印_输出』的文本、『等待_秒』的真实时间…)
    参数是"隐身"的,用户根本不知道能改。
    所以基类给个兜底:没重写 draw_buttons 的节点,自动把声明的参数画出来。
    抽成纯函数是为了能离线测(离线桩没有 layout,画不了 UI)。
    """
    result = []
    for entry in getattr(node, "gk_prop_defs", ()) or ():
        name = entry[0]
        kind = entry[1] if len(entry) > 1 else "string"
        label = entry[3] if len(entry) > 3 else ""
        result.append((name, kind, label))
    return result


# ==========================================================================
# 声明式属性
# ==========================================================================
class GKProp:
    """离线测试用的属性占位符;在 Blender 内会被 bpy.props 覆盖。"""

    def __init__(self, default=None, name="", description="", items=None, **kwargs):
        self.default = default
        self.name = name
        self.description = description
        self.items = items
        self.kwargs = kwargs

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return obj.__dict__.get(self.name, self.default)

    def __set__(self, obj, value):
        obj.__dict__[self.name] = value


if _BPY:

    def prop_float(default=0.0, name="", description="", **kw):
        return FloatProperty(name=name, description=description, default=default, **kw)

    def prop_int(default=0, name="", description="", **kw):
        return IntProperty(name=name, description=description, default=default, **kw)

    def prop_bool(default=False, name="", description="", **kw):
        return BoolProperty(name=name, description=description, default=default, **kw)

    def prop_text(default="", name="", description="", **kw):
        return StringProperty(name=name, description=description, default=default, **kw)

    def prop_enum(items, default="", name="", description="", **kw):
        # ── items 是**函数**时不能给字符串默认值(踩过的坑)────────────
        # 动态枚举(候选在运行时才算出来,比如"这张树里有哪些状态")要求
        # 默认值是**整数下标**,给 "" 会直接抛:
        #     TypeError: EnumProperty(...): 'default' can only be an
        #     integer when 'items' is a function
        # 而注册失败是**整个节点**都注册不上(GKNodeAnimTransition 直接消失),
        # 连带它的所有属性都读成默认值 —— 表现是"转场怎么突然没条件了"。
        # 所以:函数型 items 就不传 default,交给 Blender 用第 0 项。
        if callable(items):
            return EnumProperty(items=items, name=name,
                                description=description, **kw)
        return EnumProperty(items=items, default=default, name=name,
                            description=description, **kw)

    def prop_color(default=(1.0, 1.0, 1.0, 1.0), name="", description="", **kw):
        # ── 为什么不是 ColorProperty ────────────────────────────────────
        # Blender **没有** bpy.props.ColorProperty。写成它会让 from bpy.props
        # import 整段失败，_BPY 变成 False，接着**所有**节点都注册不上
        # (实测:87 个节点全挂，报 missing bl_rna attribute)。
        # 颜色在 Blender 里就是"4 分量的浮点向量 + COLOR 语义"，
        # 所以正确的做法是 FloatVectorProperty(size=4, subtype="COLOR")。
        return FloatVectorProperty(name=name, description=description,
                                   default=default, size=4, subtype="COLOR", **kw)

    def prop_vector(default=(0.0, 0.0, 0.0), name="", description="", **kw):
        # size=3 同时覆盖 2D 与 3D:2D 项目只读前两个分量，用同一个属性省事，
        # 免得节点上出现两套"维度相关的"属性
        return FloatVectorProperty(name=name, description=description,
                                   default=default, size=3, subtype="XYZ", **kw)

else:

    def prop_float(default=0.0, name="", description="", **kw):
        return GKProp(default, name=name, description=description, **kw)

    def prop_int(default=0, name="", description="", **kw):
        return GKProp(default, name=name, description=description, **kw)

    def prop_bool(default=False, name="", description="", **kw):
        return GKProp(default, name=name, description=description, **kw)

    def prop_text(default="", name="", description="", **kw):
        return GKProp(default, name=name, description=description, **kw)

    def prop_color(default=(1.0, 1.0, 1.0, 1.0), name="", description="", **kw):
        return GKProp(default, name=name, description=description, **kw)

    def prop_vector(default=(0.0, 0.0, 0.0), name="", description="", **kw):
        return GKProp(default, name=name, description=description, **kw)

    def prop_enum(items, default="", name="", description="", **kw):
        return GKProp(default, name=name, description=description, items=items, **kw)


# 节点引用属性与文本属性在 RNA 层是同一种东西，只是个名字上的区分
def prop_node(default="", name="", description="", **kw):
    return prop_text(default, name=name, description=description, **kw)


def _bpy_has_action():
    """当前环境里有没有真的 `bpy.types.Action`。

    ── 为什么要探一下(踩过的坑)──────────────────────────────────
    离线测试桩会注册一个**假的** bpy,`_BPY` 也是 True,但它的
    `bpy.types` 里没有 `Action` —— 直接用会:
        AttributeError: module 'bpy.types' has no attribute 'Action'
    那会让**整张节点表**注册不上。所以这里探一次,没有就退回文本属性。
    """
    if not _BPY or bpy is None:
        return False
    try:
        return hasattr(bpy.types, "Action")
    except Exception:
        return False


def _action_poll(self, value):
    """PointerProperty 的 poll:只允许指向 Action。

    没有 poll 的话 Blender 会把所有 ID 类型都列出来(物体、材质、网格…),
    用户在选择器里能选到一堆跟动画无关的东西。
    """
    try:
        return isinstance(value, bpy.types.Action)
    except Exception:
        return False


def prop_action(default=None, name="", description="", **kw):
    """指向一个 **Action 数据块**的属性 —— 拿到 Blender 原生动作选择器。

    ── 为什么不自己做下拉(绕了一圈才找到这个)──────────────────────
    用户要求"选物体之后自动列出它的动作",我先后试过三条路:

      1. `EnumProperty` + 动态 items 回调  -> **Blender 不允许**:
         `'default' can only be an integer when 'items' is a function`
         动态 items 的默认值只能是整数下标,而我们要的是动作名字符串。
      2. 文本框 + 『选…』按钮弹搜索列表 -> 能用,但用户仍然要**手点一下**
         ("还是需要自己填才会出现")。
      3. **`PointerProperty(type=bpy.types.Action)`** -> ✅ 就是它。

    第 3 条才是正解:UPBGE 那个截图里的"动作"栏其实就是 **Blender 原生的
    数据块选择器**(带放大镜、能搜索、能新建、能浏览全部)。用指针属性
    直接把这个原生控件拿到手,不用自己造。

    存的是数据块引用而不是名字,导出时取 `action.name` ——
    顺带免疫"用户改了动作名、节点里的字符串忘了改"这类问题。
    """
    if not _bpy_has_action():
        # 离线测试 / 桩环境:退回普通属性占位符
        return GKProp(default, name=name, description=description, **kw)
    return PointerProperty(type=bpy.types.Action, poll=_action_poll,
                           name=name, description=description, **kw)


_PROP_FACTORY = {
    "float": prop_float,
    "int": prop_int,
    "bool": prop_bool,
    "string": prop_text,
    "node": prop_node,
    "color": prop_color,
    "float_vector": prop_vector,
    "action": prop_action,
}


def _on_pin_count_changed(node, _context=None):
    """动态引脚节点的"数量"属性变了 -> 立刻重建引脚。

    ── 为什么需要这个(蓝图的体感就在这里)──────────────────────────
    节点刚写出来时,`序列` 的出口数量只能靠 `build_sockets()` 手动刷新 ——
    用户把"出口数量"从 3 调到 5,**节点上看不到第 4、5 个出口**,
    得重新加载文件才出现。那不是蓝图该有的手感:
    蓝图里改数字,引脚**立刻**长出来。

    所以给这些属性挂上 Blender 的 `update` 回调,改一次重建一次。
    找不到 `build_sockets` 的节点会被静默跳过(普通节点不受影响)。

    ── 必须走 rebuild_sockets,不能直接 build_sockets(踩过的坑)──────
    `build_sockets()` 里第一句就是 `inputs.clear()` —— 它**会连带删掉
    引脚上的连线**。所以改数量要走 `rebuild_sockets()`,那里会先把连线
    记下来、重建完再照原名接回去(见它的说明)。
    实测踩过:动画状态的『入口数量』从 2 调到 3,结果这个状态所有
    已接好的转场**全断了**,导出时报一堆"不知道从哪个状态切过来"。
    """
    rebuild = getattr(node, "rebuild_sockets", None)
    if rebuild is None:
        rebuild = getattr(node, "build_sockets", None)
    if rebuild is None:
        return
    try:
        rebuild()
    except Exception:
        # 重建失败不能让整个属性编辑崩掉 —— 引脚保持原样,用户还能撤销。
        pass
    # 通知节点树重绘,否则引脚要等下一次鼠标移动才画出来
    tree = getattr(node, "id_data", None)
    updater = getattr(tree, "update", None)
    if updater is not None:
        try:
            updater()
        except Exception:
            pass


# 这些属性一变就重建引脚(蓝图式动态引脚)。
# 值是**属性名前缀**:只要属性名里含这些词,就自动挂上重建回调。
PIN_COUNT_HINTS = ("count", "steps", "cases", "branches", "出口", "分支")


def _wants_pin_rebuild(prop_name):
    lowered = prop_name.lower()
    return any(hint in lowered for hint in PIN_COUNT_HINTS)


def materialize_props(cls):
    """把 gk_prop_defs 转成真实(Blender 可识别)的属性声明。"""
    prop_map = {}
    annotations = dict(getattr(cls, "__annotations__", {}) or {})
    for entry in getattr(cls, "gk_prop_defs", ()) or ():
        prop_name = entry[0]
        kind = entry[1]
        default = entry[2] if len(entry) > 2 else None
        label = entry[3] if len(entry) > 3 else ""
        desc = entry[4] if len(entry) > 4 else ""
        # 声明里可以带第六项:额外 kwargs(比如 min/max/update)
        extra = dict(entry[6]) if len(entry) > 6 and isinstance(entry[6], dict) else {}
        if kind == "enum":
            items = entry[5] if len(entry) > 5 else ()
            # 枚举也要能吃第六项的 kwargs —— 『逻辑_布尔运算』换运算方式时
            # 要把引脚从 A+B 变成单个 A(NOT),靠的就是这里的 update 回调。
            # 以前这里漏了 **extra,枚举上的 update 会被静默丢掉。
            descriptor = prop_enum(
                items,
                default if default is not None else (items[0][0] if items else ""),
                name=label,
                description=desc,
                **extra,
            )
        else:
            if _wants_pin_rebuild(prop_name) and "update" not in extra:
                extra["update"] = _on_pin_count_changed
            descriptor = _PROP_FACTORY.get(kind, prop_text)(
                default, name=label, description=desc, **extra)
        annotations[prop_name] = descriptor
        setattr(cls, prop_name, default)
        prop_map[prop_name] = entry
    cls.__annotations__ = annotations
    cls.gk_prop_map = prop_map
    return cls


def finalize(cls):
    """节点类装饰器:物化声明式属性 + 登记到类型表。"""
    materialize_props(cls)
    node_type_registry[cls.bl_idname] = cls
    return cls


def lookup_node_type(bl_idname):
    return node_type_registry.get(bl_idname)


# ==========================================================================
# 基类
# ==========================================================================
def build_declared_sockets(node):
    """按 gk_inputs / gk_outputs 声明创建引脚。两个分支共用。

    引脚描述是 gk_sockets.pin() 生成的字典 {kind, name, default}。
    """
    for spec in node.gk_inputs:
        socket = node.inputs.new(spec["kind"][0], spec["name"])
        if spec["default"] is not None:
            try:
                socket.default_value = spec["default"]
            except Exception:
                pass
    for spec in node.gk_outputs:
        socket = node.outputs.new(spec["kind"][0], spec["name"])
        if spec["default"] is not None:
            try:
                socket.default_value = spec["default"]
            except Exception:
                pass


if _USE_BPY_BASE:

    class _GKNodeBase(bpy.types.Node):
        """所有 GK 节点的公共基类。"""

        gk_category = "杂项"
        gk_inputs = ()          # pin() 字典序列 {kind, name, default}
        gk_outputs = ()
        gk_input_props = {}     # 引脚名 -> 属性名(未连线时读属性)
        gk_prop_defs = ()       # (属性名, 类型, 默认值, 标签, 说明[, 枚举项])
        gk_is_event = False
        gk_data_only = False
        # 从"添加节点"菜单里隐藏 —— 旧版节点保留兼容用(见 node_menu_entries)
        gk_hidden = False
        # 这个节点属于哪种节点树。默认游戏逻辑树;
        # 动画系统节点设成 "GKAnimationTree" —— 两套节点不该互相污染菜单。
        #
        # 也可以给一个**元组**表示"两张树里都能用":转场条件是布尔判断,
        # 用户会想在动画树里直接拼 `按住A 与 速度>阈值`,而不是回到玩法图
        # 去算。见 properties.node_menu_entries 的同一段过滤逻辑。
        gk_tree_type = "GKGameLogicTree"

        @classmethod
        def gk_tree_kinds(cls):
            """把 gk_tree_type 归一成元组(单字符串 = 只有一种)。"""
            kinds = cls.gk_tree_type
            if isinstance(kinds, str):
                return (kinds,)
            return tuple(kinds)

        @classmethod
        def poll(cls, node_tree):
            return node_tree.bl_idname in cls.gk_tree_kinds()

        # ---- 引脚 ----
        def init(self, context):
            self.build_sockets()

        def build_sockets(self):
            build_declared_sockets(self)

        def rebuild_sockets(self):
            """动态引脚(序列出口数量、动画状态的入口数量…)用。

            ── 为什么要先把连线记下来(踩过的坑)────────────────────────
            `inputs.clear()` 会**连带删掉引脚上的所有连线**。用户把
            『出口数量』从 3 调到 4,本来只是想多一个出口 —— 结果这个节点
            上已经接好的线**全断了**,而且没有任何提示(引脚只是空了)。
            『动画_状态』的入口数量更要命:断掉的是"哪条转场指向我",
            用户会以为状态机坏了。

            所以重建时按**引脚名字**把连线记下来,重建完再照原名接回去;
            名字在新引脚里不存在的(比如出口从 5 减到 3)自然就没了 ——
            这也符合预期。
            """
            tree = getattr(self, "id_data", None)
            saved_inputs = []
            saved_outputs = []
            for socket in getattr(self, "inputs", ()) or ():
                for link in getattr(socket, "links", ()) or ():
                    saved_inputs.append((getattr(socket, "name", ""),
                                         getattr(link, "from_socket", None)))
            for socket in getattr(self, "outputs", ()) or ():
                for link in getattr(socket, "links", ()) or ():
                    saved_outputs.append((getattr(socket, "name", ""),
                                          getattr(link, "to_socket", None)))
            self.inputs.clear()
            self.outputs.clear()
            self.build_sockets()
            if tree is None:
                return
            for name, source in saved_inputs:
                target = self.inputs.get(name)
                if target is None or source is None:
                    continue
                try:
                    tree.links.new(source, target)
                except Exception:
                    pass
            for name, target in saved_outputs:
                source = self.outputs.get(name)
                if source is None or target is None:
                    continue
                try:
                    tree.links.new(source, target)
                except Exception:
                    pass

        # ---- 取值 ----
        def get_input_prop(self, socket_name, fallback=None):
            """该输入引脚"未连线"时对应的属性名。"""
            return self.gk_input_props.get(socket_name, fallback)

        def input_value(self, ctx, socket_name, fallback=None):
            """求某个输入引脚的值:已连线交付 codegen 递归,否则读属性/默认值。

            ── `socket_name=None` 要特判(2.5D 示例抓出来的坑)──────────
            『常量_数值』这类"没有输入引脚、只有属性"的节点会传 None 进来。
            离线的假 bpy 是个 dict,`.get(None)` 返回 None 没事;
            **真 Blender 的 `bpy_prop_collection.get(None)` 会抛异常**:
                returned a result with an exception set
            于是导出时报『节点『常量_数值』求值失败』,值变成 null ——
            295 个离线测试全绿也发现不了(它们用的是假 bpy)。
            """
            socket = None if socket_name is None else self.inputs.get(socket_name)
            if socket is not None and socket.is_linked:
                return ctx.expr_of(socket)
            prop = self.get_input_prop(socket_name)
            if prop and hasattr(self, prop):
                return getattr(self, prop)
            if socket is not None:
                return getattr(socket, "default_value", fallback)
            return fallback

        def input_text(self, ctx, socket_name, fallback=""):
            value = self.input_value(ctx, socket_name, fallback)
            return value if isinstance(value, str) else str(value)

        def input_float(self, ctx, socket_name, fallback=0.0):
            value = self.input_value(ctx, socket_name, fallback)
            return float(value) if isinstance(value, (int, float)) else fallback

        def input_bool(self, ctx, socket_name, fallback=False):
            return bool(self.input_value(ctx, socket_name, fallback))

        # ---- UI ----
        def draw_buttons(self, context, layout):
            """节点体上直接画出声明的参数(用户 2026-10-04)。

            ── 只在**没有重写** draw_buttons 的节点上生效 ──────────────
            重写了 draw_buttons 的节点(HUD、相机、动画…那一大批)自己
            已经把手上的参数画好了,基类这份不会跑,也就不会重复。
            没重写的那几个原来自动"隐身",现在补上。

            节点引用(『相机』『目标』这类)要用带下拉的选择器,不能拿裸
            文本框画,所以单独走 draw_target_picker。
            """
            for name, kind, label in inline_prop_entries(self):
                try:
                    if kind == "node":
                        draw_target_picker(self, layout, name)
                    else:
                        layout.prop(self, name, text=label)
                except Exception:
                    # 画不出来不能让整个节点编辑器崩掉,退化成一行标签
                    try:
                        layout.label(text=label or name)
                    except Exception:
                        pass

        def draw_buttons_ext(self, context, layout):
            box = layout.box()
            box.label(text=f"分类: {self.gk_category}", icon="INFO")
            if self.gk_input_props:
                col = box.column(align=True)
                for socket_name, prop_name in self.gk_input_props.items():
                    col.label(text=f"{socket_name} → {prop_name}", icon="DOT")

else:

    class StubSocketCollection(list):
        """离线测试用的引脚集合:接口与 Blender 的 NodeInputs/NodeOutputs 对齐。

        放在插件里而不是测试里，是为了让插件本身也能在没有 Blender 的环境中
        被实例化(用于回归测试)，并且只维护一套桩实现。
        """

        def __init__(self, node=None, is_output=False):
            super().__init__()
            self.node = node
            self.is_output = is_output

        def get(self, name, default=None):
            for socket in self:
                if socket.name == name:
                    return socket
            return default

        def new(self, bl_idname, name, default=None):
            socket = StubSocket(self.node, name, bl_idname, is_output=self.is_output)
            socket.default_value = default
            self.append(socket)
            return socket

        def clear(self):
            del self[:]

    class StubSocket:
        """离线测试用的引脚。links 里放 StubLink。"""

        def __init__(self, node, name, bl_idname, is_output=False):
            self.node = node
            self.name = name
            self.bl_idname = bl_idname
            self.bl_label = bl_idname
            self.default_value = None
            self.is_output = is_output
            self.links = []

        @property
        def is_linked(self):
            return bool(self.links)

    class _GKNodeBase:
        gk_category = "杂项"
        gk_inputs = ()
        gk_outputs = ()
        gk_input_props = {}
        gk_prop_defs = ()
        gk_is_event = False
        gk_data_only = False
        # 从"添加节点"菜单里隐藏 —— 旧版节点保留兼容用(见 node_menu_entries)
        gk_hidden = False

        def __init__(self):
            self.name = type(self).bl_label
            self.label = ""
            self.location = (0.0, 0.0)
            self.inputs = StubSocketCollection(self, False)
            self.outputs = StubSocketCollection(self, True)
            # 用声明里的默认值填出实例属性，行为与 Blender 一致
            for entry in type(self).gk_prop_defs:
                setattr(self, entry[0], entry[2] if len(entry) > 2 else None)
            self.build_sockets()

        # ---- 引脚(与 Blender 分支保持同样的语义，供 codegen 与测试使用) ----
        def build_sockets(self):
            build_declared_sockets(self)

        def rebuild_sockets(self):
            self.inputs.clear()
            self.outputs.clear()
            self.build_sockets()

        def draw_buttons(self, context, layout):
            pass

        def get_input_prop(self, socket_name, fallback=None):
            return self.gk_input_props.get(socket_name, fallback)

        def input_value(self, ctx, socket_name, fallback=None):
            """求某个输入引脚的值:已连线交付 codegen 递归,否则读属性/默认值。

            ── `socket_name=None` 要特判(2.5D 示例抓出来的坑)──────────
            『常量_数值』这类"没有输入引脚、只有属性"的节点会传 None 进来。
            离线的假 bpy 是个 dict,`.get(None)` 返回 None 没事;
            **真 Blender 的 `bpy_prop_collection.get(None)` 会抛异常**:
                returned a result with an exception set
            于是导出时报『节点『常量_数值』求值失败』,值变成 null ——
            295 个离线测试全绿也发现不了(它们用的是假 bpy)。
            """
            socket = None if socket_name is None else self.inputs.get(socket_name)
            if socket is not None and socket.is_linked:
                return ctx.expr_of(socket)
            prop = self.get_input_prop(socket_name)
            if prop and hasattr(self, prop):
                return getattr(self, prop)
            if socket is not None:
                return getattr(socket, "default_value", fallback)
            return fallback
            
        def input_text(self, ctx, socket_name, fallback=""):
            value = self.input_value(ctx, socket_name, fallback)
            return value if isinstance(value, str) else str(value)

        def input_float(self, ctx, socket_name, fallback=0.0):
            value = self.input_value(ctx, socket_name, fallback)
            return float(value) if isinstance(value, (int, float)) else fallback

        def input_bool(self, ctx, socket_name, fallback=False):
            return bool(self.input_value(ctx, socket_name, fallback))


class GKEventNode(_GKNodeBase):
    """程序入口节点。"""

    gk_is_event = True
    gk_category = "事件"
    gk_inputs = ()


class GKStatementNode(_GKNodeBase):
    """执行语句节点,默认一个"完成"输出。"""

    gk_is_event = False
    gk_outputs = (S.pin(S.FLOW, "完成"),)


class GKExpressionNode(_GKNodeBase):
    """纯取值节点,没有执行流。"""

    gk_category = "运算"
    gk_outputs = (S.pin(S.ANY, "输出"),)
    gk_data_only = True


GK_CLASSES = ()
