# 第 126 轮 task B:相机穿模真机探针(挂 autoload,不改示例产物)
#
# ── 为什么用 autoload(而不是"探针自己当主场景")──────────────────────
# 这个示例的玩家是 GkWorld(autoload)造出来的**唯一 pawn**,出生点由
# `get_tree().current_scene` 里的 GKPlayerStart 决定。探针要是自己当主场景,
# 出生点就找不到了 —— 那就不是"用户在玩的那套"了。挂 autoload 则原样不动:
#   * 关卡仍然是真的主场景(命令行指定 大堂.tscn / 走廊.tscn);
#   * 探针只**读**状态 + 发输入(和真人按键走同一条 Input.parse_input_event)。
#
# 采样在 process_physics_priority = +1000:玩家(-0)先跑完弹簧臂、探针后读,
# 读到的一定是**这一物理帧最终**的相机位姿(不是上一帧的)。
extends Node

const PREFIX := "CAMCLIP "
# 玩家模型逐轴尺寸(第 125 轮真机量出来的:碰撞胶囊 0.5/1.8 与网格逐轴一致)
const BODY_RADIUS := 0.5
const BODY_HEIGHT := 1.8
# 弹簧臂属性(示例里『相机_弹簧臂』节点的默认值,见 gk_default_player.gd)
const ARM_PIVOT_Y := 1.4
const ARM_LEN := 5.0

var player: CharacterBody3D = null
var cam3: Camera3D = null
var cam1: Camera3D = null
var model: Node3D = null

var phase := "BOOT"
var frame := 0
var rows: Array[String] = []
var shots := false
var shot_dir := "res://camclip_shots"
var msgs: Array[String] = []
var level_name := ""
var mesh_nodes: Array[MeshInstance3D] = []
# 模型三角形的**世界坐标**副本(相机到"真正可见表面"的距离靠它算;
# AABB 只是外壳,外壳距离 >= 表面距离,单看 AABB 会把情况说得比实际轻)
var tris: Array = []
var skinned := false
var _md_last: Array = []
# ── 第 128 轮:用户"站在第三关正方体上跳"穿模 ────────────────────────
# `--box=结局箱甲 --ceil=结局天花板 --only=box`:把玩家传到方块顶上、
# 站稳、发真按键跳,逐帧记枢轴 / 天花板余量 / 相机在谁体内。
var box_name := ""
var ceil_name := ""
var only := ""
var box_aabb := AABB()
var ceil_aabb := AABB()
var has_box := false
var has_ceil := false
# ── 第 129 轮:第一人称穿模 ──────────────────────────────────────────
# 第一人称相机**不过弹簧臂**(臂长 0.25、没有碰撞射线),所以前两轮那三条
# 验收线一条都抓不到它。这里量另一类东西:
#   ① 相机在不在某个实心体**里面**(小球形状查询,射线做不到这件事);
#   ② 相机离最近几何表面多远(六个轴向射线取最小);
#   ③ 玩家胶囊中心离最近表面多远(用来证明"身体在外面、眼睛在里面");
#   ④ 相机高出胶囊顶多少(第一人称的"眼睛"到底在不在身体里)。
var measure_fp := false

func _ready() -> void:
    process_mode = Node.PROCESS_MODE_ALWAYS
    process_physics_priority = 1000        # 玩家之后采样(见文件头)
    Engine.max_fps = 60
    get_window().size = Vector2i(1152, 648)
    for a in OS.get_cmdline_user_args():
        if a == "--shoot":
            shots = true
        elif a == "--fp":
            measure_fp = true
        elif a.begins_with("--box="):
            box_name = a.substr(6)
        elif a.begins_with("--ceil="):
            ceil_name = a.substr(7)
        elif a.begins_with("--only="):
            only = a.substr(7)
    # ── 中文节点名走**文件**传(第 128 轮踩过)────────────────────────
    # `godot ... -- --box=结局箱甲` 在 Windows 上经 PowerShell 转一手之后
    # 名字会变,节点找不到(于是探针打印 found=false 然后 SKIP)。
    # `res://camclip_args.txt` 是 UTF-8,一行一个 `key=value`,没有这个问题。
    var args_file := FileAccess.open("res://camclip_args.txt", FileAccess.READ)
    if args_file != null:
        while not args_file.eof_reached():
            var line := args_file.get_line().strip_edges()
            if line.is_empty() or line.begins_with("#"):
                continue
            var parts := line.split("=", true, 1)
            if parts.size() != 2:
                continue
            var key := parts[0].strip_edges()
            var value := parts[1].strip_edges()
            if key == "box":
                box_name = value
            elif key == "ceil":
                ceil_name = value
            elif key == "only":
                only = value
            elif key == "fp":
                measure_fp = value == "1" or value == "true"
            elif key == "shoot":
                shots = value == "1" or value == "true"
        args_file.close()
    _run()

# ---------------------------------------------------------------- 基础设施

func _say(s: String) -> void:
    print(PREFIX + s)
    msgs.append(s)

func _wait(n: int) -> void:
    for _i in n:
        await get_tree().physics_frame

## 和真人按键走同一条路(缓冲事件 -> 下一帧开头派发)。
## 不能直接用 Input.action_press:它把 pressed_frame 设成**当前**物理帧,
## 而探针的优先级是 +1000(玩家已经跑完了),玩家那一帧读
## `is_action_just_pressed` 永远读不到 —— 踩过一次,跳跃全没发出去。
func _key(action: String, pressed: bool) -> void:
    var code := 0
    for ev in InputMap.action_get_events(action):
        if ev is InputEventKey:
            var k := ev as InputEventKey
            code = k.physical_keycode if k.physical_keycode != 0 else k.keycode
            break
    if code == 0:
        _say("WARN 找不到动作 " + action)
        return
    var e := InputEventKey.new()
    e.keycode = code
    e.physical_keycode = code
    e.pressed = pressed
    Input.parse_input_event(e)

