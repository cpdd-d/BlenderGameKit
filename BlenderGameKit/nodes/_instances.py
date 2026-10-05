"""实例与场景节点 —— 从 nodes/nodes.py 按领域拆出的一块(GPT 审查:5280 行的单文件)。

本模块里的节点类通过 `@N` 登记进 `_common.CLASSES`。门面 `nodes/nodes.py`
按拆分前的**原始顺序**导入各领域模块,保证 `ALL_CLASSES` 的顺序不变。
"""

from ._common import *  # noqa: F401,F403



# ==========================================================================
# 实例与场景
# ==========================================================================
@N
class GKNodeSpawn(B.GKStatementNode):
    bl_idname = "GKNodeSpawn"
    bl_label = "实例_生成"
    bl_icon = "ADD"
    gk_category = "实例"
    gk_doc = "实例化一个场景并放到指定位置(子弹 / 特效 / 敌人)"
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(TEXT, "场景路径", "res://scenes/Bullet.tscn"),)
    gk_input_props = {"场景路径": "gk_scene_path"}
    gk_prop_defs = (
        ("gk_scene_path", "string", "res://scenes/Bullet.tscn", "场景路径", "要实例化的 .tscn"),
        ("gk_x", "float", 0.0, "X", "生成位置"),
        ("gk_y", "float", 0.0, "Y", "生成位置"),
        ("gk_z", "float", 0.0, "Z", "生成位置"),
        ("gk_parent", "node", "", "父节点", "留空表示挂到当前场景根节点"),
    )
    gk_outputs = (S.pin(FLOW, "完成"), S.pin(ANY, "实例", None),)

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_scene_path", text="")


@N
class GKNodeQueueFree(B.GKStatementNode):
    bl_idname = "GKNodeQueueFree"
    bl_label = "实例_销毁"
    bl_icon = "TRASH"
    gk_category = "实例"
    gk_doc = ("销毁节点(延迟到帧末，对应 queue_free)。"
              "── 两种用法 ──"
              "① 填『目标』(场景里摆好的物体)：销毁它的时候**连同名网格"
              "一起清**(导出后一个物体常常是『网格 + 碰撞代理』两个节点,"
              "只 free 代理的话画面上还看得见它);"
              "② 把『目标』**引脚**接上『资产_创建实例』的『实例』输出："
              "销毁那个**运行时新生成的**节点(它自带网格,直接清即可)。")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(OBJECT, "目标", None),)
    gk_prop_defs = (("gk_target", "node", TARGET_SELF, "目标", "要销毁的节点"),)

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)


@N
class GKNodeAssetSpawn(B.GKStatementNode):
    bl_idname = "GKNodeAssetSpawn"
    bl_label = "资产_创建实例"
    bl_icon = "DUPLICATE"
    gk_category = "实例"
    gk_doc = ("运行时**按资产名新摆一份**(植树 / 掉宝箱 / 刷障碍)。"
              "资产 = 『游戏物理』面板里填了『资产名』的那些物体 —— "
              "这里填的名字要和那里一致(右边放大镜可以从列表里挑,"
              "列表里会写它现在场上有几份)。"
              "── 生成什么 ──"
              "一份自带网格 + 碰撞的实例:网格复用资产那份 glb(一个模型只"
              "导一次),碰撞按本体的设置生成(勾掉『带碰撞』就只有网格)。"
              "『实例』输出是**新创建的那个节点**,可以直接接"
              "『实例_销毁』的『目标』引脚 —— 用完自己清掉。"
              "── 位置 ──"
              "直接接一根『取值_位置』或向量就能摆到指定地方;"
              "没接线时用节点上的 X / Y / Z")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(VECTOR, "位置", (0.0, 0.0, 0.0)),
    )
    gk_prop_defs = (
        ("gk_asset", "string", "", "资产名",
         "要和『游戏物理』面板里填的『资产名』一模一样"),
        ("gk_x", "float", 0.0, "X", "位置(『位置』引脚没接线时用它)"),
        ("gk_y", "float", 0.0, "Y", ""),
        ("gk_z", "float", 0.0, "Z", ""),
        ("gk_parent", "node", TARGET_SELF, "父节点",
         "留空 = 挂到当前场景根节点"),
        ("gk_solid", "bool", True, "带碰撞",
         "勾上 = 生成的这一份能挡人(和本体同款碰撞)"),
    )
    gk_outputs = (S.pin(FLOW, "完成"), S.pin(OBJECT, "实例", None),)

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_asset", text="", icon="ASSET_MANAGER")
        pick = row.operator("gk.pick_asset", text="", icon="VIEWZOOM")
        pick.node_name = self.name
        pick.asset_prop = "gk_asset"
        row = layout.row(align=True)
        row.prop(self, "gk_solid", text="带碰撞", toggle=True)
        row.prop(self, "gk_x", text="X")
        row.prop(self, "gk_y", text="Y")
        # 2D 项目不画 Z(只有一个平面)
        settings = getattr(getattr(context, "scene", None), "gk_settings", None)
        if str(getattr(settings, "dimension", "3")) != "2":
            row.prop(self, "gk_z", text="Z")


