import os
import sys
from pathlib import Path

from dotenv import load_dotenv

def get_base_dir() -> Path:
    """获取根目录：兼顾开发环境与 PyInstaller 打包后的 .exe 环境"""
    # 检查当前是否被打包成了 exe (PyInstaller 的特征)
    if getattr(sys, 'frozen', False):
        # 如果是打包后的 exe，根目录就是 .exe 文件当前所在的目录
        return Path(sys.executable).resolve().parent
    else:
        # 开发环境：向上递归查找包含 pyproject.toml 的目录
        current_path = Path(__file__).resolve()
        for parent in [current_path] + list(current_path.parents):
            if (parent / "pyproject.toml").exists():
                return parent
        return Path.cwd()

# 获取正确的基准根目录
BASE_DIR = get_base_dir()

# 优先读取系统环境变量中指定的 APP_ENV（比如 production 或 development）
app_env = os.getenv("APP_ENV", "development")


def _resolve_env_file():
    # 候选顺序：
    #   a) 当前工作目录（兼容打包后 exe 同级放置 .env）
    #   b) 项目根目录（开发模式：.env 位于项目最外层）
    candidates = [
        Path.cwd() / f".env.{app_env}",
        Path.cwd() / ".env",
        BASE_DIR / f".env.{app_env}",
        BASE_DIR / ".env",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


# 2. 查找并加载环境配置文件
env_file = _resolve_env_file()
if env_file is not None:
    load_dotenv(env_file)
    print(f"[Config] 已加载配置文件: {env_file}")
else:
    print("[Config] 未找到环境配置文件，将直接读取系统环境变量。")

from vibe_cli.env.logger_config import logger
from vibe_cli.workflow.base_workflow import start


def run() -> None:
    start()
    logger.success("started")