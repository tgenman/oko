"""Synthetic library and operator CLI shared by migration and recovery scenarios."""
from pathlib import Path
import subprocess
import sys

import httpx
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.reader import HEADERS, wait_for
from tests.support.rss import rss, wait_cards
from tests.support.subscriptions import add_source
from tests.support.youtube import PNG
from tests.support.filters import transport

ROOT = Path(__file__).resolve().parents[2]


def source_library(root, *, processing=False):
    def response(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=rss())
        if request.url.path == '/image.png':
            return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
        return httpx.Response(200, text='<article><p>Imported synthetic text.</p><img src="https://source.test/image.png"></article>')

    with TestClient(create_app(root, token='test', api_only=True, rss_enabled=True,
                    rss_transport=httpx.MockTransport(response), ai_transport=transport([], theses=True)), headers=HEADERS) as client:
        assert client.patch('/api/v3/settings/oko/global', json={'theses': False, 'prompt_filters': ''}).status_code == 200
        if processing:
            assert client.put('/api/v3/processors/llamacpp', json={'endpoint': 'http://localhost:9931', 'model': 'local:latest', 'local_execution': True}).status_code == 200
            assert client.patch('/api/v3/settings/oko/global', json={'processor': 'llamacpp', 'theses': True, 'prompt_filters': 'Keep useful text'}).status_code == 200
        feed = add_source(client, 'https://source.test/feed', 'Imported feed', 'Imported folder')
        assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
        item = wait_cards(client)[0]['id']
        material = wait_for(client, lambda: (value if (value := client.get('/api/v3/materials/'+item).json())['content']['attachments'] else None))
        media_url = material['content']['attachments'][0]['url']
        assert client.get(media_url).content == PNG
        if processing:
            material = wait_for(client, lambda: (value if (value := client.get('/api/v3/materials/'+item).json())['theses']['result'] else None))
    (root / 'instance.lock').touch(mode=0o600)
    return feed, item, media_url, material



def invoke(source, target, credential, report, *, checkout=ROOT):
    return subprocess.run([sys.executable, '-m', 'oko.storage.import_sqlite', '--source', str(source),
        '--target-data', str(target), '--database-url-file', str(credential), '--report', str(report)],
        cwd=checkout, capture_output=True, text=True, timeout=45)



def credential_file(tmp_path, postgres_url):
    credential = tmp_path / 'private-dsn'
    credential.write_text(postgres_url)
    credential.chmod(0o600)
    return credential
