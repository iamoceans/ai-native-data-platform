"""Self-service password rotation invalidates every old session."""
import pytest
from fastapi.testclient import TestClient
from tests.conftest import login

pytestmark = pytest.mark.integration


def test_password_rotation_revokes_all_sessions_and_preserves_other_accounts(admin_client, bootstrap, viewer):
    with TestClient(admin_client.app) as second, TestClient(admin_client.app) as other:
        login(second, bootstrap['username'], bootstrap['password'])
        login(other, viewer['username'], viewer['password'])
        new_password = 'new-private-password-42'
        response = admin_client.post('/api/v1/auth/password', json={'current_password': bootstrap['password'], 'new_password': new_password})
        assert response.status_code == 204, response.text
        assert not admin_client.cookies.get('ainative_session')
        assert not admin_client.cookies.get('ainative_csrf')
        assert second.get('/api/v1/auth/me').status_code == 401
        assert other.get('/api/v1/auth/me').status_code == 200
        old = admin_client.post('/api/v1/auth/login', json={'username': bootstrap['username'], 'password': bootstrap['password']})
        assert old.status_code == 401
        login(admin_client, bootstrap['username'], new_password)
        assert admin_client.get('/api/v1/auth/me').status_code == 200


def test_wrong_password_is_audited_without_logging_secrets_and_rate_limited(admin_client):
    from app.db import session_scope
    from app.models.orm import AuditLog
    from sqlalchemy import select
    for _ in range(5):
        result = admin_client.post('/api/v1/auth/password', json={'current_password': 'wrong-secret', 'new_password': 'a-new-secret-value'})
        assert result.status_code == 422
        assert 'wrong-secret' not in result.text and 'a-new-secret-value' not in result.text
    assert admin_client.post('/api/v1/auth/password', json={'current_password': 'wrong-secret', 'new_password': 'a-new-secret-value'}).status_code == 429
    assert admin_client.get('/api/v1/auth/me').status_code == 200
    with session_scope() as db:
        rows = list(db.scalars(select(AuditLog).where(AuditLog.action == 'auth.password.change')))
        assert len(rows) == 5 and all(row.outcome == 'denied' and row.details == {} for row in rows)


def test_password_requires_csrf_and_valid_closed_fields(client, bootstrap):
    csrf = login(client, bootstrap['username'], bootstrap['password'])
    assert client.post('/api/v1/auth/password', json={'current_password': bootstrap['password'], 'new_password': 'valid-new-password'}).status_code == 403
    for patch in [{'new_password': 'tiny'}, {'new_password': bootstrap['password']}, {'user_id': 'foreign'}]:
        response = client.post('/api/v1/auth/password', headers={'X-CSRF-Token': csrf}, json={'current_password': bootstrap['password'], 'new_password': 'valid-new-password', **patch})
        assert response.status_code == 422
        assert bootstrap['password'] not in response.text


def test_empty_session_event_stream_ends_immediately(admin_client):
    sid = admin_client.post('/api/v1/sessions', json={'title': 'empty stream'}).json()['id']
    response = admin_client.get(f'/api/v1/sessions/{sid}/events')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/event-stream')
    assert ': connected' in response.text and 'event: stream.end' in response.text
    assert '"status":"idle"' in response.text


def test_login_holds_the_user_lock_until_its_session_is_published(admin_client, bootstrap, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError
    from app.api.routes import auth
    from app.db import session_scope
    owner = admin_client.get('/api/v1/auth/me').json()['id']
    entered, release = threading.Event(), threading.Event()
    real_verify = auth.verify_password
    def verify(encoded, password):
        if not entered.is_set():
            entered.set(); assert release.wait(5)
        return real_verify(encoded, password)
    monkeypatch.setattr(auth, 'verify_password', verify)
    with TestClient(admin_client.app) as concurrent, ThreadPoolExecutor(max_workers=1, thread_name_prefix='credential-login') as pool:
        pending = pool.submit(concurrent.post, '/api/v1/auth/login', json={'username': bootstrap['username'], 'password': bootstrap['password']})
        try:
            assert entered.wait(3)
            with pytest.raises(OperationalError):
                with session_scope() as db:
                    db.execute(text('SELECT id FROM users WHERE id = :id FOR UPDATE NOWAIT'), {'id': owner})
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 200
        response = admin_client.post('/api/v1/auth/password', json={'current_password': bootstrap['password'], 'new_password': 'rotated-after-login-42'})
        assert response.status_code == 204
        assert concurrent.get('/api/v1/auth/me').status_code == 401
