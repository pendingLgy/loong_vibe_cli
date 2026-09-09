# Task Plan: message_compactor.py 单元测试（单文件自包含版）
- Objective: 为 src\vibe_cli\message\message_compactor.py 的全部对外纯函数编写高质量单元测试；覆盖边界与工具链完整性场景；不包含任何 LangGraph 图级或集成测试，不依赖真实 LLM、数据库与 workflow 启动。
- Status: In Progress

## 约束变更（用户最新要求）
- 不创建共享 conftest.py（不使用共享 fixtures）
- helpers 与 FakeModel 全部内联在测试文件内部，测试文件完全自包含
- 用例命名语义化，按函数分组

## 背景与范围
- 被测对象: src\vibe_cli\message\message_compactor.py
- 产出文件: 仅 tests\message\test_message_compactor.py（单文件）
- 测试目录: tests\message\
- 明确排除: 图级集成测试等价物，包括 LangGraph 工作流图、HITL 审批、Checkpointer、Postgres 持久化、REPL 启动等，一律不写
- 参考: git 历史中已删除的旧测试用例命名风格 t01 至 t12 可作场景对照，但按新源码结构重写

## Steps
- [x] Step 1: 在测试文件内内联定义 FakeModel、make_msg 等 helpers（不使用共享 conftest）
- [x] Step 2: 编写 total_size 与 should_compress 单元测试：系统消息不计入、keep_last 数量边界、threshold_chars 字符阈值、空列表与恰好等于阈值的边界
- [x] Step 3: 编写 safe_cutoff 单元测试（核心复杂逻辑）：正常 span、系统消息头、无 human 锚点返回 None、消息过少返回 None、起始 ToolMessage 右移、完整工具链推进 cutoff、残缺工具链回溯、ToolMessage 回溯、非法边界返回 None
- [x] Step 4: 编写 render_history 与 plan_compaction 单元测试：四类角色渲染、AI tool_calls 占位、空消息、plan_compaction 的 None 与三元组状态、AI-only 历史拒绝压缩
- [x] Step 5: 编写 summarize_with_model 单元测试：FakeModel 返回纯字符串、content blocks 列表归一化为字符串、invoke 异常返回空串、空内容与空白裁剪
- [x] Step 6: 验证与自测：uv run pytest tests\message\test_message_compactor.py -v 全部通过

## 验收标准
- pytest 全绿，单元测试不触碰真实模型、网络、数据库或工作区外部文件
- 仅新增 test_message_compactor.py 一个测试源文件，不创建 conftest.py 与任何集成测试文件
- helpers 与 FakeModel 均为测试文件内部定义，不使用共享 fixtures


## 集成测试模块计划补充：历史 thread_id 驱动消息压缩（新增）
- 触发原因：新需求 —— 通过历史 thread_id 获取真实历史消息，再以历史消息验证消息压缩链路，形成 单元测试→集成测试 两级覆盖
- 定位：在既有纯函数单测之上新增只读集成测试，验证「PostgreSQL Checkpointer → thread_id → channel messages → 消息压缩」端到端打通；不 mock 数据库层
- 产出文件：tests\message	est_compact_thread_history_integration.py（自包含）
- 执行命令：uv run pytest tests\message	est_compact_thread_history_integration.py -v

### 约束（沿用既有风格）
- 不创建共享 conftest.py、不使用共享 fixtures；DB 连接与辅助函数全部内联在测试文件内部
- 读取真实历史为只读操作：不写入、不修改、不删除任何既有 checkpoint；首版不做数据清理
- 摘要环节通过 monkeypatch 注入 FakeModel，避免真实 LLM 网络调用与 API 消耗（默认不启用真实模型）
- 数据库不可达、缺少 database_url 或指定 thread 不存在时用 pytest.skip 自动跳过，不硬性失败，不影响纯单测结果

### 被测链路与核心 API
1. 加载项目 .env 获得 database_url（缺失则 skip）
2. postgres_memory() 获取 PostgreSQL Checkpointer 单例（PostgresSaver）
3. 构造读取配置 config = {configurable: {thread_id: 历史会话ID}}
4. 调用 checkpointer.get_tuple(config) 取最新 checkpoint，消息列表 = tuple.checkpoint[channel_values][messages]
5. 以真实历史消息驱动压缩：plan_compaction / safe_cutoff / render_history / summarize_with_model（FakeModel），或经 monkeypatch get_model 后调用 compact_msg_node(state) 验证节点行为

### 历史数据来源（已实测存在）
- PostgreSQL checkpoints 表按 thread_id 分组计数，可选出非 system 消息多、总字符数超过压缩阈值（默认 12000）的长会话；实测样本：123009、123012、123022、terminal_vibe_session 等
- 测试内预设默认 thread 常量 HISTORY_THREAD_ID（建议取长会话如 123012），允许通过环境变量覆盖；若该 thread 无足够历史则 skip

