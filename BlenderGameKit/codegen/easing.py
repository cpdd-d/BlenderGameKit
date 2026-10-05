"""缓动与曲线资源:节点图里"过渡曲线"的公共实现。

── 这一块解决什么(用户 2026-10-04 第十二轮)──────────────────────
用户的原始问题:"弹簧臂用一个曲线来从末尾到起点的位置变化来设计滑动切换
的效果……想要曲线运动的话还要在节点里连一大堆,这个怎么设计更好"。

结论(已和用户确认):不给用户一堆数学节点,而是让**一个节点**带上
『过渡曲线』这一栏 —— 内置常用的缓动档位(线性 / 缓入 / 缓出 / 缓入缓出 /
回弹),想要自己的形状就在 Blender 里**画一条曲线**选中它。导出时:

  * 内置档位  -> 生成 `Tween.interpolate_value` 的一句调用(引擎自带的
    TRANS/EASE 曲线,不占资源)
  * 自定义曲线 -> 采样成 Godot 的 `Curve` / `Curve3D` 资源(.tres),
    脚本运行时 `load()` 它,`sample(t)` 拿值

生成的两个辅助函数(按需生成,不用就不出现):

    func _gk_ease(t, trans, ease, curve = null) -> float      # 缓动求值
    func _gk_curve_load(path) / _gk_curve3d_load(path)        # 曲线资源加载

两个坑都记在 `curve_assets` 的模块说明里(用真 Godot 核对过):
`Curve3D.sample` 不可靠要用 `sample_baked`;`.tres` 里切线必须写成浮点。
"""

from .. import curve_assets
from .literals import gd_string


# 内置缓动档位 -> Godot 的 (TRANS_*, EASE_*)
# 前四个覆盖绝大多数运镜/动画需求;回弹用于"弹簧"手感。
EASE_PRESETS = {
    "LINEAR": ("TRANS_LINEAR", "EASE_IN_OUT"),
    "EASE_IN": ("TRANS_SINE", "EASE_IN"),
    "EASE_OUT": ("TRANS_SINE", "EASE_OUT"),
    "EASE_IN_OUT": ("TRANS_SINE", "EASE_IN_OUT"),
    "BACK": ("TRANS_BACK", "EASE_OUT"),
    "BOUNCE": ("TRANS_BOUNCE", "EASE_OUT"),
}

# 节点上『过渡曲线』枚举的公共定义(相机_切换 / 相机_弹簧臂 / 时间轴共用,
# 免得三个节点各写一份、改一处漏一处)
EASE_ITEMS = (
    ("LINEAR", "匀速", "从头到尾一样快(老图的行为,一字不变)"),
    ("EASE_IN", "缓入(慢起快停)", "起步慢、越走越快"),
    ("EASE_OUT", "缓出(快起慢停)", "起步快、到点前慢下来(最常用)"),
    ("EASE_IN_OUT", "缓入缓出(两头慢)", "电影运镜那种丝滑"),
    ("BACK", "回弹", "冲过头一点再回来(有点弹性)"),
    ("BOUNCE", "弹跳", "到点时弹一下"),
    ("CUSTOM", "自定义曲线(在 Blender 里画)",
     "在场景里画一条从左到右的曲线,导出成 Godot 的 Curve 资源"),
)


def ease_constants(preset):
    """档位 -> (`"Tween.TRANS_SINE"`, `"Tween.EASE_IN_OUT"`) 两个字面量。"""
    trans, ease = EASE_PRESETS.get(str(preset or "LINEAR"), EASE_PRESETS["LINEAR"])
    return f"Tween.{trans}", f"Tween.{ease}"


def needs_custom(preset):
    return str(preset or "") == "CUSTOM"


# ==========================================================================
# 曲线资源
# ==========================================================================
def assets(ctx):
    """本次生成用到的曲线资源表(懒初始化)。"""
    table = getattr(ctx, "curve_assets", None)
    if table is None:
        table = {}
        ctx.curve_assets = table
    return table


def register_curve_asset(ctx, name, kind):
    """把一条 Blender 曲线采样下来,登记进"本次导出要写的资产"。

    返回 res:// 路径;采不到(名字为空 / 不是曲线物体 / 点太少)时返回 "",
    并且**尽量给一句能照着改的告警** —— 静默返回空等于让用户自己猜。
    """
    text = str(name or "").strip()
    if not text:
        return ""
    if not curve_assets.curve_exists(text):
        ctx.warn(f"曲线『{text}』在场景里找不到(可能被改名或删掉了)—— "
                 f"这一处退回默认曲线。请用节点上的『选曲线』重新挑一条")
        return ""
    if kind == "Curve3D":
        points = curve_assets.sample_path_points(text)
        if len(points) < 2:
            ctx.warn(f"曲线『{text}』采不到点 —— 确认它是曲线物体、"
                     f"而且至少有 2 个控制点")
            return ""
        dims = curve_assets.curve_dimensions(text)
        if dims and dims != "3D":
            ctx.warn(f"曲线『{text}』画在 2D 平面上 —— 3D 相机会沿着"
                     f"z=0 平面上的轨道飞。想要空间轨道的话,"
                     f"把曲线数据的维度改成 3D")
    else:
        points = curve_assets.sample_ease_curve(text)
        if not points:
            ctx.warn(f"曲线『{text}』读不出缓动形状 —— 缓动曲线要画成"
                     f"**从左到右**、而且高度要有变化(最低点当 0、最高点当 1)")
            return ""
    assets(ctx)[text] = {"kind": kind, "points": points}
    return curve_assets.curve_res_path(text)


