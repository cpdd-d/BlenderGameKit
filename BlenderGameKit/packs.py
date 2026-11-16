"""资源包 / 数据包:导出侧的发现、校验与运行时安装(用户 2026-11-10)。

── 为什么单独一个模块 ──────────────────────────────────────────────
资源包(贴图 / shader 覆盖)和数据包(JSON 声明的方块等数据)在**运行时**
由两份 autoload 脚本负责,但"项目里有没有包、要不要装运行时、取消后怎么
摘干净"是导出侧的事。放这里而不是塞进 asset_export:

  * 纯文件操作 + JSON,不依赖 bpy —— 离线单测可以直接跑;
  * 和 persistent_player / player_prefs 一样,保持"一个能力一个模块";
  * 运行时脚本是**模板字符串**,和包格式的文档放在一起,改格式时不会
    出现"文档一处、脚本一处"的漂移。

── 目录约定 ────────────────────────────────────────────────────────
    <项目>/resourcepacks/<包名>/pack.json     目录包
    <项目>/resourcepacks/<包名>.zip           压缩包(清单在 zip 根)
    <项目>/datapacks/<包名>/pack.json         数据包,同上
运行时还会扫 `user://resourcepacks` / `user://datapacks`,用户不重新导出
也能热插拔;同 id 时 user:// 覆盖 res://。

── 一条硬约束 ──────────────────────────────────────────────────────
**没有包时一个文件都不写、project.godot 一个字都不改。** 现有示例和回归
测试大量比对导出产物,新增能力不能改变"没有用到它"时的结果。
"""

import json
import os
import re
import zipfile

# 项目里的包目录(同时也是 res:// 路径)
RESOURCE_PACKS_DIR = "resourcepacks"
DATA_PACKS_DIR = "datapacks"

# 运行时 autoload 的名字 / 脚本文件名
RESOURCE_AUTOLOAD = "GkPacks"
CONTENT_AUTOLOAD = "GkContent"
RESOURCE_SCRIPT = "gk_packs.gd"
CONTENT_SCRIPT = "gk_content.gd"

MANIFEST_NAME = "pack.json"


