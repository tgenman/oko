DROP TRIGGER reader_v_item_order ON items;
DROP FUNCTION oko_trigger_reader_v_item_order();
DROP TRIGGER reader_v_feed_importance ON feeds;
DROP FUNCTION oko_trigger_reader_v_feed_importance();
DROP TRIGGER reader_v_originals_changed ON reader_originals;
DROP FUNCTION oko_trigger_reader_v_originals_changed();
DROP TRIGGER reader_v_settings_delete ON settings;
DROP TRIGGER reader_v_settings_insert ON settings;
DROP TRIGGER reader_v_settings_update ON settings;
DROP FUNCTION oko_trigger_reader_v_settings_delete();
DROP FUNCTION oko_trigger_reader_v_settings_insert();
DROP FUNCTION oko_trigger_reader_v_settings_update();

CREATE OR REPLACE FUNCTION oko_trigger_reader_v_counts() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.visible IS DISTINCT FROM OLD.visible OR NEW.unviewed IS DISTINCT FROM OLD.unviewed THEN
  UPDATE reader_folder_counts SET visible=visible+NEW.visible-OLD.visible WHERE folder=(SELECT folder_id FROM feeds WHERE id=NEW.feed);
 END IF;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION oko_trigger_reader_v_feed_counter_added() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO reader_feed_counts VALUES (NEW.id,0,0) ON CONFLICT(feed) DO NOTHING;
 RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION oko_trigger_reader_v_feed_folder() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.folder_id IS DISTINCT FROM OLD.folder_id THEN
  UPDATE reader_folder_counts SET visible=visible-coalesce((SELECT visible FROM reader_feed_counts WHERE feed=NEW.id),0) WHERE folder=OLD.folder_id;
  UPDATE reader_folder_counts SET visible=visible+coalesce((SELECT visible FROM reader_feed_counts WHERE feed=NEW.id),0) WHERE folder=NEW.folder_id;
 END IF;
 RETURN NULL;
END $$;
DROP TABLE reader_lists;
DROP TABLE reader_originals;
