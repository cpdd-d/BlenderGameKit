"""网络联机示例(第一百轮:房间 + 自定义消息)。

用法:
    blender -b --python scripts\\make_net_demo.py
产出:
    examples/demo_net/联机示例.blend
    examples/demo_net/联机示例_godot/     (可用 Godot 直接打开)
自动检查(不用 Blender,要 Godot):`python scripts\\check_net_demo.py`

── 这一关在看什么 ────────────────────────────────────────────────
用 Godot 自带的 **ENetMultiplayerPeer**(局域网 / 直连:一个 IP + 一个端口)
做一个最小的"房间 + 自定义消息",不引任何第三方服务。两个 Blender 场景
各导出成一张 .tscn:

    联机房间.tscn     —— 主机:『联机_开房』,有人进来就发欢迎语,收到
                        消息就回一条,有人走了就记一行日志
    联机客户端.tscn   —— 客户端:『联机_加入』,收到欢迎语后回一条,
                        3 秒后『联机_断开』

两台机器(或同一台机器开两个进程)跑**同一张场景**,一边开房一边加入,
就能互相收发消息。

── ⚠ 一条必须记住的硬规矩:RPC 靠"节点路径"找对端 ────────────────
Godot 的 `@rpc` 是按**节点在场景树里的路径**把调用送到对端的。所以主机和
客户端必须跑**同一张场景**(同一个 .tscn、同样的节点结构)—— 换的只是
节点图里的『联机_开房』/『联机_加入』。两张场景结构不一样的话,连得上
但消息收不到(Godot 会报 `Node not found: "xxx" ... Failed to get path
from RPC`,真机实测过)。

── 生成的代码长什么样 ────────────────────────────────────────────
跟别的节点一样,整棵图导出成一个 `game_logic_<场景>.gd`,里面多出:

    var _gk_net: ENetMultiplayerPeer = null
    func _gk_net_start(server, ip, port, max_clients)   # 开房 / 加入
    func _gk_net_stop()                                  # 断开
    func _gk_net_send(channel, to_peer, text)            # 发消息
    @rpc("any_peer", "call_remote", "reliable")
    func _gk_net_msg(channel, sender, text)              # 收消息(固定通道)

**收消息的通道名固定是 `_gk_net_msg`,参数是 `(频道, 发送者编号, 文本)`** ——
想用别的脚本(或者别的语言写的服务端)跟它通信时,照着这个签名实现即可。

── 想自己玩 ──────────────────────────────────────────────────────
1. 同一台电脑:先跑『联机房间』(开房),再跑『联机客户端』(加入
   127.0.0.1),控制台能互相看到日志;
2. 两台电脑:客户端那张图里把『主机地址』改成主机所在的 IP;
3. 想让**同一张场景**既能开房又能加入 —— 复制一份场景,把『联机_开房』
   换成『联机_加入』就行(别改成两张结构不同的场景,见上面那条规矩)。
"""

import math
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "examples", "demo_net")
BLEND_PATH = os.path.join(OUT_DIR, "联机示例.blend")

# 两个场景的名字 = 导出后 .tscn 的根节点名(= RPC 路径的第一段)
HOST_SCENE = "联机房间"
JOIN_SCENE = "联机客户端"
HOST_TREE = "房间逻辑"
JOIN_TREE = "客户端逻辑"
# 挂脚本的那个物体(它是 Colliders 下的一个 StaticBody3D,
# 节点路径 = /root/<场景名>/Colliders/网络节点)
SCRIPT_OBJECT = "网络节点"
NET_PORT = 47777          # 用一个不常见的端口,免得和别的程序撞
NET_MAX = 8
CHANNEL = "聊天"           # 自定义消息的频道名(收发两边要一样)
CLIENT_LEAVE_AFTER = 3.0   # 客户端连上多久后自己断开(演示『联机_断开』)

HOST_HELLO = "HELLO_FROM_HOST"
HOST_PONG = "PONG_FROM_HOST"
CLIENT_PING = "PING_FROM_CLIENT"


def log(message):
    print(f"[联机] {message}")


def _apply_settings(scene, pairs):
    settings = getattr(scene, "gk_settings", None)
    if settings is None:
        return
    for attr, value in pairs:
        if hasattr(settings, attr):
            try:
                setattr(settings, attr, value)
            except Exception:                             # noqa: BLE001
                pass


def _add(tree, kind, name, x, y, **props):
    node = tree.nodes.new(kind)
    node.name = name
    node.location = (x, y)
    for key, value in props.items():
        setattr(node, key, value)
    return node


def _link(tree, source, source_pin, target, target_pin):
    tree.links.new(source.outputs[source_pin], target.inputs[target_pin])


