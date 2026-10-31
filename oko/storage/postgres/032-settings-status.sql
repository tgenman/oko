-- The status validator includes the transaction-owned source revision.
DROP TRIGGER reader_v_settings_status_delete ON settings;
DROP TRIGGER reader_v_settings_status_insert ON settings;
DROP TRIGGER reader_v_settings_status_update ON settings;
DROP FUNCTION oko_trigger_reader_v_settings_status_delete();
DROP FUNCTION oko_trigger_reader_v_settings_status_insert();
DROP FUNCTION oko_trigger_reader_v_settings_status_update();
