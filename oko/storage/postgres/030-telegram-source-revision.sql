-- TelegramAccess advances the source revision in the mutation transaction.
DO $$
DECLARE name text; relation text;
BEGIN
 FOREACH relation IN ARRAY ARRAY['telegram_bindings','telegram_profiles'] LOOP
  FOREACH name IN ARRAY ARRAY['delete','insert','update'] LOOP
   EXECUTE format('DROP TRIGGER %I ON %I','source_configuration_' || relation || '_' || name,relation);
   EXECUTE format('DROP FUNCTION %I()','oko_trigger_source_configuration_' || relation || '_' || name);
  END LOOP;
 END LOOP;
END $$;
