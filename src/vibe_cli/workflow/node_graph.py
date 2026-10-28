import json
import os
import uuid
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langgraph.types import interrupt

from vibe_cli.core.node_state import AgentState
from vibe_cli.env.check_point_memory import postgres_memory
from vibe_cli.env.logger_config import logger
from vibe_cli.env.workspace import get_primary_work_dir
from vibe_cli.message.message_compactor import DEFAULT_KEEP_LAST_TURNS, DEFAULT_THRESHOLD_CHARS, plan_compaction, \
    summarize_with_model, is_summary_message
from vibe_cli.model.model_factory import get_model
from vibe_cli.prompt.sys_diff_prompt import diff_prompt
from vibe_cli.prompt.sys_input_confirm_prompt import input_message_detect
from vibe_cli.prompt.sys_safe_check_prompt import sys_safe_check_prompt
from vibe_cli.tools import ALL_TOOLS, DANGEROUS_TOOLS
from vibe_cli.wraps.monitor import monitor_node



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

@monitor_node("input_confirm_model")
def input_confirm_model(state: AgentState, config):
    """
    输入内容确认节点
    如果识别输入内容不完整，提示用户保
    :param state:
    :return:
    """

    configurable = (config or {}).get("configurable", {})
    model_provider = (configurable or {}).get("model_provider", {})

    messages = state.get("messages", [])
    last_message = messages[-1]

    msg = input_message_detect(last_message)
    response = get_model(provider=model_provider).invoke([SystemMessage(content=msg)])

    response_text = response.text.strip()

    # 清理可能存在的 markdown 代码块符号 (```json ... ```)
    if response_text.startswith("```json"):
        response_text = response_text[7:]
    if response_text.endswith("```"):
        response_text = response_text[:-3]

    try:
        result_dict = json.loads(response_text.strip())

        # 获取字典中的字段
        status = result_dict.get("status")
        summary = result_dict.get("summary")
        missing_info = result_dict.get("missing_info")
        questions_list = result_dict.get("questions", [])

        # 将列表转换为带序号的文本
        questions_str = "\n".join([f"- {q}" for q in questions_list])

        # 使用 f-string 重新组织成字符串
        reorganized_str = f"""
        识别到用户的输入信息不完整，需要重新总结返回给用户继续补充内容
        【校验状态】：{status} \n
        【意图摘要】：{summary} \n
        【缺失信息】：{missing_info} \n
        【引导追问】： \n
        {questions_str}
        """

    except Exception as e:
        logger.error(f"json load fail. result:{response_text}. error: {str(e)}")

        return {
            "requires_approval": False,
            "node_status": "normal"
        }

    return {
        "messages": [reorganized_str],
        "requires_approval": False,
        "node_status": "normal"
    }

@monitor_node("call_model")
def call_model(state: AgentState, config):
    """
    大模型推理节点
    :param state:
    :return:
    """

    configurable = (config or {}).get("configurable", {})
    model_provider = (configurable or {}).get("model_provider", {})

    messages = state.get("messages", [])
    response = get_model(provider=model_provider).bind_tools(ALL_TOOLS).invoke(messages)

    return {
        "messages": [response],
        "requires_approval": False,
        "node_status": "normal"
    }

# 假设定义了滚动摘要的书签前缀常量
SUMMARY_BOOKMARK_PREFIX = "<!-- [ROLLING_SUMMARY] -->\n"

