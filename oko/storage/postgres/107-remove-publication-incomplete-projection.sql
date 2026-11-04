-- A non-empty collection reason on an otherwise completed publication must
-- already be represented by an owning Job before the legacy copy is removed.
LOCK TABLE jobs, media_files, publications IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE ambiguous text;
BEGIN
 SELECT string_agg(p.id, ', ' ORDER BY p.id) INTO ambiguous FROM publications p
 WHERE p.status<>'pending_collection' AND COALESCE(p.content #>> '{extra,_collection_reason}','')<>''
   AND COALESCE(p.content #>> '{extra,_reader,source_manifest,post_web_unavailable}','false')<>'true'
   AND NOT EXISTS (SELECT 1 FROM jobs j LEFT JOIN media_files m ON j.kind='download_media' AND m.id=j.item
     WHERE (j.item=p.id OR m.publication_id=p.id)
       AND j.reason_message=p.content #>> '{extra,_collection_reason}');
 IF ambiguous IS NOT NULL THEN
  RAISE EXCEPTION 'Unowned legacy collection reasons: %', ambiguous;
 END IF;
 SELECT string_agg(p.id, ', ' ORDER BY p.id) INTO ambiguous FROM publications p
 WHERE p.status<>'pending_collection'
   AND COALESCE(p.content #>> '{extra,_collection_reason}','')=''
   AND COALESCE(p.content #>> '{extra,_reader,reason}','')<>''
   AND COALESCE(p.content #>> '{extra,_reader,source_manifest,post_web_unavailable}','false')<>'true'
   AND EXISTS (
     SELECT 1 FROM regexp_split_to_table(p.content #>> '{extra,_reader,reason}', '; ') AS part(reason)
     WHERE NOT EXISTS (SELECT 1 FROM jobs j LEFT JOIN media_files m ON j.kind='download_media' AND m.id=j.item
                       WHERE (j.item=p.id OR m.publication_id=p.id) AND j.reason_message=part.reason)
       AND NOT (part.reason IN ('Субтитры не запрошены','Вложение недоступно','Вложение ожидает загрузки','Вложение не запрошено','Вложение загружается')
                AND EXISTS (SELECT 1 FROM media_files m WHERE m.publication_id=p.id AND m.status<>'ready'))
   );
 IF ambiguous IS NOT NULL THEN
  RAISE EXCEPTION 'Unowned legacy reader reasons: %', ambiguous;
 END IF;
 SELECT string_agg(p.id, ', ' ORDER BY p.id) INTO ambiguous FROM publications p
 WHERE p.status<>'pending_collection' AND (p.content #>> '{extra,_reader,full}'='false'
   OR p.content #>> '{extra,_reader,full}' ~ '^[[:space:]]*[+-]?0+[[:space:]]*$')
   AND COALESCE(p.content #>> '{extra,_reader,source_manifest,post_web_unavailable}','false')<>'true'
   AND NOT EXISTS (SELECT 1 FROM media_files m WHERE m.publication_id=p.id AND m.status<>'ready')
   AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.kind='fetch' AND j.item=p.id AND j.status IN ('pending','running','blocked','failed','unavailable'))
   AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(p.content->'blocks')='array' THEN p.content->'blocks' ELSE '[]'::jsonb END) b WHERE b->>'type'='external_media');
 IF ambiguous IS NOT NULL THEN
  RAISE EXCEPTION 'Unowned legacy incompleteness: %', ambiguous;
 END IF;
END $$;
-- Grouping is a read-time diagnostic projection of Publication and Job facts.
ALTER TABLE publications DROP COLUMN incomplete_class,
  DROP COLUMN incomplete_group_key, DROP COLUMN incomplete_group_owner,
  DROP COLUMN incomplete_group_ready, DROP COLUMN incomplete_group_unknown,
  DROP COLUMN incomplete_group_conflict;

-- Preserve an unfinished legacy collection that has no current attempt. A
-- current non-done Job already owns its outcome and wins over stale content.
WITH legacy AS (
  SELECT p.id,p.status,
    NULLIF(COALESCE(NULLIF(p.content #>> '{extra,_collection_reason}',''),
                    p.content #>> '{extra,_reader,reason}'),'') AS message,
    COALESCE(p.content #>> '{extra,_reader,source_manifest,post_web_unavailable}','false')='true' AS unavailable
  FROM publications p WHERE p.viewed=0
)
INSERT INTO jobs(kind,item,status,reason_code,reason_message)
SELECT 'fetch',id,CASE WHEN unavailable THEN 'unavailable' ELSE 'pending' END,
  CASE WHEN unavailable THEN 'post-web-unavailable' ELSE 'legacy-incomplete' END,
  COALESCE(message,'Публикация недоступна для веб-просмотра')
FROM legacy WHERE unavailable OR (status='pending_collection' AND message IS NOT NULL)
ON CONFLICT(kind,item) DO UPDATE SET
  status=excluded.status,reason_code=excluded.reason_code,reason_message=excluded.reason_message,
  generation=jobs.generation+1
WHERE jobs.status='done';

-- These values are derived from Publication and Job/MediaFile state at read time.
UPDATE publications SET content=content #- '{extra,_collection_reason}' #- '{extra,_reader,full}' #- '{extra,_reader,reason}'
WHERE content #> '{extra,_collection_reason}' IS NOT NULL
   OR content #> '{extra,_reader,full}' IS NOT NULL
   OR content #> '{extra,_reader,reason}' IS NOT NULL;
-- Reconcile newly restored fetch outcomes with persistent reader diagnostics.
DELETE FROM state WHERE key='fetch-problems-recovered-v2';
CREATE INDEX jobs_retired_media_cleanup ON jobs(id)
  WHERE kind='download_media' AND payload ? 'retired_media_key';
