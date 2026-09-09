# Integration tests: history thread driven message compaction.
# Self-contained module: no shared conftest, all helpers inline.
# Read-only against PostgreSQL; DB missing or short thread gets skipped.

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

from vibe_cli.message.message_compactor import (
    plan_compaction,
    render_history,
    should_compress,
    summarize_with_model,
    total_size, split_into_turns,
)
from vibe_cli.workflow.base_workflow import SUMMARY_BOOKMARK_PREFIX, compact_msg_node

HISTORY_THREAD_ID = os.getenv('VIBE_HISTORY_THREAD_ID', '123022')
COMPRESS_THRESHOLD = int(os.getenv('compress_threshold_chars', '12000'))
COMPRESS_KEEP_LAST = int(os.getenv('compress_keep_last', '12'))


def _project_root():
    return Path(__file__).resolve().parents[2]


def load_project_env():
    if os.getenv('_VIBE_TEST_ENV_LOADED'):
        return
    for cand in (Path.cwd() / '.env', _project_root() / '.env'):
        if cand.exists():
            load_dotenv(cand)
            os.environ['_VIBE_TEST_ENV_LOADED'] = '1'
            return


def make_h(text='h'):
    return HumanMessage(content=text, id=uuid.uuid4().hex)


def make_a(text='a', calls=None):
    if calls:
        return AIMessage(content=text or '', tool_calls=calls, id=uuid.uuid4().hex)
    return AIMessage(content=text, id=uuid.uuid4().hex)


def make_t(text='t', call_id=None):
    return ToolMessage(content=text, tool_call_id=call_id or uuid.uuid4().hex, id=uuid.uuid4().hex)


def long_synthetic_session(count=40):
    msgs = []
    for i in range(count):
        msgs.append(make_h('用户问题 {0} '.format(i) + 'x' * 500))
        msgs.append(make_a('助手回复 {0}'.format(i)))
    return msgs


def short_session():
    return [make_h('hi'), make_a('hello')]


class SimpleResponse:
    def __init__(self, content):
        self.content = content


class FakeModel:
    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return SimpleResponse(self.content)


def read_messages(tid):
    from vibe_cli.env.check_point_memory import postgres_memory
    ckpt = postgres_memory()
    config = {'configurable': {'thread_id': tid}}
    tup = ckpt.get_tuple(config)
    if tup is None:
        return []
    ch = tup.checkpoint.get('channel_values') or {}
    return list(ch.get('messages') or [])


@pytest.fixture(scope='module')
def db_messages():
    load_project_env()
    if not os.getenv('database_url'):
        pytest.skip('database_url missing, skip postgres integration tests')
    try:
        msgs = read_messages(HISTORY_THREAD_ID)
    except Exception as exc:
        pytest.skip('postgres unavailable: {0}'.format(exc))
    # if not msgs:
    #     pytest.skip('thread {0} has no messages'.format(HISTORY_THREAD_ID))
    # if not any(getattr(m, 'type', None) == 'human' for m in msgs):
    #     pytest.skip('thread {0} has no human anchor'.format(HISTORY_THREAD_ID))
    # if not should_compress(msgs, keep_last_turns=COMPRESS_KEEP_LAST, threshold_chars=COMPRESS_THRESHOLD):
    #     pytest.skip('thread {0} history too short to compress'.format(HISTORY_THREAD_ID))
    return msgs


@dataclass
class CompactionPreview:
    before: str = ''
    after: str = ''
    affected_indexes: list = field(default_factory=list)
    summary_text: str = ''
    keep_tail_start: int = 0
    kept_count: int = 0
    decision: str = 'approve'
    model_calls: int = 0


def _collect_old_summary(messages, start, cutoff):
    parts = []
    for index, m in enumerate(messages[start:cutoff], start=start):
        if m.content:
            parts.append('对话{0},内容：{1}'.format(index, m.content))
    return chr(10).join(parts)


