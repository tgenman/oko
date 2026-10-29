-- Keep processing signatures with their jobs. Invalid old associations stop the release.
DO $body$
BEGIN
    IF EXISTS (
        SELECT 1 FROM filter_inputs p LEFT JOIN items i ON i.id=p.item
        LEFT JOIN jobs j ON j.kind='filter' AND j.item=p.item
        WHERE i.id IS NULL OR j.id IS NULL OR p.signature IS NULL OR p.signature=''
    ) OR EXISTS (
        SELECT 1 FROM thesis_inputs p LEFT JOIN items i ON i.id=p.item
        LEFT JOIN jobs j ON j.kind='theses' AND j.item=p.item
        WHERE i.id IS NULL OR j.id IS NULL OR p.signature IS NULL OR p.signature=''
    ) THEN
        RAISE EXCEPTION 'invalid processing input association';
    END IF;
    BEGIN
        PERFORM j.payload::jsonb FROM jobs j JOIN filter_inputs p ON p.item=j.item WHERE j.kind='filter';
        PERFORM j.payload::jsonb FROM jobs j JOIN thesis_inputs p ON p.item=j.item WHERE j.kind='theses';
    EXCEPTION WHEN invalid_text_representation THEN
        RAISE EXCEPTION 'invalid processing input payload';
    END;
    IF EXISTS (
        SELECT 1 FROM jobs j WHERE jsonb_typeof(j.payload::jsonb) <> 'object'
          AND ((j.kind='filter' AND EXISTS (SELECT 1 FROM filter_inputs p WHERE p.item=j.item))
            OR (j.kind='theses' AND EXISTS (SELECT 1 FROM thesis_inputs p WHERE p.item=j.item)))
    ) THEN
        RAISE EXCEPTION 'invalid processing input payload';
    END IF;
END
$body$;
ALTER TABLE jobs ADD COLUMN input_snapshot jsonb CHECK (input_snapshot IS NULL OR coalesce(
    jsonb_typeof(input_snapshot)='object' AND jsonb_typeof(input_snapshot->'signature')='string'
    AND input_snapshot->>'signature' <> '', false));
UPDATE jobs j SET input_snapshot=jsonb_build_object('signature',p.signature)
FROM filter_inputs p WHERE j.kind='filter' AND j.item=p.item;
UPDATE jobs j SET input_snapshot=jsonb_build_object('signature',p.signature)
FROM thesis_inputs p WHERE j.kind='theses' AND j.item=p.item;
CREATE INDEX jobs_processing_input_backfill ON jobs(kind,id)
WHERE input_snapshot IS NOT NULL AND (payload::jsonb->>'input_revision') IS NULL;
DROP TABLE filter_inputs;
DROP TABLE thesis_inputs;
DROP FUNCTION oko_trigger_reader_v_filter_input_delete();
DROP FUNCTION oko_trigger_reader_v_filter_input_insert();
DROP FUNCTION oko_trigger_reader_v_filter_input_update();
