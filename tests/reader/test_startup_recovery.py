"""Reader readiness, responsiveness and stop do not wait for full-library recovery."""
from tests.support.cards import read_all_cards
import json
from contextlib import closing
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import httpx
import psycopg
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.processes import ServerProcess, cleanup
from tests.support.reader import HEADERS
from tests.support.rss import wait_cards
from tests.support.subscriptions import add_source


ROOT = Path(__file__).resolve().parents[2]


def library(root, dsn, count=32):
    entries = ''.join(f'<item><guid>{n}</guid><title>Saved {n}</title>'
                      f'<link>https://source.test/article/{n}</link><description>Saved text</description></item>'
                      for n in range(count))
    feed = '<rss version="2.0"><channel>'+entries+'</channel></rss>'
    def response(request):
        return httpx.Response(200, text=feed if request.url.path == '/feed' else '<article>Saved synthetic recovery text</article>')
    with TestClient(create_app(root, token='test', api_only=True, database_url=dsn,
                    rss_enabled=True, rss_transport=httpx.MockTransport(response)), headers=HEADERS) as client:
        assert client.patch('/api/v3/settings/oko/global', json={'theses':False,'prompt_filters':''}).status_code == 200
        assert client.put('/api/v3/power', json={'enabled':True}).status_code == 200
        add_source(client, 'https://source.test/feed', count=count)
        cards = wait_cards(client, count)
        assert client.put('/api/v3/power', json={'enabled':False}).status_code == 200
    return cards


def start(root, credential, *, demo=False):
    log = root.parent/'startup.log'
    with log.open('ab') as output:
        process = ServerProcess([sys.executable,'-m','tests.process_server','--persistent','--no-browser', *(['--demo'] if demo else []),
            '--host','127.0.0.1','--port','0','--data',str(root),'--database-url-file',str(credential)],
            cwd=ROOT, env={**os.environ,'PYTHONPATH':str(ROOT)}, stdout=output, stderr=subprocess.STDOUT)
    process.log_path = log
    return process


def ready(root, process, timeout=3):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        assert process.poll() is None, process.log_path.read_text()
        try:
            meta = json.loads((root/'runtime.json').read_text())
            client = httpx.Client(base_url=meta['url'], headers={'Authorization':'Bearer '+meta['token']}, timeout=2)
            if client.get('/api/v3/status').status_code == 200:
                return client
            client.close()
        except (OSError, ValueError, httpx.HTTPError):
            pass
        time.sleep(.02)
    raise AssertionError('Reader readiness waited for the full slow reconciliation')


def slow_reconciliation(dsn):
    # The external database deliberately makes each material reconciliation slow.
    # This is not a mocked application method: the real CLI and HTTP server run.
    with psycopg.connect(dsn, autocommit=True) as db:
        # A restored snapshot needs revision reconciliation even when no model
        # work is enabled; every saved root must receive its missing revision.
        db.execute("UPDATE publications SET processing_metadata='{}' WHERE viewed=0")
        db.execute('''CREATE FUNCTION slow_recovery() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN PERFORM pg_sleep(0.2); RETURN NEW; END $$''')
        db.execute('CREATE TRIGGER slow_recovery BEFORE UPDATE OF processing_metadata ON publications FOR EACH ROW EXECUTE FUNCTION slow_recovery()')


def test_reader_opens_reads_and_stops_while_database_reconciliation_is_slow(postgres_url, tmp_path):
    root = tmp_path/'reader'
    cards = library(root, postgres_url)
    credential = tmp_path/'database'
    credential.write_text(postgres_url);credential.chmod(0o600)
    slow_reconciliation(postgres_url)
    process = start(root, credential)
    try:
        with closing(ready(root, process)) as client:
            for _ in range(4):
                started = time.monotonic()
                assert client.get('/api/v3/cards').status_code == 200
                assert client.get('/api/v3/publication/'+cards[0]['id']).status_code == 200
                assert time.monotonic()-started < 3
            started = time.monotonic()
            assert client.post('/api/v3/stop').status_code == 200
            assert process.wait(timeout=3) == 0
            assert time.monotonic()-started < 3
    finally:
        cleanup(process)


