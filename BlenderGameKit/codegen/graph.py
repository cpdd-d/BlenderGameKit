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


def downstream(socket, seen=None, *, links_by_socket=None):
    """Flatten reroutes in link order, without a recursion limit on long chains.

    Keep only the current path active: a shared endpoint reached by two paths
    must retain both executions. An optional link index avoids repeatedly
    scanning Blender's entire link collection via NodeSocket.links.
    """
    active = set(seen or ())

    def links_for(current):
        if links_by_socket is not None:
            return links_by_socket.get(current, ())
        return getattr(current, "links", ())

    result = []
    stack = [(iter(links_for(socket)), None)]
    while stack:
        links, owner = stack[-1]
        link = next(links, None)
        if link is None:
            stack.pop()
            if owner is not None:
                active.remove(owner)
            continue
        target = link.to_node
        if getattr(target, "bl_idname", "") == "NodeReroute":
            if target in active:
                raise ValueError("转接点形成循环，请断开循环连线")
            active.add(target)
            stack.append((iter(links_for(target.outputs[0])), target))
        else:
            result.append((target, getattr(link.to_socket, "name", "") or "执行"))
    return result
