"""节点声明自检(纯标准库,不依赖 bpy)。

为什么需要它:节点是"声明式"的 —— 分类、引脚、`gk_input_props` 映射、
说明文字分散在几十个类里。少写一个属性不会报错,只是那张卡在运行时
悄悄退回默认值(项目里踩过多次)。这个模块把"能静态检查的约定"收在一处,
离线单测直接对所有已注册节点跑一遍,新加节点时不用靠人肉记忆。

检查的都是**真会出问题**的项:
  * `gk_input_props` 指向的引脚 / 属性必须存在(否则卡片值被静默忽略);
  * 引脚名不能为空、同一侧不能重名(按名字取引脚会拿错);
  * 分类必须在菜单顺序表里(否则节点不出现);
  * 标签唯一、说明非空(文档与搜索都靠它)。

它**不**检查"有没有代码生成处理器":动画 / AI / 环境这些图有各自的
发射器,按通用表检查会误报。
"""


def validate_node(cls):
    """检查单个节点类,返回问题列表(每条形如 ``"节点『X』: ..."``)。"""
    label = getattr(cls, "bl_label", "") or getattr(cls, "bl_idname", "?")
    issues = []
    try:
        instance = cls()
    except Exception as exc:  # noqa: BLE001 - 自检要能报出实例化失败
        return [f"节点『{label}』实例化失败: {exc}"]

    inputs = [socket.name for socket in instance.inputs]
    outputs = [socket.name for socket in instance.outputs]
    for side_name, names in (("输入", inputs), ("输出", outputs)):
        if any(not name for name in names):
            issues.append(f"节点『{label}』有空的{side_name}引脚名")
        duplicated = sorted({name for name in names if names.count(name) > 1})
        if duplicated:
            issues.append(f"节点『{label}』的{side_name}引脚重名: {duplicated}")

    mapping = getattr(instance, "gk_input_props", {}) or {}
    for pin, prop in mapping.items():
        if pin not in inputs:
            issues.append(
                f"节点『{label}』的 gk_input_props 指向不存在的引脚『{pin}』")
        if not hasattr(instance, prop):
            issues.append(
                f"节点『{label}』的 gk_input_props 指向不存在的属性『{prop}』")

    if not str(getattr(cls, "bl_label", "") or "").strip():
        issues.append(f"节点『{cls.__name__}』没有 bl_label")
    if not str(getattr(cls, "gk_doc", "") or "").strip():
        issues.append(f"节点『{label}』没有说明文字(gk_doc)")

    from .nodes import CATEGORY_ORDER
    category = getattr(cls, "gk_category", "")
    if category and category not in CATEGORY_ORDER:
        issues.append(f"节点『{label}』的分类『{category}』不在 CATEGORY_ORDER 里")
    return issues


def validate_all(classes=None):
    """检查所有已注册节点,返回 (问题列表, 检查数量)。"""
    from .node_base import node_type_registry

    if classes is None:
        classes = list(node_type_registry.values())
    issues = []
    labels = {}
    for cls in classes:
        issues.extend(validate_node(cls))
        label = getattr(cls, "bl_label", "")
        if label:
            labels.setdefault(label, []).append(cls.__name__)
    for label, owners in labels.items():
        if len(owners) > 1:
            issues.append(f"节点标签『{label}』重复: {owners}")
    return issues, len(classes)
