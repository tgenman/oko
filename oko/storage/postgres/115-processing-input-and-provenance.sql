-- The old signature includes settings and is not an input hash. Invalidate
-- attempt snapshots rather than relabelling their value; retained results keep
-- their historical input hash and provenance without inventing a prompt.
LOCK TABLE publications, jobs IN ACCESS EXCLUSIVE MODE;
ALTER TABLE jobs DROP CONSTRAINT jobs_input_snapshot_check;
ALTER TABLE jobs DROP CONSTRAINT jobs_execution_settings_check;
UPDATE jobs SET input_snapshot=NULL,execution_settings=NULL,
    status=CASE WHEN status='running' THEN 'pending' ELSE status END,
    generation=CASE WHEN status='running' THEN generation+1 ELSE generation END
WHERE kind IN ('filter','theses') AND (input_snapshot IS NOT NULL OR execution_settings IS NOT NULL OR status='running');
ALTER TABLE jobs ADD CONSTRAINT jobs_input_snapshot_check CHECK (input_snapshot IS NULL OR coalesce(
    jsonb_typeof(input_snapshot)='object' AND jsonb_typeof(input_snapshot->'input_hash')='string'
    AND input_snapshot->>'input_hash'<>'',false));
ALTER TABLE jobs ADD CONSTRAINT jobs_execution_settings_check CHECK (execution_settings IS NULL OR coalesce(
    jsonb_typeof(execution_settings)='object' AND jsonb_typeof(execution_settings->'generation')='number'
    AND execution_settings->>'generation' ~ '^[0-9]+$'
    AND jsonb_typeof(execution_settings->'parameters')='object',false));
DO $$
DECLARE invalid_ids text;
BEGIN
 SELECT string_agg(id, ', ' ORDER BY id) INTO invalid_ids FROM publications
 WHERE (processing_metadata::jsonb ? 'filter' AND jsonb_typeof(processing_metadata::jsonb->'filter')<>'object')
    OR (processing_metadata::jsonb ? 'summary' AND jsonb_typeof(processing_metadata::jsonb->'summary')<>'object');
 IF invalid_ids IS NOT NULL THEN RAISE EXCEPTION 'Invalid processing provenance: %', invalid_ids; END IF;
 SELECT string_agg(id, ', ' ORDER BY id) INTO invalid_ids FROM publications p
 WHERE EXISTS (SELECT 1 FROM unnest(ARRAY['filter','summary']) AS k(stage)
   WHERE p.processing_metadata::jsonb #>> ARRAY[stage,'processor']='codex'
     AND ((p.processing_metadata::jsonb #> ARRAY[stage,'parameters'] IS NOT NULL
           AND jsonb_typeof(p.processing_metadata::jsonb #> ARRAY[stage,'parameters']) NOT IN ('object','null'))
       OR EXISTS (SELECT 1 FROM unnest(ARRAY['reasoning_effort','fast']) AS f(name)
          WHERE p.processing_metadata::jsonb #> ARRAY[stage,name] IS NOT NULL
            AND p.processing_metadata::jsonb #> ARRAY[stage,'parameters',name] IS NOT NULL
            AND p.processing_metadata::jsonb #> ARRAY[stage,name] IS DISTINCT FROM p.processing_metadata::jsonb #> ARRAY[stage,'parameters',name])));
 IF invalid_ids IS NOT NULL THEN RAISE EXCEPTION 'Conflicting Codex parameters: %', invalid_ids; END IF;
END $$;
UPDATE publications SET processing_metadata=(
    (processing_metadata::jsonb - '_pass' - 'filter' - 'summary') ||
    CASE WHEN processing_metadata::jsonb ? 'filter' THEN jsonb_build_object('filter',
      '{"prompt":null,"processor":null,"model":null,"parameters":{},"input_hash":null,"input_hash_format":"legacy-blocks-v0"}'::jsonb ||
      ((processing_metadata::jsonb->'filter')-'signature'-'settings_hash')) ELSE '{}'::jsonb END ||
    CASE WHEN processing_metadata::jsonb ? 'summary' THEN jsonb_build_object('summary',
      '{"prompt":null,"processor":null,"model":null,"parameters":{},"input_hash":null,"input_hash_format":"legacy-blocks-v0"}'::jsonb ||
      ((processing_metadata::jsonb->'summary')-'signature'-'settings_hash')) ELSE '{}'::jsonb END
)::text WHERE processing_metadata::jsonb ?| ARRAY['_pass','filter','summary'];

-- Preserve known Codex choices once, inside the parameters object.
DO $$
DECLARE stage text;
BEGIN
 FOREACH stage IN ARRAY ARRAY['filter','summary'] LOOP
  UPDATE publications SET processing_metadata=jsonb_set(processing_metadata::jsonb,ARRAY[stage],
    ((processing_metadata::jsonb->stage)-'reasoning_effort'-'fast') || jsonb_build_object('parameters',
      (CASE WHEN jsonb_typeof(processing_metadata::jsonb #> ARRAY[stage,'parameters'])='object'
       THEN processing_metadata::jsonb #> ARRAY[stage,'parameters'] ELSE '{}'::jsonb END) ||
      (CASE WHEN (processing_metadata::jsonb->stage) ? 'reasoning_effort'
       THEN jsonb_build_object('reasoning_effort',processing_metadata::jsonb #> ARRAY[stage,'reasoning_effort']) ELSE '{}'::jsonb END) ||
      (CASE WHEN (processing_metadata::jsonb->stage) ? 'fast'
       THEN jsonb_build_object('fast',processing_metadata::jsonb #> ARRAY[stage,'fast']) ELSE '{}'::jsonb END)),true)::text
  WHERE processing_metadata::jsonb #>> ARRAY[stage,'processor']='codex'
    AND (processing_metadata::jsonb->stage) ?| ARRAY['reasoning_effort','fast'];
 END LOOP;
END $$;
