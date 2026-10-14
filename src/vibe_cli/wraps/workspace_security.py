import functools
import os
import re
from pathlib import Path

from vibe_cli.env.workspace import get_work_dirs


def _is_relative_to(path: Path, parent: Path) -> bool:
    """判断 path 是否位于 parent 内部。"""
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


_WINDOWS_SWITCH_PATTERN = re.compile(r"^/[A-Za-z][A-Za-z0-9_:-]*$")


def _is_windows_switch(token: str) -> bool:
    """判断 token 是否为 Windows 命令开关(如 /b、/S、/F:file),而非文件路径。"""
    return bool(_WINDOWS_SWITCH_PATTERN.match(token))


def enforce_workspace_security(func):
    """装饰器: 在工具执行前拦截 cwd 越权与 command 中的绝对路径越权 (支持多工作区)。"""

    @functools.wraps(func)
    def wrapper(command: str, cwd: str, *args, **kwargs):
        # 1. 获取全部合法工作区 (至少 1 个)
        work_dir_env = os.getenv("work_dir","")
        work_dirs = get_work_dirs(work_dir_env)

        # ==========================================
        # 2. 校验并规范化 cwd: 必须落在任一工作区内
        # ==========================================
        target_cwd = Path(cwd).expanduser()
        if not target_cwd.is_absolute():
            target_cwd = (work_dirs[0] / target_cwd).resolve()
        else:
            target_cwd = target_cwd.resolve()

        if not target_cwd.exists() or not target_cwd.is_dir():
            return f"安全错误: 指定的工作目录不存在或不是文件夹: {cwd}"

        if not any(_is_relative_to(target_cwd, wd) for wd in work_dirs):
            dirs_str = "\n  - ".join(str(wd) for wd in work_dirs)
            return (
                f"【安全违规拒绝执行】\n"
                f"工作目录 '{cwd}' 超出了权限范围。\n"
                f"所有操作必须在以下合法工作区内:\n  - {dirs_str}"
            )

        # ==========================================
        # 3. 检查 command 中是否携带越权绝对路径
        # ==========================================
        absolute_path_pattern = r'(?:[a-zA-Z]:[\\/]|/)[^\s"\'&|<>]+'
        found_paths = re.findall(absolute_path_pattern, command)

        for path_str in found_paths:
            clean_path = path_str.strip('"\'')
            # Windows 命令开关(如 dir /b、findstr /S /N)不是路径, 直接放行
            if os.name == "nt" and _is_windows_switch(clean_path):
                continue
            try:
                p = Path(clean_path).resolve()
            except Exception:
                continue

            if not any(_is_relative_to(p, wd) for wd in work_dirs):
                dirs_str = "\n  - ".join(str(wd) for wd in work_dirs)
                return (
                    f"【安全拒绝执行】\n"
                    f"你在 command 中使用了越权的绝对路径：'{clean_path}'。\n"
                    f"严禁操作工作区以外的文件！合法工作区:\n  - {dirs_str}"
                )

        # 4. 校验全部通过, 放行执行原工具
        return func(command=command, cwd=str(target_cwd), *args, **kwargs)

    return wrapper
