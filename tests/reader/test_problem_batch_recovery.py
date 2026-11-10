"""Recovery preserves problem identities and unowned collisions across restarts."""
import json

import pytest
from fastapi.testclient import TestClient

from oko.storage.transaction import transaction
from tests.support.app import create_app
from tests.support.reader import HEADERS
from tests.support.storage import Store


@pytest.mark.parametrize('kind',['fetch','download_media','filter','theses'])
@pytest.mark.parametrize('collision',[False,True])
def test_recovery_preserves_problem_and_foreign_collision(tmp_path,postgres_url,kind,collision):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    marker={'fetch':'fetch','download_media':'media','filter':'processing','theses':'processing'}[kind]+'-problems-recovered-v2'
    item='file' if kind=='download_media' else 'article'
    identity=f'job:model-invalid:feed:{kind}:{item}'
    body=json.dumps({'code':'model-invalid','section':'object','title':'Foreign','consequence':'Saved','next_step':'Inspect','objects':[{'kind':'material','id':'foreign'}],'action':None,'operation':'producer:'+kind,'state':'current','next_at':None})
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('article','other_web','article','feed')")
            if kind=='download_media':
                store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','pending_download')")
            store.db.execute("INSERT INTO jobs(kind,item,status,reason_code,reason_message) VALUES (%s,%s,'failed','model-invalid','synthetic failure')",(kind,item))
            if collision:
                store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('foreign',%s,'background',%s,1)",(identity,body))
            store.db.execute('DELETE FROM state WHERE key=%s',(marker,))
        if kind=='fetch':store.problems.recover_fetch_problems()
        elif kind=='download_media':store.problems.recover_media_problems()
        else:store.problems.recover_processing_problems()
        if not collision:
            assert store.db.execute('SELECT 1 FROM reader_problems WHERE identity=%s AND finished IS NULL',(identity,)).fetchone()
        else:
            rows=store.db.execute('SELECT id,body FROM reader_problems WHERE finished IS NULL').fetchall()
            assert any(row['id']!='foreign' and json.loads(row['body']).get('operation')=='producer:'+kind and {'kind':'material','id':'article'} in json.loads(row['body']).get('objects',[]) for row in rows)
        with transaction(store.db):store.db.execute('DELETE FROM state WHERE key=%s',(marker,))
    finally:store.db.close()
    def snapshot():
        current=Store(tmp_path,lambda _:None,database_url=postgres_url)
        try:return [tuple(row) for row in current.db.execute('SELECT id,identity,body,created,finished,outcome FROM reader_problems ORDER BY id')]
        finally:current.db.close()
    with TestClient(create_app(tmp_path,token='test',database_url=postgres_url,api_only=True),headers=HEADERS) as client:
        response=client.get('/api/v3/status/details')
        assert response.status_code==200
    first=snapshot()
    if collision:
        foreign=next(row for row in first if row[0]=='foreign')
        assert foreign[1]==identity and json.loads(foreign[2])==json.loads(body) and foreign[4] is None
    current=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(current.db):current.db.execute('DELETE FROM state WHERE key=%s',(marker,))
    finally:current.db.close()
    with TestClient(create_app(tmp_path,token='test',database_url=postgres_url,api_only=True),headers=HEADERS) as client:
        assert client.get('/api/v3/status/details').status_code==200
    assert snapshot()==first


@pytest.mark.parametrize('marked',[True,False])
def test_fetch_recovery_preserves_overflow_of_unowned_legacy_records(tmp_path,postgres_url,marked):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('article','other_web','article','feed')")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,reason_code,reason_message) VALUES ('fetch','article','failed','model-invalid','failure') RETURNING id").fetchone()[0]
            for index in range(101):
                identity=f'job:work-fetch:old{index}:fetch:article'
                value={'code':'work-fetch','section':'object','title':'Old','consequence':'Saved','next_step':'Inspect','objects':[{'kind':'material','id':'foreign'}],'action':None,'operation':'producer:fetch' if marked else 'producer:status','state':'current','next_at':None}
                if marked:value.update(fetch_job_id=str(job),fetch_job_identity=identity,fetch_implicit_code=True)
                body=json.dumps(value)
                store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)",(f'old{index:03}',identity,body))
            store.db.execute("DELETE FROM state WHERE key='fetch-problems-recovered-v2'")
        before=[tuple(row) for row in store.db.execute("SELECT id,identity,body,created,finished,outcome FROM reader_problems WHERE id LIKE 'old%%' ORDER BY id")]
        store.problems.recover_fetch_problems()
        assert [tuple(row) for row in store.db.execute("SELECT id,identity,body,created,finished,outcome FROM reader_problems WHERE id LIKE 'old%%' ORDER BY id")]==before
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert [tuple(row) for row in store.db.execute("SELECT id,identity,body,created,finished,outcome FROM reader_problems WHERE id LIKE 'old%%' ORDER BY id")]==before
    finally:store.db.close()


