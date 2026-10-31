-- Resolve the owned relation explicitly; never fall through to another schema.
-- Keep the lock and the restricted path for the whole migration transaction.
DO $$
DECLARE
    target_schema pg_catalog.text := pg_catalog.current_schema();
    original_path pg_catalog.text := pg_catalog.current_setting('search_path');
BEGIN
    IF target_schema IS NULL OR pg_catalog.to_regclass(pg_catalog.format('%I.%I',target_schema,'jobs')) IS NULL THEN
        RAISE EXCEPTION 'Cannot migrate job payload: jobs is missing from the current schema';
    END IF;
    EXECUTE pg_catalog.format('LOCK TABLE %I.%I IN ACCESS EXCLUSIVE MODE',target_schema,'jobs');
    PERFORM pg_catalog.set_config('search_path',pg_catalog.format('pg_catalog,%I,pg_temp',target_schema),true);
    IF EXISTS (
        SELECT 1 FROM jobs
        WHERE payload IS NULL OR NOT (payload IS JSON OBJECT WITH UNIQUE KEYS)
           OR NOT pg_input_is_valid(payload, 'jsonb')
    ) THEN
        RAISE EXCEPTION 'Cannot convert job payload: invalid, non-object, or ambiguous JSON';
    END IF;
    -- jsonb expands scientific notation. A previously readable Python float
    -- can become an integer, or a negative zero can lose its sign. Both change
    -- Python values or types; detect them before changing any data.
    IF EXISTS (
        WITH RECURSIVE nodes(value) AS (
            SELECT payload::json FROM jobs
            UNION ALL
            SELECT child.value FROM nodes n CROSS JOIN LATERAL (
                SELECT value FROM json_each(CASE WHEN json_typeof(n.value)='object' THEN n.value ELSE '{}'::json END)
                UNION ALL
                SELECT value FROM json_array_elements(CASE WHEN json_typeof(n.value)='array' THEN n.value ELSE '[]'::json END)
            ) child
        )
        SELECT 1 FROM nodes n
        CROSS JOIN LATERAL (SELECT n.value::text AS original, n.value::jsonb::text AS normalized) number
        WHERE json_typeof(n.value)='number'
          AND ((number.normalized ~ '^-?[0-9]+$'
                AND (length(ltrim(number.normalized,'-'))>4300 OR number.original ~ '[.eE]'))
               OR (number.original ~ '^-' AND number.original ~ '[.eE]'
                   AND number.original::numeric=0 AND number.normalized !~ '^-'))
    ) THEN
        RAISE EXCEPTION 'Cannot convert job payload: a number would change or become unreadable after JSONB normalization';
    END IF;
    ALTER TABLE jobs ALTER COLUMN payload DROP DEFAULT;
    ALTER TABLE jobs ALTER COLUMN payload TYPE jsonb USING payload::jsonb;
    ALTER TABLE jobs ALTER COLUMN payload SET DEFAULT '{}'::jsonb;
    ALTER TABLE jobs ADD CONSTRAINT jobs_payload_object CHECK (jsonb_typeof(payload)='object');
    PERFORM pg_catalog.set_config('search_path',original_path,true);
END $$;
