-- Preserve unexpected values for inspection rather than discarding them.
DO $$
DECLARE invalid_ids text;
BEGIN
    SELECT string_agg(id, ', ' ORDER BY id) INTO invalid_ids FROM publications
    WHERE jsonb_typeof(content) IS DISTINCT FROM 'object'
       OR (content ? 'blocks' AND jsonb_typeof(content->'blocks') IS DISTINCT FROM 'array')
       OR (content ? 'extra' AND jsonb_typeof(content->'extra') IS DISTINCT FROM 'object')
       OR (content #> '{extra,_reader}' IS NOT NULL AND jsonb_typeof(content #> '{extra,_reader}') NOT IN ('object','null'))
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(
            CASE WHEN jsonb_typeof(content->'blocks')='array' THEN content->'blocks' ELSE '[]'::jsonb END) b
          WHERE (b->>'type'='quote' AND
                 (jsonb_typeof(b->'text') IS DISTINCT FROM 'string'
                  OR COALESCE(jsonb_typeof(b->'author'),'null') NOT IN ('string','null')
                  OR COALESCE(jsonb_typeof(b->'source_url'),'null') NOT IN ('string','null')))
             OR (b->>'type'='text' AND b->>'role'='description' AND
                 jsonb_typeof(b->'text') IS DISTINCT FROM 'string'))
       OR (content #> '{extra,_reader,title}' IS NOT NULL AND jsonb_typeof(content #> '{extra,_reader,title}') NOT IN ('string','null'))
       OR (content #> '{extra,_reader,description}' IS NOT NULL AND jsonb_typeof(content #> '{extra,_reader,description}') NOT IN ('string','null'))
       OR (content #> '{extra,_reader,reply_preview}' IS NOT NULL AND jsonb_typeof(content #> '{extra,_reader,reply_preview}') NOT IN ('object','null'))
       OR (jsonb_typeof(content #> '{extra,_reader,reply_preview}')='object' AND
           ((content #> '{extra,_reader,reply_preview}') - ARRAY['text','author','source_url']) <> '{}'::jsonb)
       OR (jsonb_typeof(content #> '{extra,_reader,reply_preview}')='object' AND
           (jsonb_typeof(content #> '{extra,_reader,reply_preview,text}') IS DISTINCT FROM 'string'
            OR COALESCE(jsonb_typeof(content #> '{extra,_reader,reply_preview,author}'),'null') NOT IN ('string','null')
            OR COALESCE(jsonb_typeof(content #> '{extra,_reader,reply_preview,source_url}'),'null') NOT IN ('string','null')))
       OR ((jsonb_typeof(content #> '{extra,_reader,reply_preview}')='object'
            OR jsonb_typeof(content #> '{extra,_reader,description}')='string')
           AND jsonb_typeof(content->'blocks') IS DISTINCT FROM 'array');
    IF invalid_ids IS NOT NULL THEN
        RAISE EXCEPTION 'Invalid publication reader content: %', invalid_ids;
    END IF;
END $$;
UPDATE publications p SET content=jsonb_set(p.content, '{blocks}', p.content->'blocks' ||
    jsonb_build_array(jsonb_build_object('type','quote','text',p.content #> '{extra,_reader,reply_preview,text}',
        'author',p.content #> '{extra,_reader,reply_preview,author}',
        'source_url',p.content #> '{extra,_reader,reply_preview,source_url}')))
WHERE jsonb_typeof(p.content #> '{extra,_reader,reply_preview}')='object'
  AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(p.content->'blocks') b WHERE b->>'type'='quote');
UPDATE publications p SET content=jsonb_set(p.content, '{blocks}', p.content->'blocks' ||
    jsonb_build_array(jsonb_build_object('type','text','role','description','text',p.content #> '{extra,_reader,description}')))
WHERE jsonb_typeof(p.content #> '{extra,_reader,description}')='string'
  AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(p.content->'blocks') b WHERE b->>'type'='text' AND b->>'role'='description');
UPDATE publications SET content=content #- '{extra,_reader,title}' #- '{extra,_reader,reply_preview}' #- '{extra,_reader,description}'
WHERE content #> '{extra,_reader}' IS NOT NULL;
