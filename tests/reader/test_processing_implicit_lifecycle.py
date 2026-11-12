import pytest


@pytest.mark.parametrize('kind',['filter','theses'])
def test_http_implicit_error_reads_without_write(tmp_path,postgres_url,kind):
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    with store.db as db:
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
        db.execute("UPDATE publications SET feed_id='feed' WHERE id='article'")
        job=db.execute("INSERT INTO jobs(kind,item,status,reason_message,reason_code) VALUES (%s,'article','failed','failure',NULL) RETURNING id",(kind,)).fetchone()[0]
        store.problems.sync_processing_problem(job,[],store.wall_clock())
    store.db.close()
    with TestClient(create_app(tmp_path,token='test',database_url=postgres_url,api_only=True),headers=HEADERS) as client:
        with psycopg.connect(postgres_url,autocommit=True) as db:
            before=db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE identity=%s AND finished IS NULL",('job:work-'+kind+':feed:'+kind+':article',)).fetchone()
        response=client.get('/api/v3/status/details')
        assert response.status_code==200 and any(p['id']==before[0] for p in response.json()['problems']['current'])
        with psycopg.connect(postgres_url,autocommit=True) as db:
            assert db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE id=%s',(before[0],)).fetchone()==before
