-- Status freshness for bindings and identity uses the owned source revision.
DROP TRIGGER reader_v_telegram_bindings_delete ON telegram_bindings;
DROP TRIGGER reader_v_telegram_bindings_insert ON telegram_bindings;
DROP TRIGGER reader_v_telegram_bindings_update ON telegram_bindings;
DROP TRIGGER reader_v_profile ON telegram_profiles;
DROP TRIGGER reader_v_profile_delete ON telegram_profiles;
DROP TRIGGER reader_v_profile_insert ON telegram_profiles;
DROP FUNCTION oko_trigger_reader_v_telegram_bindings_delete();
DROP FUNCTION oko_trigger_reader_v_telegram_bindings_insert();
DROP FUNCTION oko_trigger_reader_v_telegram_bindings_update();
DROP FUNCTION oko_trigger_reader_v_profile();
DROP FUNCTION oko_trigger_reader_v_profile_delete();
DROP FUNCTION oko_trigger_reader_v_profile_insert();
