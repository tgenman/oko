"""Album membership belongs to the Telegram portion of publication content."""
import json
import psycopg
import pytest

from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS


@pytest.mark.parametrize('pending',[False,True])
def test_migration_keeps_album_members_in_content_and_drops_column(postgres_database_url,pending):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:109])
        content={} if pending else {'blocks':[{'type':'text','role':'body','text':'Album'}], 'extra':{'_reader':{'members':[11,12]}}}
        db.execute("INSERT INTO publications(id,platform,external_id,members,status,content) VALUES ('p','telegram','p','[11,12]',%s,%s::jsonb)",('pending_collection' if pending else 'ready',json.dumps(content)))
        migrate(db,MIGRATIONS[:110])
        assert db.execute("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='publications' AND column_name='members')").fetchone()==(False,)
        saved,status=db.execute("SELECT content,status FROM publications WHERE id='p'").fetchone()
        assert saved['extra']['telegram']['members']==[11,12]
        assert 'members' not in saved['extra'].get('_reader',{})
        assert status==('pending_collection' if pending else 'ready')


@pytest.mark.parametrize(('platform','members','content'),[
    ('telegram','[11,12]',{'extra':{'telegram':{'members':[11,13]}}}),
    ('telegram','[]',{'extra':{'telegram':{'members':[11,12]},'_reader':{'members':[11,13]}}}),
    ('rss','[11]',{}),
    ('rss','[]',{'extra':{'telegram':{'members':[11]}}}),
    ('telegram','invalid',{}),
    ('telegram','[11,"12"]',{}),
    ('telegram','[11,11]',{}),
])
def test_migration_stops_for_ambiguous_members_without_losing_data(postgres_database_url,platform,members,content):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:109])
        db.execute("INSERT INTO publications(id,platform,external_id,members,content) VALUES ('p',%s,'p',%s,%s::jsonb)",(platform,members,json.dumps(content)))
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS[:110])
        assert db.execute("SELECT members,content FROM publications WHERE id='p'").fetchone()==(members,content)


def test_migration_recovers_members_from_the_only_remaining_manifest_copy(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:109])
        content={'blocks':[],'extra':{'_reader':{'source_manifest':{'post':{'members':[11,12]}}}}}
        db.execute("INSERT INTO publications(id,platform,external_id,members,content) VALUES ('p','telegram','p','[]',%s::jsonb)",(json.dumps(content),))
        migrate(db,MIGRATIONS[:110])
        saved=db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]
        assert saved['extra']['telegram']['members']==[11,12]
        assert 'members' not in saved['extra']['_reader']['source_manifest']['post']
