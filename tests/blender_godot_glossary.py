"""Godot 中文对照表。

为什么专门测:Godot 界面上有一批英文**改不了**(节点类型名是引擎类名,
检查器的分组标题中文包没覆盖)。既然改不了,给一份能查的表就是唯一的办法 ——
所以这份表必须真的存在、真的被写出来,而且关键条目一个都不能少。

另外还要钉住一条实测结论:**不能给 Godot 加 `-l zh_CN`**。
加了界面反而变英文。这条坑要写进对照表里,免得以后忘掉。

用法:
    blender -b --python tests/blender_godot_glossary.py
"""

import os
import shutil
import sys
import tempfile
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


# 这些词是用户截图里出现过的 / 我们这个流程一定会碰到的。
# 少一个用户就查不到,所以逐个钉住。
MUST_HAVE = (
    # 节点类型(添加节点对话框里的)
    "MeshInstance3D", "StaticBody3D", "RigidBody3D", "CharacterBody3D",
    "Area3D", "CollisionShape3D", "Camera3D", "DirectionalLight3D",
    "WorldEnvironment",
    # 检查器分组(用户截图右侧那一列)
    "Surface Material Override", "Global Illumination", "Visibility Range",
    "Transform", "Process", "Script", "Metadata",
    # 碰撞
    "Collision Layer", "Collision Mask",
    # 灯光
    "Light Energy", "Light Color",
    # 中英对照确实成对出现了
    "网格实例", "静态碰撞体", "触发区", "变换", "光照强度",
)


def main():
    module_name = find_addon()
    if not check("找到插件", module_name, "未安装"):
        return 1
    addon_utils.enable(module_name, default_set=True, persistent=True)
    glossary = import_module(f"{module_name}.godot_glossary")

    workdir = os.path.join(tempfile.gettempdir(), "gk_glossary")
    shutil.rmtree(workdir, ignore_errors=True)

    # ---- 1. 写出来 ----
    print("=" * 72)
    print("1. 生成对照表")
    print("=" * 72)
    path, ok, message = glossary.write(workdir)
    check("写入成功", ok, message)
    check("文件真的在", path is not None and os.path.isfile(path), str(path))
    if not (path and os.path.isfile(path)):
        addon_utils.disable(module_name, default_set=True)
        return 1

    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    print(f"    {path}")
    print(f"    {len(text)} 字符 / {text.count(chr(10)) + 1} 行")

    check("内容够长(不是占位符)", len(text) > 1500, f"{len(text)} 字符")
    check("文件名带中文,一眼能认出来",
          "中文" in os.path.basename(path), os.path.basename(path))

    # ---- 2. 关键条目一个都不能少 ----
    print("=" * 72)
    print("2. 关键条目")
    print("=" * 72)
    missing = [word for word in MUST_HAVE if word not in text]
    print(f"    检查了 {len(MUST_HAVE)} 个词")
    check("该有的词都有", not missing, f"缺: {missing}")

    # ---- 3. 那张"哪些能翻哪些不能翻"的表要说清楚 ----
    print("=" * 72)
    print("3. 讲清楚了「为什么还有英文」")
    print("=" * 72)
    check("说明了节点类名不翻译",
          "引擎类名" in text or "引擎里的类名" in text)
    check("说明了检查器分组没被中文包覆盖",
          "中文包" in text or "语言包" in text)
    check("说明了编辑器本身已经是中文",
          "已经是中文" in text or "已经汉化" in text)

    # ---- 4. 那条踩过的坑必须写进去 ----
    #
    # 实测:`godot -l zh_CN` 会把界面**变成英文**(参数要两位语言码,
    # zh_CN 被当无效值回退)。这条不写下来,以后一定会有人再试一次。
    print("=" * 72)
    print("4. 记住 -l zh_CN 是坑")
    print("=" * 72)
    check("提到了 -l 参数", "-l" in text)
    check("明确说了不要用它", "不要" in text or "别加" in text)
    check("解释了原因(两位语言码/无效值)",
          "两位" in text or "无效值" in text)

    # ---- 5. find() 能用 ----
    print("=" * 72)
    print("5. find() 找得到")
    print("=" * 72)
    check("find 返回路径", glossary.find(workdir) == path, str(glossary.find(workdir)))
    check("没有的目录返回 None",
          glossary.find(os.path.join(workdir, "不存在")) is None)

    # ---- 6. 重复写不会出问题 ----
    print("=" * 72)
    print("6. 重复生成")
    print("=" * 72)
    path2, ok2, _ = glossary.write(workdir)
    check("再写一次还是成功", ok2 and path2 == path, str(path2))
    with open(path2, encoding="utf-8") as handle:
        text2 = handle.read()
    check("内容一致(是覆盖,不是追加)", text2 == text,
          f"{len(text2)} vs {len(text)}")

    # ---- 7. 写不进去时要有明确报错 ----
    print("=" * 72)
    print("7. 异常路径")
    print("=" * 72)
    _p, ok3, msg3 = glossary.write("")
    check("空目录不崩,给错误信息", ok3 is False and bool(msg3), msg3)

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
print("GLOSSARY=" + ("OK" if code == 0 and not failed else "FAILED"))
print("=" * 72)

sys.exit(0 if (code == 0 and not failed) else 1)
