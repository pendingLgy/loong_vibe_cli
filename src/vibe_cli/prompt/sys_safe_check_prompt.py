def sys_safe_check_prompt(tool_calls_info) -> str:
    prompt = f"""
    请分析以下 AI 即将调用的工具和参数，判断它是否属于以下需要人工审批的敏感或高风险操作：
    1. **高危破坏性动作**：删除数据、修改重要系统配置、`DROP`、`DELETE`、`rm`、`del` 等。
    2. **耗时/资源密集型操作**：可能导致系统长时间阻塞、高内存/高CPU消耗、大规模数据全表扫描、大文件传输或长时间批处理的耗时任务。
    3. **如果是删除agent临时生成的文件，默认非高危操作。
    
    工具调用信息：{tool_calls_info}
    
    请严格按照以下 JSON 格式回复，不要包含其他多余的 markdown 标记或文本：
    {{"is_dangerous": true或false, "reason": "判断的具体原因（如果判定为高危或耗时操作，请在原因中详细说明）","file":"操作的文件名(需要绝对路径)"}}
    """

    return prompt
