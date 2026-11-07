r"""把恐怖示例**真的玩一遍**:标题 -> 教室 -> 走廊 -> 楼梯间 -> 回标题 -> 二周目。

用法:
    python scripts\play_horror_full.py
    python scripts\play_horror_full.py --expect-m0          # 顺带探 M0 新功能(缺了就 PLAY_MISSING)
    python scripts\play_horror_full.py --out .audit\horror_play_20261027

和 check_horror_demo.py 的区别(两个都在,分工不同):
    check_horror_demo.py  一幕一个探针,把 .tscn 当子节点塞进来,**不真切场景**
                          —— 静态、快、逐条断言,但"切关卡"本身没被走过。
    本脚本               **真切场景**:拷工程 -> 探针挂 [autoload](跨场景存活)
                          -> 带窗口真渲染跑起来 -> 真的点按钮 / 真的走触发区
                          -> 每步抓一张 png。抓的是"只有玩起来才暴露"的问题:
                          相机穿模、卡死、死局、UI 盖字、状态丢失。

流程(每一行都是 PLAY_ 前缀,便于 grep):
    标题页          PLAY_START / PLAY_TITLE / PLAY_SHOT 01_title
    点『开始游戏』  PLAY_PRESS / PLAY_ROOM_ENTER scene=教室
    教室            PLAY_ROOM_OPENED(运镜收回第三人称)/ 3 张纸条 /
                    PLAY_ROOM_GATE(只有 2 张时门口挡住)/ PLAY_ROOM_THREE
    走廊            PLAY_ENEMY / PLAY_K 弄出动静 / PLAY_NOISE state=去查看 /
                    PLAY_SCARE 惊吓区压低总线 / PLAY_FAIL 被抓冻结 + 重试按钮
    音乐教室(第3章) PLAY_CH3_ENTER / PLAY_CH3_CROUCH(按住 Ctrl 走到乐谱2,本轮验收点) /
                    PLAY_CH3_SCORES / PLAY_CH3_WRONG / PLAY_CH3_PLAY / PLAY_CH3_TAPE / PLAY_CH3_EXIT
    医务室(第4章)   PLAY_CH4_ENTER(电量必须 60)/ PLAY_CH4_BATTERY(+40)/ PLAY_CH4_HIDE /
                    PLAY_CH4_RECORDS / PLAY_CH4_DOOR;没落地就 PLAY_SKIP ch4 继续走楼梯间
    楼梯间          PLAY_STAIRS_ENTER / PLAY_STAIRS_BLACKOUT(断电+追兵)
                    / PLAY_CHOICE 天台面板 / PLAY_ENDING ended=2
    回标题          PLAY_BACK_TITLE(解冻 + 相机回标题机位)
    二周目          PLAY_NEW_RUN notes=0
    收尾            PLAY_DONE(全过)/ PLAY_ASSERT_FAIL(哪条不对)/ PLAY_TIMEOUT

退出码: 0 全过 / 1 断言不过 / 2 准备阶段失败 / 3 超时。
"""

import argparse
import datetime
import os
import shutil
import subprocess
import time
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                            # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
# 恐怖示例(标题 / 教室 / 走廊 / 楼梯间 四幕,主场景 = 标题)。
# 不是 examples/demo_story/恐怖闯关示例_godot —— 那是"剧情"示例(大堂/走廊/结局)。
SRC = ROOT / "examples" / "demo_horror" / "恐怖示例_godot"
DIST = ROOT / "_dist" / "horror_play"
PROFILE = ROOT / "_dist" / "horror_play_profile"
GODOT_DEFAULT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"

