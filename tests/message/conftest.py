# Shared pytest fixtures and factories for message-compactor tests.
from __future__ import annotations

import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from vibe_cli.workflow import base_workflow


class FakeModel:
    # Model double: records every invoke() and can fail on demand.

    def __init__(self, reply='fake summary', raise_exc=False):
        self.reply = reply
        self.raise_exc = raise_exc
        self.calls = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        if self.raise_exc:
            raise RuntimeError('boom')
        self.calls.append(list(messages))
        return AIMessage(content=self.reply)

def make_msg(kind, content='x', mid=None, tool_calls=None):
    # Build a LangChain message with an explicit id (persisted shape).
    if mid is None:
        mid = kind + '-' + uuid.uuid4().hex[:8]
    if kind == 'human':
        return HumanMessage(content=content, id=mid)
    if kind == 'ai':
        if tool_calls is None:
            return AIMessage(content=content, id=mid)
        return AIMessage(content=content, id=mid, tool_calls=tool_calls)
    if kind == 'system':
        return SystemMessage(content=content, id=mid)
    if kind == 'tool':
        return ToolMessage(content=content, tool_call_id='call-1', id=mid)
    raise ValueError('unknown kind: ' + kind)


def dialog_rounds(n, content_len=50):
    # n simple Human+AI rounds producing flat list with ids h0, a0, h1, a1 ...
    msgs = []
    for i in range(n):
        q = 'Q' + str(i)
        a = 'A' + str(i)
        msgs.append(make_msg('human', content=q * content_len, mid='h' + str(i)))
        msgs.append(make_msg('ai', content=a * content_len, mid='a' + str(i)))
    return msgs

@pytest.fixture
def fake_model_factory(monkeypatch):
    # Patch base_workflow.get_model_by_name and hand back the FakeModel.
    def _factory(reply='fake summary', raise_exc=False):
        model = FakeModel(reply=reply, raise_exc=raise_exc)
        monkeypatch.setattr(base_workflow, 'get_model_by_name', lambda **kwargs: model)
        return model

    return _factory


def full_state(messages):
    # Populate every AgentState channel so LangGraph accepts the input.
    return {
        'messages': messages,
        'requires_approval': False,
        'node_status': 'normal',
        'model_name': 'fake-model',
        'base_url': 'fake-url',
        'api_key': 'fake-key',
        'work_dir': '.',
        'temperature': 0.0,
    }