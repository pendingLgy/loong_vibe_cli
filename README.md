# vibe-cli

基于 LangChain、LangGraph 的 AI 命令行助手，可调用 shell 工具执行命令，并通过安全审查机制拦截高风险操作。

## 技术栈

Python 3.13、uv、langchain、langgraph、loguru、rich、pydantic、PostgreSQL (psycopg + psycopg_pool)

## 目录结构

```text
vibe-cli\
├─ pyproject.toml              # 项目配置(uv): 依赖、命令行入口 vibe-cli = vibe_cli:main、dev 依赖组 pytest 与 pytest-cov
├─ README.md                   # 项目说明
├─ .vcl\                       # AI 工作区元数据: struct.md(项目结构, 作系统提示)、skills(技能)、plans(计划)
├─ .gitignore                  # 忽略 __pycache__、.venv、.idea、logs、.coverage 等
├─ .python-version             # Python 3.13
├─ uv.lock                     # uv 依赖锁文件
├─ .env                        # 环境变量(model_provider、database_url、work_dir、IDEA_HOME 等)
├─ vibe_agent_workflow.png     # start() 启动时自动生成的工作流拓扑图
├─ tests\                      # pytest 测试(消息压缩: 48 单元 + 16 集成, 共 64 例)
├─ scripts\                    # 本地调试脚本(inspect_checkpoint.py 经函数参数查 PostgreSQL checkpoint 四表, 不集成进 Agent 工具链)
├─ src\
   ├─ vibe_cli\                # 主包
      ├─ main.py               # 程序入口: get_base_dir(兼容源码与 PyInstaller exe)、_resolve_env_file 按序加载 .env(支持 APP_ENV)、run() 启动 workflow(REPL)
      ├─ __init__.py           # 命令行脚本入口, 暴露 main()
      |
      ├─ env\                  # 运行环境基础设施
      |   ├─ check_point_memory.py   # PostgreSQL Checkpointer(同步 PostgresSaver 单例 + psycopg_pool.ConnectionPool、setup 自动建表、close_postgres_memory 关闭)
      |   ├─ logger_config.py        # loguru 日志配置(控制台 + 文件: rotation 500MB、retention 7 天、zip 压缩)
      |   ├─ shutdown.py             # 优雅关闭: 模块导入即 atexit 注册 cleanup + SIGINT、SIGTERM 信号捕获, 关闭 PG 连接池并清理 IDEA diff 临时文件
      |   ├─ workspace.py            # 工作区解析: split_work_dir(raw)、get_work_dirs(work_dir)、get_primary_work_dir(work_dir), 支持多个 work_dir
      |   ├─ __init__.py
      |
      ├─ core\                 # 核心状态与编排
      |   ├─ node_state.py          # 状态定义: AgentState(TypedDict) + NodeStatus(NORMAL、PEND_APPROVAL、FINISHED)
      |   ├─ load_sys_prompt.py     # 系统提示注入: SYS_ID_* 固定 ID、_load_system_struct_prompt(primary_dir) 读取 .vcl\struct.md、_init_sys_prompt、sync_system_prompts(update_state 幂等覆盖)
      |   ├─ task_resume.py         # 断点续跑: task_resume(app, config, console) 检测未完成任务并以 stream_events 恢复
      |   ├─ __init__.py
      |
      ├─ message\              # 消息压缩与清理模块
      |   ├─ message_compactor.py   # 按轮次(Turn)压缩核心: is_summary_message、total_size、split_into_turns、should_compress、safe_cutoff、is_compressible、render_history、plan_compaction、summarize_with_model(模块默认阈值 12000 字符, 保留最近 3 轮)
      |   ├─ message_check.py       # 消息完整性清理: check_message_completed 删除 tool_calls 未全部完成的 AIMessage 及其孤立 ToolMessage
      |   ├─ __init__.py
      |
      ├─ model\                # 模型工厂
      |   ├─ model_factory.py       # get_model(provider): 按 model_provider 动态实例化 openai、deepseek(经 langchain_openai.ChatOpenAI, 读环境变量)
      |   ├─ __init__.py
      |
      ├─ prompt\               # 提示词模板
      |   ├─ sys_env_prompt.py          # Vibe Coding 系统提示词(sys_vibe_coding_agent(work_dirs), 含跨平台 Shell 与多工作区边界约束)
      |   ├─ sys_safe_check_prompt.py   # 工具调用安全审查提示词(高风险、耗时操作判定, 要求 JSON 输出)
      |   ├─ sys_summarize_prompt.py    # 消息压缩摘要提示词(折叠旧摘要与历史, 输出中文摘要 500 字以内)
      |   ├─ sys_diff_prompt.py         # 动态 Diff 变更汇总报告提示词(diff_prompt)
      |   ├─ __init__.py
      |
      ├─ skill\                # 专业技能注册
      |   ├─ loading_skill.py       # SkillRegistry(skills_dir): 读取 skill_index.md 为 index_content, 扫描 .vcl\skills 下各技能子目录的 SKILL.md 解析 frontmatter name 建注册表
      |   ├─ __init__.py
      |
      ├─ tools\                # Agent 可用工具
      |   ├─ shell_tool.py          # shell 命令执行工具(多编码解码、失败重试、30s 超时、日志记录实际生效目录)
      |   ├─ show_diff_tool.py      # IDEA Diff 工具(拉起 IDEA 对比文件与 Git HEAD, 临时文件存 .vcl 下 .idea_diff; 当前未注册进 ALL_TOOLS)
      |   ├─ load_skills_tool.py    # 技能加载工具(按需读取 SKILL.md, 支持重试)
      |   ├─ __init__.py            # ALL_TOOLS 工具注册表(execute_shell_command、load_skill_detail) + DANGEROUS_TOOLS
      |
      ├─ wraps\                # 装饰器与安全包装层
      |   ├─ monitor.py             # LangGraph 节点监控装饰器(记录耗时与成败日志)
      |   ├─ workspace_security.py  # 工作区安全装饰器(多工作区拦截 cwd 越权与 command 绝对路径越权; Windows 命令开关放行)
      |   ├─ __init__.py
      |
      ├─ workflow\             # LangGraph 工作流
      |   ├─ node_graph.py        # 同步核心图: build_vibe_app() 组装 6 节点(compact_msg、agent、safety_check、pend_approval、tools、dynamic_diff_node) + 条件路由 + interrupt 人工审批(HITL)
      |   ├─ base_workflow.py     # 启动与交互: start() 构建图、注入系统提示、恢复任务、REPL 主循环(stream_events v3 打字机 + 技能快捷指令)
      |   ├─ __init__.py
```

