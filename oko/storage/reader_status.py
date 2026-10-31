"""Transitional status freshness, committed with the owner's changed fact."""
from oko.storage.database import Database


def changed(db: Database) -> None:
    db.execute("""INSERT INTO state(key,value)
        VALUES ('reader-status-revision',jsonb_build_object('version',1,'changed',
            coalesce(nullif(current_setting('oko.wall',true),'')::double precision,extract(epoch FROM clock_timestamp())))::text)
        ON CONFLICT(key) DO UPDATE SET value=jsonb_build_object(
            'version',(state.value::jsonb->>'version')::bigint+1,
            'changed',coalesce(nullif(current_setting('oko.wall',true),'')::double precision,extract(epoch FROM clock_timestamp())))::text""")
