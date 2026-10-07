"""导出物的清单 + 旧文件清理(用户 2026-10-04 第十三轮)。

── 为什么需要它(两次真踩过的坑)──────────────────────────────────
导出是"把这一版该有的文件写出来",但从来不删**上一版写过、这一版不再
需要的**文件。于是项目里会慢慢积起旧产物,而它们会以很难懂的方式捣乱:

  * 两关的材质同名,后导的把前一份盖掉 —— 用户看到"关卡示例的东墙
    莫名变成红色"(第十轮);
  * 上一版留下的旧脚本带着同一个 `class_name`,Godot 直接报
    `Class "X" already exists` —— **整个工程都跑不起来**(第十一轮)。

名字撞车这一类已经各自从根上修掉了,但"旧文件还在"这件事本身没解决。
所以这里做一件事:**导出成功后,把上一次写过、这一次没写的文件删掉**。

── 三条安全规则(删文件这件事必须胆小)──────────────────────────
1. 只删**清单里记过的**文件 —— 用户自己往 `scripts/`、`materials/` 里放的
   东西不在清单里,永远不动;
2. 只删**插件自己的目录**里的一级子路径(scripts / scenes / art /
   materials / shaders / curves),别的一律不碰;
3. **导出失败就不删** —— 调用方只在整次导出全部成功后才调这里。

另外还给老工程准备了"认领":第一次导出时项目里还没有清单,那些**内容里
带插件标记**的旧文件会被认领进清单(见 `is_generated_file`),这样它们
下一轮就在清理范围内 —— 否则老工程里的脏文件永远不会被清掉。
"""

import json
import os
import re
import tempfile

from . import paths, project


# 清单文件名(放在项目根目录)。带点前缀 = 不会被 Godot 当成资源。
#
# ── 为什么不叫 .gk_generated(真撞过一次)──────────────────────────
# `.gk_generated` 已经被 project/metadata.py 占用了(它记"主场景是插件
# 设的哪一个",内容是 `{"main_scene": ...}`)。第一版我用了同一个名字,
# 结果两边互相覆盖:清理把主场景记录清了、metadata 把文件清单清了,
# 表现是"导出后主场景突然乱指"。两个东西各用各的文件,别抢名字。
MANIFEST_NAME = ".gk_export_files"
MANIFEST_VERSION = 1

# 只允许在这几个目录里删东西(全部是插件生成的目录)
GENERATED_DIRS = (project.SCRIPTS_DIR, project.SCENES_DIR, project.ART_DIR,
                  project.MATERIALS_DIR, project.SHADERS_DIR,
                  project.CURVES_DIR, project.AUDIO_DIR)

# 曲线资源文件名的后缀(`名字_8位十六进制.tres`,见 curve_assets.curve_file_name)
_CURVE_NAME_RE = re.compile(r"_[0-9a-f]{8}\.tres$", re.IGNORECASE)


def manifest_path(directory):
    return os.path.join(directory, MANIFEST_NAME)


