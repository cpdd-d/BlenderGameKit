"""Godot 进程/窗口检测、聚焦,以及系统文件管理器打开。

从 project.py 拆出(GPT 审查)。
"""

import os
import subprocess
import sys



def project_title(directory):
    """读 project.godot 里的 config/name。读不到就退回目录名。

    Godot 的窗口标题里用的是**这个值**,不是目录名 ——
    我们的目录带 `_godot` 后缀,两者通常不一样。
    """
    path = os.path.join(directory or "", "project.godot")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip().startswith("config/name="):
                    value = line.split("=", 1)[1].strip()
                    return value.strip('"').strip("'")
    except OSError:
        pass
    return os.path.basename(os.path.normpath(directory or ""))


def running_editors():
    """看现在有没有 Godot **编辑器**进程在跑。返回进程数。

    ── 为什么需要它 ────────────────────────────────────────────────
    Godot 一个项目只允许开一个编辑器实例,但你**再点一次按钮**时它不会
    复用已有的窗口 —— 而是新起一个进程,于是屏幕上出现两个 Godot 窗口,
    分不清哪个是哪个(用户反馈过)。

    Godot 本身没有"没开就开、开了就聚焦"的命令行开关,所以只能
    在启动之前自己看一眼。
    """
    if sys.platform.startswith("win"):
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq Godot*", "/NH"],
                capture_output=True, text=True, errors="replace", timeout=20)
            lines = [line for line in (result.stdout or "").splitlines()
                     if "Godot" in line]
            return len(lines)
        except Exception:
            return 0
    try:
        result = subprocess.run(["pgrep", "-c", "-f", "godot"],
                                capture_output=True, text=True, timeout=20)
        return int((result.stdout or "0").strip() or 0)
    except Exception:
        return 0


# 列出 Godot 窗口标题。
#
# 为什么要绕一层 PowerShell:Windows 上"激活别的进程的窗口"要通过
# user32 的 SetForegroundWindow,Python 标准库没有这个能力,
# 而装 pywin32 又是个重依赖。PowerShell 调 Add-Type 是系统自带的。
#
# ── 为什么拆成两步 ──────────────────────────────────────────────
# 一开始把"选窗口 + 聚焦"全塞进一段 PowerShell,结果转义引号地狱:
# 项目名带引号就崩,报错还只有一句 `' $_'`(踩过)。
# 现在让 PowerShell **只输出**标题,选哪个窗口由 Python 决定 ——
# 匹配逻辑可控,报错也能说清楚是哪个窗口。
_LIST_WINDOWS = (
    "Get-Process -Name 'Godot*' -ErrorAction SilentlyContinue |"
    " Where-Object { $_.MainWindowHandle -ne 0 } |"
    " ForEach-Object { Write-Output ($_.Id.ToString() + \"`t\" + $_.MainWindowTitle) }"
)

_FOCUS_PID = (
    "Add-Type -Namespace W -Name N -MemberDefinition '"
    "[DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr h);"
    "[DllImport(\"user32.dll\")] public static extern bool ShowWindow(IntPtr h, int c);';"
    "$p = Get-Process -Id __PID__ -ErrorAction SilentlyContinue;"
    "if ($p -and $p.MainWindowHandle -ne 0) {"
    " [W.N]::ShowWindow($p.MainWindowHandle, 9) | Out-Null;"
    " [W.N]::SetForegroundWindow($p.MainWindowHandle) | Out-Null;"
    " Write-Output 'FOCUSED' } else { Write-Output 'NO_WINDOW' }"
)


def _run_powershell(script, timeout=25):
    """跑一段 PowerShell,返回 stdout(失败返回空串)。"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return ""
    return result.stdout or ""


def godot_windows():
    """列出正在运行的 Godot 窗口。返回 [(进程号, 窗口标题)]。"""
    if not sys.platform.startswith("win"):
        return []
    out = []
    for line in _run_powershell(_LIST_WINDOWS).splitlines():
        line = line.strip()
        if not line or "\t" not in line:
            continue
        pid_text, title = line.split("\t", 1)
        try:
            out.append((int(pid_text.strip()), title.strip()))
        except ValueError:
            continue
    return out


def focus_godot_window(title_hint=""):
    """把已经在运行的 Godot 窗口切到前台。返回 (是否成功, 说明)。

    ── 为什么要按标题匹配 ──────────────────────────────────────────
    无脑聚焦"第一个 Godot 窗口"是错的:如果用户开着的是**别的项目**,
    聚焦过去只会让他更困惑。Godot 的窗口标题里带着项目名
    (形如 `World.tscn - 3dtest1 - Godot Engine`),拿项目名去匹配,
    只有确实是这个项目才切过去。
    """
    if not sys.platform.startswith("win"):
        return False, "只支持 Windows"
    windows = godot_windows()
    if not windows:
        return False, "没找到 Godot 窗口"

    hint = str(title_hint or "").strip()
    match = None
    if hint:
        for pid, title in windows:
            if hint.lower() in title.lower():
                match = (pid, title)
                break
    if match is None:
        return False, f"开着的是别的项目（{windows[0][1]}）"
    if "FOCUSED" in _run_powershell(_FOCUS_PID.replace("__PID__", str(match[0]))):
        return True, "已把它切到前台"
    return False, "没能激活那个窗口"


def open_folder(directory):
    """在系统文件管理器里打开目录。"""
    if not os.path.isdir(directory):
        return False, f"目录不存在: {directory}"
    try:
        if sys.platform.startswith("win"):
            os.startfile(directory)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", directory])
        else:
            subprocess.Popen(["xdg-open", directory])
    except Exception as exc:
        return False, f"打开失败: {exc}"
    return True, directory


def open_path(path, operator=None):
    """用系统默认程序打开一个文件(比如预览图)。

    单独一个函数是因为"打开文件"和"打开目录"在三个平台上各有各的写法,
    混在一起容易写错。这里返回 (是否成功, 说明或错误信息)。
    """
    if not os.path.isfile(path):
        message = f"文件不存在: {path}"
        if operator is not None:
            operator.report({"ERROR"}, message)
        return {"CANCELLED"}
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as exc:
        if operator is not None:
            operator.report({"ERROR"}, f"打开失败: {exc}")
        return {"CANCELLED"}
    if operator is not None:
        operator.report({"INFO"}, f"已用系统程序打开 {os.path.basename(path)}")
    return {"FINISHED"}
