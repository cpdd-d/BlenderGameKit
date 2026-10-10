"""网络联机(ENet)节点发射器 —— 第一百轮:房间 + 自定义消息。

生成物只有三块:
  1. 四个动作节点各一行:开房 / 加入 → `_gk_net_start(...)`,
     断开 → `_gk_net_stop()`,发消息 → `_gk_net_send(频道, 谁, 内容)`;
  2. 一组辅助函数(`_gk_net_start` / `_gk_net_stop` / `_gk_net_send` /
     两个信号分发函数):用 Godot 自带的 **ENetMultiplayerPeer**,
     不引第三方服务;
  3. 一个 `@rpc` 方法 `_gk_net_msg(频道, 发送者, 文本)` —— 收到消息后
     把值写进成员变量,再按频道分发给图里那几个『联机_收到消息』节点。

── 为什么是这个形状(真机标定过的,别改回去)──────────────────────
* `ENetMultiplayerPeer.new()` + `create_server(port, 最大人数)` /
  `create_client(ip, port)`,返回 Error,然后赋给
  `multiplayer.multiplayer_peer`。信号(peer_connected / peer_disconnected
  / connected_to_server / connection_failed / server_disconnected)挂在
  **`multiplayer` 这个 MultiplayerAPI 对象**上 —— 实测:先 connect、
  后赋 `multiplayer_peer`,信号照样收得到(换 peer 不换 API 对象)。
* `@rpc("any_peer", "call_remote", "reliable")`:调用方**不会**执行本地
  那份(call_remote)。实测:`rpc(...)` = 发给"除了自己以外的所有对端"
  (`rpc_id(1, ...)` 从客户端能到主机,主机 `rpc_id(id, ...)` 能到单个
  客户端)。所以"发的人自己也收不到"是**故意的**,和 Godot 语义一致。
* 发送者编号**当参数传**(不靠 `get_remote_sender_id()`):这样"谁发的"
  明确可控,也不会因为本地回显之类的情况拿到 0。
* ⚠ RPC 按**节点路径**找对端(实测:对端路径不同直接
  `Node not found ... Failed to get path from RPC`)。所以主机和客户端
  必须跑同一张场景 —— 节点上都写明了,README 也写了。
"""

from ..nodes.node_base import gd_string
from ..nodes.nodes import node_key

from .registry import stmt_node


# 有执行流的动作节点
NET_ACTION_IDS = ("GKNodeNetHost", "GKNodeNetJoin", "GKNodeNetDisconnect",
                  "GKNodeNetSend")

# 靠信号 / RPC 触发的事件节点(每个节点一条独立执行流,和触发器同一套路)
NET_EVENT_IDS = ("GKNodeNetPeerJoined", "GKNodeNetPeerLeft",
                 "GKNodeNetMessage")

# 事件节点 -> 生成函数名前缀
NET_EVENT_BASES = {
    "GKNodeNetPeerJoined": "_gk_net_evt_joined",
    "GKNodeNetPeerLeft": "_gk_net_evt_left",
    "GKNodeNetMessage": "_gk_net_evt_msg",
}

# 收消息的固定 RPC 通道名 —— 两端必须一致
NET_MSG_FUNC = "_gk_net_msg"


def _require(ctx):
    """登记"这次导出用到了联机",生成器据此带上那几个辅助函数。"""
    ctx.need_net = True


def _port_of(ctx, node):
    """端口:范围不合法时告警并退回默认值(别让 0 / 70000 这种静默失效)。"""
    try:
        port = int(getattr(node, "gk_port", 8910) or 8910)
    except (TypeError, ValueError):
        port = 8910
    if not (1 <= port <= 65535):
        ctx.warn(f"联机节点『{node.bl_label}』的『端口』是 {port},"
                 f"不在 1~65535 之间 —— 已改用默认端口 8910")
        return 8910
    return port


# ==========================================================================
# 四个动作节点
# ==========================================================================
@stmt_node("GKNodeNetHost")
def _stmt_net_host(ctx, node, indent):
    """开房:创建 ENet 服务端,自己是主机(peer id = 1)。"""
    _require(ctx)
    port = _port_of(ctx, node)
    try:
        max_clients = int(getattr(node, "gk_max", 8) or 8)
    except (TypeError, ValueError):
        max_clients = 8
    if max_clients < 1:
        max_clients = 1
    ctx.add_line(indent,
                 f"_gk_net_start(true, {gd_string('127.0.0.1')}, "
                 f"{port}, {max_clients})")
    ctx.note(f"联机:本机开房(端口 {port},最多 {max_clients} 个客户端)。"
             f"局域网里的人用你的 IP + 这个端口加入")


