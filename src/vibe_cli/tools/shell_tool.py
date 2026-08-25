import os
import subprocess

from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from vibe_cli.env.logger_config import logger


# 使用 Pydantic 定义严谨的输入参数结构与描述
class ShellInput(BaseModel):
    command: str = Field(
        ...,
        description="要执行的终端命令，例如 'git status' 或 'pytest'"
    )
    cwd: str = Field(
        ...,
        description="【必须参数】用户在 Prompt 中指定的项目路径或文件夹绝对/相对路径。必须从用户对话中提取，绝对不能留空！"
    )

# --- 1. 实现底层的 Shell 执行函数 ---
@tool(args_schema=ShellInput,description="执行shell工具")
@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(1),
    retry=retry_if_exception_type((PermissionError, OSError)), # 针对常见的系统/IO异常重试
    reraise=True # 如果 3 次全失能，最终抛出异常给 AI
)
def execute_shell_command(command: str, cwd: str) -> str:
    """在本地安全地执行终端命令行（Shell 命令），并返回输出结果或错误信息。"""
    logger.info(f"\n⚡ [Vibe Coding 终端执行] 正在运行命令: {command}")

    # 严格校验：确保用户输入了路径
    if not cwd or not cwd.strip():
        return "错误: 缺少必需的 'cwd' 参数。请在对话中明确告知需要操作的目标文件夹路径。"

    # 处理家目录波浪号 '~'
    target_cwd = os.path.expanduser(cwd.strip())

    # 校验用户输入的路径在本地是否存在且为目录
    if not os.path.exists(target_cwd) or not os.path.isdir(target_cwd):
        return f"错误: 用户指定的路径 '{target_cwd}'（原始输入: {cwd}）在本地不存在或不是一个有效的目录。"

    logger.info(f"\n⚡ [Vibe Coding 终端执行] 工作目录: {target_cwd} | 正在运行命令: {command}")

    try:
        # 使用 subprocess 运行命令
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            # 关键修复：强制使用 utf-8 编码读取，防止 Windows GBK 解码报错
            encoding="utf-8",
            errors="replace",
            cwd=target_cwd,
            timeout=30
        )

        # 拼接标准输出和错误输出
        output = ""
        if result.stdout:
            output += f"--- STDOUT ---\n{result.stdout}\n"
        if result.stderr:
            output += f"--- STDERR ---\n{result.stderr}\n"

        if not output.strip():
            output = "命令执行成功，无任何输出。"

        MAX_OUTPUT_LENGTH = 2000
        if len(output) > MAX_OUTPUT_LENGTH:
            output = output[-MAX_OUTPUT_LENGTH:] + "\n...(内容过长已截断)..."

        return output

    except subprocess.TimeoutExpired:
        return "错误: 命令执行超时（超过 30 秒）。"
    except Exception as e:
        return f"执行命令时发生异常: {str(e)}"
