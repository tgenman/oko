import json
from oko.storage.job_types import Job
from oko.storage.transaction import transaction
from tests.support.storage import Store


def test_claim_block_and_interrupt_keep_group_and_update_projection(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code) VALUES ('read','feed','pending','failure','processor-config') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        original=store.problems.processor_current()['id']
        assert store.problems.processor_configuration_candidate(1)['state']=='current'
        version=store.read_versions.version('status','runtime')['version']
        row=store.db.execute('SELECT * FROM jobs WHERE id=%s',(job,)).fetchone()
        claimed=store.jobs._claim(row)
        assert claimed and store.problems.processor_configuration_candidate(2)['state']=='retrying'
        assert store.read_versions.version('status','runtime')['version']>version
        with transaction(store.db):store.jobs.block(claimed)
        assert store.problems.processor_current()['id']==original
        assert store.problems.processor_configuration_candidate(3)['next_step']=='Включите обработку, чтобы повторить попытку.'
        with store.db:store.db.execute("UPDATE jobs SET status='running' WHERE id=%s",(job,))
        store.jobs.interrupted()
        assert store.problems.processor_current()['id']==original
        assert store.problems.processor_configuration_candidate(4)['state']=='current'
    finally:store.db.close()


def test_retry_processing_reactivates_legacy_processor_without_get(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            store.db.execute("INSERT INTO publications(id,platform,external_id,viewed) VALUES ('item','rss','item',0)")
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,input_snapshot) VALUES ('filter','item','cancelled','failure','processor-config',%s::jsonb) RETURNING id",(json.dumps({'signature':'x'}),)).fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        assert store.problems.processor_current() is None
        with transaction(store.db):assert store.jobs.retry_processing('item','filter')
        assert store.problems.processor_current() is not None
    finally:store.db.close()


def test_revoke_changed_input_closes_processor_before_get(tmp_path,postgres_url,monkeypatch):
    from oko.storage import processing_input
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            job=store.db.execute("INSERT INTO jobs(kind,item,status,error,problem_code,input_snapshot) VALUES ('filter','item','running','failure','processor-config',%s::jsonb) RETURNING id",(json.dumps({'signature':'old'}),)).fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        original=store.problems.processor_current()['id']
        monkeypatch.setattr(processing_input,'signature',lambda *_:'new')
        with transaction(store.db):store.jobs.revoke_changed_running_inputs()
        assert store.db.execute('SELECT outcome FROM reader_problems WHERE id=%s',(original,)).fetchone()[0]=='resolved'
    finally:store.db.close()


def test_replace_media_input_keeps_processor_group_and_updates_state(tmp_path,postgres_url):
    store=Store(tmp_path,lambda _:None,database_url=postgres_url)
    try:
        with store.db:
            job=store.db.execute("INSERT INTO jobs(kind,item,status,generation,error,problem_code) VALUES ('download_media','missing','running',1,'failure','processor-config') RETURNING id").fetchone()[0]
            store.problems.finish_processor_change([job],None,1)
        original=store.problems.processor_current()['id']
        with transaction(store.db):store.jobs.replace_media_input(Job(job,'download_media','missing',1))
        assert store.problems.processor_current()['id']==original
        assert store.problems.processor_configuration_candidate(2)['state']=='retrying'
    finally:store.db.close()
