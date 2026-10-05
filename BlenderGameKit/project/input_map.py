"""把节点图用到的输入动作写进 project.godot 的 [input] 段。

从 project.py 拆出(GPT 审查)。
"""

import os
import re



# ── 自动写入输入动作 ────────────────────────────────────────────────
# 生成的脚本里写的是 `const ACTION_MOVE_LEFT := "move_left"`，
# 而 Godot 里如果 project.godot 的 [input] 段没有这些动作，
# 运行时会刷一屏
#     ERROR: The InputMap action "move_left" doesn't exist.
# 按键完全没反应。用户的心智模型是"我在 Blender 里画完就能跑"，
# 所以这些动作必须自动补上。
INPUT_MARK = "; ---- 下面这些输入动作由 BlenderGameKit 从节点图自动生成 ----"
INPUT_END = "; ---- 自动生成的输入动作结束 ----"

# 动作名 -> 默认绑定的物理键值(Godot 的 Key 枚举值)。
# 没列在这里的动作会生成"存在但没绑键"的条目 —— 这样至少不报错，
# 用户可以在 Godot 的输入映射里自己绑。
DEFAULT_KEYS = {
    "move_left": 65,      # A
    "move_right": 68,     # D
    "move_up": 87,        # W
    "move_down": 83,      # S
    "jump": 32,           # 空格
    "attack": 74,         # J
    "interact": 69,       # E
    "pause": 4194305,     # Escape
    "ui_accept": 32,
    "ui_cancel": 4194305,
}


# Blender 的键名 -> Godot 的 Key 枚举值。
#
# ── 为什么需要这张表(用户 2026-10-03)────────────────────────────
# 用户在『输入_按键』里**按一下键**就绑好了,导出时要把那个键写进
# project.godot 的输入映射 —— 但 Blender 说的键名(`event.type`,比如
# `W` / `SPACE` / `LEFT_SHIFT`)和 Godot 的 keycode 是两套东西。
# 表里没有的键会退化成"动作存在但没绑键"(至少不报错,用户可以在
# Godot 的输入映射里自己补)。
KEY_TO_GODOT = {
    # 字母(A-Z 的 Godot keycode 就是 ASCII 大写)
    **{chr(code): code for code in range(65, 91)},
    # 数字(主键盘)
    **{str(digit): 48 + digit for digit in range(10)},
    "SPACE": 32,
    "ENTER": 4194309,
    "NUMPAD_ENTER": 4194310,
    "TAB": 4194306,
    "BACKSPACE": 4194308,
    "ESC": 4194305,
    "LEFT": 4194319,
    "RIGHT": 4194321,
    "UP": 4194320,
    "DOWN": 4194322,
    "LEFT_SHIFT": 4194325,
    "LEFT_CTRL": 4194326,
    "LEFT_ALT": 4194327,
    "RIGHT_SHIFT": 4194328,
    "RIGHT_CTRL": 4194329,
    "RIGHT_ALT": 4194330,
    "F1": 4194332, "F2": 4194333, "F3": 4194334, "F4": 4194335,
    "F5": 4194336, "F6": 4194337, "F7": 4194338, "F8": 4194339,
    "F9": 4194340, "F10": 4194341, "F11": 4194342, "F12": 4194343,
    "COMMA": 44, "PERIOD": 46, "SLASH": 47, "SEMI_COLON": 59,
    "MINUS": 45, "EQUAL": 61, "BACK_SLASH": 92,
    "LEFT_BRACKET": 91, "RIGHT_BRACKET": 93, "GRAVE": 96,
    "NUMPAD_0": 4194438, "NUMPAD_1": 4194439, "NUMPAD_2": 4194440,
    "NUMPAD_3": 4194441, "NUMPAD_4": 4194442, "NUMPAD_5": 4194443,
    "NUMPAD_6": 4194444, "NUMPAD_7": 4194445, "NUMPAD_8": 4194446,
    "NUMPAD_9": 4194447,
}

# Blender 和 Godot 叫法不一样的键
KEY_ALIASES = {
    "RETURN": "ENTER",
    "ESCAPE": "ESC",
}


def key_code_for(action, captured_key=""):
    """这个动作该绑哪个键:先看用户捕获的键,再退回默认表。

    返回 None 表示"没绑键"(动作仍然会写进映射表,只是空的)。
    """
    key = str(captured_key or "").strip().upper()
    if key:
        key = KEY_ALIASES.get(key, key)
        if key in KEY_TO_GODOT:
            return KEY_TO_GODOT[key]
    return DEFAULT_KEYS.get(action)


