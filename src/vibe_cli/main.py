import asyncio
import os
import sys

from dotenv import load_dotenv

from vibe_cli.workflow.base_workflow import start

# 1. 优先读取系统环境变量中指定的 APP_ENV（比如 production 或 development）
# 如果没指定，默认加载 .env.development 或直接加载 .env
app_env = os.getenv("APP_ENV", "development")

# 2. 查找对应的环境配置文件（支持在打包后的 exe 同级目录下放置）
env_file = f".env.{app_env}"

if os.path.exists(env_file):
    load_dotenv(env_file)
    print(f"[Config] 已加载配置文件: {env_file}")
elif os.path.exists(".env"):
    load_dotenv(".env")
    print("[Config] 已加载默认配置文件: .env")
else:
    print("[Config] 未找到环境配置文件，将直接读取系统环境变量。")

from vibe_cli.env.logger_config import logger


def run() -> None:
    logger.info("Hello from vibe-cli!")
    # 🟢 关键：在 Windows 下强制切换事件循环策略为 SelectorEventLoop
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(start())