def test_settings_and_viewed_during_recovery_survive_an_early_stop_and_restart(postgres_url, tmp_path):
    root = tmp_path/'reader'
    cards = library(root, postgres_url)
    credential = tmp_path/'database'
    credential.write_text(postgres_url);credential.chmod(0o600)
    slow_reconciliation(postgres_url)
    process = start(root, credential)
    try:
        with closing(ready(root, process)) as client:
            started = time.monotonic()
            assert client.patch('/api/v3/settings/oko/global', json={'prompt_filters':'Keep useful text'}).status_code == 200
            assert client.get('/api/v3/settings/oko/global').json()['effective']['prompt_filters']['value'] == 'Keep useful text'
            assert client.post('/api/v3/materials/'+cards[0]['id']+'/viewed', timeout=3).status_code == 200
            assert time.monotonic()-started < 4
            assert client.post('/api/v3/stop').status_code == 200
            assert process.wait(timeout=3) == 0
    finally:
        cleanup(process)
    process = start(root, credential)
    try:
        with closing(ready(root, process)) as client:
            assert client.get('/api/v3/settings/oko/global').json()['effective']['prompt_filters']['value'] == 'Keep useful text'
            assert client.get('/api/v3/publication/'+cards[0]['id']).status_code == 404
            assert len(read_all_cards(client, '/api/v3/cards')) == len(cards)-1
            assert client.get('/api/v3/status').json()['runtime_state'] == 'disabled'
            assert client.post('/api/v3/stop').status_code == 200
            assert process.wait(timeout=3) == 0
    finally:
        cleanup(process)


def test_background_reconciliation_failure_closes_work_admission_and_can_stop(postgres_url, tmp_path):
    root = tmp_path/'reader'
    cards = library(root, postgres_url, count=1)
    credential = tmp_path/'database'
    credential.write_text(postgres_url);credential.chmod(0o600)
    with psycopg.connect(postgres_url, autocommit=True) as db:
        db.execute("UPDATE publications SET processing_metadata='{}' WHERE id=%s", (cards[0]['id'],))
        db.execute("""CREATE FUNCTION failed_recovery() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'synthetic private recovery failure'; END $$""")
        db.execute('CREATE TRIGGER failed_recovery BEFORE UPDATE OF processing_metadata ON publications FOR EACH ROW EXECUTE FUNCTION failed_recovery()')
    process = start(root, credential, demo=True)
    try:
        deadline = time.monotonic()+10
        while not (root/'runtime.json').exists() and time.monotonic()<deadline:
            assert process.poll() is None, process.log_path.read_text()
            time.sleep(.05)
        meta = json.loads((root/'runtime.json').read_text())
        with httpx.Client(base_url=meta['url'],headers={'Authorization':'Bearer '+meta['token']},timeout=2) as client:
            deadline = time.monotonic()+5
            response = client.get('/api/v3/status')
            while response.status_code == 200 and time.monotonic() < deadline:
                time.sleep(.05)
                response = client.get('/api/v3/status')
            assert response.status_code == 503
            assert 'synthetic private recovery failure' not in response.text
            assert client.post('/api/v3/collect').status_code == 503
            assert client.post('/api/v3/stop').status_code == 200
            assert process.wait(timeout=3) == 0
    finally:
        cleanup(process)


def test_startup_removes_owned_obsolete_telegram_staging_only(tmp_path, postgres_url):
    from tests.support.storage import Store
    root = tmp_path/'library'
    old = root/'staging'/'_telegram'
    old.mkdir(parents=True)
    (old/'media.bin').write_bytes(b'synthetic temporary media')
    session = tmp_path/'outside.session'
    session.write_bytes(b'external session')
    store = Store(root, lambda _: None, database_url=postgres_url)
    try:
        assert not old.exists()
        assert session.read_bytes() == b'external session'
    finally:
        store.db.close()


def test_obsolete_telegram_staging_symlink_never_removes_external_files(tmp_path, postgres_url):
    from tests.support.storage import Store
    root = tmp_path/'library'
    staging = root/'staging'
    staging.mkdir(parents=True)
    outside = tmp_path/'outside'
    outside.mkdir()
    (outside/'session').write_bytes(b'external session')
    (staging/'_telegram').symlink_to(outside, target_is_directory=True)
    store = Store(root, lambda _: None, database_url=postgres_url)
    try:
        assert not (staging/'_telegram').exists()
        assert (outside/'session').read_bytes() == b'external session'
    finally:
        store.db.close()
