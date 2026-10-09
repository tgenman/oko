from pathlib import Path
from fastapi.testclient import TestClient
from oko.reader.app import create_app


def test_foreign_pages_cannot_read_or_mutate(tmp_path: Path):
    with TestClient(create_app(tmp_path, token='secret')) as client:
        assert client.get('/api/cards').status_code == 403
        assert client.post('/api/stop', headers={'Authorization': 'Bearer secret', 'Origin': 'https://foreign.example'}).status_code == 403
        assert client.get('/api/cards', headers={'Authorization': 'Bearer secret', 'Host': 'evil.example'}).status_code == 403
        response = client.get('/', headers={'Authorization': 'Bearer secret'})
        assert response.headers['cache-control'] == 'no-store'
        assert "frame-ancestors 'none'" in response.headers['content-security-policy']
