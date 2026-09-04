import re
from pathlib import Path

from vibe_cli.env.logger_config import logger


class SkillRegistry:
    def __init__(self, skills_dir: str | Path):
        self.skills_dir = Path(skills_dir)
        self.skill_paths: dict[str, Path] = {}  # 存储 name -> SKILL.md 绝对路径的映射
        self.index_content = ""
        self._load_skills()

    def _parse_skill_name(self, file_path: Path) -> str | None:
        """从 SKILL.md 的 Frontmatter 中解析出 name 字段"""
        try:
            content = file_path.read_text(encoding="utf-8")
            match = re.search(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
            if match:
                frontmatter = match.group(1)
                name_match = re.search(r"^name:\s*(.+)$", frontmatter, re.MULTILINE)
                if name_match:
                    return name_match.group(1).strip()
        except Exception as e:
            logger.warning(f"Failed to read/parse frontmatter for {file_path}: {e}")
        return None

    def _load_skills(self):
        """手动触发扫描与加载 skills 目录下的 xxx/SKILL.md 结构"""
        if not self.skills_dir.exists():
            logger.warning(f"Skills directory not found: {self.skills_dir}")
            return

        # 1. 加载根目录下的 skill_index.md
        index_file = self.skills_dir.joinpath("skill_index.md")
        if index_file.exists():
            self.index_content = index_file.read_text(encoding="utf-8")

        # 2. 匹配所有子目录下的 SKILL.md
        for skill_file in self.skills_dir.glob("*/SKILL.md"):
            try:
                # 优先从文件内容解析 name，解析不到则降级使用父目录名
                skill_name = self._parse_skill_name(skill_file) or skill_file.parent.name
                logger.success(f"load skill: {skill_name} dir:{skill_file}")
                self.skill_paths[skill_name] = skill_file
            except Exception as e:
                logger.exception(f"Failed to register skill at {skill_file}: {e}")

    def get_skill_path(self, skill_name: str) -> Path | None:
        """根据技能名称获取对应的 SKILL.md 文件路径"""
        return self.skill_paths.get(skill_name)

    def load_skill_content(self, skill_name: str) -> str:
        """根据名称安全加载 SKILL.md 全文内容"""
        path = self.get_skill_path(skill_name)
        if not path or not path.exists():
            return f"Error: Skill '{skill_name}' not found."
        return path.read_text(encoding="utf-8")
