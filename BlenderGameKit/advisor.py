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
6. 能一两句说清就别写五段。用户是在干活,不是在读文章。"""


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


def build_messages(question, context_text, system_prompt=None):
    """拼出 OpenAI 协议的 messages。"""
    system = system_prompt or DEFAULT_SYSTEM_PROMPT
    return [{"role": "system", "content": system},
            {"role": "user", "content": compose_text(question, context_text)}]


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
