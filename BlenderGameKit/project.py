"""Blender 文件 <-> Godot 项目的绑定。

设计目标
--------
**一个 .blend 文件对应一个 Godot 项目。** 导出目录不再是插件的全局设置,
而是从 .blend 文件的位置推出来的:

    D:/我的游戏/角色.blend
        -> 项目根目录  D:/我的游戏/角色_godot/
            ├── project.godot
            ├── scripts/    逻辑脚本(节点图生成)
            ├── scenes/     场景装配(挂好脚本的 .tscn)
            ├── art/        模型与贴图(.glb / .png)
            ├── materials/  卡通材质(.tres)
            └── shaders/    NPR 着色器(随插件复制)

这样三个好处:
  1. 不用每次导出都去填路径 —— 打开哪个 .blend 就知道该写哪
  2. 一个仓库里可以放多个 .blend,各自对应各自的 Godot 项目,互不干扰
  3. 交付时"Blender 工程 + Godot 工程"是一个整体,拷贝一个文件夹就够

为什么不直接把项目建在 .blend 旁边(同一个目录)
------------------------------------------------
不行,而且这是**必须避免**的:.blend 目录会变成 Godot 项目的 res:// 根,
于是 Godot 的 .godot/ 导入缓存和 .blend 备份文件(.blend1)全混在一起,
Godot 还会去扫描 .blend1 之类的垃圾文件。所以固定用**同名子目录**。
"""

import json
import os
import re
import shutil
import subprocess
import sys

import bpy

# 项目根目录相对 .blend 的后缀
PROJECT_SUFFIX = "_godot"

# 项目里的固定子目录名。exporter / asset_export 从这里取名字,
# 免得两处各写一份字符串、改一处漏一处。
SCRIPTS_DIR = "scripts"
SCENES_DIR = "scenes"
ART_DIR = "art"
MATERIALS_DIR = "materials"
SHADERS_DIR = "shaders"

SUBDIRS = (ART_DIR, SCRIPTS_DIR, SCENES_DIR, MATERIALS_DIR, SHADERS_DIR)

# Godot 工程的默认内容。只写最小可用的一份,
# 之后让 Godot 自己去补全其余设置。
_PROJECT_GODOT = """; 由 BlenderGameKit 自动生成
; 这个文件标记了"这是一个 Godot 项目",Godot 靠它认根目录。

config_version=5

[application]

config/name="{title}"
config/features=PackedStringArray("{version}")

[rendering]

renderer/rendering_method="{renderer}"
"""

# 项目自述,方便过一段时间回来看
_README = """# {title}

这个 Godot 项目由 BlenderGameKit 从 `{blend_name}` 生成。

**不要手工改 `scripts/` 里的文件** —— 它们由 Blender 里的节点图生成,
下次导出会覆盖。要改逻辑请在 Blender 里改节点图。

| 目录 | 内容 | 谁生成 |
|---|---|---|
| `scripts/` | GDScript 逻辑 | Blender 节点图 |
| `scenes/` | 场景装配(模型 + 脚本) | Blender 导出 |
| `art/` | 模型 `.glb` 与贴图 `.png` | Blender 导出 |
| `materials/` | 卡通材质 `.tres` | Blender NPR 预览面板 |
| `shaders/` | NPR 着色器 | 插件自带 |

`art/` 和 `materials/` 里**由你自己做的**东西不会被覆盖;
只有同名文件会被重写。
"""


def blend_dir():
    """当前 .blend 所在目录;没保存过就返回 None。"""
    path = bpy.data.filepath
    if not path:
        return None
    return os.path.dirname(os.path.abspath(path))


def blend_name():
    """当前 .blend 的文件名(不含扩展名);没保存过返回 None。"""
    path = bpy.data.filepath
    if not path:
        return None
    return os.path.splitext(os.path.basename(path))[0]


