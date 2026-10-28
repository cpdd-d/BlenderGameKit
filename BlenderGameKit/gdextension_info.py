"""检查 Godot 工程里的 GDExtension 声明与实际动态库对不对得上。

为什么单独一个模块(不写在 diagnostics.py 里):diagnostics 顶层 import bpy,
而这段逻辑是纯标准库 —— 拆出来,离线测试(不需要 Blender)才能跑。

背景(2026-10-29):
    `.gdextension` 的 `[libraries]` 右边是**实际文件名**。写错时 Godot
    **不报错**,只是不加载这个扩展;表现是"GDScript 里找不到那个类",
    极难查。这里把"声明的库"和"磁盘上的文件"逐条对一遍,缺的直接标出来。

    另一个常见错法:`target=template_debug` 编出来的文件名是
    `xxx.windows.template_debug.x86_64.dll`,而很多人照着惯性写成
    `xxx.windows.debug.x86_64.dll` —— 后缀对不上,同样是静默不加载。
"""

import configparser
import os

# Godot 自己的缓存、导出产物、版本库目录都不该参与扫描
SKIP_DIRS = {".godot", "build", "dist", "__pycache__", ".git"}


def resolve_library_path(directory, value):
    """把 `.gdextension` 里写的 `res://…` 换成磁盘上的真实路径。"""
    value = (value or "").strip().strip('"').strip("'")
    if value.startswith("res://"):
        value = value[len("res://"):]
    return os.path.join(directory, value.replace("/", os.sep))


def find_declarations(directory):
    """找出工程里所有 `.gdextension`(包括放在 addons/ 里的)。"""
    found = []
    if not directory or not os.path.isdir(directory):
        return found
    for root, dirs, files in os.walk(directory):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in sorted(files):
            if name.endswith(".gdextension"):
                found.append(os.path.join(root, name))
    return found


def inspect(path, directory=None):
    """解析一份 `.gdextension`,返回结构化结果。"""
    directory = directory or os.path.dirname(path)
    info = {
        "path": path,
        "entry_symbol": "?",
        "compatibility_minimum": "?",
        "libraries": [],
        "error": "",
    }
    parser = configparser.ConfigParser()
    try:
        with open(path, encoding="utf-8") as handle:
            parser.read_file(handle)
    except Exception as exc:                              # noqa: BLE001
        info["error"] = f"{type(exc).__name__}: {exc}"
        return info

    if parser.has_section("configuration"):
        info["entry_symbol"] = parser.get(
            "configuration", "entry_symbol", fallback="?").strip().strip('"')
        info["compatibility_minimum"] = parser.get(
            "configuration", "compatibility_minimum", fallback="?").strip().strip('"')

    if parser.has_section("libraries"):
        # raw=True:路径里带 % 时不做插值,否则 configparser 会抛异常
        for feature, raw in parser.items("libraries", raw=True):
            declared = (raw or "").strip().strip('"')
            target = resolve_library_path(directory, raw)
            info["libraries"].append({
                "feature": feature,
                "declared": declared,
                "path": target,
                "exists": os.path.isfile(target),
            })
    return info


def check(directory):
    """给诊断报告用的人话版本,返回若干行文本。"""
    if not directory or not os.path.isdir(directory):
        return ["  还没有 Godot 工程(先建项目,或 .blend 没保存)"]

    declarations = find_declarations(directory)
    if not declarations:
        return ["  (没有 .gdextension —— 纯 GDScript 工程,正常)"]

    lines = []
    for path in declarations:
        info = inspect(path, directory)
        lines.append(f"  {os.path.relpath(path, directory)}")
        if info["error"]:
            lines.append(f"      ⚠ 读不出来: {info['error']}")
            continue
        lines.append(f"      entry_symbol={info['entry_symbol']}  "
                     f"compatibility_minimum={info['compatibility_minimum']}")
        if not info["libraries"]:
            lines.append("      ⚠ 没有 [libraries] 段,Godot 不会加载任何库")
            continue
        missing = 0
        for lib in info["libraries"]:
            mark = "有" if lib["exists"] else "缺"
            lines.append(f"      [{mark}] {lib['feature']} -> {lib['declared']}")
            if not lib["exists"]:
                missing += 1
        if missing:
            lines.append("      ⚠ 上面标『缺』的库不存在:Godot **不报错**,只是不加载;")
            lines.append("        症状是 GDScript 里找不到那个类。按这里写的文件名重新编译。")
    return lines
