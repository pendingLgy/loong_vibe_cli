import json
import os
import uuid
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage, AIMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm

from vibe_cli.env.check_point_memory import postgres_memory
from vibe_cli.env.logger_config import logger
from vibe_cli.model.deepseek_model import AgentState, get_model_by_name
from vibe_cli.prompt.sys_safe_check_prompt import sys_safe_check_prompt
from vibe_cli.tools import ALL_TOOLS
from vibe_cli.wraps.monitor import monitor_node

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


def route_agent(state: AgentState) -> Literal["safety_check_path", "end_path"]:
    last_message = state["messages"][-1]

    # 关键判断：如果大模型没有调用任何工具（即：普通对话）
    if not (hasattr(last_message, "tool_calls") and last_message.tool_calls):
        return "end_path"  # 👈 直接结束，把大模型的对话内容输出给用户

    # 如果包含工具调用，才送去安全检查节点
    return "safety_check_path"


def route_safety_command(state: AgentState) -> Literal["pend_approval_path", "tools_path", "end_path"]:
    messages = state.get("messages", [])
    if not messages:
        return "end_path"

    last_message = messages[-1]

    if state.get("node_status", '') == 'finished':
        return "end_path"

    if last_message.type == "tool":
        content_str = str(last_message.content)
        if "用户已拒绝" in content_str or "操作已取消" in content_str:
            return "end_path"

    # 2. 需要人工确认（高危新增/编辑等） -> 挂起审批
    if state.get("requires_approval") or state.get("node_status") == "pend_approval":
        return "pend_approval_path"

    # 3. 安全操作（如查看、安全命令） -> 直接执行工具
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools_path"

    return "end_path"


def route_approval(state: AgentState):
    if state.get("node_status") == "normal":
        return "tools_path"

    return "agent_path"


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
            file = result_dict.get("file", "")

            logger.info(f"LLM 安全检查结果: \nis_dangerous={is_dangerous}, \n原因: {reason}, \nfile: {file}")
        except Exception as e:
            logger.exception(f"LLM 安全检查解析失败: {e}，默认按安全处理")
            is_dangerous = False
            reason = "解析出错默认放行"
            file = ""

        # 如果 LLM 判定为高危操作，则进行拦截挂起
        if is_dangerous:
            tc = next((t for t in last_message.tool_calls), last_message.tool_calls[0])

            warning_text = (
                f"⚠️ **安全审批提示**\n"
                f"系统检测到您即将执行敏感动作 (`{tc['name']}`)，参数为：`{tc['args']}`。\n"
                f"**安全评估原因**：{reason}\n"
                f"**操作文件**：{file}\n"
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
            "requires_approval": False,
            "node_status": "finished",
            "messages": tool_messages
        }


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
            "safety_check_path": "safety_check",
            "end_path": END
        }
    )

    # 【第二步】安全检查完后的分流：高危去审批，安全去执行工具，拒绝则结束
    workflow.add_conditional_edges(
        "safety_check",
        route_safety_command,
        {
            "tools_path": "tools",
            "pend_approval_path": "pend_approval",
            "end_path": END
        }
    )

    workflow.add_conditional_edges(
        "pend_approval",
        route_approval,
        {
            "tools_path": "tools",
            "agent_path": "agent",
        }
    )

    # 审批通过后，由 pend_approval 节点流转到 tools 节点执行
    workflow.add_edge("tools", "agent")

    return workflow.compile(
        checkpointer=checkpointer
    )


# ==================== 终端 REPL 交互主循环 ====================

async def start():
    model_name = os.getenv("model_name")
    base_url = os.getenv("url")
    api_key = os.getenv("api_key")
    temperature = os.getenv("temperature", 0.0)
    work_dir = os.getenv("work_dir")

    required_fields = {
        "model_name": model_name,
        "base_url": base_url,
        "api_key": api_key,
        "temperature": temperature,
        "work_dir": work_dir,
    }

    missing_fields = [
        name for name, value in required_fields.items()
        if value is None or str(value).strip() == ""
    ]

    if missing_fields:
        raise ValueError(
            f"以下环境变量不能为空: {', '.join(missing_fields)}"
        )

    app = await build_vibe_app()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
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
            stream_input = {"model_name": model_name,
                            "base_url": base_url,
                            "api_key": api_key,
                            "temperature": temperature,
                            "work_dir": work_dir,
                            "messages": [SystemMessage(content=system_prompt), HumanMessage(content=user_input)]}

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
                            console.print(f"[dim]✅ 工具执行返回: {msg.content}[/dim]")

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
