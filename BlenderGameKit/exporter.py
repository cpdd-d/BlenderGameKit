"""导出:把节点树生成 GDScript 并写到磁盘，顺带准备 Godot 工程骨架。"""

import os
import re
import tempfile
import time

from . import (blender_adapter, codegen, curve_assets, gk_log, models, paths,
               project, sound_assets)
from .nodes.node_base import snake_case

GDScriptVersion = 4

# 兜底目录:只在既没有 .blend 路径、也没有手动指定时用。
# 正常情况下不该走到这里 —— 场景目录应该由 project.resolve() 给出。
DEFAULT_OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "BlenderGameKit_Export")

# 场景文件里引用脚本的写法。
# ── 开头那行注释是干什么的(第十三轮)──────────────────────────────
# 它是"这个文件是插件生成的"的**内容标记**,给旧文件清理用
# (见 generated_files.is_generated_file)。Godot 的 .tscn 允许 `;` 注释,
# 实测 4.7.2 能正常加载(带注释的场景 load 成功)。
_EXTENDS_TEMPLATE = """; 由 BlenderGameKit 自动生成,请勿手动修改
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="{script_name}" id="1_script"]

[node name="{node_name}" type="{base_class}"]
script = ExtResource("1_script")
"""


def safe_file_name(text, fallback="player"):
    """把节点树名字转成安全的文件名(中文会被压成下划线，那就用回退名)。

    ── 顺序不能反(踩过的坑)───────────────────────────────────────
    原来先判"名字够不够长"、再压字符集 —— 于是『角色逻辑(预设)』这种
    "中文 + 标点"的名字会通过第一关(中文也算字符),压完只剩 `_`,
    最后落成 `_.gd`(实测:预设建的树就撞上了)。
    正确顺序:先按字符集压干净,再判断压完还剩什么,不行才用回退名。
    """
    name = re.sub(r"[^A-Za-z0-9_\-]", "", snake_case(text or "", ""))
    if len(name.strip("_")) < 2:
        return fallback
    return name


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
        # 游戏内调试面板(用户 2026-10-05 第二十二轮):开了之后生成的脚本
        # 会带一份日志缓冲(_gk_debug_logs),面板(环境脚本)从宿主脚本里
        # 读变量和最近打印 —— 这里只影响代码生成,面板本身在环境脚本里。
        "debug_panel": bool(getattr(props, "debug_panel", False)),
        # 上帝摄像机开关(用户 2026-10-04)
        "god_camera": bool(getattr(props, "god_camera", False)),
        # 寻路(第三十四轮;第三十六轮起 2D 也支持):『移动_寻路』要知道
        # 这一关有没有烤导航网格 —— 没烤的话它退化成直线走,并在导出报告里
        # 提醒一句"场景设置里没勾寻路"。
        # ⚠ 以前这里把 2D 强按成"没勾"—— 现在 2D 也烤导航多边形了,
        #   再按掉的话 2D 场景永远走直线(哪怕 .tscn 里真有导航区)。
        "nav_enabled": bool(getattr(props, "nav_enabled", False)),
    })

    manual = (getattr(props, "output_dir", "") or "").strip()
    if manual:
        settings["output_dir"] = manual
        settings["dir_source"] = "手动指定"
    else:
        directory, reason = project.resolve(scene)
        settings["output_dir"] = directory or ""
        settings["dir_source"] = reason

    # ── 静态资产表(用户 2026-10-05)──────────────────────────────────
    # 『资产_创建实例』是**运行时**节点:按资产名生成一份新实例,所以它
    # 需要"这个名字对应哪份 glb、碰撞生成多大"。资产名是用户在 Blender 里
    # 随手起的,只有导出侧知道这份对应关系 —— 在这里算好塞进设置,
    # 代码生成那边直接查(见 asset_export.asset_codegen_map)。
    # 算不出来(离线/桩环境)就给空表:用到了资产节点的图会拿到一条
    # "资产不在场景里"的告警,而不是让整个导出失败。
    try:
        from . import asset_export
        settings["assets"] = asset_export.asset_codegen_map(
            scene, settings.get("dimension", "3"))
        # ── 粒子发射器表(用户 2026-10-05 第二十一轮)──────────────────
        # 『特效_播放』『特效_停止』『特效_爆发』要知道目标**具体是什么
        # 节点类型** —— 同一个物体名可能是 GPUParticles3D 也可能是
        # CPUParticles3D(面板上选的 GPU / CPU),名字里看不出来。
        # 算好塞进设置,代码生成那边直接查(见 asset_export.fx_codegen_map)。
        settings["fx"] = asset_export.fx_codegen_map(
            scene, settings.get("dimension", "3"))
    except Exception:                                     # noqa: BLE001
        settings["assets"] = {}
        settings["fx"] = {}
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


