# vibe-cli 项目结构

基于 LangChain / LangGraph 的 AI 命令行助手，可调用 shell 工具执行命令，并通过安全审查机制拦截高风险操作。

技术栈：Python 3.13 / uv / langchain / langgraph / loguru / rich / pydantic / PostgreSQL (psycopg)

## 目录结构

```text
vibe-cli\
├─ pyproject.toml              # 项目配置(uv): 依赖、命令行入口 vibe-cli = vibe_cli:main
├─ README.md                   # 项目说明
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
      │   ├─ sys_env_prompt.py           # 跨平台 Shell 提示词(sys_env_shell_prompt) + Vibe Coding 提示词(sys_vibe_coding_agent)
      │   └─ __init__.py
      │
      ├─ tools\                # Agent 可用工具
      │   ├─ shell_tool.py          # shell 命令执行工具(多编码解码、失败重试、30s 超时、日志记录实际生效目录)
      │   ├─ show_diff_tool.py      # IDEA Diff 工具(拉起 IDEA 打开文件 vs Git HEAD, 临时文件存 .vcl/.idea_diff)
      │   └─ __init__.py            # ALL_TOOLS 工具注册表(execute_shell_command + show_diff)
      │
      ├─ wraps\                # 装饰器与安全包装层
      │   ├─ monitor.py             # LangGraph 节点监控装饰器(记录耗时与成败日志)
      │   ├─ workspace_security.py  # 工作区安全装饰器(拦截 cwd 越权与 command 绝对路径越权)
      │   └─ __init__.py
      │
      └─ workflow\             # LangGraph 工作流
         ├─ base_workflow.py      # 核心图: StateGraph + 安全审查路由 + 人工审批中断 + 动态 Diff 变更报告 + 读取 struct.md 作为系统提示 + 入口 compact 消息压缩
         └─ __init__.py
```
## 关键流程

1. ``main.run()`` 加载 .env 环境变量后调用 ``workflow.base_workflow.start()``
2. ``base_workflow`` 构建 StateGraph（6 个节点，含入口 compact 消息压缩节点）：
   - ``agent``：模型生成回复、决定是否调用工具
   - ``safety_check``：对工具调用进行 LLM 安全审查（返回 SafetyCheckResult）
   - ``pend_approval``：高危操作通过 ``interrupt()`` 挂起，等待人工审批
   - ``tools``：ToolNode 执行工具（ALL_TOOLS 注册表）
   - ``dynamic_diff_node``：工具执行后基于用户意图生成 diff 变更报告（dynamic_shell_diff_node）
   - 条件路由：``route_agent``（普通对话结束 / 工具调用进安全审查）、``route_safety_command``（需审批挂起 / 安全直执行 / 拒绝结束）、``route_approval``（审批通过执行工具 / 拒绝回 agent）、``route_check_mutation``（工具执行后基于用户意图判断是否进入 diff 节点）
3. 工具执行后由 ``route_check_mutation`` 基于用户意图（diff/变更/git status 等关键词）决定是否触发动态 Diff：
   - 用户有查看变更意图 -> ``dynamic_diff_node`` 通过原生 git diff/status 生成变更报告（作为独立 HumanMessage 注入，避免污染原 ToolMessage 结构），再回到 agent
   - 用户无查看变更意图 -> 直接回到 agent 循环

4. 高风险操作通过 ``interrupt()`` 挂起，外部 CLI 使用 ``Command(resume=...)`` 传入 ``approved`` 或 ``rejected`` 恢复：
   - 批准 -> ``node_status=normal``，继续执行工具并回到 agent 循环
   - 拒绝 -> 生成 ToolMessage 拦截该操作，``node_status=finished`` 结束本轮
5. 模型由 ``deepseek_model.get_model_by_name`` 创建（支持 deepseek-chat 等）
6. 对话消息通过 PostgreSQL Checkpointer 持久化，支持断点续跑（thread_id 恢复上下文）
7. 本文件 ``struct.md`` 会被 ``base_workflow.load_system_prompt()`` 读取作为系统提示词，帮助 AI 理解项目结构
8. ``base_workflow.start()`` 启动会话时依次注入三条 SystemMessage：``sys_vibe_coding_agent()``（Vibe Coding 编程助手提示词）→ ``load_system_prompt()`` 读取的 ``struct.md`` → ``sys_env_shell_prompt(work_dir)``（跨平台 Shell 提示词），强化平台适配与工作区越权拦截

## 安全机制

- 工具调用前由 ``safety_check`` 节点审查，高风险、耗时操作进入人工审批（``pend_approval`` 节点 interrupt 挂起）
- ``wraps.workspace_security`` 的 ``enforce_workspace_security`` 装饰器包装 shell 工具，拦截 cwd 越权与 command 中的绝对路径越权
- 所有节点由 ``wraps.monitor`` 的 ``monitor_node`` 装饰器监控，记录节点耗时与成败日志
- ``sys_env_prompt.sys_env_shell_prompt(work_dir)`` 在提示词层面对模型进行跨平台适配引导与工作区越权约束，与 ``workspace_security`` 装饰器形成双层防线
- ``route_check_mutation`` 基于用户输入中的 diff/变更意图关键词触发动态 Diff，无相关意图时跳过节点，减少无谓工具调用
- ``show_diff_tool.py`` 拉起 IDEA diff 对比文件与 Git HEAD，临时 HEAD 文件存于 .vcl/.idea_diff，程序退出时自动清理