def safe_project_name(text, fallback="game"):
    """把 .blend 文件名转成安全的项目名。

    Godot 的 project.godot 里 config/name 可以放中文,但**目录名**用中文在
    跨平台(尤其是 Windows + 命令行工具)时容易出问题,所以统一压成
    字母数字下划线。中文名会被压成空,这时用回退名。
    """
    name = re.sub(r"[^0-9A-Za-z_\-]", "_", text or "").strip("_")
    name = re.sub(r"_{2,}", "_", name)
    if len(name) < 2 or name.lower() in {"con", "prn", "aux", "nul"}:
        return fallback
    return name


def derive_project_dir():
    """按 .blend 的位置推导项目根目录。没保存过 .blend 就返回 None。"""
    directory = blend_dir()
    name = blend_name()
    if directory is None or name is None:
        return None
    return os.path.join(directory, safe_project_name(name) + PROJECT_SUFFIX)


def resolve(scene=None, adopt=True):
    """当前应该往哪个目录导出。返回 (项目根目录, 来源说明);推不出来时 (None, 原因)。

    优先级:
      1. 场景属性里手动指定的 project_dir
      2. **.blend 所在目录(或它的上级)已经是一个 Godot 项目**
         -> 直接用那个项目,不再套一层子目录
      3. 由 .blend 位置推导出同名 _godot 子目录

    第 2 条是实测补上的。很多人会把 .blend 直接放进 Godot 项目里 ——
    这时再建一个 `xxx_godot` 子目录就变成了**项目套项目**,既别扭又容易搞混。
    既然外层已经是项目了,就认外层。

    adopt **默认开启**:认到的外层项目缺标准子目录(art/scripts/... 很常见,
    别人的项目不一定按我们的布局建)就顺手补齐。
    这么设计是安全的 —— create_project() 是纯增量的,只建缺的目录、
    只写不存在的文件,绝不覆盖任何已有内容。而缺目录会让所有导出失败,
    让用户自己去建 5 个目录是没有意义的摩擦。
    """
    scene = scene or getattr(bpy.context, "scene", None)
    settings = getattr(scene, "gk_settings", None) if scene is not None else None

    override = (getattr(settings, "project_dir", "") or "").strip() if settings else ""
    if override:
        target = os.path.abspath(bpy.path.abspath(override))
        if adopt and os.path.isdir(target):
            try:
                create_project(target)
            except OSError:
                pass
        return target, "手动指定"

    if not bpy.data.filepath:
        return None, "这个 .blend 还没保存过。请先 Ctrl+S 存一次,项目会建在它旁边"

    enclosing = enclosing_project_dir()
    if enclosing:
        if adopt:
            # 缺子目录就补上。create_project() 是幂等的,不会覆盖已有内容。
            try:
                create_project(enclosing)
            except OSError:
                pass
        return enclosing, "Blender 文件在项目里"

    derived = derive_project_dir()
    if derived is None:
        return None, "推不出项目目录"
    return derived, "跟着 .blend"


def enclosing_project_dir():
    """如果 .blend 就在某个 Godot 项目里,返回那个项目的根目录。

    从 .blend 所在目录开始往上找(最多 4 层),遇到第一个含 project.godot
    的目录就认它。找不到返回 None。
    """
    current = blend_dir()
    if current is None:
        return None
    for _ in range(4):
        if os.path.isfile(os.path.join(current, "project.godot")):
            return current
        parent = os.path.dirname(current)
        if parent == current:      # 到盘符根了
            break
        current = parent
    return None


def describe_layout(directory):
    """给面板用的一句话说明当前是哪种布局。"""
    if not directory:
        return "未绑定"
    if enclosing_project_dir() == directory:
        return "Blender 文件在项目里"
    return "项目建在 .blend 旁边"


def is_project(directory):
    """这个目录是不是一个 Godot 项目(有 project.godot 就算)。"""
    if not directory:
        return False
    return os.path.isfile(os.path.join(directory, "project.godot"))


