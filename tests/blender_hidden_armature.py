"""回归测试:隐藏的骨架也要被导出(蒙皮 + 骨骼动画不能丢)。

用户实测:"可以播放形态键动作,但骨骼动作播放不了"。

根因:他的 MMD 骨架在视口里是**隐藏的**(`hide_get() == True`),
而 Blender 的 glTF 导出器**会跳过被隐藏的物体** —— 于是:

    导出结果: nodes=1  skins=0  animations=1(只有形态键)
    网格属性: 连 JOINTS_0 / WEIGHTS_0 都没有

手动 `hide_set(False)` 后立刻变成:
    nodes=1058  skins=1  animations=2(形态键 + 骨骼)

这个脚本在**真 Blender** 里跑(要用到 glTF 导出器,离线 stub 测不了)。
用法:
    blender -b <某个有骨架的.blend> --python tests/blender_hidden_armature.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bpy  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    mark = "OK  " if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))


def main():
    from BlenderGameKit import asset_export as ae

    scene = bpy.context.scene
    armature = next((o for o in scene.objects if o.type == "ARMATURE"), None)
    mesh = None
    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        for mod in obj.modifiers:
            if mod.type == "ARMATURE" and mod.object is armature:
                mesh = obj
                break
        if mesh is not None:
            break

    if armature is None or mesh is None:
        print("这个 .blend 里没有『网格 + 骨架』的组合,跳过")
        print("HIDDEN_ARMATURE_RESULT=SKIP")
        return

    print(f"骨架 = {armature.name} | 网格 = {mesh.name}")

    # 关键:**把骨架藏起来**,再导出
    armature.hide_set(True)
    check("骨架已隐藏", armature.hide_get() is True)

    out = os.path.join(tempfile.gettempdir(), "gk_hidden_arm_test")
    os.makedirs(out, exist_ok=True)
    path, ok, msg = ae.export_subtree_glb(out, "HiddenArm", [mesh], scene=scene)
    check("导出成功", ok, msg)
    if not ok:
        print("HIDDEN_ARMATURE_RESULT=FAIL")
        return

    # 读 glb 的 JSON 块
    import json
    import struct

    with open(path, "rb") as handle:
        data = handle.read()
    _magic, _version, length = struct.unpack("<III", data[:12])
    offset = 12
    gltf = None
    while offset < length:
        chunk_len, chunk_type = struct.unpack("<II", data[offset:offset + 8])
        body = data[offset + 8: offset + 8 + chunk_len]
        if chunk_type == 0x4E4F534A:
            gltf = json.loads(body.decode("utf-8"))
        offset += 8 + chunk_len

    skin_count = len(gltf.get("skins", []))
    anim_names = [a.get("name") for a in gltf.get("animations", [])]
    has_joints = any(
        "JOINTS_0" in prim.get("attributes", {})
        for mesh_def in gltf.get("meshes", [])
        for prim in mesh_def.get("primitives", []))

    check("有蒙皮(skins >= 1)", skin_count >= 1, f"skins={skin_count}")
    check("网格带 JOINTS_0", has_joints)
    check("导出了动画", len(anim_names) >= 1, f"动画={anim_names}")

    # 隐藏要恢复回去
    check("导出后骨架仍是隐藏的", armature.hide_get() is True)

    # 骨骼动画在不在?(名字里带"骨骼"或通道数很大)
    bone_anim = [a for a in gltf.get("animations", [])
                 if len(a.get("channels", [])) > 10]
    check("有骨骼动画(通道数 > 10)", bool(bone_anim),
          f"{[a.get('name') for a in bone_anim]}")

    failed = [n for n, ok, _d in RESULTS if not ok]
    if failed:
        print(f"失败项: {failed}")
        print("HIDDEN_ARMATURE_RESULT=FAIL")
    else:
        print(f"全部通过({len(RESULTS)} 项)")
        print("HIDDEN_ARMATURE_RESULT=OK")


main()
