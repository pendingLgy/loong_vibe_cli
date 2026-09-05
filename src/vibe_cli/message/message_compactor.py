"""Message compaction helpers for vibe-cli."""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

DEFAULT_THRESHOLD_CHARS = 12000
DEFAULT_KEEP_LAST = 12


def total_size(messages):
    """Rough char-based size of the non-system conversation."""
    return sum(len(str(m.content)) for m in messages if m.type != "system")


def should_compress(messages, keep_last=DEFAULT_KEEP_LAST, threshold_chars=DEFAULT_THRESHOLD_CHARS):
    """Return True when history is big enough and has something to prune."""
    non_system = [m for m in messages if m.type != "system"]
    if len(non_system) <= keep_last:  # too few messages to bother
        return False
    if total_size(non_system) <= threshold_chars:  # still cheap
        return False
    return True


def safe_cutoff(messages, keep_last=DEFAULT_KEEP_LAST):
    """计算安全截断的起止绝对索引。

    返回 (start_idx, cutoff)：
    - start_idx: 待压缩历史的起始位置（精准对齐到第一条非系统消息，并防呆处理左侧断裂工具链）。
    - cutoff: 待压缩历史的结束位置（也是保留区 messages[cutoff:] 的起始位置）。
    如果无需压缩或无法裁剪，则返回 None。
    """
    # 1. 过滤前置系统消息，提取第一条非系统消息的索引作为初始 start_idx
    non_system_indices = [i for i, m in enumerate(messages) if getattr(m, "type", None) != "system"]
    if len(non_system_indices) <= keep_last:  # 非系统消息数量小于等于保留阈值，无需裁剪
        return None

    # Refuse compaction when no HumanMessage anchor exists (AI-only / tool-only history)
    if not any(getattr(m, 'type', None) == 'human' for m in messages):
        return None

    start_idx = non_system_indices[0]

    # --- 左边界 (start_idx) 工具链完整性修复 ---
    # 如果起始位置落在孤立/残缺的 ToolMessage 上，向右推移以跳过不可用的工具响应
    while start_idx < len(messages):
        current_msg = messages[start_idx]
        msg_type = getattr(current_msg, "type", None)

        if msg_type == "tool":
            start_idx += 1
            continue
        break

    # 目标截断位置（大致保留最近 keep_last 条非系统消息）
    target_pos = len(non_system_indices) - keep_last
    cutoff = non_system_indices[target_pos]

    # --- 右边界 (cutoff) 工具链完整性校验 ---
    while cutoff > 0 and start_idx < cutoff:
        current_msg = messages[cutoff]
        msg_type = getattr(current_msg, "type", None)
        tool_calls = getattr(current_msg, "tool_calls", None)

        # 情况 A：当前节点是发起工具调用的 AIMessage
        if tool_calls and isinstance(tool_calls, list) and len(tool_calls) > 0:
            expected_count = len(tool_calls)
            subsequent_msgs = messages[cutoff + 1: cutoff + 1 + expected_count]

            # 校验后续 N 个节点是否全部存在且均为 ToolMessage
            is_complete_chain = (
                    len(subsequent_msgs) == expected_count
                    and all(getattr(m, "type", None) == "tool" for m in subsequent_msgs)
            )

            if is_complete_chain:
                # 完整工具链：将 cutoff 推进到该工具链全部节点之后 (+ 1 + expected_count)
                # 确保切片 messages[start_idx:cutoff] 完整包含 AIMessage 及所有配对 ToolMessage
                cutoff = cutoff + 1 + expected_count
                break
            else:
                # 不完整工具链：向前回退 1 位寻找安全切点
                cutoff -= 1
                continue

        # 情况 B：当前节点是 ToolMessage（处于工具链尾部，向前寻找发起点 AIMessage）
        if msg_type == "tool":
            cutoff -= 1
            continue

        # 情况 C：普通消息（如 HumanMessage 或无 tool_calls 的常规 AIMessage），直接锁定
        break

    # 边界有效性最终确认
    if start_idx >= cutoff or cutoff >= len(messages):
        return None

    return start_idx, cutoff


def render_history(messages):
    """Render messages into plain text for the summarizer LLM."""
    lines = []
    for m in messages:  # walk every message
        if isinstance(m, AIMessage):  # model turn
            role = "AI"
            body = str(m.content) if m.content else "[tool calls] {0}".format(m.tool_calls)
        elif m.type == "system":  # system turn
            role = "SYSTEM"
            body = str(m.content)
        elif isinstance(m, ToolMessage):  # tool result
            role = "TOOL"
            body = str(m.content)
        else:  # human fallback
            role = "USER"
            body = str(m.content)
        lines.append("{0}: {1}".format(role, body))
    return "\n\n".join(lines)


def plan_compaction(messages, keep_last=DEFAULT_KEEP_LAST, threshold_chars=DEFAULT_THRESHOLD_CHARS):
    """若满足压缩条件，返回 (start_idx, cutoff, history)，否则返回 None。"""
    if not messages:  # 无消息则跳过
        return None
    if not should_compress(messages, keep_last, threshold_chars):  # 文本量未达阈值
        return None

    # 直接获取由 safe_cutoff 规划好的起止绝对索引
    span = safe_cutoff(messages, keep_last)
    if span is None:
        return None
    start_idx, cutoff = span

    # 提取切片范围内的对话历史（自动过滤掉区间内可能夹杂的系统消息）
    history = [m for m in messages[start_idx:cutoff]]
    if not history:
        return None

    return start_idx, cutoff, history


def summarize_with_model(model, previous_summary, history_messages):
    """Fold previous summary plus absorbed turns into one new summary string.

    Returns an empty string on any failure so callers can skip compaction safely.
    """
    from vibe_cli.prompt.sys_summarize_prompt import sys_summarize_prompt
    prompt_text = sys_summarize_prompt(previous_summary, render_history(history_messages))
    try:  # never let a summarizer failure break the conversation
        resp = model.invoke(
            [SystemMessage(
                content="You are a meticulous conversation summarizer. Follow the user instruction exactly."),
             HumanMessage(content=prompt_text)]
        )
    except Exception:  # best-effort compaction
        return ""
    text = resp.content or ""
    if not isinstance(text, str):  # content blocks list
        text = " ".join(str(part) for part in text)
    return text.strip()
