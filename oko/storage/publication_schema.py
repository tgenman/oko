"""Remove the legacy per-feed URL key after selecting Publication storage."""
import re
import sqlite3


def remove_feed_url_key(db: sqlite3.Connection) -> None:
    schema=db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='items'").fetchone()[0]
    revised=re.sub(r',\s*UNIQUE\s*\(\s*feed_id\s*,\s*url\s*\)', '', schema, flags=re.IGNORECASE)
    if revised==schema:
        return
    indexes=[row[0] for row in db.execute("SELECT sql FROM sqlite_master WHERE tbl_name='items' AND type IN ('index','trigger') AND sql IS NOT NULL")]
    columns=','.join('"'+row[1].replace('"','""')+'"' for row in db.execute('PRAGMA table_info(items)'))
    db.execute('SAVEPOINT publication_key_upgrade')
    try:
        db.execute('CREATE TEMP TABLE publication_rows AS SELECT * FROM items')
        db.execute('DROP TABLE items')
        db.execute(revised)
        db.execute('INSERT INTO items ('+columns+') SELECT '+columns+' FROM publication_rows')
        db.execute('DROP TABLE publication_rows')
        for index in indexes:
            db.execute(index)
    except BaseException:
        db.execute('ROLLBACK TO publication_key_upgrade')
        db.execute('RELEASE publication_key_upgrade')
        raise
    else:
        db.execute('RELEASE publication_key_upgrade')
