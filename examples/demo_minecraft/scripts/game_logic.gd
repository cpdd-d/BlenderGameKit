##########################################################################
# 由 BlenderGameKit 自动生成，请勿手动修改。
# 节点树: 我的世界
# 修改请回到 Blender 里改节点图，然后重新导出。
##########################################################################

class_name MinecraftPlayer
extends CharacterBody3D

# ---- 输入动作名(Godot 输入映射里的动作)----
const ACTION_MINE := "mine"
const ACTION_PLACE := "place"
const ACTION_SLOT_1 := "slot_1"
const ACTION_SLOT_2 := "slot_2"
const ACTION_SLOT_3 := "slot_3"
const ACTION_SLOT_4 := "slot_4"
const ACTION_UI_CANCEL := "ui_cancel"
const ACTION_MOVE_UP := "move_up"
const ACTION_MOVE_DOWN := "move_down"
const ACTION_MOVE_LEFT := "move_left"
const ACTION_MOVE_RIGHT := "move_right"
const ACTION_JUMP := "jump"

# ---- 场景节点引用 ----
@onready var 相机_第一人称相机: Node = _gk_ref_typed(self, "第一人称相机", "Node")
@onready var 节点_monsters_怪物1: CharacterBody3D = _gk_ref_typed(self, "../Monsters/怪物1", "CharacterBody3D")
@onready var 节点_monsters_怪物2: CharacterBody3D = _gk_ref_typed(self, "../Monsters/怪物2", "CharacterBody3D")
@onready var 节点_monsters_怪物3: CharacterBody3D = _gk_ref_typed(self, "../Monsters/怪物3", "CharacterBody3D")

# ---- 可在检查器里调的参数 ----
@export var move_speed: float = 6.0  # 移动_按方向 · 速度
@export var chase_speed: float = 2.6  # 移动_朝向目标 · 速度
@export var chase_speed_2: float = 2.6  # 移动_朝向目标 · 速度
@export var chase_speed_3: float = 2.6  # 移动_朝向目标 · 速度
@export var ui_x: float = 20.0  # 界面_文本 · X
@export var ui_y: float = 44.0  # 界面_文本 · Y
@export var ui_x_2: float = 20.0  # 界面_进度条 · X
@export var ui_y_2: float = 76.0  # 界面_进度条 · Y
@export var jump_force: float = 8.5  # 移动_跳跃 · 强度

# ---- 成员变量 ----
var 方块序号: float = 0
var 生命: float = 100
var _gk_mouse_delta: Vector2
var _gk_mouse_frame: int = -1
var _gk_cam_blend_left: float
var _gk_cam_homes: Dictionary
var gk_ray_准星射线: Variant
var gk_ray_point_准星射线: Variant
var _gk_button_edge: Array = [[-1, false, false], [-1, false, false]]
var _gk_button_edge_2: Array = [[-1, false, false], [-1, false, false]]
var _gk_ui_layer: CanvasLayer
var _gk_ui_root: Control
var _gk_ui_生命文本: Label
var _gk_ui_生命条: ProgressBar
var _gk_cd_ticks: int = -999999

func _ready() -> void:
    Input.mouse_mode = Input.MOUSE_MODE_CAPTURED
    print("我的世界示例:WASD 移动 / 空格 跳 / 鼠标 转视角 / 1~4 选方块 / 左键或 E 挖 / 右键或 Q 放 / ESC 退出")

