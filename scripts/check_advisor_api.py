"""用**真实 AI 服务**把『项目顾问』跑一遍(第五十六轮)。

用法:
    blender -b --python scripts\\check_advisor_api.py

── 为什么这个脚本不在门禁里 ──────────────────────────────────────
门禁是"随时能跑、不花钱、不需要凭据"的。这个脚本两样都占:
  * 要你的 API Key(从**插件偏好设置**里读,不在命令行传、不落盘);
  * 每跑一次都真的花钱(几十秒、几次调用)。
所以它**故意不接进 build_and_test.ps1** —— 改动了 advisor / advisor_tools
之后,手动跑一次就行。

── 为什么必须真跑(单测过不了这一关)────────────────────────────
离线单测和冒烟测的都是**我假想的响应形状**。真实服务可能:
  * 调工具时 `content` 是**空串**(实测 DeepSeek 就是这样);
  * 多带一些字段(`reasoning_content` / `usage` / `system_fingerprint`);
  * 不支持 `tools` 参数(小模型直接 400)。
这些只有真发一次请求才知道。所以这个脚本验的就是"真形状对不对"。

**不会打印 API Key**,只打印它的长度。
"""

import sys
import time

import addon_utils
import bpy

# 用**当前源码**(而不是已装的那份),这样改完立刻能验
sys.path.insert(0, __file__.rsplit("scripts", 1)[0].rstrip("\\/"))

from BlenderGameKit import advisor, advisor_tools  # noqa: E402


def read_config():
    """从**插件偏好设置**里读 AI 配置。返回 (地址, 模型, Key)。

    ── 为什么从偏好设置读,不从命令行 / 环境变量 ────────────────────
    这样"你在 Blender 里配的"和"这个脚本验的"一定是同一套;命令行传参
    还会把它留在 shell 历史里。
    """
    module_name = None
    for module in addon_utils.modules():
        name = getattr(module, "__name__", "")
        if "blender_game_kit" in name:
            module_name = name
            break
    addons = bpy.context.preferences.addons
    addon = None
    candidates = [module_name] if module_name else []
    if module_name and module_name.count(".") >= 2:
        candidates.append(module_name.rsplit(".", 1)[0])
    for key in candidates:
        if key and addons.get(key) is not None:
            addon = addons.get(key)
            break
    prefs = getattr(addon, "preferences", None) if addon else None
    if prefs is None:
        print("找不到插件偏好设置(插件没装?)")
        return "", "", ""
    return (getattr(prefs, "advisor_base_url", "") or "",
            getattr(prefs, "advisor_model", "") or "",
            getattr(prefs, "advisor_api_key", "") or "")


def run_agent(base_url, api_key, model, question, snapshot="", timeout=90,
              deadline_seconds=240):
    """跑一轮完整 Agent。返回 (ok, 文本, 步骤, 卡片清单)。"""
    advisor.cancel_agent()
    advisor.clear_proposals()
    messages = advisor.build_messages(question, snapshot)
    if not advisor.start_agent(base_url, api_key, model, messages,
                               timeout=timeout):
        return False, "start_agent 返回 False(上一条还在跑?)", [], []
    state, result = "waiting", None
    deadline = time.time() + deadline_seconds
    while time.time() < deadline:
        state, result = advisor.poll_agent_step(bpy.context, base_url, api_key,
                                                model, timeout=timeout)
        if state == "done":
            break
        time.sleep(0.2)
    ok = bool(result and result[0])
    text = result[1] if result else "(没结果)"
    return ok, text, advisor.agent_steps(), advisor.agent_proposals()


