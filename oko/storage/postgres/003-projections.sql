-- Native reader, processing, collection and source revision triggers.
-- Each projection changes in the same transaction as its authoritative inputs.

CREATE FUNCTION oko_trigger_collection_feed_deleted() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    DELETE FROM collection_results WHERE feed = OLD . id;
    DELETE FROM collection_attempts WHERE feed = OLD . id;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER collection_feed_deleted AFTER DELETE ON feeds
FOR EACH ROW EXECUTE FUNCTION oko_trigger_collection_feed_deleted();

CREATE FUNCTION oko_trigger_collection_job_deleted() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    DELETE FROM collection_attempts WHERE job = OLD . id;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER collection_job_deleted AFTER DELETE ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_collection_job_deleted();

CREATE FUNCTION oko_trigger_collection_queued_insert() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind IN ( 'discover' , 'fetch' , 'download_media' ) THEN
        UPDATE jobs SET queued_wall = extract(epoch FROM clock_timestamp()) WHERE id = NEW . id;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER collection_queued_insert AFTER INSERT ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_collection_queued_insert();

CREATE FUNCTION oko_trigger_collection_queued_update() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind IN ( 'discover' , 'fetch' , 'download_media' ) AND NEW . status = 'pending' AND OLD . status != 'pending' THEN
        UPDATE jobs SET queued_wall = extract(epoch FROM clock_timestamp()) WHERE id = NEW . id;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER collection_queued_update AFTER UPDATE OF status ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_collection_queued_update();

CREATE FUNCTION oko_trigger_journal_links_action_delete() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    DELETE FROM reader_material_links WHERE kind = 'action' AND record = OLD . id;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER journal_links_action_delete AFTER DELETE ON reader_action_results
FOR EACH ROW EXECUTE FUNCTION oko_trigger_journal_links_action_delete();

CREATE FUNCTION oko_trigger_journal_links_action_insert() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    DELETE FROM reader_material_links WHERE kind = 'action' AND record = NEW . id;
    INSERT INTO reader_material_links SELECT 'action' , NEW . id , oko_json_extract ( value , '$.id' ) FROM oko_json_each ( NEW . body , '$.objects' ) WHERE oko_json_extract ( value , '$.kind' ) = 'material' ON CONFLICT DO NOTHING;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER journal_links_action_insert AFTER INSERT ON reader_action_results
FOR EACH ROW EXECUTE FUNCTION oko_trigger_journal_links_action_insert();

CREATE FUNCTION oko_trigger_journal_links_action_update() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . body IS DISTINCT FROM OLD . body THEN
        DELETE FROM reader_material_links WHERE kind = 'action' AND record = NEW . id;
        INSERT INTO reader_material_links SELECT 'action' , NEW . id , oko_json_extract ( value , '$.id' ) FROM oko_json_each ( NEW . body , '$.objects' ) WHERE oko_json_extract ( value , '$.kind' ) = 'material' ON CONFLICT DO NOTHING;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER journal_links_action_update AFTER UPDATE OF body ON reader_action_results
FOR EACH ROW EXECUTE FUNCTION oko_trigger_journal_links_action_update();

CREATE FUNCTION oko_trigger_journal_links_problem_delete() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    DELETE FROM reader_material_links WHERE kind = 'problem' AND record = OLD . id;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER journal_links_problem_delete AFTER DELETE ON reader_problems
FOR EACH ROW EXECUTE FUNCTION oko_trigger_journal_links_problem_delete();

CREATE FUNCTION oko_trigger_journal_links_problem_insert() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    DELETE FROM reader_material_links WHERE kind = 'problem' AND record = NEW . id;
    INSERT INTO reader_material_links SELECT 'problem' , NEW . id , oko_json_extract ( value , '$.id' ) FROM oko_json_each ( NEW . body , '$.objects' ) WHERE oko_json_extract ( value , '$.kind' ) = 'material' ON CONFLICT DO NOTHING;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER journal_links_problem_insert AFTER INSERT ON reader_problems
FOR EACH ROW EXECUTE FUNCTION oko_trigger_journal_links_problem_insert();

CREATE FUNCTION oko_trigger_journal_links_problem_update() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . body IS DISTINCT FROM OLD . body THEN
        DELETE FROM reader_material_links WHERE kind = 'problem' AND record = NEW . id;
        INSERT INTO reader_material_links SELECT 'problem' , NEW . id , oko_json_extract ( value , '$.id' ) FROM oko_json_each ( NEW . body , '$.objects' ) WHERE oko_json_extract ( value , '$.kind' ) = 'material' ON CONFLICT DO NOTHING;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER journal_links_problem_update AFTER UPDATE OF body ON reader_problems
FOR EACH ROW EXECUTE FUNCTION oko_trigger_journal_links_problem_update();

CREATE FUNCTION oko_trigger_journal_receipt_added() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF EXISTS ( SELECT 1 FROM reader_action_results WHERE identity = NEW . identity AND sequence != NEW . sequence ) THEN
        INSERT INTO reader_action_superseded SELECT sequence , completed FROM reader_action_results WHERE identity = NEW . identity AND sequence = ( SELECT max ( sequence ) FROM reader_action_results WHERE identity = NEW . identity AND sequence < NEW . sequence ) ON CONFLICT ( sequence ) DO UPDATE SET completed = excluded . completed;
        INSERT INTO reader_action_superseded SELECT NEW . sequence , NEW . completed WHERE EXISTS ( SELECT 1 FROM reader_action_results WHERE identity = NEW . identity AND sequence > NEW . sequence ) ON CONFLICT ( sequence ) DO UPDATE SET completed = excluded . completed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER journal_receipt_added AFTER INSERT ON reader_action_results
FOR EACH ROW EXECUTE FUNCTION oko_trigger_journal_receipt_added();

CREATE FUNCTION oko_trigger_journal_receipt_completed() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    UPDATE reader_action_superseded SET completed = NEW . completed WHERE sequence = NEW . sequence;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER journal_receipt_completed AFTER UPDATE OF completed ON reader_action_results
FOR EACH ROW EXECUTE FUNCTION oko_trigger_journal_receipt_completed();

CREATE FUNCTION oko_trigger_journal_receipt_removed() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    DELETE FROM reader_action_superseded WHERE sequence = OLD . sequence OR sequence = ( SELECT max ( sequence ) FROM reader_action_results WHERE identity = OLD . identity );
    RETURN NULL;
END
$oko$;

CREATE TRIGGER journal_receipt_removed AFTER DELETE ON reader_action_results
FOR EACH ROW EXECUTE FUNCTION oko_trigger_journal_receipt_removed();

CREATE FUNCTION oko_trigger_processing_filter_changed() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind = 'filter' AND ( NEW . status IS DISTINCT FROM OLD . status OR NEW . generation IS DISTINCT FROM OLD . generation ) THEN
        INSERT INTO processing_dirty VALUES ( NEW . item ) ON CONFLICT ( item ) DO NOTHING;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER processing_filter_changed AFTER UPDATE OF status , generation ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_processing_filter_changed();

CREATE FUNCTION oko_trigger_processing_item_added() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . viewed = 0 THEN
        INSERT INTO processing_dirty VALUES ( NEW . id ) ON CONFLICT ( item ) DO NOTHING;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER processing_item_added AFTER INSERT ON items
FOR EACH ROW EXECUTE FUNCTION oko_trigger_processing_item_added();

CREATE FUNCTION oko_trigger_processing_item_changed() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . complete IS DISTINCT FROM OLD . complete OR NEW . members IS DISTINCT FROM OLD . members OR NEW . viewed IS DISTINCT FROM OLD . viewed OR NEW . feed_id IS DISTINCT FROM OLD . feed_id THEN
        INSERT INTO processing_dirty VALUES ( NEW . id ) ON CONFLICT ( item ) DO NOTHING;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER processing_item_changed AFTER UPDATE OF complete , members , viewed , feed_id ON items
