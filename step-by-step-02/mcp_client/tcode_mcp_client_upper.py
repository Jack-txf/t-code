import asyncio
import json
from pathlib import Path
from fastmcp import Client

# MCP 配置文件路径定义
_CONFIG_PATH = Path.home() / ".tcode.json"

class TCodeMCPClient:
    """
    TCode MCP 客户端封装类
    负责配置文件的生命周期管理（创建/加载/保存）以及 MCP 客户端的初始化。
    """
    def __init__(self) -> None:
        # 先加载或初始化配置，再根据配置实例化客户端
        self.cfg: dict = self.__init_config_file()
        self.client: Client = self.__init_client()

    @staticmethod
    def __default_config() -> dict:
        """返回默认配置结构。"""
        return {"mcpServers": {}}

    @staticmethod
    def __load_config() -> dict:
        """
        读取配置文件并返回配置字典。

        Returns:
            dict: 成功读取到的配置字典。如果文件不存在则返回空字典。

        Raises:
            RuntimeError: JSON 格式错误或其他 IO 异常时抛出。
        """
        # 如果文件不存在，防御性返回空字典
        if not _CONFIG_PATH.is_file():
            return {}

        try:
            with _CONFIG_PATH.open("r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"配置文件 {_CONFIG_PATH} 格式错误: {e}")
        except Exception as e:
            raise RuntimeError(f"读取配置文件 {_CONFIG_PATH} 失败: {e}")

    @staticmethod
    def __save_config(config: dict) -> None:
        """
        将配置字典持久化写入文件。

        Args:
            config (dict): 需要保存的配置数据。
        """
        # 自动创建不存在的父级目录 (相当于 os.makedirs)
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

        with _CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    def __init_config_file(self) -> dict:
        """
        初始化配置文件的核心逻辑：
        - 如果文件已存在，直接读取并返回。
        - 如果文件不存在，生成默认配置、落盘保存后返回。
        """
        if _CONFIG_PATH.is_file():
            return self.__load_config()

        default_cfg = self.__default_config()
        self.__save_config(default_cfg)
        return default_cfg

    def __init_client(self) -> Client:
        """
        根据当前配置初始化 MCP 客户端。

        Returns:
            Client: 实例化的 fastmcp Client 对象。
        """
        print("初始化MCP客户端...")
        print(self.cfg)  # 例如: {'mcpServers': {'my-tools': {'url': '127.0.0.1:9000/mcp'}}}

        # 提取 mcpServers 配置传入 Client
        return Client(self.cfg.get("mcpServers", {}))

    async def list_tools(self) -> list:
        """
        异步获取 MCP 客户端支持的工具列表。

        Returns:
            list: 返回的工具列表。
        """
        # 使用上下文管理器安全管理客户端生命周期
        async with self.client as client:
            return await client.list_tools()

if __name__ == "__main__":
    async def main():
        tcode_mcp_client = TCodeMCPClient()
        tools = await tcode_mcp_client.list_tools()
        for tool in tools:
            print(tool)

    # 统一使用 asyncio.run() 启动异步事件循环
    asyncio.run(main())