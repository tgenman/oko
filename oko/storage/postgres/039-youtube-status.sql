-- The status validator includes the saved YouTube admission facts.
DROP TRIGGER reader_v_youtube_access_status_delete ON youtube_access;
DROP TRIGGER reader_v_youtube_access_status_insert ON youtube_access;
DROP TRIGGER reader_v_youtube_access_status_update ON youtube_access;
DROP FUNCTION oko_trigger_reader_v_youtube_access_status_delete();
DROP FUNCTION oko_trigger_reader_v_youtube_access_status_insert();
DROP FUNCTION oko_trigger_reader_v_youtube_access_status_update();
