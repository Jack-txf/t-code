"""
对话记忆压缩模块

策略：滑动窗口 + 摘要
  - 当 history 中非 system 消息超过 MAX_HISTORY_TURNS 轮时触发压缩
  - 把"老消息"喂给 LLM 生成一段摘要
  - 用一条 summary 消息替换掉老消息，保留最近 KEEP_RECENT_TURNS 轮

一轮 = 一条 user 消息（含其后跟随的所有 assistant/tool 消息）
"""

from openai import OpenAI

# 超过这么多轮就触发压缩
MAX_HISTORY_TURNS = 5

# 压缩后保留最近几轮（不压缩）
KEEP_RECENT_TURNS = 4

SUMMARY_PROMPT = """\
下面是一段 AI 助手与用户的对话历史。
请用简洁的中文总结其中讨论的主要内容、得出的结论、执行过的操作和关键信息。
总结要紧凑，100~200字，不要遗漏重要细节。

对话历史：
{history_text}
"""


def _count_turns(history: list[dict]) -> int:
    """统计 history 中 user 消息的数量（即对话轮数），不含 system。"""
    return sum(1 for m in history if m["role"] == "user")


def _split_history(history: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """
    把 history 切成三段：
      system_msgs   — role=system 的消息（保持在最前）
      old_msgs      — 需要被压缩的老消息
      recent_msgs   — 保留的最近 N 轮消息

    切割规则：找到倒数第 KEEP_RECENT_TURNS 个 user 消息的位置，
    该位置之前的所有消息就是 old_msgs。
    """
    system_msgs = [m for m in history if m["role"] == "system"]
    non_system = [m for m in history if m["role"] != "system"]

    # 找到倒数第 KEEP_RECENT_TURNS 个 user 消息的索引
    user_indices = [i for i, m in enumerate(non_system) if m["role"] == "user"]
    cut_point = user_indices[-KEEP_RECENT_TURNS]  # 切割点

    old_msgs = non_system[:cut_point]
    recent_msgs = non_system[cut_point:]

    return system_msgs, old_msgs, recent_msgs


def _format_for_summary(msgs: list[dict]) -> str:
    """把消息列表格式化成易读文本，供摘要 LLM 阅读。"""
    lines = []
    for m in msgs:
        role = m["role"]
        content = m.get("content") or ""

        if role == "user":
            lines.append(f"【用户】{content}")
        elif role == "assistant":
            if content:
                lines.append(f"【助手】{content}")
            if m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    lines.append(f"【助手调用工具】{tc['function']['name']}({tc['function']['arguments']})")
        elif role == "tool":
            # 工具结果截断，避免摘要 prompt 太长
            preview = content[:300] + "…" if len(content) > 300 else content
            lines.append(f"【工具结果】{preview}")

    return "\n".join(lines)


def compress_if_needed(history: list[dict], llm: OpenAI, model: str) -> tuple[list[dict], bool]:
    """
    检查是否需要压缩，需要则执行压缩，返回 (新history, 是否压缩了)。

    调用方：
        history, compressed = compress_if_needed(history, llm, model)
        if compressed:
            print("[memory] 已压缩对话历史")
    """
    if _count_turns(history) <= MAX_HISTORY_TURNS:
        return history, False

    system_msgs, old_msgs, recent_msgs = _split_history(history)

    # 如果老消息太少，不值得压缩
    if len(old_msgs) < 3:
        return history, False

    # 调用 LLM 生成摘要（非流式，小请求，快）
    history_text = _format_for_summary(old_msgs)
    prompt = SUMMARY_PROMPT.format(history_text=history_text)

    try:
        resp = llm.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            stream=False,
        )
        summary_text = resp.choices[0].message.content or "(摘要生成失败)"
    except Exception as e:
        # 摘要失败就不压缩，保持原样
        print(f"[memory] 摘要生成失败，跳过压缩: {e}")
        return history, False

    # 组装新 history：system + summary消息 + 近N轮
    summary_msg = {
        "role": "assistant",
        "content": f"[对话历史摘要]\n{summary_text}",
    }
    new_history = system_msgs + [summary_msg] + recent_msgs

    print(f"[memory] 压缩完成：{len(history)} → {len(new_history)} 条消息，摘要 {len(summary_text)} 字")
    return new_history, True