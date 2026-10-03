"""导出:把节点树生成 GDScript 并写到磁盘，顺带准备 Godot 工程骨架。"""

import os
import re
import time

from . import codegen, gk_log, project
from .nodes.node_base import snake_case

GDScriptVersion = 4

# 兜底目录:只在既没有 .blend 路径、也没有手动指定时用。
# 正常情况下不该走到这里 —— 场景目录应该由 project.resolve() 给出。
DEFAULT_OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "BlenderGameKit_Export")

# 场景文件里引用脚本的写法
_EXTENDS_TEMPLATE = """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="{script_name}" id="1_script"]

[node name="{node_name}" type="{base_class}"]
script = ExtResource("1_script")
"""


def safe_file_name(text, fallback="player"):
    """把节点树名字转成安全的文件名(中文会被压成下划线，那就用回退名)。"""
    name = snake_case(text or "", "")
    if not name or len(name.strip("_")) < 2:
        return fallback
    name = re.sub(r"[^A-Za-z0-9_\-]", "", name)
    return name or fallback


def build_settings(scene):
    """从 Blender 场景属性里读出导出设置。

    导出目录的优先级(见 project.resolve):
      1. 场景属性 output_dir(老的手动指定,保留兼容)
      2. 由 .blend 位置推导出来的项目目录 —— 这是推荐用法
    两者都推不出来时会返回空串,由调用方提示用户先保存 .blend。
    """
    settings = default_settings()
    if scene is None:
        return settings
    props = getattr(scene, "gk_settings", None)
    if props is None:
        return settings

    settings.update({
        "dimension": getattr(props, "dimension", "3"),
        "base_class": resolve_base_class(scene),
        "class_name": getattr(props, "class_name", ""),
        "export_tunables": bool(getattr(props, "export_tunables", True)),
        "write_scene": bool(getattr(props, "write_scene", True)),
        "node_name": getattr(props, "node_name", "Player"),
        # 运行日志开关(用户第 29 轮:运行时的状态要能复制出来)
        "debug_runtime": bool(getattr(props, "debug_runtime", False)),
    })

    manual = (getattr(props, "output_dir", "") or "").strip()
    if manual:
        settings["output_dir"] = manual
        settings["dir_source"] = "手动指定"
    else:
        directory, reason = project.resolve(scene)
        settings["output_dir"] = directory or ""
        settings["dir_source"] = reason
    return settings


def resolve_main_camera(scene):
    """算出"主相机"是谁(用户第 25 轮)。

    顺序:①设置里明确选的 ②Blender 视口当前相机(scene.camera)
          ③场景里第一台相机 ④None(运行时造一台自由飞行的)
    返回 (名字, 来源说明) —— 说明文字会写进导出报告,用户能看出用的是哪台。
    """
    if scene is None:
        return "", ""
    import bpy  # 模块级没有它(文件里其它地方也是函数内 import)
    settings = getattr(scene, "gk_settings", None)
    wanted = str(getattr(settings, "main_camera", "") or "").strip()
    if wanted and bpy.data.objects.get(wanted) is not None:
        return wanted, "设置里指定的主相机"
    view_cam = getattr(scene, "camera", None)
    if view_cam is not None and getattr(view_cam, "type", "") == "CAMERA":
        return view_cam.name, "跟着 Blender 视口的当前相机"
    cameras = [o.name for o in scene.objects
               if getattr(o, "type", "") == "CAMERA"]
    if cameras:
        return cameras[0], f"自动(场景里第一台;共 {len(cameras)} 台)"
    return "", "场景里没有相机(运行时造一台自由飞行观察机)"


def script_target_object(scene):
    """找出"挂了游戏逻辑脚本"的那个物体。没有就返回 None。"""
    if scene is None:
        return None
    for obj in script_targets(scene):
        return obj
    return None


