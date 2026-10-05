"""路径工具(纯标准库,不依赖 bpy)。

── 为什么要有这个文件(GPT 审查 / 用户 2026-10-04)──────────────────
在拆分前,路径处理散落各处:一会儿 `os.path.join`,一会儿手工拼
`"res://" + name`,一会儿又拿用户给的字符串直接当目录用。三个后果:

  * `res://` 拼法不统一 —— 少一个斜杠、用了反斜杠,Godot 就**加载不到**
    资源,而且不报错,只在检查器里显示"缺失";
  * 用户随手填的目录会被直接创建/覆盖,拼错或指向系统目录时**误伤**
    别的项目(输出目录以前完全没有边界检查);
  * 想离线测这些逻辑时必须连 bpy 一起打桩。

这里把三件事固化成纯函数(不 import bpy,可单独单测):

  * `normalize_dir()`     —— 去掉 `~`、转绝对路径、规范化分隔符;
  * `to_res_path()`       —— 统一成 Godot 的 `res://` 形式;
  * `safe_join_project()` —— 在项目根内拼路径,越界(目录穿越)直接报错;
  * `validate_project_dir()` —— 检查一个目录能不能作为写入目标。
"""

import os


def normalize_dir(path):
    """把路径归一化成绝对、规范化的字符串。空路径返回空串。

    `~` 会展开,相对路径按当前工作目录变绝对,分隔符与 `..` 会被规范化。
    返回字符串而非 `Path`,是因为插件里到处与字符串互操作(bpy、Godot
    配置文本),保持类型一致能少一层转换、也少一处踩坑。
    """
    text = str(path or "").strip()
    if not text:
        return ""
    return os.path.normpath(os.path.abspath(os.path.expanduser(text)))


def to_res_path(path, root=None):
    """把路径转成 Godot 的 `res://` 形式。

    三种输入:
      * 已经是 `res://...`      -> 只规范化分隔符与多余斜杠;
      * 给了 `root` 且 `path` 是它内部的绝对路径 -> 转成相对 `root` 的路径;
      * 其它(项目内相对路径)    -> 直接补上 `res://` 前缀。

    统一走这里之后,不会再出现"反斜杠漏进 .tscn"这类只在 Godot 里才
    暴露、而且报错信息毫不相干的低级问题。
    """
    text = str(path or "").strip()
    if not text:
        return ""
    if text.startswith("res://"):
        return "res://" + text[6:].replace("\\", "/").lstrip("/")

    relative = text.replace("\\", "/").lstrip("/")
    if root and os.path.isabs(text):
        root_abs = normalize_dir(root)
        target = normalize_dir(text)
        # 用 normcase 只做"是否在 root 内"的判断(Windows 大小写不敏感),
        # 真正算相对路径时用原始大小写,否则相对路径会被整体小写化。
        root_key = os.path.normcase(root_abs)
        prefix = root_key if root_key.endswith(os.sep) else root_key + os.sep
        if os.path.normcase(target).startswith(prefix):
            relative = os.path.relpath(target, root_abs).replace("\\", "/")
    return "res://" + relative


def safe_join_project(root, *parts):
    """在项目根目录内拼接路径;结果越界时抛 `ValueError`。

    这是给"用用户数据(节点名、动作名…)拼文件名"的地方兜底:万一名子里
    带上了 `..\\`,也不能写到项目外面去。已存在的、正常的相对子路径不受
    影响,行为与 `os.path.join` 一致。
    """
    root_norm = normalize_dir(root)
    if not root_norm:
        raise ValueError("项目根目录为空,无法拼接路径")
    joined = os.path.normpath(os.path.join(root_norm, *[str(p) for p in parts]))
    root_key = os.path.normcase(root_norm)
    joined_key = os.path.normcase(joined)
    if joined_key != root_key and not joined_key.startswith(root_key + os.sep):
        raise ValueError(f"路径越过了项目根目录: {joined}")
    return joined


def validate_project_dir(directory):
    """检查一个目录能不能作为导出/写入目标。返回 (ok, 归一化路径或错误说明)。

    只做**边界**判断:非空、归一化、不能指向一个已存在的文件(那会让
    后续"建目录"直接失败,错误还发生在很远的地方)。是否存在、是不是
    Godot 项目交给调用方按自己的语义处理 —— 这里不替它下结论。
    """
    if not directory or not str(directory).strip():
        return False, "空路径"
    path = normalize_dir(directory)
    if os.path.exists(path) and not os.path.isdir(path):
        return False, f"路径指向的是一个文件,不是文件夹: {path}"
    return True, path