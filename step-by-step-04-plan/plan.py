"""
step-06 Agent Loop：Plan-then-Execute 模式

在 step-05 基础上引入两阶段执行：
  阶段一 PLANNING  — Agent 生成 Markdown 格式的执行计划
  阶段二 EXECUTING — 用户确认后，Agent 按计划逐步执行

用户交互：
  普通输入       → 判断是否需要规划
  y / yes / 确认 → 确认当前计划，开始执行
  n / no / 取消  → 取消当前计划
  !<消息>        → 强制跳过规划，直接执行
  exit/quit/q    → 退出

运行前需先启动：
  python ../step-by-step-05-code-tools/mcp_server_05.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

# ── 路径修正 ──────────────────────────────────────────────────────────
project_root = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "step-by-step-04-plan"))

from load_env import load_env
load_env()

from openai import OpenAI
from mymcp.tmcp_client import MyMCPClient           # type: ignore
from memory.memory_compressor import compress_if_needed    # type: ignore
from memory.memory_persistence import (                     # type: ignore
    build_system_prompt_with_memory,
    execute_save_memory_tool,
    SAVE_MEMORY_TOOL_SCHEMA,
    DEFAULT_MEMORY_PATH,
    load_memory,
)
from plan.task_state import TaskState, TaskStatus, should_plan
from prompt.tcode_prompt import (
    BASE_SYSTEM_PROMPT,
    PLANNING_PROMPT,
    EXECUTING_PROMPT_TEMPLATE
)

MAX_ITERATIONS = 10
llm: Optional[OpenAI] = None

def init_llm() -> OpenAI:
    global llm
    llm = OpenAI(
        base_url=os.environ["DEEPSEEK_BASE_URL"],
        api_key=os.environ["DEEPSEEK_API_KEY"],
    )
    return llm


# ─────────────────────────────────────────────────────────────────────
# Schema 转换
# ─────────────────────────────────────────────────────────────────────

def _to_schema(tool_info) -> dict:
    params = getattr(tool_info, "inputSchema", None) or {
        "type": "object", "properties": {}, "required": [],
    }
    return {
        "type": "function",
        "function": {
            "name": tool_info.name,
            "description": tool_info.description or "",
            "parameters": params,
        },
    }


# ─────────────────────────────────────────────────────────────────────
# 工具执行
# ─────────────────────────────────────────────────────────────────────

async def execute_tool(mcp: MyMCPClient, tool_call: dict) -> str:
    name = tool_call["function"]["name"]
    raw_args = tool_call["function"]["arguments"]
    args = json.loads(raw_args) if raw_args and raw_args.strip() else {}

    print(f"\n  [tool] {name}({_preview_args(args)})")

    if name == "save_memory":
        result = execute_save_memory_tool(args)
        print(f"  [memory] {result}")
        return result

    try:
        call_result = await mcp.call_tool(name, arguments=args)
        raw = call_result.result
        content_list = getattr(raw, "content", None)
        if content_list is not None:
            parts = [getattr(item, "text", str(item)) for item in content_list]
            result_str = "\n".join(parts)
        elif isinstance(raw, str):
            result_str = raw
        else:
            result_str = json.dumps(raw, default=str, ensure_ascii=False)
    except Exception as e:
        result_str = f"Error calling tool {name!r}: {e}"

    if len(result_str) > 6000:
        result_str = result_str[:3000] + f"\n…(截断，共 {len(result_str)} 字)…\n" + result_str[-500:]

    print(f"  [result] {result_str[:200]}{'...' if len(result_str) > 200 else ''}")
    return result_str


def _preview_args(args: dict) -> str:
    parts = []
    for k, v in args.items():
        v_str = str(v)
        if len(v_str) > 60:
            v_str = v_str[:57] + "..."
        parts.append(f"{k}={v_str!r}")
    return ", ".join(parts)


# ─────────────────────────────────────────────────────────────────────
# 核心 LLM 调用（流式，返回文本 + 工具调用列表）
# ─────────────────────────────────────────────────────────────────────

def _llm_stream(messages: list[dict], tool_schemas: list[dict]) -> tuple[str, list[dict]]:
    """
    单次流式 LLM 调用。
    返回 (full_content, tool_calls_list)。
    规划阶段传 tool_schemas=[] 可禁止工具调用。
    """
    model = os.environ.get("MODEL", "deepseek-chat")

    kwargs: dict = dict(
        model=model,
        messages=messages,
        stream=True,
    )
    if tool_schemas:
        kwargs["tools"] = tool_schemas
        kwargs["tool_choice"] = "auto"

    stream = llm.chat.completions.create(**kwargs)  # type: ignore

    full_content = ""
    raw_tool_calls: dict[int, dict] = {}

    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        if delta.content:
            print(delta.content, end="", flush=True)
            full_content += delta.content

        if delta.tool_calls:
            for tc in delta.tool_calls:
                if not tc.function:
                    continue
                idx = tc.index
                if idx not in raw_tool_calls:
                    raw_tool_calls[idx] = {
                        "id": tc.id or "",
                        "type": "function",
                        "function": {"name": tc.function.name or "", "arguments": ""},
                    }
                if tc.id:
                    raw_tool_calls[idx]["id"] = tc.id
                if tc.function.name:
                    raw_tool_calls[idx]["function"]["name"] = tc.function.name
                if tc.function.arguments:
                    raw_tool_calls[idx]["function"]["arguments"] += tc.function.arguments

    print()
    tool_calls = [raw_tool_calls[k] for k in sorted(raw_tool_calls)]
    return full_content, tool_calls


# ─────────────────────────────────────────────────────────────────────
# 阶段一：规划
# ─────────────────────────────────────────────────────────────────────

def run_planning(
    user_message: str,
    history: list[dict],
    base_system: str,
) -> str:
    """
    让 LLM 生成执行计划（不使用工具，纯文本输出）。
    返回计划文本，history 不被修改（计划确认前不写入正式 history）。
    """
    print(f"\n{'═' * 50}")
    print("[PLANNING] 正在生成执行计划...")
    print("─" * 50)

    # 构建规划专用消息序列（临时，不修改主 history）
    planning_system = base_system + "\n\n" + PLANNING_PROMPT
    planning_messages = (
        [{"role": "system", "content": planning_system}]
        + [m for m in history if m["role"] != "system"]  # 带上历史上下文
        + [{"role": "user", "content": user_message}]
    )

    # 规划阶段禁用工具调用，只要纯文本计划
    plan_text, _ = _llm_stream(planning_messages, tool_schemas=[])

    print(f"\n{'─' * 50}")
    return plan_text


# ─────────────────────────────────────────────────────────────────────
# 阶段二：执行
# ─────────────────────────────────────────────────────────────────────

async def run_executing(
    task: TaskState,
    history: list[dict],
    mcp: MyMCPClient,
    tool_schemas: list[dict],
) -> tuple[str, list[dict]]:
    """
    按照 task.plan 执行工具调用序列。
    history 会被原地修改（写入 user/assistant/tool 消息）。
    返回 (最终回复, 更新后的 history)。
    """
    model = os.environ.get("MODEL", "deepseek-chat")

    # 压缩检查
    history, compressed = compress_if_needed(history, llm, model)
    if compressed:
        print("[memory-A] 对话历史已压缩 ✓")

    # 把"用户请求 + 已确认计划"写入 history
    combined_user_msg = (
        f"{task.user_request}\n\n"
        f"[已确认执行以下计划]\n{task.plan}"
    )
    history.append({"role": "user", "content": combined_user_msg})

    full_content = ""

    print(f"\n{'═' * 50}")
    print("[EXECUTING] 开始执行...")

    for iteration in range(MAX_ITERATIONS):
        task.current_step = iteration
        progress = task.progress_bar() or f"第 {iteration + 1} 轮"
        print(f"\n{'─' * 50}")
        print(f"[iteration {iteration + 1}] {progress}")

        # 在每次迭代的 system 消息里注入当前进度
        executing_system = history[0]["content"] + "\n\n" + EXECUTING_PROMPT_TEMPLATE.format(
            plan=task.plan,
            progress=progress,
        )
        messages_with_system = (
            [{"role": "system", "content": executing_system}]
            + history[1:]
        )

        full_content, tool_calls = _llm_stream(messages_with_system, tool_schemas)

        assistant_msg: dict = {"role": "assistant", "content": full_content}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        history.append(assistant_msg)

        if not tool_calls:
            # 没有工具调用说明 Agent 认为执行完毕
            break

        # 并发执行所有工具
        results = await asyncio.gather(
            *[execute_tool(mcp, tc) for tc in tool_calls],
            return_exceptions=True,
        )
        for tc, res in zip(tool_calls, results):
            content = str(res) if isinstance(res, Exception) else res
            task.step_results.append(f"{tc['function']['name']}: {content[:200]}")
            history.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": content,
            })

    task.final_output = full_content
    task.status = TaskStatus.DONE
    return full_content, history


# ─────────────────────────────────────────────────────────────────────
# 直接执行（跳过规划，兼容简单查询）
# ─────────────────────────────────────────────────────────────────────

async def run_direct(
    user_message: str,
    history: list[dict],
    mcp: MyMCPClient,
    tool_schemas: list[dict],
) -> tuple[str, list[dict]]:
    """对于不需要规划的消息，直接走 agentic loop。"""
    model = os.environ.get("MODEL", "deepseek-chat")
    history, compressed = compress_if_needed(history, llm, model)
    if compressed:
        print("[memory-A] 对话历史已压缩 ✓")

    history.append({"role": "user", "content": user_message})
    full_content = ""

    for iteration in range(MAX_ITERATIONS):
        print(f"\n{'─' * 50}")
        print(f"[iteration {iteration + 1}] calling LLM...")

        full_content, tool_calls = _llm_stream(history, tool_schemas)

        assistant_msg: dict = {"role": "assistant", "content": full_content}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        history.append(assistant_msg)

        if not tool_calls:
            break

        results = await asyncio.gather(
            *[execute_tool(mcp, tc) for tc in tool_calls],
            return_exceptions=True,
        )
        for tc, res in zip(tool_calls, results):
            history.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": str(res) if isinstance(res, Exception) else res,
            })

    return full_content, history


# ─────────────────────────────────────────────────────────────────────
# 主循环
# ─────────────────────────────────────────────────────────────────────

def _print_banner():
    print("=" * 50)
    print("  T-code step-06  Plan-then-Execute 模式")
    print("=" * 50)
    print("  提示：")
    print("  - 复杂任务（修改/重构/实现）→ 自动触发规划")
    print("  - 规划后输入 y 确认，n 取消")
    print("  - 以 ! 开头强制跳过规划直接执行")
    print("  - exit / quit / q 退出")
    print("=" * 50)


async def main_async():
    _print_banner()
    init_llm()

    # ── 持久记忆 ─────────────────────────────────────────────────────
    memory_content = load_memory(DEFAULT_MEMORY_PATH)
    if memory_content:
        print(f"[memory-B] 已加载持久记忆（{len(memory_content)} 字）✓")
    else:
        print("[memory-B] 暂无持久记忆，首次运行")

    system_prompt = build_system_prompt_with_memory(BASE_SYSTEM_PROMPT)

    # ── MCP 初始化 ────────────────────────────────────────────────────
    mcp_client = MyMCPClient()
    print("\n[MCP] 正在拉取工具列表...")
    tool_infos = await mcp_client.list_tools()
    mcp_schemas = [_to_schema(t) for t in tool_infos]
    all_tool_schemas = mcp_schemas + [SAVE_MEMORY_TOOL_SCHEMA]
    tool_names = [s["function"]["name"] for s in all_tool_schemas]
    print(f"[MCP] 共加载 {len(all_tool_schemas)} 个工具: {tool_names}\n")

    # ── 对话历史 ──────────────────────────────────────────────────────
    history: list[dict] = [{"role": "system", "content": system_prompt}]

    # ── 当前任务状态 ──────────────────────────────────────────────────
    task = TaskState()

    # ── 主循环 ────────────────────────────────────────────────────────
    while True:
        # 根据任务状态显示不同提示符
        if task.status == TaskStatus.PLANNING:
            prompt_str = "确认计划？[y/n] --> "
        else:
            prompt_str = "you --> "

        try:
            user_input = input(prompt_str)
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        user_input = user_input.strip()
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q"):
            print("Bye!")
            break

        # ── 处于 PLANNING 状态：等待用户确认 ─────────────────────────
        if task.status == TaskStatus.PLANNING:
            if user_input.lower() in ("y", "yes", "确认", "好", "ok"):
                task.status = TaskStatus.CONFIRMED
                print("\n[✓] 计划已确认，开始执行...\n")
                response, history = await run_executing(
                    task, history, mcp_client, all_tool_schemas
                )
                print(f"\n{'═' * 50}")
                print(f"[DONE] 任务完成")
                print(f"【Assistant】: {response}\n")
                task.reset()

            elif user_input.lower() in ("n", "no", "取消", "算了", "cancel"):
                task.status = TaskStatus.ABORTED
                task.abort_reason = "用户取消"
                print("[✗] 计划已取消\n")
                task.reset()

            else:
                # 用户对计划有追问或要求修改
                print("\n[PLANNING] 根据你的反馈修订计划...\n")
                revised_plan = run_planning(
                    task.user_request + f"\n\n用户反馈：{user_input}",
                    history,
                    system_prompt,
                )
                task.plan = revised_plan
                # 保持 PLANNING 状态，等待下一轮确认
            continue

        # ── 正常输入：判断是否需要规划 ───────────────────────────────
        # "!" 前缀：强制跳过规划
        actual_message = user_input[1:].strip() if user_input.startswith("!") else user_input
        force_direct = user_input.startswith("!")

        if not force_direct and should_plan(actual_message):
            # 生成计划，进入 PLANNING 状态
            task.reset()
            task.user_request = actual_message
            task.status = TaskStatus.PLANNING

            plan = run_planning(actual_message, history, system_prompt)
            task.plan = plan

            print(f"\n{'─' * 50}")
            print("[提示] 输入 y 确认执行，n 取消，或直接输入反馈修改计划")

        else:
            # 直接执行
            response, history = await run_direct(
                actual_message, history, mcp_client, all_tool_schemas
            )
            print(f"\n【Assistant】: {response}\n")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()