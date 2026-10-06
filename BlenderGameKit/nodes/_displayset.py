"""游戏显示设置节点(『游戏显示』树) —— 第四十二轮。

用户原话:"添加游戏显示设置编辑器,整合如物理显示等的排列,不然不好找";
动工前在四个形态里点了**新节点树类型**(和『游戏逻辑』『游戏动画』
同一个机制:注册 bpy.types.NodeTree 就会自动出现在编辑器类型菜单里)。

── 这张图是什么 ─────────────────────────────────────────────────────
它不是逻辑图,是一张**设置控制台** —— 四张卡各管一类"显示":

    [显示_碰撞线框]  物理显示:显示选中 / 全部 / 按导出算 / 隐藏
    [显示_调试面板]  游戏里按一个键弹出的 HUD + Godot 控制台的运行日志
    [显示_画面风格]  卡通材质 / 卡通跟随场景光照
    [显示_视口预览]  界面设计图 / AI 图的 3D 视口叠加预览开关

这四类原来散在三处(侧栏『导出为 Godot 脚本』盒 / 物体面板『视口预览』盒 /
各自的树面板),用户嫌"不好找" —— 现在收进一张图,分类摆好。

── 为什么是"写透"而不是"卡上存值 + 一个应用节点" ──────────────────
卡上的开关**直接读写当前场景的 GKSceneSettings**(`scene.gk_settings`),
节点自己不另存一份值,所以:

  * 卡上改 = 导出**立刻**按新值走,不存在"忘了点应用"这种坑
    (这个项目吃过好几次"开关开了没反应"的苦头,不能再埋一个);
  * 老工程一个字不用改:设置本来就存在场景上,这一轮只是换了个**摆法**;
  * 删掉卡片不会重置任何设置(只是那块控件不显示了,侧栏可一键补回)。

── 多场景须知 ──────────────────────────────────────────────────────
设置挂在**场景**上,所以卡上改的是"**当前场景**"的那一份。
多关卡(.blend 里好几个 Scene)时切场景,卡上的值跟着切 —— 这是对的:
每个关卡本来就各有一套显示设置(和『逻辑图』的绑定一个道理)。
"""

from ._common import *  # noqa: F401,F403

# 这张图能放的四个"设置卡"。名单写在这里(而不是散在类上)有两个用它的地方:
#   * 侧栏『补齐缺失的设置卡』按它找缺哪张;
#   * 测试按它断言"四张卡一张不少"。
DISPLAY_CARD_IDS = (
    "GKNodeDisplayCollision",
    "GKNodeDisplayDebug",
    "GKNodeDisplayStyle",
    "GKNodeDisplayPreview",
)

# 新建 / 补齐时卡片的竖向间距(卡上有四五行动态内容,排太紧会叠住)
DISPLAY_CARD_GAP = 260.0


def missing_display_cards(tree):
    """这张图里还缺哪几张设置卡(按 DISPLAY_CARD_IDS 的顺序返回 idname)。

    纯读 `tree.nodes` 的 bl_idname —— 离线桩树也能喂进来测。
    """
    have = {getattr(node, "bl_idname", "")
            for node in getattr(tree, "nodes", ()) or ()}
    return [blob for blob in DISPLAY_CARD_IDS if blob not in have]


def display_settings_rows(settings):
    """侧栏摘要:每个开关一行 (标题, 状态文本, 开着没)。

    **纯函数**(只 getattr 读值,不碰 bpy),离线测试直接喂一个假对象。
    卡上和侧栏显示的是同一批值 —— 只有一处真相。
    """
    if settings is None:
        return []
    rows = []

    debug = bool(getattr(settings, "debug_panel", False))
    if debug:
        key = str(getattr(settings, "debug_key", "") or "?")
        copy_key = str(getattr(settings, "debug_copy_key", "") or "?")
        text = f"开({key} 弹出 / {copy_key} 复制)"
    else:
        text = "关"
    rows.append(("调试面板", text, debug))

    runtime = bool(getattr(settings, "debug_runtime", False))
    rows.append(("运行日志", "每秒往控制台打一行" if runtime else "关", runtime))

    toon = bool(getattr(settings, "toon_materials", True))
    follow = bool(getattr(settings, "toon_follow_light", False))
    if not toon:
        text = "原样导出(PBR)"
    elif follow:
        text = "卡通材质 · 明暗跟随太阳"
    else:
        text = "卡通材质 · 全平涂"
    rows.append(("画面风格", text, toon))
    return rows


