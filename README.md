# vibe-cli

基于 LangChain / LangGraph 的 AI 命令行助手，可调用 shell 工具执行命令，并通过安全审查机制拦截高风险操作。

## 技术栈

Python 3.13 / uv / langchain / langgraph / loguru / rich / pydantic / PostgreSQL (psycopg)

## 目录结构

```text
vibe-cli\
├─ pyproject.toml              # 项目配置(uv): 依赖、命令行入口 vibe-cli = vibe_cli:main
├─ README.md                   # 项目说明
├─ struct.md                   # 项目结构文档，会作为 AI 系统提示词
├─ .gitignore                  # 忽略 __pycache__ / .venv / .idea / logs 等
├─ .python-version             # Python 3.13
├─ uv.lock                     # uv 依赖锁文件
├─ .env                        # 环境变量(API Key、database_url 等)
└─ src\
   └─ vibe_cli\                # 主包
      ├─ main.py               # 程序入口: 加载 .env 配置、Windows 事件循环、启动 workflow
      ├─ __init__.py           # 命令行脚本入口, 暴露 main()
      │
      ├─ env\                  # 运行环境基础设施
      │   ├─ check_point_memory.py   # PostgreSQL Checkpointer(LangGraph 持久化、断点恢复)
      │   ├─ logger_config.py        # loguru 日志配置(控制台 + 文件, 目录可配置)
      │   └─ __init__.py
      │
      ├─ model\                # 模型与状态定义
      │   ├─ deepseek_model.py      # AgentState + NodeStatus + SafetyCheckResult + 模型工厂 get_model_by_name
      │   └─ __init__.py
      │
      ├─ prompt\               # 提示词模板
      │   ├─ sys_safe_check_prompt.py   # 工具调用安全审查提示词(高风险、耗时操作判定)
      │   └─ __init__.py
      │
      ├─ tools\                # Agent 可用工具
      │   ├─ shell_tool.py          # shell 命令执行工具(多编码解码、失败重试、30s 超时)
      │   └─ __init__.py            # ALL_TOOLS 工具注册表
      │
      ├─ wraps\                # 装饰器与安全包装层
      │   ├─ monitor.py             # LangGraph 节点监控装饰器(记录耗时与成败日志)
      │   ├─ workspace_security.py  # 工作区安全装饰器(拦截 cwd 越权与 command 绝对路径越权)
      │   └─ __init__.py
      │
      └─ workflow\             # LangGraph 工作流
         ├─ base_workflow.py      # 核心图: StateGraph + 安全审查路由 + 人工审批中断 + 读取 struct.md 作为系统提示
         └─ __init__.py
```
## 关键流程

1. ``main.run()`` 加载 .env 环境变量后调用 ``workflow.base_workflow.start()``
2. ``base_workflow`` 构建 StateGraph（4 个节点）：
   - ``agent``(模型生成回复、工具调用) -> ``safety_check`` 安全审查 -> ``pend_approval``(interrupt 挂起) / ``tools`` 工具执行
   - 高风险操作通过 ``interrupt()`` 挂起等待人工审批（requires_approval + NodeStatus）
   - 审批通过（``Command(resume=approved)``）后执行工具并回到 ``agent`` 循环；拒绝则生成 ToolMessage 拦截该操作并结束本轮
3. 模型由 ``deepseek_model.get_model_by_name`` 创建(支持 deepseek-chat 等)
4. 对话消息通过 PostgreSQL Checkpointer 持久化，支持断点续跑
5. ``struct.md`` 会被 ``base_workflow.load_system_prompt()`` 读取作为系统提示词，帮助 AI 理解项目结构

## 安全机制说明

1. Agent 决定调用工具时，sys_safe_check_prompt 生成审查提示词
2. 模型返回 SafetyCheckResult 判定 is_dangerous
3. 高风险操作触发 ``pend_approval`` 节点 ``interrupt()``，挂起等待用户确认
4. 用户批准后继续执行，拒绝则生成 ToolMessage 拦截并结束本轮
5. 工作区安全装饰器 ``enforce_workspace_security`` 拦截 cwd 越权与 command 中的绝对路径越权

## 持久化说明

对话状态通过 PostgreSQL Checkpointer 保存，重启后可使用 thread_id 恢复上下文。
数据库连接由 database_url 配置，首次运行会自动建表。

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

## 许可证

Copyright (c) loong. 保留所有权利。

## .env 配置说明

项目配置集中在项目根目录 .env 中（main.py 会按候选顺序查找：当前工作目录（兼容 exe 打包）-> 项目根目录，并支持 APP_ENV 指定的 .env.{APP_ENV}）。

| 配置项 | 说明 | 示例 |
| ---- | ---- | ---- |
| model_name | 使用的模型名称（传给 get_model_by_name） | deepseek-chat |
| url | 模型 API 接口地址（对应 AgentState.base_url） | https://api.deepseek.com |
| api_key | 模型 API 密钥（对应 AgentState.api_key） | sk-xxxxxxxx |
| temperature | 采样温度，默认 0.0 | 0.0 |
| logger.dir | 日志输出目录（logs/） | 绝对路径 |
| database_url | PostgreSQL 连接串（LangGraph Checkpointer 持久化） | postgresql+asyncpg://user:pass@localhost:5432/vibe_cli |
| work_dir | 工作区根目录（enforce_workspace_security 装饰器的权限边界） | 绝对路径 |

说明：

1. main.py 查找顺序：当前工作目录 -> 项目根目录，优先加载 .env.{APP_ENV}，其次加载 .env，都没有则直接读取系统环境变量。
2. database_url 需为 asyncpg 协议（postgresql+asyncpg://），首次运行会自动建表。
3. work_dir 决定 shell 工具可访问的目录边界，cwd 或 command 中的路径越权会被拦截。
