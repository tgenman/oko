"""The actual CLI terminates TLS; local control verifies identity on loopback."""
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import time

import httpx
import pytest

from oko.reader.control import status
from oko.reader.password_auth import PasswordAccess
from tests.support.processes import ServerProcess, cleanup


def certificate(root: Path) -> tuple[Path, Path]:
    cert, key = root / 'cert.pem', root / 'key.pem'
    config = root / 'cert.cnf'
    config.write_text('[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=localhost\n[ext]\nsubjectAltName=DNS:localhost\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n')
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-keyout', str(key), '-out', str(cert), '-config', str(config)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return cert, key


def test_native_tls_login_control_and_certificate_validation(tmp_path):
    cert, key = certificate(tmp_path)
    data = tmp_path / 'data'
    PasswordAccess(data).set_user('reader', 'synthetic-password')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    origin = f'https://localhost:{port}'
    from tests.support.storage import Store
    seed = Store(data, lambda _: None)
    seed.set_work_enabled(False)
    seed.db.close()
    log_path = tmp_path / 'server.log'
    with log_path.open('w') as log:
        process = ServerProcess([sys.executable, '-m', 'tests.process_server', '--persistent', '--demo',
            '--data', str(data), '--no-browser', '--port', str(port),
            '--public-url', origin, '--tls-cert', str(cert), '--tls-key', str(key)], stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(500):
            if (data / 'runtime.json').exists(): break
            assert process.poll() is None, log_path.read_text()
            time.sleep(.02)
        metadata = json.loads((data / 'runtime.json').read_text())
        assert status(metadata, ca_file=cert)['runtime_state'] == 'disabled'
        with pytest.raises(ssl.SSLCertVerificationError):
            status(metadata)
        with pytest.raises(ssl.SSLCertVerificationError):
            status(dict(metadata, control_url=f'https://wrong.example:{port}'), ca_file=cert)
        with httpx.Client(base_url=origin, verify=ssl.create_default_context(cafile=str(cert)), trust_env=False) as client:
            assert client.get('/login').status_code == 200
            assert client.get('/api/v3/status', headers={'Authorization': 'Bearer '+metadata['token']}).status_code == 403
            response = client.post('/login', data={'username':'reader', 'password':'synthetic-password'}, headers={'Origin':origin})
            assert response.status_code == 303 and 'Secure' in response.headers['set-cookie']
            assert client.get('/api/v3/status').json()['runtime_state'] == 'disabled'
            assert client.post('/logout', headers={'Origin':'https://wrong.example'}).status_code == 403
            assert client.post('/logout', headers={'Origin':origin}).status_code == 303
    finally:
        cleanup(process)


@pytest.mark.parametrize('arguments', [ ['--tls-cert','missing'], ['--tls-key','missing'],
    ['--tls-cert','missing','--tls-key','missing','--public-url','http://localhost'],
    ['--tls-cert','missing','--tls-key','missing','--public-url','https://localhost'] ])
def test_invalid_tls_fails_before_creating_data(tmp_path, arguments):
    data = tmp_path / 'data'
    result = subprocess.run([sys.executable, '-m', 'oko', '--data', str(data), *arguments], capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert not data.exists()
