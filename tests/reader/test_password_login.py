"""Observe the password boundary through the real HTTP app and isolated files."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastapi.responses import StreamingResponse

from oko.reader.password_auth import PasswordAccess, MAX_AGE, user_record
from tests.support.app import create_app

PASSWORD = 'synthetic-password'


@pytest.fixture(scope='module')
def credentials():
    return {'version': 1, 'users': {'alice': user_record(PASSWORD), 'bob': user_record(PASSWORD)}, 'sessions': {}, 'attempts': []}


def setup(tmp_path, credentials, **options):
    (tmp_path / 'access.json').write_text(json.dumps(credentials))
    return create_app(tmp_path, token='service', persistent=True, **options)


def login(client, name='alice', origin='http://testserver'):
    return client.post('/login', data={'username': name, 'password': PASSWORD}, headers={'Origin': origin}, follow_redirects=False)


def test_all_routes_require_session_and_no_launcher_bootstrap(tmp_path, credentials):
    app = setup(tmp_path, credentials)
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        for path, operations in app.openapi()['paths'].items():
            if path in ('/login', '/login/style.css', '/login/session'):
                continue
            for method in operations:
                response = client.request(method, path, follow_redirects=False, headers={'Range': 'bytes=0-10'})
                assert response.status_code in (303, 403), (method, path, response.status_code)
                assert response.headers.get('cache-control') == 'no-store'
        for path in ['/', '/openapi.json', '/docs', '/api/v3/status', '/api/v3/materials/id/media/file']:
            assert client.get(path, follow_redirects=False, headers={'Authorization': 'Bearer service', 'Sec-Fetch-Mode': 'navigate'}).status_code in (303, 403)
        response = client.get('/api/v3/status', headers={'Authorization': 'Bearer service', 'X-Oko-Remember': '1'})
        assert response.status_code == 200 and 'set-cookie' not in response.headers
        assert client.get('/', follow_redirects=False).status_code == 303
        client.cookies.set('oko_browser', 'old-session')
        assert client.get('/api/v3/status').status_code == 403
        assert client.get('/api/v3/status', headers={b'Authorization': b'Bearer \xe9'}).status_code == 403
        page = client.get('/login')
        assert 'Логин' in page.text and 'Пароль' in page.text
        assert 'reader-shell' not in page.text and 'token' not in page.text


def test_site_icon_is_public_without_opening_reader_assets(tmp_path, credentials):
    app = setup(tmp_path, credentials)
    with TestClient(app) as client:
        page = client.get('/login')
        assert 'rel="apple-touch-icon"' in page.text
        icon = client.get('/apple-touch-icon.png', follow_redirects=False)
        assert icon.status_code == 200
        assert icon.headers['content-type'] == 'image/png'
        assert icon.content.startswith(b'\x89PNG\r\n\x1a\n')
        assert client.head('/apple-touch-icon.png', follow_redirects=False).status_code == 200
        assert client.post('/apple-touch-icon.png', follow_redirects=False).status_code == 403
        assert client.get('/index.html', follow_redirects=False).status_code == 303
        assert client.get('/api/v3/status', follow_redirects=False).status_code == 403
        assert login(client).status_code == 303
        assert 'rel="apple-touch-icon"' in client.get('/').text


def test_public_peer_cannot_claim_local_bearer_by_host_or_forwarded_headers(tmp_path, credentials):
    app = setup(tmp_path, credentials, public_url='https://reader.example')
    with TestClient(app, base_url='https://reader.example', client=('198.51.100.9', 50000)) as client:
        for host in ('localhost', '127.0.0.1', 'testserver'):
            response = client.get('/api/v3/status', headers={'Host':host, 'Authorization':'Bearer service',
                'X-Forwarded-For':'127.0.0.1', 'X-Forwarded-Host':'localhost'})
            assert response.status_code == 403


def test_two_logins_share_settings_but_logout_only_revokes_one(tmp_path, credentials):
    app = setup(tmp_path, credentials)
    with TestClient(app) as client:
        response = login(client)
        assert response.status_code == 303
        cookie = client.cookies.get('oko_access')
        assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
        assert cookie not in (tmp_path / 'access.json').read_text()
        assert PASSWORD not in (tmp_path / 'access.json').read_text()
        assert client.put('/api/v3/power', json={'enabled': False}, headers={'Origin': 'http://testserver'}).status_code == 200
        client.cookies.clear()
        assert login(client, 'bob').status_code == 303
        bob = client.cookies.get('oko_access')
        assert client.get('/api/v3/status').json()['runtime_state'] == 'disabled'
        assert client.post('/logout', headers={'Origin': 'http://testserver'}, follow_redirects=False).status_code == 303
        client.cookies.set('oko_access', bob)
        assert client.get('/api/v3/status').status_code == 403
        client.cookies.set('oko_access', cookie)
        assert client.get('/api/v3/status').status_code == 200
        PasswordAccess(tmp_path).set_user('alice', 'replacement-password')
        assert client.get('/api/v3/status').status_code == 403
        assert client.get('/login/session').status_code == 401


def test_https_origin_and_forwarded_headers_do_not_grant_access(tmp_path, credentials):
    app = setup(tmp_path, credentials, public_url='https://reader.example')
    with TestClient(app, base_url='https://reader.example') as client:
        assert login(client, origin='https://evil.example').status_code == 403
        assert login(client, origin='https://reader.example').status_code == 303
        assert 'Secure' in login(client, origin='https://reader.example').headers['set-cookie']
        for headers in [{'Origin': 'https://evil.example'}, {'Host': 'evil.example'}, {'Origin': 'http://reader.example'}]:
            assert client.get('/api/v3/status', headers=headers).status_code == 403
        assert client.put('/api/v3/power', json={'enabled': False}).status_code == 403
        assert client.get('/api/v3/status', headers={'X-Forwarded-Host': 'evil.example', 'X-Forwarded-Proto': 'http'}).status_code == 200
        client.cookies.clear()
        assert client.get('/api/v3/status', headers={'Authorization': 'Bearer service', 'X-Forwarded-Host': 'localhost'}).status_code == 403
        assert client.post('/login', data={'username': 'alice', 'password': PASSWORD}).status_code == 403


def test_rate_limit_includes_different_logins_and_survives_restart(tmp_path, credentials):
    app = setup(tmp_path, credentials)
    with TestClient(app) as client:
        for n in range(30):
            response = client.post('/login', data={'username': f'unknown{n}', 'password': 'wrong'}, headers={'Origin': 'http://testserver'})
            assert response.status_code == 401
            assert 'Неверный логин или пароль' in response.text
        assert login(client).status_code == 429
    with TestClient(create_app(tmp_path, persistent=True)) as restarted:
        assert login(restarted).status_code == 429


def test_expiry_disable_and_corruption_fail_closed(tmp_path, credentials):
    now = [100.0]
    app = setup(tmp_path, credentials, wall_clock=lambda: now[0])
    with TestClient(app) as client:
        assert login(client).status_code == 303
        now[0] += MAX_AGE + 1
        assert client.get('/api/v3/status').status_code == 403
        assert login(client).status_code == 303
        PasswordAccess(tmp_path).set_user('alice', None)
        assert client.get('/api/v3/status').status_code == 403
        (tmp_path / 'access.json').write_text('{damaged')
        assert login(client).status_code == 503
        assert client.get('/api/v3/status').status_code == 403
    (tmp_path / 'access.json').unlink()
    with TestClient(create_app(tmp_path, persistent=True)) as restarted:
        assert restarted.get('/api/v3/status').status_code == 403


def test_late_response_is_discarded_when_access_is_revoked(tmp_path, credentials):
    app = setup(tmp_path, credentials)

    @app.get('/api/v3/protected-stream')
    async def slow():
        async def body():
            yield b'allowed'
            PasswordAccess(tmp_path).set_user('alice', None)
            yield b'forbidden-late-data'
        return StreamingResponse(body())

    with TestClient(app) as client:
        assert login(client).status_code == 303
        response = client.get('/api/v3/protected-stream')
        assert b'forbidden-late-data' not in response.content


def test_unicode_password_roundtrip_through_urlencoded_form(tmp_path, credentials):
    app = setup(tmp_path, credentials)
    password = '\u754c' * 256
    PasswordAccess(tmp_path).set_user('unicode', password)
    with TestClient(app) as client:
        response = client.post('/login', data={'username': 'unicode', 'password': password},
                               headers={'Origin': 'http://testserver'}, follow_redirects=False)
        assert response.status_code == 303
        assert client.get('/api/v3/status').status_code == 200


@pytest.mark.parametrize(('owner', 'count'), [('alice', 16), ('bob', 128)])
def test_new_login_preserves_existing_sessions(tmp_path, credentials, owner, count):
    import hashlib
    now = 1000.0
    data = json.loads(json.dumps(credentials))
    revision = data['users'][owner]['revision']
    for number in range(count):
        cookie = f'old-{number}'
        data['sessions'][hashlib.sha256(cookie.encode()).hexdigest()] = {
            'user': owner, 'revision': revision, 'expires': now + 100 + number,
        }
    app = setup(tmp_path, data, wall_clock=lambda: now)
    with TestClient(app) as client:
        assert login(client).status_code == 303
        assert client.get('/login/session').status_code == 204
        client.cookies.set('oko_access', 'old-0')
        assert client.get('/login/session').status_code == 204


def test_repeated_successful_login_in_same_browser_is_not_throttled(tmp_path, credentials):
    app = setup(tmp_path, credentials, wall_clock=lambda: 1000.0)
    with TestClient(app) as client:
        for _ in range(6):
            assert login(client).status_code == 303
        assert client.get('/login/session').status_code == 204
        assert len(json.loads((tmp_path / 'access.json').read_text())['sessions']) == 1


def test_successful_login_does_not_erase_failed_attempts(tmp_path, credentials):
    app = setup(tmp_path, credentials, wall_clock=lambda: 1000.0)
    with TestClient(app) as client:
        for _ in range(4):
            assert client.post('/login', data={'username': 'alice', 'password': 'wrong'},
                               headers={'Origin': 'http://testserver'}).status_code == 401
        assert login(client).status_code == 303
        assert client.post('/login', data={'username': 'alice', 'password': 'wrong'},
                           headers={'Origin': 'http://testserver'}).status_code == 401
        assert login(client).status_code == 429


def test_successful_logins_remain_in_global_rate_limit(tmp_path, credentials):
    data = json.loads(json.dumps(credentials))
    data['attempts'] = [{'name': 'alice', 'at': 999.0, 'success': True} for _ in range(29)]
    app = setup(tmp_path, data, wall_clock=lambda: 1000.0)
    with TestClient(app) as client:
        assert login(client).status_code == 303
        assert login(client).status_code == 429
