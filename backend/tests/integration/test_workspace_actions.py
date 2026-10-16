"""Real API persistence and ownership for workspace management."""
import uuid
import pytest
from tests.conftest import login
from tests.helpers import setup_datasource_and_grants

pytestmark = pytest.mark.integration


def test_session_rename_archive_restore_and_version_guard(admin_client, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), 'conversational_ask_enabled', True)
    row = admin_client.post('/api/v1/sessions', json={'title': 'original'}).json()
    path = f"/api/v1/sessions/{row['id']}"
    renamed = admin_client.patch(path, json={'title': 'renamed', 'expected_context_version': row['context_version']})
    assert renamed.status_code == 200 and renamed.json()['title'] == 'renamed'
    assert admin_client.patch(path, json={'title': 'stale', 'expected_context_version': 999}).status_code == 409
    assert admin_client.patch(path, json={'archived': True}).json()['archived_at']
    assert not admin_client.get('/api/v1/sessions').json()['items']
    assert admin_client.get('/api/v1/sessions?include_archived=true').json()['items'][0]['id'] == row['id']
    denied = admin_client.post(path + '/turns', headers={'Idempotency-Key': str(uuid.uuid4())}, json={'text': 'analyze'})
    assert denied.status_code == 409
    assert admin_client.patch(path, json={'archived': False}).json()['archived_at'] is None
    assert admin_client.get(path).json()['title'] == 'renamed'


def test_saved_sql_is_private_and_never_executes_when_saved(admin_client, viewer, integration_env):
    from app.db import session_scope
    from app.models.orm import QueryJob
    from sqlalchemy import select,func
    source = setup_datasource_and_grants(admin_client, integration_env['source_url'])
    row = admin_client.post('/api/v1/saved-queries', json={'title': 'my template', 'datasource_id': str(source.datasource_id), 'sql': 'SELECT country FROM fixture_metrics'})
    assert row.status_code == 201, row.text
    saved = row.json()
    assert admin_client.get('/api/v1/saved-queries').json()['items'] == [saved]
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(QueryJob)) == 0
    admin_client.post('/api/v1/auth/logout')
    token = login(admin_client, viewer['username'], viewer['password'])
    admin_client.headers.update({'X-CSRF-Token': token})
    assert not admin_client.get('/api/v1/saved-queries').json()['items']
    assert admin_client.delete(f"/api/v1/saved-queries/{saved['id']}").status_code == 404
