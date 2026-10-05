"""示例:**曲线运镜 + 时间轴 + 触发器** 一关打包(用户 2026-10-04 第十三轮)。

用户原话:"设计一个示例文件来尽量包含上述我未测试的新功能,这样方便我测试"。

这个示例想让你**一进去就能看见每一样新东西**,不用先学会怎么连节点:

| 要测的 | 在示例里怎么看 |
|---|---|
| 曲线运镜(沿曲线飞) | **一进游戏**镜头就从第三人称机位沿一条画好的曲线飞到门口的特写机位(2.5 秒,缓出);按 **E** 随时再看一遍 |
| 过渡曲线(缓动档位) | 开场运镜用的是『缓出』;按 Q 回第三人称是直线 1 秒 —— 对比手感 |
| 运镜轨道预览 | 在 Blender 里选中『开场运镜』节点 → 点 **『预览运镜』**,视口里直接跑一遍 |
| 时间轴(虚幻味) | 『开场时间轴』:2.5 秒,进度曲线=缓动曲线;两条轨道分别**抬门**(1.5→4.7)和**推近镜头视野**(60→32);走完打印"开场完成" |
| 曲线轨道(3D) | 时间轴的『曲线轨道』用的就是那条 3D 曲线(和相机飞的是同一条) |
| 触发器 | 走到门口的**方框**里 → 重新播一遍开场(门 + 镜头) |
| 角色逻辑预设 | 整个角色控制(WASD / 空格跳 / 鼠标转视角 / 弹簧臂 / 滚轮缩放 / C·V 切机位)都是预设生成的 |
| 上帝摄像机 | 按 **Tab** 切自由飞行相机(WASD 飞 / 空格升降 / 鼠标转视角);没有角色也能用 |
| 粒子特效(需求 5) | 门边『火把』冒着火(GPU 粒子 + 叠加发光)、空中『烟雾』(空物体 + CPU 粒子)、门顶『火星』(勾了『不显示本体网格』的一次性火花);**J** 点火把 / **L** 灭 / **K** 爆火花 |
| 粒子节点 | 上面三个键分别走『特效_播放』『特效_停止』『特效_爆发』;发射器参数在 Blender 里选中物体 →『游戏物理』→『粒子特效』 |

操作(游戏里):
    WASD 移动 / 空格 跳 / 鼠标 转视角 / 滚轮 缩放 / C 俯瞰 V 侧视
    E 开场运镜(沿曲线)/ Q 回第三人称 / Tab 上帝机 / 走进门口方框 重播开场
    J 点火把 / L 灭火把 / K 爆火花(粒子特效:播放 / 停止 / 爆发)

跑法:
    blender -b --python scripts\\make_curve_demo.py
之后在 Godot 里打开 `examples/demo_curve/曲线时间轴示例_godot`(主场景 World.tscn)。
"""

import math
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "examples", "demo_curve")
BLEND_PATH = os.path.join(OUT_DIR, "曲线时间轴示例.blend")

# 机位(Blender 坐标;Godot 是 (x, z, -y))——
#   * 侧视机位:第三人称的家(弹簧臂挂在这台)
#   * 俯瞰机位:C 键切过去
#   * 特写机位:开场运镜的**目标**(在门口)
SIDE_CAM = (0.0, -13.0, 3.6)
TOP_CAM = (0.0, -6.0, 16.0)
CLOSE_CAM = (0.0, 9.0, 4.6)
# 运镜轨道:起点贴着侧视机位,中途**往侧面鼓一个大弯**,终点贴着特写机位。
# 鼓弯是故意的 —— "直线过渡"和"沿曲线飞"的差别一眼就能看出来。
TRACK_POINTS = [SIDE_CAM, (11.0, -4.0, 9.0), (7.0, 4.0, 6.0), CLOSE_CAM]
# 缓动曲线:慢起快停(画在 XY 平面上,导出成 Godot 的 Curve 资源)
EASE_POINTS = [(0.0, 0.0, 0.0), (0.45, 0.85, 0.0), (1.0, 1.0, 0.0)]


def log(message):
    print(f"[曲线示例] {message}")


# ==========================================================================
# 场景
# ==========================================================================
def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for group in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(group)
    for curve in list(bpy.data.curves):
        bpy.data.curves.remove(curve)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)
    scene = bpy.context.scene
    scene.name = "World"          # 单场景 -> 导出的主场景还是 World.tscn
    scene.unit_settings.system = "METRIC"
    return scene


