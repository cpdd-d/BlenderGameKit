"""游戏资产节点(『游戏资产』树) —— 第四十三轮。

用户原话:"添加游戏资产管理编辑器,便于重复利用与和常规资产隔开";
动工前在三个形态里点了**新节点树类型**(和『游戏显示』同一套做法),
并选了:收四类(3D 道具 / 图片 / 曲线 / 音频)、**跨场景共用**、
资产实例自动收进名为『资产』的集合。

── 这张图是什么 ─────────────────────────────────────────────────────
一张**资产库**:每份资产一张卡,卡上写清楚

    [资产_模型卡]   3D 道具(静态资产):本场景几份 / 本体在哪个场景 /
                    [定位] [再摆一份](跨场景:自动把本体复制进这一关)
    [资产_图片卡]   界面图 / 立绘:谁在用(哪个图里哪个节点)
    [资产_曲线卡]   运镜轨道 / 时间轴曲线:谁在用 + 定位到曲线物体
    [资产_声音卡]   本地音频文件 -> 导出时复制进项目的 audio/ 目录

── 和场景里的"常规物体"怎么隔开 ─────────────────────────────────────
* 资产库是 **.blend 级**的(跨场景):收录时扫所有场景,关卡 1 的树
  在关卡 2 也能一键摆出来(摆的时候自动补本体,导出语义一个字不变);
* 登记 / 摆一份 / 收录时,资产实例自动进同名集合『资产』——
  大纲视图里一眼分得清"哪些是可复用的资产、哪些是普通的场景物体"。

── 卡上的数据存在哪 ─────────────────────────────────────────────────
卡只存**名字**(资产名 / 图片名 / 曲线名 / 音频文件路径),真正的
资产数据还在原来该在的地方(物体属性 / bpy.data.images / 曲线物体 /
本地文件)—— 不搬数据,只做索引,所以老工程一个字不用改。
"""

from ._common import *  # noqa: F401,F403

# 这张图能放的四张卡。名单在这里是因为有三个地方要用它:
# 侧栏『收录』按它补齐、测试按它断言、诊断信息按它列清单。
ASSET_CARD_IDS = (
    "GKNodeAssetModel",
    "GKNodeAssetImage",
    "GKNodeAssetCurve",
    "GKNodeAssetSound",
)

# 新建 / 补齐时卡片的竖向间距
ASSET_CARD_GAP = 260.0

# ── "谁在用"要看的属性名(判据:这些属性存的就是资产的名字)───────────
# 图片:『界面_图片』『界面_对话框』立绘 —— 存的是 Blender 图片名。
IMAGE_PROPS = ("gk_image",)
# 曲线:相机_切换 的过渡/轨道、相机_路径、时间轴 —— 存的是曲线物体名。
CURVE_PROPS = ("gk_curve", "gk_blend_curve", "gk_path_curve")
# 音频:音效_播放 / 音效_音乐 / 音效_停止音乐 —— 存的是 res:// 路径。
SOUND_PROPS = ("gk_path",)

_USAGE_PROPS = {"image": IMAGE_PROPS, "curve": CURVE_PROPS,
                "sound": SOUND_PROPS}


def missing_asset_cards(tree):
    """这张图里还缺哪几张资产卡(按 ASSET_CARD_IDS 的顺序)。"""
    have = {getattr(node, "bl_idname", "")
            for node in getattr(tree, "nodes", ()) or ()}
    return [blob for blob in ASSET_CARD_IDS if blob not in have]


def asset_users(trees, kind, value):
    """谁在用这份资产。返回 [(树名, 节点名, 节点标签)]。

    kind 三选一:image / curve / sound。
    **纯函数**(只 getattr 读值)—— 离线测试直接喂桩树,不用开 Blender。
    """
    props = _USAGE_PROPS.get(kind) or ()
    wanted = str(value or "").strip()
    out = []
    if not wanted:
        return out
    for tree in trees or ():
        for node in getattr(tree, "nodes", ()) or ():
            for prop in props:
                if str(getattr(node, prop, "") or "").strip() == wanted:
                    out.append((str(getattr(tree, "name", "?")),
                                str(getattr(node, "name", "?")),
                                str(getattr(node, "bl_label", "?"))))
                    break
    return out