@stmt_node("GKNodeNetJoin")
def _stmt_net_join(ctx, node, indent):
    """加入:连到一个已经开好房的地址(客户端)。"""
    _require(ctx)
    port = _port_of(ctx, node)
    ip = str(getattr(node, "gk_ip", "") or "").strip() or "127.0.0.1"
    ctx.add_line(indent,
                 f"_gk_net_start(false, {gd_string(ip)}, {port}, 1)")
    ctx.note(f"联机:加入 {ip}:{port}(客户端)")


@stmt_node("GKNodeNetDisconnect")
def _stmt_net_disconnect(ctx, node, indent):
    """断开当前连接 / 关掉房间。"""
    _require(ctx)
    ctx.add_line(indent, "_gk_net_stop()")


@stmt_node("GKNodeNetSend")
def _stmt_net_send(ctx, node, indent):
    """发一条自定义消息:所有人(自己除外)或某一个玩家。"""
    _require(ctx)
    channel = str(getattr(node, "gk_channel", "默认") or "默认").strip() or "默认"
    text = ctx.input_string(node, "内容", "")
    mode = str(getattr(node, "gk_mode", "ALL") or "ALL")
    if mode == "ONE":
        # 『发给谁』可以接『有人进来』的『谁』(此时是个 int 表达式)
        target = ctx.input_number(
            node, "发给谁",
            str(int(getattr(node, "gk_peer", 0) or 0)))
        ctx.add_line(indent,
                     f"_gk_net_send({gd_string(channel)}, int({target}), {text})")
    else:
        ctx.add_line(indent, f"_gk_net_send({gd_string(channel)}, 0, {text})")


