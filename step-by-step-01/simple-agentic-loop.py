import os
import json
from typing import Optional

# 加载环境变量
from load_env import load_env
load_env()

from openai import OpenAI
from tools import TOOLS, TOOL_SCHEMAS  # 工具

client: Optional[OpenAI] = None

SYSTEM_PROMPT = """\
You are a helpful AI assistant with access to tools.
Use tools proactively to answer the user's request.
Think step by step before acting.
"""

MAX_ITERATIONS = 20  # 最大的迭代次数，防止死循环


def init_llmmodel() -> OpenAI | None:
    global client
    client = OpenAI(
        base_url=os.environ["DEEPSEEK_BASE_URL"],
        api_key=os.environ["DEEPSEEK_API_KEY"],
    )
    return client


def execute_tool(tool_call: dict) -> str:
    """执行单个工具调用，返回字符串结果。"""
    name = tool_call["function"]["name"]
    args = json.loads(tool_call["function"]["arguments"])

    if name not in TOOLS:
        return f"Error: unknown tool '{name}'"

    print(f"\n [tool] {name}({args})")
    result = TOOLS[name]["fn"](**args)

    # 截断过长输出，避免撑爆上下文
    if len(result) > 4000:
        result = result[:2000] + "\n...(truncated)...\n" + result[-500:]

    print(f" [result] {result[:200]}{'...' if len(result) > 200 else ''}")
    return result


def run_agent_loop(user_message: str, history: list[dict]) \
        -> tuple[str, list[dict]]:
    """
    执行一轮完整的 agentic loop。
    流程： 构建 messages → 流式调用 LLM → 有工具调用? 执行并继续 : 返回最终回复
    返回： (最终回复文本, 更新后的 history)
    """
    # 构建本轮完整消息：system + 历史 + 本轮用户消息
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += history
    messages.append({"role": "user", "content": user_message})

    # working 是不含 system 的副本，最终作为新 history 返回
    working = list(history)
    working.append({"role": "user", "content": user_message})

    for iteration in range(MAX_ITERATIONS):
        print(f"\n{'─' * 50}")
        print(f"[iteration {iteration + 1}] calling LLM...")

        # ── 流式调用 ──────────────────────────────────────────────
        full_content = ""
        raw_tool_calls: dict[int, dict] = {}  # index -> {id, name, arguments}

        stream = client.chat.completions.create(  # type: ignore[arg-type]
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            stream=True,
        )

        print("[LLM] ", end="", flush=True)

        for chunk in stream:
            print("-------------------------【chunk片段】")
            print(chunk, end="\n", flush=True)
            print("--------------------------------------------- end")
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            # 文本片段：实时打印并累积
            if delta.content:
                full_content += delta.content

            # 工具调用片段：按 index 拼接（流式下是增量 JSON）
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in raw_tool_calls:
                        raw_tool_calls[idx] = {"id": "", "name": "", "arguments": ""}
                    if tc.id:
                        raw_tool_calls[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            raw_tool_calls[idx]["name"] += tc.function.name
                        if tc.function.arguments:
                            raw_tool_calls[idx]["arguments"] += tc.function.arguments
        print()  # 换行

        # 整理成标准格式的工具调用列表
        tool_calls = [
            {
                "id": raw_tool_calls[i]["id"],
                "type": "function",
                "function": {
                    "name": raw_tool_calls[i]["name"],
                    "arguments": raw_tool_calls[i]["arguments"],
                },
            }
            for i in sorted(raw_tool_calls.keys())
        ]

        # ── 把 assistant 消息追加到 messages ─────────────────────
        assistant_msg: dict = {"role": "assistant", "content": full_content}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls # 如果有工具调用，则添加

        messages.append(assistant_msg)
        working.append(assistant_msg)

        # ── 没有工具调用：任务完成，退出循环 ─────────────────────
        if not tool_calls:
            final_response = full_content
            break

        # ── 有工具调用：逐个执行，结果追加后继续下一轮 ───────────
        for tc in tool_calls:
            result = execute_tool(tc)
            tool_result_msg = {
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            }
            messages.append(tool_result_msg)
            working.append(tool_result_msg)

    else:
        final_response = "[max iterations reached]"

    return final_response, working


def main():
    print("T-code v1 — input 'exit' to quit====\n")

    # 1. 历史对话存储
    history: list[dict] = []

    # 2. 初始化llm模型
    init_llmmodel()

    # 3. loop
    while True:
        try:
            user_input = input("you --> ")
        except (EOFError, KeyboardInterrupt):
            print("\n Bye!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "q"):
            print("Bye!")
            break

        # 4. run_agent_loop
        response, history = run_agent_loop(user_input, history)
        print(f"\n【Assistant】: {response}\n")


if __name__ == "__main__":
    main()
