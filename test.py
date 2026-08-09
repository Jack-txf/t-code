# 测试加载环境变量
import os

from load_env import load_env
load_env()

if __name__ == "__main__":
    for key in ("DEEPSEEK_BASE_URL", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL"):
        value = os.environ.get(key)
        if value is None:
            print(f"{key}: (not set)")
        else:
            print(f"{key}: {value}")