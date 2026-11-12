import pytest


def test_http_reads_saved_empty_media_without_write(tmp_path,postgres_url):
    import psycopg
    from fastapi.testclient import TestClient
    from tests.support.app import create_app
    from tests.support.reader import HEADERS
    from tests.support.storage import Store
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    with store.db as db:
        db.execute("INSERT INTO publications(id,platform,external_id) VALUES ('article','other_web','article')")
        db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('media','article','image','x','file','unavailable')")
        job=db.execute("INSERT INTO jobs(kind,item,status,reason_message,reason_code) VALUES ('download_media','media','failed','failure','') RETURNING id").fetchone()[0]
        store.problems.sync_media_problem(job,[],store.wall_clock())
    store.db.close()
    with TestClient(create_app(tmp_path,token='test',database_url=postgres_url,api_only=True),headers=HEADERS) as client:
        with psycopg.connect(postgres_url,autocommit=True) as db:
            before=db.execute("SELECT id,xmin::text,body FROM reader_problems WHERE body::jsonb->>'media_job_id' IS NOT NULL AND finished IS NULL").fetchone()
        response=client.get('/api/v3/status/details')
        assert response.status_code==200 and any(p['id']==before[0] for p in response.json()['problems']['current'])
        with psycopg.connect(postgres_url,autocommit=True) as db:
            assert db.execute('SELECT id,xmin::text,body FROM reader_problems WHERE id=%s',(before[0],)).fetchone()==before
