"""回归测试:隐藏骨架导致蒙皮丢失,且必须**明确告警 + 不动用户隐藏**。

用户实测:"可以播放形态键动作,但骨骼动作播放不了"。
根因是他的 MMD 骨架在视口里是隐藏的,而 **Blender 的 glTF 导出器
会跳过隐藏的物体** —— 导出结果 `skins: 0`,骨骼动画整个丢失。

试过 `hide_viewport`(显示器图标)也一样被跳过,所以**没有**
"在 Blender 里藏起来还能导出骨架"的办法。

用户明确要求不要擅自改他的隐藏状态,所以现在的行为是:

    检测到要导出的骨架是隐藏的 -> 明确告警(告诉他后果和做法)
    不擅自改隐藏(导完还是隐藏的)

要在**真 Blender** 里跑(需要真实 glTF 导出器):
    blender -b <有骨架的.blend> --python tests/blender_hidden_armature.py
"""

import json
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bpy  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'OK  ' if ok else 'FAIL'}] {name}"
          + (f"  ({detail})" if detail else ""))


def read_glb(path):
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
    joints = any("JOINTS_0" in prim.get("attributes", {})
                 for mesh_def in gltf.get("meshes", [])
                 for prim in mesh_def.get("primitives", []))
    return {
        "nodes": len(gltf.get("nodes", [])),
        "skins": len(gltf.get("skins", [])),
        "anims": [a.get("name") for a in gltf.get("animations", [])],
        "joints": joints,
    }


def main():
    from BlenderGameKit import asset_export as ae

    scene = bpy.context.scene
    armature = next((o for o in scene.objects if o.type == "ARMATURE"), None)
    mesh = None
    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        if any(m.type == "ARMATURE" and m.object is armature
               for m in obj.modifiers):
            mesh = obj
            break

    if armature is None or mesh is None:
        print("这个 .blend 里没有『网格 + 骨架』的组合,跳过")
        print("HIDDEN_ARMATURE_RESULT=SKIP")
        return

    print(f"骨架 = {armature.name} | 网格 = {mesh.name}")
    out = os.path.join(tempfile.gettempdir(), "gk_hidden_arm_test")
    os.makedirs(out, exist_ok=True)

    # ── 骨架显示 -> 正常导出(这是"应该能播"的基准)───────────────
    armature.hide_set(False)
    if hasattr(ae._run_gltf_export, "_notes"):
        ae._run_gltf_export._notes = []
    path, ok, msg = ae.export_subtree_glb(out, "ShownArm", [mesh], scene=scene)
    check("骨架显示时:导出成功", ok, msg)
    good = read_glb(path)
    check("骨架显示时:有蒙皮", good["skins"] >= 1, f"skins={good['skins']}")
    check("骨架显示时:网格带 JOINTS_0", good["joints"])
    check("骨架显示时:骨骼动画导出了", len(good["anims"]) >= 2,
          f"{good['anims']}")
    check("骨架显示时:**没有**隐藏告警",
          not any("隐藏" in n
                  for n in (getattr(ae._run_gltf_export, "_notes", []) or [])))

    # ── 骨架隐藏 -> 尊重隐藏,但必须告警 ───────────────────────────
    armature.hide_set(True)
    if hasattr(ae._run_gltf_export, "_notes"):
        ae._run_gltf_export._notes = []
    path2, ok2, msg2 = ae.export_subtree_glb(out, "HiddenArm", [mesh],
                                             scene=scene)
    check("骨架隐藏时:导出仍然成功", ok2, msg2)
    bad = read_glb(path2)
    check("骨架隐藏时:骨架确实没导出去", bad["skins"] == 0,
          f"skins={bad['skins']}")
    notes = getattr(ae._run_gltf_export, "_notes", []) or []
    warning = " ".join(notes)
    check("骨架隐藏时:给出了明确告警",
          ("隐藏" in warning and "骨架" in warning), warning[:50] + "...")
    check("骨架隐藏时:告警里给了做法",
          "显示出来" in warning or "强制" in warning)
    check("骨架隐藏时:**没擅自改**用户的隐藏", armature.hide_get() is True)

    failed = [n for n, ok, _d in RESULTS if not ok]
    if failed:
        print(f"失败项: {failed}")
        print("HIDDEN_ARMATURE_RESULT=FAIL")
    else:
        print(f"全部通过({len(RESULTS)} 项)")
        print("HIDDEN_ARMATURE_RESULT=OK")


main()
