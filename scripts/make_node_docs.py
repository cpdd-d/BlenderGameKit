"""从**节点类**生成 `docs/节点手册.md`(离线,不需要 Blender)。

用法:
    python scripts\\make_node_docs.py          # 写出手册
    python scripts\\make_node_docs.py --check  # 只校验"手册是不是最新的"

── 为什么要生成,而不是手写 ──────────────────────────────────────
README 里原来那张节点表是**手写**的,于是它一直是过期的:表头写"182 个"
而实际已经有 188 个,新加的节点(『声音_发出噪音』『AI_听见』『屏幕_暗角』
『对话_选择』…)一个都没进去。手写的清单**注定会漂**。

每个节点类本来就带 `gk_doc`(在 Blender 里按节点上的问号就看得到那段话),
引脚和参数也都是声明式的 —— 所以这份手册完全可以**从代码长出来**:
改了节点,重跑一次就是最新的;忘了重跑,`--check` 会在门禁里**报红**。

── 为什么能离线跑 ────────────────────────────────────────────────
`tests/gk_stub.py` 把 bpy 打了桩,`codegen` / `nodes` 这类模块不需要真
Blender 就能 import(离线单测一直这么干)。所以这个脚本能进**门禁第一层**,
而不是要等装完 Blender。
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# gk_stub 住在 tests/ 里(离线单测那套打桩)—— 借过来用,不复制一份
sys.path.insert(0, os.path.join(ROOT, "tests"))

import gk_stub  # noqa: E402

gk_stub.install()
ADDON = gk_stub.load_addon(os.path.join(ROOT, "BlenderGameKit"))

from BlenderGameKit import properties as props  # noqa: E402
from BlenderGameKit.nodes import nodes as node_lib  # noqa: E402

OUT = os.path.join(ROOT, "docs", "节点手册.md")

# 树类型 -> 人话(和 node_tree_error 里那份标签保持一致)
TREE_LABELS = {
    "GKGameLogicTree": "游戏逻辑",
    "GKAnimationTree": "游戏动画",
    "GKUiDesignTree": "游戏界面",
    "GKGameAiTree": "游戏AI",
    "GKDisplayTree": "游戏显示",
    "GKAssetTree": "游戏资产",
}

# 参数类型 -> 人话(生成器里 kind 是英文标识)
PROP_KINDS = {
    "string": "文本", "float": "数值", "int": "整数", "bool": "开关",
    "enum": "下拉", "node": "物体引用", "color": "颜色", "vector": "向量",
}


def _cell(text):
    """表格单元格:竖线会破坏 Markdown 表格,换成全角。"""
    return str(text or "").replace("|", "｜").replace("\n", " ").strip()


def _trees_of(cls):
    kinds = props.node_tree_kinds(cls)
    labels = [TREE_LABELS.get(kind, kind) for kind in kinds]
    if labels == ["游戏逻辑"]:
        return ""                       # 默认就是玩法图,不用特别标注
    return " / ".join(f"『{label}』" for label in labels)


def _pins_table(specs):
    if not specs:
        return ""
    lines = ["| 引脚 | 类型 | 说明 |", "|---|---|---|"]
    for spec in specs:
        kind = spec.get("kind") or ("", "")
        label = kind[1] if isinstance(kind, (tuple, list)) and len(kind) > 1 else ""
        default = spec.get("default")
        note = ""
        if default is not None and default != "":
            note = f"默认 `{default}`"
        lines.append(f"| {_cell(spec.get('name'))} | {_cell(label)} | {note} |")
    return "\n".join(lines)


def _props_table(cls):
    defs = getattr(cls, "gk_prop_defs", ()) or ()
    if not defs:
        return ""
    sock_map = {}
    for name, prop in (getattr(cls, "gk_input_props", None) or {}).items():
        sock_map[prop] = name
    lines = ["| 参数 | 类型 | 默认 | 说明 |", "|---|---|---|---|"]
    for item in defs:
        name = item[0]
        kind = PROP_KINDS.get(item[1], item[1]) if len(item) > 1 else ""
        default = item[2] if len(item) > 2 else ""
        label = item[3] if len(item) > 3 else name
        hint = item[4] if len(item) > 4 else ""
        shown = label or name
        if name.startswith("gk_"):
            shown = label or name[3:]
        linked = sock_map.get(name)
        if linked:
            shown += f"(可接『{linked}』引脚)"
        if default not in ("", None):
            shown_default = f"`{default}`"
        else:
            shown_default = ""
        lines.append(f"| {_cell(shown)} | {_cell(kind)} | "
                     f"{shown_default} | {_cell(hint)} |")
    return "\n".join(lines)


def _doc_text(cls):
    raw = getattr(cls, "gk_doc", "") or ""
    return str(raw).strip()


def build():
    """把手册拼成一段 Markdown。返回 (文本, 统计信息)。"""
    classes = list(node_lib.ALL_CLASSES)
    hidden = [cls for cls in classes if getattr(cls, "gk_hidden", False)]
    shown = [cls for cls in classes if not getattr(cls, "gk_hidden", False)]
    other = {cls.bl_idname for cls in hidden}
    # node_menu_entries 会按 CATEGORY_ORDER 排好、并把隐藏节点剔掉
    grouped = props.node_menu_entries()
    entries = [(cat, items) for cat, items in grouped]
    # 隐藏节点单独一段(它们不进菜单,但老图还能加载 —— 得留档)
    if other:
        entries.append(("(隐藏的老节点)", [c for c in classes
                                          if c.bl_idname in other]))

    out = []
    out.append("# 节点手册")
    out.append("")
    out.append(f"**这个文件是自动生成的** —— 共 {len(shown)} 个可用节点"
               f"(+{len(hidden)} 个隐藏的老节点)。")
    out.append("")
    out.append("```")
    out.append("python scripts\\make_node_docs.py        # 改完节点重跑一次")
    out.append("")
    out.append("# 或者交给门禁把关(手册和代码对不上就报红):")
    out.append("python scripts\\make_node_docs.py --check")
    out.append("```")
    out.append("")
    out.append("> 每个节点带的话在 Blender 里也看得到:选中节点按 **N** 打开侧栏,"
               "或把鼠标停在节点上。")
    out.append("")
    out.append("**怎么用这张表**:先在下面按分类找到你要的节点,"
               "再看它的『引脚』(金色『执行流』线决定先后顺序,其余是传值的)"
               "和『参数』。入门路线见 [入门路线.md](入门路线.md)。")
    out.append("")
    out.append("## 分类速查")
    out.append("")
    out.append("| 分类 | 节点数 | 常用节点 |")
    out.append("|---|---|---|")
    for category, items in entries:
        names = "、".join(cls.bl_label for cls in items[:6])
        if len(items) > 6:
            names += " …"
        out.append(f"| **{_cell(category)}** | {len(items)} | {_cell(names)} |")
    out.append("")
    out.append("> 菜单是**两级**的:『运算』下面是 常量/数学/向量/逻辑/转换,"
               "『输入』下面是 键盘/鼠标/手柄/触屏。")
    out.append("")
    out.append("---")
    out.append("")

    for category, items in entries:
        out.append(f"## {category}({len(items)} 个)")
        out.append("")
        for cls in items:
            label = getattr(cls, "bl_label", cls.__name__)
            out.append(f"### {label}")
            out.append("")
            meta = [f"`{cls.bl_idname}`"]
            trees = _trees_of(cls)
            if trees:
                meta.append(f"**只能放进 {trees}**")
            if getattr(cls, "gk_hidden", False):
                meta.append("⚠ **已隐藏**(不进菜单,但老图还能加载/导出)")
            out.append(" · ".join(meta))
            out.append("")
            doc = _doc_text(cls)
            if doc:
                out.append(doc)
                out.append("")
            table = _pins_table(getattr(cls, "gk_inputs", ()) or ())
            if table:
                out.append("**输入**")
                out.append("")
                out.append(table)
                out.append("")
            outputs = _pins_table(getattr(cls, "gk_outputs", ()) or ())
            if outputs:
                out.append("**输出**")
                out.append("")
                out.append(outputs)
                out.append("")
            props_table = _props_table(cls)
            if props_table:
                out.append("**参数**")
                out.append("")
                out.append(props_table)
                out.append("")
        out.append("---")
        out.append("")

    text = "\n".join(out).rstrip() + "\n"
    return text, {"shown": len(shown), "hidden": len(hidden),
                  "categories": len(entries)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="只校验手册是不是最新的(门禁用)")
    args = parser.parse_args()

    text, stats = build()
    print(f"节点 {stats['shown']} 个(+{stats['hidden']} 隐藏)"
          f"/ 分类 {stats['categories']} 个")

    if args.check:
        if not os.path.isfile(OUT):
            print(f"✗ 还没有 {OUT} —— 跑一次 `python scripts\\make_node_docs.py`")
            print("CHECK_NODE_DOCS=FAIL")
            return 1
        with open(OUT, "r", encoding="utf-8") as handle:
            current = handle.read()
        if current != text:
            print("✗ 手册和代码对不上了(加了/改了节点但没重新生成)")
            print(f"  跑一次:python scripts\\make_node_docs.py")
            print("CHECK_NODE_DOCS=FAIL")
            return 1
        print("CHECK_NODE_DOCS=OK")
        return 0

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print(f"已写 {OUT}({len(text)} 字)")
    return 0


if __name__ == "__main__":
    sys.exit(main())