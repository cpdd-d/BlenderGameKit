"""GK_LOG —— 统一的日志出口。

同时写到 Blender 控制台和插件内部的环形缓冲，方便面板里查看导出日志。
"""

import time

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


def records():
    return tuple(_records)


def clear():
    _records.clear()


def last_error():
    for level, line in reversed(_records):
        if level == "ERROR":
            return line
    return ""
