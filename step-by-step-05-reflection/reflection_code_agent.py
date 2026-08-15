"""Step 05：在 Step 04 的规划执行之后，加入独立反思与一次自我修复。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
STEP04_DIR = THIS_DIR.parent / "step-by-step-04-plan"
# 直接运行时 Python 会自动加入脚本目录；显式加入后，模块化导入和 IDE 调试也一致。
sys.path.insert(0, str(THIS_DIR))
sys.path.insert(0, str(STEP04_DIR))

import plan_code_agent as step04  # noqa: E402
from memory.memory_persistence import (  # noqa: E402
    DEFAULT_MEMORY_PATH,
    SAVE_MEMORY_TOOL_SCHEMA,
    build_system_prompt_with_memory,
    load_memory,
)
from mymcp.tmcp_client import MyMCPClient  # noqa: E402
from plan.task_state import TaskState, TaskStatus, parse_plan_steps, should_plan  # noqa: E402
from prompt.tcode_prompt import BASE_SYSTEM_PROMPT  # noqa: E402
from reflection.prompts import REPAIR_PROMPT, REVIEW_PROMPT  # noqa: E402
from reflection.reviewer import ReviewResult, build_transcript, format_issues, parse_review  # noqa: E402

MAX_REPAIR_ATTEMPTS = 1


def review_execution(task: TaskState, history: list[dict]) -> ReviewResult:
    """由不带工具的 Reviewer 独立评估执行证据。"""
    prompt = REVIEW_PROMPT.format(
        request=task.user_request,
        plan=task.plan,
        transcript=build_transcript(history),
    )
    kwargs = {
        "model": step04._model_name(),
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    try:
        # DeepSeek 的较新 OpenAI 兼容接口支持该参数；不支持时仍可依靠 prompt + 解析器工作。
        response = step04.llm.chat.completions.create(  # type: ignore[union-attr]
            **kwargs, response_format={"type": "json_object"}
        )
    except Exception as exc:
        print(f"[REFLECTION] JSON mode 不可用，降级为提示词约束：{exc}")
        response = step04.llm.chat.completions.create(**kwargs)  # type: ignore[union-attr]
    result = parse_review(response.choices[0].message.content or "")
    marker = "PASS" if result.passed else "FAIL"
    print(f"\n[REFLECTION] {marker}: {result.summary}")
    for issue in result.issues:
        print(f"  - {issue}")
    return result


async def repair_once(
    task: TaskState,
    review: ReviewResult,
    history: list[dict],
    mcp: MyMCPClient,
    tool_schemas: list[dict],
) -> tuple[str, list[dict]]:
    """将结构化审查意见送回执行 Agent；仍复用 Step 04 的工具循环。"""
    repair_prompt = REPAIR_PROMPT.format(
        request=task.user_request,
        plan=task.plan,
        summary=review.summary,
        issues=format_issues(review.issues),
        repair_instruction=review.repair_instruction,
    )
    history.append({"role": "user", "content": repair_prompt})
    print("\n[REPAIR] 根据 Reviewer 反馈进行一次受限修复...")
    return await step04._agentic_loop(history, mcp, tool_schemas)


async def execute_with_reflection(
    task: TaskState,
    history: list[dict],
    mcp: MyMCPClient,
    tool_schemas: list[dict],
) -> list[dict]:
    _, history = await step04.run_executing(task, history, mcp, tool_schemas)
    review = review_execution(task, history)

    for attempt in range(MAX_REPAIR_ATTEMPTS):
        if review.passed:
            print("[✓] 任务已通过反思验证。")
            return history
        print(f"[REFLECTION] 第 {attempt + 1} 次检查未通过，开始修复。")
        _, history = await repair_once(task, review, history, mcp, tool_schemas)
        review = review_execution(task, history)

    if review.passed:
        print("[✓] 修复后已通过反思验证。")
    else:
        print("[!] 已达到最大修复次数；请根据上述 Reviewer 问题人工处理。")
    return history


def print_banner() -> None:
    print("=" * 58)
    print("  T-code step-05  Reflection（反思与自我修复）")
    print("=" * 58)
    print("  复杂任务：计划确认 → 执行 → Reviewer 验证 → 最多一次修复")
    print("  简单查询：沿用 Step 04 的直接执行路径")
    print("  exit / quit / q 退出")
    print("=" * 58)


async def main_async() -> None:
    print_banner()
    step04.init_llm()
    memory_content = load_memory(DEFAULT_MEMORY_PATH)
    print(f"[memory] {'已加载持久记忆' if memory_content else '暂无持久记忆'}")
    system_prompt = build_system_prompt_with_memory(BASE_SYSTEM_PROMPT)

    mcp = MyMCPClient()
    tool_infos = await mcp.list_tools()
    tool_schemas = [step04._tool_info_to_schema(tool) for tool in tool_infos] + [SAVE_MEMORY_TOOL_SCHEMA]
    print(f"[MCP] 已加载 {len(tool_schemas)} 个工具。")

    history: list[dict] = [{"role": "system", "content": system_prompt}]
    task = TaskState()
    while True:
        try:
            user_input = input("you --> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            return
        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "q"}:
            print("Bye!")
            return

        actual_message = user_input[1:].strip() if user_input.startswith("!") else user_input
        if user_input.startswith("!") or not should_plan(actual_message):
            response, history = await step04.run_direct(actual_message, history, mcp, tool_schemas)
            print(f"\n【Assistant】: {response}\n")
            continue

        task.reset()
        task.user_request = actual_message
        task.status = TaskStatus.PLANNING
        task.plan = step04.run_planning(actual_message, history, system_prompt)
        while task.status == TaskStatus.PLANNING:
            decision = input("确认计划？[y/n/反馈] --> ").strip()
            if decision.lower() in {"y", "yes", "确认", "好", "ok"}:
                task.status = TaskStatus.CONFIRMED
                task.steps = parse_plan_steps(task.plan)
                print(f"[plan] 已解析 {len(task.steps)} 个步骤，开始执行。")
                history = await execute_with_reflection(task, history, mcp, tool_schemas)
                task.reset()
            elif decision.lower() in {"n", "no", "取消", "算了", "cancel"}:
                print("[✗] 计划已取消。")
                task.reset()
            else:
                task.plan = step04.run_planning(f"{task.user_request}\n\n用户反馈：{decision}", history, system_prompt)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