def script_targets(scene):
    """所有"挂了游戏逻辑脚本"的物体(用户 2026-10-03:"不知道哪个搭载了脚本,
    容易重复")。

    ── 为什么要能列出**全部** ────────────────────────────────────
    以前只有 `script_target_object()`(返回第一个),面板上看不出到底有几个、
    是哪几个 —— 用户于是会重复勾选,而两个物体同时导出脚本会互相覆盖
    (后写的赢),表现是"改了没生效"。
    现在:面板列出名字 + 导出报告里点名,并且**只在多于一个时告警**。
    """
    if scene is None:
        return []
    found = []
    for obj in scene.objects:
        if getattr(obj, "type", "") != "MESH":
            continue
        physics = getattr(obj, "gk_physics", None)
        if physics is None or not getattr(physics, "enabled", True):
            continue
        if getattr(physics, "script_target", False):
            found.append(obj)
    return found


def resolve_base_class(scene):
    """算出脚本该 extends 什么。

    ── 为什么必须统一在一处算 ──────────────────────────────────────
    `.gd` 的 `extends` 和 `.tscn` 里节点的 `type` 必须是同一个值,
    否则 Godot 报
        Script inherits from native type 'X',
        so it can't be assigned to an object of type: 'Y'
    然后**整个场景加载失败** —— 游戏根本跑不起来。

    实测踩过:用户勾了『作为触发器』,节点变成 Area3D,脚本却还是
    StaticBody3D。

    所以脚本和场景两边都调**这一个**函数,不可能算出两个答案。
    """
    from . import properties as props

    settings = getattr(scene, "gk_settings", None)
    dimension = getattr(settings, "dimension", "3") if settings else "3"
    target = script_target_object(scene)
    physics = getattr(target, "gk_physics", None) if target is not None else None
    return props.resolve_base_class(scene, target, physics, dimension)


def default_settings():
    return {
        "dimension": "3",
        "base_class": "CharacterBody3D",
        "class_name": "",
        "export_tunables": True,
        "output_dir": "",
        "dir_source": "",
        "write_scene": False,
        "node_name": "Player",
    }


def generate_code(tree, settings=None):
    """生成代码，返回 (代码, 告警列表, 文件名, 用到的输入动作列表)。

    输入动作要一起返回:生成的脚本里引用 "move_left" / "jump" 这些动作名,
    但 Godot 的 project.godot 里如果没登记它们,运行时会刷一屏
        ERROR: The InputMap action "move_left" doesn't exist.
    而且按键完全没反应。所以调用方要拿这份清单去写 [input] 段。
    """
    if tree is None:
        raise codegen.CodegenAbort("请先在节点编辑器里选择一棵『游戏逻辑』节点树")
    options = dict(settings or {})
    options.setdefault("dimension", "3")
    options.setdefault("base_class", "CharacterBody3D")
    options.setdefault("export_tunables", True)

    # ── 基类可能被"会用到什么"推翻(踩过的坑)────────────────────────
    # 用户实测:物体是静态的 -> 基类算成 StaticBody3D ->
    # 但动画状态机生成的代码调用了 `is_on_floor()` / `velocity`,
    # 那是 CharacterBody 才有的 -> Godot 报
    #     Function "is_on_fleoor()" not found in base self.
    #
    # 这种错不该让用户自己去查物理类型。做法:先**扫一遍节点图**
    # 看会用到哪些基类能力,当前基类给不了就换掉,并把原因告诉用户。
    wanted, why = codegen.required_base_class(tree, options)
    chosen = str(options.get("base_class") or "")
    if wanted and not _class_provides(chosen, wanted):
        options["base_class"] = wanted
        warnings_extra = (
            f"基类已从『{chosen}』改为『{wanted}』:{why}。"
            f"如果想保留原来的基类,请把物体的物理类型改成对应的种类")
    else:
        warnings_extra = ""

    code, warnings = codegen.generate(tree, options)
    if warnings_extra:
        warnings.insert(0, warnings_extra)
    file_name = safe_file_name(getattr(tree, "name", ""), "game_logic") + ".gd"
    actions = list(getattr(codegen, "last_used_actions", []) or [])
    return code, warnings, file_name, actions


