"""灯光示例(第四十八轮:阶段①灯光扩展)—— 一个"幽暗房间"里玩三种灯。

用法:
    blender -b --gpu-backend vulkan --python scripts\\make_lights_demo.py
产出:
    examples/demo_lights/灯光示例.blend
    examples/demo_lights/灯光示例_godot/   (可用 Godot 直接打开)

── 这一屏在看什么 ────────────────────────────────────────────────
三盏灯:**月光**(太阳光/平行光,冷蓝)、**台灯**(点光,暖黄)、
**手电筒**(聚光,跟着相机走)。数字键 1/2/3/4 分别开关手电筒 / 台灯 /
月光 / 全关 —— 关灯不是"看不见",而是卡通材质**真的不吃那盏光**了。

| 用到的 | 在游戏里怎么看 |
|---|---|
| **三种灯都导出** | SUN→DirectionalLight3D / POINT→OmniLight3D / SPOT→SpotLight3D,颜色 / 亮度 / 阴影都带走 |
| **手电筒(跟随相机)** | 灯光物体勾了『跟随相机(手电筒)』—— 运行时每物理帧把它的位置朝向贴到当前相机上 |
| **灯光_设置** | 数字键 1/2/3 开关对应的灯;4 = 三盏全关 |
| **卡通跟随场景光照** | 场景设置里打开:卡通材质按"影响最大的那盏亮灯"画明暗,灯全关就变平涂 |

按键:WASD 移动 / 空格 跳 / 鼠标转视角 / C·V 切机位 /
数字键 1 = 手电筒、2 = 台灯、3 = 月光、4 = 三盏全关。

跑法(Godot):打开 `灯光示例_godot` 直接 F5。
自动检查(不用 Blender,要 Godot):`python scripts\\check_lights_demo.py`
"""

import math
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "examples", "demo_lights")
BLEND_PATH = os.path.join(OUT_DIR, "灯光示例.blend")

PLAYER = "角色"
GROUND = "地面"
MOON = "月光"
LAMP = "台灯"
TORCH = "手电筒"

# 灯名 -> 记录开 / 关状态的变量名(开关靠它翻转)
LIGHT_VARS = ((TORCH, "手电筒开"), (LAMP, "台灯开"), (MOON, "月光开"))

HINT_TEXT = "1 手电筒  2 台灯  3 月光  4 全关  5 暗角加深  6 放松"

# 第五十一轮:屏幕暗角(阶段④屏幕氛围)—— 三个预设值
# (强度, 内圈半径, 边缘柔和, 过渡秒数)
VIGNETTE_OPEN = (0.5, 0.6, 0.4, 0.0)      # 开场:轻轻一圈,能看清但已经有"收窄感"
VIGNETTE_DEEP = (0.95, 0.45, 0.3, 0.35)   # 按 5:收到只剩中间一点(被追时的那种视野)
VIGNETTE_RELAX = (0.15, 0.75, 0.5, 0.35)  # 按 6:放开(演示『过渡秒数』的渐变)


def log(message):
    print(f"[灯光示例] {message}")


def clear_scene():
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for group in list(bpy.data.node_groups):
        if getattr(group, "bl_idname", "").startswith("GK"):
            bpy.data.node_groups.remove(group)
    scene.name = "灯光示例"
    return scene


def make_box(name, size, location, color=None):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = (size[0], size[1], size[2])
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if color is not None:
        material = bpy.data.materials.new(f"{name}材质")
        material.use_nodes = True
        principled = material.node_tree.nodes.get("Principled BSDF")
        if principled is not None:
            principled.inputs["Base Color"].default_value = color
        obj.data.materials.append(material)
    return obj


def make_camera(name, location, rotation):
    data = bpy.data.cameras.new(f"{name}数据")
    camera = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(camera)
    camera.location = location
    camera.rotation_euler = rotation
    return camera


def make_light(name, kind, location, energy, color, rotation=None,
               cutoff=0.0, spot_size=0.0, use_shadow=True):
    data = bpy.data.lights.new(f"{name}数据", type=kind)
    data.energy = energy
    data.color = color
    if kind in ("POINT", "SPOT") and cutoff > 0.0:
        data.cutoff_distance = cutoff
    if kind == "SPOT":
        data.spot_size = spot_size
    data.use_shadow = use_shadow
    light = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(light)
    light.location = location
    if rotation is not None:
        light.rotation_euler = rotation
    return light


