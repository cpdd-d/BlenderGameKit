"""暂停期间 `_input` 的分组回归(用户实测 bug:暂停后鼠标还能转视角)。

跑法:
    & "D:\\blender-4.5.5-windows-x64\\blender.exe" -b --factory-startup `
        --python-exit-code 1 --python tests\\blender_pause_input.py
    (不加 --factory-startup 也行,但这条测试不依赖用户扩展仓库)

## 为什么要这条测试

用户实测:"在暂停游戏后鼠标还存在控制视角旋转的 BUG"。
根因:图里用到暂停 / 对话 / 选择时,`_ready` 会把宿主脚本设成
`process_mode = Node.PROCESS_MODE_ALWAYS`(否则暂停之后没人读 Esc,
"按 Esc 解冻"和"暂停里弹的对白"都会失去接收者)。ALWAYS 是**整个节点**的
属性 —— `_process` / `_physics_process` 还能在函数入口用
`if get_tree().paused: return` 挡住,`_input` 以前一直没挡,于是暂停期间
鼠标位移照样往 `_gk_mouse_delta` 里攒,视角跟着转。

修法:每个事件处理器登记时声明 `during_pause`(见
`codegen/context.py::register_input_handler`),`_input` 里按它分两组发射。

## 判据(判错方向会真的弄坏功能,所以逐条钉住)

**不许坏**:
  * 上帝摄像机(环境脚本,按 Tab)/ 自由飞行观察机在暂停(时停)里
    **照旧能转视角**;
  * 暂停菜单自己的交互照旧:点面板外面关面板 / 对话框 / 对话历史 /
    选择框 —— 这四个都是在"暂停的树上等玩家"的,挡掉就是死锁;
  * 解冻活口(`_gk_pause_watch` + `process_frame` 信号)不能被弄没;
  * 没有事件处理器时**不生成** `_input`(老行为)。

**必须修好**:
  * 『鼠标_移动量』的累积处理器 `_gk_mouse_motion_event` 被包进
    "当前没暂停"的守卫里(滚轮 / 触屏拖动同理)。
"""

import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import BlenderGameKit as addon  # noqa: E402
from BlenderGameKit import asset_export, codegen, presets  # noqa: E402

OPTIONS = {"dimension": "3", "base_class": "CharacterBody3D",
           "export_tunables": True}

# 暂停守卫的推荐写法(生成器里那一行;下面按缩进判"谁被挡住了")
GUARD = "if get_tree() == null or not get_tree().paused:"

FAILED = []


def check(name, ok, detail=""):
    print(("  [ok] " if ok else "  [FAIL] ") + name
          + (("  -- " + str(detail)) if (not ok and detail) else ""))
    if not ok:
        FAILED.append(name)


def functions(code):
    """把生成物切成 {函数名: 函数体}(和 test_codegen.Base.functions 同一套)。"""
    funcs = {}
    name = None
    body = []
    for line in code.splitlines():
        if line.startswith("func "):
            if name is not None:
                funcs[name] = "\n".join(body)
            name = line.split("(")[0][len("func "):]
            body = []
        elif name is not None:
            body.append(line)
    if name is not None:
        funcs[name] = "\n".join(body)
    return funcs


def input_body(code):
    return functions(code).get("_input", "")


def dispatch_lines(body):
    """`_input` 里的派发行 -> {函数名: 缩进}。"""
    out = {}
    for line in body.splitlines():
        text = line.strip()
        if not text.endswith("(event)"):
            continue
        name = text[:-len("(event)")]
        if not name or "(" in name or " " in name:
            continue
        out[name] = len(line) - len(line.lstrip(" "))
    return out


def guard_at(body):
    """暂停守卫那一行的缩进(没有守卫 -> None)。"""
    for line in body.splitlines():
        text = line.strip()
        if text.startswith("if ") and ".paused" in text:
            return len(line) - len(line.lstrip(" "))
    return None


def guarded_names(body):
    """被暂停守卫**挡住**的处理器名(缩进比守卫深 = 在守卫里面)。"""
    indent = guard_at(body)
    if indent is None:
        return set()
    return {name for name, depth in dispatch_lines(body).items()
            if depth > indent}


def unconditional_names(body):
    """无条件调用(没被守卫挡住)的处理器名。

    没有守卫 = 全都是无条件调用(这时调用行的缩进是函数体的 4 空格)。
    """
    indent = guard_at(body)
    calls = dispatch_lines(body)
    if indent is None:
        return set(calls)
    return {name for name, depth in calls.items() if depth <= indent}