## 工作流与核心机制

Agent 由 LangGraph 同步 StateGraph 驱动, 图定义在 workflow\node_graph.py 的 build_vibe_app(), 入口为 compact_msg 消息压缩节点, 完整拓扑可查看启动时生成的 vibe_agent_workflow.png。

- 6 个显式节点: compact_msg、agent、safety_check、pend_approval、tools(ToolNode)、dynamic_diff_node。
- 图连边(当前实现): compact_msg -> agent; agent 条件路由(route_agent: 无工具调用直接 END, 有工具调用则去 safety_check); safety_check 条件路由(route_safety_command: 高危去 pend_approval, 安全去 tools, 拒绝去 END); pend_approval 条件路由(route_approval: 恢复后去 tools 或 agent); tools -> agent。
- 每轮流程: compact_msg 按需将最旧轮次折叠为滚动摘要, 随后进入 agent 由模型决定回复或调用工具:
  - 无工具调用: 直接结束本轮, 输出回复。
  - 有工具调用: 进入 safety_check 做 LLM 安全审查; 高危操作转入 pend_approval 经 interrupt() 挂起, REPL 展示审批信息, 用户确认或拒绝后以 Command(resume=approved、rejected) 恢复; 安全操作直接进入 tools 执行。
- 说明: route_check_mutation 与 dynamic_diff_node(按用户 diff、变更意图触发工作区变更汇总)当前保留实现, 但对应条件连边处于注释状态, 未接入主链路。
- 系统提示注入: start() 每次启动调用 core\load_sys_prompt.sync_system_prompts(), 用 3 条固定 ID 的 SystemMessage(sys-vibe-coding、sys-struct、sys-skill-index, 均标记 compressible=False) 经 update_state 幂等覆盖或追加, 内容来自 sys_env_prompt、主工作区的 .vcl\struct.md、.vcl\skills\skill_index.md。
- 专业技能: SkillRegistry 启动时读取 .vcl\skills\skill_index.md 并扫描各技能子目录的 SKILL.md; REPL 内置技能清单快捷指令(输入 skill 或 skills), 可跳过 Agent 直接展示。
- 消息压缩: 当非系统消息总字符数超过 compress_threshold_chars(compact_msg 节点默认 1200000 以适配 1M 上下文; 压缩模块默认 12000) 且完整轮次多于 compress_keep_last_turns(默认 3) 时触发, 保留最近 N 轮完整对话, 其余折叠为中文滚动摘要。
- 消息清理: start() 调用 message_check.check_message_completed(), 删除 tool_calls 未全部完成的 AIMessage 及其孤立 ToolMessage, 保证状态一致。
- 高危操作人工审批(HITL): REPL 由 stream_events(v3) 驱动打字机流, 检测到 stream.interrupted 时读取 stream.interrupts 展示审批内容, 用户确认后以 Command(resume=...) 恢复执行。
- 持久化与断点续跑: 见下节。

