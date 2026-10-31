-- Resolve the owned relation explicitly; never fall through to another schema.
-- Keep the lock and the restricted path for the whole migration transaction.
DO $$
DECLARE
    target_schema pg_catalog.text := pg_catalog.current_schema();
    original_path pg_catalog.text := pg_catalog.current_setting('search_path');
BEGIN
    IF target_schema IS NULL OR pg_catalog.to_regclass(pg_catalog.format('%I.%I',target_schema,'telegram_bindings')) IS NULL THEN
        RAISE EXCEPTION 'Cannot migrate Telegram binding: telegram_bindings is missing from the current schema';
    END IF;
    EXECUTE pg_catalog.format('LOCK TABLE %I.%I IN ACCESS EXCLUSIVE MODE',target_schema,'telegram_bindings');
    PERFORM pg_catalog.set_config('search_path',pg_catalog.format('pg_catalog,%I,pg_temp',target_schema),true);
    -- Do not silently alter column-level ACLs or their grant provenance.
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
        CROSS JOIN LATERAL pg_catalog.aclexplode(a.attacl) acl
        WHERE a.attrelid=pg_catalog.to_regclass(pg_catalog.format('%I.%I',target_schema,'telegram_bindings'))
          AND a.attname='binding' AND NOT a.attisdropped) THEN
        RAISE EXCEPTION 'Cannot migrate Telegram binding: explicit column privileges need review';
    END IF;
    -- Retain every unreadable or semantically lossy legacy byte in place.
    ALTER TABLE telegram_bindings ADD COLUMN binding_raw text;
    UPDATE telegram_bindings SET binding_raw=binding
        WHERE NOT (binding IS JSON WITH UNIQUE KEYS)
           OR NOT pg_input_is_valid(binding, 'jsonb');
    WITH RECURSIVE nodes(feed,value,depth) AS (
        SELECT feed,binding::json,0 FROM telegram_bindings WHERE binding_raw IS NULL
        UNION ALL
        SELECT n.feed,child.value,n.depth+1 FROM nodes n CROSS JOIN LATERAL (
            SELECT value FROM json_each(CASE WHEN json_typeof(n.value)='object' THEN n.value ELSE '{}'::json END)
            UNION ALL
            SELECT value FROM json_array_elements(CASE WHEN json_typeof(n.value)='array' THEN n.value ELSE '[]'::json END)
        ) child
    ), unsafe AS (
        SELECT DISTINCT n.feed FROM nodes n
        CROSS JOIN LATERAL (SELECT n.value::text AS original, n.value::jsonb::text AS normalized) number
        WHERE n.depth>=900 OR (json_typeof(n.value)='number'
          AND ((number.normalized ~ '^-?[0-9]+$'
                AND (length(ltrim(number.normalized,'-'))>4300 OR number.original ~ '[.eE]'))
               OR (number.original ~ '^-' AND number.original ~ '[.eE]'
                   AND number.original::numeric=0 AND number.normalized !~ '^-')))
    )
    UPDATE telegram_bindings b SET binding_raw=b.binding FROM unsafe WHERE b.feed=unsafe.feed;
    ALTER TABLE telegram_bindings ALTER COLUMN binding DROP NOT NULL;
    ALTER TABLE telegram_bindings ALTER COLUMN binding TYPE jsonb
        USING CASE WHEN binding_raw IS NOT NULL THEN NULL ELSE binding::jsonb END;
    ALTER TABLE telegram_bindings ADD CONSTRAINT telegram_binding_one_representation
        CHECK ((binding IS NOT NULL) <> (binding_raw IS NOT NULL));
    PERFORM pg_catalog.set_config('search_path',original_path,true);
END $$;
