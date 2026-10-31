from tests.support.storage import Store
from tests.support.postgres_work import scan_rows
from oko.storage.transaction import transaction

def test_url_invalidation_probes_ignore_ten_thousand_unrelated(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) SELECT 'p-'||n FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO publications(id) VALUES ('target'),('root')")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) SELECT 'p-'||n,'p-'||(n+1) FROM generate_series(1,9999)n")
            store.db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('root','target')")
            store.db.execute("INSERT INTO jobs(kind,item,status) SELECT 'filter','p-'||n,'done' FROM generate_series(1,10000)n")
            store.db.execute("INSERT INTO jobs(kind,item,status) VALUES ('filter','root','done')")
            store.db._db.execute('ANALYZE publications');store.db._db.execute('ANALYZE publication_links');store.db._db.execute('ANALYZE jobs')
        target="SELECT id,processing_metadata FROM publications WHERE id=%s UNION SELECT p.id,p.processing_metadata FROM publication_links l JOIN publications p ON p.id=l.source_publication_id WHERE l.target_publication_id=%s"
        jobs="SELECT id,kind,item,generation,error,problem_code,status,input_snapshot FROM jobs WHERE kind IN ('filter','theses') AND item=ANY(%s)"
        for query,args in ((target,('target','target')),(jobs,(['target','root'],))):
            plan=store.db._db.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,args).fetchone()[0][0]['Plan']
            print('scan rows',scan_rows(plan),'shared buffers',plan.get('Shared Hit Blocks'))
            assert scan_rows(plan)<1000,plan
    finally:store.db.close()
