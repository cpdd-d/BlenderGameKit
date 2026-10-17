"""昼夜循环 + 天空盒 + 太阳 + 云层 —— 真机验收(第一百三十三轮,待办清单②)。

用法:
    python scripts\\check_daynight.py [--godot <godot.exe>] [--keep]

做三件事,全部在**真 Godot** 里跑:
  1. 用插件自己的导出代码(带 `gk_stub`)生成"带昼夜那一块"的环境脚本,
     再拷一份天空着色器 `gk_sky.gdshader`;
  2. 搭一个最小工程:scene(WorldEnvironment + 平行光 + 相机 + 挂环境脚本的节点);
  3. 探针逐条断言 —— 天空材质真的挂上、时刻真的在走、太阳真的跟着转、
     跳到某时刻 / 暂停都生效、云在飘。

── 为什么不能只静态扫字符串 ──────────────────────────────────────────
这类功能的坑**全在运行期**:`get_viewport().find_world_3d()` 拿到 null、
`Sky` 没挂上、`Environment.BG_SKY` 枚举写错、暂停时着色器的 `TIME` 照走
("天黑了但云还在飘")。静态扫字符串这些一个都发现不了。

用法与退出码:`DAYNIGHT_CHECK=OK` + 退出码 0 = 全绿。
"""

import argparse
import importlib
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORK = os.path.join(ROOT, "_dist", "daynight_probe")
GODOT_CANDIDATES = (
    r"D:\godot\Godot_v4.7.2-stable_win64_console.exe",
    r"D:\godot\Godot_v4.7.2-stable_win64.exe",
)

PROJECT_GODOT = """config_version=5

[application]

config/name="DayNight Verify"
run/main_scene="res://scenes/World.tscn"
config/features=PackedStringArray("4.7")

[rendering]

renderer/rendering_method="forward_plus"
"""

SCENE = """[gd_scene load_steps=3 format=3]

[ext_resource type="Script" path="res://scripts/gk_environment.gd" id="1_env"]

[sub_resource type="Environment" id="gk_env"]

[node name="World" type="Node3D"]

[node name="Environment" type="Node" parent="."]
script = ExtResource("1_env")

[node name="Sun" type="DirectionalLight3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 0.7, 0.7, 0, -0.7, 0.7, 0, 6, 0)
light_energy = 1.0

[node name="WorldEnvironment" type="WorldEnvironment" parent="."]
environment = SubResource("gk_env")

[node name="Camera" type="Camera3D" parent="."]
transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 6)
"""

