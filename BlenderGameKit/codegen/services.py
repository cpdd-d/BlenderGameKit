"""周边服务类发射器:音频、存档、屏幕效果 / UI、滤镜。"""

from ..nodes.node_base import TARGET_SELF, gd_node_path, gd_string, snake_case
from ..nodes import nodes as node_lib

from .literals import gd_literal, gd_number
from .registry import (STMT_HANDLERS, _chain, _next_nodes, expr_node,
                       stmt_node)
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


def _emit_stream_loop(ctx, indent, stream_var):
    """给一条流打开"循环播放"—— **按流类型分别设**(第八十一轮抽出来)。

    Godot 没有一个统一点:Ogg / MP3 是 `.loop`,`wav` 是 `loop_mode`。
    只设一种的话换个格式就"播一遍就没了",而且不报错 —— 很难查。
    (素材导入时的 loop 勾选也会被这里覆盖:节点上的『循环』才是准的)

    ── wav 还要补 loop_end(第八十一轮真机抓到的)───────────────────
    只设 `loop_mode = LOOP_FORWARD` 的话,导入器给的 `loop_end` 是 0 ——
    循环长度 = 0,流**当场就结束**(实测:『音效_播放』勾循环后
    1.9 秒探针读到 `playing = false`,声音早停了,一声都听不见)。
    所以按『时长 × 采样率』算出**采样帧数**填进 `loop_end`
    (和第七十七轮场景 BGM 的 wav 循环同一个坑、同一个算法)。

    BGM(『音效_音乐』)和循环音效(『音效_播放』勾『循环』)共用这一份。
    """
    ctx.add_line(indent, f"if {stream_var} is AudioStreamOggVorbis:")
    ctx.add_line(indent + 1,
                 f"({stream_var} as AudioStreamOggVorbis).loop = true")
    ctx.add_line(indent, f"elif {stream_var} is AudioStreamMP3:")
    ctx.add_line(indent + 1, f"({stream_var} as AudioStreamMP3).loop = true")
    ctx.add_line(indent, f"elif {stream_var} is AudioStreamWAV:")
    ctx.add_line(indent + 1,
                 f"({stream_var} as AudioStreamWAV).loop_mode = "
                 f"AudioStreamWAV.LOOP_FORWARD")
    ctx.add_line(indent + 1, f"({stream_var} as AudioStreamWAV).loop_end = "
                 f"int(({stream_var} as AudioStreamWAV).get_length() * "
                 f"float(({stream_var} as AudioStreamWAV).mix_rate))")


@stmt_node("GKNodePlaySound")
def _stmt_play_sound(ctx, node, indent):
    """一次性音效:运行时建播放器、播完自己删。

    ── 为什么不要求用户先摆好 AudioStreamPlayer ─────────────────---─
    让用户为每个音效在场景里摆一个播放器节点，是"引擎在给用户添活"。
    这里按需建、播完销毁，节点图里写一条就够了。

    ── 循环音效(第八十一轮)──────────────────────────────────────
    勾了『循环』= 一直响(引擎 / 风声):流按格式打开循环,播放器**不**
    挂 `finished` 自删(那个信号永远不会来),而是登记进 `_gk_loops`
    (名字 -> 播放器),用『音效_停止』填同一个名字来停;
    同名再播一次会先停掉旧的,不会越叠越多。
    """
    path = ctx.input_string(node, "音频路径", "")
    volume = _tunable_number(ctx, node, "音量", "0.0", "sfx_volume")
    pitch = _tunable_number(ctx, node, "音高", "1.0", "sfx_pitch")
    player_type = _audio_player_type(node)
    host = _audio_host(ctx, node)
    loop = bool(getattr(node, "gk_loop", False))
    name = str(getattr(node, "gk_name", "") or "").strip() or str(node.name)

    stream_var = ctx.new_temp("sfx_stream")
    player_var = ctx.new_temp("sfx")
    if loop:
        ctx.declare_var("_gk_loops", "Dictionary", initial="{}")
    # 音频文件缺失时给出可读的报错，而不是让 load() 静默返回 null 后再炸在别处
    ctx.add_line(indent, f"var {stream_var}: AudioStream = load({path}) as AudioStream")
    ctx.add_line(indent, f"if {stream_var} != null:")
    if loop:
        _emit_stream_loop(ctx, indent + 1, stream_var)
        key_var = ctx.new_temp("sfx_key")
        old_var = ctx.new_temp("sfx_old")
        ctx.add_line(indent + 1, f"var {key_var} := {gd_string(name)}")
        ctx.add_line(indent + 1, f"if _gk_loops.has({key_var}):")
        ctx.add_line(indent + 2, f"var {old_var}: Node = _gk_loops[{key_var}]")
        ctx.add_line(indent + 2, f"if is_instance_valid({old_var}):")
        ctx.add_line(indent + 3, f'{old_var}.call("stop")')
        ctx.add_line(indent + 3, f"{old_var}.queue_free()")
    ctx.add_line(indent + 1, f"var {player_var} := {player_type}.new()")
    ctx.add_line(indent + 1, f"{player_var}.stream = {stream_var}")
    ctx.add_line(indent + 1, f"{player_var}.volume_db = {volume}")
    ctx.add_line(indent + 1, f"{player_var}.pitch_scale = {pitch}")
    ctx.add_line(indent + 1,
                 f"{player_var}.max_polyphony = {1 if loop else 4}")
    ctx.add_line(indent + 1, f"{host}.add_child({player_var})")
    ctx.add_line(indent + 1, f"{player_var}.play()")
    if loop:
        ctx.add_line(indent + 1, f"_gk_loops[{key_var}] = {player_var}")
    else:
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
    loop = bool(getattr(node, "gk_loop", True))
    bus = str(getattr(node, "gk_bus", "Master") or "Master").strip() or "Master"

    music_var = ctx.declare_var("_gk_music", "AudioStreamPlayer")
    stream_var = ctx.new_temp("bgm_stream")

    ctx.add_line(indent, f"var {stream_var}: AudioStream = load({path}) as AudioStream")
    if loop:
        # ── 循环要**按流类型**分别设(第六十四轮)────────────────────
        # (第八十一轮抽成 `_emit_stream_loop`:循环音效也用同一份)
        _emit_stream_loop(ctx, indent, stream_var)
    ctx.add_line(indent, f"if {stream_var} != null:")
    # 重复播放同一首时不要打断(主菜单返回游戏很常见)
    ctx.add_line(indent + 1, f"if {music_var} != null and {music_var}.stream == {stream_var} and {music_var}.playing:")
    ctx.add_line(indent + 2, "pass")
    ctx.add_line(indent + 1, "else:")
    ctx.add_line(indent + 2, f"if {music_var} != null:")
    ctx.add_line(indent + 3, f"{music_var}.queue_free()")
    ctx.add_line(indent + 2, f"{music_var} = AudioStreamPlayer.new()")
    ctx.add_line(indent + 2, f"{music_var}.stream = {stream_var}")
    ctx.add_line(indent + 2, f"{music_var}.bus = {gd_string(bus)}")
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
    """『音效_停止』:停 BGM(可淡出),或者停一个**同名的循环音效**。

    ── 两种模式(第八十一轮加的第二种)────────────────────────────
      * 『名字』留空 -> 老行为:停 `_gk_music`(可淡出),**一字不变**;
      * 『名字』填了 -> 从 `_gk_loops` 里按名字取出循环音的播放器,
        stop + 释放 + 抹掉登记。循环音一停就没了,不用淡出
        (引擎声说停就停才自然)。
    """
    name = str(getattr(node, "gk_name", "") or "").strip()
    if name:
        ctx.declare_var("_gk_loops", "Dictionary", initial="{}")
        key_var = ctx.new_temp("stop_key")
        old_var = ctx.new_temp("stop_old")
        ctx.add_line(indent, f"var {key_var} := {gd_string(name)}")
        ctx.add_line(indent, f"if _gk_loops.has({key_var}):")
        ctx.add_line(indent + 1, f"var {old_var}: Node = _gk_loops[{key_var}]")
        ctx.add_line(indent + 1, f"if is_instance_valid({old_var}):")
        ctx.add_line(indent + 2, f'{old_var}.call("stop")')
        ctx.add_line(indent + 2, f"{old_var}.queue_free()")
        ctx.add_line(indent + 1, f"_gk_loops.erase({key_var})")
        return
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


@stmt_node("GKNodeBusVolume")
def _stmt_bus_volume(ctx, node, indent):
    """设置音频**总线**音量(可渐变,第五十九轮)。

    ── 为什么是"总线"而不是"音乐播放器" ────────────────────────────
    『音效_音乐』只管它自己那一条流;BGM 淡出要"整层降下去"时,
    它压不住音效 / 环境音。总线音量(AudioServer)是唯一能一次压住
    一整类声音的口子 —— 恐怖段落里把 Master 压暗、或者给 BGM 单独
    建一条 Music 总线再压它,都走这里。
    """
    bus = str(getattr(node, "gk_bus", "Master") or "Master").strip() or "Master"
    target = ctx.input_number(node, "目标音量", gd_number(getattr(node, "gk_db", 0.0)))
    fade = ctx.input_number(node, "时长", gd_number(getattr(node, "gk_fade", 0.5)))

    bus_var = ctx.new_temp("bus")
    ctx.add_line(indent, f"var {bus_var} := AudioServer.get_bus_index({gd_string(bus)})")
    ctx.add_line(indent, f"if {bus_var} >= 0:")
    ctx.add_line(indent + 1, f"if {fade} <= 0.0:")
    ctx.add_line(indent + 2, f"AudioServer.set_bus_volume_db({bus_var}, {target})")
    ctx.add_line(indent + 1, "else:")
    from_var = ctx.new_temp("bus_from")
    tween_var = ctx.new_temp("bus_tween")
    ctx.add_line(indent + 2, f"var {from_var} := AudioServer.get_bus_volume_db({bus_var})")
    ctx.add_line(indent + 2, f"var {tween_var} := create_tween()")
    # 总线音量是个 float,没有属性路径可 tween —— 用 tween_method 每次回调 set
    ctx.add_line(indent + 2,
                 f"{tween_var}.tween_method(func(_v: float): "
                 f"AudioServer.set_bus_volume_db({bus_var}, _v), "
                 f"{from_var}, {target}, {fade})")
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f'push_warning("[GK] 找不到音频总线: " + {gd_string(bus)})')


