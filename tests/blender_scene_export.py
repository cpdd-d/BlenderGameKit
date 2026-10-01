"""验证:整场景导出后,Godot 里的场景和 Blender 里一样。

核心判据不是"文件生成了",而是:
  1. .glb 里真的有 Blender 场景的全部对象和层级
  2. 变换是对的(位置/旋转/缩放,含 Z-up -> Y-up 转换)
  3. 包装的 .tscn 能被 Godot 打开
  4. 主场景指向包装场景,按 F5 看到的就是整个场景

用法:
    blender -b --python tests/blender_scene_export.py
"""

import json
import os
import re
import shutil
import struct
import sys
import tempfile
import traceback
from importlib import import_module

import addon_utils
import bmesh
import bpy

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" - {detail}" if detail and not condition else ""))
    return bool(condition)


def find_addon():
    for module in addon_utils.modules():
        if "blender_game_kit" in getattr(module, "__name__", ""):
            return module.__name__
    return None


def read_glb_json(path):
    """读 .glb 的 JSON 块。GLB 格式: 12 字节头 + 若干 chunk。"""
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:4] != b"glTF":
        raise ValueError("不是 glTF 文件")
    offset = 12
    while offset < len(data):
        length, kind = struct.unpack_from("<II", data, offset)
        offset += 8
        chunk = data[offset:offset + length]
        if kind == 0x4E4F534A:      # 'JSON'
            return json.loads(chunk.decode("utf-8"))
        offset += length
    raise ValueError("找不到 JSON 块")


def _read_glb_json(path):
    """读 .glb 的 JSON 段。"""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    offset = 12
    while offset < len(data):
        length, kind = struct.unpack_from("<II", data, offset)
        offset += 8
        if kind == 0x4E4F534A:
            return json.loads(data[offset:offset + length].decode("utf-8"))
        offset += length
    return None


