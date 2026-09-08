import atexit
import signal
import sys

from vibe_cli.env.check_point_memory import close_postgres_memory
from vibe_cli.env.logger_config import logger
from vibe_cli.tools.show_diff_tool import cleanup_temp_files


def _signal_handler(sig, frame):
    """收到终止信号时的回调"""
    logger.warning(f"收到信号 {sig}，开始优雅关闭...")
    close_postgres_memory()
    cleanup_temp_files()
    sys.exit(0)


# 1. 注册程序退出时的自动清理 Hook
atexit.register(close_postgres_memory)

# 2. 捕获系统终止信号（SIGINT: Ctrl+C, SIGTERM: Docker/K8s 终止命令）
signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)