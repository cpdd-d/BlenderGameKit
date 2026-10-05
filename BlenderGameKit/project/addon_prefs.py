"""插件偏好设置(AddonPreferences)。

从 project.py 拆出(GPT 审查)。
"""

import bpy

from .. import blender_adapter
from .godot import find_godot_executable



# ---------------------------------------------------------------------------
# 插件偏好设置
# ---------------------------------------------------------------------------
class GKAddonPreferences(bpy.types.AddonPreferences):
    # bl_idname 必须等于**插件根模块名**,Blender 才认这个偏好设置属于谁。
    # 普通 addon 是 "BlenderGameKit",扩展是 "bl_ext.<仓库>.<扩展 id>"。
    # 算法统一放在 blender_adapter.addon_package(),别再各写一份
    # (以前两处都写成 `split(".")[0]`,扩展下算成 "bl_ext",于是这个类
    #  从来没能注册成功、偏好设置面板永远打不开)。
    bl_idname = blender_adapter.addon_package(__package__)

    godot_path: bpy.props.StringProperty(
        name="Godot 可执行文件",
        description="Godot 编辑器/运行时的路径。留空会自动到常见位置找",
        default="",
        subtype="FILE_PATH",
    )
    auto_export_on_run: bpy.props.BoolProperty(
        name="运行游戏前自动保存并导出",
        description=("点『直接运行游戏』时,先自动保存 .blend 并导出脚本与模型。"
                     "关掉的话点运行跑的是上次导出的旧内容"),
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Godot 可执行文件", icon="PLAY")
        box.prop(self, "godot_path", text="")
        found = find_godot_executable(context)
        if found:
            box.label(text=f"当前使用: {found}", icon="CHECKMARK")
        else:
            box.label(text="没找到 Godot,请手动指定路径", icon="ERROR")
        box.operator("gk.detect_godot", icon="VIEWZOOM")

        box = layout.box()
        box.label(text="导出行为", icon="EXPORT")
        box.prop(self, "auto_export_on_run")
        if self.auto_export_on_run:
            box.label(text="点『直接运行游戏』时会先保存 + 导出", icon="INFO")

        box = layout.box()
        box.label(text="项目绑定规则", icon="FILE_FOLDER")
        for line in (
            "一个 .blend 对应一个 Godot 项目",
            "项目建在 .blend 旁边,文件夹名 = 文件名 + _godot",
            "不想放旁边的话,在侧栏『项目』里手动指定",
        ):
            box.label(text=line, icon="DOT")


GK_CLASSES = (GKAddonPreferences,)
