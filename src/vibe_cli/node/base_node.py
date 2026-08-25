import json
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import interrupt

from vibe_cli.env.logger_config import logger
from vibe_cli.node.model import AgentState, get_model_by_name
from vibe_cli.node.monitor import monitor_node
from vibe_cli.prompt.sys_safe_check_prompt import sys_safe_check_prompt
from vibe_cli.tools import ALL_TOOLS


def route_agent(state: AgentState):
    last_message = state["messages"][-1]

    # 关键判断：如果大模型没有调用任何工具（即：普通对话）
    if not (hasattr(last_message, "tool_calls") and last_message.tool_calls):
        return "end"  # 👈 直接结束，把大模型的对话内容输出给用户

    # 如果包含工具调用，才送去安全检查节点
    return "safety_check_path"


def route_safety_command(state: AgentState) -> Literal["pend_approval", "tools", "end"]:
    messages = state.get("messages", [])
    if not messages:
        return "end"

    last_message = messages[-1]

    if state.get("node_status", '') == 'finished':
        return "end"

    if last_message.type == "tool":
        content_str = str(last_message.content)
        if "用户已拒绝" in content_str or "操作已取消" in content_str:
            return "end"

    # 2. 需要人工确认（高危新增/编辑等） -> 挂起审批
    if state.get("requires_approval") or state.get("node_status") == "pend_approval":
        return "pend_approval"

    # 3. 安全操作（如查看、安全命令） -> 直接执行工具
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"

    return "end"

@monitor_node("call_model")
def call_model(state: AgentState):
    """
    大模型推理节点
    :param state:
    :return:
    """

    messages = state.get("messages", [])
    response = get_model_by_name(model_name=state.get("model_name"), base_url=state.get("base_url"),
                                 api_key=state.get("api_key"), temperature=state.get("temperature", 0.0)
                                 ).bind_tools(ALL_TOOLS).invoke(messages)

    return {
        "messages": [response],
        "requires_approval": False,
        "node_status": "normal"
    }


@monitor_node("safety_check_node")
def safety_check_node(state: AgentState):
    """
    安全检查与分流节点（核心拦截点）
    :param state:
    :return:
    """

    messages = state.get("messages", [])
    if not messages:
        return {"requires_approval": False, "node_status": "normal"}

    last_message = messages[-1]

    # 检查是否为 AIMessage，包含工具调用，且状态不是挂起审批
    if isinstance(last_message, AIMessage) and last_message.tool_calls and state.get(
            "node_status") != "pend_approval":

        if not last_message.content:
            logger.info("检测到最后一条 AI 消息的 content 为空（纯工具调用）")

            # 构造给 DeepSeek 的提示词，让其直接以 JSON 格式返回判断结果
        tool_calls_info = str(last_message.tool_calls)
        prompt = sys_safe_check_prompt(tool_calls_info)

        try:
            # 直接调用标准支持模型
            response = get_model_by_name(model_name=state.get("model_name"), base_url=state.get("base_url"),
                                         api_key=state.get("api_key"), temperature=state.get("temperature", 0.0)
                                         ).invoke([HumanMessage(content=prompt)])
            response_text = response.content.strip()

            # 清理可能存在的 markdown 代码块符号 (```json ... ```)
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]

            result_dict = json.loads(response_text.strip())
            is_dangerous = result_dict.get("is_dangerous", False)
            reason = result_dict.get("reason", "无原因说明")

            logger.info(f"LLM 安全检查结果: is_dangerous={is_dangerous}, 原因: {reason}")
        except Exception as e:
            logger.exception(f"LLM 安全检查解析失败: {e}，默认按安全处理")
            is_dangerous = False
            reason = "解析出错默认放行"

        # 如果 LLM 判定为高危操作，则进行拦截挂起
        if is_dangerous:
            tc = next((t for t in last_message.tool_calls), last_message.tool_calls[0])

            warning_text = (
                f"⚠️ **安全审批提示**\n"
                f"系统检测到您即将执行敏感动作 (`{tc['name']}`)，参数为：`{tc['args']}`。\n"
                f"**安全评估原因**：{reason}\n"
                f"为了保障数据安全，该操作已被挂起，请选择 **确认** 或 **拒绝**。"
            )

            # 将警告提示追加到当前 AI 消息的 content 中
            last_message.content = (last_message.content or "") + "\n\n" + warning_text

            return {
                "requires_approval": True,
                "node_status": "pend_approval",
                "messages": messages
            }

    return {"requires_approval": False, "node_status": "normal"}


@monitor_node("pend_approval_interrupt_node")
def pend_approval_interrupt_node(state: AgentState):
    """
    🛑 官方现代标准：使用 interrupt() 动态中断
    它会将数据抛出给外部，并挂起等待 resume。
    """
    messages = state["messages"]
    last_message = messages[-1]

    # 提取当前准备执行的高危工具调用信息，方便在中断时展示给用户
    tool_calls = getattr(last_message, "tool_calls", [])

    # 调用 interrupt() 挂起，并把需要审批的上下文传出去
    human_decision = interrupt({
        "type": "approval_required",
        "tool_calls": tool_calls,
        "reason": "Agent 准备修改本地文件或执行敏感终端命令，需要人工审核。"
    })

    # ==========================================
    # 当外部通过 Command(resume=...) 恢复后，代码会从这里继续执行！
    # ==========================================

    # human_decision 就是外部通过 resume 传进来的值（例如 "approve" 或 "reject"）
    if human_decision == "approved":
        return {
            "requires_approval": False,
            "node_status": "normal"
        }
    else:
        # 用户拒绝：直接在节点内生成 ToolMessage 拦截，安全返回
        tool_messages = [
            ToolMessage(
                content="用户已拒绝执行该高危操作，请取消该操作并询问用户下一步指示。",
                tool_call_id=tc["id"]
            )
            for tc in tool_calls
        ]
        return {
            "requires_approval": True,
            "node_status": "finished",
            "messages": tool_messages
        }
