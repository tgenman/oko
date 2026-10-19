"""Bridge checks use real HTTP and synthetic AI, never personal credentials."""
import asyncio
from contextlib import contextmanager
from pathlib import Path
import socket
import threading
import time

import httpx
import uvicorn

from oko.processing.codex_bridge import BridgeConfig, RemoteCodex, create_app
from oko.processing.execution import CodexChoice


class SyntheticCodex:
    async def check(self):
        return {'available': True, 'limits_checked': True}

    async def catalog(self):
        return [{'model': 'synthetic', 'reasoning_efforts': ['low'], 'fast_supported': False}]

    async def complete(self, prompt, schema, directory, choice):
        return '{"text":"synthetic result"}'


@contextmanager
def running_bridge(tmp_path, factory=SyntheticCodex):
    secret = tmp_path / 'token'
    secret.write_text('x' * 48)
    secret.chmod(0o600)
    config = BridgeConfig(token_file=secret, scratch_root=tmp_path / 'scratch', release='test')
    app = create_app(config, adapter_factory=factory)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(.01)
    assert server.started
    try:
        yield f'http://127.0.0.1:{port}'
    finally:
        server.should_exit = True
        thread.join(10)
        assert not thread.is_alive()


def test_remote_codex_preserves_contract_over_real_http(tmp_path):
    with running_bridge(tmp_path) as url:
        async def scenario():
            client = RemoteCodex(url, 'x' * 48)
            assert (await client.check())['available'] is True
            assert (await client.catalog())[0]['model'] == 'synthetic'
            result = await client.complete('synthetic prompt', {'type': 'object'}, tmp_path / 'unused',
                                           CodexChoice(model='synthetic', reasoning_effort='low'))
            assert result == '{"text":"synthetic result"}'
        asyncio.run(scenario())


def job_headers(url):
    import uuid
    headers = {'Authorization': 'Bearer ' + 'x' * 48, 'X-Oko-Protocol': '1',
               'X-Oko-Session': uuid.uuid4().hex}
    with httpx.Client(base_url=url, headers=headers, trust_env=False) as client:
        health = client.get('/v1/health').json()
    headers['X-Oko-Boot'] = health['boot_id']
    return headers


def test_private_protocol_rejects_browser_bad_secret_version_and_unbounded_content(tmp_path):
    with running_bridge(tmp_path) as url, httpx.Client(base_url=url, trust_env=False) as client:
        assert client.get('/v1/health').status_code == 401
        headers = job_headers(url)
        assert client.get('/v1/health', headers=dict(headers, Origin='http://oko')).status_code == 403
        assert client.get('/v1/health', headers=dict(headers, **{'X-Oko-Protocol': '2'})).status_code == 409
        assert client.put('/v1/jobs/' + 'a' * 32, headers=headers, content='x' * 1_048_577).status_code == 413
        body = dict(boot_id=headers['X-Oko-Boot'], session_id=headers['X-Oko-Session'],
                    operation='complete', deadline=time.time() + 10,
                    prompt='synthetic', schema={'type': 'object'},
                    choice={'model': 'synthetic', 'reasoning_effort': 'low'}, command='sh')
        assert client.put('/v1/jobs/' + 'a' * 32, headers=headers, json=body).status_code == 422
        del body['command']
        body['schema'] = {'type': 'unknown'}
        assert client.put('/v1/jobs/' + 'a' * 32, headers=headers, json=body).status_code == 422
        body['schema'] = {'$ref': 'https://example.invalid/schema'}
        assert client.put('/v1/jobs/' + 'a' * 32, headers=headers, json=body).status_code == 422
        nested = {}
        for _ in range(50):
            nested = {'items': nested}
        body['schema'] = nested
        assert client.put('/v1/jobs/' + 'a' * 32, headers=headers, json=body).status_code == 422


