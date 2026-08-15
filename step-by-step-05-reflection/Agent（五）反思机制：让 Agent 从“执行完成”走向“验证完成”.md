# Agent（五）反思机制：让 Agent 从“执行完成”走向“验证完成”

前四篇，我们从最小的 Agentic Loop 出发，逐步接入 MCP 工具、上下文压缩与长期记忆，最后加入“先规划、用户确认、再执行”的工作流。到第四步，一个 Coding Agent 已拥有不错的行动能力：它能拆解任务、读写文件、运行命令，并把执行过程反馈给用户。

但还有一个根本问题：**它回答“已完成”时，我们凭什么相信任务真的完成了？**

第四步的结束条件是模型不再发起工具调用。这个实现很自然，却不等于可靠验收：模型停止调用工具，可能是任务完成，也可能是遗漏验证、误读错误，或者只是过早地下了结论。第五篇要补上的，正是执行和交付之间的最后一道关卡：**Reflection（反思/验收）**。

这里的“反思”不是让模型写一段心得，而是让一个不具备工具权限的 Reviewer，根据用户目标、已确认计划和执行证据，给出 PASS 或 FAIL。FAIL 时，系统把明确的修复意见交回执行 Agent，最多修复一次后再次验收。

本文代码位于 `step-by-step-05-reflection/`。该目录保留了第四步的 MCP、记忆、规划和执行基础模块，因此可以独立阅读、独立运行。

---

## 一、一个“看起来完成”但实际上失败的任务

先看一个非常小的需求：

> 创建 `hello.py`，输出 `Hello Agent`，并运行验证。

没有 Reflection 时，可能出现下面的过程：

```text
Agent → write_file(path="hello.py", content="print('Hello Agent')")
工具 → {"path": "hello.py", "bytes_written": 20}
Agent → 已创建 hello.py，任务完成。
```

文件确实写入了，但用户的需求包含两个动作：**创建文件**和**运行验证**。执行记录中没有 `python hello.py` 的返回值，所以“已完成”只是模型的表述，并不是可以核验的结论。

再看一个更隐蔽的失败：

```text
Agent → bash(command="python hello.py")
工具 → {"returncode": 1, "stdout": "", "stderr": "SyntaxError: ..."}
Agent → 程序已创建并验证完成。
```

LLM 擅长连贯表达，却不会天然对 `returncode != 0` 保持严格敏感。用“模型是否停止调用工具”判断成功，相当于把“模型不想继续做了”误当成“任务已经做好了”。

这就是第五步的核心命题：**执行结束不是交付结束；交付前必须有验收。**

---

## 二、反思的本质是职责分离，而不是多问一句

最直接的做法似乎是对执行 Agent 说：“请检查你刚才做得对不对。”这有一定作用，但仍然不够。执行者和检查者处在同一角色、同一上下文里，模型已经投入一个方案后，很容易倾向于维护自己的结论。

所以第五步把一次任务分给两个职责：

```text
执行 Agent
  - 理解请求、生成计划、调用工具、完成修改
        │
        ▼
Reviewer
  - 不调用工具
  - 只阅读目标、计划、执行回答和工具结果
  - 根据证据输出 PASS / FAIL
        │
        ├── PASS → 交付
        └── FAIL → 给出修复意见，交回执行 Agent
```

这个模式与真实工程中的开发、Code Review、CI 和验收测试非常接近。开发者可以认为代码没问题，但是否符合交付条件需要另一道检查来判断。教学版本中，Reviewer 与执行 Agent 默认共用模型配置；它的“独立性”主要来自不同的目标、不同的输入上下文，以及没有工具权限。生产系统可以进一步引入不同模型、规则引擎或人工 Reviewer。

这一步的重要性在于：系统不再只会沿着“做事”的方向前进，它第一次拥有了“停下来检查刚才做的事”的控制分支。

---

## 三、验收之前，先定义什么是证据

如果只把 Agent 最后的自然语言回答送给 Reviewer，Reviewer 仍然只能相信模型的自述。可靠验收应当以执行过程中留下的客观信息为依据：文件写入结果、测试命令返回码、标准输出、错误输出，以及实际读取到的内容。

| 任务类型 | 应优先检查的证据 | 不能单独作为成功依据 |
| --- | --- | --- |
| 新建文件 | 写入路径、文件内容、存在性检查 | “文件已经创建” |
| 修复 Bug | 测试/运行命令 `returncode=0` | “我已经修复” |
| 修改配置 | 修改后的配置片段、启动或校验结果 | “配置已更新” |
| 回答项目问题 | 实际读取的源码、搜索和命令输出 | 基于印象的解释 |

`reflection/reviewer.py` 中的 `build_transcript()` 负责从历史记录中提取用户、执行 Agent 和工具三类消息：

```python
def build_transcript(history: list[dict], max_chars: int = 8_000) -> str:
    lines = []
    for message in history:
        role = message.get("role")
        if role not in {"user", "assistant", "tool"}:
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        label = {"user": "用户", "assistant": "执行 Agent", "tool": "工具结果"}[role]
        lines.append(f"【{label}】{content[:1_200]}")
    transcript = "\n".join(lines)
    return transcript[-max_chars:] if len(transcript) > max_chars else transcript
```

