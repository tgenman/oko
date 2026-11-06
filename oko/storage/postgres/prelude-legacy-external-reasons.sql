-- Preserve only proven external-media diagnostics on the successful fetch Job.
-- The temporary reason_message lets migration 107 verify ownership; the
-- transaction clears it in 121, leaving the historical value in Job payload.
LOCK TABLE publications,jobs IN ACCESS EXCLUSIVE MODE;
WITH candidates AS (
 SELECT p.id, p.content #>> '{extra,_collection_reason}' AS reason
 FROM publications p JOIN jobs j ON j.kind='fetch' AND j.item=p.id
 WHERE p.status='ready' AND j.status='done' AND j.reason_code IS NULL AND j.reason_message IS NULL
   AND NOT (j.payload ? 'historical_external_media_reason')
   AND p.content #>> '{extra,_collection_reason}' = p.content #>> '{extra,_reader,reason}'
   AND (
     (p.content #>> '{extra,_collection_reason}' IN ('Видео не предоставлено публичным предпросмотром','Файл документа не предоставлен публичным предпросмотром')
      AND EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content #> '{extra,_reader,source_manifest,resources}')='array' THEN p.content #> '{extra,_reader,source_manifest,resources}' ELSE '[]'::jsonb END) r
       WHERE r->>'status'='external' AND r->>'reason'=p.content #>> '{extra,_collection_reason}'
         AND EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) b
           WHERE b->>'type'='external_media' AND b->>'kind'=r #>> '{external,kind}' AND b->>'url'=r #>> '{external,source_url}')))
     OR (p.content #>> '{extra,_collection_reason}'='External embed not saved locally'
      AND EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) b WHERE b->>'type'='external_media' AND NULLIF(b->>'url','') IS NOT NULL))
   )
)
UPDATE jobs j SET reason_message=c.reason,payload=j.payload || jsonb_build_object('historical_external_media_reason',c.reason)
FROM candidates c WHERE j.kind='fetch' AND j.item=c.id;
