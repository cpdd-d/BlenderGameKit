"""用户自定义脚本节点(第一百五十一轮)。

用户原话:"请在游戏逻辑等其他节点图中加入节点:加载脚本(启动,停止),
加载渲染脚本……这些都是用户可以自定义的脚本设计"。

── 设计 ───────────────────────────────────────────────────────────
脚本来源 = **Blender 文本编辑器里的文本块**:
  1. 在文本编辑器里新建一个脚本(语言选 Python 也行,内容写 GDScript);
  2. 第一行必须是 `extends Node`(或 CanvasLayer / Node3D / Control);
  3. 节点里挑这个文本块,导出时自动落到 `res://scripts/custom/xxx.gd`。

三个节点:
  * 『脚本_加载』:把脚本实例化成子节点并运行;可指定"启动方法";
  * 『脚本_停止』:调用"停止方法"后销毁实例;
  * 『脚本_调用』:按名字调用脚本里的一个方法(无参数)。

运行期由生成器补三个小助手(`_gk_script_load/stop/call`),
实例键默认 = 文本块名字,所以"加载 / 停止 / 调用"三个节点不用手工对暗号。
"""

from ._common import *

# 节点分类(添加菜单里的新组)
SCRIPT_CATEGORY = "脚本"


def _script_label(layout, node, title="脚本"):
    """原生文本块选择器 + 打开当前引用的文本块。"""
    row = layout.row(align=True)
    row.prop(node, "gk_script_text", text=title)
    text = getattr(node, "gk_script_text", None)
    if text is None:
        return
    op = row.operator("gk.open_text_block", text="", icon="TEXT")
    op.text_name = text.name


@N
class GKNodeScriptLoad(B.GKStatementNode):
    bl_idname = "GKNodeScriptLoad"
    bl_label = "脚本_加载"
    bl_icon = "FILE_SCRIPT"
    gk_category = SCRIPT_CATEGORY
    gk_doc = ("**把用户自己写的 GDScript 挂到当前脚本宿主上运行。**\n"
              "脚本来自 Blender 的**文本块**(文本编辑器里新建,内容写 GDScript,"
              "第一行 `extends Node`)。导出时会自动写到"
              " `res://scripts/custom/`,不需要手工放文件。\n"
              "直接引用文本块；新增或重命名文本不会串选，删除所选文本后需要重新选择。\n"
              "『实例名』留空 = 用文本块名字;『启动方法』可选 —— 加载后调用"
              "脚本里的这个方法(没有 `_ready` 也行)。\n"
              "──────── 一个最小例子 ────────\n"
              "extends Node\n"
              "func _ready():\n"
              "    print(\"脚本跑起来了\")\n"
              "func start():\n"
              "    set_process(true)\n"
              "──────── 渲染脚本 ────────\n"
              "脚本 `extends CanvasLayer` 就能画自己的 UI / 后处理层;"
              "`extends Node3D` 可以生成 3D 物体。")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_outputs = (S.pin(FLOW, "完成"),)
    gk_prop_defs = (
        ("gk_script_text", "text_block", None, "脚本", "选一个 Blender 文本块"),
        ("gk_instance", "string", "", "实例名", "留空 = 用文本块名字"),
        ("gk_start_method", "string", "", "启动方法",
         "加载后调用这个无参方法(留空只跑 _ready)"),
    )

    def draw_buttons(self, context, layout):
        _script_label(layout, self)
        layout.label(text="extends Node / CanvasLayer / Node3D / Control",
                     icon="INFO")


@N
class GKNodeScriptStop(B.GKStatementNode):
    bl_idname = "GKNodeScriptStop"
    bl_label = "脚本_停止"
    bl_icon = "CANCEL"
    gk_category = SCRIPT_CATEGORY
    gk_doc = ("**停掉由『脚本_加载』启动的脚本**:先调用『停止方法』(可选),"
              "再把实例销毁。\n"
              "『脚本』和『实例名』要和加载那边**一致**;实例名留空时两边都"
              "用文本块名字,所以通常什么都不用填。\n"
              "销毁后脚本节点会从场景里消失;下次再次『脚本_加载』会重新"
              "`_ready` 一遍(状态也重新开始)。")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_outputs = (S.pin(FLOW, "完成"),)
    gk_prop_defs = (
        ("gk_script_text", "text_block", None, "脚本", "要停的脚本文本块"),
        ("gk_instance", "string", "", "实例名", "留空 = 用文本块名字"),
        ("gk_stop_method", "string", "", "停止方法",
         "销毁前调用这个无参方法(可选)"),
    )

    def draw_buttons(self, context, layout):
        _script_label(layout, self)
        layout.label(text="销毁实例;下次加载会重新 _ready", icon="INFO")


@N
class GKNodeScriptCall(B.GKStatementNode):
    bl_idname = "GKNodeScriptCall"
    bl_label = "脚本_调用"
    bl_icon = "PLAY"
    gk_category = SCRIPT_CATEGORY
    gk_doc = ("**调用已加载脚本里的一个无参方法。**\n"
              "『脚本』/『实例名』要和『脚本_加载』一致(实例名留空即可)。\n"
              "方法不存在时只在 Godot 控制台打一条警告,不会让游戏崩掉 ——"
              "适合做\"脚本可选地提供某个钩子\"。")
    gk_inputs = (S.pin(FLOW, "执行"),)
    gk_outputs = (S.pin(FLOW, "完成"),)
    gk_prop_defs = (
        ("gk_script_text", "text_block", None, "脚本", "要调用的脚本文本块"),
        ("gk_instance", "string", "", "实例名", "留空 = 用文本块名字"),
        ("gk_method", "string", "", "方法", "要调用的无参方法名"),
    )

    def draw_buttons(self, context, layout):
        _script_label(layout, self)
        layout.label(text="调用无参方法;没有这个方法只警告一次", icon="INFO")

