# 定义固定的 SystemMessage ID 常量
import os

from langchain_core.messages import SystemMessage
from loguru import logger

from vibe_cli.prompt.sys_env_prompt import sys_vibe_coding_agent
from vibe_cli.skill.loading_skill import SkillRegistry

SYS_ID_VIBE_CODING = "sys-vibe-coding"
SYS_ID_STRUCT = "sys-struct"
SYS_ID_SKILL_INDEX = "sys-skill-index"

def _load_system_struct_prompt() -> str:
    # 1. 优先从当前工作目录 (work_dir 或 os.getcwd()) 下的 .vcl 目录中读取 struct.md
    work_dir = os.getenv("work_dir") or os.getcwd()

    candidates = [
        os.path.join(work_dir, ".vcl", "struct.md"),
    ]

    # 2. 如果需要保留对项目根目录的兜底，可以在这里继续添加备选路径
    # project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    # candidates.append(os.path.join(project_root, "struct.md"))
    for struct_path in candidates:
        if os.path.exists(struct_path):
            try:
                with open(struct_path, encoding="utf-8") as f:
                    logger.success(f"load vcl struct, dir:{struct_path}")
                    return f.read()
            except Exception as e:
                logger.exception(f"load vcl md error {str(e)}")
                continue

    return ""

def _init_sys_prompt(skill_registry: SkillRegistry, work_dir: str | None) -> list[SystemMessage]:
    system_vibe_coding_prompt = sys_vibe_coding_agent(work_dir)
    system_struct_prompt = _load_system_struct_prompt()
    system_skill_index_prompt = skill_registry.index_content

    system_prompts = [
        SystemMessage(
            id=SYS_ID_VIBE_CODING,
            content=system_vibe_coding_prompt,
            additional_kwargs={"compressible": False}
        )
    ]

    if system_struct_prompt and system_struct_prompt.strip():
        system_prompts.append(
            SystemMessage(
                id=SYS_ID_STRUCT,
                content=system_struct_prompt,
                additional_kwargs={"compressible": False}
            )
        )

    if system_skill_index_prompt and system_skill_index_prompt.strip():
        system_prompts.append(
            SystemMessage(
                id=SYS_ID_SKILL_INDEX,
                content=system_skill_index_prompt,
                additional_kwargs={"compressible": False}
            )
        )

    return system_prompts


def sync_system_prompts(app, config: dict, skill_registry: SkillRegistry, work_dir: str | None):
    """每次启动时调用：自动创建或覆盖现有的 3 个 SystemMessage。"""

    # 1. 生成带有固定 ID 的最新系统提示词列表
    latest_sys_prompts = _init_sys_prompt(skill_registry, work_dir)

    # 2. 直接更新状态（LangGraph 匹配到已存在的 ID 会自动覆盖，未匹配到则追加）
    app.update_state(
        config=config,
        values={"messages": latest_sys_prompts}
    )

    sys_ids_str = ", ".join(m.id for m in latest_sys_prompts if m.id)

    logger.success(f"系统提示词已成功同步/覆盖 (IDs: {sys_ids_str})")
