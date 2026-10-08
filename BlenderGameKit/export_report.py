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
    LAST_REPORT.clear()
    if report:
        LAST_REPORT.update(report)
    if scene is not None and report:
        try:
            scene[PERSIST_KEY] = json.dumps(report, ensure_ascii=False)
        except Exception:                                 # noqa: BLE001
            pass          # 存不进去不影响导出本身(只是重开看不到)
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