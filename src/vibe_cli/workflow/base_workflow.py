import json
import os
import uuid
from pathlib import Path
from typing import Literal, Any

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm

from vibe_cli.env.check_point_memory import postgres_memory
from vibe_cli.env.logger_config import logger
from vibe_cli.message.message_compactor import plan_compaction, summarize_with_model
from vibe_cli.model.model_factory import get_model
from vibe_cli.prompt.sys_diff_prompt import diff_prompt
from vibe_cli.prompt.sys_safe_check_prompt import sys_safe_check_prompt
from vibe_cli.skill.loading_skill import SkillRegistry
from vibe_cli.tools import ALL_TOOLS, DANGEROUS_TOOLS
from vibe_cli.workflow.load_sys_prompt import sync_system_prompts
from vibe_cli.workflow.node_state import AgentState
from vibe_cli.wraps.monitor import monitor_node

SUMMARY_BOOKMARK_PREFIX = "【历史摘要】以下是早期对话的摘要（已压缩省略）：\\n"


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


def route_check_mutation(state: dict) -> str:
    """
    【基于用户意图的条件路由】
    只有当用户明确要求查看工作区变更/Diff，或者工具调用本身与查看变更相关时，才进入 diff 节点。
    """
    messages = state.get("messages", [])
    if not messages:
        return "skip_diff_path"

    # 1. 向上回溯，找到最近的一条人类用户输入（HumanMessage）
    last_human_msg = None
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            last_human_msg = msg.content
            break

    # 如果找不到用户输入，默认跳过
    if not last_human_msg:
        return "skip_diff_path"

    # 2. 定义触发变更查看的意图关键词
    diff_intent_keywords = [
        "diff", "变更", "改动", "修改了什么", "查看状态",
        "git status", "git diff", "变化", "检查代码", "看看代码"
    ]

    # 检查用户的输入中是否包含上述意图
    user_wants_diff = any(keyword in last_human_msg.lower() for keyword in diff_intent_keywords)

    if user_wants_diff:
        logger.info("🔍 [Diff Route Check] 检测到用户有查看变更的意图，进入 dynamic_diff_node")
        return "run_diff_path"
    else:
        logger.info("⏩ [Diff Route Check] 用户未要求查看变更，直接跳过 diff 节点")
        return "skip_diff_path"


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
    response = get_model(provider=state.get("model_provider")).bind_tools(ALL_TOOLS).invoke(messages)

    return {
        "messages": [response],
        "requires_approval": False,
        "node_status": "normal"
    }


