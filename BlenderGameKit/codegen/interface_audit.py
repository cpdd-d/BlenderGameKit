"""Validate only interfaces whose generated blueprint implementation is known.

Godot native methods/signals, runtime paths and external scripts are open-ended.
Do not infer that an unknown member is missing. Known generated functions and
zero-argument blueprint signals do have an exact signature we can check.
"""

from ..models import Diagnostic
from ..nodes.node_base import TARGET_SELF, safe_function_name, snake_case
from .graph import position_key


def _scene_role_trees():
    """Read the same explicit object-to-graph bindings used by scene export."""
    try:
        import bpy
        scene = getattr(getattr(bpy, "context", None), "scene", None)
        if scene is None:
            return {}
        from ..exporter import object_logic_trees
        return {obj.name: tree for obj, tree, _reason in object_logic_trees(scene)
                if tree is not None}
    except (AttributeError, ReferenceError):
        return {}


def target_tree(tree, node, options=None, *, target_trees=None):
    """Resolve only self and explicit exported ROLE hosts; paths stay dynamic."""
    raw = str(getattr(node, "gk_target", TARGET_SELF) or TARGET_SELF)
    if raw == TARGET_SELF:
        return tree, "自身"
    if raw.startswith("@"):
        raw = ((options or {}).get("reference_bindings") or {}).get(raw[1:])
        if not raw:
            return None, ""  # target_expr owns the unbound-reference diagnostic.
    targets = _scene_role_trees() if target_trees is None else target_trees
    return targets.get(raw), raw


def emitted_function_name(tree, raw, fallback="my_function"):
    """Use the declaration's naming rule without renaming legacy/native calls.

    Modern definitions use safe_function_name, including its Unicode digest.
    Legacy function events and external methods still use snake_case. An
    explicitly supplied exported name must not receive a second digest.
    """
    from ..properties import tree_functions

    declared_name = str(raw or "").strip()
    if tree is not None and declared_name in tree_functions(tree):
        return safe_function_name(declared_name)
    return snake_case(raw, fallback)


def _interface(tree):
    from ..properties import tree_functions

    declarations = tree_functions(tree)
    methods = {}
    declared = {}
    signals = set()
    definitions = []
    for raw in declarations:
        declared[safe_function_name(raw)] = raw
    for node in getattr(tree, "nodes", ()):
        kind = getattr(node, "bl_idname", "")
        if kind == "GKNodeFunction" and not getattr(node, "gk_is_comment", False):
            raw = getattr(node, "gk_func_name", "my_function")
            methods[snake_case(raw, "my_function")] = (raw, 0)
        elif kind == "GKNodeFunctionDefine":
            definitions.append(node)
        elif kind == "GKNodeEmitSignal" and getattr(node, "gk_signal_declare", True):
            signals.add(snake_case(getattr(node, "gk_signal_name", "signal_name"), "signal_name"))
    # Match the generator's first-definition-wins rule, including frame positions.
    seen = set()
    for node in sorted(definitions, key=position_key):
        raw = str(getattr(node, "gk_func_name", "") or "").strip()
        if not raw or raw not in declarations:
            continue
        name = safe_function_name(raw)
        if name in seen:
            continue
        seen.add(name)
        methods[name] = (raw, len(declarations[raw].get("params") or ()))
    return methods, declared, signals


def function_suspends(tree, raw):
    """Known inline waits cannot supply a synchronous function result.

    Calls currently do not insert await: launching a void/background function
    does not suspend its caller. Nested typed calls receive their own check.
    External implementations remain the explicitly selected interface contract.
    """
    from .graph import downstream
    definitions = [n for n in getattr(tree, "nodes", ())
                   if getattr(n, "bl_idname", "") == "GKNodeFunctionDefine"
                   and str(getattr(n, "gk_func_name", "") or "").strip() == raw]
    if not definitions:
        return False
    pending = [sorted(definitions, key=position_key)[0]]
    seen = set()
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        kind = getattr(node, "bl_idname", "")
        if kind in {"GKNodeWait", "GKNodeRetriggerableDelay", "GKNodeScreenFade",
                    "GKNodeUiDialog", "GKNodeUiChoice", "GKNodeDialogHistory"}:
            return True
        if kind == "GKNodeQuitGame" and float(getattr(node, "gk_delay", 0) or 0) > 0:
            return True
        if kind in {"GKNodeUiTween", "GKNodeUiPop"} and getattr(node, "gk_wait", kind == "GKNodeUiPop"):
            return True
        for socket in getattr(node, "outputs", ()):
            if getattr(socket, "bl_idname", "") != "GKSocketFlow":
                continue
            # Callback outputs are emitted in separate methods, not this body.
            if kind in {"GKNodePlayAnim", "GKNodeTimeline", "GKNodeAnimStateMachineRef",
                        "GKNodeAnimOverlay", "GKNodeAnimEvent", "GKNodeUiButton",
                        "GKNodeUiInput", "GKNodeUiTextEdit", "GKNodeUiSlider",
                        "GKNodeUiDropdown", "GKNodeUiCheckbox", "GKNodeUiOnClick",
                        "GKNodeUiOnValueChanged", "GKNodeUiOnMouseClick", "GKNodeUiOnKeyPress"} \
                    and socket.name != "完成":
                continue
            try:
                pending.extend(n for n, _entry in downstream(socket))
            except ValueError:
                pass  # The generator separately reports invalid execution cycles.
    return False


