"""Offline operator CLI preserves a library across the SQLite/PostgreSQL boundary."""
import hashlib
import json
import fcntl
from pathlib import Path
import sqlite3
import subprocess
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.support.app import create_app
from tests.support.reader import HEADERS, wait_for
from tests.support.rss import rss, wait_cards
from tests.support.subscriptions import add_source
from tests.support.youtube import PNG
from tests.support.filters import transport


ROOT = Path(__file__).resolve().parents[2]
EMPTY_LEGACY_TABLES = ('content_current', 'content_versions', 'filter_restores',
                       'retained_theses', 'telegram_read_topics', 'youtube_attempts')


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


def test_cli_preserves_historical_rows_in_private_archive(postgres_url, tmp_path):
    source = tmp_path / 'sqlite'
    source_library(source)
    with sqlite3.connect(source/'state.sqlite') as db:
        db.execute('CREATE TABLE migration_retired_rows (identity TEXT, payload BLOB)')
        db.execute('INSERT INTO migration_retired_rows VALUES (?,?)', ('old-job', b'historical\x00data'))
    target, report = tmp_path / 'postgres', tmp_path / 'report.json'
    result = invoke(source, target, credential_file(tmp_path, postgres_url), report)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(report.read_text())['archived_tables'] == {'migration_retired_rows': 1}
    archive = target/'migration-archive/state.sqlite'
    assert archive.stat().st_mode & 0o077 == 0
    with sqlite3.connect(archive) as preserved:
        assert preserved.execute('SELECT * FROM migration_retired_rows').fetchall() == [('old-job', b'historical\x00data')]


