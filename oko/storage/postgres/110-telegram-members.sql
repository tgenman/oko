-- Keep album membership only in the Telegram-specific content. A pending
-- publication may contain just these facts until its body is collected.
LOCK TABLE publications IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE invalid text;
BEGIN
 SELECT string_agg(id, ', ' ORDER BY id) INTO invalid FROM publications
 WHERE NOT pg_input_is_valid(members,'jsonb')
    OR jsonb_typeof(CASE WHEN pg_input_is_valid(members,'jsonb') THEN members::jsonb END) IS DISTINCT FROM 'array'
    OR (content ? 'extra' AND jsonb_typeof(content->'extra') IS DISTINCT FROM 'object')
    OR (content #> '{extra,telegram}' IS NOT NULL AND jsonb_typeof(content #> '{extra,telegram}') IS DISTINCT FROM 'object')
    OR (content #> '{extra,telegram,members}' IS NOT NULL AND jsonb_typeof(content #> '{extra,telegram,members}') IS DISTINCT FROM 'array')
    OR (content #> '{extra,_reader,members}' IS NOT NULL AND jsonb_typeof(content #> '{extra,_reader,members}') IS DISTINCT FROM 'array')
    OR (content #> '{extra,_reader,source_manifest,post,members}' IS NOT NULL AND jsonb_typeof(content #> '{extra,_reader,source_manifest,post,members}') IS DISTINCT FROM 'array');
 IF invalid IS NOT NULL THEN RAISE EXCEPTION 'Invalid Telegram members: %', invalid; END IF;
 SELECT string_agg(id, ', ' ORDER BY id) INTO invalid FROM publications
 WHERE (platform IS DISTINCT FROM 'telegram' AND (members::jsonb<>'[]'::jsonb
      OR COALESCE(content #> '{extra,telegram,members}','[]'::jsonb)<>'[]'::jsonb
      OR COALESCE(content #> '{extra,_reader,members}','[]'::jsonb)<>'[]'::jsonb
      OR COALESCE(content #> '{extra,_reader,source_manifest,post,members}','[]'::jsonb)<>'[]'::jsonb))
   OR (members::jsonb<>'[]'::jsonb AND content #> '{extra,telegram,members}' IS NOT NULL
      AND content #> '{extra,telegram,members}' <> members::jsonb)
   OR (members::jsonb<>'[]'::jsonb AND content #> '{extra,_reader,members}' IS NOT NULL
      AND content #> '{extra,_reader,members}' <> members::jsonb)
   OR (content #> '{extra,telegram,members}' IS NOT NULL AND content #> '{extra,_reader,members}' IS NOT NULL
      AND content #> '{extra,telegram,members}' <> content #> '{extra,_reader,members}')
   OR (content #> '{extra,_reader,source_manifest,post,members}' IS NOT NULL
      AND content #> '{extra,_reader,source_manifest,post,members}' <> COALESCE(NULLIF(members::jsonb,'[]'::jsonb),content #> '{extra,telegram,members}',content #> '{extra,_reader,members}'));
 IF invalid IS NOT NULL THEN RAISE EXCEPTION 'Conflicting Telegram members: %', invalid; END IF;
END $$;
DO $$
DECLARE invalid text;
BEGIN
 SELECT string_agg(DISTINCT p.id, ', ' ORDER BY p.id) INTO invalid FROM publications p
 CROSS JOIN LATERAL (VALUES (p.members::jsonb),
   (COALESCE(p.content #> '{extra,telegram,members}','[]'::jsonb)),
   (COALESCE(p.content #> '{extra,_reader,members}','[]'::jsonb)),
   (COALESCE(p.content #> '{extra,_reader,source_manifest,post,members}','[]'::jsonb))) lists(value)
 WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(lists.value) entry(value)
               WHERE jsonb_typeof(entry.value)<>'number' OR entry.value::text !~ '^[1-9][0-9]*$')
    OR jsonb_array_length(lists.value)<>(SELECT count(DISTINCT entry.value) FROM jsonb_array_elements(lists.value) entry(value));
 IF invalid IS NOT NULL THEN RAISE EXCEPTION 'Invalid Telegram members: %', invalid; END IF;
END $$;
UPDATE publications p SET content=jsonb_set(p.content,'{extra}',
 COALESCE(p.content->'extra','{}'::jsonb) || jsonb_build_object('telegram',
 COALESCE(p.content #> '{extra,telegram}','{}'::jsonb) || jsonb_build_object('members',
 COALESCE(p.content #> '{extra,telegram,members}',NULLIF(p.members::jsonb,'[]'::jsonb),p.content #> '{extra,_reader,members}',p.content #> '{extra,_reader,source_manifest,post,members}','[]'::jsonb))),true)
WHERE p.platform='telegram';
UPDATE publications SET content=content #- '{extra,_reader,members}' #- '{extra,_reader,source_manifest,post,members}'
WHERE content #> '{extra,_reader,members}' IS NOT NULL OR content #> '{extra,_reader,source_manifest,post,members}' IS NOT NULL;
ALTER TABLE publications DROP COLUMN members;
