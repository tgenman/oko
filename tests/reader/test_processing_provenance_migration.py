"""Historical results retain their provenance when attempt hashes change."""
import json
import psycopg
import pytest
from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS


def test_processing_migration_preserves_known_facts_and_invalidates_running_attempt(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:114])
        old={'_pass':{'feed_id':'old'},'filter':{'input_hash':'known','signature':'settings-dependent','settings_hash':'old',
             'processor':'llamacpp','model':'known-model','parameters':{'temperature':0},'completed_at':'2026-01-01T00:00:00Z'},
             'summary':{'input_hash':'other','completed_at':'2026-01-02T00:00:00Z'}}
        db.execute("INSERT INTO publications(id,platform,external_id,processing_metadata) VALUES ('p','rss','p',%s)",(json.dumps(old),))
        db.execute("INSERT INTO jobs(kind,item,status,generation,input_snapshot) VALUES ('filter','p','running',5,%s::jsonb)",(json.dumps({'signature':'old'}),))
        migrate(db,MIGRATIONS)
        metadata=json.loads(db.execute("SELECT processing_metadata FROM publications WHERE id='p'").fetchone()[0])
        assert '_pass' not in metadata
        assert metadata['filter']['input_hash']=='known' and metadata['filter']['model']=='known-model'
        assert metadata['filter']['prompt'] is None and metadata['filter']['parameters']=={'temperature':0}
        assert metadata['summary']['prompt'] is None and metadata['summary']['model'] is None
        assert all('signature' not in metadata[stage] and 'settings_hash' not in metadata[stage] for stage in ('filter','summary'))
        assert db.execute("SELECT status,generation,input_snapshot FROM jobs WHERE item='p'").fetchone()==('pending',6,None)


def test_processing_migration_refuses_malformed_provenance(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:114])
        value={'filter':['unexpected']}
        db.execute("INSERT INTO publications(id,platform,external_id,processing_metadata) VALUES ('p','rss','p',%s)",(json.dumps(value),))
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS)
        assert json.loads(db.execute("SELECT processing_metadata FROM publications WHERE id='p'").fetchone()[0])==value


def test_processing_migration_moves_known_codex_parameters(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:114])
        old={'summary':{'processor':'codex','model':'known','reasoning_effort':'high','fast':True,'input_hash':'old'}}
        db.execute("INSERT INTO publications(id,platform,external_id,processing_metadata) VALUES ('p','rss','p',%s)",(json.dumps(old),))
        migrate(db,MIGRATIONS)
        saved=json.loads(db.execute("SELECT processing_metadata FROM publications WHERE id='p'").fetchone()[0])['summary']
        assert saved['parameters']=={'reasoning_effort':'high','fast':True}
        assert 'reasoning_effort' not in saved and 'fast' not in saved


def test_processing_migration_rejects_conflicting_codex_parameters(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:114])
        old={'summary':{'processor':'codex','fast':True,'parameters':{'fast':False}}}
        db.execute("INSERT INTO publications(id,platform,external_id,processing_metadata) VALUES ('p','rss','p',%s)",(json.dumps(old),))
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS)
        assert json.loads(db.execute("SELECT processing_metadata FROM publications WHERE id='p'").fetchone()[0])==old


def test_migrated_done_job_can_be_explicitly_retried_after_restart(tmp_path,postgres_database_url):
    from oko.storage.processing_input import digest
    from oko.storage.store import Store
    from oko.storage.transaction import transaction
    from oko.storage.job_repository import JobRepository
    from oko.subscriptions.subscriptions import Overrides
    content={'blocks':[{'type':'text','role':'body','text':'Saved'}],'extra':{}}
    metadata={'summary':{'input_hash':digest(content['blocks']),'signature':'old-signature','processor':'llamacpp','model':'old'}}
    result={'points':[{'text':'Old result','source_publication_ids':['p']}],'extra':{}}
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:114])
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('f','f','Feed','default','rss','rss','connected',ARRAY[]::text[])")
        db.execute("INSERT INTO publications(id,platform,external_id,url,feed_id,status,content,processing_metadata,summarized_content) VALUES ('p','rss','p','https://example.test/p','f','ready',%s::jsonb,%s,%s)",(json.dumps(content),json.dumps(metadata),json.dumps(result)))
        db.execute("INSERT INTO jobs(kind,item,status,generation,input_snapshot) VALUES ('theses','p','done',4,%s::jsonb)",(json.dumps({'signature':'old-signature'}),))
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        with transaction(store.db):
            store.subscriptions.update_settings('oko','global',Overrides(theses=True,prompt_filters=''))
            store.settings_changed()
            assert JobRepository(store).retry_processing('p','theses')
        status,generation,snapshot=store.db.execute("SELECT status,generation,input_snapshot FROM jobs WHERE item='p' AND kind='theses'").fetchone()[:]
        assert status=='pending' and generation>4 and snapshot['input_hash']
        assert json.loads(store.db.execute("SELECT summarized_content FROM publications WHERE id='p'").fetchone()[0])=={'points':result['points']}
    finally:
        store.db.close()
