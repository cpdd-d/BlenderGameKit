"""『游戏逻辑信息』(第八十六轮:E 组 13)—— 侧栏信息块的**数据层**。

用户从六个"编辑器"里点的最后一个信息类条目(前面 5 个:界面设计 /
AI 逻辑 / 显示设置 / 资产库 都做成了自己的节点树;这一个的形态当初定的是
"侧栏标签页 —— 看信息 / 清单",所以落成侧栏一块『逻辑信息』)。

它回答三个问题:

  1. **规模**:整个 .blend 里几张图、多少节点、谁挂了脚本;
  2. **归属**:本场景绑的是哪棵逻辑图(用的就是导出那套解析 `resolve_scene_tree`,
     所以"这里说没问题、导出却报错"这种事不会发生);
  3. **体检**:还没接线 / 没绑定 / 会互相覆盖这类"导出来才发现"的问题,
     在侧栏一眼看到。

── 为什么全部是纯函数 ──────────────────────────────────────────────
和 `node_groups.py` / `export_report.py` 同一个立场:只 getattr 读值、
不 import bpy,离线单测直接喂假对象。侧栏那边只负责把 bpy 里的事实
收集成 `facts` 字典,判据全在这里 —— 判据只有一处真相。
"""

# 图类型 -> 中文短名(『逻辑信息』里那一行统计用)。
TREE_KIND_LABELS = {
    "GKGameLogicTree": "逻辑",
    "GKAnimationTree": "动画",
    "GKUiDesignTree": "界面",
    "GKGameAiTree": "AI",
    "GKDisplayTree": "显示",
    "GKAssetTree": "资产",
    "GKMusicTree": "音乐",
}

# 所有 GK 节点树类型(按显示顺序)。
TREE_KINDS = tuple(TREE_KIND_LABELS)

# 体检行的级别 -> Blender 图标名(侧栏画的时候直接用)。
LEVEL_ICONS = {
    "error": "ERROR",
    "warn": "ERROR",
    "info": "INFO",
    "ok": "CHECKMARK",
}


def inventory(groups):
    """数一数这批节点图:`{"total": 张数, "nodes": 节点总数, "kinds": {短名: 张数}}`。

    认不出的类型不计进 `kinds`(但计进 total / nodes)—— 侧栏宁可少说一句,
    也不能因为一张没见过的图抛异常(冒烟里喂过假树)。
    """
    counts = {}
    total = 0
    nodes = 0
    for group in groups or ():
        total += 1
        nodes += len(getattr(group, "nodes", ()) or ())
        kind = str(getattr(group, "bl_idname", "") or "")
        label = TREE_KIND_LABELS.get(kind)
        if label:
            counts[label] = counts.get(label, 0) + 1
    return {"total": total, "nodes": nodes, "kinds": counts}


def inventory_text(groups):
    """一行话:"7 张图(逻辑 2 · 动画 1 …)/ 152 节点";一张都没有就直说。"""
    data = inventory(groups)
    if not data["total"]:
        return "还没有节点图"
    parts = [f"{label} {count}" for label, count in data["kinds"].items()]
    detail = "(" + " · ".join(parts) + ")" if parts else ""
    return f"{data['total']} 张图{detail} / {data['nodes']} 节点"


def checkup(facts):
    """体检:返回 [(级别, 一句话)] 列表;级别是 error / warn / info。

    `facts` 的键(全部可选,缺了按"拿不到"兜底):

        tree_name        本场景解析出来的逻辑图名("" = 没解析到)
        tree_reason      解析说明(resolve_scene_tree 的第二返回值)
        event_count      那棵树里的事件节点数
        carrier_count    挂了脚本的物体数
        carrier_names    它们名字(多于一个时报出来)
        uses_pathfinding 图里用了『移动_寻路』/ AI 行为寻路
        nav_enabled      场景的『寻路』开着没有
        declared_ports   树里声明的对外端口名
        written_ports    图里『逻辑_输出结果』真正写了的输出名
        disabled_trees   整个 .blend 里停用了的图数(gk_enabled = False)

    ── 为什么"没有事件"算错误 ────────────────────────────────────────
    导出的脚本确实会生成,但没有任何入口 —— 用户按 F5 看到"什么都没发生",
    而导出报告是全绿的。这种"静默失败"必须提前在侧栏说出来。
    """
    rows = []
    tree_name = str(facts.get("tree_name") or "")

    if not tree_name:
        reason = str(facts.get("tree_reason") or "").strip()
        rows.append(("error", reason or "本场景没有绑定逻辑图"))
    else:
        if int(facts.get("event_count") or 0) == 0:
            rows.append(("error", f"『{tree_name}』里没有『每当_…』这类事件节点 —— "
                                  f"脚本跑起来什么都不会做"))
        carrier_count = int(facts.get("carrier_count") or 0)
        if carrier_count == 0:
            rows.append(("warn", "还没有物体挂脚本 —— 导出的场景里不会有脚本。"
                                 "选中角色 → 『游戏物理』→ 勾『脚本挂载目标』"))
        elif carrier_count > 1:
            names = "、".join(str(n) for n in (facts.get("carrier_names") or ()))
            rows.append(("error", f"{carrier_count} 个物体都挂了脚本,会互相覆盖"
                                  f"({names}) —— 只留一个"))
        if facts.get("uses_pathfinding") and not facts.get("nav_enabled"):
            rows.append(("info", "图里用了寻路,但场景的『寻路』关着 —— "
                                 "会退化成直线走(在『导出为 Godot 脚本』里打开)"))
        written = {str(n) for n in (facts.get("written_ports") or ())}
        for name in (facts.get("declared_ports") or ()):
            name = str(name)
            if name and name not in written:
                rows.append(("info", f"端口『{name}』声明了,但图里没有"
                                     f"『逻辑_输出结果』写它(别的图读到的是空值)"))

    disabled = int(facts.get("disabled_trees") or 0)
    if disabled:
        rows.append(("info", f"有 {disabled} 张图停用了(不参与导出)—— "
                             f"在『节点图』盒子里能开回来"))
    return rows


def checkup_summary(rows):
    """体检列表的一行小结(侧栏标题行用):"2 个错误 / 1 条提醒" / "一切正常"。"""
    errors = sum(1 for level, _text in rows or () if level == "error")
    warns = sum(1 for level, _text in rows or () if level == "warn")
    infos = sum(1 for level, _text in rows or () if level == "info")
    if not (errors or warns or infos):
        return "一切正常"
    parts = []
    if errors:
        parts.append(f"{errors} 个错误")
    if warns:
        parts.append(f"{warns} 条警告")
    if infos:
        parts.append(f"{infos} 条提示")
    return " / ".join(parts)