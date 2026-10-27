"""Recovery preserves ready output without rewriting identical PostgreSQL rows."""
import httpx
import psycopg
import re
from fastapi.testclient import TestClient

from tests.support.subscriptions import add_source
from tests.support.filters import transport
from tests.support.rss import wait_cards
from tests.support.app import create_app
from tests.support.reader import HEADERS, wait_for
from tests.support.rss import rss
from tests.support.youtube import PNG


def source_library(root, database_url, *, processing=False):
    def response(request):
        if request.url.path == '/feed':
            return httpx.Response(200, text=rss())
        if request.url.path == '/image.png':
            return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
        return httpx.Response(200, text='<article><p>Imported synthetic text.</p><img src="https://source.test/image.png"></article>')

    with TestClient(create_app(root, token='test', api_only=True, rss_enabled=True, database_url=database_url,
                    rss_transport=httpx.MockTransport(response), ai_transport=transport([], theses=True)), headers=HEADERS) as client:
        assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
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
        assert client.put('/api/v3/power', json={'enabled': False}).status_code == 200
    return feed, item, media_url, material


def test_restart_does_not_rewrite_identical_materials(postgres_url, tmp_path):
    target = tmp_path/'library'
    _, item, media, original = source_library(target, postgres_url, processing=True)
    # A database-boundary guard makes write amplification fail visibly at the
    # real reader startup. It does not replace any application collaborator.
    with psycopg.connect(postgres_url, autocommit=True) as db:
        db.execute("""CREATE FUNCTION reject_unchanged_material() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN IF NEW IS NOT DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'synthetic unchanged material write';
          END IF; RETURN NEW; END $$""")
        db.execute('CREATE TRIGGER reject_unchanged_material BEFORE UPDATE ON items FOR EACH ROW EXECUTE FUNCTION reject_unchanged_material()')
    requests = []
    def response(request):
        requests.append(request.url.path)
        if request.url.path == '/feed':
            return httpx.Response(200, text=rss())
        if request.url.path == '/image.png':
            return httpx.Response(200, content=PNG, headers={'content-type': 'image/png'})
        return httpx.Response(200, text='<article><p>Imported synthetic text.</p><img src="https://source.test/image.png"></article>')

    with TestClient(create_app(target, token='test', api_only=True, database_url=postgres_url,
                    rss_enabled=True, rss_transport=httpx.MockTransport(response)), headers=HEADERS) as client:
        assert client.get('/api/v3/status').status_code == 200
        restored = client.get('/api/v3/materials/'+item)
        assert restored.status_code == 200
        assert restored.json()['theses']['result'] == original['theses']['result']
        assert client.get(media).content == PNG
        assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
        assert client.post('/api/v3/collect').status_code == 200
        # Workers are admitted only once recovery succeeded; an exception in
        # background recovery must not make this test appear green.
        wait_for(client, lambda: bool(requests))


def test_recovery_reuses_material_reads_within_each_read_phase(postgres_url, tmp_path, monkeypatch):
    target = tmp_path/'library'
    _, item, _, _ = source_library(target, postgres_url, processing=True)
    original_execute = psycopg.Connection.execute
    reads = []
    def execute(connection, query, parameters=None, **kwargs):
        if (isinstance(query, str) and re.search(r'^SELECT\s+\*\s+FROM\s+items(?:_read)?\s+WHERE\s+id\s*=', query, re.IGNORECASE)
                and parameters and parameters[0] == item):
            reads.append(query)
        return original_execute(connection, query, parameters, **kwargs)
    monkeypatch.setattr(psycopg.Connection, 'execute', execute)
    requests = []
    def response(request):
        requests.append(True)
        return httpx.Response(200, text='<rss version="2.0"><channel/></rss>')
    with TestClient(create_app(target, token='test', api_only=True, database_url=postgres_url,
                    rss_enabled=True, rss_transport=httpx.MockTransport(response)), headers=HEADERS) as client:
        assert client.get('/api/v3/status').status_code == 200
        assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
        assert client.post('/api/v3/collect').status_code == 200
        wait_for(client, lambda: bool(requests))
        assert reads, 'The database observer must see the material reads'
        assert len(reads) <= 12, f'{len(reads)} repeated point reads for one recovered material'
