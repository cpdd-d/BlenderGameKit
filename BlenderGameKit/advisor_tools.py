"""AI 顾问能用的工具:只读的自己去查,修改的只**提议**不执行。

── 两条铁律 ──────────────────────────────────────────────────────
1. **只读工具直接执行,修改工具只提议。** 用户明确选了"只读 + 提议动作":
   这个文件里**没有任何**会改 Blender 数据的代码路径 —— 改都由
   `GK_OT_ApplyAdvisorAction` 在用户点『执行』之后做。这样"AI 能不能改我的
   项目"这件事的答案永远由用户按下去那一刻决定。
2. **喂回模型的输出必须摘要。** 节点图可能有 200 个节点,全倒过去会把上下文
   撑爆(顾问特别提醒)。所以每个工具都截断 + 只给"分析需要的最小信息"。

── 为什么单独一个模块 ────────────────────────────────────────────
`advisor.py` 是协议层(拼请求 / 解析响应 / Agent 状态机),刻意保持
纯标准库、能离线测。这个文件要读 bpy 数据,分开后 advisor 那层不会被拖脏。

── 谁调用 ────────────────────────────────────────────────────────
`advisor.poll_agent_step()` 在**主线程**里调 `execute()` —— 只有主线程能碰 bpy。
"""

# 每个只读工具的结果上限(字符)。超了就截断并注明 —— 模型知道"还有更多",
# 需要时可以换个更具体的工具再问,而不是一口气塞爆上下文。
MAX_OUTPUT = 6000


