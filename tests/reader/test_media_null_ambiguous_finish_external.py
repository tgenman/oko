import json
from tests.support.storage import Store
from oko.storage.transaction import transaction
from oko.storage.job_types import Job

def test_finish_does_not_close_one_of_ambiguous_null_occurrences(tmp_path,postgres_url):
    import json
    s=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(s.db):
            s.db.execute("INSERT INTO publications(id) VALUES ('p')")
            s.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','f','pending_download')")
            job=s.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('download_media','m','running',1,'failure',NULL) RETURNING id").fetchone()[0]
            s.problems.sync_media_problem(job,[],100)
            old=s.db.execute("SELECT id,identity,body FROM reader_problems WHERE body::jsonb->>'media_job_id'=%s AND finished IS NULL",(str(job),)).fetchone()
            body=json.loads(old['body']);body['media_job_identity']=old['identity']
            s.db.execute('UPDATE reader_problems SET body=%s WHERE id=%s',(json.dumps(body),old['id']))
            s.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('duplicate','job:media-isolated:other','background',%s,1)",(json.dumps(body),))
            before={r['id']:(r['body'],r['finished']) for r in s.db.execute('SELECT id,body,finished FROM reader_problems WHERE id=ANY(%s)',([old['id'],'duplicate'],))}
            s.jobs.finish(Job(job,'download_media','m',1))
        after={r['id']:(r['body'],r['finished']) for r in s.db.execute('SELECT id,body,finished FROM reader_problems WHERE id=ANY(%s)',([old['id'],'duplicate'],))}
        assert after==before
    finally:s.db.close()