PROBE = '''extends SceneTree

var _failed := 0


func _check(name: String, ok: bool, detail: String = "") -> void:
	if not ok:
		_failed += 1
	print("DAYNIGHT ", name, "=", "ok" if ok else "FAIL", " ", detail)


func _initialize() -> void:
	var main: String = str(ProjectSettings.get_setting("application/run/main_scene", ""))
	var packed: PackedScene = load(main) as PackedScene
	if packed == null:
		print("DAYNIGHT_CHECK=FAILED scene")
		quit(1)
		return
	var root: Node = packed.instantiate()
	get_root().add_child(root)
	await process_frame
	await process_frame
	await physics_frame

	var env: Node = get_first_node_in_group("gk_daynight")
	_check("group", env != null)
	if env == null:
		_finish()
		return

	# ① 天空:程序化天空真的装到了 WorldEnvironment 上
	var world: World3D = get_root().world_3d
	var env_res: Environment = world.environment if world != null else null
	var shader_path := ""
	if env._gk_sky_mat != null and env._gk_sky_mat.shader != null:
		shader_path = env._gk_sky_mat.shader.resource_path
	_check("sky_material", env._gk_sky_on and env._gk_sky_mat != null, str(shader_path))
	_check("sky_shader", shader_path.ends_with("gk_sky.gdshader"), shader_path)
	_check("background_sky", env_res != null
			and env_res.background_mode == Environment.BG_SKY,
			str(env_res.background_mode) if env_res != null else "no environment")
	_check("sky_resource", env_res != null and env_res.sky != null)

	# ② 太阳:方向 / 强度跟着时刻走
	var sun: DirectionalLight3D = env._gk_daynight_find_sun()
	_check("sun_found", sun != null)
	if sun == null:
		_finish()
		return
	env._gk_daynight_set_time(6.0)
	await process_frame
	var dir_morning: Vector3 = sun.global_transform.basis.z.normalized()
	env._gk_daynight_set_time(12.0)
	await process_frame
	var dir_noon: Vector3 = sun.global_transform.basis.z.normalized()
	var energy_noon: float = sun.light_energy
	var day_noon: float = env._gk_sky_mat.get_shader_parameter("day_amount")
	var top_noon: Color = env._gk_sky_mat.get_shader_parameter("sky_top")
	env._gk_daynight_set_time(0.0)
	await process_frame
	var dir_night: Vector3 = sun.global_transform.basis.z.normalized()
	var energy_night: float = sun.light_energy
	var day_night: float = env._gk_sky_mat.get_shader_parameter("day_amount")
	var top_night: Color = env._gk_sky_mat.get_shader_parameter("sky_top")

	_check("sun_orbit", dir_morning.distance_to(dir_noon) > 0.05,
			"%.3f" % dir_morning.distance_to(dir_noon))
	_check("sun_below_at_night", dir_night.y < 0.0, "y=%.3f" % dir_night.y)
	_check("noon_brighter", energy_noon > energy_night * 2.0,
			"noon=%.4f night=%.4f" % [energy_noon, energy_night])
	_check("sky_day_amount", day_noon > day_night + 0.3,
			"noon=%.3f night=%.3f" % [day_noon, day_night])
	_check("sky_top_changes", top_noon != top_night,
			str(top_noon) + " vs " + str(top_night))

	# ③ 时间真的在走(直接喂 tick:无头跑得快,墙钟不可靠)
	env._gk_daynight_set_speed(1.0)
	env._gk_daynight_pause(false)
	env._gk_daynight_set_time(8.0)
	env._gk_daynight_tick(1.0)
	_check("hour_advances", absf(env._gk_dn_hour - 8.4) < 0.01, "%.4f" % env._gk_dn_hour)
	env._gk_daynight_set_speed(2.0)
	env._gk_daynight_set_time(8.0)
	env._gk_daynight_tick(1.0)
	_check("speed_doubles", absf(env._gk_dn_hour - 8.8) < 0.01, "%.4f" % env._gk_dn_hour)

	# ④ 暂停:时钟停住,但"跳到时刻"照样生效
	env._gk_daynight_set_speed(1.0)
	env._gk_daynight_pause(true)
	var frozen: float = env._gk_dn_hour
	env._gk_daynight_tick(1.0)
	env._gk_daynight_tick(1.0)
	_check("pause_freezes", is_equal_approx(env._gk_dn_hour, frozen),
			"%.4f -> %.4f" % [frozen, env._gk_dn_hour])
	var clouds_frozen: float = env._gk_sky_mat.get_shader_parameter("time_offset")
	env._gk_daynight_tick(0.5)
	_check("pause_freezes_clouds",
			is_equal_approx(float(env._gk_sky_mat.get_shader_parameter("time_offset")),
					clouds_frozen),
			"%.4f" % clouds_frozen)
	env._gk_daynight_set_time(15.0)
	_check("jump_while_paused", is_equal_approx(env._gk_dn_hour, 15.0),
			"%.4f" % env._gk_dn_hour)
	env._gk_daynight_pause(false)

	# ⑤ 云:在飘(time_offset 涨),而且云参数真的喂给了着色器
	var t0: float = env._gk_sky_mat.get_shader_parameter("time_offset")
	env._gk_daynight_tick(0.5)
	var t1: float = env._gk_sky_mat.get_shader_parameter("time_offset")
	_check("clouds_drift", t1 > t0, "%.4f -> %.4f" % [t0, t1])
	_check("cloud_amount_reaches_shader",
			float(env._gk_sky_mat.get_shader_parameter("cloud_amount")) > 0.0,
			str(env._gk_sky_mat.get_shader_parameter("cloud_amount")))
	_check("cloud_on", float(env._gk_sky_mat.get_shader_parameter("cloud_on")) > 0.5)

	# ⑥ 物理帧那条路真的接上了(墙钟推进走的是它)
	var cb := Callable(env, "_gk_daynight_physics")
	_check("physics_frame_connected", physics_frame.is_connected(cb))

	# ⑦ 月亮(第一百四十三轮):夜里亮、白天暗;月盘参数喂到着色器
	var moon: DirectionalLight3D = env._gk_dn_moon
	_check("moon_light", moon != null)
	var moon_night := 0.0
	var moon_day := 0.0
	if moon != null:
		env._gk_daynight_set_time(0.0)
		await process_frame
		moon_night = moon.light_energy
		env._gk_daynight_set_time(12.0)
		await process_frame
		moon_day = moon.light_energy
		_check("moon_at_night", moon_night > 0.05, "%.4f" % moon_night)
		_check("moon_dim_by_day", moon_night > moon_day * 2.0,
				"night=%.4f day=%.4f" % [moon_night, moon_day])
	_check("moon_sky_params",
			float(env._gk_sky_mat.get_shader_parameter("moon_phase")) > 0.0)

	# ⑧ 天气:起手天气 + 切换后雾浓度真的变
	_check("weather_start", env._gk_env_get_weather() == "阴",
			env._gk_env_get_weather())
	var fog_before: float = env_res.fog_density
	env._gk_env_set_weather("RAINY", 0.0)
	await process_frame
	var fog_after: float = env_res.fog_density
	_check("weather_switch", fog_after > fog_before * 1.2,
			"%.4f -> %.4f" % [fog_before, fog_after])

	# ⑨ 对外接口:读时间 / 跳时刻 / 整点信号
	env._gk_daynight_set_time(18.5)
	await process_frame
	_check("api_hour", is_equal_approx(env._gk_env_get_hour(), 18.5),
			"%.3f" % env._gk_env_get_hour())
	_check("api_state", env._gk_env_get_state() != "", env._gk_env_get_state())
	var hits: Array = []
	var catcher := func(hour: int) -> void: hits.append(hour)
	if not env.gk_hour_reached.is_connected(catcher):
		env.gk_hour_reached.connect(catcher)
	env._gk_env_set_time(20.2)
	await process_frame
	_check("hour_signal", hits.has(19) and hits.has(20), str(hits))
	_finish()


func _finish() -> void:
	if _failed == 0:
		print("DAYNIGHT_CHECK=OK")
		quit(0)
	else:
		print("DAYNIGHT_CHECK=FAILED failures=", _failed)
		quit(1)
'''


