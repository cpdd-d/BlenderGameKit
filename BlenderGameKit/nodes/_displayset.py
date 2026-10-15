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

# 这张图的"设置卡"名单(第四十九轮起五张)。写在这里(而不是散在类上)
# 有两个用它的地方:
#   * 侧栏『补齐缺失的设置卡』按它找缺哪张;
#   * 测试按它断言"卡片一张不少"。
DISPLAY_CARD_IDS = (
    "GKNodeDisplayCollision",
    "GKNodeDisplayDebug",
    "GKNodeDisplayStyle",
    "GKNodeDisplayPreview",
    # 第五张(第四十九轮:阶段②环境与后处理)—— 排在最后,前四张在图上
    # 的位置一张都不动(老工程新建时四张卡坐标完全不变)。
    "GKNodeDisplayEnvironment",
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

    # 环境氛围(第四十九轮):背景 / 环境光 / 雾 / 泛光 / 调色 各开没开,
    # 一行说清 —— 老工程没有这些属性时全部按"关"兜底(不能抛异常)。
    env_on = bool(getattr(settings, "env_enabled", False))
    if not env_on:
        env_text = "关"
    else:
        marks = []
        background = str(getattr(settings, "env_background", "COLOR") or "COLOR")
        marks.append("天空" if background == "SKY" else "背景色")
        try:
            ambient = float(getattr(settings, "env_ambient_energy", 0.0) or 0.0)
        except (TypeError, ValueError):
            ambient = 0.0
        if ambient > 0.0:
            marks.append("环境光")
        if bool(getattr(settings, "env_fog", False)):
            marks.append("雾")
        if bool(getattr(settings, "env_glow", False)):
            marks.append("泛光")
        if bool(getattr(settings, "env_adjust", False)):
            marks.append("调色")
        env_text = "开(" + "·".join(marks) + ")"
    rows.append(("环境氛围", env_text, env_on))
    return rows


def _scene_settings(context):
    """卡上读写的那份场景设置(GKSceneSettings);拿不到返回 None。"""
    scene = getattr(context, "scene", None)
    return getattr(scene, "gk_settings", None) if scene is not None else None


def _env_preset_library():
    """文件级环境预设库树(延迟导入,躲开 nodes<->properties 的循环)。"""
    from .. import properties

    return properties.env_preset_library()


def _picked_preset(library):
    """『预设』下拉当前选中的那个预设条目;选的是内置或没选返回 None。

    library 是 `env_preset_library()` 返回的预设库树(文件级共享,第一百一
    十五轮)。旧版本挂在场景 gk_settings 上,现在统一走这张库。
    """
    if library is None:
        return None
    pick = str(getattr(library, "env_preset_pick", "") or "")
    if not pick.isdigit():
        return None
    presets = getattr(library, "env_presets", ()) or ()
    index = int(pick)
    return presets[index] if 0 <= index < len(presets) else None


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

        # ── 全局卡通风格(第一百一十五轮阶段 B)──────────────────────
        # 场景级风格:导出时作为**默认**写进没调过参数的材质(整关一键
        # 定风格)。运行时『环境_应用预设』仍可整套覆盖。
        box = layout.box()
        row = box.row(align=True)
        row.prop(settings, "style_enabled", toggle=True, icon="MATERIAL")
        if bool(getattr(settings, "style_enabled", False)):
            srow = box.row(align=True)
            srow.prop(settings, "style_shadow_step", text="明暗分界")
            srow.prop(settings, "style_three_tone", text="三段")
            box.prop(settings, "style_shadow_tint", text="阴影色")
            srow = box.row(align=True)
            srow.prop(settings, "style_rim", text="边缘光")
            srow.prop(settings, "style_specular", text="高光")
            box.label(text="导出时作为整关材质的默认风格", icon="INFO")
        else:
            box.label(text="关着 = 老默认风格(不改任何材质)", icon="INFO")


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


@N
class GKNodeDisplayEnvironment(B.GKStatementNode):
    bl_idname = "GKNodeDisplayEnvironment"
    # ── 第 123 轮改名:『显示_环境氛围』→『游戏环境设置』(用户原话)─────
    # "现在的环境渲染成了『中世纪』的风格了……并且把环境预设改成
    #  游戏环境设置这样"
    # 改的是**标签**,`bl_idname` / 字段名一个字没动 —— 老 .blend 里的
    # 节点和值原样有效(改 idname 会让老工程整节点失效,那是数据迁移的活,
    # 收益为零)。
    bl_label = "游戏环境设置"
    bl_icon = "WORLD"
    gk_category = "显示设置"
    gk_tree_type = "GKDisplayTree"
    gk_inputs = ()
    gk_outputs = ()
    gk_doc = ("**游戏环境设置**(第四十九轮建卡 / 第 123 轮改名合并)—— "
              "给这一关挂一个 WorldEnvironment,并从这里选整套氛围。\n"
              "『环境与后处理』总开关:关着 = 产物里连节点都不多一个"
              "(老工程导出结果一字不变)。\n"
              "开着才能设这几样(卡上改 = 导出**立刻**按新值走,不用点应用):\n"
              "* **背景**:纯色(用背景色铺满)或天空(程序化天空,顶色 = 背景色);\n"
              "* **环境光**:全局补光颜色 + 强度(0 = 没有)—— 幽暗场景给一点\n"
              "  冷蓝,免得背光面死黑;\n"
              "* **雾**:体积雾 —— 恐怖片『空气感』的主力;颜色 + 浓度;\n"
              "* **泛光**:亮的地方往外溢光(灯 / 手电筒 / 火光毛起来)+ 强度 / 溢出;\n"
              "* **色调**:色调映射(线性 / Reinhard / Filmic / ACES)+ 可选调色\n"
              "  (亮度 / 对比度 / 饱和度);\n"
              "* **预设**(卡下半部分):「卡通二次元」三档(卡通日间 / 卡通黄昏 /\n"
              "  卡通夜晚)+ 氛围四档(恐怖夜 / 明亮白天 / 黄昏 / 浓雾夜),\n"
              "  一键套整套;自己调的也能存成命名预设,游戏里用\n"
              "  『环境_应用预设』节点按名字切。\n"
              "⚠ 想要**卡通二次元**而不是写实奇幻观感,关键在预设里的三件事:\n"
              "  环境光强度别低于 ~0.6(暗部不许死黑)、阴影色用**冷色相**\n"
              "  (暖棕暗部立刻变『中世纪』)、饱和度 1.15 以上。\n"
              "⚠ Godot 的 Environment **没有**内置暗角(vignette),暗角要靠\n"
              "后处理着色器 —— 留到氛围套件那一轮。\n"
              "⚠ 只对 **3D 项目**有效(WorldEnvironment 是 3D 的东西);\n"
              "2D 项目里开着也不会导出(卡上会红字提醒)。\n"
              "设置挂在**场景**上:多关卡时每关各有一套,切场景卡上的值跟着切。")

    def draw_buttons(self, context, layout):
        settings = _scene_settings(context)
        if settings is None:
            layout.label(text="拿不到场景设置(先打开一个场景)", icon="ERROR")
            return
        layout.prop(settings, "env_enabled", toggle=True, icon="WORLD")
        if not bool(getattr(settings, "env_enabled", False)):
            layout.label(text="关着 = 不导出环境(和以前完全一样)", icon="INFO")
            return
        if str(getattr(settings, "dimension", "3")) == "2":
            layout.label(text="⚠ 2D 项目不导环境(WorldEnvironment 只对 3D 有用)",
                         icon="ERROR")
            return

        # ── 环境预设(第一百一十三轮 / 第一百一十五轮:文件级共享)────
        # 存快照 / 一键应用;游戏里用『环境_应用预设』节点按名字切。
        # 预设存在文件级『环境预设库』树里 —— 多关卡共用同一套。
        #
        # ── 第 123 轮:把它提到这一盒的**最前面**,并分组显示 ────────────
        # 用户原话:"把环境预设改成游戏环境设置这样"。他的意思不是改个名,
        # 而是**这里应该是进来就能套整套氛围的地方** —— 以前它压在
        # 背景/雾/泛光/色调五组参数下面,得先滚半天才看见,于是"想要二次元"
        # 的人一直在手调单参数(调出来的就是那个『中世纪』观感)。
        # 现在顺序反过来:先挑整套预设,不满意再往下手调细节。
        library = _env_preset_library()
        box = layout.box()
        box.label(text="环境预设(选一套 = 整套氛围,跨场景共享)",
                  icon="BOOKMARKS")
        row = box.row(align=True)
        row.prop(library, "env_preset_pick", text="")
        row.operator("gk.env_preset_apply", text="应用", icon="CHECKMARK")
        # 二次元那三档单独提一行说清"这就是动画观感" —— 名字在中文里
        # 看不太出差别(卡通日间 vs 明亮白天),一句话省掉一次试错。
        box.label(text="★ 卡通日间 / 卡通黄昏 / 卡通夜晚 = 动画赛璐璐观感",
                  icon="INFO")
        box.label(text="想要二次元:先应用这三档之一,再按需要微调下面",
                  icon="CHECKMARK")
        row = box.row(align=True)
        row.prop(library, "env_preset_new_name", text="")
        row.operator("gk.env_preset_save", text="存为预设", icon="ADD")
        box.operator("gk.env_preset_remove", text="删除选中的预设", icon="X")

        # ── 卡通风格(第一百一十三轮:环境系统大蓝图)────────────────
        # 预设里额外带一份**全局 shader 风格**:运行时应用预设会把这些值
        # 覆盖到场景里所有 toon 材质。选中一个自己存的预设就能在这儿调。
        picked = _picked_preset(library)
        if picked is not None:
            style_box = box.box()
            style_box.label(text="卡通风格(运行时覆盖所有材质)", icon="MATERIAL")
            srow = style_box.row(align=True)
            srow.prop(picked, "p_style_shadow_step", text="明暗分界")
            srow.prop(picked, "p_style_three_tone", text="三段")
            style_box.prop(picked, "p_style_shadow_tint", text="阴影色")
            srow = style_box.row(align=True)
            srow.prop(picked, "p_style_rim", text="边缘光")
            srow.prop(picked, "p_style_specular", text="高光")
            style_box.label(text="阴影色用冷色相 —— 暖棕暗部会变写实奇幻",
                            icon="INFO")
        else:
            box.label(text="选一个自己存的预设,就能给它配一套卡通风格",
                      icon="INFO")

        # ── 逐项微调(背景 / 环境光 / 雾 / 泛光 / 色调)────────────────
        # 第 123 轮:从"开头"挪到"预设之后" —— 先挑整套,再动细节。
        detail = layout.box()
        detail.label(text="逐项微调(挑了预设之后再用)", icon="OPTIONS")

        box = detail.box()
        # ⚠ `IMAGE_BG` 不存在(第 123 轮用户实测抓到的崩溃):
        #     UILayout.operator(): error with keyword argument "icon" - enum ...
        # 正确的名字是 `IMAGE_BACKGROUND`。图标名写错的症状很隐蔽 ——
        # 只往状态栏打一行 traceback,整个卡片直接不画。
        # 批量自查工具见 `.audit\check_icons_all.py`。
        box.label(text="背景", icon="IMAGE_BACKGROUND")
        row = box.row(align=True)
        row.prop(settings, "env_background", expand=True)
        row = box.row(align=True)
        row.prop(settings, "env_bg_color", text="")
        row.operator("gk.env_from_world", text="", icon="WORLD")

        box = detail.box()
        box.label(text="环境光(全局补光)", icon="LIGHT")
        row = box.row(align=True)
        row.prop(settings, "env_ambient_color", text="")
        row.prop(settings, "env_ambient_energy", text="强度")
        box.operator("gk.env_from_light", text="从场景主灯取颜色",
                     icon="LIGHT_SUN")
        if float(getattr(settings, "env_ambient_energy", 0.0)) < 0.6:
            box.label(text="低于 0.6 暗部会发死 —— 二次元一般 0.6~1.0",
                      icon="ERROR")

        box = detail.box()
        row = box.row(align=True)
        row.prop(settings, "env_fog", toggle=True, icon="MOD_FLUID")
        if bool(getattr(settings, "env_fog", False)):
            row.prop(settings, "env_fog_color", text="")
            box.prop(settings, "env_fog_density", text="浓度")
            # 雾色偏暖 + 浓度高 = 那种"土黄的中世纪空气感"(用户实测的那张图)。
            fog_c = tuple(getattr(settings, "env_fog_color", (0.3, 0.34, 0.42, 1.0)))
            if len(fog_c) >= 3 and fog_c[0] > fog_c[2] + 0.05 and \
                    float(getattr(settings, "env_fog_density", 0.0)) > 0.02:
                box.label(text="雾色偏暖且浓 = 写实奇幻观感;二次元用冷色/薄雾",
                          icon="ERROR")

        box = detail.box()
        row = box.row(align=True)
        row.prop(settings, "env_glow", toggle=True, icon="LIGHT_SUN")
        if bool(getattr(settings, "env_glow", False)):
            box.prop(settings, "env_glow_intensity", text="强度")
            box.prop(settings, "env_glow_bloom", text="溢出")

        box = detail.box()
        box.prop(settings, "env_tonemap", text="色调映射")
        box.label(text="ACES 会把颜色压灰;二次元一般用 Linear",
                  icon="INFO") if str(getattr(settings, "env_tonemap", "")) == "ACES" else None
        box.prop(settings, "render_quality")
        row = box.row(align=True)
        row.prop(settings, "env_ssao")
        row.prop(settings, "env_ssil")
        row = box.row(align=True)
        row.prop(settings, "env_adjust", toggle=True, icon="COLOR")
        if bool(getattr(settings, "env_adjust", False)):
            box.prop(settings, "env_brightness", text="亮度")
            box.prop(settings, "env_contrast", text="对比度")
            box.prop(settings, "env_saturation", text="饱和度")
            if float(getattr(settings, "env_saturation", 1.0)) < 1.1:
                box.label(text="二次元建议饱和度 ≥ 1.15(颜色要够艳)",
                          icon="INFO")

        layout.label(text="改这里 = 导出立刻生效,不用点应用", icon="CHECKMARK")
        layout.label(text="暗角(Godot 无内置)留到氛围套件那轮", icon="INFO")