def make_box(name, size, location, color=None, scene=None):
    sx, sy, sz = [v * 0.5 for v in size]
    verts = [(-sx, -sy, -sz), (sx, -sy, -sz), (sx, sy, -sz), (-sx, sy, -sz),
             (-sx, -sy, sz), (sx, -sy, sz), (sx, sy, sz), (-sx, sy, sz)]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5),
             (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh = bpy.data.meshes.new(f"{name}网格")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    (scene or bpy.context.scene).collection.objects.link(obj)
    obj.location = location
    if color:
        material = bpy.data.materials.new(f"{name}材质")
        material.use_nodes = True
        node = material.node_tree.nodes.get("Principled BSDF")
        if node is not None:
            node.inputs["Base Color"].default_value = color
        obj.data.materials.append(material)
    return obj


def make_camera(name, location, rotation):
    data = bpy.data.cameras.new(f"{name}数据")
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    obj.rotation_euler = rotation
    return obj


def make_curve(name, points, dimensions="3D"):
    """一条贝塞尔曲线(控制点 + 自动手柄)。"""
    data = bpy.data.curves.new(f"{name}数据", type="CURVE")
    data.dimensions = dimensions
    spline = data.splines.new("BEZIER")
    spline.bezier_points.add(len(points) - 1)
    for handle, co in zip(spline.bezier_points, points):
        handle.co = co
        handle.handle_left_type = "AUTO"
        handle.handle_right_type = "AUTO"
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def build_scene():
    scene = clear_scene()
    # 地面 + 两面墙(墙是给弹簧臂"碰墙缩短"用的,顺便看看不穿墙)
    make_box("地面", (26.0, 26.0, 0.4), (0.0, 0.0, -0.2),
             (0.30, 0.42, 0.30, 1.0))
    make_box("北墙", (26.0, 0.6, 4.0), (0.0, 13.0, 2.0),
             (0.55, 0.55, 0.58, 1.0))
    make_box("东墙", (0.6, 26.0, 4.0), (13.0, 0.0, 2.0),
             (0.55, 0.55, 0.58, 1.0))
    # 角色(挂脚本的那个)+ 门 + 轨道标记
    character = make_box("测试角色", (1.0, 1.0, 1.6), (0.0, -9.0, 0.8),
                         (0.95, 0.55, 0.25, 1.0))
    make_box("门", (4.0, 0.5, 3.0), (0.0, 6.5, 1.5), (0.35, 0.55, 0.95, 1.0))
    make_box("轨道标记", (0.6, 0.6, 0.6), TRACK_POINTS[0],
             (0.95, 0.85, 0.25, 1.0))
    # 触发区(门口那个方框;Area3D,视口里只画线框)
    trigger = make_box("触发区", (6.0, 6.0, 3.2), (0.0, 3.0, 1.6),
                       (0.35, 0.85, 0.95, 1.0))
    trigger.display_type = "WIRE"        # 视口里别挡视线
    # ── 静态资产(用户 2026-10-05:"游戏静态资产设计")──────────────
    # 一棵树摆三份、一块石头摆两份:模型各导**一次**,实例各自带碰撞 +
    # 自带网格。测的是『游戏物理』里的『资产名 / 作为实例』+ 侧栏『场景资产』。
    tree_color = (0.22, 0.55, 0.28, 1.0)
    rock_color = (0.55, 0.55, 0.60, 1.0)
    trees = [
        make_box("树_本体", (1.2, 1.2, 3.2), (-8.0, -4.0, 1.6), tree_color),
        make_box("树_实例1", (1.2, 1.2, 3.2), (-10.6, -1.0, 1.6), tree_color),
        make_box("树_实例2", (1.2, 1.2, 3.2), (-5.4, -1.0, 1.6), tree_color),
    ]
    rocks = [
        make_box("石头_本体", (1.8, 1.8, 1.0), (-3.0, -6.5, 0.5), rock_color),
        make_box("石头_实例", (1.8, 1.8, 1.0), (-1.0, -8.5, 0.5), rock_color),
    ]
    # ── 粒子特效(需求 5,2026-10-05 第二十一轮)──────────────────────
    # 三个发射器各演示一种用法(参数在 write_fx 里填):
    #   * 火把(GPU + 叠加发光)= 循环火焰,J/L 点着 / 灭掉,K 再喷一轮;
    #   * 烟雾(空物体 + CPU)= 只标位置和方向,没有网格;
    #   * 火星(勾『不显示本体网格』+ 一次性)= 空中一个看不见的喷点,
    #     每按一次 K 爆一轮火花(带重力的那种)。
    torch = make_box("火把", (0.5, 0.5, 1.0), (2.4, 4.6, 0.5),
                     (0.55, 0.35, 0.2, 1.0))
    spark = make_box("火星", (0.3, 0.3, 0.3), (0.0, 6.5, 3.1),
                     (1.0, 0.8, 0.3, 1.0))
    smoke = bpy.data.objects.new("烟雾", None)     # 空物体:天然就是"纯标记"
    scene.collection.objects.link(smoke)
    smoke.location = (-2.6, 5.2, 1.8)
    # 相机
    side = make_camera("侧视机位", SIDE_CAM, (math.radians(70.0), 0.0, 0.0))
    make_camera("俯瞰机位", TOP_CAM, (math.radians(20.0), 0.0, 0.0))
    make_camera("特写机位", CLOSE_CAM,
                (math.radians(78.0), 0.0, math.radians(12.0)))
    scene.camera = side
    # 灯(glb 不带灯,环境脚本会按这盏太阳光补一盏)
    light_data = bpy.data.lights.new("太阳光数据", type="SUN")
    light_data.energy = 3.0
    light = bpy.data.objects.new("太阳光", light_data)
    scene.collection.objects.link(light)
    light.rotation_euler = (math.radians(50.0), 0.0, math.radians(30.0))
    # 两条曲线
    make_curve("运镜轨道", TRACK_POINTS, dimensions="3D")
    make_curve("缓动曲线", EASE_POINTS, dimensions="2D")
    return scene, character, trigger, trees, rocks


def write_physics(scene, character, trigger, trees, rocks):
    """物理类型:角色 = CharacterBody3D + 挂脚本;触发区 = Area3D;其它静态。

    静态资产:树 / 石头分别填『资产名』,第一份是本体的那一份,
    其余勾上『作为实例』(见『游戏物理』面板的『静态资产』那一栏)。
    """
    asset_of = {}
    for index, obj in enumerate(trees):
        asset_of[obj.name] = ("树", index > 0)
    for index, obj in enumerate(rocks):
        asset_of[obj.name] = ("石头", index > 0)
    for obj in scene.objects:
        physics = getattr(obj, "gk_physics", None)
        if physics is None:
            continue
        try:
            physics.shape_size = (0.0, 0.0, 0.0)      # 全 0 = 自动贴合
        except Exception:                              # noqa: BLE001
            pass
        if obj is character:
            physics.enabled = True
            physics.body_type = "CharacterBody3D"
            physics.dimension = "3"
            physics.script_target = True
        elif obj is trigger:
            physics.enabled = True
            physics.body_type = "Area3D"
            physics.dimension = "3"
            physics.is_trigger = True
        elif getattr(obj, "type", "") == "MESH":
            physics.enabled = True
            physics.body_type = "StaticBody3D"
            physics.dimension = "3"
        info = asset_of.get(obj.name)
        if info is not None:
            physics.asset_name = info[0]
            physics.asset_instance = info[1]


def write_fx(scene):
    """粒子发射器参数(『游戏物理』→『粒子特效』面板里填的那批)。

    ── 为什么 Blender 侧只是"参数"(需求 5)──────────────────────────
    Blender 的粒子**导不进 glTF**,所以这边只标"谁是发射器 + 参数多少",
    导出时在 Godot 里生成真粒子节点(见 docs/待办清单.md 第二十一轮)。

    三个发射器各演示一种用法:
      * 火把:GPU 循环火焰(叠加发光 + 往上飘),J 点着 / L 灭 / K 重喷;
      * 烟雾:**空物体**发射器 + CPU 粒子(没有网格,只标位置和方向);
      * 火星:勾『不显示本体网格』+ 一次性,每按一次 K 爆一轮火花。
    """
    def fx_for(name):
        obj = scene.objects.get(name)
        return getattr(obj, "gk_fx", None) if obj is not None else None

    torch = fx_for("火把")
    if torch is not None:
        torch.enabled = True
        torch.amount = 32
        torch.lifetime = 0.9
        torch.speed = 1.5
        torch.spread = 18.0
        torch.gravity = -0.6          # 负数 = 往上飘(火苗)
        torch.radius = 0.12
        torch.start_size = 0.22
        torch.end_size = 0.02
        torch.additive = True         # 火 / 光用叠加发光

    smoke = fx_for("烟雾")
    if smoke is not None:
        smoke.enabled = True
        smoke.particle_type = "cpu"   # CPU 粒子:兼容性最好,烟雾够用
        smoke.amount = 48
        smoke.lifetime = 2.2
        smoke.speed = 0.8
        smoke.spread = 45.0
        smoke.gravity = -0.5
        smoke.radius = 0.3
        smoke.start_size = 0.35
        smoke.end_size = 1.1          # 烟雾越飘越大
        smoke.local_coords = False    # 喷出去留在世界里(不跟着物体走)
        smoke.start_color = (0.6, 0.6, 0.65, 0.4)
        smoke.end_color = (0.35, 0.35, 0.4, 0.0)

    spark = fx_for("火星")
    if spark is not None:
        spark.enabled = True
        spark.hide_mesh = True        # 只当喷点:本体网格不导出
        spark.auto_play = False       # 要靠 K 键『特效_爆发』才喷
        spark.one_shot = True
        spark.explosiveness = 1.0
        spark.amount = 28
        spark.lifetime = 0.9
        spark.speed = 4.5
        spark.spread = 55.0
        spark.gravity = 7.0           # 火花四散后往下掉
        spark.radius = 0.1
        spark.start_size = 0.12
        spark.end_size = 0.0
        spark.additive = True


# ==========================================================================
# 节点图:预设角色 + 这一轮的新功能
# ==========================================================================
def build_graph(scene, character):
    from BlenderGameKit import presets

    logic, _anim, notes = presets.create_character_preset(
        scene, character=character,
        logic_name="曲线时间轴逻辑",
        class_name="CurveDemoPlayer",
        # 没有骨骼动画模型 —— 不建动画树(建了也只会在运行时告警)
        with_animation=False,
        with_godot_camera=True,          # Tab 上帝机
        camera_side="侧视机位", camera_top="俯瞰机位",
    )
    for note in notes:
        log(f"预设: {note}")
    extend_graph(logic, scene)
    return logic


def _add(tree, kind, name, x, y, **props):
    node = tree.nodes.new(kind)
    node.name = name
    node.location = (x, y)
    for key, value in props.items():
        setattr(node, key, value)
    return node


def _link(tree, source, source_pin, target, target_pin):
    tree.links.new(source.outputs[source_pin], target.inputs[target_pin])


def _by_name(tree, name):
    node = tree.nodes.get(name)
    if node is None:
        raise KeyError(f"节点图里没有『{name}』")
    return node


def extend_graph(tree, scene):
    """在预设的图上接着加:开场运镜 + 时间轴 + 触发器 + 这次的新节点。

    ── 『重播』执行口(用户 2026-10-05,代替以前的"变量当播放请求")────
    Blender 里**一个输入引脚只允许一条连线**,所以『时间轴』的『执行』
    只能有一个上游。以前想"开场自动播 + 按 R / 走进门再播一遍",只能拿一个
    变量当"播放请求"、再用分支去启动(第十三轮的做法)。

    现在『定时_时间轴』多了一个 **『重播』** 执行输入:两个入口做的事一样
    (时间归零、从头播),各接一条线互不打架:

        开场运镜『完成』 → 时间轴『执行』      ← 开场自动播一次
        R 键分支『是』   → 时间轴『重播』      ← 想再看一遍就按 R

    ── 这次新加的三个节点也在这张图里 ──────────────────────────────
    * 『资产_创建实例』(X 键 / 走进门口):运行时按资产名长一棵新树;
    * 『物理_启用碰撞』(Z 键关 / G 键开):把门的碰撞关掉,人就能走过去;
    * 『实例_销毁』(B 键):把某一份指定名字的树清掉。
    """
    ready_tail = _by_name(tree, "设定初始视野")     # 预设 ready 链的最后一环
    physics = _by_name(tree, "每当_物理帧")

    # ---- ① HUD 操作提示(自己按着玩需要知道能按什么)----
    hint = _add(tree, "GKNodeUiText", "操作提示", -300.0, 900.0,
                gk_text=("WASD 移动 / 空格 跳 / 鼠标 转视角 / 滚轮 缩放 / "
                         "C 俯瞰 V 侧视 / E 开场运镜 / Q 回第三人称 / "
                         "R 重播场景动画 / X 长一棵树 / B 清掉一棵树 / "
                         "Z 关门碰撞 G 开门碰撞 / Tab 上帝机 / "
                         "J 点火把 L 灭火把 K 爆火花(粒子特效)/ "
                         "走进门口方框 也长一棵树 / Esc 退出"),
                gk_x=20.0, gk_y=20.0, gk_size=18)
    _link(tree, ready_tail, "完成", hint, "执行")

    # ---- ② 开场运镜:沿『运镜轨道』飞到特写机位(过渡曲线=缓出)----
    opening = _add(tree, "GKNodeCameraSetCurrent", "开场运镜", 0.0, 900.0,
                   gk_target="特写机位", gk_blend=2.5,
                   gk_blend_trans="EASE_OUT",
                   gk_path_mode="CURVE", gk_path_curve="运镜轨道")
    _link(tree, hint, "完成", opening, "执行")

    # ---- ③ 时间轴:抬门(轨道1)+ 推近镜头(轨道2)+ 3D 曲线轨道 ----
    timeline = _add(tree, "GKNodeTimeline", "开场时间轴", 0.0, 520.0,
                    gk_time=2.5, gk_mode="ONCE",
                    gk_curve="缓动曲线",              # 进度按画的曲线走
                    gk_track_count=2,
                    gk_track1_from=1.5, gk_track1_to=7.0,    # 门:往上抬
                    gk_track2_from=60.0, gk_track2_to=32.0,  # 视野:推近
                    gk_path_curve="运镜轨道")        # 3D 曲线(和相机同一条)
    # 『执行』← 开场运镜跑完(一进游戏自动播一次)
    _link(tree, opening, "完成", timeline, "执行")

    # 更新 → 抬门:Y 用轨道值,X / Z 用门**当前**的坐标(所以门只会上下动)
    door_pos = _add(tree, "GKNodeGetPosition", "取门位置", -520.0, 360.0,
                    gk_target="门", gk_global=True)
    lift = _add(tree, "GKNodeSetPosition", "抬门", -220.0, 360.0,
                gk_target="门")
    _link(tree, door_pos, "X", lift, "X")
    _link(tree, timeline, "轨道1值", lift, "Y")
    _link(tree, door_pos, "Z", lift, "Z")
    _link(tree, timeline, "更新", lift, "执行")

    # 更新 → 推近视野:数值 = 轨道2值(推的是特写机位,当前就是它)
    zoom = _add(tree, "GKNodeCameraZoom", "推近视野", -220.0, 120.0,
                gk_target="特写机位", gk_time=0.0)
    _link(tree, timeline, "轨道2值", zoom, "数值")
    _link(tree, lift, "完成", zoom, "执行")

    # 更新 → 轨道标记:位置 = 曲线上的点(肉眼看曲线长什么样)
    marker_split = _add(tree, "GKNodeVectorSplit", "拆曲线位置", -520.0, -60.0)
    marker_set = _add(tree, "GKNodeSetPosition", "标记沿轨道走",
                      -220.0, -60.0, gk_target="轨道标记")
    _link(tree, timeline, "曲线位置", marker_split, "向量")
    _link(tree, marker_split, "X", marker_set, "X")
    _link(tree, marker_split, "Y", marker_set, "Y")
    _link(tree, marker_split, "Z", marker_set, "Z")
    _link(tree, zoom, "完成", marker_set, "执行")

    # 已结束 → 打印一行(控制台里能确认"走完了")
    done = _add(tree, "GKNodePrint", "开场结束", -220.0, -260.0,
                gk_text="开场完成:门已升起、镜头已推近、轨迹标记已到终点")
    _link(tree, timeline, "已结束", done, "执行")

    # ---- ④ 新:走进门口方框 → 长一棵树(运行时创建资产实例)----
    area = _add(tree, "GKNodeOnAreaEntered", "走进触发区", 0.0, 120.0,
                gk_area="触发区", gk_filter="测试角色")
    area_hint = _add(tree, "GKNodePrint", "进门提示", 320.0, 120.0,
                     gk_text="走进触发区:长一棵新树(资产_创建实例)")
    grow_by_area = _add(tree, "GKNodeAssetSpawn", "长一棵树(进门)",
                        640.0, 120.0,
                        gk_asset="树", gk_solid=True,
                        gk_x=6.0, gk_y=0.0, gk_z=6.0)
    _link(tree, area, "执行", area_hint, "执行")
    _link(tree, area_hint, "完成", grow_by_area, "执行")

    # ---- ⑤ 按键 E / Q / R / Esc:运镜、回第三人称、重播、退出 ----
    key_e = _add(tree, "GKNodeKey", "E 键", -900.0, -520.0,
                 gk_key="E", gk_action="key_e", gk_trigger="JUST")
    branch_e = _add(tree, "GKNodeBranch", "按了 E?", -500.0, -520.0)
    cam_e = _add(tree, "GKNodeCameraSetCurrent", "运镜到特写", -200.0, -520.0,
                 gk_target="特写机位", gk_blend=1.8,
                 gk_blend_trans="EASE_IN_OUT",
                 gk_path_mode="CURVE", gk_path_curve="运镜轨道")
    key_q = _add(tree, "GKNodeKey", "Q 键", -900.0, -760.0,
                 gk_key="Q", gk_action="key_q", gk_trigger="JUST")
    branch_q = _add(tree, "GKNodeBranch", "按了 Q?", -500.0, -760.0)
    cam_q = _add(tree, "GKNodeCameraSetCurrent", "回第三人称", -200.0, -760.0,
                 gk_target="侧视机位", gk_blend=1.0)
    _link(tree, key_e, "结果", branch_e, "条件")
    _link(tree, physics, "执行", branch_e, "执行")
    _link(tree, branch_e, "是", cam_e, "执行")
    _link(tree, key_q, "结果", branch_q, "条件")
    _link(tree, physics, "执行", branch_q, "执行")
    _link(tree, branch_q, "是", cam_q, "执行")

    # ---- ⑥ R:时间轴『重播』(这次不用变量绕圈子了)----
    # 这一段还演示**自定义函数**:R 之后的"提示 + 重播"收进一个函数,
    # 调用点留在按键分支上(这正是右键『折叠成函数』会做出来的形状 ——
    # 这里手写出来,是为了让示例在没有界面时也能重建)。
    # **上帝视角下按它最好用** —— 上帝视角期间相机过渡是暂停的,
    # 你可以飞高了慢慢看整套场景动画重新跑一遍。
    func = tree.gk_funcs.add()
    func.func_name = "重播开场"
    func.return_type = "void"
    func.params = ""
    func.comment = "R 键:重播整套场景动画(『折叠成函数』的形状)"
    key_r = _add(tree, "GKNodeKey", "R 键", -900.0, -1000.0,
                 gk_key="R", gk_action="key_r", gk_trigger="JUST")
    branch_r = _add(tree, "GKNodeBranch", "按了 R?", -500.0, -1000.0)
    call_r = _add(tree, "GKNodeFunctionCall", "调用_重播开场", -200.0, -1000.0,
                  gk_func_name="重播开场")
    _link(tree, key_r, "结果", branch_r, "条件")
    _link(tree, physics, "执行", branch_r, "执行")
    _link(tree, branch_r, "是", call_r, "执行")
    # 函数体:定义 → 提示 → 时间轴『重播』执行口
    define_r = _add(tree, "GKNodeFunctionDefine", "定义_重播开场",
                    -200.0, -1240.0, gk_func_name="重播开场")
    hint_r = _add(tree, "GKNodePrint", "R 提示", 120.0, -1240.0,
                  gk_text="R:重播场景动画(先按 Tab 上帝机,再按 R 最好看)")
    _link(tree, define_r, "执行", hint_r, "执行")
    # 『重播』执行口 —— 和开场的『执行』各是一条线,互不打架
    _link(tree, hint_r, "完成", timeline, "重播")

    # ---- ⑦ X / B:长一棵树 / 清掉一棵树 ----
    grow_key = _add(tree, "GKNodeKey", "X 键", -900.0, -1480.0,
                    gk_key="X", gk_action="key_x", gk_trigger="JUST")
    grow_branch = _add(tree, "GKNodeBranch", "按了 X?", -500.0, -1480.0)
    grow = _add(tree, "GKNodeAssetSpawn", "长一棵树", -200.0, -1480.0,
                gk_asset="树", gk_solid=True, gk_x=-6.0, gk_y=0.0, gk_z=6.0)
    grow_hint = _add(tree, "GKNodePrint", "长树提示", 80.0, -1480.0,
                     gk_text="X:长了一棵新树(复用资产『树』的模型 + 碰撞)")
    _link(tree, grow_key, "结果", grow_branch, "条件")
    _link(tree, physics, "执行", grow_branch, "执行")
    _link(tree, grow_branch, "是", grow, "执行")
    _link(tree, grow, "完成", grow_hint, "执行")

    kill_key = _add(tree, "GKNodeKey", "B 键", -900.0, -1720.0,
                    gk_key="B", gk_action="key_b", gk_trigger="JUST")
    kill_branch = _add(tree, "GKNodeBranch", "按了 B?", -500.0, -1720.0)
    kill = _add(tree, "GKNodeQueueFree", "清掉一棵树", -200.0, -1720.0,
                gk_target="树_实例2")
    kill_hint = _add(tree, "GKNodePrint", "清树提示", 80.0, -1720.0,
                     gk_text="B:清掉了『树_实例2』(实例_销毁)")
    _link(tree, kill_key, "结果", kill_branch, "条件")
    _link(tree, physics, "执行", kill_branch, "执行")
    _link(tree, kill_branch, "是", kill, "执行")
    _link(tree, kill, "完成", kill_hint, "执行")

    # ---- ⑧ Z / G:关掉 / 打开门的碰撞(开完门让人走过去)----
    # 『物理_启用碰撞』:关掉门的碰撞 -> 人可以直接穿过去;
    # 再按 G 打开,门又能挡人(门是抬起来的,挡的是"横着的门板"那一层)。
    off_key = _add(tree, "GKNodeKey", "Z 键", -900.0, -1960.0,
                   gk_key="Z", gk_action="key_z", gk_trigger="JUST")
    off_branch = _add(tree, "GKNodeBranch", "按了 Z?", -500.0, -1960.0)
    collision_off = _add(tree, "GKNodeSetCollision", "关门的碰撞",
                         -200.0, -1960.0, gk_target="门", gk_enable=False)
    _link(tree, off_key, "结果", off_branch, "条件")
    _link(tree, physics, "执行", off_branch, "执行")
    _link(tree, off_branch, "是", collision_off, "执行")

    on_key = _add(tree, "GKNodeKey", "G 键", -900.0, -2200.0,
                  gk_key="G", gk_action="key_g", gk_trigger="JUST")
    on_branch = _add(tree, "GKNodeBranch", "按了 G?", -500.0, -2200.0)
    collision_on = _add(tree, "GKNodeSetCollision", "开门的碰撞",
                        -200.0, -2200.0, gk_target="门", gk_enable=True)
    _link(tree, on_key, "结果", on_branch, "条件")
    _link(tree, physics, "执行", on_branch, "执行")
    _link(tree, on_branch, "是", collision_on, "执行")

    # ---- ⑨ J / K / L:粒子特效(需求 5,2026-10-05 第二十一轮)----
    # 三个节点各演示一件事(发射器在 Blender 里就摆在门边上):
    #   J = 『特效_播放』把火把点着;L = 『特效_停止』灭火
    #   K = 『特效_爆发』:火星(一次性,停在门顶上看不见的喷点)再爆一轮,
    #       **接着**火把也重新喷一轮 —— 顺便演示"两个爆发串成一条链"。
    key_j = _add(tree, "GKNodeKey", "J 键", -900.0, -2680.0,
                 gk_key="J", gk_action="key_j", gk_trigger="JUST")
    branch_j = _add(tree, "GKNodeBranch", "按了 J?", -500.0, -2680.0)
    fx_play = _add(tree, "GKNodeFxPlay", "点燃火把", -200.0, -2680.0,
                   gk_target="火把")
    hint_j = _add(tree, "GKNodePrint", "点火提示", 80.0, -2680.0,
                  gk_text="J:火把点着了(特效_播放)")
    _link(tree, key_j, "结果", branch_j, "条件")
    _link(tree, physics, "执行", branch_j, "执行")
    _link(tree, branch_j, "是", fx_play, "执行")
    _link(tree, fx_play, "完成", hint_j, "执行")

    key_l = _add(tree, "GKNodeKey", "L 键", -900.0, -2920.0,
                 gk_key="L", gk_action="key_l", gk_trigger="JUST")
    branch_l = _add(tree, "GKNodeBranch", "按了 L?", -500.0, -2920.0)
    fx_stop = _add(tree, "GKNodeFxStop", "灭火把", -200.0, -2920.0,
                   gk_target="火把")
    hint_l = _add(tree, "GKNodePrint", "灭火提示", 80.0, -2920.0,
                  gk_text="L:火把灭了(特效_停止;已经在飞的火苗会自己烧完)")
    _link(tree, key_l, "结果", branch_l, "条件")
    _link(tree, physics, "执行", branch_l, "执行")
    _link(tree, branch_l, "是", fx_stop, "执行")
    _link(tree, fx_stop, "完成", hint_l, "执行")

    key_k = _add(tree, "GKNodeKey", "K 键", -900.0, -3160.0,
                 gk_key="K", gk_action="key_k", gk_trigger="JUST")
    branch_k = _add(tree, "GKNodeBranch", "按了 K?", -500.0, -3160.0)
    burst_spark = _add(tree, "GKNodeFxBurst", "火星爆一轮", -200.0, -3160.0,
                       gk_target="火星")
    burst_torch = _add(tree, "GKNodeFxBurst", "火把重喷", 80.0, -3160.0,
                       gk_target="火把")
    hint_k = _add(tree, "GKNodePrint", "爆发提示", 360.0, -3160.0,
                  gk_text="K:火花爆了一轮,火把也重新喷(特效_爆发)")
    _link(tree, key_k, "结果", branch_k, "条件")
    _link(tree, physics, "执行", branch_k, "执行")
    _link(tree, branch_k, "是", burst_spark, "执行")
    _link(tree, burst_spark, "完成", burst_torch, "执行")
    _link(tree, burst_torch, "完成", hint_k, "执行")

    # Esc:退出游戏(用户 2026-10-05:"按 esc 无法退出")
    # ⚠ 每个按键分支都从『每当_物理帧』的『执行』扇出 —— 这样每个按键每帧
    # 都会被检查一遍;串成一条链的话,"先按了 E 才轮到检查 Q"。
    key_esc = _add(tree, "GKNodeKey", "Esc 键", -900.0, -2440.0,
                   gk_key="ESC", gk_action="key_esc", gk_trigger="JUST")
    branch_esc = _add(tree, "GKNodeBranch", "按了 Esc?", -500.0, -2440.0)
    quit_game = _add(tree, "GKNodeQuitGame", "退出游戏", -200.0, -2440.0)
    _link(tree, key_esc, "结果", branch_esc, "条件")
    _link(tree, physics, "执行", branch_esc, "执行")
    _link(tree, branch_esc, "是", quit_game, "执行")

    log(f"扩展示意图:共 {len(tree.nodes)} 个节点 / {len(tree.links)} 条连线")


def save_blend(path):
    """保存 .blend。

    ── 为什么无头模式不用 `save_as_mainfile`(实测,和插件无关)────────
    这台机器上"无头 Blender + 材质 + 保存"会触发**材质预览渲染**
    (Fra:1 … Rendering 4 / 4 samples),然后 EEVEE 预览在无 GPU 环境里崩:
        EXCEPTION_ACCESS_VIOLATION ← lib_id_generate_preview_exec
    最小复现:新建一个 `use_nodes=True` 的材质,`wm.save_as_mainfile`,必崩;
    连 `preferences.filepaths.file_preview_type = 'NONE'` 也拦不住。
    `bpy.data.libraries.write` 不生成预览,数据一样全 —— 用户在 GUI 里
    打开就是这张场景(默认工作区)。GUI 里跑就正常走 save_as_mainfile。
    """
    if not bpy.app.background:
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return
    blocks = set(bpy.data.objects) | set(bpy.data.meshes) | \
        set(bpy.data.materials) | set(bpy.data.curves) | \
        set(bpy.data.node_groups) | set(bpy.data.cameras) | \
        set(bpy.data.lights)
    scenes = list(bpy.data.scenes)
    bpy.data.libraries.write(path, blocks | set(scenes), fake_user=True)


# ==========================================================================
# 存 .blend + 导出
# ==========================================================================
def main():
    from BlenderGameKit import asset_export, project

    os.makedirs(OUT_DIR, exist_ok=True)
    scene, character, trigger, trees, rocks = build_scene()
    write_physics(scene, character, trigger, trees, rocks)
    write_fx(scene)
    build_graph(scene, character)
    # ── 游戏内调试面板(第二十二轮):这一关默认开着 ─────────────────
    # 游戏里按 **F1** 弹出/收起、**F2** 把全部内容复制到剪贴板。
    # 开关和按键在『导出为 Godot 脚本』框里(『调试面板』那一行)。
    # 『变量』框里每个变量行尾那个"眼睛"图标 = 要不要上面板
    # (一个都不勾 = 面板显示脚本里所有变量)。
    scene.gk_settings.debug_panel = True

    # 先建工程(project.godot)—— 不然输入映射和主场景都写不进去,
    # Godot 里按 F5 还得手动设主场景
    directory = os.path.join(OUT_DIR, "曲线时间轴示例_godot")
    project.create_project(directory, title="曲线时间轴示例",
                           blend_file_name=os.path.basename(BLEND_PATH))

    # 导出(glTF 也会碰 GPU;保存放在最后,顺序和"人操作"一致)
    result = asset_export.export_all_scenes(directory, dimension="3")
    if result.get("error"):
        log(f"导出失败: {result['error']}")
        return 1
    for entry in result.get("scenes", []):
        log(f"场景 {entry['scene']} -> {entry['scene_name']}.tscn"
            f"(脚本 {entry['script_name']})")
    for warning in result.get("warnings", []):
        log(f"告警: {warning}")
    if result.get("deleted"):
        log(f"清理旧文件: {result['deleted']}")
    log(f"工程目录: {directory}")

    save_blend(BLEND_PATH)
    log(f"已保存 {BLEND_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())