# ── 恐怖氛围套件(第五十九轮)────────────────────────────────────────
@expr_node("GKNodeTension")
def _expr_tension(ctx, node):
    """『氛围_紧张度』:离『威胁』越近越高,输出 0~1。

    ── 为什么是纯表达式、不做平滑 ──────────────────────────────────
    表达式节点可能在**别的函数作用域**里被求值(见 animation.py 里
    移动状态那次踩坑),所以不能生成中间变量。这里只算"当前瞬间"的
    紧张度;需要平滑的『氛围_心跳』自己内部做。
    """
    from .expressions import ensure_position_helper
    socket = ctx._socket(node, "威胁")
    base = None
    if socket is not None and getattr(socket, "is_linked", False):
        threat = ctx.expr_of(socket)
    else:
        raw = str(getattr(node, "gk_threat", "") or "").strip()
        if not raw or raw == TARGET_SELF:
            ctx.warn("『氛围_紧张度』没有指定『威胁』—— 它永远返回 0"
                     "(请在卡片里选一个『威胁』物体,或把引脚接上)")
            base = "0.0"
        else:
            threat = ctx.target_expr(node, "gk_threat", hint="威胁")
    if base is None:
        reader = ensure_position_helper(ctx)
        near = ctx.input_number(node, "近距", gd_number(getattr(node, "gk_near", 3.0)))
        far = ctx.input_number(node, "远距", gd_number(getattr(node, "gk_far", 15.0)))
        dist = f"({reader}({threat}) - {reader}(self)).length()"
        base = (f"clampf((({far}) - {dist}) / "
                f"maxf(({far}) - ({near}), 0.0001), 0.0, 1.0)")
    return _apply_seen_floor(ctx, node, base)


def _apply_seen_floor(ctx, node, base):
    """把『被看见』/『被看见下限』套到紧张度表达式上(第七十轮)。

    『被看见』为真时,紧张度**至少抬到下限** —— 被盯上比单纯"离得近"更慌。

    ── 三种情形 ────────────────────────────────────────────────────
      * 没接引脚、也没勾(默认 false)-> **原样返回**,老图生成物一个字不变;
      * 勾上了(固定 true)-> `maxf(base, 下限)`(不需要三元);
      * 接了引脚 -> `maxf(base, 下限 if 被看见 else 0.0)`(没被看见就退回距离)。

    『被看见下限』填 0 = 不用这个功能(同样原样返回)。
    """
    try:
        floor = float(getattr(node, "gk_seen_floor", 0.8))
    except (TypeError, ValueError):
        floor = 0.8
    if floor <= 0.0:
        return base
    seen = ctx.input_bool_expr(node, "被看见", "false")
    if seen == "false":
        return base
    if seen == "true":
        return f"maxf({base}, {gd_number(floor)})"
    return f"maxf({base}, ({gd_number(floor)}) if {seen} else 0.0)"


@stmt_node("GKNodeHeartbeat")
def _stmt_heartbeat(ctx, node, indent):
    """『氛围_心跳』:紧张度驱动的心跳(越快越响,低于阈值停)。

    ── 计时不用 delta 参数,用 get_physics_process_delta_time() ──────
    本节点被接在『每当_物理帧』后面时确实有 delta,但用户也可能从
    『每当_准备』/触发器里接一条 —— 那时没有 delta 参数。直接向引擎要
    固定步长最省心,而且心跳本来就该按物理帧走。
    """
    path_raw = str(getattr(node, "gk_path", "") or "").strip()
    if not path_raw:
        ctx.warn("『氛围_心跳』没填『心跳音效』—— 它不会发出声音"
                 "(可以先不填,等有素材了再补)")
        return

    player = ctx.declare_var("_gk_heart_player", "AudioStreamPlayer")
    timer = ctx.declare_var("_gk_heart_timer", "float")
    tense = ctx.declare_var("_gk_heart_tension", "float")

    tension = ctx.input_number(node, "紧张度", gd_number(getattr(node, "gk_tension", 0.0)))
    slow = ctx.input_number(node, "最慢间隔", gd_number(getattr(node, "gk_slow_interval", 1.1)))
    fast = ctx.input_number(node, "最快间隔", gd_number(getattr(node, "gk_fast_interval", 0.35)))
    quiet_db = ctx.input_number(node, "最轻音量", gd_number(getattr(node, "gk_quiet_db", -18.0)))
    loud_db = ctx.input_number(node, "最响音量", gd_number(getattr(node, "gk_loud_db", -4.0)))
    quiet = ctx.input_number(node, "安静阈值", gd_number(getattr(node, "gk_quiet", 0.15)))
    smooth = ctx.input_number(node, "平滑秒数", gd_number(getattr(node, "gk_smooth", 1.5)))

    dt = ctx.new_temp("hb_dt")
    ctx.add_line(indent, f"var {dt} := get_physics_process_delta_time()")
    # 平滑:朝目标紧张度靠拢(除以平滑时间;0 会被 maxf 兜住 = 不平滑)
    ctx.add_line(indent, f"{tense} += ({tension} - {tense}) * "
                         f"clampf({dt} / maxf({smooth}, 0.0001), 0.0, 1.0)")

    # 播放器懒建一次(和『音效_播放』一个路数:运行时建,不要求先摆节点)
    stream = ctx.new_temp("hb_stream")
    ctx.add_line(indent, f"if {player} == null:")
    ctx.add_line(indent + 1, f"var {stream}: AudioStream = load({gd_string(path_raw)}) as AudioStream")
    ctx.add_line(indent + 1, f"if {stream} != null:")
    ctx.add_line(indent + 2, f"{player} = AudioStreamPlayer.new()")
    ctx.add_line(indent + 2, f"{player}.stream = {stream}")
    ctx.add_line(indent + 2, f'{player}.bus = "Master"')
    ctx.add_line(indent + 2, f"self.add_child({player})")

    interval = ctx.new_temp("hb_iv")
    ctx.add_line(indent, f"if {player} != null:")
    ctx.add_line(indent + 1, f"if {tense} <= {quiet}:")
    ctx.add_line(indent + 2, f"{player}.volume_db = -80.0")
    ctx.add_line(indent + 2, f"{timer} = 0.0")
    ctx.add_line(indent + 1, "else:")
    ctx.add_line(indent + 2, f"{timer} += {dt}")
    ctx.add_line(indent + 2, f"var {interval} := lerpf({slow}, {fast}, {tense})")
    ctx.add_line(indent + 2, f"if {timer} >= {interval}:")
    ctx.add_line(indent + 3, f"{timer} = 0.0")
    ctx.add_line(indent + 3, f"{player}.volume_db = lerpf({quiet_db}, {loud_db}, {tense})")
    ctx.add_line(indent + 3, f"{player}.play()")


@stmt_node("GKNodeScare")
def _stmt_scare(ctx, node, indent):
    """『惊吓_事件』:相机震动 + 屏幕闪白 + 音效,三连一起上。

    ── 为什么在 services.py 而不是 camera.py ──────────────────────
    它同时用到"相机"(camera.py)、"屏幕"(本文件)、"音效"(本文件)——
    放中间这个文件,只对相机那边做一次**函数级导入**,不会造成
    两个模块互相 import。
    """
    from .camera import _install_camera_shake
    # ① 相机震动(和『相机_震动』完全同一份实现,属性名一致)
    _install_camera_shake(ctx, node, indent)

    # ② 屏幕闪白
    color = getattr(node, "gk_color", (1.0, 1.0, 1.0, 1.0))
    alpha = float(getattr(node, "gk_alpha", 0.5) or 0.5)
    duration = gd_number(getattr(node, "gk_flash_time", 0.3))
    rect_var = ctx.new_temp("scare_flash")
    tween_var = ctx.new_temp("scare_tween")
    ctx.add_line(indent, f"var {rect_var} := ColorRect.new()")
    ctx.add_line(indent, f"{rect_var}.color = Color({gd_number(color[0])}, "
                         f"{gd_number(color[1])}, {gd_number(color[2])}, "
                         f"{gd_number(alpha)})")
    ctx.add_line(indent, f"{rect_var}.mouse_filter = Control.MOUSE_FILTER_IGNORE")
    ctx.add_line(indent, f"{rect_var}.set_anchors_preset(Control.PRESET_FULL_RECT)")
    ctx.add_line(indent, f"{rect_var}.z_index = 100")
    ctx.add_line(indent, f"add_child({rect_var})")
    ctx.add_line(indent, f"var {tween_var} := create_tween()")
    ctx.add_line(indent, f"{tween_var}.tween_property({rect_var}, \"color:a\", 0.0, {duration})")
    ctx.add_line(indent, f"{tween_var}.tween_callback({rect_var}.queue_free)")

    # ③ 音效(可留空 —— 只震 + 闪也成立)
    socket = ctx._socket(node, "音效")
    linked = socket is not None and getattr(socket, "is_linked", False)
    path_raw = str(getattr(node, "gk_path", "") or "").strip()
    if not linked and not path_raw:
        return
    path = ctx.expr_of(socket) if linked else gd_string(path_raw)
    volume = _tunable_number(ctx, node, "音量",
                             gd_number(getattr(node, "gk_volume", 0.0)), "scare_volume")
    stream = ctx.new_temp("scare_stream")
    player = ctx.new_temp("scare_sfx")
    # 挂在**脚本所在节点**上(self)—— 惊吓音是"给你听的",不做 3D 定位,
    # 用普通 AudioStreamPlayer 就够,也不受『相机』那个引用干扰。
    ctx.add_line(indent, f"var {stream}: AudioStream = load({path}) as AudioStream")
    ctx.add_line(indent, f"if {stream} != null:")
    ctx.add_line(indent + 1, f"var {player} := AudioStreamPlayer.new()")
    ctx.add_line(indent + 1, f"{player}.stream = {stream}")
    ctx.add_line(indent + 1, f"{player}.volume_db = {volume}")
    ctx.add_line(indent + 1, f"{player}.max_polyphony = 4")
    ctx.add_line(indent + 1, f"self.add_child({player})")
    ctx.add_line(indent + 1, f"{player}.play()")
    ctx.add_line(indent + 1, f"{player}.finished.connect({player}.queue_free)")
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1, f'push_warning("找不到音频文件: " + str({path}))')


# ==========================================================================
# 存档
# ==========================================================================
# 为什么用"内存字典 + 显式保存"而不是每次写入都落盘:
#   落盘是磁盘 IO，在 _process 里每帧调用会卡帧。分开之后
#   "存档_写入"是廉价的，"存档_保存"才是真正写文件的那一次。
_SAVE_DATA_VAR = "_gk_save_data"

# ── 防改档(第九十三轮)───────────────────────────────────────────
# 『存档_保存』勾了『防改档』之后,落盘的是一层"信封":
#     {"gk_check": "<sha256>", "gk_data": "<原来的 JSON 文本>"}
# 这里存的是生成脚本里那个常量名 + 它的值。
#
# ⚠ 盐是**公开常量**,不承担保密职责 —— 它挡的是"玩家顺手打开记事本
# 把 100 改成 9999"这一类改档:改完数值还得知道格式、自己重算一遍
# sha256,才写得回去。**不是防破解**:客户端存档就在玩家机器上,
# 这是它的本质限制(节点手册 / README 里也这么写明)。
_SAVE_SALT_CONST = "_GK_SAVE_SALT"
_SAVE_SALT = "blendergamekit-save-v1"


def _save_dict(ctx):
    """声明(不初始化)存档字典成员变量。"""
    return ctx.declare_var(_SAVE_DATA_VAR, "Dictionary")