def new_tree(name):
    tree = bpy.data.node_groups.new(name, "GKGameLogicTree")
    # 没用户的节点图存盘会被丢掉(和示例脚本同一个套路)
    tree.use_fake_user = True
    return tree


def link(tree, source, source_pin, target, target_pin):
    src = source.outputs.get(source_pin)
    dst = target.inputs.get(target_pin)
    assert src is not None, f"{source.name}.{source_pin} 不存在"
    assert dst is not None, f"{target.name}.{target_pin} 不存在"
    before = len(tree.links)
    tree.links.new(src, dst)
    assert len(tree.links) == before + 1, (
        f"连线被拒:{source.name}.{source_pin} -> {target.name}.{target_pin}"
        f"({src.bl_idname} -> {dst.bl_idname})")


def add_pause(tree, name="暂停热键", menu="暂停菜单"):
    """往图里挂一个『界面_暂停游戏』(勾 Esc)。"""
    ready = tree.nodes.new("GKNodeOnReady")
    ready.name = name + "触发"
    pause = tree.nodes.new("GKNodeUiPauseGame")
    pause.name = name
    pause.gk_pause = True
    pause.gk_hotkey = True
    pause.gk_menu = menu
    pause.gk_show_mouse = True
    link(tree, ready, "执行", pause, "执行")
    return pause


