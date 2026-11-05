"""Old deletion markers become Job outcomes without losing saved content."""
import json
import psycopg
import pytest

from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS


def test_migration_moves_source_deleted_to_a_job_and_keeps_content(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:108])
        content={'blocks':[{'type':'text','role':'body','text':'Saved text'}]}
        db.execute("INSERT INTO publications(id,platform,external_id,source_deleted,content) VALUES ('p','rss','p',true,%s::jsonb)",(json.dumps(content),))
        migrate(db,MIGRATIONS[:109])
        assert db.execute("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='publications' AND column_name='source_deleted')").fetchone()==(False,)
        assert db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]==content
        status,code,message=db.execute("SELECT status,reason_code,reason_message FROM jobs WHERE kind='fetch' AND item='p'").fetchone()
        assert status=='unavailable' and code and message


@pytest.mark.parametrize('code',['source-unavailable','source_unavailable'])
def test_migration_keeps_existing_unavailable_job_reason(postgres_database_url,code):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:108])
        db.execute("INSERT INTO publications(id,platform,external_id,source_deleted) VALUES ('p','rss','p',true)")
        db.execute("INSERT INTO jobs(kind,item,status,reason_code,reason_message) VALUES ('fetch','p','unavailable',%s,'Original reason')",(code,))
        migrate(db,MIGRATIONS[:109])
        assert db.execute("SELECT status,reason_code,reason_message FROM jobs WHERE item='p'").fetchone()==('unavailable',code,'Original reason')


@pytest.mark.parametrize('status',['running','cancelled','done','blocked'])
def test_migration_stops_on_conflicting_active_fetch(postgres_database_url,status):
    import pytest
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:108])
        db.execute("INSERT INTO publications(id,platform,external_id,source_deleted) VALUES ('p','rss','p',true)")
        db.execute("INSERT INTO jobs(kind,item,status) VALUES ('fetch','p',%s)",(status,))
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS[:109])
        assert db.execute("SELECT source_deleted FROM publications WHERE id='p'").fetchone()==(True,)
        assert db.execute("SELECT status FROM jobs WHERE item='p'").fetchone()==(status,)


def test_migration_does_not_create_fetch_work_for_viewed_publications(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:108])
        db.execute("INSERT INTO publications(id,platform,external_id,source_deleted,viewed) VALUES ('p','rss','p',true,1)")
        migrate(db,MIGRATIONS[:109])
        assert db.execute("SELECT count(*) FROM jobs WHERE kind='fetch' AND item='p'").fetchone()==(0,)


def test_migration_rejects_unrelated_existing_job_reason(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:108])
        db.execute("INSERT INTO publications(id,platform,external_id,source_deleted) VALUES ('p','rss','p',true)")
        db.execute("INSERT INTO jobs(kind,item,status,reason_message) VALUES ('fetch','p','failed','Different failure')")
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS[:109])
        assert db.execute("SELECT reason_message FROM jobs WHERE item='p'").fetchone()==('Different failure',)


def test_migration_removes_the_old_source_change_marker(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:108])
        content={'blocks':[{'type':'text','role':'body','text':'Saved'}],'extra':{'_reader':{'changed':True,'other':'keep'}}}
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','rss','p',%s::jsonb)",(json.dumps(content),))
        migrate(db,MIGRATIONS[:109])
        assert db.execute("SELECT content->'extra'->'_reader' FROM publications WHERE id='p'").fetchone()[0]=={'other':'keep'}
