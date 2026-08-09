"""
step-by-step-04-plan — Plan-then-Execute 模式 Agent（重构版）

整体架构
--------
1. 启动阶段
   .env → 初始化 LLM → 读取持久记忆注入 system prompt
   → 连接 MCP server 拉取工具 schema（外加本地 save_memory 工具）

2. REPL 主循环：根据任务状态机 (TaskState) 决定走哪条路径
   - 简单/查询类输入   → run_direct()    直接 agentic loop
   - 复杂/执行类输入   → run_planning()  生成计划，进入 PLANNING 状态等待确认
   - 计划确认 (y)      → run_executing() 按计划执行，实时跟踪进度
   - "!" 前缀          → 强制跳过规划，直接执行

3. 双层记忆
   - 短期：history 轮数超阈值时用 LLM 摘要压缩老消息 (memory_compressor)
   - 长期：save_memory 工具写入 Markdown 文件，启动时注入 (memory_persistence)

运行前需先启动 MCP server：
    python step-by-step-04-plan/mymcp/tmcp_server.py
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path

# ── 路径修正 ─────────────────────────────────────────────────────────
# 脚本直接运行时，sys.path 里只有本文件所在目录，
# 而 load_env 位于仓库根目录，需要手动补上。
_PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_PROJECT_ROOT))

from load_env import load_env

load_env()

from openai import OpenAI

from mymcp.tmcp_client import MyMCPClient
from memory.memory_compressor import compress_if_needed
from memory.memory_persistence import (
    build_system_prompt_with_memory,
    execute_save_memory_tool,
    SAVE_MEMORY_TOOL_SCHEMA,
    DEFAULT_MEMORY_PATH,
    load_memory,
)
from plan.task_state import TaskState, TaskStatus, should_plan, parse_plan_steps
from prompt.tcode_prompt import (
    BASE_SYSTEM_PROMPT,
    PLANNING_PROMPT,
    EXECUTING_PROMPT_TEMPLATE,
)

# =====================================================================
# 常量与 LLM 客户端
# =====================================================================

MAX_ITERATIONS = 15         # 单个任务内 agentic loop 的最大轮数（防止工具调用死循环）
TOOL_RESULT_MAX_LEN = 6000  # 单条工具结果的最大长度，超出部分中间截断，保护上下文窗口

llm: OpenAI | None = None


def init_llm() -> OpenAI:
    """初始化全局 LLM 客户端（DeepSeek 兼容的 OpenAI 接口）。"""
    global llm
    llm = OpenAI(
        base_url=os.environ["DEEPSEEK_BASE_URL"],
        api_key=os.environ["DEEPSEEK_API_KEY"],
    )
    return llm


def _model_name() -> str:
    return os.environ.get("MODEL", "deepseek-chat")


# =====================================================================
# 1. MCP 工具 schema 转换
# =====================================================================

def _tool_info_to_schema(tool_info) -> dict:
    """将 MCP ToolInfo 转换为 OpenAI function-calling schema 格式。

    MCP 工具的 inputSchema 本身就是 JSON Schema，可直接作为 parameters；
    缺失时兜底为空 object（无参数工具）。
    """
    parameters = getattr(tool_info, "inputSchema", None) or {
        "type": "object",
        "properties": {},
        "required": [],
    }
    return {
        "type": "function",
        "function": {
            "name": tool_info.name,
            "description": tool_info.description or "",
            "parameters": parameters,
        },
    }


# =====================================================================
# 2. 工具执行
# =====================================================================

def _preview_args(args: dict) -> str:
    """生成工具参数的简短预览（单个值超过 60 字符截断），用于终端日志。"""
    parts = []
    for k, v in args.items():
        v_str = str(v)
        if len(v_str) > 60:
            v_str = v_str[:57] + "..."
        parts.append(f"{k}={v_str!r}")
    return ", ".join(parts)


async def execute_tool(mcp: MyMCPClient, tool_call: dict) -> str:
    """执行单个工具调用，返回字符串结果。

    两类工具：
      - save_memory：本地内置工具，直接读写记忆文件，不走 MCP；
      - 其余工具：通过 MCP client 远程调用，返回值可能是 content 列表、
        字符串或结构化 dict，统一归一化为字符串。
    """
    name = tool_call["function"]["name"]
    raw_args = tool_call["function"]["arguments"]
    args = json.loads(raw_args) if raw_args and raw_args.strip() else {}
    print(f"\n  [tool] {name}({_preview_args(args)})")

    # 本地工具：持久记忆写入
    if name == "save_memory":
        result = execute_save_memory_tool(args)
        print(f"  [memory] {result}")
        return result

    # MCP 远程工具
    try:
        call_result = await mcp.call_tool(name, arguments=args)
        raw = call_result.result
        content_list = getattr(raw, "content", None)
        if content_list is not None:
            # fastmcp 返回的 content 是 TextContent 等对象列表，逐个取 text
            parts = [getattr(item, "text", str(item)) for item in content_list]
            result_str = "\n".join(parts)
        elif isinstance(raw, str):
            result_str = raw
        else:
            result_str = json.dumps(raw, default=str, ensure_ascii=False)
    except Exception as e:
        # 工具失败不中断 loop，把错误信息反馈给 LLM 让它自行决策
        result_str = f"Error calling tool {name!r}: {e}"

    # 超长结果中间截断，避免撑爆上下文
    if len(result_str) > TOOL_RESULT_MAX_LEN:
        result_str = (
            result_str[:3000]
            + f"\n…(截断，共 {len(result_str)} 字)…\n"
            + result_str[-500:]
        )

    print(f"  [result] {result_str[:200]}{'...' if len(result_str) > 200 else ''}")
    return result_str


# =====================================================================
# 3. 核心 LLM 流式调用
# =====================================================================

def _llm_stream(messages: list[dict], tool_schemas: list[dict]) -> tuple[str, list[dict]]:
    """单次流式 LLM 调用，返回 (完整文本, 工具调用列表)。

    流式协议要点（DeepSeek/OpenAI 兼容）：
      - 文本内容以 delta.content 逐片到达，直接拼接并实时打印；
      - 工具调用以 delta.tool_calls 分片到达：id 和 function.name 只在
        第一个分片出现，后续分片只携带 arguments 片段，需要按 index
        累积拼装（同一轮可能并发返回多个工具调用，index 是区分 key）。
      - tool_schemas 传空列表时不带 tools 参数，用于规划阶段禁止工具调用。
    """
    kwargs: dict = dict(model=_model_name(), messages=messages, stream=True)
    if tool_schemas:
        kwargs["tools"] = tool_schemas
        kwargs["tool_choice"] = "auto"

    stream = llm.chat.completions.create(**kwargs)  # type: ignore[union-attr]

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
    # 按 index 排序还原成列表，保证多工具调用的顺序稳定
    tool_calls = [raw_tool_calls[k] for k in sorted(raw_tool_calls)]
    return full_content, tool_calls


# =====================================================================
# 4. 通用 agentic loop（直接执行 / 计划执行共用的主循环）
# =====================================================================

async def _agentic_loop(
    history: list[dict],
    mcp: MyMCPClient,
    tool_schemas: list[dict],
    *,
    system_builder=None,
    on_assistant=None,
) -> tuple[str, list[dict]]:
    """标准的"LLM ↔ 工具"循环：LLM 输出 → 执行工具 → 结果回填 → 再调 LLM，
    直到 LLM 不再发起工具调用（认为任务完成）或达到 MAX_ITERATIONS。

    两个可选钩子，供执行阶段定制行为：
      - system_builder(iteration) -> str：每轮动态生成 system 消息
        （执行阶段用它把计划和实时进度注入 system，覆盖 history[0]）；
      - on_assistant(text)：每轮 assistant 文本产出后的回调
        （执行阶段用它解析 [STEP N] 标记更新进度）。
    """
    full_content = ""

    for iteration in range(MAX_ITERATIONS):
        print(f"\n{'─' * 50}")
        print(f"[iteration {iteration + 1}] calling LLM...")

        # 有 system_builder 时，用它生成的动态 system 替换 history 首条的静态 system
        if system_builder is not None:
            messages = (
                [{"role": "system", "content": system_builder(iteration)}]
                + history[1:]
            )
        else:
            messages = history

        full_content, tool_calls = _llm_stream(messages, tool_schemas)

        if on_assistant is not None:
            on_assistant(full_content)

        # assistant 消息落盘到 history（带不带 tool_calls 两种形态）
        assistant_msg: dict = {"role": "assistant", "content": full_content}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        history.append(assistant_msg)

        # 没有工具调用 = LLM 认为任务完成，退出循环
        if not tool_calls:
            break

        # 同一轮的多个工具调用并发执行，结果按顺序回填为 role=tool 消息
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


def _compress_history_if_needed(history: list[dict]) -> list[dict]:
    """短期记忆保护：history 轮数超阈值时，用 LLM 摘要替换老消息。

    注意返回的是新列表，调用方需要重新赋值（压缩不会原地修改）。
    """
    history, compressed = compress_if_needed(history, llm, _model_name())
    if compressed:
        print("[memory-A] 对话历史已压缩 ✓")
    return history


# =====================================================================
# 5. 阶段一：规划（PLANNING）
# =====================================================================

def run_planning(user_message: str, history: list[dict], base_system: str) -> str:
    """让 LLM 生成 Markdown 执行计划，返回计划文本。

    关键设计：
      - 使用临时消息序列，不写入正式 history —— 计划在用户确认前
        不属于正式对话，避免用户取消计划后 history 里残留废弃计划；
      - tool_schemas 传空，规划阶段只要纯文本计划，禁止任何工具调用。
    """
    print(f"\n{'═' * 50}")
    print("[PLANNING] 正在生成执行计划...")
    print("─" * 50)

    planning_system = base_system + "\n\n" + PLANNING_PROMPT
    planning_messages = (
        [{"role": "system", "content": planning_system}]
        + [m for m in history if m["role"] != "system"]  # 带上历史上下文
        + [{"role": "user", "content": user_message}]
    )

    plan_text, _ = _llm_stream(planning_messages, tool_schemas=[])

    print(f"\n{'─' * 50}")
    return plan_text


# =====================================================================
# 6. 阶段二：执行（EXECUTING）
# =====================================================================

# 执行阶段要求 LLM 在每轮回复开头输出 [STEP N] 标记（见 EXECUTING_PROMPT_TEMPLATE），
# Agent 用这个标记把"计划步骤"和"执行进度"对应起来。
_STEP_MARK_RE = re.compile(r"\[STEP\s+(\d+)\s*]")


def _update_progress(task: TaskState, assistant_text: str) -> None:
    """从 LLM 回复中解析 [STEP N] 标记，更新任务进度。

    语义约定：N 表示 LLM "当前正在执行" 的步骤编号（1-based），
    因此前 N-1 步视为已完成，即 current_step = N - 1。
    解析失败（LLM 没按约定输出）时保持原进度，不倒退。
    """
    if not task.steps:
        return
    m = _STEP_MARK_RE.search(assistant_text)
    if m:
        current = int(m.group(1))
        task.current_step = min(max(current - 1, 0), len(task.steps))


async def run_executing(
    task: TaskState,
    history: list[dict],
    mcp: MyMCPClient,
    tool_schemas: list[dict],
) -> tuple[str, list[dict]]:
    """按照 task.plan 执行计划。

    与 run_direct 的区别：
      1. 把"用户请求 + 已确认计划"合并写入 history，让 LLM 明确执行依据；
      2. 每轮通过 system_builder 把计划和实时进度注入 system 消息，
         防止长执行过程中 LLM "忘记"计划；
      3. 通过 on_assistant 解析 [STEP N] 标记，驱动真实进度条。
    """
    history = _compress_history_if_needed(history)

    combined_user_msg = (
        f"{task.user_request}\n\n"
        f"[已确认执行以下计划]\n{task.plan}"
    )
    history.append({"role": "user", "content": combined_user_msg})

    base_system = history[0]["content"]

    def build_system(iteration: int) -> str:
        progress = task.progress_bar() or f"第 {iteration + 1} 轮"
        return base_system + "\n\n" + EXECUTING_PROMPT_TEMPLATE.format(
            plan=task.plan,
            progress=progress,
        )

    def track_progress(assistant_text: str) -> None:
        _update_progress(task, assistant_text)
        if task.steps:
            print(f"  [progress] {task.progress_bar()}")

    print(f"\n{'═' * 50}")
    print("[EXECUTING] 开始执行...")

    full_content, history = await _agentic_loop(
        history, mcp, tool_schemas,
        system_builder=build_system,
        on_assistant=track_progress,
    )

    # 任务收尾：进度拉满，记录最终输出
    task.current_step = len(task.steps)
    task.final_output = full_content
    task.status = TaskStatus.DONE
    return full_content, history


# =====================================================================
# 7. 直接执行（跳过规划，用于简单查询类输入）
# =====================================================================

async def run_direct(
    user_message: str,
    history: list[dict],
    mcp: MyMCPClient,
    tool_schemas: list[dict],
) -> tuple[str, list[dict]]:
    """不需要规划的消息，直接走通用 agentic loop。"""
    history = _compress_history_if_needed(history)
    history.append({"role": "user", "content": user_message})
    return await _agentic_loop(history, mcp, tool_schemas)


# =====================================================================
# 8. REPL 主循环
# =====================================================================

def _print_banner():
    print("=" * 50)
    print("  T-code step-04  Plan-then-Execute 模式")
    print("=" * 50)
    print("  提示：")
    print("  - 复杂任务（修改/重构/实现）→ 自动触发规划")
    print("  - 规划后输入 y 确认，n 取消，或直接输入反馈修订计划")
    print("  - 以 ! 开头强制跳过规划直接执行")
    print("  - exit / quit / q 退出")
    print("=" * 50)


async def _handle_planning_input(
    user_input: str,
    task: TaskState,
    history: list[dict],
    mcp: MyMCPClient,
    tool_schemas: list[dict],
    system_prompt: str,
) -> list[dict]:
    """PLANNING 状态下的输入分发：y 确认 / n 取消 / 其它视为修订反馈。

    返回（可能被压缩替换过的）history。
    """
    lowered = user_input.lower()

    # ── y：确认计划，进入执行阶段 ─────────────────────────────────
    if lowered in ("y", "yes", "确认", "好", "ok"):
        task.status = TaskStatus.CONFIRMED
        # 从 Markdown 计划中解析步骤列表，驱动执行阶段的进度条
        task.steps = parse_plan_steps(task.plan)
        if task.steps:
            print(f"\n[plan] 解析出 {len(task.steps)} 个步骤")
        print("[✓] 计划已确认，开始执行...\n")
        response, history = await run_executing(task, history, mcp, tool_schemas)
        print(f"\n{'═' * 50}")
        print("[DONE] 任务完成")
        print(f"【Assistant】: {response}\n")
        task.reset()
        return history

    # ── n：取消计划 ──────────────────────────────────────────────
    if lowered in ("n", "no", "取消", "算了", "cancel"):
        task.status = TaskStatus.ABORTED
        task.abort_reason = "用户取消"
        print("[✗] 计划已取消\n")
        task.reset()
        return history

    # ── 其它输入：用户对计划的修订反馈，带反馈重新规划 ─────────────
    print("\n[PLANNING] 根据你的反馈修订计划...\n")
    task.plan = run_planning(
        task.user_request + f"\n\n用户反馈：{user_input}",
        history,
        system_prompt,
    )
    # 保持 PLANNING 状态，等待下一轮确认
    return history


async def main_async():
    _print_banner()
    init_llm()

    # ── 长期记忆：读取持久记忆文件，注入 system prompt ─────────────
    memory_content = load_memory(DEFAULT_MEMORY_PATH)
    if memory_content:
        print(f"[memory-B] 已加载持久记忆（{len(memory_content)} 字）✓")
    else:
        print("[memory-B] 暂无持久记忆，首次运行")
    system_prompt = build_system_prompt_with_memory(BASE_SYSTEM_PROMPT)

    # ── MCP 初始化：拉取所有 server 的工具，外加本地 save_memory ───
    mcp_client = MyMCPClient()
    print("\n[MCP] 正在拉取工具列表...")
    tool_infos = await mcp_client.list_tools()
    tool_schemas = [_tool_info_to_schema(t) for t in tool_infos] + [SAVE_MEMORY_TOOL_SCHEMA]
    tool_names = [s["function"]["name"] for s in tool_schemas]
    print(f"[MCP] 共加载 {len(tool_schemas)} 个工具: {tool_names}\n")

    # ── 对话历史（首条固定为 system）与当前任务状态 ────────────────
    history: list[dict] = [{"role": "system", "content": system_prompt}]
    task = TaskState()

    while True:
        # PLANNING 状态下切换提示符，引导用户确认/取消
        prompt_str = "确认计划？[y/n] --> " if task.status == TaskStatus.PLANNING else "you --> "

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

        # ── 分支一：计划待确认状态 ─────────────────────────────────
        if task.status == TaskStatus.PLANNING:
            history = await _handle_planning_input(
                user_input, task, history, mcp_client, tool_schemas, system_prompt
            )
            continue

        # ── 分支二：正常输入，"!" 前缀强制跳过规划 ─────────────────
        actual_message = user_input[1:].strip() if user_input.startswith("!") else user_input
        force_direct = user_input.startswith("!")

        if not force_direct and should_plan(actual_message):
            # 复杂任务：生成计划，进入 PLANNING 状态等待确认
            task.reset()
            task.user_request = actual_message
            task.status = TaskStatus.PLANNING
            task.plan = run_planning(actual_message, history, system_prompt)
            print(f"\n{'─' * 50}")
            print("[提示] 输入 y 确认执行，n 取消，或直接输入反馈修改计划")
        else:
            # 简单任务：直接执行
            response, history = await run_direct(
                actual_message, history, mcp_client, tool_schemas
            )
            print(f"\n【Assistant】: {response}\n")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()