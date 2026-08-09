# 测试mcp客户端
import asyncio
from fastmcp import Client

# HTTP server
client = Client("http://127.0.0.1:9000/mcp")

async def main():
    async with client:
        await client.ping()
        # List available operations
        tools = await client.list_tools()
        for tool in tools:
            print(tool)
        print("===============")
        resources = await client.list_resources()
        print(resources)
        print("===============")
        prompts = await client.list_prompts()
        print(prompts)
        print("===============")

        # Execute operations
        # result = await client.call_tool("example_tool", {"param": "value"})
        # print(result)
        # print("===============")

if __name__ == "__main__":
    asyncio.run(main())