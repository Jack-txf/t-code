# 模拟远程的mcp服务器
import subprocess
from pathlib import Path
from fastmcp import FastMCP
mcp = FastMCP("t-mymcp-server")
# ─────────────────────────────────────────
# Tools
# ─────────────────────────────────────────
@mcp.tool(
    name="list_dirs",
    description="列出指定目录下的所有文件和子目录"
)
def list_dirs(path: str = ".") -> dict:
    """列出指定目录下的所有文件和子目录。"""
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return {"error": f"路径不存在: {path}"}
    if not target.is_dir():
        return {"error": f"不是目录: {path}"}
    entries = []
    for entry in sorted(target.iterdir()):
        entries.append({
            "name": entry.name,
            "type": "dir" if entry.is_dir() else "file",
            "size": entry.stat().st_size if entry.is_file() else None,
        })
    return {"path": target.as_posix(), "entries": entries}
@mcp.tool(
    name="read_file",
    description="读取文件内容，以 UTF-8 编码返回文本。"
)
def read_file(path: str) -> dict:
    """读取文件内容，以 UTF-8 编码返回文本。"""
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return {"error": f"文件不存在: {path}"}
    if not target.is_file():
        return {"error": f"不是文件: {path}"}
    try:
        content = target.read_text(encoding="utf-8")
        return {"path": str(target), "content": content}
    except Exception as e:
        return {"error": str(e)}
@mcp.tool(
    name="write_file",
    description="将内容写入文件（不存在时自动创建，存在时覆盖）。"
)
def write_file(path: str, content: str, encoding: str = "utf-8") -> dict:
    """将内容写入文件（不存在时自动创建，存在时覆盖）。"""
    target = Path(path).expanduser().resolve()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding=encoding)
        return {"path": str(target), "bytes_written": len(content.encode(encoding))}
    except Exception as e:
        return {"error": str(e)}
@mcp.tool(
    name="bash",
    description="执行系统 Shell 命令并返回 stdout / stderr。"
)
def bash(command: str, timeout: int = 30) -> dict:
    """执行系统 Shell 命令并返回 stdout / stderr。"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"命令超时（>{timeout}s）: {command}"}
    except Exception as e:
        return {"error": str(e)}
# ─────────────────────────────────────────
# Resources
# ─────────────────────────────────────────
DB_CONFIG = """[database]
host     = 127.0.0.1
port     = 5432
name     = app_db
user     = app_user
password = s3cr3t
[pool]
min_size = 2
max_size = 10
timeout  = 30
"""
@mcp.resource("config://database")
def database_config() -> str:
    """模拟数据库配置文件（INI 格式）。"""
    return DB_CONFIG
# ─────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────
@mcp.prompt()
def general_assistant(task: str, context: str = "") -> str:
    """通用助手提示词模板。
    Args:
        task: 需要完成的任务描述。
        context: 可选的背景信息。
    """
    ctx_block = f"\n\n背景信息：\n{context}" if context else ""
    return (
        "你是一位专业、严谨的 AI 助手。"
        f"请根据以下任务要求，给出清晰、准确、可执行的回答。{ctx_block}\n\n"
        f"任务：{task}\n\n"
        "要求：\n"
        "1. 回答要条理清晰，重点突出。\n"
        "2. 如果需要代码，请附上注释并说明运行方式。\n"
        "3. 如有多种方案，列出优劣对比后给出推荐。\n"
        "4. 不确定的内容请明确说明，不要猜测。"
    )
# ─────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────
if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=9000)