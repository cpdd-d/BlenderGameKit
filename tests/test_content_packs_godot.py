"""资源包 / 数据包的 Godot 无头集成测试。

跑法:
    python tests/test_content_packs_godot.py
    set GK_GODOT=D:\\godot\\Godot_v4.7.2-stable_win64_console.exe

需要本机有 Godot 4 可执行文件,找不到时整条测试跳过。测试会
在临时目录里造一个最小 Godot 工程:

  * 一个目录资源包(贴图 + shader 覆盖)和一个 zip 资源包(shader 覆盖);
  * 一个数据包(声明方块并由 world_blocks 生成到场景);
  * 用 packs.export_packs 安装运行时脚本与 autoload;

然后用 ``--headless --script`` 跑 runner,断言:

  * 目录包启用后材质贴图 / shader 真的换了,且切到 zip 包后未覆盖的
    贴图会恢复原样;
  * 关闭资源包后所有原资源恢复,不留痕迹;
  * 数据包方块注册成功,world_blocks 真在场景里生成节点,
    reload 不重复生成。
"""

import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import gk_stub  # noqa: E402

gk_stub.install()
gk_stub.load_addon(os.path.join(ROOT, "BlenderGameKit"))

from BlenderGameKit import packs  # noqa: E402

# 有 pytest 时标成 godot 集成测试(默认不选);没有 pytest 时仍可用
# `python tests/test_content_packs_godot.py` 直接跑。
try:
    import pytest
except ImportError:  # pragma: no cover - 取决于开发环境
    pytest = None
if pytest is not None:
    pytestmark = pytest.mark.godot

DEFAULT_GODOT = r"D:\godot\Godot_v4.7.2-stable_win64_console.exe"
GODOT = os.environ.get("GK_GODOT", DEFAULT_GODOT)

PROJECT = """config_version=5

[application]

config/name="gk_content_packs_test"

[rendering]

renderer/rendering_method="gl_compatibility"
"""

ORIGINAL_SHADER = """shader_type spatial;

void fragment() {
\tALBEDO = vec3(0.0, 0.0, 1.0);
}
"""

TEXTURED_SHADER = """shader_type spatial;

uniform sampler2D albedo_texture : source_color;

void fragment() {
\tALBEDO = texture(albedo_texture, UV).rgb;
}
"""

DIR_PACK_SHADER = """shader_type spatial;

// PACK_OVERRIDE_MARKER

void fragment() {
\tALBEDO = vec3(1.0, 0.0, 0.0);
}
"""

ZIP_PACK_SHADER = """shader_type spatial;

// ZIP_OVERRIDE_MARKER

void fragment() {
\tALBEDO = vec3(0.0, 1.0, 0.0);
}
"""


