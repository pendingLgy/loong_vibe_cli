import locale
import os
import subprocess

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

from vibe_cli.env.logger_config import logger
from vibe_cli.wraps.workspace_security import enforce_workspace_security


def __decode(data) -> str:
    if not data:
        return ""

    # 如果 subprocess 已经完成了解码
    if isinstance(data, str):
        return data

    # 1. 优先 UTF-8
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass

    # 2. 使用当前操作系统默认编码
    try:
        system_encoding = locale.getpreferredencoding(False)

        if system_encoding:
            return data.decode(system_encoding)
    except (UnicodeDecodeError, LookupError):
        pass

    # 3. Windows 中文环境
    if os.name == "nt":
        try:
            return data.decode("cp936")
        except UnicodeDecodeError:
            pass

    # 4. 最终兜底
    return data.decode("utf-8", errors="replace")


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
@tool(args_schema=ShellInput, description="执行shell工具")
@enforce_workspace_security
@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(1),
    retry=retry_if_exception_type((PermissionError, OSError)),  # 针对常见的系统/IO异常重试
    reraise=True  # 如果 3 次全失能，最终抛出异常给 AI
)
def execute_shell_command(command: str, cwd: str) -> str:
    """在本地安全地执行终端命令行（Shell 命令），并返回输出结果或错误信息。"""


    # 严格校验：确保用户输入了路径
    if not cwd or not cwd.strip():
        return "错误: 缺少必需的 'cwd' 参数。请在对话中明确告知需要操作的目标文件夹路径。"

        # 处理家目录波浪号 '~'
    target_cwd = os.path.expanduser(cwd.strip())

    # 校验用户输入的路径在本地是否存在且为目录
    if not os.path.exists(target_cwd) or not os.path.isdir(target_cwd):
        return f"错误: 用户指定的路径 '{target_cwd}'（原始输入: {cwd}）在本地不存在或不是一个有效的目录。"

    logger.info(f"⚡ [Vibe Coding 终端执行] dir:{target_cwd} 正在运行命令: {command}")

    try:
        # 使用 subprocess 运行命令
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            errors="replace",
            timeout=30,
            cwd=target_cwd,
        )

        stdout = __decode(result.stdout)
        stderr = __decode(result.stderr)

        output = ""

        if stdout:
            output += f"--- STDOUT ---\n{stdout}\n"

        if stderr:
            output += f"--- STDERR ---\n{stderr}\n"

        if not output.strip():
            output = "命令执行成功，无任何输出。"

        # MAX_OUTPUT_LENGTH = 2000
        # if len(output) > MAX_OUTPUT_LENGTH:
        #     output = output[-MAX_OUTPUT_LENGTH:] + "\n...(内容过长已截断)..."

        return output

    except subprocess.TimeoutExpired:
        return "错误: 命令执行超时（超过 30 秒）。"
    except Exception as e:
        logger.exception(f"执行命令时发生异常: {str(e)}")
        return f"执行命令时发生异常: {str(e)}"