# ==========================================================================
# 运行时脚本:资源包
# ==========================================================================
_RESOURCE_PACK_GD = """# 由 BlenderGameKit 自动生成 —— 资源包(贴图 / shader 覆盖)
#
# 目录约定:
#   res://resourcepacks/<包名>/pack.json   或  res://resourcepacks/<包名>.zip
#   user://resourcepacks/...                (用户目录,同 id 优先)
# pack.json:
#   {"id": "classic", "name": "经典材质",
#    "overrides": {"res://art/mc_grass.png": "overrides/mc_grass.png"}}
# 运行时切换:GkPacks.activate("classic") / deactivate() / cycle()
extends Node

const PACK_DIRS := ["res://resourcepacks", "user://resourcepacks"]
const MANIFEST := "pack.json"

signal packs_changed()
signal active_changed(id)

# 只有一个包时自动启用 —— 用户把包丢进目录就能看到效果,不用写代码。
@export var auto_activate_single := true

var packs: Array = []
var active_id := ""

var _sources: Dictionary = {}
var _overrides: Dictionary = {}
var _textures: Dictionary = {}
var _shaders: Dictionary = {}
var _material_originals: Dictionary = {}
var _node_originals: Dictionary = {}


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	refresh()
	var tree := get_tree()
	if tree != null:
		tree.node_added.connect(_on_node_added)
		tree.scene_changed.connect(_on_scene_changed)
	if auto_activate_single and packs.size() == 1:
		call_deferred("activate", str(packs[0].get("id", "")))


# ── 发现 ────────────────────────────────────────────────────────────
func refresh() -> void:
	packs.clear()
	_sources.clear()
	for root in PACK_DIRS:
		_scan(root)
	packs.sort_custom(func(a, b): return str(a.get("id", "")) < str(b.get("id", "")))
	packs_changed.emit()


func _scan(root: String) -> void:
	var dir := DirAccess.open(root)
	if dir == null:
		return
	dir.list_dir_begin()
	var name := dir.get_next()
	while name != "":
		if dir.current_is_dir():
			if not name.begins_with("."):
				_load_manifest(root.path_join(name).path_join(MANIFEST),
					root.path_join(name), "")
		elif name.get_extension().to_lower() == "zip":
			_load_zip(root.path_join(name))
		name = dir.get_next()
	dir.list_dir_end()


func _load_zip(zip_path: String) -> void:
	var reader := ZIPReader.new()
	if reader.open(zip_path) != OK:
		push_warning("[GkPacks] 打不开压缩包: " + zip_path)
		return
	var text := ""
	if reader.file_exists(MANIFEST):
		text = reader.read_file(MANIFEST).get_string_from_utf8()
	reader.close()
	_load_manifest_text(text, zip_path.get_file().get_basename(),
		zip_path.get_base_dir(), zip_path)


func _load_manifest(manifest_path: String, root: String, zip_path: String) -> void:
	_load_manifest_text(FileAccess.get_file_as_string(manifest_path),
		root.get_file(), root, zip_path)


func _load_manifest_text(text: String, fallback_id: String, root: String,
		zip_path: String) -> void:
	if text.length() > 0 and text.unicode_at(0) == 0xFEFF:
		text = text.substr(1)
	if text.strip_edges() == "":
		return
	var data = JSON.parse_string(text)
	if not (data is Dictionary):
		push_warning("[GkPacks] pack.json 不是 JSON 对象: " + fallback_id)
		return
	var manifest: Dictionary = data
	var id := str(manifest.get("id", fallback_id)).strip_edges()
	if id == "":
		id = fallback_id
	if id == "":
		return
	manifest["id"] = id
	_sources[id] = {"root": root, "zip": zip_path, "manifest": manifest}
	for index in range(packs.size()):
		if str(packs[index].get("id", "")) == id:
			packs.remove_at(index)
			break
	packs.append({"id": id, "name": str(manifest.get("name", id)),
		"kind": "zip" if zip_path != "" else "dir"})


func pack_ids() -> Array:
	var ids: Array = []
	for pack in packs:
		ids.append(str(pack.get("id", "")))
	return ids


# ── 切换 ────────────────────────────────────────────────────────────
func activate(id: String) -> bool:
	id = id.strip_edges()
	if id == "":
		deactivate()
		return false
	if id == active_id:
		return true
	if not _sources.has(id):
		push_warning("[GkPacks] 没有这个资源包: " + id)
		return false
	_restore()
	active_id = id
	_overrides = {}
	var manifest: Dictionary = _sources[id]["manifest"]
	var raw = manifest.get("overrides", {})
	if raw is Dictionary:
		for target in raw.keys():
			var rel := str(raw[target]).replace("\\\\", "/").strip_edges()
			if rel != "":
				_overrides[str(target)] = rel
	_textures.clear()
	_shaders.clear()
	_apply_to_tree()
	active_changed.emit(active_id)
	return true


func deactivate() -> void:
	if active_id == "":
		return
	_restore()
	active_id = ""
	_overrides.clear()
	_textures.clear()
	_shaders.clear()
	active_changed.emit("")


func cycle() -> String:
	var ids: Array = [""]
	for pack in packs:
		ids.append(str(pack.get("id", "")))
	var index := ids.find(active_id)
	if index < 0:
		index = 0
	var next_id := str(ids[(index + 1) % ids.size()])
	if next_id == "":
		deactivate()
	else:
		activate(next_id)
	return active_id


func is_active(id: String) -> bool:
	return id == active_id


# ── 应用 / 恢复 ──────────────────────────────────────────────────────
func _apply_to_tree() -> void:
	if active_id == "":
		return
	var tree := get_tree()
	if tree == null or tree.current_scene == null:
		return
	_apply_node(tree.current_scene)


func _apply_node(node: Node) -> void:
	if node == null or not is_instance_valid(node):
		return
	if node is MeshInstance3D:
		var mesh_node := node as MeshInstance3D
		_apply_material(mesh_node.material_override)
		for index in range(mesh_node.get_surface_override_material_count()):
			_apply_material(mesh_node.get_surface_override_material(index))
		if mesh_node.mesh != null:
			for index in range(mesh_node.mesh.get_surface_count()):
				_apply_material(mesh_node.mesh.surface_get_material(index))
	elif node is Sprite2D:
		_apply_texture(node, "texture")
	elif node is TextureRect:
		_apply_texture(node, "texture")
	elif node is TextureButton:
		_apply_texture(node, "texture_normal")
	for child in node.get_children():
		_apply_node(child)


func _apply_material(mat: Material) -> void:
	if mat == null or not is_instance_valid(mat):
		return
	if mat is ShaderMaterial:
		var shader_mat := mat as ShaderMaterial
		if shader_mat.shader != null:
			var path := shader_mat.shader.resource_path
			if path != "" and _overrides.has(path):
				var replacement := _shader_for(path)
				if replacement != null:
					_remember(mat, "shader", shader_mat.shader)
					shader_mat.shader = replacement
		# ShaderMaterial 的贴图是自定义参数(示例工程用
		# shader_parameter/albedo_texture),不是 BaseMaterial3D 的
		# albedo_texture;按 shader 的 uniform 列表逐个查资源路径。
		if shader_mat.shader != null:
			for uniform in shader_mat.shader.get_shader_uniform_list():
				var uniform_name := str(uniform.get("name", ""))
				if uniform_name == "":
					continue
				var value = shader_mat.get_shader_parameter(uniform_name)
				if value is Texture2D:
					var texture_path := (value as Texture2D).resource_path
					if texture_path != "" and _overrides.has(texture_path):
						var replacement := _texture_for(texture_path)
						if replacement != null:
							_remember(shader_mat,
								"shader_parameter/" + uniform_name, value)
							shader_mat.set_shader_parameter(
								uniform_name, replacement)
	elif mat is BaseMaterial3D:
		var base := mat as BaseMaterial3D
		if base.albedo_texture != null:
			var path := base.albedo_texture.resource_path
			if path != "" and _overrides.has(path):
				var replacement := _texture_for(path)
				if replacement != null:
					_remember(mat, "albedo_texture", base.albedo_texture)
					base.albedo_texture = replacement
	if mat.next_pass != null:
		_apply_material(mat.next_pass)


func _apply_texture(node: Object, prop: String) -> void:
	var texture = node.get(prop)
	if texture == null or not (texture is Texture2D):
		return
	var path := (texture as Texture2D).resource_path
	if path == "" or not _overrides.has(path):
		return
	var replacement := _texture_for(path)
	if replacement != null:
		_remember(node, prop, texture)
		node.set(prop, replacement)


func _remember(obj: Object, prop: String, value) -> void:
	var store: Dictionary = _material_originals
	if obj is Node:
		store = _node_originals
	if not store.has(obj):
		store[obj] = {}
	var props: Dictionary = store[obj]
	if not props.has(prop):
		props[prop] = value


func _restore() -> void:
	for mat in _material_originals.keys():
		if mat == null or not is_instance_valid(mat):
			continue
		var props: Dictionary = _material_originals[mat]
		for prop in props.keys():
			if mat is ShaderMaterial and prop.begins_with("shader_parameter/"):
				(mat as ShaderMaterial).set_shader_parameter(
					prop.trim_prefix("shader_parameter/"), props[prop])
			else:
				mat.set(prop, props[prop])
	_material_originals.clear()
	for node in _node_originals.keys():
		if node == null or not is_instance_valid(node):
			continue
		var props: Dictionary = _node_originals[node]
		for prop in props.keys():
			node.set(prop, props[prop])
	_node_originals.clear()


# ── 读包 ────────────────────────────────────────────────────────────
func _texture_for(path: String) -> Texture2D:
	if _textures.has(path):
		return _textures[path]
	var rel := str(_overrides.get(path, ""))
	if rel == "":
		return null
	var bytes := _read_override(rel)
	if bytes.is_empty():
		push_warning("[GkPacks] 包内缺少文件: " + rel)
		return null
	var image := Image.new()
	var err := OK
	var ext := rel.get_extension().to_lower()
	if ext == "jpg" or ext == "jpeg":
		err = image.load_jpg_from_buffer(bytes)
	else:
		err = image.load_png_from_buffer(bytes)
	if err != OK:
		push_warning("[GkPacks] 贴图解不开: " + rel)
		return null
	var texture := ImageTexture.create_from_image(image)
	_textures[path] = texture
	return texture


func _shader_for(path: String) -> Shader:
	if _shaders.has(path):
		return _shaders[path]
	var rel := str(_overrides.get(path, ""))
	if rel == "":
		return null
	var bytes := _read_override(rel)
	if bytes.is_empty():
		push_warning("[GkPacks] 包内缺少文件: " + rel)
		return null
	var code := bytes.get_string_from_utf8()
	if code.strip_edges() == "":
		return null
	var shader := Shader.new()
	shader.code = code
	_shaders[path] = shader
	return shader


func _read_override(rel: String) -> PackedByteArray:
	if not _sources.has(active_id):
		return PackedByteArray()
	var source: Dictionary = _sources[active_id]
	var zip_path := str(source.get("zip", ""))
	if zip_path != "":
		var reader := ZIPReader.new()
		if reader.open(zip_path) != OK:
			return PackedByteArray()
		var bytes := PackedByteArray()
		if reader.file_exists(rel):
			bytes = reader.read_file(rel)
		reader.close()
		return bytes
	return FileAccess.get_file_as_bytes(str(source.get("root", "")).path_join(rel))


# ── 场景变化 ────────────────────────────────────────────────────────
func _on_node_added(node: Node) -> void:
	if active_id == "" or node == null:
		return
	_apply_node(node)
	if node.has_signal("ready"):
		node.ready.connect(_apply_node.bind(node), CONNECT_ONE_SHOT)


func _on_scene_changed() -> void:
	if active_id != "":
		call_deferred("_apply_to_tree")
"""