def test_attempt_replay_is_deduplicated_and_release_keeps_tombstone(tmp_path):
    with running_bridge(tmp_path) as url:
        headers = job_headers(url)
        with httpx.Client(base_url=url, headers=headers, trust_env=False) as client:
            body = dict(boot_id=headers['X-Oko-Boot'], session_id=headers['X-Oko-Session'],
                        operation='check', deadline=time.time() + 30)
            path = '/v1/jobs/' + 'a' * 32
            assert client.put(path, json=body).json()['state'] == 'running'
            assert client.put(path, json=body).status_code == 200
            assert client.put(path, json=dict(body, operation='catalog')).status_code == 409
            for _ in range(200):
                state = client.get(path).json()
                if state['state'] != 'running':
                    break
                time.sleep(.01)
            assert state['result']['available'] is True
            assert client.delete(path).json()['state'] == 'released'
            assert client.put(path, json=body).json() == {'state': 'released'}
            assert client.post(path + '/lease').status_code == 409
            alien = dict(headers, **{'X-Oko-Session': 'b' * 32})
            assert client.get(path, headers=alien).status_code == 403


class SleepingCodex(SyntheticCodex):
    async def complete(self, prompt, schema, directory, choice):
        import os
        import signal
        import sys
        process = await asyncio.create_subprocess_exec(sys.executable, '-c',
            'import time; time.sleep(120)', start_new_session=True)
        Path(prompt).write_text(str(process.pid))
        try:
            await process.wait()
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
        return 'never'


def wait_file(path):
    for _ in range(300):
        if path.exists():
            return int(path.read_text())
        time.sleep(.01)
    raise AssertionError('synthetic subprocess did not start')


def assert_stopped(pid):
    import os
    for _ in range(300):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(.01)
    raise AssertionError('owned process survived cancellation')


def test_cancel_stops_owned_process_before_acknowledging_and_releases_slot(tmp_path):
    with running_bridge(tmp_path, SleepingCodex) as url:
        async def scenario():
            remote = RemoteCodex(url, 'x' * 48)
            pid_file = tmp_path / 'child.pid'
            task = asyncio.create_task(remote.complete(str(pid_file), {}, tmp_path / 'unused',
                CodexChoice(model='synthetic', reasoning_effort='low')))
            pid = await asyncio.to_thread(wait_file, pid_file)
            assert (await RemoteCodex(url, 'x' * 48).check())['available'] is False
            task.cancel()
            result = await asyncio.gather(task, return_exceptions=True)
            assert isinstance(result[0], asyncio.CancelledError)
            await asyncio.to_thread(assert_stopped, pid)
            assert (await remote.check())['available'] is True
        asyncio.run(scenario())


def test_absolute_deadline_expires_attempt_and_rejects_late_renewal(tmp_path):
    with running_bridge(tmp_path, SleepingCodex) as url:
        headers = job_headers(url)
        with httpx.Client(base_url=url, headers=headers, trust_env=False) as client:
            path = '/v1/jobs/' + 'c' * 32
            pid_file = tmp_path / 'expired.pid'
            body = dict(boot_id=headers['X-Oko-Boot'], session_id=headers['X-Oko-Session'],
                        operation='complete', deadline=time.time() + 2, prompt=str(pid_file),
                        schema={}, choice={'model': 'synthetic', 'reasoning_effort': 'low'})
            assert client.put(path, json=body).status_code == 200
            pid = wait_file(pid_file)
            time.sleep(2)
            assert client.post(path + '/lease').status_code == 409
            assert_stopped(pid)
            assert client.get(path).json()['state'] == 'released'


