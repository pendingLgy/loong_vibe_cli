"""Message compaction helpers for vibe-cli (Turn-based Compaction)."""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

DEFAULT_THRESHOLD_CHARS = 12000
DEFAULT_KEEP_LAST_TURNS = 3  # 默认保留最近 3 轮完整对话


def is_summary_message(msg) -> bool:
    """判断一条消息是否为压缩生成的历史摘要消息"""
    # 1. 优先校验 additional_kwargs 中的标记
    additional_kwargs = getattr(msg, "additional_kwargs", {}) or {}
    if additional_kwargs.get("is_summary") is True:
        return True

    # 2. 保留对 id 或 content 前缀的降级防御
    msg_id = str(getattr(msg, "id", ""))
    if msg_id.startswith("summary-"):
        return True

    return False


def total_size(messages):
    """Rough char-based size of the non-system conversation."""
    return sum(len(str(m.content)) for m in messages)


def split_into_turns(messages):
    """将消息列表解析为轮次区间 [(turn_start_idx, turn_end_idx), ...]。

    规则：
    1. 遇到第一条真正的 HumanMessage（非摘要）才启动轮次计算。
    2. 一轮 Turn 以 HumanMessage 为起点，之后跟随的所有消息（AI、Tool、中途插入的 SystemMessage）
       全部归入该轮次。
    3. 历史摘要消息 (is_summary=True) 被当作背景信息，不会触发新 Turn 计算。
    """
    turns = []
    current_turn_start = None

    for idx, m in enumerate(messages):

        # 遇真正的 HumanMessage（且非历史摘要消息），开启/切分轮次
        if isinstance(m, HumanMessage):
            if current_turn_start is not None:
                turns.append((current_turn_start, idx))
            current_turn_start = idx
            continue

        # 在遇到第一条真正的 HumanMessage 之前，忽略前置消息（如全局 SystemMessage 或旧摘要）
        if current_turn_start is None:
            continue

        # 一旦轮次启动，后续的所有非 Human 消息（包括中途插入的 SystemMessage）全部归入当前轮次

    # 收尾最后一轮
    if current_turn_start is not None and current_turn_start < len(messages):
        turns.append((current_turn_start, len(messages)))

    return turns


def should_compress(messages, keep_last_turns=DEFAULT_KEEP_LAST_TURNS, threshold_chars=DEFAULT_THRESHOLD_CHARS):
    """Return True when history is big enough and has enough turns to prune."""
    turns = split_into_turns(messages)
    if len(turns) <= keep_last_turns:  # 轮次不足以压缩
        return False
    if total_size(messages) <= threshold_chars:  # 字符量尚未超标
        return False
    return True


def safe_cutoff(messages, keep_last_turns=DEFAULT_KEEP_LAST_TURNS):
    """按轮次计算安全截断的起止绝对索引。

    返回 (start_idx, cutoff)：
    - start_idx: 待压缩历史的起始位置（第一轮 HumanMessage 的索引）。
    - cutoff: 待压缩历史的结束位置（也是保留区第 -keep_last_turns 轮起始 HumanMessage 的索引）。
    """
    turns = split_into_turns(messages)

    # 轮次不足保留阈值或无 HumanMessage 锚点，拒绝压缩
    if len(turns) <= keep_last_turns:
        return None

    # 计算压缩切点：压缩除最新 keep_last_turns 轮之外的所有早期轮次
    start_idx = turns[0][0]  # 第一轮的开始索引
    cutoff_turn_idx = len(turns) - keep_last_turns
    cutoff = turns[cutoff_turn_idx][0]  # 最新 N 轮中第一轮的开始索引

    # 边界有效性检查
    if start_idx >= cutoff or cutoff >= len(messages):
        return None

    return start_idx, cutoff


def is_compressible(message) -> bool:
    """检查消息是否允许被压缩（默认允许）"""
    # 获取 additional_kwargs 中的 compressible 字段，默认为 True
    return message.additional_kwargs.get("compressible", True)


def render_history(messages):
    """Render messages into plain text for the summarizer LLM."""
    lines = []
    for m in messages:

        if not is_compressible(m):
            continue

        if isinstance(m, AIMessage):
            role = "AI"
            # body = str(m.content) if m.content else "[tool calls] {0}".format(getattr(m, "tool_calls", []))
            body = str(m.text)
        elif isinstance(m, SystemMessage):
            role = "SYSTEM"
            body = str(m.text)
        # elif isinstance(m, ToolMessage):
        # role = "TOOL"
        # body = str(m.content)
        # body = str("")
        elif isinstance(m, HumanMessage):
            role = "USER"
            body = str(m.text)
        else:
            continue

        lines.append("{0}: {1}".format(role, body))

    return "\n\n".join(lines)


def plan_compaction(messages, keep_last_turns=DEFAULT_KEEP_LAST_TURNS, threshold_chars=DEFAULT_THRESHOLD_CHARS):
    """若满足按轮次压缩条件，返回 (start_idx, cutoff, history)，否则返回 None。"""
    if not messages:
        return None
    if not should_compress(messages, keep_last_turns, threshold_chars):
        return None

    span = safe_cutoff(messages, keep_last_turns)
    if span is None:
        return None
    start_idx, cutoff = span

    # 提取待压缩范围内的消息切片
    history = [m for m in messages[start_idx:cutoff] if getattr(m, "type", None) != "system"]
    if not history:
        return None

    return start_idx, cutoff, history


def summarize_with_model(model, previous_summary, history_messages):
    """Fold previous summary plus absorbed turns into one new summary string."""
    from vibe_cli.prompt.sys_summarize_prompt import sys_summarize_prompt
    prompt_text = sys_summarize_prompt(previous_summary, render_history(history_messages))
    try:
        resp = model.invoke(
            [
                SystemMessage(
                    content="You are a meticulous conversation summarizer. Follow the user instruction exactly."),
                HumanMessage(content=prompt_text)
            ]
        )
    except Exception:
        return ""
    text = resp.text or ""

    return text.strip()