## 持久化说明

对话状态通过 PostgreSQL Checkpointer 保存, 重连后可使用 thread_id 恢复上下文。
持久化采用同步 PostgresSaver + ConnectionPool(模块级单例), 退出前可调用 close_postgres_memory() 关闭连接池(程序退出时由 shutdown.py 注册 atexit 与 SIGINT、SIGTERM 自动执行); database_url 使用 postgresql 协议(psycopg 同步驱动), 首次运行自动建表。

## 测试

消息压缩模块已建立 pytest 测试体系(tests\message\):

| 文件 | 覆盖 | 用例数 |
| ---- | ---- | ---- |
| test_message_compactor.py | 纯函数单元层(自包含, 无 conftest): total_size、split_into_turns、should_compress、safe_cutoff(工具链回溯、边界)、render_history、plan_compaction、summarize_with_model | 48 |
| test_compact_thread_history_integration.py | 真实线程历史集成层(自包含、只读 PostgreSQL, DB 缺失自动 skip): 轮次切分、压缩计划、节点注入、失败兜底、dry-run 预演、批准提交与拒绝不落库、重试重生成 | 16 |

运行方式:

```bash
uv run pytest tests -v
```

覆盖率(可选):

```bash
uv run pytest tests --cov --cov-report=term-missing
```

## 启动

```bash
uv run vibe-cli
```

## 调试脚本

`scripts\inspect_checkpoint.py` 为独立调试脚本, 用于查看 LangGraph Checkpointer 在 PostgreSQL 中的四张表数据。**参数通过函数参数传入**(不使用命令行参数), 结果以可序列化 dict 或 JSON 字符串返回(不集成进 Agent 工具链):

```python
from scripts.inspect_checkpoint import inspect_table, inspect_table_json

# 方式一: 返回可 JSON 序列化的 dict
result = inspect_table("checkpoints", thread_id="123025", limit=50)

# 方式二: 直接返回 JSON 字符串
print(inspect_table_json("checkpoint_blobs", limit=100, decode_blob=False))
```

- 默认降序展示最新数据: checkpoints 与 checkpoint_writes 按 checkpoint_id, checkpoint_blobs 按 version
- 直接运行脚本时, 修改文件末尾 TARGET_ 变量即可查看目标数据 (uv run python scripts\inspect_checkpoint.py)
- msgpack blob 自动反序列化为可读文本, LangChain 消息展开为 message_type、id、content、tool_calls
- decode_blob=False 时返回 UTF-8 优先、hex 兜底的原始输出
- 解码失败时附 deserialize_error 字段并返回原始内容, 保证输出始终为合法 JSON
- 表名非法或 database_url 缺失时返回带 error 字段的 dict, 不抛异常
- .env 加载顺序与 main.py 一致: 当前工作目录优先、其次项目根目录, 支持 APP_ENV 指定 .env.{APP_ENV}

