"""共享数据模型(纯 Python,不依赖 bpy)。

── 为什么要有这个文件(GPT 审查 / 用户 2026-10-04)──────────────────
插件内部大量用 `dict` 传递设置、结果和诊断:`settings["base_class"]`、
`settings["write_scene"]`、`warnings` 是一串裸字符串。问题是:

  * 拼错字段名(`settings["baseclass"]`)不报错,只是**永远读默认值** ——
    用户会遇到"我改了设置却没生效",查起来极难;
  * 默认值散落在多处(`default_settings()` / 各处 `.get(x, fallback)`),
    很容易不一致;
  * 告警只是字符串,分不出级别 / 来源 / 修复建议。

这里把三类东西固化成 dataclass:

  * `ExportSettings` —— 一次导出的全部设置;
  * `CodegenResult`  —— 一次代码生成的产物(代码/告警/提示/输入动作);
  * `Diagnostic`     —— 一条结构化诊断(级别/代码/消息/建议/节点名)。

**边界仍然接受 dict**(外部脚本、单元测试一直这么传),只是在进入
内部逻辑时用 `from_dict()` 归一化,内部一律用属性访问。
"""

from dataclasses import dataclass, field, fields


@dataclass
class ExportSettings:
    """一次导出的设置。字段与 `exporter.default_settings()` 一一对应。"""

    dimension: str = "3"
    base_class: str = "CharacterBody3D"
    class_name: str = ""
    export_tunables: bool = True
    output_dir: str = ""
    dir_source: str = ""
    write_scene: bool = False
    node_name: str = "Player"
    debug_runtime: bool = False
    # 上帝摄像机(用户 2026-10-04):常驻一台自由飞行相机,运行时 Tab 切换
    god_camera: bool = False
    # 只算"会写哪些文件",不落盘(给"导出前先看一眼"用)
    dry_run: bool = False

    @classmethod
    def from_dict(cls, data):
        """从 dict 归一化。未知键**保留**在 `extra` 里,不丢信息。"""
        data = dict(data or {})
        known = {item.name for item in fields(cls)}
        obj = cls(**{k: v for k, v in data.items() if k in known})
        obj.extra = {k: v for k, v in data.items() if k not in known}
        return obj

    def to_dict(self):
        """转回 dict(供仍按 dict 取值的下游,如 codegen 选项)。"""
        data = {item.name: getattr(self, item.name) for item in fields(self)}
        data.update(getattr(self, "extra", {}) or {})
        return data


@dataclass
class CodegenResult:
    """一次代码生成的完整结果。"""

    code: str = ""
    warnings: list = field(default_factory=list)
    # 结构化诊断(models.Diagnostic 对象)。`warnings` 是它们**摊平后的文本**,
    # 供 UI / 日志照样按字符串显示;要按 code 归类、做过滤或测试断言时看这里。
    diagnostics: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    # [(动作名, 捕获到的键名), ...] —— 导出流程据此写 project.godot 的 [input]
    input_actions: list = field(default_factory=list)
    # 本次生成用到的曲线资源 {曲线名: {"kind": "Curve"|"Curve3D",
    # "points": [...]}} —— 导出流程据此写 curves/*.tres(见 curve_assets)
    curve_assets: dict = field(default_factory=dict)


@dataclass
class Diagnostic:
    """一条结构化诊断。

    `level` 取 "error" / "warning" / "info"。`code` 是一个稳定的短标识
    (如 `MISSING_INPUT_ACTION`),方便测试和以后做"按代码过滤"。
    `hint` 是给用户的**修复建议**。
    """

    level: str = "warning"
    code: str = ""
    message: str = ""
    hint: str = ""
    node_name: str = ""

    def render(self):
        """摊平成一行字符串(导出报告/日志仍按字符串展示)。"""
        text = self.message
        if self.hint:
            text = f"{text} —— {self.hint}"
        return text

    def is_error(self):
        return self.level == "error"


def render_warnings(items):
    """把 Diagnostic / 字符串混合列表摊平成字符串列表。

    兼容期用:老代码往 warnings 里 append 裸字符串,新代码 append
    `Diagnostic`。导出报告统一走这个函数,两边都能显示。
    """
    out = []
    for item in items or ():
        out.append(item.render() if isinstance(item, Diagnostic) else str(item))
    return out