def main():
    print("=" * 74)
    print("整场景导出测试")
    print("=" * 74)

    module_name = find_addon()
    if not check("找到插件", module_name, "未安装"):
        return 1
    addon_utils.enable(module_name, default_set=True, persistent=True)
    asset_export = import_module(f"{module_name}.asset_export")
    project = import_module(f"{module_name}.project")

    workdir = os.path.join(tempfile.gettempdir(), "gk_scene_export")
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)

    # ---- 造一个"像样"的场景:分层的角色 + 一个立方体 + 灯光 ----
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    # 父空物体
    root = bpy.data.objects.new("CharacterRoot", None)
    scene.collection.objects.link(root)
    root.location = (0.0, 0.0, 1.5)

    # 身体(球体),挂在父物体下
    body_mesh = bpy.data.meshes.new("BodyMesh")
    body = bpy.data.objects.new("Body", body_mesh)
    scene.collection.objects.link(body)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=0.5)
    bm.to_mesh(body_mesh)
    bm.free()
    body.parent = root
    body.location = (0.0, 0.0, -0.5)

    # 一个立方体,不挂父
    box_mesh = bpy.data.meshes.new("BoxMesh")
    box = bpy.data.objects.new("Box", box_mesh)
    scene.collection.objects.link(box)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=0.8)
    bm.to_mesh(box_mesh)
    bm.free()
    box.location = (3.0, 0.0, 0.4)

    # 灯光 + 相机
    light_data = bpy.data.lights.new("KeyLight", type="SUN")
    light = bpy.data.objects.new("KeyLight", light_data)
    scene.collection.objects.link(light)
    light.location = (2.0, -2.0, 6.0)

    camera_data = bpy.data.cameras.new("ShotCam")
    camera = bpy.data.objects.new("ShotCam", camera_data)
    scene.collection.objects.link(camera)
    camera.location = (0.0, -6.0, 2.0)
    # 必须给旋转 —— 不给的话相机朝 −Z(正前方水平),
    # 而它在 −6 的位置,等于看向场景**外面**,画面只有背景。
    # (排查了很久:一度以为是四元数转换算错了。)
    camera.rotation_euler = (1.30, 0.0, 0.0)
    scene.camera = camera

    # 保存 .blend,让项目解析有个落点
    blend_path = os.path.join(workdir, "scene_test.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)

    directory, _reason = project.resolve(scene)
    project.create_project(directory)
    print(f"    项目目录: {directory}")

    # ---- 1. 整场景导出 ----
    try:
        path, ok, message = asset_export.export_scene_glb(directory, "World")
    except Exception as exc:
        check("整场景导出", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1

    check("整场景导出成功", ok, message)
    check("生成了 World.glb", os.path.isfile(path), str(path))
    if not ok:
        return 1

    # ---- 2. 检查 glb 里真的有整个场景 ----
    try:
        gltf = read_glb_json(path)
    except Exception as exc:
        check("能解析 glb", False, str(exc))
        return 1

    node_names = [n.get("name", "") for n in gltf.get("nodes", [])]
    print(f"    glb 里的节点: {node_names}")
    for want in ("CharacterRoot", "Body", "Box", "KeyLight", "ShotCam"):
        check(f"glb 含对象 {want}", want in node_names, str(node_names))

    check("glb 有网格", len(gltf.get("meshes", [])) >= 2,
          str(len(gltf.get("meshes", []))))

    # 层级:Body 应该是 CharacterRoot 的子节点
    children = None
    for node in gltf.get("nodes", []):
        if node.get("name") == "CharacterRoot":
            children = node.get("children", [])
            break
    if children is None:
        check("CharacterRoot 有子节点", False, "找不到 CharacterRoot")
    else:
        child_names = [gltf["nodes"][i].get("name") for i in children]
        print(f"    CharacterRoot 的子节点: {child_names}")
        check("Body 挂在 CharacterRoot 下", "Body" in child_names, str(child_names))

    # ---- 3. 变换:Box 在 Blender 里是 (3, 0, 0.4),Y-up 转换后应是 (3, 0.4, 0) ----
    box_node = None
    for node in gltf.get("nodes", []):
        if node.get("name") == "Box":
            box_node = node
            break
    if box_node is None:
        check("能找到 Box 节点", False)
    else:
        translation = box_node.get("translation")
        print(f"    Box 在 glb 里的位移: {translation}（Blender 里是 (3, 0, 0.4)）")
        check("Box 位移被正确转换(Z-up -> Y-up)",
              translation is not None
              and abs(translation[0] - 3.0) < 0.01
              and abs(translation[1] - 0.4) < 0.01
              and abs(translation[2] - 0.0) < 0.01,
              str(translation))

    # ---- 4. 包装场景 ----
    try:
        world_scene, body_count = asset_export.write_world_scene(
            directory, "World", scene=bpy.context.scene, dimension="3")
    except Exception as exc:
        check("生成包装场景", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1
    check("生成包装场景", os.path.isfile(world_scene), str(world_scene))
    check("包装场景放在 scenes/",
          os.path.basename(os.path.dirname(world_scene)) == "scenes", world_scene)

    with open(world_scene, encoding="utf-8") as handle:
        text = handle.read()
    print("-" * 74)
    print(text)
    print("-" * 74)
    check("包装场景引用了 glb",
          'path="res://art/World.glb"' in text,
          [ln for ln in text.splitlines() if "glb" in ln][:1])

    # 架构说明:试过两种"在 .tscn 里挂材质"的写法,**都不生效**:
    #   1) 给 glb 实例的内部子节点写 material_override ->
    #      Godot 报 `Parent path './World' ... has vanished`,覆盖被丢弃
    #   2) 把网格展开成 MeshInstance3D + ExtResource glb 网格 ->
    #      运行时 mi.mesh 是 null,什么都画不出来
    # 现在用的是:instance 整个 glb(几何一定对)+ Environment 脚本运行时挂材质。
    check("包装场景 instance 了 glb",
          re.search(r'instance=ExtResource\("1_world"\)', text) is not None,
          [ln for ln in text.splitlines() if "1_world" in ln][:2])
    check("有根节点(根节点不能写 parent)",
          re.search(r'\[node name="\w+" instance=ExtResource', text) is not None,
          text.splitlines()[:6])
    check("材质映射交给 Environment 脚本",
          "materials = " in text and '[node name="Environment"' in text,
          [ln for ln in text.splitlines() if "materials" in ln][:2])

    # ---- 4a-2. 节点类型必须是合法的 Godot 类型 ----
    #
    # 回归:碰撞体的 type 曾经直接取 physics.body_type,而物体**没设过物理**
    # 时那个值是 "auto" —— 生成出 `type="auto"`,Godot 解析这个场景直接失败。
    # (实测在用户的 World.tscn 里出现过。)
    declared_types = re.findall(r'\[node name="[^"]+" type="([^"]+)"', text)
    print(f"    场景里声明的节点类型: {sorted(set(declared_types))}")
    bad_types = [t for t in declared_types
                 if t in ("auto", "", "None") or not t[0].isupper()]
    check("没有非法的节点类型(如 auto)", not bad_types, str(bad_types))

    # ---- 4b. 相机不能重复 ----
    #
    # 相机由 glb 带进来(声明式的 Camera3D 节点**不该**再生成 ——
    # 生成的话场景里就有两个相机,用户会说"多一个摄像头")。
    # Environment 脚本负责把它设成 current。
    camera_nodes = re.findall(r'\[node name="([^"]+)" type="Camera3D"', text)
    print(f"    场景里声明的 Camera3D 节点: {camera_nodes}")
    check("不另外生成 Camera3D 节点(避免两个相机)", not camera_nodes,
          str(camera_nodes))
    check("有 Environment 节点负责设主相机", '[node name="Environment"' in text)
    check("Environment 引用了脚本", 'ExtResource("env_script")' in text)
    env_script = os.path.join(directory, "scripts", "gk_environment.gd")
    check("Environment 脚本写出来了", os.path.isfile(env_script), env_script)
    if os.path.isfile(env_script):
        env_text = open(env_script, encoding="utf-8").read()
        check("脚本里写的是 Blender 的相机名",
              'camera_name: String = "ShotCam"' in env_text,
              [ln for ln in env_text.splitlines() if "camera_name" in ln][:1])
        # 灯光**不再由脚本修正**:用户决定在 Godot 里自己设计灯光,
        # glb 带进来的灯原样保留(按用户要求去掉了亮度换算)。
        check("脚本不再碰灯光",
              "light_energy" not in env_text and "sun_strength" not in env_text,
              [ln for ln in env_text.splitlines() if "light" in ln.lower()][:2])

    # ---- 4c. 相机朝向:导出后 Godot 里的前方向必须和 Blender 一致 ----
    #
    # 这是**不变量**,不是"矩阵该怎么写"的记忆题:
    #   Blender 相机前方向 = matrix_world 作用在 (0,0,-1)
    #   转成 Y-up 就是 (x, z, -y)
    #   Godot 里应当读到同样的方向。
    #
    # 为什么专门测这个:Transform3D 那 9 个数的顺序我搞错过,
    # 而且当时用了一个"看起来随机"的矩阵 (1,2,3,...) 去试 ——
    # 那个矩阵按行读和按列读得到的符号恰好一样,**没有区分力**,
    # 于是我拿着错误结论继续做,相机方向整体反了都看不出来。
    # 换成真实的不对称旋转后,两种读法立刻分得开。
    #
    # 走**真实导出**的 glb(不手写欧拉角->四元数,那会引入第二个可能出错的
    # 实现,测出来的东西就不算数了)。
    import math as _math

    for label, euler in (("默认朝前", (0.0, 0.0, 0.0)),
                         ("低头 72 度", (72.0, 0.0, 0.0)),
                         ("抬头 30 度", (-30.0, 0.0, 0.0)),
                         # 俯仰 + 偏航一起 —— **纯偏航没有区分力**:
                         # 绕前方向转不改变前方向,测了等于没测。
                         ("俯仰 20 偏航 35", (20.0, 0.0, 35.0))):
        camera.rotation_euler = tuple(_math.radians(v) for v in euler)
        bpy.context.view_layer.update()
        world = camera.matrix_world.to_3x3()
        from mathutils import Vector as _V
        blender_forward = world @ _V((0.0, 0.0, -1.0))
        expect = (blender_forward.x, blender_forward.z, -blender_forward.y)

        probe_dir = os.path.join(workdir, "camprobe_" + label.replace(" ", "_"))
        os.makedirs(probe_dir, exist_ok=True)
        _p, ok_probe, message = asset_export.export_scene_glb(
            probe_dir, "Cam", scene=scene)
        if not ok_probe:
            check(f"相机朝向({label})", False, f"导出失败: {message}")
            continue
        probe_payload = _read_glb_json(os.path.join(probe_dir, "art", "Cam.glb"))
        node = None
        for candidate in (probe_payload or {}).get("nodes", []):
            if candidate.get("name") == "ShotCam":
                node = candidate
        if node is None:
            check(f"相机朝向({label})", False, "glb 里没有 ShotCam")
            continue
        values = [float(v) for v in
                  asset_export._glb_node_matrix(probe_payload, node).split(",")]
        got = (-values[6], -values[7], -values[8])
        ok = all(abs(a - b) < 0.02 for a, b in zip(expect, got))
        print(f"    {label}: 期望 {tuple(round(v, 3) for v in expect)}"
              f" 实际 {tuple(round(v, 3) for v in got)}")
        check(f"相机朝向({label})", ok, f"{got} != {expect}")

    # 恢复成测试用的角度
    camera.rotation_euler = (1.30, 0.0, 0.0)
    bpy.context.view_layer.update()

    # ---- 5. 碰撞体(物体属性面板里设的)----
    print(f"    生成了 {body_count} 个碰撞体")
    check("生成了碰撞体", body_count > 0, str(body_count))
    check("有 CollisionShape3D 节点", "CollisionShape3D" in text,
          [ln for ln in text.splitlines() if "Collision" in ln][:3])
    check("有碰撞形状 sub_resource",
          any(k in text for k in ("BoxShape3D", "SphereShape3D",
                                  "CapsuleShape3D", "CylinderShape3D")),
          [ln for ln in text.splitlines() if "Shape3D" in ln][:3])
    # 碰撞体放在 Colliders 容器下,避免和 glb 里的同名节点撞名
    check("碰撞体挂在 Colliders 容器下", 'parent="Colliders"' in text,
          [ln for ln in text.splitlines() if '[node name="Body"' in ln][:2])
    check("有 Colliders 容器节点", '[node name="Colliders"' in text)

    # 碰撞体位置必须写出来 —— 不写就会全堆在原点,对不上模型
    check("碰撞体写了 transform", "transform = Transform3D(" in text,
          [ln for ln in text.splitlines() if "transform" in ln][:2])

    # 每个有碰撞体的物体,碰撞体位置必须和它在 Godot 里的**世界位置**一致 ——
    # 这是"碰撞体跟着模型走"的判据。
    #
    # 注意必须算世界坐标:Body 是 CharacterRoot 的子节点,它在 glb 里的
    # translation 是**局部**的 (0,-0.5,0),加上父节点的 (0,1.5,0)
    # 才是世界位置 (0,1.0,0)。碰撞体算的就是世界坐标,所以两边要按
    # 世界坐标比,否则会误判成"对不上"。
    nodes = gltf.get("nodes", [])

    def world_positions(index, parent=(0.0, 0.0, 0.0), out=None):
        if out is None:
            out = {}
        node = nodes[index]
        local = node.get("translation", [0.0, 0.0, 0.0])
        world = tuple(local[i] + parent[i] for i in range(3))
        out[node.get("name", "")] = [round(v, 3) for v in world]
        for child in node.get("children", []):
            world_positions(child, world, out)
        return out

    glb_world = {}
    for index in range(len(nodes)):
        glb_world.update(world_positions(index))
    print(f"    glb 里的世界位置: {glb_world}")

    body_positions = {}
    for match in re.finditer(
            r'\[node name="([^"]+)" type="\w*Body\w*" parent="Colliders"\]\s*\n'
            r'transform = Transform3D\([^)]*?([-\d.]+), ([-\d.]+), ([-\d.]+)\)',
            text):
        body_positions[match.group(1)] = [round(float(match.group(i)), 3)
                                          for i in (2, 3, 4)]
    print(f"    碰撞体位置: {body_positions}")

    mismatched = []
    for name, position in body_positions.items():
        expected = glb_world.get(name)
        if expected is None:
            continue
        if any(abs(a - b) > 0.01 for a, b in zip(position, expected)):
            mismatched.append(f"{name}: 碰撞体 {position} vs 模型世界位置 {expected}")
    check("碰撞体位置和模型一致(会跟着模型走)", not mismatched, str(mismatched))
    check("确实比对到了物体", len(body_positions) >= 1, str(body_positions))

    # Box 在 Blender 里是 (3, 0, 0.4) -> Godot 里应该是 (3, 0.4, 0)
    box_position = asset_export._godot_position(box, True)
    print(f"    Box 碰撞体位置: {tuple(round(v, 3) for v in box_position)}"
          f"（Blender 里是 (3, 0, 0.4)）")
    check("碰撞体位置做了 Z-up -> Y-up 转换",
          abs(box_position[0] - 3.0) < 0.01
          and abs(box_position[1] - 0.4) < 0.01
          and abs(box_position[2]) < 0.01,
          str(box_position))

    # ---- 5b. 自动形状推导 ----
    # 球体 -> 球;细长的 -> 胶囊;扁平的 -> 盒子
    guess_body = asset_export.guess_collision_shape(body, True)
    guess_box = asset_export.guess_collision_shape(box, True)
    print(f"    Body(球) 推出: {guess_body}   Box(立方) 推出: {guess_box}")
    check("球体推出球形状", guess_body == "sphere", guess_body)

    # 造一个细长的物体,应该推出胶囊
    tall_mesh = bpy.data.meshes.new("TallMesh")
    tall = bpy.data.objects.new("Tall", tall_mesh)
    scene.collection.objects.link(tall)
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=12,
                          radius1=0.25, radius2=0.25, depth=2.4)
    bm.to_mesh(tall_mesh)
    bm.free()
    guess_tall = asset_export.guess_collision_shape(tall, True)
    print(f"    Tall(细长) 推出: {guess_tall}")
    check("细长物体推出胶囊", guess_tall == "capsule", guess_tall)

    # ---- 5c. 手动指定形状会覆盖自动推导 ----
    tall.gk_physics.collision_shape = "box"
    built = asset_export.build_collision(tall, True, tall.gk_physics)
    check("手动指定盒子生效",
          built is not None and built["shape"] == "box",
          str(built["shape"] if built else None))
    check("盒子形状生成 BoxShape3D",
          built is not None and "BoxShape3D" in built["sub_resource"],
          built["sub_resource"] if built else "")

    # ---- 5d. 关掉碰撞就不生成 ----
    tall.gk_physics.collision_shape = "none"
    built_none = asset_export.build_collision(tall, True, tall.gk_physics)
    check("形状设为 none 时不生成碰撞体", built_none is None)

    # ---- 5e. 取消勾选『导出这个物体』就不生成 ----
    tall.gk_physics.collision_shape = "auto"
    tall.gk_physics.enabled = False
    # 场景名用 World —— 因为模型文件是 art/World.glb。
    # 写成 World2 的话会去找不存在的 art/World2.glb,函数直接提前返回,
    # 连主场景都不会设(实测踩过,一度以为是 set_main_scene 坏了)。
    _path2, count2 = asset_export.write_world_scene(
        directory, "World", scene=scene, dimension="3")
    enabled_count = sum(1 for _o, _p, en in asset_export.collect_physics(scene) if en)
    check("取消勾选的物体不算进碰撞体数量",
          count2 < enabled_count + 1, f"count={count2} enabled={enabled_count}")

    # ---- 6. 主场景指向它 ----
    #
    # "场景换代时主场景要跟着更新"这件事,单独用 set_main_scene 验证 ——
    # 因为 write_world_scene 要求 art/<名字>.glb 存在,而这里只有 World.glb,
    # 没法用它来模拟"从 World 换到 World2"。
    # (实测踩过:主场景一直停在第一次生成的那个,新场景里的相机一直没被
    #  看到,用户以为"相机没同步"。)
    main = project.main_scene(directory)
    print(f"    主场景: {main}")
    check("主场景指向生成的场景",
          main == "res://scenes/World.tscn", str(main))

    second = os.path.join(directory, "scenes", "Second.tscn")
    open(second, "w").close()
    project.set_main_scene(directory, "res://scenes/Second.tscn")
    check("插件自己设的主场景会跟着换代更新",
          project.main_scene(directory) == "res://scenes/Second.tscn",
          str(project.main_scene(directory)))

    # 用户手动在 Godot 里换主场景时不能被覆盖。
    #
    # 这里要**直接改 project.godot**,模拟"用户在 Godot 编辑器里选了另一个
    # 主场景" —— 关键在于 .gk_generated 标记**不会被更新**。
    # 如果图省事用 set_main_scene 去设,标记会跟着变,下次就分不清
    # "这是用户选的"还是"我们自己设的"了,测不出真正的行为。
    user_scene = os.path.join(directory, "scenes", "UserMade.tscn")
    os.makedirs(os.path.dirname(user_scene), exist_ok=True)
    open(user_scene, "w").close()
    project_file = os.path.join(directory, "project.godot")

    def point_main_scene_to(res_path):
        """直接改 project.godot(模拟用户在 Godot 里的操作)。"""
        body = open(project_file, encoding="utf-8").read()
        body = re.sub(r'^run/main_scene="[^"]*"',
                      f'run/main_scene="{res_path}"', body, count=1, flags=re.M)
        with open(project_file, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)

    point_main_scene_to("res://scenes/UserMade.tscn")
    project.set_main_scene(directory, "res://scenes/World.tscn")
    check("用户手动指定的主场景不被覆盖",
          project.main_scene(directory) == "res://scenes/UserMade.tscn",
          str(project.main_scene(directory)))

    # 反过来:指向的文件不存在时必须修,否则按 F5 会弹错
    point_main_scene_to("res://scenes/Gone.tscn")
    project.set_main_scene(directory, "res://scenes/World.tscn")
    check("指向死路径时会被修回来",
          project.main_scene(directory) == "res://scenes/World.tscn",
          str(project.main_scene(directory)))
    print(f"    输出目录: {directory}")
    addon_utils.disable(module_name, default_set=True)
    return 0


try:
    code = main()
except Exception:
    traceback.print_exc()
    code = 1

passed = sum(1 for _, ok, _ in RESULTS if ok)
failed = [name for name, ok, _ in RESULTS if not ok]
print("=" * 74)
print(f"结果: {passed}/{len(RESULTS)} 通过")
if failed:
    print("失败项: " + ", ".join(failed))
print("SCENE_EXPORT=" + ("OK" if code == 0 and not failed else "FAILED"))
print("=" * 74)

sys.exit(0 if (code == 0 and not failed) else 1)
