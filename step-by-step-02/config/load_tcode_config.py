import json
import os
# MCP从配置文件中加载
# 配置文件默认在当前用户目录下的 .tcode-模板.json
_config_path = os.path.join(os.path.expanduser("~"), ".tcode.json")

def _default_config() -> dict:
    """返回默认配置空内容。"""
    return {
        "mcpServers": {

        }
    }


def get_config_path() -> str:
    """返回当前使用的配置文件路径。"""
    return _config_path



def load_config() -> dict:
    """
    读取配置文件并返回配置字典。
    文件不存在时返回空字典。
    """
    if not os.path.isfile(_config_path):
        return {}
    try:
        with open(_config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"配置文件 {_config_path} 格式错误: {e}")
    except Exception as e:
        raise RuntimeError(f"读取配置文件 {_config_path} 失败: {e}")


def save_config(config: dict) -> None:
    """
    将配置字典写入文件。
    自动创建不存在的父目录。
    """
    config_dir = os.path.dirname(os.path.abspath(_config_path))
    os.makedirs(config_dir, exist_ok=True)
    with open(_config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# 初始化配置文件，如果有就直接读取，没有就创建
def init_config_file() -> dict:
    """
    初始化配置文件。
    - 如果文件已存在，直接读取并返回。
    - 如果文件不存在，创建默认配置、写入文件后返回。
    """
    if os.path.isfile(_config_path):
        return load_config()

    default = _default_config()
    save_config(default)
    return default


if __name__ == "__main__":
    cfg = init_config_file()
    print(f"Config path: {get_config_path()}")
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
