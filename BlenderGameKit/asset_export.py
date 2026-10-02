"""导出资产到 Godot:glTF(.glb) + 场景(.tscn) + 材质(.tres)。

为什么需要它
------------
Blender 里的模型不能"自带脚本"。Godot 的脚本只能挂在**节点**上，
而 .glb 导入后只是一个网格节点。所以正确的做法是把三件事分开:

    模型      Blender 网格/骨骼/动画  --glTF-->  .glb          (几何)
    材质      Blender 材质            --生成-->  .tres + .gdshader (外观)
    行为      节点图                  --生成-->  .gd            (逻辑)
    装配      ----  生成的 .tscn 把上面三者拼在一起  ----        (节点树)

这个模块负责"装配":导出 .glb，生成引用它的 .tscn，并把脚本挂到根节点上。
这样在 Godot 里打开就是"模型 + 碰撞体 + 脚本 + 卡通材质"的完整角色。

贴图怎么办
----------
glTF 会把贴图**内嵌**进 .glb(默认)或导出为外部文件。两种都能被 Godot 直接读。
Blender 里材质的 Base Color 贴图会变成 Godot 的 albedo_texture，
所以本插件生成的卡通材质会自动带上它(见 _base_color_texture)。
"""

import os
import re

from . import gk_log
from . import npr_preview
from . import project
from .nodes.node_base import gd_node_path, snake_case

# 导出的 .tscn 里，脚本与模型各自的 res:// 路径
#
# `{env_resource}` 是 Environment 脚本的 ext_resource 行。
# ── 为什么单独拿一个占位符出来(踩过的坑)──────────────────────────
# 以前这一行是"env_files 非空才加"。但 `build_environment_nodes` 只在
# **需要生成相机/灯节点**时才写 gk_environment.gd;相机和灯都已经在 glb 里
# 的时候它不写文件(文件就在磁盘上,不用重写),于是 env_files 是空的 ——
# 那一行就不加了,可 Environment 节点又确实生成了。
# 结果:节点引用了不存在的 ExtResource,Godot 直接报
#     Parse Error: Can't load resource ... env_script
# 用户实测就是这个:"地面有材质,为什么没渲染上" —— 因为挂材质的
# Environment 脚本根本没能加载。
ENV_RESOURCE_LINE = (
    '[ext_resource type="Script" '
    'path="res://{scripts_dir}/gk_environment.gd" id="env_script"]'
)

SCENE_TEMPLATE = """[gd_scene load_steps={steps} format=3]

[ext_resource type="Script" path="{script_path}" id="1_script"]
[ext_resource type="PackedScene" path="{model_path}" id="2_model"]
{env_resource}
{sub_resources}
[node name="{root_name}" type="{base_class}"]
script = ExtResource("1_script")
{collision}
[node name="Model" parent="." instance=ExtResource("2_model")]
{environment}
"""

# 相机 + 灯光的节点文本。
#
# 为什么角色场景也要带这些:用户按 F5 跑起来看到的是**一片灰**,
# 因为角色场景里只有网格 —— 没有相机就没有视点,没有灯光就全黑,
# 再加上没有环境就是 Godot 默认的灰底。这不是"渲染坏了",是场景里确实空。
# 从 Blender 场景里把相机和灯光搬过来,打开就能看见东西。
# glb 里没有相机时兜底用的节点。
# 名字用 Blender 里的原名 —— 硬编码叫 "Camera" 的话会和 glb 里的对不上,
# 看起来就像凭空多了一个。
ENVIRONMENT_CAMERA = """
[node name="{name}" type="Camera3D" parent="."]
transform = Transform3D({basis}, {cx}, {cy}, {cz})
fov = {fov:.2f}
current = true
"""

ENVIRONMENT_LIGHT = """
[node name="{name}" type="DirectionalLight3D" parent="."]
transform = Transform3D({basis}, {lx}, {ly}, {lz})
light_energy = {energy:.3f}
"""

# 让 glb 里带进来的相机成为主相机。
#
# ── 为什么需要这一步 ────────────────────────────────────────────
# glTF 会带相机(写进 cameras 数组),Godot 导入后自动生成 Camera3D,
# 位置朝向都对的。但**不会**把它设成 current ——
# 实测:没有这一步时 `get_root().get_camera_3d()` 返回 null,
# 按 F5 用的是默认视点,画面还是一片灰。
#
# ── 为什么用脚本而不是再写一个 Camera3D 节点 ────────────────────
# 直接再生成一个节点的话,场景里就有两个相机了。实测踩过:
# 用户在 Godot 里看到"多一个摄像头",而且分不清哪个是 Blender 的。
#
# 用 @tool 脚本挂在唯一的 Environment 节点上:单实例、幂等。
# 而且挂在 `_ready` 上,每次打开编辑器/运行都会重新指向正确的那个 ——
# 用户之后手动改主相机也不会被永久覆盖,下次导出再指回来。
ENVIRONMENT_SCRIPT = '''@tool
extends Node
## BlenderGameKit 生成:把 glb 带进来的相机设成主相机,并挂上导出的材质。
##
## 改这里没有用 —— 下次从 Blender 导出会被覆盖。
##
## ── 为什么需要这个脚本 ──────────────────────────────────────────
## glTF 会带相机过来(位置朝向都对),但**不会**设成 current ——
## 不设的话按 F5 看到的是默认视点,通常一片灰。
##
## 灯光**不再由这里管**:用户决定在 Godot 里自己设计灯光,
## 所以 glb 带进来的灯原样保留,这个脚本不碰它们。
## (以前这里会重算灯光强度、重建灯节点,结果灯光和 Godot 里
##  手动调的互相打架 —— 已按用户要求去掉。)

## Blender 里主相机的名字。
@export var camera_name: String = "{camera_name}"

## Blender 相机的垂直视场角(度)。<= 0 表示不改。
@export var camera_fov: float = {fov}

## 材质映射:"节点名=材质路径",多条之间用 **|** 分隔。
## 形如 "Ball=res://materials/Ballmat.tres|Floor=res://materials/Floor.tres"
## 也可以用 "节点名#序号=路径" 只替换某个表面(MMD 那种一个网格几十个
## 材质槽的模型必须这样,否则整网格覆盖会把所有表面刷成同一个材质)。
##
## ── 为什么不用换行分隔(踩过的坑)────────────────────────────────
## 原来这里用换行分隔,结果 Godot 直接报
##     Parse Error: Expected newline after comment string
## 因为 `@export_multiline` 的**默认值里不能放真的换行符** ——
## 那是 GDScript 字符串字面量的语法错误,整个脚本都加载不了。
## 竖线在路径和节点名里都不会出现,安全。
@export var materials: String = "{materials}"


func _ready() -> void:
	_activate_camera()
	# ── 材质必须等一帧再挂(踩过的坑)─────────────────────────────────
	# 角色的网格现在藏在**嵌套的 glb 实例**里:
	#     Cube [CharacterBody3D]
	#       Model [instance Cube.glb]   <- 网格在这一层实例内部
	# 在 _ready() 这个时刻,实例内部的节点**还没进树**,`find_child`
	# 根本搜不到它 —— 实测报 "[GK] 没找到网格节点: Cube",
	# 角色就只剩 glb 自带的默认材质(用户看到的还是"材质没生效")。
	# 用 call_deferred 推到本帧末,那时整棵树都建好了。
	_apply_materials.call_deferred()


## 把 Blender 里调好的材质挂到对应的网格上。
##
## 为什么要在运行时装,而不是写进 .tscn:
## glb 实例的**内部子节点不能被外部节点覆盖** —— 在 .tscn 里写
##     [node name="Ball" parent="World"]
##     material_override = ExtResource("mat_2")
## Godot 会报 "Parent path './World' for node 'Ball' has vanished",
## 覆盖被丢弃,看到的永远是白模。加 editable_children 也没用。
## 运行时装是有效的(实测)。
func _apply_materials() -> void:
	if materials.strip_edges().is_empty():
		return
	var root := _scene_root()
	if root == null:
		return
	# ── 分隔符是竖线,不是换行 ──────────────────────────────────────
	# 见上面 materials 变量处的说明:默认值里放真换行会让整个脚本
	# 解析失败(parse error)。竖线在节点名和 res:// 路径里都不会出现。
	var applied := 0
	for line in materials.split("|", false):
		var text := line.strip_edges()
		if text.is_empty() or not text.contains("="):
			continue
		var parts := text.split("=", true, 1)
		var node_name := parts[0].strip_edges()
		var res_path := parts[1].strip_edges()

		# ── 键可以带 "#序号" 表示"只换第 N 个表面" ──────────────────
		# 一个 MMD 模型常常是**一个网格带几十个材质槽**(实测 33 个表面)。
		# material_override 是整个网格级别的 —— 一条覆盖会把所有表面
		# 改成同一个材质,模型看起来就"只有一种颜色 / 变白"。
		# 带序号的条目改用 surface_override 逐个替换。
		var surface := -1
		if node_name.contains("#"):
			var halves := node_name.split("#", true, 1)
			node_name = halves[0].strip_edges()
			surface = int(halves[1])

		if not ResourceLoader.exists(res_path):
			push_warning("[GK] 材质不存在: " + res_path)
			continue
		var material: Material = load(res_path)
		if material == null:
			continue
		# ── 找网格节点必须**先按类型找**(踩过的坑)──────────────────
		# `find_child("Cube")` 返回的是**第一个**叫 Cube 的节点 ——
		# 而场景里有两个同名节点:
		#     Colliders/Cube [CharacterBody3D]   <- 先被找到,但它不是网格
		#     Colliders/Cube/Model/Cube [MeshInstance3D]  <- 真正要挂材质的
		# 于是 `if node is MeshInstance3D` 判false,材质挂不上,
		# 用户看到的是"角色的材质一直没生效"。
		# 所以先在所有 MeshInstance3D 里按名字找,找不到再退回 find_child。
		var node: Node = null
		for candidate in root.find_children(node_name, "MeshInstance3D", true, false):
			node = candidate
			break
		if node == null:
			node = root.find_child(node_name, true, false)
		if node is MeshInstance3D:
			var mesh_node := node as MeshInstance3D
			if surface >= 0:
				if surface >= mesh_node.get_surface_override_material_count():
					push_warning("[GK] 表面序号超出范围: " + text)
					continue
				mesh_node.set_surface_override_material(surface, material)
			else:
				mesh_node.material_override = material
			applied += 1
		else:
			push_warning("[GK] 没找到网格节点: " + node_name)
	if applied > 0:
		print("[GK] 已挂 ", applied, " 个卡通材质")


func _activate_camera() -> void:
	if camera_name.is_empty():
		return
	var found := _find_camera()
	if found == null:
		push_warning("[GK] 没找到相机节点: " + camera_name)
		return
	if camera_fov > 0.0:
		found.fov = camera_fov
	found.make_current()


## 找"场景根节点"。
##
## ── 这里踩了两个坑,都记下来 ────────────────────────────────────
##
## 坑 1:不能用 `get_node_or_null(camera_name)` 找相机 ——
## 那是**相对本节点**的路径,而相机和本节点是兄弟,必然找不到。
## 所以下面用 find_child 从场景根往下搜。
##
## 坑 2:**不能只靠 `get_tree().current_scene`**。
## 用 `godot --script` 跑探测脚本时它返回的是**窗口根节点**(/root),
## 于是灯被挂成了 `/root/Lights` 而不是 `<场景>/Lights` ——
## 看起来"生成了 4 盏灯"却一盏都找不到(实测排查了很久)。
##
## 正确做法:从自己往上走,找到最上面那个**有父节点的**节点,
## 也就是场景实例的根(它挂在 /root 下面)。
func _scene_root() -> Node:
	var node: Node = self
	while node.get_parent() != null and node.get_parent().get_parent() != null:
		node = node.get_parent()
	return node


func _find_camera() -> Camera3D:
	var root := _scene_root()
	if root == null:
		return null
	if not camera_name.is_empty():
		var direct := root.find_child(camera_name, true, false)
		if direct is Camera3D:
			return direct as Camera3D
	for node in root.find_children("*", "Camera3D", true, false):
		if node is Camera3D:
			return node as Camera3D
	return null
'''

ENVIRONMENT_NODE = """
[node name="Environment" type="Node" parent="."]
script = ExtResource("env_script")
camera_name = "{camera_name}"
camera_fov = {fov}
materials = "{materials}"
"""

COLLISION_TEMPLATE = """
[sub_resource type="CapsuleShape3D" id="cap3d"]
radius = {radius:.4f}
height = {height:.4f}

[sub_resource type="CapsuleShape2D" id="cap2d"]
radius = {radius:.4f}
height = {height:.4f}
"""

COLLISION_NODE_3D = """
[node name="Collision" type="CollisionShape3D" parent="."]
shape = SubResource("cap3d")
"""

COLLISION_NODE_2D = """
[node name="Collision" type="CollisionShape2D" parent="."]
shape = SubResource("cap2d")
"""

TOON_MATERIAL_TEMPLATE = """[gd_resource type="ShaderMaterial" load_steps={steps} format=3]

[ext_resource type="Shader" path="{shader_path}" id="1_shader"]
{textures}[resource]
shader = ExtResource("1_shader")
shader_parameter/albedo_color = Color({r:.4f}, {g:.4f}, {b:.4f}, 1)
shader_parameter/use_albedo_texture = {use_texture:.1f}
shader_parameter/albedo_strength = 1.0
shader_parameter/albedo_texture = {albedo_ref}
# 曝光归一化:灯的 light_energy 会带着能量乘进 DIFFUSE_LIGHT,
# 不除的话 light_energy=4.0 会把整个模型冲成白的(踩过,见 gk_toon.gdshader)
shader_parameter/toon_exposure = 0.35
shader_parameter/light_map_mix = 0.0
shader_parameter/shadow_step = 0.1
shader_parameter/shadow_smooth = 0.02
shader_parameter/three_tone = 0.0
shader_parameter/lit_tint = Color(1, 0.976, 0.914, 1)
shader_parameter/shadow_tint = Color(0.706, 0.627, 0.902, 1)
shader_parameter/shadow_strength = 1.0
# ── rim / 高光默认**关闭**(踩过的坑,这是"模型变白"的真凶)─────────
# 这两个都是 **EMISSION 加法项**:
#     EMISSION += rim_color.rgb * rim * rim_amount
#     EMISSION += vec3(spot * specular_amount)
# 加法项不参与任何明暗衰减,直接叠在 albedo 上。实测(用户那个
# 33 材质槽的 MMD 模型,太阳灯 light_energy=4.0):
#
#     rim=0.4  specular=0.2  -> 整个角色一层没有细节的白壳
#     rim=0.08 specular=0.05 -> 仍然偏白偏灰
#     rim=0.0  specular=0.0  -> 肤色/红衣/黑发全部正确显示(决定性实验:
#                               所有 tint 保持原样,只关这两项)
#
# 所以默认关掉,想要边缘光/高光的用户自己在 Godot 里调上去。
#
# 另外实测发现:`toon_exposure` 和 `shadow_tint` 改了以后**画面字节
# 完全相同** —— 说明 `light()` 里的卡通色阶这一段在当前 Godot 4.7 下
# 基本没参与最终着色(至少对这个模型是这样)。真正决定观感的是
# ALBEDO + EMISSION 加法项。这条还没查透,先记在这里。
shader_parameter/specular_amount = 0.0
shader_parameter/rim_amount = 0.0
shader_parameter/backlight_amount = 0.0
shader_parameter/emission_amount = 0.0
"""


