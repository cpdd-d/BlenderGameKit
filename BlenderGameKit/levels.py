"""三种关卡预设模板(2D 闯关 / 3D 关卡 / 2.5D) —— 第八十七轮(I 组 24)。

用户从待办清单里点的"一键建能跑的一关":选一种模板 → 新建场景 + 地面 +
相机 + 角色 + 起步逻辑链 + 场景/物体设置**全配好** —— 导出后 Godot 里
直接能玩。三种模板回答的是"我这种玩法该从哪开始":

   2D 闯关   维度 2:方向键移动 + 空格跳 + 相机跟随(运行时自动 Camera2D)
   3D 关卡   维度 3:第三人称 —— 复用『角色预设』那套跑通的链路
             (WASD / 空格 / 鼠标转视角 / C·V 换机位),两台相机已摆好
   2.5D      维度 3 的"固定机位"玩法:侧视机位开场,按 C / V 在
             侧视 / 俯瞰之间切;方向键在 XZ 平面走,不转视角

── 三条原则(和『角色预设』同一套)────────────────────────────────
  1. **不改底层节点**:建出来的是一张普通节点图,想怎么改都行;
  2. **相机物体要建**(和角色预设不同:那一轮用户说"相机我自己摆" ——
     但关卡模板的卖点是"一键能跑",相机不摆好就跑不起来);
  3. **老工程零影响**:只在**新建的场景**里干活,不动任何已有场景 / 物体。

── 产物是什么 ───────────────────────────────────────────────────
一次调用会多出:一个 Blender 场景(一个 Godot .tscn)、地面与角色网格、
相机(3D / 2.5D)、一盏太阳灯(3D / 2.5D)、一张连好的『游戏逻辑』图,
并把场景设置(维度 / 基类 / 用哪张图 / 类名)与物体设置(物理类型 /
『脚本挂载目标』)一并配好。导出走原有的『导出脚本 / 导出模型和材质』。
"""

# 模板清单(键, 显示名)—— 顺序就是对话框里的顺序。
TEMPLATES = (
    ("2d", "2D 闯关"),
    ("3d", "3D 关卡"),
    ("25d", "2.5D"),
)

# 每种模板的一句话说明(对话框里显示 + 生成后的提示)。
TEMPLATE_HINTS = {
    "2d": "维度 2:方向键移动 + 空格跳 + 相机跟随",
    "3d": "第三人称:WASD + 空格跳 + 鼠标转视角 + C/V 换机位",
    "25d": "固定机位:方向键在 XZ 平面走,按 C / V 在侧视/俯瞰之间切",
}

# 新场景 / 物体 / 相机的默认名字(用户可以改)。
DEFAULT_SCENE_NAMES = {"2d": "2D关卡", "3d": "3D关卡", "25d": "2.5D关卡"}
CHARACTER_NAME = "角色"
GROUND_NAME = "地面"
CAMERA_SIDE = "侧视机位"
CAMERA_TOP = "俯瞰机位"


def template_items():
    """给 Blender 的 EnumProperty 用: [(键, 显示名, 说明)]。"""
    return [(key, label, TEMPLATE_HINTS.get(key, ""))
            for key, label in TEMPLATES]


def resolve_character(scene, wanted=""):
    """找出要挂脚本的角色(照『角色预设』的规则)。

    给了名字就用它;没给就选中的网格 / 已经勾了『脚本挂载目标』的物体;
    都没有返回 None(由调用方给出提示)。
    """
    import bpy

    if wanted:
        return bpy.data.objects.get(str(wanted))
    for obj in getattr(bpy.context, "selected_objects", []) or []:
        if getattr(obj, "type", "") == "MESH":
            return obj
    for obj in getattr(scene, "objects", []) or []:
        physics = getattr(obj, "gk_physics", None)
        if physics is not None and getattr(physics, "script_target", False):
            return obj
    return None


# ==========================================================================
# 小工具:建物体 / 建相机 / 配物理
# ==========================================================================
def _link_object(scene, obj):
    scene.collection.objects.link(obj)
    return obj


