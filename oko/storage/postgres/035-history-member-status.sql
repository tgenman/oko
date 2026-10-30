-- History owners update status freshness with changed member facts.
DROP TRIGGER reader_v_history_members_delete ON history_members;
DROP TRIGGER reader_v_history_members_insert ON history_members;
DROP TRIGGER reader_v_history_members_update ON history_members;
DROP FUNCTION oko_trigger_reader_v_history_members_delete();
DROP FUNCTION oko_trigger_reader_v_history_members_insert();
DROP FUNCTION oko_trigger_reader_v_history_members_update();
