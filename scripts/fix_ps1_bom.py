# 修复 BOM:重写 scripts/build_and_test.ps1 为带 UTF-8 BOM 的文件。
#
# ── 为什么需要它(第 124 轮:同一个坑踩了三次)────────────────────────
# Windows PowerShell 5.1 读**无 BOM** 的文件时按系统 ANSI 代码页(这台机器是
# GBK)解码。这个脚本里有大量中文注释 —— 按 GBK 解会让某些"引号字节"落错位置,
# 于是解析器报 "意外的标记"、整个门禁跑不起来。
#
# 为什么 BOM 会掉:任何一次 **普通文本编辑**(编辑器/工具按 UTF-8 无 BOM 保存)
# 都会把它抹掉。所以不能只"修一次",得有一条**自愈路径**:
#   * 平时:跑门禁用 `powershell -File`(读 BOM,正常);
#   * 掉 BOM 时:用 `pwsh -File scripts\fix_ps1_bom.ps1` 或
#     `python scripts\fix_ps1_bom.py` 修回来(两者等价)。
# 本脚本就是那条自愈路径的 Python 版,和 `fix_ps1_bom.ps1` 互为备份
# (哪个能跑用哪个 —— PowerShell 被卡住时 Python 还能用)。

import io
import sys

TARGETS = (
    r"D:\BlenderGameKit\scripts\build_and_test.ps1",
)
BOM = b"\xef\xbb\xbf"


def fix(path):
    with io.open(path, "rb") as handle:
        raw = handle.read()
    had = raw.startswith(BOM)
    if had:
        raw = raw[len(BOM):]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        print(f"  [FAIL] {path} 不是 UTF-8:{exc}")
        return False
    text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    with io.open(path, "wb") as handle:
        handle.write(BOM + text.encode("utf-8"))
    with io.open(path, "rb") as handle:
        ok = handle.read(3) == BOM
    print(f"  {'已有' if had else '补上'} BOM: {path}(现在={'OK' if ok else 'FAIL'})")
    return ok


if __name__ == "__main__":
    failed = [path for path in TARGETS if not fix(path)]
    sys.exit(1 if failed else 0)