def missing_subdirs(directory):
    """返回还没建出来的子目录名。"""
    if not directory or not os.path.isdir(directory):
        return list(SUBDIRS)
    return [name for name in SUBDIRS
            if not os.path.isdir(os.path.join(directory, name))]


def create_project(directory, title=None, blend_file_name=None, renderer="forward_plus"):
    """建出完整的项目骨架。返回建出来的路径列表。

    幂等:已经存在的目录/文件**不覆盖**已有内容里属于用户的部分
    (project.godot 与 README 只在不存在时写;art/ 等目录里的文件一律不动)。
    """
    created = []
    os.makedirs(directory, exist_ok=True)

    title = title or os.path.basename(directory.rstrip("\\/"))
    # Godot 的 config/features 要写引擎版本
    try:
        version = _godot_version_string()
    except Exception:
        version = "4.4"

    project_file = os.path.join(directory, "project.godot")
    if not os.path.isfile(project_file):
        with open(project_file, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_PROJECT_GODOT.format(title=title, version=version,
                                              renderer=renderer))
        created.append(project_file)

    for name in SUBDIRS:
        path = os.path.join(directory, name)
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
            # Godot 不跟踪空目录,放个 .gitkeep 让目录能被版本管理留住
            keep = os.path.join(path, ".gitkeep")
            if not os.path.isfile(keep):
                open(keep, "w", encoding="utf-8").close()
            created.append(path)

    readme = os.path.join(directory, "README.md")
    if not os.path.isfile(readme):
        with open(readme, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_README.format(
                title=title,
                blend_name=blend_file_name or (blend_name() or "未保存的 .blend")))
        created.append(readme)

    return created


def _godot_version_string():
    """给 project.godot 用的引擎版本串。取不到就用一个保守值。"""
    executable = find_godot_executable()
    if not executable:
        return "4.4"
    try:
        result = subprocess.run([executable, "--version"], capture_output=True,
                                text=True, timeout=8)
        text = (result.stdout or "") + (result.stderr or "")
        match = re.search(r"(\d+\.\d+)", text)
        if match:
            return match.group(1)
    except Exception:
        pass
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
        package = __package__.split(".")[0] if __package__ else "BlenderGameKit"
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


# 生成标记文件:记下"主场景是我们设成哪个的"。
#
# 为什么需要它:set_main_scene 要区分两种情况 ——
#   * 主场景是**插件自己**设的  -> 场景变了(比如从 Player 变成 World)就该更新
#   * 主场景是**用户手动**选的  -> 必须尊重,不能动
# 光看"文件存不存在"分不出来,于是早期版本一律不动 ——
# 结果主场景永远停在第一次生成的 Player.tscn,而 World.tscn 里的新相机
# 一直没被看到(实测踩过:用户改了相机位置,Godot 打开还是旧的)。
_MARKER = ".gk_generated"


def _read_marker(directory):
    """读生成标记。返回 {"main_scene": res://...} 或 {}。"""
    path = os.path.join(directory or "", _MARKER)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_marker(directory, main_scene):
    path = os.path.join(directory or "", _MARKER)
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"main_scene": main_scene}, handle, ensure_ascii=False,
                      indent=2)
    except OSError:
        pass


