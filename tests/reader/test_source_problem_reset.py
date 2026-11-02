"""Retire old diagnostics without losing the current source failure."""
import json

import psycopg
import pytest
from fastapi.testclient import TestClient

from oko.storage.postgres_migrations import Migration, migrate
from oko.storage.postgres_schema import MIGRATIONS
from tests.support.app import create_app
from tests.support.reader import HEADERS


def test_release_discards_old_source_history_and_recovers_current_error(tmp_path, postgres_database_url):
    previous = MIGRATIONS[:93]
    release = MIGRATIONS[93]
    with psycopg.connect(postgres_database_url, autocommit=True) as db:
        migrate(db, previous)
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','rss','rss','connected',ARRAY[]::text[])")
        db.execute("INSERT INTO publications(id,platform,external_id,published_known,discovered_at) VALUES ('p','other_web','p',0,1)")
        db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,source_event_owned) VALUES ('fetch','p','failed','network failure','source-network',true)")
        body = dict(code='source-network', operation='producer:status', objects=[dict(kind='material', id='p')],
                    section='object', title='Old failure', consequence='Saved', next_step='Retry',
                    state='current', next_at=None, action=None)
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('old','job:source-network:p:fetch','background',%s,1)", (json.dumps(body),))
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES ('history','job:source-network:p:fetch','background',%s,1,2,'resolved')", (json.dumps(body),))
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('action','action:command','action',%s,1)", (json.dumps(body),))
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('discover','job:source-network:f:discover','background',%s,1)", (json.dumps(body),))
        marked = dict(body, operation='producer:fetch', source_group_identity='job:source-network:q:fetch')
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('marked','job:source-network:q:fetch','background',%s,1)", (json.dumps(marked),))
        db.execute("INSERT INTO state(key,value) VALUES ('source-legacy-protected:old','marker'),('source-problems-recovered-v1','true')")
        # A later failure in the same release must restore the discarded rows,
        # recovery state and index together with the ledger.
        broken = Migration(release.version, release.name, release.sql + '; SELECT missing_reset_column FROM state')
        with pytest.raises(psycopg.errors.UndefinedColumn):
            migrate(db, (*previous, broken))
        assert db.execute("SELECT id FROM reader_problems ORDER BY id").fetchall() == [('action',), ('discover',), ('history',), ('marked',), ('old',)]
        assert db.execute("SELECT source_event_owned FROM jobs WHERE item='p'").fetchone() == (True,)
        assert db.execute("SELECT to_regclass('source_legacy_protected')").fetchone()[0]
        migrate(db, (*previous, release))
        assert db.execute("SELECT id FROM reader_problems ORDER BY id").fetchall() == [('action',), ('discover',), ('marked',)]
        assert db.execute("SELECT count(*) FROM state WHERE key LIKE 'source-legacy-protected:%%' OR key='source-problems-recovered-v1'").fetchone() == (0,)
        assert db.execute("SELECT to_regclass('source_legacy_protected')").fetchone() == (None,)
        assert db.execute("SELECT status,error FROM jobs WHERE item='p'").fetchone() == ('failed', 'network failure')
        assert db.execute("SELECT id FROM publications WHERE id='p'").fetchone() == ('p',)
        # Isolate the current-error scenario from unrelated legacy test records.
        db.execute("DELETE FROM reader_problems WHERE id IN ('action','discover','marked')")
        migrate(db, MIGRATIONS)
    occurrence = None
    for _ in range(2):
        with TestClient(create_app(tmp_path, token='test', database_url=postgres_database_url, api_only=True), headers=HEADERS) as client:
            response = client.get('/api/v3/status/details')
            assert response.status_code == 200
            current = [p for p in response.json()['problems']['current'] if p['code'] == 'source_unavailable']
            assert len(current) == 1 and current[0]['id'] != 'old'
            assert current[0]['affected'] == [dict(kind='material', material_id='p')]
            if occurrence is not None:
                assert current[0]['id'] == occurrence
            occurrence = current[0]['id']
