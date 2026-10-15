"""Default pawn export and lifecycle. Levels own spawn points, never the pawn."""
from pathlib import Path
import re


# ── "自动识别默认角色"的告警(第一百二十四轮)────────────────────────
# 为什么要这样一个"信箱":`default_player()` 的调用点散布在导出流程各处
# (`owns()` 每问一个物体就调一次),函数本身没法把告警交给调用方;
# 所以识别时的告警先存这里,导出流程用 `take_infer_warnings()` 取走、
# 收进 result["warnings"](取完清空,不会重复报)。
_INFER_WARNINGS = []


def take_infer_warnings():
    """取走"自动识别默认角色"期间攒下的告警(取完清空)。"""
    out = list(_INFER_WARNINGS)
    del _INFER_WARNINGS[:]
    return out


def _remember_warning(message):
    if message and message not in _INFER_WARNINGS:
        _INFER_WARNINGS.append(message)


def infer_default_player():
    """没手选默认角色时的**自动识别**(用户第 1 问:默认角色好像没什么作用)。

    ── 判据:本关放了『玩家出生点』标记才启用(故意的 opt-in)─────────
    出生点标记是**用户主动放进场景**的东西,所以它同时表达了
    "这一关要一个跨关卡角色"。没有标记的工程(全部已有示例 / 门禁)
    走老路径:没有任何跨关卡角色,产物逐字节不变 —— 这条很关键,
    不能因为"顺手加个自动识别"就把别人比对中的 .tscn 改掉。

    启用了以后:在参与导出的场景里找 `type == "MESH"` 且
    `gk_physics.body_type == "CharacterBody3D"` 的物体。
      * 恰好 1 个 -> 就是它;
      * 多个     -> 返回 None(不猜:猜错会把用户关卡里那个角色
                    偷偷搬走 —— 关卡里不再有它,变成 autoload 生成),
                    并留一条中文告警给导出报告;
      * 0 个     -> None(这一关没有跨关卡角色)。

    只做识别,不碰 `GKWorldTree.default_player` —— 那个是"手选"，
    优先级更高,由 `default_player()` 负责。
    """
    try:
        from . import exporter, markers

        scenes = list(exporter.exportable_scenes())
        if not any(markers.player_start(scene) is not None
                   for scene in scenes):
            # 没有出生点标记 = 用户没表态 —— 一个物体都不碰(老行为)
            return None
        candidates = []
        seen = set()
        for scene in scenes:
            for obj in getattr(scene, "objects", ()) or ():
                # 标记物体自己不是角色(它默认是 StaticBody,但用户可能改过)
                if markers.kind_of(obj):
                    continue
                if str(getattr(obj, "type", "")) != "MESH":
                    continue
                physics = getattr(obj, "gk_physics", None)
                if str(getattr(physics, "body_type", "")) != "CharacterBody3D":
                    continue
                key = str(getattr(obj, "name", ""))
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(obj)
        if len(candidates) == 1:
            _note_if_unusable(candidates[0])
            return candidates[0]
        if len(candidates) > 1:
            _remember_warning(
                "多个 3D 角色体,但没在『世界设置』里指定默认角色 —— "
                "本关的角色不会跨关卡保留")
        return None
    except Exception:                                     # noqa: BLE001
        # 识别只是"锦上添花":任何意外(缺模块 / 半初始化的 bpy)都不该
        # 让导出整个失败,退回"没有跨关卡角色"这条老路。
        return None


def _note_if_unusable(player):
    """自动识别到的角色"当不了跨关卡 pawn"时提前说一句。

    ── 为什么值得单独判一次 ────────────────────────────────────────
    自动识别是**出生点标记**触发的,用户未必知道"放个出生点"还会把
    关卡里那个角色改造成跨关卡角色。如果那个角色没有角色逻辑图,
    导出会停在 `请给默认角色指定一张角色逻辑图` —— 用户完全看不出
    这两件事有关系。所以这里补一条能看懂的告警(选谁仍然是上面的规则
    决定的,这里只解释,不改结果)。

    `logic_tree` 也没填时还有一条兜底:世界设置里那个默认角色的图。
    """
    try:
        wanted = getattr(getattr(player, "gk_physics", None), "logic_tree", "")
        if not wanted:
            from .ui import world_settings
            fallback = world_settings().default_character
            wanted = fallback.name if fallback else ""
        if wanted:
            return
        _remember_warning(
            f"出生点标记让『{getattr(player, 'name', '?')}』成为本关的跨关卡角色,"
            f"但它没有角色逻辑图 —— 导出会停在『请给默认角色指定一张角色逻辑图』;"
            f"给它指定一张角色图,或在『世界设置』里手动改选默认角色")
    except Exception:                                     # noqa: BLE001
        return