func _process(delta: float) -> void:
    var _unused_delta := delta
    var _cam1 = 相机_第一人称相机
    if _cam1 != null and _gk_cam_blend_left > 0.0:
        _gk_cam_blend_left = 0.0
        if _cam1 is Node3D:
            _gk_cam_homes[_cam1] = (_cam1 as Node3D).global_transform
    if _cam1 is Node3D:
        var _gk_rot: Vector3 = (_cam1 as Node3D).rotation_degrees + (Vector3(((_gk_mouse_delta).y * 0.12), ((_gk_mouse_delta).x * 0.12), 0.0))
        if _gk_rot.x > 89.0:
            _gk_rot.x = 89.0
        elif _gk_rot.x < -89.0:
            _gk_rot.x = -89.0
        (_cam1 as Node3D).rotation_degrees = _gk_rot
    var _space2: PhysicsDirectSpaceState3D = get_world_3d().direct_space_state
    var _cam8 := get_viewport().get_camera_3d()
    var _dir4 := (Vector3(1.0, 0.0, 0.0)).normalized()
    if _cam8 != null:
        _dir4 = (-_cam8.global_transform.basis.z).normalized()
    var _from5 := global_transform.origin + Vector3(0.0, 0.0, 0.0)
    if _cam8 != null:
        _from5 = _cam8.global_position + Vector3(0.0, 0.0, 0.0)
    var _to6 := _from5 + _dir4 * 6.0
    var _query7 := PhysicsRayQueryParameters3D.create(_from5, _to6)
    _query7.collision_mask = 1
    var _hit3: Dictionary = _space2.intersect_ray(_query7)
    if not _hit3.is_empty():
        gk_ray_准星射线 = _hit3["collider"]
        gk_ray_point_准星射线 = _hit3["position"]
        if (_gk_button_down() or Input.is_action_just_pressed(ACTION_MINE)):
            if gk_ray_准星射线 != null and is_instance_valid(gk_ray_准星射线):
                gk_ray_准星射线.queue_free()
        if (_gk_button_down_2() or Input.is_action_just_pressed(ACTION_PLACE)):
            var _sw9 := int(方块序号)
            match _sw9:
                0:
                    var _asset10 := _gk_spawn_asset("res://blocks/GrassBlock.tscn", "草方块", Vector3((gk_ray_point_准星射线).x, ((gk_ray_point_准星射线).y + 0.5), (gk_ray_point_准星射线).z), [{"kind": 0, "dims": Vector3(1.0, 1.0, 1.0), "radius": 0.5, "height": 1.0, "offset": Vector3(0.0, 0.0, 0.0)}], 1, 1)
                    if _asset10 != null:
                        get_tree().current_scene.add_child(_asset10)
                1:
                    var _asset11 := _gk_spawn_asset("res://blocks/StoneBlock.tscn", "石头方块", Vector3((gk_ray_point_准星射线).x, ((gk_ray_point_准星射线).y + 0.5), (gk_ray_point_准星射线).z), [{"kind": 0, "dims": Vector3(1.0, 1.0, 1.0), "radius": 0.5, "height": 1.0, "offset": Vector3(0.0, 0.0, 0.0)}], 1, 1)
                    if _asset11 != null:
                        get_tree().current_scene.add_child(_asset11)
                2:
                    var _asset12 := _gk_spawn_asset("res://blocks/SandBlock.tscn", "沙子方块", Vector3((gk_ray_point_准星射线).x, ((gk_ray_point_准星射线).y + 0.5), (gk_ray_point_准星射线).z), [{"kind": 0, "dims": Vector3(1.0, 1.0, 1.0), "radius": 0.5, "height": 1.0, "offset": Vector3(0.0, 0.0, 0.0)}], 1, 1)
                    if _asset12 != null:
                        get_tree().current_scene.add_child(_asset12)
                3:
                    var _asset13 := _gk_spawn_asset("res://blocks/WoodBlock.tscn", "木头方块", Vector3((gk_ray_point_准星射线).x, ((gk_ray_point_准星射线).y + 0.5), (gk_ray_point_准星射线).z), [{"kind": 0, "dims": Vector3(1.0, 1.0, 1.0), "radius": 0.5, "height": 1.0, "offset": Vector3(0.0, 0.0, 0.0)}], 1, 1)
                    if _asset13 != null:
                        get_tree().current_scene.add_child(_asset13)
                _:
                    pass
    if Input.is_action_just_pressed(ACTION_SLOT_1):
        方块序号 = 0.0
    if Input.is_action_just_pressed(ACTION_SLOT_2):
        方块序号 = 1.0
    if Input.is_action_just_pressed(ACTION_SLOT_3):
        方块序号 = 2.0
    if Input.is_action_just_pressed(ACTION_SLOT_4):
        方块序号 = 3.0
    if Input.is_action_just_pressed(ACTION_UI_CANCEL):
        get_tree().quit()
    # ---- mouse_motion_reset ----
    if _gk_mouse_frame != Engine.get_physics_frames():
        _gk_mouse_delta = Vector2.ZERO
    # ---- button_edges ----
    _gk_button_down()
    _gk_button_down_2()

