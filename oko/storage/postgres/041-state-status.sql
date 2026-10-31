-- The status validator reads relevant runtime state directly.
DROP TRIGGER reader_v_state_status_delete ON state;
DROP TRIGGER reader_v_state_status_insert ON state;
DROP TRIGGER reader_v_state_update ON state;
DROP FUNCTION oko_trigger_reader_v_state_status_delete();
DROP FUNCTION oko_trigger_reader_v_state_status_insert();
DROP FUNCTION oko_trigger_reader_v_state_update();
