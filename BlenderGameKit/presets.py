"""预设:一键生成"能跑起来的角色逻辑"(用户 2026-10-04 第六条的中间那块)。

用户原话:"把现有 输入 / 移动 / 动画 / 相机 节点封装成'一键生成第三人称角色'
模板,不改底层节点"。

三条设计原则:
  1. **不改底层节点** —— 这里只负责"建节点 + 连线 + 填参数",导出的还是同一套
     节点生成的代码。建完之后就是一张**普通节点图**,想怎么改都行。
  2. **两张图**(照虚幻 5 的分工):玩法在**游戏逻辑树**、动作规则在**动画树**,
     逻辑树里只有一个『动画_状态机』引用节点 —— 图不会糊成一团。
  3. **相机物体不建**(用户确认:"只连图,相机我自己摆"):节点里按参数填相机名,
     场景里没有同名相机时运行时本来就有兜底告警(生成的 `_gk_ref` 里)。

参数默认值取自 `examples/demo_level` 里那套**已经跑通**的手感(速度 6 / 跳跃 7 /
灵敏度 0.25 / 臂长 5 / 高度 1.4 / 俯仰 12 / 俯瞰臂长 16 俯仰 80)。
"""

# 参数默认值。算子会把用户填的值覆盖进来(见 defaults()）。
DEFAULT_PARAMS = {
    # 角色物体名(挂脚本的目标)。留空 = 用当前选中的网格物体。
    "character": "",
    # 两个机位:C 切俯瞰 / V 切侧视,侧视那台是"弹簧臂的主人"
    "camera_side": "侧视机位",
    "camera_top": "俯瞰机位",
    # 手感
    "move_speed": 6.0,          # 米/秒(3D)
    "jump_force": 7.0,          # 起跳冲量
    "sensitivity": 0.25,        # 鼠标:每像素多少度
    "arm_length": 5.0,          # 弹簧臂长度
    "arm_height": 1.4,          # 杆子挂在角色上方多高
    "aim_pitch": 12.0,          # 起点俯仰(度)
    "top_arm_length": 16.0,     # 俯瞰机位的杆长
    "top_pitch": 80.0,          # 俯瞰机位的俯仰(接近垂直向下)
    # 动画(三段:待机 / 跑步 / 跳跃)。名字要和模型里的动作名对得上,
    # 对不上时 Godot 里会有一条可读的告警(不会崩)。
    "anim_idle": "待机",
    "anim_run": "跑步",
    "anim_jump": "跳跃",
    "with_animation": True,     # 要不要连动画树
    "with_ports": True,         # 要不要对外端口(位置 / 速度 / 在地面)
    "with_god_camera": True,    # 顺手把场景的『上帝摄像机』开关打开
    # 树的命名
    "logic_name": "角色逻辑(预设)",
    "anim_name": "角色动作(预设)",
    "class_name": "PlayerController",
}

# 角色控制用到的两个内部端口(鼠标累加进去,弹簧臂读它们)。
_LOOK_YAW = "视角偏航"
_LOOK_PITCH = "视角俯仰"


def defaults(**overrides):
    """拿一份完整参数:默认值 + 用户覆盖(空字符串 / None 不覆盖)。"""
    params = dict(DEFAULT_PARAMS)
    for key, value in (overrides or {}).items():
        if value is None or value == "":
            continue
        params[key] = value
    return params


# ==========================================================================
# 游戏逻辑树:第三人称角色
# ==========================================================================
def build_logic_tree(tree, params=None):
    """把一整套第三人称角色逻辑连进 `tree`(一棵游戏逻辑树)。

    结构(和 examples/demo_level 里那张跑通的图一致,去掉金币 / 关卡这些
    关卡专属的东西):

        每当_准备 ├─ 鼠标_模式(锁定) ─ 相机_设置(视野 60)
        (对外端口:视角偏航 / 视角俯仰 + 位置 / 速度 / 在地面)
        每当_物理帧 ├─ 输入_方向 → 移动_按方向(勾『随相机朝向』,重力模式)
                   ├─ 如果(在地面 且 刚按下空格)→ 移动_跳跃
                   ├─ 读偏航 → +鼠标x×灵敏度 → 写偏航 → 读俯仰 → …(夹 -20~70)
                   ├─ 弹簧臂(侧视机位,钉死)+ 弹簧臂(俯瞰机位,同偏航)
                   ├─ 如果(滚轮动了)→ 相机_缩放
                   ├─ 如果(按 C)→ 相机_切换(俯瞰) / 如果(按 V)→ 相机_切换(侧视)
                   └─ 对外端口:写位置 / 速度 / 在地面
        每当_准备 ─ 动画_状态机(引用动画树)          ← 可选

    返回:一句人话说明(给算子 report 用)。
    """
    p = defaults(**(params or {}))
    _declare_ports(tree, p)
    _build_ready(tree, p)
    _build_movement(tree, p)
    _build_camera(tree, p)
    _build_ports_writeback(tree, p)
    if p["with_animation"] and p.get("anim_tree_name"):
        _build_anim_ref(tree, p)
    return (f"{len(tree.nodes)} 个节点 / {len(tree.links)} 条连线"
            f"(相机名:{p['camera_side']} / {p['camera_top']})")


