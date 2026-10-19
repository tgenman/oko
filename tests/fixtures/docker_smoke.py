#!/usr/bin/env python3
"""Opt-in Docker Desktop/Mac integration using temporary data and synthetic AI.

Run with .venv/bin/python tests/fixtures/docker_smoke.py --image IMAGE.
No host DNS, launchd, credentials or personal sources are touched.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import httpx
import uvicorn
from oko.processing.codex_bridge import BridgeConfig, create_app
from tests.support.codex import CodexService


def docker(*args):
    result = subprocess.run(['docker', '--context', 'desktop-linux', *map(str, args)],
                            check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.strip()


def smoke(image, evidence):
    os.umask(0o077)
    evidence.mkdir(parents=True, exist_ok=True)
    report = {'image': docker('image', 'inspect', '--format', '{{.Id}}', image), 'checks': []}
    (evidence / 'report.json').write_text(json.dumps(report, indent=2))
    work = Path(tempfile.mkdtemp(prefix='oko-docker-synthetic-'))
    name = 'oko-synthetic-' + uuid.uuid4().hex[:12]
    server = None
    thread = None
    try:
        data = work / 'data'
        data.mkdir(mode=0o700)
        secret = work / 'codex'
        secret.write_text(secrets.token_urlsafe(48))
        secret.chmod(0o600)
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        bridge_port = sock.getsockname()[1]
        app = create_app(BridgeConfig(token_file=secret, scratch_root=work / 'scratch', release='synthetic-docker'),
                         adapter_factory=CodexService)
        server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level='error'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        for _ in range(200):
            if server.started:
                break
            time.sleep(.01)
        assert server.started, 'synthetic Mac bridge failed to start'
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            reader_port = listener.getsockname()[1]
        report['host_ports'] = {'reader': reader_port, 'bridge_loopback': bridge_port}
        arguments = ['--persistent', '--host', '0.0.0.0', '--port', '8765', '--public-url', 'http://oko',
                     '--codex-url', f'http://host.docker.internal:{bridge_port}',
                     '--codex-token-file', '/run/secrets/codex', '--data', '/data', '--no-browser', '--initially-disabled']

        def launch():
            return docker('run', '--detach', '--name', name, '--platform', 'linux/arm64',
                          '--user', f'{os.getuid()}:{os.getgid()}', '--read-only', '--cap-drop', 'ALL',
                          '--security-opt', 'no-new-privileges', '--tmpfs', '/tmp:mode=1777',
                          '--publish', f'127.0.0.1:{reader_port}:8765', '--volume', str(data) + ':/data',
                          '--volume', str(secret) + ':/run/secrets/codex:ro', image, *arguments)

        def wait_status(client):
            for _ in range(240):
                try:
                    runtime = json.loads((data / 'runtime.json').read_text())
                    response = client.get('/api/v3/status', headers={'Authorization': 'Bearer ' + runtime['token']})
                    if response.status_code == 200:
                        return runtime, response
                except (OSError, ValueError, httpx.HTTPError):
                    pass
                time.sleep(.1)
            raise AssertionError('container reader did not become ready')

        launch()
        with httpx.Client(base_url=f'http://127.0.0.1:{reader_port}', headers={'Host': 'oko'},
                          trust_env=False, timeout=15) as client:
            runtime, status = wait_status(client)
            assert status.json()['runtime_state'] == 'disabled', status.text
            assert client.get('/').status_code == 200
            report['checks'].append('reader persistent UI and initially disabled Store ready')
            assert client.get('/api/v3/status', headers={'Host': 'evil.invalid'}).status_code == 403
            assert client.get('/api/v3/status', headers={'Origin': 'http://evil.invalid'}).status_code == 403
            assert client.get('/api/v3/status').status_code == 200  # remembered cookie from first status
            client.cookies.clear()
            assert client.get('/api/v3/status').status_code == 403
            status = client.get('/api/v3/status', headers={'Authorization': 'Bearer ' + runtime['token']})
            assert status.status_code == 200 and 'HttpOnly' in status.headers['set-cookie']
            assert client.get('/api/v3/status').status_code == 200
            report['checks'].append('clean client denied; bearer establishes separate remembered cookie; Host and Origin rejected')
            client.headers['Authorization'] = 'Bearer ' + runtime['token']
            # Explicitly enable only an empty synthetic store to allow settings commands.
            assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
            catalog = client.get('/api/v3/processors/codex/models')
            assert catalog.status_code == 200, catalog.text
            assert catalog.json()['models'][0]['model'] == 'synthetic', catalog.text
            configuration = {kind: {'model': 'synthetic', 'reasoning_effort': 'low', 'fast': False}
                             for kind in ('filter', 'theses')}
            configured = client.put('/api/v3/processors/codex', json=configuration)
            assert configured.status_code == 200, configured.text
            checked = client.post('/api/v3/processors/codex/check')
            assert checked.status_code == 200, checked.text
            assert checked.json()['state'] == 'available', checked.text
            report['checks'].append('reader HTTP catalog/configure/check reach loopback Mac synthetic Codex over host.docker.internal')
            prompt = 'Synthetic request\n' + json.dumps({'sources': [{'id': 'synthetic-source', 'url': 'https://example.invalid/synthetic'}]})
            code = '\n'.join([
                'import asyncio,json; from pathlib import Path',
                'from oko.processing.codex_bridge import RemoteCodex',
                'from oko.processing.execution import CodexChoice',
                'async def run():',
                f' c=RemoteCodex("http://host.docker.internal:{bridge_port}",Path("/run/secrets/codex").read_text())',
                f' result=await c.complete({prompt!r},{{"type":"object"}},Path("/tmp/unused"),CodexChoice(model="synthetic",reasoning_effort="low"))',
                ' assert json.loads(result)["points"][0]["sources"][0]["id"] == "synthetic-source"',
                ' print("synthetic complete returned validated source")',
                'asyncio.run(run())'])
            result = docker('exec', name, 'python', '-c', code)
            assert 'validated source' in result
            report['checks'].append('Linux RemoteCodex.complete receives synthetic result from supervised Mac job')
            assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
            cookie = dict(client.cookies)
            token = runtime['token']
            docker('stop', '--time', '30', name)
            docker('rm', name)
            launch()
            runtime, status = wait_status(client)
            assert runtime['token'] != token
            client.headers.pop('Authorization', None)
            client.cookies.clear()
            client.cookies.update(cookie)
            status = client.get('/api/v3/status')
            assert status.status_code == 200 and status.json()['runtime_state'] == 'disabled', status.text
            settings = client.get('/api/v3/processors/codex')
            assert settings.status_code == 200 and settings.json()['configuration'] == configuration, settings.text
            report['checks'].append('stop/recreate preserves disabled state, saved processor choices and browser cookie; process bearer rotates')
        report['result'] = 'PASS'
    except Exception as error:
        report['result'] = 'FAIL'
        report['error'] = str(error)
        try:
            (evidence / 'container.log').write_text(docker('logs', name))
        except subprocess.CalledProcessError:
            pass
        raise
    finally:
        subprocess.run(['docker', '--context', 'desktop-linux', 'rm', '--force', name],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if server:
            server.should_exit = True
        if thread:
            thread.join(10)
        (evidence / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        shutil.rmtree(work)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='oko-runtime-verification')
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    smoke(args.image, args.evidence)
