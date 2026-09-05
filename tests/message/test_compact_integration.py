# Node + graph integration tests for message compaction (T13-T22).
from __future__ import annotations

from langgraph.graph import END, StateGraph
from langchain_core.messages import RemoveMessage

from conftest import dialog_rounds, full_state, make_msg
from vibe_cli.model.deepseek_model import AgentState
from vibe_cli.workflow.base_workflow import call_model, compact_node


# Minimal graph that mirrors the real entry chain: compact -> agent -> END.
def build_mini_graph():
    wf = StateGraph(AgentState)
    wf.add_node('compact', compact_node)
    wf.add_node('agent', call_model)
    wf.set_entry_point('compact')
    wf.add_edge('compact', 'agent')
    wf.add_edge('agent', END)
    return wf.compile()


# ---- T13: cold start with empty messages ----
def test_t13_cold_start_empty_state():
    assert compact_node({'messages': []}) == {}


# ---- T14: below threshold is a no-op ----
def test_t14_below_threshold_noop(monkeypatch):
    monkeypatch.setenv('compress_threshold_chars', '12000')
    monkeypatch.setenv('compress_keep_last', '12')
    state = {'messages': dialog_rounds(2, 5)}
    assert compact_node(state) == {}


# ---- T15: compaction removes expected ids, keeps system ----
def test_t15_compaction_rebuilds_tail_keeps_system(fake_model_factory, monkeypatch):
    monkeypatch.setenv("compress_threshold_chars", "100")
    monkeypatch.setenv("compress_keep_last", "4")
    model = fake_model_factory(reply="sum")
    head = [make_msg("system", content="s", mid="s0"), make_msg("system", content="s", mid="s1")]
    out = compact_node({"messages": head + dialog_rounds(4, 20)})
    updates = out["messages"]
    removed = {u.id for u in updates if isinstance(u, RemoveMessage)}
    assert removed == {"h0", "a0", "h1", "a1", "h2", "a2", "h3", "a3"}
    assert not ({"s0", "s1"} & removed)
    added = [u for u in updates if not isinstance(u, RemoveMessage)]
    assert len(added) == 5
    assert added[0].type == "human" and "sum" in added[0].content
    assert [m.content for m in added[1:]] == ["Q2" * 20, "A2" * 20, "Q3" * 20, "A3" * 20]
    assert len(model.calls) == 1


# ---- T16: failed summary = safe no-op ----
def test_t16_failed_summary_is_safe_noop(fake_model_factory, monkeypatch):
    monkeypatch.setenv('compress_threshold_chars', '100')
    monkeypatch.setenv('compress_keep_last', '4')
    msgs = dialog_rounds(4, 20)
    fake_model_factory(reply='x', raise_exc=True)
    assert compact_node({'messages': msgs}) == {}
    fake_model_factory(reply='')
    assert compact_node({'messages': msgs}) == {}


# ---- T17: env overrides steer compaction ----
def test_t17_env_overrides_control_compaction(fake_model_factory, monkeypatch):
    monkeypatch.delenv("compress_threshold_chars", raising=False)
    monkeypatch.delenv("compress_keep_last", raising=False)
    msgs = dialog_rounds(3, 20)
    assert compact_node({"messages": msgs}) == {}
    fake_model_factory(reply="sum")
    monkeypatch.setenv("compress_threshold_chars", "50")
    monkeypatch.setenv("compress_keep_last", "2")
    out = compact_node({"messages": msgs})
    updates = out["messages"]
    removed = [u for u in updates if isinstance(u, RemoveMessage)]
    added = [u for u in updates if not isinstance(u, RemoveMessage)]
    assert len(removed) == 6
    assert len(added) == 3
    assert added[0].type == "human"
    assert [m.content for m in added[1:]] == ["Q2" * 20, "A2" * 20]


# ---- T18: kept count matches keep_last ----
def test_t18_kept_tail_matches_keep_last(fake_model_factory, monkeypatch):
    monkeypatch.setenv("compress_threshold_chars", "100")
    monkeypatch.setenv("compress_keep_last", "4")
    fake_model_factory(reply="sum")
    out = compact_node({"messages": dialog_rounds(4, 20)})
    updates = out["messages"]
    removed = [u for u in updates if isinstance(u, RemoveMessage)]
    added = [u for u in updates if not isinstance(u, RemoveMessage)]
    assert len(removed) == 8
    assert len(added) == 5
    assert [m.content for m in added[1:]] == ["Q2" * 20, "A2" * 20, "Q3" * 20, "A3" * 20]

# ---- T19: graph entry, no summary injection on first round ----
def test_t19_graph_entry_no_summary_injection(fake_model_factory, monkeypatch):
    monkeypatch.setenv('compress_threshold_chars', '12000')
    monkeypatch.setenv('compress_keep_last', '12')
    model = fake_model_factory(reply='ok')
    app = build_mini_graph()
    out = app.invoke(full_state(dialog_rounds(1, 5)))
    assert out['messages'][-1].content == 'ok'
    assert len(model.calls) == 1
    assert model.calls[0][0].type == 'human'


# ---- T20: rolling summary injected after compaction ----
def test_t20_summary_physically_in_messages_after_compaction(fake_model_factory, monkeypatch):
    monkeypatch.setenv("compress_threshold_chars", "100")
    monkeypatch.setenv("compress_keep_last", "4")
    model = fake_model_factory(reply="rolling summary")
    app = build_mini_graph()
    out = app.invoke(full_state(dialog_rounds(4, 20)))
    assert out["messages"][0].type == "human"
    assert "rolling summary" in out["messages"][0].content
    assert [m.content for m in out["messages"][1:5]] == ["Q2" * 20, "A2" * 20, "Q3" * 20, "A3" * 20]
    assert len(out["messages"]) == 6
    sent = model.calls[-1]
    assert sent[0].type == "human"
    assert "rolling summary" in sent[0].content

# ---- T21: context shrinks after a graph cycle ----
def test_t21_context_shrinks_after_cycle(fake_model_factory, monkeypatch):
    monkeypatch.setenv('compress_threshold_chars', '100')
    monkeypatch.setenv('compress_keep_last', '4')
    fake_model_factory(reply='s')
    app = build_mini_graph()
    msgs = dialog_rounds(4, 20)
    out = app.invoke(full_state(msgs))
    in_size = sum(len(str(m.content)) for m in msgs if m.type != 'system')
    out_size = sum(len(str(m.content)) for m in out['messages'] if m.type != 'system')
    assert len(out['messages']) < len(msgs)
    assert out_size < in_size
    assert out["messages"][0].type == "human" and str(out["messages"][0].content or "").startswith("【历史摘要】")


# ---- T22: diff-loop re-entry preserves accumulated summary ----
def test_t22_diff_loop_reentry_keeps_summary(fake_model_factory, monkeypatch):
    monkeypatch.setenv('compress_threshold_chars', '100')
    monkeypatch.setenv('compress_keep_last', '4')
    model = fake_model_factory(reply='S1')
    app = build_mini_graph()
    first = app.invoke(full_state(dialog_rounds(4, 20)))
    assert first['messages'][0].type == 'human' and 'S1' in first['messages'][0].content
    second = app.invoke(full_state(first['messages']))
    assert second['messages'][0].type == 'human' and 'S1' in second['messages'][0].content
    assert len(model.calls) == 4
    assert model.calls[-1][0].type == 'human'
    assert 'S1' in model.calls[-1][0].content