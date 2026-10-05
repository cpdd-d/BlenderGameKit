"""节点图 -> GDScript 4 代码生成(**包入口**)。

── 为什么从单文件变成包(GPT 审查 / 用户 2026-10-04)────────────────
原来是 `codegen.py` 一个 7200 行的文件,职责混在一起。后来先拆出
`validation.py` / `api.py` 两个壳,主体仍挤在 `core.py`(6300+ 行);
现在把主体也按领域拆开:

  * `literals.py`    —— 字面量 / 类型名 / 常量工具
  * `registry.py`    —— 装饰器 + 注册表 + 派发(expr / stmt / multi_output)
  * `easing.py`      —— 缓动档位与曲线资源(Curve / Curve3D,第十二轮)
  * `expressions.py` —— 非动画表达式发射器(数学 / 逻辑 / 输入 / 骨骼……)
  * `statements.py`  —— 非动画语句发射器(运动 / 属性 / 射线 / 流程控制……)
  * `animation.py`   —— 动画播放 / 状态机 / 动画树 / 动画变量
  * `camera.py`      —— 相机套件(切换 / 机位 / 弹簧臂 / 路径 / 跟随)
  * `services.py`    —— 音频 / 存档 / 屏幕 UI 与滤镜
  * `context.py`     —— `CodegenAbort` / `_Statements` / `GenContext`
  * `generator.py`   —— `Generator` 主体(扫描 -> 组装)
  * `validation.py`  —— 生成前后的静态校验(基类匹配、动画目标)
  * `api.py`         —— 对外接口(`generate` / `generate_result`)

── 名字合并:保持 `codegen.X` 的老写法不变 ──────────────────────────
外部(以及仓库里的单元测试/脚本)大量使用 `codegen.generate`、
`codegen.CodegenAbort`、`codegen.required_base_class`,甚至
`codegen._anim_tree_entry` 这样的**下划线私有名**。为了不改动这些调用方,
这里把各子模块的命名空间**合并**到包命名空间 —— 等价于"原来的模块表面"。
这样拆分对调用方是**零感知**的。

新代码可以直接 `from . import codegen` 照旧,也可以按领域
`from .codegen import camera` 精确引用。

── 关于注册表 ──────────────────────────────────────────────────────
`EXPR_HANDLERS` / `STMT_HANDLERS` / `MULTI_OUTPUT_FUNCS` 由 `registry.py`
定义,各发射器模块 `from .registry import ...` 共享**同一份 dict**;所以
必须保证发射器模块在 `registry` 之后被 import(见下方顺序与
`BlenderGameKit/__init__.py` 的 `_SUBMODULES`)。
"""

from . import (animation, api, camera, context, easing, expressions, generator,
               literals, registry, services, statements, validation)


def _merge_namespaces(*modules):
    """把子模块里所有非 dunder 名字并入包命名空间(含单下划线私有名)。"""
    for module in modules:
        for key, value in vars(module).items():
            if not key.startswith("__"):
                globals()[key] = value


# 按依赖顺序合并(被依赖者在前);`api` 放最后,保证 generate 等对外名字不被覆盖
_merge_namespaces(literals, registry, easing, statements, camera, expressions,
                  animation, services, context, generator, validation, api)

# 清掉合并用的临时名,避免污染 `codegen.` 表面
del _merge_namespaces