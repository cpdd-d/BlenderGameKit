"""动画相关的发射器:播放、状态机、动画树、动画变量、补间。

动画状态机/动画树的「图」解析住在 `nodes/nodes.py`,这里只管把解析结果生成成 GDScript。"""

from ..nodes.node_base import TARGET_SELF, gd_node_path, gd_string, snake_case
from ..nodes.nodes import (ANIM_CATEGORY_NAMES, action_category,
                           parse_transitions, transition_pin_name)
from ..nodes import nodes as node_lib

from .literals import gd_literal, gd_number
from .registry import _chain, _next_nodes, expr_node, multi_output, stmt_node
from .statements import _tunable_number




def _animation_name_of(ctx, node):
    """这个动画节点要播的名字(已经转成 GDScript 字符串字面量)。

    优先级:
      1. **引脚连线**(上游给了个动态名字)—— 最高,说明是运行时决定的
      2. **原生动作指针** `gk_anim_action` —— 用户在 Blender 里选的数据块
      3. 文本框 `gk_anim_name` —— 手填兜底
      4. 都没有 -> `""`,由调用方转成"随便播一个"
    """
    socket = ctx._socket(node, "动画名")
    if socket is not None and getattr(socket, "is_linked", False):
        return ctx.expr_of(socket)
    action = getattr(node, "gk_anim_action", None)
    if action is not None:
        name = str(getattr(action, "name", "") or "")
        if name:
            return gd_string(name)
    return ctx.input_string(node, "动画名", "")


def _scene_fps():
    """当前场景帧率 —— 把用户填的"帧"换算成 Godot 要的"秒"。

    Blender 里说"第 30 帧"很自然,Godot 的 `seek` / `play_section` 收的
    却是秒。两边差一个帧率,所以导出时换算一次。
    拿不到场景(离线测试)就用 Blender 的默认 24。
    """
    try:
        import bpy
        render = getattr(getattr(bpy.context, "scene", None), "render", None)
        fps = float(getattr(render, "fps", 0.0) or 0.0)
        if fps > 0:
            return fps
    except Exception:
        pass
    return 24.0


def _anim_category_table():
    """{分类: [动作名, ...]} —— 用户打过分类标签的动作。

    分类是用户在『动作分类』面板里给 Action 打的自定义属性
    (见 nodes.ANIM_CATEGORY_KEY)。导出时把它整理成一张表,
    Godot 侧就能按分类挑动作(随机/顺序),不用手写名字。

    测试桩里的 bpy 没有 .data,这里返回空 —— 于是老图、离线测试
    生成的代码**一模一样**(不打标签就一行都不生成)。
    """
    try:
        import bpy
    except Exception:
        return {}
    actions = getattr(getattr(bpy, "data", None), "actions", None)
    if actions is None:
        return {}
    table = {}
    for action in actions:
        category = action_category(action)
        if not category:
            continue
        table.setdefault(category, []).append(str(action.name))
    for names in table.values():
        names.sort()
    return table


@stmt_node("GKNodePlayAnim")
def _stmt_play_anim(ctx, node, indent):
    """播动画 + 三个事件出口(Started / Running / On Finish)。

    ── 为什么要事件出口 ────────────────────────────────────────────
    UPBGE 的 Play Animation 节点最值钱的就是这三个输出 ——
    有它们才能做"播完接下一招""循环期间持续判定"这类逻辑。
    以前我们的节点只有"完成",接不了任何后续判断。

    ── 三个出口分别怎么实现 ────────────────────────────────────────
      * **开始时** -> `AnimationPlayer.animation_started` 信号
      * **结束时** -> `AnimationPlayer.animation_finished` 信号
      * **每帧**   -> Godot **没有**对应信号,只能每个 `_process` 查一次
                     `is_playing()` + `current_animation`

    信号那两个都带 `animation_name` 参数,所以回调里比对一下名字,
    避免"别的代码播了同一个播放器上的另一个动画"时误触发。
    """
    player = ctx.animation_player(node)
    # ── 层(上下半身分离动画)────────────────────────────────────────
    # layer ≥ 1 时**另起一个播放器**:复制原播放器当"上层"。
    # 两个播放器指向同一个骨架,谁**后处理**谁覆盖 —— 复制出来的那个是
    # 后加的子节点,处理顺序在后面,正好是"上半身盖住下半身"。
    # 前提:动作得是**按部位拆开**的(上半身动作只含上半身骨骼),
    # 所以是"用户把上半身动作挂 1 层,下半身动作留 0 层"这种用法。
    layer = int(getattr(node, "gk_layer", 0) or 0)
    if layer >= 1:
        ctx.need_layer_player = True
        player = f"_gk_layer_player({player}, {layer})"
    anim = _animation_name_of(ctx, node)
    speed = gd_number(getattr(node, "gk_speed", 1.0))
    # ── Blend-In / 起始帧 / 结束帧 ──────────────────────────────────
    # 都以 0 / -1 为"不生效",老图生成的代码完全不变。
    blend = float(getattr(node, "gk_blend_in", -1.0) or 0.0)
    blend_expr = gd_number(blend) if blend > 0 else "-1.0"
    start_frame = float(getattr(node, "gk_start_frame", 0.0) or 0.0)
    end_frame = float(getattr(node, "gk_end_frame", 0.0) or 0.0)
    # Godot 收的是**秒**,Blender 里填的是**帧** —— 按场景帧率换算。
    fps = _scene_fps()
    start_sec = start_frame / fps if start_frame > 0 else 0.0
    end_sec = end_frame / fps if end_frame > 0 else 0.0
    # 『续播』= 同名动画正在播就不重新开始(避免连续触发时鬼畜重播)
    keep_playing = str(getattr(node, "gk_blend_mode", "REPLACE")) == "CONTINUE"
    name_temp = ctx.new_temp("anim")
    player_temp = ctx.new_temp("ap")
    ctx.add_line(indent, f"var {player_temp} := {player}")
    # ── 名字留空时**不要**生成 has_animation("")──────────────────────
    # 节点默认值改成空之后(见 nodes.py 的说明),用户不动它就会留下空串。
    # 直接生成 `has_animation("")` 是没意义的 —— 永远 false,
    # 只会打一条"动画不存在: "的空警告,用户还是一头雾水。
    #
    # 改成:留空 = "随便播一个"。很多模型身上只有一个动画
    # (用户的胡桃有俩,但单个动作的模型更常见),这样开箱即用。
    if anim == '""':
        # 让 generate() 记得生成 _gk_first_animation() 这个辅助函数。
        # **只在这条分支里设** —— 无条件设的话,
        # 填了名字的节点也会白白带一个用不到的辅助函数。
        ctx.need_first_animation = True
        ctx.add_line(indent, f"var {name_temp} := _gk_first_animation({player_temp})")
    else:
        ctx.add_line(indent, f"var {name_temp} := {anim}")

    # ── 先探测三个事件出口有没有接线,并登记 ────────────────────────
    # 顺序很重要:`_emit_event_connections` 是遍历 `ctx.anim_events`
    # 来写 connect 行的,登记晚了就一行都不写(实测踩过)。
    wanted = []
    for output_name, suffix in (("开始时", "started"),
                                ("每帧", "running"),
                                ("结束时", "finished")):
        if _next_nodes(node, output_name):
            wanted.append((output_name, suffix))
    # 每个出口一个**独立**编号 —— 曾经两个出口共用一个编号,
    # 生成出两个同名函数(一个覆盖另一个,用户的"结束时"逻辑直接消失)。
    numbered = []
    for output_name, suffix in wanted:
        unique = ctx.new_temp("ae")
        handler = f"_gk_{suffix}_{unique}"
        # 变量名:`_gk_running__ae3` -> `_gk_run__ae3`。
        # 不这么剥的话会拼出 `_gk_run__gk_running__ae3` 这种又长又脏的名字。
        plain = handler[len(f"_gk_{suffix}_"):]
        signal_name = None
        if suffix != "running":
            signal_name = ("animation_started" if suffix == "started"
                           else "animation_finished")
        var_name = f"_gk_run_{plain}"
        numbered.append((suffix, handler, signal_name, var_name))
        ctx.anim_events.append({
            "player": player_temp, "name": name_temp,
            "func": handler, "node": node, "output": output_name,
            "indent": indent,
            "kind": "running" if suffix == "running" else "signal",
            "signal": signal_name,
            "var": var_name,
        })
    if numbered:
        # 有任何事件出口就要生成信号回调函数
        ctx.need_anim_signals = True
        # "每帧"那个还需要在 _process 里插一段检查(Godot 没有对应信号)
        if any(suffix == "running" for suffix, _h, _s, _v in numbered):
            ctx.need_anim_running = True

    # ---- 播放 + 接线 ----
    ctx.add_line(indent, f"if {player_temp} != null and "
                         f"{player_temp}.has_animation({name_temp}):")
    # 『续播』模式:同名动画正在播就整段跳过(连信号都不重接)。
    body = indent + 1
    if keep_playing:
        ctx.add_line(indent + 1,
                     f"if {player_temp}.current_animation != {name_temp} "
                     f"or not {player_temp}.is_playing():")
        body = indent + 2
    if end_sec > 0:
        # 有结束帧 -> 用 play_section 一次把 起始/结束/Blend-In/速度 全给它。
        # (Godot 4.3+ 的 API:`play_section(名, 起, 止, 混合, 速度)`)
        ctx.add_line(body,
                     f"{player_temp}.play_section({name_temp}, "
                     f"{gd_number(start_sec)}, {gd_number(end_sec)}, "
                     f"{blend_expr}, {speed})")
    else:
        ctx.add_line(body,
                     f"{player_temp}.play({name_temp}, {blend_expr}, {speed})")
        if start_sec > 0:
            # 只设了起始帧、没设结束帧 -> play 之后 seek 到起点。
            # 第二个参数 true = 立刻刷新姿势,不然要等下一帧才跳过去。
            ctx.add_line(body,
                         f"{player_temp}.seek({gd_number(start_sec)}, true)")
    # ── 接线必须在**成功分支内**、紧挨着 play()(踩过的坑)──────────
    # 上一版把 connect 写在下面的 `else:` 里 —— 那是"动画不存在"的分支,
    # 于是信号只在**播放失败**时接上,正常播放永远不接。
    # 用 CONNECT_ONE_SHOT:触发一次自动断开,重复播放不会累积连接。
    for _suffix, handler, signal_name, _var in numbered:
        if signal_name is None:
            continue
        ctx.add_line(body,
                     f"{player_temp}.{signal_name}.connect({handler}, "
                     f"CONNECT_ONE_SHOT)")
    ctx.add_line(indent, "else:")

    # 动画不存在时的警告
    ctx.add_line(indent + 1, f"if {player_temp} == null:")
    ctx.add_line(indent + 2,
                 'push_warning("[GK] 没找到 AnimationPlayer,播不了动画: %s" % '
                 f"{name_temp})")
    ctx.add_line(indent + 1, "else:")
    # 名字填错时,把**可用的动画名**打出来。实测踩过:用户从 MMD
    # 导入的模型,动作叫『骨骼动作』,而节点里默认填 "idle" ——
    # 只报一句"动画不存在: idle",用户根本不知道正确的名字是什么。
    ctx.add_line(indent + 2,
                 'push_warning("[GK] 动画不存在: %s。这个模型里可用的动画有: %s" % '
                 f"[{name_temp}, {player_temp}.get_animation_list()])")


