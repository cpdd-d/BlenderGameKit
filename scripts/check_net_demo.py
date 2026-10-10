"""网络联机示例的真机探针(第一百轮)。

用法:
    python scripts\\check_net_demo.py
    python scripts\\check_net_demo.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

它会:
  1. 把 `examples/demo_net/联机示例_godot` 拷到 `_dist/net_probe`
     (**不碰示例本身**);
  2. 造一个"对面那个人"的脚本 `peer.gd` + 两张**根节点名和示例一模一样**的
     对端场景 —— 复制示例场景、只把里面的游戏逻辑脚本换成 `peer.gd`
     (节点结构逐字节相同,RPC 的"节点路径"才对得上);
  3. **同机起两个 Godot 进程真连一次**,分别验主机侧和客户端侧。

── 为什么要两个进程 ──────────────────────────────────────────────
Godot 一个 SceneTree 里只有一个 MultiplayerAPI / 一个 multiplayer_peer ——
"开房"和"加入"必须在两个进程(两台机器)里,同机测试就是开两个进程。

── 为什么对端要用"复制场景 + 换脚本" ─────────────────────────────
Godot 的 `@rpc` 是按**节点在场景树里的路径**把调用送到对端的(真机实测:
对端路径不一样直接 `Node not found: "CLI" ... Failed to get path from
RPC`)。所以对端必须和示例跑**同一张场景** —— 复制一份、只换脚本,
节点路径就天然一致了。

── 两段实验 ──────────────────────────────────────────────────────
  实验一:示例『联机房间』(真·主机) + 对端(客户端)
          -> 验『联机_开房』/『有人进来』/『有人走了』/『联机_发消息』/
            『联机_收到消息』
  实验二:对端(服务端) + 示例『联机客户端』(真·客户端)
          -> 验『联机_加入』/『联机_断开』

断言的判据全是**行为现象**(控制台里那几行日志),不是"代码长什么样"。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "examples", "demo_net", "联机示例_godot")
OUT = os.path.join(ROOT, "_dist", "net_probe")
TMP = os.path.join(ROOT, "_dist")

HOST_SCENE = "联机房间"
JOIN_SCENE = "联机客户端"
PORT = 47777
CHANNEL = "聊天"

PEER_GD = '''extends {extends_class}

# 探针的"对面那个人"——不是插件生成的脚本,是一份**手写的**最小实现。
# 它只做一件事:证明插件生成的那一半能真的连上 / 收发 / 断开。
#
# ⚠ 收消息的方法名 / 参数必须和插件生成的一致(`_gk_net_msg(频道, 发送者,
#   文本)`)—— 这正是"自定义消息"的对外约定,README 里也写了。

var mode := "client"
var port := {port}
var done := false
var t := 0.0

func _ready() -> void:
	for a in OS.get_cmdline_user_args():
		if a.begins_with("--mode="):
			mode = a.substr(7)
		elif a.begins_with("--port="):
			port = int(a.substr(7))
	multiplayer.peer_connected.connect(_on_peer_connected)
	multiplayer.peer_disconnected.connect(_on_peer_disconnected)
	multiplayer.connected_to_server.connect(_on_connected)
	multiplayer.connection_failed.connect(_on_failed)
	multiplayer.server_disconnected.connect(_on_server_gone)
	var peer := ENetMultiplayerPeer.new()
	var err := OK
	if mode == "server":
		err = peer.create_server(port, 8)
		print("PEER_ROLE server err=", err)
	else:
		err = peer.create_client("127.0.0.1", port)
		print("PEER_ROLE client err=", err)
	multiplayer.multiplayer_peer = peer

@rpc("any_peer", "call_remote", "reliable")
func _gk_net_msg(channel: String, sender: int, text: String) -> void:
	print("PEER_GOT channel=", channel, " sender=", sender, " text=", text)
	if mode == "client":
		if text == "HELLO_FROM_HOST":
			# 收到主机的欢迎语 -> 回一条(证明客户端也能"发消息")
			rpc("_gk_net_msg", "{channel}", multiplayer.get_unique_id(),
				"PING_FROM_CLIENT")
		elif text == "PONG_FROM_HOST":
			print("PEER_DONE")
			_finish()
	else:
		if text == "PING_FROM_CLIENT":
			rpc_id(sender, "_gk_net_msg", "{channel}", 1, "PONG_FROM_PEER")

func _on_peer_connected(id: int) -> void:
	print("PEER_JOINED id=", id)
	if mode == "server":
		rpc_id(id, "_gk_net_msg", "{channel}", 1, "HELLO_FROM_PEER")

func _on_peer_disconnected(id: int) -> void:
	print("PEER_LEFT id=", id)
	_finish()

func _on_connected() -> void:
	print("PEER_CONNECTED my_id=", multiplayer.get_unique_id())

func _on_failed() -> void:
	print("PEER_CONNECT_FAILED")
	_finish()

func _on_server_gone() -> void:
	print("PEER_SERVER_GONE")
	_finish()

func _finish() -> void:
	if done:
		return
	done = true
	await get_tree().create_timer(0.5).timeout
	get_tree().quit()

func _process(delta: float) -> void:
	t += delta
	if not done and t > 14.0:
		print("PEER_TIMEOUT")
		get_tree().quit()
'''


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _extends_of(script_path):
    for line in _read(script_path).splitlines():
        if line.startswith("extends "):
            return line[len("extends "):].strip()
    return "Node"


def _swap_script(scene_text, new_script_path):
    """把场景里 id="gk_script" 那个 ext_resource 的路径换成对端脚本。"""
    pattern = re.compile(
        r'(\[ext_resource type="Script" path=")[^"]*("\s+id="gk_script"\])')
    swapped, count = pattern.subn(
        lambda m: m.group(1) + new_script_path + m.group(2), scene_text)
    return swapped, count


def prepare():
    if not os.path.isdir(SRC):
        print(f"找不到示例工程 {SRC} —— 先跑 make_net_demo.py")
        return None
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(TMP, exist_ok=True)
    shutil.copytree(SRC, OUT, ignore=shutil.ignore_patterns(".godot", "*.import"))

    host_scene = os.path.join(OUT, "scenes", f"{HOST_SCENE}.tscn")
    join_scene = os.path.join(OUT, "scenes", f"{JOIN_SCENE}.tscn")
    host_script = os.path.join(OUT, "scripts", f"game_logic_{HOST_SCENE}.gd")
    join_script = os.path.join(OUT, "scripts", f"game_logic_{JOIN_SCENE}.gd")
    for path in (host_scene, join_scene, host_script, join_script):
        if not os.path.isfile(path):
            print(f"示例里缺少 {path} —— 先跑 make_net_demo.py")
            return None

    # 对端脚本的 extends 必须和示例脚本一致(否则挂到同一个节点上会被拒)
    extends_class = _extends_of(host_script)
    if _extends_of(join_script) != extends_class:
        print("两个示例脚本的 extends 不一样,对端脚本没法定:")
        print(f"  {HOST_SCENE}: {extends_class} / "
              f"{JOIN_SCENE}: {_extends_of(join_script)}")
        return None
    _write(os.path.join(OUT, "peer.gd"),
           PEER_GD.format(extends_class=extends_class, port=PORT,
                          channel=CHANNEL))

    # 两张对端场景 = 示例场景的副本,只换脚本(节点路径必须逐字节一致)
    for src_scene, dst_name in ((host_scene, "peer_host.tscn"),
                                (join_scene, "peer_join.tscn")):
        text, count = _swap_script(_read(src_scene), "res://peer.gd")
        if count != 1:
            print(f"没能把 {os.path.basename(src_scene)} 里的游戏脚本换成对端脚本"
                  f"(匹配到 {count} 处)")
            return None
        _write(os.path.join(OUT, dst_name), text)

    return {
        "extends": extends_class,
        "host_scene": f"res://scenes/{HOST_SCENE}.tscn",
        "join_scene": f"res://scenes/{JOIN_SCENE}.tscn",
    }


def _launch(godot, scene, args, log_name):
    log_path = os.path.join(TMP, log_name)
    handle = open(log_path, "w", encoding="utf-8", newline="\n")
    cmd = [godot, "--headless", "--path", OUT, scene]
    if args:
        cmd.append("--")
        cmd.extend(args)
    proc = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT)
    return proc, handle, log_path


def _wait(proc, seconds):
    """等进程自己结束;超时返回 False(由调用方 kill)。"""
    end = time.time() + seconds
    while time.time() < end:
        if proc.poll() is not None:
            return True
        time.sleep(0.2)
    return proc.poll() is not None


def _stop(proc, handle):
    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except Exception:                                 # noqa: BLE001
            pass
    try:
        handle.close()
    except Exception:                                     # noqa: BLE001
        pass


def _log_text(path):
    try:
        return _read(path)
    except Exception:                                     # noqa: BLE001
        return ""


def run_case_one(godot, info, out):
    """实验一:示例『联机房间』真开房,对端当客户端。"""
    print("---- 实验一:示例做主机(联机_开房),对端连进来 ----")
    host, host_handle, host_log = _launch(
        godot, info["host_scene"], [], "case1_host.log")
    time.sleep(2.0)
    peer, peer_handle, peer_log = _launch(
        godot, "res://peer_host.tscn", ["--mode=client"], "case1_peer.log")
    _wait(peer, 30.0)
    _stop(peer, peer_handle)
    time.sleep(2.0)          # 让主机会员离开后再读日志
    _stop(host, host_handle)
    out["case1_host"] = _log_text(host_log)
    out["case1_peer"] = _log_text(peer_log)


def run_case_two(godot, info, out):
    """实验二:对端当服务端,示例『联机客户端』真加入。"""
    print("---- 实验二:对端做服务端,示例做客户端(联机_加入)----")
    peer, peer_handle, peer_log = _launch(
        godot, "res://peer_join.tscn", ["--mode=server"], "case2_peer.log")
    time.sleep(2.0)
    client, client_handle, client_log = _launch(
        godot, info["join_scene"], [], "case2_client.log")
    _wait(peer, 30.0)
    _stop(peer, peer_handle)
    time.sleep(1.0)
    _stop(client, client_handle)
    out["case2_peer"] = _log_text(peer_log)
    out["case2_client"] = _log_text(client_log)


def show(title, text):
    print(f"---- {title} ----")
    for line in text.splitlines():
        if line.strip():
            print("   " + line.strip())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--godot", default=(
        r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"))
    args = parser.parse_args()
    info = prepare()
    if info is None:
        return 1
    if not os.path.isfile(args.godot):
        print(f"跳过:找不到 Godot({args.godot})")
        return 0

    subprocess.run([args.godot, "--headless", "--path", OUT, "--import"],
                   capture_output=True, text=True, encoding="utf-8",
                   errors="replace")

    out = {}
    run_case_one(args.godot, info, out)
    run_case_two(args.godot, info, out)
    for key in ("case1_host", "case1_peer", "case2_peer", "case2_client"):
        show(key, out.get(key, ""))

    ok = True
    combined = "\n".join(out.values())
    for marker in ("SCRIPT ERROR", "Parse Error"):
        if marker in combined:
            bad = [line.strip() for line in combined.splitlines()
                   if marker in line][:3]
            print(f"✗ Godot 里有脚本错误 / 解析错误: {bad}")
            ok = False
    if "[GK] 联机失败" in combined:
        print("✗ 联机没起来(端口被占 / 地址不对):",
              [line.strip() for line in combined.splitlines()
               if "[GK] 联机失败" in line][:2])
        ok = False

    host = out.get("case1_host", "")
    peer1 = out.get("case1_peer", "")
    checks = (
        # 实验一:示例主机侧
        ("示例主机开房", "NET_HOST_READY" in host),
        ("示例主机看到『有人进来』", "NET_PEER_JOINED" in host),
        ("示例主机收到客户端消息(『联机_收到消息』)", "NET_HOST_GOT" in host),
        ("示例主机真的读到了消息内容", "PING_FROM_CLIENT" in host),
        ("[联机_发消息]单发:对端收到主机的欢迎语",
         "text=HELLO_FROM_HOST" in peer1),
        ("[联机_发消息]单发:对端收到主机的回信", "text=PONG_FROM_HOST" in peer1),
        ("对端连上了示例主机", "PEER_CONNECTED" in peer1),
        ("对端收完两轮消息后正常收尾", "PEER_DONE" in peer1),
        ("示例主机看到『有人走了』", "NET_PEER_LEFT" in host),
        # 实验二:示例客户端侧
        ("示例客户端发出加入", "NET_JOIN_SENT" in out.get("case2_client", "")),
        # ⚠ 示例客户端是把『内容』原样打印的(没有 text= 前缀),所以这里
        #   直接找那段文字本身。
        ("示例客户端收到服务端的消息(『联机_收到消息』)",
         "HELLO_FROM_PEER" in out.get("case2_client", "")),
        ("示例客户端收到服务端的回信",
         "PONG_FROM_PEER" in out.get("case2_client", "")),
        ("对端服务端看到客户端进来", "PEER_JOINED" in out.get("case2_peer", "")),
        ("[联机_发消息]广播:对端服务端收到了客户端的消息",
         "text=PING_FROM_CLIENT" in out.get("case2_peer", "")),
        ("示例客户端自己断开了(『联机_断开』)",
         "NET_CLIENT_LEFT" in out.get("case2_client", "")),
        ("对端服务端看到客户端离开", "PEER_LEFT" in out.get("case2_peer", "")),
    )
    for name, passed in checks:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
        if not passed:
            ok = False

    print("CHECK_NET_DEMO=" + ("OK" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
