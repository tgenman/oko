LOCK TABLE publications IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE invalid_ids text;
BEGIN
    SELECT string_agg(id, ', ' ORDER BY id) INTO invalid_ids
      FROM publications WHERE NOT pg_input_is_valid(content, 'jsonb');
    IF invalid_ids IS NOT NULL THEN
        RAISE EXCEPTION 'Invalid publication content for JSONB: %', invalid_ids;
    END IF;
END $$;
ALTER TABLE publications ALTER COLUMN content DROP DEFAULT;
ALTER TABLE publications ALTER COLUMN content TYPE jsonb USING content::jsonb;
ALTER TABLE publications ALTER COLUMN content SET DEFAULT '{}'::jsonb;