def _ensure_save_block(ctx, data, path, slot, indent=0):
    """保证存档字典在本代码块内已从磁盘载入一次(**在本条语句之前**调)。

    ── 为什么不靠"谁先谁后"标记 ────────────────────────────────────
    最初用"显式载入时打个标记，后面的节点就别插守卫"的办法。
    但它对顺序敏感:守卫只能插在**已经吐出的行**里，所以
    "写 -> 载入 -> 写"这种顺序下第二个写仍然会插一段多余的读盘。

    改成直接扫描本函数已经生成的代码:只要里面已经有 `_gk_read_save(`，
    就说明这个函数已经读过盘了，不需要再插守卫。
    这跟顺序无关，也不会漏。

    ── 第五十九轮修:缩进跟着**当前层**走 ──────────────────────────
    老实现把守卫行**硬插进当前 section 的行列表**、缩进写死 4 个空格 ——
    只在"语句正好在函数体第一层"时才对。一旦『存档_写入』出现在
    `match` / `if` 里面(恐怖示例:"选了结局再存档"),插进去的那几行
    比周围**浅**,Godot 直接
        Parse Error: Expected statement, found "Indent" instead.
    整个脚本编不过(而且只报这一句,很难联想到存档节点)。
    现在改成"就地按 indent 吐",深到哪一层都对。
    """
    ctx.need_save_helpers = True
    lines = getattr(getattr(ctx, "sink", None), "lines", None)
    if isinstance(lines, list) and any("_gk_read_save(" in text for text in lines):
        return

    slot_expr = gd_string(slot)
    ctx.add_line(indent, f"{path} = _gk_save_file({slot_expr})")
    ctx.add_line(indent, f'if {path} == "":')
    ctx.add_line(indent + 1, f"{path} = _gk_save_file({slot_expr})")
    ctx.add_line(indent + 1, f"{data} = _gk_read_save({path})")


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
    _ensure_save_block(ctx, data, path, slot, indent)
    ctx.add_line(indent, f"{data}[{key}] = {value}")


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
    _ensure_save_block(ctx, data, path, slot, indent)
    ctx.add_line(indent, f"{var_name} = {data}.get({key}, {default})")
    ctx.read_vars.add(var_name)


@stmt_node("GKNodeSaveWrite")
def _stmt_save_write(ctx, node, indent):
    """『存档_保存』:把内存里的字典写进磁盘。

    ── 防改档(第九十三轮)────────────────────────────────────────
    卡片上勾『防改档』-> 这次写盘带校验和(信封格式,见 `_SAVE_SALT_CONST`
    那段的说明)。开关**逐节点**生效:同一个图里"这个槽防、那个槽不防"
    是允许的 —— 读侧自描述,两种格式都能读回。没勾的节点生成物一字不变。
    """
    data = _save_dict(ctx)
    path = ctx.declare_var("_gk_save_path", "String")
    slot = getattr(node, "gk_slot", "save1")
    verify = bool(getattr(node, "gk_save_verify", False))
    _ensure_save_block(ctx, data, path, slot, indent)
    if verify:
        # 只有真的勾了才置位:没勾的老图照旧生成"不带校验"的那一套辅助函数
        ctx.need_save_verify = True
    ctx.add_line(indent, f"{path} = _gk_save_file({gd_string(slot)})")
    ctx.add_line(indent, f"_gk_write_save({path}, {data}{', true' if verify else ''})")


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

    第四十轮起还要扫**被『界面_套用设计』引用进来的设计图** —— 列表住在
    设计图里(_register_ui_design_tree 登记的),只扫玩法图的话会把挂在
    设计图列表里的控件当成"自由摆放",白写一堆 position(容器会覆盖)。
    """
    cache = getattr(ctx, "_ui_list_names", None)
    if cache is None:
        cache = set()
        trees = [getattr(ctx, "tree", None)]
        trees.extend(getattr(ctx, "_gk_ui_design_trees", ()) or ())
        for tree in trees:
            for node in (getattr(tree, "nodes", ()) or ()):
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

    ── 顺手登记互斥组(第一百零四轮)────────────────────────────────
    控件卡上填了『互斥组』的,建出来时挂进一个 Godot 组(前缀
    `gk_ui_mutex_`)—— 『界面_隐藏控件』勾『置顶』显示一块面板时,
    按这个组把同组其他成员收起来("同组只留一个开着")。
    留空 = 不挂组,老图生成物一字不变。
    """
    group = str(getattr(node, "gk_mutex_group", "") or "").strip()
    if group:
        ctx.add_line(indent,
                     f"{widget}.add_to_group({gd_string('gk_ui_mutex_' + group)})")
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


def _ui_name_label(ctx, indent, parent_var, text):
    """控件前面那个名称小标签(滑块 / 下拉共用),返回变量名。

    第一百零四轮:`音量 ●———○ -14 dB` 里最左那截。文字留空就整块不要
    (返回 None)—— "没有名称"不是空标签,是**没有**这个控件。
    """
    text = str(text or "").strip()
    if not text:
        return None
    var = ctx.new_temp("gk_name")
    ctx.add_line(indent, f"var {var} := Label.new()")
    ctx.add_line(indent, f"{var}.text = {gd_string(text)}")
    ctx.add_line(indent, f"{var}.mouse_filter = Control.MOUSE_FILTER_IGNORE")
    ctx.add_line(indent, f"{var}.vertical_alignment = VERTICAL_ALIGNMENT_CENTER")
    ctx.add_line(indent, f"{parent_var}.add_child({var})")
    return var


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
    click_sound = str(getattr(node, "gk_click_sound", "") or "").strip()
    click_db = gd_number(float(getattr(node, "gk_click_volume", -6.0) or 0.0))

    ctx.add_line(indent, f"if {widget} == null:")
    ctx.add_line(indent + 1, f"{widget} = Button.new()")
    ctx.add_line(indent + 1, f"{widget}.name = {gd_string(node.name)}")
    # 按钮必须吃鼠标事件，否则点不动
    ctx.add_line(indent + 1, f"{widget}.mouse_filter = Control.MOUSE_FILTER_STOP")
    ctx.add_line(indent + 1, f"{widget}.pressed.connect({handler})")
    # 界面音效(第八十一轮):点了响一声。
    # ⚠ 一次性播放器(不是成员变量):多按几次就是几个播放器,各播各的、
    #   播完自己删 —— 和『音效_播放』一次性音效同一套做法。
    if click_sound:
        cb = ctx.new_temp("gk_ui_cb")
        snd = ctx.new_temp("gk_ui_snd")
        plr = ctx.new_temp("gk_ui_plr")
        ctx.add_line(indent + 1, f"var {cb} := func() -> void:")
        ctx.add_line(indent + 2,
                     f"var {snd}: AudioStream = load({gd_string(_ui_image_res(click_sound))}) as AudioStream")
        ctx.add_line(indent + 2, f"if {snd} == null:")
        ctx.add_line(indent + 3, "return")
        ctx.add_line(indent + 2, f"var {plr} := AudioStreamPlayer.new()")
        ctx.add_line(indent + 2, f"{plr}.stream = {snd}")
        ctx.add_line(indent + 2, f"{plr}.volume_db = {click_db}")
        ctx.add_line(indent + 2, f"{root}.add_child({plr})")
        ctx.add_line(indent + 2, f"{plr}.play()")
        ctx.add_line(indent + 2, f"{plr}.finished.connect({plr}.queue_free)")
        ctx.add_line(indent + 1, f"{widget}.pressed.connect({cb})")
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
    """显示 / 隐藏某个界面控件。

    ── 『置顶』(第一百零四轮)──────────────────────────────────────
    勾了『置顶』且这次是**显示**时,多走一句 `_gk_ui_raise(控件)`:
      * 挪到最上层(谁后开谁在上,同时开两块面板时最后按的盖在上面);
      * 把『互斥组』里同组的其他成员收起来("同组只留一个开着")。
    没勾 = 老行为一字不变(只改 visible)—— 老图生成物不受影响。
    """
    widget_name = (getattr(node, "gk_widget", "") or "").strip()
    visible = "true" if getattr(node, "gk_visible", False) else "false"
    show = bool(getattr(node, "gk_visible", False))
    raise_it = show and bool(getattr(node, "gk_raise", False))
    if not widget_name:
        ctx.warn(f"『{node.bl_label}』没有填控件名，这一句被跳过了")
        return
    # 用 group 查而不是路径查:UI 控件是运行时挂在 CanvasLayer 下的，
    # 路径会随场景变化，按名字找更稳。
    var = ctx.new_temp("widget")
    ctx.add_line(indent, f"var {var} := _gk_find_widget({gd_string(widget_name)})")
    ctx.add_line(indent, f"if {var} != null:")
    ctx.add_line(indent + 1, f"{var}.visible = {visible}")
    if raise_it:
        ctx.need_ui_helpers = True
        ctx.need_raise_helper = True
        ctx.add_line(indent + 1, f"_gk_ui_raise({var})")
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


# ── 对话框 / 字幕 / 历史 三者共用的运行时变量(第六十四轮)────────────
# 打字音(逐字显示时每隔几个字响一下)和对话历史都挂在这一组变量上。
# 只在**用到对话框那一族**的图里声明 —— 别的图一个字都不多。
_DIALOG_RUNTIME_VARS = (
    # ── UI 根容器(第六十四轮补)───────────────────────────────────
    # `_gk_ensure_ui()` 用的是这两个,但它们原来**只有**界面控件节点
    # (`_ui_root()`)才声明 —— 于是"一张图里只有对话框 / 字幕"的脚本会
    # 生成 `_gk_ui_layer` 却没声明,Godot 直接
    #     Parse Error: Identifier "_gk_ui_layer" not declared
    # **整个脚本加载不了**(真 Godot --check-only 跑出来的;离线测试当时
    # 全绿,因为它们不看"变量声明和引用配没配上")。
    ("_gk_ui_layer", "CanvasLayer", None),
    ("_gk_ui_root", "Control", None),
    # 面板 / 说话人 / 正文 / 立绘 / 逐字 tween / "点过了吗"
    ("_gk_dialog_panel", "Panel", None),
    ("_gk_dialog_name", "Label", None),
    ("_gk_dialog_text", "Label", None),
    ("_gk_dialog_portrait", "TextureRect", None),
    ("_gk_dialog_tween", "Tween", None),
    ("_gk_dialog_clicked", "bool", "false"),
    # 打字音:当前这一句用什么音、每几个字响一次、多大声、已经响到第几次
    ("_gk_dialog_blip", "String", None),
    ("_gk_dialog_blip_every", "int", "2"),
    ("_gk_dialog_blip_db", "float", "-6.0"),
    ("_gk_dialog_blip_step", "int", "0"),
    # 打字音播放器(**复用同一个**):每个字都新建一个 AudioStreamPlayer
    # 会瞬间堆出几十个节点(实测会卡)。缓存的 path 用来判断要不要重新 load。
    ("_gk_blip_player", "AudioStreamPlayer", None),
    ("_gk_blip_cache", "String", None),
    # 对话历史:[[说话人, 正文], …],上限 200 句(见 _gk_dialog_show)
    ("_gk_dialog_history", "Array", "[]"),
    # 字幕的"第几代":新字幕/新对话框一出现就 +1,老字幕的定时器看到
    # 代数变了就不再关面板(否则老定时器会把新对话框关掉)
    ("_gk_sub_token", "int", "0"),
    # 回顾面板
    ("_gk_history_panel", "Panel", None),
    ("_gk_history_title", "Label", None),
    ("_gk_history_scroll", "ScrollContainer", None),
    ("_gk_history_list", "VBoxContainer", None),
    ("_gk_history_clicked", "bool", "false"),
    # 回顾面板的导出 / 点句重放(第八十一轮):
    # `_gk_history_replay_on` 是**运行期开关**(点按处理里判它决定"点面板里关不关"),
    # `_gk_history_export_btn` 让导出按钮只建一次(多次翻历史不会越堆越多)。
    # ⚠ 开关名**不能**叫 `_gk_history_replay` —— 那是函数名,Godot 报
    #   "Function has the same name as a previously declared variable"
    #   (真 --check-only 抓出来的)
    ("_gk_history_replay_on", "bool", "false"),
    ("_gk_history_export_btn", "Button", None),
)