@multi_output("GKNodeAnimLocomotion")
def _expr_anim_locomotion(ctx, node):
    """移动相关的一组布尔判断,一次给全。

    -- 为什么要这个节点 --------------------------------------------
    动画切换里 90% 的条件就是那几个:"到地面了吗""在动吗""在往下掉吗"。
    没有它的话用户要为每个条件单独拉一个表达式节点再接引脚,
    状态一多整张图全是线。

    -- 为什么**不生成中间变量**(踩过的坑)--------------------------
    第一版为了不重复算速度,生成了 4 行中间变量。结果导出的代码报:
        Identifier "_lk2_speed" not declared in the current scope
    因为表达式是在 _ready() 的**局部作用域**里求值的,而这些表达式
    被 _physics_process 里的状态判断引用 —— 跨函数了。

    而且按输出引脚求值会被调用 8 次,每次都要一个不同的临时名,
    8 组变量散在代码里也很难看。

    所以改成**全部内联**:每个输出就是一个纯表达式,不落地成变量。
    代价是速度会被算多次,但 Vector2(...).length() 是纳秒级操作,
    每帧几次完全无所谓 —— 换来的是生成的代码干净、且不会作用域串味。
    """
    # -- 声明基类需求(踩过的坑)----------------------------------
    # 这个节点用 `is_on_floor()` 和 `velocity` -- **只有 CharacterBody
    # 才有**。用户实测:物体是静态的 -> 脚本 `extends StaticBody3D`
    # -> Godot 报 `Function "is_on_floor()" not found in base self.`
    #
    # 登记下来,导出时统一校验并纠正(见 check_base_class)。
    ctx.require_base_class(
        "CharacterBody2D" if ctx.dim == 2 else "CharacterBody3D",
        "『动画判断_移动状态』要用 is_on_floor() / velocity")
    target = ctx.target_expr(node)
    # 未连线的数值 -> @export 参数,方便在 Godot 检查器里调手感
    slow = _tunable_number(ctx, node, "速度阈值", "0.1", "move_threshold")
    fast = _tunable_number(ctx, node, "跑的速度阈值", "3.0", "run_threshold")
    if ctx.dim == 2:
        speed = f"Vector2({target}.velocity.x, 0.0).length()"
    else:
        speed = (f"Vector2({target}.velocity.x, "
                 f"{target}.velocity.z).length()")
    ground = f"{target}.is_on_floor()"
    moving = f"({speed} > {slow})"
    up = f"({target}.velocity.y > 0.0)"
    return {
        "在地面": f"({ground})",
        "在移动": f"({moving})",
        "地面且移动": f"(({ground}) and {moving})",
        "已离地": f"(not ({ground}))",
        "在上升": f"({up})",
        "在下落": f"(not {up})",
        "速度快": f"({speed} > {fast})",
        "速度慢": f"({speed} <= {fast})",
    }


# ==========================================================================
# 动画树的自定义变量(侧栏『变量』那一栏 / 取变量 / 设变量节点)
# ==========================================================================
# 变量在 Blender 侧存在动画树上(见 properties.GKAnimVariable),
# 导出后就是脚本里的一个成员变量。这里负责三件事:
#   1. 把侧栏里写的类型/初值翻成 GDScript(`var hp: float = 100.0`)
#   2. 使用这张动画树时,**所有**声明的变量都落地(哪怕还没节点用它)
#   3. 『动画_取变量』求值成变量名,『动画_设变量』生成一行赋值
_ANIM_VAR_TYPES = {
    "bool": ("bool", "false"),
    "int": ("int", "0"),
    "float": ("float", "0.0"),
    "string": ("String", '""'),
    "vector": ("Vector3", "Vector3.ZERO"),
}


def _anim_var_spec(tree, name):
    """在动画树的变量表里按名字找一个变量(找不到返回 None)。"""
    for var in (getattr(tree, "gk_variables", ()) or ()):
        if str(getattr(var, "var_name", "") or "") == name:
            return var
    return None


def _anim_var_initial(kind, text):
    """把侧栏里填的『初值』文本转成合法的 GDScript 字面量。

    用户填的是自由文本(和『比较』节点上的 A/B 一个道理),
    所以这里按类型各自做一次转换;转不动就退回该类型的零值,
    绝不让"文本里多打一个字符"变成 Godot 的语法错误。
    """
    raw = str(text or "").strip()
    if kind == "bool":
        return "true" if raw.lower() in ("1", "true", "真", "是", "yes") else "false"
    if kind == "int":
        try:
            return str(int(float(raw)))
        except Exception:
            return "0"
    if kind == "float":
        try:
            return gd_number(float(raw))
        except Exception:
            return "0.0"
    if kind == "vector":
        return raw or "Vector3.ZERO"
    return gd_string(str(text or ""))


def _anim_var_ref(ctx, node):
    """把一个『动画_取变量 / 设变量』节点解析成成员变量名。"""
    raw = str(getattr(node, "gk_var_name", "") or "").strip()
    if not raw:
        ctx.warn(f"节点『{node.bl_label}』还没选变量 —— "
                 f"先在侧栏『变量』里加一个,再点它那行的『取』/『设』"
                 f"(或者用节点上的放大镜挑)")
        return "null"
    spec = _anim_var_spec(getattr(node, "id_data", None), raw)
    if spec is None:
        # 侧栏里没声明过这个名字 —— 退回"按名字自动声明"的老行为,
        # 让手工打的名字也还能用(只是没有类型和初值)
        return ctx.declare_var(raw)
    kind = str(getattr(spec, "var_type", "float") or "float")
    gd_type, fallback = _ANIM_VAR_TYPES.get(kind, ("Variant", "null"))
    initial = _anim_var_initial(kind, getattr(spec, "default_value", ""))
    return ctx.declare_var(raw, gd_type, initial=initial or fallback)


def _declare_anim_vars(ctx, tree):
    """把一张动画树里声明的变量**全部**落成成员变量。

    和 `_anim_var_ref` 的区别:这里不管有没有节点引用 ——
    侧栏里写下的变量就该在脚本里存在(和蓝图一样,变量属于这一"个"
    动画蓝图,不是"被谁用到才存在")。
    """
    for spec in (getattr(tree, "gk_variables", ()) or ()):
        raw = str(getattr(spec, "var_name", "") or "").strip()
        if not raw:
            continue
        if not raw.isascii():
            # GDScript 的标识符只认 ASCII —— 中文名会被压成下划线。
            # 用户在这套中文界面里几乎一定会填中文名,不提醒的话
            # 导出结果里只会剩个 `var v`,而且多个中文名会挤成同一个变量。
            ctx.warn(f"动画树变量『{raw}』不是英文名 —— GDScript 不支持中文"
                     f"变量名,导出后会写成 `{snake_case(raw, 'v')}`。"
                     f"请在侧栏『变量』里改成英文(比如 hp、is_invincible)")
        kind = str(getattr(spec, "var_type", "float") or "float")
        gd_type, fallback = _ANIM_VAR_TYPES.get(kind, ("Variant", "null"))
        initial = _anim_var_initial(kind, getattr(spec, "default_value", ""))
        ctx.declare_var(raw, gd_type, initial=initial or fallback)


@expr_node("GKNodeAnimFinished")
def _expr_anim_finished(ctx, node):
    """某段动画播完了吗 —— 给转场条件 / 玩法图用。

    值来自 `animation_finished` 信号记下的名字(见 anim_finished_flag):
    动画名留空 = 只要有任意动画播完就算真 —— 这样用户不填也能用。
    """
    finished_var, _player = ctx.connect_anim_finished()
    action = getattr(node, "gk_anim_action", None)
    name = str(getattr(action, "name", "") or "") if action is not None else ""
    if not name:
        name = str(getattr(node, "gk_anim_name", "") or "").strip()
    if not name:
        return f"(not {finished_var}.is_empty())"
    return f"({finished_var} == {gd_string(name)})"


@multi_output("GKNodeAnimStatus")
def _expr_anim_status(ctx, node):
    """『动画_运行状态』:把动画图的布尔情况交给玩法图判断。

    三个输出一次给全(用 multi_output,和『动画判断_移动状态』同一个做法)——
    这样同一个节点上取三个值也不会重复求值。
    """
    state_var = ctx.declare_var(
        snake_case(getattr(node, "gk_state_var", "anim_state"), "anim_state"),
        "String")
    # 闸门变量按**状态变量名**取 —— 有两台状态机时,
    # 读 `anim_state_2` 的那台就该看 `_gk_anim_done_2`,不能看错人家的
    done = ctx.declare_var("_gk_anim_done", "bool", initial="false",
                           key=state_var)
    started = ctx.declare_var("_gk_anim_started", "bool", initial="false",
                              key=state_var)
    return {
        "已结束": done,
        "在跑": started,
        "当前状态": state_var,
    }


@expr_node("GKNodeAnimGetVar")
def _expr_anim_get_var(ctx, node):
    return _anim_var_ref(ctx, node)


@stmt_node("GKNodeAnimSetVar")
def _stmt_anim_set_var(ctx, node, indent):
    name = _anim_var_ref(ctx, node)
    socket = ctx._socket(node, "值")
    if socket is not None and getattr(socket, "is_linked", False):
        value_expr = ctx.expr_of(socket)
    else:
        value = node.input_value(ctx, "值", None)
        value_expr = gd_literal(value) if value is not None else "null"
    ctx.add_line(indent, f"{name} = {value_expr}")


@stmt_node("GKNodeAnimParam")
def _stmt_anim_param(ctx, node, indent):
    """把整个模型的**形态键**统一设一个值(口型 / 表情)。

    ── 这个节点以前是"死的"(第三十九轮修)────────────────────────────
    卡片上画着『值』和『形态键序号』,但 codegen 里**从来没有**它的处理
    函数 —— 用户说的"一部分节点卡片无作用"就是它:拖进图里、连上线,
    导出后一个字符都不生成,表情参数永远不动。

    Godot 侧就一句 `MeshInstance3D.set_blend_shape_value(序号, 值)`;
    麻烦的是**找网格**:glTF 导入的网格藏在 `Model/` 实例内部,所以要
    递归找,找不到再退回整棵场景树(和『动画_播放』找播放器同一个套路)。
    两个都找不到时**报一次**警告 —— 静默不动是最难查的那种"没反应"。
    """
    value = ctx.input_number(node, "值",
                             gd_number(getattr(node, "gk_value", 1.0)))
    index = int(getattr(node, "gk_shape_index", 0) or 0)
    warned = ctx.declare_var("_gk_shape_warned", "bool", initial="false")
    meshes = ctx.new_temp("shape_meshes")
    body = ctx.new_temp("shape_mesh")
    scene_var = ctx.new_temp("shape_host")
    ctx.add_line(indent, f"var {meshes} := self.find_children("
                         f"\"*\", \"MeshInstance3D\", true, false)")
    ctx.add_line(indent, f"if {meshes}.is_empty():")
    ctx.add_line(indent + 1, f"var {scene_var} := "
                             f"get_tree().current_scene if get_tree() != null else null")
    ctx.add_line(indent + 1, f"if {scene_var} != null:")
    ctx.add_line(indent + 2, f"{meshes} = {scene_var}.find_children("
                             f"\"*\", \"MeshInstance3D\", true, false)")
    ctx.add_line(indent, f"if {meshes}.is_empty():")
    ctx.add_line(indent + 1, f"if not {warned}:")
    ctx.add_line(indent + 2, f"{warned} = true")
    ctx.add_line(indent + 2, 'push_warning("[GK] 场景里没有 MeshInstance3D,'
                             '『动画_设混合参数』改不了形态键")')
    ctx.add_line(indent, f"for {body} in {meshes}:")
    ctx.add_line(indent + 1, f"{body}.set_blend_shape_value({index}, {value})")


