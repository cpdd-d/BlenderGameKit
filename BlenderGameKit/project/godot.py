"""Godot 可执行文件发现、启动、资源导入与工程信息读取。

从 project.py 拆出(GPT 审查)。
"""

import os
import re
import shutil
import subprocess
import sys

import bpy

from .. import blender_adapter
from .layout import is_project
from .windows import focus_godot_window, project_title, running_editors



def godot_version(executable=None, context=None):
    """读 Godot 的**完整**版本号(如 "4.7.2");读不到返回空串。

    ── 为什么要完整版本 ──────────────────────────────────────────────
    打包要用**导出模板**,而模板是按完整版本号分目录的
    (`%APPDATA%\\Godot\\export_templates\\4.7.2.stable\\`)。只要到
    "4.7" 的话就对不上目录名,会误报"没装模板"。
    """
    executable = executable or find_godot_executable(context)
    if not executable:
        return ""
    try:
        result = subprocess.run([executable, "--version"], capture_output=True,
                                text=True, timeout=8)
        text = (result.stdout or "") + (result.stderr or "")
        match = re.search(r"(\d+\.\d+(?:\.\d+)?)", text)
        if match:
            return match.group(1)
    except Exception:
        pass
    return ""


def _godot_version_string():
    """给 project.godot 用的引擎版本串(主.次)。取不到就用一个保守值。"""
    full = godot_version()
    if full:
        return ".".join(full.split(".")[:2])
    return "4.4"


# ---------------------------------------------------------------------------
# Godot 可执行文件
# ---------------------------------------------------------------------------
def preferences(context=None):
    """取插件偏好设置;拿不到就返回 None(离线测试里会走到这条)。"""
    context = context or getattr(bpy, "context", None)
    if context is None:
        return None
    try:
        # 插件根模块名:普通 addon 是 "BlenderGameKit",扩展是
        # "bl_ext.<仓库>.<扩展 id>"。以前这里写 `split(".")[0]`,扩展下
        # 会得到 "bl_ext" -> addons.get() 永远返回 None -> 偏好设置
        # **从来读不到**(面板里的 Godot 路径 / 运行前自动导出都不生效)。
        package = blender_adapter.addon_package(__package__)
        addon = context.preferences.addons.get(package)
        if addon is None:
            return None
        return getattr(addon, "preferences", None)
    except Exception:
        return None


def _candidate_executables():
    """常见位置。

    注意 Godot 官方下载包解压出来的名字是带版本号的
    (Godot_v4.7.2-stable_win64.exe / ..._console.exe),
    所以不能只匹配 "godot.exe" —— 一定要用通配。
    console 版会额外开一个控制台窗口,所以**优先选非 console 版**。
    """
    patterns = ["Godot*.exe", "godot*.exe"] if sys.platform.startswith("win") \
        else ["Godot*", "godot*"]

    roots = [
        os.environ.get("GODOT_PATH", ""),
        os.environ.get("GODOT4", ""),
        r"D:\godot",
        r"C:\Godot",
        r"C:\Program Files\Godot",
        r"C:\Program Files (x86)\Godot",
        os.path.join(os.path.expanduser("~"), "Godot"),
        os.path.join(os.path.expanduser("~"), "Downloads"),
        os.path.join(os.path.expanduser("~"), "Desktop"),
        "/usr/local/bin",
        "/usr/bin",
        "/Applications",
    ]

    def rank(path):
        """越靠前越优先:非 console 版 > console 版;64 位 > 32 位。"""
        name = os.path.basename(path).lower()
        score = 0
        if "console" in name:
            score += 10
        if "32" in name:
            score += 5
        return score

    found = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        # 先扫根目录,再扫一层子目录(下载包通常解压成带版本号的文件夹)
        directories = [root]
        try:
            for entry in os.listdir(root)[:60]:
                sub = os.path.join(root, entry)
                if os.path.isdir(sub) and not entry.startswith("."):
                    directories.append(sub)
        except OSError:
            pass
        for directory in directories:
            for pattern in patterns:
                try:
                    import glob
                    found.extend(glob.glob(os.path.join(directory, pattern)))
                except OSError:
                    continue

    # 去重 + 排序 + 过滤掉明显不是编辑器的(Godot 工程目录里的临时产物)
    unique = []
    for path in found:
        if path in unique or not os.path.isfile(path):
            continue
        if os.path.basename(path).lower().startswith("godot_v"):
            unique.append(path)
        elif os.path.basename(path).lower() in {"godot.exe", "godot", "godot4"}:
            unique.append(path)
    unique.sort(key=rank)
    return unique


