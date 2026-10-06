"""周边服务类发射器:音频、存档、屏幕效果 / UI、滤镜。"""

from ..nodes.node_base import TARGET_SELF, gd_node_path, gd_string, snake_case
from ..nodes import nodes as node_lib

from .literals import gd_literal, gd_number
from .registry import _chain, _next_nodes, stmt_node
from .statements import _tunable_number




# ==========================================================================
# 音效
# ==========================================================================
def _audio_player_type(node):
    """2D 还是普通音频播放器。3D 项目里用户会取消勾选『2D 音效』。"""
    return "AudioStreamPlayer2D" if getattr(node, "gk_2d", True) else "AudioStreamPlayer"


def _audio_host(ctx, node):
    """音效挂到哪个节点上。默认挂当前脚本所在节点。"""
    raw = (getattr(node, "gk_target", "") or "").strip()
    if raw and raw != TARGET_SELF:
        return ctx.target_expr(node, hint="audio")
    return "self"


@stmt_node("GKNodePlaySound")
def _stmt_play_sound(ctx, node, indent):
    """一次性音效:运行时建播放器、播完自己删。

    ── 为什么不要求用户先摆好 AudioStreamPlayer ─────────────────---─
    让用户为每个音效在场景里摆一个播放器节点，是"引擎在给用户添活"。
    这里按需建、播完销毁，节点图里写一条就够了。
    """
    path = ctx.input_string(node, "音频路径", "")
    volume = _tunable_number(ctx, node, "音量", "0.0", "sfx_volume")
    pitch = _tunable_number(ctx, node, "音高", "1.0", "sfx_pitch")
    player_type = _audio_player_type(node)
    host = _audio_host(ctx, node)

    stream_var = ctx.new_temp("sfx_stream")
    player_var = ctx.new_temp("sfx")
    # 音频文件缺失时给出可读的报错，而不是让 load() 静默返回 null 后再炸在别处
    ctx.add_line(indent, f"var {stream_var}: AudioStream = load({path}) as AudioStream")
    ctx.add_line(indent, f"if {stream_var} != null:")
    ctx.add_line(indent + 1, f"var {player_var} := {player_type}.new()")
    ctx.add_line(indent + 1, f"{player_var}.stream = {stream_var}")
    ctx.add_line(indent + 1, f"{player_var}.volume_db = {volume}")
    ctx.add_line(indent + 1, f"{player_var}.pitch_scale = {pitch}")
    ctx.add_line(indent + 1, f"{player_var}.max_polyphony = 4")
    ctx.add_line(indent + 1, f"{host}.add_child({player_var})")
    ctx.add_line(indent + 1, f"{player_var}.play()")
    # 播完自己删:用 finished 信号，比"算时长"可靠(流长度不一定拿得到)
    ctx.add_line(indent + 1, f"{player_var}.finished.connect({player_var}.queue_free)")
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f'push_warning("找不到音频文件: " + str({path}))')


@stmt_node("GKNodePlayMusic")
def _stmt_play_music(ctx, node, indent):
    """背景音乐:只保留一个播放器，切曲目时先停旧的。"""
    path = ctx.input_string(node, "音频路径", "")
    volume = gd_number(getattr(node, "gk_volume", -6.0))
    fade = gd_number(getattr(node, "gk_fade", 0.5))

    music_var = ctx.declare_var("_gk_music", "AudioStreamPlayer")
    stream_var = ctx.new_temp("bgm_stream")

    ctx.add_line(indent, f"var {stream_var}: AudioStream = load({path}) as AudioStream")
    ctx.add_line(indent, f"if {stream_var} != null:")
    # 重复播放同一首时不要打断(主菜单返回游戏很常见)
    ctx.add_line(indent + 1, f"if {music_var} != null and {music_var}.stream == {stream_var} and {music_var}.playing:")
    ctx.add_line(indent + 2, "pass")
    ctx.add_line(indent + 1, "else:")
    ctx.add_line(indent + 2, f"if {music_var} != null:")
    ctx.add_line(indent + 3, f"{music_var}.queue_free()")
    ctx.add_line(indent + 2, f"{music_var} = AudioStreamPlayer.new()")
    ctx.add_line(indent + 2, f"{music_var}.stream = {stream_var}")
    ctx.add_line(indent + 2, f"{music_var}.bus = \"Master\"")
    ctx.add_line(indent + 2, f"self.add_child({music_var})")
    ctx.add_line(indent + 2, f"{music_var}.play()")
    if float(getattr(node, "gk_fade", 0.5) or 0.0) > 0.0:
        ctx.add_line(indent + 2, f"{music_var}.volume_db = -80.0")
        fade_var = ctx.new_temp("fade")
        ctx.add_line(indent + 2, f"var {fade_var} := create_tween()")
        ctx.add_line(indent + 2,
                     f"{fade_var}.tween_property({music_var}, \"volume_db\", {volume}, {fade})")
    else:
        ctx.add_line(indent + 2, f"{music_var}.volume_db = {volume}")
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f'push_warning("找不到音乐文件: " + str({path}))')


@stmt_node("GKNodeStopSound")
def _stmt_stop_sound(ctx, node, indent):
    fade = gd_number(getattr(node, "gk_fade", 0.5))
    music_var = ctx.declare_var("_gk_music", "AudioStreamPlayer")
    ctx.add_line(indent, f"if {music_var} != null:")
    if float(getattr(node, "gk_fade", 0.5) or 0.0) > 0.0:
        fade_var = ctx.new_temp("fade")
        ctx.add_line(indent + 1, f"var {fade_var} := create_tween()")
        ctx.add_line(indent + 1, f"{fade_var}.tween_property({music_var}, \"volume_db\", -80.0, {fade})")
        ctx.add_line(indent + 1, f"{fade_var}.tween_callback({music_var}.stop)")
    else:
        ctx.add_line(indent + 1, f"{music_var}.stop()")


# ==========================================================================
# 存档
# ==========================================================================
# 为什么用"内存字典 + 显式保存"而不是每次写入都落盘:
#   落盘是磁盘 IO，在 _process 里每帧调用会卡帧。分开之后
#   "存档_写入"是廉价的，"存档_保存"才是真正写文件的那一次。
_SAVE_DATA_VAR = "_gk_save_data"


def _save_dict(ctx):
    """声明(不初始化)存档字典成员变量。"""
    return ctx.declare_var(_SAVE_DATA_VAR, "Dictionary")


def _ensure_save_block(ctx, data, path, slot):
    """保证存档字典在本代码块内已从磁盘载入一次。

    ── 为什么不靠"谁先谁后"标记 ────────────────────────────────────
    最初用"显式载入时打个标记，后面的节点就别插守卫"的办法。
    但它对顺序敏感:守卫只能插在**已经吐出的行**里，所以
    "写 -> 载入 -> 写"这种顺序下第二个写仍然会插一段多余的读盘。

    改成直接扫描本函数已经生成的代码:只要里面已经有 `_gk_read_save(`，
    就说明这个函数已经读过盘了，不需要再插守卫。
    这跟顺序无关，也不会漏。
    """
    ctx.need_save_helpers = True
    sink = getattr(ctx, "sink", None)
    lines = getattr(sink, "lines", None)
    if not isinstance(lines, list):
        # 拿不到收集器时退化成"就地守卫一次"。仍然正确，只是啰嗦一点。
        slot_expr = gd_string(slot)
        ctx.add_line(0, f"{path} = _gk_save_file({slot_expr})")
        ctx.add_line(0, f'if {path} == "":')
        ctx.add_line(1, f"{path} = _gk_save_file({slot_expr})")
        ctx.add_line(1, f"{data} = _gk_read_save({path})")
        return

    if any("_gk_read_save(" in text for text in lines):
        return

    slot_expr = gd_string(slot)
    index = len(lines)
    lines.insert(index, f"    {path} = _gk_save_file({slot_expr})")
    lines.insert(index + 1, f'    if {path} == "":')
    lines.insert(index + 2, f"        {path} = _gk_save_file({slot_expr})")
    lines.insert(index + 3, f"        {data} = _gk_read_save({path})")


@stmt_node("GKNodeSaveSet")
def _stmt_save_set(ctx, node, indent):
    data = _save_dict(ctx)
    path = ctx.declare_var("_gk_save_path", "String")
    slot = getattr(node, "gk_slot", "save1")
    key = ctx.input_string(node, "键名", "")
    socket = ctx._socket(node, "值")
    if socket is not None and getattr(socket, "is_linked", False):
        value = ctx.expr_of(socket)
    else:
        value = gd_literal(node.input_value(ctx, "值", 0))
    ctx.add_line(indent, f"{data}[{key}] = {value}")
    _ensure_save_block(ctx, data, path, slot)


