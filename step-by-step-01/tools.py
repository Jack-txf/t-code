import os
import subprocess


# =============================================================================
# 设计逻辑概述
# =============================================================================
# 本模块实现了 Agent 可调用的本地工具集。
#
# 核心设计原则：
# 1. 统一错误处理 —— 每个工具函数内部都包裹 try/except，将异常转换为字符串返回。
#    这样即使工具执行失败，也不会中断 Agent 的循环，错误信息可以直接回传给 LLM。
# 2. 注册表模式 —— TOOLS 字典将「函数实现」与「JSON Schema 描述」绑定在一起，
#    便于动态管理和扩展新工具。
# 3. Schema 遵循 OpenAI Function Calling 规范 —— 包含 name、description、parameters
#    等字段，使 LLM 能够自主判断何时调用哪个工具、传入什么参数。
# =============================================================================


def bash(command: str, timeout: int = 30) -> str:
    """
    执行 shell 命令并返回输出结果。

    设计思路：
    - 使用 subprocess.run() 而非 os.system()，因为它更安全（不会暴露给当前 shell 环境变量注入），
      且支持 capture_output、timeout 等现代特性。
    - shell=True 允许执行管道、重定向等复杂命令，但在生产环境中需谨慎使用。
    - capture_output=True + text=True 将 stdout/stderr 直接捕获为字符串。
    - 将 stdout 与 stderr 拼接返回，确保 LLM 能看到完整的命令反馈（包括错误输出）。

    参数:
        command: 要执行的 shell 命令字符串。
        timeout: 命令超时时间（秒），默认 30 秒，防止长时间挂起拖死 Agent 循环。

    返回:
        命令的标准输出 + 标准错误；若无输出则返回 "(no output)"。
        超时或异常时返回带有错误描述的字符串。
    """
    try:
        result = subprocess.run(
            command,
            shell=True,          # 允许使用 shell 语法（如管道、重定向）
            capture_output=True, # 捕获 stdout/stderr，不打印到终端
            text=True,           # 以文本模式返回字符串，而非字节
            timeout=timeout      # 防止命令无限期阻塞
        )
        # 合并 stdout 和 stderr，让 LLM 同时看到正常输出和错误输出
        output = result.stdout + result.stderr
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


def read_file(path: str) -> str:
    """
    读取指定文本文件的内容。

    设计思路：
    - 使用 with 语句确保文件句柄自动关闭，避免资源泄漏。
    - 显式指定 encoding="utf-8"，避免 Windows 默认 GBK 编码导致的解码错误。
    - 所有异常统一捕获并转为字符串，保持 Agent 循环的稳定性。

    参数:
        path: 文件的绝对路径或相对路径。

    返回:
        文件的全部文本内容；失败时返回错误描述字符串。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error: {e}"


def write_file(path: str, content: str) -> str:
    """
    将内容写入指定文件（若文件已存在则覆盖）。

    设计思路：
    - os.makedirs(..., exist_ok=True) 自动创建缺失的父目录，避免手动逐层创建。
    - os.path.abspath(path) 将相对路径转为绝对路径，确保 makedirs 能正确提取目录层级。
    - 同样使用 utf-8 编码，保证跨平台一致性。
    - 返回写入字符数，方便 LLM 确认操作成功。

    参数:
        path: 目标文件路径。
        content: 要写入的文本内容。

    返回:
        成功时返回 "Written {n} chars to {path}"；失败时返回错误描述字符串。
    """
    try:
        # 自动创建不存在的父目录（如 path 为 "a/b/c.txt"，会递归创建 a/b）
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Written {len(content)} chars to {path}"
    except Exception as e:
        return f"Error: {e}"


def list_dir(path: str = ".") -> str:
    """
    列出指定目录下的所有文件和子目录。

    设计思路：
    - 使用 os.listdir() 获取目录条目，它只返回一层，不递归子目录。
    - sorted() 对结果排序，使输出更稳定、可读性更好。
    - 默认参数为当前目录 "."，方便 LLM 快速查看当前工作目录内容。
    - 空目录返回 "(empty)"，明确告知 LLM 目录内没有内容。

    参数:
        path: 要列出的目录路径，默认为当前目录 "。"。

    返回:
        按字母排序的文件/目录名列表，每行一个；失败时返回错误描述字符串。
    """
    try:
        entries = os.listdir(path)
        return "\n".join(sorted(entries)) or "(empty)"
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# 工具注册表
# =============================================================================
# TOOLS 是一个字典，键为工具名称（与 LLM 调用的 function.name 对应），
# 值为一个包含 "fn"（实际函数）和 "schema"（OpenAI Function Calling 格式描述）的字典。
#
# 这种设计的优势：
# 1. 新增工具时，只需再写一个函数并在 TOOLS 中注册一条记录即可，无需修改 Agent 主循环逻辑。
# 2. execute_tool() 在 simple-agentic-loop.py 中通过名称查找并调用，实现完全解耦。
# 3. schema 与 fn 紧邻，方便维护——修改参数时一处修改即可同步行为和描述。
#
# Schema 格式说明：
# - type: "function" 为固定值，表示这是一个可调用工具。
# - function.name: 工具唯一标识，LLM 会通过这个名字发起调用。
# - function.description: 工具的用途描述，直接影响 LLM 判断是否使用该工具。
# - function.parameters: JSON Schema 格式，定义 LLM 应传入的参数结构。
#   - properties: 每个参数的名称、类型和描述。
#   - required: 必填参数列表；未列出的参数视为可选。
#   - default 值通过 description 描述告知 LLM（OpenAI Schema 本身不原生支持 default）。
# =============================================================================

TOOLS: dict = {
    "bash": {
        "fn": bash,
        "schema": {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Execute a shell command and return its output.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Shell command to run"},
                        "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 30},
                    },
                    "required": ["command"],
                },
            },
        },
    },
    "read_file": {
        "fn": read_file,
        "schema": {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read the contents of a file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute or relative file path"},
                    },
                    "required": ["path"],
                },
            },
        },
    },
    "write_file": {
        "fn": write_file,
        "schema": {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write content to a file (overwrites if exists).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path to write"},
                        "content": {"type": "string", "description": "Content to write"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
    },
    "list_dir": {
        "fn": list_dir,
        "schema": {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "List files and directories at a given path.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path", "default": "."},
                    },
                    "required": [],
                },
            },
        },
    },
}

# =============================================================================
# TOOL_SCHEMAS
# =============================================================================
# 从 TOOLS 注册表中提取所有 schema，组成一个列表。
# 这个列表会在调用 LLM API 时直接传入 tools 参数：
#   client.chat.completions.create(..., tools=TOOL_SCHEMAS, tool_choice="auto")
#
# 保持 TOOLS（完整注册表）和 TOOL_SCHEMAS（纯 schema 列表）分离的好处：
# - TOOLS 供 execute_tool() 查询函数实现；
# - TOOL_SCHEMAS 供 API 调用，避免将函数对象序列化传递给网络请求。
# =============================================================================

TOOL_SCHEMAS: list = [t["schema"] for t in TOOLS.values()]