def _scene_settings(context):
    """卡上读写的那份场景设置(GKSceneSettings);拿不到返回 None。"""
    scene = getattr(context, "scene", None)
    return getattr(scene, "gk_settings", None) if scene is not None else None


@N
class GKNodeDisplayCollision(B.GKStatementNode):
    bl_idname = "GKNodeDisplayCollision"
    bl_label = "显示_碰撞线框"
    bl_icon = "SHADING_WIRE"
    gk_category = "显示设置"
    gk_tree_type = "GKDisplayTree"
    # 设置卡不是逻辑:没有引脚,只在自己的树里摆着(和设计图的控件卡一个道理)
    gk_inputs = ()
    gk_outputs = ()
    gk_doc = ("**物理显示**:把导出时会生成的碰撞体画成线框,方便对齐 / 排查。\n"
              "线框和导出用**同一套计算** —— 看到的尺寸就是生成出来的尺寸。\n"
              "『显示选中』= 只画视口里当前选中的那个模型"
              "(不选就明确报错,不会偷偷画全部);\n"
              "『显示全部』= 排查有没有漏设;"
              "『按导出算』= 这次导出会生成的全部碰撞体;\n"
              "『隐藏』= 清掉所有线框。\n"
              "线框只在 Blender 里看:**不导出、不影响游戏**。")

    def draw_buttons(self, context, layout):
        active = (getattr(context, "active_object", None)
                  or getattr(context, "object", None))
        name = str(getattr(active, "name", "") or "")
        row = layout.row(align=True)
        row.label(text=f"选中: {name}" if name else "没选中物体",
                  icon="RESTRICT_SELECT_OFF")
        row = layout.row(align=True)
        row.operator("gk.show_collision", text="显示选中",
                     icon="SHADING_WIRE")
        row.operator("gk.hide_collision", text="隐藏", icon="X")
        row = layout.row(align=True)
        row.operator("gk.show_collision", text="显示全部(排查)"
                     ).target_all = True
        row.operator("gk.preview_colliders", text="按导出算", icon="EXPORT")
        layout.label(text="线框只在 Blender 里看:不导出、不影响游戏",
                     icon="INFO")


@N
class GKNodeDisplayDebug(B.GKStatementNode):
    bl_idname = "GKNodeDisplayDebug"
    bl_label = "显示_调试面板"
    bl_icon = "INFO"
    gk_category = "显示设置"
    gk_tree_type = "GKDisplayTree"
    gk_inputs = ()
    gk_outputs = ()
    gk_doc = ("**游戏内显示**:玩的时候能看的调试 HUD + 控制台运行日志。\n"
              "『调试面板』:游戏里按一个键弹出/收起,显示 FPS / 相机 / "
              "脚本宿主的位置速度 / 变量实时值 / 最近日志;"
              "另一个键把全部内容复制到剪贴板(粘出来就能排查)。\n"
              "『运行日志』:每秒往 Godot 控制台打一行状态 —— "
              "和『导出诊断信息』配成一对(那个管编辑期,这个管运行期)。\n"
              "调试面板由**环境脚本**生成,目前只支持 3D 项目。")

    def draw_buttons(self, context, layout):
        settings = _scene_settings(context)
        if settings is None:
            layout.label(text="拿不到场景设置(先打开一个场景)", icon="ERROR")
            return
        row = layout.row(align=True)
        row.prop(settings, "debug_panel", toggle=True, icon="INFO")
        if bool(getattr(settings, "debug_panel", False)):
            row.prop(settings, "debug_key", text="")
            row.prop(settings, "debug_copy_key", text="")
            layout.label(text="游戏里:一个键弹出/收起,另一个键复制全部",
                         icon="INFO")
            if str(getattr(settings, "dimension", "3")) == "2":
                layout.label(text="⚠ 2D 项目暂时没有调试面板(只支持 3D)",
                             icon="ERROR")
        row = layout.row(align=True)
        row.prop(settings, "debug_runtime", toggle=True, icon="TEXT")
        layout.label(text="运行日志 = 每秒往 Godot 控制台打一行状态",
                     icon="INFO")