def build_preview(messages, model, decision='approve'):
    plan = plan_compaction(messages, keep_last_turns=COMPRESS_KEEP_LAST, threshold_chars=COMPRESS_THRESHOLD)
    if plan is None:
        return None
    start, cutoff, history = plan
    old_summary = _collect_old_summary(messages, start, cutoff)
    before_calls = len(model.calls)
    summary_text = summarize_with_model(model, old_summary, history)
    after_calls = len(model.calls)
    tail = messages[cutoff:]
    after = summary_text
    if tail:
        after = after + chr(10) + chr(10) + '[保留尾部说明] 最近{0}条消息原样保留，未参与压缩。'.format(len(tail))
    return CompactionPreview(
        before=render_history(messages[start:cutoff]),
        after=after,
        affected_indexes=list(range(start, cutoff)),
        summary_text=summary_text,
        keep_tail_start=cutoff,
        kept_count=len(tail),
        decision=decision,
        model_calls=after_calls - before_calls,
    )


def render_before_after(preview):
    head = '--- BEFORE (压缩前) ---'
    tail = '--- AFTER (压缩后) ---'
    return head + chr(10) + preview.before + chr(10) + chr(10) + tail + chr(10) + preview.after


def approve_or_reject(preview):
    return os.getenv('VIBE_COMPACTION_DECISION', preview.decision or 'approve')


def build_commit_ops(preview, messages):
    if not preview.affected_indexes:
        return []
    start = preview.affected_indexes[0]
    cutoff = preview.keep_tail_start
    removals = [RemoveMessage(id=m.id) for m in messages[start:] if getattr(m, 'id', None)]
    summary_msg = HumanMessage(
        id='summary-' + uuid.uuid4().hex[:8],
        content=SUMMARY_BOOKMARK_PREFIX + preview.summary_text,
    )
    rebuilt_tail = []
    for m in messages[cutoff:]:
        clone = m.model_copy(deep=True)
        clone.id = 'keep-' + uuid.uuid4().hex[:8]
        rebuilt_tail.append(clone)
    return removals + [summary_msg] + rebuilt_tail


def run_confirmed_compaction(messages, model, decision_func=approve_or_reject, max_redo=3):
    preview = None
    for _ in range(max_redo):
        preview = build_preview(messages, model)
        if preview is None:
            return None
        decision = decision_func(preview)
        if decision == 'approve':
            return preview, build_commit_ops(preview, messages)
        if decision == 'reject':
            return preview, None
    return preview, None


class TestHistoryRead:

    def test_split_into_turns(self, db_messages):
        result = split_into_turns(db_messages)
        assert result

    def test_render_history(self, db_messages):
        msg = render_history(db_messages)
        assert msg is not None

    def test_real_thread_messages_loaded(self, db_messages):
        assert db_messages
        assert any(m.type == 'human' for m in db_messages)
        assert total_size(db_messages) > COMPRESS_THRESHOLD


class TestHistoryCompactionPlan:
    def test_real_long_session_returns_triplet(self, db_messages):
        plan = plan_compaction(db_messages, keep_last_turns=COMPRESS_KEEP_LAST, threshold_chars=COMPRESS_THRESHOLD)
        assert plan is not None
        start, cutoff, history = plan
        assert 0 <= start
        assert start < cutoff
        assert cutoff <= len(db_messages)
        assert history

    def test_short_session_returns_none(self):
        assert plan_compaction(short_session()) is None

    def test_ai_only_history_returns_none(self):
        msgs = [make_a('回复{0}'.format(i)) for i in range(30)]
        assert plan_compaction(msgs) is None


class TestCompactionNodeInjection:
    def test_compact_node_emits_summary_and_rebuilt_tail(self, db_messages, monkeypatch):
        from vibe_cli.workflow import base_workflow as bw
        fake = FakeModel('集成摘要：已完成历史归纳')
        monkeypatch.setattr(bw, 'get_model', lambda provider: fake)
        state = {'messages': db_messages, 'model_provider': 'fake'}
        out = compact_msg_node(state)
        assert isinstance(out, dict)
        ops = out.get('messages') or []
        assert ops
        assert any(isinstance(x, RemoveMessage) for x in ops)
        ids = [str(getattr(x, 'id', '')) for x in ops]
        assert any(i.startswith('summary-') for i in ids)
        assert any(i.startswith('keep-') for i in ids)
        summary = next(
            x for x in ops if isinstance(x, HumanMessage) and str(getattr(x, 'id', '')).startswith('summary-'))
        assert summary.content.startswith(SUMMARY_BOOKMARK_PREFIX)
        # assert fake.calls


