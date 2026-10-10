"""导出报告(用户 2026-10-05 第二十三轮:"导出报告可视化")。

── 解决什么(用户的原话:"转头就没了")────────────────────────────
导出时的问题以前只有两种去处:

* `self.report({"WARNING"}, ...)` —— 状态栏 / 信息编辑器里一闪而过;
* 侧栏那块"上次导出"只列前 4 条,还把整段话压成一行。

而这个项目里**大部分诊断本来就有结构**(`models.Diagnostic`:
level / code / message / hint / node_name —— 见 validation.py 里的
"脚本基类不对""动画目标不对"那几条),只是没人把它们好好画出来。
这个模块负责:

    导出结果  ->  报告字典(结构化,排序 + 截断)  ->  侧栏画成清单 / 复制成文本

── 存在哪 ──────────────────────────────────────────────────────
* 内存:`LAST_REPORT`(刚导完立刻就能看);
* .blend:场景自定义属性 `gk_last_report`(JSON)—— 关掉再打开还在,
  和 `operator_io.remember_export` 的 `gk_last_export` 一个套路。
  只存必要字段 + 最多 60 条:场景里塞太多东西会让 .blend 变大(踩过)。

── 为什么报告要"复制成文本" ────────────────────────────────────
出问题时用户要把它发出来。剪贴板 + 控制台各一份,直接粘就行,
不用截图(和『导出诊断信息』『调试面板』是同一条思路)。
"""

import json
import time

# 一份报告最多记这么多条(超了就在末尾报"还有 N 条")。
MAX_ITEMS = 60
# 报告在 .blend 里的键(场景自定义属性)
PERSIST_KEY = "gk_last_report"
# 整份报告也写进这个文本块 —— 用文本编辑器读长报告(见 sync_text_block)
EXPORT_REPORT_TEXT_NAME = "GK 导出报告"

# 内存里的最近一份(会话内;和 .blend 那份互为备份)
LAST_REPORT = {}

_LEVEL_ORDER = {"error": 0, "warning": 1, "info": 2}
_LEVEL_MARK = {"error": "[错误]", "warning": "[警告]", "info": "[提示]"}


def build(result):
    """把导出结果做成报告字典(**纯函数**,离线可测)。

    收三路:
      * `diagnostics` —— 结构化诊断(优先,信息最全:带 hint / node_name);
      * `warnings`    —— 摊平后的字符串(里面混着上面那些的 render(),
                        所以按文本**去重**,免得同一条出现两遍);
      * `notes`       —— 提示(level=info)。
    """
    if not result:
        return {}

    items = []
    seen_texts = set()

    def add(level, message, hint="", node_name="", code=""):
        text = str(message or "").strip()
        if not text or text in seen_texts:
            return
        seen_texts.add(text)
        items.append({
            "level": str(level or "warning"),
            "code": str(code or ""),
            "message": text,
            "hint": str(hint or "").strip(),
            "node_name": str(node_name or "").strip(),
        })

    for diagnostic in (result.get("diagnostics") or ()):
        message = str(getattr(diagnostic, "message", "") or "")
        hint = str(getattr(diagnostic, "hint", "") or "")
        # 摊平后的写法(见 models.Diagnostic.render)也要认,否则
        # warnings 里那条一模一样的会再进一次
        seen_texts.add(f"{message} —— {hint}" if hint else message)
        add(getattr(diagnostic, "level", "warning"), message, hint,
            getattr(diagnostic, "node_name", ""), getattr(diagnostic, "code", ""))
    for warning in (result.get("warnings") or ()):
        add("warning", warning)
    for note in (result.get("notes") or ()):
        add("info", note)

    items.sort(key=lambda entry: _LEVEL_ORDER.get(entry["level"], 1))
    dropped = max(0, len(items) - MAX_ITEMS)
    return {
        "ok": bool(result.get("ok", True)),
        "error": str(result.get("error") or ""),
        "stage": str(result.get("stage") or ""),
        "script_name": str(result.get("script_name") or ""),
        "directory": str(result.get("directory") or ""),
        "lines": int(result.get("lines") or 0),
        "elapsed_ms": float(result.get("elapsed_ms") or 0.0),
        "written": len(result.get("written") or ()),
        "items": items[:MAX_ITEMS],
        "dropped": dropped,
        "at": time.strftime("%Y-%m-%d %H:%M"),
    }


def record(scene, result):
    """记住一份导出报告(内存 + .blend + 文本块)。返回报告字典。"""
    report = build(result)
    # ── 资产清单往前带(第八十九轮)──────────────────────────────────
    # 资产是**另一步**导的(art/glb、audio/…在『导出场景与模型』里写)。
    # 只导脚本时新报告里没有资产信息,但它记的那几份文件还在磁盘上 ——
    # 丢掉的话用户每次导完脚本都要"咦,资产清单呢"。带过去。
    if report and not report.get("assets"):
        carried = LAST_REPORT.get("assets")
        if carried:
            report["assets"] = carried
    LAST_REPORT.clear()
    if report:
        LAST_REPORT.update(report)
    _persist(scene, report)
    if report:
        try:
            sync_text_block(report)
        except Exception:                                 # noqa: BLE001
            pass          # 写不进文本块不影响导出本身
    return report


