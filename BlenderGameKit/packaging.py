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
# 平台表(第六十八轮:从"只有 Windows"扩到多平台)
# ---------------------------------------------------------------------------
# ── 为什么是一张表,而不是一堆 if ──────────────────────────────────
# 每个平台要的东西是**同一组**:Godot 认的平台名、预设名、产物扩展名、
# 发布模板文件名、在哪台主机上能导。写在一起,加平台 = 加一行。
#
# `hosts` 是**主机**限制:macOS 的导出 Godot 只在 macOS 上做(它要调
# codesign / hdiutil),别的平台点了也是白等 —— 界面上要明说。
PLATFORM_ORDER = ("windows", "linux", "web", "android", "macos")

_ALL_HOSTS = ("win32", "linux", "darwin")

PLATFORMS = {
    "windows": {
        "label": "Windows (.exe)",
        "preset_name": "Windows Desktop",
        "godot_platform": "Windows Desktop",
        "ext": ".exe",
        "template": "windows_release_x86_64.exe",
        "template_ext": ".exe",
        "template_tokens": ("windows", "release"),
        "hosts": _ALL_HOSTS,
    },
    "linux": {
        "label": "Linux (.x86_64)",
        "preset_name": "Linux",
        "godot_platform": "Linux",
        "ext": ".x86_64",
        "template": "linux_release.x86_64",
        "template_ext": ".x86_64",
        "template_tokens": ("linux", "release"),
        "hosts": _ALL_HOSTS,
    },
    "web": {
        "label": "Web (HTML5 .zip)",
        "preset_name": "Web",
        "godot_platform": "Web",
        "ext": ".zip",
        "template": "web_release.zip",
        "template_ext": ".zip",
        "template_tokens": ("web", "release"),
        "hosts": _ALL_HOSTS,
    },
    "android": {
        "label": "Android (.apk)",
        "preset_name": "Android",
        "godot_platform": "Android",
        "ext": ".apk",
        "template": "android_release.apk",
        "template_ext": ".apk",
        "template_tokens": ("android", "release"),
        "hosts": _ALL_HOSTS,
    },
    "macos": {
        "label": "macOS (.zip)",
        "preset_name": "macOS",
        "godot_platform": "macOS",
        "ext": ".zip",
        "template": "macos.zip",
        "template_ext": ".zip",
        "template_tokens": ("macos",),
        # Godot 只在 macOS 上导 macOS(要 codesign / hdiutil)
        "hosts": ("darwin",),
    },
}


def platform_label(platform):
    """给人看的平台名;认不出来就原样返回。"""
    spec = PLATFORMS.get(platform)
    return spec["label"] if spec else str(platform)


def platform_buildable(platform, host=None):
    """这个平台在**当前主机**上能不能导出(macOS 只能在 macOS 上)。"""
    spec = PLATFORMS.get(platform)
    if spec is None:
        return False
    return (host or sys.platform) in spec["hosts"]


def selected_platforms(settings):
    """场景设置里勾了哪些平台(按 PLATFORM_ORDER 排序)。

    **纯读 getattr**(不碰 bpy),离线喂个假对象就能测。一个都没勾 / 老工程
    没有这些字段 -> 空表 —— 由调用方给出"没勾任何平台"的提示,不瞎猜。
    """
    if settings is None:
        return []
    return [key for key in PLATFORM_ORDER
            if bool(getattr(settings, f"package_{key}", False))]



# ---------------------------------------------------------------------------
# 预设文件
# ---------------------------------------------------------------------------
def platform_options(platform, android_id="", version=""):
    """`[preset.N.options]` 里除了 product_name 还要写哪些键。

    ── 为什么捏着这么几个键 ────────────────────────────────────────
    Godot 读预设时**没写的键用该平台默认值**。写得越少,跨 4.x 小版本
    (有些键改过名)越不容易踩到"类型对不上导致整份文件解析失败"。
    所以这里只写"不写就会出事"的那几个:
      * Windows / Linux:embed_pck —— 打进可执行文件里,产物是**单个文件**,
        拷给别人就能跑(否则会多一个同名 .pck,用户容易漏拷);
      * Android:包名(不写 Godot 直接拒)+ 版本号 + **不走 gradle**
        (走它就要 Android SDK / gradle 那一整套,门槛太高);
      * macOS:bundle 架构。
    """
    if platform in ("windows", "linux"):
        return ["debug/export_console_wrapper=1",
                "binary_format/embed_pck=true",
                'binary_format/architecture="x86_64"',
                "codesign/enable=false"]
    if platform == "android":
        return [
            "gradle_build/use_gradle_build=false",
            f'package/unique_name="{android_id or "com.example.game"}"',
            f'version/name="{version or "1.0"}"',
            "version/code=1",
        ]
    if platform == "macos":
        return ['binary_format/architecture="universal"']
    return []


