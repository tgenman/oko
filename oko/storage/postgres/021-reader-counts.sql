DROP TRIGGER reader_v_counts ON reader_feed_counts;
DROP TRIGGER reader_v_counts_navigation ON reader_feed_counts;
DROP TRIGGER reader_v_folder_count ON reader_folder_counts;
DROP TRIGGER reader_v_feed_counter_added ON feeds;
DROP TRIGGER reader_v_feed_folder ON feeds;
DROP TRIGGER reader_v_folder_counter_added ON folders;
DROP TRIGGER reader_v_item_membership ON items;
DROP TRIGGER reader_v_membership_folder ON feeds;
DROP TRIGGER reader_v_membership_items_delete ON items;
DROP TRIGGER reader_v_membership_items_insert ON items;
DROP TRIGGER reader_v_membership_items_update ON items;
DROP TRIGGER reader_v_membership_visibility ON reader_visibility;
DROP FUNCTION oko_trigger_reader_v_counts();
DROP FUNCTION oko_trigger_reader_v_counts_navigation();
DROP FUNCTION oko_trigger_reader_v_folder_count();
DROP FUNCTION oko_trigger_reader_v_feed_counter_added();
DROP FUNCTION oko_trigger_reader_v_feed_folder();
DROP FUNCTION oko_trigger_reader_v_folder_counter_added();
DROP FUNCTION oko_trigger_reader_v_item_membership();
DROP FUNCTION oko_trigger_reader_v_membership_folder();
DROP FUNCTION oko_trigger_reader_v_membership_items_delete();
DROP FUNCTION oko_trigger_reader_v_membership_items_insert();
DROP FUNCTION oko_trigger_reader_v_membership_items_update();
DROP FUNCTION oko_trigger_reader_v_membership_visibility();

CREATE OR REPLACE FUNCTION oko_trigger_reader_v_hidden() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.hidden IS DISTINCT FROM OLD.hidden THEN
  INSERT INTO reader_entities SELECT kind,NEW.item,1,oko_reader_time() FROM unnest(ARRAY['card','filter','theses']) AS kind
   ON CONFLICT(kind,identity) DO UPDATE SET version=reader_entities.version+1,changed=excluded.changed;
 END IF;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION oko_trigger_reader_v_item_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO reader_entities SELECT kind,NEW.id,1,oko_reader_time() FROM unnest(ARRAY['material','card','filter','theses','media']) AS kind
  ON CONFLICT(kind,identity) DO UPDATE SET version=reader_entities.version+1,changed=excluded.changed;
 INSERT INTO reader_entities VALUES ('status','runtime',1,oko_reader_time())
  ON CONFLICT(kind,identity) DO UPDATE SET version=reader_entities.version+1,changed=excluded.changed;
 INSERT INTO reader_visibility VALUES (NEW.id,0) ON CONFLICT(item) DO NOTHING;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION oko_trigger_reader_v_item_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO reader_entities SELECT kind,OLD.id,1,oko_reader_time() FROM unnest(ARRAY['material','card','filter','theses','media']) AS kind
  ON CONFLICT(kind,identity) DO UPDATE SET version=reader_entities.version+1,changed=excluded.changed;
 INSERT INTO reader_entities VALUES ('status','runtime',1,oko_reader_time())
  ON CONFLICT(kind,identity) DO UPDATE SET version=reader_entities.version+1,changed=excluded.changed;
 DELETE FROM reader_visibility WHERE item=OLD.id;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION oko_trigger_reader_v_shared_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO reader_entities VALUES ('navigation','navigation',1,oko_reader_time())
  ON CONFLICT(kind,identity) DO UPDATE SET version=reader_entities.version+1,changed=excluded.changed;
 INSERT INTO processing_dirty VALUES (NEW.publication_id) ON CONFLICT DO NOTHING;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION oko_trigger_reader_v_shared_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO reader_entities VALUES ('navigation','navigation',1,oko_reader_time())
  ON CONFLICT(kind,identity) DO UPDATE SET version=reader_entities.version+1,changed=excluded.changed;
 INSERT INTO processing_dirty VALUES (OLD.publication_id) ON CONFLICT DO NOTHING;
 RETURN NULL;
END $$;
DROP FUNCTION oko_memberships_changed(text);
DROP FUNCTION oko_folder_memberships_changed(text);
DROP TABLE reader_feed_counts;
DROP TABLE reader_folder_counts;
DROP TABLE reader_membership_dirty;