# 探针:挂 [autoload] 的 Node,切场景不会被释放。整段作为字符串写进拷贝
# (和 check_persistent_player.py 一个写法),占位符由 _prepare() 替换。
PROBE = r'''extends Node
# 自动试玩探针(临时文件,只存在于 _dist 的拷贝里,不写回示例)。
# 挂在 [autoload] 上 —— 每切一关都还在,所以能跟着玩家走完四幕。

const OUT_DIR := "__OUT_DIR__"
const EXPECT_M0 := __EXPECT_M0__
const LIMIT_SEC := __LIMIT_SEC__

var _fails := 0
var _shots := 0
var _clock := 0
var _step := "boot"
var _stopped := false


func _ready() -> void:
    process_mode = Node.PROCESS_MODE_ALWAYS
    _clock = Time.get_ticks_msec()
    DirAccess.make_dir_recursive_absolute(OUT_DIR)
    if DisplayServer.get_name() != "headless":
        DisplayServer.window_set_size(Vector2i(__WIN_W__, __WIN_H__))
        DisplayServer.window_set_position(Vector2i(40, 40))
    call_deferred("_run")


func _process(_delta: float) -> void:
    if _stopped:
        return
    if _now() > LIMIT_SEC:
        print("PLAY_TIMEOUT step=", _step, " sec=", snappedf(_now(), 0.1))
        _quit(3)


func _now() -> float:
    return float(Time.get_ticks_msec() - _clock) * 0.001


# ---- 读场景 ---------------------------------------------------------
func _scene() -> Node:
    return get_tree().current_scene


func _scene_name() -> String:
    var s := _scene()
    if s == null:
        return "<null>"
    return String(s.name)


func _find(wanted: String) -> Node:
    var s := _scene()
    if s != null:
        var hit := s.find_child(wanted, true, false)
        if hit != null:
            return hit
    var root := get_tree().root
    if root != null:
        return root.find_child(wanted, true, false)
    return null


func _var(wanted: String):
    var s := _scene()
    if s != null:
        var value = s.get(wanted)
        if value != null:
            return value
    var p := _player()
    if p != null:
        return p.get(wanted)
    return null


func _num(value) -> float:
    if value == null:
        return 0.0
    if value is Vector3 or value is String:
        return 0.0
    return float(value)


func _notes() -> float:
    return _num(_var("纸条数"))


func _state() -> String:
    var value = _var("ai_state")
    if value != null and str(value) != "":
        return str(value)
    return "<none>"


func _player() -> Node3D:
    var s := _scene()
    if s == null:
        return null
    for node in s.find_children("*", "CharacterBody3D", true, false):
        if String(node.name).ends_with("玩家"):
            return node as Node3D
    return null


func _find_enemy() -> Node3D:
    """怪一关一个名字(走廊怪物 / 音乐教室怪物,老版本叫怪物):
    先按候选名找,兜底扫场景里的 CharacterBody3D 并排除玩家。"""
    var s := _scene()
    if s == null:
        return null
    for wanted in ["走廊怪物", "音乐教室怪物", "怪物"]:
        var hit := s.find_child(wanted, true, false)
        if hit is Node3D:
            return hit as Node3D
    for node in s.find_children("*", "CharacterBody3D", true, false):
        if String(node.name).ends_with("玩家"):
            continue
        return node as Node3D
    return null


func _stage_dist(enemy: Node3D, stage: Node3D) -> float:
    if enemy == null or stage == null:
        return -1.0
    return enemy.global_position.distance_to(stage.global_position)


func _camera() -> Camera3D:
    return get_viewport().get_camera_3d()


func _camera_name() -> String:
    var cam := _camera()
    if cam == null:
        return "<null>"
    return String(cam.name)


func _find_button(text: String) -> Button:
    var scopes: Array = []
    var s := _scene()
    if s != null:
        scopes.append(s)
    scopes.append(get_tree().root)
    for scope in scopes:
        for node in (scope as Node).find_children("*", "Button", true, false):
            if node is Button and String((node as Button).text) == text:
                return node
    return null


func _find_retry_like() -> Button:
    for needle in ["重试", "再试", "继续", "检查点", "再来", "复活", "安全屋"]:
        var hit := _find_button_like(needle)
        if hit != null:
            return hit
    return null


func _find_button_like(needle: String) -> Button:
    var s := _scene()
    if s == null:
        return null
    for node in s.find_children("*", "Button", true, false):
        if node is Button and needle in String((node as Button).text):
            return node
    return null


func _button_texts() -> String:
    var s := _scene()
    var names: Array = []
    if s != null:
        for node in s.find_children("*", "Button", true, false):
            names.append(String((node as Button).text))
    return "[" + ", ".join(names) + "]"


func _has_label(fragment: String) -> bool:
    var s := _scene()
    if s == null:
        return false
    for node in s.find_children("*", "Label", true, false):
        if node is Label and fragment in String((node as Label).text):
            return true
    return false


func _visible_controls() -> int:
    var s := _scene()
    if s == null:
        return 0
    var count := 0
    for node in s.find_children("*", "Control", true, false):
        var control := node as Control
        if control != null and control.is_visible_in_tree():
            count += 1
    return count


func _capsule_height(body: Node3D) -> float:
    if body == null:
        return 0.0
    for node in body.find_children("*", "CollisionShape3D", true, false):
        var shape := (node as CollisionShape3D).shape
        if shape is CapsuleShape3D:
            return (shape as CapsuleShape3D).height
    return 0.0


func _panel_visible() -> bool:
    var panel = _var("_gk_choice_panel")
    if panel == null:
        return false
    if panel is CanvasItem:
        return (panel as CanvasItem).visible
    return true


# ---- 断言 / 拍照 ----------------------------------------------------
func _check(ok: bool, label: String, detail: String = "") -> bool:
    if not ok:
        _fails += 1
        print("PLAY_ASSERT_FAIL ", label, " ", detail)
    return ok


func _assert_fail(label: String, detail: String = "") -> void:
    _check(false, label, detail)


func _shot(name: String) -> void:
    await RenderingServer.frame_post_draw
    var image := get_viewport().get_texture().get_image()
    if image == null:
        _assert_fail("shot_null", name)
        return
    var path := OUT_DIR + "/" + name + ".png"
    var err := image.save_png(path)
    if err == OK:
        _shots += 1
    else:
        _assert_fail("shot_save", name + " err=" + str(err))
    print("PLAY_SHOT ", name, " err=", err, " size=", image.get_width(), "x", image.get_height())


func _quit(code: int) -> void:
    if _stopped:
        return
    _stopped = true
    if code == 0 and _fails > 0:
        code = 1
    # 不管红绿都打一行汇总,方便一眼看总量;红的时候再多打一行 PLAY_FAILED
    print("PLAY_SUMMARY fail=", _fails, " shots=", _shots, " sec=", snappedf(_now(), 0.1), " code=", code)
    if code != 0:
        print("PLAY_FAILED fail=", _fails, " shots=", _shots, " sec=", snappedf(_now(), 0.1))
    get_tree().quit(code)


# ---- 时间 / 等待 ----------------------------------------------------
func _wait(seconds: float) -> void:
    # 暂停(paused=true)时 physics_frame 指望不上;create_timer 的第 2 个参数
    # process_always=true 让它在暂停里也照常走,第 4 个忽略 time_scale。
    await get_tree().create_timer(seconds, true, false, true).timeout


func _wait_physics(seconds: float) -> void:
    """按物理步等 —— Area3D 的 overlap / body_entered 只在物理步里更新。
    ⚠ 但 paused=true 时物理根本不推进,那这时候等物理帧没有意义,退回墙钟。"""
    if get_tree().paused:
        await _wait(seconds)
        return
    var until := Time.get_ticks_msec() + int(seconds * 1000.0)
    while Time.get_ticks_msec() < until:
        await get_tree().physics_frame


func _shape_y(body: Node3D) -> float:
    if body == null:
        return -1.0
    for found in body.find_children("*", "CollisionShape3D", true, false):
        return (found as Node3D).global_position.y
    return -1.0


func _door_has_body(area: Node, body: Node) -> bool:
    if area is Area3D and body != null:
        return (area as Area3D).get_overlapping_bodies().has(body)
    return false


func _door_overlaps(area: Node) -> Array:
    var paths: Array = []
    if area is Area3D:
        for found in (area as Area3D).get_overlapping_bodies():
            paths.append(str(found.get_path()))
    return paths


func _until(cond: Callable, seconds: float) -> bool:
    var until := Time.get_ticks_msec() + int(seconds * 1000.0)
    while Time.get_ticks_msec() < until:
        if cond.call():
            return true
        await get_tree().process_frame
    return bool(cond.call())


func _until_scene(wanted: String, seconds: float) -> bool:
    return await _until(func(): return _scene_name() == wanted, seconds)


# ---- 输入 -----------------------------------------------------------
func _key(code: int, pressed: bool) -> void:
    var event := InputEventKey.new()
    event.keycode = code
    event.physical_keycode = code
    event.pressed = pressed
    Input.parse_input_event(event)


func _hold(code: int, seconds: float) -> void:
    _key(code, true)
    await _wait(seconds)
    _key(code, false)


func _hold_pair(first: int, second: int, seconds: float) -> float:
    var best := 0.0
    _key(first, true)
    if second != first:
        _key(second, true)
    var until := Time.get_ticks_msec() + int(seconds * 1000.0)
    while Time.get_ticks_msec() < until:
        var body := _player() as CharacterBody3D
        if body != null:
            best = maxf(best, Vector2(body.velocity.x, body.velocity.z).length())
        await get_tree().process_frame
    if second != first:
        _key(second, false)
    _key(first, false)
    return best


func _hold_speed(code: int, seconds: float) -> float:
    return await _hold_pair(code, code, seconds)


func _press(button: Button, label: String) -> void:
    print("PLAY_PRESS ", label)
    if button == null:
        _assert_fail("press_missing", label)
        return
    var center := button.get_global_rect().get_center()
    var motion := InputEventMouseMotion.new()
    motion.position = center
    motion.global_position = center
    Input.parse_input_event(motion)
    var down := InputEventMouseButton.new()
    down.button_index = MOUSE_BUTTON_LEFT
    down.pressed = true
    down.position = center
    down.global_position = center
    Input.parse_input_event(down)
    var up := InputEventMouseButton.new()
    up.button_index = MOUSE_BUTTON_LEFT
    up.pressed = false
    up.position = center
    up.global_position = center
    Input.parse_input_event(up)
    await _wait(0.45)


func _press_until(button: Button, label: String, cond: Callable, seconds: float) -> bool:
    await _press(button, label)
    if await _until(cond, seconds):
        return true
    if button == null:
        return false
    # 合成鼠标事件进不了 GUI(世界冻结时尤其明显)就退回直接发 pressed 信号。
    # 留一行 PLAY_CLICK_FALLBACK 供人看,不当失败 —— 只是"点了没反应"的证据。
    print("PLAY_CLICK_FALLBACK ", label)
    button.pressed.emit()
    return await _until(cond, seconds)


func _teleport(area_name: String) -> void:
    print("PLAY_TELEPORT ", area_name)
    var area := _find(area_name)
    var body := _player()
    if area == null:
        _assert_fail("area_missing", area_name)
        return
    if body == null:
        _assert_fail("player_missing", "scene=" + _scene_name())
        return
    body.global_position = (area as Node3D).global_position + Vector3(0.0, 1.0, 0.0)
    await _wait(0.5)


# ---- M0 新功能探测(缺了就 PLAY_MISSING,绝不崩)---------------------
func _m0_movement() -> void:
    _step = "m0_movement"
    var body := _player()
    if body == null:
        print("PLAY_MISSING crouch_walk")
        print("PLAY_MISSING run")
        return
    var cam_before := 0.0
    if _camera() != null:
        cam_before = _camera().global_position.y
    var shape_before := _capsule_height(body)
    await _hold(KEY_CTRL, 0.8)
    await _wait(0.15)
    var cam_after := cam_before
    if _camera() != null:
        cam_after = _camera().global_position.y
    var shape_after := _capsule_height(body)
    var cam_drop := cam_before - cam_after
    var shape_drop := shape_before - shape_after
    if cam_drop > 0.15 or (shape_before > 0.0 and shape_after > 0.0 and shape_drop > 0.15):
        print("PLAY_M0 crouch=1 cam_drop=", snappedf(cam_drop, 0.01), " shape_drop=", snappedf(shape_drop, 0.01))
    else:
        print("PLAY_MISSING crouch_walk cam_drop=", snappedf(cam_drop, 0.01), " shape_drop=", snappedf(shape_drop, 0.01))
    var walk := await _hold_speed(KEY_W, 0.9)
    await _wait(0.2)
    var run := await _hold_pair(KEY_SHIFT, KEY_W, 0.9)
    await _wait(0.2)
    if walk > 0.3 and run > walk * 1.15:
        print("PLAY_M0 run=1 walk=", snappedf(walk, 0.01), " run_speed=", snappedf(run, 0.01))
    else:
        print("PLAY_MISSING run walk=", snappedf(walk, 0.01), " run_speed=", snappedf(run, 0.01))


func _press_e() -> void:
    _key(KEY_E, true)
    await _wait(0.12)
    _key(KEY_E, false)
    await _wait(0.35)


func _take_item(tag: String, var_name: String) -> String:
    """走到交互物上:新版要先按 E(交互目标),旧版走进去就自动拿。返回 auto/key/gone/none。"""
    if not await _ensure_running("item:" + tag):
        return "none"
    print("PLAY_TELEPORT ", tag)
    var before := _num(_var(var_name))
    var node := _find(tag)
    var body := _player()
    if node == null:
        return "gone"
    if body == null:
        _assert_fail("player_missing", "scene=" + _scene_name())
        return "none"
    var spot := (node as Node3D).global_position + Vector3(0.0, 1.0, 0.0)
    body.global_position = spot
    await _wait(0.5)
    if node is Area3D:
        var item_frames := -1
        for i in range(30):
            await get_tree().physics_frame
            if (node as Area3D).get_overlapping_bodies().has(body) or str(_var("交互目标")) == tag:
                item_frames = i + 1
                break
        print("PLAY_ITEM_SETTLE name=", tag, " frames=", item_frames,
              " overlaps=", _door_overlaps(node), " target=", str(_var("交互目标")))
    if _num(_var(var_name)) > before:
        return "auto"
    for i in range(3):
        await _press_e()
        if _num(_var(var_name)) > before:
            return "key"
    # 走开再回来(现实中"再走进去一次"),让触发区重新报进场
    body.global_position = spot + Vector3(3.0, 0.0, 0.0)
    await _wait(0.4)
    body.global_position = spot
    await _wait(0.5)
    for i in range(2):
        await _press_e()
        if _num(_var(var_name)) > before:
            return "key"
    return "none"


func _take_note(tag: String) -> String:
    return await _take_item(tag, "纸条数")


func _ensure_running(step: String) -> bool:
    """通用前置条件:任何关卡、任何时刻 paused=true 都意味着物理停了 ——
    Area3D 的 overlap / body_entered 一起冻住,交互会全部失灵。先解冻再谈别的。"""
    if not get_tree().paused:
        return true
    print("PLAY_PAUSED step=", step, " paused=true scene=", _scene_name(), " buttons=", _button_texts())
    for i in range(3):
        var btn := _find_button("回到安全屋")
        if btn == null:
            btn = _find_retry_like()
        if btn == null:
            break
        print("PLAY_PAUSED step=", step, " press=", String(btn.text))
        btn.pressed.emit()
        if await _until(func(): return not get_tree().paused, 8.0):
            print("PLAY_PAUSED step=", step, " resumed=true scene=", _scene_name())
            return true
    print("PLAY_ASSERT_FAIL paused_stuck step=", step, " scene=", _scene_name())
    _fails += 1
    return false


func _ensure_not_hiding() -> void:
    """这一关躲着时 E 只能"出来"(契约);所以走门前先把躲藏态退干净。"""
    if _var("躲藏中") == null:
        return
    for i in range(4):
        if not bool(_num(_var("躲藏中"))):
            return
        print("PLAY_DOOR_UNHIDE step=", i, " hiding=", bool(_num(_var("躲藏中"))))
        await _press_e()
        await _wait_physics(0.4)
    print("PLAY_ASSERT_FAIL door_still_hiding hiding=", bool(_num(_var("躲藏中"))))
    _fails += 1


func _door(tag: String) -> bool:
    """走到门口:新版积满纸条要按 E 才走,旧版走进去就切场景。返回是否切了场景。"""
    print("PLAY_TELEPORT ", tag)
    var area := _find(tag)
    var body := _player()
    if area == null or body == null:
        _assert_fail("door_target_missing", tag)
        return false
    if not await _ensure_running("door:" + tag):
        return false
    await _ensure_not_hiding()
    var start := _scene_name()
    print("PLAY_DOOR_BODY name=", tag, " paused=", get_tree().paused, " path=", str(body.get_path()),
          " is_char_body=", body is CharacterBody3D, " type=", body.get_class(),
          " pos=", body.global_position, " scene=", _scene_name())
    var base := (area as Node3D).global_position
    var last_target := ""
    var matched := false
    # ① builder 的复现:传送当帧 overlaps 连地面都没有,要 +2~3 个物理帧玩家才出现、
    #    交互目标 才被 body_entered 写成门名。所以先站到板面上,轮询到真的重叠再按 E。
    body.global_position = base + Vector3(0, 0.1, 0)
    # 以"交互目标 变成门名"为按 E 的前置条件(builder 审计:站在帘子前 target=躲藏1,
    # 那一帧按 E 会作用在躲藏1 上 = 又躲回去,人被挪走)。最多等 90 帧。
    var frames := -1
    var reached := false
    for i in range(90):
        await get_tree().physics_frame
        last_target = str(_var("交互目标"))
        var has_body := _door_has_body(area, body)
        if i == 0:
            var door_conn := (area as Area3D).get_signal_connection_list("body_entered").size()
            var ref_conn := -1
            var ref_area := _find("病历2") as Area3D
            if ref_area != null:
                ref_conn = ref_area.get_signal_connection_list("body_entered").size()
            print("PLAY_DOOR_WIRING name=", tag, " door_body_entered_conns=", door_conn,
                  " ref_病历2_conns=", ref_conn,
                  " door_exit_conns=", (area as Area3D).get_signal_connection_list("body_exited").size())
        if i < 5 or i % 10 == 0:
            print("PLAY_DOOR_TARGET name=", tag, " wait i=", i, " target=", last_target,
                  " has_body=", has_body, " overlaps=", _door_overlaps(area))
        if last_target == tag or has_body:
            frames = i + 1
            reached = true
            print("PLAY_DOOR_TARGET name=", tag, " wait i=", i, " target=", last_target,
                  " has_body=", has_body, " ready=true frames=", frames)
            break
    if reached:
        matched = true
        await _press_e()
        print("PLAY_DOOR_AFTER name=", tag, " target=", str(_var("交互目标")),
              " scene=", _scene_name(), " paused=", get_tree().paused,
              " subtitle=", _has_label("侧门") or _has_label("还差") or _has_label("锁"))
        if await _until(func(): return _scene_name() != start, 1.5):
            return true
    else:
        _assert_fail("door_target", "name=" + tag + " last_target=" + last_target + " frames=90")
    # ② 兜底:有的图触发区很高或很低,扫几个站位高度
    # 触发板有高有矮:有的是一整块立体区,有的就是地上一块 20 厘米的板(医务室侧门),
    # 所以扫几个站位高度 —— 先按老的 +1 米,再站到板面上。
    var deadline := Time.get_ticks_msec() + 30000
    for offset in [Vector3(0, 1.0, 0), Vector3(0, 0.3, 0), Vector3(0, 0.1, 0)]:
        if Time.get_ticks_msec() > deadline:
            break
        body.global_position = base + offset
        # 等物理步:overlap 与 body_entered 都在物理步里更新
        await _wait_physics(0.4)
        if await _until(func(): return _scene_name() != start, 0.5):
            return true
        last_target = str(_var("交互目标"))
        if last_target == tag or _door_has_body(area, body):
            matched = true
        await _press_e()
        if await _until(func(): return _scene_name() != start, 0.9):
            return true
    # 老图纸 / 没有『交互目标』这套机制的关:站到板面上再按一次 E
    body.global_position = base + Vector3(0, 0.1, 0)
    await _wait_physics(0.3)
    await _press_e()
    if await _until(func(): return _scene_name() != start, 1.2):
        return true
    body.global_position = base + Vector3(0, 0.1, 0)
    for frame in range(18):
        await get_tree().physics_frame
        if frame in [0, 9, 17]:
            print("PLAY_DOOR_TRACE i=", frame, " pos=", body.global_position,
                  " overlaps=", _door_overlaps(area),
                  " has_player=", _door_has_body(area, body),
                  " target=", str(_var("交互目标")))
    var overlaps: Array = _door_overlaps(area)
    print("PLAY_DOOR_NOTE name=", tag, " scene=", _scene_name(), " target=", last_target,
          " matched=", matched, " pos=", body.global_position,
          " monitoring=", (area as Area3D).monitoring if area is Area3D else "n/a",
          " mask=", (area as Area3D).collision_mask if area is Area3D else "n/a",
          " overlaps=", overlaps)
    return false


func _m0_log_panel() -> void:
    _step = "m0_log"
    var before := _visible_controls()
    _key(KEY_J, true)
    await _wait(0.1)
    _key(KEY_J, false)
    await _wait(0.5)
    var open := _visible_controls()
    if open > before:
        print("PLAY_M0 log_panel=1 controls=", before, "->", open)
        _key(KEY_J, true)
        await _wait(0.1)
        _key(KEY_J, false)
        await _wait(0.3)
    else:
        print("PLAY_MISSING log_panel controls=", before, "->", open)


func _replay_room() -> bool:
    """被抓之后从标题重开:教室三张纸条再捡一遍,过门口回走廊。"""
    _step = "replay_room"
    for tag in ["纸条1", "纸条2", "纸条3"]:
        var mode := await _take_note(tag)
        print("PLAY_REPLAY note=", tag, " mode=", mode, " notes=", snappedf(_notes(), 0.01))
    await _door("去走廊")
    if not await _until_scene("走廊", 14.0):
        await _door("去走廊")
    var back := await _until_scene("走廊", 14.0)
    print("PLAY_REPLAY_ROOM notes=", snappedf(_notes(), 0.01), " scene=", _scene_name())
    return back


# ---- 第 3 章 音乐教室(没落地就降级)--------------------------------
func _ch3_answer() -> String:
    """按契约先读宿主的"标准答案";读不到再从界面文字里抠 4 位数字;都没有就交给候选表。"""
    for name in ["录音机答案", "正确输入", "正确顺序", "乐谱顺序", "录音机正确", "答案"]:
        var value = _var(name)
        if value != null and str(value) != "":
            return str(value)
    var regex := RegEx.new()
    regex.compile("[1-4]{4}")
    var s := _scene()
    if s != null:
        for node in s.find_children("*", "Label", true, false):
            var hit := regex.search(String((node as Label).text))
            if hit != null:
                return hit.get_string()
    return ""


func _frame_bytes() -> PackedByteArray:
    await RenderingServer.frame_post_draw
    var image := get_viewport().get_texture().get_image()
    if image == null:
        return PackedByteArray()
    image.resize(96, 54, Image.INTERPOLATE_BILINEAR)
    return image.get_data()


func _tap(code: int) -> void:
    _key(code, true)
    await _wait(0.1)
    _key(code, false)
    await _wait(0.12)


func _ch3_wrong() -> void:
    _step = "ch3_wrong"
    var before := _num(_var("录音机错次"))
    var steady_a := await _frame_bytes()
    await _wait(0.3)
    var steady_b := await _frame_bytes()
    var steady := steady_a != steady_b
    var frame_a := await _frame_bytes()
    # 故意按错:整整 4 位(少一位录音机不评估,残留前缀还会把后面的正确序列顶歪)
    for code in [KEY_3, KEY_3, KEY_3, KEY_3]:
        await _tap(code)
    await _wait(0.15)
    var frame_b := await _frame_bytes()
    var after := _num(_var("录音机错次"))
    var hint := ""
    for needle in ["错", "不对", "警报", "再来", "重来", "试试", "重新"]:
        if _has_label(needle):
            hint = needle
            break
    print("PLAY_CH3_WRONG wrong=", snappedf(after, 0.01), " alarm=", frame_a != frame_b,
          " steady=", steady, " hint=", hint, " input=", str(_var("录音机输入")),
          " playing=", snappedf(_num(_var("录音机放音")), 0.01))
    _check(after > before, "ch3_wrong_counter", "wrong=" + str(snappedf(after, 0.01)))
    await _shot("14_ch3_wrong")


func _ch3_flush() -> void:
    """录音机按满 4 位才评估;不足 4 位的残留会把下一次输入顶歪,先补齐让它自己清掉。"""
    for i in range(4):
        if str(_var("录音机输入")) == "":
            return
        await _tap(KEY_1)


func _ch3_play() -> void:
    _step = "ch3_play"
    var enemy := _find_enemy()
    var stage := _find("舞台") as Node3D
    var stage_before := _stage_dist(enemy, stage)
    var drift := 0.0
    if stage_before >= 0.0:
        await _wait(0.6)
        drift = _stage_dist(enemy, stage) - stage_before
    var answer := _ch3_answer()
    var candidates: Array = []
    if answer != "":
        candidates.append(answer)
    for extra in ["3142", "1234", "4321", "1324", "2413", "4231"]:
        if not candidates.has(extra):
            candidates.append(extra)
    var used := ""
    var played := false
    await _ch3_flush()
    for candidate in candidates:
        await _ch3_flush()
        var text := String(candidate)
        for i in text.length():
            await _tap(KEY_1 + (int(text.substr(i, 1)) - 1))
        await _wait(0.6)
        if bool(_var("录音机放音")):
            played = true
            used = text
            break
    var stage_after := stage_before
    if stage_before >= 0.0:
        var until := Time.get_ticks_msec() + 3000
        while Time.get_ticks_msec() < until:
            await get_tree().process_frame
            stage_after = _stage_dist(enemy, stage)
            if absf(stage_after - stage_before) > 0.5:
                break
    var decoy := 0.0
    if stage_before >= 0.0:
        decoy = stage_after - stage_before
    var tries := 0
    if used != "":
        tries = candidates.find(used) + 1
    print("PLAY_CH3_PLAY input=", used, " decoy=", snappedf(decoy, 0.01), " enemy=", enemy != null,
          " name=", "" if enemy == null else String(enemy.name), " state=", _state(),
          " tries=", tries, " answer=", answer, " drift=", snappedf(drift, 0.01),
          " stage=", snappedf(stage_before, 0.01), "->", snappedf(stage_after, 0.01))
    await _shot("15_ch3_play")
    if not played:
        print("PLAY_MISSING ch3_play answer=", answer,
              " wrong=", snappedf(_num(_var("录音机错次")), 0.01))


func _ch3_tape() -> void:
    _step = "ch3_tape"
    var mode := await _take_item("磁带", "磁带")
    var tape := _num(_var("磁带"))
    print("PLAY_CH3_TAPE tape=", snappedf(tape, 0.01), " mode=", mode)
    await _shot("16_ch3_tape")
    if mode == "gone" and tape < 0.5:
        print("PLAY_MISSING ch3_tape reason=no_node")


func _ch3_or_skip() -> bool:
    """走廊 -> 音乐教室 -> 楼梯间。这一关还没落地就 PLAY_SKIP,退回去走老路。"""
    _step = "ch3"
    var reason := ""
    if not ResourceLoader.exists("res://scenes/音乐教室.tscn"):
        reason = "no_scene"
    elif _find("去音乐教室") == null:
        reason = "no_door"
    if reason != "":
        print("PLAY_SKIP ch3 reason=", reason, " scene=", _scene_name(),
              " door=", _find("去音乐教室") != null)
        return false
    await _door("去音乐教室")
    if not await _until_scene("音乐教室", 16.0):
        print("PLAY_SKIP ch3 reason=enter_failed scene=", _scene_name())
        return false
    print("PLAY_CH3_ENTER scene=", _scene_name())
    await _wait(0.8)
    await _shot("11_ch3_enter")
    # ① 蹲走课(本轮验收点):从出生点按住 LeftCtrl 摸到 乐谱2,被抓就是失败
    await _ch3_crouch()
    # 另外两张照旧瞬移过去读
    for tag in ["乐谱1", "乐谱3"]:
        var mode := await _take_item(tag, "乐谱数")
        print("PLAY_CH3_SCORES scores=", snappedf(_num(_var("乐谱数")), 0.01), " tag=", tag, " mode=", mode)
    _check(absf(_num(_var("乐谱数")) - 3.0) < 0.01, "ch3_scores",
           "scores=" + str(snappedf(_num(_var("乐谱数")), 0.01)))
    await _shot("13_ch3_scores")
    await _ch3_wrong()
    await _ch3_play()
    await _ch3_tape()
    # 出口:医务室落地后叫『去医务室』,没落地还叫『去楼梯间』
    await _door_any(["去医务室", "去楼梯间"])
    var out := await _until(func(): return _scene_name() == "医务室" or _scene_name() == "楼梯间", 16.0)
    print("PLAY_CH3_EXIT scene=", _scene_name(), " ok=", out)
    _check(out, "ch3_exit", "scene=" + _scene_name())
    return out


# ---- 第 3 章的蹲走课 / 第 4 章 医务室 ------------------------------
func _aim_at(target: Vector3) -> void:
    """让角色朝目标站(角色朝向带着摇臂相机,所以 W 就朝这个方向走)。"""
    var body := _player()
    if body == null:
        return
    var flat := Vector3(target.x, body.global_position.y, target.z)
    if flat.distance_to(body.global_position) < 0.1:
        return
    body.look_at(flat, Vector3.UP)


func _crouch_walk_to(node_name: String, budget_sec: float) -> bool:
    """按住 LeftCtrl 一路走到目标交互物跟前;卡住了就侧向挪一段再接着走。"""
    var node := _find(node_name) as Node3D
    var body := _player()
    if node == null:
        _assert_fail("crouch_target_missing", node_name)
        return false
    if body == null:
        _assert_fail("player_missing", "scene=" + _scene_name())
        return false
    var last_dist := body.global_position.distance_to(node.global_position)
    var last_progress := Time.get_ticks_msec()
    var side := 1.0
    var reached := false
    var target_name := node_name
    _key(KEY_CTRL, true)
    await _wait(0.3)
    _key(KEY_W, true)
    var until := Time.get_ticks_msec() + int(budget_sec * 1000.0)
    while Time.get_ticks_msec() < until:
        var dist := body.global_position.distance_to(node.global_position)
        # 走进触发区后游戏会把『交互目标』设成这东西的名字 —— 这是最准的"够着了"信号
        if str(_var("交互目标")) == target_name or dist < 1.0:
            reached = true
            break
        if get_tree().paused:
            break
        if dist < last_dist - 0.12:
            last_dist = dist
            last_progress = Time.get_ticks_msec()
        elif Time.get_ticks_msec() - last_progress > 1000:
            # 卡住了:松开 W,朝侧面挪半秒,再朝目标走
            _key(KEY_W, false)
            _aim_at(body.global_position + Vector3(side * 3.0, 0.0, 0.0))
            _key(KEY_W, true)
            await _wait(0.5)
            side = -side
            last_dist = body.global_position.distance_to(node.global_position)
            last_progress = Time.get_ticks_msec()
        _aim_at(node.global_position)
        await get_tree().process_frame
    _key(KEY_W, false)
    _key(KEY_CTRL, false)
    await _wait(0.3)
    return reached


func _ch3_crouch() -> void:
    _step = "ch3_crouch"
    var before := _num(_var("乐谱数"))
    var reached := await _crouch_walk_to("乐谱2", 20.0)
    var caught := get_tree().paused
    var got := _num(_var("乐谱数")) - before
    if reached and not caught:
        for i in range(3):
            await _press_e()
            await _wait(0.35)
            got = _num(_var("乐谱数")) - before
            if got > 0.5:
                break
            var node := _find("乐谱2") as Node3D
            var body := _player()
            if node != null and body != null:
                var step: Vector3 = node.global_position - body.global_position
                step.y = 0.0
                if step.length() > 0.05:
                    body.global_position = body.global_position + step.normalized() * 0.5
                    await _wait(0.25)
    got = _num(_var("乐谱数")) - before
    print("PLAY_CH3_CROUCH got=", snappedf(got, 0.01), " caught=", caught, " reached=", reached,
          " scores=", snappedf(_num(_var("乐谱数")), 0.01), " scene=", _scene_name())
    await _shot("12_ch3_crouch")
    # 这条是本轮验收点:不许降级通过
    _check(got >= 0.99, "ch3_crouch_got", "got=" + str(snappedf(got, 0.01)) + " reached=" + str(reached))
    _check(not caught, "ch3_crouch_caught", "蹲走途中被抓(paused=true)")


func _battery() -> float:
    return _num(_var("手电电量"))


func _player_visible() -> bool:
    var body := _player()
    if body == null:
        return true
    if not body.visible:
        return false
    var model := body.find_child("Model", true, false)
    if model is Node3D:
        return (model as Node3D).visible
    return true


func _door_any(names: Array) -> bool:
    """出口可能在改名前/改名后:挑一个场景里真有的门去走。"""
    for wanted in names:
        if _find(String(wanted)) != null:
            return await _door(String(wanted))
    _assert_fail("door_missing", str(names))
    return false


func _ch4_hide() -> void:
    _step = "ch4_hide"
    var before_hide := bool(_num(_var("躲藏中")))
    var mode := await _take_item("躲藏1", "躲藏中")
    var in_hide := bool(_num(_var("躲藏中")))
    var hidden := not _player_visible()
    print("PLAY_CH4_HIDE in=", bool(_num(_var("躲藏中"))), " cam=", _camera_name(),
          " hidden=", hidden, " mode=", mode)
    await _shot("19_ch4_hide")
    _check(bool(_num(_var("躲藏中"))), "ch4_hide_in", "躲藏中=" + str(snappedf(_num(_var("躲藏中")), 0.01)))
    _check(_camera_name() == "医务室躲藏机位", "ch4_hide_cam", "cam=" + _camera_name())
    _check(hidden, "ch4_hide_hidden", "player_visible=" + str(_player_visible()))
    await _press_e()
    var out := await _until(func(): return not bool(_num(_var("躲藏中"))), 3.0)
    print("PLAY_CH4_HIDE out=", out, " cam=", _camera_name(), " hidden=", not _player_visible(),
          " pos=", "<null>" if _player() == null else _player().global_position)
    print("PLAY_CH4_HIDE_TOGGLE hiding=", before_hide, "->", in_hide, "->", bool(_num(_var("躲藏中"))),
          " cam=", _camera_name())
    _check(out, "ch4_hide_out", "躲藏中=" + str(snappedf(_num(_var("躲藏中")), 0.01)))
    _check(_player_visible(), "ch4_hide_restore", "player_visible=" + str(_player_visible()))
    # 这条是这一关的交互契约:躲着时 E 用来"出来",不是给门用的
    _check(not before_hide and in_hide and not bool(_num(_var("躲藏中"))), "ch4_hide_toggle",
           "hiding=" + str(before_hide) + "->" + str(in_hide) + "->" + str(bool(_num(_var("躲藏中")))))


func _ch4_or_skip() -> void:
    """第 4 章 医务室:没落地就 PLAY_SKIP,继续走楼梯间。"""
    _step = "ch4"
    var reason := ""
    if not ResourceLoader.exists("res://scenes/医务室.tscn"):
        reason = "no_scene"
    elif _find("去医务室") == null and _scene_name() != "医务室":
        reason = "no_door"
    if reason != "":
        # 缺这一关也要留一行,别因为"已经走到楼梯间了"就静悄悄跳过
        print("PLAY_SKIP ch4 reason=", reason, " scene=", _scene_name(),
              " door=", _find("去医务室") != null)
        return
    if _scene_name() == "楼梯间":
        print("PLAY_SKIP ch4 reason=already_past scene=", _scene_name())
        return
    var carry := _battery()
    if _scene_name() != "医务室":
        await _door("去医务室")
        if not await _until_scene("医务室", 16.0):
            print("PLAY_SKIP ch4 reason=enter_failed scene=", _scene_name())
            return
    var battery := _battery()
    print("PLAY_CH4_ENTER scene=", _scene_name(), " battery=", snappedf(battery, 0.1),
          " carry=", snappedf(carry, 0.1))
    await _wait(0.8)
    var later := _battery()
    print("PLAY_CH4_DRAIN from=", snappedf(battery, 0.1), " to=", snappedf(later, 0.1),
          " per_sec=", snappedf((battery - later) / 0.8, 0.01))
    await _shot("17_ch4_enter")
    # 关卡 _ready 里设 60;手电开着时按 1.5/秒掉,所以读到的是 60 往下一点点
    _check(battery <= 60.05 and battery >= 58.0, "ch4_battery_entry",
           "battery=" + str(snappedf(battery, 0.1)) + " 期望 60(掉电 1.5/秒)")

    # 实验 A(只观察、不按 E):刚进关、还没躲藏、还没拿病历,先站到侧门上量一次。
    # 用来二分"是躲藏/交互把身体弄坏了"还是"这块区本来就摸不到"。
    _step = "ch4_door_a"
    var door_a := _find("去楼梯间") as Area3D
    var body_a := _player()
    if door_a != null and body_a != null:
        body_a.global_position = door_a.global_position + Vector3(0, 0.1, 0)
        var frames_a := -1
        for i in range(30):
            await get_tree().physics_frame
            if _door_has_body(door_a, body_a) or str(_var("交互目标")) == "去楼梯间":
                frames_a = i + 1
                break
        print("PLAY_CH4_DOOR_A frames=", frames_a, " overlaps=", _door_overlaps(door_a),
              " target=", str(_var("交互目标")), " pos=", body_a.global_position,
              " body_y=", snappedf(body_a.global_position.y, 0.001), " shape_y=", snappedf(_shape_y(body_a), 0.001))

    _step = "ch4_battery"
    var before := _battery()
    var mode := await _take_item("电池1", "电池数")
    var after := _battery()
    print("PLAY_CH4_BATTERY before=", snappedf(before, 0.1), " after=", snappedf(after, 0.1),
          " mode=", mode, " batteries=", snappedf(_num(_var("电池数")), 0.01))
    await _shot("18_ch4_battery")
    _check(after - before > 35.0, "ch4_battery_gain",
           "before=" + str(snappedf(before, 0.1)) + " after=" + str(snappedf(after, 0.1)))

    await _ch4_hide()

    _step = "ch4_records"
    for i in range(3):
        var tag := "病历" + str(i + 1)
        var hit := await _take_item(tag, "病历数")
        print("PLAY_CH4_RECORDS n=", snappedf(_num(_var("病历数")), 0.01), " tag=", tag, " mode=", hit)
    await _shot("20_ch4_records")
    _check(absf(_num(_var("病历数")) - 3.0) < 0.01, "ch4_records",
           "n=" + str(snappedf(_num(_var("病历数")), 0.01)))

    _step = "ch4_door"
    await _door("去楼梯间")
    var out := await _until_scene("楼梯间", 16.0)
    print("PLAY_CH4_DOOR scene=", _scene_name(), " ok=", out)
    _check(out, "ch4_door", "scene=" + _scene_name())


# ---- 主流程 ---------------------------------------------------------
func _run() -> void:
    _step = "boot"
    if not await _until(func(): return _scene() != null, 20.0):
        _assert_fail("title_scene", "scene=<null>")
        _quit(1)
        return
    await _wait(1.0)

    # ---- 标题 -------------------------------------------------------
    _step = "title"
    print("PLAY_START scene=", _scene_name())
    print("PLAY_TITLE camera=", _camera_name(), " res=", get_viewport().get_visible_rect().size)
    var start := _find_button("开始游戏")
    _check(start != null, "title_start_button", "scene=" + _scene_name())
    await _shot("01_title")

    # ---- 教室 -------------------------------------------------------
    _step = "enter_room"
    var in_room := await _press_until(start, "开始游戏", func(): return _scene_name() == "教室", 10.0)
    print("PLAY_ROOM_ENTER scene=", _scene_name())
    if not _check(in_room, "enter_room", "scene=" + _scene_name()):
        _quit(1)
        return
    await _wait(1.0)
    await _shot("02_room_opening")
    _step = "room_camera"
    var settled := await _until(func(): return _camera_name() == "侧视机位", 14.0)
    var cam := _camera()
    var body := _player()
    if cam != null and body != null:
        print("PLAY_ROOM_OPENED camera=", cam.name, " fov=", snappedf(cam.fov, 0.1),
              " dist=", snappedf(cam.global_position.distance_to(body.global_position), 0.01))
    else:
        print("PLAY_ROOM_OPENED camera=", _camera_name(), " player=", body != null)
    _check(settled, "room_camera_settle", "camera=" + _camera_name())
    await _shot("03_room_thirdperson")
    if EXPECT_M0:
        await _m0_movement()
        if _scene_name() != "教室":
            _assert_fail("m0_movement_left_room", "scene=" + _scene_name())
            _quit(1)
            return

    _step = "note1"
    var mode1 := await _take_note("纸条1")
    print("PLAY_PICKUP tag=纸条1 mode=", mode1)
    if EXPECT_M0:
        if mode1 == "key":
            print("PLAY_M0 pickup_e=1 tag=纸条1 notes=", snappedf(_notes(), 0.01))
        else:
            print("PLAY_MISSING pickup_e tag=纸条1 mode=", mode1)
    _check(_notes() >= 0.99, "note1_taken", "notes=" + str(snappedf(_notes(), 0.01)))

    _step = "note2"
    var mode2 := await _take_note("纸条2")
    print("PLAY_PICKUP tag=纸条2 mode=", mode2)
    if EXPECT_M0:
        if mode2 == "key":
            print("PLAY_M0 pickup_e=1 tag=纸条2 notes=", snappedf(_notes(), 0.01))
        else:
            print("PLAY_MISSING pickup_e tag=纸条2 mode=", mode2)
    print("PLAY_ROOM_TWO notes=", snappedf(_notes(), 0.01))
    _check(absf(_notes() - 2.0) < 0.01, "room_two_notes", "notes=" + str(snappedf(_notes(), 0.01)))
    await _shot("04_room_two_notes")

    _step = "gate"
    var went_early := await _door("去走廊")
    var stayed := _scene_name() == "教室"
    print("PLAY_ROOM_GATE scene=", _scene_name(), " notes=", snappedf(_notes(), 0.01),
          " paused=", get_tree().paused, " hint=", _has_label("还差"))
    await _shot("05_room_gate")
    if not _check(stayed and not went_early, "gate_blocked", "scene=" + _scene_name()):
        _quit(1)
        return

    _step = "note3"
    var mode3 := await _take_note("纸条3")
    print("PLAY_PICKUP tag=纸条3 mode=", mode3)
    if EXPECT_M0:
        if mode3 == "key":
            print("PLAY_M0 pickup_e=1 tag=纸条3 notes=", snappedf(_notes(), 0.01))
        else:
            print("PLAY_MISSING pickup_e tag=纸条3 mode=", mode3)
    print("PLAY_ROOM_THREE notes=", snappedf(_notes(), 0.01))
    _check(absf(_notes() - 3.0) < 0.01, "room_three_notes", "notes=" + str(snappedf(_notes(), 0.01)))
    await _shot("06_room_three_notes")
    if EXPECT_M0:
        await _m0_log_panel()

    # ---- 走廊 -------------------------------------------------------
    _step = "to_corridor"
    await _door("去走廊")
    var in_corridor := await _until_scene("走廊", 14.0)
    if not _check(in_corridor, "enter_corridor", "scene=" + _scene_name()):
        _quit(1)
        return
    await _wait(0.5)
    await _until(func(): return _camera_name() != "走廊怪物特写", 14.0)
    print("PLAY_CORRIDOR_ENTER scene=", _scene_name(), " camera=", _camera_name())
    await _shot("07_corridor")
    var enemy := _find_enemy()
    print("PLAY_ENEMY exists=", enemy != null, " name=", "" if enemy == null else String(enemy.name),
          " state=", _state())
    _check(enemy != null, "enemy_exists", "scene=" + _scene_name())

    _step = "k_noise"
    await _hold(KEY_K, 0.6)
    var heard := await _until(func(): return _state() == "去查看", 7.0)
    if not heard and enemy != null:
        var walker := _player()
        if walker != null:
            walker.global_position = (enemy as Node3D).global_position + Vector3(0.0, 1.0, 6.0)
            await _wait(0.4)
            await _hold(KEY_K, 0.6)
            heard = await _until(func(): return _state() == "去查看", 7.0)
    print("PLAY_K action=true")
    print("PLAY_NOISE state=", _state(),
          " hear_at=", snappedf(_num(_var("ai_state_hear_at")), 0.001),
          " loud=", snappedf(_num(_var("ai_state_hear_loud")), 0.01),
          " pos=", _var("ai_state_hear_pos"))
    _check(heard, "noise_heard", "state=" + _state())
    await _shot("08a_noise")

    _step = "scare"
    await _teleport("惊吓区")
    var dropped := await _until(func(): return AudioServer.get_bus_volume_db(0) < -6.0, 6.0)
    await _shot("08_scare")
    print("PLAY_SCARE bus=", snappedf(AudioServer.get_bus_volume_db(0), 0.01))
    _check(dropped, "scare_bus", "bus=" + str(snappedf(AudioServer.get_bus_volume_db(0), 0.01)))

    _step = "caught"
    var caught := await _until(func(): return get_tree().paused, 12.0)
    if not caught and enemy != null:
        var walker2 := _player()
        if walker2 != null:
            walker2.global_position = (enemy as Node3D).global_position + Vector3(0.5, 1.0, 0.0)
            caught = await _until(func(): return get_tree().paused, 8.0)
    var retry := _find_button("重试本关")
    var back_home := _find_button("回到标题")
    if retry == null:
        retry = _find_retry_like()
    print("PLAY_BUTTONS ", _button_texts())
    print("PLAY_FAIL scene=", _scene_name(), " paused=", get_tree().paused,
          " retry=", retry != null, " back=", back_home != null)
    _check(caught, "caught_freeze", "paused=" + str(get_tree().paused))
    _check(retry != null or back_home != null, "fail_buttons",
           "retry=" + str(retry != null) + " back=" + str(back_home != null) + " buttons=" + _button_texts())
    await _shot("09_fail")

    var respawned := false
    if EXPECT_M0:
        respawned = await _until(func(): return not get_tree().paused and _scene_name() == "教室", 4.0)
        if respawned:
            print("PLAY_M0 respawn_safe_room scene=", _scene_name(), " notes=", snappedf(_notes(), 0.01))
            if _notes() >= 2.99:
                print("PLAY_M0 notes_kept=1 notes=", snappedf(_notes(), 0.01))
            else:
                print("PLAY_MISSING notes_kept notes=", snappedf(_notes(), 0.01))
            await _shot("09_respawn")
        else:
            var safe := _find_retry_like()
            if safe != null:
                print("PLAY_M0 respawn_safe_room=manual button=", String(safe.text),
                      " scene=", _scene_name(), " paused=", get_tree().paused)
            else:
                print("PLAY_MISSING respawn_safe_room scene=", _scene_name(), " paused=", get_tree().paused)

    if respawned:
        _step = "retry_after_respawn"
        print("PLAY_RETRY scene=", _scene_name(), " paused=", get_tree().paused)
        await _shot("10_retry")
        await _door("去走廊")
        if not _check(await _until_scene("走廊", 14.0), "enter_corridor_again", "scene=" + _scene_name()):
            _quit(1)
            return
        await _wait(0.5)
    else:
        _step = "retry"
        var resumed := false
        var via := "none"
        if retry != null:
            var label := String(retry.text)
            await _press_until(retry, label, func(): return not get_tree().paused, 14.0)
            via = "retry:" + label
            if _scene_name() == "走廊":
                resumed = true
            elif _scene_name() == "教室":
                # 『回到安全屋』式:人在教室里,把剩下的纸条捡齐再出门
                resumed = await _replay_room()
        if not resumed and _find_button("回到标题") != null:
            via = "title_replay"
            await _press_until(_find_button("回到标题"), "回到标题",
                               func(): return _scene_name() == "标题", 14.0)
            print("PLAY_FAIL_BACK scene=", _scene_name(), " paused=", get_tree().paused)
            await _wait(0.6)
            await _press_until(_find_button("开始游戏"), "开始游戏",
                               func(): return _scene_name() == "教室", 14.0)
            await _wait(0.8)
            resumed = await _replay_room()
        print("PLAY_RETRY scene=", _scene_name(), " paused=", get_tree().paused, " via=", via)
        _check(resumed, "retry_resume", "scene=" + _scene_name() + " paused=" + str(get_tree().paused) + " via=" + via)
        await _wait(1.2)
        await _shot("10_retry")
        # 复活之后失败遮罩该收干净:1.2 秒可能只是淡出中,再等 1.6 秒看一趟
        await _wait(1.6)
        print("PLAY_AFTER_RETRY scene=", _scene_name(), " paused=", get_tree().paused,
              " buttons=", _button_texts())
        await _shot("10b_after_retry")

    # ---- 第 3 章 音乐教室(没落地就降级)-----------------------------
    await _until(func(): return _camera_name() != "走廊怪物特写", 14.0)
    await _ch3_or_skip()
    await _ch4_or_skip()

    # ---- 楼梯间 -----------------------------------------------------
    _step = "to_stairs"
    if _scene_name() != "楼梯间":
        await _door_any(["去楼梯间", "去医务室"])
    var at_stairs := await _until_scene("楼梯间", 14.0)
    print("PLAY_STAIRS_ENTER scene=", _scene_name())
    if not _check(at_stairs, "enter_stairs", "scene=" + _scene_name()):
        _quit(1)
        return
    await _wait(0.9)
    await _shot("21_stairs")

    # ── 第 5 章(第 161 轮):进门 6 秒后断电,追兵启动,结局挪到天台 ──
    _step = "blackout"
    var light_before = _find("楼梯间应急灯1")
    var lit_before := light_before != null and bool(light_before.visible)
    print("PLAY_STAIRS_BEFORE lit=", lit_before)
    # 断电是进场 6 秒的定时器;这里等够(顺带把"灯灭 + 追兵"量下来)。
    await _wait(7.2)
    var light_after = _find("楼梯间应急灯1")
    var lit_after := light_after != null and bool(light_after.visible) \
        and (light_after as Light3D).light_energy > 0.001
    print("PLAY_STAIRS_BLACKOUT lit=", lit_after,
          " ai=", str(_var("ai_state")))
    _check(lit_before, "stairs_lit_before", "lit=" + str(lit_before))
    _check(not lit_after, "stairs_blackout", "lit=" + str(lit_after))
    _check(str(_var("ai_state")) == "追击", "stairs_chase",
           "ai=" + str(_var("ai_state")))
    await _shot("21b_blackout")

    _step = "door"
    await _door("楼梯间天台")
    var panel := await _until(func(): return _panel_visible(), 12.0)
    print("PLAY_CHOICE paused=", get_tree().paused, " panel=", panel)
    _check(panel, "choice_panel", "panel=" + str(panel))
    await _shot("22_choice")

    _step = "ending"
    await _hold(KEY_2, 0.15)
    var chosen := await _until(func(): return _num(_var("结局")) >= 1.0, 12.0)
    await _wait(1.2)
    print("PLAY_ENDING scene=", _scene_name(), " ended=", snappedf(_num(_var("结局")), 0.01),
          " paused=", get_tree().paused)
    _check(chosen, "ending_chosen", "ended=" + str(snappedf(_num(_var("结局")), 0.01)))
    await _shot("23_ending")

    # ---- 回标题 -----------------------------------------------------
    _step = "back_title"
    var back2 := _find_button("回到标题")
    _check(back2 != null, "ending_back_button", "scene=" + _scene_name())
    var home := await _press_until(back2, "回到标题", func(): return _scene_name() == "标题", 14.0)
    await _wait(1.0)
    print("PLAY_BACK_TITLE scene=", _scene_name(), " paused=", get_tree().paused,
          " camera=", _camera_name())
    _check(home, "back_title_scene", "scene=" + _scene_name())
    _check(not get_tree().paused, "back_title_unpaused", "paused=" + str(get_tree().paused))
    _check(_camera_name() == "标题机位", "back_title_camera", "camera=" + _camera_name())
    await _shot("24_back_title")

    # ---- 二周目 -----------------------------------------------------
    _step = "new_run"
    var start2 := _find_button("开始游戏")
    var again := await _press_until(start2, "开始游戏", func(): return _scene_name() == "教室", 14.0)
    await _wait(1.0)
    print("PLAY_NEW_RUN scene=", _scene_name(), " notes=", snappedf(_notes(), 0.01))
    _check(again, "new_run_enter", "scene=" + _scene_name())
    _check(absf(_notes()) < 0.01, "new_run_notes_zero", "notes=" + str(snappedf(_notes(), 0.01)))
    await _shot("25_new_run")

    if _fails == 0:
        print("PLAY_DONE")
    _quit(0)
'''


