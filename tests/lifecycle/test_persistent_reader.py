"""Persistent web execution and remembered authentication at reader HTTP."""
from pathlib import Path
import time

from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.reader import Clock
from oko.reader.password_auth import PasswordAccess


def test_remembered_login_survives_restart_and_can_be_revoked(tmp_path: Path):
    PasswordAccess(tmp_path).set_user('reader', 'synthetic-password')
    options = dict(persistent=True, public_url='http://oko')
    with TestClient(create_app(tmp_path, token='bootstrap', **options), base_url='http://oko', client=('127.0.0.1', 50000)) as client:
        assert client.get('/api/v3/status').status_code == 403
        # Health/readiness probes must not allocate browser credentials.
        for _ in range(3):
            probe = client.get('/api/v3/status', headers={'Authorization': 'Bearer bootstrap', 'Host': 'localhost'})
            assert probe.status_code == 200
            assert 'set-cookie' not in probe.headers
        login = client.post('/login', data={'username': 'reader', 'password': 'synthetic-password'}, headers={'Origin': 'http://oko'}, follow_redirects=False)
        assert login.status_code == 303
        first_instance = client.get('/api/v3/status').headers['X-Oko-Instance']
        assert 'HttpOnly' in login.headers['set-cookie']
        assert 'SameSite=strict' in login.headers['set-cookie']
        cookie = client.cookies.get('oko_access')
        assert cookie and cookie != 'bootstrap'
        assert client.get('/api/v3/status').status_code == 200
        assert client.put('/api/v3/power', json={'enabled': False}).status_code == 403
        assert client.put('/api/v3/power', json={'enabled': False}, headers={'Origin': 'http://foreign'}).status_code == 403
        assert client.put('/api/v3/power', json={'enabled': False}, headers={'Origin': 'http://oko'}).status_code == 200
        assert client.get('/api/v3/status', headers={'Authorization': 'Bearer wrong'}).status_code == 403
        assert client.get('/api/v3/status', headers={'Host': 'foreign'}).status_code == 403
    # Bootstrap credentials rotate on process restart; browser session is separate.
    with TestClient(create_app(tmp_path, token='new-bootstrap', **options), base_url='http://oko') as restarted:
        restarted.cookies.set('oko_access', cookie)
        status = restarted.get('/api/v3/status')
        assert status.status_code == 200
        assert status.headers['X-Oko-Instance'] != first_instance
        assert status.json()['runtime_state'] == 'disabled'
        assert status.json()['lifetime'] == {'kind': 'explicit'}
        revoked = restarted.delete('/api/v3/browser-session', headers={'Origin': 'http://oko'})
        assert revoked.status_code == 200
        restarted.cookies.set('oko_access', cookie)
        assert restarted.get('/api/v3/status').status_code == 403
    saved = (tmp_path / 'access.json').read_text()
    assert cookie not in saved and 'bootstrap' not in saved


def test_persistent_web_stays_available_without_tabs_and_without_enabling_work(tmp_path: Path):
    clock = Clock()
    exits = []
    with TestClient(create_app(tmp_path, token='test', persistent=True, clock=clock,
                               shutdown=lambda: exits.append(True)), headers={'Authorization': 'Bearer test'}, client=('127.0.0.1', 50000)) as client:
        assert client.get('/').status_code == 200
        assert 'Логин' in client.get('/').text
        assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
        clock.now += 100
        time.sleep(.05)
        assert client.get('/api/v3/status').json()['runtime_state'] == 'disabled'
        assert exits == []


def test_persistent_process_starts_disabled_and_reuses_external_address(tmp_path: Path):
    import json
    import os
    import subprocess
    import sys
    import httpx
    from tests.support.processes import ServerProcess, cleanup, connect

    command = [sys.executable, '-m', 'tests.process_server', '--persistent', '--data', str(tmp_path),
               '--demo', '--no-browser', '--initially-disabled', '--public-url', 'http://oko']
    log_path = tmp_path / 'server.log'
    with log_path.open('w') as log:
        process = ServerProcess(command, stdout=log, stderr=subprocess.STDOUT)
    process.log_path = log_path
    try:
        for _ in range(300):
            if (tmp_path / 'runtime.json').exists():
                break
            assert process.poll() is None, log_path.read_text()
            time.sleep(.02)
        metadata = json.loads((tmp_path / 'runtime.json').read_text())
        assert metadata['url'] == 'http://oko'
        with httpx.Client(base_url=metadata['control_url'], headers={'Authorization': 'Bearer '+metadata['token']}, trust_env=False) as client:
            assert client.get('/api/v3/status').json()['runtime_state'] == 'disabled'
            assert client.get('/', follow_redirects=False).status_code == 303
            assert subprocess.run(command, capture_output=True, timeout=10).returncode == 0
            assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
            assert client.post('/api/v3/stop').status_code == 200
        assert process.wait(timeout=5) == 0
    finally:
        cleanup(process)
    with log_path.open('w') as log:
        process = ServerProcess(command, stdout=log, stderr=subprocess.STDOUT)
    process.log_path = log_path
    try:
        for _ in range(300):
            if (tmp_path / 'runtime.json').exists():
                break
            assert process.poll() is None, log_path.read_text()
            time.sleep(.02)
        metadata = json.loads((tmp_path / 'runtime.json').read_text())
        with httpx.Client(base_url=metadata['control_url'], headers={'Authorization': 'Bearer '+metadata['token']}, trust_env=False) as client:
            assert client.get('/api/v3/status').json()['runtime_state'] == 'running'
    finally:
        cleanup(process)