def _anim_speed_func(ctx, graph, node_lib):
    """"状态名 -> 播放速度"的辅助函数(第三十九轮)。

    ── 为什么做成一个函数(而不是把速度内联进 play 调用)────────────
    速度是**每个状态各自的**,而 `_gk_anim_enter(next_state)` 里拿到的是
    一个**状态名的字符串** —— 要按名字取速度,一个 `match` 函数最短:

        func _gk_anim_speed(state_name: String) -> float:
            match state_name:
                "run":
                    return 1.4
            return 1.0

    ── 为什么全 1.0 时一个字符都不生成 ─────────────────────────────
    默认就是 1.0(原速)。老图(没人动过速度)必须生成得和以前**一字不差**,
    所以没有任何非 1.0 的速度时返回 None,调用方退回原来的 `play(名字)`。
    """
    speeds = {}
    for state in graph["states"]:
        name = node_lib.anim_state_name(state)
        if not name:
            continue
        try:
            value = float(getattr(state, "gk_speed", 1.0) or 1.0)
        except (TypeError, ValueError):
            value = 1.0
        if abs(value - 1.0) > 1e-9:
            speeds[name] = value
    if not speeds:
        return None
    key = tuple(sorted(speeds.items()))
    cache = getattr(ctx, "_anim_speed_funcs", None)
    if cache is None:
        cache = {}
        ctx._anim_speed_funcs = cache
    if key in cache:
        return cache[key]
    func_name = ctx.declare_func_name("_gk_anim_speed", key=key)
    cache[key] = func_name
    body = ["    match state_name:"]
    for name in sorted(speeds):
        body.append(f"        {gd_string(name)}:")
        body.append(f"            return {gd_number(speeds[name])}")
    body.append("    return 1.0")
    ctx.extra_sections.append({
        "signature": "func " + func_name + "(state_name: String) -> float:",
        "lines": body,
        "empty_body": "    pass",
        "separator": True,
    })
    return func_name


def _emit_state_hooks(ctx, graph, player_temp, speed_func=None):
    """给每个"有事要做"的状态生成『进入时』『离开时』函数。

    返回 ({状态名: 进入函数名}, {状态名: 离开函数名})。

    一个状态需要函数,只要满足任意一条:
      * 『进入时』引脚接了线(用户要在这个状态里执行语句)
      * 『离开时』引脚接了线
      * 它填了『同时播 2 / 同时播 3』(要额外开播放器放那两段动画)
    """
    enter_hooks = {}
    exit_hooks = {}
    max_layers = 0
    for state in graph["states"]:
        name = node_lib.anim_state_name(state)
        if not name:
            continue
        extras = []
        getter = getattr(state, "extra_animation_names", None)
        if callable(getter):
            extras = [extra for extra in getter() if extra and extra != name]
        has_enter = bool(node_lib.link_targets(state, "进入时"))
        has_exit = bool(node_lib.link_targets(state, "离开时"))
        if not (has_enter or has_exit or extras):
            continue
        safe = snake_case(name, "state")

        if has_enter or extras:
            safe = snake_case(name, "state")
            with ctx.capture() as lines:
                ctx._active.clear()
                ctx._current_event = f"状态{name}进入时"
                for index, extra in enumerate(extras):
                    # 同时播的第 N 段:开一个"层"播放器(Godot 的 AnimationPlayer
                    # 一次只能播一段,play() 会把上一段顶掉)
                    layer = index + 1
                    max_layers = max(max_layers, layer)
                    ctx.need_layer_player = True
                    temp = ctx.new_temp("apx")
                    lines.add(1, f"var {temp} = _gk_layer_player({player_temp}, "
                                 f"{layer})")
                    lines.add(1, f"if {temp} != null and "
                                 f"{temp}.has_animation({gd_string(extra)}):")
                    if speed_func:
                        # 同时播的那几段跟着**本状态**的速度走
                        lines.add(2, f"{temp}.play({gd_string(extra)}, -1.0, "
                                     f"{speed_func}({gd_string(name)}))")
                    else:
                        lines.add(2, f"{temp}.play({gd_string(extra)})")
                if has_enter:
                    _chain(ctx, state, 1, "进入时")
                if len(lines) == 0:
                    lines.add(1, "pass")
            func_name = ctx.declare_func_name(f"_gk_anim_enter_{safe}")
            ctx.extra_sections.append({
                "signature": f"func {func_name}() -> void:",
                "lines": list(lines.lines),
                "empty_body": "    pass",
                "separator": True,
            })
            enter_hooks[name] = func_name

        if has_exit:
            safe = snake_case(name, "state")
            with ctx.capture() as lines:
                ctx._active.clear()
                ctx._current_event = f"状态{name}离开时"
                _chain(ctx, state, 1, "离开时")
                if len(lines) == 0:
                    lines.add(1, "pass")
            func_name = ctx.declare_func_name(f"_gk_anim_exit_{safe}")
            ctx.extra_sections.append({
                "signature": f"func {func_name}() -> void:",
                "lines": list(lines.lines),
                "empty_body": "    pass",
                "separator": True,
            })
            exit_hooks[name] = func_name
    # 记住最多有几层"同时播",_gk_anim_enter 里要按这个数量停旧层
    ctx._anim_layer_count = max_layers
    return enter_hooks, exit_hooks


def _start_condition(ctx, graph, node_lib):
    """『动画_开始』的开始条件表达式(没有开始口就返回 None)。

    ── 为什么开始条件要有"可选"的写法(用户 2026-10-03)──────────────
    用户原话:"游戏动画开始时的条件可自定义,如发生什么,一个变量,
    一个动画结束,或者是自定义开始动画条件"。
    所以这里按『开始方式』分派:立刻 / 接引脚 / 变量为真 / 刚按下 / 按住 /
    上一个动画播完。后四种都不用接线,直接在节点上选。
    """
    start = graph.get("start") if graph else None
    if start is None:
        return None
    mode = str(getattr(start, "gk_start_mode", "NOW"))
    if mode == "NOW":
        return "true"
    if mode == "PIN":
        socket = ctx._socket(start, "条件")
        if socket is None or not getattr(socket, "is_linked", False):
            ctx.warn("『动画_开始』的开始方式是『自己写』,但『条件』引脚"
                     "没接线 —— 这张动画图不会开始。接一个判断节点上去,"
                     "或者把开始方式改成『立刻开始』")
            return None
        return ctx.expr_of(socket)
    if mode == "VAR":
        name = str(getattr(start, "gk_var_name", "") or "").strip()
        if not name:
            ctx.warn("『动画_开始』选了『变量为真』但没填变量名")
            return None
        return ctx.declare_var(name, "bool")
    if mode in ("ACTION_JUST", "ACTION_PRESSED"):
        action = str(getattr(start, "gk_cond_action", "") or "").strip()
        if not action:
            ctx.warn("『动画_开始』选了按键开始但没填输入动作")
            return None
        const = ctx.add_action(action)
        func = ("is_action_just_pressed" if mode == "ACTION_JUST"
                else "is_action_pressed")
        return f"Input.{func}({const})"
    if mode == "FINISH":
        finished_var, _player = ctx.connect_anim_finished()
        name = str(getattr(start, "gk_anim_name", "") or "").strip()
        if not name:
            return f"(not {finished_var}.is_empty())"
        return f"({finished_var} == {gd_string(name)})"
    return "true"


