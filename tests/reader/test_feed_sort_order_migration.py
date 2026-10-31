import psycopg
import pytest
from oko.storage.postgres_migrations import Migration,migrate,verify
from oko.storage.postgres_schema import MIGRATIONS


def test_feed_sort_order_migration_preserves_values_and_sequence(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:67])
        db.execute("INSERT INTO feeds(id,identity,title,rowid,original_urls) OVERRIDING SYSTEM VALUE VALUES ('b','b','B',-4,'[]'),('a','a','A',123,'[]')")
        before=db.execute('SELECT id,rowid FROM feeds ORDER BY rowid').fetchall()
        sequence=db.execute("SELECT pg_get_serial_sequence('feeds','rowid')").fetchone()[0]
        migrate(db,MIGRATIONS)
        assert db.execute('SELECT id,sort_order FROM feeds ORDER BY sort_order').fetchall()==before
        assert db.execute("SELECT pg_get_serial_sequence('feeds','sort_order')").fetchone()[0]==sequence
        assert db.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='feeds' AND column_name='rowid'").fetchone()[0]==0
        db.execute("INSERT INTO feeds(id,identity,title) VALUES ('c','c','C')")
        assert isinstance(db.execute("SELECT sort_order FROM feeds WHERE id='c'").fetchone()[0],int)
        verify(db,MIGRATIONS)


def test_feed_sort_order_migration_rolls_back(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:67])
        db.execute("INSERT INTO feeds(id,identity,title) VALUES ('a','a','A')")
        before=db.execute('SELECT id,rowid FROM feeds').fetchall()
        failing=Migration(68,MIGRATIONS[67].name,MIGRATIONS[67].sql+'; SELECT missing_column FROM feeds')
        with pytest.raises(psycopg.errors.UndefinedColumn):migrate(db,(*MIGRATIONS[:67],failing))
        assert db.execute('SELECT id,rowid FROM feeds').fetchall()==before
        assert db.execute('SELECT max(version) FROM oko_schema_migrations').fetchone()[0]==67
        verify(db,MIGRATIONS[:67])
