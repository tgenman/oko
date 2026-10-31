DO $$
DECLARE
    target_schema pg_catalog.text := pg_catalog.current_schema();
    original_path pg_catalog.text := pg_catalog.current_setting('search_path');
BEGIN
    IF target_schema IS NULL OR pg_catalog.to_regclass(pg_catalog.format('%I.%I',target_schema,'jobs')) IS NULL THEN
        RAISE EXCEPTION 'Cannot migrate manual_once: jobs is missing from the current schema';
    END IF;
    EXECUTE pg_catalog.format('LOCK TABLE %I.%I IN ACCESS EXCLUSIVE MODE',target_schema,'jobs');
    PERFORM pg_catalog.set_config('search_path',pg_catalog.format('pg_catalog,%I,pg_temp',target_schema),true);
    IF EXISTS (SELECT 1 FROM jobs WHERE manual_once IS NOT NULL AND manual_once NOT IN (0,1)) THEN
        RAISE EXCEPTION 'Cannot migrate manual_once: non-boolean legacy integer';
    END IF;
    ALTER TABLE jobs ALTER COLUMN manual_once DROP DEFAULT;
    ALTER TABLE jobs ALTER COLUMN manual_once TYPE boolean USING (manual_once<>0);
    ALTER TABLE jobs ALTER COLUMN manual_once SET DEFAULT false;
    PERFORM pg_catalog.set_config('search_path',original_path,true);
END $$;
