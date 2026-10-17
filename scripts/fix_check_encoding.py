"""给 scripts/check_*.py 统一补上"控制台编码容错"(一次性小工具)。

背景:这些自查脚本会用 `✗`(U+2717)打失败信息,而 Windows 控制台默认 GBK ——
一打印就 `UnicodeEncodeError`,**失败信息被一段 Traceback 顶掉**。门禁里看到的
就是"自查脚本崩了",而不是"哪一条不对"(第一百三十一轮 / 一百三十四轮各踩一次)。

这个脚本做的事:在每个 `check_*.py` 的 import 块之后插一段幂等的

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

已经有的文件跳过;没 `import sys` 的顺手补上。用法:
    python scripts\\fix_check_encoding.py --check     # 只看要改哪些
    python scripts\\fix_check_encoding.py             # 真改
"""
import argparse
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MARK = 'reconfigure(encoding="utf-8"'
GUARD = '''

# ⚠ Windows 控制台默认 GBK,而下面会用 `✗`(U+2717)打失败信息 —— 直接打印会
#   `UnicodeEncodeError`,**失败信息本身**被一段 Traceback 顶掉(门禁里看到的
#   就是"自查脚本崩了",而不是"哪一条不对")。这里把输出改成能容错的编码。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                          # noqa: BLE001
    pass
'''


def patch(path):
    text = open(path, encoding="utf-8").read()
    if MARK in text:
        return "已有"
    lines = text.splitlines(keepends=True)
    last_import = -1
    for index, line in enumerate(lines[:200]):
        if re.match(r"^(import |from )\S", line):
            last_import = index
    if last_import < 0:
        return "跳过(找不到 import)"
    block = "".join(lines[:last_import + 1])
    if not re.search(r"^import sys$", block, re.MULTILINE):
        guard = "\nimport sys\n" + GUARD
    else:
        guard = GUARD
    lines.insert(last_import + 1, guard)
    open(path, "w", encoding="utf-8", newline="").write("".join(lines))
    return "已改"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只报告,不改")
    args = parser.parse_args()
    changed = 0
    for path in sorted(glob.glob(os.path.join(HERE, "check_*.py"))):
        name = os.path.basename(path)
        text = open(path, encoding="utf-8").read()
        if "✗" not in text and "\\u2717" not in text:
            continue
        status = "已有" if MARK in text else ("要改" if args.check else "")
        if not status:
            status = patch(path)
        if status == "已改":
            changed += 1
        print(f"{status}  {name}")
    print(f"共 {changed} 个文件被改")
    return 0


if __name__ == "__main__":
    sys.exit(main())