def sync_text_block(report):
    """把整份报告写进 Blender 文本块『GK 导出报告』。

    ── 为什么搬到这里来(用户第五十五轮:"侧栏显示的信息太多了")────────
    报告一出问题就能有十几条(带"怎么修"),全部铺在侧栏会把面板撑得很长。
    现在正文进文本块,用**文本编辑器**看 —— 能滚动 / 选择 / 复制 / 搜索,
    侧栏只留一行摘要 + 几个按钮。

    ⚠ 但侧栏**保留**出错时的『定位』按钮:点了能跳到出问题的那个节点 ——
    这是文本块替代不了的(文本块里的字点不动)。所以是"正文搬走、
    交互留下",不是全删。
    """
    import bpy

    block = bpy.data.texts.get(EXPORT_REPORT_TEXT_NAME)
    if block is None:
        block = bpy.data.texts.new(EXPORT_REPORT_TEXT_NAME)
    else:
        block.clear()
    block.write(report_text(report))
    try:
        block.use_fake_user = True
    except Exception:                                     # noqa: BLE001
        pass
    return block.name


def load(scene):
    """取最近的报告:内存里有就用内存的(刚导完),否则从 .blend 恢复。"""
    if LAST_REPORT:
        return LAST_REPORT
    raw = None
    try:
        raw = scene.get(PERSIST_KEY) if scene is not None else None
    except Exception:                                     # noqa: BLE001
        raw = None
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    LAST_REPORT.update(data)
    return LAST_REPORT


def clear():
    """清掉内存那份(测试 / 打开新文件时用)。"""
    LAST_REPORT.clear()


# ==========================================================================
# 资产清单(第八十九轮:待办 I 组 28)
# ==========================================================================
# 「导出报告列资产清单」——资产这一步(art/*.glb、art/*.png、audio/*、
# curves/*)是在脚本导出**之后**跑的,报告那时已经记过一份。这里不改报告
# 的形状,只往里补一节 `assets`:{类型: [相对路径, …]}。
#
# 分类只看"落在哪个目录 + 什么后缀",不动磁盘 —— 纯函数、离线可测。
ASSET_GROUPS = (("model", "模型"), ("image", "图片"),
                ("sound", "音频"), ("curve", "曲线"))
# 一组最多列几个名字(报告要能读;超了写"…还有 N 个")。
ASSET_LINE_LIMIT = 10
_SOUND_EXTS = (".wav", ".ogg", ".mp3", ".flac")


def _asset_dirs():
    """(art, audio, curves) 三个目录名。

    优先用 project / curve_assets 里的常量(单一出处);拿不到时
    (离线裁剪环境)退回字面量 —— 只在报告里用,错了是清单少一行,
    不影响导出本身。
    """
    art, audio, curves = "art", "audio", "curves"
    try:
        from .project import layout
        art = layout.ART_DIR
        audio = layout.AUDIO_DIR
    except Exception:                                     # noqa: BLE001
        pass
    try:
        from .curve_assets import CURVES_DIR
        curves = CURVES_DIR
    except Exception:                                     # noqa: BLE001
        pass
    return art, audio, curves


def classify_assets(directory, written):
    """把"这次写出来的文件"里属于资产的挑出来分组(**纯函数**)。

    返回 `{"model": [相对路径, …], "image": [...], "sound": [...],
    "curve": [...]}`,每组按名字排序、去重;不是资产的
    (scripts/ scenes/ materials/ shaders/)一律不收。

    `directory` 是项目根;`written` 里混着绝对路径 / 相对路径 / None,
    都以项目根为准算相对路径,算不出来的按原样比目录名。
    """
    art, audio, curves = _asset_dirs()
    groups = {key: set() for key, _label in ASSET_GROUPS}
    root = str(directory or "")
    for raw in (written or ()):
        if not raw:
            continue
        path = str(raw).replace("\\", "/")
        if root:
            prefix = root.replace("\\", "/").rstrip("/") + "/"
            if path.lower().startswith(prefix.lower()):
                path = path[len(prefix):]
        parts = path.split("/")
        if len(parts) < 2:
            continue
        folder = parts[0]
        ext = ("." + parts[-1].rsplit(".", 1)[-1].lower()
               if "." in parts[-1] else "")
        if folder == art and ext == ".glb":
            groups["model"].add(path)
        elif folder == art and ext == ".png":
            groups["image"].add(path)
        elif folder == audio and ext in _SOUND_EXTS:
            groups["sound"].add(path)
        elif folder == curves and ext == ".tres":
            groups["curve"].add(path)
    return {key: sorted(values) for key, values in groups.items()}


