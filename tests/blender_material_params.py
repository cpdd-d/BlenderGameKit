"""材质参数的两个用户报告 bug:

1. 中文界面下,材质『自定义属性』里显示的是英文键名(albedo_color 之类)
   -> 现在写 _RNA_UI,显示中文名
2. 点『从场景灯光取角度』会把用户调好的基础色/贴图重置
   -> 现在重建材质前先把节点里的当前值读出来(sRGB <-> 线性要转对)

用法:
    blender -b --python tests/blender_material_params.py
"""

import os
import sys
import traceback
from importlib import import_module

import addon_utils
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


def main():
    module_name = find_addon()
    if not check("找到插件", module_name, "未安装"):
        return 1
    addon_utils.enable(module_name, default_set=True, persistent=True)
    npr = import_module(f"{module_name}.npr_preview")

    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)

    image = bpy.data.images.new("MyTex", 32, 32)
    material = npr.build_toon_material("TestMat", base_texture=image)

    # ---- 1. 自定义属性的键名是中文 ----
    #
    # 自定义属性面板里显示的就是**键名**。键叫 albedo_color,面板里就是
    # albedo_color —— 中文界面的用户完全看不懂哪个是哪个。
    #
    # 试过用 _RNA_UI 给键设显示名(Blender 的老办法):数据能写能读,
    # 但**面板不采纳**(实测 Blender 4.5,颜色这种数组属性尤其不生效)。
    # 所以直接把键名做成中文 —— 实测可读写、可存盘、可导出。
    print("=" * 72)
    print("1. 自定义属性的键名")
    print("=" * 72)
    keys = [k for k in material.keys() if not k.startswith("_")]
    ascii_keys = [k for k in keys if k.isascii()]
    print(f"    键名({len(keys)} 个): {keys[:6]} ...")
    check("参数全部用中文键名", not ascii_keys, str(ascii_keys))
    check("键的数量对得上参数表",
          len(keys) >= len(npr.PARAM_MAP), f"{len(keys)} < {len(npr.PARAM_MAP)}")
    for name, _u, _k, _d, label in npr.PARAM_MAP[:5]:
        check(f"{name} 的键名是『{label}』",
              label in keys, f"没有 {label}")

    # ---- 1b. 读写得对得上 ----
    params = npr.read_params(material)
    check("read_params 能读出所有参数",
          len(params) == len(npr.PARAM_MAP), str(len(params)))
    check("能读到『明暗分界』的值",
          abs(float(npr.get_param(material, "shadow_step")) - 0.1) < 1e-6,
          str(npr.get_param(material, "shadow_step")))

    # ---- 1c. 旧材质(只有英文键)能迁移 ----
    print("=" * 72)
    print("2. 旧材质的英文键迁移")
    print("=" * 72)
    old = bpy.data.materials.new("旧材质")
    old["albedo_color"] = [0.9, 0.4, 0.4]
    old["face_sdf_mix"] = 0.33
    old["shadow_step"] = 0.25
    print(f"    迁移前: {list(old.keys())}")
    moved = npr.migrate_keys(old)
    print(f"    迁移后: {list(old.keys())}（迁移了 {moved} 个）")
    check("旧键被迁移成中文键", moved == 3, str(moved))
    check("迁移后英文键不在了",
          not any(k in ("albedo_color", "face_sdf_mix", "shadow_step")
                  for k in old.keys()), str(list(old.keys())))
    check("迁移后值没丢",
          abs(float(npr.get_param(old, "face_sdf_mix")) - 0.33) < 1e-6,
          str(npr.get_param(old, "face_sdf_mix")))

    # ---- 1d. 还没迁移的材质也要能读(向后兼容) ----
    unmigrated = bpy.data.materials.new("未迁移")
    unmigrated["shadow_step"] = 0.12
    check("get_param 认得英文键",
          abs(npr.get_param(unmigrated, "shadow_step") - 0.12) < 1e-6,
          str(npr.get_param(unmigrated, "shadow_step")))
    check("has_param 认得英文键", npr.has_param(unmigrated, "shadow_step"))

    # ---- 2. 取角度不重置用户的颜色和贴图 ----
    print("=" * 72)
    print("2. 取角度不重置材质")
    print("=" * 72)
    # 模拟用户直接在着色器编辑器里改基础色
    for node in material.node_tree.nodes:
        if node.bl_idname == "ShaderNodeRGB":
            node.outputs[0].default_value = (0.8, 0.1, 0.05, 1.0)
            break
    before = npr.current_base_color(material)
    print(f"    用户改的颜色(节点, sRGB) = "
          f"{[round(v, 3) for v in before]}")
    check("能读到节点里的当前颜色", before is not None)

    params = npr.read_params(material)
    params["face_sdf_angle"] = 0.42
    npr.update_material(material, params)
    after = npr.current_base_color(material)
    print(f"    取角度之后的颜色         = {[round(v, 3) for v in after]}")
    check("基础色被保留(没被重置)",
          all(abs(a - b) < 0.02 for a, b in zip(before, after)),
          f"{before} -> {after}")
    check("角度确实写进去了",
          abs(float(npr.get_param(material, "face_sdf_angle")) - 0.42) < 1e-6,
          str(npr.get_param(material, "face_sdf_angle")))

    texture = npr.current_base_image(material)
    print(f"    贴图 = {texture.name if texture else None}")
    check("基础色贴图还在", texture is not None and texture.name == "MyTex",
          str(texture))

    # ---- 3. 颜色往返不能改变数值 ----
    #
    # 这里是最容易出错的地方:节点存**线性**、属性存 **sRGB**。
    # 不转换的话每次重建都会"洗白"一次,用户看到的就是"材质被重置"。
    # (实测:用户设 (0.8,0.1,0.05),不转换会变成 (1.0,0.852,0.811)。)
    print("=" * 72)
    print("3. 连续重建 5 次,颜色不应该漂移")
    print("=" * 72)
    reference = npr.current_base_color(material)
    drift = None
    for _ in range(5):
        npr.update_material(material, {})
    drift = npr.current_base_color(material)
    print(f"    初始 {[round(v, 4) for v in reference]}")
    print(f"    5 次之后 {[round(v, 4) for v in drift]}")
    check("颜色不因反复重建而漂移",
          all(abs(a - b) < 0.005 for a, b in zip(reference, drift)),
          f"{reference} -> {drift}")

    addon_utils.disable(module_name, default_set=True)
    return 0


try:
    code = main()
except Exception:
    traceback.print_exc()
    code = 1

passed = sum(1 for _, ok, _ in RESULTS if ok)
failed = [name for name, ok, _ in RESULTS if not ok]
print("=" * 72)
print(f"结果: {passed}/{len(RESULTS)} 通过")
if failed:
    print("失败项: " + ", ".join(failed))
print("MATERIAL_PARAMS=" + ("OK" if code == 0 and not failed else "FAILED"))
print("=" * 72)

sys.exit(0 if (code == 0 and not failed) else 1)