def export_presets_text(title="game", preset_name=WINDOWS_PRESET_NAME,
                        export_path="", platform="windows", index=0,
                        android_id="", version=""):
    """生成一段 `<platform>` 的导出预设文本。

    `index` 是 `[preset.N]` 里的那个 N —— 一份 `export_presets.cfg` 可以装
    **多个预设**(多平台就靠这个);第六十八轮的"追加缺失平台"会传 0、1、2…
    """
    title = title or "game"
    path = (export_path or "").replace("\\", "/")
    spec = PLATFORMS.get(platform) or PLATFORMS["windows"]
    options = platform_options(platform, android_id=android_id, version=version)
    body = "\n".join(options + [f'application/product_name="{title}"'])
    return f"""; 由 BlenderGameKit 自动生成 —— 只在项目里**没有** export_presets.cfg
; 时创建一次;已经有的话,缺哪个平台就**追加**哪一段(写前会备份一份 .bak)。
; 之后你在 Godot 编辑器『项目 -> 导出』里改的设置都写在这个文件里,
; 插件不会再覆盖它。
; 想重置成默认:把这个文件删掉,再回 Blender 点一次『打包』。

[preset.{index}]

name="{preset_name}"
platform="{spec['godot_platform']}"
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

[preset.{index}.options]

{body}
"""


def platform_presets_text(platform, title="game", preset_name="",
                          export_path="", index=0, android_id="", version=""):
    """按平台生成预设文本(预设名留空 = 用该平台的默认名)。"""
    spec = PLATFORMS.get(platform)
    if spec is None:
        raise ValueError(f"未知平台: {platform}")
    return export_presets_text(title=title,
                               preset_name=preset_name or spec["preset_name"],
                               export_path=export_path, platform=platform,
                               index=index, android_id=android_id,
                               version=version)


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


def read_platform_preset(directory, platform):
    """已有预设里跟这个平台对得上的那一条:(预设名, export_path)。

    ── 为什么不能"读第一条"了(第六十八轮)──────────────────────────
    一份 `export_presets.cfg` 里可以有多个预设(多平台就靠这个)。原来只会
    读第一条,扩到多平台后就会拿**别的平台**的预设名去导 —— 命令行报
    "找不到预设";或者把产物丢到用户没预期的地方。

    用户在 Godot 编辑器里配过的东西优先:他可能把输出路径改到别处了。
    没配过 -> (None, None),由调用方用默认预设名 / 默认路径。
    """
    text = _read(directory, EXPORT_PRESETS_NAME)
    spec = PLATFORMS.get(platform)
    if not text or spec is None:
        return None, None
    parts = re.split(r'^\[preset\.(\d+)\]\s*$', text, flags=re.MULTILINE)
    # parts[0] 是文件头;之后每两段是一份预设(序号, 正文)
    for index in range(1, len(parts) - 1, 2):
        body = parts[index + 1]
        found = re.search(r'^platform="([^"]*)"', body, re.MULTILINE)
        if not found or found.group(1) != spec["godot_platform"]:
            continue
        name = re.search(r'^name="([^"]*)"', body, re.MULTILINE)
        path = re.search(r'^export_path="([^"]*)"', body, re.MULTILINE)
        configured = path.group(1).strip() if path else ""
        return (name.group(1) if name else None, configured or None)
    return None, None


def read_preset_platforms(directory):
    """从已有预设里读出已经**有**哪些平台(按文件里的先后顺序)。

    纯读文件 + 正则;文件不在 / 读不到 -> 空表(用空表当"什么平台都没有")。
    """
    return _platforms_in_text(_read(directory, EXPORT_PRESETS_NAME) or "")


def _platforms_in_text(text):
    """预设文本里出现过哪些 `platform="..."`。"""
    return re.findall(r'^platform="([^"]*)"', text or "", re.MULTILINE)


def _next_preset_index(text):
    """下一个可用的 `[preset.N]` 序号(空文件 -> 0)。"""
    numbers = [int(value) for value in
               re.findall(r'^\[preset\.(\d+)(?:\.options)?\]', text or "",
                          re.MULTILINE)]
    return (max(numbers) + 1) if numbers else 0


def append_missing_presets(directory, platforms, title="game",
                           android_id="", version="", output_paths=None):
    """把**缺的平台**追加进 `export_presets.cfg`,返回补了哪些平台。

    ── 为什么是"追加"而不是"重写"(用户第六十八轮选的)──────────────
    用户在 Godot 编辑器里调过的东西(图标 / 权限 / 架构 / 输出路径)全写在
    这个文件里,**重写等于把它们抹掉**。所以这里只做一件事:看文件里
    已经有哪些 `platform="..."`,缺哪段补哪段 —— 老内容**一个字节不动**。

    ── 备份 ───────────────────────────────────────────────────────
    写之前把原文另存一份 `<文件>.bak`(叫 .bak 是为了不和 Godot 的
    `export_presets.cfg` 混起来)。文件本来就不存在时**不留**空备份。

    返回补上的平台 key 列表(顺序同传入);一个都没缺 -> 空表。
    """
    path = os.path.join(directory, EXPORT_PRESETS_NAME)
    had_file = os.path.isfile(path)
    existing = (_read(directory, EXPORT_PRESETS_NAME) or "") if had_file else ""
    present = _platforms_in_text(existing)
    wanted = [key for key in platforms if key in PLATFORMS]
    missing = [key for key in wanted
               if PLATFORMS[key]["godot_platform"] not in present]
    if not missing:
        return []

    index = _next_preset_index(existing)
    blocks = []
    for offset, key in enumerate(missing):
        blocks.append(platform_presets_text(
            key, title=title, export_path=(output_paths or {}).get(key, ""),
            index=index + offset, android_id=android_id, version=version))

    os.makedirs(directory, exist_ok=True)
    if had_file:
        try:
            with open(path + ".bak", "w", encoding="utf-8", newline="\n") as handle:
                handle.write(existing)
        except OSError:
            pass
    text = existing
    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n".join(blocks)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return missing