def collect_used_assets(trees):
    """扫所有节点图,把**在用的**图片 / 曲线 / 音频路径收全(一键收录用)。

    返回 {"images": [...], "curves": [...], "sounds": [...]} ——
    按出现顺序、去重。声音这里收的是节点里写死的 res:// 路径
    (那类"没有本地文件"的卡只做登记,导出不会复制)。
    """
    found = {"images": [], "curves": [], "sounds": []}
    for kind, key in (("image", "images"), ("curve", "curves"),
                      ("sound", "sounds")):
        for prop in _USAGE_PROPS[kind]:
            for tree in trees or ():
                for node in getattr(tree, "nodes", ()) or ():
                    value = str(getattr(node, prop, "") or "").strip()
                    if value and value not in found[key]:
                        found[key].append(value)
    return found


def asset_scene_usage(name):
    """这个资产在**所有场景**里的分布(跨场景共用的依据)。

    返回 {场景名: {"total": 份数, "source": 本体物体名 or ""}}。
    需要 bpy —— 只在编辑器和导出时调。
    """
    import bpy
    from .. import asset_export
    usage = {}
    wanted = str(name or "").strip()
    if not wanted:
        return usage
    for scene in getattr(bpy.data, "scenes", ()) or ():
        try:
            entry = asset_export.collect_assets(scene).get(wanted)
        except Exception:                                 # noqa: BLE001
            continue
        if not entry:
            continue
        source = entry.get("source")
        usage[str(getattr(scene, "name", "?"))] = {
            "total": len(entry.get("members") or []),
            "source": str(getattr(source, "name", "")),
        }
    return usage


def all_asset_names():
    """全 .blend 登记过的资产名(跨场景、去重、排序)。"""
    import bpy
    names = set()
    for scene in getattr(bpy.data, "scenes", ()) or ():
        for obj in getattr(scene, "objects", ()) or ():
            physics = getattr(obj, "gk_physics", None)
            if physics is None or not getattr(physics, "enabled", True):
                continue
            name = str(getattr(physics, "asset_name", "") or "").strip()
            if name:
                names.add(name)
    return sorted(names)


def asset_tree():
    """当前 .blend 里的『游戏资产』图(没有就返回 None)。"""
    import bpy
    for group in getattr(getattr(bpy, "data", None), "node_groups", ()) or ():
        if getattr(group, "bl_idname", "") == "GKAssetTree":
            return group
    return None


def sound_card_entries(tree=None):
    """资产图里所有声音卡:**[(显示名, 本地文件, res:// 路径)]**。

    `tree` 不给就自己去 bpy.data 里找 —— 导出流程(exporter)用它
    决定要复制哪些音频;卡片和侧栏也用它。
    """
    if tree is None:
        try:
            tree = asset_tree()
        except Exception:                                 # noqa: BLE001
            tree = None
    out = []
    for node in getattr(tree, "nodes", ()) or ():
        if getattr(node, "bl_idname", "") != "GKNodeAssetSound":
            continue
        name = str(getattr(node, "gk_name", "") or "").strip()
        path = str(getattr(node, "gk_file", "") or "").strip()
        res = str(getattr(node, "gk_res", "") or "").strip()
        out.append((name, path, res))
    return out


