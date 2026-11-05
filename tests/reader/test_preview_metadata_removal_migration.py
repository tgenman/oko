"""Legacy preview facts are moved to blocks and Job before copies are removed."""
import json
import psycopg
import pytest

from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS


@pytest.mark.parametrize('already_present',[False,True])
def test_migration_preserves_preview_and_reason(postgres_database_url,already_present):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:111])
        text='Prefix with preview' if already_present else 'Prefix'
        content={'blocks':[{'type':'text','role':'body','text':text}],
                 'extra':{'_reader':{'source_manifest':{'post':{},'preview_fragment':'preview',
                    'preview_error':'Preview unavailable','post_web_unavailable':True}}}}
        db.execute("INSERT INTO publications(id,platform,external_id,status,content) VALUES ('p','telegram','p','ready',%s::jsonb)",(json.dumps(content),))
        migrate(db,MIGRATIONS[:112])
        result,status=db.execute("SELECT content,status FROM publications WHERE id='p'").fetchone()
        assert status=='pending_collection'
        assert sum(block.get('text','').count('preview') for block in result['blocks'])==1
        assert not {'preview_fragment','preview_error','post_web_unavailable'} & result['extra']['_reader']['source_manifest'].keys()
        assert db.execute("SELECT status,reason_code,reason_message FROM jobs WHERE kind='fetch' AND item='p'").fetchone()==('unavailable','post-web-unavailable','Preview unavailable')


def test_migration_rejects_different_saved_job_reason(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:111])
        content={'blocks':[],'extra':{'_reader':{'source_manifest':{'post':{},'preview_error':'First','post_web_unavailable':True}}}}
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','telegram','p',%s::jsonb)",(json.dumps(content),))
        db.execute("INSERT INTO jobs(kind,item,status,reason_code,reason_message) VALUES ('fetch','p','failed','source-unavailable','Second')")
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS[:112])
        assert db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]==content
        assert db.execute("SELECT reason_message FROM jobs WHERE item='p'").fetchone()==('Second',)


def test_migration_preserves_a_waiting_job_and_its_deadline(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:111])
        content={'blocks':[],'extra':{'_reader':{'source_manifest':{'post':{},'post_web_unavailable':True}}}}
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','telegram','p',%s::jsonb)",(json.dumps(content),))
        db.execute("INSERT INTO jobs(kind,item,status,reason_code,reason_message,wait_scope,retry_at) VALUES ('fetch','p','pending','source-wait','Wait','source',12345)")
        migrate(db,MIGRATIONS[:112])
        assert db.execute("SELECT status,reason_code,reason_message,wait_scope,retry_at FROM jobs WHERE item='p'").fetchone()==('pending','source-wait','Wait','source',12345)


def test_migration_does_not_create_a_job_for_viewed_publication(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:111])
        content={'blocks':[],'extra':{'_reader':{'source_manifest':{'post':{},'post_web_unavailable':True}}}}
        db.execute("INSERT INTO publications(id,platform,external_id,viewed,content) VALUES ('p','telegram','p',1,%s::jsonb)",(json.dumps(content),))
        migrate(db,MIGRATIONS[:112])
        assert db.execute("SELECT count(*) FROM jobs WHERE kind='fetch' AND item='p'").fetchone()==(0,)


def test_migration_reopens_legacy_done_job_with_failure(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:111])
        content={'blocks':[],'extra':{'_reader':{'source_manifest':{'post':{},'preview_error':'Old failure','post_web_unavailable':True}}}}
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','telegram','p',%s::jsonb)",(json.dumps(content),))
        db.execute("INSERT INTO jobs(kind,item,status,reason_code,reason_message) VALUES ('fetch','p','done','post-web-unavailable','Old failure')")
        migrate(db,MIGRATIONS[:112])
        assert db.execute("SELECT status,reason_code,reason_message FROM jobs WHERE item='p'").fetchone()==('unavailable','post-web-unavailable','Old failure')
