"""Status freshness recorded by the owner of a changed fact."""
from oko.storage.database import Database


def changed(db: Database) -> None:
    db.execute("""INSERT INTO reader_entities(kind,identity,version,changed)
        VALUES ('status','runtime',1,oko_reader_time()) ON CONFLICT(kind,identity)
        DO UPDATE SET version=reader_entities.version+1,changed=excluded.changed""")