def typed_call_diagnostics(tree, node, options=None):
    """Validate an explicit typed contract without guessing external interfaces."""
    from ..properties import function_call_tree, tree_functions
    source = function_call_tree(node, tree)
    raw = str(getattr(node, "gk_func_name", "") or "").strip()
    spec = tree_functions(source).get(raw)
    cross = (getattr(node, "gk_target", TARGET_SELF) or TARGET_SELF) != TARGET_SELF
    problems = []

    def report(code, message, hint):
        problems.append(Diagnostic(level="error", code=code, message=message, hint=hint,
                                   node_name=node.name, tree_name=getattr(tree, "name", "")))

    if cross and not getattr(node, "gk_function_tree", ""):
        report("INTERFACE_SOURCE_REQUIRED", "跨对象『函数_调用』没有选择接口图。", "选择提供此函数声明的角色逻辑图。")
    elif source is None:
        report("INTERFACE_SOURCE_MISSING", "『函数_调用』的接口图不存在。", "重新选择接口图；不要复用已删除图的名字。")
    elif spec is None and (cross or getattr(node, "gk_function_tree", "")):
        report("INTERFACE_FUNCTION_MISSING", f"接口图没有声明函数『{raw}』。", "从接口图的函数选择器重新选择声明。")
    elif spec is not None:
        owner, label = target_tree(tree, node, options)
        if (cross or source != tree) and owner is not None:
            actual = tree_functions(owner).get(raw)
            signature = lambda s: (tuple(t for _n, t in s.get("params", ())), s.get("return", ""))
            if actual is None or signature(actual) != signature(spec) or safe_function_name(raw) not in _interface(owner)[0]:
                report("INTERFACE_SIGNATURE_MISMATCH", f"目标『{label}』没有实现与接口图一致的函数『{raw}』。",
                       "核对目标角色图的函数定义、参数顺序/类型与返回类型。")
        if spec.get("return") and function_suspends(owner if owner is not None else source, raw):
            report("INTERFACE_ASYNC_RETURN", f"函数『{raw}』含等待，不能作为同步返回值调用。",
                   "用同步函数返回请求是否接受；异步动作完成后发送信号。")
    return problems


def audit_interfaces(tree, options=None, *, target_trees=None, emitted_nodes=None):
    """Return located diagnostics; never change generated code or graph state.

    ``target_trees`` can supply an explicit object-name-to-graph snapshot for
    offline tools. Otherwise use current scene export bindings. ``emitted_nodes``
    limits checks to statements actually compiled, avoiding dormant graph noise.
    """
    options = options or {}
    result = []
    interfaces = {}
    targets = target_trees

    def interface(source):
        if source not in interfaces:
            interfaces[source] = _interface(source)
        return interfaces[source]

    def target(node):
        nonlocal targets
        if targets is None:
            targets = _scene_role_trees()
        return target_tree(tree, node, options, target_trees=targets)

    def report(node, code, message, hint, level="error"):
        result.append(Diagnostic(level=level, code=code, message=message, hint=hint,
                                 node_name=str(getattr(node, "name", "") or ""),
                                 tree_name=str(getattr(tree, "name", "") or "")))

    def method(node, owner, raw, owner_label, callback=False):
        methods, declared, _signals = interface(owner)
        emitted_name = emitted_function_name(owner, raw, "on_signal" if callback else "my_function")
        if emitted_name in methods:
            return methods[emitted_name]
        display = declared.get(emitted_name)
        if display is None:
            return None  # May be a native/inherited/external method; do not guess.
        report(node, "INTERFACE_FUNCTION_NOT_DEFINED",
               f"『{owner_label}』的侧栏声明了函数『{display}』，但没有生成对应的函数定义。",
               "添加『函数_定义』节点并选择此声明；若调用的是继承方法，请移除无用的侧栏声明。",
               level="warning")
        return None

    for node in getattr(tree, "nodes", ()):
        if emitted_nodes is not None and node not in emitted_nodes:
            continue
        kind = getattr(node, "bl_idname", "")
        if kind == "GKNodeCallFunction":
            owner, label = target(node)
            if owner is None:
                continue
            raw = str(getattr(node, "gk_func_name", "my_function"))
            found = method(node, owner, raw, label)
            if found is not None and found[1] > 0:
                report(node, "INTERFACE_CALL_ARGUMENT_MISMATCH",
                       f"『{label}』的函数『{found[0]}』需要 {found[1]} 个参数，"
                       "『调用_函数』只会传入 0 个参数。",
                       "改用『函数_调用』并连接参数；跨对象时选择接口图与目标实例。")
        elif kind == "GKNodeConnect":
            raw = str(getattr(node, "gk_handler", "on_signal"))
            handler = method(node, tree, raw, "本图响应函数", callback=True)
            if handler is None:
                continue
            owner, label = target(node)
            if owner is None:
                continue
            signal = snake_case(getattr(node, "gk_signal_name", "pressed"), "pressed")
            if signal in interface(owner)[2] and handler[1] > 0:
                report(node, "INTERFACE_SIGNAL_ARGUMENT_MISMATCH",
                       f"『{label}』的自定义信号『{signal}』发送 0 个参数，"
                       f"响应函数『{handler[0]}』需要 {handler[1]} 个参数。",
                       "使用无参数响应函数；『信号_发出』自动声明的信号当前不携带参数。")
    return result
