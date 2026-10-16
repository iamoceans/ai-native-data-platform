"""Account-owned settings, durable preferences and version/CSRF boundaries."""
import pytest
from tests.conftest import login

pytestmark = pytest.mark.integration


def test_preferences_survive_sessions_and_are_isolated_between_accounts(admin_client, viewer, bootstrap):
    client = admin_client
    before = client.get('/api/v1/auth/me').json()
    result = client.patch('/api/v1/auth/me', json={
        'expected_settings_version': before['settings_version'], 'display_name': '  Analyst A  ',
        'locale': 'zh-CN', 'timezone': 'Asia/Singapore',
    })
    assert result.status_code == 200, result.text
    updated = result.json()
    assert updated['display_name'] == 'Analyst A'
    assert updated['settings_version'] == before['settings_version'] + 1
    assert updated['capabilities'] == before['capabilities']
    assert 'password_hash' not in updated
    stale = client.patch('/api/v1/auth/me', json={'expected_settings_version': before['settings_version'], 'locale': 'en'})
    assert stale.status_code == 409
    client.post('/api/v1/auth/logout')
    csrf = login(client, viewer['username'], viewer['password'])
    client.headers.update({'X-CSRF-Token': csrf})
    other = client.get('/api/v1/auth/me').json()
    assert other['locale'] is None and other['timezone'] is None
    assert other['display_name'] == '' and other['settings_version'] == 1
    assert client.patch('/api/v1/auth/me', json={'expected_settings_version': 1, 'locale': 'en'}).status_code == 200
    client.post('/api/v1/auth/logout')
    csrf = login(client, bootstrap['username'], bootstrap['password'])
    client.headers.update({'X-CSRF-Token': csrf})
    restored = client.get('/api/v1/auth/me').json()
    assert restored == updated
    reset = client.patch('/api/v1/auth/me', json={'expected_settings_version': updated['settings_version'], 'timezone': None, 'locale': None})
    assert reset.status_code == 200
    assert reset.json()['timezone'] is None and reset.json()['locale'] is None
    assert reset.json()['display_name'] == 'Analyst A'


@pytest.mark.parametrize('patch', [
    {'locale': 'xx'}, {'timezone': 'Mars/Olympus'}, {'timezone': '../UTC'},
    {'display_name': 'bad\nname'}, {'display_name': None}, {'display_name': 'x' * 81},
    {'roles': ['admin']}, {'user_id': 'another-account'}, {'capabilities': ['admin.manage']},
    {'settings_version': 99},
])
def test_invalid_or_privileged_profile_fields_are_rejected(admin_client, patch):
    response = admin_client.patch('/api/v1/auth/me', json={'expected_settings_version': 1, **patch})
    assert response.status_code == 422, response.text
    assert admin_client.get('/api/v1/auth/me').json()['settings_version'] == 1


def test_profile_write_requires_csrf_and_version(client, bootstrap):
    csrf = login(client, bootstrap['username'], bootstrap['password'])
    assert client.patch('/api/v1/auth/me', json={'expected_settings_version': 1, 'locale': 'en'}).status_code == 403
    assert client.patch('/api/v1/auth/me', headers={'X-CSRF-Token': csrf}, json={'locale': 'en'}).status_code == 422


def test_session_list_paginates_all_owned_history(admin_client, viewer):
    ids = []
    for index in range(45):
        response = admin_client.post('/api/v1/sessions', json={'title': f'conversation {index}'})
        assert response.status_code == 201
        ids.append(response.json()['id'])
    first = admin_client.get('/api/v1/sessions').json()
    assert len(first['items']) == 20 and first['next_cursor']
    found, cursor = first['items'], first['next_cursor']
    while cursor:
        page = admin_client.get('/api/v1/sessions', params={'cursor': cursor}).json()
        found += page['items']; cursor = page['next_cursor']
    assert len(found) == 45 and {item['id'] for item in found} == set(ids)
    assert admin_client.get('/api/v1/sessions', params={'cursor': first['next_cursor'] + 'x'}).status_code == 422
    admin_client.post('/api/v1/auth/logout')
    csrf = login(admin_client, viewer['username'], viewer['password'])
    admin_client.headers.update({'X-CSRF-Token': csrf})
    assert admin_client.get('/api/v1/sessions').json()['items'] == []