def test_cli_import_archives_known_empty_legacy_tables(postgres_url, tmp_path):
    source = tmp_path/'sqlite'
    _, item, media_url, _ = source_library(source)
    with sqlite3.connect(source/'state.sqlite') as db:
        for table in EMPTY_LEGACY_TABLES:
            db.execute('CREATE TABLE '+table+' (value TEXT)')
    target, report = tmp_path/'postgres', tmp_path/'report.json'
    result = invoke(source, target, credential_file(tmp_path, postgres_url), report)
    assert result.returncode == 0, result.stdout+result.stderr
    assert json.loads(report.read_text())['archived_tables'] == {table: 0 for table in EMPTY_LEGACY_TABLES}
    with sqlite3.connect(target/'migration-archive/state.sqlite') as archive:
        archived = {row[0] for row in archive.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert set(EMPTY_LEGACY_TABLES) <= archived
    with TestClient(create_app(target, token='test', api_only=True, database_url=postgres_url), headers=HEADERS) as client:
        assert [card['id'] for card in client.get('/api/v3/cards').json()] == [item]
        assert client.get(media_url).content == PNG


@pytest.mark.parametrize('topic', [None, 7])
def test_cli_accepts_legacy_topic_column_only_when_all_values_are_null(postgres_url, tmp_path, topic):
    source = tmp_path/'sqlite'
    _, item, media_url, _ = source_library(source)
    with sqlite3.connect(source/'state.sqlite') as db:
        db.execute('ALTER TABLE feeds ADD COLUMN telegram_topic INTEGER'+(' DEFAULT 7' if topic is not None else ''))
    before = hashlib.sha256((source/'state.sqlite').read_bytes()).hexdigest()
    target, report = tmp_path/'postgres', tmp_path/'report.json'
    result = invoke(source, target, credential_file(tmp_path, postgres_url), report)
    assert hashlib.sha256((source/'state.sqlite').read_bytes()).hexdigest() == before
    if topic is not None:
        assert result.returncode != 0
        assert not target.exists()
        assert not report.exists()
        return
    assert result.returncode == 0, result.stdout+result.stderr
    assert json.loads(report.read_text())['archived_columns'] == {'feeds': ['telegram_topic']}
    with sqlite3.connect(target/'migration-archive/state.sqlite') as archive:
        assert archive.execute('SELECT telegram_topic FROM feeds').fetchall() == [(None,)]
    with TestClient(create_app(target, token='test', api_only=True, database_url=postgres_url), headers=HEADERS) as client:
        assert [card['id'] for card in client.get('/api/v3/cards').json()] == [item]
        assert client.get(media_url).content == PNG


@pytest.mark.parametrize('table', EMPTY_LEGACY_TABLES)
def test_cli_refuses_nonempty_legacy_table_without_archiving_live_data(postgres_url, tmp_path, table):
    source = tmp_path/'sqlite'
    source_library(source)
    with sqlite3.connect(source/'state.sqlite') as db:
        db.execute('CREATE TABLE '+table+' (value TEXT)')
        db.execute('INSERT INTO '+table+' VALUES (?)', ('must remain live data',))
    before = hashlib.sha256((source/'state.sqlite').read_bytes()).hexdigest()
    target, report = tmp_path/'postgres', tmp_path/'report.json'
    result = invoke(source, target, credential_file(tmp_path, postgres_url), report)
    assert result.returncode != 0
    assert not target.exists()
    assert not report.exists()
    assert hashlib.sha256((source/'state.sqlite').read_bytes()).hexdigest() == before


@pytest.mark.parametrize('conflict', ['live_source', 'symlink', 'columns', 'unknown_table'])
def test_cli_refuses_unsafe_source_without_publishing_partial_library(postgres_url, tmp_path, conflict):
    source = tmp_path / 'sqlite'
    source_library(source)
    if conflict == 'symlink':
        (source/'secret-link').symlink_to(tmp_path/'private-dsn')
    elif conflict == 'columns':
        with sqlite3.connect(source/'state.sqlite') as db:
            db.execute('ALTER TABLE jobs ADD COLUMN future_value TEXT')
    elif conflict == 'unknown_table':
        with sqlite3.connect(source/'state.sqlite') as db:
            db.execute('CREATE TABLE future_library_data (value TEXT)')
            db.execute("INSERT INTO future_library_data VALUES ('must not disappear')")
    target, report = tmp_path / 'postgres', tmp_path / 'report.json'
    credential = credential_file(tmp_path, postgres_url)
    with (source/'instance.lock').open('rb') as lock:
        if conflict == 'live_source':
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = invoke(source, target, credential, report)
    assert result.returncode != 0
    assert not target.exists()
    assert not report.exists()
    assert postgres_url not in result.stdout + result.stderr
    # A failed import leaves the pristine target usable, without imported cards.
    with TestClient(create_app(tmp_path/'empty-reader', token='test', api_only=True,
                    database_url=postgres_url), headers=HEADERS) as client:
        assert client.get('/api/v3/cards').json() == []


def test_cli_refuses_live_or_populated_target_and_preserves_existing_library(postgres_url, tmp_path):
    source = tmp_path/'sqlite'
    source_library(source)
    credential = credential_file(tmp_path, postgres_url)
    app = lambda: create_app(tmp_path/'existing-reader', token='test', api_only=True, database_url=postgres_url,
                            rss_enabled=True, rss_transport=httpx.MockTransport(lambda _: httpx.Response(200, text=rss())))
    with TestClient(app(), headers=HEADERS) as client:
        assert client.put('/api/v3/power', json={'enabled': True}).status_code == 200
        feed = add_source(client, 'https://existing.test/feed', 'Keep this feed', 'Keep this folder')
        result = invoke(source, tmp_path/'live-import', credential, tmp_path/'live.json')
        assert result.returncode != 0
        assert not (tmp_path/'live-import').exists()
    result = invoke(source, tmp_path/'populated-import', credential, tmp_path/'populated.json')
    assert result.returncode != 0
    assert not (tmp_path/'populated-import').exists()
    with TestClient(app(), headers=HEADERS) as client:
        subscriptions = client.get('/api/v3/subscriptions').json()
        assert subscriptions['feeds'][0]['id'] == feed['id']
        assert subscriptions['folders'][0]['title'] == 'Keep this folder'


def invoke(source, target, credential, report):
    return subprocess.run([sys.executable, '-m', 'oko.storage.import_sqlite', '--source', str(source),
        '--target-data', str(target), '--database-url-file', str(credential), '--report', str(report)],
        cwd=ROOT, capture_output=True, text=True, timeout=45)


@pytest.mark.parametrize('alias_target', ['source', 'target'])
def test_cli_refuses_report_alias_into_source_without_changing_source(postgres_url, tmp_path, alias_target):
    source = tmp_path/'sqlite'
    source_library(source)
    before = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in source.rglob('*') if p.is_file()}
    alias = tmp_path/'source-alias'
    alias.symlink_to(source if alias_target == 'source' else tmp_path/'target', target_is_directory=True)
    result = invoke(source, tmp_path/'target', credential_file(tmp_path, postgres_url), alias/'import.json')
    assert result.returncode != 0
    assert not (tmp_path/'target').exists()
    assert before == {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in source.rglob('*') if p.is_file()}


