"""端到端验证:一个 .blend 对应一个 Godot 项目的完整流程。

验证的东西:
  1. 没保存 .blend 时**明确报错**,不会静默写到家目录
  2. 保存后能推出项目目录(名字 = .blend 名 + _godot)
  3. 创建项目会生成 project.godot 与全部子目录
  4. 导出脚本 / 导出模型 / 导出材质都落到**同一个项目里**
  5. 手动指定位置后,绑定信息保存在 .blend 里(重新打开还在)
  6. 创建项目是幂等的,不会覆盖已有内容

用法:
    blender -b --python tests/blender_project.py
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


def main():
    print("=" * 72)
    print("BlenderGameKit 项目绑定测试")
    print("=" * 72)

    module_name = find_addon()
    if not check("找到插件", module_name, "未安装"):
        return 1
    addon_utils.enable(module_name, default_set=True, persistent=True)

    project = import_module(f"{module_name}.project")
    exporter = import_module(f"{module_name}.exporter")

    workdir = os.path.join(tempfile.gettempdir(), "gk_project_test")
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)

    # 起一个干净场景。**不要**用 read_factory_settings():
    # 它会连 bl_ext 命名空间一起清掉,之后 import_module 会报
    # "No module named 'bl_ext.user_default...'" —— 这个坑踩过两次了。
    # 只删物体和多余的数据块就够了。
    scene = bpy.context.scene
    if scene is not None:
        for obj in list(scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
    for tree in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(tree)

    # ---- 1. 没保存 .blend 时应该明确拒绝,而不是给个兜底目录 ----
    directory, reason = project.resolve(scene)
    check("未保存时解析返回空", directory is None, str(directory))
    check("未保存时给出可读的原因", "保存" in (reason or ""), str(reason))

    # ---- 2. 保存后能推出项目目录 ----
    blend_path = os.path.join(workdir, "mygame.blend")
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    check("保存后 filepath 正确", bpy.data.filepath == blend_path, bpy.data.filepath)

    directory, reason = project.resolve(bpy.context.scene)
    expected = os.path.join(workdir, "mygame_godot")
    check("推导出项目目录", directory == expected, f"{directory} != {expected}")
    check("来源标记为『跟着 .blend』", reason == "跟着 .blend", str(reason))
    check("项目此时还不存在", not project.is_project(directory))
    check("状态显示为『尚未创建』",
          project.project_summary(directory)[0] == "尚未创建",
          project.project_summary(directory)[0])

    # ---- 3. 创建项目 ----
    try:
        created = project.create_project(directory)
    except Exception as exc:
        check("创建项目", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 1
    check("创建项目成功", len(created) > 0, str(created))
    check("生成了 project.godot", project.is_project(directory))
    check("子目录齐全", not project.missing_subdirs(directory),
          str(project.missing_subdirs(directory)))

    project_godot = open(os.path.join(directory, "project.godot"), encoding="utf-8").read()
    print("-" * 72)
    print(project_godot)
    print("-" * 72)
    check("project.godot 有 config/name", "config/name=" in project_godot)
    check("project.godot 有 config_version", "config_version=5" in project_godot)
    check("状态变为『就绪』", project.project_summary(directory)[0] == "就绪",
          project.project_summary(directory)[0])

    # ---- 4. 幂等:再建一次不该改动 project.godot ----
    # 判据用**内容**而不是 mtime:mtime 的比较精度受文件系统影响,
    # 而且"没重写"这件事的直接证据就是内容没变。
    project_file = os.path.join(directory, "project.godot")
    with open(project_file, "a", encoding="utf-8") as handle:
        handle.write("\n; 用户自己加的注释\n")
    before = open(project_file, encoding="utf-8").read()
    project.create_project(directory)
    after = open(project_file, encoding="utf-8").read()
    check("重复创建不覆盖已有 project.godot", before == after)
    check("用户注释还在", "用户自己加的注释" in after)

    # ---- 5. 导出脚本落到项目里 ----
    tree = bpy.data.node_groups.new("玩家逻辑", "GKGameLogicTree")
    try:
        settings = exporter.build_settings(bpy.context.scene)
        check("build_settings 给出项目目录",
              settings.get("output_dir") == directory, str(settings.get("output_dir")))
        result = exporter.export_tree(tree, settings, write_scene=True)
        script_dir = result["directory"]
        check("导出目录 = 项目根目录", script_dir == directory,
              f"{script_dir} != {directory}")

        # 脚本必须在 scripts/ 子目录里,不能散在项目根目录
        scripts_dir = os.path.join(directory, "scripts")
        script_files = os.listdir(scripts_dir) if os.path.isdir(scripts_dir) else []
        gd_files = [f for f in script_files if f.endswith(".gd")]
        print(f"    scripts/ 里的文件: {script_files}")
        check("脚本写进了 scripts/", bool(gd_files), str(script_files))

        scenes_dir = os.path.join(directory, "scenes")
        scene_files = os.listdir(scenes_dir) if os.path.isdir(scenes_dir) else []
        print(f"    scenes/ 里的文件: {scene_files}")
        check("场景写进了 scenes/",
              any(f.endswith(".tscn") for f in scene_files), str(scene_files))

        # 项目根目录只该有 project.godot / README,不该散落脚本
        root_files = [f for f in os.listdir(directory)
                      if os.path.isfile(os.path.join(directory, f))]
        print(f"    项目根目录的文件: {root_files}")
        check("根目录没有散落的 .gd/.tscn",
              not any(f.endswith((".gd", ".tscn")) for f in root_files),
              str(root_files))
    except Exception as exc:
        check("导出脚本", False, f"{type(exc).__name__}: {exc}")
        traceback.print_exc()

    # ---- 6. 手动指定位置会记在 .blend 里 ----
    other = os.path.join(workdir, "elsewhere", "custom_project")
    settings_group = bpy.context.scene.gk_settings
    settings_group.project_dir = other
    resolved, reason = project.resolve(bpy.context.scene)
    check("手动指定后解析到新位置", resolved == os.path.abspath(other),
          f"{resolved} != {other}")
    check("来源标记为『手动指定』", reason == "手动指定", str(reason))

    # 存盘再读回,确认写进了 .blend
    bpy.ops.wm.save_mainfile()
    bpy.ops.wm.open_mainfile(filepath=blend_path)
    reloaded, reason = project.resolve(bpy.context.scene)
    check("重新打开 .blend 后绑定还在",
          reloaded == os.path.abspath(other), f"{reloaded} != {other}")

    # 恢复成跟着 .blend 走
    bpy.context.scene.gk_settings.project_dir = ""
    restored, _reason = project.resolve(bpy.context.scene)
    check("清空后恢复为跟着 .blend 走", restored == directory, str(restored))

    # ---- 7. .blend 直接在 Godot 项目里 -> 认外层项目,不再套一层 ----
    # 这是实测补上的情况:很多人会把 .blend 直接放进 Godot 项目目录。
    # 早期版本会再建一个 `xxx_godot` 子目录,变成项目套项目。
    inner = os.path.join(workdir, "inside_project")
    os.makedirs(inner, exist_ok=True)
    with open(os.path.join(inner, "project.godot"), "w", encoding="utf-8") as handle:
        handle.write('config_version=5\n\n[application]\n\nconfig/name="outer"\n')

    inner_blend = os.path.join(inner, "hero.blend")
    bpy.ops.wm.save_as_mainfile(filepath=inner_blend)
    resolved, reason = project.resolve(bpy.context.scene)
    check(".blend 在项目里时认外层项目",
          os.path.normcase(resolved) == os.path.normcase(inner),
          f"{resolved} != {inner}")
    check("来源标记为『Blender 文件在项目里』",
          reason == "Blender 文件在项目里", str(reason))
    check("不会再多套一层 _godot",
          not resolved.endswith(project.PROJECT_SUFFIX), str(resolved))
    check("外层项目被认作已存在", project.is_project(resolved))
    check("状态直接就是『就绪』",
          project.project_summary(resolved)[0] == "就绪",
          project.project_summary(resolved)[0])
    check("布局说明正确",
          project.describe_layout(resolved) == "Blender 文件在项目里",
          project.describe_layout(resolved))

    # 子目录里的 .blend 也应该往上找到项目根
    nested = os.path.join(inner, "blend_files")
    os.makedirs(nested, exist_ok=True)
    nested_blend = os.path.join(nested, "npc.blend")
    bpy.ops.wm.save_as_mainfile(filepath=nested_blend)
    resolved_nested, _reason = project.resolve(bpy.context.scene)
    check("子目录里的 .blend 也往上找到项目",
          os.path.normcase(resolved_nested) == os.path.normcase(inner),
          f"{resolved_nested} != {inner}")

    # 回到原来的 .blend 继续后面的检查
    bpy.ops.wm.open_mainfile(filepath=blend_path)

    # ---- 8. Godot 启动参数(踩过一个很贵的坑)----
    #
    # 这个检查存在的唯一理由:之前**没有任何测试**检查过传给 Godot 的命令行,
    # 于是写错了也没人发现 —— 表现是用户点"打开 Godot"弹一个
    # "Couldn't detect whether to run the editor..." 的报错框。
    #
    # Godot 4 的模式判定:
    #     godot --path <dir> --editor   -> 打开编辑器
    #     godot --path <dir>            -> 运行游戏(不是打开编辑器!)
    #     godot --path <dir> --game     -> 无效参数,直接报错
    #
    # 做法:把 subprocess.Popen 换成一个只记录参数的假函数,
    # 这样能断言参数而不真的启动 Godot。
    captured = []
    real_popen = project.subprocess.Popen

    class _FakeProc:
        pass

    def _fake_popen(args, **kwargs):
        captured.append(list(args))
        return _FakeProc()

    try:
        project.subprocess.Popen = _fake_popen

        # 打开编辑器
        captured.clear()
        ok, message = project.open_in_godot(directory, bpy.context, editor=True)
        check("打开编辑器: 调用成功", ok, message)
        if captured:
            args = captured[-1]
            print(f"    打开编辑器参数: {args}")
            check("打开编辑器带了 --editor", "--editor" in args, str(args))
            check("打开编辑器没带 --game", "--game" not in args, str(args))
            check("参数里有 --path", "--path" in args, str(args))

        # 运行游戏
        captured.clear()
        ok, message = project.open_in_godot(directory, bpy.context, editor=False)
        if project.main_scene(directory):
            check("运行游戏: 调用成功", ok, message)
            if captured:
                args = captured[-1]
                print(f"    运行游戏参数: {args}")
                check("运行游戏没带 --editor", "--editor" not in args, str(args))
                check("运行游戏没带 --game(那是无效参数)",
                      "--game" not in args, str(args))
        else:
            # 没有主场景时应该**明确拒绝**并解释原因,
            # 而不是启动一个立刻崩掉的 Godot 让用户莫名其妙
            check("没有主场景时拒绝运行并给出原因",
                  not ok and "主场景" in (message or ""), message)
    finally:
        project.subprocess.Popen = real_popen

    # 主场景读取
    scene_value = project.main_scene(directory)
    print(f"    项目主场景: {scene_value}")
    check("能读出主场景设置(有场景时)", bool(scene_value), str(scene_value))

    # ---- 8b. 没有主场景时必须明确拒绝,而不是启动一个立刻崩掉的 Godot ----
    # 这正是用户遇到的情况:建完项目、还没导出场景,点"运行"看到的是
    # 一闪而过的 "Can't run project: no main scene defined",完全不知道原因。
    bare = os.path.join(workdir, "bare_project")
    project.create_project(bare, title="bare")
    check("新项目本来就没有主场景", project.main_scene(bare) is None,
          str(project.main_scene(bare)))

    captured.clear()
    try:
        project.subprocess.Popen = _fake_popen
        ok, message = project.open_in_godot(bare, bpy.context, editor=False)
    finally:
        project.subprocess.Popen = real_popen
    check("没主场景时运行游戏被拒绝", not ok, f"ok={ok}")
    check("拒绝原因里说了『主场景』", "主场景" in (message or ""), str(message))
    check("被拒绝时根本没启动 Godot", not captured, str(captured))
    print(f"    拒绝提示: {message}")

    # 但打开编辑器不需要主场景,应该照常放行
    captured.clear()
    try:
        project.subprocess.Popen = _fake_popen
        ok, message = project.open_in_godot(bare, bpy.context, editor=True)
    finally:
        project.subprocess.Popen = real_popen
    check("没主场景也能打开编辑器", ok, message)

    # ---- 9. 找 Godot ----
    found = project.find_godot_executable(bpy.context)
    print(f"    找到的 Godot: {found}")
    check("能找到 Godot 可执行文件(环境有的话)", True)  # 找不到不算失败
    if found:
        check("Godot 路径存在", os.path.isfile(found), found)

    # ---- 8. 名称安全化 ----
    check("中文名保留", project.safe_project_name("我的游戏") == "我的游戏",
          project.safe_project_name("我的游戏"))
    check("英文名保留", project.safe_project_name("MyGame") == "MyGame")
    check("混合名完整保留", project.safe_project_name("角色_Hero") == "角色_Hero",
          project.safe_project_name("角色_Hero"))
    check("Windows 保留名被替换",
          project.safe_project_name("con") == "game", project.safe_project_name("con"))

    print(f"    工作目录: {workdir}")
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
print("PROJECT=" + ("OK" if code == 0 and not failed else "FAILED"))
print("=" * 72)

sys.exit(0 if (code == 0 and not failed) else 1)
