##########################################################################
# 由 BlenderGameKit 自动生成，请勿手动修改。
# 节点树: 玩家角色
# 修改请回到 Blender 里改节点图，然后重新导出。
##########################################################################

class_name PlayerController
extends CharacterBody2D

# ---- 信号 ----
signal died

# ---- 输入动作名(Godot 输入映射里的动作)----
const ACTION_MOVE_LEFT := "move_left"
const ACTION_MOVE_RIGHT := "move_right"
const ACTION_MOVE_UP := "move_up"
const ACTION_MOVE_DOWN := "move_down"
const ACTION_JUMP := "jump"

# ---- 可在检查器里调的参数 ----
@export var move_speed: float = 240.0  # 移动_按方向 · 速度
@export var jump_force: float = 520.0  # 移动_跳跃 · 强度

# ---- 成员变量 ----
var health: float
var state: String

func _ready() -> void:
    health = 3.0
    print("方向键移动，空格跳跃，J 攻击")

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
        state = "jump"
        if (health <= 0.0):
            died.emit()
            queue_free()


func on_attack_pressed() -> void:
    var _anim2 := "attack"
    if _gk_find_animation_player(self).has_animation(_anim2):
        _gk_find_animation_player(self).play(_anim2, -1.0, 1.0)
    else:
        push_warning("动画不存在: %s" % _anim2)
    state = "attack"


func _gk_find_animation_player(host: Node) -> Node:
    if host is AnimationPlayer:
        return host
    var direct := host.get_node_or_null("AnimationPlayer")
    if direct is AnimationPlayer:
        return direct
    for found in host.find_children("*", "AnimationPlayer", true, false):
        return found
    if host.get_tree() != null:
        for found in host.get_tree().root.find_children("*", "AnimationPlayer", true, false):
            return found
    return null