def game_logic_trees():
    """整个 .blend 里的『游戏逻辑』节点图,按名字排序。

    离线(桩 bpy)环境里拿不到 bpy.data -> 返回空表,由调用方兜底。
    """
    try:
        import bpy
    except Exception:                                     # noqa: BLE001
        return []
    groups = getattr(getattr(bpy, "data", None), "node_groups", None)
    if groups is None:
        return []
    return sorted((g for g in groups
                   if getattr(g, "bl_idname", "") == "GKGameLogicTree"),
                  key=lambda g: getattr(g, "name", ""))


def resolve_scene_tree(scene):
    """算出**某个 Blender 场景**该用哪棵游戏逻辑图(用户 2026-10-04)。

    ── 为什么需要这个 ──────────────────────────────────────────────
    一个 .blend 里可以有多个 Blender 场景(关卡1 / 关卡2 / ...),导出时
    每个场景各出一张 .tscn。但"场景"和"节点图"原本没有绑定关系 ——
    多场景时只能靠"当前节点编辑器里打开的那棵",必然搞不清哪棵属于谁。
    所以每个场景在设置里显式选一棵,这里负责把它解析出来。

    顺序:①场景设置里显式选的(要存在且确实是游戏逻辑图)
          ②整个 .blend 只有一棵 -> 就用它(单场景项目行为完全不变)
          ③其余情况返回 None,由调用方告警
    返回 (tree, 说明文字)。
    """
    if scene is None:
        return None, ""
    settings = getattr(scene, "gk_settings", None)
    wanted = str(getattr(settings, "logic_tree", "") or "").strip()
    if wanted:
        try:
            import bpy
        except Exception:                                 # noqa: BLE001
            return None, "离线环境取不到节点图"
        tree = bpy.data.node_groups.get(wanted)
        if tree is not None and getattr(tree, "bl_idname", "") == "GKGameLogicTree":
            return tree, "场景设置里指定的逻辑图"
        return None, f"场景设置里指定的『{wanted}』不存在(或不是游戏逻辑图)"
    trees = game_logic_trees()
    if len(trees) == 1:
        return trees[0], "只有一棵游戏逻辑图,自动使用"
    if not trees:
        return None, "整个 .blend 里没有游戏逻辑图"
    return None, (f"有 {len(trees)} 棵游戏逻辑图,但这个场景没选 —— "
                  f"请在『导出』面板给场景指定一棵")


def exportable_scenes():
    """要导出成 .tscn 的 Blender 场景列表(按 .blend 里的顺序)。"""
    try:
        import bpy
    except Exception:                                     # noqa: BLE001
        return []
    return list(getattr(bpy.data, "scenes", []) or [])


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
    """默认导出设置 —— 直接由数据模型给出,避免"默认值散落多处不一致"。"""
    return models.ExportSettings().to_dict()


def generate_code(tree, settings=None):
    """生成代码，返回 (代码, 告警列表, 文件名, 用到的输入动作列表)。

    输入动作要一起返回:生成的脚本里引用 "move_left" / "jump" 这些动作名,
    但 Godot 的 project.godot 里如果没登记它们,运行时会刷一屏
        ERROR: The InputMap action "move_left" doesn't exist.
    而且按键完全没反应。所以调用方要拿这份清单去写 [input] 段。

    这是**旧签名**,只给"看看代码和文本告警"的调用方(如预览)用。
    要结构化诊断请用 `generate_code_result()`。
    """
    code, warnings, file_name, actions, _diagnostics = generate_code_result(
        tree, settings)
    return code, warnings, file_name, actions


