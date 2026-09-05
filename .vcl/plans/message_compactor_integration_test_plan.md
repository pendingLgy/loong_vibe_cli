# message_compactor 集成测试计划（已落地）

> 生成日期：2025 年 · 工作区：vibe-cli
> 状态：已同步用户最新压缩代码流程（HumanMessage 滚动摘要书签 + removals 覆盖 start_idx: 全量重建），当前 26 用例 26 passed / 0 failed —— 修订说明见文末执行记录 3
> 目标：为 vibe_cli.message.message_compactor 建立纯函数、节点级、图级端到端三层集成测试体系。

## 1. 被测对象与集成链路

核心文件：message_compactor.py（src 下 vibe_cli.message 包，159 行，纯逻辑 + LLM 调用）
| 函数 | 职责 | 集成方式 |
| --- | --- | --- |
| total_size(messages) | 非 System 消息粗略字符总量 | 被 should_compress 调用 |
| should_compress(...) | 是否达到压缩条件（条数大于 keep_last 且字符大于 threshold） | 被 plan_compaction 调用 |
| safe_cutoff(...) | 计算安全裁剪区间 (start_idx, cutoff)，对齐首个 HumanMessage、防断 ToolMessage 链 | 被 plan_compaction 调用 |
| render_history(...) | 历史消息渲染为 AI、USER、TOOL、SYSTEM 纯文本 | 被 summarize_with_model 调用 |
| plan_compaction(...) | 入口规划：满足条件返回 (start_idx, cutoff, history)，否则返回 None | 被 base_workflow.compact_node 调用 |
| summarize_with_model(model, ...) | 折叠旧摘要与历史为新摘要（失败返回空串，不抛异常；不做 2000 字符硬截断） | 被 compact_node 调用 |

关键外部依赖：
- langchain_core.messages 消息类型与 RemoveMessage
- sys_summarize_prompt（位于 vibe_cli.prompt.sys_summarize_prompt）
- 模型工厂 get_model_by_name（位于 vibe_cli.model.deepseek_model）
- LangGraph 节点监控装饰器 @monitor_node

真实图链路（base_workflow.py）：

```text
__start__ → compact(入口) → agent(call_model，直接使用通道消息；摘要书签已由 compact 物理写入 messages)
         → ... → dynamic_diff_node → compact（循环回来，每轮先压缩）
```

图中 compact_node 读取 env：compress_threshold_chars（默认 12000）与 compress_keep_last（默认 12），超阈值时整段重建：删除 messages[start_idx:] 全部带 id 消息并重新追加 HumanMessage 摘要书签（summary-*）与保留区（keep-* 新 id），不再维护独立 summary 状态。

## 2. 测试范围界定

要测（单元 + 集成）
- 6 个纯函数与半纯函数的边界和组合
- compact_node 节点级集成（直接调用节点函数，验证 state 变更）
- 图级端到端（mock 模型，免真实 LLM、免 PostgreSQL）

不测（或仅做冒烟）
- 真实大模型生成质量（摘要质量、500 字约束）→ 属模型评测，非集成测试
- PostgreSQL Checkpointer 持久化细节 → 用带显式 id 的消息模拟
- 工具本身（shell_tool 等）与压缩无关的执行正确性

## 3. 前置条件与测试基建（落地形态）

1. 运行环境：pytest 与 pytest-cov 已固化于 pyproject.toml [dependency-groups] dev，直接 uv run pytest tests.message -v
2. 测试目录：与源码包镜像，位于 tests 的 message 子目录（源码在 src 的 vibe_cli.message 包）：

```text
tests.message
├─ conftest.py                 # 共享 fixture 与工厂
├─ test_message_compactor.py   # 纯函数测试（T01-T12、T06b、T06c、T06d、T09b，共 16 例）
└─ test_compact_integration.py # compact_node + 图级端到端（T13-T22，共 10 例）
```

