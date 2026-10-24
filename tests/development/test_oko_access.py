"""Administrative commands change only the selected temporary access store."""
import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
from tests.support.app import create_app

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/oko-access'


def test_generated_access_is_private_revocable_and_not_printed(tmp_path):
    data, credential = tmp_path / 'data', tmp_path / 'password.txt'
    def command(*args):
        return subprocess.run([sys.executable, str(SCRIPT), '--data', str(data), *args], capture_output=True, text=True, timeout=15)
    created = command('set', 'reader', '--generate-to', str(credential))
    assert created.returncode == 0, created.stderr
    password = credential.read_text().strip()
    assert len(password) >= 24 and password not in created.stdout + created.stderr
    assert credential.stat().st_mode & 0o777 == 0o600
    assert (data / 'access.json').stat().st_mode & 0o777 == 0o600
    assert password not in (data / 'access.json').read_text()
    before = (data / 'access.json').read_bytes()
    assert command('set', 'reader', '--generate-to', str(credential)).returncode != 0
    assert (data / 'access.json').read_bytes() == before
    assert command('list').stdout.strip() == 'reader'
    with TestClient(create_app(data, persistent=True)) as client:
        assert client.post('/login', data={'username': 'reader', 'password': password}, headers={'Origin': 'http://testserver'}, follow_redirects=False).status_code == 303
        assert command('disable', 'reader').returncode == 0
        assert client.get('/api/v3/status').status_code == 403
    assert command('origin', 'http://unsafe.example').returncode != 0
    assert command('origin', 'https://reader.example').returncode == 0
    assert json.loads((data / 'access.json').read_text())['origin'] == 'https://reader.example'
