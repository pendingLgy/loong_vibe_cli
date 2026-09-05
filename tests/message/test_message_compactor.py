# Pure-function tests for vibe_cli.message.message_compactor (T01-T12、T06b、T06c、T06d、T09b，共 16 例).
from __future__ import annotations

import vibe_cli.prompt.sys_summarize_prompt as sp_module
from conftest import FakeModel, dialog_rounds, make_msg
from vibe_cli.message.message_compactor import (
    plan_compaction,
    render_history,
    safe_cutoff,
    should_compress,
    summarize_with_model,
    total_size,
)


# ---- T01: total_size ignores system messages ----
def test_t01_total_size_ignores_system():
    msgs = [
        make_msg('system', content='s' * 5000, mid='s0'),
        make_msg('human', content='hello', mid='h0'),
        make_msg('ai', content='world', mid='a0'),
        make_msg('tool', content='r', mid='t0'),
    ]
    assert total_size(msgs) == 5 + 5 + 1


# ---- T02: should_compress message-count boundary ----
def test_t02_should_compress_message_count_boundary():
    assert should_compress(dialog_rounds(3, 50), keep_last=4, threshold_chars=1) is True
    assert should_compress(dialog_rounds(2, 50), keep_last=4, threshold_chars=1) is False


# ---- T03: should_compress char threshold ----
def test_t03_should_compress_char_threshold():
    assert should_compress(dialog_rounds(3, 1), keep_last=4, threshold_chars=1000000) is False


# ---- T04: safe_cutoff normal span with system head ----
def test_t04_safe_cutoff_normal_span_with_system_head():
    head = [make_msg('system', content='head', mid='s' + str(i)) for i in range(3)]
    msgs = head + dialog_rounds(15, 1)
    start, cutoff = safe_cutoff(msgs, keep_last=12)
    assert start == 3
    assert msgs[cutoff].type == 'human'
    kept = [m for m in msgs[cutoff:] if m.type != 'system']
    assert len(kept) == 12


# ---- T05: start aligned to first human ----
def test_t05_safe_cutoff_start_at_first_human():
    msgs = [
        make_msg('system', content='s', mid='s0'),
        make_msg('system', content='s', mid='s1'),
        make_msg('system', content='s', mid='s2'),
        make_msg('human', content='q1', mid='h0'),
        make_msg('human', content='q2', mid='h1'),
    ]
    start, cutoff = safe_cutoff(msgs, keep_last=1)
    assert (start, cutoff) == (3, 4)


# ---- T06: tool chain backtrack protection ----
def test_t06_safe_cutoff_toolchain_backtrack():
    msgs = [
        make_msg('human', content='q0', mid='h0'),
        make_msg('ai', content='a0', mid='a0'),
        make_msg('human', content='q1', mid='h1'),
        make_msg('ai', content='a1', mid='a1'),
        make_msg('human', content='q2', mid='h2'),
        make_msg('ai', content='a2', mid='a2'),
        make_msg('human', content='q3', mid='h3'),
        make_msg('tool', content='boom', mid='t3'),
        make_msg('human', content='q4', mid='h4'),
        make_msg('ai', content='a4', mid='a4'),
        make_msg('human', content='q5', mid='h5'),
        make_msg('ai', content='a5', mid='a5'),
    ]
    start, cutoff = safe_cutoff(msgs, keep_last=4)
    assert (start, cutoff) == (0, 8)
    assert msgs[cutoff].type == 'human'

# ---- T07: invalid boundaries ----
def test_t07_safe_cutoff_invalid_boundaries():
    system_only = [make_msg('system', content='s', mid='s' + str(i)) for i in range(5)]
    assert safe_cutoff(system_only, keep_last=1) is None
    ai_only = [make_msg('ai', content='a', mid='a' + str(i)) for i in range(6)]
    assert safe_cutoff(ai_only, keep_last=1) is None
    assert safe_cutoff(dialog_rounds(2, 1), keep_last=12) is None
    tool_before_human = [
        make_msg('tool', content='r', mid='t0'),
        make_msg('human', content='q', mid='h0'),
    ]
    assert safe_cutoff(tool_before_human, keep_last=1) is None


# ---- T08: render_history roles ----
def test_t08_render_history_roles():
    tool_call = [{'name': 'shell', 'args': {'cmd': 'pwd'}, 'id': 'c1', 'type': 'tool_call'}]
    msgs = [
        make_msg('system', content='sys', mid='s0'),
        make_msg('human', content='usr', mid='h0'),
        make_msg('ai', content='', tool_calls=tool_call, mid='a0'),
        make_msg('tool', content='res', mid='t0'),
    ]
    lines = render_history(msgs).split(chr(10) + chr(10))
    assert lines[0] == 'SYSTEM: sys'
    assert lines[1] == 'USER: usr'
    assert lines[2].startswith('AI: [tool calls]')
    assert 'shell' in lines[2]
    assert lines[3] == 'TOOL: res'


