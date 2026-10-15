"""Installed candidate evidence is exercised by real loopback server processes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tests.support.install_wheel import install_wheel

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def installed_wheel(tmp_path_factory):
    root = tmp_path_factory.mktemp('candidate-installed')
    stage = root / 'source'
    stage.mkdir()
    shutil.copytree(ROOT / 'oko', stage / 'oko', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (stage / 'oko/candidate.json').write_text(json.dumps({'status': 'UNREVIEWED', 'publication_allowed': False}))
    project = (ROOT / 'pyproject.toml').read_text().replace('include = [', 'include = [\n    {path="oko/candidate.json", format=["wheel"]},', 1)
    (stage / 'pyproject.toml').write_text(project)
    wheels = root / 'wheels'
    wheels.mkdir()
    subprocess.run([sys.executable, '-c', 'from poetry.core.masonry.api import build_wheel; import sys; build_wheel(sys.argv[1])', str(wheels)], cwd=stage, check=True)
    installed = root / 'installed'
    install_wheel(next(wheels.glob('*.whl')), installed)
    return installed


def smoke(installed, root):
    return subprocess.run([sys.executable, str(ROOT / 'scripts/candidate-install.py'), '--smoke-installed', str(installed), '--fixture', str(ROOT / 'tests/support/app.py')], cwd=root, env={**os.environ, 'PATH': '', 'PYTHONPATH': ''}, capture_output=True, text=True, timeout=30)


def test_installed_smoke_observes_http_and_process_stop_without_assets_or_node(installed_wheel, tmp_path):
    result = smoke(installed_wheel, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.splitlines()[-1])
    assert evidence['api_only']['http_status'] == 200
    assert evidence['api_only']['process_exit_code'] == 0
    assert evidence['api_only']['frontend_absent'] is True
    assert evidence['client_presence']['http_status'] == 200
    assert evidence['client_presence']['process_exit_code'] == 0
    assert evidence['status'] == 'UNREVIEWED'


def test_installed_smoke_fails_when_selected_contract_status_is_unimplemented(installed_wheel, tmp_path):
    installed = tmp_path / 'future-installed'
    shutil.copytree(installed_wheel, installed)
    contract = installed / 'oko/reader/generated/openapi.json'
    document = json.loads(contract.read_text())
    paths = document['paths']
    status = next(path for path, methods in paths.items() if methods.get('get', {}).get('operationId') == 'status')
    paths['/api/v3/unimplemented-status'] = paths.pop(status)
    contract.write_text(json.dumps(document))
    result = smoke(installed, tmp_path)
    assert result.returncode != 0
    assert '/api/v3/unimplemented-status' in result.stdout + result.stderr
    assert '404' in result.stdout + result.stderr


def test_v3_smoke_never_uses_legacy_status_even_if_it_is_available(installed_wheel, tmp_path):
    installed = tmp_path / 'mixed-installed'
    shutil.copytree(installed_wheel, installed)
    contract = installed / 'oko/reader/generated/openapi.json'
    document = json.loads(contract.read_text())
    document['info']['version'] = '3.0.0'
    status = next(path for path, methods in document['paths'].items() if methods.get('get', {}).get('operationId') == 'status')
    document['paths']['/api/status'] = document['paths'].pop(status)
    for method, path, identity in [('post', '/api/v3/client-sessions', 'create_client_session'),
                                   ('put', '/api/v3/client-sessions/{identity}', 'renew_client_session'),
                                   ('delete', '/api/v3/client-sessions/{identity}', 'close_client_session')]:
        document['paths'].setdefault(path, {})[method] = {'operationId': identity, 'responses': {'200': {'description': 'Synthetic session response'}}}
    contract.write_text(json.dumps(document))
    result = smoke(installed, tmp_path)
    assert result.returncode != 0
    assert 'Selected v3 operation uses unsupported API path: status /api/status' in result.stdout + result.stderr
