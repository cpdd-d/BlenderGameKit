"""打包发布:生成导出预设 + 调 Godot `--export-release` 打出可执行文件。

── 这一块补的是什么(全流程里唯一断掉的一环)────────────────────────
之前插件导出到 Godot **工程**为止:要得到"能双击运行的游戏",用户还得自己
开 Godot 编辑器、配导出预设、去装导出模板、再点一次导出。本模块把这一段
接成一条龙 —— Blender 里点一下『打包成可执行文件』直接出 exe。

三条设计原则(都偏保守,宁可少做也不出意外):
  1. **绝不覆盖**用户已有的 `export_presets.cfg`。那是他在 Godot 编辑器里
     精心配好的东西(图标 / 权限 / 架构 / 打包方式),覆盖掉是不可接受的。
     所以只在**文件不存在**时生成一份,并且导出时优先沿用用户预设里的
     预设名与输出路径。
  2. 首选 Windows Desktop(exe)。本机可验证,也是绝大多数用户的目标平台。
     函数都留了参数位,以后加 Linux / Web 只是多一套预设文本。
  3. **导出模板缺失是头号失败模式**:没装模板时 `--export-release` 会以
     "No export template found" 直接失败,而这条英文报错用户很难自己翻译成
     "去哪儿装模板"。所以单独做探测,并把"装法"写成能照着做的人话。

模块保持**纯标准库**(不 import bpy / 不 import 本插件的其它模块),
这样离线单测和门禁里的旁路脚本都能直接 import 它。
"""

import os
import re
import subprocess
import sys


# 导出预设文件。Godot 靠它认"这个项目怎么打包"。
EXPORT_PRESETS_NAME = "export_presets.cfg"

# 预设名。命令行 `--export-release "<预设名>"` 后面跟的就是它,
# 必须和下面 _preset_text 里 `name=` 的取值**一字不差**。
WINDOWS_PRESET_NAME = "Windows Desktop"

# 打包产物默认放项目里的 dist/ 子目录。和 Godot 官方模板工程的习惯一致。
DIST_DIR = "dist"


# ---------------------------------------------------------------------------
# 预设文件
# ---------------------------------------------------------------------------
def export_presets_text(title="game", preset_name=WINDOWS_PRESET_NAME,
                        export_path=""):
    """生成一份 Windows Desktop 导出预设的文本。

    ── 为什么只写这么几个键 ────────────────────────────────────────
    Godot 读 `export_presets.cfg` 时,**没写的键会用该平台的默认值**。
    键写得越少,跨 Godot 小版本(4.2 / 4.4 / 4.7 之间有些键改过名)越不容易
    踩到"类型对不上导致整个文件解析失败"。所以这里只写那些跨 4.x 稳定、
    且确实必要的键,其余交给 Godot 自己补。

    `binary_format/embed_pck=true`:把资源打进 exe 里,出来的是**单个文件**,
    拷给别人就能跑 —— 否则会多一个同名 .pck,用户容易漏拷。
    """
    title = title or "game"
    path = (export_path or "").replace("\\", "/")
    return f"""; 由 BlenderGameKit 自动生成 —— 只在项目里**没有** export_presets.cfg
; 时创建一次。之后你在 Godot 编辑器『项目 -> 导出』里改的设置都写在这个
; 文件里,插件不会再覆盖它。
; 想重置成默认:把这个文件删掉,再回 Blender 点一次『打包成可执行文件』。

[preset.0]

name="{preset_name}"
platform="Windows Desktop"
runnable=true
advanced_options=false
dedicated_server=false
export_filter="all_resources"
include_filter=""
exclude_filter=""
export_path="{path}"
patches=PackedStringArray()
encryption_include_filters=""
encryption_exclude_filters=""
encrypt_pck=false
encrypt_directory=false

[preset.0.options]

custom_template/debug=""
custom_template/release=""
debug/export_console_wrapper=1
binary_format/embed_pck=true
binary_format/architecture="x86_64"
codesign/enable=false
application/product_name="{title}"
"""


