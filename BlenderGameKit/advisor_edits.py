"""AI 动作里"不碰 bpy"的那一半:名字解析、查重、分层布局、取值转换。

── 为什么单独一个模块(和 node_groups 同一个理由)──────────────────
这些逻辑全是**纯计算**:给一串名字 / 一条边表,算出"该放哪、重不重名、
这个值按什么类型转"。它们不摸 bpy,鸭子类型就够,所以能塞进离线 unittest
里直接跑 —— `advisor_tools` 做不到(那些动作函数要真的建节点 / 连线)。
把纯逻辑抠出来还有一层好处:第一百零七轮六个新动作里最容易写错、最值得
锁死的恰恰是这几段(分层算歪、查重漏掉、布尔解析崩),而它们都能在没有
Blender 的机器上秒级回归。

模块里**不 import bpy**(仓库硬规矩,很多轮都这么干的)。
"""

# 布局:一列(一层)的横向间距。够宽到两个普通卡片左右不打架(卡片宽约 160)。
COLUMN = 280.0
# 布局:同一层里每个节点占的纵向间距。普通卡片高度一百出头,140 留了余量。
ROW = 140.0


def parse_name_list(text):
    """把"逗号分隔的节点名"拆成去空、去重、保序的清单。

    ── 为什么中英文逗号 / 顿号都认 ─────────────────────────────────
    模型和用户都可能混着写(`准备,移动` / `准备，移动` / `准备、移动`)。
    只认半角逗号的话,后两种会整串当成一个节点名 -> 找不到 -> 白瞎一张
    卡片。所以先把各种分隔符统一成半角逗号再拆。
    """
    raw = str(text or "")
    for separator in ("，", "、", ";", "；"):
        raw = raw.replace(separator, ",")
    out = []
    for piece in raw.split(","):
        name = piece.strip()
        if name and name not in out:
            out.append(name)
    return out


def find_by_name(items, name):
    """按 `.name` 找对象(找不到返回 None)。

    ── 为什么按名字找、不按对象身份找 ───────────────────────────────
    Blender 每次读一个 `node` 都会**新造一个 Python 包装对象** ——
    `node is other` 恒为 False(见 node_groups.parented_to 那段踩坑记录)。
    节点名在一张图里是唯一的,拿名字当身份最稳,离线桩也一样是 `.name`。
    """
    wanted = str(name or "").strip()
    if not wanted:
        return None
    for item in items or ():
        if str(getattr(item, "name", "")) == wanted:
            return item
    return None


def unique_name(existing_names, desired):
    """查新名字能不能用。返回 (可用的新名 或 "", 冲突的旧名 或 "")。

    ── 为什么要**提前查重**(而不是让 Blender 自己加 .001)─────────────
    重名时 Blender 会把新名悄悄改成 `名字.001` 并返回成功 —— 用户看到的
    却是"线一头连着的节点名字没变、另一头却多了个后缀",以为改名没生效
    (而且 `.001` 这种名字写进卡片里没人看得懂)。所以这里先拒绝,
    让用户 / 模型换一个名字再提。
    """
    name = str(desired or "").strip()
    if not name:
        return "", ""
    for existing in existing_names or ():
        if str(existing) == name:
            return "", name
    return name, ""


# 认作"启动"的写法(布尔属性专用)。中英文都收 —— 用户可能在卡片上填中文。
_TRUE_WORDS = ("true", "1", "on", "yes", "是", "开", "真", "对")
_FALSE_WORDS = ("false", "0", "off", "no", "否", "关", "假", "错")


def coerce_value(current, raw):
    """把用户给的字符串,按**属性现在的类型**转过去。返回 (成功?, 值 或 说明)。

    ── 为什么按现有值的类型转,而不是猜 ─────────────────────────────
    卡片上只有字符串槽,可 bool / float / 字符串三种属性的合法输入完全不同:
    bool 得认 `true/是/1`,float 得能解析 `0.5`,枚举 / 字符串原样收。
    猜错就是给属性写了错的类型,Blender 当场抛异常。所以拿**属性当前的值**
    当标尺:它是什么类型就按那种类型解析,转不过去才报错(报错也给人话)。
    """
    if isinstance(current, bool):
        text = str(raw or "").strip().lower()
        if text in _TRUE_WORDS:
            return True, True
        if text in _FALSE_WORDS:
            return True, False
        return False, f"『{raw}』不是布尔值(填 true/false、是/否、1/0 都行)"
    if isinstance(current, int) and not isinstance(current, bool):
        try:
            return True, int(float(str(raw).strip()))
        except (TypeError, ValueError):
            return False, f"『{raw}』不是整数"
    if isinstance(current, float):
        try:
            return True, float(str(raw).strip())
        except (TypeError, ValueError):
            return False, f"『{raw}』不是数值"
    if isinstance(current, str):
        # 字符串 / 枚举都走这里:原样收下(枚举的非法项交给 Blender 拦,
        # 它自己的报错会列出可选值,比我们猜准)
        return True, str(raw)
    if isinstance(current, (list, tuple)):
        # 向量 / 颜色:逗号分隔的一组数,个数要和现在对上
        parts = [piece.strip() for piece in
                 str(raw).replace("，", ",").split(",")]
        try:
            numbers = [float(piece) for piece in parts]
        except (TypeError, ValueError):
            return False, f"『{raw}』不是一组数值(用逗号分隔,如 0,1,2)"
        if len(numbers) != len(current):
            return False, f"要 {len(current)} 个数,收到 {len(numbers)} 个"
        return True, numbers
    return False, f"这种属性(当前类型 {type(current).__name__})暂不支持改"


