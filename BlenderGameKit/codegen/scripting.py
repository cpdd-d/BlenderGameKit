"""自定义脚本节点的代码生成(第一百五十一轮)。

三个节点共用生成器里的一段小助手:
  `_gk_script_load(host, path, key, start_method)`
  `_gk_script_stop(key, stop_method)`
  `_gk_script_call(key, method)`

用法与取舍见 nodes/_scripting.py 的节点说明。
"""

from .. import script_assets
from ..nodes.node_base import gd_string
from .registry import stmt_node


def _script_name(node):
    text = script_assets.node_script_text(node)
    return text.name if text is not None else ""


def _instance_key(node):
    """加载 / 停止 / 调用三处必须算出同一个键。"""
    name = _script_name(node)
    instance = str(getattr(node, "gk_instance", "") or "").strip()
    if instance:
        return script_assets.instance_key(instance)
    if name:
        return script_assets.instance_key(name)
    return ""


@stmt_node("GKNodeScriptLoad")
def _stmt_script_load(ctx, node, indent):
    name = _script_name(node)
    if not name:
        ctx.warn("『脚本_加载』没选脚本 —— 先在文本编辑器里写一个 GDScript,"
                 "再在节点上挑它")
        return
    key = _instance_key(node)
    if not key:
        ctx.warn("『脚本_加载』算不出实例名 —— 检查『脚本』/『实例名』")
        return
    start = str(getattr(node, "gk_start_method", "") or "").strip()
    ctx.need_script_helpers = True
    ctx.add_line(indent,
                 f"_gk_script_load(self, "
                 f"{gd_string(script_assets.res_path(name))}, "
                 f"{gd_string(key)}, {gd_string(start)})")


@stmt_node("GKNodeScriptStop")
def _stmt_script_stop(ctx, node, indent):
    key = _instance_key(node)
    if not key:
        ctx.warn("『脚本_停止』没选脚本 / 没填实例名 —— 不知道要停哪一个")
        return
    stop = str(getattr(node, "gk_stop_method", "") or "").strip()
    ctx.need_script_helpers = True
    ctx.add_line(indent,
                 f"_gk_script_stop({gd_string(key)}, {gd_string(stop)})")


@stmt_node("GKNodeScriptCall")
def _stmt_script_call(ctx, node, indent):
    key = _instance_key(node)
    if not key:
        ctx.warn("『脚本_调用』没选脚本 / 没填实例名 —— 不知道要调哪一个")
        return
    method = str(getattr(node, "gk_method", "") or "").strip()
    if not method:
        ctx.warn("『脚本_调用』没填『方法』—— 不会调用任何东西")
        return
    ctx.need_script_helpers = True
    ctx.add_line(indent,
                 f"_gk_script_call({gd_string(key)}, {gd_string(method)})")
