# Unit tests for the message compactor module (src\vibe_cli\message).
# Self-contained: no shared conftest, no integration tests, no real LLM.

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from vibe_cli.message.message_compactor import (
    DEFAULT_KEEP_LAST_TURNS,
    DEFAULT_THRESHOLD_CHARS,
    plan_compaction,
    render_history,
    safe_cutoff,
    should_compress,
    summarize_with_model,
    total_size,
)


def make_h(text='h'):
    return HumanMessage(content=text)


def make_a(text='a', calls=None):
    if calls:
        return AIMessage(content=text or '', tool_calls=calls)
    return AIMessage(content=text)


def make_t(text='t', call_id='c0'):
    return ToolMessage(content=text, tool_call_id=call_id)


def make_s(text='s'):
    return SystemMessage(content=text)


def tc(cid):
    return [{'name': 'demo', 'args': {}, 'id': cid, 'type': 'tool_call'}]


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


class TestTotalSize:
    def test_empty_returns_zero(self):
        assert total_size([]) == 0

    def test_counts_human_content(self):
        msgs = [make_h('hello')]
        assert total_size(msgs) == 5

    def test_skips_system_messages(self):
        msgs = [make_s('sys-block-abcdef'), make_h('hi')]
        assert total_size(msgs) == 2

    def test_counts_ai_and_tool_content(self):
        msgs = [make_h('u'), make_a('a'), make_t('t')]
        assert total_size(msgs) == 3

    def test_mixed_and_empty_content(self):
        msgs = [make_s('ignored'), make_h(''), make_a('xy'), make_t('')]
        assert total_size(msgs) == 2


class TestShouldCompress:
    def test_empty_never_compresses(self):
        assert should_compress([]) is False

    def test_few_messages_never_compresses(self):
        msgs = [make_h('x' * 3000) for _ in range(12)]
        assert should_compress(msgs) is False

    def test_many_short_messages_do_not_compress(self):
        msgs = [make_h('short') for _ in range(20)]
        assert should_compress(msgs) is False

    def test_many_and_big_compress(self):
        msgs = [make_h('x' * 1000) for _ in range(20)]
        assert should_compress(msgs) is True

    def test_exactly_at_threshold_does_not_compress(self):
        msgs = [make_h('x' * 600) for _ in range(20)]
        assert should_compress(msgs) is False

    def test_system_messages_do_not_count_toward_threshold(self):
        msgs = [make_s('y' * 5000)] + [make_h('x' * 1000) for _ in range(20)]
        assert should_compress(msgs) is True

    def test_custom_keep_last(self):
        msgs = [make_h('x' * 3000) for _ in range(6)]
        assert should_compress(msgs, keep_last=5) is True
        assert should_compress(msgs, keep_last=6) is False

    def test_custom_threshold(self):
        msgs = [make_h('x' * 10) for _ in range(5)]
        assert should_compress(msgs, keep_last=1, threshold_chars=49) is True
        assert should_compress(msgs, keep_last=1, threshold_chars=50) is False


class TestSafeCutoff:
    def test_too_few_messages_returns_none(self):
        msgs = [make_h(str(i)) for i in range(12)]
        assert safe_cutoff(msgs) is None

    def test_ai_only_history_returns_none(self):
        msgs = [make_a(str(i)) for i in range(20)]
        assert safe_cutoff(msgs) is None

    def test_tool_only_history_returns_none(self):
        msgs = [make_t('r' + str(i), call_id='c' + str(i)) for i in range(20)]
        assert safe_cutoff(msgs) is None

    def test_plain_human_history_cutoff(self):
        msgs = [make_h('m' + str(i)) for i in range(20)]
        span = safe_cutoff(msgs)
        assert span == (0, 8)

    def test_system_prefix_shifted(self):
        msgs = [make_s('boot')] + [make_h('m' + str(i)) for i in range(20)]
        span = safe_cutoff(msgs)
        assert span == (1, 9)

    def test_keep_last_controls_cutoff(self):
        msgs = [make_h('m' + str(i)) for i in range(20)]
        assert safe_cutoff(msgs, keep_last=5) == (0, 15)

    def test_start_skips_orphan_tool(self):
        msgs = [make_t('orphan')] + [make_h('m' + str(i)) for i in range(20)]
        span = safe_cutoff(msgs)
        assert span == (1, 9)

    def test_cutoff_after_initial_chain(self):
        msgs = [
            make_h('q1'),
            make_a('', calls=tc('c1')),
            make_t('r1', call_id='c1'),
        ]
        msgs += [make_h('m' + str(i)) for i in range(20)]
        span = safe_cutoff(msgs)
        assert span == (0, 11)

    def test_full_tool_chain_advances_cutoff(self):
        msgs = [make_h(str(i) + 'x' * 1000) for i in range(8)] + [
            make_a('', calls=tc('c1')),
            make_t('r1', call_id='c1'),
        ] + [make_h(str(i) + 'x' * 1000) for i in range(10)]
        span = safe_cutoff(msgs)
        assert span == (0, 10)

    def test_full_multi_call_chain_advances_cutoff(self):
        msgs = [make_h(str(i) + 'x' * 1000) for i in range(8)] + [
            make_a('', calls=tc('c1') + tc('c2')),
            make_t('r1', call_id='c1'),
            make_t('r2', call_id='c2'),
        ] + [make_h(str(i)) for i in range(9)]
        span = safe_cutoff(msgs)
        assert span == (0, 11)

    def test_broken_tool_chain_backtracks(self):
        msgs = [make_h(str(i) + 'x' * 1000) for i in range(8)] + [
            make_a('', calls=tc('c1')),
        ] + [make_h(str(i)) for i in range(11)]
        span = safe_cutoff(msgs)
        assert span == (0, 7)

    def test_cutoff_never_lands_on_orphan_tool(self):
        msgs = [make_h(str(i) + 'x' * 1000) for i in range(8)] + [
            make_t('orphan'),
        ] + [make_h(str(i)) for i in range(11)]
        span = safe_cutoff(msgs)
        assert span == (0, 7)

    def test_empty_returns_none(self):
        assert safe_cutoff([]) is None


