import pytest
from unittest.mock import MagicMock, patch
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage, RemoveMessage

from vibe_cli.message.message_compactor import (
    total_size,
    split_into_turns,
    should_compress,
    safe_cutoff,
    render_history,
    plan_compaction,
    summarize_with_model, is_summary_message,
)
from vibe_cli.workflow.node_graph import SUMMARY_BOOKMARK_PREFIX, compact_msg_node



def test_total_size():
    """测试可压缩字符总量统计（自动跳过 SystemMessage 和不可压缩项）"""
    messages = [
        SystemMessage(content="system prompt 100 chars" * 10),
        HumanMessage(content="Hello"),  # 5 chars
        AIMessage(content="World!"),  # 6 chars
    ]
    assert total_size(messages) == 11


def test_total_size_edge_cases():
    """测试 total_size 统计字符量时的空值与异常规避"""
    messages = [
        SystemMessage(content="System prompt that should be ignored"),
        AIMessage(content="12345"),  # 5 字符
        ToolMessage(tool_call_id="tc-1", content="123"),  # 3 字符
    ]
    assert total_size(messages) == 8


def test_split_into_turns_closed_interval():
    """测试按非摘要 HumanMessage 拆分对话轮次（闭区间 [start, end]）"""
    messages = [
        SystemMessage(content="Sys"),  # index 0
        HumanMessage(id="h1", content="T1"),  # index 1 (Turn 1 start)
        AIMessage(content="R1"),  # index 2
        HumanMessage(id="h2", content="T2"),  # index 3 (Turn 2 start)
        AIMessage(content="R2"),  # index 4 (Turn 2 end)
    ]
    turns = split_into_turns(messages)
    assert len(turns) == 2
    assert turns[0] == (1, 2)
    assert turns[1] == (3, 4)


def test_should_compress_triggers():
    """测试双门控及溢出触发条件"""
    assert should_compress([], keep_last=3) is False
    assert should_compress([HumanMessage(content="hi")] * 3, keep_last=3) is False

    # 字符超标触发 (normal_trigger)
    long_messages = [HumanMessage(content="a" * 600)] * 5
    assert should_compress(long_messages, keep_last=2, threshold_chars=1000) is True

    # 条数达到 max_compressive_messages 溢出强触发
    overflow_messages = [HumanMessage(content="hi")] * 6
    assert should_compress(overflow_messages, keep_last=2, threshold_chars=99999) is False


def test_render_history_formatting_and_types():
    """测试 render_history 各种消息类型的格式化输出及工具调用预览"""
    messages = [
        HumanMessage(content="Hello"),
        AIMessage(content="Hi there"),
        SystemMessage(content="Sys prompt"),  # 应被跳过
        ToolMessage(tool_call_id="tc-1", content="Tool result data"),
        AIMessage(content="", tool_calls=[{"id": "call_db_1", "name": "get_db_connections", "args": {"env": "prod"}}]),
        HumanMessage(content="Secret", additional_kwargs={"compressible": False})
    ]

    rendered = render_history(messages)
    assert "USER: Hello" in rendered
    assert "AI: Hi there" in rendered
    assert "TOOL: Tool result data" in rendered
    assert "[tool calls]" in rendered
    assert "SYSTEM" in rendered
    assert "Secret" in rendered


def test_summarize_with_model_safe_skip():
    """测试大模型调用失败时的安全跳过契约 (Safe-Skip)"""
    mock_model = MagicMock()
    mock_model.invoke.side_effect = Exception("API Connection Error")

    result = summarize_with_model(mock_model, "previous summary", [HumanMessage(content="test")])
    assert result == ""


def test_summarize_with_model_success_and_parsing():
    """测试 summarize_with_model 正常调用以及大模型返回多模态/列表块内容时的文本提取"""
    mock_model = MagicMock()
    mock_response = AIMessage(content=["Summary part 1,", "Summary part 2."])
    mock_model.invoke.return_value = mock_response

    history = [HumanMessage(content="history text")]
    result = summarize_with_model(mock_model, "old summary", history)

    assert "Summary part 1, Summary part 2." in result
    mock_model.invoke.assert_called_once()