def _declare_dialog_runtime(ctx):
    """登记对话框那一族的运行时变量(幂等,谁先走到谁登记)。

    ⚠ 必须在**节点遍历阶段**登记:成员变量块是在遍历**之后**才写出去的,
    辅助函数阶段再 declare 就晚了(和 `_gk_dialog_panel` 同一个坑)。
    """
    for name, gd_type, initial in _DIALOG_RUNTIME_VARS:
        if initial is None:
            ctx.declare_var(name, gd_type)
        else:
            ctx.declare_var(name, gd_type, initial=initial)


def _emit_blip_setup(ctx, node, indent):
    """把这张卡上的『打字音』写进运行时变量。

    ── 为什么走成员变量,而不是给 `_gk_dialog_show()` 加参数 ──────────
    加参数就得改那个 helper 的签名(用得越多改得越多)。成员变量这条路
    只在**语句**里多三行,helper 一行不动。

    ── 为什么"没填"也要写三行 ──────────────────────────────────────
    上一张卡设过打字音之后,这张卡不写就等于**继承了别人的音效** ——
    "这张卡上没有打字音,结果它响了"是很难查的。所以每次显式写一遍
    (没填就是空字符串 = 不响)。
    """
    sound = str(getattr(node, "gk_type_sound", "") or "").strip()
    every = int(getattr(node, "gk_type_every", 2) or 2)
    volume = float(getattr(node, "gk_type_volume", -6.0) or 0.0)
    ctx.add_line(indent, f"_gk_dialog_blip = {gd_string(sound)}")
    ctx.add_line(indent, f"_gk_dialog_blip_every = {max(1, min(20, every))}")
    ctx.add_line(indent, f"_gk_dialog_blip_db = {gd_number(volume)}")


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
    # 面板 / 说话人 / 正文 / 打字音 / 对话历史这一整组都在这里面
    # (第六十四轮统一到 _declare_dialog_runtime —— 字幕、历史也要用其中几个,
    #  分开声明会漏,漏了就是"变量没声明"的 Parse Error)。
    _declare_dialog_runtime(ctx)
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

    _emit_blip_setup(ctx, node, indent)
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


@stmt_node("GKNodeUiSubtitle")
def _stmt_ui_subtitle(ctx, node, indent):
    """『界面_字幕』:飘一行字,**不阻塞**,过几秒自己消失(第六十四轮)。

    ── 和对话框唯一的分工 ──────────────────────────────────────────
    对话框 `await` 到玩家点一下;字幕**不等** —— 生成完就往下走,
    所以"一边播运镜一边出旁白"才写得出来(运镜节点和字幕在同一个
    执行链上,字幕不挡住它)。
    """
    ctx.need_ui_helpers = True
    ctx.need_dialog_helpers = True      # 复用对话框那套面板 / 逐字 helper
    ctx.need_subtitle_helpers = True
    _declare_dialog_runtime(ctx)
    _emit_blip_setup(ctx, node, indent)

    speaker = gd_string(str(getattr(node, "gk_speaker", "") or ""))
    fallback = str(getattr(node, "gk_text", "") or "")
    text = ctx.input_string(node, "文字", fallback)
    speed = gd_number(float(getattr(node, "gk_speed", 30.0) or 0.0))
    seconds = gd_number(float(getattr(node, "gk_seconds", 3.0) or 0.0))
    fade = gd_number(float(getattr(node, "gk_fade", 0.3) or 0.0))
    width = gd_number(getattr(node, "gk_width", 720.0))
    height = gd_number(getattr(node, "gk_height", 110.0))
    margin = gd_number(float(getattr(node, "gk_margin", 48.0) or 0.0))
    # 说话人立绘(第八十一轮):**填了才多传一个实参** —— 没填的老调用点
    # 还是 9 个实参,和第六十四轮逐字一致(helper 那边是带默认值的形参)
    portrait = gd_string(_ui_image_res(getattr(node, "gk_image", "")))
    args = (f"{speaker}, str({text}), {speed}, {seconds}, {fade}, "
            f"{width}, {height}, {margin}")
    if portrait != gd_string(""):
        args += f", {portrait}"

    ctx.add_line(indent, f"_gk_subtitle_show(self, {args})")


@stmt_node("GKNodeDialogHistory")
def _stmt_dialog_history(ctx, node, indent):
    """『对话_历史』:翻出一块可滚动的回顾面板,等玩家看完再继续。

    历史本身是**自动**攒的(『界面_对话框』『界面_字幕』每显示一句就记
    一条,见 `_gk_dialog_show`),这个节点只负责"打开面板给人看"。
    """
    ctx.need_ui_helpers = True
    # 只生成历史面板那一套 —— 对话框的 helper 这个图可能根本不用
    # (历史的运行时变量另外由 _declare_dialog_runtime 补上)
    ctx.need_history_helpers = True
    _declare_dialog_runtime(ctx)
    export_path = str(getattr(node, "gk_export_path", "") or "").strip()
    replay = bool(getattr(node, "gk_replay", False))
    if export_path:
        ctx.need_history_export = True
        if not export_path.startswith("user://"):
            ctx.warn(f"『{node.bl_label}』的导出路径『{export_path}』不是 "
                     f"user:// 开头 —— 打包成游戏后 res:// 是只读的,"
                     f"建议改成 user://...")
    if replay:
        # 点句重放 = "那一句像字幕一样再飘一遍" -> 要字幕(和它背后的对话框)
        # 那一套 helper 一起生成
        ctx.need_history_replay = True
        ctx.need_subtitle_helpers = True
        ctx.need_dialog_helpers = True
    # 点按采集要在**节点遍历阶段**登记(见 _stmt_ui_dialog 的说明:
    # `_input` 的派发行比辅助函数先拼好)
    ctx.register_input_handler("gk_history_input_event", reset=False, body=[
        "    if _gk_history_panel == null or not _gk_history_panel.visible:",
        "        return",
        "    if event is InputEventMouseButton and event.pressed "
        "and event.button_index == MOUSE_BUTTON_LEFT:",
        # 开了『点句重放』时,面板**里面**的点击是"点那一句",不许关面板;
        # 点面板外面 / 按任意键才算看完(没开时老行为不变:点哪都关)
        "        if _gk_history_replay_on and "
        "_gk_history_panel.get_global_rect().has_point(event.position):",
        "            return",
        "        _gk_history_clicked = true",
        "    elif event is InputEventKey and event.pressed and not event.echo:",
        "        _gk_history_clicked = true",
    ])
    ctx.add_line(indent, f"_gk_history_replay_on = {'true' if replay else 'false'}")

    title = gd_string(str(getattr(node, "gk_title", "对话回顾") or ""))
    empty = gd_string(str(getattr(node, "gk_empty", "还没有说过话") or ""))
    font = int(getattr(node, "gk_font", 18) or 18)
    width = gd_number(getattr(node, "gk_width", 720.0))
    height = gd_number(getattr(node, "gk_height", 380.0))

    args = f"{title}, {width}, {height}, {max(8, min(48, font))}, {empty}"
    if export_path or replay:
        # 两个开关的实参**一起给**(位置参数):只给一个的话没法只加尾巴
        args += (f", {gd_string(export_path)}, "
                 f"{'true' if replay else 'false'}")
    ctx.add_line(indent, f"_gk_history_show(self, {args})")
    ctx.add_line(indent, "while not _gk_history_clicked:")
    ctx.add_line(indent + 1, "await get_tree().process_frame")
    ctx.add_line(indent, "_gk_history_hide()")


def _choice_option_texts(node):
    """『对话_选择』的选项文字(从节点上取,**同一处真相**)。

    节点类自己有 `option_texts()`(卡片上也用它显示"第几个出口是哪句话"),
    这里优先调它;老节点 / 桩对象上没有这个方法时按『选项数』补默认文字。
    """
    getter = getattr(node, "option_texts", None)
    if callable(getter):
        try:
            return [str(item) for item in getter()]
        except Exception:                                 # noqa: BLE001
            pass
    try:
        count = int(getattr(node, "gk_option_count", 2) or 2)
    except (TypeError, ValueError):
        count = 2
    count = max(1, min(6, count))
    return [f"选项 {index + 1}" for index in range(count)]