### Steps
- [x] Step 1: 新建 tests\message	est_compact_thread_history_integration.py（自包含：内联加载 .env、DB 连接探测与 skip、read_messages(tid) helper、FakeModel）
- [x] Step 2: 用例一 历史读取：get_tuple 返回最新 checkpoint，channel_values.messages 非空且至少含一条 human 消息
- [x] Step 3: 用例二 历史可压缩规划：对真实长会话调 plan_compaction 断言返回三元组且 start 小于 cutoff；对短会话断言返回 None
- [x] Step 4: 用例三 压缩节点注入：monkeypatch get_model 返回 FakeModel，调用 compact_msg_node(state)（state 含真实历史消息），断言返回消息中包含 summary- 前缀 HumanMessage 与 keep- 前缀的保留尾部克隆
- [x] Step 5: 用例四 失败兜底：FakeModel 抛异常时 summarize_with_model 返回空串，compact 节点安全跳过压缩返回空字典
- [x] Step 6: 验证与回归：先单独运行本集成文件，再运行 uv run pytest tests -q 确认全部通过、无回归

### 验收标准
- 新增 1 个自包含集成测试文件，能通过历史 thread_id 从 PostgreSQL 读取历史消息并完成消息压缩链路验证
- 全量 pytest 通过；集成测试在 DB 缺失、连接失败、线程过短等场景自动 skip，不影响既有 48 个单测结果
- 不改动 src 下任何源码，不修改既有单测文件，不创建共享 conftest

## 补充 2：压缩结果人工确认——压缩前后 dry-run 预览（新增）

### 触发原因与目标
- 新增需求：获取到需要压缩的消息片段后，需调用一次模型压缩消息；随后输出「压缩前原始内容」与「压缩后内容」交由人工确认，确认通过后才真正写回
- 现状缺口：既有压缩链路在 compact 节点内生成摘要后立即删除旧消息并替换为 summary，属自动静默变更，人工无法审计
- 定位：在「历史 thread 读取、压缩、写回」链路上增加人工确认门 dry-run，将消息压缩从自动静默升级为可审计、可拒绝

### 流程设计（一次模型调用、dry-run 预览、人工确认、提交）
1. 读取历史消息（沿用前述步骤）
2. plan_compaction 与 safe_cutoff 计算待压缩区间 [start, end] 与保留尾部起点绝对索引
3. 对待压缩片段只调用一次模型生成摘要：summarize_with_model(render_history(片段))，集成测试默认注入 FakeModel
4. 构建预览对象 CompactionPreview：
   - before：原始待压缩片段经 render_history 渲染得到的压缩前内容
   - after：模型生成的摘要文本与保留尾部说明（压缩后内容）
   - affected_indexes：将被 RemoveMessage 删除的原始消息绝对索引列表
   - summary_text：待写入的 summary-* 书签正文
5. 输出 dry-run 对照（before 与 after 并列展示）并请求人工确认，三选一：approve、reject、redo
6. approve 返回可提交消息操作（RemoveMessage 与 summary-* 书签及 keep-* 尾部克隆）；reject 不产生写回，原消息与 checkpoint 不变；redo 重新调用一次模型生成新摘要

### 落地位置与测试要点（保持不改 src 承诺）
- 确认环节以集成测试文件内联的 approve_or_reject(preview) 函数表达，默认 approve（可被环境变量覆盖）；测试中 monkeypatch 该函数分别返回 approve、reject、redo 分支，不强制进入 LangGraph interrupt，避免扩大改动面
- 集成测试文件自包含：新增 CompactionPreview 简易容器与 render_before_after(preview) 对照渲染辅助，全部内联，不建共享 conftest
- 只读承诺延续：dry-run 预览与 reject 路径绝不触碰数据库写入；仅 approve 提交路径返回写回操作（首版测试用返回结构断言，不真实落库）
- 若未来需在真实 REPL 或工作流中呈现人工确认 UI，属后续迭代，应另立实现计划并同步微调 base_workflow，不在本计划范围内

### Steps（新增）
- [x] Step A: 在集成测试文件内联 CompactionPreview 容器、render_before_after(preview) 对照渲染与 approve_or_reject(preview) monkeypatch 钩子
- [x] Step B: 用例-模型仅调用一次：注入计数 FakeModel，断言一个待压缩片段恰好触发一次模型调用且摘要非空
- [x] Step C: 用例-dry-run 对照：preview.before 等于 render_history(原始片段)；preview.after 同时包含摘要文本与保留尾部说明
- [x] Step D: 用例-approve 提交：monkeypatch 返回 approve 后，提交消息操作含 RemoveMessage 且 summary- 与 keep- 前缀齐全；get_tuple 前后消息数一致（未真实落库）
- [x] Step E: 用例-reject 拒绝：monkeypatch 返回 reject 后无任何写回操作，原消息列表原样
- [x] Step F: 全量回归：uv run pytest tests -q 全部通过，无回归

### 验收标准（增量）
- 每个待压缩片段模型只调用一次；模型摘要即 after 内容
- 所有写回均发生在人工 approve 之后；dry-run 与 reject 阶段零副作用
- reject 与 redo 不影响原消息与 checkpoint 内容
- 既有 48 个单测与前述历史驱动集成用例全部通过；不改 src、不建共享 conftest