@N
class GKNodeAssetModel(B.GKStatementNode):
    bl_idname = "GKNodeAssetModel"
    bl_label = "资产_模型卡"
    bl_icon = "MESH_DATA"
    gk_category = "资产"
    gk_tree_type = "GKAssetTree"
    gk_inputs = ()
    gk_outputs = ()
    gk_prop_defs = (
        ("gk_asset", "string", "", "资产名",
         "静态资产的名字(物体『游戏物理』→『静态资产』里填的那个)"),
    )
    gk_doc = ("**一份 3D 道具资产**(树 / 石头 / 箱子这类可复用的模型)。\n"
              "『资产名』对应物体上的同名栏位:同名物体算一份资产,"
              "没勾『作为实例』的那份是**本体**(定义模型)。\n"
              "── 跨场景 ──\n"
              "资产库是整个 .blend 共用的:关卡 1 的树在关卡 2 点『再摆一份』"
              "就能摆出来 —— 这一关还没有本体时,摆的第一份会自动当本体"
              "(否则导出的场景里实例没有模型)。\n"
              "『定位』会切到本体所在的场景并选中它。")

    def draw_buttons(self, context, layout):
        name = str(getattr(self, "gk_asset", "") or "").strip()
        if not name:
            layout.label(text="还没填资产名 —— 侧栏点『收录』或『登记选中』",
                         icon="ERROR")
            return
        layout.prop(self, "gk_asset", text="", icon="ASSET_MANAGER")

        usage = asset_scene_usage(name)
        here = str(getattr(getattr(context, "scene", None), "name", ""))
        local = usage.get(here)
        if local:
            layout.label(text=f"本场景 {local['total']} 份"
                              + ("(有本体)" if local["source"] else ""),
                         icon="CHECKMARK")
        else:
            layout.label(text="本场景还没有摆", icon="INFO")

        # 本体在哪个场景(跨场景共用时最关键的一行)
        sources = [(scene, info["source"]) for scene, info in usage.items()
                   if info["source"]]
        if sources:
            scene_name, obj_name = sources[0]
            text = (f"本体:{obj_name}(本场景)" if scene_name == here
                    else f"本体在『{scene_name}』:{obj_name}")
            layout.label(text=text, icon="MESH_DATA")
        else:
            layout.label(text="还没有本体(找个场景摆第一份)", icon="ERROR")

        if usage and not (local or {}).get("source"):
            layout.label(text="这一关缺本体:点『再摆一份』会自动补上",
                         icon="INFO")

        row = layout.row(align=True)
        focus = row.operator("gk.asset_focus", text="定位", icon="VIEWZOOM")
        focus.asset_name = name
        place = row.operator("gk.asset_place", text="再摆一份", icon="DUPLICATE")
        place.asset_name = name


@N
class GKNodeAssetImage(B.GKStatementNode):
    bl_idname = "GKNodeAssetImage"
    bl_label = "资产_图片卡"
    bl_icon = "IMAGE_DATA"
    gk_category = "资产"
    gk_tree_type = "GKAssetTree"
    gk_inputs = ()
    gk_outputs = ()
    gk_prop_defs = (
        ("gk_image", "string", "", "图片",
         "Blender 里的图片名(『界面_图片』/『对话框』立绘用的那张)"),
    )
    gk_doc = ("**一张图片资产**(界面图 / 立绘 / 图标)。\n"
              "导出时这张图会落盘成 `art/ui_xxx.png` 带进 Godot —— "
              "源图在 Blender 里,不用自己拷文件。\n"
              "卡上写的是**谁在用**(哪个图里哪个节点引用了它);"
              "想换图就点『选图片』。")

    def draw_buttons(self, context, layout):
        name = str(getattr(self, "gk_image", "") or "").strip()
        row = layout.row(align=True)
        row.prop(self, "gk_image", text="", icon="IMAGE_DATA")
        picker = row.operator("gk.pick_image", text="", icon="VIEWZOOM")
        picker.node_name = self.name
        picker.image_prop = "gk_image"
        if not name:
            layout.label(text="还没选图片", icon="INFO")
            return
        image = None
        try:
            import bpy
            image = bpy.data.images.get(name)
        except Exception:                                 # noqa: BLE001
            image = None
        if image is None:
            layout.label(text="工程里找不到这张图片了(检查是否被删)",
                         icon="ERROR")
        else:
            size = getattr(image, "size", None)
            if size and int(size[0]):
                layout.label(text=f"{int(size[0])}×{int(size[1])}",
                             icon="CHECKMARK")
            else:
                layout.label(text="没有像素数据(图片可能丢了)", icon="ERROR")
        _draw_asset_users(layout, "image", name)


