"""Structural execution-flow diagnostics shared by export and project reports.

Data connections do not execute statements. A connected island of statements
can therefore look complete in Blender while generating no game behaviour.
Walk from event/function entries, including callback outputs and reroutes, to
report that island at its actual nodes. This is deliberately a wiring check,
not a claim that every branch condition will be true at runtime.
"""

from ..models import Diagnostic
from ..nodes.node_base import safe_function_name
from .graph import position_key


def audit_execution_flow(tree):
    """Return actionable warnings for unreachable statements in a logic tree.

    The graph is indexed once; disconnected cycles and long
    reroute chains cannot recurse forever. No Blender state is modified.
    UI/animation/AI design trees have their own implicit entry semantics and
    must not be interpreted as event-driven gameplay graphs.
    """
    if tree is None:
        return []
    if getattr(tree, "bl_idname", "GKGameLogicTree") != "GKGameLogicTree":
        return []
    if not getattr(tree, "gk_enabled", True):
        return []

    nodes = list(getattr(tree, "nodes", ()) or ())
    members = set(nodes)
    edges = {}
    has_flow_input = set()
    roots = set()
    # Match Generator._emit_functions: undeclared/duplicate definitions are
    # skipped, so they must not make an otherwise disconnected body reachable.
    declared = {str(getattr(item, "func_name", "") or "").strip()
                for item in getattr(tree, "gk_funcs", ()) or ()}
    functions = set()
    definitions = [node for node in nodes
                   if getattr(node, "bl_idname", "") == "GKNodeFunctionDefine"]
    for node in sorted(definitions, key=position_key):
        raw_name = str(getattr(node, "gk_func_name", "") or "").strip()
        if not raw_name or raw_name not in declared:
            continue
        name = safe_function_name(raw_name)
        if name not in functions:
            functions.add(name)
            roots.add(node)
    candidates = []
    for node in nodes:
        kind = getattr(node, "bl_idname", "")
        if getattr(node, "gk_is_comment", False):
            continue
        # Function definitions are compiled separately and can be called from
        # signals/other scripts, so an in-graph call is not required.
        if getattr(node, "gk_is_event", False):
            roots.add(node)
        elif kind == "GKNodeFunctionDefine":
            continue  # The compiler reports an invalid declaration directly.
        elif not getattr(node, "gk_data_only", False) and any(
            getattr(socket, "bl_idname", "") == "GKSocketFlow"
            for socket in getattr(node, "inputs", ()) or ()
        ):
            candidates.append(node)

    for link in getattr(tree, "links", ()) or ():
        # graph.downstream() consumes the same raw links. In particular, do not
        # silently add is_valid/is_muted filtering only to this diagnostic.
        source, target = link.from_node, link.to_node
        if source not in members or target not in members:
            continue
        from_flow = (getattr(link.from_socket, "bl_idname", "") == "GKSocketFlow"
                     or getattr(source, "bl_idname", "") == "NodeReroute")
        to_flow = (getattr(link.to_socket, "bl_idname", "") == "GKSocketFlow"
                   or getattr(target, "bl_idname", "") == "NodeReroute")
        if from_flow and to_flow:
            edges.setdefault(source, []).append(target)
            has_flow_input.add(target)

    reached = set(roots)
    pending = list(roots)
    while pending:
        for target in edges.get(pending.pop(), ()):
            if target not in reached:
                reached.add(target)
                pending.append(target)

    result = []
    graph_name = str(getattr(tree, "name", "") or "游戏逻辑")
    for node in candidates:
        if node in reached:
            continue
        name = str(getattr(node, "name", "") or getattr(node, "bl_label", "节点"))
        reason = ("所在的执行链没有连到事件或函数入口"
                  if node in has_flow_input else "没有连接金色执行口")
        result.append(Diagnostic(
            level="warning", code="UNREACHABLE_EXECUTION", node_name=name,
            tree_name=str(getattr(tree, "name", "") or ""),
            message=f"节点图『{graph_name}』中的『{name}』{reason}，其执行行为不会运行。",
            hint=("从『每当_准备』、『每当_物理帧』或有效函数入口接通金色执行线；"
                  "灰色数据线只传值，不会触发执行。若是备用逻辑，可移出当前导出图。"),
        ))
    return result
