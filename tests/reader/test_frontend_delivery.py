from pathlib import Path
import json
import hashlib
import shutil

from fastapi.testclient import TestClient

from tests.support.app import create_app


def publish_fixture(frontend):
    from oko.reader.static import FRONTEND_ROOT
    manifest = json.loads((FRONTEND_ROOT / 'current.json').read_text())
    generation = frontend / 'generations' / ('a' * 64)
    generation.mkdir(parents=True)
    for path in list(frontend.iterdir()):
        if path.name != 'generations':
            shutil.move(str(path), generation / path.name)
    assets = {p.relative_to(generation).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in generation.rglob('*') if p.is_file() and not p.is_symlink() and not any(part.startswith('leak') for part in p.relative_to(generation).parts)}
    manifest.update(generation='a' * 64, assets=assets)
    (frontend / 'current.json').write_text(json.dumps(manifest))


def test_frontend_rejects_traversal_symlinks_and_unknown_data_paths(tmp_path: Path, monkeypatch):
    frontend = tmp_path / 'frontend'
    (frontend / 'api').mkdir(parents=True)
    (frontend / 'api/unknown').write_text('must not serve data paths')
    (frontend / 'index.html').write_text('reader')
    secret = tmp_path / 'private.txt'
    secret.write_text('private bytes')
    (frontend / 'leak.txt').symlink_to(secret)
    (frontend / 'leak-directory').symlink_to(tmp_path, target_is_directory=True)
    (frontend / 'loop').symlink_to('loop')
    publish_fixture(frontend)
    monkeypatch.setattr('oko.reader.static.FRONTEND_ROOT', frontend)
    with TestClient(create_app(tmp_path / 'data', token='secret')) as client:
        for path in ('/leak.txt', '/leak-directory/private.txt', '/%2e%2e/private.txt', '/%2Fetc/passwd',
                     '/missing', '/api/v3/unknown', '/api', '/loop', '/%00', '/%2e', '/%2e/', '/%2e%2f'):
            response = client.get(path, headers={'Authorization': 'Bearer secret'})
            assert response.status_code == (410 if path == '/api' else 404), path
            assert 'private bytes' not in response.text
            assert response.headers['content-type'].startswith('application/json')
        assert client.get('/index.html', headers={'Host': 'evil.example'}).status_code == 403
        assert client.get('/index.html', headers={'Origin': 'https://foreign.example'}).status_code == 403
