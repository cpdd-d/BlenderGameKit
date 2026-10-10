"""网络联机节点(第一百轮:房间 + 自定义消息) —— 从 nodes/nodes.py 按领域拆出的一块。

本模块里的节点类通过 `@N` 登记进 `_common.CLASSES`。门面 `nodes/nodes.py`
按拆分前的**原始顺序**导入各领域模块,保证 `ALL_CLASSES` 的顺序不变。

── 这一节做什么 / 不做什么 ─────────────────────────────────────────
做:开房(主机)/ 加入(客户端)/ 断开 + 三个"别人做了什么"的事件口
    (有人进来 / 有人走了 / 收到消息)+ 一个自定义消息发送口。
不做:位置同步 / 状态同步 / 房间列表 / 匹配 / 鉴权。
    "谁在哪儿"这类同步要靠『联机_发消息』自己按帧发,本版本不内置。

── 底层是什么 ──────────────────────────────────────────────────────
Godot 自带的 **ENetMultiplayerPeer**(局域网 / 直连:一个 IP + 一个端口),
不引任何第三方服务。发出的消息走 Godot 的 `@rpc` 通道,通道名固定为
`_gk_net_msg`,参数是 `(频道, 发送者编号, 文本)`。

⚠ RPC 靠**节点路径**找对端:同一个 .tscn / 同样的节点结构才行。
所以主机和客户端要跑**同一张场景**(换的只是节点图里的『开房』/『加入』),
两边场景结构不一样的话消息收不到(Godot 会报 Node not found)。
"""

from ._common import *  # noqa: F401,F403


