"""主场景(project.godot 的 run/main_scene)读写与项目状态摘要。

从 project.py 拆出(GPT 审查)。
"""

import os
import re

from .. import paths
from .layout import is_project, missing_subdirs
from .metadata import _read_marker, _write_marker



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
    scene_res_path = paths.to_res_path(scene_res_path)

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