func _tap(action: String) -> void:
    _key(action, true)
    await _wait(2)
    _key(action, false)
    await _wait(1)

func _find_player() -> CharacterBody3D:
    var p := get_tree().get_first_node_in_group("gk_player")
    if p == null:
        var w := get_node_or_null("/root/GkWorld")
        if w != null:
            p = w.get("player")
    return p as CharacterBody3D

func _find_cams() -> void:
    for c in player.find_children("*", "Camera3D", true, false):
        var cam := c as Camera3D
        if String(cam.name).begins_with("第一"):
            cam1 = cam
        elif String(cam.name).begins_with("第三"):
            cam3 = cam
    if cam3 == null:
        for c in player.find_children("*", "Camera3D", true, false):
            if (c as Camera3D) != cam1:
                cam3 = c as Camera3D
                break

func _collect_meshes() -> void:
    mesh_nodes.clear()
    tris.clear()
    skinned = false
    if model == null:
        return
    for m in model.find_children("*", "MeshInstance3D", true, false):
        mesh_nodes.append(m as MeshInstance3D)
    # 三角形快照存**玩家局部坐标**(不是世界坐标):玩家还在下落 / 会被挪走,
    # 存世界坐标过一会儿就全错了 —— 每帧再用 player.global_transform 换回去。
    var to_local := player.global_transform.affine_inverse()
    for mi in mesh_nodes:
        var mesh := mi.mesh
        if mesh == null:
            continue
        var xf := to_local * mi.global_transform
        for s in mesh.get_surface_count():
            var arrays := mesh.surface_get_arrays(s)
            if arrays[Mesh.ARRAY_BONES] != null and not (arrays[Mesh.ARRAY_BONES] as PackedInt32Array).is_empty():
                skinned = true
            var verts: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
            var index = arrays[Mesh.ARRAY_INDEX]
            if index == null or (index as PackedInt32Array).is_empty():
                var i := 0
                while i + 2 < verts.size():
                    tris.append([xf * verts[i], xf * verts[i + 1], xf * verts[i + 2]])
                    i += 3
            else:
                var ia: PackedInt32Array = index
                var j := 0
                while j + 2 < ia.size():
                    tris.append([xf * verts[ia[j]], xf * verts[ia[j + 1]],
                                 xf * verts[ia[j + 2]]])
                    j += 3