3. FakeModel（conftest）：实现 invoke(messages) 并记录每次调用，返回可控摘要文本或抛异常；支持 bind_tools，覆盖成功与失败分支。
4. 注入方式：fixture fake_model_factory 对 base_workflow.get_model_by_name 做 monkeypatch，使 compact_node 与 call_model 共用同一 FakeModel 实例，可断言模型调用次数与入参。
5. 消息工厂 make_msg：统一构造带显式 id 的消息（模拟 checkpointer 持久化后的形态），否则 RemoveMessage 无 id 可删；dialog_rounds 批量生成 Human+AI 轮次。
6. 图级测试：full_state 补全 AgentState 全部 channel，并构建最小图 compact → agent → END，复用真实节点，免真实 LLM 与 PostgreSQL。

## 4. 测试用例设计（当前 26 用例，全部 PASSED）

### A. 纯函数层（test_message_compactor.py，16 例）

- T01 total_size 忽略 System：System 大文本 + Human 与 Tool → 只计非 System 字符 → PASSED
- T02 should_compress 条数边界：非 System 数量恰等于 keep_last=4 → False；6 条（3 轮）超阈值 → True → PASSED
- T03 should_compress 字符阈值：3 轮但内容不足 threshold → False → PASSED
- T04 safe_cutoff 常规切点：前置 3 System + 15 轮 Human、AI，keep_last=12 → start=3、cutoff 落在 HumanMessage、保留非 System 12 条 → PASSED
- T05 safe_cutoff 对齐首 Human：前置 3 System + 2 Human → start_idx 等于第一条 Human 索引（System 永不进裁剪区） → PASSED
- T06 safe_cutoff 工具链防呆：cutoff 命中孤立 ToolMessage → 回退返回 (0,8)、cutoff 落在 HumanMessage（旧断言 (0,6) 已按新语义校正） → PASSED
- T06b 工具链完整推进：cutoff 命中带 2 个 tool_calls 的 AI 且后随 2 条 Tool → 推进至工具链之后 (0,11)、落在 Human → PASSED
- T06c 工具链不完整回退：AI 声明 2 个 tool_calls 但仅 1 条 Tool 结果 → 回退 (0,7)、落在 AI（配对工具缺失） → PASSED
- T06d cutoff 命中 ToolMessage 回退：保留区起点落在孤立 ToolMessage → 回退 (0,5)、落在配对 AI 之前 → PASSED
- T07 safe_cutoff 边界无效：无 Human / 全 System / 条数不足 / Tool 先于 Human → None → PASSED
- T08 render_history 各类型渲染：AI 空 content 带 tool_calls、Tool、System、Human → AI 渲染为 AI: [tool calls] 前缀、角色标签正确 → PASSED
- T09 plan_compaction 三态：空列表、未达阈值、正常 15 轮 → 前两者 None；正常返回 (start_idx, cutoff, history) 且与切片一致、剔除 System → PASSED
- T09b plan_compaction 仅 AI：15 条 AI 无 Human 锚点 → None → PASSED
- T10 summarize_with_model 正常且不截断：FakeModel 返回 2500 字符超长摘要 → 原样返回（len==2500，证明代码已移除 2000 硬截断）；spy 校验 old_summary 与历史文本入参 → PASSED
- T11 summarize_with_model content blocks：FakeModel 返回 list → 拼接为字符串 → PASSED
- T12 summarize_with_model 异常兜底：FakeModel 抛异常 → 返回空字符串（不抛错） → PASSED

### B. 节点级集成（test_compact_integration.py，直接调 compact_node，10 例）