@stmt_node("GKNodeUiChoice")
def _stmt_ui_choice(ctx, node, indent):
    """『对话_选择』:弹问题 + 选项 -> 等玩家选 -> 按选中的出口往下走。

    ── 生成的语句(和对话框一样是**阻塞**的)──────────────────────
        _gk_choice_show(self, "问题", ["救她", "自己跑"], 速度, 宽, 高, 距底边)
        while _gk_choice_picked < 0:      # 等玩家点按钮 / 按数字键
            await get_tree().process_frame
        _gk_choice_hide()
        match _gk_choice_picked:
            1:
                <『选项1』那条链>
            2:
                <『选项2』那条链>

    ── 为什么是 `match` 而不是一堆 if ──────────────────────────────
    出口名就是序号(1 起),`match` 的 case 直接写那个数字 —— 生成物一眼
    能对回图上的出口,也不用为了"接在『选项 N』上"再维护一张映射表。

    ── 为什么没有『完成』出口 ────────────────────────────────────
    `emit_statement()` 会自动沿『完成』续接,有这个出口就成了
    "选择还没做,后面的逻辑先跑了"(和『界面_按钮』当年那个坑一模一样)。
    所以只有『选项 N』这几个出口,玩家选了哪条就走哪条。
    """
    ctx.need_ui_helpers = True
    ctx.need_choice_helpers = True
    # 成员变量 + 数字键采集要在**节点遍历阶段**登记(见 _stmt_ui_dialog 的说明)
    ctx.declare_var("_gk_choice_panel", "Panel")
    ctx.declare_var("_gk_choice_question", "Label")
    ctx.declare_var("_gk_choice_box", "VBoxContainer")
    ctx.declare_var("_gk_choice_picked", "int", initial="-1")
    ctx.register_input_handler("gk_choice_input_event", reset=False, body=[
        "    if _gk_choice_panel == null or not _gk_choice_panel.visible:",
        "        return",
        "    if not (event is InputEventKey) or not event.pressed or event.echo:",
        "        return",
        "    var index := 0",
        "    match event.keycode:",
        "        KEY_1:",
        "            index = 1",
        "        KEY_2:",
        "            index = 2",
        "        KEY_3:",
        "            index = 3",
        "        KEY_4:",
        "            index = 4",
        "        KEY_5:",
        "            index = 5",
        "        KEY_6:",
        "            index = 6",
        "    if index > 0 and index <= _gk_choice_box.get_child_count():",
        "        _gk_choice_pick(index)",
    ])

    fallback = str(getattr(node, "gk_question", "") or "")
    question = ctx.input_string(node, "问题", fallback)
    texts = _choice_option_texts(node)
    options = "[" + ", ".join(gd_string(text) for text in texts) + "]"
    speed = gd_number(float(getattr(node, "gk_speed", 30.0) or 0.0))
    width = gd_number(getattr(node, "gk_width", 640.0))
    height = gd_number(getattr(node, "gk_height", 200.0))
    margin = gd_number(getattr(node, "gk_margin", 48.0))

    ctx.add_line(indent, f"_gk_choice_show(self, str({question}), {options}, "
                         f"{speed}, {width}, {height}, {margin})")
    ctx.add_line(indent, "while _gk_choice_picked < 0:")
    ctx.add_line(indent + 1, "await get_tree().process_frame")
    ctx.add_line(indent, "_gk_choice_hide()")
    ctx.add_line(indent, "match _gk_choice_picked:")

    branches = [socket for socket in node.outputs
                if str(getattr(socket, "name", "")).startswith("选项")]
    if not branches:
        # 防御:出口被改没了也得出一段合法代码
        ctx.add_line(indent + 1, "_:")
        ctx.add_line(indent + 2, "pass")
        return
    for socket in branches:
        try:
            index = int(str(socket.name)[2:])
        except (TypeError, ValueError):
            continue
        ctx.add_line(indent + 1, f"{index}:")
        before = len(ctx.sink)
        _chain(ctx, node, indent + 2, socket.name)
        if len(ctx.sink) == before:
            # match 的分支体不能为空,没连线时补 pass(和『多路分支』同一条约定)
            ctx.add_line(indent + 2, "pass")


@expr_node("GKNodeUiChoice")
def _expr_ui_choice(ctx, node):
    """『选了第几个』:玩家选的是第几个选项(1 起数)。

    ⚠ 只有当选择**已经发生过**时才有意义 —— 而每个出口后面的链本来就在
    "选完之后"才跑,所以接在出口下游用是安全的(值 = 对应出口的序号)。
    """
    var = ctx.declare_var("_gk_choice_picked", "int", initial="-1")
    ctx.read_vars.add(var)
    return var


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
    ime = bool(getattr(node, "gk_ime", False))
    width = gd_number(getattr(node, "gk_width", 240.0))
    height = gd_number(getattr(node, "gk_height", 40.0))

    ctx.add_line(indent, f"if {widget} == null:")
    ctx.add_line(indent + 1, f"{widget} = LineEdit.new()")
    ctx.add_line(indent + 1, f"{widget}.name = {gd_string(node.name)}")
    ctx.add_line(indent + 1, f"{widget}.placeholder_text = {placeholder}")
    ctx.add_line(indent + 1, f"{widget}.max_length = {max_length}")
    if secret:
        ctx.add_line(indent + 1, f"{widget}.secret = true")
    # 输入法 / 软键盘(第八十一轮):手机上弹软键盘;桌面上输入法候选框
    # 跟着光标走(中文打字的体验全靠这三根线)
    if ime:
        ctx.need_ime_helper = True
        ctx.add_line(indent + 1, f"{widget}.virtual_keyboard_enabled = true")
        ctx.add_line(indent + 1, f"{widget}.focus_entered.connect(func() -> void:")
        ctx.add_line(indent + 2, "DisplayServer.window_set_ime_active(true)")
        ctx.add_line(indent + 2, f"_gk_ime_update({widget}))")
        ctx.add_line(indent + 1, f"{widget}.focus_exited.connect(func() -> void:")
        ctx.add_line(indent + 2, "DisplayServer.window_set_ime_active(false))")
        ctx.add_line(indent + 1, f"{widget}.text_changed.connect(func(_t: String) -> void:")
        ctx.add_line(indent + 2, f"_gk_ime_update({widget}))")
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


@stmt_node("GKNodeUiTextEdit")
def _stmt_ui_text_edit(ctx, node, indent):
    """多行输入框(TextEdit):**Ctrl + 回车**沿『提交』出口继续。

    ── 为什么是 Ctrl+回车,不是回车 ────────────────────────────────
    TextEdit 里回车是"换行",聊天框最常用的键 —— 抢走它就没法打多行了。
    Ctrl+回车是聊天软件的老规矩;`gui_input` 信号在控件自己的处理**之前**
    发出来,所以这里接得到(真 Godot 探针锁着这条)。

    ── 和『界面_输入框』的关系 ────────────────────────────────────
    同一套路数:运行时按需建、出口不叫『完成』(叫『完成』会被自动续接,
    刚建出来后面的逻辑就跑了),真正的续接放在 gui_input 回调里。
    """
    _layer, root = _ui_root(ctx, indent)
    widget = ctx.declare_var(f"_gk_ui_{node.name}", "TextEdit",
                             key=node_lib.node_key(node))
    handler = ctx.declare_func_name(
        f"gk_on_{snake_case(node.name, 'edit')}_submit", key=node_lib.node_key(node))
    text = ctx.input_string(node, "默认文字", "")
    placeholder = gd_string(str(getattr(node, "gk_placeholder", "") or ""))
    wrap = bool(getattr(node, "gk_wrap", True))
    readonly = bool(getattr(node, "gk_readonly", False))
    focus = bool(getattr(node, "gk_focus", False))
    width = gd_number(getattr(node, "gk_width", 360.0))
    height = gd_number(getattr(node, "gk_height", 120.0))

    ctx.add_line(indent, f"if {widget} == null:")
    ctx.add_line(indent + 1, f"{widget} = TextEdit.new()")
    ctx.add_line(indent + 1, f"{widget}.name = {gd_string(node.name)}")
    ctx.add_line(indent + 1, f"{widget}.placeholder_text = {placeholder}")
    if wrap:
        ctx.add_line(indent + 1,
                     f"{widget}.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY")
    if readonly:
        ctx.add_line(indent + 1, f"{widget}.editable = false")
    # 输入框必须吃鼠标 / 键盘,否则点不进去
    ctx.add_line(indent + 1, f"{widget}.mouse_filter = Control.MOUSE_FILTER_STOP")
    ctx.add_line(indent + 1, f"{widget}.gui_input.connect({handler})")
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
    body = list(lines.lines) or ["    pass"]
    ctx._current_event = prev_event
    ctx.sink = saved_sink
    # 两条守卫在最前(不管有没有接『提交』都在):不是键盘 / 不是回车 /
    # 没按 Ctrl 一律直接返回 —— 之后的链才算"提交了"
    ctx.extra_sections.append({
        "signature": f"func {handler}(event: InputEvent) -> void:",
        "lines": [
            "    if not (event is InputEventKey) or not event.pressed or event.echo:",
            "        return",
            "    if event.keycode != KEY_ENTER and event.keycode != KEY_KP_ENTER:",
            "        return",
            "    if not event.ctrl_pressed:",
            "        return",
        ] + body,
        "empty_body": "    pass",
        "separator": True,
    })


# ==========================================================================
# 设置面板三件套(第一百零四轮):滑块 / 下拉 / 复选框
# ==========================================================================
# 三个都是"运行时自己建、玩家动它、沿出口继续"—— 和『界面_按钮』同一套路数:
#   * 两个出口:**『完成』**(建好了,接着建下一个控件)+ **信号出口**
#     (『变了』/『选了』/『改了』)—— 信号出口的链放独立回调里;
#   * 真正的续接放在信号回调里,回调单独成一节(见 _ui_signal_section);
#   * 滑块 / 下拉是一整行 HBox(名称 + 控件 + …),复选框是单控件;
#     两者都按**节点名**建控件 —— 『界面_设置』『界面_隐藏控件』找得到。
#
# ⚠ 三个都有个共同纪律:初始值(滑块的 value / 下拉的 selected / 复选框的
#   button_pressed)**在接信号之前**设好 —— 接完再设会当场触发信号,
#   "刚建出来就把出口后面那条链跑一遍"(『界面_按钮』那个老坑的变体)。
@stmt_node("GKNodeUiSlider")
def _stmt_ui_slider(ctx, node, indent):
    """『界面_滑块』:一行 = 名称 + 滑轨 + **右边自动跟当前数值**。

    ── 数值那串字谁在刷 ──
    `value_changed` 接一个 lambda:显示数值时先把 `名称 + 当前值 + 后缀`
    写进右边那个 Label,再走 `{handler}(_v)`(『变了』出口那条链)。
    一次拖动刷一次,不用用户手动更新。
    值的格式化走 `_gk_num_text`(『文本_数字』同一个函数,整数不带 .0)。
    """
    _layer, root = _ui_root(ctx, indent)
    widget = ctx.declare_var(f"_gk_ui_{node.name}", "HBoxContainer",
                             key=node_lib.node_key(node))
    handler = ctx.declare_func_name(
        f"gk_on_{snake_case(node.name, 'slider')}_changed",
        key=node_lib.node_key(node))
    label = str(getattr(node, "gk_label", "") or "")
    minimum = gd_number(float(getattr(node, "gk_min", 0.0) or 0.0))
    maximum = gd_number(float(getattr(node, "gk_max", 100.0) or 0.0))
    step = gd_number(float(getattr(node, "gk_step", 1.0) or 0.0))
    value = gd_number(float(getattr(node, "gk_value", 50.0) or 0.0))
    suffix = gd_string(str(getattr(node, "gk_suffix", "") or ""))
    show_value = bool(getattr(node, "gk_show_value", True))
    width = gd_number(getattr(node, "gk_width", 320.0))
    height = gd_number(getattr(node, "gk_height", 36.0))
    from .expressions import ensure_num_text_helper
    num_text = ensure_num_text_helper(ctx)

    ctx.add_line(indent, f"if {widget} == null:")
    ctx.add_line(indent + 1, f"{widget} = HBoxContainer.new()")
    ctx.add_line(indent + 1, f"{widget}.name = {gd_string(node.name)}")
    # 整行不吃鼠标:事件要落到滑轨上(行的空白处点不到东西)
    ctx.add_line(indent + 1, f"{widget}.mouse_filter = Control.MOUSE_FILTER_IGNORE")
    _ui_name_label(ctx, indent + 1, widget, label)
    bar = ctx.new_temp("gk_slider")
    ctx.add_line(indent + 1, f"var {bar} := HSlider.new()")
    ctx.add_line(indent + 1, f"{bar}.min_value = {minimum}")
    ctx.add_line(indent + 1, f"{bar}.max_value = {maximum}")
    ctx.add_line(indent + 1, f"{bar}.step = {step}")
    ctx.add_line(indent + 1, f"{bar}.value = {value}")
    ctx.add_line(indent + 1, f"{bar}.size_flags_horizontal = Control.SIZE_EXPAND_FILL")
    ctx.add_line(indent + 1, f"{bar}.mouse_filter = Control.MOUSE_FILTER_STOP")
    ctx.add_line(indent + 1, f"{widget}.add_child({bar})")
    shown = None
    if show_value:
        shown = ctx.new_temp("gk_value")
        ctx.add_line(indent + 1, f"var {shown} := Label.new()")
        ctx.add_line(indent + 1, f"{shown}.text = {num_text}({value}) + {suffix}")
        # 数值宽度固定一块:数字长短变化时滑轨不会跟着抖
        ctx.add_line(indent + 1, f"{shown}.custom_minimum_size = Vector2(64.0, 0.0)")
        ctx.add_line(indent + 1, f"{shown}.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT")
        ctx.add_line(indent + 1, f"{shown}.vertical_alignment = VERTICAL_ALIGNMENT_CENTER")
        ctx.add_line(indent + 1, f"{shown}.mouse_filter = Control.MOUSE_FILTER_IGNORE")
        ctx.add_line(indent + 1, f"{widget}.add_child({shown})")
    _ui_add_to(ctx, indent + 1, widget, node, root)
    ctx.add_line(indent + 1, f"{bar}.value_changed.connect(func(_v: float) -> void:")
    if shown:
        ctx.add_line(indent + 2, f"{shown}.text = {num_text}(_v) + {suffix}")
    ctx.add_line(indent + 2, f"{handler}(_v))")
    _ui_size(ctx, indent, widget, node, width, height)
    _ui_place(ctx, indent, widget, node, "20.0", "20.0")
    _ui_signal_section(ctx, node, handler, output="变了",
                       signature=f"func {handler}(_value: float) -> void:")


