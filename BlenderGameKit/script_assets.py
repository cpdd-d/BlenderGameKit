"""用户自定义脚本资产(第一百五十一轮)。

用户在 Blender 的文本编辑器里写 GDScript,节点『脚本_加载 / 停止 / 调用』
挑一个文本块;导出时这里把**被节点引用到的**文本块写成工程里的
`scripts/custom/<安全名>.gd`,生成的脚本再按同一个路径 `load()`。

── 为什么用文本块 ──────────────────────────────────────────────
  * `.blend` 自包含:不用把外部 .gd 和工程一起搬来搬去;
  * 文件名由文本块名字**确定性**生成(带短哈希),同一个 .blend 每次导出
    路径一致,load() 不会今天找得到明天找不到;
  * 只导出被节点引用的文本块 —— 别人的笔记 / 导出报告不会混进工程。
"""

import hashlib
import os
import re

# 导出目录里的落地位置(res:// 路径也从这里推)
SCRIPT_DIR = "scripts/custom"
# 哪些节点带『脚本』属性(Text 数据块引用)
SCRIPT_NODES = ("GKNodeScriptLoad", "GKNodeScriptStop", "GKNodeScriptCall")


def _safe_base(name):
    """文本块名 -> 稳定、可读的 ASCII 文件名 / 运行时实例键。"""
    text = str(name or "").strip() or "script"
    base = re.sub(r"[^0-9A-Za-z_-]+", "_", text).strip("_")
    if not base:
        base = "script"
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
    return f"{base}_{digest}"


def file_name(name):
    """文本块名 -> 导出的 .gd 文件名。"""
    return _safe_base(name) + ".gd"


def res_path(name):
    """文本块名 -> Godot 的 res:// 路径(生成脚本里写死的那个)。"""
    return f"res://{SCRIPT_DIR}/{file_name(name)}"


def instance_key(name):
    """文本块名 -> 运行时实例键(节点名 / 字典键)。"""
    return _safe_base(name)


def node_script_text(node):
    """读取节点真正引用的文本块；不再按旧枚举序号或名称猜测。"""
    if getattr(node, "bl_idname", "") not in SCRIPT_NODES:
        return None
    return getattr(node, "gk_script_text", None)


def script_files(directory):
    """扫描所有节点图里引用到的文本块,返回 [(键, 落盘路径, 文本)]。

    离线(没有 bpy)返回空表 —— 导出流程只是"少复制一个文件",
    绝不会因此失败。
    """
    try:
        import bpy
    except Exception:                                     # noqa: BLE001
        return []
    blocks = {}
    for group in getattr(getattr(bpy, "data", None), "node_groups", ()) or ():
        for node in getattr(group, "nodes", ()) or ():
            block = node_script_text(node)
            if block is not None:
                blocks[instance_key(block.name)] = block
    out = []
    for key, block in sorted(blocks.items()):
        try:
            body = str(block.as_string())
        except Exception:                                 # noqa: BLE001
            continue
        path = os.path.join(directory, SCRIPT_DIR, file_name(block.name))
        out.append((key, path, body))
    return out
