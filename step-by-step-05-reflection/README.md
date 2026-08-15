# step-by-step-05-reflection — Reflection（反思与自我修复）

第 05 步接在 Plan-then-Execute 之后：计划得到确认并执行后，新增一个**不调用工具的 Reviewer** 检查实际证据；若未通过，执行 Agent 最多根据结构化反馈修复一次，再复检。

```text
用户请求 → 生成计划 → 用户确认 → 执行工具
                                  ↓
                       Reviewer：PASS / FAIL
                          ↓                 ↓
                       交付结果       一次修复 → 再次 Review
```

## 为什么需要反思

工具循环以“模型停止调用工具”作为结束条件，这不等于任务确实完成。常见问题是：修改了文件但没运行验证、命令失败后仍然总结成功、或最终产物不符合原目标。Reflection 将“执行”和“验收”拆成两个上下文，减少执行 Agent 给自己判卷的偏差。

## 目录

```text
step-by-step-05-reflection/
├── reflection_code_agent.py  # 主入口；复用 Step 04 的计划、记忆、MCP 和执行循环
├── reflection/
│   ├── prompts.py             # Reviewer / 修复阶段提示词
│   └── reviewer.py            # JSON 解析、执行记录提取、失败保守处理
└── tests/test_reviewer.py     # 无需 API 的单元测试
```

## 运行

在仓库根目录分别执行：

```powershell
python step-by-step-04-plan/mymcp/tmcp_server.py
python step-by-step-05-reflection/reflection_code_agent.py
python step-by-step-05-reflection/tests/test_reviewer.py
```

`.env` 配置、`~/.tcode.json` 中的 MCP Server 配置与 Step 04 相同。

## 关键设计

- **独立验收**：Reviewer 不带 MCP 工具 schema，只根据目标、计划与工具结果作判断。
- **结构化协议**：Reviewer 必须返回 `status / summary / issues / repair_instruction` JSON；格式错误时默认 `FAIL`，不会误报成功。
- **修复上限**：默认只修复一次（`MAX_REPAIR_ATTEMPTS = 1`），防止 Agent 在“反思—修复”中无限循环。
- **证据优先**：提示词要求用实际命令返回值、文件写入结果等判定，而不是相信 Agent 的口头总结。

> 这是教学实现：Reviewer 与执行 Agent 默认仍使用同一模型。生产环境建议分离模型/提示词、引入确定性测试，并为工具设置权限边界。
