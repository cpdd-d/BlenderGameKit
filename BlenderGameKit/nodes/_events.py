"""事件节点(_ready/_process/_input/自定义函数) —— 从 nodes/nodes.py 按领域拆出的一块(GPT 审查:5280 行的单文件)。

本模块里的节点类通过 `@N` 登记进 `_common.CLASSES`。门面 `nodes/nodes.py`
按拆分前的**原始顺序**导入各领域模块,保证 `ALL_CLASSES` 的顺序不变。
"""

from ._common import *  # noqa: F401,F403



# ==========================================================================
# 事件
# ==========================================================================
@N
class GKNodeOnReady(B.GKEventNode):
    bl_idname = "GKNodeOnReady"
    bl_label = "每当_准备"
    bl_icon = "PLAY"
    gk_category = "事件"
    gk_doc = "游戏开始时执行一次，对应 Godot 的 _ready()"
    gk_outputs = (S.pin(FLOW, "执行"),)



@N
class GKNodeOnProcess(B.GKEventNode):
    bl_idname = "GKNodeOnProcess"
    bl_label = "每当_每帧"
    bl_icon = "TIME"
    gk_category = "事件"
    gk_doc = "每一帧执行，对应 Godot 的 _process(delta)"
    gk_prop_defs = (("gk_order", "int", 0, "排序", "多个每帧事件时的执行顺序"),)
    gk_outputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "帧间隔", None),)


@N
class GKNodeOnPhysicsProcess(B.GKEventNode):
    bl_idname = "GKNodeOnPhysicsProcess"
    bl_label = "每当_物理帧"
    bl_icon = "PHYSICS"
    gk_category = "事件"
    gk_doc = "固定频率执行(默认每秒 60 次)，对应 _physics_process(delta)"
    gk_prop_defs = (("gk_order", "int", 0, "排序", "多个物理帧事件时的执行顺序"),)
    gk_outputs = (S.pin(FLOW, "执行"), S.pin(FLOAT, "帧间隔", None),)


@N
class GKNodeOnInput(B.GKEventNode):
    bl_idname = "GKNodeOnInput"
    bl_label = "每当_输入"
    bl_icon = "HAND"
    gk_category = "事件"
    gk_doc = "按键 / 鼠标事件，对应 Godot 的 _input(event)"
    gk_outputs = (S.pin(FLOW, "执行"), S.pin(ANY, "输入事件", None),)


@N
class GKNodeFunction(B.GKEventNode):
    bl_idname = "GKNodeFunction"
    bl_label = "自定义函数"
    bl_icon = "FILE_SCRIPT"
    gk_category = "事件"
    gk_doc = "定义一个可被其它节点调用、也能接信号的函数"
    gk_prop_defs = (("gk_func_name", "string", "my_function", "函数名", "生成的 func 名称"),)
    gk_outputs = (S.pin(FLOW, "执行"),)


    def draw_buttons(self, context, layout):
        layout.prop(self, "gk_func_name", text="")


# ==========================================================================
# 触发器(用户 2026-10-04 的第 6 条:"游戏触发器逻辑设计")
# ==========================================================================
# 和上面几个事件节点**不一样**:事件节点同类会合并成一个函数;触发器是
# "那件事发生时执行一次",每接一处就是一条独立的信号回调。所以代码生成
# 把它们当"每个节点一个函数"处理(见 codegen.generator 里的触发器那一段)。
#
# 四个节点覆盖三类最常用的触发:
#   * 进入区域 / 离开区域 —— Area3D/Area2D 的 body_entered / body_exited
#     (吃金币、进传送门、踩触发板、掉出边界)
#   * 碰撞体触发 —— Area3D/Area2D 的 area_entered
#     (两个"区域"相撞:角色身上的拾取区域撞到金币那个区域)
#   * 定时触发 —— 一个 Timer 的 timeout(隔几秒刷一波、限时挑战)
@N
class GKNodeOnAreaEntered(B.GKEventNode):
    bl_idname = "GKNodeOnAreaEntered"
    bl_label = "进入区域"
    bl_icon = "MESH_CIRCLE"
    gk_category = "事件"
    gk_doc = ("有物体**进入**指定 Area 区域时执行一次。做吃金币、进传送门、"
              "踩到触发板都用它。区域节点要在『游戏物理』里设成 Area3D/Area2D。")
    gk_prop_defs = (
        ("gk_area", "node", "", "区域", "要监听的 Area 节点名;必须指定"),
        ("gk_filter", "string", "", "只认这个物体",
         "填了名字就只有该物体进入才触发;留空 = 任何物体"),
    )
    gk_outputs = (S.pin(FLOW, "执行"),)


@N
class GKNodeOnAreaExited(B.GKEventNode):
    bl_idname = "GKNodeOnAreaExited"
    bl_label = "离开区域"
    bl_icon = "MESH_CIRCLE"
    gk_category = "事件"
    gk_doc = "有物体**离开**指定 Area 区域时执行一次(对应 body_exited)"
    gk_prop_defs = (
        ("gk_area", "node", "", "区域", "要监听的 Area 节点名;必须指定"),
        ("gk_filter", "string", "", "只认这个物体",
         "填了名字就只有该物体离开才触发;留空 = 任何物体"),
    )
    gk_outputs = (S.pin(FLOW, "执行"),)


@N
class GKNodeCollisionTrigger(B.GKEventNode):
    bl_idname = "GKNodeCollisionTrigger"
    bl_label = "碰撞体触发"
    bl_icon = "MESH_CIRCLE"
    gk_category = "事件"
    gk_doc = ("有另一个『区域』(Area)碰到指定区域时执行一次(对应 "
              "area_entered)。角色身上挂一个 Area 当「拾取范围」,金币也是 "
              "Area,两者相碰就触发 —— 比「比距离」更稳。")
    gk_prop_defs = (
        ("gk_area", "node", "", "区域", "要监听的 Area 节点名;必须指定"),
        ("gk_filter", "string", "", "只认这个区域",
         "填了名字就只有该 Area 进入才触发;留空 = 任何 Area"),
    )
    gk_outputs = (S.pin(FLOW, "执行"),)


@N
class GKNodeOnTimer(B.GKEventNode):
    bl_idname = "GKNodeOnTimer"
    bl_label = "定时触发"
    bl_icon = "TIME"
    gk_category = "事件"
    gk_doc = ("等一段时间后执行;勾上『重复』就每隔这么久执行一次"
              "(Godot 的 Timer 节点,自动创建,不用手动摆)")
    gk_prop_defs = (
        ("gk_seconds", "float", 1.0, "秒数", "等待 / 间隔的秒数"),
        ("gk_repeat", "bool", False, "重复",
         "不勾 = 只触发一次;勾上 = 每隔『秒数』重复触发"),
    )
    gk_outputs = (S.pin(FLOW, "执行"),)