@monitor_node("compact_msg_node")
def compact_msg_node(state):
    """消息历史压缩节点（图入口）。

    在每轮对话开始前运行。当累积的对话超出配置的阈值时，
    将最旧的完整对话轮次通过大模型归纳为滚动摘要，并通过 RemoveMessage 进行裁剪，
    从而保持大模型上下文在安全范围内。如果归纳失败，则安全跳过。
    """
    messages = state.get("messages", [])
    if not messages:  # 冷启动无消息
        return {}
    threshold = int(os.getenv("compress_threshold_chars", "12000"))
    keep_last_turns = int(os.getenv("compress_keep_last_turns", "3"))

    # 获取由 plan_compaction 返回的起止绝对索引及历史消息
    plan = plan_compaction(messages, keep_last_turns=keep_last_turns, threshold_chars=threshold)
    if plan is None:  # 上下文体积依然在安全范围内，无需压缩
        return {}
    start_idx, cutoff, history = plan

    # 1. 提取所有匹配的旧摘要内容
    # 2. 遍历每一行/每一条旧摘要，在末尾拼接逗号 ","
    # 3. 用换行符连接成一个多行汇总字符串
    old_summary = "\n".join(
        f"对话{index},内容：\n{str(m.content)} \n"
        for index, m in enumerate(messages[start_idx:cutoff], start=start_idx)
        if m.content
    )

    model = get_model(provider=state.get("model_provider"))

    new_summary = summarize_with_model(model, old_summary, history)
    if not new_summary:  # 摘要生成失败，本轮放弃裁剪以防破坏状态
        return {}

    # 1. 明确待压缩区和保留区
    history_to_compress = messages[start_idx:cutoff]
    kept_messages = messages[cutoff:]

    # 2. 移除从 start_idx 开始的【所有】后续消息（防止顺序错乱）
    removals = [
        RemoveMessage(id=m.id)
        for m in messages[start_idx:]
        if getattr(m, "id", None)
    ]

    # 3. 构造历史摘要消息
    summary_msg = HumanMessage(
        id="summary-" + uuid.uuid4().hex[:8],
        content=SUMMARY_BOOKMARK_PREFIX + new_summary,
        additional_kwargs={"is_summary": True}
    )

    # 4. 重新克隆保留区消息（重新赋予新 ID，确保被 LangGraph 识别为全新追加的消息）
    rebuilt_tail = []
    for m in kept_messages:
        clone = m.model_copy(deep=True)
        clone.id = "keep-" + uuid.uuid4().hex[:8]
        rebuilt_tail.append(clone)

    logger.info(
        "[Compact] absorbed {} msgs, rebuilt tail with summary len={}",
        len(history_to_compress),
        len(new_summary)
    )

    # 5. 按严格顺序返回：删除指令 -> 摘要消息 -> 保留的最近消息
    return {"messages": removals + [summary_msg] + rebuilt_tail}


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
    if (
            isinstance(last_message, AIMessage)
            and last_message.tool_calls
            and state.get("node_status") != "pend_approval"
            # 确保当前调用的所有工具名称，都不在危险工具列表中
            and not any(tc.get("name") in DANGEROUS_TOOLS for tc in last_message.tool_calls)
    ):

        if not last_message.content:
            logger.info("检测到最后一条 AI 消息的 content 为空（纯工具调用）")

            # 构造给 DeepSeek 的提示词，让其直接以 JSON 格式返回判断结果
        tool_calls_info = str(last_message.tool_calls)
        prompt = sys_safe_check_prompt(tool_calls_info)

        try:
            # 直接调用标准支持模型
            response = get_model(provider=state.get("model_provider")).invoke([HumanMessage(content=prompt)])
            response_text = response.text.strip()

            # 清理可能存在的 markdown 代码块符号 (```json ... ```)
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]

            result_dict = json.loads(response_text.strip())
            is_dangerous = result_dict.get("is_dangerous", False)
            reason = result_dict.get("reason", "无原因说明")
            file = result_dict.get("file", "")

            logger.info(f"LLM 安全检查结果: is_dangerous={is_dangerous} 原因: {reason} file: {file}")
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
            last_message.content = (last_message.text or "") + "\n\n" + warning_text

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


@monitor_node("dynamic_shell_diff_node")
def dynamic_shell_diff_node(state: AgentState) -> dict:
    """
        【纯 Python Git 变更收集与报告生成节点】
        不使用 LLM 总结，直接通过 Git 命令、本地解析行号（@@ 块）及绝对路径拼装报告。
        """
    messages = state.get("messages", [])
    if not messages:
        return {}

    last_message = messages[-1]
    cwd = os.getenv("work_dir")

    try:

        # 3. 让 LLM 专注于总结变更内容
        logger.info("[Git Diff Node] 正在请求 LLM 总结变更内容...")

        prompt = [
            SystemMessage(content=(diff_prompt())),
            HumanMessage(content=(
                f"当前工作区绝对路径: {cwd}\n\n"
                f"--- 变更内容 ---\n{last_message}\n\n"
            ))
        ]

        response = get_model(provider=state.get("model_provider")).invoke(prompt)

        summary_report = response.text
        if not summary_report:
            return {}

        formatted_report = f"\n\n{summary_report}"

        # 或者作为独立的辅助信息（推荐，避免污染原消息结构）
        report_msg = SystemMessage(content=f"📂 [自动检测到工作区变更]:{formatted_report}")
        return {
            "requires_approval": False,
            "node_status": "normal",
            "messages": report_msg
        }

    except Exception as e:
        logger.exception(f"动态生成或执行 Diff 命令失败: cmd:{cwd} \nmsg: {e}")
        return {
            "requires_approval": False,
            "node_status": "normal",
        }


def build_vibe_app():
    checkpointer = postgres_memory()

    tool_node = ToolNode(ALL_TOOLS)

    workflow = StateGraph(AgentState)
    workflow.add_node("compact_msg", compact_msg_node)
    workflow.add_node("agent", call_model)
    workflow.add_node("safety_check", safety_check_node)
    workflow.add_node("pend_approval", pend_approval_interrupt_node)
    workflow.add_node("tools", tool_node)
    workflow.add_node("dynamic_diff_node", dynamic_shell_diff_node)

    workflow.set_entry_point("compact_msg")
    workflow.add_edge("compact_msg", "agent")

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

    workflow.add_conditional_edges(
        "tools",
        route_check_mutation,
        {
            "run_diff_path": "dynamic_diff_node",
            "skip_diff_path": "agent"
        }
    )

    workflow.add_edge("dynamic_diff_node", "agent")

    return workflow.compile(
        checkpointer=checkpointer
    )