@N
class GKNodeSetCollision(B.GKStatementNode):
    bl_idname = "GKNodeSetCollision"
    bl_label = "物理_启用碰撞"
    bl_icon = "PHYSICS"
    gk_category = "物理"
    gk_doc = ("把目标的**碰撞**打开 / 关掉 —— 开完门让人走过去、捡走道具后"
              "不再挡路、临时把一块地板的碰撞关掉。"
              "── 只动碰撞 ──"
              "显示不动(要连显示一起切,前面接『显示_设置可见』);"
              "逻辑也不停(要停它的每帧处理用『显示_暂停节点』)。"
              "── 为什么不是 body.disabled ──"
              "Godot 4 的 CollisionObject3D **没有** disabled 这个属性"
              "(实测只有 disable_mode,那是『被禁用的形状怎么表现』,不是开关),"
              "所以真正干净的切法是 CollisionShape 的 disabled —— "
              "这个节点会把目标(和它子节点)的碰撞形状一起切")
    gk_inputs = (S.pin(FLOW, "执行"), S.pin(BOOL, "启用", True),)
    gk_input_props = {"启用": "gk_enable"}
    gk_prop_defs = (
        ("gk_enable", "bool", True, "启用",
         "未连线时用它:勾 = 碰撞生效,不勾 = 关掉碰撞"),
        ("gk_target", "node", TARGET_SELF, "目标", "要开关碰撞的节点"),
    )

    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_enable", text="", toggle=True,
                 icon="CHECKBOX_HLT" if getattr(self, "gk_enable", True)
                 else "CHECKBOX_DEHLT")
        B.draw_target_picker(self, layout)


@N
class GKNodeQuitGame(B.GKStatementNode):
    bl_idname = "GKNodeQuitGame"
    bl_label = "流程_退出游戏"
    bl_icon = "QUIT"
    gk_category = "流程"
    gk_doc = ("关掉游戏窗口(桌面版)。用户 2026-10-03 提的:"
              "开着游戏按 Esc 应该能退出 —— 那需要一个『按 Esc → 退出』的接法,"
              "这个节点就是那个『退出』。"
              "注意:网页 / 手机上没有窗口可关,那样只会停住逻辑")
    gk_inputs = (S.pin(FLOW, "执行"),)

    gk_prop_defs = (
        ("gk_delay", "float", 0.0, "延迟(秒)",
         "> 0 时先等一会儿再退(让退出动画/音效播完)"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_delay", text="延迟")


@N
class GKNodeLogicOutput(B.GKStatementNode):
    bl_idname = "GKNodeLogicOutput"
    bl_label = "逻辑_输出结果"
    bl_icon = "CHECKMARK"
    gk_category = "变量"
    gk_doc = ("**给这张逻辑图一个对外的结果**(用户 2026-10-03 的计划:"
              "『一个游戏逻辑最后应该输出布尔变量,以便与其他的游戏逻辑联动』)。"
              "名字 + 真假值 → 生成一个脚本变量(带 `输出_` 前缀),"
              "别的逻辑图 / 动画树用『变量_读取』读同一个名字即可 —— "
              "所有图编译成**同一个脚本**,所以这是跨图联动的通用做法。"
              "导出报告里会把这些输出列出来,方便对照")
    gk_inputs = (
        S.pin(FLOW, "执行"),
        S.pin(BOOL, "值", True),
    )
    gk_prop_defs = (
        ("gk_output_name", "string", "完成", "输出名",
         "别的图用『变量_读取』填 `输出_名字` 就能读到"),
        ("gk_note", "string", "", "说明", "只是给自己看的备注,不影响导出"),
    )
    gk_outputs = (S.pin(FLOW, "完成"),)


    def draw_buttons(self, context, layout):
        row = layout.row(align=True)
        row.prop(self, "gk_output_name", text="输出名")
        layout.prop(self, "gk_note", text="", icon="INFO")


@N
class GKNodeReloadScene(B.GKStatementNode):
    bl_idname = "GKNodeReloadScene"
    bl_label = "实例_重载场景"
    bl_icon = "FILE_REFRESH"
    gk_category = "实例"
    gk_doc = "重新加载当前场景(重开一局 / 复活后重置关卡)"
    gk_inputs = (S.pin(FLOW, "执行"),)

    gk_prop_defs = (
        ("gk_slot", "string", "1", "存档槽", "留空则重载当前场景;填了则读取该存档槽"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_slot", text="")


@N
class GKNodeChangeScene(B.GKStatementNode):
    bl_idname = "GKNodeChangeScene"
    bl_label = "场景_切换关卡"
    bl_icon = "SCENE_DATA"
    gk_category = "实例"
    gk_doc = ("切换到另一个关卡场景(换掉整棵场景树)。"
              "『目标场景』填 .tscn 的 res:// 路径,如 res://scenes/关卡2.tscn。"
              "── 注意 ── 切换之后当前场景里的东西全没了,所以它应该是"
              "一条链路的**最后一步**。想先播一段运镜再切,前面接"
              "『相机_切换』(带过渡)+『等待_秒』即可,这个节点只管换场")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_prop_defs = (
        ("gk_scene_path", "string", "res://scenes/关卡2.tscn", "目标场景",
         "要切过去的 .tscn 的 res:// 路径"),
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_scene_path", text="")


@N
class GKNodeGetNode(B.GKExpressionNode):
    bl_idname = "GKNodeGetNode"
    bl_label = "取值_获取节点"
    bl_icon = "NODE_SEL"
    gk_category = "实例"
    gk_doc = "按路径获取场景里的节点"
    gk_outputs = (S.pin(OBJECT, "节点", None),)

    gk_prop_defs = (("gk_path", "node", TARGET_SELF, "节点", "从当前脚本所在节点开始"),)

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout, "gk_path")


@N
class GKNodeIsOnFloor(B.GKExpressionNode):
    bl_idname = "GKNodeIsOnFloor"
    bl_label = "取值_是否在地面"
    bl_icon = "TRIA_DOWN"
    gk_category = "实例"
    gk_doc = "角色是否踩在地面(对应 is_on_floor)，做跳跃判定用"
    gk_outputs = (S.pin(BOOL, "在地面", True),)

    gk_prop_defs = (("gk_target", "node", TARGET_SELF, "目标", "角色节点"),)

    def draw_buttons(self, context, layout):
        B.draw_target_picker(self, layout)