@stmt_node("GKNodeSaveGet")
def _stmt_save_get(ctx, node, indent):
    data = _save_dict(ctx)
    path = ctx.declare_var("_gk_save_path", "String")
    slot = getattr(node, "gk_slot", "save1")
    key = ctx.input_string(node, "键名", "")
    # ── 类型"不表态"(不是 Variant)—— 第三十三轮整理示例时踩到的 ──────
    # 存档里的值可能是任意类型,所以**没人声明时**按 Variant 生成
    # (`ctx.vars` 里存 None,写出来就是 Variant,见 generator 的成员变量段)。
    # 但把它硬说成 "Variant" 会和用户**声明过的类型**打架:平台跳跃示例
    # 声明 best_score 是 float,导出就多一条
    #   "变量『best_score』同时被当成 float 和 Variant 使用,请改用不同的变量名"
    # —— 指点用户去改一个根本没问题的变量名。传 None = "我没意见",
    # 有声明就用声明的,没声明仍然是 Variant。
    var_name = ctx.declare_var(getattr(node, "gk_var", "value"), None)
    default = gd_literal(node.input_value(ctx, "默认值", 0.0))
    ctx.add_line(indent, f"{var_name} = {data}.get({key}, {default})")
    ctx.read_vars.add(var_name)
    _ensure_save_block(ctx, data, path, slot)


@stmt_node("GKNodeSaveWrite")
def _stmt_save_write(ctx, node, indent):
    data = _save_dict(ctx)
    path = ctx.declare_var("_gk_save_path", "String")
    slot = getattr(node, "gk_slot", "save1")
    ctx.add_line(indent, f"{path} = _gk_save_file({gd_string(slot)})")
    ctx.add_line(indent, f"_gk_write_save({path}, {data})")
    _ensure_save_block(ctx, data, path, slot)


@stmt_node("GKNodeSaveLoad")
def _stmt_save_load(ctx, node, indent):
    data = _save_dict(ctx)
    path = ctx.declare_var("_gk_save_path", "String")
    slot = getattr(node, "gk_slot", "save1")
    ctx.need_save_helpers = True
    # 设好路径后直接读。同块内后面的存档节点扫描到本行已有的 _gk_read_save(
    # 就不会再插一段惰性守卫，所以不需要额外的抑制标记。
    ctx.add_line(indent, f"{path} = _gk_save_file({gd_string(slot)})")
    ctx.add_line(indent, f"{data} = _gk_read_save({path})")


@stmt_node("GKNodeSaveHas")
def _stmt_save_has(ctx, node, indent):
    """『存档_是否存在』:两个执行出口(有档 / 没档)。

    ── 为什么出口不叫『完成』 ──────────────────────────────────────
    emit_statement() 在 handler 跑完后会**自动**沿名为『完成』的出口继续。
    如果这里留一个『完成』出口，它就会把我的 else 分支再执行一遍
    (实测:『没存档』被打印两次)。所以两个出口都起具体名字，
    自动续接找不到『完成』自然就停住，分支完全由本函数接管。
    """
    slot = getattr(node, "gk_slot", "save1")
    ctx.need_save_helpers = True
    ctx.add_line(indent, f"if _gk_save_exists({gd_string(slot)}):")
    before = len(ctx.sink)
    _chain(ctx, node, indent + 1, "有档")
    if len(ctx.sink) == before:
        ctx.add_line(indent + 1, "pass")
    has_else = any(True for _ in _next_nodes(node, "没档"))
    if has_else:
        ctx.add_line(indent, "else:")
        before = len(ctx.sink)
        _chain(ctx, node, indent + 1, "没档")
        if len(ctx.sink) == before:
            ctx.add_line(indent + 1, "pass")


@stmt_node("GKNodeSaveDelete")
def _stmt_save_delete(ctx, node, indent):
    slot = getattr(node, "gk_slot", "save1")
    data = _save_dict(ctx)
    ctx.need_save_helpers = True
    ctx.add_line(indent, f"_gk_delete_save({gd_string(slot)})")
    # 删档后内存里那份也要清掉，否则"删了档还能读到旧数据"
    ctx.add_line(indent, f"{data}.clear()")


# ==========================================================================
# 屏幕效果
# ==========================================================================
@stmt_node("GKNodeScreenFlash")
def _stmt_screen_flash(ctx, node, indent):
    color = getattr(node, "gk_color", (1.0, 1.0, 1.0, 1.0))
    alpha = float(getattr(node, "gk_alpha", 0.6) or 0.6)
    duration = _tunable_number(ctx, node, "时长", "0.3", "flash_time")

    rect_var = ctx.new_temp("flash")
    tween_var = ctx.new_temp("flash_tween")
    ctx.add_line(indent, f"var {rect_var} := ColorRect.new()")
    ctx.add_line(indent, f"{rect_var}.color = Color({gd_number(color[0])}, {gd_number(color[1])}, "
                         f"{gd_number(color[2])}, {gd_number(alpha)})")
    # 不挡鼠标，否则闪光期间点不动 UI
    ctx.add_line(indent, f"{rect_var}.mouse_filter = Control.MOUSE_FILTER_IGNORE")
    ctx.add_line(indent, f"{rect_var}.set_anchors_preset(Control.PRESET_FULL_RECT)")
    # 盖在 UI 之上(UI 一般在 layer 0~10)
    ctx.add_line(indent, f"{rect_var}.z_index = 100")
    ctx.add_line(indent, f"add_child({rect_var})")
    ctx.add_line(indent, f"var {tween_var} := create_tween()")
    # 只淡 alpha:`color:a` 是子属性路径，改它不影响 RGB
    ctx.add_line(indent, f"{tween_var}.tween_property({rect_var}, \"color:a\", 0.0, {duration})")
    ctx.add_line(indent, f"{tween_var}.tween_callback({rect_var}.queue_free)")


@stmt_node("GKNodeScreenFade")
def _stmt_screen_fade(ctx, node, indent):
    color = getattr(node, "gk_color", (0.0, 0.0, 0.0, 1.0))
    duration = _tunable_number(ctx, node, "时长", "1.0", "fade_time")
    to_opaque = bool(getattr(node, "gk_to_opaque", True))
    hold = float(getattr(node, "gk_hold", 0.0) or 0.0)

    # 用一个持久的遮罩层，才能做"淡出 -> 换场景 -> 淡入"
    layer_var = ctx.declare_var("_gk_fade_layer", "CanvasLayer")
    rect_var = ctx.declare_var("_gk_fade_rect", "ColorRect")

    ctx.add_line(indent, f"if {layer_var} == null:")
    ctx.add_line(indent + 1, f"{layer_var} = CanvasLayer.new()")
    ctx.add_line(indent + 1, f"{layer_var}.layer = 100")
    ctx.add_line(indent + 1, f"add_child({layer_var})")
    ctx.add_line(indent + 1, f"{rect_var} = ColorRect.new()")
    ctx.add_line(indent + 1, f"{rect_var}.mouse_filter = Control.MOUSE_FILTER_IGNORE")
    ctx.add_line(indent + 1, f"{rect_var}.set_anchors_preset(Control.PRESET_FULL_RECT)")
    ctx.add_line(indent + 1, f"{layer_var}.add_child({rect_var})")

    r, g, b = color[0], color[1], color[2]
    start_alpha = 0.0 if to_opaque else 1.0
    end_alpha = 1.0 if to_opaque else 0.0
    ctx.add_line(indent, f"{rect_var}.color = Color({gd_number(r)}, {gd_number(g)}, "
                         f"{gd_number(b)}, {gd_number(start_alpha)})")
    ctx.add_line(indent, f"{rect_var}.visible = true")

    tween_var = ctx.new_temp("fade")
    ctx.add_line(indent, f"var {tween_var} := create_tween()")
    ctx.add_line(indent, f"{tween_var}.tween_property({rect_var}, \"color:a\", "
                         f"{gd_number(end_alpha)}, {duration})")
    if not to_opaque:
        # 淡回透明后把遮罩收起来，否则它会一直盖着(虽然透明，仍会吃掉性能)
        ctx.add_line(indent, f"{tween_var}.tween_callback(func(): {rect_var}.visible = false)")
    if hold > 0.0:
        ctx.add_line(indent, f"{tween_var}.tween_interval({gd_number(hold)})")
    # 等渐变结束再走『完成』出口，这样"淡出 -> 切场景"的顺序才正确
    ctx.add_line(indent, f"await {tween_var}.finished")