def generate_code_result(tree, settings=None):
    """同 `generate_code`,但多返回一份结构化诊断(models.Diagnostic)。

    ── 为什么和 generate_code 拆开(GPT 审查:统一诊断机制)──────────
    导出报告需要按 `code` 归类问题,而预览只需要文本告警。多给一份诊断,
    同时**不改 `generate_code` 的签名** —— 免得所有调用方都跟着改。

    返回值(5 个):(代码, 告警, 文件名, 输入动作, 诊断)。
    曲线资源(第十二轮)走 `_generate_code_full` —— 它是"生成时顺带采好的
    数据",导出脚本那一步(export_tree)要拿它写 curves/*.tres。
    """
    code, warnings, file_name, actions, diagnostics, _assets = \
        _generate_code_full(tree, settings)
    return code, warnings, file_name, actions, diagnostics


def _generate_code_full(tree, settings=None):
    """`generate_code_result` 的完整版:多返回一个曲线资源表(第六项)。"""
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

    # ── 用返回值拿结果,不再回头读 codegen 的模块级全局变量 ──────────
    # 以前是 `codegen.generate()` + `getattr(codegen, "last_used_actions")`:
    # 生成失败时那个全局变量还留着上一次的旧值(GPT 审查 / 用户 2026-10-04)。
    result = codegen.generate_result(tree, options)
    code, warnings = result.code, list(result.warnings)
    diagnostics = list(getattr(result, "diagnostics", []) or [])
    if warnings_extra:
        # 基类被自动改写:渲染出来的文本与以前逐字一致,只是额外留一份
        # 带 code 的结构化诊断,方便导出报告归类。
        diagnostics.insert(0, models.Diagnostic(
            level="warning", code="BASE_CLASS_OVERRIDDEN",
            message=warnings_extra))
        warnings.insert(0, warnings_extra)
    # 脚本名默认取节点树名(中文压成空 -> 回退 game_logic.gd)。
    # ── 多场景时必须能**指定**名字(用户 2026-10-04)─────────────────
    # 两棵树的脚本都叫 game_logic.gd 的话会互相覆盖 —— 后写完的那个
    # 赢,前一关的逻辑整份丢(实测:关卡1 还挂着玩家逻辑的场景,跑的
    # 却是关卡2 的脚本)。export_all_scenes 按场景名传进来(见那里的说明)。
    # 传进来的名字**已经 sanitize 过**,这里不再二次压缩 ——
    # 再压一次会把中文抹掉("game_logic_关卡1" -> "game_logic_1")。
    override = str(options.get("script_name") or "").strip()
    if override:
        file_name = override if override.endswith(".gd") else override + ".gd"
    else:
        file_name = safe_file_name(getattr(tree, "name", ""), "game_logic") + ".gd"
    actions = list(result.input_actions)
    assets = dict(getattr(result, "curve_assets", {}) or {})
    return code, warnings, file_name, actions, diagnostics, assets


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


def atomic_write_text(path, text):
    """原子写:先写同目录临时文件,再 `os.replace` 覆盖目标。

    ── 为什么不用直接 `open(path, "w")` ─────────────────────────────
    直接写的话,进程被杀 / 磁盘写满 / 编码错误都可能留下一个**半截**的 .gd。
    那个文件在 Godot 里是一条语法错误,用户完全不知道刚才发生了什么。
    `os.replace` 在同一文件系统内是**原子**操作:目标要么还是旧内容,
    要么完整变成新内容,不存在"写了一半"的中间态。
    """
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=folder or ".",
        prefix=".gk_tmp_", suffix=".part", delete=False)
    try:
        with handle:
            handle.write(text)
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    return path


def atomic_write_bytes(path, data):
    """原子写(二进制)—— 和 `atomic_write_text` 同一套做法。

    第四十三轮加的:回滚音频资产要用它。音频是二进制,照文本写会被
    UTF-8 编码器拒绝(这是实测踩出来的:先炸在"读旧底稿"上)。
    """
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "wb", dir=folder or ".", prefix=".gk_tmp_", suffix=".part",
        delete=False)
    try:
        with handle:
            handle.write(data)
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    return path


def write_text(path, text):
    """UTF-8 写文件(Godot 与 Blender 都能正确读取)。走原子写。"""
    return atomic_write_text(path, text)


