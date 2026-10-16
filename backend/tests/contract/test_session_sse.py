"""Session SSE framing/resume/closure contract independent of service availability."""
import asyncio
import json
import uuid
from itertools import count
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from app.api.routes import conversations as routes

pytestmark = pytest.mark.contract


def response(monkeypatch, *, after=0, lost_owner=False, empty=False):
    owner, sid, turn_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(routes, 'stream_session_active', lambda *args: True)
    monkeypatch.setattr(routes, '_own_session', lambda *args: None)
    monkeypatch.setattr(routes, 'get_session_factory', lambda: lambda: nullcontext(None))
    ticks = count()
    monkeypatch.setattr(routes, 'time', SimpleNamespace(monotonic=lambda: next(ticks), sleep=lambda _: None))
    monkeypatch.setattr(routes, 'SSE_MAX_SECONDS', 10)
    monkeypatch.setattr(routes, 'SSE_POLL_SECONDS', 0)
    monkeypatch.setattr(routes.analyses_repo, 'get_session', lambda *args: SimpleNamespace(user_id=uuid.uuid4() if lost_owner else owner))
    monkeypatch.setattr(routes.analyses_repo, 'list_session_turn_ids', lambda *args: [turn_id])
    monkeypatch.setattr(routes.analyses_repo, 'list_session_analysis_ids', lambda *args: [])
    monkeypatch.setattr(routes.conversation_repo, 'active_turn', lambda *args: None)
    monkeypatch.setattr(routes.users_repo, 'role_ids_for_user', lambda *args: [])
    events = [SimpleNamespace(id=10, event_type='turn.started', payload={'text': '中文\nline'}), SimpleNamespace(id=11, event_type='turn.completed', payload={'status': 'COMPLETED'})]
    def read(db, *, pairs, after_id):
        assert pairs == [('conversation_turn', turn_id)]
        return [] if empty else [item for item in events if item.id > after_id]
    monkeypatch.setattr(routes.events_repo, 'events_for_resources', read)
    request = Request({'type': 'http', 'headers': [(b'last-event-id', str(after).encode())], 'query_string': b''})
    return routes.session_events(sid, request, SimpleNamespace(user=SimpleNamespace(id=owner), session=SimpleNamespace(id=uuid.uuid4())), None)


def collect(result):
    async def read(): return [part async for part in result.body_iterator]
    return asyncio.run(read())


def test_session_sse_framing_is_json_and_replay_is_monotonic(monkeypatch):
    result = response(monkeypatch)
    parts = collect(result)
    assert result.media_type == 'text/event-stream'
    assert result.headers['cache-control'] == 'no-cache' and result.headers['x-accel-buffering'] == 'no'
    assert parts[0] == ': connected\n\n'
    event_parts = [part for part in parts if part.startswith('id:')]
    assert [part.splitlines()[0] for part in event_parts] == ['id: 10', 'id: 11']
    assert json.loads(event_parts[0].split('data: ', 1)[1]) == {'text': '中文\nline'}
    assert parts[-1] == 'event: stream.end\ndata: {"status":"idle"}\n\n'
    assert [part.splitlines()[0] for part in collect(response(monkeypatch, after=10)) if part.startswith('id:')] == ['id: 11']


def test_empty_idle_session_closes_without_waiting_for_an_event(monkeypatch):
    assert collect(response(monkeypatch, empty=True)) == [': connected\n\n', 'event: stream.end\ndata: {"status":"idle"}\n\n']


def test_lost_session_ownership_closes_before_publishing_events(monkeypatch):
    parts = collect(response(monkeypatch, lost_owner=True))
    assert parts == [': connected\n\n', 'event: stream.closed\ndata: {"reason":"not_authorized"}\n\n']


def test_revoked_authentication_session_closes_before_replay(monkeypatch):
    result=response(monkeypatch)
    monkeypatch.setattr(routes,'stream_session_active',lambda *a: False)
    chunks=collect(result)
    assert any('session_revoked' in chunk for chunk in chunks)
    assert not any('id: 10' in chunk for chunk in chunks)
