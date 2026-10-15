"""The import host must release live HTTP servers when its owner goes away."""
import json
from pathlib import Path
import select
import subprocess
import sys

import httpx
import pytest


@pytest.mark.parametrize('shutdown', ['eof', 'terminate', 'startup-failure'])
def test_browser_host_releases_live_scenarios(tmp_path, shutdown):
    process = subprocess.Popen([sys.executable, '-m', 'tests.support.browser_host'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, cwd=Path(__file__).resolve().parents[2])
    try:
        origins = []
        for identity in (1, 2):
            process.stdin.write(json.dumps(dict(id=identity, op='start', data=str(tmp_path / str(identity)), token=str(identity))) + '\n')
            process.stdin.flush()
            assert select.select([process.stdout], [], [], 15)[0], 'No startup reply'
            reply = json.loads(process.stdout.readline())
            assert 'error' not in reply, reply
            origins.append(reply['origin'])
            assert httpx.get(reply['origin']).status_code == 200
        if shutdown == 'eof':
            process.stdin.close()
        elif shutdown == 'terminate':
            process.terminate()
        else:
            process.stdin.write(json.dumps(dict(id=3, op='start', data=None, token='3')) + '\n')
            process.stdin.flush()
            assert select.select([process.stdout], [], [], 15)[0], 'No failure reply'
            assert 'error' in json.loads(process.stdout.readline())
        assert process.wait(timeout=10) == (1 if shutdown == 'startup-failure' else 0), process.stderr.read()
        for origin in origins:
            with pytest.raises(httpx.ConnectError):
                httpx.get(origin, timeout=1)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()


def test_browser_host_shutdown_does_not_wait_for_unfinished_request(tmp_path):
    """An interrupted browser upload must not strand fixture cleanup."""
    import socket
    from urllib.parse import urlsplit
    process = subprocess.Popen([sys.executable, '-m', 'tests.support.browser_host'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, cwd=Path(__file__).resolve().parents[2])
    connection = None
    try:
        process.stdin.write(json.dumps(dict(id=1, op='start', data=str(tmp_path), token='test')) + '\n')
        process.stdin.flush()
        assert select.select([process.stdout], [], [], 15)[0]
        reply = json.loads(process.stdout.readline())
        origin = urlsplit(reply['origin'])
        connection = socket.create_connection((origin.hostname, origin.port), timeout=5)
        connection.sendall((f'PATCH /api/v3/settings/oko/global HTTP/1.1\r\nHost: {origin.netloc}\r\n'
                            'Authorization: Bearer test\r\nContent-Type: application/json\r\n'
                            'Content-Length: 1000\r\nExpect: 100-continue\r\n\r\n').encode())
        assert b'100 Continue' in connection.recv(4096)
        # Keep the request body open until after the host has shut down.
        process.stdin.close()
        assert process.wait(timeout=10) == 0, process.stderr.read()
        with pytest.raises(httpx.ConnectError):
            httpx.get(reply['origin'], timeout=1)
    finally:
        if connection:
            connection.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
