# vibe-cli 项目结构

基于 LangChain、LangGraph 的 AI 命令行助手，可调用 shell 工具执行命令，并通过安全审查机制拦截高风险操作。

技术栈：Python 3.13、uv、langchain、langgraph、loguru、rich、pydantic、PostgreSQL (psycopg)

## 目录结构

```text
vibe-cli\
├─ pyproject.toml              # 项目配置(uv): 依赖、命令行入口 vibe-cli = vibe_cli:main
├─ README.md                   # 项目说明
├─ .gitignore                  # 忽略 __pycache__、.venv、.idea、logs、.coverage 等
├─ .python-version             # Python 3.13
├─ uv.lock                     # uv 依赖锁文件
├─ .env                        # 环境变量(API Key、database_url 等)
├─ tests\                      # pytest 测试(消息压缩单元测试 + 图级集成测试, 共 26 例)
├─ src\
   ├─ vibe_cli\                # 主包
      ├─ main.py               # 程序入口: 加载 .env 配置、同步启动 workflow(REPL)
      ├─ __init__.py           # 命令行脚本入口, 暴露 main()
      |
      ├─ env\                  # 运行环境基础设施
      |   ├─ check_point_memory.py   # PostgreSQL Checkpointer(同步 PostgresSaver 单例、ConnectionPool、断点恢复、close_postgres_memory 关闭)
      |   ├─ logger_config.py        # loguru 日志配置(控制台 + 文件, 目录可配置)
      |   ├─ shutdown.py              # 优雅关闭: atexit 注册 + SIGINT、SIGTERM 信号捕获, 关闭 PG 连接池并清理临时文件
      |   ├─ __init__.py
      |
      ├─ message\              # 消息压缩模块
      |   ├─ message_compactor.py   # 纯函数压缩核心: total_size、should_compress、safe_cutoff、render_history、plan_compaction、summarize_with_model(无 2000 硬截断)
      |   ├─ __init__.py
      |
      ├─ model\                # 模型与状态定义
      |   ├─ deepseek_model.py      # AgentState + NodeStatus + SafetyCheckResult + 模型工厂 get_model_by_name
      |   ├─ __init__.py
      |
      ├─ prompt\               # 提示词模板
      |   ├─ sys_safe_check_prompt.py   # 工具调用安全审查提示词(高风险、耗时操作判定)
      |   ├─ sys_env_prompt.py           # 跨平台 Shell 提示词 + Vibe Coding 提示词
      |   ├─ sys_summarize_prompt.py     # 消息压缩摘要提示词
      |   ├─ __init__.py
      |
      ├─ skill\                 # 专业技能注册
      |   ├─ loading_skill.py        # SkillRegistry(SKILL.md 扫描 + 注册表管理)
      |   ├─ __init__.py
      |
      ├─ tools\                # Agent 可用工具
      |   ├─ shell_tool.py          # shell 命令执行工具(多编码解码、失败重试、30s 超时、日志记录实际生效目录)
      |   ├─ show_diff_tool.py      # IDEA Diff 工具(拉起 IDEA 对比文件与 Git HEAD, 临时文件存 .vcl 下 .idea_diff)
      |   ├─ load_skills_tool.py    # 技能加载工具(按需读取 SKILL.md, 支持重试)
      |   ├─ __init__.py            # ALL_TOOLS 工具注册表
      |
      ├─ wraps\                # 装饰器与安全包装层
      |   ├─ monitor.py             # LangGraph 节点监控装饰器(记录耗时与成败日志)
      |   ├─ workspace_security.py  # 工作区安全装饰器(拦截 cwd 越权与 command 绝对路径越权)
      |   ├─ __init__.py
      |
      ├─ workflow\             # LangGraph 工作流
         ├─ base_workflow.py      # 同步核心图: 6 节点(compact 入口、agent、safety_check、tools、dynamic_diff_node) + 条件路由 + 人工审批中断(stream_events v3 HITL) + 动态 Diff + 消息压缩 + 读取 struct.md 作为系统提示
         ├─ __init__.py
```

## 关键流程

1. main.run() 加载 .env 环境变量后同步调用 workflow.base_workflow.start()
2. base_workflow 构建 StateGraph(6 个节点, 入口为 compact 消息压缩节点):
   - compact: 入口节点, 按需执行消息压缩(plan_compaction + summarize_with_model)
   - agent: 模型生成回复、决定是否调用工具
   - safety_check: 对工具调用进行 LLM 安全审查(返回 SafetyCheckResult)
   - pend_approval: 高危操作通过 interrupt() 挂起, 等待人工审批
   - tools: ToolNode 执行工具(ALL_TOOLS 注册表)
   - dynamic_diff_node: 工具执行后基于用户意图生成 diff 变更报告
3. 入口 compact 仅负责压缩, 不注入摘要消息; 工具执行完成后回到 agent 循环
4. 消息压缩流程: safe_cutoff 计算起止绝对索引 -> 旧摘要折叠成 old_summary -> summarize_with_model 生成新摘要 -> RemoveMessage 删除旧消息 + HumanMessage(summary-* 书签) + keep-* 克隆重建保留尾部
5. 工具执行后由 route_check_mutation 基于用户意图(diff、变更、git status 等关键词)决定是否触发动态 Diff
6. 高风险操作通过 interrupt() 挂起; REPL 由 stream_events(v3) 驱动打字机流, 检测到 stream.interrupted 时读取 stream.interrupts 展示审批信息, 用户确认后以 Command(resume=approved|rejected) 恢复
7. 模型由 deepseek_model.get_model_by_name 创建(支持 deepseek-chat 等)
8. 对话消息通过同步 PostgreSQL Checkpointer(单例 ConnectionPool)持久化, 支持断点续跑(thread_id 恢复上下文)
9. 本文件 struct.md 会被 base_workflow.load_system_struct_prompt() 读取作为系统提示词, 帮助 AI 理解项目结构

## 安全机制

- 工具调用前由 safety_check 节点审查, 高风险、耗时操作进入人工审批(pend_approval 节点 interrupt 挂起)
- wraps.workspace_security 的 enforce_workspace_security 装饰器包装 shell 工具, 拦截 cwd 越权与 command 中的绝对路径越权
- 所有节点由 wraps.monitor 的 monitor_node 装饰器监控, 记录节点耗时与成败日志
- sys_env_prompt.sys_env_shell_prompt(work_dir) 在提示词层面对模型进行跨平台适配引导与工作区越权约束, 与 workspace_security 装饰器形成双层防线
- route_check_mutation 基于用户输入中的 diff、变更意图关键词触发动态 Diff, 无相关意图时跳过节点, 减少无谓工具调用
- show_diff_tool.py 拉起 IDEA diff 对比文件与 Git HEAD, 临时 HEAD 文件存于 .vcl 下 .idea_diff, 程序退出时自动清理 (shutdown.py 注册 atexit + 信号捕获)
