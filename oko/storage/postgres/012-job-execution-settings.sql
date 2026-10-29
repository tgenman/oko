-- Keep attempt provenance together with its durable job.
DO $body$
BEGIN
    BEGIN
        PERFORM parameters::jsonb FROM execution_settings;
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'invalid execution settings JSON';
    END;
    IF EXISTS (
        SELECT 1 FROM execution_settings e LEFT JOIN jobs j ON j.id=e.job
        WHERE j.id IS NULL OR e.generation IS NULL OR e.generation < 0
           OR e.signature IS NULL OR e.signature = '' OR e.parameters IS NULL
           OR jsonb_typeof(e.parameters::jsonb) <> 'object'
    ) THEN
        RAISE EXCEPTION 'invalid execution settings';
    END IF;
END
$body$;
ALTER TABLE jobs ADD COLUMN execution_settings jsonb
    CHECK (execution_settings IS NULL OR coalesce((
        jsonb_typeof(execution_settings) = 'object'
        AND jsonb_typeof(execution_settings->'generation') = 'number'
        AND execution_settings->>'generation' ~ '^[0-9]+$'
        AND jsonb_typeof(execution_settings->'signature') = 'string'
        AND execution_settings->>'signature' <> ''
        AND jsonb_typeof(execution_settings->'parameters') = 'object'
    ), false));
UPDATE jobs j SET execution_settings=jsonb_build_object(
    'generation',e.generation,'signature',e.signature,'parameters',e.parameters::jsonb)
FROM execution_settings e WHERE e.job=j.id;
DROP TABLE execution_settings;