# ==========================================================================
# 运行时脚本:数据包
# ==========================================================================
_CONTENT_GD = """# 由 BlenderGameKit 自动生成 —— 数据包(方块等数据驱动内容)
#
# 目录约定:
#   res://datapacks/<包名>/pack.json   或  res://datapacks/<包名>.zip
#   user://datapacks/...               (用户目录,同 id 优先)
# pack.json:
#   {"id": "ruby", "name": "红宝石扩展",
#    "blocks": [{"id": "ruby_block", "name": "红宝石块",
#                "texture": "textures/ruby.png",
#                "color": [0.8, 0.1, 0.2], "hardness": 3.0}],
#    "world_blocks": [{"block": "ruby_block", "scene": "World",
#                      "position": [0, 1, 0], "size": [1, 1, 1]}]}
# 运行时查询:GkContent.get_block("ruby_block") / block_ids() / get_recipe(id)
extends Node

const PACK_DIRS := ["res://datapacks", "user://datapacks"]
const MANIFEST := "pack.json"

var blocks: Dictionary = {}
var recipes: Array = []
var packs: Array = []

var _block_sources: Dictionary = {}
var _world_blocks: Array = []
var _texture_cache: Dictionary = {}
var _spawned: Array = []
var _spawn_queued := false


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	reload()
	var tree := get_tree()
	if tree != null:
		tree.scene_changed.connect(_on_scene_changed)


# ── 发现 ────────────────────────────────────────────────────────────
func reload() -> void:
	_clear_spawned()
	blocks.clear()
	recipes.clear()
	packs.clear()
	_block_sources.clear()
	_world_blocks.clear()
	_texture_cache.clear()
	for root in PACK_DIRS:
		_scan(root)
	_queue_spawn()


func _scan(root: String) -> void:
	var dir := DirAccess.open(root)
	if dir == null:
		return
	dir.list_dir_begin()
	var name := dir.get_next()
	while name != "":
		if dir.current_is_dir():
			if not name.begins_with("."):
				_load_manifest(root.path_join(name).path_join(MANIFEST),
					root.path_join(name), "")
		elif name.get_extension().to_lower() == "zip":
			_load_zip(root.path_join(name))
		name = dir.get_next()
	dir.list_dir_end()


func _load_zip(zip_path: String) -> void:
	var reader := ZIPReader.new()
	if reader.open(zip_path) != OK:
		push_warning("[GkContent] 打不开压缩包: " + zip_path)
		return
	var text := ""
	if reader.file_exists(MANIFEST):
		text = reader.read_file(MANIFEST).get_string_from_utf8()
	reader.close()
	_load_manifest_text(text, zip_path.get_file().get_basename(),
		zip_path.get_base_dir(), zip_path)


func _load_manifest(manifest_path: String, root: String, zip_path: String) -> void:
	_load_manifest_text(FileAccess.get_file_as_string(manifest_path),
		root.get_file(), root, zip_path)


func _load_manifest_text(text: String, fallback_id: String, root: String,
		zip_path: String) -> void:
	if text.length() > 0 and text.unicode_at(0) == 0xFEFF:
		text = text.substr(1)
	if text.strip_edges() == "":
		return
	var data = JSON.parse_string(text)
	if not (data is Dictionary):
		push_warning("[GkContent] pack.json 不是 JSON 对象: " + fallback_id)
		return
	var manifest: Dictionary = data
	var id := str(manifest.get("id", fallback_id)).strip_edges()
	if id == "":
		id = fallback_id
	if id == "":
		return
	var raw_blocks = manifest.get("blocks", [])
	if raw_blocks is Array:
		for item in raw_blocks:
			if not (item is Dictionary):
				continue
			var definition: Dictionary = item
			var block_id := str(definition.get("id", "")).strip_edges()
			if block_id == "":
				continue
			blocks[block_id] = definition
			_block_sources[block_id] = {"root": root, "zip": zip_path}
	var raw_recipes = manifest.get("recipes", [])
	if raw_recipes is Array:
		for item in raw_recipes:
			if item is Dictionary:
				recipes.append(item)
	var raw_world = manifest.get("world_blocks", [])
	if raw_world is Array:
		for item in raw_world:
			if item is Dictionary:
				_world_blocks.append(
					{"entry": item, "root": root, "zip": zip_path})
	for index in range(packs.size()):
		if str(packs[index].get("id", "")) == id:
			packs.remove_at(index)
			break
	packs.append({"id": id, "name": str(manifest.get("name", id))})


func pack_ids() -> Array:
	var ids: Array = []
	for pack in packs:
		ids.append(str(pack.get("id", "")))
	return ids


# ── 查询 ────────────────────────────────────────────────────────────
func get_block(id: String) -> Dictionary:
	return blocks.get(id, {})


func has_block(id: String) -> bool:
	return blocks.has(id)


func block_ids() -> Array:
	return blocks.keys()


func get_recipe(id: String) -> Dictionary:
	for recipe in recipes:
		if str(recipe.get("id", "")) == id:
			return recipe
	return {}


# ── 世界内容 ────────────────────────────────────────────────────────
func _on_scene_changed() -> void:
	_queue_spawn()


func _queue_spawn() -> void:
	if _spawn_queued:
		return
	_spawn_queued = true
	call_deferred("_run_spawn")


func _run_spawn() -> void:
	_spawn_queued = false
	spawn_world_blocks()


func spawn_world_blocks() -> void:
	_clear_spawned()
	var tree := get_tree()
	if tree == null or tree.current_scene == null:
		return
	var scene := tree.current_scene
	var index := 0
	for item in _world_blocks:
		var entry: Dictionary = item["entry"]
		var block_id := str(entry.get("block", ""))
		if not blocks.has(block_id):
			continue
		if not _scene_matches(str(entry.get("scene", ""))):
			continue
		var node := _build_block(block_id, entry, item["root"], item["zip"])
		if node == null:
			continue
		node.name = "GkDataBlock_%s_%d" % [block_id, index]
		scene.add_child(node)
		node.position = _vec3(entry.get("position", [0, 0, 0]), Vector3.ZERO)
		_spawned.append(node)
		index += 1


func _scene_matches(want: String) -> bool:
	if want == "" or want == "*":
		return true
	var scene := get_tree().current_scene
	if scene == null:
		return false
	if scene.name == want:
		return true
	return scene.scene_file_path.get_file().get_basename() == want


func _build_block(block_id: String, entry: Dictionary, root: String,
		zip_path: String) -> Node3D:
	var definition: Dictionary = blocks[block_id]
	var size := _vec3(entry.get("size", [1, 1, 1]), Vector3.ONE)
	var mesh_node := MeshInstance3D.new()
	var box := BoxMesh.new()
	box.size = size
	mesh_node.mesh = box
	var material := StandardMaterial3D.new()
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	var color = definition.get("color", [1, 1, 1])
	if color is Array and color.size() >= 3:
		material.albedo_color = Color(float(color[0]), float(color[1]),
			float(color[2]))
	var texture_rel := str(definition.get("texture", ""))
	if texture_rel != "":
		var texture := _texture_from(root, zip_path, texture_rel)
		if texture != null:
			material.albedo_texture = texture
	mesh_node.material_override = material
	var body := StaticBody3D.new()
	body.name = "Body"
	var shape := CollisionShape3D.new()
	var box_shape := BoxShape3D.new()
	box_shape.size = size
	shape.shape = box_shape
	body.add_child(shape)
	mesh_node.add_child(body)
	return mesh_node


func _texture_from(root: String, zip_path: String, rel: String) -> Texture2D:
	var key := zip_path + "|" + root + "|" + rel
	if _texture_cache.has(key):
		return _texture_cache[key]
	var bytes := _read_file(root, zip_path, rel)
	if bytes.is_empty():
		push_warning("[GkContent] 包内缺少贴图: " + rel)
		return null
	var image := Image.new()
	var err := OK
	var ext := rel.get_extension().to_lower()
	if ext == "jpg" or ext == "jpeg":
		err = image.load_jpg_from_buffer(bytes)
	else:
		err = image.load_png_from_buffer(bytes)
	if err != OK:
		push_warning("[GkContent] 贴图解不开: " + rel)
		return null
	var texture := ImageTexture.create_from_image(image)
	_texture_cache[key] = texture
	return texture


func _read_file(root: String, zip_path: String, rel: String) -> PackedByteArray:
	if zip_path != "":
		var reader := ZIPReader.new()
		if reader.open(zip_path) != OK:
			return PackedByteArray()
		var bytes := PackedByteArray()
		if reader.file_exists(rel):
			bytes = reader.read_file(rel)
		reader.close()
		return bytes
	return FileAccess.get_file_as_bytes(root.path_join(rel))


func _vec3(value, fallback: Vector3) -> Vector3:
	if value is Array and value.size() >= 3:
		return Vector3(float(value[0]), float(value[1]), float(value[2]))
	return fallback


func _clear_spawned() -> void:
	for node in _spawned:
		if node == null or not is_instance_valid(node):
			continue
		if node.get_parent() != null:
			node.get_parent().remove_child(node)
		node.queue_free()
	_spawned.clear()
"""