## 常用 uv 命令

| 命令 | 说明 |
| ---- | ---- |
| uv sync | 根据 pyproject.toml + uv.lock 安装依赖到虚拟环境 |
| uv add 包名 | 添加依赖并更新 lock 文件 |
| uv remove 包名 | 移除依赖 |
| uv run 命令 | 在项目虚拟环境中运行命令(自动创建 venv) |
| uv python install 3.13 | 安装指定版本 Python |
| uv lock | 重新生成锁定文件 |
| uv build | 构建 wheel + sdist 发布包 |
| uv tool install . | 全局安装本项目命令行工具 |

## 构建与安装

```bash
uv build                # 构建 wheel + sdist
uv tool install .       # 全局安装命令行工具
```

安装后可直接使用 vibe-cli 命令启动。

## .env 配置说明

项目配置集中在项目根目录 .env 中(main.py 会按候选顺序查找: 当前工作目录(兼容 exe 打包) -> 项目根目录, 并支持 APP_ENV 指定的 .env.{APP_ENV})。

| 配置项 | 说明 | 示例 |
| ---- | ---- | ---- |
| model_provider | 模型厂商(传给 get_model), 决定读取哪一组厂商变量 | deepseek |
| DEEPSEEK_MODEL_NAME | DeepSeek 模型名(provider=deepseek 时) | deepseek-chat |
| DEEPSEEK_BASE_URL | DeepSeek API 接口地址 | api.deepseek.com |
| DEEPSEEK_API_KEY | DeepSeek API 密钥 | sk-xxxxxxxx |
| OPENAI_MODEL_NAME | OpenAI 兼容模型名(provider=openai 时) | gpt-4o |
| OPENAI_BASE_URL | OpenAI 兼容接口地址 | api.openai.com |
| OPENAI_API_KEY | OpenAI API 密钥 | sk-xxxxxxxx |
| temperature | 采样温度, 默认 0.0 | 0.0 |
| logger.dir | 日志输出目录(logs\) | 绝对路径 |
| database_url | PostgreSQL 连接串(LangGraph Checkpointer 持久化, psycopg 同步, postgresql 协议) | 参考 .env |
| work_dir | 工作区根目录, 支持多个(逗号或分号分隔), 首个为主工作区; enforce_workspace_security 的权限边界 | 绝对路径 |
| compress_threshold_chars | 消息压缩触发阈值(非系统消息总字符数超限即压缩) | 12000 |
| compress_keep_last_turns | 消息压缩保留的最近完整轮次数(默认 3 轮) | 3 |
| IDEA_HOME | IDEA 可执行文件路径(show_diff_tool 拉起 diff 对比) | 绝对路径 |
| LANGSMITH_* | LangSmith 链路观测(TRACING、ENDPOINT、API_KEY、PROJECT) | 参考 .env |

说明:

1. main.py 查找顺序: 当前工作目录 -> 项目根目录, 优先加载 .env.{APP_ENV}, 其次加载 .env, 都没有则直接读取系统环境变量。
2. database_url 使用 psycopg 同步驱动(postgresql 协议, 非 asyncpg), 首次运行会自动建表。
3. work_dir 决定 shell 工具可访问的目录边界, cwd 或 command 中的路径越权会被拦截; 支持声明多个工作目录, 越权提示会列出全部合法工作区。
4. model_provider 支持的厂商可在 model_factory.get_model() 中扩展, 默认支持 openai 与 deepseek; AgentState.model_provider 与 work_dir 在 REPL 首轮由环境变量注入。

## 多工作区 (Multi work_dir)

work_dir 支持声明一个或多个工作目录, 多个目录使用英文逗号或分号分隔:

```dotenv
work_dir='dir_a,dir_b'
```

- 列表中的第一个目录为主工作目录 (primary), 作为默认 cwd 与技能 (.vcl\skills) 扫描位置。
- shell 工具与 enforce_workspace_security 允许访问任一合法工作区; 越权访问会被拦截并提示全部合法工作区。
- 仅配置单个目录时行为与旧版本完全兼容。
- 解析实现位于 src\vibe_cli\env\workspace.py, 安全放行还包含 Windows 命令开关处理。