def png_bytes(red, green, blue, alpha=255):
    """不依赖 Pillow 的 1x1 RGBA PNG。"""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = bytes([0, red, green, blue, alpha])
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def write(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    with open(path, "wb") as handle:
        handle.write(data)


def build_project(directory):
    write(os.path.join(directory, "project.godot"), PROJECT)

    # 原贴图 / 原 shader:运行时从 res:// 正常加载后再被覆盖
    write(os.path.join(directory, "art", "grass.png"), png_bytes(0, 0, 255))
    write(os.path.join(directory, "shaders", "orig.gdshader"),
          ORIGINAL_SHADER)
    write(os.path.join(directory, "shaders", "textured.gdshader"),
          TEXTURED_SHADER)

    # 目录资源包:贴图 + shader
    write(os.path.join(directory, "resourcepacks", "classic", "pack.json"),
          json.dumps({
              "id": "classic",
              "name": "经典材质",
              "overrides": {
                  "res://art/grass.png": "overrides/grass.png",
                  "res://shaders/orig.gdshader": "overrides/pack.gdshader",
              },
          }, ensure_ascii=False))
    write(os.path.join(directory, "resourcepacks", "classic", "overrides",
                       "grass.png"), png_bytes(255, 0, 0))
    write(os.path.join(directory, "resourcepacks", "classic", "overrides",
                       "pack.gdshader"), DIR_PACK_SHADER)

    # zip 资源包:只覆盖 shader
    with zipfile.ZipFile(os.path.join(directory, "resourcepacks",
                                      "zipped.zip"), "w") as archive:
        archive.writestr("pack.json", json.dumps({
            "id": "zipped",
            "name": "压缩包材质",
            "overrides": {
                "res://shaders/orig.gdshader": "overrides/zipped.gdshader",
            },
        }, ensure_ascii=False))
        archive.writestr("overrides/zipped.gdshader", ZIP_PACK_SHADER)

    # 数据包:一个方块 + 一处世界摆放
    write(os.path.join(directory, "datapacks", "ruby", "pack.json"),
          json.dumps({
              "id": "ruby",
              "name": "红宝石扩展",
              "blocks": [{
                  "id": "ruby_block",
                  "name": "红宝石块",
                  "texture": "textures/ruby.png",
                  "color": [0.8, 0.1, 0.2],
                  "hardness": 3.0,
              }],
              "world_blocks": [{
                  "block": "ruby_block",
                  "scene": "World",
                  "position": [2, 1, 0],
                  "size": [1, 1, 1],
              }],
          }, ensure_ascii=False))
    write(os.path.join(directory, "datapacks", "ruby", "textures",
                       "ruby.png"), png_bytes(255, 0, 255))


RUNNER = r'''extends SceneTree

var failures := 0


func _initialize() -> void:
	_run.call_deferred()


func _check(condition: bool, message: String) -> void:
	if not condition:
		failures += 1
		printerr("CONTENT_PACK_FAIL: " + message)


func _run() -> void:
	var world := Node3D.new()
	world.name = "World"
	root.add_child(world)
	current_scene = world

	var packs_node = root.get_node_or_null("GkPacks")
	if packs_node == null:
		packs_node = load("res://scripts/gk_packs.gd").new()
		root.add_child(packs_node)
	await process_frame

	# ── 资源包:目录包 ──────────────────────────────────────────
	var image := Image.load_from_file("res://art/grass.png")
	_check(image != null, "原贴图应能读出来")
	var original_texture := ImageTexture.create_from_image(image)
	original_texture.take_over_path("res://art/grass.png")
	var material := StandardMaterial3D.new()
	material.albedo_texture = original_texture
	var mesh_node := MeshInstance3D.new()
	mesh_node.mesh = BoxMesh.new()
	mesh_node.material_override = material
	world.add_child(mesh_node)

	var original_shader := load("res://shaders/orig.gdshader") as Shader
	_check(original_shader != null, "原 shader 应能加载")
	var shader_material := ShaderMaterial.new()
	shader_material.shader = original_shader
	var shader_node := MeshInstance3D.new()
	shader_node.mesh = BoxMesh.new()
	shader_node.material_override = shader_material
	world.add_child(shader_node)

	# 示例工程用 ShaderMaterial + shader_parameter/albedo_texture 存贴图,
	# 单独覆盖一条,确保参数路径也被识别。
	var textured_shader := load("res://shaders/textured.gdshader") as Shader
	_check(textured_shader != null, "带贴图 uniform 的 shader 应能加载")
	var textured_material := ShaderMaterial.new()
	textured_material.shader = textured_shader
	textured_material.set_shader_parameter("albedo_texture", original_texture)
	var textured_node := MeshInstance3D.new()
	textured_node.mesh = BoxMesh.new()
	textured_node.material_override = textured_material
	world.add_child(textured_node)

	packs_node.refresh()
	var ids: Array = packs_node.pack_ids()
	_check(ids.size() == 2, "应发现目录包 + zip 包,实际:" + str(ids))
	_check(packs_node.activate("classic"), "目录包应能启用")
	await process_frame

	var replaced = material.albedo_texture
	_check(replaced != null and replaced != original_texture,
		"目录包的贴图应替换材质原贴图")
	if replaced != null:
		var pixel: Color = replaced.get_image().get_pixel(0, 0)
		_check(pixel.r > 0.8 and pixel.g < 0.2,
			"替换后的贴图应来自包内 PNG,实际:" + str(pixel))
	_check(shader_material.shader != original_shader,
		"目录包的 shader 应替换原 shader")
	if shader_material.shader != null:
		_check(shader_material.shader.code.contains("PACK_OVERRIDE_MARKER"),
			"目录包 shader 内容应生效")

	var param_texture = textured_material.get_shader_parameter("albedo_texture")
	_check(param_texture != null and param_texture != original_texture,
		"ShaderMaterial 的贴图参数应被替换")
	if param_texture is Texture2D:
		var param_pixel: Color = (param_texture as Texture2D).get_image().get_pixel(0, 0)
		_check(param_pixel.r > 0.8 and param_pixel.g < 0.2,
			"ShaderMaterial 贴图参数内容应来自包内 PNG")

	# ── 资源包:切换到 zip 包 ───────────────────────────────────
	_check(packs_node.activate("zipped"), "zip 包应能启用")
	await process_frame
	if shader_material.shader != null:
		_check(shader_material.shader.code.contains("ZIP_OVERRIDE_MARKER"),
			"zip 包 shader 内容应生效")
	_check(material.albedo_texture == original_texture,
		"zip 包不覆盖贴图,原贴图应恢复")

	# ── 资源包:关闭后完全恢复 ──────────────────────────────────
	packs_node.deactivate()
	await process_frame
	_check(material.albedo_texture == original_texture,
		"关闭资源包后贴图应恢复")
	_check(shader_material.shader == original_shader,
		"关闭资源包后 shader 应恢复")
	_check(textured_material.get_shader_parameter("albedo_texture")
		== original_texture, "关闭资源包后 ShaderMaterial 贴图参数应恢复")

	# ── 数据包 ─────────────────────────────────────────────────
	var content = root.get_node_or_null("GkContent")
	if content == null:
		content = load("res://scripts/gk_content.gd").new()
		root.add_child(content)
	content.reload()
	await process_frame
	await process_frame

	_check(content.has_block("ruby_block"), "数据包方块应注册")
	var definition: Dictionary = content.get_block("ruby_block")
	_check(str(definition.get("name", "")) == "红宝石块",
		"方块名应来自 JSON")
	_check(is_equal_approx(float(definition.get("hardness", 0.0)), 3.0),
		"硬度应来自 JSON")

	var spawned := world.get_node_or_null("GkDataBlock_ruby_block_0")
	_check(spawned != null, "world_blocks 应在场景里生成方块节点")
	if spawned != null:
		_check(spawned is MeshInstance3D, "生成的方块应是 MeshInstance3D")
		_check(spawned.position.is_equal_approx(Vector3(2, 1, 0)),
			"生成位置应来自 JSON,实际:" + str(spawned.position))
		var block_material = (spawned as MeshInstance3D).material_override
		_check(block_material is StandardMaterial3D
			and block_material.albedo_texture != null,
			"生成的方块应带上数据包贴图")

	var before := world.find_children("GkDataBlock_*", "", true, false).size()
	content.reload()
	await process_frame
	await process_frame
	var after := world.find_children("GkDataBlock_*", "", true, false).size()
	_check(before == 1 and after == 1,
		"reload 不应重复生成方块,实际:" + str(before) + " -> " + str(after))

	if failures == 0:
		print("CONTENT_PACKS_RUNTIME=OK")
	quit(1 if failures > 0 else 0)
'''


@unittest.skipUnless(os.path.isfile(GODOT),
                     f"找不到 Godot 可执行文件: {GODOT}")
class ContentPacksGodotTests(unittest.TestCase):
    def test_resource_and_data_packs_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            build_project(tmp)
            installed = packs.export_packs(tmp)
            self.assertEqual(installed["warnings"], [])
            self.assertTrue(os.path.isfile(
                os.path.join(tmp, "scripts", "gk_packs.gd")))
            self.assertTrue(os.path.isfile(
                os.path.join(tmp, "scripts", "gk_content.gd")))

            write(os.path.join(tmp, "runner.gd"), RUNNER)

            env = dict(os.environ)
            env["APPDATA"] = os.path.join(tmp, "_appdata")
            env["LOCALAPPDATA"] = os.path.join(tmp, "_localappdata")
            env["XDG_DATA_HOME"] = os.path.join(tmp, "_xdg")
            os.makedirs(os.path.join(tmp, "_appdata"), exist_ok=True)

            proc = subprocess.run(
                [GODOT, "--headless", "--path", tmp, "--script",
                 "res://runner.gd"],
                cwd=tmp, env=env, capture_output=True, text=True,
                timeout=180)
            output = (proc.stdout or "") + "\n" + (proc.stderr or "")

            self.assertNotIn("SCRIPT ERROR", output)
            self.assertNotIn("CONTENT_PACK_FAIL", output)
            self.assertIn("CONTENT_PACKS_RUNTIME=OK", output)
            self.assertEqual(proc.returncode, 0, output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