def read_preset_name(directory):
    """从已有的 export_presets.cfg 里读第一个预设名;读不到返回 None。

    为什么需要:用户可能把预设改过名(或建了多个平台)。这时我们该用**他的**
    预设名去导出,而不是硬塞 "Windows Desktop" —— 否则命令行会报找不到预设。
    """
    text = _read(directory, EXPORT_PRESETS_NAME)
    if text is None:
        return None
    names = re.findall(r'^name="([^"]*)"', text, re.MULTILINE)
    return names[0] if names else None


def read_export_path(directory):
    """从已有的预设里读 `export_path`;读不到返回 None。"""
    text = _read(directory, EXPORT_PRESETS_NAME)
    if text is None:
        return None
    match = re.search(r'^export_path="([^"]*)"', text, re.MULTILINE)
    if not match or not match.group(1).strip():
        return None
    return match.group(1).strip()


def default_output_path(directory, name, ext=".exe"):
    """默认的打包产物路径: `<项目>/dist/<名字>.exe`(绝对路径)。"""
    return os.path.join(directory, DIST_DIR, f"{name}{ext}")


def rel_output_path(directory, path):
    """把产物路径转成写进预设里的那种项目相对路径(dist/xxx.exe)。"""
    if not directory or not path:
        return (path or "").replace("\\", "/")
    try:
        rel = os.path.relpath(path, directory)
    except ValueError:
        return path.replace("\\", "/")
    return rel.replace("\\", "/")


def ensure_export_presets(directory, title="game",
                          preset_name=WINDOWS_PRESET_NAME, export_path=""):
    """缺 `export_presets.cfg` 就生成一份。返回 (路径, 是否新建)。

    **已有文件一律原样保留** —— 见模块 docstring 第 1 条。
    """
    path = os.path.join(directory, EXPORT_PRESETS_NAME)
    if os.path.isfile(path):
        return path, False
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(export_presets_text(title=title, preset_name=preset_name,
                                         export_path=export_path))
    return path, True


# ---------------------------------------------------------------------------
# 导出模板探测
# ---------------------------------------------------------------------------
def export_templates_root():
    """Godot 导出模板的根目录(里面按版本号分子目录)。"""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, "Godot", "export_templates")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library",
                            "Application Support", "Godot",
                            "export_templates")
    data = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return os.path.join(data, "godot", "export_templates")


def find_template_dir(version, root=None):
    """在导出模板根目录里找**匹配这个版本**的子目录。

    目录名形如 `4.7.2.stable`,而版本号可能有 `4.7` 或 `4.7.2` 两种精度,
    所以这里用前缀匹配(4.7 能匹配到 4.7.2.stable)。
    """
    root = root or export_templates_root()
    if not version or not os.path.isdir(root):
        return None
    matched = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return None
    for entry in entries:
        full = os.path.join(root, entry)
        if os.path.isdir(full) and entry.startswith(version):
            matched.append(full)
    return matched[-1] if matched else None


def windows_release_template(directory):
    """版本目录里的 Windows 发布模板文件名;没有返回 None。

    文件名在各版本间变过(`windows_64_release.exe` / `windows_release_x86_64.exe`),
    所以按"含 windows 且含 release"来认,不写死具体名字。
    """
    if not directory or not os.path.isdir(directory):
        return None
    try:
        entries = os.listdir(directory)
    except OSError:
        return None
    for entry in entries:
        low = entry.lower()
        if "readme" in low or low.endswith(".txt"):
            continue
        if "windows" in low and "release" in low:
            return entry
    return None


