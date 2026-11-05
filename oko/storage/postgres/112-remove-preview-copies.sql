-- Preserve the fragment in body blocks and its failure in the fetch Job.
LOCK TABLE publications,jobs IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE invalid text;
BEGIN
 SELECT string_agg(id, ', ' ORDER BY id) INTO invalid FROM publications p
 WHERE (p.content #> '{extra,_reader,source_manifest,preview_fragment}' IS NOT NULL
        AND jsonb_typeof(p.content #> '{extra,_reader,source_manifest,preview_fragment}') NOT IN ('string','null'))
    OR (p.content #> '{extra,_reader,source_manifest,preview_error}' IS NOT NULL
        AND jsonb_typeof(p.content #> '{extra,_reader,source_manifest,preview_error}') NOT IN ('string','null'))
    OR (p.content #> '{extra,_reader,source_manifest,post_web_unavailable}' IS NOT NULL
        AND jsonb_typeof(p.content #> '{extra,_reader,source_manifest,post_web_unavailable}') NOT IN ('boolean','null'))
    OR (p.content ? 'blocks' AND jsonb_typeof(p.content->'blocks')<>'array')
    OR (NULLIF(p.content #>> '{extra,_reader,source_manifest,preview_error}','') IS NOT NULL
        AND COALESCE(p.content #>> '{extra,_reader,source_manifest,post_web_unavailable}','false')<>'true');
 IF invalid IS NOT NULL THEN RAISE EXCEPTION 'Invalid preview facts: %',invalid; END IF;
 SELECT string_agg(p.id, ', ' ORDER BY p.id) INTO invalid FROM publications p
 JOIN jobs j ON j.kind='fetch' AND j.item=p.id
 WHERE (NULLIF(p.content #>> '{extra,_reader,source_manifest,preview_error}','') IS NOT NULL
        AND j.reason_message IS NOT NULL
        AND strpos(j.reason_message,p.content #>> '{extra,_reader,source_manifest,preview_error}')=0)
    OR (COALESCE(p.content #>> '{extra,_reader,source_manifest,post_web_unavailable}','false')='true'
        AND j.status='done' AND j.reason_code IS NULL AND j.reason_message IS NULL);
 IF invalid IS NOT NULL THEN RAISE EXCEPTION 'Conflicting preview and fetch Job: %',invalid; END IF;
END $$;
UPDATE publications p SET content=jsonb_set(p.content,'{blocks}',
 COALESCE(p.content->'blocks','[]'::jsonb) || jsonb_build_array(jsonb_build_object('type','text','role','body',
   'text',p.content #> '{extra,_reader,source_manifest,preview_fragment}')),true)
WHERE NULLIF(p.content #>> '{extra,_reader,source_manifest,preview_fragment}','') IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(p.content->'blocks','[]'::jsonb)) b
   WHERE b->>'type'='text' AND b->>'role'='body'
     AND strpos(COALESCE(b->>'text',''),p.content #>> '{extra,_reader,source_manifest,preview_fragment}')>0);
INSERT INTO jobs(kind,item,status,reason_code,reason_message)
SELECT 'fetch',p.id,CASE WHEN p.content #>> '{extra,_reader,source_manifest,post_web_unavailable}'='true' THEN 'unavailable' ELSE 'pending' END,
 CASE WHEN p.content #>> '{extra,_reader,source_manifest,post_web_unavailable}'='true' THEN 'post-web-unavailable' ELSE 'preview-incomplete' END,
 COALESCE(NULLIF(p.content #>> '{extra,_reader,source_manifest,preview_error}',''),'Полная публикация ожидает сбора')
FROM publications p WHERE p.viewed=0 AND (p.content #>> '{extra,_reader,source_manifest,post_web_unavailable}'='true'
 OR NULLIF(p.content #>> '{extra,_reader,source_manifest,preview_fragment}','') IS NOT NULL)
ON CONFLICT(kind,item) DO UPDATE SET
 status=CASE WHEN jobs.status='done' THEN excluded.status ELSE jobs.status END,
 generation=CASE WHEN jobs.status='done' THEN jobs.generation+1 ELSE jobs.generation END,
 reason_message=COALESCE(jobs.reason_message,excluded.reason_message),
 reason_code=COALESCE(jobs.reason_code,excluded.reason_code);
UPDATE publications p SET status='pending_collection'
WHERE p.viewed=0 AND (NULLIF(p.content #>> '{extra,_reader,source_manifest,preview_fragment}','') IS NOT NULL
 OR (p.content #>> '{extra,_reader,source_manifest,post_web_unavailable}'='true'
     AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(p.content->'blocks','[]'::jsonb)) b
       WHERE b->>'type'='media' OR (b->>'type'='text' AND b->>'role'='body' AND btrim(COALESCE(b->>'text',''))<>''))));
UPDATE publications SET content=content #- '{extra,_reader,source_manifest,preview_fragment}'
  #- '{extra,_reader,source_manifest,preview_error}' #- '{extra,_reader,source_manifest,post_web_unavailable}'
WHERE content #> '{extra,_reader,source_manifest,preview_fragment}' IS NOT NULL
 OR content #> '{extra,_reader,source_manifest,preview_error}' IS NOT NULL
 OR content #> '{extra,_reader,source_manifest,post_web_unavailable}' IS NOT NULL;