- T13 冷启动空消息：state 中 messages 为空列表 → 返回空字典、不改 state → PASSED
- T14 未超阈值空转：2 轮小消息（threshold=12000） → 返回空字典 → PASSED
- T15 超阈值执行压缩：2 条 System 头 + 4 轮带 id 对话 + FakeModel（threshold=100、keep_last=4） → removed 为 h0 至 a3 全部 8 条（覆盖 start_idx: 全量、System 的 s0 与 s1 不删）；added 为 HumanMessage 摘要书签含 sum 与 Q2、A2、Q3、A3；模型仅 1 次 invoke → PASSED
- T16 摘要为空安全跳过：FakeModel 抛异常或返回空 → 返回空字典、无 RemoveMessage（防破坏） → PASSED
- T17 env 配置覆盖：threshold=50、keep_last=2、3 轮 20 字 → removed 6 条（start_idx 起全部）、added 3（摘要书签 + Q2 与 A2 保留）、added 首条为 human → PASSED
- T18 保留数量符合 keep_last：4 轮 20 字、keep_last=4 → removed 8（全删）；added 5（摘要书签 + Q2、A2、Q3、A3 保留区）；保留内容与 keep_last 语义一致 → PASSED

### C. 图级端到端（test_compact_integration.py，免 DB、mock 模型，最小图 compact → agent → END）

- T19 图入口连通：mock get_model_by_name 构建最小图；低阈值不压缩 → agent 收到原始消息、无摘要书签注入、图正常返回 → PASSED
- T20 摘要书签物理入 messages：注入超阈值 4 轮 + 带 id 消息走完整图 → 压缩后 messages 首条为 HumanMessage 摘要书签（含【历史摘要】与 rolling summary），其后为保留区；agent 调用（calls 末次）直接收到书签 → PASSED
- T21 裁剪后上下文收敛：压缩后再走一轮 → 上下文总字符与条数显著下降、剩余尾部对话完整可读 → PASSED
- T22 动态 Diff 回归后再入 compact：二次 invoke 模拟 diff-loop 回归 → compact 再次压缩不破坏 messages 内摘要书签累积（S1 保留）；模型调用累计 4 次 → PASSED

## 5. 验收标准（DoD）—— 已达成

1. 达成：uv run pytest tests.message -q → 26 passed、0 failed（含 T06b、T06c、T06d 工具链防呆补充与 T09b 仅 AI 锚点用例）
2. 达成：涉及 RemoveMessage 的用例显式断言 System 零删除（T15、T18）且 removals 覆盖 start_idx: 全量（含保留区旧 id）
3. 达成：T06 验证 tool 链防呆，新三元组语义下返回 (0,8)；T06b、T06c、T06d 细化工具链完整推进、不完整回退、命中 Tool 三分支
4. 达成：至少 1 个用例验证摘要失败等于安全 no-op（T12、T16 均通过）
5. 达成：图级测试不依赖网络、真实 API 与 PostgreSQL（T19-T22 走 FakeModel + 最小图）；T20-T22 已按 HumanMessage 书签口径同步
6. 达成：覆盖率 uv run pytest tests.message --cov=vibe_cli.message.message_compactor --cov-report=term → 99%（base_workflow 仅覆盖 compact 至 agent 最小路径，属预期）

## 6. 风险与注意事项

- 消息 id 是测试关键：不经 graph 直接调用 compact_node 时消息无自动 id；测试必须显式构造 id，否则 RemoveMessage 不生效导致误判。
- 中文 docstring 与 UTF-8：Windows 下读取文件需指定 -Encoding UTF8；测试源文件本身用 UTF-8 保存。
- int(os.getenv(...))：env 注入非法值时节点会抛 ValueError，用例需清理 env（monkeypatch.delenv）。
- 模型调用是副作用点：所有会触达模型的用例一律 FakeModel，防止测试打到真实 API。
- 摘要书签计入非 System 统计（type=human），常驻且内容短，不会造成已压缩内容反复触发；压缩删除区间为 messages[start_idx:]，头部 system 永不删除。

## 7. 落地执行记录（22 passed 阶段）

测试文件已创建于 tests 的 message 子目录并全部跑通：uv run pytest tests.message -q → 22 passed。

