from vibe_cli.tools.load_skills_tool import load_skill_detail
from vibe_cli.tools.shell_tool import execute_shell_command
from vibe_cli.tools.show_diff_tool import show_diff

# Global tool function registry
ALL_TOOLS = [
    execute_shell_command,
    show_diff,
    load_skill_detail,
]
