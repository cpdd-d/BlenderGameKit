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


def script_target_object(scene):
    """找出"挂了游戏逻辑脚本"的那个物体。没有就返回 None。"""
    if scene is None:
        return None
    for obj in scene.objects:
        if getattr(obj, "type", "") != "MESH":
            continue
        physics = getattr(obj, "gk_physics", None)
        if physics is None or not getattr(physics, "enabled", True):
            continue
        if getattr(physics, "script_target", False):
            return obj
    return None


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
