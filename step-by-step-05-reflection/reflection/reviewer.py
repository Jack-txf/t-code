"""将 LLM Reviewer 的输出约束为稳定、可处理的结构。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewResult:
    status: str
    summary: str
    issues: list[str]
    repair_instruction: str

    @property
    def passed(self) -> bool:
        return self.status == "PASS"


def parse_review(text: str) -> ReviewResult:
    """解析 Reviewer JSON；任何格式错误都保守地视为未通过。"""
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.S | re.I)
    if fenced:
        candidate = fenced.group(1)
    elif not candidate.startswith("{"):
        start, end = candidate.find("{"), candidate.rfind("}")
        candidate = candidate[start : end + 1] if start >= 0 and end > start else ""

    try:
        data = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return ReviewResult(
            status="FAIL",
            summary="Reviewer 未返回可解析的 JSON，无法证明任务已完成。",
            issues=["重新执行关键验证，并让 Reviewer 输出规定的 JSON 格式。"],
            repair_instruction="执行与用户目标最相关的验证命令，并汇报可核验的结果。",
        )

    status = str(data.get("status", "FAIL")).upper()
    status = status if status in {"PASS", "FAIL"} else "FAIL"
    raw_issues = data.get("issues", [])
    issues = [str(issue).strip() for issue in raw_issues if str(issue).strip()] if isinstance(raw_issues, list) else [str(raw_issues)]
    summary = str(data.get("summary", "")).strip() or "Reviewer 未提供结论。"
    instruction = str(data.get("repair_instruction", "")).strip()

    # FAIL 必须有可以送回执行 Agent 的行动项。
    if status == "FAIL" and not instruction:
        instruction = "根据上述问题补齐实现或验证，并提供可核验的结果。"
    return ReviewResult(status, summary, issues, instruction)


def format_issues(issues: list[str]) -> str:
    return "\n".join(f"- {issue}" for issue in issues) or "- Reviewer 未给出具体问题，请重新验证任务结果。"


def build_transcript(history: list[dict], max_chars: int = 8_000) -> str:
    """提取可审查的用户、助手及工具结果，并限制上下文大小。"""
    lines: list[str] = []
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
