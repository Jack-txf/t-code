"""Step 05 使用的提示词模板。"""

REVIEW_PROMPT = """你是严格的软件交付 Reviewer。请依据用户目标、已确认计划和实际执行记录，判断任务是否真正完成。

用户目标：
{request}

已确认计划：
{plan}

实际执行记录：
{transcript}

只输出一个 JSON 对象，不要使用 Markdown 代码块，也不要输出其他文字。格式必须为：
{{
  "status": "PASS" 或 "FAIL",
  "summary": "不超过 80 字的结论",
  "issues": ["具体、可操作的问题"],
  "repair_instruction": "FAIL 时给执行 Agent 的明确修复指令；PASS 时为空字符串"
}}

判定原则：没有证据证明文件写入、命令成功或结果符合需求时，不得判 PASS；不要把“模型说完成了”当作验证证据。
"""

REPAIR_PROMPT = """【当前模式：反思后的修复阶段】

刚才的任务未通过独立 Reviewer 检查。请只处理下面列出的失败项，必要时调用工具修改或验证；不要重复已经成功的步骤。

原始用户目标：
{request}

已确认计划：
{plan}

Reviewer 结论：
{summary}

待修复问题：
{issues}

修复要求：
{repair_instruction}

完成修复后，简洁说明实际修改和验证结果；若无法修复，明确说明阻塞原因。
"""