class FileTransaction:
    """记录一次导出将要改写的所有文件,任一步失败就整体回滚。

    ── 为什么需要(GPT 审查 / 用户 2026-10-04)──────────────────────
    一次导出会动三类文件:脚本(.gd)、场景(.tscn)以及 project.godot
    (输入映射 + 主场景)。原来的顺序是"写脚本 -> 改 project.godot -> 写场景",
    中间任何一步抛异常,磁盘上就是**半成品**:脚本已经换成新的、场景还是旧的
    —— Godot 里表现为"我改了却没生效",而且没有任何提示。

    这个类在动任何文件之前先把内容**快照**下来;失败时逐个还原,
    原来不存在的文件就删掉。于是"导出"对外变成**全有或全无**。
    另外 `write()` 内部走原子写,单个文件也不会写半截。
    """

    def __init__(self):
        self._snapshots = {}
        self.written = []

    def _snapshot(self, path, binary=False):
        if path in self._snapshots:
            return
        try:
            if binary:
                # 二进制底稿(音频):按字节读 —— 照文本读会炸在解码上
                with open(path, "rb") as handle:
                    self._snapshots[path] = handle.read()
            else:
                with open(path, "r", encoding="utf-8") as handle:
                    self._snapshots[path] = handle.read()
        except FileNotFoundError:
            self._snapshots[path] = None
        except (OSError, UnicodeDecodeError) as exc:
            # 读不出来(权限/编码)也要登记成"不存在",回滚时删掉它,
            # 总比留下一个被改了一半的文件强
            gk_log.warn(f"快照失败,回滚将删除该文件: {path}({exc})")
            self._snapshots[path] = None

    def note(self, path):
        """登记一个**由外部函数改写**的文件(如 project.godot),只做快照。

        用于 `project.set_input_actions()` / `set_main_scene()`:它们自己
        直接写盘,我们没法在中间插手,所以只能事先留底、出错时还原。
        """
        if path:
            self._snapshot(path)

    def write(self, path, text):
        self._snapshot(path)
        atomic_write_text(path, text)
        if path not in self.written:
            self.written.append(path)
        return path

    def mark(self, path):
        """登记一个**由外部写出来**的文件:快照 + 计入本轮产物。

        和 `note()` 只差"记不记帐":`note()` 给 project.godot 那类
        自己有一套管理方式的文件用;`mark()` 给"确实是这次导出写出来的"
        文件用(第四十三轮:复制进 audio/ 的音频)—— 它必须进 `written`,
        否则旧文件清理会把它当上一轮的残留删掉(实测踩过:音频刚复制
        进去就被清走了)。
        """
        if not path:
            return path
        self._snapshot(path, binary=True)
        if path not in self.written:
            self.written.append(path)
        return path

    def rollback(self):
        """把登记过的文件还原到导出前。返回还原失败的路径列表。"""
        failed = []
        for path, original in self._snapshots.items():
            try:
                if original is None:
                    if os.path.exists(path):
                        os.unlink(path)
                elif isinstance(original, bytes):
                    atomic_write_bytes(path, original)
                else:
                    atomic_write_text(path, original)
            except OSError as exc:
                failed.append(path)
                gk_log.error(f"回滚失败,请手动检查这个文件: {path}({exc})")
        self.written = []
        return failed


def render_scene_file(directory, node_name, base_class, script_name,
                      script_res_path=None):
    """算出场景文件的 (路径, 内容),**不写盘**。

    拆出这一步是为了让导出能"先在内存里备齐所有内容、校验之后再落盘"
    (GPT 审查:导出要原子)。`write_scene_file` 仍保留原签名,内部调它。

    .tscn 里引用脚本必须用 **res:// 路径**。写相对路径的话 Godot 找不到脚本,
    而且**不会报错** —— 只是在检查器里显示脚本缺失,很难查。
    """
    target_dir = os.path.join(directory, project.SCENES_DIR)
    path = os.path.join(target_dir, f"{safe_file_name(node_name, 'Main')}.tscn")
    if not script_res_path:
        # 默认脚本在 scripts/ 下
        script_res_path = f"{project.SCRIPTS_DIR}/{script_name}"
    script_res_path = paths.to_res_path(script_res_path)
    text = _EXTENDS_TEMPLATE.format(
        script_name=script_res_path, node_name=node_name or "Main", base_class=base_class
    )
    return path, text


def write_scene_file(directory, node_name, base_class, script_name, script_res_path=None):
    """写场景装配文件到 scenes/ 子目录(原子写)。"""
    path, text = render_scene_file(directory, node_name, base_class, script_name,
                                   script_res_path)
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


