##########################################################################
# 由 BlenderGameKit 自动生成，请勿手动修改。
# 节点树: 玩家
# 修改请回到 Blender 里改节点图，然后重新导出。
##########################################################################

class_name PlayerController
extends CharacterBody2D

# ---- 输入动作名(Godot 输入映射里的动作)----
const ACTION_MOVE_LEFT := "move_left"
const ACTION_MOVE_RIGHT := "move_right"
const ACTION_MOVE_UP := "move_up"
const ACTION_MOVE_DOWN := "move_down"
const ACTION_JUMP := "jump"

# ---- 可在检查器里调的参数 ----
@export var ui_x: float = 16.0  # 界面_文本 · X
@export var ui_y: float = 16.0  # 界面_文本 · Y
@export var ui_x_2: float = 16.0  # 界面_文本 · X
@export var ui_y_2: float = 44.0  # 界面_文本 · Y
@export var ui_x_3: float = 16.0  # 界面_文本 · X
@export var ui_y_3: float = 78.0  # 界面_文本 · Y
@export var ui_x_4: float = 16.0  # 界面_按钮 · X
@export var ui_y_4: float = 112.0  # 界面_按钮 · Y
@export var move_speed: float = 260.0  # 移动_按方向 · 速度
@export var jump_force: float = 560.0  # 移动_跳跃 · 强度

# ---- 成员变量 ----
var _gk_save_data: Dictionary
var _gk_save_path: String
var best_score: Variant
var score: float
var _gk_ui_layer: CanvasLayer
var _gk_ui_root: Control
var _gk_ui_操作提示: Label
var _gk_ui_分数: Label
var _gk_ui_最高分: Label
var _gk_ui_重开按钮: Button
var _gk_follow_cam: Node
var _gk_follow_look: Node

func _ready() -> void:
    _gk_save_path = _gk_save_file("best")
    _gk_save_data = _gk_read_save(_gk_save_path)
    best_score = _gk_save_data.get("best_score", 0.0)
    score = 0.0
    _gk_ensure_ui(self)
    if _gk_ui_操作提示 == null:
        _gk_ui_操作提示 = Label.new()
        _gk_ui_操作提示.name = "操作提示"
        _gk_ui_操作提示.add_theme_font_size_override("font_size", 20)
        _gk_ui_操作提示.add_theme_color_override("font_color", Color(0.85, 0.9, 1.0, 1.0))
        _gk_ui_操作提示.mouse_filter = Control.MOUSE_FILTER_IGNORE
        _gk_ui_root.add_child(_gk_ui_操作提示)
    _gk_ui_操作提示.position = Vector2(ui_x, ui_y)
    _gk_ui_操作提示.text = str("A/D 或 左右方向键 移动    空格 跳")
    _gk_ensure_ui(self)
    if _gk_ui_分数 == null:
        _gk_ui_分数 = Label.new()
        _gk_ui_分数.name = "分数"
        _gk_ui_分数.add_theme_font_size_override("font_size", 26)
        _gk_ui_分数.add_theme_color_override("font_color", Color(1.0, 0.92, 0.45, 1.0))
        _gk_ui_分数.mouse_filter = Control.MOUSE_FILTER_IGNORE
        _gk_ui_root.add_child(_gk_ui_分数)
    _gk_ui_分数.position = Vector2(ui_x_2, ui_y_2)
    _gk_ui_分数.text = str("分数: 0")
    _gk_ensure_ui(self)
    if _gk_ui_最高分 == null:
        _gk_ui_最高分 = Label.new()
        _gk_ui_最高分.name = "最高分"
        _gk_ui_最高分.add_theme_font_size_override("font_size", 20)
        _gk_ui_最高分.add_theme_color_override("font_color", Color(0.7, 0.75, 0.85, 1.0))
        _gk_ui_最高分.mouse_filter = Control.MOUSE_FILTER_IGNORE
        _gk_ui_root.add_child(_gk_ui_最高分)
    _gk_ui_最高分.position = Vector2(ui_x_3, ui_y_3)
    _gk_ui_最高分.text = str("最高分: 0")
    _gk_ensure_ui(self)
    if _gk_ui_重开按钮 == null:
        _gk_ui_重开按钮 = Button.new()
        _gk_ui_重开按钮.name = "重开按钮"
        _gk_ui_重开按钮.mouse_filter = Control.MOUSE_FILTER_STOP
        _gk_ui_重开按钮.pressed.connect(gk_on_button)
        _gk_ui_root.add_child(_gk_ui_重开按钮)
    _gk_ui_重开按钮.position = Vector2(ui_x_4, ui_y_4)
    _gk_ui_重开按钮.size = Vector2(128.0, 36.0)
    _gk_ui_重开按钮.text = str("重开 (R)")

func _process(delta: float) -> void:
    var _unused_delta := delta
    if _gk_follow_cam == null:
        _gk_follow_cam = _gk_find_camera(self)
    _gk_follow_look = self
    if _gk_follow_cam != null and _gk_follow_look != null:
        _gk_follow_cam.global_position = _gk_follow_cam.global_position.lerp(Vector2(_gk_follow_look.global_position.x + 0.0, _gk_follow_look.global_position.y + 0.0), 0.12)