def _new_box(scene, name, size, location, color=None):
    """一个立方体(尺寸 = 米)。2D 里它躺在 XY 平面上(Z 没有意义)。"""
    import bpy

    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.data.name = f"{name}Mesh"
    obj.scale = tuple(size)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    # primitive_cube_add 把物体建在**当前场景**里 —— 我们的新场景可能不是
    # 当前场景(后台 / 多场景),所以搬过去:先 unlink 再 link 到目标场景。
    for collection in list(obj.users_collection):
        collection.objects.unlink(obj)
    _link_object(scene, obj)
    if color is not None:
        material = bpy.data.materials.new(f"{name}_材质")
        material.use_nodes = True
        principled = material.node_tree.nodes.get("Principled BSDF")
        if principled is not None:
            principled.inputs["Base Color"].default_value = color
        obj.data.materials.append(material)
    return obj


def _new_camera(scene, name, location, rotation_x):
    """一台相机(Blender 相机朝 -Z 看;rotation_x 让它朝前下方)。"""
    import bpy

    data = bpy.data.cameras.new(name)
    data.lens = 50.0
    camera = bpy.data.objects.new(name, data)
    camera.location = tuple(location)
    camera.rotation_euler = (rotation_x, 0.0, 0.0)
    return _link_object(scene, camera)


def _new_sun(scene):
    import bpy

    data = bpy.data.lights.new("太阳", type="SUN")
    data.energy = 3.0
    light = bpy.data.objects.new("太阳", data)
    light.location = (3.0, -3.0, 6.0)
    return _link_object(scene, light)


def _set_physics(obj, **values):
    """配物体『游戏物理』(属性不存在就跳过 —— 老版本 .blend 兜底)。"""
    physics = getattr(obj, "gk_physics", None)
    if physics is None:
        return False
    for attr, value in values.items():
        if hasattr(physics, attr):
            try:
                setattr(physics, attr, value)
            except Exception:                             # noqa: BLE001
                pass
    return True


def _socket(sockets, name):
    """按名字取引脚(bpy 支持名称索引;离线桩是列表)。"""
    try:
        return sockets[name]
    except TypeError:
        pass
    for socket in sockets:
        if getattr(socket, "name", "") == name:
            return socket
    raise KeyError(f"关卡模板:没有引脚『{name}』")


def _add(tree, kind, name, x, y, **props):
    node = tree.nodes.new(kind)
    node.name = name
    node.location = (x, y)
    for key, value in props.items():
        setattr(node, key, value)
    return node


def _link(tree, source_node, source_pin, target_node, target_pin):
    tree.links.new(_socket(source_node.outputs, source_pin),
                   _socket(target_node.inputs, target_pin))


# ==========================================================================
# ① 2D 闯关的起步链
# ==========================================================================
def build_2d_chain(tree, params=None):
    """2D 平台跳跃的起步链(照 examples/demo_2d 那张跑通的图)。

        每当_准备 ─ 打印操作提示
        每当_物理帧 ─ 输入_方向(A/D) ─ 移动_按方向(重力模式)
                    └ 相机_跟随 ─ 如果(在地面 且 刚按空格)-> 移动_跳跃
    """
    p = params or {}
    speed = float(p.get("move_speed", 240.0))
    jump = float(p.get("jump_force", 520.0))

    ready = _add(tree, "GKNodeOnReady", "每当_准备", -1400.0, 400.0)
    hint = _add(tree, "GKNodePrint", "操作提示", -1100.0, 400.0,
                gk_text=str(p.get("hint", "A/D 移动，空格跳跃")))
    _link(tree, ready, "执行", hint, "执行")

    physics = _add(tree, "GKNodeOnPhysicsProcess", "每当_物理帧",
                   -1400.0, 0.0)
    direction = _add(tree, "GKNodeInputDirection", "输入_方向",
                     -1400.0, -260.0,
                     gk_action_left="move_left", gk_action_right="move_right",
                     gk_action_up="move_up", gk_action_down="move_down")
    move = _add(tree, "GKNodeMoveDirection", "移动_按方向", -1050.0, 0.0,
                gk_speed=speed, gk_keep_y=True)
    follow = _add(tree, "GKNodeCameraFollow", "相机_跟随", -750.0, 0.0,
                  gk_smooth=0.12)
    _link(tree, physics, "执行", move, "执行")
    _link(tree, direction, "方向", move, "方向")
    _link(tree, move, "完成", follow, "执行")

    # 跳跃:在地面 且 刚按下空格
    on_floor = _add(tree, "GKNodeIsOnFloor", "在地面吗", -1050.0, -320.0)
    jump_key = _add(tree, "GKNodeKey", "空格", -1050.0, -520.0,
                    gk_key="SPACE", gk_action="jump", gk_trigger="JUST")
    both = _add(tree, "GKNodeLogic", "能跳吗", -800.0, -420.0, gk_op="AND")
    branch = _add(tree, "GKNodeBranch", "如果_否则", -560.0, -420.0)
    do_jump = _add(tree, "GKNodeJump", "起跳", -300.0, -420.0,
                   gk_jump_force=jump)
    _link(tree, on_floor, "在地面", both, "A")
    _link(tree, jump_key, "结果", both, "B")
    _link(tree, both, "结果", branch, "条件")
    _link(tree, follow, "完成", branch, "执行")
    _link(tree, branch, "是", do_jump, "执行")
    return tree