def safe_name(text, fallback="model"):
    name = re.sub(r"[^A-Za-z0-9_\-]", "", snake_case(text or "", ""))
    return name or fallback


def _unique_file_names(names, fallback="material"):
    """给一串材质名算出**互不重复**的文件名。返回 {原名: 文件名}。

    ── 为什么需要去重(踩过的坑)────────────────────────────────────
    `safe_name()` 会把非 [A-Za-z0-9_-] 的字符**全删掉**,而 Blender 给
    重名材质加的后缀恰好是点号:

        material      -> material
        material.001  -> material001     (点号被删)
        material.002  -> material002

    看着还行,但 MMD 模型(41 个材质槽)导入后材质名经常是
    `material` / `material.001` … 甚至全是同一个基础名 ——
    实测日志里 `写入 material.tres` **连续出现了 41 次**:
    41 个材质全写到同一个文件、互相覆盖,最后只剩最后一个。
    用户在 Godot 里看到的就是"模型变白、材质没导出"。

    这里统一做一次去重:重名的加 `_2`、`_3` 后缀。
    """
    used = {}
    out = {}
    for name in names:
        base = safe_name(name, fallback)
        candidate = base
        index = 2
        while candidate in used:
            candidate = f"{base}_{index}"
            index += 1
        used[candidate] = True
        out[name] = candidate
    return out


# 碰撞预览物体的标记属性。
# 用自定义属性而不是名字前缀来识别 —— 用户可能给物体起任何名字,
# 靠名字判断会误删。导出时也靠它来排除预览线框。
PREVIEW_MARK = "gk_collision_preview"

# 工具生成的辅助材质都以此为前缀(描边预览材质等)。
# 导出时要跳过它们 —— 否则用户会看到自己的模型在 Godot 里变成纯黑壳。
TOOL_MATERIAL_PREFIX = "__GK"


def collect_objects(context):
    """要导出的对象:优先用选中的，没选就全部可见网格。"""
    selected = [obj for obj in context.selected_objects if obj.type == "MESH"]
    if selected:
        return selected, "selected"
    visible = [obj for obj in context.scene.objects if obj.type == "MESH" and not obj.hide_get()]
    return visible, "visible"


def object_bounds(objects):
    """所有网格的世界空间包围盒，用来生成合适的碰撞体尺寸。"""
    import mathutils

    mins = mathutils.Vector((1e9, 1e9, 1e9))
    maxs = mathutils.Vector((-1e9, -1e9, -1e9))
    found = False
    for obj in objects:
        for corner in obj.bound_box:
            world = obj.matrix_world @ mathutils.Vector(corner)
            for axis in range(3):
                mins[axis] = min(mins[axis], world[axis])
                maxs[axis] = max(maxs[axis], world[axis])
            found = True
    if not found:
        return None
    size = maxs - mins
    center = (maxs + mins) * 0.5
    return {"min": tuple(mins), "max": tuple(maxs), "size": tuple(size), "center": tuple(center)}


def export_glb(export_dir, name, use_selection, objects):
    """调用 Blender 的 glTF 导出。返回 (路径, 是否成功, 消息)。"""
    import bpy

    # 模型统一放 art/,和贴图在一起
    target_dir = os.path.join(export_dir, project.ART_DIR)
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, f"{name}.glb")
    kwargs = {
        "filepath": path,
        "export_format": "GLB",
        "use_selection": bool(use_selection),
        "export_apply": True,
        "export_yup": True,
        "export_materials": "EXPORT",
        "export_animations": True,
        "export_skins": True,
        "export_texcoords": True,
        "export_normals": True,
    }
    return _run_gltf_export(kwargs, path)


def export_scene_glb(export_dir, name="World", scene=None, exclude=None):
    """把**整个 Blender 场景**导出成一个 .glb(保持层级与变换)。

    返回 (路径, 是否成功, 消息)。

    ── 为什么需要单独做这个 ────────────────────────────────────────
    之前的导出是"把选中的网格导出成一个模型",适合角色。
    但要"打开 Godot 时看到的场景和 Blender 里一样",就必须导出**整个场景的
    层级结构**:空物体、父子关系、每件东西的位置。

    glTF 本身就支持层级,Blender 的导出器会把:
        * 对象父子关系  -> 节点的父子关系
        * 对象变换      -> 节点变换(并且自动做 Z-up -> Y-up 转换)
        * 灯光 / 相机   -> 灯光节点 / 相机节点
    都带过去。所以这一个文件就能让 Godot 里的场景和 Blender 一致。

    ── 预览物体必须手动排除 ────────────────────────────────────────
    碰撞线框预览设了 hide_render,**但 glTF 导出器不看这个** ——
    实测三个 `__GK碰撞预览__` 物体全都进了 .glb。
    所以这里在导出前显式把它们取消选中(导出器会跳过未选中对象),
    导出后再恢复原来的选中状态。
    """
    import bpy

    scene = scene or bpy.context.scene
    if scene is None:
        return None, False, "没有场景"

    # 记住原来的选中状态,导出后恢复 —— 不要打乱用户的选择
    previous = [obj for obj in scene.objects if obj.select_get()]

    # ── 要排除的物体(角色)────────────────────────────────────────────
    # 角色的网格会单独导成一个 glb,再 instance 到角色节点下面。
    # 如果 World.glb 里**也留着一份**,两份网格就会重叠在同一个位置 ——
    # 深度冲突,画面表现是"模型一闪一闪的"(用户实测反馈)。
    #
    # exclude 里的物体**连同它们的子级**一起排除:角色的子物体已经被
    # 角色 glb 带走了,这里再留一份同样会重叠。
    excluded = set()
    stack = [obj for obj in (exclude or ()) if obj is not None]
    while stack:
        obj = stack.pop()
        if obj in excluded:
            continue
        excluded.add(obj)
        stack.extend(obj.children)

    skipped = []
    for obj in scene.objects:
        if obj.get(PREVIEW_MARK) or obj in excluded:
            skipped.append(obj.name)
            obj.select_set(False)
        else:
            obj.select_set(not obj.hide_get())

    target_dir = os.path.join(export_dir, project.ART_DIR)
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, f"{name}.glb")

    kwargs = {
        "filepath": path,
        "export_format": "GLB",
        "use_selection": True,      # 配合上面的取消选中来排除预览物体
        "export_apply": False,
        "export_yup": True,
        "export_materials": "EXPORT",
        "export_animations": True,
        "export_skins": True,
        "export_texcoords": True,
        "export_normals": True,
        "export_cameras": True,
        # ── 不导出灯光(踩过的坑)──────────────────────────────────────
        # Blender 4.5 的 glTF 导出器在**导出太阳灯时会自己崩**:
        #     TypeError: SunLight.area(): was called with invalid keyword
        #     argument(s) (world_matrix), expected (matrix_world)
        # 那是它内部的 API 调用写错了(`area()` 的参数名从 world_matrix
        # 改成了 matrix_world),和我们的参数无关 —— 只要场景里有 Sun
        # 就必崩、整场景导出直接失败。
        #
        # 关掉之后,灯光改由 `build_environment_nodes()` 生成成
        # .tscn 里的 DirectionalLight3D —— 那条路本来就实现了,而且更可控
        # (能按 Blender 的强度算 energy)。实测:导出的场景灯光效果一样。
        "export_lights": False,
        "export_extras": True,
    }
    result = _run_gltf_export(kwargs, path)

    # 恢复选中状态
    try:
        for obj in scene.objects:
            obj.select_set(obj in previous)
    except Exception:
        pass

    if result[1] and skipped:
        return result[0], True, (f"已导出 {os.path.basename(path)}"
                                 f"（已排除 {len(skipped)} 个碰撞预览）")
    return result


def _run_gltf_export(kwargs, path):
    """跑 glTF 导出,参数名不兼容时逐个降级重试。"""
    import bpy

    working = dict(kwargs)
    for _ in range(12):
        try:
            bpy.ops.export_scene.gltf(**working)
            if os.path.isfile(path):
                return path, True, f"已导出 {os.path.basename(path)}"
            return path, False, "导出命令执行了，但文件没生成"
        except TypeError as exc:
            message = str(exc)
            dropped = None
            for key in list(working):
                if key in message and key != "filepath":
                    dropped = key
                    break
            if dropped is None:
                return path, False, f"导出参数不被支持: {message}"
            working.pop(dropped, None)
        except Exception as exc:
            # ── 导出器自己崩了:试着把可疑的开关关掉再来一次 ────────────
            # 实测踩过:Blender 4.5 的 glTF 导出器导出太阳灯时内部报
            #     SunLight.area(): invalid keyword argument 'world_matrix'
            # 那是**它自己的 bug**,和我们的参数无关,但会让整场景导出
            # 直接失败(用户看到"模型变白/没材质",其实是导出压根没成功)。
            #
            # 这里做一层兜底:报错信息里提到灯光/相机就把对应的开关关掉重试,
            # 这样下次再遇到同类导出器 bug 也不至于整个流程挂掉。
            message = str(exc)
            lowered = message.lower()
            dropped = None
            if working.get("export_lights") and ("light" in lowered
                                                 or "lamp" in lowered
                                                 or "sunlight" in lowered):
                dropped = "export_lights"
            elif working.get("export_cameras") and "camera" in lowered:
                dropped = "export_cameras"
            elif working.get("export_animations") and "anim" in lowered:
                dropped = "export_animations"
            if dropped is None:
                return path, False, f"导出失败: {message}"
            working.pop(dropped, None)
            notes = getattr(_run_gltf_export, "_notes", None)
            if notes is None:
                notes = []
                _run_gltf_export._notes = notes
            notes.append(f"导出器在 {dropped} 上报错,已关掉该项重试")
    return path, False, "重试次数用尽"