def find_godot_executable(context=None):
    """找一个可用的 Godot。优先用偏好设置里指定的。"""
    prefs = preferences(context)
    configured = (getattr(prefs, "godot_path", "") or "").strip() if prefs else ""
    if configured and os.path.isfile(configured):
        return configured

    candidates = _candidate_executables()
    if candidates:
        return candidates[0]

    for name in ("godot", "godot4", "Godot"):
        found = shutil.which(name)
        if found:
            return found
    return None


def main_scene(directory):
    """读出项目里设的主场景 res:// 路径;没设返回 None。"""
    project_file = os.path.join(directory or "", "project.godot")
    if not os.path.isfile(project_file):
        return None
    try:
        with open(project_file, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return None
    match = re.search(r'^run/main_scene="([^"]*)"', text, re.MULTILINE)
    return match.group(1) if match else None


def open_in_godot(directory, context=None, editor=True):
    """用 Godot 打开项目。返回 (成功, 说明文字)。

    用 Popen 而不是 run —— Godot 编辑器是长期运行的 GUI,
    阻塞等待会把 Blender 卡住。

    ── 参数必须传对(这里踩过一个很贵的坑)────────────────────────
    Godot 4 的模式判定:
        godot --path <dir> --editor   -> 打开编辑器
        godot --path <dir>            -> **运行游戏**(不是打开编辑器!)

    「裸 --path 就是打开编辑器」是错的。实测:
        --path           -> Error: Can't run project: no main scene defined
        --path --editor  -> 正常进编辑器
        --path --game    -> ERROR: Couldn't detect whether to run the editor,
                            the project manager or a specific project. Aborting.

    `--game` **不是有效参数**,加上它会让 Godot 无法判断该干什么。
    所以打开编辑器必须显式带 `--editor`;运行游戏则不带它。
    """
    executable = find_godot_executable(context)
    if not executable:
        return False, "找不到 Godot。请在插件偏好设置里指定 godot.exe 的路径"
    if not is_project(directory):
        return False, f"{directory} 不是 Godot 项目(没有 project.godot)"

    args = [executable, "--path", directory]
    if editor:
        # 已经在跑编辑器就**不再启动第二个** —— Godot 不会复用已有窗口,
        # 再点一次屏幕上就会多一个,分不清哪个是哪个。
        #
        # 更好的做法是把它切到前台:用户点这个按钮的意图就是"我要看 Godot",
        # 只弹一句"已经开着了"等于让他自己去一堆积压窗口里翻。
        # 但必须**确认那个窗口就是这个项目** —— 开的是别的项目的话,
        # 切过去只会让他更困惑。
        #
        # ⚠ 标题里用的是**项目名**(project.godot 里的 config/name),
        # 不是目录名。我们建目录时会加 `_godot` 后缀(3dtest1 -> 3dtest1_godot),
        # 拿目录名去匹配永远匹配不上(踩过)。
        if running_editors() > 0:
            focused, why = focus_godot_window(project_title(directory))
            if focused:
                return True, f"Godot 已经开着这个项目,{why}"
            return False, (f"已经有一个 Godot 在运行,{why}。\n"
                           f"再点会多开一个窗口。请先在 Godot 里"
                           f"关掉那个,或者手动切过去")
        args.append("--editor")
    else:
        # 直接运行游戏之前先确认有主场景,否则 Godot 会抛
        # "Can't run project: no main scene defined" 然后一闪而过,
        # 用户完全看不到原因。
        scene = main_scene(directory)
        if not scene:
            return False, ("项目还没有主场景,直接运行会失败。\n"
                           "请先在 Blender 里『导出为 Godot 脚本』并勾选"
                           "『同时生成示例场景』,然后重试")
        if not os.path.isfile(os.path.join(directory,
                                           scene.replace("res://", ""))):
            return False, (f"主场景 {scene} 指向的文件不存在。"
                           f"请重新导出一次场景")

        # ── 运行前必须让 Godot 重新导入一次资源 ──────────────────────
        # 踩过的坑:点『直接运行游戏』时,Godot **不会**做资源导入 ——
        # 它直接拿 .godot/imported/ 里的缓存开跑。而我们刚重新导出的
        # .glb / .png 对它是"新文件",缓存是旧的 ——
        # 于是用户看到的还是上一次的模型和材质,
        # 必须先手动打开一次 Godot 编辑器才会变。
        #
        # `--import` 正是为此设计的:启动编辑器 -> 等导入完成 -> 退出。
        # 所以运行游戏之前先跑一遍它,再启动游戏。
        ensure_imported(executable, directory)
    try:
        if sys.platform.startswith("win"):
            # Windows 上不给 Godot 分配控制台窗口
            creation = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
            subprocess.Popen(args, creationflags=creation, close_fds=True)
        else:
            subprocess.Popen(args, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    except OSError as exc:
        return False, f"启动 Godot 失败: {exc}"
    action = "打开编辑器" if editor else "运行游戏"
    return True, f"已用 Godot {action}: {os.path.basename(directory)}"


def ensure_imported(executable, directory, timeout=90):
    """跑一遍 Godot 的资源导入,让新导出的 .glb / .png 进缓存。

    返回 (是否跑完, 说明)。

    ── 为什么必须有这一步 ──────────────────────────────────────────
    Godot 4 把导入结果放在 `.godot/imported/` 里。**用 `--path <dir>` 直接
    运行游戏时它不会重新导入** —— 直接拿旧缓存跑。我们每次导出都会重写
    `art/*.glb`,对 Godot 来说那是"文件变了但缓存还是旧的"。

    用户的症状很典型:"点了『直接运行游戏』,看到的还是改之前的样子,
    非得先打开一次 Godot 编辑器才生效"。原因就是编辑器会触发文件系统扫描,
    而直接运行不会。

    `godot --headless --path <dir> --import` 就是官方给这个场景准备的:
    启动编辑器 -> 等资源导完 -> 退出。

    ── 为什么失败也不拦着 ──────────────────────────────────────────
    导入失败的原因很多(缓存锁住、项目还没建好…),而用户点的是"运行游戏"。
    为了导入失败就不让他跑,比看到旧资源更让人恼火。
    所以这里只尽力而为:失败就返回 False,调用方继续跑游戏。
    """
    if not executable or not directory:
        return False, "缺少 Godot 可执行文件或项目目录"

    args = [executable, "--headless", "--path", directory, "--import"]
    try:
        if sys.platform.startswith("win"):
            creation = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
            completed = subprocess.run(args, creationflags=creation,
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL,
                                       timeout=timeout, check=False)
        else:
            completed = subprocess.run(args, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL,
                                       timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return False, f"资源导入超时({timeout} 秒),已跳过"
    except OSError as exc:
        return False, f"资源导入启动失败: {exc}"

    # 退出码不可靠(实测有时是 -1),所以不拿它判成败 ——
    # 只要进程正常结束就当导入跑过一遍了。
    return True, "已刷新 Godot 资源缓存"
