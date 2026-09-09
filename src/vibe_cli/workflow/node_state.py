from typing import TypedDict, Annotated

from langgraph.graph.message import add_messages


class NodeStatus:
    NORMAL = "normal"  # 普通状态/对话
    PEND_APPROVAL = "pend_approval"  # 需要人工确认
    FINISHED = "finished"  # 结束状态


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    requires_approval: bool  # 标记当前是否因高危操作被挂起
    node_status: str  # see NodeStatus

    model_provider: str
    work_dir: str
