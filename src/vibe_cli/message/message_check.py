from langchain_core.messages import AIMessage, ToolMessage, RemoveMessage
from langgraph.graph.state import CompiledStateGraph
from loguru import logger


def check_message_completed(app: CompiledStateGraph, config: dict, ) -> dict:
    """
    全量扫描所有消息：如果 AIMessage 包含多个 tool_calls 但未全部完成，
    则删除该 AIMessage 及其已经生成的局部 ToolMessage。
    """
    state = app.get_state(config)
    messages = state.values.get("messages", [])
    if not messages:
        return {}

    # 1. 建立 tool_call_id -> ToolMessage 的映射字典
    tool_msg_map = {}
    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.tool_call_id:
            tool_msg_map[msg.tool_call_id] = msg

    messages_to_remove = []

    # 2. 遍历检查所有的 AIMessage
    for msg in messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            all_call_ids = [call.get("id") for call in msg.tool_calls if call.get("id")]

            # 检查是否有任意一个 tool_call 缺失对应的 ToolMessage
            incomplete_ids = [call_id for call_id in all_call_ids if call_id not in tool_msg_map]

            if incomplete_ids:
                # 存在未完成的调用，判定整个 AIMessage 不完整 -> 标记删除 AIMessage
                logger.info(f"[State Cleansing] 发现未全部完成的 AIMessage (ID: {msg.id}), 标记删除...")
                messages_to_remove.append(RemoveMessage(id=msg.id))

                # 级联清理：把该 AIMessage 已经成功返回的那个局部 ToolMessage 也一并删掉
                for call_id in all_call_ids:
                    if call_id in tool_msg_map:
                        completed_tool_msg = tool_msg_map[call_id]
                        logger.info(f"[State Cleansing] 级联删除已返回但孤立的 ToolMessage (ID: {completed_tool_msg.id})...")
                        messages_to_remove.append(RemoveMessage(id=completed_tool_msg.id))

    # 3. 批量发送 RemoveMessage 指令
    if messages_to_remove:
        state_update = {"messages": messages_to_remove}
        app.update_state(config, state_update)
        logger.info("remove messages")
        return {"messages": messages_to_remove}

    return {}