def _is_auto_inferred(player):
    """这个角色是"自动识别"出来的吗(不是用户在『世界设置』里手选的)?

    用途只有一个:`export()` 里区分"手选配错了 -> 报错"和
    "识别出来的不能用 -> 退回老路径 + 告警"。用户手选 = 明确要求,
    报错是对的;自动识别是出生点标记顺带触发的,报错会把整次导出弄挂。
    """
    try:
        return player is not None and player is infer_default_player()
    except Exception:                                     # noqa: BLE001
        return False


def default_player():
    """跨关卡角色(整局只生成一次的 pawn)。

    ── 三级优先(第一百二十四轮)──────────────────────────────────
    1. 『世界设置』里手选的 -> 原样返回(行为一字不变);
    2. 没选,但本关放了『玩家出生点』标记 -> 自动识别(见 infer_default_player);
    3. 其余 -> None(老行为:关卡里没有跨关卡角色)。

    第 2 条是用户第 1 问的答案:默认角色"看起来没什么作用",是因为
    不选它就完全不生效,而"选了以后产物形态变化很大"谁都不敢点。
    现在视口里的出生点标记就是那个开关。
    """
    try:
        import bpy
        chosen = next((getattr(t, "default_player", None)
                       for t in bpy.data.node_groups
                       if getattr(t, "bl_idname", "") == "GKWorldTree"), None)
        if chosen is not None:
            return chosen
        return infer_default_player()
    except (ImportError, AttributeError):
        return None


def owns(obj, player=None):
    """这个物体属不属于默认角色(它自己 / 它的某一级祖先)。

    `player` 可以由调用方传进来复用:导出流程会**逐个物体**问这个问题,
    每次重新解析一遍"默认角色是谁"在几百个物体的场景里是纯浪费
    (而且是 O(物体数²))。
    """
    if player is None:
        player = default_player()
    while obj is not None and player is not None:
        if obj == player:
            return True
        obj = getattr(obj, "parent", None)
    return False



def transform_text(matrix):
    from mathutils import Matrix
    conversion = Matrix(((1, 0, 0, 0), (0, 0, 1, 0),
                         (0, -1, 0, 0), (0, 0, 0, 1)))
    m = conversion @ matrix @ conversion.inverted()
    return "Transform3D(" + ", ".join(f"{m[r][c]:.8f}"
           for c in range(4) for r in range(3)) + ")"


def spawn_marker(scene):
    """本关的出生点物体:场景设置里手选的 -> 视口里的『玩家出生点』标记 -> None。

    ── 为什么要加标记这条路(第一百二十四轮)───────────────────────
    『玩家出生点』以前只能在侧栏的下拉里挑一个物体,绝大多数用户根本
    不知道有这一项(第 1 问那类"这东西好像没用"的根因)。现在在 3D 视口
    里放一个"玩家出生点"标记物体就等于指定出生点 —— 看得见、拖得动。
    手选的优先级更高:老工程里已经选好的那个继续说了算。

    手选的出生点**不属于本场景**时照样抛 ValueError(中文文案不变)——
    以前这里静默不报的话,复制关卡会悄悄用另一个场景的坐标。
    """
    if scene is None:
        return None
    marker = getattr(getattr(scene, "gk_settings", None), "player_spawn", None)
    if marker is not None:
        if marker.name not in scene.objects:
            raise ValueError(f"『{scene.name}』的玩家出生点不在本关中")
        return marker
    from . import markers
    return markers.player_start(scene)


