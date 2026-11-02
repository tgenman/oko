-- A quote already present in blocks is the processing input. Remove only an
-- identical reader copy; preserve unmatched legacy data for later reconciliation.
UPDATE publications
SET content = ((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #- '{extra,_reader,reply_preview}')::text
WHERE jsonb_typeof((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END)->'blocks') = 'array'
  AND jsonb_typeof((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #> '{extra,_reader,reply_preview}') = 'object'
  AND (
    SELECT jsonb_build_object('text', block->'text', 'author', block->'author',
                             'source_url', block->'source_url') =
          jsonb_build_object('text', (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #> '{extra,_reader,reply_preview,text}',
                             'author', (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #> '{extra,_reader,reply_preview,author}',
                             'source_url', (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #> '{extra,_reader,reply_preview,source_url}')
    FROM jsonb_array_elements(CASE WHEN jsonb_typeof((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END)->'blocks') = 'array' THEN (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END)->'blocks' ELSE '[]'::jsonb END) WITH ORDINALITY AS q(block, position)
    WHERE block->>'type' = 'quote'
    ORDER BY position LIMIT 1
  );

-- Preserve the reader-visible YouTube description while moving it into the
-- common content format. Existing description blocks remain authoritative.
UPDATE publications
SET content = (jsonb_set((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END), '{blocks}',
    ((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END)->'blocks') || jsonb_build_array(jsonb_build_object(
        'type', 'text', 'role', 'description',
        'text', (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #>> '{extra,_reader,description}'))) #- '{extra,_reader,description}')::text
WHERE platform = 'youtube'
  AND jsonb_typeof((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END)->'blocks') = 'array'
  AND jsonb_typeof((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #> '{extra,_reader,description}') = 'string'
  AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END)->'blocks') = 'array' THEN (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END)->'blocks' ELSE '[]'::jsonb END) AS block
                  WHERE block->>'type' = 'text' AND block->>'role' = 'description');

-- Drop the old YouTube-specific copy only when it equals the first common
-- description. Divergent legacy values remain available for inspection.
UPDATE publications
SET content = ((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #- '{extra,youtube,description}')::text
WHERE platform = 'youtube'
  AND jsonb_typeof((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END)->'blocks') = 'array'
  AND jsonb_typeof((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #> '{extra,youtube,description}') = 'string'
  AND (SELECT block->'text' = (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #> '{extra,youtube,description}'
       FROM jsonb_array_elements(CASE WHEN jsonb_typeof((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END)->'blocks') = 'array' THEN (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END)->'blocks' ELSE '[]'::jsonb END) WITH ORDINALITY AS q(block, position)
       WHERE block->>'type' = 'text' AND block->>'role' = 'description'
       ORDER BY position LIMIT 1);

-- Reader caption state is a projection of the source caption state. Keep
-- unexpected old values until they can be inspected rather than discarding them.
UPDATE publications
SET content = ((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #- '{extra,_reader,captions}')::text
WHERE platform = 'youtube'
  AND jsonb_typeof((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #> '{extra,youtube}') = 'object'
  AND (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #>> '{extra,_reader,captions}' = CASE
      WHEN (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #>> '{extra,youtube,captions}' IN ('available','unavailable','not_requested')
      THEN (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #>> '{extra,youtube,captions}'
      ELSE 'error'
  END;

-- The reader manifest can be updated as media arrives. Remove the adapter
-- snapshot only where both copies still match exactly.
UPDATE publications
SET content = ((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #- '{extra,telegram,manifest}')::text
WHERE jsonb_typeof((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #> '{extra,telegram,manifest}') = 'object'
  AND (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #> '{extra,telegram,manifest}' = (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #> '{extra,_reader,source_manifest}';

-- Adapter-only copies of album membership and description have no storage
-- consumers. Preserve divergent historical values for explicit reconciliation.
UPDATE publications
SET content = ((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #- '{extra,telegram,members}')::text
WHERE (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #> '{extra,telegram,members}' =
      (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #> '{extra,_reader,members}';

UPDATE publications
SET content = ((CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #- '{extra,telegram,description}')::text
WHERE (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #> '{extra,telegram,description}' =
      (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) #> '{extra,_reader,description}';

-- Older readers displayed the header quote even if the structured input was
-- missing it or differed. Preserve that displayed quote as the canonical block.
-- Recovery compares processing signatures against the changed blocks and
-- reconciles results and jobs when the processing input actually changed.
WITH legacy AS (
    SELECT id, (CASE WHEN pg_input_is_valid(content, 'jsonb') THEN content::jsonb END) AS doc
    FROM publications
), candidates AS (
    SELECT id, doc, doc #> '{extra,_reader,reply_preview}' AS reply
    FROM legacy
    WHERE jsonb_typeof(doc->'blocks') = 'array'
      AND jsonb_typeof(doc #> '{extra,_reader,reply_preview}') = 'object'
      AND jsonb_typeof(doc #> '{extra,_reader,reply_preview,text}') = 'string'
      AND COALESCE(jsonb_typeof(doc #> '{extra,_reader,reply_preview,author}'), 'null') IN ('string', 'null')
      AND COALESCE(jsonb_typeof(doc #> '{extra,_reader,reply_preview,source_url}'), 'null') IN ('string', 'null')
), rebuilt AS (
    SELECT c.id,
        jsonb_set(c.doc, '{blocks}',
          COALESCE((SELECT jsonb_agg(
              CASE WHEN q.position = (SELECT min(p.position)
                    FROM jsonb_array_elements(c.doc->'blocks') WITH ORDINALITY p(block, position)
                    WHERE p.block->>'type' = 'quote')
              THEN jsonb_build_object('type','quote','text',c.reply->'text',
                   'author',c.reply->'author','source_url',c.reply->'source_url')
              ELSE q.block END ORDER BY q.position)
              FROM jsonb_array_elements(c.doc->'blocks') WITH ORDINALITY q(block, position)), '[]'::jsonb)
          || CASE WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(c.doc->'blocks') b WHERE b->>'type'='quote')
               THEN '[]'::jsonb
               ELSE jsonb_build_array(jsonb_build_object('type','quote','text',c.reply->'text',
                    'author',c.reply->'author','source_url',c.reply->'source_url')) END)
        #- '{extra,_reader,reply_preview}' AS doc
    FROM candidates c
)
UPDATE publications p SET content=rebuilt.doc::text FROM rebuilt WHERE p.id=rebuilt.id;
