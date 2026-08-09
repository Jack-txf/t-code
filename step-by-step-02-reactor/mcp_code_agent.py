import asyncio
import json
import os
from typing import Optional
from mymcp.tmcp_client import MyMCPClient
# 1.导入环境变量
from load_env import load_env
load_env()
# 一些东西
SYSTEM_PROMPT = """\
你是一个计算机专家，同时还是一个高级软件架构师，精通各种编程语言的开发。
"""
MAX_ITERATIONS = 15
# 2.─────────────────────────────────────────
# LLM 初始化
# ─────────────────────────────────────────
from openai import OpenAI
llm: Optional[OpenAI] = None
def init_llm() -> OpenAI | None:
    global llm
    llm = OpenAI(
        base_url=os.environ["DEEPSEEK_BASE_URL"],
        api_key=os.environ["DEEPSEEK_API_KEY"],
    )
    return llm
# ─────────────────────────────────────────
# MCP tool schema 转换
# ─────────────────────────────────────────
def _tool_info_to_schema(tool_info) -> dict:
    """将 MCP ToolInfo 转换为 OpenAI function-calling schema 格式。"""
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
# =========================【核心方法】=========================【start】
async def execute_tool(mcp: MyMCPClient, tool_call: dict) -> str:
    """通过 MCP client 执行单个工具调用，返回字符串结果。"""
    name = tool_call["function"]["name"]
    raw_args = tool_call["function"]["arguments"]
    args = json.loads(raw_args) if raw_args and raw_args.strip() else {}
    print(f"\n  [tool] {name}({args})")
    try:
        call_result = await mcp.call_tool(name, arguments=args)
        print(f"工具执行结果：{name}({args}): {call_result}")
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
    return result_str
async def run_agent_loop(
        user_message: str,
        history: list[dict],
        mcp: MyMCPClient,
        tool_schemas: list[dict],
    ) -> tuple[str, list[dict]]:
    # 对话记忆处理
    user_msg = {"role":"user", "content": user_message}
    history.append(user_msg)
    full_content = ""

    # 循环迭代
    for iteration in range(MAX_ITERATIONS):
        print(f"\n{'─' * 50}")
        print(f"[iteration {iteration + 1}] calling LLM...")
        print("======本轮送给大模型的message列表: ")
        for msg in history:
            print(msg)
        print("==========================message: ")
        # 流式调用
        stream = llm.chat.completions.create(  # type: ignore[arg-type]
            model=os.environ.get("MODEL", "deepseek-chat"),
            messages=history,
            tools=tool_schemas,
            tool_choice="auto",
            stream=True,
        )
        # AI返回说需要工具调用
        ai_use_tools_dict: dict[int, dict] = {}
        # 本轮迭代，AI的响应
        now_iterator_aiResponse = ""
        # 你好，帮我查看一下当前目录里面有什么文件？
        for chunk in stream:
            print(chunk)
            print("---------")
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            # AI正式回应的
            if delta.content:
                print(delta.content, end="", flush=True)
                now_iterator_aiResponse += delta.content
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    if not tc.function:
                        continue
                    # 用 index 作为 key，支持同一轮多工具调用
                    idx = tc.index
                    if idx not in ai_use_tools_dict:
                        ai_use_tools_dict[idx] = {
                            "id": tc.id or "",
                            "type": "function",
                            "function": {
                                "name": tc.function.name or "",
                                "arguments": "",
                            }
                        }
                    # id 和 name 只在第一个分片出现，后续补充 arguments
                    if tc.id:
                        ai_use_tools_dict[idx]["id"] = tc.id
                    if tc.function.name:
                        ai_use_tools_dict[idx]["function"]["name"] = tc.function.name
                    if tc.function.arguments:
                        ai_use_tools_dict[idx]["function"]["arguments"] += tc.function.arguments
        # dict变成list
        ai_use_tools_list = [ ai_use_tools_dict[k] for k in ai_use_tools_dict.keys()]
        #=======
        assistant_msg: dict = {"role": "assistant", "content": now_iterator_aiResponse}
        if ai_use_tools_list:
            assistant_msg["tool_calls"] = ai_use_tools_list
        full_content += now_iterator_aiResponse # 保存本次大模型返回的
        # 工具调用的message也加进去
        history.append(assistant_msg)
        if not ai_use_tools_list:
            break
        # 【并发执行所有工具】
        results = await asyncio.gather(
            *[execute_tool(mcp, tc) for tc in ai_use_tools_list],
            return_exceptions=True,
        )
        for tc, res in zip(ai_use_tools_list, results):
            content = str(res) if isinstance(res, Exception) else res
            tool_result_msg = {
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": content,
            }
            history.append(tool_result_msg)
    return full_content, history
# =========================【核心方法】=========================【end】
async def main_async():
    print("=========== t-code MCP模式启动 ==========")
    init_llm()
    # 初始化MCP client并拉取所有tool schemas
    mcp_client = MyMCPClient()
    print("\n[MCP] 正在拉取工具列表...")
    tool_infos = await mcp_client.list_tools()
    tool_schemas = [_tool_info_to_schema(t) for t in tool_infos]
    print(f"[MCP] 共加载 {len(tool_schemas)} 个工具: {[t['function']['name'] for t in tool_schemas]}\n")
    # 3. 对话历史
    history: list[dict] = []
    sys_msg = {"role":"system", "content": SYSTEM_PROMPT}
    history.append(sys_msg)
    # 4. 主循环
    while True:
        try:
            user_input = input("you --> ")
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break
        if not user_input.strip():
            continue
        if user_input.lower() in ("exit", "quit", "q"):
            print("Bye!")
            break
        response, history = await run_agent_loop(user_input, history, mcp_client, tool_schemas)
        print(f"\n【Assistant】: {response}\n")
def main():
    asyncio.run(main_async())
if "__main__" == __name__:
    main()