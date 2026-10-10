"""游戏音乐设置节点(『游戏音乐』树) —— 第八十六轮(E 组 14)。

用户从六个"编辑器"里点的最后一个:『游戏音乐设置』(音量 / 循环 / 场景规则)。
形态跟着『游戏显示』的先例(第四十二轮用户选了**新节点树类型**,比属性
标签页顺手)—— 注册 `bpy.types.NodeTree` 就自动出现在编辑器类型菜单里。

── 这张图是什么 ─────────────────────────────────────────────────────
它不是逻辑图,是**音乐控制台** —— 两张卡各管一类:

    [音乐_场景 BGM]   这一关放哪首 / 多大声 / 循环吗 / 走哪条总线(场景规则)
    [音乐_总线音量]   开局把一条音频总线(默认 Music)设到多少 dB

卡上直接读写**当前场景**的 GKSceneSettings(和显示设置卡同一套"写透"):
卡上改 = 导出立刻按新值走,不存在"忘了点应用";多关卡时每关一套,
切场景卡上的值跟着切。

── 产物落在哪 ───────────────────────────────────────────────────────
全部落进**环境脚本**(每场景一份 —— 和开局 BGM 同一处,第四十八 / 七十七轮):
循环开关改 `_gk_start_bgm` 的 loop 设置,总线名写进 `player.bus`,
总线音量在 `_ready` 里 `AudioServer.set_bus_volume_db`。
默认值(循环开 / 总线空 / 不应用)时运行时行为与以前**完全一致**。
"""

from ._common import *  # noqa: F401,F403

# 这张图的"设置卡"名单(和 DISPLAY_CARD_IDS 同一个用法:侧栏『补齐』
# 按它找缺哪张;测试按它断言"卡片一张不少")。
MUSIC_CARD_IDS = (
    "GKNodeMusicSceneBgm",
    "GKNodeMusicBus",
)

# 新建 / 补齐时卡片的竖向间距(卡上有四五行动态内容,排太紧会叠住)
MUSIC_CARD_GAP = 260.0


def missing_music_cards(tree):
    """这张图里还缺哪几张设置卡(按 MUSIC_CARD_IDS 的顺序返回 idname)。

    纯读 `tree.nodes` 的 bl_idname —— 离线桩树也能喂进来测。
    """
    have = {getattr(node, "bl_idname", "")
            for node in getattr(tree, "nodes", ()) or ()}
    return [blob for blob in MUSIC_CARD_IDS if blob not in have]


def music_settings_rows(settings):
    """侧栏摘要:每项一行 (标题, 状态文本, 开着没)。

    **纯函数**(只 getattr 读值,不碰 bpy),离线测试直接喂假对象。
    卡上和侧栏显示的是同一批值 —— 只有一处真相。
    """
    if settings is None:
        return []
    rows = []

    raw_bgm = str(getattr(settings, "scene_bgm", "") or "").strip()
    if not raw_bgm:
        rows.append(("开局 BGM", "留空(不播)", False))
    else:
        try:
            volume = float(getattr(settings, "scene_bgm_volume", -6.0))
        except (TypeError, ValueError):
            volume = -6.0
        loop = bool(getattr(settings, "scene_bgm_loop", True))
        bus = str(getattr(settings, "scene_bgm_bus", "") or "").strip()
        marks = [f"{volume:g} dB", "循环" if loop else "播一遍"]
        if bus:
            marks.append(f"总线 {bus}")
        rows.append(("开局 BGM", " · ".join(marks), True))

    apply_bus = bool(getattr(settings, "music_bus_apply", False))
    bus = str(getattr(settings, "music_bus", "") or "").strip()
    try:
        bus_db = float(getattr(settings, "music_bus_db", -6.0))
    except (TypeError, ValueError):
        bus_db = -6.0
    if apply_bus:
        text = f"开局把 {bus or '?'} 压到 {bus_db:g} dB"
    else:
        text = "关(不碰总线音量)"
    rows.append(("总线音量", text, apply_bus))
    return rows


def _scene_settings(context):
    """卡上读写的那份场景设置(GKSceneSettings);拿不到返回 None。"""
    scene = getattr(context, "scene", None)
    return getattr(scene, "gk_settings", None) if scene is not None else None


