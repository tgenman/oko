-- A membership is the only persisted source of publication ownership.
ALTER TABLE feed_publications ADD COLUMN is_primary boolean NOT NULL DEFAULT false;
ALTER TABLE feed_publications ADD COLUMN is_unviewed boolean NOT NULL DEFAULT true;
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM items i WHERE i.viewed=0 AND i.feed_id IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM feed_publications m WHERE m.publication_id=i.id AND m.feed_id=i.feed_id)) THEN
  RAISE EXCEPTION 'publication owner is missing from feed_publications';
 END IF;
 IF EXISTS (SELECT 1 FROM items i WHERE i.viewed=0 AND i.feed_id IS NULL
            AND EXISTS (SELECT 1 FROM feed_publications m WHERE m.publication_id=i.id)) THEN
  RAISE EXCEPTION 'publication has memberships but no primary owner';
 END IF;
END $$;
-- Preserve unfinished feed cleanup before the old owner column disappears.
INSERT INTO jobs(kind,item,payload)
 SELECT 'delete',i.id,jsonb_build_object('cleanup_feed',i.feed_id)::text
 FROM items i WHERE i.viewed=1 AND i.feed_id IS NOT NULL
 ON CONFLICT(kind,item) DO UPDATE SET payload=jsonb_set(jobs.payload::jsonb,'{cleanup_feed}',to_jsonb((excluded.payload::jsonb->>'cleanup_feed')))::text;
-- The previous owner is authoritative when the membership exists.
UPDATE feed_publications m SET is_primary=true FROM items i
 WHERE i.id=m.publication_id AND i.feed_id=m.feed_id;
UPDATE feed_publications m SET is_unviewed=coalesce(i.viewed=0,false)
 FROM items i WHERE i.id=m.publication_id;
-- Preserve a stable owner even for older data whose pointer was absent.
UPDATE feed_publications m SET is_primary=true WHERE (m.feed_id,m.publication_id) IN (
 SELECT min(feed_id),publication_id FROM feed_publications f
 WHERE NOT EXISTS (SELECT 1 FROM feed_publications p WHERE p.publication_id=f.publication_id AND p.is_primary)
 GROUP BY publication_id);
CREATE UNIQUE INDEX feed_publications_one_primary ON feed_publications(publication_id) WHERE is_primary;
CREATE INDEX feed_publications_primary_feed ON feed_publications(feed_id,publication_id) WHERE is_primary;
CREATE INDEX feed_publications_primary_unviewed_feed ON feed_publications(feed_id,publication_id) WHERE is_primary AND is_unviewed;
CREATE INDEX jobs_cleanup_feed ON jobs ((payload::jsonb->>'cleanup_feed'),item) WHERE kind='delete';
CREATE FUNCTION oko_membership_primary_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM feed_publications WHERE publication_id=NEW.publication_id AND is_primary) THEN NEW.is_primary=true; END IF;
 NEW.is_unviewed=coalesce((SELECT viewed=0 FROM items WHERE id=NEW.publication_id),false);
 RETURN NEW;
END $$;
CREATE TRIGGER membership_primary_insert BEFORE INSERT ON feed_publications FOR EACH ROW EXECUTE FUNCTION oko_membership_primary_insert();
CREATE FUNCTION oko_membership_primary_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.is_primary THEN
  UPDATE feed_publications SET is_primary=true WHERE (feed_id,publication_id)=(SELECT feed_id,publication_id FROM feed_publications WHERE publication_id=OLD.publication_id ORDER BY feed_id LIMIT 1);
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER membership_primary_delete AFTER DELETE ON feed_publications FOR EACH ROW EXECUTE FUNCTION oko_membership_primary_delete();
CREATE FUNCTION oko_membership_viewed_changed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.viewed IS DISTINCT FROM OLD.viewed THEN
  UPDATE feed_publications SET is_unviewed=coalesce(NEW.viewed=0,false)
   WHERE publication_id=NEW.id AND is_unviewed IS DISTINCT FROM coalesce(NEW.viewed=0,false);
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER membership_viewed_changed AFTER UPDATE OF viewed ON items FOR EACH ROW EXECUTE FUNCTION oko_membership_viewed_changed();
DROP TRIGGER processing_item_changed ON items;
ALTER TABLE items DROP COLUMN feed_id;
CREATE INDEX items_unviewed_order ON items(published DESC,id) WHERE viewed=0;
CREATE VIEW items_by_feed AS
 SELECT i.*,m.feed_id FROM feed_publications m JOIN items i ON i.id=m.publication_id WHERE m.is_primary
 UNION ALL
 SELECT i.*,j.payload::jsonb->>'cleanup_feed' AS feed_id
 FROM items i LEFT JOIN jobs j ON j.kind='delete' AND j.item=i.id AND i.viewed=1
 WHERE NOT EXISTS (SELECT 1 FROM feed_publications m WHERE m.publication_id=i.id AND m.is_primary);