@N
class GKNodeDisplayStyle(B.GKStatementNode):
    bl_idname = "GKNodeDisplayStyle"
    bl_label = "显示_画面风格"
    bl_icon = "MATERIAL"
    gk_category = "显示设置"
    gk_tree_type = "GKDisplayTree"
    gk_inputs = ()
    gk_outputs = ()
    gk_doc = ("**导出画面风格**。\n"
              "『卡通材质』:导出时把材质转成卡通风格(描边 + 色阶)—— "
              "关掉 = 按 Blender 原样导出 PBR,想核对细节时用。\n"
              "『卡通跟随场景光照』:卡通 shader 是否跟着场景里的平行光"
              "画明暗 —— 关掉是**全平涂**(推荐,真正的二次元画法);"
              "打开才会『转太阳、明暗跟着转』。")

    def draw_buttons(self, context, layout):
        settings = _scene_settings(context)
        if settings is None:
            layout.label(text="拿不到场景设置(先打开一个场景)", icon="ERROR")
            return
        row = layout.row(align=True)
        row.prop(settings, "toon_materials", toggle=True, icon="MATERIAL")
        if bool(getattr(settings, "toon_materials", True)):
            row.prop(settings, "toon_follow_light", toggle=True, icon="LIGHT_SUN")
            if bool(getattr(settings, "toon_follow_light", False)):
                layout.label(text="明暗跟着场景太阳转", icon="INFO")
            else:
                layout.label(text="全平涂(推荐)", icon="CHECKMARK")
        else:
            layout.label(text="按 Blender 原样导出 PBR(核对细节用)", icon="INFO")


@N
class GKNodeDisplayPreview(B.GKStatementNode):
    bl_idname = "GKNodeDisplayPreview"
    bl_label = "显示_视口预览"
    bl_icon = "VIEWZOOM"
    gk_category = "显示设置"
    gk_tree_type = "GKDisplayTree"
    gk_inputs = ()
    gk_outputs = ()
    gk_doc = ("**视口叠加预览的汇总开关**。\n"
              "『游戏界面』设计图和『游戏AI』图在编辑时会把内容叠画在 3D 视口上"
              "(控件框 / 巡逻路线 + 感知圈)—— 这里一张图一行,"
              "勾选 = 开预览,点名字 = 跳过去编辑。\n"
              "嫌画面乱就在这里临时关掉,不用去改图本身。")

    def draw_buttons(self, context, layout):
        import bpy
        trees = [group for group in bpy.data.node_groups
                 if getattr(group, "bl_idname", "")
                 in ("GKUiDesignTree", "GKGameAiTree")]
        if not trees:
            layout.label(text="还没有界面设计图 / AI 图", icon="INFO")
            return
        layout.label(text="编辑这些图时叠画在 3D 视口上:", icon="INFO")
        for tree in trees[:6]:
            row = layout.row(align=True)
            row.prop(tree, "gk_preview", text="")
            kind = ("界面" if getattr(tree, "bl_idname", "") == "GKUiDesignTree"
                    else "AI")
            op = row.operator("gk.focus_tree", text=f"{tree.name}({kind})",
                              icon="NODETREE")
            op.tree_name = tree.name
        if len(trees) > 6:
            layout.label(text=f"…还有 {len(trees) - 6} 张(在侧栏『节点图』里找)",
                         icon="DOT")
        layout.label(text="预览只在 Blender 里看:不导出、不影响游戏",
                     icon="INFO")