def _add(tree, kind, name, x, y, **props):
    """建一个节点(和 make_demo_level 里那个 add 一样,方便对照)。"""
    node = tree.nodes.new(kind)
    node.name = name
    node.location = (x, y)
    for key, value in props.items():
        setattr(node, key, value)
    return node


def _socket(sockets, name):
    """按名字取引脚。

    bpy 的引脚集合支持名称索引(`node.outputs["执行"]`),而**离线测试的桩**
    是普通列表 —— 这里两种都认,预设的代码就能被单测直接跑一遍。
    """
    try:
        return sockets[name]
    except TypeError:
        pass
    for socket in sockets:
        if getattr(socket, "name", "") == name:
            return socket
    raise KeyError(f"预设:没有引脚『{name}』")


def _link(tree, source_node, source_pin, target_node, target_pin):
    tree.links.new(_socket(source_node.outputs, source_pin),
                   _socket(target_node.inputs, target_pin))


def _declare_ports(tree, p):
    """声明对外端口 + 两个视角变量(端口 = 脚本里的 `输出_<名字>`)。"""
    ports = tree.gk_ports
    specs = [
        (_LOOK_YAW, "float", "0", "第三人称视角(鼠标累加)"),
        (_LOOK_PITCH, "float", "12", "第三人称视角(鼠标累加)"),
    ]
    if p["with_ports"]:
        # 位置拆成 X/Y/Z 三个浮点端口 —— 『取值_位置』本来就只给分开的
        # X/Y/Z(没有"合成向量"的节点),拆开比硬凑一个向量诚实。
        # 速度暂时没有端口:现在**没有读速度的节点**(动画树内部会读,
        # 但那是它自己的判断,玩法图读不到)—— 见 presets 顶部的说明。
        specs += [
            ("位置X", "float", "0.0", "角色世界坐标 X"),
            ("位置Y", "float", "0.0", "角色世界坐标 Y"),
            ("位置Z", "float", "0.0", "角色世界坐标 Z"),
            ("在地面", "bool", "false", "角色在地面上吗"),
        ]
    existing = {getattr(port, "port_name", "") for port in ports}
    for name, kind, initial, comment in specs:
        if name in existing:
            continue
        port = ports.add()
        port.port_name = name
        port.port_type = kind
        port.direction = "out"
        port.default_value = initial
        port.comment = comment


def _build_ready(tree, p):
    """每当_准备:锁鼠标 + 设初始视野 (+ 启动动画状态机)。"""
    ready = _add(tree, "GKNodeOnReady", "每当_准备", -1500.0, 500.0)
    lock = _add(tree, "GKNodeMouseMode", "锁定鼠标", -1200.0, 500.0,
                gk_mode="CAPTURED")
    _link(tree, ready, "执行", lock, "执行")
    # 开场只设**视野**:机位交给弹簧臂(它才是这台相机的主人,见下面的说明)
    setup = _add(tree, "GKNodeCameraSetup", "设定初始视野", -900.0, 500.0,
                 gk_target=p["camera_side"], gk_fov=60.0, gk_distance=0.0,
                 gk_smooth=0.0)
    _link(tree, lock, "完成", setup, "执行")
    if p["with_animation"] and p.get("anim_tree_name"):
        # 状态机从"准备"这条链后面启动 —— 和视野设置串在一条执行链上,
        # 顺序确定(先锁鼠标 → 视野 → 状态机)
        ref = _add(tree, "GKNodeAnimStateMachineRef", "动画_状态机",
                   -600.0, 500.0, gk_tree_name=p["anim_tree_name"])
        _link(tree, setup, "完成", ref, "执行")