func _physics_process(delta: float) -> void:
    var _unused_delta := delta
    var _in_x14 := Input.get_action_strength(ACTION_MOVE_RIGHT) - Input.get_action_strength(ACTION_MOVE_LEFT)
    var _in_y15 := Input.get_action_strength(ACTION_MOVE_UP) - Input.get_action_strength(ACTION_MOVE_DOWN)
    if absf(_in_x14) < 0.2: _in_x14 = 0.0
    if absf(_in_y15) < 0.2: _in_y15 = 0.0
    var _in_len16 := sqrt(_in_x14 * _in_x14 + _in_y15 * _in_y15)
    if _in_len16 > 1.0:
        _in_x14 /= _in_len16
        _in_y15 /= _in_len16
    var _dir17 := (Vector3(_in_x14, 0.0, _in_y15)).normalized()
    var _move_dir19 := _dir17
    var _ref18 = _gk_resolve_camera(self)
    if _ref18 is Node3D:
        var _ref_f20 := -(_ref18 as Node3D).global_transform.basis.z
        _ref_f20.y = 0.0
        var _ref_r21 := (_ref18 as Node3D).global_transform.basis.x
        _ref_r21.y = 0.0
        if _ref_f20.length_squared() > 0.0001:
            _ref_f20 = _ref_f20.normalized()
            _move_dir19 = _ref_f20 * (_dir17).z
        if _ref_r21.length_squared() > 0.0001:
            _ref_r21 = _ref_r21.normalized()
            _move_dir19 += _ref_r21 * (_dir17).x
    self.velocity += self.get_gravity() * get_physics_process_delta_time()
    self.velocity.x = _move_dir19.x * move_speed
    self.velocity.z = _move_dir19.z * move_speed
    self.move_and_slide()
    var _here22 := _gk_global_position_3d(节点_monsters_怪物1)
    var _to_goal23 := _gk_global_position_3d(self) - _here22
    var _goal_dist24 := _to_goal23.length()
    var _wanted25 := Vector3.ZERO
    if _goal_dist24 > 1.3:
        _wanted25 = _to_goal23.normalized() * chase_speed
    节点_monsters_怪物1.velocity = 节点_monsters_怪物1.velocity.move_toward(_wanted25, 6.0 * get_physics_process_delta_time())
    if absf(_to_goal23.x) + absf(_to_goal23.z) > 0.001:
        节点_monsters_怪物1.look_at(_here22 + Vector3(_to_goal23.x, 0.0, _to_goal23.z), Vector3.UP)
    节点_monsters_怪物1.move_and_slide()
    var _here26 := _gk_global_position_3d(节点_monsters_怪物2)
    var _to_goal27 := _gk_global_position_3d(self) - _here26
    var _goal_dist28 := _to_goal27.length()
    var _wanted29 := Vector3.ZERO
    if _goal_dist28 > 1.3:
        _wanted29 = _to_goal27.normalized() * chase_speed_2
    节点_monsters_怪物2.velocity = 节点_monsters_怪物2.velocity.move_toward(_wanted29, 6.0 * get_physics_process_delta_time())
    if absf(_to_goal27.x) + absf(_to_goal27.z) > 0.001:
        节点_monsters_怪物2.look_at(_here26 + Vector3(_to_goal27.x, 0.0, _to_goal27.z), Vector3.UP)
    节点_monsters_怪物2.move_and_slide()
    var _here30 := _gk_global_position_3d(节点_monsters_怪物3)
    var _to_goal31 := _gk_global_position_3d(self) - _here30
    var _goal_dist32 := _to_goal31.length()
    var _wanted33 := Vector3.ZERO
    if _goal_dist32 > 1.3:
        _wanted33 = _to_goal31.normalized() * chase_speed_3
    节点_monsters_怪物3.velocity = 节点_monsters_怪物3.velocity.move_toward(_wanted33, 6.0 * get_physics_process_delta_time())
    if absf(_to_goal31.x) + absf(_to_goal31.z) > 0.001:
        节点_monsters_怪物3.look_at(_here30 + Vector3(_to_goal31.x, 0.0, _to_goal31.z), Vector3.UP)
    节点_monsters_怪物3.move_and_slide()
    _gk_ensure_ui(self)
    if _gk_ui_生命文本 == null:
        _gk_ui_生命文本 = Label.new()
        _gk_ui_生命文本.name = "生命文本"
        _gk_ui_生命文本.add_theme_font_size_override("font_size", 20)
        _gk_ui_生命文本.add_theme_color_override("font_color", Color(1.0, 1.0, 1.0, 1.0))
        _gk_ui_生命文本.mouse_filter = Control.MOUSE_FILTER_IGNORE
        _gk_ui_root.add_child(_gk_ui_生命文本)
    _gk_ui_生命文本.text = str(str("生命 ") + str(_gk_num_text(生命)))
    _gk_ui_生命文本.position = Vector2(ui_x, ui_y)
    _gk_ensure_ui(self)
    if _gk_ui_生命条 == null:
        _gk_ui_生命条 = ProgressBar.new()
        _gk_ui_生命条.name = "生命条"
        _gk_ui_生命条.max_value = 100.0
        _gk_ui_生命条.show_percentage = false
        var _fill := StyleBoxFlat.new()
        _fill.bg_color = Color(0.85, 0.22, 0.22, 1.0)
        _gk_ui_生命条.add_theme_stylebox_override("fill", _fill)
        _gk_ui_生命条.mouse_filter = Control.MOUSE_FILTER_IGNORE
        _gk_ui_root.add_child(_gk_ui_生命条)
    _gk_ui_生命条.size = Vector2(220.0, 16.0)
    _gk_ui_生命条.position = Vector2(ui_x_2, ui_y_2)
    _gk_ui_生命条.value = clampf(生命, 0.0, 100.0)
    if (((_gk_global_position_3d(self) - _gk_global_position_3d(节点_monsters_怪物1)).length() <= 1.3) or ((_gk_global_position_3d(self) - _gk_global_position_3d(节点_monsters_怪物2)).length() <= 1.3) or ((_gk_global_position_3d(self) - _gk_global_position_3d(节点_monsters_怪物3)).length() <= 1.3)):
        if Time.get_ticks_msec() - _gk_cd_ticks >= int((1.0) * 1000.0):
            _gk_cd_ticks = Time.get_ticks_msec()
            _gk_float_text_3d(self, "生命 -" + _gk_num_text(8.0), Color(1.0, 0.35, 0.35, 1.0), 0.7, 1.0)
            生命 = (生命 + -8.0)
            if (生命 <= 0):
                生命 = 100.0
                self.global_position = Vector3(0.5, 1.0, 0.5)
                print("你被怪物击倒,已回到出生点")
    if (self.is_on_floor() and Input.is_action_just_pressed(ACTION_JUMP)):
        self.velocity.y = jump_force
    # ---- mouse_motion_reset ----
    _gk_mouse_delta = Vector2.ZERO
    _gk_mouse_frame = Engine.get_physics_frames()
    # ---- button_edges ----
    _gk_button_down()
    _gk_button_down_2()