class TestFailureFallback:
    def test_summarize_returns_empty_on_model_error(self):
        model = FakeModel(error=RuntimeError('boom'))
        hist = [make_h('a' * 300), make_a('b')]
        assert summarize_with_model(model, '', hist) == ''
        assert len(model.calls) == 1

    def test_compact_node_skips_safely_on_model_error(self, db_messages, monkeypatch):
        from vibe_cli.workflow import base_workflow as bw
        monkeypatch.setattr(bw, 'get_model', lambda provider: FakeModel(error=RuntimeError('boom')))
        state = {'messages': db_messages, 'model_provider': 'fake'}
        assert compact_msg_node(state) == {}


class TestDryRunPreview:
    def test_model_called_exactly_once_per_preview(self):
        msgs = long_synthetic_session()
        model = FakeModel('合成摘要文本')
        preview = build_preview(msgs, model)
        assert preview is not None
        assert preview.model_calls == 1
        assert len(model.calls) == 1
        assert preview.summary_text == '合成摘要文本'

    def test_before_equals_rendered_slice_and_after_has_tail_note(self):
        msgs = long_synthetic_session()
        plan = plan_compaction(msgs)
        assert plan is not None
        start, cutoff, _ = plan
        model = FakeModel('模型生成的滚动摘要')
        preview = build_preview(msgs, model)
        assert preview.before == render_history(msgs[start:cutoff])
        assert preview.summary_text in preview.after
        assert '保留尾部说明' in preview.after
        assert preview.kept_count == len(msgs) - cutoff

    def test_render_before_after_contains_both_sections(self):
        msgs = long_synthetic_session()
        model = FakeModel('摘要正文')
        preview = build_preview(msgs, model)
        text = render_before_after(preview)
        assert 'BEFORE' in text
        assert 'AFTER' in text
        assert preview.before in text
        assert preview.after in text


class TestApproveCommit:
    def test_approve_returns_commit_ops_with_all_prefixes(self):
        msgs = long_synthetic_session()
        model = FakeModel('确认后的摘要')
        result = run_confirmed_compaction(msgs, model)
        assert result is not None
        preview, ops = result
        assert preview is not None
        assert ops
        assert any(isinstance(x, RemoveMessage) for x in ops)
        ids = [str(getattr(x, 'id', '')) for x in ops]
        assert any(i.startswith('summary-') for i in ids)
        assert any(i.startswith('keep-') for i in ids)
        assert sum(1 for x in ops if str(getattr(x, 'id', '')).startswith('summary-')) == 1

    def test_approve_does_not_write_back_to_checkpoint(self, db_messages):
        load_project_env()
        from vibe_cli.env.check_point_memory import postgres_memory
        ckpt = postgres_memory()
        config = {'configurable': {'thread_id': HISTORY_THREAD_ID}}
        before_tup = ckpt.get_tuple(config)
        if before_tup is None:
            pytest.skip('thread vanished while testing')
        before_msgs = list((before_tup.checkpoint.get('channel_values') or {}).get('messages') or [])
        model = FakeModel('不落库摘要')
        result = run_confirmed_compaction(db_messages, model)
        assert result is not None
        preview, ops = result
        assert preview is not None
        assert ops
        after_tup = ckpt.get_tuple(config)
        after_msgs = list((after_tup.checkpoint.get('channel_values') or {}).get('messages') or []) if after_tup else []
        assert len(after_msgs) == len(before_msgs)
        assert [str(getattr(m, 'id', '')) for m in after_msgs] == [str(getattr(m, 'id', '')) for m in before_msgs]


class TestRejectNoWrite:
    def test_reject_returns_no_commit_ops(self):
        msgs = long_synthetic_session()
        ids_before = [str(getattr(m, 'id', '')) for m in msgs]
        model = FakeModel('摘要')
        result = run_confirmed_compaction(msgs, model, decision_func=lambda p: 'reject')
        assert result is not None
        preview, ops = result
        assert preview is not None
        assert ops is None
        ids_after = [str(getattr(m, 'id', '')) for m in msgs]
        assert ids_after == ids_before
        assert len(model.calls) == 1


class TestRedoRegenerates:
    def test_redo_calls_model_again_then_approves(self):
        msgs = long_synthetic_session()
        model = FakeModel('第二版摘要')
        counter = {'n': 0}

        def decide(preview):
            counter['n'] += 1
            if counter['n'] < 2:
                return 'redo'
            return 'approve'

        result = run_confirmed_compaction(msgs, model, decision_func=decide)
        assert result is not None
        preview, ops = result
        assert preview is not None
        assert ops
        assert len(model.calls) == 2