# ==========================================================================
# ② 3D 关卡的起步链(复用『角色预设』那张跑通的图)
# ==========================================================================
def build_3d_chain(tree, params=None):
    """第三人称 3D:输入 / 移动 / 跳跃 / 鼠标转视角 / 两根弹簧臂 / C·V 换机位。

    直接调 `presets.build_logic_tree` —— 它就是 examples/demo_level 里
    那套已经跑通的手感(速度 6 / 跳 7 / 灵敏度 0.25),不重抄一遍。
    """
    from . import presets

    p = dict(params or {})
    preset_params = presets.defaults(
        move_speed=p.get("move_speed", 6.0),
        jump_force=p.get("jump_force", 7.0),
        sensitivity=p.get("sensitivity", 0.25),
        with_animation=False,          # 模板角色是个方盒,没有动画可播
        with_ports=True,               # 位置 / 在地面这些端口留着,别的图能用
        with_god_camera=False,         # 调试开关不替用户动(和角色预设一致)
        camera_side=CAMERA_SIDE, camera_top=CAMERA_TOP)
    preset_params["logic_name"] = tree.name
    presets.build_logic_tree(tree, preset_params)
    return tree


# ==========================================================================
# ③ 2.5D 的起步链(固定机位,不转视角)
# ==========================================================================
def build_25d_chain(tree, params=None):
    """3D 世界 + 固定侧视机位:

        每当_准备 ─ 相机_启用(侧视机位) ─ 相机_设置(视野 55)
        每当_物理帧 ─ 输入_方向 ─ 移动_按方向(重力模式,不跟相机)
                    ├ 如果(在地面 且 刚按空格)-> 移动_跳跃
                    ├ 如果(刚按 C)-> 相机_切换(俯瞰机位)
                    └ 如果(刚按 V)-> 相机_切换(侧视机位)
    """
    p = params or {}
    speed = float(p.get("move_speed", 5.0))
    jump = float(p.get("jump_force", 7.0))

    ready = _add(tree, "GKNodeOnReady", "每当_准备", -1500.0, 500.0)
    activate = _add(tree, "GKNodeCameraActive", "启用侧视机位", -1200.0, 500.0,
                    gk_active=True, gk_target=CAMERA_SIDE)
    setup = _add(tree, "GKNodeCameraSetup", "设定视野", -900.0, 500.0,
                 gk_target=CAMERA_SIDE, gk_fov=55.0, gk_distance=0.0,
                 gk_smooth=0.0)
    _link(tree, ready, "执行", activate, "执行")
    _link(tree, activate, "完成", setup, "执行")

    physics = _add(tree, "GKNodeOnPhysicsProcess", "每当_物理帧",
                   -1500.0, 0.0)
    direction = _add(tree, "GKNodeInputDirection", "输入_方向",
                     -1500.0, -260.0,
                     gk_action_left="move_left", gk_action_right="move_right",
                     gk_action_up="move_up", gk_action_down="move_down")
    move = _add(tree, "GKNodeMoveDirection", "移动_按方向", -1150.0, 0.0,
                gk_speed=speed, gk_keep_y=True)
    _link(tree, physics, "执行", move, "执行")
    _link(tree, direction, "方向", move, "方向")

    # 跳跃(和 2D 同一个模式:在地面 且 刚按下空格)
    on_floor = _add(tree, "GKNodeIsOnFloor", "在地面吗", -1150.0, -320.0)
    jump_key = _add(tree, "GKNodeKey", "空格", -1150.0, -520.0,
                    gk_key="SPACE", gk_action="jump", gk_trigger="JUST")
    both = _add(tree, "GKNodeLogic", "能跳吗", -880.0, -420.0, gk_op="AND")
    branch = _add(tree, "GKNodeBranch", "如果_否则", -640.0, -420.0)
    do_jump = _add(tree, "GKNodeJump", "起跳", -380.0, -420.0,
                   gk_jump_force=jump)
    _link(tree, physics, "执行", branch, "执行")
    _link(tree, on_floor, "在地面", both, "A")
    _link(tree, jump_key, "结果", both, "B")
    _link(tree, both, "结果", branch, "条件")
    _link(tree, branch, "是", do_jump, "执行")

    # 机位切换:C → 俯瞰,V → 侧视(各自一条分支,都挂在物理帧上)
    for key, key_name, target, label, y in (
            ("C", "C", CAMERA_TOP, "切到俯瞰", -700.0),
            ("V", "V", CAMERA_SIDE, "切回侧视", -940.0)):
        pressed = _add(tree, "GKNodeKey", label, -1150.0, y,
                       gk_key=key_name, gk_action=f"key_{key.lower()}",
                       gk_trigger="JUST")
        pick = _add(tree, "GKNodeBranch", f"按了{key}吗", -880.0, y)
        switch = _add(tree, "GKNodeCameraSetCurrent", label, -620.0, y,
                      gk_target=target)
        _link(tree, pressed, "结果", pick, "条件")
        _link(tree, physics, "执行", pick, "执行")
        _link(tree, pick, "是", switch, "执行")
    return tree


