import os
import sys
from pathlib import Path
from loguru import logger

# 1. 移除默认的处理器
logger.remove()

# 2. 添加控制台输出（带颜色、格式化）
logger.add(
    sys.stdout,
    colorize=True,
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
           "<level>{level: <8}</level> | "
           "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
           "<level>{message}</level>",
    level="INFO"
)

# ── 动态计算项目根目录 ──
# 当前文件在 src/vibe_cli/env/logger.py
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

# ── 从环境变量中获取配置的日志目录（如果没配置则默认为 "logs"）──
log_dir_env = os.getenv("logger.dir","")

# 兼容用户配置的是相对路径还是绝对路径
LOG_DIR = Path(log_dir_env)
if not LOG_DIR.is_absolute():
    LOG_DIR = BASE_DIR / LOG_DIR  # 如果是相对路径，拼接到项目根目录下

# 确保日志文件夹存在
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 3. 添加文件输出（大小达到 500MB 时切分、保留 7 天、压缩存档）
logger.add(
    LOG_DIR / "vibe_cli_{time:YYYY-MM-DD_HH-mm-ss}.log",  # 建议加上时分秒，防止同大小覆盖
    rotation="500 MB",      # 👈 核心修改：文件达到 500MB 时自动切分
    retention="7 days",     # 日志最多保留 7 天
    compression="zip",      # 过期日志自动打包为 zip
    level="DEBUG",          # 文件中记录更详细的 DEBUG 级别日志
    encoding="utf-8"
)

# 导出配置好的 logger
__all__ = ["logger"]