@monitor_node("compact_msg_node")
def compact_msg_node(state, config):
    """消息历史压缩节点（图入口）。

    在每轮对话开始前运行。当累积的对话超出配置的阈值或达到最大条数上限时，
    将最旧的完整对话轮次归纳为滚动摘要，并通过 RemoveMessage 进行精准裁剪与原地重建，
    从而保持大模型上下文在安全范围内。如果归纳失败，则安全跳过。
    """
    messages = state.get("messages", [])
    if not messages:  # 冷启动无消息
        return {}

    # 从 state 或环境变量/config 中获取动态配置参数
    configurable = (config or {}).get("configurable", {})

    model_provider = (configurable or {}).get("model_provider", {})
    threshold = int(configurable.get("compress_threshold_chars", DEFAULT_THRESHOLD_CHARS))
    keep_last_turns = int(configurable.get("compress_keep_last_turns", DEFAULT_KEEP_LAST_TURNS))
    compress_keep_summaries = int(configurable.get("compress_keep_summaries", DEFAULT_KEEP_LAST_TURNS))

    # 获取由 plan_compaction 返回的起止闭区间索引 (start_idx, end_idx) 及历史消息
    plan = plan_compaction(
        messages,
        keep_last_turns=keep_last_turns,
        threshold_chars=threshold,
        compress_keep_summaries=compress_keep_summaries
    )
    if plan is None:  # 上下文体积依然在安全范围内，无需压缩
        return {}

    start_idx, end_idx, history = plan
    # 将闭区间的 end_idx 转换为切片用的 exclusive 边界
    cutoff = end_idx + 1

    # 1. 精准提取历史中已存在的既有滚动摘要（解耦投喂，绝不重复计算）
    existing_summary_msg = next((m for m in messages[:start_idx] if is_summary_message(m)), None)
    previous_summary = getattr(existing_summary_msg, 'content', '') if existing_summary_msg else ""
    if previous_summary.startswith(SUMMARY_BOOKMARK_PREFIX):
        previous_summary = previous_summary[len(SUMMARY_BOOKMARK_PREFIX):].strip()

    # 2. 调用模型生成新滚动摘要
    model = get_model(provider=model_provider)
    new_summary = summarize_with_model(model, previous_summary, history)
    if not new_summary:  # 摘要生成失败，本轮放弃裁剪以防破坏状态
        logger.warning("[Compact] 大模型摘要生成失败，安全跳过。")
        return {}

    # 3. 构造删除指令：清除裁剪 start_idx 开始到最后的数据。新数据及保留的数据重建
    removals = [RemoveMessage(id=m.id) for m in messages[start_idx:] if getattr(m, "id", None)]

    # 4. 构造新摘要消息
    summary_msg = HumanMessage(
        id="summary-" + uuid.uuid4().hex[:16],
        content=SUMMARY_BOOKMARK_PREFIX + new_summary,
        additional_kwargs={"is_summary": True, "compressible": False}
    )

    # 5. 克隆保留区消息并赋予新 ID，确保被 LangGraph 识别为全新追加的消息
    rebuilt_tail = [
        m.model_copy(update={"id": "keep-" + m.id})
        for m in messages[cutoff:]
    ]

    logger.info("[Compact] absorbed {} msgs (closed interval up to {}), summary len={}", (end_idx - start_idx + 1),
                end_idx, len(new_summary))
    return {"messages": removals + [summary_msg] + rebuilt_tail}


@monitor_node("safety_check_node")
def safety_check_node(state: AgentState,config):
    """
    安全检查与分流节点（核心拦截点）
    :param state:
    :return:
    """

    configurable = (config or {}).get("configurable", {})
    model_provider = (configurable or {}).get("model_provider", {})

    if configurable.get("security_opt_skip",False):
        return {"requires_approval": False, "node_status": "normal"}

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
            response = get_model(provider=model_provider).invoke([HumanMessage(content=prompt)])
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
        "messages": last_message.content,
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
def dynamic_shell_diff_node(state: AgentState, config) -> dict:
    """
        【纯 Python Git 变更收集与报告生成节点】
        不使用 LLM 总结，直接通过 Git 命令、本地解析行号（@@ 块）及绝对路径拼装报告。
        """
    messages = state.get("messages", [])
    if not messages:
        return {}

    configurable = (config or {}).get("configurable", {})
    model_provider = (configurable or {}).get("model_provider", {})
    work_dir = (configurable or {}).get("work_dir", {})

    last_message = messages[-1]
    cwd = str(get_primary_work_dir(work_dir or os.getenv("work_dir", "")))

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

        response = get_model(provider=model_provider).invoke(prompt)

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
    workflow.add_node("input_confirm", input_confirm_model)
    workflow.add_node("agent", call_model)
    workflow.add_node("safety_check", safety_check_node)
    workflow.add_node("pend_approval", pend_approval_interrupt_node)
    workflow.add_node("tools", tool_node)
    workflow.add_node("dynamic_diff_node", dynamic_shell_diff_node)

    workflow.set_entry_point("compact_msg")
    workflow.add_edge("compact_msg", "input_confirm")
    workflow.add_edge("input_confirm", "agent")

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

    # workflow.add_conditional_edges(
    #     "tools",
    #     route_check_mutation,
    #     {
    #         "run_diff_path": "dynamic_diff_node",
    #         "skip_diff_path": "agent"
    #     }
    # )

    workflow.add_edge("tools", "agent")

    return workflow.compile(
        checkpointer=checkpointer
    )