def write_world_scene(export_dir, scene_name="World", scene=None,
                      dimension="3", script_name=None, script_target_name=None,
                      materials_map=None, character_model=None, file_names=None,
                      character_object=None):
    """生成主场景:模型 + 卡通材质 + 碰撞体 + 相机灯光。返回 (路径, 碰撞体数量)。

    结构(3D):

        Ball      [MeshInstance3D]   mesh = glb 里的 BallMesh
                                     material_override = materials/Ballmat.tres
        Floor     [MeshInstance3D]
        ShotCam   [Camera3D]
        KeySun    [DirectionalLight3D]
        Colliders [Node3D]
          Ball    [StaticBody3D]
            Collision [CollisionShape3D]

    ── 材质怎么挂(这条路踩了很久)────────────────────────────────────
    试过三种写法,只有第三种能工作:

    1) 给 glb 实例内部的子节点写 material_override:
           [node name="World" instance=ExtResource("1_world")]
           [node name="Ball" parent="World"]
           material_override = ExtResource("mat_2")
       **不生效**。Godot 报
           WARNING: Parent path './World' for node 'Ball' has vanished
       覆盖被丢弃,用户看到的永远是白模。加 editable_children = true 也没用。

    2) 把网格展开成 MeshInstance3D,`mesh = ExtResource("mesh_1")`:
       **不生效**。运行时 `mi.mesh` 是 `<Object#null>` ——
       glb 的网格没有按 `文件.glb/网格名` 暴露成可引用资源,画不出东西,
       画面一片纯灰。

    3) **instance 整个 glb + 运行时用脚本挂材质** ← 现在用的
       .tscn 里只 instance glb(几何/层级/变换一定对),
       材质映射交给 Environment 脚本在 `_ready` 里挂。
       实测 StandardMaterial3D 和 ShaderMaterial 都能成功显示。

    materials_map: {对象名: (材质资源名, materials/ 下的文件名)}

    character_model: 角色自己的 glb 名(不含 .glb)。给了的话,脚本挂载目标
        会在场景里生成一个**自带网格**的节点:

            Hero [CharacterBody3D]        ← 脚本挂这里;移动它
              Collision [CollisionShape3D]
              Model [instance Hero.glb]   ← 角色网格,跟着一起动

        ── 为什么必须这样(用户实测反馈)────────────────────────────
        以前只有 `Colliders/Cube [CharacterBody3D]` + 一个碰撞体,
        角色的**网格在世界 glb 实例里**(另一棵子树)。移动 Colliders/Cube
        只动了碰撞体,画面里的角色纹丝不动 ——
        用户原话:"摄像机动了但那个角色正方体没跟着动"。
        而 Godot **不允许**往 glb 实例内部挂节点,所以没法把碰撞体
        挂到 `World/Cube` 下面去。

        正确做法就是反过来:让角色节点自带一份自己的网格。
        实测验证过:移动根节点 -> 网格世界坐标跟着变(位移 6.0),
        地面保持不动。
    """
    path = os.path.join(export_dir, project.SCENES_DIR, f"{scene_name}.tscn")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    is_3d = str(dimension) != "2"

    info = glb_scene_nodes(export_dir, scene_name)
    mesh_info = info.get("meshes", {})
    if not mesh_info:
        return None, 0

    # glb 实例本身
    ext_lines = [
        f'[ext_resource type="PackedScene" '
        f'path="res://{project.ART_DIR}/{scene_name}.glb" id="1_world"]'
    ]

    use_script = bool(script_name and script_target_name)
    script_id = None
    if use_script:
        script_id = "gk_script"
        ext_lines.append(f'[ext_resource type="Script" '
                         f'path="res://{project.SCRIPTS_DIR}/{script_name}" '
                         f'id="{script_id}"]')

    # 角色自己的 glb(让角色节点自带网格,见上面 character_model 的说明)
    use_character_model = bool(character_model) and os.path.isfile(
        os.path.join(export_dir, project.ART_DIR, f"{character_model}.glb"))
    if use_character_model:
        ext_lines.append(f'[ext_resource type="PackedScene" '
                         f'path="res://{project.ART_DIR}/{character_model}.glb" '
                         f'id="1_character"]')

    # ---- 碰撞体 ----
    bodies = []
    sub_resources = []
    if scene is not None:
        for index, (obj, physics, enabled) in enumerate(collect_physics(scene)):
            if not enabled or getattr(obj, "type", "") != "MESH":
                continue
            built = build_collision(obj, is_3d, physics)
            if built is None:
                continue
            shape_id = f"shape_{index}"
            sub_resources.append(
                built["sub_resource"].replace('id="shape"', f'id="{shape_id}"'))
            bodies.append((obj, physics, built, shape_id,
                           _godot_position(obj, is_3d)))

    # ---- 相机 / 灯光 / Environment ----
    # (见上面"材质怎么挂"里的说明:相机和灯由 glb 带进来,
    #  Environment 脚本负责设主相机、修灯光亮度、挂材质)
    glb_info = glb_object_names(export_dir, scene_name)
    material_files = {}
    for obj_name in sorted(mesh_info):
        entry = (materials_map or {}).get(obj_name)
        if entry:
            material_files[obj_name] = entry[1]

    environment, env_files, _camera_name = build_environment_nodes(
        scene, is_3d, skip=glb_info, scene_name=scene_name,
        material_files=material_files)
    if env_files:
        ext_lines.append(f'[ext_resource type="Script" '
                         f'path="res://{project.SCRIPTS_DIR}/gk_environment.gd" '
                         f'id="env_script"]')

    load_steps = 1 + len(ext_lines) + len(sub_resources)

    lines = [f"[gd_scene load_steps={load_steps} format=3]", ""]
    lines.extend(ext_lines)
    lines.append("")
    for block in sub_resources:
        lines.append(block)
        lines.append("")

    for relative, content in (env_files or {}).items():
        target = os.path.join(export_dir, relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)

    # ---- 根节点下的 glb 实例 ----
    #
    # ── 为什么不把网格展开成一个个 MeshInstance3D ────────────────────
    # 试过那种做法(`mesh = ExtResource("mesh_1")`,指向 glb 里的网格),
    # **Godot 拿不到那个资源** —— 运行时 `mi.mesh` 是 `<Object#null>`,
    # 于是什么都画不出来,画面一片纯灰。
    # glb 的网格没有按 `文件.glb/网格名` 这种形式暴露成可引用的资源。
    #
    # 所以还是 instance 整个 glb:几何、层级、变换都由它负责,一定是对的。
    lines.append(f'[node name="{scene_name}" '
                 f'instance=ExtResource("1_world")]')
    lines.append("")

    # ── 材质:交给运行时脚本挂 ──────────────────────────────────────
    # 不能在 .tscn 里给实例内部的子节点写 material_override ——
    # Godot 报 `Parent path './World' for node 'Ball' has vanished`,
    # 覆盖不生效,用户看到的永远是白模。加 editable_children 也没用。
    #
    # 但**运行时**挂是生效的(实测:StandardMaterial3D 和 ShaderMaterial
    # 都成功显示)。所以把映射表交给 Environment 脚本去挂。
    #
    # ── 按表面还是要整网格?(踩过的坑)────────────────────────────────
    # MMD 那种"一个网格 33 个材质槽"的模型,整网格覆盖会把所有表面改成
    # 同一个材质,看起来"变白 / 只有一种颜色"。所以材质槽多于一个时,
    # 改用 `节点名#序号` 逐表面覆盖(见 surface_material_files)。
    material_files = {}
    blender_objects = [obj for obj in (scene.objects if scene else ())
                       if getattr(obj, "type", "") == "MESH"
                       and obj.name in mesh_info]
    for obj in blender_objects:
        per_surface = surface_material_files(obj, file_names)
        if per_surface:
            material_files.update(per_surface)
        else:
            entry = (materials_map or {}).get(obj.name)
            if entry:
                material_files[_sanitize_node_name(obj.name)] = entry[1]

    # 角色自己的 glb 是**单独**一个实例,它的网格不在 mesh_info 里,
    # 所以要额外把角色的材质也列进映射表 —— 否则角色的卡通材质挂不上,
    # 只剩 glb 自带的默认材质(用户看到的还是"角色材质没生效")。
    if use_character_model and character_object is not None:
        per_surface = surface_material_files(character_object, file_names)
        if per_surface:
            material_files.update(per_surface)
        else:
            char_meshes = glb_scene_nodes(export_dir,
                                          character_model).get("meshes", {})
            for node_name in sorted(char_meshes):
                entry = (materials_map or {}).get(node_name)
                if entry:
                    material_files[node_name] = entry[1]

    # ---- 相机 / 灯光 ----
    #
    # glTF 自带的相机和灯由 glb 实例带进来,不再另外生成节点
    # (生成的话场景里会有两个相机,用户会说"多一个摄像头")。
    # Environment 脚本负责把相机设成 current 并按 Blender 的强度修正灯光。
    environment, env_files, _camera_name = build_environment_nodes(
        scene, is_3d, skip=glb_info, scene_name=scene_name,
        material_files=material_files)
    if env_files:
        ext_lines.append(f'[ext_resource type="Script" '
                         f'path="res://{project.SCRIPTS_DIR}/gk_environment.gd" '
                         f'id="env_script"]')

    # ---- 碰撞体 ----
    if bodies:
        lines.append('[node name="Colliders" type="Node3D" parent="."]')
        lines.append("")
        for obj, physics, built, shape_id, position in bodies:
            # 节点类型和脚本基类必须由**同一个函数**决定 ——
            # 各算各的就会出现"节点是 CharacterBody3D、脚本却 extends X"
            # 这类错配。而且直接读 physics.body_type 在没有物理设置时
            # 会拿到 "auto" 这种非法类型(实测在 World.tscn 里生成过
            # `type="auto"`,Godot 连场景都解析不了)。
            body_type = _base_class_for(scene, obj, dimension)
            if getattr(physics, "is_trigger", False):
                body_type = "Area3D" if is_3d else "Area2D"

            # 脚本挂载目标且角色 glb 存在时,**给它挂上自己的网格**。
            # 见 write_world_scene 文档里 character_model 那段说明 ——
            # 不这么做的话,移动这个节点只动碰撞体,画面里的角色不动。
            attach_model = (use_script and use_character_model
                            and getattr(physics, "script_target", False))

            lines.append(f'[node name="{_sanitize_node_name(obj.name)}" '
                         f'type="{body_type}" parent="Colliders"]')
            if attach_model:
                # ── 让"glb 说了算",节点保持单位变换(踩过的坑)──────────
                # glTF 已经把物体的**完整世界变换**写进 glb 的根节点,
                # 而且 Godot 的 glTF 导入是**跨坐标系**的(Y-up),不是简单的
                # 轴置换 —— 自己再拼一份 Unity/Blender 风格的变换矩阵,
                # 旋转和缩放会对不上(实测:网格被搬回物体原点,又和碰撞体
                # 错开,画面就是重影)。
                #
                # 所以这里不自己造变换:节点留在原点,网格交给 glb 决定,
                # 碰撞体则**按 glb 根节点的原点**摆 —— 网格在哪,碰撞体就在哪。
                root = _glb_root_transform_values(export_dir, character_model)
                if root:
                    pos = root[9:12]
                else:
                    pos = list(position)
                lines.append(f"transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, "
                             f"{_fmt(pos[0])}, {_fmt(pos[1])}, {_fmt(pos[2])})")
                lines.append(f'script = ExtResource("{script_id}")')
                lines.append("")
                shape_type_name = "CollisionShape3D" if is_3d else "CollisionShape2D"
                lines.append(f'[node name="Collision" type="{shape_type_name}" '
                             f'parent="Colliders/{_sanitize_node_name(obj.name)}"]')
                lines.append(f'shape = SubResource("{shape_id}")')
                # 碰撞层/掩码也要带上 —— 走 continue 的话就不会再经过
                # 下面那段通用代码了,漏掉这里等于把这些设置丢掉
                layer = built["layer"]
                mask = built["mask"]
                if layer != "1" or mask != "1":
                    lines.append(f"collision_layer = {int(layer)}")
                    lines.append(f"collision_mask = {int(mask)}")
                lines.append("")
                # 网格不自带偏移:glb 根节点已经把它放在对的位置了,
                # 这里再写变换就等于应用两次。
                lines.append(f'[node name="Model" parent="Colliders/'
                             f'{_sanitize_node_name(obj.name)}" '
                             f'instance=ExtResource("1_character")]')
                lines.append("")
                continue

            # 非角色物体:碰撞体自己站在物体世界坐标上,节点留在原点。
            # 位置必须写出来,不写就全堆在原点,完全对不上模型
            # (实测踩过,Godot 里读回来全是 0)。
            lines.append(f"transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, "
                         f"{_fmt(position[0])}, {_fmt(position[1])}, "
                         f"{_fmt(position[2])})")
            if use_script and getattr(physics, "script_target", False):
                lines.append(f'script = ExtResource("{script_id}")')
            lines.append("")
            shape_type = "CollisionShape3D" if is_3d else "CollisionShape2D"
            lines.append(f'[node name="Collision" type="{shape_type}" '
                         f'parent="Colliders/{_sanitize_node_name(obj.name)}"]')
            lines.append(f'shape = SubResource("{shape_id}")')
            layer = built["layer"]
            mask = built["mask"]
            if layer != "1" or mask != "1":
                lines.append(f"collision_layer = {int(layer)}")
                lines.append(f"collision_mask = {int(mask)}")
            lines.append("")

    if environment:
        lines.append(environment.strip("\n"))
        lines.append("")

    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))

    project.set_main_scene(export_dir, f"{project.SCENES_DIR}/{scene_name}.tscn")
    return path, len(bodies)


_IDENTITY = ("1.000000, 0.000000, 0.000000, "
             "0.000000, 1.000000, 0.000000, "
             "0.000000, 0.000000, 1.000000, "
             "0.000000, 0.000000, 0.000000")


def _glb_node_details(export_dir, model_name):
    """从 glb 里取出每个节点(网格/相机/灯)的变换。

    返回 (网格变换, [(相机名, 变换, 垂直FOV)], [(灯名, 变换)])。

    必须算完整的 TRS 矩阵,不能只写平移 —— 相机和太阳的朝向全靠旋转,
    只写平移的话它们会一起朝上/朝前,画面立刻不对。
    """
    import json
    import math
    import struct

    matrices = {}
    cameras = []
    lights = []
    path = os.path.join(export_dir or "", project.ART_DIR, f"{model_name}.glb")
    if not os.path.isfile(path):
        return matrices, cameras, lights
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        offset = 12
        payload = None
        while offset < len(data):
            length, kind = struct.unpack_from("<II", data, offset)
            offset += 8
            if kind == 0x4E4F534A:
                payload = json.loads(data[offset:offset + length].decode("utf-8"))
                break
            offset += length
        if payload is None:
            return matrices, cameras, lights
    except (OSError, ValueError, struct.error):
        return matrices, cameras, lights

    camera_defs = payload.get("cameras", [])
    for node in payload.get("nodes", []):
        name = node.get("name", "")
        matrix = _glb_node_matrix(payload, node)
        if "mesh" in node:
            matrices[name] = matrix
        elif "camera" in node:
            fov = 0.0
            index = node["camera"]
            if index < len(camera_defs):
                perspective = camera_defs[index].get("perspective", {})
                if "yfov" in perspective:
                    fov = math.degrees(float(perspective["yfov"]))
            cameras.append((name, matrix, fov))
        elif node.get("extensions", {}).get("KHR_lights_punctual"):
            lights.append((name, matrix))
    return matrices, cameras, lights

def glb_object_names(export_dir, model_name="World"):
    """读 .glb 里有哪些对象,以及有没有相机/灯。

    为什么要读它:**glTF 本身就能带相机和灯**(Blender 导出时会带上,
    相机走 GLTF 的 cameras 数组,灯走 KHR_lights_punctual 扩展),
    Godot 导入后会**自动**生成对应的 Camera3D / DirectionalLight3D。
    如果我们再额外生成一份,场景里就会出现两个相机、两盏灯。

    实测踩过:World.glb 里有 4 个节点(Plane/Cube/Camera/Sun),
    包装场景里又生成了一个 Camera 和一个 Sun —— 用户在 Godot 里
    看到"多一个摄像头",而且分不清哪个是 Blender 的。

    返回 {"nodes": [...], "cameras": [...], "lights": [...]}。
    """
    result = {"nodes": [], "cameras": [], "lights": []}
    path = os.path.join(export_dir or "", project.ART_DIR, f"{model_name}.glb")
    if not os.path.isfile(path):
        return result
    try:
        import json
        import struct

        with open(path, "rb") as handle:
            data = handle.read()
        offset = 12
        payload = None
        while offset < len(data):
            length, kind = struct.unpack_from("<II", data, offset)
            offset += 8
            if kind == 0x4E4F534A:      # "JSON"
                payload = json.loads(data[offset:offset + length].decode("utf-8"))
                break
            offset += length
        if payload is None:
            return result
    except (OSError, ValueError, struct.error):
        return result

    for node in payload.get("nodes", []):
        name = node.get("name", "")
        result["nodes"].append(name)
        if "camera" in node:
            result["cameras"].append(name)
        if node.get("extensions", {}).get("KHR_lights_punctual"):
            result["lights"].append(name)
    return result