@N
class GKNodeMusicSceneBgm(B.GKStatementNode):
    bl_idname = "GKNodeMusicSceneBgm"
    bl_label = "音乐_场景 BGM"
    bl_icon = "SOUND"
    gk_category = "音乐设置"
    gk_tree_type = "GKMusicTree"
    # 设置卡不是逻辑:没有引脚,只在自己的树里摆着(和显示设置卡一个道理)
    gk_inputs = ()
    gk_outputs = ()
    gk_doc = ("**这一关的音乐**(场景规则)。\n"
              "『开局 BGM』挑一首本地的 wav / ogg / mp3:场景一开始自动播。"
              "音频导出时会**复制进项目的 audio/**(和『资产_声音卡』同一条"
              "管线),所以不会出现 Godot 里找不到文件。\n"
              "『循环』:默认开(背景音乐一直有);关掉 = 播一遍就停。\n"
              "『总线』:留空 = Master;想单独压背景音乐就先在 Godot 的"
              "『音频』面板建一条 Music 总线,把名字填这里。\n"
              "留空整张卡 = 不播(想自己控制就接『音效_音乐』节点)。")

    def draw_buttons(self, context, layout):
        settings = _scene_settings(context)
        if settings is None:
            layout.label(text="拿不到场景设置(先打开一个场景)", icon="ERROR")
            return
        layout.prop(settings, "scene_bgm", text="", icon="SOUND")
        row = layout.row(align=True)
        row.prop(settings, "scene_bgm_volume", text="dB")
        row.prop(settings, "scene_bgm_loop", text="循环", toggle=True,
                 icon="FILE_REFRESH")
        layout.prop(settings, "scene_bgm_bus", text="总线")

        raw = str(getattr(settings, "scene_bgm", "") or "").strip()
        if not raw:
            layout.label(text="留空 = 开局不放音乐", icon="INFO")
        elif not raw.lower().endswith((".wav", ".ogg", ".mp3")):
            # 和侧栏那句同一个立场:挑到非音频就别装作能播
            layout.label(text="选的不是音频(wav / ogg / mp3)", icon="ERROR")
        else:
            loop = bool(getattr(settings, "scene_bgm_loop", True))
            layout.label(text="开局自动播" + ("(循环)" if loop else "(播一遍)"),
                         icon="CHECKMARK")
        layout.label(text="导出时音频会复制进项目的 audio/", icon="DOT")


@N
class GKNodeMusicBus(B.GKStatementNode):
    bl_idname = "GKNodeMusicBus"
    bl_label = "音乐_总线音量"
    bl_icon = "PLAY_SOUND"
    gk_category = "音乐设置"
    gk_tree_type = "GKMusicTree"
    gk_inputs = ()
    gk_outputs = ()
    gk_doc = ("**开局把一条音频总线设到指定音量**(全局音量)。\n"
              "『总线』要填 Godot 的『音频』面板里**已有**的名字(默认 Music"
              " —— 没建过的话运行时只会给一条警告,不会崩)。\n"
              "『开局应用』不勾 = 什么都不做(老工程导出产物一字不变);"
              "勾上才写进环境脚本,场景一开始就把这条总线设成下面那个 dB。\n"
              "── 和『音效_总线音量』节点的分工 ──\n"
              "这张卡管**开局默认值**(比如\"这游戏音乐整体 -6 dB\");"
              "运行中要变(进战斗压低、菜单里让玩家自己调)用"
              "『音效_总线音量』节点。")

    def draw_buttons(self, context, layout):
        settings = _scene_settings(context)
        if settings is None:
            layout.label(text="拿不到场景设置(先打开一个场景)", icon="ERROR")
            return
        row = layout.row(align=True)
        row.prop(settings, "music_bus", text="总线")
        row.prop(settings, "music_bus_db", text="dB")
        layout.prop(settings, "music_bus_apply", toggle=True, icon="CHECKBOX_HLT")
        bus = str(getattr(settings, "music_bus", "") or "").strip()
        if not bool(getattr(settings, "music_bus_apply", False)):
            layout.label(text="没勾『开局应用』= 不动总线音量", icon="INFO")
        elif not bus:
            layout.label(text="总线名是空的 —— 打开 Godot 音频面板建一条再填",
                         icon="ERROR")
        else:
            layout.label(text=f"开局:{bus} -> "
                              f"{float(getattr(settings, 'music_bus_db', -6.0)):g} dB",
                         icon="CHECKMARK")
        layout.label(text="总线得在 Godot 里建过(默认只有 Master)", icon="DOT")