# ==========================================================================
# 包发现与校验
# ==========================================================================
def packs_root(directory, kind=RESOURCE_PACKS_DIR):
    """项目里某类包的根目录。"""
    return os.path.join(directory, kind) if directory else ""


def _read_json_text(text, label):
    """解析 pack.json 文本。返回 (dict, 错误说明)。"""
    try:
        data = json.loads(text)
    except ValueError as exc:
        return None, f"{label} 不是合法 JSON:{exc}"
    if not isinstance(data, dict):
        return None, f"{label} 的根节点必须是对象"
    return data, ""


def _normalize_manifest(data, fallback_id, pack_kind, label):
    """把清单整理成统一结构;格式问题返回 warning 而不是抛异常。

    ── 为什么宽松 ─────────────────────────────────────────────────
    包是用户手写的 JSON,一个拼写错误不应该让整次导出失败。坏的包跳过并
    在导出报告里留一条能看懂的告警,其它包照常生效。
    """
    warnings = []
    pack_id = str(data.get("id") or fallback_id or "").strip()
    if not pack_id:
        return None, warnings + [f"{label}: 缺少 id,已跳过"]
    entry = {
        "id": pack_id,
        "name": str(data.get("name") or pack_id),
        "type": pack_kind,
    }
    if pack_kind == RESOURCE_PACKS_DIR:
        raw = data.get("overrides")
        if not isinstance(raw, dict):
            return None, warnings + [f"{label}: 缺少 overrides 对象,已跳过"]
        overrides = {}
        for target, source in raw.items():
            target = str(target or "").strip()
            source = str(source or "").replace("\\", "/").strip()
            if not target.startswith("res://"):
                warnings.append(
                    f"{label}: 覆盖目标『{target}』不是 res:// 路径,已忽略")
                continue
            if not source:
                warnings.append(f"{label}: 『{target}』没有指定包内文件,已忽略")
                continue
            overrides[target] = source
        if not overrides:
            return None, warnings + [f"{label}: overrides 为空,已跳过"]
        entry["overrides"] = overrides
        entry["count"] = len(overrides)
    else:
        blocks = data.get("blocks")
        if blocks is None:
            blocks = []
        if not isinstance(blocks, list):
            return None, warnings + [f"{label}: blocks 必须是数组,已跳过"]
        valid_blocks = []
        for index, item in enumerate(blocks):
            if not isinstance(item, dict):
                warnings.append(f"{label}: blocks[{index}] 不是对象,已忽略")
                continue
            block_id = str(item.get("id") or "").strip()
            if not block_id:
                warnings.append(f"{label}: blocks[{index}] 缺少 id,已忽略")
                continue
            valid_blocks.append(dict(item, id=block_id))
        recipes = data.get("recipes") or []
        if not isinstance(recipes, list):
            warnings.append(f"{label}: recipes 必须是数组,已忽略")
            recipes = []
        world_blocks = data.get("world_blocks") or []
        if not isinstance(world_blocks, list):
            warnings.append(f"{label}: world_blocks 必须是数组,已忽略")
            world_blocks = []
        entry["blocks"] = valid_blocks
        entry["recipes"] = [item for item in recipes if isinstance(item, dict)]
        entry["world_blocks"] = [
            item for item in world_blocks if isinstance(item, dict)]
        entry["count"] = len(valid_blocks)
    return entry, warnings