def test_lost_creation_response_resolves_same_attempt_without_second_put(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    with running_bridge(tmp_path) as url:
        puts = []
        class LosingProxy(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def forward(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                with httpx.Client(trust_env=False) as client:
                    response = client.request(self.command, url + self.path,
                                              headers=dict(self.headers), content=body)
                if self.command == 'PUT':
                    puts.append(self.path)
                    self.close_connection = True
                    return
                self.send_response(response.status_code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(response.content)))
                self.end_headers()
                self.wfile.write(response.content)
            do_GET = do_PUT = do_POST = do_DELETE = forward
        proxy = ThreadingHTTPServer(('127.0.0.1', 0), LosingProxy)
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        try:
            remote = RemoteCodex(f'http://127.0.0.1:{proxy.server_port}', 'x' * 48)
            assert asyncio.run(remote.check())['available'] is True
            assert len(puts) == 1
        finally:
            proxy.shutdown()
            proxy.server_close()
            thread.join(5)


def serve_synthetic(config_data):
    config = BridgeConfig.model_validate(config_data)
    uvicorn.run(create_app(config, adapter_factory=SleepingCodex),
                host='127.0.0.1', port=config.port, log_level='error', access_log=False)


def test_helper_parent_crash_eof_stops_own_cli_and_cleans_scratch(tmp_path):
    import multiprocessing
    import os
    import signal
    secret = tmp_path / 'token'
    secret.write_text('x' * 48)
    secret.chmod(0o600)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    config = BridgeConfig(port=port, token_file=secret, scratch_root=tmp_path / 'scratch', release='test')
    helper = multiprocessing.get_context('spawn').Process(target=serve_synthetic,
                                                          args=(config.model_dump(),))
    helper.start()
    url = f'http://127.0.0.1:{port}'
    pid = None
    try:
        headers = None
        for _ in range(300):
            try:
                headers = job_headers(url)
                break
            except httpx.HTTPError:
                time.sleep(.01)
        assert headers
        with httpx.Client(base_url=url, headers=headers, trust_env=False) as client:
            pid_file = tmp_path / 'orphan.pid'
            body = dict(boot_id=headers['X-Oko-Boot'], session_id=headers['X-Oko-Session'],
                        operation='complete', deadline=time.time() + 60, prompt=str(pid_file),
                        schema={}, choice={'model': 'synthetic', 'reasoning_effort': 'low'})
            assert client.put('/v1/jobs/' + 'd' * 32, json=body).status_code == 200
            pid = wait_file(pid_file)
            os.kill(helper.pid, signal.SIGKILL)
            helper.join(5)
            assert_stopped(pid)
            for _ in range(300):
                if not (config.scratch_root / 'active.json').exists():
                    break
                time.sleep(.01)
            assert not (config.scratch_root / 'active.json').exists()
            assert not list(config.scratch_root.glob('job-*'))
    finally:
        if helper.is_alive():
            helper.kill()
        helper.join(5)
        if pid:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_supervisor_crash_blocks_new_work_until_owned_process_cleanup(tmp_path):
    import json
    import os
    import signal
    with running_bridge(tmp_path, SleepingCodex) as url:
        headers = job_headers(url)
        pid_file = tmp_path / 'supervisor-child.pid'
        with httpx.Client(base_url=url, headers=headers, trust_env=False) as client:
            body = dict(boot_id=headers['X-Oko-Boot'], session_id=headers['X-Oko-Session'],
                        operation='complete', deadline=time.time() + 60, prompt=str(pid_file),
                        schema={}, choice={'model': 'synthetic', 'reasoning_effort': 'low'})
            assert client.put('/v1/jobs/' + 'e' * 32, json=body).status_code == 200
            pid = wait_file(pid_file)
            marker = tmp_path / 'scratch' / 'active.json'
            owner = json.loads(marker.read_text())
            try:
                os.kill(owner['supervisor'], signal.SIGKILL)
                for _ in range(200):
                    old = client.get('/v1/jobs/' + 'e' * 32).json()
                    if old['state'] != 'running':
                        break
                    time.sleep(.01)
                assert old['state'] == 'error'
                next_body = dict(boot_id=headers['X-Oko-Boot'], session_id=headers['X-Oko-Session'],
                                 operation='check', deadline=time.time() + 30)
                assert client.put('/v1/jobs/' + 'f' * 32, json=next_body).status_code == 503
                assert marker.exists()
            finally:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
