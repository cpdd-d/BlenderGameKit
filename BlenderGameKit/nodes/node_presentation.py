"""Node-card presentation; never changes values, socket identities or links."""

from functools import wraps


_STYLING = set()
_LAYOUT_CONTAINERS = {"row", "column", "box", "split", "grid_flow", "column_flow"}
_PALETTES = {
    "EVENT": ((0.30, 0.13, 0.17), 220.0),
    "FLOW": ((0.29, 0.23, 0.12), 220.0),
    "ACTION": ((0.14, 0.23, 0.32), 240.0),
    "VALUE": ((0.15, 0.28, 0.23), 190.0),
}


def input_property_map(node):
    mapping = dict(getattr(node, "gk_input_props", {}))
    if getattr(node, "bl_idname", "") == "GKNodeSetVar":
        for socket in node.inputs:
            if socket.name.startswith("值 "):
                suffix = socket.name[2:]
                prop = f"gk_value_{suffix}"
                if suffix.isdigit() and hasattr(node, prop):
                    mapping[socket.name] = prop
    return mapping


def mapped_inputs(node):
    """Existing input sockets whose defaults are read from node properties."""
    mapping = input_property_map(node)
    return [(socket, mapping[socket.name])
            for socket in getattr(node, "inputs", ())
            if socket.name in mapping and hasattr(node, mapping[socket.name])]


def apply_socket_style(node):
    """Idempotent refresh after creation, links, property changes or loading."""
    if node in _STYLING:
        return
    _STYLING.add(node)
    try:
        mapping = input_property_map(node)
        active = getattr(node, "active_inputs", None)
        active_names = set(active()) if callable(active) else None
        for is_output, sockets in ((False, node.inputs), (True, node.outputs)):
            for socket in sockets:
                shape = "DIAMOND" if socket.bl_idname == "GKSocketFlow" else "CIRCLE"
                if getattr(socket, "display_shape", None) != shape:
                    socket.display_shape = shape
                if not is_output:
                    bound = socket.name in mapping and hasattr(node, mapping[socket.name])
                    hide_value = bound or socket.bl_idname == "GKSocketFlow"
                    if getattr(socket, "hide_value", None) != hide_value:
                        socket.hide_value = hide_value
                    if active_names is not None:
                        hidden = socket.name not in active_names and not socket.is_linked
                        if getattr(socket, "hide", None) != hidden:
                            socket.hide = hidden
    finally:
        _STYLING.discard(node)


def apply_node_style(node):
    """Defaults for newly created nodes only; refresh never resets user sizing."""
    apply_socket_style(node)
    tree = getattr(node, "id_data", None)
    if tree is not None and getattr(tree, "bl_idname", "") != "GKGameLogicTree":
        return
    if getattr(node, "gk_is_event", False):
        role = "EVENT"
    elif getattr(node, "gk_category", "") == "流程":
        role = "FLOW"
    elif getattr(node, "gk_data_only", False):
        role = "VALUE"
    else:
        role = "ACTION"
    color, width = _PALETTES[role]
    node.use_custom_color = True
    node.color = color
    node.width = width


class InputLayout:
    """Keep custom cards while making their input defaults truthful.

    Only property widgets on this node are intercepted. Other data, operator
    buttons and layout options keep the original Blender behavior.
    """

    def __init__(self, layout, node, seen=None):
        object.__setattr__(self, "_layout", layout)
        object.__setattr__(self, "_node", node)
        object.__setattr__(self, "_seen", set() if seen is None else seen)

    def __setattr__(self, name, value):
        setattr(self._layout, name, value)

    def __getattr__(self, name):
        member = getattr(self._layout, name)
        if name in _LAYOUT_CONTAINERS:
            def container(*args, **kwargs):
                return InputLayout(member(*args, **kwargs), self._node, self._seen)
            return container
        return member

    def _property(self, method, data, name, *args, **kwargs):
        if data != self._node:
            return getattr(self._layout, method)(data, name, *args, **kwargs)
        self._seen.add(name)
        sockets = [socket for socket, prop in mapped_inputs(data) if prop == name]
        active = getattr(data, "active_inputs", None)
        if sockets and callable(active) and not any(
                socket.name in active() for socket in sockets):
            return None
        if sockets and all(getattr(socket, "hide", False) for socket in sockets):
            return None
        if sockets and all(socket.is_linked for socket in sockets):
            row = self._layout.row(align=True)
            row.enabled = False
            label = kwargs.get("text")
            if label is None:
                entry = getattr(data, "gk_prop_map", {}).get(name, ())
                label = entry[3] if len(entry) > 3 else sockets[0].name
            kwargs["text"] = f"{label} · 连线" if label else "连线"
            return getattr(row, method)(data, name, *args, **kwargs)
        return getattr(self._layout, method)(data, name, *args, **kwargs)

    def prop(self, data, name, *args, **kwargs):
        return self._property("prop", data, name, *args, **kwargs)

    def prop_search(self, data, name, *args, **kwargs):
        return self._property("prop_search", data, name, *args, **kwargs)


def with_input_controls(draw):
    @wraps(draw)
    def draw_card(node, context, layout):
        proxy = InputLayout(layout, node)
        draw(node, context, proxy)
        # Some custom cards draw only an operation selector. Supply the actual
        # A/B/etc. properties that codegen uses, rather than socket.default_value.
        # A card can explicitly limit this fallback when some inputs are gated
        # by an option, or their unlinked value comes from a mode instead.
        allowed = getattr(node, "input_default_controls", None)
        allowed = set(allowed()) if callable(allowed) else None
        missing = [(socket, prop) for socket, prop in mapped_inputs(node)
                   if prop not in proxy._seen and not getattr(socket, "hide", False)
                   and (allowed is None or prop in allowed)]
        if missing:
            defaults = proxy.column(align=True)
            for socket, prop in missing:
                if prop not in proxy._seen:
                    defaults.prop(node, prop, text=socket.name)
    return draw_card


def with_socket_style(build):
    @wraps(build)
    def build_styled(node, *args, **kwargs):
        result = build(node, *args, **kwargs)
        apply_socket_style(node)
        return result
    return build_styled


def refresh_loaded_nodes(_unused=None):
    import bpy
    if not hasattr(bpy.data, "node_groups"):
        return
    for tree in bpy.data.node_groups:
        for node in getattr(tree, "nodes", ()):
            if node.bl_idname.startswith("GKNode"):
                apply_socket_style(node)


def on_register():
    import bpy
    bpy.app.handlers.persistent(refresh_loaded_nodes)
    if refresh_loaded_nodes not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(refresh_loaded_nodes)
    refresh_loaded_nodes()


def on_unregister():
    import bpy
    if refresh_loaded_nodes in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(refresh_loaded_nodes)
    _STYLING.clear()
