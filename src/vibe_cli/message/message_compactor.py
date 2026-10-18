# Message compaction helpers for vibe-cli (Turn-based Rolling Summary).
#
# 滚动压缩核心流程:
#   1) 双门控判定: 轮数门控 + 字符阈值门控 (支持 max_compressive_messages 溢出强触发);
#   2) 安全裁剪: 严格按闭区间轮次对齐 ([start, end]), 增量或全量打包;
#   3) LLM 迭代归纳: 既有滚动摘要 + 新历史原文 -> 最新滚动摘要 (失败时安全跳过).

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from vibe_cli.env.logger_config import logger

# 触发压缩的默认字符阈值 (适配 1M Context Window)
DEFAULT_THRESHOLD_CHARS = 1200000
# 默认无条件保留的最近对话轮次
DEFAULT_KEEP_LAST_TURNS = 12

# 摘要模型使用的固定系统指令
SUMMARY_SYSTEM_INSTRUCTION = 'You are a meticulous conversation summarizer. Follow the user instruction exactly.'


def is_summary_message(msg) -> bool:
    """判断当前消息是否为历史生成的滚动摘要消息。"""
    additional_kwargs = getattr(msg, 'additional_kwargs', None) or {}
    if additional_kwargs.get('is_summary') is True:
        return True
    return False


def total_size(messages) -> int:
    """仅统计可压缩消息的字符总量（跳过系统提示、旧摘要等不可压缩项）。"""
    total = 0
    for msg in messages:
        if getattr(msg, 'type', None) == 'system' or isinstance(msg, SystemMessage):
            continue

        val = getattr(msg, 'text', None)
        if val is None or val == '':
            val = getattr(msg, 'content', '')

        total += len(str('' if val is None else val))
    return total


def split_into_turns(messages) -> list[tuple[int, int]]:
    """解析消息链为对话轮次闭区间列表 [(turn_start, turn_end), ...]（以非摘要的 HumanMessage 作为轮次起点，两端闭区间）。"""
    turns = []
    anchor_indices = [
        idx for idx, msg in enumerate(messages)
        if isinstance(msg, HumanMessage) and not is_summary_message(msg)
    ]

    for i, start in enumerate(anchor_indices):
        end = anchor_indices[i + 1] - 1 if i + 1 < len(anchor_indices) else len(messages) - 1
        if start <= end:
            turns.append((start, end))
    return turns


def should_compress(
        messages,
        keep_last=DEFAULT_KEEP_LAST_TURNS,
        threshold_chars=DEFAULT_THRESHOLD_CHARS,
) -> bool:
    """检查是否满足压缩门控条件：常规双门控（轮数 > keep_last 且 字符量 >= threshold_chars）或条数溢出强触发。"""
    if not messages or len(messages) <= keep_last:
        return False

    turn_count = len(split_into_turns(messages))
    chars = total_size(messages)
    normal_trigger = (turn_count > keep_last) and (chars >= threshold_chars)

    return normal_trigger


def safe_cutoff(
        messages,
        keep_last=DEFAULT_KEEP_LAST_TURNS,
) -> tuple[int, int] | None:
    """计算安全的裁剪闭区间 (start_idx, end_idx)。
    逻辑说明：
    1. 找出对话轮数并过滤掉保留的最后几轮；
    2. 统计现有历史摘要的次数（条数）；
    3. 若摘要次数 >= 阈值(keep_last)，触发全量压缩（包含旧摘要）；否则触发增量压缩（排除已压缩消息）。
    """
    if not messages:
        return None

    # 步骤 1：找出对话轮数并过滤掉保留的最后几轮
    turns = split_into_turns(messages)
    if not turns or len(turns) <= keep_last:
        return None

    candidate_turns = turns[:len(turns) - keep_last]
    if not candidate_turns:
        return None

    # 步骤 2：统计现有历史摘要的次数
    existing_summary_indices = [i for i, m in enumerate(messages) if is_summary_message(m)]
    summary_count = len(existing_summary_indices)
    last_summary_idx = existing_summary_indices[-1] if existing_summary_indices else -1

    # 步骤 3：根据摘要次数是否达到阈值（keep_last）判定全量或增量
    is_full_compression = summary_count >= keep_last

    if is_full_compression:
        # 全量压缩：将旧摘要伪装成单条轮次 + 所有候选轮次一次性全部纳入
        summary_turns = [(s, s) for s in existing_summary_indices]
        selected_turns = summary_turns + candidate_turns
    else:
        # 增量压缩：严格排除已经压缩过的消息，只取未压缩的候选轮次
        selected_turns = [
            (t_start, t_end) for t_start, t_end in candidate_turns
            if t_start > last_summary_idx
        ]

    if not selected_turns:
        return None

    start_idx = selected_turns[0][0]
    end_idx = selected_turns[-1][1]

    if end_idx < start_idx or end_idx >= len(messages):
        return None

    return start_idx, end_idx


def render_history(messages) -> str:
    """将消息序列化为大模型可读的纯文本流（跳过不可压缩项，多轮之间以空行分隔）。"""
    lines = []
    for msg in messages:
        if isinstance(msg, HumanMessage):
            role = 'USER'
        elif isinstance(msg, AIMessage):
            role = 'AI'
        elif isinstance(msg, SystemMessage):
            role = 'SYSTEM'
        elif isinstance(msg, ToolMessage):
            role = 'TOOL'
        else:
            continue

        content = getattr(msg, 'content', '')
        body = '[tool calls] {0}'.format(getattr(msg, 'tool_calls', [])) if (
                isinstance(msg, AIMessage) and not content) else str('' if content is None else content)
        lines.append('{0}: {1}'.format(role, body))
    return '\n\n'.join(lines)


def plan_compaction(
        messages,
        keep_last_turns=DEFAULT_KEEP_LAST_TURNS,
        threshold_chars=DEFAULT_THRESHOLD_CHARS,
) -> tuple[int, int, list] | None:
    """门控与裁剪规划器：若满足压缩条件则返回 (start_idx, cutoff, 待压缩历史列表)，否则返回 None。"""
    if not messages:
        return None
    if not should_compress(messages, keep_last_turns, threshold_chars):
        return None
    span = safe_cutoff(messages, keep_last_turns)
    if span is None:
        return None
    start_idx, cutoff = span
    history = [m for m in messages[start_idx:cutoff+1] if
               not (getattr(m, 'type', None) == 'system' or isinstance(m, SystemMessage))]
    if not history:
        return None
    return start_idx, cutoff, history


def summarize_with_model(model, previous_summary: str, history_messages: list) -> str:
    """将既有滚动摘要与新历史原文融合，交由大模型迭代生成最新摘要（异常时触发安全跳过，返回空串）。"""
    from vibe_cli.prompt.sys_summarize_prompt import sys_summarize_prompt
    try:
        prompt_text = sys_summarize_prompt(previous_summary, render_history(history_messages))
        response = model.invoke([
            HumanMessage(content=prompt_text),
        ])
        content = getattr(response, 'content', None)
        if content is None:
            return ''
        if isinstance(content, list):
            text = ' '.join(str(block) for block in content)
        else:
            text = str(content)
        return text.strip()
    except Exception as exc:
        logger.exception('[Compact] summarize model failed, safe-skip: {0}'.format(exc))
        return ''
