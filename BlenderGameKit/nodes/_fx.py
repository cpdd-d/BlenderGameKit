"""粒子特效节点:播放 / 停止 / 爆发(需求 5,2026-10-05 第二十一轮)。

本模块里的节点类通过 `@N` 登记进 `_common.CLASSES`。门面 `nodes/nodes.py`
按拆分前的**原始顺序**导入各领域模块,保证 `ALL_CLASSES` 的顺序不变。

发射器在 Blender 里的标记方式:物体 → 『游戏物理』面板 → 『粒子特效』框 →
勾『作为粒子发射器』。这三个节点作用在**那个物体**上(导出后是 Godot 里的
GPUParticles3D / CPUParticles3D 节点)。
"""

from ._common import *  # noqa: F401,F403

# 目标提示:三个节点共用一段说明,写在这里省得三处各写一份
_FX_TARGET_HINT = ("在『游戏物理』→『粒子特效』里给那个物体勾上"
                   "『作为粒子发射器』,这里点右边的放大镜从场景里挑它")


# ==========================================================================
# 粒子特效
# ==========================================================================
@N
class GKNodeFxPlay(B.GKStatementNode):
    bl_idname = "GKNodeFxPlay"
    bl_label = "特效_播放"
    bl_icon = "PLAY"
    gk_category = "特效"
    gk_doc = ("让『目标』那个粒子发射器**开始喷** —— 开局不喷的火把这时点起来、"
              "进战斗才冒的烟这时冒起来。"
              "── 目标怎么填 ──"
              + _FX_TARGET_HINT + "。"
              "── 已经在喷的时候 ──"
              "什么都不发生(这正是不打断当前这一轮的意思);"
              "想『从头再喷一轮』请用『特效_爆发』。")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "目标", "要开始喷的粒子发射器"),
    )

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)


@N
class GKNodeFxStop(B.GKStatementNode):
    bl_idname = "GKNodeFxStop"
    bl_label = "特效_停止"
    bl_icon = "PAUSE"
    gk_category = "特效"
    gk_doc = ("让『目标』那个粒子发射器**停喷**(灭火 / 收招 / 雨停)。"
              "── 注意 ──"
              "停的是『不再生成新粒子』—— 已经在飞的那些会自然消散完,"
              "不是啪一下全没(Godot 就是这么设计的);"
              "想马上重来一轮用『特效_爆发』,想彻底不显示就把它藏起来。"
              "── 目标怎么填 ──"
              + _FX_TARGET_HINT + "。")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "目标", "要停下来的粒子发射器"),
    )

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)


@N
class GKNodeFlashRed(B.GKStatementNode):
    bl_idname = "GKNodeFlashRed"
    bl_label = "特效_闪红"
    bl_icon = "COLOR"
    gk_category = "特效"
    gk_doc = ("让『目标』整个模型**闪一下颜色**(默认红),过一会儿自动恢复 —— "
              "受击反馈 / 受伤无敌 / 被撞提示都用它。\n"
              "── 染的是哪一层 ──\n"
              "把整棵 MeshInstance3D 的材质**临时盖掉**(material_override),"
              "所以一个模型几十个材质槽也是一次全变;时间到了自动恢复原样,"
              "卡通材质不受影响。\n"
              "── 和『屏幕效果_闪白』的区别 ──\n"
              "闪白是**全屏泛光**,闪红是**这个模型自己**变红 —— "
              "谁挨打就闪谁,玩家在混战里也分得清。\n"
              "── 目标怎么填 ──\n"
              "角色/敌人的**碰撞节点**(导出的角色模型是挂在它下面的);"
              "2D 用精灵节点的 modulate 染色,同样的用法")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "秒", 0.25),)
    gk_input_props = {"秒": "gk_time"}
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "目标", "要闪的模型(整棵网格一起染)"),
        ("gk_time", "float", 0.25, "秒", "闪多久(受击一般 0.2~0.3)"),
        ("gk_color", "color", (1.0, 0.25, 0.25, 1.0), "颜色", "闪的时候染成什么色"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        B.draw_target_picker(self, row)
        row.prop(self, "gk_time", text="秒")
        layout.prop(self, "gk_color", text="")


@N
class GKNodeFxBurst(B.GKStatementNode):
    bl_idname = "GKNodeFxBurst"
    bl_label = "特效_爆发"
    bl_icon = "FILE_REFRESH"
    gk_category = "特效"
    gk_doc = ("**重新喷一轮**:把在飞的粒子清掉、从第 0 帧重来并继续喷。"
              "受击火花 / 落地烟尘 / 爆炸冲击这一类『来一下就完』的效果用它。"
              "── 和『特效_播放』的区别 ──"
              "播放 = 只把开关打开(已经在喷就什么都不做);"
              "爆发 = 不管现在什么状态,都从头来一轮。"
              "── 一次性发射器 ──"
              "发射器勾了『一次性』时,这个节点就是『再放一轮』(每按一下喷一次),"
              "配合『自动播放』关掉最顺手。"
              "── 目标怎么填 ──"
              + _FX_TARGET_HINT + "。")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_target", "node", TARGET_SELF, "目标", "要重新喷一轮的粒子发射器"),
    )

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)