# ==========================================================================
# 界面
# ==========================================================================
# ── 为什么 UI 节点都"运行时自己建"而不是要求用户先摆控件 ──────────────
# 让用户为每一行字、每一条血条在场景树里手摆一个 Control，是"引擎在给
# 用户添活"，而且摆完还要在节点图里选来选去。这里统一按需创建，
# 同一个节点重复调用只是改内容，不会叠出新控件。
def _ui_root(ctx, indent):
    """取(必要时创建)UI 根容器。返回 (layer变量, root变量)。

    Control 必须是 CanvasLayer 的子节点才能浮在游戏画面之上，
    而且要铺满全屏、默认不吃鼠标事件(否则按钮以外的 UI 会挡住点击)。
    """
    layer = ctx.declare_var("_gk_ui_layer", "CanvasLayer")
    root = ctx.declare_var("_gk_ui_root", "Control")
    ctx.need_ui_helpers = True
    ctx.add_line(indent, "_gk_ensure_ui(self)")
    return layer, root


# ── 『对齐』:枚举 id -> Godot 的 Control 预设(第三十七轮)───────────────
# 默认『左上』= 老行为(X / Y 绝对定位),老图生成的结果一字不变;
# 其余八种用**锚点预设**,窗口大小一变控件跟着屏幕的角 / 边 / 中心走。
#
# 写法是**真 Godot 实测**出来的(1152x648 -> 800x600 对照过坐标):
#     set_anchors_and_offsets_preset(Control.PRESET_CENTER,
#         Control.PRESET_MODE_KEEP_SIZE, margin)
# 居中 = (视口/2 - 尺寸/2),右下 = (视口 - 尺寸 - margin) —— 分毫不差。
UI_ALIGN_PRESETS = {
    "TOP_CENTER": "PRESET_CENTER_TOP",
    "TOP_RIGHT": "PRESET_TOP_RIGHT",
    "CENTER_LEFT": "PRESET_CENTER_LEFT",
    "CENTER": "PRESET_CENTER",
    "CENTER_RIGHT": "PRESET_CENTER_RIGHT",
    "BOTTOM_LEFT": "PRESET_BOTTOM_LEFT",
    "BOTTOM_CENTER": "PRESET_CENTER_BOTTOM",
    "BOTTOM_RIGHT": "PRESET_BOTTOM_RIGHT",
}


def _ui_list_names(ctx):
    """图里所有『界面_列表』节点的名字(第三十八轮)。

    用来判断"这个控件的父控件是不是一个容器" —— 是的话它由容器排版,
    生成器**不能再写 position / 锚点**(容器会把它们覆盖掉,真 Godot 实测)。
    """
    cache = getattr(ctx, "_ui_list_names", None)
    if cache is None:
        cache = set()
        tree = getattr(ctx, "tree", None)
        for node in getattr(tree, "nodes", ()) or ():
            if getattr(node, "bl_idname", "") == "GKNodeUiList":
                cache.add(str(getattr(node, "name", "")))
        ctx._ui_list_names = cache
    return cache


def _ui_in_list(ctx, node):
    """这个控件的『父控件』是不是一个『界面_列表』(容器)。"""
    parent = str(getattr(node, "gk_parent_widget", "") or "").strip()
    return bool(parent) and parent in _ui_list_names(ctx)


def _ui_size(ctx, indent, widget, node, width, height):
    """设控件尺寸 —— 普通控件用 `size`,挂在**列表**里的用 `custom_minimum_size`。

    ── 为什么两种都得有(真 Godot 实测)────────────────────────────
    容器会完全接管子控件的矩形:挂进 VBox 的按钮,自己设的 position /
    锚点 / size 会被它覆盖(实测:故意设成 (999,999) 也被拉回 (0,0))。
    容器认的是 **custom_minimum_size** —— 想控制"这一行多高"就填它。
    """
    if _ui_in_list(ctx, node):
        ctx.add_line(indent, f"{widget}.custom_minimum_size = "
                             f"Vector2({width}, {height})")
    else:
        ctx.add_line(indent, f"{widget}.size = Vector2({width}, {height})")


def _ui_place(ctx, indent, widget, node, def_x, def_y, size_mode="keep"):
    """摆一个界面控件:对齐 = 左上时按 X / Y(老行为),否则挂锚点。

    size_mode:
      * "keep"    —— 用控件**当前尺寸**(按钮 / 进度条 / 图片 / 面板 自己
                     设过 size,居中的就是"按这个尺寸居中");
      * "minsize" —— 用**最小尺寸**:Label 的"尺寸"就是文字本身,
                     刚建出来时它的 size 是 0,用 KeepSize 会挂在锚点上
                     不居中(锚点的取法就是这样,别弄反)。
    """
    if _ui_in_list(ctx, node):
        # 挂在列表(容器)里的控件**由容器排版** —— 写 position / 锚点是白写
        # (容器会覆盖),写了反而让人以为"填了 X / Y 没生效"是 bug
        return
    align = str(getattr(node, "gk_align", "TOP_LEFT") or "TOP_LEFT")
    if align == "TOP_LEFT" or align not in UI_ALIGN_PRESETS:
        x = _tunable_number(ctx, node, "X", def_x, "ui_x")
        y = _tunable_number(ctx, node, "Y", def_y, "ui_y")
        ctx.add_line(indent, f"{widget}.position = Vector2({x}, {y})")
        return
    margin = gd_number(float(getattr(node, "gk_margin", 24.0) or 0.0))
    mode = ("Control.PRESET_MODE_MINSIZE" if size_mode == "minsize"
            else "Control.PRESET_MODE_KEEP_SIZE")
    ctx.add_line(indent,
                 f"{widget}.set_anchors_and_offsets_preset("
                 f"Control.{UI_ALIGN_PRESETS[align]}, {mode}, {margin})")


def _ui_add_to(ctx, indent, widget, node, root, after=None):
    """把控件挂到父亲下面 —— 『父控件』留空 = 直接挂界面根。

    ── 为什么需要"父控件"(第三十七轮)────────────────────────────────
    暂停菜单 = 一块底板 + 几个按钮。如果按钮和底板**并列**挂在界面根下,
    隐藏底板时按钮还留在屏幕上(`visible` 不会往下传 —— 它们不是一家人)。
    挂成底板的子节点之后,隐藏底板就整块菜单一起消失,这就是 Godot 里
    "容器"的语义;顺带锚点也变成**相对底板**算,按钮在菜单里居中排。

    没有父控件(或名字填错)时退回挂界面根 + 告警 —— 不静默丢控件。
    """
    after = list(after or [])
    parent_name = str(getattr(node, "gk_parent_widget", "") or "").strip()
    if not parent_name:
        ctx.add_line(indent, f"{root}.add_child({widget})")
        for line in after:
            ctx.add_line(indent, line)
        return
    var = ctx.new_temp("parent")
    if parent_name in _ui_list_names(ctx):
        # 父控件是『界面_列表』-> 要挂到它**里面那个排版的容器**上,
        # 不是挂到滚动容器本身(挂错的话子控件会浮着不排队)
        ctx.need_list_helper = True
        ctx.add_line(indent, f"var {var} := _gk_list_body({gd_string(parent_name)})")
    else:
        ctx.add_line(indent, f"var {var} := _gk_find_widget({gd_string(parent_name)})")
    ctx.add_line(indent, f"if {var} != null:")
    ctx.add_line(indent + 1, f"{var}.add_child({widget})")
    for line in after:
        ctx.add_line(indent + 1, line)
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f"{root}.add_child({widget})")
    for line in after:
        ctx.add_line(indent + 1, line)
    ctx.add_line(indent + 1,
                 f'push_warning("找不到父控件『{parent_name}』，'
                 f'『{node.name}』已挂到界面根下")')


