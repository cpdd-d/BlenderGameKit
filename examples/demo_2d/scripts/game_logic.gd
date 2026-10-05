##########################################################################
# 由 BlenderGameKit 自动生成，请勿手动修改。
# 节点树: 玩家角色
# 修改请回到 Blender 里改节点图，然后重新导出。
##########################################################################

class_name PlayerController
extends CharacterBody2D

# ---- 信号 ----
signal died

# ---- 跨函数共享的引用(在 _ready 里赋值) ----
var _gk_ap0 = null

# ---- 输入动作名(Godot 输入映射里的动作)----
const ACTION_KEY_W := "key_w"
const ACTION_KEY_S := "key_s"
const ACTION_KEY_A := "key_a"
const ACTION_KEY_D := "key_d"
const ACTION_JUMP := "jump"

# ---- 可在检查器里调的参数 ----
@export var move_speed: float = 240.0  # 移动_按方向 · 速度
@export var jump_force: float = 520.0  # 移动_跳跃 · 强度
@export var ui_x: float = 20.0  # 界面_文本 · X
@export var ui_y: float = 20.0  # 界面_文本 · Y
@export var flash_time: float = 0.3  # 屏幕_闪白 · 时长

# ---- 成员变量 ----
var health: float = 3.0
var score: float = 0.0
var _gk_follow_cam: Node
var _gk_follow_look: Node
var state: String
var _gk_ui_layer: CanvasLayer
var _gk_ui_root: Control
var _gk_ui_显示分数: Label

func _ready() -> void:
    health = 3.0
    score = 0.0
    print("AD 移动，空格跳跃，J 攻击")

func _physics_process(delta: float) -> void:
    var _unused_delta := delta
    var _in_x1 := Input.get_action_strength(ACTION_KEY_D) - Input.get_action_strength(ACTION_KEY_A)
    var _in_y2 := Input.get_action_strength(ACTION_KEY_W) - Input.get_action_strength(ACTION_KEY_S)
    if absf(_in_x1) < 0.2: _in_x1 = 0.0
    if absf(_in_y2) < 0.2: _in_y2 = 0.0
    var _in_len3 := sqrt(_in_x1 * _in_x1 + _in_y2 * _in_y2)
    if _in_len3 > 1.0:
        _in_x1 /= _in_len3
        _in_y2 /= _in_len3
    var _dir4 := (Vector2(_in_x1, _in_y2)).normalized()
    self.velocity += self.get_gravity() * get_physics_process_delta_time()
    self.velocity.x = _dir4.x * move_speed
    self.move_and_slide()
    if self.velocity.x < 0.0:
        self.scale.x = -abs(self.scale.x)
    else:
        self.scale.x = abs(self.scale.x)
    # 相机跟随的逻辑在 _physics_process 里(和角色同一个时钟)
    if (self.is_on_floor() and Input.is_action_just_pressed(ACTION_JUMP)):
        self.velocity.y = -jump_force
        state = "jump"
        if (health <= 0.0):
            died.emit()
            queue_free()
    # ---- camera_follow ----
    if _gk_follow_cam == null:
        _gk_follow_cam = _gk_find_camera(self)
    _gk_follow_look = self
    if _gk_follow_cam != null and _gk_follow_look != null:
        _gk_follow_cam.global_position = _gk_follow_cam.global_position.lerp(Vector2(_gk_follow_look.global_position.x + 0.0, _gk_follow_look.global_position.y + -40.0), 0.12)


func on_attack_pressed() -> void:
    var _ap6 := _gk_find_animation_player(self)
    var _anim5 := "attack"
    if _ap6 != null and _ap6.has_animation(_anim5):
        _ap6.play(_anim5, -1.0, 1.0)
    else:
        if _ap6 == null:
            push_warning("[GK] 没找到 AnimationPlayer,播不了动画: %s" % _anim5)
        else:
            push_warning("[GK] 动画不存在: %s。这个模型里可用的动画有: %s" % [_anim5, _ap6.get_animation_list()])
    state = "attack"
    score = (score + 1.0)
    _gk_ensure_ui(self)
    if _gk_ui_显示分数 == null:
        _gk_ui_显示分数 = Label.new()
        _gk_ui_显示分数.name = "显示分数"
        _gk_ui_显示分数.add_theme_font_size_override("font_size", 24)
        _gk_ui_显示分数.add_theme_color_override("font_color", Color(1.0, 1.0, 1.0, 1.0))
        _gk_ui_显示分数.mouse_filter = Control.MOUSE_FILTER_IGNORE
        _gk_ui_root.add_child(_gk_ui_显示分数)
    _gk_ui_显示分数.position = Vector2(ui_x, ui_y)
    _gk_ui_显示分数.text = str(score)
    var _flash7 := ColorRect.new()
    _flash7.color = Color(1.0, 1.0, 1.0, 0.6)
    _flash7.mouse_filter = Control.MOUSE_FILTER_IGNORE
    _flash7.set_anchors_preset(Control.PRESET_FULL_RECT)
    _flash7.z_index = 100
    add_child(_flash7)
    var _flash_tween8 := create_tween()
    _flash_tween8.tween_property(_flash7, "color:a", 0.0, flash_time)
    _flash_tween8.tween_callback(_flash7.queue_free)
    var _scene9 := load("res://Bullet.tscn") as PackedScene
    if _scene9 == null:
        push_error("场景加载失败: %s" % "res://Bullet.tscn")
    else:
        var _inst10 := _scene9.instantiate()
        if _inst10 is Node3D or _inst10 is Node2D:
            _inst10.position = Vector2(0.0, 0.0)
        get_tree().current_scene.add_child(_inst10)


func _gk_find_animation_player(host: Node) -> Node:
    if _gk_ap0 != null and is_instance_valid(_gk_ap0):
        return _gk_ap0
    if host is AnimationPlayer:
        _gk_ap0 = host
        return _gk_ap0
    var direct := host.get_node_or_null("AnimationPlayer")
    if direct is AnimationPlayer:
        _gk_ap0 = direct
        return _gk_ap0
    for found in host.find_children("*", "AnimationPlayer", true, false):
        _gk_ap0 = found
        return _gk_ap0
    if host.get_tree() != null:
        for found in host.get_tree().root.find_children("*", "AnimationPlayer", true, false):
            _gk_ap0 = found
            return _gk_ap0
    return null


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


func _gk_find_widget(widget_name: String) -> Control:
    if _gk_ui_layer == null:
        return null
    var found := _gk_ui_layer.find_child(widget_name, true, false)
    if found is Control:
        return found
    return null