def template_install_hint(version=""):
    """模板缺失时给用户看的"去哪儿装"说明(要能照着做)。"""
    root = export_templates_root()
    return (
        f"没装 Godot 导出模板(需要版本 {version or '?'})。\n"
        f"Godot 会去这里找: {root}\\<版本>\\windows_release_x86_64.exe\n"
        "装法:打开 Godot 编辑器 -> 顶部菜单『编辑器』->『管理导出模板』-> "
        "点『下载并安装』;\n"
        f"它会自动装到 {root}。装完再回 Blender 点一次『打包成可执行文件』。"
    )


def template_missing_in_log(log):
    """Godot 的日志是不是在说"导出模板没装"。

    ── 为什么要中英都认(真机踩到的)────────────────────────────────
    Godot 的编辑器输出是**跟界面语言走的**:中文界面上它打的是
    「在预期路径处未找到导出模板： .../windows_release_x86_64.exe」,
    英文界面上才是 "No export template found"。只认英文的话,中文用户
    (本插件的主要用户)看到的永远是一句我们没翻译的 Godot 原文。
    """
    low = (log or "").lower()
    if not low:
        return False
    if "export template" in low:
        return True
    return "导出模板" in low and ("未找到" in low or "找不到" in low)


def templates_status(version):
    """导出模板是否就绪。返回 (是否就绪, 版本目录, 说明文字)。"""
    root = export_templates_root()
    directory = find_template_dir(version, root)
    if directory is None:
        return False, None, (
            f"没装 Godot 导出模板:在 {root} 里找不到版本 {version or '?'}。\n"
            + template_install_hint(version))
    found = windows_release_template(directory)
    if found is None:
        return False, directory, (
            f"{directory} 里没有 Windows 发布模板文件。\n"
            + template_install_hint(version))
    return True, directory, f"导出模板已就绪: {found}"


# ---------------------------------------------------------------------------
# 命令拼装与执行
# ---------------------------------------------------------------------------
def release_command(executable, directory, preset_name, output_path):
    """拼出 Godot 导出可执行文件的命令行(列表形式,交给 subprocess)。

    `--headless`:打包不需要窗口,用户点完还能继续在 Blender 里干活。
    `--export-release`:用 release 预设导出(不是 debug)。
    """
    return [executable, "--headless", "--path", directory,
            "--export-release", preset_name, output_path]


def run_release(executable, directory, preset_name, output_path, timeout=600):
    """跑一次打包。返回 (是否成功, 日志文本)。

    只用退出码判成败 —— Godot 导出期会打一些 `ERROR:` 字样但并非致命
    (比如渲染特性回退),拿日志关键词判成败会误报。真出问题时退出码是 1。
    """
    args = release_command(executable, directory, preset_name, output_path)
    try:
        if sys.platform.startswith("win"):
            # 不给 Godot 弹控制台窗口(和『直接运行游戏』一致)
            creation = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
            completed = subprocess.run(args, creationflags=creation,
                                       capture_output=True, text=True,
                                       encoding="utf-8", errors="replace",
                                       timeout=timeout, check=False)
        else:
            completed = subprocess.run(args, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace",
                                       timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return False, f"打包超时({timeout} 秒)"
    except OSError as exc:
        return False, f"启动 Godot 失败: {exc}"
    log = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode == 0, log


def failure_hint(log, version=""):
    """把 Godot 的失败日志翻译成"下一步该做什么"。"""
    text = log or ""
    low = text.lower()
    if template_missing_in_log(text):
        return template_install_hint(version)
    if ("preset" in low and any(token in low for token in
                                ("not found", "couldn", "can't", "invalid",
                                 "no preset"))
            or ("预设" in low and ("找不到" in low or "未找到" in low))):
        return ("没找到可用的导出预设。删掉项目里的 export_presets.cfg 再点一次"
                "打包(插件会重新生成一份)")
    tail = "\n".join(line for line in text.splitlines() if line.strip())[-600:]
    return f"Godot 返回:\n{tail}" if tail else "Godot 没给出更多信息"


def _read(directory, name):
    """读项目里的一个文件;读不到返回 None。"""
    if not directory:
        return None
    try:
        with open(os.path.join(directory, name), "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None
