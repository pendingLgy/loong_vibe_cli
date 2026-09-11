from langgraph.graph.state import CompiledStateGraph
from loguru import logger
from rich.console import Console


def task_resume(app: CompiledStateGraph, config: dict, console: Console) -> bool:
    """
    获取当前线程的状态，检查并自动修复不完整的消息链。
    :return: True 表示无异常或已修复，False 表示未找到可用状态
    """
    # 获取当前 Checkpoint 状态
    state = app.get_state(config)
    if not state or not state.values:
        return False

    if state.next:
        return False

    # history = list(app.get_state_history(config, limit=5))
    messages = state.values.get("messages", [])
    if not messages:
        return True

    have_tasks = list(state.tasks)
    if len(have_tasks) > 0:
        logger.info(f"resume tasks")
        console.print("\n[bold green]🤖 Agent 恢复任务...[/bold green]")

        stream = app.stream_events(input=None, config=config, version="v3")

        # -------------------------------------------------------------
        # 消费 stream.messages 投影：极简打字机（无需解包底层的 event 结构）
        # -------------------------------------------------------------
        for message in stream.messages:
            node = message.node
            if node != 'agent':
                continue

            for text in message.text:
                console.print(text, end="")

            console.print()  # 换行

    return True
