"""导出前的静态校验。

从原 `codegen.py` 里抽出来的一块(GPT 审查:7200 行的单文件按领域拆分)。
这里全是"生成**之前/之后**扫一遍节点图、给出告警"的纯检查函数,
不参与代码生成本身。

  * `required_base_class` —— 生成**之前**定基类(否则 `extends X` 写错要重生成)
  * `_check_base_class_supports_graph` —— 基类能力不匹配的告警
  * `_check_animation_targets` —— 动画节点选错物体的告警

对外仍然通过 `codegen.required_base_class` 使用(包入口做了名字合并)。

── 告警用结构化诊断(GPT 审查 / 用户 2026-10-04)────────────────────
这里的两条告警是"最容易让用户找不到根因"的一类,所以先改成
`models.Diagnostic`:带稳定的 `code`(便于测试断言与以后按代码过滤)和
`hint`(直接告诉用户怎么改)。`Generator.warn()` 两种都收,出口由
`generate_result()` 统一摊平成字符串。
"""

import inspect

from ..models import Diagnostic

# 只有 CharacterBody 才有 move_and_slide / is_on_floor。
# 把「移动_按方向」用在这些基类上会生成 `self.move_and_slide()`,
# 而 Godot 加载脚本时报
#     Parse Error: Function "move_and_slide()" not found in base self.
# 注意:这个错误 `godot --check-only` **查不出来**(它不做成员级类型检查),
# 只有脚本真的挂到场景上、被加载时才报。所以必须在导出前就提醒。
_CHARACTER_BODIES = {"CharacterBody2D", "CharacterBody3D"}


def _check_base_class_supports_graph(tree, options, warnings):
    """基类和节点图是否匹配 —— 不匹配就给一条看得懂的告警。"""
    base = str(options.get("base_class", "") or "")
    if not base or base in _CHARACTER_BODIES:
        return
    needed = []
    for node in getattr(tree, "nodes", ()) or ():
        bl_idname = getattr(node, "bl_idname", "")
        if bl_idname == "GKNodeMoveDirection":
            needed.append("移动_按方向")
        elif bl_idname == "GKNodeMoveToward":
            needed.append("移动_朝向目标")
        elif bl_idname == "GKNodeMoveAndSlide":
            needed.append("移动_滑动")
        elif bl_idname == "GKNodeJump":
            needed.append("移动_跳跃")
        elif bl_idname == "GKNodeIsOnFloor":
            needed.append("取值_是否在地面")
    if not needed:
        return
    unique = sorted(set(needed))
    warnings.append(Diagnostic(
        level="warning",
        code="BASE_CLASS_MISMATCH",
        message=(f"脚本基类是 {base}，但节点图里用了 {'、'.join(unique)} —— "
                 f"这些节点生成的是 move_and_slide()，只有 CharacterBody2D / "
                 f"CharacterBody3D 才有这个方法。Godot 会在加载脚本时报 "
                 f"『Function \"move_and_slide()\" not found in base self』。"),
        hint=("改法(二选一)：1) 选中要做角色的那个物体 → 『游戏物理』面板 → "
              "类型改成『3D 角色 CharacterBody3D』(2D 则 CharacterBody2D)；"
              "2) 或者把侧栏『脚本基类』设成『自动(跟着物体走)』，让它跟着物体走。"),
    ))


