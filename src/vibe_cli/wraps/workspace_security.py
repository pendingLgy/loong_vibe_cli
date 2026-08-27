import functools
import os
import re
from pathlib import Path


def enforce_workspace_security(func):
    """装饰器：在工具执行前拦截 cwd 越权和 command 中的绝对路径越权"""

    @functools.wraps(func)
    def wrapper(command: str, cwd: str, *args, **kwargs):
        # 1. 获取全局工作目录（安全兜底）
        work_dir_env = os.getenv("work_dir")
        WORK_DIR = Path(work_dir_env).resolve()

        # ==========================================
        # 2. 校验并规范化 cwd
        # ==========================================
        target_cwd = Path(cwd).expanduser()
        if not target_cwd.is_absolute():
            target_cwd = (WORK_DIR / target_cwd).resolve()
        else:
            target_cwd = target_cwd.resolve()

        if not target_cwd.exists() or not target_cwd.is_dir():
            return f"安全错误: 指定的工作目录不存在或不是文件夹: {cwd}"

        try:
            target_cwd.relative_to(WORK_DIR)
        except ValueError:
            return (
                f"【安全违规拒绝执行】\n"
                f"工作目录 '{cwd}' 超出了权限范围。\n"
                f"所有操作必须在合法的项目根目录内部: {WORK_DIR}"
            )

        # ==========================================
        # 3. 核心：检查 command 中是否夹带了绝对路径并越权
        # ==========================================
        # 匹配 command 中所有形如 "C:\..."、'C:\...' 或带盘符、或 Linux 绝对路径的片段
        absolute_path_pattern = r'(?:[a-zA-Z]:[\\/]|/)[^\s"\'&|<>]+'
        found_paths = re.findall(absolute_path_pattern, command)

        for path_str in found_paths:
            clean_path = path_str.strip('"\'')
            try:
                p = Path(clean_path).resolve()
            except Exception:
                continue

            # 检查 command 里的绝对路径是否落在了合法的 target_cwd（或 WORK_DIR）内部
            try:
                p.relative_to(target_cwd)
            except ValueError:
                # 🔴 拦截！command 中出现了越权的绝对路径
                return (
                    f"【安全拒绝执行】\n"
                    f"你在 command 中使用了越权的绝对路径：'{clean_path}'。\n"
                    f"严禁操作工作区以外的文件！所有命令必须针对 '{target_cwd}' 内部的文件进行。"
                )

        # 4. 校验全部通过，放行执行原工具（把规范化后的 cwd 传进去）
        return func(command=command, cwd=str(target_cwd), *args, **kwargs)

    return wrapper
