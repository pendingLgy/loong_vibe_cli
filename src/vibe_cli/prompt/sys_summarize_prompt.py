"""Prompt template for summarizing old conversation turns during message compaction."""


def sys_summarize_prompt(previous_summary, history_text):
    """Build a prompt that asks the LLM to compress older turns into a rolling summary."""
    prev_block = previous_summary.strip() or "(none)"
    return (
        "You are compressing conversation history for an AI coding assistant.\n"
        "The dialog below contains older turns to be absorbed. Write a concise \n"
        "Chinese summary that preserves: user requests and preferences, commands \n"
        "executed and their results, approved or rejected items, and unfinished tasks.\n\n"
        "Rules: output only the summary body text; merge with the old summary below \n"
        "if it is non-empty; keep it under 500 characters.\n\n"
        "==== OLD SUMMARY ====\n"
        + prev_block
        + "\n\n==== DIALOG TO COMPRESS ====\n"
        + history_text
        + "\n"
    )
