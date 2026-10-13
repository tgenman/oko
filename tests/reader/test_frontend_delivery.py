from pathlib import Path

from fastapi.testclient import TestClient

from tests.support.app import create_app


def test_frontend_nested_modules_survive_a_different_working_directory(tmp_path: Path, monkeypatch):
    frontend = tmp_path / 'frontend'
    (frontend / 'nested').mkdir(parents=True)
    (frontend / 'index.html').write_text('<script type="module" src="/nested/new.js"></script>')
    (frontend / 'nested/new.js').write_text('export const value = 42;')
    (frontend / 'style.css').write_text('body { color: black; }')
    monkeypatch.setattr('oko.reader.static.FRONTEND_ROOT', frontend)
    monkeypatch.chdir(tmp_path)
    with TestClient(create_app(tmp_path / 'data', token='secret')) as client:
        assert client.get('/').status_code == 200
        script = client.get('/nested/new.js')
        assert script.status_code == 200
        assert script.text == 'export const value = 42;'
        assert script.headers['content-type'].startswith('text/javascript')
        assert client.get('/style.css').headers['content-type'].startswith('text/css')
        assert script.headers['cache-control'] == 'no-store'
        assert "frame-ancestors 'none'" in script.headers['content-security-policy']
        assert client.get('/api/navigation', headers={'Authorization': 'Bearer secret'}).status_code == 200


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
    monkeypatch.setattr('oko.reader.static.FRONTEND_ROOT', frontend)
    with TestClient(create_app(tmp_path / 'data', token='secret')) as client:
        for path in ('/leak.txt', '/leak-directory/private.txt', '/%2e%2e/private.txt', '/%2Fetc/passwd',
                     '/missing', '/api/unknown', '/api', '/loop', '/%00', '/%2e', '/%2e/', '/%2e%2f'):
            response = client.get(path, headers={'Authorization': 'Bearer secret'})
            assert response.status_code == 404, path
            assert 'private bytes' not in response.text
            assert response.headers['content-type'].startswith('application/json')
        assert client.get('/index.html', headers={'Host': 'evil.example'}).status_code == 403
        assert client.get('/index.html', headers={'Origin': 'https://foreign.example'}).status_code == 403
