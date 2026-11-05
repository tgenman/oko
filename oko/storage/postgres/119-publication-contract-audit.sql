-- The previous migrations perform the agreed conversions. Do not silently
-- discard any unexpected legacy data that survived or appeared afterwards.
LOCK TABLE publications, publication_links, media_files IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE conflicts text;
BEGIN
 SELECT string_agg(p.id, ', ' ORDER BY p.id) INTO conflicts
 FROM publications p WHERE
   jsonb_typeof(p.content) IS DISTINCT FROM 'object'
   OR EXISTS (SELECT 1 FROM jsonb_object_keys(CASE WHEN jsonb_typeof(p.content)='object' THEN p.content ELSE '{}'::jsonb END) k WHERE k NOT IN ('blocks','extra'))
   OR (p.content ? 'extra' AND jsonb_typeof(p.content->'extra') IS DISTINCT FROM 'object')
   OR (p.content ? 'blocks' AND jsonb_typeof(p.content->'blocks') IS DISTINCT FROM 'array')
   OR (p.content #> '{extra,telegram}' IS NOT NULL AND jsonb_typeof(p.content #> '{extra,telegram}') IS DISTINCT FROM 'object')
   OR (p.content #> '{extra,youtube}' IS NOT NULL AND jsonb_typeof(p.content #> '{extra,youtube}') IS DISTINCT FROM 'object')
   OR COALESCE(p.content->'extra','{}'::jsonb) ?| ARRAY['_reader','_collection_reason','_collection_revision','_links','_media_urls','_input_hash','source_manifest','completeness','title','description','reply_preview','captions','members','changed','full','reason','edited']
   OR COALESCE(p.content #> '{extra,telegram}','{}'::jsonb) ?| ARRAY['manifest','description','title','reply_preview','edited']
   OR COALESCE(p.content #> '{extra,youtube}','{}'::jsonb) ?| ARRAY['captions','captions_digest','description','title','reply_preview','edited'];
 IF conflicts IS NOT NULL THEN
   RAISE EXCEPTION 'Obsolete or malformed publication content: %', conflicts;
 END IF;
 WITH documents AS (
   SELECT p.id,CASE WHEN pg_input_is_valid(p.media_ids,'jsonb') THEN p.media_ids::jsonb END AS ids
   FROM publications p
 ), invalid AS (
   SELECT d.id FROM documents d WHERE jsonb_typeof(d.ids) IS DISTINCT FROM 'array'
     OR EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(d.ids)='array' THEN d.ids ELSE '[]'::jsonb END) x WHERE jsonb_typeof(x) IS DISTINCT FROM 'string')
     OR (SELECT count(*)<>count(DISTINCT x) FROM jsonb_array_elements_text(CASE WHEN jsonb_typeof(d.ids)='array' THEN d.ids ELSE '[]'::jsonb END) x)
     OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(CASE WHEN jsonb_typeof(d.ids)='array' THEN d.ids ELSE '[]'::jsonb END) x WHERE NOT EXISTS (SELECT 1 FROM media_files m WHERE m.id=x AND m.publication_id=d.id))
     OR EXISTS (SELECT 1 FROM media_files m WHERE m.publication_id=d.id AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(CASE WHEN jsonb_typeof(d.ids)='array' THEN d.ids ELSE '[]'::jsonb END) x WHERE x=m.id))
 ) SELECT string_agg(id, ', ' ORDER BY id) INTO conflicts FROM invalid;
 IF conflicts IS NOT NULL THEN
   RAISE EXCEPTION 'Conflicting publication media IDs: %', conflicts;
 END IF;
 SELECT string_agg(DISTINCT p.id, ', ' ORDER BY p.id) INTO conflicts
 FROM publications p
 CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) b(block)
 LEFT JOIN media_files m ON m.id=b.block->>'media_id' AND m.publication_id=p.id
 WHERE b.block->>'type'='media' AND (m.id IS NULL OR NOT (b.block ? 'source_url') OR NOT (b.block ? 'publication_url') OR
   (b.block->>'source_url') IS DISTINCT FROM m.source_url);
 IF conflicts IS NOT NULL THEN
   RAISE EXCEPTION 'Conflicting publication media links: %', conflicts;
 END IF;
END $$;

-- Filter and summary are results, not another copy of adapter data.  Only an
-- empty extra or a byte-for-byte duplicate of the original can be removed.
DO $$
DECLARE conflicts text;
BEGIN
 WITH data AS (
   SELECT p.id,p.content,
     CASE WHEN pg_input_is_valid(p.filtered_content,'jsonb') THEN p.filtered_content::jsonb END AS filtered,
     CASE WHEN pg_input_is_valid(p.summarized_content,'jsonb') THEN p.summarized_content::jsonb END AS summary
   FROM publications p
 )
 SELECT string_agg(d.id, ', ' ORDER BY d.id) INTO conflicts FROM data d WHERE
   jsonb_typeof(d.filtered) IS DISTINCT FROM 'object' OR jsonb_typeof(d.summary) IS DISTINCT FROM 'object'
   OR EXISTS (SELECT 1 FROM jsonb_object_keys(CASE WHEN jsonb_typeof(d.filtered)='object' THEN d.filtered ELSE '{}'::jsonb END) k WHERE k NOT IN ('blocks','hidden','reason','removed','extra'))
   OR EXISTS (SELECT 1 FROM jsonb_object_keys(CASE WHEN jsonb_typeof(d.summary)='object' THEN d.summary ELSE '{}'::jsonb END) k WHERE k NOT IN ('points','extra'))
   OR (d.filtered ? 'extra' AND d.filtered->'extra' NOT IN ('{}'::jsonb,'null'::jsonb)
       AND d.filtered->'extra' IS DISTINCT FROM d.content->'extra')
   OR (d.summary ? 'extra' AND d.summary->'extra' NOT IN ('{}'::jsonb,'null'::jsonb));
 IF conflicts IS NOT NULL THEN
   RAISE EXCEPTION 'Conflicting publication processing data: %', conflicts;
 END IF;
END $$;
UPDATE publications SET filtered_content=(filtered_content::jsonb-'extra')::text
 WHERE filtered_content::jsonb ? 'extra';
UPDATE publications SET summarized_content=(summarized_content::jsonb-'extra')::text
 WHERE summarized_content::jsonb ? 'extra';