def default_output_path(directory, name, ext=".exe"):
    """默认的打包产物路径: `<项目>/dist/<名字><扩展名>`(绝对路径)。"""
    return os.path.join(directory, DIST_DIR, f"{name}{ext}")


def platform_output_path(directory, name, platform):
    """按平台算默认产物路径(扩展名跟着平台走:.exe / .apk / .zip / .x86_64)。"""
    spec = PLATFORMS.get(platform) or PLATFORMS["windows"]
    return default_output_path(directory, name, ext=spec["ext"])


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


def platform_template_file(platform, directory):
    """版本目录里这个平台的**发布**模板文件名;没有返回 None。

    ── 两步走(名字在各版本间变过)────────────────────────────────────
    1. 先认**精确名**(Godot 4.7 就是 `windows_release_x86_64.exe` 这一套);
    2. 认不出再退回**关键词**匹配:文件名要带全 tokens、扩展名要对,
       并且**排除 debug / console** —— 它们也在同一个目录里
       (`windows_release_arm64_console.exe`、`android_debug.apk`),
       错认成 debug 版会打出"带控制台 / 不能发布"的东西。
       多个都命中时取**最短**的那个:web 目录里有
       `web_release.zip` / `web_dlink_release.zip` / `web_nothreads_release.zip`,
       最短的正是那份最朴素的。
    """
    spec = PLATFORMS.get(platform)
    if spec is None or not directory or not os.path.isdir(directory):
        return None
    try:
        entries = os.listdir(directory)
    except OSError:
        return None
    if spec["template"] in entries:
        return spec["template"]
    hits = []
    for entry in entries:
        low = entry.lower()
        if "readme" in low or low.endswith(".txt"):
            continue
        if not low.endswith(spec["template_ext"]):
            continue
        if "debug" in low or "console" in low:
            continue
        if all(token in low for token in spec["template_tokens"]):
            hits.append(entry)
    return min(hits, key=len) if hits else None


def windows_release_template(directory):
    """版本目录里的 Windows 发布模板文件名;没有返回 None。"""
    return platform_template_file("windows", directory)


def template_install_hint(version="", platform="windows"):
    """模板缺失时给用户看的"去哪儿装"说明(要能照着做)。"""
    root = export_templates_root()
    spec = PLATFORMS.get(platform) or PLATFORMS["windows"]
    return (
        f"没装 Godot 导出模板(需要版本 {version or '?'})。\n"
        f"Godot 会去这里找: {root}\\<版本>\\{spec['template']}\n"
        "装法:打开 Godot 编辑器 -> 顶部菜单『编辑器』->『管理导出模板』-> "
        "点『下载并安装』;\n"
        f"它会自动装到 {root}。装完再回 Blender 点一次『打包』。"
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


def templates_status(version, platform="windows"):
    """这个平台的导出模板是否就绪。返回 (是否就绪, 版本目录, 说明文字)。

    第六十八轮起按平台查 —— 模板是**一个平台一份**的:装了 Windows 不等于
    装了 Android,所以界面上的状态也得分平台显示。
    """
    spec = PLATFORMS.get(platform) or PLATFORMS["windows"]
    root = export_templates_root()
    directory = find_template_dir(version, root)
    if directory is None:
        return False, None, (
            f"没装 Godot 导出模板:在 {root} 里找不到版本 {version or '?'}。\n"
            + template_install_hint(version, platform))
    found = platform_template_file(platform, directory)
    if found is None:
        return False, directory, (
            f"{directory} 里没有 {spec['label']} 的发布模板"
            f"(需要 {spec['template']})。\n"
            + template_install_hint(version, platform))
    return True, directory, f"{spec['label']} 导出模板已就绪: {found}"


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
    # ── 产物目录必须先存在(真机踩到的坑)────────────────────────────
    # Godot **不会**替你建导出目录:目录不在就报
    #     ERROR: 准备模板: 给定的导出路径不存在。
    # 这条错误文案完全没提"目录",很容易被当成预设写坏了。放在这里做,
    # 是为了让**任何**调用方(算子 / 门禁脚本 / 以后的新入口)都不会漏。
    parent = os.path.dirname(output_path)
    if parent:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            return False, f"创建导出目录失败: {exc}"

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