def credential_file(tmp_path, postgres_url):
    credential = tmp_path / 'private-dsn'
    credential.write_text(postgres_url)
    credential.chmod(0o600)
    return credential


def test_cli_import_preserves_library_and_media_without_starting_work(postgres_url, tmp_path):
    source = tmp_path / 'sqlite'
    feed, item, media_url, _ = source_library(source)
    before = hashlib.sha256((source / 'state.sqlite').read_bytes()).hexdigest()
    target, report = tmp_path / 'postgres', tmp_path / 'report.json'
    result = invoke(source, target, credential_file(tmp_path, postgres_url), report)
    assert result.returncode == 0, result.stdout + result.stderr
    assert postgres_url not in result.stdout + result.stderr
    receipt = json.loads(report.read_text())
    assert receipt['work_enabled'] is False
    assert receipt['tables']['feeds']['rows'] == 1
    # RSS publication and its linked web article both belong to the library.
    assert receipt['tables']['items']['rows'] == 2
    assert receipt['source_database_sha256'] == before
    assert hashlib.sha256((source / 'state.sqlite').read_bytes()).hexdigest() == before
    assert not (target / 'state.sqlite').exists()
    with TestClient(create_app(target, token='test', api_only=True, database_url=postgres_url), headers=HEADERS) as client:
        assert client.get('/api/v3/status').json()['runtime_state'] == 'disabled'
        assert [card['id'] for card in client.get('/api/v3/cards').json()] == [item]
        subscriptions = client.get('/api/v3/subscriptions').json()
        assert subscriptions['folders'][0]['title'] == 'Imported folder'
        assert subscriptions['feeds'][0]['id'] == feed['id']
        assert 'Imported synthetic text' in client.get('/api/v3/materials/'+item).json()['content']['text']
        assert client.get(media_url).content == PNG
        # New reader commands and cleanup jobs must allocate identities beyond
        # imported ones, while retaining the existing disabled-work semantics.
        assert client.post('/api/v3/materials/'+item+'/viewed').status_code == 200
        assert client.get('/api/v3/cards').json() == []


def test_cli_import_keeps_completed_processing_results(postgres_url, tmp_path):
    source = tmp_path/'sqlite'
    _, item, _, material = source_library(source, processing=True)
    target, report = tmp_path/'postgres', tmp_path/'report.json'
    result = invoke(source, target, credential_file(tmp_path, postgres_url), report)
    assert result.returncode == 0, result.stdout+result.stderr
    calls = []
    with TestClient(create_app(target, token='test', api_only=True, database_url=postgres_url,
                    ai_transport=transport(calls, theses=True)), headers=HEADERS) as client:
        imported = client.get('/api/v3/materials/'+item).json()
        assert imported['theses']['result'] == material['theses']['result']
        assert client.get('/api/v3/status').json()['runtime_state'] == 'disabled'
        assert calls == []
