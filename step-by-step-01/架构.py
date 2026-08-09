# 1.加载环境变量
from load_env import load_env
load_env()

# 2.初始化模型
from openai import OpenAI
def init_llmmodel() -> OpenAI | None:
    pass

# 【核心方法】loop逻辑
def run_agent_loop(user_message: str, history: list[dict]) \
        -> tuple[str, list[dict]]:
    """
    执行一轮完整的 agentic loop。
    流程： 构建 messages → 流式调用 LLM → 有工具调用? 执行并继续 : 返回最终回复
    返回： (最终回复文本, 更新后的 history)
    """
    pass

def main():
    print("T-code v1 — input 'exit' to quit====\n")
    # 3.1
    init_llmmodel()
    history: list[dict] = [] # 历史对话存储

    # 3.2 loop
    while True:
        # 3.3 感知用户输入
        try:
            user_input = input("you --> ")
        except (EOFError, KeyboardInterrupt):
            print("\n Bye!")
            break

        # 3.4 run_loop
        response, history = run_agent_loop(user_input, history)
        print(f"\n【Assistant】: {response}\n")

if __name__ == "__main__":
    main()