@pytest.mark.parametrize('kind',['fetch','subtitle'])
def test_recovery_reserves_same_problem_until_youtube_deadline(tmp_path,postgres_url,kind):
    from tests.support.youtube_action import youtube_action

    clock=[100.0]
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('article','youtube','article','feed')")
            youtube_action(store,'start',100)
            if kind=='fetch':
                store.db.execute("INSERT INTO jobs(kind,item,status,reason_message) VALUES ('fetch','article','failed','waiting')")
                store.db.execute("DELETE FROM state WHERE key='youtube-null-fetch-recovered-v1'")
            else:
                store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('sub','article','youtube:subtitles','https://example.test/sub','sub','pending_download')")
                store.db.execute("INSERT INTO jobs(kind,item,status,reason_message) VALUES ('download_media','sub','failed','waiting')")
                store.db.execute("DELETE FROM state WHERE key='subtitle-problems-recovered-v1'")
        if kind=='fetch':store.problems.recover_youtube_null_fetch()
        else:store.problems.recover_subtitles()
        due='youtube_fetch_due' if kind=='fetch' else 'subtitle_due'
        row=store.db.execute(f'SELECT id,{due} FROM reader_problems WHERE {due} IS NOT NULL').fetchone()
        assert row is not None and row[1]>100
        identity=row[0];clock[0]=float(row[1])+1
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:clock[0])
        assert any(problem['id']==identity for problem in store.problems.snapshot(clock[0])['current'])
    finally:store.db.close()


@pytest.mark.parametrize('kind',['fetch','download_media','filter','theses'])
def test_recovery_resumes_after_committed_batch_without_duplicate(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    family={'fetch':'fetch','download_media':'media','filter':'processing','theses':'processing'}[kind]
    marker=family+'-problems-recovered-v2'
    item='file' if kind=='download_media' else 'article'
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('article','other_web','article','feed')")
            if kind=='download_media':
                store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('file','article','image:1','https://example.test/file','file','pending_download')")
            store.db.execute("INSERT INTO jobs(kind,item,status,reason_code,reason_message) VALUES (%s,%s,'failed','model-invalid','synthetic failure')",(kind,item))
            store.db.execute('DELETE FROM state WHERE key=%s',(marker,))
        def fail(point):
            if point==family+'_problem_recovery_batch':raise RuntimeError('interrupted')
        store.fault=fail
        with pytest.raises(RuntimeError,match='interrupted'):
            if kind=='fetch':store.problems.recover_fetch_problems()
            elif kind=='download_media':store.problems.recover_media_problems()
            else:store.problems.recover_processing_problems()
        saved=store.db.execute('SELECT id,identity,body,created FROM reader_problems WHERE identity=%s AND finished IS NULL',(f'job:model-invalid:feed:{kind}:{item}',)).fetchone()
        assert saved is not None and not store.db.execute('SELECT 1 FROM state WHERE key=%s',(marker,)).fetchone()
        expected=tuple(saved)
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute('SELECT 1 FROM state WHERE key=%s',(marker,)).fetchone()
        rows=store.db.execute('SELECT id,identity,body,created FROM reader_problems WHERE identity=%s AND finished IS NULL',(expected[1],)).fetchall()
        assert [tuple(row) for row in rows]==[expected]
    finally:store.db.close()


@pytest.mark.parametrize('kind',['fetch','download_media','filter','theses'])
def test_recovery_resumes_after_first_of_two_job_pages(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    family={'fetch':'fetch','download_media':'media','filter':'processing','theses':'processing'}[kind]
    marker=family+'-problems-recovered-v2'
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) SELECT 'p'||n,'other_web','p'||n,'feed' FROM generate_series(1,101) n")
            if kind=='download_media':
                store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) SELECT 'm'||n,'p'||n,'image:1','https://example.test/'||n,'file','pending_download' FROM generate_series(1,101) n")
            prefix='m' if kind=='download_media' else 'p'
            store.db.execute("INSERT INTO jobs(kind,item,status,reason_code,reason_message) SELECT %s,%s||n,'failed','model-invalid','failure' FROM generate_series(1,101) n",(kind,prefix))
            store.db.execute('DELETE FROM state WHERE key=%s',(marker,))
        def fail(point):
            if point==family+'_problem_recovery_batch':raise RuntimeError('interrupted')
        store.fault=fail
        with pytest.raises(RuntimeError,match='interrupted'):
            if kind=='fetch':store.problems.recover_fetch_problems()
            elif kind=='download_media':store.problems.recover_media_problems()
            else:store.problems.recover_processing_problems()
        before=[tuple(row) for row in store.db.execute("SELECT id,identity,body,created FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'=%s ORDER BY id",('producer:'+kind,))]
        assert len(before)==100
        assert not store.db.execute('SELECT 1 FROM state WHERE key=%s',(marker,)).fetchone()
        store.db.close();store=Store(tmp_path,lambda _:None,database_url=postgres_url)
        assert store.db.execute('SELECT 1 FROM state WHERE key=%s',(marker,)).fetchone()
        after=[tuple(row) for row in store.db.execute("SELECT id,identity,body,created FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'operation'=%s ORDER BY id",('producer:'+kind,))]
        assert len(after)==101 and set(before).issubset(set(after))
    finally:store.db.close()


