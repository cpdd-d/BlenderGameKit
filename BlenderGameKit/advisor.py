"""『项目顾问』—— 把"项目现在什么状况"整理好,交给 AI 帮你想办法。

── 它解决什么 ────────────────────────────────────────────────────
用户的原话:"是否可以在侧边栏添加 AI 顾问,在遇到困难时,可让 AI 顾问分析
项目情况,并给出解决方法?"

这个功能的重点**不在调模型,而在"上下文"** —— 一个不知道 BlenderGameKit
长什么样的模型,你问它"我的角色不动",它只能给一堆泛泛而谈。所以这里做的是:
把插件**自己已经攒下的**诊断数据(插件版本 / 物体与碰撞尺寸 / 节点图与连线 /
生成的 GDScript / 导出告警 / 最近日志 / Godot 与打包状况)拼成一份快照,
再连同用户的问题一起发给模型。

── 为什么复用 diagnostics ─────────────────────────────────────────
`diagnostics.build_report()` 本来就是"出问题时把全部信息打成一份纯文本" ——
和这里要的东西几乎完全重合。再写一份"顾问专用收集"只会让两处慢慢走偏,
所以直接复用,只额外补上日志 / 导出报告 / Godot 与打包状况。

── 协议:OpenAI 兼容 ─────────────────────────────────────────────
走 `POST <base_url>/v1/chat/completions`(标准 OpenAI 协议)。这样做的好处是
**一套代码通吃** DeepSeek / 通义 / 硅基流动 / 本地 Ollama —— 它们全都实现了
这个协议,不用为每家写适配。

── 联网与隐私 ────────────────────────────────────────────────────
这是本插件**唯一的联网功能**,所以格外保守:
  * 不填 API 地址就**一个字节都不发**(默认留空);
  * 发送前 `redact()` 会把 API Key 从文本里抹掉、把绝对路径换成占位符;
  * 不做任何后台 / 自动调用 —— 只有用户点『问 AI』才发。

纯标准库模块(不 import bpy),所以"拼请求 / 解析响应 / 脱敏 / 换行"这些
纯函数都能离线测。
"""

import json
import threading
import time
import unicodedata
import urllib.error
import urllib.request


# 给模型的角色说明。必须写清"这个插件是什么" —— 否则模型会把节点图当成
# Godot 的 VisualScript,给出完全用不上的建议。
DEFAULT_SYSTEM_PROMPT = """你是 BlenderGameKit 的项目顾问。

## 这个插件是什么
BlenderGameKit 让人**在 Blender 里**用中文节点图写游戏逻辑,导出成 Godot 4
的 GDScript 后在 Godot 里运行。关键概念:
* **节点图**:在 Blender 节点编辑器里画的中文逻辑图。分几类(游戏逻辑 /
  动画 / 界面设计 / 游戏AI / 游戏显示 / 游戏资产),用不同的树类型区分。
* **执行线 vs 值线**:金色『执行』线决定先后顺序,灰色线只传值。一条执行
  引脚只能接一条线(想从多处触发要用变量或分支)。
* **脚本挂载目标**:物体在『游戏物理 (BlenderGameKit)』面板里勾『脚本挂载
  目标』,导出的场景里那个物体才会挂上脚本。**不勾 = 角色不会动,而且导出
  不报错**,只给一条告警 —— 这是最常见的"我明明连了线却不动"。
* **一个 .blend 一个 Godot 项目**:项目建在 .blend 旁边,目录名 = 文件名 +
  `_godot`,里面是 scripts / scenes / art / materials / shaders。
* **导出**:侧栏『导出为 Godot 脚本』『导出模型到 Godot』;『直接运行游戏
  (F5)』;『打包成可执行文件』出 exe(需要 Godot 的导出模板)。
* 生成的产物是**标准 GDScript**,可以在 Godot 里直接读和改。

## 你怎么回答
1. **用中文**回答。
2. 先给**最可能的原因**(按可能性从高到低排),再说**怎么验证它** ——
   用户要的是"下一步怎么做",不是一篇科普。
3. 给**具体操作**:点哪个面板、哪个按钮、改哪个属性。不要只说"检查你的
   连线和设置"这种废话。
4. 用户的问题也可能出在 Godot 那边(比如导出模板没装、主场景没设),那就
   写清在 Godot 里点哪里。
5. **不要编造**插件里不存在的功能或节点名。快照里没有的信息就说"需要你
   确认一下…",不要猜。
6. 能一两句说清就别写五段。用户是在干活,不是在读文章。
7. **用纯文本,不要 Markdown 标记** —— 显示环境不渲染 `**加粗**`、`# 标题`、
   反引号代码块,打出来就是一堆星号。要分点就用 `1. 2. 3.` 或 `- `,
   要强调就直接把话说清楚。

## 你可以用工具自己去看项目
系统消息末尾那份"项目诊断快照"是**开聊那一刻**拍的,可能已经过时。
需要更准的信息时,**直接调用工具**去看,别让用户自己去翻:
* `read_settings` 读本场景的导出设置;**`list_trees` / `list_nodes`** 看节点图;
* `check_export_errors` 看上次导出的报错;**`get_godot_status`** 看 Godot
  与导出模板;**`diagnose`** 跑一遍完整诊断(输出较长,只在需要时调)。
先想清楚要哪个,一次调对,别把工具挨个试一遍。

## 想改项目时:提议,不要假装改了
你**没有**直接修改项目的能力。要改什么就调用 `propose_action`,把动作和
参数说清楚 —— 侧栏会给用户一张**待确认卡片**,他点了才会真执行。
所以:
* **绝对不要**说"我已经帮你改好了""已经把勾选上了" —— 那是撒谎;
* 应该说"我建议这样改,点确认就生效";
* `propose_action` 只能用动作清单里列出的动作名,别自己编。"""