def _build_movement(tree, p):
    """每当_物理帧:移动(跟着相机朝向)+ 跳跃。"""
    physics = _add(tree, "GKNodeOnPhysicsProcess", "每当_物理帧", -1500.0, 0.0)

    direction = _add(tree, "GKNodeInputDirection", "输入_方向", -1500.0, -260.0,
                     gk_up="W", gk_down="S", gk_left="A", gk_right="D",
                     gk_left_action="key_a", gk_right_action="key_d",
                     gk_up_action="key_w", gk_down_action="key_s")
    # ── 勾『随相机朝向』(踩过的坑)──────────────────────────────────
    # 不勾的话方向写死在世界轴上:鼠标转过视角后"按 W"还是往原来的方向走,
    # 方向感立刻错位。勾上 = 『方向』按**当前相机**的水平朝向变换。
    move = _add(tree, "GKNodeMoveDirection", "移动_按方向", -1100.0, 0.0,
                gk_speed=float(p["move_speed"]), gk_keep_y=True,
                gk_follow_camera=True,
                # ── 预设默认"推得动刚体"(第二十八轮)────────────────────
                # Godot 的运动学角色**不会**推刚体 —— 不勾的话"撞上箱子
                # 箱子纹丝不动",十个用户九个以为坏了。预设是"开箱即用"的
                # 一套,所以这里默认打开(老 .blend 里已建好的节点不受影响)。
                gk_push_bodies=True, gk_push_force=30.0)
    _link(tree, physics, "执行", move, "执行")
    _link(tree, direction, "方向", move, "方向")

    # 跳跃:在地面 且 刚按下空格
    on_floor = _add(tree, "GKNodeIsOnFloor", "在地面吗", -1100.0, -300.0)
    jump_key = _add(tree, "GKNodeKey", "空格", -1100.0, -520.0,
                    gk_key="SPACE", gk_action="jump", gk_trigger="JUST")
    both = _add(tree, "GKNodeLogic", "能跳吗", -860.0, -400.0, gk_op="AND")
    jump_branch = _add(tree, "GKNodeBranch", "如果_否则", -600.0, -400.0)
    do_jump = _add(tree, "GKNodeJump", "起跳", -320.0, -400.0,
                   gk_jump_force=float(p["jump_force"]))
    _link(tree, on_floor, "在地面", both, "A")
    _link(tree, jump_key, "结果", both, "B")
    _link(tree, both, "结果", jump_branch, "条件")
    _link(tree, physics, "执行", jump_branch, "执行")
    _link(tree, jump_branch, "是", do_jump, "执行")
    return physics