def main():
    addon.register()

    # ── ① 第三人称预设(带『鼠标_移动量』转视角)+ 暂停 ────────────────
    # 这就是用户那边的真实摆法:预设给的那条"鼠标 -> 偏航/俯仰"链
    # 走 `ensure_mouse_delta`,再挂一个 Esc 暂停。
    tree_a = new_tree("暂停鼠标回归")
    note = presets.build_logic_tree(tree_a)
    print(f"    预设图:{note}")
    add_pause(tree_a)
    code_a, warn_a = codegen.generate(tree_a, OPTIONS)
    body_a = input_body(code_a)
    check("① 生成了 _input(event)", "func _input(event: InputEvent) -> void:" in code_a,
          str(warn_a)[:200])
    check("① 『鼠标_移动量』的累积处理器被生成",
          "_gk_mouse_motion_event" in code_a, "")
    check("① 暂停守卫出现在 _input 里", GUARD in body_a, repr(body_a))
    check("① _gk_mouse_motion_event 的调用被守卫挡住(暂停后鼠标不再转视角)",
          "_gk_mouse_motion_event" in guarded_names(body_a),
          f"派发表:{dispatch_lines(body_a)}")
    check("① 滚轮累积也被守卫挡住",
          "_gk_mouse_wheel_event" in guarded_names(body_a),
          f"派发表:{dispatch_lines(body_a)}")
    check("① 预设有两根弹簧臂 / 鼠标链(确认这确实是那张图)",
          "_gk_mouse_delta" in code_a and "camera_spring_arm" in code_a, "")

    # 解冻活口不能丢:暂停是用 `get_tree().paused` 冻的,`_input` 停了之后
    # 唯一还能读到 Esc 的就是挂在 process_frame 信号上的 `_gk_pause_watch`。
    check("① process_mode = ALWAYS 还在(Esc 解冻路径的前提)",
          "process_mode = Node.PROCESS_MODE_ALWAYS" in code_a, "")
    check("① process_frame 上还挂着 _gk_pause_watch(解冻活口)",
          "process_frame.connect(_gk_pause_watch)" in code_a, "")
    watch = functions(code_a).get("_gk_pause_watch", "")
    check("① _gk_pause_watch 里还在读 Esc 并调 _gk_apply_pause",
          "KEY_ESCAPE" in watch and "_gk_apply_pause(not tree.paused" in watch,
          repr(watch))

    # ── ② 自由飞行观察机(『相机_取状态』造的兜底机)在暂停里照旧能转视角 ──
    # 上帝机那一族(环境脚本的 Tab 机 / 这里的兜底观察机)的设计目的就是
    # "停住画面绕着看细节",把它一起停掉 = "冻住之后连看都看不了"。
    tree_b = new_tree("上帝机暂停回归")
    presets.build_logic_tree(tree_b)
    ready_b = tree_b.nodes.new("GKNodeOnReady")
    ready_b.name = "取相机触发"
    status = tree_b.nodes.new("GKNodeCameraStatus")
    status.name = "取相机"
    printer = tree_b.nodes.new("GKNodePrint")
    printer.name = "打印相机名"
    link(tree_b, ready_b, "执行", printer, "执行")
    link(tree_b, status, "相机名", printer, "内容")
    add_pause(tree_b, name="暂停热键乙")
    code_b, warn_b = codegen.generate(tree_b, OPTIONS)
    body_b = input_body(code_b)
    dispatch_b = dispatch_lines(body_b)
    check("② 同一张图里既有兜底观察机、也有鼠标视角",
          "_gk_fly_mouse_event" in dispatch_b
          and "_gk_mouse_motion_event" in dispatch_b,
          f"派发表:{dispatch_b}")
    check("② _gk_fly_mouse_event 无条件调用(暂停 / 时停里还能转视角)",
          "_gk_fly_mouse_event" in unconditional_names(body_b),
          f"派发表:{dispatch_b}")
    check("② _gk_mouse_motion_event 仍然被挡住(分组确实生效)",
          "_gk_mouse_motion_event" in guarded_names(body_b),
          f"派发表:{dispatch_b}")
    check("② 兜底观察机整机都在(_gk_fly_update / _gk_ensure_fly_camera)",
          "_gk_fly_update" in code_b and "_gk_ensure_fly_camera" in code_b,
          str(warn_b)[:200])

    # ── ③ 暂停界面自己的交互:全都不能被守卫挡住 ────────────────────
    # 四个处理器都是"在暂停的树上等玩家"的:
    #   * 点面板外面关面板(暂停菜单 / 设置页就是弹在暂停里的);
    #   * 对话框 / 对话历史 / 选择框 —— `while await` 等点按,挡住就是死锁。
    # ⚠ 控件(『界面_面板』)现在只许摆在『游戏界面』设计图里(第一百一十二轮
    #   搬家),玩法图这边用『界面_套用设计』把它拉进来 —— 和 r37 冒烟同一条路。
    design_c = bpy.data.node_groups.new("暂停界面回归设计", "GKUiDesignTree")
    design_c.use_fake_user = True
    panel = design_c.nodes.new("GKNodeUiPanel")
    panel.name = "暂停面板"
    panel.gk_close_outside = True

    tree_c = new_tree("暂停界面回归")
    ready_c = tree_c.nodes.new("GKNodeOnReady")
    apply_c = tree_c.nodes.new("GKNodeUiApplyDesign")
    apply_c.gk_design = design_c.name
    dialog = tree_c.nodes.new("GKNodeUiDialog")
    dialog.name = "对白"
    dialog.gk_text = "暂停里也能点"
    history = tree_c.nodes.new("GKNodeDialogHistory")
    history.name = "回顾"
    choice = tree_c.nodes.new("GKNodeUiChoice")
    choice.name = "选择"
    choice.gk_question = "继续吗"
    link(tree_c, ready_c, "执行", apply_c, "执行")
    link(tree_c, apply_c, "完成", dialog, "执行")
    link(tree_c, dialog, "完成", history, "执行")
    link(tree_c, history, "完成", choice, "执行")
    code_c, warn_c = codegen.generate(tree_c, OPTIONS)
    body_c = input_body(code_c)
    dispatch_c = dispatch_lines(body_c)
    for handler, label in (("gk_ui_close_outside_input", "点外面关面板"),
                           ("gk_dialog_input_event", "对话框"),
                           ("gk_history_input_event", "对话历史"),
                           ("gk_choice_input_event", "选择框")):
        check(f"③ {label} 的处理器无条件调用(暂停里照旧能用)",
              handler in unconditional_names(body_c), f"派发表:{dispatch_c}")
    check("③ 四个处理器都在 _input 里(说明它们确实登记上了)",
          set(dispatch_c) == {"gk_ui_close_outside_input",
                              "gk_dialog_input_event",
                              "gk_history_input_event",
                              "gk_choice_input_event"}, f"派发表:{dispatch_c}")
    check("③ 全都允许暂停期间跑时,不必多包一层守卫",
          guard_at(body_c) is None, repr(body_c))
    check("③ 生成无告警", not warn_c, str(warn_c)[:300])

    # ── ④ 没有事件处理器 -> 不生成 _input(老行为) ──────────────────
    tree_d = new_tree("无输入回归")
    ready_d = tree_d.nodes.new("GKNodeOnReady")
    printer_d = tree_d.nodes.new("GKNodePrint")
    printer_d.gk_text = "只有打印"
    link(tree_d, ready_d, "执行", printer_d, "执行")
    code_d, _ = codegen.generate(tree_d, OPTIONS)
    check("④ 没有任何输入处理器时不生成 _input",
          "func _input(" not in code_d, "")

    # ── ⑤ 图里本来就有『每当_输入』(那段代码在 `_input` 里)时不能被搅乱 ──
    # 生成器是**往已有的 `_input` 里追加**派发行的,所以这条要盯:
    # 用户自己那段事件体还在不在、守卫有没有插错位置。
    tree_e = new_tree("每当输入回归")
    presets.build_logic_tree(tree_e)
    on_input = tree_e.nodes.new("GKNodeOnInput")
    on_input.name = "每当_输入"
    printer_e = tree_e.nodes.new("GKNodePrint")
    printer_e.name = "打印事件"
    printer_e.gk_text = "收到输入了"
    link(tree_e, on_input, "执行", printer_e, "执行")
    add_pause(tree_e, name="暂停热键丙")
    code_e, warn_e = codegen.generate(tree_e, OPTIONS)
    body_e = input_body(code_e)
    check("⑤ 用户『每当_输入』那段代码还在(没被分组逻辑挤掉)",
          "收到输入了" in body_e, repr(body_e))
    check("⑤ 守卫和派发行都加进了同一个 _input",
          GUARD in body_e
          and "_gk_mouse_motion_event" in guarded_names(body_e),
          repr(body_e))
    check("⑤ 缩进全合法(4 的倍数、没有制表符)",
          all("\t" not in line
              and (not line.strip()
                   or (len(line) - len(line.lstrip(" "))) % 4 == 0)
              for line in code_e.splitlines()), "")
    check("⑤ 生成无告警", not warn_e, str(warn_e)[:300])

    # ── ⑥ 上帝摄像机(环境脚本,按 Tab)在暂停 / 时停里仍然收鼠标 ──────
    # 它是 asset_export 里的 @tool 模板、PROCESS_MODE_ALWAYS,和游戏逻辑
    # 脚本分开 —— 这条交叉断言防的是"以后谁顺手给它也加个暂停守卫",
    # 那会让用户按 T 冻住之后连视角都转不了。
    #
    # ── 第 130 轮:判据从"字符串里有没有 .paused"改成"**代码行**里有没有" ──
    # 用户新报了一个 bug:"在上帝模式下按暂停游戏，视角旋转仍然受鼠标移动
    # 控制"。所以 `_input` 里现在**确实**有一个新判断(光标自由时不转视角),
    # 用来把"暂停菜单"(光标交给界面)**和"时停"(光标仍然捕获)**分开。
    # 但那个判断**不是** `.paused` —— 用 `.paused` 会把时停功能一起杀掉,
    # 那才是这条断言真正要防的事。
    # ⚠ 原来的写法是 `".paused" not in god_input` —— 太粗:改完之后
    #   **注释里**提到 `.paused` 都会把它弄红(实测:我就这么被拦了一次,
    #   但那是**假红**,不是真问题)。所以现在只扫**代码行**(去掉注释与
    #   空行),既保住原意、又不会被注释误伤。
    env = asset_export.ENVIRONMENT_SCRIPT
    env_lines = env.splitlines()
    start = next((i for i, line in enumerate(env_lines)
                  if line.startswith("func _input(")), None)
    check("⑥ 环境脚本里有上帝机的 _input", start is not None, "")
    if start is not None:
        end = next((i for i in range(start + 1, len(env_lines))
                    if env_lines[i].startswith("func ")), len(env_lines))
        god_input = "\n".join(env_lines[start:end])
        # 只留代码:去掉整行注释(# 开头的)和空行
        code_only = "\n".join(
            line for line in env_lines[start:end]
            if line.strip() and not line.lstrip().startswith("#"))
        check("⑥ 上帝机自己攒鼠标位移(_gk_god_look)",
              "_gk_god_look += event.relative" in god_input, god_input[:200])
        check("⑥ 上帝机的 _input 没有被暂停守卫挡住(时停里还能转视角)",
              ".paused" not in code_only, code_only[:200])
        # ── 第 130 轮新增:光标自由的判断必须在(它才是"暂停菜单不转视角")──
        # 判据用"光标模式"而不是"暂停标志":时停(按 T)也要暂停世界,
        # 但那时光标仍然捕获着、必须还能转。这两件事只能靠鼠标模式分开。
        check("⑥ 用『光标是否自由』区分暂停菜单 / 时停(第 130 轮)",
              "Input.mouse_mode" in code_only, code_only[:200])
        check("⑥ 光标自由时把攒下的位移清零(防止重捕获时视角猛跳)",
              "_gk_god_look = Vector2.ZERO" in code_only, code_only[:200])

    for tree in (tree_a, tree_b, tree_c, tree_d, tree_e, design_c):
        try:
            bpy.data.node_groups.remove(tree)
        except Exception:                                     # noqa: BLE001
            pass

    if FAILED:
        raise AssertionError(f"暂停输入回归失败 {len(FAILED)} 条:{FAILED}")
    print("PAUSE_INPUT=OK")


main()
