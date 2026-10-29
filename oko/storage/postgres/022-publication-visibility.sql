-- Preserve the visible/hidden result while recovery recomputes any stale stage.
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM reader_visibility WHERE hidden NOT IN (0,1)) THEN
  RAISE EXCEPTION 'invalid reader visibility';
 END IF;
END $$;
UPDATE items i SET status=CASE WHEN coalesce(v.hidden,0)=1 THEN 'filter_out'
                               WHEN i.status='filter_out' THEN 'pending_filtering'
                               ELSE i.status END
FROM (SELECT i.id,v.hidden FROM items i LEFT JOIN reader_visibility v ON v.item=i.id) v
WHERE i.id=v.id AND (coalesce(v.hidden,0)=1 AND i.status IS DISTINCT FROM 'filter_out'
                    OR coalesce(v.hidden,0)=0 AND i.status='filter_out');
DROP TRIGGER reader_v_hidden ON reader_visibility;
DROP FUNCTION oko_trigger_reader_v_hidden();
CREATE OR REPLACE FUNCTION oko_trigger_reader_v_item_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO reader_entities SELECT kind,NEW.id,1,oko_reader_time() FROM unnest(ARRAY['material','card','filter','theses','media']) AS kind
  ON CONFLICT(kind,identity) DO UPDATE SET version=reader_entities.version+1,changed=excluded.changed;
 INSERT INTO reader_entities VALUES ('status','runtime',1,oko_reader_time())
  ON CONFLICT(kind,identity) DO UPDATE SET version=reader_entities.version+1,changed=excluded.changed;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION oko_trigger_reader_v_item_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO reader_entities SELECT kind,OLD.id,1,oko_reader_time() FROM unnest(ARRAY['material','card','filter','theses','media']) AS kind
  ON CONFLICT(kind,identity) DO UPDATE SET version=reader_entities.version+1,changed=excluded.changed;
 INSERT INTO reader_entities VALUES ('status','runtime',1,oko_reader_time())
  ON CONFLICT(kind,identity) DO UPDATE SET version=reader_entities.version+1,changed=excluded.changed;
 RETURN NULL;
END $$;
DROP TABLE reader_visibility;
