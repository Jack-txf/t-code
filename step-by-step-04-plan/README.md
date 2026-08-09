# step-by-step-04-plan — Plan-then-Execute 模式 Agent

T-code 教程系列的第 4 步：在 MCP 工具调用的基础上，引入 **"先规划、确认后执行"（Plan-then-Execute）** 的两阶段工作模式，并配备 **双层记忆系统**（短期摘要压缩 + 长期文件持久化）。

## 核心特性

| 特性 | 说明 |
| --- | --- |
| Plan-then-Execute | 复杂任务先生成 Markdown 计划，用户确认（`y`）后才执行；可取消（`n`）或输入反馈让 Agent 修订计划 |
| 智能路由 | `should_plan()` 启发式判断：执行类动词（重构/修改/实现…）→ 规划；查询类动词（查看/列出/是什么…）→ 直接执行；`!` 前缀强制跳过规划 |
| 实时进度条 | 计划确认时从 Markdown 解析步骤列表；执行阶段 LLM 每轮回复以 `[STEP N]` 标记上报进度，终端实时显示 `[██░░░] 2/5` |
| MCP 工具调用 | 通过 fastmcp 连接 MCP server（list_dirs / read_file / write_file / bash），支持多 server 聚合 |
| 短期记忆压缩 | 对话超过 5 轮时，用 LLM 将老消息压缩为 100~200 字摘要，保留最近 4 轮 |
| 长期持久记忆 | 内置 `save_memory` 工具（本地执行，不走 MCP），记忆存为 Markdown 文件，下次启动自动注入 system prompt |

## 目录结构

```
step-by-step-04-plan/
├── plan_code_agent.py        # 主入口：REPL 主循环 + 规划/执行/直执三条路径
├── plan.py                   # （旧版本，功能与主入口重复，仅供参考）
├── plan/
│   └── task_state.py         # 任务状态机 TaskState + should_plan() + parse_plan_steps()
├── prompt/
│   └── tcode_prompt.py       # 三个 prompt 模板：基础 / 规划 / 执行
├── memory/
│   ├── memory_compressor.py  # 短期记忆：滑动窗口 + LLM 摘要压缩
│   ├── memory_persistence.py # 长期记忆：Markdown 持久化 + save_memory 工具
│   └── .tcode_memory.md      # 持久记忆数据文件（运行时生成）
└── mymcp/
    ├── tmcp_server.py        # FastMCP server（streamable-http，端口 9000）
    ├── tmcp_client.py        # MyMCPClient：多 server MCP 客户端封装
    └── .tcode.json           # server 配置示例
```

## 运行方式

```bash
# 1. 安装依赖（仓库根目录）
source .venv/Scripts/activate
pip install -e .

# 2. 配置仓库根目录 .env
#    DEEPSEEK_BASE_URL=...
#    DEEPSEEK_API_KEY=...
#    MODEL=deepseek-chat   （可选）

# 3. 启动 MCP server（一个终端）
python step-by-step-04-plan/mymcp/tmcp_server.py

# 4. 启动 Agent（另一个终端）
python step-by-step-04-plan/plan_code_agent.py
```

## 使用示例

```
you --> 帮我创建一个 Python 的 Hello World 程序并运行验证

[PLANNING] 正在生成执行计划...
## 执行计划
**任务目标**：创建并运行 Hello World
**步骤列表**：
1. 写入 hello.py（预计使用工具：write_file）
2. 运行验证（预计使用工具：bash）
...

确认计划？[y/n] --> y

[plan] 解析出 2 个步骤
[✓] 计划已确认，开始执行...

[STEP 1]
  [tool] write_file(path='hello.py', ...)
  [progress] [░░] 0/2
[STEP 2]
  [tool] bash(command='python hello.py')
  [progress] [█░] 1/2
[DONE] 任务完成
```

其它交互命令：

- `!<消息>` — 强制跳过规划直接执行，如 `!查看当前目录有什么文件`
- PLANNING 状态下直接输入文字 — 作为修订反馈，Agent 重新生成计划
- `exit` / `quit` / `q` — 退出

## 关键设计

### 任务状态机（plan/task_state.py）

```
IDLE ──► PLANNING ──► CONFIRMED ──► (EXECUTING) ──► DONE
              │                            │
              └────────────────────────────┴──► ABORTED
```

`TaskState` dataclass 携带一次任务的完整上下文：`plan`（Markdown 计划文本）、`steps`（解析出的步骤列表）、`current_step`（已完成步骤数）、`final_output` 等，任务结束或取消后 `reset()` 回到 IDLE。

### 进度跟踪的实现

1. 计划确认时，`parse_plan_steps()` 用正则从 Markdown 中提取 `1. / 2、/ 3)` 形式的编号行（遇到编号重启即停止，避免把"注意事项"等其它编号列表误当步骤）。
2. 执行阶段的 system prompt（`EXECUTING_PROMPT_TEMPLATE`）要求 LLM 每轮回复第一行输出 `[STEP N]` 标记，N 为当前正在执行的步骤编号。
3. Agent 端用正则解析该标记：`current_step = N - 1`（前 N-1 步视为已完成），驱动 `progress_bar()` 实时渲染；任务收尾时进度拉满。
4. LLM 未按约定输出标记时保持原进度不倒退；步骤解析失败时退化为"第 N 轮"显示。

### 双层记忆（memory/）

- **短期**（`memory_compressor.py`）：history 中 user 消息超过 `MAX_HISTORY_TURNS=5` 轮时，把倒数第 `KEEP_RECENT_TURNS=4` 轮之前的老消息交给 LLM 生成摘要，用一条 `[对话历史摘要]` 消息替换。
- **长期**（`memory_persistence.py`）：记忆分四个 section（用户偏好 / 项目背景 / 重要结论 / 操作记录）存于 `.tcode_memory.md`；Agent 通过 `save_memory` 工具主动写入，启动时读取并注入 system prompt。

### 通用 agentic loop

`plan_code_agent.py` 中的 `_agentic_loop()` 是"LLM ↔ 工具"循环的唯一实现，直接执行与计划执行共用；执行阶段通过两个钩子定制行为：

- `system_builder(iteration)` — 每轮动态重建 system 消息，把计划和实时进度注入，防止长执行过程中 LLM"忘记"计划；
- `on_assistant(text)` — 每轮 assistant 回复回调，用于解析 `[STEP N]` 进度标记。

规划阶段则使用临时消息序列（计划确认前不写入正式 history），且不携带工具 schema，保证只输出纯文本计划。