这里做了两层限制：单条消息最多 1,200 个字符，完整审查材料最多 8,000 个字符。原因很实际：一次 `read_file` 可能读到数千行代码，一次测试也可能输出很长日志。如果不做边界，重要的错误码和最终测试结果会淹没在无关文本中，Reviewer 反而更容易判断失误。

教学代码采用通用文本截断；后续更好的方向是把工具结果结构化。例如单独保存 `returncode`、写入文件列表、测试通过数和失败用例，再让 Reviewer 阅读一张“事实摘要”。从非结构化日志走向结构化证据，是 Agent 可靠性提升的关键路径。

---

## 四、为什么 Reviewer 必须输出 JSON，而不是一段评论

假设 Reviewer 返回：“这次实现总体不错，但最好再运行一次。”对人来说，这句话能理解；对程序来说，它无法明确决定下一步是交付、修复，还是等待人工。因此需要一个机器可消费的协议：

```json
{
  "status": "PASS",
  "summary": "已创建 hello.py，并获得 Hello Agent 的成功运行输出。",
  "issues": [],
  "repair_instruction": ""
}
```

如果证据不足，返回失败及可操作的修复指令：

```json
{
  "status": "FAIL",
  "summary": "文件已写入，但没有发现运行验证的工具证据。",
  "issues": [
    "缺少 python hello.py 的执行结果",
    "无法证明输出是否为 Hello Agent"
  ],
  "repair_instruction": "运行 python hello.py；若输出不匹配，修正文件后再次运行。"
}
```

四个字段的职责不同：

- `status` 驱动控制流，决定交付还是修复；
- `summary` 为用户和日志提供短结论；
- `issues` 保存细粒度失败项，便于追踪；
- `repair_instruction` 把审查结论翻译成下一步可执行任务。

代码优先使用 OpenAI 兼容接口的 JSON mode；不支持该参数的服务会退化为提示词约束。无论模型如何输出，都会进入 `parse_review()`。这里采取一个很重要的失败策略：**无法解析时默认 FAIL。**

```python
result = parse_review(response.choices[0].message.content or "")
if result.passed:
    print("[✓] 任务已通过反思验证。")
else:
    print(f"[REFLECTION] FAIL: {result.summary}")
```

这是一种 fail closed 原则。格式错误、字段缺失或状态非法时，系统不能借此声称任务成功；它最多只能要求补充验证。对于 Coding Agent，偶尔多一次检查的代价，通常远低于把未验证的结果交付给用户的代价。

---

## 五、反思如何改变执行：从“报告失败”到“定向修复”

如果 Reviewer 只生成一份报告，Reflection 只是一个更漂亮的日志。它真正的价值在于：将失败结论转成下一次行动。

`repair_once()` 把原始目标、确认后的计划、Reviewer 总结、问题列表和修复指令拼成一条新的消息，再复用第四步的工具循环：

```python
repair_prompt = REPAIR_PROMPT.format(
    request=task.user_request,
    plan=task.plan,
    summary=review.summary,
    issues=format_issues(review.issues),
    repair_instruction=review.repair_instruction,
)
history.append({"role": "user", "content": repair_prompt})
_, history = await base._agentic_loop(history, mcp, tool_schemas)
```

这里没有重新设计执行器。修复 Agent 仍使用同一套 MCP 工具，仍能看到原计划和历史证据，但目标被收束为：**只处理失败项，不重复已经成功的步骤。**

继续使用 `hello.py` 例子。第一次执行只写入文件，Reviewer 返回 FAIL。修复阶段会明确要求执行：

```text
bash(command="python hello.py")
→ {"returncode": 0, "stdout": "Hello Agent\n", "stderr": ""}
```

第二次审查就能从工具结果中找到成功证据，并给出 PASS。最终结论不再只是“模型说它做完了”，而是经过“执行—检查—补证据—再检查”得到的。

---

## 六、为什么必须限制修复次数

加入 Reflection 后，很容易写出下面的循环：

```python
while review.status == "FAIL":
    execute_repair()
    review = review_again()
```

看起来很自主，实际上风险很大。模型可能反复采取同一种无效修复；外部服务可能一直不可用；模型也可能误判失败原因，不断扩大改动范围。无限循环会消耗 token、时间和工具权限，还会积累不可预测的副作用。

因此示例将修复次数设为：

```python
MAX_REPAIR_ATTEMPTS = 1
```

一次不是永远足够，而是教学阶段最清晰的“自主性预算”。生产环境可以组合多种边界：

```text
最多 2 次修复
总工具调用不超过 12 次
总执行时间不超过 3 分钟
高风险写操作必须再次确认
```

Agent 的成熟并不表现为它能无限尝试，而表现为它知道什么时候该停止并上报。真正可控的自主性，从来不是没有边界的尝试，而是在预算内推进，并在越界前把控制权还给人。

---

## 七、第五步完整控制流

第四步的主链路是：

