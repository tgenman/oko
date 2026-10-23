"""The target key upgrade must roll back its table replacement on failure."""
import sqlite3

import pytest

from oko.storage.publication_schema import remove_feed_url_key


def test_failed_key_upgrade_preserves_rows_indexes_and_triggers():
    db=sqlite3.connect(':memory:')
    db.executescript('''CREATE TABLE items(id TEXT PRIMARY KEY,feed_id TEXT,url TEXT,UNIQUE(feed_id,url));
        INSERT INTO items VALUES ('one','feed','https://web.example/one');
        CREATE INDEX items_feed ON items(feed_id);
        CREATE TABLE events(value TEXT);
        CREATE TRIGGER item_added AFTER INSERT ON items BEGIN INSERT INTO events VALUES (NEW.id); END;''')
    before=db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name").fetchall()
    def reject_recreation(action,table,*_):
        return sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_CREATE_TABLE and table=='items' else sqlite3.SQLITE_OK
    db.set_authorizer(reject_recreation)
    with pytest.raises(sqlite3.DatabaseError):
        remove_feed_url_key(db)
    db.set_authorizer(None)
    assert db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY name").fetchall()==before
    assert db.execute('SELECT * FROM items').fetchall()==[('one','feed','https://web.example/one')]
    remove_feed_url_key(db)
    db.execute("INSERT INTO items VALUES ('two','feed','https://web.example/one')")
    assert db.execute('SELECT * FROM events').fetchall()==[('two',)]
