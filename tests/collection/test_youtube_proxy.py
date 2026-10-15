"""Real SDK + real worker against owned HTTP/CONNECT fixtures only."""
import asyncio
import base64
from contextlib import contextmanager
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

import pytest

from oko.collection.youtube.source import LocalCaptions
from oko.collection.youtube.access import CaptionsBlocked


@pytest.fixture
def certificate(tmp_path):
    config = tmp_path / 'openssl.cnf'
    config.write_text('[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=youtube.test\n[ext]\nsubjectAltName=DNS:youtube.test,DNS:captions.test\nbasicConstraints=critical,CA:TRUE\n')
    cert, key = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-config', str(config), '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return cert, context


@contextmanager
def server(*, tls=None, outcome='ok', retry_after='120', caption_origin='http://captions.test', reject_proxy=False):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_CONNECT(self):
            calls.append(('CONNECT', self.path, self.headers.get('Proxy-Authorization')))
            if reject_proxy:
                self.send_error(407)
                return
            assert self.path in ('youtube.test:443', 'captions.test:443')
            self.send_response(200)
            self.end_headers()
            self.connection = tls.wrap_socket(self.connection, server_side=True)
            self.rfile = self.connection.makefile('rb')
            self.wfile = self.connection.makefile('wb')
            self.handle_one_request()
            self.close_connection = True

        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            self.do_GET()

        def do_GET(self):
            calls.append((self.command, self.path, self.headers.get('Proxy-Authorization')))
            path = urlsplit(self.path).path
            status = 200
            if outcome == '429':
                status, body = 429, 'rate limit'
            elif path == '/watch':
                body = '"INNERTUBE_API_KEY": "fixture"'
            elif path == '/player':
                playability = {'status': 'LOGIN_REQUIRED', 'reason': "Sign in to confirm you’re not a bot"} if outcome == 'blocked' else {'status': 'OK'}
                body = json.dumps({'playabilityStatus': playability, 'captions': {'playerCaptionsTracklistRenderer': {
                    'captionTracks': [{'baseUrl': caption_origin + '/captions?signature=private-signed-url', 'name': {'runs': [{'text': 'Russian'}]}, 'languageCode': 'ru'}]}}})
            elif outcome == 'caption-error':
                status, body = 500, 'private-signed-url'
            else:
                body = '<transcript><text start="0" dur="1">Синтетические субтитры</text></transcript>'
            raw = body.encode()
            self.send_response(status)
            if status == 429:
                self.send_header('Retry-After', retry_after)
            self.send_header('Content-Type', 'text/xml; charset=utf-8' if path == '/captions' else 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    instance = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=instance.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{instance.server_port}', calls
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()


def sdk_environment(tmp_path, monkeypatch, origin, cert=None):
    # The SDK/parser/requests implementation remains real. Change only endpoint
    # addresses and the fixture certificate authority. Forbid external sockets.
    customization = tmp_path / 'fixture'
    customization.mkdir()
    (customization / 'sitecustomize.py').write_text(f'''
import socket
original_connect = socket.socket.connect
def connect(sock, address):
    if address[0] not in ('127.0.0.1', '::1'):
        raise RuntimeError('External socket prohibited in caption test')
    return original_connect(sock, address)
socket.socket.connect = connect
import youtube_transcript_api._transcripts as transcripts
transcripts.WATCH_URL = {origin + '/watch?v={video_id}'!r}
transcripts.INNERTUBE_API_URL = {origin + '/player?key={api_key}'!r}
'''+ (f'''
import requests
original_init = requests.Session.__init__
def init(session):
    original_init(session)
    session.verify = {str(cert)!r}
requests.Session.__init__ = init
''' if cert else ''))
    monkeypatch.setenv('PYTHONPATH', str(customization) + os.pathsep + str(Path.cwd()))


def worker(proxy=None):
    result = subprocess.run([sys.executable, '-m', 'oko.collection.youtube.worker', 'abcdefghijk'],
                            input=json.dumps({'proxy': proxy}), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout), result.stdout + result.stderr


def test_real_sdk_https_requests_use_authenticated_connect_proxy(tmp_path, monkeypatch, certificate):
    cert, tls = certificate
    sdk_environment(tmp_path, monkeypatch, 'https://youtube.test', cert)
    with server(tls=tls, caption_origin='https://captions.test') as (origin, calls):
        proxy = origin.replace('://', '://private-user:private-password@')
        monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
        monkeypatch.setenv('NO_PROXY', '*')
        # Exercise the production parent pipe too; credentials never appear in argv.
        text = asyncio.run(LocalCaptions(proxy).fetch('abcdefghijk'))
        assert text == 'Синтетические субтитры'
        auth = 'Basic ' + base64.b64encode(b'private-user:private-password').decode()
        connects = [entry for entry in calls if entry[0] == 'CONNECT']
        assert len(connects) == 3
        assert all(entry[2] == auth for entry in connects)
        assert {entry[1] for entry in connects} == {'youtube.test:443', 'captions.test:443'}


def test_real_sdk_http_proxy_and_direct_mode_ignore_environment(tmp_path, monkeypatch):
    with server() as (direct, direct_calls), server() as (proxy, proxy_calls), server() as (environment, environment_calls):
        sdk_environment(tmp_path, monkeypatch, direct)
        monkeypatch.setenv('HTTP_PROXY', environment)
        monkeypatch.setenv('HTTPS_PROXY', environment)
        monkeypatch.setenv('ALL_PROXY', environment)
        # Proxy serves all three replies without forwarding to the direct server.
        result, _ = worker(proxy)
        assert result['text'] == 'Синтетические субтитры'
        assert len(proxy_calls) == 3 and not direct_calls and not environment_calls
        # Direct mode still ignores the environment; fail at the synthetic caption
        # hostname rather than reaching any external socket or ambient proxy.
        result, _ = worker()
        assert result['error'] and len(direct_calls) == 2 and not environment_calls


@pytest.mark.parametrize('retry_after', ['120', 'invalid', 'date'])
def test_real_429_is_not_retried_and_retry_after_crosses_worker_pipe(tmp_path, monkeypatch, retry_after):
    expected = 120 if retry_after != 'invalid' else 0
    value = formatdate(time.time() + 120, usegmt=True) if retry_after == 'date' else retry_after
    sdk_environment(tmp_path, monkeypatch, 'http://youtube.test')
    with server(outcome='429', retry_after=value) as (proxy, calls):
        with pytest.raises(CaptionsBlocked) as caught:
            asyncio.run(LocalCaptions(proxy).fetch('abcdefghijk'))
        assert expected - 2 <= caught.value.retry_after <= expected
        assert len(calls) == 1


def test_sdk_bot_response_is_classified_as_blocked(tmp_path, monkeypatch):
    sdk_environment(tmp_path, monkeypatch, 'http://youtube.test')
    with server(outcome='blocked') as (proxy, calls):
        result, _ = worker(proxy)
        assert result.get('blocked'), result
        assert len(calls) == 2


def test_proxy_error_has_no_direct_fallback_or_secret_output(tmp_path, monkeypatch, certificate):
    cert, tls = certificate
    sdk_environment(tmp_path, monkeypatch, 'https://youtube.test', cert)
    with server(tls=tls, reject_proxy=True) as (origin, calls):
        result, raw = worker(origin.replace('://', '://private-user:private-password@'))
        assert result['error'] and not result.get('blocked')
        assert len(calls) == 1
        assert 'private-user' not in raw and 'private-password' not in raw
        assert 'External socket prohibited' not in raw  # No attempted direct fallback.


def test_signed_caption_url_never_leaves_worker(tmp_path, monkeypatch):
    sdk_environment(tmp_path, monkeypatch, 'http://youtube.test')
    with server(outcome='caption-error') as (proxy, calls):
        result, raw = worker(proxy)
        assert result['error'] and not result.get('blocked')
        assert len(calls) == 3
        assert 'private-signed-url' not in raw and 'http://' not in raw