def set_main_scene(directory, scene_res_path, force=False):
    """把 run/main_scene 写进 project.godot。返回是否改动了。

    为什么需要:没有这一行,Godot 里按 F5 会弹"没有设置主场景"。
    用户的心智模型是"我在 Blender 里搭好,切到 Godot 按 F5 就能跑",
    所以这一步必须自动做掉。

    ── 什么时候会覆盖 ──────────────────────────────────────────────
    只有当"当前主场景是插件自己设的"或者"它指向的文件不存在"时才改。
    用户手动把主场景换成别的场景时,我们尊重他的选择 ——
    判断依据是 .gk_generated 标记文件(记着我们上次设成了什么)。
    """
    if not directory or not scene_res_path:
        return False
    if not scene_res_path.startswith("res://"):
        scene_res_path = "res://" + scene_res_path

    project_file = os.path.join(directory, "project.godot")
    if not os.path.isfile(project_file):
        return False

    with open(project_file, "r", encoding="utf-8") as handle:
        text = handle.read()

    line = f'run/main_scene="{scene_res_path}"'
    existing = re.search(r'^run/main_scene="([^"]*)"', text, re.MULTILINE)
    if existing:
        current = existing.group(1)
        if current == scene_res_path:
            _write_marker(directory, scene_res_path)
            return False
        if not force:
            # 文件不存在 -> 指向了个死路径,得修
            missing = not os.path.isfile(
                os.path.join(directory, current.replace("res://", "")))
            # 是我们自己设的 -> 场景换代了,跟着更新
            ours = _read_marker(directory).get("main_scene") == current
            if not missing and not ours:
                return False        # 用户手动选的,不动
        text = text[:existing.start()] + line + text[existing.end():]
    else:
        # 插到 [application] 段里
        marker = "[application]"
        index = text.find(marker)
        if index < 0:
            text += f"\n[application]\n\n{line}\n"
        else:
            insert_at = index + len(marker)
            # 跳过紧跟的空行,插在该段第一行
            while insert_at < len(text) and text[insert_at] in "\r\n":
                insert_at += 1
            text = text[:insert_at] + line + "\n" + text[insert_at:]

    with open(project_file, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    _write_marker(directory, scene_res_path)
    return True


def project_summary(directory):
    """返回一个用于面板显示的简短状态。"""
    if not directory:
        return "未绑定", "ERROR"
    if not os.path.isdir(directory):
        return "尚未创建", "INFO"
    if not is_project(directory):
        return "目录存在但不是 Godot 项目", "ERROR"
    missing = missing_subdirs(directory)
    if missing:
        return f"缺少子目录: {'、'.join(missing)}", "ERROR"
    return "就绪", "CHECKMARK"


# ---------------------------------------------------------------------------
# 插件偏好设置
# ---------------------------------------------------------------------------
class GKAddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__.split(".")[0] if __package__ else "BlenderGameKit"

    godot_path: bpy.props.StringProperty(
        name="Godot 可执行文件",
        description="Godot 编辑器/运行时的路径。留空会自动到常见位置找",
        default="",
        subtype="FILE_PATH",
    )
    auto_open_after_export: bpy.props.BoolProperty(
        name="导出后提示打开 Godot",
        description="每次导出后问一句要不要切到 Godot(不需要就关掉)",
        default=False,
    )
    auto_export_on_run: bpy.props.BoolProperty(
        name="运行游戏前自动保存并导出",
        description=("点『直接运行游戏』时,先自动保存 .blend 并导出脚本与模型。"
                     "关掉的话点运行跑的是上次导出的旧内容"),
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Godot 可执行文件", icon="PLAY")
        box.prop(self, "godot_path", text="")
        found = find_godot_executable(context)
        if found:
            box.label(text=f"当前使用: {found}", icon="CHECKMARK")
        else:
            box.label(text="没找到 Godot,请手动指定路径", icon="ERROR")
        box.operator("gk.detect_godot", icon="VIEWZOOM")

        box = layout.box()
        box.label(text="导出行为", icon="EXPORT")
        box.prop(self, "auto_open_after_export")
        box.prop(self, "auto_export_on_run")
        if self.auto_export_on_run:
            box.label(text="点『直接运行游戏』时会先保存 + 导出", icon="INFO")

        box = layout.box()
        box.label(text="项目绑定规则", icon="FILE_FOLDER")
        for line in (
            "一个 .blend 对应一个 Godot 项目",
            "项目建在 .blend 旁边,文件夹名 = 文件名 + _godot",
            "不想放旁边的话,在侧栏『项目』里手动指定",
        ):
            box.label(text=line, icon="DOT")


GK_CLASSES = (GKAddonPreferences,)
