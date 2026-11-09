"""Upgrade existing histories without rewriting their stored selections."""
import psycopg

from oko.storage.postgres_migrations import migrate, verify
from oko.storage.postgres_schema import MIGRATIONS


def test_history_integrity_upgrade_preserves_legacy_and_damaged_data(postgres_database_url):
    assert MIGRATIONS[-2].version == 126 and MIGRATIONS[-1].version == 127
    with psycopg.connect(postgres_database_url, autocommit=True) as db:
        migrate(db, MIGRATIONS[:-1])
        db.execute("INSERT INTO feeds(id,identity) VALUES ('valid','one'),('broken','two'),('absent','three'),('shape','four')")
        valid = '{"mode":"count","count":20,"since":null,"run":"saved-run"}'
        db.execute("INSERT INTO initial_history(feed,selection) VALUES ('valid',%s),('broken','{broken'),('shape','{}')", (valid,))
        db.execute("INSERT INTO history_members(feed,item,outcome) VALUES ('valid','good','loaded'),('broken','bad','pending')")
        before = db.execute('SELECT feed,selection,frozen,paused,scan_complete FROM initial_history ORDER BY feed').fetchall()
        members = db.execute('SELECT feed,item,outcome FROM history_members ORDER BY feed,item').fetchall()
        migrate(db, MIGRATIONS)
        verify(db, MIGRATIONS)
        assert db.execute('SELECT feed,selection,frozen,paused,scan_complete FROM initial_history ORDER BY feed').fetchall() == before
        assert db.execute('SELECT feed,item,outcome FROM history_members ORDER BY feed,item').fetchall() == members
        assert db.execute('SELECT id,initial_history_expected,initial_history_run FROM feeds ORDER BY id').fetchall() == [
            ('absent',False,None), ('broken',True,None), ('shape',True,None), ('valid',True,'saved-run')]
        assert db.execute('SELECT count(*) FROM history_deferred_events').fetchone()[0] == 0