def validate_output_dir(directory):
    """检查导出目录能不能安全写入。返回 (ok, 归一化后的路径或错误说明)。

    ── 为什么要这一步(GPT 审查 / 用户 2026-10-04)──────────────────
    `output_dir` 以前是"给什么就用什么":路径拼错、指到一个**文件**、
    指到系统目录,导出器都会照样建目录、覆盖 scripts/*.gd 和 project.godot。
    用户可能因此把别的项目覆盖掉,而且事后完全不知道是插件干的。
    现在先归一化(`abspath`)再检查:必须是目录(或尚不存在、可创建),
    不能指向一个已存在的文件。
    """
    if not directory or not str(directory).strip():
        return False, "没有导出目录(先在『项目』面板把 .blend 绑定到一个 Godot 项目)"
    # 归一化 + 边界判断统一走 paths(纯标准库,可离线单测)
    return paths.validate_project_dir(directory)


def _sound_asset_cards():
    """『游戏资产』图里所有声音卡:**[(显示名, 本地文件)]**(第四十三轮)。

    单独包一层是为了两件事:
      * exporter 不在模块级 import nodes(绕成循环导入),所以延后到调用时;
      * 离线测试里没有 bpy / 没有资产图 —— 拿不到就当"没有音频资产",
        导出照常走完(少带一个音频不该让整次导出失败)。
    """
    try:
        from .nodes import nodes as node_lib
        return [(name, path)
                for name, path, _res in node_lib.sound_card_entries()]
    except Exception:                                     # noqa: BLE001
        return []