def _build_camera(tree, p):
    """相机套件:鼠标转视角(累加进两个变量)+ 两根弹簧臂 + 滚轮 + C/V 切换。"""
    physics = _tree_node(tree, "每当_物理帧")
    sensitivity = float(p["sensitivity"])

    # ---- 鼠标位移 → 偏航 / 俯仰增量 ----
    look = _add(tree, "GKNodeMouseMotion", "鼠标移动量", -1500.0, -800.0)
    split = _add(tree, "GKNodeVectorSplit", "拆成 XY", -1250.0, -800.0)
    _link(tree, look, "移动量", split, "向量")
    # 灵敏度当常量用:A 端接"灵敏度常量",B 端接鼠标位移(节点上那两个
    # 数字框都会被连线覆盖 —— 别在那儿改,改了不生效)
    delta = _add(tree, "GKNodeMath", "灵敏度", -1100.0, -700.0,
                 gk_op="MUL", gk_a=sensitivity, gk_b=1.0, gk_c=0.0)
    yaw_amount = _add(tree, "GKNodeMath", "左右灵敏度", -880.0, -760.0,
                      gk_op="MUL", gk_a=1.0, gk_b=1.0, gk_c=0.0)
    pitch_amount = _add(tree, "GKNodeMath", "上下灵敏度", -880.0, -900.0,
                        gk_op="MUL", gk_a=1.0, gk_b=1.0, gk_c=0.0)
    _link(tree, delta, "结果", yaw_amount, "A")
    _link(tree, split, "X", yaw_amount, "B")
    _link(tree, delta, "结果", pitch_amount, "A")
    _link(tree, split, "Y", pitch_amount, "B")

    yaw_read = _add(tree, "GKNodeGetVar", "读偏航", 200.0, -2200.0,
                    gk_var_name=_LOOK_YAW)
    yaw_add = _add(tree, "GKNodeMath", "偏航增量", 440.0, -2200.0,
                   gk_op="ADD", gk_a=0.0, gk_b=0.0, gk_c=0.0)
    yaw_write = _add(tree, "GKNodeSetVar", "写偏航", 680.0, -2200.0,
                     gk_var_name=_LOOK_YAW)
    pitch_read = _add(tree, "GKNodeGetVar", "读俯仰", 200.0, -2380.0,
                      gk_var_name=_LOOK_PITCH)
    pitch_add = _add(tree, "GKNodeMath", "俯仰增量", 440.0, -2380.0,
                     gk_op="ADD", gk_a=0.0, gk_b=0.0, gk_c=0.0)
    pitch_clamp = _add(tree, "GKNodeMath", "夹住俯仰", 680.0, -2560.0,
                       gk_op="CLAMP", gk_a=0.0, gk_b=-20.0, gk_c=70.0)
    pitch_write = _add(tree, "GKNodeSetVar", "写俯仰", 880.0, -2380.0,
                       gk_var_name=_LOOK_PITCH)
    _link(tree, physics, "执行", yaw_write, "执行")
    _link(tree, yaw_read, "值", yaw_add, "A")
    _link(tree, yaw_amount, "结果", yaw_add, "B")
    # ★ 这一根线不能漏:漏了『写偏航』的『值』引脚会退回节点默认值
    _link(tree, yaw_add, "结果", yaw_write, "值")
    _link(tree, yaw_write, "完成", pitch_write, "执行")
    _link(tree, pitch_read, "值", pitch_add, "A")
    _link(tree, pitch_amount, "结果", pitch_add, "B")
    _link(tree, pitch_add, "结果", pitch_clamp, "A")
    _link(tree, pitch_clamp, "结果", pitch_write, "值")

    # ---- 弹簧臂 ----
    # 相机的主人**只有一根杆子**,而且钉死在侧视机位上:留空(自动当前相机)的话,
    # 『相机_切换』把俯瞰机位设为当前之后,杆子下一帧又会把它夺回来当跟随相机 ——
    # 表现就是"切到俯瞰没反应 / 俯瞰也在跟着人跑"。
    arm = _add(tree, "GKNodeCameraSpringArm", "弹簧臂(第三人称)", 1180.0, -1900.0,
               gk_target="", gk_camera=p["camera_side"],
               gk_arm=float(p["arm_length"]), gk_height=float(p["arm_height"]),
               gk_pitch=float(p["aim_pitch"]), gk_side=0.0,
               gk_collision=True, gk_radius=0.35, gk_lag=8.0)
    _link(tree, yaw_read, "值", arm, "偏航")
    _link(tree, pitch_read, "值", arm, "俯仰")
    _link(tree, physics, "执行", arm, "执行")

    top_arm = _add(tree, "GKNodeCameraSpringArm", "弹簧臂(俯瞰)", 1180.0, -2560.0,
                   gk_target="", gk_camera=p["camera_top"],
                   gk_arm=float(p["top_arm_length"]), gk_height=0.0,
                   gk_pitch=float(p["top_pitch"]), gk_side=0.0,
                   gk_collision=False, gk_radius=0.35, gk_lag=8.0)
    _link(tree, yaw_read, "值", top_arm, "偏航")
    _link(tree, arm, "完成", top_arm, "执行")

    # ---- 滚轮缩放视野(20~90 度,0.15 秒平滑)----
    wheel = _add(tree, "GKNodeMouseWheel", "滚轮", -1500.0, -1640.0)
    wheel_step = _add(tree, "GKNodeMath", "滚轮转视野", -1250.0, -1640.0,
                      gk_op="MUL", gk_b=-4.0)
    status = _add(tree, "GKNodeCameraStatus", "当前视野", -1500.0, -1820.0)
    new_view = _add(tree, "GKNodeMath", "算出新视野", -1000.0, -1700.0,
                    gk_op="ADD")
    zero = _add(tree, "GKNodeConstFloat", "零", -1500.0, -1980.0, gk_value=0.0)
    moved = _add(tree, "GKNodeCompare", "滚轮动了吗", -1250.0, -1980.0,
                 gk_op="NE")
    wheel_branch = _add(tree, "GKNodeBranch", "如果_否则", -1000.0, -1980.0)
    zoom = _add(tree, "GKNodeCameraZoom", "改视野", -740.0, -1700.0,
                gk_time=0.15, gk_min_value=20.0, gk_max_value=90.0)
    _link(tree, wheel, "滚轮", wheel_step, "A")
    _link(tree, status, "视野", new_view, "A")
    _link(tree, wheel_step, "结果", new_view, "B")
    _link(tree, new_view, "结果", zoom, "数值")
    _link(tree, wheel, "滚轮", moved, "A")
    _link(tree, zero, "值", moved, "B")
    _link(tree, moved, "结果", wheel_branch, "条件")
    # 从**事件**单独拉一条执行线:挂在别的语句后面的话,只有"上一步刚好
    # 执行"时滚轮才生效(接口示例踩过这个坑)
    _link(tree, physics, "执行", wheel_branch, "执行")
    _link(tree, wheel_branch, "是", zoom, "执行")

    # ---- C / V 切换机位(硬切:弹簧臂每帧都在摆相机,过渡反而会打架)----
    for key, action, target, name in (
            ("C", "view_top", p["camera_top"], "切到俯瞰"),
            ("V", "view_side", p["camera_side"], "切回侧视")):
        key_node = _add(tree, "GKNodeKey", f"按 {key}", -1500.0, -2100.0
                        - (180.0 if key == "V" else 0.0),
                        gk_key=key, gk_action=action, gk_trigger="JUST")
        branch = _add(tree, "GKNodeBranch", "如果_否则", -1100.0, -2100.0
                      - (180.0 if key == "V" else 0.0))
        switch = _add(tree, "GKNodeCameraSetCurrent", name, -800.0,
                      -2100.0 - (180.0 if key == "V" else 0.0),
                      gk_target=target, gk_blend=0.0)
        _link(tree, physics, "执行", branch, "执行")
        _link(tree, key_node, "结果", branch, "条件")
        _link(tree, branch, "是", switch, "执行")