def spawn_node(scene):
    """`GKPlayerStart` 节点文本(没有默认角色 / 没有出生点时是空串)。"""
    player = default_player()
    if player is None:
        return ""
    marker = spawn_marker(scene)
    if marker is None:
        # 没有任何出生点 -> 用默认角色自己的初始变换(老行为一字不变)
        marker = player
    return ('[node name="GKPlayerStart" type="Marker3D" parent="." '
            'groups=["gk_player_start"]]\ntransform = '
            + transform_text(marker.matrix_world) + '\n')


def _autoload(directory, enabled):
    path = Path(directory) / "project.godot"
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    text = re.sub(r'^GkWorld\s*=.*\n?', '', text, flags=re.M)
    if enabled:
        line = 'GkWorld="*res://scripts/gk_world.gd"'
        section = re.search(r'\[autoload\][^\[]*', text)
        if section:
            # Preferences and permanent UI must be ready before pawn BeginPlay.
            index = section.end()
            text = text[:index].rstrip() + '\n' + line + '\n\n' + text[index:]
        else:
            text += "\n[autoload]\n" + line + "\n"
    path.write_text(text, encoding="utf-8")


def export(directory):
    """Export once before levels so the player can exist before level _ready."""
    import bpy
    from . import asset_export as assets, exporter
    player = default_player()
    # 自动识别时的告警(多个 3D 角色体 / 没指定默认角色)要出现在导出报告里
    warnings = take_infer_warnings()
    if player is None:
        _autoload(directory, False)
        return {"written": [], "input_actions": [], "warnings": warnings}
    scenes = [s for s in player.users_scene if s in exporter.exportable_scenes()]
    if not scenes or player.type != "MESH":
        raise ValueError("默认角色必须是参与导出场景中的角色网格物体")
    scene = scenes[0]
    for level in exporter.exportable_scenes():
        # 只做校验:手选的出生点必须在**这一关**里(不在就抛 ValueError)。
        # 标记出生点由 spawn_marker 自己保证属于本场景,不会有问题。
        spawn_marker(level)
    physics = player.gk_physics
    if physics.body_type != "CharacterBody3D" or str(scene.gk_settings.dimension) != "3":
        raise ValueError("默认角色需要使用 3D 角色体 CharacterBody3D")
    wanted = physics.logic_tree
    if not wanted:
        from .ui import world_settings
        fallback = world_settings().default_character
        wanted = fallback.name if fallback else ""
    tree = bpy.data.node_groups.get(wanted)
    if tree is None or exporter.logic_tree_scope(tree) != "ROLE":
        # ── 自动识别出来的角色没有角色图时**不能报错**(第一百二十四轮)──
        # 这条区分很要紧:手选默认角色 = 用户明确要求,配错了必须报错;
        # 但自动识别是**出生点标记**顺带触发的 —— 用户只是放了个出生点,
        # 完全不知道这还会去动关卡里那个角色。此时抛异常等于
        # "放一个出生点就把整次导出弄挂了",那是不可接受的。
        # 所以:识别出来的这个不能用 -> 退回老路径(关卡里各生成一个角色),
        # 并留一条能看懂的中文告警。
        if _is_auto_inferred(player):
            _remember_warning(
                f"出生点标记让『{getattr(player, 'name', '?')}』被当成跨关卡角色,"
                f"但它没有角色逻辑图 —— 已按『每关各生成一个角色』导出。"
                f"想跨关卡保留角色:给它挂一张角色逻辑图,或删掉出生点标记")
            _autoload(directory, False)
            return {"written": [], "input_actions": [],
                    "warnings": warnings + take_infer_warnings()}
        raise ValueError("请给默认角色指定一张角色逻辑图")
    previous = assets._active_scene()
    assets._activate_scene(scene)
    try:
        bpy.context.view_layer.update()
        settings = exporter.build_settings(scene)
        settings.update(output_dir=directory, base_class="CharacterBody3D",
                        class_name="", script_name="gk_default_player.gd",
                        reference_bindings=exporter.object_reference_bindings(player, scene))
        result = exporter.export_tree(tree, settings, write_scene=False)
        if not result.get("ok", True):
            raise ValueError(result.get("error") or "默认角色逻辑生成失败")
        model, ok, message = assets.export_subtree_glb(
            directory, "GKDefaultPlayer", [player], scene=scene, include_cameras=True)
        if not ok:
            raise ValueError("默认角色模型导出失败: " + str(message))
        result["written"].append(model)
        members = [o for o in scene.objects
                   if o.type == "MESH" and owns(o, player)]
        materials = assets.write_materials(directory, members, prefix="GKPlayer_")
        result["written"].extend(path for _name, path, _texture in materials)
        material_names = {name: Path(path).stem for name, path, _texture in materials}
        material_files = {}
        for obj in members:
            material_files.update(assets.surface_material_files(obj, material_names))
        result["written"].extend(path for _name, path in assets.export_textures(
            directory, [bpy.data.materials[name] for name in material_names]))
        collision = assets.build_collision(player, True, physics)
        if collision is None:
            raise ValueError("默认角色缺少有效碰撞形状")
        ext = ['[ext_resource type="PackedScene" path="res://art/GKDefaultPlayer.glb" id="model"]',
               '[ext_resource type="Script" path="res://scripts/gk_default_player.gd" id="logic"]',
               '[ext_resource type="Script" path="res://scripts/gk_player_components.gd" id="components"]']
        resources = [collision["sub_resource"]]
        nodes = [f'[node name="{assets._sanitize_node_name(player.name)}" type="CharacterBody3D" groups=["gk_player"]]',
                 'script = ExtResource("logic")',
                 f'collision_layer = {physics.collision_layer}',
                 f'collision_mask = {physics.collision_mask}',
                 '[node name="Collision" type="CollisionShape3D" parent="."]',
                 'shape = SubResource("shape")',
                 assets._position_line(collision.get("offset"), True) or '',
                 '[node name="Model" parent="." instance=ExtResource("model")]',
                 'transform = ' + transform_text(player.matrix_world.inverted()),
                 '[node name="Components" type="Node" parent="."]',
                 'script = ExtResource("components")']
        if materials:
            environment, env_files, _camera = assets.build_environment_nodes(
                scene, scene_name="GKDefaultPlayer", material_files=material_files, material_only=True)
            ext.append('[ext_resource type="Script" path="res://scripts/gk_environment_GKDefaultPlayer.gd" id="env_script"]')
            nodes.append(environment)
            for relative, content in env_files.items():
                path = Path(directory) / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                result["written"].append(str(path))
        for index, piece in enumerate(assets.collision_pieces(player)):
            shape = assets.build_collision(piece, True, piece.gk_physics)
            if shape:
                sid = f"piece_{index}"
                resources.append(shape["sub_resource"].replace('id="shape"', f'id="{sid}"'))
                nodes.extend([f'[node name="Collision{index}" type="CollisionShape3D" parent="."]',
                              f'shape = SubResource("{sid}")', 'transform = ' + transform_text(
                                  player.matrix_world.inverted() @ piece.matrix_world)])
        attachments = []
        for obj in scene.objects:
            if obj.type != "LIGHT" or not owns(obj, player):
                continue
            block = assets._light_node_text(obj)
            if not block:
                continue
            block = re.sub(r'transform = Transform3D\([^\n]+\)',
                           'transform = ' + transform_text(player.matrix_world.inverted() @ obj.matrix_world), block)
            nodes.append(block)
            attachments.append((assets._sanitize_node_name(obj.name),
                                assets._sanitize_node_name(obj.parent.name) if obj.parent != player else "",
                                assets._light_follow_camera(obj)))
        import json
        config = json.dumps(attachments, ensure_ascii=False)
        component_source = COMPONENT_SCRIPT.replace("__ATTACHMENTS__", config)
        text = ('; 由 BlenderGameKit 自动生成\n[gd_scene load_steps=%d format=3]\n\n' % (1 + len(ext) + len(resources))
                + '\n'.join(ext + resources + nodes) + '\n')
        files = {"scenes/GKDefaultPlayer.tscn": text,
                 "scripts/gk_player_components.gd": component_source,
                 "scripts/gk_world.gd": WORLD_SCRIPT.replace("__DEFAULT_CAMERA__",
                     assets._gd_quoted(exporter.resolve_main_camera(scene)[0]))}
        for relative, content in files.items():
            path = Path(directory) / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            result["written"].append(str(path))
        _autoload(directory, True)
        # 自动识别的告警(如果有)并进这次导出的报告 —— 上面已经取过一次,
        # 这次取的是识别过程中(逐物体问 owns())之后新攒下的,不会重复。
        result.setdefault("warnings", [])
        result["warnings"] = list(result["warnings"]) + take_infer_warnings()
        return result
    finally:
        if previous is not None:
            assets._activate_scene(previous)


