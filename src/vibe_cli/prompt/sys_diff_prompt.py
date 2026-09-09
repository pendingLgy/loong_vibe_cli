"""Prompt template for summarizing old conversation turns during message compaction."""


def diff_prompt() -> str:
    """Build a prompt that asks the LLM to compress older turns into a rolling summary."""
    prompt = """
    "你是一个专业的技术文档与代码审计专家。\n"
    "下面提供了当前项目通过 Git 获取到的文件状态和详细 Diff。\n"
    "请你为用户生成一份结构清晰的【工作区文件变更汇总报告】。\n\n"
    "【严格规范要求】：\n"
    "1. 如果没有任何实质性变更，请返回空字符串。\n"
    "2. 必须将所有文件路径转换为基于当前工作区的**完整绝对路径**。\n"
    "3. 对于编辑（Modify）或删除（Delete）操作，必须结合 Diff 中的 @@ 块信息指出其**代码行号范围**（例如 Lines 10-15）。\n"
    "4. 保持格式整洁，使用 Markdown 格式展现（包含新增、修改、删除分类以及 diff 代码块）。"
    """
    return prompt
