"""The self-service access view reflects current grants without leaking other accounts."""
from datetime import timedelta
import uuid

import pytest
from sqlalchemy import delete

from app.db import session_scope
from app.ids import utcnow
from app.models.orm import Dataset, Datasource, Permission
from app.repositories import users as users_repo
from tests.conftest import login

pytestmark = pytest.mark.integration


@pytest.fixture()
def access_account(client, bootstrap, viewer):
    with session_scope() as session:
        owner = users_repo.get_user_by_username(session, bootstrap['username'])
        user = users_repo.get_user_by_username(session, viewer['username'])
        roles = [users_repo.get_role_by_name(session, name) for name in ['viewer', 'analyst']]
        users_repo.set_user_roles(session, user.id, [role.id for role in roles])
        source = Datasource(id=uuid.uuid4(), name='own-source', kind='postgres', enabled=True,
                            connection_config={'host': 'private-host'}, secret_ref='private-secret',
                            capabilities={}, created_by=owner.id)
        session.add(source)
        session.flush()
        ids = {}
        for name in ['fixture_metrics', 'query_only', 'expired', 'hidden', 'inactive']:
            dataset = Dataset(id=uuid.uuid4(), datasource_id=source.id, catalog_name='db', schema_name='public',
                              object_name=name, object_type='TABLE', active=name != 'inactive')
            session.add(dataset)
            ids[name] = dataset.id
        session.flush()
        def grant(name, role, action, expires=None):
            session.add(Permission(id=uuid.uuid4(), dataset_id=ids[name], role_id=role.id,
                                   action=action, expires_at=expires, created_by=owner.id))
        grant('fixture_metrics', roles[0], 'discover')
        grant('fixture_metrics', roles[1], 'query', utcnow() + timedelta(hours=1))
        grant('fixture_metrics', roles[0], 'query', utcnow() - timedelta(hours=1))
        grant('query_only', roles[1], 'query')
        grant('expired', roles[0], 'discover', utcnow() - timedelta(seconds=1))
        grant('inactive', roles[0], 'discover')
        grant('hidden', users_repo.get_role_by_name(session, 'admin'), 'discover')
        user_id = user.id
    csrf = login(client, viewer['username'], viewer['password'])
    client.headers.update({'X-CSRF-Token': csrf})
    return client, ids, user_id, roles[1].id


def test_self_access_unions_roles_excludes_expiry_inactive_and_other_users(access_account):
    client, ids, user_id, _ = access_account
    result = client.get('/api/v1/auth/me/access', params={'user_id': str(uuid.uuid4())})
    assert result.status_code == 200, result.text
    assert result.headers['cache-control'] == 'no-store'
    body = result.json()
    assert body['profile']['id'] == str(user_id)
    assert body['last_login_at'] and body['created_at'] and body['active']
    assert body['total'] == 2 and body['datasource_count'] == 1
    rows = {row['dataset_id']: row for row in body['items']}
    table = rows[str(ids['fixture_metrics'])]
    assert table['actions'] == ['discover', 'query'] and table['query_available']
    assert len(table['grants']) == 2
    assert {grant['role'] for grant in table['grants']} == {'viewer', 'analyst'}
    assert table['domain'] == '平台验收'
    assert table['catalog'] == 'db' and table['namespace'] == 'public'
    assert rows[str(ids['query_only'])]['query_available'] is False
    assert rows[str(ids['query_only'])]['domain'] is None
    assert any(domain['queryable_count'] == 1 for domain in body['domains'])
    assert len(body['roles']) == 2
    for secret in ['private-host', 'private-secret', 'password_hash', str(ids['hidden'])]:
        assert secret not in result.text


def test_revocation_expiry_role_changes_and_disabled_source_are_current(access_account):
    client, ids, user_id, analyst_id = access_account
    with session_scope() as session:
        source = session.get(Datasource, session.get(Dataset, ids['fixture_metrics']).datasource_id)
        source.enabled = False
    initial = client.get('/api/v1/auth/me/access').json()
    assert all(not item['query_available'] for item in initial['items'])
    with session_scope() as session:
        grant = session.query(Permission).filter_by(dataset_id=ids['fixture_metrics'], role_id=analyst_id, action='query').one()
        grant.expires_at = utcnow() - timedelta(seconds=1)
        session.execute(delete(Permission).where(Permission.dataset_id == ids['query_only']))
    body = client.get('/api/v1/auth/me/access').json()
    assert body['total'] == 1
    assert body['items'][0]['actions'] == ['discover']
    assert body['items'][0]['query_available'] is False
    with session_scope() as session:
        user = users_repo.get_user(session, user_id)
        users_repo.set_user_roles(session, user.id, [analyst_id])
    assert client.get('/api/v1/auth/me/access').json()['items'] == []


def test_pagination_cursor_is_self_scoped_and_all_summaries_are_complete(access_account, bootstrap):
    client, ids, _, _ = access_account
    first = client.get('/api/v1/auth/me/access?limit=1').json()
    assert first['total'] == 2 and len(first['items']) == 1 and first['next_cursor']
    second = client.get('/api/v1/auth/me/access', params={'cursor': first['next_cursor'], 'limit': 1}).json()
    assert len(second['items']) == 1 and not second['next_cursor']
    assert second['items'][0]['dataset_id'] != first['items'][0]['dataset_id']
    assert second['domains'] == first['domains']
    assert client.get('/api/v1/auth/me/access?limit=101').status_code == 422
    assert client.get('/api/v1/auth/me/access', params={'cursor': first['next_cursor']+'x'}).status_code == 422
    client.post('/api/v1/auth/logout')
    assert client.get('/api/v1/auth/me/access').status_code == 401
    with session_scope() as session:
        admin = users_repo.get_user_by_username(session, bootstrap['username'])
        users_repo.set_user_roles(session, admin.id, [users_repo.get_role_by_name(session, 'admin').id])
    csrf = login(client, bootstrap['username'], bootstrap['password'])
    client.headers.update({'X-CSRF-Token': csrf})
    assert client.get('/api/v1/auth/me/access', params={'cursor': first['next_cursor']}).status_code == 422
    admin_rows = client.get('/api/v1/auth/me/access').json()['items']
    assert {row['dataset_id'] for row in admin_rows} == {str(ids['hidden'])}  # no administrator bypass