# 对话历史最多带这么多条发给模型(一问一答算两条)。不限的话越聊越慢越贵,
# 而且老消息里那些"当时的项目快照"早就过期了,留着只会误导。
MAX_HISTORY = 12

# 对话记录写进这个 Blender 文本块 —— 用户可以在文本编辑器里滚动 / 选择 /
# 复制 / 搜索。用固定名字,下次打开同一个 .blend 还能接着看。
DIALOG_TEXT_NAME = "GK 对话记录"

# 对话历史的持久化键(存在场景自定义属性里,和 gk_last_export 同一套做法)
HISTORY_KEY = "gk_advisor_history"

# 会话内的对话历史:[{"role": "user"|"assistant", "content": str}]
HISTORY = []


# ---------------------------------------------------------------------------
# 纯函数:请求拼装 / 响应解析
# ---------------------------------------------------------------------------
def chat_url(base_url):
    """把用户填的地址补成完整的 chat/completions 地址。

    用户可能填这三种,都得能用:
        https://api.deepseek.com            -> .../v1/chat/completions
        https://api.deepseek.com/v1         -> .../v1/chat/completions
        http://localhost:11434/v1/chat/completions  -> 原样
    """
    text = (base_url or "").strip().rstrip("/")
    if not text:
        return ""
    if text.endswith("/chat/completions"):
        return text
    if text.endswith("/v1"):
        return text + "/chat/completions"
    return text + "/v1/chat/completions"


def compose_text(question, context_text):
    """把问题 + 快照拼成一段纯文本。

    单独抽出来是因为它有**两个用途**:发给 API(塞进 user 消息),
    以及『复制诊断』按钮直接复制走(不联网的兜底)。两处必须一模一样 ——
    否则用户拿到的和 AI 拿到的不是同一份,出问题没法复现。
    """
    return (
        "我在 Blender 里用 BlenderGameKit 做游戏时卡住了。下面先是我的问题,"
        "然后是一份项目诊断快照(插件自动收集的),请据此分析。\n\n"
        f"## 我的问题\n{(question or '').strip()}\n\n"
        f"## 项目诊断快照\n{context_text or '(空)'}\n"
    )


def build_messages(question, context_text, history=None, system_prompt=None):
    """拼出 OpenAI 协议的 messages(带多轮历史)。

    ── 项目快照放**系统消息**里,不放用户消息里(多轮对话的关键)──────
    一问一答都往用户消息里塞一份快照的话,聊到第五轮就有五份快照,
    又慢又贵,而且前四份**早就过期了** —— 模型会拿着"当时的节点图"回答
    现在的问题。放进系统消息就永远是**刚取的那一份**,历史消息则保持干净
    的纯对话。代价是历史里 AI 说的"快照【2】写着…"要读者自己对应,
    但换来的正确性值得。
    """
    system = system_prompt or DEFAULT_SYSTEM_PROMPT
    # ── 把**动作清单**列给模型(否则它会自己编动作名)────────────────
    # 系统提示里说了"只能用清单里的动作",可清单本身没给 —— 模型只能猜,
    # 猜错就被 propose_action 的校验挡回来,白烧一轮。这里把清单摊开。
    lines = _action_lines()
    if lines:
        system = f"{system}\n\n## 可用的动作(propose_action 只能用这些)\n{lines}"
    if context_text:
        system = f"{system}\n\n## 当前项目诊断快照\n{context_text}"
    messages = [{"role": "system", "content": system}]
    for item in list(history or [])[-MAX_HISTORY:]:
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": (question or "").strip()})
    return messages


def _action_lines():
    """动作清单的纯文本(喂给模型)。取不到工具模块时返回空串。"""
    try:
        from . import advisor_tools

        lines = []
        for name in advisor_tools.action_names():
            needs = advisor_tools.ACTIONS[name]["needs"]
            need_text = ("需要参数:" + "/".join(needs)) if needs else "不需要参数"
            lines.append(f"* {name}({need_text})—— {advisor_tools.action_hint(name)}")
        return "\n".join(lines)
    except Exception:                                     # noqa: BLE001
        return ""


def plainify(text):
    """把模型爱用的 Markdown 标记抹成纯文本。

    模型被训练成"回答要好看",所以张口就是 `**重点**`、`# 标题`、反引号。
    可显示环境(Blender 文本编辑器 / 侧栏 label)**不渲染**这些东西 ——
    用户看到的是一堆星号,截图里就是这样。
    第一道防线是系统提示里明说"用纯文本";这里是第二道,兜住不听劝的。

    ⚠ `__` 故意**不抹**:代码里的 `__init__` 会被误伤成 `init` 吗 ——
    会。而且中文回答里 `__` 当加粗用的极少,收益远小于风险。
    """
    out = text or ""
    out = out.replace("`", "")
    lines = []
    for line in out.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            # `## 小节` -> `■ 小节`(比一排井号干净,又保留了分隔感)
            line = "■ " + stripped.lstrip("#").strip()
            lines.append(line)
            continue
        # `**` 只在**成对出现**时抹掉 —— 单个 `**` 可能是代码里的幂运算
        # (`2 ** 3`),抹了就把代码改错了。
        if line.count("**") >= 2 and line.count("**") % 2 == 0:
            line = line.replace("**", "")
        lines.append(line)
    return "\n".join(lines)


