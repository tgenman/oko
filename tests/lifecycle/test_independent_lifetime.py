"""Independent execution observed through the reader HTTP interface."""
import time

from fastapi.testclient import TestClient

from oko.collection.demo import DemoSource
from tests.support.app import create_app
from tests.support.reader import Clock, wait_for

HEADERS = {'Authorization': 'Bearer test'}


def test_explicit_execution_collects_without_presence_until_stop(tmp_path):
    clock = Clock()
    exits = []
    with TestClient(create_app(tmp_path, token='test', source=DemoSource(), clock=clock,
                               api_only=True, shutdown=lambda: exits.append(True)), headers=HEADERS) as client:
        cards = wait_for(client, lambda: client.get('/api/cards').json())
        assert len(cards) == 3
        clock.now += 100
        time.sleep(.05)
        assert not client.get('/api/status').json()['stopped']
        assert exits == []
        assert client.post('/api/stop').status_code == 200
        wait_for(client, lambda: exits)
        assert client.get('/api/status').json()['stopped']


def test_api_only_process_runs_without_frontend_or_node_and_reuses_own_profile(tmp_path):
    import json
    import os
    from pathlib import Path
    import shutil
    import subprocess
    import sys

    from tests.support.processes import ServerProcess, cleanup, connect, wait_cards

    checkout = Path(__file__).resolve().parents[2]
    installed = tmp_path / 'installed'
    shutil.copytree(checkout / 'oko', installed / 'oko',
                    ignore=shutil.ignore_patterns('frontend_build', '__pycache__'))
    root = tmp_path / 'data'
    output = tmp_path / 'server.log'
    environment = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(installed), str(checkout))), 'PATH': ''}
    command = [sys.executable, '-m', 'tests.process_server', '--data', str(root), '--api-only', '--demo']
    with output.open('w') as log:
        process = ServerProcess(command, cwd=tmp_path, env=environment, stdout=log, stderr=subprocess.STDOUT)
    process.log_path = output
    try:
        with connect(root, process) as client:
            assert len(wait_cards(client, 3)) == 3
            assert client.get('/').status_code == 404
            second = subprocess.run(command, cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=10)
            assert second.returncode == 0, second.stdout + second.stderr
            assert 'explicit' in second.stdout
            assert json.loads((root / 'runtime.json').read_text())['pid'] == process.pid
            # A regular reader cannot silently change a running explicit instance.
            different = subprocess.run([part for part in command if part != '--api-only'] + ['--no-browser'],
                                       cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=10)
            assert different.returncode != 0
            assert 'explicit' in different.stderr
            assert client.post('/api/stop').status_code == 200
        assert process.wait(timeout=3) == 0
    finally:
        cleanup(process)


def test_neutral_sessions_expire_and_cannot_be_revived(tmp_path):
    clock = Clock()
    exits = []
    with TestClient(create_app(tmp_path, token='test', clock=clock, shutdown=lambda: exits.append(True)), headers=HEADERS) as client:
        created = client.post('/api/v3/client-sessions')
        assert created.status_code == 201, created.text
        first = created.json()
        assert first['renew_after_seconds'] == 2
        assert first['expires_at'].endswith('Z')
        second = client.post('/api/v3/client-sessions').json()
        assert second['id'] != first['id']
        assert client.delete('/api/v3/client-sessions/' + first['id']).status_code == 200
        clock.now += 5
        assert client.put('/api/v3/client-sessions/' + second['id']).status_code == 200
        clock.now += 7
        expired = client.put('/api/v3/client-sessions/' + second['id'])
        assert expired.status_code == 410
        assert expired.json()['code'] == 'client_session_expired'
        replacement = client.post('/api/v3/client-sessions')
        assert replacement.status_code == 201
        assert replacement.json()['id'] not in (first['id'], second['id'])
        assert exits == []


def test_v3_explicit_status_security_and_header_validation_do_not_execute_stop(tmp_path):
    with TestClient(create_app(tmp_path, token='test', api_only=True)) as client:
        assert client.get('/api/v3/status').json()['code'] == 'session_required'
        assert client.get('/api/v3/status', headers={**HEADERS, 'Origin': 'http://foreign.test'}).json()['code'] == 'origin_not_allowed'
        state = client.get('/api/v3/status', headers=HEADERS)
        assert state.status_code == 200, state.text
        assert state.json()['lifetime'] == {'kind': 'explicit'}
        for method, path in [('post', '/api/v3/client-sessions'), ('put', '/api/v3/client-sessions/never'), ('delete', '/api/v3/client-sessions/never')]:
            response = getattr(client, method)(path, headers=HEADERS)
            assert response.status_code == 409
            assert response.json()['code'] == 'lifetime_policy_incompatible'
        response = client.post('/api/v3/stop', headers={**HEADERS, 'X-Oko-Action': 'secret value'})
        assert response.status_code == 422
        assert 'secret value' not in response.text
        assert client.get('/api/v3/status', headers=HEADERS).json()['runtime_state'] == 'running'
        response = client.post('/api/v3/stop', headers={**HEADERS, 'X-Oko-Action': 'stop-command'})
        assert response.status_code == 200
        assert response.json() == {'ok': True, 'command_id': 'stop-command'}
        assert client.get('/api/v3/status', headers=HEADERS).json()['runtime_state'] == 'stopped'


def test_neutral_last_close_cancels_resistant_external_wait_and_prevents_late_commit(tmp_path):
    import asyncio
    import threading
    from oko.collection.contracts import Material, Publication

    started, release = threading.Event(), threading.Event()
    class Source:
        async def discover(self, cursor):
            return [Publication('late', 'https://synthetic.test/late', 100)], 'done'

        async def fetch(self, publication):
            started.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(.005)
                except asyncio.CancelledError:
                    continue
            return Material('Late', 'Must never be committed')

    with TestClient(create_app(tmp_path, token='test', source=Source()), headers=HEADERS) as client:
        session = client.post('/api/v3/client-sessions').json()
        try:
            assert started.wait(2)
            response = client.delete('/api/v3/client-sessions/' + session['id'])
            assert response.status_code == 200
            assert client.get('/api/v3/status').json()['work_allowed'] is False
        finally:
            release.set()
        time.sleep(.05)
        cards = client.get('/api/cards').json()
        assert all(not card['complete'] for card in cards)
        assert 'Must never be committed' not in client.get('/api/cards').text