def curve_expr(ctx, name, kind="Curve"):
    """曲线资源在生成代码里的表达式(`Curve` / `Curve3D`)。

    不用 `preload`:资源**不存在**会让脚本连编译都过不了(preload 是编译期
    求值,整个脚本加载失败)。这里走运行时的 `load` + 缓存,资源缺失只是
    一条告警 + 退回默认行为,游戏照跑 —— 和相机引用的 `_gk_ref` 一个思路。
    """
    path = register_curve_asset(ctx, name, kind)
    if not path:
        return "null"
    need_curve_load_helper(ctx, kind)
    helper = "_gk_curve3d_load" if kind == "Curve3D" else "_gk_curve_load"
    return f"{helper}({gd_string(path)})"


def need_ease_helper(ctx):
    """生成 `_gk_ease`(缓动求值)。用一次生成一次,重复调用不会重复生成。"""
    if getattr(ctx, "_gk_ease_helper", False):
        return
    ctx._gk_ease_helper = True
    ctx.extra_sections.append({
        "signature": ("func _gk_ease(t: float, trans: int, ease: int, "
                      "curve: Curve = null) -> float:"),
        "lines": [
            "    # 自定义曲线优先(在 Blender 里画的形状);否则用引擎自带的",
            "    # TRANS/EASE 档位 —— Tween.interpolate_value 是静态函数,",
            "    # 不用建 Tween 也能拿到缓动后的比例",
            "    if curve != null:",
            "        return curve.sample(clampf(t, 0.0, 1.0))",
            "    return float(Tween.interpolate_value("
            "0.0, 1.0, t, 1.0, trans, ease))",
        ],
        "empty_body": "    return t",
        "separator": True,
    })


def need_curve_load_helper(ctx, kind="Curve"):
    """生成曲线资源加载函数(带缓存;资源缺失只告警不崩)。"""
    made = getattr(ctx, "_gk_curve_load_helpers", None)
    if made is None:
        made = set()
        ctx._gk_curve_load_helpers = made
    cache = ctx.declare_var("_gk_curve_cache", "Dictionary")
    if "res" not in made:
        made.add("res")
        ctx.extra_sections.append({
            "signature": "func _gk_curve_res(path: String) -> Resource:",
            "lines": [
                "    if path.is_empty():",
                "        return null",
                f"    if {cache}.has(path):",
                f"        return {cache}[path]",
                "    var res: Resource = null",
                "    if ResourceLoader.exists(path):",
                "        res = load(path)",
                "    else:",
                "        push_warning(\"[GK] 曲线资源不存在:%s —— 在 Blender "
                "里重新导出一次\" % path)",
                f"    {cache}[path] = res",
                "    return res",
            ],
            "empty_body": "    return null",
            "separator": True,
        })
    helper = "_gk_curve3d_load" if kind == "Curve3D" else "_gk_curve_load"
    key = helper
    if key in made:
        return
    made.add(key)
    ret = "Curve3D" if kind == "Curve3D" else "Curve"
    ctx.extra_sections.append({
        "signature": f"func {helper}(path: String) -> {ret}:",
        "lines": [
            "    var res: Resource = _gk_curve_res(path)",
            f"    return res as {ret}",
        ],
        "empty_body": "    return null",
        "separator": True,
    })


def apply_ease(ctx, node, raw_expr, prop="gk_trans", curve_prop="gk_curve",
               kind="Curve"):
    """把"原始比例"套上节点上选的曲线,返回生成代码里的表达式。

    * 档位 = 自定义曲线 -> `_gk_ease(raw, 0, 0, <曲线资源>)`
    * 其它档位        -> `_gk_ease(raw, Tween.TRANS_*, Tween.EASE_*)`
    * 没填自定义曲线(选了 CUSTOM 但曲线为空)-> 退回匀速并告警
    """
    preset = getattr(node, prop, "LINEAR") or "LINEAR"
    need_ease_helper(ctx)
    if needs_custom(preset):
        name = str(getattr(node, curve_prop, "") or "").strip()
        if not name:
            ctx.warn(f"『{node.bl_label}』的『过渡曲线』选了自定义,"
                     f"但没有挑曲线 —— 这次按匀速处理。"
                     f"点节点上的『选曲线』挑一条(没有的话:添加 → 曲线 → "
                     f"贝塞尔,画一条从左到右的)")
            return raw_expr
        curve = curve_expr(ctx, name, kind)
        if curve == "null":
            return raw_expr
        return f"_gk_ease({raw_expr}, 0, 0, {curve})"
    trans, ease = ease_constants(preset)
    return f"_gk_ease({raw_expr}, {trans}, {ease})"