def tools_schema():
    """给 OpenAI 请求用的工具清单(function calling 的标准格式)。"""
    return [
        {
            "type": "function",
            "function": {
                "name": "read_settings",
                "description": ("读本场景的导出设置:2D/3D、脚本基类、类名、"
                                "绑定的逻辑图、上帝摄像机、寻路、卡通材质等。"
                                "用户问『为什么导出不对 / 角色是什么类型』"
                                "时先读它。"),
                "parameters": {"type": "object", "properties": {},
                               "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_trees",
                "description": ("列出这个 .blend 里所有节点图:类型、名字、"
                                "节点数、是否参与导出。"
                                "不确定『有哪些图』时先调它。"),
                "parameters": {"type": "object", "properties": {},
                               "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_nodes",
                "description": ("列出某张节点图里的节点和连线。"
                                "分析『角色为什么不动 / 逻辑为什么没生效』"
                                "时用它。"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tree_name": {
                            "type": "string",
                            "description": ("节点图名字。留空 = 那棵参与导出的"
                                            "游戏逻辑图"),
                        },
                        "detail": {
                            "type": "string",
                            "enum": ["summary", "full"],
                            "description": ("summary(默认)= 只看节点和连线;"
                                            "full = 连每个节点的参数一起给"),
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_export_errors",
                "description": ("读上次导出的报告:成功/失败、错误与警告清单"
                                "(带『怎么修』)、出问题的节点名。"),
                "parameters": {"type": "object", "properties": {},
                               "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_godot_status",
                "description": ("查 Godot 可执行文件、版本、**导出模板是否装了**、"
                                "项目目录、主场景。打包失败、运行不起来时先调它。"),
                "parameters": {"type": "object", "properties": {},
                               "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "diagnose",
                "description": ("跑一遍完整诊断(物体与碰撞尺寸、节点图全貌、"
                                "主相机、试导出的告警)。**输出较长**,只在上面"
                                "几个工具都不够用时调。"),
                "parameters": {"type": "object", "properties": {},
                               "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "propose_action",
                "description": ("**提议**一个修改动作。注意:这不会真的改项目 —— "
                                "用户会在侧栏看到一张待确认卡片,点了才执行。"
                                "不要在回答里说『我已经改好了』。"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": sorted(ACTIONS),
                            "description": "动作名(只能是这些之一)",
                        },
                        "target": {
                            "type": "string",
                            "description": ("动作作用的对象名(物体名 / 节点图名 / "
                                            "属性名)。不需要就留空"),
                        },
                        "value": {
                            "type": "string",
                            "description": "要设成的值(字符串;不需要就留空)",
                        },
                        "why": {
                            "type": "string",
                            "description": "为什么建议这么改(一句话)",
                        },
                    },
                    "required": ["action", "why"],
                },
            },
        },
    ]


# ---------------------------------------------------------------------------
# 可提议/可执行的动作登记表
# ---------------------------------------------------------------------------
# ── 为什么动作要**登记**而不是让 AI 自由发挥 ──────────────────────
# 让模型输出"任意 Python"当然更万能,但那是把整个 Blender 交给它 ——
# 一旦改坏,用户说不清是怎么坏的,也回不去。这里只登记**已知安全、可解释、
# 最小必要**的动作;想加新能力就往这张表里加一条。
#
# 每条:`(中文标签, 需要哪些参数, 执行函数)`
# 执行函数签名 `(context, params) -> (成功?, 说明文字)`。
ACTIONS = {}


def _action(name, label, needs, hint):
    """登记一个动作(装饰器)。"""
    def wrap(func):
        ACTIONS[name] = {"name": name, "label": label, "needs": tuple(needs),
                         "hint": hint, "apply": func}
        return func
    return wrap


@_action("enable_script_target", "给物体勾上『脚本挂载目标』",
         ("target",),
         "角色不动最常见的原因就是这个没勾 —— 导出的场景里那个物体不会挂脚本")
def _enable_script_target(context, params):
    import bpy

    name = str(params.get("target") or "").strip()
    obj = bpy.data.objects.get(name) if name else None
    if obj is None:
        return False, f"找不到物体『{name}』"
    physics = getattr(obj, "gk_physics", None)
    if physics is None:
        return False, f"『{name}』没有游戏物理属性(不是可挂脚本的物体)"
    if getattr(physics, "script_target", False):
        return True, f"『{name}』本来就已经勾上了"
    physics.script_target = True
    return True, f"已给『{name}』勾上『脚本挂载目标』"


@_action("enable_god_camera", "打开上帝摄像机", (),
         "常驻一台自由飞行相机,运行时按 Tab 切换,方便看场景做得对不对")
def _enable_god_camera(context, params):
    settings = getattr(context.scene, "gk_settings", None)
    if settings is None:
        return False, "取不到场景设置"
    settings.god_camera = True
    return True, "已打开上帝摄像机(运行时按 Tab 切换)"


@_action("enable_nav", "打开寻路导航网格", (),
         "让『移动_寻路』的角色能绕开墙和石头走到目标")
def _enable_nav(context, params):
    settings = getattr(context.scene, "gk_settings", None)
    if settings is None:
        return False, "取不到场景设置"
    settings.nav_enabled = True
    return True, "已打开寻路导航(导出后会烤一块导航网格)"


@_action("set_dimension", "改 2D / 3D", ("value",),
         "2D 和 3D 的脚本基类、向量维度都不同,选错会导致导出物在 Godot 里报错")
def _set_dimension(context, params):
    settings = getattr(context.scene, "gk_settings", None)
    if settings is None:
        return False, "取不到场景设置"
    value = str(params.get("value") or "").strip()
    if value not in ("2", "3"):
        return False, f"只能是 2 或 3,收到『{value}』"
    settings.dimension = value
    return True, f"已改成 {value}D"


@_action("set_base_class", "改脚本基类", ("value",),
         "脚本的 extends 必须和节点类型一致,否则 Godot 拒绝加载整个场景")
def _set_base_class(context, params):
    settings = getattr(context.scene, "gk_settings", None)
    if settings is None:
        return False, "取不到场景设置"
    settings.base_class = str(params.get("value") or "").strip()
    return True, f"脚本基类已改成『{settings.base_class}』"


@_action("create_godot_project", "创建 Godot 项目", (),
         "在 .blend 旁边建出配套的 Godot 工程(所有导出/运行的落点)")
def _create_godot_project(context, params):
    from . import project

    directory, reason = project.resolve(getattr(context, "scene", None),
                                        adopt=True)
    if not directory:
        return False, reason or "推不出项目目录(先 Ctrl+S 存一次 .blend)"
    try:
        created = project.create_project(directory)
    except OSError as exc:
        return False, f"创建失败: {exc}"
    return True, (f"已创建/补齐 Godot 项目:{directory}"
                  f"({len(created)} 项)")


@_action("set_main_camera", "指定主相机", ("target",),
         "游戏默认用哪台相机(填场景里那台相机物体的名字)")
def _set_main_camera(context, params):
    import bpy

    settings = getattr(context.scene, "gk_settings", None)
    if settings is None:
        return False, "取不到场景设置"
    name = str(params.get("target") or "").strip()
    obj = bpy.data.objects.get(name)
    if obj is None:
        return False, f"找不到物体『{name}』"
    if getattr(obj, "type", "") != "CAMERA":
        return False, f"『{name}』不是相机(它是 {getattr(obj, 'type', '?')})"
    settings.main_camera = name
    return True, f"主相机已指定为『{name}』"


@_action("set_toon_materials", "切换卡通材质", ("value",),
         "开 = 导出成卡通风格(好看统一);关 = 保持 Blender 原样(便于核对细节)")
def _set_toon_materials(context, params):
    settings = getattr(context.scene, "gk_settings", None)
    if settings is None:
        return False, "取不到场景设置"
    raw = str(params.get("value") or "").strip().lower()
    if raw not in ("true", "false", "1", "0", "on", "off", "是", "否"):
        return False, f"要 true / false,收到『{params.get('value')}』"
    on = raw in ("true", "1", "on", "是")
    settings.toon_materials = on
    return True, f"卡通材质已{'打开' if on else '关闭'}"


@_action("export_script", "导出为 Godot 脚本", (),
         "把节点图重新生成一遍 GDScript 落进 Godot 工程")
def _export_script(context, params):
    import bpy

    try:
        bpy.ops.gk.export_script()
    except Exception as exc:                              # noqa: BLE001
        return False, f"导出失败: {exc}"
    return True, "已导出脚本(结果看『导出报告』)"


@_action("export_assets", "导出模型和材质", (),
         "把场景里的网格 / 材质 / 贴图导进 Godot 工程")
def _export_assets(context, params):
    import bpy

    try:
        bpy.ops.gk.export_assets()
    except Exception as exc:                              # noqa: BLE001
        return False, f"导出失败: {exc}"
    return True, "已导出模型和材质"


def action_label(name):
    """动作的中文标签(卡片标题用)。"""
    entry = ACTIONS.get(name)
    return entry["label"] if entry else name


def action_hint(name):
    """动作的用途说明(卡片副标题用)。"""
    entry = ACTIONS.get(name)
    return entry["hint"] if entry else ""


def action_names():
    """所有动作名(系统提示里列给模型看)。"""
    return sorted(ACTIONS)


def action_menu_items(self, context=None):
    """给侧栏『手动执行一个动作』用的枚举项。

    ── 为什么要给用户一个"手动选动作"的入口 ──────────────────────
    提议卡片必须由 AI 发起 —— 而 AI 不认识"用户没说但确实需要改"的东西,
    也可能没配 AI。给一个手选入口,这套动作清单就不会白做。
    """
    return [(name, ACTIONS[name]["label"], ACTIONS[name]["hint"])
            for name in sorted(ACTIONS)]


def build_proposal(params):
    """把模型给的参数变成一张待确认卡片。返回 (喂回模型的说明, 卡片 或 None)。

    ── 为什么要在这里**校验**而不是等用户点了再报错 ────────────────
    模型可能编一个不存在的动作名、或者该给 target 却没给。如果等用户点
    『执行』才发现,那张卡片就是**假承诺**(看着像能用,点了却报错)。
    所以当场校验:不合格就只把错误喂回模型让它重来,卡片根本不出现。
    """
    name = str(params.get("action") or "").strip()
    entry = ACTIONS.get(name)
    if entry is None:
        return (f"没有叫『{name}』的动作。可用的动作:{action_names()}", None)
    missing = [key for key in entry["needs"] if not str(params.get(key) or "").strip()]
    if missing:
        return (f"动作『{name}』还缺参数:{missing}。请补齐后重新提议。", None)

    target = str(params.get("target") or "").strip()
    value = str(params.get("value") or "").strip()
    card = {
        "action": name,
        "label": entry["label"],
        "target": target,
        "value": value,
        "why": str(params.get("why") or "").strip(),
        "hint": entry["hint"],
    }
    return ("已经把这张卡片展示给用户了,他点『执行』才会生效。"
            "请在回答里说明你建议这么改的理由,但**不要说已经改好了**。"), card


def apply_proposal(context, card):
    """用户点『执行』之后,真正落地。返回 (成功?, 说明文字)。"""
    entry = ACTIONS.get((card or {}).get("action", ""))
    if entry is None:
        return False, "这个动作已经不存在了(可能插件版本变了)"
    params = {"target": (card or {}).get("target", ""),
              "value": (card or {}).get("value", "")}
    try:
        return entry["apply"](context, params)
    except Exception as exc:                              # noqa: BLE001
        return False, f"执行失败: {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# 只读工具的实现
# ---------------------------------------------------------------------------
def execute(name, params, context):
    """执行一个工具。返回 (喂回模型的文本, 待确认动作 或 None)。

    **在主线程里调用**(只读工具要读 bpy 数据)。
    """
    handler = _READERS.get(name)
    if handler is None:
        if name == "propose_action":
            return build_proposal(params or {})
        return (f"没有叫『{name}』的工具。可用:"
                f"{sorted(list(_READERS) + ['propose_action'])}"), None
    try:
        return _clip(handler(context, params or {})), None
    except Exception as exc:                              # noqa: BLE001
        return (f"工具『{name}』执行失败: {type(exc).__name__}: {exc}"), None


def _clip(text):
    """截断过长的输出(见模块开头第 2 条铁律)。"""
    text = str(text or "")
    if len(text) <= MAX_OUTPUT:
        return text
    return (text[:MAX_OUTPUT]
            + f"\n…(输出太长,已截断;共 {len(text)} 字)")


def _read_settings(context, params=None):
    """⚠ 所有 reader 的签名统一是 `(context, params)`。

    不统一的话 `execute()` 按统一签名去调,没有参数的工个就会报
    "takes 1 positional argument but 2 were given" —— 冒烟里真踩到了。
    """
    _ = params                                            # 签名统一用不到它
    import bpy

    scene = getattr(context, "scene", None)
    settings = getattr(scene, "gk_settings", None) if scene else None
    lines = [f".blend: {bpy.data.filepath or '(没保存过)'}",
             f"场景: {getattr(scene, 'name', '(没有)')}"]
    if settings is None:
        lines.append("场景设置没挂上(插件可能没注册成功)")
        return "\n".join(lines)
    for attr in ("dimension", "base_class", "class_name", "node_name",
                 "logic_tree", "god_camera", "nav_enabled", "nav_agent_radius",
                 "toon_materials", "toon_follow_light", "write_scene"):
        lines.append(f"{attr} = {getattr(settings, attr, '(没有这个属性)')!r}")
    return "\n".join(lines)


def _read_trees(context, params=None):
    import bpy

    kinds = {"GKGameLogicTree": "游戏逻辑", "GKAnimationTree": "动画",
             "GKUiDesignTree": "界面设计", "GKGameAiTree": "游戏AI",
             "GKDisplayTree": "显示设置", "GKAssetTree": "资产库"}
    groups = [g for g in bpy.data.node_groups
              if getattr(g, "bl_idname", "") in kinds]
    if not groups:
        return ("一张节点图都没有。\n"
                "提示:节点图要勾 use_fake_user 才会存进 .blend")
    lines = [f"共 {len(groups)} 张图:"]
    for group in groups:
        blob = getattr(group, "bl_idname", "")
        lines.append(f"- [{kinds.get(blob, blob)}] {group.name}: "
                     f"{len(group.nodes)} 节点 / {len(group.links)} 连线"
                     f" / 参与导出={getattr(group, 'gk_enabled', '?')}")
    return "\n".join(lines)


def _read_nodes(context, params=None):
    params = params or {}
    import bpy

    name = str(params.get("tree_name") or "").strip()
    group = bpy.data.node_groups.get(name) if name else _logic_tree()
    if group is None:
        return (f"找不到节点图『{name}』" if name
                else "没有参与导出的游戏逻辑图")
    full = str(params.get("detail") or "summary") == "full"
    lines = [f"图『{group.name}』({getattr(group, 'bl_idname', '')}):"
             f"{len(group.nodes)} 节点 / {len(group.links)} 连线"]
    for node in group.nodes:
        blob = getattr(node, "bl_idname", "?")
        label = getattr(node, "bl_label", blob)
        lines.append(f"- {blob} 「{node.name}」({label})")
        if full:
            for key in _node_params(node):
                lines.append(f"    {key}")
    lines.append("--- 连线 ---")
    for link in group.links:
        lines.append(f"{link.from_node.name}.{link.from_socket.name}"
                     f" -> {link.to_node.name}.{link.to_socket.name}")
    return "\n".join(lines)


def _node_params(node):
    """把节点上**非空**的 gk_ 属性列出来。

    只列非空 —— 一个节点有十几个 gk_ 属性,大部分是默认值/空串,全列出来
    等于给模型灌一屏废话(顾问特别提醒过要摘要)。
    """
    out = []
    rna = getattr(node, "bl_rna", None)
    if rna is None:
        return out
    for prop in rna.properties:
        identifier = getattr(prop, "identifier", "")
        if not identifier.startswith("gk_"):
            continue
        try:
            value = getattr(node, identifier)
        except Exception:                                 # noqa: BLE001
            continue
        if callable(value) or hasattr(value, "to_dict"):
            continue                                      # 跳过集合类属性
        if value in ("", False, None, 0, 0.0):
            continue
        out.append(f"{identifier} = {value!r}")
    return out


def _read_export_errors(context, params=None):
    from . import export_report

    scene = getattr(context, "scene", None)
    report = export_report.load(scene)
    if not report:
        return "还没有导出过(没有报告)"
    errors, warnings, infos = export_report.counts(report)
    lines = [f"上次导出: {'成功' if report.get('ok') else '失败'} · "
             f"错误 {errors} · 警告 {warnings} · 提示 {infos} · "
             f"时间 {report.get('at', '?')}"]
    if report.get("error"):
        lines.append(f"失败原因[{report.get('stage', '')}]: {report['error']}")
    for level, label in (("error", "错误"), ("warning", "警告")):
        for item in export_report.items_of(report, level):
            lines.append(f"[{label}] {item.get('message', '')}")
            if item.get("hint"):
                lines.append(f"    怎么修: {item['hint']}")
            if item.get("node_name"):
                lines.append(f"    相关节点: {item['node_name']}")
    return "\n".join(lines)


def _read_godot_status(context, params=None):
    from . import packaging, project

    lines = []
    executable = project.find_godot_executable(context)
    lines.append(f"Godot 可执行文件: {executable or '(没找到)'}")
    version = project.godot_version(executable, context) if executable else ""
    lines.append(f"Godot 版本: {version or '(取不到)'}")
    if version:
        ready, template_dir, note = packaging.templates_status(version)
        lines.append(f"导出模板: {'已就绪' if ready else '缺失'} {template_dir or ''}")
        if not ready:
            lines.append(f"  {note.splitlines()[0] if note else ''}")
    directory, reason = project.resolve(getattr(context, "scene", None))
    lines.append(f"项目目录: {directory or '(推不出来)'} ({reason})")
    lines.append(f"主场景: {project.main_scene(directory) or '(没设)'}")
    export_preset = packaging.read_preset_name(directory)
    lines.append(f"导出预设: {export_preset or '(还没有,打包时会自动生成)'}")
    return "\n".join(lines)


def _read_diagnose(context, params=None):
    from . import diagnostics

    return diagnostics.build_report(context)


def _logic_tree():
    import bpy

    for group in bpy.data.node_groups:
        if (getattr(group, "bl_idname", "") == "GKGameLogicTree"
                and getattr(group, "gk_enabled", True)):
            return group
    return None


# 工具名 -> 实现。**只读**的都在这里(没有一个会改数据)。
_READERS = {
    "read_settings": _read_settings,
    "list_trees": _read_trees,
    "list_nodes": _read_nodes,
    "check_export_errors": _read_export_errors,
    "get_godot_status": _read_godot_status,
    "diagnose": _read_diagnose,
}