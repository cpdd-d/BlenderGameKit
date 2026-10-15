"""临时探针:线框标记物体(第一百二十四轮)。

跑法:
    & "D:\\blender-4.5.5-windows-x64\\blender.exe" -b --factory-startup \
      --python-exit-code 1 --python .audit\\blender_markers.py

查四件事:
  1) 出生点 / 区域环境点 / 关卡父级 三种标记 -> 导出的 .tscn 里真的出现了
     GKPlayerStart / GKEnvZone_ / Area3D / BoxShape3D / 关卡父级分组节点;
  2) 区域脚本 scripts/gk_env_zones.gd 落盘,且里面有 body_entered /
     gk_player / TWEEN_PAUSE_PROCESS;
  3) **没有标记**的普通场景:产物与"把新代码摘掉"的同一套导出**逐字节**一致
     (用 monkey-patch 把 env_zone_exports / level_root_exports 换成空实现,
      等价于改动前的行为;git stash 会清掉别人的工作,所以不能那么干);
  4) default_player() 的三级语义(Lead 追加的那条):无标记 -> None;
     一个角色 + 标记 -> 那个角色;两个角色 + 标记 -> None + 告警。

不进 tests/ —— 这是临时探针,用完即弃。
"""
import sys
import tempfile
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import BlenderGameKit as addon
from BlenderGameKit import asset_export as ae, markers, project
from BlenderGameKit import persistent_player as pp

addon.register()

FAILURES = []


