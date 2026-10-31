-- History progress participates directly in the status validator.
DROP TRIGGER reader_v_initial_history_delete ON initial_history;
DROP TRIGGER reader_v_initial_history_insert ON initial_history;
DROP TRIGGER reader_v_initial_history_update ON initial_history;
DROP FUNCTION oko_trigger_reader_v_initial_history_delete();
DROP FUNCTION oko_trigger_reader_v_initial_history_insert();
DROP FUNCTION oko_trigger_reader_v_initial_history_update();