@stmt_node("GKNodeAnimStateMachineRef")
def _stmt_anim_tree_ref(ctx, node, indent):
    """驱动一张**动画树** —— 玩法图和动画图分开放。

    见 nodes.py 里 GKNodeAnimState 那段的说明(用户要求照虚幻 5 的
    Animation Blueprint 那样拆开,别都堆在一个节点里)。

    ── 这一版改了什么(2026-10-03)────────────────────────────────
    老实现是"从入口状态沿一条链走一圈",只有三个能工作的形状:
    一条直链、一个环、或者什么都没有。用户实际的图(状态1 的『转到』
    直接连到状态2 的『进入』)导出后只剩一句 `pass`。现在改成**图**:

        entry --播完--> B                (状态直连,见 nodes.anim_tree_graph)
        A --[转场:条件]--> B             (转场节点)
        A --[转场:按住某键]--> C         (同一个状态多条出线)

    生成的形状:

        func _gk_anim_enter(next_state: String) -> void:
            anim_state = next_state
            ...真的 play()

        func _physics_process(_delta):
            if anim_state == "A":
                if <条件1>: _gk_anim_enter("B")
                elif <条件2>: _gk_anim_enter("C")
            elif anim_state == "B":
                ...

    三个关键点:
      * **入口状态真的会播** —— 老代码只写 `anim_state = "骨骼动作"`,
        从头到尾没有一处 play(),所以角色进游戏后一直摆着不动。
      * **同一个状态多条出线** —— 按画布上下顺序 if/elif,第一条成立就切。
      * **直连 = 播完就切** —— 用户就是这么连的(见 nodes.anim_tree_graph)。
    """
    tree = node_lib.find_animation_tree(getattr(node, "gk_tree_name", ""))
    # 这张动画树上声明的自定义变量全部落地成成员变量 ——
    # 即使暂时还没有『动画_取变量』节点引用它。侧栏里写下的东西
    # 必须在导出的脚本里真实存在,否则用户在 Godot 里根本找不到它。
    if tree is not None:
        _declare_anim_vars(ctx, tree)
    graph = node_lib.anim_tree_graph(tree) if tree is not None else None
    if graph is None or graph["entry"] is None:
        ctx.warn(f"节点『{node.bl_label}』找不到可用的动画状态机"
                 f"{'『' + node.gk_tree_name + '』' if node.gk_tree_name else ''}"
                 f"。先在『游戏动画』面板里建一张动画树,放一个『动画_状态』"
                 f"(没有任何转场指向它 = 起始状态),再把它连到下一个状态"
                 f"(或者中间放一个『动画_转场』写条件)")
        return
    # 树自己解析出来的问题,一条条转成导出告警 ——
    # 用户看的是导出报告,所以这里必须**原样带出去**,而不是只画在面板上。
    for problem in graph["problems"]:
        ctx.warn(problem)

    # ── 播放器:一定是**脚本所在物体**上的那个 ────────────────────────
    # 用户 2026-10-03 要求删掉『动画_状态机』的物体选择("在此物体选择
    # 动画_状态机时自然是作用于本身")。所以这里 use_target=False ——
    # 连老文件里存着的那个已废弃物体名也不认,免得它偷偷复活,
    # 生成一条指向别的物体的路径。
    player = ctx.animation_player(node, use_target=False)
    # ── 状态变量**按节点**独立(用户 2026-10-03:"一个游戏逻辑里是否可以
    #    有多个游戏动画?")────────────────────────────────────────────
    # 以前**不行**:两张动画树都写进同一个 `anim_state`、共用同一个
    # `_gk_anim_enter`,后一张把前一张整个盖掉 ——
    # 实测:两个『动画_状态机』节点只生成了一套状态机。
    # 按节点当 key 声明之后,第二台自动叫 `anim_state_2`,互不干扰。
    # ── 状态变量:按**名字**共享(而不是自动加序号)──────────────────
    # 试过"每台状态机自动 anim_state / anim_state_2":两台机器确实不打架了,
    # 但『动画_运行状态』(按名字读 anim_state)就对不上了 —— 它读到的是
    # 别人那一份,状态永远显示错。
    #
    # 现在:名字就是身份。一台机器占一个名字;第二台还想用同一个名字时
    # **明确告警**,告诉用户去改『状态变量名』(比如 ui_state)。
    # 这样"一台机器 + 一个读取节点"这种 99% 的用法永远是对的,
    # 多台的时候也只需要用户动一次手、而且看得见原因。
    requested = snake_case(getattr(node, "gk_state_var", "anim_state"),
                           "anim_state")
    owners = getattr(ctx, "_state_var_owners", None)
    if owners is None:
        owners = {}
        ctx._state_var_owners = owners
    mine = node_lib.node_key(node)
    if requested in owners and owners[requested] != mine:
        ctx.warn(f"有两台『动画_状态机』都用『状态变量名 = {requested}』—— "
                 f"它们会互相覆盖(动画会乱跳)。把其中一台的『状态变量名』"
                 f"改个名字(比如 ui_state),然后『动画_运行状态』也填同一个")
    owners[requested] = mine
    var_name = ctx.declare_var(requested, "String")
    # -- 播放器引用必须是**成员变量**,不能是局部变量(踩过的坑)------
    # 状态判断跑在 _physics_process 里,而 _ready 里也用它。
    # 用 new_temp()(局部变量)会炸:
    #     Identifier "_ap1" not declared in the current scope
    # 实测用户导出后报了这个错。
    #
    # 而且只在 _ready 里赋值一次就够了(它不会变),
    # 所以赋值语句只在 _ready 那次执行时生成,避免每个物理帧都查一遍。
    player_temp = ctx.anim_player_member()
    if not getattr(ctx, "_anim_player_assigned", False):
        ctx._anim_player_assigned = True
        # 用 `=` 不用 `:=`:成员变量已经在类顶部声明过 `var _ap0 = null`,
        # 再写 `var _ap0 := ...` 就是**同一作用域里重复声明**,
        # Godot 会报 "There is already a variable named ..."。
        #
        # 带 `if == null` 的守卫是为了和 generate() 里补的那次赋值**幂等**:
        # 用户没接『动画_播放完成』时只有这里赋一次;接了的话 _ready 里
        # 还会补一次(那时玩家图里可能先用到它),两次都不该重复赋值。
        ctx.add_line(indent, f"if {player_temp} == null:")
        ctx.add_line(indent + 1, f"{player_temp} = {player}")

    # ── "上游动画播完"要靠信号,不能靠 current_animation(实测踩过)────
    # 详见 anim_finished_flag 里的实测数据。只有图里真的有"播完"边
    # 才生成这套东西(没用到就不占行数)。
    has_finish = any(edge["kind"] == node_lib.ANIM_EDGE_FINISH
                     for edge in graph["edges"])
    finished_var = None
    if has_finish:
        tree_name = getattr(tree, "name", "动画树")
        # 只声明标志;信号连接由 generate() 统一补进 _ready(见那里的说明)
        finished_var = ctx.anim_finished_flag(tree_name)[0]

    # ── 状态自己的执行流 + 同时播多段动画(2026-10-03 新增)──────────
    # 每个"有事要做"的状态生成一个函数:
    #     _gk_anim_enter_攻击()   ← 『进入时』接的语句 + 同时播的第 2/3 段动画
    #     _gk_anim_exit_攻击()    ← 『离开时』接的语句
    # 然后 _gk_anim_enter() 里按状态名分派(match)。
    #
    # ── 为什么不把语句直接内联进 _gk_anim_enter ──────────────────
    # 那几个分支的长度完全由用户的图决定,内联会让"进入状态"这个唯一入口
    # 变成一大坨;拆成函数之后,每个状态自己的逻辑在脚本里也是**分开的一块**。
    # ── 状态速率(第三十九轮):有状态填了非 1.0 才生成速度函数 ──────
    speed_func = _anim_speed_func(ctx, graph, node_lib)
    enter_hooks, exit_hooks = _emit_state_hooks(ctx, graph, player_temp,
                                                speed_func=speed_func)

    debug = bool(getattr(node, "gk_debug_print", False))
    # ── 进入状态统一走一个函数 ──────────────────────────────────────
    # 老代码每处切换都内联四行(`if has_animation` / `play` / `else` /
    # `push_warning`),想加一个"进入时打印"要改 N 处。收成一个函数之后,
    # 状态切换只有**一个**生成点,而且"进入状态"这件事有了唯一的定义。
    enter_func = ctx.enter_anim_state_func(node, var_name, player_temp, debug,
                                           finished_var,
                                           enter_hooks=enter_hooks,
                                           exit_hooks=exit_hooks,
                                           speed_func=speed_func)

    # 起始状态 = 图里**没有任何边指向**的那个状态(照 UE5 的 Entry 节点);
    # 放了『动画_开始』就以它连的那个为准(显式指定,不用猜)。
    entry_name = node_lib.anim_state_name(graph["entry"])
    # ── 开始口 / 结束口(用户 2026-10-03)──────────────────────────
    # 有这两个口之一时,这张图变成"有始有终"的:条件成立才开始,
    # 走到『动画_结束』就停下并把『已结束』置真 —— 玩法图用
    # 『动画_运行状态』读它,接着做下一步逻辑。
    # 两个口都没有的老图**生成结果一字不变**(见下面的 else 分支)。
    start_node = graph.get("start")
    start_expr = _start_condition(ctx, graph, node_lib)
    has_start = start_node is not None and start_expr is not None
    has_exit = any(edge["kind"] == node_lib.ANIM_EDGE_EXIT
                   for edge in graph["edges"])
    started = done = None
    finish_func = None
    if has_start or has_exit:
        # 闸门变量也按状态变量区分 —— 否则两台机器会互相把对方的
        # "开始/结束"标志清掉(一个播完另一个也跟着收工)
        started = ctx.declare_var("_gk_anim_started", "bool",
                                  initial="false", key=var_name)
        done = ctx.declare_var("_gk_anim_done", "bool",
                               initial="false", key=var_name)
        if has_exit:
            finish_func = ctx.anim_finish_func(started, done, var_name,
                                               player_temp, graph)
    if has_start:
        # 条件每物理帧判一次 —— 一开始就判的话,条件当时不成立就再也没机会
        ctx.anim_starts.append({
            "node": node, "expr": start_expr, "var": started, "done": done,
            "enter": enter_func, "entry": entry_name,
        })
        ctx.need_anim_start_process = True
    elif has_exit:
        # 没有开始口但有结束口:第一帧直接开演,但**演完不会自己重来**
        # (要有开始口 + 条件才会重来)
        ctx.add_line(indent, f"if not {started} and not {done}:")
        ctx.add_line(indent + 1, f"{started} = true")
        ctx.add_line(indent + 1, f"{done} = false")
        ctx.add_line(indent + 1, f"{enter_func}({gd_string(entry_name)})")
    else:
        # 两个口都没有(老图):一进游戏就播起始状态,和以前一模一样
        ctx.add_line(indent, f"if {var_name}.is_empty():")
        ctx.add_line(indent + 1, f"{enter_func}({gd_string(entry_name)})")

    if str(getattr(node, "gk_check_mode", "EVERY_FRAME")) == "EVERY_FRAME":
        # 交给 generate() 统一发射到 _physics_process —— 跟移动同步判定,
        # 避免"人走出去了状态还没切"的一帧延迟(和相机跟随同一个道理)。
        ctx.anim_trees.append({
            "node": node, "graph": graph, "var": var_name,
            "player": player_temp, "enter": enter_func, "debug": debug,
            "finished": finished_var, "gate": started,
            "finish": finish_func,
        })
        ctx.need_anim_tree_process = True
        return
    _emit_state_graph(ctx, indent, graph, var_name, player_temp, enter_func,
                      finished_var, finish_func, started)


def _find_animation_tree(name):
    """按名字找一张动画树;名字为空就用文件里第一张。

    判断只有一份,在 `nodes.find_animation_tree` —— 面板、节点上的预览、
    导出三处必须看到同一张树,不然会出现"面板说用 A、导出却用了 B"。
    """
    return node_lib.find_animation_tree(name)


def _state_label(state):
    """状态显示名,给告警用。

    真名判断在 `nodes.anim_state_name`(优先用选中的动作名,否则用手填的)——
    这里保留一个同名的转发,是因为早期写下的诊断脚本/测试都在调它。
    """
    return node_lib.anim_state_name(state) or getattr(state, "bl_label", "?")


def _anim_tree_entry(tree):
    """找状态机的**入口状态** —— 照 UE5 的 Entry 节点。

    -- 判断规则:**没有任何一条转场指到**的状态才是入口 ------------
    (老注释里写的"『进入』引脚没接线就是入口"是错的:环里的状态
    同时满足两个身份,实测 `idle -> run -> idle` 里 run 也被判成了入口。)

    保留这个函数是因为侧栏面板和冒烟测试都在用它 —— 但判断本身已经
    挪进 `nodes.anim_tree_graph`,这里只负责取结果。
    """
    if tree is None:
        return None, []
    graph = node_lib.anim_tree_graph(tree)
    entry = graph["entry"]
    if entry is None:
        return None, []
    # 节点身份一律走 node_key(as_pointer),不能用 id()/is ——
    # 详见 nodes.node_key:同一个节点从连线拿到的是**另一个**包装对象。
    targeted = {node_lib.node_key(edge["to"]) for edge in graph["edges"]}
    named = [s for s in graph["states"] if node_lib.anim_state_name(s)]
    entries = [s for s in named
               if node_lib.node_key(s) not in targeted] or [entry]
    return entry, entries