def _load_zip_pack(path, kind, label):
    try:
        with zipfile.ZipFile(path) as archive:
            text = archive.read(MANIFEST_NAME).decode("utf-8-sig")
    except KeyError:
        return None, [f"{label}: 压缩包根目录没有 {MANIFEST_NAME},已跳过"]
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError) as exc:
        return None, [f"{label}: 压缩包读不出来({exc}),已跳过"]
    data, error = _read_json_text(text, f"{label}/{MANIFEST_NAME}")
    if error:
        return None, [error]
    entry, warnings = _normalize_manifest(
        data, os.path.splitext(os.path.basename(path))[0], kind, label)
    if entry is not None:
        entry["kind"] = "zip"
    return entry, warnings


def list_packs(directory, kind=RESOURCE_PACKS_DIR):
    """扫描项目里的包。返回 (packs, warnings)。

    packs 里每项:
      {"id", "name", "kind": "dir"/"zip", "path", "count", ...}
    同 id 时后扫到的覆盖先扫到的(目录按名字排序,user:// 在运行时覆盖 res://)。
    """
    packs = []
    warnings = []
    root = packs_root(directory, kind)
    if not root or not os.path.isdir(root):
        return packs, warnings
    order = {}
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        label = f"{kind}/{name}"
        if os.path.isdir(path):
            manifest_path = os.path.join(path, MANIFEST_NAME)
            if not os.path.isfile(manifest_path):
                continue
            try:
                with open(manifest_path, "r",
                          encoding="utf-8-sig") as handle:
                    text = handle.read()
            except OSError as exc:
                warnings.append(f"{label}: 读不了 {MANIFEST_NAME}({exc})")
                continue
            data, error = _read_json_text(text, f"{label}/{MANIFEST_NAME}")
            if error:
                warnings.append(error)
                continue
            entry, entry_warnings = _normalize_manifest(
                data, name, kind, label)
            if entry is None:
                warnings.extend(entry_warnings)
                continue
            warnings.extend(entry_warnings)
            entry["kind"] = "dir"
            entry["path"] = path
        elif name.lower().endswith(".zip"):
            entry, entry_warnings = _load_zip_pack(path, kind, label)
            warnings.extend(entry_warnings)
            if entry is None:
                continue
            entry["path"] = path
        else:
            continue
        if entry["id"] in order:
            warnings.append(
                f"{label}: 包 id『{entry['id']}』重复,后一个生效")
            packs[order[entry["id"]]] = entry
        else:
            order[entry["id"]] = len(packs)
            packs.append(entry)
    return packs, warnings


