ALTER TABLE telegram_read_state ADD COLUMN needs_sync boolean NOT NULL DEFAULT false;
INSERT INTO telegram_read_state(feed, needs_sync)
    SELECT f.id, true FROM telegram_read_dirty d JOIN feeds f ON f.id=d.feed
    WHERE f.transport='telegram' AND f.connection IS DISTINCT FROM 'deleting'
    ON CONFLICT(feed) DO UPDATE SET needs_sync=true;
CREATE INDEX telegram_read_needs_sync ON telegram_read_state(feed) WHERE needs_sync;

DROP TRIGGER telegram_read_initial_added ON telegram_read_state;
DROP TRIGGER telegram_read_initial_changed ON telegram_read_state;
DROP TRIGGER telegram_read_item_added ON items;
DROP TRIGGER telegram_read_item_changed ON items;
DROP TRIGGER telegram_read_member_insert ON telegram_read_members;
DROP TRIGGER telegram_read_member_update ON telegram_read_members;
DROP FUNCTION oko_trigger_telegram_read_initial_added();
DROP FUNCTION oko_trigger_telegram_read_initial_changed();
DROP FUNCTION oko_trigger_telegram_read_item_added();
DROP FUNCTION oko_trigger_telegram_read_item_changed();
DROP FUNCTION oko_trigger_telegram_read_member_insert();
DROP FUNCTION oko_trigger_telegram_read_member_update();

CREATE OR REPLACE FUNCTION oko_membership_owner_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE ident text;
BEGIN
 ident=coalesce(NEW.publication_id,OLD.publication_id);
 INSERT INTO reader_entities SELECT kind,ident,1,oko_reader_time() FROM unnest(ARRAY['material','card','filter','theses']) AS kind
 ON CONFLICT(kind,identity) DO UPDATE SET version=reader_entities.version+1,changed=excluded.changed;
 INSERT INTO processing_dirty VALUES(ident) ON CONFLICT DO NOTHING;
 IF (TG_OP='INSERT' AND NEW.is_primary) OR (TG_OP='DELETE' AND OLD.is_primary) THEN
  INSERT INTO reader_entities VALUES ('status','runtime',1,oko_reader_time())
  ON CONFLICT(kind,identity) DO UPDATE SET version=reader_entities.version+1,changed=excluded.changed;
 END IF;
 RETURN NULL;
END $$;
DROP TABLE telegram_read_dirty;