# ==========================================================================
# 整块的生成:事件节点 -> 独立函数 + 信号/RPC 分发 + 辅助函数
# ==========================================================================
def emit_network(gen, sections):
    """把联机的事件节点、分发函数和辅助函数拼进最终的 sections。

    在 `Generator.generate()` 里、**触发区那一段之后**调用 ——
    和触发器同一个套路:每个事件节点一条独立的执行流函数,
    信号 / RPC 连接统一补进 `_ready`(一次性的事)。
    """
    ctx = gen.ctx
    nodes = list(gen._nodes())
    used = [n for n in nodes if getattr(n, "bl_idname", "") in NET_ACTION_IDS]
    events = [n for n in nodes if getattr(n, "bl_idname", "") in NET_EVENT_IDS]
    if not used and not events:
        return
    ctx.need_net = True

    # ── 成员变量 ────────────────────────────────────────────────────
    # 事件节点的『谁』/『谁发的』/『内容』输出就是这几个变量(见 nodes/
    # _network.py 的 gk_event_output_vars):回调先写值,再跑用户那条链。
    ctx.declare_var("_gk_net", "ENetMultiplayerPeer", initial="null")
    ctx.declare_var("_gk_net_peer", "int", initial="0")
    ctx.declare_var("_gk_net_msg_sender", "int", initial="0")
    ctx.declare_var("_gk_net_msg_text", "String", initial='""')

    # ── 每个事件节点一条独立执行流 ──────────────────────────────────
    joined_funcs = []
    left_funcs = []
    msg_funcs = []          # [(节点, 函数名)]
    for node in events:
        base = NET_EVENT_BASES.get(node.bl_idname)
        if base is None:
            continue
        func_name = ctx.declare_func_name(base, key=node_key(node))
        ctx._active.clear()
        ctx._current_event = node.name
        body = ctx.new_section()
        gen._emit_event_body(node, 1)
        sections.append({
            "signature": f"func {func_name}() -> void:",
            "lines": list(body.lines),
            "empty_body": "    pass",
            "separator": True,
        })
        if node.bl_idname == "GKNodeNetPeerJoined":
            joined_funcs.append(func_name)
        elif node.bl_idname == "GKNodeNetPeerLeft":
            left_funcs.append(func_name)
        else:
            msg_funcs.append((node, func_name))

    # ── 信号分发(peer_connected / peer_disconnected)────────────────
    # 两个分发函数**总是**生成(即便图里没接对应的事件节点)——
    # 用户下一版加个『有人进来』时不必重新想"信号到底连没连"。
    connected_body = [f"    _gk_net_peer = id"] + [
        f"    {name}()" for name in joined_funcs]
    disconnected_body = [f"    _gk_net_peer = id"] + [
        f"    {name}()" for name in left_funcs]
    for signature, lines in (
            ("_gk_net_on_peer_connected", connected_body),
            ("_gk_net_on_peer_disconnected", disconnected_body)):
        ctx.extra_sections.append({
            "signature": f"func {signature}(id: int) -> void:",
            "lines": lines,
            "empty_body": "    pass",
            "separator": True,
        })

    # ── 收消息的 RPC 方法 ───────────────────────────────────────────
    # 值先写进成员变量,再按频道分发 —— 『频道』对不上的不触发。
    rpc_body = [
        "    _gk_net_msg_sender = sender",
        "    _gk_net_msg_text = text",
    ]
    for node, func_name in msg_funcs:
        if bool(getattr(node, "gk_any", False)):
            rpc_body.append(f"    {func_name}()")
        else:
            channel = str(getattr(node, "gk_channel", "默认") or "默认").strip() or "默认"
            rpc_body.append(f"    if channel == {gd_string(channel)}:")
            rpc_body.append(f"        {func_name}()")
    ctx.extra_sections.append({
        # ⚠ @rpc 注解必须**紧贴** func(中间不能插入空行),所以它和函数
        #   签名拼在同一个 section 的 signature 里(拼接时按 \n 分行)。
        "signature": (f'@rpc("any_peer", "call_remote", "reliable")\n'
                      f"func {NET_MSG_FUNC}(channel: String, sender: int, "
                      f"text: String) -> void:"),
        "lines": rpc_body,
        "empty_body": "    pass",
        "separator": True,
    })

    # ── 辅助函数 ────────────────────────────────────────────────────
    ctx.extra_sections.append({
        "signature": ("func _gk_net_start(server: bool, ip: String, "
                      "port: int, max_clients: int) -> void:"),
        "lines": [
            "    # 已经在联机就先收摊,避免「开了两次」留下旧连接",
            "    if _gk_net != null:",
            "        multiplayer.multiplayer_peer = null",
            "        _gk_net.close()",
            "        _gk_net = null",
            "    var peer := ENetMultiplayerPeer.new()",
            "    var err := OK",
            "    if server:",
            "        err = peer.create_server(port, max_clients)",
            "    else:",
            "        err = peer.create_client(ip, port)",
            "    if err != OK:",
            "        push_warning(\"[GK] 联机失败(%s 端口 %s 错误码 %s)\" %"
            " [\"开房\" if server else \"加入\", port, err])",
            "        return",
            "    _gk_net = peer",
            "    multiplayer.multiplayer_peer = peer",
        ],
        "empty_body": "    pass",
        "separator": True,
    })
    ctx.extra_sections.append({
        "signature": "func _gk_net_stop() -> void:",
        "lines": [
            "    var old := _gk_net",
            "    _gk_net = null",
            "    multiplayer.multiplayer_peer = null",
            "    if old != null:",
            "        old.close()",
        ],
        "empty_body": "    pass",
        "separator": True,
    })
    ctx.extra_sections.append({
        "signature": ("func _gk_net_send(channel: String, to_peer: int, "
                      "text: String) -> void:"),
        "lines": [
            "    if _gk_net == null or multiplayer.multiplayer_peer == null:",
            "        push_warning(\"[GK] 还没联机就发消息了,这条已忽略\")",
            "        return",
            "    var _me := multiplayer.get_unique_id()",
            "    if to_peer > 0:",
            f"        rpc_id(to_peer, {gd_string(NET_MSG_FUNC)}, channel,"
            f" _me, text)",
            "    else:",
            f"        rpc({gd_string(NET_MSG_FUNC)}, channel, _me, text)",
        ],
        "empty_body": "    pass",
        "separator": True,
    })

    # ── _ready 里把两个信号接上 ─────────────────────────────────────
    # 放在 _ready **最前面**(和触发器的信号连接同一个理由):用户的
    # 『每当_准备』链里可能有『等待_秒』,排在后面会晚好几秒才挂上。
    ready_lines = [
        "    # ---- 联机:接上多人信号(开房 / 加入之后收得到「有人进来」等)----",
        "    if not multiplayer.peer_connected.is_connected(_gk_net_on_peer_connected):",
        "        multiplayer.peer_connected.connect(_gk_net_on_peer_connected)",
        "    if not multiplayer.peer_disconnected.is_connected(_gk_net_on_peer_disconnected):",
        "        multiplayer.peer_disconnected.connect(_gk_net_on_peer_disconnected)",
    ]
    gen._append_block(sections, "_ready", "func _ready() -> void:",
                      ready_lines, at_front=True)