def available_labels(classes, limit=0):
    """所有节点类的**中文标签**(去重、保序)。给"可用标签"提示用。"""
    out = []
    for cls in classes or ():
        label = str(getattr(cls, "bl_label", "") or "").strip()
        if label and label not in out:
            out.append(label)
    return out[:limit] if limit else out


def resolve_node_type(token, classes):
    """把模型给的节点类型解析成节点类。返回 (类 或 None, 错误说明)。

    ── 两种写法都认(最稳)────────────────────────────────────────
    用户在卡片上 / 模型在参数里可能写 `移动_按方向`(标签,人看得懂),
    也可能写 `GKNodeMoveDirection`(内部名,代码里复制来的)。两种都匹配,
    省掉一次"明明有这个节点却说找不到"的挫败。
    找不到时回**几个可用标签** —— 模型能照着改,用户也知道能填什么;
    只回一句"没找到"的话,它下一轮大概率还编同一个名字。
    """
    wanted = str(token or "").strip()
    if not wanted:
        return None, "没给节点类型(填节点的中文标签,如『移动_按方向』)"
    for cls in classes or ():
        if str(getattr(cls, "bl_idname", "")) == wanted:
            return cls, ""
    for cls in classes or ():
        if str(getattr(cls, "bl_label", "")) == wanted:
            return cls, ""
    sample = "、".join(available_labels(classes, 10))
    return None, (f"没有叫『{wanted}』的节点类型。可以填节点的中文标签"
                  f"(如『移动_按方向』/『界面_按钮』)或内部名(如 GKNodePrint)。"
                  f"常见的比如:{sample}")


def layer_positions(names, edges, column=COLUMN, row=ROW):
    """按**连线的分层**算每个节点该摆哪。返回 {名字: (x, y)}。

    ── 算法(执行流从左上到右下)──────────────────────────────────
    分两步:
      1. **分层**:一条边 u→v 表示"v 在前一个之后",`v` 的层 = 所有前驱
         层号的最大值 + 1(最长路径分层)。同层的节点自然并排。
      2. **摆位**:层号 → x(越靠后越右),同层内按出现顺序从上往下堆
         (y 递减)。同层节点 y 各不相同 → **绝不重叠**。

    ── 为什么要处理环 ──────────────────────────────────────────────
    节点图允许有环(『重复执行』那种),Kahn 拓扑排序会剩下一批入度降不到 0
    的节点。这里把它们挑出来,从"已确定的最大层"往后**顺序铺开** ——
    既不会无限循环,也不会和别的节点挤在一格。

    ── 为什么只返回位置、不碰节点 ──────────────────────────────────
    这是这个动作"零风险"的底气:函数拿到的只是一串名字 + 边表,
    算完吐一串坐标,**根本改不到任何东西**。真正写回 `node.location`
    的是 advisor_tools 里那一小段,连线 / 拓扑一个字不碰。
    """
    order = []
    for name in names or ():
        text = str(name)
        if text not in order:
            order.append(text)
    known = set(order)
    adjacency = {name: [] for name in order}
    indegree = {name: 0 for name in order}
    for edge in edges or ():
        try:
            source, target = str(edge[0]), str(edge[1])
        except (TypeError, IndexError):
            continue
        if source not in known or target not in known or source == target:
            continue
        adjacency[source].append(target)
        indegree[target] += 1

    layer = {name: 0 for name in order}
    remaining = dict(indegree)
    queue = [name for name in order if remaining[name] == 0]
    while queue:
        node = queue.pop(0)
        for target in adjacency[node]:
            if layer[node] + 1 > layer[target]:
                layer[target] = layer[node] + 1
            remaining[target] -= 1
            if remaining[target] == 0:
                queue.append(target)
    # 环里剩下的节点:从"已确定的最大层"之后顺序铺开(见上面的说明)
    cyclic = [name for name in order if remaining[name] > 0]
    if cyclic:
        cursor = max(layer.values(), default=-1) + 1
        for name in cyclic:
            layer[name] = max(layer[name], cursor)
            cursor = layer[name] + 1

    # 同层按出现顺序堆叠 —— 用 order 保证结果稳定(测试才敢断言具体坐标)
    rows = {}
    for name in order:
        rows.setdefault(layer[name], []).append(name)
    positions = {}
    for level in sorted(rows):
        for index, name in enumerate(rows[level]):
            positions[name] = (level * column, -index * row)
    return positions


def non_overlapping_spot(positions, column=COLUMN, row=ROW):
    """给新建的节点挑一个**不和现有节点重叠**的落点。

    ── 为什么不是简单"放 (0,0)"────────────────────────────────────
    新节点要是落在别人身上,用户第一眼看到的是"两个卡片叠一块、连的线
    糊在一起" —— 以为建失败或者图坏了。这里在**网格**上扫,挑第一个
    和所有已有点都隔开的格子;判"隔开"用半格容差(点不必精确落在网格上,
    真节点的坐标往往是手拖出来的任意值)。
    """
    used = []
    for item in positions or ():
        try:
            used.append((float(item[0]), float(item[1])))
        except (TypeError, ValueError, IndexError):
            continue
    for col in range(0, 200):
        for row_index in range(0, 200):
            spot = (col * column, -row_index * row)
            blocked = any(abs(spot[0] - x) < column * 0.6
                          and abs(spot[1] - y) < row * 0.6
                          for x, y in used)
            if not blocked:
                return spot
    return (0.0, 0.0)                                     # 理论上到不了