@expr_node("GKNodeUiSlider")
def _expr_ui_slider(ctx, node):
    """『值』:滑块现在停在哪个数(拖到哪读到哪)。

    按**自己节点的名字**找控件(控件就是用节点名建的);读的时候现取,
    所以拖动后立刻能读到新值(不缓存)。
    """
    ctx.need_ui_helpers = True
    ctx.need_widget_value_helper = True
    return f"_gk_widget_value({gd_string(str(getattr(node, 'name', '')))})"


@stmt_node("GKNodeUiDropdown")
def _stmt_ui_dropdown(ctx, node, indent):
    """『界面_下拉』:原生 OptionButton 或 ◀ 当前项 ▶ 两种样式。

    ── 当前选的是第几个记在哪 ──
    记在**控件自己的 meta `gk_index`** 上(**0 起存**):两种样式
    (原生的 item_selected / 箭头的 pressed)都往它写,『选了第几个』
    输出按名字读它(见 `_gk_widget_index`,给用户时 +1)。
    不读 `OptionButton.selected` 是因为左右切换样式里根本没有
    OptionButton —— 两种样式一条读法,用户不用管自己选的是哪种。
    """
    _layer, root = _ui_root(ctx, indent)
    widget = ctx.declare_var(f"_gk_ui_{node.name}", "HBoxContainer",
                             key=node_lib.node_key(node))
    handler = ctx.declare_func_name(
        f"gk_on_{snake_case(node.name, 'dropdown')}_selected",
        key=node_lib.node_key(node))
    items = list(node_lib.dropdown_item_texts(node))
    items_literal = "[" + ", ".join(gd_string(text) for text in items) + "]"
    style = str(getattr(node, "gk_style", "NATIVE") or "NATIVE").upper()
    label = str(getattr(node, "gk_label", "") or "")
    if not str(getattr(node, "gk_items", "") or "").strip():
        ctx.warn(f"『{node.bl_label}』没有填『选项』——先按一个『选项』占位生成")
    try:
        picked = int(getattr(node, "gk_index", 1) or 1) - 1      # 属性是 1 起数
    except (TypeError, ValueError):
        picked = 0
    picked = max(0, min(len(items) - 1, picked))
    width = gd_number(getattr(node, "gk_width", 240.0))
    height = gd_number(getattr(node, "gk_height", 36.0))
    items_var = ctx.new_temp("gk_items")

    ctx.add_line(indent, f"if {widget} == null:")
    ctx.add_line(indent + 1, f"{widget} = HBoxContainer.new()")
    ctx.add_line(indent + 1, f"{widget}.name = {gd_string(node.name)}")
    ctx.add_line(indent + 1, f"{widget}.mouse_filter = Control.MOUSE_FILTER_IGNORE")
    ctx.add_line(indent + 1, f"var {items_var} := {items_literal}")
    ctx.add_line(indent + 1, f'{widget}.set_meta("gk_index", {picked})')
    _ui_name_label(ctx, indent + 1, widget, label)
    if style == "ARROWS":
        # ── 左右切换:◀ [当前项] ▶(到头按回另一头)──────────────────
        prev = ctx.new_temp("gk_prev")
        middle = ctx.new_temp("gk_pick")
        following = ctx.new_temp("gk_next")
        ctx.add_line(indent + 1, f"var {prev} := Button.new()")
        ctx.add_line(indent + 1, f'{prev}.text = "◀"')
        ctx.add_line(indent + 1, f"{prev}.custom_minimum_size = Vector2(40.0, 0.0)")
        ctx.add_line(indent + 1, f"{prev}.mouse_filter = Control.MOUSE_FILTER_STOP")
        ctx.add_line(indent + 1, f"{widget}.add_child({prev})")
        ctx.add_line(indent + 1, f"var {middle} := Label.new()")
        ctx.add_line(indent + 1, f"{middle}.text = str({items_var}[{picked}])")
        ctx.add_line(indent + 1, f"{middle}.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER")
        ctx.add_line(indent + 1, f"{middle}.vertical_alignment = VERTICAL_ALIGNMENT_CENTER")
        ctx.add_line(indent + 1, f"{middle}.size_flags_horizontal = Control.SIZE_EXPAND_FILL")
        ctx.add_line(indent + 1, f"{middle}.mouse_filter = Control.MOUSE_FILTER_IGNORE")
        ctx.add_line(indent + 1, f"{widget}.add_child({middle})")
        ctx.add_line(indent + 1, f"var {following} := Button.new()")
        ctx.add_line(indent + 1, f'{following}.text = "▶"')
        ctx.add_line(indent + 1, f"{following}.custom_minimum_size = Vector2(40.0, 0.0)")
        ctx.add_line(indent + 1, f"{following}.mouse_filter = Control.MOUSE_FILTER_STOP")
        ctx.add_line(indent + 1, f"{widget}.add_child({following})")
        ctx.add_line(indent + 1, f"{prev}.pressed.connect(func() -> void:")
        ctx.add_line(indent + 2,
                     f"var _next := (int({widget}.get_meta(\"gk_index\")) - 1 + "
                     f"{items_var}.size()) % {items_var}.size()")
        ctx.add_line(indent + 2, f'{widget}.set_meta("gk_index", _next)')
        ctx.add_line(indent + 2, f"{middle}.text = str({items_var}[_next])")
        ctx.add_line(indent + 2, f"{handler}(_next))")
        ctx.add_line(indent + 1, f"{following}.pressed.connect(func() -> void:")
        ctx.add_line(indent + 2,
                     f"var _next := (int({widget}.get_meta(\"gk_index\")) + 1) % "
                     f"{items_var}.size()")
        ctx.add_line(indent + 2, f'{widget}.set_meta("gk_index", _next)')
        ctx.add_line(indent + 2, f"{middle}.text = str({items_var}[_next])")
        ctx.add_line(indent + 2, f"{handler}(_next))")
    else:
        # ── 原生下拉:OptionButton(点开一个小列表)──────────────────
        picker = ctx.new_temp("gk_option")
        ctx.add_line(indent + 1, f"var {picker} := OptionButton.new()")
        ctx.add_line(indent + 1, f"for _item in {items_var}:")
        ctx.add_line(indent + 2, f"{picker}.add_item(str(_item))")
        ctx.add_line(indent + 1, f"{picker}.selected = {picked}")
        ctx.add_line(indent + 1, f"{picker}.size_flags_horizontal = Control.SIZE_EXPAND_FILL")
        ctx.add_line(indent + 1, f"{picker}.mouse_filter = Control.MOUSE_FILTER_STOP")
        ctx.add_line(indent + 1, f"{widget}.add_child({picker})")
        ctx.add_line(indent + 1, f"{picker}.item_selected.connect(func(_i: int) -> void:")
        ctx.add_line(indent + 2, f'{widget}.set_meta("gk_index", _i)')
        ctx.add_line(indent + 2, f"{handler}(_i))")
    _ui_add_to(ctx, indent + 1, widget, node, root)
    _ui_size(ctx, indent, widget, node, width, height)
    _ui_place(ctx, indent, widget, node, "20.0", "20.0")
    _ui_signal_section(ctx, node, handler, output="选了",
                       signature=f"func {handler}(_index: int) -> void:")


@expr_node("GKNodeUiDropdown")
def _expr_ui_dropdown(ctx, node):
    """『选了第几个』:下拉框现在选中的是第几个(**1 起数**)。

    和『对话_选择』的『选了第几个』同一个规矩(第 1 项 = 1),
    两张图里的编号不用来回换算。
    """
    ctx.need_ui_helpers = True
    ctx.need_widget_index_helper = True
    return f"_gk_widget_index({gd_string(str(getattr(node, 'name', '')))})"


@stmt_node("GKNodeUiCheckbox")
def _stmt_ui_checkbox(ctx, node, indent):
    """『界面_复选框』:CheckBox(勾选框)或 CheckButton(开关)。

    勾上 / 取消都走 `toggled` -> `{handler}(_on)`(『改了』出口那条链);
    『勾了吗』输出按名字读控件的 `button_pressed`(见 `_gk_widget_checked`)。
    """
    _layer, root = _ui_root(ctx, indent)
    style = str(getattr(node, "gk_style", "BOX") or "BOX").upper()
    kind = "CheckButton" if style == "SWITCH" else "CheckBox"
    widget = ctx.declare_var(f"_gk_ui_{node.name}", kind,
                             key=node_lib.node_key(node))
    handler = ctx.declare_func_name(
        f"gk_on_{snake_case(node.name, 'toggle')}_toggled",
        key=node_lib.node_key(node))
    text = gd_string(str(getattr(node, "gk_text", "") or ""))
    checked = "true" if getattr(node, "gk_checked", False) else "false"
    width = float(getattr(node, "gk_width", 180.0) or 0.0)
    height = gd_number(getattr(node, "gk_height", 36.0))

    ctx.add_line(indent, f"if {widget} == null:")
    ctx.add_line(indent + 1, f"{widget} = {kind}.new()")
    ctx.add_line(indent + 1, f"{widget}.name = {gd_string(node.name)}")
    ctx.add_line(indent + 1, f"{widget}.text = {text}")
    ctx.add_line(indent + 1, f"{widget}.button_pressed = {checked}")
    # 复选框要能吃鼠标,否则点不动
    ctx.add_line(indent + 1, f"{widget}.mouse_filter = Control.MOUSE_FILTER_STOP")
    ctx.add_line(indent + 1, f"{widget}.toggled.connect({handler})")
    _ui_add_to(ctx, indent + 1, widget, node, root)
    if width > 0.0:
        _ui_size(ctx, indent, widget, node, gd_number(width), height)
    _ui_place(ctx, indent, widget, node, "20.0", "20.0")
    _ui_signal_section(ctx, node, handler, output="改了",
                       signature=f"func {handler}(_on: bool) -> void:")


