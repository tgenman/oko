-- Fetch attempts are guarded by Job generation and current publication facts.
-- Retain every linked identity before removing the obsolete temporary list.
LOCK TABLE publications,publication_links IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE conflicts text;
BEGIN
 SELECT string_agg(p.id, ', ' ORDER BY p.id) INTO conflicts FROM publications p
 WHERE p.content #> '{extra,_links}' IS NOT NULL AND (
   jsonb_typeof(p.content #> '{extra,_links}')<>'array'
   OR EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content #> '{extra,_links}')='array'
       THEN p.content #> '{extra,_links}' ELSE '[]'::jsonb END) v(value)
      WHERE jsonb_typeof(v.value)<>'object' OR v.value - ARRAY['platform','external_id','url'] <> '{}'::jsonb
         OR NOT EXISTS (SELECT 1 FROM publication_links l JOIN publications target ON target.id=l.target_publication_id
              WHERE l.source_publication_id=p.id AND target.platform=v.value->>'platform'
                AND target.external_id=v.value->>'external_id' AND target.url=v.value->>'url'))
   OR (SELECT count(*)<>count(DISTINCT value) FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content #> '{extra,_links}')='array'
       THEN p.content #> '{extra,_links}' ELSE '[]'::jsonb END) v(value)));
 IF conflicts IS NOT NULL THEN RAISE EXCEPTION 'Unresolved publication links: %',conflicts; END IF;
END $$;
UPDATE publications SET content=content #- '{extra,_input_hash}' #- '{extra,_collection_revision}' #- '{extra,_links}'
WHERE content #> '{extra,_input_hash}' IS NOT NULL OR content #> '{extra,_collection_revision}' IS NOT NULL
 OR content #> '{extra,_links}' IS NOT NULL;