# ==========================================================================
# 内容嗅探:这个文件是不是插件生成的?
# ==========================================================================
def _head(path, size=2048):
    """读文件开头一小段(读不动就当空)。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(size)
    except OSError:
        return ""


def _plugin_shader_names():
    """插件自带着色器的文件名集合(shaders/ 里同名的就是插件写的)。"""
    try:
        from . import asset_export
        folder = asset_export.shader_source_dir()
    except Exception:                                     # noqa: BLE001
        return set()
    try:
        return {name for name in os.listdir(folder)
                if name.endswith((".gdshader", ".gdshaderinc"))}
    except OSError:
        return set()


def is_generated_file(path):
    """只靠**内容里的标记**判断"这是插件生成的",不靠名字猜。

    ── 为什么不能用名字(踩过的坑)────────────────────────────────
    `scripts/game_logic.gd` 这种名字用户也可能自己写一个;反过来插件的
    产物会有 `角色逻辑.gd` 这种名字。名字不可靠,内容可靠 ——
    生成的脚本头两行就是"由 BlenderGameKit 自动生成",材质一定引用
    `gk_toon.gdshader`,场景一定引用 `gk_environment` 脚本。

    返回 True 只说明"可以认领进清单";**删不删还要再看它在不在旧清单里**
    (见 stale_files),所以这里的判断宽松一点是安全的。
    """
    name = os.path.basename(path)
    folder = os.path.basename(os.path.dirname(path))
    ext = os.path.splitext(name)[1].lower()
    if folder == project.SCRIPTS_DIR and ext == ".gd":
        text = _head(path, 3000)
        # 两个都是插件的固定话术:生成的逻辑脚本是"由 BlenderGameKit 自动
        # 生成…",环境脚本是"BlenderGameKit 生成:把 glb 带进来的相机…"。
        # 用**整句话**而不是单独一个词,免得用户脚本里提一句插件就被认领。
        return ("由 BlenderGameKit 自动生成" in text
                or "BlenderGameKit 生成" in text)
    if folder == project.MATERIALS_DIR and ext == ".tres":
        return "gk_toon" in _head(path, 4096)
    if folder == project.CURVES_DIR and ext == ".tres":
        return bool(_CURVE_NAME_RE.search(name))
    if folder == project.AUDIO_DIR and ext in (".wav", ".ogg", ".mp3"):
        # 音频没法靠"内容标记"认领(二进制、没有可读的头)。
        # 靠**目录 + 扩展名**:audio/ 只有资产管线(『资产_声音卡』)会写,
        # 而且删不删还要再看"它在不在上一轮的清单里"(见 stale_files)——
        # 用户自己放进 audio/ 的文件从没进过清单,永远不会被删。
        return True
    if folder == project.SHADERS_DIR and ext in (".gdshader", ".gdshaderinc"):
        return name in _plugin_shader_names()
    if folder == project.SCENES_DIR and ext == ".tscn":
        text = _head(path, 4096)
        return "BlenderGameKit" in text or "gk_environment" in text
    return False


def scan_generated_files(directory):
    """扫一遍插件目录,返回**能认领的**文件的相对路径(正斜杠)。"""
    found = []
    for folder in GENERATED_DIRS:
        full = os.path.join(directory, folder)
        if not os.path.isdir(full):
            continue
        for name in sorted(os.listdir(full)):
            path = os.path.join(full, name)
            if not os.path.isfile(path):
                continue
            if is_generated_file(path):
                found.append(f"{folder}/{name}")
    return found


# 生成的文本里引用的资源(`res://art/World.glb` / `res://curves/x.tres`…)
#
# ── 为什么还要"看扩展名"(踩过的坑)───────────────────────────────
# 第一版只写了字符类,于是脚本注释里那句
#   `形如 "Ball=res://materials/Ballmat.tres|Floor=..."`
# 后面的中文解释也被当成路径抓进来了("res://路径里都不会出现…"),
# 清单里多出一条莫名其妙的条目。所以:遇到引号/竖线/空格/括号就停,
# 而且**只认已知的扩展名** —— 宁少不多。
_RES_REF_RE = re.compile(r"res://([^\"'\s|()=<>]+)")
_RES_EXTS = (".gd", ".tscn", ".tres", ".glb", ".png", ".gdshader",
             ".gdshaderinc", ".json", ".import",
             # 音频资产(第四十三轮):脚本里写死 `res://audio/x.wav`,
             # 清单判定"被引用到"时必须认得它,否则刚复制的音频会被
             # 当成旧文件清掉(实测踩过)
             ".wav", ".ogg", ".mp3")
# 每个文件最多读这么多字符找引用(生成的脚本/场景都不大,这是防呆)
_REF_READ_LIMIT = 400000


def referenced_files(directory, relatives):
    """这些文件里 `res://` 引用到的、项目内的文件(相对路径)。

    ── 为什么清理要看"引用"而不只看"写过"(真的踩过一次)────────────
    插件有两条导出路径:『导出 Godot 脚本』**先**把脚本(和曲线资源)写了,
    『导出资产』**后**装配场景。如果清理只承认"这次流水线写的文件",
    那么后一步会把前面刚写好的脚本/曲线当成"上一版的残留"删掉 ——
    自毁。所以再加一条判据:**被这次导出的东西引用到的文件,一律保留**。
    引用的链条是天然的:场景 -> 脚本 / 环境脚本 / 模型,
    脚本 -> 曲线资源,材质 -> 着色器 / 贴图。
    """
    found = set()
    for rel in relatives or ():
        path = os.path.join(directory, str(rel).replace("/", os.sep))
        try:
            if os.path.getsize(path) > _REF_READ_LIMIT:
                continue
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read(_REF_READ_LIMIT)
        except OSError:
            continue
        for match in _RES_REF_RE.findall(text):
            item = match.strip().replace("\\", "/").lstrip("/")
            if not item or not item.lower().endswith(_RES_EXTS):
                continue
            if item not in found:
                found.add(item)
    return found


def reachable_files(directory, written, passes=3):
    """把"这次导出真正需要"的文件算全(写过 + 被引用,迭代几轮)。

    ── 为什么要迭代 ──────────────────────────────────────────────
    引用是一层套一层的:场景 -> 脚本 -> 曲线 -> 着色器 -> 贴图。只算一轮
    会漏掉后面的层级,那些文件就会被误判成"旧文件"。轮数固定为 3 轮:
    链子最长就这么长,再深也不会多出来。

    另外**只从"已经确定要保留"的文件里往外找引用** —— 旧场景文件自己
    引用的东西不算数(不然一个旧场景会把它的旧脚本一起"保"下来,
    而旧脚本正是 `class_name` 冲突的来源)。
    """
    keep = {str(item).replace("\\", "/") for item in (written or ())}
    for _round in range(max(1, passes)):
        if not keep:
            break
        grown = keep | referenced_files(directory, sorted(keep))
        if grown == keep:
            break
        keep = grown
    return keep


# ==========================================================================
# 清单读写
# ==========================================================================
def read_manifest(directory):
    """读上一次的清单。没有 / 坏了都返回空表(绝不让它把导出搞崩)。"""
    path = manifest_path(directory)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, list):
        return []
    out = []
    for item in files:
        text = str(item or "").strip().replace("\\", "/").lstrip("/")
        if text and text not in out:
            out.append(text)
    return out


def write_manifest(directory, relative_files):
    """把这一版写了哪些文件记下来(原子写:不留下半截清单)。"""
    path = manifest_path(directory)
    payload = {
        "version": MANIFEST_VERSION,
        "generator": "BlenderGameKit",
        "files": sorted(set(relative_files)),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=directory or ".",
        prefix=".gk_tmp_", suffix=".part", delete=False)
    try:
        with handle:
            handle.write(text)
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    return path


def relative_files(directory, absolute_paths):
    """绝对路径 -> 项目内相对路径(正斜杠);不在项目内的丢掉。

    ── 大小写只用来"判断在不在项目里",不能用来算相对路径(踩过的坑)──
    Windows 路径比较要用 `normcase`(大小写不敏感),但 `os.path.relpath`
    的结果**必须是原始大小写** —— 第一版把 normcase 过的路径喂给 relpath,
    于是 `scenes/World.tscn` 变成 `scenes/world.tscn`,和磁盘上的名字对不上,
    清理就把刚写好的主场景当成"旧文件"删了(示例导出时真删掉了)。
    """
    root = paths.normalize_dir(directory)
    out = []
    if not root:
        return out
    root_key = os.path.normcase(root)
    prefix = root_key + os.sep
    for item in absolute_paths or ():
        text = str(item or "").strip()
        if not text:
            continue
        full = paths.normalize_dir(text)
        if not os.path.normcase(full).startswith(prefix):
            continue
        rel = os.path.relpath(full, root).replace("\\", "/")
        if rel and rel not in out:
            out.append(rel)
    return out


# ==========================================================================
# 清理
# ==========================================================================
def stale_files(directory, current, previous):
    """上一次写过、这一次没写的(相对路径)。"""
    keep = {str(item).replace("\\", "/") for item in (current or ())}
    out = []
    for rel in previous or ():
        text = str(rel or "").replace("\\", "/").lstrip("/")
        if not text or text in keep:
            continue
        if text not in out:
            out.append(text)
    return out


def removable_path(directory, relative):
    """相对路径 -> 可以安全删除的绝对路径;不允许删时返回 None。

    ── 这里挡的是"清单被人手改过" / "清单被别处复制过来"──────────────
    清单是普通文本文件,用户可能自己编辑(甚至把别的工程的清单拷进来)。
    所以删除前**一条条再查**:

      1. 必须是 `<插件目录>/<文件名>` 这种两级路径(不带子目录、不在根目录);
      2. 目录必须是插件自己的那几个(scripts / scenes / art / materials /
         shaders / curves);
      3. 必须还在项目根内(`safe_join_project`,防目录穿越);
      4. 必须不是隐藏文件 / README;
      5. **必须还能从内容上认出是插件生成的** —— 这一条是最后一道保险:
         万一清单里混进了用户自己的文件(手改、拷错清单),也不能删。
         代价是 `.glb` / `.png` 这类**读不出标记**的二进制旧文件不清理
         (它们只是没人引用,不会让工程坏掉,宁可留着)。
    """
    parts = str(relative or "").replace("\\", "/").split("/")
    if len(parts) != 2 or not all(parts):
        return None
    folder, name = parts
    if folder not in GENERATED_DIRS:
        return None
    if name.startswith(".") or name.lower() in ("readme.md",):
        return None
    try:
        full = paths.safe_join_project(directory, folder, name)
    except ValueError:
        return None
    if not os.path.isfile(full):
        return None
    if not is_generated_file(full):
        # 认不出标记 = 不删(可能是用户自己的东西)
        return None
    return full


def remove_files(directory, relatives):
    """删掉一批相对路径,返回**真的删掉了的**相对路径列表。"""
    deleted = []
    for rel in relatives:
        full = removable_path(directory, rel)
        if full is None:
            continue
        try:
            os.remove(full)
        except OSError:
            continue
        deleted.append(rel)
    return deleted


def cleanup_after_export(directory, written_paths):
    """一次完整导出成功之后:认领 + 记账 + 删旧的。返回删掉的相对路径。

    `written_paths` 是本次导出真的写出来的文件(绝对路径)。判定"要保留"
    的最终依据是 `reachable_files`(写过 + 被引用到的)—— 这样脚本先导、
    场景后导的两段式流程也不会互相误删(见 referenced_files 的说明)。
    """
    if not directory or not os.path.isdir(directory):
        return []
    previous = read_manifest(directory)
    marked = scan_generated_files(directory)
    if not previous:
        # 老工程(还没有清单):把"内容能认出是插件生成的"文件认领成上一轮
        # 的产物 —— 否则那些旧残留永远不会被清掉(它们正是坏事的源头)。
        previous = list(marked)
    keep = reachable_files(directory, relative_files(directory, written_paths))
    removed = remove_files(directory, stale_files(directory, keep, previous))
    # 新清单 = 现在还在的插件文件 + 这一轮保留的东西(下次导出就按它比)
    write_manifest(directory, sorted(set(scan_generated_files(directory))
                                     | keep))
    return removed