def find_godot(explicit=""):
    if explicit:
        return explicit if os.path.isfile(explicit) else ""
    for candidate in GODOT_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("godot") or ""


def build_project(work):
    """用插件自己的代码生成环境脚本 + 拷着色器 + 写工程。"""
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(os.path.join(work, "scenes"), exist_ok=True)
    os.makedirs(os.path.join(work, "scripts"), exist_ok=True)
    os.makedirs(os.path.join(work, "shaders"), exist_ok=True)

    sys.path.insert(0, os.path.join(ROOT, "tests"))
    gk_stub = importlib.import_module("gk_stub")
    gk_stub.install()
    gk_stub.load_addon(os.path.join(ROOT, "BlenderGameKit"))
    ae = importlib.import_module("BlenderGameKit.asset_export")
    NN = importlib.import_module("BlenderGameKit.nodes.nodes")

    # ── 真节点类 + 桩环境:四张卡按真实默认值串在执行流上 ──────────────
    tree = gk_stub.StubNodeTree("游戏环境与后期")
    start = tree.new_node(NN.GKNodeEnvDefault, "环境")
    sky = tree.new_node(NN.GKNodeSkyBox, "天空盒")
    clouds = tree.new_node(NN.GKNodeClouds, "云")
    sun = tree.new_node(NN.GKNodeSun, "太阳")
    daynight = tree.new_node(NN.GKNodeDayNight, "昼夜")
    # 固定几个值,断言才好写;其余走卡上的出厂默认
    daynight.gk_day_seconds = 60.0
    daynight.gk_start_hour = 8.0
    clouds.gk_cloud_amount = 0.6
    moon = tree.new_node(NN.GKNodeMoon, "月亮")
    moon.gk_moon_energy = 0.5
    moon.gk_moon_phase = 0.5
    weather = tree.new_node(NN.GKNodeWeather, "天气")
    weather.gk_weather = "CLOUDY"
    gk_stub.connect(tree, start, "执行", sun, "执行")
    gk_stub.connect(tree, sun, "完成", sky, "执行")
    gk_stub.connect(tree, sky, "完成", clouds, "执行")
    gk_stub.connect(tree, clouds, "完成", moon, "执行")
    gk_stub.connect(tree, moon, "完成", daynight, "执行")
    gk_stub.connect(tree, daynight, "完成", weather, "执行")

    original = ae._post_graph
    ae._post_graph = lambda: tree
    try:
        block = ae._post_block()
    finally:
        ae._post_graph = original
    if "_gk_daynight_setup" not in block:
        raise SystemExit("环境脚本里没有生成昼夜那一块:\n" + block[:400])

    script = ae.render_env_script(camera_name="", post_block=block)
    with open(os.path.join(work, "scripts", "gk_environment.gd"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(script)
    shutil.copy2(os.path.join(ae.shader_source_dir(), "gk_sky.gdshader"),
                 os.path.join(work, "shaders", "gk_sky.gdshader"))
    with open(os.path.join(work, "project.godot"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROJECT_GODOT)
    with open(os.path.join(work, "scenes", "World.tscn"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(SCENE)
    with open(os.path.join(work, "daynight_probe.gd"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE)
    return work


def run(work, godot):
    env = dict(os.environ, GODOT_SILENCE_ROOT_WARNING="1")
    # ⚠ 必须显式 `encoding="utf-8"`:Godot 的输出是 UTF-8,而 Windows 上
    #   `text=True` 会用**系统区域编码(GBK)**去解 —— 一遇到不能解的字节
    #   就在读取线程里抛 UnicodeDecodeError,主流程拿到的是"没有任何输出",
    #   看着像"探针什么都没打印"(踩过一次)。
    common = dict(capture_output=True, text=True, timeout=300, env=env,
                  encoding="utf-8", errors="replace")
    subprocess.run([godot, "--headless", "--path", work, "--import"], **common)
    proc = subprocess.run([godot, "--headless", "--path", work,
                           "--script", "res://daynight_probe.gd"], **common)
    output = (proc.stdout or "") + "\n" + (proc.stderr or "")
    failures = []
    for line in output.splitlines():
        text = line.strip()
        if text.startswith("DAYNIGHT "):
            print("   " + text)
            if "=FAIL" in text:
                failures.append(text)
        elif "SCRIPT ERROR" in text or "Parse Error" in text or text.startswith("ERROR:"):
            # 脚本级报错直接透出来(着色器在 verify_shaders 那一关单独查)
            if "shader" not in text.lower():
                failures.append(text)
    ok = "DAYNIGHT_CHECK=OK" in output and not failures
    if not ok:
        print("---- 探针输出(尾部 30 行)----")
        for line in [ln for ln in output.splitlines() if ln.strip()][-30:]:
            print("   " + line)
    return ok


def main():
    parser = argparse.ArgumentParser(description="昼夜循环真机验收")
    parser.add_argument("--godot", default="", help="Godot 可执行文件")
    args = parser.parse_args()

    godot = find_godot(args.godot)
    if not godot:
        print("跳过:找不到 Godot")
        return 0

    work = build_project(WORK)
    print(f"工程:{work}")
    ok = run(work, godot)
    # 工程**故意留着**便于排查(体积很小,而且里面的环境脚本就是导出产物)
    print("DAYNIGHT_ACCEPT=" + ("OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
