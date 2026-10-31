import json
from tests.support.storage import Store
from oko.storage.job_types import Job
from oko.storage import source_groups
from oko.storage.transaction import transaction

def test_replace_media_input_syncs_source_without_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with transaction(store.db):
            store.db.execute("INSERT INTO publications(id) VALUES ('p')")
            store.db.execute("INSERT INTO media_files(id,publication_id,resource_key,source_locator,name,status) VALUES ('m','p','image','x','file','unavailable')")
            jid=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,generation) VALUES ('download_media','m','running','old error','source-network',1) RETURNING id").fetchone()[0]
            source_groups.sync_jobs(store,[jid],1)
        with transaction(store.db):store.jobs.replace_media_input(Job(jid,'download_media','m',1))
        row=store.db.execute("SELECT body FROM reader_problems WHERE finished IS NULL AND body::jsonb->>'source_group_identity' IS NOT NULL").fetchone()
        body=json.loads(row['body'])
        assert 'reason' not in body,body
    finally:store.db.close()
