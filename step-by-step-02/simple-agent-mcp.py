# 初始化环境变量
from load_env import load_env
load_env()
# 初始化模型
import os
from openai import OpenAI
def init_llmmodel() -> OpenAI | None:
    global client
    client = OpenAI(
        base_url=os.environ["DEEPSEEK_BASE_URL"],
        api_key=os.environ["DEEPSEEK_API_KEY"],
    )
    return client


def main():
    pass

if __name__ == "__main__":
    main()