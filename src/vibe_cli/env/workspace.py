"""工作区配置解析: 支持单个或多个 work_dir。

work_dir 环境变量可声明多个工作目录, 使用英文逗号、分号或换行分隔, 例如:
    work_dir='dir_a,dir_b'
列表中的第一个目录为主工作目录 (primary), 用作默认 cwd 与技能扫描位置。
"""
from __future__ import annotations

import os
from pathlib import Path

# 多个目录的分隔符 (逗号 / 分号 / 换行)
_SEPARATORS = (",", ";", "\n", "\r")


def _path_key(path: Path) -> str:
    """生成用于去重的路径键 (Windows 下不区分大小写)。"""
    return str(path).casefold() if os.name == "nt" else str(path)


def split_work_dir(raw: str | None) -> list[str]:
    """将原始 work_dir 字符串拆分为去空白的目录字符串列表。"""
    if not raw:
        return []
    normalized = raw
    for sep in _SEPARATORS:
        normalized = normalized.replace(sep, "\n")
    tokens: list[str] = []
    for line in normalized.split("\n"):
        cleaned = line.strip().strip(chr(39) + chr(34)).strip()
        if cleaned:
            tokens.append(cleaned)
    return tokens


def get_work_dirs(work_dir:str) -> list[Path]:
    """解析 work_dir, 返回去重后的绝对路径列表 (保持声明顺序)。

    未配置时回退到当前工作目录, 保证至少返回 1 个合法工作区。
    """
    raw_dirs = split_work_dir(work_dir)
    if not raw_dirs:
        raw_dirs = [os.getcwd()]

    result: list[Path] = []
    seen: set[str] = set()
    for item in raw_dirs:
        try:
            resolved = Path(item).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        key = _path_key(resolved)
        if key in seen:
            continue
        seen.add(key)
        result.append(resolved)

    if not result:
        result.append(Path.cwd().resolve())
    return result


def get_primary_work_dir(work_dir: str) -> Path:
    """返回主工作目录 (列表首个), 无配置时回退到当前工作目录。"""
    return get_work_dirs(work_dir)[0]



