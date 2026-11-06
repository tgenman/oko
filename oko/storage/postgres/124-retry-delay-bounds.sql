-- Preserve the effective ordering of the two settings while bringing old
-- user-configured values within the supported range. Existing source waits
-- and job deadlines are deliberately left untouched.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM settings
    WHERE folder_id IS NULL AND feed_id IS NULL
      AND field IN ('retry_initial_seconds', 'retry_max_seconds')
      AND (jsonb_typeof(value) <> 'number' OR value::text !~ '^[0-9]+$')
  ) THEN
    RAISE EXCEPTION 'invalid stored retry delay; resolve settings before migration';
  END IF;
END $$;

UPDATE settings
SET value = to_jsonb(least(3600::numeric, greatest(60::numeric, (value::text)::numeric))::integer)
WHERE folder_id IS NULL AND feed_id IS NULL
  AND field IN ('retry_initial_seconds', 'retry_max_seconds');