# 哪些基类"已经提供了"某个类的能力 —— 用来避免无谓的替换。
# 只需要覆盖实际会出现的组合:CharacterBody 是终极需求,其它都比它弱。
_PROVIDES = {
    "CharacterBody3D": {"CharacterBody3D"},
    "CharacterBody2D": {"CharacterBody2D"},
}


def _class_provides(have, want):
    """`have` 这个基类是否已经满足 `want` 的需求?"""
    if have == want:
        return True
    return want in _PROVIDES.get(have, set())


def write_text(path, text):
    """UTF-8 写文件(Godot 与 Blender 都能正确读取)。"""
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def write_scene_file(directory, node_name, base_class, script_name, script_res_path=None):
    """写场景装配文件到 scenes/ 子目录。

    .tscn 里引用脚本必须用 **res:// 路径**。写相对路径的话 Godot 找不到脚本,
    而且**不会报错** —— 只是在检查器里显示脚本缺失,很难查。
    """
    target_dir = os.path.join(directory, project.SCENES_DIR)
    path = os.path.join(target_dir, f"{safe_file_name(node_name, 'Main')}.tscn")
    if not script_res_path:
        # 默认脚本在 scripts/ 下
        script_res_path = f"{project.SCRIPTS_DIR}/{script_name}"
    if not script_res_path.startswith("res://"):
        script_res_path = f"res://{script_res_path}"
    text = _EXTENDS_TEMPLATE.format(
        script_name=script_res_path, node_name=node_name or "Main", base_class=base_class
    )
    return write_text(path, text)


def write_project_file(directory, project_name="BlenderGameKitDemo"):
    """已废弃。项目骨架统一由 project.create_project() 生成。

    保留这个函数只是不破坏外部引用,它现在**什么都不做**:
    早期版本在这里写了一个简陋的 project.godot,和 project.create_project()
    的模板不一致 —— 两个地方写同一个文件必然打架,而且后者还会带上
    正确的引擎版本号。
    """
    return None


def build_starter_graph(tree):
    """在一棵空的节点树里搭出可运行的最小示例,返回 (节点数, 说明)。

    为什么要这个:空节点树让人无从下手 —— 47 个节点摆在那里,但"该从哪开始"
    完全看不出来。用户的第一反应是"这个怎么用啊"。

    示例内容是一条最短的完整链路:
        每当_准备 ──执行──> 打印_输出
    也就是 Godot 里的:
        func _ready() -> void:
            print("你好，世界")

    刻意做得**短**:短到一眼能看完,但又是完整的(有事件、有语句、有连线)。
    用户照着改就行。
    """
    if tree is None:
        return 0, "没有节点树"

    # 先清空,避免和用户已画的内容混在一起
    for node in list(tree.nodes):
        tree.nodes.remove(node)

    # 找节点类。用 bl_idname 查而不是直接引用,这样和节点定义解耦。
    from .nodes import nodes as node_lib
    by_id = {cls.bl_idname: cls for cls in node_lib.ALL_CLASSES}

    ready_cls = by_id.get("GKNodeOnReady")
    print_cls = by_id.get("GKNodePrint")
    if ready_cls is None or print_cls is None:
        return 0, "找不到『每当_准备』或『打印_输出』节点"

    event = tree.nodes.new(ready_cls.bl_idname)
    event.location = (-260.0, 0.0)
    printer = tree.nodes.new(print_cls.bl_idname)
    printer.location = (60.0, 0.0)

    # 连线:事件的『执行』-> 语句的『执行』
    source = event.outputs.get("执行")
    target = printer.inputs.get("执行")
    if source is None or target is None:
        return 2, "节点建好了,但找不到『执行』引脚,请手动连线"
    tree.links.new(source, target)

    tree.nodes.active = printer
    return 2, "每当_准备 → 打印_输出"