func _input(event: InputEvent) -> void:
    # 暂停期间不跑:角色玩法输入(视角 / 滚轮 / 触屏)
    if get_tree() == null or not get_tree().paused:
        _gk_mouse_motion_event(event)


func _gk_resolve_camera(host: Node) -> Node:
    # ① 视口里正在用的那台
    var cam: Node = host.get_viewport().get_camera_3d()
    if cam != null:
        return cam
    cam = host.get_viewport().get_camera_2d()
    if cam != null:
        return cam
    # ② 打了 gk_camera 分组的
    var grouped := host.get_tree().get_first_node_in_group("gk_camera")
    if grouped != null:
        return grouped
    # ③ 场景里第一台相机(顺手把它设成当前 —— 不然画面全黑)
    for found in host.get_tree().root.find_children("*", "Camera3D", true, false):
        (found as Camera3D).make_current()
        return found
    for found in host.get_tree().root.find_children("*", "Camera2D", true, false):
        (found as Camera2D).make_current()
        return found
    return null


func _gk_ref(host: Node, wanted: String) -> Node:
    if wanted.is_empty():
        return null
    var direct := host.get_node_or_null(wanted)
    if direct != null:
        return direct
    if host.get_tree() == null:
        return null
    # 先找同名子节点(限定在 host 子树里),再全树找 ——
    # 导出后脚本常在『碰撞代理』物体上,而用户摆的相机/角色
    # 是它的兄弟节点,相对路径找不到
    for found in host.find_children(wanted, "", true, false):
        return found
    for found in host.get_tree().root.find_children(wanted, "", true, false):
        return found
    push_warning("[GK] 没找到节点:%s —— 检查名字拼写,或者它是不是没被导出" % wanted)
    return null