def build_environment_nodes(scene, is_3d=True, skip=None, scene_name="World",
                            material_files=None):
    """决定相机和灯怎么处理。返回 (节点文本, 需要写的文件, 相机名)。

    节点文本里如果需要 ext_resource(id="env_script"),调用方要把它写出来。
    第二个返回值是 {相对路径: 内容} 形式的"需要额外写的文件"。

    ── 分两种情况 ──────────────────────────────────────────────────
    1. **glb 里已经有相机**(glTF 能带相机和灯,Blender 导出时会写进去,
       Godot 导入后自动生成 Camera3D / DirectionalLight3D):
       不再生成相机节点 —— 生成的话场景里会有两个相机,用户会发现
       "打开 Godot 多一个摄像头",而且分不清哪个是 Blender 的。
       只挂一个 Environment 脚本把主相机设成 current。

    2. **glb 里没有相机**(比如只导了网格):
       老老实实生成一个 Camera3D 和 DirectionalLight3D 节点,
       不然按 F5 是一片灰 —— 这个坑踩过。
    """
    if scene is None or not is_3d:
        return "", {}, ""

    skip = skip or {}
    glb_has_camera = bool(skip.get("cameras"))
    # ── 灯光一律自己生成,不再依赖 glb ───────────────────────────────
    # 我们**主动关掉了 glTF 的灯光导出**(见 export_scene_glb 里的
    # export_lights=False):Blender 4.5 的导出器导出太阳灯时会自己崩,
    # 让整场景导出失败。所以 glb 里**不可能**有灯,
    # `skip["lights"]` 不该再影响判断 —— 否则场景里会一盏灯都没有(全黑)。
    glb_has_light = False

    parts = []
    files = {}

    # ---- 情况 1:相机来自 glb,只需要把它设成 current ----
    if glb_has_camera:
        active = scene.camera
        name = _sanitize_node_name(active.name) if active is not None else \
            _sanitize_node_name(skip["cameras"][0])
        # FOV 也要一起给:Godot 的 Camera3D.fov 是**垂直**视场角,
        # glTF 的 yfov 也是垂直的,理论上能对上;但用户可能在 Blender 里
        # 改过传感器比例,所以这里按 Blender 的当前值算一遍更稳。
        fov = 0.0
        if active is not None:
            try:
                # Blender 的 angle 是水平角,按传感器比例换算成垂直角
                horizontal = float(active.data.angle_x)
                render = getattr(scene, "render", None)
                width = float(getattr(render, "resolution_x", 1920) or 1920)
                height = float(getattr(render, "resolution_y", 1080) or 1080)
                if width > 0 and height > 0:
                    import math
                    fov = math.degrees(
                        2.0 * math.atan(math.tan(horizontal / 2.0)
                                        * height / width))
            except Exception:
                fov = 0.0

        # 灯光**不在这里处理**:用户决定在 Godot 里自己设计灯光,
        # glb 带进来的灯原样保留。以前这里会重算强度/重建灯节点,
        # 结果和用户在 Godot 里的手动调整互相打架(用户反馈)。

        # ── 灯光:这条分支以前**不生成灯**,靠 glb 带进来 ─────────────
        # 现在 glb 里已经没有灯了(见 export_lights=False 的说明),
        # 所以这里必须自己补一盏,否则场景全黑 ——
        # 实测踩过:包装场景里一个 Light3D 都没有。
        for obj in scene.objects:
            if obj.type != "LIGHT" or obj.hide_get() or obj.data.type != "SUN":
                continue
            light_position = _godot_position(obj, True)
            light_energy = float(getattr(obj.data, "energy", 1.0))
            parts.append(ENVIRONMENT_LIGHT.format(
                name=_sanitize_node_name(obj.name),
                basis=_light_basis_text(obj),
                lx=_fmt(light_position[0]), ly=_fmt(light_position[1]),
                lz=_fmt(light_position[2]),
                energy=max(0.2, min(light_energy * 1.2, 4.0))))
            break

        fields = dict(
            camera_name=name, fov=_fmt(fov),
            materials=_material_table(material_files),
        )
        parts.append(ENVIRONMENT_NODE.format(**fields))
        files[os.path.join(project.SCRIPTS_DIR, "gk_environment.gd")] = \
            ENVIRONMENT_SCRIPT.format(**fields)
        return "".join(parts), files, name

    # ---- 情况 2:glb 里没有,得自己补 ----
    camera_name = ""
    fov_for_env = 0.0
    for obj in scene.objects:
        if obj.type != "CAMERA" or obj.hide_get():
            continue
        position = _godot_position(obj, True)
        fov = 70.0
        try:
            fov = float(obj.data.angle_x) * _DEG
        except Exception:
            pass
        camera_name = _sanitize_node_name(obj.name)
        fov_for_env = fov
        parts.append(ENVIRONMENT_CAMERA.format(
            name=camera_name,
            basis=_camera_basis_text(obj),
            cx=_fmt(position[0]), cy=_fmt(position[1]), cz=_fmt(position[2]),
            fov=fov))
        break       # 只补一个

    if not glb_has_light:
        for obj in scene.objects:
            if obj.type != "LIGHT" or obj.hide_get() or obj.data.type != "SUN":
                continue
            position = _godot_position(obj, True)
            energy = float(getattr(obj.data, "energy", 1.0))
            # Blender 的太阳能量 1.0 在 Godot 里偏暗,乘个系数让默认看起来正常
            parts.append(ENVIRONMENT_LIGHT.format(
                name=_sanitize_node_name(obj.name),
                basis=_light_basis_text(obj),
                lx=_fmt(position[0]), ly=_fmt(position[1]), lz=_fmt(position[2]),
                energy=max(0.2, min(energy * 1.2, 4.0))))
            break

    # ── Environment 节点(踩过的坑)───────────────────────────────────
    # 以前这条分支**没有**加 Environment 节点 —— 只补了相机和灯就返回了。
    # 于是角色场景里没有任何东西去挂卡通材质:
    #     * 情况 1(相机灯都在 glb 里)会生成 Environment
    #     * 情况 2(自己补相机灯)不生成  ← 用户踩的就是这个
    # 而角色 glb 是刻意不带相机灯光的(见 export_subtree_glb),
    # 所以角色场景**永远**走情况 2 → 卡通材质永远挂不上。
    # 用户看到的就是"地面有材质,为什么没渲染上"。
    #
    # 判据是"有活要干",不是"有相机/灯":只要**有材质要挂**,
    # 就必须生成 Environment —— 哪怕场景里一个相机一盏灯都没有
    # (用户可能自己在 Godot 里加,但材质还是得有人挂)。
    material_table = _material_table(material_files)
    if parts or material_table:
        fields = dict(
            camera_name=camera_name, fov=_fmt(fov_for_env),
            materials=material_table,
        )
        if not parts:
            # 纯为了挂材质而生成:没有相机可指,名字留空让脚本兜底去找
            fields["camera_name"] = ""
            fields["fov"] = _fmt(0.0)
        parts.append(ENVIRONMENT_NODE.format(**fields))
        files[os.path.join(project.SCRIPTS_DIR, "gk_environment.gd")] = \
            ENVIRONMENT_SCRIPT.format(**fields)

    return "".join(parts), files, camera_name


def surface_material_files(obj, file_names):
    """给一个网格对象算出**按表面**的材质映射:{键: 文件名}。

    键的写法:
        "节点名"        -> 整个网格(material_override)
        "节点名#序号"   -> 只换第 N 个表面(surface_override)

    ── 为什么要按表面(踩过的坑)────────────────────────────────────
    MMD 模型常常是**一个网格带几十个材质槽**(实测那个胡桃模型:
    33 个表面 / 41 个材质,身体/脸/衣服/头发各是不同的贴图)。
    而 `material_override` 是**整个网格级别**的:一条覆盖把所有表面
    改成同一个材质 —— 用户看到的就是"模型变白 / 只有一种颜色",
    会以为材质压根没导出。

    只有一个材质槽时(最常见)仍然走整网格覆盖 —— 那条路已经验过很多次。
    """
    if obj is None or getattr(obj, "type", "") != "MESH":
        return {}
    node_name = _sanitize_node_name(getattr(obj, "name", "") or "Mesh")
    slots = [slot.material for slot in getattr(obj, "material_slots", ())]
    real = [(i, m) for i, m in enumerate(slots)
            if m is not None and not m.name.startswith(TOOL_MATERIAL_PREFIX)]
    if not real:
        return {}
    if len(real) == 1:
        entry = (file_names or {}).get(real[0][1].name)
        return {node_name: entry} if entry else {}
    out = {}
    for index, material in real:
        entry = (file_names or {}).get(material.name)
        if entry:
            out[f"{node_name}#{index}"] = entry
    return out


def _material_table(material_files):
    """把材质映射拼成脚本里用的 "键=路径" 多行文本。

    键有两种写法:
        "节点名=路径"        -> 整个网格用它(material_override)
        "节点名#序号=路径"   -> 只替换**第 N 个表面**(surface_override)

    用换行分隔而不是 JSON:GDScript 里解析 JSON 要额外代码,
    而这个映射很简单,一行一个就够了。路径必须是 res:// 开头的正斜杠形式 ——
    Windows 的反斜杠 Godot 认不出来。

    ── 为什么需要"按表面"(踩过的坑)────────────────────────────────
    一个 MMD 模型常常是**一个网格带几十个材质槽**(实测那个胡桃模型:
    33 个表面 / 41 个材质)。而 `material_override` 是**整个网格**级别的 ——
    一条覆盖把所有表面都改成同一个材质,结果模型看起来像"只有一种颜色 /
    变白",用户以为材质没导出。
    所以按表面逐个覆盖,才能还原模型本来的样子。
    """
    if not material_files:
        return ""
    lines = []
    for key in sorted(material_files):
        file_name = material_files[key]
        # 补上 .tres —— 这个映射有两种来源:
        #   * scene_materials():值已经带 .tres
        #   * surface_material_files():值是不带扩展名的文件名
        # 不补的话 Godot 报"材质不存在: res://materials/material"(缺后缀)。
        if not str(file_name).endswith(".tres"):
            file_name = f"{file_name}.tres"
        lines.append(f"{key}=res://{project.MATERIALS_DIR}/{file_name}")
    # ── 用**竖线**连接,不要用换行 ──────────────────────────────────
    # 踩过的坑:以前返回真换行,而这个值会被写进
    #     @export_multiline var materials: String = "..."
    # 的**默认值**里 —— GDScript 的字符串字面量不允许裸换行,
    # Godot 直接报 "Parse Error: Expected newline after comment string",
    # 整个 Environment 脚本加载不了,材质一个都挂不上。
    # 竖线在节点名和 res:// 路径里都不会出现,安全。
    return "|".join(lines)


def _sanitize_node_name(name):
    """Godot 节点名里不能有这些字符,换成下划线。

    点号尤其要紧:Godot 用 `.` 和 `:` 处理节点路径,
    名字里带点会让 `parent="A.B/C"` 这种路径解析出问题。
    """
    out = []
    for char in str(name):
        out.append("_" if char in '.:@/%"\\' else char)
    return "".join(out) or "Node"


# 弧度转角度(reduce 掉依赖 math 的一行常数)
_DEG = 57.29577951308232


def _light_basis_text(obj):
    """灯光的朝向。DirectionalLight3D 靠 -Z 指方向,和相机一样。

    只写位置不写朝向的话,太阳光一律从正上方照下来 ——
    在 Blender 里明明是斜着打的侧光,到 Godot 里就成了顶光,
    明暗完全对不上。所以朝向必须一起导出。
    """
    return _camera_basis_text(obj)


def _camera_basis_text(obj):
    """把 Blender 相机的朝向转成 Godot 的 Transform3D 基(9 个数)。

    ── 这里连着踩了两个坑,都记下来 ────────────────────────────────

    **坑一:.tscn 文本里 Transform3D 的 9 个数是"列优先"。**
    权威依据(用 Godot 自己验的,不是猜的):
        目标 basis.x=(1,2,3) y=(4,5,6) z=(7,8,9)
        官方 var_to_str 输出 = Transform3D(1, 4, 7, 2, 5, 8, 3, 6, 9, ...)
        再解析回来完全还原。
    规律:**三个基向量的第一个分量连续出现**(x.x, y.x, z.x),
    然后第二个分量、第三个分量。按行优先写等于把矩阵转置了 ——
    实测症状:相机朝上后方看,世界原点投到屏幕 y=1084(画面高 648),
    is_position_in_frustum=false,画面一片纯色。

    **坑二:相机看的是 -Z,不是 +Z。**
    直接抄旋转矩阵的列当基,会让 -Z 指向上后方。
    所以要用 right / up / back 三个方向来搭,其中 back = -(前方向)。

    坐标轴转换沿用 glTF 的 Y-up 约定:Godot(x, y, z) = Blender(x, z, -y)。
    """
    identity = ("1.000000, 0.000000, 0.000000, "
                "0.000000, 1.000000, 0.000000, "
                "0.000000, 0.000000, 1.000000")
    try:
        from mathutils import Vector

        basis = obj.matrix_world.to_3x3()
        # Blender 相机:局部 +X = 右,+Y = 上,局部 -Z = 前
        right = basis @ Vector((1.0, 0.0, 0.0))
        up = basis @ Vector((0.0, 1.0, 0.0))
        back = -(basis @ Vector((0.0, 0.0, -1.0)))   # 前方向取反

        def conv(v):
            """Blender 向量 -> Godot 向量(Z-up -> Y-up)。"""
            return (v.x, v.z, -v.y)

        rx, ry, rz = conv(right)
        ux, uy, uz = conv(up)
        bx, by, bz = conv(back)
        # **列优先**:x 轴三个分量、y 轴三个分量、z 轴三个分量
        # 即 x.x, y.x, z.x, x.y, y.y, z.y, x.z, y.z, z.z
        return (f"{_fmt(rx)}, {_fmt(ux)}, {_fmt(bx)}, "
                f"{_fmt(ry)}, {_fmt(uy)}, {_fmt(by)}, "
                f"{_fmt(rz)}, {_fmt(uz)}, {_fmt(bz)}")
    except Exception:
        return identity


def _godot_position(obj, is_3d=True):
    """把 Blender 物体的世界坐标转成 Godot 坐标。

    glTF 导出用 `export_yup=True`,做的是 **Z-up -> Y-up**:
        Blender(x, y, z)  ->  Godot(x, z, -y)

    这个转换必须在这里也做一遍 —— 否则碰撞体会全部堆在原点,
    看起来"有碰撞体"但完全对不上模型(实测踩过)。
    """
    if obj is None:
        return (0.0, 0.0, 0.0)
    try:
        from mathutils import Vector

        world = obj.matrix_world.translation
        x, y, z = float(world.x), float(world.y), float(world.z)
    except Exception:
        return (0.0, 0.0, 0.0)
    if not is_3d:
        # 2D:只取 x,y(Blender 的 Z 当成 2D 的 y 更符合直觉,
        # 因为在 2D 里用户通常把物体摆在 XY 平面上)
        return (x, y, 0.0)
    return (x, z, -y)


def colliders_that_mismatch_mesh(scene, min_ratio=0.8):
    """找出"碰撞体明显比网格小"的物体名。

    返回 [(物体名, 网格尺寸, 碰撞体尺寸)]。

    ── 为什么需要查这个(用户实测反馈)──────────────────────────────
    『碰撞尺寸』(`shape_size`)是手动尺寸,**一旦填了它就覆盖一切** ——
    不管你把形状选成胶囊还是球,用的都是这组数字。填小了不会有任何报错:
    导出的场景看着完全正常,但运行起来"角色走到模型跟前就**陷进去**"。
    用户把这个现象描述成"渲染错误",其实是碰撞体和可见模型对不上。

    实测踩过:用户给一个 2×2×2 的方块填了 `shape_size=(0.5, 1, 0.5)`,
    导出的球半径只有 0.5 —— 角色能**陷进去一大半**。

    ── 为什么只报"偏小" ────────────────────────────────────────────
    碰撞体比模型大,最坏是"还没碰到就撞上了";比模型小才会**视觉穿模**,
    那才是用户会当成 bug 报的现象。另外用户可能故意把地面做得很大,
    只报偏小就不会拿这种正常配置去刷屏。
    """
    out = []
    if scene is None:
        return out
    for obj, physics, enabled in collect_physics(scene):
        if not enabled or getattr(obj, "type", "") != "MESH":
            continue
        if obj.get(PREVIEW_MARK):
            continue
        built = build_collision(obj, True, physics)
        if built is None:
            continue
        bounds = object_bounds([obj])
        if bounds is None:
            continue
        mesh_size = sorted((abs(v) for v in bounds["size"]), reverse=True)

        # 碰撞体的外接尺寸:box 直接用 size;capsule / sphere 由半径和高算
        sub = built["sub_resource"]
        numbers = {}
        for key in ("radius", "size", "height"):
            m = re.search(rf"^{key} = (.+)$", sub, re.MULTILINE)
            if m:
                numbers[key] = m.group(1)
        if "size" in numbers:
            dims = [float(v) for v in re.findall(r"-?\d+\.?\d*", numbers["size"])]
            collider_size = sorted((abs(v) for v in dims[:3]), reverse=True)
        else:
            found = re.findall(r"-?\d+\.?\d*", numbers.get("radius", "0"))
            r = float(found[0]) if found else 0.0
            found_h = re.findall(r"-?\d+\.?\d*", numbers.get("height", ""))
            h = float(found_h[0]) if found_h else r * 2.0
            # 胶囊总高 = 圆柱段 + 两端半球;球就是 2r
            collider_size = sorted((r * 2.0, max(h, r * 2.0), r * 2.0), reverse=True)

        if len(collider_size) < 3 or not any(mesh_size):
            continue
        # 只看**最大的两条边**:地面这种"又大又薄"的物体本来就该配一个
        # 小盒子(用户常这么干,而且贴合得很好),拿最短边去比会误报。
        ratios = [c / m for m, c in zip(mesh_size[:2], collider_size[:2])
                  if m > 1e-6]
        if len(ratios) >= 2 and min(ratios) < min_ratio:
            out.append((obj.name,
                        tuple(round(v, 3) for v in mesh_size),
                        tuple(round(v, 3) for v in collider_size)))
    return out


