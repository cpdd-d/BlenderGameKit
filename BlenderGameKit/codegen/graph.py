"""Traversal through editor-only reroutes and frame-aware ordering."""


def position_key(node):
    x, y = getattr(node, "location", (0.0, 0.0))[:2]
    parent = getattr(node, "parent", None)
    while parent is not None:
        px, py = getattr(parent, "location", (0.0, 0.0))[:2]
        x, y = x + px, y + py
        parent = getattr(parent, "parent", None)
    return y, x


def upstream(socket):
    seen = set()
    while socket is not None:
        links = getattr(socket, "links", ())
        link = next(iter(links), None)
        if link is None:
            return None
        source = link.from_node
        if getattr(source, "bl_idname", "") != "NodeReroute":
            return link
        if source in seen:
            raise ValueError("转接点形成循环，请断开循环连线")
        seen.add(source)
        socket = source.inputs[0]
    return None


def downstream(socket, seen=None):
    seen = set() if seen is None else seen
    result = []
    for link in getattr(socket, "links", ()):
        target = link.to_node
        if getattr(target, "bl_idname", "") == "NodeReroute":
            if target in seen:
                raise ValueError("转接点形成循环，请断开循环连线")
            result.extend(downstream(target.outputs[0], seen | {target}))
        else:
            result.append((target, getattr(link.to_socket, "name", "") or "执行"))
    return result