@pytest.mark.parametrize('kind',['fetch','download_media','filter','theses'])
@pytest.mark.parametrize('damage',['no_objects','invalid_object','empty_id','ambiguous_identity','mismatched_code','null_identity','mismatched_operation'])
def test_recovery_preserves_untrusted_legacy_problem(tmp_path,postgres_url,kind,damage):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    family={'fetch':'fetch','download_media':'media','filter':'processing','theses':'processing'}[kind]
    marker=family+'-problems-recovered-v2'
    identity=f'job:model-invalid:feed:{kind}:article'
    body={'code':'model-invalid','section':'object','title':'Old','consequence':'Saved','next_step':'Inspect','objects':[{'kind':'material','id':'article'}],'action':None,'operation':'producer:'+kind,'state':'current','next_at':None}
    if damage=='no_objects':body['objects']=[]
    elif damage=='invalid_object':body['objects']=[{'kind':'feed','id':'article'}]
    elif damage=='empty_id':body['objects']=[{'kind':'material','id':''}]
    elif damage=='ambiguous_identity':identity=f'job:model-invalid:feed:fetch:other:{kind}:article'
    elif damage=='mismatched_code':identity=f'job:other:feed:{kind}:article'
    elif damage=='null_identity':body[{'fetch':'fetch_job_identity','download_media':'media_job_identity','filter':'processing_job_identity','theses':'processing_job_identity'}[kind]]=None
    else:body['operation']='producer:theses' if kind=='filter' else 'producer:filter'
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('untrusted',%s,'background',%s,1)",(identity,json.dumps(body)))
            store.db.execute('DELETE FROM state WHERE key=%s',(marker,))
        before=tuple(store.db.execute("SELECT id,identity,body,created,finished,outcome FROM reader_problems WHERE id='untrusted'").fetchone())
        if kind=='fetch':store.problems.recover_fetch_problems()
        elif kind=='download_media':store.problems.recover_media_problems()
        else:store.problems.recover_processing_problems()
        assert tuple(store.db.execute("SELECT id,identity,body,created,finished,outcome FROM reader_problems WHERE id='untrusted'").fetchone())==before
    finally:store.db.close()


@pytest.mark.parametrize('kind',['fetch','download_media','filter','theses'])
def test_recovery_resolves_legacy_status_without_identity_override(tmp_path,postgres_url,kind):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    marker={'fetch':'fetch','download_media':'media','filter':'processing','theses':'processing'}[kind]+'-problems-recovered-v2'
    identity=f'job:model-invalid:feed:{kind}:article'
    body={'code':'model-invalid','section':'object','title':'Old','consequence':'Saved','next_step':'Inspect','objects':[{'kind':'material','id':'article'}],'action':None,'operation':'producer:status','state':'current','next_at':None}
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('feed','feed','Feed','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,feed_id) VALUES ('article','other_web','article','feed')")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('legacy',%s,'background',%s,1)",(identity,json.dumps(body)))
            store.db.execute('DELETE FROM state WHERE key=%s',(marker,))
        if kind=='fetch':store.problems.recover_fetch_problems()
        elif kind=='download_media':store.problems.recover_media_problems()
        else:store.problems.recover_processing_problems()
        row=store.db.execute("SELECT identity,body,finished,outcome FROM reader_problems WHERE id='legacy'").fetchone()
        assert row is not None and row['identity']==identity and json.loads(row['body'])==body
        assert row['finished'] is not None and row['outcome']=='resolved'
    finally:store.db.close()