执行中发现并修复的 3 个环境适配点（已修正断言或工厂后复跑通过）：
1. AIMessage(tool_calls=None) 校验失败：当前 langchain_core 版本要求 tool_calls 为 list，显式 None 抛 pydantic ValidationError → make_msg 对 tool_calls 为 None 时不传该参数。
2. render_history 的 AI 行实际带 AI: 前缀（源码先判 isinstance(AIMessage)）→ T08 断言改为以 AI: [tool calls] 开头。
3. 图级模型调用计数：compact_node 内部 summarize_with_model 也是一次 invoke，agent 调用在其后 → T20、T22 断言模型入参改用 calls 末次（agent 那一次）区分两层。

运行命令对照：
- 快速验证：uv run pytest tests.message -q
- 覆盖率：uv run pytest tests.message --cov=vibe_cli.message.message_compactor --cov-report=term

## 8. 落地执行记录 2 —— 用户更新压缩代码流程后的中间状态（历史）

- 用户更新代码（当时未提交）：safe_cutoff 返回 (start_idx, cutoff)，start_idx 对齐首条 HumanMessage；AgentState 移除 summary 字段；compact_node 摘要书签由 SystemMessage 改为 HumanMessage（滚动融合设计）；compact 为图入口且 dynamic_diff_node 回归 compact；pyproject 新增 dev 依赖 pytest 与 pytest-cov。
- 当时测试：uv run pytest tests.message -q → 15 passed 与 7 failed（T06、T15、T17、T18、T20、T21、T22）。
- 失败根因：
  1. 摘要书签已改为 HumanMessage，旧断言仍写死 type 为 system（T15 与 T20-22）。
  2. compact_node.removals 只覆盖 messages[start_idx:cutoff]；按 LangGraph add_messages 语义保留区旧 id 未删除，随后 keep-* 克隆被追加，图级出现重复消息且摘要书签错位（T20-22 失败，T15 与 T17、T18 removed 计数不符）。
  3. safe_cutoff 新三元组语义下 T06 期望值 (0,6) 过期，实际返回 (0,8)。
- 待办（已于后续全部完成，见第 9 节）：
  - 修正 compact_node：removals 覆盖 messages[start_idx:] 全部带 id 消息（含保留区旧 id），messages[:start_idx] 头部不动；使 summary_msg 与 keep-* 克隆按 updates 顺序追加，保证通道顺序为 前置提示词、摘要书签、保留对话 且不重复。
  - 按 HumanMessage 书签口径同步 T06、T15、T17、T18、T20、T21、T22 断言。
  - uv run pytest tests.message -q 全绿；覆盖率 --cov 不低于 90%。

## 9. 落地执行记录 3 —— 代码流程调整完成后的计划与测试同步（最终）

- 代码已最终落地（与第 8 节待办一致）：
  1. compact_node.removals 现覆盖 messages[start_idx:] 全部带 id 消息（含保留区旧 id），messages[:start_idx] 头部不动。
  2. 摘要书签为 HumanMessage（id 为 summary-*，正文为【历史摘要】前缀 + new_summary），物理常驻 messages 通道。
  3. 保留区每条克隆换 keep-* 新 id；按 updates 顺序返回 removals + summary_msg + rebuilt_tail → 通道顺序 = 前置提示词 + 摘要书签 + 保留对话，无重复、无错位。
  4. summarize_with_model 已移除 2000 字符硬截断（text.strip() 原样返回），摘要精炼由 sys_summarize_prompt 约束。
  5. AgentState 不再维护独立 summary 字段（已从状态定义删除）。
- 测试同步结果：
  - T06 期望值按 (0,8) 校正；T15、T17、T18、T20、T21、T22 断言全部按 HumanMessage 书签 + start_idx 全删口径同步。
  - 新增工具链防呆用例 T06b、T06c、T06d 与仅 AI 无锚点用例 T09b（共 26 用例）。
  - uv run pytest tests.message -q → 26 passed（0.16s）；message_compactor.py 覆盖率 99%（miss 1 行）。
- 本计划文档同步更新：用例矩阵全部 PASSED、测试文件结构标注 26 例（T01-T12、T06b、T06c、T06d、T09b、T13-T22）。

