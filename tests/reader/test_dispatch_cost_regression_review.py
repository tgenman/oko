from tests.support.storage import Store
from tests.support.postgres_work import measure_reads
from oko.storage.transaction import transaction


def test_scoped_media_dispatch_does_not_scan_ten_thousand_foreign_jobs(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url,wall_clock=lambda:100)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','rss','rss','connected','[]'),('b','b','B','default','rss','rss','connected','[]')")
            store.db.execute("INSERT INTO publications(id,viewed) VALUES ('target',0)")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) VALUES ('a','target',true,true)")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('selected','target','image','x','file','pending_download')")
            store.db.execute("INSERT INTO publications(id,viewed) SELECT 'p-'||n,0 FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO feed_publications(feed_id,publication_id,is_primary,is_unviewed) SELECT 'b','p-'||n,true,true FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) SELECT 'm-'||n,'p-'||n,'image','x','file','pending_download' FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,next) SELECT 'download_media','m-'||n,'pending',0 FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO jobs(kind,item,status,next) VALUES ('download_media','selected','pending',0)")
        for table in ('publications','feed_publications','media_files'):store.db._db.execute('ANALYZE '+table)
        with measure_reads(store.db) as work:result=store.jobs._media_candidate(100,['a'],['a'],False)
        assert result is not None and result['item']=='selected'
        print("scoped media read work",work)
        assert work.rows<1000,work
    finally:store.db.close()
