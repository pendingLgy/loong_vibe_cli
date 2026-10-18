"""Prompt template for summarizing old conversation turns during message compaction."""


def sys_summarize_prompt(previous_summary, history_text):
    """Build a prompt that asks the LLM to compress older turns into a rolling summary."""
    prev_block = previous_summary.strip() or "(none)"

    return f"""
    # Role
    你是一个资深的大模型长期记忆管理与上下文压缩专家。你的任务是维护和更新一份**连续的滚动摘要（Rolling Summary）**。

    ## Core Objective
    请将【既有的历史滚动摘要】与【新发生的对话历史原文（包含人类指令、大模型思考及 Tool Call 工具调用记录）】进行深度融合与提炼，生成一份**更新后的、结构化的最新滚动摘要**。

    ---

    ## Rules & Constraints (核心提取原则)

    1. **核心信息与技术栈保留优先**：
       - 必须严格保留：用户的核心身份、长远目标、技术栈选型（如使用的框架、Python工具、数据库、特定版本）、架构设计方案、已解决的关键问题以及未完成的待办事项。

    2. **工具调用（Tool Calls）与结构化数据提炼（重点）**：
       - 历史中包含 AI 与系统交互的工具调用记录（如 `tool_use` / `tool_output`、命令行执行、OCR 识别、网页抓取等）。
       - **提取原则**：请提取工具调用的**核心意图、关键中间发现与最终结论**（例如：调用了什么工具、锁定了什么配置、获取了什么关键数据结构或报错原因）。
       - **严禁**大段照抄原始的 JSON 参数、巨型文本日志、长串 OCR 原始坐标或重复的调试输出，只保留对后续开发有价值的**结构化事实**。

    3. **新旧融合与迭代**：
       - 如果新对话或工具调用结果对旧内容进行了修正、补充或深入（例如方案调整、代码重构、Bug 修复），请以**最新结论**为准进行更新，覆盖或修订旧摘要中的陈旧信息。

    4. **去粗取精（剔除噪音）**：
       - 必须丢弃：无意义的客套话、临时性错误尝试、已被修正的废弃思路、琐碎的对话交互细节。

    5. **时序与连贯性**：
       - 摘要应当具有良好的可读性和逻辑连贯性，采用客观、精炼的第三方视角或结构化语言（如技术文档风格）进行描述。

    ---

    ## Input Data (输入内容)

    ### 1. 既有历史滚动摘要 (Previous Summary)
    {prev_block}

    ### 2. 待压缩的新对话历史 (New History Messages with Tool Calls)
    {history_text}

    ---

    ## Output Requirement (输出规范)
    - 请直接输出更新后的完整滚动摘要正文。
    - **不要**包含任何诸如“好的，这是更新后的摘要：”之类的客套寒暄或前缀说明。
    - 保证信息密度高、条理清晰（可适当使用小标题、列表或专业术语）。
    """
