-- Prevent immutable migrations 96/97 from preferring legacy reader copies.
-- This prelude is checksummed as part of migration 103 and runs before those
-- migrations only when they are still pending in the same release transaction.
LOCK TABLE publications IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE invalid_ids text;
BEGIN
    SELECT string_agg(id, ', ' ORDER BY id) INTO invalid_ids FROM publications
    WHERE jsonb_typeof((CASE WHEN pg_input_is_valid(content,'jsonb') THEN content::jsonb END) #> '{extra,_reader,reply_preview}')='object'
      AND ((CASE WHEN pg_input_is_valid(content,'jsonb') THEN content::jsonb END) #> '{extra,_reader,reply_preview}')
          - ARRAY['text','author','source_url'] <> '{}'::jsonb;
    IF invalid_ids IS NOT NULL THEN
        RAISE EXCEPTION 'Unknown publication quote fields: %', invalid_ids;
    END IF;
END $$;
UPDATE publications SET content=(content::jsonb #- '{extra,_reader,title}')::text
WHERE jsonb_typeof((CASE WHEN pg_input_is_valid(content,'jsonb') THEN content::jsonb END) #> '{extra,_reader,title}')='string';
UPDATE publications p SET content=(p.content::jsonb #- '{extra,_reader,reply_preview}')::text
WHERE jsonb_typeof((CASE WHEN pg_input_is_valid(p.content,'jsonb') THEN p.content::jsonb END) #> '{extra,_reader,reply_preview}')='object'
  AND jsonb_typeof((CASE WHEN pg_input_is_valid(p.content,'jsonb') THEN p.content::jsonb END) #> '{extra,_reader,reply_preview,text}')='string'
  AND COALESCE(jsonb_typeof((CASE WHEN pg_input_is_valid(p.content,'jsonb') THEN p.content::jsonb END) #> '{extra,_reader,reply_preview,author}'),'null') IN ('string','null')
  AND COALESCE(jsonb_typeof((CASE WHEN pg_input_is_valid(p.content,'jsonb') THEN p.content::jsonb END) #> '{extra,_reader,reply_preview,source_url}'),'null') IN ('string','null')
  AND EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof((CASE WHEN pg_input_is_valid(p.content,'jsonb') THEN p.content::jsonb END)->'blocks')='array'
    THEN p.content::jsonb->'blocks' ELSE '[]'::jsonb END) block WHERE block->>'type'='quote');
