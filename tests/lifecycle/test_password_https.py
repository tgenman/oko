"""Real HTTPS proxy, verified test certificate, HTTP reader, and temporary credentials."""
import contextlib
import http.server
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import httpx

from oko.reader.password_auth import PasswordAccess
from tests.support.processes import ServerProcess, cleanup


def test_password_reader_behind_https_proxy(tmp_path: Path):
    PasswordAccess(tmp_path).set_user('reader', 'synthetic-password')
    certificate, key = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    config = tmp_path / 'certificate.cnf'
    config.write_text('[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=localhost\n[ext]\nsubjectAltName=DNS:localhost\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n')
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-keyout', str(key), '-out', str(certificate), '-config', str(config)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    upstream = ['']

    class Proxy(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def proxy(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0))) if self.command == 'POST' else None
            request = urllib.request.Request(upstream[0] + self.path, data=body, method=self.command,
                headers={k: v for k, v in self.headers.items() if k.lower() not in ('connection', 'transfer-encoding', 'x-forwarded-host', 'x-forwarded-proto')})
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    return None
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            try:
                response = opener.open(request, timeout=5)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                content = response.read()
                self.send_response(response.status)
                for name, value in response.headers.items():
                    if name.lower() not in ('transfer-encoding', 'content-length', 'connection'):
                        self.send_header(name, value)
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)

        do_GET = proxy
        do_POST = proxy

    proxy = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate, key)
    proxy.socket = tls.wrap_socket(proxy.socket, server_side=True)
    external = f'https://localhost:{proxy.server_port}'
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    log_path = tmp_path / 'server.log'
    with log_path.open('w') as log:
        process = ServerProcess([sys.executable, '-m', 'tests.process_server', '--persistent', '--data', str(tmp_path),
                    '--demo', '--no-browser', '--initially-disabled', '--public-url', external], stdout=log, stderr=subprocess.STDOUT)
    process.log_path = log_path
    try:
        for _ in range(500):
            if (tmp_path / 'runtime.json').exists():
                break
            assert process.poll() is None, log_path.read_text()
            time.sleep(.02)
        metadata = json.loads((tmp_path / 'runtime.json').read_text())
        upstream[0] = metadata['control_url']
        with httpx.Client(base_url=external, verify=ssl.create_default_context(cafile=str(certificate)), trust_env=False) as client:
            assert client.get('/').status_code == 303
            assert client.get('/login').status_code == 200
            assert client.get('/api/v3/status', headers={'Authorization': 'Bearer ' + metadata['token']}).status_code == 403
            result = client.post('/login', data={'username': 'reader', 'password': 'synthetic-password'}, headers={'Origin': external})
            assert result.status_code == 303 and 'Secure' in result.headers['set-cookie']
            assert client.get('/api/v3/status').json()['runtime_state'] == 'disabled'
            assert client.post('/logout', headers={'Origin': external}).status_code == 303
            assert client.get('/api/v3/status').status_code == 403
    finally:
        cleanup(process)
        proxy.shutdown()
        proxy.server_close()
        thread.join(timeout=5)