def _ui_image_res(value):
    """界面图片的引用 -> `res://` 路径。

    ── 两套写法 ────────────────────────────────────────────────────
      * 填 `res://...` -> 原样用(用户自己放进工程的文件);
      * 填 Blender 里的**图片名**(点『选图片』挑的)-> 导出时会把它存成
        `art/ui_<名字>_<摘要>.png`,这里算的是同一个文件名。
    ── 为什么名字尾巴要挂摘要 ──────────────────────────────────────
    和曲线资源同一个坑:图片名可以是中文,压成 ASCII 之后
    『头像』和『立绘』会撞成同一个文件名(互相覆盖,两处显示同一张图)。
    名字算法**两边必须一致**:见 `curve_assets.image_file_name`。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("res://"):
        return text
    from .. import curve_assets
    return curve_assets.image_res_path(text)


@stmt_node("GKNodeUiText")
def _stmt_ui_text(ctx, node, indent):
    _layer, root = _ui_root(ctx, indent)
    widget = ctx.declare_var(f"_gk_ui_{node.name}", "Label", key=node_lib.node_key(node))
    text = ctx.input_string(node, "文字", "")
    size = int(getattr(node, "gk_size", 24) or 24)
    color = getattr(node, "gk_color", (1.0, 1.0, 1.0, 1.0))

    ctx.add_line(indent, f"if {widget} == null:")
    ctx.add_line(indent + 1, f"{widget} = Label.new()")
    ctx.add_line(indent + 1, f"{widget}.name = {gd_string(node.name)}")
    ctx.add_line(indent + 1, f"{widget}.add_theme_font_size_override(\"font_size\", {size})")
    ctx.add_line(indent + 1, f"{widget}.add_theme_color_override(\"font_color\", "
                             f"Color({gd_number(color[0])}, {gd_number(color[1])}, "
                             f"{gd_number(color[2])}, {gd_number(color[3])}))")
    # 文字控件默认不吃鼠标，否则会挡住下面的按钮
    ctx.add_line(indent + 1, f"{widget}.mouse_filter = Control.MOUSE_FILTER_IGNORE")
    _ui_add_to(ctx, indent + 1, widget, node, root)
    # 文字先赋值、再摆位:居中用的是"最小尺寸 = 文字大小",
    # 文字还没填的时候 min size 是 0(摆完再填就偏了)
    ctx.add_line(indent, f"{widget}.text = str({text})")
    _ui_place(ctx, indent, widget, node, "20.0", "20.0", size_mode="minsize")


@stmt_node("GKNodeUiBar")
def _stmt_ui_bar(ctx, node, indent):
    _layer, root = _ui_root(ctx, indent)
    widget = ctx.declare_var(f"_gk_ui_{node.name}", "ProgressBar", key=node_lib.node_key(node))
    value = ctx.input_number(node, "数值", "100.0")
    max_value = gd_number(getattr(node, "gk_max", 100.0))
    width = gd_number(getattr(node, "gk_width", 200.0))
    height = gd_number(getattr(node, "gk_height", 16.0))
    color = getattr(node, "gk_color", (0.85, 0.25, 0.25, 1.0))

    ctx.add_line(indent, f"if {widget} == null:")
    ctx.add_line(indent + 1, f"{widget} = ProgressBar.new()")
    ctx.add_line(indent + 1, f"{widget}.name = {gd_string(node.name)}")
    ctx.add_line(indent + 1, f"{widget}.max_value = {max_value}")
    ctx.add_line(indent + 1, f"{widget}.show_percentage = false")
    # ProgressBar 的填充色靠 theme override，不设就是系统默认灰
    ctx.add_line(indent + 1, "var _fill := StyleBoxFlat.new()")
    ctx.add_line(indent + 1, f"_fill.bg_color = Color({gd_number(color[0])}, "
                             f"{gd_number(color[1])}, {gd_number(color[2])}, "
                             f"{gd_number(color[3])})")
    ctx.add_line(indent + 1, f"{widget}.add_theme_stylebox_override(\"fill\", _fill)")
    ctx.add_line(indent + 1, f"{widget}.mouse_filter = Control.MOUSE_FILTER_IGNORE")
    _ui_add_to(ctx, indent + 1, widget, node, root)
    # 尺寸先定、再挂锚点 —— 居中的算法要用到当前尺寸
    _ui_size(ctx, indent, widget, node, width, height)
    _ui_place(ctx, indent, widget, node, "20.0", "60.0")
    # 钳到合法区间:血量变成 -1 或超过上限时进度条会画出诡异的样子
    ctx.add_line(indent, f"{widget}.value = clampf({value}, 0.0, {max_value})")


@stmt_node("GKNodeUiButton")
def _stmt_ui_button(ctx, node, indent):
    """按钮:点击后沿『点击』出口继续。

    ── 为什么出口不叫『完成』 ──────────────────────────────────────
    emit_statement() 会在 handler 跑完后自动沿『完成』出口继续执行。
    按钮的语义是"点了才往下走"，如果叫『完成』，节点图一连线就会在
    按钮创建的那一刻立刻执行后面的逻辑(比如直接开始游戏)。
    所以出口叫『点击』，自动续接找不到『完成』就停住，
    真正的续接由回调函数 _gk_on_<节点名> 负责。
    """
    _layer, root = _ui_root(ctx, indent)
    widget = ctx.declare_var(f"_gk_ui_{node.name}", "Button", key=node_lib.node_key(node))
    # 回调函数名同样要防同名:两个按钮可以都叫『开始按钮』，
    # 若共用 gk_on_button 就会有两个同名函数，Godot 直接报重复定义。
    handler = ctx.declare_func_name(f"gk_on_{snake_case(node.name, 'button')}", key=node_lib.node_key(node))
    text = ctx.input_string(node, "文字", "")
    width = gd_number(getattr(node, "gk_width", 160.0))
    height = gd_number(getattr(node, "gk_height", 48.0))

    ctx.add_line(indent, f"if {widget} == null:")
    ctx.add_line(indent + 1, f"{widget} = Button.new()")
    ctx.add_line(indent + 1, f"{widget}.name = {gd_string(node.name)}")
    # 按钮必须吃鼠标事件，否则点不动
    ctx.add_line(indent + 1, f"{widget}.mouse_filter = Control.MOUSE_FILTER_STOP")
    ctx.add_line(indent + 1, f"{widget}.pressed.connect({handler})")
    _ui_add_to(ctx, indent + 1, widget, node, root)
    _ui_size(ctx, indent, widget, node, width, height)
    _ui_place(ctx, indent, widget, node, "100.0", "200.0")
    ctx.add_line(indent, f"{widget}.text = str({text})")

    # 把『点击』出口的后续代码放进一个独立函数
    #
    # ⚠ `new_section()` 会换掉写入目标而且**不会自己换回来**(见
    #   GenContext.capture 的说明)。所以这里必须手动存一份再还回去 ——
    #   否则『完成』出口那条链(接着建下一个按钮 / 后面的逻辑)会写进
    #   这个回调段里,而且**在快照之后**写入,等于被静默丢掉。
    #   实测现场:暂停菜单的三个按钮只建出来第一个,后面的全没了。
    saved_sink = ctx.sink
    lines = ctx.new_section()
    ctx._active.clear()
    prev_event = ctx._current_event
    ctx._current_event = node.name
    _chain(ctx, node, 1, "点击")
    if len(lines) == 0:
        lines.add(1, "pass")
    ctx._current_event = prev_event
    ctx.sink = saved_sink
    ctx.extra_sections.append({
        "signature": f"func {handler}() -> void:",
        "lines": list(lines.lines),
        "empty_body": "    pass",
        "separator": True,
    })


@stmt_node("GKNodeUiHide")
def _stmt_ui_hide(ctx, node, indent):
    widget_name = (getattr(node, "gk_widget", "") or "").strip()
    visible = "true" if getattr(node, "gk_visible", False) else "false"
    if not widget_name:
        ctx.warn(f"『{node.bl_label}』没有填控件名，这一句被跳过了")
        return
    # 用 group 查而不是路径查:UI 控件是运行时挂在 CanvasLayer 下的，
    # 路径会随场景变化，按名字找更稳。
    var = ctx.new_temp("widget")
    ctx.add_line(indent, f"var {var} := _gk_find_widget({gd_string(widget_name)})")
    ctx.add_line(indent, f"if {var} != null:")
    ctx.add_line(indent + 1, f"{var}.visible = {visible}")
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f'push_warning("找不到界面控件: " + {gd_string(widget_name)})')
    ctx.need_ui_helpers = True


@stmt_node("GKNodeUiImage")
def _stmt_ui_image(ctx, node, indent):
    """图片(TextureRect):图标 / 立绘 / logo。

    ── 图片从哪来(第三十七轮)────────────────────────────────────────
    『图片』填 Blender 里的图片名(『选图片』挑的)-> 导出时存成
    `art/ui_<名字>_<摘要>.png`(见 asset_export.export_ui_images,
    文件名算法在 curve_assets.image_file_name,**两边必须一致**);
    填 `res://...` 则原样用。图片找不到时给告警,不让 load() 静默返回 null。
    """
    _layer, root = _ui_root(ctx, indent)
    widget = ctx.declare_var(f"_gk_ui_{node.name}", "TextureRect", key=node_lib.node_key(node))
    socket = ctx._socket(node, "图片")
    if socket is not None and getattr(socket, "is_linked", False):
        # 运行中换图:路径由图里算出来
        path_expr = ctx.input_string(node, "图片", "")
    else:
        raw = str(getattr(node, "gk_image", "") or "").strip()
        if not raw:
            ctx.warn(f"『{node.bl_label}』还没选图片 —— 游戏里这一块是空的"
                     f"(点节点上的『选图片』挑一张)")
        path_expr = gd_string(_ui_image_res(raw))
    width = gd_number(getattr(node, "gk_width", 0.0))
    height = gd_number(getattr(node, "gk_height", 0.0))
    keep = bool(getattr(node, "gk_keep", True))
    tint = getattr(node, "gk_tint", (1.0, 1.0, 1.0, 1.0))

    ctx.add_line(indent, f"if {widget} == null:")
    ctx.add_line(indent + 1, f"{widget} = TextureRect.new()")
    ctx.add_line(indent + 1, f"{widget}.name = {gd_string(node.name)}")
    ctx.add_line(indent + 1, f"{widget}.mouse_filter = Control.MOUSE_FILTER_IGNORE")
    # EXPAND_IGNORE_SIZE:尺寸由我们说了算(不跟着贴图原始大小跑),
    # 这样"填了宽高"和"用原图大小"两种情况能走同一套代码
    ctx.add_line(indent + 1, f"{widget}.expand_mode = TextureRect.EXPAND_IGNORE_SIZE")
    stretch = ("STRETCH_KEEP_ASPECT_CENTERED" if keep else "STRETCH_SCALE")
    ctx.add_line(indent + 1, f"{widget}.stretch_mode = TextureRect.{stretch}")
    ctx.add_line(indent + 1, f"{widget}.modulate = Color({gd_number(tint[0])}, "
                             f"{gd_number(tint[1])}, {gd_number(tint[2])}, "
                             f"{gd_number(tint[3])})")
    _ui_add_to(ctx, indent + 1, widget, node, root)

    tex = ctx.new_temp("ui_tex")
    w_var = ctx.new_temp("ui_w")
    h_var = ctx.new_temp("ui_h")
    ctx.add_line(indent, f"if ResourceLoader.exists({path_expr}):")
    ctx.add_line(indent + 1, f"{widget}.texture = load({path_expr})")
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f'push_warning("找不到界面图片: " + str({path_expr}))')
    # ── 尺寸:填 0 的那个方向按**原图比例**算 ────────────────────────
    # 「宽 0 高 0」= 原图大小;只填一边 = 另一边按比例推出来
    # (比例模式下不会把图拉变形);两边都填了 = 按填的走。
    ctx.add_line(indent, f"var {tex} := {widget}.texture")
    ctx.add_line(indent, f"var {w_var} := {width}")
    ctx.add_line(indent, f"var {h_var} := {height}")
    ctx.add_line(indent, f"if {tex} != null:")
    ctx.add_line(indent + 1, f"var _tw := float({tex}.get_width())")
    ctx.add_line(indent + 1, f"var _th := float({tex}.get_height())")
    ctx.add_line(indent + 1, f"if {w_var} <= 0.0 and {h_var} <= 0.0:")
    ctx.add_line(indent + 2, f"{w_var} = _tw")
    ctx.add_line(indent + 2, f"{h_var} = _th")
    ctx.add_line(indent + 1, f"elif {w_var} <= 0.0:")
    if keep:
        ctx.add_line(indent + 2, f"{w_var} = _tw * ({h_var} / _th) if _th > 0.0 else _tw")
    else:
        ctx.add_line(indent + 2, f"{w_var} = _tw")
    ctx.add_line(indent + 1, f"elif {h_var} <= 0.0:")
    if keep:
        ctx.add_line(indent + 2, f"{h_var} = _th * ({w_var} / _tw) if _tw > 0.0 else _th")
    else:
        ctx.add_line(indent + 2, f"{h_var} = _th")
    _ui_size(ctx, indent, widget, node, w_var, h_var)
    _ui_place(ctx, indent, widget, node, "20.0", "20.0")


@stmt_node("GKNodeUiPanel")
def _stmt_ui_panel(ctx, node, indent):
    """半透明底板(菜单背景 / 提示条)。"""
    _layer, root = _ui_root(ctx, indent)
    widget = ctx.declare_var(f"_gk_ui_{node.name}", "Panel", key=node_lib.node_key(node))
    color = getattr(node, "gk_color", (0.06, 0.07, 0.10, 0.72))
    radius = int(getattr(node, "gk_radius", 12) or 0)
    width = gd_number(getattr(node, "gk_width", 480.0))
    height = gd_number(getattr(node, "gk_height", 240.0))
    bottom = bool(getattr(node, "gk_bottom", True))
    start_hidden = bool(getattr(node, "gk_start_hidden", False))

    ctx.add_line(indent, f"if {widget} == null:")
    ctx.add_line(indent + 1, f"{widget} = Panel.new()")
    ctx.add_line(indent + 1, f"{widget}.name = {gd_string(node.name)}")
    ctx.add_line(indent + 1, "var _box := StyleBoxFlat.new()")
    ctx.add_line(indent + 1, f"_box.bg_color = Color({gd_number(color[0])}, "
                             f"{gd_number(color[1])}, {gd_number(color[2])}, "
                             f"{gd_number(color[3])})")
    for corner in ("top_left", "top_right", "bottom_left", "bottom_right"):
        ctx.add_line(indent + 1, f"_box.corner_radius_{corner} = {radius}")
    ctx.add_line(indent + 1, f"{widget}.add_theme_stylebox_override(\"panel\", _box)")
    # 底板不吃鼠标:它是装饰,不该挡住按钮 / 游戏里的点击
    ctx.add_line(indent + 1, f"{widget}.mouse_filter = Control.MOUSE_FILTER_IGNORE")
    # 垫底 / 开局藏 都跟着"挂到谁下面"走:有父控件时是相对**兄弟**
    # 垫底(move_child 只对自家孩子有效,对 root 调会报 not a child)
    after = []
    if bottom:
        after.append(f"{widget}.get_parent().move_child({widget}, 0)")
    if start_hidden:
        after.append(f"{widget}.visible = false")
    _ui_add_to(ctx, indent + 1, widget, node, root, after=after)
    _ui_size(ctx, indent, widget, node, width, height)
    _ui_place(ctx, indent, widget, node, "20.0", "20.0")


@stmt_node("GKNodeUiSet")
def _stmt_ui_set(ctx, node, indent):
    """运行时改控件:位置 / 大小 / 透明度 / 着色 / 文字(勾哪项改哪项)。"""
    ctx.need_ui_helpers = True
    widget_name = (getattr(node, "gk_widget", "") or "").strip()
    flags = [bool(getattr(node, key, False)) for key in
             ("gk_set_pos", "gk_set_size", "gk_set_alpha", "gk_set_color",
              "gk_set_text")]
    if not widget_name:
        ctx.warn(f"『{node.bl_label}』没有填控件名，这一句被跳过了")
        return
    if not any(flags):
        ctx.warn(f"『{node.bl_label}』一项都没勾(位置 / 大小 / 透明度 / 颜色 / "
                 f"文字)—— 这句什么都不会做")
        return
    var = ctx.new_temp("widget")
    ctx.add_line(indent, f"var {var} := _gk_find_widget({gd_string(widget_name)})")
    ctx.add_line(indent, f"if {var} == null:")
    ctx.add_line(indent + 1, f'push_warning("找不到界面控件: " + {gd_string(widget_name)})')
    ctx.add_line(indent, "else:")
    body = indent + 1
    if getattr(node, "gk_set_pos", False):
        x = _tunable_number(ctx, node, "X", "0.0", "ui_x")
        y = _tunable_number(ctx, node, "Y", "0.0", "ui_y")
        ctx.add_line(body, f"{var}.position = Vector2({x}, {y})")
    if getattr(node, "gk_set_size", False):
        width = gd_number(getattr(node, "gk_width", 0.0))
        height = gd_number(getattr(node, "gk_height", 0.0))
        ctx.add_line(body, f"{var}.size = Vector2({width}, {height})")
    if getattr(node, "gk_set_color", False):
        color = getattr(node, "gk_color", (1.0, 1.0, 1.0, 1.0))
        # 只换 RGB,透明度归『改透明度』那一项管(两项互不打扰)
        ctx.add_line(body, f"{var}.modulate = Color({gd_number(color[0])}, "
                           f"{gd_number(color[1])}, {gd_number(color[2])}, "
                           f"{var}.modulate.a)")
    if getattr(node, "gk_set_alpha", False):
        alpha = _tunable_number(ctx, node, "透明度", "1.0", "ui_alpha")
        ctx.add_line(body, f"{var}.modulate = Color({var}.modulate.r, "
                           f"{var}.modulate.g, {var}.modulate.b, {alpha})")
    if getattr(node, "gk_set_text", False):
        text = ctx.input_string(node, "文字", "")
        ctx.add_line(body, f"if {var} is Label or {var} is Button:")
        ctx.add_line(body + 1, f"{var}.text = str({text})")
        ctx.add_line(body, "else:")
        ctx.add_line(body + 1, f'push_warning("『{widget_name}』不是文字控件，'
                               f'『改文字』跳过")')


@stmt_node("GKNodeUiPauseGame")
def _stmt_ui_pause(ctx, node, indent):
    """暂停 / 继续整个世界(`get_tree().paused`)+ 鼠标 + 菜单显隐。

    ── 为什么暂停菜单能点得动(真 Godot 实测,不是猜的)──────────────
    `get_tree().paused = true` 之后,按钮的 `pressed` **照样触发**
    (四种 process_mode 摆法全试过,连默认的 INHERIT 都能点)——
    因为 GUI 输入是视口在收,不是"节点的每帧处理"。所以暂停菜单
    用普通『界面_按钮』就能做,不需要额外的 process_mode 设置。
    ── Esc 热键为什么要走信号 ────────────────────────────────────────
    暂停之后本脚本的 `_input` / `_physics_process` 都停了 —— 挂在
    `get_tree().process_frame` 信号上的回调**不受暂停影响**(同样是实测),
    所以 Esc 由信号回调来读,这是"暂停之后还有人能解冻"的唯一活口。
    """
    ctx.need_ui_helpers = True
    ctx.need_pause_helpers = True
    paused = bool(getattr(node, "gk_pause", True))
    menu = (getattr(node, "gk_menu", "") or "").strip()
    mouse = bool(getattr(node, "gk_show_mouse", True))
    hotkey = bool(getattr(node, "gk_hotkey", True))
    # 暂停状态的那几个成员变量(只声明一次;名字固定,同图共享)
    ctx.declare_var("_gk_pause_mouse_was", "int", initial="-1")
    if hotkey:
        # 监听用到的两个状态变量只在**勾了热键**时声明 —— 没勾时它们
        # 是没人读的成员变量(白占一行)
        ctx.declare_var("_gk_pause_armed", "bool", initial="false")
        ctx.declare_var("_gk_pause_esc_held", "bool", initial="false")
        # 热键的默认设置取**第一个勾了 Esc 的**节点(一份图一个热键就够)
        if not getattr(ctx, "_pause_hotkey_taken", False):
            ctx._pause_hotkey_taken = True
            ctx.declare_var("_gk_pause_menu", "String",
                            initial=gd_string(menu))
            ctx.declare_var("_gk_pause_show_mouse", "bool",
                            initial="true" if mouse else "false")
        ctx.add_line(indent, "_gk_arm_pause_watch(self)")
        ctx.note(f"『{node.bl_label}』勾了『Esc 也能开关』—— Esc 由它统一"
                 f"开关暂停,不要再接『输入_按键』(Esc)的线,否则一次按下"
                 f"会被处理两遍(开了又关)")
    if not menu:
        ctx.note(f"『{node.bl_label}』没填『菜单控件』—— 世界会暂停,"
                 f"但菜单面板不会自动弹出来")
    action = "true" if paused else "false"
    mouse_literal = "true" if mouse else "false"
    ctx.add_line(indent, f"_gk_apply_pause({action}, {gd_string(menu)}, {mouse_literal})")


@stmt_node("GKNodeUiDialog")
def _stmt_ui_dialog(ctx, node, indent):
    """对话框:显示 + 逐字 + 等"点一下 / 按一下"。

    ── 生成的语句(这里是**阻塞**的)────────────────────────────────
        _gk_dialog_show(...)           造面板 / 填字 / 开始逐字
        while not _gk_dialog_clicked:  await ...   # 等第一次点按
        if _gk_dialog_typing():                      # 还在打字 -> 先补完
            _gk_dialog_finish()
            <再等一次点按>
        _gk_dialog_hide()                            # 关掉,继续后面的逻辑

    `await` 不会打断执行流:后面的节点挂在本节点的『完成』出口上,
    await 结束后照常续上(和『屏幕_渐变遮罩』同一个套路)。
    点按在 `_gk_dialog_input_event` 里采集(走 `_input`,**不挑控件**,
    所以对话框打开时点哪儿、按哪个键都算"下一句")。
    """
    ctx.need_ui_helpers = True
    ctx.need_dialog_helpers = True
    # ── 成员变量 + 点按采集(在**节点遍历阶段**登记)──────────────────
    # `_input(event)` 的派发表在 generator 里是**早于辅助函数**拼的,
    # 辅助函数阶段再登记就晚了(派发行里不会出现这个函数名)。
    ctx.declare_var("_gk_dialog_panel", "Panel")
    ctx.declare_var("_gk_dialog_name", "Label")
    ctx.declare_var("_gk_dialog_text", "Label")
    ctx.declare_var("_gk_dialog_portrait", "TextureRect")
    ctx.declare_var("_gk_dialog_tween", "Tween")
    ctx.declare_var("_gk_dialog_clicked", "bool", initial="false")
    ctx.register_input_handler(
        "gk_dialog_input_event", reset=False, body=[
            "    if _gk_dialog_panel == null or not _gk_dialog_panel.visible:",
            "        return",
            "    if event is InputEventMouseButton and event.pressed "
            "and event.button_index == MOUSE_BUTTON_LEFT:",
            "        _gk_dialog_clicked = true",
            "    elif event is InputEventKey and event.pressed and not event.echo:",
            "        _gk_dialog_clicked = true",
        ])
    speaker = gd_string(str(getattr(node, "gk_speaker", "") or ""))
    fallback = str(getattr(node, "gk_text", "") or "")
    text = ctx.input_string(node, "文字", fallback)
    portrait = gd_string(_ui_image_res(getattr(node, "gk_image", "")))
    speed = gd_number(float(getattr(node, "gk_speed", 30.0) or 0.0))
    width = gd_number(getattr(node, "gk_width", 720.0))
    height = gd_number(getattr(node, "gk_height", 150.0))
    margin = gd_number(getattr(node, "gk_margin", 48.0))

    ctx.add_line(indent, f"_gk_dialog_show(self, {speaker}, str({text}), "
                         f"{speed}, {portrait}, {width}, {height}, {margin})")
    ctx.add_line(indent, "while not _gk_dialog_clicked:")
    ctx.add_line(indent + 1, "await get_tree().process_frame")
    # 打字中先补完:玩家急着看下一句时不用等它一个字一个字蹦
    ctx.add_line(indent, "if _gk_dialog_typing():")
    ctx.add_line(indent + 1, "_gk_dialog_finish()")
    ctx.add_line(indent + 1, "_gk_dialog_clicked = false")
    ctx.add_line(indent + 1, "while not _gk_dialog_clicked:")
    ctx.add_line(indent + 2, "await get_tree().process_frame")
    ctx.add_line(indent, "_gk_dialog_hide()")


# ==========================================================================
# 界面第二版(第三十八轮):列表 / 缓动 / 九宫格 / 输入框 / 焦点
# ==========================================================================
@stmt_node("GKNodeUiList")
def _stmt_ui_list(ctx, node, indent):
    """列表容器:滚动容器 + 里面一个会排队的 VBox / HBox。

    ── 为什么"滚动"和"排版"是两层(真 Godot 实测)──────────────────
    ScrollContainer 自己**不是**排版的容器(它只负责"内容比它高就出滚动条"),
    真正的排版是它里面那个 VBoxContainer 干的。所以『界面_列表』建两层:
        ScrollContainer(名字 = 节点名,别人按名字找它)
          └── VBoxContainer(名字 = 节点名 + "内容",子控件挂这儿)
    子控件挂哪一层由 `_gk_list_body()` 决定(见 _ui_add_to)。
    """
    _layer, root = _ui_root(ctx, indent)
    scroll = bool(getattr(node, "gk_scroll", True))
    vertical = bool(getattr(node, "gk_vertical", True))
    separation = int(getattr(node, "gk_separation", 8) or 0)
    width = gd_number(getattr(node, "gk_width", 320.0))
    height = gd_number(getattr(node, "gk_height", 200.0))
    inner_type = "VBoxContainer" if vertical else "HBoxContainer"
    outer_type = "ScrollContainer" if scroll else inner_type
    widget = ctx.declare_var(f"_gk_ui_{node.name}", outer_type,
                             key=node_lib.node_key(node))

    ctx.add_line(indent, f"if {widget} == null:")
    ctx.add_line(indent + 1, f"{widget} = {outer_type}.new()")
    ctx.add_line(indent + 1, f"{widget}.name = {gd_string(node.name)}")
    if scroll:
        inner = ctx.declare_var(f"_gk_ui_{node.name}_内容", inner_type,
                                key=(node_lib.node_key(node), "内容"))
        ctx.add_line(indent + 1, f"{inner} = {inner_type}.new()")
        ctx.add_line(indent + 1, f"{inner}.name = {gd_string(node.name + '内容')}")
        ctx.add_line(indent + 1, f"{inner}.add_theme_constant_override("
                                 f"\"separation\", {separation})")
        ctx.add_line(indent + 1, f"{widget}.add_child({inner})")
        # 滚动容器要**吃掉**滚轮事件(IGNORE 的话鼠标滚轮会穿过去,列表滚不动)
        ctx.add_line(indent + 1, f"{widget}.mouse_filter = Control.MOUSE_FILTER_STOP")
    else:
        ctx.add_line(indent + 1, f"{widget}.add_theme_constant_override("
                                 f"\"separation\", {separation})")
        # 纯排版容器不吃鼠标:点它空白处应该穿到游戏 / 下面的控件
        ctx.add_line(indent + 1, f"{widget}.mouse_filter = Control.MOUSE_FILTER_IGNORE")
    _ui_add_to(ctx, indent + 1, widget, node, root)
    _ui_size(ctx, indent, widget, node, width, height)
    _ui_place(ctx, indent, widget, node, "12.0", "60.0")


@stmt_node("GKNodeUiListFill")
def _stmt_ui_list_fill(ctx, node, indent):
    """把一串东西灌进列表(先清空旧行再建,每帧刷不会越堆越多)。"""
    ctx.need_ui_helpers = True
    list_name = (getattr(node, "gk_list", "") or "").strip()
    if not list_name:
        ctx.warn(f"『{node.bl_label}』没有填『列表』(哪个列表?)—— 这一句被跳过了")
        return
    if list_name not in _ui_list_names(ctx):
        ctx.warn(f"『{node.bl_label}』的『列表』填的是『{list_name}』,"
                 f"但图里没有一个叫这个名字的『界面_列表』—— 这一句被跳过了")
        return
    socket = ctx._socket(node, "内容")
    if socket is not None and getattr(socket, "is_linked", False):
        source = ctx.expr_of(socket)
    else:
        var_name = (getattr(node, "gk_var", "") or "").strip()
        if var_name:
            source = ctx.declare_var(var_name, "Array")
            ctx.read_vars.add(source)
        else:
            raw = str(getattr(node, "gk_text", "") or "")
            items = [part.strip() for part in raw.split("|") if part.strip()]
            source = "[" + ", ".join(gd_string(item) for item in items) + "]"
    line_height = gd_number(float(getattr(node, "gk_line_height", 0.0) or 0.0))
    size = int(getattr(node, "gk_size", 20) or 20)
    color = getattr(node, "gk_color", (1.0, 1.0, 1.0, 1.0))
    ctx.need_list_helper = True      # _gk_list_fill 内部要用 _gk_list_body
    ctx.need_list_fill_helper = True
    helper = ctx.declare_func_name("_gk_list_fill", key="__list_fill__")
    ctx.add_line(indent, f"{helper}({gd_string(list_name)}, {source}, "
                         f"{line_height}, {size}, "
                         f"Color({gd_number(color[0])}, {gd_number(color[1])}, "
                         f"{gd_number(color[2])}, {gd_number(color[3])}))")


@stmt_node("GKNodeUiTween")
def _stmt_ui_tween(ctx, node, indent):
    """把一个界面控件的属性缓动过去(菜单淡入 / 提示条滑进来)。"""
    ctx.need_ui_helpers = True
    widget_name = (getattr(node, "gk_widget", "") or "").strip()
    if not widget_name:
        ctx.warn(f"『{node.bl_label}』没有填『控件名』—— 这一句被跳过了")
        return
    prop = str(getattr(node, "gk_property", "ALPHA") or "ALPHA")
    to = ctx.input_number(node, "到", gd_number(getattr(node, "gk_to", 1.0)))
    time = gd_number(float(getattr(node, "gk_time", 0.4) or 0.0))
    delay = float(getattr(node, "gk_delay", 0.0) or 0.0)
    wait = bool(getattr(node, "gk_wait", False))
    trans = str(getattr(node, "gk_trans", "TRANS_SINE") or "TRANS_SINE")
    ease = str(getattr(node, "gk_ease", "EASE_OUT") or "EASE_OUT")
    # 位置 X / Y 是**相对**的(+80 = 往右 80):写法差一点点,见节点说明
    relative = prop in ("POS_X", "POS_Y")
    paths = {
        "ALPHA": "modulate:a",
        "POS_X": "position:x",
        "POS_Y": "position:y",
        "SCALE": "scale:x",
        "WIDTH": "size:x",
        "HEIGHT": "size:y",
    }
    path = paths.get(prop, "modulate:a")

    var = ctx.new_temp("widget")
    tween = ctx.new_temp("ui_tween")
    ctx.add_line(indent, f"var {var} := _gk_find_widget({gd_string(widget_name)})")
    ctx.add_line(indent, f"if {var} == null:")
    ctx.add_line(indent + 1, f'push_warning("找不到界面控件: " + {gd_string(widget_name)})')
    ctx.add_line(indent, "else:")
    body = indent + 1
    ctx.add_line(body, f"var {tween} := {var}.create_tween()")
    ctx.add_line(body, f"{tween}.set_trans(Tween.{trans})")
    ctx.add_line(body, f"{tween}.set_ease(Tween.{ease})")
    if delay > 0.0:
        ctx.add_line(body, f"{tween}.tween_interval({gd_number(delay)})")
    if relative:
        axis = "x" if prop == "POS_X" else "y"
        ctx.add_line(body, f"{tween}.tween_property({var}, \"{path}\", "
                           f"{var}.position.{axis} + ({to}), {time})")
    else:
        ctx.add_line(body, f"{tween}.tween_property({var}, \"{path}\", {to}, {time})")
    if wait:
        # 勾了"等它播完":后面的节点等动画结束再走(不勾就边播边往下)
        ctx.add_line(body, f"await {tween}.finished")


@stmt_node("GKNodeUiNinePatch")
def _stmt_ui_ninepatch(ctx, node, indent):
    """九宫格底图(九个切片,拉多大都不变形)。"""
    _layer, root = _ui_root(ctx, indent)
    widget = ctx.declare_var(f"_gk_ui_{node.name}", "NinePatchRect",
                             key=node_lib.node_key(node))
    raw = str(getattr(node, "gk_image", "") or "").strip()
    if not raw:
        ctx.warn(f"『{node.bl_label}』还没选图片 —— 游戏里这块是空的"
                 f"(点节点上的『选图片』挑一张)")
    path_expr = gd_string(_ui_image_res(raw))
    patch = int(getattr(node, "gk_slice", 12) or 0)
    width = gd_number(getattr(node, "gk_width", 360.0))
    height = gd_number(getattr(node, "gk_height", 120.0))
    tint = getattr(node, "gk_tint", (1.0, 1.0, 1.0, 1.0))
    bottom = bool(getattr(node, "gk_bottom", False))

    ctx.add_line(indent, f"if {widget} == null:")
    ctx.add_line(indent + 1, f"{widget} = NinePatchRect.new()")
    ctx.add_line(indent + 1, f"{widget}.name = {gd_string(node.name)}")
    for side in ("left", "top", "right", "bottom"):
        ctx.add_line(indent + 1, f"{widget}.patch_margin_{side} = {patch}")
    ctx.add_line(indent + 1, f"{widget}.mouse_filter = Control.MOUSE_FILTER_IGNORE")
    ctx.add_line(indent + 1, f"{widget}.modulate = Color({gd_number(tint[0])}, "
                             f"{gd_number(tint[1])}, {gd_number(tint[2])}, "
                             f"{gd_number(tint[3])})")
    after = []
    if bottom:
        after.append(f"{widget}.get_parent().move_child({widget}, 0)")
    _ui_add_to(ctx, indent + 1, widget, node, root, after=after)
    ctx.add_line(indent, f"if ResourceLoader.exists({path_expr}):")
    ctx.add_line(indent + 1, f"{widget}.texture = load({path_expr})")
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f'push_warning("找不到界面图片: " + str({path_expr}))')
    _ui_size(ctx, indent, widget, node, width, height)
    _ui_place(ctx, indent, widget, node, "12.0", "12.0")


@stmt_node("GKNodeUiInput")
def _stmt_ui_input(ctx, node, indent):
    """输入框(LineEdit):回车沿『提交』出口继续。

    ── 为什么出口不叫『完成』 ──────────────────────────────────────
    和按钮同一个道理:叫『完成』会在输入框刚建出来就往下跑。
    真正的续接在 `text_submitted` 回调里(真 Godot 实测:**回车**触发、
    点别处失焦**不**触发)。
    """
    _layer, root = _ui_root(ctx, indent)
    widget = ctx.declare_var(f"_gk_ui_{node.name}", "LineEdit",
                             key=node_lib.node_key(node))
    handler = ctx.declare_func_name(
        f"gk_on_{snake_case(node.name, 'input')}_submit", key=node_lib.node_key(node))
    text = ctx.input_string(node, "默认文字", "")
    placeholder = gd_string(str(getattr(node, "gk_placeholder", "") or ""))
    max_length = int(getattr(node, "gk_max_length", 24) or 0)
    secret = bool(getattr(node, "gk_password", False))
    focus = bool(getattr(node, "gk_focus", False))
    width = gd_number(getattr(node, "gk_width", 240.0))
    height = gd_number(getattr(node, "gk_height", 40.0))

    ctx.add_line(indent, f"if {widget} == null:")
    ctx.add_line(indent + 1, f"{widget} = LineEdit.new()")
    ctx.add_line(indent + 1, f"{widget}.name = {gd_string(node.name)}")
    ctx.add_line(indent + 1, f"{widget}.placeholder_text = {placeholder}")
    ctx.add_line(indent + 1, f"{widget}.max_length = {max_length}")
    if secret:
        ctx.add_line(indent + 1, f"{widget}.secret = true")
    # 输入框必须吃鼠标 / 键盘,否则点不进去
    ctx.add_line(indent + 1, f"{widget}.mouse_filter = Control.MOUSE_FILTER_STOP")
    ctx.add_line(indent + 1, f"{widget}.text_submitted.connect({handler})")
    _ui_add_to(ctx, indent + 1, widget, node, root)
    _ui_size(ctx, indent, widget, node, width, height)
    _ui_place(ctx, indent, widget, node, "20.0", "20.0")
    ctx.add_line(indent, f"{widget}.text = str({text})")
    if focus:
        ctx.add_line(indent, f"{widget}.grab_focus()")

    saved_sink = ctx.sink
    lines = ctx.new_section()
    ctx._active.clear()
    prev_event = ctx._current_event
    ctx._current_event = node.name
    _chain(ctx, node, 1, "提交")
    if len(lines) == 0:
        lines.add(1, "pass")
    ctx._current_event = prev_event
    ctx.sink = saved_sink
    ctx.extra_sections.append({
        "signature": f"func {handler}(_text: String) -> void:",
        "lines": list(lines.lines),
        "empty_body": "    pass",
        "separator": True,
    })


@stmt_node("GKNodeUiFocus")
def _stmt_ui_focus(ctx, node, indent):
    """把键盘 / 手柄焦点放到某个控件上(方向键就能选按钮了)。"""
    ctx.need_ui_helpers = True
    widget_name = (getattr(node, "gk_widget", "") or "").strip()
    focus = bool(getattr(node, "gk_focus", True))
    if not widget_name:
        # 留空 = 清掉焦点(回到鼠标操作)
        ctx.add_line(indent, "var _gk_focused := get_viewport().gui_get_focus_owner()")
        ctx.add_line(indent, "if _gk_focused != null:")
        ctx.add_line(indent + 1, "_gk_focused.release_focus()")
        return
    var = ctx.new_temp("widget")
    ctx.add_line(indent, f"var {var} := _gk_find_widget({gd_string(widget_name)})")
    ctx.add_line(indent, f"if {var} == null:")
    ctx.add_line(indent + 1, f'push_warning("找不到界面控件: " + {gd_string(widget_name)})')
    ctx.add_line(indent, "else:")
    if focus:
        ctx.add_line(indent + 1, f"{var}.grab_focus()")
    else:
        ctx.add_line(indent + 1, f"{var}.release_focus()")


@stmt_node("GKNodeFloatText")
def _stmt_float_text(ctx, node, indent):
    """『界面_飘字』:在目标头顶冒一行一次性文字(+1 / -1 / 伤害数字)。

    主体在 `ensure_float_text_helper` 生成的那个函数里(它负责造 Label、
    上飘、淡出、自删)—— 这里只拼参数:
      文字 = 前缀 + `_gk_num_text(数值)`
    数字走『文本_数字』那套规则,所以整数不会飘成 "+1.0"。
    """
    anchor = ctx.target_expr(node, "gk_target", hint="节点")
    prefix = gd_string(str(getattr(node, "gk_prefix", "+") or ""))
    fallback = gd_number(float(getattr(node, "gk_value", 1.0) or 0.0))
    value = ctx.input_number(node, "数值", fallback)
    color = getattr(node, "gk_color", (1.0, 0.9, 0.35, 1.0))
    seconds = gd_number(float(getattr(node, "gk_time", 0.8) or 0.0))
    rise = gd_number(float(getattr(node, "gk_rise", 1.2) or 0.0))
    from .expressions import ensure_float_text_helper, ensure_num_text_helper
    helper = ensure_float_text_helper(ctx)
    number = ensure_num_text_helper(ctx)
    # self 永远是有效的,不用包 `if self != null`(包了只是噪音)
    body = indent
    if anchor != "self":
        ctx.add_line(indent, f"if {anchor} != null:")
        body = indent + 1
    ctx.add_line(body,
                 f"{helper}({anchor}, {prefix} + {number}({value}), "
                 f"Color({gd_number(color[0])}, {gd_number(color[1])}, "
                 f"{gd_number(color[2])}, {gd_number(color[3])}), "
                 f"{seconds}, {rise})")


# ==========================================================================
# 屏幕效果
# ==========================================================================
# 全屏滤镜着色器。用 SCREEN_TEXTURE / SCREEN_UV 读当前画面再染色 ——
# 这是 Godot 4 画布着色器里读屏的标准做法(实测编译通过)。
_GD_FILTER_SHADER = """shader_type canvas_item;
uniform float amount : hint_range(0.0, 1.0) = 0.0;
uniform vec4 tint : source_color = vec4(1.0, 1.0, 1.0, 1.0);
void fragment() {
    vec4 screen = texture(SCREEN_TEXTURE, SCREEN_UV);
    COLOR = vec4(mix(screen.rgb, tint.rgb, amount), screen.a);
}
"""
_FILTER_SHADER_CONST = "_GK_FILTER_SHADER"


def _screen_filter_mat(ctx):
    """取(必要时创建)全屏滤镜的材质。返回 (材质变量, 根变量, 矩形变量)。"""
    layer = ctx.declare_var("_gk_filter_layer", "CanvasLayer")
    rect = ctx.declare_var("_gk_filter_rect", "ColorRect")
    mat = ctx.declare_var("_gk_filter_mat", "ShaderMaterial")
    ctx.need_screen_filter = True
    return mat, layer, rect


@stmt_node("GKNodeScreenFilter")
def _stmt_screen_filter(ctx, node, indent):
    mat, layer, rect = _screen_filter_mat(ctx)
    amount = _tunable_number(ctx, node, "强度", "0.0", "filter_amount")
    color = getattr(node, "gk_color", (1.0, 0.15, 0.15, 1.0))
    softness = float(getattr(node, "gk_softness", 0.0) or 0.0)

    # 第一次用到时把全屏遮罩建起来(着色器源码作为常量放在脚本开头)
    ctx.add_line(indent, "_gk_ensure_filter(self)")
    ctx.add_line(indent, f"{mat}.set_shader_parameter(\"tint\", "
                         f"Color({gd_number(color[0])}, {gd_number(color[1])}, "
                         f"{gd_number(color[2])}, {gd_number(color[3])}))")
    if softness > 0.0:
        tween_var = ctx.new_temp("filter")
        ctx.add_line(indent, f"var {tween_var} := create_tween()")
        ctx.add_line(indent, f"{tween_var}.tween_method(func(v): "
                             f"{mat}.set_shader_parameter(\"amount\", v), "
                             f"float({mat}.get_shader_parameter(\"amount\")), "
                             f"{amount}, {gd_number(softness)})")
    else:
        ctx.add_line(indent, f"{mat}.set_shader_parameter(\"amount\", {amount})")
