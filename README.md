# vibe-cli

基于 LangChain、LangGraph 的 AI 命令行助手，可调用 shell 工具执行命令，并通过安全审查机制拦截高风险操作。

## 技术栈

Python 3.13、uv、langchain、langgraph、loguru、rich、pydantic、PostgreSQL (psycopg + psycopg_pool)

## 目录结构

```text
vibe-cli\
├─ pyproject.toml              # 项目配置(uv): 依赖、命令行入口 vibe-cli = vibe_cli:main, dev 依赖组 pytest、pytest-cov
├─ README.md                   # 项目说明
├─ .vcl\                       # AI 工作区元数据: struct.md(项目结构, 作系统提示)、skills(技能)、plans(计划)
├─ .gitignore                  # 忽略 __pycache__、.venv、.idea、logs、.coverage 等
├─ .python-version             # Python 3.13
├─ uv.lock                     # uv 依赖锁文件
├─ .env                        # 环境变量(model_provider、API Key、database_url 等)
├─ vibe_agent_workflow.png     # base_workflow 启动时生成的工作流拓扑图(自动)
├─ tests\                      # pytest 测试(消息压缩: 48 单元 + 16 集成, 共 64 例)
├─ src\
   ├─ vibe_cli\                # 主包
      ├─ main.py               # 程序入口: 加载 .env 配置(支持 APP_ENV 与 exe 打包)、同步启动 workflow(REPL)
      ├─ __init__.py           # 命令行脚本入口, 暴露 main()
      |
      ├─ env\                  # 运行环境基础设施: check_point_memory(同步 PostgreSQL Checkpointer + ConnectionPool 单例)、logger_config(loguru)、shutdown(优雅关闭: atexit+信号捕获关闭 PG 池与清理临时文件)
      ├─ message\              # 消息压缩核心(message_compactor.py: 按轮次压缩, is_summary_message、split_into_turns、plan_compaction、模型摘要, 默认保留最近 3 轮)
      ├─ model\                # 模型工厂(model_factory.py: get_model(provider) 按 model_provider 实例化 openai、deepseek)
      ├─ prompt\               # 提示词模板: Vibe Coding、安全审查、消息压缩摘要、动态 Diff 报告
      ├─ skill\                 # 专业技能注册: SkillRegistry(扫描 skills 目录加载 skill_index.md 与 SKILL.md)
      ├─ tools\                # Agent 可用工具: shell 执行、IDEA diff、技能加载(ALL_TOOLS 统一注册)
      ├─ wraps\                # 装饰器与安全包装: 节点监控、工作区越权拦截
      ├─ workflow\             # LangGraph 工作流: node_state(AgentState、NodeStatus) + load_sys_prompt(系统提示注入与同步) + base_workflow(6 节点图 + 路由 + HITL 审批 + 动态 Diff + 压缩 + REPL)
```

## 工作流与核心机制

Agent 由 LangGraph 同步 StateGraph 驱动，入口为 compact_msg 消息压缩节点，完整拓扑可查看 vibe_agent_workflow.png。

- 6 个显式节点：compact_msg、agent、safety_check、pend_approval、tools(ToolNode)、dynamic_diff_node。
- 每轮流程：compact_msg 按需将最旧轮次折叠为滚动摘要，随后进入 agent 由模型决定回复或调用工具：
  - 无工具调用：直接结束本轮，输出回复。
  - 有工具调用：进入 safety_check 做 LLM 安全审查；高危操作转入 pend_approval 经 interrupt() 挂起，REPL 展示审批信息，用户确认或拒绝后以 Command(resume=approved、rejected) 恢复；安全操作直接进入 tools 执行。
  - tools 执行完成后，route_check_mutation 根据用户输入是否含 diff、变更、git status 等关键词决定是否进入 dynamic_diff_node 生成【工作区文件变更汇总报告】，随后回到 agent。
- 系统提示注入：start() 每次启动调用 sync_system_prompts()，用 3 个固定 ID 的 SystemMessage(sys-vibe-coding、sys-struct、sys-skill-index，均 compressible=False) 经 update_state 幂等覆盖或追加，内容来自 sys_env_prompt、.vcl\struct.md、.vcl\skills\skill_index.md。
- 专业技能：SkillRegistry 启动时读取 .vcl\skills\skill_index.md 并扫描各技能子目录的 SKILL.md；REPL 内置技能清单快捷指令，可跳过 Agent 直接展示。
- 消息压缩：当非系统消息总字符数超过 compress_threshold_chars(默认 12000) 且完整轮次数多于 compress_keep_last_turns(默认 3) 时触发，保留最近 N 轮完整对话，其余折叠为中文滚动摘要。
- 高危操作人工审批(HITL)：REPL 由 stream_events(v3) 驱动打字机流，检测到 stream.interrupted 时读取 stream.interrupts 展示审批内容，用户确认后以 Command(resume=...) 恢复执行。
- 持久化与断点续跑：见下节。

