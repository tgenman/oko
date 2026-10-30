"""Transaction-owned monotonic revision of source configuration."""
from oko.storage.database import Database


def changed(db: Database) -> None:
    db.execute("""INSERT INTO state(key,value) VALUES ('sources-version','1')
        ON CONFLICT(key) DO UPDATE SET value=(state.value::bigint+1)::text""")