def build_scene():
    scene = clear_scene()
    # ⚠ 必须有不止一个网格:整场景只有"角色"一个网格时插件不会生成主场景
    # .tscn(它以为这关没东西可看)—— 探针就没有 World.tscn 可加载了。
    make_box(GROUND, (20.0, 20.0, 0.4), (0.0, 0.0, -0.2),
             (0.22, 0.24, 0.30, 1.0))
    player = make_box(PLAYER, (1.0, 1.0, 1.6), (0.0, 0.0, 1.0),
                      (0.90, 0.60, 0.30, 1.0))
    side = make_camera("侧视机位", (0.0, -15.0, 5.0),
                       (math.radians(72.0), 0.0, 0.0))
    make_camera("俯瞰机位", (0.0, -7.0, 17.0), (math.radians(20.0), 0.0, 0.0))
    scene.camera = side

    # ── 三盏灯(第四十八轮的主角)───────────────────────────────────
    # 月光:太阳光,偏蓝、斜着打(方向靠旋转)
    make_light(MOON, "SUN", (0.0, 0.0, 6.0), 0.4, (0.6, 0.7, 1.0),
               rotation=(math.radians(55.0), 0.0, math.radians(35.0)))
    # 台灯:点光,暖黄,照到 8 米
    make_light(LAMP, "POINT", (4.0, 2.2, 0.0), 800.0, (1.0, 0.7, 0.4),
               cutoff=8.0)
    # 手电筒:聚光,不投影,锥 40°,运行时贴到相机上(下面勾『跟随相机』)
    torch = make_light(TORCH, "SPOT", (0.0, 1.5, 3.0), 1500.0,
                       (1.0, 0.98, 0.9), rotation=(math.radians(80.0), 0.0, 0.0),
                       cutoff=14.0, spot_size=math.radians(40.0),
                       use_shadow=False)
    log(f"场景:{GROUND} + {PLAYER} + 两台相机 + 三盏灯"
        f"({MOON} 太阳 / {LAMP} 点光 / {TORCH} 聚光)")
    return scene, player, torch


def write_physics(scene, player, torch):
    """角色 = 角色体 + 挂脚本;地面 = 静态体;手电筒 = 勾『跟随相机』。"""
    for obj in scene.objects:
        physics = getattr(obj, "gk_physics", None)
        if physics is None:
            continue
        if obj is player:
            physics.enabled = True
            physics.body_type = "CharacterBody3D"
            physics.dimension = "3"
            physics.script_target = True
        elif getattr(obj, "type", "") == "MESH":
            physics.enabled = True
            physics.body_type = "StaticBody3D"
            physics.dimension = "3"
    physics = getattr(torch, "gk_physics", None)
    if physics is not None:
        physics.light_follow_camera = True
        log(f"{TORCH}:已勾『跟随相机(手电筒)』")


def _add(tree, kind, name, x, y, **props):
    node = tree.nodes.new(kind)
    node.name = name
    node.location = (x, y)
    for key, value in props.items():
        setattr(node, key, value)
    return node


def _link(tree, source, source_pin, target, target_pin):
    tree.links.new(source.outputs[source_pin], target.inputs[target_pin])


def _find(tree, bl_idname):
    for node in tree.nodes:
        if node.bl_idname == bl_idname:
            return node
    raise RuntimeError(f"预设里没有 {bl_idname} 节点")


def _add_vignette(tree, name, x, y, values):
    """一盏暗角预设(强度 / 内圈 / 柔和 / 过渡)。"""
    amount, radius, edge, fade = values
    return _add(tree, "GKNodeScreenVignette", name, x, y,
                gk_amount=amount, gk_radius=radius, gk_edge=edge,
                gk_fade=fade)


