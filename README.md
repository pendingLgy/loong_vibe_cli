# vibe-cli

基于 LangChain、LangGraph 的 AI 命令行助手，可调用 shell 工具执行命令，并通过安全审查机制拦截高风险操作。

## 技术栈

Python 3.13、uv、langchain、langgraph、loguru、rich、pydantic、PostgreSQL (psycopg)

## 目录结构

```text
vibe-cli\
├─ pyproject.toml              # 项目配置(uv): 依赖、命令行入口 vibe-cli = vibe_cli:main, dev 依赖组 pytest、pytest-cov
├─ README.md                   # 项目说明
├─ .vcl\                      # AI 工作区元数据: struct.md(项目结构, 作系统提示)、skills(技能)、plans(计划)
├─ .gitignore                  # 忽略 __pycache__、.venv、.idea、logs、.coverage 等
├─ .python-version             # Python 3.13
├─ uv.lock                     # uv 依赖锁文件
├─ .env                        # 环境变量(API Key、database_url 等)
├─ tests\                      # pytest 测试(消息压缩: 16 单元 + 10 集成, 共 26 例)
├─ src\
   ├─ vibe_cli\                # 主包
      ├─ main.py               # 程序入口: 加载 .env 配置、同步启动 workflow(REPL)
      ├─ __init__.py           # 命令行脚本入口, 暴露 main()
      |
      ├─ env\                  # 运行环境基础设施: check_point_memory(同步 PostgreSQL Checkpointer)、logger_config(loguru)、shutdown(优雅关闭: atexit+信号捕获关闭 PG 池与清理临时文件)
      ├─ message\              # 消息压缩核心(message_compactor.py: 阈值判断、安全切分、plan、模型摘要, 无 2000 硬截断)
      ├─ model\                # 模型与状态定义: AgentState、SafetyCheckResult、get_model_by_name
      ├─ prompt\               # 提示词模板: 安全审查、跨平台 Shell、Vibe Coding、消息压缩摘要
      ├─ skill\                 # 专业技能注册: SkillRegistry(SKILL.md 扫描与管理)
      ├─ tools\                # Agent 可用工具: shell 执行、IDEA diff、技能加载
      ├─ wraps\                # 装饰器与安全包装: 节点监控、工作区越权拦截
      ├─ workflow\             # LangGraph 工作流: base_workflow(6 节点图 + 路由 + 审批 + 动态 Diff + 压缩)
```

## 持久化说明

对话状态通过 PostgreSQL Checkpointer 保存，重启后可使用 thread_id 恢复上下文。
持久化采用同步 PostgresSaver + ConnectionPool(模块级单例), 退出前可调 close_postgres_memory() 关闭连接池(程序退出时由 shutdown.py 注册 atexit 与 SIGINT、SIGTERM 自动执行); database_url 使用 postgresql:// 协议(psycopg 同步驱动), 首次运行自动建表。

## 测试

消息压缩模块已建立 pytest 测试体系（tests\message\）：

| 文件 | 覆盖 | 用例数 |
| ---- | ---- | ---- |
| test_message_compactor.py | 纯函数单元层: total_size、should_compress、safe_cutoff(工具链回溯、边界)、render_history、plan_compaction、summarize_with_model | 16 |
| test_compact_integration.py | 图级集成层: 入口压缩、摘要注入与保留、上下文收缩、diff 循环重入、失败兜底 | 10 |

运行方式：

```bash
uv run pytest tests -v
```

覆盖率(可选)：

```bash
uv run pytest tests --cov --cov-report=term-missing
```

## 常用 uv 命令

| 命令 | 说明 |
| ---- | ---- | ---- |
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
| model_name | 使用的模型名称（传给 get_model_by_name） | deepseek-chat |
| url | 模型 API 接口地址（对应 AgentState.base_url） | api.deepseek.com |
| api_key | 模型 API 密钥（对应 AgentState.api_key） | sk-xxxxxxxx |
| temperature | 采样温度，默认 0.0 | 0.0 |
| logger.dir | 日志输出目录（logs\） | 绝对路径 |
| database_url | PostgreSQL 连接串（LangGraph Checkpointer 持久化, psycopg 同步） | postgresql://user:pass@localhost:5432/vibe_cli |
| work_dir | 工作区根目录（enforce_workspace_security 装饰器的权限边界） | 绝对路径 |
| compress_threshold_chars | 消息压缩触发阈值（非系统消息总字符数超限即压缩，默认 12000） | 12000 |
| compress_keep_last | 消息压缩保留的最近消息条数（默认 12） | 12 |
| IDEA_HOME | IDEA 可执行文件路径（show_diff_tool 拉起 diff 对比） | 绝对路径 |
| LANGSMITH_* | LangSmith 链路观测(TRACING、ENDPOINT、API_KEY、PROJECT) | 参考 .env |

说明：

1. main.py 查找顺序：当前工作目录 -> 项目根目录，优先加载 .env.{APP_ENV}，其次加载 .env，都没有则直接读取系统环境变量。
2. database_url 使用 psycopg 同步协议（postgresql://，非 asyncpg），首次运行会自动建表。
3. work_dir 决定 shell 工具可访问的目录边界，cwd 或 command 中的路径越权会被拦截。