def _build_ports_writeback(tree, p):
    """每物理帧把 位置 / 速度 / 在地面 写进对外端口(别的图能读)。

    ── 为什么位置走『变量_设置』而不是『逻辑_输出结果』──────────────
    『逻辑_输出结果』的变量**固定声明成 bool**、值还会被 `bool(...)` 裹一层
    (见 codegen._stmt_logic_output)—— 三个坐标塞进去会变成 `bool(x)`。
    端口本质就是脚本变量(`输出_<名字>`),而『变量_设置』写同名会命中
    同一个变量(`ctx._var_aliases`),所以浮点走它、布尔仍走输出节点。
    """
    if not p["with_ports"]:
        return
    physics = _tree_node(tree, "每当_物理帧")
    # 位置拆成 X/Y/Z:『取值_位置』本来就只给分开的三个数,一个节点三个口
    reader = _add(tree, "GKNodeGetPosition", "取值_位置(端口)", -1500.0, -2800.0)
    # 事件节点的出口叫『执行』,语句节点的出口叫『完成』—— 链上第一环和
    # 后面几环的引脚名不一样(接错就是"没有引脚『执行』")
    previous, previous_pin = physics, "执行"
    for index, axis in enumerate(("X", "Y", "Z")):
        writer = _add(tree, "GKNodeSetVar", f"发布_位置{axis}",
                      -1300.0 + index * 300.0, -2800.0,
                      gk_var_name=f"位置{axis}")
        _link(tree, previous, previous_pin, writer, "执行")
        _link(tree, reader, axis, writer, "值")
        previous, previous_pin = writer, "完成"

    # 布尔端口:最直接,用『逻辑_输出结果』(它本来就是 bool)
    floor_reader = _add(tree, "GKNodeIsOnFloor", "在地面吗(端口)",
                        -400.0, -2800.0)
    publisher = _add(tree, "GKNodeLogicOutput", "发布_在地面",
                     -200.0, -2800.0, gk_output_name="在地面")
    _link(tree, previous, previous_pin, publisher, "执行")
    _link(tree, floor_reader, "在地面", publisher, "值")


