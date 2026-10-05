"""生成标记文件(.gk_generated)读写。

从 project.py 拆出(GPT 审查)。
"""

import json
import os



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