@N
class GKNodeAssetCurve(B.GKStatementNode):
    bl_idname = "GKNodeAssetCurve"
    bl_label = "资产_曲线卡"
    bl_icon = "CURVE_DATA"
    gk_category = "资产"
    gk_tree_type = "GKAssetTree"
    gk_inputs = ()
    gk_outputs = ()
    gk_prop_defs = (
        ("gk_curve", "string", "", "曲线",
         "场景里那条曲线物体的名字(运镜轨道 / 时间轴曲线)"),
    )
    gk_doc = ("**一条曲线资产**(相机运镜轨道 / 时间轴曲线)。\n"
              "导出时采样成 `curves/xxx.tres` 带进 Godot。\n"
              "卡上写的是**谁在用**(哪个节点的哪个栏位选了它);"
              "曲线一多很容易忘了哪条在用、哪条能删,这里一眼看清。")

    def draw_buttons(self, context, layout):
        name = str(getattr(self, "gk_curve", "") or "").strip()
        row = layout.row(align=True)
        row.prop(self, "gk_curve", text="", icon="CURVE_DATA")
        focus = row.operator("gk.focus_object", text="", icon="VIEWZOOM")
        focus.object_name = name
        if not name:
            layout.label(text="还没选曲线", icon="INFO")
            return
        try:
            import bpy
            found = bpy.data.objects.get(name) is not None
        except Exception:                                 # noqa: BLE001
            found = False
        if not found:
            layout.label(text="工程里找不到这条曲线了(检查是否被删)",
                         icon="ERROR")
        _draw_asset_users(layout, "curve", name)


@N
class GKNodeAssetSound(B.GKStatementNode):
    bl_idname = "GKNodeAssetSound"
    bl_label = "资产_声音卡"
    bl_icon = "SOUND"
    gk_category = "资产"
    gk_tree_type = "GKAssetTree"
    gk_inputs = ()
    gk_outputs = ()
    gk_prop_defs = (
        ("gk_name", "string", "", "名字",
         "资产库里显示的名字(留空 = 用文件名)"),
        ("gk_file", "string", "", "本地文件",
         "挑一个本地音频(wav / ogg / mp3);导出时复制进项目的 audio/"),
        ("gk_res", "string", "", "游戏里的路径",
         "挑文件后自动写好;音效节点用『从资产库挑』引用它"),
    )
    gk_doc = ("**一个音频资产**(音效 / BGM)。\n"
              "点『浏览』挑一个本地文件(wav / ogg / mp3),导出时它会被"
              "复制进 Godot 项目的 `audio/` 目录 —— 不用自己拷文件、"
              "也不会因为路径写错而在运行时找不到。\n"
              "音效节点(『音效_播放』『音效_音乐』)卡上点『从资产库挑』"
              "即可引用这份资产。\n"
              "只有路径、没有本地文件的卡(比如从老图收进来的 "
              "`res://art/xxx.wav`)只做登记,导出不会复制。")

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_name", text="", icon="SOUND")
        browse = row.operator("gk.asset_pick_sound", text="", icon="FILEBROWSER")
        browse.node_name = self.name
        layout.prop(self, "gk_file", text="", icon="FILE_SOUND")
        res = str(getattr(self, "gk_res", "") or "").strip()
        if res:
            layout.label(text=res, icon="LINKED")
        path = str(getattr(self, "gk_file", "") or "").strip()
        if path:
            import os
            if os.path.isfile(path):
                layout.label(text="导出会复制进 audio/(推荐)", icon="CHECKMARK")
            else:
                layout.label(text=f"本地文件找不到了:{path}", icon="ERROR")
        elif res:
            layout.label(text="只登记路径:导出不复制(自己放进项目)", icon="INFO")
        if res:
            _draw_asset_users(layout, "sound", res)


def _draw_asset_users(layout, kind, value):
    """卡上那一段"谁在用"。

    最多列 3 条(卡不宽,列满会糊),每条带一个『跳过去』按钮 ——
    点一下切到那张图并选中那个节点(比"你知道在哪吗"强)。
    """
    import bpy
    users = asset_users(getattr(getattr(bpy, "data", None), "node_groups", ())
                        or (), kind, value)
    if not users:
        layout.label(text="还没有地方用它", icon="INFO")
        return
    layout.label(text=f"{len(users)} 处在用:", icon="LINENUMBERS_ON")
    for tree_name, node_name, label in users[:3]:
        row = layout.row(align=True)
        row.label(text=f"{label} · {tree_name}", icon="NODETREE")
        pick = row.operator("gk.asset_focus_node", text="", icon="VIEWZOOM")
        pick.tree_name = tree_name
        pick.node_name = node_name
    if len(users) > 3:
        layout.label(text=f"…还有 {len(users) - 3} 处", icon="DOT")