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


def test_http_stop_finishes_after_postgres_backend_termination(postgres_url, tmp_path):
    armed = False
    shutdown = threading.Event()
    def fault(point):
        if armed and point == 'settings_after_values':
            store = app.state.host.execution._store
            identity = store.db.execute('SELECT pg_backend_pid()').fetchone()[0]
            with psycopg.connect(postgres_url, autocommit=True) as administrator:
                assert administrator.execute('SELECT pg_terminate_backend(%s)', (identity,)).fetchone()[0]
            raise RuntimeError('synthetic interrupted settings write')
    app = create_app(tmp_path, token='test', api_only=True, database_url=postgres_url,
                     fault=fault, shutdown=shutdown.set)
    with TestClient(app, headers=HEADERS, raise_server_exceptions=False) as client:
        assert client.get('/api/v3/settings/oko/global').status_code == 200
        assert client.patch('/api/v3/settings/oko/global', json={'prompt_filters':'Committed'}).status_code == 200
        armed = True
        assert client.patch('/api/v3/settings/oko/global', json={'prompt_filters':'Pending'}).status_code == 500
        started = time.monotonic()
        assert client.post('/api/v3/stop').status_code == 200
        assert shutdown.wait(2)
        assert time.monotonic()-started < 2




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


def test_enabled_work_waits_for_recovery_then_runs_without_a_second_start(postgres_url, tmp_path):
    root = tmp_path/'reader'
    cards = library(root, postgres_url)
    credential = tmp_path/'database'
    credential.write_text(postgres_url);credential.chmod(0o600)
    slow_reconciliation(postgres_url)
    process = start(root, credential, demo=True)
    try:
        with closing(ready(root, process)) as client:
            assert client.put('/api/v3/power', json={'enabled':True}).status_code == 200
            # Demo collection is queued during initialization but must wait for
            # recovery; settings/external checks still have ordinary admission.
            time.sleep(.8)
            assert len(read_all_cards(client, '/api/v3/cards')) == len(cards)
            # Remove the external latency before testing ordinary collection;
            # the fixture targets recovery, not normal publication writes.
            with psycopg.connect(postgres_url, autocommit=True) as db:
                db.execute('DROP TRIGGER slow_recovery ON publications')
            deadline = time.monotonic()+20
            while time.monotonic() < deadline:
                if len(read_all_cards(client, '/api/v3/cards')) == len(cards)+3:
                    break
                time.sleep(.05)
            assert len(read_all_cards(client, '/api/v3/cards')) == len(cards)+3
            assert client.post('/api/v3/stop').status_code == 200
            assert process.wait(timeout=3) == 0
    finally:
        cleanup(process)


def test_global_settings_after_startup_queue_slow_reconciliation_without_blocking_reader(postgres_url, tmp_path):
    root = tmp_path/'reader'
    cards = library(root, postgres_url)
    credential = tmp_path/'database'
    credential.write_text(postgres_url);credential.chmod(0o600)
    process = start(root, credential, demo=True)
    try:
        with closing(ready(root, process)) as client:
            assert client.put('/api/v3/power', json={'enabled':True}).status_code == 200
            deadline = time.monotonic()+20
            while len(read_all_cards(client, '/api/v3/cards')) != len(cards)+3 and time.monotonic() < deadline:
                time.sleep(.05)
            assert len(read_all_cards(client, '/api/v3/cards')) == len(cards)+3
            # Collection proves the initial recovery finished and crons started.
            assert client.put('/api/v3/power', json={'enabled':False}).status_code == 200
            with psycopg.connect(postgres_url, autocommit=True) as db:
                db.execute('''CREATE FUNCTION slow_recovery() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN PERFORM pg_sleep(0.2); RETURN NEW; END $$''')
                db.execute('CREATE TRIGGER slow_recovery BEFORE UPDATE OF processing_metadata ON publications FOR EACH ROW EXECUTE FUNCTION slow_recovery()')
            started = time.monotonic()
            changed = client.patch('/api/v3/settings/oko/global', json={'prompt_filters':'Use the new rules'}, timeout=2)
            assert changed.status_code == 200
            assert time.monotonic()-started < 2
            assert client.get('/api/v3/settings/oko/global').json()['effective']['prompt_filters']['value'] == 'Use the new rules'
            assert client.get('/api/v3/cards').status_code == 200
            assert client.put('/api/v3/power', json={'enabled':True}).status_code == 200
            started = time.monotonic()
            assert client.post('/api/v3/materials/'+cards[0]['id']+'/filter/retry', timeout=2).status_code == 200
            assert time.monotonic()-started < 2
            assert client.post('/api/v3/materials/'+cards[0]['id']+'/viewed', timeout=3).status_code == 200
            assert client.post('/api/v3/stop').status_code == 200
            assert process.wait(timeout=3) == 0
    finally:
        cleanup(process)


    process = start(root, credential)
    try:
        with closing(ready(root, process)) as client:
            assert client.get('/api/v3/settings/oko/global').json()['effective']['prompt_filters']['value'] == 'Use the new rules'
            assert client.get('/api/v3/publication/'+cards[0]['id']).status_code == 404
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


def test_global_rules_change_stays_responsive_with_400_publications(postgres_url, tmp_path, record_property):
    from tests.support.completion_library import library as completion_library
    root = tmp_path/'library'
    completion_library(root,postgres_url,400)
    app = create_app(root,token='test',api_only=True,database_url=postgres_url)
    with TestClient(app,headers=HEADERS) as client:
        deadline = time.monotonic()+60
        while app.state.host.execution._store.processing.recovering and time.monotonic()<deadline:
            time.sleep(.05)
        assert not app.state.host.execution._store.processing.recovering
        statements = []
        store = app.state.host.execution._store
        client.portal.call(lambda: store.db.set_trace_callback(statements.append))
        try:
            started = time.monotonic()
            result = client.patch('/api/v3/settings/oko/global',json={'prompt_filters':'New synthetic rule'})
            elapsed = time.monotonic()-started
        finally:
            client.portal.call(lambda: store.db.set_trace_callback(None))
        assert result.status_code == 200, result.text
        assert elapsed < 2, elapsed
        record_property('global_rules_sql_calls', len(statements))
        record_property('global_rules_sql_budget_deferred', 100)
        print(f'Deferred global rules SQL budget: {len(statements)} calls (previous limit <100)')
