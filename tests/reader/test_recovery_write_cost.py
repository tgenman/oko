"""Recovery preserves ready output without rewriting identical PostgreSQL rows."""
import httpx
import psycopg
import re
from fastapi.testclient import TestClient

from tests.support.sqlite_import import source_library, invoke, credential_file
from tests.support.app import create_app
from tests.support.reader import HEADERS, wait_for
from tests.support.rss import rss
from tests.support.youtube import PNG


def test_restart_does_not_rewrite_identical_materials(postgres_url, tmp_path):
    source = tmp_path/'source'
    _, item, media, original = source_library(source, processing=True)
    target = tmp_path/'target'
    imported = invoke(source, target, credential_file(tmp_path, postgres_url), tmp_path/'report.json')
    assert imported.returncode == 0, imported.stderr
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
    source = tmp_path/'source'
    _, item, _, _ = source_library(source, processing=True)
    target = tmp_path/'target'
    imported = invoke(source, target, credential_file(tmp_path, postgres_url), tmp_path/'report.json')
    assert imported.returncode == 0, imported.stderr
    original_execute = psycopg.Connection.execute
    reads = []
    def execute(connection, query, parameters=None, **kwargs):
        if (isinstance(query, str) and re.search(r'^SELECT\s+\*\s+FROM\s+items\s+WHERE\s+id\s*=', query, re.IGNORECASE)
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
