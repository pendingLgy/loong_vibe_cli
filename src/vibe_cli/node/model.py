from typing import TypedDict, Annotated, NotRequired

from langchain_openai import ChatOpenAI
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class NodeStatus:
    NORMAL = "normal"  # 普通状态/对话
    PEND_APPROVAL = "pend_approval"  # 需要人工确认
    FINISHED = "finished"  # 结束状态


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    requires_approval: bool  # 标记当前是否因高危操作被挂起
    node_status: str  # see NodeStatus

    model_name: str  # 💡 新增：动态指定当前请求使用哪个模型
    temperature: NotRequired[float]
    base_url: str
    api_key: str


class SafetyCheckResult(BaseModel):
    is_dangerous: bool = Field(description="该工具调用是否属于高危、具有破坏性、删除数据、修改系统配置等敏感操作")
    reason: str = Field(description="判断为高危或安全的具体原因")


def get_model_by_name(
        model_name: str,
        base_url: str,
        api_key: str,
        temperature: float = 0.0,
) -> ChatOpenAI:
    """模型工厂：根据名称及参数返回对应的 ChatOpenAI 实例。

    参数:
        model_name: 模型名称 (例如 'gpt-4o', 'deepseek-chat', 'claude-3-5-sonnet' 等)
        temperature: 采样温度，默认 0
        base_url: 接口代理地址，如果为 None 则自动读取环境变量 OPENAI_BASE_URL
        api_key: 密钥，如果为 None 则自动读取环境变量 OPENAI_API_KEY
    """

    model = ChatOpenAI(
        model=model_name,
        temperature=temperature,
        base_url=base_url,
        api_key=api_key,
    )

    return model