def check(label, condition, detail=""):
    mark = "ok  " if condition else "FAIL"
    print(f"[{mark}] {label}" + (f" :: {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def clear_objects():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def clear_scenes(keep):
    for scene in list(bpy.data.scenes):
        if scene != keep:
            bpy.data.scenes.remove(scene)


def cube(name, location=(0, 0, 0), scale=(1, 1, 1)):
    bpy.ops.mesh.primitive_cube_add(size=2, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    return obj


def role_tree(name="角色逻辑"):
    tree = bpy.data.node_groups.new(name, "GKGameLogicTree")
    tree.gk_scope = "ROLE"
    tree.nodes.new("GKNodeOnReady")
    return tree


# ==========================================================================
# 场景一:三种标记齐全
# ==========================================================================
scene = bpy.context.scene
scene.name = "标记关"
clear_objects()
clear_scenes(scene)

floor = cube("地板", (0, 0, -0.1), (10, 10, 0.1))
floor.gk_physics.collision_shape = "box"

hero = cube("主角", (0, 0, 1), (0.5, 0.5, 1))
hero.gk_physics.body_type = "CharacterBody3D"
hero.gk_physics.collision_shape = "capsule"
hero.gk_physics.logic_tree = role_tree().name

spawn = cube("出生点", (2, 0, 3), (0.3, 0.3, 0.3))
markers.set_kind(spawn, markers.KIND_PLAYER_START)

zone = cube("雾区", (5, 1, 0), (3, 1, 2))
markers.set_kind(zone, markers.KIND_ENV_ZONE)
zone.gk_physics.env_preset = "浓雾夜"
zone.gk_physics.env_fade = 0.4

root = bpy.data.objects.new("关卡A", None)
scene.collection.objects.link(root)
markers.set_kind(root, markers.KIND_LEVEL_ROOT)
prop = cube("道具", (1, 1, 1), (0.5, 0.5, 0.5))
prop.parent = root
prop.matrix_parent_inverse = root.matrix_world.inverted()

bpy.ops.object.camera_add(location=(0, -6, 2))
scene.camera = bpy.context.object
bpy.ops.object.light_add(type="SUN", location=(3, -3, 6))

# 属性可读可写(老 .blend 兼容那一条的现场证据)
check("marker_kind 可读可写(别名)", zone.gk_physics.marker_kind == "ENV_ZONE",
      zone.gk_physics.marker_kind)
check("gk_marker_kind 同步(ui.py 用的名字)",
      zone.gk_physics.gk_marker_kind == "ENV_ZONE",
      zone.gk_physics.gk_marker_kind)
check("普通物体读出来是 NONE(不是空串)", floor.gk_physics.gk_marker_kind
      == markers.NONE_VALUE, floor.gk_physics.gk_marker_kind)
check("自定义属性 gk_marker 也打上了",
      str(spawn.get("gk_marker", "")) == "PLAYER_START",
      str(spawn.get("gk_marker", "")))
markers.set_kind(zone, "")
check("set_kind 传空 = 取消标记", markers.kind_of(zone) == ""
      and zone.gk_physics.gk_marker_kind == markers.NONE_VALUE
      and zone.gk_physics.marker_kind == markers.NONE_VALUE)
check("取消标记后自定义属性被摘掉", "gk_marker" not in zone)
markers.set_kind(zone, markers.KIND_ENV_ZONE)
check("set_kind 设回去(两个属性都变)",
      zone.gk_physics.gk_marker_kind == "ENV_ZONE"
      and zone.gk_physics.marker_kind == "ENV_ZONE"
      and markers.kind_of(zone) == "ENV_ZONE")
# 直接写属性(面板就是这么写的)-> 别名跟着变
zone.gk_physics.gk_marker_kind = "ENV_ZONE"
check("直接写 gk_marker_kind 别名同步",
      zone.gk_physics.marker_kind == "ENV_ZONE")
check("markers.kind_of 认得区域", markers.kind_of(zone) == "ENV_ZONE")
check("markers.kind_of 普通物体 = ''", markers.kind_of(floor) == "")
check("player_start 找到出生点", markers.player_start(scene) is spawn)
check("env_zones 找到区域", markers.env_zones(scene) == [zone])
check("level_roots 找到关卡父级", markers.level_roots(scene) == [root])
check("zone_preset", markers.zone_preset(zone) == "浓雾夜",
      markers.zone_preset(zone))
check("zone_restore 默认 True", markers.zone_restore(zone) is True)
check("zone_fade", abs(markers.zone_fade(zone) - 0.4) < 1e-6)
check("zone_size 乘了 scale", markers.zone_size(zone) == (6.0, 2.0, 4.0),
      str(markers.zone_size(zone)))
check("zone_missing_preset 空名 = True", markers.zone_missing_preset(spawn)
      is False)
zone.gk_physics.env_preset = ""
check("zone_missing_preset 空预设 = True",
      markers.zone_missing_preset(zone) is True)
zone.gk_physics.env_preset = "浓雾夜"

with tempfile.TemporaryDirectory(prefix="gk_marker_probe_") as temp:
    project.create_project(temp)
    project.set_main_scene(temp, "res://scenes/World.tscn")
    result = ae.export_all_scenes(temp)
    check("标记关导出无 error", not result.get("error"), str(result.get("error")))
    text = (Path(temp) / "scenes/World.tscn").read_text(encoding="utf-8")
    check("有 GKPlayerStart", "GKPlayerStart" in text)
    check("出生点用的是标记物体的变换", "2.00000000, 3.00000000" in text,
          [line for line in text.splitlines() if "GKPlayerStart" in line])
    check("有 GKEnvZone_ 前缀节点", "GKEnvZone_雾区" in text)
    check("区域是 Area3D", '[node name="GKEnvZone_雾区" type="Area3D"' in text)
    check("有 BoxShape3D", "BoxShape3D" in text)
    check("盒子尺寸换了轴(6,4,2)",
          "size = Vector3(6.000000, 4.000000, 2.000000)" in text,
          [line for line in text.splitlines() if line.startswith("size =")])
    check("区域挂在 gk_env_zone 组",
          'groups=["gk_env_zone"]' in text)
    check("区域脚本被引用", 'id="env_zones"' in text)
    check("区域参数带上了浓雾夜(雾浓度 0.12)",
          '"fog_d": 0.120000' in text and '"fog": true' in text)
    check("区域过渡秒写进去了", "zone_fade = 0.400000" in text)
    check("区域离开还原写进去了", "zone_restore = true" in text)
    check("关卡父级分组节点存在",
          '[node name="关卡A" type="Node3D" parent="."]' in text)
    check("道具的物理体被挪进分组",
          '[node name="道具" type="StaticBody3D" parent="关卡A"' in text,
          [line for line in text.splitlines() if 'name="道具"' in line])
    check("道具的碰撞体跟着换了父路径",
          'parent="关卡A/道具"' in text,
          [line for line in text.splitlines() if 'name="Collision"' in line])
    check("Colliders 容器还在",
          '[node name="Colliders" type="Node3D" parent="."]' in text)
    check("标记物体没进几何(出生点不在 Colliders 里)",
          'name="出生点"' not in text)
    zone_script = Path(temp) / "scripts/gk_env_zones.gd"
    check("gk_env_zones.gd 落盘", zone_script.is_file())
    script_text = zone_script.read_text(encoding="utf-8")
    check("脚本是 @tool", script_text.startswith("#") and "@tool" in script_text)
    check("脚本接 body_entered / body_exited",
          "body_entered.connect" in script_text
          and "body_exited.connect" in script_text)
    check("脚本按 gk_player 组过滤", 'is_in_group("gk_player")' in script_text)
    check("脚本暂停时也过渡",
          "Tween.TWEEN_PAUSE_PROCESS" in script_text)
    check("区域脚本被记进 written(旧文件清理不会删它)",
          any(str(path).endswith("gk_env_zones.gd")
              for path in result.get("written", ())),
          "written 条数 " + str(len(result.get("written", ()))))
    # 角色被自动识别成跨关卡角色 -> 关卡场景里不该再有它
    check("自动识别的角色不进关卡几何", "CharacterBody3D" not in text)
    check("默认角色场景生成", (Path(temp) / "scenes/GKDefaultPlayer.tscn").is_file())

# ==========================================================================
# 场景二:普通场景(没有任何标记) —— 产物必须与"改动前"逐字节一致
# ==========================================================================
clear_objects()
clear_scenes(scene)
scene.name = "普通关"
plain_floor = cube("地板", (0, 0, -0.1), (10, 10, 0.1))
plain_floor.gk_physics.collision_shape = "box"
plain_hero = cube("角色", (0, 0, 1), (0.5, 0.5, 1))
plain_hero.gk_physics.body_type = "CharacterBody3D"
plain_hero.gk_physics.collision_shape = "capsule"
plain_hero.gk_physics.logic_tree = role_tree("角色逻辑2").name
plain_prop = cube("箱子", (4, 0, 0.5), (0.5, 0.5, 0.5))
bpy.ops.object.camera_add(location=(0, -6, 2))
scene.camera = bpy.context.object
bpy.ops.object.light_add(type="SUN", location=(3, -3, 6))


def export_plain(directory, strip_new_code):
    """导出一个普通场景;strip_new_code=True 时把新代码换成空实现。"""
    saved = (ae.env_zone_exports, ae.level_root_exports)
    if strip_new_code:
        ae.env_zone_exports = lambda scene, is_3d=True: {
            "nodes": [], "resources": [], "warnings": []}
        ae.level_root_exports = lambda scene: ([], {})
    try:
        project.create_project(directory)
        project.set_main_scene(directory, "res://scenes/World.tscn")
        exported = ae.export_all_scenes(directory)
        scene_text = (Path(directory) / "scenes/World.tscn").read_text(
            encoding="utf-8")
        written = []
        for path in exported.get("written", ()):
            try:
                written.append(str(Path(path).resolve().relative_to(
                    Path(directory).resolve())).replace("\\", "/"))
            except ValueError:
                written.append(str(path))
        written.sort()
        return exported, scene_text, written
    finally:
        ae.env_zone_exports, ae.level_root_exports = saved


with tempfile.TemporaryDirectory(prefix="gk_plain_new_") as temp_new, \
        tempfile.TemporaryDirectory(prefix="gk_plain_old_") as temp_old:
    new_result, new_text, new_written = export_plain(temp_new, False)
    old_result, old_text, old_written = export_plain(temp_old, True)
    check("普通场景导出无 error(新代码)", not new_result.get("error"),
          str(new_result.get("error")))
    check("普通场景导出无 error(摘掉新代码)", not old_result.get("error"),
          str(old_result.get("error")))
    check("没有标记时不生成 GKEnvZone / env_zones",
          "GKEnvZone" not in new_text and "env_zones" not in new_text)
    check("没有标记时不生成 gk_env_zones.gd",
          not (Path(temp_new) / "scripts/gk_env_zones.gd").exists())
    check("没有标记时不生成关卡父级分组", "关卡A" not in new_text)
    check("普通场景 .tscn 与改动前逐字节一致", new_text == old_text,
          "" if new_text == old_text else "长度 %d vs %d"
          % (len(new_text), len(old_text)))
    check("普通场景产物清单一致", new_written == old_written,
          str(set(new_written) ^ set(old_written)))

# ==========================================================================
# default_player 的三级语义(Lead 追加的验收)
# ==========================================================================
clear_objects()
clear_scenes(scene)
scene.name = "语义关"
plain = cube("角色", (0, 0, 1))
plain.gk_physics.body_type = "CharacterBody3D"
plain.gk_physics.logic_tree = role_tree("角色逻辑3").name
check("① 没有出生点标记 -> None", pp.default_player() is None,
      str(pp.default_player()))

spawn2 = cube("出生点", (1, 0, 1), (0.3, 0.3, 0.3))
spawn2.gk_physics.marker_kind = markers.KIND_PLAYER_START
check("② 一个角色 + 出生点标记 -> 那个角色",
      pp.default_player() is plain, str(pp.default_player()))
check("② 识别结果和 infer_default_player 一致",
      pp.infer_default_player() is plain)

second = cube("第二个角色", (5, 0, 1))
second.gk_physics.body_type = "CharacterBody3D"
pp.take_infer_warnings()
check("③ 两个角色 + 标记 -> None", pp.default_player() is None,
      str(pp.default_player()))
warnings = pp.take_infer_warnings()
check("③ 给出了『多个角色』的中文告警",
      any("多个 3D 角色体" in message for message in warnings), str(warnings))

# 老行为:手选优先(世界设置里指定了就原样返回)
from BlenderGameKit import ui
world = ui.world_settings()
world.default_player = second
check("④ 手选优先于自动识别", pp.default_player() is second)
world.default_player = None
check("④ 清掉手选回到老路(None)", pp.default_player() is None)

print("MARKER_FAILURES=%d" % len(FAILURES))
for name in FAILURES:
    print("  FAILED: " + name)
if FAILURES:
    raise SystemExit(1)
print("MARKERS=OK")
