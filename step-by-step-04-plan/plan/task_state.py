"""
任务状态机（step-04）

Agent 处理一个复杂任务时经历的生命周期：

  IDLE ──► PLANNING ──► CONFIRMED ──► EXECUTING ──► DONE
                │                          │
                └──────────────────────────┴──► ABORTED

状态含义：
  IDLE       没有活跃任务，等待用户输入
  PLANNING   Agent 已生成执行计划，等待用户确认
  CONFIRMED  用户确认计划，即将执行
  EXECUTING  正在执行工具调用序列
  DONE       任务完成
  ABORTED    用户取消或执行失败
"""

from dataclasses import dataclass, field
from enum import Enum
import re

class TaskStatus(Enum):
    IDLE       = "idle"
    PLANNING   = "planning"
    CONFIRMED  = "confirmed"
    EXECUTING  = "executing"
    DONE       = "done"
    ABORTED    = "aborted"


@dataclass
class TaskState:
    """当前活跃任务的全部状态。"""
    status: TaskStatus = TaskStatus.IDLE
    # 原始用户请求
    user_request: str = ""
    # Agent 生成的计划（Markdown 格式）
    plan: str = ""
    # 计划中的步骤列表（计划确认时由 parse_plan_steps 从 Markdown 解析填充）
    steps: list[str] = field(default_factory=list)
    # 已完成的步骤数（0-based 计数，由执行阶段 LLM 回复中的 [STEP N] 标记驱动，
    # 语义是"前 N-1 步已完成"，不是 LLM 的循环轮次）
    current_step: int = 0
    # 每步的执行结果摘要
    step_results: list[str] = field(default_factory=list)
    # 最终输出
    final_output: str = ""
    # 失败原因（ABORTED 时填充）
    abort_reason: str = ""

    def reset(self):
        """重置为 IDLE，准备接受新任务。"""
        self.status = TaskStatus.IDLE
        self.user_request = ""
        self.plan = ""
        self.steps = []
        self.current_step = 0
        self.step_results = []
        self.final_output = ""
        self.abort_reason = ""

    def is_active(self) -> bool:
        return self.status not in (TaskStatus.IDLE, TaskStatus.DONE, TaskStatus.ABORTED)

    def progress_bar(self) -> str:
        """返回进度条字符串，如 [██░░░] 2/5。

        steps 为空（计划解析失败或未解析）时返回空串，
        调用方应退化为"第 N 轮"之类的轮次显示。
        """
        total = len(self.steps)
        if total == 0:
            return ""
        done = min(max(self.current_step, 0), total)  # 钳制在 [0, total]
        bar = "█" * done + "░" * (total - done)
        return f"[{bar}] {done}/{total}"


# ─────────────────────────────────────────────────────────────────────
# 从 Markdown 计划中解析步骤列表
# ─────────────────────────────────────────────────────────────────────
# 匹配 "1. xxx" / "2、xxx" / "3) xxx" / "4）xxx" 这类编号行
_STEP_LINE_RE = re.compile(r"^\s*(\d+)\s*[.、)）]\s*(.+?)\s*$")


def parse_plan_steps(plan: str) -> list[str]:
    """从 Markdown 计划文本中解析编号步骤列表。

    计划模板中"步骤列表"是 1./2./3. 形式的编号列表（见 PLANNING_PROMPT）。
    启发式规则：
      - 收集所有编号行，按编号排序后返回步骤描述；
      - 编号重新从 1 开始时停止收集 —— 说明进入了计划的另一个编号列表
        （如"注意事项"里的编号项），避免把非步骤内容误当步骤。
    解析不到任何编号行时返回空列表，进度条退化为轮次显示。
    """
    steps: list[tuple[int, str]] = []
    for line in plan.splitlines():
        m = _STEP_LINE_RE.match(line)
        if not m:
            continue
        num, text = int(m.group(1)), m.group(2)
        if num == 1 and steps:
            break  # 编号重启 = 新的列表，步骤列表已结束
        steps.append((num, text))
    return [text for _, text in sorted(steps)]


# ─────────────────────────────────────────────────────────────────────
# 判断用户输入是否需要规划
# ─────────────────────────────────────────────────────────────────────
# 这些动词暗示任务复杂、有副作用，需要先规划再执行
_PLAN_TRIGGERS = [
    "帮我", "请帮", "帮助我",
    "重构", "refactor",
    "修改", "修复", "fix",
    "实现", "添加", "新增", "增加", "implement", "add",
    "删除", "移除", "remove", "delete",
    "优化", "改进", "optimize",
    "创建", "生成", "写一个", "写个", "create", "generate",
    "迁移", "升级", "migrate", "upgrade",
]

_SIMPLE_TRIGGERS = [
    "是什么", "什么是", "解释", "说明", "介绍",
    "怎么", "如何", "为什么",
    "查看", "看看", "列出", "显示", "show", "list",
    "搜索", "查找", "找",
]


def should_plan(user_message: str) -> bool:
    """
    判断这条用户消息是否应该先做规划。

    启发式规则：
    - 包含"执行类"动词 → 需要规划
    - 只包含"查询类"动词 → 直接执行
    - 消息很短（< 10 字）→ 直接执行
    - 以 "!" 开头 → 强制直接执行（用户明确跳过规划）
    """
    if user_message.startswith("!"):
        return False
    if len(user_message.strip()) < 10:
        return False

    msg_lower = user_message.lower()
    # 有明确的查询关键字 → 不需要规划
    for kw in _SIMPLE_TRIGGERS:
        if kw in msg_lower:
            return False
    # 有执行类关键字 → 需要规划
    for kw in _PLAN_TRIGGERS:
        if kw in msg_lower:
            return True
    return False