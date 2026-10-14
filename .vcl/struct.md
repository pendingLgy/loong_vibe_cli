# vibe-cli 项目结构

> 文档版本: **v1.7.0** · 每次更新本文件内容时须同步递增版本号: 结构性变更或新增章节时次版本加1, 局部修改或勘误时修订号加1

基于 LangChain、LangGraph 的 AI 命令行助手，可调用 shell 工具执行命令，并通过安全审查机制拦截高风险操作。

技术栈：Python 3.13、uv、langchain、langgraph、loguru、rich、pydantic、PostgreSQL (psycopg + psycopg_pool)

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
├─ scripts\                    # 本地调试脚本(inspect_checkpoint.py 经函数参数查 PostgreSQL checkpoint 四表, 按 checkpoint_id 与 version 降序, msgpack 自动解码, 不集成进 Agent 工具链)
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
      |   ├─ load_sys_prompt.py     # 系统提示注入: SYS_ID_* 固定 ID、_load_system_struct_prompt(primary_dir) 读取主工作区 .vcl\struct.md、_init_sys_prompt、sync_system_prompts(update_state 幂等覆盖)
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

## 关键流程

1. main.run() 加载 .env 环境变量后同步调用 workflow.base_workflow.start()
2. base_workflow.start() 构建 REPL: 创建 app = node_graph.build_vibe_app()、初始化 SkillRegistry、sync_system_prompts 注入系统提示、task_resume 恢复任务、check_message_completed 清理未完成消息
3. node_graph.build_vibe_app() 构建 StateGraph(6 个显式节点, 入口 compact_msg, ToolNode 包装 ALL_TOOLS):
   - compact_msg: 入口节点, 按轮次执行消息压缩(plan_compaction + summarize_with_model)
   - agent: call_model 节点, 模型生成回复、决定是否调用工具(route_agent: 无工具调用时直接结束)
   - safety_check: 对工具调用进行 LLM 安全审查(解析 JSON: is_dangerous、reason、file)
   - pend_approval: 高危操作通过 interrupt() 挂起, 等待人工审批; 用户拒绝时注入 ToolMessage 通知模型取消
   - tools: ToolNode 执行工具(ALL_TOOLS 注册表)
   - dynamic_diff_node: 定义保留但未接入(对应 tools 条件连边处于注释状态)
4. 消息压缩流程(按轮次): split_into_turns 切分轮次 -> should_compress 与 safe_cutoff 判定 -> plan_compaction 返回 (start_idx、cutoff、history) -> summarize_with_model 折叠旧摘要与历史 -> compact_msg 节点以 RemoveMessage 删除从 start_idx 起的全部旧消息, 再追加 HumanMessage(summary 前缀书签) + keep 前缀克隆重建保留尾部; 摘要生成失败时安全跳过
5. 工具执行完成后回到 agent 循环(workflow.add_edge(tools, agent)); route_check_mutation 与 dynamic Diff 目前未启用
6. 高危操作通过 interrupt() 挂起; REPL 由 stream_events(v3) 驱动打字机流, 检测到 stream.interrupted 时读取 stream.interrupts 展示审批信息, 用户经 Confirm 确认后以 Command(resume=approved、rejected) 恢复
7. 模型由 model_factory.get_model(provider=state.model_provider) 创建; 模型厂商由环境变量 model_provider 指定(openai 或 deepseek)
8. 对话消息通过同步 PostgreSQL Checkpointer(单例 ConnectionPool)持久化, 支持断点续跑(thread_id 恢复上下文)
9. 系统提示注入: start() 每次启动都会调用 core.load_sys_prompt.sync_system_prompts(), 将 3 条固定 ID 的 SystemMessage(sys-vibe-coding、sys-struct、sys-skill-index, 均标记 compressible=False) 经 app.update_state 幂等覆盖或追加到状态, 内容来自 sys_env_prompt、主工作区 .vcl\struct.md、.vcl\skills\skill_index.md
10. 本文件 .vcl\struct.md 会被 core.load_sys_prompt 模块的 _load_system_struct_prompt(primary_dir) 读取作为系统提示词, 帮助 AI 理解项目结构; start() 启动时还会生成 vibe_agent_workflow.png 流程图
11. REPL 内置技能清单快捷指令, 可跳过 Agent 直接展示 .vcl\skills\skill_index.md 中的已注册技能内容

## 安全机制

- 工具调用前由 safety_check 节点审查, 高风险、耗时操作进入人工审批(pend_approval 节点 interrupt 挂起)
- wraps.workspace_security 的 enforce_workspace_security 装饰器包装 shell 工具, 支持多工作区: cwd 与 command 中的绝对路径须落在任一合法工作区内, 否则拦截并提示全部合法工作区
- 该装饰器在 Windows 下放行行内命令开关(如 dir 的 b、findstr 的 S 等斜杠参数), 避免将开关误判为越权绝对路径, 同时仍校验真正的盘符路径与多段 Unix 绝对路径
- 所有节点由 wraps.monitor 的 monitor_node 装饰器监控, 记录节点耗时与成败日志
- sys_env_prompt.sys_vibe_coding_agent(work_dirs) 在提示词层面对模型进行跨平台适配引导与多工作区越权约束, 与 workspace_security 装饰器形成双层防线
- tools\__init__.py 集中声明 ALL_TOOLS 与 DANGEROUS_TOOLS, 作为工具注册与安全策略扩展的统一入口
- show_diff_tool.py 拉起 IDEA diff 对比文件与 Git HEAD, 临时 HEAD 文件存于 .vcl 下 .idea_diff, 程序退出时自动清理 (shutdown.py 注册 atexit + 信号捕获)

## 多工作区支持 (v1.6.0 起)

- 环境变量 `work_dir` 支持声明多个工作目录, 使用英文逗号或分号分隔; 列表首个为主工作目录 (primary)。
- 解析逻辑集中于 `src\vibe_cli\env\workspace.py` (split_work_dir(raw)、get_work_dirs(work_dir)、get_primary_work_dir(work_dir))。
- 安全校验 `enforce_workspace_security`: cwd 与 command 绝对路径须落在任一合法工作区内, 否则拦截并提示全部合法工作区, 并在 Windows 下放行命令开关。
- `_load_system_struct_prompt` 从主工作区读取 `.vcl\struct.md`。
- 示例: `work_dir='dir_a,dir_b'`。
