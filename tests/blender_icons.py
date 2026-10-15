"""面板 / 节点里写的每一个图标名,都必须是 Blender 真的认识的那个。

跑法:
    blender -b --factory-startup --python-exit-code 1 --python tests/blender_icons.py

── 为什么要有这一关(第 123 轮,用户实测截图)────────────────────────
用户状态栏里出现一句:
    TypeError: UILayout.operator(): error with keyword argument "icon" - enum ...
截图里那一块界面**整个不见了**,他只看到"按钮有问题"。

原因:面板里写了 `icon="LOOP_FORWARD"`,而 Blender 的枚举叫 `LOOP_FORWARDS`
(带 S);另一处写了 `icon="IMAGE_BG"`(正确名是 `IMAGE_BACKGROUND`)。

这类错的症状**极隐蔽**:
  * 只在 draw() 跑到那一行时抛,只往控制台/状态栏打一行;
  * 现有的 `tests/blender_panels.py` 抓不到 —— 因为那两块面板在它的用例表外;
  * 测试和文档都不会因为一个字符串写错而变红。

所以这里做**静态全扫**:把插件源码里所有 `icon="..."` / `bl_icon = "..."`
取出来,逐个对照 RNA 给出的合法清单。998 处一次扫完,一秒钟。
"""
import os
import re
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "BlenderGameKit")

# 合法清单:UILayout 的 icon 参数枚举 = Blender 的完整图标表。
# (`Node.bl_rna.properties["bl_icon"]` 在 4.5 上拿不到 enum_items,
#  所以统一用 UILayout 这一份 —— 它是超集,对 bl_icon 同样适用。)
VALID = {item.identifier for item in
         bpy.types.UILayout.bl_rna.functions["prop"]
         .parameters["icon"].enum_items}
if not VALID:                                              # pragma: no cover
    print("ICONS=SKIP(拿不到图标枚举)")
    sys.exit(0)

PATTERNS = (
    re.compile(r'\bicon\s*=\s*"([^"]+)"'),
    re.compile(r"\bicon\s*=\s*'([^']+)'"),
    re.compile(r'\bbl_icon\s*=\s*"([^"]+)"'),
    re.compile(r"\bbl_icon\s*=\s*'([^']+)'"),
)

uses = 0
bad = []
for dirpath, _dirnames, filenames in os.walk(PACKAGE):
    if "__pycache__" in dirpath:
        continue
    for filename in filenames:
        if not filename.endswith(".py"):
            continue
        path = os.path.join(dirpath, filename)
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                for pattern in PATTERNS:
                    for name in pattern.findall(line):
                        uses += 1
                        if name not in VALID:
                            bad.append((os.path.relpath(path, ROOT),
                                        number, name))

print(f"图标用法 {uses} 处,合法清单 {len(VALID)} 个")
for rel, number, name in bad:
    print(f"  [FAIL] {rel}:{number}  icon=\"{name}\" 不在 Blender 图标表里")
print(f"ICON_USES={uses}")
print(f"ICON_BAD={len(bad)}")
if bad:
    print("ICONS=FAILED")
    raise SystemExit(1)
print("ICONS=OK")
