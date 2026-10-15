"""环境预设:套用要真写进去、"当前生效"要认得出来。

── 为什么要专门测(第 125 轮,用户第 8 条)──────────────────────────
用户原话:"环境预设里哪个选了但没标颜色,不知道现在生效的是哪个环境。"

真机一跑发现**两个**问题:
  ① `gk.env_preset_apply_builtin`(右键菜单 / 预设库面板点的那一档)以前是
     **空转** —— 它拿 `_ENV_PRESET_FIELDS`(那是**自定义预设** PropertyGroup
     的字段名 `p_background` / `p_bg_color` …)去查内置档位
     `ENV_PRESET_BUILTINS`(短名 `bg_mode` / `bg` / `amb_e` …),两边一个都
     对不上,循环全 `continue` —— 点菜单只改了 `env_enabled` 和
     `env_background`,环境光 / 雾 / 泛光 / 调色**一个都没写**。
     用户看到的就是"点了没反应"。
  ② 没有任何"当前是哪一档"的标记 —— 每一档都画着一样的对勾。

这两个都不是"文件生成了没"能测出来的,所以单独一个探针。

用法:
    blender -b --python tests/blender_env_presets.py
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
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isfile(os.path.join(root, "BlenderGameKit", "__init__.py")):
        sys.path.insert(0, root)
        return "BlenderGameKit"
    return None


def main():
    module_name = find_addon()
    if not check("找到插件", module_name, "未安装"):
        return 1
    if module_name == "BlenderGameKit":
        import_module(module_name).register()
    else:
        addon_utils.enable(module_name, default_set=True)

    ui = import_module(f"{module_name}.ui")
    from importlib import import_module as _imp
    common = _imp(f"{module_name}.nodes._common")
    presets = common.ENV_PRESET_BUILTINS

    scene = bpy.context.scene
    settings = scene.gk_settings
    check("场景有 gk_settings", settings is not None)
    if settings is None:
        return 1

    # ---- 1. 每一档都必须**真的**把参数写进去 --------------------------
    # 键名对照表覆盖了哪些字段,就检查哪些字段。
    num_pairs = ui._ENV_BUILTIN_NUM
    flag_pairs = ui._ENV_BUILTIN_FLAG
    color_pairs = ui._ENV_BUILTIN_COLOR
    untouched = []
    for name, values in presets.items():
        bpy.ops.gk.env_preset_apply_builtin(preset_name=name)
        for preset_key, engine_key in num_pairs:
            if preset_key not in values:
                continue
            if abs(float(getattr(settings, engine_key)) - float(values[preset_key])) > 1e-4:
                untouched.append(f"{name}.{preset_key}")
        for preset_key, engine_key in flag_pairs:
            if preset_key not in values:
                continue
            if bool(getattr(settings, engine_key)) != bool(values[preset_key]):
                untouched.append(f"{name}.{preset_key}")
        for preset_key, engine_key in color_pairs:
            if preset_key not in values:
                continue
            want = [float(v) for v in values[preset_key][:3]]
            have = [float(v) for v in getattr(settings, engine_key)[:3]]
            if any(abs(a - b) > 1e-4 for a, b in zip(want, have)):
                untouched.append(f"{name}.{preset_key}")
    check(f"{len(presets)} 档内置预设的每一项都真的写进了场景设置",
          not untouched, ", ".join(untouched[:12]))

    # 背景模式也要对:bg_mode=2 是天空,1 是纯色
    bpy.ops.gk.env_preset_apply_builtin(preset_name="明亮白天")
    check("bg_mode=2 的档位 -> 背景切到天空",
          str(settings.env_background) == "SKY", str(settings.env_background))
    bpy.ops.gk.env_preset_apply_builtin(preset_name="卡通日间")
    check("bg_mode=1 的档位 -> 背景切回纯色",
          str(settings.env_background) == "COLOR", str(settings.env_background))

    # ---- 2. 套完要能认出"当前是哪一档" --------------------------------
    wrong = []
    for name in presets:
        bpy.ops.gk.env_preset_apply_builtin(preset_name=name)
        detected = ui.active_env_preset_name(scene)
        if detected != name:
            wrong.append(f"{name} -> {detected!r}")
    check("每一档套完都能被 active_env_preset_name 认出来", not wrong,
          ", ".join(wrong))

    # ---- 3. 关掉环境 / 手动改值 -> 不能硬说还在用某一档 ---------------
    settings.env_enabled = False
    check("环境关着时不报任何档位",
          ui.active_env_preset_name(scene) == "",
          repr(ui.active_env_preset_name(scene)))
    bpy.ops.gk.env_preset_apply_builtin(preset_name="卡通日间")
    settings.env_ambient_energy = 0.99          # 手动调歪
    check("手动改了参数之后不再硬认某一档",
          ui.active_env_preset_name(scene) == "",
          repr(ui.active_env_preset_name(scene)))

    # ---- 4. 生效那一档要能被标出来 --------------------------------
    # 用户原话:"环境预设里哪个选了但没标颜色,不知道现在生效的是哪个环境。"
    #
    # ⚠ 这里**不**去 new 菜单 / 面板对象:Blender 的 `Menu` / `Panel` 子类
    #   不能直接构造(`bpy_struct.__new__(struct): expected a single argument`,
    #   真机抓到)。所以"标当前"这条规则抽成了 `ui.env_preset_label()`
    #   (`Menu.draw` / `Panel.draw` / 右键菜单三处共用它)—— 测它即可,
    #   规则对了三处就都对。
    bpy.ops.gk.env_preset_apply_builtin(preset_name="卡通日间")
    label, in_use = ui.env_preset_label("卡通日间", "卡通日间")
    check("生效那一档的文字带『当前』", in_use and "当前" in label, label)
    label, in_use = ui.env_preset_label("卡通黄昏", "卡通日间")
    check("没生效的档位不带『当前』", (not in_use) and "当前" not in label, label)
    # 没有任何一档生效(自定义)时,谁都不该被标
    label, in_use = ui.env_preset_label("卡通日间", "")
    check("环境是自定义时谁也不标『当前』",
          (not in_use) and "当前" not in label, label)
    if module_name == "BlenderGameKit":
        import_module(module_name).unregister()
    else:
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
print("ENV_PRESETS=" + ("OK" if code == 0 and not failed else "FAILED"))
print("=" * 72)

sys.exit(0 if (code == 0 and not failed) else 1)
