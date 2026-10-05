"""项目布局:目录推导、project.godot 骨架与标准子目录。

从 project.py 拆出(GPT 审查:缩小单文件职责)。
"""

import os
import re

# bpy 访问统一走适配层,路径归一化统一走 paths(GPT 审查:分离适配层 / 统一路径 API)
from .. import blender_adapter, paths
# 曲线资源目录名(相机沿曲线运镜 / 时间轴曲线)。名字定义在 curve_assets,
# 因为脚本里写死的 `res://curves/...` 就是它 —— 两处各写一份迟早对不上。
from ..curve_assets import CURVES_DIR  # noqa: F401  (借这里并入 project 命名空间)


# 项目根目录相对 .blend 的后缀
PROJECT_SUFFIX = "_godot"

# 项目里的固定子目录名。exporter / asset_export 从这里取名字,
# 免得两处各写一份字符串、改一处漏一处。
SCRIPTS_DIR = "scripts"
SCENES_DIR = "scenes"
ART_DIR = "art"
MATERIALS_DIR = "materials"
SHADERS_DIR = "shaders"

# ⚠ `curves/` 故意**不在** SUBDIRS 里:老项目没有这个目录,列进去的话
# 项目面板会一直显示"缺少子目录: curves"(用户什么都没做错却看到红色)。
# 它由导出流程按需创建(见 curve_assets.curve_files)。
SUBDIRS = (ART_DIR, SCRIPTS_DIR, SCENES_DIR, MATERIALS_DIR, SHADERS_DIR)

# Godot 工程的默认内容。只写最小可用的一份,
# 之后让 Godot 自己去补全其余设置。
_PROJECT_GODOT = """; 由 BlenderGameKit 自动生成
; 这个文件标记了"这是一个 Godot 项目",Godot 靠它认根目录。

config_version=5

[application]

config/name="{title}"
config/features=PackedStringArray("{version}")

[rendering]

renderer/rendering_method="{renderer}"
"""

# 项目自述,方便过一段时间回来看
_README = """# {title}

这个 Godot 项目由 BlenderGameKit 从 `{blend_name}` 生成。

**不要手工改 `scripts/` 里的文件** —— 它们由 Blender 里的节点图生成,
下次导出会覆盖。要改逻辑请在 Blender 里改节点图。

| 目录 | 内容 | 谁生成 |
|---|---|---|
| `scripts/` | GDScript 逻辑 | Blender 节点图 |
| `scenes/` | 场景装配(模型 + 脚本) | Blender 导出 |
| `art/` | 模型 `.glb` 与贴图 `.png` | Blender 导出 |
| `materials/` | 卡通材质 `.tres` | Blender NPR 预览面板 |
| `shaders/` | NPR 着色器 | 插件自带 |
| `curves/` | 你画的曲线(运镜轨道 / 时间轴) `.tres` | Blender 曲线物体(按需创建) |

`art/` 和 `materials/` 里**由你自己做的**东西不会被覆盖;
只有同名文件会被重写。
"""


def blend_dir():
    """当前 .blend 所在目录;没保存过就返回 None。"""
    path = blender_adapter.filepath()
    if not path:
        return None
    return os.path.dirname(os.path.abspath(path))


def blend_name():
    """当前 .blend 的文件名(不含扩展名);没保存过返回 None。"""
    path = blender_adapter.filepath()
    if not path:
        return None
    return os.path.splitext(os.path.basename(path))[0]


def safe_project_name(text, fallback="game"):
    """把 .blend 文件名转成安全的项目名。

    Godot 的 project.godot 里 config/name 可以放中文,但**目录名**用中文在
    跨平台(尤其是 Windows + 命令行工具)时容易出问题,所以统一压成
    字母数字下划线。中文名会被压成空,这时用回退名。
    """
    name = re.sub(r"[^0-9A-Za-z_\-]", "_", text or "").strip("_")
    name = re.sub(r"_{2,}", "_", name)
    if len(name) < 2 or name.lower() in {"con", "prn", "aux", "nul"}:
        return fallback
    return name


def derive_project_dir():
    """按 .blend 的位置推导项目根目录。没保存过 .blend 就返回 None。"""
    directory = blend_dir()
    name = blend_name()
    if directory is None or name is None:
        return None
    return os.path.join(directory, safe_project_name(name) + PROJECT_SUFFIX)


