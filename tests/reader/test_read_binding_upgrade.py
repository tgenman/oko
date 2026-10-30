import psycopg
from oko.storage.postgres_migrations import migrate,verify
from oko.storage.postgres_schema import MIGRATIONS
from tests.support.storage import Store


def test_guarded_release_migrates_v64_orphan(postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:64])
        db.execute("INSERT INTO telegram_bindings(feed,binding) VALUES ('orphan','not-json')")
        migrate(db,MIGRATIONS)
        verify(db,MIGRATIONS)
        assert db.execute("SELECT binding FROM telegram_bindings WHERE feed='orphan'").fetchone()==('not-json',)


def test_jsonb_unrepresentable_orphans_survive_upgrade(postgres_database_url):
    values=['{"role":1e1000000000}',r'{"role":"\u0000"}']
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:64])
        for index,value in enumerate(values):
            db.execute('INSERT INTO telegram_bindings(feed,binding) VALUES (%s,%s)',(str(index),value))
        migrate(db,MIGRATIONS)
        assert [row[0] for row in db.execute('SELECT binding FROM telegram_bindings ORDER BY feed')]==values


def test_active_binding_with_jsonb_invalid_extra_is_indexed_at_startup(tmp_path,postgres_database_url):
    original = r'{"kind":"client","role":"personal","extra":"\u0000"}'
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:64])
        db.execute("INSERT INTO feeds(id,identity,title,folder_id,kind,transport,connection,original_urls) VALUES ('a','a','A','default','telegram','telegram','connected','[]')")
        db.execute("INSERT INTO telegram_bindings(feed,binding) VALUES ('a',%s)",(original,))
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        assert store.db.execute("SELECT binding,indexed_role FROM telegram_bindings WHERE feed='a'").fetchone()==(original,'personal')
        assert store.subscriptions.telegram.dependents('personal')==['a']
    finally:
        store.db.close()
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        assert store.subscriptions.telegram.dependents('personal')==['a']
    finally:
        store.db.close()


def test_valid_orphan_role_keeps_dependents_after_upgrade(tmp_path,postgres_database_url):
    with psycopg.connect(postgres_database_url,autocommit=True) as db:
        migrate(db,MIGRATIONS[:64])
        original = '{"kind":"client","role":"personal"}'
        db.execute("INSERT INTO telegram_bindings(feed,binding) VALUES ('orphan',%s)",(original,))
        migrate(db,MIGRATIONS)
    store=Store(tmp_path,lambda _:None,database_url=postgres_database_url)
    try:
        assert store.subscriptions.telegram.dependents('personal')==['orphan']
        assert store.db.execute("SELECT binding FROM telegram_bindings WHERE feed='orphan'").fetchone()[0]==original
    finally:
        store.db.close()