def make_box(name, size, location, color, scene):
    bpy.ops.object.select_all(action="DESELECT")
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (size[0], size[1], size[2])
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    material = bpy.data.materials.new(f"{name}_材质")
    material.use_nodes = True
    principled = material.node_tree.nodes.get("Principled BSDF")
    if principled is not None:
        principled.inputs["Base Color"].default_value = color
    obj.data.materials.append(material)
    # primitive_cube_add 会把它加进**当前场景**,多场景时要把归属改过来
    if obj.name not in scene.objects:
        for collection in list(obj.users_collection):
            collection.objects.unlink(obj)
        scene.collection.objects.link(obj)
    return obj


def build_scene(scene_name, node_color):
    """一个最小的房间:地面 + 一个当"服务器机柜"的方块 + 相机 + 太阳光。"""
    scene = bpy.data.scenes.new(scene_name)
    scene.gk_settings.dimension = "3"
    ground = make_box("地面", (30.0, 30.0, 0.4), (0.0, 0.0, -0.2),
                      (0.28, 0.32, 0.38, 1.0), scene)
    node_obj = make_box(SCRIPT_OBJECT, (1.6, 1.2, 2.0), (0.0, 0.0, 1.0),
                        node_color, scene)
    physics = node_obj.gk_physics
    physics.body_type = "StaticBody3D"
    physics.script_target = True
    try:
        physics.shape_size = (0.0, 0.0, 0.0)
    except Exception:                                     # noqa: BLE001
        pass
    # 别的物体不挂脚本(否则同一条逻辑会被跑好几遍)
    ground.gk_physics.body_type = "StaticBody3D"

    camera_data = bpy.data.cameras.new("机位数据")
    camera = bpy.data.objects.new("机位", camera_data)
    scene.collection.objects.link(camera)
    camera.location = (0.0, -12.0, 7.0)
    camera.rotation_euler = (math.radians(66.0), 0.0, 0.0)
    scene.camera = camera

    light_data = bpy.data.lights.new("太阳光数据", type="SUN")
    light_data.energy = 3.0
    light = bpy.data.objects.new("太阳光", light_data)
    scene.collection.objects.link(light)
    light.rotation_euler = (math.radians(50.0), 0.0, math.radians(30.0))
    return scene, node_obj


def build_host_graph():
    """主机图:开房 -> 有人进来就发欢迎语;收到消息就回一条;有人走记日志。"""
    tree = bpy.data.node_groups.new(HOST_TREE, "GKGameLogicTree")
    tree.use_fake_user = True

    ready = _add(tree, "GKNodeOnReady", "准备", -640.0, 0.0)
    host = _add(tree, "GKNodeNetHost", "开房", -320.0, 0.0,
                gk_port=NET_PORT, gk_max=NET_MAX)
    host_log = _add(tree, "GKNodePrint", "主机就绪", 0.0, 0.0,
                    gk_text="NET_HOST_READY")
    _link(tree, ready, "执行", host, "执行")
    _link(tree, host, "完成", host_log, "执行")

    joined = _add(tree, "GKNodeNetPeerJoined", "有人进来", -640.0, -280.0)
    joined_log = _add(tree, "GKNodePrint", "记进来", -320.0, -280.0,
                      gk_text="NET_PEER_JOINED")
    hello = _add(tree, "GKNodeNetSend", "发欢迎语", 0.0, -280.0,
                 gk_channel=CHANNEL, gk_mode="ONE", gk_text=HOST_HELLO)
    _link(tree, joined, "执行", joined_log, "执行")
    _link(tree, joined_log, "完成", hello, "执行")
    _link(tree, joined, "谁", hello, "发给谁")

    recv = _add(tree, "GKNodeNetMessage", "收消息", -640.0, -560.0,
                gk_channel=CHANNEL)
    recv_log = _add(tree, "GKNodePrint", "记收到", -320.0, -560.0,
                    gk_text="NET_HOST_GOT")
    recv_text = _add(tree, "GKNodePrint", "打印内容", 0.0, -560.0)
    pong = _add(tree, "GKNodeNetSend", "回一条", 320.0, -560.0,
                gk_channel=CHANNEL, gk_mode="ONE", gk_text=HOST_PONG)
    _link(tree, recv, "执行", recv_log, "执行")
    _link(tree, recv_log, "完成", recv_text, "执行")
    _link(tree, recv, "内容", recv_text, "内容")
    _link(tree, recv_text, "完成", pong, "执行")
    _link(tree, recv, "谁发的", pong, "发给谁")

    left = _add(tree, "GKNodeNetPeerLeft", "有人走了", -640.0, -840.0)
    left_log = _add(tree, "GKNodePrint", "记离开", -320.0, -840.0,
                    gk_text="NET_PEER_LEFT")
    _link(tree, left, "执行", left_log, "执行")

    log(f"图『{HOST_TREE}』:开房(端口 {NET_PORT}) / 有人进来->发 {HOST_HELLO} / "
        f"收到消息->打印+回 {HOST_PONG} / 有人走了->记日志")
    return tree


