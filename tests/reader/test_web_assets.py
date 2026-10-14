import re
from urllib.parse import urljoin

from fastapi.testclient import TestClient

from tests.support.app import create_app


def test_reader_serves_entire_browser_module_graph_without_exposing_reader_data(tmp_path):
    with TestClient(create_app(tmp_path, token='test')) as client:
        page = client.get('/')
        assert page.status_code == 200
        entry = re.search(r'<script[^>]*\bsrc="([^"]+)"[^>]*>', page.text)
        assert entry
        pending = [entry.group(1)]
        for path in re.findall(r'<link[^>]*rel="stylesheet"[^>]*href="([^"]+)"', page.text):
            css = client.get(path)
            assert css.status_code == 200
            assert css.headers['content-type'].startswith('text/css')
        visited = set()
        while pending:
            path = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.headers['content-type'].startswith('text/javascript'), path
            for relative in re.findall(r"(?:\bfrom\s*|\bimport\s*)['\"]([^'\"]+)['\"]", response.text):
                pending.append(urljoin(path, relative))
        assert client.get('/api/subscriptions').status_code == 403
        assert client.get('/api/subscriptions', headers={'Authorization':'Bearer test'}).status_code == 200
