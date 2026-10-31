import hashlib,json
from tests.support.storage import Store
from oko.storage.transaction import transaction

def test_old_protection_marker_does_not_hide_reused_item_error(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        identity='job:work-fetch:f:fetch:p'
        body=dict(code='work-fetch',section='object',title='old',consequence='old',next_step='old',objects=[dict(kind='material',id='p')],action=None,operation='producer:status',state='current',next_at=None,reason='old error')
        key='fetch-null-protected:'+hashlib.sha256(b'p').hexdigest()
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','F','default','youtube','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('f','p',true)")
            store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('fetch','p','failed','new error',NULL)")
            store.db.execute("INSERT INTO reader_problems(id,identity,origin,body,created) VALUES ('new-occurrence',%s,'background',%s,1)",(identity,json.dumps(body)))
            store.db.execute("INSERT INTO state(key,value) VALUES (%s,%s)",(key,json.dumps(['oko-fetch-null-v1','p'])))
        current=store.problems.snapshot(100)['current']
        problem=next(p for p in current if p['id']=='new-occurrence')
        assert problem.get('reason')=='new error',problem
    finally:store.db.close()