func _gk_ref_typed(host: Node, wanted: String, type_name: String) -> Node:
    # ── 为什么按类型找(用户 2026-10-04,实测)────────────────
    # 导出后一个物体常常是**两个节点**:网格在 glb 实例里(根下面),
    # 碰撞代理在 Colliders 下 —— 两个同名。`_gk_ref` 按树的顺序找,
    # 拿到的往往是网格(MeshInstance3D),赋给 `area_金币1: Area3D` 直接报
    #     Trying to assign value of type 'MeshInstance3D' to a
    #     variable of type 'Area3D'
    # 于是触发器的 body_entered 永远接不上(金币吃不了)。
    # 所以先在全树里找**类型对得上**的那个,找不到再退回老逻辑。
    if wanted.is_empty() or host.get_tree() == null:
        return null
    if ClassDB.class_exists(type_name):
        for found in host.get_tree().root.find_children(wanted, type_name, true, false):
            return found
    return _gk_ref(host, wanted)


func _gk_spawn_asset(path: String, what: String, where: Vector3, shapes: Array, layer: int, mask: int) -> Node3D:
    var packed := load(path) as PackedScene
    if packed == null:
        push_error("[GK] 资产模型加载失败: %s" % path)
        return null
    var body: Node3D = null
    if not shapes.is_empty():
        var solid := StaticBody3D.new()
        solid.collision_layer = layer
        solid.collision_mask = mask
        for data in shapes:
            var shape := CollisionShape3D.new()
            match int(data.get("kind", 0)):
                1:
                    var ball := SphereShape3D.new()
                    ball.radius = maxf(float(data.get("radius", 0.5)), 0.05)
                    shape.shape = ball
                2:
                    var cap := CapsuleShape3D.new()
                    cap.radius = maxf(float(data.get("radius", 0.5)), 0.05)
                    cap.height = maxf(float(data.get("height", 1.0)), cap.radius * 2.0)
                    shape.shape = cap
                3:
                    var cyl := CylinderShape3D.new()
                    cyl.radius = maxf(float(data.get("radius", 0.5)), 0.05)
                    cyl.height = maxf(float(data.get("height", 1.0)), 0.05)
                    shape.shape = cyl
                4:
                    var hull := ConvexPolygonShape3D.new()
                    var pts: PackedVector3Array = data.get("points", PackedVector3Array())
                    if pts.size() < 4:
                        continue
                    hull.points = pts
                    shape.shape = hull
                _:
                    var box := BoxShape3D.new()
                    box.size = Vector3(data.get("dims", Vector3.ONE))
                    shape.shape = box
            shape.position = Vector3(data.get("offset", Vector3.ZERO))
            solid.add_child(shape)
        body = solid
    else:
        body = Node3D.new()
    body.name = what
    var model := packed.instantiate()
    body.add_child(model)
    if model is Node3D:
        var placed := model as Node3D
        placed.transform = placed.transform.affine_inverse()
    body.position = where
    return body


