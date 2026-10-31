import pytest
from tests.support.storage import Store


@pytest.mark.parametrize('kind',['discover','read','feed-delete'])
def test_discover_processor_belongs_to_actual_feed_with_colliding_publication(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,kind,transport,connection,original_urls) VALUES ('same','same','same','rss','rss','connected',ARRAY[]::text[]),('other','other','other','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('same',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('other','same',true)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,problem_code,error) VALUES (%s,'same','failed','processor-config','failure') RETURNING id",(kind,)).fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        problem=store.problems.processor_configuration_candidate(1)
        assert problem is not None
        assert problem['objects']==[dict(kind='feed',id='same')]
    finally:store.db.close()

def test_legacy_processor_empty_resource_does_not_break_http(tmp_path,postgres_database_url):
    import json,psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    from oko.storage.postgres_migrations import migrate
    from oko.storage.postgres_schema import MIGRATIONS
    body=dict(code='processor-config',section='action',title='legacy',consequence='c',next_step='n',objects=[dict(kind='feed',id='')],action=None,operation='producer:read',state='current',next_at=None)
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES ('legacy','processor:configuration','background',%s,90,95,'resolved')",(json.dumps(body),))
    app=create_app(tmp_path,token='test',database_url=postgres_database_url,api_only=True,wall_clock=lambda:100)
    with TestClient(app,headers=HEADERS,raise_server_exceptions=False) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text


def test_legacy_processor_bad_request_id_does_not_break_http(tmp_path,postgres_database_url):
    import json,psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    from oko.storage.postgres_migrations import migrate
    from oko.storage.postgres_schema import MIGRATIONS
    body=dict(code='processor-config',section='action',title='legacy',consequence='c',next_step='n',objects=[],request_id='bad id',action=None,operation='producer:read',state='current',next_at=None)
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS)
        db.execute("INSERT INTO reader_problems(id,identity,origin,body,created,finished,outcome) VALUES ('legacy','processor:configuration','background',%s,90,95,'resolved')",(json.dumps(body),))
    app=create_app(tmp_path,token='test',database_url=postgres_database_url,api_only=True,wall_clock=lambda:100)
    with TestClient(app,headers=HEADERS,raise_server_exceptions=False) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200,response.text