# ---- T09: plan_compaction three states ----
def test_t09_plan_compaction_states():
    assert plan_compaction([], keep_last=4, threshold_chars=1) is None
    assert plan_compaction(dialog_rounds(3, 1), keep_last=12, threshold_chars=1000000) is None
    big = dialog_rounds(15, 50)
    plan = plan_compaction(big, keep_last=12, threshold_chars=100)
    assert plan is not None
    start, cutoff, history = plan
    assert start == 0
    assert all(m.type != 'system' for m in history)
    assert history == big[start:cutoff]
    assert len(big) - cutoff == 12

# ---- T10: summarize success + no hard 2000-char truncation ----
def test_t10_summarize_with_model_success(monkeypatch):
    captured = {}

    def spy(previous_summary, history_text):
        captured['prev'] = previous_summary
        captured['text'] = history_text
        return 'PROMPT'

    monkeypatch.setattr(sp_module, 'sys_summarize_prompt', spy)
    model = FakeModel(reply='s' * 2500)
    out = summarize_with_model(model, 'old summary', dialog_rounds(2, 5))
    assert out == 's' * 2500
    assert len(out) == 2500  # code removed hard truncation, summary kept whole
    assert captured['prev'] == 'old summary'
    assert 'USER: ' in captured['text']
    assert len(model.calls) == 1


# ---- T11: content blocks list flattening ----
def test_t11_summarize_content_blocks_list():
    model = FakeModel(reply=['part one', 'part two'])
    out = summarize_with_model(model, '', dialog_rounds(1, 2))
    assert out == 'part one part two'


# ---- T12: summarizer failure returns empty string ----
def test_t12_summarize_failure_returns_empty():
    failing = FakeModel(reply='x', raise_exc=True)
    assert summarize_with_model(failing, '', dialog_rounds(1, 2)) == ''
def test_t06b_toolchain_complete_advances():
    tc = [{'name': 'shell', 'args': {'cmd': 'pwd'}, 'id': 'c1', 'type': 'tool_call'}, {'name': 'shell', 'args': {'cmd': 'pwd'}, 'id': 'c2', 'type': 'tool_call'}]
    msgs = [make_msg('human', content='q0', mid='h0'), make_msg('ai', content='a0', mid='a0'), make_msg('human', content='q1', mid='h1'), make_msg('ai', content='a1', mid='a1'), make_msg('human', content='q2', mid='h2'), make_msg('ai', content='a2', mid='a2'), make_msg('human', content='q3', mid='h3'), make_msg('ai', content='a3', mid='a3'), make_msg('ai', content='', tool_calls=tc, mid='a4'), make_msg('tool', content='r1', mid='t4a'), make_msg('tool', content='r2', mid='t4b'), make_msg('human', content='q5', mid='h5')]
    start, cutoff = safe_cutoff(msgs, keep_last=4)
    assert (start, cutoff) == (0, 11)
    assert msgs[cutoff].type == 'human'


def test_t06c_toolchain_incomplete_backtracks():
    tc = [{'name': 'shell', 'args': {'cmd': 'pwd'}, 'id': 'c1', 'type': 'tool_call'}, {'name': 'shell', 'args': {'cmd': 'pwd'}, 'id': 'c2', 'type': 'tool_call'}]
    msgs = [make_msg('human', content='q0', mid='h0'), make_msg('ai', content='a0', mid='a0'), make_msg('human', content='q1', mid='h1'), make_msg('ai', content='a1', mid='a1'), make_msg('human', content='q2', mid='h2'), make_msg('ai', content='a2', mid='a2'), make_msg('human', content='q3', mid='h3'), make_msg('ai', content='a3', mid='a3'), make_msg('ai', content='', tool_calls=tc, mid='a4'), make_msg('tool', content='r1', mid='t4a'), make_msg('human', content='q5', mid='h5'), make_msg('ai', content='a5', mid='a5')]
    start, cutoff = safe_cutoff(msgs, keep_last=4)
    assert (start, cutoff) == (0, 7)
    assert msgs[cutoff].type == 'ai'


def test_t06d_tool_message_cutoff_backtracks():
    msgs = [make_msg('human', content='q0', mid='h0'), make_msg('ai', content='a0', mid='a0'), make_msg('human', content='q1', mid='h1'), make_msg('ai', content='a1', mid='a1'), make_msg('human', content='q2', mid='h2'), make_msg('ai', content='a2', mid='a2'), make_msg('tool', content='boom', mid='t3'), make_msg('human', content='q3', mid='h3'), make_msg('ai', content='a3', mid='a3'), make_msg('human', content='q4', mid='h4')]
    start, cutoff = safe_cutoff(msgs, keep_last=4)
    assert (start, cutoff) == (0, 5)
    assert msgs[cutoff].type == 'ai'


def test_t09b_plan_compaction_ai_only_none():
    ai_only = [make_msg('ai', content='x' * 100, mid='a' + str(i)) for i in range(15)]
    assert plan_compaction(ai_only, keep_last=4, threshold_chars=100) is None