@expr_node("GKNodeUiCheckbox")
def _expr_ui_checkbox(ctx, node):
    """『勾了吗』:这个复选框现在是勾着的吗(布尔)。"""
    ctx.need_ui_helpers = True
    ctx.need_widget_checked_helper = True
    return f"_gk_widget_checked({gd_string(str(getattr(node, 'name', '')))})"


@stmt_node("GKNodeUiSkin")
def _stmt_ui_skin(ctx, node, indent):
    """『界面_皮肤』:建一套 Theme 挂到 UI 根上(第八十一轮)。

    ── 为什么做成一个**语句节点**而不是全局设置 ────────────────────
    皮肤是"这张图长什么样"的一部分,和别的界面节点住在同一张图里最好找;
    挂到 `_gk_ui_root.theme` 之后,后面建的控件(包括『界面_套用设计』
    展开出来的)**自动**继承 —— Godot 的主题是往下传的。

    ── 覆盖哪些 ──────────────────────────────────────────────────
      * Button 三态(normal / hover / pressed)+ **focus**(选中高亮);
      * VScrollBar / HScrollBar 的 scroll(轨道)+ grabber 三态;
      * Panel 的 panel、LineEdit / TextEdit 的 normal / focus;
      * ProgressBar 的 background / fill;
      * 常用类型的 font_color + `default_font_size`。
    卡片自己设过 override 的控件(『界面_文本』的颜色、『界面_面板』的
    底色)以卡片为准 —— 主题只兜底。
    """
    _layer, root = _ui_root(ctx, indent)
    font_size = int(getattr(node, "gk_font_size", 20) or 20)
    font_color = getattr(node, "gk_font_color", (0.95, 0.97, 1.0, 1.0))
    panel_color = getattr(node, "gk_panel_color", (0.06, 0.07, 0.10, 0.92))
    button_color = getattr(node, "gk_button_color", (0.16, 0.20, 0.30, 0.95))
    hover_color = getattr(node, "gk_button_hover", (0.22, 0.28, 0.42, 1.0))
    press_color = getattr(node, "gk_button_press", (0.10, 0.13, 0.20, 1.0))
    focus_color = getattr(node, "gk_focus_color", (1.0, 0.86, 0.45, 0.85))
    scroll_color = getattr(node, "gk_scroll_color", (1.0, 1.0, 1.0, 0.30))
    radius = int(getattr(node, "gk_radius", 8) or 0)

    def color(entry):
        return (f"Color({gd_number(entry[0])}, {gd_number(entry[1])}, "
                f"{gd_number(entry[2])}, {gd_number(entry[3])})")

    def stylebox(box_var, bg, corners=True, border=False):
        """一个圆角方块的生成(底色 / 圆角 / 可选描边)。

        ⚠ 属性行和 `var` 行**同一级**:GDScript 里没有开新块,
          缩进再深一级就是语法错误(真 Godot 直接 Parse Error)。
        """
        ctx.add_line(indent, f"var {box_var} := StyleBoxFlat.new()")
        ctx.add_line(indent, f"{box_var}.bg_color = {bg}")
        if corners:
            for corner in ("top_left", "top_right",
                           "bottom_left", "bottom_right"):
                ctx.add_line(indent,
                             f"{box_var}.corner_radius_{corner} = {radius}")
        if border:
            for edge in ("top", "bottom", "left", "right"):
                ctx.add_line(indent, f"{box_var}.border_width_{edge} = 2")
            ctx.add_line(indent, f"{box_var}.border_color = {bg}")

    theme = ctx.new_temp("gk_ui_theme")
    ctx.add_line(indent, f"var {theme} := Theme.new()")
    ctx.add_line(indent, f"{theme}.default_font_size = {max(8, min(72, font_size))}")
    text_color = ctx.new_temp("gk_ui_color")
    ctx.add_line(indent, f"var {text_color} := {color(font_color)}")
    type_var = ctx.new_temp("gk_ui_t")
    ctx.add_line(indent, f"for {type_var} in [\"Label\", \"Button\", "
                         f"\"LineEdit\", \"TextEdit\"]:")
    ctx.add_line(indent + 1,
                 f"{theme}.set_color(\"font_color\", {type_var}, {text_color})")

    # 按钮:三态 + 焦点(焦点就是"列表项选中高亮"的那个高亮)
    for key, bg in (("normal", color(button_color)),
                    ("hover", color(hover_color)),
                    ("pressed", color(press_color))):
        box = ctx.new_temp("gk_ui_btn")
        stylebox(box, bg)
        ctx.add_line(indent, f"{theme}.set_stylebox({gd_string(key)}, \"Button\", {box})")
    focus_box = ctx.new_temp("gk_ui_focus")
    stylebox(focus_box, color(focus_color), border=True)
    ctx.add_line(indent, f"{theme}.set_stylebox(\"focus\", \"Button\", {focus_box})")

    # 面板 / 输入框
    panel_box = ctx.new_temp("gk_ui_panel")
    stylebox(panel_box, color(panel_color))
    ctx.add_line(indent, f"{theme}.set_stylebox(\"panel\", \"Panel\", {panel_box})")
    edit_box = ctx.new_temp("gk_ui_edit")
    stylebox(edit_box, color(panel_color))
    ctx.add_line(indent, f"{theme}.set_stylebox(\"normal\", \"LineEdit\", {edit_box})")
    ctx.add_line(indent, f"{theme}.set_stylebox(\"normal\", \"TextEdit\", {edit_box})")
    ctx.add_line(indent, f"{theme}.set_stylebox(\"focus\", \"LineEdit\", {focus_box})")
    ctx.add_line(indent, f"{theme}.set_stylebox(\"focus\", \"TextEdit\", {focus_box})")

    # 滚动条(第八十一轮『滚动条美化』):轨道按滑块色淡化,不另配颜色
    grabber = color(scroll_color)
    track_var = ctx.new_temp("gk_ui_track")
    stylebox(track_var, f"Color({gd_number(scroll_color[0])}, "
                        f"{gd_number(scroll_color[1])}, "
                        f"{gd_number(scroll_color[2])}, "
                        f"{gd_number(float(scroll_color[3]) * 0.35)})")
    grab_box = ctx.new_temp("gk_ui_grab")
    stylebox(grab_box, grabber)
    bar_var = ctx.new_temp("gk_ui_bar")
    ctx.add_line(indent,
                 f"for {bar_var} in [\"VScrollBar\", \"HScrollBar\"]:")
    for key in ("scroll", "grabber", "grabber_highlight", "grabber_pressed"):
        ctx.add_line(indent + 1,
                     f"{theme}.set_stylebox({gd_string(key)}, {bar_var}, "
                     f"{track_var if key == 'scroll' else grab_box})")

    # 进度条
    fill_box = ctx.new_temp("gk_ui_fill")
    stylebox(fill_box, color(button_color))
    bag_box = ctx.new_temp("gk_ui_barbg")
    stylebox(bag_box, "Color(0.0, 0.0, 0.0, 0.35)")
    ctx.add_line(indent, f"{theme}.set_stylebox(\"background\", \"ProgressBar\", {bag_box})")
    ctx.add_line(indent, f"{theme}.set_stylebox(\"fill\", \"ProgressBar\", {fill_box})")

    ctx.add_line(indent, f"{root}.theme = {theme}")


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


# ==========================================================================
# 『界面_套用设计』/『界面_按钮点击』—— 界面设计图那条线(第四十轮)
# ==========================================================================
# ── 导出时"设计图怎么变成代码"(用户选的"套用式")───────────────────
# 玩法图的『界面_套用设计』节点被走到时,生成器**现场把设计图展开**:
# 把里面每个"控件构建"节点按**父控件依赖**排好序,逐个调用它们各自的
# 发射器 —— 生成的东西和它们直接待在玩法图里时**一字不差**。
#
# 这样做的三个好处:
#   1. 设计图里的节点不需要自己"被执行" —— 引用它的那一个节点就是入口,
#      和『动画_状态机』引用动画树完全对称;
#   2. 生成的代码没有第二套写法:控件还是 `_gk_ui_<节点名>`,
#      所以玩法图的『界面_隐藏控件』『界面_设置』按名字照样找得到;
#   3. 老图不受影响 —— 控件节点留在玩法图里生成的结果不变。
def _ui_builder_idnames():
    """所有"控件构建"节点的 bl_idname(名单在 nodes/_display.py)。"""
    cache = getattr(_ui_builder_idnames, "_cache", None)
    if cache is None:
        cache = frozenset(cls.bl_idname for cls in node_lib.ALL_CLASSES
                          if getattr(cls, "gk_ui_builder", False))
        _ui_builder_idnames._cache = cache
    return cache


def _ui_design_nodes(tree):
    """设计图里的控件构建节点(保持节点树里的先后顺序)。"""
    builders = _ui_builder_idnames()
    return [n for n in (getattr(tree, "nodes", ()) or ())
            if getattr(n, "bl_idname", "") in builders]


def _ui_design_order(nodes):
    """排成"父控件一定排在子控件前面"(深度优先 + 环保护)。

    ── 为什么必须排序 ──────────────────────────────────────────────
    生成的是**顺序执行**的代码:`_gk_find_widget(父名)` 在父控件还没
    建出来时找不到 -> 控件被退回挂在界面根下(有告警,但用户看到的
    是"明明填了父控件却没进去")。
    """
    by_name = {}
    for node in nodes:
        by_name.setdefault(str(getattr(node, "name", "")), node)
    ordered = []
    done = set()
    visiting = set()

    def visit(node):
        key = node_lib.node_key(node)
        if key in done or key in visiting:
            return
        visiting.add(key)
        parent = by_name.get(str(getattr(node, "gk_parent_widget", "") or "").strip())
        if parent is not None:
            visit(parent)
        visiting.discard(key)
        done.add(key)
        ordered.append(node)

    for node in nodes:
        visit(node)
    return ordered