def _build_anim_ref(tree, p):
    """逻辑树里那个『动画_状态机』引用节点(见 _build_ready 里的串联)。"""
    # 建在准备链上,所以这里什么都不用做 —— 留着这个函数是为了让
    # build_logic_tree 的流程一眼能看懂(将来要加"变量触发"再改这里)。
    return None


def _tree_node(tree, name):
    """按名字找已经建好的节点(找不到就抛 —— 说明连线的顺序被改了)。"""
    for node in tree.nodes:
        if node.name == name:
            return node
    raise KeyError(f"预设:找不到节点『{name}』")


# ==========================================================================
# 动画树:待机 / 跑步 / 跳跃
# ==========================================================================
def build_anim_tree(tree, params=None):
    """把三段动作(待机 / 跑步 / 跳跃)连进一棵动画树。

    结构(照虚幻 5 的动画状态机,也照 examples/demo_anim_tree 的写法):

        动画_开始 ──→ [待机] ──转到──→ 转场(在移动) ──→ [跑步]
                       │                                   │
                       └──→ 转场(已离地) ──→ [跳跃] ←──────┘
                                               │
                                转场(在地面) ──┘ → [待机]

    条件来自『动画判断_移动状态』(在地面 / 在移动 / 已离地 / 速度慢…),
    不用自己算 —— 它读的就是角色的速度/是否落地。
    """
    p = defaults(**(params or {}))
    import bpy

    loco = _add(tree, "GKNodeAnimLocomotion", "移动状态", -1100.0, -120.0,
                gk_speed_threshold=0.6, gk_fast_threshold=2.5)
    states = {}
    for index, (key, default_name) in enumerate((("anim_idle", p["anim_idle"]),
                                                 ("anim_run", p["anim_run"]),
                                                 ("anim_jump", p["anim_jump"]))):
        states[key] = _new_state(tree, str(p[key] or default_name),
                                 (0.0, -index * 220.0))

    start = _add(tree, "GKNodeAnimEntry", "动画开始", -1100.0, 320.0,
                 gk_start_mode="NOW")
    _link(tree, start, "开始", states["anim_idle"], "进入")

    _new_transition(tree, (-560.0, -60.0), states["anim_idle"], states["anim_run"],
                    loco, "在移动")
    _new_transition(tree, (-560.0, 160.0), states["anim_run"], states["anim_idle"],
                    loco, "速度慢")
    _new_transition(tree, (420.0, 180.0), states["anim_idle"], states["anim_jump"],
                    loco, "已离地")
    _new_transition(tree, (420.0, -60.0), states["anim_run"], states["anim_jump"],
                    loco, "已离地")
    _new_transition(tree, (1100.0, 60.0), states["anim_jump"], states["anim_idle"],
                    loco, "在地面")
    return (f"{len(states)} 个状态 / {len(tree.links)} 条连线"
            f"(待机={p['anim_idle']} 跑步={p['anim_run']} 跳跃={p['anim_jump']})")


def _new_state(tree, action_name, location):
    """一个动画状态。

    动作名能在 .blend 里找到同名动作就直接**指过去**(节点上能看到动作);
    找不到就只记状态名 —— 导出后 Godot 按名字找动作,找不到会有一条
    可读告警(不会崩),用户把状态名改成自己模型里的动作名即可。
    """
    import bpy

    node = tree.nodes.new("GKNodeAnimState")
    node.name = f"状态_{action_name}"
    node.location = location
    try:
        action = bpy.data.actions.get(action_name)
    except Exception:                                     # noqa: BLE001
        # 离线桩里 bpy.data 是简化的(没有 actions)—— 真 Blender 里永远有
        action = None
    if action is not None:
        node.gk_anim_action = action
    else:
        node.gk_state_name = action_name
    return node


def _new_transition(tree, location, from_state, to_state, cond_node, cond_pin):
    """接一条转场:上游 ← 状态的『转到』,转到 → 下一个状态的『进入』。

    『进入』有多个(『入口数量』可调):**一个输入引脚只允许一条线**,
    所以先找空的;都满了就把入口数量调大(重建引脚会保住原有连线)。
    """
    trans = tree.nodes.new("GKNodeAnimTransition")
    trans.location = location
    trans.gk_cond_source = "PIN"
    _link(tree, from_state, "转到", trans, "上游")
    inlet = None
    for socket in to_state.inputs:
        if not socket.is_linked:
            inlet = socket
            break
    if inlet is None:
        to_state.gk_in_count = min(8, len(to_state.inputs) + 1)
        to_state.rebuild_sockets()
        inlet = to_state.inputs[-1]
    tree.links.new(_socket(trans.outputs, "转到"), inlet)
    _link(tree, cond_node, cond_pin, trans, "条件")
    return trans