## 点到三角形的**精确**距离(Ericson 的最近点分区算法)。
## 为什么非要它:AABB 是外壳,量出来只会比真实表面**远**;
## 而"相机近平面切进模型"看的是表面距离,差几厘米结论就反了。
func _tri_dist(p: Vector3, a: Vector3, b: Vector3, c: Vector3) -> float:
    var ab := b - a
    var ac := c - a
    var ap := p - a
    var d1 := ab.dot(ap)
    var d2 := ac.dot(ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return p.distance_to(a)
    var bp := p - b
    var d3 := ab.dot(bp)
    var d4 := ac.dot(bp)
    if d3 >= 0.0 and d4 <= d3:
        return p.distance_to(b)
    var vc := d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        return p.distance_to(a + ab * (d1 / (d1 - d3)))
    var cp := p - c
    var d5 := ab.dot(cp)
    var d6 := ac.dot(cp)
    if d6 >= 0.0 and d5 <= d6:
        return p.distance_to(c)
    var vb := d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        return p.distance_to(a + ac * (d2 / (d2 - d6)))
    var va := d3 * d6 - d5 * d4
    if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
        var w := (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return p.distance_to(b + (c - b) * w)
    var denom := 1.0 / (va + vb + vc)
    return p.distance_to(a + ab * (vb * denom) + ac * (vc * denom))


func _min_mesh_dist(p: Vector3) -> float:
    var xf := player.global_transform
    var best := 1.0e9
    for tri in tris:
        best = minf(best, _tri_dist(p, xf * tri[0], xf * tri[1], xf * tri[2]))
    return best

## 模型(可见网格)在世界空间的合并 AABB
func _model_aabb() -> AABB:
    var out := AABB()
    var first := true
    for mi in mesh_nodes:
        if not is_instance_valid(mi):
            continue
        var xf := mi.global_transform
        var a := mi.get_aabb()
        for i in 8:
            var corner := a.position + Vector3(
                a.size.x if (i & 1) else 0.0,
                a.size.y if (i & 2) else 0.0,
                a.size.z if (i & 4) else 0.0)
            var wp := xf * corner
            if first:
                out = AABB(wp, Vector3.ZERO)
                first = false
            else:
                out = out.expand(wp)
    return out

func _dist_to_aabb(p: Vector3, a: AABB) -> float:
    var lo := a.position
    var hi := a.position + a.size
    var q := Vector3(clampf(p.x, lo.x, hi.x), clampf(p.y, lo.y, hi.y),
                     clampf(p.z, lo.z, hi.z))
    return p.distance_to(q)

func _in_aabb(p: Vector3, a: AABB) -> bool:
    var lo := a.position
    var hi := a.position + a.size
    return (p.x >= lo.x and p.x <= hi.x and p.y >= lo.y and p.y <= hi.y
            and p.z >= lo.z and p.z <= hi.z)

## 玩家**局部坐标**里的"身体胶囊"(r=0.5 / h=1.8,第 125 轮那组数字)
func _in_body(cam: Vector3) -> bool:
    var lp := player.global_transform.affine_inverse() * cam
    return (Vector2(lp.x, lp.z).length() <= BODY_RADIUS
            and lp.y >= -0.02 and lp.y <= BODY_HEIGHT + 0.02)

func _who(obj: Object) -> String:
    var n := obj as Node
    if n != null:
        return String(n.name)
    return "-"

## 一个 StaticBody3D / Area3D 在世界空间的 AABB(按它自己的碰撞形状算)。
##
## 为什么不直接 `get_aabb()`:`CollisionObject3D` 没有那个方法,形状挂在
## 子节点 `CollisionShape3D` 上。这里只认盒子 / 胶囊这两种(示例里够用),
## 认不出来就返回空 AABB 并在日志里说一声 —— 不许悄悄给个错数。
func _body_aabb(node: Node) -> Array:
    var out := AABB()
    var found := false
    for child in node.find_children("*", "CollisionShape3D", true, false):
        var cs := child as CollisionShape3D
        var shape := cs.shape
        if shape == null:
            continue
        var xf := cs.global_transform
        var half := Vector3.ZERO
        if shape is BoxShape3D:
            half = (shape as BoxShape3D).size * 0.5
        elif shape is CapsuleShape3D:
            var cap := shape as CapsuleShape3D
            var r := minf(cap.radius, cap.height * 0.5)
            half = Vector3(r, cap.height * 0.5, r)
        else:
            continue
        for i in 8:
            var corner := Vector3(
                half.x if (i & 1) else -half.x,
                half.y if (i & 2) else -half.y,
                half.z if (i & 4) else -half.z)
            var wp := xf * corner
            if not found:
                out = AABB(wp, Vector3.ZERO)
                found = true
            else:
                out = out.expand(wp)
    return [found, out]

## 复刻弹簧臂那一条射线(同样的起点 / 终点 / 排除表),看它撞到了谁
##
## ⚠ 只排除**玩家自己**:示例里第二段是 `if cam is CollisionObject3D`,而
## Camera3D 根本不是 CollisionObject3D —— 检查生成物也确认了那段永远不成立
## (`camera.py` 确实写了它,但类型上不可能真)。所以这里照抄"只有玩家"。
func _arm_ray(pivot: Vector3, dir: Vector3) -> Array:
    var space := player.get_world_3d().direct_space_state
    var ex: Array[RID] = [player.get_rid()]
    var q := PhysicsRayQueryParameters3D.create(
        pivot, pivot + dir * ARM_LEN, 0xFFFFFFFF, ex)
    var hit := space.intersect_ray(q)
    if hit.is_empty():
        return [-1.0, "-"]
    return [pivot.distance_to(hit["position"]), _who(hit.get("collider"))]

func _shot(tag: String) -> void:
    if not shots:
        return
    # ⚠ 无头模式**没有渲染线程**:`RenderingServer.frame_post_draw` 永远不会发,
    #   在这里 await 会把整个探针协程**永久挂住** —— 现象是"游戏照跑、HEART
    #   照打、phase 卡死不动、逐帧表一个字都没写",`--quit-after` 到点才退。
    #   第 128 轮踩了半小时:窗口化跑时往 camclip_args.txt 写了 `shoot=1`,
    #   后来拿同一份目录跑无头就卡在第一个 `_shot` 上,而目录里留着**上一跑的**
    #   逐帧表 —— 复制回来一分析,得出"改动没生效"的错误结论。
    #   两道防线:①这里直接挡掉;②setup 脚本建副本时删掉旧的 camclip_frames.txt。
    if DisplayServer.get_name() == "headless":
        return
    await RenderingServer.frame_post_draw
    DirAccess.make_dir_recursive_absolute(
        ProjectSettings.globalize_path(shot_dir))
    var img := get_viewport().get_texture().get_image()
    img.save_png(shot_dir + "/" + tag + ".png")
    _say("SHOT " + tag)

# ---------------------------------------------------------------- 每帧采样

func _physics_process(_delta: float) -> void:
    frame += 1
    # ── 探针隔离:关卡剧情会把整棵场景换掉(大堂->走廊->结局)并暂停游戏 ──
    # 相机弹簧臂根本不依赖剧情,但"换关"会把玩家连相机一起搬走、AI 还会
    # 追着玩家推 —— 测出来的数字就不是"站在墙边"了。所以每帧强制解暂停,
    # 切关那句在 setup.py 里已经被换成 print(见那里的注释)。
    if get_tree().paused:
        get_tree().paused = false
    if frame % 60 == 0:
        var cs := get_tree().current_scene
        _say("HEART f=%d scene=%s file=%s paused=%s phase=%s kids=%d"
             % [frame, (String(cs.name) if cs != null else "null"),
                (String(cs.scene_file_path) if cs != null else "-"),
                str(get_tree().paused), phase,
                (cs.get_child_count() if cs != null else -1)])
    if player == null or not is_instance_valid(player):
        return
    var p := player.global_position
    var aabb := _model_aabb()
    var pivot := p + Vector3(0.0, ARM_PIVOT_Y, 0.0)
    var raw_pivot: Variant = player.get("_gk_spring_pivot")
    if raw_pivot is Vector3 and (raw_pivot as Vector3) != Vector3.ZERO:
        pivot = raw_pivot
    var dir := Vector3.ZERO
    var raw_dir: Variant = player.get("_gk_spring_dir")
    if raw_dir is Vector3:
        dir = raw_dir
    var dists := {}
    for pair in [["3", cam3], ["1", cam1]]:
        var cam := pair[1] as Camera3D
        if cam == null:
            continue
        var cp := cam.global_position
        dists[pair[0]] = {
            "piv": cp.distance_to(pivot),
            "ctr": cp.distance_to(aabb.position + aabb.size * 0.5),
            "aabb": _dist_to_aabb(cp, aabb),
            "in": 1 if _in_aabb(cp, aabb) else 0,
            "body": 1 if _in_body(cp) else 0,
            "y": cp.y - p.y,
        }
    var cur := "?"
    if cam3 != null and cam3.current:
        cur = "3"
    elif cam1 != null and cam1.current:
        cur = "1"
    var ray: Array = [-1.0, "-"]
    if dir != Vector3.ZERO:
        ray = _arm_ray(pivot, dir)
    var d3: Dictionary = dists.get("3", {})
    var d1: Dictionary = dists.get("1", {})
    # 相机到**模型真实表面**的距离(只要模型不是骨骼动画,快照就是准的)
    var md3 := -1.0
    var md1 := -1.0
    if not tris.is_empty() and (tris.size() <= 6000 or frame % 5 == 0):
        if cam3 != null:
            md3 = _min_mesh_dist(cam3.global_position)
        if cam1 != null:
            md1 = _min_mesh_dist(cam1.global_position)
        _md_last = [md3, md1]
    elif not tris.is_empty() and _md_last.size() == 2:
        md3 = _md_last[0]
        md1 = _md_last[1]
    var lp3 := Vector3.ZERO
    if cam3 != null:
        lp3 = player.global_transform.affine_inverse() * cam3.global_position
    # ── 第 128 轮:方块顶 / 天花板这两条世界信息 ──────────────────────
    # cl = 天花板底面到枢轴还剩多少(负数 = 枢轴已经**穿到天花板上面**去了)
    # cinC / cinB = 相机是不是钻进了天花板 / 方块里面
    var cl := 999.0
    var cam_y := cam3.global_position.y if cam3 != null else 0.0
    if has_ceil:
        cl = ceil_aabb.position.y - pivot.y
    var cin_c := 0
    var cin_b := 0
    if cam3 != null:
        if has_ceil and _in_aabb(cam3.global_position, ceil_aabb):
            cin_c = 1
        if has_box and _in_aabb(cam3.global_position, box_aabb):
            cin_b = 1
    rows.append("F %d %s cur=%s py=%.4f | 3:piv=%.4f ctr=%.4f aabb=%.4f in=%d body=%d dy=%.4f | 1:piv=%.4f ctr=%.4f aabb=%.4f in=%d body=%d dy=%.4f | ray=%.4f %s | vis=%d ppos=(%.3f,%.3f,%.3f) c3=(%.3f,%.3f,%.3f) c1=(%.3f,%.3f,%.3f) | md3=%.4f md1=%.4f lp3=(%.4f,%.4f,%.4f) | pivy=%.4f camy=%.4f cl=%.4f cinC=%d cinB=%d%s" % [
        frame, phase, cur, p.y,
        d3.get("piv", -1.0), d3.get("ctr", -1.0), d3.get("aabb", -1.0),
        d3.get("in", -1), d3.get("body", -1), d3.get("y", 0.0),
        d1.get("piv", -1.0), d1.get("ctr", -1.0), d1.get("aabb", -1.0),
        d1.get("in", -1), d1.get("body", -1), d1.get("y", 0.0),
        ray[0], ray[1],
        1 if (model != null and model.visible) else 0,
        p.x, p.y, p.z,
        cam3.global_position.x if cam3 != null else 0.0,
        cam3.global_position.y if cam3 != null else 0.0,
        cam3.global_position.z if cam3 != null else 0.0,
        cam1.global_position.x if cam1 != null else 0.0,
        cam1.global_position.y if cam1 != null else 0.0,
        cam1.global_position.z if cam1 != null else 0.0,
        md3, md1, lp3.x, lp3.y, lp3.z,
        pivot.y, cam_y, cl, cin_c, cin_b,
        _fp_tail(),
    ])

# ---------------------------------------------------------------- 第 129 轮:第一人称

## 第一人称那几列(只在 `--fp` 时追加,免得改掉前几轮逐帧表的字节)。
##
## 为什么"在不在里面"必须用小球查询、不能只打射线:
## Godot 的 `intersect_ray` **看不见"起点所在的那块实心体"**(第 126 轮独立量过:
## 从实心盒子里往外打 -> MISS)。相机已经伸进天花板里时,射线全都 MISS,
## 看上去"哪儿都不近"—— 那正是最坏的情况却被读数成最好。
## 所以:**射线量"在外面时有多近",小球量"在不在里面",两个一起看。**
func _fp_tail() -> String:
    if not measure_fp:
        return ""
    var c1p := cam1.global_position if cam1 != null else Vector3.ZERO
    var body := player.global_position
    var cap_top := player.global_position + Vector3(0.0, 0.9, 0.0)
    var inside := _inside_bodies(c1p)
    var body_inside := _inside_bodies(body)
    # 第三人称相机也量一份(第 129 轮 D2 第 3 问:是不是只有第一人称这样)
    var c3p := cam3.global_position if cam3 != null else Vector3.ZERO
    # ⚠ 一条经验(第 129 轮第三次踩):**把拼好的整段再 `%`**。
    # 写成 `return ("a" + "b" % args)` 时,`%` 只作用于 `"b"` ——
    # 占位符个数对不上就整段不格式化(输出的还是 `%.4f` 这种东西),
    # 而且不报错、不崩,只是数据悄悄变成废的。
    var fmt := (" | c1geo=%.4f c1in=%s pgeo=%.4f pin=%s c1cap=%+.4f "
                + "c1p=(%.3f,%.3f,%.3f) c3geo=%.4f c3in=%s c3cap=%+.4f")
    return fmt % [_nearest_geo(c1p), inside, _nearest_geo(body), body_inside,
                  c1p.y - cap_top.y, c1p.x, c1p.y, c1p.z,
                  _nearest_geo(c3p), _inside_bodies(c3p), c3p.y - cap_top.y]

## 离最近几何表面多远:朝 6 个轴向各打一条,取最近命中。
## ⚠ 起点已经在实心体内部时这个方法**没有意义**(射线全 MISS),见上面说明。
func _nearest_geo(p: Vector3, reach: float = 3.0) -> float:
    var space := player.get_world_3d().direct_space_state
    var ex: Array[RID] = [player.get_rid()]
    var best := -1.0
    for d in [Vector3.UP, Vector3.DOWN, Vector3.LEFT, Vector3.RIGHT,
              Vector3.FORWARD, Vector3.BACK]:
        var hit := space.intersect_ray(
            PhysicsRayQueryParameters3D.create(p, p + d * reach, 0xFFFFFFFF, ex))
        if hit.is_empty():
            continue
        var dist: float = p.distance_to(hit["position"])
        if best < 0.0 or dist < best:
            best = dist
    return best

## 这个点上有没有实心体(2 厘米小球做**形状**查询)。返回撞到的第一个名字,
## 没有就 "-"。排除玩家自己(否则永远报"在"玩家胶囊里 —— 第一人称本来就在)。
func _inside_bodies(p: Vector3) -> String:
    var space := player.get_world_3d().direct_space_state
    var sphere := SphereShape3D.new()
    sphere.radius = 0.02
    var q := PhysicsShapeQueryParameters3D.new()
    q.shape = sphere
    q.transform = Transform3D(Basis(), p)
    q.collision_mask = 0xFFFFFFFF
    var ex: Array[RID] = [player.get_rid()]
    q.exclude = ex
    var hits := space.intersect_shape(q, 8)
    if hits.is_empty():
        return "-"
    var node := hits[0].get("collider") as Node
    return String(node.name) if node != null else "?"

# ---------------------------------------------------------------- 场景脚本

## 按名字找节点,而且**优先要带碰撞的那一个**。
##
## ⚠ 第 128 轮踩过:`find_child("结局箱甲")` 先找到的是 **glb 里的网格节点**
## (同名!)—— 关卡根节点就是 glb 实例,它的内部节点排在 `Colliders` 前面。
## 拿到网格节点 -> 没有 CollisionShape3D 子节点 -> AABB 全 0 -> 后面的
## "枢轴到天花板还剩多少"全成 999(假数据)。所以这里扫全部后代,
## 命中名字的里面**优先返回 CollisionObject3D**,找不到才退回普通节点。
func _find_named(scene: Node, wanted: String) -> Node:
    var loose: Node = null
    for n in scene.find_children("*", "", true, false):
        var node_name := String(n.name)
        if node_name != wanted and node_name.find(wanted) < 0:
            continue
        if n is CollisionObject3D:
            return n
        if loose == null:
            loose = n
    return loose

func _boot() -> void:
    await _wait(5)
    var cs := get_tree().current_scene
    level_name = String(cs.scene_file_path) if cs != null else "?"
    if level_name.is_empty() and cs != null:
        level_name = String(cs.name)
    var boot_level := level_name
    var guard := 0
    while player == null and guard < 900:
        await _wait(1)
        guard += 1
        player = _find_player()
    if player == null:
        _say("FATAL 没找到玩家(gk_player 组里没人)")
        get_tree().quit()
        return
    _find_cams()
    model = player.get_node_or_null("Model") as Node3D
    _collect_meshes()
    _say("LEVEL=" + boot_level)
    # 模型包围盒 / 三角形规模:后面所有"进没进身体"的判断都基于它
    var aabb0 := _model_aabb()
    var lp0 := player.global_transform.affine_inverse()
    _say("MODEL_AABB_WORLD pos=(%.4f,%.4f,%.4f) size=(%.4f,%.4f,%.4f)"
         % [aabb0.position.x, aabb0.position.y, aabb0.position.z,
            aabb0.size.x, aabb0.size.y, aabb0.size.z])
    var lo := lp0 * aabb0.position
    var hi := lp0 * (aabb0.position + aabb0.size)
    _say("MODEL_AABB_LOCAL(相对玩家原点) min=(%.4f,%.4f,%.4f) max=(%.4f,%.4f,%.4f)"
         % [minf(lo.x, hi.x), minf(lo.y, hi.y), minf(lo.z, hi.z),
            maxf(lo.x, hi.x), maxf(lo.y, hi.y), maxf(lo.z, hi.z)])
    _say("MODEL_TRIS=%d skinned=%s meshes=%d"
         % [tris.size(), str(skinned), mesh_nodes.size()])
    var anim := player.find_children("*", "AnimationPlayer", true, false)
    _say("MODEL_ANIM_PLAYERS=%d" % anim.size())
    _say("PLAYER=" + str(player.get_path()))
    _say("CAM3=" + (str(cam3.get_path()) if cam3 != null else "null"))
    _say("CAM1=" + (str(cam1.get_path()) if cam1 != null else "null"))
    _say("MODEL=" + (str(model.get_path()) if model != null else "null")
         + " meshes=" + str(mesh_nodes.size()))
    if cam3 != null:
        _say("CAM3 fov=%.2f near=%.4f far=%.1f current=%s"
             % [cam3.fov, cam3.near, cam3.far, str(cam3.current)])
    if cam1 != null:
        _say("CAM1 fov=%.2f near=%.4f far=%.1f current=%s"
             % [cam1.fov, cam1.near, cam1.far, str(cam1.current)])
    # 模型材质的背面剔除设置(方案里"开双面"那条到底改的是什么)
    if not mesh_nodes.is_empty():
        var mi := mesh_nodes[0]
        var mat := mi.get_active_material(0)
        var cull: Variant = "(无材质)"
        if mat is StandardMaterial3D:
            cull = (mat as StandardMaterial3D).cull_mode
        elif mat is ShaderMaterial:
            cull = (mat as ShaderMaterial).get_shader_parameter("cull_mode")
        _say("MATERIAL=%s cull=%s" % [str(mat), str(cull)])
    # 碰撞体(玩家自己的):形状 / 尺寸 / 是不是 Area
    var col := player.get_node_or_null("Collision")
    if col is CollisionShape3D:
        var sh := (col as CollisionShape3D).shape
        _say("PLAYER_SHAPE=%s" % str(sh))
        if sh is CapsuleShape3D:
            _say("PLAYER_SHAPE_CAPSULE r=%.4f h=%.4f"
                 % [(sh as CapsuleShape3D).radius, (sh as CapsuleShape3D).height])
        elif sh is BoxShape3D:
            _say("PLAYER_SHAPE_BOX size=%.4f,%.4f,%.4f"
                 % [(sh as BoxShape3D).size.x, (sh as BoxShape3D).size.y,
                    (sh as BoxShape3D).size.z])
    _say("PLAYER_CLASS=%s layer=%d mask=%d"
         % [player.get_class(), player.collision_layer, player.collision_mask])

    # 等落地
    guard = 0
    while (not player.is_on_floor()) and guard < 400:
        await _wait(1)
        guard += 1
    var cs2 := get_tree().current_scene
    _say("LANDED on_floor=%s y=%.4f frame=%d scene=%s"
         % [str(player.is_on_floor()), player.global_position.y, frame,
            (String(cs2.name) if cs2 != null else "null")])
    # ── 第 128 轮:用户站的那个方块 / 那间屋子的天花板 ────────────────
    # 用户原话:"摄像机穿模是我站在**第三关卡的那个正方体上面**,然后跳跃,
    # 就出现了" —— 先把这两块几何的世界 AABB 量出来,后面每一帧都拿它算。
    if not box_name.is_empty() or not ceil_name.is_empty():
        var scene := get_tree().current_scene
        if scene != null:
            if not box_name.is_empty():
                var box := _find_named(scene, box_name)
                # ⚠ 这里必须分两句写:`var got := 函数() if 条件 else [false, AABB()]`
                # 这种"三元 + 类型推断"GDScript 推不出来,会报
                # `Invalid call arguments` 并让**整个 autoload 加载失败**
                # (第 128 轮踩过:探针没加载 -> 没人 quit -> Godot 挂着不退出)。
                var got: Array = [false, AABB()]
                if box != null:
                    got = _body_aabb(box)
                has_box = bool(got[0])
                box_aabb = got[1]
                # ⚠ 两条一起踩过:①GDScript 不支持 Python 式相邻字符串自动拼接;
                # ②`+` 和 `%` 的优先级和 Python 一样是 `%` 更高 ——
                # `"a" + "b" % x` 只会格式化后半截。所以整段拼接必须**加括号**。
                _say(("BOX %s found=%s aabb pos=(%.4f,%.4f,%.4f) "
                      + "size=(%.4f,%.4f,%.4f) 顶面 y=%.4f")
                     % [box_name, str(box != null), box_aabb.position.x,
                        box_aabb.position.y, box_aabb.position.z,
                        box_aabb.size.x, box_aabb.size.y, box_aabb.size.z,
                        box_aabb.position.y + box_aabb.size.y])
            if not ceil_name.is_empty():
                var ceil_node := _find_named(scene, ceil_name)
                var got2: Array = [false, AABB()]      # 同上:别用三元推断
                if ceil_node != null:
                    got2 = _body_aabb(ceil_node)
                has_ceil = bool(got2[0])
                ceil_aabb = got2[1]
                _say(("CEIL %s found=%s aabb pos=(%.4f,%.4f,%.4f) "
                      + "size=(%.4f,%.4f,%.4f) 底面 y=%.4f")
                     % [ceil_name, str(ceil_node != null),
                        ceil_aabb.position.x, ceil_aabb.position.y,
                        ceil_aabb.position.z, ceil_aabb.size.x,
                        ceil_aabb.size.y, ceil_aabb.size.z,
                        ceil_aabb.position.y])

## ── 第 129 轮:第一人称贴墙 / 贴墙角 / 低天花板 ──────────────────────
##
## 为什么单拎出来:第一人称相机**不过弹簧臂**(臂长 0.25、没有任何碰撞射线),
## 它每帧被直接摆到 `玩家位置 + (0,1.55,0) + 方向*0.25`。玩家胶囊沿 y 只有
## ±0.9 —— 也就是**"眼睛"比胶囊顶还高 0.65 米**。贴着墙走时身体挡得住它,
## 但**竖直方向没有任何东西挡**(低天花板、方块的顶、门楣)。
##
## 三个阶段:①背朝墙倒退;②贴墙角;③(再叠 `--box`)方块顶跳。
func _phase_fp_walk() -> void:
    await _tap("view_top" if measure_fp else "view_side")
    await _wait(10)
    _say("FP_START pos=(%.3f,%.3f,%.3f) cam1=(%.3f,%.3f,%.3f) 相机高出胶囊顶=%+.4f"
         % [player.global_position.x, player.global_position.y,
            player.global_position.z, cam1.global_position.x,
            cam1.global_position.y, cam1.global_position.z,
            cam1.global_position.y - (player.global_position.y + 0.9)])

    phase = "N_FP_WALL"
    Input.action_press("move_down")          # 倒退 -> 用后背把相机顶向墙
    await _wait(130)
    Input.action_release("move_down")
    await _wait(20)
    await _shot("N_fp_wall")
    _say("FP_WALL_STOP pos=(%.3f,%.3f,%.3f) cam1=(%.3f,%.3f,%.3f)"
         % [player.global_position.x, player.global_position.y,
            player.global_position.z, cam1.global_position.x,
            cam1.global_position.y, cam1.global_position.z])

    phase = "N_FP_CORNER"
    Input.action_press("move_down")
    Input.action_press("move_right")
    await _wait(130)
    Input.action_release("move_down")
    Input.action_release("move_right")
    await _wait(20)
    await _shot("N_fp_corner")
    _say("FP_CORNER_STOP pos=(%.3f,%.3f,%.3f) cam1=(%.3f,%.3f,%.3f)"
         % [player.global_position.x, player.global_position.y,
            player.global_position.z, cam1.global_position.x,
            cam1.global_position.y, cam1.global_position.z])

func _pose(pitch: float) -> void:
    player.set("输出_视角俯仰", pitch)

## ── 第 128 轮:站到方块顶上,站稳,再跳(用户报的那个场景)──────────
##
## 为什么要"传送 + 等站稳"而不是一开始就摆在那儿:弹簧臂的状态是**累积**的
## (平滑后的世界位置),突然传送会让相机横跨半个关卡;`GkWorld` 换关时
## 就是这么干的 —— 把 `_gk_spring_ready*` 清掉让它**直接吸附**到新位置。
func _phase_box() -> void:
    if not has_box:
        _say("SKIP 没有方块信息(用 --box=节点名)")
        return
    # ── 先切到**第三人称** ──────────────────────────────────────────
    # 示例把玩家选的视角存进 user:// 偏好里(GkPlayerPrefs),所以上一次跑
    # 留下的"第一人称"会让这一跑悄悄从第一人称开始 —— 那样模型是隐藏的,
    # 弹簧臂倒是照跑,但拍的图不是用户看到的画面(第 128 轮踩过)。
    # 第 129 轮反过来:`--fp` 时切到**第一人称**(这一轮查的就是它)。
    await _tap("view_top" if measure_fp else "view_side")
    await _wait(5)
    _say("CAM_CURRENT=%s(1=第一人称,3=第三人称)"
         % ("3" if (cam3 != null and cam3.current) else
            ("1" if (cam1 != null and cam1.current) else "?")))
    var top := box_aabb.position.y + box_aabb.size.y
    var center := box_aabb.position + box_aabb.size * 0.5
    var spawn := Vector3(center.x, top + 0.9 + 0.05, center.z)
    player.global_position = spawn
    player.velocity = Vector3.ZERO
    player.set("输出_视角偏航", 0.0)
    for prop in player.get_property_list():
        var pname := String(prop.name)
        if pname.begins_with("_gk_spring_ready") \
                or pname.begins_with("_gk_spring_base_yaw_ready"):
            player.set(pname, false)
    player.reset_physics_interpolation()
    var guard := 0
    while (not player.is_on_floor()) and guard < 200:
        await _wait(1)
        guard += 1
    _say("BOX_STAND pos=(%.4f,%.4f,%.4f) on_floor=%s 枢轴y=%.4f 天花板余量=%.4f"
         % [player.global_position.x, player.global_position.y,
            player.global_position.z, str(player.is_on_floor()),
            player.global_position.y + ARM_PIVOT_Y,
            (ceil_aabb.position.y - player.global_position.y - ARM_PIVOT_Y)
            if has_ceil else 999.0])

    phase = "M_BOX_IDLE"
    await _wait(90)
    await _shot("M_box_idle")

    phase = "M_BOX_JUMP"
    await _tap("jump")
    await _wait(20)
    await _shot("M_box_jump_up")
    # ── 按**条件**拍:相机跑到天花板上面那一瞬间 ─────────────────────
    # 那一段只有 25 帧(0.42 秒),按固定帧号拍很容易拍到窗口外面
    # (第 128 轮就拍空过一次)。这里逐帧盯着,一满足就拍。
    var ceil_top := ceil_aabb.position.y + ceil_aabb.size.y if has_ceil else 0.0
    var got_above := false
    for _i in 90:
        await _wait(1)
        if not shots or got_above or cam3 == null or not has_ceil:
            continue
        if cam3.global_position.y > ceil_top:
            got_above = true
            _say("SHOT_CONDITION 相机到天花板上面了:f=%d camy=%.4f "
                 + "(天花板顶面 %.4f)lp3=(%.3f,%.3f,%.3f)"
                 % [frame, cam3.global_position.y, ceil_top,
                    (player.global_transform.affine_inverse()
                     * cam3.global_position).x,
                    (player.global_transform.affine_inverse()
                     * cam3.global_position).y,
                    (player.global_transform.affine_inverse()
                     * cam3.global_position).z])
            await _shot("M_box_above_ceiling")
    await _shot("M_box_jump_after")
    var p := player.global_position
    _say("BOX_JUMP_DONE pos=(%.4f,%.4f,%.4f) on_floor=%s"
         % [p.x, p.y, p.z, str(player.is_on_floor())])

func _run() -> void:
    await _boot()
    if player == null:
        return
    _pose(12.0)
    # --only=box:直接跑"站方块顶"那一段(第 128 轮查用户报的场景时用,
    # 省掉 A~K 那一千多帧)
    # --only=fp :第 129 轮 —— 第一人称:先贴墙/贴墙角,再(给了 --box 就)上方块顶跳
    if only == "fp":
        await _phase_fp_walk()
        if has_box:
            await _phase_box()
        var ff := FileAccess.open("res://camclip_frames.txt", FileAccess.WRITE)
        if ff != null:
            for r in rows:
                ff.store_line(r)
            ff.close()
            _say("ROWS=%d" % rows.size())
        _say("DONE")
        await _wait(3)
        get_tree().quit()
        return
    if only == "box":
        await _phase_box()
        var f0 := FileAccess.open("res://camclip_frames.txt", FileAccess.WRITE)
        if f0 != null:
            for r in rows:
                f0.store_line(r)
            f0.close()
            _say("ROWS=%d" % rows.size())
        _say("DONE")
        await _wait(3)
        get_tree().quit()
        return

    # ── A 开阔地静止(出生点:背墙 3.75m)────────────────────────────
    phase = "A_IDLE"
    await _wait(90)
    await _shot("A_idle_third")

    # ── B 开阔地跳跃 ────────────────────────────────────────────────
    phase = "B_JUMP"
    await _tap("jump")
    await _wait(120)
    await _shot("B_jump_third")

    # ── C 倒退贴墙(真按键,不是传送)────────────────────────────────
    phase = "C_BACK_WALL"
    Input.action_press("move_down")
    for _i in 200:
        await _wait(1)
        if player.get_real_velocity().length() < 0.05 and _i > 40:
            break
    Input.action_release("move_down")
    await _wait(20)
    _say("WALL_STOP ppos=(%.3f,%.3f,%.3f) cam3=(%.3f,%.3f,%.3f)"
         % [player.global_position.x, player.global_position.y,
            player.global_position.z, cam3.global_position.x,
            cam3.global_position.y, cam3.global_position.z])

    # ── D 贴墙静止 ──────────────────────────────────────────────────
    phase = "D_WALL_IDLE"
    await _wait(60)
    await _shot("D_wall_third")

    # ── E 贴墙跳跃(用户说的"有时跳跃")─────────────────────────────
    phase = "E_WALL_JUMP"
    await _tap("jump")
    await _wait(40)                 # 起跳后 40 帧 ≈ 最高点(相机被顶得最近)
    await _shot("E_apex_model_on")
    # ── 同一帧再拍一张"模型藏起来"的 ────────────────────────────────
    # 两张相减 = 模型在画面上占了哪一块。相机真的贴到人身上时,
    # 这一块会大到占满屏幕中间(第 126 轮:走廊里 0.0385m,比近平面还近)。
    # ⚠ 必须只在 --shoot(窗口化)时做:无头那几跑里藏两帧模型会把后面
    #   每一帧的相位都推后 4 帧,B/C 两个变体就没法逐字节比了(踩过)。
    if shots and model != null:
        model.visible = false
        await _wait(2)
        await _shot("E_apex_model_off")
        model.visible = true
        await _wait(2)
    elif shots:
        await _shot("E_apex_model_off")
    await _wait(80)
    await _shot("E_walljump_after")

    # ── F 贴墙 + 俯仰 -20(往下看:相机会不会被地板顶住)───────────
    phase = "F_PITCH_DOWN"
    _pose(-20.0)
    await _wait(90)
    await _shot("F_wall_pitchdown")
    _pose(12.0)
    await _wait(20)

    # ── G 切第一人称 ────────────────────────────────────────────────
    phase = "G_SWITCH_FP"
    await _tap("view_top")
    await _wait(60)
    await _shot("G_first_person")

    # ── H 第一人称里跳 ──────────────────────────────────────────────
    phase = "H_FP_JUMP"
    await _tap("jump")
    await _wait(120)
    await _shot("H_fp_jump")

    # ── I 切回第三人称(贴墙)────────────────────────────────────────
    phase = "I_SWITCH_TP"
    await _tap("view_side")
    await _wait(60)
    await _shot("I_back_third")

    # ── J 跳跃 + 半空切视角(用户原话的合体)─────────────────────────
    phase = "J_JUMP_SWITCH"
    await _tap("jump")
    await _wait(20)
    await _tap("view_top")
    await _wait(25)
    await _tap("view_side")
    await _wait(90)
    await _shot("J_after_switch")

    # ── K 离开墙:向前走 1.5 秒再切视角(对照组)─────────────────────
    phase = "K_WALK_AWAY"
    Input.action_press("move_up")
    await _wait(90)
    Input.action_release("move_up")
    await _wait(30)
    phase = "K_SWITCH_FP2"
    await _tap("view_top")
    await _wait(20)
    phase = "K_SWITCH_TP2"
    await _tap("view_side")
    await _wait(40)
    await _shot("K_away_third")

    # ── 收尾:把逐帧表落盘 ─────────────────────────────────────────
    var f := FileAccess.open("res://camclip_frames.txt", FileAccess.WRITE)
    if f != null:
        for r in rows:
            f.store_line(r)
        f.close()
        _say("ROWS=%d" % rows.size())
    _say("DONE")
    await _wait(3)
    get_tree().quit()