def _edge_condition(ctx, edge, player_temp, finished_var=None):
    """一条边的条件表达式;算不出来返回 None。

    kind 决定从哪儿取:
      FINISH    -> 上游那段动画播完了(靠 animation_finished 信号,见
                   _anim_finished_helpers:查 current_animation 是不行的)
      ACTION_*  -> 节点上直接选的输入动作
      PIN       -> 接在『条件』引脚上的表达式
    """
    kind = edge.get("kind")
    if kind == node_lib.ANIM_EDGE_FINISH:
        source = node_lib.anim_state_name(edge["from"])
        if not source or not finished_var:
            return None
        # 信号回调里记下的名字 == 上游状态的名字 -> 就是"它播完了"
        return f"({finished_var} == {gd_string(source)})"
    if kind == node_lib.ANIM_EDGE_EXIT:
        # 走到『动画_结束』= 无条件(它就是"演完了"这个动作本身)
        return "true"
    trans = edge.get("transition")
    if trans is None:
        return None
    if kind in node_lib.ANIM_ACTION_SOURCES:
        action = str(getattr(trans, "gk_cond_action", "") or "").strip()
        if not action:
            return None
        const = ctx.add_action(action)
        return f"{node_lib.ANIM_ACTION_SOURCES[kind]}({const})"
    getter = getattr(trans, "condition_expr", None)
    if getter is None:
        return None
    return getter(ctx)


def _emit_state_graph(ctx, indent, graph, var_name, player_temp, enter_func,
                      finished_var=None, finish_func=None, gate=None):
    """把状态图生成成 if/elif 链。

    每个状态一段,状态内部按画布上下顺序判转移:

        if anim_state == "A":
            if <条件1>:
                _gk_anim_enter("B")
            elif <条件2>:
                _gk_anim_enter("C")

    -- 为什么状态内用 elif 而不是各自一个 if ------------------------
    "A 同时满足两条转移"必须有确定答案。虚幻 5 的语义是"从上往下第一个
    成立的赢",而且一帧只走一步 —— 用一串 if 的话同一帧能连跳三四个状态,
    表现是"一按键直接跳到最终状态、中间的动作一帧都没播"。
    """
    emitted = 0
    # ── 开始口 / 结束口的"闸门" ──────────────────────────────────────
    # 有开始口或结束口时,转场只在**"这段动画正在跑"**的时候判:
    # 还没开始 -> 不判;走到结束口了 -> 也不判(否则"结束"这个状态
    # 会立刻被别的转场拉走,用户根本等不到玩法图读到"已结束")。
    body_indent = indent
    if gate:
        ctx.add_line(indent, f"if {gate}:")
        body_indent = indent + 1
    for state in graph["states"]:
        name = node_lib.anim_state_name(state)
        if not name:
            continue
        state_key = node_lib.node_key(state)
        outgoing = [e for e in graph["edges"]
                    if node_lib.node_key(e["from"]) == state_key]
        if not outgoing:
            continue
        # 同一个状态里的边按画布顺序(上面的先判)。
        # 同一个节点驱动两次时顺序也必须稳定,所以排序键里带上目标名。
        outgoing.sort(key=lambda e: (e["order"],
                                     node_lib.anim_state_name(e["to"])))
        ctx.add_line(body_indent, f"{'if' if emitted == 0 else 'elif'} "
                                  f"{var_name} == {gd_string(name)}:")
        first = True
        for edge in outgoing:
            condition = _edge_condition(ctx, edge, player_temp, finished_var)
            if edge["kind"] == node_lib.ANIM_EDGE_EXIT:
                # 走到『动画_结束』:不切状态,而是**收工**
                if not condition or not finish_func:
                    continue
                ctx.add_line(body_indent + 1,
                             f"{'if' if first else 'elif'} {condition}:")
                ctx.add_line(body_indent + 2, f"{finish_func}()")
                first = False
                continue
            target = node_lib.anim_state_name(edge["to"])
            if not condition or not target:
                # 条件算不出来(比如『条件来源=自己写』但引脚没接)——
                # anim_tree_graph 已经报过原因,这里不重复刷告警。
                continue
            ctx.add_line(body_indent + 1,
                         f"{'if' if first else 'elif'} {condition}:")
            ctx.add_line(body_indent + 2, f"{enter_func}({gd_string(target)})")
            first = False
        if first:
            # 这一段一条转移都没生成出来 —— 留个 pass 免得语法错误
            ctx.add_line(body_indent + 1, "pass")
        emitted += 1
    if emitted == 0:
        ctx.add_line(body_indent, "pass")
    # ── 这里**不能**清 `_gk_anim_finished`(改过两次才对)──────────────
    # 第一版每帧判完就清,结果"(某动画播完了) 并且 (在地面上)"这种复合条件
    # 永远等不到:那一刻玩家还在空中,条件为假,标志却被清掉了,
    # 之后落地也不会再触发。现在标志**粘住**到下一段动画开播
    # (由 `_gk_on_anim_started` 信号和 `_gk_anim_enter` 负责清)。


# ==========================================================================
# 动画混合树(第六十一轮)
# ==========================================================================
# ── 为什么这里才第一次用 Godot 的 AnimationTree ──────────────────────
# 状态机那套是**手写 if/elif 链 + AnimationPlayer.play()**(见上面),
# 因为"切状态"本来就是离散的,自己写反而更短更可读。
# 但"连续混合"是 AnimationPlayer **做不到**的:它没有权重概念,一次只能
# 播一段。Godot 里唯一能做权重混合的就是 AnimationTree + BlendSpace。
# 所以这一块**真的**建一个 AnimationTree(运行时用 GDScript 建,不生成
# .tres —— 和插件"不额外产出资源文件"的一贯做法一致)。
_BLEND_MAX_POINTS = 6


