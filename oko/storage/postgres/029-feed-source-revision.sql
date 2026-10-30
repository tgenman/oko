-- Feed mutation owners advance the source revision in their transaction.
DROP TRIGGER source_configuration_feeds_delete ON feeds;
DROP TRIGGER source_configuration_feeds_insert ON feeds;
DROP TRIGGER source_configuration_feeds_update ON feeds;
DROP FUNCTION oko_trigger_source_configuration_feeds_delete();
DROP FUNCTION oko_trigger_source_configuration_feeds_insert();
DROP FUNCTION oko_trigger_source_configuration_feeds_update();