def export_tree(tree, settings=None, write_scene=None):
    """导出节点树，返回一个结果字典(给 UI 用)。"""
    settings = dict(settings or default_settings())
    started = time.time()
    code, warnings, script_name, actions = generate_code(tree, settings)
    # ── 挂了脚本的物体:列出来 + 多于一个就告警(用户 2026-10-03)────────
    # 两个物体同时勾『挂游戏逻辑脚本』的话,导出是**两次覆盖同一个脚本文件**
    # (后写的赢),表现是"我改了设置却没生效"。这里点名说清楚。
    # 离线环境(桩 bpy)里拿不到 scene —— 拿不到就跳过这条检查,
    # 绝不能因为"报告里想多写一行"而让导出失败
    targets = []
    try:
        import bpy
        targets = script_targets(bpy.context.scene)
    except Exception:
        targets = []
    if len(targets) > 1:
        names = "、".join(getattr(o, "name", "?") for o in targets[:4])
        warnings = list(warnings) + [
            f"有 {len(targets)} 个物体都勾了『挂游戏逻辑脚本』({names})—— "
            f"它们会**轮流覆盖**同一个脚本文件,后导出的那个赢。"
            f"请只留一个(选中其它物体 -> 『游戏物理』面板 -> 取消勾选)"]
    elif targets:
        gk_log.info(f"脚本挂在: {getattr(targets[0], 'name', '?')}"
                    f"(可以在『游戏物理』面板里换)")
    # 主相机:说清这一局用的是哪台(用户第 25 轮:"多个窗口时就提示选择")
    try:
        import bpy as _bpy
        cam_name, cam_why = resolve_main_camera(_bpy.context.scene)
        if cam_name:
            gk_log.info(f"主相机: {cam_name}({cam_why})")
        else:
            warnings = list(warnings) + [
                "场景里一台相机都没有 —— 运行时会自动造一台自由飞行观察机"
                "(WASD 平移 / 空格上升 / Shift 下降 / 鼠标转视角)。"
                "想固定镜头:放一台相机,或者在『游戏物理』那一栏指定主相机"]
    except Exception:
        pass
    directory = settings.get("output_dir") or DEFAULT_OUTPUT_DIR
    # 脚本写进 scripts/ 子目录 —— 项目根目录只放 project.godot,
    # 这样"公用的文件夹"结构清晰:脚本、场景、模型、材质各归各位。
    script_path = write_text(
        os.path.join(directory, project.SCRIPTS_DIR, script_name), code)

    # 把用到的输入动作写进 project.godot。
    # 不做这一步的话,Godot 里按键完全没反应,而且只刷一行
    # ERROR: The InputMap action "move_left" doesn't exist.
    # 用户根本想不到"要去输入映射里手工加动作"。
    if actions:
        count = project.set_input_actions(directory, actions)
        if count:
            gk_log.info(f"已把 {count} 个输入动作写进 project.godot")

    written = [script_path]
    should_write_scene = settings.get("write_scene") if write_scene is None else write_scene
    if should_write_scene:
        scene_path = write_scene_file(
            directory,
            settings.get("node_name", "Player"),
            settings.get("base_class", "CharacterBody3D"),
            script_name,
        )
        written.append(scene_path)
        # 顺手把主场景指过去,否则 Godot 里按 F5 会弹"没有设置主场景"
        scene_name = os.path.basename(scene_path)
        if project.set_main_scene(directory, f"{project.SCENES_DIR}/{scene_name}"):
            gk_log.info(f"已把主场景设为 {project.SCENES_DIR}/{scene_name}")

    elapsed = (time.time() - started) * 1000.0
    gk_log.info(f"导出完成: {script_path} ({len(code.splitlines())} 行, {elapsed:.0f} ms)")
    for warning in warnings:
        gk_log.warn(warning)

    return {
        "ok": True,
        "code": code,
        "warnings": warnings,
        "script_name": script_name,
        "written": written,
        "directory": directory,
        "lines": len(code.splitlines()),
        "elapsed_ms": elapsed,
    }
