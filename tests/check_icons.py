"""校验所有节点图标名是否在 Blender 的合法图标白名单里。

Blender 的 bl_icon 只接受内置枚举值，写错会导致**整类注册失败**(而且是静默的)。
这个脚本在 Blender 里跑，输出非法图标与建议替换。

用法: blender -b --factory-startup --python tests/check_icons.py
"""

import json
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 从 RNA 里取合法图标清单
valid = set()
for type_name in ("Node", "ShaderNode", "CompositorNode", "GeometryNode"):
    node_type = getattr(bpy.types, type_name, None)
    if node_type is None:
        continue
    try:
        for item in node_type.bl_rna.properties["bl_icon"].enum_items:
            valid.add(item.identifier)
    except Exception:
        pass

# 手工兜底一份常用清单(万一 RNA 取不到)
if not valid:
    valid = {"NONE", "INFO", "ADD", "REMOVE", "TRASH", "PLAY", "TIME", "DOT", "NODE"}

from BlenderGameKit.nodes import nodes as node_lib  # noqa: E402

bad = []
for cls in node_lib.ALL_CLASSES:
    icon = getattr(cls, "bl_icon", "NONE")
    if icon not in valid:
        bad.append((cls.bl_idname, cls.bl_label, icon))

print("ICON_CHECK_BEGIN")
print(json.dumps({
    "total": len(node_lib.ALL_CLASSES),
    "valid_count": len(valid),
    "invalid": bad,
}, ensure_ascii=False, indent=1))
print("ICON_CHECK_END")

if bad:
    print(f"\n有 {len(bad)} 个非法图标名:")
    for bl_idname, label, icon in bad:
        print(f"  {bl_idname:26s} {label:14s} -> {icon}")