def request_payload(model, messages, temperature=0.2, tools=None):
    """OpenAI 兼容的请求体。stream=False:我们要一次拿完,不做流式。

    ── 为什么用原生 `tools` 参数,不靠解析自然语言(顾问的建议)──────
    另一种做法是"在提示里让模型说『我要调用 xxx』,再拿正则去抠"。那条路
    **各家模型遵守程度天差地别**,换个模型就失灵。而 `tools` 是 OpenAI 的
    标准字段,DeepSeek / 通义 / Ollama 全都实现了 —— 一次写对,到处能用。
    """
    payload = {"model": model, "messages": messages,
               "temperature": temperature, "stream": False}
    if tools:
        payload["tools"] = list(tools)
        payload["tool_choice"] = "auto"
    return payload


def parse_response(data):
    """从 OpenAI 兼容的响应里取回答。返回 (是否成功, 文本或错误说明)。

    各家的响应骨架一致(`choices[0].message.content`),但出错时的形状
    五花八门,所以这里逐层判,拿不到就给一句看得懂的话。
    """
    if not isinstance(data, dict):
        return False, "AI 返回的不是 JSON 对象"
    error = data.get("error")
    if error:
        message = error.get("message") if isinstance(error, dict) else str(error)
        return False, f"AI 返回错误: {message}"
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return False, "AI 返回里没有 choices(响应结构不像 OpenAI 协议)"
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if content is None:
        content = first.get("text")      # 少数实现用 text 字段
    if not content:
        return False, "AI 的回答是空的"
    return True, str(content).strip()


def check_config(base_url, model):
    """配置够不够发一次请求。返回错误说明,够就返回空串。

    抽出来是因为**两条路都要用**:普通问答和带工具的 Agent 循环。分开写
    迟早会漏其中一条(漏了就是"点下去报个看不懂的错")。
    """
    if not chat_url(base_url):
        return ("还没填 AI 地址。请到 插件偏好设置 -> 『项目顾问』里填"
                "API 地址 / 模型名 / API Key")
    if not (model or "").strip():
        return ("还没填模型名。请到 插件偏好设置 -> 『项目顾问』里填"
                "(比如 deepseek-chat / qwen-plus)")
    return ""


# ── 这个模型不支持工具调用吗(试过一次就记住,别每轮都白试)────────
_NO_TOOLS = {"disabled": False}


def tools_disabled():
    """是否已经确认"这个模型不支持工具调用"。"""
    return bool(_NO_TOOLS["disabled"])


def reset_tools_disabled():
    """换模型 / 改地址之后要清掉这个记忆(测试也用)。"""
    _NO_TOOLS["disabled"] = False


def tools_unsupported_in(text):
    """这段报错是不是在说"我不认识 tools / function 参数"。

    ── 为什么必须处理这条(真会踩)──────────────────────────────────
    不是所有本地模型都支持 function calling —— 比如 Ollama 上一个小模型,
    请求里带上 `tools` 它会**直接 400**。对用户来说就是"AI 顾问突然坏了",
    而且报的是一串英文,根本看不出是"这个模型不支持工具"。

    所以:一旦看到这类报错,**自动退化成不带工具的纯问答**再试一次 ——
    功能少一点,但用户还能正常问问题。这是"优雅降级"而不是"报错退出"。
    """
    low = (text or "").lower()
    if not low:
        return False
    if "tool" not in low and "function" not in low:
        return False
    return any(token in low for token in
               ("not support", "unsupported", "unknown", "invalid",
                "unrecognized", "no such", "does not", "doesn't", "不存在",
                "不支持", "未知", "无效"))