def _check_animation_targets(tree, warnings):
    """检查动画节点选的动作和它要播的物体对不对得上。

    ── 回答用户的问题:"如果应用动画物体错误了会怎么样" ─────────────
    **以前是静默失败** —— Godot 里 AnimationPlayer 找不到那个动画,
    只在控制台打一条警告,画面看不出问题,用户不知道哪错了。

    现在**导出时**就报出来。判据见 nodes.check_action_target。
    """
    try:
        from ..nodes import nodes as _nodes
    except Exception:
        return
    checker = getattr(_nodes, "check_action_target", None)
    if checker is None:
        return
    try:
        import bpy
        objects = getattr(getattr(bpy, "data", None), "objects", None)
    except Exception:
        objects = None
    if objects is None:
        # 离线测试环境:没有真 bpy,跳过这项检查
        return

    for node in getattr(tree, "nodes", ()) or ():
        if node.bl_idname != "GKNodePlayAnim":
            continue
        # 节点上『动画播放器』填了就用它;留空 = 自动查找,
        # 那种情况没法在导出时确定目标,跳过(运行时才找得到)。
        target_name = str(getattr(node, "gk_player", "") or "")
        if not target_name:
            continue
        obj = objects.get(target_name)
        if obj is None:
            continue
        message = checker(node, obj)
        if message:
            warnings.append(Diagnostic(
                level="warning",
                code="ANIM_TARGET_MISMATCH",
                message=message,
                hint="检查节点上的『动画播放器』是否指向了带这个动画的物体",
                node_name=str(getattr(node, "name", "") or ""),
            ))


def _check_display_switch(tree, warnings):
    """『显示_效果开关』每张玩法图只允许一个。

    ── 为什么要拦(用户明确要求)────────────────────────────────────
    这个节点管的是**整关**的显示效果(环境氛围 / 画面风格 / 调试面板),
    不是某一个物体。图里放两个的话,"到底以哪个为准"没有合理解 ——
    生成出来还会两条都执行,顺序不同结果就不同,极难排查。

    用户原话:"同一类节点仅允许添加一个(不允许两个一样的)"。
    Blender 那边『添加节点』也会拦一次(见 operator_io),这里是导出侧的兜底
    —— 老 .blend 复制粘贴节点 / 脚本建图都可能绕过界面。
    """
    names = [str(getattr(node, "name", "") or "?")
             for node in getattr(tree, "nodes", ()) or ()
             if getattr(node, "bl_idname", "") == "GKNodeDisplaySwitch"]
    if len(names) <= 1:
        return
    warnings.append(Diagnostic(
        level="warning",
        code="DISPLAY_SWITCH_DUPLICATE",
        message=(f"这张图里有 {len(names)} 个『显示_效果开关』"
                 f"({'、'.join(names)})。它们都管**整关**的显示效果,"
                 f"同时存在时说不清以哪个为准。"),
        hint=("只留一个。想控制**某个物体**的显隐,用『显示_设置可见』"
              "(它才是按物体一个个来的)"),
        node_name=names[0],
    ))


_SUSPENDING_NODES = {"GKNodeWait", "GKNodeUiDialog", "GKNodeUiChoice"}


def _check_suspending_flow_fanout(tree, warnings):
    """An awaited branch makes sibling execution order difficult to reason about."""
    for node in getattr(tree, "nodes", ()) or ():
        for socket in getattr(node, "outputs", ()) or ():
            if getattr(socket, "bl_idname", "") != "GKSocketFlow":
                continue
            links = list(getattr(socket, "links", ()) or ())
            if len(links) < 2:
                continue
            pending = [getattr(link, "to_node", None) for link in links]
            seen = set()
            suspends = False
            while pending:
                current = pending.pop()
                if current is None:
                    continue
                key = (getattr(current, "as_pointer", lambda: id(current))())
                if key in seen:
                    continue
                seen.add(key)
                if getattr(current, "bl_idname", "") in _SUSPENDING_NODES:
                    suspends = True
                    break
                for output in getattr(current, "outputs", ()) or ():
                    if getattr(output, "bl_idname", "") == "GKSocketFlow":
                        pending.extend(getattr(link, "to_node", None)
                                       for link in getattr(output, "links", ()) or ())
            if suspends:
                name = str(getattr(node, "name", "") or "?")
                warnings.append(Diagnostic(
                    level="warning", code="SUSPENDING_FLOW_FANOUT",
                    message=f"『{name}』的『{socket.name}』连接了多条执行链，其中有等待节点；执行顺序依赖节点位置。",
                    hint="接一个『序列』节点，用编号出口明确初始化与等待链的顺序；把等待链放在最后。",
                    node_name=name,
                ))


