"""Message compaction helpers for vibe-cli."""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

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
    """Index such that the non-system part of messages[:cutoff] may be pruned."""
    non_system_idx = [i for i, m in enumerate(messages) if m.type != "system"]
    if len(non_system_idx) <= keep_last:  # nothing to prune
        return None
    pos = len(non_system_idx) - keep_last
    cutoff = non_system_idx[pos]
    while cutoff < len(messages) and messages[cutoff].type == "tool":  # keep tool chain coherent
        cutoff += 1
    return cutoff


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
        elif m.type == "tool":  # tool result
            role = "TOOL"
            body = str(m.content)
        else:  # human fallback
            role = "USER"
            body = str(m.content)
        lines.append("{0}: {1}".format(role, body))
    return "\n\n".join(lines)


def plan_compaction(messages, keep_last=DEFAULT_KEEP_LAST, threshold_chars=DEFAULT_THRESHOLD_CHARS):
    """Return (cutoff, history) if compression should run else None."""
    if not messages:  # nothing to do
        return None
    if not should_compress(messages, keep_last, threshold_chars):  # still small
        return None
    cutoff = safe_cutoff(messages, keep_last)
    if cutoff is None or cutoff <= 0:  # cannot prune anything useful
        return None
    history = [m for m in messages[:cutoff] if m.type != "system"]
    if not history:  # only system messages would be dropped
        return None
    return cutoff, history


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
    return text.strip()[:2000]