def test_extremely_complex_rolling_summary_pipeline():
    """测试极端复杂的长对话链条：包含全局SystemMessage、已存在的滚动摘要、Tool调用链交互、多轮交织以及增量/全量裁剪策略。"""
    messages = [
        # 0: 全局系统提示词（永不压缩）
        SystemMessage(id="sys-global", content="System: You are an advanced AI assistant."),

        # 1: 历史滚动摘要（模拟第 1-2 轮被压缩过的内容）
        HumanMessage(
            id="summary-history-001",
            content=SUMMARY_BOOKMARK_PREFIX + "Initial summary: User asked about server setup and configurations.",
            additional_kwargs={"is_summary": True}
        ),

        # --- 轮次 3 (应被纳入本轮增量压缩) ---
        HumanMessage(id="h-3", content="Please check the active database connections."),
        AIMessage(
            id="ai-3",
            content="",
            tool_calls=[{"id": "call_db_1", "name": "get_db_connections", "args": {"env": "prod"}}]
        ),
        ToolMessage(id="tm-3", tool_call_id="call_db_1", content="Active connections: 42"),
        AIMessage(id="ai-3-resp", content="The production environment currently has 42 active connections."),

        # --- 轮次 4 (应被纳入本轮增量压缩) ---
        HumanMessage(id="h-4", content="Can you analyze if this is normal?"),
        AIMessage(id="ai-4", content="Yes, 42 is well within the normal operational threshold of 100."),

        # --- 轮次 5 (最近保留轮次 keep_last_turns = 2) ---
        HumanMessage(id="h-5", content="Great. Let's look at the memory usage instead."),
        AIMessage(id="ai-5", content="Sure, fetching memory metrics now."),

        # --- 轮次 6 (最近保留轮次 keep_last_turns = 2) ---
        HumanMessage(id="h-6", content="Thanks for your help today."),
        AIMessage(id="ai-6", content="You're very welcome! Let me know if you need anything else."),
    ]

    # 1. 验证 split_into_turns 是否能正确识别出 4 个闭区间轮次
    turns = split_into_turns(messages)
    assert len(turns) == 4
    assert turns[0] == (2, 5)
    assert turns[1] == (6, 7)
    assert turns[2] == (8, 9)
    assert turns[3] == (10, 11)

    # 2. 验证 safe_cutoff 的增量裁剪行为
    cutoff_res = safe_cutoff(messages, keep_last=2, max_compressive_messages=50)
    assert cutoff_res is not None
    start_idx, cutoff = cutoff_res
    assert start_idx == 2
    assert cutoff == 7

    # 3. 验证 plan_compaction 拿到的历史原文过滤掉了 SystemMessage 且包含 ToolMessage
    plan = plan_compaction(messages, keep_last_turns=2, threshold_chars=1, max_compressive_messages=50)
    assert plan is not None
    p_start, p_cutoff, history = plan
    assert p_start == 2
    assert p_cutoff == 7
    assert not any(isinstance(m, SystemMessage) for m in history)
    assert any(isinstance(m, ToolMessage) for m in history)

    # 4. 验证整个 LangGraph 节点（compact_msg_node）在复杂链条下的集成输出
    state = {
        "messages": messages,
        "model_provider": "mock_provider"
    }
    config = {
        "configurable": {
            "compress_threshold_chars": 1,
            "compress_keep_last_turns": 2,
            "max_compressive_messages": 50
        }
    }

    mock_model = MagicMock()
    mock_model.invoke.return_value = AIMessage(
        content="Updated summary: Checked DB connections (42 active) and confirmed normal status.")

    with patch("vibe_cli.workflow.node_graph.get_model", return_value=mock_model):
        result = compact_msg_node(state, config)

    assert "messages" in result
    res_msgs = result["messages"]

    removals = [m for m in res_msgs if isinstance(m, RemoveMessage)]
    assert len(removals) > 0

    summary_msgs = [m for m in res_msgs if is_summary_message(m)]
    assert len(summary_msgs) == 1
    assert SUMMARY_BOOKMARK_PREFIX in summary_msgs[0].content
    assert "42 active" in summary_msgs[0].content

    kept_rebuilds = [m for m in res_msgs if getattr(m, "id", "").startswith("keep-")]
    assert len(kept_rebuilds) == 4


def test_default_multiple_initial_summaries():
    """测试默认初始化多条（例如三条）历史摘要的场景。

    验证系统能否将多条预设摘要安全提取、合并，并参与后续的滚动归纳与清理。
    """
    messages = [
        SystemMessage(id="sys-0", content="System Prompt"),

        # 默认初始化三条历史摘要
        HumanMessage(id="summary-init-1", content=SUMMARY_BOOKMARK_PREFIX + "Initial Summary Part 1: Project goals.",
                     additional_kwargs={"is_summary": True}),
        HumanMessage(id="summary-init-2",
                     content=SUMMARY_BOOKMARK_PREFIX + "Initial Summary Part 2: Tech stack choices.",
                     additional_kwargs={"is_summary": True}),
        HumanMessage(id="summary-init-3",
                     content=SUMMARY_BOOKMARK_PREFIX + "Initial Summary Part 3: Architecture design.",
                     additional_kwargs={"is_summary": True}),

        # 新产生的对话轮次（触发压缩）
        HumanMessage(id="h-1", content="Let's implement the compaction node."),
        AIMessage(id="ai-1", content="Sure, let's write it down."),
        HumanMessage(id="h-2", content="Make sure it handles edge cases."),
        AIMessage(id="ai-2", content="Understood, edge cases covered."),
    ]

    state = {
        "messages": messages,
        "model_provider": "mock_provider"
    }
    config = {
        "configurable": {
            "compress_threshold_chars": 1,  # 极低阈值确保触发
            "compress_keep_last_turns": 1,  # 保留最后一轮 (h-2)
            "max_compressive_messages": 2
        }
    }

    mock_model = MagicMock()
    # 模拟大模型将三条旧摘要与新历史成功融合成一条最新大摘要
    mock_model.invoke.return_value = AIMessage(content="Unified summary of initial parts 1-3 plus recent compaction.")

    with patch("vibe_cli.workflow.node_graph.get_model", return_value=mock_model):
        result = compact_msg_node(state, config)

    assert "messages" in result
    res_msgs = result["messages"]

    # 1. 验证三条初始旧摘要是否全部被放入 removals 中进行清理
    removals = [m for m in res_msgs if isinstance(m, RemoveMessage)]
    removed_ids = {r.id for r in removals}
    assert "summary-init-1" in removed_ids
    assert "summary-init-2" in removed_ids
    assert "summary-init-3" in removed_ids

    # 2. 验证是否生成了唯一一条合并后的新摘要
    summary_msgs = [m for m in res_msgs if is_summary_message(m)]
    assert len(summary_msgs) == 1
    assert SUMMARY_BOOKMARK_PREFIX in summary_msgs[0].content
    assert "Unified summary" in summary_msgs[0].content