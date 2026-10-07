"""音频资产(第四十三轮:『游戏资产』图的『资产_声音卡』)。

用户这一轮要的四类资产里,音频是**唯一没有管线**的:『音效_播放』
『音效_音乐』卡上只能手敲一个 `res://art/jump.wav`,文件还得自己拷进
Godot 项目 —— 敲错一个字母,运行时就是一句 "找不到音频文件"。

现在改成:『资产_声音卡』挑一个**本地文件**(wav / ogg / mp3),
导出时复制到项目的 `audio/` 目录,名字**两边算得一模一样**:

    sound_file_name(本地路径) -> "<压成 ASCII 的名字>_<8位摘要>.wav"
    sound_res_path(本地路径)  -> "res://audio/<上面那个>"

『音效_播放』卡上的『从资产库挑』把节点的 `gk_path` 直接写成后者,
不用手敲、也不会写错。

── 为什么摘要挂在**路径**上(和曲线 / 图片不一样)────────────────
曲线和图片的摘要算的是"名字";音频的身份是**那个文件** ——
两个文件夹里各有一份 jump.wav 是两次不同的导入,压 ASCII 后
名字会撞。所以摘要算完整路径,一个文件一个目标名。
"""

import os
import shutil

# 摘要 / ASCII 压缩和曲线、图片共用同一套(名字压完会撞,必须挂摘要)。
# 从 curve_assets 借而不是再抄一份 —— 抄一份迟早两处不一致。
from .curve_assets import _crc, _safe_base

# 目录名和 project.layout.AUDIO_DIR 一致。这里不 import project:
# 保持和 curve_assets / 图片那两处一样的做法(写死 + 注释),
# 免得绕成循环导入。
AUDIO_DIR = "audio"

# Godot 直接认的音频格式
AUDIO_EXTS = (".wav", ".ogg", ".mp3")


def is_sound_file(path):
    """是不是我们能带进 Godot 的音频文件。"""
    return os.path.splitext(str(path or ""))[1].lower() in AUDIO_EXTS


def sound_file_name(local_path):
    """目标文件名:ASCII 名字 + 路径摘要(见模块头)。"""
    base = os.path.basename(str(local_path or ""))
    stem, ext = os.path.splitext(base)
    digest = _crc(os.path.normcase(os.path.abspath(str(local_path or ""))))
    return f"{_safe_base(stem, 'sound')}_{digest}{ext.lower()}"


def sound_res_path(local_path):
    """脚本 / 节点里引用的 `res://audio/...`。"""
    return f"res://{AUDIO_DIR}/{sound_file_name(local_path)}"


def sound_files(directory, cards):
    """这次导出要带进项目的音频:**(显示名, 本地路径, 目标路径, res://)**。

    cards 是 [(名字, 本地文件路径)] —— 来自资产图的『资产_声音卡』。
    没有本地文件(只登记路径的卡)和不在格式表里的一律跳过:
    导出不该因为一张没挑文件的卡就报错。
    """
    out = []
    seen = set()
    for name, path in cards or ():
        path = str(path or "").strip()
        if not path or not is_sound_file(path):
            continue
        target = os.path.join(str(directory or ""), AUDIO_DIR,
                              sound_file_name(path))
        if target in seen:
            continue
        seen.add(target)
        out.append((str(name or os.path.basename(path)), path, target,
                    sound_res_path(path)))
    return out


def copy_sounds(directory, cards):
    """把音频复制进项目。返回 (写进去的相对路径, 找不到的 [(名字, 路径)])。

    源文件被挪走 / 删掉时**不抛异常**,只记进 missing —— 一个音频丢了
    不该让整次导出失败(和曲线"点太少就跳过"同一个立场)。
    复制不进 FileTransaction(那是文本事务):最坏情况是多留一个
    音频文件,下一次导出的清理会按清单把它收走。
    """
    written = []
    missing = []
    for name, path, target, _res in sound_files(directory, cards):
        if not os.path.isfile(path):
            missing.append((name, path))
            continue
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(path, target)
        except OSError:
            missing.append((name, path))
            continue
        written.append(f"{AUDIO_DIR}/{os.path.basename(target)}")
    return written, missing