def export_tree(tree, settings=None, write_scene=None):
    """导出节点树，返回一个结果字典(给 UI 用)。

    ── 现在是**事务式**的(GPT 审查 / 用户 2026-10-04)────────────────
    内容先在内存里备齐、校验,再统一落盘;中途任何一步失败都会把已经
    写过的文件**回滚**成导出前的样子,不留下"脚本是新的、场景是旧的"
    这种半成品项目。`settings["dry_run"] = True` 时只算清单、不写盘。
    """
    # 边界仍接受 dict(脚本/测试一直这么传),进来先归一化成数据模型 ——
    # 内部一律用属性访问,拼错字段名会直接报错而不是"静默读默认值"。
    options = models.ExportSettings.from_dict(settings or default_settings())
    settings = options.to_dict()
    started = time.time()
    dry_run = options.dry_run
    code, warnings, script_name, actions, diagnostics, curve_assets_data = \
        _generate_code_full(tree, settings)
    warnings = list(warnings)          # 已在 codegen 出口摊平成字符串
    diagnostics = list(diagnostics)    # 结构化诊断(level / code / message / hint)

    def _note(diagnostic):
        """登记一条结构化诊断:既保留对象(给程序化消费),又把摊平后的
        文本放进 warnings —— UI / 日志 / `self.report()` 一直吃字符串,
        这里做一次转换,边界之外的类型**完全不变**(GPT 审查:统一诊断机制)。
        """
        diagnostics.append(diagnostic)
        warnings.append(diagnostic.render())

    # ── 挂了脚本的物体:列出来 + 多于一个就告警(用户 2026-10-03)────────
    # 两个物体同时勾『挂游戏逻辑脚本』的话,导出是**两次覆盖同一个脚本文件**
    # (后写的赢),表现是"我改了设置却没生效"。这里点名说清楚。
    # 离线环境(桩 bpy)里拿不到 scene —— 拿不到就跳过这条检查,
    # 绝不能因为"报告里想多写一行"而让导出失败
    targets = []
    try:
        # 场景统一从适配层取;离线(无 bpy.context)时拿到 None,
        # script_targets(None) 直接返回空表 —— 与旧行为一致,不再需要
        # 单独捕获 AttributeError。
        targets = script_targets(blender_adapter.scene())
    except Exception:
        gk_log.exception("检查『挂脚本物体』时出错(继续导出)")
        targets = []
    if len(targets) > 1:
        names = "、".join(getattr(o, "name", "?") for o in targets[:4])
        _note(models.Diagnostic(
            level="warning", code="MULTIPLE_SCRIPT_TARGETS",
            message=(f"有 {len(targets)} 个物体都勾了『挂游戏逻辑脚本』({names})—— "
                     f"它们会**轮流覆盖**同一个脚本文件,后导出的那个赢。"),
            hint="请只留一个(选中其它物体 -> 『游戏物理』面板 -> 取消勾选)"))
    elif targets:
        gk_log.info(f"脚本挂在: {getattr(targets[0], 'name', '?')}"
                    f"(可以在『游戏物理』面板里换)")
    # 主相机:说清这一局用的是哪台(用户第 25 轮:"多个窗口时就提示选择")
    scene_now = blender_adapter.scene()
    if scene_now is not None:
        try:
            cam_name, cam_why = resolve_main_camera(scene_now)
            if cam_name:
                gk_log.info(f"主相机: {cam_name}({cam_why})")
            else:
                _note(models.Diagnostic(
                    level="warning", code="NO_CAMERA",
                    message=("场景里一台相机都没有 —— 运行时会自动造一台自由飞行"
                             "观察机(WASD 平移 / 空格上升 / Shift 下降 / 鼠标转视角)。"),
                    hint="想固定镜头:放一台相机,并把它设为 Blender 视口的"
                          "当前相机(小键盘 0);也可以打开『上帝摄像机』"))
        except Exception:
            gk_log.exception("检查主相机时出错(继续导出)")

    # ── 先校验目录,再动任何文件 ────────────────────────────────────
    valid, directory = validate_output_dir(
        options.output_dir or DEFAULT_OUTPUT_DIR)
    if not valid:
        gk_log.error(directory)
        return {
            "ok": False, "error": directory, "stage": "校验导出目录",
            "rolled_back": True, "code": code, "warnings": list(warnings),
            "diagnostics": list(diagnostics),
            "script_name": script_name, "written": [], "manifest": [],
            "directory": "", "lines": len(code.splitlines()), "elapsed_ms": 0.0,
        }

    # 脚本写进 scripts/ 子目录 —— 项目根目录只放 project.godot,
    # 这样"公用的文件夹"结构清晰:脚本、场景、模型、材质各归各位。
    script_path = paths.safe_join_project(directory, project.SCRIPTS_DIR,
                                          script_name)
    should_write_scene = (options.write_scene if write_scene is None
                          else write_scene)
    scene_path = None
    scene_text = None
    if should_write_scene:
        scene_path, scene_text = render_scene_file(
            directory,
            options.node_name,
            options.base_class,
            script_name,
        )
        # 顺手把主场景指过去,否则 Godot 里按 F5 会弹"没有设置主场景"

    project_file = os.path.join(directory, "project.godot")
    manifest = [script_path]
    # ── 曲线资源(第十二轮)──────────────────────────────────────────
    # 脚本里写死 `res://curves/xxx.tres`(相机沿曲线运镜 / 时间轴的曲线),
    # 所以必须和脚本同一次落盘 —— 少写一次就是"脚本引用了不存在的资源"
    # (运行时只是警句 + 退回直线过渡,但用户会以为曲线没生效)。
    curve_files = curve_assets.curve_files(directory, curve_assets_data)
    manifest.extend(path for _name, path, _text in curve_files)
    # ── 音频资产(第四十三轮)──────────────────────────────────────────
    # 『游戏资产』图的『资产_声音卡』挑好的本地文件,导出时复制进项目的
    # audio/ —— 音效节点里写死的 `res://audio/...` 才找得到东西。
    # 和曲线同一条思路:脚本引用什么,这一步就得把什么带上。
    sound_cards = _sound_asset_cards()
    planned_sounds = [
        f"{sound_assets.AUDIO_DIR}/{os.path.basename(target)}"
        for _name, _path, target, _res
        in sound_assets.sound_files(directory, sound_cards)]
    manifest.extend(planned_sounds)
    if scene_path:
        manifest.append(scene_path)
    if actions or should_write_scene:
        manifest.append(project_file)
    if not os.path.isfile(project_file):
        _note(models.Diagnostic(
            level="warning", code="NO_PROJECT_FILE",
            message="导出目录里没有 project.godot —— 输入映射和主场景这次不会写进去。",
            hint="先建好 Godot 项目(『项目』面板 -> 创建),否则 Godot 里按 F5 跑不起来"))
        manifest = [p for p in manifest if p != project_file]

    if dry_run:
        gk_log.info(f"[dry-run] 将写入 {len(manifest)} 个文件: {manifest}")
        return {
            "ok": True, "dry_run": True, "code": code, "warnings": list(warnings),
            "diagnostics": list(diagnostics),
            "script_name": script_name, "written": [], "manifest": manifest,
            "directory": directory, "lines": len(code.splitlines()),
            "elapsed_ms": 0.0,
        }

    transaction = FileTransaction()
    stage = "写脚本"
    try:
        transaction.write(script_path, code)

        # 曲线资源(和脚本一起进事务:出错一起回滚)
        if curve_files:
            stage = "写曲线资源"
            for name, path, text in curve_files:
                transaction.write(path, text)
            gk_log.info(f"曲线资源 {len(curve_files)} 个 -> {project.CURVES_DIR}/")

        # 把用到的输入动作写进 project.godot。
        # 不做这一步的话,Godot 里按键完全没反应,而且只刷一行
        # ERROR: The InputMap action "move_left" doesn't exist.
        # 用户根本想不到"要去输入映射里手工加动作"。
        if actions:
            stage = "写输入映射"
            transaction.note(project_file)
            count = project.set_input_actions(directory, actions)
            if count:
                gk_log.info(f"已把 {count} 个输入动作写进 project.godot")

        if should_write_scene:
            stage = "写场景"
            transaction.write(scene_path, scene_text)
            stage = "设主场景"
            transaction.note(project_file)
            scene_name = os.path.basename(scene_path)
            if project.set_main_scene(directory, f"{project.SCENES_DIR}/{scene_name}"):
                gk_log.info(f"已把主场景设为 {project.SCENES_DIR}/{scene_name}")

        # 音频资产(第四十三轮):复制进 audio/。不走文本事务(二进制),
        # 所以放在事务写入之后 —— 最坏情况是多留一个音频文件,
        # 下一次导出的清理会按清单把它收走(见 generated_files)。
        if planned_sounds:
            stage = "复制音频资产"
            written_sounds, missing_sounds = sound_assets.copy_sounds(
                directory, sound_cards)
            if written_sounds:
                gk_log.info(f"音频资产 {len(written_sounds)} 个 -> "
                            f"{sound_assets.AUDIO_DIR}/")
            # 复制成功的**记进本轮产物**:不记的话旧文件清理会把它删掉
            # (它不在事务的 written 里 —— 二进制是直接拷的)
            for name, path, target, _res in sound_assets.sound_files(
                    directory, sound_cards):
                if os.path.isfile(target):
                    transaction.mark(target)
            for missing_name, missing_path in missing_sounds:
                _note(models.Diagnostic(
                    level="warning", code="SOUND_FILE_MISSING",
                    message=f"音频资产『{missing_name}』的本地文件找不到了:"
                            f"{missing_path}",
                    hint="在『游戏资产』图的『资产_声音卡』上重新挑一次文件"))
            # 没复制成功的从清单里去掉(它们没进项目,别让清理以为写过)
            kept = [rel for rel in planned_sounds
                    if os.path.isfile(os.path.join(directory, rel))]
            manifest = [item for item in manifest
                        if item not in planned_sounds] + kept
    except Exception as exc:
        failed = transaction.rollback()
        gk_log.exception(f"导出失败({stage}),已回滚本次写入")
        if failed:
            message = (f"{stage}失败: {exc};有 {len(failed)} 个文件回滚失败,"
                       f"请手动检查: {'、'.join(failed)}")
        else:
            message = f"{stage}失败(已回滚,工程保持原样): {exc}"
        gk_log.error(message)
        return {
            "ok": False, "error": str(exc), "stage": stage, "message": message,
            "rolled_back": not failed, "code": code, "warnings": list(warnings),
            "diagnostics": list(diagnostics),
            "script_name": script_name, "written": [], "manifest": manifest,
            "directory": directory, "lines": len(code.splitlines()),
            "elapsed_ms": (time.time() - started) * 1000.0,
        }

    elapsed = (time.time() - started) * 1000.0
    gk_log.info(f"导出完成: {script_path} ({len(code.splitlines())} 行, {elapsed:.0f} ms)")
    for warning in warnings:
        gk_log.warn(warning)

    return {
        "ok": True,
        "code": code,
        "warnings": warnings,
        "diagnostics": list(diagnostics),
        "script_name": script_name,
        # 这次导出用到的输入动作(动作名, 捕获的键)—— 多场景导出要靠它
        # 把各场景的动作**并起来**再写 [input](见 export_all_scenes)。
        # 不返回的话,后导出的场景会覆盖前一个场景的动作(第三十一轮实测:
        # 关卡1 的 key_e 被关卡2 的覆盖,第一关按 E 完全没反应)。
        "input_actions": list(actions),
        "written": list(transaction.written),
        "manifest": manifest,
        "directory": directory,
        "lines": len(code.splitlines()),
        "elapsed_ms": elapsed,
    }
