"""Blender 文件 <-> Godot 项目的绑定。

设计目标
--------
**一个 .blend 文件对应一个 Godot 项目。** 导出目录不再是插件的全局设置,
而是从 .blend 文件的位置推出来的:

    D:/我的游戏/角色.blend
        -> 项目根目录  D:/我的游戏/角色_godot/
            ├── project.godot
            ├── scripts/    逻辑脚本(节点图生成)
            ├── scenes/     场景装配(挂好脚本的 .tscn)
            ├── art/        模型与贴图(.glb / .png)
            ├── materials/  卡通材质(.tres)
            └── shaders/    NPR 着色器(随插件复制)

这样三个好处:
  1. 不用每次导出都去填路径 —— 打开哪个 .blend 就知道该写哪
  2. 一个仓库里可以放多个 .blend,各自对应各自的 Godot 项目,互不干扰
  3. 交付时"Blender 工程 + Godot 工程"是一个整体,拷贝一个文件夹就够

为什么不直接把项目建在 .blend 旁边(同一个目录)
------------------------------------------------
不行,而且这是**必须避免**的:.blend 目录会变成 Godot 项目的 res:// 根,
于是 Godot 的 .godot/ 导入缓存和 .blend 备份文件(.blend1)全混在一起,
Godot 还会去扫描 .blend1 之类的垃圾文件。所以固定用**同名子目录**。
"""


# ── 包入口:按依赖顺序导入子模块,再把命名空间合并回来 ──────────────
# 外部一直用 `project.resolve()` / `project.set_input_actions()` 这类写法,
# 合并命名空间后**零感知**(与 codegen 包、nodes 门面同一套做法)。
# 依赖顺序:layout/windows 无依赖 -> godot 依赖两者 -> scene 依赖 layout+metadata。
from . import layout
from . import windows
from . import godot
from . import metadata
from . import input_map
from . import scene
from . import addon_prefs


def _merge_namespaces(*modules):
    """把子模块里所有非 dunder 名字并入包命名空间(含单下划线私有名)。"""
    for module in modules:
        for key, value in vars(module).items():
            if not key.startswith("__"):
                globals()[key] = value


_merge_namespaces(layout, windows, godot, metadata, input_map, scene,
                  addon_prefs)
del _merge_namespaces
