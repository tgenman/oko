DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM feeds
        WHERE NOT (config IS JSON OBJECT WITH UNIQUE KEYS)
           OR NOT pg_input_is_valid(config, 'jsonb')
    ) THEN
        RAISE EXCEPTION 'Cannot convert feed config: invalid, non-object, or ambiguous JSON';
    END IF;
    -- jsonb expands scientific notation. A previously readable Python float
    -- can become an integer, or a negative zero can lose its sign. Both change
    -- Python values or types; detect them before changing any data.
    IF EXISTS (
        WITH RECURSIVE nodes(value) AS (
            SELECT config::json FROM feeds
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
        RAISE EXCEPTION 'Cannot convert feed config: a number would change or become unreadable after JSONB normalization';
    END IF;
END $$;
ALTER TABLE feeds ALTER COLUMN config DROP DEFAULT;
ALTER TABLE feeds ALTER COLUMN config TYPE jsonb USING config::jsonb;
ALTER TABLE feeds ALTER COLUMN config SET DEFAULT '{}'::jsonb;
ALTER TABLE feeds ADD CONSTRAINT feeds_config_object CHECK (jsonb_typeof(config)='object');
