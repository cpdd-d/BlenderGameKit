# 第 126 轮 task B:射线 vs 触发区(Area3D)/ 触发区玩家到底站不站得住
#
# 只回答两个问题,全部真机量:
#   ① 弹簧臂那条 `intersect_ray` 会不会被**触发区**(Area3D)挡住?
#      —— 用户截图里给玩家勾了『作为触发器』,必须知道它算不算障碍。
#   ② 勾了『作为触发器』的玩家(节点变 Area3D)还能不能被地板托住?
extends Node3D

const PREFIX := "RAYPROBE "
var frame := 0
var tests_done := false
var area_fall: Node3D = null
var area_fall_y0 := 0.0

func _say(s: String) -> void:
    print(PREFIX + s)

func _ready() -> void:
    Engine.max_fps = 60
    _build()
    _run()

func _box_shape(size: Vector3) -> BoxShape3D:
    var s := BoxShape3D.new()
    s.size = size
    return s

func _capsule(r: float, h: float) -> CapsuleShape3D:
    var s := CapsuleShape3D.new()
    s.radius = r
    s.height = h
    return s

func _add_body(name_: String, pos: Vector3, shape: Shape3D) -> StaticBody3D:
    var b := StaticBody3D.new()
    b.name = name_
    b.position = pos
    var c := CollisionShape3D.new()
    c.shape = shape
    b.add_child(c)
    add_child(b)
    return b

func _add_area(name_: String, pos: Vector3, shape: Shape3D,
               parent: Node3D = null) -> Area3D:
    var a := Area3D.new()
    a.name = name_
    a.position = pos
    var c := CollisionShape3D.new()
    c.shape = shape
    a.add_child(c)
    if parent == null:
        add_child(a)
    else:
        parent.add_child(a)
    return a

func _build() -> void:
    # 地板:所有东西都站在它上面
    _add_body("地板", Vector3(0, -0.5, 0), _box_shape(Vector3(80, 1, 40)))
    # A 道:实心墙(x=0)
    _add_body("实心墙", Vector3(0, 1.4, -3), _box_shape(Vector3(4, 4, 0.5)))
    # B 道:触发区(Area3D),和实心墙同一个位置关系(x=5)
    _add_area("触发区", Vector3(5, 1.4, -3), _box_shape(Vector3(4, 4, 0.5)))
    # C 道:一个"玩家"(CharacterBody3D + 胶囊 0.5/1.8),身上带一个触发区子节点
    var pawn := CharacterBody3D.new()
    pawn.name = "假玩家"
    pawn.position = Vector3(-5, 0.9, 0)
    var pc := CollisionShape3D.new()
    pc.shape = _capsule(0.5, 1.8)
    pawn.add_child(pc)
    add_child(pawn)
    _add_area("玩家触发区", Vector3(0, 0.9, 0), _box_shape(Vector3(1.2, 1.8, 1.2)), pawn)
    # D 道:一个"作为触发器"的玩家(Area3D),悬空 —— 看它掉不掉
    area_fall = _add_area("触发区玩家", Vector3(20, 3.0, 0),
                          _box_shape(Vector3(1.2, 1.8, 1.2)))
    area_fall_y0 = area_fall.position.y

func _ray(from: Vector3, to: Vector3, exclude: Array[RID],
          with_areas: bool) -> Dictionary:
    var space := get_world_3d().direct_space_state
    var q := PhysicsRayQueryParameters3D.create(from, to, 0xFFFFFFFF, exclude)
    q.collide_with_areas = with_areas
    return space.intersect_ray(q)

func _fmt(hit: Dictionary) -> String:
    if hit.is_empty():
        return "MISS(没撞到)"
    var col = hit.get("collider")
    var n := "-"
    if col is Node:
        n = String((col as Node).name)
    var pos: Vector3 = hit.get("position")
    return "HIT %s pos=%s (z=%.4f)" % [n, str(pos), pos.z]

func _wait(n: int) -> void:
    for _i in n:
        await get_tree().physics_frame

func _physics_process(_d: float) -> void:
    frame += 1

func _run() -> void:
    await _wait(3)
    var pawn := get_node_or_null("假玩家") as CharacterBody3D
    var q_default := PhysicsRayQueryParameters3D.create(
        Vector3.ZERO, Vector3(0, 0, -1))
    _say("DEFAULT collide_with_bodies=%s collide_with_areas=%s"
         % [str(q_default.collide_with_bodies), str(q_default.collide_with_areas)])
    _say("GODOT=%s" % Engine.get_version_info()["string"])

    # T1 实心墙:默认参数
    _say("T1 射线->实心墙 默认: " + _fmt(_ray(
        Vector3(0, 1.4, 0), Vector3(0, 1.4, -6), [], false)))
    # T2 触发区:默认参数(弹簧臂用的就是这套)
    _say("T2 射线->触发区 默认(collide_with_areas=false): " + _fmt(_ray(
        Vector3(5, 1.4, 0), Vector3(5, 1.4, -6), [], false)))
    # T3 触发区:显式打开 collide_with_areas
    _say("T3 射线->触发区 collide_with_areas=true: " + _fmt(_ray(
        Vector3(5, 1.4, 0), Vector3(5, 1.4, -6), [], true)))
    # T4 假玩家自己的胶囊:排除自己的 RID(示例的做法)
    var self_rid: Array[RID] = [pawn.get_rid()]
    _say("T4 枢轴(体内)->墙,排除玩家 RID: " + _fmt(_ray(
        Vector3(-5, 1.4, 0.6), Vector3(-5, 1.4, -6), self_rid, false)))
    # T5 同一发,但**不排除**自己 —— 射线起点在胶囊体内
    _say("T5 枢轴(体内)->墙,不排除自己: " + _fmt(_ray(
        Vector3(-5, 1.4, 0.6), Vector3(-5, 1.4, -6), [], false)))
    # T6 玩家身上的触发区子节点:默认参数会不会挡
    _say("T6 射线穿过玩家触发区 默认: " + _fmt(_ray(
        Vector3(-5, 1.4, 0.6), Vector3(-5, 1.4, -6), [], false)))
    # T7 起点在实心体内部(不排除)
    _say("T7 起点在实心墙内部 默认: " + _fmt(_ray(
        Vector3(0, 1.4, -3), Vector3(0, 1.4, -6), [], false)))
    # T8 排除 Area3D 的 RID + collide_with_areas=true(以后要是真打开会怎样)
    var area := get_node_or_null("触发区") as Area3D
    var ex_area: Array[RID] = [area.get_rid()]
    _say("T8 触发区 + collide_with_areas=true + 排除它的 RID: " + _fmt(_ray(
        Vector3(5, 1.4, 0), Vector3(5, 1.4, -6), ex_area, true)))
    # T9 触发区玩家的 group / 是不是 CollisionObject3D / 有没有物理体
    _say("T9 触发区玩家 class=%s is_CollisionObject3D=%s"
         % [area_fall.get_class(), str(area_fall is CollisionObject3D)])

    # T10 让"触发区玩家"和"假玩家"各站 90 帧,看谁被地板托住
    await _wait(90)
    _say("T10 90 帧后(有物理体的玩家) y=%.4f on_floor=%s"
         % [pawn.global_position.y, str(pawn.is_on_floor())])
    _say("T10 90 帧后(作为触发器的玩家) y=%.4f(初始 %.2f,掉下去了就是没被托住)"
         % [area_fall.global_position.y, area_fall_y0])
    tests_done = true
    _say("DONE")
    await _wait(3)
    get_tree().quit()
