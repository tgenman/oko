-- Job generation and a fresh input fingerprint guard late results. The old
-- Publication-scoped token is internal coordination, not result provenance.
LOCK TABLE publications, jobs IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE conflicts text;
BEGIN
 SELECT string_agg(id, ', ' ORDER BY id) INTO conflicts FROM publications
 WHERE NOT pg_input_is_valid(processing_metadata,'jsonb')
    OR jsonb_typeof(CASE WHEN pg_input_is_valid(processing_metadata,'jsonb') THEN processing_metadata::jsonb END) IS DISTINCT FROM 'object'
    OR (CASE WHEN pg_input_is_valid(processing_metadata,'jsonb') THEN processing_metadata::jsonb ELSE '{}'::jsonb END ? '_revision'
       AND COALESCE(CASE WHEN pg_input_is_valid(processing_metadata,'jsonb') THEN processing_metadata::jsonb->>'_revision' END,'') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
    OR (CASE WHEN pg_input_is_valid(processing_metadata,'jsonb') THEN processing_metadata::jsonb ELSE '{}'::jsonb END ? '_revision_input'
       AND (jsonb_typeof(CASE WHEN pg_input_is_valid(processing_metadata,'jsonb') THEN processing_metadata::jsonb->'_revision_input' END) IS DISTINCT FROM 'string'
         OR COALESCE(CASE WHEN pg_input_is_valid(processing_metadata,'jsonb') THEN processing_metadata::jsonb->>'_revision_input' END,'') !~ '^[0-9a-f]{64}$'));
 IF conflicts IS NOT NULL THEN
   RAISE EXCEPTION 'Invalid processing revision: %', conflicts;
 END IF;
END $$;
UPDATE publications SET processing_metadata=(processing_metadata::jsonb-'_revision'-'_revision_input')::text
 WHERE processing_metadata::jsonb ?| ARRAY['_revision','_revision_input'];
UPDATE jobs SET payload=payload-'input_revision'
 WHERE kind IN ('filter','theses') AND payload ? 'input_revision';