def required_base_class(tree, options=None):
    """扫一遍节点图,返回 (需要的基类, 为什么) —— 不需要就 (None, "")。

    ── 为什么要在生成**之前**扫 ────────────────────────────────────
    基类会写进生成的 `extends X` 那一行。等生成完再发现"这个基类
    没有 is_on_floor()"就晚了 —— 得整个重生成。
    所以先静态扫一遍节点类型,提前把基类定下来。

    动画树的『动画判断_移动状态』用 `is_on_floor()` / `velocity`,
    AI 图的『AI_状态』(行为)用 `velocity` / `move_and_slide()` ——
    两者都只有 CharacterBody 有。以后有别的节点提需求,
    在 `_BASE_CLASS_NEEDS` 里加一条就行。
    """
    if tree is None:
        return None, ""
    dim = str((options or {}).get("dimension", "3"))
    # 动画树和 AI 图都不在游戏逻辑图里,得单独找出来一起扫
    trees = [tree]
    try:
        import bpy
        for group in getattr(getattr(bpy, "data", None), "node_groups", ()) or ():
            if getattr(group, "bl_idname", "") in ("GKAnimationTree",
                                                   "GKGameAiTree"):
                trees.append(group)
    except Exception:
        pass

    for candidate in trees:
        for node in getattr(candidate, "nodes", ()) or ():
            need = _BASE_CLASS_NEEDS.get(getattr(node, "bl_idname", ""))
            if need is None:
                continue
            if callable(need):
                # need 回调有两种签名:老的是 `need(dim)`,新的是 `need(dim, node)`
                # (要看节点属性才能判断的,比如 AI 启用的『作用在谁』)。
                # 按参数个数挑,别硬塞 —— 硬塞会 TypeError。
                try:
                    nparams = len(inspect.signature(need).parameters)
                except (TypeError, ValueError):
                    nparams = 1
                result = need(dim, node) if nparams >= 2 else need(dim)
            else:
                result = need
            if result:
                return result, (f"节点『{getattr(node, 'bl_label', '?')}』"
                                f"用到了它才有的成员")
    return None, ""


# 节点类型 -> 需要什么基类(或一个根据维度返回基类的函数)
_BASE_CLASS_NEEDS = {
    # is_on_floor() / velocity 只有 CharacterBody 有
    "GKNodeAnimLocomotion": lambda dim: ("CharacterBody2D" if dim == "2"
                                         else "CharacterBody3D"),
    # 移动/跳跃这些也是 CharacterBody 的活
    "GKNodeMoveAndSlide": lambda dim: ("CharacterBody2D" if dim == "2"
                                       else "CharacterBody3D"),
    "GKNodeMoveDirection": lambda dim: ("CharacterBody2D" if dim == "2"
                                        else "CharacterBody3D"),
    "GKNodeMoveToward": lambda dim: ("CharacterBody2D" if dim == "2"
                                     else "CharacterBody3D"),
    "GKNodeJump": lambda dim: ("CharacterBody2D" if dim == "2"
                               else "CharacterBody3D"),
    # ── AI 启用(第四十一轮)─────────────────────────────────────────
    # 第一百二十轮修正:AI 代码操作的是**目标物体**(velocity/move_and_slide
    # 作用在『作用在谁』上,它自己就是 CharacterBody)。只有『作用在谁』
    # 留空 / 是宿主自己时,宿主脚本才需要 CharacterBody 能力 ——
    # 否则(目标是独立怪物)宿主不该被抬成 CharacterBody,否则关卡图挂
    # 场景根(Node3D)会 extends 对不上节点类型、整个关卡逻辑加载失败。
    # ⚠ GKNodeAiState 在 AI 树里,不在这张表 —— AI 状态节点只描述行为,
    #   真正"谁来跑"由『AI_启用』的『作用在谁』决定,不能据此抬宿主基类。
    "GKNodeAiEnable": lambda dim, node: (
        ("CharacterBody2D" if dim == "2" else "CharacterBody3D")
        if str(getattr(node, "gk_target", "") or "") in ("", "__self__")
        else None),
}
