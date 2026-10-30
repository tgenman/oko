-- Status uses source revision; title changes are recorded by the feed owner.
DROP TRIGGER reader_v_feed_delete ON feeds;
DROP TRIGGER reader_v_feed_insert ON feeds;
DROP TRIGGER reader_v_feed_update ON feeds;
DROP FUNCTION oko_trigger_reader_v_feed_delete();
DROP FUNCTION oko_trigger_reader_v_feed_insert();
DROP FUNCTION oko_trigger_reader_v_feed_update();
