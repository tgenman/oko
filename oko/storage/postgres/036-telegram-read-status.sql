-- Telegram read-state owners record status freshness in their transaction.
DO $$
DECLARE name text; relation text;
BEGIN
 FOREACH relation IN ARRAY ARRAY['telegram_read_members','telegram_read_state'] LOOP
  FOREACH name IN ARRAY ARRAY['delete','insert','update'] LOOP
   EXECUTE format('DROP TRIGGER %I ON %I','reader_v_' || relation || '_status_' || name,relation);
   EXECUTE format('DROP FUNCTION %I()','oko_trigger_reader_v_' || relation || '_status_' || name);
  END LOOP;
 END LOOP;
END $$;