def has_packs(directory):
    """项目里有没有任何资源包或数据包。"""
    resource_packs, _ = list_packs(directory, RESOURCE_PACKS_DIR)
    data_packs, _ = list_packs(directory, DATA_PACKS_DIR)
    return bool(resource_packs or data_packs)


# ==========================================================================
# 安装到 Godot 项目
# ==========================================================================
def _ensure_autoload(directory, name, script_name, enabled):
    """把 autoload 注册进(或从)project.godot 摘掉。

    和 asset_export.ensure_main_ui_autoload 同一套规则:脚本可以随清理链
    消失,但 [autoload] 里的注册必须跟着状态走 —— 否则 Godot 启动时报
    "Failed to instantiate an autoload",整个工程起不来。
    """
    project_file = os.path.join(directory, "project.godot")
    if not os.path.isfile(project_file):
        return False
    with open(project_file, "r", encoding="utf-8") as handle:
        text = handle.read()
    want_line = f'{name}="*res://scripts/{script_name}"'
    existing = re.search(rf"^{re.escape(name)}\s*=.*$", text, re.MULTILINE)
    if enabled:
        if existing:
            if existing.group(0).strip() == want_line:
                return False
            text = text[:existing.start()] + want_line + text[existing.end():]
        elif "[autoload]" in text:
            index = text.index("[autoload]") + len("[autoload]")
            text = text[:index] + f"\n\n{want_line}" + text[index:]
        else:
            text = text.rstrip() + f"\n\n[autoload]\n\n{want_line}\n"
    else:
        if existing is None:
            return False
        text = text[:existing.start()] + text[existing.end():]
        text = re.sub(r"\n{3,}", "\n\n", text)
    with open(project_file, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return True


def _write_runtime_script(directory, script_name, source):
    scripts_dir = os.path.join(directory, "scripts")
    os.makedirs(scripts_dir, exist_ok=True)
    path = os.path.join(scripts_dir, script_name)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(source)
    return path


def export_packs(directory):
    """按项目里的包安装 / 卸载运行时。返回 {"written", "warnings", "notes"}。

    * 有资源包 -> 写 `scripts/gk_packs.gd` 并注册 GkPacks;
    * 有数据包 -> 写 `scripts/gk_content.gd` 并注册 GkContent;
    * 某一类没有 -> 只摘旧注册,脚本交给 generated_files 的清理链;
    * 两类都没有 -> project.godot 与 scripts/ 都不动。
    """
    result = {"written": [], "warnings": [], "notes": []}
    if not directory or not os.path.isdir(directory):
        result["warnings"].append("没有项目目录 —— 资源包/数据包运行时未安装")
        return result

    resource_packs, resource_warnings = list_packs(
        directory, RESOURCE_PACKS_DIR)
    data_packs, data_warnings = list_packs(
        directory, DATA_PACKS_DIR)
    result["warnings"].extend(resource_warnings)
    result["warnings"].extend(data_warnings)

    jobs = (
        (resource_packs, RESOURCE_SCRIPT, _RESOURCE_PACK_GD,
         RESOURCE_AUTOLOAD, "资源包"),
        (data_packs, CONTENT_SCRIPT, _CONTENT_GD,
         CONTENT_AUTOLOAD, "数据包"),
    )
    for packs, script_name, source, autoload, label in jobs:
        if packs:
            path = _write_runtime_script(directory, script_name, source)
            result["written"].append(path)
            try:
                changed = _ensure_autoload(
                    directory, autoload, script_name, True)
            except OSError as exc:
                result["warnings"].append(
                    f"写 project.godot 的 [autoload]({label})失败:{exc}")
            else:
                if changed:
                    result["notes"].append(
                        f"{label}运行时已注册为 autoload({autoload})")
            names = "、".join(pack["id"] for pack in packs[:6])
            if len(packs) > 6:
                names += "…"
            result["notes"].append(f"发现 {len(packs)} 个{label}:{names}")
        else:
            try:
                changed = _ensure_autoload(
                    directory, autoload, script_name, False)
            except OSError as exc:
                result["warnings"].append(
                    f"摘除 {autoload} 注册失败:{exc}")
            else:
                if changed:
                    result["notes"].append(
                        f"没有{label}:{autoload} 的旧注册已摘掉")
    return result
