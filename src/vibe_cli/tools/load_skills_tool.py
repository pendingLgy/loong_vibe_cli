from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

@tool(description="加载指定专业技能的完整内容")
@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(1),
    retry=retry_if_exception_type((PermissionError, OSError)),  # 针对常见的系统/IO异常重试
    reraise=True  # 如果 3 次全失能，最终抛出异常给 AI
)
def load_skill_detail(skill_name: str, config: RunnableConfig) -> str:
    """加载指定专业技能的完整内容。"""
    # 从 config 中安全获取非全局的 skill_registry 实例
    configurable = config.get("configurable", {})
    registry = configurable.get("skill_registry")

    if not registry:
        return "Error: SkillRegistry instance not found in config."

    return registry.load_skill_content(skill_name)
