"""Blender 适配层:把对 bpy 的访问集中在这里。

── 为什么(GPT 审查 / 用户 2026-10-04)────────────────────────────
bpy 访问以前散落在 project.layout / exporter / operator_io … 各处,
于是核心逻辑离不开 Blender,离线测试只能靠 `gk_stub` 打桩(桩里还得
模拟 `bpy.data` / `bpy.context`,很难维护)。

把最常用的三项访问集中到本模块:

  * `filepath()`  —— 当前 .blend 路径;
  * `scene()`     —— 当前场景(离线时返回 None);
  * `abs_path()`  —— 把 `//` 之类的相对路径按 .blend 位置展开。

`import bpy` 放在函数内部,所以本模块在**没有 Blender**时也能安全导入;
离线环境里 `scene()` 返回 None,调用方据此走"无场景"分支即可。

边界说明:这里只搬"取数据"的调用,不改任何行为 —— 每个函数就是把原来
那一句 bpy 访问原样挪过来。UI 面板(ui.py / operator_io.py)天然依赖
Blender,不强行迁移。
"""


def _bpy():
    """拿到 bpy 模块。放函数内 import,离线导入本模块不会失败。"""
    import bpy
    return bpy


def filepath():
    """当前 .blend 的绝对路径;没保存过时返回空串。"""
    return getattr(getattr(_bpy(), "data", None), "filepath", "") or ""


def scene():
    """当前场景;拿不到(无 bpy.context,如离线测试)时返回 None。"""
    return getattr(getattr(_bpy(), "context", None), "scene", None)


def addon_package(package):
    """由子模块的 `__package__` 推出**插件根模块名**。

    ── 为什么要有这个函数(GPT 审查 / 用户 2026-10-04)──────────────
    插件在 Blender 里可能以两种方式加载,根模块名完全不同:

      * 普通 addon      -> "BlenderGameKit"
      * 扩展(extension) -> "bl_ext.<仓库>.<扩展 id>"

    `__package__.split(".")[0]` 在扩展下会算成 `"bl_ext"` —— 既不是合法
    插件名,也匹配不上任何插件。这个错误以前在**两处**各犯了一次:

      * `AddonPreferences.bl_idname` —— 偏好设置类从来注册不上;
      * `preferences()` 里的 `context.preferences.addons.get(...)` ——
        偏好设置**永远读不到**,面板里改的 Godot 路径 / 运行前自动导出
        全都不生效(表现就是"改了设置没反应")。

    统一走这里,只算一次。约定:传入的是**插件根下一级**子模块的
    `__package__`(例如 `<根>.project`),退一级就是根。
    """
    text = str(package or "")
    if "." not in text:
        return text or "BlenderGameKit"
    return text.rsplit(".", 1)[0]


def abs_path(path):
    """按 Blender 规则展开路径(如 `//foo` 相对 .blend)。无 bpy 时原样返回。"""
    text = str(path or "")
    expander = getattr(getattr(_bpy(), "path", None), "abspath", None)
    if callable(expander):
        return expander(text)
    return text