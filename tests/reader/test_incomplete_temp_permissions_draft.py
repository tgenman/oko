import os
from uuid import uuid4
import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from oko.storage.postgres_migrations import migrate, verify_reader_permissions, SchemaMismatch
from oko.storage.postgres_schema import MIGRATIONS
from tests.support.storage import Store


def test_reader_temporary_privilege_preflight_and_first_write(tmp_path):
    base=os.environ['OKO_TEST_POSTGRES_URL']
    name='incomplete_test_'+uuid4().hex
    role='incomplete_reader_'+uuid4().hex
    with psycopg.connect(base,autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role),sql.Literal('synthetic')))
        try:
            admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(name)))
            try:
                admin.execute(sql.SQL('REVOKE TEMPORARY ON DATABASE {} FROM PUBLIC').format(sql.Identifier(name)))
                db_dsn=make_conninfo(base,dbname=name)
                reader_dsn=make_conninfo(db_dsn,user=role,password='synthetic')
                # The reader preflight works even before the schema exists.
                with psycopg.connect(reader_dsn,autocommit=True) as reader:
                    with pytest.raises(SchemaMismatch,match='TEMPORARY privilege'):
                        verify_reader_permissions(reader)
                import subprocess,sys
                credentials=tmp_path/'synthetic-reader-dsn'
                credentials.write_text(reader_dsn);credentials.chmod(0o600)
                preflight=subprocess.run([sys.executable,'-m','oko.storage.postgres_migrations','--check-reader-permissions','--database-url-file',str(credentials)],capture_output=True,text=True)
                assert preflight.returncode!=0 and 'TEMPORARY privilege' in preflight.stderr
                with psycopg.connect(db_dsn,autocommit=True) as db:
                    migrate(db,MIGRATIONS)
                    db.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
                    db.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO {}').format(sql.Identifier(role)))
                    db.execute(sql.SQL('REVOKE INSERT,UPDATE,DELETE ON oko_schema_migrations FROM {}').format(sql.Identifier(role)))
                    db.execute(sql.SQL('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}').format(sql.Identifier(role)))
                with psycopg.connect(reader_dsn,autocommit=True) as reader:
                    with pytest.raises(SchemaMismatch,match='TEMPORARY privilege'):
                        verify_reader_permissions(reader)
                with pytest.raises(SchemaMismatch,match='TEMPORARY privilege'):
                    Store(tmp_path,lambda _:None,database_url=reader_dsn)
                admin.execute(sql.SQL('GRANT TEMPORARY ON DATABASE {} TO {}').format(sql.Identifier(name),sql.Identifier(role)))
                with psycopg.connect(reader_dsn,autocommit=True) as reader:verify_reader_permissions(reader)
                preflight=subprocess.run([sys.executable,'-m','oko.storage.postgres_migrations','--check-reader-permissions','--database-url-file',str(credentials)],capture_output=True,text=True)
                assert preflight.returncode==0,preflight.stderr
                store=Store(tmp_path,lambda _:None,database_url=reader_dsn)
                try:
                    with store.db:
                        store.db.execute("INSERT INTO publications(id,platform,external_id,viewed,complete,content,incomplete_class) VALUES ('p','rss','p',0,1,'{\"extra\":{\"_reader\":{\"full\":false}}}',0)")
                        store.incomplete_draft.mark(['p'])
                    assert store.db.execute("SELECT 1 FROM publications WHERE id='p'").fetchone()
                finally:store.db.close()
            finally:admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
        finally:admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))