def create_workflow_img(app: CompiledStateGraph[Any, Any, Any, Any]):
    try:
        # 1. 获取工作目录，如果没有设置则默认使用当前运行目录
        target_dir = os.getenv("work_dir") or os.getcwd()
        output_path = Path(target_dir).joinpath("vibe_agent_workflow.png")

        # 2. 生成并保存图片
        png_data = app.get_graph().draw_mermaid_png()
        with open(output_path, "wb") as f:
            f.write(png_data)

        logger.success(f"[green]✅ 已成功生成流程图图片: {output_path}[/green]")
    except Exception as e:
        logger.warning(f"生成 PNG 流程图失败: {e}")


# ==================== 终端 REPL 交互主循环 ====================

def start():
    work_dir = os.getenv("work_dir")
    model_provider = os.getenv("model_provider")

    required_fields = {
        "work_dir": work_dir,
        "model_provider": model_provider,
    }

    missing_fields = [
        name for name, value in required_fields.items()
        if value is None or str(value).strip() == ""
    ]

    if missing_fields:
        raise ValueError(
            f"以下环境变量不能为空: {', '.join(missing_fields)}"
        )

    # 例如在环境变量和 work_dir 准备好之后：
    skills_dir = Path(work_dir).joinpath(".vcl", "skills")
    skill_registry = SkillRegistry(skills_dir)

    config = {
        "configurable": {
            # "thread_id": str(uuid.uuid4()),
            "thread_id": "123025",
            "skill_registry": skill_registry
        }
    }

    app = build_vibe_app()

    create_workflow_img(app)
    sync_system_prompts(app, config, skill_registry, work_dir)

    console = Console()
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

            # 🧩 新增：拦截 /skill 或 /skills 快捷指令
            cleaned_input = user_input.strip().lower()
            if cleaned_input in ["/skill", "/skills"]:
                index_content = skill_registry.index_content
                if not index_content:
                    console.print(
                        "[yellow]⚠️ 当前工作区暂未发现已注册的技能清单（skills/skill_index.md 不存在或为空）。[/yellow]")
                else:
                    console.print(
                        Panel(Markdown(f"### 🧩 当前已注册的技能清单\n\n{index_content}"), border_style="cyan"))
                continue  # 拦截成功后直接进入下一轮循环，不走后续的大模型 Agent 流程

            # 初始输入流或恢复流
            stream_input = {"model_provider": model_provider, "work_dir": work_dir, "messages": [HumanMessage(content=user_input)]}

            while True:

                # 1. 异步唤起并获取 v3 AsyncGraphRunStream 控制器
                stream = app.stream_events(input=stream_input, config=config, version="v3")

                console.print("\n[bold green]🤖 Agent 响应中...[/bold green]")

                # -------------------------------------------------------------
                # 2. 消费 stream.messages 投影：极简打字机（无需解包底层的 event 结构）
                # -------------------------------------------------------------
                for message in stream.messages:
                    node = message.node
                    if node != 'agent':
                        continue

                    for text in message.text:
                        console.print(text, end="")

                    console.print()  # 换行

                # -------------------------------------------------------------
                # 3. 中断处理 (Human-in-the-Loop)
                # -------------------------------------------------------------
                if stream.interrupted:
                    console.print("\n[bold yellow]⏸️ 触发安全拦截，等待用户授权...[/bold yellow]")

                    # 从 stream.interrupts 提取具体的审批 Payload
                    for interrupt_payload in stream.interrupts:
                        console.print(
                            Panel(
                                f"⚠️ [bold yellow]敏感操作需要确认：[/bold yellow]\n{interrupt_payload.value}",
                                title="人机协作审批 (HITL)",
                                border_style="yellow",
                            )
                        )

                    # 终端交互确认
                    if Confirm.ask("是否批准 Agent 继续执行此操作？", default=True):
                        console.print("[green]✅ 已批准，恢复 Agent 执行...[/green]")
                        # 传入 Command 响应恢复信号
                        stream_input = Command(resume="approved")
                    else:
                        console.print("[red]❌ 已拒绝操作，中断流程。[/red]")
                        stream_input = Command(resume="rejected")

                    # 继续下一次 while 循环以推进恢复后的图任务
                    continue
                # 没有被中断，说明全部流程已经执行完毕，退出控制循环
                break


        except KeyboardInterrupt:
            console.print("\n[yellow]操作已中断。[/yellow]")
            break
        except Exception as e:
            import traceback
            console.print(f"[bold red]❌ 发生错误: {str(e)}[/bold red]")
            logger.critical(f"[dim red]{traceback.format_exc()}[/dim red]")