def _post_json(url, api_key, payload, timeout):
    """发一次 POST。返回 (是否成功, 数据 或 错误说明)。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:                                 # noqa: BLE001
            pass
        if exc.code in (401, 403):
            return False, (f"AI 拒绝了请求(HTTP {exc.code}) —— 多半是 API Key "
                           f"不对或没有权限。\n{detail}")
        return False, f"AI 请求失败(HTTP {exc.code})。\n{detail}"
    except urllib.error.URLError as exc:
        return False, (f"连不上 AI 服务: {exc.reason}\n"
                       f"(检查网络,以及 API 地址是否写对: {url})")
    except Exception as exc:                              # noqa: BLE001
        return False, f"调用 AI 失败: {type(exc).__name__}: {exc}"

    try:
        return True, json.loads(raw)
    except ValueError:
        return False, f"AI 返回的不是 JSON(前 300 字):\n{raw[:300]}"


def call_chat_raw(base_url, api_key, model, messages, timeout=60, tools=None):
    """发一次请求,返回**原始 JSON**。返回 (是否成功, 数据 或 错误说明)。

    ── 为什么要有"原始"这一层 ─────────────────────────────────────
    带工具的时候,我们要的不只是 `choices[0].message.content` —— 还得看
    `message.tool_calls`(模型要求调哪些工具)。而 `parse_response` 只负责
    抽那段文字,看不到工具。所以拆成两层:这一层负责"把 JSON 拿回来",
    上层决定怎么解释它。

    ── 错误分支都要有"下一步怎么办"────────────────────────────────
    401/403 = Key 不对(最常见),超时 / 连不上 = 地址或网络。这些如果只把
    异常原文丢出去,用户没法行动。
    """
    problem = check_config(base_url, model)
    if problem:
        return False, problem
    url = chat_url(base_url)

    # 已经确认这个模型不吃 tools -> 就别再带上去了
    if tools and tools_disabled():
        tools = None

    ok, data = _post_json(url, api_key,
                          request_payload(model, messages, tools=tools), timeout)
    if ok or not tools:
        return ok, data

    # ── 带了工具却失败:可能是这个模型不支持 -> 脱掉工具重试一次 ────
    if tools_unsupported_in(data if isinstance(data, str) else ""):
        ok2, data2 = _post_json(url, api_key,
                                request_payload(model, messages), timeout)
        if ok2:
            _NO_TOOLS["disabled"] = True
            return True, data2
    return ok, data


def call_chat(base_url, api_key, model, messages, timeout=60):
    """发一次请求并取出回答文字。返回 (是否成功, 回答或可读的错误说明)。"""
    ok, data = call_chat_raw(base_url, api_key, model, messages, timeout)
    if not ok:
        return False, data
    return parse_response(data)


# ---------------------------------------------------------------------------
# 纯函数:工具调用(function calling)
# ---------------------------------------------------------------------------
def message_of(data):
    """取出 `choices[0].message` 字典;取不到返回 {}。"""
    if not isinstance(data, dict):
        return {}
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return {}
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message")
    return message if isinstance(message, dict) else {}


def extract_tool_calls(data):
    """从响应里抠出模型要求调用的工具。

    返回 `[{"id": ..., "name": ..., "arguments": {...}}, ...]`。

    ── arguments 是**字符串**里的 JSON(必须容忍它坏掉)──────────────
    OpenAI 协议里 `arguments` 是一个 JSON **字符串**。模型偶尔会给出不合法
    的 JSON(少个括号、多个逗号)。这时**不能整轮报废** —— 让调用方收到
    "解析失败"这个结果,它会把这句话当作工具返回值喂回模型,模型下一轮
    多半就自己改对了。这比直接报错给用户好得多。
    """
    calls = []
    message = message_of(data)
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list):
        return calls
    for index, item in enumerate(raw_calls):
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, dict):
            parsed = arguments                     # 少数实现直接给对象
        else:
            text = str(arguments or "").strip()
            try:
                parsed = json.loads(text) if text else {}
            except ValueError:
                parsed = None
            if parsed is not None and not isinstance(parsed, dict):
                parsed = None
        calls.append({
            "id": str(item.get("id") or f"call_{index}"),
            "name": name,
            "arguments": parsed,
        })
    return calls


def assistant_tool_message(data, tool_calls):
    """把"模型这一轮说的话 + 它要调的工具"做成一条 assistant 消息。

    下一轮请求**必须**把它原样带上,否则协议不成立(工具结果无处安放,
    有些实现会直接报 400)。
    """
    content = message_of(data).get("content") or ""
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {"id": call["id"], "type": "function",
             "function": {"name": call["name"],
                          "arguments": json.dumps(call.get("arguments") or {},
                                                  ensure_ascii=False)}}
            for call in tool_calls
        ],
    }


def tool_result_message(tool_call_id, content):
    """工具执行结果的消息(协议要求 role=tool + tool_call_id)。"""
    return {"role": "tool", "tool_call_id": tool_call_id,
            "content": str(content or "")}


def tools_payload():
    """给请求用的工具清单。取不到工具模块时返回空(退化成纯聊天)。"""
    try:
        from . import advisor_tools

        return advisor_tools.tools_schema()
    except Exception:                                     # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# 纯函数:脱敏 / 截断 / 换行
# ---------------------------------------------------------------------------
def redact(text, secrets=(), replacements=()):
    """发送前脱敏:抹掉密钥、把绝对路径换成占位符。

    `secrets` 里长度太短的不抹 —— 比如用户把 Key 填成一个字符,那抹了
    会把正常文字也打死(比如把 "a" 全换成 "***")。密钥正常都是几十个字符,
    所以给个下限很安全。
    """
    out = text or ""
    for secret in secrets:
        if secret and len(str(secret)) >= 6:
            out = out.replace(str(secret), "***")
    for old, new in replacements:
        if old:
            out = out.replace(str(old), str(new))
    return out


def truncate(text, limit, note="\n…(内容太长,已截断)"):
    """按字符数截断。limit <= 0 表示不截断。"""
    out = text or ""
    if limit and limit > 0 and len(out) > limit:
        return out[:limit] + note
    return out


def _char_width(char):
    """这个字符在界面上占几列(中文/全角算 2,其余算 1)。"""
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def display_width(text):
    """整串文字在界面上占几列。"""
    return sum(_char_width(char) for char in (text or ""))


def wrap(text, width=46):
    """把长文本切成适合在侧栏一行行显示的短行。

    Blender 的 `label(text=...)` **不会自动换行** —— 给它一段长文本,它
    直接截断(中文更惨,一屏只能看到十来个字)。所以回答要自己切。

    ── 两个细节 ────────────────────────────────────────────────────
    1. 按**显示宽度**切,不按字符个数:CJK 占两列,混排时按个数切会忽长忽短;
    2. 英文/代码行优先在**空格**处断,不然 `some_function_name` 会被切一半
       (中文没有空格,就只能按宽度断)。
    """
    lines = []
    for paragraph in (text or "").splitlines():
        if not paragraph.strip():
            lines.append("")
            continue
        current = ""
        current_width = 0
        for char in paragraph:
            char_width = _char_width(char)
            if current and current_width + char_width > width:
                # 英文/代码:退到最后一个空格处断,尽量不切断单词
                cut = current.rfind(" ") if " " in current else -1
                if cut > 0 and cut >= len(current) // 2:
                    lines.append(current[:cut])
                    current = current[cut + 1:]
                    current_width = display_width(current)
                else:
                    lines.append(current)
                    current = ""
                    current_width = 0
            current += char
            current_width += char_width
        lines.append(current)
    # 末尾多余空行去掉(分段之间的空行保留一个)
    while lines and lines[-1] == "":
        lines.pop()
    return lines


# ---------------------------------------------------------------------------
# 上下文收集(需要 bpy)
# ---------------------------------------------------------------------------
def collect_context(context, include_code=True, max_chars=160000, extra=""):
    """把"项目现在什么状况"拼成一段文本,发给 AI。返回 (文本, 各段字符数)。

    各段都是**尽力而为**:任何一段取不到都只写一行说明,绝不因为某段失败
    就让整个顾问用不了 —— 顾问在"项目正坏着"的时候最该能用。
    """
    from . import codegen, diagnostics, export_report, gk_log, packaging, project

    scene = getattr(context, "scene", None)
    sections = []

    # 【1】复用『出问题了?』那份诊断:插件版本 / 物体与碰撞尺寸 / 节点图与
    #      连线 / 主相机 / 试导出的告警。这一份是上下文的主体。
    try:
        sections.append(("诊断信息", diagnostics.build_report(context)))
    except Exception as exc:                              # noqa: BLE001
        sections.append(("诊断信息", f"(收集失败: {type(exc).__name__}: {exc})"))

    # 【2】生成的 GDScript —— "角色为什么不动"这类问题往往要看真代码。
    if include_code:
        sections.append(("生成的 GDScript", _code_section(scene, codegen)))

    # 【3】上次导出报告(带 hint / 出问题的节点名,比日志更好读)
    try:
        report = export_report.load(scene) if scene is not None else None
        if report:
            sections.append(("上次导出报告",
                             truncate(export_report.report_text(report), 20000)))
    except Exception as exc:                              # noqa: BLE001
        sections.append(("上次导出报告", f"(读取失败: {exc})"))

    # 【4】Godot 与打包状况 —— 打包失败最常见的原因(没装导出模板)在这里
    sections.append(("Godot 与打包", _godot_section(context, packaging, project)))

    # 【5】最近日志:ERROR 全带上(带 traceback),其余只带尾巴
    sections.append(("最近日志", _log_section(gk_log)))

    if extra:
        sections.append(("补充", extra))

    # 脱敏:抹掉 Key、把 .blend / 项目目录换成占位符
    path_map = _path_placeholders(context, project)
    secrets = _secrets(context)
    parts = []
    sizes = []
    for title, text in sections:
        body = truncate(redact(text, secrets, path_map), max_chars // 4)
        parts.append(f"===== {title} =====\n{body}")
        sizes.append((title, len(body)))
    return redact("\n\n".join(parts), secrets, path_map), sizes


def _code_section(scene, codegen):
    """把主逻辑图生成的 GDScript 附上。"""
    try:
        settings = getattr(scene, "gk_settings", None)
        options = {"dimension": str(getattr(settings, "dimension", "3")),
                   "base_class": str(getattr(settings, "base_class", "auto"))}
        result = codegen.generate_result(_main_logic_tree(), options)
        header = (f"(生成 {len(result.code.splitlines())} 行;"
                  f"告警 {len(result.warnings)} 条)")
        return f"{header}\n{truncate(result.code, 40000)}"
    except Exception as exc:                              # noqa: BLE001
        return f"(生成失败: {type(exc).__name__}: {exc})"


def _main_logic_tree():
    """找那棵参与导出的游戏逻辑图(和 diagnostics 里的取法一致)。"""
    import bpy

    for group in bpy.data.node_groups:
        if (getattr(group, "bl_idname", "") == "GKGameLogicTree"
                and getattr(group, "gk_enabled", True)):
            return group
    return None


def _godot_section(context, packaging, project):
    """Godot 定位 / 版本 / 导出模板 / 主场景。"""
    lines = []
    try:
        executable = project.find_godot_executable(context)
        lines.append(f"Godot 可执行文件: {executable or '(没找到)'}")
        version = project.godot_version(executable, context) if executable else ""
        lines.append(f"Godot 版本: {version or '(取不到)'}")
        if version:
            ready, template_dir, note = packaging.templates_status(version)
            lines.append(f"导出模板: {'已就绪' if ready else '缺失'} {template_dir or ''}")
            if not ready:
                lines.append(f"  {note.splitlines()[0] if note else ''}")
    except Exception as exc:                              # noqa: BLE001
        lines.append(f"(取不到: {type(exc).__name__}: {exc})")
    try:
        directory, reason = project.resolve(getattr(context, "scene", None))
        lines.append(f"项目目录: {directory or '(推不出来)'}  ({reason})")
        lines.append(f"主场景: {project.main_scene(directory) or '(没设)'}")
    except Exception as exc:                              # noqa: BLE001
        lines.append(f"(项目解析失败: {type(exc).__name__}: {exc})")
    return "\n".join(lines)


def _log_section(gk_log):
    """最近日志:ERROR 全带上,其它只留尾巴。"""
    try:
        records = gk_log.records()
    except Exception as exc:                              # noqa: BLE001
        return f"(读不到日志: {exc})"
    if not records:
        return "(还没有日志)"
    errors = [line for level, line in records if level == "ERROR"]
    others = [line for level, line in records if level != "ERROR"][-40:]
    lines = [f"(共 {len(records)} 条,其中 ERROR {len(errors)} 条)"]
    if errors:
        lines.append("--- ERROR(全部)---")
        lines.extend(errors[-20:])
    if others:
        lines.append("--- 其它(最近 40 条)---")
        lines.extend(others)
    return "\n".join(lines)


def _path_placeholders(context, project):
    """绝对路径 -> 占位符。既保护隐私,也让回答更好读。"""
    import bpy

    pairs = []
    blend = bpy.data.filepath
    if blend:
        import os
        pairs.append((os.path.dirname(blend), "<blend目录>"))
        pairs.append((blend, "<本工程.blend>"))
    try:
        directory, _reason = project.resolve(getattr(context, "scene", None))
        if directory:
            pairs.append((directory, "<Godot项目>"))
    except Exception:                                     # noqa: BLE001
        pass
    # 长的先替换 —— 否则 <blend目录> 会先把 <Godot项目> 的前缀吃掉
    pairs.sort(key=lambda item: len(item[0]), reverse=True)
    return pairs


def _secrets(context):
    """要从文本里抹掉的东西(目前只有 API Key)。"""
    try:
        from . import project

        prefs = project.preferences(context)
        key = getattr(prefs, "advisor_api_key", "") if prefs else ""
        return [key] if key else []
    except Exception:                                     # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# 异步请求:后台线程只做网络 IO,结果回主线程处理
# ---------------------------------------------------------------------------
# ── 为什么必须异步(用户 2026-10-08 的反馈)────────────────────────
# 原来点『问 AI』是**阻塞**的:界面冻 60 秒。只问一句还能忍,一旦要"连续
# 对话"、甚至以后让 AI 连着调几个工具,这个模式会直接冻死 —— 体验比现在
# 的侧栏还差。所以异步是"对话界面"和"AI 亲自操作"共同的前提,必须先做。
#
# ── 线程里的铁律(不遵守会出"偶尔崩、偶尔不刷新"的鬼影 bug)────────
# 只有**主线程**能碰 bpy。所以那个线程只做两件事:网络 IO + 解析,结果写进
# 下面这个模块级字典;碰 UI、碰 HISTORY、写文本块**一律回主线程做**。
_REQUEST = {"active": False, "started": 0.0, "timeout": 60, "result": None}


def _spawn(fn, timeout):
    """在后台线程里跑 `fn()`。已经在跑时返回 False。

    线程里**只许做网络 IO + 文本处理** —— 碰一下 bpy 就会出"偶尔崩、
    偶尔不刷新"的鬼影 bug(见上面那段线程铁律)。结果写进 `_REQUEST`,
    由主线程的 `poll_background()` 取走。
    """
    if _REQUEST["active"]:
        return False
    _REQUEST.update({"active": True, "started": time.time(),
                     "timeout": timeout, "result": None})

    def worker():
        try:
            _REQUEST["result"] = fn()
        except Exception as exc:                          # noqa: BLE001
            _REQUEST["result"] = (
                False, f"调用 AI 失败: {type(exc).__name__}: {exc}")

    threading.Thread(target=worker, daemon=True, name="GK-Advisor").start()
    return True


def request_in_background(base_url, api_key, model, messages, timeout=60):
    """在后台线程里发一次请求、取回答文字。已经在跑时返回 False。

    (普通问答走这条;带工具的 Agent 循环走下面的 `start_agent`。)
    """
    return _spawn(lambda: call_chat(base_url, api_key, model, messages,
                                    timeout=timeout), timeout)


def is_busy():
    """有没有请求在跑(侧栏用它显示『正在问 AI…』)。"""
    return bool(_REQUEST["active"])


def poll_background():
    """主线程调用:还没好返回 None;好了返回 (ok, text) 并清状态。

    ── 为什么要自己算超时 ────────────────────────────────────────
    `urlopen` 的 timeout 只管"连接 + 读"那一段。万一底层卡在别处(DNS /
    代理 / TLS 握手),那个线程会一直挂着 —— 它是 daemon,不会拖住退出,
    但"侧栏永远显示正在问 AI…"同样让人难受。所以这里用时间兜底:超了就
    放弃等待,那个线程随它去。
    """
    if not _REQUEST["active"]:
        return None
    result = _REQUEST["result"]
    if result is None:
        if time.time() - _REQUEST["started"] > _REQUEST["timeout"] + 15:
            limit = _REQUEST["timeout"]
            _REQUEST.update({"active": False, "result": None})
            return (False, f"AI 请求超时(超过 {limit} 秒没有回应),已放弃等待。"
                           f"检查网络,或换个小一点的模型试试")
        return None
    _REQUEST.update({"active": False, "result": None})
    return result


def cancel_background():
    """放弃等待(那个线程是 daemon,会自己结束)。"""
    _REQUEST.update({"active": False, "result": None})


# ---------------------------------------------------------------------------
# Agent 循环:让 AI 自己看项目、并提议动作
# ---------------------------------------------------------------------------
# 最多来回几轮"模型要工具 -> 我们喂结果"(防止模型和人绕圈子烧钱)
MAX_TOOL_ROUNDS = 6

# Agent 状态。**所有字段只在主线程改** —— 后台线程唯一的出口是 `_REQUEST`。
_AGENT = {
    "active": False,     # 整轮在跑
    "stage": "request",  # request(等网络) / tools(等主线程执行工具) / done
    "messages": [],      # 发给模型的完整对话(含工具结果)
    "round": 0,
    "answer": "",        # 最终的回答文字
    "error": "",
    "pending": None,     # 待主线程执行的 tool_calls
    "steps": [],         # 给用户看的"它做了什么"清单(比如"读了节点图")
    # 待用户确认的动作(**可以有多张** —— AI 常常一次建议好几处改动,
    # 只留最后一张会让前面的建议**静默消失**,用户根本没看到)
    "proposals": [],
}


def start_agent(base_url, api_key, model, messages, timeout=60, tools=None):
    """开始一轮 Agent。已经在跑时返回 False。

    `messages` 由 `build_messages` 拼好(系统提示 + 历史 + 快照 + 本次问题)。
    """
    if _AGENT["active"] or _REQUEST["active"]:
        return False
    if tools is None:
        tools = tools_payload()
    _AGENT.update({"active": True, "stage": "request",
                   "messages": list(messages), "round": 0, "answer": "",
                   "error": "", "pending": None, "steps": [],
                   "proposals": []})
    return _spawn(lambda: call_chat_raw(base_url, api_key, model,
                                        _AGENT["messages"], timeout=timeout,
                                        tools=tools), timeout)


def agent_active():
    """整轮 Agent 还在跑吗(侧栏用它显示状态)。"""
    return bool(_AGENT["active"])


def agent_steps():
    """这一轮 Agent 已经做过的事(侧栏显示"它读了节点图"之类)。"""
    return list(_AGENT["steps"])


def agent_proposals():
    """待用户确认的动作卡片清单(可能有多张,也可能为空)。"""
    return list(_AGENT["proposals"])


def add_proposal(card):
    """追加一张待确认卡片。"""
    if card:
        _AGENT["proposals"].append(card)


def proposal_at(index):
    """取第 index 张卡片;越界返回 None。

    ── 为什么要按**下标**取,而不是"取当前那张"────────────────────
    卡片可以有好几张(见 agent_proposals 的说明)。执行/忽略必须点名到
    具体哪一张 —— 否则用户点第二张的『忽略』,清掉的是第一张。
    """
    try:
        index = int(index)
    except (TypeError, ValueError):
        return None
    cards = _AGENT["proposals"]
    return cards[index] if 0 <= index < len(cards) else None


def drop_proposal(index):
    """丢掉第 index 张卡片(执行过 / 忽略过)。返回是否真的丢掉了。"""
    try:
        index = int(index)
    except (TypeError, ValueError):
        return False
    cards = _AGENT["proposals"]
    if 0 <= index < len(cards):
        del cards[index]
        return True
    return False


def clear_proposals():
    """全部清掉(清空对话时用)。"""
    _AGENT["proposals"] = []


def cancel_agent():
    """放弃整轮(后台线程是 daemon,会自己结束)。"""
    cancel_background()
    _AGENT.update({"active": False, "stage": "done", "pending": None})


def poll_agent_step(context, base_url="", api_key="", model="", timeout=60):
    """**主线程**推进 Agent 一轮。返回 (状态, 结果):

        ("waiting", None)    还在等 —— 调用方继续挂定时器
        ("continue", None)   推进了一步 —— 调用方继续挂定时器
        ("done", (ok, 文本)) 整轮结束

    ── 为什么工具必须在**这里**(主线程)执行(这一轮最关键的一点)────
    只读工具要读节点图、场景设置、导出报告 —— 全是 Blender 数据。而后台
    线程碰 bpy 会崩。所以循环只能这么做:

        线程:发请求 ──> 主线程:看出 tool_calls,在这里执行工具
                    ──> 线程:把结果喂回去再发一轮 ──> ...

    即"网络在线程、工具在主线程",两边交替。这样既不会冻界面,也不会
    在后台线程碰 bpy。
    """
    if not _AGENT["active"]:
        return "done", (False, "没有在跑的 AI 请求")

    stage = _AGENT["stage"]

    if stage == "request":
        result = poll_background()
        if result is None:
            return "waiting", None
        ok, data = result
        if not ok:
            _AGENT.update({"active": False, "stage": "done", "error": data})
            return "done", (False, data)
        calls = extract_tool_calls(data)
        if not calls:
            text_ok, text = parse_response(data)
            _AGENT.update({"active": False, "stage": "done",
                           "answer": text if text_ok else "",
                           "error": "" if text_ok else text})
            return "done", (text_ok, text)
        # 模型要求调工具 -> 记下它说的话 + 要调的工具,等主线程执行
        _AGENT["messages"].append(assistant_tool_message(data, calls))
        _AGENT["pending"] = calls
        _AGENT["stage"] = "tools"
        return "continue", None

    if stage == "tools":
        calls = _AGENT["pending"] or []
        _AGENT["pending"] = None
        for call in calls:
            output, proposal = _execute_tool(call, context)
            # **追加**而不是覆盖:模型常常一口气建议好几处改动,
            # 覆盖会让前面的建议静默消失(用户根本没机会看到)
            add_proposal(proposal)
            _AGENT["steps"].append(_step_label(call, bool(proposal)))
            _AGENT["messages"].append(
                tool_result_message(call["id"], output))
        # 模型不支持工具调用而退化成纯问答时,得**告诉用户**为什么 AI
        # 没去查项目 —— 否则看起来就像它偷懒
        if tools_disabled() and not any("不支持工具" in s
                                        for s in _AGENT["steps"]):
            _AGENT["steps"].append("这个模型不支持工具调用,已退化成纯问答")
        _AGENT["round"] += 1
        if _AGENT["round"] > MAX_TOOL_ROUNDS:
            message = (f"AI 连续要了 {MAX_TOOL_ROUNDS} 轮工具,先停一下"
                       f"(避免绕圈子)。可以把问题拆小一点再问")
            _AGENT.update({"active": False, "stage": "done", "error": message})
            return "done", (False, message)
        # 把工具结果喂回去,发下一轮
        _AGENT["stage"] = "request"
        started = _spawn(lambda: call_chat_raw(base_url, api_key, model,
                                               _AGENT["messages"],
                                               timeout=timeout,
                                               tools=tools_payload()), timeout)
        if not started:
            message = "发起下一轮请求失败(上一条还在跑)"
            _AGENT.update({"active": False, "stage": "done", "error": message})
            return "done", (False, message)
        return "continue", None

    return "done", (not _AGENT["error"], _AGENT["answer"] or _AGENT["error"])


def _execute_tool(call, context):
    """执行一个工具。返回 (喂回模型的文本, 待确认动作 或 None)。

    ⚠ 输出必须**摘要**再喂:比如节点图可能有 200 个节点,全倒过去会把
    上下文撑爆(顾问特别提醒过)。截断逻辑在 advisor_tools 里。
    """
    name = call.get("name", "")
    if call.get("arguments") is None:
        # 模型给的 arguments 不是合法 JSON —— 把这件事当作工具结果告诉它,
        # 它下一轮多半会自己改对(比直接失败给用户好得多)
        return (f"参数不是合法的 JSON,请重新用正确的参数调用 {name}"), None
    try:
        from . import advisor_tools

        return advisor_tools.execute(name, call["arguments"], context)
    except Exception as exc:                              # noqa: BLE001
        return f"工具执行失败: {type(exc).__name__}: {exc}", None


def _step_label(call, made_proposal):
    """给用户看的一行"它做了什么"。"""
    name = call.get("name", "")
    if made_proposal:
        return "提议了一个动作(等你确认)"
    if call.get("arguments") is None:
        return f"{_TOOL_LABELS.get(name, name)}(参数有问题,已让它重试)"
    return _TOOL_LABELS.get(name, name)


# 工具 -> 给用户看的中文短句(侧栏「它做了什么」用)
_TOOL_LABELS = {
    "read_settings": "读了场景设置",
    "list_trees": "看了一遍所有节点图",
    "list_nodes": "读了节点图里的节点",
    "check_export_errors": "查了上次导出报告",
    "get_godot_status": "查了 Godot 与打包状况",
    "diagnose": "跑了一遍完整诊断",
    "propose_action": "提议了一个动作(等你确认)",
}


# ---------------------------------------------------------------------------
# 对话历史 / 对话记录
# ---------------------------------------------------------------------------
# 内存里最多留这么多条(发出去的时候再按 MAX_HISTORY 截)
_HISTORY_CAP = 40


def remember(role, content):
    """往对话历史里记一条。"""
    content = (content or "").strip()
    if not content:
        return
    HISTORY.append({"role": role, "content": content})
    del HISTORY[:-_HISTORY_CAP]


def clear_history():
    HISTORY.clear()


def last_answer():
    """最近一条 AI 回答(『复制回答』用)。"""
    for item in reversed(HISTORY):
        if item.get("role") == "assistant":
            return item.get("content", "")
    return ""


def save_history(scene):
    """把对话历史存进场景 —— 重开 .blend 还能接着聊。

    和 `gk_last_export` 同一套做法:这些信息本来就该跟着文件走。
    """
    if scene is None:
        return
    try:
        scene[HISTORY_KEY] = json.dumps(HISTORY[-_HISTORY_CAP:],
                                        ensure_ascii=False)
    except Exception:                                     # noqa: BLE001
        pass


def load_history(scene):
    """从场景里恢复对话历史。"""
    HISTORY.clear()
    if scene is None:
        return
    raw = scene.get(HISTORY_KEY)
    if not raw:
        return
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return
    if not isinstance(data, list):
        return
    for item in data[-_HISTORY_CAP:]:
        if isinstance(item, dict) and item.get("role") in ("user", "assistant"):
            HISTORY.append({"role": item["role"],
                            "content": str(item.get("content", ""))})


def turn_text(question, answer, error=""):
    """把一轮问答拼成要写进文本块的那几行(纯函数,好测)。"""
    stamp = time.strftime("%Y-%m-%d %H:%M")
    parts = [f"【我】{stamp}\n{(question or '').strip()}\n"]
    if error:
        parts.append(f"\n【AI 顾问】调用失败\n{plainify(error)}\n")
    else:
        body = plainify(answer or "")
        parts.append(f"\n【AI 顾问】{len(body)} 字\n{body}\n")
    return "".join(parts)


def sync_dialog_text(question, answer, error=""):
    """把这一轮问答追加进 Blender 文本块,返回文本块名字。

    ── 为什么用文本块而不是侧栏 label(用户截图里的问题)──────────
    侧栏 label 是**一行一个**,长回答会把侧栏撑到看不见底,而且**没有滚动
    条**;再叠上"每行只放得下十来个字",一屏能看的字少得可怜。
    Blender 的文本编辑器天生能滚动 / 选择 / 复制 / 搜索 —— 所以对话记录
    写进文本块,**读的地方是文本编辑器**。
    """
    import bpy

    block = bpy.data.texts.get(DIALOG_TEXT_NAME)
    if block is None:
        block = bpy.data.texts.new(DIALOG_TEXT_NAME)
        block.write("BlenderGameKit 项目顾问 —— 对话记录\n"
                    "(回答由 AI 生成,请自行核对。清空请点侧栏『清空对话』)\n")
    else:
        block.write("\n\n" + "-" * 60 + "\n")
    block.write(turn_text(question, answer, error))
    try:
        block.use_fake_user = True
    except Exception:                                     # noqa: BLE001
        pass
    return block.name


def clear_dialog_text():
    """把对话记录文本块清空(保留一句说明)。返回是否真的有这个块。"""
    import bpy

    block = bpy.data.texts.get(DIALOG_TEXT_NAME)
    if block is None:
        return False
    block.clear()
    block.write("BlenderGameKit 项目顾问 —— 对话记录\n(已清空)\n")
    return True