func _gk_button_down() -> bool:
    var physics := Engine.is_in_physics_frame()
    var frame := Engine.get_physics_frames() if physics else Engine.get_process_frames()
    var sample: Array = _gk_button_edge[1 if physics else 0]
    if int(sample[0]) != frame:
        var pressed: bool = Input.is_mouse_button_pressed(MOUSE_BUTTON_LEFT)
        sample[2] = pressed and not bool(sample[1])
        sample[1] = pressed
        sample[0] = frame
    return bool(sample[2])


func _gk_button_down_2() -> bool:
    var physics := Engine.is_in_physics_frame()
    var frame := Engine.get_physics_frames() if physics else Engine.get_process_frames()
    var sample: Array = _gk_button_edge_2[1 if physics else 0]
    if int(sample[0]) != frame:
        var pressed: bool = Input.is_mouse_button_pressed(MOUSE_BUTTON_RIGHT)
        sample[2] = pressed and not bool(sample[1])
        sample[1] = pressed
        sample[0] = frame
    return bool(sample[2])


func _gk_global_position_3d(host: Node) -> Vector3:
    if host is Node3D:
        return (host as Node3D).global_position
    return Vector3.ZERO


func _gk_num_text(value: float) -> String:
    # 整数值就显示成 '3'(不是 '3.0');有小数才保留两位
    if is_equal_approx(value, roundf(value)):
        return str(int(roundf(value)))
    return str(snappedf(value, 0.01))


func _gk_float_text_3d(anchor: Node, text: String, color: Color, seconds: float, rise: float) -> void:
    if anchor == null or not anchor.is_inside_tree():
        return
    var parent: Node = anchor.get_tree().current_scene
    if parent == null:
        parent = anchor.get_parent()
    if parent == null:
        return
    if not (anchor is Node3D):
        return
    var label := Label3D.new()
    label.text = text
    label.modulate = color
    label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
    label.no_depth_test = true
    label.font_size = 64
    label.pixel_size = 0.01
    label.outline_size = 12
    label.outline_modulate = Color(0.12, 0.12, 0.12, 0.9)
    var start := (anchor as Node3D).global_position + Vector3(0.0, 1.4, 0.0)
    parent.add_child(label)
    label.global_position = start
    var tween := label.create_tween()
    tween.set_parallel(true)
    tween.tween_property(label, "global_position", start + Vector3(0.0, rise, 0.0), seconds)
    tween.tween_property(label, "modulate:a", 0.0, seconds)
    tween.chain().tween_callback(label.queue_free)


func _gk_ensure_ui(host: Node) -> void:
    if _gk_ui_layer == null:
        _gk_ui_layer = CanvasLayer.new()
        _gk_ui_layer.name = "GKUI"
        _gk_ui_layer.layer = 10
        _gk_ui_layer.process_mode = Node.PROCESS_MODE_ALWAYS
        host.add_child(_gk_ui_layer)
        _gk_ui_root = Control.new()
        _gk_ui_root.name = "Root"
        _gk_ui_root.process_mode = Node.PROCESS_MODE_ALWAYS
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


func _gk_mouse_motion_event(event: InputEvent) -> void:
    if event is InputEventMouseMotion:
        _gk_mouse_delta += event.relative
