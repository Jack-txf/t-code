import asyncio
import logging
# ─────────────────────────────────────────
# 配置
# ─────────────────────────────────────────
import json
from dataclasses import dataclass
from pathlib import Path
from fastmcp import Client
from fastmcp.client import StreamableHttpTransport
_DEFAULT_CONFIG: dict = {
    "mcpServers": {
        "my-tools": {
            "url": "http://127.0.0.1:9000/mcp",
            "transport": "streamable-http"
        }
    }
}
_CONFIG_PATH = Path.home() / ".tcode.json"

def _load_config() -> dict:
    """读取 ~/.tcode.json，不存在则创建默认配置并返回。"""
    if not _CONFIG_PATH.exists():
        _CONFIG_PATH.write_text(
            json.dumps(_DEFAULT_CONFIG, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"[McpClient] 配置文件不存在，已创建默认配置: {_CONFIG_PATH}")
    return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))

@dataclass
class ToolInfo:
    """携带来源 server 信息的 tool 描述。"""
    server: str
    name: str
    description: str
    def __str__(self) -> str:
        return f"[{self.server}] {self.name}: {self.description}"

@dataclass
class CallResult:
    """tool 调用结果，携带来源 server 信息。"""
    server: str
    tool: str
    result: object
    def __str__(self) -> str:
        return f"[{self.server}] {self.tool} {self.result}"


class MyMCPClient:
    """支持多 server 的 MCP 客户端。
    用法：
        # 连接配置文件里的所有 server（默认）
        client = McpClient()
        # 只连接指定的 server
        client = McpClient(server_names=["my-tools", "another-server"])
        # 只连接单个 server
        client = McpClient(server_names="my-tools")
    """
    def __init__(self, server_names: str | list[str] | None = None) -> None:
        cfg = _load_config()
        all_mcp_servers: dict = cfg.get("mcpServers", {})
        if not all_mcp_servers:
            logging.info(f"配置文件 {_CONFIG_PATH} 中 mcpServers 为空")
            return
        # =======
        # 确定目标 server 列表
        if server_names is None:
            targets = list(all_mcp_servers.keys())
        elif isinstance(server_names, str):
            targets = [server_names]
        else:
            targets = list(server_names)
        # 确定映射
        self._clients: dict[str, Client] = {}
        for name in targets:
            url: str = all_mcp_servers[name]["url"]
            self._clients[name] = Client(StreamableHttpTransport(url))
            print(f"[McpClient] 已注册 server={name!r}, url={url}")
    def get_client(self, server_name: str) -> Client:
        """获取指定 server 的底层 Client 实例。"""
        if server_name not in self._clients:
            raise ValueError(f"未注册的 server: {server_name!r}")
        return self._clients[server_name]
    # 列出所有工具！！！
    async def list_tools(self):
        server_list = list(self._clients.keys())
        # 内置方法
        async def _fetch(server_name: str) -> list[ToolInfo]:
            try:
                async with self._clients[server_name] as c:
                    tools = await c.list_tools()
                return [ToolInfo(server=server_name, name=t.name, description=t.description or "") for t in tools]
            except Exception as e:
                print(f"[McpClient] ⚠ server={server_name!r} 不可用，已跳过: {e}")
                return []
        # 开头的 *aws 表示：它接收若干个独立的可等待对象作为位置参数，而不是「接收一个装着协程的列表」
        results = await asyncio.gather(*[_fetch(n) for n in server_list])
        # 这个二维列表（列表套列表）展平成一个一维列表
        return [item for group in results for item in group]
    # 调用工具！！！
    async def call_tool(self,
           tool_name: str,
           server_name: str | None = None,
           arguments: dict | None = None
           ):
        """调用指定 tool。
        Args:
            tool_name:   tool 名称。
            server_name: 明确指定 server；None 时自动在所有 server 中查找。
            arguments:   传给 tool 的参数字典。
        """
        arguments = arguments or {}
        if server_name:
            # 检查一下有没有这个服务
            if not self._clients.__contains__(server_name):
                logging.error(f"没有这个服务名: {server_name!r}")
                return CallResult(server=server_name, tool=tool_name, result=f"没有这个服务名: {server_name!r}!")
            target = server_name
        else: # 如果没有指定servername，那么就找第一个有这个工具的
            all_tools = await self.list_tools()
            matched = [t.server for t in all_tools if t.name == tool_name]
            if not matched:
                logging.error(f"所有 server 中均未找到 tool: {tool_name!r}")
                return CallResult(server="None", tool=tool_name, result="没有这个工具!")
            target = matched[0]
        # 执行工具==============================
        async with self._clients[target] as c:
            try:
                result = await c.call_tool(tool_name, arguments)
            except Exception as e:
                logging.error(f"调用 server={target!r} tool={tool_name!r} 失败: {e}")
                return CallResult(server="None", tool=tool_name, result=f"调用 server={target!r} tool={tool_name!r} 失败: {e}")
        return CallResult(server=target, tool=tool_name, result=result)

if __name__ == "__main__":
    my_client = MyMCPClient()
    tools = asyncio.run(my_client.list_tools())
    for tool in tools:
        print(tool)
    print("==========")
    call_result = asyncio.run(
        my_client.call_tool("list_dirs", "my-tools", {"path": "D:/python-code/T-code/step-by-step-02-refactor"}))
    print(call_result)