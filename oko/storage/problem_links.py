"""Indexed material references and superseded receipts, derived from journal rows.

Triggers keep these projections in the same transaction, including legacy writes.
Only initial migration scans the journal; its authoritative JSON stays unchanged.
"""
from oko.storage.sqlite_schema import is_sqlite
from oko.storage.database import Database, Row


def install(db: Database) -> None:
    if not is_sqlite(db):
        return
    migrated = db.execute("SELECT 1 FROM state WHERE key='journal-index-version' AND value='1'").fetchone()
    db.executescript('''
        CREATE TABLE IF NOT EXISTS reader_material_links (
            kind TEXT, record TEXT, item TEXT, PRIMARY KEY(kind,record,item));
        CREATE INDEX IF NOT EXISTS reader_material_link_item ON reader_material_links(item,kind,record);
        CREATE TABLE IF NOT EXISTS reader_action_superseded (sequence INTEGER PRIMARY KEY, completed REAL);
        CREATE INDEX IF NOT EXISTS reader_superseded_expiry ON reader_action_superseded(completed);
    ''')
    for kind, table in [('problem', 'reader_problems'), ('action', 'reader_action_results')]:
        values = f"SELECT '{kind}',NEW.id,json_extract(value,'$.id') FROM json_each(NEW.body,'$.objects') WHERE json_extract(value,'$.kind')='material'"
        for event in ('INSERT', 'UPDATE OF body'):
            name = event.split()[0].lower()
            guard = 'WHEN NEW.body IS NOT OLD.body' if name == 'update' else ''
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS journal_links_{kind}_{name} AFTER {event} ON {table} {guard} BEGIN
                DELETE FROM reader_material_links WHERE kind='{kind}' AND record=NEW.id;
                INSERT OR IGNORE INTO reader_material_links {values}; END''')
        db.execute(f'''CREATE TRIGGER IF NOT EXISTS journal_links_{kind}_delete AFTER DELETE ON {table} BEGIN
            DELETE FROM reader_material_links WHERE kind='{kind}' AND record=OLD.id; END''')
    db.executescript('''
        CREATE TRIGGER IF NOT EXISTS journal_receipt_added AFTER INSERT ON reader_action_results
          WHEN EXISTS (SELECT 1 FROM reader_action_results WHERE identity=NEW.identity AND sequence!=NEW.sequence) BEGIN
            INSERT OR REPLACE INTO reader_action_superseded
                SELECT sequence,completed FROM reader_action_results WHERE identity=NEW.identity
                AND sequence=(SELECT max(sequence) FROM reader_action_results WHERE identity=NEW.identity AND sequence<NEW.sequence);
            INSERT OR REPLACE INTO reader_action_superseded
                SELECT NEW.sequence,NEW.completed WHERE EXISTS (
                    SELECT 1 FROM reader_action_results WHERE identity=NEW.identity AND sequence>NEW.sequence);
        END;
        CREATE TRIGGER IF NOT EXISTS journal_receipt_completed AFTER UPDATE OF completed ON reader_action_results BEGIN
            UPDATE reader_action_superseded SET completed=NEW.completed WHERE sequence=NEW.sequence;
        END;
        CREATE TRIGGER IF NOT EXISTS journal_receipt_removed AFTER DELETE ON reader_action_results BEGIN
            DELETE FROM reader_action_superseded WHERE sequence=OLD.sequence OR sequence=(
                SELECT max(sequence) FROM reader_action_results WHERE identity=OLD.identity);
        END;
    ''')
    if not migrated:
        with db:
            db.execute('DELETE FROM reader_material_links')
            db.execute('DELETE FROM reader_action_superseded')
            for kind, table in [('problem', 'reader_problems'), ('action', 'reader_action_results')]:
                db.execute(f'''INSERT OR IGNORE INTO reader_material_links
                    SELECT ?,r.id,json_extract(o.value,'$.id') FROM {table} r,json_each(r.body,'$.objects') o
                    WHERE json_extract(o.value,'$.kind')='material' ''', (kind,))
            db.execute('''INSERT OR REPLACE INTO reader_action_superseded SELECT sequence,completed
                FROM reader_action_results r WHERE EXISTS (SELECT 1 FROM reader_action_results n
                    WHERE n.identity=r.identity AND n.sequence>r.sequence)''')
            db.execute("INSERT OR REPLACE INTO state VALUES ('journal-index-version','1')")


def material_records(db: Database, kind: str, members: set[str]) -> list[Row]:
    table = {'problem': 'reader_problems', 'action': 'reader_action_results'}[kind]
    rows = {}
    ids = sorted(members)
    for start in range(0, len(ids), 400):
        batch = ids[start:start + 400]
        query = f'SELECT * FROM {table} WHERE id IN (SELECT record FROM reader_material_links WHERE kind=? AND item IN (' + ','.join('?' for _ in batch) + '))'
        rows.update({r['id']: r for r in db.execute(query, [kind, *batch])})
    return list(rows.values())


def cleanup(db: Database, cutoff: float, limit: int = 200) -> None:
    db.execute('''DELETE FROM reader_action_results WHERE sequence IN (
        SELECT sequence FROM reader_action_superseded WHERE completed<=? ORDER BY completed LIMIT ?)''', (cutoff, limit))
