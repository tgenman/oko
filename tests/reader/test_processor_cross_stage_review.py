import pytest
from oko.storage.transaction import transaction
from tests.support.storage import Store


@pytest.mark.parametrize('selected,other,old_status,new_status', [('filter','theses','cancelled','blocked'),('theses','filter','pending','cancelled')])
def test_shared_owner_claim_updates_other_stage_processor(selected,other,old_status,new_status,tmp_path,postgres_url,monkeypatch):
    from oko.storage import processing_input
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected',ARRAY[]::text[]),('b','b','B','default','rss','rss','connected',ARRAY[]::text[])")
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,processing_metadata) VALUES ('p','rss','p',0,'{}')")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary) VALUES ('a','p',true),('b','p',false)")
            chosen=store.db.execute("INSERT INTO jobs(kind,item,status) VALUES (%s,'p','pending') RETURNING id",(selected,)).fetchone()[0]
            changed=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES (%s,'p',%s,'failure','processor-config') RETURNING id",(other,old_status)).fetchone()[0]
            store.problems.finish_processor_change([changed],None,1)
        monkeypatch.setattr(processing_input,'processing_feed',lambda *_:'a')
        monkeypatch.setattr(processing_input,'content',lambda *_:{})
        monkeypatch.setattr(processing_input,'blocks',lambda *_:[])
        monkeypatch.setattr(processing_input,'stage_enabled',lambda *_:True)
        monkeypatch.setattr(store.processing,'reconcile_accepted_item',lambda *_:None)
        with transaction(store.db):
            row=store.db.execute('SELECT * FROM jobs WHERE id=%s',(chosen,)).fetchone()
            store.jobs._select_processing_feed(row)
        row=store.db.execute('SELECT status,processor_visible FROM jobs WHERE id=%s',(changed,)).fetchone()
        assert row['status']==new_status
        assert row['processor_visible']==(new_status=='blocked')
        assert bool(store.problems.processor_current())==(new_status=='blocked')
    finally:store.db.close()
