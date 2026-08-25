import asyncio
import os
import sys

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langgraph.types import Command
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm

from vibe_cli.env.check_point_memory import postgres_memory
from vibe_cli.env.logger_config import logger
from vibe_cli.node.base_node import call_model, safety_check_node, pend_approval_interrupt_node, route_agent, \
    route_safety_command
from vibe_cli.node.model import AgentState
from vibe_cli.tools import ALL_TOOLS

console = Console()


def load_system_prompt() -> str:
    # 优先读取项目根目录下的 struct.md 作为系统提示词
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    candidates = [
        os.path.join(project_root, "struct.md"),
    ]
    for struct_path in candidates:
        if os.path.exists(struct_path):
            with open(struct_path, encoding="utf-8") as f:
                return f.read()
    return ""


async def build_vibe_app():
    checkpointer = await postgres_memory()

    tool_node = ToolNode(ALL_TOOLS)

    workflow = StateGraph(AgentState)
    workflow.add_node("agent", call_model)
    workflow.add_node("safety_check", safety_check_node)
    workflow.add_node("pend_approval", pend_approval_interrupt_node)
    workflow.add_node("tools", tool_node)

    workflow.set_entry_point("agent")

    # 【第一步】agent 执行完后进行条件分流：普通对话直接结束，工具调用进安全检查
    workflow.add_conditional_edges(
        "agent",
        route_agent,
        {
            "safety_check": "safety_check",
            "end": END
        }
    )

    # 【第二步】安全检查完后的分流：高危去审批，安全去执行工具，拒绝则结束
    workflow.add_conditional_edges(
        "safety_check",
        route_safety_command,
        {
            "tools": "tools",
            "pend_approval": "pend_approval",
            "end": END
        }
    )

    # 审批通过后，由 pend_approval 节点流转到 tools 节点执行
    workflow.add_edge("pend_approval", "tools")
    workflow.add_edge("tools", "agent")

    return workflow.compile(
        checkpointer=checkpointer
    )


# ==================== 终端 REPL 交互主循环 ====================

async def start():
    app = await build_vibe_app()
    config = {"configurable": {"thread_id": "terminal_vibe_session"}}
    system_prompt = load_system_prompt()

    console.print(Panel.fit(
        "🚀 [bold cyan]Local Vibe Coding Assistant (Official Interrupt)[/bold cyan]\n输入你的需求，输入 exit 退出。",
        border_style="cyan"))

    while True:
        try:
            user_input = console.input("\n[bold green]👤 You > [/bold green]")
            if user_input.strip().lower() in ["exit", "quit"]:
                console.print("[yellow]再见！保持 Vibe 状态 🚀[/yellow]")
                break

            if not user_input.strip():
                continue

            # 初始输入流或恢复流
            stream_input = {"messages": [SystemMessage(content=system_prompt), HumanMessage(content=user_input)]}

            while True:
                # 驱动图执行
                has_interrupt = False
                async for event in app.astream(stream_input, config=config, stream_mode="updates"):
                    for node_name, output in event.items():
                        if node_name == "agent":
                            msg = output["messages"][-1]
                            if msg.content:
                                console.print(Markdown(f"\n🤖 **Agent 思考/回复**:\n{msg.content}"))
                            if getattr(msg, "tool_calls", None):
                                for tc in msg.tool_calls:
                                    console.print(
                                        f"[dim]🛠️ 准备调用工具: [bold]{tc['name']}[/bold] 参数: {tc['args']}[/dim]")

                        elif node_name == "tools":
                            msg = output["messages"][-1]
                            console.print(f"[dim]✅ 工具执行返回: {msg.content[:200]}...[/dim]")

                # 检查当前图是否因为节点的 interrupt() 而暂停
                snapshot = await app.aget_state(config)
                if snapshot.tasks and any(task.interrupts for task in snapshot.tasks):
                    has_interrupt = True
                    # 提取 interrupt 传出来的数据
                    interrupt_data = snapshot.tasks[0].interrupts[0].value

                    console.print(Panel(
                        f"⚠️ [bold yellow]安全警报：Agent 准备执行高危操作！[/bold yellow]\n详情: {interrupt_data}",
                        border_style="yellow"
                    ))

                    if Confirm.ask("是否批准执行该操作？", default=True):
                        console.print("[green]✅ 批准执行，唤醒 Agent 中...[/green]")
                        # 🟢 官方标准恢复：使用 Command(resume="approved")
                        stream_input = Command(resume="approved")
                    else:
                        console.print("[red]❌ 已拒绝该操作。[/red]")
                        # 🔴 官方标准恢复：使用 Command(resume="rejected")
                        stream_input = Command(resume="rejected")

                if not has_interrupt:
                    break  # 如果没有触发中断，说明本轮对话正常结束，跳出内层 while 循环等待下一次用户输入

        except KeyboardInterrupt:
            console.print("\n[yellow]操作已中断。[/yellow]")
            break
        except Exception as e:
            import traceback
            console.print(f"[bold red]❌ 发生错误: {str(e)}[/bold red]")
            logger.critical(f"[dim red]{traceback.format_exc()}[/dim red]")


