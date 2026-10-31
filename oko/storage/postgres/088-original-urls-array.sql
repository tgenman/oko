-- Convert only JSON arrays of strings; preserve element order and duplicates.
-- The migration runner holds the schema-exclusive lease and commits the ledger atomically.
LOCK TABLE feeds IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE invalid_count bigint;
BEGIN
    SELECT count(*) INTO invalid_count FROM feeds
        WHERE NOT CASE
            WHEN original_urls IS NULL OR NOT pg_input_is_valid(original_urls, 'jsonb') THEN false
            WHEN jsonb_typeof(original_urls::jsonb) <> 'array' THEN false
            ELSE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(original_urls::jsonb) AS element(value) WHERE jsonb_typeof(element.value) <> 'string')
        END;
    IF invalid_count > 0 THEN
        RAISE EXCEPTION 'Cannot migrate feeds.original_urls: % legacy values are not JSON arrays of strings representable as PostgreSQL text', invalid_count;
    END IF;
END $$;
CREATE TEMP TABLE original_urls_migration ON COMMIT DROP AS
    SELECT f.id, ARRAY(SELECT element FROM jsonb_array_elements_text(f.original_urls::jsonb)
        WITH ORDINALITY AS a(element,position) ORDER BY position) AS urls FROM feeds f;
ALTER TABLE feeds ALTER COLUMN original_urls TYPE text[] USING ARRAY[]::text[];
UPDATE feeds f SET original_urls=m.urls FROM original_urls_migration m WHERE m.id=f.id;
ALTER TABLE feeds ALTER COLUMN original_urls SET DEFAULT ARRAY[]::text[], ALTER COLUMN original_urls SET NOT NULL;
ALTER TABLE feeds ADD CONSTRAINT feeds_original_urls_no_null CHECK (array_position(original_urls,NULL::text) IS NULL);