def build_join_graph():
    """客户端图:加入 -> 收到欢迎语就回一条(只回一次) -> 3 秒后断开。"""
    tree = bpy.data.node_groups.new(JOIN_TREE, "GKGameLogicTree")
    tree.use_fake_user = True

    ready = _add(tree, "GKNodeOnReady", "准备", -640.0, 0.0)
    join = _add(tree, "GKNodeNetJoin", "加入", -320.0, 0.0,
                gk_ip="127.0.0.1", gk_port=NET_PORT)
    join_log = _add(tree, "GKNodePrint", "已发出加入", 0.0, 0.0,
                    gk_text="NET_JOIN_SENT")
    _link(tree, ready, "执行", join, "执行")
    _link(tree, join, "完成", join_log, "执行")

    recv = _add(tree, "GKNodeNetMessage", "收消息", -640.0, -280.0,
                gk_channel=CHANNEL)
    recv_log = _add(tree, "GKNodePrint", "记收到", -320.0, -280.0,
                    gk_text="NET_CLIENT_GOT")
    recv_text = _add(tree, "GKNodePrint", "打印内容", 0.0, -280.0)
    # ⚠ 只回一次:不挡的话"收到就回"会和主机的"收到就回"来回刷屏
    once = _add(tree, "GKNodeDoOnce", "只回一次", 320.0, -280.0)
    ping = _add(tree, "GKNodeNetSend", "回一条", 640.0, -280.0,
                gk_channel=CHANNEL, gk_mode="ALL", gk_text=CLIENT_PING)
    _link(tree, recv, "执行", recv_log, "执行")
    _link(tree, recv_log, "完成", recv_text, "执行")
    _link(tree, recv, "内容", recv_text, "内容")
    _link(tree, recv_text, "完成", once, "执行")
    _link(tree, once, "首次", ping, "执行")

    timer = _add(tree, "GKNodeOnTimer", "过一会儿断开", -640.0, -560.0,
                 gk_seconds=CLIENT_LEAVE_AFTER, gk_repeat=False)
    off = _add(tree, "GKNodeNetDisconnect", "断开", -320.0, -560.0)
    off_log = _add(tree, "GKNodePrint", "已断开", 0.0, -560.0,
                   gk_text="NET_CLIENT_LEFT")
    _link(tree, timer, "执行", off, "执行")
    _link(tree, off, "完成", off_log, "执行")

    log(f"图『{JOIN_TREE}』:加入 127.0.0.1:{NET_PORT} / 收到消息->打印+回 "
        f"{CLIENT_PING}(只回一次) / {CLIENT_LEAVE_AFTER:g} 秒后断开")
    return tree


def save_blend(path):
    if not bpy.app.background:
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return
    blocks = set(bpy.data.objects) | set(bpy.data.meshes) | \
        set(bpy.data.materials) | set(bpy.data.curves) | \
        set(bpy.data.node_groups) | set(bpy.data.cameras) | set(bpy.data.lights)
    scenes = list(bpy.data.scenes)
    bpy.data.libraries.write(path, blocks | set(scenes), fake_user=True)


def main():
    from BlenderGameKit import asset_export, project

    os.makedirs(OUT_DIR, exist_ok=True)
    host_scene, _host_obj = build_scene(HOST_SCENE, (0.35, 0.65, 0.95, 1.0))
    join_scene, _join_obj = build_scene(JOIN_SCENE, (0.95, 0.6, 0.3, 1.0))
    # 清掉默认的空场景(不然会多导一张空的)。⚠ 必须等两个真场景建好再删 ——
    # Blender 不允许删到"最后一个本地场景"为止。
    for scene in list(bpy.data.scenes):
        if scene in (host_scene, join_scene):
            continue
        if not scene.objects:
            bpy.data.scenes.remove(scene)
    host_tree = build_host_graph()
    join_tree = build_join_graph()
    host_scene.gk_settings.logic_tree = host_tree.name
    join_scene.gk_settings.logic_tree = join_tree.name

    _apply_settings(host_scene, (("dimension", "3"),))
    _apply_settings(join_scene, (("dimension", "3"),))

    directory = os.path.join(OUT_DIR, "联机示例_godot")
    project.create_project(directory, title="联机示例",
                           blend_file_name=os.path.basename(BLEND_PATH))
    result = asset_export.export_all_scenes(directory, dimension="3")
    if result.get("error"):
        log(f"导出失败:{result['error']}")
        return 1
    for entry in result.get("scenes", []):
        log(f"场景 {entry['scene']} -> {entry['scene_name']}.tscn"
            f"(脚本 {entry['script_name']})")
    for warning in result.get("warnings", []):
        log(f"告警: {warning}")
    log(f"工程目录: {directory}")

    save_blend(BLEND_PATH)
    log(f"已保存 {BLEND_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
