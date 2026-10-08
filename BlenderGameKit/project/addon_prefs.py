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

    # ── 项目顾问(第五十三轮)────────────────────────────────────────
    # 走 OpenAI 兼容协议,所以 DeepSeek / 通义 / 硅基流动 / 本地 Ollama
    # 填的都是这三项。**地址默认留空 = 一个字节都不发**。
    advisor_base_url: bpy.props.StringProperty(
        name="AI 地址(OpenAI 兼容)",
        description=("比如 https://api.deepseek.com 或 http://localhost:11434"
                     "(本地 Ollama)。留空 = 不发请求。插件会自动补上 "
                     "/v1/chat/completions"),
        default="",
    )
    advisor_model: bpy.props.StringProperty(
        name="模型名",
        description="比如 deepseek-chat / qwen-plus / llama3。填错会收到 400",
        default="",
    )
    advisor_api_key: bpy.props.StringProperty(
        name="API Key",
        description=("本地 Ollama 可以不填;云服务需要。⚠ 只能明文存在 "
                     "Blender 偏好设置里,别用主账号的 Key"),
        default="",
        subtype="PASSWORD",
    )
    advisor_include_code: bpy.props.BoolProperty(
        name="把节点图与生成的代码也发给 AI",
        description=("勾选(默认)= 连生成的 GDScript 一起发,"
                     "诊断『角色为什么不动』这类问题更准;"
                     "不勾 = 只发诊断与报错,不含代码"),
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

        # ── 项目顾问(第五十三轮)────────────────────────────────────
        # 这是插件**唯一的联网功能**,所以把"会发生什么"写在面板上:
        # 地址留空就一个字节都不发;只在你点『问 AI』时才发。
        box = layout.box()
        box.label(text="项目顾问(AI)", icon="QUESTION")
        box.prop(self, "advisor_base_url", text="AI 地址")
        box.prop(self, "advisor_model", text="模型名")
        box.prop(self, "advisor_api_key", text="API Key")
        box.prop(self, "advisor_include_code")
        if not (self.advisor_base_url or "").strip():
            box.label(text="地址留空 = 不联网,只在侧栏整理出可复制的诊断",
                      icon="INFO")
        else:
            from .. import advisor
            box.label(text=f"实际请求: {advisor.chat_url(self.advisor_base_url)}",
                      icon="INFO")
        box.label(text="只在点『问 AI』时发送;不会自动上传", icon="LOCKED")

        box = layout.box()
        box.label(text="项目绑定规则", icon="FILE_FOLDER")
        for line in (
            "一个 .blend 对应一个 Godot 项目",
            "项目建在 .blend 旁边,文件夹名 = 文件名 + _godot",
            "不想放旁边的话,在侧栏『项目』里手动指定",
        ):
            box.label(text=line, icon="DOT")


GK_CLASSES = (GKAddonPreferences,)
