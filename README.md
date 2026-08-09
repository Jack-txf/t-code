# T-code — 从零手写一个 Coding Agent（渐进式教程）

一个基于 **DeepSeek API（OpenAI 兼容接口）** 的最小化 Agent 教程项目，从最简单的本地工具循环开始，逐步演进到 **MCP 工具协议 → 双层记忆 → Plan-then-Execute 规划执行**，每一步都是一个可独立运行的完整 Agent。

## 演进路线

| 阶段 | 目录 | 核心主题                   | 新增能力 |
| --- | --- |------------------------| --- |
| Step 01 | `step-by-step-01/` | 本地工具的 Agentic Loop     | 流式输出、function calling、工具结果回填循环 |
| Step 02 | `step-by-step-02/` | MCP 协议入门               | FastMCP server/client、工具从本地函数变为远程调用 |
| Step 02-reactor | `step-by-step-02-reactor/` | 多 server MCP Agent【重构】 | 多 server 聚合、工具自动路由、结构化返回 |
| Step 03 | `step-by-step-03-memory/` | 记忆系统                   | 短期摘要压缩 + 长期 Markdown 持久记忆 |
| Step 04 | `step-by-step-04-plan/` | Plan-then-Execute      | 两阶段规划执行、任务状态机、实时进度条 |

每一步都建立在前一步之上，建议按顺序阅读代码。

### Step 01 — 本地工具的 Agentic Loop

单文件 REPL Agent，演示 Agent 的最小内核：**LLM 输出 → 解析 tool_calls → 执行本地工具 → 结果回填 → 再调 LLM**，直到不再调用工具。

- `simple-agentic-loop.py` — 主程序（流式响应按 `index` 拼装 tool_calls 分片，工具结果超 4000 字中间截断）
- `tools.py` — 本地工具注册表：`bash` / `read_file` / `write_file` / `list_dir`

### Step 02 — MCP 协议入门

把 Step 01 的 4 个本地工具搬到 **MCP server** 上，Agent 改为通过 MCP 协议远程调用。

- `remote_mcp_server/FastMcp_Server.py` — FastMCP server（HTTP transport，端口 9000）
- `mcp_client/tcode_mcp_client.py` — 最小 client 测试脚本
- `mcp_client/tcode_mcp_client_upper.py` — `TCodeMCPClient` 封装类，从 `~/.tcode.json` 读配置
- `config/load_tcode_config.py` — 配置加载/创建工具

### Step 02-reactor — 多 server MCP Agent

更完善的 MCP Agent：server 端工具返回结构化 `dict`（另演示了 resources 和 prompts），client 端支持**多 server 并发拉取工具列表、调用时按工具名自动路由**。

- `mymcp/tmcp_server.py` — FastMCP server（streamable-http，端口 9000）
- `mymcp/tmcp_client.py` — `MyMCPClient` 多 server 封装（`StreamableHttpTransport`，`async with` 管理连接生命周期）
- `mcp_code_agent.py` — 主程序，OpenAI function-calling ↔ MCP 工具的完整 agentic loop

### Step 03 — 记忆系统

在 02-reactor 基础上加入**双层记忆**：

- **短期记忆**（`memory/memory_compressor.py`）：对话超过 5 轮时，用 LLM 把老消息压缩成 100~200 字摘要，保留最近 4 轮，防止上下文溢出
- **长期记忆**（`memory/memory_persistence.py`）：内置 `save_memory` 工具（本地执行），记忆按"用户偏好/项目背景/重要结论/操作记录"四个分类写入 `.tcode_memory.md`，下次启动自动注入 system prompt

### Step 04 — Plan-then-Execute

引入**两阶段执行模式**：复杂任务先生成 Markdown 计划，用户确认后才执行。

- 任务状态机：`IDLE → PLANNING → CONFIRMED → EXECUTING → DONE / ABORTED`
- `should_plan()` 启发式路由：执行类输入自动触发规划，查询类直接执行，`!` 前缀强制跳过
- 计划可确认（`y`）/ 取消（`n`）/ 输入反馈让 Agent 修订
- 执行阶段 LLM 以 `[STEP N]` 标记上报进度，终端实时渲染 `[██░░░] 2/5` 进度条
- 详细说明见 [step-by-step-04-plan/README.md](step-by-step-04-plan/README.md)


## 仓库结构

```
t-code/
├── step-by-step-01/           # 本地工具 agentic loop
├── step-by-step-02/           # MCP 入门（server + client + config）
├── step-by-step-02-reactor/   # 多 server MCP Agent
├── step-by-step-03-memory/    # + 双层记忆系统
├── step-by-step-04-plan/      # + Plan-then-Execute 模式
├── load_env.py                # .env 加载（python-dotenv）
├── test.py                    # 环境变量快速验证
├── pyproject.toml             # 项目元数据与依赖
└── 环境说明.md                 # 环境配置说明
```

## 核心依赖

- `openai` — OpenAI SDK，对接 DeepSeek 兼容 API（流式 + function calling）
- `fastmcp` — MCP server/client 框架（`FastMCP` / `Client` / `StreamableHttpTransport`）
- `python-dotenv` — `.env` 环境变量加载

## 贯穿全项目的实现要点

- **流式 tool_calls 拼装**：`id` 和 `function.name` 只在第一个分片出现，后续分片只带 `arguments`，需按 `index` 累积（同一轮可有多个并发工具调用）
- **工具结果保护**：超长结果中间截断，避免撑爆上下文
- **MCP 连接生命周期**：`fastmcp.Client` 实例化不建立连接，必须用 `async with` 管理
- **本地工具与 MCP 工具共存**：如 `save_memory` 这类内置工具在 Agent 端直接执行，不走 MCP
