# Agent（五）反思机制：让 Agent 从“执行完成”走向“验证完成”

前四篇我们已经把一个最小 Coding Agent 逐步补齐：它会调用工具，会通过 MCP 扩展能力，会压缩上下文并保存长期记忆，也会在执行前给出计划、等待确认。

到第四步为止，链路看起来已经很完整：

```text
用户请求 → 计划 → 确认 → 工具执行 → 最终回答
```

但这里藏着一个很容易被忽略的问题：**Agent 什么时候算真正完成任务？**

在之前的实现中，只要模型不再发起 tool call，主循环就结束。它可能是因为任务完成了，也可能是因为遗漏了验证步骤、误解了工具错误，或者只是“觉得自己完成了”。因此第五篇要补上的能力是：**Reflection（反思）**。更准确地说，它不是让模型写一段泛泛的复盘，而是让一个独立的 Reviewer 基于执行证据给任务验收。

本文对应代码位于 `step-by-step-05-reflection/`。

## 一、从“会执行”到“会验收”

设想一个非常常见的请求：“创建一个 Python 文件，并运行验证。”执行 Agent 也许调用了 `write_file`，然后直接回答“已完成”。如果没有运行命令，或者命令运行失败，那么这个回答并不可信。

所以我们把结束阶段拆为两层：

```text
执行 Agent
  └─ 负责按计划调用工具、完成改动
        ↓
Reviewer
  └─ 只阅读目标、计划、工具返回和执行回答，输出 PASS / FAIL
        ↓
FAIL → 把具体问题交还给执行 Agent 修复一次 → 再次 Reviewer
PASS → 交付
```

这里的关键不在于“多调用一次模型”，而在于**职责隔离**：执行 Agent 负责做事，Reviewer 负责挑错。Reviewer 不携带工具定义，不能偷偷修改结果；它只能判断证据是否足够。

## 二、Reviewer 的输入为什么必须包含执行记录

如果只把最终回答给 Reviewer，它依然只能相信模型的自述。因此我们从对话历史中抽取三类记录：用户目标、执行 Agent 输出、工具调用结果。

```python
def build_transcript(history: list[dict], max_chars: int = 8_000) -> str:
    lines = []
    for message in history:
        role = message.get("role")
        if role not in {"user", "assistant", "tool"}:
            continue
        content = str(message.get("content") or "").strip()
        if content:
            lines.append(f"【{role}】{content[:1_200]}")
    return "\n".join(lines)[-max_chars:]
```

这样，Reviewer 能看到诸如 `returncode: 0`、测试输出、写入的路径等可核验事实。为了避免反思阶段的上下文无限增长，记录既按单条截断，也设置了总长度上限。

## 三、用 JSON 代替模糊的“反思文本”

如果只要求 Reviewer “检查一下任务”，下游很难稳定处理它的回答。因此我们约定固定协议：

```json
{
  "status": "PASS",
  "summary": "已创建并成功运行 hello.py。",
  "issues": [],
  "repair_instruction": ""
}
```

失败时则应给出明确修复指令：

```json
{
  "status": "FAIL",
  "summary": "文件已写入，但没有运行验证。",
  "issues": ["缺少 python hello.py 的执行证据"],
  "repair_instruction": "运行 python hello.py，并根据输出修复错误。"
}
```

代码中使用 `response_format={"type": "json_object"}` 约束模型输出，并在 `parse_review()` 中做容错。一个重要原则是：**解析失败默认 FAIL**。比起偶尔多做一次验证，错误地把未验证的任务标记为成功要危险得多。

## 四、把反思结果变成下一次行动

反思的价值不在于生成报告，而在于影响执行。`repair_once()` 把 `issues` 与 `repair_instruction` 组成新的用户消息，再复用第四篇的 `_agentic_loop()`：

```python
repair_prompt = REPAIR_PROMPT.format(
    request=task.user_request,
    plan=task.plan,
    summary=review.summary,
    issues=format_issues(review.issues),
    repair_instruction=review.repair_instruction,
)
history.append({"role": "user", "content": repair_prompt})
_, history = await step04._agentic_loop(history, mcp, tool_schemas)
```

这里没有重写 MCP、记忆、计划或流式工具调用，而是直接复用第四步已经成熟的执行循环。第五步真正新增的是一个更可靠的收尾控制流。

## 五、为什么只允许修复一次

反思—修复天然可能形成循环：模型修不好，Reviewer 持续失败，模型又继续尝试。因此示例将 `MAX_REPAIR_ATTEMPTS` 固定为 `1`。修复一次后仍失败，就明确把问题交给人。

这个限制体现了一个工程原则：**Agent 的自主性必须有预算**。后续可以把“次数”扩展为 token、时间、工具调用次数或风险等级预算。

## 六、运行与验证

先启动沿用的 MCP Server：

```powershell
python step-by-step-04-plan/mymcp/tmcp_server.py
```

再启动第五步 Agent：

```powershell
python step-by-step-05-reflection/reflection_code_agent.py
```

可以输入：

```text
帮我创建一个 hello.py，输出 Hello Agent，并运行验证
```

确认计划后，观察终端依次出现 `[EXECUTING]`、`[REFLECTION]`，以及在必要时出现的 `[REPAIR]`。此外，可直接运行 `python step-by-step-05-reflection/tests/test_reviewer.py`；它覆盖了 JSON 正常解析、格式异常时失败保守处理、以及工具证据被纳入审查记录三个不依赖模型的逻辑。

## 七、这一阶段的边界与下一步

这仍是教学版实现。Reviewer 和执行 Agent 默认使用同一个模型，模型也可能看错工具输出；它无法替代真正的单元测试、编译检查或人工审查。更可靠的生产实践是：优先运行确定性测试，把测试结果作为 Reviewer 的主要证据；并让 Reviewer 使用不同提示词、不同模型，或由规则引擎补充判断。

到这里，Agent 的主链路变为：

```text
工具循环 → MCP → 记忆 → 计划确认 → 执行 → 反思验收 → 受限修复
```

下一篇可以继续讨论“安全边界”：怎样限制 `bash` 和文件写入范围、给高风险操作加入人工审批，并将执行器真正放进可控沙箱。那时，这个 Agent 就会从“能完成任务”进一步走向“能在边界内可靠完成任务”。
