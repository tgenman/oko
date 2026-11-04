-- Keep every legacy media reference and stop for manual repair when the saved
-- list, common content blocks and media rows disagree.
LOCK TABLE publications, media_files IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE conflicts text;
BEGIN
    WITH documents AS MATERIALIZED (
        SELECT p.id,p.content,CASE WHEN pg_input_is_valid(p.media_ids,'jsonb') THEN p.media_ids::jsonb END AS ids
        FROM publications p
    ), checked AS (
        SELECT d.id FROM documents d
        WHERE jsonb_typeof(d.content) IS DISTINCT FROM 'object'
          OR (d.content ? 'blocks' AND jsonb_typeof(d.content->'blocks') IS DISTINCT FROM 'array')
          OR jsonb_typeof(d.ids) IS DISTINCT FROM 'array'
          OR EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(d.ids)='array' THEN d.ids ELSE '[]'::jsonb END) x
                     WHERE jsonb_typeof(x) IS DISTINCT FROM 'string')
          OR (SELECT count(*) <> count(DISTINCT x) FROM jsonb_array_elements_text(
                CASE WHEN jsonb_typeof(d.ids)='array' THEN d.ids ELSE '[]'::jsonb END) x)
          OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(CASE WHEN jsonb_typeof(d.ids)='array' THEN d.ids ELSE '[]'::jsonb END) x
                     WHERE NOT EXISTS (SELECT 1 FROM media_files m WHERE m.id=x AND m.publication_id=d.id))
          OR EXISTS (SELECT 1 FROM media_files m WHERE m.publication_id=d.id
                     AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(CASE WHEN jsonb_typeof(d.ids)='array' THEN d.ids ELSE '[]'::jsonb END) x WHERE x=m.id))
          OR EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(d.content->'blocks')='array' THEN d.content->'blocks' ELSE '[]'::jsonb END) b
                     WHERE b->>'type'='media' AND (jsonb_typeof(b->'media_id') IS DISTINCT FROM 'string'
                       OR NOT EXISTS (SELECT 1 FROM media_files m WHERE m.id=b->>'media_id' AND m.publication_id=d.id)
                       OR NOT EXISTS (SELECT 1 FROM jsonb_array_elements_text(CASE WHEN jsonb_typeof(d.ids)='array' THEN d.ids ELSE '[]'::jsonb END) x WHERE x=b->>'media_id')))
    )
    SELECT string_agg(id, ', ' ORDER BY id) INTO conflicts FROM checked;
    IF conflicts IS NOT NULL THEN
        RAISE EXCEPTION 'Publication media reference conflicts: %', conflicts;
    END IF;
END $$;
