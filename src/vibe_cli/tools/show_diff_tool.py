# show_diff_tool.py - Use IntelliJ IDEA diff command to view file changes.
import atexit
import os
import subprocess
from pathlib import Path

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

from vibe_cli.env.logger_config import logger

# 全局记录生成的临时文件，以便程序退出时自动清理
_TEMP_FILES_TO_CLEAN = set()


def cleanup_temp_files():
    """程序退出时自动清理生成的临时 HEAD 文件和空目录"""
    for file_path in list(_TEMP_FILES_TO_CLEAN):
        try:
            p = Path(file_path)
            if p.exists():
                p.unlink()
        except Exception:
            pass
    for file_path in list(_TEMP_FILES_TO_CLEAN):
        try:
            p = Path(file_path).parent
            if p.exists() and not any(p.iterdir()):
                p.rmdir()
        except Exception:
            pass


def __git_head_content(repo_dir: Path, rel_path: str) -> bytes | None:
    """Read original content of file at Git HEAD; return None when unavailable."""
    try:
        result = subprocess.run(
            ['git', 'show', f'HEAD:{rel_path}'],
            capture_output=True,
            cwd=str(repo_dir),
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout
    except Exception as exc:
        logger.warning(f'git show HEAD:{rel_path} failed: {exc}')
    return None


class ShowDiffInput(BaseModel):
    file_path: str = Field(
        ...,
        description='Path of the file whose changes to view (relative or absolute inside workspace)',
    )
    cwd: str = Field(
        ...,
        description='Workspace root directory (project root) as absolute or relative path',
    )


@tool(args_schema=ShowDiffInput,
      description='Use IntelliJ IDEA diff command to open a diff view of the file vs its Git HEAD version')
@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(1),
    retry=retry_if_exception_type((PermissionError, OSError)),  # 针对常见的系统/IO异常重试
    reraise=True  # 如果 3 次全失能，最终抛出异常给 AI
)
def show_diff(file_path: str, cwd: str) -> str:
    """Open IntelliJ IDEA diff for a single file vs Git HEAD."""

    # 1. 规范化工作区路径
    work_dir = Path(cwd).expanduser().resolve()

    # 2. 解析目标文件绝对路径
    target = Path(file_path)
    if not target.is_absolute():
        target = work_dir.joinpath(target)
    target = target.resolve()

    if not target.is_file():
        return f'Error: file does not exist or is not a regular file: {target}'

    # 3. 确定 IDEA 可执行文件路径（优先级：环境变量 -> 默认命令）
    idea_exe = os.getenv("IDEA_EXECUTABLE") or os.getenv("IDEA_HOME")
    if not idea_exe:
        # 尝试使用系统 PATH 中默认的命令
        idea_exe = "idea64.exe" if os.name == "nt" else "idea"

    # 4. 计算相对于 Git 仓库根目录的相对路径 (rel_path)
    try:
        rel_path = target.relative_to(work_dir).as_posix()
    except ValueError:
        return f'Error: file path {target} is outside the workspace root {work_dir}'

    # 5. 获取 Git HEAD 版本的内容
    head_content = __git_head_content(work_dir, rel_path)

    # 6. 在临时目录生成 .HEAD 文件用于比对（采用路径扁平化命名，防止同名文件冲突）
    tmp_dir = work_dir.joinpath('.vcl', '.idea_diff')
    tmp_dir.mkdir(parents=True, exist_ok=True)

    safe_name = rel_path.replace('/', '_') + '.HEAD'
    head_file = tmp_dir.joinpath(safe_name)

    head_file.write_bytes(head_content if head_content is not None else b'')

    # 将临时文件加入自动清理队列
    _TEMP_FILES_TO_CLEAN.add(str(head_file))

    try:
        # 7. 异步拉起 IDEA Diff 窗口
        subprocess.Popen(
            [idea_exe, 'diff', str(head_file), str(target)],
            cwd=str(work_dir),
        )
    except Exception as exc:
        logger.exception('Failed to launch IDEA diff')
        return f'Failed to launch IDEA diff (is IDEA in your PATH?): {exc}'

    logger.info(f'IDEA diff opened: {head_file} <-> {target}')
    return (
            'IDEA diff view opened.' + os.linesep
            + f'  Left  (HEAD)  : {head_file}' + os.linesep
            + f'  Right (Local) : {target}' + os.linesep
            + 'For new files the left side is empty (no HEAD version).'
    )
