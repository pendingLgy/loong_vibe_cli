import os
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Confirm

from vibe_cli.core.load_sys_prompt import sync_system_prompts
from vibe_cli.core.task_resume import task_resume
from vibe_cli.env.logger_config import logger
from vibe_cli.env.workspace import get_work_dirs, get_primary_work_dir
from vibe_cli.message.message_check import check_message_completed
from vibe_cli.skill.loading_skill import SkillRegistry
from vibe_cli.workflow.node_graph import build_vibe_app


def create_workflow_img(app: CompiledStateGraph[Any, Any, Any, Any], work_dir: str):
    try:
        # 1. 获取工作目录，如果没有设置则默认使用当前运行目录
        target_dir = get_primary_work_dir(work_dir)
        output_path = Path(target_dir).joinpath("vibe_agent_workflow.png")

        # 2. 生成并保存图片
        png_data = app.get_graph().draw_mermaid_png()
        with open(output_path, "wb") as f:
            f.write(png_data)

        logger.success(f"[green]✅ 已成功生成流程图图片: {output_path}[/green]")
    except Exception as e:
        logger.warning(f"生成 PNG 流程图失败: {e}")


# ==================== 终端 REPL 交互主循环 ====================

def start():
    work_dir = os.getenv("work_dir")
    model_provider = os.getenv("model_provider")
    security_opt_skip = bool(os.getenv("security_opt_skip",False))
    compress_threshold_chars = int(os.getenv("compress_threshold_chars", "1200000"))
    compress_keep_last_turns = int(os.getenv("compress_keep_last_turns", "3"))
    max_compressive_messages = int(os.getenv("max_compressive_messages", "10"))

    required_fields = {
        "work_dir": work_dir,
        "model_provider": model_provider,
    }

    missing_fields = [
        name for name, value in required_fields.items()
        if value is None or str(value).strip() == ""
    ]

    if missing_fields:
        raise ValueError(
            f"以下环境变量不能为空: {', '.join(missing_fields)}"
        )

    # 例如在环境变量和 work_dir 准备好之后：
    skills_dir = get_primary_work_dir(work_dir).joinpath(".vcl", "skills")
    skill_registry = SkillRegistry(skills_dir)

    config = {
        "configurable": {
            # "thread_id": str(uuid.uuid4()),
            "thread_id": "123061",
            "skill_registry": skill_registry,
            "security_opt_skip": security_opt_skip, # 跳过安全限制
            "compress_threshold_chars": compress_threshold_chars, # 消息压缩触发阈值
            "compress_keep_last_turns": compress_keep_last_turns, # 消息压缩保留轮次
            "max_compressive_messages": max_compressive_messages,
        }
    }

    console = Console()

    app = build_vibe_app()

    task_resume(app, config, console)
    # create_workflow_img(app, work_dir)
    sync_system_prompts(app, config, skill_registry, [str(p) for p in get_work_dirs(work_dir)])
    check_message_completed(app, config)

    console.print(Panel.fit(
        "🚀 [bold cyan]Local Vibe Coding Assistant (Official Interrupt)[/bold cyan]\n输入你的需求，输入 exit 退出。",
        border_style="cyan"))

    while True:
        try:
            user_input = console.input("\n[bold green]👤 You > [/bold green]")
            if user_input.strip().lower() in ["exit", "quit"]:
                console.print("[yellow]再见！保持 Vibe 状态 🚀[/yellow]")
                break

            if not user_input.strip():
                continue

            # 🧩 新增：拦截 /skill 或 /skills 快捷指令
            cleaned_input = user_input.strip().lower()
            if cleaned_input in ["/skill", "/skills"]:
                index_content = skill_registry.index_content
                if not index_content:
                    console.print(
                        "[yellow]⚠️ 当前工作区暂未发现已注册的技能清单（skills/skill_index.md 不存在或为空）。[/yellow]")
                else:
                    console.print(
                        Panel(Markdown(f"### 🧩 当前已注册的技能清单\n\n{index_content}"), border_style="cyan"))
                continue  # 拦截成功后直接进入下一轮循环，不走后续的大模型 Agent 流程

            # 初始输入流或恢复流
            stream_input = {
                "model_provider": model_provider,
                "work_dir": work_dir,
                "messages": [HumanMessage(content=user_input)]
            }

            while True:

                # 1. 异步唤起并获取 v3 AsyncGraphRunStream 控制器
                stream = app.stream_events(input=stream_input, config=config, version="v3")

                console.print("\n[bold green]🤖 Agent 响应中...[/bold green]")

                # -------------------------------------------------------------
                # 2. 消费 stream.messages 投影：极简打字机（无需解包底层的 event 结构）
                # -------------------------------------------------------------
                for message in stream.messages:
                    node = message.node
                    if node != 'agent':
                        continue

                    for text in message.text:
                        console.print(text, end="")

                    console.print()  # 换行

                # -------------------------------------------------------------
                # 3. 中断处理 (Human-in-the-Loop)
                # -------------------------------------------------------------
                if stream.interrupted:
                    console.print("\n[bold yellow]⏸️ 触发安全拦截，等待用户授权...[/bold yellow]")

                    # 从 stream.interrupts 提取具体的审批 Payload
                    for interrupt_payload in stream.interrupts:
                        console.print(
                            Panel(
                                f"⚠️ [bold yellow]敏感操作需要确认：[/bold yellow]\n{interrupt_payload.value}",
                                title="人机协作审批 (HITL)",
                                border_style="yellow",
                            )
                        )

                    # 终端交互确认
                    if Confirm.ask("是否批准 Agent 继续执行此操作？", default=True):
                        console.print("[green]✅ 已批准，恢复 Agent 执行...[/green]")
                        # 传入 Command 响应恢复信号
                        stream_input = Command(resume="approved")
                    else:
                        console.print("[red]❌ 已拒绝操作，中断流程。[/red]")
                        stream_input = Command(resume="rejected")

                    # 继续下一次 while 循环以推进恢复后的图任务
                    continue
                # 没有被中断，说明全部流程已经执行完毕，退出控制循环
                break


        except KeyboardInterrupt:
            console.print("\n[yellow]操作已中断。[/yellow]")
            break
        except Exception as e:
            import traceback
            console.print(f"[bold red]❌ 发生错误: {str(e)}[/bold red]")
            logger.critical(f"[dim red]{traceback.format_exc()}[/dim red]")
