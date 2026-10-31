-- TelegramAccess records diagnostic-message freshness in its transaction.
DROP TRIGGER reader_v_profile_message ON telegram_profiles;
DROP FUNCTION oko_trigger_reader_v_profile_message();
