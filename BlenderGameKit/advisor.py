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
   要强调就直接把话说清楚。"""


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


def request_payload(model, messages, temperature=0.2):
    """OpenAI 兼容的请求体。stream=False:我们要一次拿完,不做流式。"""
    return {"model": model, "messages": messages,
            "temperature": temperature, "stream": False}


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


def call_chat(base_url, api_key, model, messages, timeout=60):
    """真发一次请求。返回 (是否成功, 回答或可读的错误说明)。

    ── 为什么是阻塞调用 ────────────────────────────────────────────
    算子跑在 Blender 主线程上,所以这次调用期间界面会没响应。这是**刻意**
    和插件其它地方保持一致:『直接运行游戏』要等 Godot 起来、『打包成
    可执行文件』要等 Godot 导出(最长 600 秒),都是"先说一声,然后等"。
    好处是不用引线程 + 定时器轮询那套东西,少一整类难查的 bug。
    调用方(算子)必须在调用**之前**告诉用户要等多久。

    ── 错误分支都要有"下一步怎么办"────────────────────────────────
    401/403 = Key 不对(最常见),超时 / 连不上 = 地址或网络。这些如果只把
    异常原文丢出去,用户没法行动。
    """
    url = chat_url(base_url)
    if not url:
        return False, ("还没填 AI 地址。请到 插件偏好设置 -> 『项目顾问』里填"
                       "API 地址 / 模型名 / API Key")
    if not (model or "").strip():
        return False, ("还没填模型名。请到 插件偏好设置 -> 『项目顾问』里填"
                       "(比如 deepseek-chat / qwen-plus)")

    body = json.dumps(request_payload(model, messages),
                      ensure_ascii=False).encode("utf-8")
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
        data = json.loads(raw)
    except ValueError:
        return False, f"AI 返回的不是 JSON(前 300 字):\n{raw[:300]}"
    return parse_response(data)


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


def request_in_background(base_url, api_key, model, messages, timeout=60):
    """在后台线程里发请求。已经有请求在跑时返回 False。"""
    if _REQUEST["active"]:
        return False
    _REQUEST.update({"active": True, "started": time.time(),
                     "timeout": timeout, "result": None})

    def worker():
        try:
            _REQUEST["result"] = call_chat(base_url, api_key, model, messages,
                                           timeout=timeout)
        except Exception as exc:                          # noqa: BLE001
            _REQUEST["result"] = (
                False, f"调用 AI 失败: {type(exc).__name__}: {exc}")

    threading.Thread(target=worker, daemon=True, name="GK-Advisor").start()
    return True


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
