import json
from tests.support.storage import Store


def test_isolated_valid_fractional_upper_timestamp_is_detected(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        body=dict(code='processor-config',processor_group_identity='processor:configuration',section='action',title='legacy',consequence='c',next_step='n',objects=[],action=None,operation='producer:read',state='current',next_at=None)
        with store.db:
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy','processor:configuration-isolated:test','background',%s,%s)",(json.dumps(body),253402300799.5))
        from oko.storage.problems import _readable_timestamp
        assert _readable_timestamp(253402300799.5)
        record=store.problems.processor_current()
        assert record and record['id']=='legacy'
    finally:store.db.close()

def test_null_target_processor_job_does_not_break_status_http(tmp_path,postgres_database_url):
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    from oko.storage.postgres_migrations import migrate
    from oko.storage.postgres_schema import MIGRATIONS
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:70])
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('filter',NULL,'failed','failure','processor-config')")
        migrate(db,MIGRATIONS)
    app=create_app(tmp_path,token='test',database_url=postgres_database_url,api_only=True)
    with TestClient(app,headers=HEADERS,raise_server_exceptions=False) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text


def test_null_target_processor_history_closes_without_erasing_job(tmp_path,postgres_database_url):
    import json,psycopg
    from oko.processing.execution import CodexChoice,CodexSettings
    from oko.storage.postgres_migrations import migrate
    from oko.storage.postgres_schema import MIGRATIONS
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:70])
        db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,payload) VALUES ('filter',NULL,'pending','failure','processor-config',%s)",(json.dumps({'preclaim_blocked':True}),))
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        original=store.problems.processor_current()['id']
        choice=CodexChoice(model='synthetic',reasoning_effort='low',fast=False)
        store.configure_codex(CodexSettings(filter=choice,theses=choice))
        row=store.db.execute('SELECT body,outcome FROM reader_problems WHERE id=%s',(original,)).fetchone()
        assert row['outcome']=='resolved' and json.loads(row['body'])['objects']==[]
        assert store.db.execute("SELECT 1 FROM jobs WHERE kind='filter' AND item IS NULL").fetchone()
    finally:store.db.close()


def test_reserved_row_is_legacy_wire_valid_and_not_projected(tmp_path,postgres_url):
    from oko.reader import snapshots,projection_v3
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('p','youtube','p',0)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','youtube:subtitles','synthetic','captions','pending')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('download_media','m','pending','failure','processor-config') RETURNING id").fetchone()[0]
        store.youtube.blocked(100,900)
        with store.db:store.problems.finish_processor_change([job],None,100)
        reserved=store.problems.processor_reservation()
        row=store.db.execute('SELECT * FROM reader_problems WHERE id=%s',(reserved['id'],)).fetchone()
        body=json.loads(row['body']);body.update(id=row['id'],created_at=row['created'],finished_at=row['finished'],outcome=row['outcome'],state='finished')
        fact=snapshots.Problem.model_validate(body)
        snapshot=snapshots.ProblemSnapshot.model_validate(dict(current=[],recent=[fact],count=0,updated_at=100.0))
        assert not projection_v3.problems(snapshot,None,{},set()).recent
    finally:store.db.close()