MARKER = ".play_horror_out"


def _foreign(out):
    """目录里已经有东西、又不是本探针写的 —— 一律当别人的证据,一个字节都不碰。"""
    if not out.is_dir():
        return False
    if (out / MARKER).exists():
        return False
    return any(item.is_file() for item in out.iterdir())


def _claim_out(requested, force):
    """挑输出目录:默认 .audit/horror_play_<今天>;那天已经被别人占了就退到 _full。"""
    if requested:
        out = Path(requested).resolve()
        if not force and _foreign(out):
            print(f"[停止] {out} 里已经有别的文件(不是本探针写的)。")
            print("       换 --out,或者加 --force 明确同意覆盖。")
            return None
        return out
    day = "horror_play_" + datetime.date.today().strftime("%Y%m%d")
    root = ROOT / ".audit"
    out = root / day
    suffix = 1
    while not force and _foreign(out):
        suffix += 1
        out = root / (day + "_full" if suffix == 2 else f"{day}_full{suffix}")
    return out


def _wipe(dist):
    """删掉上一趟的拷贝;有 Godot 还没退出时会占着目录,重试几次再说。"""
    for _ in range(5):
        try:
            if dist.exists():
                shutil.rmtree(dist)
            return True
        except OSError:
            time.sleep(1.0)
    print(f"[停止] 删不掉 {dist} —— 多半还有上一趟没退出的 Godot 占着它。")
    print("       先把那个 Godot 关掉(或结束进程),再重跑。")
    return False


