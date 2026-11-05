"""Obsolete collection tracking is removed without touching publication facts."""
import json
import psycopg

from oko.storage.postgres_migrations import migrate
from oko.storage.postgres_schema import MIGRATIONS


def test_migration_removes_collection_tracking_from_content(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:110])
        value={'blocks':[{'type':'text','role':'body','text':'Saved'}],
               'extra':{'_input_hash':'old','_collection_revision':'old-revision','example':{'keep':True}}}
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','rss','p',%s::jsonb)",(json.dumps(value),))
        migrate(db,MIGRATIONS[:111])
        result=db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]
        assert result=={'blocks':value['blocks'],'extra':{'example':{'keep':True}}}


def test_migration_removes_link_copy_only_when_relationship_preserves_it(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:110])
        value={'blocks':[],'extra':{'_links':[{'platform':'other_web','external_id':'article','url':'https://example.test/article'}]}}
        db.execute("INSERT INTO publications(id,platform,external_id,url,content) VALUES ('p','rss','p','https://example.test/p',%s::jsonb),('a','other_web','article','https://example.test/article','{}'::jsonb)",(json.dumps(value),))
        db.execute("INSERT INTO publication_links(source_publication_id,target_publication_id) VALUES ('p','a')")
        migrate(db,MIGRATIONS[:111])
        saved=db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]
        assert '_links' not in saved['extra']
        assert db.execute("SELECT target_publication_id FROM publication_links WHERE source_publication_id='p'").fetchone()==('a',)


def test_migration_stops_on_unowned_link_copy(postgres_database_url):
    import pytest
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:110])
        value={'blocks':[],'extra':{'_links':[{'platform':'other_web','external_id':'article','url':'https://example.test/article'}]}}
        db.execute("INSERT INTO publications(id,platform,external_id,content) VALUES ('p','rss','p',%s::jsonb)",(json.dumps(value),))
        with pytest.raises(psycopg.errors.RaiseException,match='p'):
            migrate(db,MIGRATIONS[:111])
        assert db.execute("SELECT content FROM publications WHERE id='p'").fetchone()[0]==value
