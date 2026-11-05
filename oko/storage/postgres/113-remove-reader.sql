-- Remove the old reader copy only after its display facts are represented by
-- Publication blocks, MediaFile and Job. Ambiguity is resolved manually.
LOCK TABLE publications,media_files,jobs IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE conflicts text;
BEGIN
 SELECT string_agg(p.id, ', ' ORDER BY p.id) INTO conflicts FROM publications p
 WHERE p.content #> '{extra,_reader}' IS NOT NULL AND (
  jsonb_typeof(p.content #> '{extra,_reader}') NOT IN ('object','null')
  OR (jsonb_typeof(p.content #> '{extra,_reader}')='object' AND
      (p.content #> '{extra,_reader}') - ARRAY['title','description','reply_preview','full','reason','captions','attachments','_media_urls','source_manifest'] <> '{}'::jsonb)
  OR (p.content #> '{extra,_reader,attachments}' IS NOT NULL AND jsonb_typeof(p.content #> '{extra,_reader,attachments}') NOT IN ('array','null'))
  OR (p.content #> '{extra,_reader,_media_urls}' IS NOT NULL AND jsonb_typeof(p.content #> '{extra,_reader,_media_urls}') NOT IN ('object','null'))
  OR (p.content #> '{extra,_reader,source_manifest}' IS NOT NULL AND jsonb_typeof(p.content #> '{extra,_reader,source_manifest}') NOT IN ('object','null'))
  OR (jsonb_typeof(p.content #> '{extra,_reader,source_manifest}')='object' AND
      (p.content #> '{extra,_reader,source_manifest}') - ARRAY['post','resources','source_url'] <> '{}'::jsonb)
  OR (p.content #> '{extra,_reader,source_manifest,post}' IS NOT NULL AND jsonb_typeof(p.content #> '{extra,_reader,source_manifest,post}')<>'object')
  OR (jsonb_typeof(p.content #> '{extra,_reader,source_manifest,post}')='object' AND
      (p.content #> '{extra,_reader,source_manifest,post}') - ARRAY['text','description','reply_preview','edited','resources'] <> '{}'::jsonb)
  OR (p.content #> '{extra,_reader,source_manifest,resources}' IS NOT NULL AND jsonb_typeof(p.content #> '{extra,_reader,source_manifest,resources}')<>'array')
  OR (p.content #> '{extra,_reader,source_manifest,post,resources}' IS NOT NULL AND jsonb_typeof(p.content #> '{extra,_reader,source_manifest,post,resources}')<>'array')
  OR EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content #> '{extra,_reader,source_manifest,post,resources}')='array'
       THEN p.content #> '{extra,_reader,source_manifest,post,resources}' ELSE '[]'::jsonb END) r
      WHERE jsonb_typeof(r)<>'object' OR r - ARRAY['key','kind'] <> '{}'::jsonb OR NULLIF(r->>'key','') IS NULL
       OR (NOT EXISTS (SELECT 1 FROM media_files m WHERE m.publication_id=p.id AND m.resource_key=r->>'key')
           AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content #> '{extra,_reader,source_manifest,resources}')='array'
             THEN p.content #> '{extra,_reader,source_manifest,resources}' ELSE '[]'::jsonb END) x WHERE x->>'key'=r->>'key')))
  OR (p.content #> '{extra,_reader,source_manifest,source_url}' IS NOT NULL AND jsonb_typeof(p.content #> '{extra,_reader,source_manifest,source_url}') NOT IN ('string','null'))
  OR (NULLIF(p.content #>> '{extra,_reader,source_manifest,source_url}','') IS NOT NULL AND NULLIF(p.url,'') IS NOT NULL
      AND p.content #>> '{extra,_reader,source_manifest,source_url}'<>p.url)
  OR (NULLIF(p.content #>> '{extra,_reader,source_manifest,post,text}','') IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) b
      WHERE b->>'type'='text' AND b->>'role'='body' AND strpos(COALESCE(b->>'text',''),p.content #>> '{extra,_reader,source_manifest,post,text}')>0))
  OR (p.content #> '{extra,_reader,captions}' IS NOT NULL AND p.content #> '{extra,_reader,captions}'<>'null'::jsonb
      AND (p.platform IS DISTINCT FROM 'youtube' OR jsonb_typeof(p.content #> '{extra,_reader,captions}')<>'string'
           OR p.content #>> '{extra,_reader,captions}' NOT IN ('available','unavailable','not_requested','pending','running','error')))
  OR (NOT EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) b WHERE b->>'type'='text' AND b->>'role'='description')
      AND NULLIF(p.content #> '{extra,_reader,description}','null'::jsonb) IS NOT NULL
      AND NULLIF(p.content #> '{extra,_reader,source_manifest,post,description}','null'::jsonb) IS NOT NULL
      AND p.content #> '{extra,_reader,description}' IS DISTINCT FROM p.content #> '{extra,_reader,source_manifest,post,description}')
  OR (NOT EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) b WHERE b->>'type'='quote')
      AND NULLIF(p.content #> '{extra,_reader,reply_preview}','null'::jsonb) IS NOT NULL
      AND NULLIF(p.content #> '{extra,_reader,source_manifest,post,reply_preview}','null'::jsonb) IS NOT NULL
      AND p.content #> '{extra,_reader,reply_preview}' IS DISTINCT FROM p.content #> '{extra,_reader,source_manifest,post,reply_preview}')
  OR (NOT EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) b WHERE b->>'type'='text' AND b->>'role'='description')
      AND COALESCE(NULLIF(p.content #> '{extra,_reader,description}','null'::jsonb),NULLIF(p.content #> '{extra,_reader,source_manifest,post,description}','null'::jsonb)) IS NOT NULL
      AND jsonb_typeof(COALESCE(NULLIF(p.content #> '{extra,_reader,description}','null'::jsonb),NULLIF(p.content #> '{extra,_reader,source_manifest,post,description}','null'::jsonb)))<>'string')
  OR (NOT EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) b WHERE b->>'type'='quote')
      AND COALESCE(NULLIF(p.content #> '{extra,_reader,reply_preview}','null'::jsonb),NULLIF(p.content #> '{extra,_reader,source_manifest,post,reply_preview}','null'::jsonb)) IS NOT NULL
      AND (jsonb_typeof(COALESCE(NULLIF(p.content #> '{extra,_reader,reply_preview}','null'::jsonb),NULLIF(p.content #> '{extra,_reader,source_manifest,post,reply_preview}','null'::jsonb)))<>'object'
       OR jsonb_typeof(COALESCE(NULLIF(p.content #> '{extra,_reader,reply_preview,text}','null'::jsonb),NULLIF(p.content #> '{extra,_reader,source_manifest,post,reply_preview,text}','null'::jsonb))) IS DISTINCT FROM 'string'
       OR COALESCE(jsonb_typeof(COALESCE(NULLIF(p.content #> '{extra,_reader,reply_preview,author}','null'::jsonb),NULLIF(p.content #> '{extra,_reader,source_manifest,post,reply_preview,author}','null'::jsonb))),'null') NOT IN ('string','null')
       OR COALESCE(jsonb_typeof(COALESCE(NULLIF(p.content #> '{extra,_reader,reply_preview,source_url}','null'::jsonb),NULLIF(p.content #> '{extra,_reader,source_manifest,post,reply_preview,source_url}','null'::jsonb))),'null') NOT IN ('string','null')
       OR COALESCE(NULLIF(p.content #> '{extra,_reader,reply_preview}','null'::jsonb),NULLIF(p.content #> '{extra,_reader,source_manifest,post,reply_preview}','null'::jsonb)) - ARRAY['text','author','source_url'] <> '{}'::jsonb))
  OR EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content #> '{extra,_reader,attachments}')='array'
       THEN p.content #> '{extra,_reader,attachments}' ELSE '[]'::jsonb END) a
      WHERE jsonb_typeof(a)<>'object' OR a - ARRAY['name','media_id','kind','mime'] <> '{}'::jsonb
        OR jsonb_typeof(a->'name') IS DISTINCT FROM 'string' OR NULLIF(a->>'name','') IS NULL
        OR jsonb_typeof(a->'mime') IS DISTINCT FROM 'string' OR a->>'kind' NOT IN ('image','video','audio','circle','file')
        OR NULLIF(a->>'media_id','') IS NULL
        OR NOT EXISTS (SELECT 1 FROM media_files m WHERE m.id=a->>'media_id' AND m.publication_id=p.id))
  OR EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content #> '{extra,_reader,attachments}')='array'
       THEN p.content #> '{extra,_reader,attachments}' ELSE '[]'::jsonb END) a
      JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) b
        ON b->>'type'='media' AND b->>'media_id'=a->>'media_id'
      WHERE NOT ((a->>'kind'='image' AND b->>'role' IN ('image','cover'))
          OR (a->>'kind' IN ('video','circle') AND b->>'role'='video')
          OR (a->>'kind'='audio' AND b->>'role'='audio')
          OR (a->>'kind'='file' AND b->>'role' IN ('attachment','subtitles')))
        OR (a->>'kind'='circle' AND b->>'presentation' IS NOT NULL AND b->>'presentation'<>'circle'))
  OR (SELECT count(*)<>count(DISTINCT a->>'media_id') FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content #> '{extra,_reader,attachments}')='array'
       THEN p.content #> '{extra,_reader,attachments}' ELSE '[]'::jsonb END) a)
  OR EXISTS (SELECT 1 FROM jsonb_each(CASE WHEN jsonb_typeof(p.content #> '{extra,_reader,_media_urls}')='object'
       THEN p.content #> '{extra,_reader,_media_urls}' ELSE '{}'::jsonb END) u(name,value)
      WHERE jsonb_typeof(u.value)<>'string' OR NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content #> '{extra,_reader,attachments}')='array'
          THEN p.content #> '{extra,_reader,attachments}' ELSE '[]'::jsonb END) a
        WHERE a->>'name'=u.name AND u.value #>> '{}'='/api/v3/materials/'||p.id||'/media/'||(a->>'media_id')))
  OR (NULLIF(p.content #>> '{extra,_reader,reason}','') IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM jobs j LEFT JOIN media_files m ON j.kind='download_media' AND m.id=j.item
      WHERE (j.item=p.id OR m.publication_id=p.id) AND j.reason_message=p.content #>> '{extra,_reader,reason}'))
  OR (p.content #> '{extra,_reader,captions}' IS NOT NULL AND p.content #> '{extra,_reader,captions}'<>'null'::jsonb
      AND p.content #> '{extra,youtube,captions}' IS NOT NULL
      AND p.content #> '{extra,_reader,captions}' IS DISTINCT FROM p.content #> '{extra,youtube,captions}')
  OR EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content #> '{extra,_reader,source_manifest,resources}')='array'
       THEN p.content #> '{extra,_reader,source_manifest,resources}' ELSE '[]'::jsonb END) r
      WHERE jsonb_typeof(r)<>'object' OR r - ARRAY['key','origin','order','name','kind','mime','status','reason','source_url','sha256','external'] <> '{}'::jsonb
       OR NULLIF(r->>'key','') IS NULL OR COALESCE(r->>'status','') NOT IN ('loaded','missing','unsupported','external')
       OR (r->>'status'='external' AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) b
          WHERE b->>'type'='external_media' AND b->>'kind'=r #>> '{external,kind}' AND b->>'url'=r #>> '{external,source_url}'))
       OR (r->>'status'<>'external' AND NOT EXISTS (SELECT 1 FROM media_files m WHERE m.publication_id=p.id AND m.resource_key=r->>'key')
           AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.item=p.id AND j.reason_message=r->>'reason' AND NULLIF(r->>'reason','') IS NOT NULL))
       OR (r #>> '{external,poster_key}' IS NOT NULL AND NOT EXISTS (SELECT 1 FROM media_files m WHERE m.publication_id=p.id AND m.resource_key=r #>> '{external,poster_key}')))
 );
 IF conflicts IS NOT NULL THEN RAISE EXCEPTION 'Unrepresented reader facts: %',conflicts; END IF;
END $$;
-- Existing common blocks and the title column take precedence.
UPDATE publications SET url=content #>> '{extra,_reader,source_manifest,source_url}'
WHERE NULLIF(url,'') IS NULL AND NULLIF(content #>> '{extra,_reader,source_manifest,source_url}','') IS NOT NULL;
UPDATE publications p SET content=jsonb_set(p.content,'{blocks}',COALESCE(p.content->'blocks','[]'::jsonb) ||
 jsonb_build_array(jsonb_build_object('type','text','role','description','text',
   COALESCE(NULLIF(p.content #> '{extra,_reader,description}','null'::jsonb),NULLIF(p.content #> '{extra,_reader,source_manifest,post,description}','null'::jsonb)))),true)
WHERE jsonb_typeof(COALESCE(NULLIF(p.content #> '{extra,_reader,description}','null'::jsonb),NULLIF(p.content #> '{extra,_reader,source_manifest,post,description}','null'::jsonb)))='string'
 AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(p.content->'blocks','[]'::jsonb)) b WHERE b->>'type'='text' AND b->>'role'='description');
UPDATE publications p SET content=jsonb_set(p.content,'{blocks}',COALESCE(p.content->'blocks','[]'::jsonb) ||
 jsonb_build_array(jsonb_build_object('type','quote','text',COALESCE(NULLIF(p.content #> '{extra,_reader,reply_preview,text}','null'::jsonb),NULLIF(p.content #> '{extra,_reader,source_manifest,post,reply_preview,text}','null'::jsonb)),
 'author',COALESCE(NULLIF(p.content #> '{extra,_reader,reply_preview,author}','null'::jsonb),NULLIF(p.content #> '{extra,_reader,source_manifest,post,reply_preview,author}','null'::jsonb)),
 'source_url',COALESCE(NULLIF(p.content #> '{extra,_reader,reply_preview,source_url}','null'::jsonb),NULLIF(p.content #> '{extra,_reader,source_manifest,post,reply_preview,source_url}','null'::jsonb)))),true)
WHERE jsonb_typeof(COALESCE(NULLIF(p.content #> '{extra,_reader,reply_preview,text}','null'::jsonb),NULLIF(p.content #> '{extra,_reader,source_manifest,post,reply_preview,text}','null'::jsonb)))='string'
 AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(p.content->'blocks','[]'::jsonb)) b WHERE b->>'type'='quote');
UPDATE publications p SET content=jsonb_set(p.content,'{blocks}',(
 SELECT jsonb_agg(CASE WHEN b->>'type'='media' AND EXISTS (
   SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content #> '{extra,_reader,attachments}')='array'
     THEN p.content #> '{extra,_reader,attachments}' ELSE '[]'::jsonb END) a
   WHERE a->>'kind'='circle' AND a->>'media_id'=b->>'media_id')
 THEN b || '{"presentation":"circle"}'::jsonb ELSE b END ORDER BY ordinal)
 FROM jsonb_array_elements(p.content->'blocks') WITH ORDINALITY x(b,ordinal)))
WHERE jsonb_typeof(p.content->'blocks')='array' AND jsonb_array_length(p.content->'blocks')>0
 AND jsonb_typeof(p.content #> '{extra,_reader,attachments}')='array';
UPDATE publications p SET content=jsonb_set(p.content,'{blocks}',COALESCE(p.content->'blocks','[]'::jsonb) || (
 SELECT COALESCE(jsonb_agg(jsonb_build_object('type','media','role',CASE WHEN a->>'kind'='circle' THEN 'video' WHEN a->>'kind' IN ('image','video','audio') THEN a->>'kind' ELSE 'attachment' END,
  'media_id',m.id,'source_url',m.source_url,'publication_url',CASE WHEN p.platform='telegram' AND jsonb_array_length(COALESCE(p.content #> '{extra,telegram,members}','[]'::jsonb))>1 THEN NULL ELSE NULLIF(p.url,'') END) || CASE WHEN a->>'kind'='circle' THEN '{"presentation":"circle"}'::jsonb ELSE '{}'::jsonb END ORDER BY ordinal),'[]'::jsonb)
 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content #> '{extra,_reader,attachments}')='array' THEN p.content #> '{extra,_reader,attachments}' ELSE '[]'::jsonb END) WITH ORDINALITY x(a,ordinal)
 JOIN media_files m ON m.id=a->>'media_id' AND m.publication_id=p.id
 WHERE NOT EXISTS (SELECT 1 FROM jsonb_array_elements(COALESCE(p.content->'blocks','[]'::jsonb)) b WHERE b->>'type'='media' AND b->>'media_id'=m.id)),true)
WHERE jsonb_typeof(p.content #> '{extra,_reader,attachments}')='array' AND jsonb_array_length(p.content #> '{extra,_reader,attachments}')>0;
UPDATE publications p SET content=jsonb_set(p.content,'{extra}',(p.content->'extra') || jsonb_build_object('youtube',
 COALESCE(p.content #> '{extra,youtube}','{}'::jsonb) || jsonb_build_object('captions',p.content #> '{extra,_reader,captions}')),true)
WHERE p.platform='youtube' AND p.content #> '{extra,_reader,captions}' IS NOT NULL
 AND p.content #> '{extra,_reader,captions}'<>'null'::jsonb AND p.content #> '{extra,youtube,captions}' IS NULL;
UPDATE publications SET content=content #- '{extra,_reader}' WHERE content #> '{extra,_reader}' IS NOT NULL;