COMPONENT_SCRIPT = '''# 由 BlenderGameKit 自动生成
extends Node
var lamps: Array[Node3D] = []
# 角色身体从中心往外有多厚(米)。手电要放到身体**前面**才不会自挡,
# 这个值在 _attach 里量一次(见 _gk_measure_reach)。
var _gk_reach := 0.65
func _ready() -> void:
    call_deferred("_attach")
func _attach() -> void:
    var pawn := get_parent()
    for entry in __ATTACHMENTS__:
        var lamp := pawn.get_node_or_null(NodePath(entry[0])) as Node3D
        if lamp == null:
            continue
        if entry[2]:
            lamps.append(lamp)
        elif not entry[1].is_empty():
            var holder := pawn.get_node("Model").find_child(entry[1], true, false)
            if holder != null:
                lamp.reparent(holder, true)
    _gk_reach = _gk_measure_reach(pawn)

## 量"角色身体从原点往外有多厚"(水平方向,取网格包围盒的角)。
## ── 为什么需要(第 129 轮:用户"第三人称下手电筒的位置太靠后")────────
## 手电是按『跟随相机』实现的:每帧把灯**整个**抄成相机的变换。
## 第一人称下相机≈眼睛,没问题;但**第三人称下相机在角色身后约 3.5~5 米**,
## 灯就跟着退到背后 —— 光从背后射过来,角色正面不亮、身前一大片影子,
## 而且该照亮的地方全被角色自己挡住。
## 修法是把灯挪到"角色身上、再往视线前方让出身体厚度"的位置,
## 所以先得知道身体有多厚。**只在 _attach 里量一次**(网格不会中途变形),
## 每帧再去扫一遍 MeshInstance3D 是白费性能。
## 量不到网格时退回 0.65(胶囊半径 0.5 + 余量),不影响可用性。
func _gk_measure_reach(pawn: Node) -> float:
    if not (pawn is Node3D):
        return 0.65
    var center := (pawn as Node3D).global_position
    var reach := 0.0
    for found in pawn.find_children("*", "MeshInstance3D", true, false):
        var mi := found as MeshInstance3D
        if mi == null or mi.mesh == null:
            continue
        var box := mi.get_aabb()
        var xf := mi.global_transform
        # 八个角里离角色中心最远的**水平**距离(竖直厚度不影响手电前伸)
        for index in range(8):
            var corner := box.position + Vector3(
                box.size.x if (index & 1) else 0.0,
                box.size.y if (index & 2) else 0.0,
                box.size.z if (index & 4) else 0.0)
            var world: Vector3 = xf * corner
            reach = maxf(reach, Vector2(world.x - center.x, world.z - center.z).length())
    if reach <= 0.0:
        return 0.65
    # +0.15:让灯离身体表面有一点空隙,免得贴在皮肤上产生条纹
    return reach + 0.15

func _process(_delta: float) -> void:
    var camera := get_viewport().get_camera_3d()
    if camera == null:
        return
    var pawn := get_parent()
    var forward := -camera.global_transform.basis.z
    # 角色在**视线方向**上的距离:第一人称≈0(灯留在原地),
    # 第三人称≈相机到角色的正前方距离(灯跟到角色身上)。
    var depth := 0.0
    if pawn is Node3D:
        depth = ((pawn as Node3D).global_position
            - camera.global_position).dot(forward)
    for lamp in lamps:
        lamp.global_transform = camera.global_transform
        # depth > 0 = 角色在相机前面(第三人称就是这么用的)。
        # depth <= 0 时(角色在相机后面 / 纯场景相机)保持老行为:灯就贴在相机上。
        if depth > 0.0:
            lamp.global_position = (camera.global_position
                + forward * (depth + _gk_reach))
'''