def guess_collision_shape(obj, is_3d=True):
    """按物体尺寸猜一个合适的碰撞形状。

    规则(面板上显示的和导出时用的是**同一份逻辑**):

      把三边从大到小排成 a >= b >= c,看**较小的两边 b/c** 的比值:

        b/c 很小(< 1.05)    -> 球      截面接近正方,两个小边都差不多
        c 很小(b/c 很大)    -> 盒子    **扁平**:有一个方向明显更薄
        b/c 居中(1.25~1.8) -> 胶囊    一个方向长,另两个方向中等且接近

    ── 为什么不是"最长/最短"比例 ──────────────────────────────────
    第一版用的是 longest/shortest,结果两个都错:
      * 圆柱(半径 = 高)的包围盒是**立方体**,比例 1.0,被判成球 ——
        球和圆柱在包围盒上确实分不出来,但那不该判成球
      * **压扁**的立方体比例高达 15,被当成"细长"判成胶囊 ——
        可它是扁的,明显该是盒子

    关键区别是**扁平**(一个方向薄)和**细长**(一个方向长)。
    只看最长/最短会把这两者混为一谈,所以现在看两个较小边的比例。
    """
    if obj is None:
        return "box"
    bounds = object_bounds([obj])
    if bounds is None:
        return "box"
    size = bounds["size"]
    if is_3d:
        dims = sorted((abs(size[0]), abs(size[1]), abs(size[2])), reverse=True)
    else:
        dims = sorted((abs(size[0]), abs(size[1])), reverse=True)
        dims.append(min(dims) * 0.5)      # 2D 里没有第三个轴,补一个"薄"的

    a, b, c = dims
    if c <= 1e-6:
        return "box"

    mid_ratio = b / c          # 两个较小边的比例
    long_ratio = a / b         # 最长边比中间边

    # 扁平:一个方向明显更薄 -> 盒子(墙、地板、平台)
    if mid_ratio > 1.8:
        return "box"
    # 细长:一个方向明显更长,另两个方向接近 -> 胶囊(人形、柱子)
    if long_ratio > 1.35 and mid_ratio <= 1.35:
        return "capsule"
    # 三边接近:包围盒分不出球和方盒。
    # 默认给球 —— 对于"看起来圆"的东西(道具、球体角色)球更贴合,
    # 而方块类物体用户通常会自己去面板上改成盒子。
    return "sphere"

def collision_preview_mesh(obj, is_3d=True, physics=None):
    """算出碰撞体的可视化线框数据(顶点 + 边)。

    返回 dict 或 None:
        {"verts": [(x,y,z), ...], "edges": [(i,j), ...]}

    为什么要这个:碰撞形状在 Blender 里**看不见** —— 用户只能靠"按尺寸会选:胶囊"
    这行字去猜它到底多大、贴不贴合。UPBGE 是直接把碰撞体画在视口里的,
    这里照做:导出前先给一个线框物体,所见即所得。

    坐标是**物体局部**的,调用方负责摆到正确位置。
    """
    if obj is None:
        return None
    bounds = object_bounds([obj])
    if bounds is None:
        return None

    shape = getattr(physics, "collision_shape", "auto") if physics else "auto"
    if shape == "none":
        return None
    if shape == "auto":
        shape = guess_collision_shape(obj, is_3d)

    override = None
    if physics is not None:
        raw = getattr(physics, "shape_size", None)
        if raw is not None and len(raw) == 3:
            override = (float(raw[0]), float(raw[1]), float(raw[2]))

    size = bounds["size"]
    if override is not None and shape != "auto":
        radius = max(override[0], 0.001)
        half_z = max(override[2], 0.001)
        height = max(override[1], 0.002)
    else:
        radius = max(size[0], size[2], 0.001) * 0.5
        half_z = max(size[2], 0.001) * 0.5
        height = max(size[1], 0.002)

    if shape == "box":
        if override is not None:
            hx, hy, hz = (max(override[0], 0.001) * 0.5,
                          max(override[1], 0.002) * 0.5,
                          max(override[2], 0.001) * 0.5)
        else:
            hx = max(size[0], 0.001) * 0.5
            hy = max(size[1], 0.002) * 0.5
            hz = max(size[2], 0.001) * 0.5
        verts = [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz),
                 (-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]
        edges = [(0, 1), (1, 2), (2, 3), (3, 0),
                 (4, 5), (5, 6), (6, 7), (7, 4),
                 (0, 4), (1, 5), (2, 6), (3, 7)]
        return {"verts": verts, "edges": edges}

    if shape == "sphere":
        return _sphere_wire(radius)

    if shape == "capsule":
        # 胶囊 = 上下半球 + 两条直边。用线框近似,段数够看清就行。
        return _capsule_wire(radius, max(height, radius * 2.0))

    if shape == "cylinder":
        return _cylinder_wire(radius, max(height, 0.002))

    return None


def _sphere_wire(radius, segments=16, rings=4):
    """球的线框:经线 + 纬线。

    用标准的球坐标:
        x = r * sin(phi) * cos(theta)
        y = r * sin(phi) * sin(theta)
        z = r * cos(phi)
    phi 是极角(0..pi),theta 是方位角(0..2pi)。
    """
    import math

    verts = []
    edges = []

    # 经线:固定 theta,扫 phi
    for i in range(4):
        theta = math.pi * i / 4.0
        base = len(verts)
        for j in range(segments + 1):
            phi = math.pi * j / segments
            verts.append((radius * math.sin(phi) * math.cos(theta),
                          radius * math.sin(phi) * math.sin(theta),
                          radius * math.cos(phi)))
        for j in range(segments):
            edges.append((base + j, base + j + 1))

    # 纬线:固定 phi,扫 theta
    for k in range(1, rings + 1):
        phi = math.pi * k / (rings + 1)
        r = radius * math.sin(phi)
        z = radius * math.cos(phi)
        base = len(verts)
        for j in range(segments):
            theta = 2.0 * math.pi * j / segments
            verts.append((r * math.cos(theta), r * math.sin(theta), z))
        for j in range(segments):
            edges.append((base + j, base + (j + 1) % segments))

    return {"verts": verts, "edges": edges}


