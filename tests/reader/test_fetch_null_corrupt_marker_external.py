import hashlib,json
from tests.support.storage import Store
from oko.storage.transaction import transaction

def test_protected_escaped_json_is_not_rewritten(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        identity='job:work-fetch:f:fetch:p';rid='protected'
        body=dict(code='work-fetch',section='object',title='old',consequence='old',next_step='old',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None,reason='old error')
        raw=json.dumps(body)
        key='fetch-null-protected:'+hashlib.sha256(rid.encode()).hexdigest()
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','new error',NULL)")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES (%s,%s,'background',%s,1)",(rid,identity,raw))
            store.db.execute("INSERT INTO state(key,value) VALUES (%s,%s)",(key,'foreign-marker'))
        store.problems.snapshot(100)
        row=store.db.execute('SELECT body,finished FROM reader_problems WHERE id=%s',(rid,)).fetchone()
        assert row['body']==raw and row['finished'] is None
    finally:store.db.close()
