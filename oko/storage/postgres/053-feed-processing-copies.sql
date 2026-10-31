-- Only empty legacy copies can be removed without choosing between conflicting facts.
DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM feeds WHERE filter_config::jsonb <> '{}'::jsonb OR summary_config::jsonb <> '{}'::jsonb) THEN
  RAISE EXCEPTION 'nonempty feed processing config needs reconciliation';
 END IF;
END $$;
ALTER TABLE feeds DROP COLUMN filter_config, DROP COLUMN summary_config;
