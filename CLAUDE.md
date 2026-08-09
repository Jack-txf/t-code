# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup & Running

- **Python version**: 3.12
- **Virtual environment**: `.venv/` in the repo root
- **Package manager**: `pyproject.toml` exists with `setuptools` backend

To install dependencies:

```bash
source .venv/Scripts/activate
pip install -e .
```

To run the agents:

```bash
# Step 1 — local tools only
python step-by-step-01/simple-agentic-loop.py

# Step 2 — MCP client test
python step-by-step-02/mcp_client/tcode_mcp_client.py

# Step 2-reactor — full MCP agent
python step-by-step-02-reactor/mcp_code_agent.py

# MCP server (run in another terminal)
python step-by-step-02/remote_mcp_server/FastMcp_Server.py
# or
python step-by-step-02-reactor/mymcp/tmcp_server.py
```

Required environment variables in `.env` at repo root:
- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_API_KEY`
- `DEEPSEEK_MODEL` (optional, defaults to `deepseek-chat` via `MODEL` fallback)

## Architecture

### Phase 1 — Local Tools (`step-by-step-01/`)

A single-file REPL agent that uses the OpenAI SDK to call a DeepSeek-compatible API with streaming and function calling.

- **Entry point**: `main()` reads user input in a loop and calls `run_agent_loop()`.
- **Streaming**: Uses `client.chat.completions.create(..., stream=True)`. The stream handler accumulates text content and tool-call fragments by `index`.
- **Agent loop**: `run_agent_loop()` appends the user's message to `messages` (working copy for the API) and `history` (persistent conversation state). It loops up to `MAX_ITERATIONS = 20`. If the model returns `tool_calls`, they are executed and their results appended as `role: "tool"` messages before the next LLM call.
- **Truncation**: Tool outputs longer than 4000 chars are truncated in the middle to avoid context overflow.

**Files:**
- `simple-agentic-loop.py` — Agent 主程序
- `tools.py` — 本地工具注册表（bash, read_file, write_file, list_dir）
- `架构.py` — 骨架/模板文件

### Phase 2 — MCP Integration (`step-by-step-02/`)

Introduces the Model Context Protocol (MCP) via `fastmcp`.

**Server (`remote_mcp_server/FastMcp_Server.py`)**
- `FastMCP` server exposing the same 4 tools over HTTP (`transport="http"`, port 9000).

**Client (`mcp_client/`)**
- `tcode_mcp_client.py` — 直接使用 `fastmcp.Client` 连接 `http://127.0.0.1:9000/mcp` 的测试脚本。
- `tcode_mcp_client_upper.py` — `TCodeMCPClient` 封装类，从 `~/.tcode.json` 读取配置，用 `async with` 管理 `Client` 生命周期。

**Config (`config/`)**
- `load_tcode_config.py` — 加载/创建 `~/.tcode.json` 的同步工具函数。

### Phase 2-Reactor — Multi-Server MCP Agent (`step-by-step-02-reactor/`)

A more advanced agent that supports multiple MCP servers and better tool result handling.

**Server (`mymcp/tmcp_server.py`)**
- `FastMCP` server with `transport="streamable-http"` (port 9000).
- Tools return structured `dict` instead of raw strings.
- Also defines resources (`config://database`) and prompts.

**Client (`mymcp/tmcp_client.py`)**
- `MyMCPClient` — 支持多 server 的 MCP 客户端封装。
- 使用 `StreamableHttpTransport` 连接。
- `list_tools()` 并发拉取所有 server 的工具列表，返回带 `server` 字段的 `ToolInfo`。
- `call_tool()` 支持自动路由（不指定 server 时在所有 server 中查找）。
- 所有 `Client` 连接通过 `async with` 上下文管理器管理。

**Agent (`mcp_code_agent.py`)**
- 主程序，使用 `MyMCPClient` 获取工具 schema，通过 OpenAI function-calling 调用 MCP 工具。
- 工具调用结果通过 `execute_tool()` 解析（处理 `content` 列表、字符串、或其他类型的返回）。

### Shared Utilities

- `load_env.py` — 使用 `python-dotenv` 加载 `.env` 到环境变量。
- `test.py` — 打印 `DEEPSEEK_BASE_URL`, `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` 用于快速验证。

## Key Dependencies

- `openai` — OpenAI SDK for DeepSeek-compatible API
- `fastmcp` — MCP server/client framework (`FastMCP`, `Client`, `StreamableHttpTransport`)
- `mcp` — Underlying MCP protocol implementation (transitive dependency)
- `python-dotenv` — `.env` file loading

## Notes

- There is no test suite, linter config, or CI.
- The `.idea/` directory suggests PyCharm/IntelliJ is used for development.
- MCP servers must be started manually before running the agent.
- `fastmcp.Client` requires `async with` for connection lifecycle management; instantiating `Client` does **not** open the transport connection.