def _verify_copy(src, dist):
    """生成器是边删边写的:拷到一半可能缺场景文件,跑起来就是"找不到 走廊.tscn"。"""
    missing = []
    for scene in sorted(src.glob("scenes/*.tscn")):
        target = dist / scene.relative_to(src)
        if not target.is_file() or target.stat().st_size == 0:
            missing.append(scene.name)
    return missing


def _prepare(dist, out_dir, expect_m0, limit_sec, size):
    """拷工程 -> 写探针 -> 挂 [autoload]。源工程一个字节都不动。"""
    if not SRC.is_dir():
        print(f"找不到示例工程 {SRC} —— 先跑 make_horror_demo.py")
        return False
    if not _wipe(dist):
        return False
    shutil.copytree(SRC, dist, ignore=shutil.ignore_patterns(".godot"))
    missing = _verify_copy(SRC, dist)
    if missing:
        print(f"⚠ 拷贝时源工程正在重新生成(缺 {missing}),等 3 秒重拷一次…")
        time.sleep(3.0)
        if not _wipe(dist):
            return False
        shutil.copytree(SRC, dist, ignore=shutil.ignore_patterns(".godot"))
        missing = _verify_copy(SRC, dist)
        if missing:
            print(f"[停止] 源工程还缺 {missing} —— 多半 builder 正在重新生成,等它跑完再试。")
            return False
    probe = (PROBE
             .replace("__OUT_DIR__", out_dir.as_posix())
             .replace("__EXPECT_M0__", "true" if expect_m0 else "false")
             .replace("__LIMIT_SEC__", str(float(limit_sec)))
             .replace("__WIN_W__", str(size[0]))
             .replace("__WIN_H__", str(size[1])))
    (dist / "play_probe.gd").write_text(probe, encoding="utf-8", newline="\n")
    project = dist / "project.godot"
    text = project.read_text(encoding="utf-8")
    entry = 'PlayProbe="*res://play_probe.gd"'
    if "[autoload]" in text:
        text = text.replace("[autoload]", "[autoload]\n\n" + entry, 1)
    elif "[input]" in text:
        text = text.replace("[input]", "[autoload]\n\n" + entry + "\n\n[input]", 1)
    else:
        text = text + "\n[autoload]\n\n" + entry + "\n"
    project.write_text(text, encoding="utf-8", newline="\n")
    return True