def asset_total(report):
    """一份报告里记了几份资产(没记过就是 0)。"""
    assets = report.get("assets") if isinstance(report, dict) else None
    if not isinstance(assets, dict):
        return 0
    return sum(len(entries or ()) for entries in assets.values())


def _persist(scene, report):
    """报告写回 .blend(内存那份由调用方管)。"""
    if scene is None or not report:
        return
    try:
        scene[PERSIST_KEY] = json.dumps(report, ensure_ascii=False)
    except Exception:                                     # noqa: BLE001
        pass          # 存不进去不影响导出本身(只是重开看不到)


def note_assets(scene, directory, written):
    """把这次真的落了哪些资产记进最近那份报告(第八十九轮)。

    调用点在**导出资产/模型之后**(脚本导出那一步已经记过报告)。
    没有报告(用户只导了资产、还没导过脚本)就开一份最简的 ——
    这样资产导出也有个能看的清单。返回记了几份资产。
    """
    groups = classify_assets(directory, written)
    total = sum(len(entries) for entries in groups.values())
    if not total:
        return 0
    report = dict(LAST_REPORT) or dict(load(scene))
    if not report:
        report = {"ok": True, "error": "", "stage": "", "script_name": "",
                  "directory": str(directory or ""), "lines": 0,
                  "elapsed_ms": 0.0, "written": 0, "items": [], "dropped": 0,
                  "at": time.strftime("%Y-%m-%d %H:%M")}
    report["assets"] = groups
    LAST_REPORT.clear()
    LAST_REPORT.update(report)
    _persist(scene, report)
    try:
        sync_text_block(report)
    except Exception:                                     # noqa: BLE001
        pass          # 写不进文本块不影响导出本身
    return total


def counts(report):
    """(错误数, 警告数, 提示数)。"""
    error = warning = info = 0
    for item in (report.get("items") or ()):
        level = str(item.get("level") or "")
        if level == "error":
            error += 1
        elif level == "info":
            info += 1
        else:
            warning += 1
    return error, warning, info


def items_of(report, level):
    """按级别取条目(侧栏分组画的时候用)。"""
    return [item for item in (report.get("items") or ())
            if str(item.get("level") or "warning") == level]


def report_text(report):
    """整份报告的纯文本(『复制报告』用):粘给谁都能看懂。"""
    if not report:
        return "(还没有导出过 —— 先点『导出为 Godot 脚本』)"
    lines = []
    state = "成功" if report.get("ok") else "失败"
    lines.append(f"=== BlenderGameKit 导出报告({state})===")
    if report.get("at"):
        lines.append(f"时间: {report['at']}")
    if report.get("error"):
        stage = f"(在『{report['stage']}』这一步)" if report.get("stage") else ""
        lines.append(f"错误 {stage}: {report['error']}")
    if report.get("script_name"):
        lines.append(f"脚本: {report['script_name']}"
                     f"({report.get('lines', 0)} 行)")
    if report.get("directory"):
        lines.append(f"目录: {report['directory']}")
    lines.append(f"写了 {report.get('written', 0)} 个文件"
                 f",耗时 {report.get('elapsed_ms', 0.0):.0f} ms")
    error, warning, info = counts(report)
    lines.append(f"问题: {error} 个错误 / {warning} 个警告 / {info} 个提示"
                 if (error or warning or info) else "问题: 没有")
    # ── 资产清单(第八十九轮)────────────────────────────────────────
    # 记的是"这次真的落了哪些资产文件"(见 note_assets)——用户靠它核对
    # "我的三份资产都进去了没",不用去翻 art/ 目录。
    if asset_total(report):
        lines.append(f"资产清单({asset_total(report)} 份):")
        for key, label in ASSET_GROUPS:
            entries = (report.get("assets") or {}).get(key) or []
            if not entries:
                continue
            shown = list(entries[:ASSET_LINE_LIMIT])
            line = f"  {label}: " + "、".join(shown)
            if len(entries) > len(shown):
                line += f"…(还有 {len(entries) - len(shown)} 个)"
            lines.append(line)
    for index, item in enumerate(report.get("items") or (), start=1):
        mark = _LEVEL_MARK.get(str(item.get("level")), "[警告]")
        lines.append("")
        lines.append(f"{index}. {mark} {item.get('message', '')}")
        if item.get("hint"):
            lines.append(f"    怎么修: {item['hint']}")
        if item.get("node_name"):
            lines.append(f"    相关节点: {item['node_name']}")
        if item.get("code"):
            lines.append(f"    代码: {item['code']}")
    if report.get("dropped"):
        lines.append("")
        lines.append(f"(还有 {report['dropped']} 条没列出来,"
                     f"上面这些是最严重的)")
    lines.append("")
    lines.append("(以上全部内容可以直接复制发出来)")
    return "\n".join(lines)