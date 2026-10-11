"""3D 恐怖剧情闯关(第一/三人称可切换)示例的自动检查。

用法:
    python scripts\\check_story_demo.py
    python scripts\\check_story_demo.py --godot "D:\\godot\\Godot_v4.7.2-stable_win64_console.exe"

它会:
  1. 把 `examples/demo_story/恐怖闯关示例_godot` 拷到 `_dist/story_probe`
     (**不碰示例本身**);
  2. 在副本里加一个探针场景,把主场景指过去;
  3. 用 Godot 无头跑,读探针打印的 STORY_* 行,汇总 PASS / FAIL。

── 探针怎么测(而不是读文本)────────────────────────────────────
探针**自己当主场景**,把三张关卡 .tscn **依次**实例化成子节点驱动
(测完 `queue_free()`)。**不能**用 `change_scene_to_file` 跑关卡切换 ——
那会把当前场景(探针)整棵换掉,后面的断言就没机会跑。所以关卡切换那一半
用**出码文本**验(见 `_code_checks`)。

断言(全是"现象",不是"代码长什么样"):
  * 关卡 1 大堂:章节卡 + HUD(目标 / 钥匙数 / 电量 / 提示)建出来;
    **C → 第一人称机位 / V → 第三人称机位**;走进钥匙触发区 -> 钥匙数 = 1、
    钥匙物体隐掉、目标文字改了;F 开手电(SpotLight 亮 + 掉电)、再按 F 关;
    走进大门区域 -> 大门链真的跑了(时间轴开始 + 门板在升起);
  * 关卡 2 走廊:开场对话 / 剧情选择弹出来;AI 在巡逻;钻柜子 -> 冻结成
    『躲藏着』+ 提示『屏住呼吸…』+ 暗角变深,出来恢复;凑到怪物正前方近处 ->
    转追击 + 紧张度上去 + 心跳播放器在响 + 贴身惊吓(相机在震);
  * 关卡 3 结局:选择框弹出 -> 按 1 -> 存档落盘(user://)+ 结局大字显示。
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "examples", "demo_story", "恐怖闯关示例_godot")
OUT = os.path.join(ROOT, "_dist", "story_probe")
GODOT_DEFAULT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"

# 三关(Blender 场景名 = 导出的 .tscn 名)
LOBBY = "大堂"
CORRIDOR = "走廊"
ENDING = "结局"

PROBE_GD = '''extends Node

# 恐怖闯关示例的探针。结论行统一 STORY_ 前缀,Python 那边逐条断言。
#
# 探针**自己当主场景**,把三张关卡 .tscn **依次**实例化成子节点驱动
# (测完 queue_free())—— 不用 change_scene_to_file:那会把探针整棵换掉。
# 状态机跑在 _physics_process 里(而不是 await 协程):按 C / V / F 这类键
# 要 `Input.is_action_just_pressed` 轮询,而"按下"和"松开"必须**跨帧**
# (同一帧里松了又按,那一帧的 just_pressed 会被后面的松手冲掉)。
# ⚠ 探针的物理帧优先级压到最低:保证按下键那一刻**先**被探针发出去,
#   同一帧里关卡角色的 _physics_process 才读得到"刚按下"。
var world: Node = null
var player: Node = null
var enemy: Node = null
var step := 0
var t := 0.0
# 跨步要留的值
var spawn := Vector3.ZERO
var after_c := ""
var after_v := ""
var torch: Node = null
var on_visible := false
var on_flag := false
var drain := 0.0
var battery_ui := ""
var off_visible := false
var door_y0 := 0.0
var hide_state := ""
var hide_hint := ""
var hide_vig := -1.0
var heard := false
var max_shake := 0.0
var advance_at := 0.0


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	# 探针先跑、关卡后跑(见文件头)。
	process_physics_priority = -1000
	# ⚠ 无头模式主循环**不锁帧**(能跑到几百 fps),而物理步是每秒 60 次 ——
	#   于是 `--quit-after` 数的"帧"远远跑在"物理时间"前面:三关加起来要
	#   十几秒物理时间,却只够跑一两千帧,安全网会**提前**把探针掐掉(实测
	#   撞过:走廊跑完、结局那关一个字都没打印)。锁到 60 = 帧率与物理同步,
	#   1800 帧差不多就是 30 秒,安全网才是真的"安全网"。
	Engine.max_fps = 60
	# ⚠ 无头模式默认视口只有 64×64:先调大再实例化 —— 界面控件的锚点 /
	#   一些按视口尺寸算的逻辑都按它来,太小会读到怪值。
	get_window().size = Vector2i(1152, 648)


func _push_key(code: int, pressed: bool) -> void:
	# 和真人按键走同一条路(事件流)。keycode + physical_keycode 都设:
	# 生成脚本里既有按动作名查的(C / V / F),也有直接看键码的(选项按 1/2)。
	var event := InputEventKey.new()
	event.keycode = code
	event.physical_keycode = code
	event.pressed = pressed
	Input.parse_input_event(event)


func _find(name: String, type_name: String) -> Node:
	if world == null:
		return null
	var found := world.find_children(name, type_name, true, false)
	return found[0] if not found.is_empty() else null


func _load(level: String) -> void:
	world = load("res://scenes/" + level + ".tscn").instantiate()
	add_child(world)


func _drop_world() -> void:
	if world != null:
		world.queue_free()
	world = null
	player = null
	enemy = null


func _widget(name: String) -> Node:
	if player == null:
		return null
	var ui := player.find_child("GKUI", true, false)
	if ui == null:
		return null
	var root := ui.find_child("Root", true, false)
	if root == null:
		return null
	return root.find_child(name, true, false)


func _text(name: String) -> String:
	var node := _widget(name)
	return String(node.text) if node != null else "<无>"


func _var(name: String):
	return player.get(name) if player != null else null


func _panel_visible(which: String) -> bool:
	var panel = _var(which)
	return panel != null and panel.visible


func _vig_amount() -> float:
	# 暗角是**全屏 ColorRect + 着色器**做的(不是 Environment 参数),
	# 所以读材质的 `vignette_amount` —— 节点不存在时返回 -1。
	var mat = _var("_gk_vignette_mat")
	if mat == null:
		return -1.0
	return float(mat.get_shader_parameter("vignette_amount"))


func _cam_name() -> String:
	var cam := get_viewport().get_camera_3d()
	return String(cam.name) if cam != null else "<无>"


func _physics_process(delta: float) -> void:
	t += delta
	match step:
		# ── 关卡 1 大堂 ────────────────────────────────────────────
		0:
			if t >= 0.1:
				_load("%(lobby)s")
				player = _find("大堂玩家", "CharacterBody3D")
				step = 1
				t = 0.0
		1:
			if t >= 0.5:
				print("STORY_LOBBY_HUD goal=", _text("目标文本"),
					" keys=", _text("钥匙文本"),
					" battery=", _text("电量文本"),
					" hint=", _text("提示文本"),
					" card=", _text("章节卡"))
				_push_key(KEY_C, true)      # C = 第一人称
				step = 2
				t = 0.0
		2:
			if t >= 0.25:
				_push_key(KEY_C, false)
				step = 3
				t = 0.0
		3:
			if t >= 0.3:
				after_c = _cam_name()
				_push_key(KEY_V, true)      # V = 第三人称
				step = 4
				t = 0.0
		4:
			if t >= 0.25:
				_push_key(KEY_V, false)
				step = 5
				t = 0.0
		5:
			if t >= 0.3:
				after_v = _cam_name()
				print("STORY_LOBBY_CAM after_c=", after_c, " after_v=", after_v)
				# 挪进钥匙触发区(物理帧之后 Area3D 才会报进出事件)
				var key_area := _find("钥匙", "Area3D")
				if key_area != null and player != null:
					player.global_position = key_area.global_position
				step = 6
				t = 0.0
		6:
			if t >= 0.4:
				var key_area2 := _find("钥匙", "Area3D")
				print("STORY_LOBBY_KEY picked=", int(_var("钥匙数")),
					" visible=", key_area2.visible if key_area2 != null else "<无>",
					" keys_ui=", _text("钥匙文本"),
					" goal_ui=", _text("目标文本"))
				torch = _find("手电筒", "SpotLight3D")
				_push_key(KEY_F, true)      # F = 手电开
				step = 7
				t = 0.0
		7:
			if t >= 0.25:
				_push_key(KEY_F, false)
				step = 8
				t = 0.0
		8:
			if t >= 0.2:
				on_visible = torch != null and torch.visible
				step = 9
				t = 0.0
		9:
			# 亮着掉 1.5 秒电,再看电量少没少
			if t >= 1.5:
				on_flag = bool(_var("手电开"))
				drain = float(_var("手电电量"))
				battery_ui = _text("电量文本")
				_push_key(KEY_F, true)      # 再按一次 = 关
				step = 10
				t = 0.0
		10:
			if t >= 0.25:
				_push_key(KEY_F, false)
				step = 11
				t = 0.0
		11:
			if t >= 0.2:
				off_visible = torch != null and torch.visible
				print("STORY_LOBBY_TORCH on_visible=", on_visible,
					" on_flag=", on_flag,
					" drain=", snappedf(drain, 0.01),
					" battery_ui=", battery_ui,
					" off_visible=", off_visible)
				var door := _find("大门门板", "AnimatableBody3D")
				door_y0 = door.global_position.y if door != null else 0.0
				var door_area := _find("大门区域", "Area3D")
				if door_area != null and player != null:
					player.global_position = door_area.global_position
				step = 12
				t = 0.0
		12:
			if t >= 0.45:
				var door2 := _find("大门门板", "AnimatableBody3D")
				print("STORY_LOBBY_DOOR tl_play=", _var("_gk_tl_play"),
					" door_y0=", snappedf(door_y0, 0.01),
					" door_y=", snappedf(door2.global_position.y, 0.01) if door2 != null else 0.0)
				step = 13
				t = 0.0
		13:
			# 大门的时间轴 0.9 秒后会自动切场景(会换掉探针)—— 看完副作用就放掉
			if t >= 0.1:
				_drop_world()
				print("STORY_LOBBY_DONE")
				step = 14
				t = 0.0
		# ── 关卡 2 走廊 ────────────────────────────────────────────
		14:
			if t >= 0.1:
				_load("%(corridor)s")
				player = _find("走廊玩家", "CharacterBody3D")
				enemy = _find("怪物", "CharacterBody3D")
				step = 15
				t = 0.0
		15:
			if t >= 0.5:
				spawn = player.global_position if player != null else Vector3.ZERO
				print("STORY_CORRIDOR_START state=", _var("ai_state"),
					" has_enemy=", enemy != null,
					" far_tension=", snappedf(float(_var("_gk_heart_tension")), 0.001))
				step = 16
				t = 0.0
		16:
			# 开场章节卡 3 秒之后才弹对话
			if t >= 3.0:
				print("STORY_CORRIDOR_DIALOG visible=", _panel_visible("_gk_dialog_panel"))
				advance_at = 0.0
				step = 17
				t = 0.0
		17:
			# 反复敲回车推进对话,直到**剧情选择**弹出(或超时兜底)
			if _panel_visible("_gk_choice_panel"):
				print("STORY_CORRIDOR_CHOICE visible=true")
				step = 18
				t = 0.0
			elif t >= 8.0:
				print("STORY_CORRIDOR_CHOICE visible=false")
				step = 18
				t = 0.0
			elif t >= advance_at:
				_push_key(KEY_ENTER, true)
				_push_key(KEY_ENTER, false)
				advance_at = t + 0.35
		18:
			if t >= 0.2:
				# 躲进柜子甲
				var cab := _find("柜子甲", "Area3D")
				if cab != null and player != null:
					player.global_position = cab.global_position
				step = 19
				t = 0.0
		19:
			if t >= 0.6:
				hide_state = String(_var("ai_state"))
				hide_hint = _text("提示文本")
				hide_vig = _vig_amount()
				# 挪出去(回出生点)
				if player != null:
					player.global_position = spawn
				step = 20
				t = 0.0
		20:
			if t >= 0.6:
				print("STORY_CORRIDOR_HIDE in_state=", hide_state,
					" in_hint=", hide_hint,
					" out_state=", _var("ai_state"),
					" out_hint=", _text("提示文本"))
				print("STORY_CORRIDOR_VIGNETTE hide=", snappedf(hide_vig, 0.01),
					" exit=", snappedf(_vig_amount(), 0.01),
					" layer=", _var("_gk_vignette_layer") != null)
				# 挪到怪物正前方近处:看见就追 + 贴身惊吓
				if enemy != null and player != null:
					player.global_position = enemy.global_position + Vector3(2.0, 0.0, 0.0)
				heard = false
				max_shake = 0.0
				step = 21
				t = 0.0
		21:
			# 追 + 吓:这 1.2 秒里每帧采样心跳 / 震动
			var heart = _var("_gk_heart_player")
			if heart != null and heart.playing:
				heard = true
			max_shake = maxf(max_shake, float(_var("_gk_shake_left")))
			if t >= 1.2:
				print("STORY_CORRIDOR_CHASE state=", _var("ai_state"),
					" near_tension=", snappedf(float(_var("_gk_heart_tension")), 0.001),
					" heart=", heart != null,
					" playing=", heard,
					" shake=", snappedf(max_shake, 0.01))
				step = 22
				t = 0.0
		22:
			if t >= 0.1:
				_drop_world()
				print("STORY_CORRIDOR_DONE")
				step = 23
				t = 0.0
		# ── 关卡 3 结局 ────────────────────────────────────────────
		23:
			if t >= 0.1:
				_load("%(ending)s")
				player = _find("结局玩家", "CharacterBody3D")
				step = 24
				t = 0.0
		24:
			# 章节卡 3 秒之后才弹对话
			if t >= 3.0:
				advance_at = 0.0
				step = 25
				t = 0.0
		25:
			# 推进开场两句对话,直到**结局选择**弹出
			if _panel_visible("_gk_choice_panel"):
				print("STORY_ENDING_CHOICE visible=true")
				step = 26
				t = 0.0
			elif t >= 8.0:
				print("STORY_ENDING_CHOICE visible=false")
				step = 26
				t = 0.0
			elif t >= advance_at:
				_push_key(KEY_ENTER, true)
				_push_key(KEY_ENTER, false)
				advance_at = t + 0.35
		26:
			# 选第一个选项(按 1 = 回头救她)
			_push_key(KEY_1, true)
			step = 27
			t = 0.0
		27:
			if t >= 0.25:
				_push_key(KEY_1, false)
				step = 28
				t = 0.0
		28:
			if t >= 0.6:
				var big := _widget("结局大字1")
				print("STORY_ENDING_RESULT ending=", int(_var("结局")),
					" saved=", FileAccess.file_exists("user://gk_story_end.json"),
					" big_visible=", big != null and big.visible,
					" big_text=", String(big.text) if big != null else "<无>")
				step = 29
				t = 0.0
		29:
			if t >= 0.1:
				_drop_world()
				print("STORY_ENDING_DONE")
				print("STORY_DONE")
				set_physics_process(false)
				get_tree().quit()
''' % {"lobby": LOBBY, "corridor": CORRIDOR, "ending": ENDING}

PROBE_TSCN = '''[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://probe.gd" id="1_probe"]

[node name="Probe" type="Node"]
script = ExtResource("1_probe")
'''


def prepare():
    if not os.path.isdir(SRC):
        print(f"跳过:找不到示例工程 {SRC} —— 先跑 make_story_demo.py")
        return False
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    shutil.copytree(SRC, OUT, ignore=shutil.ignore_patterns(".godot", "*.import"))
    with open(os.path.join(OUT, "probe.gd"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE_GD)
    with open(os.path.join(OUT, "probe.tscn"), "w",
              encoding="utf-8", newline="\n") as handle:
        handle.write(PROBE_TSCN)
    project_file = os.path.join(OUT, "project.godot")
    with open(project_file, "r", encoding="utf-8") as handle:
        text = handle.read()
    text = re.sub(r'run/main_scene="[^"]*"',
                  'run/main_scene="res://probe.tscn"', text)
    with open(project_file, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return True


def run_godot(godot):
    if not os.path.exists(godot):
        print(f"跳过:找不到 Godot({godot})")
        return None

    def _import_and_run():
        subprocess.run([godot, "--headless", "--path", OUT, "--import"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
        done = subprocess.run([godot, "--headless", "--path", OUT,
                               "--quit-after", "1800"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace")
        return (done.stdout or "") + (done.stderr or "")

    out = _import_and_run()
    # ── 偶发兜底:第一次 --import 偶尔会漏掉某个 .glb(门禁实测撞过),
    #    载入 .tscn 会报 "referenced non-existent resource"。重跑一次;真缺
    #    文件的话第二次照样报,只是把"偶发没导入齐"和"真没有"分开。
    if "non-existent resource" in out:
        print("    ⚠ 导入没齐(non-existent resource),重跑一次…")
        out = _import_and_run()
    return out


def _errors(text, problems):
    for line in text.splitlines():
        if "SCRIPT ERROR" in line or "Parse Error" in line:
            problems.append(f"Godot 脚本 / 解析错误: {line.strip()}")
            return


def _need(problems, text, name, pattern, describe):
    """抓一条 STORY 行;抓不到就记一条失败。返回 match 或 None。"""
    match = re.search(pattern, text)
    if not match:
        problems.append(f"{describe} 没跑出来(STORY_{name} 那行没打印)")
    return match


def _expect(problems, match, group_index, value, describe):
    if match is None:
        return False
    got = match.group(group_index)
    if got != value:
        problems.append(f"{describe} 不对(期望 {value},实际 {got}): " + match.group(0))
        return False
    return True


def _code_checks(problems):
    """结构层 + 关卡切换(读文件,不用 Godot)。

    ── 为什么"切关卡"只能这么验 ──────────────────────────────────
    关底那三句 `change_scene_to_file(...)` 一旦真的执行,就会把**当前场景**
    (探针)整棵换掉 —— 探针里的断言再也没机会跑。所以这一半用**出码文本**:
    每个 `game_logic_*.gd` 里必须真的有"切到下一关"那句,而且路径对得上。
    """
    # 主场景 = 大堂(按 F5 从第一关进)
    project_file = os.path.join(SRC, "project.godot")
    with open(project_file, "r", encoding="utf-8") as handle:
        project = handle.read()
    if 'run/main_scene="res://scenes/大堂.tscn"' not in project:
        problems.append("主场景不是大堂(按 F5 应该从第一关进)")

    # 三张关卡 .tscn:都在,且各自引用自己的 游戏逻辑 / 环境脚本
    levels = ((LOBBY, "大堂", "大堂"), (CORRIDOR, "走廊", "走廊"),
              (ENDING, "结局", "结局"))
    for level, logic, env in levels:
        scene_path = os.path.join(OUT, "scenes", f"{level}.tscn")
        if not os.path.isfile(scene_path):
            problems.append(f"没找到场景 scenes/{level}.tscn")
            continue
        with open(scene_path, "r", encoding="utf-8") as handle:
            scene = handle.read()
        if f"res://scripts/game_logic_{logic}.gd" not in scene:
            problems.append(f"{level}.tscn 没引用 game_logic_{logic}.gd")
        if f"res://scripts/gk_environment_{env}.gd" not in scene:
            problems.append(f"{level}.tscn 没引用 gk_environment_{env}.gd")

    # 每个 game_logic 里要有"切到下一关"那句(路径对得上)
    switches = {
        "大堂": "res://scenes/走廊.tscn",
        "走廊": "res://scenes/结局.tscn",
        "结局": "res://scenes/大堂.tscn",
    }
    for level, target in switches.items():
        path = os.path.join(OUT, "scripts", f"game_logic_{level}.gd")
        if not os.path.isfile(path):
            problems.append(f"没找到生成的脚本 scripts/game_logic_{level}.gd")
            continue
        with open(path, "r", encoding="utf-8") as handle:
            code = handle.read()
        if "change_scene_to_file" not in code:
            problems.append(f"game_logic_{level}.gd 里没有切关卡的调用")
        if f'"{target}"' not in code:
            problems.append(f"game_logic_{level}.gd 里没有切到 {target} 的那句")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--godot", default=GODOT_DEFAULT)
    args = parser.parse_args()
    if not prepare():
        return 0

    text = run_godot(args.godot)
    if text is None:
        return 0
    for line in text.splitlines():
        if "STORY_" in line or "SCRIPT ERROR" in line or "Parse Error" in line:
            print(line.strip())

    problems = []
    _errors(text, problems)

    # ── 关卡 1 大堂 ─────────────────────────────────────────────────
    hud = _need(problems, text, "LOBBY_HUD",
                r"STORY_LOBBY_HUD goal=(\S*) keys=(\S*) battery=(\S*) "
                r"hint=(\S*) card=(\S*)", "大堂 HUD")
    if hud:
        _expect(problems, hud, 1, "目标:找到钥匙,打开大门", "大堂目标文字")
        _expect(problems, hud, 2, "钥匙:0", "大堂钥匙数文字")
        # 电量控件:格式是 `手电电量:<数>`(Godot 的 str(100.0) 会带 .0),
        # 只断言"前缀对 + 开局是满格"—— 顺带证明电量真的初始化成了 100。
        if not hud.group(3).startswith("手电电量:"):
            problems.append("大堂电量文字不对(期望前缀『手电电量:』,实际 "
                            + hud.group(3) + "): " + hud.group(0))
        elif float(hud.group(3).split(":", 1)[1]) != 100.0:
            problems.append("大堂开局电量不是满格: " + hud.group(0))
        _expect(problems, hud, 5, "第一章:门厅", "大堂章节卡文字")
    cam = _need(problems, text, "LOBBY_CAM",
                r"STORY_LOBBY_CAM after_c=(\S+) after_v=(\S+)", "大堂 C/V 切机位")
    if cam:
        _expect(problems, cam, 1, "大堂第一人称机位", "按 C 后当前相机(应第一人称)")
        _expect(problems, cam, 2, "大堂第三人称机位", "按 V 后当前相机(应第三人称)")
    key = _need(problems, text, "LOBBY_KEY",
                r"STORY_LOBBY_KEY picked=(\S+) visible=(\S+) keys_ui=(\S+) "
                r"goal_ui=(\S+)", "大堂拾钥匙")
    if key:
        _expect(problems, key, 1, "1", "拾钥匙后钥匙数")
        _expect(problems, key, 2, "false", "拾钥匙后钥匙物体可见性")
        _expect(problems, key, 3, "钥匙:1", "拾钥匙后 HUD 钥匙数")
        _expect(problems, key, 4, "目标:打开大门", "拾钥匙后 HUD 目标文字")
    torch = _need(problems, text, "LOBBY_TORCH",
                  r"STORY_LOBBY_TORCH on_visible=(\S+) on_flag=(\S+) "
                  r"drain=([\d.]+) battery_ui=(\S+) off_visible=(\S+)", "大堂手电")
    if torch:
        _expect(problems, torch, 1, "true", "按 F 后手电灯可见")
        _expect(problems, torch, 2, "true", "按 F 后『手电开』")
        if float(torch.group(3)) >= 99.999:
            problems.append("手电亮着但电量没下降(默认 0 / 没初始化?): "
                            + torch.group(0))
        try:
            lit_level = float(torch.group(4).split(":", 1)[1])
        except (ValueError, IndexError):
            lit_level = -1.0
        if lit_level >= 100.0 or lit_level < 0.0:
            problems.append("开了手电后 HUD 电量文字没变: " + torch.group(0))
        _expect(problems, torch, 5, "false", "再按 F 后手电灯可见")
    door = _need(problems, text, "LOBBY_DOOR",
                 r"STORY_LOBBY_DOOR tl_play=(\S+) door_y0=([\d.\-]+) "
                 r"door_y=([\d.\-]+)", "大堂大门链")
    if door:
        _expect(problems, door, 1, "true", "走进大门区域后时间轴启动")
        if not (float(door.group(3)) > float(door.group(2)) + 0.3):
            problems.append(f"大门时间轴启动了但门板没升起"
                            f"({door.group(2)} -> {door.group(3)}): " + door.group(0))
    if "STORY_LOBBY_DONE" not in text:
        problems.append("大堂探针没跑完")

    # ── 关卡 2 走廊 ─────────────────────────────────────────────────
    start = _need(problems, text, "CORRIDOR_START",
                  r"STORY_CORRIDOR_START state=(\S+) has_enemy=(\S+) "
                  r"far_tension=([\d.]+)", "走廊开场")
    if start:
        _expect(problems, start, 1, "巡逻", "走廊怪物开场状态(AI 在跑)")
        _expect(problems, start, 2, "true", "走廊怪物找得到")
    dial = _need(problems, text, "CORRIDOR_DIALOG",
                 r"STORY_CORRIDOR_DIALOG visible=(\S+)", "走廊开场对话框")
    if dial:
        _expect(problems, dial, 1, "true", "走廊开场对话框弹出")
    choice = _need(problems, text, "CORRIDOR_CHOICE",
                   r"STORY_CORRIDOR_CHOICE visible=(\S+)", "走廊剧情选择")
    if choice:
        _expect(problems, choice, 1, "true", "走廊剧情选择弹出")
    hide = _need(problems, text, "CORRIDOR_HIDE",
                 r"STORY_CORRIDOR_HIDE in_state=(\S+) in_hint=(\S+) "
                 r"out_state=(\S+) out_hint=(\S*)", "走廊躲藏")
    if hide:
        _expect(problems, hide, 1, "躲藏着", "钻进柜子后 AI 状态(应冻结)")
        _expect(problems, hide, 2, "屏住呼吸…", "钻进柜子后 HUD 提示")
        _expect(problems, hide, 3, "巡逻", "离开柜子后 AI 状态(应恢复)")
        _expect(problems, hide, 4, "", "离开柜子后 HUD 提示(应清空)")
    vig = _need(problems, text, "CORRIDOR_VIGNETTE",
                r"STORY_CORRIDOR_VIGNETTE hide=([\d.\-]+) exit=([\d.\-]+) "
                r"layer=(\S+)", "走廊暗角")
    if vig:
        _expect(problems, vig, 3, "true", "走廊暗角全屏节点存在")
        if float(vig.group(1)) <= float(vig.group(2)):
            problems.append(f"躲藏时暗角没变深({vig.group(1)} vs {vig.group(2)}): "
                            + vig.group(0))
    chase = _need(problems, text, "CORRIDOR_CHASE",
                  r"STORY_CORRIDOR_CHASE state=(\S+) near_tension=([\d.]+) "
                  r"heart=(\S+) playing=(\S+) shake=([\d.]+)", "走廊追击/惊吓")
    if chase:
        _expect(problems, chase, 1, "追击", "凑近后怪物状态(应看见就追)")
        if float(chase.group(2)) < 0.6:
            problems.append("凑近后紧张度没上去: " + chase.group(0))
        _expect(problems, chase, 3, "true", "心跳播放器建起来")
        _expect(problems, chase, 4, "true", "心跳播放器在响")
        if float(chase.group(5)) <= 0.0:
            problems.append("贴身了但惊吓没触发(相机没震): " + chase.group(0))
    if "STORY_CORRIDOR_DONE" not in text:
        problems.append("走廊探针没跑完")

    # ── 关卡 3 结局 ─────────────────────────────────────────────────
    end_choice = _need(problems, text, "ENDING_CHOICE",
                       r"STORY_ENDING_CHOICE visible=(\S+)", "结局选择框")
    if end_choice:
        _expect(problems, end_choice, 1, "true", "结局选择框弹出")
    result = _need(problems, text, "ENDING_RESULT",
                   r"STORY_ENDING_RESULT ending=(\S+) saved=(\S+) "
                   r"big_visible=(\S+) big_text=(\S+)", "结局结果")
    if result:
        _expect(problems, result, 1, "1", "按 1 后『结局』变量")
        _expect(problems, result, 2, "true", "结局存档落盘(user://gk_story_end.json)")
        _expect(problems, result, 3, "true", "结局大字显示出来")
        _expect(problems, result, 4, "结局:回头救她", "结局大字文字")
    if "STORY_ENDING_DONE" not in text:
        problems.append("结局探针没跑完")

    # ── 结构层 / 关卡切换(读文件)──────────────────────────────────
    _code_checks(problems)

    if "STORY_DONE" not in text:
        problems.append("探针没跑完(可能中途报错 / 卡住)")

    for item in dict.fromkeys(problems):
        print(f"✗ {item}")
    print("CHECK_STORY_DEMO=" + ("OK" if not problems else "FAIL"))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
