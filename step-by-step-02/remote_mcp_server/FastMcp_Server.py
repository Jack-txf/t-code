# 使用FastMCP来构建MCP Server
import os
import subprocess
from fastmcp import FastMCP

mcp = FastMCP("t-code-MCP-server")

@mcp.tool(
    name="bash",
    description="执行shell命令用的",
    meta={"version": "1.0", "author": "txf"}
)
def bash(command: str, timeout: int = 30) -> str:
    """
    执行 shell 命令并返回输出结果。

    参数:
        command: 要执行的 shell 命令字符串。
        timeout: 命令超时时间（秒），默认 30 秒。

    返回:
        命令的标准输出 + 标准错误；失败时返回错误描述。
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout + result.stderr
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool(
    name="write_file",
    description="写入文件用的工具",
    meta={"version": "1.0", "author": "txf"}
)
def write_file(path: str, content: str) -> str:
    """
    将内容写入指定文件（若文件已存在则覆盖）。

    参数:
        path: 目标文件路径。
        content: 要写入的文本内容。

    返回:
        成功时返回写入字符数信息；失败时返回错误描述。
    """
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Written {len(content)} chars to {path}"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool(
    name="list_dir",
    description="列出当前目录下的文件和子目录",
    meta={"version": "1.0", "author": "txf"}
)
def list_dir(path: str = ".") -> str:
    """
    列出指定目录下的所有文件和子目录。

    参数:
        path: 要列出的目录路径，默认为当前目录 "."。

    返回:
        按字母排序的文件/目录名列表，每行一个；失败时返回错误描述。
    """
    try:
        entries = os.listdir(path)
        return "\n".join(sorted(entries)) or "(empty)"
    except Exception as e:
        return f"Error: {e}"


@mcp.tool(
    name="read_file",
    description="读取文件用的工具",
    meta={"version": "1.0", "author": "txf"}
)
def read_file(path: str) -> str:
    """
    读取指定文本文件的内容。

    参数:
        path: 文件的绝对路径或相对路径。

    返回:
        文件的全部文本内容；失败时返回错误描述。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error: {e}"


@mcp.resource("data://config")
def get_config() -> dict:
    return {"theme": "dark", "version": "1.0"}


if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=9000)