def _input_action_block(action, captured_key=""):
    """一个动作的 project.godot 片段。

    captured_key 是用户在 Blender 里**按一下键**捕获到的键名 ——
    他在 Blender 里按 W,Godot 里 W 就该真的能用。
    """
    key = key_code_for(action, captured_key)
    if key is None:
        return f'{action}={{\n"deadzone": 0.2,\n"events": []\n}}\n'
    event = (
        'Object(InputEventKey,"resource_local_to_scene":false,'
        '"resource_name":"","device":-1,"window_id":0,'
        '"alt_pressed":false,"shift_pressed":false,"ctrl_pressed":false,'
        '"meta_pressed":false,"pressed":false,"keycode":0,'
        f'"physical_keycode":{key},"key_label":0,"unicode":0,"location":0,'
        '"echo":false,"script":null)'
    )
    return (f'{action}={{\n"deadzone": 0.2,\n"events": [{event}\n]\n}}\n')


def set_input_actions(directory, actions, keys=None):
    """把节点图用到的输入动作写进 project.godot 的 [input] 段。

    返回实际写入的动作个数。

    actions 里可能混着两种东西:`"jump"` 这样的动作名,以及
    `("jump", "SPACE")` 这样的 (动作名, 捕获到的键) —— 后者是用户在
    『输入_按键』里**按一下键**绑的,导出时要真的写进映射表。
    keys 是 {动作名: 键名} 的补充表(给调用方按名字传)。

    ── 为什么用"标记块"而不是全量重写 [input] ────────────────────────
    用户很可能自己在 Godot 里配过输入映射(改手柄、加键位)。
    全量重写会把他的改动冲掉。所以只维护标记之间那一段:
    重新导出时删掉上一段、写入新的一段，标记之外的东西一律不碰。
    """
    if not directory or not actions:
        return 0

    project_file = os.path.join(directory, "project.godot")
    if not os.path.isfile(project_file):
        return 0

    keys = dict(keys or {})
    pairs = []
    for entry in actions:
        if isinstance(entry, (tuple, list)) and len(entry) == 2:
            pairs.append((str(entry[0]), str(entry[1] or "")))
        else:
            name = str(entry)
            pairs.append((name, keys.get(name, "")))
    wanted = [(name, key) for name, key in pairs if name and name.strip()]
    # ── ui_* 是 Godot 内置动作,不要自己重新定义 ──────────────────────
    # 生成器允许用户不填动作名,这时会退回 Godot 内置的 "ui_left" 之类。
    # 把这些写进 [input] 会用一份空的 events 覆盖掉内置绑定,
    # 属于"帮倒忙"。它们本来就在,不用我们管。
    wanted = [(name, key) for name, key in wanted
              if not name.startswith("ui_")]
    # 去重但保持顺序(同名动作只写一次;带上键的那份优先)
    seen = {}
    order = []
    for name, key in wanted:
        if name not in seen:
            seen[name] = key
            order.append(name)
        elif key and not seen[name]:
            seen[name] = key
    if not order:
        return 0

    with open(project_file, "r", encoding="utf-8") as handle:
        text = handle.read()

    # 1) 先去掉上一次自动生成的那一段
    pattern = re.compile(
        re.escape(INPUT_MARK) + r".*?" + re.escape(INPUT_END) + r"\n?",
        re.DOTALL,
    )
    cleaned = pattern.sub("", text)

    # 2) 组成新的一段
    block = [INPUT_MARK]
    for name in order:
        block.append(_input_action_block(name, seen[name]).rstrip("\n"))
    block.append(INPUT_END)
    block_text = "\n".join(block) + "\n"

    # 3) 放进 [input] 段。没有就新建一个(放在 [rendering] 之前，
    #    或者文件末尾)。
    if re.search(r"^\[input\]\s*$", cleaned, re.MULTILINE):
        cleaned = re.sub(r"(^\[input\]\s*$)", r"\1\n" + block_text, cleaned,
                         count=1, flags=re.MULTILINE)
    elif re.search(r"^\[rendering\]\s*$", cleaned, re.MULTILINE):
        cleaned = re.sub(r"(^\[rendering\]\s*$)", "[input]\n" + block_text + "\n\\1",
                         cleaned, count=1, flags=re.MULTILINE)
    else:
        if not cleaned.endswith("\n"):
            cleaned += "\n"
        cleaned += "\n[input]\n" + block_text

    if cleaned == text:
        return 0
    with open(project_file, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(cleaned)
    # ── 返回**真正写进 [input] 的动作数**(GPT 审查 / 用户 2026-10-04)──
    # 以前返回 len(wanted):那是"去重前、还没过滤 ui_* 之前"的长度。
    # 节点图里同一个动作被两处引用(比如两个『输入_按键』都绑 jump)时,
    # 日志会报"写入 6 个动作"而实际只写了 5 个 —— 对不上就很难排查。
    # 实际写进去的是去重后的 order,返回它。
    return len(order)
