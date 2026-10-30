-- Navigation reads folders directly; folder names do not alter runtime status.
DROP TRIGGER reader_v_folder_delete ON folders;
DROP TRIGGER reader_v_folder_insert ON folders;
DROP TRIGGER reader_v_folder_update ON folders;
DROP FUNCTION oko_trigger_reader_v_folder_delete();
DROP FUNCTION oko_trigger_reader_v_folder_insert();
DROP FUNCTION oko_trigger_reader_v_folder_update();