def resolve(scene=None, adopt=True):
    """当前应该往哪个目录导出。返回 (项目根目录, 来源说明);推不出来时 (None, 原因)。

    优先级:
      1. 场景属性里手动指定的 project_dir
      2. **.blend 所在目录(或它的上级)已经是一个 Godot 项目**
         -> 直接用那个项目,不再套一层子目录
      3. 由 .blend 位置推导出同名 _godot 子目录

    第 2 条是实测补上的。很多人会把 .blend 直接放进 Godot 项目里 ——
    这时再建一个 `xxx_godot` 子目录就变成了**项目套项目**,既别扭又容易搞混。
    既然外层已经是项目了,就认外层。

    adopt **默认开启**:认到的外层项目缺标准子目录(art/scripts/... 很常见,
    别人的项目不一定按我们的布局建)就顺手补齐。
    这么设计是安全的 —— create_project() 是纯增量的,只建缺的目录、
    只写不存在的文件,绝不覆盖任何已有内容。而缺目录会让所有导出失败,
    让用户自己去建 5 个目录是没有意义的摩擦。
    """
    scene = scene or blender_adapter.scene()
    settings = getattr(scene, "gk_settings", None) if scene is not None else None

    override = (getattr(settings, "project_dir", "") or "").strip() if settings else ""
    if override:
        target = paths.normalize_dir(blender_adapter.abs_path(override))
        if adopt and os.path.isdir(target):
            try:
                create_project(target)
            except OSError:
                pass
        return target, "手动指定"

    if not blender_adapter.filepath():
        return None, "这个 .blend 还没保存过。请先 Ctrl+S 存一次,项目会建在它旁边"

    enclosing = enclosing_project_dir()
    if enclosing:
        if adopt:
            # 缺子目录就补上。create_project() 是幂等的,不会覆盖已有内容。
            try:
                create_project(enclosing)
            except OSError:
                pass
        return enclosing, "Blender 文件在项目里"

    derived = derive_project_dir()
    if derived is None:
        return None, "推不出项目目录"
    return derived, "跟着 .blend"


def enclosing_project_dir():
    """如果 .blend 就在某个 Godot 项目里,返回那个项目的根目录。

    从 .blend 所在目录开始往上找(最多 4 层),遇到第一个含 project.godot
    的目录就认它。找不到返回 None。
    """
    current = blend_dir()
    if current is None:
        return None
    for _ in range(4):
        if os.path.isfile(os.path.join(current, "project.godot")):
            return current
        parent = os.path.dirname(current)
        if parent == current:      # 到盘符根了
            break
        current = parent
    return None


def describe_layout(directory):
    """给面板用的一句话说明当前是哪种布局。"""
    if not directory:
        return "未绑定"
    if enclosing_project_dir() == directory:
        return "Blender 文件在项目里"
    return "项目建在 .blend 旁边"


def is_project(directory):
    """这个目录是不是一个 Godot 项目(有 project.godot 就算)。"""
    if not directory:
        return False
    return os.path.isfile(os.path.join(directory, "project.godot"))


def missing_subdirs(directory):
    """返回还没建出来的子目录名。"""
    if not directory or not os.path.isdir(directory):
        return list(SUBDIRS)
    return [name for name in SUBDIRS
            if not os.path.isdir(os.path.join(directory, name))]


def create_project(directory, title=None, blend_file_name=None, renderer="forward_plus"):
    """建出完整的项目骨架。返回建出来的路径列表。

    幂等:已经存在的目录/文件**不覆盖**已有内容里属于用户的部分
    (project.godot 与 README 只在不存在时写;art/ 等目录里的文件一律不动)。
    """
    created = []
    os.makedirs(directory, exist_ok=True)

    title = title or os.path.basename(directory.rstrip("\\/"))
    # Godot 的 config/features 要写引擎版本
    try:
        # 延迟导入:godot 模块反向依赖 layout.is_project,模块级导入会成环
        from .godot import _godot_version_string

        version = _godot_version_string()
    except Exception:
        version = "4.4"

    project_file = os.path.join(directory, "project.godot")
    if not os.path.isfile(project_file):
        with open(project_file, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_PROJECT_GODOT.format(title=title, version=version,
                                              renderer=renderer))
        created.append(project_file)

    for name in SUBDIRS:
        path = os.path.join(directory, name)
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
            # Godot 不跟踪空目录,放个 .gitkeep 让目录能被版本管理留住
            keep = os.path.join(path, ".gitkeep")
            if not os.path.isfile(keep):
                open(keep, "w", encoding="utf-8").close()
            created.append(path)

    readme = os.path.join(directory, "README.md")
    if not os.path.isfile(readme):
        with open(readme, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_README.format(
                title=title,
                blend_name=blend_file_name or (blend_name() or "未保存的 .blend")))
        created.append(readme)

    return created