_CHAIN_BUILDERS = {
    "2d": build_2d_chain,
    "3d": build_3d_chain,
    "25d": build_25d_chain,
}


# ==========================================================================
# 一次性把"一关"建好(算子调这个)
# ==========================================================================
def create_level(template="3d", scene_name="", character_name="",
                 params=None):
    """建一关:新场景 + 地面 / 角色 / 相机 + 逻辑图 + 设置。

    返回 {"scene": 场景, "tree": 逻辑图, "character": 角色, "notes": [说明]}。
    老场景一个字都不动(新场景是 `bpy.data.scenes.new` 出来的)。
    """
    import bpy

    key = str(template or "3d")
    if key not in _CHAIN_BUILDERS:
        raise ValueError(f"不认识的关卡模板:{key}")
    p = dict(params or {})
    is_2d = key == "2d"

    scene = bpy.data.scenes.new(str(scene_name or DEFAULT_SCENE_NAMES[key]))

    # ---- 物体:地面 + (2D 多一块跳台/箱子)+ 角色 ------------------
    if is_2d:
        # 2D 世界 = Blender 的 XY 平面。地板**要**碰撞(不然角色往下掉),
        # 但 2D 寻路会把碰撞体当障碍 —— 模板不开寻路,两者不冲突。
        ground = _new_box(scene, GROUND_NAME, (30.0, 1.0, 1.0),
                          (0.0, -5.0, 0.0), color=(0.35, 0.4, 0.45, 1.0))
        platform = _new_box(scene, "跳台", (4.0, 0.6, 1.0),
                            (5.0, -1.6, 0.0), color=(0.5, 0.45, 0.35, 1.0))
        character = _new_box(scene, str(character_name or CHARACTER_NAME),
                             (0.9, 1.4, 1.0), (0.0, -3.2, 0.0),
                             color=(0.4, 0.65, 0.9, 1.0))
        _set_physics(ground, enabled=True, dimension="2",
                     body_type="StaticBody2D")
        _set_physics(platform, enabled=True, dimension="2",
                     body_type="StaticBody2D")
        cameras = []
    else:
        ground = _new_box(scene, GROUND_NAME, (60.0, 60.0, 1.0),
                          (0.0, 0.0, -0.5), color=(0.35, 0.4, 0.45, 1.0))
        if key == "3d":
            # 几个台阶方块:一眼能看出"绕开障碍"的手感,也是推箱子的试验品
            _new_box(scene, "方块A", (1.6, 1.6, 1.6), (4.0, 2.0, 0.8),
                     color=(0.75, 0.5, 0.35, 1.0))
            _new_box(scene, "方块B", (1.6, 1.6, 1.6), (-3.0, 4.5, 0.8),
                     color=(0.6, 0.5, 0.7, 1.0))
        character = _new_box(scene, str(character_name or CHARACTER_NAME),
                             (0.9, 0.9, 1.8), (0.0, 0.0, 0.9),
                             color=(0.4, 0.65, 0.9, 1.0))
        _set_physics(ground, enabled=True, dimension="3",
                     body_type="StaticBody3D")
        _new_sun(scene)
        # 两台相机:名字就是预设链路里约定的那两个(弹簧臂 / 相机_切换按名字找)
        if key == "3d":
            cameras = [
                _new_camera(scene, CAMERA_SIDE, (0.0, 5.5, 2.4), 1.28),
                _new_camera(scene, CAMERA_TOP, (0.0, -0.35, 16.0), 0.10),
            ]
        else:
            # 2.5D:侧视机位摆在 -Y 侧、稍抬高,正对画面中央(和 2.5D 示例一致)
            cameras = [
                _new_camera(scene, CAMERA_SIDE, (0.0, -9.0, 3.2), 1.30),
                _new_camera(scene, CAMERA_TOP, (0.0, -0.35, 12.0), 0.10),
            ]

    _set_physics(character, enabled=True,
                 dimension="2" if is_2d else "3",
                 body_type="CharacterBody2D" if is_2d else "CharacterBody3D",
                 script_target=True)

    # ---- 逻辑图 --------------------------------------------------
    label = dict(TEMPLATES).get(key, "关卡")
    tree = bpy.data.node_groups.new(f"{label}逻辑", "GKGameLogicTree")
    # 没有用户引用它的节点树,存盘时会被直接丢掉(踩过的坑)
    tree.use_fake_user = True
    _CHAIN_BUILDERS[key](tree, p)

    # ---- 场景设置 -------------------------------------------------
    notes = []
    settings = getattr(scene, "gk_settings", None)
    if settings is not None:
        for attr, value in (("dimension", "2" if is_2d else "3"),
                            ("base_class",
                             "CharacterBody2D" if is_2d else "CharacterBody3D"),
                            ("logic_tree", tree.name)):
            if hasattr(settings, attr):
                try:
                    setattr(settings, attr, value)
                except Exception:                         # noqa: BLE001
                    pass
        notes.append(f"场景『{scene.name}』:维度 {'2' if is_2d else '3'} · "
                     f"逻辑图 = {tree.name}")
    else:
        notes.append("⚠ 拿不到场景设置(重载插件后再试)")

    notes.append(f"角色『{character.name}』:物理类型 + 挂脚本 ✓")
    if cameras:
        notes.append("相机:" + " / ".join(obj.name for obj in cameras))
    else:
        notes.append("2D:运行时自动造一台 Camera2D 框住全场(跟随由『相机_跟随』接管)")

    # 顺手切过去,让用户立刻看到成果(后台模式下没有窗口,跳过)
    try:
        window = getattr(bpy.context, "window", None)
        if window is not None:
            window.scene = scene
    except Exception:                                     # noqa: BLE001
        pass
    return {"scene": scene, "tree": tree, "character": character,
            "notes": notes}