def _register_ui_design_tree(ctx, tree):
    """登记"这次导出用到了这张设计图"(『界面_列表』的父控件判断要看它)。"""
    trees = getattr(ctx, "_gk_ui_design_trees", None)
    if trees is None:
        trees = []
        ctx._gk_ui_design_trees = trees
    if all(existing is not tree for existing in trees):
        trees.append(tree)


@stmt_node("GKNodeUiApplyDesign")
def _stmt_ui_apply_design(ctx, node, indent):
    """『界面_套用设计』:把设计图里摆好的控件一次性建出来。"""
    raw = str(getattr(node, "gk_design", "") or "").strip()
    tree = node_lib.find_ui_design_tree(raw)
    if tree is None:
        ctx.warn(f"『{node.bl_label}』找不到界面设计图"
                 f"(填的是『{raw or '自动(第一张)'}』)—— 在侧栏"
                 f"『游戏界面』面板里点『打开/新建界面设计图』建一张")
        return
    widgets = _ui_design_nodes(tree)
    if not widgets:
        ctx.warn(f"设计图『{tree.name}』里没有控件(文本 / 按钮 / 面板 / 列表…),"
                 f"这一句什么都不会建 —— 切到『游戏界面』那张图摆几个控件")
        return
    _register_ui_design_tree(ctx, tree)
    # 外层执行栈要**保住**:『界面_按钮』的发射器生成点击回调时会
    # `_active.clear()`(那边是故意"换一个执行上下文"),不还原的话
    # 外层链路的回路保护就没了。
    saved_active = set(ctx._active)
    for widget in _ui_design_order(widgets):
        handler = STMT_HANDLERS.get(getattr(widget, "bl_idname", ""))
        if handler is None:                               # pragma: no cover
            continue
        handler(ctx, widget, indent)
    ctx._active.clear()
    ctx._active.update(saved_active)


def _ui_signal_section(ctx, node, handler, output="点击", signature=None):
    """把一个信号出口后面的链收进独立回调函数(照『界面_按钮』的做法)。

    第一百零四轮起不只给『点击』用:滑块『变了』/ 下拉『选了』/ 复选框
    『改了』都走这里 —— 各自的回调**带信号参数**(value / index / toggled),
    `signature` 不传就按老样子生成无参签名。

    ⚠ `new_section()` 换了写入目标而且**不会自己换回来**,所以这里必须
    手动存一份再还回去(见『界面_按钮』那条踩坑记录);执行栈也一样,
    回调链按"新上下文"生成,生成完把外层的还原回去。
    """
    saved_sink = ctx.sink
    saved_active = set(ctx._active)
    lines = ctx.new_section()
    ctx._active.clear()
    prev_event = ctx._current_event
    ctx._current_event = node.name
    _chain(ctx, node, 1, output)
    if len(lines) == 0:
        lines.add(1, "pass")
    ctx._current_event = prev_event
    ctx._active.clear()
    ctx._active.update(saved_active)
    ctx.sink = saved_sink
    ctx.extra_sections.append({
        "signature": signature or f"func {handler}() -> void:",
        "lines": list(lines.lines),
        "empty_body": "    pass",
        "separator": True,
    })


@stmt_node("GKNodeUiOnClick")
def _stmt_ui_on_click(ctx, node, indent):
    """『界面_按钮点击』:按名字给(设计图里的)按钮接上行为。"""
    widget_name = (getattr(node, "gk_widget", "") or "").strip()
    if not widget_name:
        ctx.warn(f"『{node.bl_label}』没有填控件名,这一句被跳过了")
        return
    ctx.need_ui_helpers = True
    handler = ctx.declare_func_name(
        f"gk_on_{snake_case(widget_name, 'button')}", key=node_lib.node_key(node))
    var = ctx.new_temp("button")
    ctx.add_line(indent, f"var {var} := _gk_find_widget({gd_string(widget_name)})")
    ctx.add_line(indent, f"if {var} != null and {var} is BaseButton:")
    # 幂等:放每帧的链上也只接一次(重复 connect 会让点一下跑两遍)
    ctx.add_line(indent + 1, f"if not {var}.pressed.is_connected({handler}):")
    ctx.add_line(indent + 2, f"{var}.pressed.connect({handler})")
    ctx.add_line(indent, "else:")
    ctx.add_line(indent + 1,
                 f'push_warning("找不到按钮『{widget_name}』:先确认设计图里摆了'
                 f'它,并且之前已经『界面_套用设计』")')
    _ui_signal_section(ctx, node, handler)


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
# 全屏滤镜着色器:读当前画面再染色(受击变红 / 中毒变绿)。
#
# ── ⚠ 读屏必须用 `hint_screen_texture` 的 uniform(第五十一轮真机抓到)──
# 这里原来写的是 Godot 3 时代的内置变量 `SCREEN_TEXTURE`,而 **Godot 4.7
# 已经把它删掉了**。实测(scripts/verify_shaders.py 真编译):
#     SHADER ERROR: SCREEN_TEXTURE 已被移除，请改用 hint_screen_texture 和 uniform.
#     ERROR: Shader compilation failed.
# 也就是说**以前导出的『屏幕_滤镜』根本编不过** —— 只是没人发现:无头
# 探针用的是 dummy 渲染器,着色器编译错误不进探针的断言。
# 现在按官方写法声明一个 uniform:
#     uniform sampler2D screen_tex : hint_screen_texture;
#     texture(screen_tex, SCREEN_UV)
# 并把"内嵌着色器必须真的编得过"接进了门禁(见 scripts/verify_shaders.py)。
_GD_FILTER_SHADER = """shader_type canvas_item;
uniform sampler2D screen_tex : hint_screen_texture, repeat_disable, filter_nearest;
uniform float amount : hint_range(0.0, 1.0) = 0.0;
uniform vec4 tint : source_color = vec4(1.0, 1.0, 1.0, 1.0);
void fragment() {
    vec4 screen = texture(screen_tex, SCREEN_UV);
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


# ── 屏幕暗角(第五十一轮:阶段④屏幕氛围)──────────────────────────────
# Godot 的 `Environment` **没有内置 vignette**,所以暗角只能自己写全屏着色器。
#
# ── 为什么和『屏幕_滤镜』分成两层,而不是塞进同一个着色器 ──────────────
# 塞进去要改 `_GK_FILTER_SHADER` 的内容 —— 那会让**已经导出过**的老工程
# 在下次导出时着色器源码整体变一个字(产物不再逐字一致),而且两者本来
# 就是"可以叠加"的两件事(受击整屏变红 + 四角一直压暗)。各占一层最省事:
# 滤镜在 CanvasLayer 90、暗角在 92,谁也不需要知道对方存在。
# ⚠ 着色器里的注释**只能用 ASCII**(见 scripts/verify_shaders.py 的说明):
#   Godot 的着色器词法分析器碰到非 ASCII 字符直接报
#   `Tokenizer: Unknown character #22522`,整个着色器编不出来。
#   所以下面这行注释是英文的,中文说明留在 Python 这一层。
_GD_VIGNETTE_SHADER = """shader_type canvas_item;
uniform sampler2D screen_tex : hint_screen_texture, repeat_disable, filter_nearest;
uniform float vignette_amount : hint_range(0.0, 1.0) = 0.0;
uniform float vignette_radius : hint_range(0.0, 1.0) = 0.55;
uniform float vignette_edge : hint_range(0.01, 1.5) = 0.35;
uniform vec4 vignette_color : source_color = vec4(0.0, 0.0, 0.0, 1.0);
void fragment() {
    vec4 screen = texture(screen_tex, SCREEN_UV);
    // distance to screen center: 0 = center, 1 = corner (normalized by 0.5*sqrt(2))
    vec2 offset = SCREEN_UV - vec2(0.5, 0.5);
    float dist = length(offset) / 0.70710678;
    float v = smoothstep(vignette_radius, vignette_radius + vignette_edge, dist);
    COLOR = vec4(mix(screen.rgb, vignette_color.rgb, v * vignette_amount), screen.a);
}
"""
_VIGNETTE_SHADER_CONST = "_GK_VIGNETTE_SHADER"


def _screen_vignette_mat(ctx):
    """取(必要时创建)全屏暗角的材质。返回 (材质变量, 根变量, 矩形变量)。"""
    layer = ctx.declare_var("_gk_vignette_layer", "CanvasLayer")
    rect = ctx.declare_var("_gk_vignette_rect", "ColorRect")
    mat = ctx.declare_var("_gk_vignette_mat", "ShaderMaterial")
    ctx.need_screen_vignette = True
    return mat, layer, rect


@stmt_node("GKNodeScreenVignette")
def _stmt_screen_vignette(ctx, node, indent):
    """屏幕暗角:全屏一层着色器,只在四角按颜色压暗。

    生成的三句(和『屏幕_滤镜』同一套写法):
        _gk_ensure_vignette(self)                       # 第一次用到时建全屏遮罩
        _gk_vignette_mat.set_shader_parameter("vignette_radius", 0.55)
        _gk_vignette_mat.set_shader_parameter("vignette_edge", 0.35)
        _gk_vignette_mat.set_shader_parameter("vignette_color", Color(0, 0, 0, 1))
        _gk_vignette_mat.set_shader_parameter("vignette_amount", 0.7)   # 或渐变过去

    ⚠ **内圈 / 柔和 / 颜色每次都写**(它们不是"状态"而是"这一层的形状"),
    只有『强度』可能被渐变 —— 和滤镜只写 tint + amount 是同一个取舍。
    """
    mat, _layer, _rect = _screen_vignette_mat(ctx)
    amount = _tunable_number(ctx, node, "强度", "0.0", "vignette_amount")
    radius = float(getattr(node, "gk_radius", 0.55) or 0.0)
    edge = float(getattr(node, "gk_edge", 0.35) or 0.0)
    color = getattr(node, "gk_color", (0.0, 0.0, 0.0, 1.0))
    fade = float(getattr(node, "gk_fade", 0.0) or 0.0)

    ctx.add_line(indent, "_gk_ensure_vignette(self)")
    ctx.add_line(indent, f"{mat}.set_shader_parameter(\"vignette_radius\", "
                         f"{gd_number(radius)})")
    ctx.add_line(indent, f"{mat}.set_shader_parameter(\"vignette_edge\", "
                         f"{gd_number(edge)})")
    ctx.add_line(indent, f"{mat}.set_shader_parameter(\"vignette_color\", "
                         f"Color({gd_number(color[0])}, {gd_number(color[1])}, "
                         f"{gd_number(color[2])}, {gd_number(color[3])}))")
    if fade > 0.0:
        tween_var = ctx.new_temp("vignette")
        ctx.add_line(indent, f"var {tween_var} := create_tween()")
        ctx.add_line(indent, f"{tween_var}.tween_method(func(v): "
                             f"{mat}.set_shader_parameter(\"vignette_amount\", v), "
                             f"float({mat}.get_shader_parameter(\"vignette_amount\")), "
                             f"{amount}, {gd_number(fade)})")
    else:
        ctx.add_line(indent, f"{mat}.set_shader_parameter(\"vignette_amount\", "
                             f"{amount})")