FOR EACH ROW EXECUTE FUNCTION oko_trigger_processing_item_changed();

CREATE FUNCTION oko_trigger_reader_v_counts() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . visible IS DISTINCT FROM OLD . visible OR NEW . unviewed IS DISTINCT FROM OLD . unviewed THEN
        UPDATE reader_folder_counts SET visible = visible + NEW . visible - OLD . visible WHERE folder = ( SELECT folder_id FROM feeds WHERE id = NEW . feed );
        UPDATE reader_lists SET pending = 1 WHERE pending = 0 AND identity IN ( SELECT l . identity FROM reader_lists l WHERE l . scope = 'all' OR ( l . scope = 'feed' AND l . owner = NEW . feed ) OR ( l . scope = 'folder' AND l . owner = ( SELECT folder_id FROM feeds WHERE id = NEW . feed ) ) );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_counts AFTER UPDATE ON reader_feed_counts
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_counts();

CREATE FUNCTION oko_trigger_reader_v_counts_navigation() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . visible IS DISTINCT FROM OLD . visible OR ( NEW . unviewed > 0 ) IS DISTINCT FROM ( OLD . unviewed > 0 ) THEN
        INSERT INTO reader_entities VALUES ( 'navigation' , 'navigation' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_counts_navigation AFTER UPDATE ON reader_feed_counts
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_counts_navigation();

CREATE FUNCTION oko_trigger_reader_v_deleted_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'material' , OLD . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_deleted_DELETE AFTER DELETE ON source_deleted
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_deleted_DELETE();

CREATE FUNCTION oko_trigger_reader_v_deleted_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'material' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_deleted_INSERT AFTER INSERT ON source_deleted
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_deleted_INSERT();

CREATE FUNCTION oko_trigger_reader_v_deleted_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . item IS DISTINCT FROM OLD . item THEN
        INSERT INTO reader_entities VALUES ( 'material' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_deleted_UPDATE AFTER UPDATE ON source_deleted
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_deleted_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_feed_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'feed' , OLD . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'navigation' , 'navigation' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_feed_DELETE AFTER DELETE ON feeds
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_feed_DELETE();

CREATE FUNCTION oko_trigger_reader_v_feed_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'feed' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'navigation' , 'navigation' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_feed_INSERT AFTER INSERT ON feeds
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_feed_INSERT();

CREATE FUNCTION oko_trigger_reader_v_feed_counter_added() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_feed_counts VALUES ( NEW . id , 0 , 0 ) ON CONFLICT ( feed ) DO NOTHING;
    INSERT INTO reader_originals SELECT f . id , coalesce ( ( SELECT CAST ( oko_json_extract ( value , '$' ) AS INTEGER ) FROM settings WHERE scope = 'feed' AND owner = f . id AND field = 'originals' ) , ( SELECT CAST ( oko_json_extract ( value , '$' ) AS INTEGER ) FROM settings WHERE scope = 'folder' AND owner = f . folder_id AND field = 'originals' ) , ( SELECT CAST ( oko_json_extract ( value , '$' ) AS INTEGER ) FROM settings WHERE scope = 'oko' AND owner = 'global' AND field = 'originals' ) , 1 ) FROM feeds f WHERE f . id = NEW . id ON CONFLICT ( feed ) DO UPDATE SET value = excluded . value WHERE reader_originals . value IS DISTINCT FROM excluded . value;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_feed_counter_added AFTER INSERT ON feeds
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_feed_counter_added();

CREATE FUNCTION oko_trigger_reader_v_feed_folder() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . folder_id IS DISTINCT FROM OLD . folder_id THEN
        UPDATE reader_folder_counts SET visible = visible - coalesce ( ( SELECT visible FROM reader_feed_counts WHERE feed = NEW . id ) , 0 ) WHERE folder = OLD . folder_id;
        UPDATE reader_folder_counts SET visible = visible + coalesce ( ( SELECT visible FROM reader_feed_counts WHERE feed = NEW . id ) , 0 ) WHERE folder = NEW . folder_id;
        INSERT INTO reader_originals SELECT f . id , coalesce ( ( SELECT CAST ( oko_json_extract ( value , '$' ) AS INTEGER ) FROM settings WHERE scope = 'feed' AND owner = f . id AND field = 'originals' ) , ( SELECT CAST ( oko_json_extract ( value , '$' ) AS INTEGER ) FROM settings WHERE scope = 'folder' AND owner = f . folder_id AND field = 'originals' ) , ( SELECT CAST ( oko_json_extract ( value , '$' ) AS INTEGER ) FROM settings WHERE scope = 'oko' AND owner = 'global' AND field = 'originals' ) , 1 ) FROM feeds f WHERE f . id = NEW . id ON CONFLICT ( feed ) DO UPDATE SET value = excluded . value WHERE reader_originals . value IS DISTINCT FROM excluded . value;
        UPDATE reader_lists SET pending = 1 WHERE pending = 0 AND identity IN ( SELECT l . identity FROM reader_lists l WHERE l . scope = 'folder' AND l . owner IN ( OLD . folder_id , NEW . folder_id ) );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_feed_folder AFTER UPDATE ON feeds
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_feed_folder();

CREATE FUNCTION oko_trigger_reader_v_feed_importance() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . importance IS DISTINCT FROM OLD . importance THEN
        UPDATE reader_lists SET pending = 1 WHERE pending = 0 AND identity IN ( SELECT l . identity FROM reader_lists l WHERE l . sort = 'importance' AND ( l . scope = 'all' OR ( l . scope = 'folder' AND l . owner = NEW . folder_id ) ) );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_feed_importance AFTER UPDATE ON feeds
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_feed_importance();

CREATE FUNCTION oko_trigger_reader_v_feed_material() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind IS DISTINCT FROM OLD . kind OR NEW . transport IS DISTINCT FROM OLD . transport THEN
        INSERT INTO reader_entities SELECT 'material' , id , 1 , oko_reader_time ( ) FROM items WHERE feed_id = NEW . id AND viewed = 0 ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_feed_material AFTER UPDATE ON feeds
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_feed_material();

CREATE FUNCTION oko_trigger_reader_v_feed_update() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . title IS DISTINCT FROM OLD . title OR NEW . folder_id IS DISTINCT FROM OLD . folder_id OR NEW . identity IS DISTINCT FROM OLD . identity OR NEW . kind IS DISTINCT FROM OLD . kind OR NEW . original_urls IS DISTINCT FROM OLD . original_urls OR NEW . connection IS DISTINCT FROM OLD . connection OR NEW . transport IS DISTINCT FROM OLD . transport OR NEW . importance IS DISTINCT FROM OLD . importance OR NEW . initial_days IS DISTINCT FROM OLD . initial_days OR NEW . public_ai IS DISTINCT FROM OLD . public_ai OR NEW . read_history IS DISTINCT FROM OLD . read_history OR NEW . auto_read IS DISTINCT FROM OLD . auto_read OR NEW . telegram_peer IS DISTINCT FROM OLD . telegram_peer OR NEW . telegram_account IS DISTINCT FROM OLD . telegram_account OR NEW . telegram_username IS DISTINCT FROM OLD . telegram_username THEN
        INSERT INTO reader_entities VALUES ( 'feed' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_feed_update AFTER UPDATE ON feeds
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_feed_update();

CREATE FUNCTION oko_trigger_reader_v_filter_eligibility_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF OLD . kind = 'filter' THEN
        INSERT INTO reader_entities VALUES ( 'theses' , OLD . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_filter_eligibility_DELETE AFTER DELETE ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_filter_eligibility_DELETE();

CREATE FUNCTION oko_trigger_reader_v_filter_eligibility_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind = 'filter' THEN
        INSERT INTO reader_entities VALUES ( 'theses' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_filter_eligibility_INSERT AFTER INSERT ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_filter_eligibility_INSERT();

CREATE FUNCTION oko_trigger_reader_v_filter_eligibility_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind = 'filter' AND ( NEW . status = 'done' ) IS DISTINCT FROM ( OLD . status = 'done' ) THEN
        INSERT INTO reader_entities VALUES ( 'theses' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_filter_eligibility_UPDATE AFTER UPDATE ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_filter_eligibility_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_filter_input_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'filter' , OLD . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'theses' , OLD . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'card' , OLD . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_filter_input_DELETE AFTER DELETE ON filter_inputs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_filter_input_DELETE();

CREATE FUNCTION oko_trigger_reader_v_filter_input_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'filter' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'theses' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'card' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_filter_input_INSERT AFTER INSERT ON filter_inputs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_filter_input_INSERT();

CREATE FUNCTION oko_trigger_reader_v_filter_input_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . signature IS DISTINCT FROM OLD . signature THEN
        INSERT INTO reader_entities VALUES ( 'filter' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'theses' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'card' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_filter_input_UPDATE AFTER UPDATE ON filter_inputs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_filter_input_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_folder_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'folder' , OLD . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'navigation' , 'navigation' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_folder_DELETE AFTER DELETE ON folders
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_folder_DELETE();

CREATE FUNCTION oko_trigger_reader_v_folder_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'folder' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'navigation' , 'navigation' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_folder_INSERT AFTER INSERT ON folders
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_folder_INSERT();

CREATE FUNCTION oko_trigger_reader_v_folder_count() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . visible IS DISTINCT FROM OLD . visible THEN
        INSERT INTO reader_entities VALUES ( 'folder' , NEW . folder , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'navigation' , 'navigation' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_folder_count AFTER UPDATE ON reader_folder_counts
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_folder_count();

CREATE FUNCTION oko_trigger_reader_v_folder_counter_added() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_folder_counts VALUES ( NEW . id , 0 ) ON CONFLICT ( folder ) DO NOTHING;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_folder_counter_added AFTER INSERT ON folders
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_folder_counter_added();

CREATE FUNCTION oko_trigger_reader_v_folder_update() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . title IS DISTINCT FROM OLD . title OR NEW . position IS DISTINCT FROM OLD . position THEN
        INSERT INTO reader_entities VALUES ( 'folder' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'navigation' , 'navigation' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_folder_update AFTER UPDATE ON folders
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_folder_update();

CREATE FUNCTION oko_trigger_reader_v_hidden() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . hidden IS DISTINCT FROM OLD . hidden THEN
        INSERT INTO reader_entities VALUES ( 'card' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'filter' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'theses' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        UPDATE reader_feed_counts SET visible = visible + OLD . hidden - NEW . hidden WHERE feed = ( SELECT feed_id FROM items WHERE id = NEW . item AND viewed = 0 );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_hidden AFTER UPDATE ON reader_visibility
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_hidden();

CREATE FUNCTION oko_trigger_reader_v_history_members_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'feed' , OLD . feed , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_history_members_DELETE AFTER DELETE ON history_members
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_history_members_DELETE();

CREATE FUNCTION oko_trigger_reader_v_history_members_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'feed' , NEW . feed , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_history_members_INSERT AFTER INSERT ON history_members
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_history_members_INSERT();

CREATE FUNCTION oko_trigger_reader_v_history_members_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . outcome IS DISTINCT FROM OLD . outcome THEN
        INSERT INTO reader_entities VALUES ( 'feed' , NEW . feed , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_history_members_UPDATE AFTER UPDATE ON history_members
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_history_members_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_initial_history_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'feed' , OLD . feed , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_initial_history_DELETE AFTER DELETE ON initial_history
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_initial_history_DELETE();

CREATE FUNCTION oko_trigger_reader_v_initial_history_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'feed' , NEW . feed , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_initial_history_INSERT AFTER INSERT ON initial_history
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_initial_history_INSERT();

CREATE FUNCTION oko_trigger_reader_v_initial_history_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . selection IS DISTINCT FROM OLD . selection OR NEW . frozen IS DISTINCT FROM OLD . frozen OR NEW . paused IS DISTINCT FROM OLD . paused OR NEW . scan_complete IS DISTINCT FROM OLD . scan_complete THEN
        INSERT INTO reader_entities VALUES ( 'feed' , NEW . feed , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_initial_history_UPDATE AFTER UPDATE ON initial_history
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_initial_history_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_item_data() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . feed_id IS DISTINCT FROM OLD . feed_id OR NEW . url IS DISTINCT FROM OLD . url OR NEW . published IS DISTINCT FROM OLD . published OR NEW . published_known IS DISTINCT FROM OLD . published_known OR NEW . source_order IS DISTINCT FROM OLD . source_order OR NEW . complete IS DISTINCT FROM OLD . complete OR NEW . members IS DISTINCT FROM OLD . members OR NEW . viewed IS DISTINCT FROM OLD . viewed THEN
        INSERT INTO reader_entities VALUES ( 'material' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'card' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_item_data AFTER UPDATE ON items
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_item_data();

CREATE FUNCTION oko_trigger_reader_v_item_delete() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'material' , OLD . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'card' , OLD . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'filter' , OLD . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'theses' , OLD . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'media' , OLD . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    UPDATE reader_feed_counts SET unviewed = unviewed - CASE WHEN OLD . viewed = 0 THEN 1 ELSE 0 END , visible = visible - CASE WHEN OLD . viewed = 0 AND coalesce ( ( SELECT hidden FROM reader_visibility WHERE item = OLD . id ) , 0 ) = 0 THEN 1 ELSE 0 END WHERE feed = OLD . feed_id;
    DELETE FROM reader_visibility WHERE item = OLD . id;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_item_delete AFTER DELETE ON items
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_item_delete();

CREATE FUNCTION oko_trigger_reader_v_item_inputs() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . complete IS DISTINCT FROM OLD . complete OR NEW . members IS DISTINCT FROM OLD . members OR NEW . feed_id IS DISTINCT FROM OLD . feed_id THEN
        INSERT INTO reader_entities VALUES ( 'filter' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'theses' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_item_inputs AFTER UPDATE ON items
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_item_inputs();

CREATE FUNCTION oko_trigger_reader_v_item_insert() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'material' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'card' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'filter' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'theses' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'media' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_visibility VALUES ( NEW . id , 0 ) ON CONFLICT ( item ) DO NOTHING;
    INSERT INTO reader_feed_counts SELECT NEW . feed_id , CASE WHEN NEW . viewed = 0 THEN 1 ELSE 0 END , CASE WHEN NEW . viewed = 0 THEN 1 ELSE 0 END WHERE NEW . feed_id IS NOT NULL ON CONFLICT ( feed ) DO UPDATE SET unviewed = reader_feed_counts . unviewed + excluded . unviewed , visible = reader_feed_counts . visible + excluded . visible;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_item_insert AFTER INSERT ON items
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_item_insert();

CREATE FUNCTION oko_trigger_reader_v_item_media_available() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . complete IS DISTINCT FROM OLD . complete THEN
        INSERT INTO reader_entities VALUES ( 'media' , NEW . id , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_item_media_available AFTER UPDATE ON items
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_item_media_available();

CREATE FUNCTION oko_trigger_reader_v_item_membership() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . viewed IS DISTINCT FROM OLD . viewed OR NEW . feed_id IS DISTINCT FROM OLD . feed_id THEN
        UPDATE reader_feed_counts SET unviewed = unviewed - CASE WHEN OLD . viewed = 0 THEN 1 ELSE 0 END , visible = visible - CASE WHEN OLD . viewed = 0 AND coalesce ( ( SELECT hidden FROM reader_visibility WHERE item = OLD . id ) , 0 ) = 0 THEN 1 ELSE 0 END WHERE feed = OLD . feed_id;
        UPDATE reader_feed_counts SET unviewed = unviewed + CASE WHEN NEW . viewed = 0 THEN 1 ELSE 0 END , visible = visible + CASE WHEN NEW . viewed = 0 AND coalesce ( ( SELECT hidden FROM reader_visibility WHERE item = NEW . id ) , 0 ) = 0 THEN 1 ELSE 0 END WHERE feed = NEW . feed_id;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_item_membership AFTER UPDATE ON items
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_item_membership();

CREATE FUNCTION oko_trigger_reader_v_item_order() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . published IS DISTINCT FROM OLD . published OR NEW . published_known IS DISTINCT FROM OLD . published_known OR NEW . source_order IS DISTINCT FROM OLD . source_order THEN
        UPDATE reader_lists SET pending = 1 WHERE pending = 0 AND identity IN ( SELECT l . identity FROM reader_lists l WHERE l . scope = 'all' OR ( l . scope = 'feed' AND l . owner = NEW . feed_id ) OR ( l . scope = 'folder' AND l . owner = ( SELECT folder_id FROM feeds WHERE id = NEW . feed_id ) ) );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_item_order AFTER UPDATE ON items
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_item_order();

CREATE FUNCTION oko_trigger_reader_v_job_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_DELETE AFTER DELETE ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_DELETE();

CREATE FUNCTION oko_trigger_reader_v_job_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_INSERT AFTER INSERT ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_INSERT();

CREATE FUNCTION oko_trigger_reader_v_job_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . status IS DISTINCT FROM OLD . status OR NEW . error IS DISTINCT FROM OLD . error OR NEW . next IS DISTINCT FROM OLD . next OR NEW . problem_code IS DISTINCT FROM OLD . problem_code THEN
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_UPDATE AFTER UPDATE ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_job_card_filter_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF OLD . kind = 'filter' THEN
        INSERT INTO reader_entities VALUES ( 'card' , OLD . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_card_filter_DELETE AFTER DELETE ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_card_filter_DELETE();

CREATE FUNCTION oko_trigger_reader_v_job_card_filter_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind = 'filter' THEN
        INSERT INTO reader_entities VALUES ( 'card' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_card_filter_INSERT AFTER INSERT ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_card_filter_INSERT();

CREATE FUNCTION oko_trigger_reader_v_job_card_filter_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind = 'filter' AND ( NEW . status IS DISTINCT FROM OLD . status OR ( NEW . error IS NULL ) IS DISTINCT FROM ( OLD . error IS NULL ) ) THEN
        INSERT INTO reader_entities VALUES ( 'card' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_card_filter_UPDATE AFTER UPDATE ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_card_filter_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_job_card_theses_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF OLD . kind = 'theses' THEN
        INSERT INTO reader_entities VALUES ( 'card' , OLD . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_card_theses_DELETE AFTER DELETE ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_card_theses_DELETE();

CREATE FUNCTION oko_trigger_reader_v_job_card_theses_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind = 'theses' THEN
        INSERT INTO reader_entities VALUES ( 'card' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_card_theses_INSERT AFTER INSERT ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_card_theses_INSERT();

CREATE FUNCTION oko_trigger_reader_v_job_card_theses_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind = 'theses' AND ( NEW . status IS DISTINCT FROM OLD . status OR ( NEW . error IS NULL ) IS DISTINCT FROM ( OLD . error IS NULL ) ) THEN
        INSERT INTO reader_entities VALUES ( 'card' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_card_theses_UPDATE AFTER UPDATE ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_card_theses_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_job_fetch_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF OLD . kind = 'fetch' THEN
        INSERT INTO reader_entities VALUES ( 'material' , OLD . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'card' , OLD . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_fetch_DELETE AFTER DELETE ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_fetch_DELETE();

CREATE FUNCTION oko_trigger_reader_v_job_fetch_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind = 'fetch' THEN
        INSERT INTO reader_entities VALUES ( 'material' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'card' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_fetch_INSERT AFTER INSERT ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_fetch_INSERT();

CREATE FUNCTION oko_trigger_reader_v_job_fetch_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind = 'fetch' AND ( CASE WHEN coalesce ( ( SELECT complete FROM items WHERE id = OLD . item ) , 0 ) = 0 THEN OLD . status IN ( 'pending' , 'running' ) ELSE OLD . status = 'running' OR ( OLD . status = 'pending' AND OLD . next <= oko_reader_clock ( ) ) END ) IS DISTINCT FROM ( CASE WHEN coalesce ( ( SELECT complete FROM items WHERE id = NEW . item ) , 0 ) = 0 THEN NEW . status IN ( 'pending' , 'running' ) ELSE NEW . status = 'running' OR ( NEW . status = 'pending' AND NEW . next <= oko_reader_clock ( ) ) END ) THEN
        INSERT INTO reader_entities VALUES ( 'material' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'card' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_fetch_UPDATE AFTER UPDATE ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_fetch_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_job_filter_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF OLD . kind = 'filter' THEN
        INSERT INTO reader_entities VALUES ( 'filter' , OLD . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_filter_DELETE AFTER DELETE ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_filter_DELETE();

CREATE FUNCTION oko_trigger_reader_v_job_filter_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind = 'filter' THEN
        INSERT INTO reader_entities VALUES ( 'filter' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_filter_INSERT AFTER INSERT ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_filter_INSERT();

CREATE FUNCTION oko_trigger_reader_v_job_filter_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind = 'filter' AND ( NEW . status IS DISTINCT FROM OLD . status OR NEW . error IS DISTINCT FROM OLD . error OR NEW . next IS DISTINCT FROM OLD . next ) THEN
        INSERT INTO reader_entities VALUES ( 'filter' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_filter_UPDATE AFTER UPDATE ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_filter_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_job_history_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF OLD . kind = 'fetch' AND OLD . error IS NOT NULL THEN
        INSERT INTO reader_entities SELECT DISTINCT 'feed' , h . feed , 1 , oko_reader_time ( ) FROM history_members h WHERE h . item = OLD . item AND h . outcome IN ( 'retrying' , 'unavailable' ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_history_DELETE AFTER DELETE ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_history_DELETE();

CREATE FUNCTION oko_trigger_reader_v_job_history_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind = 'fetch' AND NEW . error IS NOT NULL THEN
        INSERT INTO reader_entities SELECT DISTINCT 'feed' , h . feed , 1 , oko_reader_time ( ) FROM history_members h WHERE h . item = NEW . item AND h . outcome IN ( 'retrying' , 'unavailable' ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_history_INSERT AFTER INSERT ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_history_INSERT();

CREATE FUNCTION oko_trigger_reader_v_job_history_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind = 'fetch' AND NEW . error IS DISTINCT FROM OLD . error THEN
        INSERT INTO reader_entities SELECT DISTINCT 'feed' , h . feed , 1 , oko_reader_time ( ) FROM history_members h WHERE h . item = NEW . item AND h . outcome IN ( 'retrying' , 'unavailable' ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_history_UPDATE AFTER UPDATE ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_history_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_job_theses_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF OLD . kind = 'theses' THEN
        INSERT INTO reader_entities VALUES ( 'theses' , OLD . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_theses_DELETE AFTER DELETE ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_theses_DELETE();

CREATE FUNCTION oko_trigger_reader_v_job_theses_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind = 'theses' THEN
        INSERT INTO reader_entities VALUES ( 'theses' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_theses_INSERT AFTER INSERT ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_theses_INSERT();

CREATE FUNCTION oko_trigger_reader_v_job_theses_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind = 'theses' AND ( NEW . status IS DISTINCT FROM OLD . status OR NEW . error IS DISTINCT FROM OLD . error OR NEW . next IS DISTINCT FROM OLD . next ) THEN
        INSERT INTO reader_entities VALUES ( 'theses' , NEW . item , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_theses_UPDATE AFTER UPDATE ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_theses_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_job_time_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind IN ( 'fetch' , 'filter' , 'theses' ) THEN
        INSERT INTO reader_job_time VALUES ( NEW . id , NEW . next , CASE WHEN NEW . status != 'pending' OR NEW . next <= oko_reader_clock ( ) THEN 1 ELSE 0 END ) ON CONFLICT ( job ) DO UPDATE SET deadline = excluded . deadline , ready = excluded . ready;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_time_INSERT AFTER INSERT ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_time_INSERT();

CREATE FUNCTION oko_trigger_reader_v_job_time_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . kind IN ( 'fetch' , 'filter' , 'theses' ) THEN
        INSERT INTO reader_job_time VALUES ( NEW . id , NEW . next , CASE WHEN NEW . status != 'pending' OR NEW . next <= oko_reader_clock ( ) THEN 1 ELSE 0 END ) ON CONFLICT ( job ) DO UPDATE SET deadline = excluded . deadline , ready = excluded . ready;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_time_UPDATE AFTER UPDATE ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_time_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_job_time_delete() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    DELETE FROM reader_job_time WHERE job = OLD . id;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_job_time_delete AFTER DELETE ON jobs
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_job_time_delete();

CREATE FUNCTION oko_trigger_reader_v_membership_folder() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . folder_id IS DISTINCT FROM OLD . folder_id OR NEW . importance IS DISTINCT FROM OLD . importance THEN
        PERFORM oko_memberships_changed ( NEW . id );
        PERFORM oko_folder_memberships_changed ( OLD . folder_id );
        PERFORM oko_memberships_changed ( feed_id ) FROM feed_publications WHERE publication_id IN ( SELECT id FROM items WHERE feed_id = NEW . id );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_membership_folder AFTER UPDATE ON feeds
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_membership_folder();

CREATE FUNCTION oko_trigger_reader_v_membership_items_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF OLD . platform IS NOT NULL THEN
        PERFORM oko_memberships_changed ( OLD . feed_id );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_membership_items_DELETE AFTER DELETE ON items
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_membership_items_DELETE();

CREATE FUNCTION oko_trigger_reader_v_membership_items_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . platform IS NOT NULL THEN
        PERFORM oko_memberships_changed ( NEW . feed_id );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_membership_items_INSERT AFTER INSERT ON items
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_membership_items_INSERT();

CREATE FUNCTION oko_trigger_reader_v_membership_items_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . platform IS NOT NULL AND ( NEW . viewed IS DISTINCT FROM OLD . viewed OR NEW . feed_id IS DISTINCT FROM OLD . feed_id OR NEW . published IS DISTINCT FROM OLD . published OR NEW . published_known IS DISTINCT FROM OLD . published_known OR NEW . source_order IS DISTINCT FROM OLD . source_order ) THEN
        PERFORM oko_memberships_changed ( feed_id ) FROM feed_publications WHERE publication_id = NEW . id;
        PERFORM oko_memberships_changed ( OLD . feed_id );
        PERFORM oko_memberships_changed ( NEW . feed_id );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_membership_items_UPDATE AFTER UPDATE ON items
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_membership_items_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_membership_visibility() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . hidden IS DISTINCT FROM OLD . hidden THEN
        PERFORM oko_memberships_changed ( feed_id ) FROM feed_publications WHERE publication_id = NEW . item;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_membership_visibility AFTER UPDATE ON reader_visibility
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_membership_visibility();

CREATE FUNCTION oko_trigger_reader_v_originals_changed() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . value IS DISTINCT FROM OLD . value THEN
        INSERT INTO reader_entities SELECT 'material' , id , 1 , oko_reader_time ( ) FROM items WHERE feed_id = NEW . feed AND viewed = 0 ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_originals_changed AFTER UPDATE ON reader_originals
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_originals_changed();

CREATE FUNCTION oko_trigger_reader_v_profile() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . state IS DISTINCT FROM OLD . state OR NEW . user_id IS DISTINCT FROM OLD . user_id THEN
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities SELECT 'feed' , feed , 1 , oko_reader_time ( ) FROM telegram_bindings WHERE oko_json_extract ( binding , '$.role' ) = NEW . role ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_profile AFTER UPDATE ON telegram_profiles
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_profile();

CREATE FUNCTION oko_trigger_reader_v_profile_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities SELECT 'feed' , feed , 1 , oko_reader_time ( ) FROM telegram_bindings WHERE oko_json_extract ( binding , '$.role' ) = OLD . role ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_profile_DELETE AFTER DELETE ON telegram_profiles
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_profile_DELETE();

CREATE FUNCTION oko_trigger_reader_v_profile_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities SELECT 'feed' , feed , 1 , oko_reader_time ( ) FROM telegram_bindings WHERE oko_json_extract ( binding , '$.role' ) = NEW . role ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_profile_INSERT AFTER INSERT ON telegram_profiles
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_profile_INSERT();

CREATE FUNCTION oko_trigger_reader_v_profile_message() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . message IS DISTINCT FROM OLD . message THEN
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_profile_message AFTER UPDATE ON telegram_profiles
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_profile_message();

CREATE FUNCTION oko_trigger_reader_v_reader_action_results_status_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_reader_action_results_status_DELETE AFTER DELETE ON reader_action_results
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_reader_action_results_status_DELETE();

CREATE FUNCTION oko_trigger_reader_v_reader_action_results_status_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_reader_action_results_status_INSERT AFTER INSERT ON reader_action_results
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_reader_action_results_status_INSERT();

CREATE FUNCTION oko_trigger_reader_v_reader_action_results_status_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . result IS DISTINCT FROM OLD . result THEN
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_reader_action_results_status_UPDATE AFTER UPDATE ON reader_action_results
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_reader_action_results_status_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_reader_problems_status_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_reader_problems_status_DELETE AFTER DELETE ON reader_problems
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_reader_problems_status_DELETE();

CREATE FUNCTION oko_trigger_reader_v_reader_problems_status_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_reader_problems_status_INSERT AFTER INSERT ON reader_problems
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_reader_problems_status_INSERT();

CREATE FUNCTION oko_trigger_reader_v_reader_problems_status_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . body IS DISTINCT FROM OLD . body OR NEW . finished IS DISTINCT FROM OLD . finished OR NEW . outcome IS DISTINCT FROM OLD . outcome THEN
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_reader_problems_status_UPDATE AFTER UPDATE ON reader_problems
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_reader_problems_status_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_runtime() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . body IS DISTINCT FROM OLD . body THEN
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_runtime AFTER UPDATE ON reader_runtime
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_runtime();

CREATE FUNCTION oko_trigger_reader_v_runtime_first() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_runtime_first AFTER INSERT ON reader_runtime
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_runtime_first();

CREATE FUNCTION oko_trigger_reader_v_settings_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF OLD . field = 'originals' THEN
        INSERT INTO reader_originals SELECT f . id , coalesce ( ( SELECT CAST ( oko_json_extract ( value , '$' ) AS INTEGER ) FROM settings WHERE scope = 'feed' AND owner = f . id AND field = 'originals' ) , ( SELECT CAST ( oko_json_extract ( value , '$' ) AS INTEGER ) FROM settings WHERE scope = 'folder' AND owner = f . folder_id AND field = 'originals' ) , ( SELECT CAST ( oko_json_extract ( value , '$' ) AS INTEGER ) FROM settings WHERE scope = 'oko' AND owner = 'global' AND field = 'originals' ) , 1 ) FROM feeds f WHERE OLD . scope = 'oko' OR ( OLD . scope = 'feed' AND f . id = OLD . owner ) OR ( OLD . scope = 'folder' AND f . folder_id = OLD . owner ) ON CONFLICT ( feed ) DO UPDATE SET value = excluded . value WHERE reader_originals . value IS DISTINCT FROM excluded . value;
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_settings_DELETE AFTER DELETE ON settings
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_settings_DELETE();

CREATE FUNCTION oko_trigger_reader_v_settings_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . field = 'originals' THEN
        INSERT INTO reader_originals SELECT f . id , coalesce ( ( SELECT CAST ( oko_json_extract ( value , '$' ) AS INTEGER ) FROM settings WHERE scope = 'feed' AND owner = f . id AND field = 'originals' ) , ( SELECT CAST ( oko_json_extract ( value , '$' ) AS INTEGER ) FROM settings WHERE scope = 'folder' AND owner = f . folder_id AND field = 'originals' ) , ( SELECT CAST ( oko_json_extract ( value , '$' ) AS INTEGER ) FROM settings WHERE scope = 'oko' AND owner = 'global' AND field = 'originals' ) , 1 ) FROM feeds f WHERE NEW . scope = 'oko' OR ( NEW . scope = 'feed' AND f . id = NEW . owner ) OR ( NEW . scope = 'folder' AND f . folder_id = NEW . owner ) ON CONFLICT ( feed ) DO UPDATE SET value = excluded . value WHERE reader_originals . value IS DISTINCT FROM excluded . value;
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_settings_INSERT AFTER INSERT ON settings
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_settings_INSERT();

CREATE FUNCTION oko_trigger_reader_v_settings_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . field = 'originals' AND NEW . value IS DISTINCT FROM OLD . value THEN
        INSERT INTO reader_originals SELECT f . id , coalesce ( ( SELECT CAST ( oko_json_extract ( value , '$' ) AS INTEGER ) FROM settings WHERE scope = 'feed' AND owner = f . id AND field = 'originals' ) , ( SELECT CAST ( oko_json_extract ( value , '$' ) AS INTEGER ) FROM settings WHERE scope = 'folder' AND owner = f . folder_id AND field = 'originals' ) , ( SELECT CAST ( oko_json_extract ( value , '$' ) AS INTEGER ) FROM settings WHERE scope = 'oko' AND owner = 'global' AND field = 'originals' ) , 1 ) FROM feeds f WHERE NEW . scope = 'oko' OR ( NEW . scope = 'feed' AND f . id = NEW . owner ) OR ( NEW . scope = 'folder' AND f . folder_id = NEW . owner ) ON CONFLICT ( feed ) DO UPDATE SET value = excluded . value WHERE reader_originals . value IS DISTINCT FROM excluded . value;
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_settings_UPDATE AFTER UPDATE ON settings
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_settings_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_settings_status_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_settings_status_DELETE AFTER DELETE ON settings
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_settings_status_DELETE();

CREATE FUNCTION oko_trigger_reader_v_settings_status_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_settings_status_INSERT AFTER INSERT ON settings
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_settings_status_INSERT();

CREATE FUNCTION oko_trigger_reader_v_settings_status_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . value IS DISTINCT FROM OLD . value THEN
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_settings_status_UPDATE AFTER UPDATE ON settings
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_settings_status_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_shared_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    PERFORM oko_memberships_changed ( OLD . feed_id );
    INSERT INTO reader_entities VALUES ( 'navigation' , 'navigation' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO processing_dirty VALUES ( OLD . publication_id ) ON CONFLICT DO NOTHING;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_shared_DELETE AFTER DELETE ON feed_publications
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_shared_DELETE();

CREATE FUNCTION oko_trigger_reader_v_shared_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    PERFORM oko_memberships_changed ( NEW . feed_id );
    INSERT INTO reader_entities VALUES ( 'navigation' , 'navigation' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO processing_dirty VALUES ( NEW . publication_id ) ON CONFLICT DO NOTHING;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_shared_INSERT AFTER INSERT ON feed_publications
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_shared_INSERT();

CREATE FUNCTION oko_trigger_reader_v_state_status_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF OLD . key = 'work-enabled' OR OLD . key LIKE 'feed-status:%' OR OLD . key = 'processor:codex' THEN
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_state_status_DELETE AFTER DELETE ON state
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_state_status_DELETE();

CREATE FUNCTION oko_trigger_reader_v_state_status_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . key = 'work-enabled' OR NEW . key LIKE 'feed-status:%' OR NEW . key = 'processor:codex' THEN
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_state_status_INSERT AFTER INSERT ON state
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_state_status_INSERT();

CREATE FUNCTION oko_trigger_reader_v_state_update() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . value IS DISTINCT FROM OLD . value AND ( NEW . key = 'work-enabled' OR NEW . key LIKE 'feed-status:%' OR NEW . key = 'processor:codex' ) THEN
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_state_update AFTER UPDATE ON state
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_state_update();

CREATE FUNCTION oko_trigger_reader_v_telegram_avatars_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'feed' , OLD . feed , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_telegram_avatars_DELETE AFTER DELETE ON telegram_avatars
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_telegram_avatars_DELETE();

CREATE FUNCTION oko_trigger_reader_v_telegram_avatars_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'feed' , NEW . feed , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_telegram_avatars_INSERT AFTER INSERT ON telegram_avatars
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_telegram_avatars_INSERT();

CREATE FUNCTION oko_trigger_reader_v_telegram_avatars_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . digest IS DISTINCT FROM OLD . digest THEN
        INSERT INTO reader_entities VALUES ( 'feed' , NEW . feed , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_telegram_avatars_UPDATE AFTER UPDATE ON telegram_avatars
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_telegram_avatars_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_telegram_bindings_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'feed' , OLD . feed , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities SELECT 'material' , id , 1 , oko_reader_time ( ) FROM items WHERE feed_id = OLD . feed AND viewed = 0 ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_telegram_bindings_DELETE AFTER DELETE ON telegram_bindings
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_telegram_bindings_DELETE();

CREATE FUNCTION oko_trigger_reader_v_telegram_bindings_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'feed' , NEW . feed , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    INSERT INTO reader_entities SELECT 'material' , id , 1 , oko_reader_time ( ) FROM items WHERE feed_id = NEW . feed AND viewed = 0 ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_telegram_bindings_INSERT AFTER INSERT ON telegram_bindings
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_telegram_bindings_INSERT();

CREATE FUNCTION oko_trigger_reader_v_telegram_bindings_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . binding IS DISTINCT FROM OLD . binding THEN
        INSERT INTO reader_entities VALUES ( 'feed' , NEW . feed , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities SELECT 'material' , id , 1 , oko_reader_time ( ) FROM items WHERE feed_id = NEW . feed AND viewed = 0 ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_telegram_bindings_UPDATE AFTER UPDATE ON telegram_bindings
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_telegram_bindings_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_telegram_read_members_status_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_telegram_read_members_status_DELETE AFTER DELETE ON telegram_read_members
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_telegram_read_members_status_DELETE();

CREATE FUNCTION oko_trigger_reader_v_telegram_read_members_status_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_telegram_read_members_status_INSERT AFTER INSERT ON telegram_read_members
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_telegram_read_members_status_INSERT();

CREATE FUNCTION oko_trigger_reader_v_telegram_read_members_status_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . eligible IS DISTINCT FROM OLD . eligible OR NEW . missing IS DISTINCT FROM OLD . missing OR NEW . reason IS DISTINCT FROM OLD . reason THEN
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_telegram_read_members_status_UPDATE AFTER UPDATE ON telegram_read_members
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_telegram_read_members_status_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_telegram_read_state_status_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_telegram_read_state_status_DELETE AFTER DELETE ON telegram_read_state
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_telegram_read_state_status_DELETE();

CREATE FUNCTION oko_trigger_reader_v_telegram_read_state_status_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_telegram_read_state_status_INSERT AFTER INSERT ON telegram_read_state
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_telegram_read_state_status_INSERT();

CREATE FUNCTION oko_trigger_reader_v_telegram_read_state_status_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . since IS DISTINCT FROM OLD . since OR NEW . initial IS DISTINCT FROM OLD . initial OR NEW . confirmed IS DISTINCT FROM OLD . confirmed OR NEW . waiting IS DISTINCT FROM OLD . waiting OR NEW . error IS DISTINCT FROM OLD . error THEN
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_telegram_read_state_status_UPDATE AFTER UPDATE ON telegram_read_state
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_telegram_read_state_status_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_time_ready() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . ready IS DISTINCT FROM OLD . ready THEN
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities SELECT CASE kind WHEN 'fetch' THEN 'material' ELSE kind END , item , 1 , oko_reader_time ( ) FROM jobs WHERE id = NEW . job AND ( kind = 'fetch' OR error IS NOT NULL ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
        INSERT INTO reader_entities SELECT 'card' , item , 1 , oko_reader_time ( ) FROM jobs WHERE id = NEW . job AND kind = 'fetch' ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_time_ready AFTER UPDATE ON reader_job_time
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_time_ready();

CREATE FUNCTION oko_trigger_reader_v_youtube_access_status_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_youtube_access_status_DELETE AFTER DELETE ON youtube_access
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_youtube_access_status_DELETE();

CREATE FUNCTION oko_trigger_reader_v_youtube_access_status_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_youtube_access_status_INSERT AFTER INSERT ON youtube_access
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_youtube_access_status_INSERT();

CREATE FUNCTION oko_trigger_reader_v_youtube_access_status_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . next_at IS DISTINCT FROM OLD . next_at OR NEW . blocked_until IS DISTINCT FROM OLD . blocked_until OR NEW . strikes IS DISTINCT FROM OLD . strikes THEN
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_youtube_access_status_UPDATE AFTER UPDATE ON youtube_access
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_youtube_access_status_UPDATE();

CREATE FUNCTION oko_trigger_reader_v_youtube_time() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . paused IS DISTINCT FROM OLD . paused THEN
        INSERT INTO reader_entities VALUES ( 'status' , 'runtime' , 1 , oko_reader_time ( ) ) ON CONFLICT ( kind , identity ) DO UPDATE SET version = reader_entities . version + 1 , changed = excluded . changed;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER reader_v_youtube_time AFTER UPDATE ON reader_youtube_time
FOR EACH ROW EXECUTE FUNCTION oko_trigger_reader_v_youtube_time();

CREATE FUNCTION oko_trigger_source_configuration_feeds_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO state VALUES ( 'sources-version' , '1' ) ON CONFLICT ( key ) DO UPDATE SET value = CAST ( CAST ( state . value AS INTEGER ) + 1 AS TEXT );
    RETURN NULL;
END
$oko$;

CREATE TRIGGER source_configuration_feeds_DELETE AFTER DELETE ON feeds
FOR EACH ROW EXECUTE FUNCTION oko_trigger_source_configuration_feeds_DELETE();

CREATE FUNCTION oko_trigger_source_configuration_feeds_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO state VALUES ( 'sources-version' , '1' ) ON CONFLICT ( key ) DO UPDATE SET value = CAST ( CAST ( state . value AS INTEGER ) + 1 AS TEXT );
    RETURN NULL;
END
$oko$;

CREATE TRIGGER source_configuration_feeds_INSERT AFTER INSERT ON feeds
FOR EACH ROW EXECUTE FUNCTION oko_trigger_source_configuration_feeds_INSERT();

CREATE FUNCTION oko_trigger_source_configuration_feeds_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . id IS DISTINCT FROM OLD . id OR NEW . identity IS DISTINCT FROM OLD . identity OR NEW . kind IS DISTINCT FROM OLD . kind OR NEW . folder_id IS DISTINCT FROM OLD . folder_id OR NEW . original_urls IS DISTINCT FROM OLD . original_urls OR NEW . connection IS DISTINCT FROM OLD . connection OR NEW . transport IS DISTINCT FROM OLD . transport OR NEW . importance IS DISTINCT FROM OLD . importance OR NEW . initial_days IS DISTINCT FROM OLD . initial_days OR NEW . public_ai IS DISTINCT FROM OLD . public_ai OR NEW . read_history IS DISTINCT FROM OLD . read_history OR NEW . auto_read IS DISTINCT FROM OLD . auto_read OR NEW . telegram_peer IS DISTINCT FROM OLD . telegram_peer OR NEW . telegram_account IS DISTINCT FROM OLD . telegram_account OR NEW . telegram_username IS DISTINCT FROM OLD . telegram_username THEN
        INSERT INTO state VALUES ( 'sources-version' , '1' ) ON CONFLICT ( key ) DO UPDATE SET value = CAST ( CAST ( state . value AS INTEGER ) + 1 AS TEXT );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER source_configuration_feeds_UPDATE AFTER UPDATE ON feeds
FOR EACH ROW EXECUTE FUNCTION oko_trigger_source_configuration_feeds_UPDATE();

CREATE FUNCTION oko_trigger_source_configuration_initial_history_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO state VALUES ( 'sources-version' , '1' ) ON CONFLICT ( key ) DO UPDATE SET value = CAST ( CAST ( state . value AS INTEGER ) + 1 AS TEXT );
    RETURN NULL;
END
$oko$;

CREATE TRIGGER source_configuration_initial_history_DELETE AFTER DELETE ON initial_history
FOR EACH ROW EXECUTE FUNCTION oko_trigger_source_configuration_initial_history_DELETE();

CREATE FUNCTION oko_trigger_source_configuration_initial_history_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO state VALUES ( 'sources-version' , '1' ) ON CONFLICT ( key ) DO UPDATE SET value = CAST ( CAST ( state . value AS INTEGER ) + 1 AS TEXT );
    RETURN NULL;
END
$oko$;

CREATE TRIGGER source_configuration_initial_history_INSERT AFTER INSERT ON initial_history
FOR EACH ROW EXECUTE FUNCTION oko_trigger_source_configuration_initial_history_INSERT();

CREATE FUNCTION oko_trigger_source_configuration_initial_history_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . feed IS DISTINCT FROM OLD . feed OR NEW . frozen IS DISTINCT FROM OLD . frozen OR NEW . paused IS DISTINCT FROM OLD . paused OR NEW . scan_complete IS DISTINCT FROM OLD . scan_complete OR oko_json_extract ( NEW . selection , '$.mode' ) IS DISTINCT FROM oko_json_extract ( OLD . selection , '$.mode' ) OR oko_json_extract ( NEW . selection , '$.count' ) IS DISTINCT FROM oko_json_extract ( OLD . selection , '$.count' ) OR oko_json_extract ( NEW . selection , '$.date' ) IS DISTINCT FROM oko_json_extract ( OLD . selection , '$.date' ) OR oko_json_extract ( NEW . selection , '$.timezone' ) IS DISTINCT FROM oko_json_extract ( OLD . selection , '$.timezone' ) OR oko_json_extract ( NEW . selection , '$.since' ) IS DISTINCT FROM oko_json_extract ( OLD . selection , '$.since' ) OR oko_json_extract ( NEW . selection , '$.run' ) IS DISTINCT FROM oko_json_extract ( OLD . selection , '$.run' ) OR oko_json_extract ( NEW . selection , '$.resume' ) IS DISTINCT FROM oko_json_extract ( OLD . selection , '$.resume' ) OR oko_json_extract ( NEW . selection , '$.cancelled' ) IS DISTINCT FROM oko_json_extract ( OLD . selection , '$.cancelled' ) THEN
        INSERT INTO state VALUES ( 'sources-version' , '1' ) ON CONFLICT ( key ) DO UPDATE SET value = CAST ( CAST ( state . value AS INTEGER ) + 1 AS TEXT );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER source_configuration_initial_history_UPDATE AFTER UPDATE ON initial_history
FOR EACH ROW EXECUTE FUNCTION oko_trigger_source_configuration_initial_history_UPDATE();

CREATE FUNCTION oko_trigger_source_configuration_settings_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO state VALUES ( 'sources-version' , '1' ) ON CONFLICT ( key ) DO UPDATE SET value = CAST ( CAST ( state . value AS INTEGER ) + 1 AS TEXT );
    RETURN NULL;
END
$oko$;

CREATE TRIGGER source_configuration_settings_DELETE AFTER DELETE ON settings
FOR EACH ROW EXECUTE FUNCTION oko_trigger_source_configuration_settings_DELETE();

CREATE FUNCTION oko_trigger_source_configuration_settings_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO state VALUES ( 'sources-version' , '1' ) ON CONFLICT ( key ) DO UPDATE SET value = CAST ( CAST ( state . value AS INTEGER ) + 1 AS TEXT );
    RETURN NULL;
END
$oko$;

CREATE TRIGGER source_configuration_settings_INSERT AFTER INSERT ON settings
FOR EACH ROW EXECUTE FUNCTION oko_trigger_source_configuration_settings_INSERT();

CREATE FUNCTION oko_trigger_source_configuration_settings_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . scope IS DISTINCT FROM OLD . scope OR NEW . owner IS DISTINCT FROM OLD . owner OR NEW . field IS DISTINCT FROM OLD . field OR NEW . value IS DISTINCT FROM OLD . value THEN
        INSERT INTO state VALUES ( 'sources-version' , '1' ) ON CONFLICT ( key ) DO UPDATE SET value = CAST ( CAST ( state . value AS INTEGER ) + 1 AS TEXT );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER source_configuration_settings_UPDATE AFTER UPDATE ON settings
FOR EACH ROW EXECUTE FUNCTION oko_trigger_source_configuration_settings_UPDATE();

CREATE FUNCTION oko_trigger_source_configuration_telegram_bindings_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO state VALUES ( 'sources-version' , '1' ) ON CONFLICT ( key ) DO UPDATE SET value = CAST ( CAST ( state . value AS INTEGER ) + 1 AS TEXT );
    RETURN NULL;
END
$oko$;

CREATE TRIGGER source_configuration_telegram_bindings_DELETE AFTER DELETE ON telegram_bindings
FOR EACH ROW EXECUTE FUNCTION oko_trigger_source_configuration_telegram_bindings_DELETE();

CREATE FUNCTION oko_trigger_source_configuration_telegram_bindings_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO state VALUES ( 'sources-version' , '1' ) ON CONFLICT ( key ) DO UPDATE SET value = CAST ( CAST ( state . value AS INTEGER ) + 1 AS TEXT );
    RETURN NULL;
END
$oko$;

CREATE TRIGGER source_configuration_telegram_bindings_INSERT AFTER INSERT ON telegram_bindings
FOR EACH ROW EXECUTE FUNCTION oko_trigger_source_configuration_telegram_bindings_INSERT();

CREATE FUNCTION oko_trigger_source_configuration_telegram_bindings_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . feed IS DISTINCT FROM OLD . feed OR NEW . binding IS DISTINCT FROM OLD . binding THEN
        INSERT INTO state VALUES ( 'sources-version' , '1' ) ON CONFLICT ( key ) DO UPDATE SET value = CAST ( CAST ( state . value AS INTEGER ) + 1 AS TEXT );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER source_configuration_telegram_bindings_UPDATE AFTER UPDATE ON telegram_bindings
FOR EACH ROW EXECUTE FUNCTION oko_trigger_source_configuration_telegram_bindings_UPDATE();

CREATE FUNCTION oko_trigger_source_configuration_telegram_profiles_DELETE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO state VALUES ( 'sources-version' , '1' ) ON CONFLICT ( key ) DO UPDATE SET value = CAST ( CAST ( state . value AS INTEGER ) + 1 AS TEXT );
    RETURN NULL;
END
$oko$;

CREATE TRIGGER source_configuration_telegram_profiles_DELETE AFTER DELETE ON telegram_profiles
FOR EACH ROW EXECUTE FUNCTION oko_trigger_source_configuration_telegram_profiles_DELETE();

CREATE FUNCTION oko_trigger_source_configuration_telegram_profiles_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO state VALUES ( 'sources-version' , '1' ) ON CONFLICT ( key ) DO UPDATE SET value = CAST ( CAST ( state . value AS INTEGER ) + 1 AS TEXT );
    RETURN NULL;
END
$oko$;

CREATE TRIGGER source_configuration_telegram_profiles_INSERT AFTER INSERT ON telegram_profiles
FOR EACH ROW EXECUTE FUNCTION oko_trigger_source_configuration_telegram_profiles_INSERT();

CREATE FUNCTION oko_trigger_source_configuration_telegram_profiles_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . role IS DISTINCT FROM OLD . role OR NEW . account IS DISTINCT FROM OLD . account OR NEW . user_id IS DISTINCT FROM OLD . user_id OR NEW . generation IS DISTINCT FROM OLD . generation OR NEW . state IS DISTINCT FROM OLD . state THEN
        INSERT INTO state VALUES ( 'sources-version' , '1' ) ON CONFLICT ( key ) DO UPDATE SET value = CAST ( CAST ( state . value AS INTEGER ) + 1 AS TEXT );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER source_configuration_telegram_profiles_UPDATE AFTER UPDATE ON telegram_profiles
FOR EACH ROW EXECUTE FUNCTION oko_trigger_source_configuration_telegram_profiles_UPDATE();

CREATE FUNCTION oko_trigger_source_read_initial() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . initial IS DISTINCT FROM OLD . initial THEN
        INSERT INTO state VALUES ( 'sources-version' , '1' ) ON CONFLICT ( key ) DO UPDATE SET value = CAST ( CAST ( state . value AS INTEGER ) + 1 AS TEXT );
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER source_read_initial AFTER UPDATE OF initial ON telegram_read_state
FOR EACH ROW EXECUTE FUNCTION oko_trigger_source_read_initial();

CREATE FUNCTION oko_trigger_telegram_read_initial_added() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO telegram_read_dirty VALUES ( NEW . feed ) ON CONFLICT ( feed ) DO NOTHING;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER telegram_read_initial_added AFTER INSERT ON telegram_read_state
FOR EACH ROW EXECUTE FUNCTION oko_trigger_telegram_read_initial_added();

CREATE FUNCTION oko_trigger_telegram_read_initial_changed() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . initial IS DISTINCT FROM OLD . initial OR NEW . since IS DISTINCT FROM OLD . since THEN
        INSERT INTO telegram_read_dirty VALUES ( NEW . feed ) ON CONFLICT ( feed ) DO NOTHING;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER telegram_read_initial_changed AFTER UPDATE OF initial , since ON telegram_read_state
FOR EACH ROW EXECUTE FUNCTION oko_trigger_telegram_read_initial_changed();

CREATE FUNCTION oko_trigger_telegram_read_item_added() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO telegram_read_dirty SELECT NEW . feed_id WHERE NEW . feed_id IS NOT NULL ON CONFLICT ( feed ) DO NOTHING;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER telegram_read_item_added AFTER INSERT ON items
FOR EACH ROW EXECUTE FUNCTION oko_trigger_telegram_read_item_added();

CREATE FUNCTION oko_trigger_telegram_read_item_changed() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    IF NEW . complete IS DISTINCT FROM OLD . complete OR NEW . members IS DISTINCT FROM OLD . members OR NEW . viewed IS DISTINCT FROM OLD . viewed THEN
        INSERT INTO telegram_read_dirty SELECT NEW . feed_id WHERE NEW . feed_id IS NOT NULL ON CONFLICT ( feed ) DO NOTHING;
    END IF;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER telegram_read_item_changed AFTER UPDATE OF complete , members , viewed ON items
FOR EACH ROW EXECUTE FUNCTION oko_trigger_telegram_read_item_changed();

CREATE FUNCTION oko_trigger_telegram_read_member_INSERT() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO telegram_read_dirty VALUES ( NEW . feed ) ON CONFLICT ( feed ) DO NOTHING;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER telegram_read_member_INSERT AFTER INSERT ON telegram_read_members
FOR EACH ROW EXECUTE FUNCTION oko_trigger_telegram_read_member_INSERT();

CREATE FUNCTION oko_trigger_telegram_read_member_UPDATE() RETURNS trigger
LANGUAGE plpgsql AS $oko$
BEGIN
    INSERT INTO telegram_read_dirty VALUES ( NEW . feed ) ON CONFLICT ( feed ) DO NOTHING;
    RETURN NULL;
END
$oko$;

CREATE TRIGGER telegram_read_member_UPDATE AFTER UPDATE ON telegram_read_members
FOR EACH ROW EXECUTE FUNCTION oko_trigger_telegram_read_member_UPDATE();
