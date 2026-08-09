"""
持久记忆模块（方向 B）

核心思路：
  - 记忆以 Markdown 文件形式存储（.tcode_memory.md），人和 AI 都可以直接编辑
  - Agent 通过 save_memory 工具主动写入重要信息
  - 每次会话启动时自动读取，注入 system prompt

记忆文件结构（Markdown）：
  ## 用户偏好
  ## 项目背景
  ## 重要结论
  ## 操作记录
"""
from datetime import datetime
from pathlib import Path

# 记忆文件默认路径（在调用方目录下）
DEFAULT_MEMORY_PATH = Path(__file__).parent / ".tcode_memory.md"

# 允许写入的分类（key 供 Agent 使用，value 是 Markdown 标题）
MEMORY_SECTIONS = {
    "preference": "用户偏好",
    "project":    "项目背景",
    "conclusion": "重要结论",
    "operation":  "操作记录",
}

# 注入 system prompt 时的包装模板
MEMORY_INJECT_TEMPLATE = """\
--- 持久记忆（上次会话保存的重要信息）---
{content}
--- 持久记忆结束 ---
"""

# 告诉 Agent 什么时候该调用 save_memory 的提示词片段
MEMORY_USAGE_HINT = """\
你拥有持久记忆能力。当对话中出现以下情况时，主动调用 save_memory 工具保存：
- 用户明确说明自己的偏好、习惯、要求（preference）
- 了解到项目的背景、技术栈、约束条件（project）
- 得出了重要的分析结论或决策（conclusion）
- 执行了重要的操作，下次可能需要回溯（operation）
"""


# ─────────────────────────────────────────────────────────────────────
# 读 / 写
# ─────────────────────────────────────────────────────────────────────

def load_memory(path: Path = DEFAULT_MEMORY_PATH) -> str:
    """读取记忆文件，返回原始内容；文件不存在返回空字符串。"""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def save_memory(content: str, section: str = "conclusion",
                path: Path = DEFAULT_MEMORY_PATH) -> str:
    """
    把一条记忆追加写入指定分类。

    Args:
        content: 要保存的内容（一句话到一段话均可）
        section: 分类 key，必须是 MEMORY_SECTIONS 中的键
        path:    记忆文件路径

    Returns:
        操作结果描述字符串（供 Agent 读取）
    """
    if section not in MEMORY_SECTIONS:
        valid = ", ".join(MEMORY_SECTIONS.keys())
        return f"错误：无效的分类 '{section}'，有效分类为：{valid}"

    section_title = MEMORY_SECTIONS[section]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"- [{timestamp}] {content.strip()}"

    # 读取现有内容
    existing = path.read_text(encoding="utf-8") if path.exists() else ""

    # 找到对应的 ## 分类，追加到其末尾；找不到就新建
    section_header = f"## {section_title}"

    if section_header in existing:
        # 在该 section 末尾追加（找下一个 ## 之前的位置）
        lines = existing.split("\n")
        insert_idx = len(lines)  # 默认追加到文件末尾
        in_section = False
        for i, line in enumerate(lines):
            if line.strip() == section_header:
                in_section = True
                continue
            if in_section and line.startswith("## "):
                insert_idx = i  # 下一个 section 开始前
                break
        lines.insert(insert_idx, entry)
        new_content = "\n".join(lines)
    else:
        # 追加新 section
        separator = "\n\n" if existing.strip() else ""
        new_content = existing + f"{separator}{section_header}\n{entry}\n"

    path.write_text(new_content.strip() + "\n", encoding="utf-8")
    return f"已保存到【{section_title}】：{content[:60]}{'…' if len(content) > 60 else ''}"


def clear_memory(section: str | None = None,
                 path: Path = DEFAULT_MEMORY_PATH) -> str:
    """
    清除记忆。section=None 时清除全部，否则只清除指定分类。
    """
    if not path.exists():
        return "记忆文件不存在，无需清除。"

    if section is None:
        path.unlink()
        return "已清除全部持久记忆。"

    if section not in MEMORY_SECTIONS:
        return f"无效分类：{section}"

    section_title = MEMORY_SECTIONS[section]
    section_header = f"## {section_title}"
    existing = path.read_text(encoding="utf-8")

    if section_header not in existing:
        return f"分类【{section_title}】中没有记忆。"

    # 删除该 section 的所有内容（到下一个 ## 为止）
    lines = existing.split("\n")
    new_lines = []
    skip = False
    for line in lines:
        if line.strip() == section_header:
            skip = True
            continue
        if skip and line.startswith("## "):
            skip = False
        if not skip:
            new_lines.append(line)

    path.write_text("\n".join(new_lines).strip() + "\n", encoding="utf-8")
    return f"已清除【{section_title}】的全部记忆。"


# ─────────────────────────────────────────────────────────────────────
# 注入 system prompt
# ─────────────────────────────────────────────────────────────────────

def build_system_prompt_with_memory(base_prompt: str,
                                    path: Path = DEFAULT_MEMORY_PATH) -> str:
    """
    把持久记忆注入 base system prompt，返回增强后的 system prompt。
    如果没有记忆文件，只附加 MEMORY_USAGE_HINT（告诉 Agent 何时保存记忆）。
    """
    memory_content = load_memory(path)

    if memory_content:
        memory_block = MEMORY_INJECT_TEMPLATE.format(content=memory_content)
        return base_prompt + "\n\n" + memory_block + MEMORY_USAGE_HINT
    else:
        return base_prompt + MEMORY_USAGE_HINT


# ─────────────────────────────────────────────────────────────────────
# Agent 可调用的工具 schema
# ─────────────────────────────────────────────────────────────────────
SAVE_MEMORY_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "save_memory",
        "description": (
            "把重要信息永久保存到记忆文件，下次会话启动时会自动加载。"
            "当发现用户偏好、项目背景、重要结论或关键操作时，主动调用此工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要保存的内容，用一到三句话描述清楚。",
                },
                "section": {
                    "type": "string",
                    "enum": list(MEMORY_SECTIONS.keys()),
                    "description": (
                        "分类：preference（用户偏好）/ project（项目背景）"
                        "/ conclusion（重要结论）/ operation（操作记录）"
                    ),
                },
            },
            "required": ["content", "section"],
        },
    },
}


def execute_save_memory_tool(args: dict,
                              path: Path = DEFAULT_MEMORY_PATH) -> str:
    """在 agent loop 的工具执行逻辑里调用此函数处理 save_memory。"""
    content = args.get("content", "")
    section = args.get("section", "conclusion")
    return save_memory(content, section, path)