# ==========================================================================
# 联机(网络)—— 第一百轮
# ==========================================================================
@N
class GKNodeNetHost(B.GKStatementNode):
    bl_idname = "GKNodeNetHost"
    bl_label = "联机_开房"
    bl_icon = "WORLD"
    gk_category = "网络"
    gk_doc = ("开一个房间等别人连进来(你是主机)。"
              "── 怎么用 ──"
              "放在『每当_准备』后面;同一局域网里的人用『联机_加入』"
              "填你的 IP + 同一个端口就能进来。"
              "── 端口 ──"
              "两台机器要填一样的;同一台电脑开两个进程测试用默认的就行。"
              "── 上限 ──"
              "最多允许几个客户端连进来(不含主机自己)。")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_port", "int", 8910, "端口", "两边要填一样的端口(1024~65535)"),
        ("gk_max", "int", 8, "最多几个人", "最多允许几个客户端连进来"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_port", text="端口")
        row.prop(self, "gk_max", text="上限")


@N
class GKNodeNetJoin(B.GKStatementNode):
    bl_idname = "GKNodeNetJoin"
    bl_label = "联机_加入"
    bl_icon = "URL"
    gk_category = "网络"
    gk_doc = ("连到别人的房间(客户端)。"
              "── 地址 ──"
              "『主机地址』填开房那台机器的 IP;同一台电脑开两个进程测试"
              "就填 127.0.0.1。端口要和主机填的一样。"
              "── 一定要记住 ──"
              "RPC 按**节点路径**找对端 —— 主机和客户端要跑**同一张场景**"
              "(同一个 .tscn、同样的节点结构)。两边场景结构不一样时,"
              "连得上但消息收不到。")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_ip", "string", "127.0.0.1", "主机地址", "主机的 IP;本机测试填 127.0.0.1"),
        ("gk_port", "int", 8910, "端口", "要和主机填的一样"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_ip", text="")
        row.prop(self, "gk_port", text="端口")


@N
class GKNodeNetDisconnect(B.GKStatementNode):
    bl_idname = "GKNodeNetDisconnect"
    bl_label = "联机_断开"
    bl_icon = "X"
    gk_category = "网络"
    gk_doc = ("断开当前连接 / 关掉房间。之后可以再开房或再加入。"
              "主机断开 = 整个房间关掉,里面的人会被踢回单机。")
    gk_inputs = (S.pin(FLOW, "执行"),)


@N
class GKNodeNetSend(B.GKStatementNode):
    bl_idname = "GKNodeNetSend"
    bl_label = "联机_发消息"
    bl_icon = "FORWARD"
    gk_category = "网络"
    gk_doc = ("给别的玩家发一段自定义消息(收到的人触发『联机_收到消息』)。"
              "── 频道 ──"
              "两边要填一样的名字才能对上(比如 聊天 / 出招 / 血量)。"
              "── 发给谁 ──"
              "『所有人』= 房间里其他每个人(发的人自己不会收到);"
              "『某个玩家』= 只发给他,把『有人进来』的『谁』接过来即可。"
              "── 怎么收 ──"
              "收到的人那边的『联机_收到消息』节点会执行,里面能拿到"
              "『谁发的』和『内容』。")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(TEXT, "内容", ""),
        S.pin(INT, "发给谁", 0),
    )
    gk_input_props = {"内容": "gk_text", "发给谁": "gk_peer"}
    gk_prop_defs = (
        ("gk_channel", "string", "默认", "频道", "收消息那边要填一样的名字"),
        ("gk_mode", "enum", "ALL", "发给", "发给房间里的谁", (
            ("ALL", "所有人", "房间里的其他每个人(自己不会收到)"),
            ("ONE", "某个玩家", "只发给『发给谁』那一个玩家"),
        )),
        ("gk_text", "string", "", "内容", "没连线时发这段文字"),
        ("gk_peer", "int", 0, "发给谁", "玩家编号;接『有人进来』的『谁』更省事"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_channel", text="频道")
        row.prop(self, "gk_mode", text="")
        if str(getattr(self, "gk_mode", "ALL")) == "ONE":
            row.prop(self, "gk_peer", text="谁")


@N
class GKNodeNetPeerJoined(B.GKEventNode):
    bl_idname = "GKNodeNetPeerJoined"
    bl_label = "有人进来"
    bl_icon = "IMPORT"
    gk_category = "网络"
    gk_doc = ("有玩家连进来时执行一次(主机侧最有用:发欢迎语、告诉他规则)。"
              "『谁』= 那个玩家的编号 —— 直接接『联机_发消息』的『发给谁』"
              "就只发给他一个人。")
    gk_event_output_vars = {"谁": "_gk_net_peer"}
    gk_outputs = (S.pin(FLOW, "执行"), S.pin(INT, "谁", 0))


@N
class GKNodeNetPeerLeft(B.GKEventNode):
    bl_idname = "GKNodeNetPeerLeft"
    bl_label = "有人走了"
    bl_icon = "REMOVE"
    gk_category = "网络"
    gk_doc = ("有玩家断开 / 掉线时执行一次。『谁』= 走掉那个玩家的编号"
              "(可以用来把他在场上的角色清掉)。")
    gk_event_output_vars = {"谁": "_gk_net_peer"}
    gk_outputs = (S.pin(FLOW, "执行"), S.pin(INT, "谁", 0))


@N
class GKNodeNetMessage(B.GKEventNode):
    bl_idname = "GKNodeNetMessage"
    bl_label = "联机_收到消息"
    bl_icon = "FILE_TICK"
    gk_category = "网络"
    gk_doc = ("收到别的玩家用『联机_发消息』发来的消息时执行一次。"
              "『频道』要和发的时候填的一样;勾了『任何频道都接』就不筛频道。"
              "『谁发的』= 发送者编号(可以直接转发回去),『内容』= 那段文字。")
    gk_event_output_vars = {"谁发的": "_gk_net_msg_sender",
                            "内容": "_gk_net_msg_text"}
    gk_prop_defs = (
        ("gk_any", "bool", False, "任何频道都接", "勾上 = 不管什么频道都触发"),
        ("gk_channel", "string", "默认", "频道", "只响应这个频道的消息"),
    )
    gk_outputs = (
        S.pin(FLOW, "执行"),
        S.pin(INT, "谁发的", 0),
        S.pin(TEXT, "内容", ""),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_any", text="任何频道", toggle=True)
        if not getattr(self, "gk_any", False):
            row.prop(self, "gk_channel", text="")