func _physics_process(delta: float) -> void:
    var _unused_delta := delta
    var _dir1 := (Input.get_vector(ACTION_MOVE_LEFT, ACTION_MOVE_RIGHT, ACTION_MOVE_UP, ACTION_MOVE_DOWN, 0.2)).normalized()
    self.velocity += self.get_gravity() * get_physics_process_delta_time()
    self.velocity.x = _dir1.x * move_speed
    self.move_and_slide()
    if self.velocity.x < 0.0:
        self.scale.x = -abs(self.scale.x)
    else:
        self.scale.x = abs(self.scale.x)
    if (self.is_on_floor() and Input.is_action_just_pressed(ACTION_JUMP)):
        self.velocity.y = -jump_force


func restart_game() -> void:
    get_tree().call_deferred("reload_current_scene")


func _gk_find_camera_named(host: Node, wanted: String) -> Node:
    if wanted.is_empty():
        return null
    for found in host.get_tree().root.find_children(wanted, "Camera3D", true, false):
        return found
    for found in host.get_tree().root.find_children(wanted, "Camera2D", true, false):
        return found
    return _gk_find_camera(host)


func _gk_find_camera(host: Node) -> Node:
    for child in host.get_tree().get_nodes_in_group("gk_camera"):
        if child is Camera2D or child is Camera3D:
            return child
    for found in host.get_tree().root.find_children("*", "Camera2D", true, false):
        return found
    for found in host.get_tree().root.find_children("*", "Camera3D", true, false):
        return found
    return null


func _gk_save_file(slot: String) -> String:
    return "user://gk_" + slot + ".json"

func _gk_save_exists(slot: String) -> bool:
    return FileAccess.file_exists(_gk_save_file(slot))

func _gk_read_save(path: String) -> Dictionary:
    if not FileAccess.file_exists(path):
        return {}
    var file := FileAccess.open(path, FileAccess.READ)
    if file == null:
        push_warning("存档打不开: " + path)
        return {}
    var text := file.get_as_text()
    file.close()
    var parsed: Variant = JSON.parse_string(text)
    if parsed is Dictionary:
        return parsed
    push_warning("存档内容不是合法 JSON: " + path)
    return {}

func _gk_write_save(path: String, data: Dictionary) -> void:
    var file := FileAccess.open(path, FileAccess.WRITE)
    if file == null:
        push_warning("存档写不进去: " + path)
        return
    file.store_string(JSON.stringify(data))
    file.close()


func _gk_delete_save(slot: String) -> void:
    var path := _gk_save_file(slot)
    if not FileAccess.file_exists(path):
        return
    DirAccess.remove_absolute(path)


func gk_on_button() -> void:
    restart_game()


func _gk_ensure_ui(host: Node) -> void:
    if _gk_ui_layer == null:
        _gk_ui_layer = CanvasLayer.new()
        _gk_ui_layer.name = "GKUI"
        _gk_ui_layer.layer = 10
        host.add_child(_gk_ui_layer)
        _gk_ui_root = Control.new()
        _gk_ui_root.name = "Root"
        _gk_ui_root.set_anchors_preset(Control.PRESET_FULL_RECT)
        _gk_ui_root.mouse_filter = Control.MOUSE_FILTER_IGNORE
        _gk_ui_layer.add_child(_gk_ui_root)


func _gk_find_widget(host: Node, widget_name: String) -> Control:
    if _gk_ui_layer == null:
        return null
    var found := _gk_ui_layer.find_child(widget_name, true, false)
    if found is Control:
        return found
    return null


# ==========================================================================
# 下面是手写补充(不在节点图里)。
#
# ── 为什么这段是手写的 ──────────────────────────────────────────────
# 金币被吃掉时要同时做三件事:加分、刷新 HUD、破纪录就立刻存盘。
# 用节点图要七八个节点才拼得出来，而这个示例的重点是"看得见 + 好读"。
# 真实项目可以两种方式混用:高频简单的逻辑画节点图，
# 个别需要精细控制的地方手写。
#
# score / best_score 这两个变量由节点图声明(『变量_设置』『存档_读取到变量』)，
# _gk_ui_分数 / _gk_ui_最高分 两个控件也由『界面_文本』建好 ——
# 所以这里直接引用就行，**不要重复声明**，否则 Godot 报变量重定义。
# ==========================================================================

## 金币脚本碰到玩家时会调用这个方法: body.add_score(10)
func add_score(amount: int) -> void:
    score += float(amount)
    if score > float(best_score):
        best_score = score
        # 破纪录才写盘:每吃一个金币都写文件没必要，而且写盘会卡帧
        _gk_save_data["best_score"] = best_score
        _gk_write_save(_gk_save_file("best"), _gk_save_data)
        if _gk_ui_最高分 != null:
            _gk_ui_最高分.text = "最高分: %d" % int(best_score)
    if _gk_ui_分数 != null:
        _gk_ui_分数.text = "分数: %d" % int(score)
