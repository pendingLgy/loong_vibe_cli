# Task Plan: Message History Compression (Compact Node)
- **Objective**: Add automatic long-conversation compression to vibe-cli so arbitrary session length stays cheap and fast: send system prompts only on first round, summarize absorbed early turns via LLM, prune old messages with RemoveMessage while keeping tool-call pairs intact, and keep global memory via a summary state key.
- **Status**: Completed

## Steps
- [ ] Step 1: Add summary field to AgentState in model\deepseek_model.py
- [ ] Step 2: Add summarize prompt template in prompt module
- [ ] Step 3: Add compactor logic module in workflow (decision + safe cutoff + LLM summarize)
- [ ] Step 4: Wire compact_node into base_workflow graph (entry node then agent), inject summary in call_model
- [ ] Step 5: Fix start() to send system prompts only on first round
- [ ] Step 6: Validate via import check + dry-run unit test of cutoff function


---

## 消息压缩具体流程 (Detailed Flow)

### 1. 触发条件（compact_node 入口判定）
仅当同时满足以下两个条件才执行压缩，否则节点空转放行：
- 非 System 消息数量大于 keep_last（默认 12 条）
- 非 System 消息内容总字符数大于 compress_threshold_chars（默认 12000）

### 2. 执行流程
```text
用户输入
   |
   v
compact_node（图入口，每轮最先执行）
   |-- 未超阈值 -- 直接放行 --> agent
   |-- 超阈值：
        1) plan_compaction() 计算安全切点 cutoff
           保留最近 keep_last 条非 System 消息；
           若切点落在 ToolMessage 上则向后顺延，保证 tool_call 配对完整
        2) 取 messages[:cutoff] 中非 System 消息作为 history 交给 LLM
        3) summarize_with_model() 调用 LLM：输入 = 旧 summary + history 渲染文本 -> 输出新 summary
        4) 构造 RemoveMessage 列表，删除被吸收区间中非 System 且带 id 的消息
        5) 更新 state = { messages: removals, summary: new_summary }
   |
   v
agent (call_model)
   |-- 若 summary 非空 -> 在消息最前方注入 SystemMessage（历史摘要） + summary
   |-- bind_tools(ALL_TOOLS).invoke(messages)
```

### 3. 摘要融合规则
- 旧 summary 与本次被吸收的对话一起提交给 LLM，要求融合去重、保留关键信息、输出不超过 500 字（存储截断 2000 字符兜底）。
- 摘要失败或返回空 -> 跳过本轮删除（安全 no-op），不阻塞对话。

### 4. 删除规则（RemoveMessage）
- 仅删除 messages[:cutoff] 区间内 type != system 且带消息 id 的消息。
- SystemMessage（系统提示词）永不删除。
- id 由 graph / checkpointer 持久化时自动分配；直接调用节点（不经 graph）时消息无 id 则不删除，因此压缩必须在完整图中运行。

### 5. 顺带修复：系统提示词只首轮注入
- 原缺陷：start() 每轮都将 system_prompts + 新提问 作为输入，add_messages 对无 id 消息执行追加 -> 每轮重复累积多份 SystemMessage。
- 修复后：first_round 标志控制，仅首轮注入 system_prompts，后续轮次只传新提问，根治累积问题。

### 6. 可配置项（.env 可选）
| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| compress_threshold_chars | 12000 | 触发压缩的字符阈值 |
| compress_keep_last | 12 | 保留的最近消息条数 |

### 7. 验证结果
- 图结构：__start__ -> compact -> agent -> (safety_check / end) ...
- 单元测试：tool 链切点完整、System 消息不删、保留条数符合 keep_last。
- 端到端（mock 模型 + 免 DB）：31 条消息 -> 压缩后保留 9 条，summary 正确生成并注入 call_model。