WORLD_SCRIPT = '''# 由 BlenderGameKit 自动生成
extends Node
# One pawn instance for the whole game, including cameras, inventory and children.
var player: CharacterBody3D
var changing := false
var active_camera: Camera3D
# 开局那一关的"出生点还没装好"重试标记(见 `_level_ready`)
var _level_retried := false

func _ready() -> void:
    process_mode = Node.PROCESS_MODE_ALWAYS
    get_tree().scene_changed.connect(_level_ready)
    player = load("res://scenes/GKDefaultPlayer.tscn").instantiate()
    player.process_mode = Node.PROCESS_MODE_DISABLED
    for camera in player.find_children("*", "Camera3D", true, false):
        camera.current = camera.name == "__DEFAULT_CAMERA__"
    add_child(player)
    # ── 开局那一关必须**自己**接一次(用户实测 bug,第一百二十五轮)──────
    # `scene_changed` 是**引擎启动加载第一个场景时**发出去的,而 autoload 的
    # `_ready()` 那时才刚跑 —— 信号早就响过了,开局这次没有任何人接。
    # 后果是灾难性的、而且很难猜:`_level_ready()` 是**唯一**把玩家从
    # `PROCESS_MODE_DISABLED` 解回 `PAUSABLE` 的地方,于是开局那一关里
    #   * `_physics_process` 从不被调用 -> WASD 不动(连重力都不跑);
    #   * `_input` 一并停摆 -> 鼠标转不了视角;
    #   * 玩家也没被搬到 `GKPlayerStart`,停在 (0,0,0) 里。
    # 换一次关卡之后一切正常(那次信号有人接)—— 所以现象正好是
    # "只有开局不能动"。用户报的"运行后不能移动与旋转视角"就是它。
    # 用 `call_deferred`:此刻 `current_scene` 还在装配,拿不到出生点。
    _level_ready.call_deferred()

func _level_ready() -> void:
    var level := get_tree().current_scene
    if level == null or not is_instance_valid(player):
        return
    # ── 兜底:起手这一帧可能还没装好,下一帧再试一次 ────────────────────
    # 只在这里重试(而不是"每帧都扫"):`_process` 里挂一个每秒 60 次的
    # 检查纯属浪费,而且一旦有人手动把玩家设成 DISABLED 想省性能,
    # 我们的兜底会把它偷偷开回来。
    if level.get_node_or_null("GKPlayerStart") == null and not _level_retried:
        _level_retried = true
        _level_ready.call_deferred()
        return
    var spawn := level.get_node_or_null("GKPlayerStart") as Node3D
    if spawn != null:
        player.global_transform = spawn.global_transform
    player.velocity = Vector3.ZERO
    # Spring arms cache world positions; a teleport must not interpolate across levels.
    for property in player.get_property_list():
        if String(property.name).begins_with("_gk_spring_ready") or String(property.name).begins_with("_gk_spring_base_yaw_ready"):
            player.set(property.name, false)
    player.reset_physics_interpolation()
    player.process_mode = Node.PROCESS_MODE_PAUSABLE
    if is_instance_valid(active_camera):
        active_camera.make_current()
    elif get_viewport().get_camera_3d() == null:
        var cameras := player.find_children("*", "Camera3D", true, false)
        if not cameras.is_empty():
            cameras[0].make_current()
    changing = false
    _level_retried = false

func change_level(path: String) -> void:
    if changing:
        return
    # Validate/load first. An invalid destination must leave this level playable.
    if not ResourceLoader.exists(path):
        push_error("[GK] Level not found: " + path)
        return
    var packed := load(path) as PackedScene
    if packed == null:
        push_error("[GK] Not a scene: " + path)
        return
    changing = true
    active_camera = get_viewport().get_camera_3d()
    player.process_mode = Node.PROCESS_MODE_DISABLED
    var error := get_tree().change_scene_to_packed(packed)
    if error != OK:
        changing = false
        player.process_mode = Node.PROCESS_MODE_PAUSABLE
        push_error("[GK] Could not change level: " + path)
'''