def _env():
    """user:// 指到 _dist 里,顺手清掉上一趟的存档 —— 每次跑都是干净的一周目。"""
    env = os.environ.copy()
    env["APPDATA"] = str(PROFILE)
    env["LOCALAPPDATA"] = str(PROFILE)
    for stale in PROFILE.rglob("gk_save*.json"):
        try:
            stale.unlink()
        except OSError:
            pass
    return env


def _import_assets(godot, dist, env):
    cmd = [godot, "--headless", "--path", str(dist), "--import"]
    print("$ " + " ".join(cmd))
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, timeout=600)
        raw = (done.stdout or "") + (done.stderr or "")
    except subprocess.TimeoutExpired:
        return "", "导入超时(600s)"
    if "non-existent resource" in raw:
        # 第一次导入偶尔漏一个 .glb(导入缓存没写全);真缺文件的话第二次照样报。
        print("    ⚠ 导入没齐(non-existent resource),重跑一次…")
        done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, timeout=600)
        raw = (done.stdout or "") + (done.stderr or "")
    return raw, ""


def _run_play(godot, dist, env, timeout):
    """带窗口跑(不加 --headless):真渲染,截图才有东西。"""
    cmd = [godot, "--path", str(dist)]
    print("$ " + " ".join(cmd))
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, timeout=timeout, cwd=str(dist))
        return (done.stdout or "") + (done.stderr or ""), done.returncode
    except subprocess.TimeoutExpired as exc:
        head, tail = exc.stdout or "", exc.stderr or ""
        if isinstance(head, bytes):
            head = head.decode("utf-8", "replace")
        if isinstance(tail, bytes):
            tail = tail.decode("utf-8", "replace")
        print(f"PLAY_TIMEOUT driver=1 timeout={timeout}s")
        return head + tail, None