```text
用户请求 → 判断是否需要计划 → 生成计划 → 用户确认 → 执行工具 → 回答
```

第五步不推翻这条链路，而是在执行后增加验收与修复：

```text
用户请求
  │
  ├── 简单查询 ─────────────────────────────→ 直接执行
  │
  └── 复杂任务 → 生成计划 → 用户确认 → 按计划执行
                                            │
                                            ▼
                                   提取执行证据 Transcript
                                            │
                                            ▼
                                      Reviewer 审查
                                   ┌────────┴────────┐
                                   ▼                 ▼
                                 PASS              FAIL
                                   │                 │
                                   ▼                 ▼
                                 交付        一次修复并复检
                                                       │
                                                       ▼
                                            通过则交付；否则人工介入
```

目录上，`reflection_code_agent.py` 是第五步入口；`base_code_agent.py`、`memory/`、`mymcp/`、`plan/` 与 `prompt/` 是从第四步完整复制而来的基础能力；`reflection/` 则是新增的 Reviewer、提示词与解析逻辑。每个 `step-by-step` 都能独立运行，避免学习者在阅读第五步时还要回到第四步追踪源码。

---

## 八、运行、观察与测试

先启动第五步自己的 MCP Server：

```powershell
python step-by-step-05-reflection/mymcp/tmcp_server.py
```

再启动 Agent：

```powershell
python step-by-step-05-reflection/reflection_code_agent.py
```

输入下面的任务最容易观察完整闭环：

```text
帮我创建一个 hello.py，输出 Hello Agent，并运行验证
```

确认计划后，可以关注三类输出：

1. `[EXECUTING]`：按计划调用工具；
2. `[REFLECTION] PASS/FAIL`：Reviewer 基于证据给出的结论；
3. `[REPAIR]`：只在 FAIL 时出现，表示系统正在按失败项做一次定向补救。

此外，下面的测试不需要 API Key：

```powershell
python step-by-step-05-reflection/tests/test_reviewer.py
```

它验证了三件确定性的事：有效 JSON 能被识别为 PASS；非 JSON 返回会保守地成为 FAIL；工具结果会进入审查记录。我们不试图为“模型一定正确”写测试，而是为程序可控制、可预测的部分写测试。这正是 Agent 从演示代码进入工程代码的分界线。

---

## 九、Reflection 不能替代测试

Reviewer 能做语义层面的验收：它可以发现“用户要求运行验证，但记录里没有运行命令”，也能发现“测试失败却被总结为成功”。但它不应替代单元测试、编译检查和人工审查：

- 它可能误读复杂日志；
- 它无法证明边界条件被充分覆盖；
- 它会受到不可靠工具输出的影响；
- 同一模型执行和审查时，仍可能存在共同偏差。

更可靠的生产实践应当是：

```text
编译 / 类型检查 / 单元测试 / lint 等确定性检查
                    ↓
工具返回结构化结果
                    ↓
Reviewer 判断需求是否满足、证据是否充分
                    ↓
高风险或不确定结论交给人
```

Reflection 不是测试的竞争者，而是测试之上的协调层：它提醒 Agent 应验证什么，判断验证是否覆盖用户目标，并在证据不足时拒绝过早交付。

---

## 十、我的思考：可靠的 Agent，首先要学会面对不确定性

我认为，Agent 从 Demo 走向工程系统，最关键的转折并不在于它接入了多少工具，也不在于规划写得多漂亮，而在于它有没有一套处理“不确定性”的结构。

传统程序的可靠性主要来自确定的逻辑分支；LLM Agent 的输入、规划和语言总结都天然带有不确定性。我们无法保证它每次都正确理解需求、正确读取日志、正确选择工具。因此，试图用更长的 Prompt 一次性让模型“永远正确”，是一条没有终点的路。

更有意义的方向是承认模型会错，并为错误设计机制：

```text
可能遗漏验证      → 以执行证据为依据，引入 Reviewer
可能误读失败日志  → 结构化工具结果，优先确定性测试
可能重复无效尝试  → 限制次数、时间和工具调用预算
可能触发危险操作  → 设置权限边界和人工确认
可能无法判断      → 明确失败，并升级给人
```

Reflection 的深层价值并不是“多调用一次 LLM”，而是系统不再把模型的自信当作事实。成熟的 Agent 不应总是努力给出漂亮的成功结论；当证据不足时，它应该能诚实地说：**我还不能证明任务已经完成。**

这句话看起来保守，却是可信自动化的起点。真正值得信任的系统，不是从不失败的系统，而是失败时仍能留下证据、限制影响、暴露不确定性，并把控制权及时还给人的系统。

至此，教程主线变为：

```text
Agentic Loop → MCP → 记忆 → Plan-then-Execute → Reflection
```

下一篇可以自然走向安全执行：限制 `bash` 与文件写入范围、为高风险操作加入审批、将工具调用放入受控工作区。前五步解决“怎样让 Agent 能思考、能行动、能检查自己”；安全边界将继续回答一个更现实的问题：**当它真的开始行动时，我们如何放心地把权限交给它？**