## 持久化说明

对话状态通过 PostgreSQL Checkpointer 保存，重启后可使用 thread_id 恢复上下文。
持久化采用同步 PostgresSaver + ConnectionPool(模块级单例), 退出前可调 close_postgres_memory() 关闭连接池(程序退出时由 shutdown.py 注册 atexit 与 SIGINT、SIGTERM 自动执行); database_url 使用 postgresql 协议(psycopg 同步驱动), 首次运行自动建表。

## 测试

消息压缩模块已建立 pytest 测试体系（tests\message\）：

| 文件 | 覆盖 | 用例数 |
| ---- | ---- | ---- |
| test_message_compactor.py | 纯函数单元层(自包含, 无 conftest): total_size、split_into_turns、should_compress、safe_cutoff(工具链回溯、边界)、render_history、plan_compaction、summarize_with_model | 48 |
| test_compact_thread_history_integration.py | 真实线程历史集成层(自包含、只读 PostgreSQL, DB 缺失自动 skip): 轮次切分、压缩计划、节点注入、失败兜底、dry-run 预览、批准提交与拒绝不落库、重试重生成 | 16 |

运行方式：

```bash
uv run pytest tests -v
```

覆盖率(可选)：

```bash
uv run pytest tests --cov --cov-report=term-missing
```

## 启动
```bash
uv run vibe-cli
```

## 常用 uv 命令

| 命令 | 说明 |
| ---- | ---- |
| uv sync | 根据 pyproject.toml + uv.lock 安装依赖到虚拟环境 |
| uv add 包名 | 添加依赖并更新 lock 文件 |
| uv remove 包名 | 移除依赖 |
| uv run 命令 | 在项目虚拟环境中运行命令（自动创建 venv） |
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

项目配置集中在项目根目录 .env 中（main.py 会按候选顺序查找：当前工作目录（兼容 exe 打包）-> 项目根目录，并支持 APP_ENV 指定的 .env.{APP_ENV}）。

| 配置项 | 说明 | 示例 |
| ---- | ---- | ---- |
| model_provider | 模型厂商（传给 get_model），决定读取哪一组厂商变量 | deepseek |
| DEEPSEEK_MODEL_NAME | DeepSeek 模型名（provider=deepseek 时） | deepseek-chat |
| DEEPSEEK_BASE_URL | DeepSeek API 接口地址 | api.deepseek.com |
| DEEPSEEK_API_KEY | DeepSeek API 密钥 | sk-xxxxxxxx |
| OPENAI_MODEL_NAME | OpenAI 兼容模型名（provider=openai 时） | gpt-4o |
| OPENAI_BASE_URL | OpenAI 兼容接口地址 | api.openai.com |
| OPENAI_API_KEY | OpenAI API 密钥 | sk-xxxxxxxx |
| temperature | 采样温度，默认 0.0 | 0.0 |
| logger.dir | 日志输出目录（logs\） | 绝对路径 |
| database_url | PostgreSQL 连接串（LangGraph Checkpointer 持久化, psycopg 同步, postgresql 协议） | 参考 .env |
| work_dir | 工作区根目录（enforce_workspace_security 装饰器的权限边界） | 绝对路径 |
| compress_threshold_chars | 消息压缩触发阈值（非系统消息总字符数超限即压缩，默认 12000） | 12000 |
| compress_keep_last_turns | 消息压缩保留的最近完整轮次数（默认 3 轮） | 3 |
| IDEA_HOME | IDEA 可执行文件路径（show_diff_tool 拉起 diff 对比） | 绝对路径 |
| LANGSMITH_* | LangSmith 链路观测(TRACING、ENDPOINT、API_KEY、PROJECT) | 参考 .env |

说明：

1. main.py 查找顺序：当前工作目录 -> 项目根目录，优先加载 .env.{APP_ENV}，其次加载 .env，都没有则直接读取系统环境变量。
2. database_url 使用 psycopg 同步驱动（postgresql 协议，非 asyncpg），首次运行会自动建表。
3. work_dir 决定 shell 工具可访问的目录边界，cwd 或 command 中的路径越权会被拦截。
4. model_provider 支持的厂商可在 model_factory.get_model() 中扩展，默认支持 openai 与 deepseek；AgentState.model_provider 与 work_dir 在 REPL 首轮由环境变量注入。
