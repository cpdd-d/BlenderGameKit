"""GK_LOG —— 统一的日志出口。

同时写到 Blender 控制台和插件内部的环形缓冲，方便面板里查看导出日志。
"""

import time
import traceback

MAX_RECORDS = 200
_records = []


def _stamp():
    return time.strftime("%H:%M:%S")


def log(message, level="INFO"):
    line = f"[{_stamp()}] [{level}] {message}"
    _records.append((level, line))
    if len(_records) > MAX_RECORDS:
        del _records[: len(_records) - MAX_RECORDS]
    try:
        print(f"[BlenderGameKit] {message}")
    except Exception:
        pass
    return line


def info(message):
    return log(message, "INFO")


def warn(message):
    return log(message, "WARN")


def error(message):
    return log(message, "ERROR")


def exception(message):
    """记录一条 ERROR,并**附上完整 traceback**。

    ── 为什么不能只用 error(str(exc))(GPT 审查 / 用户 2026-10-04)────
    `print(f"... {exc}")` 只留下最后一句异常文本,真正的出错位置(
    哪个文件哪一行、调用栈)全丢了 —— 用户反馈问题时也给不出线索。
    这个函数在 except 块里调用,自动抓当前栈。
    """
    detail = traceback.format_exc().rstrip()
    line = log(f"{message}\n{detail}", "ERROR")
    return line


def records():
    return tuple(_records)


def clear():
    _records.clear()


def last_error():
    for level, line in reversed(_records):
        if level == "ERROR":
            return line
    return ""