class TestRenderHistory:
    def test_empty_returns_empty(self):
        assert render_history([]) == ''

    def test_renders_system(self):
        out = render_history([make_s('boot')])
        assert out == 'SYSTEM: boot'

    def test_renders_human(self):
        out = render_history([make_h('hello')])
        assert out == 'USER: hello'

    def test_renders_ai_content(self):
        out = render_history([make_a('world')])
        assert out == 'AI: world'

    def test_renders_tool(self):
        out = render_history([make_t('result')])
        assert out == 'TOOL: result'

    def test_joins_messages_with_blank_line(self):
        msgs = [make_s('boot'), make_h('q'), make_a('a'), make_t('r')]
        assert render_history(msgs) == 'SYSTEM: boot\n\nUSER: q\n\nAI: a\n\nTOOL: r'

    def test_ai_tool_calls_placeholder(self):
        ai = make_a('', calls=tc('c1'))
        out = render_history([ai])
        assert out == 'AI: [tool calls] {0}'.format(ai.tool_calls)

    def test_ai_without_content_without_calls_placeholder(self):
        ai = make_a('')
        out = render_history([ai])
        assert out == 'AI: [tool calls] {0}'.format(ai.tool_calls)

    def test_content_may_be_non_string(self):
        ai = AIMessage(content=['part1', 'part2'])
        out = render_history([ai])
        assert out == 'AI: {0}'.format(str(['part1', 'part2']))


class TestPlanCompaction:
    def test_empty_returns_none(self):
        assert plan_compaction([]) is None

    def test_not_enough_messages_returns_none(self):
        msgs = [make_h('x' * 3000) for _ in range(12)]
        assert plan_compaction(msgs) is None

    def test_ai_only_returns_none(self):
        msgs = [make_a('x' * 3000) for _ in range(20)]
        assert plan_compaction(msgs) is None

    def test_small_history_returns_none(self):
        msgs = [make_h('m' + str(i)) for i in range(20)]
        assert plan_compaction(msgs, threshold_chars=10 ** 9) is None

    def test_plan_returns_span_and_history(self):
        msgs = [make_h('m' + str(i) + 'x' * 1000) for i in range(20)]
        plan = plan_compaction(msgs)
        assert plan is not None
        start, cutoff, history = plan
        assert start == 0
        assert cutoff == 8
        assert len(history) == 8
        assert all(isinstance(m, HumanMessage) for m in history)

    def test_plan_history_uses_span_boundaries(self):
        msgs = [make_h(str(i) + 'x' * 1000) for i in range(8)] + [
            make_a('', calls=tc('c1')),
            make_t('r1', call_id='c1'),
        ] + [make_h(str(i) + 'x' * 1000) for i in range(10)]
        plan = plan_compaction(msgs)
        assert plan is not None
        start, cutoff, history = plan
        assert (start, cutoff) == (0, 10)
        assert len(history) == 10

    def test_plan_compacts_in_place_without_mutation(self):
        msgs = [make_h('m' + str(i) + 'x' * 1000) for i in range(20)]
        snapshot = list(msgs)
        plan = plan_compaction(msgs)
        assert plan is not None
        assert msgs == snapshot


class TestSummarizeWithModel:
    def test_returns_stripped_string(self):
        model = FakeModel(content='  summary text  ')
        out = summarize_with_model(model, 'old', [make_h('m0')])
        assert out == 'summary text'
        assert len(model.calls) == 1

    def test_prompt_contains_old_and_history(self):
        model = FakeModel(content='ok')
        out = summarize_with_model(model, 'previous-summary', [make_h('m0'), make_a('m1')])
        assert out == 'ok'
        sent = model.calls[0]
        text = '\n'.join(str(getattr(m, 'content', '')) for m in sent)
        assert 'previous-summary' in text
        assert 'USER: m0' in text
        assert 'AI: m1' in text

    def test_normalizes_content_blocks_list(self):
        model = FakeModel(content=[{'type': 'text', 'text': 'a'}, {'type': 'text', 'text': 'b'}])
        out = summarize_with_model(model, 'old', [make_h('m0')])
        assert out == "{'type': 'text', 'text': 'a'} {'type': 'text', 'text': 'b'}"

    def test_none_content_returns_empty(self):
        model = FakeModel(content=None)
        out = summarize_with_model(model, 'old', [make_h('m0')])
        assert out == ''

    def test_invoke_error_returns_empty(self):
        model = FakeModel(content='unused', error=RuntimeError('boom'))
        out = summarize_with_model(model, 'old', [make_h('m0')])
        assert out == ''

    def test_messages_passed_are_system_and_human(self):
        model = FakeModel(content='ok')
        summarize_with_model(model, 'old', [make_h('m0')])
        sent = model.calls[0]
        assert len(sent) == 2
        assert sent[0].type == 'system'
        assert sent[1].type == 'human'
