import atexit
import signal
import sys

from vibe_cli.env.check_point_memory import close_postgres_memory
from vibe_cli.env.logger_config import logger
from vibe_cli.tools.show_diff_tool import cleanup_temp_files


def cleanup():
    """统一的资源清理函数（无参数，适合 atexit）"""
    close_postgres_memory()
    cleanup_temp_files()


def _signal_handler(sig, frame):
    """系统终止信号回调"""
    logger.warning(f"收到信号 {sig}，开始优雅关闭...")
    # sys.exit(0) 会触发 atexit 注册的 cleanup()，所以这里不需要手动重复调用
    sys.exit(0)


# 1. 注册程序正常退出（包含 sys.exit）时的清理 Hook
atexit.register(cleanup)

# 2. 捕获系统终止信号（SIGINT / SIGTERM），触发 sys.exit 进而间接调用 cleanup
signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)