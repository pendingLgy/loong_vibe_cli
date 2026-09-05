# 消息压缩（Message Compaction）设计文档

- [x] Step 1: AgentState 不设独立 summary 字段（摘要书签物理常驻 messages）
- [x] Step 2: prompt 模块新增 sys_summarize_prompt 摘要提示词
- [x] Step 3: message 包 message_compactor.py 纯函数层 total_size、should_compress、safe_cutoff、render_history、plan_compaction、summarize_with_model
- [x] Step 4: base_workflow.py 接入 compact_node（图入口 compact 到 agent；dynamic_diff_node 回归 compact）；compact_node 物理重建 messages（删后重加），call_model 直接用通道
- [x] Step 5: start() 系统提示词仅首轮注入（first_round 标志；resume 时探测持久化历史已有 system 则跳过注入）
- [x] Step 6: 用户更新压缩代码流程 —— safe_cutoff 返回 (start_idx, cutoff)（start_idx 对齐首条 HumanMessage）；摘要书签由 SystemMessage 改为 HumanMessage：Human 书签天然成为下一轮 first human，被纳入待吸收区间自动滚动融合，替代旧方案中前置区 system 书签加额外清除逻辑
- [x] Step 7: 修正 compact_node 删除范围 —— 已落地：removals 覆盖 messages[start_idx:] 全部带 id 消息（含保留区旧 id）；messages[:start_idx] 前置区不动；summary_msg 与 keep-* 克隆按 updates 顺序追加，通道顺序 = 前置提示词 + 摘要书签 + 保留对话，无重复
- [x] Step 8: 同步 26 个测试用例断言（human 书签、删除范围、safe_cutoff 三元组语义）—— pytest 26 passed 全绿；message_compactor.py 覆盖率 99%（不低于 90% 达标）

## 1. 触发条件（compact_node 入口判定）

仅当同时满足以下两个条件才执行压缩，否则节点空转放行：
- 非 System 对话消息数量大于 keep_last（默认 12 条）
- 非 System 对话内容总字符数大于 compress_threshold_chars（默认 12000）

> 统计口径为非 System 消息。HumanMessage 摘要书签计入统计（type=human）；它常驻且内容短（由提示词约束精炼输出，代码已不做 2000 字符硬截断），不会造成已压缩内容反复触发。原 system 书签口径已废弃：system 不进 total_size 与 keep_last 计数，也无法被 start_idx 吸收删除。

## 2. 裁剪与执行流程（当前代码）

```text
用户输入
   
   v
compact_node（图入口，每轮最先执行）
   -- 未超阈值 -- 直接放行 -- agent
   -- 超阈值：
        1) plan_compaction() 返回 (start_idx, cutoff, history)
           - start_idx = 第一条 HumanMessage 的绝对索引
             （首轮 = 对话首问；后续轮 = 旧摘要书签本身，因为书签是 HumanMessage 且位于对话头部，
               旧书签被纳入待吸收区间，形成滚动摘要，无需额外清除逻辑）
           - cutoff = 倒数第 keep_last 条非 System 消息所在位置
           - 工具链防呆：cutoff 若命中 ToolMessage 或带 tool_calls 的 AIMessage，则向前回退到安全断点，
             保证 AI tool_calls 与其 Tool 结果不跨区断裂
           - 起点终点有效性校验：start_idx 小于 cutoff 且 cutoff 小于 len(messages) 才可压缩
        2) history = messages[start_idx:cutoff]（含区间内可能夹杂的 System）

        3) summarize_with_model() 调用 LLM：
           - old_summary = 从 history 中提取前缀匹配【历史摘要】的旧书签正文并拼接（含区间内 System 渲染行）
           - 与 history 一并提交，折叠滚动摘要 new_summary；失败或空则安全 no-op
        4) removals = 对 messages[start_idx:] 全部带 id 消息生成 RemoveMessage（头部前置区不动）
        5) summary_msg = HumanMessage(id 为 summary-*，正文为【历史摘要】前缀 + new_summary)
        6) rebuilt_tail = 保留区 messages[cutoff:] 逐条克隆换 keep-* 新 id（原内容原类型）；无 id 原样保留
        7) 返回 updates = removals + [summary_msg] + rebuilt_tail
           - 目标通道顺序 = 前置系统提示词组 到 摘要书签 到 保留对话
   -- 通道 messages 已包含摘要书签（compact_node 物理常驻），直接 bind_tools 发送
   v
agent (call_model)
   -- bind_tools(ALL_TOOLS).invoke(messages)
```

## 3. 摘要融合规则

- messages 内旧摘要书签正文（HumanMessage，前缀匹配【历史摘要】）与本次待吸收的对话（含区间内 System 渲染出的 SYSTEM 行）一起提交给 LLM，要求融合去重、保留关键信息、输出精炼（sys_summarize_prompt 约束约 500 字以内；代码已不做 2000 字符硬截断）。
- render_history 已按 AI、USER、TOOL、SYSTEM 四种角色渲染；区间内 System 消息同样以 SYSTEM 行进入摘要输入，避免信息静默丢失。
- 摘要失败或返回空则跳过本轮删除（安全 no-op），不阻塞对话。

## 4. 删除规则（RemoveMessage，当前代码已落地）

- removals 覆盖 messages[start_idx:] 全部带 id 消息（含保留区旧 id，避免 LangGraph add_messages 将旧保留区消息留在原位导致与 keep-* 克隆重复），messages[:start_idx] 前置区不动；随后按 updates 顺序追加 summary_msg 与 keep 克隆，得到 前置提示词组 + 摘要书签 + 保留对话。
- 换新 id 原因（保留区消息克隆换 keep-* id）：同批 remove 后若保留区消息仍用原 id，add_messages 会将其保留在原位，摘要书签只能追加到列表末尾；换新 id 后按 updates 顺序 append，确保摘要位于保留对话之前。
- 图内消息均带 id（checkpointer 持久化分配）；直接调用节点（不经 graph 的 add_messages）时消息若无 id 则不删除、也不换 id，因此压缩应在完整图中运行。

## 5. 顺带修复：系统提示词只首轮注入

- 原缺陷：start() 每轮都将 system_prompts + 新提问 作为输入，add_messages 对无 id 消息执行追加，导致每轮重复累积多份 SystemMessage。
- 修复后：first_round 标志控制，仅首轮注入 system_prompts，后续轮次只传新提问，根治累积问题；线程 resume 时若历史已含 system 消息则不再注入。

## 6. 可配置项（.env 可选）

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| compress_threshold_chars | 12000 | 触发压缩的字符阈值（按非 System 对话消息统计） |
| compress_keep_last | 12 | 触发压缩的消息条数阈值（按非 System 对话消息统计） |

## 7. 验证状态（当前）

- 图结构：__start__ 到 compact 到 agent 到 后续节点 到 dynamic_diff_node 再回到 compact（每轮入口先压缩）
- pytest tests 目录 message 用例当前：26 passed、0 failed；覆盖 message_compactor.py 99%（uv run pytest tests.message --cov=vibe_cli.message.message_compactor --cov-report=term）
- 端到端目标口径（mock 模型 + 免 DB）已由节点级 T15、T17、T18 与图级 T19-T22 验证：超阈值长对话压缩后通道 = 前置提示词 + HumanMessage 摘要书签（summary-*，含【历史摘要】与 new_summary）+ 最近保留对话（keep-* 克隆），call_model 直接使用通道消息

