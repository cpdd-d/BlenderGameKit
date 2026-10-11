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

# 名字解析 / 查重 / 分层布局这些**纯计算**放在单独模块里(它不 import bpy),
# 这样那几段最容易写歪的逻辑能离线秒级回归(见 advisor_edits 模块开头)。
from . import advisor_edits as edits  # noqa: E402


def tools_schema():
    """给 OpenAI 请求用的工具清单(function calling 的标准格式)。"""
    # 动作说明书拼进描述里(第一百零七轮):只给 enum 的话模型不知道
    # target / value / args 该填什么,只能猜 —— 猜错就是一张点了会报错的卡片。
    catalog = ("**提议**一个修改动作。注意:这不会真的改项目 —— "
               "用户会在侧栏看到一张待确认卡片,点了才执行。"
               "不要在回答里说『我已经改好了』。"
               "要一次改好几处时用 `propose_actions`(整批一起提)。\n\n"
               "可用的动作(含各自要的参数):\n" + action_catalog())
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
                "description": catalog,
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
                                            "节点名)。不需要就留空"),
                        },
                        "value": {
                            "type": "string",
                            "description": "要设成的值(字符串;不需要就留空)",
                        },
                        "args": {
                            "type": "object",
                            "description": ("动作自己的参数(对象)。**每个动作"
                                            "要哪些键见上面的动作说明**;"
                                            "不需要就留空对象"),
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
        {
            "type": "function",
            "function": {
                "name": "propose_actions",
                "description": ("**一次提议多条**修改(看完项目后,把要改的"
                                "一次全提出来 —— 别一条一条分好几轮)。"
                                "同样不会真的改项目:侧栏会一起摆出这几张"
                                "待确认卡片,用户点『全部执行』或逐张点才生效。\n\n"
                                "每个动作要的参数:\n" + action_catalog()),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "actions": {
                            "type": "array",
                            "description": "要提议的动作清单(1~6 条)",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "action": {
                                        "type": "string",
                                        "enum": sorted(ACTIONS),
                                        "description": "动作名(只能是这些之一)",
                                    },
                                    "target": {
                                        "type": "string",
                                        "description": "对象名(不需要就留空)",
                                    },
                                    "value": {
                                        "type": "string",
                                        "description": "要设成的值(不需要就留空)",
                                    },
                                    "args": {
                                        "type": "object",
                                        "description": ("动作自己的参数对象"
                                                        "(见上面的动作说明)"),
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
                    "required": ["actions"],
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


def _action(name, label, needs, hint, args_hint=None):
    """登记一个动作(装饰器)。

    ── `args` 是干什么的(第一百零七轮)────────────────────────────
    卡片原来只有 `target` + `value` 两个字符串槽 —— 够表达"改哪个开关",
    但表达不了"在哪张图、建哪种节点、接在谁后面"。硬塞进两个槽会变成
    `target="游戏逻辑|界面_按钮"` 这种要靠分隔符猜的写法:模型容易写错,
    用户看卡片也看不懂。
    所以卡片多了第三个槽 **`args`**(一个对象),每个动作用 `args_hint`
    声明自己认识哪些键 —— 这份说明会**拼进给模型的工具描述里**
    (见 `action_catalog`),模型不用猜参数名;校验不过时也会回给模型,
    它能自己改对再提一次。
    """
    def wrap(func):
        ACTIONS[name] = {"name": name, "label": label, "needs": tuple(needs),
                         "hint": hint, "args_hint": dict(args_hint or {}),
                         "apply": func}
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


# ---------------------------------------------------------------------------
# 改节点图的动作(第一百零七轮)
# ---------------------------------------------------------------------------
# 这一组和上面那批"改设置 / 开关"不一样:它们动的是**节点图**这种结构化数据
# (建节点、连线、改属性、删改名字、加分组框、重排布局)。所以卡片多了 `args`
# 那一层参数(哪张图 / 哪种节点 / 接在谁后面),见 `_action` 里的说明。
#
# ── 安全模型一个字没动 ──────────────────────────────────────────────
# 这些函数**只会在用户点『执行』之后**被 `apply_proposal` 调到;AI 那头
# 能做的仍然只是"提议"。加动作 ≠ 给 AI 开直通车。
def _resolve_tree(args):
    """动作要动手的节点图。返回 (图 或 None, 错误说明)。

    `args.tree` 留空 = 参与导出的那张游戏逻辑图(和 `_read_nodes` 一个口径)。
    """
    import bpy

    name = str((args or {}).get("tree") or "").strip()
    if name:
        group = bpy.data.node_groups.get(name)
        if group is None:
            return None, f"找不到节点图『{name}』(用 list_trees 看有哪些图)"
        return group, ""
    group = _logic_tree()
    if group is None:
        return None, ("没有参与导出的游戏逻辑图 —— 先在侧栏『游戏逻辑』里"
                      "新建或指定一张")
    return group, ""


def _node_not_found(tree, name):
    """找不到节点时的人话(顺带把图里现有的节点名报几个)。"""
    names = [node.name for node in tree.nodes]
    if names:
        more = "…" if len(names) > 12 else ""
        return (f"图『{tree.name}』里找不到节点『{name}』。"
                f"图里的节点有:{'、'.join(names[:12])}{more}")
    return f"图『{tree.name}』里还没有节点(先 add_node 建一个)"


def _editable_hint(node):
    """列出这个节点上几个 `gk_` 属性名 —— 拼错属性名时给用户/模型照着改。

    只列前几个:节点动辄十几二十个 `gk_` 属性,全倒出来是噪音。
    """
    names = []
    try:
        for prop in getattr(node, "bl_rna", None).properties:
            identifier = getattr(prop, "identifier", "")
            if identifier.startswith("gk_"):
                names.append(identifier)
    except Exception:                                     # noqa: BLE001
        return ""
    names.sort()
    if not names:
        return ""
    tail = "…" if len(names) > 8 else ""
    return "这个节点能改的属性有:" + "、".join(names[:8]) + tail


def _flow_outputs(node):
    return [socket for socket in (getattr(node, "outputs", None) or ())
            if str(getattr(socket, "bl_idname", "")) == "GKSocketFlow"]


def _flow_inputs(node):
    return [socket for socket in (getattr(node, "inputs", None) or ())
            if str(getattr(socket, "bl_idname", "")) == "GKSocketFlow"]


@_action("add_node", "新建节点并连线", ("args:type",),
         "在图里建一个节点,可以顺手接在某个节点后面",
         args_hint={
             "tree": "节点图名(留空 = 参与导出的游戏逻辑图)",
             "type": "节点类型,填中文标签(如『移动_按方向』/『界面_按钮』)"
                     "或内部名(如 GKNodePrint)",
             "after": "接在哪个节点之后(节点名);留空 = 只建不连",
             "pin": "从上游的哪个输出口接;留空 = 第一个执行口(通常是『完成』)",
         })
def _add_node(context, params):
    from .nodes import nodes as node_lib

    args = (params or {}).get("args") or {}
    tree, error = _resolve_tree(args)
    if tree is None:
        return False, error
    cls, error = edits.resolve_node_type(args.get("type"), node_lib.ALL_CLASSES)
    if cls is None:
        return False, error

    # ── 先拦"这张图收不收这种节点"(踩过的坑)────────────────────────
    # 在『游戏逻辑』图里加『动画状态』节点,Blender 会甩出一句英文 RuntimeError
    # (poll 挡下来了)。给一句人话告诉用户该去哪种树(复用 operator_io 的判据)。
    try:
        from .operator_io import node_tree_error
        problem = node_tree_error(cls, tree)
    except Exception:                                     # noqa: BLE001
        problem = ""
    if problem:
        return False, problem

    after_name = str(args.get("after") or "").strip()
    anchor = edits.find_by_name(tree.nodes, after_name) if after_name else None
    if after_name and anchor is None:
        # 目标节点不存在就**在建之前**失败,以免留下一个没人接的孤儿节点
        return False, _node_not_found(tree, after_name)

    try:
        node = tree.nodes.new(cls.bl_idname)
    except Exception as exc:                              # noqa: BLE001
        return False, f"这张图放不下『{cls.bl_label}』: {exc}"

    # 摆位:有 after 就摆它右边一列;否则挑个不重叠的空白格
    if anchor is not None:
        width = float(getattr(anchor, "width", 0.0) or 0.0) or 160.0
        node.location = (float(anchor.location[0]) + width + 60.0,
                         float(anchor.location[1]))
    else:
        node.location = edits.non_overlapping_spot(
            [(item.location[0], item.location[1]) for item in tree.nodes])

    linked = ""
    if anchor is not None:
        pin_name = str(args.get("pin") or "").strip()
        if pin_name:
            output = (anchor.outputs.get(pin_name)
                      if hasattr(anchor.outputs, "get") else None)
            if output is None:
                names = [socket.name for socket in
                         (getattr(anchor, "outputs", None) or ())]
                tree.nodes.remove(node)
                return False, (f"『{anchor.name}』没有叫『{pin_name}』的输出口。"
                               f"它的输出口有:{'、'.join(names) or '（没有）'}")
        else:
            outputs = _flow_outputs(anchor)
            if not outputs:
                tree.nodes.remove(node)
                return False, (f"『{anchor.name}』没有执行流输出口,"
                               f"不能从它往下接(它是取值节点?)")
            output = outputs[0]
        inputs = _flow_inputs(node)
        if not inputs:
            tree.nodes.remove(node)
            return False, (f"『{cls.bl_label}』没有执行流输入口,不能接在别人"
                           f"后面(它是事件 / 取值节点?)")
        try:
            tree.links.new(output, inputs[0])
        except Exception as exc:                          # noqa: BLE001
            tree.nodes.remove(node)
            return False, (f"接线失败(引脚类型对不上?): {exc}")
        linked = f",并从『{anchor.name}』的『{output.name}』接上了『{inputs[0].name}』"

    return True, f"已新建『{cls.bl_label}』节点,名字是『{node.name}』{linked}"


@_action("set_node_prop", "改节点卡片上的属性",
         ("args:node", "args:prop", "args:value"),
         "改节点上填的参数(如按钮上的文字、控件显示与否)",
         args_hint={
             "tree": "节点图名(留空 = 游戏逻辑图)",
             "node": "节点名(卡片标题上的名字)",
             "prop": "属性名(如 gk_text / gk_visible)",
             "value": "要设成的值(字符串;布尔填 true/false、是/否)",
         })
def _set_node_prop(context, params):
    args = (params or {}).get("args") or {}
    tree, error = _resolve_tree(args)
    if tree is None:
        return False, error
    node = edits.find_by_name(tree.nodes, args.get("node"))
    if node is None:
        return False, _node_not_found(tree, args.get("node"))
    prop = str(args.get("prop") or "").strip()
    if not prop:
        return False, "没给属性名(如 gk_text)"
    # ── 必须 **hasattr** 验一遍 ───────────────────────────────────────
    # 拼错的属性名如果直接 setattr,Blender 不报错、静默造一个没用的自定义
    # 属性(节点看着"改成功了",行为却一点没变)。先验存在再改。
    if not hasattr(node, prop):
        return False, (f"『{node.name}』上没有『{prop}』这个属性(名字拼错了?)"
                       f"{_editable_hint(node)}")
    current = getattr(node, prop)
    ok, value = edits.coerce_value(current, args.get("value"))
    if not ok:
        return False, f"『{prop}』{value}{_editable_hint(node)}"
    try:
        setattr(node, prop, value)
    except Exception as exc:                              # noqa: BLE001
        return False, f"写『{prop}』失败: {exc}"
    return True, f"『{node.name}』的 {prop} 已改成 {getattr(node, prop)!r}"


@_action("delete_node", "删除节点(连带清线)", ("args:node",),
         "删掉一个节点,连着它接的线一起清掉",
         args_hint={
             "tree": "节点图名(留空 = 游戏逻辑图)",
             "node": "要删的节点名",
         })
def _delete_node(context, params):
    args = (params or {}).get("args") or {}
    tree, error = _resolve_tree(args)
    if tree is None:
        return False, error
    node = edits.find_by_name(tree.nodes, args.get("node"))
    if node is None:
        return False, _node_not_found(tree, args.get("node"))
    name = node.name
    # 先数一遍要连带清掉的线(remove 之后这些线就查不到了,数不出"清了几条")
    removed = 0
    for link in list(getattr(tree, "links", None) or ()):
        ends = (getattr(getattr(link, "from_node", None), "name", ""),
                getattr(getattr(link, "to_node", None), "name", ""))
        if name in ends:
            removed += 1
    try:
        # Blender 会自动清掉跟这个节点相连的所有连线,不用手动断
        tree.nodes.remove(node)
    except Exception as exc:                              # noqa: BLE001
        return False, f"删不掉『{name}』: {exc}"
    return True, f"已删除『{name}』,顺带清掉 {removed} 条连线"


@_action("rename_node", "改节点名", ("args:node", "args:name"),
         "给节点换个名字(连线不受影响)",
         args_hint={
             "tree": "节点图名(留空 = 游戏逻辑图)",
             "node": "原名(当前节点名)",
             "name": "新名字",
         })
def _rename_node(context, params):
    args = (params or {}).get("args") or {}
    tree, error = _resolve_tree(args)
    if tree is None:
        return False, error
    node = edits.find_by_name(tree.nodes, args.get("node"))
    if node is None:
        return False, _node_not_found(tree, args.get("node"))
    new_name = str(args.get("name") or "").strip()
    if not new_name:
        return False, "没给新名字"
    usable, clash = edits.unique_name([n.name for n in tree.nodes], new_name)
    if not usable:
        return False, (f"已经有节点叫『{clash}』了,换个名字。"
                       f"(不拦的话 Blender 会自动把新名加成『{clash}.001』——"
                       f"看着像改名没生效)")
    old = node.name
    try:
        node.name = new_name
    except Exception as exc:                              # noqa: BLE001
        return False, f"改名失败: {exc}"
    return True, f"『{old}』已改名为『{node.name}』"


@_action("group_nodes", "加分组框", ("args:nodes",),
         "把几个节点框成一个分组框(能折叠、拖动整组)",
         args_hint={
             "tree": "节点图名(留空 = 游戏逻辑图)",
             "nodes": "要框起来的节点名,**逗号分隔**(如『准备,移动』)",
             "title": "框的标题(留空 = 『分组』)",
         })
def _group_nodes(context, params):
    from . import operator_io

    args = (params or {}).get("args") or {}
    tree, error = _resolve_tree(args)
    if tree is None:
        return False, error
    names = edits.parse_name_list(args.get("nodes"))
    if not names:
        return False, "没给要框起来的节点名(逗号分隔,如『准备,移动』)"
    found = []
    missing = []
    for name in names:
        node = edits.find_by_name(tree.nodes, name)
        if node is None:
            missing.append(name)
        else:
            found.append(node)
    if missing:
        return False, (f"找不到这些节点:{'、'.join(missing)}(先在侧栏列表里"
                       f"核对名字)")
    # ── 就用插件早就有的那套"分组框"机制 ────────────────────────────
    # 分组框在数据上就是 Blender 原生的 `NodeFrame`(带标题 / 颜色 / 认子
    # 节点),`GK_OT_AddCommentBox` 也是这么建的。包围盒也用它的纯函数
    # `comment_box_rect` —— 不另发明一套结构,免得和"折叠 / 展开"那条链脱节。
    location, width, height = operator_io.comment_box_rect(found)
    try:
        frame = tree.nodes.new("NodeFrame")
    except Exception as exc:                              # noqa: BLE001
        return False, f"建分组框失败: {exc}"
    # 顺序要紧:先定框的位置,再认子(认子时 Blender 会把子节点坐标换算成
    # 相对框的坐标,框没定好子节点就会被算歪 —— 见 add_comment_box 那段)。
    frame.location = location
    frame.width = width
    frame.height = height
    frame.use_custom_color = True
    frame.color = (0.16, 0.30, 0.45)
    frame.label = str(args.get("title") or "").strip() or "分组"
    frame.label_size = 16
    for node in found:
        node.parent = frame
    return True, (f"已把 {len(found)} 个节点框进『{frame.label}』"
                  f"(分组框名字『{frame.name}』,可折叠)")


@_action("tidy_tree", "自动整理节点布局", (),
         "按连线重新排一下位置(执行流从左上到右下),只动位置、不动连线",
         args_hint={
             "tree": "节点图名(留空 = 游戏逻辑图)",
         })
def _tidy_tree(context, params):
    args = (params or {}).get("args") or {}
    tree, error = _resolve_tree(args)
    if tree is None:
        return False, error
    # ── 只重排"真节点",分组框留在原地 ───────────────────────────────
    # 分组框(NodeFrame)在连线里根本不出现,拉进来会被全塞到第一层、
    # 和它罩着的节点叠在一起。跳过后框位置不变,子节点的相对坐标照旧有效。
    real_nodes = [node for node in tree.nodes
                  if str(getattr(node, "bl_idname", "")) != "NodeFrame"]
    if not real_nodes:
        return False, f"图『{tree.name}』里还没有节点,没得整理"
    names = [node.name for node in real_nodes]
    edges = []
    for link in list(getattr(tree, "links", None) or ()):
        edges.append((getattr(getattr(link, "from_node", None), "name", ""),
                      getattr(getattr(link, "to_node", None), "name", "")))
    # 纯函数只算坐标,**不碰任何节点** —— 写回 node.location 的只有下面这段,
    # 连线 / 节点数量一个字都不变(所以这是零风险动作)。
    positions = edits.layer_positions(names, edges)
    moved = 0
    for node in real_nodes:
        spot = positions.get(node.name)
        if spot is None:
            continue
        before = (round(float(node.location[0]), 3),
                  round(float(node.location[1]), 3))
        if before != (round(spot[0], 3), round(spot[1], 3)):
            moved += 1
        node.location = spot
    return True, (f"已按连线重排 {len(names)} 个节点的位置(实际挪动 {moved} 个),"
                  f"连线一条没动")


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


def action_catalog():
    """给模型看的动作说明书:每个动作要哪些参数、`args` 里能填什么键。

    ── 为什么必须带上这段(第一百零七轮)──────────────────────────
    以前工具描述里只列了动作**名字**(一个 enum)。模型知道有
    `add_node` 这个动作,却不知道 `args` 该填 `tree` / `type` / `after` ——
    只能猜,猜错就是一张"点了会报错"的卡片。把每个动作的参数说明拼进
    描述里,它一次就能写对;校验失败时这段也一起回给它,能自己改对。
    """
    lines = []
    for name in sorted(ACTIONS):
        entry = ACTIONS[name]
        need = "、".join(entry["needs"]) or "不需要参数"
        lines.append(f"- {name}({entry['label']}):{entry['hint']}｜需要:{need}")
        for key, text in (entry.get("args_hint") or {}).items():
            lines.append(f"    · args.{key} = {text}")
    return "\n".join(lines)


def _parse_args(raw):
    """把 `args` 收成一个 dict。返回 (字典, 错误说明)。

    模型有时会把 `args` 写成一个 **JSON 字符串**(function calling 的
    arguments 本来就是字符串嵌套,很容易糊在一起),有时写成真正的对象。
    两种都收下,别让格式问题浪费用户一张卡片。
    """
    if raw in (None, "", {}):
        return {}, ""
    if isinstance(raw, dict):
        return dict(raw), ""
    text = str(raw).strip()
    if not text:
        return {}, ""
    if not text.startswith("{"):
        return {}, (f"它得是一个对象(形如 {{\"type\": \"界面_按钮\"}}),"
                    f"收到『{text[:60]}』")
    try:
        import json
        loaded = json.loads(text)
    except Exception as exc:                              # noqa: BLE001
        return {}, f"JSON 解析失败({exc})"
    if not isinstance(loaded, dict):
        return {}, "解析出来不是对象"
    return loaded, ""


def _args_doc(name):
    """某个动作的 `args` 说明(校验失败时回给模型)。"""
    entry = ACTIONS.get(name) or {}
    hints = entry.get("args_hint") or {}
    if not hints:
        return ""
    return "\n这个动作的 args 可以填:\n" + "\n".join(
        f"  · {key} = {text}" for key, text in hints.items())


def action_menu_items(self, context=None):
    """给侧栏『手动执行一个动作』用的枚举项。

    ── 为什么要给用户一个"手动选动作"的入口 ──────────────────────
    提议卡片必须由 AI 发起 —— 而 AI 不认识"用户没说但确实需要改"的东西,
    也可能没配 AI。给一个手选入口,这套动作清单就不会白做。
    """
    return [(name, ACTIONS[name]["label"], ACTIONS[name]["hint"])
            for name in sorted(ACTIONS)]


def _validate_card(params):
    """校验一条提议 -> (错误说明 或 "", 卡片 或 None)。

    校验放在**提议**这一步(而不是等用户点『执行』)是为了不让卡片变成
    **假承诺** —— 点了才发现参数不对,用户会失去对这套动作的信任。
    """
    name = str(params.get("action") or "").strip()
    entry = ACTIONS.get(name)
    if entry is None:
        return f"没有叫『{name}』的动作。可用的动作:{action_names()}", None
    args, arg_error = _parse_args(params.get("args"))
    if arg_error:
        return (f"动作『{name}』的 args 不行:{arg_error}"
                + _args_doc(name)), None
    target = str(params.get("target") or "").strip()
    value = str(params.get("value") or "").strip()
    missing = []
    for key in entry["needs"]:
        if key == "args":
            if not args:
                missing.append("args")
        elif key.startswith("args:"):
            if not str(args.get(key[len("args:"):]) or "").strip():
                missing.append("args." + key[len("args:"):])
        elif not str(params.get(key) or "").strip():
            missing.append(key)
    if missing:
        hint = _args_doc(name)
        return (f"动作『{name}』还缺参数:{missing}。请补齐后重新提议。"
                + (("\n" + hint) if hint else "")), None
    card = {
        "action": name,
        "label": entry["label"],
        "target": target,
        "value": value,
        "args": args,
        "why": str(params.get("why") or "").strip(),
        "hint": entry["hint"],
    }
    return "", card


def build_proposal(params):
    """把模型给的参数变成一张待确认卡片。返回 (喂回模型的说明, 卡片 或 None)。

    ── 为什么要在这里**校验**而不是等用户点了再报错 ────────────────
    模型可能编一个不存在的动作名、或者该给 target 却没给。如果等用户点
    『执行』才发现,那张卡片就是**假承诺**(看着像能用,点了却报错)。
    所以当场校验:不合格就只把错误喂回模型让它重来,卡片根本不出现。
    """
    error, card = _validate_card(params or {})
    if error:
        return error, None
    return ("已经把这张卡片展示给用户了,他点『执行』才会生效。"
            "请在回答里说明你建议这么改的理由,但**不要说已经改好了**。"), card


def build_proposals(params):
    """批量提议(第九十轮:待办 31 "AI 连续调多个工具后一起提议")。

    返回 (喂回模型的说明, [卡片, …])。**逐条校验**:合格的进清单,
    不合格的在说明里点名(模型下一轮能补对,而不是整批作废)。

    ── 为什么要一个批量工具(而不是让模型连调几次 propose_action)────
    连调多次当然也能出多张卡片(循环里是**追加**),但:
      * 模型常常"提一条、等结果、再提一条",白烧好几轮;
      * 用户那边要一张张点『执行』 —— 一次清洁的修改(勾上挂载目标 +
        打开寻路)要点两次。
    有了它:一次工具调用把**整批**摆出来,侧栏一个『全部执行』一键做完
    (见 GK_OT_ApplyAllAdvisorActions)。
    """
    items = params.get("actions") if isinstance(params, dict) else None
    if not isinstance(items, list) or not items:
        return ("`propose_actions` 需要一个非空的 `actions` 数组"
                "(每项含 action / target / value / why)。"), []
    cards = []
    errors = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(f"第 {index} 条不是对象,已跳过")
            continue
        error, card = _validate_card(item)
        if error:
            errors.append(f"第 {index} 条:{error}")
            continue
        cards.append(card)
    if not cards:
        return ("这一批**一条都不合格**,没有卡片出现。问题:\n- "
                + "\n- ".join(errors)), []
    note = (f"已经把 {len(cards)} 张卡片一起展示给用户了 —— 他可以逐张点"
            f"『执行』,也可以一次『全部执行』。**不要**说已经改好了。")
    if errors:
        note += "\n这几条不合格(没生成卡片,请补对后可以再提):\n- " \
                + "\n- ".join(errors)
    return note, cards


def apply_proposal(context, card):
    """用户点『执行』之后,真正落地。返回 (成功?, 说明文字)。"""
    entry = ACTIONS.get((card or {}).get("action", ""))
    if entry is None:
        return False, "这个动作已经不存在了(可能插件版本变了)"
    params = {"target": (card or {}).get("target", ""),
              "value": (card or {}).get("value", ""),
              # 老卡片(第一百零六轮之前存的)没有 args 这一项 —— 给个空对象,
              # 动作自己按"没填 = 默认"处理
              "args": (card or {}).get("args") or {}}
    try:
        return entry["apply"](context, params)
    except Exception as exc:                              # noqa: BLE001
        return False, f"执行失败: {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# 只读工具的实现
# ---------------------------------------------------------------------------
def execute(name, params, context):
    """执行一个工具。返回 (喂回模型的文本, 待确认动作)。

    待确认动作这一项可能是:单张卡片(dict)/ 一批卡片(list)/ 没有(None)——
    `propose_actions` 是批量的(第九十轮),调用方按"列表"统一处理。

    **在主线程里调用**(只读工具要读 bpy 数据)。
    """
    handler = _READERS.get(name)
    if handler is None:
        if name == "propose_action":
            return build_proposal(params or {})
        if name == "propose_actions":
            return build_proposals(params or {})
        return (f"没有叫『{name}』的工具。可用:"
                f"{sorted(list(_READERS) + ['propose_action', 'propose_actions'])}"), None
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
             "GKDisplayTree": "显示设置", "GKAssetTree": "资产库",
             "GKMusicTree": "游戏音乐"}
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