def main():
    base_url, model, api_key = read_config()
    print(f"CONFIG url={base_url!r} model={model!r} key_len={len(api_key)}")
    if not (base_url and model and api_key):
        print("跳过:还没在插件偏好设置里配好 AI 地址 / 模型名 / Key")
        return 0
    print(f"CHAT_URL {advisor.chat_url(base_url)}")
    failures = []

    # ── 1. 连通性 + 模型名是否有效 ──────────────────────────────
    print("\n=== 1. 普通问答(不带工具)===")
    started = time.time()
    ok, text = advisor.call_chat(
        base_url, api_key, model,
        [{"role": "user", "content": "只回四个字:连接正常"}], timeout=90)
    print(f"BASIC ok={ok} 耗时={time.time() - started:.1f}s")
    print("BASIC_TEXT", str(text)[:200])
    if not ok:
        print("✗ 基本连通就失败了 —— 先查 Key / 地址 / 模型名")
        print("CHECK_ADVISOR_API=FAIL")
        return 1

    # ── 2. tools 协议 + 响应形状(关键)──────────────────────────
    print("\n=== 2. 带工具(验证 tools 协议与响应形状)===")
    ok2, data = advisor.call_chat_raw(
        base_url, api_key, model,
        [{"role": "system", "content": "你是 BlenderGameKit 的顾问。"},
         {"role": "user", "content": "先调用 read_settings 看场景设置,"
                                     "再一句话总结。"}],
        timeout=90, tools=advisor_tools.tools_schema())
    print(f"TOOLS ok={ok2}")
    if not ok2:
        print("TOOLS_ERR", str(data)[:400])
        failures.append("带 tools 的请求失败")
    else:
        calls = advisor.extract_tool_calls(data)
        message = advisor.message_of(data)
        print("MESSAGE_KEYS", sorted(message.keys()))
        print("CONTENT", repr(str(message.get("content"))[:120]))
        print("TOOL_CALLS", [(c["name"], c["arguments"]) for c in calls])
        print("TOOLS_DISABLED", advisor.tools_disabled())
        if not calls:
            failures.append("模型没返回 tool_calls(协议形状可能对不上)")

    # ── 3. 完整 Agent 循环:真模型自己调工具 ────────────────────
    print("\n=== 3. Agent 循环(真模型自己去看项目)===")
    ok3, text3, steps3, _cards = run_agent(
        base_url, api_key, model,
        "这个项目现在是什么状态?用工具看一下再回答,一句话就好。",
        "(可能很空)")
    print("AGENT ok=", ok3, "steps=", steps3)
    print("AGENT_ANSWER", str(text3)[:300])
    if not ok3:
        failures.append("Agent 循环没跑完")
    if not steps3:
        failures.append("Agent 没调用任何工具(它应该自己去查)")

    # ── 4. 让模型提议动作:卡片要出来,而且措辞不能撒谎 ──────────
    print("\n=== 4. 提议动作 ===")
    ok4, text4, steps4, cards4 = run_agent(
        base_url, api_key, model,
        "场景里还没有相机,我看不到画面。请用 propose_action 提议一个"
        "合适的动作,然后一句话说明。")
    print("PROPOSE ok=", ok4, "steps=", steps4)
    print("PROPOSE_ANSWER", str(text4)[:300])
    print("CARD_COUNT", len(cards4))
    for index, card in enumerate(cards4):
        print(f"  CARD[{index}]", card.get("action"), card.get("target"),
              card.get("value"))
    if not cards4:
        failures.append("模型没有产出任何提议卡片")
    elif "已" in str(text4) and "改好" in str(text4):
        # 模型不许说"我已经改好了" —— 它没有那个能力
        failures.append("模型谎称已经改好了")

    # ── 5. 多张卡片 + 按下标点名 ───────────────────────────────
    print("\n=== 5. 一次提议两个动作(多卡片)===")
    ok5, text5, steps5, cards5 = run_agent(
        base_url, api_key, model,
        "帮我做两件事:一是让寻路能用,二是把卡通材质关掉(我想核对细节)。"
        "请**分别**用两次 propose_action 提议这两件事。")
    print("MULTI ok=", ok5, "card_count=", len(cards5))
    print("MULTI_ANSWER", str(text5)[:300])
    for index, card in enumerate(cards5):
        print(f"  CARD[{index}]", card.get("action"), card.get("value"))
    if len(cards5) < 2:
        # 模型不一定每次都肯调两次 —— 只提醒,不算失败
        print("提示:模型这次只提了 "
              f"{len(cards5)} 条。多卡片路径由单测锁着,这里只是观察。")
    else:
        advisor.drop_proposal(1)
        left = advisor.agent_proposals()
        print("AFTER_DROP_1 count=", len(left),
              "first=", left[0].get("action") if left else None)
        if len(left) != 1 or left[0].get("action") != cards5[0].get("action"):
            failures.append("按下标丢卡片丢错了(下标点名有 bug)")
        advisor.clear_proposals()

    advisor.cancel_agent()
    print()
    if failures:
        print("发现的问题:")
        for item in failures:
            print("  ✗", item)
        print("CHECK_ADVISOR_API=FAIL")
        return 1
    print("真机全链路通过(连通 / tools 协议 / Agent 循环 / 提议卡片 / 多卡片)")
    print("CHECK_ADVISOR_API=OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())