def _build_init(tree, ready):
    """开场:提示键位 + 把三盏灯的状态变量置为『开』+ 铺一层暗角。

    返回链尾,后面的暗角按键链挂在它上面。
    """
    prev = _add(tree, "GKNodeUiText", "按键提示", -1500.0, -3600.0,
                gk_text=HINT_TEXT, gk_size=28, gk_color=(1.0, 1.0, 1.0, 1.0),
                gk_align="TOP_CENTER", gk_margin=24.0)
    _link(tree, ready, "执行", prev, "执行")
    for index, (light_name, var_name) in enumerate(LIGHT_VARS):
        const = _add(tree, "GKNodeConstBool", f"开_{light_name}", -1500.0,
                     -3800.0 - index * 180.0, gk_value=True)
        write = _add(tree, "GKNodeSetVar", f"初始_{var_name}", -1120.0,
                     -3800.0 - index * 180.0, gk_var_name=var_name)
        _link(tree, prev, "完成", write, "执行")
        _link(tree, const, "值", write, "值")
        prev = write
    # 第五十一轮:开场铺一层暗角 —— 幽暗房间的"视线收窄"
    open_vignette = _add_vignette(tree, "开场暗角", -1120.0, -4600.0,
                                  VIGNETTE_OPEN)
    _link(tree, prev, "完成", open_vignette, "执行")
    return open_vignette


def _build_vignette_key(tree, event, key, name, values, x, y):
    """一个数字键 = 换一组暗角(带过渡,演示『过渡秒数』的渐变)。"""
    key_node = _add(tree, "GKNodeKey", f"按{key}", x, y,
                    gk_key=key, gk_trigger="JUST")
    branch = _add(tree, "GKNodeBranch", f"判断{key}", x + 260.0, y)
    vignette = _add_vignette(tree, name, x + 520.0, y, values)
    _link(tree, event, "执行", branch, "执行")
    _link(tree, key_node, "结果", branch, "条件")
    _link(tree, branch, "是", vignette, "执行")


def _build_toggle(tree, event, key, light_name, var_name, slot):
    """一个数字键 = 翻转一盏灯:灯光_设置(取反) + 记状态。

    ⚠ **先设灯、后写变量**:『灯光_设置』的『开启』引脚接的是
    `not (状态变量)`,如果先写变量再设灯,那句 not 会把刚翻过的值再非一次
    (等于没翻)。所以链是:如果_否则 --> 灯光_设置 --> 变量_设置。
    """
    x = -1500.0 + slot * 900.0
    y = -3600.0 - slot * 420.0
    # 数字键**不填动作名**,让它按按键名自动生成(`1` -> `key_1`)——
    # 第四十八轮顺手修了那里的一处坑:以前 `snake_case("1")` 会因为
    # "以数字开头"落到 fallback,1~9 全撞成同一个动作 `key_key`,
    # 按一下 1 会把所有数字键绑的逻辑一起触发(实测踩过)。
    key_node = _add(tree, "GKNodeKey", f"按{key}", x, y,
                    gk_key=key, gk_trigger="JUST")
    branch = _add(tree, "GKNodeBranch", f"判断{key}", x + 260.0, y)
    read = _add(tree, "GKNodeGetVar", f"读{var_name}", x + 260.0, y - 260.0,
                gk_var_name=var_name)
    flip = _add(tree, "GKNodeLogic", f"取反{var_name}", x + 480.0, y - 260.0,
                gk_op="NOT")
    flip.build_sockets()
    light = _add(tree, "GKNodeSetLight", f"灯_{light_name}", x + 560.0, y,
                 gk_light=light_name, gk_on=True, gk_energy=0.0)
    write = _add(tree, "GKNodeSetVar", f"存{var_name}", x + 840.0, y,
                 gk_var_name=var_name)
    _link(tree, event, "执行", branch, "执行")
    _link(tree, key_node, "结果", branch, "条件")
    _link(tree, branch, "是", light, "执行")
    _link(tree, light, "完成", write, "执行")
    _link(tree, read, "值", flip, "A")
    _link(tree, flip, "结果", light, "开启")
    _link(tree, flip, "结果", write, "值")