CREATE VIEW unviewed_items_by_feed AS
 SELECT i.*,m.feed_id FROM feed_publications m JOIN items i ON i.id=m.publication_id
 WHERE m.is_primary AND m.is_unviewed AND i.viewed=0;
CREATE VIEW items_read AS SELECT i.*,coalesce(
 (SELECT m.feed_id FROM feed_publications m WHERE m.publication_id=i.id AND m.is_primary),
 (SELECT j.payload::jsonb->>'cleanup_feed' FROM jobs j WHERE j.kind='delete' AND j.item=i.id AND i.viewed=1)
) AS feed_id FROM items i;
CREATE OR REPLACE FUNCTION oko_trigger_processing_item_changed() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . complete IS DISTINCT FROM OLD . complete OR NEW . members IS DISTINCT FROM OLD . members OR NEW . viewed IS DISTINCT FROM OLD . viewed THEN
        INSERT INTO processing_dirty VALUES ( NEW . id ) ON CONFLICT ( item ) DO NOTHING;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE OR REPLACE FUNCTION oko_trigger_reader_v_feed_material() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind IS DISTINCT FROM OLD . kind OR NEW . transport IS DISTINCT FROM OLD . transport THEN
        INSERT INTO reader_entities SELECT 'material' , id , 1 , oko_reader_time ( ) FROM unviewed_items_by_feed WHERE feed_id = NEW . id AND viewed = 0 ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE OR REPLACE FUNCTION oko_trigger_reader_v_hidden() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . hidden IS DISTINCT FROM OLD . hidden THEN
        INSERT INTO reader_entities VALUES ( 'card' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'filter' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'theses' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        UPDATE reader_feed_counts SET visible = visible + OLD . hidden - NEW . hidden WHERE feed = ( SELECT feed_id FROM items_read WHERE id = NEW . item AND viewed = 0 );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE OR REPLACE FUNCTION oko_trigger_reader_v_item_data() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . url IS DISTINCT FROM OLD . url OR NEW . published IS DISTINCT FROM OLD . published OR NEW . published_known IS DISTINCT FROM OLD . published_known OR NEW . source_order IS DISTINCT FROM OLD . source_order OR NEW . complete IS DISTINCT FROM OLD . complete OR NEW . members IS DISTINCT FROM OLD . members OR NEW . viewed IS DISTINCT FROM OLD . viewed THEN
        INSERT INTO reader_entities VALUES ( 'material' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'card' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE OR REPLACE FUNCTION oko_trigger_reader_v_item_delete() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'material' , OLD . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'card' , OLD . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'filter' , OLD . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'theses' , OLD . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'media' , OLD . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    UPDATE reader_feed_counts SET unviewed = unviewed - CASE WHEN OLD . viewed = 0 THEN 1 ELSE 0 END , visible = visible - CASE WHEN OLD . viewed = 0 AND coalesce ( ( SELECT hidden FROM reader_visibility WHERE item = OLD . id ) , 0 ) = 0 THEN 1 ELSE 0 END WHERE feed = (SELECT feed_id FROM items_read WHERE id = OLD . id);
    DELETE FROM reader_visibility WHERE item = OLD . id;
    RETURN NULL;
END
$oko$;

CREATE OR REPLACE FUNCTION oko_trigger_reader_v_item_inputs() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . complete IS DISTINCT FROM OLD . complete OR NEW . members IS DISTINCT FROM OLD . members THEN
        INSERT INTO reader_entities VALUES ( 'filter' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'theses' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE OR REPLACE FUNCTION oko_trigger_reader_v_item_insert() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'material' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'card' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'filter' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'theses' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'media' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_visibility VALUES ( NEW . id , 0 ) ON CONFLICT ( item ) DO NOTHING;
    INSERT INTO reader_feed_counts SELECT (SELECT feed_id FROM items_read WHERE id = NEW . id) , CASE WHEN NEW . viewed = 0 THEN 1 ELSE 0 END , CASE WHEN NEW . viewed = 0 THEN 1 ELSE 0 END WHERE (SELECT feed_id FROM items_read WHERE id = NEW . id) IS NOT NULL ON CONFLICT ( feed ) DO UPDATE SET unviewed = reader_feed_counts . unviewed + excluded . unviewed , visible = reader_feed_counts . visible + excluded . visible;
    RETURN NULL;
END
$oko$;

CREATE OR REPLACE FUNCTION oko_trigger_reader_v_item_membership() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . viewed IS DISTINCT FROM OLD . viewed THEN
        UPDATE reader_feed_counts SET unviewed = unviewed - CASE WHEN OLD . viewed = 0 THEN 1 ELSE 0 END , visible = visible - CASE WHEN OLD . viewed = 0 AND coalesce ( ( SELECT hidden FROM reader_visibility WHERE item = OLD . id ) , 0 ) = 0 THEN 1 ELSE 0 END WHERE feed = (SELECT feed_id FROM items_read WHERE id = OLD . id);
        UPDATE reader_feed_counts SET unviewed = unviewed + CASE WHEN NEW . viewed = 0 THEN 1 ELSE 0 END , visible = visible + CASE WHEN NEW . viewed = 0 AND coalesce ( ( SELECT hidden FROM reader_visibility WHERE item = NEW . id ) , 0 ) = 0 THEN 1 ELSE 0 END WHERE feed = (SELECT feed_id FROM items_read WHERE id = NEW . id);
    END IF;
    RETURN NULL;
END
$oko$;

CREATE OR REPLACE FUNCTION oko_trigger_reader_v_item_order() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . published IS DISTINCT FROM OLD . published OR NEW . published_known IS DISTINCT FROM OLD . published_known OR NEW . source_order IS DISTINCT FROM OLD . source_order THEN
        UPDATE reader_lists SET pending = 1 WHERE pending = 0 AND identity IN ( SELECT l . identity FROM reader_lists l WHERE l . scope = 'all' OR ( l . scope = 'feed' AND l . owner = (SELECT feed_id FROM items_read WHERE id = NEW . id) ) OR ( l . scope = 'folder' AND l . owner = ( SELECT folder_id FROM feeds WHERE id = (SELECT feed_id FROM items_read WHERE id = NEW . id) ) ) );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE OR REPLACE FUNCTION oko_trigger_reader_v_membership_folder() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . folder_id IS DISTINCT FROM OLD . folder_id OR NEW . importance IS DISTINCT FROM OLD . importance THEN
        PERFORM oko_memberships_changed ( NEW . id );
        PERFORM oko_folder_memberships_changed ( OLD . folder_id );
        PERFORM oko_memberships_changed ( feed_id ) FROM feed_publications WHERE publication_id IN ( SELECT id FROM items_by_feed WHERE feed_id = NEW . id );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE OR REPLACE FUNCTION oko_trigger_reader_v_membership_items_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF OLD . platform IS NOT NULL THEN
        PERFORM oko_memberships_changed ( (SELECT feed_id FROM items_read WHERE id = OLD . id) );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE OR REPLACE FUNCTION oko_trigger_reader_v_membership_items_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . platform IS NOT NULL THEN
        PERFORM oko_memberships_changed ( (SELECT feed_id FROM items_read WHERE id = NEW . id) );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE OR REPLACE FUNCTION oko_trigger_reader_v_membership_items_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . platform IS NOT NULL AND ( NEW . viewed IS DISTINCT FROM OLD . viewed OR NEW . published IS DISTINCT FROM OLD . published OR NEW . published_known IS DISTINCT FROM OLD . published_known OR NEW . source_order IS DISTINCT FROM OLD . source_order ) THEN
        PERFORM oko_memberships_changed ( feed_id ) FROM feed_publications WHERE publication_id = NEW . id;
        PERFORM oko_memberships_changed ( (SELECT feed_id FROM items_read WHERE id = OLD . id) );
        PERFORM oko_memberships_changed ( (SELECT feed_id FROM items_read WHERE id = NEW . id) );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE OR REPLACE FUNCTION oko_trigger_reader_v_originals_changed() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . value IS DISTINCT FROM OLD . value THEN
        INSERT INTO reader_entities SELECT 'material' , id , 1 , oko_reader_time ( ) FROM unviewed_items_by_feed WHERE feed_id = NEW . feed AND viewed = 0 ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE OR REPLACE FUNCTION oko_trigger_reader_v_telegram_bindings_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'feed' , OLD . feed , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities SELECT 'material' , id , 1 , oko_reader_time ( ) FROM unviewed_items_by_feed WHERE feed_id = OLD . feed AND viewed = 0 ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE OR REPLACE FUNCTION oko_trigger_reader_v_telegram_bindings_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'feed' , NEW . feed , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities SELECT 'material' , id , 1 , oko_reader_time ( ) FROM unviewed_items_by_feed WHERE feed_id = NEW . feed AND viewed = 0 ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE OR REPLACE FUNCTION oko_trigger_reader_v_telegram_bindings_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . binding IS DISTINCT FROM OLD . binding THEN
        INSERT INTO reader_entities VALUES ( 'feed' , NEW . feed , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities SELECT 'material' , id , 1 , oko_reader_time ( ) FROM unviewed_items_by_feed WHERE feed_id = NEW . feed AND viewed = 0 ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE OR REPLACE FUNCTION oko_trigger_telegram_read_item_added() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO telegram_read_dirty SELECT (SELECT feed_id FROM items_read WHERE id = NEW . id) WHERE (SELECT feed_id FROM items_read WHERE id = NEW . id) IS NOT NULL ON CONFLICT ( feed ) DO NOTHING;
    RETURN NULL;
END
$oko$;

CREATE OR REPLACE FUNCTION oko_trigger_telegram_read_item_changed() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . complete IS DISTINCT FROM OLD . complete OR NEW . members IS DISTINCT FROM OLD . members OR NEW . viewed IS DISTINCT FROM OLD . viewed THEN
        INSERT INTO telegram_read_dirty SELECT (SELECT feed_id FROM items_read WHERE id = NEW . id) WHERE (SELECT feed_id FROM items_read WHERE id = NEW . id) IS NOT NULL ON CONFLICT ( feed ) DO NOTHING;
    END IF;
    RETURN NULL;
END
$oko$;
CREATE TRIGGER processing_item_changed AFTER UPDATE OF complete, members, viewed ON items FOR EACH ROW EXECUTE FUNCTION oko_trigger_processing_item_changed();
CREATE FUNCTION oko_membership_owner_changed() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE ident text; owner text;
BEGIN
 ident=coalesce(NEW.publication_id,OLD.publication_id); owner=coalesce(NEW.feed_id,OLD.feed_id);
 INSERT INTO reader_entities SELECT kind,ident,1,oko_reader_time() FROM unnest(ARRAY['material','card','filter','theses']) AS kind
 ON CONFLICT(kind,identity) DO UPDATE SET version=reader_entities.version+1,changed=excluded.changed;
 INSERT INTO processing_dirty VALUES(ident) ON CONFLICT DO NOTHING;
 INSERT INTO telegram_read_dirty VALUES(owner) ON CONFLICT DO NOTHING;
 IF (TG_OP='INSERT' AND NEW.is_primary) OR (TG_OP='DELETE' AND OLD.is_primary) THEN
  INSERT INTO reader_entities VALUES ('status','runtime',1,oko_reader_time())
  ON CONFLICT(kind,identity) DO UPDATE SET version=reader_entities.version+1,changed=excluded.changed;
 END IF;
 RETURN NULL;
END $$;
CREATE TRIGGER membership_owner_changed AFTER INSERT OR DELETE OR UPDATE OF is_primary ON feed_publications FOR EACH ROW EXECUTE FUNCTION oko_membership_owner_changed();