# ==========================================================================
# 一次性把两张图 + 场景/物体设置都弄好(算子调这个)
# ==========================================================================
def create_character_preset(scene, character=None, **overrides):
    """建两张图 + 配好场景和物体设置。返回 (逻辑树, 动画树, 说明列表)。

    ── 为什么连场景设置一起配(chu: 少踩坑)──────────────────────────
    只建图不配设置的话,新用户按 F5 大概率"角色不动":要么物体不是
    CharacterBody3D(生成的角色移动代码需要它),要么没勾『脚本挂载目标』
    (导出的 .tscn 里根本不会挂脚本)。这两条都在这里顺手配上。
    """
    import bpy

    from . import exporter

    params = defaults(**overrides)
    notes = []

    # 1) 角色物体(留空 = 选中的网格 / 已经挂了脚本的物体)
    if character is None:
        character = resolve_character(scene, str(params.get("character", "")))

    # 2) 动画树(放前面:逻辑树里的引用节点要填它的名字)
    anim_tree = None
    if params["with_animation"]:
        anim_tree = bpy.data.node_groups.new(str(params["anim_name"]),
                                            "GKAnimationTree")
        # 没有用户引用它的节点树,**存盘时会被直接丢掉**(踩过的坑)
        anim_tree.use_fake_user = True
        notes.append("动画树:" + build_anim_tree(anim_tree, params))
        params["anim_tree_name"] = anim_tree.name

    # 3) 逻辑树
    logic_tree = bpy.data.node_groups.new(str(params["logic_name"]),
                                         "GKGameLogicTree")
    logic_tree.gk_scope = "ROLE"
    logic_tree.use_fake_user = True
    notes.append("逻辑树:" + build_logic_tree(logic_tree, params))

    # 4) 场景设置:尺寸 / 基类 / 类名 / 用哪张图 / 上帝摄像机
    settings = getattr(scene, "gk_settings", None)
    if settings is not None:
        def _set(attr, value):
            if hasattr(settings, attr):
                try:
                    setattr(settings, attr, value)
                except Exception:                         # noqa: BLE001
                    pass

        _set("dimension", "3")
        _set("base_class", "CharacterBody3D")
        _set("class_name", str(params["class_name"]))
        if params["with_god_camera"]:
            _set("god_camera", True)
        notes.append("角色逻辑图 = " + logic_tree.name)

    # 5) 物体设置:物理类型 + 挂脚本
    if character is not None:
        physics = getattr(character, "gk_physics", None)
        if physics is not None:
            for attr, value in (("enabled", True), ("dimension", "3"),
                                ("body_type", "CharacterBody3D"),
                                ("script_target", True),
                                ("logic_tree", logic_tree.name)):
                if hasattr(physics, attr):
                    try:
                        setattr(physics, attr, value)
                    except Exception:                     # noqa: BLE001
                        pass
            notes.append(f"角色『{character.name}』:CharacterBody3D + 挂脚本 ✓")
        else:
            notes.append(f"⚠ 『{character.name}』没有『游戏物理』属性,"
                         f"请手动勾『脚本挂载目标』")
    else:
        notes.append("⚠ 没找到角色物体 —— 请手动勾『脚本挂载目标』,"
                     "并在『脚本挂载目标』里选好角色")
    return logic_tree, anim_tree, notes


def resolve_character(scene, wanted=""):
    """找出"要挂脚本的角色"物体。

    给了名字就用它(名字错了返回 None,由调用方提示);没给就:
      1. 当前选中的网格物体
      2. 已经勾了『脚本挂载目标』的物体
    """
    import bpy

    if wanted:
        return bpy.data.objects.get(str(wanted))
    for obj in getattr(bpy.context, "selected_objects", []) or []:
        if getattr(obj, "type", "") == "MESH":
            return obj
    for obj in getattr(scene, "objects", []) or []:
        physics = getattr(obj, "gk_physics", None)
        if physics is not None and getattr(physics, "script_target", False):
            return obj
    return None
