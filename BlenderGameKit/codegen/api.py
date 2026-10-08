"""代码生成的对外接口。

从原 `codegen.py` 末尾抽出来的一块(GPT 审查)。这里只有"怎么调生成器、
怎么把结果交出去",不包含生成逻辑本身(那在 `core.py`)。

── 关于模块级全局变量的保留 ─────────────────────────────────────
`last_used_actions` / `last_notes` 是历史包袱:老调用方在 `generate()`
之后回头读它们拿结果。有两个坑:生成抛异常时读到的是**上一次**的旧值、
两次生成交错会互相覆盖。现在推荐用 `generate_result()` 的返回值;
这两个全局变量仍然会被回写,只为兼容尚未迁移的脚本/测试。
"""

from ..models import CodegenResult, Diagnostic, render_warnings
from .context import CodegenAbort
from .generator import Generator
from .validation import (_check_animation_targets,
                         _check_base_class_supports_graph,
                         _check_display_switch)

# 上一次生成用到的输入动作 [(动作名, 捕获的键), ...] 与提示(兼容用)
last_used_actions = []
last_notes = []


def generate_result(tree, options=None):
    """生成 GDScript，返回一个 `CodegenResult`(**推荐入口**)。

    所有结果(代码 / 告警 / 提示 / 用到的输入动作)都在返回值里,
    调用方不再需要回头去读模块级全局变量。
    """
    if tree is None:
        raise CodegenAbort("没有可导出的节点树")
    generator = Generator(tree, options)
    code = generator.generate()
    # ── 收集告警:列表里可能是字符串,也可能是 Diagnostic(GPT 审查)──
    # 统一在这里分流:`warnings` 摊平成字符串(老接口 / UI / 测试照旧),
    # `diagnostics` 保留结构化对象(带 code / hint,便于归类与断言)。
    collected = list(generator.ctx.warnings)
    _check_base_class_supports_graph(tree, options or {}, collected)
    _check_animation_targets(tree, collected)
    _check_display_switch(tree, collected)
    warnings = render_warnings(collected)
    diagnostics = [item for item in collected if isinstance(item, Diagnostic)]
    # 登记本次用到的输入动作，供导出流程写进 project.godot
    # 同时把用户在『输入_按键』里**捕获的键**一起带上(动作名, 键)——
    # 他在 Blender 里按 W,Godot 里 W 就该真的能用,而不是又绑回默认的 D。
    # 提示(正常用法)单独给出去 —— 导出报告里展示,但不算告警
    result = CodegenResult(
        code=code,
        warnings=warnings,
        diagnostics=diagnostics,
        notes=list(generator.ctx.notes),
        input_actions=[
            (action, generator.ctx.action_keys.get(const, ""))
            for const, action in generator.ctx.input_actions.items()
        ],
        # 曲线资源(相机沿曲线运镜 / 时间轴曲线):导出流程据此写 .tres。
        # 内容在**生成时**就采样好了(见 curve_assets),这里只是转交。
        curve_assets=dict(getattr(generator.ctx, "curve_assets", {}) or {}),
    )
    # ── 兼容:仍然回写模块级全局变量 ────────────────────────────────
    # 仓库里还有脚本/单元测试在读 `codegen.last_used_actions`。保留写入
    # 以免一次改爆;但**新代码请用 generate_result() 的返回值**(GPT 审查)。
    #
    # 注意:必须**原地修改**(`[:] =`),不能重新赋值 —— 包入口
    # `codegen/__init__.py` 在导入时把这些列表对象按引用合并进了包命名空间,
    # 一旦 `globals()["last_used_actions"] = [...]` 换了新对象,
    # `codegen.last_used_actions` 就还指着旧的那个空列表(实测踩过)。
    last_used_actions[:] = list(result.input_actions)
    last_notes[:] = list(result.notes)
    return result


def generate(tree, options=None):
    """生成 GDScript，返回 (代码, 告警列表)。保留旧签名,内部走 generate_result。"""
    result = generate_result(tree, options)
    return result.code, result.warnings


def generate_to_dict(tree, options=None):
    result = generate_result(tree, options)
    return {"code": result.code, "warnings": result.warnings,
            "lines": result.code.count("\n") + 1}