def _capsule_wire(radius, height, segments=16):
    """胶囊线框:两圈 + 四条竖边 + 半球弧。"""
    import math

    half = max(height * 0.5 - radius, 0.0)
    verts = []
    edges = []
    for z in (half, -half):
        base = len(verts)
        for j in range(segments):
            theta = 2.0 * math.pi * j / segments
            verts.append((radius * math.cos(theta), radius * math.sin(theta), z))
        for j in range(segments):
            edges.append((base + j, base + (j + 1) % segments))
    top = len(verts) - 2 * segments
    bottom = len(verts) - segments
    for j in range(0, segments, max(1, segments // 4)):
        edges.append((top + j, bottom + j))
    # 上下极点
    apex_top = len(verts)
    verts.append((0.0, 0.0, half + radius))
    apex_bottom = len(verts)
    verts.append((0.0, 0.0, -half - radius))
    for j in range(0, segments, max(1, segments // 4)):
        edges.append((top + j, apex_top))
        edges.append((bottom + j, apex_bottom))
    return {"verts": verts, "edges": edges}


def _cylinder_wire(radius, height, segments=16):
    import math

    half = height * 0.5
    verts = []
    edges = []
    for z in (half, -half):
        base = len(verts)
        for j in range(segments):
            theta = 2.0 * math.pi * j / segments
            verts.append((radius * math.cos(theta), radius * math.sin(theta), z))
        for j in range(segments):
            edges.append((base + j, base + (j + 1) % segments))
    top = len(verts) - 2 * segments
    bottom = len(verts) - segments
    for j in range(0, segments, max(1, segments // 4)):
        edges.append((top + j, bottom + j))
    return {"verts": verts, "edges": edges}


def _fmt(value):
    """Godot 的浮点写法。留 6 位小数,避免科学计数法。"""
    return f"{float(value):.6f}"


# 3D 碰撞形状的 sub_resource 模板
SHAPE_TEMPLATES_3D = {
    "box": ('[sub_resource type="BoxShape3D" id="shape"]\n'
            "size = Vector3({x}, {y}, {z})"),
    "sphere": ('[sub_resource type="SphereShape3D" id="shape"]\n'
               "radius = {r}"),
    "capsule": ('[sub_resource type="CapsuleShape3D" id="shape"]\n'
                "radius = {r}\nheight = {h}"),
    "cylinder": ('[sub_resource type="CylinderShape3D" id="shape"]\n'
                 "radius = {r}\nheight = {h}"),
}

SHAPE_TEMPLATES_2D = {
    "box": ('[sub_resource type="RectangleShape2D" id="shape"]\n'
            "size = Vector2({x}, {y})"),
    "sphere": ('[sub_resource type="CircleShape2D" id="shape"]\n'
               "radius = {r}"),
    "capsule": ('[sub_resource type="CapsuleShape2D" id="shape"]\n'
                "radius = {r}\nheight = {h}"),
    "cylinder": ('[sub_resource type="CircleShape2D" id="shape"]\n'
                 "radius = {r}"),
}


def build_collision(obj, is_3d=True, physics=None):
    """给一个物体算出碰撞体配置。

    返回 dict 或 None(不需要碰撞时):
        {"shape": "capsule", "sub_resource": "...", "node": "...", "offset": (x,y,z)}
    `node` 是挂在物体下面的 CollisionShape 节点文本。
    """
    if obj is None:
        return None

    shape = "auto"
    layer = "1"
    mask = "1"
    is_trigger = False
    override_size = None

    if physics is not None:
        shape = getattr(physics, "collision_shape", "auto")
        layer = getattr(physics, "collision_layer", "1")
        mask = getattr(physics, "collision_mask", "1")
        is_trigger = bool(getattr(physics, "is_trigger", False))
        size = getattr(physics, "shape_size", None)
        if size is not None and len(size) == 3:
            override_size = (float(size[0]), float(size[1]), float(size[2]))

    if shape == "none":
        return None
    if shape == "auto":
        shape = guess_collision_shape(obj, is_3d)

    bounds = object_bounds([obj])
    if bounds is None:
        return None
    size = bounds["size"]

    # 形状参数
    if is_3d:
        if override_size is not None:
            radius = max(override_size[0], 0.001)
            height = max(override_size[1], 0.002)
            half = (override_size[0], override_size[1] * 0.5, override_size[2])
        else:
            radius = max(size[0], size[2], 0.001) * 0.5
            height = max(size[1], 0.002)
            half = (size[0] * 0.5, size[1] * 0.5, size[2] * 0.5)
        params = {
            "x": _fmt(half[0] * 2.0), "y": _fmt(half[1] * 2.0),
            "z": _fmt(half[2] * 2.0),
            "r": _fmt(radius), "h": _fmt(max(height, radius * 2.0)),
        }
        template = SHAPE_TEMPLATES_3D.get(shape, SHAPE_TEMPLATES_3D["box"])
        node_type = "CollisionShape3D"
        offset = (0.0, 0.0, 0.0)
    else:
        if override_size is not None:
            radius = max(override_size[0], 0.001)
            height = max(override_size[1], 0.002)
        else:
            radius = max(size[0], 0.001) * 0.5
            height = max(size[1], 0.002)
        params = {
            "x": _fmt(max(size[0], 0.002)), "y": _fmt(max(size[1], 0.002)),
            "z": "0.0",
            "r": _fmt(radius), "h": _fmt(max(height, radius * 2.0)),
        }
        template = SHAPE_TEMPLATES_2D.get(shape, SHAPE_TEMPLATES_2D["box"])
        node_type = "CollisionShape2D"
        offset = (0.0, 0.0, 0.0)

    body_type = "StaticBody3D"
    if physics is not None:
        body_type = getattr(physics, "body_type", "StaticBody3D")
    if is_trigger or "Area" in body_type:
        node_type = "CollisionShape3D" if is_3d else "CollisionShape2D"

    node_lines = [f'[node name="Collision" type="{node_type}" parent="."]',
                  'shape = SubResource("shape")']
    if offset != (0.0, 0.0, 0.0):
        node_lines.append(f"position = Vector3({_fmt(offset[0])}, "
                          f"{_fmt(offset[1])}, {_fmt(offset[2])})")
    if layer != "1" or mask != "1":
        node_lines.append(f"collision_layer = {int(layer)}")
        node_lines.append(f"collision_mask = {int(mask)}")

    return {
        "shape": shape,
        "sub_resource": template.format(**params),
        "node": "\n".join(node_lines),
        "layer": layer,
        "mask": mask,
        "is_trigger": is_trigger,
    }


def collect_physics(scene):
    """收集场景里所有物体的物理设置。

    返回 [(物体, 设置对象, 是否启用)] —— 顺序跟场景一致,生成结果稳定。
    不导入 bpy:scene 由调用方传进来,这样离线测试也能跑。
    """
    if scene is None:
        return []
    out = []
    for obj in scene.objects:
        physics = getattr(obj, "gk_physics", None)
        # 以前这里有个"是否导出"的开关,后来按用户要求删掉了:
        # 所有网格都导出,碰撞盒要不要在属性里直接配,不用先开一个总开关。
        out.append((obj, physics, True))
    return out


def scene_materials(scene, enabled_only=True, file_names=None):
    """收集"对象名 -> 材质文件"的映射,给 write_world_scene 做材质覆盖用。

    返回 {对象名: (材质资源名, materials/ 下的文件名)}。

    为什么需要:glTF **不携带 NPR 外观**。卡通着色靠色阶切分、边缘光、
    面部 SDF,glTF 的 PBR 材质表达不了,所以 .glb 里全是素白的
    StandardMaterial3D。我们给每个 Blender 材质生成的 .tres 一直没被引用。

    有了这个映射,包装场景就能给每个对象加一条 material_override,
    直接指向 .tres —— glb 负责几何,tres 负责外观。
    """
    out = {}
    if scene is None:
        return out
    for obj in scene.objects:
        if getattr(obj, "type", "") != "MESH":
            continue
        if obj.get(PREVIEW_MARK):
            continue
        material = first_real_material(obj)
        if material is None:
            continue
        if file_names and material.name in file_names:
            file_name = file_names[material.name] + ".tres"
        else:
            file_name = safe_name(material.name, "material") + ".tres"
        out[obj.name] = (material.name, file_name)
    return out


def first_real_material(obj):
    """取物体的第一个**正经**材质,跳过工具生成的预览材质。

    为什么要跳:『显示描边预览』会往 obj.data.materials 里 append 一个
    叫 __GK描边预览材质__ 的纯色材质。如果不管它,导出时它会被当成
    物体的材质导出去 —— 用户会看到自己的模型在 Godot 里变成一个纯黑壳。
    """
    for material in (getattr(obj.data, "materials", None) or ()):
        if material is None:
            continue
        if material.name.startswith(TOOL_MATERIAL_PREFIX):
            continue
        return material
    return None


def _base_color_texture(material):
    """从 Blender 材质里找 Base Color 贴图，返回图片名(导出后 Godot 里的文件名)。"""
    if material is None or not material.use_nodes:
        return None
    for node in material.node_tree.nodes:
        if node.type != "BSDF_PRINCIPLED":
            continue
        socket = node.inputs.get("Base Color")
        if socket is None or not socket.is_linked:
            continue
        source = socket.links[0].from_node
        if source.type == "TEX_IMAGE" and source.image is not None:
            return source.image.name
    return None


def _base_color_rgb(material):
    """取材质的基础色，用作卡通材质的主色。"""
    fallback = (0.85, 0.82, 0.88)
    if material is None or not material.use_nodes:
        return fallback
    for node in material.node_tree.nodes:
        if node.type != "BSDF_PRINCIPLED":
            continue
        socket = node.inputs.get("Base Color")
        if socket is None:
            continue
        if socket.is_linked:
            continue
        value = socket.default_value
        return (float(value[0]), float(value[1]), float(value[2]))
    return fallback


def write_npr_material(export_dir, material, shader_res_dir="shaders"):
    """把 Blender 里的 NPR 预览材质导出成 Godot 的 .tres。

    这是"在 Blender 里调参"能成立的关键一步:参数存在材质自定义属性上,
    这里读出来直接写成 Godot 的 shader_parameter,两边不会各调一遍。

    返回 (文件路径, 参数个数);材质不是 NPR 材质时返回 (None, 0)。
    """
    # npr_preview 在模块级导入。
    # 注意:不能在函数内部写 from . import npr_preview —— 在 Blender 扩展
    # 环境里,函数局部的相对导入会因为模块路径解析方式不同而报
    # "'bl_ext' is not a package"。模块级导入没有这个问题。
    params = npr_preview.read_params(material)
    if not params:
        return None, 0

    name = safe_name(material.name, "material")
    texture = _base_color_texture(material)

    textures_block = ""
    albedo_ref = "null"
    steps = 2
    if texture:
        tex_name = safe_name(texture, "texture") + ".png"
        textures_block = (
            f'[ext_resource type="Texture2D" path="res://{project.ART_DIR}/{tex_name}" '
            f'id="2_albedo"]\n'
        )
        albedo_ref = 'ExtResource("2_albedo")'
        steps = 3

    lines = [
        f'[gd_resource type="ShaderMaterial" load_steps={steps} format=3]',
        "",
        f'[ext_resource type="Shader" path="res://{shader_res_dir}/gk_toon.gdshader" '
        f'id="1_shader"]',
    ]
    if textures_block:
        lines.append(textures_block.rstrip("\n"))
    lines.append("")
    lines.append("[resource]")
    lines.append('shader = ExtResource("1_shader")')
    lines.append(f"shader_parameter/albedo_texture = {albedo_ref}")
    # 必须显式写开关:hint_default_white 让未指定贴图时 texture() 恒返回白色,
    # 运行时区分不出"没贴图"和"贴图是白的"。
    lines.append(f"shader_parameter/use_albedo_texture = "
                 f"{1.0 if texture else 0.0:.1f}")

    for uniform, value in params.items():
        if isinstance(value, (tuple, list)):
            r, g, b = float(value[0]), float(value[1]), float(value[2])
            lines.append(
                f"shader_parameter/{uniform} = Color({r:.6f}, {g:.6f}, {b:.6f}, 1)"
            )
        else:
            lines.append(f"shader_parameter/{uniform} = {float(value):.6f}")

    path = os.path.join(export_dir, "materials", f"{name}.tres")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    return path, len(params)


def write_materials(export_dir, objects, shader_res_dir="shaders"):
    """给每个 Blender 材质生成一份 Godot 卡通材质。返回 [(材质名, 文件路径, 有无贴图)]。

    objects 传 None 表示"整个场景里所有网格的材质" —— 整场景导出时用这个,
    因为场景里每个对象都需要自己的 .tres 才能显示成卡通。
    """
    if objects is None:
        import bpy

        scene = getattr(bpy.context, "scene", None)
        objects = [obj for obj in (scene.objects if scene else [])
                   if getattr(obj, "type", "") == "MESH"
                   and not obj.get(PREVIEW_MARK)]

    # ── 文件名先去重,再逐个写 ──────────────────────────────────────
    # 不过滤重名的话,M MD 那种几十个材质槽的模型会全部落到同一个
    # `material.tres` 上互相覆盖(实测日志里连续 41 次"写入 material.tres")。
    wanted = []
    for obj in objects:
        for slot in getattr(obj, "material_slots", ()):
            material = slot.material
            if material is None or material.name in wanted:
                continue
            # 跳过工具生成的辅助材质(描边预览的纯色壳)。
            # 不跳的话它会变成一条 .tres,用户可能在 Godot 里选中它,
            # 把模型改成纯黑。
            if material.name.startswith(TOOL_MATERIAL_PREFIX):
                continue
            wanted.append(material.name)
    file_names = _unique_file_names(wanted)

    written = []
    for obj in objects:
        for slot in getattr(obj, "material_slots", ()):
            material = slot.material
            if material is None or material.name not in file_names:
                continue
            if material.name in {item[0] for item in written}:
                continue
            name = file_names[material.name]
            rgb = _base_color_rgb(material)
            texture = _base_color_texture(material)

            textures_block = ""
            albedo_ref = "null"
            steps = 2
            if texture:
                tex_name = safe_name(texture, "texture") + ".png"
                textures_block = (
                    f'[ext_resource type="Texture2D" '
                    f'path="res://{project.ART_DIR}/{tex_name}" id="2_albedo"]\n'
                )
                albedo_ref = 'ExtResource("2_albedo")'
                steps = 3

            text = TOON_MATERIAL_TEMPLATE.format(
                steps=steps,
                shader_path=f"res://{shader_res_dir}/gk_toon.gdshader",
                textures=textures_block,
                r=rgb[0], g=rgb[1], b=rgb[2],
                albedo_ref=albedo_ref,
                use_texture=1.0 if texture else 0.0,
            )
            path = os.path.join(export_dir, project.MATERIALS_DIR, f"{name}.tres")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            written.append((material.name, path, bool(texture)))
    return written


def npr_get(material, name, default=None):
    """读材质参数(中文键/英文键都认)。

    单独包一层是为了 asset_export 不用在模块顶层 import npr_preview ——
    那会绕成循环导入(npr_preview 也 import 别的东西)。
    """
    from . import npr_preview

    return npr_preview.get_param(material, name, default)


def export_textures(export_dir, materials):
    """把材质用到的贴图导出成 PNG 到 art/。

    返回 [(图片名, 路径)]。

    ── 为什么必须有这一步 ──────────────────────────────────────────
    生成的 .tres 里有这么一行:
        [ext_resource type="Texture2D" path="res://art/xxx.png" ...]
    如果那个 PNG 不存在,Godot 加载 .tres 会失败,进而**整个场景实例化失败**
    ——表现是打开游戏一片空白/一片灰,而日志里只有一行
    "Failed loading resource"。这个坑实测踩过,而且是"卡通材质显示不出来"
    的直接原因:普通图片材质直接挂在模型上、不经过 .tres,所以看不出问题。

    glTF 会把贴图**内嵌**进 .glb,但那只对 glb 自己的材质有效;
    我们的 .tres 是独立文件,必须自己把 PNG 落盘。
    """
    import bpy

    out = []
    seen = set()
    target_dir = os.path.join(export_dir, project.ART_DIR)
    os.makedirs(target_dir, exist_ok=True)

    for material in materials or ():
        if material is None:
            continue
        # ── 基础色贴图必须按 .tres 期望的**那个名字**导出 ─────────────
        # 踩过的坑:`.tres` 里写的是 `res://art/{safe_name(图片名)}.png`,
        # 比如图片叫 `hair.png` -> 引用 `hair_png.png`;
        # 而这里原来只按 `_material_image_names()` 导出,名字对不上 ——
        # Godot 报 "Resource file not found: res://art/hair_png.png",
        # 材质加载失败,模型回到白模。
        #
        # 所以基础色这张图先按 .tres 的名字单独导一份,再导其余的。
        base_name = _base_color_texture(material)
        if base_name and base_name not in seen:
            seen.add(base_name)
            base_image = bpy.data.images.get(base_name)
            if base_image is not None and base_image.size[0] > 0:
                # 名字算法**必须和 write_materials 里写进 .tres 的完全一致**:
                # 那边是 `safe_name(texture, "texture") + ".png"`,
                # 这里少一步就会变成两套名字,Godot 照样找不到文件。
                file_name = safe_name(base_name, "texture") + ".png"
                path = os.path.join(target_dir, file_name)
                try:
                    _save_image(base_image, path)
                    out.append((base_name, path))
                except Exception as exc:
                    gk_log.warn(f"贴图 {base_name} 导出失败: {exc}")

        for name in _material_image_names(material):
            if name in seen:
                continue
            seen.add(name)
            image = bpy.data.images.get(name)
            if image is None or image.size[0] == 0:
                continue
            file_name = safe_name(name, "texture") + ".png"
            path = os.path.join(target_dir, file_name)
            try:
                _save_image(image, path)
            except Exception as exc:
                gk_log.warn(f"贴图 {name} 导出失败: {exc}")
                continue
            out.append((name, path))
    return out


def _save_image(image, path):
    """把 Blender 图像存成 PNG。

    优先用 save_render —— 它对**程序生成**的图像也有效
    (bpy.data.images.new() 造的图没有磁盘路径,save() 会失败)。
    失败则退回 save()。
    """
    import bpy

    try:
        image.save_render(path)
        return
    except Exception:
        pass
    scene = getattr(bpy.context, "scene", None)
    settings = scene.render.image_settings if scene is not None else None
    old_format = getattr(settings, "file_format", None)
    if settings is not None:
        settings.file_format = "PNG"
    try:
        image.filepath_raw = path
        image.file_format = "PNG"
        image.save()
    finally:
        if settings is not None and old_format is not None:
            settings.file_format = old_format


def _material_image_names(material):
    """列出材质里用到的所有图像名(包括非 Base Color 的,比如 SDF)。"""
    names = []
    if material is None or not getattr(material, "use_nodes", False):
        return names
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return names
    for node in tree.nodes:
        image = getattr(node, "image", None)
        if image is not None and getattr(image, "name", ""):
            names.append(image.name)
    return names


def glb_scene_nodes(export_dir, model_name="World"):
    """解析 .glb,返回可以直接建成 Godot 节点的信息。

    返回 {"meshes": {物体名: 网格资源名}, "cameras": [...], "lights": [...],
          "nodes": [...]}。

    ── 为什么要解析 glb 而不直接 instance 它 ────────────────────────
    包装场景原本是 `[node name="World" instance=ExtResource("1_world")]`,
    然后另外声明节点给实例内部的子节点加 material_override。
    **这条路在 Godot 里走不通**:

        WARNING: Parent path './World' for node 'Ball' has vanished
                 when instantiating: 'res://scenes/World.tscn'.

    glb 实例的内部子节点不能被外部节点覆盖 —— 材质覆盖**从来没生效过**,
    用户在 Godot 里看到的永远是白模。加 editable_children = true 也没用
    (实测过)。

    所以改成:把 glb 里的每个对象展开成一个 MeshInstance3D 节点,
    网格用 ExtResource 指向 glb 里的 mesh 资源(形如 "1_world/BallMesh"),
    材质直接写在 mesh 上。这样**没有任何覆盖**,Godot 不会抱怨,
    而且 .tscn 里就能看到完整的对象树。
    """
    result = {"meshes": {}, "cameras": [], "lights": [], "nodes": [],
              "materials": {}, "root_matrix": None}
    path = os.path.join(export_dir or "", project.ART_DIR, f"{model_name}.glb")
    if not os.path.isfile(path):
        return result
    try:
        import json
        import struct

        with open(path, "rb") as handle:
            data = handle.read()
        offset = 12
        payload = None
        while offset < len(data):
            length, kind = struct.unpack_from("<II", data, offset)
            offset += 8
            if kind == 0x4E4F534A:      # "JSON"
                payload = json.loads(data[offset:offset + length].decode("utf-8"))
                break
            offset += length
        if payload is None:
            return result
    except (OSError, ValueError, struct.error):
        return result

    meshes = payload.get("meshes", [])
    materials = payload.get("materials", [])

    for index, mesh in enumerate(meshes):
        # glTF 的网格名 = Blender 的 mesh data 名,不一定等于对象名,
        # 所以按**节点**去找更靠得住 —— 下面遍历节点时再填。
        result["materials"][index] = mesh.get("name", f"Mesh{index}")

    for node in payload.get("nodes", []):
        name = node.get("name", "")
        if "mesh" in node:
            mesh_index = node["mesh"]
            mesh_name = (meshes[mesh_index].get("name", "") if
                         mesh_index < len(meshes) else "")
            primitives = (meshes[mesh_index].get("primitives", [])
                          if mesh_index < len(meshes) else [])
            material_index = None
            if primitives:
                material_index = primitives[0].get("material")
            material_name = ""
            if material_index is not None and material_index < len(materials):
                material_name = materials[material_index].get("name", "")
            result["meshes"][name] = {
                "mesh_resource": mesh_name or name,
                "primitives": len(primitives),
                "material": material_name,
            }
        if "camera" in node:
            result["cameras"].append(name)
        if node.get("extensions", {}).get("KHR_lights_punctual"):
            result["lights"].append(name)
        result["nodes"].append(name)

    # glb 的根节点变换(第一个 scene 的第一个 node)。
    # 角色 glb 里它带着"角色在 Blender 里的世界变换",instance 到原点的
    # 角色节点下面会**再偏移一次**,所以调用方要拿它算逆变换抵消掉。
    scenes = payload.get("scenes", [])
    nodes_all = payload.get("nodes", [])
    if scenes:
        roots = scenes[0].get("nodes") or []
        if roots and roots[0] < len(nodes_all):
            result["root_matrix"] = _glb_node_matrix(payload, nodes_all[roots[0]])
    return result


def _glb_node_matrix(payload, node):
    """把 glTF 节点的 TRS 转成 Godot 的 Transform3D 文本。

    glTF 用 TRS(translation / rotation 四元数 / scale)**列优先**矩阵,
    Godot 的 Transform3D 也是列优先 —— 所以顺序一致,可以直接展开。
    但**有旋转/缩放时必须算矩阵**,不能只写平移:
    相机和太阳的朝向全靠这个,只写平移它们就会一起朝上。
    """
    translation = node.get("translation", [0.0, 0.0, 0.0])
    rotation = node.get("rotation", [0.0, 0.0, 0.0, 1.0])     # x, y, z, w
    scale = node.get("scale", [1.0, 1.0, 1.0])

    x, y, z, w = (float(rotation[0]), float(rotation[1]),
                  float(rotation[2]), float(rotation[3]))
    sx, sy, sz = (float(scale[0]), float(scale[1]), float(scale[2]))
    # 四元数 -> 3x3 旋转矩阵(标准公式)
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    m = [
        [1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy)],
        [2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx)],
        [2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)],
    ]
    # 乘上缩放(列缩放)
    for row in range(3):
        m[row][0] *= sx
        m[row][1] *= sy
        m[row][2] *= sz

    # Godot 的 Transform3D 文本里,9 个数依次是 **X 轴、Y 轴、Z 轴**三个向量,
    # 然后才是原点。
    #
    # ── 这个顺序我搞错过,把判别方法记下来 ──────────────────────────
    # 错误经历:拿一个"看起来足够随机"的矩阵 (1,2,3,4,5,6,7,8,9) 去试,
    # 结论是"列优先",于是照此生成 —— 结果相机方向整体反了。
    #
    # **那个测试矩阵没有区分力**:在这个矩阵上,按行读和按列读得到的
    # "前方向"符号恰好一样,所以它证明不了任何事。
    #
    # 可靠的判别方法:用一个真实的不对称矩阵(相机旋转)去比 ——
    # 两种读法给出的前方向符号相反,拿 Godot 实测值一对就知道是哪种。
    values = [m[0][0], m[1][0], m[2][0],
              m[0][1], m[1][1], m[2][1],
              m[0][2], m[1][2], m[2][2],
              float(translation[0]), float(translation[1]), float(translation[2])]
    return ", ".join(_fmt(v) for v in values)


def build_model_nodes(export_dir, model_name, node_names, materials_map,
                      resource_id="1_world", indent=""):
    """把 glb 里的对象展开成 Godot 的 MeshInstance3D 节点文本。

    返回 (节点文本, 用到的网格资源名列表)。

    为什么要展开而不是 instance glb:见 glb_scene_nodes 的说明 ——
    glb 实例的内部子节点没法从外部覆盖材质,加 editable_children 也没用。
    展开之后材质直接挂在 mesh 上,不需要任何覆盖。
    """
    import json
    import struct

    path = os.path.join(export_dir or "", project.ART_DIR, f"{model_name}.glb")
    if not os.path.isfile(path):
        return "", []
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        offset = 12
        payload = None
        while offset < len(data):
            length, kind = struct.unpack_from("<II", data, offset)
            offset += 8
            if kind == 0x4E4F534A:
                payload = json.loads(data[offset:offset + length].decode("utf-8"))
                break
            offset += length
        if payload is None:
            return "", []
    except (OSError, ValueError, struct.error):
        return "", []

    meshes = payload.get("meshes", [])
    lines = []
    used = []
    wanted = set(node_names or ())
    for node in payload.get("nodes", []):
        name = node.get("name", "")
        if "mesh" not in node or (wanted and name not in wanted):
            continue
        mesh_index = node["mesh"]
        if mesh_index >= len(meshes):
            continue
        mesh_resource = meshes[mesh_index].get("name", "") or name
        used.append(mesh_resource)
        lines.append(f'{indent}[node name="{_sanitize_node_name(name)}" '
                     f'type="MeshInstance3D" parent="."]')
        lines.append(f'{indent}transform = Transform3D('
                     f'{_glb_node_matrix(payload, node)})')
        lines.append(f'{indent}mesh = ExtResource("{resource_id}/{mesh_resource}")')
        material_file = (materials_map or {}).get(name, (None, None))[1]
        if material_file:
            lines.append(f'{indent}material_override = '
                         f'ExtResource("res://{project.MATERIALS_DIR}/'
                         f'{material_file}")')
        lines.append("")
    return "\n".join(lines), used
def meshes_without_material(scene):
    """找出"会被导出但一个材质都没有"的网格物体名。

    ── 为什么要专门查这个 ──────────────────────────────────────────
    材质是**按物体上的材质槽**导出的:`scene_materials()` 只收集
    `first_real_material(obj)` 拿得到东西的物体。一个材质槽都没有的网格
    会被整个跳过 —— 于是 .glb 里它就是一个没有材质的网格,
    Godot 用默认材质渲染,**看起来是一片灰**。

    实测踩过:用户建了个 Cube 当角色,忘了给材质,导出的角色场景里
    那个方块就是灰的,他以为"渲染坏了"。这不是渲染问题,
    是那个物体在 Blender 里就没有材质。
    """
    out = []
    if scene is None:
        return out
    for obj in scene.objects:
        if getattr(obj, "type", "") != "MESH":
            continue
        if obj.get(PREVIEW_MARK):
            continue
        if not obj.data.materials:
            out.append(obj.name)
            continue
        if first_real_material(obj) is None:
            out.append(obj.name)
    return out


# ── 已删除:_blender_world_to_godot_transform() ──────────────────────
#
# 曾经写过一个"把 Blender 物体的世界变换手写成 Godot Transform3D"的函数,
# 按 Godot(x,y,z) = Blender(x, z, -y) 置换基向量。**这条路是错的,别再走。**
#
# 实测症状:带旋转/缩放时矩阵对不上 —— 缩放跑到别的轴上、原点也抵消不干净,
# 角色网格因此和碰撞体错开,画面表现为重影。
#
# 原因是 **Godot 的 glTF 导入是跨坐标系的(Y-up),不是简单的轴置换**,
# 靠自己拼矩阵必然会偏。
#
# 正确做法(现在用的):glTF 已经把手物体的完整世界变换写进 glb 根节点,
# 而且 Godot 导入时自己会处理对坐标系 —— 所以**让 glb 说了算**:
#   * 角色节点保持单位 basis,网格直接交给 glb
#   * 碰撞体按 glb 根节点的原点摆(见 _glb_root_transform_values)
# 用一个数据源决定两者位置,不让两条独立推算的路径去"对齐"。


def _glb_root_transform_values(export_dir, model_name):
    """读 glb 根节点的变换,返回 12 个数的列表(读不到返回 None)。"""
    raw = glb_scene_nodes(export_dir, model_name).get("root_matrix")
    if not raw:
        return None
    try:
        m = [float(part.strip()) for part in str(raw).split(",")]
    except ValueError:
        return None
    return m if len(m) == 12 else None


def _glb_root_inverse_transform(export_dir, model_name, obj, is_3d=True):
    """算出抵消角色 glb 内部根节点变换的 Transform3D 文本(没有偏移时返回 "")。

    ── 为什么需要它(踩过的坑)────────────────────────────────────────
    glTF 导出会把物体的**世界变换**写进 glb 的根节点。于是角色 glb 的根
    节点带着"角色在 Blender 里的位置",把它 instance 到一个位于原点的
    CharacterBody3D 下面,网格就会**再偏移一次** ——
    和碰撞体(按物体世界坐标生成)错开,表现是
    **重影 + 深度冲突闪烁**(用户实测:"模型一闪一闪的")。

    给它一个逆变换就能把这份内部偏移抵消掉:网格正好落在物体世界坐标上,
    和碰撞体严丝合缝。
    """
    info = glb_scene_nodes(export_dir, model_name)
    raw = info.get("root_matrix")
    if not raw:
        return ""
    # _glb_node_matrix 返回的是 "a, b, c, ..." 这样的文本(12 个数)
    try:
        m = [float(part.strip()) for part in str(raw).split(",")]
    except ValueError:
        return ""
    if len(m) != 12:
        return ""
    identity = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
    if all(abs(m[i] - identity[i]) < 1e-9 for i in range(12)):
        return ""
    # 12 个数按 Godot 的列优先顺序:前 9 个是基向量,后 3 个是平移
    a = [[m[0], m[3], m[6]],
         [m[1], m[4], m[7]],
         [m[2], m[5], m[8]]]
    t = [m[9], m[10], m[11]]

    det = (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
           - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
           + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    if abs(det) < 1e-12:
        return ""
    inv = [
        [(a[1][1] * a[2][2] - a[1][2] * a[2][1]) / det,
         (a[0][2] * a[2][1] - a[0][1] * a[2][2]) / det,
         (a[0][1] * a[1][2] - a[0][2] * a[1][1]) / det],
        [(a[1][2] * a[2][0] - a[1][0] * a[2][2]) / det,
         (a[0][0] * a[2][2] - a[0][2] * a[2][0]) / det,
         (a[0][2] * a[1][0] - a[0][0] * a[1][2]) / det],
        [(a[1][0] * a[2][1] - a[1][1] * a[2][0]) / det,
         (a[0][1] * a[2][0] - a[0][0] * a[2][1]) / det,
         (a[0][0] * a[1][1] - a[0][1] * a[1][0]) / det],
    ]
    # 逆平移 = -inv3x3 * t
    it = [-(inv[r][0] * t[0] + inv[r][1] * t[1] + inv[r][2] * t[2])
          for r in range(3)]
    values = [inv[0][0], inv[1][0], inv[2][0],
              inv[0][1], inv[1][1], inv[2][1],
              inv[0][2], inv[1][2], inv[2][2],
              it[0], it[1], it[2]]
    return "Transform3D(" + ", ".join(_fmt(v) for v in values) + ")"


def _base_class_for(scene, obj, dimension="3"):
    """算出某个物体对应的脚本基类。

    脚本的 `extends` 和场景里节点的 `type` 必须是同一个值 —— 否则 Godot
    直接拒绝加载脚本,游戏跑不起来。所以两边都走这一个函数。

    返回的**一定是具体类型**,不会是 "auto":
    `resolve_base_class` 在"物体没设物理、场景又选了自动"时会把 "auto"
    原样返回,那会生成 `type="auto"` 这种非法节点 ——
    实测在 World.tscn 里出现过(Godot 解析这个场景直接失败)。
    """
    from . import properties as props

    physics = getattr(obj, "gk_physics", None) if obj is not None else None
    resolved = props.resolve_base_class(scene, obj, physics, dimension)
    if resolved and resolved != props.BASE_CLASS_AUTO:
        return resolved
    # "auto" 不是合法 Godot 类型,兜底成静态碰撞体(最保守的选择)
    return "StaticBody3D" if str(dimension) != "2" else "StaticBody2D"


def export_assets_pipeline(scene, directory, dimension="3", script_name=None):
    """完整的资产导出流程:整场景 glb -> 材质 -> 包装场景 -> 角色场景。

    返回一个结果字典,不抛异常(出错时把错误放进 "error"),
    这样调用方(比如"运行前自动导出")可以简单地警告一下就继续。

    ── 为什么抽成独立函数 ──────────────────────────────────────────
    这段流程以前只存在于 bpy.ops.gk.export_assets 的 execute 里。
    但"直接运行游戏时自动导出"也需要它 —— 在算子里再调另一个算子会卷进
    Blender 的操作符上下文约定,出问题时错误信息很难懂。
    这里的每一步本来就是纯函数,抽出来直接调更清楚。

    ── 顺序很重要 ──────────────────────────────────────────────────
    材质**必须在包装场景之前**生成:包装场景要用 material_override
    引用 materials/*.tres,引用不存在的文件 Godot 会报错。

    模型用整场景那份:只 instance 角色的话,按 F5 只能看到角色一个人
    悬在空中,地面和别的道具都不见了(实测踩过)。
    """
    import bpy

    result = {"written": [], "warnings": [], "error": None,
              "bodies": 0, "materials": 0, "textures": 0,
              "cameras": 0, "lights": 0}
    if scene is None or not directory:
        result["error"] = "没有场景或没有项目目录"
        return result

    is_3d = str(dimension) != "2"

    # ---- 0) 先找脚本挂载目标 ----
    # 必须最先做:它决定"角色是谁",而角色的网格要从 World.glb 里排除掉
    # (否则两份网格重叠 -> 深度冲突 -> 模型一闪一闪)。
    script_target = None
    for obj, physics, enabled in collect_physics(scene):
        if enabled and physics is not None and \
                getattr(physics, "script_target", False):
            script_target = obj
            break

    # ── 没勾「脚本挂载目标」时必须说出来 ────────────────────────────
    # 不勾的话导出的场景**一个脚本都不会挂** —— 用户按 F5 看到角色不动,
    # 会以为插件坏了。实测踩过:导出看起来全成功(文件都写了、无报错),
    # 但 .tscn 里根本没有 script = 那一行。
    if script_name and script_target is None:
        result["warnings"].append(
            "脚本没有挂到任何节点上:请在物体的『游戏物理 (BlenderGameKit)』"
            "面板里勾上『脚本挂载目标』(一般选角色身体那一个),再导出一次。"
            "否则导出的场景里不会有脚本,角色不会动。"
        )

    # ---- 0b) 角色自己的 glb(必须在导出整场景**之前**)----
    #
    # 包装场景要把角色节点做成"自带网格"的(self-contained),
    # 就得先有这个 glb 文件;而且导出整场景时要把它排除掉。
    # 见 write_world_scene 文档里 character_model 那段说明。
    char_model = None
    if script_target is not None:
        char_model = _sanitize_node_name(script_target.name) or "Character"
        char_path, char_ok, char_msg = export_subtree_glb(
            directory, char_model, [script_target], scene=scene)
        if char_ok:
            result["written"].append(char_path)
        else:
            result["warnings"].append(
                f"角色单独导出失败({char_msg}),已退回整场景模型:"
                f"这样角色移动时看不到自己的网格跟着动。")
            char_model = None

    # ---- 1) 整场景 glb(排除角色,避免两份网格重叠)----
    world_path, world_ok, world_message = export_scene_glb(
        directory, "World", scene=scene,
        exclude=[script_target] if (script_target is not None and char_model) else None)
    if world_ok and world_path:
        result["written"].append(world_path)
    else:
        # ── 失败时 world_path 是 None,绝不能塞进 written ─────────────
        # 实测踩过:导出失败后 `written` 里混进了 None,调用方
        # (`gk.export_assets`)拿它去 `os.path.basename(path)` 就崩:
        #     TypeError: expected str, bytes or os.PathLike object, not NoneType
        # 结果是"导出失败"这件事本身又把插件搞崩一次,用户看到的是
        # 一堆看不懂的 Python traceback,而不是"导出失败了,原因是…"。
        result["warnings"].append(f"整场景导出失败: {world_message}")

    # ---- 2) 材质(必须在包装场景之前)----
    #
    # 贴图必须在材质**之前或同时**落盘:.tres 里引用
    # `res://art/xxx.png`,文件不存在的话 Godot 加载 .tres 会失败,
    # 进而整个场景实例化失败 —— 表现是打开一片空白,日志里只有一行
    # "Failed loading resource"。这是"卡通材质显示不出来"的直接原因。
    # 材质文件名先由 write_materials 统一算出来,再把同一份传给
    # scene_materials —— 两边各算一次的话,重名材质(M MD 模型动辄几十个
    # 槽)会算出互相对不上的文件名,运行时按名字找不到文件。
    materials_map = scene_materials(scene)
    try:
        materials = write_materials(directory, None)
        result["written"].extend(item[1] for item in materials)
        result["written"].extend(copy_shaders(directory))
        result["materials"] = len(materials)
        # 用**实际写出的文件名**回填映射,保证两边一致:
        # write_materials 返回 (材质名, 路径, 有无贴图),这里去掉扩展名
        # 得到 {材质名: 文件名},再让 scene_materials 用同一份。
        by_material = {}
        for mat_name, path, _tex in materials:
            base = os.path.basename(path)
            by_material[mat_name] = base[:-5] if base.endswith(".tres") else base
        materials_map = scene_materials(scene, file_names=by_material)
        # 贴图要跟着材质一起落盘。
        #
        # ── 必须喂**全部**材质,不能只喂 materials_map 里那几个 ────────
        # 踩过的坑:`materials_map` 是 {物体名: 第一个材质},一个网格
        # 只有一个条目。MMD 那种一个网格 33 个材质槽的模型,用它去导贴图
        # 只会导出**第一张贴图**,其余材质的贴图全部缺失 ——
        # Godot 报 "Resource file not found: res://art/hair_png.png",
        # 材质加载失败,模型回到白模。
        # 所以这里直接拿 write_materials 实际处理过的那批材质。
        used_materials = []
        seen_materials = set()
        for _mat_name, _path, _tex in materials:
            material = bpy.data.materials.get(_mat_name)
            if material is not None and _mat_name not in seen_materials:
                seen_materials.add(_mat_name)
                used_materials.append(material)
        textures = export_textures(directory, used_materials)
        result["written"].extend(path for _n, path in textures)
        result["textures"] = len(textures)
    except OSError as exc:
        result["warnings"].append(f"材质生成失败: {exc}")
        materials_map = {}

    # ---- 3) 包装场景:整个场景 + 材质覆盖 + 碰撞体 + 相机灯光 ----

    # ---- 2b) 角色自己的 glb(必须在包装场景**之前**)----
    #
    # 包装场景要把角色节点做成"自带网格"的(self-contained),
    # 就得在写 .tscn 之前先有这个 glb 文件。
    # 见 write_world_scene 文档里 character_model 那段说明。
    char_model = None
    if script_target is not None:
        char_model = _sanitize_node_name(script_target.name) or "Character"
        char_path, char_ok, char_msg = export_subtree_glb(
            directory, char_model, [script_target], scene=scene)
        if char_ok:
            result["written"].append(char_path)
        else:
            result["warnings"].append(
                f"角色单独导出失败({char_msg}),已退回整场景模型:"
                f"这样角色移动时看不到自己的网格跟着动。")
            char_model = None

    try:
        world_scene, body_count = write_world_scene(
            directory, "World",
            scene=scene, dimension=dimension,
            script_name=script_name if script_target else None,
            script_target_name=script_target.name if script_target else None,
            materials_map=materials_map,
            character_model=char_model,
            file_names=by_material,
            character_object=script_target,
        )
        result["written"].append(world_scene)
        result["bodies"] = body_count
    except OSError as exc:
        result["warnings"].append(f"包装场景生成失败: {exc}")
        return result

    # 相机 / 灯光数量(面板上要报告,用户靠这个判断"会不会又是一片灰")
    # 相机 / 灯光数量(面板上要报告,用户靠这个判断"会不会又是一片灰")
    glb_info = glb_object_names(directory, "World")
    _env, _files, _cam = build_environment_nodes(
        scene, is_3d, skip=glb_info, scene_name="World")
    result["cameras"] = len(glb_info.get("cameras", ())) or (
        1 if any(obj.type == "CAMERA" for obj in scene.objects) else 0)
    result["lights"] = len(glb_info.get("lights", ())) or (
        1 if any(obj.type == "LIGHT" for obj in scene.objects) else 0)

    # ---- 4) 角色场景(单独调试角色用)----
    #
    # ── 主场景是 World.tscn ─────────────────────────────────────────
    # 用户的反馈很明确:"应该默认为平面为主场景,不然每次打开 Godot
    # 再设置项目主场景太烦人了"。
    #
    # 以前把主场景指向角色场景,是因为 World.tscn 里移动角色时画面不动
    # (角色网格在世界 glb 里,和碰撞体分属两棵子树)。现在 World.tscn 里
    # 角色节点**自带网格**了(见 character_model),这个问题不存在了,
    # 所以主场景回到 World —— 打开就能看到地面和整个场景。
    #
    # 角色场景保留:它的用途是"只调试角色一个人",不再抢主场景。
    if script_name and script_target is not None:
        bounds = object_bounds([script_target])
        try:
            scene_path, _cam, _light = write_character_scene(
                directory,
                root_name=script_target.name,
                # 基类必须和主场景里那个节点的类型**一致** ——
                # 不一致的话 Godot 会拒绝加载脚本
                # (Script inherits from native type 'X', so it can't be
                #  assigned to an object of type: 'Y')。
                # 所以这里调的是同一个解析函数,不另外猜。
                base_class=_base_class_for(scene, script_target, dimension),
                # 上面 0b) 已经导过了,这里直接复用,不重复导一遍
                model_name=char_model or "World",
                script_name=script_name,
                bounds=bounds,
                dimension=dimension,
                scene=scene,
                # 材质映射要带过去 —— 不带的话角色场景里没有 materials=,
                # gk_environment.gd 就挂不上卡通材质(踩过)
                materials_map=materials_map,
                file_names=by_material,
                character_object=script_target,
                # 不再抢主场景:World.tscn 现在也是"移动角色时画面会跟着动"的
                set_as_main=False,
            )
            result["written"].append(scene_path)
            result["character_scene"] = scene_path
        except OSError as exc:
            result["warnings"].append(f"角色场景生成失败: {exc}")

    result["directory"] = directory

    # ---- 5) 没有材质的网格会渲染成灰模,提前说清楚 ----
    # 这不是渲染坏了,是那个物体在 Blender 里就没有材质。
    # 不说的话用户只会看到"一片灰",完全想不到要去加材质。
    no_material = meshes_without_material(scene)
    if no_material:
        result["warnings"].append(
            "这些网格没有材质,在 Godot 里会显示成灰模:"
            + "、".join(no_material)
            + "。在 Blender 里给它们指定一个材质(『材质属性』面板 → 新建),"
              "再导出一次就有颜色了。"
        )

    # ---- 6) 碰撞体和网格尺寸差太多 —— 看起来像"陷进去/悬空" ----
    # 导出本身不会失败,但运行起来"走到模型跟前就穿模"或者"没碰到就撞上",
    # 用户很容易把它当成渲染 bug(实测就这么被报过)。
    mismatched = colliders_that_mismatch_mesh(scene)
    if mismatched:
        details = ";".join(
            f"{name} 网格{mesh} 碰撞体{collider}"
            for name, mesh, collider in mismatched[:4])
        result["warnings"].append(
            "这些物体的碰撞体和网格尺寸差得比较多,运行时会看起来"
            "『陷进去』或『悬空』:" + details
            + "。在『游戏物理』面板里把『碰撞尺寸』清成 0,让碰撞体自动贴合模型。"
        )

    return result


_ROOT_GLB = None


def export_subtree_glb(export_dir, name, roots, scene=None):
    """把若干物体**及其全部子级**导出成一个独立的 .glb。

    ── 为什么角色必须有自己的 glb ──────────────────────────────────
    以前角色场景里 instance 的是整个 `World.glb`,于是它的节点树长这样:

        Cube [CharacterBody3D]        ← 脚本在这里,移动它
          Model [instance World.glb]  ← **整个世界**都塞在这里
            Plane / Suzanne / Camera …

    结果:一移动角色,**整个世界跟着跑**(地面、别的道具全飞了)。
    而且因为 Godot 不允许往实例内部挂节点,也没法把碰撞体挂到角色网格上
    (实测报 "Parent path './World/Suzanne' has vanished")。

    正确做法:角色自己一个 glb,里面只有角色网格。

    返回 (路径, 是否成功, 消息)。
    """
    import bpy

    scene = scene or bpy.context.scene
    if scene is None or not roots:
        return None, False, "没有可导出的角色物体"

    # 收集:根 + 全部后代 + **蒙皮用的骨架**
    #
    # ── 为什么必须带上骨架(实测踩过)────────────────────────────────
    # 骨架在 Blender 里通常是网格的**兄弟**(两者同级,网格挂一个
    # Armature 修改器指过去),而不是网格的子级。只收"根 + 后代"的话,
    # 骨架会被漏掉 —— 导出的角色 glb 里 `skins: 0`、`animations: []`,
    # 动画全留在了世界 glb 里,角色身上根本播不了。
    #
    # 实测症状:Body.glb 里 animations=[] / skins=0,而 World.glb 里
    # animations=['RigAction'] / skins=1 —— 骨骼和角色分家了。
    wanted = set()
    stack = [obj for obj in roots if obj is not None]
    seen = set()
    while stack:
        obj = stack.pop()
        if obj in seen:
            continue
        seen.add(obj)
        wanted.add(obj)
        stack.extend(obj.children)
        # 蒙皮骨架:网格身上的 Armature 修改器指向谁,就把谁拉进来
        for mod in getattr(obj, "modifiers", ()) or ():
            if getattr(mod, "type", "") == "ARMATURE" and getattr(mod, "object", None):
                stack.append(mod.object)
        # 骨架本身也要算:如果入口物体就是个骨架
        if getattr(obj, "type", "") == "ARMATURE":
            stack.extend(obj.children)

    previous = [obj for obj in scene.objects if obj.select_get()]
    for obj in scene.objects:
        obj.select_set(obj in wanted and not obj.get(PREVIEW_MARK))

    target_dir = os.path.join(export_dir, project.ART_DIR)
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, f"{name}.glb")

    kwargs = {
        "filepath": path,
        "export_format": "GLB",
        "use_selection": True,
        "export_apply": False,
        "export_yup": True,
        "export_materials": "EXPORT",
        "export_animations": True,
        "export_skins": True,
        "export_texcoords": True,
        "export_normals": True,
        # 相机灯光由 Environment 脚本 / 主场景负责,角色 glb 里不要
        "export_cameras": False,
        "export_lights": False,
        "export_extras": True,
    }
    result = _run_gltf_export(kwargs, path)

    try:
        for obj in scene.objects:
            obj.select_set(obj in previous)
    except Exception:
        pass
    return result


def write_character_scene(export_dir, root_name, base_class, model_name, script_name,
                          bounds, dimension="3", with_collision=True, scene=None,
                          set_as_main=True, model_dir=None, materials_map=None,
                          file_names=None, character_object=None):
    """生成把 模型 + 碰撞体 + 脚本 + 相机灯光 拼在一起的 .tscn。

    ── 结构(这才是能让角色动起来的结构)─────────────────────────────
        Cube [CharacterBody3D]        ← 脚本挂这里;移动它
          Collision [CollisionShape3D]
          Model [instance Cube.glb]   ← 只有角色网格

    碰撞体和网格都是**根节点的子级**,所以根一动,两个一起动。

    为什么要带相机和灯光:以前只放模型,结果按 F5 看到的**是一片灰** ——
    没有相机就没有视点,没有灯光就全黑,再加上 Godot 默认的灰底,
    看起来就像"渲染坏了"。从 Blender 场景里把相机和灯光搬过来就没这个问题。

    set_as_main=False 时不会改主场景 —— 整场景导出会先写 World 场景并设成主场景,
    之后再写角色场景时不能再抢,否则 F5 打开的就只剩角色了。
    """
    dimension_2d = str(dimension) == "2"
    sub_resources = ""
    collision = ""
    if with_collision and bounds is not None:
        size = bounds["size"]
        if dimension_2d:
            radius = max(size[0], 0.01) * 0.5
            height = max(size[1], 0.02)
        else:
            radius = max(size[0], size[2], 0.01) * 0.5
            height = max(size[1], 0.02)
        sub_resources = COLLISION_TEMPLATE.format(radius=radius, height=height)
        collision = COLLISION_NODE_2D if dimension_2d else COLLISION_NODE_3D

    # 相机 / 灯光的处理和包装场景一致:glb 里已经有就不再生成
    # (见 build_environment_nodes 的说明)。角色场景 instance 的也是
    # 那个 glb,所以它同样带着相机和灯。
    glb_info = glb_scene_nodes(export_dir, model_name)

    # ── 材质映射必须一起带过来(踩过的坑)───────────────────────────
    # 以前这里**没传** material_files,于是角色场景里的 Environment 节点
    # 不会写 materials= 那一行,`gk_environment.gd` 也就挂不上卡通材质 ——
    # 角色只剩 glb 自带的 StandardMaterial3D,看起来"材质没生效"
    # (用户实测:"地面有材质,为什么没渲染上")。
    #
    # 键用 **glb 里的网格节点名** —— 因为 `_apply_materials` 是在 Godot
    # 运行时按节点名 find_child 找的。Blender 的物体名在导出时会被
    # sanitize,两者可能不一致,所以优先用 glb 里真实存在的节点名。
    #
    # 材质槽多于一个时改成**逐表面**覆盖(`节点名#序号`),否则 MMD 那种
    # 一个网格几十个材质槽的模型会被一条 material_override 全部刷成同一个
    # 材质,看起来"变白"(见 surface_material_files 的说明)。
    material_files = {}
    per_surface = surface_material_files(character_object, file_names)
    if per_surface:
        material_files.update(per_surface)
    else:
        for node_name in sorted(glb_info.get("meshes", {})):
            entry = (materials_map or {}).get(node_name)
            if entry:
                material_files[node_name] = entry[1]
        if not material_files:
            # glb 里没解析出网格名(或名字对不上)时,退回用 Blender 物体名当键。
            # 单网格角色(最常见)的两种情况名字通常一致。
            for obj_name in sorted(materials_map or {}):
                material_files[obj_name] = materials_map[obj_name][1]

    environment, env_files, _camera_name = build_environment_nodes(
        scene, not dimension_2d, skip=glb_info, scene_name=model_name,
        material_files=material_files)
    camera_count = len(glb_info.get("cameras", ()))
    light_count = len(glb_info.get("lights", ()))

    # Environment 脚本的 ext_resource 行 —— 只要有 Environment 节点就必须有它。
    # 注意判据是 **environment 文本非空**,不是 env_files 非空:
    # build_environment_nodes 在"相机灯都在 glb 里"时不重写脚本文件,
    # 但脚本本身早就在磁盘上了。以前拿 env_files 当判据,导致节点引用了
    # 一个没声明的 ExtResource,Godot 直接报
    #     Parse Error: Can't load resource ... env_script
    env_resource = ""
    if environment.strip():
        env_resource = ENV_RESOURCE_LINE.format(scripts_dir=project.SCRIPTS_DIR)

    steps = 1 + (1 if sub_resources else 0) + 2 + (1 if env_resource else 0)
    text = SCENE_TEMPLATE.format(
        steps=steps,
        script_path=f"res://{project.SCRIPTS_DIR}/{script_name}",
        model_path=f"res://{model_dir or project.ART_DIR}/{model_name}.glb",
        env_resource=env_resource,
        sub_resources=sub_resources,
        root_name=root_name or "Character",
        base_class=base_class,
        collision=collision,
        environment=environment,
    )
    # .tscn 统一放 scenes/(脚本在 scripts/,模型在 art/)
    path = os.path.join(export_dir, project.SCENES_DIR,
                        f"{root_name or 'Character'}.tscn")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    for relative, content in (env_files or {}).items():
        target = os.path.join(export_dir, relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    if set_as_main:
        # 顺手设成主场景,否则 Godot 里按 F5 会弹"没有设置主场景"
        project.set_main_scene(export_dir,
                               f"{project.SCENES_DIR}/{os.path.basename(path)}")
    return path, camera_count, light_count


def shader_source_dir():
    """着色器所在目录。

    必须放在**插件包内部**(BlenderGameKit/shaders/)，因为它要随扩展一起
    打包;放在仓库根目录的话，插件装进 Blender 之后就找不到了。
    """
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "shaders")


def copy_shaders(export_dir):
    """把 NPR 着色器复制进项目的 shaders/ 目录(Godot 不能引用目录外的文件)。"""
    source = shader_source_dir()
    written = []
    if not os.path.isdir(source):
        gk_log.warn(f"找不到着色器目录: {source}")
        return written
    target = os.path.join(export_dir, project.SHADERS_DIR)
    os.makedirs(target, exist_ok=True)
    for name in sorted(os.listdir(source)):
        if not name.endswith(".gdshader"):
            continue
        with open(os.path.join(source, name), "r", encoding="utf-8") as src:
            text = src.read()
        with open(os.path.join(target, name), "w", encoding="utf-8", newline="\n") as dst:
            dst.write(text)
        written.append(os.path.join(target, name))
    return written


def ensure_project_file(export_dir, project_name="BlenderGameKit Asset Export"):
    """确保目录里有 project.godot,返回它的路径。

    已存在时直接返回路径(不重写、也不返回 None),调用方不用区分
    "新建"和"已存在"两种情况。

    这里**只用** project.create_project() 那一套模板,不另写一份 ——
    早期版本这里自己拼过一个更简陋的 project.godot,和 create_project()
    写的内容不一致,两个地方都能建 project.godot 必然打架。
    """
    project.create_project(export_dir, title=project_name)
    path = os.path.join(export_dir, "project.godot")
    # create_project() 一定会把它建出来,所以这里不需要再兜底拼一份 ——
    # 曾经留着一段"如果不存在就自己写"的老代码,和模板不一致且永远不会执行。
    return path if os.path.exists(path) else None
