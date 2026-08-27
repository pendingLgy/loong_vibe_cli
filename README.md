# vibe-cli

基于 LangChain / LangGraph 的 AI 命令行助手，可调用 shell 工具执行命令，并通过安全审查机制拦截高风险操作。

## 技术栈

Python 3.13 / uv / langchain / langgraph / loguru / rich / pydantic / PostgreSQL (psycopg)

## 目录结构

```text
vibe-cli\
├─ pyproject.toml              # 项目配置(uv): 依赖、命令行入口 vibe-cli = vibe_cli:main
├─ README.md                   # 项目说明
├─ .vcl\
   ├─ skill\                   # skill 存放目录
   └─ struct.md                # 项目结构文档，会作为 AI 系统提示词
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
      │   ├─ sys_env_prompt.py           # 跨平台 Shell 系统提示词(平台适配、工作区越权拦截, 启动时注入模型)
      │   └─ __init__.py
      │
      ├─ tools\                # Agent 可用工具
      │   ├─ shell_tool.py          # shell 命令执行工具(多编码解码、失败重试、30s 超时、日志记录实际生效目录)
      │   └─ __init__.py            # ALL_TOOLS 工具注册表
      │
      ├─ wraps\                # 装饰器与安全包装层
      │   ├─ monitor.py             # LangGraph 节点监控装饰器(记录耗时与成败日志)
      │   ├─ workspace_security.py  # 工作区安全装饰器(拦截 cwd 越权与 command 绝对路径越权)
      │   └─ __init__.py
      │
      └─ workflow\             # LangGraph 工作流
         ├─ base_workflow.py      # 核心图: StateGraph + 安全审查路由 + 人工审批中断 + 动态 Diff 变更报告 + 读取 struct.md 作为系统提示
         └─ __init__.py
```

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