def parse_blend_points(text):
    """把 `走:0` / `走:0:1.5` 解析成 [(动画名, 位置, 速度)]。解析不出来的跳过。

    第三个字段(速度)是第七十三轮加的:**每个混合点各自播多快**,不写 = 1.0
    (原速)。位置 / 速度解析不出来时按老规矩兜底(位置 = 序号、速度 = 1)。
    """
    points = []
    for chunk in str(text or "").split("|"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [piece.strip() for piece in chunk.split(":")]
        name = parts[0]
        if not name:
            continue
        values = []
        for piece in parts[1:3]:
            try:
                values.append(float(piece))
            except ValueError:
                values.append(None)
        values = (values + [None, None])[:2]
        if values[0] is None:
            values[0] = float(len(points))
        points.append((name, values[0],
                       1.0 if values[1] is None else values[1]))
        if len(points) >= _BLEND_MAX_POINTS:
            break
    return points


def _ensure_blend_tree_helper(ctx):
    """确保"建混合树"的辅助函数已经生成,返回它的名字。

    生成的函数(挂在脚本所在节点上,树也挂在它下面):
        _gk_ensure_blend_tree(host, player, points, tree_name) -> AnimationTree
    """
    made = getattr(ctx, "_blend_helper", None)
    if made:
        return made
    name = "_gk_ensure_blend_tree"
    ctx._blend_helper = name
    ctx.extra_sections.append({
        "signature": (f"func {name}(host: Node, player: Node, points: Array, "
                      f"tree_name: String, loop: bool) -> AnimationTree:"),
        "lines": [
            "    if host == null:",
            "        return null",
            "    var existing: AnimationTree = host.get_node_or_null(tree_name)",
            "    if existing != null:",
            "        return existing",
            "    if player == null or not (player is AnimationPlayer):",
            '        push_warning("[GK] 混合树找不到 AnimationPlayer —— 混合不会生效")',
            "        return null",
            "    var player_ap := player as AnimationPlayer",
            # ── 循环播放(第六十一轮,真机踩到)──────────────────────────
            # 混合点里的动作(走路 / 跑步)本来就是**循环**的;可 glTF 导进来的
            # 动画 `loop_mode` 默认是 LOOP_NONE —— 播一遍就停在最后一帧,
            # 于是"混合位置变了但姿势不动"(探针实测:0.5 时高度回到原点)。
            "    if loop:",
            "        for entry in points:",
            "            var anim: Animation = player_ap.get_animation("
            "StringName(entry[0]))",
            "            if anim != null and anim.loop_mode == Animation.LOOP_NONE:",
            "                anim.loop_mode = Animation.LOOP_LINEAR",
            "    var tree := AnimationTree.new()",
            "    tree.name = tree_name",
            "    host.add_child(tree)",
            "    var space := AnimationNodeBlendSpace1D.new()",
            "    for entry in points:",
            "        var anim_node := AnimationNodeAnimation.new()",
            "        anim_node.animation = StringName(entry[0])",
            # ── 这个点自己的播放速度(第七十三轮)────────────────────────
            # ⚠ 没用 AnimationNodeTimeScale:它的 scale 是**参数**(没有属性),
            #   而混合点里的子节点没有可寻址的名字,设不进去(真机实测:
            #   `AnimationNodeTimeScale` 的属性列表是空的)。
            #   改走 AnimationNodeAnimation 自带的"自定义时间轴":
            #   timeline_length = 原长 / 速度,再把 stretch_time_scale 打开。
            "        if entry.size() > 2 and float(entry[2]) != 1.0:",
            "            var _src: Animation = player_ap.get_animation("
            "StringName(entry[0]))",
            "            if _src != null:",
            "                anim_node.use_custom_timeline = true",
            "                anim_node.timeline_length = _src.length / float(entry[2])",
            "                anim_node.stretch_time_scale = true",
            # ⚠ 走了自定义时间轴,**循环要看节点自己的 loop_mode**(真机实测:
            #   不补这一行,2 倍速那段播到 0.5 秒就冻在最后一帧不动了 ——
            #   Animation 资源上的 loop_mode 在这种情况下不生效)
            "                if loop:",
            "                    anim_node.loop_mode = Animation.LOOP_LINEAR",
            # 第 4 个参数是**这个混合点的名字**。不给的话 Godot 4.7 会打一条
            # 弃用警告(add_blend_point: No name provided ...),
            # 满屏都是,用户会以为是我们的 bug。
            "        space.add_blend_point(anim_node, float(entry[1]), "
            "-1, StringName(entry[0]))",
            "    tree.tree_root = space",
            "    tree.root_node = tree.get_path_to(host)",
            "    tree.anim_player = tree.get_path_to(player)",
            "    tree.active = true",
            "    return tree",
        ],
        "empty_body": "    return null",
        "separator": True,
    })
    return name


def parse_blend_points_2d(text):
    """把 `待机:0:0` / `待机:0:0:1.5` 解析成 [(动画名, x, y, 速度)](第七十二轮)。

    和 1D 的 `名:位置` 只差"多一个数";第四个数是**这个点自己的播放速度**
    (第七十三轮,不写 = 1.0)。解析不出来的照样跳过,不抛异常 ——
    用户手打错一个字符不该让整个导出失败。
    """
    points = []
    for chunk in str(text or "").split("|"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [piece.strip() for piece in chunk.split(":")]
        name = parts[0]
        if not name:
            continue
        values = []
        for piece in parts[1:4]:
            try:
                values.append(float(piece))
            except ValueError:
                values.append(None)
        # 补齐三位(`甲` 这种只写了名字的不能下标越界)
        values = (values + [None, None, None])[:3]
        if values[0] is None:
            values[0] = float(len(points))
        if values[1] is None:
            values[1] = 0.0
        points.append((name, values[0], values[1],
                       1.0 if values[2] is None else values[2]))
        if len(points) >= _BLEND_MAX_POINTS:
            break
    return points


def format_blend_points(points, dim=1):
    """把 [(名, 位置[, 速度])] / [(名, x, y[, 速度])] 拼回 `混合点` 字符串。

    对话框(『挑动画…』)按这个写回节点;解析用上面那两个 —— 一进一出**同一套
    规则**,免得界面和导出各说各话(界面用 :g 去掉多余小数位,读起来干净)。

    ⚠ 速度 = 1 时**不写**那个字段:老 .blend 打开是 `走:0|跑:1`,改一下位置
      不该平白多出一串 `:1`。
    """
    chunks = []
    for point in points:
        if dim == 2:
            name, x, y = point[0], point[1], point[2]
            speed = point[3] if len(point) > 3 else 1.0
            chunk = f"{name}:{float(x):g}:{float(y):g}"
        else:
            name, pos = point[0], point[1]
            speed = point[2] if len(point) > 2 else 1.0
            chunk = f"{name}:{float(pos):g}"
        if abs(float(speed) - 1.0) > 0.0001:
            chunk += f":{float(speed):g}"
        chunks.append(chunk)
        if len(chunks) >= _BLEND_MAX_POINTS:
            break
    return "|".join(chunks)


def _ensure_blend_tree_2d_helper(ctx):
    """确保"建 2D 混合树"的辅助函数已经生成,返回它的名字。

    ⚠ **单独一份**,不跟 1D 那份合并:合并要改签名,而老图(只用了 1D)的
      生成物必须一字不变 —— 这是本项目的验收线之一。
    """
    made = getattr(ctx, "_blend_2d_helper", None)
    if made:
        return made
    name = "_gk_ensure_blend_tree_2d"
    ctx._blend_2d_helper = name
    ctx.extra_sections.append({
        "signature": (f"func {name}(host: Node, player: Node, points: Array, "
                      f"tree_name: String, loop: bool) -> AnimationTree:"),
        "lines": [
            "    if host == null:",
            "        return null",
            "    var existing: AnimationTree = host.get_node_or_null(tree_name)",
            "    if existing != null:",
            "        return existing",
            "    if player == null or not (player is AnimationPlayer):",
            '        push_warning("[GK] 混合树找不到 AnimationPlayer —— 混合不会生效")',
            "        return null",
            "    var player_ap := player as AnimationPlayer",
            # 循环:和 1D 同一个坑(glTF 的动画默认 LOOP_NONE,播一遍就停在最后
            # 一帧 -> "混合位置变了但姿势不动")
            "    if loop:",
            "        for entry in points:",
            "            var anim: Animation = player_ap.get_animation("
            "StringName(entry[0]))",
            "            if anim != null and anim.loop_mode == Animation.LOOP_NONE:",
            "                anim.loop_mode = Animation.LOOP_LINEAR",
            "    var tree := AnimationTree.new()",
            "    tree.name = tree_name",
            "    host.add_child(tree)",
            # 顺序和 1D 那份一致:先把树挂进场景树,再往里放点 ——
            # BlendSpace2D 放点时要做三角化,让树已经"在世"上最稳。
            "    var space := AnimationNodeBlendSpace2D.new()",
            "    for entry in points:",
            "        var anim_node := AnimationNodeAnimation.new()",
            "        anim_node.animation = StringName(entry[0])",
            # 单点倍速:同 1D(见上面那份的说明)
            "        if entry.size() > 3 and float(entry[3]) != 1.0:",
            "            var _src: Animation = player_ap.get_animation("
            "StringName(entry[0]))",
            "            if _src != null:",
            "                anim_node.use_custom_timeline = true",
            "                anim_node.timeline_length = _src.length / float(entry[3])",
            "                anim_node.stretch_time_scale = true",
            # 循环要补节点自己的 loop_mode(见 1D 那份的说明)
            "                if loop:",
            "                    anim_node.loop_mode = Animation.LOOP_LINEAR",
            "        space.add_blend_point(anim_node, Vector2(float(entry[1]), "
            "float(entry[2])), -1, StringName(entry[0]))",
            "    tree.tree_root = space",
            "    tree.root_node = tree.get_path_to(host)",
            "    tree.anim_player = tree.get_path_to(player)",
            "    tree.active = true",
            "    return tree",
        ],
        "empty_body": "    return null",
        "separator": True,
    })
    return name


def _points_literal(points, dim=1):
    """混合点 -> GDScript 字面量(第七十三轮:带上"每点速度")。

    ⚠ **速度全是 1 时不加那一列** —— 老图(没写速度)的生成物要和以前逐字
      一致;只有真写了速度才补上(helper 里用 `entry.size()` 判断有没有)。
    """
    def _speed(point):
        return point[3] if dim == 2 else point[2]

    with_speed = any(abs(float(_speed(item)) - 1.0) > 0.0001 for item in points)
    items = []
    for point in points:
        if dim == 2:
            name, x, y = point[0], point[1], point[2]
            body = f"{gd_string(name)}, {gd_number(x)}, {gd_number(y)}"
        else:
            name, pos = point[0], point[1]
            body = f"{gd_string(name)}, {gd_number(pos)}"
        if with_speed:
            body += f", {gd_number(_speed(point))}"
        items.append(f"[{body}]")
    return "[" + ", ".join(items) + "]"


@stmt_node("GKNodeBlendTree")
def _stmt_blend_tree(ctx, node, indent):
    """『动画_混合树』:建 AnimationTree(在 _ready 里)+ 每帧推参数。"""
    points = parse_blend_points(getattr(node, "gk_points", ""))
    if len(points) < 2:
        ctx.warn("『动画_混合树』至少要有**两个**混合点才会混 —— "
                 "『混合点』写成 `走:0|跑:1` 这样(多个用 | 分隔)")
        return

    # 一个物体上可以放多棵混合树(比如"移动"一棵、"表情"一棵)——
    # 第二棵起自动叫 blend_tree_2 / GKBlendTree_2,不会互相覆盖。
    index = len(ctx.anim_blend_trees)
    suffix = "" if index == 0 else f"_{index + 1}"
    var_name = ctx.declare_var(f"blend_tree{suffix}", "AnimationTree")
    tree_name = f"GKBlendTree{suffix}"

    points_literal = _points_literal(points, dim=1)
    player = ctx.animation_player(node)
    loop = "true" if bool(getattr(node, "gk_loop", True)) else "false"
    # 配了『叠层动画』-> 换成"带一层 one-shot"的那份 helper(老图用它原来的)
    overlay = str(getattr(node, "gk_overlay", "") or "").strip()
    if overlay:
        helper = _ensure_blend_tree_layer_helper(ctx)
        call = (f"{helper}(self, {player}, {points_literal}, "
                f"{gd_string(tree_name)}, {loop}, {gd_string(overlay)}, "
                f"{gd_number(getattr(node, 'gk_overlay_in', 0.15))}, "
                f"{gd_number(getattr(node, 'gk_overlay_out', 0.25))}, 1)")
        pos_path = "base/blend_position"
    else:
        helper = _ensure_blend_tree_helper(ctx)
        call = (f"{helper}(self, {player}, {points_literal}, "
                f"{gd_string(tree_name)}, {loop})")
        pos_path = "blend_position"
    ctx.add_line(indent, f"if {var_name} == null:")
    ctx.add_line(indent + 1, f"{var_name} = {call}")

    socket = ctx._socket(node, "参数")
    if socket is not None and getattr(socket, "is_linked", False):
        param = ctx.expr_of(socket)
    else:
        param = gd_number(getattr(node, "gk_value", 0.0))
    smooth = gd_number(getattr(node, "gk_smooth", 0.0))
    ctx.anim_blend_trees.append({
        "node": node, "var": var_name, "param": param, "smooth": smooth,
        "debug": bool(getattr(node, "gk_debug_print", False)),
        "names": [item[0] for item in points],
        "overlay": overlay, "pos_path": pos_path,
    })
    ctx.need_anim_tree_process = True

    if bool(getattr(node, "gk_debug_print", False)):
        ctx.add_line(indent, f"if {var_name} != null:")
        ctx.add_line(indent + 1, f'print("[GK 混合树] 已建 ", '
                                 f'{gd_string(tree_name)}, " 混合点 ", '
                                 f'{points_literal})')


@stmt_node("GKNodeBlendTree2D")
def _stmt_blend_tree_2d(ctx, node, indent):
    """『动画_混合树2D』:建 AnimationNodeBlendSpace2D + 每帧推 Vector2 参数。

    和 1D 那棵共用"每帧推参数"的下游(`ctx.anim_blend_trees` ->
    `_emit_blend_update`),只是 spec 里多一个 `dim=2` 和 X / Y 两个参数。
    """
    points = parse_blend_points_2d(getattr(node, "gk_points", ""))
    if len(points) < 3:
        ctx.warn("『动画_混合树2D』至少要**三个**混合点才围得成面 —— "
                 "『混合点』写成 `待机:0:0|前走:0:1|左走:-1:0|右走:1:0` 这样"
                 "(只想按一个参数混走↔跑,用『动画_混合树』)")
        return

    # 1D / 2D 一起数:一个物体上放多棵混合树时名字不能撞。
    index = len(ctx.anim_blend_trees)
    suffix = "" if index == 0 else f"_{index + 1}"
    var_name = ctx.declare_var(f"blend_tree{suffix}", "AnimationTree")
    tree_name = f"GKBlendTree{suffix}"

    points_literal = _points_literal(points, dim=2)
    player = ctx.animation_player(node)
    loop = "true" if bool(getattr(node, "gk_loop", True)) else "false"
    # 配了『叠层动画』-> 走"带一层 one-shot"的那份 helper(第七十三轮)
    overlay = str(getattr(node, "gk_overlay", "") or "").strip()
    if overlay:
        helper = _ensure_blend_tree_layer_helper(ctx)
        call = (f"{helper}(self, {player}, {points_literal}, "
                f"{gd_string(tree_name)}, {loop}, {gd_string(overlay)}, "
                f"{gd_number(getattr(node, 'gk_overlay_in', 0.15))}, "
                f"{gd_number(getattr(node, 'gk_overlay_out', 0.25))}, 2)")
        pos_path = "base/blend_position"
    else:
        helper = _ensure_blend_tree_2d_helper(ctx)
        call = (f"{helper}(self, {player}, {points_literal}, "
                f"{gd_string(tree_name)}, {loop})")
        pos_path = "blend_position"
    ctx.add_line(indent, f"if {var_name} == null:")
    ctx.add_line(indent + 1, f"{var_name} = {call}")

    def _axis(socket_name, prop, default):
        socket = ctx._socket(node, socket_name)
        if socket is not None and getattr(socket, "is_linked", False):
            return ctx.expr_of(socket)
        return gd_number(getattr(node, prop, default))

    ctx.anim_blend_trees.append({
        "node": node, "var": var_name, "dim": 2,
        "param_x": _axis("X", "gk_x", 0.0),
        "param_y": _axis("Y", "gk_y", 0.0),
        "smooth": gd_number(getattr(node, "gk_smooth", 0.0)),
        "debug": bool(getattr(node, "gk_debug_print", False)),
        "names": [item[0] for item in points],
        "overlay": overlay, "pos_path": pos_path,
    })
    ctx.need_anim_tree_process = True

    if bool(getattr(node, "gk_debug_print", False)):
        ctx.add_line(indent, f"if {var_name} != null:")
        ctx.add_line(indent + 1, f'print("[GK 混合树2D] 已建 ", '
                                 f'{gd_string(tree_name)}, " 混合点 ", '
                                 f'{points_literal})')


def _ensure_blend_tree_layer_helper(ctx):
    """『叠层』那棵树的辅助函数(第七十三轮)。

    结构(真 Godot 4.7.2 探针实测出来的一版):

        AnimationNodeBlendTree
          ├─ base    = BlendSpace1D / 2D(混合点那套)
          ├─ shot    = AnimationNodeAnimation(叠层动画)
          ├─ oneshot = AnimationNodeOneShot(fadein/fadeout)
          │      输入 0 "in"   ← base
          │      输入 1 "shot" ← shot
          └─ output  ← oneshot

    ── 为什么是这个接法(两个坑都踩过了)──────────────────────────
    1. `AnimationNodeOneShot` 的两个输入**按名字**是 `in`(0)/ `shot`(1)。
       第一版把叠层动画接到了 0 上(以为 0 就是"那一段"),于是那个 one-shot
       根本没有可播的东西 —— 探针实测:触发后画面纹丝不动
       (`during=0.14`,和基准一模一样)。
    2. 官网上那版是把 oneshot 直接接到 `output` 的 0 / 1 **两个**输入上;
      可 4.7.2 的 `output` 节点**只吃一个输入**(探针报
       `Index p_input_index = 1 is out of bounds ... connections.size() = 1`)。
       所以中间那层 `AnimationNodeBlend2` + 触发时推 `blend_amount` 是**绕路**
       —— 现在不需要了:`oneshot` 自己就会"没触发时透传 `in`、触发时混进
       `shot`、播完按 `fadeout_time` 退回 `in`"。探针实测:基准 0.1 ->
       触发后 1.88 -> 1.2 秒后回到 0.07。

    ⚠ 参数路径:空间成了子节点 -> `parameters/base/blend_position`。
    """
    made = getattr(ctx, "_blend_layer_helper", None)
    if made:
        return made
    name = "_gk_ensure_blend_tree_layer"
    ctx._blend_layer_helper = name
    ctx.extra_sections.append({
        "signature": (f"func {name}(host: Node, player: Node, points: Array, "
                      f"tree_name: String, loop: bool, overlay: String, "
                      f"fade_in: float, fade_out: float, dim: int) "
                      f"-> AnimationTree:"),
        "lines": [
            "    if host == null:",
            "        return null",
            "    var existing: AnimationTree = host.get_node_or_null(tree_name)",
            "    if existing != null:",
            "        return existing",
            "    if player == null or not (player is AnimationPlayer):",
            '        push_warning("[GK] 混合树找不到 AnimationPlayer —— 混合不会生效")',
            "        return null",
            "    var player_ap := player as AnimationPlayer",
            "    if loop:",
            "        for entry in points:",
            "            var anim: Animation = player_ap.get_animation("
            "StringName(entry[0]))",
            "            if anim != null and anim.loop_mode == Animation.LOOP_NONE:",
            "                anim.loop_mode = Animation.LOOP_LINEAR",
            "    var tree := AnimationTree.new()",
            "    tree.name = tree_name",
            "    host.add_child(tree)",
            "    var space: AnimationRootNode",
            "    if dim == 2:",
            "        var space2 := AnimationNodeBlendSpace2D.new()",
            "        for entry in points:",
            "            var node2 := AnimationNodeAnimation.new()",
            "            node2.animation = StringName(entry[0])",
            "            if entry.size() > 3 and float(entry[3]) != 1.0:",
            "                var src2: Animation = player_ap.get_animation("
            "StringName(entry[0]))",
            "                if src2 != null:",
            "                    node2.use_custom_timeline = true",
            "                    node2.timeline_length = src2.length / float(entry[3])",
            "                    node2.stretch_time_scale = true",
            "                    if loop:",
            "                        node2.loop_mode = Animation.LOOP_LINEAR",
            "            space2.add_blend_point(node2, Vector2(float(entry[1]), "
            "float(entry[2])), -1, StringName(entry[0]))",
            "        space = space2",
            "    else:",
            "        var space1 := AnimationNodeBlendSpace1D.new()",
            "        for entry in points:",
            "            var node1 := AnimationNodeAnimation.new()",
            "            node1.animation = StringName(entry[0])",
            "            if entry.size() > 2 and float(entry[2]) != 1.0:",
            "                var src1: Animation = player_ap.get_animation("
            "StringName(entry[0]))",
            "                if src1 != null:",
            "                    node1.use_custom_timeline = true",
            "                    node1.timeline_length = src1.length / float(entry[2])",
            "                    node1.stretch_time_scale = true",
            "                    if loop:",
            "                        node1.loop_mode = Animation.LOOP_LINEAR",
            "            space1.add_blend_point(node1, float(entry[1]), "
            "-1, StringName(entry[0]))",
            "        space = space1",
            "    var shot := AnimationNodeAnimation.new()",
            "    shot.animation = StringName(overlay)",
            # ⚠ 叠层那段**必须播一次**(LOOP_NONE)。要是它带循环(资产里原本
            #   就是循环的,或者用户拿走路动作当叠层),OneShot 永远等不到它
            #   结束,人就一直僵在叠层姿势上 —— 而且不报任何错,很难查
            #   (真机实测:循环的叠层会让它每 0.4 秒重来一遍,停不下来)。
            "    var shot_anim: Animation = player_ap.get_animation("
            "StringName(overlay))",
            "    if shot_anim != null:",
            "        shot_anim.loop_mode = Animation.LOOP_NONE",
            "    var oneshot := AnimationNodeOneShot.new()",
            "    oneshot.fadein_time = fade_in",
            "    oneshot.fadeout_time = fade_out",
            "    var bt := AnimationNodeBlendTree.new()",
            "    bt.add_node(\"base\", space, Vector2(0.0, 0.0))",
            "    bt.add_node(\"shot\", shot, Vector2(0.0, 120.0))",
            "    bt.add_node(\"oneshot\", oneshot, Vector2(200.0, 60.0))",
            "    bt.connect_node(\"oneshot\", 0, \"base\")",
            "    bt.connect_node(\"oneshot\", 1, \"shot\")",
            "    bt.connect_node(\"output\", 0, \"oneshot\")",
            "    tree.tree_root = bt",
            "    tree.root_node = tree.get_path_to(host)",
            "    tree.anim_player = tree.get_path_to(player)",
            "    tree.active = true",
            "    return tree",
        ],
        "empty_body": "    return null",
        "separator": True,
    })
    return name


def _ensure_overlay_helpers(ctx):
    """叠层的"播放 / 收起"两个小函数,返回它们的名字。

    ── 为什么没有 `blend_amount` 了(第七十三轮真机探针改的)────────
    原来是"显式 Blend2 + 触发时把 blend_amount 推成 1、播完再推回 0"。
    探针证明 `AnimationNodeOneShot` 自己就会:没触发时透传 `in`(基准)、
    触发时把 `shot` 混进来、播完按 `fadeout_time` 退回 `in` ——
    所以那两层管子纯属绕路,现在只有一句"发个请求"。

    ⚠ 『保持秒数』= 0 时**不排定时器**:让那段动画自己播完(OneShot 会按
      自己的长度退回去)。> 0 才排一个"到点强制收起"。
      ⚠ 播放那个**故意不是协程**:收尾用 `SceneTreeTimer` + lambda,而不是
      `await`。协程不 await 直接用,Godot 会警告"coroutine called without
      await" —— 用户上一轮才因为一串生成代码的告警来找过账。
    """
    made = getattr(ctx, "_overlay_helpers", None)
    if made:
        return made
    fire_name = "_gk_overlay_fire"
    stop_name = "_gk_overlay_stop"
    ctx._overlay_helpers = (fire_name, stop_name)
    ctx.extra_sections.append({
        "signature": f"func {fire_name}(tree: AnimationTree, hold: float) -> void:",
        "lines": [
            "    if tree == null:",
            "        return",
            "    tree.set(\"parameters/oneshot/request\", "
            "AnimationNodeOneShot.ONE_SHOT_REQUEST_FIRE)",
            "    if hold <= 0.0:",
            "        return",
            "    var timer := tree.get_tree().create_timer(hold)",
            "    timer.timeout.connect(func() -> void:",
            "        if tree != null and is_instance_valid(tree):",
            "            tree.set(\"parameters/oneshot/request\", "
            "AnimationNodeOneShot.ONE_SHOT_REQUEST_ABORT))",
        ],
        "empty_body": "    pass",
        "separator": True,
    })
    ctx.extra_sections.append({
        "signature": f"func {stop_name}(tree: AnimationTree) -> void:",
        "lines": [
            "    if tree == null:",
            "        return",
            "    tree.set(\"parameters/oneshot/request\", "
            "AnimationNodeOneShot.ONE_SHOT_REQUEST_ABORT)",
        ],
        "empty_body": "    pass",
        "separator": True,
    })
    return fire_name, stop_name


@stmt_node("GKNodeAnimOverlay")
def _stmt_anim_overlay(ctx, node, indent):
    """『动画_叠层』:触发某棵混合树上的那一层一次性动作。"""
    index = int(getattr(node, "gk_tree_index", 0) or 0)
    trees = getattr(ctx, "anim_blend_trees", ()) or ()
    if index < 0 or index >= len(trees):
        ctx.warn("『动画_叠层』找不到要叠的那棵混合树 —— 同一张图里先放一个"
                 "『动画_混合树』/『动画_混合树2D』;有多棵时用『哪一棵混合树』选")
        return
    spec = trees[index]
    if not spec.get("overlay"):
        ctx.warn("『动画_叠层』要叠的那棵混合树**没有配『叠层动画』** —— "
                 "先在那张卡片上填一个动画名(比如 受击),再回来触发")
        return
    fire_name, stop_name = _ensure_overlay_helpers(ctx)
    tree_var = spec["var"]
    if str(getattr(node, "gk_action", "FIRE") or "FIRE") == "STOP":
        ctx.add_line(indent, f"{stop_name}({tree_var})")
        return
    hold = gd_number(getattr(node, "gk_hold", 0.0))
    ctx.add_line(indent, f"{fire_name}({tree_var}, {hold})")


def _emit_blend_update(ctx, indent, spec):
    """每物理帧把参数推给 blend_position(由 generator 在物理帧段调用)。"""
    var_name = spec["var"]
    smooth = spec["smooth"]
    # 配了叠层的树,空间成了子节点 -> 参数路径多一层 `base/`(第七十三轮)
    path = spec.get("pos_path", "blend_position")
    ctx.add_line(indent, f"if {var_name} != null:")
    if spec.get("dim") == 2:
        # 2D 的 blend_position 是 **Vector2** —— lerpf 只认标量,得用 Vector2.lerp
        param = f"Vector2({spec['param_x']}, {spec['param_y']})"
        if smooth not in ("0.0", "0"):
            dt = ctx.new_temp("bt_dt")
            now = ctx.new_temp("bt_now")
            ctx.add_line(indent + 1,
                         f"var {dt} := get_physics_process_delta_time()")
            ctx.add_line(indent + 1,
                         f"var {now}: Vector2 = "
                         f'{var_name}.get("parameters/{path}")')
            ctx.add_line(indent + 1,
                         f'{var_name}.set("parameters/{path}", '
                         f"{now}.lerp({param}, "
                         f'clampf({dt} / maxf({smooth}, 0.0001), 0.0, 1.0)))')
        else:
            ctx.add_line(indent + 1,
                         f'{var_name}.set("parameters/{path}", {param})')
        return
    param = spec["param"]
    if smooth not in ("0.0", "0"):
        dt = ctx.new_temp("bt_dt")
        ctx.add_line(indent + 1, f"var {dt} := get_physics_process_delta_time()")
        current = f'float({var_name}.get("parameters/{path}"))'
        ctx.add_line(indent + 1,
                     f'{var_name}.set("parameters/{path}", '
                     f'lerpf({current}, {param}, '
                     f'clampf({dt} / maxf({smooth}, 0.0001), 0.0, 1.0)))')
    else:
        ctx.add_line(indent + 1,
                     f'{var_name}.set("parameters/{path}", {param})')


@stmt_node("GKNodeAnimStateMachine")
def _stmt_anim_state_machine(ctx, node, indent):
    """动画状态机 —— 取代手拼分支网。

    ── 生成思路 ────────────────────────────────────────────────────
    不做"每个状态一个函数"那种展开(GDScript 里会很啰嗦),
    而是生成一段**紧凑的 if 链**:按状态表顺序逐条判断,

        if anim_state == "idle" and <条件引脚>:
            anim_state = "run"
            <切动画>

    这么做的理由:
      * 生成结果短、可读 —— 用户能直接看出哪条规则在生效
      * 状态表是**有序**的,优先级就是书写顺序(第一条命中的赢),
        这和 UPBGE / 蓝图里"从上往下第一个为真的分支"直觉一致
      * 不需要为每个状态生成函数,省掉一堆样板

    ── 检查时机 ────────────────────────────────────────────────────
    『每帧检查』-> 整段挂在 `_physics_process` 里
    『只在进入时判』-> 只在本次执行时判一次
    """
    rules = parse_transitions(getattr(node, "gk_transitions", ""))
    if not rules:
        ctx.warn(f"节点『{node.bl_label}』的状态表是空的,什么都不会发生。"
                 f"写一行像 `idle -> run` 这样的规则")
        return

    player = ctx.animation_player(node)
    # ── 状态变量**按节点**独立(用户 2026-10-03 的问题)──────────────
    # 用户问"一个游戏逻辑里是否可以有多个游戏动画?"—— 以前**不行**:
    # 两张动画树都写进同一个 `anim_state`、共用同一个 `_gk_anim_enter`,
    # 后一张把前一张整个盖掉(实测:两张树只生成一套状态机)。
    # 现在按节点 key 声明,第二个状态机自动叫 `anim_state_2`。
    var_name = ctx.declare_var(
        snake_case(getattr(node, "gk_state_var", "anim_state"), "anim_state"),
        "String", key=node_lib.node_key(node))
    player_temp = ctx.new_temp("ap")
    ctx.add_line(indent, f"var {player_temp} := {player}")
    # 首次执行时把状态设成"起始状态"
    start_state = ctx.input_string(node, "起始状态", "idle")
    ctx.add_line(indent, f"if {var_name}.is_empty():")
    ctx.add_line(indent + 1, f"{var_name} = {start_state}")

    every_frame = str(getattr(node, "gk_check_mode", "EVERY_FRAME")) == "EVERY_FRAME"
    if every_frame:
        # 整段逻辑要放进 _physics_process —— 由 ctx 收集,generate() 统一发射
        ctx.anim_state_machines.append({
            "node": node, "rules": rules, "var": var_name,
            "player": player_temp,
        })
        ctx.need_anim_state_process = True
        return

    # 只在进入时判:直接在这里展开一次
    _emit_state_transitions(ctx, node, indent, rules, var_name, player_temp)


def _emit_state_transitions(ctx, node, indent, rules, var_name, player_temp):
    """生成转移判断的 if 链。见 _stmt_anim_state_machine 的说明。"""
    for start, end in rules:
        pin_name = transition_pin_name(start, end)
        socket = ctx._socket(node, pin_name)
        if socket is None:
            # 状态表改了但引脚还没重建 —— 提示用户点一下节点
            ctx.warn(f"节点『{node.bl_label}』的规则 `{start} -> {end}` "
                     f"还没有对应的引脚。在节点上把状态表重新编辑一次"
                     f"(或重新加载文件)就会长出来")
            continue
        if not getattr(socket, "is_linked", False):
            # 没接条件 -> 这条规则永远不会触发,明确告诉用户
            ctx.warn(f"节点『{node.bl_label}』的规则 `{start} -> {end}` "
                     f"没有接条件,不会生效")
            continue
        condition = ctx.expr_of(socket)
        ctx.add_line(indent, f'if {var_name} == {gd_string(start)} and {condition}:')
        ctx.add_line(indent + 1, f'{var_name} = {gd_string(end)}')
        # 切状态 = 播目标状态的动画(状态名就是动画名)
        ctx.add_line(indent + 1, f"if {player_temp} != null and "
                                 f"{player_temp}.has_animation({gd_string(end)}):")
        ctx.add_line(indent + 2,
                     f"{player_temp}.play({gd_string(end)}, -1.0, 1.0)")
        ctx.add_line(indent + 1, "else:")
        ctx.add_line(indent + 2,
                     f'push_warning("[GK] 状态『{end}』没有对应的动画: %s" % '
                     f"{gd_string(end)})")


@stmt_node("GKNodeSetState")
def _stmt_set_state(ctx, node, indent):
    var_name = ctx.declare_var(getattr(node, "gk_var", "state"), "String")
    state = ctx.input_string(node, "状态", "idle")
    ctx.add_line(indent, f"{var_name} = {state}")


# ==========================================================================
# 动画:其余常用操作
# ==========================================================================
@expr_node("GKNodeAnimPlaying")
def _expr_anim_playing(ctx, node):
    """某个动画片段是否正在播放。

    Godot 的 AnimationPlayer 只有 `is_playing()`(当前有没有在播)和
    `current_animation`(当前播的是谁),没有"某个片段是否在播"。
    所以这里组合判断:留空动画名 = 只看 is_playing;
    填了名字 = 还要当前播的就是它。
    """
    player = ctx.animation_player(node)
    name = (getattr(node, "gk_anim_name", "") or "").strip()
    temp = ctx.new_temp("ap")
    ctx.add_line(ctx._expr_indent, f"var {temp} := {player}")
    if not name:
        return f"({temp} != null and {temp}.is_playing())"
    return (f"({temp} != null and {temp}.is_playing() "
            f"and {temp}.current_animation == {gd_string(name)})")


@stmt_node("GKNodeAnimPause")
def _stmt_anim_pause(ctx, node, indent):
    """暂停 / 继续当前动画。

    ── API 是实测出来的,不是猜的(踩过的坑)────────────────────────
    Godot 4.7 的 `AnimationPlayer` **没有 `paused` 属性** ——
    写了会报 "Invalid assignment of property 'paused'"。
    真实可用的是:
        pause()                 暂停(停在当前位置)
        play(name)              从当前位置继续(不再传名字就得重播)
        active / set_active()   开关整个播放器

    所以这里:
      * 暂停 -> `pause()`(位置保持不变)
      * 继续 -> 用 `current_animation` 重新 `play()`,从当前位置接着放
        (不能用 stop(),那会回到静止姿势)
    """
    player = ctx.animation_player(node)
    temp = ctx.new_temp("ap")
    pos_var = ctx.declare_var("_gk_anim_pause_pos", "float")
    ctx.add_line(indent, f"var {temp} := {player}")
    ctx.add_line(indent, f"if {temp} != null:")
    if getattr(node, "gk_paused", True):
        # 记下当前位置 —— 恢复时要 seek 回来
        ctx.add_line(indent + 1, f"{pos_var} = {temp}.current_animation_position")
        ctx.add_line(indent + 1, f"{temp}.pause()")
    else:
        # 恢复:`play()` 会把动画**从头开始**,所以要用 seek 回到暂停时的位置。
        # (实测:只调 play() 不 seek,动画会跳回开头)
        ctx.add_line(indent + 1, f"if not {temp}.current_animation.is_empty():")
        ctx.add_line(indent + 2, f"{temp}.play({temp}.current_animation)")
        ctx.add_line(indent + 2, f"{temp}.seek({pos_var}, true)")


@stmt_node("GKNodeAnimStop")
def _stmt_anim_stop(ctx, node, indent):
    player = ctx.animation_player(node)
    temp = ctx.new_temp("ap")
    ctx.add_line(indent, f"var {temp} := {player}")
    ctx.add_line(indent, f"if {temp} != null:")
    ctx.add_line(indent + 1, f"{temp}.stop()")


@stmt_node("GKNodeAnimSetSpeed")
def _stmt_anim_set_speed(ctx, node, indent):
    player = ctx.animation_player(node)
    speed = _tunable_number(ctx, node, "速度", "1.0", "anim_speed")
    temp = ctx.new_temp("ap")
    ctx.add_line(indent, f"var {temp} := {player}")
    ctx.add_line(indent, f"if {temp} != null:")
    ctx.add_line(indent + 1, f"{temp}.speed_scale = {speed}")


@expr_node("GKNodeAnimLength")
def _expr_anim_length(ctx, node):
    """动画片段时长(秒)。名字不存在时返回 0,不是崩溃。"""
    player = ctx.animation_player(node)
    name = ctx.input_string(node, "动画名", "idle")
    ap_temp = ctx.new_temp("ap")
    name_temp = ctx.new_temp("anim")
    ctx.add_line(ctx._expr_indent, f"var {ap_temp} := {player}")
    ctx.add_line(ctx._expr_indent, f"var {name_temp} := {name}")
    return (f"(0.0 if ({ap_temp} == null or not {ap_temp}.has_animation({name_temp})) "
            f"else {ap_temp}.get_animation({name_temp}).length)")