def _build_all_off(tree, event):
    """数字键 4 = 三盏全关(状态变量一并置假,下次按 1 才是"打开")。"""
    x = -1500.0
    y = -4900.0
    key_node = _add(tree, "GKNodeKey", "按4", x, y, gk_key="4",
                    gk_trigger="JUST")
    branch = _add(tree, "GKNodeBranch", "判断4", x + 260.0, y)
    _link(tree, event, "执行", branch, "执行")
    _link(tree, key_node, "结果", branch, "条件")
    prev, prev_pin = branch, "是"
    for index, (light_name, var_name) in enumerate(LIGHT_VARS):
        off = _add(tree, "GKNodeConstBool", f"假_{light_name}", x, y - 260.0
                   - index * 180.0, gk_value=False)
        write = _add(tree, "GKNodeSetVar", f"清{var_name}", x + 340.0,
                     y - index * 180.0, gk_var_name=var_name)
        light = _add(tree, "GKNodeSetLight", f"灭_{light_name}", x + 640.0,
                     y - index * 180.0, gk_light=light_name, gk_on=False)
        _link(tree, prev, prev_pin, write, "执行")
        _link(tree, off, "值", write, "值")
        _link(tree, write, "完成", light, "执行")
        prev, prev_pin = light, "完成"


def build_graph(scene, player):
    """玩法图:第三人称角色预设 + 灯光开关 + 键位提示。"""
    from BlenderGameKit import presets

    logic, _anim, notes = presets.create_character_preset(
        scene, character=player,
        logic_name="灯光逻辑",
        class_name="LightsDemoPlayer",
        with_animation=False,
        with_ports=False,
        with_god_camera=False,
        camera_side="侧视机位",
        camera_top="俯瞰机位",
    )
    for note in notes:
        log(f"预设: {note}")

    ready = _find(logic, "GKNodeOnReady")
    event = _find(logic, "GKNodeOnPhysicsProcess")

    _build_init(logic, ready)
    for slot, (light_name, var_name) in enumerate(LIGHT_VARS):
        _build_toggle(logic, event, str(slot + 1), light_name, var_name, slot)
    _build_all_off(logic, event)
    # 第五十一轮:暗角那两个键挂在**开场链尾**(提示文字之后),
    # 不挂在每物理帧上 —— 它们只在按下的那一刻生效
    _build_vignette_key(logic, event, "5", "暗角加深", VIGNETTE_DEEP,
                        -1120.0, -4900.0)
    _build_vignette_key(logic, event, "6", "暗角放松", VIGNETTE_RELAX,
                        -1120.0, -5200.0)
    log("玩法图:角色预设 + 数字键 1/2/3 开关灯 + 4 全关 + 5/6 暗角深松"
        f" + 键位提示(开场暗角 {VIGNETTE_OPEN[0]})")
    return logic


def _apply_settings(scene, pairs):
    settings = getattr(scene, "gk_settings", None)
    if settings is None:
        return
    for attr, value in pairs:
        if hasattr(settings, attr):
            try:
                setattr(settings, attr, value)
            except Exception:                             # noqa: BLE001
                pass


def save_blend(path):
    if not bpy.app.background:
        bpy.ops.wm.save_as_mainfile(filepath=path)
        return
    blocks = set(bpy.data.objects) | set(bpy.data.meshes) | \
        set(bpy.data.materials) | set(bpy.data.node_groups) | \
        set(bpy.data.cameras) | set(bpy.data.lights)
    scenes = list(bpy.data.scenes)
    bpy.data.libraries.write(path, blocks | set(scenes), fake_user=True)


def main():
    from BlenderGameKit import asset_export, project

    os.makedirs(OUT_DIR, exist_ok=True)
    scene, player, torch = build_scene()
    write_physics(scene, player, torch)
    build_graph(scene, player)
    # 卡通材质要跟着场景光画明暗 —— 不开的话卡通材质是全平涂,
    # 关灯 / 手电筒照到谁就都看不出来了(探针也断言不了)。
    _apply_settings(scene, (("dimension", "3"), ("toon_follow_light", True)))

    directory = os.path.join(OUT_DIR, "灯光示例_godot")
    project.create_project(directory, title="灯光示例",
                           blend_file_name=os.path.basename(BLEND_PATH))
    result = asset_export.export_all_scenes(directory, dimension="3")
    if result.get("error"):
        log(f"导出失败:{result['error']}")
        return 1
    for entry in result.get("scenes", []):
        log(f"场景 {entry['scene']} -> {entry['scene_name']}.tscn"
            f"(脚本 {entry['script_name']})")
    for warning in result.get("warnings", []):
        log(f"告警: {warning}")
    if result.get("deleted"):
        log(f"清理旧文件: {result['deleted']}")

    save_blend(BLEND_PATH)
    log(f"已保存 {BLEND_PATH}")
    log(f"工程目录: {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())