def main():
    global SRC
    parser = argparse.ArgumentParser(description="把恐怖示例真的玩一遍并逐张截图")
    parser.add_argument("--godot", default=GODOT_DEFAULT)
    parser.add_argument("--project", default=str(SRC), help="源工程(只读;会被拷到 --dist)")
    parser.add_argument("--dist", default=str(DIST), help="拷贝出来的可写工程")
    parser.add_argument("--out", default=None, help="截图 + play.log 目录")
    parser.add_argument("--force", action="store_true", help="目录里已有别人的文件也照样清掉(危险)")
    parser.add_argument("--expect-m0", action="store_true",
                        help="顺带探 M0 新功能(蹲走/跑动/E 拾取/J 日志/回安全屋);缺了打 PLAY_MISSING 不算失败")
    parser.add_argument("--timeout", type=int, default=240, help="整局超时(秒)")
    parser.add_argument("--resolution", default="1280x720")
    args = parser.parse_args()

    SRC = Path(args.project).resolve()
    dist = Path(args.dist).resolve()
    width, height = (int(v) for v in args.resolution.lower().split("x"))

    if not Path(args.godot).is_file():
        print(f"找不到 Godot: {args.godot}")
        return 2
    out_dir = _claim_out(args.out, args.force)
    if out_dir is None:
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in list(out_dir.glob("*.png")) + [out_dir / "play.log", out_dir / "run_output.log"]:
        if old.exists():
            old.unlink()
    (out_dir / MARKER).write_text("play_horror_full\n", encoding="utf-8")

    print(f"源工程   {SRC}")
    print(f"拷贝到   {dist}")
    print(f"截图到   {out_dir}")
    print(f"M0 断言  {'开' if args.expect_m0 else '关'}    超时 {args.timeout}s    窗口 {width}x{height}")
    if not _prepare(dist, out_dir, args.expect_m0, max(args.timeout - 20, 30), (width, height)):
        return 2

    env = _env()
    imported, failure = _import_assets(args.godot, dist, env)
    if failure:
        print(f"准备失败: {failure}")
        return 2
    if "SCRIPT ERROR" in imported or "Parse Error" in imported:
        print(imported[-4000:])
        print("准备失败: 探针或示例有脚本错误(见上,原文在 _dist/horror_play)")
        return 2

    raw, code = _run_play(args.godot, dist, env, args.timeout)
    # 导入缓存偶尔漏一个 .glb(第九十五轮门禁撞过,这里也撞过):补导入再跑一趟。
    if "non-existent resource" in raw or "Failed loading scene" in raw:
        print("    ⚠ 导入没齐,补一次 --import 再跑…")
        _import_assets(args.godot, dist, env)
        raw, code = _run_play(args.godot, dist, env, args.timeout)
    (out_dir / "run_output.log").write_text(raw, encoding="utf-8")

    play_lines = [line.rstrip() for line in raw.splitlines() if line.startswith("PLAY")]
    for line in play_lines:
        print("  " + line)
    (out_dir / "play.log").write_text("\n".join(play_lines) + "\n", encoding="utf-8")

    shots = sorted(p.name for p in out_dir.glob("*.png"))
    print(f"\n截图 {len(shots)} 张: {', '.join(shots)}")
    print(f"Godot 退出码 {code}")

    problems = []
    if code is None:
        problems.append(f"超时({args.timeout}s 没跑完) —— 见 PLAY_TIMEOUT")
    elif code != 0:
        problems.append(f"Godot 退出码 {code}")
    if "PLAY_DONE" not in play_lines:
        problems.append("没出现 PLAY_DONE")
    problems.extend(line for line in play_lines if line.startswith("PLAY_ASSERT_FAIL"))
    for bad in ("SCRIPT ERROR", "Parse Error", "non-existent resource"):
        if bad in raw:
            hit = next(line for line in raw.splitlines() if bad in line)
            problems.append(f"Godot 报错: {hit.strip()[:200]}")
    if len(shots) < 10:
        problems.append(f"截图只有 {len(shots)} 张(<10)")

    missing = [line for line in play_lines if line.startswith("PLAY_MISSING")]
    if missing:
        print(f"\nM0 缺失 {len(missing)} 项(不算失败):")
        for line in missing:
            print("  " + line)
    fallbacks = [line for line in play_lines if line.startswith("PLAY_CLICK_FALLBACK")]
    if fallbacks:
        print(f"\n鼠标点击没进 GUI、退回发信号 {len(fallbacks)} 次:")
        for line in fallbacks:
            print("  " + line)

    if problems:
        print("\n[FAIL] 不通过:")
        for item in problems:
            print("  - " + item)
        return 3 if code is None else 1
    print(f"\n[OK] 全部通过: {len(shots)} 张截图 + play.log -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
